# RUTINER — slik jobber vi

Gjelder alle i pipelinen. Avvik fra rutinene er selv en review-feil.

## 1. Roller

| Rolle | Hvem | Ansvar |
|---|---|---|
| **Eier** | Deg | Godkjenner retning, eier kontoer/nøkler/avtaler, bærer juridisk ansvar. Involveres bare der policy krever menneske. |
| **Claude.ai** | Arkitekt og produktleder | Bestemmer struktur og spesifikasjon, lager drafts, koordinerer reviews, tar beslutninger på vegne av Eier der det trengs. Avslutter **alltid** med NÅ/NESTE-blokken (se pkt. 3). |
| **ChatGPT** | Spesifikasjonsreview | Reviewer drafts mot de tre faste spørsmålene (se docs/README-arbeidsflyt.md). Svar limes inn i PR-beskrivelsen. |
| **Claude Code** | Implementering | Skriver kode i repoet, kjører tester lokalt og på staging-serveren. Deployer aldri til produksjon direkte. |
| **Codex** | Kodereview og merge | Håndhever de fire merge-portene. Merger kun grønt. |

## 2. Modulrutine — én modul om gangen, helt ferdig

1. **Draft** (Claude.ai): spesifikasjon/kode mot akseptansekriteriene i docs/spesifikasjon (v7.2). Én modul = én branch = én PR.
2. **Spesifikasjonsreview (ChatGPT) — OBLIGATORISK for alle PR-er som rører `platform/`, `policies/` eller `deploy/`.** Claude.ai sender draften (spesifikasjon eller kode) til ChatGPT FØR Claude Code starter implementering. Review-svaret limes inn i PR-beskrivelsen. Kun PR-er som utelukkende endrer `docs/` kan hoppe over porten, og da skal PR-beskrivelsen si det eksplisitt med begrunnelse.
   *Historikk: porten ble hoppet over i PR-003 (forsvarlig, ren docs) og PR-004 (ikke forsvarlig — tillitsankerets tilstandslag). Codex og Claude Code fanget tolv P1 i PR-004-rundene, men porten foran skal redusere antallet som når dit. Denne presiseringen finnes fordi arkitekten brøt sin egen rutine; regelen gjelder Claude.ai mest av alle.*
3. **Implementering** (Claude Code): kode + tester, inkludert obligatoriske negative policytester.
4. **Kodereview** (Codex): fire porter, merge til main.
5. **Staging-test** (Claude Code): modulen kjøres på staging-serveren — ekte server, syntetiske data, sandkasse-integrasjoner. Hele sjekklisten i modulens manifest må bestå 100 %.
6. **Aksept** (Claude.ai bekrefter, Eier informeres): modulstatus settes til `aktiv`. Først nå starter neste modul.

**Regel:** «Testes direkte på serveren» betyr staging-serveren — aldri produksjon. Produksjon nås kun via utrullingsløypen i v7.2 (kanari → gradvis → automatisk rollback).

**Bootstrap-unntak (kun fase 1-plattformmoduler):** M-1, M-2, M-37 og M-38 er gjensidig avhengige — m01 kan f.eks. ikke bestå `feilinjisering_til_unntakskø` før M-37 finnes, og M-37 kan ikke bygges uten M-1. For disse fire gjelder «ferdig før neste» på KJEDENIVÅ: de bygges i samspill, og ingen fase 2-modul startes før ALLE fire har bestått hele sin staging-sjekkliste. Regelen som aldri fravikes: en modul settes ikke til `aktiv` i registeret før alle sjekklistepunkter er ja — blokkerte punkter markeres `blokkert_av: <modul>` i manifestet, ikke som ja. Fra fase 2 gjelder regelen bokstavelig per modul.

**Presisering 2026-08-05 — delt evidens mellom plattformmodulene.** Regelen over var *uoppfyllbar som skrevet*: den krevde at fire moduler besto hver sin staging-sjekkliste, mens tre av dem ikke hadde manifest i det hele tatt. M-2 og M-37 har nå egne manifester. Og fordi de ble bygget **som del av** m01-kjeden, måler de samme staging-kjøringene flere av dem samtidig — derfor kan de fire plattformmodulene **dele evidensartefakter** i stedet for å produsere samme bevis tre ganger.

