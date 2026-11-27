# Juridiske tekster: UTKAST

**Status: utkast 27.11.2026, eiers opplysninger fylt inn. Ikke publisert, og ingen kunde har godtatt noe.**

De tre dokumentene må gjennomgås av eier, og helst av en jurist, før de
publiseres og registreringen begynner å kreve at de godtas.

| Fil | Hva | Hvem den gjelder |
|---|---|---|
| `vilkar.md` | Bruksvilkår for tjenesten | Firmaet som registrerer seg (kunden) |
| `personvern.md` | Personvernerklæring | Brukerne som logger inn, og alle som vil vite hva vi gjør med data |
| `databehandleravtale.md` | Databehandleravtale (GDPR art. 28) | Kunden er behandlingsansvarlig for sine kunders og ansattes data, og Disponit er databehandler |

## Fylt inn av eier 27.11.2026

- Leverandør: Disponit, 2007 Kjeller. **Org.nr. mangler** — se under.
- Kontakt: eliassi@gmail.com (også `DISPONIT_KONTAKT_EPOST` på verten).
- Vert og e-post: one.com. Serveren (81.88.19.25) er registrert på
  dogado GmbH i one.com-konsernet, Dortmund, **Tyskland (EØS)** — slått
  opp i RIPE 27.11.
- Pris: avtales individuelt.
- Ansvarsgrense: forslag (§ 9) — det kunden har betalt siste 12 måneder,
  null i prøveperioden, ikke ved grov uaktsomhet/forsett.
- Verneting: Lillestrøm tingrett.

## Før publisering

1. **Organisasjonsnummer.** «Disponit» uten org.nr. er ikke en juridisk
   person — avtalepart blir da personen bak. Registrer et
   enkeltpersonforetak eller AS i Brønnøysundregistrene, og sett inn
   navn og nummer.
2. Helst en gjennomlesning av jurist.

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
