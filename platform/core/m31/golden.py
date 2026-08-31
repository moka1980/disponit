"""Golden-sett på DISK, hash-pinnet (dom 1 — biasmaalinger.json-
presedensen): basen bærer kun HODET (modul, sett, versjon, kanonisk
hash, antall), aldri eksemplene. Denne modulen eier den KANONISKE
hashen og settets form, så registrerings- og kjøre-CLI-en leser samme
bytes på samme måte — to hash-varianter av samme fil ville vært to
identiteter for ett sett.

Hashen er over den KANONISKE PROJEKSJONEN (parset JSON, sort_keys,
kompakte skilletegn) — samme disiplin som `kanonisk_projeksjon` for
manifester: formatering og kommentarløse omdumpinger dør i parsingen,
identiteten er strukturen.

Scoringen (v1-adapteren for m57) bor også her: EKSAKT match på
`oppfylt`-mappen + MENGDELIKHET på funn-KATEGORIER. Posisjoner og
sitater sammenlignes ikke i v1 — de er evidens for leseren, ikke
fasitens akse.

Core importerer ALDRI fra moduler (RUTINER §7): denne fila kjenner
ingen modellklient — den får svar-dicter og eksempler, og dømmer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: Feltene et golden-eksempel MÅ bære. Lukket sett-form (m57-regelen:
#: kontrakten er settet, ikke feltene noen kom på å måle) — men
#: eksemplene får bære EKSTRA felt (notat, opphav): de er del av
#: identiteten (hashen) uten å være del av dommen.
EKSEMPEL_FELTER = frozenset({"id", "tekst", "vekter",
                             "forventet_oppfylt",
                             "forventede_funn_kategorier"})


class Settfeil(Exception):
    """Settet på disk har feil form — registrering/kjøring skal stoppe
    FØR noe skrives og FØR første modellkall."""


def kanonisk_hash(data: object) -> str:
    """sha256 over den kanoniske projeksjonen av parsede data."""
    kanonisk = json.dumps(data, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    return hashlib.sha256(kanonisk.encode("utf-8")).hexdigest()


def les_sett(sti: Path) -> tuple[list[dict], str]:
    """Les og VALIDER et golden-sett fra disk. -> (eksempler, hash).

    Formen håndheves her, én gang, for begge CLI-ene: en liste av
    eksempler der hvert eksempel bærer de påkrevde feltene, `vekter` og
    `forventet_oppfylt` deler NØYAKTIG samme kravsett, og
    funn-kategoriene er en liste av strenger. Persondata-disiplinen
    (syntetisk, blindet form) er reviewansvar for settfila — formen her
    kan bare kreve at tekstene finnes.
    """
    try:
        data = json.loads(Path(sti).read_text(encoding="utf-8"))
    except (OSError, ValueError) as feil:
        raise Settfeil(f"kan ikke lese settet: {feil}") from feil
    if not isinstance(data, list) or not data:
        raise Settfeil("settet må være en ikke-tom JSON-liste av eksempler")
    for i, eks in enumerate(data):
        if not isinstance(eks, dict) or not EKSEMPEL_FELTER <= set(eks):
            mangler = (sorted(EKSEMPEL_FELTER - set(eks))
                       if isinstance(eks, dict) else type(eks).__name__)
            raise Settfeil(f"eksempel {i}: mangler felter ({mangler})")
        if not isinstance(eks["id"], str) or not eks["id"]:
            raise Settfeil(f"eksempel {i}: `id` må være en ikke-tom streng")
        if not isinstance(eks["tekst"], str) or not eks["tekst"].strip():
            raise Settfeil(f"eksempel {i}: `tekst` må være en ikke-tom"
                           " streng")
        vekter = eks["vekter"]
        forventet = eks["forventet_oppfylt"]
        if (not isinstance(vekter, dict) or not vekter
                or not all(isinstance(v, int) for v in vekter.values())):
            raise Settfeil(f"eksempel {i}: `vekter` må være en ikke-tom"
                           " dict med heltallsvekter")
        if not isinstance(forventet, dict) \
                or set(forventet) != set(vekter) \
                or not all(isinstance(v, bool) for v in forventet.values()):
            raise Settfeil(
                f"eksempel {i}: `forventet_oppfylt` må dekke NØYAKTIG"
                " kravene i `vekter`, med bokstavelige booleans —"
                " fasiten er settets, aldri modellens")
        kategorier = eks["forventede_funn_kategorier"]
        if not isinstance(kategorier, list) \
                or not all(isinstance(k, str) and k for k in kategorier):
            raise Settfeil(f"eksempel {i}: `forventede_funn_kategorier`"
                           " må være en liste av ikke-tomme strenger")
    ider = [eks["id"] for eks in data]
    if len(set(ider)) != len(ider):
        raise Settfeil("eksempel-id-ene må være unike")
    return data, kanonisk_hash(data)


def eksempel_bestatt(svar: dict, eksempel: dict) -> bool:
    """v1-scoringen for m57-formede svar (`{funn, oppfylt}`).

    EKSAKT match på `oppfylt`-mappen (fail-closed: et krav modellen
    ikke svarte på er alt False i klientens kontrakt) + MENGDELIKHET på
    funn-kategoriene: fasiten sier HVILKE kategorier som skal finnes,
    ikke hvor mange funn eller hvor de står.
    """
    if not isinstance(svar, dict):
        return False
    oppfylt = svar.get("oppfylt")
    if oppfylt != eksempel["forventet_oppfylt"]:
        return False
    funn = svar.get("funn")
    if not isinstance(funn, list):
        return False
    kategorier = {f.get("kategori") for f in funn if isinstance(f, dict)}
    return kategorier == set(eksempel["forventede_funn_kategorier"])
