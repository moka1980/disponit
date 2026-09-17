#!/usr/bin/env python3
"""Feilinjisering for en SVEIPMODUL — samme produsent for alle.

Punktet heter «feilinjisering til unntakskø», men for en sveip er
spørsmålet et annet enn for en eiermodul med oppdrag: sveipen har ingen
kvittering å forfalske. Det den KAN gjøre galt, er å la en halv kjøring
se ut som en hel — et funn skrevet mens resten rullet tilbake, en
kandidat som ble borte, en feil som ikke meldte fra.

Derfor måles tre ting på en ekte, injisert feil:

  1. RENT UTFALL — kjøringen melder `feilet`, aldri taushet og aldri et
     halvt svar (SP-3: en driftsfeil er ikke et verdikt).
  2. INGEN DELVIS SKRIVING — registerets funn står NØYAKTIG som før:
     samme antall åpne, samme typer, ingen lukket, ingen ny.
  3. ALARMEN — to sammenhengende feil løfter alarmen, én gjør det ikke.
     En stille sveip som feiler hver runde er verre enn en som stopper.

Feilen injiseres UTENFRA, i transporten: kjøringen får RUNTIME-rollens
tilkobling, som migrasjon 112 eksplisitt har revokert EXECUTE på
sveipedøra fra. Det er den ekte funksjonen som kalles, den ekte feilveien
som tas, og ingenting i modulen er endret for målingens skyld — en
injeksjon som krever en bryter inne i koden måler bryteren.

INJEKSJONEN HAR SIN EGEN POSITIVE KONTROLL: basen spørres om rollen
faktisk mangler EXECUTE (`has_function_privilege`), og svaret står i
artefaktet. En feilet kjøring beviser ingenting hvis den kunne kommet av
en skrivefeil i tilkoblingsstrengen.

BRUK (på verten som root, `staging.env` sourcet):
    python deploy/staging/sveip-feilinjisering.py --modul m19_adresse [--ut …]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (SVEIPMODULER, _sjekk_grenser,  # noqa: E402
                            sveip_feilinjisering_bevisrot_sha256,
                            valider_artefaktformat)


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def tilstand(m, k: dict) -> dict:
    """Registerets funn, talt av migratoren TENANT FOR TENANT: antall
    åpne, antall lukkede, og summen av funntypene.

    Kontekst per tenant, ikke én telling over hele tabellen: funntabellen
    har FORCE RLS også mot eieren, så en spørring uten tenantkontekst gir
    null rader — og null lik null hadde sett ut som et urørt register
    uansett hva kjøringen gjorde.

    MÅLINGEN TAR PÅ SEG EIERROLLEN, som er den eneste med
    kryss-tenant-lesing av subjektlisten (112, snevert: bare den
    tabellen, bare SELECT, bare uten tenantkontekst). Uten den ser også
    tenantlisten tom ut, og tellingen blir null av feil grunn.

    Et tall alene holder heller ikke: ett funn kunne blitt lukket og et
    annet åpnet i samme feilende kjøring, og summen stått stille."""
    # SET ROLE ER TRANSAKSJONELT, og `rollback()` under ville tatt rollen
    # av igjen ved neste runde i løkka. Den committes derfor én gang.
    m.execute(f"SET ROLE {k['maalerolle']}")
    m.commit()
    m.execute("SELECT set_config('disponit.tenant', '', true)")
    tenanter = [r[0] for r in m.execute(
        f"SELECT DISTINCT tenant FROM {k['tenantkilde']} ORDER BY 1"
    ).fetchall()]
    m.rollback()
    per: dict[str, list[int]] = {}
    for tenant in tenanter:
        m.execute("SELECT set_config('disponit.tenant', %s, true)", (tenant,))
        rader = m.execute(
            f"SELECT funntype, count(*) FILTER (WHERE apen),"
            f" count(*) FILTER (WHERE NOT apen) FROM {k['funntabell']}"
            " WHERE tenant = %s GROUP BY 1", (tenant,)).fetchall()
        m.rollback()
        for funntype, apne, lukkede in rader:
            rad = per.setdefault(funntype, [0, 0])
            rad[0] += int(apne)
            rad[1] += int(lukkede)
    return {"per_type": {t: list(v) for t, v in sorted(per.items())},
            "apne": sum(v[0] for v in per.values()),
            "lukkede": sum(v[1] for v in per.values()),
            "tenanter": len(tenanter)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modul", required=True)
    ap.add_argument("--vert", default=os.uname().nodename)
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    if a.modul not in SVEIPMODULER:
        raise SystemExit(f"AVBRUTT: {a.modul} er ikke registrert som"
                         " sveipmodul i manifestskjema.SVEIPMODULER")
    k = SVEIPMODULER[a.modul]
    m_dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    sv_dsn = os.environ.get(k["dsn_variabel"])
    uten_dsn = os.environ.get(k["dsn_uten_execute"])
    if not (m_dsn and sv_dsn and uten_dsn):
        raise SystemExit(
            f"AVBRUTT: DISPONIT_MIGRATOR_URL/{k['dsn_variabel']}/"
            f"{k['dsn_uten_execute']} mangler")
    m = psycopg.connect(m_dsn)
    modul = __import__(f"drift.{k['modul_fil']}", fromlist=["kjor"])

    # FØR: registerets tilstand, talt av radene.
    for_tilstand = tilstand(m, k)
    _log(f"før: {for_tilstand['apne']} åpne, {for_tilstand['lukkede']}"
         f" lukkede over {for_tilstand['tenanter']} tenanter")

    # DEN EKTE VEIEN FØRST: en kjøring som skal lykkes, så vi vet at
    # riggen virker og at feilen etterpå er feilen vi injiserte.
    sv = psycopg.connect(sv_dsn)
    frisk = modul.kjor(sv)
    sv.close()
    _log(f"frisk kjøring: feilet={frisk.feilet} alarm={frisk.alarm_utlost}")
    if frisk.hoppet_over:
        raise SystemExit("AVBRUTT: den friske kjøringen ble HOPPET OVER"
                         " (arbeidernøkkelen var opptatt) — en måling mot"
                         " en sveip som aldri kjørte er ingen måling")
    etter_frisk = tilstand(m, k)

    # INJEKSJONEN: samme kode, samme dør — men runtime-tilkoblingen, som
    # ikke har EXECUTE. Feilen kommer utenfra, som en ekte driftsfeil.
    uten = psycopg.connect(uten_dsn)
    rolle, har_execute = uten.execute(
        "SELECT current_user, has_function_privilege(current_user, %s,"
        " 'EXECUTE')", (k["sveipedor"],)).fetchone()
    uten.rollback()
    _log(f"injeksjonsrolle: {rolle} har_execute={har_execute}")
    forste = modul.kjor(uten, tidligere_feil=0)
    andre = modul.kjor(uten, tidligere_feil=1)
    uten.close()
    _log(f"injisert 1: feilet={forste.feilet} alarm={forste.alarm_utlost}")
    _log(f"injisert 2: feilet={andre.feilet} alarm={andre.alarm_utlost}")

    etter = tilstand(m, k)
    urort = (etter == etter_frisk)
    ts = datetime.now(timezone.utc).isoformat()
    art = {
        "krav_id": k["feilinjisering_krav"], "ts": ts, "bestatt": True,
        "oppsett": {"modul": a.modul, "vert": a.vert,
                    "funntabell": k["funntabell"],
                    "tenantkilde": k["tenantkilde"],
                    "maalerolle": k["maalerolle"],
                    "sveipedor": k["sveipedor"],
                    "injeksjonsrolle": str(rolle),
                    "injeksjon": f"tilkobling som {k['rolle_uten_execute']}"
                                 f" — uten EXECUTE på {k['sveipedor']}",
                    "bevisrot_sha256": sveip_feilinjisering_bevisrot_sha256()},
        "maalt": {
            "frisk_feilet": bool(frisk.feilet),
            "injeksjonsrolle_har_execute": bool(har_execute),
            "injiserte_kjoringer": 2,
            "injisert_feilet": int(bool(forste.feilet)) + int(bool(andre.feilet)),
            "alarm_etter_forste": bool(forste.alarm_utlost),
            "alarm_etter_andre": bool(andre.alarm_utlost),
            "apne_for": etter_frisk["apne"], "apne_etter": etter["apne"],
            "lukkede_for": etter_frisk["lukkede"],
            "lukkede_etter": etter["lukkede"],
            "registeret_urort": urort,
            "tenanter": etter["tenanter"],
            "per_type_for": etter_frisk["per_type"],
            "per_type_etter": etter["per_type"],
        },
    }
    feil = valider_artefaktformat(art, k["feilinjisering_krav"]) \
        + _sjekk_grenser(k["feilinjisering_krav"], art)
    art["bestatt"] = not feil
    if feil:
        art["feil"] = feil
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"{k['feilinjisering_krav']}-"
                    f"{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True)
                  + "\n", encoding="utf-8")
    _log(f"skrev {ut} (bestatt={art['bestatt']})")
    for f in feil:
        _log("  FEIL:", f)
    return 0 if not feil else 1


if __name__ == "__main__":
    raise SystemExit(main())
