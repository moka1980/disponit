"""Rapportbygging + sanitering (PR-014c §7): motorens rå utdata inn,
skjemagyldig rapport ut — eller Motorfeil, aldri et delvis artefakt.

Saneringen er ÆRLIG kutting: URL-er strippes for query/fragment,
selektorer kappes til 200 tegn, maks 10 eksempler per regel og maks 500
funn — og når funnlisten kappes, sier `avkortet` det (den lyver aldri om
at alt kom med). Miljøblokka er SERVERKONTEKSTENS, aldri motorens.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from .motor import Motorfeil, Motorresultat

_ALVOR = ("kritisk", "alvorlig", "moderat", "lav")
MAKS_FUNN = 500
MAKS_EKSEMPLER = 10
MAKS_SELEKTOR = 200


def _ren_url(raa: str) -> str:
    """https-URL uten query, fragment og credentials — eller Motorfeil."""
    try:
        d = urlsplit(str(raa))
    except ValueError as e:
        raise Motorfeil("uleselig url fra motoren") from e
    if d.scheme != "https" or not d.hostname:
        raise Motorfeil("motoren rapporterte en ikke-https-url")
    vert = d.hostname + (f":{d.port}" if d.port else "")
    return urlunsplit(("https", vert, d.path or "/", "", ""))


def _antall(raa, standard: int) -> int:
    """Motorens telling som heltall — eller Motorfeil, ALDRI en naken
    ValueError.

    Motorutdata er ubetrodd (§2): `{"antall": "ukjent"}` skal gi den
    dokumenterte feil-kvitteringen. `controller.kjor_en` fanger kun
    Motorfeil og ValidationError, så en konverteringsfeil herfra ville
    sluppet ut av controllerløkka og latt det claimede oppdraget stå
    ufullført til fristen — nøyaktig taushetenes utfall §10 forbyr.
    """
    if raa is None or raa == "":
        return standard
    if isinstance(raa, bool) or not isinstance(raa, (int, float, str)):
        raise Motorfeil("antall fra motoren er ikke et tall")
    try:
        return int(raa)
    except (TypeError, ValueError) as e:
        raise Motorfeil("antall fra motoren er ikke et tall") from e


def bygg(resultat: Motorresultat, *, payload: dict, kontekst: dict) -> dict:
    """-> rapport-dict, klar for skjemavalidering (som controlleren ALLTID
    kjører selv før opplasting — serveren validerer uansett, men modulen
    skal ikke sende noe den selv kan se er ugyldig).

    `kontekst` er controllerens serverkontekst:
    {axe_versjon, chromium_versjon, container_image_digest, viewport,
     locale, timezone} — fra config/digest, aldri fra motoren.
    """
    sider = []
    for s in resultat.sider:
        if not isinstance(s, dict):
            raise Motorfeil("side-post uleselig")
        sider.append({"url": _ren_url(s.get("url")),
                      "status": s.get("status")
                      if s.get("status") in ("ok", "feilet") else "feilet"})
    if not sider:
        raise Motorfeil("motoren kontrollerte ingen sider")

    sammendrag = {k: 0 for k in _ALVOR}
    funn = []
    for f in resultat.funn:
        if not isinstance(f, dict) or f.get("alvorlighet") not in _ALVOR:
            raise Motorfeil("funn-post uleselig")
        antall = _antall(f.get("antall"), 0)
        if antall < 1:
            continue
        sammendrag[f["alvorlighet"]] += antall
        if len(funn) < MAKS_FUNN:
            funn.append({
                "regel_id": str(f.get("regel_id"))[:128],
                "alvorlighet": f["alvorlighet"],
                "antall": antall,
                "eksempler": [str(e)[:MAKS_SELEKTOR]
                              for e in (f.get("eksempler") or [])
                              [:MAKS_EKSEMPLER]]})

    truffet, tak, verdi = (tuple(resultat.avkortet) + (None, None, None))[:3]
    # Kappet VI funnlisten, er rapporten avkortet uansett hva proxyen sa —
    # `avkortet` skal aldri love mer fullstendighet enn det som står i den.
    if len(resultat.funn) > MAKS_FUNN:
        truffet, tak = True, (tak if tak is not None else MAKS_FUNN)
        verdi = verdi if verdi is not None else len(resultat.funn)

    begrensninger = []
    for b in resultat.blokkert:
        if not isinstance(b, dict):
            continue
        vert = str(b.get("vert") or "").split("/")[0].split("?")[0].lower()
        if not vert:
            continue
        begrensninger.append({
            "vert": vert[:253],
            "antall": max(1, _antall(b.get("antall"), 1)),
            "art": b.get("art") if b.get("art") in
                   ("stilark", "font", "skript", "bilde", "annet")
                   else "annet"})

    return {
        "kravsett": payload.get("kravsett"),
        "regelsett_versjon": resultat.regelsett_versjon,
        "kjort_ts": datetime.now(timezone.utc).isoformat(),
        "varighet_ms": resultat.varighet_ms,
        "sider_kontrollert": sider,
        "funn": funn,
        "sammendrag": sammendrag,
        "avkortet": {"truffet": bool(truffet), "tak": tak, "verdi": verdi},
        "dekningsbegrensninger": begrensninger[:200],
        "miljo": {k: kontekst[k] for k in
                  ("axe_versjon", "chromium_versjon",
                   "container_image_digest", "viewport", "locale",
                   "timezone")},
        "manuelle_kriterier_vurdert": False,
    }
