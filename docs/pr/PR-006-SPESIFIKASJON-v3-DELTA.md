# PR-006 SPESIFIKASJON v3 — DELTA (de åtte gjenstående kontraktene)

**Draft: Claude.ai · Kun endringer. v1+v2 står ellers. Symmetriprinsipp
brukt gjennomgående: pre-auth-paradokset løses med SAMME mønster begge
steder — DB-backet, engangs, fencing-bundet kapabilitet.**

## 1. Tenantbundet M-37-identitet: claim-bundet arbeidskapabilitet

Ny tabell `arbeidskapabiliteter` (RLS-unntatt, eid av NOLOGIN-rolle,
kun nåbar via SECURITY DEFINER): tenant, jti (CSPRNG ≥128 bit),
unntak_id, claim_id, claim_generation, repair_operation_id,
tillatt_handling, utloper = LEAST(claim_utloper, now()+60 s),
brukt BOOLEAN. Utstedes atomisk av `utsted_arbeidskapabilitet(...)`
KUN under gyldig fencing (WHERE claim_id + generation + status +
utloper). Innløses av API-ets pre-auth-vei via
`innlos_kapabilitet(jti)` — atomisk engangsbruk (UPDATE brukt=false→true
RETURNING), bygger `EvaluationContext(rolle='m37',
kilde='arbeidskapabilitet')`. Beslutningsendepunktet håndhever i tillegg:
event.handling == kapabilitetens tillatt_handling, policy-tenant ==
kapabilitetens tenant, Idempotency-Key == repair_operation_id.
DB-backet i stedet for HMAC-stateless: null nøkkeldistribusjon, atomisk
engangsbruk, og kompromiss av arbeideren gir maks én handling på én sak
i ett lease-vindu. Forbudslisten fra reviewen (globalt token, tenant i
body, tokenlager hos arbeider, direktekall, claim-som-auth) tas inn som
negative tester.

## 2. Oppdragsclaim og kvitteringsingest — API-nivå, samme mønster

Eiermoduler er prosesser med egne tokens; scope-format
`orders:execute:<handlingsprefiks>` (lukket prefiksliste per modul).
To nye endepunkter + underliggende herdede funksjoner:

**`POST /v1/oppdrag/claim`** (modultoken): pre-auth → SECURITY DEFINER
`claim_neste_oppdrag(modul_id, prefiks, claim_id, lease)` — NOLOGIN-eier,
search_path=pg_catalog, atomisk SKIP LOCKED på oppdrag med
status='opprettet' AND eiermodul=modul_id (settes ved opprettelse fra
handlingsprefiks) — aldri andre modulers eller ubundne oppdrag. Returnerer
oppdragsmetadata + oppdragspayload (tenant-DEK-kryptert, samme mønster
som unntak) + en **kvitteringskapabilitet** (jti bundet til tenant,
oppdrag_id, modul_id, oppdrags-claim_id, utloper=oppdragsfrist).

**`POST /v1/oppdrag/kvittering`**: innløser kvitteringskapabiliteten
(pre-auth), verifiserer HMAC-signaturen mot modulens registrerte
verifikatornøkkel I APP-LAGET (nøkkelregisteret bor i app-state, ikke
DB), og committer i ÉN tenantbundet transaksjon: oppdrag→utfort/feilet
+ sak-overgang + historikk + revisjonsloggpost. Kvittering er idempotent
på (oppdrag_id, resultathash). Ugyldig signatur → sikkerhetssak, ingen
statusendring. **Den syntetiske eiermodulen bruker NØYAKTIG disse to
endepunktene — null direkte DB-skriving i staging-selen (evidensport).**

## 3. Sen/motstridende kvittering (reviewens regler, ordrett vedtatt)

Gyldig etter M-37-lease men før oppdragsfrist → kan avslutte automatisk.
Etter at saken er `manuell` → lagres som `sen_kvittering`-historikk,
ingen automatisk statusendring. For superseded/kansellert oppdrag →
sikkerhets-/driftsavvikssak, ingen statusendring. To ulike resultathasher
for samme oppdrag → sikkerhetssak. Identisk kvittering → idempotent no-op.

## 4. Generation/kansellering — DB-status er ikke kansellering

