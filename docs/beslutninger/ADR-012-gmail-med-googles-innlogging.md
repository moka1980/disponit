# ADR-012: Gmail kobles til med Googles egen innlogging

**Dato:** 25.11.2026 · **Status:** vedtatt (eier) · **Utfyller:** ADR-011

## Bakgrunn

ADR-011 valgte IMAP med app-passord for vanlig Gmail, fordi Googles
innlogging krever en godkjenning hos Google før fremmede kunder kan bruke
den. Første ekte forsøk (eier, testkonto, 25.11) stoppet på app-passordet:
siden hos Google lot seg ikke åpne. Eiers vurdering: telefoner og nyere
e-postprogrammer bruker innlogging, og nesten ingen bruker app-passord.

## Vedtak

1. **Googles innlogging er hovedveien.** Kunden skriver adressen, sendes
   til Google, logger inn og godtar. Samme flyt som Microsoft-koblingen:
   kode + PKCE, MAC-et engangsstate, browserbinding, samme callback.
2. **App-passordet står som reserve**, foldet sammen under knappen.
3. **Kilden er fortsatt en IMAP-kilde** (229). Det som ligger kryptert er
   et refresh-token i stedet for et app-passord; innhenteren veksler det
   til et kortlivet token per runde og logger inn med XOAUTH2. Ingen
   migrasjon, ingen nye dører i basen.
4. **Ett scope:** `https://mail.google.com/`. Det finnes ikke et smalere
   som virker over IMAP og SMTP.
5. **Postboksen bevises før noe lagres:** callbacken logger inn over IMAP
   for adressen kunden skrev. Logget hun inn med en annen konto, sier Gmail
   nei, og ingen kilde skrives.
6. **To slags nei skilles i runden:** Google avviser tokenet (400/401) →
   kilden `feilet`, kunden kobler til på nytt. Google svarer ikke, eller
   innloggingen er ikke satt opp på verten → drift, kilden står urørt.

## Det som IKKE er løst av koden

Scopet er i Googles klasse «begrenset». Det betyr:

- **Testmodus** (før Googles godkjenning): bare Google-kontoer som er lagt
  inn som testbrukere i Google Cloud-prosjektet kan koble til, høyst 100,
  og tilkoblingen utløper etter sju døgn og må gjøres på nytt.
- **For fremmede kunder** kreves Googles verifisering av appen og en
  sikkerhetsgjennomgang hos en godkjent tredjepart, som fornyes årlig og
  koster penger. Det er eiers beslutning når den bestilles.

Inntil da er innloggingen egnet for egne testkontoer, og app-passordet er
det en fremmed kunde faktisk kan bruke.

## Oppsett (eier, én gang)

1. Google Cloud Console → nytt prosjekt → slå på «Gmail API».
2. Samtykkeskjerm: type «Ekstern», legg til scopet
   `https://mail.google.com/`, legg testkontoene inn som testbrukere.
3. Legitimasjon → OAuth-klient-ID → «Nettapplikasjon», med
   omdirigerings-URI `https://disponit.com/v1/epost/kilder/callback`.
4. Legg `DISPONIT_GOOGLE_CLIENT_ID` og `DISPONIT_GOOGLE_CLIENT_SECRET` i
   miljøfila på verten og kjør deploy på nytt.

Uten punkt 4 svarer knappen at innloggingen ikke er satt opp, og åpner
app-passordet.

## Ikke prøvd

Runden mot ekte Google er ikke kjørt i test: kodevekslingen, fornyelsen og
Gmail er etterligninger. Første ekte tilkobling er prøven.
