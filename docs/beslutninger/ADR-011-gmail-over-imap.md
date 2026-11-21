# ADR-011: Vanlig Gmail kobles til over IMAP med app-passord

**Status:** vedtatt 2026-11-21 av eier («gmail via imap»). Gjennomført i
migrasjon 229 og `plan/epost_imap.py`.

## Problemet

Kundetypen er små bedrifter, og mange av dem deler én VANLIG Gmail-konto —
ikke Google Workspace, ikke Microsoft 365. M-6 kjente bare Microsoft.

Googles egen innlogging (OAuth) er ikke en vei vi kan åpne for fremmede
kunder i dag: å lese Gmail er et «restricted scope», som krever at appen
verifiseres og gjennomgår en årlig sikkerhetsvurdering hos en godkjent
tredjepart. I testmodus kan bare kontoer vi selv fører opp koble til, og
tilkoblingen utløper etter sju dager.

## Vedtaket

En kilde kan ha leverandøren `imap`. Kunden oppgir Gmail-adressen og et
**app-passord** hun lager hos Google. Plattformen leser innboksen over IMAP
og svarer over SMTP fra samme adresse.

- **Vertene er faste** (`imap.gmail.com:993`, `smtp.gmail.com:465`). Kunden
  oppgir aldri en server, så det finnes ingen vei til å få plattformen til
  å koble seg mot en vert hun har valgt.
- **Bare app-passord tas imot** (16 bokstaver). Et vanlig kontopassord
  avvises før det når noe sted.
- **Innloggingen prøves ved tilkobling**, før noe lagres.
- **Passordet lagres kryptert** med tenantens DEK, i samme felt som
  Microsoft-tokenet, og returneres aldri. Bare innhenterens rolle kan lese
  det.
- **Første runde starter fra nå.** Ingenting eldre enn tilkoblingen hentes.
- **Et svar går aldri to ganger.** Et brudd FØR serveren har tatt imot
  meldingen prøves igjen; en usikker feil underveis settes som feilet og
  overlates til et menneske.

## Microsoft-veien er urørt

`plan/epost.py` står i M-6s bevisrot og er ikke endret. De to veiene har
hver sine kandidatdører; `m6_hentekandidater` og `m6_sendekandidater` fikk
ett ledd til (`leverandor = 'm365'`), så en IMAP-kilde aldri havner i en
runde som ville forsøkt et Microsoft-token på den.

## Det som mangler, og sies

- **Sletting i Gmail speiles ikke.** Microsoft-veien gjør det (210).
- **Bare Gmail.** Andre leverandører (one.com, Domeneshop) er samme
  mekanisme med andre faste verter, og legges til i `TJENESTER`.
- **Ikke prøvd mot en ekte Gmail-konto av meg.** Testene bruker en
  etterlignet postboks. Serveren når begge Gmail-portene (målt 21.11.2026).
- **Et app-passord gir tilgang til HELE kontoen**, ikke bare innboksen —
  det er Googles modell, ikke vår. Kunden kan trekke det tilbake hos Google
  når som helst.

## Angre

Deaktiver kilden under E-post. `git revert` av PR-en; migrasjonen kan stå —
uten IMAP-kilder oppfører dørene seg som før.
