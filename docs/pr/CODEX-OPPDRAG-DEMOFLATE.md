# OPPDRAG TIL CODEX — Demoflate for investorer og pilotkunder

**Type:** frittstående prototypeoppdrag, IKKE en modul i 45-katalogen.
**Branch:** `demo-flate-investor`.
**Formål:** vise Disponit til investorer og pilotkunder før plattformen er ferdig.
**Rolle:** Codex bygger. Denne gangen er Codex implementerer, ikke reviewer.

---

## 0. Hva dette er — og hva det ikke er

Dette er en **demoflate**: en klikkbar, ærlig visning av hvordan Disponit ser ut
og henger sammen når det er ferdig. Ingenting trenger å virke. Ingen backend,
ingen database, ingen innlogging.

Det er **ikke** en ny versjon av kundeflaten. Den ekte kundeflaten (M-1) lever i
`platform/core/ui/static/` og er i produksjon. **Rør den ikke.**

### Den ene regelen som betyr mest

Flaten skal vises til **investorer**. Da er det avgjørende at den ikke påstår at
noe er ferdig som ikke er det. En demo som viser «45 moduler aktive» når tre er
bygget, er en usann påstand til folk som vurderer å investere penger — og den
faller sammen i første tekniske due diligence.

Løsningen er ikke å skjule at plattformen er tidlig. Løsningen er å **vise
utbyggingsplanen som produktets styrke**: 45 spesifiserte moduler, fire faser,
tre i drift, resten med dato og avhengigheter. Det er en mer overbevisende
historie enn en falsk fullført plattform, og den holder i et rom med folk som
har sett hundre pitcher.

Derfor:

- Hver modul viser **ekte status**: `i drift`, `bygges`, `planlagt`.
- Statuslinja teller **ekte tall**: «3 av 45 moduler i drift».
- Demodata merkes **synlig** som demodata. Ikke med liten grå tekst i bunnen —
  et vedvarende bånd eller en pill som alltid er synlig.
- Ingen oppdiktede kundenavn som ligner ekte selskaper. Bruk åpenbart fiktive:
  «Nordvik Regnskap AS», «Bjørkli Elektro».
- Ingen tall som utgir seg for å være målte resultater. «Spart 340 timer» er en
  påstand — enten merkes den som illustrasjon, eller så er den ute.

Alt annet i dette dokumentet er detaljer. Dette er kontrakten.

---

## 1. Fire flater

Én applikasjon, fire visninger, samme skall.

| # | Flate | Hvem | Jobb |
|---|---|---|---|
| 1 | **Forside** | førstegangsbesøkende | Hva er Disponit, hvorfor er det annerledes, hva er status |
| 2 | **Kundeflate** | ansatt hos pilotkunde | Daglig dashboard: hva skjedde, hva krever meg |
| 3 | **Kundeadmin** | administrator hos kunden | Egne brukere, roller, policy, integrasjoner, egne moduler |
| 4 | **Eier-/rotpanel** | deg | Alle tenanter, modulaktivering, utrulling, revisjonslogg |

Bytte mellom flatene skal være **åpenbart** — dette er et demonstrasjonsverktøy,
ikke et produkt med skjult admin. En tydelig velger øverst er riktig.

---

## 2. Layout (fra oppdragsgiver, bygger på prototype v5–v7)

```
┌──────────────────────────────────────────────────────────────┐
│ Skip-to-content · Hovednavigasjon (5–7) · Søk                │
├───────────┬──────────────────────────────────┬───────────────┤
│ Modulmeny │ Dashboard                        │ Kontekstpanel │
│ gruppert  │  KPI-kort                        │ detaljer om   │
│ etter     │  prioriterte varsler             │ valgt modul   │
│ avdeling  │  siste aktiviteter               │               │
│ (kan      │                                  │               │
│  skjules) │                                  │               │
├───────────┴──────────────────────────────────┴───────────────┤
│ 3 av 45 moduler i drift · 2 varsler · Sist synkronisert 09:42 │
└──────────────────────────────────────────────────────────────┘
```

**Responsivt:** under `--bp-smal` (720px) blir modulmenyen en skuff og
kontekstpanelet legger seg under dashbordet. Tokenet finnes allerede
(`--bp-smal`, `--skygge-skuff`, `--teppe`, `--z-skuff`) — bruk det.

---

## 3. Innholdet finnes allerede — ikke dikt det opp

### 3.1 Modulkatalogen (45 moduler)

Ligger som **strukturert JS-data** i
`docs/spesifikasjon/disponit-prototype-v7.html`, som `const M=[...]`.

