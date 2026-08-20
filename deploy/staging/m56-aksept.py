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
kan bare bevises «grønne da» av kjøringen på akseptcommiten. Den
kjøringen slås opp og må være ferdig, grønn og kjørt på nettopp den
commiten; `--ci-commit` er derfor akseptcommiten, ikke en fri streng.

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
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HER = Path(__file__).resolve().parent
REPO = HER.parents[1]
sys.path.insert(0, str(REPO / "platform/core"))

MODUL = "m_wcag_audit"   # registernavnet (modulmappen heter m56_wcag_audit)
MILJO = "staging"
KRAV = "wcag-kontroll-v1"
DRILLKRAV = "rollback-m56-v1"
TENANT = "t-wcagfasit"
MANIFEST_REL = "platform/modules/m56_wcag_audit/manifest.yaml"
MANIFEST_STI = REPO / MANIFEST_REL
#: Workflowen invariantpunktene faktisk hviler på. Repoet har flere
#: workflows, og en grønn kjøring av en ANNEN på samme commit beviser
#: ingenting om testene her (Codex, #117).
CI_WORKFLOW = ".github/workflows/ci.yml"

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


def les_manifest() -> tuple[dict, str]:
    """Manifestet og sha256 av de BYTENE som ble tolket. -> (innhold, sha)."""
    import yaml
    raa = MANIFEST_STI.read_bytes()
    return (yaml.safe_load(raa.decode("utf-8")),
            hashlib.sha256(raa).hexdigest())


def _git(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *argv], capture_output=True)


def loes_akseptcommit(oppgitt: str | None) -> str:
    """Commiten akseptraden skal peke på — full sha, og den må finnes.

    `--manifest-commit` var før en helt ukontrollert streng: hva som helst
    kunne skrives inn i den immutable raden. Nå må verdien peke på en
    faktisk commit i dette repoet før noe måles mot den.
    """
    ref = oppgitt or "HEAD"
    r = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    sha = r.stdout.decode().strip()
    if r.returncode != 0 or len(sha) != 40:
        raise SystemExit(f"AVBRUTT: manifest-commit {ref!r} er ingen commit i"
                         " dette repoet")
    return sha


def bind_til_commit(commit: str, rel: str, sha: str) -> None:
    """De validerte bytene må VÆRE bytene commiten inneholder.

    Codex' P1 på PR #117 (runde 2): hash-, skjema- og grensekontrollene
    hadde ARBEIDSTREET som tillitsrot, mens `manifest_commit` var en
    ukontrollert CLI-verdi eller bare `HEAD`. Endres manifestet og
    artefaktene sammen før kommandoen kjøres, passerer alt — og
    akseptraden peker på en commit som ikke inneholder én eneste av de
    bytene den påstår å bevise. Her måles hvert ledd i kjeden
    (manifest → artefakt → råfil) mot commitens egne blober.
    """
    r = _git("cat-file", "blob", f"{commit}:{rel}")
    if r.returncode != 0:
        raise SystemExit(f"AVBRUTT: {rel} finnes ikke i {commit[:12]}… —"
                         " akseptraden ville pekt på en commit uten dette"
                         " beviset")
    i_commit = hashlib.sha256(r.stdout).hexdigest()
    if i_commit != sha:
        raise SystemExit(f"AVBRUTT: {rel} i arbeidstreet er ikke fila i"
                         f" {commit[:12]}… — {sha[:12]}… mot"
                         f" {i_commit[:12]}…; ucommitede bytes er ikke evidens")


def _repo_slug() -> str:
    r = _git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?$",
                  r.stdout.decode().strip())
    if r.returncode != 0 or not m:
        raise SystemExit("AVBRUTT: finner ingen GitHub-remote å slå"
                         " CI-kjøringen opp mot")
    return m.group(1)


def _hent_ci_kjoring(run_id: str) -> dict:
    """Kjøringen slik GitHub kjenner den — eller avbrudd. Fail-closed:
    et punkt ingen bekreftet kjøring bærer, skrives ikke."""
    slug = _repo_slug()
    url = f"https://api.github.com/repos/{slug}/actions/runs/{run_id}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "m56-aksept"})
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=30) as svar:
            return json.loads(svar.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"AVBRUTT: CI-kjøring {run_id} kan ikke slås opp i"
                         f" {slug} (HTTP {e.code}) — en kjøring som ikke"
                         " finnes, beviser ingenting")
    except (OSError, ValueError) as e:
        raise SystemExit(f"AVBRUTT: oppslaget av CI-kjøring {run_id} feilet"
                         f" ({type(e).__name__}) — invariantpunktene kan ikke"
                         " skrives uten at kjøringen er bekreftet grønn")


