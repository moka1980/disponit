"""Kjøringen (klarsignalet §7): porsjonsvis gjennom bunten, evaluering
per kandidat — og AVBRUTT KJØRING PROMOTERER INGENTING (port 28).

Kontrakten er SP-3s: ett rent utfall. Enten kommer HELE resultatet
(hver kandidat evaluert, listeutkastene bygget), eller så kommer et
kodet feilutfall uten noe resultat i det hele tatt — det finnes ingen
vei ut av denne fila med en halv liste. Gjenopptak er en NY bestilling;
delresultater holdes aldri varme.
"""
from __future__ import annotations

from collections.abc import Mapping
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


def _felter(kandidatfelter_for, medlem, fremdrift):
    """Feltuttrekket er FREMMED kode på nøyaktig samme måte som
    tekstuttrekket — og det er INJISERT av samme grunn (Codex P2).

    Uten denne vakten falt et unntak fra den strukturerte søknaden helt
    ned til catch-allen og kom ut som `modellfeil`. Da leste både
    arbeiderens retry og driftsdiagnostikken at MODELLEN sviktet på et
    inndatauttrekk som er rent deterministisk og aldri hadde vært i
    nærheten av modellen: koden er utfallets eneste data (SP-3), og en
    vranglest søknadsform skal ikke sende noen på leting etter modellen.
    """
    try:
        felter = kandidatfelter_for(medlem)
    except Exception as feil:
        raise Kjoringsfeil("feltuttrekk_feilet", fremdrift) from feil
    # EN UTTREKKER MELDER OGSÅ FEIL VED Å GI TILBAKE INGENTING (Codex P2).
    # Vakten over fanget bare REISTE unntak. Signaliserer den strukturerte
    # søknaden sin vranglesning ved å returnere `None` — eller noe annet
    # som ikke er et kart — slapp verdien gjennom her, og først nede i
    # `_flett_felter` røk `dict(nye)`. Det unntaket har ingen vakt over
    # seg: det faller til catch-allen og kommer ut som `modellfeil`, altså
    # nøyaktig den feilattribusjonen denne funksjonen ble laget for å
    # hindre. Returtypen måles derfor her, som `_tekst` måler sin.
    #
    # PORTEN MÅLER KONTRAKTEN, IKKE ÉN IMPLEMENTASJON AV DEN (Codex P2).
    # Sto det `isinstance(felter, dict)` her, avviste vakten en
    # `MappingProxyType` eller en `UserDict` — former `_flett_felter` alt
    # tok imot, fordi `dict(nye)` der nede normaliserer et hvilket som
    # helst kart. Vakten skulle stanse `None` og annet som IKKE er et
    # kart; i stedet snevret den inn kontrakten for den injiserte
    # uttrekkeren og gjorde et gyldig uttrekk til et kodet feilutfall.
    # `Mapping` er den kontrakten forbrukeren faktisk krever.
    if not isinstance(felter, Mapping):
        raise Kjoringsfeil("feltuttrekk_feilet", fremdrift)
    return felter


