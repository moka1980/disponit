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
            #
            # EN UGYLDIG FORM SOM KOMMER SIST, VINNER (Codex P1). Sto det
            # bare `rad is None` her, ble `{"kontakt": "annen@eksempel.no"}`
            # i søknadsbrevet KASTET fordi CV-en alt hadde levert en gyldig
            # liste for samme felt: `blind` fikk aldri se den ugyldige
            # formen, kunne ikke felle den, og en personopplysning som bare
            # sto i det senere dokumentet ble med i den samlede teksten til
            # modellen umaskert. Fail-closed må måle DEN VERSTE formen
            # feltet kom i, ikke den første — så en ugyldig verdi settes
            # også over en gyldig rad, og porten der nede stopper kjøringen.
            if rad is None or not isinstance(verdier, (list, tuple)):
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

    KJENT BEGRENSNING — MINNET ER IKKE BUNDET (Codex G6/P1, utsatt til
    #173). `biter` holder hvert utpakkede dokument til hele arkivet er
    lest, og returverdien bærer `kildetekst` for HVER kandidat:
    topppunktet er Θ(hele buntens tekst), uansett hvor små porsjoner
    arkivgaten leser i. En bunt som passerte hver eneste arkivgrense kan
    derfor fortsatt OOM-drepe arbeideren. Det lar seg ikke lukke smått:
    å binde minnet krever at RETURKONTRAKTEN over endres — artefaktene
    strømmes til kandidatlagrene (057) underveis, og retur blir
    referanser + rangering, med SP-3-atomisiteten flyttet fra minnet til
    promoteringsvakten som alt står i 056. Det er ny maskin, og K1 sier
    egen PR. Eierens K2-dom (23/8) er valg 1, og den bærer HARD SPERRE:
    ingen kjøring mot reelle bunter i full størrelse før #173 er landet.
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
    # Om minnet: tekstbitene holdes til evalueringen, og det er ingen ny
    # størrelsesorden — `evaluer_kandidat` returnerer `kildetekst` per
    # kandidat, så resultatet bærer allerede hele buntens tekst. Det er
    # nettopp DET som er G6 (se docstringen): topppunktet er ubundet i
    # begge ender, og #173 binder begge ved å strømme artefaktene.
    # Selve STRØMMINGEN er uendret: arkivgatens grenser måles fortsatt
    # per medlem under lesing, aldri på en utpakket bunt.
    #
    # Biten er (medlemsnavn, tekst, felter): FELTENE FØLGER MEDLEMMET SITT
    # HELE VEIEN (Codex P2). Teksten ble sortert på medlemsnavn her nede,
    # men feltene ble flettet i lesesløyfa — altså i zip-rekkefølge. Bidro
    # to filer for samme kandidat ulike verdier til samme maskerte felt,
    # ga en ombyttet arkivrekkefølge en annen listerekkefølge, og
    # `blinding.blind` nummererte tokenene ulikt: samme dokumenter, ulik
    # `kildetekst`, ulik artefakt. Determinismen C2 innførte for teksten
    # gjelder feltene like fullt.
    #
    # RÅNAVNET FØLGER MED SOM SKILLETEGN (Codex P2). Biten bærer BEGGE
    # navnene — det normaliserte og medlemmets eget — fordi det
    # normaliserte alene ikke er en entydig nøkkel: se sorteringen under.
    biter: dict[str, list[tuple[str, str, str, object]]] = {}
    lest = 0
    try:
        for merke, medlem, data in parsing.les_porsjonsvis(sti):
            # FREMDRIFTEN TELLER MEDLEMMER, IKKE SJEKKPUNKTER (Codex P2).
            # `les_porsjonsvis` leverer et merke bare hver 200. fil og på
            # det siste medlemmet; sto `fremdrift` stille mellom dem, meldte
            # et utfall på medlem 150 `filer_lest: 0` — og etter en
            # porsjonsgrense kunne det underrapportere med opptil 199.
            # Dette feltet er kontraktens EVIDENS for hvor langt kjøringen
            # kom (§7), så det som telles må være det som faktisk er lest.
            # `byte_lest`/`filer_totalt` er strømmens egne målinger og
            # hentes fortsatt fra siste merke — de gjettes ikke her.
            lest += 1
            fremdrift = (dict(merke) if merke
                         else {**fremdrift, "filer_lest": lest})
            navn = medlem.navn.replace("\\", "/")
            kandidat_id = navn.split("/")[0]
            biter.setdefault(kandidat_id, []).append(
                (navn, medlem.navn,
                 _tekst(tekst_for, medlem, data, fremdrift),
                 kandidatfelter_for(medlem)))
        for kandidat_id in sorted(biter):
            # Sortert på medlemsnavn: samme bunt gir samme tekst OG samme
            # feltrekkefølge, uansett hvilken rekkefølge arkivet leverte
            # medlemmene i. Nøkkelen er navnene alene — to biter kan ellers
            # bli sammenlignet på feltdikten, som ikke har noen orden.
            #
            # RÅNAVNET BRYTER LIKHETEN (Codex P2). Det normaliserte navnet
            # er IKKE entydig: en bunt kan lovlig bære både `k1/a.html` og
            # `k1\a.html` — parsingen tar imot begge (bare traversering og
            # endelse måles på den normaliserte formen), og linjen over
            # gjør dem til samme navn. Da ble denne sorteringen et
            # uavgjort, og `sorted` er stabil: rekkefølgen falt tilbake på
            # ARKIVREKKEFØLGEN, altså nøyaktig den avhengigheten C2 fjernet.
            # Ombyttede oppføringer ga en annen feltflettingsrekkefølge og
            # dermed en annen tokentildeling — `[NAVN-1]` kunne peke på en
            # annen person. Rånavnet er medlemmets egen, entydige nøkkel
            # (buntgaten avviser duplikater av den), så det avgjør likheten.
            medlemmer = sorted(biter[kandidat_id], key=lambda bit: bit[:2])
            tekst = "\n\n".join(bit[2] for bit in medlemmer)
            kandidatfelter: dict = {}
            for *_, nye in medlemmer:
                _flett_felter(kandidatfelter, nye)
            # EN TOM SØKNAD ER IKKE EN DÅRLIG SØKNAD (Codex P1). Porten
            # over måler bare at uttrekket ER en `str`, og en skannet pdf
            # uten OCR, en docx uten lesbare avsnitt og en html som bare
            # er markup gir alle `""` eller bare blanktegn. Da får
            # modellen ingenting å vurdere — og den svarer skjemakomplett
            # «ingen krav oppfylt», som blir en VELLYKKET artefakt og en
            # plassering nederst i rangeringen. Kandidaten er dermed
            # vurdert som om søknaden var tom, i stillhet, i stedet for
            # at uttrekket meldes som det som feilet.
            if not tekst.strip():
                raise Kjoringsfeil("tekstuttrekk_feilet", fremdrift)
            resultat = evaluering.evaluer_kandidat(
                modell, tekst, kandidatfelter, vekter,
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
