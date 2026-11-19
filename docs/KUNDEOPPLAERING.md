# Opplæring: slik bruker kunden disponit i dag

Skrevet 18.9.2026, oppdatert 19.11.2026. Dekker de **40 modulene som er sertifisert og i
drift**. Gjelder det som faktisk virker nå, ikke det som er planlagt.

---

## 1. Den ene setningen som forklarer alt

> **Agentene finner og foreslår. De handler ikke.**

Hver modul er bygget slik med vilje. Lageragenten ser at en vare er under
bestillingspunktet, men den bestiller ikke. Fordringsagenten regner
aldersfordelingen, men den sender ikke purringen selv. Kontovakten ser at
et kontonummer er endret, men den stopper ingen betaling.

Grunnen er den samme hver gang: en automatisk handling er en beslutning,
og en beslutning trenger en ansvarlig med et navn. Modulene gjør det
maskiner er gode til — å se alt, hver natt, uten å bli lei — og lar
mennesket gjøre det mennesker er gode til: bestemme.

**Dette er det viktigste å få fram i opplæringen.** En kunde som venter
at systemet «ordner det selv» blir skuffet. En kunde som forstår at det
er en våken assistent som aldri glemmer, blir fornøyd.

---

## 2. Rytmen kunden lever i

Tre ting, i denne rekkefølgen. Og én ting som ikke er der: **det finnes
ingen «Utfør»-knapp noe sted.** Knappene heter FØR, REGISTRER, LAGRE,
ÅPNE og AVGJØR. Det er ikke en mangel; det er designet.

**Én gang, ved oppstart: sett kravet og legg inn grunnlaget.** Hver modul
spør om noe som er kundens eget. For noen er det tall — hvor mange døgn
en faktura kan stå ubetalt, hvor lenge en kontoverifikasjon er gyldig,
hva som er normal arbeidsdag. For andre er det grunndata: en mva-sats,
en onboardingmal med steg, en stillingsprofil. Modulen har ingen mening
om noe av dette. Uten det gjør den ingenting, og den sier fra: kunden får
funnet «Ingen grenser satt».

**Hver natt: sveipen går.** Mellom 04:00 og 07:45 UTC — altså 06:00 til
09:45 norsk sommertid — går hver modul gjennom alt den passer på og
skriver **funn**. De 40 sveipene er spredt utover vinduet, én modul om
gangen.

To moduler venter ikke på natten. **E-post** henter hvert femte minutt,
og et godkjent svar går ut innen fem minutter. **Rekruttering** har en
arbeider som evaluerer søknadsbunten fortløpende. **Kontovakt** er en
mellomting: selve kontoendringen ser kunden med én gang, fordi den
skrives i samme øyeblikk som kontonummeret føres — de andre funnene
kommer om natten. Et funn er en påstand med et tall
bak: «denne avtalen utløper om ti dager», «dette kollier har stått uten
bevegelse i 200 døgn». Kunden har ikke gjort noe; funnet var bare sant.

**Hver dag: kunden behandler funnene.** Et funn lukkes ikke ved å trykke
bort. Det lukkes ved at tilstanden opphører — varen telles, avtalen
fornyes, posten avstemmes — og neste natts sveip ser det og lukker funnet
selv. Noen funn kan et menneske lukke med en begrunnelse.

Det siste er verdt å si høyt i opplæringen: **kunden fjerner ikke funn,
kunden fjerner årsaken.**

---

## 3. Tre ting som går på tvers av alle modulene

**Policy- og fullmaktsmotoren (M-1)** avgjør hva en agent har lov til.
Fullmaktene velges ved registrering, og de er ikke pynt: en modul som
ikke har fullmakt gjør ingenting, uansett hvor åpenbar handlingen er.

**Revisjonsloggen (M-2)** skriver hver eneste handling med hvem, hva og
når. Den er ikke en logg man leter i når noe har gått galt — den er
grunnlaget for at en kunde kan vise en revisor hva som skjedde.

**Unntakskøen (M-37)** er der alt havner som en agent ikke kunne avgjøre.
Den er ikke en feilliste. Den er arbeidslisten, og at noe står der er
systemet som fungerer som det skal.

---

## 4. Hvor kunden finner dem

De fleste modulene har sin egen flate i menyen. Navnene kunden ser er
disse:

