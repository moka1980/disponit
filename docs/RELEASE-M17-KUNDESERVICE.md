# M-17 kundesvar som selvbetjening (ARC B, modul 3) — nøyaktig hva som må kjøres

Skrevet 9/9-2026. M-23/M-44-formen (docs/RELEASE-M23-PURRING.md,
docs/RELEASE-M44-KAMPANJE.md), svarets dommer. Alt under rører
`/opt/disponit` og `/etc/disponit` og kjøres av eier — eller av meg med
`m17-oppsett.sh`, som for de to første.

## 0. Hva kjeden er (160–164)

| Ledd | Hva | PR |
|---|---|---|
| Adressen og godkjenningen | avsenderens adresse kryptert på henvendelsen (AAD `m17:avsender`, maske i køen); dommen `godkjent` på utkastet — et menneskes ja til at plattformen sender | #448 |
| Bestillingstypen | `kundeservice.svar.send`: ett godkjent utkast til én henvendelse; `v_kundeservice` attesterer `svar_godkjent`, `v_dlp` attesterer `dlp_sjekk`, `ingen_okonomiske_lofter` og `mottaker_i_kontaktregister` — målt på utkastteksten av `api/svarkontroll.py` (heuristikk, funn som koder); målportene (lukket/unntakskø/uten adresse/uten svarvei → 409) FØR kvote | #449 |
| Utløseren | planarbeiderens runde (`plan.kundeservice`): kandidater = godkjent utkast + åpen henvendelse + ikke i unntakskøen + adresse + e-postkanal + ikke bestilt; agentrollen; én bestilling per utkast; kill-switch `DISPONIT_SVAR_UTLOSER=av` | #450 |
| Eiermodulen | `disponit-m17.service` (`drift.m17_arbeider` → `modules.m17_kundeservice.controller`): claim over API-et, adressen/emnet/utkastet dekryptert i claim-svaret, tilstanden spurt en gang til, husets SMTP i tenantens navn med signatur (`POST /v1/kundeservice/avsender`), «Re: <emne>», HMAC-signert kvittering | PR 4 |
| Bokføringen | kvittering `utfort` → `m17_svar_sendt`: utkastet `sendt`, henvendelsen lukket som «besvart», evidens `svar.sendt`; saken bærer referansene så R1 kan bygge oppdraget | PR 5 |
| Flaten | avsenderprofil, statusen som ord, plattformens utfall per utkast, grensen `m17-svar-v1` | PR 6 |

## 1. Utrulling (skjer av seg selv)

Merge → CI → deploy-workflowen kjører `opp.sh` (migrasjoner t.o.m. 164).

## 2–5. Nøkkel, konto, registrering, onboarding (én kommando)

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > /root/v_kundeservice.hemmelighet
chmod 0600 /root/v_kundeservice.hemmelighet
sudo bash /opt/disponit/aktiv/deploy/staging/m17-oppsett.sh /root/v_kundeservice.hemmelighet
```

`v_kundeservice/vk1` inn i `DISPONIT_ATT_NOKLER` (API + plan), konto
`disponit-m17`, `/etc/disponit/m17/{konfig,kvitteringsnokkel.json}`,
unit, registrering `m17-r1`, `bytt_release` → claiming, onboarding,
`systemctl enable --now disponit-m17`. `v_dlp` finnes alt i prod.

## 6. Tenanten (Fjordlys på `disponit`, via portalen)

1. Policy: utvidelsen `policies/utvidelser/kundeservice-svar.yaml` inn i
   et utkast (editoren kan ikke legge til handlinger/verifikatorer — jeg
   lager utkastet via API-et med et engangs-token, som for M-44) → fire
   øyne.
2. Avsenderprofil (`#/kundeservice`).
3. En henvendelse (kanal e-post, avsender = eiers adresse), et utkast,
   «Godkjenn for sending».

## 7. Bevisrunden (`m17-svar-v1`, ti punkter)

| # | Punkt | Måles ved |
|---|---|---|
| 1 | `adresse_eller_tekst_i_klartekst_utenfor_registeret` | køen, utkastlisten, oppdragets kvittering, saken, journalen: aldri adressen, aldri teksten utenfor innsynsscopet |
| 2 | `bestilling_uten_policy` | policy uten handlingen → runden bestiller ingenting |
| 3 | `sending_uten_godkjenning` | foreslått utkast → ikke kandidat; ugodkjent bestilling → sak |
| 4 | `dobbel_bestilling_samme_utkast` | to planrunder → én rad i `svarbestilling` |
| 5 | `dobbel_sending_samme_oppdrag` | én e-post per oppdrag; kvittering to ganger = idempotent |
| 6 | `personopplysning_i_svaret` | fødselsnummer i teksten → sak (dlp_sjekk usant) |
| 7 | `okonomisk_lofte_i_svaret` | «20 % rabatt» → sak |
| 8 | `svar_uten_mottaker_eller_svarvei` | skjema/lukket → 409, ikke kandidat, `feilet` fra modulen |
| 9 | `kvittering_uten_bokforing` | utkastet `sendt`, henvendelsen besvart, evidens |
| 10 | `kill_switch_konsumerte_utkast` | `DISPONIT_SVAR_UTLOSER=av` → ingenting; på igjen → kandidaten står |
