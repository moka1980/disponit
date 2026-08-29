# PR-012 SPESIFIKASJON v8 — DELTA (to motorbindinger → GO)

**Draft: Claude.ai · Form C, rollefordeling og regresjonsport står.
To bindinger som hindrer at godkjenningen blir bredere enn mennesket ga.**

## 1. Eksakt feltsamsvar FØR overstyringsregelen vurderes

Uten dette kunne en godkjenning for `belop=50 000` blitt brukt på en
hendelse med `belop=500 000` — grensen ville blitt kontrollert mot feil
verdi. Motoren krever nå EKSAKT likhet mellom `MenneskeligGodkjenning`
og den serverbygde hendelsen for ALLE:

| Felt | Sammenligning |
|---|---|
| `tenant` | eksakt |
| `target_action` | eksakt mot hendelsens handling |
| `ressurs_id` | eksakt |
| `belop` | eksakt (Decimal-likhet, ikke ≤) |
| `valuta` | eksakt |
| `hi_integritet_hash` | eksakt mot sakens handlingsintensjon |

**Rekkefølge er bindende:** (1) bevis likhet på alle seks → (2) evaluer
`belop_maks` **mot hendelsens autoritative beløp** (ikke mot
godkjenningens kopi) → (3) anvend overstyringen.
Avvik i ETT felt → **STOPP + sikkerhetsrouting** (ikke stille avvisning —
et avvik her betyr at konvolutten forsøkes brukt på noe annet enn den ble
gitt for).
Negativ test per felt — seks tester, ikke én.

## 2. Én bundet grunnkode per godkjenning

`MenneskeligGodkjenning` manglet grunnkoden, så én godkjenning kunne
løftet ALLE godkjennbare grunnkoder motoren fant ved ny evaluering.
Rettet:

- **Godkjenningsrunden OG MAC-konvolutten binder nøyaktig én
  `(grunnkode, target_action)`.** Feltet `bundet_grunnkode` legges til
  begge (og til `MenneskeligGodkjenning`).
- Runden opprettes med grunnkoden fra den saken faktisk ble stoppet på —
  server-utledet fra unntakets begrunnelseskjede, aldri klientvalgt.
- **Motoren kan løfte KUN denne grunnkoden.**
- Har den nye evalueringen **flere blokkerende grunnkoder** → ingen
  TILLAT (godkjenningen dekker ikke resten).
- Er den bundne grunnkoden **ikke lenger blokkerende** → ingen TILLAT
  via overstyring (situasjonen er en annen enn den mennesket vurderte);
  utfallet blir det motoren ellers ville gitt.
- **Flergrunnsgodkjenning utsettes til egen protokoll** (deklarert).

**Revisjonsloggen registrerer den BUNDNE grunnkoden** (fra konvolutten),
ikke den motoren tilfeldigvis anvendte etterpå. Begrunnelseskoden
`menneskelig_godkjenning_anvendt` bærer: runde, godkjennere,
`bundet_grunnkode`, `belop_maks` brukt, `godkjennings_policy_hash`.

## 3. Konsekvens for UI og porten
- `tillatte_handlinger[]` for `godkjenn` er kun tilgjengelig når saken
  har **nøyaktig én** blokkerende grunnkode som ligger i `godkjennbare`.
  Flere blokkerende grunner → `godkjenn` utilgjengelig med
  `aarsak_utilgjengelig: flere_blokkerende_grunner`.
- `HandlingDialog` viser hvilken konkret grunn som godkjennes, i klartekst
  («Du godkjenner: beløp over policygrense — 45 000 NOK») — mennesket ser
  nøyaktig hva det binder seg til.

## 4. Tester (tillegg)
Seks feltavvikstester (tenant, target_action, ressurs_id, belop, valuta,
hi-hash) → hver gir STOPP + sikkerhetsrouting · grense evalueres mot
hendelsens beløp etter bevist likhet · godkjenning med grunnkode A løfter
ikke grunnkode B · to blokkerende grunnkoder → ingen TILLAT · bundet
grunnkode ikke lenger blokkerende → ingen overstyring anvendt ·
revisjonslogg viser bundet grunnkode fra konvolutten · UI skjuler
`godkjenn` ved flere blokkerende grunner.