def kjor_bunt(sti, modell, *, vekter, kandidatfelter_for, tekst_for,
              biasmaalinger, antall_soknader, blinding_av=False,
              auditrad=None):
    """-> {"rangering": [...], "artefakter": {kandidat_id: ...},
    "fremdrift": {...}} — eller Kjoringsfeil, aldri noe imellom.

    `kandidatfelter_for(medlem)` er innslaget fra den strukturerte
    søknaden (blindingens kilde); kandidat-identiteten er MANIFESTETS
    (#161, `soknader.json` — `les_manifest`-kartet), aldri medlemsstiens
    mappenavn.

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
        # #161 (eiers B): kandidatene DEKLARERES av buntens eget
        # `soknader.json` og bindes toveis mot katalogen FØR én byte
        # innhold pakkes ut — «den så ut som en søknad» er ikke en
        # inspeksjon. Deklarert kandidattall måles mot oppdragets
        # signerte tall her, foran strømmen: et avvik er en ugyldig
        # bunt, aldri et resultat.
        kart = parsing.les_manifest(sti, parsing.inspiser_bunt(sti))
        if len(set(kart.values())) != antall_soknader:
            raise Kjoringsfeil("kandidattall_avvik", fremdrift)
        # LAGRINGSHÅNDTEREREN HØRER TIL LESINGEN, IKKE HELE KJØRINGEN
        # (Codex P2). Denne indre `try`-en dekker BARE arkivgaten. Sto
        # håndtereren nede blant de øvrige, dekket den også `modell.vurder`,
        # og en `ConnectionResetError` fra modellklienten — en `OSError` MED
        # errno som alle andre — ble meldt som `infrastrukturfeil`. Forrige
        # runde flyttet lagringsfeilen ut av modellkøen; uten denne
        # innsnevringen tok den modellens egne nettverksfeil med seg samme
        # vei, og da leter driften etter et lagringsavbrudd som aldri fant
        # sted. Kilden avgjør koden, og kilden er hvor unntaket oppsto.
        try:
            for merke, medlem, data in parsing.les_porsjonsvis(sti):
                # FREMDRIFTEN TELLER MEDLEMMER, IKKE SJEKKPUNKTER (Codex P2).
                # `les_porsjonsvis` leverer et merke bare hver 200. fil og på
                # det siste medlemmet; sto `fremdrift` stille mellom dem,
                # meldte et utfall på medlem 150 `filer_lest: 0` — og etter
                # en porsjonsgrense kunne det underrapportere med opptil 199.
                # Dette feltet er kontraktens EVIDENS for hvor langt
                # kjøringen kom (§7), så det som telles må være det som
                # faktisk er lest. `byte_lest`/`filer_totalt` er strømmens
                # egne målinger og hentes fortsatt fra siste merke — de
                # gjettes ikke her.
                lest += 1
                fremdrift = (dict(merke) if merke
                             else {**fremdrift, "filer_lest": lest})
                navn = medlem.navn.replace("\\", "/")
                # Kandidaten er MANIFESTETS dom, aldri mappenavnets
                # (#161): toveisbindingen over garanterer at hvert
                # strømmet medlem har nøyaktig én linje i kartet, og
                # tallporten foran strømmen har alt målt deklarert mot
                # signert — in-strøm-tellingen fra #210 er dermed
                # AVLØST, ikke fjernet: dens jobb gjøres nå før uttrekk
                # i det hele tatt starter.
                #
                # …men OPPSLAGET FEILER KODET, IKKE MED KeyError (Cursor
                # P2). `kart[...]` leste garantien som om den var målt
                # her: bindingen skjedde mot `inspiser_bunt`s katalog, og
                # `les_porsjonsvis` åpner arkivet PÅ NYTT. Byttes fila i
                # vinduet mellom dem — eller divergerer de to lesningene
                # av en annen grunn — er et umatchet medlem nøyaktig det
                # `medlem_uadressert` finnes for; `KeyError` ga i stedet
                # catch-allens `modellfeil`, altså feil kø og feil alarm
                # for en bunt modellen aldri fikk se.
                kandidat_id = kart.get(medlem.navn)
                if kandidat_id is None:
                    raise Kjoringsfeil("medlem_uadressert", fremdrift)
                biter.setdefault(kandidat_id, []).append(
                    (navn, medlem.navn,
                     _tekst(tekst_for, medlem, data, fremdrift),
                     _felter(kandidatfelter_for, medlem, fremdrift)))
        except OSError as feil:
            # LAGRINGEN ER IKKE MODELLEN (Codex P2). `les_porsjonsvis`
            # slipper MED VILJE en `OSError` MED errno gjennom som seg selv:
            # en lesefeil på disk eller nettlager er DRIFT, ikke en påstand
            # om kundens bunt — å kalle den `korrupt_bunt` ville gjort vår
            # feil til en kundeavvisning (parsing.py, «`OSError` MED errno
            # er noe helt annet»). Catch-allen under pakket den likevel som
            # «modellfeil», og da leste både arbeiderens retry og
            # driftsdiagnostikken et lagringsavbrudd som at MODELLEN sviktet:
            # feil kø, feil alarm, feil sak. Utfallet får derfor sin egen
            # kode — fortsatt kodet, så SP-3 står, men riktig adressert.
            #
            # Den ERRNO-LØSE formen er dekompressorens («Invalid data
            # stream»), og den er alt oversatt til `korrupt_bunt` før den
            # kommer hit; kom den likevel, er den fremmed kode som alt annet.
            if feil.errno is None:
                raise Kjoringsfeil("modellfeil", fremdrift) from feil
            raise Kjoringsfeil("infrastrukturfeil", fremdrift) from feil
        # EN BUNT UTEN KANDIDATER ER IKKE EN FULLFØRT EVALUERING (Codex P2).
        # Er zip-en tom, eller bærer den bare katalogoppføringer, yielder
        # `les_porsjonsvis` ingenting: `biter` blir tom, løkken under kjører
        # aldri, `ranger({}, ...)` gir en tom liste — og kjøringen returnerte
        # et VELLYKKET utfall med tom rangering og tomt artefaktkart. Da har
        # oppdraget «lyktes» uten at én eneste søknad ble vurdert, og
        # promoteringsvakten i 056 får en gyldig, tom liste å slippe videre.
        # Kontrakten sier `antall_soknader` er 1–5000 (payload-skjemaet), så
        # null kandidater er per definisjon en ugyldig bunt — og en ugyldig
        # bunt er SP-3s kodede utfall, aldri et resultat.
        if not biter:
            raise Kjoringsfeil("tom_bunt", fremdrift)
        # Sluttporten står som DEFENSE (mekanismen er nå manifestets
        # toveisbinding + tallporten foran strømmen): faller de, skal
        # dette aldri passere stille.
        if len(biter) != antall_soknader:
            raise Kjoringsfeil("kandidattall_avvik", fremdrift)
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