| Flaten heter | Adresse | Modul |
|---|---|---|
| Bankavstemming | `#/avstemming` | M-13 |
| Fakturakontroll | `#/faktura` | M-14 |
| Kundeservice | `#/kundeservice` | M-17 |
| Kunde-onboarding | `#/onboarding` | M-18 |
| Adresseregister | `#/adresse` | M-19 |
| Kundefordringer | `#/fordring` | M-23 |
| Leverandører og SLA | `#/leverandor` | M-24 |
| Prosjekter og kontrakter | `#/prosjekt` | M-25 |
| Prisbok og Tilbud | `#/prisbok`, `#/tilbud` | M-26 |
| Lager | `#/lager` | M-27 |
| Lønnsgrunnlag | `#/lonn` | M-39 |
| Betaling | `#/betaling` | M-41 |
| Kontovakt | `#/kontovakt` | M-42 |
| Kampanjeregister | `#/kampanje` | M-44 |
| E-post | `#/epost` | M-6 |
| Rekruttering | `#/rekruttering` | M-57 |
| WCAG kontroll | — | M-56 |

**Tre moduler har ingen egen flate, og det er med vilje.** Policy- og
fullmaktsmotoren (M-1) møter kunden som «Policy» og
«Policyadministrasjon», revisjonsloggen (M-2) ligger under hver enkelt
sak som sporet av hva som skjedde, og unntakskøen (M-37) møter kunden
som «Unntak». De er ikke arbeidsflater — de er lag som går under alle de
andre.

Kunden trenger ikke modulnumrene. De står her fordi de dukker opp i
manifester, i støttesaker og i katalogen på nettsiden.

---

## 5. Modulene, én for én

Kolonnen «passer på» er det kunden får. Kolonnen «gjør ikke» er like
viktig i opplæringen — det er der forventningene brister hvis de ikke
settes.

### Penger inn og ut

| Modul | Passer på | Gjør IKKE |
|---|---|---|
| **Bankavstemming (M-13)** | Bankposter som ikke er avstemt, bilag som er forfalt uten dekning, og bilag som bare er delvis dekket | Bokfører ikke. En automatisk bokføring er en skriving i regnskapet. |
| **Fakturakontroll (M-14)** | Dubletter, mva-avvik og fakturaer som mangler godkjenning | Betaler ikke. |
| **Kundefordringer (M-23)** | Forfalte fordringer og aldersfordelingen på dem | Foreslår ingen nedbetalingsplan overfor kunden. |
| **Betalingsstatus (M-41)** | Betalinger som står uavklart for lenge, beløpsavvik mot det forventede, og autorisasjoner som er utløpt | Refunderer ingenting, autoriserer ingen betaling. |
| **Kontovakt (M-42)** | Kontonummer som er endret, kontoer ingen har verifisert, og verifikasjoner som er for gamle | Verifiserer ingen konto mot en ekstern kanal, og stopper ingen betaling. |

**Fire øyne i kontovakten:** den som oppga kontonummeret kan ikke
verifisere det selv. Systemet nekter. Det er ikke et skjema som er
strengt — det er hele poenget med modulen.

### Kunder og leveranse

| Modul | Passer på | Gjør IKKE |
|---|---|---|
| **Kundeservice (M-17)** | Henvendelser som står uklassifisert eller ubesvart for lenge | Svarer ikke. Den foreslår et svar som **utkast**. |
| **E-post (M-6)** | Henter inn e-post fra Microsoft 365 hvert femte minutt og kobler den til kundeservice. Sletting i Outlook speiles hit. | Sender ingenting av seg selv. Et svar må godkjennes av et menneske, og går bare ut hvis postboksen er koblet til med skrivetilgang. |
| **Onboarding (M-18)** | Løp som har stoppet opp, steg som er forbi fristen, og løp uten en aktiv eier | Provisjonerer ingenting. Den holder oversikten, ikke spaden. |
| **Adressevalidering (M-19)** | Adresser ingen har kontrollert, kontroller som er utløpt, avviste adresser, og kontroller gjort med en metode kravet ikke godtar | Slår ingenting opp eksternt. Kundens adresse forlater aldri huset. |
| **Kampanje (M-44)** | Kampanjer, mottakere og samtykkets historikk | Sendte null før fullmakten fantes. Samtykke er en forutsetning, ikke en innstilling. |

### Drift og verdikjede

