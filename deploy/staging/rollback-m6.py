#!/usr/bin/env python3
"""Flippedrillen for M-6 (e-postinnhenteren) — produserer `m6-rollback-v1`.

M-6 har ingen egen release og ingen egen arbeider: innhenteren
(`plan.epost.hent_en`) kjører i planarbeideren og RULLER MED KJERNEN.
En rollback av M-6 er derfor en rollback av kjernebytene innhenteren er
— `/opt/disponit/aktiv` pekes på forgjengerens katalog. Det som må holde
er innhentingens tilstand over rullingen:

  (a) INFLIGHT: en innhenting på de drillede bytene avbrytes MIDT I
      (side 1 committet, side 2 svarer 503) — meldingene fra side 1 står,
      delta-cursoren står urørt (ingen melding er «hentet» uten å være
      lagret);
  (b) RULLBAKK: forgjengerens bytes fullfører den samme kilden fra samme
      cursor — side 1 om igjen (idempotent, null dubletter), side 2 nytt,
      delta satt; hver melding har nøyaktig én evidensrad;
  (c) KANDIDAT: de drillede bytene ser alt som hentet (delta-veien gir
      ingenting nytt).

Postboksen er RIGGET (m6_fasit.py-formen, akseptert for datasettpunktet):
`hent_en(conn, rad, graf=…, veksler=…)` tar transporten som parameter;
alt annet — token-dekryptering, lagring, broen til kundeservice,
evidens — går som i drift, i den ekte basen med planarbeiderens rolle.
Hver kjøring er en UNDERPROSESS med `PYTHONPATH` på release-katalogen,
og runneren bevitner hvilken fil `plan.epost` faktisk ble lastet fra.
Kilden fødes `deaktivert` så planrunden aldri plukker den; `hent_en`
tar raden den får.

BRUK (på verten som root, fra et utsjekk, `staging.env` sourcet):
    python deploy/staging/rollback-m6.py \\
        --forgjenger-katalog /opt/disponit/releases/<c1> [--ut …]

Hemmeligheter (DSN-er, KEK) leses av miljøet og printes aldri.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (_sjekk_grenser, m6_digest,  # noqa: E402
                            valider_artefaktformat)

KRAV = "m6-rollback-v1"
AKTOR = "m6-drill"
POSTBOKS = "drill@disponit.com"
KUNDE = "eliassi@gmail.com"
KUNDE_NAVN = "Drill Kunde"
PER_SIDE = 5

RUNNER = r'''
import json, os, sys
inn = json.load(open(sys.argv[1], encoding="utf-8"))
from db.pg import koble, sett_kontekst
import plan.epost as pe
from plan.epost import GRAPH, GraphFeil, hent_en, AKTOR as PLANAKTOR

tenant, kid, rid, modus = inn["tenant"], inn["kilde_id"], inn["runde_id"], inn["modus"]
ider = inn["ider"]                      # [[merke, ...side1], [...side2]]

def melding(merke, i):
    return {"id": f"AAMk-{merke}-{rid}", "conversationId": f"c-{rid}",
            "receivedDateTime": f"2026-09-17T1{i % 10}:00:00Z",
            "toRecipients": [{"emailAddress": {"address": inn["postboks"]}}],
            "subject": f"Drill {merke}", "bodyPreview": f"Forhåndsvisning {merke}",
            "hasAttachments": False,
            "from": {"emailAddress": {"address": inn["kunde"], "name": inn["kunde_navn"]}}}

sider = [
    {"value": [melding(m, i) for i, m in enumerate(ider[0])],
     "@odata.nextLink": f"{GRAPH}/drill/{rid}/side2"},
    {"value": [melding(m, i) for i, m in enumerate(ider[1])],
     "@odata.deltaLink": f"{GRAPH}/drill/{rid}/delta1"},
]
kall = []

def graf(access, url, *, tekstkropp=False):
    kall.append(url)
    if "/me/messages/" in url:
        mid = url.split("/me/messages/")[1].split("?")[0]
        merke = mid.split("-")[1]
        return {"body": {"contentType": "text",
                         "content": f"Hei, dette er drillmelding {merke}. Mvh {inn['kunde_navn']}"}}
    if modus == "delta" or f"/drill/{rid}/delta" in url:
        return {"value": [], "@odata.deltaLink": f"{GRAPH}/drill/{rid}/delta2"}
    if modus == "avbrutt" and url.endswith("/side2"):
        raise GraphFeil(503, "drill: avbrutt midt i innhentingen")
    if not sider:
        return {"value": [], "@odata.deltaLink": f"{GRAPH}/drill/{rid}/tom"}
    return sider.pop(0)

def veksler(_konfig, _refresh):
    return {"access_token": "drill-" + rid}

pa = koble(os.environ["DISPONIT_PLAN_URL"])
sett_kontekst(pa, tenant, PLANAKTOR, "m6-drill")
rad = pa.execute("SELECT tenant, kilde_id, postboks, delta_token, sist_hentet_ts"
                 " FROM epost_kilde WHERE tenant=%s AND kilde_id=%s",
                 (tenant, kid)).fetchone()
pa.rollback()
res = hent_en(pa, rad, graf=graf, veksler=veksler)
pa.close()
print(json.dumps({"resultat": res, "kall": kall, "epost_fil": pe.__file__,
                  "pid": os.getpid()}, default=str))
'''


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def kjor_i(katalog: Path, inn: dict) -> dict:
    """Innhentingen som underprosess fra `katalog` — bytene der."""
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{katalog}/platform/core:{katalog}/platform"
    with tempfile.TemporaryDirectory() as td:
        Path(td, "inn.json").write_text(json.dumps(inn), encoding="utf-8")
        Path(td, "runner.py").write_text(RUNNER, encoding="utf-8")
        r = subprocess.run([sys.executable, f"{td}/runner.py", f"{td}/inn.json"],
                           capture_output=True, text=True, timeout=600,
                           cwd=f"{katalog}/platform/core", env=env)
    if r.returncode != 0:
        raise SystemExit(f"AVBRUTT: runneren i {katalog} feilet:\n"
                         + r.stderr[-1500:])
    ut = json.loads(r.stdout.strip().splitlines()[-1])
    if not ut["epost_fil"].startswith(str(katalog) + "/"):
        raise SystemExit(f"AVBRUTT: plan.epost lastet fra {ut['epost_fil']},"
                         f" ikke {katalog}")
    return ut


def q(conn, tenant, sql, args=()):
    conn.execute("SELECT set_config('disponit.tenant', %s, true),"
                 " set_config('disponit.aktor', %s, true),"
                 " set_config('disponit.request_id', %s, true)",
                 (tenant, AKTOR, "drill"))
    cur = conn.execute(sql, args)
    rader = cur.fetchall() if cur.description else []
    conn.commit()
    return rader


def lag_kilde(rt, tenant: str) -> uuid.UUID:
    """Postboksen inn gjennom samme rad callbacken skriver (m6_fasit-formen),
    så DEAKTIVERT: planrunden skal aldri plukke den — drillen gir
    `hent_en` raden selv."""
    from api.epost_kilde import SENDESCOPE  # noqa: F401 — krever konfig
    from db import kryptering
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, AKTOR, "m6-drill")
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(rt, tenant)
    rt.commit()
    ct, nonce = kryptering.krypter(dek, {"refresh_token": "drill-refresh"},
                                   tenant, key_id)
    scope = "https://graph.microsoft.com/Mail.Read offline_access"
    sett_kontekst(rt, tenant, AKTOR, "m6-drill")
    kid = rt.execute(
        "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
        " auth_kryptert, nonce, key_id, scope)"
        " VALUES (%s,'m365',%s,%s,%s,%s,%s) RETURNING kilde_id",
        (tenant, POSTBOKS, ct, nonce, key_id, scope)).fetchone()[0]
    rt.commit()
    q(rt, tenant, "UPDATE epost_kilde SET status='deaktivert'"
      " WHERE tenant=%s AND kilde_id=%s", (tenant, kid))
    return kid


def tilstand(rt, tenant, kid, rid) -> dict:
    (antall, ulike), = q(rt, tenant,
                         "SELECT count(*), count(DISTINCT leverandor_melding_id)"
                         " FROM epost_melding WHERE tenant=%s AND kilde_id=%s",
                         (tenant, kid))
    (delta,), = q(rt, tenant, "SELECT delta_token FROM epost_kilde"
                  " WHERE tenant=%s AND kilde_id=%s", (tenant, kid))
    (ev, ev_hash), = q(rt, tenant,
                       "SELECT count(*), count(DISTINCT input_hash) FROM revisjonslogg"
                       " WHERE tenant=%s AND kilde='m17_kundeservice'"
                       " AND handling='henvendelse.mottatt'", (tenant,))
    return {"meldinger": int(antall), "ulike": int(ulike), "delta": delta,
            "evidens": int(ev), "evidens_ulike": int(ev_hash)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forgjenger-katalog", required=True)
    ap.add_argument("--drillet-katalog", default="/opt/disponit/aktiv")
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    rt_dsn = os.environ.get("DATABASE_URL")
    if not (rt_dsn and os.environ.get("DISPONIT_PLAN_URL")
            and os.environ.get("DISPONIT_KEK")):
        raise SystemExit("AVBRUTT: DATABASE_URL/DISPONIT_PLAN_URL/DISPONIT_KEK mangler")
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"{KRAV}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    if ut.exists():
        raise SystemExit(f"AVBRUTT: {ut} finnes")
    d_kat = Path(a.drillet_katalog).resolve()
    f_kat = Path(a.forgjenger_katalog).resolve()
    if d_kat == f_kat:
        raise SystemExit("AVBRUTT: forgjengeren er den drillede katalogen")
    for kat in (d_kat, f_kat):
        if not (kat / "platform/core/plan/epost.py").is_file():
            raise SystemExit(f"AVBRUTT: {kat} har ingen innhenter")
    drillet_digest, forgjenger_digest = m6_digest(d_kat), m6_digest(f_kat)
    _log(f"drillet {d_kat.name[:8]} ({drillet_digest[:12]}) ← forgjenger"
         f" {f_kat.name[:8]} ({forgjenger_digest[:12]})")

    rt = psycopg.connect(rt_dsn)
    tenant = f"t-m6drill-{secrets.token_hex(3)}"
    rid = secrets.token_hex(4)
    kid = lag_kilde(rt, tenant)
    ider = [[f"a{i}" for i in range(PER_SIDE)], [f"b{i}" for i in range(PER_SIDE)]]
    felles = {"tenant": tenant, "kilde_id": str(kid), "runde_id": rid,
              "ider": ider, "postboks": POSTBOKS, "kunde": KUNDE,
              "kunde_navn": KUNDE_NAVN}
    t0 = tilstand(rt, tenant, kid, rid)
    if t0["meldinger"] or t0["delta"]:
        raise SystemExit("AVBRUTT: kilden er ikke tom før drillen")
    _log(f"tenant {tenant}, kilde {str(kid)[:8]}, {2 * PER_SIDE} meldinger rigget")

    def nye(res):
        r = res.get("resultat") or {}
        return int(r.get("nye") or 0), int(r.get("sett") or 0)

    def utfall(res):
        """`hent_en` sier `feilet: <grunn>` (auth), `forbigaende: graph_<n>`
        (SP-3: en driftsfeil som prøves igjen) eller ingenting (ok)."""
        r = res.get("resultat") or {}
        if r.get("feilet"):
            return f"feilet:{r['feilet']}"
        if r.get("forbigaende"):
            return f"forbigaende:{r['forbigaende']}"
        return "ok"

    # (a) inflight på de drillede bytene — avbrutt etter side 1
    r1 = kjor_i(d_kat, {**felles, "modus": "avbrutt"})
    t1 = tilstand(rt, tenant, kid, rid)
    _log(f"inflight (drillet): {utfall(r1)}"
         f" — {t1['meldinger']} meldinger lagret, delta {t1['delta']!r}")

    # (b) rullbakken: forgjengerens bytes fullfører fra samme cursor
    r2 = kjor_i(f_kat, {**felles, "modus": "full"})
    t2 = tilstand(rt, tenant, kid, rid)
    _log(f"rullbakk (forgjenger): {utfall(r2)}"
         f" — {t2['meldinger']} meldinger ({t2['ulike']} ulike), delta satt="
         f"{bool(t2['delta'])}")

    # (c) kandidaten: de drillede bytene ser alt som hentet
    r3 = kjor_i(d_kat, {**felles, "modus": "delta"})
    t3 = tilstand(rt, tenant, kid, rid)
    _log(f"kandidat (drillet): {utfall(r3)}"
         f" — {t3['meldinger']} meldinger, evidens {t3['evidens']}"
         f" ({t3['evidens_ulike']} ulike)")

    n1, s1 = nye(r1); n2, s2 = nye(r2); n3, s3 = nye(r3)
    art = {
        "krav_id": KRAV, "ts": datetime.now(timezone.utc).isoformat(),
        "bestatt": True,
        "oppsett": {
            "modul": "m06_epost", "miljo": "staging", "vert": os.uname().nodename,
            "tenant": tenant, "kilde_id": str(kid),
            "drillet_release": d_kat.name, "forgjenger_release": f_kat.name,
            "drillet_katalog": str(d_kat), "forgjenger_katalog": str(f_kat),
            "drillet_digest": drillet_digest, "forgjenger_digest": forgjenger_digest,
            "kandidat_digest": drillet_digest,
            "meldinger_rigget": 2 * PER_SIDE, "per_side": PER_SIDE,
            "form": "kjerne: innhenteren ruller med kjernen — rigget Graph,"
                    " ekte base, planarbeiderens rolle, underprosess per"
                    " release-katalog",
        },
        "identiteter": {
            "runde_id": rid, "inflight_pid": r1["pid"], "rullback_pid": r2["pid"],
            "kandidat_pid": r3["pid"],
            "inflight_fil": r1["epost_fil"], "rullback_fil": r2["epost_fil"],
            "kandidat_fil": r3["epost_fil"],
        },
        "maalt": {
            "inflight_utfall": utfall(r1),
            "inflight_hentet": t1["meldinger"],
            "inflight_delta_uendret": t1["delta"] is None,
            "rullbakk_utfall": utfall(r2),
            "rullbakk_nye": n2, "rullbakk_sett": s2,
            "rullbakk_delta_satt": bool(t2["delta"]),
            "total_meldinger": t3["meldinger"],
            "dubletter": t3["meldinger"] - t3["ulike"],
            "kandidat_utfall": utfall(r3),
            "kandidat_nye": n3, "kandidat_sett": s3,
            "evidens_mottatt": t3["evidens"],
            "evidens_ulike_hash": t3["evidens_ulike"],
            "rullback_bytes_er_forgjengerens":
                r2["epost_fil"].startswith(str(f_kat) + "/"),
            "kandidat_bytes_er_drillede":
                r3["epost_fil"].startswith(str(d_kat) + "/"),
            "release_digest_bundet": (m6_digest(d_kat) == drillet_digest
                                      and m6_digest(f_kat) == forgjenger_digest),
        },
        "etterkontroll": {
            "kilde_deaktivert": q(rt, tenant, "SELECT status FROM epost_kilde"
                                  " WHERE tenant=%s AND kilde_id=%s",
                                  (tenant, kid))[0][0] == "deaktivert",
            "digest_likhet": drillet_digest == m6_digest(d_kat),
            "aktiv_urort": Path("/opt/disponit/aktiv").resolve() == d_kat,
        },
    }
    feil = valider_artefaktformat(art, KRAV) + _sjekk_grenser(KRAV, art)
    art["bestatt"] = not feil
    if feil:
        art["feil"] = feil
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    _log(f"skrev {ut} (bestatt={art['bestatt']})")
    for f in feil:
        _log("  FEIL:", f)
    return 0 if not feil else 1


if __name__ == "__main__":
    raise SystemExit(main())
