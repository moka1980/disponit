#!/usr/bin/env python3
"""M-38s sertifiseringslest — klasse G i ADR-005 (kjernekrok).

M-38 skriver ingenting eget. Den er to kroker i kjernen: en per-tenant
plass-rangering som TREDJE sorteringsnøkkel i de to claim-funksjonene
(085), og en innholdsadressert memoisering av skjemavandringen i
`policyregister.hent_aktiv` (`m38.policycache`). Fasit, feilinjisering
og rollback måles i den ISOLERTE basen på 55432, bygget fra commiten som
måles; riggen går testsuitens egne hjelpere (test_m37,
test_bootstrap_ankerrad), som derfor står i bevisroten.

  fasit           syntetisk_datasett_likt_lokalt + revisjonslogg_korrekt.
                  En KJENT KØ i tre tenanter dreneres gjennom den ekte
                  claim-funksjonen; rekkefølgen sammenlignes med fasiten.
                  Oppdragskøen og prioritetsforrangen likeså. Memoiseringen
                  måles som antall skjemavandringer over gjentatte lesinger.
                  Revisjonsloggen: MÅLT FRAVÆR — en claim endrer bare
                  køtabellen, og ingen revisjonsrad skrives av krokene.
  feilinjisering  ugyldig claim-id, statement_timeout, trukket EXECUTE og
                  et korrupt lagret innhold ETTER et cachetreff: typet feil,
                  aldri en delvis claim, aldri et gammelt svar fra cachen.
  rollback        forgjengerens og kandidatens kjerne leser samme policy
                  og drenerer like køer: like svar.
  ytelse          claim under kølast (p95) og memoiseringens treffrate.

BRUK (på verten, som administrator, med riggens DSN-er eksportert):
  python deploy/staging/m38-lest.py fasit|feilinjisering|ytelse
  python deploy/staging/m38-lest.py rollback --forgjenger /opt/disponit/releases/<sha>
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (M38_MODULFILER, _sjekk_grenser,  # noqa: E402
                            kjerne_digest, m38_bevisrot_sha256,
                            release_digest, valider_artefaktformat)

UT = REPO / "deploy/staging/artefakter"

#: Sakskøen: A har seks (eldst), B én, C to (yngst). Arbeid i flukt koster
#: plass i partisjonen, så B1 og C1 går foran As plass-2-sak; ved lik plass
#: avgjør alderen. Anti-dominansen (fem levende claims per tenant) stopper
#: A etter A5 — A6 claimes når As saker er fullført.
SAKSKO = (("a", 6), ("b", 1), ("c", 2))
SAK_VENTET = ["a1", "b1", "c1", "a2", "c2", "a3", "a4", "a5", "a6"]
#: Oppdragskøen: D seks, E ett, samme eiermodul. Andre claim er Es.
OPPDRAG_VENTET_TO = ["d1", "e1"]
LESINGER = 50


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
    return {"modul": "m38_ruter", "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": m38_bevisrot_sha256()}


def _t37():
    from tests import test_m37 as t
    return t


def _mg():
    from db.pg import koble
    return koble(_env("DISPONIT_TEST_MIGRATOR_DSN"))


def _ko_tom(sup) -> bool:
    n = sup.execute(
        "SELECT count(*) FROM unntak WHERE sakstype='normal' AND status IN"
        " ('ny','verifikasjon_klar','verifikasjon_retry_klar')").fetchone()[0]
    sup.rollback()
    return n == 0


def _claim_sak(dsn: str) -> tuple[tuple[str, int] | None, list[str]]:
    """Én claim som runtime, med tabellene transaksjonen ENDRET målt fra
    innsiden (pg_stat_xact) før commit."""
    from db.pg import koble
    c = koble(dsn)
    try:
        cid = secrets.token_hex(16)
        c.execute("SELECT set_config('disponit.aktor','m37-arbeider',true),"
                  " set_config('disponit.request_id',%s,true)", (cid,))
        rad = c.execute("SELECT tenant, id FROM claim_neste_sak(%s,120)",
                        (cid,)).fetchone()
        endret = sorted(r[0] for r in c.execute(
            "SELECT relname FROM pg_stat_xact_user_tables"
            " WHERE n_tup_ins + n_tup_upd + n_tup_del > 0").fetchall())
        c.commit()
        return ((rad[0], int(rad[1])) if rad else None), endret
    finally:
        c.close()


def _claim_oppdrag(dsn: str) -> tuple[int | None, list[str]]:
    from db.pg import koble
    t = _t37()
    c = koble(dsn)
    try:
        c.execute("SELECT set_config('disponit.aktor','m37-arbeider',true),"
                  " set_config('disponit.request_id','m38',true)")
        rad = c.execute("SELECT id FROM claim_neste_oppdrag(%s,%s,%s,300)",
                        (t.EIERMODUL, ["purring."],
                         secrets.token_hex(16))).fetchone()
        endret = sorted(r[0] for r in c.execute(
            "SELECT relname FROM pg_stat_xact_user_tables"
            " WHERE n_tup_ins + n_tup_upd + n_tup_del > 0").fetchall())
        c.commit()
        return (int(rad[0]) if rad else None), endret
    finally:
        c.close()


def _fullfor(mg, tenant: str) -> None:
    t = _t37()
    t._sett_kontekst(mg, tenant, "m37-arbeider", "opprydd")
    mg.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s AND"
               " status='under_behandling'", (tenant,))
    mg.commit()


def _revisjonsrader(sup) -> int:
    n = sup.execute("SELECT count(*) FROM revisjonslogg").fetchone()[0]
    sup.rollback()
    return n


def _sakskoe(mg, runde: str, merke: str = "") -> tuple[dict, dict]:
    t = _t37()
    tenanter, navn = {}, {}
    for tkey, antall in SAKSKO:
        ten = f"t-m38fasit-{tkey}{merke}-{runde}"
        tenanter[tkey] = ten
        for i in range(antall):
            sid = t._lag_sak(mg, ten)[0]
            navn[(ten, int(sid))] = f"{tkey}{i + 1}"
    return tenanter, navn


def _dren_saker(dsn, mg, tenanter, navn) -> tuple[list[str], set[str]]:
    rekke, endret = [], set()
    for runde in range(2):
        while True:
            rad, e = _claim_sak(dsn)
            if rad is None:
                break
            endret.update(e)
            rekke.append(navn.get(rad, f"fremmed:{rad[0]}"))
        if runde == 0:
            _fullfor(mg, tenanter["a"])
    return rekke, endret


def _policy_lesinger(mg_dsn: str, tenant: str, pid: str, n: int) -> dict:
    """`n` lesinger av samme aktive policy; teller skjemavandringene ved å
    pakke `valider_policy` (instrumentering — svaret endres ikke)."""
    from db.pg import koble, sett_kontekst
    import policy_validator.schema as skjema
    from api import policyregister as pr
    from m38 import policycache
    teller = {"n": 0}
    ekte = skjema.valider_policy

    def tellende(innhold):
        teller["n"] += 1
        return ekte(innhold)
    skjema.valider_policy = tellende
    policycache.toem()
    svar, ms = set(), []
    c = koble(mg_dsn)
    try:
        for i in range(n):
            sett_kontekst(c, tenant, "sys", f"m38-{i}")
            t0 = time.monotonic()
            pol, h = pr.hent_aktiv(c, tenant, pid)
            ms.append((time.monotonic() - t0) * 1000)
            c.rollback()
            svar.add(h)
    finally:
        c.close()
        skjema.valider_policy = ekte
    return {"lesinger": n, "valideringer": teller["n"],
            "ulike_svar": len(svar), "ms": [round(x, 3) for x in ms]}


def _registrer_policy(mg, tenant: str, pid: str, versjon: str) -> str:
    from db.pg import sett_kontekst
    from tests.test_bootstrap_ankerrad import _policy
    from api import policyregister as pr
    sett_kontekst(mg, tenant, "sys", "m38-reg")
    h = pr.registrer(mg, tenant, _policy(versjon, policy_id=pid),
                     "produksjon")
    mg.commit()
    return h


# ---------------------------------------------------------------------------

def fasit() -> Path:
    sup = psycopg.connect(_env("M38_SUPER_DSN"))
    _er_isolert(sup)
    if not _ko_tom(sup):
        sys.exit("køen er ikke tom — kjør fasiten først etter byggingen")
    dsn, mdsn = _env("DISPONIT_TEST_DSN"), _env("DISPONIT_TEST_MIGRATOR_DSN")
    t = _t37()
    mg = _mg()
    runde = secrets.token_hex(4)
    rev0 = _revisjonsrader(sup)

    # 1. Sakskøen.
    tenanter, navn = _sakskoe(mg, runde)
    rev_for = _revisjonsrader(sup)
    sak_rekke, sak_endret = _dren_saker(dsn, mg, tenanter, navn)
    rev_claim = _revisjonsrader(sup) - rev_for

    # 2. Oppdragskøen.
    dten, eten = f"t-m38fasit-d-{runde}", f"t-m38fasit-e-{runde}"
    onavn = {}
    for ten, k, antall in ((dten, "d", 6), (eten, "e", 1)):
        for i in range(antall):
            sak, logg = t._lag_sak(mg, ten)
            onavn[t._lag_oppdrag(mg, ten, sak, logg)[0]] = f"{k}{i + 1}"
    # Sakene bak oppdragene skal ikke forstyrre sakskøen senere.
    for ten in (dten, eten):
        t._sett_kontekst(mg, ten, "m37-arbeider", "opprydd")
        mg.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s"
                   " AND status='ny'", (ten,))
    mg.commit()
    opp_rekke, opp_endret = [], set()
    rev_for = _revisjonsrader(sup)
    while True:
        oid, e = _claim_oppdrag(dsn)
        if oid is None:
            break
        opp_endret.update(e)
        opp_rekke.append(onavn.get(oid, f"fremmed:{oid}"))
    rev_claim += _revisjonsrader(sup) - rev_for

    # 3. Prioriteten går foran fairness (negativ kontroll for nøkkelens
    #    plass): Fs hoy-sak står bakerst i Fs egen backlog, men claimes
    #    først. Prioriteten settes som suiten gjør det (vakten av, som
    #    migrator — test_m37 sin form).
    ften, gten = f"t-m38fasit-f-{runde}", f"t-m38fasit-g-{runde}"
    for _ in range(3):
        t._lag_sak(mg, ften)
    t._lag_sak(mg, gten)
    hoy = t._lag_sak(mg, ften)[0]
    mg.execute("SET LOCAL ROLE disponit_migrator")
    t._sett_kontekst(mg, ften)
    mg.execute("ALTER TABLE unntak DISABLE TRIGGER unntak_laas")
    mg.execute("UPDATE unntak SET prioritet='hoy' WHERE tenant=%s AND id=%s",
               (ften, hoy))
    mg.execute("SET CONSTRAINTS ALL IMMEDIATE")
    mg.execute("ALTER TABLE unntak ENABLE TRIGGER unntak_laas")
    mg.commit()
    forste, _e = _claim_sak(dsn)
    prioritet_forst = forste == (ften, int(hoy))
    # Dren resten, så køen er tom for neste punkt.
    for ten in (ften, gten):
        _fullfor(mg, ten)
    while _claim_sak(dsn)[0] is not None:
        pass
    for ten in (ften, gten):
        _fullfor(mg, ten)

    # 4. Memoiseringen.
    pten = f"t-m38fasit-p-{runde}"
    pid = "m38-" + secrets.token_hex(3)
    _registrer_policy(mg, pten, pid, "1.0.0")
    # Lesingene alene telles: registreringen av 1.0.1 er
    # policyregisterets skrivevei, ikke M-38s, og står mellom målingene.
    rev_for = _revisjonsrader(sup)
    les1 = _policy_lesinger(mdsn, pten, pid, LESINGER)
    rev_cache = _revisjonsrader(sup) - rev_for
    _registrer_policy(mg, pten, pid, "1.0.1")
    rev_for = _revisjonsrader(sup)
    les2 = _policy_lesinger(mdsn, pten, pid, 2)
    rev_cache += _revisjonsrader(sup) - rev_for

    _log(f"saker {sak_rekke}; oppdrag {opp_rekke}; prioritet først"
         f" {prioritet_forst}; lesinger {les1['valideringer']}/"
         f"{les1['lesinger']}, etter ny versjon {les2['valideringer']}")
    art = {
        "oppsett": {**_oppsett(runde), "tenanter": {**tenanter, "d": dten,
                                                    "e": eten, "f": ften,
                                                    "g": gten, "p": pten}},
        "maalt": {
            "sak_rekkefolge": sak_rekke,
            "sak_ventet": SAK_VENTET,
            "oppdrag_rekkefolge": opp_rekke,
            "oppdrag_ventet_to": OPPDRAG_VENTET_TO,
            "oppdrag_alle": sorted(opp_rekke) == sorted(onavn.values()),
            "prioritet_forst": prioritet_forst,
            "cache_lesinger": les1["lesinger"],
            "cache_valideringer": les1["valideringer"],
            "cache_ulike_svar": les1["ulike_svar"],
            "cache_ny_versjon_valideringer": les2["valideringer"],
            "claim_endrede_tabeller_sak": sorted(sak_endret),
            "claim_endrede_tabeller_oppdrag": sorted(opp_endret),
            "nye_revisjonsrader_claim": rev_claim,
            "nye_revisjonsrader_cache": rev_cache,
            "revisjonsrader_totalt_endret": _revisjonsrader(sup) - rev0,
        },
    }
    return _skriv("m38-fasit-v1", art)


def _digest_unntak(sup) -> str:
    r = sup.execute("SELECT count(*), coalesce(sum(hashtext(u::text)"
                    "::bigint),0) FROM unntak u").fetchone()
    sup.rollback()
    return f"{r[0]}:{r[1]}"


def feilinjisering() -> Path:
    sup = psycopg.connect(_env("M38_SUPER_DSN"))
    _er_isolert(sup)
    dsn, mdsn = _env("DISPONIT_TEST_DSN"), _env("DISPONIT_TEST_MIGRATOR_DSN")
    t = _t37()
    mg = _mg()
    runde = secrets.token_hex(4)
    ten = f"t-m38brudd-{runde}"
    for _ in range(40):
        t._lag_sak(mg, ten)
    from db.pg import koble
    ut = []

    def kjor(navn, fn):
        for_ = _digest_unntak(sup)
        avvist, sqlstate, tekst = False, None, None
        try:
            fn()
        except psycopg.Error as e:
            avvist, sqlstate, tekst = True, e.sqlstate, str(e)[:200]
        except Exception as e:                          # noqa: BLE001
            avvist, tekst = True, f"{type(e).__name__}: {e}"[:200]
        ut.append({"brudd": navn, "avvist": avvist, "sqlstate": sqlstate,
                   "feiltype": tekst, "urort": _digest_unntak(sup) == for_})
        _log(f"{navn}: {ut[-1]}")

    def ugyldig_claim_id():
        c = koble(dsn)
        try:
            c.execute("SELECT * FROM claim_neste_sak('ikke-hex',120)")
        finally:
            c.rollback()
            c.close()

    def statement_timeout():
        c = koble(dsn)
        try:
            c.execute("SET statement_timeout = 1")
            c.execute("SELECT pg_sleep(0.01), * FROM claim_neste_sak(%s,120)",
                      (secrets.token_hex(16),))
            c.commit()
        finally:
            c.rollback()
            c.close()

    def execute_trukket():
        mg.execute("SET ROLE disponit_m37_claimer")
        mg.execute("REVOKE EXECUTE ON FUNCTION claim_neste_sak(text,integer)"
                   " FROM disponit")
        mg.execute("RESET ROLE")
        mg.commit()
        try:
            c = koble(dsn)
            try:
                c.execute("SELECT * FROM claim_neste_sak(%s,120)",
                          (secrets.token_hex(16),))
                c.commit()
            finally:
                c.rollback()
                c.close()
        finally:
            mg.execute("SET ROLE disponit_m37_claimer")
            mg.execute("GRANT EXECUTE ON FUNCTION claim_neste_sak(text,"
                       "integer) TO disponit")
            mg.execute("RESET ROLE")
            mg.commit()

    kjor("ugyldig_claim_id", ugyldig_claim_id)
    kjor("statement_timeout", statement_timeout)
    kjor("execute_trukket", execute_trukket)

    # Korrupt lagret innhold ETTER et cachetreff: rekomputeringen står
    # foran memoiseringen, så treffet kan aldri gi et gammelt svar.
    pid = "m38b-" + secrets.token_hex(3)
    _registrer_policy(mg, ten, pid, "1.0.0")
    for_ = _policy_lesinger(mdsn, ten, pid, 2)
    from db.pg import sett_kontekst
    from api import policyregister as pr
    # DB-korrupsjon som suiten gjør den: innholdet endres, hash-kolonnen
    # står urørt.
    sett_kontekst(mg, ten, "sys", "m38-korrupt")
    mg.execute("UPDATE policyer SET innhold = jsonb_set(innhold,"
               " '{tidssone}', '\"Mars/Olympus\"') WHERE tenant=%s"
               " AND policy_id=%s AND aktiv", (ten, pid))
    mg.commit()
    c = koble(mdsn)
    korrupt = {"brudd": "korrupt_innhold_etter_treff", "avvist": False,
               "sqlstate": None, "feiltype": None, "urort": True,
               "treff_for": for_["valideringer"] == 1}
    try:
        sett_kontekst(c, ten, "sys", "m38-les")
        pr.hent_aktiv(c, ten, pid)
    except pr.PolicyKorrupt as e:
        korrupt["avvist"], korrupt["feiltype"] = True, \
            f"PolicyKorrupt: {str(e)[:160]}"
    finally:
        c.rollback()
        c.close()
    ut.append(korrupt)
    _log(f"korrupt: {korrupt}")
    # Bruddkøen tømmes, så rulleseriens drenering bare ser sine egne.
    t._sett_kontekst(mg, ten, "m37-arbeider", "opprydd")
    mg.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s"
               " AND status IN ('ny','under_behandling')", (ten,))
    mg.commit()
    art = {"oppsett": _oppsett(runde),
           "maalt": {"brudd": ut, "injiserte": len(ut)}}
    return _skriv("m38-feilinjisering-v1", art)


KALLER = r"""
import json, secrets, sys
from db.pg import koble, sett_kontekst
from api import policyregister as pr
from m38 import policycache
import policy_validator.schema as skjema
dsn, mdsn, tenant, pid = sys.argv[1:5]
teller = {"n": 0}
ekte = skjema.valider_policy
def tellende(i):
    teller["n"] += 1
    return ekte(i)
