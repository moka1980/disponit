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

#: UTTREKKETS UTDATA HAR SAMME TAK SOM SINKEN (Codex P2, #173).
#:
#: `api.app._KANDIDAT_DOK_MAKS` avviser en parsettekst over 25 MiB som
#: `request_feilformet`, og controlleren melder den 4xx-en som
#: `kandidatlagring_feilet` for HELE bunten. Men uttrekkeren hadde ingen
#: tilsvarende grense: `_pdf` returnerte hele `pdftotext`-stdout uansett
#: størrelse. En PDF som er innenfor arkivets `MAKS_ENKELTFIL` (25 MiB
#: komprimert/binært) kan lovlig pakke ut til langt mer tekst — og da
#: godtok arkivgaten dokumentet, godtok uttrekket det, og sinken felte
#: bunten på noe ingen av de to portene hadde sagt fra om.
#:
#: Grensen hører hjemme HER, ikke ved sinken: her er den et KODET
#: uttrekksutfall om ett dokument (`uttrekk_uleselig`, båret urørt
#: gjennom `kjor_bunt`s oversetter), mens den ved sinken er en
#: lagringsfeil om hele evalueringen. Å heve sinkens tak i stedet ville
#: sluppet ubundet tekst inn i `TEXT`-kolonnen og fjernet §4-budsjettet
#: i stedet for å flytte det.
#:
#: Tallet er §4-tallet, speilet — modules/ og api/ importerer ikke
#: hverandre. `test_173_uttrekkstaket_er_sinkens_tak` binder de to.
#:
#: Målt på ETTER-formen, `len(tekst.encode("utf-8"))`, som er nøyaktig
#: det sinken måler: `errors="replace"` kan gjøre én ugyldig byte til
#: tre (U+FFFD), så et tak på rå stdout ville vært et annet tall enn
#: sinkens og sluppet gjennom akkurat det som felte bunten.
MAKS_TEKST = 25 * 1024 * 1024


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
            tekst = _html(data)
        elif navn.endswith(".docx"):
            tekst = _docx(data)
        elif navn.endswith(".pdf"):
            tekst = self._pdf(data)
        else:
            raise Uttrekksfeil("uttrekk_ustottet", medlem.navn)
        # PÅ KONTRAKTGRENSEN, IKKE I HVER UTTREKKER (Codex P2, #173).
        # `MAKS_TEKST` er en egenskap ved det uttrekket LEVERER, og dette
        # er det ene stedet det forlater modulen. `_pdf` var den eneste
        # ubundne veien i dag — `_html` gir aldri mer tekst enn kilden,
        # og `_docx` er alt bundet av `MAKS_DOCX_XML` — men porten måler
        # kontrakten, ikke dagens tre implementasjoner av den: en fjerde
        # uttrekker skal arve grensen, ikke måtte huske den.
        if len(tekst.encode("utf-8")) > MAKS_TEKST:
            raise Uttrekksfeil("uttrekk_uleselig",
                               f"{medlem.navn}: tekst for stor")
        return tekst
