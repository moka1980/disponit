# M56-AKSEPTFLIPP — IMPLEMENTERINGSKLARSIGNAL (GO, frosset)

**Til Claude Code · Grunnlag: klarsignalutkast v2 (A1–A3) + lesesvar
2026-08-20 mot main `0ebe10f` og prod-basens live tilstand. Alle grener
valgt. Branch: `pr-XXX-m56-akseptflipp` — PR-nummer settes ved branch.
Stående porter SP-1…SP-12.**

> **Migrasjonsnummer: neste ledige mot main**, verifisert ved
> branch-push og på nytt ved hver merge. **SP-10 gjelder:** migrasjonen
> legger refererbare nøkler på bebodde tabeller (`artefakt` har 24
> promoterte rader i prod) — begge kjøringene (tom + seedet) i CI.

---

## 0. Grenene, valgt av lesesvaret

| Spørsmål | Funn | Valgt gren |
|---|---|---|
| Akseptovergang | Finnes ikke i noe register; `sett_modulstatus` krever null bevis (prod-hendelse 35: tom begrunnelse, tom detalj) | **Opprettes etter `policyaktivering`-mønsteret:** immutabel aksepthendelse + herdet funksjon |
| Identitet (A1) | `modulrelease`- og `moduldeployment`-PK-er er permanente (immutable-triggere); **alle fem releasene deler samme digest** — bare identiteten skiller dem | Drill og aksept FK-bindes til **deploymentraden**, digest snapshottes i tillegg |
| E2E-lineage (A2) | Artefaktet bærer release/kontrakt/epoch **kapabilitets-attestert** (samme klasse som `claimet_av`), men uten FK; 23 av 24 prod-artefakter er fra r1, kun 1 fra r5 | FK-tillegg `artefakt → modulrelease` **og** delt releasekolonne i hendelsens artefakt-FK (E1e-formen); negativporten har levende materiale |
| Evidens (A3) | Grensen heter **`wcag-kontroll-v1`**, uregistrert; målingene ligger i en root-eid, muterbar fil utenfor repo; manifestets bindingsmekanisme (krav_id + innsjekket artefakt + sha256 + KRAVGRENSER) står ferdig og ubrukt | **Bindingsmekanismen tas i bruk fullt ut** + én immutabel akseptobservasjon per grensepunkt i DB, pekende på de samme innsjekkede artefaktene |

**Miljøspørsmålet (flagg 1c/4) avgjøres slik:** aksepten binder
deploymentraden **slik den faktisk kjører** — `(m_wcag_audit, 'staging',
wcag-r5)`. Å opprette en 'produksjon'-rad for å akseptere den ville vært
en påstand om en topologi som ikke finnes; det eksisterer ett miljø, og
etiketten er 'staging'. Aksepthendelsen navngir miljøraden eksplisitt,
og **aksept gjelder per deploymentrad**: materialiseres et reelt
produksjonsmiljø senere, krever den raden sin egen aksept med egen
drill. Manifestets `driftstilstand: produksjon` er katalogens sannhet om
*bruk* (i drift for kunden), ikke om miljøetiketten — det skrives i
manifestkommentaren.

**Navnet rettes overalt:** `wcag-kontroll-v1`, ikke `wcag-modul-v1`.

## 1. DDL (neste ledige migrasjon)

