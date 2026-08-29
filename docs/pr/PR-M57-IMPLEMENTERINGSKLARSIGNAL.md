# M-57 REKRUTTERINGSAGENT (ATS) — IMPLEMENTERINGSKLARSIGNAL (GO, frosset)

**Til Claude Code · Konsolidert: spesifikasjon + v2-delta + v3-delta +
outbox-lesesvar (main `bf3f714`). Alle grener valgt, alle grenser
tallfestet. Branch: `pr-XXX-m57-ats` — PR-nummer settes ved branch.
Migrasjon: **neste ledige mot main**, verifisert ved branch-push og ved
hver merge. Stående porter SP-1…SP-13; K1–K5 i rundene.
**Implementeres etter aksept-arcens avslutning og #132.**

> **SP-10 gjelder:** migrasjonen bytter `oppdrag_opprinnelse_komplett`
> på en bebodd tabell — seedet prøvekjøring i tillegg til tom base.

---

## 0. Stående krav
WCAG 2.1 AA fra første commit; axe-port i samme PR (§8). Evidensgrense
`m57-v1` registreres i KRAVGRENSER FØR bygging; UMAALTE-regelen står.

## 1. Det bærende

Evalueringen er **rådgivende** (artefakter, ingenting utad).
Utsendelsen er **irreversibel** og skjer kun fordi et menneske signerte.
Én oppdragstype for evalueringen (`rekruttering.evaluering`); utsendelsen
er ikke et nytt modulnoppdrag, men en signaturbundet frigivelse — 053s
entydighetsport står urørt.

## 2. Opprinnelsesvalget: **(b) — tredje opprinnelse `frigivelse`**

Lesesvarets svar 3.3 legger valget hit, og (b) velges.

**Begrunnelse:** 038s doktrine er at hver opphavsvei har sin egen herdede
funksjon, og at CHECK-en dekker kombinasjonene uttømmende. Alternativ (a)
ville bundet påkrevdheten til et *par* (opprinnelse, oppdragstype) — en
svakere og mer indirekte form, der en fremtidig oppdragstype på
beslutningsveien kunne havne i feil arm ved uoppmerksomhet. (b) er den
formen 038 selv valgte da beslutningsveien kom til, og den gjør
hovedporten renest: funksjonen krever frigivelsen, **og** CHECK-en krever
den ved direkte DML.

```sql
-- Ny opprinnelse + generisk referansekolonne
ALTER TABLE oppdrag ADD COLUMN frigivelse_id UUID;   -- «frigitt av», generisk

ALTER TABLE oppdrag
  ADD CONSTRAINT oppdrag_frigivelse_fk
  FOREIGN KEY (tenant, frigivelse_id)
  REFERENCES utsendingsfrigivelse (tenant, frigivelse_id);

-- Constraint-swap i SAMME migrasjon: totalformen må nevne den nye kolonnen
ALTER TABLE oppdrag DROP CONSTRAINT oppdrag_opprinnelse_komplett;
ALTER TABLE oppdrag ADD CONSTRAINT oppdrag_opprinnelse_komplett CHECK (
     (opprinnelse = 'm37_reparasjon'
        AND unntak_id IS NOT NULL AND loggpost_id IS NOT NULL
        AND repair_operation_id IS NOT NULL
        AND beslutning_loggpost_id IS NULL AND frigivelse_id IS NULL)
  OR (opprinnelse = 'beslutning'
        AND beslutning_loggpost_id IS NOT NULL
        AND unntak_id IS NULL AND loggpost_id IS NULL
        AND repair_operation_id IS NULL AND frigivelse_id IS NULL)
  OR (opprinnelse = 'frigivelse'
        AND frigivelse_id IS NOT NULL
        AND unntak_id IS NULL AND loggpost_id IS NULL
        AND repair_operation_id IS NULL AND beslutning_loggpost_id IS NULL));
```
Hver arm tar eksplisitt stilling til hvert felt (SP-5-total, som før).
`oppdrag_kolonnelaas` gjør `frigivelse_id` immutabel etter INSERT gratis
— verifiseres, ikke antas (port).

