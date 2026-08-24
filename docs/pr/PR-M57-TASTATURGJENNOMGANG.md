# M-57 Rekruttering — tastaturgjennomgang (klarsignalet §8)

## Hvor gjennomgangen faktisk er gjort

**Flaten står i sitekartet fra og med utførelsesarmen (#176).** Ruten var
bevisst tatt ut til serverendepunktene `/v1/rekruttering/*` fantes (Codex
P1 / Cursor P1, fiksrunde 1). De er registrert i `api/app.py` nå, og
oppføringen `{ nokkel: "rekruttering", scope: "decisions:read" }` er
derfor tilbake i `BASISRUTER` — i samme deployerbare endring, som
kommentaren lovet.

Menyveien er dermed gåbar, og rad 1 under er endret fra UTESTÅENDE til
PORTET. Den er portet på det som faktisk er MÅLT, og ikke mer:
`sitekart.test.js` porterer begge leddene som gjorde adressen uåpnbar —
at `byggRuter` gir en leseøkt ruten, og at `tillatteFlater` slipper
flaten gjennom for den økten (uten begge lander `#/rekruttering` på
reserveflaten Oversikt). Selve fokusflyttingen inn i hovedinnholdet er
ruterens generelle atferd (`ruter.js`, `hoved.focus()` ved hver
navigasjon som ikke er første tegning) — den er delt av samtlige ruter og
hører ikke denne flaten til.

Resten av gjennomgangen er gjort på **flatens egen modul i jsdom-brettet**
(`platform/core/ui/test/rekruttering.test.js`), der `visRekruttering`
monteres direkte i et `<main id="hovedinnhold">` med stubbet transport —
samme tre som en økt ville fått, uten ruteren foran. Flytene under er
merket ærlig: **PORTET** betyr at en test i suiten feiler hvis flyten
ryker, **KOMPONENTPORTET** at mekanismen er portet der den bor (dialogens
egne tester), og **UTESTÅENDE** at flyten hører til ruteren og først kan
gjennomgås når ruten er inne.

Sist gjennomgått: 24. august 2026, Cursor-fiksrunde 1 på #176 (HEAD på
`m57-utforelsesarm`).

## Flytene

| # | Flyt | Tastene | Forventet — og observert | Status |
|---|---|---|---|---|
| 1 | Nå flaten | `Tab` fra menyen → «Rekruttering», `Enter` | Menyoppføringen finnes for en `decisions:read`-økt, og `#/rekruttering` rendrer rekrutteringsflaten — ikke reserveflaten. Fokus lander i hovedinnholdet; overskriften leses | **PORTET** — `sitekart.test.js`: «byggRuter: hver rute krever scopet API-et bak flaten krever» asserterer både at ruten er i `byggRuter` for leseøkten og at `tillatteFlater` slipper flaten gjennom. NB: raden sto som UTESTÅENDE fram til utførelsesarmen (#176) — ruten var ute mens `/v1/rekruttering/*` ikke fantes, og flyten var da umulig å gå. Fokusflyttingen selv er ruterens generelle atferd (`ruter.js`), delt av alle ruter |
| 2 | Endre **vekt** | `Tab` til range-kontrollen, `←`/`→` | Synlig verdi (output) følger; tabellen re-rangeres; ny rekkefølge annonseres i `aria-live="polite"` uten fokusflytting | **PORTET** — «vektendring uten mus re-rangerer og kunngjøres» |
| 3 | **Sortere** tabellen | `Tab` til kolonneknappen «Poeng», `Enter` | `aria-sort` veksler ascending/descending; rekkefølgen snur | **KOMPONENTPORTET** — sorteringsknappen og `aria-sort` er `DataTabell`s egne tester; flaten porterer utgangssorteringen |
| 4 | Kandidat**detaljer** | `Tab` til «Detaljer», `Enter` | Dialog med fokusfelle; `Tab` sirkler inne i panelet; `Esc` lukker og fokus RETURNERES til «Detaljer»-knappen | **PORTET** — «`Detaljer` åpner panelet med funn, sitat og spørsmål». Fokusfella og `Esc` er `Detaljpanel`/`aapneDialog` sine egne tester. NB: knappen var død fram til fiksrunde 2 (radhandlingen ble sendt som `utfor`, ikke `paaKlikk`); punktet sto som «observert» i dette dokumentet uten å være det. Nå er den portet, ikke påstått |
| 5 | Skru av **blinding** | `Tab` til bryteren, `Space` | `alertdialog` åpnes med fokus på dialogens **lukkeknapp** — `aapneDialog` fokuserer første fokuserbare i DOM-rekkefølge, og det er lukkeknappen i dialogtoppen, ikke begrunnelsesfeltet; `Tab` når feltet i neste steg. Begrunnelsen er påkrevd, og en tom begrunnelse lar dialogen STÅ; `Esc` avbryter og bryteren står PÅ | **PORTET** — «avskruing av blinding krever alertdialog med begrunnelse», og åpningsfokuset er nå ASSERTERT i samme test. NB: raden sto som «fokus i første felt» fram til fiksrunde 3 — det var en påstand, ikke en observasjon (Codex P2) |
| 6 | **Signer**e en liste | `Tab` til «Signer og send», `Enter` | `alertdialog` med antall, listetype og hashens kortform + «Kan ikke angres»; `Tab` når Avbryt og Signer; utfallet leses fra `role="alert"` | **PORTET** — «signaturdialogen sier antall, type, hashkortform …» (port 31) |

Ingen av flytene krever mus eller pekerpresisjon; ingen informasjon bæres
av farge alene (kategorien står som tekst i cellen, og fargeklassene er
en redundant koding oppå — `komponenter.css`).

Dokumentet er det porten `ui.tastaturgjennomgang_dokumentert` krever. Det
er verdiløst hvis det påstår mer enn det som er målt: en gjennomgang som
sier «observert» om en knapp som ikke gjorde noe, er falsk evidens. Står
en flyt som UTESTÅENDE her, er det fordi den ikke KAN gjennomgås ennå —
ikke fordi den ble hoppet over.
