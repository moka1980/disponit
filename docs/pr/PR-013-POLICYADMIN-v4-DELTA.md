# PR-013 SPESIFIKASJON v4 — DELTA (fem implementeringsgrenser → GO)

**Draft: Claude.ai · Fem kontrakter lukket + én produktforutsetning
løftet til Eier (§6).**

## 1. `policy_hode` — låsbar ankerrad, også ved første aktivering

Uten aktiv rad låser `FOR UPDATE` ingenting, og to samtidige
førstegangsaktiveringer kunne begge regnet mot deny-all og allokert samme
versjon. Unikbrudd er feilhåndtering, ikke samtidighetskontroll. Rettet:
```sql
CREATE TABLE policy_hode (
  tenant TEXT NOT NULL, policy_id TEXT NOT NULL,
  neste_versjon INT NOT NULL DEFAULT 1,
  aktiv_versjon INT,                    -- NULL = ingen aktiv (deny-all gjelder)
  revisjon BIGINT NOT NULL DEFAULT 0,   -- monoton, økes ved hver aktivering
  PRIMARY KEY (tenant, policy_id)
);
```
- **ALLE aktiveringer, inkludert den første, låser `policy_hode`
  FØRST** (`SELECT ... FOR UPDATE`), deretter utkast/aktiv rad.
- Hoderaden opprettes ved onboarding (eller `INSERT ... ON CONFLICT DO
  NOTHING` før låsing) — den finnes alltid før en aktivering starter.
- Versjonsallokering: `neste_versjon` leses og økes under låsen.
  Delindeksen `en_aktiv_per_policy` består som andre forsvarslinje.
- Låserekkefølge utvides: `policy_hode → policyer → policyutkast →
  aktiveringsrunde → aktiveringsattestasjon`.

## 2. `DENY_ALL_V1` er EFFEKTIV motorpolicy, ikke bare diffgrunnlag

Ellers kunne administrasjonen vist én baseline mens beslutningsveien
brukte en annen fail-closed-tilstand. Rettet:
- Mangler tenant aktiv policy, **evaluerer motoren mot NØYAKTIG samme
  versjonerte `DENY_ALL_V1`** (delt konstant, samme kodevei — ikke en
  separat «ingen policy»-gren).
- `deny_all_hash` og `deny_all_versjon` bindes i runde, diff,
  attestasjonskonvolutt og revisjonslogg.
- **Endring av deny-all-konstanten ER en motorsemantikkendring** →
  krever ny `motor_semantikkversjon` og ny klassifikatorversjon (§3).

## 3. Semantikkversjonen bindes til innhold, ikke til disiplin

CI-porten fanget bare at nummeret ble endret uten klassifikatorbump —
ikke at semantikken ble endret uten at noen bumpet nummeret. Rettet:
- Et **reviewet semantikkmanifest** lister de filene og regeltabellene som
  bærer policysemantikk (`engine.py`s beslutningsveier, `schema.py`s
  semantiske validering, tidsvindu-/frekvenskoden, `DENY_ALL_V1`).
- `motor_semantikkversjon` bindes til **SHA-256 over manifestets filer**.
- **CI feiler når checksummen endres uten at både
  `motor_semantikkversjon` OG `klassifikatorversjon` er bumpet.**
- Manifestet er selv reviewet: å fjerne en fil fra det er en eksplisitt,
  synlig handling i diffen — ikke noe som skjer stille.

## 4. Nøytralitet bevises strukturelt, ikke med grep

Tekstsøk beviser ikke fravær av dynamiske oppslag. Rettet:
- Policyen får en eksplisitt **`metadata`-seksjon** for felt uten
  semantikk (visningskoder, beskrivelser, notater).
- **`metadata` FJERNES før policyen sendes til motoren** — motoren ser
  aldri feltene, så nøytraliteten er strukturell.
- **Ethvert felt som når motorens semantiske policyobjekt og mangler en
  bevist monotoniregel klassifiseres som UTVIDER.** Ingen wildcard,
  ingen grep-basert sikkerhetsbevis.
- CI-port: felt i `metadata` som likevel forekommer i det objektet
  motoren mottar → rødt.

## 5. DST-atferd fastsatt som motorsemantikk

En vilkårlig kanonisk uke kan ligge utenfor begge klokkeovergangene.
Bindende regler, delt mellom motor og klassifikator:
- **Samme tidssone:** sammenlign mengder av tillatte **lokale ukeminutter**.
- **Ikke-eksisterende lokal tid (vårskifte):** eksplisitt **fail-closed** —
  minuttet regnes som IKKE tillatt i begge sett.
- **Dobbelt lokal tid (høstskifte):** regelen gjelder **begge
  `fold`-verdier**; et vindu som dekker den tvetydige timen dekker begge
  forekomster.
- **Tidssoneendring → alltid UTVIDER.**
- **Parser og DST-regler deles** mellom motor og klassifikator (ett
  bibliotek, én versjon), men **klassifikatoren kaller ALDRI motorens
  beslutningsfunksjon rekursivt** — kun den delte tidsmengde-funksjonen.

## 6. ⚠️ Produktforutsetning til Eier: tre brukere før første policy

Med v2 §4 (forfatter ≠ godkjenner) og v3 §5 (første policy = UTVIDER =
fire øyne) følger: **en ny tenant trenger minst tre autoriserte brukere
før første policy kan aktiveres** — én forfatter og to godkjennere.

Dette er sikkert og prinsipielt riktig: den første policyen er den største
fullmaktsutvidelsen som skjer for en kunde. Men det er et **eksplisitt
onboardingkrav**, ikke en skjult sperre, og det må stå i
onboardingdokumentasjonen. **Ingen enbruker-bypass finnes eller skal
bygges.**

Alternativet — en signert plattform-onboardingprosedyre der Disponit selv
utgjør den ene godkjenneren — er mulig, men er en egen tillitsgrense og
bør i så fall spesifiseres separat, ikke smugles inn her.
**Eiers avgjørelse: aksepterer vi tre-bruker-kravet for onboarding i v1?**

## Tester (tillegg)
To samtidige førstegangsaktiveringer → én vinner via `policy_hode`-lås,
ingen duplikat versjon, unikbrudd inntreffer ALDRI som normalvei · tenant
uten aktiv policy → motoren evaluerer mot `DENY_ALL_V1` (samme hash som
diffgrunnlaget) · semantikkmanifest-checksum endret uten dobbeltbump →
CI rødt · `metadata`-felt som når motorobjektet → CI rødt · ukjent felt i
motorobjektet → UTVIDER · vårskifte-minutt regnes som ikke tillatt i
begge sett · høstskifte dekker begge `fold` · klassifikator og motor gir
identisk tidsmengde for samme vindu.
