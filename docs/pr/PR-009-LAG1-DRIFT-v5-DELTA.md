# PR-009 SPESIFIKASJON v5 — DELTA (én sannhetskilde for tokenstatus)

**Draft: Claude.ai · v1–v4 står. Ett P1 + tre presiseringer.**

## 1. `status` er eneste autoritet — `aktiv` fjernes i samme migrasjon

To muterbare felt ville gitt `aktiv=false` + `status='AKTIV'`, og eldre
tilbakekallingskode som bare setter `aktiv=false` ville sluttet å virke.
Reviewens anbefalte modell vedtatt:
1. Backfill `status` fra `aktiv` (v4 §2, uendret).
2. **Oppdater ALLE veier** — opprettelse, aktivering, rotasjon,
   tilbakekalling (`token-cli.py`, eventuelle admin-veier) — til å skrive
   `status`.
3. `verifiser_token` → `status='AKTIV'`.
4. **`ALTER TABLE api_tokener DROP COLUMN aktiv`** i SAMME migrasjon.

Ingen midlertidig sameksistens. (Skulle en avhengighet vise seg å trenge
`aktiv`, blir den en GENERERT kolonne fra `status` — aldri direkte
skrivbar — men standardvalget er å fjerne den.) Codex-port: grep etter
`aktiv` i token-veier gir null treff etter migrasjonen.

## 2. Tre presiseringer

**Migrasjonens «virker under»:** presiseres ærlig — eksisterende tokens
virker FØR og ETTER commit; kall kan blokkeres av DDL-låsen i selve
migrasjonsvinduet. Det er akseptabelt (kort vindu, staging), og
formuleringen lover ikke mer.

**PENDING-verifikasjonsfunksjonen:** CLI-avgrenset og herdet (SECURITY
DEFINER, `search_path=pg_catalog`, EXECUTE kun til token-admin-rollen).
Den gjør ALDRI `PENDING` gyldig som API-principal — den sammenligner kun
hemmelighet mot lagret MAC for CLI-ens egen bekreftelse.

**Deploy vs. interaktiv bootstrap SKILLES:**
- `opp.sh` (automatisert deploy) kjører UTEN TTY-krav og fullfører uten
  operatørbekreftelse. Den utsteder ingen tokens.
- `bootstrap-token.sh` (eksplisitt interaktiv førstegangs
  hemmelighetsutlevering) krever TTY og operatørbekreftelse — kjøres
  separat av operatøren.
Automatisert deploy henger dermed aldri på et menneske.

## Akseptansekriterier (tillegg)
`aktiv`-kolonnen finnes ikke etter migrasjon · tilbakekalling via
`status='TILBAKEKALT'` sperrer tokenet umiddelbart · `opp.sh` fullfører
uten TTY · `bootstrap-token.sh` nekter uten TTY · PENDING avvist som
API-principal.