**`opprett_frigivelsesoppdrag(...)`** er eneste vei til
`opprinnelse='frigivelse'`: SECURITY DEFINER, `krev_tenantkontekst`
først, setter opprinnelse og `frigivelse_id` selv (aldri fra request),
og krever at frigivelsesraden finnes. EXECUTE kun til utsendingsrollen.

## 3. Signaturkjeden (v2 + v3, konsolidert)

**Listen er en versjon.** Immutabel rad, redigering gir ny `liste_id`:

```sql
CREATE TABLE utsendingsliste (
  tenant TEXT NOT NULL, liste_id UUID NOT NULL,
  utkast_serie UUID NOT NULL, forrige_liste_id UUID,
  oppdrag_id BIGINT NOT NULL,                    -- evalueringsoppdraget (BIGINT, lesesvar 1)
  listetype TEXT NOT NULL CHECK (listetype IN ('invitasjon','avslag')),
  malversjon TEXT NOT NULL, innhold_hash TEXT NOT NULL,
  antall INT NOT NULL CHECK (antall > 0 AND antall <= 5000),
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, liste_id),
  UNIQUE (tenant, liste_id, innhold_hash),
  UNIQUE (tenant, utkast_serie, liste_id),                    -- serie-refererbar
  UNIQUE (tenant, utkast_serie, innhold_hash),
  FOREIGN KEY (tenant, utkast_serie, forrige_liste_id)
    REFERENCES utsendingsliste (tenant, utkast_serie, liste_id),
  FOREIGN KEY (tenant, oppdrag_id) REFERENCES oppdrag (tenant, id));
CREATE UNIQUE INDEX ett_barn_per_versjon ON utsendingsliste
  (tenant, utkast_serie, forrige_liste_id) WHERE forrige_liste_id IS NOT NULL;
CREATE UNIQUE INDEX en_rot_per_serie ON utsendingsliste
  (tenant, utkast_serie) WHERE forrige_liste_id IS NULL;
-- append-only trigger (hele raden)

CREATE TABLE utsendingssignatur (
  tenant TEXT NOT NULL, liste_id UUID NOT NULL,
  utkast_serie UUID NOT NULL, innhold_hash TEXT NOT NULL,
  signatar TEXT NOT NULL,                         -- FK mot brukeridentitet
  signert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  operasjonsnokkel TEXT NOT NULL,                 -- SP-2
  PRIMARY KEY (tenant, liste_id),
  UNIQUE (tenant, liste_id, innhold_hash, utkast_serie),      -- refererbar
  FOREIGN KEY (tenant, utkast_serie, liste_id)
    REFERENCES utsendingsliste (tenant, utkast_serie, liste_id),
  FOREIGN KEY (tenant, liste_id, innhold_hash)
    REFERENCES utsendingsliste (tenant, liste_id, innhold_hash));
CREATE UNIQUE INDEX en_signert_versjon_per_serie
  ON utsendingssignatur (tenant, utkast_serie);
-- append-only; INSERT kun til signaturfunksjonens eier

CREATE TABLE utsendingsfrigivelse (
  tenant TEXT NOT NULL, frigivelse_id UUID NOT NULL,
  liste_id UUID NOT NULL, innhold_hash TEXT NOT NULL, utkast_serie UUID NOT NULL,
  mottaker_ref TEXT NOT NULL, frigitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, frigivelse_id),
  UNIQUE (tenant, frigivelse_id),                             -- for oppdrag-FK
  UNIQUE (tenant, liste_id, mottaker_ref),
  FOREIGN KEY (tenant, liste_id, innhold_hash, utkast_serie)
    REFERENCES utsendingssignatur (tenant, liste_id, innhold_hash, utkast_serie));
-- append-only; INSERT kun til utsendingsfunksjonens eier
```

**Kjeden, hvert ledd en navngitt constraint:**
oppdrag → frigivelse → signatur → listeversjon → innhold.
Lineage er lineær og serie-bundet: én rot, høyst ett barn per ledd,
høyst én signert versjon per serie. **Ingen gyldig signatur ⇒ ingen
representerbar ATS-utsendelse**, bevisbart med direkte DML.