skjema.valider_policy = tellende
policycache.toem()
hasher = []
c = koble(mdsn)
for i in range(10):
    sett_kontekst(c, tenant, "sys", f"rb-{i}")
    pol, h = pr.hent_aktiv(c, tenant, pid)
    c.rollback()
    hasher.append(h)
c.close()
rekke = []
while True:
    c = koble(dsn)
    cid = secrets.token_hex(16)
    rad = c.execute("SELECT tenant, id FROM claim_neste_sak(%s,120)",
                    (cid,)).fetchone()
    c.commit(); c.close()
    if rad is None:
        break
    rekke.append([rad[0], int(rad[1])])
print(json.dumps({"valideringer": teller["n"], "hasher": sorted(set(hasher)),
                  "rekke": rekke}))
"""


def _kjor_i(rot: Path, *args) -> dict:
    env = {**os.environ,
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}"}
    p = subprocess.run([sys.executable, "-c", KALLER, *args],
                       cwd=rot / "platform", env=env, capture_output=True,
                       text=True, timeout=600)
    if p.returncode != 0:
        sys.exit(f"kalleren i {rot} feilet:\n{p.stderr[-2000:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def rollback(forgjenger: Path) -> Path:
    sup = psycopg.connect(_env("M38_SUPER_DSN"))
    _er_isolert(sup)
    dsn, mdsn = _env("DISPONIT_TEST_DSN"), _env("DISPONIT_TEST_MIGRATOR_DSN")
    mg = _mg()
    runde = secrets.token_hex(4)
    pten = f"t-m38rb-p-{runde}"
    pid = "m38rb-" + secrets.token_hex(3)
    _registrer_policy(mg, pten, pid, "1.0.0")

    def en_side(merke, rot):
        tenanter, navn = _sakskoe(mg, runde, merke)
        # Anti-dominansen stopper A etter fem; kalleren drenerer én gang.
        svar = _kjor_i(rot, dsn, mdsn, pten, pid)
        rekke = [navn.get((t_, i), "fremmed") for t_, i in svar["rekke"]]
        for ten in tenanter.values():
            _fullfor(mg, ten)
        while _claim_sak(dsn)[0] is not None:
            pass
        for ten in tenanter.values():
            _fullfor(mg, ten)
        return {"valideringer": svar["valideringer"],
                "hasher": svar["hasher"], "rekke": rekke}
    rb = en_side("rb", forgjenger)
    kd = en_side("kd", REPO)
    art = {
        "oppsett": {**_oppsett(runde), "forgjenger": str(forgjenger),
                    "kjerne_forgjenger": kjerne_digest(forgjenger),
                    "kjerne_kandidat": kjerne_digest(REPO),
                    "modul_forgjenger": release_digest(forgjenger,
                                                       M38_MODULFILER),
                    "modul_kandidat": release_digest(REPO, M38_MODULFILER)},
        "maalt": {"forgjenger": rb, "kandidat": kd,
                  "svar_like": rb == kd,
                  "kandidat_lik_fasit": kd["rekke"] == SAK_VENTET[:8]
                  and kd["valideringer"] == 1},
    }
    _log(f"forgjenger {rb}; kandidat {kd}")
    return _skriv("m38-rollback-v1", art)


def ytelse(kall: int) -> Path:
    sup = psycopg.connect(_env("M38_SUPER_DSN"))
    _er_isolert(sup)
    dsn, mdsn = _env("DISPONIT_TEST_DSN"), _env("DISPONIT_TEST_MIGRATOR_DSN")
    t = _t37()
    mg = _mg()
    runde = secrets.token_hex(4)
    # KØLAST: 10 tenanter à 50 saker — rangeringen ser 500 rader.
    tenanter = [f"t-m38y-{i}-{runde}" for i in range(10)]
    for ten in tenanter:
        for _ in range(50):
            t._lag_sak(mg, ten)
    ms, feil, claimet = [], 0, 0
    for i in range(kall):
        if i and i % 50 == 0:
            for ten in tenanter:
                _fullfor(mg, ten)
        t0 = time.monotonic()
        try:
            rad, _e = _claim_sak(dsn)
            claimet += 1 if rad else 0
        except psycopg.Error:
            feil += 1
        ms.append((time.monotonic() - t0) * 1000)
    pid = "m38y-" + secrets.token_hex(3)
    _registrer_policy(mg, tenanter[0], pid, "1.0.0")
    les = _policy_lesinger(mdsn, tenanter[0], pid, kall)
    s = sorted(ms)
    p95 = s[max(0, int(round(0.95 * len(s))) - 1)]
    treff = (les["lesinger"] - les["valideringer"]) / les["lesinger"]
    _log(f"{kall} claims under kølast: p95={p95:.2f} ms; treffrate {treff}")
    art = {"oppsett": _oppsett(runde),
           "maalt": {"kall": kall, "feilede": feil, "claimet": claimet,
                     "koelengde": 500, "p95_ms": round(p95, 3),
                     "maks_ms": round(s[-1], 3),
                     "ms": [round(x, 3) for x in ms],
                     "cache_lesinger": les["lesinger"],
                     "cache_valideringer": les["valideringer"],
                     "treffrate": round(treff, 4)}}
    return _skriv("m38-ytelse-v1", art)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("punkt", choices=("fasit", "feilinjisering", "rollback",
                                      "ytelse"))
    ap.add_argument("--forgjenger", type=Path)
    ap.add_argument("--kall", type=int, default=200)
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
        sti = ytelse(a.kall)
    return 0 if json.loads(sti.read_text())["bestatt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
