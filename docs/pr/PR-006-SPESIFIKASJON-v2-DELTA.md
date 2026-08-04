# PR-006 SPESIFIKASJON v2 — DELTA (svar på de åtte blokkerne)

**Draft: Claude.ai · Kun endringene. Prosessisolasjon, JCS og
null-fullmakts-prinsippet står uendret fra v1.**

## 1. Tenant-sikker claim: SECURITY DEFINER-funksjon (anbefalt modell valgt)

`claim_neste_sak(p_claim_id TEXT, p_lease_s INT)` i migrasjon 004:
eies av ny NOLOGIN-rolle `disponit_m37_claimer`; `search_path=pg_catalog`;
kun skjemakvalifiserte objekter; ingen dynamisk SQL; `REVOKE ALL FROM
PUBLIC`, EXECUTE kun til arbeiderrollen. Atomisk i én setning:
`UPDATE unntak SET status='under_behandling', claim_id=$1,
claim_utloper=now()+$2, forsok=forsok+1 WHERE (tenant,id) = (SELECT
tenant,id FROM unntak WHERE sakstype='normal' AND status='ny' AND
forsok < maks_auto_forsok_snapshot ORDER BY prioritet DESC, ts
FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING tenant, id, handling,
kategori, loggpost_id` — metadata, ALDRI payload. Deretter åpner
arbeideren tenantbundet behandlingstransaksjon der `SET LOCAL` via
`sett_kontekst()` er første operasjon. Negative tester: funksjonen kan
aldri returnere sikkerhet/drift-saker eller claime på tvers av
fencing; arbeiderrollen er ellers fortsatt full-RLS.

## 2. Executor/outbox — redusert v1-scope, protokollen definert nå

Ingen eiermoduler eksisterer ennå; M-37 kan derfor ikke erklære
forretningshandlinger «utført». v1 leverer protokollen og stopper der
sannheten stopper:

**Ny tabell `oppdrag` (migrasjon 004):** tenant, id, kompositt-FK til
unntak og til beslutnings-loggposten, handling,
`repair_operation_id` (UNIQUE per tenant — se pkt. 4), status
`opprettet|plukket|utfort|feilet`, kvittering JSONB, kvittering_signatur,
tidsstempler. RLS+FORCE, append+status-mønsteret, kolonnelås.

**Flyt R1/R2:** re-evaluering via API (`Idempotency-Key =
repair_operation_id`) → TILLAT → INSERT oppdrag i samme transaksjon som
statusskiftet → sak `venter_utførelse`. Sak settes `løst` KUN ved
**signert, ressursbundet resultatkvittering**: eiermodulen signerer
(samme HMAC-/nøkkelregister-mekanisme som attestasjoner) over
(tenant, oppdrag_id, repair_operation_id, resultat, ressurs_id, ts).
Kvittering etter lease-utløp er gyldig — den binder til oppdraget, ikke
claimen. Uteblir kvittering innen oppdragsfrist (default 24 t) →
`manuell`.

**R2 avgrenses** til sideeffektfrie tekniske kontroller (re-validering,
re-oppslag mot autoritativ kilde) — kan nå `løst` direkte uten oppdrag.
Alt som krever forretningsutførelse går R1-veien.

**Feilinjiserings-evidens:** en syntetisk eiermodul (staging-only,
tydelig merket, egen registrert verifikatornøkkel) plukker oppdrag og
poster signerte kvitteringer — protokollen bevises ende-til-ende uten å
late som produksjonsutførere finnes.

## 3. Lease-fencing og fornyelse

`claim_id` er fencing-token. ALLE skriv etter claim (status, oppdrag,
kompensasjon) har `WHERE tenant=? AND id=? AND claim_id=? AND
status='under_behandling' AND claim_utloper>now()` — null rader =
mistet lease = full abort: ingen statusskriv, ingen oppdragsopprettelse,
ingen kompensasjon (repair_operation_id-unikheten er andre forsvarslinje
mot dubletter hvis fencing skulle glippe). Heartbeat: fornyelse ved 50 %
av leasen, samme claim_id i WHERE. Bindende kappløpstest: A claimer og
blokkeres forbi lease → B re-claimer → As terminal-skriv treffer null
rader og avvises; historikk viser claim → claim_utlopt → claim → terminal.

## 4. Stabil reparasjons- og kompensasjonsidentitet

`repair_operation_id = SHA-256(tenant ‖ unntak_id ‖ handler_id@versjon ‖
target_action ‖ kanonisk_input_hash)` — forsok og claim_id inngår ALDRI
(transportdetaljer). Nye data som faktisk endrer reparasjonen gir ny
`repair_generation` med ny input-hash, logget som egen historikkhendelse
`repair_generation_ny`; eldre generasjons oppdrag markeres `feilet`
(superseded) FØR ny opprettes. Kompensasjon:
`compensation:<unntak_id>:<handling>:<original_loggpost_id>`.

