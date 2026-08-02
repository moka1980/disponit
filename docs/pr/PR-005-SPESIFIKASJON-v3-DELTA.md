# PR-005 SPESIFIKASJON v3 — DELTA mot v2

**Draft: Claude.ai · Kun endringene reviewen krevde. Alt annet i v2 står
uendret. Nummereringen følger reviewens syv punkter.**

---

## 1. Migrasjon 003 + checksum-bootstrap

- PR-005 leverer `platform/core/db/migrations/003_unntak_api_policy.sql`
  (ikke 002 — main har allerede 001 og 002). Alle filbaner, testplan og
  forventninger i v2 oppdateres tilsvarende: forventet migrasjonstilstand
  etter PR-005 er `[1, 2, 3]`.
- **Bootstrap-kontrakt for checksum-herdingen**, i denne rekkefølgen, som
  eksplisitt migrator-operasjon FØR 003 kjøres:
  1. Bootstrap-skript (`deploy/staging/migrasjon-bootstrap.py`, kjøres av
     migrator-rollen, én gang, under `pg_advisory_lock`)
  2. Checksums for 001 og 002 er FASTE, reviewede konstanter i skriptet —
     beregnet fra main 679ee9e og verifisert i PR-review, ikke lest blindt
     fra disk ved kjøring. Skriptet feiler hardt hvis diskfilene ikke
     matcher konstantene.
  3. Backfill: `UPDATE migrasjoner SET checksum = <konstant> WHERE versjon IN (1,2)`
  4. Deretter `ALTER TABLE migrasjoner ALTER COLUMN checksum SET NOT NULL`
  5. Fra dette punktet er historiske migrasjonsfiler immutable — endret fil
     → hard feil i kjøreren
  6. **Kjøreren eier transaksjonen:** én migrasjon = én transaksjon startet
     av kjøreren. Migrasjonsfiler skal IKKE inneholde `BEGIN/COMMIT`.
     003 skrives uten; 001/002 beholdes uendret (immutable) — kjøreren
     detekterer legacy-`BEGIN/COMMIT` i nøyaktig versjonene 1 og 2 og
     kjører dem rått; alle versjoner ≥ 3 med `BEGIN/COMMIT` i filen avvises.

## 2. Konsistent retentionmodell: crypto-shredding

Reviewens anbefalte løsning velges. Alle tre utsagn blir sanne:
- `payload_kryptert` forblir `NOT NULL` og UFORANDERLIG (kolonnelåsen står).
- Retention gjennomføres ved å DESTRUERE tenant-DEK-en for nøkkelversjonen
  når alle unntak som bruker den er løst/avvist og eldre enn 180 dager:
  `wrapped_dek` overskrives med NULL og `destruert_ts` settes i
  nøkkelregisteret (se pkt. 3). Ciphertext består som evidens-artefakt,
  men kan ikke lenger dekrypteres.
- Destruksjonen logges i `unntak_historikk` per berørt sak
  (`til_status` uendret, egen hendelsestype `dek_destruert`) og i
  revisjonsloggen som handling `tenantnokkel.destruer` (irreversibel,
  policystyrt — kun migrator/vedlikeholdsrollen).
- DEK-rotasjon hver 90. dag skaper naturlige destruksjonskohorter:
  én nøkkelversjon dekker maks 90 dagers saker.

## 3. Tenant-DEK-register

```sql
CREATE TABLE tenant_nokler (
  tenant       TEXT NOT NULL,
  key_id       TEXT NOT NULL,
  wrapped_dek  BYTEA,                 -- NULL etter destruksjon (pkt. 2)
  wrap_alg     TEXT NOT NULL,         -- 'AES-256-GCM-KEK-v1'
  dek_alg      TEXT NOT NULL,         -- 'AES-256-GCM'
  opprettet    TIMESTAMPTZ NOT NULL DEFAULT now(),
  aktiv        BOOLEAN NOT NULL DEFAULT true,
  destruert_ts TIMESTAMPTZ,
  PRIMARY KEY (tenant, key_id),
  CHECK (wrapped_dek IS NOT NULL OR destruert_ts IS NOT NULL)
);
```
- RLS + FORCE; runtime kan lese (trenger DEK for kryptering/dekryptering),
  kun vedlikeholdsrollen kan INSERT/UPDATE; destruksjon (`wrapped_dek → NULL`)
  er eneste tillatte UPDATE, håndhevet av trigger.
- `unntak` får tenantkonsistent FK:
  `FOREIGN KEY (tenant, key_id) REFERENCES tenant_nokler (tenant, key_id)`.
- Maks én `aktiv` per tenant: delindeks `UNIQUE (tenant) WHERE aktiv`.
- **GCM-tag:** inngår i ciphertext — `payload_kryptert = ct || tag`
  (tag = siste 16 bytes), nonce i egen kolonne som før. Spesifisert
  eksplisitt så eksterne implementasjoner ikke gjetter.

## 4. Tenantbundet unntakshistorikk

```sql
ALTER TABLE unntak ADD CONSTRAINT unntak_tenant_id_unik UNIQUE (tenant, id);

CREATE TABLE unntak_historikk (
  id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant     TEXT NOT NULL,
  unntak_id  BIGINT NOT NULL,
  hendelse   TEXT NOT NULL CHECK (hendelse IN
             ('statusendring','claim','claim_utlopt','dek_destruert')),
  fra_status TEXT,
  til_status TEXT,
  aktor      TEXT NOT NULL,           -- fra DB-kontekst, se under
  request_id TEXT,
  claim_id   TEXT,
  ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id)
);
```
- RLS + FORCE, append-only-triggere mot UPDATE/DELETE/TRUNCATE — samme
  mønster som revisjonsloggen.
