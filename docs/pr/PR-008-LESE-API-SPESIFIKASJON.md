# PR-008 SPESIFIKASJON — Lese-API for M-1 kundeflate (til ChatGPT-porten)

**Draft: Claude.ai · Basis: gjeldende main (Starlette-app, `sett_kontekst`,
`verifiser_token` SECURITY DEFINER, feilveitabell som data, RLS+FORCE).
Leverer de fem nye lese-endepunktene UI-spesifikasjonen (v1–v4) avhenger
av. GO-ens fire bindende krav er innarbeidet som spesifikasjonskrav.
Rent lese-API — ingen mutasjon, ingen ny forretningslogikk.**

## 0. Fundament dette bygger på (redesignes ikke)

Alle endepunkter arver nettverksinngangens eksisterende porter:
to-transaksjonsmodell (pre-auth → forretningstransaksjon), `sett_kontekst`
som FØRSTE operasjon i forretningstransaksjonen, RLS+FORCE mot
`disponit.tenant`, boot-sjekkene, body-grense, loopback/TLS-porten. Lese-
endepunktene legger KUN til nye ruter — ingen ny motor, ingen skriving.

## 1. Brukersesjon: egen token-klasse for browseren (GO-krav 3)

Kundens nettleser bruker ALDRI agent-, modul- eller M-37-token. Ny
token-rolle `bruker` med rene lese-scopes:
- `decisions:read`, `exceptions:read`, `policy:read`, valgfritt
  `security:read` (egen ops/compliance-rolle).
- Utstedes av en serveradministrert, tenantbundet brukersesjon.
- Verifiseres via samme herdede `verifiser_token` (utvides med scope-retur
  hvis ikke allerede der) — brukertoken kan KUN nå lese-endepunktene, aldri
  `POST /v1/beslutning` eller oppdrags-/kvitteringsveiene (scope-sjekk per
  rute).
- Transport: hvis cookie brukes → `HttpOnly`, `Secure`, `SameSite`, og
  CSRF-token klargjort for fremtidige mutasjoner. Ingen token i
  `localStorage`/`sessionStorage`/URL/DOM (Codex-port + UI-spec §4).

## 2. De fem endepunktene

Alle: `bruker`-token, tenant fra sesjonen (aldri body/query), RLS gjør
kryss-tenant til null rader, ukjent OG annen tenants ID → identisk 404
(GO-krav, Codex-port). Feil bruker eksisterende feilmodell.

### 2.1 `GET /v1/oversikt` — scope `decisions:read`
Serverberegnet døgnaggregat, fast vindu, ingen trend/mål (ikke M-16):
```
200: {vindu_start, vindu_slutt, tidssone, tillatt, stoppet, unntak, totalt}
```
Beregnes fra revisjonsloggen for tenanten, siste 24 t rullende.

### 2.2 `GET /v1/beslutninger` — scope `decisions:read`
Cursor-paginert liste, redusert rad:
```
?cursor=&limit=  (limit ≤ 100, default 50)  sortering ts DESC
200: {rader: [{id, ts, handling, policybeslutning[TILLAT|STOPP|UNNTAK],
              begrunnelse[koder]}], neste_cursor?}
```
Keyset-cursor (ts,id), signert som eksisterende cursor-mønster.

### 2.3 `GET /v1/beslutninger/{id}` — scope `decisions:read`
Selvstendig detalj (GO-krav 4 + diskriminert union GO-krav 1):
```
200: {
  id, handling, begrunnelse[koder display-safe],
  policy_versjon, policy_hash, beslutning_ts,
  revisjonslogg_ref,               // se §3 om ID-identitet
  resultat: <diskriminert union, se §4>
}
```

### 2.4 `GET /v1/unntak/{id}` — scope `exceptions:read`
(Listen `GET /v1/unntak` finnes.) Detalj:
```
200: {id, ts, handling, kategori[enum], sakstype[enum], status[enum],
      prioritet[enum], begrunnelse[koder], historikk[cursor-paginert]}
```
Aldri payload/attestasjoner/nøkler. Enum-verdier = faktiske maskinkoder
(migrasjon 003/007).

