"""Svarets form (ARC B kundeservice, PR 4). Teksten er MENNESKETS — det
godkjente utkastet (160) sendes slik det står; modulen legger til
emnet («Re: <henvendelsens emne>») og tenantens signatur. Ingen
fletting: et svar er ikke en mal, og et felt teksten nevner er kundens
ord, ikke et hull.
"""
from __future__ import annotations

MALVERSJON = "svar-v1"


class Flettefeil(Exception):
    def __init__(self, kode: str):
        super().__init__(kode)
        self.kode = kode


def bygg(utforelse: dict) -> dict:
    """-> {malversjon, emne, tekst}. Tom tekst eller tomt emne er en
    Flettefeil — aldri en tom e-post."""
    tekst = utforelse.get("tekst")
    if not isinstance(tekst, str) or not tekst.strip():
        raise Flettefeil("felt_mangler:tekst")
    emne = utforelse.get("emne")
    if not isinstance(emne, str) or not emne.strip():
        raise Flettefeil("felt_mangler:emne")
    emne = emne.strip()
    if not emne.lower().startswith("re:"):
        emne = "Re: " + emne
    signatur = utforelse.get("signatur")
    if isinstance(signatur, str) and signatur.strip():
        tekst = tekst.rstrip() + "\n\n" + signatur.strip()
    return {"malversjon": MALVERSJON, "emne": emne, "tekst": tekst}
