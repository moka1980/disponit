"""Malene (klarsignalet §6): plattformeid STRUKTUR og flettefelt,
kundeeid tone/firmatekst — og INGEN vei fra modellutdata til
utsendingstekst.

Det siste er en statisk port (port 13), og porten måler IMPORTGRAFEN:
denne fila importerer aldri `evaluering` eller noe modellsymbol, og
`flett` tar bare verdier for feltene malen selv deklarerer. Et funn
refereres med funn-ID (sporbart), aldri med funnets tekst — teksten i
utsendelsen er malens, ikke modellens.

Det porten IKKE måler (Codex P2, #160): dataflyt gjennom en kaller.
`firmatekst` tas i dag som en fri streng, så en orkestrator som sender
modellutdata dit, får det ordrett ut i en invitasjon eller et avslag.
Løftet «ingen vei fra modellutdata til utsendingstekst» (§6) er altså
målt i denne fila, ikke i huset. Den ekte lukkingen er at feltet blir en
REFERANSE til kundeeid, lagret tekst — nytt lager, rettighetsgrense,
oppslag og forfatterflate — og den maskinen bor i #160, ikke her.
"""
from __future__ import annotations

import re


class Malfeil(Exception):
    def __init__(self, kode: str, detalj: str = ""):
        self.kode = kode
        super().__init__(f"{kode}: {detalj}" if detalj else kode)


#: Plattformens maler: lukket sett, lukkede flettefelt. `tekst` er
#: strukturen; kundeeid tone kommer som `firmatekst`. «Satt av kunden i
#: policyflaten» er MÅLET (#160), ikke dagens tilstand: policyflaten
#: finnes ikke ennå, og feltet er inntil videre en verdi kalleren
#: oppgir — det er kallerens ansvar at den er kundens, og ingen port her
#: kan si det for den.
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

#: Feltene som LOVLIG kan være tomme (Codex P2). `firmatekst` er kundens
#: tone, og «ingen tone» er en ekte tilstand — en kunde som ikke har satt
#: noe, skal få malens struktur uten et påhengt avsnitt. Alle ANDRE felter
#: bærer selve løftet i setningen de står i: en invitasjon uten
#: `tidsvalg_lenke` ber kandidaten velge et tidspunkt «her:» og peker
#: ingen steder, og et avslag uten `funn_id` sier at vurderingen er
#: sporbar med en referanse som ikke finnes. Porten målte bare TYPEN og
#: flettesyntaksen, så en tom streng passerte som en verdi. Den er ikke en
#: verdi — den er et hull med riktig type.
_KAN_VAERE_TOMME = frozenset({"firmatekst"})


def flett(malnavn: str, felter: dict[str, str]) -> dict:
    """-> {malversjon, tekst}. Feltene må være NØYAKTIG malens (port 14):
    et felt utenfor malen er avvist, et manglende felt likeså — en mal
    med hull er ikke en utsendingstekst. Verdiene må være rene strenger
    uten flettefeltsyntaks (ingen andreordens fletting), og for alle
    felter utenom `_KAN_VAERE_TOMME` må de faktisk bære innhold — et
    tomt påkrevd felt er samme hull som et manglende felt, bare med
    riktig type."""
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
        if navn not in _KAN_VAERE_TOMME and not verdi.strip():
            raise Malfeil("tomt_flettefelt", navn)
    return {"malversjon": mal["malversjon"],
            "tekst": mal["tekst"].format(**felter)}
