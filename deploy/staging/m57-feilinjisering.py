#!/usr/bin/env python3
"""Feilinjisering for M-57 — produserer `m57-feilinjisering-v1`.

SP-3 (driftsfeil er ikke verdikter) på M-57: én GIFTIG bunt bestilles som
kunden bestiller (inndata reservert, lastet opp, bestilt som bestiller) —
et arkivmedlem på 8 MiB nuller komprimert til noen kilobyte, over
`parsing.MAKS_KOMPRIMERINGSFORHOLD` (100:1). Arbeideren skal reise
`Buntfeil` → `Kjoringsfeil` og kvittere `feilet` med kode; API-et skal
sette oppdraget `feilet` og — kontrakten er kompenserende — knytte en sak
til oppdraget i M-37s kø (`sak_for_oppdrag`, årsak utforelse_feilet).
Skriptet dømmer ingenting selv: det leser oppdragsraden, kandidatlageret
og køen og skriver tallene; porten (`_grenser_m57_feilinjisering`)
validerer.

BRUK (på verten som root, fra et utsjekk, `staging.env` sourcet):
    python deploy/staging/m57-feilinjisering.py --tenant t-m57fasit [--ut …]

Tokenet lages på verten (token-cli, bootstrap), holdes i minne, printes
aldri og tilbakekalles ved avslutning.
"""
from __future__ import annotations

import argparse
import atexit
import io
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import _sjekk_grenser, valider_artefaktformat  # noqa: E402

API = "https://disponit.com"
KRAV = "m57-feilinjisering-v1"
GIFTBYTES = 8 * 1024 * 1024
FEILFRIST_S = 900.0


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def bestillertoken(tenant: str) -> str:
    r = subprocess.run(
        [sys.executable, str(REPO / "deploy/staging/token-cli.py"), "opprett",
         "--tenant", tenant, "--rolle", "bestiller",
         "--scope", "bestilling:opprett", "--bootstrap"],
        capture_output=True, text=True, timeout=120)
    treff = re.search(r"\b([A-Za-z0-9_-]+\.[A-Za-z0-9_-]{20,})\b", r.stdout)
    if not treff:
        raise SystemExit("AVBRUTT: fikk ikke bestillertoken")
    tok = treff.group(1)
    tid = tok.split(".", 1)[0]
    atexit.register(lambda: subprocess.run(
        [sys.executable, str(REPO / "deploy/staging/token-cli.py"),
         "deaktiver", tid], capture_output=True, timeout=60))
    return tok


