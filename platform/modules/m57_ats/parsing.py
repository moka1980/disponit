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
import json
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

#: #161 (eiers B): bunten BÆRER sin egen deklarasjon — et lukket
#: `soknader.json` i roten navngir hver kandidat og filene hens.
#: Grensene er klarsignalets §4: 1–5000 kandidater.
MANIFESTNAVN = "soknader.json"
MAKS_KANDIDATER = 5000
MAKS_MANIFESTBYTES = 4 * 1024 * 1024

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
    docx-runden (`_inspiser_docx`); den ytre hadde den ikke.

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

    UTSATT, K2 → #155. NI runder har funnet ni ULIKE former og ÉN rot:
    to implementasjoner av samme grense divergerer med nødvendighet.
    Eier valgte A — én strømmende gate, brukt rekursivt — som eget issue
    + egen PR. Denne funksjonen er den lappede tilstanden til den lander,
    og hver lapp under er navngitt med runden som fant den.

    Runde 8 (Cursor P1) og runde 9 (Cursor P2) er IKKE lappet, og det er
    med vilje — de treffer ikke en manglende sjekk, men selve
    delelinjen mellom de to gatene, altså nøyaktig det #155 river ut:

    * Runde 8 — GRENSENE HER MÅLER KATALOGEN, IKKE BYTENE. `file_size`
      og `compress_size` under er hva den indre sentralkatalogen
      PÅSTÅR; ingenting leses, og ingen CRC måles. En patchet indre
      katalog kan derfor oppgi lav `file_size` og gå forbi 25 MB,
      100:1 og 2 GB, mens den faktiske ekspansjonen først skjer i
      tekstuttrekket. Den ytre veien lærte dette i `les_porsjonsvis`
      (katalogen er en PÅSTAND, strømmen måler byte). Lappen ville vært
      å lese hvert indre medlem med et hardt tak her — som ER den
      strømmende gaten, bygget for tredje gang, i en fiksrunde. #155s
      egen tekst felte denne formen på forhånd: «Katalogen i en zip er
      ikke bytene i den.»
    * Runde 9 — BUDSJETTLINJEN. `les_porsjonsvis` legger både
      containerens målte byte (`lest`) og denne funksjonens
      katalogsum (`utpakket`) til totalen, så et docx-lag betaler to
      ganger. Det feiler LUKKET (en ærlig bunt kan avvises som for
      stor, ingen slipper gjennom), og det er grunnen til at det ikke
      hastes: å fjerne `lest` her ville tatt bort den ENESTE målte
      byten en docx bidrar med, og latt katalogpåstanden fra runde 8
      stå alene som budsjett. Hvilket lag som betaler, er ikke en lapp
      — det er definisjonen av «ett budsjett, gjennomgående», og den
      hører til i #155.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as indre:
            alle = indre.infolist()
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError,
            UnicodeDecodeError) as feil:
        # `PK` er ikke en zip; en docx som ikke lar seg lese som arkiv er
        # ikke en docx. `UnicodeDecodeError` er samme klasse og kom inn
        # sammen med den ytre døren (`_apne_katalog`): et indre filnavn
        # som PÅSTÅR UTF-8 uten å være det, feller `ZipFile(...)` med en
        # rå `ValueError` — den ene bibliotekformen denne tuppelen ikke
        # kjente. Døren utvides, den bygges ikke på nytt.
        raise Buntfeil("feil_innholdstype", navn) from feil
    # Mappene teller også her (Codex P2): budsjettet er katalogarbeid, og
    # en mappeoppføring koster like mye å lese som en filoppføring. Den
    # ytre gaten teller alle oppføringer, og den indre kan ikke telle
    # færre uten at forskjellen er nettopp veien rundt.
    if filer_brukt + len(alle) > MAKS_FILER:
        raise Buntfeil("for_mange_filer",
                       f"{navn}: {filer_brukt} + {len(alle)}")
    # Runde 20 (Codex P2): sti- og lenkeporten måler HVER oppføring, ikke
    # bare de vi pakker ut. Samme rekkefølgefeil som i den ytre gaten —
    # mappene ble filtrert bort før navnet ble målt, så `../../unnslapp/`
    # inni en docx passerte porten som finnes for å avvise den.
    for info in alle:
        _sjekk_navn(info.filename, kontekst=f"{navn}/{info.filename}")
        # Symlenkeporten fra ytre gate gjelder også her (Cursor P3). En
        # zip bærer filtypen i `external_attr`, og uttrekket skjer i
        # containeren: en lenke inni en docx er samme klasse som en
        # lenke i bunten, og den ene gaten kan ikke være strengere enn
        # den andre uten at forskjellen er et hull.
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise Buntfeil("symlenke", f"{navn}/{info.filename}")
    infos = [i for i in alle if not i.is_dir()]
    utpakket = 0
    sett: set[str] = set()
    for info in infos:
        # Duplikatporten fra ytre gate gjelder også her (Cursor P2). En zip
        # kan bære to oppføringer med samme navn, og et medlemsoppslag på
        # navn treffer navnekartet, som bare husker den SISTE — to
        # `word/document.xml` betyr at det uttrekket leser ikke er det
        # samme dokumentet gaten målte. Hvilken tekst som evalueres er
        # ikke et sted for stillhet, hverken ute eller inne.
        if info.filename in sett:
            raise Buntfeil("duplikat_medlem", f"{navn}/{info.filename}")
        sett.add(info.filename)
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
    with _apne_katalog(sti) as zf:
        # ÉN teller for hele bunten, nøstede docx-medlemmer inkludert
        # (Codex P1): budsjettet starter på buntens egne oppføringer og
        # forbrukes videre av hver docx, aldri nullstilt per arkiv. Det
        # er KATALOGOPPFØRINGENE som telles, ikke de filtrerte
        # medlemmene — ellers finansierte hver mappe i den ytre bunten
        # et indre docx-medlem gratis (Codex P2).
        filer = len(zf.infolist())
        # Manifestet er DEKLARASJON, ikke søknadsinnhold (#161): det
        # leses av `les_manifest`, aldri av innholdsstrømmen — teller
        # hverken som fremdrift eller tekst.
        innhold = [m for m in medlemmer if m.navn != MANIFESTNAVN]
        for nr, medlem in enumerate(innhold, start=1):
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
            data = b"".join(biter)
            # ET ARKIV KJENNES PÅ HALEN, IKKE PÅ HODET (Codex P2).
            # `nostet_arkiv`-porten hadde to armer, og begge målte en
            # BEGYNNELSE: endelsen i gaten, og `_ARKIVMAGI` mot hodet —
            # sistnevnte inne i HTML-grenen, fordi HTML er den eneste
            # endelsen uten egen magi. En zip identifiseres derimot av
            # sentralkatalogen SIST i fila, så et medlem kan tilfredsstille
            # `%PDF` i byte 0 og likevel være et komplett arkiv: en PDF med
            # påhengt EOCD passerte innholdstypeporten, og de indre
            # oppføringene ble aldri målt mot fil-, forholds- eller
            # totalbudsjettet.
            #
            # Testen er derfor hele medlemmet, ikke et prefiks, og den er
            # et EKTE oppslag: `is_zipfile` leter opp sentralkatalogen
            # (K4/SP-13 — aldri en signaturliste som gjetter på formatet).
            # Den AVVISER; den inspiserer ikke. DOCX er unntatt fordi det
            # ER en zip, og den har sin egen dør i `_inspiser_docx`.
            if (_endelse(medlem.navn) != ".docx"
                    and zipfile.is_zipfile(io.BytesIO(data))):
                raise Buntfeil("nostet_arkiv", medlem.navn)
            if _endelse(medlem.navn) == ".docx":
                utpakket, indre = _inspiser_docx(
                    medlem.navn, data,
                    filer_brukt=filer, byte_brukt=total)
                total += utpakket
                filer += indre
            if nr % porsjon == 0 or nr == len(innhold):
                fremdrift = {"filer_lest": nr,
                             "filer_totalt": len(innhold),
                             "byte_lest": total}
            else:
                fremdrift = None
            yield fremdrift, medlem, data


