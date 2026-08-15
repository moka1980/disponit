# PR-014b — IMPLEMENTERINGSKLARSIGNAL (GO, domenekontroll · egress · artefakt)

**Til Claude Code · Konsolidert spesifikasjon + v2. Deltaformen forlates.
Branch: `pr-014b-domene-egress-artefakt`. Andre av tre: 014a → 014b → 014c.
Forutsetninger: `m37_unntak`-aksept lukket · 014a (migrasjon 013) merget og
staging-verifisert.**

**Dette er plattforminfrastruktur alle senere eiermoduler arver. Ingen
WCAG-spesifikk logikk her.** Migrasjon **014** — additiv mot 013: ingen
kolonne eller constraint på 013-tabellene røres.

---

## 1. Samlet DDL (migrasjon 014) — autoritativ

```sql
-- DNS-kontroll per tenant. Bevis på sonekontroll på et tidspunkt, ikke eierskap.
CREATE TABLE domenekontroll (
  tenant TEXT NOT NULL,
  hostname TEXT NOT NULL,            -- IDNA2008 A-label, lowercase, uten avsluttende punktum
  status TEXT NOT NULL CHECK (status IN
    ('ventende','verifisert','avklaring_kreves','utlopt','tilbakekalt')),
  wildcard BOOLEAN NOT NULL DEFAULT false,
  autorisasjonsgenerasjon BIGINT NOT NULL DEFAULT 0,   -- monoton, §3 B1
  challenge_token_hash TEXT,         -- sha256; klartekst vises ÉN gang, lagres aldri
  challenge_utstedt TIMESTAMPTZ, challenge_utloper TIMESTAMPTZ,   -- 7 døgn
  verifisert_ts TIMESTAMPTZ,
  siste_vellykkede_revalidering TIMESTAMPTZ,
  utloper TIMESTAMPTZ,               -- verifisert_ts + 90 døgn
  PRIMARY KEY (tenant, hostname));
CREATE UNIQUE INDEX en_verifisert_per_hostname
  ON domenekontroll (hostname) WHERE status = 'verifisert';

CREATE TABLE domenekontroll_hendelse (...);   -- append-only, alle overganger + grunn

-- Global serialiseringsautoritet (§3 B2). INGEN RLS, INGEN runtime-SELECT.
CREATE TABLE hostname_binding (
  hostname TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  bundet_ts TIMESTAMPTZ NOT NULL DEFAULT now());

-- Eneste flate egress-proxyen ser (§3 B1). RLS gjelder via security_invoker.
CREATE VIEW v_domeneautorisasjon WITH (security_invoker = true) AS
  SELECT tenant, hostname, autorisasjonsgenerasjon,
         (status = 'verifisert' AND now() < utloper
          AND siste_vellykkede_revalidering > now() - interval '72 hours') AS gyldig
  FROM domenekontroll;

-- Artefakttyper, bundet til modulkontrakten i 013
CREATE TABLE artefakttype_register (
  artefakttype TEXT PRIMARY KEY,
  eiermodul TEXT NOT NULL,
  kontraktversjon INT NOT NULL, kontrakt_hash TEXT NOT NULL,
  skjema_hash TEXT NOT NULL,
  FOREIGN KEY (eiermodul, kontraktversjon, kontrakt_hash)
    REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash));

CREATE TABLE artefakt (
  artefakt_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant TEXT NOT NULL, oppdrag_id BIGINT NOT NULL,
  artefakttype TEXT NOT NULL REFERENCES artefakttype_register (artefakttype),
  modul_id TEXT NOT NULL, release_id TEXT NOT NULL,
  kontraktversjon INT NOT NULL, kontrakt_hash TEXT NOT NULL,
  module_epoch BIGINT NOT NULL,
  tilstand TEXT NOT NULL DEFAULT 'staged'
    CHECK (tilstand IN ('staged','promotert','forkastet','karantene','bevart')),
  storrelse_bytes INT NOT NULL
    CHECK (storrelse_bytes > 0 AND storrelse_bytes <= 1048576),   -- 1 MiB, v1
  klartekst_sha256 TEXT NOT NULL,    -- SERVERBEREGNET over JCS-kanonisert klartekst
  ciphertext BYTEA, nonce BYTEA, dek_ref TEXT NOT NULL,           -- ct+nonce nulles ved forkastet
  kapabilitet_jti TEXT NOT NULL UNIQUE,
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now(), promotert_ts TIMESTAMPTZ,
  -- En payload som FINNES må være strukturelt dekrypterbar: 12-byte nonce,
  -- ciphertext = ct || 16-byte GCM-tag. IS NOT NULL-leddene er ikke overflødige
  -- (`octet_length(NULL)` er NULL, og en CHECK som evaluerer til NULL passerer).
  CONSTRAINT artefakt_payload_struktur CHECK (
    (ciphertext IS NULL AND nonce IS NULL)
    OR (ciphertext IS NOT NULL AND nonce IS NOT NULL
        AND octet_length(ciphertext) > 16 AND octet_length(nonce) = 12)),
  FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, id),
  FOREIGN KEY (tenant, dek_ref)    REFERENCES tenant_nokler (tenant, key_id),
  FOREIGN KEY (modul_id, kontraktversjon, kontrakt_hash)
    REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash));
CREATE UNIQUE INDEX ett_promotert_per_oppdrag ON artefakt
  (oppdrag_id, artefakttype) WHERE tilstand = 'promotert';
CREATE INDEX artefakt_staged_opprydding ON artefakt (opprettet)
  WHERE tilstand = 'staged';
```
**Oppdragsidentiteten er `oppdrag.id`, ikke en UUID.** `oppdrag` har
`id BIGINT GENERATED ALWAYS AS IDENTITY` (005 linje 297) og ingen
`oppdrag_id`-kolonne; den referensielle identiteten er `(tenant, id)`
(`oppdrag_tenant_id_unik`). En `oppdrag_id UUID REFERENCES oppdrag
(oppdrag_id)` kunne verken opprettes, og en FK uten `tenant` ville
uansett ikke bevist at artefaktet og oppdraget tilhører samme tenant.
Tenantscopet FK er selve beviset — derfor står den, ikke en enkel
kolonnereferanse. Tilstandene `karantene` og `bevart` hører til
epoch-avviket (§7 pkt. 8): et artefakt som ikke kan promoteres skal
bevares, ikke forkastes, og opprydningen må kunne se forskjell.
RLS + FORCE per tenant på `domenekontroll` og `artefakt`.
`sett_kontekst` først på alle veier inn — også fra egress-proxyen.
Immutabilitet med triggere: `domenekontroll_hendelse` og
`artefakttype_register` tåler ingen UPDATE; `domenekontroll.status`,
`.autorisasjonsgenerasjon`, `artefakt.tilstand` og `hostname_binding`
endres **kun** via funksjonene i §2.

