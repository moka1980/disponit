# PR-012 SPESIFIKASJON v2 — DELTA (åtte kontrakter → GO)

**Draft: Claude.ai · Prinsippet står (mennesket attesterer, motoren
beslutter). Åtte kontrakter lukket. Punkt 1 er en FORMELL UTVIDELSE av
dataminimeringskontrakten — ikke en omgåelse.**

## 1. `handlingsintensjon` — formell utvidelse av minimeringskontrakten

**Problemet:** minimert payload mangler `belop` (PR-005b allowlist), så
motoren kan ikke re-evaluere en `over_grense`-sak — og det er nettopp den
saken et menneske vil godkjenne. Uten dette er godkjenn-veien tom.

**Beslutning: lagre en `handlingsintensjon` — eksplisitt, avgrenset,
kryptert, med egen begrunnelse per felt.** Dette er alternativ (a) fra
PR-007-briefen, som var riktig å avvise DER (ikke nødvendig) og er
nødvendig HER. v2 Del 5-kontrakten endres formelt:

```sql
-- migrasjon 010, på unntak
handlingsintensjon_kryptert BYTEA,   -- tenant-DEK, envelope som payload
hi_key_id TEXT, hi_nonce BYTEA,
hi_integritet_hash TEXT,             -- over CIPHERTEXT (ikke klartekst)
hi_skjemaversjon INT
```
- **Lukket feltliste** (`additionalProperties: false`), kun det motoren
  trenger for re-evaluering av godkjennbare vilkår:
  `{handling, ressurs_id, belop?, valuta?, tidspunkt, dataklasser,
    dataklasser_kilde, attestasjoner_referanser[]}`.
  Hvert felt har begrunnelse i spesifikasjonen; ingen fri passthrough.
- **Skrives KUN ved beslutninger som kan bli menneskelig godkjennbare**
  (policyen har `menneskelig_overstyring` for handlingen) — ikke for alle
  beslutninger. Minimeringen består der den kan.
- Uforanderlig (kolonnelås), append-only, retention = sakens, destrueres
  ved crypto-shredding (DEK), aldri DELETE.
- Persondata: samme kildereferanse-regel som payload — ingen råverdier.
- **Ingen verdi hentes fra klient, tidsnærhet eller «siste rad».**
- Saker uten `handlingsintensjon` (eldre, eller handling uten
  `menneskelig_overstyring`) → **godkjenn er ikke tilgjengelig**, kun
  avvis/eskaler. Fail-closed, synlig i `tillatte_handlinger`.

## 2. Menneskelig attestasjon = lukket, server-MAC-et konvolutt

Egen type `disponit_human_approval_v1` — **mennesket registreres ALDRI
som PR-007-verifikatormodul**. Konvolutten MAC-es server-side (pepper fra
credential, aldri i DB) og binder:
`tenant · unntak_id · godkjenningsrunde · bruker_id · authz_version ·
handling (target action) · ressurs_id · hi_integritet_hash ·
policy_versjon + policy_hash · belop + valuta (når relevant) · utstedt ·
utloper · jti · decision_operation_id`.
Motoren verifiserer ALLE bindingene mot serverbygget kontekst (samme
strenghet som PR-006 v3 attestasjonsbinding). Avvik → STOPP + sikkerhet.

## 3. Lukket mapping: hvilke VILKÅR kan godkjennes (ikke kategorier)

Kategori-listen erstattes. Et menneske kan ikke «godkjenne bort»
`teknisk_feil` eller dikte manglende data:
```yaml
menneskelig_overstyring:
  godkjennbare:                       # lukket liste (grunnkode, handling)
    - grunnkode: belop_over_grense
      handling: faktura.bokfor
      belop_maks: "50000.00"
      valuta: NOK                     # PÅKREVD med belop_maks
  krever_rolle: okonomiansvarlig
  krever_fire_oyne: true
  begrunnelse_pakrevd: true
```
- Ukjent (grunnkode, handling)-kombinasjon → **fail-closed**, ingen
  godkjenning.
- `belop_maks` KREVER `valuta`; **ingen implisitt valutakonvertering** —
  ulik valuta i saken enn i regelen = ikke godkjennbar.
- `teknisk_feil`, `manglende_data` og lignende er IKKE godkjennbare —
  de løses av M-37 eller avvises.

## 4. Egne statuser for menneskeflyten (ikke `under_behandling`)

`under_behandling` er M-37-arbeiderens claim-tilstand; gjenbruk ville
gjort saken claimbar av feil prosess. Nye statuser (migrasjon 010):
```
manuell → venter_godkjenning        (godkjenn-flyt startet)
venter_godkjenning → venter_andre_godkjenner   (fire øyne, én mottatt)
venter_godkjenning | venter_andre_godkjenner → godkjenning_klar
godkjenning_klar → venter_utførelse | løst     (ny beslutning kjørt)
alle tre → manuell                  (utløp/kansellering, runde lukkes)
alle tre → avvist                   (avvis-handlingen)
```
**M-37-arbeideren claimer ALDRI disse tre statusene** (claim-funksjonen
filtrerer på `status='ny'` + `verifikasjon_*`, uendret) — eierskapet er
utvetydig.

