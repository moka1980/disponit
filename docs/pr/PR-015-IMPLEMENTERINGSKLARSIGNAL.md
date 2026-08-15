# PR-015 — IMPLEMENTERINGSKLARSIGNAL (GO, operativt lag)

**Til Claude Code · Konsolidert spesifikasjon + v2 + v3. Deltaformen
forlates. Branch: `pr-015-operativt-lag`.
Forutsetning: PR-014b merget (migrasjon 016 + 017 på main).**

**Hva dette er:** 014b definerte funksjonene. PR-015 er kallerne — pluss
én ny tabell som fire-øyne-kontrakten krever. Ingen WCAG-logikk. Ingen
endring på 016/017.

---

## 1. DDL (migrasjon 018) — autoritativ, eneste nye tabell

```sql
-- Fire øyne ved positiv cross-tenant domenetildeling (§4)
CREATE TABLE overtakelse_attestasjon (
  sak_id UUID NOT NULL,
  saksrevisjon BIGINT NOT NULL,        -- foreldes ved ny konflikt, §4
  aktor TEXT NOT NULL,
  utfall TEXT NOT NULL CHECK (utfall IN ('godkjenn','avvis')),
  vinnende_tenant TEXT NOT NULL,
  hostname TEXT NOT NULL,
  avgitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (sak_id, saksrevisjon, aktor));   -- én aktør, én stemme per revisjon
```
Append-only (trigger: ingen UPDATE, ingen DELETE) — også foreldede
attestasjoner er evidens for at noen attesterte et utfall som ble
foreldet. RLS + FORCE. `sett_kontekst` først på alle veier inn.
`domenekontroll.saksrevisjon` økes av `verifiser_domenekontroll()` under
hostname-låsen (016-funksjonen kalles, ikke endres).

## 2. Resolverarbeider — kaller `revalider_domenekontroll()`

`disponit-domenerevalidering.timer`, hver time, egen Unix-bruker, rolle
`disponit_domains_admin`. **Arbeideren har ingen egen autoritet:** den
slår opp DNS og kaller funksjonen. Statusbeslutningen ligger i databasen.
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

- **≥2 uavhengige resolvere; uenighet → ikke vellykket revalidering.**
- **Diversitet er deploy-port:** minst to resolvere hos ulike operatører
  og ulike nett. Konfigurasjon som bryter det → oppstart nektes.
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
- **Én åpen sak per hostname** (UNIQUE på ikke-terminal
  `domeneovertakelse`-sak). Ny konflikt på hostname med åpen sak → samme
  sak, ny hendelse. Terminal sak gjenbrukes aldri.
- Saksvisningen viser det databasen kan bevise: hvem som besto challenge
  når, hvem som mistet autorisasjonen, og at A er stoppet uansett utfall.
  Den anslår ikke hvem som «egentlig» eier domenet.

**A→B→C:** hostname-låsen serialiserer uansett antall parter. C overtar
B-s plass i den åpne saken (B → `tilbakekalt`, C → `avklaring_kreves`, ny
hendelse). **Ingen tenant blir `verifisert` av at en annen taper** — A
gjenoppstår ikke. ≥3 parter innen 24 t → `hoy_konfliktrate` på saken;
det stopper ingenting automatisk.

## 4. Fire øyne ved positiv tildeling

| Utfall | Krav | Hvorfor |
|---|---|---|
| **Avvis** (B → `tilbakekalt`) | **Én** attestasjon | Fail-closed; ingen får autorisasjon |
| **Godkjenn** (B → `verifisert`) | **To distinkte** attestasjoner | Etablerer hvilken kunde plattformen autoriserer |

- De to radene må ha identisk `(saksrevisjon, utfall, vinnende_tenant,
  hostname)`. Avvik → ingen avgjørelse, aldri en sammenslåing.
- **Ingen enkelt aktør produserer begge** — håndhevet av primærnøkkelen,
  ikke av UI-et. Begge krever `domains:adjudicate`.
- **Ny konflikt invaliderer ventende attestasjoner:** overtar C, økes
  `saksrevisjon` i samme transaksjon som overtakelsen, og B-attestasjonen
  kan aldri telle mot C-utfallet. Radene bevares.
- **Motoren beslutter:** `avgjor_domeneovertakelse()` teller
  attestasjonene under hostname-låsen og gjør overgangen.
- **Én autorisert aktør → positiv tildeling er umulig.** Riktig
  fail-closed, men det skal *sies*: feilkode `krever_to_attestasjoner`
  med antall autoriserte aktører, oversatt i UI. Den ærlige utveien: når
  A-s 90-døgnsvindu løper ut, verifiserer B på nytt uten konflikt.

## 5. Opplastingskapabilitet utstedes ved claim

- **Utstedes av `POST /v1/oppdrag/claim`** sammen med
  kvitteringskapabiliteten, som **separat token** — aldri utledet av den,
  aldri samme audience. Ikke noe nytt on-demand-endepunkt i v1.
- **Bindingen er serverkontekstens:** `tenant · oppdrag_id · modul_id ·
  release_id · kontraktversjon · kontrakt_hash · module_epoch ·
  artefakttype`. Modulen ber ikke om felt; den mottar et token.
- **`artefakttype` hentes fra `artefakttype_register`.** Finnes ingen
  registrert artefakttype → **ingen opplastingskapabilitet**, og claim
  lykkes fortsatt. En modul som ikke skal laste opp, får ikke lov.