## 4. Tallfestede grenser

| Grense | Verdi | Begrunnelse |
|---|---|---|
| Søknader per bestilling | **5000** hard; over → avvist ved validering | Katalogens løfte; aldri stille avkorting |
| Slettefrist (søkerdata) | kundevalgt **30–365 døgn**, standard **90**; løper fra prosessen lukkes | Streng standard, spenn for legitime behov |
| Utførelsesfrist | nytt valg **240 min** i `UTFORELSESFRIST_VALG`; lease dekker fristen (037) | Tallet reverifiseres mot målt prøvekjøring før merge; avvik → klarsignalet oppdateres, ikke porten svekkes |
| Arkiv: utpakket totalstørrelse | **2 GB** | Zip-bombe |
| Arkiv: komprimeringsforhold | **maks 100:1** per fil | Zip-bombe |
| Arkiv: filantall | **maks 20 000** | 5000 søknader × noen vedlegg |
| Arkiv: nøstede arkiver | **0** (ikke tillatt) | Angrepsflate |
| Enkeltfil | **maks 25 MB** | Praktisk CV-grense |

## 5. Kandidatdatagrensen (TTL)

Ved utløp slettes payload i **alle seks lagre**: originaldokument,
parset mellomtekst, evalueringsartefakt (funn, sitater, rangering,
begrunnelser), intervjuspørsmål, utsendingsdata (`mottaker_ref`,
flettefeltverdier), av-maskeringstabellen. Består: rad-ID, tidsstempler,
`slettet_ts`, innholdshash, antall, statuser. **Spesifikasjonen påstår
ikke at hashen er anonym** — den sier at payload er slettet og minimal
revisjonsevidens består. Modulen kan ikke forlenge frist; ingen hold i
v1.

## 6. Evaluering, blinding, innhold

Blinding før modellsteget, målt på faktisk input; avskruing auditert.
Rangering med synlige vekter, aldri prosent som målt egenskap.
Risikofunn krever kildereferanse i søknadsteksten — skjemaavvist ellers;
lukket kategorisett uten karaktertrekk-kategorier. Modellen i
container-image (digest = modellversjon); biasmåling bundet til digesten
er akseptkrav. Maler: plattformeid struktur og flettefelt, kundeeid
tone/firmatekst; **ingen vei fra modellutdata til utsendingstekst**
(statisk port). E-post via plattformens signerte utsendingsvei;
**ingen M-6/M-8-avhengighet** — katalogens integrasjonsfelt oppdateres i
denne PR-en. Invitasjon bærer lenke til tidsvalg.

## 7. Skala og feil

Porsjonsvis parsing med fremdrift som evidens. Avbrutt kjøring →
**ingen promotert liste**, rent feilutfall (SP-3), gjenopptas som ny
bestilling. Container credential-fri og nettverksløs under parsing
(port 24-formen).

## 8. Flaten (WCAG)

Kandidatliste i `<table>` med `<caption>`, `<th scope>`, `aria-sort`.
Trafikklys aldri kun farge — kategori som tekst. Vekter som
`<input type="range">` med `<label>`, synlig verdi, ny rekkefølge
annonsert i `aria-live="polite"`. Blindingsbryter med `alertdialog` ved
avskruing (valget auditeres). Sidepanel som dialog med fokusfangst.
Signaturdialog: antall, listetype, hashens kortform, «Dette sender N
e-poster. Kan ikke angres.» Utfall i `role="alert"`. All tekst via
`locales/nb.json`; **axe i samme PR**; tastaturgjennomgang dokumentert.

## 9. Codex-porter

**Opprinnelse og outbox (1–7).** 1 ATS-oppdrag uten `frigivelse_id` →
CHECK-avvist (direkte DML) · 2 `frigivelse_id` satt på annen opprinnelse
→ CHECK-avvist · 3 `frigivelse_id` endret etter INSERT → kolonnelåsen
avviser (verifisert, ikke antatt) · 4 `opprett_frigivelsesoppdrag` er
eneste vei til `opprinnelse='frigivelse'` (statisk + GRANT) · 5
Reparasjons- og beslutningsveien uendret (regresjon på begge armer) ·
6 Frigivelse uten signatur → FK-avvist · 7 **Ingen gyldig signatur ⇒
ingen ATS-utsendelse**, målt med direkte DML uten funksjonen.

