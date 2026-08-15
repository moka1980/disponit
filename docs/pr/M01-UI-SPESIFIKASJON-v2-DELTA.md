# M-1 UI-SPESIFIKASJON v2 — DELTA (seks P1 → GO)

**Draft: Claude.ai · v1 (IA, fire flater, read-only policy, telling-ikke-KPI)
består. Reviewens anbefalte løsninger vedtatt direkte. Prototypen forblir
visuell skisse — IKKE bevis på WCAG eller komponentferdighet.**

## 1. Unntak er READ-ONLY i UI v1 — «Behandle sak» ut av scope

En generell status-knapp kan omgå M-37-maskinen. v1-beslutning:
- UI v1 VISER unntak (metadata, begrunnelseskjede, historikk) — behandler
  dem ikke. «Behandle sak»-knappen fjernes fra implementeringsscope OG
  e2e-kriteriet.
- Manuell behandling fra UI blir egen PR-kjede med egen backend-protokoll:
  serveren returnerer `tillatte_handlinger[]` per sak (lukket liste, aldri
  klientutledet fra status/rolle), hver handling med idempotensnøkkel og
  saksversjon/ETag; konflikt → ny lasting, aldri blind retry. Terminal
  `manuell` gjenåpnes ikke fra UI.
- e2e-kriteriet (§8.5) endres: «tenant logger inn, ser policy, ser en
  beslutning i loggen, åpner et unntak og ser begrunnelse+historikk» —
  behandling utgår til egen PR.

## 2. §5 blir eksakt API-kontrakt (eller eksplisitt backend-avhengighet)

v1s «revisjonslogg-visning» var beskrivelse, ikke kontrakt. Rettet: hver
flate spesifiserer metode, sti, scope, parametre, responsfelter+enums,
cursor-paginering, sorteringsorden, feilmodell+HTTP-status, maks side/filter.

**Sannhetssjekk mot main:** `GET /v1/unntak` FINNES (metadata, cursor).
De øvrige lese-endepunktene UI trenger FINNES IKKE ennå og listes som
EKSPLISITTE BACKEND-AVHENGIGHETER (egen liten API-PR før frontend):
- `GET /v1/beslutninger?cursor=&limit=&filter=` (scope `decisions:read`) —
  revisjonslogg-lesning, cursor-paginert, `limit` ≤ 100, sortering
  `ts DESC`, responsfelter: `{ts, handling, beslutning[enum: TILLAT|STOPP|
  UNNTAK], begrunnelse[kodeliste], policy_versjon, utfrt_status?}`.
- `GET /v1/oversikt` (scope `decisions:read`) — serverberegnet døgnaggregat
  `{vindu_start, vindu_slutt, tidssone, tillatt, stoppet, unntak, totalt}`.
- `GET /v1/policy/aktiv` (scope `policy:read`) — aktiv policy i
  menneskelesbar, allerede validert struktur.
v1s selvmotsigelse («kun eksisterende endepunkter») rettes: UI avhenger av
disse tre nye + eksisterende `GET /v1/unntak`. Backend-PR-en spesifiseres
mot API-porten som PR-005b.

## 3. Tenantbytte er en sikkerhetsgrense, ikke navigasjon

v1s tenant-nedtrekk fjernes fra v1. Beslutning:
- v1 viser tenantnavnet som TEKST (ingen nedtrekk) — sesjonen er bundet
  til én tenant server-side.
- Multi-workspace-bytte blir egen kontrakt NÅR det trengs: server utsteder
  tenantbundet sesjon/token, returnerer listen brukeren faktisk kan åpne,
  klienten sender aldri ønsket tenant som autoritet, og ALL lokal state
  (cache, pågående kall, UI-state) tømmes ved bytte — ingen lekkasje via
  URL, browser-cache eller telemetry.

## 4. XSS + dataminimering i frontend (bindende)

Prototypens `innerHTML` var trygt kun fordi data er syntetisk. Bindende
for implementering:
- API-data rendres med tekstnoder / rammeverkets standard-escaping. INGEN
  ufiltrert `innerHTML` på API-data.
- INGEN HTML i locale-strenger (de er tekst, ikke markup).
- Content-Security-Policy uten inline-script i produksjon.
- ALDRI i DOM/URL/browserlager/logg/analytics: payload, attestasjoner,
  tokens, nøkler, sensitive begrunnelser.
- Rå maskinkoder KAN vises (support-sporet), men escapes.