| Modul | Passer på | Gjør IKKE |
|---|---|---|
| **Leverandør og SLA (M-24)** | SLA-brudd, priser over terskel, avtaler som utløper, og avtaler ingen måler | Bestiller ikke, sier ikke opp. |
| **Prosjekt og kontrakt (M-25)** | Milepæler forbi frist, budsjett overskredet, prosjekter uten betalingsplan og uten registrert arbeid | Fakturerer ikke, attesterer ikke. |
| **Prisbok og tilbud (M-26)** | Prisboka og tilbudene som bygger på den | Setter ingen pris. |
| **Lager og logistikk (M-27)** | Varer under bestillingspunktet, dødt lager, varer uten bestillingspunkt, og beholdning ingen har talt | Bestiller ikke påfyll. |
| **Lønnsgrunnlag (M-39)** | Timer uten arbeidsplan, avvik mot planen, overtid, og timer ført på ukjent prosjektkode | Utbetaler ingenting og lager ingen lønnsfil. |

### Andre

| Modul | Passer på | Gjør IKKE |
|---|---|---|
| **Rekruttering / ATS (M-57)** | Søknader, kandidater og rangering | Avgjør ikke hvem som ansettes. |
| **Tidsvalg for intervju (M-08)** | Intervjutider per rekrutteringsprosess, og hvilken tid hver invitert kandidat har valgt | Skriver ikke i kundens kalender. Kandidaten velger selv bak en personlig lenke uten innlogging; et bekreftet valg er endelig, og en tid med et valg kan ikke trekkes. |
| **Datakvalitet (M-03)** | Tomme felt, feil format og duplikater i plattformens egne tabeller, målt mot faste regler — og regler som ikke lot seg måle | Retter ingenting, slår ingenting sammen og stopper ingen bestilling. Et funn forsvinner når dataene er rettet og neste måling ikke finner det igjen. |
| **Dokumentmaler (M-05)** | Malfamilier og versjoner gjennom utkast, publisert, tilbaketrukket og forkastet; utfylling fra en publisert mal | Skriver ingen dokumenter av seg selv og dikter ikke felt: et felt uten verdi markeres som manglende. En publisert versjon kan aldri endres — en endring er en ny versjon. |
| **Ordliste (M-09)** | Begreper som har gått ut på dato eller snart gjør det | Skriver ikke begreper selv. Ordlisten fylles av plattformen; en endring er en ny versjon, og den gamle består. |
| **Nøkkeltall (M-16)** | Tellinger over det som faktisk har skjedd: beslutninger, unntak åpnet og lukket, og hvor lang tid lukkingen tok, for 24 timer, 7 eller 30 dager | Analyserer ikke og spår ingenting. Tallene er tellinger over rader som finnes, og andelene står ved siden av tellingene de er regnet fra. |
| **Tilskudd (M-51)** | Aktive ordninger uten estimat, estimater uten beløp eller over ordningens tak, og søknadsfrister som nærmer seg eller er passert | Søker ikke på egen hånd og lover ingen utbetaling. Et estimat er et anslag til den som skal søke. |
| **Tilganger (M-12)** | Tilganger som har stått lenger enn sin egen gjennomgangsfrist uten at noen navngitt har sett på dem | Oppretter, flytter og fjerner ingen tilganger. En gjennomgang er en attestasjon fra et navngitt menneske, datert i dag. |
| **Personvernforespørsler (M-30)** | Innsyns- og sletteforespørsler som har passert fristen eller nærmer seg den, og forespørsler som ikke peker på noen lagre | Sletter ingenting og svarer ikke på egen hånd. En forespørsel lukkes av et skrevet svar, aldri av at fristen går ut. |
| **Postjournal og innsyn (M-50)** | Journalposter mot en kilde som har utløpt, kilder som snart utløper, og personopplysninger der slettefristen er passert eller nærmer seg | Publiserer ingenting og anonymiserer ikke selv. Anonymisering er en egen handling med navn på. |
| **HMS og avvik (M-53)** | Regelverk som har utløpt eller snart utløper, avvik der oppbevaringstiden snart går ut, og avvik som ble anonymisert for tidlig | Lukker ingen avvik og melder ikke videre til myndigheter. |
| **Retensjon (M-04)** | Plattformens egne lagre: lagre uten skrevet slettedom, lagre som ikke lot seg måle, og sletting som henger etter fristen | Sletter ingenting. Viser hvor data ligger og hvor lenge, slik at personvernforespørsler vet hvilke lagre de gjelder. |
| **Kontrollregister / compliance (M-34)** | Kontroller som ikke er etterprøvd innen fristen, og kontroller der eieren ikke lenger er med i firmaet | Etterprøver ikke selv og attesterer ingenting. «Oppfylt» krever en etterprøving med dato og evidenshenvisning. |
| **Likviditet (M-15)** | Prognoser som bygger på et grunnlag eldre enn grensen, prognoser der banen går under null, prognoser mot en modell som ikke gjelder lenger, og modeller som snart utløper | Flytter ingen penger og tar ikke opp lån. Prognosen er et øyeblikksbilde: lag en ny når grunnlaget har endret seg. |
| **Nettside og innhold (M-20)** | Utkast som er merket klare uten at noen har sett dem, og kilder som snart utløper mens de bærer en publisert påstand | Publiserer ingenting av seg selv. Publisering krever en forhåndsvisning av nøyaktig det som publiseres. |
| **Anbud (M-46)** | Frister som nærmer seg eller er passert, krav som ikke er dekket, anbud uten krav, og anbud utenfor søkeprofilen | Leverer ikke tilbud. Den holder oversikten over hva som mangler. |
| **Myndighetsrapportering (M-47)** | Rapportplikter med frist som nærmer seg eller er passert uten bevis, og regelverk som utløper eller er utløpt | Sender ingenting til myndighetene. Et bevis er kvitteringen kunden selv fikk. |
| **Motparter (M-48)** | Motparter ingen har vurdert, profiler uten vurdering, avviklede foretak, forslag over taket, og gjentatte feil mot foretaksregisteret | Setter ingen kredittgrense og stopper ingen ordre. |
| **Sanksjonskontroll (M-49)** | Subjekter som ikke er kontrollert, treff ingen har avklart, bekreftede treff, og kontroller mot en liste som er avløst | Avviser ingen kunde. Et treff er en sak for et menneske. |
| **Tollklassifisering (M-52)** | Forslag under sikkerhetsterskelen, forslag som ikke er merket klare, og regelverk som utløper eller er utløpt | Deklarerer ingenting. Et forslag under terskelen avgis aldri. |
| **EHF-kontroll (M-54)** | Formfeil uten retting, rettinger som ikke er merket klare, og regelsett som utløper eller er utløpt | Sender ingen faktura. Den kontrollerer formen, ikke innholdet. |
| **WCAG-kontroll (M-56)** | Automatisk tilgjengelighetskontroll av nettsteder kunden har bekreftet at de kontrollerer | Retter ikke nettstedet. |

