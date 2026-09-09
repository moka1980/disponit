# M-44 kampanje som selvbetjening (ARC B, modul 2) — nøyaktig hva som må kjøres

Skrevet 9/9-2026. M-23-purringens form (docs/RELEASE-M23-PURRING.md),
kampanjens dommer. Alt under rører `/opt/disponit` og `/etc/disponit`
og kjøres derfor av eier — eller av meg med `m44-oppsett.sh`, som for
M-23. Rekkefølgen er bindende: modulen kan ikke claime før den er
registrert og onboardet, og utløseren bokfører ingenting for tenanten
før modulen er claimbar (`bestillingstype_utilgjengelig` er
plattformtilstand) — så ingenting går tapt om stegene tar tid.

## 0. Hva kjeden er (153–158)

| Ledd | Hva | PR |
|---|---|---|
| Adressen og innholdet | `kontakt` på mottakeren, kryptert med tenantens DEK (flaten viser maske); `emne`/`tekst` på kampanjen | #440 |
| Bestillingstypen | `kampanje.send` gjennom `POST /v1/bestilling`: én mottaker i én kampanje på sendedagen; kroppen er to referanser og ett omfang; `v_samtykke` attesterer `samtykke_gyldig` (registerets dom på dagen) og `avmeldingslenke`; målportene (avlyst/ikke i planen/deaktivert/uten adresse/uten innhold → 409) FØR kvote | #441 |
| Utløseren | planarbeiderens runde (`plan.materialiser` → `plan.kampanje`): kandidater = planlagt par + kampanje registrert med innhold + dato nådd + mottaker aktiv med adresse OG samtykkehistorikk + ikke alt bestilt; bestiller som policyrollen `agent`; én bestilling per (kampanje, mottaker); kill-switch `DISPONIT_KAMPANJE_UTLOSER=av` | #442 |
| Eiermodulen | `disponit-m44.service` (`drift.m44_arbeider` → `modules.m44_kampanje.controller`, delt mekanikk i `modules.felles.levering`): claim over API-et, adressen dekryptert og teksten i claim-svaret (`utforelse`), samtykket spurt en gang til på dagen, husets SMTP i tenantens navn (avsenderprofil `POST /v1/kampanje/avsender`), avmeldingslenken i hver e-post, HMAC-signert kvittering; uvisst SMTP-utfall er terminalt | PR 4 |
| Bokføringen | kvittering `utfort` → `m44_kampanje_levert`: raden i `kampanjelevering`, evidens `kampanje.levert`; sakspayloaden bærer referansene så R1 kan bygge oppdraget | PR 5 |
| Flaten | avsenderprofil, leveransestatus per kampanje, leveringslinjene per kampanje (maske, aldri adresse), grensen `m44-kampanje-v1` | PR 6 |

## 1. Utrulling (skjer av seg selv)

Merge → CI → deploy-workflowen kjører `opp.sh` (migrasjoner t.o.m.
158). Ingen ny rolle på verten.

## 2–5. Nøkkel, konto, registrering, onboarding (én kommando)

```sh
python3 -c 'import secrets; print(secrets.token_urlsafe(48))' > /root/v_samtykke.hemmelighet
chmod 0600 /root/v_samtykke.hemmelighet
sudo bash /opt/disponit/aktiv/deploy/staging/m44-oppsett.sh /root/v_samtykke.hemmelighet
```

Skriptet er M-23-skriptets form og idempotent: `v_samtykke/vs1` inn i
`DISPONIT_ATT_NOKLER` (API + plan, materialisert via `opp.sh`), konto
`disponit-m44`, `/etc/disponit/m44/{konfig,kvitteringsnokkel.json}`
(husets SMTP fra `varsel/smtp.env`), unit, `setfacl` på
`/etc/disponit`, registrering av modulkjeden som `m44-r1`
(`registrer-m44-kampanje.py`), `bytt_release` → `claiming` i `staging`,
modulhode `aktiv`, onboarding via et engangs drift-token som
tilbakekalles, `systemctl enable --now disponit-m44`.

## 6. Tenanten (Fjordlys på `disponit`, via portalen)

1. Policy: utvidelsen `policies/utvidelser/kampanje-send.yaml` inn i
   utkastet (verifikatoren `v_samtykke` og handlingen `kampanje.send`:
   auto, unntakskø, 2 per 30 døgn per mottaker, vilkår
   `samtykke_gyldig` + `avmeldingslenke`, tillatt for `agent`) → fire
   øyne. Til da svarer bestillingsveien `ukjent_handling`, og utløseren
   bestiller ingenting (`kampanje_uten_policy` i journalen).
2. Avsenderprofil (`#/kampanje`), grense (2/7/730 er standard).
3. Mottakere med samtykke (`gitt`/`bekreftet`) og adresse; kampanje med
   innhold, avmeldingslenke og dato; mottakerne i planen.

## 7. Bevisrunden (`m44-kampanje-v1`, ti punkter)

| # | Punkt | Måles ved |
|---|---|---|
| 1 | `adresse_i_klartekst_utenfor_registeret` | `GET /v1/kampanje`, leveringene, oppdragets kvittering, saken, journalen: aldri adressen, aldri teksten |
| 2 | `bestilling_uten_policy` | policy uten `kampanje.send` → runden bestiller ingenting, ingen beslutning |
| 3 | `levering_uten_samtykke` | aldri samtykket → ikke kandidat; trukket etter plan → brudd/sak; trukket før claim → `feilet samtykke_ugyldig` |
| 4 | `dobbel_bestilling_samme_par` | to planrunder → én rad i `kampanjebestilling` |
| 5 | `dobbel_levering_samme_oppdrag` | én e-post i postkassen per oppdrag; kvittering to ganger = idempotent |
| 6 | `levering_uten_avmeldingslenke` | lenken står i e-posten; uten lenke → `malfeil` |
| 7 | `innhold_eller_adresse_mangler` | 409 fra bestillingsveien; ikke kandidat; `feilet` fra modulen |
| 8 | `policygrense_omgaatt` | tredje bestilling innen 30 d → brudd i unntakskøen |
| 9 | `kvittering_uten_bokforing` | raden i `kampanjelevering`, evidens `kampanje.levert` |
| 10 | `kill_switch_konsumerte_par` | `DISPONIT_KAMPANJE_UTLOSER=av` → ingenting; på igjen → paret står |

Artefaktet skrives som `{"krav_id":"m44-kampanje-v1","bestatt":true,
"maalt":{…}}` og måles med `manifestskjema._sjekk_grenser`.