## 5. Beslutning ≠ utførelse i UI (retter min v1-feil)

v1 lot badgen «Stoppet» dekke både ekte STOPP og M-37-unntak — feil, samme
familie som PR-006/007-funnene. Rettet:
- Beslutningsdetalj viser SEPARATE felt: `policybeslutning`
  (TILLAT|STOPP|UNNTAK), beslutningstidspunkt + policyversjon,
  utførelsesstatus (hvis relevant — TILLAT betyr tillatt, ikke utført),
  kvitteringsstatus, unntaks-/sikkerhetsstatus.
- `beslutning.STOPP` brukes KUN for maskinkoden som betyr stopp. Et
  UNNTAK er `beslutning.UNNTAK` med sin kategori (manglende_data,
  teknisk_feil, over_grense …) — aldri fremstilt som «stoppet av policy»
  når det egentlig venter på data eller behandling.
- `BeslutningBadge` får tre distinkte tilstander (finnes), MEN
  unntaks-badgen lenker til kategorien, og TILLAT-badgen sier eksplisitt
  «tillatt» ikke «utført».

## 6. Tilgjengelighet: prototypen er skisse, komponentene bygges portert

Reviewens konkrete avvik tas som byggekrav (prototypen forblir skisse):
- Klikkbare rader → knapp/lenke i cellen (fokuserbar, tastaturaktiverbar),
  ikke `<tr onclick>`.
- Drawer → ekte dialog: bakgrunn `inert`, fokusfelle innen dialogen,
  fokusretur ved lukk (ESC + klikk-utenfor finnes alt).
- `sr-only`-klassen defineres (manglet).
- Hver tabell `<caption>`.
- Språkvelger: enten funksjonell eller fjernet i v1 (ikke fake-interaktiv).
- `aria-sort` + tastatursortering faktisk implementert der lovet.
- Ingen inline-stil/-tekst i produksjonsform — alt via tokens.css + locale.
- Responsiv: definert oppførsel < `--bp-narrow` (nav kollapser, topplinje
  brytes, tabeller blir kort-lister). 200 % zoom testes.

## 7. Ni fundamentkomponenter lagt til biblioteket (§3 utvidet)

Reviewens liste, vedtatt — disse er dyre å ettermontere når 40+ moduler
har arvet settet:

| Komponent | Rolle |
|---|---|
| `SideStatus` | Felles loading/tom/feil/uautorisert/stale (samler §4-tilstandene i én kontrakt) |
| `TilgangsVakt` | Viser kun server-returnerte `tillatte_handlinger`/capabilities; aldri klientutledet rolle |
| `CursorNavigasjon` | Cursor-paginering for logg/kø — aldri ubegrensede tabeller |
| `VarselBanner` | Sesjonsutløp, policyendring, stale data, sikkerhetsstopp |
| `LiveRegion` | `aria-live` for lasting/lagring/feil |
| `Tidspunkt` | Konsekvent tidssone + absolutt tid + lokalisert format (fra locale) |
| `KodeForklaring` | Oversatt tekst + råkode + trygg fallback for ukjent kode |
| `SensitiveData` | Maskering/redaksjon + eksplisitt «ikke tilgjengelig» |
| `HandlingDialog` | Server-definert handling, konsekvens, idempotens, konflikt (for den fremtidige behandlings-PR-en) |

`KodeForklaring`-fallbacken er viktig: en ukjent kode fra en nyere motor
skal vise råkoden trygt, ikke krasje eller vise tom streng (lukket format,
samme prinsipp som resten av systemet).

## 8. Rekkefølge (revidert)

1. Backend-avhengighets-PR: de tre nye lese-endepunktene (§2) mot API-porten.
2. UI-tokens som mangler → tokens.css (skygge/z-index/breakpoint + `sr-only`).
3. Komponentbibliotek (v1 §3 + de ni §7) mot tokens, axe-core i CI.
4. Fire flater, hver med alle fem tilstander via `SideStatus`.
5. Kobles mot §2-endepunktene når de + lag-1-drift er på staging; mock som
   speiler kontrakten til da.
6. e2e (§1-revidert): logg inn → policy → beslutning i logg → åpne unntak,
   se begrunnelse+historikk. Behandling er egen senere PR.

## Ingen åpne spørsmål
v1s tre er besvart av reviewen (read-only policy ✓, døgntelling ✓ med fast
vindu+tidssone+ingen trend, ni komponenter ✓). Dette deltaet lukker de
seks P1. Klar for kort bekreftelse.
