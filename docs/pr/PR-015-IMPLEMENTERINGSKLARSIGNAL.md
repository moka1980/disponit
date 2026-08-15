# PR-015 — IMPLEMENTERINGSKLARSIGNAL (GO, operativt lag)

**Til Claude Code · Konsolidert spesifikasjon + v2 + v3. Deltaformen
forlates. Branch: `pr-015-operativt-lag`.
Forutsetning: PR-014b merget (migrasjon 016 + 017 på main).**

**Hva dette er:** 014b definerte funksjonene. PR-015 er kallerne — pluss
migrasjon **019**, som fire-øyne-kontrakten, resolverkontrakten,
fencingen og batchgrensen krever. Ingen WCAG-logikk.

**Migrasjonsnummeret er 019, ikke 018.** `018_kanonisk_hostname.sql`
ligger allerede på main (PR-014b-oppfølgingen), og kjøreren nøkler
anvendte migrasjoner utelukkende på de tre sifrene
(`platform/core/db/kjorer.py:54` — `v = int(fil.name[:3])`). En ny
`018_*.sql` ville blitt hoppet over på en oppgradert base (versjon 18 er
registrert) og på en fersk base ville kun den alfabetisk første `018_*`
kjørt. Tabellen under ville da aldri eksistert.

**019 er ikke bare en tabell.** Portene under kan ikke håndheves av
kallere alene; de krever at 019 erstatter funksjoner fra 016/017/018.
016-, 017- og 018-*filene* er immutable (checksum), så 019 legger nye
`CREATE OR REPLACE`-versjoner og `ALTER TABLE ... ADD COLUMN` oppå dem:

| # | Objekt i 019 | Hvorfor det ikke kan ligge i kalleren |
|---|---|---|
| 1 | `overtakelse_attestasjon` (ny tabell, m/ `rolle` + `authz_version`) + `overtakelse_attestasjon_saksbinding()` (trigger) | Fire øyne, §4; målet må bindes til saken, §1; autorisasjonen må kunne reautoriseres ved tildeling, §4.3 |
| 2 | `domeneobservasjonsrunde` + `domeneobservasjon` (nye tabeller) | Observasjonen må bæres av databasen, ikke av kalleren, §2.4 |
| 3 | `apne_domeneobservasjonsrunde(tenant, hostname)` (ny) | Runden er engangs, kortlevd og bundet til én rad, §2.4 |
| 4 | `meld_domeneobservasjon(runde, observert_txt)` (ny) | Observatøridentiteten er `session_user`, aldri en parameter, §2.4 |
| 5 | `verifiser_domenekontroll(…, p_runde UUID)` | **Førstegangsverifisering er den farligste veien** — den kan opprette en autorisasjon og utløse en overtakelse. Uten runde er den beviskravsfri, §2.5 |
| 6 | `revalider_domenekontroll(…, p_runde UUID)` | Arbeideren skal ikke være autoritet, §2.4 |
| 7 | `rydd_staged_artefakter(p_maks INT)` | Batchgrense uten å miste evidensfristpredikatet, §6 / port 25 |
| 8 | `artefaktkapabilitet.owner_generation` + `.owner_claim_id`, med oppgraderingssekvens | Fencing ved reclaim, §5 |
| 9 | `artefaktkapabilitet_statusmaskin()` + `utsted_artefaktkapabilitet()` + `innlos_artefaktkapabilitet()` + `lagre_artefakt_staged()` | Generasjonen må stemples ved utstedelse og valideres i den ATOMISKE forbrukeren, §5 |
| 10 | `avgjor_domeneovertakelse(…, p_runde UUID)` — flerpartsoppgjør, friskhetskrav og taperoppgjør | Én godkjenning må avvise de øvrige avklaringsradene i samme transaksjon (§3), telle kun ferske attestasjoner mot fersk DNS-evidens (§4), og la taperens sak kunne lukkes (§3.1) |

**Fire ting hører ikke hjemme i 019, men i Python** — autorisasjonen og
saksflyten ligger ikke i databasen, og en port som bare finnes i SQL er
ikke nådd:

| # | Objekt i Python | Hvor | Hvorfor |
|---|---|---|---|
| A | Rollen `domeneavgjorer` i `ROLLE_TIL_SCOPES` | `api/autorisasjon.py:17` | Ingen eksisterende rolle bærer `domains:adjudicate`, §4.1 |
| B | `domains:adjudicate` i `BROWSER_MUTASJONSSCOPES` | `api/app.py:799` | Uten det nektes attestanten før ruten, §4.2 |
| C | Ruten `POST /v1/domener/overtakelse/{unntak_id}/attestasjon` + **behandleren `behandle_domeneattestasjon()`** + familiegjerdet på PR-012-ruten | `api/app.py`, `api/domeneovertakelse.py`, `api/unntaksbehandling.py` | PR-012-s behandler kan ikke avgjøre en domenesak (§4.2b), og scopet slås opp fra handlingen, ikke fra saksfamilien, §4.2 |
| D | `ny → manuell` i `opprett_overtakelsessak()` | `api/domeneovertakelse.py` | Saken er ellers synlig, men ikke handterbar, §3 |

---

## 1. DDL (migrasjon 019) — autoritativ

```sql
-- Fire øyne ved positiv cross-tenant domenetildeling (§4)
CREATE TABLE overtakelse_attestasjon (
  tenant TEXT NOT NULL,                -- RLS-nøkkel; tenanten saken tilhører
  unntak_id BIGINT NOT NULL,           -- M-37-saken (unntak.id er BIGINT identity)
  aktor TEXT NOT NULL,
  rolle TEXT NOT NULL,                 -- rollen som bar scopet DA stemmen ble avgitt
  authz_version INT NOT NULL,          -- brukermedlemskap.authz_version, samme øyeblikk
  utfall TEXT NOT NULL CHECK (utfall IN ('godkjenn','avvis')),
  vinnende_tenant TEXT NOT NULL,
  hostname TEXT NOT NULL CHECK (er_kanonisk_hostname(hostname)),
  forventet_generasjon BIGINT NOT NULL,
  avgitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, unntak_id, aktor),      -- én aktør, én stemme per sak
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id));
```

`rolle` og `authz_version` er ikke pynt: uten dem kan oppgjøret ikke
bevise at begge stemmene fortsatt er autoriserte når domenet faktisk
tildeles (§4.3). Feltene speiler `menneskelig_attestasjon (bruker_id,
rolle, authz_version)` fra PR-012 (011 linje 233) — samme snapshot, samme
grunn.

**Målet utledes AV saken, det avtales ikke mellom aktørene.** FK-en over
binder bare raden til en sak; den sier ingenting om at
`(vinnende_tenant, hostname, forventet_generasjon)` er *denne sakens*
mål. To aktører som er enige med hverandre om et helt annet ventende
hostname ville ellers fått en SECURITY DEFINER-funksjon til å flytte den
raden — gjensidig enighet er ikke bevis når begge verdiene kommer fra
samme skjema. Trigger `overtakelse_attestasjon_saksbinding()` (BEFORE
INSERT) slår derfor opp sakens eget mål og avviser avvik:

```sql
-- Sakens mål ligger i idempotensnøkkelen, som SERVEREN skrev:
--   revisjonslogg.idempotency_key = 'domeneovertakelse:<hostname>:<generasjon>'
-- Hostnavnet er kanonisk (018 §0) og kan aldri inneholde kolon, så
-- split_part er entydig. Joinen bærer kilde/kategori/handling, nøyaktig
-- som `opprett_overtakelsessak` skriver dem — en fremmed loggpost med
-- samme nøkkel kan ikke matche.
SELECT split_part(r.idempotency_key, ':', 2),
       split_part(r.idempotency_key, ':', 3)::BIGINT
  INTO v_hostname, v_generasjon
  FROM public.unntak u
  JOIN public.revisjonslogg r ON r.tenant = u.tenant AND r.id = u.loggpost_id
 WHERE u.tenant = NEW.tenant AND u.id = NEW.unntak_id
   AND u.kategori = 'domeneovertakelse' AND u.handling = 'domene.overtakelse'
   AND r.kilde = 'domeneovertakelse';
```
- `NEW.hostname` må være `v_hostname`, `NEW.forventet_generasjon` må være
  `v_generasjon`. Avvik → exception, ikke en stille ignorering.
- `NEW.vinnende_tenant` må være `NEW.tenant` når `utfall = 'godkjenn'`:
  saken tilhører utfordreren (`opprett_overtakelsessak` kalles med
  `tenant_ny`), og en godkjenning som utpeker noen andre er ikke denne
  sakens utfall. `avvis` krever ingen vinner, men bærer feltet uendret
  for at radene skal kunne sammenlignes felt for felt.
- Ingen sak funnet (feil familie, fremmed `unntak_id`) → exception.

`avgjor_domeneovertakelse()` teller altså attestasjoner som allerede er
bevist å peke på sakens eget mål, og leser generasjonen på nytt under
radlåsen (§3). Enigheten mellom de to radene er da et *fire-øyne*-krav,
ikke identitetsbeviset.
**Identiteten er M-37-sakens, ikke en egen UUID.** `unntak.id` er
`BIGINT GENERATED ALWAYS AS IDENTITY`, den referensielle identiteten er
`(tenant, id)` (`unntak_tenant_id_unik`), og `opprett_overtakelsessak()`
returnerer nettopp den heltalls-IDen. En `sak_id UUID` uten
`tenant`-kolonne kunne verken hatt fremmednøkkel til saken eller
tenant-scopet RLS.

**Saken ER revisjonen — det finnes ingen `saksrevisjon`.**
`domenekontroll` har `autorisasjonsgenerasjon` (016 linje 30), ikke
`saksrevisjon`, og `verifiser_domenekontroll()` oppdaterer ikke noe felt
med det navnet. Foreldelsen trengs likevel ikke som eget felt:
`opprett_overtakelsessak()` nøkler saken på
`domeneovertakelse:<hostname>:<generasjon>`, der generasjonen er
B-radens `autorisasjonsgenerasjon` etter overtakelsen — monoton, altså
unik per konflikt. En ny konflikt gir derfor en **ny `unntak_id`**, og en
attestasjon avgitt på den forrige saken kan aldri telle mot den nye:
primærnøkkelen bærer saken. 016/018 endres ikke, og trenger det ikke.

**Ingen DELETE, og én whitelistet UPDATE: fornyelse.** Attestasjoner
foreldes etter 72 timer (§4), og primærnøkkelen tillater kun én rad per
aktør per sak — uten en vei til å attestere på nytt ville en sak der
vinduet løp ut mellom første og andre stemme vært permanent
uavgjørbar, altså nøyaktig den låsingen foreldelsen skal hindre. Triggeren
tillater derfor UPDATE av **kun** `avgitt_ts`, `utfall`, `rolle` og
`authz_version`, kun med `avgitt_ts = now()`, og aldri av `unntak_id`,
`aktor`, `vinnende_tenant`, `hostname` eller `forventet_generasjon` —
bindingsfeltene er uforanderlige, akkurat som saksbindingstriggeren
krever. **`rolle` og `authz_version` er stemmens snapshot, ikke sakens
mål:** en fornyelse er en ny stemme avgitt nå, så autorisasjonen som
telles må være den som gjaldt nå. Fryses de, ville en fornyelse kunnet
bære en foreldet autorisasjon inn i et ferskt vindu. DELETE er
forbudt. **Evidenskjeden ligger i `unntak_historikk`:** hver attestasjon
og hver fornyelse skrives som `attestasjon_registrert` med aktør, utfall
og tidspunkt, så tabellen er en projeksjon av «gjeldende stemme per
aktør», mens historikken — den som faktisk er append-only — bærer alle
stemmene som noen gang ble avgitt, også på en sak som ble forbigått av en
ny konflikt. RLS + FORCE. `sett_kontekst` først på alle veier inn.

## 2. Revalideringsarbeider og observasjonskontrakten — planlegger, observerer ikke

`disponit-domenerevalidering.timer`, hver time, egen Unix-bruker, rolle
`disponit_domains_admin`. **Arbeideren er en scheduler, ikke en kilde:**
den bestemmer *hvilke* rader som skal revalideres når, åpner en runde og
ber til slutt om avgjørelsen — men den slår ikke opp DNS selv og kan
ikke melde inn en observasjon (§2.4). Selve oppslaget gjøres av separate
**observatørprosesser** med hver sin DB-rolle, som skriver observasjonen
i eget navn. Statusbeslutningen ligger i databasen.
`pg_advisory_lock` på arbeidernøkkel — to kjøringer overlapper aldri.

