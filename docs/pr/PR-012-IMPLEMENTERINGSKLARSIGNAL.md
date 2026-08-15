# PR-012 — IMPLEMENTERINGSKLARSIGNAL (GO, unntaksbehandling v1)

**Til Claude Code · Konsolidert v1–v6. Branch: `pr-012-unntaksbehandling`.
GO + ni vilkår i PR-beskrivelsen. Dette er første HANDLING i Disponit —
M-1 går fra «se» til «gjøre».**

## Bærende prinsipp (leses først)

**Mennesket avgir en attestasjon; motoren tar beslutningen.** En
«godkjenn»-knapp som flipper status ville vært en bakdør forbi
policymotoren. I stedet MAC-signeres den menneskelige godkjenningen som
en lukket konvolutt og mates inn i en NY beslutning gjennom motoren —
samme mønster som R1s fase 2. Godkjenner et menneske noe policyen ikke
tillater et menneske å godkjenne, stopper motoren det igjen. **Mennesket
får ingen nye fullmakter — kun en rolle policyen allerede har definert.**

## De ni bindende vilkårene

### V1. Én transaksjon, én kodevei, fast rekkefølge
`behandle_unntakshandling(...)` på SAMME DB-connection — aldri internt
HTTP: `sett_kontekst` → lås sak (`FOR UPDATE`) → kontroller saksversjon/
status/fencing → reserver idempotens → revalider aktiv policy + ALLE
godkjennere → skriv attestasjon + rundestatus → **runde `brukt` FØR
`godkjenningsutfall` settes inn** (så bindingstriggeren kan verifisere
kjeden) → kjør beslutning via eksisterende kjerne → revisjonslogg +
eventuell outbox → commit alt eller ingenting.
Låserekkefølge: `unntak → godkjenningsrunde → menneskelig_attestasjon →
oppdrag → kapabilitet`.

### V2. Fail-closed, aldri fallback
Menneskeflyt uten lås, aktiv runde, gyldig MAC, gyldig utløp, matchende
hash eller reservert `decision_operation_id` → **AVBRYT**. Ingen
beslutning, ingen ny kø-sak. Menneskeflyten faller aldri tilbake til
ordinær opprettelse.

### V3. Sikkerhetsevidens overlever rollback
Ved MAC-/bindingsbrudd rulles FORRETNINGStransaksjonen tilbake, men
sikkerhetshendelsen **persisteres gjennom eksisterende fail-closed
sikkerhetsrouting** (egen connection/transaksjon). «Ingenting committes»
gjelder forretningsendringer, ikke evidens.

### V4. Undertrykking av ny kø-sak er server-utledet
Flagget finnes IKKE i noen offentlig signatur. Aktiveres kun internt når
alle fem er verifisert i samme transaksjon: låst sak · aktiv runde ·
gyldige ubrukte attestasjoner · matchende `hi_integritet_hash` +
`godkjennings_policy_hash` · reservert `decision_operation_id`.

### V5. To adskilte policyhasher
`intensjon_policy_hash` (uforanderlig, AES-GCM AAD ved kryptering OG all
dekryptering) vs. `godkjennings_policy_hash` (aktiv policy frosset per
runde, avgjør om ny runde er lovlig). Aldri sammenblandet.
**AAD = `tenant ‖ unntak_id ‖ target_action ‖ hi_skjemaversjon ‖
intensjon_policy_hash`**, alle fra uforanderlige saksfelt.

### V6. Godkjenning kun for eksplisitt godkjennbare vilkår
Lukket mapping `(grunnkode, handling)` i `menneskelig_overstyring` —
ikke kategorier. `teknisk_feil`/`manglende_data` kan aldri godkjennes.
`belop_maks` krever `valuta`; ingen implisitt konvertering. Mangler
feltet → ingen godkjenning mulig (deny-by-default).

