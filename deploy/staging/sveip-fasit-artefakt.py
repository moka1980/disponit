#!/usr/bin/env python3
"""Fasitartefaktet for en SVEIPMODUL — samme produsent for alle.

Driver modulens eget sett (`riggmodul`) gjennom modulens egne dører,
kjører sveipen gjennom `modul.kjor()` som arbeideren gjør, og måler fire
akser:

  * FUNNAVVIK — hvert subjekt fikk NØYAKTIG den funntypen fasiten sier,
    og de rene fikk ingen. Re-regnet av funntabellen, aldri av et
    aggregat.
  * IDEMPOTENS — andre kjøring gir null NYE funn. En sveip som finner
    det samme på nytt hver runde fyller køen med duplikater.
  * LUKKING — det rene subjektet fødes med et funn i første sveip;
    rettelsen kommer MELLOM kjøringene, og andre sveip skal LUKKE
    funnet. Uten dette steget er lukkeveien aldri målt.
  * EVIDENS — hver dør skrev sin hendelse i revisjonsloggen, med aktør
    og egen `input_hash`.

RIGGKONTRAKTEN en modul må fylle: `forbered(rt, runde)`,
`riggtenanter(rigg)`, `kontroller_ren(rt, rigg)`, `maal(m, rigg)`,
`sett_sha256()`, og konstantene `AKTOR`, `EVIDENSKILDE` og `RENSES` —
merkelappen på subjektet som fødes med et funn og renses mellom
kjøringene.

SVEIPEN KALLES GJENNOM MODULEN, ikke rett på SQL-funksjonen: låsen,
kontraktvalideringen og committen er en del av det som måles.

Skriptet dømmer ingenting: `bestatt` er produsentens påstand, og
`manifestskjema._grenser_generisk_fasit` validerer.

BRUK (på verten som root, `staging.env` sourcet):
    python deploy/staging/sveip-fasit-artefakt.py --modul m27_lager [--ut …]
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

from manifestskjema import (SVEIPMODULER, _sjekk_grenser,  # noqa: E402
                            sveip_fasit_bevisrot_sha256,
                            valider_artefaktformat)


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def evidens(rt, rigg_modul, tenanter: list[str]) -> dict:
    """Modulens egne hendelser i evidenskjeden — antall, og hvor mange
    som mangler aktør eller deler `input_hash` med en annen."""
    talt: dict[str, int] = {}
    uten_aktor = 0
    hasher: set[str] = set()
    dubletter = 0
    for tenant in tenanter:
        # KONTEKSTEN SETTES HER, ikke gjennom riggens private hjelper:
        # produsenten kjenner tenanten og riggens aktør, og et generisk
        # skript som griper inn i en modulfils understrek-navn er en
        # kontrakt ingen har skrevet ned.
        rt.execute("SELECT set_config('disponit.tenant', %s, true),"
                   " set_config('disponit.aktor', %s, true)",
                   (tenant, rigg_modul.AKTOR))
        rader = rt.execute(
            "SELECT handling, aktor, input_hash FROM revisjonslogg"
            " WHERE tenant=%s AND kilde=%s",
            (tenant, rigg_modul.EVIDENSKILDE)).fetchall()
        rt.rollback()
        for handling, aktor, ih in rader:
            talt[handling] = talt.get(handling, 0) + 1
            if not (aktor or "").strip():
                uten_aktor += 1
            if ih in hasher:
                dubletter += 1
            hasher.add(ih)
    return {"per_handling": talt, "uten_aktor": uten_aktor,
            "delte_input_hash": dubletter, "totalt": sum(talt.values())}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modul", required=True)
    ap.add_argument("--vert", default="disponit.com")
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    if a.modul not in SVEIPMODULER:
        raise SystemExit(f"AVBRUTT: {a.modul} er ikke registrert som"
                         " sveipmodul i manifestskjema.SVEIPMODULER")
    k = SVEIPMODULER[a.modul]
    krav = k["fasit_krav"]
    rt_dsn = os.environ.get("DATABASE_URL")
    sv_dsn = os.environ.get(k["dsn_variabel"])
    m_dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not (rt_dsn and sv_dsn and m_dsn):
        raise SystemExit("AVBRUTT: DATABASE_URL/DISPONIT_MIGRATOR_URL/"
                         f"{k['dsn_variabel']} mangler")
    rigg_modul = __import__(k["riggmodul"])
    modul = __import__(f"drift.{k['modul_fil']}", fromlist=["kjor"])

    rt = psycopg.connect(rt_dsn)          # dørene, som flaten
    sv = psycopg.connect(sv_dsn)          # sveipen, som arbeideren
    m = psycopg.connect(m_dsn)            # MÅLINGEN av funntabellen
    runde = secrets.token_hex(4)
    rigg = rigg_modul.forbered(rt, runde)
    tenanter = rigg_modul.riggtenanter(rigg)
    _log(f"runde {runde}: {len(rigg['subjekter'])} subjekter i"
         f" {len(tenanter)} tenanter")

    def sveip(merkelapp: str):
        res = modul.kjor(sv)
        if res.hoppet_over:
            raise SystemExit(f"AVBRUTT: {merkelapp} ble HOPPET OVER"
                             " (arbeidernøkkelen opptatt) — en fasit mot en"
                             " sveip som aldri kjørte er ingen fasit")
        if res.feilet:
            raise SystemExit(f"AVBRUTT: {merkelapp} FEILET")
        _log(f"{merkelapp}: tenanter={res.tenanter} nye={res.nye}"
             f" oppdaterte={res.oppdaterte} lukkede={res.lukkede}")
        return res

    forste = sveip("sveip 1")
    for_dom = rigg_modul.maal(m, rigg)
    # NAVNET KOMMER FRA RIGGEN, ikke fra en streng her. M-19 og M-27
    # kaller subjektet `ren`, M-25 kaller det `rent` — en hardkodet
    # merkelapp i en generisk flate leste bare null, og aksen ville sett
    # umålt ut på en modul der mekanismen virket.
    rent_funn_for = next(
        (len(r["fikk"]) for r in for_dom["per_subjekt"]
         if r["merke"] == rigg_modul.RENSES), 0)
    _log(f"etter sveip 1: det rene subjektet har {rent_funn_for} åpent funn")

    # RETTELSEN KOMMER HER, mellom kjøringene: da har andre sveip noe å
    # lukke, og aksen er målt i stedet for påstått.
    rigg_modul.kontroller_ren(rt, rigg)
    andre = sveip("sveip 2")
    dom = rigg_modul.maal(m, rigg)
    _log(f"funnavvik etter sveip 2: {dom['avvik'] or 'ingen'}")

    ev = evidens(rt, rigg_modul, tenanter)
    ts = datetime.now(timezone.utc).isoformat()
    art = {
        "krav_id": krav, "ts": ts, "bestatt": True,
        "oppsett": {
            "modul": a.modul, "vert": a.vert, "runde": runde,
            "tenanter": tenanter, "riggmodul": k["riggmodul"],
            "sett_sha256": rigg_modul.sett_sha256(),
            "bevisrot_sha256": sveip_fasit_bevisrot_sha256(a.modul),
            "evidenskilde": rigg_modul.EVIDENSKILDE,
        },
        "maalt": {
            "subjekter": len(rigg["subjekter"]),
            "funnavvik": len(dom["avvik"]),
            "avviksliste": dom["avvik"],
            "per_subjekt": dom["per_subjekt"],
            "sveip1_nye": forste.nye, "sveip1_tenanter": forste.tenanter,
            "sveip2_nye": andre.nye, "sveip2_oppdaterte": andre.oppdaterte,
            "sveip2_lukkede": andre.lukkede,
            "funnavvik_etter_andre": len(dom["avvik"]),
            "rent_funn_for_kontroll": rent_funn_for,
            "evidens_totalt": ev["totalt"],
            "evidens_uten_aktor": ev["uten_aktor"],
            "evidens_delte_input_hash": ev["delte_input_hash"],
            "evidens_per_handling": ev["per_handling"],
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
