# ARKITEKTDOM — #168 identitetsvalget + de tre v1-rapportene

**Draft: Claude.ai · Full sti: `docs/pr/BESLUTNING-168-rapportidentitet.md`.
Grunnlag: PR #270 (v2-utkast, A1–A4 innarbeidet) og #168-tråden slik den
er referert. Bindende for registrerings-PR-en.**

---

## 1. Dom: **registreringstuppelen**, ikke navneformen

Trådens punkt 3 er ikke et hinder for tuppelen — det er **beviset for
den**. At `…rapport.v2` avvises som overlapp betyr at navneregisteret
selv sier at navneformen ikke kan bære en versjon. Da er det to veier:
finne på et navn som ikke overlapper, eller la versjonen være det den
er.

Å finne et sidestilt navn (`…beslutningsspor` e.l.) ville skjult
slektskapet: en leser kunne ikke se relasjonelt at v2 avløser v1, og
«hvilken versjon gjelder nå» ville vært en konvensjon i en streng. Det er
SP-12 og SP-§3 i samme sving — et navn som *påstår* identitet og
rekkefølge, håndhevet av en prefiksregel.

**Formen:**

```sql
-- Identiteten er tuppelen, ikke strengen
CREATE TABLE artefakttype_versjon (
  artefakttype TEXT NOT NULL,          -- uendret navn, ingen .v2-suffiks
  skjemaversjon INT NOT NULL,
  forrige_versjon INT,                 -- lineage, NULL for første
  status TEXT NOT NULL CHECK (status IN ('gjeldende','avviklet')),
  registrert_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (artefakttype, skjemaversjon),
  UNIQUE (artefakttype, skjemaversjon, status),      -- refererbar
  FOREIGN KEY (artefakttype, forrige_versjon)
    REFERENCES artefakttype_versjon (artefakttype, skjemaversjon));
CREATE UNIQUE INDEX en_gjeldende_per_type
  ON artefakttype_versjon (artefakttype) WHERE status = 'gjeldende';
-- immutabel bortsett fra gjeldende → avviklet (én vei, trigger)
```
- **Navnet er uendret**, prefikslukkingen står urørt og trenger ingen
  dispensasjon. Lese-API-ets par trengs ikke for et nytt navn, fordi det
  ikke kommer et nytt navn.
- **Artefaktraden bærer `skjemaversjon` med FK** mot tuppelen — det er
  A4 gjort relasjonelt: en v1-rapport *kan ikke* leses som v2, fordi
  raden peker på sin egen registrerte versjon.
- **`en_gjeldende_per_type`** gjør «hvilken versjon promoteres nå» til en
  lagringstilstand, ikke en avtale. Det løser trådens punkt 4 mekanisk
  (§3).
- Lineær versjonskjede med `forrige_versjon` — samme form som
  `utsendingsliste`-serien, av samme grunn.

Immutabiliteten er den ene retningen `gjeldende → avviklet`; en avviklet
versjon kan aldri bli gjeldende igjen. Ellers ville «v2 er innført» vært
reverserbart uten spor.

## 2. De tre v1-rapportene: **ikke option B, og ikke manuell makulering**

Eiers A-dom løser problemet framover, og det er riktig. Men de tre står
igjen med payload som overlever kundens frist, og «terminale, reapes
ikke» er ikke et unntak fra sletteplikten.

**Mekanismen finnes allerede og er ratifisert:** M-57-klarsignalets §5
slår fast at **payload-tømming ikke er en tilstandsendring**. Raden
består med hash, `slettet_ts` og statuser; terminal tilstand endres
aldri. De tre v1-rapportene hører derfor inn i kandidatdatagrensen og
tømmes ved frist — samme reaper, samme port, ingen ny mekanisme og ingen
manuell makulering.

Det er billigere enn option B og mer etterrettelig enn å slette manuelt:
etter tømmingen kan revisjonen fortsatt bevise at rapportene fantes og
hva de hashet til.

**Egen liten PR** — den skal ikke ri med registreringen, og den skal ha
en dato. Uten dato blir den den posten som er nesten ferdig i seks
måneder.

## 3. Trådens punkt 4 gjøres mekanisk

«Ingen ny v1-rapport bør promoteres i mellomtiden» er en intensjon.
Med `en_gjeldende_per_type` blir den en regel: registrerings-PR-en setter
v2 `gjeldende` og v1 `avviklet` i samme transaksjon, og
promoteringsveien avviser artefakter mot en avviklet versjon.

Til registrerings-PR-en er merget står v1 fortsatt `gjeldende` — det er
riktig, for alternativet er en flate som ikke kan promotere noe. Vinduet
er da nøyaktig så langt som PR-en tar, og det er synlig.

## 4. A1-restens plassering: godtatt, med presisering

At reap-siden av A1 flyttes til registrerings-PR-en er riktig — v2 blir
først et artefakt der. Presiseringen: **porten skal ikke bare vise null
treff etter reaping, men at rapporten fortsatt finnes.** En rapport som
forsvant ville også gitt null treff. Begge halvdelene i samme test.

## 5. Porter til registrerings-PR-en

1 Artefakt med `skjemaversjon` som ikke finnes i tuppelen → FK-avvist ·
2 v1-artefakt lest som v2 → umulig (versjonen følger raden) ·
3 To `gjeldende` for samme type → unikavvist · 4 `avviklet → gjeldende`
→ trigger-avvist · 5 Promotering mot avviklet versjon → avvist ·
6 `forrige_versjon` som ikke finnes eller peker på annen type →
FK-avvist · 7 **A1 fullt:** fixture i kjøringens dokumenter → rapporten
består etter reaping **og** har null treff · 8 Navneregisterets
prefikslukking uendret (regresjon — ingen dispensasjon innført).

**Evidensgrense (tillegg):** `artefakt.versjon_uten_registrering = 0` ·
`type.to_gjeldende_versjoner = 0` · `versjon.avviklet_reaktivert = 0` ·
`promotering.mot_avviklet_versjon = 0` ·
`rapport.borte_etter_reaping = 0` · `rapport.treff_etter_reaping = 0` ·
`v1rapporter.utenfor_kandidatgrensen = 0`.

---

```
NÅ:    Registrerings-PR-en mot denne dommen (tuppel, ikke navneform);
       A1-restens begge halvdeler i samme test — Claude Code
NESTE: Egen liten PR med dato: de tre v1-rapportene inn i
       kandidatdatagrensen og payload-tømt ved frist; deretter
       leseflate-flyttingen (#183) — Claude Code / Eier
```