### 2.5 `GET /v1/policy/aktiv` — scope `policy:read`
Aktiv policy for tenanten, menneskelesbar (allerede validert struktur fra
`policyer`-registeret): roller, handlinger med grenser/vilkår,
verifikatorer. Read-only (UI-spec §6).

## 3. ID-identitet avklart (GO-krav 4)

Beslutningens `id` ER revisjonslogg-ID-en — de er samme identitet
(beslutninger ER revisjonsloggposter). Derfor: ETT felt `id`, og
`revisjonslogg_ref` DROPPES som separat felt (min v3 antok de kunne være
ulike; de er ikke). Detaljendepunktet slår opp direkte på
revisjonslogg-id under tenant-RLS. Dyplenke `/{id}` laster selvstendig
uten listekall (Codex-port).

## 4. `resultat` som diskriminert union (GO-krav 1)

Ikke et kartesisk produkt av enums — eksakte `oneOf`-varianter med
diskriminator `art`:
```
oneOf:
  {art: "policy_stoppet"}                              // STOPP
     + valgfritt sikkerhet (§5)
  {art: "sideeffektfri_tillatt"}                       // TILLAT, ingen utførelse
  {art: "outbox_venter",  oppdrag_id}                  // TILLAT + venter
  {art: "outbox_utfort",  oppdrag_id, kvittering:"GYLDIG"}
  {art: "outbox_feilet",  oppdrag_id, kvittering:"GYLDIG"}
  {art: "kvittering_sen_eller_konflikt", oppdrag_id,
        kvittering:["SEN"|"KONFLIKT"]}                 // påstår ALDRI UTFØRT
  {art: "til_unntak", unntak:{id, kategori, status}}   // UNNTAK
     + valgfritt sikkerhet (§5)
```
Ingen kombinasjon er gyldig bare fordi hvert enkeltfelt er kjent —
serveren returnerer én navngitt variant, UI matcher på `art` og viser
`Feiltilstand` for ukjent variant (fremtidssikkert: ny motor-variant
krasjer ikke UI). `outbox_utfort` KAN ikke oppstå uten `kvittering:GYLDIG`
— beslutning≠utførelse håndhevet i skjemaet (OpenAPI/servermodell avviser
ulovlige kombinasjoner — Codex-port).

## 5. Sikkerhetsfeltets fravær er del av skjemaet (GO-krav 2)

- Token UTEN `security:read` → `sikkerhet` er FRAVÆRENDE fra responsen.
- Token MED `security:read` → `sikkerhet: {sak_finnes: bool}`.
Manglende felt tolkes ALDRI som `false` (UI validerer mot riktig
responsvariant per scope). To Codex-porter: uten scope → feltet mangler;
med scope → korrekt boolean.

## 6. Testplan (Codex-porter fra GO + tenant)

Per endepunkt: kryss-tenant-ID → 404 identisk med ukjent (fem tester);
`bruker`-token når IKKE `POST /v1/beslutning` (scope-nekt); dyplenke-detalj
uten forutgående listekall; OpenAPI/servermodell avviser hver ulovlig
beslutning–utførelse-kombinasjon (én test per ulovlig par); uten
`security:read` → sikkerhet fraværende; med → boolean; ingen
token/hemmelighet i browserlager/DOM/URL/logg (statisk + runtime);
cursor-paginering (limit-tak, keyset-stabilitet); frontend kan ikke
erklæres ferdig mot kun mocks (integrasjonsport mot merget endepunkt).

## Spørsmål til ChatGPT

1. Bør `bruker`-token/brukersesjon spesifiseres FULLT her (utstedelse,
   levetid, fornyelse, CSRF), eller er det egen sesjons-PR og PR-008
   dekker kun at endepunktene KREVER `bruker`-scope? Jeg lener mot det
   siste — lese-API og sesjonsutstedelse er to kontrakter.
2. `GET /v1/oversikt` beregner døgnaggregat ved hver forespørsel — akseptabelt
   ved dagens skala, eller bør det caches/materialiseres allerede nå?
3. `resultat`-unionens `art`-varianter: dekker de syv alle faktiske
   utfall motoren+M-37 kan produsere, eller ser du et utfall som ikke
   mapper til nøyaktig én variant?
