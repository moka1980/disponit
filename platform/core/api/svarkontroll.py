"""Registerets vitne for et kundesvar (ARC B kundeservice, PR 2).

Bransjemalen krever tre vilkår av `v_dlp` før en e-post til en kjent
mottaker går ut: `dlp_sjekk`, `ingen_okonomiske_lofter` og
`mottaker_i_kontaktregister`. Ingen ekstern DLP-tjeneste finnes; det
som finnes er teksten i utkastet, og den kan måles. Målingen er en
HEURISTIKK og sier det: den finner det den leter etter, og attesterer
USANT når den finner noe — resultatet er aldri pyntet. Et funn blir en
sak i unntakskøen der et menneske leser teksten; en tekst uten funn er
attestert ren for det heuristikken ser.

Funnene er KODER, aldri tekstutdrag: attestasjonen og saken bærer
«fodselsnummer», ikke nummeret.
"""
from __future__ import annotations

import re

#: Personopplysninger og hemmeligheter som ikke skal ut i et kundesvar.
_DLP = (
    # Elleve sifre i rekke, med eller uten mellomrom etter dag/måned/år:
    # fødselsnummer (og D-nummer). Kontonummer er også elleve, i 4-2-5.
    ("fodselsnummer", re.compile(r"(?<!\d)\d{6}\s?\d{5}(?!\d)")),
    ("kontonummer", re.compile(r"(?<!\d)\d{4}[ .]\d{2}[ .]\d{5}(?!\d)")),
    # Kortnummer: 13–19 sifre i grupper.
    ("kortnummer", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
    ("passord", re.compile(r"\b(passord|password|pin[- ]?kode?)\s*[:=]",
                           re.IGNORECASE)),
)

#: Løfter om penger: beløp, prosent, og ordene et løfte bæres av.
_LOFTER = (
    ("belop", re.compile(
        r"(?<![\w.])\d(?:[\d .]*\d)?\s?(?:kr|kroner|nok)\b", re.IGNORECASE)),
    ("belop", re.compile(r"\b(?:kr|nok)\s?\d", re.IGNORECASE)),
    ("prosent", re.compile(r"(?<!\w)\d+(?:[,.]\d+)?\s?%")),
    ("lofte", re.compile(
        r"\b(gratis|rabatt|refunder\w*|refusjon|kompensasjon|kompenser\w*|"
        r"erstatning|erstatte[rs]?|kostnadsfritt|tilbakebetal\w*|"
        r"vi dekker|uten kostnad|prisavslag|kreditnota|kreditere\w*)\b",
        re.IGNORECASE)),
)


def dlp_funn(tekst: str) -> list[str]:
    """-> koder for det heuristikken fant, sortert og uten gjentak."""
    return sorted({kode for kode, m in _DLP if m.search(tekst or "")})


def okonomiske_lofter(tekst: str) -> list[str]:
    return sorted({kode for kode, m in _LOFTER if m.search(tekst or "")})
