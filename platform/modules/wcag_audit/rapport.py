"""Rapportbygging + sanitering (PR-014c §7): motorens rå utdata inn,
skjemagyldig rapport ut — eller Motorfeil, aldri et delvis artefakt.

Saneringen er ÆRLIG kutting: URL-er strippes for query/fragment,
selektorer kappes til 200 tegn, maks 10 eksempler per regel, maks 500
funn og maks 200 dekningsbegrensninger — og kappes EN AV LISTENE, sier
`avkortet` det (den lyver aldri om at alt kom med). Miljøblokka er
SERVERKONTEKSTENS, aldri motorens.
"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from .motor import Motorfeil, Motorresultat

_ALVOR = ("kritisk", "alvorlig", "moderat", "lav")
MAKS_FUNN = 500
MAKS_EKSEMPLER = 10
MAKS_SELEKTOR = 200
MAKS_BEGRENSNINGER = 200


def _ren_url(raa: str) -> str:
    """https-URL uten query, fragment og credentials — eller Motorfeil.

    HELE parsingen står innenfor vakten (Codex P1), også `d.port`: den er
    en property som SELV kaster ValueError på `https://x.example:not-a-
    port/` og på portnumre utenfor 1–65535. Stod den utenfor, ville en
    naken ValueError sluppet forbi `controller.kjor_en` (som kun fanger
    Motorfeil og ValidationError) og latt det claimede oppdraget stå
    ufullført til fristen — samme taushet §10 forbyr for `_antall`.
    """
    try:
        d = urlsplit(str(raa))
        vert, port = d.hostname, d.port
    except ValueError as e:
        raise Motorfeil("uleselig url fra motoren") from e
    if d.scheme != "https" or not vert:
        raise Motorfeil("motoren rapporterte en ikke-https-url")
    return urlunsplit(("https", vert + (f":{port}" if port else ""),
                       d.path or "/", "", ""))


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

    # Dekningsbegrensningene SLÅS SAMMEN per (vert, art) før taket måles
    # (Codex P2): proxyen teller per subressurs, så den samme verten kan
    # komme igjen mange ganger. Summeringen er ærlig — tallet blir det
    # totale — og den gjør at taket sjelden treffer i det hele tatt.
    samlet: dict[tuple, dict] = {}
    for b in resultat.blokkert:
        if not isinstance(b, dict):
            continue
        vert = str(b.get("vert") or "").split("/")[0].split("?")[0].lower()
        if not vert:
            continue
        art = (b.get("art") if b.get("art") in
               ("stilark", "font", "skript", "bilde", "annet") else "annet")
        nokkel = (vert[:253], art)
        post = samlet.setdefault(nokkel, {"vert": nokkel[0], "antall": 0,
                                          "art": art})
        post["antall"] += max(1, _antall(b.get("antall"), 1))
    # Størst først: treffer taket likevel, er det de STØRSTE
    # begrensningene som kommer med, ikke de tilfeldig første.
    begrensninger = sorted(samlet.values(),
                           key=lambda p: (-p["antall"], p["vert"], p["art"]))
    # ... og kappes lista, SIER `avkortet` det. Uten dette kunne den
    # promoterte evidensen påstå at ingenting var utelatt samtidig som den
    # utelot kjente dekningsbegrensninger — stikk i strid med hele
    # poenget med feltet (014b B3).
    if len(begrensninger) > MAKS_BEGRENSNINGER:
        truffet = True
        tak = tak if tak is not None else MAKS_BEGRENSNINGER
        verdi = verdi if verdi is not None else len(begrensninger)
        begrensninger = begrensninger[:MAKS_BEGRENSNINGER]

    return {
        "kravsett": payload.get("kravsett"),
        "regelsett_versjon": resultat.regelsett_versjon,
        "kjort_ts": datetime.now(timezone.utc).isoformat(),
        "varighet_ms": resultat.varighet_ms,
        "sider_kontrollert": sider,
        "funn": funn,
        "sammendrag": sammendrag,
        "avkortet": {"truffet": bool(truffet), "tak": tak, "verdi": verdi},
        "dekningsbegrensninger": begrensninger,
        "miljo": {k: kontekst[k] for k in
                  ("axe_versjon", "chromium_versjon",
                   "container_image_digest", "viewport", "locale",
                   "timezone")},
        "manuelle_kriterier_vurdert": False,
    }