```sql
-- Drillen: egen smal, immutabel tabell (lesesvar 2: detalj-jsonb i
-- modulregister_hendelse har ingen skjemahåndheving)
CREATE TABLE moduldrill (
  drill_id BIGINT GENERATED ALWAYS AS IDENTITY,
  modul_id TEXT NOT NULL,
  miljo TEXT NOT NULL,
  fra_release TEXT NOT NULL,
  til_release TEXT NOT NULL,        -- releasen det rulles TILBAKE til
  epoch_snapshot BIGINT NOT NULL,   -- module_epoch da drillen kjørte (fencing-konteksten)
  digest_snapshot TEXT NOT NULL,
  claim_stopp_ok BOOLEAN NOT NULL,      -- (a) draining claimer ikke nye
  rene_utfall_ok BOOLEAN NOT NULL,      -- (b) SP-3 på løpende oppdrag
  tilbake_ok BOOLEAN NOT NULL,          -- (c) fram igjen gjenoppretter plukking
  aktor TEXT NOT NULL,
  utfort_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (modul_id, drill_id),
  -- A1: identitet, ikke bytes — FK mot de permanente radene
  FOREIGN KEY (modul_id, miljo, fra_release) REFERENCES moduldeployment (modul_id, miljo, release_id),
  FOREIGN KEY (modul_id, til_release) REFERENCES modulrelease (modul_id, release_id),
  -- refererbar nøkkel for aksepthendelsen: drill FOR denne deployment
  UNIQUE (modul_id, miljo, fra_release, drill_id));
-- append-only trigger som de andre

-- A2: artefaktets releasesnapshot blir også relasjonelt
ALTER TABLE artefakt ADD CONSTRAINT artefakt_release_fk
  FOREIGN KEY (modul_id, release_id) REFERENCES modulrelease (modul_id, release_id);
-- refererbar nøkkel med tilstand i identiteten (E1f-formen: kvalifikasjonen
-- 'promotert' står I nøkkelen; resultatlåsen gjør den varig)
ALTER TABLE artefakt ADD CONSTRAINT artefakt_refererbar
  UNIQUE (tenant, artefakt_id, modul_id, release_id, tilstand);

-- Aksepthendelsen
CREATE TABLE modulaksept (
  modul_id TEXT NOT NULL,
  miljo TEXT NOT NULL,
  release_id TEXT NOT NULL,
  drill_id BIGINT NOT NULL,
  e2e_tenant TEXT NOT NULL,
  e2e_artefakt_id UUID NOT NULL,    -- form verifiseres mot artefakt-PK
  e2e_tilstand TEXT NOT NULL DEFAULT 'promotert' CHECK (e2e_tilstand = 'promotert'),
  evidens_jsonl_sha256 TEXT NOT NULL,   -- SP-11: den innsjekkede filen
  manifest_commit TEXT NOT NULL,
  ci_run TEXT NOT NULL, ci_commit TEXT NOT NULL,
  aktor TEXT NOT NULL,
  akseptert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (modul_id, miljo, release_id),      -- én aksept per deploymentrad
  FOREIGN KEY (modul_id, miljo, release_id) REFERENCES moduldeployment (modul_id, miljo, release_id),
  -- A1: drillen gjelder NØYAKTIG denne deploymentraden (delte kolonner bærer båndet)
  FOREIGN KEY (modul_id, miljo, release_id, drill_id)
    REFERENCES moduldrill (modul_id, miljo, fra_release, drill_id),
  -- A2: E2E-artefaktet er promotert OG produsert av samme release (delt release_id)
  FOREIGN KEY (e2e_tenant, e2e_artefakt_id, modul_id, release_id, e2e_tilstand)
    REFERENCES artefakt (tenant, artefakt_id, modul_id, release_id, tilstand));
-- append-only trigger; INSERT kun til akseptfunksjonens eier

-- A3: én immutabel observasjon per grensepunkt i wcag-kontroll-v1
CREATE TABLE modulaksept_punkt (
  modul_id TEXT NOT NULL, miljo TEXT NOT NULL, release_id TEXT NOT NULL,
  punkt TEXT NOT NULL,
  grenseverdi TEXT NOT NULL, maalt_verdi TEXT NOT NULL,
  kilde_type TEXT NOT NULL CHECK (kilde_type IN
    ('artefakt','registerhendelse','evidensfil','ci_kjoring')),
  kilde_ref TEXT NOT NULL,          -- artefakt_id/hash · hendelses-id · sha256 · run+sha
  PRIMARY KEY (modul_id, miljo, release_id, punkt),
  FOREIGN KEY (modul_id, miljo, release_id) REFERENCES modulaksept (modul_id, miljo, release_id));
-- append-only; skrives av akseptfunksjonen i samme transaksjon
```

**Akseptfunksjonen** (`aksepter_moduldeployment`, eier modul_eier,
EXECUTE kun `disponit_modules_admin`, SP-1/SP-2 med replay-nøkkel):
verifiserer drillens tre kontrollpunkter = true, skriver hendelse +
alle punktrader i én transaksjon. Manglende eller ufullstendig
punktsett → ingen hendelse. **DB-CHECK-en binder DB-siden** (flagg 1b):
manifest-/katalogflippen speiles av CI-portene, ikke av CHECK.

## 2. Evidensapparatet tas i bruk (A3, hele veien)

1. `wcag-kontroll-v1` **registreres i `manifestskjema.KRAVGRENSER`**
   (presedens: `perf-m01-v1`).
2. `evidens.jsonl` **sjekkes inn** under `deploy/staging/artefakter/`
   med sha256 i manifestet — SP-11-byteporten gjelder fra da av; den
   root-eide serverfilen er ikke lenger kilden.
3. Alle seks sjekklistepunkter får `krav_id` + innsjekket artefakt +
   `artefakt_sha256` + strukturerte `bevismaalinger` — og flippes til
   `ja` **først da**. `rollback_testet` flippes av drillen i denne
   arcen. RUTINER pkt. 6 oppfylles dermed ærlig, ikke omdefinert.
