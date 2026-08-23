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
# arkivmagi eller arkivendelse felles.
ARKIVENDELSER = frozenset({
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar"})
TILLATTE_ENDELSER = frozenset({".pdf", ".docx", ".html", ".htm"})

#: Magi per endelse: deklarasjonen og innholdet må være SAMME påstand
#: («feil innholdstype»-porten). HTML har ingen pålitelig magi og måles
#: bare negativt (ikke arkiv-, ikke PDF-magi).
_MAGI = {".pdf": (b"%PDF",), ".docx": (b"PK\x03\x04",)}
_ARKIVMAGI = (b"PK\x03\x04", b"\x1f\x8b", b"7z\xbc\xaf", b"Rar!",
              b"BZh", b"\xfd7zXZ")


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
    with zipfile.ZipFile(sti) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > MAKS_FILER:
            raise Buntfeil("for_mange_filer", str(len(infos)))
        for info in infos:
            _sjekk_navn(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise Buntfeil("symlenke", info.filename)
            endelse = _endelse(info.filename)
            if endelse in ARKIVENDELSER:
                raise Buntfeil("nostet_arkiv", info.filename)
            if endelse not in TILLATTE_ENDELSER:
                raise Buntfeil("ukjent_innholdstype", info.filename)
            if info.file_size > MAKS_ENKELTFIL:
                raise Buntfeil("enkeltfil_for_stor", info.filename)
            if info.compress_size and (
                    info.file_size / info.compress_size
                    > MAKS_KOMPRIMERINGSFORHOLD):
                raise Buntfeil("komprimeringsforhold", info.filename)
            total += info.file_size
            if total > MAKS_TOTAL_UTPAKKET:
                raise Buntfeil("total_for_stor", info.filename)
            medlemmer.append(Medlem(info.filename, info.file_size))
    return medlemmer


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
                    hode = f.read(8)
                    endelse = _endelse(medlem.navn)
                    for magi in _MAGI.get(endelse, ()):
                        if not hode.startswith(magi):
                            raise Buntfeil("feil_innholdstype",
                                           medlem.navn)
                    if endelse in (".html", ".htm") and any(
                            hode.startswith(m) for m in _ARKIVMAGI):
                        raise Buntfeil("nostet_arkiv", medlem.navn)
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
            total += lest
            if nr % porsjon == 0 or nr == len(medlemmer):
                fremdrift = {"filer_lest": nr,
                             "filer_totalt": len(medlemmer),
                             "byte_lest": total}
            else:
                fremdrift = None
            yield fremdrift, medlem, b"".join(biter)
