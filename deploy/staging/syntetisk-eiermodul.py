#!/usr/bin/env python3
"""Syntetisk eiermodul — STAGING BARE. Beviser outbox-protokollen.

Hvorfor den finnes: det finnes ingen ekte eiermoduler ennå, og M-37 kan
derfor ikke erklære en forretningshandling «utført». v2-delta pkt. 2 stopper
der sannheten stopper — protokollen leveres, ikke påstanden om at
produksjonsutførere finnes. Denne modulen plukker oppdrag og poster signerte
kvitteringer, slik at protokollen kan bevises ende-til-ende UTEN å late som.

DEN VIKTIGSTE EGENSKAPEN ER HVA DEN IKKE GJØR:

  Den skriver ALDRI i databasen. Ikke én INSERT, ikke én UPDATE, ingen
  databasedriver i det hele tatt. Alt går gjennom de to ordinære
  endepunktene `/v1/oppdrag/claim` og `/v1/oppdrag/kvittering`, med et
  ordinært modultoken og en registrert verifikatornøkkel.

  Grunnen: en sele som skriver i databasen selv beviser at VI kan skrive i
  vår egen database. Den beviser ingenting om at protokollen virker — og
  feilinjiseringsartefaktet ville sagt «ende-til-ende bestått» om noe helt
  annet. Derfor er dette evidensbevis 8 (v3-delta), og derfor håndheves det
  av en statisk sjekk i testsuiten
  (`test_port10_syntetisk_eiermodul_skriver_aldri_direkte_i_databasen`).

BRUK:
    DISPONIT_EIERMODUL_TOKEN=... DISPONIT_EIERMODUL_NOKKEL=... \\
    python3 deploy/staging/syntetisk-eiermodul.py --api http://127.0.0.1:8099
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "platform/core"))

from policy_validator import jcs   # noqa: E402  (etter sys.path)

#: Staging-sperren. Modulen skal ikke kunne startes mot produksjon ved et
#: uhell — og en instruks om det i en README er ingen sperre.
FORBUDT_MILJO = "produksjon"


class Nekt(RuntimeError):
    """Modulen skal ikke starte."""


def krev_staging() -> None:
    if os.environ.get("DISPONIT_MILJO") == FORBUDT_MILJO:
        raise Nekt(
            "den syntetiske eiermodulen er STAGING-ONLY og nekter å kjøre med"
            " DISPONIT_MILJO=produksjon — den signerer kvitteringer for"
            " handlinger ingen har utført")


def signer_kvittering(kvittering: dict, nokkel_id: str, hemmelighet: str) -> dict:
    """HMAC-SHA256 over RFC 8785-kanoniske bytes, uten signaturfeltet.

    NØYAKTIG samme mekanisme som attestasjonene bruker
    (`policy_validator.attestering`). Det er med vilje: to
    signaturmekanismer i samme system betyr at den ene før eller siden blir
    svakere enn den andre uten at noen merker det.
    """
    ut = dict(kvittering)
    ut["kanonisering"] = jcs.KANONISERING
    uten = {k: v for k, v in ut.items() if k != "signatur"}
    mac = hmac.new(hemmelighet.encode("utf-8"), jcs.kanoniske_bytes(uten),
                   hashlib.sha256).hexdigest()
    ut["signatur"] = {"alg": "HMAC-SHA256", "nokkel_id": nokkel_id,
                      "verdi": mac}
    return ut


def _post(api: str, sti: str, token: str, kropp: dict):
    import httpx
    return httpx.post(f"{api.rstrip('/')}{sti}",
                      headers={"authorization": f"Bearer {token}",
                               "content-type": "application/json"},
                      content=json.dumps(kropp, ensure_ascii=False).encode(),
                      timeout=30.0)


def plukk_og_kvitter(api: str, token: str, verifikator: str, nokkel_id: str,
                     hemmelighet: str, *, resultat: str = "utfort") -> dict | None:
    """Én runde: claim ett oppdrag, post én signert kvittering.

    Returnerer oppdragsmetadataene, eller None når køen er tom. Klartekst
    fra oppdraget logges ALDRI — bare id-er. Canary-testen i
    feilinjiseringen planter kjente verdier i payloaden og feiler hvis de
    dukker opp i logger eller på disk.
    """
    r = _post(api, "/v1/oppdrag/claim", token, {})
    if r.status_code == 204:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"claim feilet: {r.status_code} {r.text[:200]}")
    o = r.json()

    kvittering = signer_kvittering({
        "oppdrag_id": o["oppdrag_id"],
        "tenant": o["tenant"],
        # Kvitteringskapabiliteten fra claim-responsen. Uten den slipper vi
        # ikke inn i kvitteringsporten i det hele tatt — modultokenet alene
        # er ikke nok, og skal ikke være det: det er langlivet og gjelder
        # alle modulens oppdrag, mens denne gjelder DETTE.
        "kvittering_jti": o["kvittering_jti"],
        "repair_operation_id": o["repair_operation_id"],
        # Owner-fencing (v4-delta pkt. 3): kvitteringen bærer den
        # GJELDENDE owner-claimen. Uten disse to feltene kan en utdatert
        # utfører avslutte saken.
        "owner_claim_id": o["owner_claim_id"],
        "owner_generation": o["owner_generation"],
        "resultat": resultat,
        # Ressursbindingen: kvitteringen sier hvilken ressurs som faktisk
        # ble rørt, ikke bare at «noe» ble gjort.
        "ressurs_id": o["payload"].get("ressurs_id"),
        "verifikator": verifikator,
    }, nokkel_id, hemmelighet)

    k = _post(api, "/v1/oppdrag/kvittering", token, kvittering)
    if k.status_code not in (200, 202):
        raise RuntimeError(f"kvittering avvist: {k.status_code} {k.text[:200]}")
    return {"oppdrag_id": o["oppdrag_id"], "unntak_id": o["unntak_id"],
            "svar": k.status_code, "status": k.json().get("status")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api", default=os.environ.get("DISPONIT_API_URL",
                                                   "http://127.0.0.1:8099"))
    p.add_argument("--runder", type=int, default=0,
                   help="0 = til køen er tom")
    p.add_argument("--intervall", type=float, default=0.5)
    p.add_argument("--resultat", default="utfort", choices=("utfort", "feilet"))
    a = p.parse_args(argv)

    krev_staging()
    token = os.environ.get("DISPONIT_EIERMODUL_TOKEN", "")
    hemmelighet = os.environ.get("DISPONIT_EIERMODUL_NOKKEL", "")
    verifikator = os.environ.get("DISPONIT_EIERMODUL_ID", "syntetisk-eiermodul")
    nokkel_id = os.environ.get("DISPONIT_EIERMODUL_NOKKELID", "n1")
    if not token or len(hemmelighet) < 32:
        print("AVBRUTT: DISPONIT_EIERMODUL_TOKEN og _NOKKEL (>=32 tegn) kreves",
              file=sys.stderr)
        return 2

    behandlet, runde = 0, 0
    while a.runder == 0 or runde < a.runder:
        runde += 1
        res = plukk_og_kvitter(a.api, token, verifikator, nokkel_id,
                               hemmelighet, resultat=a.resultat)
        if res is None:
            if a.runder == 0:
                break
            time.sleep(a.intervall)
            continue
        behandlet += 1
        print(json.dumps(res, ensure_ascii=False), flush=True)
    print(f"ferdig: {behandlet} oppdrag kvittert", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
