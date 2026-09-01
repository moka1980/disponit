#!/usr/bin/env python3
"""Kjør M-35s kontinuitetsøvelse og skriv rapportartefaktet
(`artefakt-m35-skjema.json`, krav_id `m35-v1`).

    DISPONIT_MIGRATOR_URL=… python3 deploy/staging/kjor-m35-ovelse.py \\
        --tenant <tenant> [--statusfil <sti>] [--live-url <url>] \\
        [--ut <fil>] [--siste-gronne-alder-dogn <n>]

FORMEN er registrer-m57-ats-formen (REPO-injisert sys.path, docstring
som kontrakt, konstantene lest FRA `oppdragskontrakt` og aldri skrevet
på nytt her). AVVIKET er at dette skriptet ikke registrerer noe: det
MÅLER, og målinger tar parametre — derfor argparse i stedet for
posisjonelle argumenter, og derfor er statusfilstien INJISERBAR.

HVORFOR STIEN ER INJISERBAR: porten «statusfil fraværende/foreldet →
rødt funn» er den viktigste i hele modulen (dom 4: aldri grønt uten
evidens), og en port som bare kan kjøres på en vert med rot-tilgang og
en levende backupkatalog blir aldri kjørt. Med `--statusfil` kan en
pytest legge en fil med kjent innhold — eller la være å legge den — og
måle at dommen blir rød. Standardverdien er driftsstien; injeksjonen
er en TESTINNGANG, ikke en produksjonsvalgmulighet.

DE FIRE MÅLINGENE (planens §4):
  1. Backup-evidensen — LESES fra `/var/backups/disponit/
     siste-verifisering.json`, som backup-db.sh skriver KUN ved suksess
     og atomisk (dom 4). Aldri journal-parsing, aldri en egen restore.
     Fraværende, uparsbar eller eldre enn 2 døgn ⇒ RØDT funn og
     `restore_verifisert: false`.
  2. Helsen — GET på `--live-url` (standard `/live` lokalt). Svarte den
     ikke 200, er det et rødt funn.
  3. Kartferskheten — hver KRITISK rad i `kontinuitet_tjeneste` må peke
     på en referent som fortsatt finnes. `systemd_unit` verifiseres mot
     systemd, `modul` mot modulregisteret, `ekstern` er UVERIFISERBAR
     herfra og gir et GULT funn (øvelsen sier ærlig hva den ikke kan
     måle, i stedet for å gjette grønt).
  4. Kontaktdekningen — hver kritiske tjenestes kontaktrolle må ha en
     kontakt bekreftet innenfor 90 døgn.

RTO-TALLET (dom 5) heter `maalt_restoretid_s` og er
restore-til-ISOLERT-BASE-proxyen fra backupskriptets egen verifisering.
Det er IKKE full tjeneste-RTO; den krever en selvrevers-øvelse (v2), og
verken dette skriptet, artefaktet eller flaten later som noe annet.

`--siste-gronne-alder-dogn` tas imot fordi øvelseshistorikken bor i
artefaktlageret, og PR-B er det som kobler kjøringen til det. Uten
argumentet står feltet `null`, og `m35-v1`-grensen feller det — som den
skal: en rytme ingen har målt er ingen rytme.

Exit 0 hvis rapporten er bestått, 1 ellers. Skriptet MUTERER ingenting.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(os.environ.get("DISPONIT_REPO",
                           Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from modules.m35_kontinuitet import ovelse  # noqa: E402

#: Driftsstien. Injiserbar med `--statusfil` (se docstringen).
STANDARD_STATUSFIL = Path("/var/backups/disponit/siste-verifisering.json")
STANDARD_LIVE = "http://127.0.0.1:8000/live"
LIVE_TIMEOUT_S = 5


def les_kart(conn, tenant: str) -> list[tuple]:
    """Kartradene på registerets egen form — SAMME rad mater både
    ferskhets- og dekningsmålingen (`ovelse.vurder_kart` /
    `vurder_kontakter`), så de aldri kan dømme på hvert sitt utvalg."""
    return [tuple(r) for r in conn.execute(
        "SELECT tjeneste_id, referent_type, referent_id, kritikalitet,"
        " kontaktrolle FROM kontinuitet_tjeneste WHERE tenant=%s"
        " ORDER BY tjeneste_id", (tenant,)).fetchall()]


def les_kontakter(conn, tenant: str) -> list[tuple]:
    """(rolle, bekreftet_ts som epoch|None) — epoch fordi målingen er
    aritmetikk, og datoparsing i målelogikken er en feilkilde til."""
    return [(r[0], None if r[1] is None else r[1].timestamp())
            for r in conn.execute(
                "SELECT rolle, bekreftet_ts FROM beredskapskontakt"
                " WHERE tenant=%s", (tenant,)).fetchall()]


def _systemd_unit_finnes(unit: str) -> bool | None:
    """True/False, eller None når systemd ikke kan spørres herfra —
    None er «uverifiserbar», og øvelsen sier det i stedet for å gjette."""
    if shutil.which("systemctl") is None:
        return None
    try:
        r = subprocess.run(["systemctl", "list-unit-files", "--no-legend",
                            "--no-pager", unit],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode not in (0, 1):
        return None
    return bool(r.stdout.strip())


def lag_referentsjekk(conn):
    """`referent_finnes(type, id)` -> True/False/None.

    `modul` slås opp i modulregisteret på disk (manifestet ER
    registeret, 014a §7). `ekstern` er alltid None: en tredjeparts
    tilstand kan ikke måles herfra, og et grønt der ville vært en
    gjetning i akkurat den raden der en gjetning koster mest.
    """
    moduler = {p.name for p in (REPO / "platform" / "modules").iterdir()
               if (p / "manifest.yaml").is_file()} \
        if (REPO / "platform" / "modules").is_dir() else set()

    def referent_finnes(referent_type: str, referent_id: str):
        if referent_type == "systemd_unit":
            return _systemd_unit_finnes(referent_id)
        if referent_type == "modul":
            return referent_id in moduler
        return None            # 'ekstern' — uverifiserbar herfra
    return referent_finnes


def sjekk_live(url: str) -> bool:
    """GET /live. ENHVER feil er et nei — en helsesjekk som svarer
    «kanskje» er ingen helsesjekk."""
    try:
        with urllib.request.urlopen(url, timeout=LIVE_TIMEOUT_S) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="M-35 kontinuitetsøvelse (krav_id m35-v1)")
    ap.add_argument("--tenant", required=True)
    ap.add_argument("--statusfil", type=Path, default=STANDARD_STATUSFIL,
                    help="backup-db.sh sin siste-verifisering.json"
                         " (injiserbar for test)")
    ap.add_argument("--live-url", default=STANDARD_LIVE)
    ap.add_argument("--ut", type=Path,
                    help="skriv artefaktet hit (ellers stdout)")
    ap.add_argument("--siste-gronne-alder-dogn", type=float, default=None)
    ap.add_argument("--commit", default="0" * 40)
    ap.add_argument("--vert", default=os.uname().nodename)
    a = ap.parse_args(argv)

    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not dsn:
        print("AVBRUTT: DISPONIT_MIGRATOR_URL mangler", file=sys.stderr)
        return 2

    naa = time.time()
    statusfil = ovelse.vurder_statusfil(a.statusfil, naa)
    live_ok = sjekk_live(a.live_url)

    with psycopg.connect(dsn) as conn:
        # Lesetransaksjon med tenantkontekst — RLS gjelder også her, og
        # en øvelse som leste på tvers av tenanter ville vært nøyaktig
        # den lekkasjen registeret er skopet for å hindre.
        conn.execute("SELECT set_config('disponit.tenant',%s,true)",
                     (a.tenant,))
        tjenester = les_kart(conn, a.tenant)
        kontakter_rader = les_kontakter(conn, a.tenant)
        referent_finnes = lag_referentsjekk(conn)
        conn.rollback()

    kart = ovelse.vurder_kart(tjenester, referent_finnes)
    kontakter = ovelse.vurder_kontakter(tjenester, kontakter_rader, naa)

    # De to hendelsesinvariantene MÅLES av testsuiten (append-only og
    # etteranalyse-kravet er basens vakter, ikke noe en CLI kan prøve
    # uten å skrive). Her rapporteres de som UMÅLTE — null forsøk — og
    # `m35-v1`-grensen feller nettopp det: en port som aldri kjørte har
    # ikke målt noe. Å skrive `1 forsøk, 0 brudd` her ville vært å
    # signere for en måling skriptet ikke gjorde.
    rapport = ovelse.bygg_rapport(
        tenant=a.tenant, commit=a.commit, vert=a.vert,
        ts_iso=datetime.fromtimestamp(naa, timezone.utc).isoformat(),
        statusfil=statusfil, kart=kart, kontakter=kontakter,
        live_ok=live_ok,
        tidslinje_forsok=0, tidslinje_brudd=0,
        lukking_forsok=0, lukking_brudd=0,
        siste_gronne_alder_dogn=a.siste_gronne_alder_dogn,
        # DDL-dommen eies av migrasjonskjøringen, ikke av øvelsen.
        # `false` her er ærlig: dette skriptet har ikke kjørt
        # migrasjonene to ganger og kan ikke påstå at de var grønne.
        ddl_begge_gronne=False)

    tekst = json.dumps(rapport, indent=2, ensure_ascii=False,
                       sort_keys=True) + "\n"
    if a.ut:
        a.ut.write_text(tekst, encoding="utf-8")
        print(f"skrev {a.ut} (bestatt={rapport['bestatt']})")
    else:
        sys.stdout.write(tekst)
    return 0 if rapport["bestatt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
