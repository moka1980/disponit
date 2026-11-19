#!/usr/bin/env python3
"""M-05s sertifiseringslest — klasse C i ADR-002 (tenantregister uten sveip).

M-05 har ingen sveip og ingen funn. Den har fem dører som endrer
tenanttilstand (opprett familie, opprett versjon, publiser, trekk
tilbake, forkast) og én lesedør (utfylling). Alle fire punktene måles i
den ISOLERTE basen på 55432, bygget fra commiten som måles — så
evidenstriggerne i 213 er med uten å vente på en deploy.

  fasit           syntetisk_datasett_likt_lokalt + revisjonslogg_korrekt.
                  En KJENT DØRSEKVENS i to tenanter, kalt som runtime med
                  tenantkontekst. Sluttilstanden RE-REGNES av radene
                  (familier, versjoner per status, komponenter, felt), og
                  utfyllingen av den publiserte malen gir kjent tekst.
                  Gjenspill av hver skrivende dør skal være et stille ja:
                  ingen nye rader, ingen nye hendelser. Evidensen telles
                  per handling, med aktør og egen input_hash.
  feilinjisering  fem kall som bryter en vakt. Hvert skal avvises med en
                  typet SQLSTATE, uten én endret rad i modulens fire
                  tabeller og uten én ny hendelse.
  rollback        forgjengerens og kandidatens kjerne svarer på samme
                  register gjennom det ekte endepunktet (`GET
                  /v1/dokumentmal` og utfyllingen): like svar.
  ytelse          utfyllingsdøra under last, p95 mot en pinnet grense.

BRUK (på verten, som administrator, med riggens DSN-er eksportert):
  python deploy/staging/m05-lest.py fasit|feilinjisering|ytelse
  python deploy/staging/m05-lest.py rollback --forgjenger /opt/disponit/releases/<sha>
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

from manifestskjema import (M05_MODULFILER, _sjekk_grenser,  # noqa: E402
                            kjerne_digest, m05_bevisrot_sha256,
                            release_digest, valider_artefaktformat)

UT = REPO / "deploy/staging/artefakter"
AKTOR = "m05-fasit"
EVIDENSKILDE = "m05_dokumentmal"
TABELLER = ("malfamilie", "malversjon", "malkomponent", "malfelt")

MILJO = {
    "DISPONIT_TOKEN_PEPPER": "p" * 40,
    "DISPONIT_KEK": "b" * 64,
    "DISPONIT_ATT_NOKLER": json.dumps({"v_fordring": {"k1": "x" * 40}}),
    "DISPONIT_MAC_NOKLER": json.dumps(
        {"mk1": {"rolle": "signerer", "hemmelighet": "m" * 40}}),
    "DISPONIT_RATE_PER_MIN": "100000",
}

KOMPONENTER = [
    {"komponenttype": "tekst", "innhold": "Avtale mellom "},
    {"komponenttype": "felt", "feltnokkel": "part_a"},
    {"komponenttype": "tekst", "innhold": " og "},
    {"komponenttype": "felt", "feltnokkel": "part_b"},
    {"komponenttype": "klausul", "innhold": " Oppsigelsestid tre maaneder.",
     "laast": True},
]
FELT = [
    {"feltnokkel": "part_a", "paakrevd": True, "felttype": "tekst",
     "beskrivelse": "Første part"},
    {"feltnokkel": "part_b", "paakrevd": True, "felttype": "tekst",
     "beskrivelse": "Andre part"},
]
VERDIER = {"part_a": "Fjordlys AS", "part_b": "Kari Nordmann"}
UTFYLT = "Avtale mellom Fjordlys AS og Kari Nordmann Oppsigelsestid tre maaneder."

#: Dørsekvensen per tenant. (handling, familie, versjon)
#: A: F1 → V1 publiseres og trekkes tilbake; V2 forkastes; F2 → V3 publiseres.
#: B: F3 → V4 står som utkast.
SEKVENS = {
    "a": [("familie", "F1", None), ("versjon", "F1", "V1"),
          ("publiser", None, "V1"), ("trekk", None, "V1"),
          ("versjon", "F1", "V2"), ("forkast", None, "V2"),
          ("familie", "F2", None), ("versjon", "F2", "V3"),
          ("publiser", None, "V3")],
    "b": [("familie", "F3", None), ("versjon", "F3", "V4")],
}
#: Forventet sluttilstand, re-regnet av radene.
FORVENTET = {
    "a": {"familier": 2, "versjoner": {"tilbaketrukket": 1, "forkastet": 1,
                                       "publisert": 1},
          "komponenter": 15, "felt": 6},
    "b": {"familier": 1, "versjoner": {"utkast": 1},
          "komponenter": 5, "felt": 2},
}
#: Forventet evidens per handling og tenant.
FORVENTET_EVIDENS = {
    "a": {"malfamilie.opprettet": 2, "malversjon.opprettet": 3,
          "malversjon.publisert": 2, "malversjon.tilbaketrukket": 1,
          "malversjon.forkastet": 1},
    "b": {"malfamilie.opprettet": 1, "malversjon.opprettet": 1},
}


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
    return {"modul": "m05_dokumentmal", "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": m05_bevisrot_sha256()}


def _kontekst(c, tenant: str) -> None:
    c.execute("SELECT set_config('disponit.tenant', %s, true),"
              " set_config('disponit.aktor', %s, true),"
              " set_config('disponit.request_id', 'm05-lest', true)",
              (tenant, AKTOR))


def _kall(rt, tenant: str, handling: str, fam, ver, ider: dict) -> None:
    """Ett dørkall som runtime — samme vei som API-et går."""
    _kontekst(rt, tenant)
    if handling == "familie":
        rt.execute("SELECT m5_opprett_malfamilie(%s,%s,%s,%s,%s)",
                   (tenant, f"Fasitmal {fam}", None, AKTOR, ider[fam]))
    elif handling == "versjon":
        rt.execute("SELECT * FROM m5_opprett_malversjon(%s,%s,%s::jsonb,"
                   "%s::jsonb,%s,%s)",
                   (tenant, ider[fam], json.dumps(KOMPONENTER),
                    json.dumps(FELT), AKTOR, ider[ver]))
    else:
        dor = {"publiser": "m5_publiser_malversjon",
               "trekk": "m5_trekk_tilbake_malversjon",
               "forkast": "m5_forkast_malversjon"}[handling]
        rt.execute(f"SELECT {dor}(%s,%s,%s)", (tenant, ider[ver], AKTOR))
    rt.commit()


def forbered(rt, runde: str) -> dict:
    rigg = {"tenanter": {}, "ider": {}}
    for tkey, sekvens in SEKVENS.items():
        tenant = f"t-m05fasit-{tkey}-{runde}"
        rigg["tenanter"][tkey] = tenant
        ider = {n: str(uuid.uuid4()) for n in
                {x for _h, f, v in sekvens for x in (f, v) if x}}
        rigg["ider"][tkey] = ider
        for handling, fam, ver in sekvens:
            _kall(rt, tenant, handling, fam, ver, ider)
    return rigg


def _tilstand(sup, tenant: str) -> dict:
    fam = sup.execute("SELECT count(*) FROM malfamilie WHERE tenant=%s",
                      (tenant,)).fetchone()[0]
    ver = dict(sup.execute(
        "SELECT status, count(*) FROM malversjon WHERE tenant=%s"
        " GROUP BY 1", (tenant,)).fetchall())
    komp = sup.execute("SELECT count(*) FROM malkomponent WHERE tenant=%s",
                       (tenant,)).fetchone()[0]
    felt = sup.execute("SELECT count(*) FROM malfelt WHERE tenant=%s",
                       (tenant,)).fetchone()[0]
    sup.rollback()
    return {"familier": fam, "versjoner": ver, "komponenter": komp,
            "felt": felt}


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


def _utfyll(rt, tenant: str, vid: str) -> str:
    _kontekst(rt, tenant)
    rader = rt.execute(
        "SELECT tekst FROM m5_fyll_mal(%s,%s,%s::jsonb) ORDER BY rekkefolge",
        (tenant, vid, json.dumps(VERDIER))).fetchall()
    rt.rollback()
    return "".join(r[0] or "" for r in rader)


# ---------------------------------------------------------------------------

def fasit() -> Path:
    sup = psycopg.connect(_env("M05_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(rt, runde)
    _log(f"rigget runde {runde}: {rigg['tenanter']}")

    per, avvik = [], 0
    for tkey, tenant in rigg["tenanter"].items():
        fikk = _tilstand(sup, tenant)
        ok = fikk == FORVENTET[tkey]
        avvik += 0 if ok else 1
        per.append({"tenant": tkey, "ventet": FORVENTET[tkey],
                    "fikk": fikk})
    tekst = _utfyll(rt, rigg["tenanter"]["a"], rigg["ider"]["a"]["V3"])

    # GJENSPILL: hver skrivende dør med samme id og samme innhold skal være
    # et stille ja — ingen nye rader, ingen nye hendelser.
    for_gjenspill = _digest(sup)
    gjenspill_feil = 0
    for tkey, sekvens in SEKVENS.items():
        tenant = rigg["tenanter"][tkey]
        for handling, fam, ver in sekvens:
            if handling not in ("familie", "versjon"):
                continue
            try:
                _kall(rt, tenant, handling, fam, ver, rigg["ider"][tkey])
            except psycopg.Error:
                rt.rollback()
                gjenspill_feil += 1
    etter_gjenspill = _digest(sup)

    ev = {tkey: _evidens(sup, t) for tkey, t in rigg["tenanter"].items()}
    ev_avvik = sum(1 for tkey in SEKVENS
                   if ev[tkey]["per_handling"] != FORVENTET_EVIDENS[tkey])
    _log(f"tilstand avvik {avvik}; utfylling {tekst!r}; gjenspill"
         f" urørt={for_gjenspill == etter_gjenspill}; evidens {ev}")
    art = {
        "oppsett": {**_oppsett(runde), "tenanter": rigg["tenanter"]},
        "maalt": {
            "per_tenant": per,
            "tilstandsavvik": avvik,
            "tenanter": len(per),
            "dorkall": sum(len(s) for s in SEKVENS.values()),
            "utfylling_lik_fasit": tekst == UTFYLT,
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
    return _skriv("m05-fasit-v1", art)


def feilinjisering() -> Path:
    sup = psycopg.connect(_env("M05_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(rt, runde)
    ta = rigg["tenanter"]["a"]
    ia = rigg["ider"]["a"]

    def brudd_publiser_igjen(c):
        _kontekst(c, ta)
        c.execute("SELECT m5_publiser_malversjon(%s,%s,%s)",
                  (ta, ia["V3"], AKTOR))

    def brudd_trekk_utkast(c):
        tb = rigg["tenanter"]["b"]
        _kontekst(c, tb)
        c.execute("SELECT m5_trekk_tilbake_malversjon(%s,%s,%s)",
                  (tb, rigg["ider"]["b"]["V4"], AKTOR))

    def brudd_materiell_konflikt(c):
        _kontekst(c, ta)
        annet = json.loads(json.dumps(KOMPONENTER))
        annet[0]["innhold"] = "Endret avtale mellom "
        c.execute("SELECT * FROM m5_opprett_malversjon(%s,%s,%s::jsonb,"
                  "%s::jsonb,%s,%s)",
                  (ta, ia["F2"], json.dumps(annet), json.dumps(FELT),
                   AKTOR, ia["V3"]))

    def brudd_uten_kontekst(c):
        c.execute("SELECT m5_opprett_malfamilie(%s,%s,%s,%s,%s)",
                  (ta, "Uten kontekst", None, AKTOR, str(uuid.uuid4())))

    def brudd_endre_publisert(c):
        # Som eieren, rett i tabellen: innholdsvakten skal nekte.
        _kontekst(c, ta)
        c.execute("SET LOCAL ROLE disponit_mal_eier")
        c.execute("UPDATE malkomponent SET innhold = 'X' WHERE tenant=%s"
                  " AND versjon_id=%s AND komponenttype='tekst'",
                  (ta, ia["V3"]))

    brudd = [("publiser_publisert", rt, brudd_publiser_igjen),
             ("trekk_tilbake_utkast", rt, brudd_trekk_utkast),
             ("materiell_idempotenskonflikt", rt, brudd_materiell_konflikt),
             ("dor_uten_tenantkontekst", rt, brudd_uten_kontekst),
             ("endre_publisert_innhold", mg, brudd_endre_publisert)]
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
    return _skriv("m05-feilinjisering-v1", art)


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
dsn, tok = sys.argv[1], sys.argv[2]
app = lag_app(dsn)
with TestClient(app) as k:
    r = k.get("/v1/dokumentmal", headers={"authorization": f"Bearer {tok}"})
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
    sup = psycopg.connect(_env("M05_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(rt, runde)
    tok = _token(mg, rigg["tenanter"]["a"])
    rb, kd = _http(forgjenger, tok), _http(REPO, tok)
    kd_tekst = json.dumps(kd["body"], ensure_ascii=False)
    art = {
        "oppsett": {**_oppsett(runde), "forgjenger": str(forgjenger),
                    "kjerne_forgjenger": kjerne_digest(forgjenger),
                    "kjerne_kandidat": kjerne_digest(REPO),
                    "modul_forgjenger": release_digest(forgjenger,
                                                       M05_MODULFILER),
                    "modul_kandidat": release_digest(REPO, M05_MODULFILER)},
        "maalt": {
            "rullbakk_status": rb["status"],
            "kandidat_status": kd["status"],
            "svar_like": rb == kd,
            # Positiv kontroll: svaret ER registeret riggen fylte.
            "kandidat_baerer_riggen": all(
                rigg["ider"]["a"][v] in kd_tekst for v in ("V1", "V2", "V3")),
        },
    }
    _log(f"rullbakk {rb['status']}, kandidat {kd['status']},"
         f" like={rb == kd}")
    return _skriv("m05-rollback-v1", art)


def ytelse(kall: int) -> Path:
    sup = psycopg.connect(_env("M05_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    runde = secrets.token_hex(4)
    rigg = forbered(rt, runde)
    ta, vid = rigg["tenanter"]["a"], rigg["ider"]["a"]["V3"]
    ms, feil, riktige = [], 0, 0
    for _ in range(kall):
        t0 = time.monotonic()
        try:
            tekst = _utfyll(rt, ta, vid)
            riktige += 1 if tekst == UTFYLT else 0
        except psycopg.Error:
            rt.rollback()
            feil += 1
        ms.append((time.monotonic() - t0) * 1000)
    s = sorted(ms)
    p95 = s[max(0, int(round(0.95 * len(s))) - 1)]
    _log(f"{kall} utfyllinger: p50={s[len(s) // 2]:.2f} ms p95={p95:.2f} ms")
    art = {"oppsett": _oppsett(runde),
           "maalt": {"kall": kall, "feilede": feil, "riktige": riktige,
                     "p50_ms": round(s[len(s) // 2], 3),
                     "p95_ms": round(p95, 3), "maks_ms": round(s[-1], 3),
                     "ms": [round(x, 3) for x in ms]}}
    return _skriv("m05-ytelse-v1", art)


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