Delingen har én betingelse, og den er hele forskjellen på deling og smutthull: **punktet må navngi hvilken MÅLING i det delte artefaktet som beviser det for nettopp den modulen** — strukturert, i feltet `bevismaalinger`, som punktseparerte stier inn i artefaktet. «Samme artefakt» uten «samme måling» er å låne en konklusjon.

Bindingen er STRUKTURERT og ikke fritekst, fordi fritekst ikke lar seg etterprøve maskinelt: `notat: "banan_maaling = true"` er både unikt og ikke-tomt. `manifestskjema.valider_artefakter` åpner det hash-verifiserte artefaktet og krever at hver oppgitte sti FINNES. `notat` forklarer fortsatt hvorfor målingen er relevant — men den strukturerte bindingen beviser hvilken måling forklaringen gjelder. At målingen er *relevant* for modulen forblir reviewansvar; at den *finnes* er maskinelt. Eksempler fra de tre manifestene: `rollback-m01-v1` beviser `rollback_testet` for M-2 gjennom `tapte_loggposter = 0` og uendret radtelling for `revisjonslogg` — men **ikke** for M-37, fordi den kjøringen deaktiverte beslutningsmodulen og aldri arbeiderprosessen. M-37s punkt står derfor `nei`.

Evidenskjeden er uendret: `manifestskjema.valider_artefakter` åpner filen, verifiserer sha256 mot innholdet og regner samtlige tall ut på nytt mot `KRAVGRENSER` — for hvert manifest som peker på den. Et delt artefakt slipper altså gjennom nøyaktig like mange porter som et eget.

**Sjekklisteformat i manifester:** `staging_sjekkliste`-verdier er `ja | nei | blokkert_av: <modul-id>`. Et sjekklistepunkt uten definert, målbar grense regnes som `nei` — en port man ikke kan måle, er ingen port. m01s ytelsesport er definert slik: 100 beslutninger/sekund vedvarende i 60 sekunder mot staging-PostgreSQL med 20 samtidige tilkoblinger, p95-latens under 150 ms, null feil og null tapte loggposter (1:1 beslutning↔loggpost verifisert etter kjøringen). Grensen er satt for Cloud Server S (2 vCPU) og justeres ved målt behov. m01-manifestet oppdateres tilsvarende: `feilinjisering_til_unntakskø: blokkert_av: m37`, `rollback_testet: blokkert_av: m37`, `ytelse_bestatt: nei` (grense definert, kjøres på staging i PR-005-runden).

## 3. Fast avslutningsblokk — obligatorisk i hver leveranse

Hver leveranse fra Claude.ai avsluttes med:

```
NÅ:    <konkret oppgave> — <hvem> — <full sti fra repo-rot>
NESTE: <konkret oppgave> — <hvem> — <full sti fra repo-rot>
```

Ingen leveranse uten denne blokken. Uklarhet om hvem/hva/hvor er en feil.

## 4. Filplassering

- Hver ny fil oppgis med **full sti fra repo-rot** når den lages eller omtales.
- Filer som ikke passer i strukturen (docs/STRUKTUR.md) avvises i review — strukturen endres bevisst, aldri tilfeldig.

## 5. Språk (i18n) — globalt fra bunnen

- **Ingen hardkodet visningstekst** i kode eller markup. All tekst brukeren ser, hentes via nøkkel fra `locales/<språk>.json`.
- Nytt språk = **én ny fil** i `locales/`. Ingen kodeendring.
- Motoren (core) returnerer stabile maskinkoder (`beslutning`, `unntak_kategori`, `begrunnelse` som koder+parametre siden PR-002) — disse ER oversettelsesnøklene. Revisjonsloggen lagrer kodene som intern evidens; visningslaget oversetter via `locales/`.
- Formater (dato, valuta, tall) hentes alltid fra locale — aldri hardkodet.

## 6. Design — én kilde

