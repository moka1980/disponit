# PR-013 — IMPLEMENTERINGSKLARSIGNAL (GO, policyadministrasjon v1)

**Til Claude Code · Konsolidert v1–v5. Branch: `pr-013-policyadmin`.
GO + ti vilkår i PR-beskrivelsen. Siste punkt i Eiers rekkefølge.**

## Bærende prinsipp
Å endre policy er å endre agentens fullmakter. Derfor samme strenghet som
kodeutrulling: **aktive versjoner er uforanderlige · aktivering er atomisk
og reversibel · ingen aktivering uten menneskelig godkjenning · ingen
godkjenning uten sett diff · utvidelse av fullmakt er egen risikoklasse.**

## De ti bindende vilkårene

### V1. `policy_hode` er ankerrad og eneste aktiv-autoritet
Alle aktiveringer, også den første, låser `policy_hode` FØRST.
`aktiv_versjon` er autoriteten motoren leser (`NULL` → `DENY_ALL_V1`),
bundet med kompositt-FK (DEFERRABLE) til `policyer`.
`policyer.aktiv` er AVLEDET og trigger-håndhevet: `aktiv=true` ⇔
`versjon = policy_hode.aktiv_versjon`. Versjonsallokering under låsen;
unikbrudd er aldri normalvei. Låserekkefølge: `policy_hode → policyer →
policyutkast → aktiveringsrunde → aktiveringsattestasjon`.

### V2. `KLASSIFIKATOR_V1` — uttømmende, skjemadrevet, fail-closed
Én-til-én-mapping fra HVER muterbar leaf-path i skjema v0.2 til én regel.
Ingen wildcard. Ukjent sti/type/skjema → **UTVIDER**. Mengde vs. ordnet
deklarert i skjemaet. Samlet klasse = strengeste enkeltendring.
**Frekvens:** INNSNEVRER kun ved uendret vindu+scope og redusert `maks`;
alt annet UTVIDER (burst-fullmakt, ikke gjennomsnittsrate).
**Tidsvindu:** mengdeinklusjon over kanonisk uke i lokale ukeminutter;
vårskifte fail-closed; høstskifte dekker begge `fold`; tidssoneendring →
UTVIDER. **Delt tidskodevei med motoren** — klassifikatoren kaller aldri
motorens beslutningsfunksjon rekursivt.

### V3. `metadata` isoleres strukturelt
Felt uten semantikk ligger i `metadata`, som **fjernes før policyen sendes
til motoren**. Ethvert felt som når motorens semantiske objekt uten bevist
monotoniregel → UTVIDER. Ingen grep-basert nøytralitetsbevis.

### V4. Diff og klasse rekalkuleres fra låste rader
Ved aktivering: lås → kanoniser begge (JCS) → beregn diff OG risikoklasse
på nytt → sammenlign mot rundens bundne verdier → fastslå godkjennerkrav →
aktiver. **Ingen caller kan sende diff, klasse eller antall godkjennere.**

### V5. Runde og attestasjon binder klassifiseringen
`diff_hash · risikoklasse · klassifikatorversjon · klassifisering_hash ·
policyskjema_versjon · motor_semantikkversjon · deny_all_hash/-versjon ·
pakrevd_antall_godkjennere`. Avvik ved rekalkulering → runde kansellert.
Konvolutt `disponit_policy_activation_v1` (full feltliste i v2 §3 + v5 §3),
engangsbruk `UNIQUE (tenant, jti)`, MAC-register og rotasjon per PR-012.

### V6. Godkjennerregler (Eiers produktbeslutning)
**UTVIDER:** to ulike godkjennere; forfatteren kan være den ene, aldri
begge. **INNSNEVRER/NØYTRAL:** én godkjenner ≠ forfatter.
**Første policy** følger den generelle UTVIDER-regelen — to godkjennere,
minst én ≠ forfatter; forfatteren *kan* være én av dem, men trenger ikke.
Minimum to brukere; to uavhengige godkjennere er også gyldig.
**Ingen enbruker-bypass. Disponit er ikke skjult medgodkjenner.**

