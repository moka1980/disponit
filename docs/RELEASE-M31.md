# M-31-seeding på staging — golden-sett-porten for m57

Skrevet 31/8-2026 (RELEASE-M57-formen). Alt under er *staging* —
produksjon er utenfor enhver avtale. Kommandoene rører
`/opt/disponit` og `/etc/disponit` og kjøres derfor KUN av eier eller
etter uttrykkelig beskjed.

## 0. Hva porten er (og ikke er)

Migrasjon 086 legger golden-sett-porten INNE i `bytt_release`: har en
modul et GJELDENDE `evalueringskrav`, nekter registeret ethvert
releasebytte uten en BESTÅTT `evalueringskjoring` for NØYAKTIG det
kravet (eksakt kravversjon, dom 3) og kandidatens `artifact_digest`.
Porten gjelder ALLE miljøer — digesten er modellens miljøuavhengige
identitet (dom 2). En modul UTEN krav-rad er uberørt (opt-in, ingen
backfill): frem til steg 4 under er m57s releasebytter som før.

## 1. Forutsetninger

- 086 er kjørt (skjer i `opp.sh`-vinduet; `migrer.py` deler også ut
  runtime-SELECT på m31-tabellene).
- m57-kjeden er registrert (RELEASE-M57 §2) — porten binder seg til
  `modulrelease.artifact_digest`, som ER modellens manifest-sha256.
- Golden-settet finnes på disk: 20–50 SYNTETISKE norske søknadstekster
  i blindet form (dom 1: aldri ekte persondata — det er et
  KRAVGRENSER-punkt, `sett.persondata_i_eksempler = 0`), på formen
  `m31.golden.les_sett` krever (id/tekst/vekter/forventet_oppfylt/
  forventede_funn_kategorier per eksempel).

## 2. Registrer settet (dør 1 — hodet i basen, bytene på disk)

```sh
DISPONIT_MIGRATOR_URL=… python3 deploy/staging/registrer-m31-golden-sett.py \
    m57_ats hovedsett 1 <sti-til-settet>.json
```

Hashen er den KANONISKE (parset JSON, sort_keys) — formatering er ikke
identitet. Idempotent; avvikende innhold på samme (sett, versjon) er en
immutabilitetskonflikt, og da er svaret en NY versjon, aldri en
retting.

## 3. Målekjøring mot DAGENS digest (dør 3, uten krav)

```sh
DISPONIT_MIGRATOR_URL=… \
DISPONIT_M31_MODELL_URL=http://127.0.0.1:11434 \
DISPONIT_M31_MODELLNAVN=<modellnavn> \
python3 deploy/staging/kjor-m31-evaluering.py \
    m57_ats <dagens artifact_digest> <sti-til-settet>.json
```

MERK exit-koden: uten gjeldende krav registreres kjøringen med
`kravversjon NULL` og `bestatt = false` — skriptet returnerer 1, og
DET ER FORVENTET her. Dette er en MÅLING, ikke en port: tallene
(andel bestått, p95, modellfeil) er grunnlaget for tersklene i steg 4.
En NULL-rad bærer per konstruksjon aldri et bytte.

## 4. Sett kravet (dør 2) — porten er PÅ fra denne raden

Tersklene settes fra steg 3s måling (aldri strengere enn dagens modell
faktisk målte — ellers er neste bytte blokkert til settet eller
modellen endres):

```sql
SET ROLE disponit_modules_admin;
SELECT sett_evalueringskrav('m57_ats', 'hovedsett', 1,
    '<kanonisk hash fra steg 2>',
    <min_andel>,      -- f.eks. 0.90
    <maks_p95_ms>,    -- eller NULL for intet latenskrav
    0,                -- maks modellfeil
    'seed-31-08');
```

## 5. Neste modellbytte møter porten

Fra nå: et m57-modellbytte (ny digest) krever

1. `kjor-m31-evaluering.py m57_ats <ny digest> <settet>.json` —
   exit 0 (bestått mot gjeldende krav), så
2. `bytt_release(...)` som før — porten slipper byttet gjennom fordi
   den beståtte kjøringen finnes for eksakt (kravversjon, digest).

En innstramming (`sett_evalueringskrav` på nytt) historiserer forrige
krav i samme transaksjon og koster én re-kjøring for neste bytte —
gamle beståtte kjøringer bærer ALDRI et bytte under et nytt krav
(dom 3). Nødveien er uendret: `noddeaktiver_modul` går utenom porten
(den deaktiverer, bytter aldri), og en nodeaktivert modul avvises i
`bytt_release` FØR porten, som før 086.

## 6. Avlesning

`GET /v1/modellstyring` (admin-flaten «Modellstyring», scope
`security:read`) viser model card per modul: gjeldende krav, settet,
siste beståtte kjøring og kjøringslisten — AVLEDET av registeret ved
hver lesing, aldri lagret (dom 4).
