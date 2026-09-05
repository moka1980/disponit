# M-53 mot M-30: oppbevaringsplikt mot sletteplikt

`docs/KLYNGE7-FUNDAMENT.md` sa at M-53 bygges sist, og at
**grensesnittet mot M-30 avklares FØR koden**. Dette er den
avklaringen. Den er skrevet etter å ha lest 093 (M-4s
retensjonsregister) og 099 (M-30s personvernregister) linje for linje,
og den endrer på ett punkt det klyngefundamentet trodde.

## Det klyngefundamentet trodde

> «M-30 eier sletteretten; M-53 vil ha rader M-30 ikke får røre.»

Formuleringen forutsetter at M-30 SLETTER. **Det gjør den ikke.**

M-30 er et REGISTER over forespørsler, ikke en sletter. Den holder
`subjekt_ref` som en henvisning og ikke som identitet, `svar_ref` som
en henvisning og ikke som innholdet i svaret, og 099 sier det rett ut
om koblingstabellen:

> «saken sier hvilke lagre den gjelder, `retensjonslager` (093) sier
> hvilken reaper og hvilken frist som gjelder for hvert av dem, og
> UTFØRELSEN gjøres av den som eier lageret. **Ingenting i denne
> migrasjonen rører en eneste rad i et av dem.**»

Konflikten er derfor ikke to moduler som slåss om de samme radene. Den
er noe smalere og mer presist: **et menneske skal skrive ett svar på en
slettesak, og M-53 er lageret der svaret må bli et delvis nei.**

## Det som allerede er løst, og som vi ikke skal bygge på nytt

M-4s `retensjonslager` svarer allerede på spørsmålet «hva er
oppbevaringsregimet for dette lageret». M-53 skal registrere seg der,
som en hvilken som helst annen tabell, med:

| felt | verdi | hvorfor |
|---|---|---|
| `dom` | `under_frist` | HMS-avvik HAR en frist — den er bare lang. `uten_frist_akseptert` ville vært en løgn: det leses som «ingen frist, og det er greit», mens dette er et lager som er LÅST av en hjemmel. |
| `reaper` | `m53_reap_avvik` | ANONYMISERER, sletter ikke. M-50s dom (124), ordrett: sletting ville fjernet beviset på at vi hadde raden. |
| `reapetkolonne` | `anonymisert_ts` | |
| `fristkilde` | `hmsavvik.oppbevaring_til` | Ikke et tall. Se neste avsnitt. |

`retensjonslager_dom_vakt` krever alle tre for `under_frist`, og §2s
triggere i 093 sjekker at reaperen faktisk står i `pg_proc`. Vi kan
ikke registrere påstanden uten å ha bygget den.

## Det registeret IKKE kan svare på, og som er M-53s egen jobb

`retensjonslager.frist_dogn` er ETT tall for HELE lageret. HMS-frister
er ikke ett tall:

* et ordinært avvik uten personskade,
* en personskade med helseopplysninger etter GDPR art. 9,
* et varsel etter arbeidsmiljøloven kap. 2 A

har ulik hjemmel og ulik lengde. Registreres de under ett tall, er
tallet feil for minst to av tre — og et lager som SIER at det står
under frist mens fristen er feil, er verre enn et lager som sier at det
ikke har noen.

**Derfor bærer HVER RAD sin egen hjemmel.**
`hmsavvik.oppbevaring_hjemmel` og `hmsavvik.oppbevaring_til` er
`NOT NULL`. Det er invarianten `avvik_uten_oppbevaringshjemmel`, og
formen er M-50s `journalperson.slettefrist NOT NULL` (124): et avvik
uten oppbevaringsgrunnlag skal ikke kunne OPPSTÅ, fordi oppdagelsen
kommer for sent. `retensjonslager`-raden er etiketten på skapet;
hjemmelen på raden er det som gjelder.

## Hullet vi IKKE lukker i M-53, og hva vi gjør i stedet

`personvernsak.status` er `('apen', 'besvart', 'avvist')`, og
`personvernsak_lager` har **ingen status per lager** — jeg leste etter
en, den finnes ikke. En slettesak som dekker fem lagre får ETT svar.

