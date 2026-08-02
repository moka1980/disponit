# PR-005b SPESIFIKASJON — delta til kort ChatGPT-bekreftelse

**Draft: Claude.ai · Basis: v2 + v3-delta har allerede GO; 005a (main
e00c062) er merget og binder implementasjonen. Dette dokumentet ber IKKE
om ny arkitekturreview — kun bekreftelse på at 005b-planen er konform
med GO-en og 005a-bindingene. Implementering starter ved bekreftelse.**

## Scope 005b

API-laget: `platform/core/api/app.py` + `kjerne.py` (implementert, ikke
skjelett), token-CLI, lasttest, m01-manifest i strukturert format,
CI/deploy-oppdateringer. Alt annet fra GO-en er levert i 005a.

## Designregel fra 005a-erfaringen (bindende for draften)

Hver port spesifiseres **udelelig fra start**: bygget + obligatorisk +
umulig å omgå i samme leveranse. Ingen port leveres som «valgfri nå,
herdes senere». Konkret: boot-sjekkene, body-grensen, tenant-settingen
og idempotens-låsen er ikke middleware som kan konfigureres bort — de
er koblet i kodeveien slik at endepunktet ikke eksisterer uten dem.

## De tre 005a-bindingene, operasjonalisert

1. **`sikker_beslutning_pg` er eneste vei til motoren.** kjerne.behandle()
   REIMPLEMENTERER IKKE beslutningsflyten — den pakker sikker_beslutning_pg
   i idempotens-claimet: advisory-lås → claim → `sikker_beslutning_pg(...,
   nokler=last_nokler())` (aldri None på nettverksveien — allerede
   boot-håndhevet) → routing/kryptering/unntaksrad per 005a-kontraktene →
   idempotens ferdig. Én transaksjon, grensene fra kjerne.py-kontrakten.
2. **`disponit.tenant` + `disponit.aktor` + `disponit.request_id` settes
   med SET LOCAL som FØRSTE statement i hver transaksjon**, fra
   verifisert token-kontekst — aldri fra payload. Manglende setting er
   allerede fail-closed i DB (RLS gir null rader; historikk feiler).
3. **Ingen nye GRANTs uten begrunnelse i PR-beskrivelsen.** 005b antas å
   klare seg med runtime-rettighetene fra 005a (unntak_historikk
   INSERT-only via trigger, policyer SELECT-only). Viser implementeringen
   behov for mer, dokumenteres hvorfor — Codex-port.

## Presiseringer siden GO (small print, ikke arkitektur)

- Attestasjoner er allerede request-bundet i 005a — app.py sender
  request-konteksten inn, ingen ny mekanikk i 005b.
- `deploy/staging/migrer.*` fra 005a er eneste migrasjonsvei — lasttest
  og API-oppsett gjenbruker den, introduserer ingen egen.
- Token-CLI (opprett/roter/deaktiver) kjører som authenticator-rollen på
  serveren, logger til revisjonsloggen med `disponit.aktor='token-cli'`,
  og skriver aldri secret til disk — vises én gang på stdout.
- Rate-grense i minne per prosess: deklarert svakhet fra GO-en står;
  boot-sperren (loopback eller TLS-flagg) er kompensasjonen.
- Lasttest per v2 Del 6 uendret; kjøres MOT API-et (ikke direkte mot
  kjernen) slik at ytelsesporten måler hele nettverksveien.

## Testplan-status

v2 Del 8 + v3-tillegget står som fasit. 005a dekket DB-kontraktene
(155 tester på staging); 005b leverer resten: alle HTTP-feilveier fra
Del 4-tabellen (én test per rad), 20-tråders idempotens over HTTP,
boot-nekt-testene, token-CLI-rundtur, cursor-manipulering, scope-nekt.

## Spørsmål til ChatGPT (kun disse)

1. Er innpakkingen «idempotens-claim rundt sikker_beslutning_pg» riktig
   lagdeling, eller ser du en transaksjonsgrense som brytes?
2. Token-CLI som authenticator-rolle med engangs-stdout: akseptabelt for
   staging, eller kreves mer nå?
3. Noe i 005a-bindingene som gjør en del av den GO-ede Del 3-4
   uimplementerbar som spesifisert?
