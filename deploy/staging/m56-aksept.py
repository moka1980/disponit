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
TENANT = "t-wcagfasit"

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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drill", type=Path, required=True)
    ap.add_argument("--runde", type=Path, required=True)
    ap.add_argument("--e2e-artefakt", required=True)
    ap.add_argument("--ci-run", required=True)
    ap.add_argument("--ci-commit", required=True)
    ap.add_argument("--manifest-commit")
    a = ap.parse_args()

    drill = json.loads(a.drill.read_text(encoding="utf-8"))
    runde = json.loads(a.runde.read_text(encoding="utf-8"))
    if not (drill.get("bestatt") and runde.get("bestatt")):
        raise SystemExit("AVBRUTT: et rødt artefakt aksepterer ingenting")
    evidens_sti = REPO / runde["oppsett"]["kilde"]
    evidens_sha = hashlib.sha256(evidens_sti.read_bytes()).hexdigest()
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