## 2. Herdede funksjoner (SECURITY DEFINER, NOLOGIN-eier, `search_path=pg_catalog`)

| Funksjon | Håndhever |
|---|---|
| `utsted_challenge()` | Token ≥128 bit, lagres kun hashet, 7 døgn. **Reutstedelse er gratis og ubegrenset** — kontrollen skal ikke straffe det riktige |
| **`verifiser_domenekontroll()`** | **Advisory-lås på hostname FØRST** · ≥2 uavhengige resolvere må være enige · PSL-port (pinnet) · atomisk takeover/avklaring (§3 B4) · `autorisasjonsgenerasjon++` · `hostname_binding` oppdatert i samme transaksjon |
| `revalider_domenekontroll()` | Daglig jobb. Setter `siste_vellykkede_revalidering`. **Endrer aldri status alene** — ferskheten i §1-visningen gjør jobben |
| `tilbakekall_domenekontroll()` | Umiddelbar, auditert grunn, `autorisasjonsgenerasjon++` |
| `avgjor_domeneovertakelse()` | Fra M-37-attestasjon: godkjent → `verifisert` m/ nytt 90-døgnsvindu; avvist → `tilbakekalt`. Ingen knapp skriver status direkte |
| `registrer_artefakttype()` | FK mot `modulkontrakt`; `skjema_hash` immutable |
| `lagre_artefakt_staged()` | Forbruker kapabiliteten ATOMISK under radlåsen · binding + størrelse + payloadstruktur · **serverberegnet JCS-hash** · tenant-DEK · `synchronous_commit=on` · idempotent på `(kapabilitet_jti, klartekst_sha256)`. Skjema håndheves **ikke** — se §7 |
| `promoter_artefakt()` | **I SAMME transaksjon som statusovergangen.** Verifiserer tilstand · tenant · oppdrag · release · epoch · hash |
| `rydd_staged_artefakter()` | Positiv regel: `staged` > 24 t **og uten refererende kvittering, inkludert karantenesatt**. Idempotent |

