# PR-012 SPESIFIKASJON v4 — DELTA (fire kontrakter → GO)

**Draft: Claude.ai · v1–v3 står der de ikke motsies. Fire punkter.**

## 1. Transaksjonsmodellen rettet — `klar` overlever ikke rollback

v3s «teknisk feil → rollback, runden forblir `klar`» er umulig: `klar`
settes i den transaksjonen som rulles tilbake. Vedtatt modell (én
transaksjon, v1-scope):
- **Forventede motorutfall er DATA og committes** — TILLAT, STOPP,
  TIL_UNNTAK er alle gyldige resultater av en gjennomført behandling
  (utfallsmatrisen i v3 §5 gjelder uendret).
- **Reell teknisk/DB-feil ruller tilbake** siste attestasjon OG
  `klar`-overgangen. **Runden står fortsatt `apen`.**
- **Første fire-øyne-attestasjon består** — den ble skrevet i en TIDLIGERE
  transaksjon og berøres ikke.
- Samme idempotente request kan forsøkes på nytt (`Idempotency-Key`).
- En varig `klar`-tilstand etter teknisk feil ville krevd en separat
  prosessor — **det er en annen protokoll og ikke i v1** (deklarert).

Konsekvens: `klar` er en tilstand som KUN eksisterer i den vellykkede
transaksjonen, mellom siste attestasjon og `brukt`. Statusmaskinen
beholder den for auditerbarhet innen transaksjonen.

## 2. Terminalt godkjenningsutfall — stopper nye RUNDER, ikke bare nye saker

v3 hindret nye kø-saker, men samme policy kunne gjort «godkjenn»
tilgjengelig igjen og produsert ubegrensede beslutninger mot samme
intensjon. Rettet:
```sql
CREATE TABLE godkjenningsutfall (
  tenant TEXT NOT NULL, unntak_id BIGINT NOT NULL,
  hi_integritet_hash TEXT NOT NULL, policy_hash TEXT NOT NULL,
  decision_operation_id TEXT NOT NULL, motorutfall TEXT NOT NULL,
  beslutning_loggpost_id BIGINT NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, unntak_id, hi_integritet_hash, policy_hash),
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id),
  FOREIGN KEY (tenant, beslutning_loggpost_id) REFERENCES revisjonslogg (tenant, id)
);
```
- Append-only (trigger), RLS+FORCE.
- **Samme (sak, intensjonshash, policyhash) kan ALDRI godkjennes på
  nytt** — `tillatte_handlinger` utelater `godkjenn`, og POST-ruten
  avviser med lukket kode `allerede_godkjent_utfall`.
- Ny runde blir først mulig når **policyhash endres** ELLER en legitim ny
  **handlingsintensjon/generasjon** oppstår (ny hash).
- **Undertrykt UNNTAK får eksplisitt append-only kobling:**
  `beslutning_loggpost_id` her ER referansen fra original sak til den nye
  beslutningsloggposten — ikke fritekst i historikk (historikk skriver i
  tillegg, men er ikke bæreren).

## 3. Herdet opprettelsesfunksjon + snapshotfelt (CHECK kan ikke lese policy)

En PostgreSQL-CHECK kan ikke evaluere den aktive policyraden. Rettet:
- **Én herdet opprettelsesfunksjon** (den eksisterende beslutningsveien)
  leser policyen og setter uforanderlig snapshotfelt
  `intensjon_pakrevd BOOLEAN NOT NULL` på unntaksraden.
- Lokal CHECK håndhever konsistensen:
```sql
CHECK (NOT intensjon_pakrevd OR (
  handlingsintensjon_kryptert IS NOT NULL AND hi_key_id IS NOT NULL
  AND hi_nonce IS NOT NULL AND hi_integritet_hash IS NOT NULL
  AND hi_skjemaversjon IS NOT NULL))
```
- `intensjon_pakrevd` er kolonnelåst (kan aldri endres etter innsetting).
- **AES-GCM AAD binder:** `tenant ‖ unntak_id ‖ target_action ‖
  hi_skjemaversjon ‖ policy_hash`. Ciphertext kan dermed ikke flyttes
  mellom saker — dekryptering feiler hvis konteksten avviker.

## 4. MAC-nøkkelens fulle livssyklus

- **Algoritme:** HMAC-SHA-256, nøkkel ≥ 256 bit.
- **Tilstander:** `signerer` | `verifiserer` | `pensjonert`.
  **Nøyaktig ÉN nøkkel i `signerer`** (håndheves i registeret ved boot —
  flere → prosessen nekter start).
- Gamle nøkler beholdes i `verifiserer` så lenge ubrukte eller
  auditerbare attestasjoner refererer dem (spørring mot
  `menneskelig_attestasjon.mac_key_id` før pensjonering).
- **Ukjent eller `pensjonert` nøkkel ved beslutning → fail-closed**
  (STOPP, ingen godkjenning).
- **Rotasjonstest MENS en fire-øyne-runde er åpen:** første attestasjon
  signert med K1, rotasjon til K2, andre attestasjon signert med K2 →
  begge må verifisere (K1 i `verifiserer`), runden fullfører.
- **`klar → brukt` kontrollerer utløp OG MAC for BEGGE attestasjoner
  under samme lås** — ikke bare den siste.

## Tester (tillegg)
Teknisk feil under siste godkjenning → runde `apen`, første attestasjon
består, retry med samme nøkkel virker · samme (sak, intensjon, policy)
kan ikke godkjennes to ganger · ny policyhash åpner ny runde · undertrykt
UNNTAK har FK-kobling til ny loggpost (ikke bare historikktekst) ·
ciphertext flyttet til annen sak → dekryptering feiler (AAD) ·
`intensjon_pakrevd` kan ikke endres · to nøkler i `signerer` → boot nektes
· pensjonert nøkkel → fail-closed · rotasjon midt i åpen runde fullfører ·
utløpt FØRSTE attestasjon oppdages ved `klar → brukt`.
