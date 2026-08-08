# PR-008 SPESIFIKASJON v4 — DELTA (fire tetthetskontrakter → GO)

**Draft: Claude.ai · v1–v3 står der de ikke motsies. Reviewens anbefalte
modeller vedtatt — inkludert den ærlige keyset-semantikken uten
snapshotløfte.**

## 1. Backfill via entydig evidens + kardinalitet håndhevet i DB

Reparasjonsoppdragets `unntak.loggpost_id` peker på loggposten som skapte
UNNTAKET, ikke reparasjonsbeslutningen som skapte oppdraget — så
kjede-backfill kan gi gyldig FK med semantisk FEIL beslutning. Rettet:
- Backfill bruker KUN direkte, entydig evidens: `repair_operation_id`
  finnes både på oppdraget og på fase-2-beslutningens loggpost (PR-007) →
  koble på dét. For ordinære oppdrag: `idempotency_key`/beslutnings-id som
  finnes på begge.
- `unntak.loggpost_id`-kjeden alene er ALDRI tilstrekkelig for
  reparasjonsoppdrag.
- Mangler entydig evidens → `beslutning_loggpost_id = NULL`; vises som
  «utførelsesdata ikke tilgjengelig», ALDRI koblet til antatt beslutning.
- **Nye oppdrag: `beslutning_loggpost_id NOT NULL` håndhevet i DB** (constraint
  settes etter backfill, som checksum-bootstrapen i migrasjon 003).
- **Kardinalitet: én beslutning → maks ett oppdrag:**
  `UNIQUE (tenant, beslutning_loggpost_id)`. Detaljresponsens entallsform
  er dermed korrekt og DB-håndhevet. (Skulle flere oppdrag per beslutning
  bli nødvendig senere, er det en eksplisitt fremtidig endring til
  listeform — ikke antatt nå.)

## 2. Total evidensflaggmatrise + `IKKE_RELEVANT`

`evidensstatus`-enum omdøpes: `INGEN → IKKE_RELEVANT` (var tvetydig mellom
«ikke relevant» og «ingen mottatt»). Ny enum: `IKKE_RELEVANT | MANGLER |
GYLDIG`. Flaggene `sen_evidens`/`konflikt_evidens` får total matrise:

| resultat.art | evidensstatus | sen_evidens | konflikt_evidens |
|---|---|---|---|
| policy_stoppet | IKKE_RELEVANT | false | false |
| sideeffektfri_tillatt | IKKE_RELEVANT | false | false |
| til_unntak | IKKE_RELEVANT | false | false |
| outbox_opprettet | MANGLER | {false\|true} | {false\|true} |
| outbox_plukket | MANGLER | {false\|true} | {false\|true} |
| outbox_utfort | GYLDIG | {false\|true} | {false\|true} |
| outbox_feilet | GYLDIG\|MANGLER | {false\|true} | {false\|true} |
| outbox_kansellert | IKKE_RELEVANT\|GYLDIG | {false\|true} | {false\|true} |

Bindende regler (servermodellen håndhever
`art × evidensstatus × sen × konflikt`):
- `sen_evidens`/`konflikt_evidens` lovlige KUN for `outbox_*`.
- Begge kan være true samtidig hvis DB faktisk har begge evidenstypene.
- **`konflikt_evidens=true` INNEBÆRER at sikkerhetssak finnes** — server-
  invariant. Uten `security:read` er `sikkerhet` fortsatt utelatt, men
  invarianten gjelder (konfliktevidens uten underliggende sikkerhetssak
  er en serverfeil som avvises).
- Konflikt endrer ALDRI `resultat.art` — kun presentasjonspresedens over sen.
- **`outbox_kansellert` KAN ha sen/konfliktevidens** (PR-006: kvittering
  for kansellert/superseded oppdrag lagres som sen/motstridende) UTEN å
  gjenåpnes — v3s «aldri SEN/KONFLIKT» var feil nå som disse er flagg, ikke
  utførelsesstatus. Korrigert her.

## 3. Ærlig keyset uten snapshotløfte (v1)

