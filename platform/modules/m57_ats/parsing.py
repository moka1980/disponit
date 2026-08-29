"""Arkivgaten (klarsignalet §4) — håndhevet FØR utpakking, målt igjen
under lesing.

Grensene er tallfestet i klarsignalet og PINNET her; en test som vil
endre dem må endre denne fila, ikke en konfigurasjon. Deklarerte
størrelser i zip-katalogen er PÅSTANDER — `les_porsjonsvis` måler de
faktiske bytene under strømming og feller avvik der, så en løgnaktig
header ikke blir en vei rundt gaten.

Selve tekstuttrekket fra PDF/DOCX skjer i den credential-frie,
nettverksløse containeren (§7, port 24-formen fra 014b); denne fila er
plattform-sidens dør inn og slipper bare gjennom det gaten har målt.
"""
from __future__ import annotations

import contextlib
import io
import json
import lzma
import re
import zipfile
import zlib
from dataclasses import dataclass

from . import blinding
from pathlib import Path

#: §4, ordrett. Heltall i byte der det gjelder byte.
MAKS_TOTAL_UTPAKKET = 2 * 1024 * 1024 * 1024   # 2 GB
MAKS_KOMPRIMERINGSFORHOLD = 100                # per fil, 100:1
MAKS_FILER = 20_000
MAKS_ENKELTFIL = 25 * 1024 * 1024              # 25 MB
# Nøstede arkiver: 0 (ikke tillatt). DOCX er teknisk en zip og er
# UNNTAKET — den er en av de tre lovede innholdstypene, og magien
# `PK` kreves der (innholdstypeporten under), mens alt annet med
# arkivmagi eller arkivendelse felles. Unntaket gjelder TYPEN, ikke
# grensene: gaten (`_mal_medlem`) bruker seg selv på det indre
# arkivet, mot BUNTENS budsjett — samme kode, samme teller, aldri et
# friskt sett per docx.
#: OOXML-pakkens OBLIGATORISKE deler. En docx er ikke «en zip som heter
#: .docx» — den er en OPC-pakke, og uten disse to finnes det ikke noe
#: dokument å trekke tekst ut av (Codex P2). Uten kravet passerte enhver
#: lesbar zip med riktig endelse innholdstypeporten og feilet først som
#: en rå uttrekksfeil nede i containeren, i stedet for som portens egen
#: `feil_innholdstype`.
DOCX_PAKKEMEDLEMMER = frozenset({"[Content_Types].xml",
                                 "word/document.xml"})
