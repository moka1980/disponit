# PR-014b SPESIFIKASJON — Domenekontroll, controller/browser-separasjon, egress-proxy og artefaktprotokoll

**Draft: Claude.ai · Andre av tre: 014a → 014b (dette) → 014c.
Plattforminfrastruktur ALLE senere eiermoduler arver — ingen
WCAG-spesifikk logikk her. Full sti: `docs/PR-014b-DOMENE-EGRESS-ARTEFAKT-SPESIFIKASJON.md`.**

**Forutsetninger:** `m37_unntak` modulaksept lukket · 014a (migrasjon 013)
merget og staging-verifisert.

---

## 0. Avgrensning og forholdet til 013

014b leverer fire ting 014c bare *bruker*: en verifikator for
DNS-kontroll, en tillitsgrense rundt fremmed kode, en kontrollert
nettverksvei ut, og en vei for evidens inn.

**014b er additivt mot 013.** Ingen kolonne legges til og ingen constraint
endres på 014a-tabellene, fordi Claude Code bygger dem nå. Alt nytt ligger
i **migrasjon 014** med egne tabeller og FK-er *inn* mot 013.
*(Rettelse: både PR-014 v2 §2 og 014a-spesifikasjonen skrev «migrasjon
013» om domenetabellene. 013 er nå modulregisteret alene.)*

**Ærlig navngivning — vilkåret og tabellen skifter navn.** DNS-TXT beviser
at noen kontrollerte sonen på verifiseringstidspunktet. Det er ikke
eierskap, og det er ikke *nå*. Derfor: tabellen heter `domenekontroll`,
policyvilkåret heter **`domenekontroll_verifisert`** (erstatter
`domene_eid_av_kunde` fra PR-014 B4), og attestasjonsteksten er «bekreftet
DNS-kontroll, sist revalidert `<ts>`». Ingen aktiv policy refererer det
gamle navnet ennå — omdøpingen er gratis nå og umulig senere.

---

## 1. Plattformregel, med tre håndhevingspunkter

Kunden kan aldri fjerne kontrollen, heller ikke med fire øyne (PR-014 v2
§1). Regelen håndheves **tre** steder, ikke to — det tredje er nytt:

| # | Hvor | Hva som kontrolleres |
|---|---|---|
| a | Før oppdragsopprettelse (beslutningsveien) | Gyldig, fersk attestasjon fra `v_domene` |
| b | Ved claim, under oppdragslåsen | Radene i `domenekontroll` PÅ NYTT — ikke attestasjonen |
| c | **Før HVER toppnivånavigasjon** | Radene på nytt, i egress-proxyen |

(b) og (c) leser databasen, ikke attestasjonen: en attestasjon er et
øyeblikksbilde, og tilbakekalling skal virke midt i en kjøring.
Policyen kan stille **strengere** krav (hvilke av tenantens hostnames,
hvilket omfang, hvilke tider) — aldri svakere.

**Ferskhet (samme kontraktdisiplin som PR-010):** attestasjon utstedes kun
når `status='verifisert'` OG `now() < utloper` OG
`siste_vellykkede_revalidering > now() - 72 timer`. Revalideringsjobben
kjører **daglig**, så en forbigående DNS-feil koster tre forsøk, ikke
kundens onboarding. Tre døgn uten svar stopper nye oppdrag uten å rive
raden.

## 2. `domenekontroll` — verifikatorlivsløp (migrasjon 014)

