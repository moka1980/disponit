#!/usr/bin/env python3
"""Flippedrillen for en SVEIPMODUL UTEN EGEN RELEASE — kjerneformen.

En sveip som rullerer med kjernen har ingen egen release å bytte: en
rollback av modulen ER en rollback av kjernebytene sveipen består av,
`/opt/disponit/aktiv` pekt på forgjengerens katalog. Det som må holde er
REGISTERETS tilstand over rullingen, i tre steg:

  (a) AVBRUTT: en sveip på de drillede bytene drepes MIDT I skrivingen —
      registeret står urørt (ingen halve funn), og arbeidernøkkelen
      slippes, så sveipen ikke er stengt ute av sin egen døde sesjon.
      Å drepe KLIENTEN er ikke nok, og drillen måler nettopp det: en
      backend som står og venter på lås merker ikke at klienten er borte,
      og ville fullført og committet sveipen så snart låsen slapp. Økten
      avsluttes derfor også på serversiden — som når en release rulles og
      tjenestens tilkoblinger forsvinner;
  (b) RULLBAKK: forgjengerens bytes fullfører den samme sveipen og
      skriver funnene — hvert subjekt nøyaktig én gang;
  (c) KANDIDAT: de drillede bytene kjører igjen og finner INGENTING nytt
      — de godtar det forgjengeren skrev.

AVBRUDDET KOMMER UTENFRA, som en ekte driftshendelse: en annen
tilkobling tar `ACCESS EXCLUSIVE` på funntabellen, sveipen blokkerer på
den, og prosessen drepes med SIGKILL mens transaksjonen står åpen.
Ingen bryter inne i modulen — en drill som trengte det, ville målt
bryteren.

Hver kjøring er en UNDERPROSESS med `PYTHONPATH` på release-katalogen,
og runneren bevitner hvilken fil sveipmodulen faktisk ble lastet fra.

BRUK (på verten som root, `staging.env` sourcet):
    python deploy/staging/rollback-sveipkjerne.py --modul m19_adresse \\
        --forgjenger-katalog /opt/disponit/releases/<c1> [--ut …]

Hemmeligheter (DSN-er) leses av miljøet og printes aldri.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg  # noqa: E402

from manifestskjema import (SVEIPMODULER, _sjekk_grenser,  # noqa: E402
                            kjerne_digest, sveip_rollback_bevisrot_sha256,
                            sveipkjerne_digest, valider_artefaktformat)

#: Hvor lenge vi venter på at sveipen faktisk BLOKKERER på tabellåsen.
#: Dreper vi før den har nådd skrivingen, måler drillen en prosess som
#: aldri rakk å gjøre noe — og det ville sett ut som suksess.
VENT_PAA_BLOKK = 60.0

RUNNER = r'''
import importlib, json, os, sys
inn = json.load(open(sys.argv[1], encoding="utf-8"))
import psycopg
modul = importlib.import_module("drift." + inn["modul_fil"])
conn = psycopg.connect(os.environ[inn["dsn_variabel"]])
# ÉN LINJE FØR SVEIPEN, slik at forelderen vet at bytene er lastet og
# tilkoblingen står: uten den kan «drept» bety «drept før den startet».
print(json.dumps({"klar": True, "fil": modul.__file__, "pid": os.getpid()}),
      flush=True)
res = modul.kjor(conn)
conn.close()
print(json.dumps({"klar": False, "fil": modul.__file__, "pid": os.getpid(),
                  "res": {"tenanter": res.tenanter, "nye": res.nye,
                          "oppdaterte": res.oppdaterte, "lukkede": res.lukkede,
                          "feilet": res.feilet, "hoppet_over": res.hoppet_over,
                          "alarm_utlost": res.alarm_utlost}}), flush=True)
'''


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


#: Hvor lenge vi venter på at underprosessen melder seg klar.
VENT_PAA_KLAR = 120.0


def _les_linje(p: subprocess.Popen, frist: float) -> tuple[str | None, str]:
    """Én linje fra barnets stdout, MED FRIST. -> (linjen, resten lest).

    `readline()` uten frist ville hengt for alltid om barnet døde før det
    rakk å skrive noe — og en drill som henger natten gjennom er verre
    enn en som feiler. Resten som ble lest gis tilbake, så den senere
    `communicate()` ikke mister begynnelsen av neste linje."""
    buf = ""
    slutt = time.monotonic() + frist
    sel = selectors.DefaultSelector()
    os.set_blocking(p.stdout.fileno(), False)
    sel.register(p.stdout, selectors.EVENT_READ)
    try:
        while time.monotonic() < slutt:
            for _ in sel.select(timeout=0.5):
                bit = p.stdout.read()
                if bit:
                    buf += bit
                    if "\n" in buf:
                        linje, rest = buf.split("\n", 1)
                        return linje, rest
                elif bit == "":
                    return (None, buf)      # EOF uten linje
            if p.poll() is not None and "\n" not in buf:
                return None, buf
        return None, buf
    finally:
        sel.unregister(p.stdout)
        sel.close()
        os.set_blocking(p.stdout.fileno(), True)


def start_i(katalog: Path, inn: dict) -> tuple[subprocess.Popen, dict, str]:
    """Sveipen som underprosess fra `katalog`. -> (prosess, klarlinje, rest)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{katalog}/platform/core:{katalog}/platform"
    td = tempfile.mkdtemp(prefix="sveipdrill-")
    Path(td, "inn.json").write_text(json.dumps(inn), encoding="utf-8")
    Path(td, "runner.py").write_text(RUNNER, encoding="utf-8")
    p = subprocess.Popen([sys.executable, f"{td}/runner.py", f"{td}/inn.json"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, cwd=f"{katalog}/platform/core", env=env)
    linje, rest = _les_linje(p, VENT_PAA_KLAR)
    if linje is None:
        p.kill()
        _, err = p.communicate(timeout=60)
        raise SystemExit(f"AVBRUTT: runneren i {katalog} meldte seg aldri"
                         f" klar innen {VENT_PAA_KLAR:g}s:\n{(err or '')[-1500:]}")
    klar = json.loads(linje.strip())
    if not klar["fil"].startswith(str(katalog) + "/"):
        p.kill()
        p.communicate(timeout=60)
        raise SystemExit(f"AVBRUTT: sveipen ble lastet fra {klar['fil']},"
                         f" ikke {katalog}")
    return p, klar, rest


def kjor_i(katalog: Path, inn: dict) -> dict:
    """En hel sveip fra `katalog` — venter til den er ferdig."""
    p, klar, rest = start_i(katalog, inn)
    try:
        ut, err = p.communicate(timeout=900)
    except subprocess.TimeoutExpired:
        # EN HENGENDE SVEIP RYDDES OPP, ikke etterlates: barnet holder
        # advisory-låsen, og neste steg i drillen ville blokkert på den.
        p.kill()
        _ut, err = p.communicate()
        raise SystemExit(f"AVBRUTT: runneren i {katalog} brukte over 900s"
                         f" og ble drept:\n{(err or '')[-1500:]}")
    ut = rest + ut
    if p.returncode != 0:
        raise SystemExit(f"AVBRUTT: runneren i {katalog} feilet:\n{err[-1500:]}")
    siste = json.loads(ut.strip().splitlines()[-1])
    if siste.get("klar"):
        raise SystemExit(f"AVBRUTT: runneren i {katalog} rapporterte aldri"
                         " et resultat")
    return siste


def maal(m, k: dict, tenanter: list[str]) -> dict:
    """Drillens egne funn, talt gjennom eierrollen tenant for tenant.

    `dubletter` teller rader som deler subjekt OG funntype — det er
    formen en rulling kan skrive samme sannhet to ganger i.

    SUBJEKTKOLONNEN KOMMER FRA MODULENS OPPFØRING: registrene deler form,
    ikke navn (`subjekt_id` i M-19, `vare_id` i M-27), og en hardkodet
    kolonne her ville gjort den generiske drillen til M-19s egen."""
    # SET ROLE ER TRANSAKSJONELT: en `rollback()` rett etterpå ville tatt
    # rollen av igjen, og tellingen under hadde kjørt som migratoren.
    # Rollen committes derfor, og bare tenantkonteksten rulles tilbake.
    m.execute(f"SET ROLE {k['maalerolle']}")
    m.commit()
    totalt = apne = distinkte = 0
    for tenant in tenanter:
        m.execute("SELECT set_config('disponit.tenant', %s, true)", (tenant,))
        rad = m.execute(
            f"SELECT count(*), count(*) FILTER (WHERE apen),"
            f" count(DISTINCT ({k['subjektkolonne']}, funntype))"
            f" FROM {k['funntabell']}"
            " WHERE tenant = %s", (tenant,)).fetchone()
        m.rollback()
        totalt += int(rad[0]); apne += int(rad[1]); distinkte += int(rad[2])
    return {"rader": totalt, "apne": apne, "dubletter": totalt - distinkte}


def blokkert(m, tabell: str) -> bool:
    """Står det en ANNEN backend og venter på lås på funntabellen?

    Målt i `pg_locks`, ikke i `pg_stat_activity`: spørreteksten der er
    skjult for alle andre enn superbrukeren og sesjonens egen rolle, så
    et oppslag på «hvem kjører sveipedøra» ville alltid sagt nei — og
    drillen ville konkludert med at sveipen aldri blokkerte. Låsetabellen
    er synlig for alle."""
    rad = m.execute(
        "SELECT count(*) FROM pg_locks WHERE relation = %s::regclass"
        " AND NOT granted AND pid <> pg_backend_pid()", (tabell,)).fetchone()
    m.rollback()
    return int(rad[0]) > 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modul", required=True)
    ap.add_argument("--forgjenger-katalog", required=True)
    ap.add_argument("--drillet-katalog", default="/opt/disponit/aktiv")
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    if a.modul not in SVEIPMODULER:
        raise SystemExit(f"AVBRUTT: {a.modul} er ikke registrert som"
                         " sveipmodul i manifestskjema.SVEIPMODULER")
    k = SVEIPMODULER[a.modul]
    krav = k["rollback_krav"]
    rt_dsn = os.environ.get("DATABASE_URL")
    m_dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    sv_dsn = os.environ.get(k["dsn_variabel"])
    if not (rt_dsn and m_dsn and sv_dsn):
        raise SystemExit("AVBRUTT: DATABASE_URL/DISPONIT_MIGRATOR_URL/"
                         f"{k['dsn_variabel']} mangler")
    d_kat = Path(a.drillet_katalog).resolve()
    f_kat = Path(a.forgjenger_katalog).resolve()
    if d_kat == f_kat:
        raise SystemExit("AVBRUTT: forgjengeren er den drillede katalogen")
    for kat in (d_kat, f_kat):
        if not (kat / "platform/drift" / f"{k['modul_fil']}.py").is_file():
            raise SystemExit(f"AVBRUTT: {kat} har ingen {k['modul_fil']}")
    # KJERNEN er det som rulles; modulens egne filer står som oftest
    # stille gjennom en rulling, og måles derfor bare som et faktum.
    d_kjerne, f_kjerne = kjerne_digest(d_kat), kjerne_digest(f_kat)
    d_dig = sveipkjerne_digest(d_kat, a.modul)
    f_dig = sveipkjerne_digest(f_kat, a.modul)
    _log(f"drillet {d_kat.name[:8]} (kjerne {d_kjerne[:12]}, modul"
         f" {d_dig[:12]}) ← forgjenger {f_kat.name[:8]} (kjerne"
         f" {f_kjerne[:12]}, modul {f_dig[:12]})")

    rigg_modul = __import__(k["riggmodul"])
    rt = psycopg.connect(rt_dsn)
    m = psycopg.connect(m_dsn)
    laas = psycopg.connect(m_dsn)
    # ØKTEN AVSLUTTES AV SIN EGEN ROLLE. Migratoren er verken superbruker
    # eller medlem av `pg_signal_backend`, og kan ikke røre sveipens
    # backend; sveiperollen kan rydde sine egne.
    avslutter = psycopg.connect(sv_dsn)
    runde = secrets.token_hex(4)
    rigg = rigg_modul.forbered(rt, runde)
    tenanter = rigg_modul.riggtenanter(rigg)
    _log(f"runde {runde}: {len(rigg['subjekter'])} subjekter i"
         f" {len(tenanter)} tenanter")
    inn = {"modul_fil": k["modul_fil"], "dsn_variabel": k["dsn_variabel"]}
    t0 = maal(m, k, tenanter)
    if t0["rader"]:
        raise SystemExit("AVBRUTT: riggens tenanter har alt funn før drillen")

    # ------------------------------------------------------------------
    # (a) AVBRUTT: låsen tas utenfra, sveipen blokkerer, prosessen drepes.
    # ------------------------------------------------------------------
    laas.execute(f"LOCK TABLE {k['funntabell']} IN ACCESS EXCLUSIVE MODE")
    p, klar, _rest = start_i(d_kat, inn)
    frist = time.monotonic() + VENT_PAA_BLOKK
    sto_i_lås = False
    while time.monotonic() < frist:
        if blokkert(m, k["funntabell"]):
            sto_i_lås = True
            break
        if p.poll() is not None:
            break
        time.sleep(0.2)
    if not sto_i_lås:
        p.kill(); laas.rollback()
        raise SystemExit("AVBRUTT: sveipen blokkerte aldri på tabellåsen —"
                         " en drept prosess som ikke rakk å skrive måler"
                         " ingenting")
    # HVEM VENTER? Backend-pid-en tas FØR drapet: etterpå er det ingen
    # spørring å kjenne den igjen på.
    bakgrunn = m.execute(
        f"SELECT pid FROM pg_locks WHERE relation = %s::regclass"
        " AND NOT granted AND pid <> pg_backend_pid()",
        (k["funntabell"],)).fetchone()[0]
    m.rollback()
    os.kill(p.pid, signal.SIGKILL)
    p.wait(timeout=60)
    drept = p.returncode == -signal.SIGKILL
    # KLIENTEN ER BORTE — MEN IKKE ØKTEN. En backend som venter på lås
    # merker ingenting før låsen slipper, og ville da fullført og
    # committet sveipen mot en klient som ikke finnes. Det måles, og så
    # avsluttes økten på serversiden, slik en release-rulling gjør.
    levde = bool(avslutter.execute(
        "SELECT count(*)>0 FROM pg_stat_activity WHERE pid = %s",
        (bakgrunn,)).fetchone()[0])
    avslutter.rollback()
    avsluttet = bool(avslutter.execute("SELECT pg_terminate_backend(%s)",
                                       (bakgrunn,)).fetchone()[0])
    avslutter.commit()
    _log(f"foreldreløs backend {bakgrunn}: levde={levde}"
         f" avsluttet={avsluttet}")
    laas.rollback()          # slipper ACCESS EXCLUSIVE
    t1 = maal(m, k, tenanter)
    _log(f"avbrutt (drillet): drept={drept} returkode={p.returncode}"
         f" — {t1['rader']} funnrader i riggen")

    # Arbeidernøkkelen SKAL være fri: den døde sesjonen slipper den.
    modul_lokalt = __import__(f"drift.{k['modul_fil']}", fromlist=["kjor"])
    nokkel = modul_lokalt.ARBEIDERNOKKEL
    fri = laas.execute(
        "SELECT count(*)=0 FROM pg_locks WHERE locktype='advisory'"
        " AND ((classid::bigint << 32) | objid::bigint) = %s"
        " AND objsubid = 1", (nokkel,)).fetchone()[0]
    laas.rollback()
    _log(f"arbeidernøkkelen fri etter drapet: {fri}")

    # ------------------------------------------------------------------
    # (b) RULLBAKK: forgjengerens bytes fullfører den samme sveipen.
    # ------------------------------------------------------------------
    r2 = kjor_i(f_kat, inn)
    t2 = maal(m, k, tenanter)
    _log(f"rullbakk (forgjenger): {r2['res']} — {t2['apne']} åpne funn i"
         f" riggen, {t2['dubletter']} dubletter")

    # ------------------------------------------------------------------
    # (c) KANDIDAT: de drillede bytene godtar det forgjengeren skrev.
    # ------------------------------------------------------------------
    r3 = kjor_i(d_kat, inn)
    t3 = maal(m, k, tenanter)
    _log(f"kandidat (drillet): {r3['res']} — {t3['rader']} funnrader,"
         f" {t3['rader'] - t2['rader']} nye")

    ts = datetime.now(timezone.utc).isoformat()
    art = {
        "krav_id": krav, "ts": ts, "bestatt": True,
        "oppsett": {
            "modul": a.modul, "miljo": "staging", "vert": os.uname().nodename,
            "runde": runde, "tenanter": tenanter,
            "funntabell": k["funntabell"], "maalerolle": k["maalerolle"],
            "drillet_release": d_kat.name, "forgjenger_release": f_kat.name,
            "drillet_katalog": str(d_kat), "forgjenger_katalog": str(f_kat),
            "drillet_digest": d_dig, "forgjenger_digest": f_dig,
            "drillet_kjernedigest": d_kjerne,
            "forgjenger_kjernedigest": f_kjerne,
            "arbeidernokkel": nokkel, "avbrutt_backend_pid": bakgrunn,
            "bevisrot_sha256": sveip_rollback_bevisrot_sha256(),
            "form": "kjerne: sveipen ruller med kjernen — avbruddet tas"
                    " utenfra med ACCESS EXCLUSIVE + SIGKILL, ekte base,"
                    " underprosess per release-katalog",
        },
        "identiteter": {
            "avbrutt_pid": klar["pid"], "rullback_pid": r2["pid"],
            "kandidat_pid": r3["pid"],
            "avbrutt_fil": klar["fil"], "rullback_fil": r2["fil"],
            "kandidat_fil": r3["fil"],
        },
        "maalt": {
            "inflight_drept": drept,
            "inflight_returkode": p.returncode,
            "inflight_blokkerte_paa_laas": sto_i_lås,
            "inflight_backend_levde_etter_drap": levde,
            "inflight_backend_avsluttet": avsluttet,
            "inflight_funn": t1["rader"],
            "arbeidernokkel_fri": bool(fri),
            "rullbakk_funn": t2["apne"],
            "rullbakk_rader": t2["rader"],
            "dubletter": t3["dubletter"],
            "kandidat_nye": t3["rader"] - t2["rader"],
            "kandidat_apne": t3["apne"],
            "rullback_bytes_er_forgjengerens":
                r2["fil"].startswith(str(f_kat) + "/"),
            "kandidat_bytes_er_drillede":
                r3["fil"].startswith(str(d_kat) + "/"),
            # BEGGE KATALOGENE MÅLES PÅ NYTT til slutt: endret noe seg
            # under drillen, målte vi ikke de bytene vi sier.
            "release_digest_bundet": (sveipkjerne_digest(d_kat, a.modul) == d_dig
                                      and sveipkjerne_digest(f_kat, a.modul) == f_dig
                                      and kjerne_digest(d_kat) == d_kjerne
                                      and kjerne_digest(f_kat) == f_kjerne),
            # MODULENS EGNE FILER er som regel de samme over en rulling.
            # Det er ikke en feil — det er nettopp derfor kjerneformen
            # finnes — men det skal stå i artefaktet.
            "modul_digest_likt": d_dig == f_dig,
        },
        "etterkontroll": {
            "aktiv_urort": Path("/opt/disponit/aktiv").resolve() == d_kat,
            "kandidat_feilet": bool(r3["res"]["feilet"]),
        },
    }
    feil = valider_artefaktformat(art, krav) + _sjekk_grenser(krav, art)
    art["bestatt"] = not feil
    if feil:
        art["feil"] = feil
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"{krav}-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True)
                  + "\n", encoding="utf-8")
    _log(f"skrev {ut} (bestatt={art['bestatt']})")
    for f in feil:
        _log("  FEIL:", f)
    return 0 if not feil else 1


if __name__ == "__main__":
    raise SystemExit(main())
