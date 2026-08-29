# PR-010 SPESIFIKASJON — Brukersesjon for browser (til ChatGPT-porten)

**Draft: Claude.ai · Hard avhengighet før M-1 UI kan snakke med API-et
(PR-008 GO-krav). Leverer innlogging, sesjonscookie, CSRF, fornyelse og
logout. Bygger på `bruker`-tokenklassen fra PR-008 — erstatter den ikke,
men gir en trygg måte browseren får den på.**

## 0. Grensen: sesjon ≠ token

- **API-token** (agent/modul/bruker) = maskin-til-maskin, Bearer, ingen
  cookie. Uendret fra PR-005b/008.
- **Sesjon** = browserens vei til `bruker`-scopes. Serveren holder
  sesjonen; browseren får KUN en ugjettbar sesjonsreferanse i cookie.
  **Ingen bearer-token forlater serveren til browseren** (PR-008
  Codex-port 9).

## 1. Sesjonslager (migrasjon 009)

```sql
CREATE TABLE brukersesjon (
  sesjon_id_hash TEXT PRIMARY KEY,     -- SHA-256 av cookie-verdien
  tenant     TEXT NOT NULL,
  bruker_id  TEXT NOT NULL,
  scopes     TEXT[] NOT NULL,          -- lese-scopes, fra brukerens rolle
  opprettet  TIMESTAMPTZ NOT NULL DEFAULT now(),
  siste_bruk TIMESTAMPTZ NOT NULL DEFAULT now(),
  utloper    TIMESTAMPTZ NOT NULL,     -- absolutt tak
  tilbakekalt BOOLEAN NOT NULL DEFAULT false,
  csrf_hash  TEXT NOT NULL             -- SHA-256 av CSRF-token
);
```
- **Cookie-verdien lagres ALDRI** — kun hash (som `api_tokener.secret_mac`).
  DB-dump gir ingen gyldige sesjoner.
- RLS+FORCE på tenant; oppslag via herdet SECURITY DEFINER
  `slaa_opp_sesjon(hash)` (NOLOGIN-eier, `search_path=pg_catalog`) som
  returnerer (tenant, bruker_id, scopes) — aldri hashene.
- Uforanderlig unntatt `siste_bruk` og `tilbakekalt` (kolonnelås-trigger).

## 2. Levetid — to tak

- **Inaktivitet:** 30 min uten kall → ugyldig.
- **Absolutt:** 12 timer fra `opprettet`, uansett aktivitet → ny innlogging.
- Fornyelse skjer implisitt (`siste_bruk` oppdateres maks 1×/min for å
  unngå skrivestøy), ALDRI utover det absolutte taket.
- Utløpt/tilbakekalt sesjon → `401` med lukket kode `sesjon_ugyldig`; UI
  viser innloggingsflaten (UI-spec §4 «uautorisert»-tilstand).

## 3. Cookie-kontrakt

`disponit_sesjon` = 256 bits CSPRNG, base64url:
`HttpOnly` · `Secure` · `SameSite=Lax` · `Path=/` · `Max-Age` = inaktivitets-
taket · ingen `Domain` (host-only). **Ingen sesjonsdata i cookien** — den
er kun en referanse. Ingen `localStorage`/`sessionStorage` noe sted.

## 4. CSRF (klar for fremtidige mutasjoner)

Double-submit med server-bundet hemmelighet:
- Ved innlogging: CSRF-token (256 bits) returneres i responsBODY (ikke
  cookie), lagres i minne av UI-et, og `csrf_hash` lagres i sesjonsraden.
- ALLE ikke-idempotente kall (fremtidige mutasjoner) krever header
  `X-Disponit-CSRF`; sammenlignes konstant-tid mot `csrf_hash`.
- Lese-endepunktene (PR-008) krever ikke CSRF, men kontrakten finnes fra
  dag én så behandlings-PR-en ikke må ettermontere den.

## 5. Innlogging — v1 med deklarert grense

**v1: passordbasert innlogging mot `bruker`-tabell** (Argon2id, per-bruker
salt, ingen pepper i DB). Deklarert avgrensning: SSO/OIDC er egen senere
PR — men `bruker_id` og `tenant` er modellert slik at en ekstern
identitetsleverandør kan kobles på uten skjemaendring.
- Feilet innlogging: generisk melding (aldri «feil passord» vs «ukjent
  bruker»), rate-grense per (bruker, IP), eksponentiell backoff.
- Vellykket: sesjon opprettes, cookie settes, CSRF returneres, hendelsen
  logges i revisjonsloggen (`bruker.innlogging`, uten passord).

## 6. Endepunkter

| Endepunkt | Beskrivelse |
|---|---|
| `POST /v1/sesjon` | Innlogging → cookie + CSRF-token i body |
| `DELETE /v1/sesjon` | Logout → `tilbakekalt=true`, cookie slettes (Max-Age=0). Idempotent |
| `GET /v1/sesjon` | Hvem er jeg: `{tenant, bruker_id, scopes, utloper}` — ingen hasher |

Alle PR-008 lese-endepunkter aksepterer FRA NÅ enten Bearer `bruker`-token
(maskintest) ELLER sesjonscookie. Samme scope-sjekk, én kodevei for
autorisasjon — ikke to parallelle.

## 7. Fire samtidighetsspørsmål besvart

| Kontroll | Alle veier inn? | Under samtidighet? | Riktig vs velformet? | Lukket format? |
|---|---|---|---|---|
| Sesjonsoppslag | Én funksjon, brukt av både cookie- og Bearer-veien | `siste_bruk`-oppdatering er ikke-blokkerende (maks 1×/min, ingen lås-strid) | Sjekker tilbakekalt + begge tidstak, ikke bare eksistens | Lukket 401-kode |
| Logout | Kun `DELETE /v1/sesjon` | Samtidig logout + kall: `tilbakekalt` leses i samme transaksjon som oppslag → taperen får 401 | Tilbakekalling er umiddelbar, ikke «utløper snart» | Idempotent |
| CSRF | Alle ikke-idempotente ruter (default-deny liste) | Token er per sesjon, ikke per request → ingen kappløp | Konstant-tids sammenligning mot hash | Header-navn fast |
| Innloggingsforsøk | Kun `POST /v1/sesjon` | Rate-grense per (bruker,IP) delt state — i minne i v1 (deklarert svakhet, loopback) | Generisk feil uansett årsak | Lukket feilkode |

## 8. Akseptansekriterier

Innlogging gir cookie som virker mot lese-endepunktene · cookie er
HttpOnly+Secure+SameSite (header-test) · sesjonsverdi finnes IKKE i DB
(kun hash — dump-test) · inaktivitet 30 min → 401 · absolutt 12 t → 401
selv med aktivitet · logout → umiddelbart 401, idempotent · CSRF kreves
på en testmutasjon og avvises ved feil/manglende token · kryss-tenant:
sesjon for tenant A ser aldri tenant B · ingen token/sesjonsverdi i
browserlager, DOM, URL eller logg.

## Spørsmål til ChatGPT
1. `SameSite=Lax` vs `Strict`: Lax er valgt for at dyplenker fra e-post
   skal virke. Riktig avveining for et bedriftsverktøy, eller bør det
   være Strict i v1?
2. Passordbasert v1 med SSO deklarert som senere PR — eller bør vi hoppe
   rett til OIDC og unngå å bygge passordhåndtering vi senere fjerner?
3. Bør sesjonscookie og Bearer-token kunne brukes mot samme endepunkter
   (som spesifisert), eller er det tryggere at browser-sesjonen KUN når
   lese-endepunktene og maskin-token kun de andre?
