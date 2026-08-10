# PR-013 SPESIFIKASJON — Policyadministrasjon v1 (til ChatGPT-porten)

**Draft: Claude.ai · Å endre policy er å endre agentens fullmakter. Denne
leveransen får derfor samme strenghet som kodeutrulling: validering,
versjonering, diff, godkjenning, atomisk aktivering, rullbarhet, full
revisjon. Bygger på eksisterende `policyregister.py`, `policyer`-tabellen
med `en_aktiv_per_policy`-delindeks, skjema v0.2, JCS og PR-012s
mønstre — redesigner ingenting av dem.**

## 0. Fem ufravikelige prinsipper

1. **Aktive versjoner er uforanderlige.** Ingen redigering av en aktiv
   policy, noensinne. Endring = ny versjon.
2. **Aktivering er atomisk og reversibel.** Én aktiv versjon per
   (tenant, policy_id) — delindeksen finnes. Rullbakk = aktivering av en
   tidligere versjon, som er en ny aktiveringshendelse (aldri sletting).
3. **Ingen aktivering uten menneskelig godkjenning.** Minst én, og fire
   øyne der policyen selv krever det (§6).
4. **Ingen aktivering uten sett diff.** Godkjenneren må ha kvittert for
   den konkrete endringen — ikke bare for «versjon 7».
5. **Utvidelse av fullmakt er en egen risikoklasse** og merkes eksplisitt
   (§5). Innsnevring og utvidelse behandles ikke likt.

## 1. Livsløp

```
utkast (muterbart)  →  validert  →  godkjent  →  aktiv  →  historisk
   ↑ redigeres         ↑ frosset    ↑ frosset            ↑ ved ny aktivering
   └─ forkastes                                          └─ aldri slettet
```
- **`utkast`:** eneste muterbare tilstand. Egen tabell (`policyutkast`),
  ikke `policyer` — et utkast er ikke en policy.
- **`validert`:** utkastet har bestått skjema v0.2 + semantisk validering
  og er **frosset** (`innholds_hash` beregnet, JCS-kanonisert). Videre
  redigering = nytt utkast fra dette.
- **`godkjent`:** aktiveringsrunden er fullført (§6).
- **`aktiv`:** raden ligger i `policyer` med `aktiv=true`. Uforanderlig.
- **`historisk`:** tidligere aktiv. **Kan aldri slettes** (PR-008s
  retention-regel: versjoner referert av revisjonslogg eller ikke-terminale
  saker er beskyttet — nå utvidet til ALLE tidligere aktive versjoner).

## 2. Datamodell (migrasjon 012)

```sql
CREATE TABLE policyutkast (
  tenant TEXT NOT NULL, utkast_id TEXT NOT NULL,
  policy_id TEXT NOT NULL,
  basert_pa_versjon TEXT,              -- hvilken aktiv versjon det bygger på
  basert_pa_hash TEXT,                 -- for konfliktdeteksjon (§4)
  innhold JSONB NOT NULL,
  utkastversjon INT NOT NULL DEFAULT 1,-- optimistisk lås ved redigering
  status TEXT NOT NULL CHECK (status IN ('utkast','validert','godkjent','forkastet','aktivert')),
  innholds_hash TEXT,                  -- settes ved validering, deretter uforanderlig
  opprettet_av TEXT NOT NULL, opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, utkast_id)
);

CREATE TABLE aktiveringsrunde (            -- speiler godkjenningsrunde (PR-012)
  tenant TEXT NOT NULL, utkast_id TEXT NOT NULL, runde INT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('apen','klar','brukt','utlopt','kansellert')),
  diff_hash TEXT NOT NULL,               -- hva godkjennerne faktisk så (§4)
  utkast_innholds_hash TEXT NOT NULL,
  apnet TIMESTAMPTZ NOT NULL DEFAULT now(), utloper TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant, utkast_id, runde)
);
CREATE UNIQUE INDEX en_aktiv_aktiveringsrunde ON aktiveringsrunde
  (tenant, utkast_id) WHERE status IN ('apen','klar');

CREATE TABLE aktiveringsattestasjon (      -- speiler menneskelig_attestasjon
  ..., tenant, utkast_id, runde, bruker_id, rolle, authz_version,
  diff_hash TEXT NOT NULL,                 -- MÅ matche rundens
  konvoluttversjon, konvolutt_hash, mac, mac_key_id, jti, utloper,
  UNIQUE (tenant, jti),
  UNIQUE (tenant, utkast_id, runde, bruker_id)
);
```
Alle: RLS+FORCE, append-only der relevant, kolonnelås på frosne felt.
MAC-registeret fra PR-012 gjenbrukes (samme livssyklus, egen
konvoluttype `disponit_policy_activation_v1`).

## 3. Validering — tre lag, alle fail-closed

1. **Skjema v0.2** (`schema.py`, eksisterende) — struktur,
   `additionalProperties: false`.