### V7. `er_forfatter` er SERVER-UTLEDET
Beregnes som `bruker_id == policyutkast.opprettet_av` **etter at begge
rader er låst**. Klienten kan aldri sende eller påvirke feltet.
**DB-trigger avviser en attestasjon der booleanen ikke samsvarer med
identitetene.**

### V8. Semantikkchecksum + runtime-verifikasjon
Checksum over: manifestlisten selv (inkl. filstier) · innholdet i alle
listede filer og regeltabeller · låste bibliotekversjoner · tzdata-versjon
· `DENY_ALL_V1`. CI feiler ved endring uten dobbelt versjonsbump.
**I tillegg verifiseres den ved deploy/oppstart:** avviker vertens
tzdata eller biblioteker fra det artefakten ble bygget med, **stopper
deploy eller prosessoppstart** (en CI-port beskytter ikke produksjon).

### V9. `DENY_ALL_V1` er effektiv motorpolicy
Tenant uten aktiv policy evaluerer mot NØYAKTIG samme versjonerte
konstant — delt kodevei, ikke en separat «ingen policy»-gren. Hash og
versjon bindes i runde, diff, attestasjon og revisjonslogg. Endring av
konstanten ER en motorsemantikkendring.

### V10. Rullbakk = ny versjon; runtime kan ikke skrive `policyer`
Rullbakk kopierer historisk innhold til NY versjonsidentitet
(`basert_pa_versjon`, `rollback_av_versjon`, ny monoton versjon) og
risikoklassifiseres som alt annet — en rullbakk kan være en utvidelse.
Runtime-rollene mangler direkte INSERT/UPDATE/DELETE på `policyer`;
aktivering skjer kun via herdet funksjon som aldri deaktiverer uten å
sette inn etterfølger i samme transaksjon.

## De fjorten Codex-portene
1. Avvik mellom aktiv peker og avledet `aktiv` avvises ved commit
2. To samtidige førstegangsaktiveringer serialiseres på `policy_hode`
3. Endret manifest, motorfil, avhengighet eller tzdata → dobbelt versjonsbump kreves
4. Forfatter kan aldri være eneste godkjenner
5. Ingen klientpåstand kan påvirke `er_forfatter`
6. Tenant uten aktiv policy evaluerer nøyaktig `DENY_ALL_V1`
7. Hele klassifikatorens UTVIDER-regelsett mutasjonstestes
8. Ny leaf uten regel → CI rødt; regel mot slettet leaf → CI rødt
9. `metadata`-felt som når motorobjektet → CI rødt
10. Runtime kan ikke skrive `policyer` direkte; krasj under aktivering → gammel policy består
11. Rekalkulert diff/klasse avviker fra rundens → runde kansellert
12. Rullbakk får ny versjonsidentitet og klassifiseres
13. Deploy/oppstart stopper ved semantikkavvik mot artefakten
14. Klassifikator og motor gir identisk tidsmengde for samme vindu (DST inkludert)

## Omfang
Migrasjon 012: `policy_hode`, `policyutkast`, `aktiveringsrunde`,
`aktiveringsattestasjon`, kompositt-FK + trigger for aktiv-pekeren ·
`KLASSIFIKATOR_V1` + semantikkmanifest + CI-porter · skjema v0.2 utvides
med `metadata`-seksjon og `menneskelig_overstyring`-leafs · API:
utkast-CRUD, valider, diff, aktiveringsrunde (`policy:write` /
`policy:activate`, adskilte) · UI: femte flate med diffvisning og
risikoklasse per endring · evidensgrense `policyadmin-v1`.

## Etter merge → staging → Eier
Evidensartefakt, deretter: **du oppretter et utkast, ser diffen med
risikoklassen, får den godkjent, og aktiverer.** Da styrer du hva agenten
har lov til — bak validering, diff, fire øyne og full revisjon.
