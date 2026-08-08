# PR-008 SPESIFIKASJON v2 — DELTA (syv datamodell-kontrakter → GO)

**Draft: Claude.ai · v1 står der det ikke motsies. Reviewens anbefalte
modeller vedtatt direkte. Kjernen: resultat, evidens og sikkerhet er TRE
ortogonale akser, ikke én sammenslått union.**

## 1. Tre ortogonale akser i beslutningsdetaljen

`resultat`, `evidensstatus` og `sikkerhet` er uavhengige toppnivåfelt —
en sikkerhetssak kan oppstå etter TILLAT (motstridende kvittering), så den
kan ikke henge under en resultatvariant:
```
GET /v1/beslutninger/{id} →
{
  id, handling, begrunnelse[koder], policy_versjon, policy_hash, beslutning_ts,
  resultat:       <union, §2>,
  evidensstatus:  [INGEN|MANGLER|GYLDIG|SEN|KONFLIKT],   // egen akse, §2
  sikkerhet:      {sak_finnes: bool}                     // toppnivå, §3
}
```
Resultatunionen er IDENTISK uansett sikkerhetsscope — ingen duplisering
av OpenAPI-varianter.

## 2. Resultat (policy+utførelse) vs. evidensstatus (kvittering) — adskilt

`resultat.art` beskriver policybeslutning + ordinær utførelsestilstand;
`evidensstatus` beskriver kvitteringen separat (en sen kvittering kan
eksistere samtidig med et terminalt oppdrag — derfor egen akse):
```
resultat.art oneOf:
  policy_stoppet                              // STOPP
  sideeffektfri_tillatt                       // TILLAT, ingen outbox
  outbox_opprettet   {oppdrag_id}             // TILLAT, venter plukking
  outbox_plukket     {oppdrag_id}             // TILLAT, under utførelse
  outbox_utfort      {oppdrag_id}             // TILLAT, eiermodul meldt utført
  outbox_feilet      {oppdrag_id}             // TILLAT, utførelse feilet
  outbox_kansellert  {oppdrag_id, superseded: bool}   // kansellert/superseded
  til_unntak         {unntak_id, kategori, status}     // UNNTAK

evidensstatus (egen skalar enum):
  INGEN        // ikke outbox-handling (sideeffektfri/stopp/unntak)
  MANGLER      // outbox opprettet, ingen kvittering ennå
  GYLDIG       // gyldig resultatkvittering mottatt
  SEN          // gyldig kvittering etter utførelsesfrist (PR-007 v4)
  KONFLIKT     // motstridende kvittering → utløser sikkerhetssak
```
De ni tilfellene reviewen krevde mapper entydig:
sideeffektfri = `sideeffektfri_tillatt`+`INGEN`; venter =
`outbox_opprettet`/`outbox_plukket`+`MANGLER`; utført =
`outbox_utfort`+`GYLDIG`; feilet = `outbox_feilet`+`GYLDIG`;
kansellert/superseded = `outbox_kansellert`+(evidens etter faktisk rad);
sen = `outbox_*`+`SEN`; konflikt = `outbox_*`+`KONFLIKT` (+`sikkerhet`);
STOPP = `policy_stoppet`+`INGEN`; UNNTAK = `til_unntak`+`INGEN`.
**`outbox_utfort` påstår aldri utførelse uten at evidensstatus bekrefter
det** — beslutning≠utførelse over to akser. Server utleder BEGGE fra samme
autoritative oppdrags-/kvitteringsrader. UI matcher `art`, ukjent →
`Feiltilstand`.

## 3. Sikkerhet på toppnivå, scope-styrt (uendret prinsipp, flyttet)
- Uten `security:read` → `sikkerhet` FRAVÆRENDE fra ALLE varianter.
- Med `security:read` → `sikkerhet: {sak_finnes}` finnes for ALLE varianter.
Fravær ≠ false. (GO-krav 2, nå ortogonalt.)

## 4. Eksplisitt kobling beslutning → oppdrag/unntak (aldri tidsnærhet)

Koblingen må være en stabil nøkkel, aldri gjettet fra tid/handling/«siste
rad». Sannhetssjekk mot main + eksplisitt migrasjonsavhengighet:
- **beslutning → unntak:** `unntak.loggpost_id` FK til revisjonslogg
  FINNES (migrasjon 003). Detaljen slår opp `WHERE loggpost_id = {id}`.
- **beslutning → oppdrag:** oppdrag kobles til unntak (M-37), ikke direkte
  til loggposten. Kjeden er `beslutning(loggpost) ← unntak ← oppdrag`.
  Detaljendepunktet følger denne, ikke tidsnærhet.
- **Flere generasjoner** (PR-007 retry): detaljen viser den GJELDENDE
  generasjonens oppdrag/evidens (høyeste `verification_generation` som er
  terminal-relevant), og evidensstatus reflekterer den. Eldre generasjoner
  er historikk, ikke hovedresultat.