## 5. Status `manuell` (reell terminaltilstand)

Migrasjon 004 utvider CHECK og statusmaskinen:
`ny → under_behandling → løst | avvist | manuell | venter_utførelse`,
`venter_utførelse → løst | manuell`, `under_behandling → ny` (kun
lease-utløp). R3-klasser går til `manuell` VED FØRSTE CLAIM — ingen
bortkastede forsøk — med saksvarsling i v1 (strukturert logg + metric;
synlig i `GET /v1/unntak?status=manuell`). `forsok >= snapshot` under
behandling → `manuell`, aldri stående `ny`.

## 6. Snapshot av policykontekst på saken

Migrasjon 004: `unntak` får `maks_auto_forsok_snapshot INT NOT NULL`,
`policy_versjon TEXT NOT NULL`, `policy_content_hash TEXT NOT NULL` —
settes av API-veien ved opprettelse (kolonnelåst etterpå). Effektiv
forsøksgrense = `LEAST(snapshot, plattformtak 3)` — systemet kan stramme
inn globalt, aldri løsne. Policyendring etter opprettelse endrer aldri
en eksisterende saks retrysemantikk; behandling re-evaluerer alltid mot
AKTIV policy (og logger hash-avvik som historikkhendelse
`policy_endret_siden_opprettelse`).

## 7. Lukket M-37-taksonomi

`m37/taksonomi.py`: frossen plattformtaksonomi (settet fra
policy-skjemaets obligatoriske kategorier + sikkerhetskategoriene).
Handler deklarerer kategorier ⊆ taksonomien (CI-validert). En sak
behandles kun hvis kategorien finnes i BÅDE sakens policy-liste OG
handlerens deklarasjon. `ugyldig_data` splittes per Grunn-kode
(data-tabell i `api/feil.py`-stil): korrigerbar/gjenhentbar → R1;
semantisk motstridende → manuell; manipulasjonsmistanke → sikkerhetskø.

## 8. Evidensgrenser (feilinjisering-m01-v1, revidert)

Terminal = reell DB-status (`løst|avvist|manuell`); `venter_utførelse`
regnes ikke — testsettet designes så alle 20 når terminal via den
syntetiske eiermodulens kvitteringer. Minst én injisert sak SKAL gjennom
lease-tap + re-claim (historikk beviser kjeden). Kvitteringer bindes til
repair_operation_id + sak. Artefaktet dokumenterer separate PID/cgroup
for API- og M-37-prosess. Klartekst-test bruker kjente canary-verdier i
payload i tillegg til grep. Øvrige grenser fra v1 står; rollback-grensen
uendret.

## Feilveiene fra reviewen — tilstand og gjenopptak (uttømmende)

| Feilvei | Regel |
|---|---|
| Claim uten kjent tenant | Umulig by design: claim-funksjonen returnerer tenant atomisk |
| Mistet lease under nettverkskall | Fencing-WHERE gir null rader → abort; ingen skriv |
| Arbeider fullfører etter fencing-tap | Samme — terminal-skriv avvises; historikk urørt |
| API TILLAT men executor starter aldri | Oppdragsfrist → `manuell` m/ oppdrag `feilet` |
| Executor utført, kvittering tapt | Executor re-poster (kvittering idempotent på oppdrag_id); frist → `manuell`, oppdrag består som evidens |
| Kvittering etter lease-utløp | Gyldig — binder oppdrag, ikke claim (pkt. 2) |
| Policy endret under behandling | Re-evaluering mot aktiv policy + historikkhendelse (pkt. 6) |
| DEK destruert, sak behandlingsbar | Dekryptering umulig → `manuell` m/ historikk `dek_utilgjengelig`; destruksjonsjobben skal ikke treffe åpne saker (sjekk i destruer-veien — negativ test) |
| Verifikator utilgjengelig | R1 tilbake til kø m/ backoff; teller mot forsøksgrense |
| Nye data mens eldre reparasjon kjører | repair_generation-regelen (pkt. 4): gammel superseded før ny |
| Kompensasjon godkjent, ikke utført | Samme oppdragsfrist-vei som R1 → `manuell` |
| Krasj etter oppdrag, før statusskriv | Gjenopptak: oppdrag med repair_operation_id finnes → statusskriv er idempotent replay, ingen ny beslutning (nøkkelen stopper dublett) |

## Svar-kvittering på portspørsmålene

Policy-skjema: løst med snapshot (pkt. 6) og taksonomi (pkt. 7).
Reversibilitet: løst med kvitteringsprotokoll (pkt. 2). Unntaks-
håndtering: tabellen over. R3/lease/idempotens: pkt. 5/3/4 følger
reviewens anbefalinger direkte.
