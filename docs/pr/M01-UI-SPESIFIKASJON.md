# UI-SPESIFIKASJON — M-1 kundeflate + varig designfundament (til porten)

**Draft: Claude.ai · Første ekte kundegrensesnitt i Disponit. Blir malen
alle senere moduler arver — derfor spesifiseres den varige designen HER,
én gang. Bygget mot faktisk `design/tokens.css` og faktiske
motorkoder/`locales/nb.json` fra main.**

## 0. Hva dette er — og ikke er

- ER: den første kundevendte flaten. Lar en tenant logge inn, se og forstå
  sin policy, se beslutningsloggen (M-2), og behandle unntakskøen (M-37).
- ER: det varige designfundamentet — komponentbibliotek, navigasjon,
  tilstander — som M-13, M-14, M-17 osv. arver uendret.
- ER IKKE: det interne driftsbildet (lag 1, eget spor).
- ER IKKE: M-16 KPI-dashboards (kundevendte nøkkeltall, egen senere modul).
- ER IKKE: policy-REDIGERING i v1 — se §6 (les først, rediger som eget steg).

## 1. Ufravikelige fundamentregler (arves av alle moduler)

1. **Én designkilde.** Alt utseende fra `design/tokens.css`. Ingen komponent
   definerer egen farge/font/avstand (RUTINER pkt. 6). UI-spesifikke tokens
   som mangler (skygger, z-index, breakpoints) LEGGES i tokens.css, aldri
   inline.
2. **Ingen hardkodet tekst.** Alt via nøkkel fra `locales/`. Motorens
   maskinkoder ER nøklene (`beslutning.*`, `kode.*`, `unntak.*` finnes
   allerede). Nye UI-nøkler får prefiks `ui.` / `m01.`.
3. **WCAG 2.1 AA er en port, ikke en ambisjon.** axe-core i CI blokkerer
   merge (RUTINER pkt. 6). Konkret for hver komponent: semantisk HTML,
   `--focus` på ALT interaktivt, farge aldri eneste signal (alltid
   farge + ikon + tekst — som beslutnings-badgene under), 4.5:1 kontrast,
   200 % zoom, full tastaturnavigasjon, `aria-live` for async-status.
4. **Server er sannhet.** UI viser KUN det API-et returnerer. Ingen
   forretningslogikk i frontend — ingen policy-evaluering, ingen
   beslutning, ingen statusutledning klientside. Payload dekrypteres aldri
   i UI (M-37-payload er ikke engang tilgjengelig for lesing — kun metadata).
5. **Tenant-isolasjon synlig og reell.** Alt hentes med tenant-token;
   UI viser aldri data på tvers. Innlogget tenant vises alltid i topplinjen.
6. **Les-tungt, handlingsfattig.** M-1-flaten er overvåkning og forståelse,
   ikke kontroll. De få handlingene (behandle unntak) er eksplisitte,
   bekreftede og logget. Maks 3 primærhandlinger per skjerm (v7.2-prinsipp).

## 2. Informasjonsarkitektur (fire flater)

```
Disponit  [tenant: Acme AS ▾]                    [språk ▾] [logg ut]
──────────────────────────────────────────────────────────────────
│ Oversikt   Policy   Beslutninger   Unntak (3) │   ← global nav
──────────────────────────────────────────────────────────────────
```

- **Oversikt** — helsebilde: sist behandlede beslutninger (antall
  tillatt/stoppet/unntak siste døgn som TELLING, ikke KPI-graf), antall
  åpne unntak, policyversjon i bruk. Landingsside.
- **Policy** — les policyen som håndheves: roller, handlinger med grenser,
  vilkår, verifikatorer. Menneskelesbar visning av YAML-en, ikke råtekst.
- **Beslutninger** — revisjonsloggen (M-2), filtrerbar. Hver rad:
  tidspunkt, handling, beslutning-badge, begrunnelse (oversatte koder).
- **Unntak** — M-37-køen. Liste med status/kategori/prioritet; detaljpanel
  med begrunnelseskjede og historikk; behandling der policy tillater.

## 3. Komponentbibliotek (det varige settet)

Hver komponent er token-drevet, WCAG-portert, og gjenbrukes av alle moduler:

| Komponent | Rolle | Token/WCAG-notat |
|---|---|---|
| `AppShell` | Topplinje + global nav + tenant/språk/logg-ut | Landemerker `header/nav/main`; skip-lenke |
| `BeslutningBadge` | TILLAT/STOPP/UNNTAK | Farge (`--auto`/`--danger`/`--guard`) + ikon + tekst; aldri farge alene |
| `KategoriTag` | Unntakskategori | `--info-bg`; tekst fra `unntak.*` |
| `DataTabell` | Logg/kø-lister | `th scope`; sortering med `aria-sort`; tastaturnav; tom-tilstand |
| `Detaljpanel` | Beslutnings-/unntaksdetalj | `aria-live` ved lasting; fokusfelle-fri drawer |
| `BegrunnelseKjede` | Kodene som ledet til beslutning | Ordnet liste; hver kode oversatt + rå-kode i `--font-mono` for support |
| `StatusTidslinje` | Unntak-historikk (opprettet→claim→terminal) | Ikon+tekst per steg; ingen fargekoding alene |
| `Bekreftelsesdialog` | Før enhver behandling | Beskriver konsekvens; primær/avbryt; ESC + fokusretur |
| `TomTilstand` | Ingen data | Forklarende, ikke bare «ingen rader» |
| `Feiltilstand` | API-feil | Sier hva som gikk galt + hva brukeren kan gjøre (speiler M-1s egen feilfilosofi) |
| `Lasteskjelett` | Async | `aria-busy`; respekterer reduced-motion |

## 4. Skjermtilstander (obligatorisk for hver flate)

Hver flate spesifiserer ALLE fem — ikke bare «det fungerer»:
lasting (skjelett), innhold, tom, feil (med årsak+utvei), uautorisert
(token utløpt → re-innlogging). Dette er en port: en flate uten alle fem
er ikke ferdig.

## 5. Datakontrakt mot API-et (fra faktiske endepunkter)

UI kaller KUN eksisterende endepunkter:
- Oversikt/Beslutninger: leser fra revisjonslogg-visning (aggregat +
  liste). Hver rad har `beslutning`, `begrunnelse[koder]`, `handling`, `ts`.
- Unntak: `GET /v1/unntak?status=` (metadata only — aldri payload, jf.
  scope). Behandling: den definerte behandlingsveien (statusovergang med
  bekreftelse), som skriver historikk.
- Alt bak tenant-token; `EvaluationContext` bygges server-side (UI sender
  aldri tenant/rolle i body).
- **Ingen** UI-endepunkt returnerer dekryptert payload, secret, token
  eller nøkkel. Testbar port.

## 6. Policy-visning: les i v1, rediger senere (bevisst)

Å redigere policy er å endre en kundes fullmakter — det MÅ gå gjennom
samme utrullingsløype som kode (validering mot skjema v0.2, versjonering,
rollback — v7.2). Derfor: **v1 viser policy read-only**, menneskelesbart.
Redigering er egen PR-kjede med egen spesifikasjon (skjemavalidering i
UI, diff mot aktiv versjon, bekreftet aktivering). Å bygge redigering nå
ville omgå porten vi bygde policyregisteret for å håndheve.

## 7. Prototype vedlagt

En statisk, klikkbar HTML-prototype (`disponit-m01-ui-prototype.html`)
følger denne spesifikasjonen: samme tokens, samme koder, alle fire flater,
komponentene i §3, og tilstandene i §4. Den er ikke koblet til API —
data er syntetisk — men den viser NØYAKTIG hva som skal bygges, så
review skjer mot noe synlig, ikke bare tekst. Dette er lag-2s pendant til
run_synthetic: bevis før implementering.

## 8. Implementeringsvei (etter GO)

1. UI-tokens som mangler → `design/tokens.css` (skygge, z-index, breakpoint).
2. Komponentbibliotek §3 som frontend-modul mot tokens, med axe-core i CI.
3. Fire flater §2, hver med alle fem tilstander §4.
4. Kobles mot API først når lag 1 (staging-drift) kjører — til da mot
   en mock som speiler datakontrakten §5.
5. Sel/e2e: en tenant logger inn, ser policy, ser en beslutning i loggen,
   åpner et unntak, behandler det → historikk oppdatert. Det er «M-1
   fungerer» sett fra kunden.

## Spørsmål til ChatGPT

1. Er read-only-policy i v1 riktig grense, eller bør minst
   grense-justering (beløp) være redigerbar tidlig gitt at det er den
   vanligste kundeendringen?
2. Bør Oversikt vise telling (v1) eller er selv enkle døgn-tall å regne
   som KPI og dermed M-16-territorium vi skal holde oss unna?
3. Komponentbiblioteket blir malen for 40+ moduler — mangler settet i §3
   noe som er dyrt å ettermontere når det først er arvet bredt?
