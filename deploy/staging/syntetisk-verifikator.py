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


def bygg_attestasjon(o: dict, vilkaar: str, *, verifikator: str,
                     positiv: bool, levetid_min: int = 60,
                     alder_s: int = 5,
                     verdier: dict | None = None) -> dict:
    """ÉN indre attestasjon — ett vilkår, ett faktum, én signatur.

    Feltnavnene er MOTORENS (`tenant_id`, `handling`, `vilkaar`,
    `ressurs_id`, `policy_id`, `verifikator`, `resultat`), ikke en egen
    dialekt: fase 2 sender dette objektet uendret inn i policymotoren.
    Hadde attestasjonen hatt egne navn, måtte API-et bygget om og signert
    på nytt — og API-et er ingen verifikator.

    `handling` er MÅLHANDLINGEN, ikke verifikasjonshandlingen. Beviset
    skal binde til det fase 2 faktisk ber om; bandt det til
    `verifiser.<vilkår>`, ville motoren gitt `attestasjon_feil_handling`.
    """
    p = o["payload"]
    naa = datetime.now(timezone.utc)
    return {
        "tenant_id": o["tenant"],
        "handling": p["maalhandling"],
        "vilkaar": vilkaar,
        "ressurs_id": p["ressurs_id"],
        "policy_id": p["policy_id"],
        "utstedt": (naa - timedelta(seconds=alder_s)).isoformat(),
        "utloper": (naa + timedelta(minutes=levetid_min)).isoformat(),
        "jti": secrets.token_hex(16),
        "verifikator": verifikator,
        "resultat": positiv,
        # Et vilkår med `min:` i policyen krever en MÅLT verdi, ikke bare et
        # ja. Verdien kommer fra kommandolinjen, ikke fra oppdraget: en
        # verifikator som lot den som bestiller kontrollen bestemme hva
        # svaret skal bli, ville ikke vært en verifikator. I produksjon er
        # dette modulens egen måling mot den autoritative kilden.
        **({"verdi": verdier[vilkaar]} if verdier and vilkaar in verdier else {}),
    }


def bygg_konvolutt(o: dict, *, verifikator: str, nokkel_id: str,
                   hemmelighet: str, resultat: str = "positiv",
                   permanent: bool = False, levetid_min: int = 60,
                   alder_s: int = 5, verdier: dict | None = None) -> dict:
    """ÉN ytre konvolutt over HELE settet (Scope v2 pkt. 3.1).

    To lag med hvert sitt formål, og de er ikke utbyttbare:

    - Den YTRE signaturen binder settet til oppdraget, saken og
      generasjonen. Den er kvitteringens integritet — det er den API-et
      verifiserer, og den som gjør at et vilkår ikke kan legges til eller
      fjernes underveis.
    - De INDRE signaturene gjør hver attestasjon brukbar som bevis for
      policymotoren i fase 2, som verifiserer hver enkelt for seg.

    Verifikatoren sender ALLE vilkårene i settet i én kvittering. Sender
    den færre, blir generasjonen `negativ` og ingen bevis lagres — et
    delvis sett er ikke et sett.
    """
    p = o["payload"]
    positiv = resultat == "positiv"
    status = {"positiv": "attestert", "negativ": "negativ",
              "ikke_attesterbar": "ikke_attesterbar"}[resultat]
    elementer = []
    for vilkaar in p["vilkaar_sett"]:
        e = {"vilkaar": vilkaar, "status": status, "permanent": permanent}
        if status == "attestert":
            e["attestasjon"] = attestering.signer(
                bygg_attestasjon(o, vilkaar, verifikator=verifikator,
                                 positiv=positiv, levetid_min=levetid_min,
                                 alder_s=alder_s, verdier=verdier),
                nokkel_id, hemmelighet)
        else:
            e["attestasjon"] = None
        elementer.append(e)

    return attestering.signer({
        "protokollversjon": oppdragskontrakt.PROTOKOLLVERSJON,
        "kvitteringstype": "verifikasjonskvittering_v1",
        "tenant_id": o["tenant"],
        "oppdrag_id": o["oppdrag_id"],
        "unntak_id": o["unntak_id"],
        "fase1_repair_operation_id": o["repair_operation_id"],
        "verification_generation": o["verification_generation"],
        "krav_sett_hash": p["krav_sett_hash"],
        "verifikator": verifikator,
        "nokkel_id": nokkel_id,
        "utstedt": datetime.now(timezone.utc).isoformat(),
        "attestasjoner": elementer,
    }, nokkel_id, hemmelighet)


