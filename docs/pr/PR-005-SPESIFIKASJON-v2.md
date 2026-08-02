# PR-005 SPESIFIKASJON v2 (revidert etter ChatGPT NO-GO — til andre reviewrunde)

**Draft: Claude.ai · Status: VENTER PÅ ANDRE REVIEWRUNDE · Implementering
starter ikke før GO.** Basert på main 679ee9e. Endringslogg mot v1 nederst,
mappet mot de tolv bindende punktene + retro-P1-ene fra PR-004.

Scope: nettverksinngangen (API), M-37 unntakskø-lagring, policyregister,
idempotens, tenantbundne attestasjoner, herdet migrasjonskjører.
UTENFOR scope: offentlig eksponering, M-37-behandlingsmotor (PR-006),
JCS-kanonisering (PR-006, før eksterne verifikatorer), UI.

---

## Del 1: Migrasjon 002 — datamodell

### 1.1 Herdet migrasjonskjører (retro-P2, tatt inn nå)

`migrer()` skrives om FØR 002 kjøres første gang:
- `pg_advisory_lock` rundt hele kjøringen (én migrator om gangen)
- `migrasjoner`-tabellen utvides med `checksum TEXT NOT NULL` (SHA-256 av filinnhold)
- Kjører KUN versjoner som mangler i tabellen
- Historisk fil med endret checksum → hard feil, ingen kjøring
- Én migrasjonsfil = én transaksjon

### 1.2 Revisjonslogg — selvstendig evidensidentitet (retro-P1 #3)

```sql
ALTER TABLE revisjonslogg
  ADD COLUMN handling TEXT,
  ADD COLUMN request_id TEXT,
  ADD COLUMN idempotency_key TEXT,
  ADD COLUMN policy_content_hash TEXT,
  ADD COLUMN attestation_set_hash TEXT,
  ADD CONSTRAINT revisjonslogg_tenant_id_unik UNIQUE (tenant, id);
```
Nullable for historiske rader; API-veien setter alltid alle fem.
`attestation_set_hash` = SHA-256 over sorterte attestasjons-signaturer i
hendelsen (kobler evidens til nøyaktig hvilke bevis som forelå).

### 1.3 Unntakstabell med tenant-konsistent evidenskjede

```sql
CREATE TABLE unntak (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
  tenant        TEXT NOT NULL,
  loggpost_id   BIGINT NOT NULL,
  handling      TEXT NOT NULL,
  kategori      TEXT NOT NULL,
  prioritet     TEXT NOT NULL CHECK (prioritet IN ('normal','hoy')) DEFAULT 'normal',
  status        TEXT NOT NULL CHECK (status IN ('ny','under_behandling','løst','avvist')) DEFAULT 'ny',
  status_ts     TIMESTAMPTZ NOT NULL DEFAULT now(),
  forsok        INT NOT NULL DEFAULT 0 CHECK (forsok >= 0),
  claim_id      TEXT,
  claim_utloper TIMESTAMPTZ,
  payload_kryptert BYTEA NOT NULL,
  key_id        TEXT NOT NULL,
  alg           TEXT NOT NULL,
  nonce         BYTEA NOT NULL,
  FOREIGN KEY (tenant, loggpost_id) REFERENCES revisjonslogg (tenant, id)
);
```
- Kompositt-FK: et unntak hos tenant A kan aldri peke på evidens hos tenant B.
- RLS + FORCE som øvrige tabeller; samme runtime-/migrator-rolleskille.
- `kategori` valideres i tjenestelaget mot policyens `unntak.kategorier` før innsetting.
- Skriveregel: unntaksrad settes inn i SAMME transaksjon som loggposten.
  Feiler unntaksinnsettingen, committes heller ikke loggposten → STOPP.

**Kolonnelås (trigger):** UPDATE kan KUN endre `status`, `status_ts`,
`forsok`, `claim_id`, `claim_utloper`. Endring av tenant, loggpost_id,
handling, kategori, prioritet, payload-felter eller ts avvises.
DELETE og TRUNCATE avvises (samme mønster som revisjonsloggen).
Statusoverganger håndheves: ny→under_behandling→(løst|avvist),
under_behandling→ny (kun ved lease-utløp). Alt annet avvises.

