# M-14 bokføring som selvbetjening (ARC B, modul 4) — nøyaktig hva som må kjøres

Skrevet 10/9-2026. M-23/M-44/M-17-formen (docs/RELEASE-M17-KUNDESERVICE.md),
bilagets dommer. Alt under rører `/opt/disponit` og `/etc/disponit` og
kjøres av eier — eller av meg med `m14-oppsett.sh`, som for de tre første.

## 0. Hva kjeden er (165–168)

Bransjemalen har båret `faktura.bokfor` (auto, ≤ 25 000 NOK) og
`faktura.bokfor_stor` (auto_med_vilkaar, ≤ 100 000) siden M-1 uten at de
noen gang fyrte (KLYNGE4-FUNDAMENT); registeret sa «bokfører ingenting».
Nå er bokføringen plattformens arm rundt registeret — registeret selv
signerer og bokfører fortsatt ingenting, og doktrineportene måler det.

| Ledd | Hva | PR |
|---|---|---|
| Bestillingstypen | `faktura.bokfor` / `faktura.bokfor_stor`: én referanse (`faktura:<uuid>`), omfang `bilag`; målport-døra `m14_for_bokforing` (165) leser tilstanden og de tre kontrollradene 106 kjørte; `dublettsjekk` + `mva_validert` attesteres som `v_regnskap`, `leverandor_i_register` som `v_register` — et `avvik` er en usann attestasjon (sak). 404/409 FØR kvote (ukjent; avvist/bokført/over beløpsgrensen uten manuell kontroll). Beløpet i eventet er brutto, så `belop_maks` måler det | #455 |
| Utløseren | planarbeiderens runde (`plan.faktura`): kandidater = mottatt/kontrollert + tre rene kontroller + (over tenantens beløpsgrense bare med manuell kontroll) + ikke bestilt; handlingen velges av POLICYENS `belop_maks` (liten før stor), over begge → `menneske_kreves` uten beslutning; agentrollen; én bestilling per faktura (`bokforingsbestilling`, 166); kill-switch `DISPONIT_BOKFORING_UTLOSER=av` | #456 |
| Eiermodulen | `disponit-m14.service` (`drift.m14_arbeider` → `modules.m14_fakturakontroll.controller`): claim over API-et, bilaget i claim-svarets `utforelse` (`api/bokforing.py`, 165-døra), bygger bilaget som en AVSKRIFT (ingen omregning), kvitterer signert som `v_regnskap`. Ingen SMTP, ingen base: v1-koblingen er husets bilagsregister | #457 |
| Bokføringen | kvittering `utfort` → `m14_faktura_bokfort` (167): bilaget registreres i M-13 (`ut`, fakturaens brutto, leverandøren som motpart, deterministisk bilag_id), fakturaen `bokfort` med bilagets identitet, evidens `faktura.bokfort`; døra nekter avvist og et bilag som avviker fra fakturaen; `bokfort` er aldri en dom et menneske kan sette | PR 4 |
| Flaten | statusen «Bokført», plattformens bestilling og bilaget per faktura som TEKST (`m14_bokforingsbildet`, 168), grensen `m14-bokforing-v1` | PR 5 |

## 1. Utrulling (skjer av seg selv)

Merge → CI → deploy-workflowen kjører `opp.sh` (migrasjoner t.o.m. 168).

## 2–5. Nøkkel, konto, registrering, onboarding (én kommando)

```sh
sudo bash /opt/disponit/aktiv/deploy/staging/m14-oppsett.sh
```

INGEN NY HEMMELIGHET: `v_regnskap` står i `DISPONIT_ATT_NOKLER` (API +
plan) siden M-1; skriptet leser den fra API-ets nøkkelfil og legger den i
`/etc/disponit/m14/kvitteringsnokkel.json`. Konto `disponit-m14`,
`/etc/disponit/m14/konfig` (uten SMTP), unit, registrering `m14-r1` (to
oppdragstyper på samme kontrakt), `bytt_release` → claiming, onboarding,
`systemctl enable --now disponit-m14`. `v_register` finnes alt i prod.

## 6. Tenanten (Fjordlys på `disponit`)

INGEN POLICYENDRING: bransjemalen bærer begge handlingene, og tenantens
aktive policy (0.6.0) har dem. Terskler (beløpsgrense 25 000, mva-slingring)
og mva-satser står fra 8/9; leverandøren Nordkabel Engros AS er kjent.

## 7. Bevisrunden (`m14-bokforing-v1`, ti punkter)

