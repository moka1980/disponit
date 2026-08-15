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

**019 er ikke bare en tabell.** Fire av portene under kan ikke håndheves
av kallere alene; de krever at 019 erstatter funksjoner fra 016/017.
016- og 017-*filene* er immutable (checksum), så 019 legger nye
`CREATE OR REPLACE`-versjoner og `ALTER TABLE ... ADD COLUMN` oppå dem:

| # | Objekt i 019 | Hvorfor det ikke kan ligge i kalleren |
|---|---|---|
| 1 | `overtakelse_attestasjon` (ny tabell) | Fire øyne, §4 |
| 2 | `revalider_domenekontroll(…, resolvere, observert_txt)` | Arbeideren skal ikke være autoritet, §2.4 |
| 3 | `rydd_staged_artefakter(p_maks INT)` | Batchgrense, §6 / port 25 |
| 4 | `artefaktkapabilitet.owner_generation` + validering | Fencing ved reclaim, §5 |

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
  hostname TEXT NOT NULL,
  avgitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, unntak_id, aktor),      -- én aktør, én stemme per sak
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id));
```
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

## 2. Resolverarbeider — kaller `revalider_domenekontroll()`

`disponit-domenerevalidering.timer`, hver time, egen Unix-bruker, rolle
`disponit_domains_admin`. **Arbeideren har ingen egen autoritet:** den
slår opp DNS og leverer *observasjonen* til funksjonen, som validerer den
(§2.4). Statusbeslutningen ligger i databasen.
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

**Evidensen sendes inn i databasen — regelen bor ikke i arbeideren.**
016/018-signaturen `revalider_domenekontroll(tenant, hostname, aktor)`
tar ingen observert TXT-verdi og ingen resolveridentiteter; den setter
`siste_vellykkede_revalidering = now()` på enhver verifisert rad. Siden
arbeideren har `disponit_domains_admin` (016 linje 929), kunne en
feilende eller kompromittert arbeider friske opp et hvilket som helst
verifisert domene uten å ha slått opp noe. Derfor legger 019:

```sql
revalider_domenekontroll(p_tenant TEXT, p_hostname TEXT, p_aktor TEXT,
                         p_resolvere TEXT[], p_observert_txt TEXT)
