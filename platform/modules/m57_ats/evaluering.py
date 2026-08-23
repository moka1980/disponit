"""Evaluering (klarsignalet §6): rangering med synlige vekter — aldri
prosent som målt egenskap; risikofunn krever kildereferanse i
søknadsteksten; lukket kategorisett uten karaktertrekk-kategorier;
modellen er et container-image der digesten ER modellversjonen, og
biasmåling bundet til digesten er akseptkrav.

Modellen er INJISERT (m56s motor-form): denne fila eier kontrakten
rundt den — blindet input inn, skjemavaliderte funn ut — aldri selve
kjøringen.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from . import blinding

#: Lukket kategorisett (§6). Ingen karaktertrekk: kategoriene beskriver
#: DOKUMENTASJONEN mot stillingens krav, aldri personen. En ny kategori
#: er en kontraktsendring, ikke en modellidé.
FUNN_KATEGORIER = frozenset({
    "krav_ikke_dokumentert",
    "manglende_dokumentasjon",
    "motstridende_opplysning",
    "uklar_tidslinje",
    "utenfor_soknadsfrist",
})


class Evalueringsfeil(Exception):
    def __init__(self, kode: str, detalj: str = ""):
        self.kode = kode
        super().__init__(f"{kode}: {detalj}" if detalj else kode)


def valider_funn(funn: dict, soknadstekst: str) -> None:
    """Skjemaporten (port 15): et funn uten kildereferanse som ordrett
    står i søknadsteksten, finnes ikke. Sitatet måles mot teksten på
    [start:slutt] — en referanse som ikke treffer er like avvist som en
    som mangler."""
    kategori = funn.get("kategori")
    if kategori not in FUNN_KATEGORIER:
        raise Evalueringsfeil("ukjent_kategori", repr(kategori))
    kilde = funn.get("kilde")
    if not isinstance(kilde, dict):
        raise Evalueringsfeil("uten_kildereferanse")
    start, slutt, sitat = (kilde.get("start"), kilde.get("slutt"),
                           kilde.get("sitat"))
    if (not isinstance(start, int) or not isinstance(slutt, int)
            or not isinstance(sitat, str) or not sitat
            or soknadstekst[start:slutt] != sitat):
        raise Evalueringsfeil("uten_kildereferanse")


def _krev_helt_svar(svar: object, vekter: dict[str, int]) -> dict:
    """Et AVKORTET modellsvar er en FEIL, ikke et tomt resultat
    (Codex P1).

    Artefakten ble bygget med `.get(..., tom)` per felt, så `{}` — det et
    avbrutt eller lengdekuttet svar typisk er — ble til en vellykket
    evaluering: ingen funn, ingen oppfylte krav, ingen intervjuspørsmål.
    Kalleren kunne rangere og promotere den kandidaten som «oppfyller
    ingenting». En avbrutt kjøring skal gi et rent feilutfall (§7), og
    det gjelder også når avbruddet er modellens eget.

    `oppfylt` måles mot PROFILEN: `ranger` avviser krav utenfor den, og
    speilbildet er at et krav som mangler stille ble til null poeng."""
    if not isinstance(svar, dict):
        raise Evalueringsfeil("ufullstendig_modellsvar",
                              type(svar).__name__)
    for felt, form in (("funn", list), ("oppfylt", dict),
                       ("intervjusporsmal", list)):
        if not isinstance(svar.get(felt), form):
            raise Evalueringsfeil("ufullstendig_modellsvar", felt)
    if set(svar["oppfylt"]) != set(vekter):
        raise Evalueringsfeil(
            "ufullstendig_modellsvar",
            "oppfylt: " + ",".join(sorted(
                set(vekter) ^ set(svar["oppfylt"]))))
    if any(not isinstance(s, str) or not s
           for s in svar["intervjusporsmal"]):
        raise Evalueringsfeil("ufullstendig_modellsvar",
                              "intervjusporsmal")
    return svar


def ranger(kandidater: dict[str, dict[str, bool]],
           vekter: dict[str, int]) -> list[dict]:
    """Rangering med SYNLIGE vekter: poengsummen er en sum av
    vekt × oppfylt per krav, og nedbrytningen følger med hvert innslag.
    Ingen prosent, ingen «match score» — poeng er poeng (§6).
    """
    if not vekter or any(not isinstance(v, int) or v < 0
                         for v in vekter.values()):
        raise Evalueringsfeil("ugyldige_vekter")
    ut = []
    for kandidat_id, oppfylt in kandidater.items():
        ukjente = set(oppfylt) - set(vekter)
        if ukjente:
            raise Evalueringsfeil("krav_utenfor_profilen",
                                  ",".join(sorted(ukjente)))
        # Oppfyllelsen er BOOLSK, ikke sannhetsverdien til hva som helst
        # (Codex P2). Modellutdata er ikke typesjekket noe sted, og
        # `"false"` — den vanligste JSON-feilen en modell gjør — er en
        # sann streng: kandidaten fikk hele kravets vekt og rangeringen
        # ble stille feil. En verdi vi ikke kan lese er en feil, aldri et
        # ja.
        ulovlige = [k for k, v in oppfylt.items() if not isinstance(v, bool)]
        if ulovlige:
            raise Evalueringsfeil("ikke_boolsk_oppfyllelse",
                                  ",".join(sorted(ulovlige)))
        nedbrytning = {krav: (vekter[krav] if oppfylt.get(krav) else 0)
                       for krav in vekter}
        ut.append({"kandidat_id": kandidat_id,
                   "poeng": sum(nedbrytning.values()),
                   "nedbrytning": nedbrytning})
    # Stabil orden: poeng synkende, deretter kandidat-id — likhet skal
    # være synlig som likhet, aldri stille avgjort av dict-rekkefølgen.
    ut.sort(key=lambda k: (-k["poeng"], k["kandidat_id"]))
    return ut


@dataclass(frozen=True)
class Biasmaaling:
    image_digest: str
    artefakt_sha256: str
    ts: str


def _er_sha256(verdi: object) -> bool:
    return (isinstance(verdi, str) and len(verdi) == 64
            and all(c in "0123456789abcdef" for c in verdi.lower()))


def krev_biasmaaling(image_digest: str,
                     maalinger: dict[str, Biasmaaling]) -> Biasmaaling:
    """Port 17: et imagebytte uten NY biasmåling blokkerer aksepten.
    Målingen er bundet til digesten — samme modellfil bak ny digest er
    en ny modellversjon med et nytt bevisbehov.

    En OPPFØRING er ikke en måling (Codex P2): porten sjekket bare at det
    lå noe under digesten og at objektet gjentok den, så
    `Biasmaaling(digest, "", "")` — uten artefakthash og uten tidspunkt —
    passerte som bevis. Feltene måles derfor: digesten på formen
    `sha256:<64 hex>`, artefakthashen som sha256, tidspunktet som
    lesbar ISO 8601.

    At artefakten hashen peker på FINNES, måles ikke her: evidensgrensen
    `m57-v1` eier oppslaget mot artefaktlageret, og et lager denne fila
    ikke har, ville vært en ny maskin i en fiksrunde."""
    maaling = maalinger.get(image_digest)
    if maaling is None or maaling.image_digest != image_digest:
        raise Evalueringsfeil("bias_maling_mangler_for_digest",
                              image_digest)
    if not (isinstance(image_digest, str)
            and image_digest.startswith("sha256:")
            and _er_sha256(image_digest[7:])):
        raise Evalueringsfeil("bias_maling_ugyldig_digest", image_digest)
    if not _er_sha256(maaling.artefakt_sha256):
        raise Evalueringsfeil("bias_maling_uten_artefakt", image_digest)
    try:
        datetime.fromisoformat(str(maaling.ts).replace("Z", "+00:00"))
    except (TypeError, ValueError) as feil:
        raise Evalueringsfeil("bias_maling_uten_tidspunkt",
                              image_digest) from feil
    return maaling


def evaluer_kandidat(modell, soknadstekst: str,
                     kandidatfelter: dict[str, list[str]],
                     vekter: dict[str, int], *,
                     biasmaalinger: dict[str, Biasmaaling],
                     blinding_av: bool = False,
                     auditrad: dict | None = None) -> dict:
    """Én kandidat gjennom hele kontrakten: biasmåling for modellens
    digest (port 17), blindet input (port 16), skjemavaliderte funn
    (port 15). Modellen får ALDRI se råteksten når blinding står på —
    rekkefølgen her er selve invarianten, ikke en implementasjonsdetalj.

    Funnenes `kilde.start/slutt` indekserer `kildetekst` i returverdien —
    ALDRI `soknadstekst`. Blindingen endrer lengder, så de to er ikke
    samme koordinatsystem.
    """
    krev_biasmaaling(modell.image_digest, biasmaalinger)
    tekst, avmaskering = blinding.evalueringsinput(
        soknadstekst, kandidatfelter,
        blinding_av=blinding_av, auditrad=auditrad)
    blinding.krev_blindet(tekst, avmaskering)
    svar = _krev_helt_svar(modell.vurder(tekst, vekter), vekter)
    for funn in svar["funn"]:
        valider_funn(funn, tekst)
    # `kildetekst` er strengen kildereferansene faktisk indekserer, og den
    # følger med artefakten (Codex P2). Blindingen ENDRER lengder — «Kari»
    # blir `[NAVN-1]` — så en [start:slutt] validert mot den blindede
    # teksten peker på noe annet i råsøknaden. Å sende offsetene videre
    # uten strengen de hører til, var å invitere til nettopp den
    # forvekslingen; her er referansen entydig, og verifiserbar av
    # mottakeren med samme snitt som `valider_funn` bruker.
    return {"funn": list(svar["funn"]),
            "oppfylt": dict(svar["oppfylt"]),
            "intervjusporsmal": list(svar["intervjusporsmal"]),
            "avmaskering": avmaskering,
            "kildetekst": tekst}
