# ADR-004: Klasse F — plattformvakt

**Status:** vedtatt 2026-11-19 (Claude Code, med eiers fullmakt «du kan
bestemme arkitektavklaringen»).
**Gjelder:** M-10 (backupinnsyn) og M-11 (selvtest).
**Bygger på:** ADR-002 og ADR-003.

## Problemet

M-10 og M-11 måler plattformen, ikke en kunde. Hver av dem har:

- **én skrivedør** gitt til **én jobbrolle**: `registrer_backupverifisering`
  til `disponit_driftstatus`, og `registrer_selvtest` til
  `disponit_selvtest`;
- **én lesedør** (`backup_status`, `selvtest_status`);
- **ett forpass i varselsenderen** som varsler plattformtenantens
  administratorer når jobben har vært taus for lenge (30 t / 3 t).

De har ingen tenantkolonne og skriver ingen revisjonslogg. Begge kjører
allerede i drift. 19/11 sto det 79 verifiserte backuper i basen, og
selvtesten hadde vært grønn hver time det siste døgnet. Klasse B (M-03,
M-04) er den nærmeste formen, men B har ingen varslingsvei. Klasse E har
varslingsveien, men E er tenantskopet og evidensskrivende.

## Dommen

**F0. Klasse F er «plattformvakt».** Én produsent
(`deploy/staging/plattformvakt-lest.py --modul X`). Fasit, feilinjisering og
rollback måles i den isolerte basen på 55432, med en egen
rigg-plattformtenant, fordi riggen skriver plattformtabeller. Ytelsen måles
på den ekte jobben mot driftsbasen, som i klasse B.

**F1. Syntetisk datasett.** Tre egenskaper måles:

- **Taushet varsles.** Når tabellen er tom, varsles det på samme terskel som
  når siste kjøring er for gammel: fravær er feil. Det skjer én gang per
  døgn, og bare til administratorene. Riggen har to administratorer, én med
  bare portal og én med e-post, og ett medlem uten admin som aldri skal få
  varselet.
- **Jobbens grunnlag står med egne tall.**
  - M-10: den ekte jobben fører en riggrapport inn. Lesedøren viser
    rapportens egne tall, et gjenspill skriver ingenting, og en manglende
    rapport er den milde tilstanden (kode 0).
  - M-11: seks runder med kjent samletdom, som til sammen dekker grønn,
    rød og ikke-konfigurert. SMTP-proben kjøres av jobbens egen kode mot
    riggfiler. En rød probe gir ett varsel per administrator i samme
    transaksjon, og samme røde probe samme døgn gir ikke et nytt.
- **Negativ kontroll.** Når jobben er fersk, gir forpasset ingen varsler i
  en tenant som har administratorer.

**F2. Revisjonslogg: målt fravær med positiv kontroll** (ADR-002 D1, klasse
B). En full fasitkjøring gir null nye rader i `revisjonslogg`, mens jobben
beviselig skrev. Katalogbeviset viser at bare jobbrollen har `EXECUTE` på
skrivedøren, og at bare dørens eier har `INSERT` på tabellene.

**F3. Feilinjisering.** Bare ekte driftsfeil brukes:

- **M-10:** en rapport som mangler et felt, en med feil type, et CHECK-brudd
  i basen og tilbaketrukket `EXECUTE` på skrivedøren. Hver skal gi kode ≠ 0
  og ingen rader.
- **M-11:** en runde der probene feiler (sokkel og enhetsfiler finnes ikke)
  skal fortsatt registreres med hver probe. Tilbaketrukket `EXECUTE` på
  skrivedøren skal gi kode ≠ 0 og ingen rader.
- **Begge:** tilbaketrukket `EXECUTE` på forpasset er E2 fra ADR-003. Den
  ordinære sendingen går, feilen telles separat, og senderen avslutter med
  kode ≠ 0.

Etter reparasjonen skal jobben og forpasset gå rent igjen.

**F4. Rollback.** Forgjengerens og kandidatens jobbkode kjøres mot samme
rigg, og må gi like svar. Et gjenspill over rullingen skal ikke skrive
noe.

**F5. Ytelse.** Den ekte jobben kjøres tre runder mot driftsbasen. Taket er
10 s for M-10 og 30 s for M-11, fordi selvtesten har 5 s per probe og en
unit-frist på 3 min.

**F6. Suiten** måles som for alle andre.

## Det dommen ikke gjør

Den flipper ingenting, og den endrer ingen terskel (30 t / 3 t).