**Planen avledes av hostname, aldri lagret:**
```
revalideringsminutt(hostname) = int(sha256(hostname)[0:8], 16) mod 1440
retry-slott: minutt · minutt + 4 t · minutt + 8 t     (jitter ±5 min INNENFOR slottet)
```
Bootstrap og import spres av seg selv; restore fra backup gir identisk
plan; et feilforsøk kan ikke forskyve normalplanen fordi det ikke finnes
noen lagret plan å forskyve. Vellykket forsøk setter
`siste_vellykkede_revalidering`, og senere slott hopper over raden fordi
den er fersk.

### 2.1 Tre køer, streng prioritet

**Alle tre køene ser kun `status = 'verifisert'`.** En rad i
`avklaring_kreves` er ikke foreldet — den er under avgjørelse, og
`apne_domeneobservasjonsrunde(…, 'revalidering')` godtar den ikke (§2.4).
Talte den med, ville hver eneste time plukket den i kø 1 (som er
ubegrenset og aldri kappes), åpnet en runde som funksjonen avviser, og
skrevet en revalideringsfeil på en rad der DNS-en ikke er problemet.
Det er permanent retry-last utenfor K og et feilsignal som lyver.
Ferskheten for en konfliktrad håndheves ett annet sted, i det ene
øyeblikket den betyr noe: `formal = 'overtakelsesoppgjor'`-runden ved
tildeling (§4). Vinneren får `siste_vellykkede_revalidering = now()` av
`avgjor_domeneovertakelse` (018 linje 406) og kommer derfor inn i kø 2
som fersk rad, ikke som etterslep.

| # | Kø | Regel |
|---|---|---|
| **1** | **Sikkerhetsnett** — `verifisert` og `siste_vellykkede_revalidering < now() - 26 t` | **Utenfor budsjettet. Aldri utsatt, aldri kappet** |
| 2 | **Normalslott** — `verifisert`, minuttet falt i vinduet, raden ≥ 20 t gammel | Innenfor budsjettet |
| 3 | **Etterslep** — `verifisert`, slott passert mens timeren var nede, eldste først | Budsjettet som er igjen etter kø 2 |

Kø 1 er ubegrenset *rett til å bli plukket*, ikke ubegrenset arbeid:
oppslagene kjøres med **fast samtidighetsgrense C = 8**. Ingen rad
droppes. Overskrider kø 1 budsjettet, er det en **målt hendelse**, ikke en
feil.

### 2.2 Absolutt budsjett

```
N = antall rader med status = 'verifisert'      -- samme populasjon som køene
K = ceil(0.10 * N)      -- HARDT tak per kjøring for kø 2 + kø 3 samlet
```
`N` er nøyaktig det køene kan plukke fra (§2.1). Talte den også
`avklaring_kreves`, ville budsjettet vært regnet på rader arbeideren aldri
får lov til å revalidere — K ville vokst av konflikter og krympet igjen når
de ble avgjort, uten at det hadde noe med revalideringslasten å gjøre.
**K håndheves med `LIMIT`**, ikke som forventning. Rader fra kø 2 som ikke
får plass blir etterslep og plukkes neste kjøring — slottet er avledet, så
ingenting mistes. Hashskjevhet påvirker hvor mye etterslep som oppstår,
men kan aldri bryte K.

*Drenering, regnet:* normallast ≈ N/24 ≈ 0,042·N per time; ledig kapasitet
≈ 0,058·N. Seks timers outage gir etterslep ≈ 0,25·N → drenert på
≈ 4,3 timer. 24-timersporten holder med margin.

### 2.3 Invariant vs. målt

- **Garantert av scheduleren:** kø 2 + kø 3 overskrider aldri K · kø 1
  kappes aldri · ingen rad forlater planen · retry forskyver ikke planen.
- **Målt driftsegenskap:** hvor jevnt radene faktisk fordeler seg.
  `sha256 mod 1440` er tilnærmet uniform, men **garanterer ikke**
  at ingen time får > 10 % av en vilkårlig populasjon. Skjevhet er
  observasjon, aldri sikkerhetsbevis.

### 2.4 Resolverkontrakt og korrelert feil

**Observasjonen skal ikke kunne påstås av den som ber om revalideringen.**
016/018-signaturen `revalider_domenekontroll(tenant, hostname, aktor)`
tar ingen observert TXT-verdi og ingen resolveridentiteter; den setter
`siste_vellykkede_revalidering = now()` på enhver verifisert rad. Siden
arbeideren har `disponit_domains_admin` (016 linje 929), kunne en
feilende eller kompromittert arbeider friske opp et hvilket som helst
verifisert domene uten å ha slått opp noe.

**Å ta TXT-verdien som parameter løser det ikke.** Challenge-tokenet står
i en offentlig, cachebar DNS-TXT-post: enhver som har sett det én gang —
arbeideren selv, hver eneste kjøring — kan reprodusere hashen for alltid.
En hashsjekk beviser *kunnskap om tokenet*, ikke at noen resolver svarte
nå. På samme måte er `p_resolvere TEXT[]` bare tekst kalleren skriver:
en kompromittert arbeider oppgir to oppdiktede navn og består
distinkthetskravet. Begge deler lar arbeideren fortsette å friske opp et
domene etter at TXT-posten er fjernet. Regelen må derfor flyttes ut av
kallerens hender helt, ikke pakkes inn i flere parametere.

**Observatørene skriver selv, i egen rolle.** 019 innfører en rundetabell
og en observasjonstabell, og observasjonen registreres av en funksjon som
tar identiteten fra `session_user` — aldri fra et argument:

```sql
CREATE TABLE domeneobservasjonsrunde (
  runde_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant TEXT NOT NULL, hostname TEXT NOT NULL CHECK (er_kanonisk_hostname(hostname)),
  formal TEXT NOT NULL                    -- hva runden kan brukes til, lukket enum
    CHECK (formal IN ('verifisering','revalidering','overtakelsesoppgjor')),
  apnet TIMESTAMPTZ NOT NULL DEFAULT now(),
  utloper TIMESTAMPTZ NOT NULL,           -- kort, f.eks. now() + 5 min
  status TEXT NOT NULL DEFAULT 'apen' CHECK (status IN ('apen','brukt','forkastet')));

CREATE TABLE domeneobservasjon (
  runde_id UUID NOT NULL REFERENCES domeneobservasjonsrunde (runde_id),
  observator TEXT NOT NULL,               -- session_user, satt av funksjonen
  txt_hash TEXT NOT NULL,                 -- sha256, beregnet I databasen
  observert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (runde_id, observator));    -- én observatør, én observasjon
```

`formal` er en lukket enum og **ikke** dekorasjon: en runde åpnet for å
friske opp et verifisert domene skal ikke kunne brukes til å opprette en
ny autorisasjon eller til å avgjøre en tvist. Hver konsument krever sitt
eget formål, og runden er uansett engangs.

| Funksjon | Kalles av | Håndhever |
|---|---|---|
| `apne_domeneobservasjonsrunde(tenant, hostname, formal)` → `runde_id` | arbeideren (`disponit_domains_admin`) eller API-et (`disponit`) | Raden må finnes og stå i den statusen formålet krever (`ventende`/`utlopt` for `verifisering`, `verifisert` for `revalidering`, `avklaring_kreves` for `overtakelsesoppgjor`); `challenge_token_hash` må være satt og challengen ikke utløpt; kort TTL; runden er engangs |
| `meld_domeneobservasjon(runde_id, observert_txt)` | **observatørrollene** `disponit_domeneobservator_*` | `observator := session_user`; hashen beregnes i DB og må være lik radens `challenge_token_hash`; runden må være `apen` og ikke utløpt |
| `verifiser_domenekontroll(tenant, hostname, wildcard, aktor, p_runde UUID)` | API-et | Som under, pluss: runden har `formal = 'verifisering'`, gjelder dette `(tenant, hostname)`, er `apen` og ikke utløpt, og har **≥ 2 observasjoner fra distinkte `observator` med samme `txt_hash`** — alt under hostname-låsen, FØR noen status settes eller noen overtakelse utløses |
| `revalider_domenekontroll(tenant, hostname, aktor, p_runde UUID)` | arbeideren | Samme runde-krav med `formal = 'revalidering'`; så settes tidsstemplet og runden merkes `brukt` |
| `avgjor_domeneovertakelse(…, p_runde UUID)` | API-et, ved **positiv** tildeling | Samme runde-krav med `formal = 'overtakelsesoppgjor'` på vinnerens `(tenant, hostname)` — §4 |

Det som er vunnet: arbeideren har **ikke** EXECUTE på
`meld_domeneobservasjon`, og kan ikke skrive `domeneobservasjon`
direkte. Identiteten er `session_user`, altså Postgres' egen
autentisering (klientsertifikat/passord per observatørprosess) — ikke en
streng kalleren velger. En kompromittert arbeider kan derfor åpne runder
den vil og kalle funksjonen så ofte den vil, men uten to ekte
observatørprosesser som *hver for seg* har slått opp TXT-posten og meldt
den inn i samme runde, skjer ingenting. Klarteksten lagres fortsatt aldri;
det er hashen som bæres. Observatøridentitetene skrives på
`domenekontroll_hendelse` som evidens.

De gamle signaturene uten `p_runde` — `revalider_domenekontroll` med tre
argumenter og `verifiser_domenekontroll` med fire —
**REVOKE-es fra `disponit_domains_admin` og fra `disponit`** i samme
migrasjon; ellers består den gamle, ubeviste veien ved siden av den nye.

### 2.5 Førstegangsverifisering er samme port

**Observatørkravet kan ikke gjelde bare oppfriskningen.** En runde kan per
definisjon først åpnes for `revalidering` når raden allerede er
`verifisert` — men veien *inn* i `verifisert` var i 016/018
`verifiser_domenekontroll(tenant, hostname, wildcard, aktor)`, som tar
ingen observert TXT-verdi i det hele tatt og har EXECUTE for
`disponit_domains_admin` (016 linje 928). En kompromittert domenearbeider
kunne altså hoppe over hele §2.4-maskineriet ved å kalle den direkte: den
oppretter en autorisasjon uten ett eneste DNS-oppslag, og — verre — den er
funksjonen som *utløser overtakelsen* av en annen tenants aktive
verifisering (018 B4). Å kreve to observatører for å friske opp et domene,
men null for å opprette eller overta det, er ikke en port; det er en port
med dør ved siden av.

019 gir derfor `verifiser_domenekontroll` en femte parameter `p_runde
UUID` med **nøyaktig samme runde-krav** som revalideringen, kontrollert
under hostname-låsen **før** noen status settes og før overtakelsesgrenen
i det hele tatt evalueres. Runden åpnes med `formal = 'verifisering'` mot
en rad som er `ventende` eller `utlopt` og som har en ikke-utløpt
challenge — nøyaktig den tilstanden hvor TXT-posten skal ligge ute.
Førstegangsverifisering og oppfriskning har med dette **én** felles
beviskrav-grense, ikke to.

**Restrisikoen, sagt rett ut:** en kompromittert *observatør* kan lyve om
hva dens resolver svarte, og to samvirkende observatører kan forfalske en
runde. Databasen kan ikke selv slå opp DNS og har ingen måte å avgjøre
det på. Det som begrenser skaden er at (a) diversitetsporten under
plasserer observatørene hos ulike operatører, i ulike nett og som ulike
prosesser med hver sin DB-credential, så én kompromittert vert gir én
stemme; (b) hver runde er engangs, kortlevd og bundet til ett formål og
én `(tenant, hostname)`, så en observasjon kan aldri gjenbrukes til en
annen overgang enn den den ble meldt for; og (c) arbeideren som *velger*
hvilke rader som skal behandles, aldri selv kan avgi en stemme.
To samvirkende observatører kan derimot både opprette, friske opp og —
sammen med to attestanter — vinne en tvist. Det er en akseptert, navngitt
restrisiko som hviler på deploy-porten under, ikke på et DNS-bevis
databasen kan kontrollere selv.

- **≥2 uavhengige observatører; uenighet → ikke vellykket revalidering.**
  Uenighet er ikke en beslutning arbeideren tar: to observasjoner med
  ulik `txt_hash` i samme runde får rett og slett aldri funksjonen til å
  telle to like.
- **Diversitet er deploy-port:** minst to observatørprosesser, hver med
  sin egen DB-rolle og credential, mot resolvere hos ulike operatører og
  i ulike nett. Konfigurasjon som bryter det → oppstart nektes.
  Deploy-porten er det som gjør identitetene *uavhengige*; databasen
  autentiserer dem og teller dem, men kan ikke vite hvem som eier
  resolverne bak.
- **Bred feil (> 20 % innen én time) → én driftsalarm.** Terskelen
  dedupliserer **varslingen**; den klassifiserer ikke tenantens tilstand,
  oppretter ingen M-37-sak, og skjuler ikke at `tenant X / hostname Y` har
  tre døgn uten vellykket revalidering. Individuelle feil forblir
  tenantbundet, auditert og søkbart evidens. Terskelen er konfigurerbar
  og målt.
