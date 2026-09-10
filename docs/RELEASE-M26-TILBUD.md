# M-26 tilbud som selvbetjening (ARC B, modul 5) — nøyaktig hva som må kjøres

Skrevet 10/9-2026. M-23/M-44/M-17/M-14-formen (docs/RELEASE-M14-BOKFORING.md),
tilbudets dommer. Alt under rører `/opt/disponit` og `/etc/disponit` og
kjøres av eier — eller av meg med `m26-oppsett.sh`, som for de fire første.

## 0. Hva kjeden er (169–174)

Bransjemalen har båret `tilbud.generer` (auto_med_vilkaar, ≤ 150 000 NOK,
vilkår `priser_fra_prisbok` og `laste_klausuler_uendret` via `v_prisbok`)
siden M-1 uten at den noen gang fyrte; prisboka (108) laget aldri et
tilbud. Nå er tilbudet et REGISTER ved siden av boka, og sendingen er
plattformens arm rundt det: registeret regner, et menneske godkjenner,
plattformen sender, kvitteringen bokfører. Boka selv er urørt
(108-doktrinen: ordet «tilbud» finnes ikke i prisbok.py/prisbok.js).

| Ledd | Hva | PR |
|---|---|---|
| Registeret | `tilbud` (kunden kryptert, maske i lista), `tilbudslinje` (listepris og enhetspris ≤ listepris fra boka på tilbudsdatoen, summen regnes av døra), `tilbudsklausul` (standardklausulene bundet i versjon); dommen `godkjent`/`forkastet` bare fra utkast; de to faktaene `priser_fra_boka`/`klausuler_uendret` regnes av registeret (169); flaten `#/tilbud` uten send-knapp | #462 |
| Bestillingstypen | `tilbud.generer`: én referanse (`tilbud:<uuid>`), omfang `tilbud`; målport-døra `m26_for_tilbud` (170) regner faktaene for ett tilbud; `priser_fra_prisbok` + `laste_klausuler_uendret` attesteres som `v_prisbok` — et avvik er en usann attestasjon (sak). 404/409 FØR kvote (ukjent; ikke godkjent/utløpt/uten linjer). Beløpet i eventet er summen, så `belop_maks` måler den. Utvidelsen `policies/utvidelser/tilbud-generer.yaml` bærer `persondata` i dataklassene | #463 |
| Utløseren | planarbeiderens runde (`plan.tilbud`): kandidater = godkjent + gyldig_til ≥ i dag + linjer + ikke bestilt; agentrollen (`agent:tilbud`); én bestilling per tilbud (`tilbudsbestilling`, 171); kill-switch `DISPONIT_TILBUD_UTLOSER=av` | #464 |
| Eiermodulen | `disponit-m26.service` (`drift.m26_arbeider` → `modules.m26_prisbok.controller`): claim over API-et, adressen dekryptert i claim-svarets `utforelse` (172-døra `m26_for_sending`, aldri i payloaden), linjene og den bundne klausulteksten, avsenderprofilen (`POST /v1/tilbud/avsender`), mala «tilbud-v1» som tekst-e-post over husets SMTP, HMAC-signert kvittering som `v_prisbok` | #465 |
| Bokføringen | kvittering `utfort` → `m26_tilbud_sendt` (173): tilbudet `sendt` (bare fra godkjent, bare via døra — aldri en dom), oppdrag/tid/mal frosset, evidens `tilbud.sendt` uten adresse; kvitteringen må være oppdragets tilbud | #466 |
| Flaten | avsenderprofilen, statusen «Sendt», plattformens bestilling og sending per tilbud som TEKST (`m26_tilbudsbildet`, 174), grensen `m26-tilbud-v1` | PR 6 |

## 1. Utrulling (skjer av seg selv)

Merge → CI → deploy-workflowen kjører `opp.sh` (migrasjoner t.o.m. 174).

## 2–5. Nøkkel, konto, registrering, onboarding (én kommando)

```sh
sudo bash /opt/disponit/aktiv/deploy/staging/m26-oppsett.sh
```

INGEN NY HEMMELIGHET: `v_prisbok` står i `DISPONIT_ATT_NOKLER` (API +
plan) siden M-1; skriptet leser den fra API-ets nøkkelfil og legger den i
`/etc/disponit/m26/kvitteringsnokkel.json`. Konto `disponit-m26`,
`/etc/disponit/m26/konfig` (husets SMTP fra `varsel/smtp.env`, som M-17),
unit, registrering `m26-r1`, `bytt_release` → claiming, onboarding,
`systemctl enable --now disponit-m26`.

## 6. Tenanten (Fjordlys på `disponit`)