---

## 6. Steg 1 for hver modul: sett grensene

Nesten hver modul har et eget funn som heter **«Ingen grenser satt»**
eller «Ingen krav satt». Det er modulens måte å si at den ikke kan måle
noe før kunden har fylt inn sine egne tall. Dette bør være første punkt
i enhver opplæringsøkt.

| Modul | Første steg | Hva som må fylles inn |
|---|---|---|
| Bankavstemming | «Registrer konto» | En bankkonto, så poster og bilag |
| Fakturakontroll | «Legg til sats» | En mva-sats — uten den blokkeres «Registrer faktura» |
| Kundeservice | «Lagre avsender» | Avsenderprofil, ellers går svar i tenantens navn |
| Kunde-onboarding | «Registrer mal» → «Lagre stegene» | En mal med minst ett steg |
| Adresseregister | «Lagre krav» | Frist, gyldighetstid, godkjente metoder |
| Kundefordringer | «Lagre purreplanen» | Purreplan og avsenderprofil |
| Leverandører og SLA | «Registrer leverandør» → «Lagre tersklene» | Hva «for dyrt» og «for dårlig» betyr hos dere |
| Prosjekter | «Registrer prosjekt» → «Lagre grensene» | Budsjett, milepælfrist, stillhetsgrense |
| Prisbok | «Registrer produkt» → «Lagre grensene» | Rabattgrense, utløpsvarsel |
| Lager | «Registrer vare» → «Sett bestillingspunkt» | Punkt med begrunnelse, så grensene |
| Lønnsgrunnlag | «Registrer lønnstaker» → «Sett arbeidsplan» | Normaltid dag og uke, avvik, vurderingsvindu |
| Betaling | «Registrer subjekt» → «Lagre grenser» | Uavklart-frist, beløpsavvik, reautorisasjonsfrist |
| Kontovakt | «Lagre grenser» | Hvor lenge en konto kan stå uverifisert, og hvor lenge en verifikasjon gjelder |
| Kampanje | «Lagre grense» → «Lagre avsender» | Frekvenstak og avsender |
| Rekruttering | Fanen «Profiler» → «Ny profil» | En stillingsprofil med vektede krav |
| Kontrollregister | «Registrer kontroll» | Kravreferanse, eier og hvor ofte den skal etterprøves |
| Likviditet | «Lagre grensene» → «Registrer modell» | Horisont, grunnlagsalder og målefrist, så en modell |
| Nettside og innhold | «Lagre grenser» → «Lagre kilde» | Hvor lenge en kilde gjelder, og varselfristen |
| Anbud | «Lagre søkeprofil» | Bransjekoder, geografi og verdispenn — tenantens egne |
| Myndighetsrapportering | «Lagre varselfrister» → «Registrer regelverk» | Varsel før frist, eskalering og regelverksvarsel |
| Motparter | «Lagre policy» → «Registrer motpart» | Hvor lenge en vurdering gjelder, og taket for forslag |
| Sanksjonskontroll | «Lagre policy» → «Registrer listeversjon» | Matchterskel og frister — kunden velger selv |
| Tollklassifisering | «Lagre terskler» → «Lagre regelverk» | Sikkerhetsterskelen — kunden velger selv |
| EHF-kontroll | «Lagre terskler» → «Lagre regelsett» | Utløpsvarsel og avviksfrist |
| Personvern | «Registrer forespørsel» | Hvem som spør, hva slags forespørsel, når den kom, og hvilke lagre den gjelder |
| Postjournal | «Lagre grenser» → «Registrer kilde» | Varselfrister, så kilden journalpostene hentes fra |
| HMS og avvik | «Lagre grensene» → «Meld avviket» | Varselfrister og regelverket avvikene meldes mot |
| Tilganger | «Registrer objekt» → «Registrer tilgang» | Systemet, så hvem som har tilgang, med eier, hjemmel og hvor ofte den skal gjennomgås |
| Tilskudd | «Lagre terskler» → «Aktiver ordning» | Varselfrister, så hvilke ordninger dere følger |
| Dokumentmaler | «Opprett malfamilie» → «Publiser versjonen» | En familie, en versjon med tekst og felt, og publisering før den kan fylles ut |
| E-post | «Koble til M365» | En postboks. Kobles den til med kun lesetilgang, kan man ikke svare herfra. |

