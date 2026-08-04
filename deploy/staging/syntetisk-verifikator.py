#!/usr/bin/env python3
"""Syntetisk verifikator — STAGING BARE. Beviser tofaseprotokollens fase 1.

Plukker verifikasjonsoppdrag og poster signerte attestasjoner. Den er
søsteren til `syntetisk-eiermodul.py`, og har den samme viktigste
egenskapen: **den skriver ALDRI i databasen.** Ikke én INSERT, ingen
databasedriver — alt går gjennom de to ordinære endepunktene.

Grunnen er den samme, og den er verdt å gjenta: en sele som skriver i
databasen selv beviser at VI kan skrive i vår egen database. Den beviser
ingenting om at protokollen virker.

FORSKJELLEN fra eiermodulen er hva den har lov til: verifikatoren
KONTROLLERER og ATTESTERER, den utfører aldri en forretningshandling. Fase
1 har null forretningsfullmakter. Attestasjonen den signerer er selve
beviset — og den binder til tenant, sak, vilkår, ressurs og målhandling, så
den kan ikke flyttes til en annen sak.

BRUK:
    DISPONIT_VERIFIKATOR_TOKEN=... DISPONIT_VERIFIKATOR_NOKKEL=... \\
    python3 deploy/staging/syntetisk-verifikator.py --api http://127.0.0.1:8099
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "platform/core"))

from policy_validator import attestering  # noqa: E402
import oppdragskontrakt                    # noqa: E402

FORBUDT_MILJO = "produksjon"


class Nekt(RuntimeError):
    """Modulen skal ikke starte."""


def krev_staging() -> None:
    if os.environ.get("DISPONIT_MILJO") == FORBUDT_MILJO:
        raise Nekt(
            "den syntetiske verifikatoren er STAGING-ONLY og nekter å kjøre"
            " med DISPONIT_MILJO=produksjon — den signerer attestasjoner for"
            " vilkår ingen har kontrollert")


def bygg_konvolutt(o: dict, *, verifikator: str, resultat: str,
                   levetid_min: int = 60) -> dict:
    """Den signerte konvolutten. Den ER attestasjonen.

    Feltnavnene er motorens (`tenant_id`, `handling`, `policy_id`,
    `verifikator`, `resultat`), ikke noen egen dialekt — nettopp fordi
    fase 2 sender dette objektet rett inn i policymotoren. Hadde
    konvolutten hatt sine egne navn, måtte API-et bygget om og signert på
    nytt, og API-et er ingen verifikator.

    `handling` er MÅLHANDLINGEN fra oppdraget, ikke verifikasjonshandlingen.
    Attestasjonen skal binde til det fase 2 ber om.
    """
    p = o["payload"]
    naa = datetime.now(timezone.utc)
    positiv = resultat == "positiv"
    return {
        "protokollversjon": oppdragskontrakt.PROTOKOLLVERSJON,
        "kvitteringstype": "verifikasjonskvittering_v1",
        # --- motorens bindingsfelter ---------------------------------
        "tenant_id": o["tenant"],
        "handling": p["maalhandling"],
        "vilkaar": p["vilkaar"],
        "ressurs_id": p["ressurs_id"],
        "policy_id": p["policy_id"],
        "utstedt": (naa - timedelta(seconds=5)).isoformat(),
        "utloper": (naa + timedelta(minutes=levetid_min)).isoformat(),
        "jti": secrets.token_hex(16),
        "verifikator": verifikator,
        "resultat": positiv,
        # --- fase-1-bindingene ---------------------------------------
        "oppdrag_id": o["oppdrag_id"],
        "unntak_id": o["unntak_id"],
        "fase1_repair_operation_id": o["repair_operation_id"],
        "verification_generation": o["verification_generation"],
        "attestert_resultat": resultat,
        "nokkel_id": o["_nokkel_id"],
    }


def _post(api: str, sti: str, token: str, kropp: dict):
    import httpx
    return httpx.post(f"{api.rstrip('/')}{sti}",
                      headers={"authorization": f"Bearer {token}",
                               "content-type": "application/json"},
                      content=json.dumps(kropp, ensure_ascii=False).encode(),
                      timeout=30.0)


def verifiser_ett(api: str, token: str, verifikator: str, nokkel_id: str,
                  hemmelighet: str, *, resultat: str = "positiv") -> dict | None:
    """Én runde: claim ett verifikasjonsoppdrag, post én signert attestasjon.

    Returnerer metadata, eller None når køen er tom. Attestasjonens innhold
    logges ALDRI — bare id-er.
    """
    r = _post(api, "/v1/oppdrag/claim", token, {})
    if r.status_code == 204:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"claim feilet: {r.status_code} {r.text[:200]}")
    o = r.json()
    o["_nokkel_id"] = nokkel_id
    if o.get("verification_generation") is None:
        raise RuntimeError(
            "claim-responsen mangler verification_generation — uten den kan"
            " attestasjonen ikke bindes til generasjonen som bestilte den")

    konvolutt = attestering.signer(
        bygg_konvolutt(o, verifikator=verifikator, resultat=resultat),
        nokkel_id, hemmelighet)

    k = _post(api, "/v1/oppdrag/kvittering", token,
              {"kvittering_jti": o["kvittering_jti"], "konvolutt": konvolutt})
    if k.status_code not in (200, 202):
        raise RuntimeError(f"attestasjon avvist: {k.status_code} {k.text[:300]}")
    return {"oppdrag_id": o["oppdrag_id"], "unntak_id": o["unntak_id"],
            "vilkaar": o["payload"]["vilkaar"], "svar": k.status_code,
            "status": k.json().get("status")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api", default=os.environ.get("DISPONIT_API_URL",
                                                   "http://127.0.0.1:8099"))
    p.add_argument("--runder", type=int, default=0, help="0 = til køen er tom")
    p.add_argument("--intervall", type=float, default=0.5)
    p.add_argument("--resultat", default="positiv",
                   choices=("positiv", "negativ"))
    a = p.parse_args(argv)

    krev_staging()
    token = os.environ.get("DISPONIT_VERIFIKATOR_TOKEN", "")
    hemmelighet = os.environ.get("DISPONIT_VERIFIKATOR_NOKKEL", "")
    verifikator = os.environ.get("DISPONIT_VERIFIKATOR_ID", "v_fordring")
    nokkel_id = os.environ.get("DISPONIT_VERIFIKATOR_NOKKELID", "k1")
    if not token or len(hemmelighet) < 32:
        print("AVBRUTT: DISPONIT_VERIFIKATOR_TOKEN og _NOKKEL (>=32 tegn) kreves",
              file=sys.stderr)
        return 2

    behandlet, runde = 0, 0
    while a.runder == 0 or runde < a.runder:
        runde += 1
        res = verifiser_ett(a.api, token, verifikator, nokkel_id, hemmelighet,
                            resultat=a.resultat)
        if res is None:
            if a.runder == 0:
                break
            time.sleep(a.intervall)
            continue
        behandlet += 1
        print(json.dumps(res, ensure_ascii=False), flush=True)
    print(f"ferdig: {behandlet} vilkår attestert", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