POLICYENDRING KREVES — «ingen policyendring» gjaldt M-14, ikke M-26:
bransjemalens `tilbud.generer` har ikke `persondata` i dataklassene, og
kundens adresse er persondata. Utvidelsen
`policies/utvidelser/tilbud-generer.yaml` ERSTATTER handlingen (samme id)
i tenantens policy → ny versjon 0.7.0. Editoren kan ikke bytte en handling
— utkastet lages via API-et med et engangs-token (`policyforvalter`,
`policy:write`, tilbakekalt etterpå), som for M-44 og M-17; eier + én til
attesterer (fire øyne). Ingen ny verifikator: `v_prisbok` finnes.

Deretter: avsenderprofil (`#/tilbud` → «Avsenderen»), og tilbud med linjer
fra boka (KAB-PFXP-3x2.5 12,50/m, VP-SPLIT-6 28 900,00; TIME-MONT har
ingen pris og kan ikke stå i et tilbud) som et menneske GODKJENNER.

## 7. Bevisrunden (`m26-tilbud-v1`, ti punkter)

| # | Punkt | Måles ved |
|---|---|---|
| 1 | `tilbud_uten_policy` | før 0.7.0: runden logger `tilbud_uten_policy`, bestiller ingenting |
| 2 | `tilbud_uten_godkjenning` | utkast: ikke kandidat; manuell bestilling → 409; dom «sendt» → 400 |
| 3 | `priser_utenfor_boka` | linje under rabattgrensen (150 ‰) godkjent → sak (`priser_fra_prisbok` usann) |
| 4 | `klausul_erstattet` | BET-14 får ny versjon etter binding → sak (`laste_klausuler_uendret` usann) |
| 5 | `belop_over_policyens_tak` | sum over 150 000 → `belop_over_grense` |
| 6 | `dobbel_bestilling_samme_tilbud` | to planrunder → én rad i `tilbudsbestilling` |
| 7 | `dobbel_sending_samme_oppdrag` | kvittering to ganger → én evidens, `sendt` én gang |
| 8 | `utlopt_eller_uten_linjer` | utløpt tilbud → 409, ikke kandidat |
| 9 | `kvittering_uten_bokforing` | tilbudet `sendt` i `#/tilbud`, evidens `tilbud.sendt`, e-posten hos eier, ingen adresse utenfor registeret |
| 10 | `kill_switch_konsumerte_tilbud` | `DISPONIT_TILBUD_UTLOSER=av` → ingenting; på igjen → kandidaten står |

Artefaktet skrives som `deploy/staging/artefakter/m26-tilbud-v1-<ts>.json`
etter skjemaet `artefakt-m26-tilbud-skjema.json` (generert fra
`M26_TILBUD_INVARIANTER`), og porten `test_bevisartefaktet_passerer_grensen`
(PR 7) holder det innsjekket.

## Gjennomført 10/9-2026 på disponit-srv

Alle seks PR-ene (#462–#467) ble merget og deployet 10/9 (migrasjoner
169–174). Vertssteget `m26-oppsett.sh` gikk i ett (v_prisbok/k1 fra
`DISPONIT_ATT_NOKLER`, husets SMTP, `disponit-m26` oppe, `m26-r1`
claiming, modulhode aktiv, drift-token tilbakekalt). Policy 0.7.0
(utkast via API med engangs-token, tilbakekalt; eier + én til attesterte)
bærer `tilbud.generer` med `persondata`.

Bevisrunden (§7) gikk 14:43–14:45Z; artefaktet er
`deploy/staging/artefakter/m26-tilbud-v1-20260910T144500Z.json` og
porten `test_bevisartefaktet_passerer_grensen` holder det innsjekket.
Kort: kill-switch av → ingenting; på → T1 tillatt (oppdrag 110) og sendt
av modulen 2 s senere, T2 (rabatt) sak 130, T4 (173 400) sak 131; T3
(utkast) og T5 (utløpt) aldri kandidater, manuelt → 409; dom «sendt» →
400; runde 2 etter BET-14 v2 → T6 sak 132, ingen dobbel bestilling;
flaten viser status, bestilling og sending per tilbud; kundens adresse
står ingen steder utenfor registeret.

Gule funn (i artefaktet): saker 130–132 går manuell i M-37 (ingen
v_prisbok-verifikatormodul); en klausulendring gjør ALLE usendte tilbud
til «klausul endret» (produktvalg: skal `gyldig_fra` skjerme eldre
tilbud?); punkt 1 og 7 er målt i portene, ikke live; runden logger bare
når den plukket noe; tenantens egen svar-til-adresse står i lista
(presisering av «aldri adressen»); eiers bekreftelse av at e-posten kom
fram kom 10/9 ~19:30Z («Tilbud eposten har kommet») — funnet
`epost_bekreftelse_ventes` er fjernet fra artefaktet. CodeRabbits stående merknad om usignerte
bevisartefakter gjelder også her (felles oppfølging, som for M-14).

