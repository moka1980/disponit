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
import os
import shlex
import subprocess
import tempfile
import time
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

#: Hvor ofte den voksende stdout-filen måles mot `MAKS_TEKST` mens
#: pdf-kommandoen kjører. Overskytelsen kommandoen rekker å skrive før
#: den felles, er dermed bundet av gjennomstrømningen i ETT intervall —
#: ikke av hele fristen.
_PDF_MAALEINTERVALL_S = 0.1


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
        """GRENSEN HÅNDHEVES MENS DEN SKRIVES, IKKE ETTERPÅ (Codex P1,
        #173).

        `capture_output=True` materialiserte HELE stdout i minnet før
        `tekst_for`s tak i det hele tatt fikk se den. En PDF innenfor
        arkivets 25 MiB kan pakke ut til langt mer tekst enn unitens
        `MemoryMax=1G` (`deploy/staging/disponit-m57.service`), og da
        ble arbeideren OOM-drept før den rakk å returnere det kodede
        `uttrekk_uleselig`-utfallet: taket sto bak den døren det skulle
        vokte.

        Derfor går stdout til en TEMPORÆR FIL som måles mens den
        vokser, og kommandoen felles i det den passerer `MAKS_TEKST`.
        Minnet ser aldri mer enn ett tak (+1 byte), og disken heller.

        SAMME TALL, IKKE ET NYTT: rå stdout måles mot `MAKS_TEKST` selv
        om taket ellers måles på ETTER-formen. `errors="replace"` kan
        bare VOKSE (én ugyldig byte blir tre), aldri krympe — så
        `len(tekst.encode("utf-8")) >= len(rå)`, og rå over taket
        betyr at kontraktporten uansett ville felt dokumentet. Den
        tidlige grensen avviser altså aldri noe den sene ville sluppet
        gjennom, og et speil nummer to oppstår ikke.

        Både stdin og stdout er filer, aldri rør: et rør fylles opp av
        en kommando som skriver mens vi fortsatt mater den, og da står
        begge parter og venter på hverandre til fristen.
        """
        if not self.pdf_kommando:
            raise Uttrekksfeil("uttrekk_ustottet", "pdf uten kommando")
        try:
            with tempfile.TemporaryFile() as inn, \
                    tempfile.TemporaryFile() as ut:
                inn.write(data)
                inn.seek(0)
                kode = self._kjor_bundet(inn, ut)
                if kode != 0:
                    raise Uttrekksfeil("uttrekk_uleselig",
                                       f"pdf: rc={kode}")
                ut.seek(0)
                raa = ut.read(MAKS_TEKST + 1)
        except OSError as feil:
            # SPOLEN ER DRIFT, IKKE DOKUMENTETS FEIL (Codex P2, #173).
            # Denne grenen dekker `TemporaryFile`, `write`, `read` og
            # `fstat` — altså det MIDLERTIDIGE FILSYSTEMET, ikke
            # kundens pdf. Er den flaten full, borte eller svarer den
            # EIO, sa denne linjen likevel `uttrekk_uleselig`: bunten
            # ble avbrutt med at søkerens dokument var ulesbart, og
            # arbeiderens retry og driftsalarmen leste feil kø. Samme
            # misattribusjon som `_spoletekst` fikk rettet én kodevei
            # lenger ut, og koden er den samme derfra:
            # `infrastrukturfeil` bæres urørt til `kjor_bunt`s
            # oversetter, som alt kjenner den.
            #
            # Kommandoen som ikke lar seg STARTE er en annen sak og
            # felles i `_kjor_bundet` før den når hit — se der.
            raise Uttrekksfeil("infrastrukturfeil",
                               f"pdf-spole: {type(feil).__name__}") from feil
        # Kommandoen kan ha rukket å skrive forbi taket innenfor ett
        # måleintervall, og en kommando som avslutter av seg selv blir
        # aldri målt underveis i det hele tatt.
        if len(raa) > MAKS_TEKST:
            raise Uttrekksfeil("uttrekk_uleselig", "pdf: tekst for stor")
        return raa.decode("utf-8", errors="replace")

    def _kjor_bundet(self, inn, ut) -> int:
        """Kjører `pdf_kommando` med stdout til `ut`, og dreper den så
        snart filen passerer `MAKS_TEKST` eller fristen er ute.

        STARTEN FELLES FOR SEG (Codex P2, #173). `Popen` reiser
        `OSError` når den konfigurerte kommandoen ikke finnes, ikke er
        kjørbar eller mangler ressurser å starte i. Det er ikke
        spolens feil, og det er ikke pdf-ens: det er DEPLOYMENTEN som
        ikke kan trekke ut pdf — nøyaktig samme sak som en tom
        `pdf_kommando`, og derfor samme kode. Uten denne oversettelsen
        falt starten ned i `_pdf`s spolegren og ville blitt meldt som
        `infrastrukturfeil`, altså en drift-retry mot en feil som
        aldri går over av seg selv."""
        frist = time.monotonic() + self.frist_s
        try:
            p = subprocess.Popen(self.pdf_kommando, stdin=inn, stdout=ut,
                                 stderr=subprocess.DEVNULL)
        except OSError as feil:
            raise Uttrekksfeil(
                "uttrekk_ustottet",
                f"pdf-kommando: {type(feil).__name__}") from feil
        with p:
            while True:
                igjen = frist - time.monotonic()
                try:
                    p.wait(timeout=max(0.0,
                                       min(_PDF_MAALEINTERVALL_S, igjen)))
                    return p.returncode
                except subprocess.TimeoutExpired:
                    pass
                if os.fstat(ut.fileno()).st_size > MAKS_TEKST:
                    p.kill()
                    raise Uttrekksfeil("uttrekk_uleselig",
                                       "pdf: tekst for stor")
                if igjen <= 0:
                    p.kill()
                    raise Uttrekksfeil("uttrekk_uleselig",
                                       "pdf: TimeoutExpired")

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
