# M-1 UI — KONSOLIDERT IMPLEMENTERINGSGRUNNLAG (til ChatGPT for endelig signal)

**Draft: Claude.ai · Konsoliderer UI-spesifikasjon v1 + v2/v3/v4-deltaene
til ett implementerbart grunnlag. Backend er verifisert klar av Claude
Code mot main `218d322`. Ber om endelig implementeringsklarsignal —
samme form som PR-006 til PR-010 fikk.**

## 0. Status på v1s tre «åpne» spørsmål — ALLEREDE BESVART

De står som stale tekst i v1 §Spørsmål; svarene kom i første UI-review og
er innarbeidet. Ingen re-litigering nødvendig:
1. **Read-only policy i v1 er riktig** — selv en beløpsgrense endrer
   fullmakter og må gjennom validering, diff, aktivering, revisjon og
   rollback. Ingen «lite» redigeringsunntak.
2. **Døgntellinger er ikke M-16** — så lenge vinduet er fast,
   serverberegnet, uten mål/trend/score/anbefaling, og tidspunkt +
   tidssone for siste oppdatering vises.
3. **Ni fundamentkomponenter manglet** — lagt til i v2 §7 (under).

## 1. Scope (v1 §0 + v2 §1, endelig)

**ER:** første kundeflate — fire flater (Oversikt, Policy, Beslutninger,
Unntak) + det varige designfundamentet som 40+ senere moduler arver.
**ER IKKE:** driftsbilde (lag 1), M-16 KPI, policy-REDIGERING,
unntaks-BEHANDLING (v2 §1: «Behandle sak» ute av scope og ute av
e2e-kriteriet — en generell statusknapp kunne omgått M-37-maskinen).

## 2. Backend: verifisert klar, null gap (Claude Code mot main 218d322)

Alle seks endepunkter finnes og er scoped: `/v1/oversikt`,
`/v1/beslutninger` + `/{id}`, `/v1/unntak` + `/{id}` + `/{id}/historikk`,
`/v1/policy/aktiv`. Sesjonsruter fra PR-010 finnes. Eneste fravær er
skrive/behandling — bevisst utenfor scope. Tokens, locales, tokens.css og
prototype er på plass.

## 3. Komponentbibliotek (v1 §3 + v2 §7 = det varige settet)

**Fra v1:** AppShell · BeslutningBadge · KategoriTag · DataTabell ·
Detaljpanel · BegrunnelseKjede · StatusTidslinje · Bekreftelsesdialog ·
TomTilstand · Feiltilstand · Lasteskjelett.
**Fra v2 §7:** SideStatus · TilgangsVakt · CursorNavigasjon ·
VarselBanner · LiveRegion · Tidspunkt · KodeForklaring · SensitiveData ·
HandlingDialog (**bygges IKKE nå** — v3 §3: ingen ubrukt sikkerhets-
kritisk komponent på spekulasjon; beholdes i planen).

`KodeForklaring` må ha trygg fallback: ukjent kode fra nyere motor viser
råkoden escaped, krasjer ikke og viser aldri tom streng.

## 4. Bindende byggekrav

**Fundament (v1 §1):** alt utseende fra `design/tokens.css` (manglende
UI-tokens legges DIT, aldri inline) · ingen hardkodet tekst, alt via
`locales/` der motorens maskinkoder ER nøklene · WCAG 2.1 AA som
CI-blokkerende port (axe-core) · server er sannhet, null forretningslogikk
i frontend · tenant-isolasjon synlig og reell.

**Fem skjermtilstander per flate (v1 §4, nå via `SideStatus`):** lasting,
innhold, tom, feil (årsak + utvei), uautorisert. En flate uten alle fem
er ikke ferdig.

**XSS/dataminimering (v2 §4):** API-data rendres med tekstnoder /
rammeverkets escaping — ingen ufiltrert `innerHTML` · ingen HTML i
locale-strenger · CSP uten inline-script (UI-ets CSP defineres i DENNE
leveransen — PR-009b holdt den bevisst åpen) · aldri payload,
attestasjoner, tokens eller nøkler i DOM/URL/browserlager/logg/analytics ·
råkoder kan vises, men escapes.

