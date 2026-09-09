"""Purremalene (ARC B, PR 4). Teksten bor HER, i modulen, versjonert
(M-57s `maler`-form) — ikke i en fri tekst i oppdraget, som ingen
signatur dekker. Tre trinnhandlinger, tre maler; `inkasso` finnes ikke:
den sendes aldri automatisk (eiervedtaket 9/9).

Feltene er tallene fra fordringen og tenantens avsenderprofil. Alle
felter en mal nevner må ha innhold — en purring med et tomt
fakturanummer er ikke en purring.
"""
from __future__ import annotations

import re

MALER: dict[str, dict] = {
    "paaminnelse": {
        "malversjon": "paaminnelse-v1",
        "felter": frozenset({"fakturanummer", "rest", "forfall",
                             "avsender"}),
        "emne": "Påminnelse om faktura {fakturanummer}",
        "tekst": ("Hei,\n\n"
                  "vi minner om faktura {fakturanummer} på {rest} kr, som"
                  " forfalt {forfall}. Har du allerede betalt, kan du se"
                  " bort fra denne meldingen.\n\n"
                  "Vennlig hilsen\n{avsender}"),
    },
    "purring": {
        "malversjon": "purring-v1",
        "felter": frozenset({"fakturanummer", "rest", "forfall",
                             "avsender", "gebyr"}),
        "emne": "Purring: faktura {fakturanummer}",
        "tekst": ("Hei,\n\n"
                  "faktura {fakturanummer} på {rest} kr forfalt {forfall}"
                  " og er fortsatt ikke betalt. Vi ber om at beløpet"
                  " betales snarest.{gebyr}\n\n"
                  "Har du spørsmål om kravet, svar på denne e-posten.\n\n"
                  "Vennlig hilsen\n{avsender}"),
    },
    "inkassovarsel": {
        "malversjon": "inkassovarsel-v1",
        "felter": frozenset({"fakturanummer", "rest", "forfall",
                             "avsender", "gebyr"}),
        "emne": "Inkassovarsel: faktura {fakturanummer}",
        "tekst": ("Hei,\n\n"
                  "faktura {fakturanummer} på {rest} kr forfalt {forfall}"
                  " og er ikke betalt. Dette er et inkassovarsel etter"
                  " inkassoloven § 9: betales ikke kravet innen 14 dager"
                  " fra dette varselet, sendes det til inkasso, og"
                  " ytterligere kostnader kan påløpe.{gebyr}\n\n"
                  "Har du innsigelser mot kravet, svar på denne e-posten"
                  " før fristen.\n\n"
                  "Vennlig hilsen\n{avsender}"),
    },
}

_FELTMONSTER = re.compile(r"\{([a-z_]+)\}")


class Flettefeil(Exception):
    def __init__(self, kode: str):
        super().__init__(kode)
        self.kode = kode


def kroner(ore: int) -> str:
    """Øre → «1 234,50» (norsk form, tusenskille som mellomrom)."""
    ore = int(ore)
    fortegn = "-" if ore < 0 else ""
    ore = abs(ore)
    hel, rest = divmod(ore, 100)
    return f"{fortegn}{hel:,}".replace(",", " ") + f",{rest:02d}"


def _ore(utforelse: dict, felt: str, *, minst: int) -> int:
    """Et beløp i øre må VÆRE et beløp: heltall, ikke bool, ikke under
    gulvet. En purring på «0,00 kr» eller «None kr» er ikke en purring
    (CodeRabbit på PR 4) — Flettefeil, og controlleren kvitterer malfeil."""
    v = utforelse.get(felt)
    if v is None and minst <= 0:
        return 0
    if isinstance(v, bool) or not isinstance(v, int) or v < minst:
        raise Flettefeil(f"felt_mangler:{felt}")
    return v


def felter_fra(utforelse: dict) -> dict[str, str]:
    """Claim-svarets `utforelse` → malfeltene. Avsendernavnet er
    tenantens profil, ellers tenant-id-en (ærlig, ikke pent)."""
    gebyr = _ore(utforelse, "gebyr_ore", minst=0)
    return {
        "fakturanummer": str(utforelse.get("fakturanummer") or ""),
        "rest": kroner(_ore(utforelse, "rest_ore", minst=1)),
        "forfall": str(utforelse.get("forfall") or ""),
        "avsender": str(utforelse.get("avsender_navn")
                        or utforelse.get("tenant") or ""),
        "gebyr": (f" Purregebyr på {kroner(gebyr)} kr kommer i tillegg."
                  if gebyr > 0 else ""),
    }


def flett(handling_trinn: str, felter: dict[str, str]) -> dict:
    """-> {malversjon, emne, tekst}. Ukjent handling eller tomt felt →
    `Flettefeil` — aldri en e-post med hull i."""
    mal = MALER.get(handling_trinn)
    if mal is None:
        raise Flettefeil("mal_ukjent")
    for navn in mal["felter"]:
        if navn == "gebyr":
            continue                    # tom tekst er en ekte tilstand
        if not isinstance(felter.get(navn), str) or not felter[navn].strip():
            raise Flettefeil(f"felt_mangler:{navn}")
    brukt = set(_FELTMONSTER.findall(mal["tekst"] + mal["emne"]))
    if brukt - set(felter):
        raise Flettefeil("felt_mangler:" + ",".join(sorted(brukt
                                                            - set(felter))))
    return {"malversjon": mal["malversjon"],
            "emne": mal["emne"].format(**felter),
            "tekst": mal["tekst"].format(**felter)}
