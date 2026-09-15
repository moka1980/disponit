#!/usr/bin/env python3
"""ENGANGS: e-post hentet FØR broen fantes → henvendelser i M-17.

Broen i `plan/epost.py` (migrasjon 203) fyrer bare for meldinger som
NETTOPP ble lagret — den står etter `INSERT ... ON CONFLICT DO NOTHING`
og ser bare nye rader. Det er riktig for driften, men det etterlater
etterslepet: meldinger hentet inn før broen ble rullet ut er usynlige for
kundeserviceregisteret, og delta-cursoren har gått forbi dem for godt.

MÅLT 15/9: `wcagvakt` hadde 18 slike. Dette skriptet tar dem.

ENGANGS, IKKE EN SLØYFE, og det er en bevisst avveining. En permanent
etterslepsrunde ville skannet de samme radene hver eneste runde i all
framtid for et etterslep som slutter å vokse i det broen er ute. Prisen
for det ville vært en markørkolonne på `epost_melding` — altså en
migrasjon og et nytt felt å holde i synk — for en jobb som gjøres én
gang per eksisterende kunde.

IDEMPOTENT LIKEVEL: `m17_ta_imot` deduplikerer på
(tenant, kanal, ekstern_ref), så skriptet kan kjøres om igjen uten å
lage dubletter. Det rapporterer hvor mange som var nye.

BRUK (på verten, som root — DSN-en er planarbeiderens credential):
    /opt/disponit/.venv/bin/python \\
        deploy/staging/m6-etterslep-til-m17.py --tenant wcagvakt \\
        [--grense 500] [--torr]

`--torr` teller uten å skrive.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

#: RUNTIME-ROLLENS credential, ikke planarbeiderens — og det er MÅLT,
#: ikke valgt av vane: `disponit_plan_arbeider` har bare INSERT på
#: `epost_melding` og kan ikke LESE den. Broen i driften trenger ikke
#: lese (den har meldingen i hånda), men etterslepet må hente radene
#: fram igjen. `disponit` har både SELECT på tabellen og EXECUTE på
#: `m17_ta_imot`, og er dermed den eneste rollen som kan gjøre begge
#: leddene. Å gi planarbeideren SELECT for en engangsjobb ville utvidet
#: en driftsrolles fullmakt permanent.
CREDFIL = "/etc/disponit/api/DATABASE_URL"

#: …OG NØKKELEN. Kroppene er kryptert med tenantens DEK, som selv er
#: pakket med KEK-en. Under systemd kommer begge via `LoadCredential`;
#: utenfor finnes ingen `$CREDENTIALS_DIRECTORY`, og da må de hentes
#: her. Første kjøring lastet BARE DSN-en og feilet på alle 36
#: meldingene med «DISPONIT_KEK mangler» — en halv credential-lasting er
#: verre enn ingen, for den ser ut som en datafeil.
KEKFIL = "/etc/disponit/api/DISPONIT_KEK"


def _dsn() -> str | None:
    if os.environ.get("DISPONIT_ETTERSLEP_DSN"):
        return os.environ["DISPONIT_ETTERSLEP_DSN"]
    f = Path(CREDFIL)
    return f.read_text(encoding="utf-8").strip() if f.exists() else None


def _side(conn, tenant, markor, grense):
    """Én side av etterslepet, etter `markor` = (mottatt_ts, lev_id).

    Paret er nøkkelen: to meldinger kan dele tidsstempel, og en markør
    på tid alene ville hoppet over den ene eller gjentatt den andre.
    """
    from db.pg import sett_kontekst
    from plan.epost import AKTOR
    sett_kontekst(conn, tenant, AKTOR, "etterslep")
    if markor is None:
        rader = conn.execute(
            "SELECT leverandor_melding_id, mottatt_ts, kropp_kryptert,"
            "       nonce, key_id FROM epost_melding"
            " WHERE tenant=%s AND retning='inn'"
            " ORDER BY mottatt_ts, leverandor_melding_id LIMIT %s",
            (tenant, grense)).fetchall()
    else:
        rader = conn.execute(
            "SELECT leverandor_melding_id, mottatt_ts, kropp_kryptert,"
            "       nonce, key_id FROM epost_melding"
            " WHERE tenant=%s AND retning='inn'"
            "   AND (mottatt_ts, leverandor_melding_id) > (%s, %s)"
            " ORDER BY mottatt_ts, leverandor_melding_id LIMIT %s",
            (tenant, markor[0], markor[1], grense)).fetchall()
    conn.rollback()
    return rader


def kjor(conn, tenanter, *, grense: int, torr: bool) -> dict:
    from db import kryptering
    from db.pg import sett_kontekst
    from plan.epost import AKTOR, _til_kundeservice

    ut = {"tenanter": 0, "sett": 0, "nye": 0, "hoppet": 0, "feilet": 0}
    for tenant in tenanter:
        ut["tenanter"] += 1
        # PAGINERT PÅ EN STABIL MARKØR (CodeRabbit, major). Første
        # utkast hadde `ORDER BY mottatt_ts LIMIT %s` og ingenting som
        # førte den videre: en kunde med flere meldinger enn grensen
        # ville fått de SAMME første N behandlet hver kjøring, og resten
        # aldri. `mottatt_ts` alene er ingen nøkkel — to meldinger kan
        # ha samme tidsstempel — så markøren er PARET med
        # leverandør-id-en.
        rader = _side(conn, tenant, None, grense)
        while rader:
            for lev_id, mottatt, ct, nonce, key_id in rader:
                ut["sett"] += 1
                if torr:
                    continue
                try:
                    sett_kontekst(conn, tenant, AKTOR, "etterslep")
                    dek = kryptering.hent_dek(conn, tenant, key_id)
                    p = kryptering.dekrypter(dek, bytes(ct), bytes(nonce),
                                             tenant, key_id)
                    # BROEN KALLES, ikke en kopi av den: samme utledning,
                    # samme hjelpere, samme dør. En egen innlesingsvei her
                    # ville vært et annet sett regler for de samme radene.
                    fra = p.get("fra", "")
                    m = {"id": lev_id,
                         "receivedDateTime":
                             mottatt.isoformat() if mottatt else None,
                         "from": {"emailAddress": {"address": fra}}}
                    key_id2, dek2 = kryptering.hent_eller_opprett_aktiv_dek(
                        conn, tenant)
                    ny = _til_kundeservice(conn, tenant, m, fra,
                                           p.get("emne", ""),
                                           p.get("kropp", ""),
                                           dek2, key_id2)
                    conn.commit()
                    ut["nye" if ny else "hoppet"] += 1
                except Exception as e:                    # noqa: BLE001
                    conn.rollback()
                    ut["feilet"] += 1
                    # TYPENAVNET, aldri meldingen: et unntak kan bære emne
                    # eller adresse.
                    print(json.dumps({"hendelse": "etterslep_feilet",
                                      "tenant": tenant,
                                      "melding": str(lev_id)[:12],
                                      "feil": type(e).__name__}),
                          file=sys.stderr)
            if len(rader) < grense:
                break
            siste = rader[-1]
            rader = _side(conn, tenant, (siste[1], siste[0]),
                          grense)
    return ut


def main() -> int:
    ap = argparse.ArgumentParser()
    # TENANTEN OPPGIS, den utledes ikke. Første utkast gjorde et
    # kryss-tenant `SELECT DISTINCT tenant` uten kontekst — og RLS
    # skjulte ALT, så skriptet ville rapportert «0 tenanter» i
    # produksjon og sett vellykket ut. En kryss-tenant-dør for dette
    # ville vært ny fullmakt for en engangsjobb; operatøren vet hvilke
    # kunder som finnes.
    ap.add_argument("--tenant", action="append", required=True,
                    help="tenant å ta etterslepet for (kan gjentas)")
    ap.add_argument("--grense", type=int, default=500,
                    help="maks meldinger per tenant")
    ap.add_argument("--torr", action="store_true",
                    help="tell uten å skrive")
    a = ap.parse_args()
    dsn = _dsn()
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": f"fant ingen DSN ({CREDFIL})"}),
              file=sys.stderr)
        return 2
    # KEK-EN KREVES BARE NÅR DET SKAL SKRIVES (CodeRabbit): `--torr`
    # teller rader og dekrypterer ingenting, og en tørrkjøring som
    # nektet uten nøkkelen ville gjort det umulig å SE etterslepet fra
    # en maskin uten tilgang til den.
    if not a.torr:
        if not os.environ.get("DISPONIT_KEK"):
            k = Path(KEKFIL)
            if k.exists():
                os.environ["DISPONIT_KEK"] = k.read_text(
                    encoding="utf-8").strip()
        if not os.environ.get("DISPONIT_KEK"):
            # NEKTER Å STARTE framfor å feile på hver melding: uten
            # KEK-en kan ingen kropp dekrypteres, og kjøringen ville
            # rapportert 36 «feilet» som så ut som ødelagte data.
            print(json.dumps(
                {"hendelse": "oppstart_nektet",
                 "grunn": f"fant ingen DISPONIT_KEK ({KEKFIL})"}),
                file=sys.stderr)
            return 2
    from db.pg import koble
    conn = koble(dsn)
    try:
        ut = kjor(conn, a.tenant, grense=max(1, a.grense), torr=a.torr)
    finally:
        conn.close()
    print(json.dumps({"hendelse": "m6_etterslep", "torr": a.torr, **ut},
                     ensure_ascii=False))
    return 1 if ut["feilet"] else 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
