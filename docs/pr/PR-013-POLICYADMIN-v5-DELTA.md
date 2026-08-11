# PR-013 SPESIFIKASJON v5 — DELTA (produktbeslutning + to kontrakter → GO)

**Draft: Claude.ai · Eiers produktbeslutning innarbeidet (§3), to
tekniske kontrakter lukket. v2 §4 ERSTATTES av §3 her.**

## 1. Én autoritativ aktiv-peker

`policy_hode.aktiv_versjon` og `policyer.aktiv` kunne avvike — to
autoriteter for samme sannhet. Rettet, reviewens anbefaling:
- **`policy_hode.aktiv_versjon` ER autoriteten** som motoren leser.
  `NULL` betyr ingen aktiv policy → `DENY_ALL_V1` (v4 §2).
- **Kompositt-FK binder pekeren:**
  `FOREIGN KEY (tenant, policy_id, aktiv_versjon)
   REFERENCES policyer (tenant, policy_id, versjon)` (DEFERRABLE INITIALLY
  DEFERRED, så aktivering kan sette inn rad og peker i samme transaksjon).
- **`policyer.aktiv` beholdes som avledet felt**, håndhevet av deferrable
  constraint-trigger: `aktiv=true` ⇔ `versjon = policy_hode.aktiv_versjon`
  for samme (tenant, policy_id). Delindeksen `en_aktiv_per_policy` består
  som andre forsvarslinje.
- Aktivering oppdaterer ny versjonsrad OG pekeren i samme transaksjon,
  under `policy_hode`-låsen (v4 §1).
- **Negativ test fremtvinger avvik** (sett `aktiv=true` på feil versjon,
  eller flytt pekeren uten å oppdatere raden) → DB avviser.

## 2. Semantikkchecksummen dekker mer enn filinnhold

Ellers kunne en fil fjernes fra manifestet, eller en avhengighet endres,
uten nødvendig semantikkbump. Checksummen beregnes over:
1. **Den kanoniske manifestlisten selv**, inkludert filstier (så fjerning
   av en oppføring endrer summen).
2. **Innholdet i alle listede filer og regeltabeller.**
3. **Låste bibliotekversjoner** som påvirker semantikk (lockfil-hasher for
   de relevante pakkene).
4. **tzdata-/timezone-versjonen** som styrer DST-semantikken (v4 §5).
5. **`DENY_ALL_V1`-konstanten.**

CI feiler når denne summen endres uten at BÅDE `motor_semantikkversjon`
OG `klassifikatorversjon` er bumpet.

## 3. Godkjennerregler (ERSTATTER v2 §4) — Eiers produktbeslutning

| Risikoklasse | Krav |
|---|---|
| **UTVIDER** | **To ulike godkjennere.** Forfatteren KAN være den ene, men aldri begge — minst én uavhengig person må godkjenne |
| **INNSNEVRER / NØYTRAL** | **Én godkjenner, forskjellig fra forfatteren** |
| **Første policy** (UTVIDER mot deny-all) | Forfatter + én uavhengig godkjenner → **minimum to brukere** |

- **Ingen enbruker-bypass finnes eller skal bygges.** Et
  enkeltpersonforetak forblir på `DENY_ALL_V1` til en separat, administrert
  onboardingprotokoll eventuelt spesifiseres. **Disponit er ikke skjult
  medgodkjenner i v1.**
- **Runden og attestasjonene binder hvilke konkrete brukere som oppfyller
  kravet** (`pakrevd_antall_godkjennere` + identiteter), og
  sluttkontrollen ved aktivering revaliderer **begge** (medlemskap,
  `policy:activate`, rolle, `authz_version`) — v2 §2/§5 uendret.
- Attestasjonskonvolutten (v2 §3) får `er_forfatter: bool`, slik at
  «forfatteren kan være én av to, aldri begge» er maskinelt håndhevbar og
  etterprøvbar i revisjonen.

## Tester (tillegg)
`aktiv=true` på versjon ≠ pekeren → DB avviser · peker flyttet uten
radoppdatering → DB avviser · motoren leser pekeren, ikke `aktiv` ·
`aktiv_versjon = NULL` → `DENY_ALL_V1` evalueres · fil fjernet fra
manifest → CI rødt · tzdata-versjon endret → CI rødt · lockfil-hash for
semantisk avhengighet endret → CI rødt · UTVIDER med forfatter som BEGGE
godkjennere → avvist · UTVIDER med forfatter + én uavhengig → godkjent ·
INNSNEVRER med kun forfatter → avvist · første policy med to brukere →
aktivert · `er_forfatter` bundet i konvolutten og synlig i revisjonsloggen.
