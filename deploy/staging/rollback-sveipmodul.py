#!/usr/bin/env python3
"""Flippedrillen for en SVEIPMODUL — produserer `m<X>-rollback-v1`.

`rollback-m56-v1`s form (049), uten image: en sveipmodul kjører
`python -m drift.<m>_arbeider` fra `/opt/disponit/aktiv`, og en release
ER filene i `manifestskjema.SVEIPMODUL_RELEASEFILER` (digesten er
`sveipmodul_digest`, regnbar av treet). Release-byttet for ÉN modul er en
systemd-override på arbeiderens unit som peker `WorkingDirectory` og
`PYTHONPATH` på en release-katalog — plattformen (API, andre arbeidere)
står urørt (planens valg B-i).

De tre leddene aksepten krever, målt på oppdragsradene selv:
  (a) claim-stopp: den drenerte releasen claimer INGENTING nytt.
  (b) rent utfall: oppdraget som VAR claimet da rullingen traff,
      fullfører med signert kvittering — aldri et falskt verdikt.
  (b2) rullbakken KJØRER forgjengerens bytes og claimer og fullfører
       oppdraget claim-stoppet lot ligge.
  (c) kandidaten (de drillede bytene) overtar og claimer sitt eget.

FORBEREDELSEN (`--forbered`): registeret bærer bare r1 for hver modul,
og r1s digest ble oppgitt for hånd 9/9 — den er ikke regnbar. Drillen
trenger en FORGJENGER med bootbare bytes. `--forbered` registrerer to
releaser fra to release-kataloger som fortsatt ligger på verten
(`m14-r2-<commit>` og `m14-r3-<commit>`, digest regnet av katalogene)
og bytter til dem i rekkefølge gjennom `bytt_release` — så registerets
egen historie (`releasebytte`-hendelsene) sier at r3 overtok fra r2.

BRUK (på verten som root, fra et utsjekk av grenen, `staging.env` sourcet):
    python deploy/staging/rollback-sveipmodul.py --modul m14_fakturakontroll \\
        --forbered --forgjenger-katalog /opt/disponit/releases/<c1> \\
        --drillet-katalog /opt/disponit/releases/<c2>
    python deploy/staging/rollback-sveipmodul.py --modul m14_fakturakontroll \\
        --forgjenger-katalog /opt/disponit/releases/<c1> --ut <artefakt>

Hemmeligheter (DSN-er, token) leses av miljøet og printes aldri.
"""
from __future__ import annotations

import argparse
import atexit
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (_sjekk_grenser, kanonisk_projeksjon,  # noqa: E402
                            sveipmodul_digest, valider_artefaktformat)

MILJO = "staging"
API = "https://disponit.com"
DRILLNOKKEL = 915_774_057          # et annet rom enn m56s 915_774_056
RESERVASJONSVARIGHET_S = 600
RESERVASJONSHJERTESLAG_S = 30
OVERTAKELSESFRIST_S = 900
CLAIMSTOPP_VENT_S = 25.0
AKTOR = "sveipmodul-drill"

#: Per modul: arbeiderens unit, bestillingstypen drilloppdragene bærer,
#: og hvordan tenanten forberedes slik at bestillingene går `tillat`.
MODULER: dict[str, dict] = {
    "m14_fakturakontroll": {
        "unit": "disponit-m14", "prefiks": "m14",
        "bestillingstype": "faktura.bokfor", "bransje": "tjenestebedrift",
        # Bransjemalen: `faktura.bokfor` er `tillatt_for: [agent]`.
        "rolle": "agent", "krav_id": "m14-rollback-v1",
    },
}

RESERVASJONSTOKEN = ""
RESERVASJONEN_TAPT = ""
HJERTESTOPP = threading.Event()
OVERRIDE_SKREVET = False


def _log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------- register
def _admin(m):
    m.execute("SET ROLE disponit_modules_admin")


def _reset(m):
    m.execute("RESET ROLE")