- **Hvis en nødvendig FK mangler** (f.eks. direkte loggpost→oppdrag skulle
  vise seg nødvendig): eksplisitt migrasjon i backend-PR-en, ikke
  applikasjonsgjetting. **Åpent for Claude Code å bekrefte mot main:** at
  `beslutning(loggpost) ← unntak ← oppdrag`-kjeden er tilstrekkelig for
  detaljvisningen uten ny FK. Flagges hvis ikke.

## 5. Tenant- og filterbundet cursor

Cursorpayload (signert, konstant-tids HMAC-sammenligning) binder:
`versjon, tenant, endepunkt, sorteringsretning, siste (ts,id), aktive
filtre, øvre_grense (snapshot-tak), utløp`. Regler:
- Cursor fra annen tenant ELLER annet endepunkt → `400` (lukket kode
  `cursor_ugyldig`).
- Endret filter → gammel cursor ugyldig.
- Ugyldig/utløpt/manipulert → `400 cursor_ugyldig`.
- Keyset-predikat presist for `ORDER BY ts DESC, id DESC`:
  `(ts, id) < (siste_ts, siste_id)`.
- `øvre_grense` snapshotter maks (ts,id) ved første side → nye rader under
  paginering gir verken duplikater eller flytter snapshotet.

## 6. Liste-filter gjeninnført
`GET /v1/beslutninger?cursor=&limit=&policybeslutning=` — minst
`policybeslutning`-filter (TILLAT|STOPP|UNNTAK), som v2 av UI-spec krevde
og v1 av denne ved uhell droppet. Filteret inngår i cursor-bindingen (§5).

## 7. Unntakshistorikk: eget endepunkt (inventar → seks nye)
```
GET /v1/unntak/{id}/historikk?cursor=&limit=   scope exceptions:read
200: {rader: [{id, hendelse, fra_status, til_status, ts}], neste_cursor?}
```
Sortering `ts ASC, id ASC` (kronologisk); `id` er sekundærnøkkel siden
`ts` ikke er entydig. Detaljendepunktet `/v1/unntak/{id}` returnerer da
IKKE historikk inline (fjernes derfra). **Inventar: seks nye endepunkter**
+ eksisterende `GET /v1/unntak`.

## 8. Aktiv policy som lukket, redigert DTO
`GET /v1/policy/aktiv` returnerer en ALLOWLIST-DTO, aldri rå YAML eller
registerrad:
```
{policy_id, versjon, innholds_hash, roller[], handlinger[{navn, modus,
 grenser, vilkaar}], verifikatorer[{offentlig_id, autoritetsmetadata}]}
```
ALDRI: tokenhash, pepper, HMAC/private nøkler, krypteringsmetadata, interne
DB-felt, rå YAML, sikkerhetskonfig uten scope. Maskin-DTO med stabile
koder — UI oversetter, API leverer ikke presentasjonstekst.

## 9. Full auth-/feilmatrise per rute (allowlist, default-deny)
- Manglende/ugyldig sesjon → `401`.
- Gyldig token uten påkrevd scope → `403`.
- Ukjent/annen tenants detalj-ID → identisk `404`.
- Ugyldig cursor/filter/limit → `400` (lukkede koder).
- Intern feil → sanitert `500` + korrelasjons-ID (ingen stack/intern info).
- **`bruker`-rollen forbudt på ALLE muterende endepunkter via
  allowlist/default-deny** — ikke bare testet mot `POST /v1/beslutning`.
  Ruteregisteret deklarerer påkrevd scope per rute; manglende deklarasjon
  = ingen tilgang (fail-closed).

## Testplan-korreksjon (reviewens)
- Detaljruter (`/beslutninger/{id}`, `/unntak/{id}`, `/unntak/{id}/historikk`):
  ukjent OG annen tenant → identisk 404.
- Liste/oversikt/policy: tenant A får ALDRI rader, tellinger eller
  policyfelt fra tenant B (RLS-bevist).
- Cursor fra tenant B avvist hos tenant A.
- Hver `resultat.art` × `evidensstatus`-kombinasjon som er ULOVLIG avvises
  av servermodellen; hver lovlig mappes fra faktiske DB-rader.
- Uten/med `security:read` → sikkerhet fraværende/boolean, alle varianter.
- `bruker` default-deny mot hvert muterende endepunkt (allowlist-test).
- Oversikt-invariant: `tillatt + stoppet + unntak = totalt`, rullende 24t UTC.

## Svar på v1-spørsmålene (reviewens konklusjoner vedtatt)
1. Sesjonsutstedelse = egen PR. PR-008 definerer/håndhever `bruker`-
   scopes; browserintegrasjon blokkert til sesjons-PR (utstedelse, levetid,
   fornyelse, logout, cookie, CSRF). Manuelle stagingtoken kun for API-test.
2. Ingen cache — tenant-/tidsindeksert spørring + ytelsesgrense.
   Invariant `tillatt+stoppet+unntak=totalt`, rullende 24t UTC, tidssone er
   presentasjon ikke beregning.
3. Variantene utvidet (kansellert/superseded lagt til, evidens skilt ut) —
   §2. Ingen åpne spørsmål igjen.