## 5. `godkjenningsrunde` — fire øyne uten permanent frysing

Egen tabell løser append-only-frysingen:
```sql
godkjenningsrunde(tenant, unntak_id, runde INT, status TEXT
  CHECK (status IN ('apen','klar','brukt','utlopt','kansellert')),
  apnet, utloper, PRIMARY KEY (tenant, unntak_id, runde))
CREATE UNIQUE INDEX en_apen_runde ON godkjenningsrunde
  (tenant, unntak_id) WHERE status = 'apen';
```
- Overganger: `apen → klar → brukt`; `apen → utlopt|kansellert`
  (trigger-håndhevet, auditert).
- **Attestasjonens unikhet inkluderer runden:**
  `UNIQUE (tenant, unntak_id, runde, bruker_id)` — samme bruker kan delta
  i en NY runde etter utløp; attestasjoner slettes aldri.
- **Ny runde arver ALDRI gamle godkjenninger.**
- **Ved siste godkjenning revalideres BEGGE brukeres aktive medlemskap,
  rolle og `authz_version`** — en godkjenner som mistet rollen underveis
  teller ikke.

## 6. Avvis kan ikke ubetinget love «aldri utført»

Avvis kontrollerer oppdrags- og kapabilitetstilstand ATOMISK:
| Tilstand | Utfall |
|---|---|
| Ingen oppdrag, ingen utestående kapabilitet | Kanseller ev. fenced, → `avvist` |
| Oppdrag `opprettet` (ikke claimet) | Kanseller fenced (`kansellert`), → `avvist` |
| Oppdrag `plukket`/`utfort`, eller kapabilitet utestående, eller ukjent | **IKKE lov å påstå «avvist uten utførelse»** → saken → `manuell` med sikkerhets-/avklaringsflagg og tydelig UI-tekst |
| Sen kvittering ankommer etter avvis | Egen tilstand `sen_kvittering_etter_avvis` + sikkerhetssak; endrer ikke terminal status (PR-007 v4-mønsteret) |
UI-teksten sier aldri «ingenting ble utført» uten at DB beviser det.

## 7. Eskaleringsmål er server-returnert

`eskalert_til` kan ikke være fri rolle fra body:
- `GET /v1/unntak/{id}` returnerer `tillatte_eskaleringsmal[]` — lukket
  liste utledet fra aktiv policy + organisasjonens rollemodell.
- Mål utenfor listen → `400`, lukket kode.
- **Selveskalering til egen rolle avvises** (meningsløs).
- Eskalering ØKER `saksversjon`, er idempotent på `Idempotency-Key`, og
  skriver historikk.

## 8. Én herdet kodevei — ingen intern HTTP i transaksjonen

«API + motor i én transaksjon» krever samme DB-connection.
`behandle_unntakshandling(...)` (SECURITY DEFINER der nødvendig, ellers
kjernefunksjon) kjører i NØYAKTIG denne rekkefølgen, alt eller ingenting:
1. `sett_kontekst` (tenant, aktør, request_id) FØRST
2. Lås saken (`FOR UPDATE`)
3. Kontroller `saksversjon`, status og fencing (claim_generation)
4. Reserver idempotens
5. Revalider aktiv policy + ALLE godkjennere (medlemskap, rolle, authz_version)
6. Skriv attestasjon + rundestatus
7. Kjør ny beslutning via **eksisterende beslutningskjerne på samme
   connection** (aldri internt HTTP-kall)
8. Skriv revisjonslogg + eventuell outbox-rad
9. Commit alt eller ingenting
Låserekkefølge føyes til den dokumenterte: `unntak → godkjenningsrunde →
menneskelig_attestasjon → oppdrag → kapabilitet`.

## Svar på reviewens tre punkter (vedtatt)
1. Attestasjon er riktig grense — men KUN med komplett handlingsintensjon
   (§1) og eksplisitt policy-definert vilkår (§3). Avvis/eskaler avslutter
   køflyten uten å autorisere forretningshandlingen.
2. Egne statuser, ja (§4) — ikke `under_behandling`.
3. Fire øyne synlig med egen runde, utløpstid og gjenværende krav (§5);
   utløp lukker runden, sletter aldri attestasjoner.

## Evidenskrav (revidert `behandling-m37-v1`)
Som v1, pluss: sak uten handlingsintensjon → godkjenn utilgjengelig ·
ulik valuta → ikke godkjennbar · `teknisk_feil` kan ikke godkjennes ·
utløpt runde → samme bruker kan delta i ny runde · godkjenner som mister
rollen mellom første og andre godkjenning → avvist ved revalidering ·
avvis på claimet oppdrag → manuell m/ avklaring, ALDRI «ikke utført» ·
eskaleringsmål utenfor liste → 400 · ingen intern HTTP i transaksjonen
(statisk sjekk) · handlingsintensjon aldri i klartekst i logg/dump.
