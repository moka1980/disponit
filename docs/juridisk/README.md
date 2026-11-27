# Juridiske tekster: UTKAST

**Status: utkast skrevet 27.11.2026. Ikke publisert, og ingen kunde har godtatt noe.**

De tre dokumentene må gjennomgås av eier, og helst av en jurist, før de
publiseres og registreringen begynner å kreve at de godtas.

| Fil | Hva | Hvem den gjelder |
|---|---|---|
| `vilkar.md` | Bruksvilkår for tjenesten | Firmaet som registrerer seg (kunden) |
| `personvern.md` | Personvernerklæring | Brukerne som logger inn, og alle som vil vite hva vi gjør med data |
| `databehandleravtale.md` | Databehandleravtale (GDPR art. 28) | Kunden er behandlingsansvarlig for sine kunders og ansattes data, og Disponit er databehandler |

## Det eier må fylle inn (`[FYLL INN: …]` i tekstene)

1. **Juridisk enhet** som leverer tjenesten: navn, organisasjonsnummer og adresse.
2. **Kontaktadresse** for kunder og for personvernspørsmål. Samme adresse kan settes som
   `DISPONIT_KONTAKT_EPOST` på verten, så den også vises i prøveperiodebanneret.
3. **Vertsleverandør og land** for serveren (`disponit-srv`, OpenStack-sky). Bekreft
   leverandør og at dataene ligger i EØS.
4. **E-postleverandør** for utgående e-post fra plattformens egen adresse (SMTP).
5. **Pris og betaling** når betaling innføres. Vilkårene sier nå bare at prøveperioden
   er gratis og at videre bruk avtales.
6. **Ansvarsbegrensning**: beløpsgrensen i vilkårene § 9.

## Fakta som er hentet fra koden (endres koden, må teksten følge)

| Påstand i teksten | Hvor den bor |
|---|---|
| Prøveperioden er 30 dager | `api/firmaregistrering.py` `PROVE_DOGN` |
| Når prøveperioden er over, står agentene og e-posten stille; innlogging, lesing og eksport består | `api/firmastatus.py`, migrasjon 232 (#630) |
| Eksport av alle data som én fil, når som helst | `api/eksport.py` (#631) |
| E-post fra tilkoblede postbokser slettes etter 90 dager (kunden kan velge 30–365) | migrasjon 088, `slettefrist_dogn` |
| Etter oppsigelse: 30 dagers angrefrist, deretter destrueres firmaets nøkkel og dataene ryddes | migrasjon 190, `slettefrist_dogn` |
| En kundes kontaktpunkt slettes 90 dager etter at kunden er avviklet | migrasjon 184 |
| Data krypteres med en egen nøkkel per firma, og e-postadresser gjenkjennes via pseudonym | `db/kryptering.py`, migrasjon 183 |
| Ingen språkmodell (KI-modell) brukes på kundenes data | Eiers vedtak 27.11.2026 |
| Innlogging med Google eller Microsoft; ingen passord lagres hos oss | `api/sesjon.py` (OIDC) |

## Når tekstene er godkjent

Neste steg i koden er å publisere sidene (`/vilkar`, `/personvern`,
`/databehandleravtale`) og la registreringen kreve et kryss for vilkårene og
databehandleravtalen, lagret med versjon og tidspunkt på firmaet.