- Alarmen sier «vi fikk ikke svar», aldri «domenene er tapt».

## 3. M-37-kobling — konflikten kan avgjøres

- **Inn:** saken opprettes av `verifiser_domenekontroll()` (014b B4) og
  **blir synlig** i PR-012-flaten: familie `domeneovertakelse`, lineage
  til begge rader, begge hostnames i saksvisningen.
- **Ut:** attestasjonen kaller `avgjor_domeneovertakelse()`.
  **Ingen knapp skriver status** — invariant 3.
- **Saken må settes `manuell` når den opprettes.** Synlig er ikke det
  samme som handterbar: `opprett_overtakelsessak()` skriver saken uten
  status, altså `ny` (`003_unntak_api_policy.sql` linje 136), og
  `sakstype='sikkerhet'` gjør at normalarbeideren aldri claimer den
  (`claim_neste_sak` filtrerer `WHERE k.sakstype = 'normal'`, 007 linje
  861). Ingenting flytter den videre av seg selv. PR-012 åpner en runde
  kun fra `manuell` og hever ellers `runde_ulovlig_tilstand`
  (`unntaksbehandling.py:312`) — så uten dette ville hver eneste
  overtakelsessak stått i køen uten at verken godkjenning eller avvisning
  kunne begynne, og port 20 vært umulig å bestå. PR-015 legger derfor den
  ene, auditerte overgangen inn i `opprett_overtakelsessak()`, i **samme
  transaksjon** som saken skrives:
  `UPDATE unntak SET status='manuell' WHERE tenant=… AND id=… AND status='ny'`.
  - Overgangen er whitelistet fra før (`ny → manuell`, 011 linje 151);
    det er R3-veien for saksklasser som ikke har noen automatisk vei
    (005 linje 103), og en overtakelse er per definisjon en av dem
    (`UKJENT_SNAPSHOT`, `maks_auto_forsok = 0`). Statustriggeren skriver
    `statusendring` i `unntak_historikk` som for enhver annen overgang.
  - **Kun på opprettelsesveien.** Idempotensgrenen returnerer en
    eksisterende sak urørt; `AND status='ny'` er gjerdet som gjør at et
    retry aldri kan dra en sak som alt står i `venter_godkjenning` eller
    er terminal, tilbake til `manuell`.
- **Saken må også kunne bære en runde.** `manuell` er ikke terminalt,
  men den ENESTE veien ut er `manuell → venter_godkjenning`, og den
  krever at en `apen godkjenningsrunde` allerede finnes (011 linje
  185–190). Verken `løst` eller `avvist` kan nås fra `manuell` direkte.
  Domeneoppgjøret må altså gå gjennom PR-012-s runde — det er ikke et
  valg, det er statusmaskinen. `opprett_godkjenningsrunde` stiller da to
  krav loggposten må oppfylle, og `opprett_overtakelsessak()` er den som
  skriver loggposten:
  - **En blokkerende grunnkode sist i `begrunnelse`.** `_siste_grunnkode`
    leser den siste posten i kjeden (`unntaksbehandling.py:77–85`); er
    den tom, feiler runden på `godkjenn_utilgjengelig`. Koden er
    `domene_overtakelse_avklaring`, og den står **med vilje ikke** i noen
    tenants `menneskelig_overstyring.godkjennbare` — da er
    `_er_godkjennbar` usann, den ordinære saksvisningen tilbyr aldri
    «godkjenn» på en domenesak (`lesing.py:437`), og runden åpnes kun av
    domenebehandleren med `krev_godkjennbar=False`. Familiegjerdet (§4.2)
    er porten; dette er beltet.
  - **En oppløselig `policy_id` på loggposten.** `_policy_id()` leser
    `revisjonslogg.policy_id` og krever en gyldig policyref
    (`unntaksbehandling.py:481–490`); hashen fryses på runden. Saken
    skrives derfor med utfordrertenantens aktive policyref, som enhver
    annen M-37-sak. Har tenanten ingen aktiv policy, kan runden ikke
    åpnes — en ærlig fail-closed-tilstand som svares som
    `policy_id_ukjent`, ikke maskeres.
- **Scope `domains:adjudicate`**, eget, båret av den nye rollen
  `domeneavgjorer` (§4.1). Unntaksscopene `exceptions:approve` /
  `:reject` / `:escalate` gir aldri cross-tenant domeneautoritet, uansett
  kombinasjon.
- **Én åpen sak per konflikt, ikke per hostname.** Idempotensnøkkelen er
  `domeneovertakelse:<hostname>:<generasjon>`: samme konflikt (samme
  generasjon) → samme sak ved retry; ny konflikt (ny, monoton generasjon)
  → ny sak. Terminal sak gjenbrukes aldri — det faller ut av at nøkkelen
  er unik per konflikt, ikke av en UNIQUE på ikke-terminal status. En
  UNIQUE på hostname ville tvert imot blokkert den nye saken en tredje
  part må ha for å kunne avgjøres.
- Saksvisningen viser det databasen kan bevise: hvem som besto challenge
  når, hvem som mistet autorisasjonen, og at A er stoppet uansett utfall.
  Den anslår ikke hvem som «egentlig» eier domenet.

**A→B→C:** hostname-låsen serialiserer uansett antall parter, men C
overtar **ikke** B-s plass. Slik 018 faktisk gjør det: A ble `tilbakekalt`
da B overtok; når C verifiserer mens B står i `avklaring_kreves`, treffer
C `avklaring_kreves`-grenen, som setter C → `avklaring_kreves` med
`konflikt_motpart = B` og flytter `hostname_binding` til C — **B røres
ikke**. B og C står altså begge i avklaring, hver med sin sak. Det er en
flerpartstvist, ikke en kjede av tilbakekallinger.
**Ingen tenant blir `verifisert` av at en annen taper** — A gjenoppstår
ikke, og kun `avgjor_domeneovertakelse()` løfter noen ut av avklaring.
PR-015 må derfor kunne avgjøre en tvist med **flere åpne saker på samme
hostname**: en `godkjenn` for én part krever at de øvrige avklaringsradene
avvises i samme transaksjon under hostname-låsen. ≥3 parter innen 24 t →
`hoy_konfliktrate` på sakene; det stopper ingenting automatisk.

**Det krever at 019 erstatter `avgjor_domeneovertakelse()` (objekt 10).**
018-versjonen kan ikke gjøre dette, og det er ikke en mangel som kan
dekkes av kalleren:
- Den rører **kun den tenanten den får inn** (018 linje 352–426) — de
  øvrige `avklaring_kreves`-radene på samme hostname står urørt.
- Godkjenning krever i tillegg at `hostname_binding` allerede står på
  `p_tenant` (018 linje 390–396). I A→B→C står bindingen på C, så B kan
  **ikke** godkjennes i det hele tatt — selv om B er den saken fire øyne
  faktisk har attestert.

019-versjonen tar hostname-låsen, og gjør så hele oppgjøret i én
transaksjon:
```
avgjor_domeneovertakelse(p_tenant, p_hostname, p_forventet_generasjon,
                         p_godkjent, p_aktor, p_runde UUID)
  1. lås hostname; les p_tenant-raden FOR UPDATE
  2. status må være 'avklaring_kreves' og generasjonen må stemme (uendret fra 018)
     — MED ÉN TILLEGGSGREN, se 5 under
  3. p_godkjent:
       - p_runde må være en `apen`, ikke utløpt runde med
         formal='overtakelsesoppgjor' på (p_tenant, p_hostname), med >= 2
         observasjoner fra distinkte observatører og samme txt_hash (§4)
       - attestasjonene må være FERSKE (§4)
       - vinneren settes 'verifisert', generasjon++, nytt 90-døgnsvindu
       - hostname_binding settes til p_tenant  (den FLYTTES, den forutsettes ikke)
       - HVER ANNEN rad på p_hostname med status 'avklaring_kreves' settes
         'tilbakekalt' med grunn 'tapte_domeneoppgjor', og en hendelse per rad
         — i SAMME transaksjon, under samme lås.
         GENERASJONEN PÅ TAPERRADENE RØRES IKKE (se §3.1)
       - runden merkes 'brukt'
  4. NOT p_godkjent: kun p_tenant-raden → 'tilbakekalt', generasjon++
       (uendret fra 018); de øvrige avklaringsradene blir stående,
       tvisten er ikke avgjort. Ingen runde kreves — avvisning gir ingen
       autorisasjon
  5. TAPEROPPGJØR (kun NOT p_godkjent): står raden allerede 'tilbakekalt'
       med grunn 'tapte_domeneoppgjor' på nettopp p_forventet_generasjon,
       er kallet en LOVLIG NO-OP som returnerer 'alt_avgjort' i stedet for
       å feile. Domeneraden røres ikke; det er saken som skal lukkes (§3.1)
```
Bindingssjekken fra 018 faller altså bort som *forutsetning* og blir en
*konsekvens*: det er avgjørelsen som utpeker bindingshaveren, ikke
bindingshaveren som avgjør hvem som kan avgjøres. Gjerdet mot en
foreldet sak ligger fortsatt i generasjonen, som leses under radlåsen,
og nå også i saksbindingen fra §1 — det er de to som gjør at en gammel
attestasjon ikke kan autorisere en ny konflikt.

### 3.1 Taperens sak må kunne lukkes

**Å tilbakekalle taperens domenerad avgjør ikke taperens M-37-sak.** I
A→B→C har både B og C hver sin åpne sak. Godkjennes B, settes C-s
domenerad `tilbakekalt` i samme transaksjon — men C-s sak står fortsatt
åpen i unntaksflaten, og med 018/019-gjerdet «status må være
`avklaring_kreves`» ville *ethvert* forsøk på å avgjøre den feile. Uten
punkt 5 over blir C-s sak permanent uhandterbar: en sak i køen som ingen
handling kan lukke. Det er nøyaktig den tilstanden invariant 10 forbyr —
flaten ville påstått «venter på avgjørelse» om noe som alt er avgjort.

**Hvorfor det ikke kan gjøres atomisk fra funksjonen:** `unntak` har
RLS + FORCE (`011_unntaksbehandling.sql` linje 414), og C-s sak tilhører
C-s tenant. En SECURITY DEFINER-funksjon som kjører i B-s tenantkontekst
skal ikke — og skal ikke kunne — skrive C-s saksrad. Å legge inn en
omgåelse for dette ville brutt tenantisolasjonen for å rydde en kø. Det er
feil pris.

**Oppgjøret er derfor todelt, og begge delene er beviste:**
1. **Domeneradene avgjøres atomisk** i B-s transaksjon, under
   hostname-låsen. Utfallet er ikke til forhandling etterpå.
2. **C-s sak lukkes i C-s egen kontekst**, med **én** `avvis`-attestasjon
   (§4) på **domeneruten** (§4.2) — ikke på PR-012-s generelle
   handlingsendepunkt. Runden er PR-012-s (statusmaskinen krever det, §3),
   men behandleren og oppgjøret er domenets (§4.2b). Grunnen til at den
   generelle ruten ikke kan brukes er konkret: `POST /v1/unntak/{id}/handling` autentiserer `avvis` med
   `exceptions:reject` (`unntaksbehandling.py:597`, og på nytt under
   sakslåsen på linje 265), mens `domeneavgjorer` med vilje ikke bærer
   noen unntaksskrivescopes (§4.1).
   Sendte vi C-s avgjører den veien, måtte tenanten i tillegg gitt hen
   `godkjenner` — altså skrivetilgang til hele unntakskøen for å lukke én
   domenesak. Punkt 5 over er det som gjør veien farbar i databasen:
   funksjonen ser at raden alt er `tilbakekalt` med grunn
   `tapte_domeneoppgjor`, returnerer `alt_avgjort`, og kalleren fører
   saken til `avvist` med hendelsen `avvist_handling`. Ingen ny
   domeneovergang, ingen ny autorisasjon — bare en sak som lukkes mot
   evidens databasen alt bærer.

**Derfor røres ikke generasjonen på taperradene.** Saken C-s attestasjon
er bundet til (§1) bærer den generasjonen C-raden hadde da konflikten
oppstod, og saksbindingstriggeren krever at `forventet_generasjon`
stemmer med sakens idempotensnøkkel. Bumpet vi generasjonen ved
`tapte_domeneoppgjor`, kunne C-s attestasjon aldri skrives i det hele
tatt — saken ville vært låst inne av den transaksjonen som skulle gjøre
den lukkbar. Generasjonen bumpes uansett når det trengs: en
reapplikasjon fra C treffer reapplikasjonsgrenen i
`verifiser_domenekontroll`, som selv øker den og dermed skaper en ny sak
(018 linje 179–185).

