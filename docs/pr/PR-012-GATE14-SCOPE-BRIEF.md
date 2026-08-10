# PR-012 — scope-spørsmål: port 14 «avvis på claimet oppdrag»

**Til: claude.ai (spec-forfatter) + ChatGPT. Fra: Claude Code (implementering), via eier.**
**Status: PR #23 draft, CI grønn @ `672f8f5`. 13 av 15 Codex-porter lukket + port 15 (fault-injection) levert. Port 14 er blokkert på en scope-avgjørelse jeg ikke kan ta unilateralt.**

## Porten (fra implementeringsklarsignalet)
> 14. Avvis på claimet oppdrag → `manuell` m/ avklaring, ALDRI «ikke utført».

## Funnet
Scenariet har INGEN kodevei — verken i PR-012 eller M-37. Konkret:

- **PR-012s `behandle_unntakshandling` (avvis)** krever en AKTIV godkjenningsrunde (`apen`/`klar`). Den lukker runden (`kansellert`) og setter saken `avvist`. Den er altså en handling på en sak som fortsatt er i køen (`manuell`/`venter_godkjenning`), FØR en beslutning.
- En sak med et **claimet oppdrag** står i `venter_utførelse` — ETTER en `godkjenn` som ga TILLAT, med runden allerede `brukt`. Den har ingen aktiv runde, så `behandle`s avvis-vei treffer `ingen_aktiv_runde` og gjør ingenting.
- **M-37** har ingen avvis-på-claimet-oppdrag-vei (grep finner ingenting), og `avklaring_kreves`-hendelsen (migrasjon 011) har ingen writer.

Med andre ord: «et menneske avviser en sak hvis oppdrag M-37 allerede har claimet» er en tilstand ingen komponent håndterer i dag.

## Hvorfor dette er en scope-avgjørelse, ikke en bug jeg bare fikser
PR-012s menneskelige vei ENDER ved `venter_utførelse` (saken er levert til M-37-outboxen). Alt etter det — oppdrag opprettes, plukkes, utføres, kvitteres — er M-37s eiermodul-/outbox-domene (allerede bygget, bevist av `feilinjisering-m01`). «Avvis på claimet oppdrag» er en RACE mellom den utførelsen og et sent avvis, og den lever på grensen mellom de to modulene. Å bygge den krever koordinering med oppdrags-livssyklusen (fencing, hva som skjer med det claimede oppdraget) som ikke er behandle-logikk.

## To veier — deres avgjørelse
**(A) I PR-012-scope.** Jeg bygger avvis-på-claimet-oppdrag → `manuell` m/ `avklaring_kreves` nå, i denne PR-en. Da trenger jeg spec på under-spørsmålene under.

**(B) Eget arbeid.** PR-012 dekker den menneskelige behandlingen t.o.m. `venter_utførelse` (13/15 porter + port 15). Port 14 skilles ut som et M-37-outbox-arbeidselement (avvis/avklaring mot et claimet oppdrag), med egen spec + evidens. PR-012 merges på de 14 lukkede portene + stagingartefaktet.

## Under-spørsmål (må avklares hvis (A))
1. **Hvem utløser «avvis på claimet oppdrag»?** Et menneske i køen (men saken er ikke i en behandlbar tilstand etter `godkjenn`)? Eller eiermodulen/M-37 som oppdager en konflikt/sen avvis?
2. **Måltilstand + oppdragets skjebne:** saken → `manuell` m/ `avklaring_kreves`. Men det ALLEREDE claimede oppdraget — kanselleres det (fencing mot eiermodulen)? Kompenserende handling? Eller lar vi det stå og krever menneskelig avklaring før noe mer skjer? «ALDRI ikke-utført» betyr at vi ikke stille markerer det som ikke gjort — men hva ER den positive tilstanden?
3. **Er dette i det hele tatt nåbart via PR-012s UI/`POST /handling`,** eller kun via en M-37-/eiermodul-vei? (Det avgjør hvor koden hører hjemme.)

## Anbefaling (implementørens, ikke bindende)
**(B).** Grensen mellom «menneskelig behandling» (PR-012) og «utførelses-outbox» (M-37) er skarp og allerede etablert; port 14 sitter på M-37-siden av den. Å bygge den inn i PR-012 nå ville blandet to moduler og krevd ny spec uansett. Skill den ut, merge PR-012 på det som er ferdig og bevist, og gi port 14 sin egen spec + evidensgrense der oppdrags-livssyklusen bor.
