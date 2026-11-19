#!/usr/bin/env python3
"""M-31s sertifiseringslest — klasse H i ADR-007 (plattformport).

M-31 er golden-sett-porten på releasebyttene: tre dører som bare
plattformadministratoren når (registrer sett, sett krav, registrer
kjøring), en port INNE i `bytt_release`, og en egen append-only
hendelsesstrøm (`modellstyring_hendelse`). Registeret er globalt — ingen
tenant, ingen RLS — så fasit, feilinjisering og rollback rigges i den
ISOLERTE basen på 55432. Riggen går testsuitens egne hjelpere
(`test_m31_modellstyring`), som derfor står i bevisroten.

  fasit           syntetisk_datasett_likt_lokalt + revisjonslogg_korrekt.
                  Golden-settenes kanoniske hash regnes av repoets filer og
                  sammenlignes med hashene DRIFTSBASEN har registrert
                  (pinnet i grensen). Settet registreres gjennom den ekte
                  CLI-en, og en kjent dørsekvens gir kjente portutfall.
                  Hver dørovergang står som én hendelse med aktør.
  feilinjisering  porten feiler SYNKRONT og lukket: bytte uten bestått
                  kjøring, kjøring mot ukjent sett, delvis kjøring,
                  sett-immutabilitet og hash-avvik FØR første modellkall.
                  Typet avvisning, uten spor.
  rollback        forgjengerens og kandidatens kjerne regner samme hash og
                  samme score av samme sett; porten svarer likt etterpå.
  ytelse          en EKTE evaluering mot vertens Ollama: p95 per eksempel
                  mot en pinnet grense, ingen modellfeil.

BRUK (på verten, som administrator, med riggens DSN-er eksportert):
  python deploy/staging/m31-lest.py fasit|feilinjisering
  python deploy/staging/m31-lest.py rollback --forgjenger /opt/disponit/releases/<sha>
  python deploy/staging/m31-lest.py ytelse --modell-url http://127.0.0.1:11434 \
      --modellnavn qwen2.5:3b-instruct --digest <manifest-sha256>
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
os.environ.setdefault("DISPONIT_KEK", "b" * 64)
os.environ.setdefault("DISPONIT_TOKEN_PEPPER", "p" * 40)
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (M31_DRIFTSHASHER, M31_MODULFILER,  # noqa: E402
                            _sjekk_grenser, kjerne_digest,
                            m31_bevisrot_sha256, release_digest,
                            valider_artefaktformat)

UT = REPO / "deploy/staging/artefakter"
SETT_ID = "m57-golden"
SETTFIL = {v: REPO / f"deploy/staging/m57-golden-v{v}.json" for v in (1, 2)}
TABELLER = ("golden_sett", "evalueringskrav", "evalueringskjoring",
            "modellstyring_hendelse")
#: Dørsekvensens portutfall, i rekkefølge (se `fasit`).
UTFALL_VENTET = ["byttet", "nektet", "byttet", "nektet", "nektet"]
HENDELSER_VENTET = {"sett_registrert": 1, "krav_satt": 2,
                    "kjoring_registrert": 2}


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def _env(navn: str) -> str:
    v = os.environ.get(navn)
    if not v:
        sys.exit(f"{navn} mangler — kjør med riggens DSN-er eksportert")
    return v


def _skriv(krav: str, art: dict) -> Path:
    art["krav_id"] = krav
    art["ts"] = datetime.now(timezone.utc).isoformat()
    art.setdefault("feil", [])
    art["bestatt"] = True
    art["feil"] = sorted(set(art["feil"]
                             + valider_artefaktformat(art, krav)
                             + _sjekk_grenser(krav, art)))
    art["bestatt"] = not art["feil"]
    UT.mkdir(parents=True, exist_ok=True)
    sti = UT / f"{krav}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    sti.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                              sort_keys=True) + "\n", encoding="utf-8")
    _log(f"skrev {sti} (bestatt={art['bestatt']})")
    for f in art["feil"]:
        _log("  FEIL:", f)
    return sti


def _er_isolert(sup) -> None:
    db, port = sup.execute(
        "SELECT current_database(), current_setting('port')").fetchone()
    sup.rollback()
    if db != "disponit_test" or port != "55432":
        sys.exit(f"NEKTET: kjører bare i disponit_test på 55432"
                 f" (fikk {db}:{port})")


def _oppsett(runde: str) -> dict:
    return {"modul": "m31_modellstyring", "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": m31_bevisrot_sha256()}


def _t31():
    from tests import test_m31_modellstyring as t
    return t


def _cli(navn: str):
    sti = REPO / "deploy/staging" / navn
    spec = importlib.util.spec_from_file_location(navn.replace("-", "_"), sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _digest(sup) -> str:
    h = hashlib.sha256()
    for t in TABELLER + ("moduldeployment",):
        n, s = sup.execute(
            f"SELECT count(*), coalesce(sum(hashtext(x::text)::bigint), 0)"
            f" FROM public.{t} x").fetchone()
        h.update(f"{t}:{n}:{s}\n".encode())
    sup.rollback()
    return h.hexdigest()


def _hendelser(sup, modul: str) -> dict:
    rader = sup.execute(
        "SELECT hendelse, aktor FROM modellstyring_hendelse"
        " WHERE modul_id=%s", (modul,)).fetchall()
    sup.rollback()
    per: dict[str, int] = {}
    for h, _a in rader:
        per[h] = per.get(h, 0) + 1
    return {"per_hendelse": per, "totalt": len(rader),
            "uten_aktor": sum(1 for _h, a in rader if not (a or "").strip())}


def _forsok(a, fn) -> tuple[str, str | None]:
    """Ett portkall. -> ("byttet" | "nektet", sqlstate)."""
    try:
        fn()
        a.commit()
        return "byttet", None
    except psycopg.Error as e:
        a.rollback()
        return "nektet", e.sqlstate


def _kjede(runde: str, merke: str = "") -> dict:
    """Modul, kontrakt og to releaser — suitens `_kjede`."""
    t = _t31()
    a = t._admin()
    m = f"m31f{merke}-{runde}"
    kh = secrets.token_hex(32)
    d1, d2 = secrets.token_hex(32), secrets.token_hex(32)
    t._kjede(a, m, kh, ("r1", d1), ("r2", d2))
    a.commit()
    return {"a": a, "m": m, "kh": kh, "d1": d1, "d2": d2}


def fasit() -> Path:
    sup = psycopg.connect(_env("M31_SUPER_DSN"))
    _er_isolert(sup)
    os.environ["DISPONIT_MIGRATOR_URL"] = _env("DISPONIT_TEST_MIGRATOR_DSN")
    from m31 import golden
    t = _t31()
    runde = secrets.token_hex(4)

    # 0. DATASETTETS IDENTITET: repoets filer mot det driftsbasen bærer.
    hasher = {str(v): golden.les_sett(SETTFIL[v])[1] for v in SETTFIL}
    eksempler, hash2 = golden.les_sett(SETTFIL[2])

    k = _kjede(runde)
    a, m, kh = k["a"], k["m"], k["kh"]
    # 1. Settet registreres gjennom den EKTE CLI-en — to ganger.
    reg = _cli("registrer-m31-golden-sett.py")
    rk1 = reg.main([m, SETT_ID, "2", str(SETTFIL[2])])
    rk2 = reg.main([m, SETT_ID, "2", str(SETTFIL[2])])
    lagret = sup.execute(
        "SELECT innhold_hash, antall_eksempler FROM golden_sett"
        " WHERE modul_id=%s AND sett_id=%s AND versjon=2",
        (m, SETT_ID)).fetchone()
    sup.rollback()

    n = len(eksempler)
    utfall = []
    # a. Ingen krav: byttet går som før.
    utfall.append(_forsok(a, lambda: t._bytt(a, m, "r1", kh))[0])
    # b. Kravet settes — porten er PÅ fra denne raden.
    t._krav(a, m, SETT_ID, 2, hash2, andel=0.9)
    a.commit()
    # c. Bytte uten bestått kjøring.
    utfall.append(_forsok(a, lambda: t._bytt(a, m, "r2", kh))[0])
    # d. En bestått kjøring for r2s digest …
    b1 = t._kjoring(a, m, k["d2"], SETT_ID, 2, hash2, antall=n, bestatt=n)
    a.commit()
    # e. … bærer byttet.
    utfall.append(_forsok(a, lambda: t._bytt(a, m, "r2", kh))[0])
    # f. Kravet STRAMMES (ny kravversjon): den gamle dommen bærer ikke.
    t._krav(a, m, SETT_ID, 2, hash2, andel=1.0)
    a.commit()
    utfall.append(_forsok(a, lambda: t._bytt(a, m, "r1", kh))[0])
    # g. En kjøring under det nye kravet som IKKE består.
    b2 = t._kjoring(a, m, k["d1"], SETT_ID, 2, hash2, antall=n,
                    bestatt=n - 4)
    a.commit()
    utfall.append(_forsok(a, lambda: t._bytt(a, m, "r1", kh))[0])

    hend = _hendelser(sup, m)
    runtime_skriver = [tab for tab in TABELLER if sup.execute(
        "SELECT has_table_privilege('disponit', %s,"
        " 'INSERT') OR has_table_privilege('disponit', %s, 'UPDATE')"
        " OR has_table_privilege('disponit', %s, 'DELETE')",
        (tab, tab, tab)).fetchone()[0]]
    sup.rollback()
    _log(f"hasher {hasher}; utfall {utfall}; hendelser {hend};"
         f" dommer {b1}/{b2}")
    art = {
        "oppsett": {**_oppsett(runde), "modul_i_registeret": m},
        "maalt": {
            "sett_hasher": hasher,
            "drift_hasher": {str(v): h for v, h in M31_DRIFTSHASHER.items()},
            "cli_returkoder": [rk1, rk2],
            "registrert_hash_lik_fil": bool(lagret) and lagret[0] == hash2
            and lagret[1] == n,
            "eksempler": n,
            "portutfall": utfall,
            "portutfall_ventet": UTFALL_VENTET,
            "dommer": [bool(b1), bool(b2)],
            "hendelser": hend["per_hendelse"],
            "hendelser_ventet": HENDELSER_VENTET,
            "hendelser_totalt": hend["totalt"],
            "hendelser_uten_aktor": hend["uten_aktor"],
            "runtime_skriverett": runtime_skriver,
        },
    }
    return _skriv("m31-fasit-v1", art)


class _Teller:
    """Modellklient som bare teller kallene — ingen skal komme."""

    def __init__(self, *_a):
        self.kall = 0
        self.modellnavn = "teller"

    def vurder(self, tekst, vekter):
        self.kall += 1
        raise RuntimeError("modellen ble kalt")


def feilinjisering() -> Path:
    sup = psycopg.connect(_env("M31_SUPER_DSN"))
    _er_isolert(sup)
    os.environ["DISPONIT_MIGRATOR_URL"] = _env("DISPONIT_TEST_MIGRATOR_DSN")
    from m31 import golden
    t = _t31()
    runde = secrets.token_hex(4)
    eksempler, hash2 = golden.les_sett(SETTFIL[2])
    n = len(eksempler)
    k = _kjede(runde, "b")
    a, m, kh = k["a"], k["m"], k["kh"]
    t._sett(a, m, SETT_ID, 2, hash2, antall=n)
    t._krav(a, m, SETT_ID, 2, hash2, andel=0.9)
    a.commit()

    brudd = [
        ("bytte_uten_bestatt_kjoring",
         lambda: t._bytt(a, m, "r2", kh)),
        ("kjoring_mot_ukjent_sett",
         lambda: t._kjoring(a, m, k["d2"], SETT_ID, 2, "c" * 64,
                            antall=n, bestatt=n)),
        ("delvis_kjoring",
         lambda: t._kjoring(a, m, k["d2"], SETT_ID, 2, hash2,
                            antall=n - 1, bestatt=n - 1)),
        ("sett_immutabilitet",
         lambda: t._sett(a, m, SETT_ID, 2, "d" * 64, antall=n)),
    ]
    ut = []
    for navn, fn in brudd:
        for_ = _digest(sup)
        utfall, sqlstate = _forsok(a, fn)
        ut.append({"brudd": navn, "avvist": utfall == "nektet",
                   "sqlstate": sqlstate, "urort": _digest(sup) == for_})
        _log(f"{navn}: {ut[-1]}")

    # HASH-AVVIK STOPPER FØR FØRSTE MODELLKALL: en fil som avviker fra det
    # registrerte hodet når aldri modellen.
    with tempfile.TemporaryDirectory() as d:
        endret = json.loads(SETTFIL[2].read_text(encoding="utf-8"))
        endret[0]["tekst"] += " (endret)"
        sti = Path(d) / "sett.json"
        sti.write_text(json.dumps(endret, ensure_ascii=False),
                       encoding="utf-8")
        teller = _Teller()
        for_ = _digest(sup)
        kode, melding = None, ""
        try:
            kode = _cli("kjor-m31-evaluering.py").main(
                [m, k["d2"], str(sti)], modellfabrikk=lambda *x: teller)
        except SystemExit as e:
            kode, melding = 1, str(e)[:160]
        ut.append({"brudd": "hash_avvik_for_modellkall",
                   "avvist": bool(kode) and "hash-avvik" in melding,
                   "sqlstate": None, "feiltype": melding,
                   "modellkall": teller.kall,
                   "urort": _digest(sup) == for_})
        _log(f"hash-avvik: {ut[-1]}")
    art = {"oppsett": _oppsett(runde),
           "maalt": {"brudd": ut, "injiserte": len(ut)}}
    return _skriv("m31-feilinjisering-v1", art)


KALLER = r"""
import json, sys
from pathlib import Path
from m31 import golden
eks, h = golden.les_sett(Path(sys.argv[1]))
perfekt = sum(golden.eksempel_bestatt(
    {"oppfylt": dict(e["forventet_oppfylt"]),
     "funn": [{"kategori": k} for k in e["forventede_funn_kategorier"]]}, e)
    for e in eks)