def les_manifest(sti: str | Path,
                 medlemmer: list[Medlem]) -> dict[str, str]:
    """#161 (eiers B): les og bind `soknader.json` mot katalogen, BEGGE
    veier, før én byte søknadsinnhold pakkes ut.

    -> {medlemsnavn: kandidat_id} for hvert innholdsmedlem.

    Lukket form: toppobjekt med NØYAKTIG nøkkelen `soknader`, en liste
    (1–MAKS_KANDIDATER) av objekter med NØYAKTIG `kandidat_id` (ikke-tom
    tekst, unik) og `filer` (ikke-tom liste av tekst, globalt unike).
    Alt annet — ukjente nøkler, feil typer, duplikater — er
    `manifest_feilformet`: en deklarasjon vi ikke forstår fullt ut er
    ingen deklarasjon.

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
    # At oversettelsen nå står to steder er den kjente divergensrisikoen
    # (`_inspiser_docx`, ni runder): den konsolideres når #155 gjør gaten
    # til én strømmende vei brukt rekursivt — ikke som ny maskin i en
    # fiksrunde (K1).
    try:
        with _apne_katalog(sti) as zf:
            raa = zf.read(MANIFESTNAVN)
    except KeyError as feil:
        # …OG FRAVÆRET MÅLES DER UTTREKKET SKJER (Cursor P1, runde 2).
        # Linja over slår opp `MANIFESTNAVN` i `medlemmer` — en katalog
        # ANDRE leste — mens `zf.read` åpner arkivet PÅ NYTT og slår opp i
        # sitt eget navnekart. Divergerer de to (fila byttet i vinduet
        # mellom `inspiser_bunt` og her, eller en `medlemmer`-liste som
        # ikke kom fra denne bunten), reiser `zipfile` en `KeyError` som
        # gikk rå til `kjor_bunt`s catch-all: `modellfeil` om en bunt
        # modellen aldri fikk se. Samme klasse som `kart.get` i
        # `kjoring.py` — en garanti målt ett sted er ingen garanti det
        # andre. En deklarasjon vi ikke finner, ER `manifest_mangler`.
        raise Buntfeil("manifest_mangler", MANIFESTNAVN) from feil
    except zipfile.BadZipFile as feil:
        raise Buntfeil("korrupt_bunt", f"{MANIFESTNAVN}: {feil}") from feil
    except (zlib.error, lzma.LZMAError) as feil:
        raise Buntfeil("korrupt_bunt",
                       f"{MANIFESTNAVN}: {type(feil).__name__}") from feil
    except OSError as feil:
        if feil.errno is not None:
            raise
        raise Buntfeil("korrupt_bunt",
                       f"{MANIFESTNAVN}: {type(feil).__name__}") from feil
    except (RuntimeError, NotImplementedError) as feil:
        raise Buntfeil("uleselig_medlem",
                       f"{MANIFESTNAVN}: {type(feil).__name__}") from feil
    try:
        data = json.loads(raa.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as feil:
        raise Buntfeil("manifest_feilformet", "uleselig json") from feil
    if not isinstance(data, dict) or set(data) != {"soknader"} \
            or not isinstance(data["soknader"], list):
        raise Buntfeil("manifest_feilformet", "lukket form")
    soknader = data["soknader"]
    if not 1 <= len(soknader) <= MAKS_KANDIDATER:
        raise Buntfeil("manifest_feilformet",
                       f"kandidattall {len(soknader)}")
    kart: dict[str, str] = {}
    sett_kandidater: set[str] = set()
    for rad in soknader:
        if not isinstance(rad, dict) or set(rad) != {"kandidat_id",
                                                     "filer"}:
            raise Buntfeil("manifest_feilformet", "lukket kandidatform")
        kid, filer = rad["kandidat_id"], rad["filer"]
        if not isinstance(kid, str) or not kid.strip() \
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
    uadressert = navnene - set(kart) - {MANIFESTNAVN}
    if uadressert:
        raise Buntfeil("medlem_uadressert", sorted(uadressert)[0])
    return kart