2. **Semantisk validering** (utvides): refererte roller finnes; refererte
   verifikatorer finnes og er aktive; `betrodd_for` peker på vilkår som
   faktisk brukes; `menneskelig_overstyring.godkjennbare` peker på
   eksisterende (grunnkode, handling); `belop_maks` har `valuta`;
   tidsvinduer er reelle klokkeslett; ingen duplikater.
3. **Konsistens mot systemet:** `meta.status` er tillatt i miljøet
   (eksisterende `tillatte_statuser`); ingen handling refererer en
   oppdragstype/eiermodul som ikke finnes.

Validering er **ren og deterministisk** — ingen sideeffekt. Feil
returneres som strukturerte koder med sti (`handlinger[3].grenser.valuta`),
oversatt i UI via locale.

## 4. Diff er obligatorisk og bindende

Diffen beregnes server-side mellom **aktiv versjon** og **utkastets
frosne innhold**, JCS-kanonisert på begge sider.
- `diff_hash = SHA-256(JCS(strukturert diff))`.
- **Godkjenneren attesterer `diff_hash`**, ikke versjonsnummeret (prinsipp
  4). Endres utkastet etter at runden åpnet → hash avviker → runden
  kanselleres, ny runde kreves.
- **Konfliktdeteksjon:** ble aktiv versjon endret siden utkastet ble
  laget (`basert_pa_hash` ≠ nåværende aktiv hash) → utkastet kan ikke
  aktiveres. Det må rebaseres (nytt utkast fra ny aktiv versjon) og
  diffes på nytt. **Aldri automatisk fletting.**

## 5. Risikoklassifisering: utvidelse vs. innsnevring

Diffen klassifiseres maskinelt per endring:
| Klasse | Eksempler |
|---|---|
| **UTVIDER** | høyere `belop_maks`, ny handling i `auto`, fjernet vilkår, bredere tidsvindu, ny betrodd verifikator, ny/videre `menneskelig_overstyring`, `alltid_stopp` → `auto` |
| **INNSNEVRER** | lavere grense, nytt vilkår, fjernet handling, smalere vindu, `auto` → `alltid_stopp` |
| **NØYTRAL** | omdøping av beskrivelseskoder, rekkefølge |

- **Enhver UTVIDER-endring krever fire øyne** (uavhengig av policyens
  egne innstillinger — dette er en plattformregel, ikke kundekonfigurasjon).
- Ren INNSNEVRER/NØYTRAL kan aktiveres med én godkjenner.
- Klassifiseringen vises i UI per endring, med UTVIDER tydelig markert.
- Ukjent/uklassifiserbar endring → behandles som **UTVIDER**
  (fail-closed).

## 6. Aktiveringsrunde (speiler PR-012s godkjenningsrunde)

- Åpnes mot et `validert` utkast; binder `diff_hash` og
  `utkast_innholds_hash`.
- Én aktiv runde per utkast (delindeks).
- Fire øyne der §5 krever det: to ulike `bruker_id` med `policy:activate`.
- Ved siste attestasjon revalideres begge brukeres medlemskap, rolle og
  `authz_version`; MAC og utløp kontrolleres for begge under samme lås.
- Utløp (24 t) lukker runden; attestasjoner slettes aldri.

## 7. Aktivering — én transaksjon

`aktiver_policy(...)`, samme mønster som `behandle_unntakshandling`:
1. `sett_kontekst` først.
2. Lås utkast + aktiveringsrunde + `policyer`-radene for (tenant, policy_id).
3. Kontroller: runde `klar`, MAC+utløp for alle attestasjoner,
   `diff_hash` matcher, `basert_pa_hash` = nåværende aktiv hash,
   utkastets `innholds_hash` uendret.
4. Revalider innholdet på nytt (lag 1–3) — en policy som ikke lenger
   validerer kan ikke aktiveres, selv om den gjorde det i går.
5. `UPDATE policyer SET aktiv=false WHERE tenant=? AND policy_id=? AND aktiv`
   + `INSERT` ny rad med `aktiv=true` — delindeksen garanterer
   nøyaktig én.
6. Skriv revisjonslogg (`policy.aktiver`) med versjon, hash, diff_hash,
   godkjennere, risikoklasse.
7. Sett runde `brukt`, utkast `aktivert`.
8. Commit alt eller ingenting.

**Ingen cache å invalidere** — motoren laster policy per request fra DB
(PR-005b-beslutningen). Aktivering trer i kraft for NESTE beslutning;
pågående beslutninger fullfører mot den policyen de lastet.

## 8. Rullbakk

«Rull tilbake til versjon N» er **en ny aktivering** av N (ny rad, ny
revisjonspost, ny runde). Ingen sletting, ingen nedmigrering, ingen
egen mekanisme. Diff beregnes fra nåværende aktiv til N og
risikoklassifiseres som ellers — en rullbakk KAN være en utvidelse, og
skal da kreve fire øyne som alt annet.