```sql
CREATE TABLE domenekontroll (
  tenant TEXT NOT NULL,
  hostname TEXT NOT NULL,          -- IDNA2008 A-label, lowercase, uten avsluttende punktum
  status TEXT NOT NULL CHECK (status IN
    ('ventende','verifisert','utlopt','tilbakekalt')),
  wildcard BOOLEAN NOT NULL DEFAULT false,
  challenge_token_hash TEXT,       -- sha256 av tokenet; klartekst vises ÉN gang og lagres aldri
  challenge_utstedt TIMESTAMPTZ,
  challenge_utloper TIMESTAMPTZ,   -- 7 døgn
  verifisert_ts TIMESTAMPTZ,
  siste_vellykkede_revalidering TIMESTAMPTZ,
  utloper TIMESTAMPTZ,             -- verifisert_ts + 90 døgn
  PRIMARY KEY (tenant, hostname));

CREATE UNIQUE INDEX en_verifisert_per_hostname
  ON domenekontroll (hostname) WHERE status = 'verifisert';

CREATE TABLE domenekontroll_hendelse (...);   -- append-only, alle overganger + grunn
```
RLS + FORCE per tenant som ellers. `sett_kontekst` først på alle veier inn.

- **DNS-TXT-challenge:** tilfeldig token ≥128 bit på
  `_disponit-verifisering.<hostname>`. **Ingen portalregistrering** som
  alternativ vei. Tokenet lagres kun hashet — en DB-dump gir ingen aktiv
  challenge. Mistet token løses med **gratis reutstedelse** (ingen ny
  saksbehandling, ingen kostnad — kontrollen skal ikke straffe det
  riktige).
- **Flere resolvere:** oppslaget gjøres mot minst to uavhengige,
  konfigurerte resolvere. **Uenighet → ikke verifisert** (fail-closed mot
  split-horizon og spoofing).
- **Wildcard** kun når challenge er bekreftet på apex; dekker **ett** nivå
  subdomener, aldri nestet. Hostname må ha minst én etikett under
  public suffix — sjekket mot **pinnet PSL-versjon**. Utdatert PSL avviser
  nye TLD-er (fail-closed), den åpner aldri noe.
- **Tilbakekalling** er umiddelbar, gjelder pågående oppdrag (§1b/c), og
  er den eneste terminale statusen.
- **Konkurrerende kontroll:** består tenant B challenge for et hostname
  tenant A har `verifisert`, settes A til `tilbakekalt`
  (`grunn: overtatt_dns_kontroll`) i SAMME transaksjon, auditert, og A-s
  pågående oppdrag stoppes. DNS-kontroll er autoriteten; nyeste bevis
  vinner. (Se spørsmål 3.)
- `v_domene` registreres som verifikator med egen nøkkel. Attestasjonen
  binder `(tenant, hostname, wildcard, utloper,
  siste_vellykkede_revalidering, jti)`.

## 3. Controller/browser-separasjon — browseren har ingenting å stjele

To prosesser, to tillitsnivåer. **Alt som kommer ut av browseren er
ubetrodd inndata.**

| | Controller (`disponit-<modul>-controller`) | Browser (egen container) |
|---|---|---|
| Credentials | Modultoken, kvitteringskapabilitet, artefaktkapabilitet | **Ingen.** Kun et per-oppdrag proxy-token |
| Nettverk | API over Unix-socket (PR-009-tillitsgrensen) | **Kun** egress-proxyen. Default-deny netpolicy |
| DB / nøkler / metadata-endepunkt | Ingen direkte DB-skriving (PR-006-kontrakten) | Ingen tilgang i det hele tatt (negativ test) |
| Rolle | Claimer, styrer, validerer, laster opp | Renderer fremmed kode og returnerer data |

- Browseren kjøres non-root med **Chromium-sandbox aktiv (aldri
  `--no-sandbox` — oppstart nektes)**, read-only rot, tom tmpfs, ingen
  host-mounts, alle capabilities droppet, seccomp + AppArmor, egne CPU-,
  minne-, prosess- og tidsgrenser. **Ny browser-context per oppdrag**,
  null persistent cookie/cache/service worker. Downloads, popup, clipboard
  og filtilgang deaktivert.
- Styringskanalen eksponerer et **lukket sett kommandoer**. CDP er ikke en
  sikkerhetsgrense, og behandles ikke som en: alt som returneres
  skjemavalideres og lengdebegrenses av controlleren før det kan bli
  artefakt. Brudd på skjemaet → oppdraget feiler, aldri delvis artefakt.
- **Statisk AST-test:** `browser/` importerer aldri `api/`, `core/` eller
  DB-laget (samme mønster som invariant 5).