## 3. De fire bindende vilkårene

### B1. Domenekontroll håndheves per proxiet request
**Hver request — hver HTTP/2-strøm, også på gjenbrukt forbindelse —
starter med et ferskt autoritativt oppslag** mot `v_domeneautorisasjon`.
Ingen lease, ingen cache, ingen gammel proxy-attestasjon. Proxy-sesjonen
bærer `autorisasjonsgenerasjon`; avvik eller `gyldig = false` → request
blokkert, oppdrag → UNNTAK. **DB utilgjengelig → alle requests blokkeres.**

Lukket metode- og protokolliste: kun `GET` og `HEAD`. `CONNECT` godtas
**utelukkende** som inngang til TLS-terminering mot port 443 på autorisert
hostname — aldri som ugjennomsiktig tunnel. `Upgrade`/WebSocket, h2c,
`TRACE` og alt utenfor listen avvises lukket og telles som brudd.

**CA-privatnøkkelen finnes aldri i browser-imaget** (deploy-port);
containeren får kun det offentlige trust anchor-et. `Proxy-Authorization`
og proxy-tokenet er hop-by-hop, strippes, når aldri origin. Ingen
request-/responskropp logges.

**Rekkevidde, presist:** enhver request hvis autorisasjonskontroll starter
etter at tilbakekallingen er committet, blokkeres. Et svar under
overføring avbrytes best-effort og loves ikke som invariant.

### B2. Konkurrerende domenekontroll serialiseres globalt per hostname
`pg_advisory_xact_lock` på hostname **før** noen tenant-rad røres.
Funksjonen ser eksisterende binding gjennom `hostname_binding` — en egen,
ikke-RLS-tabell **uten SELECT for runtime**. Taper nummer to observerer
**committet tilstand**, ikke en unique violation: utfallet er
deterministisk, ikke en feilkode.

**Transaksjonen tar kun hostname-låsen og `domenekontroll`-rader (sortert
på `tenant`), og aldri en lås videre i 014a-kjeden. Den rører ingen
oppdragsrader** — pågående oppdrag stoppes gjennom generasjonen og B1
(lat deteksjon, som PR-013 §8).

### B3. Samme hostname gjelder også subressurser
**Hver proxiet request, toppnivå som subressurs, må gå til nøyaktig det
autoriserte hostnamet.** Ingen unntak i v1. «Subressurs» er ingen
autorisasjonskategori.

Blokkeringen er et **påkrevd felt**, ikke en stillhet — artefaktskjemaet
har `dekningsbegrensninger` (tom liste = ingen kjente begrensninger):
`{"type":"ekstern_ressurs_blokkert","antall":12,"verter":["cdn.example.com"],"maks_verter_vist":20}`.
Kun hostname lagres — aldri path, query eller fragment. **014c eier
semantikken** om hva dette betyr for dekningsgrad.

### B4. Overtakelse fjerner autorisasjon, men gir den ikke bort

| Tilstand hos A | A | B |
|---|---|---|
| `verifisert` OG `now() < utloper`, annen tenant | `tilbakekalt`, grunn `overtatt_dns_kontroll`, generasjon++ | Bevis bevart, **`avklaring_kreves`** — kan ikke opprette oppdrag |
| `utlopt`/`tilbakekalt`/ingen rad | — | `verifisert`, nytt 90-døgnsvindu |
| Samme tenant reverifiserer | — | `verifisert`, vindu fornyet, ingen avklaring |

Konflikten oppretter **én idempotent M-37-sak**, familie
`domeneovertakelse`, lineage til begge rader. Gjenbruk kun av sak som
positivt er ikke-terminal OG av samme familie. **Terminale saker endres
aldri.** Avgjørelsen tas i unntaksbehandlingen (PR-012).

## 4. Plattformregelen — tre håndhevingspunkter

Kunden kan aldri fjerne kontrollen, heller ikke med fire øyne.

