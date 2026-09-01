# Klyngen «orden i eget hus» — fundamentet

Fem moduler bygges parallelt: **M-3** datakvalitet, **M-4**
dataforvalter, **M-5** dokumentmal, **M-9** kunnskap, **M-21**
avtalefrist. Denne fila er kontrakten mellom sporene. Den er skrevet
fordi parallell modulbygging har én forutsigbar feilmåte — to spor som
rører den samme delte fila — og fordi vi vet nøyaktig hvilke filer det
gjelder.

## Hvorfor fundamentet finnes

Tre erfaringer fra de foregående rundene:

1. **Migrasjonsnumre kolliderer.** M-31 og M-38 tok begge 086; M-35 og
   M-10 tok begge 089. Begge gangene måtte den siste renummereres, og
   renummerering er billig i seg selv, men dyr fordi fasit-pinningen og
   kjørerens sekvenskrav må følge med. Numrene er derfor tildelt her,
   én gang, før noen skriver SQL.
2. **Nye DB-roller stopper deployen.** #324 la to roller i
   migrasjonene. Verten hadde dem ikke, og deployporten stoppet — helt
   korrekt — med *«AVBRUTT: DISPONIT_DRIFTSTATUS_URL mangler»*. Hver
   påfølgende merge ville stoppet på det samme. Fem moduler etter tur
   ville gitt fem slike stopp. Klyngens åtte roller opprettes derfor i
   fundamentet, i én omgang.
3. **`test_pr008.py` binder RUTESCOPE toveis til `Route()` i
   app.py-kilden.** En RUTESCOPE-linje i fundamentet uten en registrert
   rute er rød fra første commit. Rutene kan altså IKKE forhåndsregistreres.

## Tildelte migrasjonsnumre

| Nr | Modul | Fil |
|----|-------|-----|
| 092 | M-3 | `092_m3_datakvalitet.sql` |
| 093 | M-4 | `093_m4_retensjonsregister.sql` |
| 094 | M-5 | `094_m5_malregister.sql` |
| 095 | M-9 | `095_m9_begrepsregister.sql` |
| 096 | M-21 | `096_m21_pliktregister.sql` |

Kjøreren krever **ubrutt sekvens**. Et spor som bygger i egen worktree
før et lavere nummer er merget, må derfor midlertidig bruke neste
ledige der — og renummerere til sitt tildelte nummer ved PR-tid.
Lander sporene i rekkefølge 092 → 096, oppstår situasjonen ikke.

## Hva fundamentet eier (denne PR-en)

* `platform/modules/m0{3,4,5,9}_*/manifest.yaml` og
  `platform/modules/m21_avtalefrist/manifest.yaml`
* `plattformdata.js`: `MODULSTATUS` og `MODULER` for 3, 4, 5, 9, 21 —
  **samme commit som manifestene**, fordi
  `test_modulstatus_dekker_manifestene` binder dem sammen
* `manifestskjema.py`: `KRAVGRENSER` for `m3-v1`…`m21-v1` — registrert
  FØR byggingen de skal måle (§0-regelen)
* `locales/{nb,en}.json`: `site.modul.m{3,4,5,9,21}.{navn,tekst}`
* `oppsett-postgresql.sh` og `ci.yml`: klyngens åtte DB-roller
* denne fila

## Hva hver modul-PR eier (og altså IKKE skal legge her)

* sin migrasjon, sin modulkatalog, sine tester
* `app.py`: `Route(...)` **og** `RUTESCOPE`-linja — i samme commit
* `sitekart.js` `BASISRUTER` og `app.js` flatekartet
* `migrer.py`: sine grants (de refererer tabeller som ikke finnes før
  migrasjonen har kjørt)
* `opp.sh`: sin enhet, sin `SELVREVERS_ENHETER`-oppføring og sin
  DSN-port — **porten hører til modulen som trenger enheten**, aldri
  til fundamentet, ellers stopper fundamentets egen deploy
* eventuelle `varsel`-CHECK-utvidelser (additivt, 041-formen)

## Etter at fundamentet er merget

Eieren kjører **én gang** på verten:

```
sudo bash /opt/disponit/aktiv/deploy/staging/oppsett-postgresql.sh
```

Skriptet er idempotent på to nivåer — roller opprettes bare om de
mangler, og en rolles DSN-er skrives bare om en nøkkel mangler i
miljøfila. Eksisterende passord roteres ikke.

## v1-avgrensningene, samlet

Alle fem er MÅLERE og REGISTRE. Ingen av dem endrer kundens data, og
ingen av dem er en ny slettevei. Det er det som gjør dem trygge å bygge
samtidig.

* **M-3** profilerer og rapporterer. Retter ikke, slår ikke sammen,
  **blokkerer ingen bestilling** — blokkering er en policyendring med
  attestasjon, ikke en bieffekt av at noen la til en terskel.
* **M-4** fører retensjonsregnskapet. **Sletter ingenting** utenfor
  egne målerader; de seks reaperne som alt kjører rører den ikke.
* **M-5** registrerer maler og fyller dem ut. Returnerer tekst —
  lagrer ikke dokument, sender ikke, publiserer ikke.
* **M-9** registrerer begreper med kilde og gyldighetsdato, og søker
  over dem. Ingen RAG, ingen vektorbase, ingen eksterne kilder.
* **M-21** registrerer plikter og varsler før frist. Trekker ikke ut
  fra avtaledokumenter, sender ikke inn noe sted.
