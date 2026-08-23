# M-57 Rekruttering — manuell tastaturgjennomgang (klarsignalet §8)

Gjennomført i flaten `#/rekruttering`. Punktene 2, 5 og 6 er i
tillegg PORTET i `rekruttering.test.js` (vektendring uten mus med
kunngjøring, blindingsdialogen, signaturdialogen); punktene 1, 3 og 4
(navigasjon, sorteringsknappens tastaturaktivering, fokusfella/Escape i
detaljpanelet) er den MANUELLE gjennomgangen dette dokumentet er —
fokusfella selv er portet der den bor, i dialogkomponentens egne tester.
Dokumentet er det porten `ui.tastaturgjennomgang_dokumentert` krever.

| # | Flyt | Tastene | Forventet — og observert |
|---|---|---|---|
| 1 | Nå flaten | `Tab` fra menyen → «Rekruttering», `Enter` | Fokus lander i hovedinnholdet; overskriften leses |
| 2 | Endre **vekt** | `Tab` til range-kontrollen, `←`/`→` | Synlig verdi (output) følger; tabellen re-rangeres; ny rekkefølge annonseres i `aria-live="polite"` uten fokusflytting |
| 3 | **Sortere** tabellen | `Tab` til kolonneknappen «Poeng», `Enter` | `aria-sort` veksler ascending/descending; rekkefølgen snur |
| 4 | Kandidat**detaljer** | `Tab` til «Detaljer», `Enter` | Dialog med fokusfelle; `Tab` sirkler inne i panelet; `Esc` lukker og fokus RETURNERES til «Detaljer»-knappen |
| 5 | Skru av **blinding** | `Tab` til bryteren, `Space` | `alertdialog` åpnes med fokus i første felt; begrunnelsen er påkrevd; `Esc` avbryter og bryteren står PÅ |
| 6 | **Signer**e en liste | `Tab` til «Signer og send», `Enter` | `alertdialog` med antall, listetype og hashens kortform + «Kan ikke angres»; `Tab` når Avbryt og Signer; utfallet leses fra `role="alert"` |

Ingen av flytene krever mus eller pekerpresisjon; ingen informasjon
bæres av farge alene (kategorien står som tekst i cellen).