Feltene per modul: `n` (nummer), `name`, `area` (avdeling), `p` (fase 1–4),
`level` (Lett/Medium/Avansert/Strategisk), `goal`, `flow[]`, `input`, `output`,
`int` (integrasjoner), `guard` (sikkerhetsgrenser), `kpi`, `accept`
(akseptansekriterier), `dep` (avhengigheter).

Hent den ut programmatisk. Ikke skriv den av for hånd — 45 moduler avskrevet
manuelt blir 45 sjanser til å innføre en feil, og katalogen er **frosset på 45**
(v7 §«Katalogen fryses på 45»), så den skal stemme eksakt.

**De 11 avdelingene** (dette er grupperingen i venstremenyen):

| Avdeling | Moduler | Antall |
|---|---|---|
| Plattform og sikkerhet | 1, 2, 29, 31, 37, 38 | 6 |
| Økonomi | 13, 14, 15, 23, 39, 41, 42 | 7 |
| Kunde og salg | 17, 18, 19, 25, 26, 43 | 6 |
| IT og drift | 10, 11, 12, 22, 35 | 5 |
| Analyse og ledelse | 16, 33, 36, 45 | 4 |
| Juridisk og compliance | 21, 30, 32, 34 | 4 |
| Data og kunnskap | 3, 4, 9 | 3 |
| Innkjøp og logistikk | 24, 27, 28 | 3 |
| Samarbeid og HR | 7, 8, 40 | 3 |
| Dokument og kommunikasjon | 5, 6 | 2 |
| Markedsføring | 20, 44 | 2 |

### 3.2 Ekte status per 15. august 2026

Utledet av hva som faktisk finnes i `platform/modules/` og i `main`:

| Modul | Navn | Status | Grunnlag |
|---|---|---|---|
| M-1 | Policy- og fullmaktsmotor | **i drift** | `platform/modules/m01_policy`, PR-013 policyadmin + PR-014 policyeditor merget |
| M-2 | Revisjonslogg og evidens | **i drift** | `platform/modules/m02_revisjonslogg` |
| M-37 | Unntaks- og feilhåndteringsagent | **i drift** | `platform/modules/m37_unntak`, PR-012 merget |
| M-38 | Kapasitets-, kø- og modellruter | **bygges** | delvis dekket av PR-014a modulregister + PR-015 operativt lag |
| øvrige 41 | | **planlagt** | ingen kode |

Hardkod ikke disse i markup. Legg dem i **ett datastruktur-objekt** slik at det
er én linje å endre når M-3 blir ferdig. Det er nettopp det oppdragsgiver ber om
med «aktiveres hver gang en modul er ferdig og testet».

### 3.3 Fire faser

v7 §«Fire faser — lette moduler først». Fase 1 er `Lett`-modulene, og ingen ny
fase starter før alle moduler i forrige fase består akseptansekriterier i
produksjon hos pilotkunder. **Vis fasene** — de er utbyggingsplanen, og det er
den investorer skal kjøpe.

### 3.4 Forsidens argumenter

Hent fra v7, ikke fra fantasien. Overskriftene som allerede finnes:

- «Definisjon av *helautomatisert*» — den presise definisjonen er
  differensieringen, ikke et buzzword.
- «Felles plattform — bygges én gang»
- «Fire faser — lette moduler først»
- «WCAG 2.1 AA — plattformkrav, ikke etterarbeid»
- «Plattformen driver sin egen bedrift» — dette er det sterkeste beviset dere
  har: plattformen brukes på seg selv.
- «Ingen endring går rett i produksjon — noensinne»
- «Ærlig kostnadspåstand»: *ingen obligatorisk kostnad per LLM-token; lokale
  modeller er standard*. Integrasjoner har egne kostnader.

Den siste er verdt en egen plass på forsiden. «Ærlig kostnadspåstand» er en
uvanlig ting å lede med, og det gjør den troverdig.

---

## 4. Design — systemet finnes, ikke lag et nytt

`design/tokens.css` er **eneste kilde til utseende i hele plattformen**
(docs/RUTINER.md pkt. 6). Komponenter definerer **aldri** egne farger, fonter
eller avstander.

Tokens som allerede er definert og skal brukes:

```
Flater:      --paper #F6F8F7   --card #FFFFFF   --line #D5DEDD
Tekst:       --ink #14232B     --muted #5A6E77
Semantikk:   --auto #0F7A5C / --auto-bg #E8F3EE     (automatisert, ok)
             --guard #A05A00 / --guard-bg #FBF2E3   (policy, stopp)
             --danger #9A2B2B / --danger-bg #F8E9E9
             --info #1D6FA5 / --info-bg #E5EFF6
Type:        --font-sans (Inter/system)   --font-mono
Form:        --radius 8px  --sp-1..--sp-8  --skygge  --focus
Lag:         --z-innhold/-topplinje/-overlegg/-skuff  --bp-smal 720px
```

