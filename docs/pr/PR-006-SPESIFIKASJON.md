# PR-006 SPESIFIKASJON — M-37 behandlingsmotor (til ChatGPT-porten)

**Draft: Claude.ai · Basis: main c021bca (295 tester) + PR-006-brief fra
Claude Code 2026-08-03. Lagringen (migrasjon 003), claiming-feltene,
krypteringen, routing-dataen i `api/feil.py` og kølesingen FINNES —
denne spesifikasjonen bygger på dem og redesigner ingenting.**

## 0. Arkitekturbeslutning: egen prosess, aldri inline

Ytelsesporten er målt med 3,4× spredning (p95 24–82 ms) og p99 207 ms.
**M-37 kjører derfor som egen arbeiderprosess mot køen**
(`platform/core/m37/arbeider.py`, systemd-unit på staging) — aldri i
`kjerne.behandle()` eller API-prosessen. Forespørselsveien skriver saker;
arbeideren behandler dem. Null M-37-arbeid i request-path er en
Codex-port (statisk sjekk: api/ importerer aldri m37/).

## 1. Arbeideren — ny vei inn, samme vakter

Arbeideren er en NY inngang til databasen (brief-spørsmål 1) og får
nøyaktig samme vakter som de to eksisterende:

- `sett_kontekst(conn, tenant, aktor='m37-arbeider', request_id=claim_id)`
  som FØRSTE databaseoperasjon i hver behandlingstransaksjon — tenant
  hentes fra den claimede sakens rad.
- `Transaksjonsvakt` gjenbrukes: én eier av commit per flyt — i
  arbeideren er det behandlingsløkken, aldri reparasjonshandlere.
- Runtime-rollens EKSISTERENDE rettigheter er tilstrekkelige og utvides
  ikke: status-UPDATE på unntak ✓, historikk via trigger ✓,
  SELECT policyer/tenant_nokler ✓. `exceptions:manage` forblir reservert
  (ingen API-utvidelse i PR-006; arbeideren går DB-direkte på samme host).

**Transaksjonsmodell:** (a) claim-transaksjon — kort:
`SELECT … WHERE sakstype='normal' AND status='ny' AND forsok <
maks_auto_forsok ORDER BY prioritet DESC, ts FOR UPDATE SKIP LOCKED
LIMIT 1` → sett under_behandling, claim_id (CSPRNG), claim_utloper =
now() + lease (default 120 s), forsok+1 → commit. (b) behandlings-
transaksjon — dekrypter payload (aldri til disk/logg), slå opp
reparasjonsregel, utfør, verifiser, sett terminal status → commit.
Krasj mellom (a) og (b): lease utløper, sak går under_behandling→ny via
statusmaskinregelen som allerede finnes; historikk `claim_utlopt`.

**Manuell kø uten statusutvidelse:** saker med
`forsok >= policyens maks_auto_forsok` claimes ALDRI av auto-arbeideren.
De forblir `ny` og er per definisjon manuell kø — synlig i
`GET /v1/unntak` som i dag. Sakstype sikkerhet/drift claimes heller
aldri av normal-arbeideren (kø-flom-vernet består).

## 2. Reparasjonsbiblioteket — lukket register

Register i kode: `m37/reparasjoner.py` — en LUKKET mapping
`(kategori, handlingsprefiks) → handler`. Ukjent kombinasjon = ingen
reparasjon = forblir i kø til manuell grense nås (brief-spørsmål 3:
ny nøkkel er en feil-vei, ikke stillhet — CI-test validerer at
registeret kun inneholder kategorier fra policy-skjemaets enum).

**v1-omfang (uttømmende — tre handlere, ikke flere):**

| Regel | Kategori | Hva den gjør | Terminal |
|---|---|---|---|
| R1 re-innsending | manglende_data | Sjekker om manglende attestasjon/felt nå foreligger (spør verifikator-kilden). JA → bygg NY hendelse og send den som NY beslutning gjennom API-et (`decision:write`-token for arbeideren, egen Idempotency-Key avledet av unntak-id+forsok). NEI → tilbake til kø | løst hvis ny beslutning TILLAT og utført; ellers kø |
| R2 begrenset retry | teknisk_feil | Retry av idempotent operasjon med eksponentiell backoff (maks 3, allerede begrenset av forsok-telleren) | løst ved suksess |
| R3 policykrevende | over_grense, regelkonflikt, ugyldig_data, ukjent | INGEN automatisk reparasjon — beslutningen var riktig. Saken modnes til manuell grense | manuell (ny + forsok=maks) |

**Invariant (Codex-port):** en reparasjon utfører ALDRI forretningsside-
effekten selv og omgår ALDRI motoren — R1 går gjennom hele API-veien og
policyporten på nytt. M-37 har dermed null egne fullmakter; den kan bare
be om nye, policystyrte beslutninger. Dette bevarer invariant 1–6 uten
nye rettigheter. Hver reparasjon skriver historikk (ny rad, aldri over).

## 3. Kompenserende reversering — samme prinsipp

