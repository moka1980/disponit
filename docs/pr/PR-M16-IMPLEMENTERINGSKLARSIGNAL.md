# M-16 NØKKELTALL — IMPLEMENTERINGSKLARSIGNAL (GO)

**Til Claude Code · Konsolidert spesifikasjon (GO på M16-1–4) mot main
`5f2e233` / lesesvar 2026-08-21. Branch: `pr-XXX-m16-nokkeltall` —
PR-nummer settes ved branch. Stående porter SP-1…SP-13.
K1–K5-konvergensreglene gjelder sløyferundene.**

> **Ingen migrasjon.** v1 er ren lesing. Ingen nye indekser — terskelen
> for senere indeksarbeid er en målt spørring over 100 ms i prod, og da
> kommer indeksen som egen liten migrasjon med SP-10-kjøring, aldri i en
> flate-PR.

---

## 1. Grensen (produktkontrakten, testbar)

**M-16 viser hva Disponit faktisk gjorde. Den analyserer ikke.**

- Hvert tall er en telling over rader som finnes, eller en **radvis**
  differanse (én saks varighet). **Ingen divisjon i definerne** — ingen
  andel, gjennomsnitt eller median (statisk port).
- Ett datapunkt vises som ett punkt. Ingen glatting, interpolasjon,
  trendlinje eller prognose (statisk port).
- Tomme vinduer vises som eksplisitt null med teksten «ingen».
- **Ukjent enumverdi telles synlig som «ukjent» i totalen, aldri stille
  utenfor** — fail-closed for tellinger.

## 2. Kortene

| Kort | Kilde · tidsanker | Innhold |
|---|---|---|
| **Beslutninger** | `revisjonslogg` · `ts` | Partisjon per beslutning per vindu. **Domenet leses ordrett fra enum-CHECK-en** (prod viser TILLAT/STOPP/UNNTAK) — aldri antatt. Frekvensreservasjoner (`frekvens_hendelser`, `tidspunkt`) som eget tall i kortet |
| **Policyaktiveringer** | `policyaktivering` · `aktivert_ts` | Per vindu; partisjon per `aktiveringskilde`; partisjon per `pakrevd_antall`; rå tellinger 1 / 2 attestanter (`attestant_b IS NOT NULL`) — ingen andel |
| **Oppdrag** | `oppdrag` · `status_ts` | Partisjon per status per vindu |
| **Unntakskøen** | `unntak` · `ts` (opprettet), `status_ts` (lukket) | **To akser, aldri blandet:** aktivitet i `[fra, til)` per kategori; og «åpne nå» som egen rå teller **utenfor vindusvelgeren**. Radvis varighet `status_ts − ts` for lukkede — som radfakta, aldri aggregert. **Kun metadatafelter; kryptert payload røres aldri** |
| **Planer (tick)** | `bestillingsplan_tick` · **`vindu_start`**, aldri `registrert` (SP-6) | Prod har 0 rader: kortet viser «ingen planer kjørte i det valgte vinduet» som setning, ikke tom graf — skopet til vinduet, aldri en all-tid-påstand. Spørringen finnes og er testet med fixture for dagen data kommer |

## 3. Vindus- og suminvariantene

- **Én vindusdefinisjon:** UTC i basen, halvåpne intervaller
  `[fra, til)`, tidssone kun presentasjon. **Én delt hjelpefunksjon** —
  statisk port på at ingen kortspørring har egen vindusaritmetikk.
  Hendelse nøyaktig på `til` tilhører neste vindu.
- **Suminvariant per partisjon:** hver gjensidig utelukkende gruppe
  telles med total i **samme skann** (`COUNT(*) FILTER`,
  lesing.py-mønsteret). Flere partisjoner på samme kort summeres aldri
  på tvers. Radvise varigheter inngår i ingen suminvariant.
- **Aktivitet ≠ tilstand:** «åpne nå» er tilstand og påvirkes ikke av
  vinduet. «Åpne ved `til`» finnes ikke i v1 — historisk tilstand kan
  ikke bevises fra dagens tabell.

## 4. API og definere

- `GET /v1/nokkeltall?fra=…&til=…` bak `decisions:read` —
  **generaliseringen av 24h-sammendraget**: samme filtertelling,
  valgbart vindu (forhåndsdefinert: 24 t / 7 d / 30 d). Gjenbruk
  mønsteret; ikke nytt maskineri.
- Alle spørringer via definere med `tenant = p_tenant` +
  `krev_tenantkontekst` (SP-1); flaten leser aldri tabeller direkte
  (SP-7). **v1 er tenant-skopet** — ingen plattformvisning, ingen
  eksport, ingen tenant-sammenligning.
