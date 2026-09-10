"""Tilbudets form (ARC B tilbud, PR 4). Tallene er REGISTERETS — linjene
kommer i claim-svarets `utforelse` (172) slik døra satte dem, og skrives
inn som de står. Modulen regner ikke og runder ikke: et tilbud er en
avskrift av boka, og hver forskjell ville vært en feil ingen kunne
forklare. Klausulene er den BUNDNE versjonen (teksten tilbudet siterte).
"""
from __future__ import annotations

MALVERSJON = "tilbud-v1"


class Flettefeil(Exception):
    def __init__(self, kode: str):
        super().__init__(kode)
        self.kode = kode


def belop(ore) -> str:
    if not isinstance(ore, int) or isinstance(ore, bool):
        raise Flettefeil("felt_ugyldig:belop")
    neg = "-" if ore < 0 else ""
    a = abs(ore)
    return f"{neg}{a // 100},{a % 100:02d}"


def bygg(utforelse: dict) -> dict:
    """-> {malversjon, emne, tekst}. Manglende navn, linjer eller sum er
    en Flettefeil — aldri et halvt tilbud."""
    navn = utforelse.get("kunde_navn")
    if not isinstance(navn, str) or not navn.strip():
        raise Flettefeil("felt_mangler:kunde_navn")
    linjer = utforelse.get("linjer")
    if not isinstance(linjer, list) or not linjer:
        raise Flettefeil("felt_mangler:linjer")
    sum_ore = utforelse.get("sum_ore")
    if not isinstance(sum_ore, int) or isinstance(sum_ore, bool) or sum_ore < 0:
        raise Flettefeil("felt_ugyldig:sum_ore")
    avsender = (utforelse.get("avsender_navn") or "").strip() or "oss"
    valuta = utforelse.get("valuta") or "NOK"
    gyldig = utforelse.get("gyldig_til") or ""
    emne = f"Tilbud fra {avsender}: {belop(sum_ore)} {valuta}"
    deler = [f"Hei {navn.strip()},", ""]
    innledning = utforelse.get("innledning")
    if isinstance(innledning, str) and innledning.strip():
        deler += [innledning.strip(), ""]
    deler.append("Tilbudet omfatter:")
    for l in linjer:
        try:
            deler.append(
                f"  - {l['produktkode']} {l['produktnavn']}: "
                f"{l['antall']} {l['enhet']} à {belop(l['enhetspris_ore'])}"
                f" = {belop(l['linjesum_ore'])} {valuta}")
        except (KeyError, TypeError, ValueError) as e:
            raise Flettefeil("felt_ugyldig:linje") from e
    deler += ["", f"Sum: {belop(sum_ore)} {valuta}",
              f"Tilbudet gjelder til {gyldig}." if gyldig else ""]
    klausuler = utforelse.get("klausuler") or []
    if klausuler:
        deler += ["", "Betingelser:"]
        for k in klausuler:
            if not isinstance(k, dict):
                raise Flettefeil("felt_ugyldig:klausul")
            tittel = str(k.get("tittel") or k.get("kode") or "").strip()
            tekst = str(k.get("tekst") or "").strip()
            if tekst:
                deler.append(f"  {tittel}: {tekst}")
    signatur = utforelse.get("signatur")
    if isinstance(signatur, str) and signatur.strip():
        deler += ["", signatur.strip()]
    tekst = "\n".join(d for d in deler if d is not None).rstrip() + "\n"
    return {"malversjon": MALVERSJON, "emne": emne, "tekst": tekst}