def den_ene_claimende(m, modul):
    rader = m.execute(
        "SELECT d.release_id, d.kontraktversjon, d.kontrakt_hash,"
        "       r.artifact_digest"
        "  FROM moduldeployment d JOIN modulrelease r"
        "    ON r.modul_id = d.modul_id AND r.release_id = d.release_id"
        "   AND r.kontraktversjon = d.kontraktversjon"
        "   AND r.kontrakt_hash = d.kontrakt_hash"
        " WHERE d.modul_id=%s AND d.miljo=%s AND d.livslop='claiming'"
        " ORDER BY d.kontraktversjon, d.kontrakt_hash, d.release_id",
        (modul, MILJO)).fetchall()
    m.commit()
    if len(rader) != 1:
        raise SystemExit(f"AVBRUTT: {modul} har {len(rader)} claimende"
                         f" deployments i {MILJO} — drillen måler ÉN")
    return rader[0]


def forgjengeren(m, modul, drillet, kver, khash):
    """Releasen i den nest siste `releasebytte`-hendelsen på linjen."""
    egen = m.execute(
        "SELECT max(id) FROM modulregister_hendelse"
        " WHERE modul_id=%s AND miljo=%s AND hendelse='releasebytte'"
        "   AND kontraktversjon=%s AND kontrakt_hash=%s AND release_id=%s",
        (modul, MILJO, kver, khash, drillet)).fetchone()[0]
    if egen is None:
        raise SystemExit(f"AVBRUTT: {drillet} har ingen releasebytte-"
                         "hendelse — kjør --forbered først")
    rad = m.execute(
        "SELECT h.release_id, r.artifact_digest FROM modulregister_hendelse h"
        "  JOIN modulrelease r ON r.modul_id=h.modul_id"
        "   AND r.release_id=h.release_id AND r.kontraktversjon=h.kontraktversjon"
        "   AND r.kontrakt_hash=h.kontrakt_hash"
        " WHERE h.modul_id=%s AND h.miljo=%s AND h.hendelse='releasebytte'"
        "   AND h.kontraktversjon=%s AND h.kontrakt_hash=%s AND h.id < %s"
        " ORDER BY h.id DESC LIMIT 1",
        (modul, MILJO, kver, khash, egen)).fetchone()
    m.commit()
    if rad is None:
        raise SystemExit(f"AVBRUTT: {drillet} har ingen forgjenger på"
                         " kontraktlinjen")
    return rad


def registrer_release(m, modul, rel, kver, khash, manifest_hash, digest):
    _admin(m)
    m.execute("SELECT registrer_release(%s,%s,%s,%s,%s,%s,%s)",
              (modul, rel, kver, khash, manifest_hash, digest, AKTOR))
    _reset(m)
    m.commit()


def bytt_release(m, modul, rel, kver, khash):
    _admin(m)
    if RESERVASJONSTOKEN:
        m.execute("SELECT set_config('disponit.deployreservasjon', %s, true)",
                  (RESERVASJONSTOKEN,))
    m.execute("SELECT bytt_release(%s,%s,%s,%s,%s,%s)",
              (modul, MILJO, rel, kver, khash, AKTOR))
    _reset(m)
    m.commit()


def livslop(m, modul, rel):
    rad = m.execute("SELECT livslop FROM moduldeployment WHERE modul_id=%s"
                    " AND miljo=%s AND release_id=%s",
                    (modul, MILJO, rel)).fetchone()
    m.commit()
    return rad[0] if rad else None


def manifest_hash_i(katalog: Path, modul: str) -> str:
    return kanonisk_projeksjon(
        (katalog / "platform/modules" / modul / "manifest.yaml")
        .read_text(encoding="utf-8"))


# ------------------------------------------------------------ reservasjon
def ta_reservasjonen(m, modul, dsn):
    global RESERVASJONSTOKEN
    fikk = m.execute("SELECT pg_try_advisory_lock(%s, hashtext(%s))",
                     (DRILLNOKKEL, f"{modul}:{MILJO}")).fetchone()[0]
    m.commit()
    if not fikk:
        raise SystemExit("AVBRUTT: en annen drill holder drillåsen")
    token = "drill-" + secrets.token_hex(12)
    _admin(m)
    m.execute("SELECT ta_deployreservasjon(%s,%s,%s,%s,%s)",
              (modul, MILJO, token, AKTOR, f"{RESERVASJONSVARIGHET_S} seconds"))
    _reset(m)
    m.commit()
    RESERVASJONSTOKEN = token
    os.environ["DISPONIT_DEPLOYRESERVASJON"] = token
    atexit.register(frigi_reservasjonen, m, modul)
    t = threading.Thread(target=_hjerteslag, args=(dsn, modul), daemon=True)
    t.start()