Når en sak krever kompensasjon (f.eks. R1-re-innsending avdekker at
opprinnelig delutført handling må nulles): arbeideren leser
`reversering {type: kompenserende, handling, frist_sekunder}` fra
policyen og SENDER KOMPENSASJONSHANDLINGEN SOM NY BESLUTNING gjennom
API-et. Kompensasjoner er altså selv policystyrte handlinger —
kompensasjonshandlingen må være definert i kundens policy, ellers går
saken til manuell kø (fail-closed). `frist_sekunder` sjekkes mot sakens
ts; utløpt frist → aldri automatisk kompensasjon → manuell.
Irreversible handlinger kompenseres ALDRI automatisk i v1.

## 4. JCS-kanonisering (RFC 8785) — retro-P2 lukkes

`attestering.kanonisk_bytes` byttes til RFC 8785-kompatibel
implementasjon. Attestasjonen får feltet `kanonisering: "JCS"`;
nettverksveien AKSEPTERER KUN JCS fra og med denne PR-en (lukket format
— manglende/ukjent verdi avvises; alle verifikatorer er interne, så
bruddet er kontrollert og skjer i samme PR som byttet). Testvektorer fra
RFC 8785 inngår i suiten; `default=str`-fallback fjernes (ikke-JSON-typer
er nå valideringsfeil, ikke stille strengkonvertering).

## 5. Evidensgrensene defineres FØRST (brief §5)

Begge legges i `KRAVGRENSER` i `manifestskjema.py` I DENNE PR-EN, før
arbeidet som skal måles — artefaktskjema med `additionalProperties: false`
som PR #8 etablerte:

**`feilinjisering-m01-v1`** (setter `feilinjisering_til_unntakskø: ja`):
injisert_antall = 20 · kategorier_dekket ≥ 3 (minst én per R1/R2/R3-klasse)
· terminal_andel = 1.0 innen 300 s · løst_andel av reparerbare = 1.0 ·
manuell_andel av ikke-reparerbare = 1.0 · historikk_komplett = true
(ubrutt kjede opprettet→claim→terminal per sak, verifisert mot
historikktabellen) · klartekst_payload_funnet = false (grep i artefakt,
logger og DB-dump) · p95_api_under_last ≤ 150 ms målt MENS arbeideren
kjører (beviser separat-prosess-beslutningen).

**`rollback-m01-v1`** (setter `rollback_testet: ja` — grensene har
manglet; defineres nå slik at den som gjør arbeidet har en fasit):
deaktivering_effektiv_s ≤ 5 (modul inaktiv i register → API svarer
definert 503-kontrakt `modul_inaktiv`) · reaktivering_effektiv_s ≤ 5 ·
tapte_loggposter = 0 · pågående_requests_korrekt_avvist = 1.0 (ingen
halvferdige transaksjoner) · andre_tabeller_uendret = true.
Selve rollback-kjøringen kan gjøres i PR-006 eller egen liten PR —
grensene er uansett definert her.

## 6. De tre portspørsmålene, besvart per kontroll

| Kontroll | Alle veier inn? | Riktig, ikke bare velformet? | Lukket format? |
|---|---|---|---|
| Claiming | Kun arbeideren claimer; API kan ikke (ingen UPDATE-vei i api/) — AST-test | SKIP LOCKED + lease + forsok-grense; kappløpstest 20 arbeidere → hver sak claimes én gang | status/sakstype er CHECK-enums; ukjent claim-tilstand umulig |
| Reparasjonsregister | Kun arbeideren leser det; handlere kan ikke kalles utenfra (private) | Handler må VERIFISERE resultat før løst (R1: ny beslutning TILLAT+utført) | Lukket mapping; kategori utenfor policy-enum = CI-feil |
| Kompensasjon | Kun via API-beslutning — ingen direkte-DB-vei | Policyport validerer kompensasjonen som enhver handling | Udefinert kompensasjonshandling → manuell, aldri gjetting |
| JCS | Én kanoniseringsfunksjon, brukt av signering OG verifisering | RFC 8785-testvektorer | `kanonisering`-felt påkrevd; ukjent verdi avvises |
| Evidens | CI-porten fra PR #8 gjelder begge nye krav_id-er | Domenegrenser (andeler, tider), ikke bare skjema | additionalProperties: false |

## 7. Testplan (minimum)

Kappløp: 20 samtidige arbeidere, 50 saker → hver sak nøyaktig én claim
(historikk-tellingen beviser). Lease-utløp → re-claim med historikk.
R1-rundtur ende-til-ende på staging (injisert manglende_data → løst via
ny API-beslutning → begge loggposter koblet via historikk). R3 modnes til
manuell og claimes aldri igjen. Sikkerhets-/driftssaker claimes aldri av
normal-arbeider (negativ test). Arbeider uten kontekst → transaksjonen
feiler (gjenbruk av eksisterende vakt). Dekryptert payload finnes aldri
i logg/disk (grep-test). JCS-vektorer + avvisning av ikke-JCS.
Feilinjiserings- og ev. rollback-artefakt gjennom evidensporten.

## Spørsmål til ChatGPT

1. R3-semantikken: er «riktig beslutning, ingen reparasjon, modnes til
   manuell» korrekt behandling av over_grense/regelkonflikt — eller bør
   noen av disse ha en varslingsvei allerede i v1?
2. Lease-default 120 s og maks 3 auto-forsøk: rimelige startverdier, eller
   bør de være policy-felter per kunde allerede nå?
3. R1s idempotensnøkkel avledet av (unntak_id, forsok): ser du et
   kappløp/replay-hull i den konstruksjonen?
