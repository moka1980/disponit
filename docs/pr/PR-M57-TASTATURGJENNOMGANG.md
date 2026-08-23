# M-57 Rekruttering — tastaturgjennomgang (klarsignalet §8)

## Hvor gjennomgangen faktisk er gjort

**Flaten står IKKE i sitekartet.** Rekrutteringsruten er bevisst tatt ut
til serverendepunktene `/v1/rekruttering/*` finnes (Codex P1 / Cursor P1,
fiksrunde 1), og `tillatteFlater` holder også en håndskrevet
`#/rekruttering` ute. Det finnes altså ingen menyvei til flaten i dag, og
dokumentet skal ikke påstå en.

Gjennomgangen er derfor gjort på **flatens egen modul i jsdom-brettet**
(`platform/core/ui/test/rekruttering.test.js`), der `visRekruttering`
monteres direkte i et `<main id="hovedinnhold">` med stubbet transport —
samme tre som en økt ville fått, uten ruteren foran. Flytene under er
merket ærlig: **PORTET** betyr at en test i suiten feiler hvis flyten
ryker, **KOMPONENTPORTET** at mekanismen er portet der den bor (dialogens
egne tester), og **UTESTÅENDE** at flyten hører til ruteren og først kan
gjennomgås når ruten er inne.

Sist gjennomgått: 23. august 2026, fiksrunde 2 (HEAD på `pr-m57-cp4-v2`).

## Flytene

| # | Flyt | Tastene | Forventet — og observert | Status |
|---|---|---|---|---|
| 1 | Nå flaten | `Tab` fra menyen → «Rekruttering», `Enter` | Fokus lander i hovedinnholdet; overskriften leses | **UTESTÅENDE** — ruten står ikke i sitekartet (`sitekart.test.js` porterer at den er ute). Gjennomgås når serverarmen lander |
| 2 | Endre **vekt** | `Tab` til range-kontrollen, `←`/`→` | Synlig verdi (output) følger; tabellen re-rangeres; ny rekkefølge annonseres i `aria-live="polite"` uten fokusflytting | **PORTET** — «vektendring uten mus re-rangerer og kunngjøres» |
| 3 | **Sortere** tabellen | `Tab` til kolonneknappen «Poeng», `Enter` | `aria-sort` veksler ascending/descending; rekkefølgen snur | **KOMPONENTPORTET** — sorteringsknappen og `aria-sort` er `DataTabell`s egne tester; flaten porterer utgangssorteringen |
| 4 | Kandidat**detaljer** | `Tab` til «Detaljer», `Enter` | Dialog med fokusfelle; `Tab` sirkler inne i panelet; `Esc` lukker og fokus RETURNERES til «Detaljer»-knappen | **PORTET** — «`Detaljer` åpner panelet med funn, sitat og spørsmål». Fokusfella og `Esc` er `Detaljpanel`/`aapneDialog` sine egne tester. NB: knappen var død fram til fiksrunde 2 (radhandlingen ble sendt som `utfor`, ikke `paaKlikk`); punktet sto som «observert» i dette dokumentet uten å være det. Nå er den portet, ikke påstått |
| 5 | Skru av **blinding** | `Tab` til bryteren, `Space` | `alertdialog` åpnes med fokus i første felt; begrunnelsen er påkrevd og en tom begrunnelse lar dialogen STÅ; `Esc` avbryter og bryteren står PÅ | **PORTET** — «avskruing av blinding krever alertdialog med begrunnelse» |
| 6 | **Signer**e en liste | `Tab` til «Signer og send», `Enter` | `alertdialog` med antall, listetype og hashens kortform + «Kan ikke angres»; `Tab` når Avbryt og Signer; utfallet leses fra `role="alert"` | **PORTET** — «signaturdialogen sier antall, type, hashkortform …» (port 31) |

Ingen av flytene krever mus eller pekerpresisjon; ingen informasjon bæres
av farge alene (kategorien står som tekst i cellen, og fargeklassene er
en redundant koding oppå — `komponenter.css`).

Dokumentet er det porten `ui.tastaturgjennomgang_dokumentert` krever. Det
er verdiløst hvis det påstår mer enn det som er målt: en gjennomgang som
sier «observert» om en knapp som ikke gjorde noe, er falsk evidens. Står
en flyt som UTESTÅENDE her, er det fordi den ikke KAN gjennomgås ennå —
ikke fordi den ble hoppet over.
