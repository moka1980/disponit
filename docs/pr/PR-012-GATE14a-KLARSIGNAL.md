# GATE 14a — IMPLEMENTERINGSKLARSIGNAL (LUKKET, GO)

**Til Claude Code · Siste ledd i PR-012. Gate 14a i endelig form; 14b
registreres som eget M-37-arbeid. Etter dette: alle femten porter lukket.**

## De tre bindende presiseringene

### P1. ALLE relaterte rader, ikke én
Kontrollen gjelder samtlige oppdrag OG kapabiliteter for saken. Avvis
tillates KUN når **hver eneste** relatert rad positivt tilfredsstiller
kriteriet (intet oppdrag/kapabilitet, eller `kansellert` før claim med
fencing-evidens).
**Ingen `MAX`, ingen «siste oppdrag», ingen vilkårlig entallsrad.**
Én levende rad blant flere kansellerte → `avklaring_kreves`.

### P2. Foreldrelåsen hindrer phantom-innsetting
Uten dette kunne 14a sett «ingen oppdrag», mens en annen transaksjon satte
inn ett rett etter kontrollen — vakten ville vært omgåelig i et
samtidighetsvindu.
**ALLE kodeveier som oppretter oppdrag eller arbeidskapabilitet for en
sak må låse samme `unntak`-rad FØRST** (`SELECT ... FOR UPDATE` på saken
før INSERT). Det gjelder R1/fase 2, `behandle_unntakshandling`,
M-37-arbeideren og enhver fremtidig vei.
Codex-port: statisk sjekk på at ingen INSERT mot `oppdrag` eller
`arbeidskapabiliteter` skjer uten forutgående saks-lås.

### P3. Idempotens er SEMANTISK, ikke bare per nøkkel
`Idempotency-Key` beskytter transport-retry; **saksinvarianten beskytter
mot flere ulike nøkler.** Er `avklaring_kreves` allerede satt for samme
utestående tilstand:
- nytt forsøk gir fortsatt `409 utestaaende_oppdrag`,
- **ingen ny versjonsøkning**,
- **ingen ny historikkrad**.
Kun en ENDRET utestående tilstand (nytt oppdrag, endret kapabilitet) kan
gi ny flagging med versjonsøkning.

## Codex-porter for 14a
1. Sak uten oppdrag/kapabilitet → avvis virker
2. Oppdrag `kansellert` før claim m/ fencing-evidens → avvis virker
3. Enhver annen oppdragstilstand → 409 + `avklaring_kreves` committet
4. **Ukjent/ny oppdragsstatus → 409** (syntetisk status, fremtidssikring)
5. **Sak med BÅDE kansellert OG levende oppdrag → 409** (P1)
6. Utestående kapabilitet uten oppdrag → 409
7. **Kappløp: «ingen oppdrag funnet» vs. samtidig oppdragsopprettelse →
   aldri `avvist`** (P2 — saks-låsen serialiserer)
8. Ingen INSERT mot oppdrag/kapabilitet uten forutgående saks-lås (statisk)
9. Gjentatt forsøk, ulike idempotensnøkler → samme 409, ingen ny
   versjonsøkning, ingen ny historikkrad (P3)
10. `saksversjon` økt ved FØRSTE flagging; commit før 409-svaret
11. Kappløp kvittering vs. avvis → begge lovlige utfall per matrisen,
    aldri `avvist` mens utførelse er mulig eller bekreftet
12. Deadlock-test: blandet avvis / kvitteringsingest / timeout, null deadlock

## Rekkefølge i handleren (endelig)
1. Lås `unntak` (`FOR UPDATE`) — samme rad alle skrivere låser.
2. Les ALLE oppdrag + kapabiliteter for saken; krev positivt bevis for
   hver (P1).
3. Ikke bevist → sett `avklaring_kreves` (hvis ikke allerede satt for
   samme tilstand, P3), øk `saksversjon`, skriv historikk.
4. **COMMIT.**
5. Returner lagret `409 utestaaende_oppdrag`.
6. Bevist trygt → ordinær avvis-vei (kanseller åpen runde, sak → `avvist`).

---

## Etter dette

PR-012 har **alle femten porter lukket**. Gjenstår: staging-artefakt
`behandling-m37-v1`, Codex-review, merge. Gate 14b (oppløsning av
levende oppdrag ved menneskelig avvis) registreres som eget
M-37-outbox-arbeid med egen spesifikasjon og evidensgrense.

Da behandler Eier en ekte sak på disponit.com — og M-1 har gått hele
veien fra spesifikasjon til at et menneske griper inn i køen, uten at
policyen mistet makt underveis.
