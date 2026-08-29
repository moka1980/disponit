"""UTF-8-bytene av UBETRODD tekst — den ene formen som ikke kan kaste.

`json.loads` godtar `"\\ud800"`. Et ensomt surrogat er ikke en formfeil for
JSON-parseren; det blir en helt alminnelig `str` i hendelsen, og ingen
validering kalleren rekker å gjøre får det bort — verdien ligger allerede i
minnet når den leses. Pythons UTF-8-koder nekter derimot å kode det, fordi
sekvensen ikke har noen gyldig UTF-8-form.

Det gjør ENHVER avtrykksberegning over en ubetrodd hendelse til et sted
avsenderen kan kaste et unntak fra. Og et unntak på beslutningsveien er
ikke et fail-closed stopp med en begrunnelse: det er en forespørsel som
dør UTEN revisjonspost, valgt av den som sendte den (Codex P2).

`errors="surrogatepass"` og ikke `"backslashreplace"`/`"replace"`: avtrykk
og input-hasher må holde ULIKE verdier ULIKE. `backslashreplace` gjør
surrogatet om til TEKSTEN `\\ud800`, som kolliderer med en streng som
faktisk inneholder de seks tegnene; `replace` slår alle sammen til U+FFFD.
`surrogatepass` gir hver kodeenhet sin egen tre-bytes sekvens
(`ed a0 80`), og den sekvensen kan ikke oppstå fra noe gyldig kodepunkt —
kodingen forblir injektiv, og ingen eksisterende avtrykk endrer verdi
(de inneholder per definisjon ingen surrogater, ellers hadde de kastet).

IKKE for `jcs.kanoniske_bytes`. De bytene SIGNERES, og en signatur er et
løfte om en nøyaktig bytesekvens. Der er riktig svar på «denne verdien har
ingen lovlig UTF-8-form» å nekte, ikke å finne på en. Regelen her gjelder
avtrykk som må være TOTALE, ikke det som skrives under på.
"""
from __future__ import annotations


def utf8(tekst: str) -> bytes:
    """Tekst -> bytes for hashing og avtrykk. Kaster aldri på `str`."""
    return tekst.encode("utf-8", errors="surrogatepass")
