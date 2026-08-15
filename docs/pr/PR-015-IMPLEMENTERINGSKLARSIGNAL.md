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
| 2 | `domeneobservasjonsrunde` (m/ `challenge_token_hash`, unikindeks på levende runde, køindeks) + `domeneobservasjon` (nye tabeller) | Observasjonen må bæres av databasen, ikke av kalleren, §2.4; runden må være bundet til challengeVERSJONEN, §2.4a; én levende runde per mål og formål, §2.4 |
| 2b | `rydd_domeneobservasjonsrunder(p_maks INT)` (ny) | En runde som bare utløper har ingen avslutning, og køtabellen vokser uten grense, §2.4bb |
| 3 | `apne_domeneobservasjonsrunde(tenant, hostname, formal)` (ny) | Runden er engangs, kortlevd, idempotent under sonelåsen og bundet til én rad og én challenge, §2.4/§2.4a |
| 4 | `meld_domeneobservasjon(runde, observert_txt)` (ny) | Observatøridentiteten er `session_user`, aldri en parameter, §2.4 |
| 4b | `hent_apne_observasjonsrunder()` (ny, avgrenset lesekø) | Observatøren må kunne *finne* runden den skal svare på. Uten den kan prosessen autentisere, men aldri oppdage et `runde_id`, §2.4b |
| 5 | `verifiser_domenekontroll(…, p_runde UUID)` | **Førstegangsverifisering er den farligste veien** — den kan opprette en autorisasjon og utløse en overtakelse. Uten runde er den beviskravsfri, §2.5 |
| 6 | `revalider_domenekontroll(…, p_runde UUID)` | Arbeideren skal ikke være autoritet, §2.4 |
| 6b | `hent_revalideringskandidater(p_grense INT)` (ny) | `domenekontroll` har FORCE RLS med tenant-policy; et kolonnegrant gir den globale scheduleren null rader, og domenene mister ferskhet uten at én alarm går, §2.2b |
| 7 | `rydd_staged_artefakter(p_maks INT)` | Batchgrense uten å miste evidensfristpredikatet, §6 / port 25 |
| 8 | `artefaktkapabilitet.owner_generation` + `.owner_claim_id`, med oppgraderingssekvens | Fencing ved reclaim, §5 |
| 9 | `artefaktkapabilitet_statusmaskin()` + `utsted_artefaktkapabilitet()` + `innlos_artefaktkapabilitet()` + `lagre_artefakt_staged()` | Generasjonen må stemples ved utstedelse og valideres i den ATOMISKE forbrukeren, §5 |
| 10 | `avgjor_domeneovertakelse(…, p_runde UUID)` — flerpartsoppgjør, friskhetskrav og taperoppgjør | Én godkjenning må avvise de øvrige avklaringsradene i samme transaksjon (§3), telle kun ferske attestasjoner mot fersk DNS-evidens (§4), og la taperens sak kunne lukkes (§3.1) |
| 11 | `forelder_hostname()` + `sone_overlapp()` (nye) + sonelåsen og overlappsgrenen i `verifiser_domenekontroll` / `avgjor_domeneovertakelse` | Wildcard-scopen dekker ett nivå mer enn hostnavnet, men 016/018 gjerder kun det litterale navnet: `example.com` (wildcard, tenant A) og `foo.example.com` (tenant B) kan i dag begge stå `verifisert` samtidig, §2.5b |
| 12 | `domenekonfliktpart` (ny tabell) | En wildcard-verifisering kan overlappe FLERE innehavere samtidig; `konflikt_motpart` er én kolonne, og oppgjøret må kunne løse hele mengden atomisk, §2.5b |
| 13 | Oppgraderingsryddingen av eksisterende overlapp, FØR gjerdet installeres | Gjerdet er fremoverrettet: rader som alt er `verifisert` i overlapp forblir doble til noen rydder dem, §2.5b |
| 14 | `utsted_challenge()` (`CREATE OR REPLACE`, ACL bevares) | En reissue må forkaste åpne runder på målet, ellers kan evidens for en invalidert challenge forbrukes etterpå, §2.4a |

**Seks ting hører ikke hjemme i 019, men i Python** — autorisasjonen og
saksflyten ligger ikke i databasen, og en port som bare finnes i SQL er
ikke nådd:

| # | Objekt i Python | Hvor | Hvorfor |
|---|---|---|---|
| A | Rollene `domeneavgjorer` og `domeneforvalter` i `ROLLE_TIL_SCOPES` | `api/autorisasjon.py:17` | Ingen eksisterende rolle bærer `domains:adjudicate` (§4.1) eller `domains:verify` (§2.5c) |
| B | `domains:adjudicate` + `domains:verify` i `BROWSER_MUTASJONSSCOPES` | `api/app.py:799` | Uten dem nektes attestanten og domeneforvalteren før ruten, §4.2/§2.5c |
| C | Ruten `POST /v1/domener/overtakelse/{unntak_id}/attestasjon` + **behandleren `behandle_domeneattestasjon()`** + familiegjerdet på PR-012-ruten | `api/app.py`, `api/domeneovertakelse.py`, `api/unntaksbehandling.py` | PR-012-s behandler kan ikke avgjøre en domenesak (§4.2b), og scopet slås opp fra handlingen, ikke fra saksfamilien, §4.2 |
| D | `ny → manuell` i `opprett_overtakelsessak()` | `api/domeneovertakelse.py` | Saken er ellers synlig, men ikke handterbar, §3 |
| E | **Verifiseringsflaten**: `POST /v1/domener`, `POST /v1/domener/{hostname}/verifisering`, `GET /v1/domener[/{hostname}]` | `api/app.py`, `api/domenekontroll.py` | Etter §2.4c er `disponit` eneste kaller av `verifiser_domenekontroll`, og ingen rute kaller den — uten dette kan intet domene bli autorisert, §2.5c |
| F | `POST /v1/domener/overtakelse/{unntak_id}/runde` | `api/domeneovertakelse.py` | Attestanten må få `dns_runde_id` fra et sted; oppgjørsrunden åpnes ikke av seg selv, §2.5c/§4.2b |

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

-- RLS + FORCE hører i DDL-en, ikke bare i prosaen nederst i denne
-- seksjonen: runtime har bordgrant på tabellen (§6c), så uten policy leser
-- og skriver den delte `disponit`-rollen stemmer i alle tenanter.
ALTER TABLE overtakelse_attestasjon ENABLE ROW LEVEL SECURITY;
ALTER TABLE overtakelse_attestasjon FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON overtakelse_attestasjon
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
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

`disponit-domenerevalidering.timer`, hver time, egen Unix-bruker, egen
least-privilege-rolle `disponit_domenerevalidator` (§6b).
**Arbeideren er en scheduler, ikke en kilde:**
den bestemmer *hvilke* rader som skal revalideres når, åpner en runde og
ber til slutt om avgjørelsen — men den slår ikke opp DNS selv og kan
ikke melde inn en observasjon (§2.4). Kandidatene henter den gjennom
`hent_revalideringskandidater()`, ikke ved å lese `domenekontroll`: den
er en global scheduler mot en tenant-isolert tabell, og §2.2b er grunnen
til at det ikke kan løses med et grant. Selve oppslaget gjøres av separate
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

### 2.2b Køen må kunne LESES — et kolonnegrant gir null rader

**Scheduleren er global, `domenekontroll` er tenant-isolert, og RLS bryr
seg ikke om at grantet er der.** 016 slår på `ENABLE` *og* `FORCE ROW LEVEL
SECURITY` på tabellen (linje 353–354), og den eneste policyen filtrerer på
`current_setting('disponit.tenant')` (linje 361–363). Et kolonne-SELECT til
`disponit_domenerevalidator` — som et tidligere utkast her ga — gir derfor
nøyaktig to utfall, og begge er feil: uten satt kontekst ser arbeideren
**ingen** rader, med satt kontekst ser den **én** tenant. Rollen har verken
`BYPASSRLS` eller noen vei til å oppdage tenantlista den måtte løpe gjennom
(den har ikke SELECT på `tenant`-registeret, og skal ikke ha det).
Konsekvensen er stille: timeren kjører grønt hver time, `N` blir 0, ingen
runde åpnes, ingen feil skrives — og hvert eneste domene mister ferskheten
sin etter 72 timer uten at én alarm går. Et grant som ikke kan brukes er
verre enn ingen grant, fordi det ser ut som tilgangen finnes.

Køen leveres derfor som **én smal SECURITY DEFINER-funksjon**, eid av
`disponit_domene_eier` (som *har* BYPASSRLS, 016 §2) — samme mønster som
observatørkøen i §2.4b, av samme grunn:

```sql
CREATE FUNCTION hent_revalideringskandidater(p_grense INT DEFAULT 5000)
RETURNS TABLE (tenant TEXT, hostname TEXT, wildcard BOOLEAN,
               autorisasjonsgenerasjon BIGINT, verifisert_ts TIMESTAMPTZ,
               siste_vellykkede_revalidering TIMESTAMPTZ, utloper TIMESTAMPTZ,
               n_verifisert BIGINT)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT d.tenant, d.hostname, d.wildcard, d.autorisasjonsgenerasjon,
           d.verifisert_ts, d.siste_vellykkede_revalidering, d.utloper,
           -- Vindusfunksjonen regnes over HELE WHERE-mengden, før LIMIT:
           -- `N` er populasjonen, ikke antall rader kallet rakk å hente.
           -- Ellers ville K krympet med kappingen og køen aldri drenert.
           count(*) OVER ()
      FROM public.domenekontroll d
     WHERE d.status = 'verifisert'                       -- §2.1, ordrett
     ORDER BY d.siste_vellykkede_revalidering ASC NULLS FIRST, d.hostname
     LIMIT p_grense $$;
```

- **Kolonnene er de samme som kolonnegrantet ville gitt** — og ikke én mer.
  `challenge_token_hash` og `konflikt_motpart` er ikke arbeiderens (samme
  grense som egressens grant, 016 linje 386). Funksjonen er `STABLE`, har
  ingen skrivevei og kan ikke velge en annen status: `verifisert` står i
  kroppen, ikke i et argument, så en kompromittert arbeider kan ikke be om
  konfliktradene den er utestengt fra i §2.1.
- **`status = 'verifisert'` er hardkodet, ikke en parameter.** Det er
  samme populasjon som `N` og som alle tre køene — én definisjon, ett sted.
- **Sorteringen gir sikkerhetsnettet forrang uansett kapping.** Eldste
  vellykkede revalidering først, `NULL` (aldri revalidert) aller først, så
  `p_grense` kan aldri sulte kø 1. `hostname` som andrenøkkel gjør
  rekkefølgen deterministisk mellom to kjøringer.
- **`p_grense` er en minnegrense, ikke K.** K regnes fortsatt av
  `n_verifisert` (§2.2) og håndheves med `LIMIT` i arbeiderens egen plan.
  Blir populasjonen større enn `p_grense`, er det en **målt** hendelse med
  samme form som overskridelse av kø 1: arbeideren logger at kandidatlista
  ble kappet, og port 10 måler at det ikke skjer stille.
- **Ingen ny credential, ingen BYPASSRLS på jobbrollen.** Alternativet —
  å gi `disponit_domenerevalidator` `BYPASSRLS` — ville gitt en
  nettverksautentiserbar rolle full kryss-tenant lesetilgang til alt den
  ellers har grant på, for å løse ett leseproblem. En funksjon som
  returnerer syv kolonner for én status er den minste flaten som virker.

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
`siste_vellykkede_revalidering = now()` på enhver verifisert rad. Den
signaturen er grantet til `disponit_domains_admin` (016 linje 929), altså
rollen et 014b-oppsett ville gitt arbeideren: en feilende eller
kompromittert arbeider kunne friske opp et hvilket som helst verifisert
domene uten å ha slått opp noe.

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
  challenge_token_hash TEXT NOT NULL,     -- challengen runden gjelder, §2.4a
  apnet TIMESTAMPTZ NOT NULL DEFAULT now(),
  utloper TIMESTAMPTZ NOT NULL,           -- kort, f.eks. now() + 5 min
  status TEXT NOT NULL DEFAULT 'apen' CHECK (status IN ('apen','brukt','forkastet')));

-- Én LEVENDE runde per mål og formål. Uten den kan to samtidige kallere åpne
-- hver sin `apen` runde på samme (tenant, hostname, formal), hver samle sine
-- to observasjoner og hver bli forbrukt: samtidige API-retries ville da
-- verifisert eller fornyet det samme domenet flere ganger, og en kaller som
-- kan åpne runder kunne fylle begge observatørkøene med identisk arbeid.
CREATE UNIQUE INDEX en_apen_runde_per_mal
    ON domeneobservasjonsrunde (tenant, hostname, formal) WHERE status = 'apen';

-- Køindeks: nøyaktig predikatet og sorteringen `hent_apne_observasjonsrunder()`
-- kjører, og begge observatørprosessene kjører den kontinuerlig (§2.4b).
CREATE INDEX domeneobservasjonsrunde_ko
    ON domeneobservasjonsrunde (apnet) WHERE status = 'apen';

