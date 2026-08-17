"""Skjemavalidering av artefaktinnhold (PR-014c §8) — CP5-hullet fra 014b.

Artefakttypens `skjema_hash` har til nå vært en påstand ingen kunne slå
opp: innhold ble kryptert og promotert uten at noen validerte det mot
noe. 036 ga skjemaet et innholdsadressert lager; her er oppslaget og
valideringen — brukt på NØYAKTIG TO punkter, begge påkrevde:

  1. Ved OPPLASTING (`_artefakt_upload`): valider klarteksten før
     kryptering. Avvis — ingen staged rad, ingen kapabilitet kastet bort
     på innhold som aldri kan promoteres.
  2. Ved PROMOTERING (kvittering-ingest, samme transaksjon som
     statusovergangen): dekrypter og valider PÅ NYTT mot samme hash.
     Skjemaet er immutabelt, så dette er ikke forsvar mot endring — det
     er forsvar mot at en FREMTIDIG opplastingsvei glemmer punkt 1.

Ingen skjemarad for hashen → avvist. Ingen stille promotering av innhold
ingen kan validere.
"""
from __future__ import annotations

import json

import jsonschema
import psycopg


def hent_skjema(conn: psycopg.Connection, artefakttype: str) -> dict | None:
    """Skjemaet artefakttypen er bundet til, via registerets `skjema_hash`.
    -> None når typen mangler i registeret ELLER hashen mangler skjemarad —
    begge er samme svar for kalleren: innholdet kan ikke valideres, og da
    skal det heller ikke tas imot."""
    rad = conn.execute(
        "SELECT s.skjema FROM artefakttype_register r"
        "  JOIN artefaktskjema s ON s.skjema_hash = r.skjema_hash"
        " WHERE r.artefakttype = %s", (artefakttype,)).fetchone()
    return rad[0] if rad else None


def valider(skjema: dict, innhold: dict) -> list[str]:
    """-> feilliste (tom = gyldig). Draft 2020-12, samme validatorfamilie
    som policyskjemaet. Feilene er tekst for LOGGEN — de sendes aldri
    ordrett til klienten (innholdet kan bære persondata)."""
    feil = []
    validator = jsonschema.Draft202012Validator(skjema)
    for e in sorted(validator.iter_errors(innhold),
                    key=lambda e: list(e.absolute_path)):
        sti = "/".join(str(p) for p in e.absolute_path) or "<rot>"
        feil.append(f"{sti}: {e.message[:160]}")
        if len(feil) >= 20:
            feil.append("… (avkortet)")
            break
    return feil
