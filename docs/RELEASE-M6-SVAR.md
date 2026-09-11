# M-6 svar: å svare fra sin egen postboks uten å åpne Outlook

Skrevet 10/9-2026, etter eiers spørsmål samme kveld: «blir det mulig at
kunden kan svare herfra også?» — «via outlook boksen».

Og etter eiers RETTELSE, som er premisset for hele armen:

> «Men for den delen, trenger vel ikke at den skal godkjennes under fire
> øyne, fordi den delen har med outlook å gjøre, målet er å gjøre den
> delen enklere for kunden å lese og sende email fra sin outlook uten å
> åpne outlook, slik at kunden har alt på et sted.»

Første utkast av utsendings-PR-en la sendingen gjennom **policyporten**
med en egen oppdragstype og fire øyne — mønsteret fra M-17, der
PLATTFORMEN finner på teksten. Rettelsen er korrekt på sakens premiss:
her skriver mennesket teksten selv, og policyporten er bygget for å
holde **agenten** i bånd, ikke brukeren. Oppdragstypen og
policyutvidelsen ble fjernet, ikke forsvart.

Det som IKKE falt bort, er alt som verner personopplysninger: teksten er
kryptert i basen, feilgrunnen er en kode og ikke leverandørens tekst, og
utsendingsveien har ingen egen mottaker.

## 0. Hva kjeden er (178–181)

| Ledd | Hva | PR |
|---|---|---|
| Samtykket | `epost_kilde.scope` husker hva samtykket FAKTISK ga. Scopet utvidet til `Mail.Send`; `kan_svare` leses av samtykkets egne ord, ikke av vårt ønske. Kilder koblet før 178 har `NULL` = kun lesing | 178 |
| Utkastet | `POST /v1/epost/meldinger/{id}/svarutkast` (`epost:utkast:behandle`): mennesket skriver teksten, den lagres tenant-DEK-kryptert og fødes `foreslatt`. `POST …/utkast/{id}/dom` godkjenner eller forkaster. Hver dom bærer sin egen aktør og sitt eget tidspunkt — en angring er en NY dom | 179 |
| Køen | `POST /v1/epost/utkast/{id}/send` (samme scope, **ingen policyport**) setter utkastet `sendes` og skriver evidens `epost.svar_bestilt`. Web-API-et sender aldri selv: nøkkelen til postboksen ligger hos bakgrunnsprosessen (088) | 180 |
| Utsendingen | planarbeiderens runde, hvert 5. minutt: `m6_sendekandidater` (kryss-tenant), tråd-id og kryptert tekst fra `m6_for_utsending` — **ingen adresse** — og Graph `POST /me/messages/{id}/reply`. `m6_svar_sendt` er idempotent; `m6_svar_feilet` krever en KODE | 181 |

## 1. Utrulling

Merge → CI → deploy kjører `opp.sh` (migrasjoner t.o.m. 181). **Ingen
vertssteg utover deployen**: ingen ny hemmelighet, ingen ny rolle, ingen
ny unit, ingen policyendring. Sendingen går i planarbeiderens runde som
alt kjører.

Kill-switchen er inntakets: `DISPONIT_EPOST_INNTAK=av` i
`/etc/disponit/plan/konfig` stopper hele M-6-runden, sendingen med.

**Eksisterende kilder må kobles på nytt.** Scopet ble utvidet i 178, og
et refresh-token utstedt før det bærer ikke `Mail.Send`. Flaten viser
det: en kilde uten sendetilgang får ingen «Send svaret»-knapp, fordi en
knapp som alltid feiler er en løgn om hva systemet kan.

## 2. Ventetiden er sagt i flaten

Eier godtok forsinkelsen på ett vilkår: «Det er bare å ha liten notat at
eposten sendes om 5 minutter». Notatet står i svarseksjonen. Køen er
ikke et hinder, den er hvor nøkkelen bor.

## 3. Bevisrunden (`m6-svar-v1`, elleve punkter)

| # | Punkt | Måles ved |
|---|---|---|
| 1 | `utkast_i_klartekst_i_basen` | rå SELECT på `epost_utkast` gir ciphertext, aldri svarteksten |
| 2 | `dom_uten_aktor_eller_tidspunkt` | hver dom skriver sin egen `avgjort_ts`/`avgjort_av`; gjenspill er stille ja |
| 3 | `sendt_satt_av_en_dom` | `m6_avgjor_utkast` avviser `sendt` — kvitteringens vei er utsendingens |
| 4 | `utkast_til_slettet_melding` | en slettet melding har ingen tråd; både skriving og sending stopper |
| 5 | `utkastvei_uten_eget_scope` | `epost:kilde:administrer` åpner ikke utkastveien (088s `epost:utkast:behandle`) |
| 6 | `sendingen_ble_en_agenthandling` | ingen oppdragstype `epost.svar*`, ingen policyport, ingen fire øyne |
| 7 | `sendt_uten_samtykkets_sendescope` | en kilde uten `Mail.Send` i sitt eget `scope` stoppes i døra |
| 8 | `sendt_noe_som_ikke_sto_i_ko` | runden sender bare `sendes`; `foreslatt` og `forkastet` går aldri ut |
| 9 | `utsending_med_egen_mottaker` | veien har ingen `toRecipients`: den svarer i tråden |
| 10 | `feilgrunn_med_persondata` | grunnen må matche `^[a-z][a-z0-9_]{0,63}$` — leverandørens tekst kan bære en adresse |
| 11 | `forbigaende_feil_konsumerte_koen` | 5xx lar raden stå `sendes`; bare 401/403 terminerer som `feilet` |

Artefaktet skrives som `deploy/staging/artefakter/m6-svar-v1-<ts>.json`
etter skjemaet `artefakt-m6-svar-skjema.json` (generert fra
`M6_SVAR_INVARIANTER`). Ja-punktet `rundtur_paa_disponit_com` krever en
EKTE runde: eier svarer fra flaten på en melding i sin egen postboks, og
svaret kommer fram i tråden.

## 4. Grensene som står igjen, sagt høyt

* **Svar, ikke ny e-post.** Veien er Graph `reply` i en tråd som alt
  finnes. Å skrive til noen som ikke har skrevet først krever en
  mottakervei som ikke finnes — og som er en helt annen dom.
* **Ingen vedlegg.** Svaret er tekst. Inntaket laster ikke ned vedlegg,
  og utsendingen legger ingen ved.
* **Ingen agent skriver teksten.** `epost_klassifisering` står fortsatt
  tom, og modellveien er ikke bygget. Dommen 31/8 — fire ukers
  foreslå-drift med `feil_mottaker=0` før en agent får foreslå tekst —
  gjelder uendret. Den handler om AGENTENS sending, og den er ikke
  bygget her.
* **Den pinnede invarianten `modul_sendevei_finnes` (m6-v1, planens §7)
  står, og bør leses nøyaktig.** Den måler modulens EGNE filer
  (`platform/modules/m06_epost`), og de er fortsatt uten sendevei.
  Svararmen bor i kjernen, og den sender menneskets egen tekst. Skulle
  noen en dag legge en modellvei inn i modulen, felles porten fortsatt.