**Flaten må vise det.** C-s sak merkes `tapte_domeneoppgjor` med
motparten og hostnavnet, slik at handlingen er «bekreft utfallet», ikke
«avgjør en tvist som alt er avgjort». Saken er fortsatt et menneskelig
klikk — motoren beslutter, mennesket attesterer (invariant 3) — men det
finnes alltid nøyaktig én lovlig handling, og den krever kun én
attestasjon.

## 4. Fire øyne ved positiv tildeling

| Utfall | Krav | Hvorfor |
|---|---|---|
| **Avvis** (B → `tilbakekalt`) | **Én** attestasjon | Fail-closed; ingen får autorisasjon |
| **Godkjenn** (B → `verifisert`) | **To distinkte** attestasjoner | Etablerer hvilken kunde plattformen autoriserer |

- De to radene må ha identisk `(unntak_id, utfall, vinnende_tenant,
  hostname, forventet_generasjon)`. Avvik → ingen avgjørelse, aldri en
  sammenslåing. **Enigheten er i tillegg til saksbindingen fra §1, ikke i
  stedet for den:** begge radene er allerede bevist å bære sakens eget
  mål, så det de to øynene faktisk bekrefter er *utfallet*, ikke hvilken
  rad som skal flyttes.
- **Ingen enkelt aktør produserer begge** — håndhevet av primærnøkkelen,
  ikke av UI-et. Begge krever `domains:adjudicate`, avgitt på domeneruten
  (§4.2) — det er den ene veien inn for denne saksfamilien.
- **Ny konflikt invaliderer ventende attestasjoner** i kraft av
  saksidentiteten: C-s konflikt får sin egen `unntak_id` (ny generasjon,
  §1), og en attestasjon avgitt på B-s sak bærer B-s `unntak_id` i
  primærnøkkelen. Den kan derfor ikke telles av en avgjørelse på C-s sak
  — ingen revisjonsteller å øke, ingen `saksrevisjon` som må holdes i
  synk. Radene bevares.
- **Motoren beslutter:** `avgjor_domeneovertakelse()` teller
  attestasjonene under hostname-låsen og gjør overgangen.
- **Og hver talt stemme må fortsatt være autorisert** når overgangen
  skjer — ikke bare da den ble avgitt. Se §4.3; det er den porten som
  gjør at en fjernet rolle faktisk får virkning på en ventende sak.

- **Attestasjoner foreldes, og godkjenning krever fersk DNS-evidens.**
  `avgitt_ts` skrives, men uten et krav til den er den kun pynt. Angrepet
  er konkret: B beviser DNS-kontroll, får én attestasjon, fjerner
  TXT-posten fra sonen — og får den andre attestasjonen måneder senere.
  Ingenting i mellomtiden kan oppdage det, for en rad i
  `avklaring_kreves` er blokkert både fra verifisering (018 linje 163) og
  fra revalidering. `avgjor_domeneovertakelse()` ville da delt ut et
  ferskt 90-døgnsvindu på evidens som ikke lenger fantes. To krav lukker
  det, og begge håndheves i funksjonen, ikke i UI-et:
  1. **Attestasjonsvindu.** Kun rader med
     `avgitt_ts > now() - interval '72 hours'` telles. Eldre rader slettes
     aldri (append-only, de er evidens for at noen attesterte), men de
     teller ikke. Utløper vinduet, må begge aktørene attestere på nytt —
     samme sak, samme `unntak_id`, ny `avgitt_ts`. Det krever at
     primærnøkkelen `(tenant, unntak_id, aktor)` kan **fornyes**: en ny
     attestasjon fra samme aktør på samme sak er en `INSERT … ON CONFLICT
     DO UPDATE` av `avgitt_ts` og `utfall` — den eneste tillatte
     endringen, håndhevet av append-only-triggeren, og hver fornyelse
     skrives til `unntak_historikk` som `attestasjon_registrert`.
  2. **Fersk observasjonsrunde ved positiv tildeling.** Godkjenning
     krever i tillegg `p_runde` — en `apen`, ikke utløpt runde med
     `formal = 'overtakelsesoppgjor'` på vinnerens `(tenant, hostname)`,
     med ≥ 2 observasjoner fra distinkte observatører og samme
     `txt_hash` (§2.4). Det er dette som gjør at TXT-posten må ligge ute
     **i det øyeblikket domenet tildeles**, ikke bare da konflikten
     oppstod. Runden er kortlevd og merkes `brukt`, så den kan ikke
     spares på.
  `avvis` krever ingen av delene: én attestasjon, ingen autorisasjon
  gitt, fail-closed.

- **Én autorisert aktør → positiv tildeling er umulig, permanent.**
  Riktig fail-closed, men det skal *sies* helt: feilkode
  `krever_to_attestasjoner` med antall autoriserte aktører, oversatt i UI,
  **og med den eneste faktiske utveien: tenanten må gi
  `domains:adjudicate` til en aktør til — se §4.1, som er det som gjør
  den utveien mulig i det hele tatt.**

  Det finnes **ingen tidsbasert utvei** — «vent til A-s 90-døgnsvindu
  løper ut» er ikke en av dem, og skal ikke stå i UI-et:
  1. A settes `tilbakekalt` i selve overtakelsen (018 B4 rad 1). A-s gamle
     `utloper` er dermed uten virkning; det finnes ikke noe vindu som
     løper ut.
  2. B står `avklaring_kreves`, og `verifiser_domenekontroll()` returnerer
     tidlig for nettopp den statusen (018 linje 163). Et nytt
     verifiseringsforsøk fra B logger `verifisering_blokkert` og gjør
     ingenting.
  3. Selv `avvis` (én attestasjon) lukker det ikke: B → `tilbakekalt` med
     `konflikt_motpart` satt, og en reapplikasjon treffer da
     reapplikasjonsgrenen som sender B rett tilbake til
     `avklaring_kreves` med en ny sak.

  En tenant med én autorisert aktør blir altså stående i avklaring til
  det finnes to. Det er en akseptabel fail-closed-tilstand, men den må
  være **legibel**, ikke en instruksjon om å vente på noe som aldri
  inntreffer.

### 4.1 Rollen som bærer scopet

**«Gi scopet til en aktør til» er ikke en handling som finnes ennå.**
Autorisasjonskilden er `brukermedlemskap.roller`, oversatt av
`scopes_for_roller()` mot det **lukkede** kartet `ROLLE_TIL_SCOPES`
(`platform/core/api/autorisasjon.py:17–49`). En ukjent rolle gir tomt
scope-sett (default-deny), og **ingen** av de fem rollene i kartet —
`leser`, `sikkerhet`, `admin`, `godkjenner`, `policyforvalter` — bærer
`domains:adjudicate`. Slik dokumentet stod, var «den eneste faktiske
utveien» dermed umulig å utføre: hver eneste positive overtakelse ville
vært permanent blokkert, også for en tenant med ti brukere. PR-015 legger
derfor rollen inn:

```python
# platform/core/api/autorisasjon.py — ROLLE_TIL_SCOPES
"domeneavgjorer": frozenset({"decisions:read", "exceptions:read",
                             "domains:adjudicate"}),
```

- **Egen rolle, ikke et påheng på `godkjenner` eller `admin`.** En
  cross-tenant domenetildeling er en annen fullmakt enn å behandle egen
  unntakskø; å slå dem sammen ville gitt hver eksisterende godkjenner
  domeneautoritet uten at noen bestemte det. Rollen er dessuten det som
  gjør fire-øyne reelt: tenanten kan gi `domeneavgjorer` til to personer
  uten å gi dem noe annet.
- **Leserettighetene følger med av nødvendighet:** en avgjørelse kan ikke
  attesteres uten å kunne åpne saken (`exceptions:read`) og se lineagen
  (`decisions:read`). Ingen skrivescopes utover det ene.
- **Scopenavnet `exceptions:handle` finnes ikke** og skal ikke brukes noe
  sted i PR-015. PR-012-scopene er per handling —
  `exceptions:approve` / `:reject` / `:escalate` — nettopp for at et
  reject-scope aldri skal kunne godkjenne. Porten under er derfor
  formulert mot dem.
- **Tildeling er en eierhandling, ikke en ny flate i v1.** Roller settes i
  `brukermedlemskap.roller` (migrasjon 010 linje 61); trigger
  `brukermedlemskap_authz_bump()` øker `authz_version` ved enhver endring
  (010 linje 71–85), så et nytt scope trer i kraft uten omstart og en
  fjernet rolle river sesjonen med seg ved neste forespørsel
  (`sesjon.py:562–566`). Det gjelder **sesjoner**; en allerede avgitt
  stemme dør ikke av seg selv — det er §4.3 som gjør den delen sann.
  Rolleadministrasjon i UI er registrert
  arbeidselement, ikke PR-015-scope — men **DB-veien må dokumenteres i
  `docs/RUTINER.md`**, ellers er utveien fortsatt bare en påstand.

### 4.2 Veien inn — egen rute, og døren må slippe rollen gjennom

**Et scope ingen rute spør etter er ingen fullmakt.** Rollen fra §4.1 er
nødvendig, men ikke tilstrekkelig: PR-012-s handlingsendepunkt slår opp
scopet fra *operatørhandlingen* (`_HANDLING_SCOPE`,
`unntaksbehandling.py:597`), ikke fra saksfamilien, så en `domeneavgjorer`
ville blitt nektet ved døren på `exceptions:approve` — og en `godkjenner`
sluppet inn på en cross-tenant domenesak, stikk i strid med §3 og port 13.
PR-015 gir derfor overtakelsessaken sin **egen rute**, og setter et gjerde
på den generelle:

| Rute | Scope | Familie |
|---|---|---|
| `POST /v1/domener/overtakelse/{unntak_id}/attestasjon` (ny) | `domains:adjudicate` | kun `kategori = 'domeneovertakelse'`, bevist under sakslåsen |
| `POST /v1/unntak/{id}/handling` (PR-012, uendret ellers) | `exceptions:approve` / `:reject` / `:escalate` | **avviser** `domeneovertakelse` med `feil_saksfamilie` (409), under samme lås |

- **Byggeklossene er PR-012-s; oppgjøret er domenets.** Ruten er tynn —
  form, auth, CSRF (dobbel-innsending, som PR-012) — men den delegerer
  **ikke** til `behandle_unntakshandling`. Den kaller
  `behandle_domeneattestasjon()` i `api/domeneovertakelse.py`, som
  gjenbruker PR-012-s beviste mekanismer i samme rekkefølge
  (idempotens-claim i eiertransaksjonen, `FOR UPDATE` på saken,
  reautorisering etter låsen, optimistisk `saksversjon`-lås,
  `opprett_godkjenningsrunde`, `unntak_historikk`) og erstatter kun det
  som er motorens: policy-/intensjonsleddet og beslutningen. Se §4.2b for
  hvorfor delegering ikke er mulig, og for rekkefølgen.
- **Familien bevises, den påstås ikke av stien.** `kategori` leses fra
  den `FOR UPDATE`-låste raden i steg 2 og må være `domeneovertakelse`;
  er den noe annet, er svaret `unntak_ukjent` (404) — en avgjører har
  ikke `exceptions:read` på resten av køen som saksliste og skal ikke
  kunne kartlegge den gjennom feilkoder. Gjerdet på den generelle ruten
  er speilbildet, men svarer `feil_saksfamilie` (409): den som står der
  har allerede lov til å se saken, så koden skal si *hvorfor* handlingen
  ikke hører hjemme her, ikke skjule at saken finnes. Koden legges i
  `_FEIL_HTTP` (`unntaksbehandling.py:602`), ikke overlatt til
  fallbacken. Det er *dette* gjerdet som gjør port 13 sann: uten det
  ville tre unntaksscopes fortsatt kunne drive en domenesak gjennom
  godkjenningsrunden.
- **Browsersesjonen må slippe gjennom.** `_autentiser()` nekter enhver
  muterende browsersesjon som ikke står i `BROWSER_MUTASJONSSCOPES`
  (`platform/core/api/app.py:799–806, 832`) — et lukket sett som i dag er
  PR-012-s tre unntaksscopes pluss PR-013-s to policyscopes. To mennesker
  med `domeneavgjorer` ville altså blitt nektet **før** ruten i det hele
  tatt ble nådd, uansett hva `scopes_for_roller()` returnerer, og port
  20g vært umulig ende-til-ende. PR-015 utvider derfor settet:

  ```python
  # platform/core/api/app.py — BROWSER_MUTASJONSSCOPES
  "domains:adjudicate",   # PR-015 §4.2: attestasjon er en menneskehandling
                          # i nettleseren, CSRF-vernet som PR-012-s.
  ```

  Det er en bevisst utvidelse av et lukket sett, ikke en oppmykning:
  attestasjonen *er* en menneskelig handling i en browsersesjon, og
  vernet er nøyaktig det PR-012 bruker — dobbel-innsending på en rute som
  selv verner seg. Maskintokens er upåvirket.

### 4.2b Egen behandler — `behandle_unntakshandling` kan ikke avgjøre en domenesak

