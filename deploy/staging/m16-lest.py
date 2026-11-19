#!/usr/bin/env python3
"""M-16s sertifiseringslest — klasse D i ADR-002 (lesemodul i kjernen).

M-16 har ingen arbeider, ingen kø, ingen egen release og ingen
skrivevei. Den er `GET /v1/nokkeltall` og definerne bak (051/084/086).
Alle fire punktene måles derfor gjennom DET EKTE ENDEPUNKTET — appen
bygges med `api.app.lag_app`, og kallet går med et token preget i
basen, slik `test_api` gjør — i den ISOLERTE basen på 55432, bygget fra
commiten som måles.

  fasit           syntetisk_datasett_likt_lokalt + revisjonslogg_korrekt.
                  To riggtenanter med KJENT datasett (beslutninger i og
                  utenfor vinduet, åpne og lukkede saker med kjent
                  varighet). Svaret skal være nøyaktig fasiten, og den ene
                  tenanten skal aldri se den andres rader. MÅLT FRAVÆR:
                  kallene gir null nye revisjonsrader og ingen endrede
                  tabeller utover autentiseringens egen bokføring, og
                  hver m16-definer er ikke-VOLATILE (basen nekter en
                  STABLE funksjon å skrive).
  feilinjisering  EXECUTE trekkes fra runtime på den FØRSTE og den SISTE
                  defineren endepunktet kaller. Svaret skal være et typet
                  feilsvar, aldri 200 med delvise tall. ACL-en
                  gjenopprettes og måles lik.
  rollback        forgjengerens kjerne og kandidatens svarer på samme
                  datasett: like tall.
  ytelse          30 kall mot en tenant med volum, p95 under grensen.

Skriptet dømmer ingenting: `bestatt` er produsentens påstand, og
`manifestskjema._grenser_m16_*` validerer.

BRUK (på verten, som administrator, med riggens DSN-er eksportert):
  python deploy/staging/m16-lest.py fasit
  python deploy/staging/m16-lest.py feilinjisering
  python deploy/staging/m16-lest.py rollback --forgjenger /opt/disponit/releases/<sha>
  python deploy/staging/m16-lest.py ytelse
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
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (M16_MODULFILER, _sjekk_grenser,  # noqa: E402
                            kjerne_digest, m16_bevisrot_sha256,
                            release_digest, valider_artefaktformat)

UT = REPO / "deploy/staging/artefakter"
RUNTIME = "disponit"

#: Testverdiene `test_api.miljo` bruker. Riggbasen er en testbase.
MILJO = {
    "DISPONIT_TOKEN_PEPPER": "p" * 40,
    "DISPONIT_KEK": "b" * 64,
    "DISPONIT_ATT_NOKLER": json.dumps({"v_fordring": {"k1": "x" * 40}}),
    "DISPONIT_MAC_NOKLER": json.dumps(
        {"mk1": {"rolle": "signerer", "hemmelighet": "m" * 40}}),
    "DISPONIT_RATE_PER_MIN": "100000",
}

#: Feltene som ER svaret i fasitens forstand.
KJERNEFELT = ("beslutninger", "apne_naa", "unntak_lukkede_totalt",
              "unntak_lukketid")


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
        sys.exit(f"NEKTET: riggen skriver beslutninger og saker direkte og"
                 f" kjører bare i disponit_test på 55432 (fikk {db}:{port})")


def _oppsett(runde: str) -> dict:
    return {"modul": "m16_nokkeltall", "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": m16_bevisrot_sha256()}


# ---------------------------------------------------------------------------
# Datasettet — skrevet direkte som migrator, testsuitens form. M-16 har
# ingen dør; radene den teller skrives av andre moduler i drift.
# ---------------------------------------------------------------------------

def _kontekst(mg, tenant: str) -> None:
    """RLS på revisjonslogg og unntak krever tenantkonteksten — også for
    migratoren (testsuitens `_sett_kontekst`)."""
    mg.execute("SELECT set_config('disponit.tenant', %s, true),"
               " set_config('disponit.aktor', 'm16-fasit', true)",
               (tenant,))


def _logg(mg, tenant: str, beslutning: str, ts_sql: str = "now()") -> int:
    _kontekst(mg, tenant)
    return mg.execute(
        "INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
        " beslutning, begrunnelse, idempotency_key, kilde, ts) VALUES"
        f" (%s,'h','p',%s,'[]'::jsonb,%s,'arbeidskapabilitet', {ts_sql})"
        " RETURNING id",
        (tenant, beslutning, secrets.token_hex(8))).fetchone()[0]


def _sak(mg, tenant: str, status: str, ts_sql: str, status_ts_sql: str):
    _kontekst(mg, tenant)
    mg.execute("INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek)"
               " VALUES (%s,'k1','\\x00'::bytea) ON CONFLICT DO NOTHING",
               (tenant,))
    lid = _logg(mg, tenant, "UNNTAK", ts_sql)
    mg.execute(
        "INSERT INTO unntak (tenant, loggpost_id, handling, kategori,"
        " sakstype, prioritet, status, payload_kryptert, key_id, nonce,"
        " sakskilde, maks_auto_forsok_snapshot, policy_versjon,"
        " policy_content_hash, ts, status_ts) VALUES (%s,%s,'utbetaling',"
        "'over_grense','normal','hoy',%s,'\\x00'::bytea,'k1','\\x00'::bytea,"
        f"'policybrudd',3,'1.0.0',%s, {ts_sql}, {status_ts_sql})",
        (tenant, lid, status, "c" * 64))


#: tenant → (TILLAT, STOPP, åpne saker, lukkede saker, beslutninger
#: utenfor 24t-vinduet). Hver sak bærer sin egen UNNTAK-loggpost.
SETT = {"a": (5, 2, 3, 2, 1), "b": (4, 0, 1, 0, 0)}
#: Varigheten til en lukket sak: opprettet for 2 t siden, lukket for 1 t.
LUKKETID_S = 3600


def forventet(tkey: str) -> dict:
    tillat, stopp, apne, lukkede, _ute = SETT[tkey]
    unntak = apne + lukkede
    deler = {k: v for k, v in (("TILLAT", tillat), ("STOPP", stopp),
                                ("UNNTAK", unntak)) if v}
    return {"beslutninger": {"total": tillat + stopp + unntak,
                             "deler": deler},
            "apne_naa": apne,
            "unntak_lukkede_totalt": lukkede,
            "unntak_lukketid": {
                "sum_s": lukkede * LUKKETID_S, "antall": lukkede,
                "gjennomsnitt_s": LUKKETID_S if lukkede else None}}


def forbered(mg, runde: str) -> dict:
    rigg = {}
    for tkey, (tillat, stopp, apne, lukkede, ute) in SETT.items():
        tenant = f"t-m16fasit-{tkey}-{runde}"
        rigg[tkey] = tenant
        for _ in range(tillat):
            _logg(mg, tenant, "TILLAT", "now() - interval '1 hour'")
        for _ in range(stopp):
            _logg(mg, tenant, "STOPP", "now() - interval '1 hour'")
        for _ in range(ute):
            _logg(mg, tenant, "TILLAT", "now() - interval '3 days'")
        for _ in range(apne):
            _sak(mg, tenant, "manuell", "now() - interval '1 hour'",
                 "now() - interval '1 hour'")
        for _ in range(lukkede):
            _sak(mg, tenant, "løst", "now() - interval '2 hours'",
                 "now() - interval '1 hour'")
        mg.commit()
    return rigg


def _token(mg, tenant: str) -> str:
    token_id = "tk_" + secrets.token_hex(8)
    hemmelig = secrets.token_urlsafe(32)
    mac = hmac.new(MILJO["DISPONIT_TOKEN_PEPPER"].encode(),
                   hemmelig.encode(), hashlib.sha256).hexdigest()
    mg.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes,"
        " secret_mac, status) VALUES (%s,%s,'agent',%s,%s,'AKTIV')",
        (token_id, tenant, ["decisions:read", "exceptions:read"], mac))
    mg.commit()
    return f"{token_id}.{hemmelig}"


#: Ett kall, i en EGEN prosess med koden i `rot`. Samme vei for fasit,
#: feilinjisering, rollback og ytelse — og for forgjenger og kandidat.
KALLER = r"""
import json, os, sys, time
from api.app import lag_app
from starlette.testclient import TestClient
dsn, tokener, vinduer, n = sys.argv[1], json.loads(sys.argv[2]), \
    json.loads(sys.argv[3]), int(sys.argv[4])