**Lineage (8–12).** 8 UPDATE på listeversjon → trigger-avvist ·
9 Forelder i annen serie → FK-avvist · 10 To barn av samme forelder →
unikavvist (samtidig: én vinner, taperen får konflikt) · 11 To røtter i
samme serie → unikavvist · 12 To signerte versjoner i samme serie →
unikavvist; signatur med `utkast_serie` ≠ listens → FK-avvist.

**Innhold og modell (13–17).** 13 Ingen vei modellutdata → utsendingstekst
(statisk) · 14 Flettefelt utenfor malen → avvist · 15 Funn uten
kildereferanse → skjemaavvist · 16 Evalueringsinput fri for maskerte
felt (målt på input); avskrudd blinding uten auditrad → avvist ·
17 Imagebytte uten ny biasmåling → aksept blokkert.

**TTL (18–20).** 18 Fixture-streng i alle seks lagre → null treff på
tvers etter reaping · 19 Reaping av ett lager alene → rødt · 20 Modulen
kan ikke forlenge frist (statisk).

**Arkiv (21–26).** Zip-bombe (forhold og totalstørrelse), path
traversal, symlenke, nøstet arkiv, feil innholdstype, filantall — hver
sin test; container uten credentials og uten nett.

**Skala og flate (27–32).** 27 5001 → avvist ved validering · 28 Avbrutt
kjøring → ingen promotert liste · 29 axe null `alvorligeBrudd` ·
30 Trafikklys og vektendring uten farge/mus · 31 Signaturdialogens tekst
og hashkortform vist · 32 Ingen hardkodet visningstekst;
tastaturgjennomgang dokumentert.

**SP-10 (33).** Begge kjøringer grønne; seedet base bærer oppdrag med
begge eksisterende opprinnelser gjennom constraint-swappen.

**Alle tester konstruerer egen tilstand.** Ingen delt fixture.

## 10. Evidensgrense `m57-v1`

**Sikkerhetsinvarianter:** `utsending.uten_signaturkjede = 0` ·
`liste.signert_versjon_endret = 0` · `serie.to_signerte_versjoner = 0` ·
`liste.forelder_i_annen_serie = 0` ·
`ttl.persondata_funnet_etter_reaping = 0` ·
`blinding.maskert_felt_i_modellinput = 0` ·
`utsending.modellgenerert_fritekst = 0` ·
`arkiv.utpakking_utenfor_grense = 0`.

**Øvrige:** `oppdrag.frigivelse_id_endret = 0` ·
`oppdrag.frigivelse_pa_annen_opprinnelse = 0` ·
`serie.forgrenet_historikk = 0` · `serie.uten_entydig_rot = 0` ·
`funn.uten_kildereferanse = 0` · `blinding.avskrudd_uten_auditrad = 0` ·
`bias.maling_mangler_for_digest = 0` ·
`ttl.lager_utenfor_kandidatgrensen = 0` ·
`bestilling.over_5000_akseptert = 0` ·
`kjoring.delvis_resultat_promotert = 0` ·
`ui.axe_alvorlige_brudd = 0` · `ui.tastaturgjennomgang_dokumentert = ja` ·
`ddl.begge_kjoringer_gronne = ja`.

Et punkt uten definert, målbar grense regnes som `nei`.

---

```
NÅ:    (Etter aksept-arcen og #132) Implementer M-57 mot dette
       klarsignalet — Claude Code
       — platform/core/db/migrations/NNN_m57_utsending.sql,
         platform/modules/m57_ats/, ui/rekruttering/, locales/nb.json
NESTE: M-57-aksept (ordinær deployment-aksept, 049–053) når modulen
       kjører; #112 / #115 / #116 / #127 etter eiers prioritering
       — Claude.ai / Claude Code
```