**«Samme flyt, annet scope» er ikke en mulig implementasjon.** Å sende
domenestemmen inn i `behandle_unntakshandling` med `domains:adjudicate`
feiler på fem uavhengige steder, og de fire første feiler *før* noen
stemme registreres:

1. **Scopet er ikke kallerens.** Funksjonen slår det opp selv fra det
   lukkede kartet `_OP_SCOPE[operatorhandling]`
   (`unntaksbehandling.py:182, 265`) — de tre unntakshandlingene, ingen
   parameter. `domains:adjudicate` kan altså ikke «sendes med»; det måtte
   vært lagt inn i kartet, og da ville det også åpnet den generelle
   ruten, stikk i strid med gjerdet over.
2. **Runden kan ikke åpnes for `godkjenn`.** Endepunktet åpner runden med
   `krev_godkjennbar=True` for godkjenn, og `opprett_godkjenningsrunde`
   krever da `unntak.intensjon_pakrevd` (`unntaksbehandling.py:129`). En
   overtakelsessak har ingen handlingsintensjon (`UKJENT_SNAPSHOT`), så
   den *første* godkjenn-attestasjonen dør på `godkjenn_utilgjengelig`.
3. **Forretningsrollen finnes ikke.** Godkjenn krever
   `menneskelig_overstyring.krever_rolle` fra tenantens **aktive
   forretningspolicy**, og at operatøren bærer nettopp den rollen
   (`unntaksbehandling.py:280–289`). `domeneavgjorer` er en
   plattformrolle, ikke et policyfelt; ingen tenants policy nevner den.
4. **Intensjonen dekrypteres ubetinget ved terskel.** Ved nådd terskel
   kaller flyten `kryptering.hent_dek(conn, tenant, hi_key_id)` og
   dekrypterer `handlingsintensjon` før motoren kjøres
   (`unntaksbehandling.py:414–421`). `hi_key_id` er NULL på en
   overtakelsessak.
5. **Utfallet ville vært feil selv om alt over gikk.** Flyten tar ingen
   `p_runde`, kaller aldri `avgjor_domeneovertakelse()`, og setter ved
   TILLAT saken `venter_utførelse` (`unntaksbehandling.py:466`) — en
   tilstand som venter på en utførelse som ikke finnes. Avvis-grenen
   setter saken `avvist` med én gang. Domeneraden ville stått **urørt**
   etter en fullført attestasjonsrunde: saken lukket, autorisasjonen ikke
   flyttet. Det er nøyaktig påstanden invariant 10 forbyr.

**Behandleren, med rekkefølgen:**
```
behandle_domeneattestasjon(tenant, aktor, unntak_id, utfall,
                           forventet_saksversjon, dns_runde_id, idempotency_key)
  1. idempotens-claim i eiertransaksjonen        (som PR-012 steg 1)
  2. FOR UPDATE på unntaksraden. kategori må være 'domeneovertakelse',
     ellers `unntak_ukjent` (404) — familiegjerdets speilbilde (§4.2)
  3. reautorisering ETTER låsen: medlemskap + `domains:adjudicate`,
     fail-closed, ingen fallback-rolle  (som PR-012 steg 3)
  4. optimistisk lås på `saksversjon`             (som PR-012 steg 4)
  5. runden: er den aktive runden UTLØPT, merkes den `utlopt` og saken
     føres tilbake til `manuell` (begge whitelistet) — attestasjonene
     røres ikke, de henger på saken, ikke på runden. Finnes ingen aktiv
     runde: `opprett_godkjenningsrunde(..., krev_godkjennbar=False)` —
     den lovlige `manuell → venter_godkjenning` med en `apen` runde som
     allerede finnes (011 linje 169 + 185). `krev_godkjennbar=False` er
     PR-012-s egen vei for handlinger som ikke skal re-evalueres av
     motoren; den krever ingen intensjon
  6. INSERT ... ON CONFLICT DO UPDATE i `overtakelse_attestasjon`.
     Målet skrives fra SAKEN, aldri fra kroppen; saksbindingstriggeren
     (§1) avviser avvik. `rolle` + `authz_version` fra steg 3 (§4.3).
     Historikk: `attestasjon_registrert`
  7. tell FERSKE attestasjoner med samme `utfall` (§4):
       avvis → terskel 1 · godkjenn → terskel 2, distinkte aktører
     under terskel → `venter_andre_godkjenner`, svar `gjenstaar`, ferdig
  8. terskel nådd: reautoriser HVER talt attestant (§4.3), så
     runden `apen → klar` og saken → `godkjenning_klar`
  9. `avgjor_domeneovertakelse(..., p_runde)` i SAMME transaksjon.
     `p_runde` er DNS-runden (§2.4) og kreves kun ved `godkjenn`;
     den er ikke godkjenningsrunden fra steg 5
 10. godkjenningsrunden `klar → brukt` med
     `decision_operation_id = 'domeneoppgjor-<unntak_id>-r<runde>'`
     (011 linje 318 krever en id; den er tekst, ikke en motoroperasjon),
     så saken → `løst` ved godkjenn, `avvist` ved avvis og ved
     `alt_avgjort` (§3.1). Historikk + `_fullfor` på idempotensnøkkelen
```

**To runder, to levetider — og de må ikke blandes.** `p_runde` er
DNS-observasjonsrunden fra §2.4 (minutter, `formal =
'overtakelsesoppgjor'`); godkjenningsrunden fra steg 5 er PR-012-s
saksrunde (`RUNDE_TTL = 24 timer`, `unntaksbehandling.py:35`).

**Attestasjonene henger på saken, ikke på saksrunden — og det er derfor
72-timersvinduet er nåbart.** `menneskelig_attestasjon` har `runde` i
nøkkelen; `overtakelse_attestasjon` har det med vilje ikke (§1). Ellers
ville en stemme avgitt time 0 vært verdiløs i time 25, uansett hva §4
sier, fordi saksrunden er utløpt — og porten «begge ferske innen 72 t»
hadde vært 24 t i praksis. Utløper saksrunden mellom to stemmer, åpner
steg 5 en ny (`utlopt` → tilbake til `manuell` → ny runde). Det er en
tilstandsbærer som fornyes, ikke evidens som mistes: en utløpt saksrunde
har aldri båret et domeneoppgjør, for oppgjøret skjer i steg 9 i samme
transaksjon som runden merkes `brukt`.

**Statusveien bruker kun whitelistede overganger** (011 linje 168–176):
`ny → manuell` (§3) → `venter_godkjenning` (steg 5, med runden på plass)
→ `venter_andre_godkjenner` (steg 7) → `godkjenning_klar` (steg 8) →
`løst`/`avvist` (steg 10), pluss
`venter_godkjenning`/`venter_andre_godkjenner → manuell` når saksrunden
må fornyes (steg 5). Alle står i whitelisten. Ingen ny
tilstand, ingen endring i `unntak_kolonnelaas` — 011 er checksum-låst og
skal ikke røres.

**Ingen rad i `godkjenningsutfall`.** Den tabellen krever
`hi_integritet_hash` og et `motorutfall` (011 linje 345–352); en
domenetildeling har ingen intensjonshash og ingen motorbeslutning. Å
skrive en syntetisk hash dit ville vært å påstå en beslutning motoren
aldri tok. Evidenskjeden for domeneoppgjøret er
`overtakelse_attestasjon` + `unntak_historikk` +
`domenekontroll_hendelse`, og den bærer mer enn `godkjenningsutfall`
kunne: målet, observatørene og begge stemmene.

**`menneskelig_attestasjon` brukes heller ikke.** Den er motorens
MAC-signerte konvolutt, bundet til intensjonshash og policyhash
(`unntaksbehandling.py:347`). Domenestemmen har sin egen tabell nettopp
fordi den må bindes til *sakens mål* (§1) — et krav ingen
PR-012-attestasjon har.

### 4.3 Hver talt stemme reautoriseres ved tildelingen

**Ferskhet er ikke autorisasjon.** Attestasjonsvinduet (§4) sier at
stemmen ble avgitt for under 72 timer siden; det sier ingenting om at
aktøren fortsatt *har lov*. Angrepet er konkret og ligger helt innenfor
vinduet: aktør 1 attesterer, mister så `domeneavgjorer` — eller
deaktiveres, eller får medlemskapet fjernet — og aktør 2 attesterer
dagen etter. Uten en sjekk teller aktør 1-s stemme fortsatt, og to
mennesker der bare det ene fortsatt er avgjører fullfører en cross-tenant
domenetildeling. Reautoriseringen i steg 3 (§4.2b) dekker bare den som
handler *nå*; den sier ingenting om den andre raden.

**Kontrakten er PR-013-s, og den er alt bevist i koden.** Aktivering av en
policy reautoriserer hver bundet godkjenner ved aktiveringen —
`_reautoriser_godkjennere()` (`policyadmin.py:646–676`), selv et resultat
av en Codex-runde. PR-015 bruker samme mønster på attestantene, i steg 8,
i **samme transaksjon** som `avgjor_domeneovertakelse()`:

```
for hver TALT attestasjon (aktor, rolle, authz_version), SORTERT på aktor:
    rad = laas_godkjenner(sakens tenant, aktor)     -- FOR UPDATE, 013 linje 195
    rad IS NULL                       -> `attestant_uautorisert:<aktor>:mangler_medlemskap`
    rad.authz_version <> authz_version-> `attestant_uautorisert:<aktor>:authz_endret`
    rolle NOT IN rad.roller           -> `attestant_uautorisert:<aktor>:rolle_borte`
    'domains:adjudicate' NOT IN scopes_for_roller(rad.roller)
                                      -> `attestant_uautorisert:<aktor>:scope_mangler`
```

- **`authz_version` er hele beviset.** `brukermedlemskap_authz_bump()`
  øker den ved *enhver* endring av raden — rolle fjernet, rolle lagt til,
  `aktiv` satt av. Et uendret versjonsnummer er derfor et positivt bevis
  på at autorisasjonen er den samme som da stemmen ble avgitt, ikke en
  liste over endringer vi husket å lete etter. Rolle- og scope-sjekkene
  under er defense-in-depth mot en rad som skulle blitt bumpet og ikke
  ble det.
- **Låsen, ikke bare lesningen.** `laas_godkjenner` er SECURITY DEFINER
  med `FOR UPDATE`, så en samtidig tilbakekalling ikke kan committe etter
  lesningen og vinne kappløpet mot oppgjøret. Uten låsen ville sjekken
  vært et øyeblikksbilde tildelingen straks kunne overleve. Funksjonen
  finnes fra før og har allerede `EXECUTE` for `disponit` (013 linje
  205–206) — samme rolle domeneruten kjører som. Ingen ny GRANT, ingen ny
  funksjon.
- **Deterministisk låserekkefølge.** Sortert på `aktor`, av nøyaktig
  samme grunn som PR-013 (`policyadmin.py:663–666`): to samtidige
  oppgjør som deler en attestant tar samme lås først og serialiseres i
  stedet for å vranglåse.
- **Scopeoversettelsen blir i Python.** `ROLLE_TIL_SCOPES` er
  applikasjonens lukkede kart; databasen kjenner bare rollenavn.
  Sjekken kjøres derfor av kalleren — men **under samme transaksjon og
  samme radlås** som `avgjor_domeneovertakelse()`, så den er ikke en
  preflight. Rekkefølgen er ufravikelig: lås attestantene, verifiser,
  *så* kall oppgjøret. Faller én sjekk, rulles hele transaksjonen
  tilbake: ingen domeneovergang, ingen sakslukking, og attestasjonene
  blir stående som evidens.
- **Fail-closed og legibelt.** Feilen navngir aktøren og grunnen (403),
  slik at flaten kan si «aktør X er ikke lenger avgjører — hen må
  attestere på nytt, eller tenanten må gi rollen til en annen», ikke
  «ukjent feil». Den utveien er den samme som §4.1 beskriver.
- **Gjelder også `avvis`.** Én stemme er også en stemme; en aktør som har
  mistet rollen skal ikke kunne lukke en domenesak.

## 5. Opplastingskapabilitet utstedes ved claim

- **Utstedes av `POST /v1/oppdrag/claim`** sammen med
  kvitteringskapabiliteten, som **separat token** — aldri utledet av den,
  aldri samme audience. Ikke noe nytt on-demand-endepunkt i v1.
- **Bindingen er serverkontekstens:** `tenant · oppdrag_id · modul_id ·
  release_id · kontraktversjon · kontrakt_hash · module_epoch ·
  artefakttype · owner_generation`. Modulen ber ikke om felt; den mottar
  et token.