def _vurder_ci_kjoring(data: dict, run_id: str, ci_commit: str) -> list[str]:
    """De fire påstandene invariantpunktene hviler på, målt på svaret."""
    feil: list[str] = []
    if str(data.get("id")) != str(run_id):
        feil.append(f"oppslaget svarte med kjøring {data.get('id')!r}, ikke"
                    f" {run_id}")
    # Codex' P1 på PR #117 (runde 3): id, terminalstatus, conclusion og
    # sha sa ingenting om HVILKEN workflow som kjørte. Repoet har også
    # `claude.yml`, og en grønn kjøring DERFRA på akseptcommiten kjører
    # ikke én eneste av invarianttestene punktene påberoper seg — like
    # fullt bar den alle 16. Punktene hviler på testene i `ci.yml`, så
    # det er den workflowen som må ha kjørt.
    if (data.get("path") or "") != CI_WORKFLOW:
        feil.append(f"kjøringen er {data.get('path') or '?'}, ikke"
                    f" {CI_WORKFLOW} — bare invarianttestene der bærer"
                    " punktene")
    if data.get("status") != "completed":
        feil.append(f"status={data.get('status')!r} — kjøringen er ikke ferdig")
    if data.get("conclusion") != "success":
        feil.append(f"conclusion={data.get('conclusion')!r} — bare en grønn"
                    " kjøring bærer punktene")
    if (data.get("head_sha") or "") != ci_commit:
        feil.append(f"kjøringen testet {(data.get('head_sha') or '?')[:12]}…,"
                    f" ikke akseptcommiten {ci_commit[:12]}…")
    return feil


def verifiser_ci_kjoring(run_id: str, ci_commit: str) -> dict:
    """CI-kjøringen bak de 16 invariantpunktene. -> kjøringen.

    Codex' P1 på PR #117 (runde 2): punktene ble hardkodet grønne fra to
    strenger kalleren skrev. En skrivefeil, en gammel kjøring eller en RØD
    kjøring ga like fullt 16 immutable «grønne» punkter — en kjøring
    ingen har sett bevise noe som helst. Nå slås kjøringen opp: den må
    finnes i dette repoet, være `ci.yml` (runde 3: en grønn
    `claude.yml`-kjøring på samme commit kjører ingen av
    invarianttestene), være FERDIG og GRØNN, og ha testet nøyaktig
    akseptcommiten (klarsignalets §2.5: «run-ID + commit-sha på
    akseptcommiten»).
    """
    if not re.fullmatch(r"[0-9]{1,20}", run_id):
        raise SystemExit(f"AVBRUTT: --ci-run {run_id!r} er ingen"
                         " workflow-run-id")
    data = _hent_ci_kjoring(run_id)
    feil = _vurder_ci_kjoring(data, run_id, ci_commit)
    if feil:
        raise SystemExit(f"AVBRUTT: CI-kjøring {run_id} bærer ikke"
                         " invariantpunktene:\n  " + "\n  ".join(feil))
    return data


def repo_rel(sti: Path, hva: str = "artefaktet") -> str:
    """Repo-relativ, POSIX-normalisert sti — eller avbrudd."""
    try:
        return sti.resolve().relative_to(REPO).as_posix()
    except ValueError:
        raise SystemExit(f"AVBRUTT: {hva} {sti} peker utenfor repoet — bare"
                         " innsjekkede, manifestbundne artefakter aksepterer"
                         " noe")


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
    rel = repo_rel(sti)
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


def drillens_maaletid(drill: dict) -> "datetime":
    """Da drillen FAKTISK kjørte — artefaktets egen `ts`. -> datetime.

    Codex' P2 på PR #117 (runde 3): `moduldrill.utfort_ts` sto med
    `DEFAULT now()` og fikk aldri en verdi, så en drill kjørt timer eller
    dager før aksepten ble skrevet inn som om den kjørte i
    akseptøyeblikket. Måletiden er drillartefaktets, registreringstiden
    er basens — to fakta, to kolonner. Her leses det første, med
    tidssone: en naiv tidsstempelstreng er ikke et øyeblikk.
    """
    raa = drill.get("ts")
    try:
        ts = datetime.fromisoformat(str(raa))
    except (TypeError, ValueError):
        raise SystemExit(f"AVBRUTT: drillartefaktets ts {raa!r} er ingen"
                         " ISO-8601-tid — måletiden kan ikke skrives")
    if ts.tzinfo is None:
        raise SystemExit(f"AVBRUTT: drillartefaktets ts {raa!r} mangler"
                         " tidssone — et øyeblikk uten sone er ikke et"
                         " øyeblikk")
    if ts > datetime.now(timezone.utc):
        raise SystemExit(f"AVBRUTT: drillartefaktets ts {raa!r} ligger fram"
                         " i tid — det er en påstand om framtiden, ikke en"
                         " måling")
    return ts