| # | Hvor | Hva |
|---|---|---|
| a | Før oppdragsopprettelse | Autoritativt oppslag i `v_domeneautorisasjon`; `(hostname, autorisasjonsgenerasjon)` stemples på oppdraget |
| b | Ved claim, under oppdragslåsen | Radene PÅ NYTT — det stemplede generasjonstallet må fortsatt stemme |
| c | **Før hver proxiede request** | `v_domeneautorisasjon` + generasjon (B1) |

**Det finnes ingen `v_domene`-verifikator, og punkt (a) skal ikke vente på
en.** Et repo-søk gir ingen slik visning, funksjon, modul eller
attestasjonsskjema: det eneste som finnes er `v_domeneautorisasjon`, som
er egress-flaten i §1. `v_domene` kommer fra PR-014-utkastet, der
domeneeierskap skulle utstede en signert attestasjon gjennom
PR-007-verifikatorløypa (`valgt_verifikator`, `nokkel_id`, `signatur`).
Den løypa ble aldri koblet til domener, og 014b løste problemet på en
annen måte: **databasen er selv autoriteten**, og alle tre punktene leser
den samme raden. En attestasjon ville bare vært en kopi av raden med
kortere levetid — og det tredje punktet slår den likevel ihjel ved hver
request. Alle tre punktene krever `status='verifisert'` OG
`now() < utloper` OG `siste_vellykkede_revalidering > now() - 72 t`;
det er nettopp `gyldig`-uttrykket i visningen, ett sted, ikke tre.

**Åpen port, ikke løst av 016:** `oppdrag` har i dag ingen
`hostname`-/`autorisasjonsgenerasjon`-kolonne (005 §oppdrag, utvidet i
014), så stemplingen i (a) og gjenlesningen i (b) har ingen bærer.
016 leverte punkt (c) — visningen, generasjonen og RLS-en — og
domenekontrollen selv. Punktene (a) og (b) er derfor **spesifisert her,
men ikke implementert**, og kolonnene må komme i migrasjonen som
innfører crawloppdraget. Inntil da er egress-porten den eneste
håndhevede, og det skal ikke leses som at de to andre er dekket.

Revalidering kjører daglig: tre forsøk før en forbigående DNS-feil får
konsekvens. Policyen kan stille **strengere**
krav, aldri svakere. Wildcard kun bekreftet på apex, ett nivå, aldri
nestet; minst én etikett under public suffix (pinnet PSL — utdatert PSL
avviser, den åpner aldri).

## 5. Egress-proxy og crawlgrenser

- **Positiv adresseregel:** pinnet tabell over tillatte globalt-routbare
  unicast-områder; alt utenfor avvises. **CI verifiserer tabellen mot IANA
  Special-Purpose Registry** — ny spesialallokering blir rød test, ikke
  stille hull. Gjelder A og AAAA, IPv4-mapped, 6to4/Teredo/NAT64.
- **Blandet offentlig/privat DNS-svar → hele requesten avvises.**
- **IP pinnes til forbindelsen**; original hostname for SNI og
  sertifikatvalidering; **revalidering og repinning ved hvert redirect og
  hver nye forbindelse.**
- **Proxyen terminerer TLS** og validerer målets kjede mot pinnet trust
  store. Sertifikatfeil hos målet overføres som proxyfeil, aldri skjules.
- **Redirect:** maks 5 hopp, full ny kontroll per hopp; toppnivå-redirect
  til annet hostname → avbrutt med UNNTAK. Klartekst-HTTP som redirectmål
  avvises.
- **Per-oppdrag proxy-token** bundet til `tenant · oppdrag_id · modul_id ·
  release_id · module_epoch · autorisert hostname · autorisasjonsgenerasjon
  · utløp`. Ingen open relay.

| Grense | v1 |
|---|---|
| Innhold | Kun `text/html` parses og telles som side |
| Lenker | Fragment fjernes; lenke med query hoppes over |
| Maks sider · dybde | 50 · 3 |
| Maks requests · bytes · tid per oppdrag | 500 · 100 MiB · 10 min |
| Maks respons | 5 MiB |
| Hastighet | ≤ 1 request/sek |
| `robots.txt` | 4xx → tillatt; **5xx/uleselig → ingen crawl**; disallow respekteres |

**Takene telles i proxyen, ikke i browseren.** Treffes et tak, avsluttes
kjøringen normalt og rapporten bærer `avkortet: true` med tak og verdi.