def _hjerteslag(dsn, modul):
    global RESERVASJONEN_TAPT
    while not HJERTESTOPP.wait(RESERVASJONSHJERTESLAG_S):
        try:
            with psycopg.connect(dsn) as k:
                _admin(k)
                k.execute("SELECT forleng_deployreservasjon(%s,%s,%s,%s)",
                          (modul, MILJO, RESERVASJONSTOKEN,
                           f"{RESERVASJONSVARIGHET_S} seconds"))
                _reset(k)
                k.commit()
        except psycopg.errors.LockNotAvailable as e:
            RESERVASJONEN_TAPT = str(e)
            return
        except Exception as e:                              # noqa: BLE001
            _log(f"  ADVARSEL: hjerteslaget nådde ikke basen ({e})")


def krev_reservasjonen(hva):
    if RESERVASJONEN_TAPT:
        raise SystemExit(f"AVBRUTT før {hva}: reservasjonen er tapt —"
                         f" {RESERVASJONEN_TAPT}")


def frigi_reservasjonen(m, modul):
    if not RESERVASJONSTOKEN:
        return
    HJERTESTOPP.set()
    try:
        m.rollback()
        _admin(m)
        m.execute("SELECT frigi_deployreservasjon(%s,%s,%s)",
                  (modul, MILJO, RESERVASJONSTOKEN))
        _reset(m)
        m.commit()
    except Exception as e:                                  # noqa: BLE001
        _log(f"  ADVARSEL: reservasjonen ble ikke frigitt ({e}) — utløpet"
             " rydder")


# ----------------------------------------------------------- unit-override
def _overridefil(unit: str) -> Path:
    return Path(f"/etc/systemd/system/{unit}.service.d/drill.conf")


def boot_fra(unit: str, katalog: Path | None, hva: str) -> float:
    """Arbeideren startes fra `katalog` (override) eller fra `aktiv`
    (override fjernet). -> sekunder til unit-en er aktiv."""
    global OVERRIDE_SKREVET
    fil = _overridefil(unit)
    if katalog is None:
        if fil.exists():
            fil.unlink()
        OVERRIDE_SKREVET = False
    else:
        # Fjernes også ved et unormalt avbrudd: en override som står
        # igjen lar neste deploy flytte `aktiv` uten at arbeideren følger.
        if not OVERRIDE_SKREVET:
            atexit.register(fjern_override, unit)
        fil.parent.mkdir(parents=True, exist_ok=True)
        fil.write_text(
            "[Service]\n"
            f"WorkingDirectory={katalog}/platform\n"
            "Environment=PYTHONPATH=\n"
            f"Environment=PYTHONPATH={katalog}/platform/core:{katalog}/platform\n",
            encoding="utf-8")
        OVERRIDE_SKREVET = True
    t0 = time.monotonic()
    subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=60)
    subprocess.run(["systemctl", "restart", f"{unit}.service"], check=True,
                   timeout=120)
    for _ in range(60):
        r = subprocess.run(["systemctl", "is-active", f"{unit}.service"],
                           capture_output=True, text=True, timeout=30)
        if r.stdout.strip() == "active":
            _log(f"  {hva}: {unit} aktiv fra {katalog or 'aktiv'}")
            return time.monotonic() - t0
        time.sleep(1)
    raise SystemExit(f"AVBRUTT: {unit} kom ikke opp fra {katalog or 'aktiv'}")


def fjern_override(unit: str):
    fil = _overridefil(unit)
    if fil.exists():
        fil.unlink()
        subprocess.run(["systemctl", "daemon-reload"], timeout=60)
        subprocess.run(["systemctl", "restart", f"{unit}.service"], timeout=120)


# ------------------------------------------------------------- tenanten
def _sk(rt, tenant):
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, AKTOR, "drill")


