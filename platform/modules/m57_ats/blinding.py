"""Blinding (klarsignalet §6): maskering FØR modellsteget, målt på
faktisk input; avskruing er en AUDITERT handling, aldri en stille
parameter.

De maskerte feltene er navngitt i katalogen («navn, kjønn, alder,
adresse, bilde og kontaktfelt») — modulen påstår ikke anonymisering ut
over dem. Av-maskeringstabellen er payload i `kandidat_avmaskering`
(057) og reapes med resten.
"""
from __future__ import annotations

#: Katalogens løfte, ordrett — settet er LUKKET og rekkefølgen stabil
#: (tokennummereringen skal være deterministisk for samme input).
MASKERTE_FELTER: tuple[str, ...] = (
    "navn", "kjonn", "alder", "adresse", "bilde", "kontakt")


class Blindingsfeil(Exception):
    def __init__(self, kode: str):
        self.kode = kode
        super().__init__(kode)


def blind(tekst: str, kandidatfelter: dict[str, list[str]]
          ) -> tuple[str, dict[str, str]]:
    """-> (blindet tekst, avmaskeringstabell {token: klartekst}).

    `kandidatfelter` er de STRUKTURERTE verdiene fra søknaden
    ({felt: [verdier]}); fritekst-gjenkjenning av personalia er bevisst
    IKKE lovet her — løftet er de navngitte feltene.
    """
    ukjente = set(kandidatfelter) - set(MASKERTE_FELTER)
    if ukjente:
        raise Blindingsfeil("ukjent_maskeringsfelt")
    avmaskering: dict[str, str] = {}
    for felt in MASKERTE_FELTER:
        for nr, verdi in enumerate(kandidatfelter.get(felt, ()), start=1):
            if not verdi:
                continue
            token = f"[{felt.upper()}-{nr}]"
            avmaskering[token] = verdi
            tekst = tekst.replace(verdi, token)
    return tekst, avmaskering


def krev_blindet(tekst: str, avmaskering: dict[str, str]) -> None:
    """Porten på FAKTISK modellinput (port 16): ingen av klartekst-
    verdiene får stå i teksten som går til modellen."""
    for token, verdi in avmaskering.items():
        if verdi and verdi in tekst:
            raise Blindingsfeil("maskert_felt_i_modellinput")


def evalueringsinput(tekst: str, kandidatfelter: dict[str, list[str]], *,
                     blinding_av: bool = False,
                     auditrad: dict | None = None
                     ) -> tuple[str, dict[str, str]]:
    """Den ENESTE veien til modellinput. Standard er blindet; avskrudd
    krever en auditrad med aktør, tidspunkt og begrunnelse — mangler
    den, finnes ikke input (port 16b)."""
    if blinding_av:
        if not (isinstance(auditrad, dict)
                and auditrad.get("aktor") and auditrad.get("ts")
                and auditrad.get("begrunnelse")):
            raise Blindingsfeil("avskrudd_uten_auditrad")
        return tekst, {}
    blindet, avmaskering = blind(tekst, kandidatfelter)
    krev_blindet(blindet, avmaskering)
    return blindet, avmaskering