```
som under hostname-låsen krever, og ellers hever exception:
- `p_resolvere` inneholder **≥ 2 distinkte** identiteter (uenighet
  representeres ved at arbeideren ikke kaller med begge — men *antallet*
  er databasens krav, ikke arbeiderens løfte),
- `encode(sha256(convert_to(p_observert_txt,'UTF8')),'hex')` er lik
  radens `challenge_token_hash`. Klarteksten lagres fortsatt aldri;
  arbeideren beviser oppslaget ved å kunne reprodusere hashen.

Resolveridentitetene skrives på `domenekontroll_hendelse` som evidens.
3-argumentsversjonen **REVOKE-es fra `disponit_domains_admin`** i samme
migrasjon; ellers består den gamle, ubeviste veien ved siden av den nye.

- **≥2 uavhengige resolvere; uenighet → ikke vellykket revalidering.**
- **Diversitet er deploy-port:** minst to resolvere hos ulike operatører
  og ulike nett. Konfigurasjon som bryter det → oppstart nektes.
  Deploy-porten er det som gjør identitetene *uavhengige*; databasen
  teller dem, men kan ikke vite hvem som eier dem.
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

## 4. Fire øyne ved positiv tildeling

| Utfall | Krav | Hvorfor |
|---|---|---|
| **Avvis** (B → `tilbakekalt`) | **Én** attestasjon | Fail-closed; ingen får autorisasjon |
| **Godkjenn** (B → `verifisert`) | **To distinkte** attestasjoner | Etablerer hvilken kunde plattformen autoriserer |

- De to radene må ha identisk `(unntak_id, utfall, vinnende_tenant,
  hostname)`. Avvik → ingen avgjørelse, aldri en sammenslåing.
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
  reclaim øker `oppdrag.owner_generation` (015 linje 276) uten å endre
  tenant, oppdrag, modul, release, kontrakt, epoch eller artefakttype —
  hvert eneste felt i 017-bindingen forblir altså gyldig for den gamle
  eieren, og `artefaktkapabilitet` har ingen generasjonskolonne. Den gamle
  eieren kunne derfor lastet opp med sitt token helt frem til
  evidensfristen, etter at en annen arbeider hadde overtatt oppdraget.
  019 legger `owner_generation BIGINT NOT NULL` (og `owner_claim_id`) på
  `artefaktkapabilitet`, stempler den ved utstedelse under oppdragslåsen,
  og `innlos_artefaktkapabilitet` **avviser** en kapabilitet hvis
  generasjon er lavere enn oppdragets nåværende. Kolonnene er
  bindingsfelter — uforanderlige via `artefaktkapabilitet_statusmaskin()`.
  Dette er en **egen port** (24b), ikke bare en utvidet negativ test:
  uten kolonnen finnes ingen stale/fencing-garanti å teste.

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
| Revalidering | Én timer, én arbeidernøkkel; manuell kjøring tar samme lås; 3-argumentsversjonen REVOKE-et | Advisory-lås; K som `LIMIT`; avledet plan | Funksjonen validerer TXT-hash og ≥2 distinkte resolvere; arbeideren setter aldri status | Kaller kun `revalider_domenekontroll(…, resolvere, txt)` |
| Overtakelsesavgjørelse | Kun PR-012-attestasjon → funksjonen | Hostname-lås; én sak per konflikt­generasjon; ny konflikt = ny `unntak_id` | To distinkte aktører med `domains:adjudicate`, identisk utfall | Funksjonens enum; PK `(tenant, unntak_id, aktor)` hindrer dobbeltstemme |
| Opplastingskapabilitet | Kun `POST /v1/oppdrag/claim` | Epoch **og `owner_generation`** under oppdragslåsen | Bundet til serverkontekst, ikke modulens ønske | Ingen registrert artefakttype → tom liste, ingen kapabilitet |
| Rydding | Én timer, funksjonens positive regel | Batchgrense i funksjonen (`LIMIT p_maks`) + idempotens | Karantene bevares på egenskap, ikke på alder | Kaller kun `rydd_staged_artefakter(500)` |

## 8. Codex-porter

**Revalidering (1–10).**
1 To samtidige kjøringer → én kjører, én venter ·
2 Uenige resolvere → ikke vellykket, `siste_vellykkede_revalidering` urørt ·
2b **Kall med kun én resolveridentitet, eller med en TXT-verdi som ikke
hasher til `challenge_token_hash` → exception, tidsstempel urørt** — også
når kalleren har `disponit_domains_admin`; og 3-argumentsversjonen er
ikke lenger kallbar for arbeiderrollen ·
3 Tre døgn uten svar → attestasjon nektes; raden ikke slettet eller
`utlopt`-satt av arbeideren ·
4 Resolverkonfigurasjon uten diversitet → oppstart nektes (deploy-port) ·
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
`uenige_resolvere_avvist = alle` ·
**`revalidering_uten_gyldig_txt_hash = 0`** ·
**`revalidering_med_under_2_resolvere = 0`** ·
**`revalider_3arg_kallbar_for_arbeider = nei`** ·
`godkjenn_med_en_attestasjon = 0` · `samme_aktor_to_stemmer = 0` ·
`attestasjon_pa_annen_sak_talt = 0` ·
`saker_per_konfliktgenerasjon ≤ 1` · `kjede_abc.a_gjenoppstatt = 0` ·
**`tidsbasert_utvei_fra_avklaring = 0`** ·
`avgjorelse_uten_scope_nektet = alle` ·
`tokens_distinkte = ja` · `uten_artefakttype_utstedt = 0` ·
**`kapabilitet_per_registrert_type = alle`** ·
**`opplasting_med_foreldet_owner_generation = 0`** ·
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
       resolverarbeider, M-37-kobling, kapabilitetsutstedelse ved claim,
       ryddetimere — Claude Code
       — platform/core/db/migrations/019_overtakelse_attestasjon.sql
         (tabell + revalider_domenekontroll m/evidens +
          rydd_staged_artefakter(p_maks) + artefaktkapabilitet.owner_generation),
         platform/drift/domenerevalidering.py, platform/drift/artefaktrydding.py,
         platform/core/api/domeneovertakelse.py,
         platform/core/api/app.py (oppdrag_claim, linje ~717)
NESTE: Ren docs-rettelse av migrasjonsnummer (014 → 016/017) i de tre
       014b-dokumentene; egen PR, porten hoppes over med begrunnelse
       — Claude Code — docs/PR-014b-*.md
```