4. `valider_artefakter` re-måler ved hver CI-kjøring; DB-observasjonene
   (§1) refererer de samme innsjekkede, hash-bundne artefaktene — de to
   portene kan aldri peke på ulikt bevis.
5. Invariantpunktene uten historiske rader (`skjema.*`, `egress.*`,
   `malautorisasjon.*`) bevises «grønne da» via `ci_kjoring`-kilden:
   run-ID + commit-sha på akseptcommiten.

## 3. Drillen — kjørbar nå, og manifestnoten rettes

Lesesvarets flagg 3: r4 og r5 er samme bytes; `r5→r4→r5` via
`bytt_release` booter. Drillen kjøres mot staging-raden, måler (a)
claim-stopp for draining-releasen, (b) rene utfall på løpende oppdrag
(SP-3 — aldri falske verdikter), (c) gjenopprettet plukking etter
tilbakebytte, og skriver drillraden med epoch- og digest-snapshot.
Manifestnoten om at drillen ikke kan kjøres **slettes** — den var sann
da den ble skrevet og er det ikke lenger.

At alle releasene deler digest er A1s levende bevis og skal stå i
PR-beskrivelsen: digest kunne ikke skilt drillet fra udrillet release.

## 4. Innhold i samme PR

- Planlinjen inn i M-56-flyten: «Mottar bestilling gjennom
  beslutningsveien, **eller fra en aktiv plan**» — sann siden 048.
- Manifestflippen: `status: under_utvikling → aktiv`,
  `driftstilstand: ikke_i_drift → produksjon`, sammen (manifestets egen
  regel), med miljøkommentaren fra §0.
- Katalogens statusetikett avledes fra manifestet — ingen hardkodet
  tekst (innholdsnotatets varsel innfris).

## 5. Codex-porter

1 Aksept uten drillrad → FK-avvist · 2 Drill mot annen deploymentrad
enn den som aksepteres → FK-avvist (A1; **identisk digest i testen**, så
porten beviser at identiteten bærer) · 3 E2E-artefakt fra annen release
(r1-materialet finnes) → FK-avvist (A2) · 4 E2E-artefakt ikke promotert
→ FK-avvist (tilstand i nøkkelen, E1f-formen) · 5 Aksept uten komplett
punktsett → ingen hendelse (samme transaksjon) · 6 Hendelse/drill/punkt
tåler ingen UPDATE/DELETE · 7 Drill med et kontrollpunkt = false →
aksept avvist · 8 Replay-nøkkel: to kall → én hendelse (SP-2) ·
9 Ordinære roller: INSERT/EXECUTE nektet · 10 `wcag-kontroll-v1`
registrert; sjekklistepunkt `ja` uten krav_id+artefakt → CI-port rød ·
11 evidens.jsonl-sha256 i manifest == innsjekket fil (SP-11) ·
12 Planlinje + manifestflipp + katalogetikett i samme PR (innholdsdiff);
etikett ikke hardkodet (statisk) · 13 SP-10: begge kjøringer; seedet
base bærer promoterte artefakter på to releaser · 14 Ny aksept kreves
per deploymentrad (negativ: hendelse for (staging, r5) autoriserer ikke
(produksjon, r5)).

## 6. Evidensgrense `m56-akseptflipp-v1` (defineres FØR arbeidet)

**Sikkerhetsinvarianter:** `aksept.uten_drill = 0` ·
`aksept.drill_annen_deployment = 0` · `aksept.e2e_annen_release = 0` ·
`aksept.e2e_ikke_promotert = 0` · `aksept.uten_komplett_punktsett = 0` ·
`aksept.hendelse_endret = 0`.

**Øvrige:** `drill.kontrollpunkt_false_akseptert = 0` ·
`aksept.replay_ga_to_hendelser = 0` ·
`kravgrense.uregistrert_ved_flipp = 0` ·
`sjekkliste.ja_uten_kravid = 0` · `evidensfil.sha_avvik = 0` ·
`katalog.hardkodet_status = 0` ·
`ddl.begge_kjoringer_gronne = ja` ·
`manifest.utdatert_drillnote_fjernet = ja`.

Et punkt uten definert, målbar grense regnes som `nei`.

---

```
NÅ:    Implementer akseptflippen mot dette klarsignalet — migrasjon
       (neste ledige), aksepter_moduldeployment, drillen kjørt og
       skrevet, evidensapparatet i bruk, innhold i samme PR
       — Claude Code
       — platform/core/db/migrations/NNN_modulaksept.sql,
         platform/modules/m56_wcag_audit/manifest.yaml,
         deploy/staging/artefakter/, docs/INNHOLD-…, ui/katalog
NESTE: Etter merge: #112 i neste policykonsolidering; #115/#116 når
       eier prioriterer; M-16 når datainventar ønskes
       — Claude Code / Claude.ai
```
