"""Tekstuttrekket: `tekst_for(medlem, data) -> str` for kontraktens tre
innholdstyper. HTML og DOCX trekkes ut i prosessen med stdlib-parsere
(ekte parsere, aldri regex over fremmed grammatikk — SP-13/K4); PDF
delegeres til en KONFIGURERT kommando (`pdftotext`-form) — samme
delegasjonsmønster som m56s motor.

Uttrekksfeil er kodede `Uttrekksfeil` (SP-3), aldri rå unntak: kjøringen
skal kunne si «denne FILEN kunne ikke leses», ikke «modellen feilet».
"""
from __future__ import annotations

import io
import shlex
import subprocess
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree


class Uttrekksfeil(Exception):
    def __init__(self, kode: str, detalj: str = ""):
        self.kode = kode
        super().__init__(f"{kode}: {detalj}" if detalj else kode)


class _HtmlTekst(HTMLParser):
    _DROPP = {"script", "style"}

    def __init__(self):
        super().__init__()
        self.biter: list[str] = []
        self._dropp = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._DROPP:
            self._dropp += 1

    def handle_endtag(self, tag):
        if tag in self._DROPP and self._dropp:
            self._dropp -= 1

    def handle_data(self, data):
        if not self._dropp and data.strip():
            self.biter.append(data.strip())


def _html(data: bytes) -> str:
    p = _HtmlTekst()
    try:
        p.feed(data.decode("utf-8", errors="strict"))
    except UnicodeDecodeError as feil:
        raise Uttrekksfeil("uttrekk_uleselig", "html-koding") from feil
    return "\n".join(p.biter)


#: Taket for utpakket dokument-XML: en docx-bombe skal felles her, ikke
#: i minnet (samme grense som buntens enkeltfiltak).
MAKS_DOCX_XML = 25 * 1024 * 1024


def _docx(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            info = zf.getinfo("word/document.xml")
            if info.file_size > MAKS_DOCX_XML:
                raise Uttrekksfeil("uttrekk_uleselig", "docx: for stor")
            with zf.open(info) as f:
                # Målt på FAKTISKE byte, ikke bare katalogpåstanden.
                xml = f.read(MAKS_DOCX_XML + 1)
            if len(xml) > MAKS_DOCX_XML:
                raise Uttrekksfeil("uttrekk_uleselig", "docx: for stor")
        rot = ElementTree.fromstring(xml)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError,
            OSError) as feil:
        raise Uttrekksfeil("uttrekk_uleselig",
                           f"docx: {type(feil).__name__}") from feil
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    avsnitt = []
    for p in rot.iter(f"{ns}p"):
        tekst = "".join(t.text or "" for t in p.iter(f"{ns}t"))
        if tekst.strip():
            avsnitt.append(tekst.strip())
    return "\n".join(avsnitt)


class Uttrekker:
    """`pdf_kommando` er driftskonfigurert (f.eks. «pdftotext - -»):
    PDF-bytene på stdin, tekst på stdout, hard frist. Tom kommando =
    PDF-uttrekk er utilgjengelig i denne deploymenten, og en PDF i
    bunten er da et kodet stopp — aldri en stille tom tekst."""

    def __init__(self, pdf_kommando: str = "", *, frist_s: float = 60.0):
        self.pdf_kommando = shlex.split(pdf_kommando) if pdf_kommando \
            else []
        self.frist_s = frist_s

    def _pdf(self, data: bytes) -> str:
        if not self.pdf_kommando:
            raise Uttrekksfeil("uttrekk_ustottet", "pdf uten kommando")
        try:
            r = subprocess.run(self.pdf_kommando, input=data,
                               capture_output=True,
                               timeout=self.frist_s, check=False)
        except (OSError, subprocess.TimeoutExpired) as feil:
            raise Uttrekksfeil("uttrekk_uleselig",
                               f"pdf: {type(feil).__name__}") from feil
        if r.returncode != 0:
            raise Uttrekksfeil("uttrekk_uleselig",
                               f"pdf: rc={r.returncode}")
        return r.stdout.decode("utf-8", errors="replace")

    def tekst_for(self, medlem, data: bytes) -> str:
        navn = medlem.navn.lower()
        if navn.endswith((".html", ".htm")):
            return _html(data)
        if navn.endswith(".docx"):
            return _docx(data)
        if navn.endswith(".pdf"):
            return self._pdf(data)
        raise Uttrekksfeil("uttrekk_ustottet", medlem.navn)