tomt = sum(golden.eksempel_bestatt({"oppfylt": {}, "funn": []}, e)
           for e in eks)
print(json.dumps({"hash": h, "antall": len(eks), "perfekt": perfekt,
                  "tomt": tomt}))
"""


def _kjor_i(rot: Path) -> dict:
    env = {**os.environ,
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}"}
    p = subprocess.run([sys.executable, "-c", KALLER, str(SETTFIL[2])],
                       cwd=rot / "platform", env=env, capture_output=True,
                       text=True, timeout=300)
    if p.returncode != 0:
        sys.exit(f"kalleren i {rot} feilet:\n{p.stderr[-2000:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def rollback(forgjenger: Path) -> Path:
    sup = psycopg.connect(_env("M31_SUPER_DSN"))
    _er_isolert(sup)
    t = _t31()
    runde = secrets.token_hex(4)
    rb, kd = _kjor_i(forgjenger), _kjor_i(REPO)
    # Porten etter rullingen: samme form som fasitens c–e.
    k = _kjede(runde, "r")
    a, m, kh = k["a"], k["m"], k["kh"]
    t._sett(a, m, SETT_ID, 2, kd["hash"], antall=kd["antall"])
    t._krav(a, m, SETT_ID, 2, kd["hash"], andel=0.9)
    a.commit()
    for_ = _forsok(a, lambda: t._bytt(a, m, "r2", kh))[0]
    t._kjoring(a, m, k["d2"], SETT_ID, 2, kd["hash"], antall=kd["antall"],
               bestatt=kd["perfekt"])
    a.commit()
    etter = _forsok(a, lambda: t._bytt(a, m, "r2", kh))[0]
    art = {
        "oppsett": {**_oppsett(runde), "forgjenger": str(forgjenger),
                    "kjerne_forgjenger": kjerne_digest(forgjenger),
                    "kjerne_kandidat": kjerne_digest(REPO),
                    "modul_forgjenger": release_digest(forgjenger,
                                                       M31_MODULFILER),
                    "modul_kandidat": release_digest(REPO, M31_MODULFILER)},
        "maalt": {"forgjenger": rb, "kandidat": kd, "svar_like": rb == kd,
                  "hash_lik_drift": kd["hash"] == M31_DRIFTSHASHER[2],
                  "scoring_skiller": kd["perfekt"] == kd["antall"]
                  and kd["tomt"] < kd["antall"],
                  "port_for_og_etter": [for_, etter]},
    }
    _log(f"forgjenger {rb}; kandidat {kd}; port {for_}/{etter}")
    return _skriv("m31-rollback-v1", art)


def ytelse(modell_url: str, modellnavn: str, digest: str) -> Path:
    """En EKTE evaluering mot vertens Ollama. Registeret er den isolerte
    basen: målingen skal ikke legge en kjøring i driftsregisteret."""
    sup = psycopg.connect(_env("M31_SUPER_DSN"))
    _er_isolert(sup)
    os.environ["DISPONIT_MIGRATOR_URL"] = _env("DISPONIT_TEST_MIGRATOR_DSN")
    os.environ["DISPONIT_M31_MODELL_URL"] = modell_url
    os.environ["DISPONIT_M31_MODELLNAVN"] = modellnavn
    from m31 import golden
    t = _t31()
    runde = secrets.token_hex(4)
    eksempler, hash2 = golden.les_sett(SETTFIL[2])
    k = _kjede(runde, "y")
    a, m = k["a"], k["m"]
    t._sett(a, m, SETT_ID, 2, hash2, antall=len(eksempler))
    a.commit()
    with tempfile.TemporaryDirectory() as d:
        sti = Path(d) / "sett.json"
        shutil.copy(SETTFIL[2], sti)
        try:
            kode = _cli("kjor-m31-evaluering.py").main([m, digest, str(sti)])
        except SystemExit as e:
            sys.exit(f"evalueringen stoppet: {e}")
    rad = sup.execute(
        "SELECT antall_eksempler, antall_bestatt, antall_modellfeil, p50_ms,"
        " p95_ms, varighet_s, modellnavn FROM evalueringskjoring"
        " WHERE modul_id=%s", (m,)).fetchone()
    sup.rollback()
    if rad is None:
        sys.exit("ingen kjøring ble registrert")
    art = {"oppsett": {**_oppsett(runde), "modell_url": modell_url,
                       "artifact_digest": digest},
           "maalt": {"cli_returkode": kode, "eksempler": rad[0],
                     "bestatt": rad[1], "modellfeil": rad[2],
                     "p50_ms": rad[3], "p95_ms": rad[4],
                     "varighet_s": float(rad[5]), "modellnavn": rad[6]}}
    _log(f"{rad}")
    return _skriv("m31-ytelse-v1", art)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("punkt", choices=("fasit", "feilinjisering", "rollback",
                                      "ytelse"))
    ap.add_argument("--forgjenger", type=Path)
    ap.add_argument("--modell-url", default="http://127.0.0.1:11434")
    ap.add_argument("--modellnavn", default="qwen2.5:3b-instruct")
    ap.add_argument("--digest")
    a = ap.parse_args()
    if a.punkt == "fasit":
        sti = fasit()
    elif a.punkt == "feilinjisering":
        sti = feilinjisering()
    elif a.punkt == "rollback":
        if not a.forgjenger:
            sys.exit("--forgjenger mangler")
        sti = rollback(a.forgjenger)
    else:
        if not a.digest:
            sys.exit("--digest mangler")
        sti = ytelse(a.modell_url, a.modellnavn, a.digest)
    return 0 if json.loads(sti.read_text())["bestatt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
