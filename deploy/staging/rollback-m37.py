#!/usr/bin/env python3
"""Flippedrillen for M-37 (unntakskøens arbeider) — produserer `m37-rollback-v1`.

M-37 er ikke en sveipmodul: arbeideren (`python -m m37.arbeider`, unit
`disponit-m37`) har ingen `moduldeployment`-rad og intet modultoken. Den
claimer saker fra `unntak` med en LEASE (`claim_neste_sak`, migrasjon 005)
og skriver bare gjennom fencing-WHERE (claim_id OG generasjon OG status OG
levende lease). En rollback av arbeideren er derfor et spørsmål om leasen:

  (a) en sak som var CLAIMET da rullingen traff (lease levende), går
      hverken tapt eller dobbelt: den rullbakne arbeideren frigir den
      utløpte leasen (`frigi_utlopte_claims`), re-claimer med
      generasjon + 1 og fullfører den — nøyaktig én behandling;
  (b) det gamle claim-tokenet treffer null rader etter overtakelsen
      (fencingen holder, ikke bare i testene);
  (c) rullbakken KJØRER forgjengerens bytes (cwd i /proc, digest av
      katalogen), og kandidaten (de drillede bytene) overtar etterpå og
      claimer og fullfører sin egen sak.

Sakene lages slik de oppstår i drift: en M-23-tenant (bransjemalen
gjennom bootstrap-døra) får én fordring per sak, 5 døgn over forfall —
purreplanens trinn 1 er forfalt, planrunden bestiller som agent, og
policyens vilkår `forfall_passert_dager min 14` bryter → UNNTAK gjennom
`ved_brudd: unntakskø`. Ingen purring sendes; ingen e-post.

«Midt i leasen» ordnes deterministisk, uten å røre koden som måles:
arbeideren holdes med SIGSTOP mens saken lages, en ACCESS EXCLUSIVE-lås
på `policyer` tas (migratorrollen), arbeideren slippes med SIGCONT —
den claimer (commit, lease levende) og blokkerer på policylesingen
(`_aktiv_policy`) FØR sitt første skriv. Da treffer rullingen: unit-
override til forgjengerens katalog, `systemctl restart`. Låsen slippes
så snart den gamle prosessen er død. Låsvinduet måles og står i
artefaktet.

Release-byttet for ÉN unit er en systemd-override på `WorkingDirectory`
(unitten kjører `-m m37.arbeider` fra `<release>/platform/core`);
plattformen ellers står urørt. Overriden fjernes før artefaktet skrives.

BRUK (på verten som root, fra et utsjekk av grenen, `staging.env` sourcet,
`DISPONIT_M37_DSN` = innholdet i /etc/disponit/m37/DATABASE_URL):
    python deploy/staging/rollback-m37.py \\
        --forgjenger-katalog /opt/disponit/releases/<c1> --ut <artefakt>

Hemmeligheter (DSN-er) leses av miljøet og printes aldri.
"""
from __future__ import annotations

import argparse
import atexit
import importlib.util
import json
import os
import re
import secrets
import signal
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

from manifestskjema import (_sjekk_grenser, m37_digest,  # noqa: E402
                            valider_artefaktformat)

MILJO = "staging"
UNIT = "disponit-m37"
KRAV = "m37-rollback-v1"
AKTOR = "m37-drill"
OVERRIDE = Path(f"/etc/systemd/system/{UNIT}.service.d/drill.conf")
HEARTBEAT = Path("/run/disponit-m37/heartbeat")
#: `claim_neste_sak` klemmer leasen til [30, 600] s; arbeideren ber om 120.
OVERTAKELSESFRIST_S = 660.0
CLAIMFRIST_S = 30.0
LUKKEFRIST_S = 120.0

STOPPET_PID: int | None = None
LAAS: psycopg.Connection | None = None
DRILL_FULLFORT = False


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def _last_driver():
    """Sveipmodul-driveren bærer M-23-forberedelsen (bransjemal gjennom
    bootstrap-døra, purreplan, fordring, bestilling via planrunden)."""
    sti = REPO / "deploy/staging/rollback-sveipmodul.py"
    spec = importlib.util.spec_from_file_location("rollback_sveipmodul", sti)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ systemd/proc
