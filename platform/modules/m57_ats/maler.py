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


class KundeeidFirmatekst:
    """Firmateksten som REFERANSE (#160, klarsignalet §6): den eneste
    lovlige bæreren av kundens tone inn i `flett`. Konstrueres av
    resolveren i core — et oppslag i det kundeeide, versjonerte
    utsendingstekst-lageret (079) — aldri av modellsiden. Port 13 måler
    fortsatt at denne fila ikke importerer noe modellsymbol; typekravet
    i `flett` måler at en fri streng aldri når malen — dataflyt gjennom
    en KALLER er nå også stengt, for kalleren har ingen strengvei å
    sende modellprosa inn."""

    __slots__ = ("tekst_id", "versjon", "tekst")

    def __init__(self, tekst_id: str, versjon: int, tekst: str):
        self.tekst_id = tekst_id
        self.versjon = versjon
        self.tekst = tekst


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
        "felter": frozenset({"stilling", "kandidatnavn",
                             "tidsvalg_lenke"}),
        "tekst": ("Hei {kandidatnavn},\n\n"
                  "vi inviterer deg til intervju for stillingen"
                  " {stilling}. Velg et tidspunkt som passer deg her:"
                  " {tidsvalg_lenke}\n\n{firmatekst}"),
    },
    "avslag": {
        "malversjon": "avslag-v1",
        "felter": frozenset({"stilling", "kandidatnavn", "funn_id"}),
        "tekst": ("Hei {kandidatnavn},\n\n"
                  "takk for søknaden din til stillingen {stilling}."
                  " Vi går dessverre ikke videre med den. Vurderingen"
                  " er sporbar hos oss med referanse {funn_id}.\n\n"
                  "{firmatekst}"),
    },
}

_FELTMONSTER = re.compile(r"\{([a-z_]+)\}")

#: (#160: `firmatekst` er ute av feltene — «kan være tom» gjelder nå
#: bare referansens fravær, håndtert i `flett` selv. Alle gjenværende
#: felter bærer løftet i setningen de står i og må ha innhold.)


def flett(malnavn: str, felter: dict[str, str], *,
          firmatekst: KundeeidFirmatekst | None = None) -> dict:
    """-> {malversjon, tekst, firmatekst_ref, firmatekst_versjon}.

    Feltene må være NØYAKTIG malens (port 14): et felt utenfor malen er
    avvist, et manglende felt likeså — en mal med hull er ikke en
    utsendingstekst. Verdiene må være rene strenger uten
    flettefeltsyntaks (ingen andreordens fletting) og faktisk bære
    innhold — et tomt påkrevd felt er samme hull som et manglende felt.

    FIRMATEKSTEN ER EN REFERANSE (#160): den tas ALDRI som felt eller
    fri streng — bare som `KundeeidFirmatekst` fra resolverens oppslag
    i det kundeeide lageret, eller `None` («ingen tone», en ekte
    tilstand). Svaret bærer referansen, så utsendelsen er sporbar til
    nøyaktig den forfattede versjonen."""
    mal = MALER.get(malnavn)
    if mal is None:
        raise Malfeil("ukjent_mal", malnavn)
    gitt = set(felter)
    if "firmatekst" in gitt:
        raise Malfeil("firmatekst_er_referanse",
                      "kundens tone hentes fra lageret (079), aldri som"
                      " fri streng")
    if gitt - mal["felter"]:
        raise Malfeil("flettefelt_utenfor_malen",
                      ",".join(sorted(gitt - mal["felter"])))
    if mal["felter"] - gitt:
        raise Malfeil("flettefelt_mangler",
                      ",".join(sorted(mal["felter"] - gitt)))
    for navn, verdi in felter.items():
        if not isinstance(verdi, str) or _FELTMONSTER.search(verdi):
            raise Malfeil("ugyldig_feltverdi", navn)
        if not verdi.strip():
            raise Malfeil("tomt_flettefelt", navn)
    if firmatekst is None:
        tone = ""
        ref, ref_versjon = None, None
    else:
        if not isinstance(firmatekst, KundeeidFirmatekst):
            raise Malfeil("firmatekst_er_referanse", type(firmatekst).__name__)
        if not isinstance(firmatekst.tekst, str) \
                or _FELTMONSTER.search(firmatekst.tekst):
            raise Malfeil("ugyldig_feltverdi", "firmatekst")
        tone = firmatekst.tekst
        ref, ref_versjon = firmatekst.tekst_id, firmatekst.versjon
    return {"malversjon": mal["malversjon"],
            "tekst": mal["tekst"].format(**felter, firmatekst=tone),
            "firmatekst_ref": ref,
            "firmatekst_versjon": ref_versjon}