- **Levetid = evidensfristen for oppdraget**, aldri lengre.
- **Epoch kontrolleres under oppdragslåsen** ved utstedelse.
- Ved reclaim følger gammelt opplastingstoken samme stale/fencing-
  semantikk som resten av claim-kjeden (014a V2) — dekket av den utvidede
  negative kapabilitetstesten, ikke av en ny port.

## 6. Ryddetimer

`disponit-artefaktrydding.timer`, hvert 15. minutt, kaller
`rydd_staged_artefakter()`. Timeren legger **ingen logikk oppå** den
positive regelen (`staged` > 24 t **og** uten refererende kvittering,
inkludert karantenesatt).
- **Batchgrense 500 per kjøring**, så opphopning ikke låser tabellen i én
  transaksjon.
- **Karantenesatt evidens telles og rapporteres, aldri ryddes.**
- To sammenhengende feilede kjøringer → alarm. En stille ryddejobb er en
  voksende disk.

## 7. De fire portspørsmålene

| Kontroll | Alle veier inn? | Samtidighet? | Riktig vs. velformet? | Lukket format? |
|---|---|---|---|---|
| Revalidering | Én timer, én arbeidernøkkel; manuell kjøring tar samme lås | Advisory-lås; K som `LIMIT`; avledet plan | ≥2 uenige resolvere → ikke vellykket; arbeideren setter aldri status | Kaller kun `revalider_domenekontroll()` |
| Overtakelsesavgjørelse | Kun PR-012-attestasjon → funksjonen | Hostname-lås; én åpen sak per hostname; revisjon foreldes atomisk | To distinkte aktører med `domains:adjudicate`, identisk utfall | Funksjonens enum; PK hindrer dobbeltstemme |
| Opplastingskapabilitet | Kun `POST /v1/oppdrag/claim` | Epoch under oppdragslåsen | Bundet til serverkontekst, ikke modulens ønske | Ingen registrert artefakttype → ingen kapabilitet |
| Rydding | Én timer, funksjonens positive regel | Batch + idempotens | Karantene bevares på egenskap, ikke på alder | Kaller kun `rydd_staged_artefakter()` |

## 8. Codex-porter

**Revalidering (1–10).**
1 To samtidige kjøringer → én kjører, én venter ·
2 Uenige resolvere → ikke vellykket, `siste_vellykkede_revalidering` urørt ·
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
16 To attestasjoner med ulikt `vinnende_tenant` eller ulik revisjon →
ingen avgjørelse ·
17 C overtar med B-attestasjon inne → `saksrevisjon` økt, B-attestasjonen
teller ikke, raden bevart ·
18 Avvis med én attestasjon → B `tilbakekalt`; tenant med én autorisert
aktør får legibel feilkode, ikke stillhet ·
19 Ny konflikt på hostname med åpen sak → samme sak, ny hendelse; terminal
sak + ny konflikt → ny sak, terminal urørt ·
20 A→B→C: hver overtakelse tilbakekaller forrige, kun C
`avklaring_kreves`, A gjenoppstår ikke; ≥3 parter innen 24 t →
`hoy_konfliktrate`.

**Kapabilitet (21–24).**
21 Claim returnerer to distinkte tokens; opplastingstokenet virker ikke
som kvittering og motsatt ·
22 Oppdrag uten registrert artefakttype → claim OK, ingen
opplastingskapabilitet ·
23 Levetid > evidensfrist → utstedelse avvist ·
24 Epoch endret mellom claim og utstedelse → ingen kapabilitet.
Utvidet: eksisterende negative reclaim-/fencing-test dekker nå også
opplastingstokenet.

**Rydding (25–27).**
25 600 kandidater → to batcher, idempotent, ingen låsing over grense ·
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
`godkjenn_med_en_attestasjon = 0` · `samme_aktor_to_stemmer = 0` ·
`attestasjon_pa_foreldet_revisjon_talt = 0` ·
`apne_saker_per_hostname ≤ 1` · `kjede_abc.a_gjenoppstatt = 0` ·
`avgjorelse_uten_scope_nektet = alle` ·
`tokens_distinkte = ja` · `uten_artefakttype_utstedt = 0` ·
`levetid_over_frist_avvist = alle` ·
`karantene_bevart = alle` · `idempotens_kjoring2_slettet = 0`.

**Målte egenskaper (rapporteres, ingen bestått/ikke bestått):**
`fordeling.maks_andel_per_time` for testpopulasjonen ·
`sikkerhetsnett.kjoringer_over_K` · `dreneringstid_timer` ved 6 t outage ·
`alarm.terskel_utlost`.

Et sjekklistepunkt uten definert, målbar grense regnes som `nei`.

---

```
NÅ:    Implementer PR-015 mot dette klarsignalet — migrasjon 018,
       resolverarbeider, M-37-kobling, kapabilitetsutstedelse ved claim,
       ryddetimere — Claude Code
       — platform/core/migrasjoner/018_overtakelse_attestasjon.sql,
         platform/drift/domenerevalidering.py, platform/drift/artefaktrydding.py,
         api/domeneovertakelse.py, api/oppdrag_claim.py
NESTE: Ren docs-rettelse av migrasjonsnummer (014 → 016/017) i de tre
       014b-dokumentene; egen PR, porten hoppes over med begrunnelse
       — Claude Code — docs/PR-014b-*.md
```
