# ADR-009: Denne verten blir produksjon

**Status:** vedtatt 2026-11-20 av eier («2»: gjør denne serveren til
produksjon). Forberedelsene er bygget; selve byttet er IKKE gjort.
**Erstatter:** miljøtabellen i `docs/DEPLOY.md` for denne verten («staging
og produksjon er alltid separate maskiner»).

## Problemet

`docs/DEPLOY.md` sier at `disponit-srv` er staging, og at produksjon skal
være en egen maskin. `opp.sh` håndhevet det: en miljøfil med
`DISPONIT_MILJO=produksjon` stoppet utrullingen. Samtidig er det denne
verten som har de ekte firmaene, og eier vil ikke drifte to maskiner nå.

Modusen styrer tre ting, målt i koden 20.11.2026:

1. **Hvilke policyer som binder** (`policyregister.tillatte_statuser`). I
   produksjonsmodus binder bare policyer merket `produksjon`.
2. **Hvilke moduler som tar bestillinger** (`api/bestilling.py`). En
   bestilling er bare claimbar når modulen har en `claiming` deployment i
   prosessens eget miljø.
3. **Hva forsiden lover** (`/ui/oppsett.json`).

## Dommen

**V1. Vertsrollen står i `/etc/disponit/vertsrolle`,** rot-eid, med
nøyaktig `staging` eller `produksjon`. Uten fila er verten staging.
`opp.sh` krever at `DISPONIT_MILJO` i miljøfila er LIK vertsrollen, i
begge miljøportene. To uavhengige fakta må være enige; en redigert
miljøfil alene bytter aldri modus.

**V2. Klarsjekken (`deploy/staging/produksjonsklar.sh`) står før første
mutasjon** når vertsrollen er produksjon. Den leser bare, og blokkerer på:

- et firma i bruk (aktivt medlem eller beslutning siste 30 døgn) med en
  aktiv policy som ikke er merket `produksjon`;
- en modul som har `claiming` i staging, men ikke i produksjon med samme
  kontrakt;
- en produksjons-deployment uten gyldig modultoken.

**V3. Policyer løftes av mennesker, gjennom fire øyne.** Ingen policy
flyttes til `produksjon` med en databaseoppdatering. Firmaets egen
policyforvalter lager et utkast i Policyadministrasjon, og en ANNEN person
godkjenner det.

**V4. Testfirmaene (`t-…`) slutter å virke på verten.** De står på
utkast-policyer med vilje. Målinger som trenger dem, går i den isolerte
basen (`disponit_test` på 55432), som ADR-006 alt gjør for M-33.

**V5. Rekkefølgen er fast**, og hvert steg kan angres til og med steg 4:

1. Firmaene i bruk får produksjonspolicy (V3).
2. Hver arbeidermodul får en `claiming` deployment i `produksjon`
   (`bytt_release`, samme release og kontrakt som i staging).
   Staging-deploymenten blir stående.
3. Hver arbeider får et modultoken for produksjon. Det gamle tokenet
   beholdes ved siden av.
4. Klarsjekken er grønn.
5. `echo produksjon > /etc/disponit/vertsrolle`, `DISPONIT_MILJO=produksjon`
   i miljøfila, ny utrulling av samme release.

Angre etter steg 5: slett vertsrollefila, sett miljøfila tilbake, rull ut
på nytt. Staging-deploymentene og de gamle tokenene står der fortsatt.

## Tilstanden 20.11.2026

Klarsjekken mot drift ga åtte blokkeringer: `wcagvakt` står på en
utkast-policy; M-14, M-17, M-23, M-26, M-44 og WCAG-revisjonen mangler
produksjons-deployment; M-57 har en, men arbeideren har ikke token for
den. `demo-a` er ikke i bruk og slutter å binde.

## Gjort 20.11.2026 (steg 2 og 3)

Alle sju arbeidermodulene har nå en `claiming` deployment i `produksjon`
og et modultoken for den (`deploy/staging/modul-til-produksjon.sh`). De
gamle tokenfilene står ved siden av som `DISPONIT_MODULTOKEN.staging-<tid>`.
Klarsjekken har én blokkering igjen: `wcagvakt` på utkast-policy (steg 1,
menneskets).

**Funnet underveis:** staging-tokenene utløp 17.10.2026, og alle sju
arbeiderne hadde feilet i hver runde siden. Ingenting kalte
`/v1/modul/token/roter`. Køen var tom for ekte firmaer i hele perioden, så
ingen bestilling gikk tapt. Rettet med `disponit-modultoken.timer`
(`roter-modultokener.sh`, installeres med `modultoken-oppsett.sh`): tokener
med ti døgn eller mindre igjen roteres, og et utløpt token feller uniten.

## Det dommen ikke gjør

- Den skiller ikke staging og produksjon. Etter byttet finnes det ingen
  staging-maskin. Nye migrasjoner går rett til verten med kundedata, bak
  CI og den isolerte basen alene.
- M-01 står som `aktiv/staging` til byttet er gjort.
