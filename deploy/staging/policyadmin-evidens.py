#!/usr/bin/env python3
"""Policyadministrasjon-evidens — produserer `policyadmin-v1`.

Kjører den EKTE aktiveringsflyten (`api.policyadmin`) mot en ekte base: ekte
klassifikator, ekte diff, ekte herdet `aktiver_policy` (SECURITY DEFINER),
ekte MAC-signering, ekte triggere. Fire kategori-veier beviser
fire-øyne-fullmaktsmodellen:

  - utvider:         UTVIDER (mot deny-all) krever TO godkjennere → aktiveres
                     først når en uavhengig godkjenner nummer to attesterer.
  - forfatter_alene: forfatteren alene når ALDRI terskelen (append-only UNIQUE
                     + «minst én ikke-forfatter») → aldri aktivert.
  - innsnevrer:      INNSNEVRER krever ÉN godkjenner ≠ forfatteren → aktiveres.
  - rebasering:      en konkurrerende aktivering flytter basen etter at runden
                     åpnet → rekalk under låsen avviser → rebasering.

Harde invarianter (målt fra råtellinger, aldri et flagg):
  - policyer_med_flere_aktive = 0 (atomisiteten: aldri to aktive, V1/V10).
  - runtime_skrivenekt = 1 (runtime nektes direkte INSERT i `policyer`, V10).
  - diff_binding: hver attestasjons `diff_hash` == rundens (godkjenneren
    attesterte DIFFEN, ikke versjonsnummeret).

Artefaktet MÅLES her, men VALIDERES av evidensporten
(`manifestskjema._grenser_policyadmin`), aldri av dette skriptet: `bestatt` er
produsentens egen påstand.

BRUK:
    DISPONIT_REPO=/opt/disponit DISPONIT_TEST_DSN=... \\
    DISPONIT_TEST_MIGRATOR_DSN=... DISPONIT_MAC_NOKLER=... \\
    python3 deploy/staging/policyadmin-evidens.py [--ut artefakt.json] [--per N]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(os.environ["DISPONIT_REPO"])
sys.path.insert(0, str(REPO / "platform/core"))

DSN = os.environ["DISPONIT_TEST_DSN"]
MIGRATOR = os.environ["DISPONIT_TEST_MIGRATOR_DSN"]
TENANT = "t-paev-" + secrets.token_hex(3)

import psycopg                                              # noqa: E402
from api import policyadmin                                 # noqa: E402
from api import policyregister as pr                        # noqa: E402
from api.mac_register import last_mac_register              # noqa: E402
from db.pg import koble, sett_kontekst                      # noqa: E402


def _naa():
    return datetime.now(timezone.utc)


def _mig():
    c = koble(MIGRATOR)
    sett_kontekst(c, TENANT, "sys", "r0")
    return c


def _rt():
    return koble(DSN)


def _identitet(m, sub):
    return m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES ('https://idp',%s)"
        " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
        " RETURNING bruker_id", (sub,)).fetchone()[0]


def _medlem(sub, roller):
    m = _mig()
    bid = _identitet(m, f"{TENANT}-{sub}")
    arr = "ARRAY[" + ",".join(f"'{r}'" for r in roller) + "]"
    m.execute(f"INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
              f" VALUES (%s,%s,{arr}) ON CONFLICT (tenant,bruker_id)"
              f" DO UPDATE SET roller=EXCLUDED.roller", (TENANT, bid))
    m.commit()
    m.close()
    return bid


def _utkast(uid, pid, av, innhold):
    m = _mig()
    h = pr.innholds_hash(innhold)
    m.execute(
        "INSERT INTO policyutkast (tenant,utkast_id,policy_id,innhold,"
        "innholds_hash,status,opprettet_av) VALUES"
        " (%s,%s,%s,%s::jsonb,%s,'validert',%s)",
        (TENANT, uid, pid, json.dumps(innhold), h, av))
    m.commit()
    m.close()


def _aktiv_base(pid, innhold, versjon="1"):
    m = _mig()
    h = pr.innholds_hash(innhold)
    m.execute(
        "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,status,"
        "innhold,aktiv) VALUES (%s,%s,%s,%s,'produksjon',%s::jsonb,true)",
        (TENANT, pid, versjon, h, json.dumps(innhold)))
    m.execute(
        "INSERT INTO policy_hode (tenant,policy_id,neste_versjon,aktiv_versjon,"
        "revisjon) VALUES (%s,%s,%s,%s,1)",
        (TENANT, pid, int(versjon) + 1, versjon))
    m.commit()
    m.close()


def _apne(rt, uid, aktor):
    r = policyadmin.opprett_aktiveringsrunde(
        rt, tenant=TENANT, utkast_id=uid, aktor=aktor, request_id="r",
        naa=_naa())
    rt.commit()
    return r


def _attest(rt, uid, aktor, diff_hash):
    idem = secrets.token_hex(8)
    ih = f"{TENANT}\x1f{uid}\x1f{aktor}\x1f{diff_hash}\x1f{idem}"
    return policyadmin.attester_aktivering(
        rt, MAC, tenant=TENANT, aktor=aktor, request_id="r", utkast_id=uid,
        forventet_diff_hash=diff_hash, idempotency_key=idem, input_hash=ih,
        naa=_naa())


def kjor(per: int) -> dict:
    forf = _medlem("forf", ["policyforvalter"])
    uavh = _medlem("uavh", ["policyforvalter"])
    maalt = {
        "utvider": {"injisert": 0, "aktivert": 0},
        "forfatter_alene": {"injisert": 0, "stoppet": 0},
        "innsnevrer": {"injisert": 0, "aktivert": 0},
        "rebasering": {"injisert": 0, "rebasert": 0},
    }
    rt = _rt()
    try:
        for i in range(per):
            # --- utvider: to godkjennere → aktivert -----------------------
            pid = f"utv-{secrets.token_hex(3)}"
            uid = f"u-{secrets.token_hex(4)}"
            _utkast(uid, pid, forf, {"roller": [{"id": "r1"}],
                                     "handlinger": [{"id": "h1"}]})
            r = _apne(rt, uid, forf)
            maalt["utvider"]["injisert"] += 1
            _attest(rt, uid, forf, r["diff_hash"])
            res = _attest(rt, uid, uavh, r["diff_hash"])
            if res["utfall"] == "aktivert":
                maalt["utvider"]["aktivert"] += 1

            # --- forfatter_alene: forfatter alene → aldri aktivert --------
            pid = f"fal-{secrets.token_hex(3)}"
            uid = f"u-{secrets.token_hex(4)}"
            _utkast(uid, pid, forf, {"roller": [{"id": "r1"}]})
            r = _apne(rt, uid, forf)
            maalt["forfatter_alene"]["injisert"] += 1
            res = _attest(rt, uid, forf, r["diff_hash"])
            if res["utfall"] == "venter_godkjennere" \
                    and _aktiv_versjon(pid) is None:
                maalt["forfatter_alene"]["stoppet"] += 1

            # --- innsnevrer: én uavhengig → aktivert ----------------------
            pid = f"inn-{secrets.token_hex(3)}"
            uid = f"u-{secrets.token_hex(4)}"
            _aktiv_base(pid, {"roller": [{"id": "r1"}, {"id": "r2"}]})
            _utkast(uid, pid, forf, {"roller": [{"id": "r1"}]})
            r = _apne(rt, uid, forf)
            maalt["innsnevrer"]["injisert"] += 1
            res = _attest(rt, uid, uavh, r["diff_hash"])
            if res["utfall"] == "aktivert":
                maalt["innsnevrer"]["aktivert"] += 1

            # --- rebasering: base flyttes under runden → rebasering -------
            pid = f"reb-{secrets.token_hex(3)}"
            uid = f"u-{secrets.token_hex(4)}"
            _utkast(uid, pid, forf, {"roller": [{"id": "r1"}]})
            r = _apne(rt, uid, forf)
            maalt["rebasering"]["injisert"] += 1
            _aktiv_base(pid, {"roller": [{"id": "rX"}]})   # konkurrent
            _attest(rt, uid, forf, r["diff_hash"])
            res = _attest(rt, uid, uavh, r["diff_hash"])
            if res["utfall"] == "rebasering_kreves":
                maalt["rebasering"]["rebasert"] += 1
    finally:
        rt.close()

    # --- harde invarianter (målt fra DB) ----------------------------------
    m = _mig()
    flere = m.execute(
        "SELECT count(*) FROM (SELECT tenant, policy_id FROM policyer"
        " WHERE tenant=%s AND aktiv GROUP BY tenant, policy_id"
        " HAVING count(*) > 1) x", (TENANT,)).fetchone()[0]
    diff_tot = m.execute(
        "SELECT count(*) FROM aktiveringsattestasjon WHERE tenant=%s",
        (TENANT,)).fetchone()[0]
    diff_treff = m.execute(
        "SELECT count(*) FROM aktiveringsattestasjon a JOIN aktiveringsrunde r"
        " ON a.tenant=r.tenant AND a.utkast_id=r.utkast_id AND a.runde=r.runde"
        " WHERE a.tenant=%s AND a.diff_hash=r.diff_hash", (TENANT,)).fetchone()[0]
    m.rollback()
    m.close()

    # runtime_skrivenekt: runtime MÅ nektes direkte INSERT i policyer (V10).
    nekt = 0
    rt2 = _rt()
    try:
        sett_kontekst(rt2, TENANT, "forf", "r")
        rt2.execute(
            "INSERT INTO policyer (tenant,policy_id,versjon,innholds_hash,"
            "status,innhold,aktiv) VALUES (%s,'x','9','h','produksjon',"
            "'{}'::jsonb,false)", (TENANT,))
        rt2.rollback()          # skulle ALDRI nå hit
    except psycopg.errors.InsufficientPrivilege:
        nekt = 1
        rt2.rollback()
    finally:
        rt2.close()

    return {
        "kategorier_dekket": ["utvider", "forfatter_alene", "innsnevrer",
                              "rebasering"],
        **maalt,
        "aktiveringer_totalt": (maalt["utvider"]["aktivert"]
                                + maalt["innsnevrer"]["aktivert"]),
        "policyer_med_flere_aktive": int(flere),
        "runtime_skrivenekt": nekt,
        "diff_binding_treff": int(diff_treff),
        "diff_binding_totalt": int(diff_tot),
        "handlinger_totalt": int(diff_tot),
    }


def _aktiv_versjon(pid):
    m = _mig()
    rad = m.execute("SELECT aktiv_versjon FROM policy_hode WHERE tenant=%s AND"
                    " policy_id=%s", (TENANT, pid)).fetchone()
    m.rollback()
    m.close()
    return rad[0] if rad else None


def main() -> int:
    global MAC
    ap = argparse.ArgumentParser()
    ap.add_argument("--ut", default=None)
    ap.add_argument("--per", type=int, default=2)
    args = ap.parse_args()
    MAC = last_mac_register()

    t0 = time.monotonic()
    maalt = kjor(args.per)
    maalt["varighet_sek"] = round(time.monotonic() - t0, 3)

    injisert = sum(maalt[k]["injisert"] for k in
                   ("utvider", "forfatter_alene", "innsnevrer", "rebasering"))
    artefakt = {
        "krav_id": "policyadmin-v1",
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "bestatt": True,
        "oppsett": {"injisert_antall": injisert,
                    "kategorier": ["utvider", "forfatter_alene", "innsnevrer",
                                   "rebasering"]},
        "maalt": maalt,
    }
    raa = json.dumps(artefakt, ensure_ascii=False, indent=2).encode("utf-8")
    if args.ut:
        Path(args.ut).write_bytes(raa)
        print(f"skrev {args.ut}")
        print(f"sha256 {hashlib.sha256(raa).hexdigest()}")
    else:
        sys.stdout.write(raa.decode("utf-8") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
