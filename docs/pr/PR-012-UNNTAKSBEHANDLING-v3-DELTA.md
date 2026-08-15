# PR-012 SPESIFIKASJON v3 — DELTA (seks kontrakter → GO)

**Draft: Claude.ai · Prinsipp og v2-retning står. Seks kontrakter lukket
+ presisert `handlingsintensjon`-skjema.**

## 1. Attestasjonens faktiske DB-form (navnekollisjonen rettet)

v1s `handling` (`godkjenn|avvis|eskaler`) og v2s `handling` (target
action) kan ikke være samme felt. Rettet:
```sql
menneskelig_attestasjon(
  id, tenant, unntak_id, runde INT,
  operatorhandling TEXT CHECK (operatorhandling IN ('godkjenn','avvis','eskaler')),
  target_action TEXT,                    -- f.eks. faktura.bokfor; NULL for avvis/eskaler
  bruker_id, rolle, authz_version INT,
  konvoluttversjon INT NOT NULL,         -- 1 = disponit_human_approval_v1
  konvolutt_hash TEXT NOT NULL,          -- SHA-256 over kanonisk JCS-konvolutt
  mac TEXT NOT NULL, mac_key_id TEXT NOT NULL,
  jti TEXT NOT NULL, utloper TIMESTAMPTZ NOT NULL,
  begrunnelse_kryptert BYTEA, key_id TEXT, nonce BYTEA,
  saksversjon INT NOT NULL, ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (tenant, jti),                                    -- §2
  UNIQUE (tenant, unntak_id, runde, bruker_id),            -- fire øyne
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id))
```
**MAC-nøkkelregister:** `mac_key_id` peker på nøkkel i app-state (fra
systemd credential, ALDRI i DB). Register støtter rotasjon (flere aktive
nøkler, én gjeldende for signering). **Oppstartsperre:** manglende/ugyldig
register → prosessen nekter start (som `last_nokler`). Verifikasjon
konstant-tid. Kanonisering: JCS (RFC 8785), som resten av systemet.

## 2. Engangsbruk håndhevet, ikke antatt

- `UNIQUE (tenant, jti)` — konvolutten kan ikke registreres to ganger.
- Attestasjonen **konsumeres** ved rundens `klar → brukt` i SAMME commit
  som den auditerte beslutningen og eventuell outbox-rad.
- Replay med IDENTISK input → samme idempotente resultat (via
  `Idempotency-Key`). Replay med AVVIKENDE input → sikkerhetsrouting,
  ingen ny beslutning.

## 3. Maks én aktiv runde (delindeksen dekket ikke `klar`)

```sql
CREATE UNIQUE INDEX en_aktiv_runde ON godkjenningsrunde
  (tenant, unntak_id) WHERE status IN ('apen','klar');
```
Åpning, utløp og siste godkjenning låser **samme saks- og runderad** i
dokumentert rekkefølge: `unntak → godkjenningsrunde →
menneskelig_attestasjon → oppdrag → kapabilitet` (v2 §8, uendret).

## 4. Policy-drift mellom første og andre godkjenner

Første attestasjon binder policyhash A; en signatur over A kan ikke
gjøres om til B ved «revalidering». Valgt modell (eksplisitt):
- **Runden fryser policy A** (`policy_versjon` + `policy_hash` på
  runderaden, kolonnelåst).
- **Siste godkjenning krever at A FORTSATT er aktiv.**
- Er aktiv policy blitt B → **runden kanselleres** (`kansellert`), saken
  → `manuell`, historikk `policy_endret_under_godkjenning`. **Begge må
  godkjenne på nytt under B** i ny runde.
- Dette er en NORMAL hendelse, ikke et signaturangrep: ingen sikkerhets-
  sak, ingen rate-straff, tydelig UI-tekst («policyen er endret — saken
  må godkjennes på nytt»).

## 5. Total utfallsmatrise for den nye beslutningen

v2 dekket kun `venter_utførelse|løst`. Fullstendig:

