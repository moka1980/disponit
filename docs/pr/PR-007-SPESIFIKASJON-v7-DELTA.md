# PR-007 SPESIFIKASJON v7 — DELTA (fire ferskhets-/avslutningsfunn → GO)

**Draft: Claude.ai · Modell (b) + form A + v1–v6 står. Reviewens
anbefalte løsninger vedtatt direkte. Fire punkter, alle om det
verifikatoren ikke kan love over tid.**

## 1. Ferskhet er ikke `utloper` alene — motoren er den autoritative porten

v6 pkt. 5 sjekket `now() < utloper` i fase 2. Men en attestasjon kan bli
UGYLDIG før den utløper (underliggende faktum endret: leverandør fjernet
fra register, konto avregistrert). `utloper` er verifikatorens
løfte-om-ferskhet, ikke en garanti. Rettelse:

- Fase 2-revalideringen (v6 pkt. 5) beholdes som BILLIG forhåndssjekk
  (utløp + sett-hash), men er IKKE den autoritative avgjørelsen.
- **Motoren er autoritativ.** Fase 2 bygger hendelsen med hele settet og
  kjører ordinær beslutning; motorens vilkårskontroll (attestasjon gyldig,
  ikke utløpt, ressursbundet) er siste ord. Dette er allerede fail-closed
  i motoren — v7 gjør det til den bevisste primærkontrollen, ikke en
  bakstopp «mot formodning» (v6-formuleringen nedgraderes).
- Kortere ferskhetskrav for høyrisiko-vilkår: verifikatoren setter
  `utloper` selv per vilkårstype (bank/konto kort, register lengre).
  Policyen kan sette et TAK på attestasjonslevetid per handling
  (`maks_attestasjon_alder_s`, valgfritt felt) — ingest avviser
  attestasjon eldre enn taket uansett `utloper`. Fravær av felt = kun
  `utloper` gjelder.

## 2. Ikke-innhentbar under fase 1: manuell, men budsjett-korrekt

v6 pkt. 4: ett `ikke_attesterbar` → `manuell` direkte. Skjerpes mot
misbruk: en verifikator som (feilaktig eller ondsinnet) melder
`ikke_attesterbar` skal ikke kunne omgå retry-budsjettet ELLER låse en
sak som EGENTLIG er forbigående utilgjengelig.

- `ikke_attesterbar` med eksplisitt `permanent: true` (verifikator
  bekrefter prinsipiell u-innhentbarhet, f.eks. «ressurs finnes ikke») →
  `manuell` direkte, historikk `vilkaar_permanent_uinnhentbar`.
- `ikke_attesterbar` uten `permanent` (kilde forbigående nede) → behandles
  som `negativ`: retry-klar/manuell PER BUDSJETT (v4/v5). Bruker budsjett,
  konvergerer eller gir opp kontrollert.
- Skillet er verifikatorens signerte påstand, logget og etterprøvbart —
  ikke M-37s gjetning.

## 3. Avslutning er monoton — «komplett» fastslås én gang, atomisk

v6 gjorde bevisinnsamling atomisk. v7 sikrer at OVERGANGEN til
`verifikasjon_klar` (fase-1-komplett) er like monoton som terminal
generasjonsstatus (v4 pkt. 2):

- `aktiv → positiv` skjer i ÉN transaksjon med sett-komplett-sjekken;
  etter `positiv` er settet frosset komplett og revurderes aldri.
- En sen kvittering som ankommer etter `positiv` → append-only
  konfliktevidens (v4 pkt. 2-mekanismen), endrer ALDRI det komplette
  settet eller generasjonens terminalstatus.
- Fase 2s revalidering (pkt. 1) kan sende saken til `retry_klar` (nytt
  fullt sett-løp, ny generasjon) — men den MUTERER aldri den `positiv`
  generasjonens bevis. Ny generasjon = friskt sett fra bunnen, aldri
  patching av et gammelt.

## 4. Verifikator-autoritet er vilkårsbundet, ikke sett-bundet

Én kvittering bærer hele settet (v5), men ulike vilkår kan kreve ULIKE
verifikatorer (policyens `verifikatorer.betrodd_for` binder verifikator
til vilkår — finnes fra PR-002). Én modul som signerer hele settet må
være betrodd for HVERT vilkår i det. Rettelse:

- **Per-attestasjon-verifikasjon:** ingest sjekker for HVERT vilkår i
  kvitteringen at den signerende verifikatoren er i policyens
  `betrodd_for` for NØYAKTIG det vilkåret. Én utrodd verifikator for ett
  vilkår → hele kvitteringen avvist (sikkerhet), ingen bevis.
- Settet kan derfor kreve FLERE verifikatormoduler. Da bærer ikke én
  kvittering hele settet — hver verifikator leverer sin del.
  `registrer_verifikasjonsbevis` akkumulerer da over flere kvitteringer,
  men FORTSATT atomisk mot `positiv`: generasjonen blir `positiv` kun når
  bevis for HELE `krav_sett` foreligger, hver signert av rett verifikator.
  Delvise bevis holdes i en `aktiv` generasjon som `mottatt_delsett`
  (metadata), aldri som saks-fremgang — saken er fortsatt
  `venter_verifikasjon`, ingen fase 2 før komplett.

Dette gjenåpner delvis akkumulering som v6 pkt. 2 stengte — men KONTROLLERT
og KUN når settet krever flere verifikatorer:
- Hver del-kvittering er atomisk og sett-hash-bundet.
- Delbevis er append-only, aldri overskrivbare.
- Timeout på `aktiv` generasjon med ufullstendig sett → `negativ` →
  retry/manuell (intet delsett «henger» evig).
- Idempotens per (generasjon, vilkår, verifikator); dobbel del-kvittering
  for samme vilkår → idempotent eller konflikt (ulik attestasjon).
- Fase-1-komplett-sjekken (pkt. 3) er fortsatt den ENE monotone
  overgangen: `positiv` settes atomisk når siste manglende delbevis lander.

## Bindende tester (i tillegg til v4/v5/v6)

Attestasjon gyldig men underliggende faktum endret → motoren avviser i
fase 2 (autoritativ) · attestasjon eldre enn `maks_attestasjon_alder_s`
→ ingest avviser tross gyldig `utloper` · `ikke_attesterbar permanent`
→ manuell uten budsjettbruk; uten permanent → retry teller budsjett ·
sen kvittering etter positiv → konfliktevidens, komplett sett uendret ·
sett med to vilkår som krever to ulike verifikatorer → begge del-
kvitteringer kreves for positiv; én utrodd verifikator for sitt vilkår →
hele avvist · delsett henger aldri: timeout → negativ → retry/manuell ·
dobbel delkvittering samme vilkår → idempotent/konflikt.
