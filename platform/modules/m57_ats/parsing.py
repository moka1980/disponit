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

import io
import lzma
import zipfile
import zlib
from dataclasses import dataclass
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
# grensene: `_inspiser_docx` måler det indre arkivet mot BUNTENS
# budsjett — samme tall, samme teller, aldri et friskt sett per docx.
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

    UTSATT, K1 → #161 — SØKNADSANTALLET er IKKE bundet her. `MAKS_FILER`
    er 20 000 katalogoppføringer; `antall_soknader` (1–5000) valideres ved
    bestillingen og leses aldri igjen. Gaten kjenner MEDLEMMER, ikke
    søkere — én søknad kan være `cv.html` alene eller `cv.html` +
    `vedlegg.docx`, og hvilken av delene det er, står ikke i en
    zip-katalog. Å telle filer mot 5000 ville vært å gjette grammatikken
    (SP-13/K4): en kandidatform kan ikke gjettes ut av katalogen, den må
    DEKLARERES.

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
    with zipfile.ZipFile(sti) as zf:
        alle = zf.infolist()
        if len(alle) > MAKS_FILER:
            raise Buntfeil("for_mange_filer", str(len(alle)))
        infos = [i for i in alle if not i.is_dir()]
        for info in infos:
            _sjekk_navn(info.filename)
            # En zip KAN bære to oppføringer med samme navn, og
            # `zf.open(navn)` slår opp i navnekartet — som bare husker den
            # SISTE. To ulike `cv.html` ville da blitt lest som den samme,
            # to ganger, og den første søknaden forsvunnet i stillhet
            # (Codex P2). Hvilke søknader som evalueres er ikke et sted
            # for stillhet: bunten avvises.
            if info.filename in sett:
                raise Buntfeil("duplikat_medlem", info.filename)
            sett.add(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise Buntfeil("symlenke", info.filename)
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


def _inspiser_docx(navn: str, data: bytes, *,
                   filer_brukt: int = 0,
                   byte_brukt: int = 0) -> tuple[int, int]:
    """DOCX-unntaket måles som alle andre arkiver — mot BUNTENS budsjett,
    ikke mot sitt eget (Codex P1).

    ROTÅRSAKEN, funnet fjerde runde på denne funksjonen: den indre gaten
    var en KOPI av den ytre med SIN EGEN tilstand. Hver runde fant nok en
    grense som ikke var kopiert (medlemsnavnet, 25 MB, duplikatene) — og
    denne gangen var det ikke en manglende sjekk, men en nullstilt
    teller: `MAKS_FILER` ble målt på nytt per docx, så to docx-er à
    20 000 indre medlemmer passerte begge portene og ga 40 000 filer til
    uttrekket. Grensene er buntens, ikke per arkiv: det som er brukt
    kommer inn (`filer_brukt`, `byte_brukt`), og det som ble brukt går ut
    (utpakkede byte, antall medlemmer). Da kan ingen ny nøstet fil få et
    friskt budsjett.

    En `.docx` ble sluppet gjennom på `PK`-magien og sin egen KOMPRIMERTE
    størrelse alene. En liten docx kan bære et indre medlem som pakker ut
    til gigabyte: den ytre bunten passerte hver eneste 2 GB/100:1-sjekk,
    fordi den bare inneholdt de alt komprimerte docx-bytene, og bomben
    møtte først tekstuttrekket. Unntaket er at DOCX er en av de tre lovede
    innholdstypene — ikke at grensene ikke gjelder inni den.

    UTSATT, K2 → #155. Sju runder har funnet sju ULIKE former og ÉN rot:
    to implementasjoner av samme grense divergerer med nødvendighet.
    Eier valgte A — én strømmende gate, brukt rekursivt — som eget issue
    + egen PR. Denne funksjonen er den lappede tilstanden til den lander,
    og hver lapp under er navngitt med runden som fant den.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as indre:
            alle = indre.infolist()
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as feil:
        # `PK` er ikke en zip; en docx som ikke lar seg lese som arkiv er
        # ikke en docx.
        raise Buntfeil("feil_innholdstype", navn) from feil
    # Mappene teller også her (Codex P2): budsjettet er katalogarbeid, og
    # en mappeoppføring koster like mye å lese som en filoppføring. Den
    # ytre gaten teller alle oppføringer, og den indre kan ikke telle
    # færre uten at forskjellen er nettopp veien rundt.
    if filer_brukt + len(alle) > MAKS_FILER:
        raise Buntfeil("for_mange_filer",
                       f"{navn}: {filer_brukt} + {len(alle)}")
    infos = [i for i in alle if not i.is_dir()]
    utpakket = 0
    sett: set[str] = set()
    for info in infos:
        _sjekk_navn(info.filename, kontekst=f"{navn}/{info.filename}")
        # Duplikatporten fra ytre gate gjelder også her (Cursor P2). En zip
        # kan bære to oppføringer med samme navn, og et medlemsoppslag på
        # navn treffer navnekartet, som bare husker den SISTE — to
        # `word/document.xml` betyr at det uttrekket leser ikke er det
        # samme dokumentet gaten målte. Hvilken tekst som evalueres er
        # ikke et sted for stillhet, hverken ute eller inne.
        if info.filename in sett:
            raise Buntfeil("duplikat_medlem", f"{navn}/{info.filename}")
        sett.add(info.filename)
        # Symlenkeporten fra ytre gate gjelder også her (Cursor P3). En
        # zip bærer filtypen i `external_attr`, og uttrekket skjer i
        # containeren: en lenke inni en docx er samme klasse som en
        # lenke i bunten, og den ene gaten kan ikke være strengere enn
        # den andre uten at forskjellen er et hull.
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise Buntfeil("symlenke", f"{navn}/{info.filename}")
        if _endelse(info.filename) in ARKIVENDELSER:
            raise Buntfeil("nostet_arkiv", f"{navn}/{info.filename}")
        # 25 MB-grensen gjelder MEDLEMMET, også inni en docx (Codex P1).
        # Løkken målte forholdet og totalen, men ikke enkeltfilen — og de
        # tre grensene fanger ulike ting: et moderat komprimerbart medlem
        # kan pakke ut til hundrevis av megabyte uten å bryte hverken
        # 100:1 eller 2 GB, mens den ytre docx-en holder seg under 25 MB
        # og passerer ytre gate. Da er det nettopp den overdimensjonerte
        # inputen tekstuttrekket møter, som grensen finnes for å stoppe.
        if info.file_size > MAKS_ENKELTFIL:
            raise Buntfeil("enkeltfil_for_stor", f"{navn}/{info.filename}")
        if info.file_size > 0 and (
                info.compress_size <= 0
                or info.file_size / info.compress_size
                > MAKS_KOMPRIMERINGSFORHOLD):
            raise Buntfeil("komprimeringsforhold",
                           f"{navn}/{info.filename}")
        utpakket += info.file_size
        if byte_brukt + utpakket > MAKS_TOTAL_UTPAKKET:
            raise Buntfeil("total_for_stor", f"{navn}/{info.filename}")
    # Innholdstypeporten måler PAKKEN, ikke endelsen (Codex P2). Sjekken
    # står etter løkken med vilje: en docx med en sti utenfor bunten er
    # avvist som nettopp det, ikke som feil innholdstype.
    if not DOCX_PAKKEMEDLEMMER <= sett:
        raise Buntfeil(
            "feil_innholdstype",
            f"{navn}: mangler "
            + ", ".join(sorted(DOCX_PAKKEMEDLEMMER - sett)))
    return utpakket, len(alle)


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
    total = 0
    with zipfile.ZipFile(sti) as zf:
        # ÉN teller for hele bunten, nøstede docx-medlemmer inkludert
        # (Codex P1): budsjettet starter på buntens egne oppføringer og
        # forbrukes videre av hver docx, aldri nullstilt per arkiv. Det
        # er KATALOGOPPFØRINGENE som telles, ikke de filtrerte
        # medlemmene — ellers finansierte hver mappe i den ytre bunten
        # et indre docx-medlem gratis (Codex P2).
        filer = len(zf.infolist())
        for nr, medlem in enumerate(medlemmer, start=1):
            biter: list[bytes] = []
            lest = 0
            try:
                with zf.open(medlem.navn) as f:
                    hode = f.read(_HODEBYTE)
                    endelse = _endelse(medlem.navn)
                    for magi in _MAGI.get(endelse, ()):
                        if not hode.startswith(magi):
                            raise Buntfeil("feil_innholdstype",
                                           medlem.navn)
                    if endelse in (".html", ".htm"):
                        if any(hode.startswith(m) for m in _ARKIVMAGI):
                            raise Buntfeil("nostet_arkiv", medlem.navn)
                        # HTML har ingen magi, men den har en FORM: et
                        # dokument begynner med et merke. Den gamle porten
                        # var en denyliste på åtte byte — og `%PDF` sto
                        # ikke i den, tross kommentaren som lovet det, så
                        # en PDF omdøpt til `cv.html` gikk rett gjennom
                        # (Codex P2). En positiv form fanger hele klassen
                        # i stedet for de signaturene noen kom på — og
                        # hodet må være langt nok til at formen finnes i
                        # det: åtte byte rommet ikke en BOM og et par
                        # linjeskift før merket.
                        if not _ser_ut_som_html(hode):
                            raise Buntfeil("feil_innholdstype",
                                           medlem.navn)
                    biter.append(hode)
                    lest = len(hode)
                    while True:
                        bit = f.read(1 << 16)
                        if not bit:
                            break
                        lest += len(bit)
                        if lest > MAKS_ENKELTFIL:
                            raise Buntfeil("enkeltfil_for_stor",
                                           medlem.navn)
                        # Denne sjekken er den TIDLIGE: den avbryter en
                        # bombe midt i strømmen, før hele medlemmet ligger
                        # i `biter`. Den er ikke totalens eneste port — se
                        # under.
                        if total + lest > MAKS_TOTAL_UTPAKKET:
                            raise Buntfeil("total_for_stor", medlem.navn)
                        biter.append(bit)
            except zipfile.BadZipFile as feil:
                raise Buntfeil("korrupt_bunt",
                               f"{medlem.navn}: {feil}") from feil
            # DEKOMPRESSOREN har sine EGNE feiltyper (Codex P2). `zipfile`
            # oversetter bare det den selv oppdager — ødelagte headere og
            # CRC-avvik — til `BadZipFile`. En SKADET komprimert strøm
            # feller biblioteket under, FØR CRC-en i det hele tatt måles,
            # og da kommer feilen i det formatets egen form: DEFLATE gir
            # `zlib.error`, LZMA gir `lzma.LZMAError`, og BZIP2 gir en
            # `OSError` uten errno («Invalid data stream»). Alle tre gikk
            # forbi begge håndteringene her, så en STRUKTURELT gyldig zip
            # med ødelagt payload ble en uventet arbeiderfeil i stedet for
            # det kodede utfallet kontrakten lover (SP-3) — og den formen
            # er billig å lage for den som leverer bunten.
            except (zlib.error, lzma.LZMAError) as feil:
                raise Buntfeil("korrupt_bunt",
                               f"{medlem.navn}: {type(feil).__name__}"
                               ) from feil
            # `OSError` MED errno er noe helt annet: lesefeil på den
            # underliggende fila (disk, nettlager). Det er ikke en påstand
            # om buntens innhold, og å kalle det `korrupt_bunt` ville
            # gjort en driftsfeil til en kundeavvisning — bunten ville
            # blitt forkastet for noe som var vårt. Den slipper derfor
            # gjennom som seg selv; bare den errno-løse formen
            # dekompressoren kaster, er buntens skyld.
            except OSError as feil:
                if feil.errno is not None:
                    raise
                raise Buntfeil("korrupt_bunt",
                               f"{medlem.navn}: {type(feil).__name__}"
                               ) from feil
            # Et passordbeskyttet medlem passerer katalogen, men `zf.open`
            # kaster `RuntimeError: password required` — og en komprimering
            # biblioteket ikke har (bzip2/lzma uten modul) kaster
            # `NotImplementedError`. Begge slapp ut som RÅ exceptions forbi
            # denne håndteringen (Codex P2). Kontrakten er et KODET utfall
            # (SP-3), aldri en bibliotekfeil: en bunt vi ikke kan lese er
            # avvist med grunn, ikke en 500.
            except (RuntimeError, NotImplementedError) as feil:
                raise Buntfeil("uleselig_medlem",
                               f"{medlem.navn}: {type(feil).__name__}"
                               ) from feil
            # Totalen håndheves der den ENDRES, ikke bare der den vokser
            # (Codex P2). Sjekken over står inne i lesesløyfa, og et medlem
            # som får plass i førstelesingen kommer aldri inn i den:
            # `f.read` returnerer tomt, `break` går, og denne linja la
            # medlemmet til uten å spørre. Én liten HTML-fil etter en bunt
            # som alt lå tett på 2 GB — eller mange av dem — passerte
            # dermed grensen fritt. `_inspiser_docx` måler alt sin egen
            # total etter hver oppdatering; den ytre strømmen gjorde det
            # ikke.
            total += lest
            if total > MAKS_TOTAL_UTPAKKET:
                raise Buntfeil("total_for_stor", medlem.navn)
            if _endelse(medlem.navn) == ".docx":
                utpakket, indre = _inspiser_docx(
                    medlem.navn, b"".join(biter),
                    filer_brukt=filer, byte_brukt=total)
                total += utpakket
                filer += indre
            if nr % porsjon == 0 or nr == len(medlemmer):
                fremdrift = {"filer_lest": nr,
                             "filer_totalt": len(medlemmer),
                             "byte_lest": total}
            else:
                fremdrift = None
            yield fremdrift, medlem, b"".join(biter)