**Tilgjengelighet (v2 §6):** knapp/lenke i celle (ikke `<tr onclick>`) ·
drawer som ekte dialog med `inert` bakgrunn, fokusfelle og fokusretur ·
`sr-only` definert i base-CSS (ikke som token — v3 §3.4) · `<caption>` på
hver tabell · språkvelger funksjonell eller fjernet · `aria-sort` faktisk
implementert · responsiv oppførsel under breakpoint (build-time konstant
fra tokenkilde, ikke CSS-variabel i `@media` — v3 §3.4) · 200 % zoom.

## 5. Datakontrakt (v3 §1-2 + v4, endelig)

**Beslutningsdetalj — tre ortogonale akser, UI kombinerer ALDRI selv:**
`resultat.art` (åtte varianter: policy_stoppet, sideeffektfri_tillatt,
outbox_opprettet/plukket/utfort/feilet/kansellert, til_unntak) ·
`evidensstatus` (IKKE_RELEVANT | MANGLER | GYLDIG) · `sen_evidens` og
`konflikt_evidens` som flagg · `sikkerhet` toppnivå, FRAVÆRENDE uten
`security:read` (fravær ≠ false — UI validerer mot riktig responsvariant).
**Ukjent `art` → `Feiltilstand`**, aldri gjetting — fremtidssikkert mot
nye motorvarianter.

**`TILLAT` betyr tillatt, ikke utført.** Badgen sier «Tillatt»;
utførelsesstatus er eget felt. Et UNNTAK fremstilles med sin kategori,
aldri som «stoppet av policy».

**Paginering:** `CursorNavigasjon` mot ærlig keyset (v4 §3) — ingen
snapshotløfte. UI tilbyr «Oppdater» for nytt førstesidebilde.

## 6. Sesjonsintegrasjon (PR-010)

Innlogging via OIDC-redirect (`POST /v1/oidc/start`) · sesjon via
`__Host-disponit_sesjon` (HttpOnly, ikke lesbar for UI) · CSRF-token fra
`__Host-disponit_csrf` (JS-lesbar med hensikt) sendes i
`X-Disponit-CSRF` på fremtidige mutasjoner · `GET /v1/sesjon` gir
tenant/bruker/scopes for AppShell · 401 → uautorisert-tilstand med
innloggingsflate, aldri stille feil · logout-tekst sier «logget ut av
Disponit», aldri av IdP-en.

## 7. Implementeringsrekkefølge

1. UI-tokens som mangler → `design/tokens.css`; `sr-only` → base-CSS.
2. Komponentbibliotek (§3) med axe-core i CI fra første komponent.
3. Fire flater, hver med alle fem tilstander via `SideStatus`.
4. Mot ekte endepunkter (de finnes) — mock kun der sesjon ikke er oppe.
5. E2E: logg inn → se policy → se beslutning i logg → åpne unntak → se
   begrunnelse + historikk. **Behandling er ikke del av kriteriet.**

## 8. Foreslåtte Codex-porter
Ingen ufiltrert `innerHTML` på API-data (statisk sjekk) · axe-core grønn
på alle fire flater · alle fem tilstander demonstrert per flate · ukjent
`resultat.art` → Feiltilstand (ikke krasj) · `sikkerhet` fraværende uten
scope → ingen seksjon, ikke «nei» · ingen hardkodet visningstekst
(grep-port mot locales) · ingen token/sesjonsverdi i DOM/URL/storage ·
tastaturnavigasjon gjennom hele en flate · 200 % zoom uten tap ·
`TILLAT` fremstilles aldri som «utført».

## Bes om
Endelig implementeringsklarsignal i samme form som PR-006–010: bindende
vilkår + Codex-porter, slik at Claude Code kan bygge. Grunnlaget er
komplett; ingen kjente åpne spørsmål.