CREATE TABLE domeneobservasjon (
  -- ON DELETE CASCADE er ikke bekvemmelighet: §2.4bb sletter terminale runder
  -- etter 30 døgn, og hver RUKKET runde har barnerader her. Med default
  -- NO ACTION ville nettopp de slettene feilet på fremmednøkkelen, og
  -- retensjonen bare virket på runder ingen observatør svarte på.
  runde_id UUID NOT NULL REFERENCES domeneobservasjonsrunde (runde_id)
                         ON DELETE CASCADE,
  observator TEXT NOT NULL,               -- session_user, satt av funksjonen
  txt_hash TEXT NOT NULL,                 -- sha256, beregnet I databasen
  observert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (runde_id, observator));    -- én observatør, én observasjon
```

`formal` er en lukket enum og **ikke** dekorasjon: en runde åpnet for å
friske opp et verifisert domene skal ikke kunne brukes til å opprette en
ny autorisasjon eller til å avgjøre en tvist. Hver konsument krever sitt
eget formål, og runden er uansett engangs.

**Åpningen er idempotent under hostname-låsen.** `apne_domeneobservasjonsrunde()`
tar sonelåsen (§2.5b) først, setter så hver utløpt `apen` runde på målet til
`forkastet`, og returnerer deretter `runde_id` for en runde som fortsatt lever
i stedet for å opprette en ny. Unikindeksen er beltet: en kaller som forsøker
å omgå funksjonen kan ikke få to `apen` rader på samme mål og formål.
Idempotensen er ikke bare hygiene — den er det som gjør at et API-retry (§2.5c)
poller den *samme* runden i stedet for å åpne en ny hver gang klienten spør.

| Funksjon | Kalles av | Håndhever |
|---|---|---|
| `apne_domeneobservasjonsrunde(tenant, hostname, formal)` → `runde_id` | revalideringsarbeideren (`disponit_domenerevalidator`) eller API-et (`disponit`) | Raden må finnes og stå i den statusen formålet krever (`ventende`/`utlopt` for `verifisering`, `verifisert` for `revalidering`, `avklaring_kreves` for `overtakelsesoppgjor`); `challenge_token_hash` må være satt, **kopieres til runden** (§2.4a) og challengen ikke utløpt (unntatt `revalidering`, §2.5); kort TTL; åpningen er idempotent under sonelåsen; runden er engangs |
| `hent_apne_observasjonsrunder()` → `SETOF (runde_id, hostname, formal)` | **observatørrollene** `disponit_domeneobservator_*` | Kun `apen`, ikke utløpt, og kun runder kalleren selv ikke alt har meldt inn i; `ORDER BY apnet LIMIT 50`; **ingen `tenant` i utdata** (§2.4b) |
| `meld_domeneobservasjon(runde_id, observert_txt)` | **observatørrollene** `disponit_domeneobservator_*` | `observator := session_user`; hashen beregnes i DB og må være lik **rundens** `challenge_token_hash` (§2.4a); runden må være `apen` og ikke utløpt |
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

### 2.4a Runden er bundet til challengeVERSJONEN, ikke bare til raden

**En runde som bare kjenner `(tenant, hostname, formal)` kan forbrukes med
evidens for en challenge som ikke gjelder lenger.** Kappløpet er konkret:
begge observatørene slår opp og melder inn H1; `utsted_challenge()` skriver
så en ny challenge H2 på raden — reissue er en helt lovlig operasjon, og med
§2.5c er den dessuten nåbar fra API-et; deretter tar
`verifiser_domenekontroll()` hostname-låsen og finner en fortsatt `apen`
runde med to enige observasjoner. Sammenlignet den bare mot radens
*nåværende* hash, ville H1-observasjonene ikke telt — men sammenlignet den
mot ingenting, ville de telt, og en invalidert challenge hadde opprettet
eller flyttet en autorisasjon. Derfor:

- `apne_domeneobservasjonsrunde()` **kopierer** radens
  `challenge_token_hash` inn i runden (`NOT NULL`) under sonelåsen.
- `meld_domeneobservasjon()` sammenligner mot **rundens** hash, ikke radens.
  Observatøren svarer på den challengen runden ble åpnet for.
- Hver konsument (`verifiser_`, `revalider_`, `avgjor_`) sammenligner
  rundens `challenge_token_hash` mot radens **under samme radlås som
  statusovergangen**, og avviser med `challenge_endret` ved avvik. Bindingen
  kontrolleres altså atomisk med overgangen, ikke før den.
- `utsted_challenge()` er reissue: 019-versjonen setter hver `apen` runde på
  målet til `forkastet` i samme transaksjon. Det er den positive halvdelen —
  gammel evidens blir aldri stående og vente på en konsument.

**Utløpt challenge er en port ved førstegangsverifisering, ikke ved
oppfriskning.** `utsted_challenge()` setter `challenge_utloper` til sju
døgn, mens en verifisert autorisasjon lever i 90 og revalideres daglig.
Krevde `formal = 'revalidering'` en ikke-utløpt challenge, ville hvert
eneste domene mistet evnen til å åpne revalideringsrunden sin på dag åtte
og blitt ugyldig i `v_domeneautorisasjon` 72 timer senere — mens riktig
TXT-post fortsatt lå ute. Kravet gjelder derfor kun `verifisering` og
`overtakelsesoppgjor`, som begge *etablerer* en autorisasjon. For
`revalidering` er beviset uendret: TXT-posten må fortsatt hashe til den
tokenverdien raden bærer, av to distinkte observatører. Det er tokenet i
sonen som er beviset, ikke utstedelsestidspunktet.

### 2.4b Observatøren må kunne finne runden — en avgrenset lesekø

**EXECUTE på `meld_domeneobservasjon` alene er en prosess som ikke kan
gjøre noe.** Funksjonen tar `runde_id` som første argument, og
observatøren har hverken SELECT på `domeneobservasjonsrunde`, EXECUTE på
`apne_domeneobservasjonsrunde` eller noe API som forteller den at en runde
finnes — runden åpnes av arbeideren eller API-et, i en helt annen prosess.
Uten en vei til å oppdage `runde_id` og hostnavnet kan observatørunitene
autentisere og polle i det uendelige uten noen gang å finne noe å svare
på. Da samles aldri to observasjoner, og §2.4 stopper **all**
verifisering og all revalidering i stedet for å herde dem. Deploy-avsnittet
(§6b) sier uttrykkelig at unitene «poller åpne runder»; dette er
funksjonen de poller.

```sql
CREATE FUNCTION hent_apne_observasjonsrunder()
RETURNS TABLE (runde_id UUID, hostname TEXT, formal TEXT)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = public AS $$
BEGIN
  -- Kun observatørrollene. Ikke fordi GRANT-en ikke holder, men fordi
  -- køen filtrerer på `session_user`: en annen rolle ville fått hele
  -- listen, og det er ikke det denne funksjonen er til for.
  IF session_user NOT LIKE 'disponit_domeneobservator@_%' ESCAPE '@' THEN
    RAISE EXCEPTION 'kun observatørroller kan lese observasjonskøen';
  END IF;
  RETURN QUERY
    SELECT r.runde_id, r.hostname, r.formal
      FROM public.domeneobservasjonsrunde r
     WHERE r.status = 'apen' AND r.utloper > now()
       AND NOT EXISTS (SELECT 1 FROM public.domeneobservasjon o
                        WHERE o.runde_id = r.runde_id
                          AND o.observator = session_user)
     ORDER BY r.apnet
     LIMIT 50;                      -- avgrenset: køen er arbeid, ikke et register