## 4. Egress-proxy — den eneste veien ut

Et kontrollert domene kan peke til localhost eller skifte DNS-svar etter
verifisering. All browsertrafikk går gjennom proxyen (PR-010 v6 §1).

- **Positiv adresseregel:** en **pinnet tabell over tillatte
  globalt-routbare unicast-områder**; alt utenfor avvises. Tabellen
  verifiseres i CI mot IANA Special-Purpose Registry, slik at en **ny
  spesialallokering blir en rød test, ikke et stille hull**. Gjelder A og
  AAAA, IPv4-mapped, 6to4/Teredo/NAT64.
- **Blandet offentlig/privat DNS-svar → hele requesten avvises.**
- **IP pinnes til forbindelsen.** Original hostname brukes for SNI og
  sertifikatvalidering. **Revalidering og repinning ved hvert redirect og
  hver nye forbindelse**, også for subressurser.
- **Kun normalisert HTTPS, kun port 443.** `file:`, `data:`, `blob:`,
  FTP og klartekst-HTTP er forbudt — også som redirectmål.
- **Redirect:** maks 5 hopp, hvert hopp full ny kontroll. Toppnivå-redirect
  til annet hostname krever **eget, gyldig `domenekontroll`-oppslag** —
  ellers avbrytes oppdraget med UNNTAK.
- **Proxyen terminerer TLS** mot målet og validerer målets kjede mot en
  pinnet trust store; browseren stoler kun på en intern CA som finnes
  inne i browser-containeren. Uten terminering kan ikke redirect- og
  per-request-reglene håndheves ved nettverksgrensen — de ville blitt
  avhengige av den ubetrodde browseren. **Konsekvensen sies rett ut:
  proxyen ser klartekst. Ingen responskropp logges eller lagres.**
  Sertifikatfeil hos målet MÅ overføres som proxyfeil, aldri skjules.
- **Proxyen er ingen open relay:** hver forbindelse krever et per-oppdrag
  token bundet til `tenant · oppdrag_id · modul_id · release_id ·
  module_epoch · tillatte hostnames · utløp`. Epoch-avvik → ingen
  forbindelse.
- **Takene telles i proxyen**, ikke i browseren (§5).

## 5. Crawlgrenser — positiv liste, eksakte tak, synlig avkorting

| Regel | v1-verdi |
|---|---|
| Toppnivånavigasjon | **Kun nøyaktig samme hostname** som frøet. Ingen www↔apex-ekvivalens |
| Metode | Kun GET og HEAD |
| Innhold | Kun `text/html` parses og telles som side; annet forkastes |
| Lenker | Fragment fjernes; lenke med query **hoppes over** |
| Maks sider · dybde | 50 · 3 |
| Maks HTTP-requests · bytes · tid per oppdrag | 500 · 100 MiB · 10 min |
| Maks respons | 5 MiB |
| Hastighet | ≤ 1 request/sek mot samme hostname |
| `robots.txt` | Hentes via proxyen. 4xx → tillatt, **5xx/uleselig → ingen crawl** (fail-closed), disallow respekteres |

**Subressurser er unntatt hostname-regelen, med vilje.** CSS og webfonts
ligger ofte på CDN, og en kontrastkontroll uten stilark gir *feil svar*,
ikke manglende svar. Subressurser tillates derfor mot ethvert globalt
routbart HTTPS-endepunkt under samme tak — men de **telles aldri som
sider og crawles aldri**. Dette er trygt nøyaktig fordi browseren ikke
har noen credentials å lekke (§3). (Se spørsmål 2.)

**Avkorting er et felt, ikke en stillhet.** Treffes et tak, avsluttes
kjøringen normalt og rapporten bærer `avkortet: true` med hvilket tak og
hvilken verdi. Systemet påstår aldri en fullstendighet databasen ikke kan
bevise.

## 6. URL-inndata — én normalisator, tre kall