### V7. Tre scopes, reautorisering etter låsing
`exceptions:approve` / `:reject` / `:escalate` — `exceptions:manage`
utgår. `tillatte_handlinger[]` er PRESENTASJON; POST-ruten
reautoriserer samme handling ETTER låsing.

### V8. Fire øyne med versjonert runde
`godkjenningsrunde` med `apen → klar → brukt` / `apen → utlopt|kansellert`.
Delindeks `UNIQUE (tenant, unntak_id) WHERE status IN ('apen','klar')`.
**`UNIQUE (tenant, decision_operation_id)`** når operasjons-ID finnes
(eller FK til idempotensreservasjonen). Attestasjonsunikhet inkluderer
runden. Ny runde arver aldri gamle godkjenninger. Ved siste godkjenning
revalideres BEGGE brukeres medlemskap, rolle og `authz_version`, og MAC +
utløp for BEGGE attestasjoner kontrolleres under samme lås.

### V9. Avvis lover aldri mer enn DB kan bevise
Ingen oppdrag / `opprettet` → kanseller fenced, `avvist`. `plukket`/
`utfort`/ukjent → **ikke lov å påstå «avvist uten utførelse»** → saken
`manuell` med avklaringsflagg. Sen kvittering etter avvis = append-only
konfliktevidens + sikkerhetssak, **aldri ny status** på den terminale saken.
Avvis kansellerer atomisk en `apen|klar` runde.

## De femten Codex-portene
1. Ingen intern HTTP i transaksjonen (statisk sjekk)
2. Menneskeflyt uten aktiv runde → avbrutt, ingen beslutning og ingen ny kø-sak
3. MAC-avvik → sikkerhetsevidens persistert selv om alt annet rulles tilbake
4. Undertrykkingsflagget ikke eksponert i noen offentlig signatur
5. Intensjon kryptert under policy A dekrypteres etter at aktiv policy er B
6. Ciphertext flyttet til annen sak → dekryptering feiler (AAD)
7. `teknisk_feil` kan ikke godkjennes; ulik valuta ikke godkjennbar
8. `exceptions:reject` alene kan ikke godkjenne
9. Rolle fjernet mellom GET og POST → avvist ved reautorisering
10. Runde kan ikke åpnes mens forrige er `apen` eller `klar`
11. Utløpt runde → samme bruker kan delta i ny runde; attestasjoner slettes aldri
12. Samme (sak, intensjonshash, godkjenningspolicyhash) kan ikke godkjennes to ganger
13. Loggpost fra riktig operasjon men feil sak → avvist av bindingstrigger
14. Avvis på claimet oppdrag → `manuell` m/ avklaring, aldri «ikke utført»
15. Teknisk feil under siste godkjenning → runde `apen`, første attestasjon består, retry virker

## Omfang
Migrasjon 010: `handlingsintensjon_*` + `intensjon_pakrevd` +
`intensjon_policy_hash` på `unntak`; nye statuser (`venter_godkjenning`,
`venter_andre_godkjenner`, `godkjenning_klar`) med trigger; tabellene
`menneskelig_attestasjon`, `godkjenningsrunde`, `godkjenningsutfall` med
bindingstrigger · policy-skjema: `menneskelig_overstyring` (valgfritt,
lukket) · API: `POST /v1/unntak/{id}/handling` + utvidet detaljrespons
(`tillatte_handlinger[]`, `tillatte_eskaleringsmal[]`, `saksversjon`) ·
MAC-nøkkelregister (HMAC-SHA-256, `signerer|verifiserer|pensjonert`,
nøyaktig én signerer, oppstartsperre) · UI: `HandlingDialog` bygges nå,
mot server-returnerte handlinger · evidensgrense `behandling-m37-v1`.

## Etter merge → staging
Feilinjiser 12 saker over 4 kategorier, kjør evidensartefaktet, og
deretter: **Eier behandler en ekte sak i køen på disponit.com.**
