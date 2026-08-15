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
| 1 | `overtakelse_attestasjon` (ny tabell) + `overtakelse_attestasjon_saksbinding()` (trigger) | Fire øyne, §4; målet må bindes til saken, §1 |
| 2 | `revalideringsrunde` + `revalideringsobservasjon` (nye tabeller) | Observasjonen må bæres av databasen, ikke av kalleren, §2.4 |
| 3 | `meld_domeneobservasjon(runde, observert_txt)` (ny) | Observatøridentiteten er `session_user`, aldri en parameter, §2.4 |
| 4 | `revalider_domenekontroll(…, p_runde UUID)` | Arbeideren skal ikke være autoritet, §2.4 |
| 5 | `rydd_staged_artefakter(p_maks INT)` | Batchgrense, §6 / port 25 |
| 6 | `artefaktkapabilitet.owner_generation` + `.owner_claim_id`, med oppgraderingssekvens | Fencing ved reclaim, §5 |
| 7 | `artefaktkapabilitet_statusmaskin()` + `utsted_artefaktkapabilitet()` + `innlos_artefaktkapabilitet()` + `lagre_artefakt_staged()` | Generasjonen må stemples ved utstedelse og valideres i den ATOMISKE forbrukeren, §5 |
| 8 | `avgjor_domeneovertakelse(…)` — flerpartsoppgjør | Én godkjenning må avvise de øvrige avklaringsradene i samme transaksjon, §3 |

---

## 1. DDL (migrasjon 019) — autoritativ

```sql
-- Fire øyne ved positiv cross-tenant domenetildeling (§4)
CREATE TABLE overtakelse_attestasjon (
  tenant TEXT NOT NULL,                -- RLS-nøkkel; tenanten saken tilhører
  unntak_id BIGINT NOT NULL,           -- M-37-saken (unntak.id er BIGINT identity)
  aktor TEXT NOT NULL,
  utfall TEXT NOT NULL CHECK (utfall IN ('godkjenn','avvis')),
  vinnende_tenant TEXT NOT NULL,
  hostname TEXT NOT NULL CHECK (er_kanonisk_hostname(hostname)),
  forventet_generasjon BIGINT NOT NULL,
  avgitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, unntak_id, aktor),      -- én aktør, én stemme per sak
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id));
```

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

Append-only (trigger: ingen UPDATE, ingen DELETE) — også attestasjoner på
en sak som ble forbigått av en ny konflikt er evidens for at noen
attesterte et utfall. RLS + FORCE. `sett_kontekst` først på alle veier
inn.

## 2. Revalideringsarbeider — planlegger, observerer ikke

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

| # | Kø | Regel |
|---|---|---|
| **1** | **Sikkerhetsnett** — `siste_vellykkede_revalidering < now() - 26 t` | **Utenfor budsjettet. Aldri utsatt, aldri kappet** |
| 2 | **Normalslott** — minuttet falt i vinduet, raden ≥ 20 t gammel | Innenfor budsjettet |
| 3 | **Etterslep** — slott passert mens timeren var nede, eldste først | Budsjettet som er igjen etter kø 2 |

Kø 1 er ubegrenset *rett til å bli plukket*, ikke ubegrenset arbeid:
oppslagene kjøres med **fast samtidighetsgrense C = 8**. Ingen rad
droppes. Overskrider kø 1 budsjettet, er det en **målt hendelse**, ikke en
feil.

### 2.2 Absolutt budsjett

```
N = antall rader med status IN ('verifisert','avklaring_kreves')
K = ceil(0.10 * N)      -- HARDT tak per kjøring for kø 2 + kø 3 samlet
```
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
CREATE TABLE revalideringsrunde (
  runde_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant TEXT NOT NULL, hostname TEXT NOT NULL CHECK (er_kanonisk_hostname(hostname)),
  apnet TIMESTAMPTZ NOT NULL DEFAULT now(),
  utloper TIMESTAMPTZ NOT NULL,           -- kort, f.eks. now() + 5 min
  status TEXT NOT NULL DEFAULT 'apen' CHECK (status IN ('apen','brukt','forkastet')));

CREATE TABLE revalideringsobservasjon (
  runde_id UUID NOT NULL REFERENCES revalideringsrunde (runde_id),
  observator TEXT NOT NULL,               -- session_user, satt av funksjonen
  txt_hash TEXT NOT NULL,                 -- sha256, beregnet I databasen
  observert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (runde_id, observator));    -- én observatør, én observasjon