```
Tillatt form: https://<A-label hostname>[/<normalisert sti>]   (port implisitt 443)
```
Avvises ved inndata: credentials (`bruker:pass@`), query, fragment,
eksplisitt port, ikke-HTTPS-skjema, IP-litteral, avsluttende punktum,
ikke-normalisert prosentkoding eller `..`, og hostname som endres av
IDNA2008-normalisering (inndata må allerede være normalisert).

Samme funksjon `urlkontrakt.normaliser()` brukes ved (a) policy-/
inndatavalidering, (b) oppdragsopprettelse og (c) i proxyen — håndhevet
med **statisk AST-test** på at ingen av de tre veiene har egen parsing.

## 7. Artefaktprotokoll — modulen rører aldri DB eller DEK

**Egen kapabilitet.** `artifact_upload_capability` er separat fra
kvitteringskapabiliteten: eget audience, eget scope (`artifacts:upload`),
kort levetid, og bundet til `tenant · oppdrag_id · modul_id · release_id ·
kontraktversjon · kontrakt_hash · module_epoch · artefakttype`.
Kryssbruk i begge retninger avvises.

```sql
CREATE TABLE artefakttype_register (
  artefakttype TEXT PRIMARY KEY,
  eiermodul TEXT NOT NULL,
  kontraktversjon INT NOT NULL, kontrakt_hash TEXT NOT NULL,
  skjema_hash TEXT NOT NULL,
  FOREIGN KEY (eiermodul, kontraktversjon, kontrakt_hash)
    REFERENCES modulkontrakt (modul_id, kontraktversjon, kontrakt_hash));

CREATE TABLE artefakt (
  artefakt_id UUID PRIMARY KEY,
  tenant TEXT NOT NULL, oppdrag_id UUID NOT NULL,
  artefakttype TEXT NOT NULL REFERENCES artefakttype_register (artefakttype),
  modul_id TEXT NOT NULL, release_id TEXT NOT NULL,
  kontraktversjon INT NOT NULL, kontrakt_hash TEXT NOT NULL,
  module_epoch BIGINT NOT NULL,
  tilstand TEXT NOT NULL CHECK (tilstand IN ('staged','promotert','forkastet')),
  storrelse_bytes INT NOT NULL CHECK (storrelse_bytes > 0
                                  AND storrelse_bytes <= 1048576),  -- 1 MiB, v1
  klartekst_sha256 TEXT NOT NULL,        -- SERVERBEREGNET over JCS-kanonisert klartekst
  ciphertext BYTEA, dek_ref TEXT NOT NULL,
  kapabilitet_jti TEXT NOT NULL UNIQUE,
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now(), promotert_ts TIMESTAMPTZ);

CREATE UNIQUE INDEX ett_promotert_per_oppdrag ON artefakt
  (oppdrag_id, artefakttype) WHERE tilstand = 'promotert';
```

1. Controlleren laster opp **lukket rapport** til `POST /v1/artefakt`.
2. API-et validerer størrelse + skjema mot `skjema_hash`, **krypterer med
   tenant-DEK**, lagrer `staged` med `synchronous_commit=on`.
3. API returnerer `artefakt_id` + **serverberegnet hash**. Modulens egen
   hash-påstand finnes ikke i skjemaet.
4. Resultatkvitteringen binder begge.
5. Kvitteringsingest verifiserer tilstand, tenant, oppdrag, release,
   epoch og hash, og **promoterer artefaktet i SAMME transaksjon som
   statusovergangen**.
6. **Kvittering godtas aldri før artefaktet er varig lagret og verifisert.**
7. **Idempotens:** samme `kapabilitet_jti` + samme bytes → samme
   `artefakt_id`. Samme jti + ANDRE bytes → avvist som motstridende
   evidens, sikkerhetssak (som PR-006-kvitteringen).
8. **Opprydding er en positiv regel:** `staged` eldre enn 24 t **og uten
   refererende kvittering, inkludert karantenesatt kvittering** →
   `forkastet`, ciphertext nullet (crypto-shredding), idempotent timer.
   Epoch-avvik ved promotering gir karantene — og karantenesatt evidens
   ryddes ALDRI bort (014a §5).
