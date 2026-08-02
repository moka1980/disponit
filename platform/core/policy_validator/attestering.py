"""Kryptografisk attestasjonsverifikasjon (PR-004, ADR-001 krav 3).

Allowlisten i policyen hindrer UKJENTE verifikatorer. Den hindrer ikke en
kompromittert innsender i å DIKTE en attestasjon i en kjent verifikators
navn. Denne modulen tetter det: hver attestasjon bærer HMAC-SHA256 over
kanonisk innhold, med hemmelig nøkkel per verifikator. Uten gyldig
signatur er attestasjonen verdiløs.

Nøkkelregister: {verifikator_id: {nokkel_id: hemmelighet}}. Flere nøkler
per verifikator muliggjør rotasjon uten nedetid: ny nøkkel legges til,
verifikatoren bytter, gammel fjernes. Lastes fra miljøvariabelen
DISPONIT_ATT_NOKLER (JSON) eller fil med 0600-rettigheter — aldri fra
repo, aldri fra hendelsen.

Verifisering skjer i inngangsporten (sikker_beslutning_pg), FØR motoren
evaluerer — motoren selv er uendret.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

from .engine import Grunn

_SIGNATURFELT = "signatur"
_ALG = "HMAC-SHA256"


def kanonisk_bytes(att: dict) -> bytes:
    """Deterministisk serialisering av attestasjonen UTEN signaturfeltet.
    Samme innhold gir samme bytes — på tvers av prosesser og språk med
    samme regler (sorterte nøkler, ingen mellomrom, UTF-8)."""
    uten = {k: v for k, v in att.items() if k != _SIGNATURFELT}
    return json.dumps(uten, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), default=str).encode("utf-8")


def signer(att: dict, nokkel_id: str, hemmelighet: str) -> dict:
    """Returnerer kopi av attestasjonen med signatur. Brukes av
    verifikator-modulene (M-14, M-42, …) når de utsteder attestasjoner."""
    mac = hmac.new(hemmelighet.encode("utf-8"), kanonisk_bytes(att),
                   hashlib.sha256).hexdigest()
    ut = dict(att)
    ut[_SIGNATURFELT] = {"alg": _ALG, "nokkel_id": nokkel_id, "verdi": mac}
    return ut


def verifiser(att: dict, nokler: dict) -> bool:
    """True hvis og bare hvis signaturen er gyldig for attestasjonens
    verifikator. Konstant-tids sammenligning. Kaster aldri."""
    try:
        sig = att.get(_SIGNATURFELT)
        if not isinstance(sig, dict) or sig.get("alg") != _ALG:
            return False
        verifikator = att.get("verifikator")
        hemmelighet = (nokler.get(verifikator) or {}).get(sig.get("nokkel_id"))
        if not isinstance(hemmelighet, str) or not hemmelighet:
            return False
        forventet = hmac.new(hemmelighet.encode("utf-8"), kanonisk_bytes(att),
                             hashlib.sha256).hexdigest()
        verdi = sig.get("verdi")
        return isinstance(verdi, str) and hmac.compare_digest(forventet, verdi)
    except Exception:
        return False  # fail-closed


def kontroller_hendelse(event: dict, nokler: dict) -> Grunn | None:
    """Porten sikker_beslutning_pg kaller. None == alle attestasjoner har
    gyldig signatur. Ellers: første brudd som Grunn (=> STOPP).
    En hendelse uten attestasjoner passerer porten (motoren håndhever
    selv at påkrevde vilkår har attestasjon)."""
    attester = event.get("attestasjoner")
    if attester is None:
        return None
    if not isinstance(attester, dict):
        return Grunn("attestasjoner_feilformet")
    for navn, att in attester.items():
        if not isinstance(att, dict):
            return Grunn("attestasjon_feilformet", {"vilkaar": str(navn)})
        if _SIGNATURFELT not in att:
            return Grunn("attestasjon_uten_signatur", {"vilkaar": str(navn)})
        if not verifiser(att, nokler):
            return Grunn("attestasjon_signatur_ugyldig", {"vilkaar": str(navn)})
    return None


def last_nokler(kilde: str | None = None) -> dict:
    """Laster nøkkelregisteret.

    Rekkefølge: eksplisitt filsti-argument -> miljøvariabelen
    DISPONIT_ATT_NOKLER (JSON-innhold) -> miljøvariabelen
    DISPONIT_ATT_NOKLER_FIL (sti). Filer må ha 0600/0400 — for åpne
    rettigheter er en feil, ikke en advarsel (fail-closed).
    """
    if kilde is None and (raa := os.environ.get("DISPONIT_ATT_NOKLER")):
        return _valider_register(json.loads(raa))
    sti = Path(kilde or os.environ.get("DISPONIT_ATT_NOKLER_FIL", ""))
    if not sti or not sti.is_file():
        raise FileNotFoundError(
            "nøkkelregister ikke funnet: sett DISPONIT_ATT_NOKLER eller "
            "DISPONIT_ATT_NOKLER_FIL")
    modus = stat.S_IMODE(sti.stat().st_mode)
    if modus & 0o077:
        raise PermissionError(
            f"nøkkelfilen {sti} har rettigheter {oct(modus)} — krever 0600")
    return _valider_register(json.loads(sti.read_text(encoding="utf-8")))


def _valider_register(reg: object) -> dict:
    if not isinstance(reg, dict) or not reg:
        raise ValueError("nøkkelregisteret må være et ikke-tomt objekt")
    for vid, nokler in reg.items():
        if not isinstance(nokler, dict) or not nokler:
            raise ValueError(f"verifikator '{vid}' mangler nøkler")
        for nid, hemmelighet in nokler.items():
            if not isinstance(hemmelighet, str) or len(hemmelighet) < 32:
                raise ValueError(
                    f"nøkkel '{vid}/{nid}' må være streng på minst 32 tegn")
    return reg