- **Aktor fra serverkontekst, aldri klientpayload:** tjenestelaget setter
  `SET LOCAL app.aktor = <token-identitet>` og `SET LOCAL app.request_id`
  ved transaksjonsstart (fra autentisert kontekst); historikk-triggeren
  leser `current_setting('app.aktor')`. Mangler settingen → triggeren
  feiler transaksjonen (fail-closed, ingen anonym historikk).

## 5. Implementerbar sikkerhetsrouting: sakstype i unntak

Reviewens alternativ 1 velges:
```sql
ALTER TABLE unntak ADD COLUMN sakstype TEXT NOT NULL
  CHECK (sakstype IN ('normal','sikkerhet','drift')) DEFAULT 'normal';
```
- Routingtabellen i v2 Del 4 oppdateres: `m37` → `sakstype=normal`;
  `sikkerhet + m37-referanse` → `sakstype=sikkerhet` (+ strukturert
  sikkerhetslogg og metric som før); `drift` med identifiserbar tenant →
  `sakstype=drift`. Uten identifiserbar tenant: kun nødlogg/alarm, ingen rad.
- `sakstype` inngår i kolonnelåsen (kan aldri endres etter innsetting).
- Kø-flom-vernet består: ordinære M-37-arbeidere (PR-006) claimer KUN
  `sakstype='normal'`; sikkerhets- og driftssaker har egne køer med samme
  claim-/status-/historikkmekanisme. Signaturbrudd/replay får dermed en
  stabil M-37-ID uten å berøre normal kø.
- `GET /v1/unntak` med `exceptions:read` returnerer kun `sakstype=normal`;
  sikkerhet/drift krever eget scope (`security:read`) — lagt til i
  scopelisten.

## 6. Idempotens under samtidighet (bindende)

- `idempotens`-tabellen får `status TEXT NOT NULL CHECK (status IN
  ('paagaar','ferdig')) DEFAULT 'paagaar'` og `respons` blir nullable
  (NULL mens paagaar; CHECK: `status='ferdig'` ⇒ `respons IS NOT NULL`).
- **Claim før evaluering, serialisert per nøkkel:**
  1. `pg_advisory_xact_lock(hashtextextended(tenant||nokkel, 0))`
  2. `INSERT ... ON CONFLICT (tenant, nokkel) DO NOTHING RETURNING *`
  3. Fikk rad → vinner: evaluer, skriv loggpost/unntak/jti i samme
     transaksjon, oppdater raden til `ferdig` med respons, commit
     (låsen slippes ved commit).
  4. Fikk ikke rad → eksisterende rad leses ETTER at låsen er ervervet
     (vinnerens transaksjon er da committet): `input_hash` lik → returner
     lagret respons; ulik → 409. `paagaar`-rad med lås ledig betyr krasjet
     vinner → raden claimes på nytt (UPDATE til egen request_id under låsen)
     og evalueres — aldri evig blokkert.
- **Bindende test (tas inn i testplanen):** 20 samtidige requests, samme
  tenant/nøkkel/input → nøyaktig én evaluering (motor-kall telles),
  nøyaktig én revisjonsrad, nøyaktig maks én unntaksrad og én
  jti-konsumering, alle 20 svar byte-identiske.

## 7. Herdet SECURITY DEFINER

`verifiser_token(...)`-funksjonen spesifiseres med:
- `SET search_path = pg_catalog, public` (fast, i funksjonsdefinisjonen)
- Kun skjemakvalifiserte tabellnavn i kroppen; ingen dynamisk SQL
- Eier: egen `NOLOGIN`-rolle (`disponit_authenticator`)
- `REVOKE ALL ON FUNCTION ... FROM PUBLIC` +
  `GRANT EXECUTE` kun til API-runtime-rollen
- Signatur: inn `token_id`; ut `(tenant, rolle, scopes, aktiv, utloper)` —
  **`secret_mac` returneres aldri**. MAC-sammenligningen skjer INNE i
  funksjonen: runtime sender `HMAC(pepper, secret)`-kandidaten inn, og
  funksjonen svarer kun gyldig/ugyldig + kontekst. Pepper ligger hos
  API-prosessen (miljø), aldri i databasen — DB-dump alene er dermed
  ubrukelig for tokenforfalskning.
- Negativ test: runtime-rollen får permission denied på direkte
  `SELECT * FROM api_tokener`; funksjonskall med feil MAC gir ugyldig
  uten timing-forskjell (konstant-tids sammenligning i funksjonen).

---

**Testplan-tillegg (oppsummert):** samtidig idempotens (pkt. 6),
migrasjonsbootstrap (checksums matcher konstanter; endret 001/002 → hard
feil; 003 med BEGIN/COMMIT avvises), FK tenant-kryssing avvises i
historikk og tenant_nokler, dek_destruert-flyt (payload uleselig etterpå,
metadata består, historikkrad finnes), sakstype-filtrering i
GET /v1/unntak, historikk uten app.aktor-setting feiler transaksjonen,
SECURITY DEFINER-negativtestene i pkt. 7.