- Ingen dekrypteringsvei kalles fra nøkkeltallsveien (statisk port).
- Beslutningslisten lenker til eksisterende `/v1/beslutninger`
  (keyset) — ingen ny liste bygges.

## 5. Flaten — første graf-flate

- **Tabellen er tilgangsformen.** Hvert kort er ekte `<table>` med
  `<caption>` og `<th scope>`; alle tall finnes som tekst. Søyleraden er
  HTML/CSS-bredde ved siden av tallet — **ingen SVG-kurver, ingen
  canvas**, ingen informasjon som bare finnes i søylen.
- Kategori/beslutningstype aldri kun ved farge; kontrast fra
  `design/tokens.css`.
- Tomt vindu: rad med 0 og «ingen». Tick-kortet: tomtilstandssetning.
- Vindusvelger som `<select>` med `<label>`; valgt vindu og tidssone i
  klartekst ved tallene. «Åpne nå»-telleren står visuelt adskilt fra
  vinduskortene, med egen ledetekst.
- `t()`-nøkler for all tekst (`locales/nb.json`); KPI-tellermønsteret
  gjenbrukes; **axe-port i samme PR**; manuell tastaturgjennomgang
  dokumentert.

## 6. Codex-porter

**Data (1–7d).** 1 Suminvariant per partisjon fra samme skann, testet
under samtidig skriving; «ukjent» i totalen · 2 Tenant-binding per
definer: én tenant ser aldri en annens tall · 3 Ingen direkte SELECT
fra flaten (statisk) · 4 Ingen dekrypteringsvei fra nøkkeltallsveien
(statisk) · 5 Hendelse på `til` → neste vindu, aldri begge · 6 Delt
vindushjelp; ingen egen vindusaritmetikk (statisk) · 7 Tidsanker per
kort med kjent fixture · 7b Sak opprettet før vinduet, fortsatt åpen →
aldri aktivitet, alltid «åpne nå» · 7c «Åpne nå» upåvirket av
vindusvelgeren (ende til ende) · 7d Tick med `registrert` og
`vindu_start` i ulike vinduer → tilhører `vindu_start`-vinduet.

**Ærlighet (8–11).** 8 Ingen interpolasjons-/glattingskode (statisk) ·
9 Tomt vindu → 0 og «ingen»; tick-kortet → setning · 10 Ingen divisjon
i definerne (statisk); radvise varigheter eneste differanseform ·
11 UI-tall == API-svar (ende til ende).

**Flate (12–15).** 12 axe null `alvorligeBrudd` · 13 Alle tall som
tekst; søyle uten tekstlig verdi finnes ikke · 14 Aldri kun farge;
kontrast fra tokens · 15 Ingen hardkodet visningstekst;
tastaturgjennomgang dokumentert.

**Alle tester konstruerer egen tilstand.** Ingen delt fixture.

## 7. Evidensgrense `m16-v1` (defineres FØR arbeidet)

**Sikkerhetsinvarianter:** `nokkeltall.kryss_tenant_lesing = 0` ·
`nokkeltall.dekryptering_kalt = 0` ·
`nokkeltall.direkte_tabellesing = 0`.

**Øvrige:** `partisjon.suminvariant_brutt = 0` ·
`partisjon.ukjent_verdi_utenfor_total = 0` ·
`kort.egen_vindusaritmetikk = 0` · `vindu.dobbelttelling_pa_grense = 0` ·
`sak.tilstand_blandet_med_aktivitet = 0` ·
`tick.vindu_etter_registrert = 0` ·
`definer.divisjon_finnes = 0` · `flate.glatting_finnes = 0` ·
`flate.avledet_skaar = 0` · `flate.tall_kun_i_soyle = 0` ·
`flate.tomt_vindu_skjult = 0` ·
`ui.axe_alvorlige_brudd = 0` · `ui.tastaturgjennomgang_dokumentert = ja` ·
`ytelse.sporring_over_100ms_uten_indeksflagg = 0`.

Et punkt uten definert, målbar grense regnes som `nei`.

---

```
NÅ:    Implementer M-16 mot dette klarsignalet — definere,
       /v1/nokkeltall som generalisering av 24h-sammendraget, kortflate
       med axe i samme PR; ingen migrasjon — Claude Code
       — platform/core/api/lesing.py (generalisering),
         platform/core/db/ (definere), ui/nokkeltall/, locales/nb.json
NESTE: Etter merge: m02-aksept-arcen (flipper m56-manifestet);
       M-57-spesifikasjon når eier prioriterer bygging; #112/#115/#116
       etter eiers prioritering — Claude.ai / Claude Code
```
