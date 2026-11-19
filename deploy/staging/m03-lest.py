#!/usr/bin/env python3
"""M-03s sertifiseringslest — klasse B i ADR-002 (plattformregister + måler).

M-03 profilerer plattformens egne tenant-tabeller mot et GLOBALT
regelregister som seedes i migrasjon (092). En rigg som skal gi kjent
fasit må skrive regler, tabeller og dårlige rader — plattformdommer — og
det skal aldri skje i driftsbasen. Formen er M-04s (`m04-lest.py`):

  * FASIT, FEILINJISERING og ROLLBACK rigges i den ISOLERTE basen på
    55432, bygget fra commiten som måles. Produsenten NEKTER alt annet.
  * YTELSE måles på den EKTE jobben (`drift.kjor_kvalitetsprofilering`)
    mot driftsbasen, med målerens egen credential.

LUKKING ER IMPLISITT, og det er modulens egen dom (092, `m3_funn_vakt`):
«et funn lukkes ved at målingen slutter å reise det, ikke ved sletting».
Et funn er derfor ÅPENT når det ble sett i den siste fullførte kjøringen,
og LUKKET når en fullført kjøring etter det ikke reiste det.

  fasit           syntetisk_datasett_likt_lokalt + revisjonslogg_korrekt.
                  Sju subjekter (regel, tenant) over alle fire funntypene,
                  en REN som rettes mellom kjøringene og en negativ
                  kontroll. MÅLT FRAVÆR: null nye revisjonsrader, null
                  endrede rader utenfor M-03s fire tabeller og riggens,
                  ingen innloggingsrolle med skriverett på regelregisteret,
                  og målerens eneste EXECUTE-grant er `m3_profiler`.
  feilinjisering  to ekte driftsfeil gjennom den ekte inngangen: en
                  rundegrense på 1 ms og en tilkobling uten EXECUTE. Begge
                  skal feile uten å etterlate en kjøring som ser fullført
                  ut, og alarmere på den andre.
  rollback        drill (drept midt i en regel), så forgjengerens og
                  kandidatens kode måler hver sin hele runde: samme åpne
                  funn.
  ytelse          tre kjøringer av den ekte jobben mot driftsbasen.

BRUK (på verten):
  python deploy/staging/m03-lest.py fasit|feilinjisering
  python deploy/staging/m03-lest.py rollback --forgjenger /opt/disponit/releases/<sha>
  python deploy/staging/m03-lest.py ytelse --dsn-fil /etc/disponit/kvalitet/DISPONIT_KVALITETSMAALER_URL
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (M03_MODULFILER, _sjekk_grenser,  # noqa: E402
                            kjerne_digest, m03_bevisrot_sha256,
                            release_digest, valider_artefaktformat)

UT = REPO / "deploy/staging/artefakter"
EGNE = ("kvalitetsregel", "kvalitetskjoring", "kvalitetsprofil",
        "kvalitetsfunn")
EIER = "disponit_kvalitet_eier"
MAALER = "disponit_kvalitetsmaaler"
MAALEDOR = "m3_profiler(integer)"
PLATTFORM = "__plattform_kvalitet"


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
        sys.exit(f"NEKTET: riggen skriver plattformdommer og kjører bare i"
                 f" disponit_test på 55432 (fikk {db}:{port})")


def _oppsett(runde: str) -> dict:
    return {"modul": "m03_datakvalitet", "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": m03_bevisrot_sha256()}


# ---------------------------------------------------------------------------
# Riggen — bare i den isolerte basen.
# ---------------------------------------------------------------------------

def _tabell(mg, navn: str, rader: list[tuple[str, str | None]]) -> None:
    mg.execute(f"CREATE TABLE {navn} (tenant text NOT NULL, verdi text)")
    mg.execute(f"GRANT SELECT (tenant, verdi) ON {navn} TO {EIER}")
    for tenant, verdi in rader:
        mg.execute(f"INSERT INTO {navn} (tenant, verdi) VALUES (%s,%s)",
                   (tenant, verdi))
    mg.commit()


def _regel(mg, regel_id: str, tabell: str) -> None:
    # Regelregisteret eies av modulens eier; migratoren er medlem UTEN
    # arv og må ta rollen for én innsetting (testsuitens form).
    mg.execute(f"SET LOCAL ROLE {EIER}")
    mg.execute(
        "INSERT INTO kvalitetsregel (regel_id, relasjon, kolonne, regeltype,"
        " uttrykk, alvorlighet, terskel_andel, begrunnelse) VALUES"
        " (%s,%s,'verdi','ikke_tom',NULL,'middels',0,"
        " 'Fasit (m03-lest): verdi skal aldri være tom')",
        (regel_id, tabell))
    mg.commit()


#: (merkelapp, forventet etter ANDRE kjøring, etter FØRSTE, tenantnøkkel)
SETT: tuple[tuple[str, str | None, str | None, str], ...] = (
    ("terskel_a", "terskel_overskredet", "terskel_overskredet", "a"),
    ("terskel_b", "terskel_overskredet", "terskel_overskredet", "b"),
    ("umaalbar", "umaalbar", "umaalbar", "p"),
    ("ukjent_tabell", "ukjent_tabell", "ukjent_tabell", "p"),
    ("uten_kolonne", "regel_uten_kolonne", "regel_uten_kolonne", "p"),
    # Den RENE har en tom verdi i første kjøring; raden rettes mellom
    # kjøringene, og andre kjøring skal ikke reise funnet igjen.
    ("ren", None, "terskel_overskredet", "a"),
    # Den NEGATIVE KONTROLLEN: alle verdier fylt ut.
    ("ok", None, None, "a"),
)
RENSES = "ren"
FUNNTYPER = ("regel_uten_kolonne", "terskel_overskredet", "ukjent_tabell",
             "umaalbar")


def forbered(mg, runde: str) -> dict:
    p = f"fm3_{runde}_"
    ta, tb = f"t-m03fasit-a-{runde}", f"t-m03fasit-b-{runde}"
    tenant = {"a": ta, "b": tb, "p": PLATTFORM}
    regler: dict[str, str] = {}

    _tabell(mg, p + "terskel", [(ta, None), (ta, "x"), (tb, None)])
    regler["terskel_a"] = regler["terskel_b"] = f"fm3_{runde}.terskel.ikke_tom"
    _regel(mg, regler["terskel_a"], p + "terskel")

    _tabell(mg, p + "umaal", [(ta, None)])
    regler["umaalbar"] = f"fm3_{runde}.umaal.ikke_tom"
    _regel(mg, regler["umaalbar"], p + "umaal")
    mg.execute(f"REVOKE SELECT ON {p}umaal FROM {EIER}")
    mg.commit()

    _tabell(mg, p + "borte", [(ta, "x")])
    regler["ukjent_tabell"] = f"fm3_{runde}.borte.ikke_tom"
    _regel(mg, regler["ukjent_tabell"], p + "borte")
    mg.execute(f"DROP TABLE {p}borte")
    mg.commit()

    _tabell(mg, p + "ukol", [(ta, "x")])
    regler["uten_kolonne"] = f"fm3_{runde}.ukol.ikke_tom"
    _regel(mg, regler["uten_kolonne"], p + "ukol")
    mg.execute(f"ALTER TABLE {p}ukol DROP COLUMN verdi")
    mg.commit()

    _tabell(mg, p + "ren", [(ta, None)])
    regler["ren"] = f"fm3_{runde}.ren.ikke_tom"
    _regel(mg, regler["ren"], p + "ren")

    _tabell(mg, p + "ok", [(ta, "x"), (ta, "y")])
    regler["ok"] = f"fm3_{runde}.ok.ikke_tom"
    _regel(mg, regler["ok"], p + "ok")

    return {"tenant": tenant, "regler": regler,
            "subjekter": {m: {"regel_id": regler[m], "tenant": tenant[t]}
                          for m, _v, _f, t in SETT}}


def rens(mg, runde: str) -> None:
    mg.execute(f"UPDATE fm3_{runde}_ren SET verdi = 'rettet'")
    mg.commit()


# ---------------------------------------------------------------------------
# Målinger — som superbruker i riggen (lesing forbi RLS), aldri skriving.
# ---------------------------------------------------------------------------

def _siste_fullforte(sup) -> str | None:
    rad = sup.execute(
        "SELECT kjoring_id::text FROM kvalitetskjoring WHERE NOT avbrutt"
        " ORDER BY startet_ts DESC LIMIT 1").fetchone()
    sup.rollback()
    return rad[0] if rad else None


def _apne(sup, rigg: dict) -> dict[str, list[str]]:
    """ÅPENT = sett i siste fullførte kjøring (092s lukkedom)."""
    siste = _siste_fullforte(sup)
    ut: dict[str, list[str]] = {}
    for merke, s in rigg["subjekter"].items():
        rader = sup.execute(
            "SELECT funntype FROM kvalitetsfunn WHERE tenant=%s"
            " AND regel_id=%s AND sist_sett_kjoring::text=%s ORDER BY 1",
            (s["tenant"], s["regel_id"], siste)).fetchall()
        ut[merke] = [r[0] for r in rader]
    sup.rollback()
    return ut


def _dom(sup, rigg: dict, kolonne: int) -> tuple[list, int]:
    apne = _apne(sup, rigg)
    per, avvik = [], 0
    for merke, *forv in SETT:
        ventet = forv[kolonne]
        fikk = sorted(apne.get(merke, []))
        avvik += 0 if fikk == ([ventet] if ventet else []) else 1
        per.append({"merke": merke, "ventet": ventet, "fikk": fikk,
                    "regel_id": rigg["subjekter"][merke]["regel_id"]})
    return per, avvik


def _radbilde(sup) -> dict[str, str]:
    tabeller = [r[0] for r in sup.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n"
        " ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
        " AND c.relkind IN ('r','p') ORDER BY 1").fetchall()]
    bilde = {}
    for t in tabeller:
        if t in EGNE or t.startswith("fm3_"):
            continue
        n, h = sup.execute(
            f'SELECT count(*), coalesce(sum(hashtext(x::text)::bigint), 0)'
            f' FROM public."{t}" x').fetchone()
        bilde[t] = f"{n}:{h}"
    sup.rollback()
    return bilde


def _katalogbevis(sup) -> dict:
    skrivere = [r[0] for r in sup.execute(
        "SELECT r.rolname FROM pg_roles r WHERE r.rolcanlogin"
        " AND NOT r.rolsuper AND r.rolname <> 'disponit_migrator'"
        " AND has_table_privilege(r.oid, 'public.kvalitetsregel',"
        "                         'INSERT,UPDATE,DELETE,TRUNCATE')"
        " ORDER BY 1").fetchall()]
    # ACL-en leses EKSPLISITT: `has_function_privilege` teller med PUBLIC.
    grants = sorted({r[0] for r in sup.execute(
        "SELECT p.proname FROM pg_proc p"
        "  JOIN pg_namespace n ON n.oid = p.pronamespace,"
        "  LATERAL aclexplode(p.proacl) a"
        " WHERE n.nspname = 'public' AND a.privilege_type = 'EXECUTE'"
        "   AND a.grantee = (SELECT oid FROM pg_roles WHERE rolname = %s)",
        (MAALER,)).fetchall()})
    sup.rollback()
    return {"registerskrivere": skrivere, "maaler_execute_grants": grants}


def _revisjonsrader(sup) -> int:
    n = sup.execute("SELECT count(*) FROM revisjonslogg").fetchone()[0]
    sup.rollback()
    return n


def _profiler(dsn: str) -> dict:
    from db.pg import koble
    from drift import kvalitetsprofilering as kp
    c = koble(dsn)
    try:
        r = kp.kjor(c, grense=500)
        return {"kjoring_id": r.kjoring_id, "antall_regler": r.antall_regler,
                "avbrutt": r.avbrutt, "feilet": r.feilet,
                "hoppet_over": r.hoppet_over}
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Punktene.
# ---------------------------------------------------------------------------

def fasit() -> Path:
    sup = psycopg.connect(_env("M03_SUPER_DSN"))
    _er_isolert(sup)
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    maaler = _env("DISPONIT_TEST_KVALITETSMAALER_DSN")
    runde = secrets.token_hex(4)
    _profiler(maaler)                          # basen i ro
    rigg = forbered(mg, runde)
    regler = sorted(set(rigg["regler"].values()))
    _log(f"rigget runde {runde}: {len(SETT)} subjekter, {len(regler)} regler")

    rev_for, bilde_for = _revisjonsrader(sup), _radbilde(sup)
    k1 = _profiler(maaler)
    per1, avvik1 = _dom(sup, rigg, 1)
    nye1 = sup.execute(
        "SELECT count(*) FROM kvalitetsfunn WHERE regel_id = ANY(%s)"
        " AND forst_sett_kjoring::text = %s",
        (regler, k1["kjoring_id"])).fetchone()[0]
    sup.rollback()
    _log(f"kjøring 1: {k1} — {nye1} nye funn, avvik {avvik1}")

    rens(mg, runde)
    k2 = _profiler(maaler)
    per2, avvik2 = _dom(sup, rigg, 0)
    nye2 = sup.execute(
        "SELECT count(*) FROM kvalitetsfunn WHERE regel_id = ANY(%s)"
        " AND forst_sett_kjoring::text = %s",
        (regler, k2["kjoring_id"])).fetchone()[0]
    ren = rigg["subjekter"][RENSES]
    lukket = sup.execute(
        "SELECT count(*) FROM kvalitetsfunn WHERE tenant=%s AND regel_id=%s"
        " AND sist_sett_kjoring::text = %s",
        (ren["tenant"], ren["regel_id"], k1["kjoring_id"])).fetchone()[0]
    sup.rollback()
    rev_etter, bilde_etter = _revisjonsrader(sup), _radbilde(sup)
    endret = sorted(t for t in set(bilde_for) | set(bilde_etter)
                    if bilde_for.get(t) != bilde_etter.get(t))
    kat = _katalogbevis(sup)
    _log(f"kjøring 2: {k2} — {nye2} nye, {lukket} lukket, avvik {avvik2};"
         f" revisjonslogg {rev_for} → {rev_etter}; endret {endret or 'ingen'};"
         f" {kat}")
    art = {
        "oppsett": {**_oppsett(runde), "regler": rigg["regler"],
                    "tenanter": rigg["tenant"]},
        "maalt": {
            "subjekter": len(SETT),
            "funntyper_dekket": sorted({f for _m, f, _f1, _t in SETT if f}),
            "per_subjekt_forste": per1,
            "per_subjekt": per2,
            "funnavvik_forste": avvik1,
            "funnavvik": avvik2,
            "kjoring1_fullfort": not k1["avbrutt"] and not k1["feilet"],
            "kjoring2_fullfort": not k2["avbrutt"] and not k2["feilet"],
            "kjoring1_nye": nye1,
            "kjoring2_nye": nye2,
            "kjoring2_lukkede": lukket,
            "revisjonsrader_for": rev_for,
            "revisjonsrader_etter": rev_etter,
            "nye_revisjonsrader": rev_etter - rev_for,
            "fremmede_tabeller_sett": len(bilde_for),
            "endrede_fremmede_tabeller": endret,
            **kat,
        },
    }
    return _skriv("m03-fasit-v1", art)


def _inngang(dsn: str, tilstand: Path, rot: Path = REPO,
             ekstra: dict | None = None) -> dict:
    env = {**os.environ, "DISPONIT_KVALITETSMAALER_URL": dsn,
           "DISPONIT_KVALITETSTILSTAND": str(tilstand),
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}",
           **(ekstra or {})}
    env.pop("CREDENTIALS_DIRECTORY", None)
    p = subprocess.run(
        [sys.executable, "-m", "drift.kjor_kvalitetsprofilering"],
        cwd=rot / "platform", env=env, capture_output=True, text=True,
        timeout=600)
    linje = json.loads(p.stdout.strip().splitlines()[-1])
    linje["returkode"] = p.returncode
    return linje


def feilinjisering() -> Path:
    sup = psycopg.connect(_env("M03_SUPER_DSN"))
    _er_isolert(sup)
    maaler = _env("DISPONIT_TEST_KVALITETSMAALER_DSN")
    runtime = _env("DISPONIT_TEST_DSN")
    runde = secrets.token_hex(4)
    _profiler(maaler)

    def bilde() -> str:
        h = hashlib.sha256()
        for t in EGNE:
            n, s = sup.execute(
                f"SELECT count(*), coalesce(sum(hashtext(x::text)::bigint),0)"
                f" FROM public.{t} x").fetchone()
            h.update(f"{t}:{n}:{s}\n".encode())
        sup.rollback()
        return h.hexdigest()

    # (a) RUNDEGRENSE UNDER KOSTNADEN, gjennom den ekte inngangen: hele
    # kallet felles, og ingen kjøring skal stå igjen som fullført.
    from drift import kvalitetsprofilering as kp
    from db.pg import koble
    for_a = bilde()
    c = koble(maaler)
    try:
        ra = [kp.kjor(c, grense=500, rundegrense_ms=1, tidligere_feil=i)
              for i in range(2)]
    finally:
        c.close()
    etter_a = bilde()
    _log(f"rundegrense 1 ms: {[(r.feilet, r.alarm_utlost) for r in ra]}")

    # (b) TILKOBLING UTEN EXECUTE.
    har_execute = sup.execute(
        "SELECT has_function_privilege('disponit', %s, 'EXECUTE')",
        (MAALEDOR,)).fetchone()[0]
    sup.rollback()
    for_b = bilde()
    with tempfile.TemporaryDirectory() as d:
        rb = [_inngang(runtime, Path(d) / "t.json") for _ in range(2)]
    etter_b = bilde()
    _log(f"uten EXECUTE: {[(x['feilet'], x['alarm']) for x in rb]}")

    frisk = _profiler(maaler)
    art = {
        "oppsett": {**_oppsett(runde),
                    "injeksjoner": ["statement_timeout 1 ms rundt hele runden",
                                    "tilkobling som disponit — uten EXECUTE"
                                    " på " + MAALEDOR]},
        "maalt": {
            "rundegrense_feilet": [bool(r.feilet) for r in ra],
            "rundegrense_alarm": [bool(r.alarm_utlost) for r in ra],
            "rundegrense_urort": for_a == etter_a,
            "injeksjonsrolle_har_execute": bool(har_execute),
            "uten_execute_feilet": [bool(x["feilet"]) for x in rb],
            "uten_execute_alarm": [bool(x["alarm"]) for x in rb],
            "uten_execute_returkoder": [x["returkode"] for x in rb],
            "uten_execute_urort": for_b == etter_b,
            "frisk_etterpa": not frisk["feilet"] and not frisk["avbrutt"],
        },
    }
    return _skriv("m03-feilinjisering-v1", art)


def _kjor_kode(rot: Path, dsn: str) -> dict:
    prog = (
        "import json\n"
        "from db.pg import koble\n"
        "from drift import kvalitetsprofilering as kp\n"
        f"c = koble({dsn!r})\n"
        "r = kp.kjor(c, grense=500)\n"
        "print(json.dumps({'kjoring_id': r.kjoring_id, 'feilet': r.feilet,"
        " 'avbrutt': r.avbrutt, 'antall_regler': r.antall_regler}))\n")
    env = {**os.environ,
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}"}
    p = subprocess.run([sys.executable, "-c", prog], cwd=rot / "platform",
                       env=env, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        return {"feilet": True, "avbrutt": True, "stderr": p.stderr[-400:]}
    return json.loads(p.stdout.strip().splitlines()[-1])


def rollback(forgjenger: Path) -> Path:
    sup = psycopg.connect(_env("M03_SUPER_DSN"))
    _er_isolert(sup)
    sup.rollback()
    sup.autocommit = True
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    maaler = _env("DISPONIT_TEST_KVALITETSMAALER_DSN")
    runde = secrets.token_hex(4)
    _profiler(maaler)
    rigg = forbered(mg, runde)
    _profiler(maaler)                          # riggens funn står åpne

    def apne() -> list[str]:
        a = _apne(sup, rigg)
        return [f"{m}|{'/'.join(v)}" for m, v in sorted(a.items())]

    kjoringer_for = sup.execute(
        "SELECT count(*) FROM kvalitetskjoring").fetchone()[0]
    # DRILLEN: riggens terskeltabell låses eksklusivt, så runden blir
    # stående INNE i en regel når den drepes.
    laas = f"fm3_{runde}_terskel"
    holder = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    holder.execute(f"LOCK TABLE {laas} IN ACCESS EXCLUSIVE MODE")
    env = {**os.environ, "DISPONIT_KVALITETSMAALER_URL": maaler,
           "DISPONIT_KVALITETSTILSTAND": str(Path(tempfile.mkdtemp())
                                            / "t.json"),
           "PYTHONPATH": f"{REPO / 'platform/core'}:{REPO / 'platform'}"}
    env.pop("CREDENTIALS_DIRECTORY", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "drift.kjor_kvalitetsprofilering"],
        cwd=REPO / "platform", env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    ventet = False
    for _ in range(100):
        ventet = sup.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_locks l JOIN pg_class c"
            " ON c.oid = l.relation WHERE c.relname = %s AND NOT l.granted)",
            (laas,)).fetchone()[0]
        if ventet:
            break
        time.sleep(0.1)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=30)
    backends = [r[0] for r in sup.execute(
        "SELECT pid FROM pg_stat_activity WHERE usename = %s",
        (MAALER,)).fetchall()]
    avsluttet = all(sup.execute("SELECT pg_terminate_backend(%s)", (b,))
                    .fetchone()[0] for b in backends)
    holder.rollback()
    holder.close()
    time.sleep(1)
    # Runden er ÉN setning: en drept runde etterlater INGEN kjøringsrad —
    # den kan ikke se fullført ut, for den finnes ikke.
    kjoringer_etter = sup.execute(
        "SELECT count(*) FROM kvalitetskjoring").fetchone()[0]
    from db.pg import koble
    from drift import kvalitetsprofilering as kp
    c = koble(maaler)
    try:
        nokkel_fri = c.execute("SELECT pg_try_advisory_lock(%s)",
                               (kp.ARBEIDERNOKKEL,)).fetchone()[0]
        if nokkel_fri:
            c.execute("SELECT pg_advisory_unlock(%s)", (kp.ARBEIDERNOKKEL,))
        c.commit()
    finally:
        c.close()
    _log(f"drill: ventet={ventet} backends={len(backends)}"
         f" avsluttet={avsluttet} kjøringer {kjoringer_for} →"
         f" {kjoringer_etter} nøkkel fri={nokkel_fri}")

    rb = _kjor_kode(forgjenger, maaler)
    apne_rb = apne()
    kd = _kjor_kode(REPO, maaler)
    apne_kd = apne()
    _log(f"rullbakk {rb} — {apne_rb}\nkandidat {kd} — {apne_kd}")
    art = {
        "oppsett": {**_oppsett(runde), "forgjenger": str(forgjenger),
                    "kjerne_forgjenger": kjerne_digest(forgjenger),
                    "kjerne_kandidat": kjerne_digest(REPO),
                    "modul_forgjenger": release_digest(forgjenger,
                                                       M03_MODULFILER),
                    "modul_kandidat": release_digest(REPO, M03_MODULFILER)},
        "maalt": {
            "drill_ventet_i_regelen": bool(ventet),
            "drill_backends_avsluttet": bool(avsluttet and backends),
            "drept_runde_etterlot_kjoring": kjoringer_etter != kjoringer_for,
            "arbeidernokkel_fri": bool(nokkel_fri),
            "rullbakk_fullfort": not rb.get("feilet") and not rb.get("avbrutt"),
            "kandidat_fullfort": not kd.get("feilet") and not kd.get("avbrutt"),
            "apne_etter_rullbakk": len([a for a in apne_rb
                                        if not a.endswith("|")]),
            "apne_etter_kandidat": len([a for a in apne_kd
                                        if not a.endswith("|")]),
            "apne_like": apne_rb == apne_kd,
        },
    }
    return _skriv("m03-rollback-v1", art)


def ytelse(dsn_fil: Path, runder: int) -> Path:
    """Den ekte jobben mot driftsbasen. Riggen rører ingenting her."""
    dsn = dsn_fil.read_text(encoding="utf-8").strip()
    with psycopg.connect(dsn) as c:
        db = c.execute("SELECT current_database()").fetchone()[0]
    kjoringer = []
    with tempfile.TemporaryDirectory() as d:
        for i in range(runder):
            t0 = time.monotonic()
            linje = _inngang(dsn, Path(d) / "t.json")
            sek = time.monotonic() - t0
            kjoringer.append({
                "sekunder": round(sek, 3),
                "regler": int(linje.get("antall_regler", 0)),
                "umaalbare": int(linje.get("antall_umaalbare", 0)),
                "avbrutt": bool(linje.get("avbrutt")),
                "feilet": bool(linje.get("feilet")),
                "hoppet_over": bool(linje.get("hoppet_over")),
                "returkode": linje["returkode"]})
            _log(f"kjøring {i + 1}: {kjoringer[-1]}")
    art = {
        "oppsett": {"modul": "m03_datakvalitet",
                    "vert": socket.gethostname(), "base": db,
                    "runder": runder,
                    "bevisrot_sha256": m03_bevisrot_sha256()},
        "maalt": {
            "kjoringer": kjoringer,
            "maks_sekunder": max(k["sekunder"] for k in kjoringer),
            "regler_maalt": sum(k["regler"] for k in kjoringer),
            "umaalbare": sum(k["umaalbare"] for k in kjoringer),
            "feilede": sum(1 for k in kjoringer if k["feilet"]),
        },
    }
    return _skriv("m03-ytelse-v1", art)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("punkt", choices=("fasit", "feilinjisering", "rollback",
                                      "ytelse"))
    ap.add_argument("--forgjenger", type=Path)
    ap.add_argument("--dsn-fil", type=Path)
    ap.add_argument("--runder", type=int, default=3)
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
        if not a.dsn_fil:
            sys.exit("--dsn-fil mangler")
        sti = ytelse(a.dsn_fil, a.runder)
    return 0 if json.loads(sti.read_text())["bestatt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
