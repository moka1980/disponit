"""Kjøringen (klarsignalet §7): porsjonsvis gjennom bunten, evaluering
per kandidat — og AVBRUTT KJØRING PROMOTERER INGENTING (port 28).

Kontrakten er SP-3s: ett rent utfall. Enten kommer HELE resultatet
(hver kandidat evaluert, listeutkastene bygget), eller så kommer et
kodet feilutfall uten noe resultat i det hele tatt — det finnes ingen
vei ut av denne fila med en halv liste. Gjenopptak er en NY bestilling;
delresultater holdes aldri varme.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import blinding, evaluering, parsing


@dataclass(frozen=True)
class Kjoringsfeil(Exception):
    """Det rene feilutfallet: koden + hvor langt kjøringen kom.

    Fremdriften er EVIDENS (hvor mange filer/kandidater som var lest da
    det røk), aldri et delresultat — det finnes ikke noe felt her som
    kan bære en kandidatliste.
    """

    kode: str
    fremdrift: dict = field(default_factory=dict)

    def __str__(self):
        return f"{self.kode} ({self.fremdrift})"


def _flett_felter(samlet, nye):
    """Blindingens kilde er HELE kandidatmappen. Står navnet bare i
    søknadsbrevet, skal det maskeres i CV-en også — feltene fra hvert
    medlem legges derfor sammen (uten duplikater, i den rekkefølgen de
    kom, så tokennummereringen holder seg deterministisk). Formen måles
    av `blinding.blind`; her flettes den bare."""
    for felt, verdier in dict(nye).items():
        rad = samlet.get(felt)
        if not (isinstance(rad, list) and isinstance(verdier, (list, tuple))):
            # Enten er dette første verdien for feltet, eller så er en av
            # de to en form `blind` skal FELLE. Da settes den som den er,
            # og porten der nede måler den — flettingen skjuler aldri en
            # ugyldig maskeringsform bak et pent snitt.
            if rad is None:
                samlet[felt] = (list(verdier)
                                if isinstance(verdier, (list, tuple))
                                else verdier)
            continue
        for verdi in verdier:
            if verdi not in rad:
                rad.append(verdi)


def _tekst(tekst_for, medlem, data, fremdrift):
    """Uttrekket er FREMMED kode (containerens), og feiler det, er det et
    kodet utfall — ikke en rå `PdfReadError` ut av modulen."""
    try:
        tekst = tekst_for(medlem, data)
    except Exception as feil:
        raise Kjoringsfeil("tekstuttrekk_feilet", fremdrift) from feil
    if not isinstance(tekst, str):
        # En uttrekker som gir tilbake bytene sine (eller None) er samme
        # feil som den vi kom fra: da hadde modellen fått binærstøy igjen.
        raise Kjoringsfeil("tekstuttrekk_feilet", fremdrift)
    return tekst


def kjor_bunt(sti, modell, *, vekter, kandidatfelter_for, tekst_for,
              biasmaalinger, blinding_av=False, auditrad=None):
    """-> {"rangering": [...], "artefakter": {kandidat_id: ...},
    "fremdrift": {...}} — eller Kjoringsfeil, aldri noe imellom.

    `kandidatfelter_for(medlem)` er innslaget fra den strukturerte
    søknaden (blindingens kilde); kandidat-id er medlemsstiens første
    ledd (én mappe per kandidat, m56-fasitformen).

    `tekst_for(medlem, data)` ER TEKSTUTTREKKET, OG DET ER PÅKREVD
    (Codex P1). To av de tre lovede innholdstypene er BINÆRE: en docx er
    en komprimert OPC-pakke og en pdf har sin egen interne koding, så
    `data.decode("utf-8", errors="replace")` ga ikke søknaden — den ga
    modellen støy med U+FFFD, og en evaluering av støy er en evaluering
    som ser gyldig ut og er verdiløs. Selve uttrekket hører hjemme i den
    credential-frie, nettverksløse containeren (§7, port 24-formen, jf.
    `parsing`-modulens egen dør), og kjøringen skal derfor FÅ det inn,
    aldri gjette det: uten en uttrekker finnes det ingen kjøring. Feiler
    uttrekket, er det et kodet utfall som alt annet
    (`tekstuttrekk_feilet`), ikke en rå bibliotekfeil.
    """
    artefakter: dict[str, dict] = {}
    oppfylt: dict[str, dict] = {}
    fremdrift: dict = {"filer_lest": 0, "filer_totalt": 0, "byte_lest": 0}
    # ÉN KANDIDAT, ÉN EVALUERING (Codex P1). Én mappe kan inneholde både
    # CV og søknadsbrev, og med `artefakter[kandidat_id] = resultat` per
    # MEDLEM vant den siste: kvalifikasjoner og funn fra de øvrige filene
    # forsvant i stillhet, og rangeringen avhang av zip-medlemmenes
    # rekkefølge. Mappen samles derfor først og evalueres én gang.
    #
    # Om minnet: tekstbitene holdes til evalueringen, men det er ingen ny
    # størrelsesorden — `evaluer_kandidat` returnerer `kildetekst` per
    # kandidat, så resultatet bærer allerede hele buntens tekst. Selve
    # STRØMMINGEN er uendret: arkivgatens grenser måles fortsatt per
    # medlem under lesing, aldri på en utpakket bunt.
    biter: dict[str, list[tuple[str, str]]] = {}
    felter: dict[str, dict] = {}
    try:
        for merke, medlem, data in parsing.les_porsjonsvis(sti):
            if merke:
                fremdrift = merke
            navn = medlem.navn.replace("\\", "/")
            kandidat_id = navn.split("/")[0]
            biter.setdefault(kandidat_id, []).append(
                (navn, _tekst(tekst_for, medlem, data, fremdrift)))
            _flett_felter(felter.setdefault(kandidat_id, {}),
                          kandidatfelter_for(medlem))
        for kandidat_id in sorted(biter):
            # Sortert på medlemsnavn: samme bunt gir samme tekst, uansett
            # hvilken rekkefølge arkivet leverte medlemmene i.
            tekst = "\n\n".join(
                tekst for _, tekst in sorted(biter[kandidat_id]))
            resultat = evaluering.evaluer_kandidat(
                modell, tekst, felter[kandidat_id], vekter,
                biasmaalinger=biasmaalinger,
                blinding_av=blinding_av, auditrad=auditrad)
            artefakter[kandidat_id] = resultat
            oppfylt[kandidat_id] = resultat["oppfylt"]
        # RANGERINGEN ER EN DEL AV KJØRINGEN (Codex P1). Sto den utenfor
        # `try`, slapp `ranger`s egne kodede feil — ugyldige vekter, krav
        # utenfor profilen, ikke-boolsk oppfyllelse — ut som RÅ
        # `Evalueringsfeil`, mens nøyaktig samme unntakstype ble oversatt
        # til `Kjoringsfeil` når den kom fra evalueringen én linje over.
        # I arbeideren er det forskjellen på det rene SP-3-utfallet og en
        # uventet arbeiderfeil.
        rangering = evaluering.ranger(oppfylt, vekter)
    except Kjoringsfeil:
        # Alt som alt ER utfallet, går videre som seg selv: uten denne
        # linjen ville catch-allen under pakket det inn på nytt som
        # «modellfeil» og skjult koden det ble reist med.
        raise
    except parsing.Buntfeil as feil:
        raise Kjoringsfeil(feil.kode, fremdrift) from feil
    except evaluering.Evalueringsfeil as feil:
        raise Kjoringsfeil(feil.kode, fremdrift) from feil
    except blinding.Blindingsfeil as feil:
        # Fail-closed-blindingen (K2-dommen 23/8) er også et RENT utfall:
        # tomme strukturerte felter stopper kjøringen med kode, aldri
        # med rå exception — og aldri med rå tekst videre i stillhet.
        raise Kjoringsfeil(feil.kode, fremdrift) from feil
    except Exception as feil:   # modellen er fremmed kode — også dens
        raise Kjoringsfeil("modellfeil", fremdrift) from feil
    return {"rangering": rangering,
            "artefakter": artefakter, "fremdrift": fremdrift}