def verifiser_modul(art: dict, hva: str) -> None:
    """Artefaktet må navngi MODULEN som aksepteres, ikke en annen.

    Codex' P2 på PR #117 (runde 3): runde-sammendraget kalte modulen
    `m56_wcag_audit` — KATALOGNAVNET — mens registeret, drillen, 049-radene
    og dette skriptet bruker `m_wcag_audit`. Skjemaet krevde bare en
    ikke-tom streng, og skriptet sammenlignet aldri feltet med `MODUL`, så
    et sammendrag som navnga en helt annen modul ble evidens for en
    immutabel `m_wcag_audit`-aksept. Feltet er nå bundet i skjemaet OG målt
    her: to lag, samme sannhet.
    """
    navn = (art.get("oppsett") or {}).get("modul")
    if navn != MODUL:
        raise SystemExit(f"AVBRUTT: {hva} gjelder modul {navn!r}, ikke"
                         f" {MODUL!r} — evidens for en annen modul aksepterer"
                         " ingenting her")


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
    manifest_commit = loes_akseptcommit(a.manifest_commit)
    manifest, manifest_sha = les_manifest()
    kjedefeil = ms.valider_artefakter(manifest)
    if kjedefeil:
        raise SystemExit("AVBRUTT: manifestets evidenskjede er rød:\n  "
                         + "\n  ".join(kjedefeil))
    drill, drill_sha = les_bundet_artefakt(a.drill, DRILLKRAV, manifest)
    runde, runde_sha = les_bundet_artefakt(a.runde, KRAV, manifest)
    verifiser_modul(drill, "drillartefaktet")
    verifiser_modul(runde, "runde-sammendraget")
    drill_ts = drillens_maaletid(drill)
    evidens_sha = verifiser_kilde(runde)
    # …og hele den kjeden bindes til commiten raden faktisk skriver:
    # manifestet (tillitsroten), begge artefaktene og råfilen bakerst.
    for rel, sha in ((MANIFEST_REL, manifest_sha),
                     (repo_rel(a.drill), drill_sha),
                     (repo_rel(a.runde), runde_sha),
                     (runde["oppsett"]["kilde"], evidens_sha)):
        bind_til_commit(manifest_commit, rel, sha)
    # Invariantpunktene har ingen historiske rader: de hviler HELT på at
    # CI-kjøringen finnes, er grønn og testet akseptcommiten.
    ci_commit = loes_akseptcommit(a.ci_commit)
    if ci_commit != manifest_commit:
        raise SystemExit(f"AVBRUTT: --ci-commit {ci_commit[:12]}… er ikke"
                         f" akseptcommiten {manifest_commit[:12]}… — punktene"
                         " påberoper seg «grønn CI på akseptcommiten», og da"
                         " må det være den kjøringen som måles")
    verifiser_ci_kjoring(a.ci_run, ci_commit)

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
                          "kilde_ref": f"run {a.ci_run} @ {ci_commit}"}
    punkter["malautorisasjon.positiv_sti_virker"] = {
        "grenseverdi": "ja", "maalt_verdi": "ja",
        "kilde_type": "ci_kjoring",
        "kilde_ref": f"run {a.ci_run} @ {ci_commit}"}

    from db.pg import koble
    conn = koble(os.environ["DISPONIT_MIGRATOR_URL"])
    try:
        conn.execute("SET ROLE disponit_modules_admin")
        o = drill["oppsett"]
        drill_id = conn.execute(
            "SELECT registrer_moduldrill(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "'m56-aksept',%s)",
            (MODUL, MILJO, o["drillet_release"], o["rullback_release"],
             o["kandidat_release"],
             drill["maalt"]["nye_oppdrag_claimet_av_drillet_release"] == 0,
             drill["maalt"]["falske_verdikter"] == 0,
             drill["maalt"]["kandidat_promoterte_artefakter"] >= 1,
             f"drill-{o['kandidat_release']}", drill_ts)).fetchone()[0]
        conn.execute(
            "SELECT aksepter_moduldeployment(%s,%s,%s,%s,%s,%s,%s::uuid,"
            "%s,%s,%s,%s,%s::jsonb,%s,'m56-aksept')",
            (MODUL, MILJO, o["kandidat_release"], drill_id, KRAV,
             TENANT, a.e2e_artefakt, evidens_sha, manifest_commit,
             a.ci_run, ci_commit, json.dumps(punkter),
             f"aksept-{o['kandidat_release']}"))
        conn.commit()
        # Codex' P1 på PR #117 (runde 2): kvitteringslesningen kjørte
        # fortsatt som `disponit_modules_admin`, og 049 gir den rollen
        # BARE `EXECUTE` på de to definerne — `SELECT` på tabellen har
        # eier og runtime, ikke admin. Raden var altså skrevet og
        # committet, hvorpå denne lesningen ga `permission denied`: både
        # kjøringen og hvert eneste forsøk på nytt rapporterte feil på en
        # aksept som ALT lå der. Fullmakten legges ned når den er brukt —
        # kvitteringen leses som migrator (samme grep som drillskriptets
        # etterkontroll).
        conn.execute("RESET ROLE")
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
