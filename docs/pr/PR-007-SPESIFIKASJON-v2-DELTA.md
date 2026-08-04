# PR-007 SPESIFIKASJON v2 — DELTA (åtte kontrakter → GO)

**Draft: Claude.ai · Modell (b) beholdt. Kun endringene. Reviewens
anbefalte løsninger vedtatt direkte.**

## 1. Fase 2 re-claimes alltid — `verifikasjon_klar` som egen status

Kvitteringsingest opptrer ALDRI som M-37-arbeider og forlenger aldri en
lease. Positiv verifikasjon gjør KUN dette, atomisk: lagre bevis + sett
saken `verifikasjon_klar`. Deretter er fase 1 terminal og all lease
sluppet. `claim_neste_sak()` utvides til å claime `verifikasjon_klar`
med NY `claim_id` og inkrementert `claim_generation` — først den nye
claimen starter fase 2. Samme claim gjennom begge asynkrone faser er
forbudt (negativ test: gammel arbeider kan ikke bygge fase 2 etter
re-claim → fencing-WHERE treffer 0 rader).

## 2. Varig faseinformasjon i DB

Migrasjon 007 legger på `unntak` (kolonnelåst etter claim, satt av
fenced overgang):
- `venter_verifikasjon` og `verifikasjon_klar` — begge ikke-terminale
- `verification_generation INT NOT NULL DEFAULT 0`
- `ventet_bevis_id BIGINT` — hvilket verifikasjonsbevis fase 2 konsumerer
Fase-2-claimen VALIDERER: status='verifikasjon_klar', at
`ventet_bevis_id` peker på et gyldig, ikke-utløpt bevis for riktig
generation. Fasen utledes ALDRI indirekte fra «finnes en bevisrad» —
den er eksplisitt status + generation i DB.

## 3. Faseidentitet med generasjon

`fase1_id = SHA-256(tenant ‖ unntak_id ‖ 'verifikasjon' ‖ vilkaar ‖
handler_id@versjon ‖ verification_generation)`. Retry av SAMME
generasjon → samme id (idempotent). Ny generasjon (monoton +1) kan
opprettes KUN etter eksplisitt terminal/utløpt overgang på forrige
(`venter_verifikasjon → manuell`, eller bevis utløpt). DB-håndhevelse:
- `verifikasjonsbevis` UNIQUE endres til
  `(tenant, unntak_id, vilkaar, verification_generation)`
- Delindeks `maks_en_aktiv_verifikasjon_per_sak_vilkaar` UNIQUE
  `(tenant, unntak_id, vilkaar) WHERE status='aktiv'` — maks én aktiv
  generasjon per sak+vilkår, samtidig som nye generasjoner tillates
  sekvensielt. Løser motsigelsen reviewen fant.
`fase2_id = SHA-256(tenant ‖ unntak_id ‖ 'beslutning' ‖ target_action ‖
ventet_bevis_id)` — binder til konkret bevis.

## 4. Lukket signert konvolutt

Signaturen dekker en KANONISK (JCS) konvolutt med nøyaktig disse
feltene (lukket, additionalProperties: false):
`protokollversjon, tenant, oppdrag_id, unntak_id, fase1_repair_operation_id,
verification_generation, vilkaar, ressurs_id, attestert_resultat,
vilkaarsverdier?, utstedt, utloper, verifikator_id, nokkel_id, jti`.
Ingest kontrollerer ALLE bindinger server-side mot oppdraget og saken;
`verifikator_id` må stemme med den verifiserte NØKKELEN (nøkkel-eier),
ikke det selvrapporterte feltet. Ethvert avvik → sikkerhetslogg, ingen
bevisrad. Et bevis kan dermed aldri flyttes mellom sak/oppdrag/ressurs.

## 5. Lukket, versjonert verifikasjonskvittering