- **`artefakttype` hentes fra `artefakttype_register` — ett token per
  registrert type.** Registeret har `artefakttype` som eneste
  unikhetskrav (016 linje 89); flere typer kan dele samme
  `(eiermodul, kontraktversjon, kontrakt_hash)`. Serveren kan derfor ikke
  velge én type, og modulen sender ingen ønsket type. Claim returnerer
  følgelig **kapabilitetslisten** for kontrakten: én kapabilitet per
  registrert type, deterministisk sortert på `artefakttype`. Registrerer
  kontrakten nøyaktig én type, er listen ett element — det vanlige
  tilfellet, uendret i praksis. Finnes **ingen** registrert artefakttype →
  **tom liste, ingen opplastingskapabilitet**, og claim lykkes fortsatt.
  En modul som ikke skal laste opp, får ikke lov.
- **Levetid = evidensfristen for oppdraget**, aldri lengre.
- **Epoch kontrolleres under oppdragslåsen** ved utstedelse.
- **Fencing mot reclaim krever `owner_generation` i bindingen.** Et
  reclaim øker `oppdrag.owner_generation` (`INT NOT NULL DEFAULT 0`, 005
  linje 317) uten å endre tenant, oppdrag, modul, release, kontrakt,
  epoch eller artefakttype — hvert eneste felt i 017-bindingen forblir
  altså gyldig for den gamle eieren, og `artefaktkapabilitet` har ingen
  generasjonskolonne. Den gamle eieren kunne derfor lastet opp med sitt
  token helt frem til evidensfristen, etter at en annen arbeider hadde
  overtatt oppdraget. 019 legger `owner_generation INT` og
  `owner_claim_id TEXT` på `artefaktkapabilitet` og stempler dem ved
  utstedelse under oppdragslåsen. Kolonnene er bindingsfelter —
  uforanderlige via `artefaktkapabilitet_statusmaskin()`, som derfor må
  erstattes i samme migrasjon (objekt 9). Dette er en **egen port**
  (24b), ikke bare en utvidet negativ test: uten kolonnen finnes ingen
  stale/fencing-garanti å teste.

- **Sjekken hører hjemme i `lagre_artefakt_staged()`, ikke bare i
  preflighten.** `innlos_artefaktkapabilitet()` er et rent oppslag; den
  brenner ingenting (017 linje 162–175). Opplastingsveien leser
  bindingen der, **krypterer så rapporten med tenant-DEK-en**, og kaller
  først deretter `lagre_artefakt_staged()`, som er den som validerer og
  forbruker kapabiliteten atomisk under `FOR UPDATE`
  (`platform/core/api/app.py:1928–1976`). Mellom de to kallene går det
  reell tid — koden håndterer alt eksplisitt at tilstanden endrer seg
  der (utløps-kappløpet i `InvalidParameterValue`-grenen). Et reclaim som
  committer i det vinduet ville passert en preflight-only-sjekk og så
  blitt konsumert. 019-versjonen av `lagre_artefakt_staged()`
  sammenligner derfor kapabilitetens `owner_generation` mot
  `oppdrag.owner_generation` **etter** at kapabilitetsraden er låst, og
  hever `invalid_parameter_value` ved avvik. Preflighten beholder samme
  sjekk — den gir det tidlige, billige avslaget — men den er ikke
  porten.
  **Unntaket er idempotensgrenen:** en kapabilitet som allerede er
  `brukt` returnerer sitt eksisterende `artefakt_id` uendret, også etter
  et reclaim. Der skrives ingen ny evidens, og et retry som mister svaret
  skal ikke straffes for at oppdraget siden skiftet eier.

- **Oppgraderingsveien er en del av migrasjonen, ikke en detalj.**
  `ALTER TABLE artefaktkapabilitet ADD COLUMN owner_generation INT NOT
  NULL` feiler umiddelbart på enhver base som alt har rader fra 017. Og
  å backfille med oppdragets *nåværende* generasjon ville vært verre enn
  å feile: tokens utstedt før et reclaim ville da fått den nye
  generasjonen stemplet på seg og blitt velsignet av nettopp den porten
  de skal stoppes av. Den opprinnelige generasjonen ble aldri lagret og
  kan ikke rekonstrueres. Sekvensen er derfor:
  1. `ADD COLUMN owner_generation INT` og `owner_claim_id TEXT`, **begge
     nullbare** — ingen default, ingen backfill.
  2. `UPDATE artefaktkapabilitet SET status = 'feilet' WHERE status =
     'utstedt'` — hvert levende, ubrukt token invalideres. `feilet` er
     terminal i statusmaskinen, så ingen av dem kan brukes igjen.
     Modulene claimer på nytt og får stemplede tokens. Fail-closed, og
     tapet er en kortlevd kapabilitet, ikke evidens.
  3. `ADD CONSTRAINT artefaktkapabilitet_generasjon_kjent CHECK
     (owner_generation IS NOT NULL OR status <> 'utstedt')`. Historiske
     `brukt`/`feilet`-rader beholder NULL som det de er — evidens fra før
     fencingen fantes — mens hver ny `utstedt` rad må bære generasjonen.
     Siden bindingsfelter er uforanderlige, arver en rad stemplet sitt
     hele veien til `brukt`.
  4. `utsted_artefaktkapabilitet()` skriver begge kolonnene under
     oppdragslåsen, i samme `SELECT` som verifiserer at oppdraget er
     `plukket`.

## 6. Ryddetimer

`disponit-artefaktrydding.timer`, hvert 15. minutt, kaller
`rydd_staged_artefakter(500)`. Timeren legger **ingen logikk oppå** den
positive regelen.

**Den positive regelen er 016-s, og den endres ikke.** Et artefakt er
kvalifisert når det er `staged`, eldre enn 24 timer, **og oppdraget det
tilhører ikke lenger tar imot evidens** (`NOT EXISTS (… oppdrag o WHERE
o.tenant = a.tenant AND o.id = a.oppdrag_id AND o.evidensfrist > now())`,
016 linje 860–868). `evidensfrist` er normalt mye senere enn 24 timer
(produksjon: 30 døgn), og oppdraget godtar signert evidens helt fram til
den: en kvittering som lander i vinduet mellom de to fristene registreres
som `sen_kvittering`, og `bevar_artefakt()` kan bare bevare en rad som
fortsatt er `staged` (016 linje 898). Ryddet vi på 24 t alene, ville en
gyldig sen kvittering pekt på en rapport hvis ciphertext alt var nullet —
evidensen ødelagt av oppryddingen som skulle beskytte den. 019 **arver
predikatet ordrett**; det eneste som legges til er `LIMIT`.
`bevart` og `karantene` er retained og røres uansett aldri, fordi
funksjonen kun treffer `tilstand = 'staged'`.

- **Batchgrensen ligger i funksjonen, ikke i timeren.** 016-versjonen
  (linje 856) gjør én ubegrenset `UPDATE` over alle kvalifiserte rader og
  returnerer kun antallet — og timeren er her uttrykkelig forbudt å legge
  logikk oppå. Med 600 kandidater ville første kjøring altså tatt alle 600
  i én transaksjon, stikk i strid med port 25 og med selve grunnen til at
  grensen finnes. 019 erstatter den med
  `rydd_staged_artefakter(p_maks INT DEFAULT 500)`, som velger radene med
  **samme WHERE-ledd som 016**, pluss
  `ORDER BY opprettet LIMIT p_maks FOR UPDATE SKIP LOCKED`, og oppdaterer
  kun dem. **Batchgrense 500 per kjøring**, så opphopning ikke låser
  tabellen i én transaksjon. Returverdien er fortsatt antallet ryddede
  rader, så «to sammenhengende feilede kjøringer → alarm» er uendret.
  `FOR UPDATE` beholder dessuten serialiseringen mot `bevar_artefakt()`,
  som låser samme rad før den bevarer (016 linje 892–897).
- **Karantenesatt evidens telles og rapporteres, aldri ryddes.**
- To sammenhengende feilede kjøringer → alarm. En stille ryddejobb er en
  voksende disk.

## 6b. Deploy-porten — rollene, credentialene og timerne

**En jobb som ikke er installert, kjører ikke; en rolle som ikke kan
logge inn, observerer ikke.** Fem Python-filer og én migrasjon er ikke
hele PR-015: `deploy/staging/oppsett-postgresql.sh` er repoets **eneste**
rollegrense — migrasjoner har uttrykkelig forbud mot å opprette
clusterroller (skriptets egen kommentar, linje 30–34) — og
`deploy/staging/opp.sh` har en **lukket** unit-liste (linje 51–54) som
både preflightes og enables. Uten endringer begge steder er §2 og §6
spesifikasjoner uten kjøretid.

**Observatørrollene må finnes FØR migrasjonen.** 019 gjør `GRANT EXECUTE
ON FUNCTION meld_domeneobservasjon(...) TO disponit_domeneobservator_1,
disponit_domeneobservator_2` — en `GRANT` til en rolle som ikke finnes er
en feil, ikke en advarsel, og ville stoppet første migrasjonskjøring på
en fersk installasjon. Rekkefølgen i skriptet er allerede riktig (roller
linje 39–51, `migrer.py` linje 221); det som mangler er rollene:

| Rolle | Type | Hvorfor |
|---|---|---|
| `disponit_domeneobservator_1` / `_2` | **LOGIN**, tilfeldig passord, egen DSN | Identiteten er `session_user` (§2.4). En NOLOGIN-rolle kan ikke være noens `session_user`, og uten DSN kan prosessen ikke autentisere |
| `disponit_domains_admin` | **LOGIN** + DSN (i dag NOLOGIN, linje 44–47) | Både revalideringsarbeideren (§2) og ryddetimeren (§6) kjører som den; 016 gir den EXECUTE (linje 927–933). Uten innlogging er de grantene uinnløselige |

- Begge går inn i LOGIN-løkken og gjennom `sikre_rolle_dsn` +
  `verifiser_og_reparer` (linje 99–123), som er skriptets beviste vei:
  passordrotasjon og miljøfil holdes i takt, og en halvskrevet DSN
  repareres før noen migrasjon kjøres.
- **Ikke medlemskap i stedet for innlogging.** Å la en eksisterende
  LOGIN-rolle arve `disponit_domains_admin` ville dratt med seg *arvede*
  rettigheter — og RLS-policyer med `TO`-klausul matcher på arvet
  medlemskap. Det er nøyaktig fellen skriptets `WITH INHERIT FALSE`-
  kommentar (linje 60–66) beskriver, gjeninnført av en GRANT som ser ut
  som en formalitet. Egen credential per prosess er også det §2.4 faktisk
  krever.
- **Observatørene får ikke mer enn det ene.** EXECUTE på
  `meld_domeneobservasjon`, ingenting annet: ikke
  `apne_domeneobservasjonsrunde`, ikke revalidering, ikke SELECT på
  `domenekontroll`. En kompromittert observatør skal kunne lyve om én
  observasjon, ikke lese domeneporteføljen.

**Unitene må inn i den lukkede lista.** Fire nye filer i
`deploy/staging/`, lagt til `UNITS` i `opp.sh` slik at `preflight_units`
verifiserer dem og `systemctl enable --now` starter dem:

| Unit | Kadens | Kjører som |
|---|---|---|
| `disponit-domenerevalidering.service` + `.timer` | hver time (§2) | `disponit_domains_admin` |
| `disponit-artefaktrydding.service` + `.timer` | hvert 15. min (§6) | `disponit_domains_admin` |
| `disponit-domeneobservator-1.service` / `-2.service` | kontinuerlig, poller åpne runder | hver sin `disponit_domeneobservator_*` |

- Observatørene er **to separate units med hver sin miljøfil**, ikke to
  tråder i én prosess. Delte de prosess, ville «to distinkte
  `session_user`» vært en formalitet: ett kompromiss gir da begge
  stemmene, og hele §2.4 hviler på at det ikke er tilfellet.
- **Diversitetsporten håndheves i prosessen, ikke av en kommentar.**
  Hver observatør leser sin resolver fra egen konfigurasjon og **nekter å
  starte** hvis den ser samme resolver-endepunkt som den andre, eller
  hvis den kjører med en DB-rolle som ikke er en
  `disponit_domeneobservator_*`. Sagt rett ut om staging: begge
  prosessene kjører på **samme vert** med ulike resolveroperatører. Det
  gir rolle- og resolverdiversitet, ikke vertsdiversitet — en navngitt
  restrisiko som lukkes i produksjon, ikke en port vi later som er
  bestått.
- Ryddetimeren erstatter ikke `disponit-rydd-pending.timer` (PR-009,
  PENDING-tokens). To ulike jobber, to ulike navn.

## 7. De fire portspørsmålene