ARKIVENDELSER = frozenset({
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar"})
TILLATTE_ENDELSER = frozenset({".pdf", ".docx", ".html", ".htm"})

#: #161 (eiers B): bunten BÆRER sin egen deklarasjon — et lukket
#: `soknader.json` i roten navngir hver kandidat og filene hens.
#: Grensene er klarsignalets §4: 1–5000 kandidater.
MANIFESTNAVN = "soknader.json"
MAKS_KANDIDATER = 5000
MAKS_MANIFESTBYTES = 4 * 1024 * 1024
#: KANDIDAT-ID-ENS LUKKEDE ASCII-KANON (eierdom, K2-kjennelsen på #216 —
#: valg A). Porten telte før opp TEGNKLASSER å avvise, én runde per
#: klasse: blanktegn (runde 3), Cf/ZWSP (runde 5), og etter dem sto
#: RTL-markører, NFKC-ekvivalenter og homoglyfer (`а` U+0430 mot `a`) i
#: kø. En LUKKET grammatikk dreper hele «to ID-er som ser like ut for et
#: menneske»-klassen i én dom — og den unngår håndrullede regler over en
#: fremmed grammatikk (Unicode), som er K4s nabolag. Formen er
#: KONTRAKT, ikke en fiksdetalj: den står i `kontrakt/KONTRAKT.md`.
#: Ikke-tom, maks 64 tegn, starter alfanumerisk; alt annet er
#: `manifest_feilformet`. `fullmatch` gjør at `$`-ens nylinjesmutthull
#: aldri oppstår.
KANDIDAT_ID_KANON = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")

#: Magi per endelse: deklarasjonen og innholdet må være SAMME påstand
#: («feil innholdstype»-porten).
_MAGI = {".pdf": (b"%PDF",), ".docx": (b"PK\x03\x04",)}
#: Alle tre zip-signaturene, ikke bare den lokale filhodet (Codex P2): et
#: TOMT arkiv begynner med `PK\x05\x06` og et spennet med `PK\x07\x08`, og
#: en denyliste som ikke kjenner dem er en denyliste med hull.
_ARKIVMAGI = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", b"\x1f\x8b",
              b"7z\xbc\xaf", b"Rar!", b"BZh", b"\xfd7zXZ")
#: Hodet innholdstypeporten måler. Magien bor i de første få bytene, men
#: HTML har ingen magi — bare en FORM, og formen tåler lovlige
#: innledende blanktegn (Codex P2). Et hode på åtte byte inneholdt ingen
#: `<` for et dokument som begynte med en BOM og et par linjeskift, så
#: fullt gyldig HTML ble avvist som feil innholdstype. Grensa er
#: BUNDET: porten leser et hode, aldri filen.
_HODEBYTE = 512


class Buntfeil(Exception):
    """Én kode per grense — utfallet er data, ikke prosa (SP-3-formen)."""

    def __init__(self, kode: str, detalj: str = ""):
        self.kode = kode
        super().__init__(f"{kode}: {detalj}" if detalj else kode)


@dataclass(frozen=True)
class Medlem:
    navn: str
    storrelse: int


def _sjekk_navn(navn: str, *, kontekst: str | None = None) -> None:
    """Måler NAVNET arkivet oppgir — aldri en sammensatt visningsstreng.

    `kontekst` er detaljen i feilen (hvilken ytre fil medlemmet satt i),
    ikke det som vurderes. Skillet er funnet (Codex P1): det indre
    DOCX-medlemmet ble validert som `f"{ytre}/{info.filename}"`, og et
    absolutt medlemsnavn ble da til `cv.docx//tmp/escape.xml` eller
    `cv.docx/C:/escape.xml` — hverken «starter med /» eller «kolon i
    posisjon 1» traff lenger, så nøyaktig den stien porten finnes for å
    avvise, slapp gjennom gaten før utpakking.
    """
    ren = navn.replace("\\", "/")
    if ren.startswith("/") or (len(ren) > 1 and ren[1] == ":"):
        raise Buntfeil("sti_utenfor_bunten", kontekst or navn)
    if any(del_ == ".." for del_ in ren.split("/")):
        raise Buntfeil("sti_utenfor_bunten", kontekst or navn)


def _endelse(navn: str) -> str:
    return Path(navn.replace("\\", "/")).suffix.lower()


def _ser_ut_som_html(hode: bytes) -> bool:
    """HTML har ingen magi, men den har en FORM: første ikke-blanke tegn
    (etter en eventuell BOM) er `<`. Positiv gjenkjenning, ikke en
    denyliste over signaturene noen kom på — den slapp gjennom `%PDF`.

    En tom fil er ikke HTML: den har ingen form å måle, og en tom søknad
    er ikke en søknad."""
    for bom in (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff"):
        if hode.startswith(bom):
            hode = hode[len(bom):]
            break
    return hode.lstrip(b" \t\r\n\x00").startswith(b"<")


@contextlib.contextmanager
def _kodet_lesefeil(navn: str, *, mangler: str):
    """UTTREKKETS bibliotekformer, oversatt til kodede utfall ÉTT sted.

    `_apne_katalog` dekker åpningen av sentralkatalogen; dette dekker
    lesingen av et medlem. De to lesestedene — innholdsstrømmen og
    `les_manifest` — hadde hver sin kopi av nøyaktig denne kjeden, og
    det er samme rot som #155 river ut på gate-siden: to
    implementasjoner av samme oversettelse divergerer med nødvendighet.
    Bare KeyError-armen skiller dem, og den er derfor et argument
    (`mangler`) — en deklarasjon vi ikke finner er `manifest_mangler`,
    et deklarert medlem vi ikke finner er `manifest_medlem_mangler`.

    Kontrakten er et KODET utfall (SP-3), aldri en rå bibliotekfeil:
    ødelagte headere og CRC-avvik kommer som `BadZipFile`, men en SKADET
    komprimert strøm feller dekompressoren FØR CRC-en måles, og da i det
    formatets egen form — `zlib.error` for DEFLATE, `lzma.LZMAError` for
    LZMA, og en errno-løs `OSError` for BZIP2. `OSError` MED errno er
    noe helt annet: en lesefeil på disk eller nettlager er DRIFT, ikke
    buntens skyld, og å kalle den `korrupt_bunt` ville gjort vår feil til
    en kundeavvisning. Passord gir `RuntimeError` og en manglende
    komprimeringsmodul `NotImplementedError` — begge er bunter vi ikke
    kan lese, ikke bunter som er ødelagte.
    """
    try:
        yield
    except KeyError as feil:
        raise Buntfeil(mangler, navn) from feil
    except zipfile.BadZipFile as feil:
        raise Buntfeil("korrupt_bunt", f"{navn}: {feil}") from feil
    except (zlib.error, lzma.LZMAError) as feil:
        raise Buntfeil("korrupt_bunt",
                       f"{navn}: {type(feil).__name__}") from feil
    except OSError as feil:
        if feil.errno is not None:
            raise
        raise Buntfeil("korrupt_bunt",
                       f"{navn}: {type(feil).__name__}") from feil
    except (RuntimeError, NotImplementedError) as feil:
        raise Buntfeil("uleselig_medlem",
                       f"{navn}: {type(feil).__name__}") from feil


def _apne_katalog(sti: str | Path) -> zipfile.ZipFile:
    """DEN YTRE SENTRALKATALOGEN HAR ÉN DØR, OG DEN ER KODET (Codex P2).

    `is_zipfile` leter opp EOCD-posten — den leser IKKE katalogen. En
    bunt med gyldig hale og en ødelagt oppføring lenger inne passerer
    derfor `ikke_zip`-porten, og `ZipFile(...)` feller først når den
    faktisk parser posten. Begge de ytre åpningsstedene sto UTENFOR all
    håndtering (`inspiser_bunt` har ingen, og `les_porsjonsvis` fanger
    først inne i medlemssløyfa), så en angriperlevert bunt ble en uventet
    arbeiderfeil i stedet for det kodede utfallet kontrakten lover
    (SP-3). Den INDRE katalogen har hatt nettopp denne døren siden
    docx-runden (`_mal_docx`); den ytre hadde den ikke.

    Kodene er de samme som medlemssløyfa bruker, fordi det er de samme
    bibliotekformene: bytene i katalogen er ødelagte (`BadZipFile`, og
    et filnavn som PÅSTÅR UTF-8 uten å være det) er en KORRUPT bunt,
    mens et arkiv biblioteket ikke har midler til å lese
    (`NotImplementedError` på en zip-versjon den ikke kan, `RuntimeError`)
    er ULESELIG. Døren AVVISER; den inspiserer ikke — grensene måles som
    før av kallerne.
    """
    try:
        return zipfile.ZipFile(sti)
    except zipfile.BadZipFile as feil:
        raise Buntfeil("korrupt_bunt", str(feil)) from feil
    except UnicodeDecodeError as feil:
        raise Buntfeil("korrupt_bunt",
                       f"katalognavn: {feil.reason}") from feil
    except (RuntimeError, NotImplementedError) as feil:
        raise Buntfeil("uleselig_medlem",
                       f"{type(feil).__name__}: {feil}") from feil


def inspiser_bunt(sti: str | Path) -> list[Medlem]:
    """Hele gaten mot KATALOGEN, før én byte pakkes ut.

    Rekkefølgen er bevisst: stier og lenker (angrep) før typer og
    størrelser (grenser), og totalsummen løpende — en bunt som bryter
    2 GB på fil nr. 3 avvises der, ikke etter 20 000 headere.

    `MAKS_FILER` måles på ALLE katalogoppføringer, mappene inkludert
    (Codex P2). Grensen bevokter arbeidet katalogen påfører oss — minne
    og parsetid i `infolist()` — og det arbeidet er gjort før vi rekker
    å filtrere. Ble mappene filtrert bort FØR målingen, slapp en bunt
    med 20 001 tomme mapper og én HTML-søknad gjennom nettopp det
    budsjettet grensen finnes for å holde.

    LUKKET av #161 (eiers B): søknadsantallet bindes IKKE her — gaten
    kjenner MEDLEMMER, ikke søkere, og en kandidatform kan ikke gjettes
    ut av en katalog (SP-13/K4). Den DEKLARERES: `les_manifest` binder
    buntens eget `soknader.json` toveis mot katalogen, og
    utførelsesarmen måler deklarert kandidattall mot oppdragets signerte
    tall FØR strømmen.

    UTSATT, K1 → #162 — `MAKS_FILER` måles ETTER at `infolist()` har
    materialisert hver eneste `ZipInfo` (Codex P2). En kompakt zip med
    ~100 000 oppføringer koster derfor både parsetid og et objekt per
    oppføring FØR grensen rekker å avvise den, altså nøyaktig det
    arbeidet budsjettet finnes for å holde. Å måle antallet først krever
    at noen leser sentralkatalogens EOCD-post (og ZIP64-lokatoren) — en
    bit av zip-grammatikken lest for hånd, som er SP-13/K4 rett i
    ansiktet, eller en bundet katalogleser, som er en maskin. Den ekte
    lukkingen er BUNDET INNDATA: bunten har i dag ingen deklarert fysisk
    størrelse fordi den ikke har noen vei inn (samme rot som
    `soknadsbunt_ref`, #162), og med en bundet vei inn er arbeidet her
    bundet av et tall noen har signert. Ny maskin: #162, ikke en
    fiksrunde.

    Eiers avgjørelse: **B — bunten bærer et manifest.** Et lukket
    `soknader.json` i buntens rot navngir hver kandidat og filene hens;
    en ekte parser slår manifestet opp mot de faktiske medlemmene begge
    veier (uadressert medlem og manglende medlem er like rødt), og
    `len(kandidater)` må være == oppdragets `antall_soknader` og ≤ 5000
    FØR innholdet parses — ekstern-lesing-doktrinen, samme grunn som
    resten av denne gaten kjører før utpakking. `kjoring.py` (som ikke
    finnes ennå — `les_porsjonsvis` har ingen kaller utenfor test) bytter
    kandidat-utledning til manifestet i samme PR som snittet fra
    utførelsesarmen. Ny maskin: eget issue (#161) + egen PR.
    """
    if not zipfile.is_zipfile(sti):
        raise Buntfeil("ikke_zip")
    medlemmer: list[Medlem] = []
    total = 0
    sett: set[str] = set()
    with _apne_katalog(sti) as zf:
        alle = zf.infolist()
        if len(alle) > MAKS_FILER:
            raise Buntfeil("for_mange_filer", str(len(alle)))
        # STIPORTEN MÅLER KATALOGEN, IKKE UTVALGET (Codex P2). Mappene ble
        # filtrert bort FØR `_sjekk_navn`, så `../../unnslapp/` sto i
        # katalogen til en bunt både gaten og strømmen godtok — porten som
        # finnes for å avvise stier ut av bunten, så aldri oppføringen.
        # En mappeoppføring er en oppføring: at VI ikke pakker den ut, er
        # vår lesning, ikke en egenskap ved arkivet. Navn og filtype måles
        # derfor på hver eneste oppføring; resten av grensene gjelder
        # medlemmene som faktisk bærer innhold.
        for info in alle:
            _sjekk_navn(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise Buntfeil("symlenke", info.filename)
        infos = [i for i in alle if not i.is_dir()]
        for info in infos:
            # En zip KAN bære to oppføringer med samme navn, og
            # `zf.open(navn)` slår opp i navnekartet — som bare husker den
            # SISTE. To ulike `cv.html` ville da blitt lest som den samme,
            # to ganger, og den første søknaden forsvunnet i stillhet
            # (Codex P2). Hvilke søknader som evalueres er ikke et sted
            # for stillhet: bunten avvises.
            if info.filename in sett:
                raise Buntfeil("duplikat_medlem", info.filename)
            sett.add(info.filename)
            if info.filename == MANIFESTNAVN:
                # Manifestet er DEKLARASJONEN, ikke en søknad: det er den
                # ene lovlige .json-en, bor i roten, og størrelses-
                # begrenses her — resten av gaten (endelse/magi) gjelder
                # søknadsinnhold og skal ikke se det.
                if info.file_size > MAKS_MANIFESTBYTES:
                    raise Buntfeil("manifest_feilformet", "for stort")
                # …MEN NULL KOMPRIMERT ER ET UENDELIG FORHOLD OGSÅ HER
                # (Cursor P2, runde 3). `continue` under hoppet over hele
                # bombe-armen nedenfor, så deklarasjonen var den ene
                # oppføringen som kunne påstå innhold med `compress_size
                # = 0` og likevel slippe gaten — nøyaktig hullet den
                # armen ble skrevet for å lukke, gjenåpnet for fila det
                # er BILLIGST å forme ondsinnet. Bare null-armen speiles:
                # ærlig JSON komprimerer over 100:1, og 4 MiB-taket over
                # binder skaden et ekte forholdstak ikke trengs for.
                if info.file_size > 0 and info.compress_size <= 0:
                    raise Buntfeil("komprimeringsforhold", info.filename)
                total += info.file_size
                if total > MAKS_TOTAL_UTPAKKET:
                    raise Buntfeil("total_for_stor", info.filename)
                medlemmer.append(Medlem(info.filename, info.file_size))
                continue
            endelse = _endelse(info.filename)
            if endelse in ARKIVENDELSER:
                raise Buntfeil("nostet_arkiv", info.filename)
            if endelse not in TILLATTE_ENDELSER:
                raise Buntfeil("ukjent_innholdstype", info.filename)
            if info.file_size > MAKS_ENKELTFIL:
                raise Buntfeil("enkeltfil_for_stor", info.filename)
            # `compress_size = 0` på en fil som PÅSTÅR innhold er ikke en
            # fil som ikke er komprimert — det er et uendelig forhold, og
            # den gamle sannhetstesten hoppet over hele sjekken for
            # nettopp den verdien (Cursor P2). En ondsinnet sentralkatalog
            # kunne dermed deklarere stor `file_size` og null komprimert.
            if info.file_size > 0 and (
                    info.compress_size <= 0
                    or info.file_size / info.compress_size
                    > MAKS_KOMPRIMERINGSFORHOLD):
                raise Buntfeil("komprimeringsforhold", info.filename)
            total += info.file_size
            if total > MAKS_TOTAL_UTPAKKET:
                raise Buntfeil("total_for_stor", info.filename)
            medlemmer.append(Medlem(info.filename, info.file_size))
    return medlemmer


@dataclass
class Budsjett:
    """Buntens ENE budsjett, båret gjennom alle nivåer (#155).

    Ni runder på `_inspiser_docx` fant ni ulike former og én rot: to
    implementasjoner av samme grense divergerer med nødvendighet. Den
    femte runden fant nettopp dette feltet — `MAKS_FILER` ble målt på
    nytt per docx, så to docx-er à 20 000 indre medlemmer passerte begge
    portene og ga 40 000 filer til uttrekket.

    Budsjettet er derfor et OBJEKT som gaten muterer, ikke tall som
    sendes inn og ut. Et friskt budsjett kan da ikke oppstå ved et uhell:
    det må konstrueres, og det gjøres ett sted.
    """

    filer: int = 0
    byte: int = 0


def _mal_medlem(navn: str, aapne, *, budsjett: Budsjett,
                komprimert: int | None = None,
                kontekst: str = "", dybde: int = 0) -> bytes:
    """ÉN gate. Måler ett medlem mot buntens budsjett, og bruker seg selv
    rekursivt på et DOCX-medlem (#155, eiers valg A).

    NAVNET MÅLES RÅTT (runde 6). Kontekst legges på for FEILMELDINGENS
    skyld, aldri før målingen: `_sjekk_navn("cv.docx//tmp/x")` ser en
    relativ sti med et rart navn, mens `_sjekk_navn("/tmp/x")` ser den
    absolutte stien den er. Runde 6 fant nøyaktig den forskjellen, og
    her er den en egenskap ved gaten, ikke en lapp.

    BYTENE ER SANNHETEN, IKKE KATALOGEN (runde 8). `file_size` og
    `compress_size` er hva sentralkatalogen PÅSTÅR. En patchet indre
    katalog kunne oppgi lav `file_size` og gå forbi 25 MB, 100:1 og 2 GB,
    mens den faktiske ekspansjonen skjedde i tekstuttrekket. Gaten leser
    derfor strømmen med et hardt tak og teller det den FAKTISK fikk —
    samme lærdom den ytre veien tok i `les_porsjonsvis`.

    FORHOLDET MÅLES MOT DET VI FAKTISK FIKK. `MAKS_KOMPRIMERINGSFORHOLD`
    sto før på katalogens `file_size / compress_size` — påstand delt på
    påstand. Her er telleren de bytene strømmen TALTE, så en katalog som
    lyver lavt om `file_size` ikke lenger kjøper seg forbi porten; den
    lyver da bare om sin egen nevner. `compress_size <= 0` på noe som
    leverte byte er et uendelig forhold, ikke «ukomprimert».

    ETT BUDSJETT, ÉN GANG (runde 9, presisert av Cursor-runde 2).
    Tidligere la strømmen containerens målte byte OG docx-gatens
    katalogsum til totalen, så et docx-lag betalte to ganger. Formen
    overlevde inn i denne gaten: containerens `lest` ble lagt til, og
    så betalte hvert indre medlem for de SAMME bytene en gang til.
    Nå betaler BLADENE, og bare de: et docx-medlem legger ikke sin egen
    blob til totalen, fordi medlemmene inni den gjør det. PDF og HTML
    er blader og betaler for seg selv. Ingen dobbelttelling, ingen
    nullstilling.
    """
    fullt = f"{kontekst}/{navn}" if kontekst else navn
    _sjekk_navn(navn, kontekst=fullt if kontekst else None)

    budsjett.filer += 1
    if budsjett.filer > MAKS_FILER:
        raise Buntfeil("for_mange_filer", f"{fullt}: {budsjett.filer}")

    # ENDELSEN FELLER FØR VI LESER. Et nøstet arkiv kjennes på to
    # uavhengige ting: navnet det bærer, og formen bytene har. Den
    # første koster ingenting og måles her, slik den ytre gaten gjør;
    # den andre måles under, på hele medlemmet. DOCX er unntaket, og
    # bare det.
    endelse = _endelse(navn)
    if endelse in ARKIVENDELSER:
        raise Buntfeil("nostet_arkiv", fullt)
    # DYBDEVAKTEN ER DET SOM BINDER REKURSJONEN — ikke de to portene
    # under. Ethvert ANNET nøstet arkiv felles på endelsen rett over
    # eller på formen (`endelse != ".docx"` og `er_arkiv`); DOCX er
    # unntatt fra begge, og en docx i en docx er derfor den ene formen
    # INGEN av dem ser. Vakten står HER, hos den andre endelsesarmen, og
    # ikke etter lesingen: et nøstet arkiv er felt på NAVNET, og da skal
    # det hverken leses inn mot 25 MB, belastes budsjettet, eller rekke
    # å bli omdøpt til `feil_innholdstype` av magiporten under — en
    # `word/nested.docx` med søppelbyte er et NØSTET ARKIV, som er den
    # grensen klarsignalet §4 setter til null. Tallet er ikke hellig;
    # vakten er, og uten den er klassen ikke lukket.
    if endelse == ".docx" and dybde:
        raise Buntfeil("nostet_arkiv", fullt)

    # CONTAINEREN BETALER IKKE FOR BARNA SINE. Et DOCX-medlem måles ett
    # nivå ned, byte for byte, og de bytene ER containerens innhold. Ble
    # begge lagt til, betalte hvert docx-lag omtrent DOBBELT mot
    # klarsignalets «utpakket totalstørrelse | 2 GB» — for `ZIP_STORED`
    # eller nesten ukomprimerbart innhold er containeren ≈ summen av de
    # indre bytene — og ærlige bunter under taket ble avvist. Zip-bomben
    # felles av den INDRE målingen, som er den som måler den faktiske
    # ekspansjonen. Dybdevakten over gjør betingelsen eksakt: en `.docx`
    # som kommer HIT har alltid dybde 0, og da følger indre måling
    # alltid i `_mal_docx`. Containerens egne byte er likevel BUNDET —
    # av `MAKS_ENKELTFIL` og av forholdet mot dens `komprimert`, begge
    # under — og den ytre katalogporten holder buntens leste totalsum
    # under taket uansett.
    betaler = endelse != ".docx"

    biter: list[bytes] = []
    lest = 0
    with aapne() as f:
        hode = f.read(_HODEBYTE)
        biter.append(hode)
        lest = len(hode)
        while True:
            bit = f.read(1 << 16)
            if not bit:
                break
            lest += len(bit)
            if lest > MAKS_ENKELTFIL:
                raise Buntfeil("enkeltfil_for_stor", fullt)
            if betaler and budsjett.byte + lest > MAKS_TOTAL_UTPAKKET:
                raise Buntfeil("total_for_stor", fullt)
            biter.append(bit)
    if lest > 0 and komprimert is not None and (
            komprimert <= 0
            or lest / komprimert > MAKS_KOMPRIMERINGSFORHOLD):
        raise Buntfeil("komprimeringsforhold", fullt)
    if betaler:
        budsjett.byte += lest
        if budsjett.byte > MAKS_TOTAL_UTPAKKET:
            raise Buntfeil("total_for_stor", fullt)
    data = b"".join(biter)

    # KLASSIFISERINGEN LESER BYTENE (runde 7). `_ser_ut_som_html` avviser
    # på FORM — positiv gjenkjenning — mens den gamle docx-veien avviste
    # på det katalogen PÅSTOD. Katalogen i en zip er ikke bytene i den, og
    # de to sidene har samme rot. Her er begge sider samme spørsmål: hva
    # ER dette, målt på innholdet?
    for magi in _MAGI.get(endelse, ()):
        if not data.startswith(magi):
            raise Buntfeil("feil_innholdstype", fullt)
    if endelse in (".html", ".htm"):
        if any(data.startswith(m) for m in _ARKIVMAGI):
            raise Buntfeil("nostet_arkiv", fullt)
        if not _ser_ut_som_html(data[:_HODEBYTE]):
            raise Buntfeil("feil_innholdstype", fullt)

    er_arkiv = zipfile.is_zipfile(io.BytesIO(data))
    if endelse != ".docx" and er_arkiv:
        raise Buntfeil("nostet_arkiv", fullt)
    if endelse == ".docx":
        # Dybden er alt felt over; her nede finnes bare dybde 0.
        _mal_docx(data, budsjett=budsjett, kontekst=fullt)
    return data


def _mal_docx(data: bytes, *, budsjett: Budsjett, kontekst: str) -> None:
    """DOCX-unntaket: samme gate, ett nivå ned.

    Unntaket er at DOCX er en av de tre lovede innholdstypene — ikke at
    grensene ikke gjelder inni den. Derfor er dette ikke en gate, men en
    LØKKE over `_mal_medlem`: alle sju rundenes funn treffer den ene
    implementasjonen, fordi det bare finnes én.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError,
            UnicodeDecodeError) as feil:
        raise Buntfeil("feil_innholdstype", kontekst) from feil
    with zf:
        try:
            alle = zf.infolist()
        except (zipfile.BadZipFile, RuntimeError, NotImplementedError,
                UnicodeDecodeError) as feil:
            raise Buntfeil("feil_innholdstype", kontekst) from feil
        # KATALOGEN AVVISES FØR DEN LESES (Codex P2). Den inkrementelle
        # tellingen under er riktig, men den er for SEN alene: en katalog
        # som overskrider grensen med én oppføring rakk å bli målt først
        # når `_mal_medlem` hadde åpnet og pakket ut hver eneste
        # foregående oppføring — altså opptil hele bytebudsjettet brukt
        # på en katalog vi ALLEREDE visste var for stor. Antallet er
        # kjent her: `alle` er materialisert, og den fjernede
        # implementasjonen sammenlignet nettopp `budsjett.filer +
        # len(alle)` før den leste noe. Terskelen er den samme som den
        # inkrementelle — hver oppføring i `alle` betaler nøyaktig én
        # gang, i mappearmen eller i `_mal_medlem` — så dette er en
        # tidligere avvisning, ikke en strengere grense. Ute gjør den
        # ytre gaten det samme med `len(alle) > MAKS_FILER` før løkkene.
        if budsjett.filer + len(alle) > MAKS_FILER:
            raise Buntfeil("for_mange_filer",
                           f"{kontekst}: {budsjett.filer + len(alle)}")
        # Mappene teller mot budsjettet, som ute (runde 7): grensen
        # bevokter arbeidet katalogen påfører oss, og det arbeidet er
        # gjort før vi rekker å filtrere.
        #
        # STIEN MÅLES PÅ HVER OPPFØRING, FØR FILTYPEN (Codex P2). Da
        # `_sjekk_navn` for medlemmene ble utsatt til `_mal_medlem`, ble
        # den stående igjen inne i mappearmen — men symlenketesten under
        # gjelder ALLE oppføringer. `../../escape.xml` med lenkebiter ble
        # derfor meldt som `symlenke` mens den er `sti_utenfor_bunten`,
        # og kodene er den offentlige, sikkerhetsrelevante utgangen av
        # gaten. Den ytre katalogporten måler navn på hver oppføring før
        # den ser på filtypen; her er rekkefølgen nå den samme. At
        # `_mal_medlem` måler navnet en gang til er samme dublett som
        # ute — en idempotent port, ikke to implementasjoner.
        for info in alle:
            _sjekk_navn(info.filename,
                        kontekst=f"{kontekst}/{info.filename}")
            if info.is_dir():
                budsjett.filer += 1
                if budsjett.filer > MAKS_FILER:
                    raise Buntfeil("for_mange_filer",
                                   f"{kontekst}/{info.filename}")
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise Buntfeil("symlenke", f"{kontekst}/{info.filename}")
        sett: set[str] = set()
        for info in (i for i in alle if not i.is_dir()):
            if info.filename in sett:
                raise Buntfeil("duplikat_medlem",
                               f"{kontekst}/{info.filename}")
            sett.add(info.filename)
            _mal_medlem(info.filename,
                        lambda i=info: zf.open(i),
                        budsjett=budsjett,
                        komprimert=info.compress_size,
                        kontekst=kontekst, dybde=1)
        if not DOCX_PAKKEMEDLEMMER <= sett:
            raise Buntfeil(
                "feil_innholdstype",
                f"{kontekst}: mangler "
                + ", ".join(sorted(DOCX_PAKKEMEDLEMMER - sett)))


def les_porsjonsvis(sti: str | Path, *, porsjon: int = 200):
    """Generator: (fremdrift, medlem, bytes) — porsjonsvis parsing med
    fremdrift som evidens (§7).

    Katalogen er en PÅSTAND, og strømmen stoler ikke på den: de faktiske
    bytene måles per fil og løpende totalt, og innholdstypeporten kjøres
    på de første bytene — deklarert endelse og magi må være samme
    påstand, og HTML med arkivmagi er et nøstet arkiv uansett navn.
    En bunt der katalog og innhold spriker (CRC/lengde) er KORRUPT og
    avvises med egen kode — aldri som en rå zipfile-exception; et rent
    feilutfall er kontrakten (SP-3).
    """
    medlemmer = inspiser_bunt(sti)
    # ÉN GATE, ETT BUDSJETT (#155). Strømmen hadde sin egen `total` og
    # `filer`, og docx-veien sin egen tilstand — to implementasjoner av
    # samme grense, som er nøyaktig roten de ni rundene delte. Budsjettet
    # er nå ett objekt som bæres gjennom hvert nivå.
    budsjett = Budsjett()
    with _apne_katalog(sti) as zf:
        innhold = [m for m in medlemmer if m.navn != MANIFESTNAVN]
        # HVER OPPFØRING TELLES ÉN GANG. Budsjettet kan ikke seedes med
        # hele katalogen når `_mal_medlem` teller sitt eget medlem: da
        # betalte hvert innholdsmedlem to ganger, og en ærlig bunt på
        # 10 001 oppføringer ble avvist mot et tak på 20 000. Her betaler
        # oppføringene strømmen IKKE selv måler — mappene og manifestet —
        # og medlemmene teller seg selv i gaten.
        budsjett.filer = max(0, len(zf.infolist()) - len(innhold))
        for nr, medlem in enumerate(innhold, start=1):
            # Begge oppslagene går på NAVNET, i strømmens eget navnekart —
            # det samme kartet uttrekket bruker. Et medlem som forsvant
            # mellom `inspiser_bunt`s lesning og denne feller dem begge
            # med `KeyError`, og det er et kodet utfall.
            with _kodet_lesefeil(medlem.navn,
                                 mangler="manifest_medlem_mangler"):
                komprimert = zf.getinfo(medlem.navn).compress_size
                data = _mal_medlem(medlem.navn,
                                   lambda m=medlem: zf.open(m.navn),
                                   budsjett=budsjett,
                                   komprimert=komprimert)
            if nr % porsjon == 0 or nr == len(innhold):
                fremdrift = {"filer_lest": nr,
                             "filer_totalt": len(innhold),
                             "byte_lest": budsjett.byte}
            else:
                fremdrift = None
            yield fremdrift, medlem, data


@dataclass(frozen=True)
class Manifestet:
    """Buntens deklarasjon, lest og toveisbundet: `kart` er
    {medlemsnavn: kandidat_id}; `felter` er kandidatens STRUKTURERTE
    personfelter ({kandidat_id: {felt: [verdier]}}) — blindingens kilde
    (#158s strukturelle retning: personfeltene DEKLARERES, de søkes
    aldri opp i fritekst)."""
    kart: dict[str, str]
    felter: dict[str, dict[str, list[str]]]


def _uten_duplikatnokler(par: list[tuple[str, object]]) -> dict:
    """`json`s objektbygger, men en DUPLIKATNØKKEL er `manifest_feilformet`
    (Codex P1, review 15:20 på `7b8fa66`).

    Standardoppførselen er stille «siste vinner»:
    `{"navn": ["Kari"], "navn": ["Ola"]}` blir `{"navn": ["Ola"]}`, og
    `Kari` — en personverdi kunden faktisk DEKLARERTE — finnes ikke
    lenger for blindingen. Navnet står fortsatt i CV-en, det maskeres
    ikke, og `krev_blindet` leter bare etter det som ER i
    avmaskeringstabellen; porten godkjenner altså en modellinput med
    klartekstnavnet i, og kjøringen telles som blindet. Det er en
    LEKKASJE, ikke en formfeil — og den er usynlig for hver eneste port
    nedstrøms, fordi tapet skjer FØR noen av dem får se dokumentet.

    Porten gjelder HELE manifestdokumentet, ikke bare `felter`: en
    duplisert `soknader`, `kandidat_id` eller `filer` taper like stille
    en deklarasjon vi da aldri får bundet toveis mot katalogen.
    Nøkkelnavnet trenger vi ikke gjette mellom — vi avviser, vi velger
    ikke: en deklarasjon som sier to ting om samme nøkkel er ingen
    deklarasjon. Samme dom som den lukkede toppformen og
    `KANDIDAT_ID_KANON`, og samme retning som resten av lesingen: én vei
    inn, og bunten som mente `Kari` sier `Kari`."""
    ut: dict = {}
    for nokkel, verdi in par:
        if nokkel in ut:
            raise Buntfeil("manifest_feilformet",
                           f"duplikatnøkkel {nokkel!r}")
        ut[nokkel] = verdi
    return ut


def les_manifest(sti: str | Path,
                 medlemmer: list[Medlem]) -> Manifestet:
    """#161 (eiers B): les og bind `soknader.json` mot katalogen, BEGGE
    veier, før én byte søknadsinnhold pakkes ut.

    -> `Manifestet` (kart + deklarerte personfelter).

    Lukket form: toppobjekt med NØYAKTIG nøkkelen `soknader`, en liste
    (1–MAKS_KANDIDATER) av objekter med NØYAKTIG `kandidat_id` (unik, og
    på `KANDIDAT_ID_KANON`s lukkede ASCII-form) og `filer` (ikke-tom
    liste av tekst, globalt unike). Alt annet — ukjente nøkler, feil
    typer, duplikater — er `manifest_feilformet`: en deklarasjon vi ikke
    forstår fullt ut er ingen deklarasjon.

    Toveisbindingen er dommen fra #153: et manifest-navn uten medlem
    (`manifest_medlem_mangler`) og et medlem uten manifest-linje
    (`medlem_uadressert`) er like røde — «den så ut som en søknad» er
    ikke en inspeksjon. Manifestet adresserer aldri seg selv.
    """
    navnene = {m.navn for m in medlemmer}
    if MANIFESTNAVN not in navnene:
        raise Buntfeil("manifest_mangler")
    # LESINGEN AV MANIFESTET ER EN LESING SOM ALLE ANDRE (Cursor P1).
    # `_apne_katalog` dekker ÅPNINGEN av sentralkatalogen, ikke uttrekket
    # av et medlem: en CRC-skadet, kryptert eller uleselig `soknader.json`
    # feller `zf.read` med bibliotekets egne former — `BadZipFile`,
    # dekompressorens `zlib.error`/`lzma.LZMAError`/errno-løse `OSError`,
    # og `RuntimeError`/`NotImplementedError` for passord og manglende
    # komprimering. Alle gikk rå forbi denne linja til `kjor_bunt`s
    # catch-all og ble meldt som `modellfeil` — samme klasse som
    # «lagring/dekompresjon ≠ modell»: en bunt vi ikke kan lese er kundens
    # avviste bunt med kode (SP-3), aldri en påstand om at MODELLEN sviktet.
    # `OSError` MED errno slipper gjennom som seg selv av samme grunn som i
    # `les_porsjonsvis`: en lesefeil på disk eller nettlager er DRIFT, ikke
    # buntens skyld.
    #
    # …OG FRAVÆRET MÅLES DER UTTREKKET SKJER (Cursor P1, runde 2).
    # Linja over slår opp `MANIFESTNAVN` i `medlemmer` — en katalog ANDRE
    # leste — mens `zf.read` åpner arkivet PÅ NYTT og slår opp i sitt eget
    # navnekart. Divergerer de to (fila byttet i vinduet mellom
    # `inspiser_bunt` og her, eller en `medlemmer`-liste som ikke kom fra
    # denne bunten), reiser `zipfile` en `KeyError` som gikk rå til
    # `kjor_bunt`s catch-all: `modellfeil` om en bunt modellen aldri fikk
    # se. Samme klasse som `kart.get` i `kjoring.py` — en garanti målt ett
    # sted er ingen garanti det andre. En deklarasjon vi ikke finner, ER
    # `manifest_mangler`.
    #
    # Oversettelsen sto før i to kopier, her og i strømmen. Det er den
    # kjente divergensrisikoen, og den er nå én funksjon
    # (`_kodet_lesefeil`) — samme dom som #155 feller over gate-siden.
    with _kodet_lesefeil(MANIFESTNAVN, mangler="manifest_mangler"):
        with _apne_katalog(sti) as zf:
            raa = zf.read(MANIFESTNAVN)
    # TAKET MÅLES PÅ BYTENE, IKKE BARE PÅ PÅSTANDEN (Cursor P2, runde 4).
    # `inspiser_bunt` håndhever `MAKS_MANIFESTBYTES` på `info.file_size`,
    # og det er KATALOGENS påstand — nøyaktig det strømmen ikke stoler på
    # for søknadsinnhold, der `lest > MAKS_ENKELTFIL` måles på de faktiske
    # bytene av samme grunn. Deklarasjonsarmen manglet den speilingen:
    # katalogen `inspiser_bunt` leste er ikke nødvendigvis den `zf.read`
    # åpner (fila byttet i vinduet, eller en `medlemmer`-liste fra en
    # annen lesning — samme divergens `manifest_mangler`-porten over
    # finnes for), og da var taket bare en påstand vi hadde tatt for god
    # fisk. Vi måler det vi faktisk holder.
    if len(raa) > MAKS_MANIFESTBYTES:
        raise Buntfeil("manifest_feilformet", "for stort")
    try:
        data = json.loads(raa.decode("utf-8"),
                          object_pairs_hook=_uten_duplikatnokler)
    except Buntfeil:
        # Hookens egen dom er ALLEREDE kodet (duplikatnøkkel). Den er
        # ingen `ValueError`, så den ville flydd forbi klausulen under
        # av seg selv — linja står her for at det skal være et VALG og
        # ikke en tilfeldighet ved klassehierarkiet.
        raise
    # JSON-DYBDE ER OGSÅ EN FORM (Cursor P1, runde 2). `json` melder
    # SYNTAKS som `ValueError`, men NØSTING som `RecursionError` — og den
    # er ingen `ValueError`. Noen tusen `[` innenfor `MAKS_MANIFESTBYTES`
    # (4 MiB rommer millioner) feller dermed dekoderen med et unntak som
    # gikk rått til `kjor_bunt`s catch-all og ble meldt som `modellfeil`.
    # Deklarasjonen er den delen av bunten som er BILLIGST å forme
    # ondsinnet, så feil kø her er feil kø for et angrep: en deklarasjon
    # vi ikke kan lese, er `manifest_feilformet` — uansett om det er
    # tegnene eller dybden vi ikke kommer gjennom.
    except (UnicodeDecodeError, ValueError, RecursionError) as feil:
        raise Buntfeil("manifest_feilformet", "uleselig json") from feil
    if not isinstance(data, dict) or set(data) != {"soknader"} \
            or not isinstance(data["soknader"], list):
        raise Buntfeil("manifest_feilformet", "lukket form")
    soknader = data["soknader"]
    if not 1 <= len(soknader) <= MAKS_KANDIDATER:
        raise Buntfeil("manifest_feilformet",
                       f"kandidattall {len(soknader)}")
    kart: dict[str, str] = {}
    felter_ut: dict[str, dict[str, list[str]]] = {}
    sett_kandidater: set[str] = set()
    for rad in soknader:
        # `felter` er VALGFRITT per kandidat (#158-retningen): uten
        # deklarerte personfelter kan kandidaten ikke blindes, og
        # kjøringen feller det som `blinding_uten_felter` — et kodet
        # utfall, aldri en gjettet NER over fritekst.
        if not isinstance(rad, dict) or set(rad) not in (
                {"kandidat_id", "filer"},
                {"kandidat_id", "filer", "felter"}):
            raise Buntfeil("manifest_feilformet", "lukket kandidatform")
        kid, filer = rad["kandidat_id"], rad["filer"]
        # ÉN LUKKET KANON, IKKE ÉN TEGNKLASSE PER RUNDE (eierdom, valg A
        # på #216). Porten sto før som `not kid.strip() or kid !=
        # kid.strip()` — den avviste blanktegn, men U+200B og resten av
        # Cf er lovlige, ULIKE nøkler `strip()` ikke rører, så `"k1"` og
        # `"k1<U+200B>"` var to lovlige kandidater i samme deklarasjon.
        # Klassen «to ID-er som ser like ut for et menneske» er ubundet
        # så lenge vi teller opp hva vi avviser; den er lukket i det
        # øyeblikket vi sier hva vi GODTAR. `KANDIDAT_ID_KANON` erstatter
        # begge betingelsene (flaten krymper), og vi avviser fortsatt i
        # stedet for å kanonisere: én vei inn, og bunten som mente `"k1"`
        # sier `"k1"`.
        if not isinstance(kid, str) \
                or not KANDIDAT_ID_KANON.fullmatch(kid) \
                or kid in sett_kandidater:
            raise Buntfeil("manifest_feilformet", "kandidat_id")
        sett_kandidater.add(kid)
        if not isinstance(filer, list) or not filer:
            raise Buntfeil("manifest_feilformet", f"filer for {kid}")
        for navn in filer:
            if not isinstance(navn, str) or navn in kart \
                    or navn == MANIFESTNAVN:
                raise Buntfeil("manifest_feilformet", str(navn))
            if navn not in navnene:
                raise Buntfeil("manifest_medlem_mangler", navn)
            kart[navn] = kid
        if "felter" in rad:
            fd = rad["felter"]
            if not isinstance(fd, dict) or not fd \
                    or not set(fd) <= set(blinding.MASKERTE_FELTER):
                raise Buntfeil("manifest_feilformet", f"felter for {kid}")
            rene: dict[str, list[str]] = {}
            for felt, verdier in fd.items():
                # ETT PREDIKAT, TO DØRER (eierdom, K2-kjennelse runde 4
                # på #217, valg A). Grensesettet — type, tomhet, antall,
                # lengde, verdiens egen skrivemåte — telles ikke opp her.
                # Det EIES av `blinding.feltverdier_lukket`, som `blind`
                # kaller på den injiserte veien (`kandidatfelter_for`,
                # som går utenom denne lesingen). To håndskrevne
                # opptellinger over samme sett ga fire Cursor-runder på
                # rad, én manglende grense per runde; med ett predikat
                # kan de to dørene ikke divergere. Bare FEILKODEN er
                # vår egen: den sier hvilken dør som felte, aldri
                # hvilken grense som gjaldt.
                if not blinding.feltverdier_lukket(verdier):
                    raise Buntfeil("manifest_feilformet",
                                   f"felter.{felt} for {kid}")
                rene[felt] = list(verdier)
            felter_ut[kid] = rene
    uadressert = navnene - set(kart) - {MANIFESTNAVN}
    if uadressert:
        raise Buntfeil("medlem_uadressert", sorted(uadressert)[0])
    return Manifestet(kart=kart, felter=felter_ut)
