# PR-005b — BINDENDE KORREKSJONER (tillegg til spesifikasjonen)

**Med dette tillegget er ChatGPT-beslutningen GO uten ny reviewrunde.
De tre punktene er merge-krav — Codex verifiserer hvert av dem eksplisitt.**

## 1. Transaksjonseierskap: kjerne.behandle() eier commit

- Én ytre transaksjon eier hele flyten:
  `SET LOCAL → advisory-lås → idempotens-claim → sikker_beslutning_pg →
  logg/JTI/unntak → idempotens ferdig → commit`.
- `kjerne.behandle()` er ENESTE sted commit/rollback skjer. Alle interne
  `conn.transaction()`-blokker (inkl. i sikker_beslutning_pg og
  PgTellerLager når ytre transaksjon finnes) blir savepoints — psycopg
  gjør dette automatisk ved nesting, men Codex verifiserer at ingen
  kodevei kaller `conn.commit()`/`rollback()` utenfor eieren.
- Ingen `ferdig`-idempotensrespons lagres før loggpost, eventuell
  JTI-konsumering og eventuell unntaksrad er skrevet i samme transaksjon.
- Exceptions som betyr rollback PROPAGERER til eieren. De konverteres
  aldri til ordinær STOPP inne i et savepoint som deretter committes —
  STOPP-som-resultat og rollback-som-feil er to adskilte utfall.
- `last_nokler()` kalles og valideres ÉN gang ved boot; requestveien får
  registeret fra immutable app-state. Ingen fil-/miljølesing per request.

## 2. Tre roller + atomisk token-CLI

Rollemodell (retter v3-ordlyden — NOLOGIN-eieren kan ikke selv koble til):

| Rolle | Egenskap | Rettighet |
|---|---|---|
| `disponit_authenticator` | NOLOGIN | Eier `verifiser_token` og `api_tokener` |
| `disponit_runtime` | LOGIN | KUN `EXECUTE verifiser_token` — aldri tabelltilgang |
| `disponit_token_admin` | LOGIN | Minimal DML på `api_tokener` (INSERT + UPDATE av aktiv/utloper/secret_mac) + INSERT revisjonslogg. Eier ingenting. |

CLI-kontrakt (alle er merge-krav):
- Secret: ≥ 256 bits CSPRNG (`secrets.token_bytes(32)`), vises som
  `token_id.secret` nøyaktig én gang på interaktiv TTY.
- Secret godtas ALDRI som kommandolinjeargument; ingen visning når stdout
  er pipe/redirect — unntatt eksplisitt `--bootstrap`-modus (separat flagg,
  logget som egen handling).
- Tokenendring + revisjonsloggpost committes i SAMME transaksjon.
- `set -x`-sikkert: skriptet kjører med `set +x`-guard; secret finnes
  aldri i exceptions, logger eller shell-historikk (leses via stdin/TTY).
- Rotasjon: ny secret opprettes og committes FØR gammel deaktiveres
  (to transaksjoner, ny først) — feiler noe, består gammel token.

## 3. To-transaksjonsmodell: pre-auth før tenantkontekst

Rettelse av 005b-bindingen «SET LOCAL som første statement» — den var
sirkulær (tenant er ukjent før tokenet er verifisert):

**Pre-auth-transaksjon (per request):**
1. `request_id` genereres i serveren (aldri fra klient)
2. KUN kall til `verifiser_token(token_id, kandidat_mac)` — ingen
   tenanttabeller, ingen policylasting, ingen RLS-operasjoner
3. Commit/rollback, transaksjonen lukkes

**Autentisert forretningstransaksjon:**
1. Første statements: `SET LOCAL disponit.tenant / disponit.aktor /
   disponit.request_id` — fra pre-auth-resultatet
2. Deretter idempotens-lås, policyoppslag, beslutningsflyt per pkt. 1

**`/ready`:** bruker eksplisitt systemkontekst og leser KUN
ikke-tenantbundet status (migrasjonsversjon, DB-ping, nøkkelregister i
app-state). Later aldri som den har tokenkontekst.

---

**Codex-porter for disse tre:** (a) grep/statisk sjekk: ingen
commit/rollback utenfor kjerne.behandle() og CLI; (b) rolletest:
token_admin kan ikke EXECUTE-e funksjonen eller lese andre tabeller,
runtime kan ikke lese api_tokener; (c) test: request uten gyldig token
utfører null tenantbundne statements (verifiseres med statement-logg i test).