**Historikk (append-only revisjonsspor for saksbehandling):**
```sql
CREATE TABLE unntak_historikk (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  unntak_id BIGINT NOT NULL, tenant TEXT NOT NULL,
  fra_status TEXT, til_status TEXT NOT NULL,
  aktor TEXT NOT NULL, ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
Fylles av trigger på statusendring. Append-only-triggere som ellers.

**Claiming (PR-006 bruker, PR-005 definerer):** atomisk via
`SELECT … FOR UPDATE SKIP LOCKED` + sett `under_behandling`, `claim_id`,
`claim_utloper = now() + lease`. Krasjet sak: når `claim_utloper < now()`
kan saken re-claimes; overgangen logges i historikk. `forsok` inkrementeres
ved claim; `forsok > policyens maks_auto_forsok` → kun manuell-kø (PR-006).

### 1.4 Idempotens

```sql
CREATE TABLE idempotens (
  tenant TEXT NOT NULL, nokkel TEXT NOT NULL,
  input_hash TEXT NOT NULL, respons JSONB NOT NULL,
  ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, nokkel)
);
```
`POST /v1/beslutning` KREVER `Idempotency-Key`-header (mangler → 400).
Samme (tenant, nøkkel, input_hash) → lagret respons returneres, ingen ny
evaluering. Samme nøkkel, annen input_hash → 409. Innsettingen skjer i
samme transaksjon som loggposten. Retention: 24 t (opprydding i PR-006).

### 1.5 Policyregister

```sql
CREATE TABLE policyer (
  tenant TEXT NOT NULL, policy_id TEXT NOT NULL, versjon TEXT NOT NULL,
  innholds_hash TEXT NOT NULL, status TEXT NOT NULL,
  innhold JSONB NOT NULL, aktiv BOOLEAN NOT NULL DEFAULT false,
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, policy_id, versjon)
);
CREATE UNIQUE INDEX en_aktiv_per_policy ON policyer (tenant, policy_id)
  WHERE aktiv;
