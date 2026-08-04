# PR-007 — IMPLEMENTERINGSKLARSIGNAL (GO, R1 enkelt-verifikator)

**Til Claude Code · Implementér mot v1–v6 + v7 pkt. 1–3 + Scope v2 fra
main 019e06a. Branch: `pr-007-r1-tofase`. GO + fire vilkår i
PR-beskrivelsen. v7 pkt. 4 og HELE v8 er UTE av scope (fler-verifikator
→ manuell, egen fremtidig PR).**

## De fire implementeringsvilkårene (bindende merge-krav)

### V1. Aktiv autoritet kontrolleres rett før fase 2 — egen registerhash
Fase 2 (i fenced claim, før hendelsen bygges) re-kontrollerer at
`valgt_verifikator` fortsatt er aktiv og betrodd for ALLE vilkårene, mot
aktivt autoritetsregister. `fase2_id` bindes til:
```
SHA-256(tenant ‖ unntak_id ‖ 'beslutning' ‖ target_action ‖ generation
        ‖ aktiv_policy_hash ‖ aktiv_autoritetsregister_hash ‖ krav_sett_hash)
```
Tilbaketrukket verifikatorautoritet stopper fase 2 SELV om policyinnholdet
er uendret (registerhash endres → gammel fase2_id kan ikke gjenbrukes).

### V2. Skill attestasjonsrevokasjon fra verifikatorautoritet — ærlig kontrakt
PR-007 implementerer IKKE attestasjonsrevokasjon (per-JTI-tilbakekalling).
Ferskhetskontrakten er derfor NØYAKTIG, uten å påstå mer:
```
signatur gyldig ∧ ikke utløpt ∧ innen maks_attestasjon_alder_s
                ∧ verifikator fortsatt autorisert
```
Ingen kode eller dok påstår per-attestasjon-revokasjonssjekk. (Fremtidig:
tenantbundet JTI-revokasjonsregister — deklarert, ikke i v1.)

### V3. Avslappet policyendring — definert utfall
Aktiv policy har FJERNET et vilkår siden snapshot: bygg fase 2 KUN med
attestasjoner aktiv policy fortsatt krever; send ALDRI overflødige gamle
attestasjoner; bind beslutning til aktiv policyhash + aktivt kravsetts
hash; motoren evaluerer mot aktiv policy. (Nye/strengere krav følger
allerede retry/manuell — v6/Scope v2.)

### V4. Deterministisk prioritet er TOTAL
Flere kandidater, lik eksplisitt prioritet → alltid laveste
`verifikator_id` som sekundærnøkkel. Ugyldig prioritering eller ukjent
verifikator → fail-closed (`manuell`). ALDRI avhengig av databaseorden.

## De ti Codex-mergeportene (hver MÅ ha en test som dreper sin vakt)

1. Union har flere verifikatorer, skjæring har én → R1
2. Tom skjæring → `manuell`, null oppdrag
3. Flere kandidater lik prioritet → stabilt laveste `verifikator_id`
4. Autoritet tilbaketrukket etter ingest → fase 2 stoppes
5. Uendret policy, endret autoritetsregister → gammel/gjenbrukt fase2_id omgår ikke kontrollen
6. Policy fjerner et vilkår → gammel attestasjon sendes ikke videre
7. `resultathash` dekker hele kanoniske innholdet, ikke ytre signatur
8. Klient og arbeider kan ikke påvirke valgt verifikator
9. Uautorisert `permanent=true` → ikke direkte `manuell` (behandles negativ)
10. Ingen fler-verifikator-delakkumulering eller `verifikasjonsdel` finnes i v1

## Implementeringsomfang (samlet, endelig form)

- **Migrasjon 007** (kjøreren eier tx, reviewet checksum): statuser
  `venter_verifikasjon`, `verifikasjon_klar`, `verifikasjon_retry_klar`
  + statusmaskin (v4/v7); `verifikasjonsgenerasjon` (aktiv/positiv/negativ/
  utlopt — INGEN konflikt-status) med `valgt_verifikator`,
  `autoritetsregister_versjon`, `krav_sett_hash`, `frist`, `bevis_id`;
  append-only `verifikasjonsbevis` (kryptert, kompositt-nøkkel);
  `unntak.krav_sett JSONB` (frosset, kolonnelåst) + FJERN `ventet_bevis_id`;
  `registrer_verifikasjonsbevis` SECURITY DEFINER (FOR UPDATE, atomisk,
  hele settet i én kvittering); `(handler_id, target_action) →
  utforelsesklasse`-register. INGEN `verifikasjonsdel`-tabell.
- **`platform/core/m37/`:** klassifisering med skjæringsmengde-grense +
  deterministisk verifikatorvalg (Scope v2); innhentbar/ikke-innhentbar +
  `permanent`-håndtering (v7 pkt. 2 + Scope v2 pkt. 5); fase 1 (ett oppdrag,
  hele settet, én verifikator); fase 2 (aktiv-autoritet-revalidering V1 +
  policyendring V3, bygg med komplett sett, → motor, respekter outbox);
  retry-arbeider (generation +1 fenced).
- **`platform/core/oppdragskontrakt.py`:** `vilkaar_sett`-array på
  oppdragstype `verifikasjon`; `verifikasjonskvittering_v1` med ÉN ytre
  JCS-konvolutt over settet (Scope v2 pkt. 3.1); `resultathash` over
  kanonisk konvolutt uten ytre signatur (3.2); `krav_sett` lukket
  versjonert elementskjema (3.3).
- **`api/`:** kvitteringsingest → `registrer_verifikasjonsbevis` med aktiv-
  autoritet-sjekk (V1/Scope v2 pkt. 2); `claim_neste_sak` for
  `verifikasjon_klar` + `verifikasjon_retry_klar`.
- **`deploy/staging/`:** syntetisk verifikator (KUN API-endepunkter, null
  direkte DB-skriving) som poster signert `verifikasjonskvittering_v1`.

## Låserekkefølge (V2 fra PR-006 + denne PR, uten verifikasjonsdel)
```
unntak → verifikasjonsgenerasjon → verifikasjonsbevis → oppdrag → kapabilitet
```
Dokumentert i kode, deadlock-testet under blandet claim/ingest/timeout.

## Etter merge → staging → evidens
Bootstrap → migrasjon 007 → full suite → **feilinjiserings-artefakt**:
injiser `manglende_data`/`attestasjon_mangler` der settet dekkes av én
verifikator → signert kvittering → fase 2 → `løst`. `lost_andel av
reparerbare = 1.0` oppnås FØR gang. Negative avgrensninger i samme
artefakt: fler-verifikator-sak (tom skjæring) → `manuell` uten oppdrag;
verdi-mangel → `manuell`; sideeffekt → `venter_utførelse`. Grønt ⇒ m01s
`feilinjisering_til_unntakskø: ja`.

## Invarianter urørt
Null egne fullmakter (fase 1 attesterer, fase 2 via policy+outbox+motor).
`api/` importerer aldri `m37/`. Én skrivevei til revisjonsloggen. Alle
veier `sett_kontekst` først. Motoren er autoritativ ferskhetsport (v7).
Kjøreren eier migrasjonstransaksjonen.