**En ting som overrasker:** skriveknappene vises bare for brukere som
har rett til å opprette. En ren leser ser listene, men ikke skjemaene —
og tror da at modulen er tom. Sjekk rollen først når noen sier «det står
ingenting her».

---

## 7. Slik kommer en ny kunde i gang

1. **Registrer firmaet** på forsiden. Utvidelsene velges her, og for et
   enkeltpersonforetak kan de ikke utvides etterpå.
2. **Sett kravet i hver modul dere skal bruke.** Dette er det eneste
   steget som krever at noen tenker. Tallene er deres egne, ikke bransjens.
3. **Legg inn grunndataene** — varer, leverandører, prosjekter, ansatte,
   alt etter hvilke moduler dere bruker.
4. **Vent én natt.** Første sveip gir det første bildet, og det pleier å
   være ubehagelig ærlig. Det er meningen.
5. **Behandle funnene, og se at de lukker seg selv** når årsaken er borte.

---

## 8. De vanlige misforståelsene

**«Systemet gjorde ingenting i natt.»** Sjekk om grensene er satt. En
modul uten terskel har ingen mening om noe, og sier fra med funnet «Ingen
grenser satt».

**«Det står ingenting her.»** Sjekk rollen. Uten rett til å opprette ser
brukeren listene, men ingen skjemaer.

**«Funnet forsvant ikke da jeg fikset det.»** Sveipen går én gang i
døgnet. Funnet lukkes neste natt.

**«Det kom 40 funn første natt.»** Det er riktig. Første sveip ser
etterslepet som har bygget seg opp, ikke bare det som skjedde i går.

**«Kan den ikke bare bestille varen selv?»** Ikke i dag, og ikke uten en
fullmakt noen har gitt bevisst. Det er en beslutning, ikke en oppgave.

---

## 9. Hva som ennå ikke er der

28 av 57 moduler er ikke sertifisert ennå. **En modul som ikke står i
tabellen over, er ikke i drift** — uansett hva katalogen på nettsiden
lover om den.

Modulkatalogen i grensesnittet merker hver modul som **i drift**,
**klargjort**, **bygges** eller **planlagt**, og merkingen er avlest fra
modulregisteret, ikke skrevet av en selger. Bruk den i salgssamtalen:
den er det ærligste bildet som finnes, og den er alltid oppdatert.
