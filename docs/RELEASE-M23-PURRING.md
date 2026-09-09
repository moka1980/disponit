# M-23 purring som selvbetjening (ARC B) — nøyaktig hva som må kjøres

Skrevet 9/9-2026. Kjeden er på main t.o.m. PR 5 (#436) og flaten i
PR 6. Alt under rører `/opt/disponit` og `/etc/disponit` og kjøres
derfor KUN av eier. Rekkefølgen er bindende: modulen kan ikke claime
før den er registrert og onboardet, og utløseren bokfører ingenting
for tenanten før modulen er claimbar (`bestillingstype_utilgjengelig`
er plattformtilstand, PR 3) — så ingenting går tapt om stegene tar tid.

## 0. Hva kjeden er (146–151)

| Ledd | Hva | PR |
|---|---|---|
| Adressen | `mottaker_epost` på fordringen, kryptert med tenantens DEK; flaten viser maske | #431 |
| Bestillingstypen | `purring.send` gjennom `POST /v1/bestilling`; trinnet er dørens (`m23_fordring_for_purring`), aldri bestillerens; `v_fordring` attesterer `forfall_passert_dager` (målt) og `ingen_aktiv_tvist` | #432 |
| Utløseren | planarbeiderens runde (`plan.materialiser` → `plan.purring`): kandidater = åpen fordring + `trinn_forfalt` + mottaker + ikke alt bestilt; bestiller som policyrollen `agent`; én bestilling per fordring og trinn; kill-switch `DISPONIT_PURRING_UTLOSER=av` | #433 |
| Eiermodulen | `disponit-m23.service` (`drift.m23_arbeider` → `modules.m23_fordring.controller`): claim over API-et, adressen dekryptert i claim-svaret (`utforelse`), husets SMTP i tenantens navn, HMAC-signert kvittering; uvisst SMTP-utfall er terminalt (`sending_uviss`) | #435 |
| Bokføringen | kvittering `utfort` → `m23_purring_sendt`: fordringen på trinnet som ble purret, hendelsen `purring`, evidens `purring.sendt`; sakspayloaden bærer nok til at R1 kan bygge oppdraget | #436 |
| Flaten | avsenderprofil (navn + svar-til), purringshistorikk per fordring, grensen `m23-purring-v1` | PR 6 |

## 1. Utrulling (skjer av seg selv)

Merge → CI → deploy-workflowen kjører `opp.sh` (migrasjoner t.o.m.
151). Ingen ny rolle, ingen ny credential på verten for PR 1–5.

## 2. Nøkler (eier, på verten)

1. **`v_fordring` i `DISPONIT_ATT_NOKLER`** — både API-ets
   (`/etc/disponit/api/…`) og planarbeiderens
   (`/etc/disponit/plan/DISPONIT_ATT_NOKLER`). Formen er den samme som
   for de andre verifikatorene: `{"v_fordring": {"<nokkel_id>":
   "<hemmelighet ≥ 32 tegn>"}}`. Uten den mintes ingen attestasjon og
   hver bestilling går til unntakskøen (trygt, men ikke automatisert).
2. **Modulens kvitteringsnøkkel:** `/etc/disponit/m23/kvitteringsnokkel.json`
   `{"verifikator": "<navn>", "nokkel_id": "<id>", "hemmelighet": "<…>"}`
   — og SAMME verifikator/nøkkel inn i API-ets `DISPONIT_ATT_NOKLER`
   (kvitteringen verifiseres der, mot app-state, aldri mot basen).
   Bruk gjerne `v_fordring`-nøkkelen til begge.

## 3. Modulens konto og konfig (eier, på verten)

```sh
sudo useradd --system --home /nonexistent --shell /usr/sbin/nologin disponit-m23
sudo install -d -m 0750 -o root -g disponit-m23 /etc/disponit/m23
# konfig: API-URL, kvitteringsnøkkelsti og husets SMTP (samme som varselsenderen)
sudo tee /etc/disponit/m23/konfig >/dev/null <<'K'
DISPONIT_API_URL=http://127.0.0.1:8099
DISPONIT_KVITTERINGSNOKKEL=/etc/disponit/m23/kvitteringsnokkel.json
DISPONIT_SMTP_VERT=…
DISPONIT_SMTP_PORT=587
DISPONIT_SMTP_BRUKER=…
DISPONIT_SMTP_PASSORD=…
DISPONIT_SMTP_AVSENDER=…
K
sudo chmod 0640 /etc/disponit/m23/konfig /etc/disponit/m23/kvitteringsnokkel.json
sudo chgrp disponit-m23 /etc/disponit/m23/konfig /etc/disponit/m23/kvitteringsnokkel.json
sudo cp /opt/disponit/aktiv/deploy/staging/disponit-m23.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## 4. Registrering av modulkjeden (eier, med migrator-DSN)

```sh
cd /opt/disponit/aktiv
DISPONIT_MIGRATOR_URL=… .venv/bin/python deploy/staging/registrer-m23-fordring.py \
    m23-r1 <kontrakt_hash> <artifact_digest> <payload_skjema_hash> <kvittering_skjema_hash>
```

Idempotent: `installer_modul` → `registrer_kontrakt`
(`krever_outbox`/`kompenserende`) → `registrer_release` (manifestets
kanoniske projeksjon regnes ut) → `registrer_oppdragstype`
(`purring.send`). Ingen artefakttype — kvitteringen er evidensen.
Hashene er release-materialets (immutable rader; sjekk formen FØR):
`artifact_digest` = sha256 av den utsjekkede `platform/modules/m23_fordring`
(f.eks. `tar -C platform/modules -cf - m23_fordring | sha256sum`).

Deployment-raden (`moduldeployment … livslop='claiming'`) settes av
onboardingen/aksepten på vanlig vis (035/052); til den finnes svarer
claim 403 og utløseren bokfører ingenting.

## 5. Onboarding → modultoken (eier)

```sh
# drift-token med modules:onboard (token-cli, TTY)
curl -s -X POST https://disponit.com/v1/modul/onboarding \
  -H "authorization: Bearer $DRIFT_TOKEN" -H 'content-type: application/json' \
  -d '{"modul_id":"m23_fordring","miljo":"<miljo>","release_id":"m23-r1"}'
# → hemmelighet (engangs) → innløs:
curl -s -X POST https://disponit.com/v1/modul/onboarding/innlos \
  -H 'content-type: application/json' -d '{"hemmelighet":"<…>"}'
# → token → /etc/disponit/m23/DISPONIT_MODULTOKEN (0640 root:disponit-m23)
sudo systemctl enable --now disponit-m23
sudo journalctl -u disponit-m23 -n 5     # forvent {"hendelse":"m23_arbeider_oppe"}
```

## 6. Tenanten (Fjordlys på `disponit`, via portalen)

1. Policy: bransjemalen har `purring.send` (auto, unntakskø, 1/14 d
   per faktura, `forfall_passert_dager` ≥ 14, tillatt for `agent`).
   Aktiver den (fire øyne) — eller en kopi der inkassovarsel er
   `alltid_stopp` hvis det skal være eierens ord.
2. Purreplan (3/14/28 døgn) og avsenderprofil (`#/fordring`).
3. Fordringer med `mottaker_epost` til en postkasse du kontrollerer.

## 7. Bevisrunden (`m23-purring-v1`, ti punkter)

Hvert punkt måles som (forsøk, brudd) og ja-punktet er
`rundtur_paa_disponit_com`. Kjøres av meg etter §2–§6, mot
disponit.com, med tre fordringer (0, 5 og 20 døgn over forfall):

| # | Punkt | Måles ved |
|---|---|---|
| 1 | `adresse_i_klartekst_utenfor_fordringen` | `GET /v1/fordring`, hendelser, oppdragets kvittering, journalen: aldri adressen |
| 2 | `bestilling_uten_policy` | policy uten `purring.send` → runde bestiller ingenting, ingen beslutning |
| 3 | `sending_uten_mottaker` | fordring uten adresse → ikke kandidat; menneskelig oppdrag → `feilet mottaker_mangler` |
| 4 | `dobbel_bestilling_samme_trinn` | to planrunder → én rad i `purringsbestilling` |
| 5 | `dobbel_sending_samme_oppdrag` | én e-post i postkassen per oppdrag; kvittering to ganger = idempotent |
| 6 | `trinn_valgt_av_bestiller` | `trinn` i bestillingskroppen → 400; flyttet fordring → `trinn_flyttet` |
| 7 | `policygrense_omgaatt` | 5 døgn → brudd i unntakskøen; andre bestilling innen 14 d → brudd |
| 8 | `inkasso_sendt_automatisk` | trinn `inkasso` finnes ikke i kontrakten; inkassovarsel → unntakskø når policyen sier det |
| 9 | `kvittering_uten_bokforing` | fordringen på trinn 1, hendelse `purring`, evidens `purring.sendt` |
| 10 | `kill_switch_konsumerte_trinn` | `DISPONIT_PURRING_UTLOSER=av` → ingenting; på igjen → kandidaten står |

Artefaktet skrives som `{"krav_id":"m23-purring-v1","bestatt":true,
"maalt":{…}}` og måles med `manifestskjema._sjekk_grenser`.