def api(metode, sti, tok, data=None, ctype="application/json"):
    req = urllib.request.Request(API + sti, data=data, method=metode)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Idempotency-Key", "feilinj-" + secrets.token_hex(8))
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def giftig_bunt() -> bytes:
    """Ett medlem over komprimeringsgrensen, ellers en gyldig bunt."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("k-0001/soknad.html",
                   "<html><body><p>Jeg søker stillingen. Norsk morsmål.</p>"
                   "</body></html>")
        z.writestr("k-0001/vedlegg.txt", b"\x00" * GIFTBYTES)
        z.writestr("soknader.json", json.dumps({"soknader": [
            {"kandidat_id": "k-0001",
             "filer": ["k-0001/soknad.html", "k-0001/vedlegg.txt"],
             "felter": {"navn": ["Gift Kandidat"]}}]}))
    return buf.getvalue()


def q(conn, tenant, sql, args=()):
    conn.execute("SELECT set_config('disponit.tenant', %s, true)", (tenant,))
    rader = conn.execute(sql, args).fetchall()
    conn.rollback()
    return rader


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--vert", default=os.uname().nodename)
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not dsn:
        raise SystemExit("AVBRUTT: DISPONIT_MIGRATOR_URL mangler")
    m = psycopg.connect(dsn)
    profil = q(m, a.tenant, "SELECT profil_id, versjon FROM stillingsprofil"
               " WHERE tenant=%s ORDER BY opprettet DESC LIMIT 1", (a.tenant,))
    if not profil:
        raise SystemExit(f"AVBRUTT: {a.tenant} har ingen stillingsprofil")
    profilref = f"{profil[0][0]}@{profil[0][1]}"
    release = q(m, a.tenant, "SELECT release_id FROM moduldeployment WHERE"
                " modul_id='m57_ats' AND miljo='staging' AND livslop='claiming'")
    release_id = release[0][0] if release else "?"

    tok = bestillertoken(a.tenant)
    kropp = giftig_bunt()
    st, sv = api("POST", "/v1/inndata/reserver", tok, json.dumps(
        {"eiermodul": "m57_ats", "formaal": "soknadsbunt"}).encode())
    if st != 201:
        raise SystemExit(f"AVBRUTT: reserver {st} {sv}")
    jti, ref = sv["reservasjon_jti"], sv["inndata_ref"]
    st, sv = api("PUT", f"/v1/inndata/opplast/{jti}", tok, kropp, "application/zip")
    if st != 201:
        raise SystemExit(f"AVBRUTT: opplast {st} {sv}")
    t0 = time.monotonic()
    st, sv = api("POST", "/v1/bestilling", tok, json.dumps(
        {"bestillingstype": "rekruttering.evaluering", "inndata_ref": ref,
         "stillingsprofil_ref": profilref, "antall_soknader": 1,
         "omfang": "bunt"}).encode())
    if st != 200 or not isinstance(sv, dict) or sv.get("beslutning") != "tillat":
        raise SystemExit(f"AVBRUTT: bestilling {st} {sv}")
    oid = int(sv["oppdrag_id"])
    _log(f"giftig bunt ({len(kropp)} byte zip, {GIFTBYTES} byte utpakket medlem)"
         f" bestilt: oppdrag {oid}")

    # vent på terminalstatus — RENT utfall, aldri en hengende jobb
    while True:
        rad = q(m, a.tenant, "SELECT status, kvittering->>'resultat',"
                " kvittering->>'feilkode' FROM oppdrag WHERE tenant=%s AND id=%s",
                (a.tenant, oid))[0]
        if rad[0] in ("utfort", "feilet"):
            break
        if time.monotonic() - t0 > FEILFRIST_S:
            raise SystemExit(f"AVBRUTT: oppdrag {oid} er {rad[0]} etter"
                             f" {FEILFRIST_S:g} s — ingen terminalstatus")
        time.sleep(5)
    feilet_etter = time.monotonic() - t0
    status, resultat, feilkode = rad
    _log(f"oppdrag {oid}: {status} ({resultat}, {feilkode}) etter {feilet_etter:.0f} s")

    promoterte = q(m, a.tenant,
                   "SELECT count(*) FROM kandidat_evalueringsartefakt e"
                   " JOIN rekrutteringsprosess p ON p.tenant=e.tenant"
                   "  AND p.prosess_id=e.prosess_id"
                   " WHERE p.tenant=%s AND p.oppdrag_id=%s", (a.tenant, oid))[0][0]
    saker = q(m, a.tenant, "SELECT id, oppdrag_id, sakskilde, arsak, status"
              " FROM unntak WHERE tenant=%s AND oppdrag_id=%s ORDER BY id",
              (a.tenant, oid))
    uten = q(m, a.tenant, "SELECT count(*) FROM unntak WHERE tenant=%s"
             " AND oppdrag_id IS NULL AND ts > now() - interval '1 hour'"
             " AND sakskilde='oppdrag'", (a.tenant,))[0][0]
    sak = saker[0] if saker else (None, None, None, None, None)
    ts = datetime.now(timezone.utc).isoformat()
    art = {
        "krav_id": KRAV, "ts": ts, "bestatt": True,
        "oppsett": {"modul": "m57_ats", "vert": a.vert, "tenant": a.tenant,
                    "gift": f"zip-medlem {GIFTBYTES // (1024 * 1024)} MiB nuller"
                            " (forhold > 100:1)",
                    "release": release_id},
        "maalt": {"injisert_jobber": 1, "injisert_oppdrag_id": oid,
                  "oppdrag_status": status, "kvittering_resultat": resultat,
                  "feilkode": feilkode, "promoterte_artefakter": int(promoterte),
                  "unntakskoe_poster": len(saker),
                  "koeposter_uten_jobbinding": int(uten),
                  "sak_id": sak[0], "sak_oppdrag_id": sak[1],
                  "sak_sakskilde": sak[2], "sak_arsak": sak[3],
                  "sak_status": sak[4], "feilet_etter_s": round(feilet_etter, 1)},
    }
    feil = valider_artefaktformat(art, KRAV) + _sjekk_grenser(KRAV, art)
    art["bestatt"] = not feil
    if feil:
        art["feil"] = feil
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"{KRAV}-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True)
                  + "\n", encoding="utf-8")
    _log(f"skrev {ut} (bestatt={art['bestatt']})")
    for f in feil:
        _log("  FEIL:", f)
    return 0 if not feil else 1


if __name__ == "__main__":
    raise SystemExit(main())