Ny generation kan starte KUN når gammel er én av: (a) uclaimet →
kanselleres atomisk (fencing-WHERE status='opprettet' → 'kansellert');
(b) terminal med kvittering. Er gammel claimet/plukket (utførelse kan
pågå) → ny generation opprettes IKKE; saken går til `manuell` med
historikk `generation_blokkert_aktiv_utforelse`. Ingen automatisk
parallellkjøring av generasjoner, noensinne. Oppdragsstatus utvides:
`opprettet|plukket|utfort|feilet|kansellert`.

## 5. R2 innskrenkes til lokale kontroller

R2 = KUN tekniske kontroller av data M-37 allerede har (re-validering av
format/konsistens, re-dekryptering, skjemasjekk). ALLE oppslag mot
autoritative kilder er sideeffektfrie **verifikasjonsoppdrag** gjennom
outbox-protokollen (pkt. 2) — utført av verifikator-/eiermodul med egne
fullmakter. Finnes ingen slik modul ennå → ruten går til `manuell`.
Null-fullmaktsprinsippet er dermed uten unntak: M-37 rører aldri
ERP/bank/CRM, verken for skriving eller lesing.

## 6. Claim-funksjonens parametergrenser og fairness

Funksjonen håndhever selv: lease = CLAMP(p_lease_s, 30, 600), default
120; p_claim_id må matche `^[0-9a-f]{32,}$` ellers avvis; forsøksgrense
= LEAST(maks_auto_forsok_snapshot, 3). Ny kolonne
`claim_generation INT NOT NULL DEFAULT 0` på unntak, inkrementeres
atomisk per claim — ALL fencing-WHERE inkluderer generation (ekte
fencing-token, ikke bare id-likhet). Deterministisk rekkefølge:
`ORDER BY prioritet DESC, ts, id`. Fairness v1: claim hopper over
tenanter med ≥5 saker allerede under_behandling (enkel anti-dominans);
full per-tenant fairness er deklarert M-38-scope.

## 7. Backfill av eksisterende saker (migrasjon 004, før NOT NULL)

Rekkefølge i migrasjonen: (1) legg kolonner nullable; (2) backfill fra
EVIDENS: JOIN revisjonsloggposten saken peker på — der
policy_content_hash finnes (alle 005a/005b-saker), kopieres hash og
versjon, og snapshot settes fra policyens dagjeldende verdi lagret i
loggpostens policy-innslag; (3) rader UTEN tilstrekkelig evidens får
eksplisitte legacy-verdier (`policy_versjon='legacy'`,
hash='legacy', snapshot=0) og settes DIREKTE til `manuell` med
historikk `legacy_uten_snapshot` — aldri automatisk behandling; (4)
først deretter `SET NOT NULL`. Aktiv policy brukes ALDRI blindt som
backfill-kilde.

## 8. Versjonert taksonomi + handler-deklarasjon (eksplisitt kontrakt)

```python
M37_TAKSONOMI_V1 = frozenset({
    "manglende_data", "teknisk_feil", "over_grense",
    "regelkonflikt", "ugyldig_data", "ukjent",
    "svindelmistanke", "hms_avvik",
})
```
Handler-deklarasjon (dataklasse, CI-validert, lukket —
additionalProperties-prinsippet i kode):
`handler_id, versjon, kategorier ⊆ taksonomi, grunnkoder (eksplisitt
liste), sideeffektfri: bool, krever_outbox: bool, tillatte_malhandlinger,
timeout_s, lease_s`. Sak med kategori ELLER grunnkode utenfor både
policyens liste og handlerens deklarasjon → `manuell` direkte ved
klassifisering, ingen gjentatte claims.

## Evidensporten utvides (de åtte bevisene, vedtatt)

Syntetisk eiermodul beviser via artefakt: (1) bruker kun claim-/
kvitterings-endepunktene; (2) kan ikke claime annen moduls/tenants
oppdrag; (3) to samtidige eiere → én vinner; (4) mistet lease → gammel
eier nektes ordinær terminal; (5) identisk kvittering idempotent;
(6) motstridende kvittering → sikkerhetssak; (7) sen kvittering lagres
uten statusendring; (8) null direkte DB-skriving (statisk sjekk av
staging-selen).