```

| Funksjon | Kalles av | Håndhever |
|---|---|---|
| `apne_revalideringsrunde(tenant, hostname)` → `runde_id` | arbeideren (`disponit_domains_admin`) | Raden må være `verifisert`; kort TTL; runden er engangs |
| `meld_domeneobservasjon(runde_id, observert_txt)` | **observatørrollene** `disponit_domeneobservator_*` | `observator := session_user`; hashen beregnes i DB og må være lik radens `challenge_token_hash`; runden må være `apen` og ikke utløpt |
| `revalider_domenekontroll(tenant, hostname, aktor, p_runde UUID)` | arbeideren | Under hostname-låsen: runden er `apen`, ikke utløpt, gjelder dette `(tenant, hostname)`; **≥ 2 observasjoner fra distinkte `observator`**, alle med samme `txt_hash`; så settes tidsstemplet og runden merkes `brukt` |

Det som er vunnet: arbeideren har **ikke** EXECUTE på
`meld_domeneobservasjon`, og kan ikke skrive `revalideringsobservasjon`
direkte. Identiteten er `session_user`, altså Postgres' egen
autentisering (klientsertifikat/passord per observatørprosess) — ikke en
streng kalleren velger. En kompromittert arbeider kan derfor åpne runder
den vil og kalle funksjonen så ofte den vil, men uten to ekte
observatørprosesser som *hver for seg* har slått opp TXT-posten og meldt
den inn i samme runde, skjer ingenting. Klarteksten lagres fortsatt aldri;
det er hashen som bæres. Observatøridentitetene skrives på
`domenekontroll_hendelse` som evidens.

3-argumentsversjonen **REVOKE-es fra `disponit_domains_admin`** i samme
migrasjon; ellers består den gamle, ubeviste veien ved siden av den nye.

**Restrisikoen, sagt rett ut:** en kompromittert *observatør* kan lyve om
hva dens resolver svarte, og to samvirkende observatører kan forfalske en
runde. Databasen kan ikke selv slå opp DNS og har ingen måte å avgjøre
det på. Det som begrenser skaden er at (a) diversitetsporten under
plasserer observatørene hos ulike operatører, i ulike nett og som ulike
prosesser med hver sin DB-credential, så én kompromittert vert gir én
stemme, og (b) `revalider_domenekontroll` **kan bare friske opp en rad
som allerede er `verifisert`** — den kan aldri opprette en
domeneautorisasjon, aldri løfte en status og aldri avgjøre en konflikt.
Maksimal effekt av et fullstendig kompromiss er altså at et domene som en
gang var verifisert holdes kunstig ferskt til en operatør tilbakekaller
det. Det er en akseptert, navngitt restrisiko — ikke en lukket port.

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
- **Scope `domains:adjudicate`**, eget. `exceptions:handle` alene gir
  aldri cross-tenant domeneautoritet.
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

**Det krever at 019 erstatter `avgjor_domeneovertakelse()` (objekt 8).**
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
                         p_godkjent, p_aktor)
  1. lås hostname; les p_tenant-raden FOR UPDATE
  2. status må være 'avklaring_kreves' og generasjonen må stemme (uendret fra 018)
  3. p_godkjent:
       - vinneren settes 'verifisert', generasjon++, nytt 90-døgnsvindu
       - hostname_binding settes til p_tenant  (den FLYTTES, den forutsettes ikke)
       - HVER ANNEN rad på p_hostname med status 'avklaring_kreves' settes
         'tilbakekalt' med grunn 'tapte_domeneoppgjor', generasjon++, og en
         hendelse per rad — i SAMME transaksjon, under samme lås
  4. NOT p_godkjent: kun p_tenant-raden → 'tilbakekalt' (uendret fra 018);
       de øvrige avklaringsradene blir stående, tvisten er ikke avgjort
```
Bindingssjekken fra 018 faller altså bort som *forutsetning* og blir en
*konsekvens*: det er avgjørelsen som utpeker bindingshaveren, ikke
bindingshaveren som avgjør hvem som kan avgjøres. Gjerdet mot en
foreldet sak ligger fortsatt i generasjonen, som leses under radlåsen,
og nå også i saksbindingen fra §1 — det er de to som gjør at en gammel
attestasjon ikke kan autorisere en ny konflikt.

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
  ikke av UI-et. Begge krever `domains:adjudicate`.