def sh(*a, timeout=120) -> str:
    return subprocess.run(list(a), check=True, capture_output=True,
                          text=True, timeout=timeout).stdout.strip()


def mainpid() -> int:
    return int(sh("systemctl", "show", "-p", "MainPID", "--value", UNIT) or 0)


def unit_aktiv() -> bool:
    r = subprocess.run(["systemctl", "is-active", UNIT],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"


def lever(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def cwd_av(pid: int) -> str:
    """Arbeidskatalogen til prosessen. Type=simple: `systemctl restart`
    returnerer ved fork, FØR systemd har gjort chdir(WorkingDirectory) —
    et tidlig oppslag ser `/`. Vent til katalogen er satt."""
    for _ in range(80):
        cwd = os.readlink(f"/proc/{pid}/cwd")
        if cwd != "/":
            return cwd
        time.sleep(0.25)
    return os.readlink(f"/proc/{pid}/cwd")


def heartbeat_alder_s() -> float | None:
    try:
        hb = json.loads(HEARTBEAT.read_text())
        return time.time() - float(hb["ts"])
    except Exception:
        return None


def skriv_override(kat: Path):
    OVERRIDE.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDE.write_text("[Service]\n"
                        f"WorkingDirectory={kat}/platform/core\n")
    sh("systemctl", "daemon-reload")


def fjern_override():
    if OVERRIDE.exists():
        OVERRIDE.unlink()
    sh("systemctl", "daemon-reload")


def restart():
    sh("systemctl", "restart", UNIT, timeout=180)


def gjenopprett():
    """atexit: arbeideren skal ALLTID stå igjen på `aktiv` uten lås/stopp."""
    global STOPPET_PID, LAAS
    if STOPPET_PID and lever(STOPPET_PID):
        os.kill(STOPPET_PID, signal.SIGCONT)
        STOPPET_PID = None
    if LAAS is not None:
        try:
            LAAS.rollback(); LAAS.close()
        except Exception:
            pass
        LAAS = None
    if not DRILL_FULLFORT:
        _log("AVBRUTT — fjerner override og restarter arbeideren fra aktiv")
        try:
            fjern_override(); restart()
        except Exception as e:  # pragma: no cover
            _log(f"gjenoppretting feilet: {e}")


# ------------------------------------------------------------ databasen
def q(conn, tenant: str, sql: str, args=()):
    """Én transaksjon med RLS-kontekst (alle tabellene har FORCE RLS)."""
    conn.execute("SELECT set_config('disponit.tenant', %s, true),"
                 " set_config('disponit.aktor', %s, true),"
                 " set_config('disponit.request_id', %s, true)",
                 (tenant, AKTOR, "drill"))
    cur = conn.execute(sql, args)
    rader = cur.fetchall() if cur.description else []
    antall = cur.rowcount
    conn.commit()
    return rader, antall


def db_naa(conn):
    r = conn.execute("SELECT now()").fetchone()[0]
    conn.commit()
    return r


def sak_rad(rt, tenant, sid) -> dict:
    rader, _ = q(rt, tenant,
                 "SELECT status, claim_generation, claim_utloper, claim_id,"
                 " forsok, now() FROM unntak WHERE tenant=%s AND id=%s",
                 (tenant, sid))
    s, g, u, c, f, n = rader[0]
    return {"status": s, "gen": g, "utloper": u, "claim_id": c,
            "forsok": f, "naa": n}


def historikk(rt, tenant, sid):
    rader, _ = q(rt, tenant,
                 "SELECT hendelse, claim_id, claim_generation, aktor, ts"
                 " FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s"
                 " ORDER BY id", (tenant, sid))
    return rader


def ny_sak(D, rt, tenant, planunit: str, i: int) -> int:
    """Ny fordring (5 døgn over forfall) + fordringssveip + én planrunde
    -> det nyeste unntaket for tenanten (som i drift: sveipen lager
    `trinn_forfalt`-funnet, planrunden bestiller som agent, policyens
    vilkår bryter → UNNTAK)."""
    t0 = db_naa(rt)
    ny_fordring(D, rt, tenant, i)
    subprocess.run(["systemctl", "start", f"{planunit}.service"],
                   check=True, timeout=600)
    rader, _ = q(rt, tenant,
                 "SELECT id FROM unntak WHERE tenant=%s AND ts > %s"
                 " ORDER BY id DESC LIMIT 1", (tenant, t0))
    if not rader:
        raise SystemExit("AVBRUTT: planrunden ga ikke noe unntak")
    return int(rader[0][0])


def vent(pred, frist_s: float, hva: str, intervall=0.25) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < frist_s:
        if pred():
            return time.monotonic() - t0
        time.sleep(intervall)
    raise SystemExit(f"AVBRUTT: {hva} ikke innen {frist_s:g} s")


def vent_lukket(rt, tenant, sid, frist_s) -> dict:
    vent(lambda: sak_rad(rt, tenant, sid)["status"]
         not in ("ny", "under_behandling"), frist_s, f"sak {sid} lukket")
    return sak_rad(rt, tenant, sid)


FENCING = ("WHERE tenant=%s AND id=%s AND claim_id=%s"
           " AND claim_generation=%s AND status='under_behandling'"
           " AND claim_utloper > now()")


def fencing_treff(ar, tenant, sid, claim_id, gen) -> int:
    rader, _ = q(ar, tenant, "SELECT count(*) FROM unntak " + FENCING,
                 (tenant, sid, claim_id, gen))
    return int(rader[0][0])


def fencing_skriv(ar, tenant, sid, claim_id, gen) -> int:
    """Det gamle tokenets skriv — samme WHERE som `_fencing` i arbeideren.
    -> rader truffet. En no-op-SET så et treff (som ikke skal skje) ikke
    endrer saken."""
    _, antall = q(ar, tenant, "UPDATE unntak SET forsok = forsok " + FENCING,
                  (tenant, sid, claim_id, gen))
    return int(antall)


def blokkert_paa_policyer(rt) -> int:
    rader, _ = q(rt, "-", "SELECT count(*) FROM pg_locks WHERE"
                 " relation='policyer'::regclass AND NOT granted")
    return int(rader[0][0])


# ------------------------------------------------------------ sakene
#: Purringsutløseren bokfører ETT forsøk per (fordring, trinn), og neste
#: trinn er sist SENDTE + 1 — så én fordring gir nøyaktig én sak. Trinn 1
#: ved 3 døgn, fordringen 5 døgn over forfall: kandidat, men policyens
#: vilkår `forfall_passert_dager min 14` bryter → UNNTAK, ingen sending.
M37_PLAN = [
    {"navn": "Påminnelse", "dogn_etter_forfall": 3,
     "handling": "paaminnelse", "gebyr_ore": 0},
    {"navn": "Purring", "dogn_etter_forfall": 14,
     "handling": "purring", "gebyr_ore": 7000},
    {"navn": "Inkassovarsel", "dogn_etter_forfall": 28,
     "handling": "inkassovarsel", "gebyr_ore": 35000},
]


def forbered_tenant(D, rt, tenant: str, k: dict):
    """Bransjemalen gjennom bootstrap-døra, purreplanen og avsenderen —
    som `forbered_m23`, med drillens plan."""
    D.sikre_policy(rt, tenant, k["bransje"], k["fullmakter"])
    D._sk(rt, tenant)
    rt.execute("SELECT m23_sett_purreplan(%s,%s::jsonb,%s)",
               (tenant, json.dumps(M37_PLAN), D.AKTOR))
    rt.commit()
    D._sk(rt, tenant)
    rt.execute("SELECT m23_sett_avsender(%s,%s,%s,%s)",
               (tenant, "Drill AS", "post@disponit.com", D.AKTOR))
    rt.commit()


def ny_fordring(D, rt, tenant: str, i: int) -> str:
    """Én fordring 5 døgn over forfall med mottaker (eiers testadresse),
    så fordringssveipen som lager `trinn_forfalt`-funnet."""
    fid = uuid.uuid4(); nr = f"M37DRILL-{secrets.token_hex(3)}-{i}"
    D._sk(rt, tenant)
    rt.execute(
        "SELECT m23_registrer_fordring(%s,%s,%s,%s,%s,current_date - 35,"
        " current_date - 5,%s)",
        (tenant, fid, f"Drill Kunde {i}", nr, 51_000 + i, D.AKTOR))
    rt.commit()
    h, maske, ct, nonce, key_id = D._epostfelter(rt, tenant, b"m23:mottaker")
    D._sk(rt, tenant)
    rt.execute("SELECT m23_sett_mottaker(%s,%s,%s,%s,%s,%s,%s,%s)",
               (tenant, fid, maske, ct, nonce, key_id, h, D.AKTOR))
    rt.commit()
    subprocess.run(["systemctl", "start", "disponit-fordringssveip.service"],
                   check=True, timeout=600)
    return str(fid)


# ------------------------------------------------------------ drillen
def main() -> int:
    global STOPPET_PID, LAAS, DRILL_FULLFORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--forgjenger-katalog", required=True)
    ap.add_argument("--drillet-katalog", default="/opt/disponit/aktiv")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    rt_dsn = os.environ.get("DATABASE_URL")
    mig_dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    ar_dsn = os.environ.get("DISPONIT_M37_DSN")
    if not (rt_dsn and mig_dsn and ar_dsn):
        raise SystemExit("AVBRUTT: DATABASE_URL/DISPONIT_MIGRATOR_URL/"
                         "DISPONIT_M37_DSN mangler")
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"{KRAV}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    if ut.exists():
        raise SystemExit(f"AVBRUTT: {ut} finnes")
    ut.parent.mkdir(parents=True, exist_ok=True)

    # 0. preflight
    if not unit_aktiv():
        raise SystemExit(f"AVBRUTT: {UNIT} er ikke aktiv")
    if OVERRIDE.exists():
        raise SystemExit(f"AVBRUTT: {OVERRIDE} står igjen fra før")
    d_kat = Path(a.drillet_katalog).resolve()
    f_kat = Path(a.forgjenger_katalog).resolve()
    if d_kat == f_kat:
        raise SystemExit("AVBRUTT: forgjengeren er den drillede katalogen")
    for kat in (d_kat, f_kat):
        if not (kat / "platform/core/m37/arbeider.py").is_file():
            raise SystemExit(f"AVBRUTT: {kat} har ingen m37-arbeider")
    drillet_digest, forgjenger_digest = m37_digest(d_kat), m37_digest(f_kat)
    pid0 = mainpid()
    if cwd_av(pid0) != f"{d_kat}/platform/core":
        raise SystemExit(f"AVBRUTT: arbeideren kjører fra {cwd_av(pid0)},"
                         f" ikke {d_kat}")
    atexit.register(gjenopprett)
    _log(f"drillet {d_kat.name[:8]} ({drillet_digest[:12]}) ←"
         f" forgjenger {f_kat.name[:8]} ({forgjenger_digest[:12]}),"
         f" pid {pid0}")

    D = _last_driver()
    k = D.MODULER["m23_fordring"]
    rt = psycopg.connect(rt_dsn)
    m = psycopg.connect(mig_dsn)
    ar = psycopg.connect(ar_dsn)
    tenant = a.tenant or f"t-m37drill-{secrets.token_hex(3)}"
    if not re.fullmatch(r"t-m37drill-[0-9a-f]{6}", tenant):
        raise SystemExit(f"AVBRUTT: tenant {tenant!r} følger ikke drillens"
                         " navneform t-m37drill-<6 hex>")

    # 1. tenanten — sakene oppstår som i drift, uten at noe sendes
    har_policy, _ = q(rt, tenant, "SELECT 1 FROM policyer WHERE tenant=%s"
                      " LIMIT 1", (tenant,))
    if not har_policy:
        forbered_tenant(D, rt, tenant, k)
    _log(f"tenant {tenant}: bransjemal og purreplan på plass")

    # 2. probe: den drillede arbeideren behandler et unntak herfra
    probe = ny_sak(D, rt, tenant, k["planunit"], 1)
    p = vent_lukket(rt, tenant, probe, LUKKEFRIST_S)
    _log(f"probe {probe}: {p['status']} (generasjon {p['gen']})")

    # 3. inflight: claimet, lease levende, blokkert før første skriv
    os.kill(pid0, signal.SIGSTOP); STOPPET_PID = pid0
    inflight = ny_sak(D, rt, tenant, k["planunit"], 2)
    r0 = sak_rad(rt, tenant, inflight)
    if r0["status"] != "ny":
        raise SystemExit(f"AVBRUTT: inflight-saken er {r0['status']} før"
                         " arbeideren slapp")
    LAAS = m
    m.execute("LOCK TABLE policyer IN ACCESS EXCLUSIVE MODE")
    t_laas = time.monotonic()
    os.kill(pid0, signal.SIGCONT); STOPPET_PID = None
    vent(lambda: sak_rad(rt, tenant, inflight)["status"] == "under_behandling",
         CLAIMFRIST_S, "claim av inflight-saken")
    r1 = sak_rad(rt, tenant, inflight)
    time.sleep(1.5)
    blokkert = blokkert_paa_policyer(rt)
    r1b = sak_rad(rt, tenant, inflight)
    lease_rest = (r1b["utloper"] - r1b["naa"]).total_seconds()
    fencing_for = fencing_treff(ar, tenant, inflight, r1["claim_id"], r1["gen"])
    _log(f"inflight {inflight}: claimet (generasjon {r1['gen']}), lease"
         f" {lease_rest:.0f} s igjen, {blokkert} ventende lås på policyer,"
         f" fencing-predikatet treffer {fencing_for}")

    # 4. rullingen: forgjengerens bytes, den drillede prosessen dør midt i
    skriv_override(f_kat)
    t_kill = time.monotonic()
    restart()
    dod_innen = vent(lambda: not lever(pid0), 30.0, "den drillede prosessen død")
    pid1 = mainpid()
    m.rollback(); LAAS = None
    laasvindu = time.monotonic() - t_laas
    cwd1 = cwd_av(pid1)
    _log(f"rullbakk pid {pid1} fra {cwd1} (drillet død etter {dod_innen:.1f} s,"
         f" låsvindu {laasvindu:.1f} s)")

    # 5. overtakelsen: utløpt lease frigis, ny generasjon, én behandling
    vent(lambda: sak_rad(rt, tenant, inflight)["gen"] > r1["gen"],
         OVERTAKELSESFRIST_S - (time.monotonic() - t_kill),
         "overtakelse av inflight-saken", intervall=1.0)
    overtakelse_s = time.monotonic() - t_kill
    r2 = sak_rad(rt, tenant, inflight)
    fencing_etter = fencing_treff(ar, tenant, inflight, r1["claim_id"], r1["gen"])
    gammel_skriv = fencing_skriv(ar, tenant, inflight, r1["claim_id"], r1["gen"])
    r3 = vent_lukket(rt, tenant, inflight, LUKKEFRIST_S)
    h = historikk(rt, tenant, inflight)
    claims = [x for x in h if x[0] == "claim"]
    utlopt = [x for x in h if x[0] == "claim_utlopt"]
    behandlinger = [x for x in h if x[0] == "klassifisert"]
    under_gammel = [x for x in behandlinger if x[1] == r1["claim_id"]]
    rader, _ = q(rt, tenant, "SELECT count(*) FROM oppdrag WHERE tenant=%s"
                 " AND unntak_id=%s", (tenant, inflight))
    oppdrag_for = int(rader[0][0])
    _log(f"overtakelse etter {overtakelse_s:.0f} s: generasjon {r2['gen']},"
         f" utfall {r3['status']}, {len(claims)} claims, {len(utlopt)}"
         f" claim_utlopt, {len(behandlinger)} behandlinger, gammelt token"
         f" traff {gammel_skriv} rader")

    # 6. kandidaten: de drillede bytene tilbake, egen sak
    fjern_override()
    restart()
    pid2 = mainpid()
    cwd2 = cwd_av(pid2)
    kand = ny_sak(D, rt, tenant, k["planunit"], 3)
    rk = vent_lukket(rt, tenant, kand, LUKKEFRIST_S)
    _log(f"kandidat pid {pid2} fra {cwd2}: sak {kand} {rk['status']}"
         f" (generasjon {rk['gen']})")

    # 7. etterkontroll
    DRILL_FULLFORT = True
    vent(lambda: (heartbeat_alder_s() or 1e9) < 5.0, 30.0, "fersk heartbeat")
    hb = heartbeat_alder_s()
    pid_etter = mainpid()
    bundet = (m37_digest(d_kat) == drillet_digest
              and m37_digest(f_kat) == forgjenger_digest)
    art = {
        "krav_id": KRAV, "ts": datetime.now(timezone.utc).isoformat(),
        "bestatt": True,
        "oppsett": {
            "modul": "m37_unntak", "miljo": MILJO, "vert": os.uname().nodename,
            "unit": UNIT, "tenant": tenant,
            "drillet_release": d_kat.name, "forgjenger_release": f_kat.name,
            "drillet_katalog": str(d_kat), "forgjenger_katalog": str(f_kat),
            "drillet_digest": drillet_digest,
            "forgjenger_digest": forgjenger_digest,
            "kandidat_digest": drillet_digest,
            "lease_s": round((r1["utloper"] - r1["naa"]).total_seconds()
                             + 0.0, 1),
            "instrument": "SIGSTOP/SIGCONT på arbeideren mens saken lages;"
                          " ACCESS EXCLUSIVE på policyer til den drillede"
                          " prosessen er død",
            "sakskilde": "planrunde → vilkårsbrudd forfall_passert_dager"
                         " (purring.send, én fordring per sak)",
        },
        "identiteter": {
            "probe_sak_id": str(probe), "inflight_sak_id": str(inflight),
            "kandidat_sak_id": str(kand),
            "drillet_pid": pid0, "rullback_pid": pid1, "kandidat_pid": pid2,
            "inflight_claim_id_prefiks": (r1["claim_id"] or "")[:8],
        },
        "maalt": {
            "probe_utfall": p["status"], "probe_claim_generation": p["gen"],
            "inflight_status_ved_rulling": r1b["status"],
            "inflight_claim_generation_ved_rulling": r1["gen"],
            "inflight_lease_rest_s_ved_rulling": round(lease_rest, 1),
            "arbeider_ventet_paa_policyer": blokkert,
            "fencing_treff_for_rulling": fencing_for,
            "drillet_dod_innen_s": round(dod_innen, 2),
            "laasvindu_s": round(laasvindu, 2),
            "overtakelse_claim_generation": r2["gen"],
            "overtakelse_ventetid_s": round(overtakelse_s, 1),
            "claim_utlopt_hendelser": len(utlopt),
            "claims_totalt": len(claims),
            "fencing_treff_etter_overtakelse": fencing_etter,
            "gammel_claim_traff_rader": gammel_skriv,
            "inflight_utfall": r3["status"],
            "behandlinger_av_inflight": len(behandlinger),
            "behandlinger_under_gammel_claim": len(under_gammel),
            "oppdrag_for_inflight": oppdrag_for,
            "rullback_cwd_er_forgjengerens": cwd1 == f"{f_kat}/platform/core",
            "kandidat_cwd_er_aktiv": cwd2 == f"{d_kat}/platform/core",
            "kandidat_claim_generation": rk["gen"],
            "kandidat_utfall": rk["status"],
            "release_digest_bundet": bundet,
        },
        "etterkontroll": {
            "unit_aktiv": unit_aktiv(),
            "unit_override_fjernet": not OVERRIDE.exists(),
            "heartbeat_alder_s": round(hb, 2) if hb is not None else None,
            "uventede_restarter": 0 if pid_etter == pid2 else 1,
            "digest_likhet": drillet_digest == m37_digest(d_kat),
            "rullback_bytes_er_forgjengerens":
                cwd1 == f"{f_kat}/platform/core"
                and m37_digest(f_kat) == forgjenger_digest,
        },
    }
    feil = valider_artefaktformat(art, KRAV) + _sjekk_grenser(KRAV, art)
    art["bestatt"] = not feil
    if feil:
        art["feil"] = feil
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False) + "\n")
    _log(f"skrev {ut} (bestatt={art['bestatt']})")
    for f in feil:
        _log("  FEIL:", f)
    return 0 if not feil else 1


if __name__ == "__main__":
    raise SystemExit(main())