9. DB-lagring maks 1 MiB i v1, håndhevet med CHECK. Objektlager er en
   senere, bevisst endring — ikke en drift.
10. Innsyn krever `artifacts:read`. Modulen ser aldri DEK, aldri
    ciphertext, aldri DB.

## 8. GRANT og låserekkefølge

- `disponit_runtime` og modulrollene har **INGEN** INSERT/UPDATE/DELETE på
  `domenekontroll`, `artefakt` eller `artefakttype_register` — kun EXECUTE
  på de herdede funksjonene (SECURITY DEFINER, NOLOGIN-eier,
  `search_path=pg_catalog`). Negativ GRANT-test per tabell.
- **Låserekkefølge, som tillegg til 014a §5:** `domenekontroll` låses
  alltid FØRST og holder aldri lås mens en annen tas; `artefakt` låses
  alltid SIST, etter oppdragsraden. Deadlock-testen utvides med samtidig
  tilbakekalling, kvitteringsingest og releasepromotering.

## 9. De fire portspørsmålene

| Kontroll | Alle veier inn? | Samtidighet? | Riktig vs. velformet? | Lukket format? |
|---|---|---|---|---|
| Domenekontroll | Beslutning · claim · hver toppnavigasjon | Tilbakekalling under kjøring fanges ved neste navigasjon; claim revaliderer under oppdragslås | Krever bekreftet DNS-kontroll + ferskhet < 72 t, ikke bare en rad | CHECK-enum + attestasjon med bundet jti |
| Egress | Eneste nettverksvei; browseren har ingen annen | Repinning ved hvert redirect og hver forbindelse | Adressen må være i den pinnede tillatte tabellen | Pinnet tabell, CI-verifisert mot IANA |
| URL-form | Tre kallsteder, én normalisator (AST-test) | — | Normalisert form, ikke «parses uten feil» | Positiv grammatikk, alt annet avvises |
| Artefakt | Kun `POST /v1/artefakt` med egen kapabilitet | Én promotert per oppdrag (delindeks); jti unik | Serverberegnet hash + skjema_hash, ikke modulens påstand | `additionalProperties: false`, versjonert |

## 10. Evidensgrense `domene-egress-artefakt-v1` (defineres FØR arbeidet)

Artefakter i KRAVGRENSER, med `bevismaalinger`-stier per punkt:
`egress-014b-v1` (`ssrf.forsok`, `ssrf.blokkert`, `ssrf.sluppet_gjennom = 0`
over ≥ 40 vektorer: localhost, link-local, CGNAT, metadata, rebinding,
blandet svar, redirectkjede, ikke-443, klartekst-HTTP) ·
`domene-014b-v1` (`challenge.uenige_resolvere_avvist`,
`ferskhet.attestasjoner_nektet_over_72t`, `tilbakekalling.stoppet_ms` p95
< 5000 fra tilbakekalling til nektet navigasjon) ·
`artefakt-014b-v1` (`opplasting.avvist_over_1mib`,
`promotering.samtidige = 2 → promotert = 1`,
`opprydding.karantene_bevart = alle`) ·
`sandkasse-014b-v1` (`negativ.db = nektet`, `negativ.metadata = nektet`,
`negativ.utenfor_proxy = nektet`, `nosandbox_oppstart = nektet`).
Et punkt uten målbar grense regnes som `nei`.

## 11. Codex-porter

