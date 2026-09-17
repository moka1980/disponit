# PLAN — rollback-drillen for sveipmodulene (049-formen)

Skrevet 17/9-2026, ETTER at M-44, M-26, M-14 og M-57 ble sertifisert i
natt (#538, #539, #540, #542, #544, #545). Eiers vedtak samme dag:
betaling først når alle 57 er ferdige — og «få på plass de modulene som
fungerer og er i drift». Dette dokumentet er det som står mellom de
modulene og ordet `aktiv`, målt — ikke antatt.

## 1. Veggen, målt

`manifestskjema.aktiv_uten_bevis` (RUTINER pkt. 2): en modul settes ikke
til `aktiv` før ALLE sjekklistepunkter er `ja`; `registry.valider`:
`driftstilstand` utenom `ikke_i_drift` krever `aktiv`. `blokkert` teller
som uavklart.

| Modul | Sjekkliste | Det som mangler | Release i registeret (17/9) |
|---|---|---|---|
| M-14 fakturakontroll | 5/6 | rollback_testet | m14-r1, digest 47fe6c43… |
| M-17 kundeservice | 5/6 | rollback_testet | m17-r1, digest cea1329d… |
| M-23 fordring | 5/6 | rollback_testet | m23-r1, digest 5824bca0… |
| M-26 prisbok | 5/6 | rollback_testet | m26-r1, digest 27e8418d… |
| M-44 kampanje | 5/6 | rollback_testet | m44-r1, digest e8c622a2… |
| M-37 unntak | 6/7 | rollback_testet | plattformkjerne, ikke registrert modul |
| M-06 e-post | 5/6 | rollback_testet | ingen egen release, ingen arbeider |
| M-57 ATS | 2 ja / 1 blokkert / 3 nei | rollback + tre kjøringer (#541 lukket) | m57-r1-20260827 |

Alle åtte har NØYAKTIG én release. Grensene finnes alt i KRAVGRENSER
(`m14-rollback-v1`, `m17-…`, `m23-…`, `m26-…`, `m44-…`, `m57-v1`s
rollback-felter), i `rollback-m56-v1`s form: `min_rullback_claims 1`,
`min_rullback_promoterte`, `maks_claims_etter_drenering 0`,
`maks_falske_verdikter 0`, `krev_release_digest_bundet`. `_sjekk_grenser`
har en FAIL-CLOSED arm for hver: «flippedrillen er ikke bygget — verken
produsent eller artefaktskjema finnes».

## 2. Døra (052 `registrer_moduldrill`) — hva en drill MÅ bevise

Lest av funksjonen, ikke av dokumentasjon:

1. `p_drillet` (releasen som ruller tilbake) står som deployment i livsløp
   `draining`; `p_kandidat` (den som overtar etterpå) står `claiming`.
   Drillet, rullback og kandidat står på SAMME kontraktlinje
   (modul, miljø, kontraktversjon, kontrakt_hash).
2. Kandidatens digest er den drillede releasens digest (samme bytes
   kommer tilbake); rullbakkens digest er FORGJENGERENS — den releasen
   registerets egen historie sier den drillede overtok fra.
3. `p_module_epoch` er modulhodets epoch da drillen ble målt
   (snapshot), og `p_utfort_ts` ligger innenfor reservasjonsvinduet.
4. TRE drilloppdrag i drillens tenant, hvert med rent utfall og
   kvittering: ett INFLIGHT (claimet av den drillede før dreneringen,
   fullført etter), ett RULLBACK (claimet og fullført av forgjengeren
   mens den var claiming), ett KANDIDAT (claimet av kandidaten etter
   fram-rullingen). Målt via `owner_claim_id`/`claim_release_id` på
   oppdragsradene — ikke via påstander.
5. Én drillereservasjon (`moduldeployment_reservasjon`) med hjerteslag;
   utløper den, ignorerer basens vakt raden.
6. Artefaktet (`rollback-<m>-v1`) bærer tellingene OG identitetene
   (`_identiteter_stemmer`), og manifestet sha256-binder det.

`rollback-m56.py` (1707 linjer) gjør alt dette for M-56 — men fasene
bytter release ved å boote et DOCKER-IMAGE gjennom
`wcag-staging-sjekkliste.py` fase 2/4/9. Sveipmodulene har ikke image:
arbeiderne kjører `python -m drift.<m>_arbeider` fra
`/opt/disponit/aktiv/platform` (symlenken deployen flytter).

## 3. To valg som må avgjøres FØR koding (§0-rekkefølgen)

**A. Hva er «forgjengerens bytes» for en sveipmodul?** Digesten i
`modulrelease.artifact_digest` (47fe6c43… for m14-r1) ble oppgitt ved
registreringen 9/9 som release-materialets sha256. For at
`krev_release_digest_bundet` skal bety noe, må digesten være REGNBAR av
treet: forslaget er sha256 over `platform/modules/<m>/` + `drift/<m>_*.py`
+ modulens migrasjoner (kanonisk liste i registreringsskriptet), slik at
r2 registreres fra treet og r1s digest kan verifiseres mot
`/opt/disponit/releases/<r1-commit>` som fortsatt ligger på disk.

**B. Hvordan bytter drillen release for ÉN modul uten å flytte hele
plattformen?** To veier:
  - (i) per-modul unit-override: `disponit-m14.service` får en
    `Environment=DISPONIT_RELEASE_KATALOG=/opt/disponit/releases/<sha>`
    og `WorkingDirectory`/`PYTHONPATH` derfra — drillen skriver override,
    `daemon-reload`, restart. Plattformen (API, andre arbeidere) står
    urørt. ANBEFALT.
  - (ii) flippe `/opt/disponit/aktiv` (rollback-m01-formen) — ruller ALT
    tilbake i drillvinduet, og drillen for én modul blir en plattform-
    hendelse. Ikke anbefalt for åtte moduler.

## 4. Rekkefølgen (M-14 først — enkleste bestillingsvei, alt målt i natt)

1. `deploy/staging/rollback-sveipmodul.py --modul m14_fakturakontroll`:
   reservasjon + hjerteslag (kopiert i FORM fra rollback-m56, ikke delt
   modul — m56s bevisrot binder de bytene), forgjengeroppslag,
   r2-registrering gjennom `registrer-m14-fakturakontroll.py` (idempotent
   på identisk innhold), tre bestillinger av `bokforing.*`-typen mot
   fasit-tenanten gjennom API-et (token laget på verten med
   `token-cli.py --bootstrap`, tilbakekalt etterpå — M-57-natta viste
   veien), fasene som unit-override (valg B-i), registrering via
   `registrer_moduldrill`, artefakt.
2. `platform/core/artefakt-rollback-modul-skjema.json` (generisk,
   `rollback-m56-v1`s felt + `modul`), ARTEFAKTSKJEMAER for de fem
   `m<X>-rollback-v1`, og `_sjekk_grenser`-armene erstattes med
   `_grenser_rollback_modul` (samme sjekker som `rollback-m56-v1`).
3. Manifest M-14: `rollback_testet: ja` → alle ja → `status: aktiv`,
   `driftstilstand: produksjon`; `plattformdata.js` 14 → `i_drift`
   (pinnet av `test_modulstatus_folger_manifestene`).
4. Gjenta for M-17, M-23, M-26, M-44 (halv dag hver når driveren står),
   så M-57 (samme drill; de tre nei-punktene er egne kjøringer).
5. M-37 og M-06 er egne figurer: M-37 er plattformkjerne (drillen er
   `rollback-m01`-formen på API-et), M-06 har ingen arbeider (punktet
   krever først at inntaket får en egen release — eller at manifestet
   sier at rollback måles av plattformens drill).

## 5. Anslag

Driveren + M-14 gjennom hele kjeden med CodeRabbit/CI: 2–3 dager.
Deretter ~½ dag per modul. Betaling kommer etter alle 57 (eiers vedtak
17/9), så dette er neste arc.