**Statusfargene faller ut av semantikken som allerede finnes:** `i drift` er
`--auto` (det systemet gjør av seg selv), `bygges` er `--info`, `planlagt` er
`--muted`. Ikke innfør en ny farge for dette.

**Mangler et token du trenger?** Legg det i `tokens.css`, ikke inline i en
komponent. Det er regelen, og den er grunnen til at flaten henger sammen.

**Farge alene bærer aldri betydning** (WCAG 1.4.1). En statuspill må ha tekst
eller form i tillegg til farge.

---

## 5. Hvor koden skal ligge

```
prototype/disponit-flate-demo.html     ← her
```

`prototype/` inneholder allerede v5-, v6- og v7-prototypene, og
`platform/core/ui/server.py` serverer **kun** `static/` («Ingen andre kataloger
er nåbare»). Demoen kan derfor ikke lekke inn i produksjonsflaten ved et uhell.
Det er hele grunnen til at den skal ligge der.

**Selvstendig fil.** Ingen byggesteg, ingen CDN, ingen npm. Skal kunne åpnes med
dobbeltklikk og sendes som vedlegg til en investor. Inline CSS/JS; kopier
tokens inn i `<style>` med en kommentar om at `design/tokens.css` er kilden.

---

## 6. WCAG 2.1 AA — plattformkrav, ikke etterarbeid

Dette er ikke en demo-unntakssone. v7 sier det rett ut, og dere selger
tilgjengelighet (`uu-sjekk`/WCAG er et søsterprodukt) — en utilgjengelig demo
ville vært pinlig i nøyaktig det rommet dere skal inn i.

Minimum:

- Skip-to-content som faktisk virker (den står i layouten av en grunn).
- Tastaturnavigasjon gjennom hele flaten; synlig fokus (`--focus`) på alt
  interaktivt.
- Modulmenyen: ekte `<nav>` med `<ul>`, ikke divs. Skuffen skal kunne lukkes med
  Escape og returnere fokus dit den kom fra.
- Kontrast ≥ 4.5:1 på all tekst — tokenene er valgt for å holde dette, så ikke
  finn på egne mellomtoner.
- `prefers-reduced-motion` respekteres (allerede i `tokens.css`).
- Ett `<h1>` per flate, deretter uhoppet overskriftsnivå.
- Statuslinja er `aria-live="polite"`, ikke en stum div.

---

## 7. Akseptansekriterier

Codex leverer først når alle disse er sanne. Dette er porten.

1. **Katalogen stemmer:** 45 moduler, hentet programmatisk fra v7, fordelt på de
   11 avdelingene med nøyaktig antallene i tabellen over.
2. **Status er ærlig:** tre moduler `i drift`, én `bygges`, 41 `planlagt`.
   Statuslinja viser «3 av 45». Ingen visning påstår noe annet noe sted.
3. **Ett sted å endre status:** å sette M-3 til `i drift` er én linje, og slår
   gjennom i menyen, KPI-kortene, statuslinja og kontekstpanelet samtidig.
4. **Demodata er merket** med et vedvarende, synlig element på alle fire flater.
5. **Alle fire flater** finnes og er nåbare fra hverandre uten å redigere URL-en.
6. **Layouten stemmer** med §2, inkludert at modulmenyen kan skjules og at
   kontekstpanelet viser den valgte modulen.
7. **Ingen egne farger:** `grep` etter hex-koder utenfor `:root` gir null treff.
8. **Tastatur alene** kommer gjennom hele flaten, med synlig fokus hele veien.
9. **Filen åpner frittstående** fra `file://` uten nettverk og uten konsollfeil.
10. **`platform/core/ui/static/` er urørt** — `git diff` viser ingen endring der.

---

## 8. Det som IKKE er med i dette oppdraget

- Ingen backend, API, autentisering eller database.
- Ingen endring i `platform/`, `policies/`, `deploy/` eller migrasjoner.
- Ingen ny modul i katalogen — de 45 er frosset.
- Ingen endring i den ekte kundeflaten.

Om noe av dette virker nødvendig for å komme i mål, er det et signal om at
oppdraget er misforstått. Spør heller enn å utvide.

---

## 9. Til slutt — hva demoen skal få en investor til å tenke

Ikke «dette er ferdig». Det er den ikke, og det gjennomskues.

Men: «disse folkene vet nøyaktig hva de bygger, i hvilken rekkefølge, med
hvilke sikkerhetsgrenser — og de tre modulene som er ferdige, er ferdige på
ordentlig, med revisjonslogg og akseptansekriterier som består i produksjon.»

Presisjonen **er** pitchen. Bygg flaten så den viser presisjonen.