app = lag_app(dsn)
ut = []
with TestClient(app) as k:
    for tenant, tok in tokener.items():
        for v in vinduer:
            for _ in range(n):
                t0 = time.monotonic()
                r = k.get(f"/v1/nokkeltall?vindu={v}",
                          headers={"authorization": f"Bearer {tok}"})
                ms = (time.monotonic() - t0) * 1000
                try:
                    body = r.json()
                except Exception:
                    body = {"_tekst": r.text[:200]}
                ut.append({"tenant": tenant, "vindu": v,
                           "status": r.status_code, "ms": ms, "body": body})
app.tjeneste.pool.lukk()
print(json.dumps(ut))
"""


def _kall(rot: Path, tokener: dict, vinduer=("24t",), n: int = 1) -> list:
    env = {**os.environ, **MILJO,
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}"}
    for k in ("DISPONIT_MILJO", "DISPONIT_TLS_AKTIV",
              "CREDENTIALS_DIRECTORY"):
        env.pop(k, None)
    p = subprocess.run(
        [sys.executable, "-c", KALLER, _env("DISPONIT_TEST_DSN"),
         json.dumps(tokener), json.dumps(list(vinduer)), str(n)],
        cwd=rot / "platform", env=env, capture_output=True, text=True,
        timeout=900)
    if p.returncode != 0:
        sys.exit(f"kalleren feilet:\n{p.stderr[-2000:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def _kjerne(body: dict) -> dict:
    return {k: body.get(k) for k in KJERNEFELT}


def _radbilde(sup) -> dict[str, str]:
    tabeller = [r[0] for r in sup.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n"
        " ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
        " AND c.relkind IN ('r','p') ORDER BY 1").fetchall()]
    bilde = {}
    for t in tabeller:
        n, h = sup.execute(
            f'SELECT count(*), coalesce(sum(hashtext(x::text)::bigint), 0)'
            f' FROM public."{t}" x').fetchone()
        bilde[t] = f"{n}:{h}"
    sup.rollback()
    return bilde


def _volatile_definere(sup) -> list[str]:
    """D1 (b): en m16-definer som er VOLATILE kunne skrevet. STABLE og
    IMMUTABLE nektes skriving av basen selv."""
    rader = sup.execute(
        "SELECT p.oid::regprocedure::text FROM pg_proc p"
        " JOIN pg_namespace n ON n.oid = p.pronamespace"
        " WHERE n.nspname = 'public' AND p.proname LIKE 'm16\\_%'"
        " AND p.provolatile = 'v' ORDER BY 1").fetchall()
    antall = sup.execute(
        "SELECT count(*) FROM pg_proc p JOIN pg_namespace n"
        " ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
        " AND p.proname LIKE 'm16\\_%'").fetchone()[0]
    sup.rollback()
    return [r[0] for r in rader], antall


# ---------------------------------------------------------------------------
# Punktene.
# ---------------------------------------------------------------------------

def fasit() -> Path:
    sup = psycopg.connect(_env("M16_SUPER_DSN"))
    _er_isolert(sup)
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(mg, runde)
    tokener = {rigg[t]: _token(mg, rigg[t]) for t in SETT}
    _log(f"rigget runde {runde}: {rigg}")

    rev_for = sup.execute("SELECT count(*) FROM revisjonslogg").fetchone()[0]
    sup.rollback()
    bilde_for = _radbilde(sup)
    svar = _kall(REPO, tokener, ("24t", "7d"))
    bilde_etter = _radbilde(sup)
    rev_etter = sup.execute(
        "SELECT count(*) FROM revisjonslogg").fetchone()[0]
    sup.rollback()
    endret = sorted(t for t in set(bilde_for) | set(bilde_etter)
                    if bilde_for.get(t) != bilde_etter.get(t))

    per, avvik = [], 0
    for tkey in SETT:
        tenant = rigg[tkey]
        vent = forventet(tkey)
        for s in svar:
            if s["tenant"] != tenant:
                continue
            fikk = _kjerne(s["body"]) if s["status"] == 200 else None
            if s["vindu"] == "24t":
                ok = fikk == vent
            else:
                # 7d: samme tall, pluss beslutningene utenfor 24t.
                ute = SETT[tkey][4]
                v7 = json.loads(json.dumps(vent))
                v7["beslutninger"]["total"] += ute
                if ute:
                    v7["beslutninger"]["deler"]["TILLAT"] = \
                        v7["beslutninger"]["deler"].get("TILLAT", 0) + ute
                ok = fikk == v7
                vent = v7
            avvik += 0 if ok else 1
            per.append({"tenant": tkey, "vindu": s["vindu"],
                        "status": s["status"], "ventet": vent, "fikk": fikk})
    volatile, antall_definere = _volatile_definere(sup)
    _log(f"{len(per)} svar, avvik {avvik}; revisjonslogg {rev_for} →"
         f" {rev_etter}; endret: {endret or 'ingen'}; volatile: {volatile}")

    art = {
        "oppsett": {**_oppsett(runde), "tenanter": rigg},
        "maalt": {
            "svar": len(per),
            "per_svar": per,
            "funnavvik": avvik,
            "tenanter": len(SETT),
            "nye_revisjonsrader": rev_etter - rev_for,
            "fremmede_tabeller_sett": len(bilde_for),
            "endrede_tabeller": endret,
            "m16_definere": antall_definere,
            "volatile_definere": volatile,
        },
    }
    return _skriv("m16-fasit-v1", art)


def feilinjisering() -> Path:
    sup = psycopg.connect(_env("M16_SUPER_DSN"))
    _er_isolert(sup)
    sup.autocommit = True
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(mg, runde)
    tenant = rigg["a"]
    tokener = {tenant: _token(mg, tenant)}

    def acl(fn: str) -> list:
        return sorted(sup.execute(
            "SELECT a.grantee::regrole::text, a.privilege_type"
            " FROM pg_proc p, LATERAL aclexplode(p.proacl) a"
            " WHERE p.oid = %s::regprocedure", (fn,)).fetchall())

    def signatur(navn: str) -> str:
        return sup.execute(
            "SELECT p.oid::regprocedure::text FROM pg_proc p"
            " JOIN pg_namespace n ON n.oid = p.pronamespace"
            " WHERE n.nspname = 'public' AND p.proname = %s",
            (navn,)).fetchone()[0]

    injeksjoner = []
    for navn in ("m16_beslutninger", "m16_tick_alltid"):
        fn = signatur(navn)
        for_acl = acl(fn)
        sup.execute(f"REVOKE EXECUTE ON FUNCTION {fn} FROM PUBLIC, {RUNTIME}")
        har = sup.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                          (RUNTIME, fn)).fetchone()[0]
        try:
            s = _kall(REPO, tokener)[0]
        finally:
            for grantee, priv in for_acl:
                mottaker = "PUBLIC" if grantee in ("-", "0") else grantee
                sup.execute(f"GRANT {priv} ON FUNCTION {fn} TO {mottaker}")
        etter_acl = acl(fn)
        frisk = _kall(REPO, tokener)[0]
        injeksjoner.append({
            "definer": fn,
            "runtime_har_execute": bool(har),
            "status": s["status"],
            "feilkode": (s["body"].get("feil") or s["body"].get("kode")
                         or s["body"].get("error")),
            "delvise_tall": any(k in s["body"] for k in KJERNEFELT),
            "acl_gjenopprettet": for_acl == etter_acl,
            "frisk_status": frisk["status"],
            "frisk_lik_fasit": _kjerne(frisk["body"]) == forventet("a"),
        })
        _log(f"{fn}: {injeksjoner[-1]}")

    art = {
        "oppsett": {**_oppsett(runde),
                    "injeksjonsrolle": RUNTIME,
                    "injeksjon": "EXECUTE trukket fra runtime (og PUBLIC)"
                                 " på første og siste definer"},
        "maalt": {"injeksjoner": injeksjoner,
                  "injiserte": len(injeksjoner)},
    }
    return _skriv("m16-feilinjisering-v1", art)


def rollback(forgjenger: Path) -> Path:
    sup = psycopg.connect(_env("M16_SUPER_DSN"))
    _er_isolert(sup)
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(mg, runde)
    tokener = {rigg[t]: _token(mg, rigg[t]) for t in SETT}
    rb = _kall(forgjenger, tokener)
    kd = _kall(REPO, tokener)

    def dom(svar):
        return {s["tenant"]: (s["status"], _kjerne(s["body"]))
                for s in svar}
    d_rb, d_kd = dom(rb), dom(kd)
    fasit_ok = all(d_kd.get(rigg[t]) == (200, forventet(t)) for t in SETT)
    _log(f"rullbakk={d_rb}\nkandidat={d_kd}")
    art = {
        "oppsett": {**_oppsett(runde), "forgjenger": str(forgjenger),
                    "kjerne_forgjenger": kjerne_digest(forgjenger),
                    "kjerne_kandidat": kjerne_digest(REPO),
                    "modul_forgjenger": release_digest(forgjenger,
                                                       M16_MODULFILER),
                    "modul_kandidat": release_digest(REPO, M16_MODULFILER)},
        "maalt": {
            "rullbakk_status": sorted({s["status"] for s in rb}),
            "kandidat_status": sorted({s["status"] for s in kd}),
            "svar_like": d_rb == d_kd,
            "kandidat_lik_fasit": fasit_ok,
            "tenanter": len(SETT),
        },
    }
    return _skriv("m16-rollback-v1", art)


def ytelse(kall: int, beslutninger: int, saker: int) -> Path:
    sup = psycopg.connect(_env("M16_SUPER_DSN"))
    _er_isolert(sup)
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    tenant = f"t-m16fasit-ytelse-{runde}"
    _kontekst(mg, tenant)
    mg.execute(
        "INSERT INTO revisjonslogg (tenant, input_hash, policy_id,"
        " beslutning, begrunnelse, idempotency_key, kilde, ts)"
        " SELECT %s, 'h', 'p', (ARRAY['TILLAT','STOPP'])[1 + g %% 2],"
        " '[]'::jsonb, md5(random()::text || g), 'arbeidskapabilitet',"
        " now() - (g %% 720) * interval '1 hour'"
        " FROM generate_series(1, %s) g", (tenant, beslutninger))
    for i in range(saker):
        _sak(mg, tenant, "løst" if i % 2 else "manuell",
             "now() - interval '2 hours'", "now() - interval '1 hour'")
    mg.commit()
    tokener = {tenant: _token(mg, tenant)}
    svar = _kall(REPO, tokener, ("30d",), kall)
    ms = sorted(s["ms"] for s in svar)
    p95 = ms[max(0, int(round(0.95 * len(ms))) - 1)]
    total = sorted({s["body"].get("beslutninger", {}).get("total")
                    for s in svar})
    _log(f"{len(svar)} kall: p50={ms[len(ms) // 2]:.1f} ms"
         f" p95={p95:.1f} ms maks={ms[-1]:.1f} ms; totaler {total}")
    art = {
        "oppsett": {**_oppsett(runde), "tenant": tenant,
                    "beslutninger": beslutninger, "saker": saker,
                    "vindu": "30d"},
        "maalt": {
            "kall": len(svar),
            "feilede": sum(1 for s in svar if s["status"] != 200),
            "p50_ms": round(ms[len(ms) // 2], 2),
            "p95_ms": round(p95, 2),
            "maks_ms": round(ms[-1], 2),
            "totaler": total,
            "ms": [round(x, 2) for x in ms],
        },
    }
    return _skriv("m16-ytelse-v1", art)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("punkt", choices=("fasit", "feilinjisering", "rollback",
                                      "ytelse"))
    ap.add_argument("--forgjenger", type=Path)
    ap.add_argument("--kall", type=int, default=30)
    ap.add_argument("--beslutninger", type=int, default=50_000)
    ap.add_argument("--saker", type=int, default=500)
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
        sti = ytelse(a.kall, a.beslutninger, a.saker)
    return 0 if json.loads(sti.read_text())["bestatt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