Og GDPR art. 17 nr. 3 bokstav b gjør nettopp det delte svaret til det
RIKTIGE svaret: sletteretten gjelder ikke der behandlingen er
nødvendig for å oppfylle en rettslig forpliktelse. Det korrekte svaret
på en slettesak som treffer avviksregisteret er «ja til de fire, nei
til det femte, med hjemmel» — og den setningen har M-30 ikke plass
til.

**Vi utvider ikke M-30.** Migrasjonene er forward-only, og å utvide et
lukket sett i en merget modul for en modul som ennå ikke finnes, ville
vært å endre den fungerende for den ubygde. Hullet står skrevet her i
stedet, og M-53 gjør det så billig som mulig å leve med:

`m53_oppbevaringsgrunnlag(p_tenant, p_avvik_id)` returnerer hjemmelen,
datoen den løper til, regelversjonen den ble regnet under, og om
anonymisering er mulig nå. Det er SETNINGEN saksbehandleren limer inn
i `personvernsak.avvist_begrunnelse` — som 099 uansett krever er
ikke-tom. Vi flytter ikke avgjørelsen inn i maskinen; vi sørger for at
mennesket som tar den, har hjemmelen for hånden i det øyeblikket det
skrives, i stedet for å måtte lete etter den.

**`sletting_uten_m30_avklaring` blir dermed målbar:** anonymisering FØR
oppbevaringsfristen krever en henvisning til en M-30-sak. Etter
fristen gjør reaperen det uten. En rad som forsvant tidlig uten en sak
å vise til, er et funn.

## Det som ikke er M-30, og som er skarpere

Avklaringen over er den klyngefundamentet ba om. Under arbeidet med den
kom en ting fram som ikke står der, og som er verre:

**HUSETS EGEN STANDARDKOLONNE ER LEKKASJEN.**

Hver tabell i dette huset har `opprettet_av TEXT NOT NULL`. Den er
riktig overalt ellers — 099 la til og med `lukket_av` med overlegg,
fordi «en statusovergang som ikke bærer navnet sitt er en overgang en
jobb kunne ha gjort».

På et ANONYMT avvik er den samme kolonnen selve bruddet. Et anonymt
avvik som bærer aktøren i `opprettet_av` er ikke anonymt; det er et
avvik der navnet står i en annen kolonne enn den man ser på. Og verre:
`revisjonslogg` er append-only, håndhevet av trigger siden 001. **Et
navn som lekker inn i evidenskjeden kan aldri fjernes igjen** — den
samme garantien som gjør beviskjeden troverdig, gjør lekkasjen
permanent. M-30 så det for sitt eget register og skrev det inn i
`m30_evidens`: `subjekt_ref` står ALDRI i evidensraden. Her gjelder
det varsleren.

Derfor, i M-53:

1. **Anonymt avvik er en FØRSTEKLASSES TILSTAND, ikke et tomt
   navnefelt.** Melderen er en egen rad som ikke finnes i det hele
   tatt, ikke en NULL. Et felt som KAN fylles blir fylt.
2. `opprettet_av` er NULL på anonyme avvik — kolonnen kan altså ikke
   være `NOT NULL` i denne tabellen, og avviket fra husformen står
   skrevet i migrasjonen med denne begrunnelsen.
3. Evidenskjeden skriver `aktor = NULL` for anonyme avvik.
   `revisjonslogg.aktor` er nullbar (001), så formen finnes allerede.
4. **Tidsstemplet er også identitet.** `now()` på mikrosekundet, i en
   bedrift med tolv ansatte og en vaktliste, peker på én person.
   Anonyme avvik bærer DATO, ikke tidspunkt.
5. **Fritekst kan ikke sikres, og vi later ikke som.** «Jeg sa fra til
   formannen på tirsdag» identifiserer melderen uansett hva skjemaet
   gjør. Flaten sier det til den som skriver, før den skriver. Det er
   den ærlige grensen for hva en database kan love.

Punkt 4 og 5 er ikke i `KRAVGRENSER`. De hører til
`anonymt_avvik_kan_spores`, og de måles.

## V1-dommen

Uendret fra klyngen: modulen VARSLER INGEN MYNDIGHET og LUKKER INGEN
AVVIK. Arbeidstilsynet får ingenting fra oss i v1. Et avviksmottak som
lukket avvik selv, ville vært en HMS-avdeling uten mennesker i.