**URL-kontrakt:** `https://<A-label hostname>[/<normalisert sti>]`, port
implisitt 443. Avvises ved inndata: credentials, query, fragment,
eksplisitt port, ikke-HTTPS, IP-litteral, avsluttende punktum,
ikke-normalisert prosentkoding, `..`, og hostname som endres av
IDNA2008-normalisering. **Én funksjon `urlkontrakt.normaliser()`, tre
kallsteder, håndhevet med statisk AST-test.**

## 6. Controller/browser-separasjon

| | Controller | Browser (egen container) |
|---|---|---|
| Credentials | Modultoken, kvitterings- og artefaktkapabilitet | **Ingen.** Kun per-oppdrag proxy-token |
| Nettverk | API over Unix-socket (PR-009) | **Kun** egress-proxyen, default-deny netpolicy |
| DB/nøkler/metadata | Ingen direkte DB-skriving | Ingen tilgang overhodet (negativ test) |

Non-root, **Chromium-sandbox aktiv (aldri `--no-sandbox` — oppstart
nektes)**, read-only rot, tom tmpfs, ingen host-mounts, alle capabilities
droppet, seccomp + AppArmor, CPU-/minne-/prosess-/tidsgrenser, **ny
browser-context per oppdrag**, null persistent cookie/cache/service worker,
downloads/popup/clipboard/filtilgang av.

**Alt som kommer ut av browseren er ubetrodd inndata.** Styringskanalen
eksponerer et lukket kommandosett; CDP behandles ikke som sikkerhetsgrense.
Skjemabrudd → oppdraget feiler, aldri delvis artefakt.
**Statisk AST-test:** `browser/` importerer aldri `api/`, `core/` eller DB-laget.

## 7. Artefaktprotokoll

**Egen kapabilitet.** `artifact_upload_capability`: eget audience, eget
scope (`artifacts:upload`), kort levetid, bundet til `tenant · oppdrag_id ·
modul_id · release_id · kontraktversjon · kontrakt_hash · module_epoch ·
artefakttype`. Kryssbruk mot kvitteringskapabiliteten avvises begge veier.

1. Controlleren laster opp lukket rapport til `POST /v1/artefakt`.
2. API-et validerer størrelse, JCS-kanoniserer, krypterer med tenant-DEK
   og lagrer `staged` varig. **Skjemavalideringen er en åpen port** — se
   under.
3. API returnerer `artefakt_id` + **serverberegnet hash**. Modulens egen
   hash-påstand finnes ikke i skjemaet.
4. Resultatkvitteringen binder begge.
5. Ingest verifiserer og **promoterer i samme transaksjon som
   statusovergangen**.
6. **Kvittering godtas aldri før artefaktet er varig lagret og verifisert.**
7. **Idempotens på `(kapabilitet_jti, klartekst_sha256)`** — samme
   kanoniske dokument gir samme `artefakt_id` uansett transportserialisering.
   Samme jti + annet kanonisk dokument → motstridende evidens, sikkerhetssak.
8. Epoch-avvik → ingen promotering, kvittering karantenesatt, **artefaktet
   bevares** (opprydding rører det aldri). Signatur-/tenant-/oppdrags-/
   release-/bindingsavvik → sikkerhetsrouting, som 014a §5.
9. Innsyn krever `artifacts:read`. Modulen ser aldri DEK, ciphertext, DB.

**Skjemavalidering: en hash er ikke et skjema.** `artefakttype_register`
lagrer kun `skjema_hash TEXT NOT NULL` (016 linje 93), og det finnes
ingen skjemakilde noe sted i repoet — verken tabell, fil eller register
som `POST /v1/artefakt` kunne slått opp. Endepunktet parser derfor,
JCS-kanoniserer, måler størrelse, hasher og krypterer et **vilkårlig**
JSON-objekt. En hash alene kan ikke validere noe: den kan bekrefte at et
skjema man allerede har er det registrerte, aldri produsere skjemaet.
Steg 2 kan altså ikke i dag håndheve «lukket rapport», og en åpen eller
feilformet rapport blir varig evidens.

