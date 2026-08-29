"""Kjøringen (klarsignalet §7): porsjonsvis gjennom bunten, evaluering
per kandidat — og AVBRUTT KJØRING PROMOTERER INGENTING (port 28).

Kontrakten er SP-3s: ett rent utfall. Enten kommer HELE resultatet
(hver kandidat evaluert, listeutkastene bygget), eller så kommer et
kodet feilutfall uten noe resultat i det hele tatt — det finnes ingen
vei ut av denne fila med en halv liste. Gjenopptak er en NY bestilling;
delresultater holdes aldri varme.
"""
from __future__ import annotations

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from . import blinding
from . import modell as modellklient
from . import uttrekk, evaluering, parsing


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
    kodet utfall — ikke en rå `PdfReadError` ut av modulen.

    MEN EN `Uttrekksfeil` ER ALT UTFALLET (Cursor P2, runde 6). Vakten
    under fanget `Exception`, altså også uttrekkerens egne SP-3-koder, og
    `uttrekk_ustottet`/`uttrekk_uleselig` kom ut som den generiske
    `tekstuttrekk_feilet`. Følgen sto å lese i `kjor_bunt`: dens
    `except uttrekk.Uttrekksfeil` var DØD kode — `Uttrekksfeil` reises
    bare i `uttrekk.py`, og eneste vei derfra hit går gjennom denne
    linjen, så ingen nådde noensinne fram til oversetteren. En pdf uten
    `pdftotext` i deploymenten, en docx-bombe og en ugyldig UTF-8-html
    ble alle rapportert som «tekstuttrekket feilet generisk», og både
    driftsloggen og `kjoring_avbrutt:<kode>` pekte bort fra det som
    faktisk gikk galt.

    Koden bæres derfor videre urørt til `kjor_bunt`s egen oversetter, der
    den alt hører hjemme. Ingen ny oversettelse her: to steder som gjør
    om `Uttrekksfeil` til `Kjoringsfeil` er to kilder til samme sannhet.

    NULLBYTEN FJERNES HER (Codex P2). PostgreSQL kan ikke lagre en
    nullbyte i `TEXT` eller `jsonb` i det hele tatt, og et uttrekk fra
    html eller pdf kan lovlig bære en — den passerer arkivgaten og
    uttrekket og felte først på INSERT, som en rå `psycopg.Error` API-et
    oversetter til `db_utilgjengelig`. `lever` leser 5xx som DRIFT,
    brenner hele retrykjeden mot en frisk base, og feller til slutt HELE
    evalueringen som `kandidatlagring_feilet` — med en falsk
    infrastrukturalarm på veien. Én søknad med en artefakt-nullbyte tok
    altså ned buntens 5 000 andre.

    HER, og ikke ved sinken: dette er det ENE stedet fremmed
    uttrekkerkode kommer inn, og teksten går videre til BÅDE
    dokumentlageret, modellen og `kildetekst` i artefaktet. Renset ved
    grensen ser alle tre det samme; renset ved sinken ville modellen
    vurdert én tekst og lageret båret en annen.

    Å fjerne er riktigere enn å avvise nettopp for DENNE byten: den er
    ikke innhold. Ingen leser kan se den, PDF-/HTML-uttrekk produserer
    den som artefakt av kodingen, og evidensen den «endrer» er en byte
    som per konstruksjon ikke kunne vært lagret. Det er ikke
    normaliseringen `rekruttering._kandidater` avviser — der ville et
    ulesbart `funn` normalisert til `[]` gjort kandidaten GRØNNERE, altså
    endret betydning. Her endres ingen betydning; alternativet er å felle
    kjøringen på et usynlig tegn. Plattformdøren avviser den fortsatt
    (`request_feilformet`): modulen skal ikke være lagrenes eneste vern.
    """
    try:
        tekst = tekst_for(medlem, data)
    except uttrekk.Uttrekksfeil:
        raise
    except Exception as feil:
        raise Kjoringsfeil("tekstuttrekk_feilet", fremdrift) from feil
    if not isinstance(tekst, str):
        # En uttrekker som gir tilbake bytene sine (eller None) er samme
        # feil som den vi kom fra: da hadde modellen fått binærstøy igjen.
        raise Kjoringsfeil("tekstuttrekk_feilet", fremdrift)
    return tekst.replace("\x00", "")


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


def _spoletekst(medlemmer, fremdrift):
    """Kandidatens tekst lest tilbake fra spolen — SAMME utfallsklasse
    som lagringen, aldri modellens (Codex P2).

    Spolen er en midlertidig filflate, og en `OSError` derfra er DRIFT:
    disken er full, nettlageret forsvant, fd-en ble stengt under oss.
    Begge passene leste den utenfor strømløkkens `except OSError`, så
    lesefeilen falt helt ned til catch-allen og kom ut som `modellfeil`
    — i et miljø der modellen ikke har sviktet i det hele tatt, og der
    arbeiderens retry og driftsdiagnostikken dermed leser feil kø og
    feil alarm. Nøyaktig misattribusjonen `infrastrukturfeil` ble
    innført for, én kodevei lenger ut.

    Uten errno-splitten fra strømløkken, med vilje: DEN finnes for
    dekompressorens errno-løse «Invalid data stream», og det er ikke en
    form som kan komme ut av vår egen spolefil.

    `newline=""` ER PÅKREVD (Codex P2, #173). Uten den gjør Python
    universell linjeskiftoversettelse på veien inn: `\\r\\n` og enslig
    `\\r` fra uttrekkeren blir `\\n`. Spolen er ikke en logg — den er
    kilden til de EKSAKTE strengsammenligningene nedstrøms, og
    `lagre_dokument` har alt persistert uttrekkerens ORIGINALE tekst.
    Oversettelsen ga derfor to ulike sannheter om samme dokument: et
    manifestfelt med et internt `\\r\\n` — en flerlinjes adresse, for
    eksempel — matchet den uttrukne teksten før spolingen og matchet
    IKKE etterpå, og den blindingssjekken feller et gyldig manifest.
    """
    try:
        return "\n\n".join(_les_spole(bit[2]) for bit in medlemmer)
    except OSError as feil:
        raise Kjoringsfeil("infrastrukturfeil", fremdrift) from feil


def _spoledokumenter(medlemmer, fremdrift):
    """Kandidatens dokumenter lest tilbake HVER FOR SEG (#174): skjøtingen
    skjer først på den blindede siden (`evaluer_kandidat` med
    `blinding.SKJOT`), så et modellsitat aldri kan krysse en
    dokumentgrense råteksten ikke har. Samme utfallsklasse som
    `_spoletekst` — spolefeil er drift, aldri modellens."""
    try:
        return [_les_spole(bit[2]) for bit in medlemmer]
    except OSError as feil:
        raise Kjoringsfeil("infrastrukturfeil", fremdrift) from feil


def _les_spole(sti):
    """Spolefila lest UTEN linjeskiftoversettelse — se `_spoletekst`.

    Egen funksjon fordi `Path.read_text` først tar `newline` i 3.13, og
    `with` hører hjemme i en setning, ikke i et generatoruttrykk der
    lukkingen ville hvilt på refcounting."""
    with sti.open(encoding="utf-8", newline="") as fil:
        return fil.read()


def kjor_bunt(sti, modell, *, vekter, tekst_for, biasmaalinger,
              antall_soknader, kandidatfelter_for=None,
              blinding_av=False, avskruing_hendelse_id=None,
              hendelseoppslag=None,
              lagre_dokument=None, lagre_kandidat=None):
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

    STRØMMINGEN (#173, eiers valg b + i): `lagre_dokument(kandidat_id,
    medlemsnavn, data, tekst)` kalles per medlem UNDER lesingen, og
    `lagre_kandidat(kandidat_id, resultat)` rett etter hver evaluering —
    kandidatlagrene (057) fylles underveis, aldri i en sluttbatch.
    SP-3 står: ingenting er PROMOTERT før hele kjøringen lyktes —
    atomisiteten bor i promoteringsvakten (056), ikke i minnet. En
    sink-feil er et kodet utfall (`kandidatlagring_feilet`), aldri en
    rå exception. Sinkene er valgfrie for testbarhet; produksjonveien
    (controlleren) gir alltid begge.

    UTTREKKSSIDEN AV MINNET ER BUNDET: tekstene spoles til disk i
    arbeiderens egen tempkatalog og leses tilbake én kandidat om gangen
    (to pass — porten for hele bunten først, så evalueringen).
    Toppunktet på tekstsiden er den STØRSTE kandidaten, ikke bunten.
    RETURENS `artefakter` bærer fortsatt hver kandidats resultat
    (rapport-v1-skjemaet er registrert og immutabelt til #168s v2), så
    full minnebinding lander først med v2 — målt og meldt i #173.

    KJENT BEGRENSNING — INTET INTERNT TAK, VERKEN PÅ TID ELLER
    AUTORITET. Løkka under tar verken en `frist_s` eller et
    avbruddssignal: den evaluerer hver kandidat til bunten er tom.
    Kjøringens varighet bindes ved LEVERING — kalleren måler vinduet
    FØR bunten hentes, og leveringsportene (`lease_tapt` før
    opplasting, kvitteringens statusskifte etter) stopper et resultat
    som ble ferdig for sent eller uten lease. Avbruddssignalet er sin
    egen returkontraktsendring og ble IKKE smuglet inn i #173s
    strømming; se KONTRAKT.md, `dom-klasse: kjoring-avbrudd-og-frist`.

    `sti` MÅ VÆRE INSTANSBUNDET NÅR DEN ER DELBAR — det er kallerens
    ansvar (Codex P1, eierdom K2-kjennelse runde 7 på #217, valg B i
    inode-form). Stien åpnes flere uavhengige ganger: `les_manifest`
    henter DEKLARASJONEN (blindingens kilde), `les_porsjonsvis` henter
    INNHOLDET. Byttes fila i vinduet mellom dem med et
    TOPOLOGI-BEVARENDE bytte — samme medlemsnavn, samme antall —
    blindes arkiv A-s deklarasjon inn i arkiv B-s dokument: A-s
    verdier maskeres og TREFFER (så vakuøsitetsporten tier), en
    personverdi som bare står i B er ikke deklarert (så port 16 har
    ingenting å lete etter), og kjøringen fullfører som blindet med
    klartekst hos modellen. De eksisterende portene tar bare det
    topologi-ENDRENDE byttet. Kalleren holder derfor bunten åpen og gir
    en instansbundet sti — `/proc/self/fd/<fd>` — når stien kan deles
    med andre skrivere; da går alle åpningene gjennom samme inode, og
    byttet kan per konstruksjon ikke nå kjøringen. Kontrolleren er
    eneste produksjonskaller og eier fila den selv skrev. En kaller som
    gir en delbar filsti bærer klassen selv; se KONTRAKT.md,
    `dom-klasse: arkivinstans-toctou`.
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
    # SPOLEN (#173): teksten skrives til disk i det den er trukket ut,
    # og biten bærer STIEN — ikke innholdet. Katalogen er arbeiderens
    # egen (samme tillitssone som bunten selv, som alt ligger utpakket
    # der) og dør med kjøringen.
    biter: dict[str, list[tuple[str, str, Path, object]]] = {}
    lest = 0
    # SPOLEN OPPRETTES I DEN KODEDE VEIEN (Codex P2). Linjen sto UTENFOR
    # `try`-en under, og `TemporaryDirectory` reiser `OSError` når
    # arbeiderens midlertidige filsystem er utilgjengelig, fullt eller
    # nektet. Catch-allen som gjør feil om til `Kjoringsfeil` kjørte
    # derfor aldri, og `kjor_en` fanger bare `Kjoringsfeil`/skjemafeil:
    # arbeideren døde uten den KODEDE feilkvitteringen alle andre
    # spole-I/O-feil sender (`_spoletekst`, strømløkkens `except
    # OSError`). Samme klasse som lesefeilene forrige runde flyttet inn i
    # gaten — og samme kode, for kilden er den samme disken.
    try:
        spole = tempfile.TemporaryDirectory(prefix="m57-spole-")
    except OSError as feil:
        raise Kjoringsfeil("infrastrukturfeil", fremdrift) from feil
    spolerot = Path(spole.name)
    #: Kom kroppen helt igjennom? Leses av `finally` under, og er en
    #: EKSPLISITT flagg og ikke `sys.exc_info()`: den siste svarer på
    #: «håndteres det et unntak NÅ», og et kall fra en ytre `except`-arm
    #: ville gjort et rent gjennomløp umulig å skille fra et feilet.
    kropp_fullfort = False
    try:
        # LAGRINGSHÅNDTEREREN HØRER TIL LESINGEN, IKKE HELE KJØRINGEN
        # (Codex P2). Denne indre `try`-en dekker BARE arkivgaten. Sto
        # håndtereren nede blant de øvrige, dekket den også `modell.vurder`,
        # og en `ConnectionResetError` fra modellklienten — en `OSError` MED
        # errno som alle andre — ble meldt som `infrastrukturfeil`. Forrige
        # runde flyttet lagringsfeilen ut av modellkøen; uten denne
        # innsnevringen tok den modellens egne nettverksfeil med seg samme
        # vei, og da leter driften etter et lagringsavbrudd som aldri fant
        # sted. Kilden avgjør koden, og kilden er hvor unntaket oppsto.
        #
        # …OG DEKLARASJONEN ER NÅ DEL AV ARKIVGATEN (Cursor P1, runde 4).
        # #161 la arkivlesing FORAN strømmen — `inspiser_bunt` og
        # `les_manifest` åpner begge bunten på lageret — men lot de to
        # linjene stå UTENFOR denne `try`-en. `les_manifest` slipper med
        # vilje en `OSError` MED errno rått ut, av nøyaktig samme grunn
        # som `les_porsjonsvis` gjør det: en lesefeil på disk eller
        # nettlager er DRIFT, ikke buntens skyld. Utenfor gaten hadde den
        # ingen håndterer å lande i, og catch-allen nederst meldte den
        # som `modellfeil` — feil kø og feil alarm for en bunt modellen
        # aldri fikk se, altså samme klasse som allerede er lukket for
        # strømmen. Gaten flyttes ikke og oversettelsen dupliseres ikke:
        # lesingen flyttes inn i håndtereren som alt eier den.
        try:
            # #161 (eiers B): kandidatene DEKLARERES av buntens eget
            # `soknader.json` og bindes toveis mot katalogen FØR én byte
            # innhold pakkes ut — «den så ut som en søknad» er ikke en
            # inspeksjon. Deklarert kandidattall måles mot oppdragets
            # signerte tall her, foran strømmen: et avvik er en ugyldig
            # bunt, aldri et resultat.
            manifestet = parsing.les_manifest(
                sti, parsing.inspiser_bunt(sti))
            kart = manifestet.kart
            if len(set(kart.values())) != antall_soknader:
                raise Kjoringsfeil("kandidattall_avvik", fremdrift)
            if kandidatfelter_for is None:
                # Blindingens kilde er DEKLARASJONEN (#158s strukturelle
                # retning): manifestets `felter` per kandidat. En
                # kandidat uten deklarerte felter blindes ikke — og
                # felles da av fail-closed-porten som
                # `blinding_uten_felter`, aldri av et fritekst-søk etter
                # personalia.
                #
                # …MEN UTFALLET ER KJENT HER, OG FELLES DERFOR HER (Codex
                # P2, eierdom 26/8 pkt. 2). Porten er den samme
                # fail-closed-dommen, bare målt på det tidspunktet den
                # faktisk er avgjort: `manifestet.felter` er lest, så en
                # deklarert kandidat uten `felter` KAN ikke ende noe
                # annet sted enn `blinding_uten_felter`. Sto målingen
                # igjen nede i `evaluer_kandidat`, betalte bunten først
                # hele uttrekket — hvert medlem pakket ut og beholdt i
                # `biter` — og fordi kandidatene evalueres `sorted`,
                # kunne TIDLIGERE kandidater ha vært hos modellen før
                # utfallet ble reist for en senere. En stor bunt kunne
                # dessuten treffe minnegrensen først og komme ut med feil
                # kode. Dette er ikke en ny grense: det er samme økonomi
                # som kandidattallporten over — det som er avgjort før
                # strømmen, felles før strømmen.
                #
                # Betingelsen speiler `evalueringsinput` NØYAKTIG: er
                # blindingen avskrudd (`blinding_av`), finnes det ingen
                # `blinding_uten_felter` nede i veien heller, og en
                # kandidat uten deklarerte felter er da lovlig. Porten
                # skal flytte utfallet, aldri utvide det.
                if not blinding_av:
                    for kid in sorted(set(kart.values())):
                        if not manifestet.felter.get(kid):
                            raise Kjoringsfeil("blinding_uten_felter",
                                               fremdrift)

                def kandidatfelter_for(medlem):
                    return manifestet.felter.get(
                        kart.get(medlem.navn, ""), {})
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
                tekst = _tekst(tekst_for, medlem, data, fremdrift)
                felter = _felter(kandidatfelter_for, medlem, fremdrift)
                # STRØMMEN UT (#173): dokumentet og teksten går til
                # lageret i det de er målt — feiler lagringen, feiler
                # kjøringen kodet, før flere medlemmer pakkes ut.
                if lagre_dokument is not None:
                    # ÉN kode for enhver sinkfeil (CodeRabbit): sinken
                    # er fremmed kode, og en pass-through-arm for dens
                    # egne Kjoringsfeil var en dør ingen bruker — alt
                    # den kunne gjort var å forkle en lagringsfeil som
                    # noe annet.
                    try:
                        lagre_dokument(kandidat_id, medlem.navn, data,
                                       tekst)
                    except Exception as feil:   # noqa: BLE001 — kodet
                        raise Kjoringsfeil("kandidatlagring_feilet",
                                           fremdrift) from feil
                spolesti = spolerot / f"{lest}.txt"
                # `newline=""` på BEGGE sider (Codex P2, #173): lesningen
                # er der oversettelsen faktisk beit, men uten den her er
                # rundturen bare byte-eksakt fordi `os.linesep` tilfeldigvis
                # er `\n` på Linux. Spolen skal bære uttrekkerens streng
                # uendret av konstruksjon, ikke av plattformflaks.
                with spolesti.open("w", encoding="utf-8",
                                   newline="") as fil:
                    fil.write(tekst)
                biter.setdefault(kandidat_id, []).append(
                    (navn, medlem.navn, spolesti, felter))
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
        # `tom_bunt` ER FJERNET — EIERDOM (K2-kjennelse på #216, valg B).
        # Tre uavhengige vakter leste samme tilstand etter strømløkken, så
        # det var REKKEFØLGEN, ikke tilstedeværelsen, som avgjorde hvilket
        # ord en divergens fikk. Forsvant ALLE deklarerte medlemmer mellom
        # bindingen og `les_porsjonsvis`, ble `biter` tom, og `if not biter`
        # stjal utfallet fra vakten under: en bunt som DEKLARERTE
        # kandidater ble meldt «tom» i stedet for `manifest_medlem_mangler`.
        # Vakten var dessuten alt død for sitt opprinnelige formål — #161
        # feller tom zip og bare-kataloger som `manifest_mangler` FØR
        # strømmen — og en vakt som per konstruksjon aldri kan fyre riktig
        # er ikke et vern, det er støy. Dommen fjerner overlappet i stedet
        # for å stokke det: manifestporten er frontdøren mot «aldri et
        # vellykket tomt utfall», `lest != len(kart)` eier divergensen, og
        # `len(biter) != antall_soknader` står som eneste defense bak den.
        #
        # TOVEIS MIDT I FLUKT VAR BARE ÉN VEI (Cursor P2). Oppslaget over
        # feller et medlem strømmen har og kartet mangler; den OMVENDTE
        # divergensen — kartet deklarerer filer strømmen aldri yielder —
        # hadde ingen måling i det hele tatt. Mister arkivet et deklarert
        # medlem i vinduet mellom bindingen og `les_porsjonsvis`, mens
        # hver kandidat beholder minst én fil, treffer `len(biter)`
        # fortsatt `antall_soknader`: kjøringen LYKKES, og en kandidat
        # evalueres på et halvt dokumentsett uten at noen sa fra.
        #
        # `les_porsjonsvis` yielder nøyaktig innholdsmedlemmene
        # (manifestet er filtrert bort der), så `lest` og `len(kart)` er
        # samme tall i en hel bunt. Koden er `les_manifest`s egen for
        # nettopp denne retningen — deklarert navn uten medlem — så de to
        # veiene beholder hvert sitt ord uansett hvor divergensen dukker
        # opp: `medlem_uadressert` den ene veien, `manifest_medlem_mangler`
        # den andre.
        if lest != len(kart):
            raise Kjoringsfeil("manifest_medlem_mangler", fremdrift)
        # Sluttporten står som DEFENSE (mekanismen er nå manifestets
        # toveisbinding + tallporten foran strømmen): faller de, skal
        # dette aldri passere stille.
        if len(biter) != antall_soknader:
            raise Kjoringsfeil("kandidattall_avvik", fremdrift)
        # TO PASS: BLINDINGPORTEN FOR HELE BUNTEN FØR NOEN KANDIDAT NÅR
        # MODELLEN (Cursor P2). Dette er samme REKKEFØLGEPRIS som porten
        # foran strømmen over, bare ett hakk senere i veien: den porten
        # feller det som er avgjort av DEKLARASJONEN alene (`felter`
        # mangler), mens vakuøsiteten — en deklarasjon som ikke traff
        # dokumentet, `ugyldig_maskeringsform` — krever teksten og er
        # derfor først avgjort her, når `biter` er komplett.
        #
        # Sto blindingen igjen inne i evalueringsløkka, betalte bunten
        # prisen kandidatene evalueres `sorted` for: med `k1` gyldig og
        # `k2` vakuøs var `k1` ALT hos modellen når `k2` felte kjøringen.
        # NBSP/NFD-deklarasjoner passerer `les_manifest`/
        # `feltverdier_lukket` med vilje (eierdom, K2-kjennelse runde 5,
        # valg B), så de er lovlige helt hit — nettopp derfor er det her
        # de må måles, og for ALLE, før det første modellkallet.
        #
        # Ingen ny maskin: porten er `blinding.evalueringsinput`, uendret
        # og fortsatt den ENESTE veien til modellinput. Den kalles to
        # ganger per kandidat — her som port, og inne i
        # `evaluer_kandidat` som input — og det er et bevisst valg
        # fremfor å cache resultatet gjennom en ny parameter: en
        # `modellinput=`-dør inn i `evaluer_kandidat` ville åpnet en vei
        # utenom `blinding_uten_felter`, eller tvunget den fail-closed-
        # regelen ut i en ANDRE opptelling — nøyaktig divergensen
        # K2-kjennelsen runde 4 lukket («ETT predikat, to kallesteder»).
        # `blind` er en ren funksjon av `(tekst, kandidatfelter)`, så de
        # to kallene er per konstruksjon samme verdi; prisen er
        # regex-arbeid som uansett forsvinner i modellkallet ved siden av.
        #
        # ... MEN OPPSLAGET ER IKKE REGEXARBEID (Codex P2, runde 2 på
        # #247). `krev_avskruingshendelse` slår opp i BASEN, og med to
        # kallesteder per kandidat ble én uforanderlig autorisasjon til
        # 2 × 5 000 spørringer på den støttede buntgrensen. Verre enn
        # prisen er REKKEFØLGEN: en forbigående oppslagsfeil i andre
        # løkke feller kjøringen først etter at tidligere kandidater alt
        # har nådd modellen — nøyaktig den halvveis-eksponeringen
        # topassformen over finnes for å hindre.
        #
        # Hendelsen slås derfor opp ÉN gang for bunten, her, før første
        # kandidat: en oppslagsfeil er avgjort før noen tekst er sendt.
        # Predikatet er uendret og står fortsatt på begge kallestedene
        # («ETT predikat, to kallesteder») — det er kilden som er
        # memoisert, ikke porten. Et annet id enn buntens gir `None`, som
        # porten avviser som «ingen rad»: cachen er fail-closed, ikke en
        # snarvei forbi oppslaget.
        if blinding_av:
            hendelsen = blinding.krev_avskruingshendelse(
                avskruing_hendelse_id, hendelseoppslag)

            def hendelseoppslag(hendelse_id, _hendelse=hendelsen):
                return (_hendelse
                        if hendelse_id == avskruing_hendelse_id else None)
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
            # DOKUMENTENE HOLDES FRA HVERANDRE TIL ETTER BLINDINGEN
            # (#174). Skjøtingen skjedde FØR blindingen, og da kunne et
            # modellsitat krysse skjøten: `valider_funn` godtok et utdrag
            # som ikke står i noe faktisk søknadsdokument (Codex G7 på
            # #170). Blindingen endrer lengder, så råtekstens skjøter kan
            # ikke bæres inn i det blindede koordinatsystemet i det hele
            # tatt — grensene MÅ oppstå på den blindede siden.
            # `evaluer_kandidat` skjøter dem selv, med `blinding.SKJOT`,
            # og regner grensene av den samme. Lest fra SPOLEN (#173):
            # bitene bærer stier, ikke tekst.
            dokumenter = _spoledokumenter(medlemmer, fremdrift)
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
            #
            # MÅLT UTEN Å BYGGE TEKSTEN (Codex P2). Sjekken sto på en
            # skjøtet kopi som ikke ble brukt til noe annet: parseren
            # slipper gjennom inntil 2 GB utpakket, `dokumenter` og
            # medlemsstrengene blir stående, og `blind_dokumenter` lager
            # sin EGEN skjøtede kopi rett etterpå. Den ene ekstra
            # kandidatstore allokeringen kunne ta livet av arbeideren for
            # bunter som før gikk inn — før noe modellkall. `any` leser
            # dokument for dokument og stanser på det første som har
            # innhold.
            if not any(d.strip() for d in dokumenter):
                raise Kjoringsfeil("tekstuttrekk_feilet", fremdrift)
            blinding.evalueringsinput_dokumenter(
                dokumenter, kandidatfelter,
                blinding_av=blinding_av,
                avskruing_hendelse_id=avskruing_hendelse_id,
                hendelseoppslag=hendelseoppslag)
        # ANDRE PASS leser spolen på nytt, én kandidat om gangen — ikke
        # en `klargjort`-dict med hele buntens tekst (#173): portpasset
        # over har alt garantert at ALLE kandidater blindes gyldig før
        # den første når modellen, og spolen gir samme byte begge
        # ganger. Evalueringen går i samme deterministiske orden —
        # og fortsatt per DOKUMENT (#174): skjøten legges først på den
        # blindede siden, i `evaluer_kandidat`.
        for kandidat_id in sorted(biter):
            medlemmer = sorted(biter[kandidat_id], key=lambda bit: bit[:2])
            dokumenter = _spoledokumenter(medlemmer, fremdrift)
            kandidatfelter = {}
            for *_, nye in medlemmer:
                _flett_felter(kandidatfelter, nye)
            resultat = evaluering.evaluer_kandidat(
                modell, dokumenter, kandidatfelter, vekter,
                biasmaalinger=biasmaalinger,
                blinding_av=blinding_av,
                avskruing_hendelse_id=avskruing_hendelse_id,
                hendelseoppslag=hendelseoppslag)
            # STRØMMEN UT, KANDIDATSIDEN (#173): artefaktet går til
            # lageret i det det er evaluert — samme kodede utfall som
            # dokumentsiden.
            if lagre_kandidat is not None:
                try:
                    lagre_kandidat(kandidat_id, resultat)
                except Exception as feil:       # noqa: BLE001 — kodet
                    raise Kjoringsfeil("kandidatlagring_feilet",
                                       fremdrift) from feil
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
        kropp_fullfort = True
    except Kjoringsfeil:
        # Alt som alt ER utfallet, går videre som seg selv: uten denne
        # linjen ville catch-allen under pakket det inn på nytt som
        # «modellfeil» og skjult koden det ble reist med.
        raise
    except parsing.Buntfeil as feil:
        raise Kjoringsfeil(feil.kode, fremdrift) from feil
    except uttrekk.Uttrekksfeil as feil:
        # Uttrekket er FILENS/KONFIGURASJONENS feil, aldri modellens —
        # samme misattribusjonsklasse som lagring/dekompresjon. Denne
        # grenen sto DØD til Cursor P2 runde 6: `_tekst` er eneste vei
        # fra `uttrekk` inn hit, og dens catch-all spiste koden før den
        # kom så langt. Den er oversettelsens ENE sted igjen.
        raise Kjoringsfeil(feil.kode, fremdrift) from feil
    except modellklient.Modellfeil as feil:
        # Transport mot modellserveren er DRIFT med egen kode — «modellen
        # svarte galt» og «modellen var nede» er to ulike ord.
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
    finally:
        # OPPRYDDINGEN SKAL IKKE OVERSKRIVE UTFALLET (Codex P2).
        # `cleanup()` reiser når det midlertidige filsystemet blir
        # utilgjengelig eller svarer EIO/EPERM, og den reiser fra
        # `finally` — altså ETTER at all oversettelse over er ferdig. En
        # rå `OSError` derfra erstattet både et vellykket resultat og en
        # alt kodet `Kjoringsfeil`, og `kjor_en` fanger den ikke:
        # arbeideren døde uten feilkvittering, med opprydding som
        # dødsårsak i stedet for det som faktisk skjedde.
        #
        # Feilen får derfor ordet BARE når det ikke alt finnes et utfall
        # å melde, og da som `infrastrukturfeil` — samme kode og samme
        # kilde som spolens øvrige I/O. Feilet kroppen, er dens kode den
        # sanne, og oppryddingsfeilen forlates i stillhet: en spole som
        # ikke lot seg slette er en katalog i `TMPDIR`, ikke en grunn
        # til å bytte ut diagnosen driften skal handle på.
        try:
            spole.cleanup()
        except OSError as feil:
            if kropp_fullfort:
                raise Kjoringsfeil("infrastrukturfeil", fremdrift) from feil
    return {"rangering": rangering,
            "artefakter": artefakter, "fremdrift": fremdrift}