| Motorutfall | Sakens utfall | Runde | Lineage |
|---|---|---|---|
| TILLAT, sideeffektfri | `løst` | `brukt` | ny loggpost peker på opprinnelig sak |
| TILLAT + outbox | `venter_utførelse` → `løst` ved kvittering | `brukt` | oppdrag bærer `beslutning_loggpost_id` (PR-008) |
| STOPP | **`manuell`** m/ historikk `godkjenning_stoppet_av_policy` + STOPP-koden | `brukt` | ingen ny sak |
| TIL_UNNTAK | **`manuell`**, og det nye unntaket opprettes IKKE som egen kø-sak | `brukt` | se anti-rekursjon |
| Teknisk/transaksjonell feil | ingen endring (rollback) | `klar` (uendret) | ingen |

**Anti-rekursjon (bindende):** en beslutning som stammer fra en
menneskelig godkjenning bærer `opphav_unntak_id`. Gir den UNNTAK, skal
det IKKE opprettes en ny selvstendig kø-sak — resultatet registreres på
den OPPRINNELIGE saken (status `manuell` + begrunnelseskjede), slik at
godkjenn → unntak → godkjenn ikke kan produsere en kjede av identiske
saker. Idempotens: samme `decision_operation_id` gir samme utfall.

## 6. Tre scopes, reautorisering etter låsing, avvis lukker runden

- **Separate scopes:** `exceptions:approve`, `exceptions:reject`,
  `exceptions:escalate`. `exceptions:manage` utgår som samlebegrep.
  Default-deny: mangler scope → handlingen finnes ikke for brukeren.
- `tillatte_handlinger[]` er PRESENTASJON. **POST-ruten reautoriserer
  samme handling ETTER at saken er låst** (steg 3 i v2 §8) — en rolle som
  ble fjernet mellom GET og POST stopper handlingen.
- **Avvis kansellerer en `apen|klar` runde** atomisk i samme transaksjon.
- **«Sen kvittering etter avvis» er IKKE en ny status** — det er
  append-only konfliktevidens + sikkerhetssak. Den terminale saken
  (`avvist`) endres ALDRI. (Retter v2 §6, som feilaktig gjorde det til en
  tilstand.)

## 7. `handlingsintensjon`-skjemaet presisert

- **Kanonisk serialisering:** JCS (RFC 8785), sorterte nøkler.
- **Decimal som streng**, regex `^\d{1,13}\.\d{2}$` (som policy-DTO).
- **Valuta:** ISO-4217, tre bokstaver, PÅKREVD når `belop` finnes.
- **Tid:** RFC 3339 med UTC-offset.
- **Maks størrelse:** 8 KiB klartekst (avvises over).
- **AES-256-GCM**, nonce i egen kolonne, **tag i ciphertext** (siste 16
  byte) — samme som unntakspayload.
- **Tenantkonsistent FK til nøkkelregisteret:**
  `FOREIGN KEY (tenant, hi_key_id) REFERENCES tenant_nokler (tenant, key_id)`.
- **Krypteres i SAMME transaksjon som unntaket opprettes** — ingen
  mellomtilstand med manglende eller ukryptert intensjon er mulig
  (CHECK: handling med `menneskelig_overstyring` ⇒ intensjon NOT NULL).
- `hi_skjemaversjon` lukket konstant; ukjent versjon → godkjenn
  utilgjengelig (fail-closed), ikke gjetting.

## Tester (tillegg)
Konvolutt med `operatorhandling` i `target_action`-feltet avvist ·
jti-replay avvist · runde kan ikke åpnes mens forrige er `klar` ·
policyendring mellom godkjennere → runde kansellert, ingen sikkerhetssak,
begge godkjenner på nytt · motor gir STOPP → sak `manuell`, ingen ny
kø-sak · godkjenn → UNNTAK produserer ALDRI ny selvstendig sak · scope
`exceptions:reject` alene kan ikke godkjenne · rolle fjernet mellom GET
og POST → handling avvist ved reautorisering · avvis lukker åpen runde ·
sen kvittering etter avvis → evidens + sikkerhet, `avvist` uendret ·
handling m/ menneskelig_overstyring uten intensjon kan ikke opprettes.