- **Ny konflikt invaliderer ventende attestasjoner** i kraft av
  saksidentiteten: C-s konflikt får sin egen `unntak_id` (ny generasjon,
  §1), og en attestasjon avgitt på B-s sak bærer B-s `unntak_id` i
  primærnøkkelen. Den kan derfor ikke telles av en avgjørelse på C-s sak
  — ingen revisjonsteller å øke, ingen `saksrevisjon` som må holdes i
  synk. Radene bevares.
- **Motoren beslutter:** `avgjor_domeneovertakelse()` teller
  attestasjonene under hostname-låsen og gjør overgangen.
- **Én autorisert aktør → positiv tildeling er umulig, permanent.**
  Riktig fail-closed, men det skal *sies* helt: feilkode
  `krever_to_attestasjoner` med antall autoriserte aktører, oversatt i UI,
  **og med den eneste faktiske utveien: tenanten må gi
  `domains:adjudicate` til en aktør til.**

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
  erstattes i samme migrasjon (objekt 7). Dette er en **egen port**
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
positive regelen (`staged` > 24 t **og** uten refererende kvittering,
inkludert karantenesatt).
- **Batchgrensen ligger i funksjonen, ikke i timeren.** 016-versjonen
  (linje 856) gjør én ubegrenset `UPDATE` over alle kvalifiserte rader og
  returnerer kun antallet — og timeren er her uttrykkelig forbudt å legge
  logikk oppå. Med 600 kandidater ville første kjøring altså tatt alle 600
  i én transaksjon, stikk i strid med port 25 og med selve grunnen til at
  grensen finnes. 019 erstatter den med
  `rydd_staged_artefakter(p_maks INT DEFAULT 500)`, som velger radene med
  `... ORDER BY opprettet LIMIT p_maks FOR UPDATE SKIP LOCKED` og
  oppdaterer kun dem. **Batchgrense 500 per kjøring**, så opphopning ikke
  låser tabellen i én transaksjon. Returverdien er fortsatt antallet
  ryddede rader, så «to sammenhengende feilede kjøringer → alarm» er
  uendret.
- **Karantenesatt evidens telles og rapporteres, aldri ryddes.**
- To sammenhengende feilede kjøringer → alarm. En stille ryddejobb er en
  voksende disk.

## 7. De fire portspørsmålene

| Kontroll | Alle veier inn? | Samtidighet? | Riktig vs. velformet? | Lukket format? |
|---|---|---|---|---|
| Revalidering | Én timer, én arbeidernøkkel; manuell kjøring tar samme lås; 3-argumentsversjonen REVOKE-et | Advisory-lås; K som `LIMIT`; avledet plan; runden er engangs | Observasjonene skrives av observatørrollene selv (`session_user`), hashes i DB; arbeideren har ikke EXECUTE på `meld_domeneobservasjon` og setter aldri status | Kaller kun `apne_revalideringsrunde()` + `revalider_domenekontroll(…, p_runde)` |
| Overtakelsesavgjørelse | Kun PR-012-attestasjon → funksjonen | Hostname-lås; hele oppgjøret i én transaksjon; én sak per konflikt­generasjon; ny konflikt = ny `unntak_id` | Målet bevist mot sakens egen idempotensnøkkel (§1); to distinkte aktører med `domains:adjudicate`, identisk utfall | Funksjonens enum; PK `(tenant, unntak_id, aktor)` hindrer dobbeltstemme |
| Opplastingskapabilitet | Kun `POST /v1/oppdrag/claim` | Epoch **og `owner_generation`** under oppdragslåsen ved utstedelse; generasjonen sjekkes på nytt under kapabilitetens radlås i `lagre_artefakt_staged()` | Bundet til serverkontekst, ikke modulens ønske | Ingen registrert artefakttype → tom liste, ingen kapabilitet |
| Rydding | Én timer, funksjonens positive regel | Batchgrense i funksjonen (`LIMIT p_maks`) + idempotens | Karantene bevares på egenskap, ikke på alder | Kaller kun `rydd_staged_artefakter(500)` |

