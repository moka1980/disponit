#!/usr/bin/env python3
"""Sertifiseringslesten for klasse F i ADR-004 (plattformvakt).

M-10 (backupinnsyn) og M-11 (selvtest) måler PLATTFORMEN, ikke en tenant.
Hver har én skrivedør gitt til én jobbrolle (`disponit_driftstatus`,
`disponit_selvtest`), én lesedør, og ett forpass i varselsenderen som
varsler plattformtenantens administratorer når jobben har vært TAUS for
lenge (30 t / 3 t). Ingen av dem skriver revisjonslogg: punktet måles som
MÅLT FRAVÆR med positiv kontroll (ADR-002 D1, klasse B-formen).

Fasit, feilinjisering og rollback rigges i den ISOLERTE basen på 55432,
bygget fra commiten som måles, med en egen rigg-plattformtenant. Ytelsen
måles på den EKTE jobben mot driftsbasen, som klasse B.

  fasit           syntetisk_datasett_likt_lokalt + revisjonslogg_korrekt.
  feilinjisering  ekte driftsfeil: ugyldig grunnlag, et CHECK-brudd,
                  tilbaketrukket EXECUTE på skrivedøren og på forpasset.
  rollback        forgjengerens og kandidatens jobbkode mot samme rigg.
  ytelse          den ekte jobben, tre runder, mot en pinnet grense.

BRUK (på verten, som administrator, med riggens DSN-er eksportert):
  python deploy/staging/plattformvakt-lest.py --modul m10_backup fasit
  sudo python deploy/staging/plattformvakt-lest.py --modul m11_selvtest \
      ytelse --dsn-fil /etc/disponit/selvtest/DISPONIT_SELVTEST_URL
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (PLATTFORMVAKTER, _sjekk_grenser,  # noqa: E402
                            kjerne_digest, plattformvakt_bevisrot_sha256,
                            release_digest, valider_artefaktformat)

UT = REPO / "deploy/staging/artefakter"
SMTP_NAVN = ("DISPONIT_SMTP_VERT", "DISPONIT_SMTP_PORT",
             "DISPONIT_SMTP_BRUKER", "DISPONIT_SMTP_PASSORD",
             "DISPONIT_SMTP_AVSENDER")


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


def _oppsett(modul: str, runde: str) -> dict:
    return {"modul": modul, "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": plattformvakt_bevisrot_sha256()}


def _kontekst(c, tenant: str) -> None:
    c.execute("SELECT set_config('disponit.tenant', %s, true),"
              " set_config('disponit.aktor', 'plattformvakt-fasit', true)",
              (tenant,))


def _plattformtenant(mg, tenant: str, runde: str) -> dict:
    """Rigg-plattformtenanten: to administratorer (portal / e-post) og ett
    medlem UTEN admin, som aldri skal få et plattformvarsel."""
    ut = {}
    for merke, roller, kanal in (("a", ["admin"], "kun_portal"),
                                 ("b", ["admin"], "epost_og_portal"),
                                 ("c", ["leser"], "epost_og_portal")):
        _kontekst(mg, tenant)
        bid = mg.execute(
            "INSERT INTO brukeridentitet (issuer, sub, profil)"
            " VALUES ('https://plattformvakt.fasit', %s, %s::jsonb)"
            " RETURNING bruker_id",
            (f"{tenant}:{merke}", json.dumps(
                {"epost": f"{merke}-{runde}@plattformvakt.test",
                 "epost_verifisert": True}))).fetchone()[0]
        mg.execute("INSERT INTO brukermedlemskap (tenant, bruker_id, roller,"
                   " aktiv) VALUES (%s,%s,%s,true)", (tenant, bid, roller))
        mg.execute("INSERT INTO varselvalg (tenant, bruker_id, kanal)"
                   " VALUES (%s,%s,%s)", (tenant, bid, kanal))
        mg.commit()
        ut[merke] = bid
    return ut


def _varsler(sup, tenant: str, art: str) -> dict:
    """Varsler av én art i tenanten, per bruker → [(ressurs_id, status)]."""
    rader = sup.execute(
        "SELECT bruker_id, ressurs_id, epost_status FROM varsel"
        " WHERE tenant=%s AND art=%s ORDER BY id", (tenant, art)).fetchall()
    sup.rollback()
    ut: dict[str, list] = {}
    for b, r, s in rader:
        ut.setdefault(b, []).append([r, s])
    return ut


def _varselfordeling(sup, tenant: str, art: str, brukere: dict) -> dict:
    v = _varsler(sup, tenant, art)
    navn = {bid: m for m, bid in brukere.items()}
    ut = {"a": 0, "b": 0, "c": 0, "status_avvik": 0}
    for bid, liste in v.items():
        m = navn.get(bid, "?")
        ut[m] = ut.get(m, 0) + len(liste)
        for _r, s in liste:
            if (m == "a" and s != "ikke_aktuelt") or \
                    (m == "b" and s not in ("koet", "sendt")):
                ut["status_avvik"] += 1
    return ut


def _revisjonsrader(sup) -> int:
    n = sup.execute("SELECT count(*) FROM revisjonslogg").fetchone()[0]
    sup.rollback()
    return n


def _skrivere(sup, modul: str) -> dict:
    """Katalogbeviset: hvem kan skrive registeret, og hvem kan kalle
    skrivedøren — tabelleieren og funksjonseieren unntatt."""
    k = PLATTFORMVAKTER[modul]
    tab = sorted({f"{g}:{p}" for g, p in sup.execute(
        "SELECT grantee, privilege_type FROM information_schema"
        ".role_table_grants g JOIN pg_class c ON c.relname = g.table_name"
        " AND c.relnamespace = 'public'::regnamespace"
        " WHERE g.table_schema='public' AND g.table_name = ANY(%s)"
        "   AND privilege_type IN ('INSERT','UPDATE','DELETE','TRUNCATE')"
        "   AND grantee <> pg_get_userbyid(c.relowner)",
        (list(k["tabeller"]),)).fetchall()})
    exe = sorted({r[0] for r in sup.execute(
        "SELECT CASE WHEN a.grantee = 0 THEN 'PUBLIC'"
        "            ELSE pg_get_userbyid(a.grantee) END"
        "  FROM pg_proc p, aclexplode(p.proacl) a"
        " WHERE p.proname = %s AND a.privilege_type = 'EXECUTE'"
        "   AND a.grantee <> p.proowner", (k["skrivedor"],)).fetchall()})
    sup.rollback()
    return {"tabellskrivere": tab, "skrivedor_execute": exe}


def _forpass(vdsn: str, modul: str, tenant: str) -> int:
    from db.pg import koble
    k = PLATTFORMVAKTER[modul]
    c = koble(vdsn)
    try:
        n = c.execute(f"SELECT {k['forpass']}(%s)", (tenant,)).fetchone()[0]
        c.commit()
    finally:
        c.close()
    return n


# ---------------------------------------------------------------------------
# M-10: rapporten backup-db.sh skriver, og jobben som fører den inn.
# ---------------------------------------------------------------------------

def _rapport(d: Path, navn: str, **endr) -> Path:
    nu = datetime.now(timezone.utc)
    r = {"backup_ts": (nu - timedelta(minutes=30)).isoformat(),
         "verifisert_ts": (nu - timedelta(minutes=5)).isoformat(),
         "restore_varighet_s": 12.5, "tabeller": 187,
         "storrelse_b": 48_123_456}
    r.update(endr)
    for k in [k for k, v in r.items() if v is None]:
        del r[k]
    sti = d / f"{navn}.json"
    sti.write_text(json.dumps(r), encoding="utf-8")
    return sti


INNGANG_M10 = r"""
import sys
from drift import kjor_backupstatus
sys.exit(kjor_backupstatus.main())
"""
INNGANG_M11 = r"""
import sys
from drift import kjor_selvtest
sys.exit(kjor_selvtest.main())
"""
INNGANG_SENDER = r"""
import sys
from drift import kjor_varselsender
sys.exit(kjor_varselsender.main())
"""


def _rent_miljo(rot: Path) -> dict:
    env = {**os.environ,
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}"}
    for n in ("CREDENTIALS_DIRECTORY",) + SMTP_NAVN:
        env.pop(n, None)
    return env


def _jobb(rot: Path, kode: str, env_extra: dict) -> tuple[int, str]:
    env = {**_rent_miljo(rot), **env_extra}
    p = subprocess.run([sys.executable, "-c", kode], cwd=rot / "platform",
                       env=env, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout + p.stderr).strip()[-800:]


def _m10_jobb(rot: Path, ddsn: str, rapport: Path) -> tuple[int, dict]:
    kode, ut = _jobb(rot, INNGANG_M10, {"DISPONIT_DRIFTSTATUS_URL": ddsn,
                                        "DISPONIT_BACKUPRAPPORT": str(rapport)})
    linje = {}
    for l in ut.splitlines()[::-1]:
        try:
            linje = json.loads(l)
            break
        except ValueError:
            continue
    return kode, linje


def _m10_rader(sup) -> int:
    n = sup.execute("SELECT count(*) FROM backupverifisering").fetchone()[0]
    sup.rollback()
    return n


def _m10_status(rt, tenant: str) -> list:
    _kontekst(rt, tenant)
    rader = rt.execute("SELECT backup_ts, tabeller, storrelse_b,"
                       " restore_varighet_s FROM backup_status(%s, 5)",
                       (tenant,)).fetchall()
    rt.rollback()
    return rader


def fasit_m10(sup, rt, mg, vdsn, runde) -> dict:
    ddsn = _env("DISPONIT_TEST_DRIFTSTATUS_DSN")
    if _m10_rader(sup):
        sys.exit("basen er ikke fersk: backupverifisering har rader — kjør"
                 " fasiten først etter byggingen")
    p, q = f"t-m10fasit-p-{runde}", f"t-m10fasit-q-{runde}"
    bp, bq = _plattformtenant(mg, p, runde), _plattformtenant(mg, q, runde)
    rev0 = _revisjonsrader(sup)
    # 1. FRAVÆR ER FEIL: en tom tabell varsles på samme terskel som en
    #    foreldet. Én gang per døgn.
    uteblitt1 = _forpass(vdsn, "m10_backup", p)
    uteblitt2 = _forpass(vdsn, "m10_backup", p)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        rap = _rapport(d, "gyldig")
        k1, l1 = _m10_jobb(REPO, ddsn, rap)
        k2, l2 = _m10_jobb(REPO, ddsn, rap)
        k3, l3 = _m10_jobb(REPO, ddsn, d / "finnes-ikke.json")
        innhold = json.loads(rap.read_text())
    status = _m10_status(rt, p)
    egne_tall = bool(status) and (
        status[0][1] == innhold["tabeller"]
        and status[0][2] == innhold["storrelse_b"]
        and float(status[0][3]) == innhold["restore_varighet_s"])
    # NEGATIV KONTROLL: en fersk verifisering → ingen varsel i Q, som HAR
    # administratorer.
    uteblitt_fersk = _forpass(vdsn, "m10_backup", q)
    return {
        "tenanter": {"p": p, "q": q},
        "maalt": {
            "uteblitt_forste": uteblitt1, "uteblitt_samme_dogn": uteblitt2,
            "uteblitt_fordeling": _varselfordeling(
                sup, p, "backupverifisering_uteblitt", bp),
            "uteblitt_etter_fersk": uteblitt_fersk,
            "uteblitt_fersk_fordeling": _varselfordeling(
                sup, q, "backupverifisering_uteblitt", bq),
            "jobb": [{"returkode": k1, "linje": l1},
                     {"returkode": k2, "linje": l2},
                     {"returkode": k3, "linje": l3}],
            "rader": _m10_rader(sup),
            "lesedor_egne_tall": egne_tall,
            "nye_revisjonsrader": _revisjonsrader(sup) - rev0,
            **_skrivere(sup, "m10_backup"),
        },
    }


# ---------------------------------------------------------------------------
# M-11: probesettet og skrivedøren som feller dommen.
# ---------------------------------------------------------------------------

def _smtp(d: Path, antall: int | None) -> dict:
    """SMTP-proben, kjørt av JOBBENS EGEN kode mot en riggfil."""
    from drift import selvtest
    sti = d / f"smtp-{antall}.env"
    if antall is None:
        sti = d / "finnes-ikke.env"
    else:
        sti.write_text("".join(f"{n}=x\n" for n in SMTP_NAVN[:antall]),
                       encoding="utf-8")
    with _uten_smtp_miljo():
        return selvtest.probe_smtp_oppsett(str(sti)).som_json()


@contextlib.contextmanager
def _uten_smtp_miljo():
    gamle = {n: os.environ.pop(n) for n in SMTP_NAVN if n in os.environ}
    try:
        yield
    finally:
        os.environ.update(gamle)


GRONN = {"status": "gronn", "maalt": {}}
UKONF = {"status": "ikke_konfigurert", "maalt": {"grunn": "fasit"}}
ROD = {"status": "rod", "maalt": {"grunn": "fasit"}}


def _m11_runder(d: Path) -> list[tuple[str, dict, str, int]]:
    """(merke, prober, ventet samlet, ventet NYE rødvarsler)."""
    full, delvis, ingen = _smtp(d, 5), _smtp(d, 2), _smtp(d, 0)
    return [
        ("gronn", {"smtp_oppsett": full, "db_drift": GRONN,
                   "api_live": GRONN}, "gronn", 0),
        # Én rød probe → ett varsel per ADMINISTRATOR, i samme transaksjon.
        ("rod", {"smtp_oppsett": delvis, "db_drift": GRONN}, "rod", 2),
        ("ukonf", {"smtp_oppsett": ingen, "ollama": UKONF},
         "ikke_konfigurert", 0),
        # «Ikke satt opp» ved siden av grønt er grønt — og varsles ALDRI.
        ("blandet", {"smtp_oppsett": ingen, "db_drift": GRONN}, "gronn", 0),
        # Samme røde probe samme døgn: ett varsel, ikke to.
        ("rod_igjen", {"smtp_oppsett": delvis, "db_drift": GRONN}, "rod", 0),
        # En ANNEN rød probe er et nytt varsel.
        ("rod_annen", {"smtp_oppsett": full, "api_ready": ROD}, "rod", 2),
    ]


def _m11_door(sdsn: str, kid: str, prober: dict, tenant: str) -> int:
    from db.pg import koble
    c = koble(sdsn)
    try:
        n = c.execute("SELECT registrer_selvtest(%s,%s,%s)",
                      (kid, json.dumps(prober), tenant)).fetchone()[0]
        c.commit()
    finally:
        c.close()
    return n


def _m11_kjoring(sup, kid: str) -> tuple[str | None, int]:
    r = sup.execute("SELECT samlet, (SELECT count(*) FROM selvtest_probe p"
                    " WHERE p.kjoring_id = k.kjoring_id)"
                    " FROM selvtest_kjoring k WHERE kjoring_id=%s",
                    (kid,)).fetchone()
    sup.rollback()
    return (r[0], r[1]) if r else (None, 0)


def fasit_m11(sup, rt, mg, vdsn, runde) -> dict:
    sdsn = _env("DISPONIT_TEST_SELVTEST_DSN")
    n = sup.execute("SELECT count(*) FROM selvtest_kjoring").fetchone()[0]
    sup.rollback()
    if n:
        sys.exit("basen er ikke fersk: selvtest_kjoring har rader — kjør"
                 " fasiten først etter byggingen")
    p, q = f"t-m11fasit-p-{runde}", f"t-m11fasit-q-{runde}"
    bp, bq = _plattformtenant(mg, p, runde), _plattformtenant(mg, q, runde)
    rev0 = _revisjonsrader(sup)
    uteblitt1 = _forpass(vdsn, "m11_selvtest", p)
    uteblitt2 = _forpass(vdsn, "m11_selvtest", p)
    per, rod_for = [], 0
    with tempfile.TemporaryDirectory() as d:
        runder = _m11_runder(Path(d))
    ider = {}
    for merke, prober, samlet, nye in runder:
        kid = str(uuid.uuid4())
        ider[merke] = kid
        ny = _m11_door(sdsn, kid, prober, p)
        fikk, antall = _m11_kjoring(sup, kid)
        rod_etter = sum(_varselfordeling(sup, p, "selvtest_rodt",
                                         bp)[m] for m in "abc")
        per.append({"merke": merke, "ventet": [samlet, nye, len(prober)],
                    "fikk": [fikk, rod_etter - rod_for, antall],
                    "ny": ny})
        rod_for = rod_etter
    # GJENSPILL av en registrert runde: et stille ja, ingen nye rader.
    for_gj = _m11_kjoring(sup, ider["rod"])
    gjenspill = _m11_door(sdsn, ider["rod"],
                          runder[1][1], p)
    etter_gj = _m11_kjoring(sup, ider["rod"])
    uteblitt_fersk = _forpass(vdsn, "m11_selvtest", q)
    return {
        "tenanter": {"p": p, "q": q},
        "maalt": {
            "uteblitt_forste": uteblitt1, "uteblitt_samme_dogn": uteblitt2,
            "uteblitt_fordeling": _varselfordeling(
                sup, p, "selvtest_uteblitt", bp),
            "uteblitt_etter_fersk": uteblitt_fersk,
            "uteblitt_fersk_fordeling": _varselfordeling(
                sup, q, "selvtest_uteblitt", bq),
            "runder": per,
            "rundeavvik": sum(1 for r in per if r["ventet"] != r["fikk"]
                              or r["ny"] != 1),
            "rodvarsel_fordeling": _varselfordeling(sup, p, "selvtest_rodt",
                                                    bp),
            "gjenspill_ny": gjenspill,
            "gjenspill_urort": for_gj == etter_gj,
            "nye_revisjonsrader": _revisjonsrader(sup) - rev0,
            **_skrivere(sup, "m11_selvtest"),
        },
    }


def fasit(modul: str) -> Path:
    sup = psycopg.connect(_env("FORPASS_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    vdsn = _env("DISPONIT_TEST_VARSEL_DSN")
    runde = secrets.token_hex(4)
    ut = (fasit_m10 if modul == "m10_backup" else fasit_m11)(
        sup, rt, mg, vdsn, runde)
    _log(f"{modul}: {json.dumps(ut['maalt'], default=str)[:1500]}")
    art = {"oppsett": {**_oppsett(modul, runde), "tenanter": ut["tenanter"]},
           "maalt": ut["maalt"]}
    return _skriv(f"{PLATTFORMVAKTER[modul]['kort']}-fasit-v1", art)


# ---------------------------------------------------------------------------

def _rettighet(mg, modul: str, hva: str, rolle: str, gi: bool) -> None:
    k = PLATTFORMVAKTER[modul]
    sig = k["signaturer"][hva]
    mg.execute(f"SET ROLE {k['eier']}")
    mg.execute(f"{'GRANT' if gi else 'REVOKE'} EXECUTE ON FUNCTION {sig}"
               f" {'TO' if gi else 'FROM'} {rolle}")
    mg.execute("RESET ROLE")
    mg.commit()


class _Postkasse:
    def __init__(self):
        self.til: list[str] = []

    def __call__(self, til, emne, tekst):
        self.til.append(til)


def _sender(vdsn: str, plattformtenant: str) -> tuple[dict, _Postkasse]:
    from db.pg import koble
    from drift import varselsender
    post = _Postkasse()
    gammel = os.environ.get("DISPONIT_PLATTFORMTENANT")
    os.environ["DISPONIT_PLATTFORMTENANT"] = plattformtenant
    c = koble(vdsn)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            res = varselsender.kjor(c, send=post)
    finally:
        c.close()
        if gammel is None:
            os.environ.pop("DISPONIT_PLATTFORMTENANT", None)
        else:
            os.environ["DISPONIT_PLATTFORMTENANT"] = gammel
    return res, post


def _forpassbrudd(sup, mg, vdsn, modul: str, runde: str) -> dict:
    """E2 fra ADR-003, på plattformforpasset: trukket EXECUTE for
    senderen. Den ordinære sendingen skal gå, feilen telles, inngangen
    gir kode ≠ 0, og etter reparasjonen går det rent."""
    t = f"t-{PLATTFORMVAKTER[modul]['kort']}brudd-{runde}"
    b = _plattformtenant(mg, t, runde + "x")
    _kontekst(mg, t)
    mg.execute(
        "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
        " ressurs_id, hendelse, tekstnokkel, parametre, epost_status)"
        " VALUES (%s,%s,'attestering_venter','policyutkast',%s,'r1',"
        "         'varsel.attestering_venter','{}'::jsonb,'koet')",
        (t, b["b"], "u-" + secrets.token_hex(4)))
    mg.commit()
    _rettighet(mg, modul, "forpass", "disponit_varselsender", False)
    try:
        res, post = _sender(vdsn, t)
        kode, _ut = _jobb(REPO, INNGANG_SENDER,
                          {"DISPONIT_DATABASE_URL": vdsn,
                           "DISPONIT_PLATTFORMTENANT": t})
    finally:
        _rettighet(mg, modul, "forpass", "disponit_varselsender", True)
    res_e, _p = _sender(vdsn, t)
    kode_e, _u = _jobb(REPO, INNGANG_SENDER, {"DISPONIT_DATABASE_URL": vdsn,
                                              "DISPONIT_PLATTFORMTENANT": t})
    return {"brudd": "forpass_execute_trukket",
            "forpass_feil": res.get("forpass_feil"),
            "ordinaer_sendt": len(post.til) >= 1,
            "returkode": kode,
            "forpass_feil_etter": res_e.get("forpass_feil"),
            "returkode_etter": kode_e}


def feilinjisering(modul: str) -> Path:
    k = PLATTFORMVAKTER[modul]
    sup = psycopg.connect(_env("FORPASS_SUPER_DSN"))
    _er_isolert(sup)
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    vdsn = _env("DISPONIT_TEST_VARSEL_DSN")
    runde = secrets.token_hex(4)
    brudd = []
    if modul == "m10_backup":
        ddsn = _env("DISPONIT_TEST_DRIFTSTATUS_DSN")
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            for navn, rap in (
                    ("rapport_mangler_felt",
                     _rapport(d, "mangler", tabeller=None)),
                    ("rapport_feil_type",
                     _rapport(d, "type", tabeller="187")),
                    ("check_brudd_i_basen", _rapport(d, "check", tabeller=3))):
                for_ = _m10_rader(sup)
                kode, linje = _m10_jobb(REPO, ddsn, rap)
                brudd.append({"brudd": navn, "returkode": kode,
                              "grunn": linje.get("grunn"),
                              "nye_rader": _m10_rader(sup) - for_})
            _rettighet(mg, modul, "skrivedor", "disponit_driftstatus", False)
            try:
                for_ = _m10_rader(sup)
                kode, linje = _m10_jobb(REPO, ddsn,
                                        _rapport(d, "uten_execute",
                                                 backup_ts=datetime.now(
                                                     timezone.utc)
                                                 .isoformat()))
                brudd.append({"brudd": "skrivedor_execute_trukket",
                              "returkode": kode, "grunn": linje.get("grunn"),
                              "nye_rader": _m10_rader(sup) - for_})
            finally:
                _rettighet(mg, modul, "skrivedor", "disponit_driftstatus",
                           True)
            for_ = _m10_rader(sup)
            kode, linje = _m10_jobb(REPO, ddsn, _rapport(
                d, "frisk", backup_ts=(datetime.now(timezone.utc)
                                       + timedelta(seconds=1)).isoformat()))
            frisk = {"returkode": kode, "skrevet": linje.get("skrevet"),
                     "nye_rader": _m10_rader(sup) - for_}
    else:
        sdsn = _env("DISPONIT_TEST_SELVTEST_DSN")
        p = f"t-m11brudd-p-{runde}"
        _plattformtenant(mg, p, runde)
        with tempfile.TemporaryDirectory() as d:
            # EN PROBE SOM FEILER STOPPER IKKE RUNDEN: den ekte jobben med
            # en sokkel som ikke finnes og en tom enhetskatalog. Hver probe
            # skal stå i runden, de feilede som røde.
            from db.pg import koble
            from drift import selvtest
            c = koble(sdsn)
            try:
                res = selvtest.kjor(c, tenant=p,
                                    sokkel=str(Path(d) / "ingen.sock"),
                                    smtp_sti=str(Path(d) / "ingen.env"),
                                    enhetskatalog=str(Path(d)))
            finally:
                c.close()
            samlet, antall = _m11_kjoring(sup, res["kjoring_id"])
            brudd.append({"brudd": "prober_feiler",
                          "returkode": 0 if res["ny"] == 1 else 1,
                          "samlet": samlet, "prober_i_runden": antall,
                          "prober_maalt": len(res["prober"]),
                          "nye_rader": res["ny"]})
        _rettighet(mg, modul, "skrivedor", "disponit_selvtest", False)
        try:
            n0 = sup.execute("SELECT count(*) FROM selvtest_kjoring"
                             ).fetchone()[0]
            sup.rollback()
            kode, ut = _jobb(REPO, INNGANG_M11,
                             {"DISPONIT_SELVTEST_URL": sdsn,
                              "DISPONIT_PLATTFORMTENANT": p})
            n1 = sup.execute("SELECT count(*) FROM selvtest_kjoring"
                             ).fetchone()[0]
            sup.rollback()
            brudd.append({"brudd": "skrivedor_execute_trukket",
                          "returkode": kode, "grunn": ut[-200:],
                          "nye_rader": n1 - n0})
        finally:
            _rettighet(mg, modul, "skrivedor", "disponit_selvtest", True)
        kode, ut = _jobb(REPO, INNGANG_M11, {"DISPONIT_SELVTEST_URL": sdsn,
                                             "DISPONIT_PLATTFORMTENANT": p})
        n2 = sup.execute("SELECT count(*) FROM selvtest_kjoring"
                         ).fetchone()[0]
        sup.rollback()
        frisk = {"returkode": kode, "skrevet": n2 - n1, "nye_rader": n2 - n1}
    forpass = _forpassbrudd(sup, mg, vdsn, modul, runde)
    _log(f"{modul}: brudd {brudd}; frisk {frisk}; forpass {forpass}")
    art = {"oppsett": _oppsett(modul, runde),
           "maalt": {"brudd": brudd, "frisk_etterpa": frisk,
                     "forpass": forpass}}
    return _skriv(f"{k['kort']}-feilinjisering-v1", art)


# ---------------------------------------------------------------------------

KALLER_M11 = r"""
import json, sys
from db.pg import koble
from drift import selvtest
dsn, kid, tenant, d = sys.argv[1:5]
c = koble(dsn)
res = selvtest.kjor(c, kjoring_id=kid, tenant=tenant,
                    sokkel=d + "/ingen.sock", smtp_sti=d + "/smtp.env",
                    enhetskatalog=d)