- Alle farger, typografi, avstander og fokus-stiler defineres kun i `design/tokens.css`.
- Komponenter refererer variabler — aldri egne verdier. Endre utseende = endre én fil.
- WCAG 2.1 AA-kravene i v7.2 gjelder alt UI; axe-core i CI blokkerer merge ved brudd.

## 7. Moduler — legg til og fjern uten ringvirkning

- En modul er en mappe under `platform/modules/` med `manifest.yaml`. Registeret (`platform/core/registry.py`) oppdager den automatisk.
- Fjerne modul = sett `status: inaktiv` (eller slett mappen). Registeret nekter å aktivere moduler med manglende/inaktive avhengigheter — ingenting annet påvirkes.
- Core importerer **aldri** fra moduler. Moduler snakker kun med core-API-er, aldri direkte med hverandre.

## 8. GitHub — der pipelinen faktisk håndheves

Repoet bor på github.com. Reglene under er ikke anbefalinger — de konfigureres som branch protection slik at GitHub nekter det som er forbudt.

**Flyt:** Claude Code lager branch `pr-XXX-mNN-kortnavn` → åpner PR med malen (.github/PULL_REQUEST_TEMPLATE.md) → CI kjører automatisk (.github/workflows/ci.yml) → ChatGPT-review limes inn i PR-beskrivelsen → Codex reviewer i PR-en og merger når portene er grønne → merge til main trigger staging-deploy (PR-004).

> ✅ **Status 2026-08-01: branch protection er aktiv og satt opp av Claude Code via GitHub-API-et** (ikke i Settings-menyen). Del B i `docs/PUSH-INSTRUKS.md` er utført; verifiser med `gh api repos/moka1980/disponit/branches/main/protection` framfor å sette den opp på nytt.

**Eiers beslutning 2026-08-01: merge-porten driftes av pipelinen (Claude Code / Codex) uten Eier.** Det endrer to ting fra det opprinnelige oppsettet, og begge er bevisste:

- **Ingen menneskelig godkjenning kreves lenger** — heller ikke på tillitsankeret (M-1 policymotor, M-2 revisjonslogg, M-37 unntaksmotor). Anbefalingen i `docs/README-arbeidsflyt.md` om å beholde én menneskelig port er dermed **forlatt med vitende og vilje**. Porten som er igjen er maskinell: ingenting når `main` uten grønn CI, og CI inneholder de negative policytestene som beviser at handling utenfor policy faktisk stoppes. Svekkes en test, svekkes porten — derfor er «ingen fjernet/svekket negativ test» merge-port nr. 1.
- **`enforce_admins` er nå PÅ.** Det kunne den ikke være før: så lenge en godkjenning var påkrevd og det bare finnes én konto, ville `main` låst seg helt (GitHub lar ingen godkjenne sin egen PR). Med null påkrevde godkjenninger er det ingen låsing — og da forsvinner admin-forbikjøringen som tidligere ble bevist med «Bypassed rule violations». **Ingen kan lenger pushe direkte til `main`. Ikke Codex, ikke Claude Code, ikke Eier.**

**Branch protection på `main` (aktiv nå):**
- Require pull request before merging — 0 påkrevde godkjenninger, ingen direkte push
- Require status checks to pass: `CI / test`, strict (branchen må være oppdatert mot `main`)
- Require linear history · ingen force-push · ingen sletting av `main`
- `enforce_admins: true` — reglene gjelder også repo-eier

**CODEOWNERS er ikke lenger en sperre.** Filen beholdes fordi GitHub fortsatt automatisk ber om review fra eier på de fire stiene, men den **blokkerer ingen merge**. Skal tillitsanker-porten gjeninnføres senere, er det ett API-kall: `require_code_owner_reviews: true` + `required_approving_review_count: 1` — og da må rolle-kontoene under finnes først.

**Rolle-kontoer (fortsatt anbefalt, nå av en annen grunn):** egne GitHub-kontoer for Claude Code og Codex gir sporbarhet — hvem gjorde hva — og gjør det mulig å kreve at Codex faktisk godkjenner Claude Codes PR før merge, uten at Eier involveres. Så lenge begge kjører som `moka1980` er «Codex reviewet» kun en påstand i PR-beskrivelsen, ikke noe GitHub kan bekrefte.