END $$;
```

- **`tenant` returneres ikke.** Observatøren trenger hostnavnet for å
  slå opp TXT-posten; den trenger ikke å vite hvem som eier det. Uten
  tenant er utdata en arbeidsliste, ikke `domenekontroll`-porteføljen —
  som er nøyaktig grensen §6b setter.
- **`LIMIT 50` og «ikke meldt av meg»** gjør køen selvdrenerende: en
  observatør som har svart, ser ikke runden igjen, og en observatør som
  ligger etter kan aldri dra hele tabellen ut i ett kall.
- **Køen utvider ikke angrepsflaten.** Den lister kun runder som
  *allerede* er åpnet av en autorisert kaller, den er kortlevd av samme
  grunn som runden (`utloper`), og den kan verken åpne, avslutte eller
  omformålsbestemme noe. Restrisikoen er uendret fra §2.4: den ligger hos
  to samvirkende observatører, ikke hos oppdagelsen av `runde_id`.

### 2.4bb Runder er kortlevde — tabellen må være det også

**Ingenting i §2.4 avslutter en runde som bare utløper.** En runde merkes
`brukt` av konsumenten sin, men en runde der observatørene aldri ble enige,
eller der kalleren aldri kom tilbake, blir stående `apen` for alltid. Med
daglig revalidering av N domener er det N nye runder i døgnet som aldri
forlater det partielle køindeksen dekker, og to prosesser som poller
`hent_apne_observasjonsrunder()` kontinuerlig ville til slutt sortert en
historikk i stedet for en arbeidsliste. Tre ting lukker det, og ingen av
dem rører evidenskjeden:

1. **Utløpsovergangen er eksplisitt.** `apne_domeneobservasjonsrunde()`
   setter utløpte `apen` runder på målet til `forkastet` før den åpner
   (over), og `rydd_domeneobservasjonsrunder(p_maks INT DEFAULT 500)` gjør
   det samme for alle mål: `apen` og `utloper < now()` → `forkastet`,
   `ORDER BY utloper LIMIT p_maks FOR UPDATE SKIP LOCKED`. Samme batchform
   som §6, samme grunn.
2. **Retensjon, ikke evighet.** Samme funksjon sletter runder som har vært
   terminale (`brukt`/`forkastet`) i mer enn **30 døgn**, og
   `domeneobservasjon`-radene følger med (`ON DELETE CASCADE` på
   fremmednøkkelen). Det er trygt fordi den varige evidensen — hvilke
   observatører som var enige om hvilken hash, og hvilken overgang det
   førte til — allerede er kopiert til `domenekontroll_hendelse`, som er
   append-only og aldri ryddes. Runden er arbeidstilstand; hendelsen er
   evidens.
3. **Køen leser aldri historikken.** Det partielle indekset
   `domeneobservasjonsrunde_ko` dekker kun `status = 'apen'`, så
   pollespørringen holder seg konstant i størrelse uansett hvor mye som
   har vært.

Funksjonen kjøres av **ryddetimeren** (§6), som allerede har en batchgrense
og en alarm på to sammenhengende feilede kjøringer. Ingen ny timer, ingen
ny credential.

### 2.4c Rettighetskontrakten for 019 — REVOKE først, så GRANT

**En ny signatur er et nytt funksjonsobjekt, og nye funksjoner får
`EXECUTE` for `PUBLIC` som default.** Det gjelder hver eneste funksjon
019 innfører eller gir en ny parameterliste: `p_runde`-versjonene er ikke
`CREATE OR REPLACE` av 016/018-funksjonene, de er *nye* objekter ved siden
av dem. 016 fjerner den defaulten eksplisitt for sine egne funksjoner
(linje 916–924); gjør ikke 019 det samme, kan enhver rolle i clusteret
kalle SECURITY DEFINER-verifisering og -overtakelse direkte, og hele
kapittelet over er dekorasjon. Motsatt: å revoke fra `PUBLIC` uten å
GRANT-e til de faktiske kallerne gjør API-et og arbeideren ute av stand
til å kalle dem i det hele tatt. **Begge halvdeler må stå i migrasjonen:**

```sql
-- 1. Nye objekter: fjern PUBLIC-defaulten
REVOKE ALL ON FUNCTION apne_domeneobservasjonsrunde(TEXT, TEXT, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hent_apne_observasjonsrunder() FROM PUBLIC;
REVOKE ALL ON FUNCTION meld_domeneobservasjon(UUID, TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION verifiser_domenekontroll(TEXT, TEXT, BOOLEAN, TEXT, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION revalider_domenekontroll(TEXT, TEXT, TEXT, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION avgjor_domeneovertakelse(TEXT, TEXT, BIGINT, BOOLEAN, TEXT, UUID) FROM PUBLIC;
REVOKE ALL ON FUNCTION rydd_staged_artefakter(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION rydd_domeneobservasjonsrunder(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION hent_revalideringskandidater(INT) FROM PUBLIC;
REVOKE ALL ON FUNCTION forelder_hostname(TEXT) FROM PUBLIC;
REVOKE ALL ON FUNCTION sone_overlapp(TEXT, TEXT, BOOLEAN) FROM PUBLIC;

-- 2. Minste nødvendige EXECUTE, per kaller i tabellen i §2.4.
--    Merk hvilke roller som IKKE står her: `disponit_domains_admin` får
--    ingen nye grants i 019 og forblir NOLOGIN (§6b). Hver kjørende jobb
--    har sin egen rolle med nøyaktig de funksjonene jobben kaller.
GRANT EXECUTE ON FUNCTION apne_domeneobservasjonsrunde(TEXT, TEXT, TEXT)
  TO disponit_domenerevalidator, disponit;
GRANT EXECUTE ON FUNCTION hent_apne_observasjonsrunder()
  TO disponit_domeneobservator_1, disponit_domeneobservator_2;
GRANT EXECUTE ON FUNCTION meld_domeneobservasjon(UUID, TEXT)
  TO disponit_domeneobservator_1, disponit_domeneobservator_2;
GRANT EXECUTE ON FUNCTION verifiser_domenekontroll(TEXT, TEXT, BOOLEAN, TEXT, UUID)
  TO disponit;                       -- API-et; arbeideren verifiserer ikke
GRANT EXECUTE ON FUNCTION revalider_domenekontroll(TEXT, TEXT, TEXT, UUID)
  TO disponit_domenerevalidator;     -- arbeideren; API-et revaliderer ikke
GRANT EXECUTE ON FUNCTION avgjor_domeneovertakelse(TEXT, TEXT, BIGINT, BOOLEAN, TEXT, UUID)
  TO disponit;                       -- behandleren, §4.2b
GRANT EXECUTE ON FUNCTION rydd_staged_artefakter(INT)
  TO disponit_artefaktrydder;        -- ryddetimeren, §6
GRANT EXECUTE ON FUNCTION rydd_domeneobservasjonsrunder(INT)
  TO disponit_artefaktrydder;        -- samme timer, §2.4bb
-- Utstedelse av challenge er nå en API-handling (§2.5c). 016 ga den kun til
-- den NOLOGIN-rollen som ikke lenger kjører noe; uten denne linja finnes det
-- ingen kallbar vei til å registrere et domene i det hele tatt.
GRANT EXECUTE ON FUNCTION utsted_challenge(TEXT, TEXT, BOOLEAN, TEXT, TEXT)
  TO disponit;
-- Revalidatoren må kunne SE køen sin, og et kolonnegrant på
-- `domenekontroll` kan IKKE gi den det: tabellen har ENABLE + FORCE RLS med
-- en tenant-policy (016 linje 353–363), og jobbrollen har verken BYPASSRLS
-- eller noen vei til å sette hver tenants kontekst. Grantet ville sett
-- riktig ut og levert null rader (§2.2b). Køen går derfor gjennom eierens
-- egen SECURITY DEFINER-funksjon, som er den ENESTE kryss-tenant lesingen
-- arbeideren har — syv kolonner, én status.
GRANT EXECUTE ON FUNCTION hent_revalideringskandidater(INT)
  TO disponit_domenerevalidator;
-- `forelder_hostname` og `sone_overlapp` (§2.5b) får INGEN grant: de kalles
-- kun innenfra de SECURITY DEFINER-funksjonene som eier gjerdet. Et grant
-- til runtime ville gjort `sone_overlapp` til et kryss-tenant leseoppslag
-- over hele domeneporteføljen — den er STABLE og ser bort fra RLS.
```

**De gamle overloadene må vekk — alle tre, ikke bare to.** Så lenge en
gammel signatur er kallbar, står den ubeviste veien åpen ved siden av den
nye:

| Gammel signatur | Hvem har den i dag | Hvorfor den ikke kan bli stående |
|---|---|---|
| `revalider_domenekontroll(TEXT, TEXT, TEXT)` | `disponit_domains_admin` (016 linje 929) | Frisker opp et hvilket som helst verifisert domene uten observasjonsrunde, §2.4 |
| `verifiser_domenekontroll(TEXT, TEXT, BOOLEAN, TEXT)` | `disponit_domains_admin` (016 linje 928) | Oppretter en autorisasjon og **utløser en overtakelse** uten ett DNS-oppslag, §2.5 |
| `avgjor_domeneovertakelse(TEXT, TEXT, BIGINT, BOOLEAN, TEXT)` | `disponit_domains_admin` (016 linje 931) | **Tildeler domenet til den bundne utfordreren uten attestasjoner og uten runde** — hele §4 forbigått i ett kall |

Den siste er den som gjør de to første til halve arbeid: den tildeler
domenet til den bundne utfordreren i ett kall, uten attestasjoner og uten
runde. Alle tre **REVOKE-es fra `disponit_domains_admin` og fra
`disponit`** i 019, i samme migrasjon som etterfølgerne får sine grants.

**At rollen forblir NOLOGIN er ikke et argument for å la dem stå.**
`disponit_domains_admin` er fortsatt nåbar med `SET ROLE` for migrator og
for superbrukeren, og den er nettopp den rollen en fremtidig endring ville
fristes til å gi en credential (§6b sier hvorfor den ikke skal få en).
En revokert funksjon er en vei som ikke finnes; en NOLOGIN-rolle er en vei
som ikke er tatt ennå. Bare den første er en port.

`rydd_staged_artefakter()` uten argumenter (016 linje 856) **droppes**
(`DROP FUNCTION rydd_staged_artefakter()`), den revokes ikke: med en
default på `p_maks` ville `rydd_staged_artefakter()` blitt et tvetydig
kall mot to overloads, og timeren ville feilet på `function is not
unique` i stedet for å rydde. Ingen annen kaller finnes.

`lagre_artefakt_staged()`, `utsted_artefaktkapabilitet()`,
`innlos_artefaktkapabilitet()` og triggerfunksjonene beholder signaturene
sine (objekt 9). De er ekte `CREATE OR REPLACE`, som **bevarer ACL-en** —
016/017-s REVOKE + GRANT står ved lag, og 019 skal verken gjenta eller
røre dem.

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
  resolverne bak. Operatøren og AS-nummeret er derfor **konfigurert og
  validert mot en lukket liste** (§6b) — ikke utledet av at to
  endepunktstrenger er ulike, for det er de også innenfor én operatør.
- **Bred feil (> 20 % innen én time) → én driftsalarm.** Terskelen
  dedupliserer **varslingen**; den klassifiserer ikke tenantens tilstand,
  oppretter ingen M-37-sak, og skjuler ikke at `tenant X / hostname Y` har
  tre døgn uten vellykket revalidering. Individuelle feil forblir
  tenantbundet, auditert og søkbart evidens. Terskelen er konfigurerbar
  og målt.
- Alarmen sier «vi fikk ikke svar», aldri «domenene er tapt».

### 2.5b Gjerdet må dekke namespacet, ikke bare hostnavnet

**Wildcard-scopen dekker ett nivå mer enn raden den står på — gjerdet
gjør ikke det.** `en_verifisert_per_hostname`, `hostname_binding` (PK på
`hostname`) og advisory-låsen `domene:<hostname>` (016 linje 450, 633,
664) nøkler alle på hostnavnet slik det er skrevet. Verifiserer tenant A
`example.com` med `wildcard = true`, og tenant B deretter
`foo.example.com`, kolliderer ingenting: ulike hostnavn, ulike låser,
ingen B4-gren. `v_domeneautorisasjon` gir da `gyldig = true` for **to**
tenanter på det samme effektive hostnavnet — A via `wildcard_scope`, B
eksakt — og egressen slipper begge gjennom. Det er nøyaktig
dobbelttildelingen overtakelsesflyten finnes for å hindre, oppnådd uten å
røre den. 016/017/018 er checksum-låst, så gjerdet hører i 019.

**Sonelåsen: to nøkler dekker hele overlappsrommet.** Wildcard er én bit,
ikke en dybde (`v_domeneautorisasjon` eksponerer `wildcard AS
wildcard_scope`, 016 linje 170), altså nøyaktig ett nivå. To rader kan
derfor bare dekke hverandre hvis den ene *er* den andres direkte
forelder. Hver vei som kan sette en rad `verifisert` tar begge låsene, i
deterministisk rekkefølge — ellers vranglåser `example.com`→`com` mot
`foo.example.com`→`example.com`:

```sql
-- Kanonisk hostnavn (018 §0) er lowercase A-label uten avsluttende punktum,
-- så ren strengaritmetikk er entydig her.
CREATE FUNCTION forelder_hostname(h TEXT) RETURNS TEXT
  LANGUAGE sql IMMUTABLE STRICT AS $$
    SELECT CASE WHEN position('.' in h) = 0 THEN NULL
                ELSE substring(h from position('.' in h) + 1) END $$;

-- Begge nøklene, sortert. Ikke «låsen på hostnavnet, og så en til».
FOR v_nokkel IN
    SELECT k FROM unnest(ARRAY['domene:' || p_hostname,
                               'domene:' || public.forelder_hostname(p_hostname)]) AS k
     WHERE k IS NOT NULL ORDER BY k
LOOP
    PERFORM pg_advisory_xact_lock(hashtextextended(v_nokkel, 0));
END LOOP;
```

**Overlappstesten er lukket og positiv — tre måter, ikke «alt som ligner».**

```sql
CREATE FUNCTION sone_overlapp(p_tenant TEXT, p_hostname TEXT, p_wildcard BOOLEAN)
RETURNS TABLE (tenant TEXT, hostname TEXT, wildcard BOOLEAN,
               autorisasjonsgenerasjon BIGINT, retning TEXT)
  LANGUAGE sql STABLE SECURITY DEFINER AS $$
    SELECT d.tenant, d.hostname, d.wildcard, d.autorisasjonsgenerasjon,
           -- Retningen er en egenskap ved HOSTNAVNENE, ikke ved wildcard-biten.
           CASE WHEN d.hostname = p_hostname                           THEN 'eksakt'
                WHEN d.hostname = public.forelder_hostname(p_hostname) THEN 'forelder'
                ELSE                                                        'barn'
           END
      FROM public.domenekontroll d
     WHERE d.tenant <> p_tenant
       AND d.status = 'verifisert'
       AND ( d.hostname = p_hostname
          OR (d.wildcard AND public.forelder_hostname(p_hostname) = d.hostname)
          OR (p_wildcard AND public.forelder_hostname(d.hostname) = p_hostname)) $$;
```

**Retningen utledes av navnene, ikke av wildcard-biten.** Wildcard-flagget
sier *hvorfor* to rader overlapper; det sier ingenting om hvem som er
forelder. Et utkast som testet `d.wildcard` først feilet på en helt vanlig
konstellasjon: eksisterende `foo.example.com` med `wildcard = true`, ny
wildcard-verifisering av `example.com`. Da treffer barnegrenen i
`WHERE`-leddet, men `CASE`-en ville merket raden `forelder` — og oppgjøret
under ville fulgt forelderoppskriften og kun slått av barnets wildcard,
mens barnets **eksakte** `foo.example.com`-autorisasjon ble stående inne i
vinnerens wildcard-scope. To tenanter, ett effektivt hostnavn, gjennom
gjerdet som skulle stoppe nettopp det. `d.hostname = forelder(p_hostname)`
er entydig i alle fire kombinasjonene av wildcard-bitene.

**Utfallet: fail-closed i utfordrerens disfavør, aldri i innehaverens.**

| `retning` | Hva 019 gjør ved verifisering | Hvorfor |
|---|---|---|
| ingen treff | Som før | Ingen overlapp |
| `eksakt` | Uendret B4-gren (018) | Samme navn skiftet hender; innehaverens bevis er per definisjon foreldet |
| `forelder` / `barn` | Den nye raden settes **`avklaring_kreves`, aldri `verifisert`**; innehaverens rad røres **ikke**; `opprett_overtakelsessak()` på utfordrerens eget `(hostname, generasjon)` | Begge bevisene kan være sanne samtidig — et delegert subdomene er ikke en motsigelse. Ingen av dem er foreldet, så ingen skal tilbakekalles automatisk |

Det siste feltet er ikke forsiktighet, det er en DoS-sperre: å tilbakekalle
A-s wildcard fordi noen beviste kontroll over ett delegert subdomene ville
gjort delegering til et våpen mot forelderen.

#### En wildcard-verifisering kan møte FLERE innehavere samtidig

**`sone_overlapp` er en mengde, ikke en rad.** Med `p_wildcard = true` er
barnegrenen `forelder_hostname(d.hostname) = p_hostname` — den treffer
*hvert* verifiserte barn under navnet. Eier tenant B `foo.example.com` og
tenant C `bar.example.com` når tenant D verifiserer wildcard `example.com`,
returnerer funksjonen to rader hos to ulike tenanter. `konflikt_motpart` er
én `TEXT`-kolonne (016 linje 44), og et oppgjør som løser «taperen» i
entall ville da autorisert D-s wildcard mens ett av barna fortsatt sto
`verifisert`. Overlappet er altså flerpartig av natur, og både saken og
oppgjøret må bære hele mengden:

```sql
-- 019: hvilke motparter konflikten faktisk består av. Én rad per overlapp.
CREATE TABLE domenekonfliktpart (
  tenant TEXT NOT NULL,                   -- UTFORDREREN; RLS-nøkkel, som saken
  hostname TEXT NOT NULL,
  autorisasjonsgenerasjon BIGINT NOT NULL,-- konfliktens generasjon = sakens
  motpart_tenant TEXT NOT NULL,
  motpart_hostname TEXT NOT NULL,
  retning TEXT NOT NULL CHECK (retning IN ('eksakt','forelder','barn')),
  oppdaget TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, hostname, autorisasjonsgenerasjon,
               motpart_tenant, motpart_hostname));

-- RLS + FORCE, som `domenekontroll` (016 linje 353–363). Kolonnekommentaren
-- «RLS-nøkkel» over er en HENSIKT; uten disse tre linjene er den ingenting.
-- Runtime har bordgrant på tabellen (§6c), og en tabell uten policy leverer
-- hver eneste tenants rader til den delte `disponit`-rollen: én glemt
-- tenant-predikat i en saksvisning, og hostnavn og motparts-tenant-IDer
-- lekker på tvers. FORCE fordi eieren (`disponit_domene_eier`) selv skriver
-- her — den har BYPASSRLS, så skrivingen i `verifiser_domenekontroll()`
-- rammes ikke, men ingen annen eier-vei slipper unna policyen.
ALTER TABLE domenekonfliktpart ENABLE ROW LEVEL SECURITY;
ALTER TABLE domenekonfliktpart FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolasjon ON domenekonfliktpart
    USING      (tenant = current_setting('disponit.tenant', true))
    WITH CHECK (tenant = current_setting('disponit.tenant', true));
```

**`tenant` er utfordreren, og det er utfordreren som ser konflikten.**
Motparten (`motpart_tenant`) ser den *ikke* gjennom denne tabellen, og det
er tilsiktet: motparten ser sin egen `domenekontroll`-rad og hendelsene på
den, som er dens eget faktum. Samme grense som §3.1 setter for saken —
konfliktraten avledes per tenant, aldri skrevet inn i en annen tenants
saksbilde.

- **Skrives kun av `verifiser_domenekontroll()`**, under sonelåsen, i samme
  transaksjon som utfordreren settes `avklaring_kreves` — én rad per treff i
  `sone_overlapp`. Runtime har SELECT (§6c), aldri INSERT: mengden er
  databasens observasjon, ikke kallerens påstand.
- **`konflikt_motpart` beholder sin betydning** og settes til motparten i
  den *første* raden sortert på `(retning, motpart_hostname, motpart_tenant)`
  — det er verdien 018-s reapplikasjonsgren og `opprett_overtakelsessak()`
  allerede leser. Den er en peker inn i mengden, ikke mengden selv, og
  saksvisningen leser `domenekonfliktpart`, aldri kolonnen alene.
- **Saken forblir én per konflikt.** Idempotensnøkkelen er fortsatt
  `domeneovertakelse:<hostname>:<generasjon>` (§1) — det er utfordrerens
  konflikt som avgjøres, ikke ett parforhold av gangen, og fire øyne
  attesterer ett utfall for hele mengden.

**Oppgjøret snevrer inn nøyaktig det omstridte — for hver motpart.** I samme
transaksjon som den positive tildelingen (§4), under samme sonelås,
itererer `avgjor_domeneovertakelse()` over **hele** mengden:

| Taperraden er | Hva som skjer | Hvorfor |
|---|---|---|
| `eksakt` | `tilbakekalt`, grunn `tapte_domeneoppgjor` | Samme navn; det er selve autorisasjonen som skifter hender |
| `barn` (dens eget hostnavn ligger inne i vinnerens wildcard-scope) | `tilbakekalt`, grunn `tapte_domeneoppgjor` — **også når barnet selv har wildcard**; barnets scope dør med raden | Det omstridte er barnets eget navn. Å bare slå av barnets wildcard ville latt `foo.example.com` stå autorisert inne i vinnerens scope |
| `forelder` (kun wildcard-utvidelsen overlapper; forelderens eget hostnavn er ikke omstridt) | `wildcard = false`, generasjon++, hendelse `wildcardscope_innsnevret`; raden blir stående `verifisert` for sitt eget navn | Vinneren vant ett navn, ikke forelderens sone |

**Mengden avledes på nytt under låsen, den leses ikke fra saken.**
`avgjor_domeneovertakelse()` kaller `sone_overlapp()` selv i
oppgjørstransaksjonen og sammenligner resultatet med `domenekonfliktpart`
for sakens generasjon. Er de ulike — et barn er kommet til, en motpart er
tilbakekalt i mellomtiden — avvises tildelingen med `konfliktbildet_endret`
og saken føres tilbake til `manuell` med en ny mengde. To mennesker skal
ikke kunne attestere ett konfliktbilde og få et annet gjennomført; og
motsatt skal en motpart som er kommet til etter attestasjonen ikke kunne
overleve tildelingen ubemerket. Sonelåsen dekker dette fullt ut: enhver
verifisering av et barn under `example.com` tar også låsen
`domene:example.com`, så mengden kan ikke endre seg mens oppgjøret kjører.

**Men eksaktparten er allerede gjort opp ved verifiseringen — den kan ikke
avledes på nytt.** En naiv mengdesammenligning avviser hver eneste ordinære
eksakt-overtakelse: B4-grenen (018 linje 210–216) setter innehaveren A til
`tilbakekalt` i *samme* transaksjon som konfliktraden skrives, mens
`sone_overlapp()` kun ser `status = 'verifisert'` (over). Ved oppgjøret
avleder funksjonen derfor den tomme mengden mot en registrert mengde som
inneholder A, konkluderer `konfliktbildet_endret` — og hele §4-flyten
feiler på sin egen normalvei, i det tilfellet som er vanligst av alle.
Sammenligningen må dermed skille på `retning`, ikke gjøres i ett:

| Registrert `retning` | Hvordan den kontrolleres ved oppgjøret | Hvorfor |
|---|---|---|
| `forelder` / `barn` | Sammenlignes felt for felt mot `sone_overlapp()`-radene med samme retning. Avvik → `konfliktbildet_endret` | Disse motpartene står fortsatt `verifisert` (§2.5b-grenen rører dem ikke), så de *skal* dukke opp på nytt. Faller en bort eller kommer en til, er bildet et annet enn det som ble attestert |
| `eksakt` | Motpartens rad slås opp direkte og må fortsatt **ikke** være `verifisert`. Er den det, → `konfliktbildet_endret` | A ble tilbakekalt da konflikten oppsto; at den ikke er kommet tilbake er hele påstanden. En A som har rukket å bli `verifisert` igjen er et nytt bilde, ikke det attesterte |

Og speilvendt: returnerer `sone_overlapp()` en `eksakt`-rad ved oppgjøret,
avvises tildelingen uansett hva som er registrert. En verifisert rad på
vinnerens eget navn hos en annen tenant betyr at noen har vunnet navnet i
mellomtiden — å tildele over den ville gitt to `gyldig` tenanter på samme
hostnavn, altså nøyaktig invarianten §2.5b finnes for. Skillet svekker
ingenting: `eksakt`-parten er den ene motparten oppgjøret ikke *kan* miste
uoppdaget, fordi `en_verifisert_per_hostname` gjør den unik og 018-s
reapplikasjonsgren sender den gjennom `avklaring_kreves` hvis den forsøker
seg igjen.

**Konfliktraten er avledet, ikke et felt.** «≥ 3 parter innen 24 t →
`hoy_konfliktrate`» (§3) leses fra `domenekonfliktpart`: antall distinkte
`motpart_tenant` pluss utfordreren på samme `hostname` med `oppdaget >
now() - 24 t`. Markøren har ingen egen kolonne på `unntak` og ingen egen
hendelsestype — den er en autoritativ avledning fra tabellen over, som
tenantens saksvisning regner ut på samme evidens hver gang. Et lagret flagg
måtte vært skrevet på saker i **andre** tenanter (RLS forbyr det, §3.1) og
kunne blitt stående igjen etter at konflikten var avgjort.

`avvis` avgjør ingenting: utfordrerens rad blir `tilbakekalt`, alle
innehaverne står urørt, og `domenekonfliktpart`-radene blir stående som
evidens for hva som ble avvist.

**Invarianten holder gjennom hele forløpet.** Utfordreren står
`avklaring_kreves`, som ikke er `gyldig` i `v_domeneautorisasjon`, så det
effektive hostnavnet har hele tiden nøyaktig **én** autorisert tenant.
Veien ut er oppgjøret, ikke klokka (port 18b gjelder her som der), og
konfliktraden er ikke revalideringsarbeid (port 10c).

**Navngitt, ikke skjult:** en wildcard-verifisering på et offentlig
suffiks er et *annet* problem enn dette, og det er allerede stengt av at
ingen kan bevise DNS-kontroll over `com` gjennom en TXT-post i sonen.
Sonelåsen tar likevel `domene:com` som nøkkel — en låsnøkkel er ikke en
påstand om at raden finnes.

#### Gjerdet er fremoverrettet — det som alt står inne må ryddes

**En base oppgradert fra 016/018 kan allerede ha overlappet.** Gjerdet over
gjelder fra og med 019 og repareres ikke av seg selv: to rader som ble
`verifisert` før migrasjonen — A med wildcard `example.com`, B med eksakt
`foo.example.com` — blir stående nøyaktig som de er, og
`v_domeneautorisasjon` gir dem begge `gyldig = true` i det uendelige.
Installerte 019 bare gjerdet, ville migrasjonen påstått en invariant basen
ikke oppfyller. 019 rydder derfor **først**, i samme transaksjon, før
funksjonene erstattes:

```
for hvert par (d1, d2) av verifiserte rader i ULIKE tenanter der
    d1.hostname = forelder(d2.hostname) og d1.wildcard
    -- KUN forelderens wildcard-bit. Ikke `d1.wildcard OR d2.wildcard`:
    -- `sone_overlapp` (over) treffer utelukkende når FORELDEREN har
    -- wildcard, fordi barnets egen wildcard dekker barnebarn — ikke
    -- forelderen. `example.com` uten wildcard hos A og `foo.example.com`
    -- MED wildcard hos B har disjunkte scoper; et `OR` ville tilbakekalt
    -- den yngste av to fullt lovlige autorisasjoner, altså skrudd av
    -- egress for en tenant migrasjonen ikke hadde noe å utsette på.
    -- Ryddingen må dekke nøyaktig det gjerdet under gjerder, hverken mer
    -- eller mindre.
    -- eksakt-navnkollisjon kan ikke finnes: `en_verifisert_per_hostname`
    -- har gjerdet den siden 016
  innehaver := den med LAVEST (verifisert_ts, hostname, tenant)   -- deterministisk
  utfordrer := den andre
  utfordrer → 'tilbakekalt', grunn 'namespaceoverlapp_ved_019',
              konflikt_motpart = innehaver.tenant, generasjon++
  hendelse på begge rader, med hele overlappsbildet i `detalj`
  hostname_binding røres IKKE
```

- **Innehaveren er den eldste verifiseringen.** Ikke den bredeste, ikke
  den smaleste: den som sto der først. Regelen må kunne kjøres to ganger
  med samme utfall på en base som ble avbrutt, og `verifisert_ts` er det
  eneste feltet som gir det uten å ta stilling til hvem som «egentlig» eier
  navnet — det er fire øyne som avgjør, ikke en migrasjon.
- **`tilbakekalt` med motpart, ikke `avklaring_kreves`.** Det er ikke en
  formalitet: en rad i `avklaring_kreves` kan **kun** løftes ut av
  `avgjor_domeneovertakelse()`, som krever en M-37-sak — og saken opprettes
  av `opprett_overtakelsessak()` i Python, med tenantens aktive policyref og
  en blokkerende grunnkode (§3). En SQL-migrasjon kan ikke skrive den, og
  radene ville stått permanent uavgjørbare. `tilbakekalt` **med
  `konflikt_motpart` satt** treffer derimot 018-s reapplikasjonsgren (018
  linje 186–199): tenantens neste verifisering gjennom §2.5c sender raden
  til `avklaring_kreves` med **ny** generasjon og returnerer
  `konflikt:<motpart>`, som er nøyaktig signalet `opprett_overtakelsessak()`
  lages fra. Veien ut er den ordinære overtakelsesflyten, ikke en migrasjon
  som later som den kan avgjøre en tvist.
- **Fail-closed, og det koster noe navngitt.** Etter ryddingen har hvert
  effektivt hostnavn nøyaktig én `gyldig` tenant. Den yngste taper
  egresstilgangen sin inntil den verifiserer på nytt og vinner et oppgjør —
  et reelt driftsavbrudd for den tenanten, ført som hendelse på raden og
  talt i migrasjonens returverdi. Alternativet er å la to tenanter dele et
  hostnavn videre, og det er nettopp tilstanden hele §2.5b finnes for å
  gjøre umulig.
- **Antallet rapporteres, aldri stille.** Migrasjonen `RAISE NOTICE`-er
  antall ryddede rader, og port 2k måler at tallet er 0 på en base uten
  overlapp og nøyaktig 1 på en base som er konstruert med ett.

### 2.5c Noen må faktisk kalle verifiseringen — kjøreveien inn

**Etter §2.4c finnes det ellers ingen kallbar vei til en ny autorisasjon.**
Den gamle 4-argumentsversjonen er revokert, den nye femargumentsversjonen
er grantet **kun** til `disponit` — og et søk gjennom repoet finner
`verifiser_domenekontroll` utelukkende i migrasjoner og tester. Det finnes
ingen `/v1/domener`-rute, og implementasjonsinventaret la til én rute:
attestasjonsruten. Uten dette avsnittet lukker PR-015 sidesporene og lar
hoveddøren stå umurt igjen: ingen tenant kan registrere et domene, og
`utsted_challenge` ligger fortsatt bare hos en NOLOGIN-rolle. PR-015 må
derfor ha **verifiseringsflaten**, ikke bare oppgjørsflaten.

| Rute | Scope | Hva den gjør |
|---|---|---|
| `POST /v1/domener` | `domains:verify` | `utsted_challenge(tenant, hostname, wildcard, aktor, …)`; returnerer TXT-navn og token **én gang** (hashen lagres, klarteksten aldri, 016). Reissue på samme hostnavn forkaster åpne runder (§2.4a) |
| `POST /v1/domener/{hostname}/verifisering` | `domains:verify` | Åpner (eller gjenbruker, §2.4) runden med `formal = 'verifisering'` og svarer `202 venter_observasjoner` med `runde_id`. Er runden alt full — to distinkte observatører, samme `txt_hash` — kaller den `verifiser_domenekontroll(…, p_runde)` og svarer `200 verifisert`, `409 avklaring_kreves` (overlapp eller overtakelse, §2.5b/§3) eller `409 observasjon_uteblitt` når runden utløp uten to enige |
| `GET /v1/domener` / `GET /v1/domener/{hostname}` | `domains:read` | Tenantens egne rader: status, `utloper`, `siste_vellykkede_revalidering`, konfliktbildet fra `domenekonfliktpart`. Aldri `challenge_token_hash` |
| `POST /v1/domener/overtakelse/{unntak_id}/runde` | `domains:adjudicate` | Åpner runden med `formal = 'overtakelsesoppgjor'` på **sakens** vinnende `(tenant, hostname)` og returnerer `runde_id`-en attestasjonsruten tar som `dns_runde_id` (§4.2b steg 9). Uten den måtte attestanten gjettet en UUID |

- **Verifiseringen er tostegs og idempotent, ikke blokkerende.** Runden
  fylles av to andre prosesser, som poller sin egen kø (§2.4b); en HTTP-tråd
  som ventet på dem ville holdt en transaksjon åpen over et DNS-oppslag hos
  to operatører. Klienten kaller derfor samme rute igjen — åpningen er
  idempotent under sonelåsen, så et retry poller den *samme* runden — og
  ruten forbruker den i det øyeblikket den er full. Utløpt runde uten to
  enige er ikke en feil i basen: neste kall åpner en ny mot samme
  challenge.
- **`domains:verify` er tenantens eget domene, `domains:adjudicate` er en
  annens.** De to scopene er adskilte med vilje og bæres av hver sin rolle.
  Å registrere `example.com` for seg selv er en alminnelig
  administrasjonshandling; å avgjøre hvem av to kunder plattformen
  autoriserer, er ikke. En `domeneforvalter` kan aldri attestere et
  oppgjør, og en `domeneavgjorer` kan aldri registrere et domene:

  ```python
  # platform/core/api/autorisasjon.py — ROLLE_TIL_SCOPES
  "domeneforvalter": frozenset({"decisions:read", "policy:read",
                                "domains:read", "domains:verify"}),
  ```

  `domains:read` legges i tillegg til `admin` og `sikkerhet`, som lesescope:
  domeneporteføljen er tenantens egen tilstand, og en flate ingen kan se er
  ikke en flate.
- **Begge skrivescopene inn i `BROWSER_MUTASJONSSCOPES`.** `domains:verify`
  av samme grunn som `domains:adjudicate` (§4.2): registreringen skjer i
  nettleseren, med PR-012-s CSRF-vern. Lesescopet hører ikke hjemme der —
  settet gjelder mutasjon.
- **`utsted_challenge` grantes til `disponit`** i 019 (§2.4c). Den er ikke
  en cross-tenant-funksjon: den skriver challenge på tenantens egen rad, og
  reissue er allerede en dokumentert, auditert operasjon. Grantet til
  `disponit_domains_admin` blir stående — rollen er NOLOGIN og nås kun med
  `SET ROLE` (§6b).
- **Ingen rute setter status.** Alle fire kaller funksjoner som gjør
  overgangen selv, under sonelås og radlås. Invariant 3 er uendret: motoren
  beslutter, ruten formidler.

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
`hoy_konfliktrate`, **avledet av `domenekonfliktpart`** (§2.5b), ikke et
lagret flagg; markøren vises i saksvisningen og stopper ingenting
automatisk.

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
  1. lås SONEN (hostnavnet OG forelderen, sortert — §2.5b);
     les p_tenant-raden FOR UPDATE
  2. status må være 'avklaring_kreves' og generasjonen må stemme (uendret fra 018)
     — MED ÉN TILLEGGSGREN, se 5 under
  3. p_godkjent:
       - p_runde må være en `apen`, ikke utløpt runde med
         formal='overtakelsesoppgjor' på (p_tenant, p_hostname), med >= 2
         observasjoner fra distinkte observatører og samme txt_hash (§4),
         og rundens challenge_token_hash må stemme med radens (§2.4a)
       - attestasjonene må være FERSKE (§4)
       - konfliktbildet avledes på nytt: sone_overlapp(...) sammenlignet med
         domenekonfliktpart for sakens generasjon → avvik = 'konfliktbildet_endret',
         ingen tildeling (§2.5b)
       - vinneren settes 'verifisert', generasjon++, nytt 90-døgnsvindu
       - hostname_binding settes til p_tenant  (den FLYTTES, den forutsettes ikke)
       - HVER ANNEN rad på p_hostname med status 'avklaring_kreves' settes
         'tilbakekalt' med grunn 'tapte_domeneoppgjor', og en hendelse per rad
         — i SAMME transaksjon, under samme lås.
         GENERASJONEN PÅ TAPERRADENE RØRES IKKE (se §3.1)
       - HVER rad i overlappsmengden (§2.5b) avgjøres i samme transaksjon:
         'eksakt'/'barn' → 'tilbakekalt', 'forelder' → wildcard = false.
         Flere motparter er ETT oppgjør, ikke ett kall per motpart
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
| `POST /v1/domener/overtakelse/{unntak_id}/runde` (ny, §2.5c) | `domains:adjudicate` | samme familiegjerde; åpner DNS-runden attestasjonen skal bære |
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
     `p_runde` er DNS-runden (§2.4), åpnet av rundeRUTEN i §2.5c og sendt
     inn som `dns_runde_id`; den kreves kun ved `godkjenn` og er ikke
     godkjenningsrunden fra steg 5. Funksjonen avgjør HELE
     overlappsmengden (§2.5b) — flere motparter er ett oppgjør, ikke ett
     kall per motpart
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
  **Og unntaket må gjelde i preflighten også, ellers finnes det ikke.**
  `innlos_artefaktkapabilitet()` kjører før `lagre_artefakt_staged()`; sammenlignet
  den generasjonen ubetinget, ville en `brukt` kapabilitet fra før et reclaim
  blitt avvist der — og den idempotente grenen lenger inne aldri blitt nådd.
  Nøyaktig port 24d (tapt svar → reclaim → retry av samme dokument) ville da
  ikke kunnet returnere den opprinnelige artefakt-IDen. Generasjonsleddet i
  preflighten gjelder derfor **kun `status = 'utstedt'`**; `brukt` slippes
  gjennom til den hash-sjekkede idempotensgrenen, som svarer på innhold, ikke
  på eierskap. `feilet` er terminal og avvises som før.

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
`rydd_staged_artefakter(500)` og deretter
`rydd_domeneobservasjonsrunder(500)` (§2.4bb). Timeren legger **ingen
logikk oppå** noen av de positive reglene.

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
logge inn, observerer ikke.** Python-filene og migrasjonen er ikke hele
PR-015: `deploy/staging/oppsett-postgresql.sh` er repoets **eneste**
rollegrense — migrasjoner har uttrykkelig forbud mot å opprette
clusterroller (skriptets egen kommentar, linje 30–34) — og
`deploy/staging/opp.sh` har en **lukket** unit-liste (linje 51–54) og en
like lukket brukerløkke (linje 67–69), som begge preflightes og enables.
Uten endringer begge steder er §2 og §6 spesifikasjoner uten kjøretid.

**Alle fire nye rollene må finnes FØR migrasjonen.** 019 gjør `GRANT
EXECUTE` til `disponit_domeneobservator_1`, `disponit_domeneobservator_2`,
`disponit_domenerevalidator` og `disponit_artefaktrydder` (§2.4c) — en
`GRANT` til en rolle som ikke finnes er en feil, ikke en advarsel, og ville
stoppet første migrasjonskjøring på en fersk installasjon. Rekkefølgen i
skriptet er allerede riktig (roller linje 39–51, `migrer.py` linje 221);
det som mangler er rollene:

| Rolle | Type | Hvorfor |
|---|---|---|
| `disponit_domeneobservator_1` / `_2` | **LOGIN**, tilfeldig passord, egen DSN | Identiteten er `session_user` (§2.4). En NOLOGIN-rolle kan ikke være noens `session_user`, og uten DSN kan prosessen ikke autentisere |
| `disponit_domenerevalidator` | **LOGIN** + DSN, **ny** | Revalideringsarbeideren (§2). Har nøyaktig tre EXECUTE (`apne_domeneobservasjonsrunde`, `revalider_domenekontroll(…, p_runde)`, `hent_revalideringskandidater(INT)`) og **ingen bord- eller kolonnegrant** — køen leses gjennom funksjonen fordi RLS gjør et kolonnegrant tomt, §2.2b/§2.4c |
| `disponit_artefaktrydder` | **LOGIN** + DSN, **ny** | Ryddetimeren (§6). Har nøyaktig to EXECUTE: `rydd_staged_artefakter(INT)` og `rydd_domeneobservasjonsrunder(INT)` |
| `disponit_domains_admin` | **NOLOGIN, uendret fra 014b** | Se under: den skal ikke bli en servicecredential |

- Alle fire går inn i LOGIN-løkken (de tre nye; domains_admin blir i
  NOLOGIN-løkken) og gjennom `sikre_rolle_dsn` + `verifiser_og_reparer`
  (linje 99–123), som er skriptets beviste vei: passordrotasjon og miljøfil
  holdes i takt, og en halvskrevet DSN repareres før noen migrasjon kjøres.
- **`disponit_domains_admin` skal ikke bli en servicecredential.** Et
  tidligere utkast løste «jobbene kan ikke autentisere» ved å gi den
  eksisterende domenerollen LOGIN og DSN. Det ville eksponert **hele**
  016-grantsettet dens, ikke bare revalidering og rydding: den beholder
  `utsted_challenge`, `tilbakekall_domenekontroll` og
  `registrer_artefakttype` (016 linje 927–933). Et kompromiss av *hvilken
  som helst* av de to timer-credentialene hadde da kunnet tilbakekalle en
  hvilken som helst tenants domene eller skrive om challengen dens — også
  etter at de tre gamle overloadene er revokert (§2.4c). To timere med hver
  sin least-privilege-rolle koster to linjer i oppsettskriptet og fjerner
  hele den flaten. Rollen blir stående som det den er i 014b: en NOLOGIN
  eier-/adminrolle som nås med `SET ROLE` av superbruker eller migrator,
  aldri over nettverket.
- **Og den må bli værende NOLOGIN på en oppgradert base.** En base som
  rakk å kjøre et tidligere PR-015-utkast har rollen som LOGIN med et
  passord i miljøfilen. Overgangen tilbake er like eksplisitt og idempotent
  som den ville vært den andre veien, og står **før** DSN-ene tas i bruk:

  ```bash
  # PR-015 (Codex P1): domenerollen er en NOLOGIN-adminrolle. Kjørte basen et
  # tidligere utkast som ga den LOGIN, skal den tilbake — ALTER er idempotent
  # og trygg både på en fersk base og på en oppgradert. Jobbene har sine egne
  # roller (over); ingenting mister tilgang av denne linja.
  sudo -u postgres psql -qc "ALTER ROLE $DOMAINSADMIN NOLOGIN"
  ```

  Porten måles på **både** en fersk og en oppgradert base (port 28c):
  `disponit_domains_admin` kan ikke logge inn, og begge timerne kjører
  likevel.
- **Ikke medlemskap i stedet for egne roller.** Å la de nye jobbrollene
  arve `disponit_domains_admin` ville dratt med seg *arvede* rettigheter —
  og RLS-policyer med `TO`-klausul matcher på arvet medlemskap. Det er
  nøyaktig fellen skriptets `WITH INHERIT FALSE`-kommentar (linje 60–66)
  beskriver, gjeninnført av en GRANT som ser ut som en formalitet. Egen
  credential med egne grants per prosess er også det §2.4 faktisk krever.
- **Observatørene får nøyaktig to funksjoner, og ingenting mer.** EXECUTE
  på `hent_apne_observasjonsrunder()` (§2.4b) og på
  `meld_domeneobservasjon` — lese køen, svare på den. Ikke
  `apne_domeneobservasjonsrunde`, ikke revalidering, ikke
  `avgjor_domeneovertakelse`, ikke SELECT på noen tabell, heller ikke
  `domeneobservasjonsrunde` selv. En kompromittert observatør skal kunne
  lyve om én observasjon, ikke lese domeneporteføljen — og køen gir den
  hostnavn uten tenant, altså arbeidet uten porteføljen. **Én funksjon
  alene ville vært en prosess som ikke kan gjøre noe:**
  `meld_domeneobservasjon` tar `runde_id` som argument, og uten køen
  finnes det ingen vei til den verdien.

**Unitene må inn i den lukkede lista.** Fire nye filer i
`deploy/staging/`, lagt til `UNITS` i `opp.sh` slik at `preflight_units`
verifiserer dem og `systemctl enable --now` starter dem:

| Unit | Kadens | DB-rolle | Unix-bruker |
|---|---|---|---|
| `disponit-domenerevalidering.service` + `.timer` | hver time (§2) | `disponit_domenerevalidator` | `disponit-domener` |
| `disponit-artefaktrydding.service` + `.timer` | hvert 15. min (§6) | `disponit_artefaktrydder` | `disponit-rydd` |
| `disponit-domeneobservator-1.service` | kontinuerlig, poller åpne runder | `disponit_domeneobservator_1` | `disponit-obs1` |
| `disponit-domeneobservator-2.service` | kontinuerlig, poller åpne runder | `disponit_domeneobservator_2` | `disponit-obs2` |

- Observatørene er **to separate units med hver sin miljøfil**, ikke to
  tråder i én prosess. Delte de prosess, ville «to distinkte
  `session_user`» vært en formalitet: ett kompromiss gir da begge
  stemmene, og hele §2.4 hviler på at det ikke er tilfellet.
- **Separate prosesser er ikke nok — de må være separate *identiteter*.**
  `opp.sh`-s brukerløkke (linje 67–69) oppretter i dag kun `disponit-api`,
  `disponit-m37` og `disponit-helse`. Kjørte begge observatørunitene som
  samme Unix-bruker, kunne prosess 1 lest prosess 2-s miljøfil og
  autentisert som `disponit_domeneobservator_2`: ett kompromiss, begge
  stemmene, og §2.4-s eneste reelle skanse borte — uten at noen `GRANT`
  eller `session_user`-sjekk hadde merket det. Fire nye systembrukere legges
  derfor inn i den samme løkken (`--system --no-create-home --shell
  /usr/sbin/nologin`), og credentialen følger identiteten:

  | Fil | Eier | Modus |
  |---|---|---|
  | `/etc/disponit/observator-1.env` | `disponit-obs1` | `0400` |
  | `/etc/disponit/observator-2.env` | `disponit-obs2` | `0400` |
  | `/etc/disponit/domenerevalidering.env` | `disponit-domener` | `0400` |
  | `/etc/disponit/artefaktrydding.env` | `disponit-rydd` | `0400` |

  Hver unit setter `User=` til sin egen bruker og `EnvironmentFile=` til sin
  egen fil; ingen av de fire kan lese de andres. Den delte
  `/etc/disponit/miljo` (0600, root) blir **ikke** montert inn i
  observatørunitene — der ligger KEK, token-pepper og migrator-DSN, og en
  observatør har ingenting der å gjøre. Alternativet, `LoadCredential=` med
  `systemd-creds`, gir samme isolasjon og er likeverdig; det som ikke er
  likeverdig, er én felles fil to prosesser kan lese.
- **Porten er en negativ test, ikke en påstand:** observatør 1-s bruker
  forsøker å lese observatør 2-s miljøfil → `Permission denied` (port 28d).
- **Diversitetsporten måler operatør, ikke streng.** `8.8.8.8` og
  `8.8.4.4` er to ulike endepunkter og **én** operatør; to DoH-aliaser kan
  peke på samme tjeneste. En ulikhetstest på endepunktstekst ville derfor
  bestått med begge observasjonene hengende på ett driftsansvar og ett
  kompromiss — samtidig som databasen behandler de to innloggingsrollene
  som *uavhengig* evidens, god nok til å opprette og flytte
  domeneautorisasjon. Uavhengigheten kan ikke utledes; den må bæres av
  konfigurasjonen som en påstand som lar seg validere:

  | Nøkkel | Innhold | Validering ved oppstart |
  |---|---|---|
  | `DISPONIT_RESOLVER_ENDEPUNKT` | resolverens adresse/URL | Må være satt; ingen defaultverdi |
  | `DISPONIT_RESOLVER_OPERATOR` | operatørens id, fra en **lukket** liste i repoet (`deploy/staging/resolveroperatorer.json`) | Ukjent verdi → oppstart nektes, ikke en advarsel |
  | `DISPONIT_RESOLVER_ASN` | AS-nummeret endepunktet ligger i | Må være satt og stemme med operatørens oppføring i lista |
  | `DISPONIT_OBSERVATOR_MOTPART_OPERATOR` / `_ASN` | den andre observatørens operatør og ASN | Begge må være **ulike** egne verdier |

  Prosessen nekter å starte hvis en nøkkel mangler, hvis operatøren ikke
  står i den lukkede lista, hvis operatøren eller ASN-et er lik motpartens,
  eller hvis DB-rollen ikke er en `disponit_domeneobservator_*`. Lista er
  data i repoet og revideres som kode: en ny resolver tas i bruk ved å
  legge den inn, ikke ved å skrive en ny streng i en miljøfil.
  Endepunktulikhet er fortsatt et krav, men den svakeste av de tre — den
  alene beviser ingenting.
- Sagt rett ut om staging: begge prosessene kjører på **samme vert**, med
  resolvere hos ulike operatører i ulike AS-nummer. Det gir rolle-,
  operatør- og nettdiversitet, ikke vertsdiversitet — en navngitt
  restrisiko som lukkes i produksjon, ikke en port vi later som er
  bestått.
- Ryddetimeren erstatter ikke `disponit-rydd-pending.timer` (PR-009,
  PENDING-tokens). To ulike jobber, to ulike navn.

### 6c. Runtime-tilgangen til `overtakelse_attestasjon` hører i `migrer.py`

**En inline GRANT i 019 ville blitt vasket bort ved neste deploy.**
`deploy/staging/migrer.py` kjører `NULLSTILL_TABELLER` — `REVOKE ALL` på
**hver tabell migrator eier** (linje 42–55) — etter at migrasjonene er
kjørt, og gjenoppretter deretter kun det som står i den lukkede
`RETTIGHETER`-blokka (linje 57–114). `overtakelse_attestasjon` opprettes
av migrator og står ikke i den lista. 016 sier dette rett ut om sine egne
tabeller: runtime-grants hører i `migrer.py`, «en løs GRANT her ville
blitt vasket bort» (016 linje 377–380). Uten en linje der ville
`behandle_domeneattestasjon()` steg 6 — som er en direkte
`INSERT ... ON CONFLICT DO UPDATE`, ikke et funksjonskall — feilet med
`permission denied` på aller første stemme, på en base som er nettopp
deployet og der alle testene er grønne.

```python
# PR-015: fire øyne ved cross-tenant domenetildeling. Runtime SKRIVER raden
# direkte (som `menneskelig_attestasjon` i PR-012): saksbindingstriggeren
# (§1) beviser målet, fornyelsestriggeren låser bindingsfeltene, og RLS +
# FORCE holder den tenantbundet — derfor er et bordgrant trygt her.
# UPDATE er nødvendig for fornyelsen (§1); DELETE er forbudt og gis aldri.
GRANT SELECT, INSERT, UPDATE ON overtakelse_attestasjon TO {rolle};
# Konfliktbildet leses av saksvisningen og av verifiseringsflaten (§2.5b/§2.5c).
# KUN SELECT: radene skrives av `verifiser_domenekontroll()` som
# `disponit_domene_eier`, aldri av runtime — mengden er databasens
# observasjon, ikke kallerens påstand.
GRANT SELECT ON domenekonfliktpart TO {rolle};
```

- **Kun `RETTIGHETER`, ikke `ARBEIDER_RETTIGHETER`.** Attestasjonen
  skrives av API-behandleren (`disponit`). `disponit_arbeider` attesterer
  ikke, og skal ikke kunne det.
- **`domeneobservasjonsrunde` og `domeneobservasjon` får ingen linje.**
  De nås utelukkende gjennom SECURITY DEFINER-funksjonene i §2.4/§2.4b;
  et bordgrant der ville gjort observatørkontrakten til pynt — runtime
  kunne skrevet sin egen observasjon. Samme resonnement som
  `arbeidskapabiliteter` (migrer.py linje 80–84).
- **`disponit_domains_admin` berøres ikke av dette.** REVOKE-syklusen
  kjøres kun for `disponit`, token-admin og `disponit_arbeider`, så 016-s
  EXECUTE-grants til domenerollen overlever som før — det samme gjelder
  019-s grants til jobbrollene og observatørrollene (§6b), som heller ikke
  er med i syklusen.

### 6d. Eierskapsreparasjonen må kjenne 019-objektene

**`deploy/staging/eierskap-reparasjon.sql` er en lukket designtabell, og
019 legger til objekter den ikke kjenner.** Skriptet bygger `_design` (linje
32–…) med hver privilegert eid tabell og funksjon, klassifiserer så alt
*annet* som strøgods og flytter det til migrator (linje 147–165). Listen
ender i dag på 016/017-signaturene. Etter 019 ville hver ny
`disponit_domene_eier`-eid funksjon — `verifiser_domenekontroll(…, UUID)`,
`revalider_domenekontroll(…, UUID)`, `avgjor_domeneovertakelse(…, UUID)`,
`apne_domeneobservasjonsrunde`, `hent_apne_observasjonsrunder`,
`meld_domeneobservasjon`, `sone_overlapp`, `forelder_hostname`,
`rydd_domeneobservasjonsrunder`, `hent_revalideringskandidater` — og de
nye tabellene
(`overtakelse_attestasjon`, `domenekonfliktpart`,
`domeneobservasjonsrunde`, `domeneobservasjon`) blitt reklassifisert som
ordinære objekter og fått eier migrator ved neste `oppsett-postgresql.sh`.
Og fordi 019 da for lengst er checksum-hoppet, kjøres den aldri på nytt:
eierskapet blir **ikke** gjenopprettet. SECURITY DEFINER-funksjonene ville
kjørt med migrators privilegier i stedet for den BYPASSRLS-avgrensede
eierrollen, og hele NOLOGIN-eiergrensen vært borte — stille, på en base der
alle testene er grønne.

- **Hver ny og hver erstattet signatur inn i `_design`.** Signaturen er
  nøkkelen (`to_regprocedure`), så `p_runde`-versjonene er *nye* rader ved
  siden av 016/018-radene — ikke erstatninger av dem.
- **De tre gamle overloadene tas UT** i samme runde: 019 revoker dem
  (§2.4c), og en designrad for et objekt som fortsatt finnes, men ikke skal
  finnes, er en påstand om at gjerdet ikke er satt. Beholdes de likevel til
  de er droppet overalt, gjelder samme transitoriske begrunnelse som
  `claim_neste_oppdrag`-raden i filen — men da skrevet ned, ikke antatt.
- **Paritetstesten er porten.** Den samme testen som fanget
  `hent_pending_token` og `slaa_opp_sesjon` da de manglet, må dekke
  019-objektene: hver funksjon eid av `disponit_domene_eier` i basen skal
  ha en rad i `_design`, og omvendt (port 28e).

## 7. De fire portspørsmålene

| Kontroll | Alle veier inn? | Samtidighet? | Riktig vs. velformet? | Lukket format? |
|---|---|---|---|---|
| Domeneobservasjon (**verifisering OG revalidering**) | Begge inngangene krever `p_runde`; alle tre gamle overloads (verifisering, revalidering **og femarguments-`avgjor_domeneovertakelse`**) er REVOKE-et fra både `disponit_domains_admin` og `disponit`, og hver ny signatur er REVOKE-et fra `PUBLIC` før den grantes til sin ene kaller (§2.4c); veien inn til en NY autorisasjon er kun verifiseringsruten (§2.5c); én timer, én arbeidernøkkel; manuell kjøring tar samme lås | Advisory-lås; K som `LIMIT`; avledet plan; runden er engangs, kortlevd og **unik per (tenant, hostname, formal)** mens den lever; åpningen er idempotent | Observasjonene skrives av observatørrollene selv (`session_user`), hashes i DB mot **rundens** challenge (§2.4a); arbeideren har ikke EXECUTE på `meld_domeneobservasjon` og setter aldri status; køen leses gjennom `hent_revalideringskandidater()` fordi FORCE RLS gjør et kolonnegrant tomt (§2.2b) | `formal` er lukket enum; kaller kun `apne_domeneobservasjonsrunde()` + `verifiser_/revalider_domenekontroll(…, p_runde)`; terminale runder ryddes på tid (§2.4bb) |
| Overtakelsesavgjørelse | Kun domeneruten (§4.2) → **egen behandler** (§4.2b) → PR-012-runden → funksjonen; den generelle unntaksruten avviser familien, og saken settes `manuell` ved opprettelse så veien i det hele tatt finnes (§3); taperens sak har nøyaktig én lovlig handling (§3.1) | Hostname-lås; hele oppgjøret — inkludert reautoriseringen av hver talt attestant (§4.3) — i én transaksjon; én sak per konflikt­generasjon; ny konflikt = ny `unntak_id` | Målet bevist mot sakens egen idempotensnøkkel (§1); to distinkte aktører som **fortsatt** har `domains:adjudicate` ved tildelingen (§4.3; rollen finnes, §4.1, og døren slipper den gjennom, §4.2), identisk utfall, **begge ferske (72 t)**, og **fersk observasjonsrunde** ved positiv tildeling | Funksjonens enum; PK `(tenant, unntak_id, aktor)` hindrer dobbeltstemme; fornyelse endrer kun `avgitt_ts`/`utfall` + autorisasjonssnapshotet |
| Domenegjerdet (§2.5b) | Overlappstesten ligger i `verifiser_domenekontroll` — den ENESTE veien inn i `verifisert`, også overtakelsesgrenen; ingen kaller kan hoppe over den, og `sone_overlapp` har ingen grant utenfor funksjonen; **rader fra før 019 ryddes av migrasjonen selv**, så gjerdet ikke bare gjelder fremover | **Sonelås på både hostnavnet og forelderen**, i sortert rekkefølge; `example.com` og `foo.example.com` serialiseres mot hverandre, ikke bare mot seg selv; hele overlappsmengden avledes på nytt under låsen ved oppgjør — `forelder`/`barn` mot `sone_overlapp()`, `eksakt` mot at motparten fortsatt ikke er `verifisert`, siden den ble tilbakekalt allerede ved verifiseringen (§2.5b) | Gjerdet måler *effektiv* dekning (wildcard = ett nivå), ikke likhet i hostnavnstreng; `retning` utledes av hostnavnene, ikke av wildcard-biten; overlapp gir `avklaring_kreves` + sak, aldri `verifisert` | Tre og bare tre overlappsformer (`eksakt`/`forelder`/`barn`); en fjerde form er en feil, ikke stillhet; motpartsmengden er en tabell (`domenekonfliktpart`), ikke én kolonne |
| Drift av det hele (§6b/§6c/§6d) | Rollene opprettes kun i `oppsett-postgresql.sh` (migrasjoner har forbud); unitene kun i `opp.sh`-s lukkede liste, som preflightes og enables; runtime-bordtilgangen kun i `migrer.py`-s lukkede `RETTIGHETER` (en inline GRANT vaskes bort); eierskapet kun i `eierskap-reparasjon.sql`-s `_design` | Roller før migrasjon; REVOKE-syklus etter migrasjon, så grants gjenopprettes fra lista; timerne tar hver sin advisory-lås; observatørene er separate prosesser med hver sin Unix-bruker og egen 0400-miljøfil | Innlogging per rolle, `is-active` per unit og **første attestasjonsskriv etter et fullt deploy** MÅLES på en fersk base (port 28/28b), ikke antas; at domenerollen IKKE kan logge inn måles på en oppgradert base (28c) | `UNITS`, `RETTIGHETER`, `_design` og resolveroperatørlista er lukkede lister; en observatør med feil rolle, ukjent operatør eller delt operatør/ASN nekter å starte |
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
2g **Femargumentsversjonen av `avgjor_domeneovertakelse` er ikke lenger
kallbar** for `disponit_domains_admin` eller `disponit`: et direkte kall
med `(tenant, hostname, unntak_id, true, aktor)` → `permission denied`,
domeneraden urørt, ingen tildeling. Målt med `SET ROLE`, ikke over
nettverket — rollen er NOLOGIN (§6b), og porten skal bevise at veien er
**revokert**, ikke bare at den er uinnlogget ·
2h **Ingen 019-funksjon er kallbar for `PUBLIC`.** For hver nye signatur
i §2.4c: en rolle uten eksplisitt grant (test-rollen holder) kaller
funksjonen → `permission denied`. Målt med `has_function_privilege('public', …,
'EXECUTE') = false` for **alle** oppføringene, ikke et utvalg — og
samtidig at hver navngitt kaller i tabellen faktisk **kan** kalle sin
egen, så REVOKE-en ikke låser ute driften den skal beskytte ·
2i **Observatøren finner runden.** `hent_apne_observasjonsrunder()` kalt
av `disponit_domeneobservator_1` mot en nettopp åpnet runde returnerer
`(runde_id, hostname, formal)` og **ingen `tenant`**; etter at samme
observatør har meldt inn, er runden borte fra dens egen kø, men fortsatt
synlig for `_2`; en utløpt eller `brukt` runde vises ikke; kallet fra
`disponit_domains_admin` eller `disponit` → nektet. Testen må kjøre hele
kjeden observatørprosessen faktisk kjører — kø → oppslag → melding —
ikke starte fra et `runde_id` testen selv kjenner ·
2j **Wildcard-namespacet er gjerdet (§2.5b):** tenant A verifiserer
`example.com` med `wildcard = true`; tenant B kjører deretter en helt
gyldig verifisering av `foo.example.com` med to enige observatører →
B-raden blir **`avklaring_kreves`, aldri `verifisert`**, A-raden er
urørt, og det opprettes en overtakelsessak på B-s eget hostnavn. Målt på
`v_domeneautorisasjon`: gjennom hele forløpet har nøyaktig **én** tenant
`gyldig = true` for `foo.example.com`. Motsatt retning måles i samme port
(B eksakt først, A wildcard etterpå), og en søskenrad
(`bar.example.com` hos en tredje tenant, uten wildcard i bildet) skal
**ikke** utløse noe. Samtidighet er en del av porten: `example.com` og
`foo.example.com` verifiseres i to parallelle transaksjoner og
serialiseres av sonelåsen — en variant som bare kjører dem sekvensielt
består selv uten lås ·
2j-2 **Retningen er hostnavnenes, ikke wildcard-bitens:** eksisterende
`foo.example.com` med `wildcard = true` hos tenant B, deretter wildcard
`example.com` hos tenant D → `sone_overlapp` merker B-raden `barn`, og
et positivt oppgjør for D setter **hele** B-raden `tilbakekalt`. En
implementasjon som kun slår av B-s wildcard etterlater
`foo.example.com` autorisert inne i D-s scope, og porten måler nettopp
det på `v_domeneautorisasjon`: null hostnavn med to `gyldig` tenanter ·
2j-3 **Flerparts wildcard-overlapp:** B eier `foo.example.com`, C eier
`bar.example.com`, D verifiserer wildcard `example.com` → begge blir
rader i `domenekonfliktpart` på D-s sak, saksvisningen viser begge, og
**én** positiv attestasjonsrunde tilbakekaller **begge** i samme
transaksjon. Variant: et tredje barn (`baz.example.com`) verifiseres
etter attestasjonene, men før oppgjøret → tildelingen avvises med
`konfliktbildet_endret`, ingen rad flyttes, saken tilbake til `manuell` ·
2j-4 **Eksaktparten avledes ikke på nytt (§2.5b):** den helt ordinære
veien — A `verifisert` på `example.com`, B verifiserer og overtar, A
settes `tilbakekalt` i samme transaksjon som konfliktraden skrives, to
attestanter godkjenner → B blir `verifisert`. En implementasjon som
sammenligner hele mengden mot `sone_overlapp()` i ett feiler her med
`konfliktbildet_endret` på sin egen normalvei, og porten er nettopp den
målingen. Negativ variant i samme port: A rekker å bli `verifisert` igjen
før oppgjøret → tildelingen **avvises**, og
`v_domeneautorisasjon` har fortsatt null hostnavn med to `gyldig`
tenanter ·
2k **Oppgraderingsryddingen (§2.5b):** en base konstrueres med rader som
016/018 slapp gjennom — A wildcard `example.com` (eldst) og B eksakt
`foo.example.com` — og 019 kjøres → B står `tilbakekalt` med grunn
`namespaceoverlapp_ved_019` og `konflikt_motpart = A`, A er urørt, antallet
er rapportert, og `v_domeneautorisasjon` har null hostnavn med to `gyldig`
tenanter. **Det disjunkte paret måles i samme port:** A eksakt (uten
wildcard) på `example.com` og B **med** wildcard på `foo.example.com` er
*ikke* overlapp — scopene er disjunkte — og begge skal stå urørt etter
019. En rydding som predikerer på `d1.wildcard OR d2.wildcard` består
hovedmålingen og feller dette paret. **Veien ut måles i samme port:** B verifiserer på nytt gjennom
§2.5c → reapplikasjonsgrenen gir `avklaring_kreves` med ny generasjon og en
fersk M-37-sak som kan avgjøres. En base uten overlapp får `ryddet = 0`, og
migrasjonen er idempotent ved ny kjøring ·
2l **Én levende runde per mål og formål:** to samtidige åpninger for samme
`(tenant, hostname, formal)` → samme `runde_id` tilbake, aldri to `apen`
rader (målt med unikindeksen, ikke bare med returverdien); en utløpt `apen`
runde forkastes ved neste åpning i stedet for å blokkere ·
2m **Runden er bundet til challengeversjonen (§2.4a):** to observatører
melder inn H1, `utsted_challenge()` skriver H2, konsumenten kaller
`verifiser_domenekontroll(…, p_runde)` → `challenge_endret`, ingen rad blir
`verifisert`, og runden er `forkastet`. Motsatt: en revalideringsrunde åpnes
og forbrukes på dag 30, altså **etter** at `challenge_utloper` er passert →
lykkes, fordi TXT-posten fortsatt hasher riktig ·
2n **Runder ryddes (§2.4bb):** en `apen` runde som utløper uten to
observasjoner settes `forkastet` av `rydd_domeneobservasjonsrunder`; en
terminal runde eldre enn 30 døgn slettes med observasjonene sine, mens
`domenekontroll_hendelse` for samme forløp er urørt; batchgrensen holder
(`p_maks` rader per kjøring), og køspørringen bruker det partielle indekset
(målt i `EXPLAIN`, ikke antatt) ·
2o **Verifiseringsflaten finnes og virker (§2.5c):** en `domeneforvalter`
registrerer `example.com` (`POST /v1/domener`), får token én gang, kaller
verifiseringsruten → `202` med `runde_id`; to observatørprosesser melder
inn; nytt kall på samme rute → `200 verifisert`. Målt ende-til-ende gjennom
HTTP med browsersesjon, ikke ved å kalle SQL-funksjonen direkte. Negativt:
en `domeneavgjorer` nektes på registreringsruten, en `domeneforvalter`
nektes på attestasjonsruten, og tokenet vises aldri igjen ·
2p **De nye tenantbundne tabellene er faktisk RLS-lukket (§1/§2.5b):**
runtime-rollen setter `sett_kontekst` på tenant X og leser
`domenekonfliktpart` og `overtakelse_attestasjon` → null rader tilhørende
tenant Y, og et `INSERT` med `tenant = Y` avvises av `WITH CHECK`. Målt
med `disponit`-rollen over den faktiske forbindelsen, ikke som eier: en
tabell med grant og uten policy leverer alt, og en test som kjører som
migrator eller `disponit_domene_eier` ser aldri forskjellen ·
3 Tre døgn uten svar → attestasjon nektes; raden ikke slettet eller
`utlopt`-satt av arbeideren ·
4 Observatørkonfigurasjon uten diversitet (samme operatør, samme nett
eller samme DB-rolle) → oppstart nektes (deploy-port). **Den harde
varianten:** to *ulike* endepunkter hos **samme** operatør (`8.8.8.8` og
`8.8.4.4`, eller to aliaser for samme DoH-tjeneste) → nektet, selv om
endepunktstrengene er ulike; likeså manglende nøkkel, operatør utenfor
den lukkede lista, ASN som ikke stemmer med operatøroppføringen, og lik
motpart på operatør eller ASN. En test som bare sammenligner
endepunktstrenger består med begge observasjonene hos én operatør ·
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
10d **Scheduleren ser faktisk køen sin (§2.2b):** tre tenanter har
`verifisert`-rader; `disponit_domenerevalidator` kobler til over sin egen
DSN, kaller `hent_revalideringskandidater()` **uten** `sett_kontekst` og
får rader fra alle tre, med `n_verifisert` = totalen. Samme kall med
kontekst satt på én tenant gir fortsatt alle tre — funksjonen er
eierens, ikke kallerens. Negativt i samme port: rollen har hverken bord-
eller kolonnegrant på `domenekontroll` (direkte `SELECT` → `permission
denied`), får ingen `avklaring_kreves`-rad, og ser hverken
`challenge_token_hash` eller `konflikt_motpart`. Og med `p_grense` satt
lavere enn populasjonen er `n_verifisert` fortsatt totalen, mens
kappingen er logget — et `count(*)` over den kappede lista ville krympet
K nøyaktig når køen er lengst. Porten må kjøres som jobbrollen over
nettverket; en test som kjører som migrator eller superbruker består med
et kolonnegrant som i drift gir null rader.

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
20k **Oppgjøret snevrer inn nøyaktig det omstridte (§2.5b):** B vinner
namespace-tvisten mot A-s wildcard → A står fortsatt `verifisert` på
`example.com`, men med `wildcard = false`, bumpet generasjon og hendelsen
`wildcardscope_innsnevret`; B blir `verifisert` på `foo.example.com`.
Vinner derimot wildcard-parten over en eksakt-host-taper, blir taperraden
`tilbakekalt` med grunn `tapte_domeneoppgjor`. Begge deler i samme
transaksjon som tildelingen. `avvis` lar innehaveren stå **helt** urørt.
En implementasjon som tilbakekaller hele wildcard-raden feiler porten: det
ville gjort et delegert subdomene til en vei til å slå ut forelderens
autorisasjon.

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
`artefakt_id` returneres, ingen ny rad, ingen exception. Retryen må gå
gjennom **hele** opplastingsveien inkludert `innlos_artefaktkapabilitet`:
en preflight som sammenligner generasjonen også for `brukt` rader avviser
retryen før den idempotente grenen nås, og porten er da rød selv om
`lagre_artefakt_staged()` er riktig (§5) ·
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
observatør- og jobbrollene feiler ikke), begge observatørrollene,
`disponit_domenerevalidator` og `disponit_artefaktrydder` kan **logge inn
med DSN-en fra miljøfilen**, og `opp.sh` installerer og starter begge
timerne og begge observatørprosessene. Målt som `systemctl is-active` per
unit og én vellykket innlogging per rolle. En observatør startet med feil
DB-rolle, eller med samme resolveroperatør/ASN som den andre, **skal nekte
å starte**. Uten denne porten kan hele PR-015 bestå testsuiten og likevel
ikke revalidere ett eneste domene i drift.
28c **Domenerollen forblir uten innlogging (§6b).** Kjør
`oppsett-postgresql.sh` **to ganger** på en base der
`disponit_domains_admin` finnes fra PR-014b, og på en base der et tidligere
utkast ga den `LOGIN` med passord → begge ender med `rolcanlogin = false`,
en tilkobling som den rollen avvises, og begge timerne kjører likevel på
sine egne roller. Porten er dobbel: den måler at den brede adminrollen ikke
er en nettverksidentitet, **og** at jobbene ikke var avhengige av at den var
det ·
28d **Observatørcredentialene er isolert på OS-nivå (§6b).**
`sudo -u disponit-obs1 cat /etc/disponit/observator-2.env` →
`Permission denied`; samme vei motsatt; ingen av de fire jobbrollenes
miljøfiler er lesbare for de andre, og ingen av dem kan lese
`/etc/disponit/miljo`. Målt på filsystemet etter et fullt `opp.sh`, ikke i
unit-filene. To separate units under samme Unix-bruker består enhver test
som bare teller prosesser, og gir likevel ett kompromiss begge stemmene ·
28e **Eierskapsdesignet dekker 019 (§6d).** Kjør `eierskap-reparasjon.sql`
etter at 019 er anvendt, og mål paritet begge veier: hver funksjon og tabell
eid av `disponit_domene_eier` i basen har en rad i `_design`, og hver
`_design`-rad peker på et objekt som finnes (eller er dokumentert
transitorisk). Kjør deretter reparasjonen **på nytt** og mål at eierskapet
er uendret. Uten porten flytter neste `oppsett-postgresql.sh` de nye
SECURITY DEFINER-funksjonene til migrator — stille, og uten at 019 kjøres
om igjen for å rette det ·
28b **Rettighetene overlever deployet (§6c).** Kjør `migrer.py` **to
ganger** på samme base — altså gjennom en hel REVOKE-ALL-syklus etter at
019 er anvendt — og skriv så en attestasjon som `disponit`:
`overtakelse_attestasjon` må fortsatt kunne SELECT/INSERT/UPDATE-es.
Samme kjøring måler negativt: `disponit` har **ikke** INSERT på
`domeneobservasjon` eller `domeneobservasjonsrunde`. En test som bare
kjører migrasjonen én gang består selv med en inline GRANT som drift
vasker bort ved neste deploy.

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
**`avgjor_5arg_kallbar = nei`** ·
**`019_funksjon_kallbar_for_public = 0`** (alle nye signaturer, §2.4c) ·
**`navngitt_kaller_uten_execute = 0`** (samme liste, motsatt vei) ·
**`observator_finner_apen_runde = ja`** ·
**`observasjonsko_lekker_tenant = 0`** ·
**`observasjonsko_lesbar_for_arbeider_eller_api = 0`** ·
**`verifisering_uten_2_observatorer = 0`** ·
**`overtakelse_utlost_uten_observasjonsrunde = 0`** ·
**`to_tenanter_gyldige_for_samme_effektive_hostnavn = 0`** (målt på
`v_domeneautorisasjon`, med wildcard-scopen ekspandert ett nivå) ·
**`namespace_overlapp_endt_i_verifisert = 0`** ·
**`wildcardrad_tilbakekalt_av_subdomeneoppgjor = 0`** ·
**`sonelas_manglet_ved_samtidig_forelder_barn = 0`** ·
**`retning_feilklassifisert_pa_wildcard_barn = 0`** ·
**`uavgjort_motpart_igjen_etter_wildcardoppgjor = 0`** (alle rader fra
`sone_overlapp`, ikke bare den første) ·
**`oppgjor_pa_endret_konfliktbilde = 0`** ·
**`ordinaer_eksaktovertakelse_avvist_som_endret_bilde = 0`** (den
vanligste veien: A tilbakekalt ved verifiseringen, B godkjennes) ·
**`gjenoppstatt_eksaktmotpart_tildelt_over = 0`** ·
**`overlapp_fra_for_019_igjen_etter_migrasjon = 0`** ·
**`disjunkt_par_tilbakekalt_av_019_ryddingen = 0`** (eksakt forelder +
wildcard barn er IKKE overlapp) ·
**`ryddet_overlappsrad_uten_vei_tilbake = 0`** (reapplikasjon gir ny sak) ·
**`to_apne_runder_pa_samme_mal_og_formal = 0`** ·
**`runde_forbrukt_etter_challengebytte = 0`** ·
**`revalidering_nektet_pa_utlopt_challenge = 0`** ·
**`utlopt_runde_staende_apen_etter_rydding = 0`** ·
**`observasjonsrunde_beholdt_over_retensjon = 0`** (målt på en runde som
FIKK to observasjoner — det er den fremmednøkkelen fanger) ·
**`retensjonssletting_feilet_pa_fremmednokkel = 0`** ·
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
**`roller_med_domains_verify = {domeneforvalter}`** (nøyaktig) ·
**`nytt_domene_verifisert_over_http = ja`** (§2.5c, ende-til-ende) ·
**`verifiseringsrute_apnet_ny_runde_ved_retry = 0`** ·
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
**`jobbrolle_uten_login_eller_dsn = 0`** (revalidator + rydder) ·
**`nye_timere_installert_og_aktive = alle`** ·
**`attestasjonsskriv_etter_to_migrer_kjoringer = ok`** ·
**`runtime_har_skriv_pa_observasjonstabellene = 0`** ·
**`runtime_har_skriv_pa_domenekonfliktpart = 0`** ·
**`konfliktpart_lest_pa_tvers_av_tenant = 0`** (runtime-rollen, med
`sett_kontekst` på tenant X, ser ingen rad tilhørende tenant Y) ·
**`attestasjon_lest_pa_tvers_av_tenant = 0`** (samme måling) ·
**`revalideringsko_uten_tenantkontekst = alle verifiserte rader`**
(jobbrollen, ingen `sett_kontekst`; et kolonnegrant ville gitt 0) ·
**`revalidator_har_bord_eller_kolonnegrant = 0`** ·
**`N_krympet_av_kandidatkappingen = 0`** ·
**`migrasjon_019_pa_fersk_base_feiler = nei`** ·
**`domains_admin_kan_logge_inn = 0`** (fersk OG oppgradert base) ·
**`observator_kan_lese_annen_observators_miljofil = 0`** ·
**`019_objekt_uten_rad_i_eierskapsdesignet = 0`** ·
**`eid_funksjon_flyttet_til_migrator_av_reparasjonen = 0`** ·
**`observator_startet_med_delt_resolver = 0`** ·
**`observator_startet_med_delt_operator_eller_asn = 0`** ·
**`observatorkonfig_uten_operator_eller_asn_godtatt = 0`**.

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
         (oppgraderingsryddingen av eksisterende namespaceoverlapp FØRST,
          før gjerdet installeres, §2.5b +
          overtakelse_attestasjon m/saksbinding og fornyelse +
          domenekonfliktpart (flerpartsmengden, §2.5b) +
          domeneobservasjonsrunde m/challenge_token_hash, unikindeks på
          levende runde og køindeks + domeneobservasjon (ON DELETE CASCADE) +
          apne_domeneobservasjonsrunde (idempotent under sonelåsen) +
          hent_apne_observasjonsrunder + meld_domeneobservasjon +
          rydd_domeneobservasjonsrunder(p_maks) (§2.4bb) +
          utsted_challenge som forkaster åpne runder (§2.4a) +
          forelder_hostname + sone_overlapp m/retning fra HOSTNAVNENE +
          sonelåsen (§2.5b) +
          verifiser_domenekontroll(p_runde) MED overlappsgrenen +
          revalider_domenekontroll(p_runde) + avgjor_domeneovertakelse
          m/flerpartsoppgjør over HELE overlappsmengden, friskhetskrav,
          taperoppgjør og innsnevring av taperens wildcard-scope +
          rydd_staged_artefakter(p_maks) MED 016-s evidensfristledd +
          artefaktkapabilitet.owner_generation m/oppgraderingssekvens og
          validering i lagre_artefakt_staged (preflighten kun for `utstedt`) +
          hent_revalideringskandidater(p_grense) — køen som SECURITY
          DEFINER, fordi FORCE RLS gjør et kolonnegrant tomt (§2.2b) +
          RLS + FORCE + tenant-policy på overtakelse_attestasjon OG
          domenekonfliktpart (§1/§2.5b) +
          REVOKE/GRANT-blokka for hver ny signatur, GRANT av utsted_challenge
          til `disponit`, EXECUTE på køfunksjonen til revalidatoren, REVOKE
          av de tre gamle overloadene og DROP av rydd_staged_artefakter(),
          §2.4c),
         platform/core/api/autorisasjon.py (rollene `domeneavgjorer` (§4.1)
           og `domeneforvalter` (§2.5c) + `domains:read`),
         platform/drift/domenerevalidering.py, platform/drift/domeneobservator.py,
         platform/drift/artefaktrydding.py (rydder også observasjonsrunder),
         platform/core/api/domenekontroll.py (verifiseringsflaten: registrer,
           åpne/forbruke runde, les portefølje — §2.5c),
         platform/core/api/domeneovertakelse.py (saken opprettes `manuell`
           med grunnkode og policyref, §3; ruten som åpner oppgjørsrunden;
           behandleren `behandle_domeneattestasjon()` med domeneoppgjøret og
           reautoriseringen av hver talt attestant, §4.2b + §4.3),
         platform/core/api/unntaksbehandling.py (familiegjerdet på den
         generelle ruten + scopet fra saksfamilien under låsen, §4.2),
         platform/core/api/app.py (oppdrag_claim, linje ~717;
         artefaktopplasting, linje ~1928; `domains:adjudicate` OG
         `domains:verify` i BROWSER_MUTASJONSSCOPES, linje ~799; rutene
         POST /v1/domener, POST /v1/domener/{hostname}/verifisering,
         GET /v1/domener[/{hostname}] (§2.5c),
         POST /v1/domener/overtakelse/{unntak_id}/runde (§2.5c) og
         POST /v1/domener/overtakelse/{unntak_id}/attestasjon, §4.2),
         deploy/staging/oppsett-postgresql.sh (observatørrollene +
           `disponit_domenerevalidator` + `disponit_artefaktrydder` som LOGIN
           m/DSN, og et eksplisitt idempotent `ALTER ROLE
           disponit_domains_admin NOLOGIN`, §6b — uten rollene feiler 019 på
           en fersk base, og med en credentialed adminrolle er hele 016-
           grantsettet eksponert),
         deploy/staging/resolveroperatorer.json (lukket operatør/ASN-liste
           som observatørene validerer konfigurasjonen mot, §6b),
         deploy/staging/opp.sh (UNITS utvides med de fire nye unitene, og
           brukerløkka med fire nye systembrukere — én per jobb, egen
           0400-miljøfil, §6b),
         deploy/staging/migrer.py (RETTIGHETER utvides med
           `overtakelse_attestasjon` og SELECT på `domenekonfliktpart`,
           §6c — uten den vaskes 019-s grant bort av REVOKE-syklusen og
           første stemme feiler),
         deploy/staging/eierskap-reparasjon.sql (`_design` utvides med hver
           ny/erstattet 019-signatur og de nye eide tabellene, §6d — ellers
           flyttes de til migrator ved neste oppsett og 019 kjøres aldri om
           igjen for å rette det),
         deploy/staging/disponit-domenerevalidering.{service,timer},
         deploy/staging/disponit-artefaktrydding.{service,timer},
         deploy/staging/disponit-domeneobservator-{1,2}.service,
         docs/RUTINER.md (hvordan eier tildeler `domeneavgjorer` og
           `domeneforvalter`)
NESTE: Draft PR-014c (automatisk WCAG-kontroll) — første eiermodul på
       plattformen 014a/014b/015 bygde — Claude.ai
       — docs/pr/PR-014c-*.md
```