## 8. Codex-porter

**Revalidering (1–10).**
1 To samtidige kjøringer → én kjører, én venter ·
2 Uenige observatører (ulik `txt_hash` i samme runde) → ikke vellykket,
`siste_vellykkede_revalidering` urørt ·
2b **Runde med kun én observasjon, eller med en TXT-verdi som ikke hasher
til `challenge_token_hash` → exception, tidsstempel urørt** — også når
kalleren har `disponit_domains_admin`; og 3-argumentsversjonen er ikke
lenger kallbar for arbeiderrollen ·
2c **Arbeiderrollen forsøker å melde en observasjon selv** — direkte
`INSERT` i `revalideringsobservasjon` og kall til
`meld_domeneobservasjon()` → begge nektet på rettigheter; en
observatørrolle som melder inn kan **ikke** oppgi en annen identitet enn
sin egen (`observator` er `session_user`, ikke et argument) ·
2d **Runden er engangs og kortlevd:** samme `runde_id` brukt to ganger →
andre kall avvist; utløpt runde → avvist; runde åpnet for ett
`(tenant, hostname)` og brukt mot et annet → avvist ·
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

**Alarm (11).** 11 Bred resolverfeil → én driftsalarm, null M-37-saker, og
`tenant X / hostname Y` fortsatt individuelt synlig med tre døgn uten
suksess.

**M-37 og fire øyne (12–20).**
12 Overtakelsessak synlig i PR-012-flaten med begge hostnames og lineage ·
13 Avgjørelse uten `domains:adjudicate` → nektet, selv med
`exceptions:handle` ·
14 Godkjenn med én attestasjon → nektet med `krever_to_attestasjoner` ·
15 Samme aktør to ganger → avvist av primærnøkkel, ikke av UI ·
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
fortsatt åpen.

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
26 Karantenesatt artefakt eldre enn 24 t → bevart, telt i
`karantene_bevart` ·
27 To feilede ryddekjøringer → alarm.

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
**`revalider_3arg_kallbar_for_arbeider = nei`** ·
`godkjenn_med_en_attestasjon = 0` · `samme_aktor_to_stemmer = 0` ·
`attestasjon_pa_annen_sak_talt = 0` ·
**`attestasjon_med_mal_utenfor_saken = 0`** ·
`saker_per_konfliktgenerasjon ≤ 1` · `kjede_abc.a_gjenoppstatt = 0` ·
**`forbigatt_part_kan_godkjennes = ja`** ·
**`taperrader_igjen_i_avklaring_etter_godkjenn = 0`** ·
**`tidsbasert_utvei_fra_avklaring = 0`** ·
`avgjorelse_uten_scope_nektet = alle` ·
`tokens_distinkte = ja` · `uten_artefakttype_utstedt = 0` ·
**`kapabilitet_per_registrert_type = alle`** ·
**`opplasting_med_foreldet_owner_generation = 0`** (målt på
`lagre_artefakt_staged`, med reclaimet committet ETTER preflighten) ·
**`utstedt_kapabilitet_uten_generasjon_etter_019 = 0`** ·
**`migrasjon_019_feiler_pa_eksisterende_rader = nei`** ·
`levetid_over_frist_avvist = alle` ·
**`ryddebatch_over_p_maks = 0`** ·
`karantene_bevart = alle` · `idempotens_kjoring2_slettet = 0`.

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
         (overtakelse_attestasjon m/saksbinding + revalideringsrunde og
          revalideringsobservasjon + meld_domeneobservasjon +
          revalider_domenekontroll(p_runde) + avgjor_domeneovertakelse
          m/flerpartsoppgjør + rydd_staged_artefakter(p_maks) +
          artefaktkapabilitet.owner_generation m/oppgraderingssekvens og
          validering i lagre_artefakt_staged),
         platform/drift/domenerevalidering.py, platform/drift/domeneobservator.py,
         platform/drift/artefaktrydding.py,
         platform/core/api/domeneovertakelse.py,
         platform/core/api/app.py (oppdrag_claim, linje ~717;
         artefaktopplasting, linje ~1928)
NESTE: Ren docs-rettelse av migrasjonsnummer (014 → 016/017) i de tre
       014b-dokumentene; egen PR, porten hoppes over med begrunnelse
       — Claude Code — docs/PR-014b-*.md
```
