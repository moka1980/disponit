# Arbeidsflyt og kvalitetsporter

Denne pakken er startpunktet for M-1 (Policy- og fullmaktsmotor) bygget på åpne kilder, slik at plattformutviklingen kan starte uten å vente på pilotkunder. Pilotene kommer inn senere som validering — de endrer data (malene og skjemaet), ikke motorens kode.

## Innhold

| Fil | Hva den er |
|---|---|
| `policy-schema-v0.2.json` | Det generiske formatet M-1 leser — JSON Schema 2020-12, `additionalProperties: false`. Likt for alle bedrifter. Dette er kontrakten motoren kodes mot. Innført i PR-002. |
| `bransjemal-tjenestebedrift.yaml` | Startpolicy for tjeneste-/rådgivningsbedrifter |
| `bransjemal-netthandel.yaml` | Startpolicy for netthandel (angrerett, refusjon, samtykke) |
| `bransjemal-handverk-bygg.yaml` | Startpolicy for håndverk/bygg (prosjekt, underleverandør, HMS) |

Alle beløpsgrenser er bevisst forsiktige startverdier. Kunden justerer dem selv — det er poenget med at policy er data.

## Viktig premiss

Malene er **utkast fra åpne kilder** (lovverk, bransjepraksis, vilkårssider). De dekker utsiden av bransjene. Interne fullmakter — hvem godkjenner hva, faktiske beløpsgrenser, unntakene — finnes ikke i åpne kilder og fylles inn per kunde. Feltet `meta.status` skal derfor gå `utkast → validert_pilot → produksjon`, og ingen mal markedsføres som ferdig før minst én reell bedrift har kjørt den i skyggemodus.

## AI-arbeidsflyt: Claude → ChatGPT → Claude Code → Codex

Foreslått pipeline, med kvalitetsporter som gjør den trygg:

**Steg 1 — Draft (Claude):** Claude skriver spesifikasjon/kode for én modul om gangen, alltid mot akseptansekriteriene i prototype v8. Én modul = én branch = én pull request. Aldri flere moduler i samme PR.

**Steg 2 — Spesifikasjonsreview (ChatGPT):** ChatGPT reviewer drafts mot tre faste spørsmål: (a) Bryter noe med policy-skjemaet? (b) Er alle handlinger reversible eller eksplisitt merket irreversible med harde vilkår? (c) Mangler unntakshåndtering for noen feilvei? Review-svaret limes inn i PR-beskrivelsen.

**Steg 3 — Implementering (Claude Code):** Implementerer mot spesifikasjonen. Hver PR må inneholde: enhetstester, minst én test per akseptansekriterium fra v8-modulen, og en negativ test som beviser at handling utenfor policy faktisk stoppes.

**Steg 4 — Kodereview + merge (Codex):** Codex reviewer koden. Merge tillates kun når alle porter er grønne:

1. CI: alle tester passerer, inkludert negative policytester
2. Ingen handling i koden mangler `ved_brudd`-håndtering
3. Dekning av akseptansekriteriene dokumentert i PR
4. Ingen secrets, ingen direkte skrivetilgang utenom policymotoren

> ⛔ **Status 2026-08-01: anbefalingen under er IKKE fulgt.** Eier har bestemt at merge-porten driftes av Claude Code og Codex uten Eier, også på tillitsankeret. Avsnittet står igjen fordi begrunnelsen fortsatt er gyldig og bør kunne leses opp igjen den dagen noen vurderer å gjeninnføre porten — se `docs/RUTINER.md` pkt. 8 for hva som faktisk er slått på.

**Én menneskelig port anbefales beholdt:** endringer i selve policymotoren (M-1), revisjonsloggen (M-2) og unntaksmotoren (M-37) bør kreve menneskelig godkjenning av merge. Begrunnelsen er deres egen arkitektur: disse tre er tillitsankeret alle andre moduler hviler på. Å la AI merge endringer i sikkerhetsfundamentet uten menneske er samme feil som «null menneskelig innblanding»-påstanden dere allerede har forlatt — helautomatisk normaldrift, policybasert unntakshåndtering. Utviklingspipelinen bør følge samme prinsipp som produktet.

## Utrullingsløype — ingenting rett i produksjon

Merge til main er ikke deploy. Etter merge går hver endring gjennom en automatisert løype der hvert steg blokkerer neste: (1) CI med enhetstester, negative policytester og WCAG-test, (2) isolert testmiljø med syntetiske data og sandkasse-integrasjoner (aldri kundedata, aldri ekte systemer), (3) modell-/agentevaluering (M-31) mot regresjonssett, (4) kanarikjøring der kunde null alltid får versjonen først, deretter en liten andel tenants, (5) gradvis blå/grønn-utrulling til 100 %, og (6) automatisk rollback ved avvik, logget i M-2 og klassifisert i M-37. Endringer i policy-skjema og bransjemaler følger nøyaktig samme løype som kode: valideres, testes mot syntetiske hendelser, versjoneres med rollback — først da aktiveres de for kunder. Full spesifikasjon står i prototype v8, seksjonen «Utrulling».

## Status og neste steg

**Gjennomført:** PR-001 (validator-kjernen) og PR-002 (sikkerhets- og
skjemakontrakt etter ChatGPT-review: autentisert kontekst, betrodde
attestasjoner, Decimal-beløp, strukturert frekvens med atomisk
reservasjon, fail-closed dataklasser, IANA-tidssoner,
logg-før-utførelse). 62 tester på main. Punkt 1–3 i den opprinnelige
byggeplanen er dermed levert.

**PR-003 (denne endringen):** de utgåtte v0.1-filene er slettet, ADR-001 er
vedtatt, og dette dokumentet beskriver nå faktisk tilstand.

**Neste:**
1. **PR-004 (tilstandslag):** PostgreSQL for revisjonslogg og
   frekvensteller + kryptografisk attestasjonsverifikasjon — bindende
   krav i `docs/beslutninger/ADR-001-revisjonslogg-i-postgresql.md`.
   PostgreSQL 18 er installert på staging (Cloud Server S) og røyktestet;
   tilkoblingsstrengen ligger i `~/disponit-staging/.env` på serveren.
2. **PR-005 (M-37 unntakskø):** strukturert kø for alt som stoppes,
   inkl. utførelse av kompenserende reversering.
3. **Bransjemaler:** utvid fra 3 til 10–15 fra åpne kilder mens
   tilstandslaget bygges; deretter første pilotkunde i skyggemodus og
   malstatus `utkast → validert_pilot`.

Prinsippet står: bygg generisk, la virkeligheten korrigere data — ikke
arkitektur.
