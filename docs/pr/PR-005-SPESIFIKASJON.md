# PR-005 SPESIFIKASJON (draft til ChatGPT-review — ingen kode er skrevet ennå)

**Draft: Claude.ai · Status: VENTER PÅ SPESIFIKASJONSREVIEW (steg 2) ·
Implementering starter ikke før review-svar foreligger.**

Scope: API-skjelett (nettverksinngangen til motoren) + M-37 unntakskø-lagring.
Basert på main 679ee9e (95 tester, PostgreSQL-tilstandslag med RLS, HMAC-attestasjoner).

## Del A: M-37 unntakskø — lagring (migrasjon 002)

Alt som stoppes skal kunne behandles strukturert. PR-005 leverer LAGRINGEN
og skrivingen; behandlingsmotoren (klassifisering, reparasjon, rollback)
er PR-006.

**Tabell `unntak`:**

| Kolonne | Type | Merknad |
|---|---|---|
| id | IDENTITY PK | |
| ts | TIMESTAMPTZ | opprettet |
| tenant | TEXT NOT NULL | RLS som øvrige tabeller, FORCE |
| loggpost_id | BIGINT NOT NULL REFERENCES revisjonslogg(id) | hvert unntak peker på beslutningen som skapte det — evidenskjeden er ubrutt |
| handling | TEXT NOT NULL | |
| kategori | TEXT NOT NULL | fra policyens unntak.kategorier |
| status | TEXT NOT NULL CHECK IN ('ny','under_behandling','løst','avvist') DEFAULT 'ny' |
| forsok | INT NOT NULL DEFAULT 0 | maks fra policyens maks_auto_forsok |
| payload | JSONB NOT NULL | hendelsen (persondata-minimert, se sikkerhetskrav 6) |
| status_ts | TIMESTAMPTZ | siste statusendring |

**Skriveregel:** `sikker_beslutning_pg` utvides: når beslutningen er
UNNTAK, settes unntaksraden inn i SAMME transaksjon som loggposten
(samme mønster som frekvensreservasjonen — enten begge eller ingen).
STOPP med effekt frys skal OGSÅ gi unntaksrad (kategori fra Grunn),
siden frys krever oppfølging. Ren STOPP (f.eks. uautentisert) gir ikke
unntaksrad — det er avvisning, ikke sak.

**Statusoverganger håndheves av DB-trigger:** ny→under_behandling→løst/avvist;
alt annet avvises. Ingen sletting (append + status, aldri DELETE).

## Del B: API-skjelett (`platform/core/api/`)

FastAPI, kun intern bruk i denne PR-en (staging bak brannmur; ingen
offentlig eksponering før M-29-gjennomgang).

**Endepunkter:**

1. `POST /v1/beslutning` — kjernen. Body: `{policy_id, event}`.
   Flyt: autentiser → last policy → `sikker_beslutning_pg(..., nokler=last_nokler())` → svar
   `{beslutning, policy_id, begrunnelse[koder], unntak_id?}`.
2. `GET /v1/unntak?status=ny` — liste for tenanten (paginert, maks 100).
3. `GET /helse` — liveness: DB-ping + migrasjonsversjon. Ingen auth, ingen detaljer utover ok/versjon.

**Autentisering (bindende):**
- Bearer-token per tenant. Tokenlager: tabell `api_tokener`
  (tenant, token_hash SHA-256, rolle, aktiv, opprettet) — tokens lagres
  ALDRI i klartekst. Sammenligning i konstant tid.
- `EvaluationContext` bygges UTELUKKENDE server-side fra tokenoppslaget
  (tenant, rolle, kilde='api_token', autentisert=True). Felter i request
  som påstår tenant/rolle ignoreres — payload kan aldri velge identitet.
- `nokler=last_nokler()` er OBLIGATORISK i API-veien: prosessen skal
  NEKTE OPPSTART hvis nøkkelregisteret mangler/er ugyldig (fail-closed
  ved boot, ikke ved første request).

**Sikkerhetskrav (bindende, hver med negativ test):**
1. Manglende/ukjent/inaktivt token → 401, ingen policy-lasting, ingen loggpost med tenant.
2. Body over 256 KB → 413 før parsing.
3. Ukjent policy_id for tenanten → 404 uten å avsløre om policyen finnes for andre tenanter.
4. Rate-grense per token (enkel: N req/min i minne per prosess nå; M-38 tar over senere) → 429.
5. Ingen CORS (intern API). Ingen stack traces i svar; feil → generisk 500 + loggpost.
6. Persondata-minimering i unntak.payload: feltene i event beholdes, men
   verdier i dataklasse persondata/sensitiv maskeres med SHA-256-referanse
   (kan slås opp mot kildesystem ved behandling — payload er arbeidsgrunnlag,
   ikke arkiv). Åpent reviewspørsmål til ChatGPT: er felt-nivå maskering
   riktig granularitet, eller bør hele payload krypteres med tenant-nøkkel?
7. API-prosessen kjører som runtime-rollen fra PR-004 (kan ikke røre egne vakter/RLS).

**Uttrykkelig UTENFOR scope:** offentlig eksponering, TLS-terminering
(staging: kun localhost/brannmur), M-37-behandlingslogikk, UI, andre
endepunkter enn de tre.

## Del C: m01 ytelsesport

Lasttest-skript `deploy/staging/lasttest-m01.py`: 100 beslutninger/s i
60 s, 20 samtidige tilkoblinger, mot POST /v1/beslutning på staging.
Bestått = p95 < 150 ms, 0 feil, 1:1 beslutning↔loggpost etterpå.
Resultatet skrives inn i m01-manifestet (`ytelse_bestatt: ja` + måltall).

## Testplan (minimum)

- Alle sikkerhetskrav 1–7 med negativ test
- UNNTAK-beslutning → unntaksrad + loggpost i samme transaksjon (og rollback-test: feiler unntaksskriv, committes heller ikke loggposten, svar = STOPP)
- Statusovergangs-trigger avviser ulovlige overganger og DELETE
- Boot uten nøkkelregister → prosess nekter start
- Token i klartekst finnes ingen steder (test: grep i DB-dump etter kjent testtoken)
- RLS: tenant A kan ikke lese tenant Bs unntak (gjenbruk mønsteret fra PR-004)

## Spørsmål ChatGPT bes svare på (utover de tre faste)

1. Er skillet «UNNTAK og frys → unntaksrad; ren STOPP → ikke» riktig, eller bør noen STOPP-koder (f.eks. verifikator_ikke_betrodd) også bli saker?
2. Sikkerhetskrav 6: felt-maskering vs. tenant-kryptert payload?
3. Er tokenmodellen (hash i DB, konstant-tids sammenligning, rolle per token) tilstrekkelig for intern staging-API, eller bør mTLS kreves allerede nå?
