#!/usr/bin/env python3
"""M-08s sertifiseringslest — klasse C i ADR-002 (tenantregister uten sveip).

M-8 v1 er tidsvalg-benen M-57s invitasjoner trenger (082): kunden
oppretter og deaktiverer intervjuslots, utsenderen utsteder et
kapabilitetstoken per kandidat, og kandidaten velger en tid uten
innlogging. Fra 214 skriver hver av de fire skrivende veiene sin
hendelse i evidenskjeden.

RIGGEN GÅR HUSETS EGEN VEI GJENNOM M-57-KJEDEN. En slot kan bare leve
under en levende rekrutteringsprosess, og et token bare for en kandidat
på en SIGNERT invitasjonsliste. Den lovlige veien dit er lang (oppdrag,
kryptert payload, liste, signatur), og den finnes allerede, målt, i
testsuitens hjelpere (`test_m57_utsending._prosess_med_kandidater`,
`test_m57_utsender._signert_liste`, `test_m8_tidsvalg._slot/_utsted/
_velg`). Produsenten bruker DEM — med tenanten byttet per kjøring —
framfor å skrive en tredje kopi av kjeden som kunne drevet fra de to.
Testfilene er derfor en del av bevisroten.

Alle fire punktene måles i den ISOLERTE basen på 55432, bygget fra
commiten som måles.

  fasit           kjent dørsekvens i to tenanter; sluttilstand re-regnet
                  av radene; et gjentatt valg er et stille ja; evidensen
                  telles per handling, med aktør og egen input_hash.
  feilinjisering  fem brudd: fire vakter som skal kaste typet, og et valg
                  med feil MAC som skal få det uniforme avslaget. Ingen
                  skal etterlate spor.
  rollback        forgjengerens og kandidatens kjerne svarer likt på
                  `GET /v1/rekruttering/tidsvalg`.
  ytelse          kandidatens oppslag under last, p95 mot en grense.

BRUK (på verten, som administrator, med riggens DSN-er eksportert):
  python deploy/staging/m08-lest.py fasit|feilinjisering|ytelse
  python deploy/staging/m08-lest.py rollback --forgjenger /opt/disponit/releases/<sha>
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

#: Testmiljøet hjelperne forutsetter (`test_api.miljo`). Riggbasen er en
#: testbase; verdiene er testsuitens egne.
MILJO = {
    "DISPONIT_TOKEN_PEPPER": "p" * 40,
    "DISPONIT_KEK": "b" * 64,
    "DISPONIT_ATT_NOKLER": json.dumps({"v_fordring": {"k1": "x" * 40}}),
    "DISPONIT_MAC_NOKLER": json.dumps(
        {"mk1": {"rolle": "signerer", "hemmelighet": "m" * 40}}),
    "DISPONIT_RATE_PER_MIN": "100000",
}
os.environ.update(MILJO)
for _k in ("DISPONIT_MILJO", "DISPONIT_TLS_AKTIV"):
    os.environ.pop(_k, None)

import psycopg  # noqa: E402

from manifestskjema import (M08_MODULFILER, _sjekk_grenser,  # noqa: E402
                            kjerne_digest, m08_bevisrot_sha256,
                            release_digest, valider_artefaktformat)

UT = REPO / "deploy/staging/artefakter"
EVIDENSKILDE = "m08_kalender"
TABELLER = ("m8_slot", "m8_slotvalg", "m8_tidsvalgtoken")


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def _env(navn: str) -> str:
    v = os.environ.get(navn)
    if not v:
        sys.exit(f"{navn} mangler — kjør med riggens DSN-er eksportert")
    return v


def _hjelpere():
    """Testsuitens hjelpere, importert som pakken de er."""
    from tests import test_m8_tidsvalg as t8
    from tests import test_m57_utsender as tu
    from tests import test_m57_utsending as ts
    return t8, tu, ts


def _sett_tenant(tenant: str) -> None:
    """Hjelperne leser modulkonstanten TENANT. Den byttes i HVER lastet
    testmodul som bærer den, så kjeden ikke blandes mellom tenanter."""
    for navn, mod in list(sys.modules.items()):
        if navn.startswith("tests.") and hasattr(mod, "TENANT"):
            mod.TENANT = tenant


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
                              sort_keys=True, default=str) + "\n",
                   encoding="utf-8")
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
    return {"modul": "m08_kalender", "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": m08_bevisrot_sha256()}


# ---------------------------------------------------------------------------
# Riggen.
# ---------------------------------------------------------------------------

#: tenant → (kandidater, slots, deaktiverte av dem)
SETT = {"a": (2, 3, 1), "b": (1, 1, 0)}
#: Forventet sluttilstand per tenant, re-regnet av radene.
FORVENTET = {
    "a": {"slots": {"aktiv": 2, "deaktivert": 1}, "valg": 2,
          "tokener": {"brukt": 2}},
    "b": {"slots": {"aktiv": 1}, "valg": 1, "tokener": {"brukt": 1}},
}
FORVENTET_EVIDENS = {
    "a": {"slot.opprettet": 3, "slot.deaktivert": 1,
          "tidsvalgtoken.utstedt": 2, "slotvalg.valgt": 2},
    "b": {"slot.opprettet": 1, "tidsvalgtoken.utstedt": 1,
          "slotvalg.valgt": 1},
}


def forbered(mg, rt, runde: str) -> dict:
    """Dørsekvensen. Tenant A: to kandidater, tre slots med kapasitet 1,
    den tredje deaktiveres. Kandidat 1 velger slot 1; kandidat 2 prøver
    slot 1 (full — ingen rad), så slot 2. Tenant B: én av hver."""
    t8, tu, ts = _hjelpere()
    rigg = {"tenanter": {}, "prosesser": {}, "slots": {}, "tokener": {},
            "utfall": {}}
    for tkey, (n_kand, n_slot, n_deakt) in SETT.items():
        tenant = f"t-m08fasit-{tkey}-{runde}"
        _sett_tenant(tenant)
        rigg["tenanter"][tkey] = tenant
        oid, pid, kids, _ = ts._prosess_med_kandidater(mg, n_kand)
        lid = tu._signert_liste(mg, kids, oid)
        sids = [t8._slot(mg, pid, timer=24 + 2 * i) for i in range(n_slot)]
        for sid in sids[n_slot - n_deakt:]:
            ts._sett_kontekst(mg, tenant)
            mg.execute("SET ROLE disponit_m37_claimer")
            mg.execute("SELECT m8_deaktiver_slot(%s,%s)", (tenant, sid))
            mg.execute("RESET ROLE")
            mg.commit()
        toks = [t8._utsted(mg, lid, kid)[:2] for kid in kids]
        utfall = []
        utfall.append(t8._velg(rt, *toks[0], sids[0])[0])
        rt.commit()
        if n_kand > 1:
            utfall.append(t8._velg(rt, *toks[1], sids[0])[0])   # full
            rt.commit()
            utfall.append(t8._velg(rt, *toks[1], sids[1])[0])
            rt.commit()
        rigg["prosesser"][tkey] = str(pid)
        rigg["slots"][tkey] = [str(s) for s in sids]
        rigg["tokener"][tkey] = toks
        rigg["utfall"][tkey] = utfall
    return rigg


def _tilstand(sup, tenant: str) -> dict:
    slots = dict(sup.execute(
        "SELECT status, count(*) FROM m8_slot WHERE tenant=%s GROUP BY 1",
        (tenant,)).fetchall())
    valg = sup.execute(
        "SELECT count(*) FROM m8_slotvalg WHERE tenant=%s AND slettet_ts"
        " IS NULL", (tenant,)).fetchone()[0]
    tok = dict(sup.execute(
        "SELECT status, count(*) FROM m8_tidsvalgtoken WHERE tenant=%s"
        " GROUP BY 1", (tenant,)).fetchall())
    sup.rollback()
    return {"slots": slots, "valg": valg, "tokener": tok}


def _evidens(sup, tenant: str) -> dict:
    rader = sup.execute(
        "SELECT handling, aktor, input_hash FROM revisjonslogg"
        " WHERE tenant=%s AND kilde=%s", (tenant, EVIDENSKILDE)).fetchall()
    sup.rollback()
    per: dict[str, int] = {}
    for h, _a, _i in rader:
        per[h] = per.get(h, 0) + 1
    return {"per_handling": per, "totalt": len(rader),
            "uten_aktor": sum(1 for _h, a, _i in rader
                              if not a or a == "ukjent"),
            "delte_input_hash": len(rader) - len({i for *_x, i in rader})}


def _digest(sup) -> str:
    h = hashlib.sha256()
    for t in TABELLER + ("revisjonslogg",):
        n, s = sup.execute(
            f"SELECT count(*), coalesce(sum(hashtext(x::text)::bigint), 0)"
            f" FROM public.{t} x").fetchone()
        h.update(f"{t}:{n}:{s}\n".encode())
    sup.rollback()
    return h.hexdigest()


def _koble():
    sup = psycopg.connect(_env("M08_SUPER_DSN"))
    _er_isolert(sup)
    from db.pg import koble
    mg = koble(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    rt = koble(_env("DISPONIT_TEST_DSN"))
    return sup, mg, rt


# ---------------------------------------------------------------------------

def fasit() -> Path:
    sup, mg, rt = _koble()
    runde = secrets.token_hex(4)
    rigg = forbered(mg, rt, runde)
    _log(f"rigget runde {runde}: utfall {rigg['utfall']}")
    per, avvik = [], 0
    for tkey, tenant in rigg["tenanter"].items():
        fikk = _tilstand(sup, tenant)
        avvik += 0 if fikk == FORVENTET[tkey] else 1
        per.append({"tenant": tkey, "ventet": FORVENTET[tkey], "fikk": fikk})
    utfall_ok = (rigg["utfall"]["a"] == ["valgt", "slot_fullt", "valgt"]
                 and rigg["utfall"]["b"] == ["valgt"])

    # GJENSPILL: samme token, samme slot — et stille ja. Døra bekrefter
    # valget på nytt (`valgt`, samme tid) uten å skrive en eneste rad.
    t8, _tu, _ts = _hjelpere()
    for_ = _digest(sup)
    ta = rigg["tenanter"]["a"]
    _sett_tenant(ta)
    gjen = t8._velg(rt, *rigg["tokener"]["a"][0], rigg["slots"]["a"][0])[0]
    rt.commit()
    etter = _digest(sup)

    ev = {k: _evidens(sup, t) for k, t in rigg["tenanter"].items()}
    ev_avvik = sum(1 for k in SETT
                   if ev[k]["per_handling"] != FORVENTET_EVIDENS[k])
    _log(f"tilstand avvik {avvik}; gjenspill {gjen!r} urørt={for_ == etter};"
         f" evidens {ev}")
    art = {
        "oppsett": {**_oppsett(runde), "tenanter": rigg["tenanter"]},
        "maalt": {
            "per_tenant": per, "tilstandsavvik": avvik,
            "tenanter": len(per), "utfall": rigg["utfall"],
            "utfall_lik_fasit": utfall_ok,
            "gjenspill_utfall": gjen,
            "gjenspill_urort": for_ == etter,
            "evidens_per_tenant": {k: v["per_handling"]
                                   for k, v in ev.items()},
            "evidens_ventet": FORVENTET_EVIDENS,
            "evidens_avvik": ev_avvik,
            "evidens_totalt": sum(v["totalt"] for v in ev.values()),
            "evidens_uten_aktor": sum(v["uten_aktor"] for v in ev.values()),
            "evidens_delte_input_hash": sum(v["delte_input_hash"]
                                            for v in ev.values()),
        },
    }
    return _skriv("m08-fasit-v1", art)


def feilinjisering() -> Path:
    sup, mg, rt = _koble()
    runde = secrets.token_hex(4)
    rigg = forbered(mg, rt, runde)
    t8, _tu, ts = _hjelpere()
    ta = rigg["tenanter"]["a"]
    _sett_tenant(ta)
    pid = rigg["prosesser"]["a"]
    s_valgt = rigg["slots"]["a"][0]

    def som_claimer(c, sql, args):
        ts._sett_kontekst(c, ta)
        c.execute("SET ROLE disponit_m37_claimer")
        rad = c.execute(sql, args).fetchone()
        return rad

    brudd = [
        ("deaktiver_slot_med_valg", lambda: som_claimer(
            mg, "SELECT m8_deaktiver_slot(%s,%s)", (ta, s_valgt))),
        ("materiell_idempotenskonflikt", lambda: som_claimer(
            mg, "SELECT m8_opprett_slot(%s,%s, now() + interval '9 days',"
            " now() + interval '10 days', 1, %s)", (ta, pid, s_valgt))),
        ("dor_uten_tenantkontekst", lambda: (
            mg.execute("SET ROLE disponit_m37_claimer"),
            mg.execute("SELECT m8_opprett_slot(%s,%s, now() + interval"
                       " '9 days', now() + interval '10 days', 1, %s)",
                       (ta, pid, str(uuid.uuid4()))))),
        ("flytt_slottid", lambda: som_claimer(
            mg, "UPDATE m8_slot SET start_ts = start_ts + interval '1 hour'"
            " WHERE tenant=%s AND slot_id=%s RETURNING 1", (ta, s_valgt))),
    ]
    ut = []
    for navn, fn in brudd:
        for_ = _digest(sup)
        sqlstate, avvist = None, False
        try:
            fn()
            mg.commit()
        except psycopg.Error as e:
            mg.rollback()
            avvist, sqlstate = True, e.sqlstate
        ut.append({"brudd": navn, "avvist": avvist, "sqlstate": sqlstate,
                   "utfall": None, "urort": for_ == _digest(sup)})
        _log(f"{navn}: {ut[-1]}")
    # Feil MAC: den offentlige døra kaster ikke — den gir det UNIFORME
    # avslaget, NULL rader, så en gjetter ikke lærer noe av feilformen.
    for_ = _digest(sup)
    tid, _secret = rigg["tokener"]["a"][1]
    svar = t8._velg(rt, tid, "feil-hemmelighet", rigg["slots"]["a"][1])
    rt.commit()
    utfall = svar[0] if svar else "uniformt_avslag"
    ut.append({"brudd": "valg_med_feil_mac", "avvist": utfall != "valgt",
               "sqlstate": None, "utfall": utfall,
               "urort": for_ == _digest(sup)})
    _log(f"valg_med_feil_mac: {ut[-1]}")
    art = {"oppsett": _oppsett(runde),
           "maalt": {"brudd": ut, "injiserte": len(ut)}}
    return _skriv("m08-feilinjisering-v1", art)


def _token(mg, tenant: str) -> str:
    token_id = "tk_" + secrets.token_hex(8)
    hemmelig = secrets.token_urlsafe(32)
    mac = hmac.new(MILJO["DISPONIT_TOKEN_PEPPER"].encode(),
                   hemmelig.encode(), hashlib.sha256).hexdigest()
    mg.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes,"
        " secret_mac, status) VALUES (%s,%s,'agent',%s,%s,'AKTIV')",
        (token_id, tenant, ["decisions:read"], mac))
    mg.commit()
    return f"{token_id}.{hemmelig}"


KALLER = r"""
import json, sys
from api.app import lag_app
from starlette.testclient import TestClient
dsn, tok, pid = sys.argv[1], sys.argv[2], sys.argv[3]
app = lag_app(dsn)
with TestClient(app) as k:
    r = k.get(f"/v1/rekruttering/tidsvalg?prosess_id={pid}",
              headers={"authorization": f"Bearer {tok}"})
    body = r.json()
