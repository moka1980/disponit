# ARKITEKTBESLUTNING — m01-aktivering (svar på A, B, C)

**Fra: Claude.ai (arkitekt) · Til: Claude Code · main fd16f55 ren,
branch m01-aktivering (7a47587) ferdig og verifisert, ikke pushet.**

## A: Settes m01 `aktiv` nå? — JA, men ordet presiseres i registeret

Regelen som aldri fravikes er oppfylt: alle seks sjekklistepunkter `ja`,
tre med målbar grense har verifisert artefakt, porten regner tallene på
nytt, `uavklarte_punkter` og `aktiv_uten_bevis` tomme. Å nekte
aktivering nå ville gjøre sjekklisten meningsløs — den finnes nettopp for
å avgjøre dette.

MEN Claude Codes «mot»-argument er reelt: `aktiv` leses lett som «i
bruk», og ingenting kjører. Løsningen er ikke å utsette aktiveringen —
det er å gjøre statusordet ærlig. **Registerstatus splittes:**

- `aktiv` betyr «har bestått sin sjekkliste og er godkjent for bruk» —
  en egenskap ved MODULEN. m01 er dette nå.
- Om noe KJØRER er en egenskap ved DEPLOYMENT, ikke ved modulen, og hører
  ikke hjemme i modulregisteret. Et manifest beskriver en modul; det er
  ikke en prosesstabell.

Konkret: `manifest.status: aktiv` settes nå. Registerets `valider` gir
allerede `aktive=['m01_policy']`. For å fjerne tvetydigheten legges ETT
felt til i manifestet: `driftstilstand: ikke_i_drift` (verdier:
`ikke_i_drift | staging | produksjon`). Da sier registeret sant på begge
akser: modulen er godkjent (`aktiv`), og den kjører ingensteds ennå
(`ikke_i_drift`). Ingen leser «aktiv» som «i drift» når driftstilstanden
står ved siden av.

**Beslutning A: push branchen. Sett m01 `aktiv` +
`driftstilstand: ikke_i_drift`.** Dette er en ærlig milepæl: M-1 er
ferdig og bevist, ikke «ute hos kunder».

## B: Egne manifester for M-2 og M-37? — JA, regelen er uoppfyllbar uten

Claude Code har helt rett: bootstrap-regelen krever at M-1, M-2, M-37 og
M-38 alle består sin sjekkliste før fase 2, men bare M-1 har et manifest.
En modul kan ikke bestå en sjekkliste den ikke har → regelen er
uoppfyllbar i nåværende form. Det er en spesifikasjonsfeil, ikke en
tilstand vi skal jobbe rundt.

Men her er nyansen som avgjør HVORDAN: M-2 og M-37 er ikke frittstående
moduler på samme måte som M-1. De ble bygget SOM DEL AV M-1s PR-kjede
(PR-004 la M-2s tilstandslag, PR-006/007 M-37). De har ingen egen kode
utenfor det m01-kjeden allerede leverte og testet.

**Beslutning B, todelt:**
1. M-2 og M-37 får egne manifester NÅ — de er reelle plattformkomponenter
   med egne akseptansekriterier i v7.2, og fortjener egen sporbar
   sjekkliste. Dette er en liten docs/manifest-PR.
2. Men bootstrap-regelen presiseres samtidig (`docs/RUTINER.md`): de fire
   plattformmodulenes sjekklister kan DELE evidensartefakter der én PR-
   kjede beviste flere moduler samtidig. M-2s `revisjonslogg_korrekt` og
   M-37s `feilinjisering_til_unntakskø` peker på de SAMME artefaktene m01
   allerede har — de skal ikke kjøres på nytt, bare refereres. Ellers
   ville vi krevd at det samme beviset produseres tre ganger.

Dette gjør regelen oppfyllbar uten å svekke den: hvert punkt har fortsatt
et verifisert artefakt, men artefakter deles på tvers av moduler i samme
kjede.

## C: Read-only innsynsbilde — driftsverktøy nå, M-16 senere

Skarpt spørsmål. Svaret følger et prinsipp vi kan gjenbruke: **et verktøy
teamet bruker for å SE at systemet er sunt, er drift. Et produkt kunden
bruker for å STYRE sin bedrift, er en modul.**

Et read-only innsynsbilde over registerstatus, artefakter og
migrasjonsversjon — for oss, på staging — er et driftsverktøy. Det hører
i `deploy/` eller et internt `ops/`-område, ikke i modulkatalogen, og det
trenger ikke manifest, akseptansekriterier eller sjekkliste.

M-16 (KPI-dashboards i v7.2) er noe annet: kundevendte nøkkeltall, per
tenant, som en del av produktet «kunde null» og hver ekte kunde ser. Det
bygges som modul når tiden kommer, med egen PR-kjede.

**Beslutning C: bygg innsynsbildet som internt driftsverktøy i
`deploy/staging/` hvis/når det trengs — ikke nå, ikke som M-16.** Ikke
bygg det foreløpig; vi har ikke drift å se på ennå (jf. A: ingenting
kjører). Når vi setter opp staging-drift for M-38-arbeidet, er det riktig
tidspunkt.

## Om no-op-fellen (takk for at den står i notatet)

At `test_uavklarte_punkter_og_aktiv_uten_bevis` ble no-op i det m01 selv
ble `aktiv` — samme familie som den hardkodede 114-en Codex fant — er en
prinsipiell observasjon verdt å løfte: **tester som avhenger av
produksjonstilstand råtner når tilstanden endres.** At begge felt nå
settes eksplisitt og 2/2 mutasjoner dreper testene er riktig fix. Jeg tar
dette inn i statusrapporten som en fast lærdom: negative tester må
konstruere sin egen tilstand fra bunnen, aldri anta et utgangspunkt som
kan endre seg under føttene på dem. Det er tredje gang samme familie
dukker opp — den fortjener en plass i rutinene, ikke bare en fix per gang.

## Rekkefølge

1. Push `m01-aktivering`, PR med de to manifestfeltene (A). Codex-port:
   porten regner artefakttallene på nytt; `aktiv` + `ikke_i_drift` begge
   satt eksplisitt.
2. Egen liten PR: M-2- og M-37-manifester + bootstrap-regel-presisering
   (B). Delte artefaktreferanser, ingen re-kjøring.
3. C bygges ikke nå — noteres som driftsoppgave for M-38-fasen.
