# ADR-008: Playbook-katalogen for kontinuitet

**Status:** vedtatt 2026-11-20 (Claude Code, med eiers fullmakt «du kan
bestemme arkitektavklaringen»).
**Gjelder:** M-35 (krise og kontinuitet).
**Bygger på:** M-35-dom 3 (089): godkjenning er git-merge, og
tjenestekartet bærer `navn@sha256`.

## Problemet

089 bygget fire dører for tjenestekartet og beredskapskontaktene, ga
API-rollen rett til å kalle dem og tegnet begge tabellene i dashbordet.
Men ingen HTTP-vei kalte dørene, og dashbordet hadde ingen skjemaer. En
kunde så et tomt kart og kunne ikke fylle det.

Det som manglet for å bygge veien, var svaret på tre spørsmål dom 3 lot
stå åpne:

1. Hvor bor playbook-filene?
2. Hvem regner hashen i `navn@sha256`?
3. Hva skjer med kartet når en playbook endres?

## Dommen

**P1. Filene bor i `platform/core/playbooks/kontinuitet/<navn>.yaml`.** De
følger releasen. Det som er merget, er godkjent (dom 3), og det som
ligger i releasen, er det serveren tilbyr.

**P2. Formen er fast:** `navn`, `tittel`, `naar` og `steg` (1–20
setninger), og ingenting annet. Filnavnet er `<navn>.yaml`. En fil som
ikke holder formen, feller lastingen av hele katalogen.

**P3. Serveren regner hashen, av filens bytes.** Klienten sender bare
navnet. API-et slår opp navnet i katalogen og setter sammen referansen.
En kropp som bærer en ferdig referanse, blir avvist. Slik kan et
kartinnslag aldri peke på en tekst som ikke finnes i releasen.

**P4. En endret playbook gjør bindingen «endret siden den ble valgt».**
Raden i kartet beholder den gamle hashen til kunden lagrer innslaget på
nytt. Dashbordet sier det som ord på raden. Ingen binding flyttes
automatisk: kunden valgte en bestemt tekst, og en ny tekst er et nytt
valg.

**P5. En kontakt må være et aktivt medlem av firmaet.** Fremmednøkkelen
i 089 peker på den globale brukertabellen, så døren godtar enhver bruker
på plattformen. API-et sjekker medlemskapet før det kaller døren.

**P6. Ingen migrasjon og ingen nye rettigheter.** Dørene, vaktene og
tilgangene fra 089 står urørt. Veien er fem ruter
(`platform/core/api/kontinuitet_kart.py`) bak de to scopene modulen alt
har.

## Det dommen ikke gjør

- Øvelsen måler ikke bindingen. En rad med utdatert playbook er synlig i
  dashbordet, men gir ikke et funn i øvelsesrapporten.
- Kunden kan ikke skrive egne playbooker. Katalogen er plattformens. Egne
  playbooker per firma krever en godkjenningsvei som ikke er git-merge,
  og det er en ny dom.
- Kontakter kan ikke fjernes. Identiteten er frosset i 089; en ny person
  i rollen er en ny kontakt med en annen prioritet.

## Følger for sertifiseringen

M-35 står aktiv. Ingen fil i bevisrotene er endret: suiteandelen
(`test_m35_kontinuitet.py`) og produsenten (`m35-lest.py`) er urørt, og
de nye testene står i `test_m35_kart.py`.