| Kontroll | Alle veier inn? | Samtidighet? | Riktig vs. velformet? | Lukket format? |
|---|---|---|---|---|
| Domeneobservasjon (**verifisering OG revalidering**) | Begge inngangene krever `p_runde`; de gamle signaturene uten runde er REVOKE-et fra både `disponit_domains_admin` og `disponit`; én timer, én arbeidernøkkel; manuell kjøring tar samme lås | Advisory-lås; K som `LIMIT`; avledet plan; runden er engangs og kortlevd | Observasjonene skrives av observatørrollene selv (`session_user`), hashes i DB; arbeideren har ikke EXECUTE på `meld_domeneobservasjon` og setter aldri status | `formal` er lukket enum; kaller kun `apne_domeneobservasjonsrunde()` + `verifiser_/revalider_domenekontroll(…, p_runde)` |
| Overtakelsesavgjørelse | Kun domeneruten (§4.2) → **egen behandler** (§4.2b) → PR-012-runden → funksjonen; den generelle unntaksruten avviser familien, og saken settes `manuell` ved opprettelse så veien i det hele tatt finnes (§3); taperens sak har nøyaktig én lovlig handling (§3.1) | Hostname-lås; hele oppgjøret — inkludert reautoriseringen av hver talt attestant (§4.3) — i én transaksjon; én sak per konflikt­generasjon; ny konflikt = ny `unntak_id` | Målet bevist mot sakens egen idempotensnøkkel (§1); to distinkte aktører som **fortsatt** har `domains:adjudicate` ved tildelingen (§4.3; rollen finnes, §4.1, og døren slipper den gjennom, §4.2), identisk utfall, **begge ferske (72 t)**, og **fersk observasjonsrunde** ved positiv tildeling | Funksjonens enum; PK `(tenant, unntak_id, aktor)` hindrer dobbeltstemme; fornyelse endrer kun `avgitt_ts`/`utfall` + autorisasjonssnapshotet |
| Drift av det hele (§6b) | Rollene opprettes kun i `oppsett-postgresql.sh` (migrasjoner har forbud); unitene kun i `opp.sh`-s lukkede liste, som preflightes og enables | Roller før migrasjon; timerne tar hver sin advisory-lås; observatørene er separate prosesser | Innlogging per rolle og `is-active` per unit MÅLES på en fersk base (port 28), ikke antas | `UNITS` er en lukket liste; en observatør med feil rolle eller delt resolver nekter å starte |
| Opplastingskapabilitet | Kun `POST /v1/oppdrag/claim` | Epoch **og `owner_generation`** under oppdragslåsen ved utstedelse; generasjonen sjekkes på nytt under kapabilitetens radlås i `lagre_artefakt_staged()` | Bundet til serverkontekst, ikke modulens ønske | Ingen registrert artefakttype → tom liste, ingen kapabilitet |
| Rydding | Én timer, funksjonens positive regel — **inkludert 016-s evidensfristledd**, ikke bare 24 t | Batchgrense i funksjonen (`LIMIT p_maks`) + `FOR UPDATE` mot `bevar_artefakt()` + idempotens | Karantene og `bevart` bevares på tilstand, ikke på alder; sen evidens bevares på oppdragets frist | Kaller kun `rydd_staged_artefakter(500)` |

## 8. Codex-porter

**Domeneobservasjon (1–10).**
1 To samtidige kjøringer → én kjører, én venter ·
2 Uenige observatører (ulik `txt_hash` i samme runde) → ikke vellykket,
`siste_vellykkede_revalidering` urørt ·
2b **Runde med kun én observasjon, eller med en TXT-verdi som ikke hasher
til `challenge_token_hash` → exception, tidsstempel urørt** — også når
kalleren har `disponit_domains_admin`; og 3-argumentsversjonen er ikke
lenger kallbar for arbeiderrollen ·
2c **Arbeiderrollen forsøker å melde en observasjon selv** — direkte
`INSERT` i `domeneobservasjon` og kall til
`meld_domeneobservasjon()` → begge nektet på rettigheter; en
observatørrolle som melder inn kan **ikke** oppgi en annen identitet enn
sin egen (`observator` er `session_user`, ikke et argument) ·
2d **Runden er engangs og kortlevd:** samme `runde_id` brukt to ganger →
andre kall avvist; utløpt runde → avvist; runde åpnet for ett
`(tenant, hostname)` og brukt mot et annet → avvist ·
2e **Førstegangsverifisering krever samme bevis:**
`verifiser_domenekontroll` uten `p_runde`, med en runde med kun én
observasjon, med feil `txt_hash`, eller med en runde åpnet for
`formal = 'revalidering'` → **exception, ingen rad blir `verifisert`, og
ingen overtakelse utløses** — også når kalleren har
`disponit_domains_admin`. Testen må dekke både den ferske raden
(`ventende` → `verifisert`) og overtakelsesgrenen (fremmed eier →
A `tilbakekalt` + B `avklaring_kreves`): det er *den* veien som flytter en
annen tenants autorisasjon ·
2f **4-argumentsversjonen av `verifiser_domenekontroll` er ikke lenger
kallbar** for `disponit_domains_admin` eller `disponit` — REVOKE
verifisert med et direkte kall, ikke antatt ·
3 Tre døgn uten svar → attestasjon nektes; raden ikke slettet eller
`utlopt`-satt av arbeideren ·
4 Observatørkonfigurasjon uten diversitet (samme operatør, samme nett
eller samme DB-rolle) → oppstart nektes (deploy-port) ·
5 **Konstruert patologisk hashfordeling** (≥ 3·K rader i samme time) →
kø 2 + kø 3 overskrider aldri K; overskuddet blir etterslep og dreneres;
ingen rad tapt ·
6 Bootstrap, 500 rader verifisert i samme sekund → alle revalidert innen
et døgn, K aldri overskredet, faktisk fordeling **rapportert** ·
7 Seks timers outage → **outage-kohorten** monotont synkende mot null,
tom innen 24 t, K aldri overskredet av kø 2+3 ·
8 Restore fra backup → identisk plan (samme minutter) ·
9 Feilet forsøk → planen uendret; forsøk 2 og 3 på +4 t/+8 t; vellykket
forsøk 1 → slott 2 og 3 hopper over raden ·
10 Rad passerer 26 t → plukket i samme kjøring **selv når K er brukt
opp**; totalen overskrider K og telles i `sikkerhetsnett.kjoringer_over_K`.
10b Kø 1 med 200 rader → samtidighet aldri over C = 8, null rader droppet.
10c **Konfliktrader er ikke revalideringsarbeid:** en rad i
`avklaring_kreves` med `siste_vellykkede_revalidering` 30 timer gammel →
**ikke plukket av noen kø**, ingen runde åpnet, ingen revalideringsfeil
logget, og den teller ikke i `N`. Etter at `avgjor_domeneovertakelse`
godkjenner den, er den `verifisert` med fersk
`siste_vellykkede_revalidering` og går inn i kø 2 til sitt eget slott —
ikke i sikkerhetsnettet. Testen må måle begge sidene av overgangen; en
variant som bare teller kø 1 består selv med den permanente retry-lasten.

**Alarm (11).** 11 Bred resolverfeil → én driftsalarm, null M-37-saker, og
`tenant X / hostname Y` fortsatt individuelt synlig med tre døgn uten
suksess.

**M-37 og fire øyne (12–20g).**
12 Overtakelsessak synlig i PR-012-flaten med begge hostnames og lineage ·
12b **Saken er handterbar i det den er synlig:** en fersk overtakelsessak
står `manuell` (ikke `ny`), og første attestasjon åpner runden uten
`runde_ulovlig_tilstand`. Et retry av samme konflikt returnerer samme sak
**uten** å røre statusen — også når saken alt er `venter_godkjenning`
eller terminal ·
13 Avgjørelse uten `domains:adjudicate` → nektet, selv med alle tre
unntaksscopene (`exceptions:approve` + `:reject` + `:escalate`); og de tre
unntaksscopene på **den generelle** ruten mot en `domeneovertakelse`-sak
→ `feil_saksfamilie`, ingen runde åpnet, ingen attestasjon skrevet ·
13b **Døren, ikke bare kartet:** en browsersesjon med `domeneavgjorer`
når faktisk domeneruten (`domains:adjudicate` står i
`BROWSER_MUTASJONSSCOPES`), og CSRF-en håndheves der som i PR-012. En
sesjon uten CSRF-token nektes ·
14 Godkjenn med én attestasjon → nektet med `krever_to_attestasjoner` ·
15 Samme aktør to ganger → avvist av primærnøkkel, ikke av UI (fornyelse
er en oppdatering av samme rad, ikke en andre stemme) ·
16 To attestasjoner med ulikt `vinnende_tenant` eller ulik `unntak_id` →
ingen avgjørelse ·
16b **Attestasjon som peker på et ANNET mål enn sakens:** to aktører
attesterer samstemt et hostname/`vinnende_tenant`/generasjon som ikke er
det saken bærer i idempotensnøkkelen → **avvist ved INSERT**, ikke ved
telling; en `godkjenn` der `vinnende_tenant <> tenant` avvises likeså.
Gjensidig enighet flytter aldri en rad saken ikke handler om ·
17 C overtar med B-attestasjon inne → C-s konflikt får ny `unntak_id`,
B-attestasjonen teller ikke mot C-saken, raden bevart ·
18 Avvis med én attestasjon → B `tilbakekalt`; tenant med én autorisert
aktør får legibel feilkode som **navngir «gi en aktør til
`domains:adjudicate`»**, ikke stillhet og ikke «vent på 90 døgn» ·
18b **Ingen tidsbasert utvei finnes:** B i `avklaring_kreves`, klokka
skrudd forbi A-s gamle `utloper` → nytt verifiseringsforsøk fra B gir
fortsatt `avklaring_kreves` og hendelsen `verifisering_blokkert`; A
gjenoppstår ikke ·
19 Ny konflikt på hostname → **ny sak** (ny generasjon i
idempotensnøkkelen); retry av samme konflikt → samme sak, ny hendelse;
terminal sak urørt ·
20 A→B→C: A `tilbakekalt`, **B og C står begge i `avklaring_kreves`**
(018 rører ikke B når C kommer inn), A gjenoppstår ikke; godkjenning av
én part avviser de øvrige avklaringsradene i samme transaksjon; ≥3 parter
innen 24 t → `hoy_konfliktrate`.
20b **Den forbigåtte parten kan faktisk godkjennes:** B godkjennes mens
`hostname_binding` står på C → B blir `verifisert`, bindingen **flyttes**
til B, og C settes `tilbakekalt` med grunn `tapte_domeneoppgjor` i samme
transaksjon. 018-versjonen ville nektet B på «forbigått»; 019-versjonen
gjør bindingen til en konsekvens av avgjørelsen, ikke en forutsetning for
den ·
20c **Avvisning avgjør ikke tvisten:** `avvis` på B med C fortsatt i
avklaring → kun B blir `tilbakekalt`, C står urørt og saken dens er
fortsatt åpen ·
20d **Taperens sak kan faktisk lukkes — av rollen som skal lukke den:** B
godkjennes, C settes `tilbakekalt` med grunn `tapte_domeneoppgjor` —
deretter avgir C-s avgjører, som bærer **kun** `domeneavgjorer` og ingen
unntaksscopes, **én** `avvis`-attestasjon på C-s egen sak → attestasjonen
godtas av saksbindingstriggeren (C-radens generasjon er urørt),
`avgjor_domeneovertakelse` returnerer `alt_avgjort` uten å røre
domeneraden, og C-s sak ender `avvist`. **Ingen sak står igjen åpen på
hostnavnet etter oppgjøret.** En variant som bare sjekker domeneradene
består selv med en permanent uhandterbar kø og beviser derfor ingenting ·
20e **Foreldet attestasjon teller ikke:** aktør 1 attesterer, klokka
skrus 73 timer fram, aktør 2 attesterer → **ingen avgjørelse**,
feilkode `krever_to_attestasjoner`; aktør 1 fornyer, og først da går
godkjenningen gjennom. Fornyelsen skriver ny `attestasjon_registrert` i
`unntak_historikk`, og ingen bindingsfelter er endret ·
20f **Godkjenning krever fersk DNS-evidens:** B beviser kontroll, får to
ferske attestasjoner, men TXT-posten er fjernet → runden med
`formal = 'overtakelsesoppgjor'` får aldri to like observasjoner, og
`avgjor_domeneovertakelse` **nekter**. B forblir `avklaring_kreves`,
ingen ny 90-døgnsautorisasjon utstedt ·
20g **Rollen finnes og virker:** en bruker med `domeneavgjorer` i
`brukermedlemskap.roller` får `domains:adjudicate`; `godkjenner`,
`admin`, `sikkerhet`, `leser` og `policyforvalter` får det **ikke**, og
en bruker med `exceptions:approve` alene nektes å attestere. To brukere
med `domeneavgjorer` — og **ingen andre scopes** — fullfører en positiv
tildeling ende-til-ende **over HTTP i browsersesjon**, fra sak i `manuell`
til `verifisert` domenerad og lukkede saker. Testen må gå gjennom
endepunktet; en variant som kaller `behandle_unntakshandling` direkte
hopper over både `BROWSER_MUTASJONSSCOPES` og familiegjerdet og beviser
derfor ingenting om veien inn.
20h **Attestasjonen flytter faktisk domenet:** etter den andre
godkjenn-attestasjonen er B-radens status `verifisert`, `hostname_binding`
står på B, generasjonen er økt og et nytt 90-døgnsvindu er satt — målt på
**domenekontroll-raden**, ikke på saksstatusen. Saken skal samtidig ende
`løst`, aldri `venter_utførelse`. En variant som bare sjekker at saken
lukket seg ville bestått med `behandle_unntakshandling`, som aldri kaller
`avgjor_domeneovertakelse()` og lar domeneraden stå urørt ·
20i **Attestasjonen dør med rollen:** aktør 1 attesterer, tenanten
fjerner `domeneavgjorer` fra aktør 1 (eller setter medlemskapet
inaktivt), aktør 2 attesterer innenfor 72-timersvinduet → **ingen
tildeling**, 403 `attestant_uautorisert` som navngir aktør 1, og B står
fortsatt `avklaring_kreves` med domeneraden urørt. Gis rollen tilbake, må
aktør 1 attestere på nytt (ny `authz_version`) før oppgjøret går. Samme
port for `avvis` med én stemme. Testen må endre medlemskapet **etter**
første stemme; en variant som bare sjekker scopet ved døren beviser
ingenting om den andre raden ·
20j **72-timersvinduet er faktisk 72 timer:** aktør 1 attesterer, klokka
skrus 25 timer fram (saksrunden er da utløpt, attestasjonen fortsatt
fersk), aktør 2 attesterer → **tildelingen går gjennom**; en ny
saksrunde er åpnet, den gamle står `utlopt`, og begge attestasjonene er
uendret. En implementasjon som binder domenestemmen til saksrunden
består 20e og feiler her.