| # | Punkt | Måles ved |
|---|---|---|
| 1 | `bokforing_uten_policy` | policy uten handlingene → runden bestiller ingenting (porten; prod-policyen har dem) |
| 2 | `bokforing_med_kontrollavvik` | mva-avvik / ukjent leverandør: ikke kandidat; manuell bestilling → sak (attestasjon negativ) |
| 3 | `bokforing_over_belopsgrense` | 30 000 som liten → `belop_over_grense`; 150 000 → `menneske_kreves` |
| 4 | `dobbel_bestilling_samme_faktura` | to planrunder → én rad i `bokforingsbestilling` |
| 5 | `dobbel_bokforing_samme_oppdrag` | kvittering to ganger → ett bilag |
| 6 | `bilag_avviker_fra_fakturaen` | annet beløp/motpart → døra nekter (porten) |
| 7 | `bokforing_uten_manuell_kontroll_over_grensen` | 30 000 uten manuell → 409, ikke kandidat |
| 8 | `bokforing_av_avvist_eller_bokfort` | avvist → 409 / `feilet`; `bokfort` som dom → 400 |
| 9 | `kvittering_uten_bokforing` | fakturaen `bokfort`, bilaget i `#/avstemming`, evidens |
| 10 | `kill_switch_konsumerte_fakturaer` | `DISPONIT_BOKFORING_UTLOSER=av` → ingenting; på igjen → kandidaten står |

## Gjennomført 10/9-2026 på disponit-srv

Alle fem PR-ene (#455–#459) ble merget og deployet 10/9 (migrasjoner
165–168). Vertsteget `deploy/staging/m14-oppsett.sh` FEILET første gang:
registerets herdede `registrer_oppdragstype` (040) nekter to typer der
den ene er strengprefiks av den andre (`faktura.bokfor` /
`faktura.bokfor_stor`), og hele registreringen rullet tilbake.
Testporten hadde satt typeradene inn rått og så det aldri. Fikset i #460
(main `c2cbedff`): ÉN oppdragstype med begge handlingene (M-23s form med
inkassovarselet), og porten går gjennom den herdede funksjonen. **Lærdom:**
en rigg som setter inn registerrader rått tester ikke registerets vakt —
gå samme dør som verten. Andre kjøring: `v_regnskap/k1` kopiert fra
`DISPONIT_ATT_NOKLER` (ingen ny hemmelighet), `disponit-m14` oppe,
`m14-r1` claiming, modulhode aktiv, drift-tokenet tilbakekalt.

Ingen policyendring: 0.6.0 bærer begge handlingene.

Bevisrunden (§7) gikk 10:26–10:45Z; artefaktet er
`deploy/staging/artefakter/m14-bokforing-v1-20260910T104500Z.json` og
porten `test_bevisartefaktet_passerer_grensen` måler det. Live: før
vertsteget plukket timer-rundene NK-2026-4471 (23 125 kr, kontrollert
av et menneske 8/9) og stoppet på `bestillingstype_utilgjengelig` uten
å bokføre (plattformtilstand konsumerer ikke). Riggen: R1 ren 12 500,
R2 mva-avvik, R3 ukjent leverandør, R4 30 000 uten manuell kontroll,
R5 31 000 med, R6 150 000 med, R7 avvist. Manuell bestilling: R2 → sak
129 (`rolle_ikke_tillatt`), R4/R7 → 409, ukjent → 404, `bokfort` som
dom → 400. Kill-switch av → ingenting; på → runden plukket 4:
NK-2026-4471 og R1 → `faktura.bokfor` (oppdrag 107, 109), R5 →
`faktura.bokfor_stor` (108), R6 → `menneske_kreves`; R2/R3/R4/R7 aldri
kandidater. Modulen kvitterte tre oppdrag `utfort` (200, én gang hver);
bilagene `LF-NK-2026-4471`, `LF-NK-2026-5101`, `LF-NK-2026-5105` står i
M-13s bilagsregister (`#/avstemming`) og fakturaene er `bokfort` med
bilagets identitet — NK-2026-4471 beholdt menneskets `avgjort_av`.
Runde 2 og timer-runden etter: stille, `bokforingsbestilling` uendret
(4). Flaten viser bestillingen og bilaget per faktura.

Seks gule funn: tre punkter målt bare i portene (uten policy — 0.6.0
bærer handlingene; bilaget som avviker; kvittering to ganger); manuell
bestilling av et avvik felles på ROLLEN før attestasjonene måles
(presedens); M-37 mangler verifikatormodul (sak 129 `manuell`); en
planrunde uten kandidater er stille; v1-koblingen er husets
bilagsregister (ingen ekstern regnskapskobling ennå); og
registreringen som nektet prefiks-overlapp (fikset i #460, lagret som
lærdom).
