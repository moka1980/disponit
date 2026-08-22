#!/usr/bin/env python3
"""Skriver m02-aksepthendelsen — plattformmodulens søsterform av m56-aksepten.

m02 har ingen deploymentrad: identiteten er INNHOLDET (SP-12) —
manifestets commit + sha256 — og hendelsen skrives av
`aksepter_plattformmodul` (054) mot grensen `m02-aksept-v1`. Skriptet er
den TYNNE kalleren rundt basens porter: det verifiserer artefaktene
gjennom evidensporten (delt med m56-aksept.py — én port, aldri to),
binder hver fil til akseptcommiten, lar VERIFIKATOREN attestere bytene
(en annen autentisert identitet enn akseptøren), og lar basen måle alt
den måler selv. Punktsettet leses FRA registeret — skriptet dikter
ingen punkter, det fyller registerets egne med de artefaktene grensens
`grenseverdi` navngir.

Tredelt binding for `revisjonslogg_korrekt` (klarsignalet §2): den delte
r21-målingen refereres VED HASH — artefaktet eies av m56-akseptens
runde, og delingsregelen er at to punkter kan dele en MÅLING, aldri en
type måling. CI-leddene bæres av grensens egen CI-attest.

BRUK (kjøres ETTER merge av bindings-PR-en, fra et utsjekk av `main`):
    sudo -E python3 deploy/staging/m02-aksept.py \
        --runde deploy/staging/artefakter/wcag-kontroll-v2-<ts>.json \
        --suite deploy/staging/artefakter/m02-suite-v1-<ts>.json \
        --fordeling deploy/staging/artefakter/m02-fordeling-v1-<ts>.json \
        --ci-run <workflow-run-id> --ci-commit <sha> [--manifest-commit <sha>]

    Run-ID-en er push-kjøringen på merge-commiten, som for m56-aksepten.
    m56-aksepthendelsen og m02-aksepthendelsen er uavhengige; FLIPPEN av
    m56-manifestet krever begge (egen PR, portene 14–15).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

HER = Path(__file__).resolve().parent
REPO = HER.parent.parent

MODUL = "m02_revisjonslogg"
GRENSE = "m02-aksept-v1"
MANIFEST_REL = "platform/modules/m02_revisjonslogg/manifest.yaml"

#: Hvilket artefakt hvert ordinære artefaktpunkt måles mot: krav_id-en
#: porten skal kjøre filen gjennom, og hvilken kommandolinje-fil (eller
#: repofil) som bærer målingen. Grensens `grenseverdi` navngir eierskapet
#: i klartekst; dette er den maskinlesbare formen av samme kart.
PUNKTKILDER = {
    "feilinjisering_til_unntakskø": ("feilinjisering-m01-v1", "m01_feilinj"),
    "ytelse_bestatt": ("perf-m01-v1", "m01_perf"),
    "rollback_testet": ("rollback-m01-v1", "m01_rollback"),
    "revisjonslogg_korrekt": ("wcag-kontroll-v2", "runde"),
    "tester_gronne_pa_staging": ("m02-suite-v1", "suite"),
    "syntetisk_datasett_likt_lokalt": ("m02-fordeling-v1", "fordeling"),
}

#: De innsjekkede m01-artefaktene — historiske målinger som alt er bundet
#: i m01-manifestet; her gjenbrukes MÅLINGENE (ved hash), aldri typen.
M01_ARTEFAKTER = {
    "m01_feilinj": "deploy/staging/artefakter/"
                   "feilinjisering-m01-v1-20260805T074109.json",
    "m01_perf": "deploy/staging/artefakter/"
                "perf-m01-v1-maal-20260802T205915.json",
    "m01_rollback": "deploy/staging/artefakter/"
                    "rollback-m01-v1-20260805T081921.json",
}


def _m56():
    """m56-aksept.py som modul — evidensporten, CI-porten og
    commit-bindingen er ÉN implementasjon, aldri to som kan drive."""
    spec = importlib.util.spec_from_file_location(
        "m56_aksept", HER / "m56-aksept.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runde", type=Path, required=True)
    ap.add_argument("--suite", type=Path, required=True)
    ap.add_argument("--fordeling", type=Path, required=True)
    ap.add_argument("--ci-run", required=True)
    ap.add_argument("--ci-commit", required=True)
    ap.add_argument("--manifest-commit")
    a = ap.parse_args()

    ma = _m56()
    import os

    import yaml

    # 1. Akseptcommiten og manifestets identitet: bytene hashes HER, og
    #    commit-bindingen beviser at nøyaktig de bytene er sporet ved
    #    commiten — identiteten måles, den påstås ikke (054s eget krav:
    #    verifikatorens attest må så bære samme digest).
    commit = ma.loes_akseptcommit(a.manifest_commit)
    ci_commit = a.ci_commit.lower()
    manifest_bytes = (REPO / MANIFEST_REL).read_bytes()
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    ma.bind_til_commit(commit, MANIFEST_REL, manifest_sha)
    manifest = yaml.safe_load(manifest_bytes.decode("utf-8"))

    # 2. Artefaktene gjennom evidensporten (delt med m56-aksepten):
    #    stien må være NØYAKTIG den m02-MANIFESTET binder for kravet,
    #    sha-en må være manifestets, formen lukket og grensene regnet på
    #    nytt — og hver fil bindes til akseptcommiten (ucommitede bytes
    #    er ikke evidens).
    filer: dict[str, tuple[str, str, dict]] = {}
    for nokkel, sti in (("runde", a.runde), ("suite", a.suite),
                        ("fordeling", a.fordeling)):
        krav_id = next(kid for k, (kid, kn) in PUNKTKILDER.items()
                       if kn == nokkel)
        innhold, sha = ma.les_bundet_artefakt(sti, krav_id, manifest)
        ma.bind_til_commit(commit, ma.repo_rel(sti), sha)
        filer[nokkel] = (ma.repo_rel(sti), sha, innhold)
    for nokkel, rel in M01_ARTEFAKTER.items():
        krav_id = next(kid for k, (kid, kn) in PUNKTKILDER.items()
                       if kn == nokkel)
        innhold, sha = ma.les_bundet_artefakt(REPO / rel, krav_id, manifest)
        ma.bind_til_commit(commit, rel, sha)
        filer[nokkel] = (rel, sha, innhold)

    # 2b. Siste ledd i SP-11-kjeden: råfila bak WCAG-sammendraget.
    #
    #     Codex' P2 (#147, runde 1): `les_bundet_artefakt` validerer
    #     SAMMENDRAGET — stien, manifestets sha, det lukkede skjemaet og
    #     tellerne — men leser aldri JSONL-en `oppsett.kilde` navngir.
    #     m56-veien kaller `verifiser_kilde` og binder råfila til
    #     akseptcommiten; denne kalleren gjorde ingen av delene. Et
    #     sammendrag hvis råfil manglet eller ikke lenger matchet
    #     `oppsett.kilde_sha256` kunne dermed bære den immutable
    #     `revisjonslogg_korrekt`-aksepten, med en kilde-peker til noe
    #     som ikke finnes. Samme port som m56 — én implementasjon,
    #     aldri to som kan drive — så kjeden manifest→sammendrag→råfil
    #     er sha-bundet ledd for ledd, og også råfila er ucommitede
    #     bytes til den er bundet til akseptcommiten.
    runde_innhold = filer["runde"][2]
    evidens_sha = ma.verifiser_kilde(runde_innhold)
    ma.bind_til_commit(commit, runde_innhold["oppsett"]["kilde"], evidens_sha)

    # 3. CI-kjøringen mot GitHub — samme port som m56-aksepten.
    ma.verifiser_ci_kjoring(a.ci_run, ci_commit)

    from db.pg import koble
    if not os.environ.get("DISPONIT_VERIFIKATOR_URL"):
        raise SystemExit(
            "AVBRUTT: DISPONIT_VERIFIKATOR_URL mangler — attesten og"
            " aksepten skal skrives av to autentiserte identiteter.")
    conn = koble(os.environ["DISPONIT_MIGRATOR_URL"])
    vconn = koble(os.environ["DISPONIT_VERIFIKATOR_URL"])
    try:
        # 4. Punktsettet leses FRA registeret. Sentinelene styrer formen:
        #    `<utenfor grensen>` -> skopet med registerets egen
        #    begrunnelse; `<manifestets sha256>` -> identitetspunktet.
        punkter: dict[str, dict] = {}
        attest_pr_fil: dict[str, dict[str, str]] = {}
        for punkt, kt, grense, krav in conn.execute(
                "SELECT punkt, kilde_type, grenseverdi, maalt_krav"
                "  FROM akseptkrav_punkt WHERE krav_id=%s", (GRENSE,)):
            if krav == "<utenfor grensen>":
                punkter[punkt] = {"status": "utenfor_grensen",
                                  "begrunnelse": grense}
                continue
            if krav == "<manifestets sha256>":
                punkter[punkt] = {
                    "status": "maalt", "grenseverdi": grense,
                    "maalt_verdi": manifest_sha, "kilde_type": kt,
                    "kilde_ref": f"{MANIFEST_REL}@sha256:{manifest_sha}"}
                attest_pr_fil.setdefault(
                    f"{MANIFEST_REL}\x00{manifest_sha}", {})[punkt] = \
                    manifest_sha
                continue
            if punkt not in PUNKTKILDER:
                raise SystemExit(
                    f"AVBRUTT: registerpunktet {punkt!r} har ingen kilde i"
                    " skriptets kart — grensen er utvidet uten at kalleren"
                    " fulgte med; det er skriptet som skal rettes")
            _, kilde = PUNKTKILDER[punkt]
            rel, sha, _innhold = filer[kilde]
            punkter[punkt] = {"status": "maalt", "grenseverdi": grense,
                              "maalt_verdi": krav, "kilde_type": kt,
                              "kilde_ref": f"{rel}@sha256:{sha}"}
            attest_pr_fil.setdefault(f"{rel}\x00{sha}", {})[punkt] = krav

        # 5. Verifikatorens referater: én attest per FIL, med punktene
        #    filen bærer — en annen autentisert identitet enn akseptøren
        #    har lest nøyaktig de bytene og skrevet hva de bar.
        for fil_nokkel, fil_punkter in sorted(attest_pr_fil.items()):
            rel, sha = fil_nokkel.split("\x00")
            vconn.execute(
                "SELECT attester_evidensfil(%s,%s,%s,%s::jsonb,"
                "'m02-aksept')",
                (GRENSE, rel, sha, json.dumps(fil_punkter)))
        vconn.commit()

        # 6. CI-attesten — verifikatoren refererer kjøringen porten i §3
        #    nettopp målte mot GitHub.
        arbeidsflyt = conn.execute(
            "SELECT arbeidsflyt FROM akseptkrav_ci WHERE krav_id=%s",
            (GRENSE,)).fetchone()[0]
        vconn.execute(
            "SELECT attester_ci_kjoring(%s,%s,'push','main','success',"
            "%s,'m02-aksept')", (a.ci_run, arbeidsflyt, ci_commit))
        vconn.commit()

        # 7. Hendelsen. Nøkkelen er deterministisk på HELE identiteten
        #    (SP-2: en replay av NØYAKTIG dette innholdet er en no-op;
        #    alt annet på samme nøkkel skal høres).
        #
        #    Codex' P2 (#147, runde 1): den forrige formen var
        #    `m02-aksept-<manifest_sha[:12]>` — bare et prefiks av
        #    manifestdigesten, uten commiten. Aksepteres de SAMME
        #    manifestbytene fra en senere commit (manifestet er
        #    uendret, men en urelatert merge flyttet HEAD), er nøkkelen
        #    identisk mens `manifest_commit`, `ci_run` og `ci_commit`
        #    er andre. `plattformmodulaksept.nokkel` er globalt UNIQUE,
        #    og 054 avviser eksplisitt gjenbruk av en nøkkel med annet
        #    innhold — den andre, fullt gyldige aksepten kunne dermed
        #    aldri skrives. Nøkkelen bærer nå radens egen identitet:
        #    akseptcommiten + HELE digesten, samme kjerne som
        #    `UNIQUE (modul_id, manifest_commit, grense_id)`.
        #    CI-leddene holdes UTENFOR med vilje: et nytt forsøk på
        #    samme commit med en annen kjøring skal treffe 054s klare
        #    «nøkkel gjenbrukt med annet innhold», ikke stille bli en
        #    ny nøkkel som bryter mot radnøkkelen i stedet.
        nokkel = f"m02-aksept-{commit}-{manifest_sha}"
        conn.execute("SET ROLE disponit_modules_admin")
        conn.execute(
            "SELECT aksepter_plattformmodul(%s,%s,%s,%s,%s,%s,%s::jsonb,"
            "%s,'m02-aksept')",
            (MODUL, commit, manifest_sha, GRENSE, a.ci_run, ci_commit,
             json.dumps(punkter), nokkel))
        conn.commit()
        conn.execute("RESET ROLE")
        rad = conn.execute(
            "SELECT akseptert_ts FROM plattformmodulaksept"
            " WHERE modul_id=%s AND manifest_commit=%s",
            (MODUL, commit)).fetchone()
        print(f"AKSEPTERT: ({MODUL}, {commit[:12]}…, {manifest_sha[:12]}…)"
              f" grense={GRENSE} ts={rad[0]}")
    finally:
        vconn.close()
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
