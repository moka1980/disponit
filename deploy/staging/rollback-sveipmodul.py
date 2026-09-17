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
import urllib.error
import urllib.request
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
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

#: Per modul: arbeiderens unit, oppdragstypen drilloppdragene bærer, og
#: hvordan tenanten forberedes og ETT nytt kandidatoppdrag lages — så
#: planrunden (som `agent:<modul>`) bestiller nøyaktig ett per ledd.
#: `TESTMOTTAKER` er eiers testadresse (husregel: aldri oppdiktede) —
#: fire av modulene SENDER til mottakeren.
TESTMOTTAKER = "eliassi@gmail.com"
MODULER: dict[str, dict] = {
    "m14_fakturakontroll": {
        "modul": "m14_fakturakontroll",
        "unit": "disponit-m14", "prefiks": "m14",
        "oppdragstype": "faktura.bokfor", "bransje": "tjenestebedrift",
        "fullmakter": [],
        # Bransjemalen: `faktura.bokfor` er `tillatt_for: [agent]` —
        # bokføring bestilles av PLANRUNDEN (`plan.faktura`, som
        # `agent:faktura`), aldri av en kunde. Drillen går samme vei.
        "planunit": "disponit-plan", "krav_id": "m14-rollback-v1",
        # Fase 4 (m56-formen): modultokenet er bundet til (modul, miljø,
        # RELEASE) — hver boot re-onboardes gjennom den ekte HTTP-veien.
        "tokenfil": "/etc/disponit/m14/DISPONIT_MODULTOKEN",
        "gruppe": "disponit-m14",
        "forbered": "forbered_m14", "nytt": "nytt_m14",
    },
    "m26_prisbok": {
        "modul": "m26_prisbok",
        "unit": "disponit-m26", "prefiks": "m26",
        "oppdragstype": "tilbud.generer", "bransje": "tjenestebedrift",
        # Malens `tilbud.generer` tillater ikke persondata (kundens adresse
        # er persondata — målt 17/9: `dataklasse_ikke_tillatt`); utvidelsen
        # `tilbud-generer` erstatter handlingen med persondata tillatt.
        "fullmakter": ["tilbud-generer"],
        "planunit": "disponit-plan", "krav_id": "m26-rollback-v1",
        "tokenfil": "/etc/disponit/m26/DISPONIT_MODULTOKEN",
        "gruppe": "disponit-m26",
        "forbered": "forbered_m26", "nytt": "nytt_m26",
    },
    "m23_fordring": {
        "modul": "m23_fordring",
        "unit": "disponit-m23", "prefiks": "m23",
        "oppdragstype": "purring.send", "bransje": "tjenestebedrift",
        # `purring.send` står i bransjemalen; kandidaten krever et åpent
        # `trinn_forfalt`-funn, så fordringssveipen kjøres før planrunden.
        "fullmakter": [],
        "planunit": "disponit-plan", "krav_id": "m23-rollback-v1",
        "tokenfil": "/etc/disponit/m23/DISPONIT_MODULTOKEN",
        "gruppe": "disponit-m23",
        "forbered": "forbered_m23", "nytt": "nytt_m23",
        "sveipunit": "disponit-fordringssveip",
    },
    "m57_ats": {
        "modul": "m57_ats",
        "unit": "disponit-m57", "prefiks": "m57",
        "oppdragstype": "rekruttering.evaluering", "bransje": "tjenestebedrift",
        "fullmakter": [],
        # M-57 bestilles av KUNDEN (bestiller) gjennom API-et — inndata
        # reserveres, bunten lastes opp, bestillingen legges — ikke av
        # planrunden. Drillen går samme vei med et bootstrap-token for
        # tenanten, laget på verten og tilbakekalt etterpå.
        "bestill": "api", "planunit": None, "krav_id": "m57-rollback-v1",
        # Releasens digest er MODELLENS (registrer-m57-ats.py: «denne
        # modulens image ER modellen»), og M-31-porten i `bytt_release`
        # krever en bestått evalueringskjøring for nettopp den. Trebytene
        # bevitnes av unit-overriden (WorkingDirectory), ikke av digesten.
        "digest": "modell", "konfig": "/etc/disponit/m57/konfig",
        "digestnokkel": "DISPONIT_M57_MODELL_DIGEST",
        "tokenfil": "/etc/disponit/m57/DISPONIT_MODULTOKEN",
        "gruppe": "disponit-m57",
        "forbered": "forbered_m57", "nytt": "nytt_m57",
    },
    "m17_kundeservice": {
        "modul": "m17_kundeservice",
        "unit": "disponit-m17", "prefiks": "m17",
        "oppdragstype": "kundeservice.svar.send", "bransje": "tjenestebedrift",
        # `kundeservice.svar.send` er en utvidelse (`tillatt_for: [agent]`).
        "fullmakter": ["kundeservice-svar"],
        "planunit": "disponit-plan", "krav_id": "m17-rollback-v1",
        "tokenfil": "/etc/disponit/m17/DISPONIT_MODULTOKEN",
        "gruppe": "disponit-m17",
        "forbered": "forbered_m17", "nytt": "nytt_m17",
    },
    "m44_kampanje": {
        "modul": "m44_kampanje",
        "unit": "disponit-m44", "prefiks": "m44",
        "oppdragstype": "kampanje.send", "bransje": "tjenestebedrift",
        # `kampanje.send` er en utvidelse (`tillatt_for: [agent]`) —
        # velges som fullmakt ved registreringen, her gjennom samme
        # bootstrap-dør.
        "fullmakter": ["kampanje-send"],
        "planunit": "disponit-plan", "krav_id": "m44-rollback-v1",
        "tokenfil": "/etc/disponit/m44/DISPONIT_MODULTOKEN",
        "gruppe": "disponit-m44",
        "forbered": "forbered_m44", "nytt": "nytt_m44",
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


# ---------------------------------------------------------- onboarding
def _post_json(sti: str, kropp: dict, bearer: str | None = None) -> dict:
    req = urllib.request.Request(API + sti, data=json.dumps(kropp).encode(),
                                 method="POST")
    req.add_header("Content-Type", "application/json")
    if bearer:
        req.add_header("Authorization", "Bearer " + bearer)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_kropp": e.read().decode("utf-8", "replace")[:200]}


def onboard_for(k: dict, release: str) -> None:
    """Fase 4: arbeiderens modultoken for NØYAKTIG denne releasen —
    ops-token → engangshemmelighet → modultoken (HTTP, den ekte veien,
    som m14-oppsett.sh fase 5). Et token bundet til en drenert release
    fences ved claim-porten; det er selve mekanismen drillen måler, og
    derfor må hver boot bære sitt eget token."""
    ut = subprocess.run(
        [sys.executable, str(REPO / "deploy/staging/token-cli.py"), "opprett",
         "--tenant", "disponit", "--rolle", "drift", "--scope",
         "modules:onboard", "--bootstrap"],
        capture_output=True, text=True, timeout=120)
    m = re.search(r"^\s*(tk_[A-Za-z0-9_-]+\.[^\s]+)\s*$", ut.stdout, re.M)
    if not m:
        raise SystemExit("AVBRUTT: fikk ikke drift-token for onboardingen")
    drift = m.group(1); drift_id = drift.split(".", 1)[0]
    try:
        sv = _post_json("/v1/modul/onboarding",
                        {"modul_id": k["modul"], "miljo": MILJO,
                         "release_id": release}, bearer=drift)
        hem = sv.get("hemmelighet")
        if not hem:
            raise SystemExit(f"AVBRUTT: onboarding for {release} avvist:"
                             f" {json.dumps(sv)[:200]}")
        sv2 = _post_json("/v1/modul/onboarding/innlos", {"hemmelighet": hem})
        tok = sv2.get("token")
        if not tok:
            raise SystemExit(f"AVBRUTT: innløsning for {release} avvist:"
                             f" {json.dumps(sv2)[:200]}")
        fil = Path(k["tokenfil"])
        tmp = fil.with_name(fil.name + ".ny")
        tmp.write_text(tok, encoding="utf-8")
        subprocess.run(["chown", f"root:{k['gruppe']}", str(tmp)], check=True)
        tmp.chmod(0o640)
        tmp.replace(fil)
        _log(f"  onboardet {k['unit']} for {release}")
    finally:
        subprocess.run([sys.executable, str(REPO / "deploy/staging/token-cli.py"),
                        "deaktiver", drift_id], capture_output=True, timeout=120)


# ----------------------------------------------------------- unit-override
def _overridefil(unit: str) -> Path:
    return Path(f"/etc/systemd/system/{unit}.service.d/drill.conf")


def boot_fra(k: dict, unit: str, katalog: Path | None, release: str,
             hva: str) -> float:
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
    onboard_for(k, release)
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


DRILL_FULLFORT = False


def gjenopprett_arbeideren(k: dict, dsn: str) -> None:
    """Ved AVBRUDD: overriden vekk og arbeideren onboardet for den releasen
    som står claiming — ellers står prod fencet til noen gjør det for hånd
    (målt 17/9). Ved normal slutt har drillen selv gjort det."""
    if DRILL_FULLFORT:
        return
    try:
        fjern_override(k["unit"])
        with psycopg.connect(dsn) as m:
            rad = m.execute(
                "SELECT release_id FROM moduldeployment WHERE modul_id=%s"
                " AND miljo=%s AND livslop='claiming'",
                (k["modul"], MILJO)).fetchone()
        if rad:
            onboard_for(k, rad[0])
            subprocess.run(["systemctl", "restart", f"{k['unit']}.service"],
                           timeout=120)
            _log(f"  gjenopprettet: {k['unit']} onboardet for {rad[0]}")
    except Exception as e:                                  # noqa: BLE001
        _log(f"  ADVARSEL: gjenopprettingen feilet ({e}) — onboard arbeideren"
             " for den claimende releasen for hånd")


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


def forbered_m14(rt, tenant: str) -> dict:
    """Terskler, sats og kjent leverandør (M-24) — så hver faktura
    `m14_for_bokforing` ser er ren."""
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
    return {"lev": lev}


def nytt_m14(rt, tenant: str, ctx: dict, i: int) -> str:
    """ÉN faktura, registrert og avgjort `kontrollert` — akkurat nå, så
    neste planrunde finner nøyaktig én kandidat i tenanten."""
    fid = uuid.uuid4(); nr = f"DRILL-{secrets.token_hex(3)}-{i}"
    netto = 10_000 + i; mva = (netto * 250 + 500) // 1000
    _sk(rt, tenant)
    rt.execute(
        "SELECT m14_registrer_faktura(%s,%s,%s,%s,%s,%s,%s,'hoy','NOK',"
        " current_date - 10, current_date + 20, current_date - 1, %s)",
        (tenant, fid, ctx["lev"], nr, netto, mva, netto + mva, AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m14_avgjor_faktura(%s,%s,'kontrollert',%s,%s)",
               (tenant, fid, "Drill: kontrollert uten avvik.", AKTOR))
    rt.commit()
    return str(fid)


def forbered_m44(rt, tenant: str) -> dict:
    """Grense og avsender. Mottakeren lages PER oppdrag: utvidelsen
    `kampanje-send` har `frekvens.maks: 2` per mottaker per 30 dager, og
    drillen bestiller fire — én mottaker hver, alle med eiers testadresse."""
    _sk(rt, tenant)
    rt.execute("SELECT m44_sett_grense(%s,10,7,730,%s)", (tenant, AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m44_sett_avsender(%s,%s,%s,%s)",
               (tenant, "Drill AS", "post@disponit.com", AKTOR))
    rt.commit()
    return {}


def nytt_m44(rt, tenant: str, ctx: dict, i: int) -> str:
    """ÉN mottaker (kryptert kontakt, gyldig samtykke) og ÉN kampanje med
    innhold, planlagt i dag, med mottakeren i planen — nøyaktig én
    kandidat for `m44_kampanjekandidater`, innenfor frekvensgrensen."""
    from api.kampanje import _kontakt_kryptert
    mid = uuid.uuid4()
    _sk(rt, tenant)
    rt.execute("SELECT m44_registrer_mottaker(%s,%s,%s,%s,%s,%s)",
               (tenant, mid, f"M-drill-{secrets.token_hex(3)}-{i}",
                f"Drill Mottaker {i}", TESTMOTTAKER, AKTOR))
    rt.commit()
    _sk(rt, tenant)
    ct, nonce, key_id = _kontakt_kryptert(rt, tenant, TESTMOTTAKER)
    rt.execute("SELECT m44_sett_kontakt(%s,%s,%s,%s,%s,%s)",
               (tenant, mid, ct, nonce, key_id, AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute(
        "SELECT m44_registrer_samtykke(%s,%s,%s,'gitt','preferanseside',"
        "       %s,'nyhetsbrev',current_date - 1,'drill',%s)",
        (tenant, uuid.uuid4(), mid, f"s-drill-{secrets.token_hex(3)}", AKTOR))
    rt.commit()
    kid = uuid.uuid4(); kode = f"K-drill-{secrets.token_hex(3)}-{i}"
    _sk(rt, tenant)
    rt.execute(
        "SELECT m44_registrer_kampanje(%s,%s,%s,%s,'salg',%s,current_date,"
        "       %s,%s,%s)",
        (tenant, kid, kode, f"Drill kampanje {i}",
         "https://disponit.com/avmeld", f"Disponit flippedrill {i}",
         "Dette er en teknisk prøvesending fra flippedrillen. Ingen handling"
         " kreves.", AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m44_legg_i_plan(%s,%s,%s,%s)", (tenant, kid, mid, AKTOR))
    rt.commit()
    return str(kid)


def _epostfelter(rt, tenant: str, aad: bytes):
    """(hash, maske, ct, nonce, key_id) for eiers testadresse — slik
    API-ene gjør det (tilbud/fordring): sha256 i små bokstaver, maske
    `x****@…`, kryptert under tenantens DEK med modulens AAD."""
    import hashlib
    from db import kryptering
    e = TESTMOTTAKER.strip().lower()
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(rt, tenant)
    ct, nonce = kryptering.krypter(dek, {"e": e}, tenant, key_id, ekstra_aad=aad)
    maske = e[0] + "****" + e[e.index("@"):]
    return hashlib.sha256(e.encode("utf-8")).hexdigest(), maske, ct, nonce, key_id


def forbered_m26(rt, tenant: str) -> dict:
    """Prisbok med terskler, ett produkt med gjeldende pris, én
    standardklausul og avsenderprofilen tilbudet sendes i."""
    _sk(rt, tenant)
    rt.execute("SELECT m26_sett_terskler(%s,100,30,7,%s)", (tenant, AKTOR))
    rt.commit()
    pid = uuid.uuid4()
    _sk(rt, tenant)
    rt.execute("SELECT m26_registrer_produkt(%s,%s,%s,%s,'time',%s)",
               (tenant, pid, f"P-drill-{secrets.token_hex(3)}", "Drilltime", AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m26_sett_pris(%s,%s,%s,'NOK',current_date - 30,%s,%s)",
               (tenant, pid, 120_000, "drill", AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m26_sett_klausul(%s,%s,%s,%s,true,current_date,%s)",
               (tenant, "DRILL-LEV", "Levering",
                "Levering skjer innen 30 dager.", AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m26_sett_avsenderprofil(%s,%s,%s,%s,%s)",
               (tenant, "Drill AS", "post@disponit.com",
                "Vennlig hilsen Drill AS", AKTOR))
    rt.commit()
    return {"pid": pid}


def nytt_m26(rt, tenant: str, ctx: dict, i: int) -> str:
    """ÉN godkjent tilbud med én linje fra boka, gyldig 30 dager, kunden
    = eiers testadresse — nøyaktig én kandidat for `m26_tilbudskandidater`."""
    h, maske, ct, nonce, key_id = _epostfelter(rt, tenant, b"m26:kunde")
    tid = uuid.uuid4()
    _sk(rt, tenant)
    rt.execute(
        "SELECT * FROM m26_lag_tilbud(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "current_date,current_date + 30,%s,%s::jsonb,%s)",
        (tenant, tid, f"Drill Kunde {i}", None, h, maske, ct, nonce, key_id,
         "Teknisk prøvetilbud fra flippedrillen.",
         json.dumps([{"produkt_id": str(ctx["pid"]), "antall": 1,
                      "enhetspris_ore": None}]), AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m26_avgjor_tilbud(%s,%s,'godkjent',%s)",
               (tenant, tid, AKTOR))
    rt.commit()
    return str(tid)


def forbered_m17(rt, tenant: str) -> dict:
    """Ingenting utover policyen: henvendelsen bærer sin egen avsender."""
    return {}


def nytt_m17(rt, tenant: str, ctx: dict, i: int) -> str:
    """ÉN henvendelse fra eiers testadresse (m17-fasitens dør
    `m17_ta_imot`), klassifisert `svar_kreves`, med et utkast som et
    menneske GODKJENNER — nøyaktig én kandidat for `m17_svarkandidater`."""
    from api.kundeservice import (_AAD_AVSENDER, _avsenderhash,
                                  _avsendermaske, _krypter)
    from db import kryptering
    _sk(rt, tenant)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(rt, tenant)
    rt.commit()
    hid = uuid.uuid4()
    e_ct, e_n = _krypter(dek, key_id, tenant, f"Drill henvendelse {i}")
    k_ct, k_n = _krypter(dek, key_id, tenant,
                         "Teknisk prøve fra flippedrillen. Ingen handling kreves.")
    a_ct, a_n = _krypter(dek, key_id, tenant, TESTMOTTAKER, aad=_AAD_AVSENDER)
    _sk(rt, tenant)
    rt.execute(
        "SELECT * FROM m17_ta_imot(%s,%s,'epost',%s,now() - interval '1 day',"
        "       %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, hid, f"H-drill-{secrets.token_hex(3)}-{i}",
         _avsenderhash(TESTMOTTAKER), e_ct, e_n, k_ct, k_n, key_id, AKTOR,
         _avsendermaske(TESTMOTTAKER), a_ct, a_n))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m17_klassifiser(%s,%s,'normal','teknisk','svar_kreves',"
               "'menneske',NULL,%s)", (tenant, hid, AKTOR))
    rt.commit()
    uid = uuid.uuid4()
    u_ct, u_n = _krypter(dek, key_id, tenant,
                         "Hei! Dette er et teknisk prøvesvar fra flippedrillen.")
    _sk(rt, tenant)
    rt.execute(
        "SELECT m17_lagre_utkast(%s,%s,%s,%s,%s,%s,%s::text[],'menneske',NULL,%s)",
        (tenant, uid, hid, u_ct, u_n, key_id, [], AKTOR))
    rt.execute("SELECT m17_avgjor_utkast(%s,%s,'godkjent',%s)",
               (tenant, uid, AKTOR))
    rt.commit()
    return str(hid)


M23_PLAN = [
    {"navn": "Påminnelse", "dogn_etter_forfall": 3,
     "handling": "paaminnelse", "gebyr_ore": 0},
    {"navn": "Purring", "dogn_etter_forfall": 14,
     "handling": "purring", "gebyr_ore": 7000},
    {"navn": "Inkassovarsel", "dogn_etter_forfall": 28,
     "handling": "inkassovarsel", "gebyr_ore": 35000},
]


def forbered_m23(rt, tenant: str) -> dict:
    """Purreplanen (m23-fasitens tre trinn) og avsenderen."""
    _sk(rt, tenant)
    rt.execute("SELECT m23_sett_purreplan(%s,%s::jsonb,%s)",
               (tenant, json.dumps(M23_PLAN), AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m23_sett_avsender(%s,%s,%s,%s)",
               (tenant, "Drill AS", "post@disponit.com", AKTOR))
    rt.commit()
    return {}


def nytt_m23(rt, tenant: str, ctx: dict, i: int) -> str:
    """ÉN fordring 20 døgn over forfall (policyens vilkår
    `forfall_passert_dager min: 14`) med mottaker (eiers testadresse), så
    fordringssveipen som lager `trinn_forfalt`-funnet — nøyaktig én
    kandidat for `m23_purringskandidater` (trinn 1, påminnelse)."""
    fid = uuid.uuid4(); nr = f"DRILL-{secrets.token_hex(3)}-{i}"
    _sk(rt, tenant)
    rt.execute(
        "SELECT m23_registrer_fordring(%s,%s,%s,%s,%s,current_date - 50,"
        " current_date - 20,%s)",
        (tenant, fid, f"Drill Kunde {i}", nr, 50_000 + i, AKTOR))
    rt.commit()
    h, maske, ct, nonce, key_id = _epostfelter(rt, tenant, b"m23:mottaker")
    _sk(rt, tenant)
    rt.execute("SELECT m23_sett_mottaker(%s,%s,%s,%s,%s,%s,%s,%s)",
               (tenant, fid, maske, ct, nonce, key_id, h, AKTOR))
    rt.commit()
    subprocess.run(["systemctl", "start", "disponit-fordringssveip.service"],
                   check=True, timeout=600)
    return str(fid)


BESTILLERTOKEN: dict[str, str] = {}


def _bestillertoken(tenant: str) -> str:
    """Bootstrap-token for tenanten (rolle bestiller, scope
    bestilling:opprett) — laget på verten med token-cli, holdt i minne,
    aldri printet, tilbakekalt ved avslutning."""
    if tenant in BESTILLERTOKEN:
        return BESTILLERTOKEN[tenant]
    r = subprocess.run(
        [sys.executable, str(REPO / "deploy/staging/token-cli.py"), "opprett",
         "--tenant", tenant, "--rolle", "bestiller",
         "--scope", "bestilling:opprett", "--bootstrap"],
        capture_output=True, text=True, timeout=120)
    treff = re.search(r"\b([A-Za-z0-9_-]+\.[A-Za-z0-9_-]{20,})\b", r.stdout)
    if not treff:
        raise SystemExit("AVBRUTT: fikk ikke bestillertoken for tenanten")
    tok = treff.group(1)
    BESTILLERTOKEN[tenant] = tok
    tid = tok.split(".", 1)[0]

    def tilbakekall():
        subprocess.run([sys.executable, str(REPO / "deploy/staging/token-cli.py"),
                        "deaktiver", tid], capture_output=True, timeout=60)
    atexit.register(tilbakekall)
    return tok


def _api(metode: str, sti: str, tok: str, data: bytes | None,
         ctype: str = "application/json") -> tuple[int, dict | str]:
    req = urllib.request.Request(API + sti, data=data, method=metode)
    req.add_header("Authorization", "Bearer " + tok)
    req.add_header("Idempotency-Key", "drill-" + secrets.token_hex(8))
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]


def forbered_m57(rt, tenant: str) -> dict:
    """Stillingsprofilen tenanten alt har (fasit-tenanten) — drillen lager
    ingen; en tenant uten profil kan ikke bestille."""
    _sk(rt, tenant)
    rad = rt.execute(
        "SELECT profil_id, versjon FROM stillingsprofil WHERE tenant=%s"
        " ORDER BY opprettet DESC LIMIT 1", (tenant,)).fetchone()
    rt.rollback()
    if rad is None:
        raise SystemExit(f"AVBRUTT: {tenant} har ingen stillingsprofil")
    golden = json.loads((REPO / "deploy/staging/m57-golden-v2.json")
                        .read_text(encoding="utf-8"))
    return {"profil": f"{rad[0]}@{rad[1]}", "golden": golden}


def nytt_m57(rt, tenant: str, ctx: dict, i: int) -> str:
    """Én liten bunt (to søknader fra golden v2) reservert, lastet opp og
    bestilt som bestiller — nøyaktig kundens vei. -> oppdrag_id (str)."""
    import html
    import io
    import zipfile
    tok = _bestillertoken(tenant)
    buf = io.BytesIO(); soknader = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for j in range(2):
            g = ctx["golden"][(2 * i + j) % len(ctx["golden"])]
            kid = f"drill-{i}-{j}"
            z.writestr(f"{kid}/soknad.html", "<html><body><p>"
                       + html.escape(g["tekst"]).replace("\n", "<br>")
                       + "</p></body></html>")
            soknader.append({"kandidat_id": kid, "filer": [f"{kid}/soknad.html"],
                             "felter": {"navn": [f"Drill {i}-{j}"]}})
        z.writestr("soknader.json", json.dumps({"soknader": soknader}))
    st, sv = _api("POST", "/v1/inndata/reserver", tok, json.dumps(
        {"eiermodul": "m57_ats", "formaal": "soknadsbunt"}).encode())
    if st != 201:
        raise SystemExit(f"AVBRUTT: inndata/reserver {st} {sv}")
    jti, ref = sv["reservasjon_jti"], sv["inndata_ref"]
    st, sv = _api("PUT", f"/v1/inndata/opplast/{jti}", tok, buf.getvalue(),
                  "application/zip")
    if st != 201:
        raise SystemExit(f"AVBRUTT: inndata/opplast {st} {sv}")
    st, sv = _api("POST", "/v1/bestilling", tok, json.dumps(
        {"bestillingstype": "rekruttering.evaluering", "inndata_ref": ref,
         "stillingsprofil_ref": ctx["profil"], "antall_soknader": len(soknader),
         "omfang": "bunt"}).encode())
    if st != 200 or not isinstance(sv, dict) or sv.get("beslutning") != "tillat":
        raise SystemExit(f"AVBRUTT: bestilling {st} {sv}")
    return str(sv["oppdrag_id"])


def bestill_via_planen(m, rt, k: dict, tenant: str, ctx: dict, i: int,
                       merkelapp: str) -> int:
    """Lager ETT kandidatobjekt og lar PLANRUNDEN bestille (som
    `agent:<modul>`, samme bestillingsvei som i drift). -> oppdrag_id."""
    t0 = naa(m)
    ref = globals()[k["nytt"]](rt, tenant, ctx, i)
    if k.get("bestill") == "api":
        _log(f"  {merkelapp}: oppdrag {ref} bestilt gjennom API-et")
        return int(ref)
    subprocess.run(["systemctl", "start", f"{k['planunit']}.service"],
                   check=True, timeout=600)
    _tenantkontekst(m, tenant)
    rad = m.execute(
        "SELECT id FROM oppdrag WHERE tenant=%s AND oppdragstype=%s"
        " AND opprettet > %s ORDER BY id DESC LIMIT 1",
        (tenant, k["oppdragstype"], t0)).fetchone()
    m.commit()
    if rad is None:
        raise SystemExit(f"AVBRUTT: planrunden bestilte ikke ({merkelapp},"
                         f" {k['oppdragstype']} for {ref})")
    _log(f"  {merkelapp}: oppdrag {rad[0]} bestilt av planrunden")
    return int(rad[0])


def sikre_policy(rt, tenant: str, bransje: str, fullmakter: list):
    """Bransjemalen inn gjennom bootstrap-døra — som registreringen gjør."""
    from db import kryptering
    from api.firmaregistrering import _aktiver_bransjemal
    _sk(rt, tenant)
    # Bransjemalen skrives som `utkast` — en tenant med EN policyrad er
    # forberedt, og bootstrap-døra nekter (med rette) en gang til.
    finnes = rt.execute(
        "SELECT 1 FROM policyer WHERE tenant=%s LIMIT 1",
        (tenant,)).fetchone()
    if finnes:
        rt.rollback(); return
    kryptering.hent_eller_opprett_aktiv_dek(rt, tenant)
    _aktiver_bransjemal(rt, tenant, bransje, list(fullmakter))
    rt.commit()


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
def digest_for(k: dict, kat: Path) -> str:
    """Releasens digest for katalogen: treets bytes (sveipmodulene), eller
    modellens fra arbeiderens konfig (M-57) — samme verdi for hver release,
    som i registeret."""
    if k.get("digest") != "modell":
        return sveipmodul_digest(kat, k["modul"])
    for linje in Path(k["konfig"]).read_text(encoding="utf-8").splitlines():
        if linje.startswith(k["digestnokkel"] + "="):
            return linje.split("=", 1)[1].strip().strip("'\"").removeprefix("sha256:")
    raise SystemExit(f"AVBRUTT: {k['digestnokkel']} mangler i {k['konfig']}")


def forbered(m, a, k):
    """--forbered: r2 (forgjengerkatalogen) og r3 (den drillede
    katalogen) inn i registeret, bytt til dem i rekkefølge."""
    modul = a.modul
    drillet, kver, khash, _dg = den_ene_claimende(m, modul)
    f_kat, d_kat = Path(a.forgjenger_katalog), Path(a.drillet_katalog)
    r2 = f"{k['prefiks']}-r2{a.release_suffiks}-{f_kat.name[:8]}"
    r3 = f"{k['prefiks']}-r3{a.release_suffiks}-{d_kat.name[:8]}"
    for rel, kat in ((r2, f_kat), (r3, d_kat)):
        dg = digest_for(k, kat)
        registrer_release(m, modul, rel, kver, khash,
                          manifest_hash_i(kat, modul), dg)
        bytt_release(m, modul, rel, kver, khash)
        _log(f"  registrert og byttet til {rel} (digest {dg[:12]}…, fra {kat})")
    # Arbeiderens token er bundet til release: uten dette står den fencet
    # mot claim-porten fra nå (målt 17/9 på m14 og m44).
    onboard_for(k, r3)
    subprocess.run(["systemctl", "restart", f"{k['unit']}.service"], check=True,
                   timeout=120)
    _log(f"forberedt: {drillet} → {r2} → {r3}; arbeideren onboardet for {r3};"
         f" drillen kan nå rulle {r3} → {r2}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modul", required=True, choices=sorted(MODULER))
    ap.add_argument("--forgjenger-katalog", required=True)
    ap.add_argument("--drillet-katalog", default="/opt/disponit/aktiv")
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--forbered", action="store_true")
    ap.add_argument("--release-suffiks", default="",
                    help="skiller et nytt r2/r3-par fra et tidligere (radene er immutable)")
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
    atexit.register(gjenopprett_arbeideren, k, dsn)
    drillet, kver, khash, drillet_digest = den_ene_claimende(m, modul)
    forgjenger, forgjenger_digest = forgjengeren(m, modul, drillet, kver, khash)
    f_kat = Path(a.forgjenger_katalog).resolve()
    d_kat = Path(a.drillet_katalog).resolve()
    if digest_for(k, f_kat) != forgjenger_digest:
        raise SystemExit(f"AVBRUTT: {f_kat} bærer ikke forgjengerens bytes"
                         f" ({forgjenger} {forgjenger_digest[:12]}…)")
    if digest_for(k, d_kat) != drillet_digest:
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
    sikre_policy(rt, tenant, k["bransje"], k["fullmakter"])
    ctx = globals()[k["forbered"]](rt, tenant)
    mh = manifest_hash_i(d_kat, modul)
    registrer_release(m, modul, rb_id, kver, khash, mh, forgjenger_digest)
    registrer_release(m, modul, kand_id, kver, khash, mh, drillet_digest)

    # probe: den levende arbeideren claimer for den drillede releasen.
    krev_reservasjonen("proben")
    o0 = bestill_via_planen(m, rt, k, tenant, ctx, 0, "probe")
    rel0, _ = vent_claimet(m, tenant, o0, OVERTAKELSESFRIST_S)
    st0 = vent_terminal(m, tenant, o0, OVERTAKELSESFRIST_S)
    if rel0 != drillet or st0 != "utfort":
        raise SystemExit(f"AVBRUTT: proben ble {st0} av {rel0!r}, ikke"
                         f" utført av {drillet}")
    _log(f"  probe {o0}: utført av {drillet}")

    # (b) inflight: claimet av den drillede — så rulles den.
    krev_reservasjonen("inflight")
    o1 = bestill_via_planen(m, rt, k, tenant, ctx, 1, "inflight")
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
    o2 = bestill_via_planen(m, rt, k, tenant, ctx, 2, "claimstopp")
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
    boot_fra(k, unit, f_kat, rb_id, "rullbakken")
    rel2, rb_overtakelse = vent_claimet(m, tenant, o2, OVERTAKELSESFRIST_S)
    st2 = vent_terminal(m, tenant, o2, OVERTAKELSESFRIST_S)
    kv2 = kvittering_ok(m, tenant, o2)
    _log(f"  rullbakken: {o2} {st2} av {rel2!r} etter {rb_overtakelse:.1f} s")

    # (c) fram igjen: kandidaten (drillede bytes) — fencingen FØR bestillingen.
    krev_reservasjonen("kandidaten")
    bytt_release(m, modul, kand_id, kver, khash)      # rb → draining
    o3 = bestill_via_planen(m, rt, k, tenant, ctx, 3, "framigjen")
    boot_fra(k, unit, d_kat, kand_id, "kandidaten")
    rel3, overtakelse = vent_claimet(m, tenant, o3, OVERTAKELSESFRIST_S)
    st3 = vent_terminal(m, tenant, o3, OVERTAKELSESFRIST_S)
    kv3 = kvittering_ok(m, tenant, o3)
    _log(f"  kandidaten: {o3} {st3} av {rel3!r} etter {overtakelse:.1f} s")

    # etterkontroll: kandidatens bytes ER aktiv-treet — overriden vekk.
    aktiv = Path("/opt/disponit/aktiv").resolve()
    fjern_override(unit)
    rt.close()
    modulstatus = m.execute("SELECT status FROM modulhode WHERE modul_id=%s",
                            (modul,)).fetchone()[0]
    m.commit()
    bundet = (digest_for(k, f_kat) == forgjenger_digest
              and digest_for(k, d_kat) == drillet_digest
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
                              digest_for(k, f_kat) == forgjenger_digest,
                          "unit_override_fjernet": not _overridefil(unit).exists()},
    }
    global DRILL_FULLFORT
    DRILL_FULLFORT = True
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
