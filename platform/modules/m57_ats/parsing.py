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
import zipfile
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
# grensene: `_inspiser_docx` måler det indre arkivet med de samme
# tallene, og legger den utpakkede totalen til buntens.
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


def _sjekk_navn(navn: str) -> None:
    ren = navn.replace("\\", "/")
    if ren.startswith("/") or (len(ren) > 1 and ren[1] == ":"):
        raise Buntfeil("sti_utenfor_bunten", navn)
    if any(del_ == ".." for del_ in ren.split("/")):
        raise Buntfeil("sti_utenfor_bunten", navn)


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
    """
    if not zipfile.is_zipfile(sti):
        raise Buntfeil("ikke_zip")
    medlemmer: list[Medlem] = []
    total = 0
    sett: set[str] = set()
    with zipfile.ZipFile(sti) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAKS_FILER:
            raise Buntfeil("for_mange_filer", str(len(infos)))
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


def _inspiser_docx(navn: str, data: bytes) -> int:
    """DOCX-unntaket måles som alle andre arkiver — og returnerer den
    utpakkede totalen sin, så den teller med i buntens (Codex P1).

    En `.docx` ble sluppet gjennom på `PK`-magien og sin egen KOMPRIMERTE
    størrelse alene. En liten docx kan bære et indre medlem som pakker ut
    til gigabyte: den ytre bunten passerte hver eneste 2 GB/100:1-sjekk,
    fordi den bare inneholdt de alt komprimerte docx-bytene, og bomben
    møtte først tekstuttrekket. Unntaket er at DOCX er en av de tre lovede
    innholdstypene — ikke at grensene ikke gjelder inni den.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as indre:
            infos = [i for i in indre.infolist() if not i.is_dir()]
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as feil:
        # `PK` er ikke en zip; en docx som ikke lar seg lese som arkiv er
        # ikke en docx.
        raise Buntfeil("feil_innholdstype", navn) from feil
    if len(infos) > MAKS_FILER:
        raise Buntfeil("for_mange_filer", f"{navn}: {len(infos)}")
    utpakket = 0
    for info in infos:
        _sjekk_navn(f"{navn}/{info.filename}")
        if _endelse(info.filename) in ARKIVENDELSER:
            raise Buntfeil("nostet_arkiv", f"{navn}/{info.filename}")
        if info.file_size > 0 and (
                info.compress_size <= 0
                or info.file_size / info.compress_size
                > MAKS_KOMPRIMERINGSFORHOLD):
            raise Buntfeil("komprimeringsforhold",
                           f"{navn}/{info.filename}")
        utpakket += info.file_size
        if utpakket > MAKS_TOTAL_UTPAKKET:
            raise Buntfeil("total_for_stor", f"{navn}/{info.filename}")
    return utpakket


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
                        if total + lest > MAKS_TOTAL_UTPAKKET:
                            raise Buntfeil("total_for_stor", medlem.navn)
                        biter.append(bit)
            except zipfile.BadZipFile as feil:
                raise Buntfeil("korrupt_bunt",
                               f"{medlem.navn}: {feil}") from feil
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
            total += lest
            if _endelse(medlem.navn) == ".docx":
                total += _inspiser_docx(medlem.navn, b"".join(biter))
                if total > MAKS_TOTAL_UTPAKKET:
                    raise Buntfeil("total_for_stor", medlem.navn)
            if nr % porsjon == 0 or nr == len(medlemmer):
                fremdrift = {"filer_lest": nr,
                             "filer_totalt": len(medlemmer),
                             "byte_lest": total}
            else:
                fremdrift = None
            yield fremdrift, medlem, b"".join(biter)