def forbered_m14(rt, tenant: str, antall: int) -> list[dict]:
    """Fakturaer som `m14_for_bokforing` slipper gjennom: terskler, sats,
    kjent leverandør (M-24), eksakt mva, avgjort `kontrollert`.
    -> bestillingskropper."""
    _sk(rt, tenant)
    rt.execute("SELECT m14_sett_terskler(%s,1,10000000,30,3,%s)", (tenant, AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m14_sett_mvasats(%s,'hoy',250,'2020-01-01'::date,"
               "NULL::date,%s)", (tenant, AKTOR))
    rt.commit()
    lev = "Drill Leverandør AS"
    _sk(rt, tenant)
    rt.execute("SELECT m24_registrer_leverandor(%s,%s,%s,NULL,%s)",
               (tenant, uuid.uuid4(), lev, AKTOR))
    rt.commit()
    kropper = []
    for i in range(antall):
        fid = uuid.uuid4(); nr = f"DRILL-{secrets.token_hex(3)}-{i}"
        netto = 10_000 + i; mva = (netto * 250 + 500) // 1000
        _sk(rt, tenant)
        rt.execute(
            "SELECT m14_registrer_faktura(%s,%s,%s,%s,%s,%s,%s,'hoy','NOK',"
            " current_date - 10, current_date + 20, current_date - 1, %s)",
            (tenant, fid, lev, nr, netto, mva, netto + mva, AKTOR))
        rt.commit()
        _sk(rt, tenant)
        rt.execute("SELECT m14_avgjor_faktura(%s,%s,'kontrollert',%s,%s)",
                   (tenant, fid, "Drill: kontrollert uten avvik.", AKTOR))
        rt.commit()
        # Kroppen er referansen og omfanget — beløp, leverandør og
        # kontroller er registerets (`_normaliser_bokforing`).
        kropper.append({"bestillingstype": "faktura.bokfor",
                        "faktura_ref": f"faktura:{fid}", "omfang": "bilag"})
    return kropper


def sikre_policy(rt, tenant: str, bransje: str):
    """Bransjemalen inn gjennom bootstrap-døra — som registreringen gjør."""
    from db import kryptering
    from api.firmaregistrering import _aktiver_bransjemal
    _sk(rt, tenant)
    finnes = rt.execute(
        "SELECT 1 FROM policyer WHERE tenant=%s AND status='produksjon'",
        (tenant,)).fetchone()
    if finnes:
        rt.rollback(); return
    kryptering.hent_eller_opprett_aktiv_dek(rt, tenant)
    _aktiver_bransjemal(rt, tenant, bransje, [])
    rt.commit()


# --------------------------------------------------------------- token
def lag_token(tenant: str, rolle: str) -> tuple[str, str]:
    ut = subprocess.run(
        [sys.executable, str(REPO / "deploy/staging/token-cli.py"), "opprett",
         "--tenant", tenant, "--rolle", rolle, "--scope", "bestilling:opprett",
         "--bootstrap"], capture_output=True, text=True, timeout=120)
    m = re.search(r"^\s*([A-Za-z0-9_-]+\.[A-Za-z0-9_-]{20,})\s*$", ut.stdout, re.M)
    if not m:
        raise SystemExit("AVBRUTT: fikk ikke token: "
                         + re.sub(r"[A-Za-z0-9_-]{30,}", "…", ut.stdout + ut.stderr)[:300])
    tok = m.group(1)
    return tok, tok.split(".", 1)[0]


def tilbakekall_token(token_id: str):
    subprocess.run([sys.executable, str(REPO / "deploy/staging/token-cli.py"),
                    "deaktiver", token_id], capture_output=True, timeout=120)


def bestill(tok: str, kropp: dict, merkelapp: str) -> int:
    req = urllib.request.Request(API + "/v1/bestilling",
                                 data=json.dumps(kropp).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Idempotency-Key", f"drill-{merkelapp}-{secrets.token_hex(6)}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            sv = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"AVBRUTT: bestillingen ({merkelapp}) avvist:"
                         f" {e.code} {e.read().decode('utf-8', 'replace')[:300]}")
    if sv.get("beslutning") != "tillat":
        raise SystemExit(f"AVBRUTT: bestillingen ({merkelapp}) ble"
                         f" {sv.get('beslutning')!r}")
    return int(sv["oppdrag_id"])


# ------------------------------------------------------------ oppdragene
def _tenantkontekst(m, tenant):
    m.execute("SELECT set_config('disponit.tenant', %s, true)", (tenant,))


def status(m, tenant, oid):
    _tenantkontekst(m, tenant)
    rad = m.execute("SELECT status, claim_release_id, forste_claim_ts"
                    " FROM oppdrag WHERE tenant=%s AND id=%s",
                    (tenant, oid)).fetchone()
    m.commit()
    return rad


def vent_claimet(m, tenant, oid, frist_s):
    t0 = time.monotonic()
    while time.monotonic() - t0 < frist_s:
        rad = status(m, tenant, oid)
        if rad and rad[1]:
            return rad[1], time.monotonic() - t0
        time.sleep(1)
    return None, time.monotonic() - t0


def vent_terminal(m, tenant, oid, frist_s):
    t0 = time.monotonic()
    st = None
    while time.monotonic() - t0 < frist_s:
        rad = status(m, tenant, oid)
        st = rad[0] if rad else None
        if st in ("utfort", "feilet"):
            return st
        time.sleep(1)
    return st


def kvittering_ok(m, tenant, oid) -> bool:
    m.rollback()
    _admin(m)
    rad = m.execute("SELECT maal_rent_utfall(%s,%s)", (tenant, oid)).fetchone()
    _reset(m)
    m.commit()
    return bool(rad and rad[0])


def claims_av(m, tenant, rel, etter_ts) -> int:
    _tenantkontekst(m, tenant)
    n = m.execute("SELECT count(*) FROM oppdrag WHERE tenant=%s"
                  " AND claim_release_id=%s AND forste_claim_ts > %s",
                  (tenant, rel, etter_ts)).fetchone()[0]
    m.commit()
    return int(n)


def naa(m):
    t = m.execute("SELECT now()").fetchone()[0]
    m.commit()
    return t


# ------------------------------------------------------------------ main
def forbered(m, a, k):
    """--forbered: r2 (forgjengerkatalogen) og r3 (den drillede
    katalogen) inn i registeret, bytt til dem i rekkefølge."""
    modul = a.modul
    drillet, kver, khash, _dg = den_ene_claimende(m, modul)
    f_kat, d_kat = Path(a.forgjenger_katalog), Path(a.drillet_katalog)
    r2 = f"{k['prefiks']}-r2-{f_kat.name[:8]}"
    r3 = f"{k['prefiks']}-r3-{d_kat.name[:8]}"
    for rel, kat in ((r2, f_kat), (r3, d_kat)):
        dg = sveipmodul_digest(kat, modul)
        registrer_release(m, modul, rel, kver, khash,
                          manifest_hash_i(kat, modul), dg)
        bytt_release(m, modul, rel, kver, khash)
        _log(f"  registrert og byttet til {rel} (digest {dg[:12]}…, fra {kat})")
    _log(f"forberedt: {drillet} → {r2} → {r3}; drillen kan nå rulle {r3} → {r2}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modul", required=True, choices=sorted(MODULER))
    ap.add_argument("--forgjenger-katalog", required=True)
    ap.add_argument("--drillet-katalog", default="/opt/disponit/aktiv")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--forbered", action="store_true")
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    k = MODULER[a.modul]
    modul, unit = a.modul, k["unit"]
    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    rt_dsn = os.environ.get("DATABASE_URL")
    if not dsn or not rt_dsn:
        raise SystemExit("AVBRUTT: DISPONIT_MIGRATOR_URL/DATABASE_URL mangler")
    m = psycopg.connect(dsn)
    if a.forbered:
        forbered(m, a, k)
        return 0
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"{k['krav_id']}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    if ut.exists():
        raise SystemExit(f"AVBRUTT: {ut} finnes")
    ut.parent.mkdir(parents=True, exist_ok=True)

    # 0. preflight
    ta_reservasjonen(m, modul, dsn)
    drillet, kver, khash, drillet_digest = den_ene_claimende(m, modul)
    forgjenger, forgjenger_digest = forgjengeren(m, modul, drillet, kver, khash)
    f_kat = Path(a.forgjenger_katalog).resolve()
    d_kat = Path(a.drillet_katalog).resolve()
    if sveipmodul_digest(f_kat, modul) != forgjenger_digest:
        raise SystemExit(f"AVBRUTT: {f_kat} bærer ikke forgjengerens bytes"
                         f" ({forgjenger} {forgjenger_digest[:12]}…)")
    if sveipmodul_digest(d_kat, modul) != drillet_digest:
        raise SystemExit(f"AVBRUTT: {d_kat} bærer ikke den drillede releasens"
                         f" bytes ({drillet} {drillet_digest[:12]}…)")
    epoch = m.execute("SELECT module_epoch FROM modulhode WHERE modul_id=%s",
                      (modul,)).fetchone()[0]
    m.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    rb_id = f"{k['prefiks']}-drill-rb-{stamp}"
    kand_id = f"{k['prefiks']}-drill-k-{stamp}"
    _log(f"drillet {drillet} (digest {drillet_digest[:12]}…) ← forgjenger"
         f" {forgjenger} ({forgjenger_digest[:12]}…); rb={rb_id} k={kand_id}")

    # 1. tenanten og oppdragene
    tenant = a.tenant or f"t-{k['prefiks']}drill-{secrets.token_hex(3)}"
    rt = psycopg.connect(rt_dsn)
    sikre_policy(rt, tenant, k["bransje"])
    kropper = forbered_m14(rt, tenant, 4)
    rt.close()
    tok, tok_id = lag_token(tenant, k["rolle"])
    atexit.register(tilbakekall_token, tok_id)
    mh = manifest_hash_i(d_kat, modul)
    registrer_release(m, modul, rb_id, kver, khash, mh, forgjenger_digest)
    registrer_release(m, modul, kand_id, kver, khash, mh, drillet_digest)

    # probe: den levende arbeideren claimer for den drillede releasen.
    krev_reservasjonen("proben")
    o0 = bestill(tok, kropper[0], "probe")
    rel0, _ = vent_claimet(m, tenant, o0, OVERTAKELSESFRIST_S)
    st0 = vent_terminal(m, tenant, o0, OVERTAKELSESFRIST_S)
    if rel0 != drillet or st0 != "utfort":
        raise SystemExit(f"AVBRUTT: proben ble {st0} av {rel0!r}, ikke"
                         f" utført av {drillet}")
    _log(f"  probe {o0}: utført av {drillet}")

    # (b) inflight: claimet av den drillede — så rulles den.
    krev_reservasjonen("inflight")
    o1 = bestill(tok, kropper[1], "inflight")
    rel1, _ = vent_claimet(m, tenant, o1, OVERTAKELSESFRIST_S)
    if rel1 != drillet:
        raise SystemExit(f"AVBRUTT: inflight claimet av {rel1!r}")
    t_drain = naa(m)
    bytt_release(m, modul, rb_id, kver, khash)        # drillet → draining
    _log(f"  {drillet} drenert med {o1} underveis")
    st1 = vent_terminal(m, tenant, o1, OVERTAKELSESFRIST_S)
    kv1 = kvittering_ok(m, tenant, o1)
    falske = 0 if (st1 in ("utfort", "feilet") and kv1) else 1

    # (a) claim-stopp: nytt oppdrag, drenert release, levende arbeider.
    o2 = bestill(tok, kropper[2], "claimstopp")
    t_o2 = time.monotonic()
    time.sleep(CLAIMSTOPP_VENT_S)
    rad2 = status(m, tenant, o2)
    if rad2 and rad2[1]:
        raise SystemExit(f"AVBRUTT: {o2} ble claimet av {rad2[1]} etter"
                         " dreneringen — claim-porten fencer ikke")
    ventetid = time.monotonic() - t_o2
    etter_drenering = claims_av(m, tenant, drillet, t_drain)

    # (b2) rullbakken: forgjengerens bytes bootes og claimer.
    krev_reservasjonen("rullbakken")
    boot_fra(unit, f_kat, "rullbakken")
    rel2, rb_overtakelse = vent_claimet(m, tenant, o2, OVERTAKELSESFRIST_S)
    st2 = vent_terminal(m, tenant, o2, OVERTAKELSESFRIST_S)
    kv2 = kvittering_ok(m, tenant, o2)
    _log(f"  rullbakken: {o2} {st2} av {rel2!r} etter {rb_overtakelse:.1f} s")

    # (c) fram igjen: kandidaten (drillede bytes) — fencingen FØR bestillingen.
    krev_reservasjonen("kandidaten")
    bytt_release(m, modul, kand_id, kver, khash)      # rb → draining
    o3 = bestill(tok, kropper[3], "framigjen")
    boot_fra(unit, d_kat, "kandidaten")
    rel3, overtakelse = vent_claimet(m, tenant, o3, OVERTAKELSESFRIST_S)
    st3 = vent_terminal(m, tenant, o3, OVERTAKELSESFRIST_S)
    kv3 = kvittering_ok(m, tenant, o3)
    _log(f"  kandidaten: {o3} {st3} av {rel3!r} etter {overtakelse:.1f} s")

    # etterkontroll: kandidatens bytes ER aktiv-treet — overriden vekk.
    aktiv = Path("/opt/disponit/aktiv").resolve()
    fjern_override(unit)
    modulstatus = m.execute("SELECT status FROM modulhode WHERE modul_id=%s",
                            (modul,)).fetchone()[0]
    m.commit()
    bundet = (sveipmodul_digest(f_kat, modul) == forgjenger_digest
              and sveipmodul_digest(d_kat, modul) == drillet_digest
              and aktiv == d_kat)
    art = {
        "krav_id": k["krav_id"], "ts": datetime.now(timezone.utc).isoformat(),
        "bestatt": True,
        "oppsett": {"modul": modul, "miljo": MILJO, "vert": "disponit-srv",
                    "tenant": tenant, "drillet_release": drillet,
                    "rullback_release": rb_id, "kandidat_release": kand_id,
                    "forgjenger_release": forgjenger,
                    "drillet_digest": drillet_digest,
                    "kandidat_digest": drillet_digest,
                    "rullback_digest": forgjenger_digest,
                    "forgjenger_digest": forgjenger_digest,
                    "forgjenger_katalog": str(f_kat), "drillet_katalog": str(d_kat),
                    "module_epoch": int(epoch), "kontraktversjon": int(kver),
                    "kontrakt_hash": khash},
        "identiteter": {"inflight_oppdrag_id": str(o1),
                        "rullback_oppdrag_id": str(o2),
                        "kandidat_oppdrag_id": str(o3),
                        "probe_oppdrag_id": str(o0)},
        "maalt": {"inflight_oppdrag": 1, "inflight_utfall": st1 or "ukjent",
                  "inflight_har_signert_kvittering": kv1,
                  "falske_verdikter": falske,
                  "claims_etter_drenering": etter_drenering,
                  "ventetid_ubehandlet_s": round(ventetid, 1),
                  "rullback_claimet_oppdrag": 1 if (rel2 == rb_id and st2 == "utfort") else 0,
                  "rullback_har_signert_kvittering": kv2,
                  "rullback_promoterte": 0,
                  "rullback_overtakelse_s": round(rb_overtakelse, 1),
                  "kandidat_claimet_oppdrag": 1 if (rel3 == kand_id and st3 == "utfort") else 0,
                  "kandidat_har_signert_kvittering": kv3,
                  "overtakelse_s": round(overtakelse, 1),
                  "release_digest_bundet": bundet},
        "etterkontroll": {"drillet_livslop": livslop(m, modul, drillet),
                          "rullback_livslop": livslop(m, modul, rb_id),
                          "kandidat_livslop": livslop(m, modul, kand_id),
                          "modulstatus": modulstatus,
                          "digest_likhet": True,
                          "rullback_bytes_er_forgjengerens":
                              sveipmodul_digest(f_kat, modul) == forgjenger_digest,
                          "unit_override_fjernet": not _overridefil(unit).exists()},
    }
    formfeil = valider_artefaktformat(art, k["krav_id"])
    grensefeil = _sjekk_grenser(k["krav_id"], art)
    art["bestatt"] = not formfeil and not grensefeil
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True)
                  + "\n", encoding="utf-8")
    _log(f"skrev {ut} (bestatt={art['bestatt']})")
    for f in formfeil + grensefeil:
        _log(f"  RØDT: {f}")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
