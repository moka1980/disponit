#!/usr/bin/env python3
"""M-35s sertifiseringslest — klasse C i ADR-002 (tenantregister uten sveip).

M-35 har ingen sveip og ingen funn. Den har sju dører som endrer
tenanttilstand (opprett og oppdater tjeneste, opprett og bekreft kontakt,
opprett hendelse, legg post, lukk hendelse) og én lesevei
(`GET /v1/kontinuitet`). Alle fire punktene måles i den ISOLERTE basen på
55432, bygget fra commiten som måles — evidenstriggerne i 219 er med.

  fasit           syntetisk_datasett_likt_lokalt + revisjonslogg_korrekt.
                  En KJENT DØRSEKVENS i to tenanter, kalt som runtime med
                  tenantkontekst. Sluttilstanden RE-REGNES av radene, og
                  gjenspill av hver skrivende dør skal være et stille ja.
                  Evidensen telles per handling, med aktør og egen
                  input_hash.
  feilinjisering  seks kall som bryter en vakt: typet SQLSTATE, uten én
                  endret rad i modulens fire tabeller og uten ny hendelse.
  rollback        forgjengerens og kandidatens kjerne svarer på samme
                  register gjennom det ekte endepunktet: like svar.
  ytelse          leseveien (`svar_for`) under last, p95 mot en pinnet
                  grense.

BRUK (på verten, som administrator, med riggens DSN-er eksportert):
  python deploy/staging/m35-lest.py fasit|feilinjisering|ytelse
  python deploy/staging/m35-lest.py rollback --forgjenger /opt/disponit/releases/<sha>
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

import psycopg  # noqa: E402

from manifestskjema import (M35_MODULFILER, _sjekk_grenser,  # noqa: E402
                            kjerne_digest, m35_bevisrot_sha256,
                            release_digest, valider_artefaktformat)

UT = REPO / "deploy/staging/artefakter"
AKTOR = "m35-fasit"
EVIDENSKILDE = "m35_kontinuitet"
TABELLER = ("kontinuitet_tjeneste", "beredskapskontakt",
            "kontinuitetshendelse", "kontinuitetshendelse_post")

MILJO = {
    "DISPONIT_TOKEN_PEPPER": "p" * 40,
    "DISPONIT_KEK": "b" * 64,
    "DISPONIT_ATT_NOKLER": json.dumps({"v_fordring": {"k1": "x" * 40}}),
    "DISPONIT_MAC_NOKLER": json.dumps(
        {"mk1": {"rolle": "signerer", "hemmelighet": "m" * 40}}),
    "DISPONIT_RATE_PER_MIN": "100000",
}

PLAYBOOK = "gjenoppretting-api@" + "a" * 64
PLAYBOOK2 = "gjenoppretting-api@" + "b" * 64

#: Dørsekvensen per tenant. (handling, nøkkel)
#: A: T1, T2, oppdater T1; K1 bekreftes; H1 får observasjon + etteranalyse
#:    og lukkes; H2 får et tiltak og står åpen.
#: B: T3, K2 (ubekreftet), H3 (åpen).
SEKVENS = {
    "a": [("tjeneste", "T1"), ("tjeneste", "T2"), ("oppdater", "T1"),
          ("kontakt", "K1"), ("bekreft", "K1"),
          ("hendelse", "H1"), ("post_observasjon", "H1"),
          ("post_etteranalyse", "H1"), ("lukk", "H1"),
          ("hendelse", "H2"), ("post_tiltak", "H2")],
    "b": [("tjeneste", "T3"), ("kontakt", "K2"), ("hendelse", "H3")],
}
#: Forventet sluttilstand, re-regnet av radene.
FORVENTET = {
    "a": {"tjenester": 2, "kontakter": 1, "bekreftet": 1, "hendelser": 2,
          "lukket": 1, "poster": {"opprettet": 2, "observasjon": 1,
                                  "etteranalyse": 1, "lukket": 1,
                                  "tiltak": 1}},
    "b": {"tjenester": 1, "kontakter": 1, "bekreftet": 0, "hendelser": 1,
          "lukket": 0, "poster": {"opprettet": 1}},
}
#: Forventet evidens per handling og tenant.
FORVENTET_EVIDENS = {
    "a": {"tjeneste.opprettet": 2, "tjeneste.oppdatert": 1,
          "kontakt.opprettet": 1, "kontakt.bekreftet": 1,
          "hendelse.opprettet": 2, "hendelse.observasjon": 1,
          "hendelse.etteranalyse": 1, "hendelse.lukket": 1,
          "hendelse.tiltak": 1},
    "b": {"tjeneste.opprettet": 1, "kontakt.opprettet": 1,
          "hendelse.opprettet": 1},
}
#: Hvilke dører som er SKRIVENDE og kan gjenspilles som et stille ja.
GJENSPILLBARE = ("tjeneste", "kontakt", "hendelse", "post_observasjon",
                 "post_etteranalyse", "post_tiltak", "lukk")


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
    return {"modul": "m35_kontinuitet", "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": m35_bevisrot_sha256()}


def _kontekst(c, tenant: str) -> None:
    c.execute("SELECT set_config('disponit.tenant', %s, true),"
              " set_config('disponit.aktor', %s, true),"
              " set_config('disponit.request_id', 'm35-lest', true)",
              (tenant, AKTOR))


def _bruker(mg, tenant: str, merke: str) -> str:
    bid = mg.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m35.fasit', %s) RETURNING bruker_id",
        (f"{tenant}:{merke}",)).fetchone()[0]
    mg.commit()
    return bid


def _kall(rt, tenant: str, handling: str, n: str, ider: dict) -> None:
    """Ett dørkall som runtime — samme vei som API-et går."""
    _kontekst(rt, tenant)
    i = ider[n]
    if handling == "tjeneste":
        rt.execute("SELECT m35_opprett_tjeneste(%s,'systemd_unit',%s,"
                   "'kritisk',3600,900,%s,'drift',%s,%s)",
                   (tenant, f"disponit-{n.lower()}.service", PLAYBOOK,
                    AKTOR, i))
    elif handling == "oppdater":
        rt.execute("SELECT m35_oppdater_tjeneste(%s,%s,'viktig',7200,1800,"
                   "%s,'drift',%s)", (tenant, i, PLAYBOOK2, AKTOR))
    elif handling == "kontakt":
        rt.execute("SELECT m35_opprett_kontakt(%s,'drift',1::smallint,%s,"
                   "%s,%s)", (tenant, ider["bruker"], AKTOR, i))
    elif handling == "bekreft":
        rt.execute("SELECT m35_bekreft_kontakt(%s,%s,%s)", (tenant, i, AKTOR))
    elif handling == "hendelse":
        rt.execute("SELECT m35_opprett_hendelse(%s,'kontinuitet.fasit',"
                   "'{}'::jsonb,'alvorlig',%s,%s)", (tenant, AKTOR, i))
    elif handling.startswith("post_"):
        typ = handling[len("post_"):]
        rt.execute("SELECT m35_legg_post(%s,%s,%s,%s,%s,%s)",
                   (tenant, i, typ, f"Fasitpost {typ}", AKTOR,
                    ider[f"{n}:{typ}"]))
    elif handling == "lukk":
        rt.execute("SELECT m35_lukk_hendelse(%s,%s,%s,%s)",
                   (tenant, i, AKTOR, "Gjenopprettet og analysert"))
    else:
        raise ValueError(handling)
    rt.commit()


def forbered(rt, mg, runde: str) -> dict:
    rigg = {"tenanter": {}, "ider": {}}
    for tkey, sekvens in SEKVENS.items():
        tenant = f"t-m35fasit-{tkey}-{runde}"
        rigg["tenanter"][tkey] = tenant
        ider = {n: str(uuid.uuid4()) for _h, n in sekvens}
        for h, n in sekvens:
            if h.startswith("post_"):
                ider[f"{n}:{h[len('post_'):]}"] = str(uuid.uuid4())
        ider["bruker"] = _bruker(mg, tenant, "kontakt")
        rigg["ider"][tkey] = ider
        for handling, n in sekvens:
            _kall(rt, tenant, handling, n, ider)
    return rigg


def _tilstand(sup, tenant: str) -> dict:
    def en(sql):
        return sup.execute(sql, (tenant,)).fetchone()[0]
    ut = {
        "tjenester": en("SELECT count(*) FROM kontinuitet_tjeneste"
                        " WHERE tenant=%s"),
        "kontakter": en("SELECT count(*) FROM beredskapskontakt"
                        " WHERE tenant=%s"),
        "bekreftet": en("SELECT count(*) FROM beredskapskontakt"
                        " WHERE tenant=%s AND bekreftet_ts IS NOT NULL"),
        "hendelser": en("SELECT count(*) FROM kontinuitetshendelse"
                        " WHERE tenant=%s"),
        "lukket": en("SELECT count(*) FROM kontinuitetshendelse"
                     " WHERE tenant=%s AND lukket_ts IS NOT NULL"),
        "poster": dict(sup.execute(
            "SELECT posttype, count(*) FROM kontinuitetshendelse_post"
            " WHERE tenant=%s GROUP BY 1", (tenant,)).fetchall()),
    }
    sup.rollback()
    return ut


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
                              if not a or a != AKTOR),
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


def _les(rt, tenant: str) -> dict:
    from api.kontinuitet import svar_for
    _kontekst(rt, tenant)
    svar = svar_for(rt, tenant)
    rt.rollback()
    return svar


# ---------------------------------------------------------------------------

def fasit() -> Path:
    sup = psycopg.connect(_env("M35_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(rt, mg, runde)
    _log(f"rigget runde {runde}: {rigg['tenanter']}")

    per, avvik = [], 0
    for tkey, tenant in rigg["tenanter"].items():
        fikk = _tilstand(sup, tenant)
        ok = fikk == FORVENTET[tkey]
        avvik += 0 if ok else 1
        per.append({"tenant": tkey, "ventet": FORVENTET[tkey],
                    "fikk": fikk})
    svar = _les(rt, rigg["tenanter"]["a"])
    lesing_lik = (len(svar.get("tjenester", [])) == 2
                  and len(svar.get("kontakter", [])) == 1
                  and len(svar.get("hendelser", [])) == 2)

    # GJENSPILL: hver skrivende dør med samme id og samme innhold skal være
    # et stille ja — ingen nye rader, ingen nye hendelser.
    for_gjenspill = _digest(sup)
    gjenspill_feil = 0
    for tkey, sekvens in SEKVENS.items():
        tenant = rigg["tenanter"][tkey]
        for handling, n in sekvens:
            if handling not in GJENSPILLBARE:
                continue
            if handling.startswith("post_") and tkey == "a" and n == "H1":
                continue          # H1 er lukket: en post der er et brudd
            if handling == "tjeneste" and tkey == "a" and n == "T1":
                continue          # T1 er oppdatert: opprettelsen med det
                                  # gamle innholdet er en materiell konflikt
            try:
                _kall(rt, tenant, handling, n, rigg["ider"][tkey])
            except psycopg.Error:
                rt.rollback()
                gjenspill_feil += 1
    etter_gjenspill = _digest(sup)

    ev = {tkey: _evidens(sup, t) for tkey, t in rigg["tenanter"].items()}
    ev_avvik = sum(1 for tkey in SEKVENS
                   if ev[tkey]["per_handling"] != FORVENTET_EVIDENS[tkey])
    _log(f"tilstand avvik {avvik}; lesing lik {lesing_lik}; gjenspill"
         f" urørt={for_gjenspill == etter_gjenspill}; evidens {ev}")
    art = {
        "oppsett": {**_oppsett(runde), "tenanter": rigg["tenanter"]},
        "maalt": {
            "per_tenant": per,
            "tilstandsavvik": avvik,
            "tenanter": len(per),
            "dorkall": sum(len(s) for s in SEKVENS.values()),
            "utfylling_lik_fasit": lesing_lik,
            "gjenspill_urort": for_gjenspill == etter_gjenspill,
            "gjenspill_feilet": gjenspill_feil,
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
    return _skriv("m35-fasit-v1", art)


def feilinjisering() -> Path:
    sup = psycopg.connect(_env("M35_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(rt, mg, runde)
    ta, ia = rigg["tenanter"]["a"], rigg["ider"]["a"]

    def lukk_uten_etteranalyse(c):
        _kontekst(c, ta)
        c.execute("SELECT m35_lukk_hendelse(%s,%s,%s,%s)",
                  (ta, ia["H2"], AKTOR, "Forsøk uten etterlæring"))

    def post_paa_lukket(c):
        _kontekst(c, ta)
        c.execute("SELECT m35_legg_post(%s,%s,'observasjon',%s,%s,%s)",
                  (ta, ia["H1"], "Etter lukking", AKTOR, str(uuid.uuid4())))

    def materiell_konflikt(c):
        _kontekst(c, ta)
        c.execute("SELECT m35_opprett_tjeneste(%s,'systemd_unit',%s,"
                  "'normal',3600,900,%s,'drift',%s,%s)",
                  (ta, "disponit-t1.service", PLAYBOOK, AKTOR, ia["T1"]))

    def uten_kontekst(c):
        c.execute("SELECT m35_opprett_hendelse(%s,'kontinuitet.fasit',"
                  "'{}'::jsonb,'alvorlig',%s,%s)",
                  (ta, AKTOR, str(uuid.uuid4())))

    def dorens_egen_posttype(c):
        _kontekst(c, ta)
        c.execute("SELECT m35_legg_post(%s,%s,'lukket',%s,%s,%s)",
                  (ta, ia["H2"], "Falsk lukking", AKTOR, str(uuid.uuid4())))

    def endre_tidslinjen(c):
        # Som eieren av dørene, rett i tabellen: append-only-vakten skal
        # nekte også ham.
        _kontekst(c, ta)
        c.execute("SET LOCAL ROLE disponit_m37_claimer")
        c.execute("UPDATE kontinuitetshendelse_post SET tekst = 'X'"
                  " WHERE tenant=%s AND hendelse_id=%s", (ta, ia["H1"]))

    brudd = [("lukk_uten_etteranalyse", rt, lukk_uten_etteranalyse),
             ("post_paa_lukket_hendelse", rt, post_paa_lukket),
             ("materiell_idempotenskonflikt", rt, materiell_konflikt),
             ("dor_uten_tenantkontekst", rt, uten_kontekst),
             ("dorens_egen_posttype", rt, dorens_egen_posttype),
             ("endre_tidslinjen", mg, endre_tidslinjen)]
    ut = []
    for navn, conn, fn in brudd:
        for_ = _digest(sup)
        sqlstate, avvist = None, False
        try:
            fn(conn)
            conn.commit()
        except psycopg.Error as e:
            conn.rollback()
            avvist, sqlstate = True, e.sqlstate
        etter = _digest(sup)
        ut.append({"brudd": navn, "avvist": avvist, "sqlstate": sqlstate,
                   "urort": for_ == etter})
        _log(f"{navn}: {ut[-1]}")
    art = {"oppsett": _oppsett(runde),
           "maalt": {"brudd": ut, "injiserte": len(ut)}}
    return _skriv("m35-feilinjisering-v1", art)


def _token(mg, tenant: str) -> str:
    token_id = "tk_" + secrets.token_hex(8)
    hemmelig = secrets.token_urlsafe(32)
    mac = hmac.new(MILJO["DISPONIT_TOKEN_PEPPER"].encode(),
                   hemmelig.encode(), hashlib.sha256).hexdigest()
    mg.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes,"
        " secret_mac, status) VALUES (%s,%s,'agent',%s,%s,'AKTIV')",
        (token_id, tenant, ["kontinuitet:read"], mac))
    mg.commit()
    return f"{token_id}.{hemmelig}"


KALLER = r"""
import json, sys
from api.app import lag_app
from starlette.testclient import TestClient
dsn, tok = sys.argv[1], sys.argv[2]
app = lag_app(dsn)
with TestClient(app) as k:
    r = k.get("/v1/kontinuitet", headers={"authorization": f"Bearer {tok}"})
    body = r.json()