Porten lukkes med en **serverid skjemakilde**, ikke med en påstand fra
modulen:
```sql
CREATE TABLE artefaktskjema (
  skjema_hash TEXT PRIMARY KEY,     -- sha256 over JCS-kanonisert `skjema`
  skjema      JSONB NOT NULL,
  registrert  TIMESTAMPTZ NOT NULL DEFAULT now());
-- Append-only + trigger som avviser INSERT der hashen ikke er sha256 over
-- de kanoniserte bytene. Innholdet er da bundet til navnet sitt.
ALTER TABLE artefakttype_register
  ADD CONSTRAINT artefakttype_skjema_fk
  FOREIGN KEY (skjema_hash) REFERENCES artefaktskjema (skjema_hash);
```
`registrer_artefakttype()` kan da ikke registrere en type mot et skjema
som ikke finnes, og steg 2 validerer rapporten mot bytene i registeret
— verifisert mot hashen før bruk — i stedet for mot hashen alene.

**Status: ikke levert i 016.** Tabellen, triggeren og FK-en må komme i en
senere migrasjon (016 er immutable, checksum-låst), og
`skjema_hash`-kolonnen er i mellomtiden et navn uten oppslagsvei. Det
skal stå her, ikke leses inn i steg 2 som om det var håndhevet.

## 8. GRANT-modell (default-deny)

| Rolle | Rettighet |
|---|---|
| `disponit_egress` | **SELECT kun på `v_domeneautorisasjon`.** Ingen andre tabeller, ingen skriverett |
| `disponit_runtime` | SELECT på `domenekontroll`, `artefakt`, `artefakttype_register`. **INGEN** INSERT/UPDATE/DELETE. **INGEN SELECT på `hostname_binding`** |
| Modulroller | Ingen DB-tilgang. Kun `POST /v1/artefakt` med egen kapabilitet |
| `disponit_domains_admin` | EXECUTE på funksjonene i §2 — ikke direkte DML |

**Låserekkefølge, tillegg til 014a §5:** domeneverifisering/-overtakelse
tar hostname-lås → `domenekontroll` (sortert `tenant`) og **stopper der**.
`artefakt` låses alltid sist, etter oppdragsraden. Ingen ny syklus mot
oppdrag/release/artefakt.

## 9. Codex-porter

**Domenekontroll (1–14).** 1 Uverifisert hostname → ingen oppdragsopprettelse ·
2 Attestasjon gyldig, kontroll tilbakekalt før claim → claim nektes ·
3 Tilbakekalling midt i kjøring → neste request nektes, UNNTAK ·
4 Revalidering > 72 t → ingen ny attestasjon · 5 Uenige resolvere → ikke
verifisert · 6 Wildcard ett nivå; nestet avvist; PSL-apex avvist ·
7 Challenge-token ikke i klartekst i DB-dump; reutstedelse virker ·
8 To samtidige challenges for samme hostname → ett deterministisk utfall,
taperen ser committet tilstand (aldri unique violation) · 9 Overtakelse
rører null oppdragsrader · 10 Cross-tenant innen aktivt vindu → A
`tilbakekalt`, B `avklaring_kreves`, B kan ikke opprette oppdrag ·
11 M-37-sak idempotent, familie `domeneovertakelse`, terminal sak aldri
gjenbrukt · 12 Avklaring godkjent → B `verifisert` m/ nytt vindu; avvist →
`tilbakekalt` · 13 A `utlopt`/`tilbakekalt` → B `verifisert` direkte ·
14 Samme tenant reverifiserer → ingen avklaring.

**Egress (15–26).** 15 Tilbakekalling committet → hver etterfølgende
request blokkert, også på gjenbrukt HTTP/2-forbindelse; **null sluppet
gjennom** · 16 DB utilgjengelig → alle requests blokkert, oppdrag → UNNTAK ·
17 `disponit_egress` har SELECT kun på visningen · 18 `CONNECT` som
ugjennomsiktig tunnel · `Upgrade`/WebSocket · h2c · metode utenfor
{GET, HEAD} → lukket avvisning · 19 Kontrollert domene som resolver til
privat IP → avvist · 20 Blandet offentlig/privat DNS-svar → hele requesten
avvist · 21 DNS-rebinding mellom verifisering og forbindelse → ingen
forbindelse · 22 Toppnivå-redirect til ukontrollert hostname → avbrutt,
UNNTAK; redirect til http:// → avvist · 23 **Subressurs mot fremmed
hostname → blokkert** · 24 Ny IANA-spesialallokering ikke i pinnet tabell
→ CI rød · 25 Tak truffet → `avkortet: true` med tak og verdi ·
26 `robots.txt` 5xx → ingen crawl; disallow respektert i målets logg.

