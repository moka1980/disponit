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
2. **Spesifikasjonsreview** (ChatGPT): tre faste spørsmål, svar i PR.
3. **Implementering** (Claude Code): kode + tester, inkludert obligatoriske negative policytester.
4. **Kodereview** (Codex): fire porter, merge til main.
5. **Staging-test** (Claude Code): modulen kjøres på staging-serveren — ekte server, syntetiske data, sandkasse-integrasjoner. Hele sjekklisten i modulens manifest må bestå 100 %.
6. **Aksept** (Claude.ai bekrefter, Eier informeres): modulstatus settes til `aktiv`. Først nå starter neste modul.

**Regel:** «Testes direkte på serveren» betyr staging-serveren — aldri produksjon. Produksjon nås kun via utrullingsløypen i v7.2 (kanari → gradvis → automatisk rollback).

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
- Motoren (core) returnerer stabile maskinkoder (`beslutning`, `unntak_kategori`) — disse ER oversettelsesnøklene. `begrunnelse`-tekstene i revisjonsloggen er intern evidens (norsk), ikke brukergrensesnitt; strukturerte koder+parametre for visning kommer i PR-002.
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

> ✅ **Status 2026-08-01: branch protection ER aktiv — punktene under er allerede slått på** (av Claude Code via GitHub-API-et, ikke i Settings-menyen). Del B i `docs/PUSH-INSTRUKS.md` er dermed utført; verifiser med `gh api repos/moka1980/disponit/branches/main/protection` framfor å sette dem opp på nytt.
>
> ⚠️ **Ett hull står igjen: `enforce_admins` er av, og alle AI-rollene kjører foreløpig som Eiers egen konto (`moka1980`, admin).** Det er bevist, ikke antatt: en direkte push til `main` ble sluppet gjennom med «Bypassed rule violations». For den kontoen er reglene fortsatt bare en avtale. Fikses av rolle-kontoene nederst i dette punktet — **først når de finnes kan `enforce_admins` slås på**, for med bare én konto låser `main` seg (GitHub lar ingen godkjenne sin egen PR). Til da fanger CI-jobben `pr-porten` forbikjøringen etterskuddsvis.

**Branch protection på `main` (Eier aktiverer én gang under Settings → Branches):**
- Require pull request before merging (ingen direkte push — gjelder alle)
- Require status checks to pass: `CI / test`
- Require review from Code Owners (aktiverer tillitsanker-porten i .github/CODEOWNERS: policymotor, register, policy-skjema og CI-oppsettet krever Eiers godkjenning; alt annet merger Codex selv)
- Require linear history + ingen force-push

**Rolle-kontoer:** Claude Code og Codex bruker egne GitHub-kontoer/tokens med skrivetilgang til brancher, aldri til main. Eier eier repoet og er @EIER i CODEOWNERS (@moka1980).