app.tjeneste.pool.lukk()
body.pop("request_id", None)
print(json.dumps({"status": r.status_code, "body": body}, sort_keys=True))
"""


def _http(rot: Path, tok: str) -> dict:
    env = {**os.environ, **MILJO,
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}"}
    for k in ("DISPONIT_MILJO", "DISPONIT_TLS_AKTIV",
              "CREDENTIALS_DIRECTORY"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", KALLER,
                        _env("DISPONIT_TEST_DSN"), tok],
                       cwd=rot / "platform", env=env, capture_output=True,
                       text=True, timeout=300)
    if p.returncode != 0:
        sys.exit(f"kalleren feilet:\n{p.stderr[-2000:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def rollback(forgjenger: Path) -> Path:
    sup = psycopg.connect(_env("M35_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(rt, mg, runde)
    tok = _token(mg, rigg["tenanter"]["a"])
    rb, kd = _http(forgjenger, tok), _http(REPO, tok)
    kd_tekst = json.dumps(kd["body"], ensure_ascii=False)
    art = {
        "oppsett": {**_oppsett(runde), "forgjenger": str(forgjenger),
                    "kjerne_forgjenger": kjerne_digest(forgjenger),
                    "kjerne_kandidat": kjerne_digest(REPO),
                    "modul_forgjenger": release_digest(forgjenger,
                                                       M35_MODULFILER),
                    "modul_kandidat": release_digest(REPO, M35_MODULFILER)},
        "maalt": {
            "rullbakk_status": rb["status"],
            "kandidat_status": kd["status"],
            "svar_like": rb == kd,
            # Positiv kontroll: svaret ER registeret riggen fylte.
            "kandidat_baerer_riggen": all(
                rigg["ider"]["a"][v] in kd_tekst for v in ("T1", "T2", "H1",
                                                           "H2")),
        },
    }
    _log(f"rullbakk {rb['status']}, kandidat {kd['status']},"
         f" like={rb == kd}")
    return _skriv("m35-rollback-v1", art)


def ytelse(kall: int) -> Path:
    sup = psycopg.connect(_env("M35_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(rt, mg, runde)
    ta = rigg["tenanter"]["a"]
    ms, feil, riktige = [], 0, 0
    for _ in range(kall):
        t0 = time.monotonic()
        try:
            svar = _les(rt, ta)
            riktige += 1 if (len(svar["tjenester"]) == 2
                             and len(svar["hendelser"]) == 2) else 0
        except psycopg.Error:
            rt.rollback()
            feil += 1
        ms.append((time.monotonic() - t0) * 1000)
    s = sorted(ms)
    p95 = s[max(0, int(round(0.95 * len(s))) - 1)]
    _log(f"{kall} lesinger: p50={s[len(s) // 2]:.2f} ms p95={p95:.2f} ms")
    art = {"oppsett": _oppsett(runde),
           "maalt": {"kall": kall, "feilede": feil, "riktige": riktige,
                     "p50_ms": round(s[len(s) // 2], 3),
                     "p95_ms": round(p95, 3), "maks_ms": round(s[-1], 3),
                     "ms": [round(x, 3) for x in ms]}}
    return _skriv("m35-ytelse-v1", art)


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