```
- Innsetting kun etter bestått v0.2-skjema + semantisk validering; valideres
  PÅ NYTT ved lasting (fail-closed mot DB-korrupsjon).
- Atomisk aktivering: deaktiver gammel + aktiver ny i én transaksjon;
  delindeksen garanterer maks én aktiv.
- INGEN cache i PR-005: policy lastes per request fra DB. Eliminerer
  cache-invalidering ved dagens skala; cache er M-38-scope senere.
- Tillatt `meta.status` per miljø: produksjon = KUN `produksjon`
  (hardkodet, ikke konfigurerbart). Staging = `DISPONIT_TILLATTE_POLICYSTATUSER`
  (default `utkast,validert_pilot,produksjon` — utkast trengs for syntetisk
  last og kunde null; hver beslutning logger `mal_status` allerede).
- Beslutningen binder `policy_content_hash` (= innholds_hash) i loggposten.
- API stoler aldri på `policy_id` fra requesten alene: oppslag er alltid
  `WHERE tenant = <kontekstens tenant> AND policy_id = %s AND aktiv`.
  Treff hos annen tenant er umulig per spørring OG per RLS.

---

## Del 2: Attestasjoner — tenantbinding og replay (retro-P1 #1 og #2)

Attestasjonsmodellen utvides (attestering.py + motorkontrakt):

**Obligatoriske felter:** `verifikator`, `tenant_id`, `handling`,
`vilkaar`, `ressurs_id`, `policy_id`, `utstedt`, `utloper`, `jti`
(minst 128 bits tilfeldighet), + `signatur`.

**Motoren sammenligner ALT mot serverbygget virkelighet:**
`tenant_id == context.tenant_id`, `handling == hendelsens handling`,
`vilkaar == vilkårsnavnet den påberopes for`, `ressurs_id == event.ressurs_id`,
`policy_id == aktuell policys policy_id`, `utstedt <= naa < utloper`.
Ethvert avvik → STOPP med spesifikk Grunn-kode. Dagens PR-004-felter
(uten binding) avvises på API-veien — ingen bakoverkompatibilitet på
nettverksinngangen.

**Replaybeskyttelse:** for handlinger med `reversering.type: irreversibel`
konsumeres `jti` atomisk i samme transaksjon som loggpost/reservasjon:
```sql
CREATE TABLE attestasjon_jti (
  tenant TEXT NOT NULL, jti TEXT NOT NULL, konsumert TIMESTAMPTZ NOT NULL DEFAULT now(),
  utloper TIMESTAMPTZ NOT NULL, PRIMARY KEY (tenant, jti)
);
```
`INSERT` er konsumeringen; unikbrudd = replay → STOPP + sikkerhetsrouting.
Opprydding av utløpte jti-er: del av PR-006-vedlikehold.
For reversible handlinger verifiseres binding + signatur uten konsumering
(dokumentert avveining: full konsumering overalt vurderes i PR-006 når
volumtall finnes).

---

## Del 3: API

### 3.1 Endepunkter

| Endepunkt | Scope | Beskrivelse |
|---|---|---|
| `POST /v1/beslutning` | `decision:write` | Idempotency-Key påkrevd. Body `{policy_id, event}`. Svar `{beslutning, policy_id, policy_content_hash, begrunnelse[koder], unntak_id?, request_id}` |
| `GET /v1/unntak?status=` | `exceptions:read` | Kun metadata (id, ts, handling, kategori, prioritet, status) — ALDRI payload. Payload-tilgang krever `exceptions:manage` (PR-006). Keyset-paginering med signert cursor (HMAC m/ server-pepper); manipulert cursor → 400 |
| `GET /live` | ingen | Prosessen kjører. Ingen DB-avhengighet, ingen detaljer |
| `GET /ready` | ingen, MEN bindes kun til localhost | DB + migrasjonsversjon-match + nøkkelregister + policyregister OK. Eksponerer ok/ikke-ok, ikke versjonsdetaljer |

API bindes til loopback i PR-005 (staging bak brannmur). Binding til
ekstern interface er FORBUDT inntil TLS (og mTLS for tjeneste-til-tjeneste)
er på plass OG retro-P1-ene er verifisert lukket — dette er en kodet
oppstartssjekk, ikke en instruks (bind-adresse ≠ loopback uten
`DISPONIT_TLS_AKTIV` → prosessen nekter start).

### 3.2 Token

Format `token_id.secret` (secret ≥ 256 bits entropi). Tabell:
```sql
CREATE TABLE api_tokener (
  token_id TEXT PRIMARY KEY, tenant TEXT NOT NULL, rolle TEXT NOT NULL,
  scopes TEXT[] NOT NULL, secret_mac TEXT NOT NULL,
  aktiv BOOLEAN NOT NULL DEFAULT true,
  utloper TIMESTAMPTZ, last_used_at TIMESTAMPTZ,
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now()
);
```
- Oppslag på `token_id`; `secret_mac = HMAC-SHA256(server_pepper, secret)`;
  konstant-tids sammenligning. Ingen tabellskann, ingen ren SHA-256.
- Runtime-rollen kan IKKE lese tabellen: verifisering skjer via avgrenset
  `SECURITY DEFINER`-funksjon `verifiser_token(token_id, secret_mac)` som
  returnerer (tenant, rolle, scopes, aktiv, utloper) — eid av egen
  authenticator-rolle. Samme mønster som runtime/vakter-skillet fra PR-004.
- Tokenhåndtering er klassifisert: opprettelse/rotasjon/deaktivering er
  admin-operasjoner via CLI-skript på serveren (migrator-rollen), logges i
  revisjonsloggen med handling `token.opprett|roter|deaktiver`,
  reversering: deaktivering = direkte, opprettelse = kompenserende
  (deaktiver). Tokens forekommer ALDRI i logger, metrics eller feilmeldinger.
- `last_used_at` oppdateres maks én gang per minutt per token (skrivestøy).

### 3.3 Kontekst og nøkler

- `EvaluationContext` bygges UTELUKKENDE fra tokenoppslaget. Identitetsfelter
  i payload ignoreres.
- `nokler=last_nokler()` obligatorisk; manglende/ugyldig register → prosessen
  NEKTER OPPSTART.

### 3.4 Body-grense

ASGI-middleware teller faktisk mottatte bytes og avbryter ved 256 KiB —
uavhengig av Content-Length, også for chunked transfer. Ugyldig/manglende
Content-Length ved ikke-chunked → 411/400. Avbrutt → 413 uten parsing.

---

## Del 4: Feilveier — komplett kontrakt

Hver rad: HTTP-status, intern Grunn-kode, routing, fail-closed-resultat.
Routing-verdier: `avvis` (kun svar), `m37` (unntaksrad), `sikkerhet`
(sikkerhetslogg + metric + ratebegrensning), `drift` (alarm), kombinasjoner.

| Feilvei | HTTP | Grunn-kode | Routing |
|---|---|---|---|
| Manglende/ukjent/inaktivt/utløpt token | 401 | token_ugyldig | sikkerhet. INGEN tenant-sak, ingen policy-lasting |
| Gyldig token, manglende scope | 403 | scope_mangler | sikkerhet |
| Idempotency-Key mangler | 400 | idempotensnokkel_mangler | avvis |
| Samme nøkkel, annen input | 409 | idempotenskonflikt | avvis + metric |
| Body > 256 KiB / ugyldig lengde | 413/411 | body_for_stor | sikkerhet (aggregert) |
| Ugyldig JSON / feilformet request | 400 | request_feilformet | sikkerhet (aggregert, ALDRI full payload i sak) |
| Ukjent policy_id for tenant | 404 | policy_ukjent | avvis (avslører ikke andre tenanters policyer) |
| Policy feiler re-validering ved lasting | 500 | policy_korrupt | drift + m37 (tenant er kjent) |
| Policyregister/tokenregister utilgjengelig | 503 | register_utilgjengelig | drift. STOPP, ingen beslutning |
| DB-pool tom / timeout | 503 | db_utilgjengelig | drift |
| Attestasjon: signatur ugyldig / uten signatur | 200 m/ STOPP | attestasjon_signatur_ugyldig | sikkerhet + m37-referanse |
| Attestasjon: feil tenant/handling/policy-binding | 200 m/ STOPP | attestasjon_feil_binding | sikkerhet + m37-referanse |
| Attestasjon: jti-replay | 200 m/ STOPP | attestasjon_replay | sikkerhet (prioritert) + m37-referanse |
| verifikator_ikke_betrodd | 200 m/ STOPP | (eksisterende kode) | sikkerhet + m37-referanse — IKKE ordinær kø (kø-flom-vern) |
| Beslutning UNNTAK | 200 | (kategorikode) | m37 |
| STOPP m/ effekt=frys | 200 | (eksisterende) | m37 med prioritet=hoy |
| Autentisert, handlingsbar policyfeil (f.eks. policy_belopsgrense_ugyldig) | 200 m/ STOPP | (eksisterende) | m37 |
| Unntaksinnsetting feiler etter beslutning | 500 | unntaksskriv_feilet | drift. Transaksjonen rulles — loggpost committes IKKE, svar er STOPP |
| Revisjonslogging feiler | 500 | logging_feilet | drift + nødlogg (se 4.1) |
| Tenant-DEK mangler | 500 | tenantnokkel_mangler | drift. Rollback, STOPP — ALDRI klartekstlagring |
| Rate-grense nådd | 429 | rate_grense | sikkerhet (aggregert) |
| Manipulert paginering-cursor | 400 | cursor_ugyldig | sikkerhet |

Rate-limit-state er i-minne per prosess i PR-005 og NULLSTILLES ved
restart — deklarert svakhet, akseptert fordi API-et er loopback-only;
M-38 overtar med delt state før ekstern eksponering.

`sikkerhet`-routing i PR-005 = strukturert sikkerhetslogg (egen fil/journald,
uten payload/tokens) + teller-metric. Egen tabell er PR-006.

### 4.1 Auditfeilkontrakt (presisert)

Når revisjonslogg-commit feiler: best-effort strukturert NØDLOGG uten
payload (ts, tenant hvis kjent, request_id, feiltype) til journald/stderr,
metric + alarm, svar = STOPP, INGEN sideeffekt, og svaret merkes IKKE som
auditert. **1:1-garantien presiseres:** én loggpost for hver ferdigbehandlet
beslutning som returneres som auditert; ved auditfeil returneres et separat
fail-closed systemresultat som ikke regnes som gjennomført beslutning.

---

## Del 5: Unntaks-payload — minimering + tenantkryptering

- Lagre KUN felter M-37-behandling faktisk trenger (definert feltliste per
  Grunn-kategori i implementasjonen).
- Persondata erstattes med ugjennomsiktig kildereferanse
  `{connector, resource_id, field_id}` — ingen hashing av personverdier
  (ordlisteangrep + koblingsfare).
- Rest krypteres med envelope encryption: per-tenant DEK (AES-256-GCM),
  DEK pakket av KEK fra `DISPONIT_KEK` (miljø, 0600-disiplin som
  attestasjonsnøklene). `key_id`, alg og nonce lagres ved siden av ciphertext.
- Søkbare metadata (handling, kategori, status, prioritet, ts) utenfor ciphertext.
- Keyed HMAC (egen nøkkel) KUN der deterministisk deduplisering trengs.
- Dekryptert payload logges aldri.
- Retention: unntak løst/avvist > 180 dager får payload_kryptert nullstilt
  (metadata + historikk består); DEK-rotasjon: ny DEK per tenant per 90 dager,
  gamle DEK-er beholdes for lesing til retention utløper. Rotasjonsskript i PR-006.
- Mangler tenant-DEK → rollback + STOPP (rad i feilveitabellen).

---

## Del 6: Ytelsesport m01 (reproduserbar, per review F)

Skript `deploy/staging/lasttest-m01.py`:
- 10 s warmup utenfor måling; deretter NØYAKTIG 6 000 målte requests
- Open-loop 100 req/s, 20 samtidige forbindelser
- p95 på serversvartid over alle 6 000; krav < 150 ms
- Null HTTP-/timeout-/DB-feil; ingen skjulte retries (klient uten retry)
- Etterkontroll: 6 000 auditerte beslutninger = 6 000 revisjonsrader;
  antall unntaksrader stemmer med routingreglene for den syntetiske miksen
- CPU, minne, DB-connections og lock-waits samples hvert 5. sekund
- Testtoken og payloads forhåndsgenerert før warmup
- Resultat skrives som JSON-artefakt til `deploy/staging/artefakter/`
  og lastes opp som CI-/staging-artefakt; manifestfeltet peker på artefaktet
  (`krav_id: perf-m01-v1`), er aldri selv beviset

---

## Del 7: Manifestformat (rettet, med skjema)

```yaml
staging_sjekkliste:
  tester_gronne_pa_staging:        {status: ja}
  syntetisk_datasett_likt_lokalt:  {status: ja}
  revisjonslogg_korrekt:           {status: ja}
  feilinjisering_til_unntakskø:    {status: blokkert, blokkert_av: m37}
  ytelse_bestatt:                  {status: nei, krav_id: perf-m01-v1}
  rollback_testet:                 {status: nei, krav_id: rollback-m01-v1}
