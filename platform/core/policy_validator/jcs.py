"""JSON Canonicalization Scheme — RFC 8785 (PR-006 §4, retro-P2).

Hvorfor dette ikke er «json.dumps med sorterte nøkler»:

  1. NØKKELREKKEFØLGE. RFC 8785 sorterer på UTF-16-KODEENHETER, Python på
     Unicode-kodepunkter. For alt i BMP er de like, men et tegn utenfor BMP
     (U+10000 og opp) blir i UTF-16 til et surrogatpar som starter på
     0xD800 — altså LAVERE enn f.eks. U+E000, mens kodepunktet er
     HØYERE. To implementasjoner ville sortert de to nøklene motsatt, og
     signaturen ville ikke validert på tvers.

  2. TALL. `json.dumps` skriver `1e+21`; ECMAScript — og dermed RFC 8785 —
     skriver `1e+21` for det tallet, men `100000000000000000000` for 1e20.
     Grensene ligger på 1e21 og 1e-7, og de er ikke der Python setter dem.

  3. `default=str`. Den gamle `kanonisk_bytes` hadde den, og det er en
     STILLE datatypekonvertering: en `Decimal`, en `datetime` eller et
     vilkårlig objekt ble til en streng, signert, og så sammenlignet mot
     en annen implementasjons tolkning av det samme. Nå er en ikke-JSON-type
     en VALIDERINGSFEIL. Det er hele poenget med å bytte: kanonisering som
     gjetter er ikke kanonisering.

Formatet er LUKKET på nettverksveien fra og med PR-006: attestasjoner må
bære `kanonisering: "JCS"`, og manglende eller ukjent verdi avvises. Alle
verifikatorer er interne, så bruddet er kontrollert og skjer i samme
leveranse som byttet — designregelen fra 005b: bygg porten, gjør den
obligatorisk, og gjør den umulig å omgå i SAMME leveranse.
"""
from __future__ import annotations

import math

KANONISERING = "JCS"


class Ikkekanoniserbar(TypeError):
    """En verdi JSON ikke kan uttrykke. Aldri en stille konvertering."""


# --- Strenger --------------------------------------------------------------

#: RFC 8785 §3.2.2.2: kun disse får kort escape. Alle andre kontrolltegn
#: blir \u00XX med SMÅ bokstaver i hex.
_KORTE = {0x08: "\\b", 0x09: "\\t", 0x0A: "\\n", 0x0C: "\\f", 0x0D: "\\r",
          0x22: '\\"', 0x5C: "\\\\"}


def _streng(s: str) -> str:
    ut = ['"']
    for tegn in s:
        kode = ord(tegn)
        kort = _KORTE.get(kode)
        if kort is not None:
            ut.append(kort)
        elif kode < 0x20:
            ut.append(f"\\u{kode:04x}")
        else:
            # Alt annet skrives LITERALT, inkludert ikke-ASCII. Utdata er
            # UTF-8, og `\uXXXX`-escaping av æøå ville gitt en annen
            # bytesekvens enn en JavaScript-implementasjon produserer.
            ut.append(tegn)
    ut.append('"')
    return "".join(ut)


# --- Tall ------------------------------------------------------------------

def _tall(x: int | float) -> str:
    """ECMAScript `Number::toString`, slik RFC 8785 §3.2.2.3 krever.

    Python og JavaScript er enige om sifrene (begge bruker korteste
    representasjon som runder tilbake til samme double), men uenige om
    formen: eksponentgrensene og skrivemåten er andre. Konverteringen her
    flytter Python over på JavaScript sine regler.
    """
    if isinstance(x, bool):
        raise Ikkekanoniserbar("bool er ikke et tall i JSON-forstand")
    if isinstance(x, int):
        # Heltall skrives som heltall uansett størrelse. RFC 8785 forutsetter
        # at input er gyldig JSON-tall; et heltall utenfor IEEE-754 sitt
        # trygge område ville uansett ikke overlevd en rundtur gjennom en
        # JavaScript-implementasjon, så det avvises framfor å tape presisjon
        # stille.
        if abs(x) > 2 ** 53 - 1:
            raise Ikkekanoniserbar(
                f"heltallet {x} er utenfor IEEE-754 sitt trygge område og"
                " kan ikke kanoniseres tapsfritt")
        return str(x)
    if math.isnan(x) or math.isinf(x):
        # NaN og uendelig FINNES ikke i JSON. `json.dumps` skriver dem
        # likevel, som `NaN`/`Infinity` — ugyldig JSON som andre parsere
        # avviser. Samme felle som `_tall()` i manifestporten fant:
        # NaN gjør enhver sammenligning False.
        raise Ikkekanoniserbar(f"{x!r} kan ikke uttrykkes i JSON")
    if x == 0:
        return "0"          # også for -0.0: ES6 gir "0"

    if float(x).is_integer() and abs(x) < 1e21:
        return str(int(x))

    r = repr(float(x))
    if "e" not in r and "E" not in r:
        return r
    mantisse, _, eksp = r.partition("e")
    e = int(eksp)
    mantisse = mantisse.rstrip("0").rstrip(".") if "." in mantisse else mantisse
    if 0 < e < 21:
        return f"{float(x):.{max(0, 17)}g}"
    # ES6 skriver eksponenten med eksplisitt fortegn og uten ledende nuller.
    return f"{mantisse}e{'+' if e >= 0 else '-'}{abs(e)}"


# --- Nøkkelsortering -------------------------------------------------------

def _utf16_nokkel(s: str) -> tuple[int, ...]:
    """Nøkkelen RFC 8785 sorterer på: UTF-16-kodeenheter.

    `s.encode('utf-16-be')` gir nøyaktig de kodeenhetene, surrogatpar og
    alt. Python sitt eget `sorted()` ville brukt kodepunkter, og det er en
    ANNEN rekkefølge så snart en nøkkel inneholder tegn utenfor BMP.
    """
    b = s.encode("utf-16-be", errors="surrogatepass")
    return tuple(int.from_bytes(b[i:i + 2], "big") for i in range(0, len(b), 2))


# --- Serialisering ---------------------------------------------------------

def kanoniser(verdi: object) -> str:
    """RFC 8785-kanonisk JSON-tekst. Kaster `Ikkekanoniserbar`.

    Ingen `default`-parameter, og det er med vilje: en escape hatch her
    ville gjenåpnet nøyaktig hullet `default=str` var.
    """
    if verdi is None:
        return "null"
    if verdi is True:
        return "true"
    if verdi is False:
        return "false"
    if isinstance(verdi, str):
        return _streng(verdi)
    if isinstance(verdi, (int, float)):
        return _tall(verdi)
    if isinstance(verdi, (list, tuple)):
        return "[" + ",".join(kanoniser(v) for v in verdi) + "]"
    if isinstance(verdi, dict):
        for k in verdi:
            if not isinstance(k, str):
                raise Ikkekanoniserbar(
                    f"objektnøkkelen {k!r} er ikke en streng")
        nokler = sorted(verdi, key=_utf16_nokkel)
        return "{" + ",".join(
            f"{_streng(k)}:{kanoniser(verdi[k])}" for k in nokler) + "}"
    raise Ikkekanoniserbar(
        f"{type(verdi).__name__} kan ikke kanoniseres — konverter eksplisitt"
        " før signering, aldri stille i serialiseringen")


def kanoniske_bytes(verdi: object) -> bytes:
    """UTF-8-bytene som signeres. RFC 8785 §3.1."""
    return kanoniser(verdi).encode("utf-8")
