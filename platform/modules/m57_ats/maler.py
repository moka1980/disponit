"""Malene (klarsignalet §6): plattformeid STRUKTUR og flettefelt,
kundeeid tone/firmatekst — og INGEN vei fra modellutdata til
utsendingstekst.

Det siste er en statisk port (port 13), og denne fila er hele beviset:
den importerer aldri `evaluering` eller noe modellsymbol, og `flett` tar
bare verdier for feltene malen selv deklarerer. Et funn refereres med
funn-ID (sporbart), aldri med funnets tekst — teksten i utsendelsen er
malens, ikke modellens.
"""
from __future__ import annotations

import re


class Malfeil(Exception):
    def __init__(self, kode: str, detalj: str = ""):
        self.kode = kode
        super().__init__(f"{kode}: {detalj}" if detalj else kode)


#: Plattformens maler: lukket sett, lukkede flettefelt. `tekst` er
#: strukturen; kundeeid tone kommer som `firmatekst` — et flettefelt som
#: alle andre, satt av kunden i policyflaten, aldri av modellen.
MALER: dict[str, dict] = {
    "invitasjon": {
        "malversjon": "invitasjon-v1",
        "felter": frozenset({"stilling", "kandidatnavn", "tidsvalg_lenke",
                             "firmatekst"}),
        "tekst": ("Hei {kandidatnavn},\n\n"
                  "vi inviterer deg til intervju for stillingen"
                  " {stilling}. Velg et tidspunkt som passer deg her:"
                  " {tidsvalg_lenke}\n\n{firmatekst}"),
    },
    "avslag": {
        "malversjon": "avslag-v1",
        "felter": frozenset({"stilling", "kandidatnavn", "funn_id",
                             "firmatekst"}),
        "tekst": ("Hei {kandidatnavn},\n\n"
                  "takk for søknaden din til stillingen {stilling}."
                  " Vi går dessverre ikke videre med den. Vurderingen"
                  " er sporbar hos oss med referanse {funn_id}.\n\n"
                  "{firmatekst}"),
    },
}

_FELTMONSTER = re.compile(r"\{([a-z_]+)\}")


def flett(malnavn: str, felter: dict[str, str]) -> dict:
    """-> {malversjon, tekst}. Feltene må være NØYAKTIG malens (port 14):
    et felt utenfor malen er avvist, et manglende felt likeså — en mal
    med hull er ikke en utsendingstekst. Verdiene må være rene strenger
    uten flettefeltsyntaks (ingen andreordens fletting)."""
    mal = MALER.get(malnavn)
    if mal is None:
        raise Malfeil("ukjent_mal", malnavn)
    gitt = set(felter)
    if gitt - mal["felter"]:
        raise Malfeil("flettefelt_utenfor_malen",
                      ",".join(sorted(gitt - mal["felter"])))
    if mal["felter"] - gitt:
        raise Malfeil("flettefelt_mangler",
                      ",".join(sorted(mal["felter"] - gitt)))
    for navn, verdi in felter.items():
        if not isinstance(verdi, str) or _FELTMONSTER.search(verdi):
            raise Malfeil("ugyldig_feltverdi", navn)
    return {"malversjon": mal["malversjon"],
            "tekst": mal["tekst"].format(**felter)}