`snapshot_max_id` gir IKKE et ekte snapshot: PostgreSQL-sekvenser er ikke
transaksjonelle, så en lav ID reservert før men committet etter første
side dukker opp senere. `REPEATABLE READ` lever ikke mellom HTTP-kall.
**Reviewens v1-anbefaling vedtatt — vanlig keyset, ærlig semantikk:**
- Ingen duplikater for uendrede rader (keyset garanterer det).
- Samtidig innsetting KAN bli synlig eller utelatt mellom sider — dokumentert,
  ikke skjult bak et falskt snapshotløfte.
- UI får «Oppdater»-handling for nytt konsistent førstesidebilde.
- Cursor binder `versjon, tenant, endepunkt, sorteringsretning, aktive
  filtre, siste (ts,id), utløp` (INGEN `snapshot_max_id` — fjernet).
- Predikat: Beslutninger DESC `(ts,id) < (siste_ts,siste_id)`;
  Historikk ASC `(ts,id) > (siste_ts,siste_id)`.
- HMAC konstant-tids; cursor fra annen tenant/endepunkt/filter → `400
  cursor_ugyldig`.
(Strengt snapshot er bevisst UTSATT — hvis noen gang nødvendig, egen
kontrakt med server-side resultatsett + TTL + ytelsestest, ikke overlatt
til implementeringen.)

## 4. Komplette, lukkede policy-DTO-er (alle nestede definert)

`skjemaversjon` inn i PolicyDTO; alle refererte typer definert; alle nivåer
`additionalProperties: false`:
```
PolicyDTO { skjemaversjon:int, policy_id:str≤128, versjon:str≤64,
  innholds_hash:str(64), roller:[RolleDTO]≤50, handlinger:[HandlingDTO]≤200,
  verifikatorer:[VerifikatorDTO]≤100 }
RolleDTO { id:str≤64, beskrivelse_kode:str≤128 }
HandlingDTO { navn:str≤128, modus:[auto|auto_med_vilkaar|alltid_stopp],
  grenser:GrenserDTO|null, vilkaar:[str≤128]≤50 }
GrenserDTO { belop_maks:str(decimal)|null, valuta:str(3 ISO-4217)|null,
  tidsvindu:TidsvinduDTO|null, frekvens:FrekvensDTO|null }
TidsvinduDTO { ukedager:[int 0-6]≤7, fra:str("HH:MM"), til:str("HH:MM"),
  tidssone:str(IANA) }
FrekvensDTO { maks:int≥1, vindu_enhet:[time|dag|uke|maaned], vindu_antall:int≥1 }
VerifikatorDTO { offentlig_id:str≤128, betrodd_for:[str≤128]≤50,
  kan_fastsla_permanent:bool }
```
Beslutninger:
- **decimal som JSON-STRENG** (`belop_maks:"25000.00"`) — unngår
  float-presisjonstap, konsistent med motorens Decimal-kontrakt.
- Valgfrie felt eksplisitt `|null`; **tomt objekt og `null` betyr det
  SAMME** for grenser (ingen grense) — normaliseres til `null`.
- Valuta: ISO-4217 tre bokstaver. Tidsvindu: IANA-tidssone + ukedager 0–6.
- `additionalProperties: false` på hvert nivå; ukjent felt = byggefeil,
  ikke passthrough (versjonert via `skjemaversjon`).

## Bindende tester (reviewens, vedtatt)
Legacy reparasjonsoppdrag kobles IKKE til opprinnelig unntaksbeslutning
(kun via repair_operation_id) · ny oppdragsrad uten beslutnings-FK avvises
(DB) · to oppdrag samme beslutning avvist (UNIQUE) · kansellert oppdrag
med sen/konfliktevidens beholder `outbox_kansellert` · konfliktevidens uten
sikkerhetssak bryter serverinvariant (avvist) · cursor-test med transaksjon
som reserverer lav ID og committer ETTER første side (beviser ærlig
keyset-semantikk, ingen falskt snapshot) · ukjent felt avvist i HVERT
nestet policy-DTO-nivå.

## Ingen åpne punkter
FK+kardinalitet DB-håndhevet · evidensflaggmatrise total · paginering ærlig
keyset · policy-DTO-er komplett lukket. Klar for GO.
