# PR-015 SPESIFIKASJON v3 — DELTA (P1a → GO)

**Draft: Claude.ai · Kun P1a. P2, migrasjon 018, resolverdiversitet,
M-37-koblingen, kapabilitetsutstedelsen og ryddetimerne er urørt og
åpnes ikke.**

Reviewen har rett, og feilen er min samme feil ett nivå ned: forrige gang
lovet evidensgrensen en spredning uten mekanisme; denne gangen lovet den
en grense sterkere enn mekanismen. **Hashen fordeler statistisk;
scheduleren må garantere.** Nedenfor gjør jeg det siste eksplisitt og
retter grensene til det algoritmen faktisk kan bevise.

## 1. Tre køer, streng prioritet

Ferskhet vinner over fordeling. Rekkefølgen er absolutt, og en rad kan
aldri holdes tilbake av hensyn til en pen graf:

| # | Kø | Regel |
|---|---|---|
| **1** | **Sikkerhetsnett** — `siste_vellykkede_revalidering < now() - 26 t` | **Utenfor budsjettet. Aldri utsatt, aldri kappet.** |
| 2 | **Normalslott** — radens minutt falt i vinduet, og raden er ≥ 20 t gammel | Innenfor budsjettet |
| 3 | **Etterslep** — slott passert mens timeren var nede, eldste slott først | Innenfor budsjettet, det som er igjen etter kø 2 |

Kø 1 er ikke ubegrenset arbeid, bare ubegrenset *rett til å bli plukket*:
oppslagene kjøres med **fast samtidighetsgrense C** (v1: 8 parallelle
resolveroppslag). Ingen rad droppes; køen dreneres så fort
infrastrukturen tillater. Overskrides budsjettet av kø 1, er det en
**målt og rapportert hendelse**, ikke en feil.

## 2. Budsjettet er absolutt, ikke et påslag

«25 % ekstra ut over normal andel» var relativt til en størrelse
mekanismen ikke kontrollerer. Rettet — budsjettet regnes mot populasjonen:

```
N = antall rader med status IN ('verifisert','avklaring_kreves')
K = ceil(0.10 * N)      -- hard tak per kjøring for kø 2 + kø 3 samlet
```

- **K håndheves med `LIMIT`,** ikke som forventning. Rader fra kø 2 som
  ikke får plass, blir etterslep og plukkes neste kjøring — de mister
  ikke slottet sitt permanent, siden slottet er avledet og ikke lagret.
- **Hashskjevhet absorberes dermed av taket.** At en time tilfeldigvis
  hasher 63 rader i stedet for 21 er nå uten betydning for grensen: 63 er
  fortsatt godt under K for realistiske N, og over K blir resten etterslep.
- **Etterslepet er stabilt fordi K > gjennomsnittlig normallast.**
  Timeren kjører hver time, så normallasten er ≈ N/24 ≈ 0,042·N mens
  K = 0,10·N. Ledig kapasitet er ≈ 0,058·N per time.
  **Utledning for seks timers outage:** etterslep ≈ 6·N/24 = 0,25·N,
  drenert på ≈ 0,25/0,058 ≈ **4,3 timer**. 24-timersgrensen holder med
  bred margin, og marginen er nå regnet, ikke antatt.

## 3. Hva som er bevis og hva som er observasjon

Dette er skillet reviewen ber om, og det skal stå i dokumentet fordi det
ellers blir borte i neste runde:

- **Garantert av scheduleren (sikkerhets-/kontraktnivå):** kø 2 + kø 3
  overskrider aldri K per kjøring · kø 1 kappes aldri · ingen rad forlater
  planen · retry forskyver ikke normalplanen.
- **Statistisk egenskap av hashen (operasjonell, målt):** hvor jevnt
  radene faktisk fordeler seg over døgnet.
  `sha256(hostname) mod 1440` er tilnærmet uniform, men **garanterer ikke
  matematisk** at ingen time får > 10 % av en vilkårlig populasjon.
  Skjevhet er derfor en driftsegenskap vi måler og eventuelt justerer
  etter — aldri et sikkerhetsbevis.

## 4. Reviderte evidensgrenser (erstatter `revalidering-015-v1`-tillegget i v2)

**Håndhevede grenser (invarianter):**
`budsjett.ko2_pluss_ko3_over_K = 0` (også på skjev populasjon) ·
`sikkerhetsnett.utsatt = 0` · `sikkerhetsnett.rad_over_26t_uplukket = 0` ·
`plan.uendret_etter_restore = ja` · `plan.forskjovet_av_retry = 0` ·
`recovery.etterslep_igjen_etter_24t = 0` (6 t outage) ·
`etterslep.monotont_synkende_under_drenering = ja`.

**Målte egenskaper (rapporteres, ingen bestått/ikke bestått):**
`fordeling.maks_andel_per_time` for testpopulasjonen ·
`sikkerhetsnett.kjoringer_over_K` · `dreneringstid_timer` ved 6 t outage.

*(Grensene `bootstrap/steadystate.maks_andel_per_time ≤ 0.10` og
`recovery.maks_andel_per_time ≤ 0.125` fra v2 utgår som porter og
gjenoppstår som målinger — de var sterkere enn mekanismen.)*

## 5. Tester (erstatter porter 21–23 og 26 fra v2)

21. **Konstruert skjev populasjon:** hostnames valgt slik at ≥ 3·K rader
    hasher til samme time → kø 2 + kø 3 overskrider aldri K; overskuddet
    blir etterslep og dreneres; ingen rad tapt
22. Bootstrap, 500 rader verifisert i samme sekund → alle revalidert innen
    et døgn, K aldri overskredet, faktisk fordeling **rapportert**
23. Seks timers outage → etterslep monotont synkende, tomt innen 24 t,
    K aldri overskredet av kø 2+3
26. Rad passerer 26 t → plukket i samme kjøring **selv når K allerede er
    brukt opp**; totalen for kjøringen overskrider K, og hendelsen er
    talt i `sikkerhetsnett.kjoringer_over_K`
26b. Kø 1 med 200 rader → samtidighet aldri over C, null rader droppet

Øvrige porter fra v2 står uendret.

---

```
NÅ:    v3-deltaet (kun P1a) tilbake gjennom spesifikasjonsporten
       — ChatGPT (Eier relayer) — docs/PR-015-OPERATIVT-LAG-v3-DELTA.md
NESTE: Ved GO: konsolidert implementeringsklarsignal for PR-015
       (spesifikasjon + v2 + v3 slås sammen, full DDL for migrasjon 018,
       deltaformen forlates) — Claude.ai
       — docs/PR-015-IMPLEMENTERINGSKLARSIGNAL.md
```