## 9. Samspill med pågående arbeid (allerede spesifisert, bekreftes her)

- **PR-012:** aktivering under en åpen godkjenningsrunde → runden
  kanselleres (`policy_endret_under_godkjenning`), begge må godkjenne på
  nytt under ny policy. Normal hendelse, ingen sikkerhetssak.
- **PR-007:** saker med frosset `krav_sett` og `intensjon_policy_hash`
  påvirkes ikke; fase 2 revaliderer mot aktiv policy og kan sende saken
  til retry/manuell.
- **PR-008:** historiske versjoner er referert av revisjonslogg og kan
  aldri slettes — nå håndhevet for alle tidligere aktive versjoner.

## 10. API og scopes

| Endepunkt | Scope |
|---|---|
| `GET /v1/policy/aktiv` | `policy:read` (finnes) |
| `GET /v1/policy/versjoner` + `/{versjon}` | `policy:read` |
| `POST /v1/policy/utkast` · `PATCH /{utkast_id}` · `DELETE` (forkast) | `policy:write` |
| `POST /v1/policy/utkast/{id}/valider` | `policy:write` |
| `GET /v1/policy/utkast/{id}/diff` | `policy:write` |
| `POST /v1/policy/utkast/{id}/aktivering` (åpne runde / attester) | `policy:activate` |

- **`policy:write` og `policy:activate` er ADSKILTE.** Den som skriver
  kan ikke alene aktivere. Ved fire øyne kan forfatteren dessuten ikke
  være en av godkjennerne (`opprettet_av` ∉ godkjennere).
- Browser-mutasjon: sesjon + Origin + CSRF (PR-010/PR-012s carve-out).
- `Idempotency-Key` på alle skriveruter; `utkastversjon` som optimistisk
  lås på `PATCH` (409 ved konflikt, aldri blind retry).

## 11. UI (femte flate i M-1)

Ny flate **Policyadministrasjon**, arver komponentbiblioteket:
utkastliste · redigering (strukturert skjema, ikke rå YAML) ·
valideringsfeil med sti · **diffvisning med risikoklasse per endring** ·
aktiveringsdialog som viser diffen og krever eksplisitt kvittering ·
fire-øyne-status · versjonshistorikk med hvem som aktiverte hva og når.
`tillatte_handlinger[]`-mønsteret fra PR-012 gjelder: serveren bestemmer
hva brukeren kan gjøre.

## 12. Fire samtidighetsspørsmål

| Kontroll | Alle veier inn? | Samtidighet? | Riktig vs velformet? | Lukket format? |
|---|---|---|---|---|
| Aktivering | Kun `aktiver_policy` | Delindeks + lås → nøyaktig én aktiv | Revalidering ved aktivering, ikke bare ved lagring | Statusenum, CHECK |
| Diff-binding | Server beregner, klient sender aldri | `diff_hash` endres → runde kanselleres | Godkjenner attesterer innhold, ikke nummer | JCS-kanonisert |
| Risikoklasse | Server, per endring | N/A (ren funksjon) | Ukjent → UTVIDER (fail-closed) | Lukket klassesett |
| Utkastredigering | `PATCH` m/ `utkastversjon` | To redaktører → 409, aldri tapt skriving | Validering før frysing | Skjema v0.2 |

## 13. Evidenskrav (`policyadmin-v1`, defineres FØR arbeidet)
Utkast → validering → diff → fire øyne → aktivering ende-til-ende ·
UTVIDER-endring kan ikke aktiveres med én godkjenner · forfatter kan ikke
være godkjenner · endret utkast etter åpnet runde → runde kansellert ·
aktiv versjon endret siden utkast → rebasering kreves, ingen fletting ·
nøyaktig én aktiv versjon under 20 samtidige aktiveringsforsøk ·
rullbakk til eldre versjon fungerer og risikoklassifiseres · policy som
ikke lenger validerer kan ikke aktiveres · historisk versjon kan ikke
slettes · åpen godkjenningsrunde i PR-012 kanselleres ved aktivering.

## Spørsmål til ChatGPT

1. **Er «UTVIDER krever alltid fire øyne» som plattformregel riktig**, eller
   bør det være kundekonfigurerbart (med et plattformgulv)? Jeg har valgt
   hardkodet plattformregel fordi en kunde som slår det av, slår av den
   eneste kontrollen som skiller «juster grense» fra «gi agenten mer makt».
2. **Bør v1 ha simulering** — «kjør de siste N beslutningene mot utkastet
   og vis hva som ville endret seg»? Motoren er deterministisk og
   simuleringen er ren lesing, så den er trygg, men den utvider scope.
   Jeg har utsatt den til v2; er det riktig avveining for en funksjon som
   ville fanget utilsiktede konsekvenser før aktivering?
3. **Utkast i egen tabell** (`policyutkast`) fremfor `policyer` med
   `status='utkast'` — riktig grense, eller ser du en fordel ved å holde
   alt i én tabell med statusfelt?