c.close()
print(json.dumps({"ny": res["ny"],
                  "status": {k: v["status"] for k, v in res["prober"].items()}},
                 sort_keys=True))
"""


def rollback(modul: str, forgjenger: Path) -> Path:
    k = PLATTFORMVAKTER[modul]
    sup = psycopg.connect(_env("FORPASS_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    p = f"t-{k['kort']}rb-{runde}"
    _plattformtenant(mg, p, runde)
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        if modul == "m10_backup":
            ddsn = _env("DISPONIT_TEST_DRIFTSTATUS_DSN")
            nu = datetime.now(timezone.utc)
            r1 = _rapport(d, "r1", backup_ts=(nu + timedelta(seconds=2))
                          .isoformat(), tabeller=201)
            r2 = _rapport(d, "r2", backup_ts=(nu + timedelta(seconds=3))
                          .isoformat(), tabeller=202)
            krb, lrb = _m10_jobb(forgjenger, ddsn, r1)
            kkd_gj, lkd_gj = _m10_jobb(REPO, ddsn, r1)
            kkd, lkd = _m10_jobb(REPO, ddsn, r2)
            status = _m10_status(rt, p)
            tabeller = sorted({r[1] for r in status} & {201, 202})
            maalt = {
                "forgjenger": {"returkode": krb, "skrevet": lrb.get("skrevet")},
                "kandidat_gjenspill": {"returkode": kkd_gj,
                                       "skrevet": lkd_gj.get("skrevet")},
                "kandidat": {"returkode": kkd, "skrevet": lkd.get("skrevet")},
                "svar_like": krb == kkd == 0 and lrb.get("skrevet")
                == lkd.get("skrevet") == 1,
                "gjenspill_over_rulling": lkd_gj.get("skrevet"),
                "lesedor_baerer_begge": tabeller == [201, 202],
            }
        else:
            sdsn = _env("DISPONIT_TEST_SELVTEST_DSN")
            (d / "smtp.env").write_text("".join(f"{n}=x\n"
                                                for n in SMTP_NAVN[:2]))

            def kjor(rot, kid):
                p_ = subprocess.run(
                    [sys.executable, "-c", KALLER_M11, sdsn, kid, p, str(d)],
                    cwd=rot / "platform", env=_rent_miljo(rot),
                    capture_output=True, text=True, timeout=300)
                if p_.returncode:
                    sys.exit(f"selvtesten i {rot} feilet:\n{p_.stderr[-1500:]}")
                return json.loads(p_.stdout.strip().splitlines()[-1])
            k1, k2 = str(uuid.uuid4()), str(uuid.uuid4())
            rb = kjor(forgjenger, k1)
            gj = kjor(REPO, k1)
            kd = kjor(REPO, k2)
            s1, s2 = _m11_kjoring(sup, k1), _m11_kjoring(sup, k2)
            maalt = {
                "forgjenger": rb, "kandidat": kd,
                "svar_like": rb["status"] == kd["status"] and rb["ny"]
                == kd["ny"] == 1 and s1 == s2,
                "gjenspill_over_rulling": gj["ny"],
                "lesedor_baerer_begge": s1[0] == s2[0] == "rod",
            }
    art = {"oppsett": {**_oppsett(modul, runde), "forgjenger": str(forgjenger),
                       "kjerne_forgjenger": kjerne_digest(forgjenger),
                       "kjerne_kandidat": kjerne_digest(REPO),
                       "modul_forgjenger": release_digest(forgjenger,
                                                          k["modulfiler"]),
                       "modul_kandidat": release_digest(REPO,
                                                        k["modulfiler"])},
           "maalt": maalt}
    _log(f"{modul}: {maalt}")
    return _skriv(f"{k['kort']}-rollback-v1", art)


def ytelse(modul: str, dsn_fil: Path, runder: int) -> Path:
    """Den EKTE jobben mot driftsbasen, som klasse B. Riggen rører ingenting:
    M-10 leser den ekte rapporten (idempotent), M-11 kjører en ekte runde."""
    k = PLATTFORMVAKTER[modul]
    dsn = dsn_fil.read_text(encoding="utf-8").strip()
    with psycopg.connect(dsn) as c:
        db = c.execute("SELECT current_database()").fetchone()[0]
    kjoringer = []
    for i in range(runder):
        t0 = time.monotonic()
        if modul == "m10_backup":
            kode, ut = _jobb(REPO, INNGANG_M10,
                             {"DISPONIT_DRIFTSTATUS_URL": dsn})
        else:
            miljo = {"DISPONIT_SELVTEST_URL": dsn}
            for fil in ("/etc/disponit/varsel/smtp.env",
                        "/etc/disponit/m57/modell.env"):
                try:
                    for linje in Path(fil).read_text().splitlines():
                        if "=" in linje and not linje.startswith("#"):
                            n, v = linje.split("=", 1)
                            miljo[n.strip()] = v.strip()
                except OSError:
                    pass
            kode, ut = _jobb(REPO, INNGANG_M11, miljo)
        sek = time.monotonic() - t0
        kjoringer.append({"sekunder": round(sek, 3), "returkode": kode,
                          "linje": ut.splitlines()[-1][:400] if ut else ""})
        _log(f"kjøring {i + 1}: {kjoringer[-1]}")
    art = {"oppsett": {"modul": modul, "vert": socket.gethostname(),
                       "base": db, "runder": runder,
                       "bevisrot_sha256": plattformvakt_bevisrot_sha256()},
           "maalt": {"kjoringer": kjoringer,
                     "maks_sekunder": max(x["sekunder"] for x in kjoringer),
                     "feilede": sum(1 for x in kjoringer
                                    if x["returkode"] != 0)}}
    return _skriv(f"{k['kort']}-ytelse-v1", art)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modul", required=True, choices=sorted(PLATTFORMVAKTER))
    ap.add_argument("punkt", choices=("fasit", "feilinjisering", "rollback",
                                      "ytelse"))
    ap.add_argument("--forgjenger", type=Path)
    ap.add_argument("--dsn-fil", type=Path)
    ap.add_argument("--runder", type=int, default=3)
    a = ap.parse_args()
    if a.punkt == "fasit":
        sti = fasit(a.modul)
    elif a.punkt == "feilinjisering":
        sti = feilinjisering(a.modul)
    elif a.punkt == "rollback":
        if not a.forgjenger:
            sys.exit("--forgjenger mangler")
        sti = rollback(a.modul, a.forgjenger)
    else:
        if not a.dsn_fil:
            sys.exit("--dsn-fil mangler")
        sti = ytelse(a.modul, a.dsn_fil, a.runder)
    return 0 if json.loads(sti.read_text())["bestatt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
