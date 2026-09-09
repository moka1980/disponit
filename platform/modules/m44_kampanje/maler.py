"""Kampanjeflettingen (ARC B kampanje, PR 4). Teksten er TENANTENS (153:
emne og tekst på kampanjen) — modulen fletter bare to felter inn, og
sørger for det ene policyen krever: AVMELDINGSLENKEN står i hver e-post
(vilkåret `avmeldingslenke`, bransjemalen). Mangler den i teksten, legges
den til som fot. Uten lenke leveres ingenting.

Feltene: `{navn}` (mottakerens navn) og `{avmeldingslenke}`. Et felt
teksten nevner som ikke finnes, er en Flettefeil — aldri en e-post med
hull i, og aldri en e-post med en fremmed streng formatert inn.
"""
from __future__ import annotations

import re

MALVERSJON = "kampanje-v1"
TILLATTE_FELT = frozenset({"navn", "avmeldingslenke"})
AVMELDINGSFOT = ("\n\n--\nØnsker du ikke flere slike e-poster fra oss?"
                 " Meld deg av her: {avmeldingslenke}")

_FELTMONSTER = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class Flettefeil(Exception):
    def __init__(self, kode: str):
        super().__init__(kode)
        self.kode = kode


def felter_fra(utforelse: dict) -> dict[str, str]:
    lenke = utforelse.get("avmeldingslenke")
    if not isinstance(lenke, str) or not lenke.strip():
        raise Flettefeil("felt_mangler:avmeldingslenke")
    navn = utforelse.get("mottaker_navn")
    return {"navn": navn.strip() if isinstance(navn, str) else "",
            "avmeldingslenke": lenke.strip()}


def _flett(tekst: str, felter: dict[str, str]) -> str:
    ukjent = sorted({f for f in _FELTMONSTER.findall(tekst)
                     if f not in TILLATTE_FELT})
    if ukjent:
        raise Flettefeil("felt_ukjent:" + ",".join(ukjent))
    if "{navn}" in tekst and not felter["navn"]:
        raise Flettefeil("felt_mangler:navn")
    # Bytt felt for felt — aldri str.format: tenantens tekst kan bære
    # klammer som ikke er felter, og de skal stå som de er.
    return _FELTMONSTER.sub(lambda m: felter[m.group(1)], tekst)


def flett(utforelse: dict) -> dict:
    """-> {malversjon, emne, tekst}. Emne og tekst er tenantens; lenken
    er garantert med."""
    felter = felter_fra(utforelse)
    emne, tekst = utforelse.get("emne"), utforelse.get("tekst")
    if not isinstance(emne, str) or not emne.strip():
        raise Flettefeil("felt_mangler:emne")
    if not isinstance(tekst, str) or not tekst.strip():
        raise Flettefeil("felt_mangler:tekst")
    if "{avmeldingslenke}" not in tekst:
        tekst = tekst.rstrip() + AVMELDINGSFOT
    return {"malversjon": MALVERSJON,
            "emne": _flett(emne.strip(), felter),
            "tekst": _flett(tekst, felter)}
