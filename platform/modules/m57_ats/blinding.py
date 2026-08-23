"""Blinding (klarsignalet §6): maskering FØR modellsteget, målt på
faktisk input; avskruing er en AUDITERT handling, aldri en stille
parameter.

Løftet er PRESIST, og ordlyden er valgt for å ikke lese sterkere enn
porten måler: modulen maskerer **de navngitte feltene i sin kanoniske
form** — «navn, kjønn, alder, adresse, bilde og kontaktfelt» slik de
står i de STRUKTURERTE verdiene. Den påstår ikke anonymisering ut over
dem, og den påstår ikke å finne personalia som bare finnes i fritekst.
En invariant som hviler på et SØK i fritekst kan ikke være absolutt:
fire runder har målt lekkasje ut (delstrenger, versaler, NFC/NFD) og
korrupsjon inn fra samme grense. Den absolutte formen er strukturell
blinding — personfeltene finnes ikke i inputen i det hele tatt — og den
er eiers ratifiserte mål (B-veien, eget issue + egen PR); til den lander
er ordlyden over det ærlige løftet.

Av-maskeringstabellen er payload i `kandidat_avmaskering` (057) og
reapes med resten.
"""
from __future__ import annotations

import re

#: Katalogens løfte, ordrett — settet er LUKKET og rekkefølgen stabil
#: (tokennummereringen skal være deterministisk for samme input).
MASKERTE_FELTER: tuple[str, ...] = (
    "navn", "kjonn", "alder", "adresse", "bilde", "kontakt")


class Blindingsfeil(Exception):
    def __init__(self, kode: str):
        self.kode = kode
        super().__init__(kode)


def _monster(verdi: str) -> re.Pattern[str]:
    """Personverdien som mønster — VERSALUFØLSOMT (Codex P1).

    De strukturerte feltene og dokumentet er to kilder som sjelden er
    enige om store bokstaver: metadata sier `Kari`, CV-en skriver `KARI`
    i overskriften. `str.replace` og `in` er begge versalfølsomme, så
    navnet gikk umaskert til modellen — og `krev_blindet` lette etter
    nøyaktig samme skrivemåte og fant den ikke, så porten sa god for
    lekkasjen. Samme mønster brukes derfor BEGGE steder: det som
    maskeres og det som måles er per definisjon den samme testen.

    Løftet er versalene, ikke normaliseringsformer: `re.IGNORECASE`
    gjør Unicodes enkle case-folding, og en NFC/NFD-forskjell i kilden
    er fortsatt en forskjell."""
    return re.compile(re.escape(verdi), re.IGNORECASE)


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
    par: list[tuple[str, str]] = []
    for felt in MASKERTE_FELTER:
        for nr, verdi in enumerate(kandidatfelter.get(felt, ()), start=1):
            if not verdi:
                continue
            token = f"[{felt.upper()}-{nr}]"
            avmaskering[token] = verdi
            par.append((token, verdi))
    # LENGSTE VERDI FØRST, på tvers av alle felter (Codex P1). Erstatning
    # i feltrekkefølge var to lekkasjer i én: «Ola» før «Ola Nordmann» gir
    # `[NAVN-1] Nordmann` — etternavnet når modellen — og «Ann» før
    # «Ann@example.com» gir `[NAVN-1]@example.com`, som `krev_blindet`
    # godtar fordi den HELE adressen ikke lenger står der. Token-
    # nummereringen er fortsatt feltrekkefølgen, så den er uendret og
    # deterministisk; bare erstatningsrekkefølgen er lengdestyrt.
    for token, verdi in sorted(par, key=lambda p: -len(p[1])):
        # Erstatningen er en funksjon, ikke en mal: tokenet skal stå
        # ordrett, aldri tolkes som `re`-referanser. Avmaskeringstabellen
        # bærer den STRUKTURERTE skrivemåten — en avmaskering gir altså
        # `Kari` tilbake der dokumentet skrev `KARI`, og det er riktig:
        # feltverdien er kilden, dokumentets versaler er formatering.
        tekst = _monster(verdi).sub(lambda _t, tok=token: tok, tekst)
    return tekst, avmaskering


def krev_blindet(tekst: str, avmaskering: dict[str, str]) -> None:
    """Porten på FAKTISK modellinput (port 16): ingen av klartekst-
    verdiene får stå i teksten som går til modellen."""
    for token, verdi in avmaskering.items():
        if verdi and _monster(verdi).search(tekst):
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
    # FAIL-CLOSED (Codex P1, eiers K2-avgjørelse). Med tomme eller
    # manglende strukturerte felter ble `avmaskering` tom, `krev_blindet`
    # godkjente VAKUØST — den har ingenting å lete etter — og råteksten
    # gikk til modellen mens kjøringen ble registrert som blindet. En
    # port som ikke kan feile er ikke en port; her er avviket at det ikke
    # FINNES noe å måle, og et umålt utfall er et avvist utfall (SP-3).
    #
    # ÆRLIG OM HVA DETTE IKKE LUKKER: dette feller det degenererte
    # tilfellet (ingenting å maskere), ikke det delvise. Et uttrekk som
    # gir `navn` men taper `adresse` passerer fortsatt, like vakuøst for
    # adressen. Fullstendighet har ingen målbar definisjon så lenge
    # invarianten hviler på et fritekstsøk — den kommer med B-veien
    # (strukturell blinding), som er eiers ratifiserte mål. Denne linjen
    # er SP-3 på veien som alt finnes, ikke en femte maskeringsform.
    if not avmaskering:
        raise Blindingsfeil("blinding_uten_felter")
    krev_blindet(blindet, avmaskering)
    return blindet, avmaskering