**Kapabilitet (21–24b).**
21 Claim returnerer distinkte tokens; opplastingstokenet virker ikke
som kvittering og motsatt ·
22 Oppdrag uten registrert artefakttype → claim OK, tom
kapabilitetsliste ·
22b **Kontrakt med to registrerte artefakttyper → to kapabiliteter,
deterministisk sortert, hver bundet til sin type; ingen av dem kan brukes
til å laste opp den andre typen** ·
23 Levetid > evidensfrist → utstedelse avvist ·
24 Epoch endret mellom claim og utstedelse → ingen kapabilitet ·
24b **Reclaim-fencing: A claimer og får token, B reclaimer (samme tenant,
oppdrag, modul, release, kontrakt, epoch og artefakttype — kun
`owner_generation` er økt), A forsøker opplasting innen evidensfristen →
avvist på generasjon.** Egen port, ikke bare en utvidet negativ test.
24c **Reclaim i vinduet mellom preflight og forbruk:** A passerer
`innlos_artefaktkapabilitet`, reclaimet committer mens rapporten
krypteres, A kaller `lagre_artefakt_staged` → **avvist der**, ingen
artefaktrad, kapabiliteten ikke brent. Testen må committe reclaimet
mellom de to kallene; en variant som bare sjekker preflighten består selv
med en usikret forbruker og beviser derfor ingenting ·
24d **Idempotensen overlever et reclaim:** A laster opp, kapabiliteten
blir `brukt`, B reclaimer, A retryer samme kanoniske dokument → samme
`artefakt_id` returneres, ingen ny rad, ingen exception ·
24e **Oppgraderingsveien:** base med både `utstedt`- og `brukt`-rader fra
017 → migrasjonen går gjennom, hver `utstedt` rad ender `feilet`,
historiske `brukt`-rader beholder `owner_generation IS NULL`, og et token
utstedt før migrasjonen kan ikke brukes etterpå.

**Rydding (25–27).**
25 600 kandidater → to batcher à 500 og 100 **fordi funksjonen har
`LIMIT`**, idempotent, ingen låsing over grense ·
25b **Evidensfristen overlever batchgrensen:** artefakt `staged` i 48
timer, oppdragets `evidensfrist` fortsatt fram i tid → **ikke ryddet**,
ciphertext intakt; en sen, signert kvittering som lander etterpå kan
fortsatt `bevar_artefakt()`. Samme rad etter at `evidensfrist` er passert
→ ryddet. Testen må konstruere begge sidene av fristen; en variant som
bare teller batchstørrelsen ville bestått selv om 019 mistet
evidensfristleddet ·
26 Karantenesatt artefakt eldre enn 24 t → bevart, telt i
`karantene_bevart`; det samme for `bevart` ·
27 To feilede ryddekjøringer → alarm.

**Deploy (28).**
28 **Fersk installasjon fra skriptene alene** (§6b): `oppsett-postgresql.sh`
kjøres på en tom base → migrasjon 019 går gjennom (`GRANT` til
observatørrollene feiler ikke), begge observatørrollene og
`disponit_domains_admin` kan **logge inn med DSN-en fra miljøfilen**, og
`opp.sh` installerer og starter begge timerne og begge
observatørprosessene. Målt som `systemctl is-active` per unit og én
vellykket innlogging per rolle. En observatør startet med feil DB-rolle,
eller med samme resolver-endepunkt som den andre, **skal nekte å
starte**. Uten denne porten kan hele PR-015 bestå testsuiten og likevel
ikke revalidere ett eneste domene i drift.

**Alle tester konstruerer egen tilstand.** Ingen delt fixture.

## 9. Evidensgrense `operativt-lag-v1` (defineres FØR arbeidet)

**Håndhevede grenser (invarianter):**
`budsjett.ko2_pluss_ko3_over_K = 0` (også på patologisk populasjon) ·
`sikkerhetsnett.utsatt = 0` · `sikkerhetsnett.rad_over_26t_uplukket = 0` ·
`plan.uendret_etter_restore = ja` · `plan.forskjovet_av_retry = 0` ·
**`recovery.outage_kohort_igjen_etter_24t = 0`** og
**`recovery.outage_kohort_monotont_synkende = ja`** (målt på den
identifiserte 6-timers-kohorten, ikke på global kø 3 — nytt etterslep fra
en senere skjev time er legitimt og teller ikke som recovery-feil) ·
`dobbeltkjoring = 0` · `status_satt_av_arbeider = 0` ·
`uenige_observatorer_avvist = alle` ·
**`revalidering_uten_gyldig_txt_hash = 0`** ·
**`revalidering_med_under_2_observatorer = 0`** ·
**`observasjon_meldt_av_arbeiderrollen = 0`** ·
**`observator_oppgitt_som_argument = 0`** ·
**`runde_gjenbrukt_eller_utlopt_godtatt = 0`** ·
**`runde_brukt_pa_feil_formal = 0`** ·
**`revalider_3arg_kallbar_for_arbeider = nei`** ·
**`verifiser_4arg_kallbar = nei`** ·
**`verifisering_uten_2_observatorer = 0`** ·
**`overtakelse_utlost_uten_observasjonsrunde = 0`** ·
`godkjenn_med_en_attestasjon = 0` · `samme_aktor_to_stemmer = 0` ·
`attestasjon_pa_annen_sak_talt = 0` ·
**`attestasjon_med_mal_utenfor_saken = 0`** ·
**`foreldet_attestasjon_talt = 0`** (vindu 72 t) ·
**`fornyelse_endret_bindingsfelt = 0`** ·
**`godkjenning_uten_fersk_observasjonsrunde = 0`** ·
`saker_per_konfliktgenerasjon ≤ 1` · `kjede_abc.a_gjenoppstatt = 0` ·
**`forbigatt_part_kan_godkjennes = ja`** ·
**`taperrader_igjen_i_avklaring_etter_godkjenn = 0`** ·
**`apne_saker_pa_hostname_etter_oppgjor = 0`** ·
**`taperrad_generasjon_bumpet_ved_oppgjor = 0`** ·
**`tidsbasert_utvei_fra_avklaring = 0`** ·
`avgjorelse_uten_scope_nektet = alle` ·
**`roller_med_domains_adjudicate = {domeneavgjorer}`** (nøyaktig, ikke
«minst») ·
**`domenesak_behandlet_pa_generell_unntaksrute = 0`** ·
**`domenerad_uendret_etter_fullfort_godkjenning = 0`** ·
**`domenesak_endt_i_venter_utforelse = 0`** ·
**`oppgjor_med_uautorisert_attestant = 0`** ·
**`attestasjon_uten_authz_snapshot = 0`** ·
**`attestasjon_ende_til_ende_kun_med_domeneavgjorer = ja`** (over HTTP,
browsersesjon, ikke direkte funksjonskall) ·
**`overtakelsessak_opprettet_i_status_ny = 0`** ·
**`retry_endret_status_pa_eksisterende_sak = 0`** ·
**`avklaringsrad_plukket_av_revalidering = 0`** ·
`tokens_distinkte = ja` · `uten_artefakttype_utstedt = 0` ·
**`kapabilitet_per_registrert_type = alle`** ·
**`opplasting_med_foreldet_owner_generation = 0`** (målt på
`lagre_artefakt_staged`, med reclaimet committet ETTER preflighten) ·
**`utstedt_kapabilitet_uten_generasjon_etter_019 = 0`** ·
**`migrasjon_019_feiler_pa_eksisterende_rader = nei`** ·
`levetid_over_frist_avvist = alle` ·
**`ryddebatch_over_p_maks = 0`** ·
**`ryddet_for_evidensfrist = 0`** ·
`karantene_bevart = alle` · `idempotens_kjoring2_slettet = 0` ·
**`observatorrolle_uten_login_eller_dsn = 0`** ·
**`nye_timere_installert_og_aktive = alle`** ·
**`migrasjon_019_pa_fersk_base_feiler = nei`** ·
**`observator_startet_med_delt_resolver = 0`**.

**Målte egenskaper (rapporteres, ingen bestått/ikke bestått):**
`fordeling.maks_andel_per_time` for testpopulasjonen ·
`sikkerhetsnett.kjoringer_over_K` · `dreneringstid_timer` ved 6 t outage ·
`alarm.terskel_utlost`.

Et sjekklistepunkt uten definert, målbar grense regnes som `nei`.

---

```
NÅ:    Implementer PR-015 mot dette klarsignalet — migrasjon 019,
       revalideringsarbeider + observatører, M-37-kobling,
       kapabilitetsutstedelse ved claim, ryddetimere — Claude Code
       — platform/core/db/migrations/019_operativt_lag.sql
         (overtakelse_attestasjon m/saksbinding og fornyelse +
          domeneobservasjonsrunde og domeneobservasjon +
          apne_domeneobservasjonsrunde + meld_domeneobservasjon +
          verifiser_domenekontroll(p_runde) +
          revalider_domenekontroll(p_runde) + avgjor_domeneovertakelse
          m/flerpartsoppgjør, friskhetskrav og taperoppgjør +
          rydd_staged_artefakter(p_maks) MED 016-s evidensfristledd +
          artefaktkapabilitet.owner_generation m/oppgraderingssekvens og
          validering i lagre_artefakt_staged),
         platform/core/api/autorisasjon.py (rollen `domeneavgjorer`, §4.1),
         platform/drift/domenerevalidering.py, platform/drift/domeneobservator.py,
         platform/drift/artefaktrydding.py,
         platform/core/api/domeneovertakelse.py (saken opprettes `manuell`
           med grunnkode og policyref, §3; behandleren
           `behandle_domeneattestasjon()` med domeneoppgjøret og
           reautoriseringen av hver talt attestant, §4.2b + §4.3),
         platform/core/api/unntaksbehandling.py (familiegjerdet på den
         generelle ruten + scopet fra saksfamilien under låsen, §4.2),
         platform/core/api/app.py (oppdrag_claim, linje ~717;
         artefaktopplasting, linje ~1928; `domains:adjudicate` i
         BROWSER_MUTASJONSSCOPES, linje ~799; ruten
         POST /v1/domener/overtakelse/{unntak_id}/attestasjon, §4.2),
         deploy/staging/oppsett-postgresql.sh (observatørrollene som LOGIN
           m/DSN + `disponit_domains_admin` som LOGIN m/DSN, §6b — uten
           dem feiler 019 på en fersk base og ingen jobb kan autentisere),
         deploy/staging/opp.sh (UNITS utvides med de fire nye unitene, §6b),
         deploy/staging/disponit-domenerevalidering.{service,timer},
         deploy/staging/disponit-artefaktrydding.{service,timer},
         deploy/staging/disponit-domeneobservator-{1,2}.service,
         docs/RUTINER.md (hvordan eier tildeler `domeneavgjorer`)
NESTE: Draft PR-014c (automatisk WCAG-kontroll) — første eiermodul på
       plattformen 014a/014b/015 bygde — Claude.ai
       — docs/pr/PR-014c-*.md
```
