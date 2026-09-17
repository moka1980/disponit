#!/usr/bin/env python3
"""Fasitartefaktet for M-19 (`m19-fasit-v1`).

Driver settet i `m19_fasit.py` gjennom modulens egne dører, kjører
sveipen med sin egen funksjon (uten tenantkontekst, som arbeideren), og
måler fire akser:

  * FUNNAVVIK — hvert subjekt fikk NØYAKTIG den funntypen fasiten sier,
    og det rene fikk ingen. Re-regnet av `adressefunn`, aldri av et
    aggregat.
  * IDEMPOTENS — andre kjøring gir null NYE funn. En sveip som finner
    det samme på nytt hver runde fyller køen med duplikater.
  * LUKKING — det rene subjektet fødes UKONTROLLERT og får sitt funn i
    første sveip; kontrollen registreres mellom kjøringene, og andre
    sveip skal LUKKE funnet. Uten dette steget var lukkingen aldri målt.
  * EVIDENS — hver dør skrev sin hendelse i revisjonsloggen, med aktør
    og egen `input_hash`.

Skriptet dømmer ingenting: `bestatt` er produsentens påstand, og
`manifestskjema._grenser_m19_fasit` validerer.

BRUK (på verten som root, `staging.env` sourcet):
    python deploy/staging/m19-fasit-artefakt.py [--ut …]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import psycopg  # noqa: E402

import m19_fasit as fasit  # noqa: E402
from manifestskjema import (_sjekk_grenser, m19_fasit_bevisrot_sha256,  # noqa: E402
                            valider_artefaktformat)

KRAV = "m19-fasit-v1"


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def sveip(sv, grense: int = 500) -> dict:
    """Sveipen gjennom modulens egen dør — uten tenantkontekst, som
    arbeideren kaller den."""
    sv.execute("SELECT set_config('disponit.tenant', '', true),"
               " set_config('disponit.aktor', %s, true)", (fasit.AKTOR,))
    rad = sv.execute("SELECT * FROM m19_sveip_adresser(%s)",
                     (grense,)).fetchone()
    sv.commit()
    return {"tenanter": int(rad[0]), "nye": int(rad[1]),
            "oppdaterte": int(rad[2]), "lukkede": int(rad[3])}


def evidens(rt, tenanter: list[str]) -> dict:
    """M-19s egne hendelser i evidenskjeden — antall, og hvor mange som
    mangler aktør eller deler `input_hash` med en annen."""
    talt: dict[str, int] = {}
    uten_aktor = 0
    hasher: set[str] = set()
    dubletter = 0
    for tenant in tenanter:
        fasit._sk(rt, tenant)
        rader = rt.execute(
            "SELECT handling, aktor, input_hash FROM revisjonslogg"
            " WHERE tenant=%s AND kilde='m19_adresse'", (tenant,)).fetchall()
        rt.rollback()
        for handling, aktor, ih in rader:
            talt[handling] = talt.get(handling, 0) + 1
            if not (aktor or "").strip():
                uten_aktor += 1
            if ih in hasher:
                dubletter += 1
            hasher.add(ih)
    return {"per_handling": talt, "uten_aktor": uten_aktor,
            "delte_input_hash": dubletter,
            "totalt": sum(talt.values())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vert", default="disponit.com")
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    rt_dsn = os.environ.get("DATABASE_URL")
    sv_dsn = os.environ.get("DISPONIT_ADRESSESVEIP_URL")
    m_dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not (rt_dsn and sv_dsn and m_dsn):
        raise SystemExit("AVBRUTT: DATABASE_URL/DISPONIT_ADRESSESVEIP_URL/"
                         "DISPONIT_MIGRATOR_URL mangler")
    rt = psycopg.connect(rt_dsn)          # dørene, som flaten
    sv = psycopg.connect(sv_dsn)          # sveipen, som arbeideren
    m = psycopg.connect(m_dsn)            # MÅLINGEN av funntabellen
    runde = secrets.token_hex(4)
    rigg = fasit.forbered(rt, runde)
    _log(f"runde {runde}: {len(rigg['subjekter'])} subjekter i"
         f" {rigg['med_krav']} / {rigg['uten_krav']}")

    forste = sveip(sv)
    _log(f"sveip 1: {forste}")
    for_dom = fasit.maal(m, rigg)
    rent_funn_for = next(
        (len(r["fikk"]) for r in for_dom["per_subjekt"] if r["ventet"] is None), 0)
    _log(f"etter sveip 1: det rene subjektet har {rent_funn_for} åpent funn")

    # KONTROLLEN KOMMER HER, mellom kjøringene: da har andre sveip noe å
    # lukke, og aksen er målt i stedet for påstått.
    fasit.kontroller_ren(rt, rigg)
    andre = sveip(sv)
    _log(f"sveip 2: {andre}")
    dom = fasit.maal(m, rigg)
    _log(f"funnavvik etter sveip 2: {dom['avvik'] or 'ingen'}")
    etter = dom

    ev = evidens(rt, [rigg["med_krav"], rigg["uten_krav"]])
    ts = datetime.now(timezone.utc).isoformat()
    art = {
        "krav_id": KRAV, "ts": ts, "bestatt": True,
        "oppsett": {
            "modul": "m19_adresse", "vert": a.vert, "runde": runde,
            "tenanter": [rigg["med_krav"], rigg["uten_krav"]],
            "sett_sha256": fasit.sett_sha256(),
            "bevisrot_sha256": m19_fasit_bevisrot_sha256(),
            "ukontrollert_dogn": fasit.UKONTROLLERT_DOGN,
            "gyldig_dogn": fasit.GYLDIG_DOGN,
            "godkjente_metoder": list(fasit.GODKJENTE_METODER),
        },
        "maalt": {
            "subjekter": len(rigg["subjekter"]),
            "funnavvik": len(dom["avvik"]),
            "avviksliste": dom["avvik"],
            "per_subjekt": dom["per_subjekt"],
            "sveip1_nye": forste["nye"], "sveip1_tenanter": forste["tenanter"],
            "sveip2_nye": andre["nye"], "sveip2_oppdaterte": andre["oppdaterte"],
            "sveip2_lukkede": andre["lukkede"],
            "funnavvik_etter_andre": len(etter["avvik"]),
            "rent_funn_for_kontroll": rent_funn_for,
            "evidens_totalt": ev["totalt"],
            "evidens_uten_aktor": ev["uten_aktor"],
            "evidens_delte_input_hash": ev["delte_input_hash"],
            "evidens_per_handling": ev["per_handling"],
        },
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