Egen kvitteringstype `verifikasjonskvittering_v1` (additionalProperties:
false) — den ENESTE som kan bære en attestasjon. Ordinære
utførelseskvitteringer avviser attestasjonsfelt (skjemaet nekter).
Idempotens på (oppdrag_id, resultathash); annen gyldig kvittering for
samme generation → sikkerhetsrouting. Registreres i den lukkede
kvitteringstaksonomien ved siden av utførelseskvitteringen.

## 6. Fase 2 respekterer outbox — ingen snarvei

Fase 2s nye beslutning behandles NØYAKTIG som enhver beslutning:
- `AVVIS`/feil → `manuell` (eller definert terminal)
- `TILLAT` for eksplisitt SIDEEFFEKTFRI handlingstype → kan avsluttes `løst`
- `TILLAT` med sideeffekt → oppdrag opprettes, saken → `venter_utførelse`;
  KUN gyldig eierkvittering (den eksisterende outbox-porten) setter `løst`
Sideeffektfri-flagget kommer fra handler-deklarasjonen (PR-006 v3 pkt. 8),
ikke fra en antakelse. PR-007 lager INGEN vei rundt outbox.

## 7. Bevis krypteres — samme modell som øvrig M-37-data

`verifikasjonsbevis` deler ikke lenger klartekst:
- `attestasjon_kryptert BYTEA` (tenant-DEK, AES-256-GCM), `key_id`,
  `nonce`, `alg` — samme envelope som `unntak.payload_kryptert`
- `integritet_hash TEXT` over kanonisk klartekst (verifisering uten
  dekryptering)
- Ikke-sensitiv metadata (vilkaar, verifikator_id, generation, gyldig_til)
  i klartekstkolonner for oppslag
- DEK/KEK forlater aldri kryptolaget
- Retention via crypto-shredding (DEK destrueres), IKKE DELETE —
  append-only på innhold består; opprydding er avgrenset, auditert
  mekanisme som for `unntak`. Motsigelsen (både «ingen DELETE» og «ryddes»)
  løst: raden slettes aldri, nøkkelen destrueres.

## 8. Autorisert skrivevei for bevis

INSERT skjer KUN via SECURITY DEFINER `registrer_verifikasjonsbevis(...)`:
NOLOGIN-eier, search_path=pg_catalog, REVOKE ALL FROM PUBLIC, EXECUTE kun
til runtime. Tenant, oppdrag og generation UTLEDES server-side fra den
verifiserte konvolutten (pkt. 4) — aldri fra klientparametre. Ingen
direkte runtime-INSERT/UPDATE/DELETE på tabellen. Avviste signaturer →
sikkerhetslogg, ingen bevisrad. Symmetrisk med claim-/kvitterings-
funksjonene fra PR-006.

## Svar på spørsmål 1 — tre eksplisitte ruter (fail-closed)

Klassifisereren ruter på Grunn-kode:
- manglende attestasjon innhentbar autoritativt → **R1 tofase**
- manglende forretningsverdi/originaldata → **manuell**
- ukjent/sammensatt årsak der første kategori ikke kan BEVISES →
  **manuell** (den tredje klassen — fail-closed, aldri gjett R1)

## Bindende tester (reviewens liste, vedtatt)

Verifikasjon etter utløpt fase-1-lease: lagres, fase 2 kun med ny claim ·
gammel arbeider bygger ikke fase 2 etter re-claim · bevis for annen
tenant/sak/ressurs/vilkår/generation/oppdrag avvises (én test per
binding) · to identiske kvitteringer idempotent, to ulike gyldige →
sikkerhetssak · utløpt attestasjon → ikke `verifikasjon_klar` · ny
generation etter utløp, retry beholder identitet · `TILLAT` med
sideeffekt setter ikke `løst` uten eierkvittering · ingen attestasjon i
klartekst (canary) · verifikator har ingen direkte DB-skriverett ·
manglende verdi + ukjent Grunn-kode + sammensatt årsak → manuell.