def _post(api: str, sti: str, token: str, kropp: dict):
    import httpx
    return httpx.post(f"{api.rstrip('/')}{sti}",
                      headers={"authorization": f"Bearer {token}",
                               "content-type": "application/json"},
                      content=json.dumps(kropp, ensure_ascii=False).encode(),
                      timeout=30.0)


def verifiser_ett(api: str, token: str, verifikator: str, nokkel_id: str,
                  hemmelighet: str, *, resultat: str = "positiv",
                  permanent: bool = False, alder_s: int = 5,
                  verdier: dict | None = None) -> dict | None:
    """Én runde: claim ett verifikasjonsoppdrag, post HELE settet én gang.

    Returnerer metadata, eller None når køen er tom. Attestasjonenes
    innhold logges ALDRI — bare id-er og vilkårsnavn.
    """
    r = _post(api, "/v1/oppdrag/claim", token, {})
    if r.status_code == 204:
        return None
    if r.status_code != 200:
        raise RuntimeError(f"claim feilet: {r.status_code} {r.text[:200]}")
    o = r.json()
    if o.get("verification_generation") is None:
        raise RuntimeError(
            "claim-responsen mangler verification_generation — uten den kan"
            " kvitteringen ikke bindes til generasjonen som bestilte den")
    if not (o.get("payload") or {}).get("vilkaar_sett"):
        raise RuntimeError(
            "oppdraget bærer intet vilkaar_sett — form A krever at HELE"
            " settet står i oppdraget, ellers kan fase 2 aldri bli komplett")

    konvolutt = bygg_konvolutt(o, verifikator=verifikator, nokkel_id=nokkel_id,
                               hemmelighet=hemmelighet, resultat=resultat,
                               permanent=permanent, alder_s=alder_s,
                               verdier=verdier)

    k = _post(api, "/v1/oppdrag/kvittering", token,
              {"kvittering_jti": o["kvittering_jti"], "konvolutt": konvolutt})
    if k.status_code not in (200, 202):
        raise RuntimeError(f"kvittering avvist: {k.status_code} {k.text[:300]}")
    return {"oppdrag_id": o["oppdrag_id"], "unntak_id": o["unntak_id"],
            "vilkaar": list(o["payload"]["vilkaar_sett"]),
            "svar": k.status_code, "status": k.json().get("status")}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--api", default=os.environ.get("DISPONIT_API_URL",
                                                   "http://127.0.0.1:8099"))
    p.add_argument("--runder", type=int, default=0, help="0 = til køen er tom")
    p.add_argument("--intervall", type=float, default=0.5)
    p.add_argument("--resultat", default="positiv",
                   choices=("positiv", "negativ", "ikke_attesterbar"))
    p.add_argument("--permanent", action="store_true", help=(
        "verifikatoren påstår PRINSIPIELL u-innhentbarhet. Påstanden er"
        " bare bindende hvis policyen har gitt den kan_fastsla_permanent"))
    p.add_argument("--verdi", action="append", default=[], metavar="VILKAAR=N",
                   help=("målt verdi for et vilkår med `min:` i policyen,"
                         " f.eks. --verdi forfall_passert_dager=30"))
    p.add_argument("--alder-s", type=int, default=5, help=(
        "hvor gammel attestasjonen skal utstedes — for å måle policyens"
        " maks_attestasjon_alder_s-tak"))
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

    verdier = {}
    for par in a.verdi:
        navn, _, raa = par.partition("=")
        try:
            verdier[navn] = int(raa) if raa.strip().lstrip("-").isdigit() \
                else float(raa)
        except ValueError:
            print(f"AVBRUTT: --verdi {par!r} er ikke et tall", file=sys.stderr)
            return 2

    behandlet, runde = 0, 0
    while a.runder == 0 or runde < a.runder:
        runde += 1
        res = verifiser_ett(a.api, token, verifikator, nokkel_id, hemmelighet,
                            resultat=a.resultat, permanent=a.permanent,
                            alder_s=a.alder_s, verdier=verdier)
        if res is None:
            if a.runder == 0:
                break
            time.sleep(a.intervall)
            continue
        behandlet += 1
        print(json.dumps(res, ensure_ascii=False), flush=True)
    print(f"ferdig: {behandlet} sett attestert", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