app.tjeneste.pool.lukk()
body.pop("request_id", None)
print(json.dumps({"status": r.status_code, "body": body}, sort_keys=True))
"""


def _http(rot: Path, tok: str, pid: str) -> dict:
    env = {**os.environ,
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}"}
    env.pop("CREDENTIALS_DIRECTORY", None)
    p = subprocess.run([sys.executable, "-c", KALLER,
                        _env("DISPONIT_TEST_DSN"), tok, pid],
                       cwd=rot / "platform", env=env, capture_output=True,
                       text=True, timeout=300)
    if p.returncode != 0:
        sys.exit(f"kalleren feilet:\n{p.stderr[-2000:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def rollback(forgjenger: Path) -> Path:
    sup, mg, rt = _koble()
    runde = secrets.token_hex(4)
    rigg = forbered(mg, rt, runde)
    ta, pid = rigg["tenanter"]["a"], rigg["prosesser"]["a"]
    tok = _token(mg, ta)
    rb, kd = _http(forgjenger, tok, pid), _http(REPO, tok, pid)
    kd_tekst = json.dumps(kd["body"])
    art = {
        "oppsett": {**_oppsett(runde), "forgjenger": str(forgjenger),
                    "kjerne_forgjenger": kjerne_digest(forgjenger),
                    "kjerne_kandidat": kjerne_digest(REPO),
                    "modul_forgjenger": release_digest(forgjenger,
                                                       M08_MODULFILER),
                    "modul_kandidat": release_digest(REPO, M08_MODULFILER)},
        "maalt": {
            "rullbakk_status": rb["status"], "kandidat_status": kd["status"],
            "svar_like": rb == kd,
            "kandidat_baerer_riggen": all(s in kd_tekst
                                          for s in rigg["slots"]["a"]),
        },
    }
    _log(f"rullbakk {rb['status']}, kandidat {kd['status']}, like={rb == kd}")
    return _skriv("m08-rollback-v1", art)


def ytelse(kall: int) -> Path:
    sup, mg, rt = _koble()
    runde = secrets.token_hex(4)
    rigg = forbered(mg, rt, runde)
    t8, _tu, _ts = _hjelpere()
    _sett_tenant(rigg["tenanter"]["a"])
    tid, secret = rigg["tokener"]["a"][0]
    ms, feil, riktige = [], 0, 0
    for _ in range(kall):
        t0 = time.monotonic()
        try:
            rader = t8._oppslag(rt, tid, secret)
            rt.rollback()
            riktige += 1 if rader else 0
        except psycopg.Error:
            rt.rollback()
            feil += 1
        ms.append((time.monotonic() - t0) * 1000)
    s = sorted(ms)
    p95 = s[max(0, int(round(0.95 * len(s))) - 1)]
    _log(f"{kall} oppslag: p50={s[len(s) // 2]:.2f} ms p95={p95:.2f} ms")
    art = {"oppsett": _oppsett(runde),
           "maalt": {"kall": kall, "feilede": feil, "riktige": riktige,
                     "p50_ms": round(s[len(s) // 2], 3),
                     "p95_ms": round(p95, 3), "maks_ms": round(s[-1], 3),
                     "ms": [round(x, 3) for x in ms]}}
    return _skriv("m08-ytelse-v1", art)


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