```
- Gyldige status: `ja | nei | blokkert`; `blokkert` KREVER `blokkert_av`.
- Manifest-skjema (JSON Schema) legges i `platform/core/` og valideres i CI.
- Per review D2 SPLITTES rollback i to: teknisk deaktivering via registeret
  (`rollback-m01-v1` — kan bevises NÅ, derfor `nei`, ikke blokkert) og
  ende-til-ende unntaksbehandling (dekkes av feilinjiserings-punktet som
  ER blokkert av m37).
- Manifestendringen leveres i DENNE PR-en (rører platform/ → gjennom porten),
  ikke i docs-PR-en. Docs-PR-en er KUN `docs/RUTINER.md`.

---

## Del 8: Testplan (minimum, alle negative)

Alle rader i feilveitabellen (Del 4) — én test per rad. I tillegg:
idempotens-replay returnerer identisk respons uten ny evaluering og uten ny
loggpost; 409 ved nøkkelgjenbruk med annet innhold; kompositt-FK avviser
kryss-tenant-referanse; kolonnelås-trigger avviser endring av låste felt;
statusovergangs- og historikk-trigger; TRUNCATE avvises på alle
append-only-tabeller; jti-konsumering under kappløp (20 tråder, én vinner);
attestasjon med feil tenant/handling/policy avvises enkeltvis (én test per
bindingsfelt); boot nekter uten nøkkelregister, uten KEK, og ved
ikke-loopback-bind uten TLS-flagg; token i klartekst finnes ikke i DB-dump;
runtime-rollen kan ikke SELECT-e api_tokener direkte; RLS på unntak,
idempotens, policyer og jti (kryss-tenant-lesing umulig); migrasjonskjører
avviser endret historisk fil og kjører kun manglende; policy med status
utenfor miljølisten avvises ved lasting.

---

## Endringslogg v1 → v2 (mot de tolv bindende punktene)

1. Tenantbundne + replaybeskyttede attestasjoner → Del 2 (lukker retro-P1 #1 og #2)
2. Idempotens → Del 1.4 + feilveitabell
3. Tenantbundet policyregister m/ versjon, hash, atomisk aktivering, miljøstatus → Del 1.5
4. Kompositt-FK for evidenskjeden → Del 1.3
5. Minimering + tenantkryptering (ikke SHA-256 av personverdier) → Del 5
6. Scope-basert tilgang, payload krever eget scope → Del 3.1/3.2
7. /live og /ready separert, readiness kun localhost → Del 3.1
8. Grunnkodebasert routing m/ eksplisitt tabell inkl. kø-flom-vern → Del 4
9. Full status-/claiming-/historikkmodell for M-37 → Del 1.3
10. Presisert auditfeilkontrakt + redefinert 1:1 → Del 4.1
11. Gyldig, skjemavalidert manifestformat + rollback-splitt → Del 7
12. Docs-only-korreksjon: manifest i PR-005, docs-PR kun RUTINER.md → Del 7

Retro-P1 #3 (evidensidentitet i revisjonsloggen) → Del 1.2.
Retro-P2 migrasjonskjører → Del 1.1 (tatt inn nå, ikke utsatt).
Retro-P2 JCS-kanonisering → PR-006, bindende før første eksterne verifikator.
Ytelsesport revidert per review F → Del 6.
