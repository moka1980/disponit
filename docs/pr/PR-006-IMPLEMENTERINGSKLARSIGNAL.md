# PR-006 — IMPLEMENTERINGSKLARSIGNAL (GO fra ChatGPT, alle fire runder)

**Til Claude Code · Implementér mot v1 + v2 + v3 + v4 fra gjeldende main
(bygg fra din siste merge-commit, ikke eldre øyeblikksbilde).
Branch: `pr-006-m37-behandling`. GO-en og de tre vilkårene limes inn i
PR-beskrivelsen som merge-krav.**

## De tre implementasjonsvilkårene (bindende)

### V1. Harmoniser kapabilitetens tidsgrenser
Håndhev invariant i kode OG i kapabilitetstabellens CHECK der mulig:
```
reservasjon_utloper ≤ kapabilitet_utloper ≤ claim_utloper
```
Reservasjon kan ALDRI gjenopptas etter kapabilitets- eller claim-utløp.
Da må gjeldende claim avsluttes/re-claimes og NY kapabilitet utstedes med
SAMME stabile `repair_operation_id`. (Retter v3s 60 s mot v4s 5 min:
5-minutters `reservert`-timeout gjelder kun frigjøring til `feilet`, og
kan aldri overstige claim-leasen — clamp i frigjøringsfunksjonen.)

### V2. Historisk policyoppslag bruker HELE identiteten
Backfill-oppslag: `tenant + policy_id + versjon + innholds_hash` (ikke
bare tenant/versjon/hash). Re-hash lagret innhold, sammenlign mot
revisjonsloggen FØR `maks_auto_forsok` brukes. Avvik → legacy + `manuell`.

### V3. Policyretention håndheves i migrasjon 004 (ikke bare dokumentert)
DB-vakt (trigger på `policyer` BEFORE DELETE) som avviser sletting når
policyversjonen refereres av: revisjonslogg, ikke-terminale unntak,
oppdrag eller reparasjonsoperasjoner. **Gjelder også migratorrollen** —
kun en eksplisitt, separat arkiv-/migrasjonsprosedyre kan omgå den.
Negativ test beviser at migrator ikke kan slette en referert policy.

## De ti Codex-portene (hver MÅ ha en test som dreper sin vakt)

1. Kapabilitet kan ikke brennes uten auditert beslutning (krasj-test mellom pre-auth og commit → kapabilitet forblir `reservert`, gjenopptas)
2. Annen `request_id` kan ikke overta en reservasjon
3. Utløpt claim kan ikke bruke/fullføre kapabiliteten (invariant V1)
4. Utdatert owner-generation kan aldri avslutte automatisk
5. Identisk kvittering idempotent; motstridende → sikkerhetssak
6. Eiermodul mottar aldri ciphertext, DEK eller KEK (canary + statisk sjekk)
7. Payloadfelt utenfor oppdragsskjemaet returneres aldri (lukket skjema)
8. Legacy-policy uten verifiserbar historisk rad → `manuell`
9. Referert policyversjon kan ikke slettes (invariant V3, inkl. migrator)
10. Syntetisk eiermodul bruker kun ordinære API-endepunkter, null direkte DB-skriving (statisk sjekk av staging-selen)

## Implementeringsomfang (fra v1–v4, oppsummert)

- **Migrasjon 004:** status `manuell` + `venter_utførelse` i unntak-CHECK og statusmaskin; `claim_generation`, `maks_auto_forsok_snapshot`, `policy_versjon`, `policy_content_hash` på unntak (backfill FØR NOT NULL per v3 pkt.7 + v4 pkt.5 + V2); tabeller `oppdrag`, `arbeidskapabiliteter`; claim-/kapabilitets-/kvitterings-SECURITY DEFINER-funksjoner (NOLOGIN-eiere, search_path=pg_catalog, REVOKE ALL FROM PUBLIC); policyretention-trigger (V3). Kjøreren eier transaksjonen (ingen BEGIN/COMMIT i fila); reviewet checksum til bootstrap-registeret.
- **`platform/core/m37/`:** `arbeider.py` (claim-løkke, egen prosess/systemd — aldri i api/), `reparasjoner.py` (R1/R2/R3, lukket register), `taksonomi.py` (`M37_TAKSONOMI_V1` frozenset + handler-deklarasjoner), `oppdragsskjema.py` (lukket felt-whitelist per oppdragstype).
- **`platform/core/api/`:** endepunktene `/v1/oppdrag/claim` og `/v1/oppdrag/kvittering`; kapabilitetsinnløsning i pre-auth-veien (reserver→bruk, commit-bundet); API-side dekryptering + dataminimering. Ingen ny runtime-GRANT uten begrunnelse.
- **`deploy/staging/`:** syntetisk eiermodul (staging-only, egen registrert verifikatornøkkel, KUN de to ordinære endepunktene), systemd-unit for arbeideren.
- **KRAVGRENSER (`manifestskjema.py`):** `feilinjisering-m01-v1` (v3 pkt.8, revidert v2 §8) og `rollback-m01-v1` (fra PR-006 v1 §5) — begge FØR arbeidet som måles.
- **JCS:** `attestering.kanonisk_bytes` → RFC 8785, `kanonisering`-felt påkrevd på nettverksveien, RFC-testvektorer, `default=str` fjernet.
- **Invarianter 1–6 fra brief-en:** urørt. `api/` importerer aldri `m37/` (statisk sjekk). Én skrivevei til revisjonsloggen. `sett_kontekst` først på alle veier inn — nå også arbeideren og oppdragsveien.

## Testkrav (utover de ti portene)
Én test per akseptansekriterium i v7.2 for M-37; minst én negativ test som beviser at handling utenfor policy stoppes; de åtte evidensbevisene for syntetisk eiermodul (v3); lease-tap/re-claim-kappløp (20 arbeidere); JCS-vektorer + avvisning av ikke-JCS; backfill-tester (evidens funnet → snapshot; evidens mangler → manuell).

## Etter merge → staging
Bootstrap → migrasjon 004 → full suite → feilinjiserings-artefakt gjennom
evidensporten (de åtte bevisene) → hvis grønt: m01s siste blokkerte
sjekklistepunkt (`feilinjisering_til_unntakskø`) er løst.