**Sandkasse (27–31).** 27 `--no-sandbox` → oppstart nektes ·
28 Browser-container: ingen DB, nøkler, metadata eller nettvei utenom
proxy · 29 CA-privatnøkkel finnes ikke i browser-image (deploy-port) ·
30 `Proxy-Authorization`/proxy-token når aldri origin · 31 Browserdata som
bryter lukket skjema → forkastet, ingen artefakt.

**URL (32).** 32 Credentials/query/fragment/port/ikke-HTTPS avvist ved
inndata; AST-test: én normalisator, tre kallsteder.

**Artefakt (33–40).** 33 Artefakt > 1 MiB → avvist · 34
Kvitteringskapabilitet brukt til opplasting → avvist, og motsatt ·
35 Kvittering før artefakt varig lagret → avvist · 36 Samme jti + ulik
serialisering av samme kanoniske dokument → samme `artefakt_id`; annet
kanonisk dokument → sikkerhetssak · 37 Epoch-avvik → ingen promotering,
kvittering karantenesatt, artefakt bevart av opprydding · 38 To samtidige
kvitteringer → nøyaktig én promotering · 39 `dekningsbegrensninger`
mangler → avvist av lukket skjema; blokkert vert framgår med antall og
vert, uten path/query · 40 `staged` > 24 t uten refererende kvittering →
`forkastet`, ciphertext nullet, idempotent.

**Rettigheter og samtidighet (41–42).** 41 Runtime/modulroller kan ikke
skrive `domenekontroll`, `artefakt`, `artefakttype_register`, og har ingen
SELECT på `hostname_binding` · 42 Deadlock-test: samtidig overtakelse,
tilbakekalling, kvitteringsingest og releasepromotering.

**Alle tester konstruerer egen tilstand** — egen tenant, eget hostname,
eget oppdrag, opprettet gjennom de offentlige funksjonene. Ingen delt
fixture.

## 10. Evidensgrense `domene-egress-artefakt-v1`
Defineres i KRAVGRENSER FØR arbeidet. Artefakter og `bevismaalinger`-stier:

- `egress-014b-v1`: `ssrf.forsok ≥ 40` · `ssrf.sluppet_gjennom = 0` ·
  `metode.utenfor_liste_avvist = alle` · `connect.ugjennomsiktig_avvist = alle` ·
  `autorisasjonskontroll.p95_ms < 1`
- `domene-014b-v1`: **`tilbakekalling.sluppet_gjennom_etter_commit = 0`
  (sikkerhetsinvariant)** · `tilbakekalling.stoppet_ms.p95 < 5000`
  (kun ytelsesmåling) · `overtakelse.deterministisk_utfall = 1` ·
  `overtakelse.oppdragsrader_rort = 0` ·
  `challenge.uenige_resolvere_avvist = alle`
- `sandkasse-014b-v1`: `negativ.db = nektet` · `negativ.metadata = nektet` ·
  `negativ.utenfor_proxy = nektet` · `nosandbox_oppstart = nektet` ·
  `ca_privatnokkel_i_image = nei` · `proxy_authorization_mot_origin = 0`
- `artefakt-014b-v1`: `opplasting.avvist_over_1mib = alle` ·
  `promotering.samtidige_2_gir_1 = ja` ·
  `opprydding.karantene_bevart = alle` ·
  `dekningsbegrensninger.felt_pakrevd = ja`

Et sjekklistepunkt uten målbar grense regnes som `nei`.

---

```
NÅ:    Implementer PR-014b mot dette klarsignalet — migrasjon 014, egress-proxy,
       browser-container, artefakt-API — Claude Code
       — platform/core/migrasjoner/014_domene_egress_artefakt.sql,
         platform/egress/, platform/browser/, api/artefakt.py
NESTE: Draft PR-014c (automatisk WCAG-kontroll: modulmanifest, rapportskjema
       med dekningsbegrensninger og avkorting, evidensgrense wcag-audit-v1)
       — Claude.ai — docs/PR-014c-WCAG-KONTROLL-SPESIFIKASJON.md
```
