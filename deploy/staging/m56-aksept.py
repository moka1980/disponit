#!/usr/bin/env python3
"""Akseptflippen for m56 — skriver drillraden og aksepthendelsen (049).

Kjøres PÅ verten ETTER at migrasjon 049 er deployet. Skriptet finner
selv opp ingenting: hvert tall og hver referanse leses fra de
INNSJEKKEDE, sha256-bundne artefaktene (drillartefaktet og
runde-sammendraget) og fra basen — og lagringen håndhever resten
(FK-kjedene i 049: drill for nøyaktig denne deploymentraden, promotert
E2E-artefakt fra samme release, komplett punktsett eller ingen
hendelse).

Punktobservasjonene (A3) bygges fra kravpunkt-registeret i basen:
måletallene fra runde-sammendraget der de finnes, CI-kjøringen som
kilde for de rene invariantpunktene (skjema.*, egress-tokenet,
malautorisasjonens negativporter) — de har ingen historiske rader og
kan bare bevises «grønne da» av kjøringen på akseptcommiten.

BRUK:
    sudo -E python3 deploy/staging/m56-aksept.py \
        --drill deploy/staging/artefakter/rollback-m56-v1-<ts>.json \
        --runde deploy/staging/artefakter/wcag-kontroll-v1-<ts>.json \
        --e2e-artefakt <uuid fra drillens kandidat-kjøring> \
        --ci-run <workflow-run-id> --ci-commit <sha> [--manifest-commit <sha>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HER = Path(__file__).resolve().parent
REPO = HER.parents[1]
sys.path.insert(0, str(REPO / "platform/core"))

MODUL = "m_wcag_audit"   # registernavnet (modulmappen heter m56_wcag_audit)
MILJO = "staging"
KRAV = "wcag-kontroll-v1"
DRILLKRAV = "rollback-m56-v1"
TENANT = "t-wcagfasit"
MANIFEST_STI = REPO / "platform/modules/m56_wcag_audit/manifest.yaml"

#: grensepunkt → (kilde_type, hvordan verdien hentes). Måletallene peker
#: på runde-sammendraget (selv sha-bundet i manifestet); de rene
#: invariantpunktene bærer CI-kjøringen som kilde. Grensene er §12s.
MAALTE = {
    "kontroll.ti_kjoringer_signert_innen_frist":
        ("10/10", lambda m: f"{m['kjoringer_signert_innen_frist']}"
                            f"/{m['kjoringer_krav']}"),
    "funn.avvik_mot_fasit": ("0", lambda m: str(m["avvik_mot_fasit"])),
    "robots.brudd_i_mallogg":
        ("0", lambda m: str(m["robots_private_forisporsler"])),
    # 0 utførte over grensen — og taket er MÅLT (minst ett faktisk
    # avslag), ellers er «0» bare fravær av forsøk.
    "frekvens.over_grense_utfort":
        ("0", lambda m: "0" if m["frekvens_avvist_over_grense"] >= 1
                        else "umålt (taket ga aldri avslag)"),
    "egress.proxytoken_til_ikke_ekstern_lesing":
        ("0", lambda m: str(m["egress_lekkasjer"])),
}
CI_PUNKTER = (
    "skjema.brudd_promotert", "skjema.hash_uten_rad_akseptert",
    "skjema.mutert_ureferert", "skjema.slettet",
    "rapport.uten_pakrevd_arlighetsfelt_akseptert",
    "rapport.klartekst_i_logg_eller_dump",
    "domene.kontroll_uten_verifisering",
    "payload.felt_utover_skjema_utlevert",
    "deploy.registerrad_uten_kodefestet_type",
    "deploy.ekstern_lesing_uten_malautorisasjonsflagg",
    "klasse.eksisterende_kontrakt_omklassifisert",
    "klasse.aktivering_uten_frekvensgrense_lyktes",
    "klasse.aktivering_uten_malautorisasjon_lyktes",
    "malautorisasjon.ikke_registrert_vilkar_talte",
    "malautorisasjon.feil_maldomene_godtatt",
)


def les_manifest() -> dict:
    import yaml
    return yaml.safe_load(MANIFEST_STI.read_text(encoding="utf-8"))


def les_bundet_artefakt(sti: Path, krav_id: str,
                        manifest: dict) -> tuple[dict, str]:
    """Artefaktet gjennom HELE evidensporten. -> (innhold, sha256).

    Codex' P1 på PR #117: den forrige formen leste `bestatt` — et felt
    KALLEREN kontrollerer — og skrev deretter en immutabel, grønn
    drill- og akseptrad. En håndskrevet JSON-fil med `bestatt: true` og
    passende tellere nådde altså de priviligerte funksjonene, og hele
    evidensgrensen skriptet finnes for var omgått av en tekstredigerer.

    Fire lag, alle FØR transaksjonen åpnes:

    1. stien må ligge i repoet og være NØYAKTIG den fila manifestet
       binder for `krav_id` — en lokalt endret eller fremmed fil er
       ikke evidens uansett hva den inneholder;
    2. sha256 av de leste bytene må være manifestets (`_les_artefakt`
       hasher og tolker ETT lesesteg, så det er samme bytes begge veier);
    3. det lukkede JSON-skjemaet (`additionalProperties: false`);
    4. `_sjekk_grenser` — som REGNER UT invariantene på nytt i stedet
       for å tro på `bestatt`, og som håndhever §12-grensene.
    """
    import manifestskjema as ms
    try:
        rel = sti.resolve().relative_to(REPO).as_posix()
    except ValueError:
        raise SystemExit(f"AVBRUTT: {sti} peker utenfor repoet — bare"
                         " innsjekkede, manifestbundne artefakter aksepterer"
                         " noe")
    bundet = {p["artefakt_sha256"] for p in
              (manifest.get("staging_sjekkliste") or {}).values()
              if isinstance(p, dict) and p.get("status") == "ja"
              and p.get("krav_id") == krav_id and p.get("artefakt") == rel
              and p.get("artefakt_sha256")}
    if not bundet:
        raise SystemExit(f"AVBRUTT: {rel} er ikke artefaktet manifestet"
                         f" binder for {krav_id}")
    data, sha, melding = ms._les_artefakt(sti)
    if melding:
        raise SystemExit(f"AVBRUTT: {rel}: {melding}")
    if sha not in bundet:
        raise SystemExit(f"AVBRUTT: {rel} er endret — manifestet binder"
                         f" {sorted(bundet)[0][:12]}…, filen er {sha[:12]}…")
    feil = (ms.valider_artefaktformat(data, krav_id)
            + ms._sjekk_grenser(krav_id, data))
    if feil:
        raise SystemExit(f"AVBRUTT: {rel} består ikke evidensporten:\n  "
                         + "\n  ".join(feil))
    return data, sha


def verifiser_kilde(runde: dict) -> str:
    """Råfilen bak sammendraget. -> sha256 av de faktiske bytene.

    Sammendraget er avledet av `evidens.jsonl`; binder det en råfil som
    ikke lenger er den, er `kilde_sha256` i akseptraden en peker til noe
    som ikke finnes. Kjeden manifest→sammendrag→råfil skal være sha-bundet
    ledd for ledd (SP-11), og siste ledd måles her.
    """
    kilde = (runde.get("oppsett") or {}).get("kilde")
    forventet = (runde.get("oppsett") or {}).get("kilde_sha256")
    if not kilde or not forventet:
        raise SystemExit("AVBRUTT: sammendraget mangler kilde/kilde_sha256")
    sti = (REPO / kilde).resolve()
    try:
        sti.relative_to(REPO)
    except ValueError:
        raise SystemExit(f"AVBRUTT: kilden {kilde} peker utenfor repoet")
    try:
        sha = hashlib.sha256(sti.read_bytes()).hexdigest()
    except OSError as e:
        raise SystemExit(f"AVBRUTT: kilden {kilde} kan ikke leses"
                         f" ({type(e).__name__})")
    if sha != forventet:
        raise SystemExit(f"AVBRUTT: {kilde} er ikke råfilen sammendraget"
                         f" binder — {forventet[:12]}… mot {sha[:12]}…")
    return sha


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drill", type=Path, required=True)
    ap.add_argument("--runde", type=Path, required=True)
    ap.add_argument("--e2e-artefakt", required=True)
    ap.add_argument("--ci-run", required=True)
    ap.add_argument("--ci-commit", required=True)
    ap.add_argument("--manifest-commit")
    a = ap.parse_args()

    import manifestskjema as ms

    # Ingenting skrives før hele evidenskjeden er målt: manifestets egen
    # port (hash → skjema → grenser → hvilken måling punktet påberoper
    # seg), så de to artefaktene kalleren faktisk sendte inn, så råfilen.
    manifest = les_manifest()
    kjedefeil = ms.valider_artefakter(manifest)
    if kjedefeil:
        raise SystemExit("AVBRUTT: manifestets evidenskjede er rød:\n  "
                         + "\n  ".join(kjedefeil))
    drill, _ = les_bundet_artefakt(a.drill, DRILLKRAV, manifest)
    runde, _ = les_bundet_artefakt(a.runde, KRAV, manifest)
    evidens_sha = verifiser_kilde(runde)
    manifest_commit = a.manifest_commit or subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()

    m = runde["maalt"]
    punkter = {}
    runde_ref = (f"{runde['oppsett']['kilde']}"
                 f"@sha256:{evidens_sha[:16]}")
    for punkt, (grense, hent) in MAALTE.items():
        punkter[punkt] = {"grenseverdi": grense, "maalt_verdi": hent(m),
                          "kilde_type": "evidensfil",
                          "kilde_ref": runde_ref}
    for punkt in CI_PUNKTER:
        punkter[punkt] = {"grenseverdi": "0 (porttest rød ved brudd)",
                          "maalt_verdi": "0 (grønn CI på akseptcommiten)",
                          "kilde_type": "ci_kjoring",
                          "kilde_ref": f"run {a.ci_run} @ {a.ci_commit}"}
    punkter["malautorisasjon.positiv_sti_virker"] = {
        "grenseverdi": "ja", "maalt_verdi": "ja",
        "kilde_type": "ci_kjoring",
        "kilde_ref": f"run {a.ci_run} @ {a.ci_commit}"}

    import os

    from db.pg import koble
    conn = koble(os.environ["DISPONIT_MIGRATOR_URL"])
    try:
        conn.execute("SET ROLE disponit_modules_admin")
        o = drill["oppsett"]
        drill_id = conn.execute(
            "SELECT registrer_moduldrill(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "'m56-aksept')",
            (MODUL, MILJO, o["drillet_release"], o["rullback_release"],
             o["kandidat_release"],
             drill["maalt"]["nye_oppdrag_claimet_av_drillet_release"] == 0,
             drill["maalt"]["falske_verdikter"] == 0,
             drill["maalt"]["kandidat_promoterte_artefakter"] >= 1,
             f"drill-{o['kandidat_release']}")).fetchone()[0]
        conn.execute(
            "SELECT aksepter_moduldeployment(%s,%s,%s,%s,%s,%s,%s::uuid,"
            "%s,%s,%s,%s,%s::jsonb,%s,'m56-aksept')",
            (MODUL, MILJO, o["kandidat_release"], drill_id, KRAV,
             TENANT, a.e2e_artefakt, evidens_sha, manifest_commit,
             a.ci_run, a.ci_commit, json.dumps(punkter),
             f"aksept-{o['kandidat_release']}"))
        conn.commit()
        rad = conn.execute(
            "SELECT akseptert_ts FROM modulaksept WHERE modul_id=%s"
            " AND miljo=%s AND release_id=%s",
            (MODUL, MILJO, o["kandidat_release"])).fetchone()
        print(f"AKSEPTERT: ({MODUL}, {MILJO}, {o['kandidat_release']})"
              f" drill_id={drill_id} ts={rad[0]}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