1. Uverifisert hostname → ingen oppdragsopprettelse
2. Attestasjon gyldig, kontroll tilbakekalt før claim → claim nektes
3. Tilbakekalling midt i kjøring → neste toppnavigasjon nektes, UNNTAK
4. Siste vellykkede revalidering > 72 t → ingen ny attestasjon
5. Uenige resolvere → ikke verifisert
6. Wildcard dekker ett nivå; nestet avvist; PSL-apex avvist
7. Challenge-token finnes ikke i klartekst i DB-dump
8. Konkurrerende DNS-kontroll → gammel `tilbakekalt`, auditert, oppdrag stoppet
9. URL med credentials/query/fragment/port/ikke-HTTPS → avvist ved inndata; AST-test: én normalisator
10. Kontrollert domene som resolver til privat IP → avvist av egress
11. Blandet offentlig/privat DNS-svar → hele requesten avvist
12. DNS-rebinding mellom verifisering og forbindelse → ingen forbindelse
13. Toppnivå-redirect til ukontrollert hostname → avbrutt, UNNTAK; redirect til http:// → avvist
14. Subressurs mot fremmed globalt routbart HTTPS-vert tillatt, men aldri crawlet eller toppnavigert; mot privat IP blokkert
15. Ny IANA-spesialallokering ikke i pinnet tabell → CI rød
16. `--no-sandbox` → oppstart nektes
17. Browser-container: ingen DB, ingen nøkler, ingen metadata, ingen nettvei utenom proxy (negative tester)
18. Browserdata som bryter lukket skjema → forkastet, ingen artefakt
19. Tak truffet → kjøring stoppet, `avkortet: true` med tak og verdi
20. `robots.txt` 5xx → ingen crawl; disallow respektert i målets logg
21. Artefakt > 1 MiB → avvist; kvitteringskapabilitet brukt til opplasting → avvist (og motsatt)
22. Kvittering før artefakt varig lagret → avvist
23. Samme jti + samme bytes → idempotent; samme jti + andre bytes → sikkerhetssak
24. Epoch-avvik ved promotering → ingen promotering, kvittering karantenesatt, artefakt bevart av opprydding
25. To samtidige kvitteringer → nøyaktig én promotering
26. Runtime/modulroller kan ikke skrive `domenekontroll`, `artefakt`, `artefakttype_register`
27. Deadlock-test: samtidig tilbakekalling, kvitteringsingest og releasepromotering

**Alle tester konstruerer egen tilstand** — egen tenant, eget hostname,
eget oppdrag, opprettet gjennom de offentlige funksjonene. Ingen delt
fixture (tre tester har råtnet av det før).

---

## Spørsmål til ChatGPT

1. **TLS-terminering i egress-proxyen.** Jeg har valgt at proxyen
   terminerer TLS og validerer målets sertifikatkjede selv, fordi
   redirect- og per-request-reglene ellers må håndheves inne i den
   ubetrodde browseren. Prisen er at proxyen ser klartekst og at browseren
   kun validerer vår interne CA. Er det riktig avveining, eller bør v1
   heller bruke CONNECT-tunnel og akseptere at redirectkontrollen blir
   svakere?
2. **Subressurser mot fremmede hostnames.** Jeg tillater dem (uten
   crawling, under samme tak), fordi en kontrastkontroll uten eksternt
   stilark gir *gale* funn, ikke færre. Alternativet er streng
   samme-vert-regel med rapporten eksplisitt merket «kjørt uten eksterne
   stilark og fonter». Hvilken av de to er mest ærlig i v1?
3. **Konkurrerende DNS-kontroll.** Jeg lar nyeste bevis vinne: tenant B
   som består challenge tilbakekaller tenant A automatisk. Det er
   teknisk korrekt, men det er også en vei til å stoppe en kundes
   pågående kontroller. Bør overtakelse i stedet gå til
   `avklaring_kreves` med menneskelig behandling, når A fortsatt er
   innenfor sitt 90-døgnsvindu?

---

```
NÅ:    PR-014b-spesifikasjonen gjennom spesifikasjonsporten (de tre faste
       spørsmålene + de tre over); svaret limes inn i PR-beskrivelsen
       — ChatGPT (Eier relayer) — docs/PR-014b-DOMENE-EGRESS-ARTEFAKT-SPESIFIKASJON.md
NESTE: Parallelt: 014a-implementering på branch pr-014a-modulregister og
       lukking av m37_unntak-aksepten (rollback-m37-driver + staging-måling)
       — Claude Code — platform/core/ (migrasjon 013) og
       platform/modules/m37_unntak/manifest.yaml
```
