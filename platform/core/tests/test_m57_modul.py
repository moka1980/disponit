"""M-57-modulen: arkivgaten (portene 21–26), innhold og modell (13–17)
og 5000-taket (27).

Alle tester konstruerer egen tilstand — buntene bygges byte for byte i
tmp_path, og der en header kan LYVE, patches katalogen bevisst så begge
lagene måles: gaten mot deklarasjonen, strømmen mot de faktiske bytene.
"""
from __future__ import annotations

import collections
import errno
import io
import os
import struct
import types
import zipfile
from pathlib import Path

import pytest

from modules.m57_ats import blinding, evaluering, maler, parsing
from modules.m57_ats.evaluering import Biasmaaling
from oppdragskontrakt import (OPPDRAGSTYPER, bryter_feltkontrakten,
                              mangler_paakrevde, minimer)

ROT = Path(__file__).resolve().parents[3]
MODULROT = ROT / "platform/modules/m57_ats"


def _manifest(filer: list[tuple]) -> str:
    """#161: buntens deklarasjon, utledet av RIGGENS egen filliste —
    testen deklarerer (topmappe = kandidat, riggens etablerte form);
    produksjonen gjetter aldri. Poster uten mappeprefiks (angreps-/
    grensefixturer) adresseres som sin egen kandidat."""
    import json as _json
    kandidater: dict[str, list[str]] = {}
    for navn, _innhold, *_a in filer:
        if navn.endswith("/"):
            continue
        kid = navn.replace("\\", "/").split("/")[0]
        kandidater.setdefault(kid, []).append(navn)
    return _json.dumps({"soknader": [
        {"kandidat_id": k, "filer": v} for k, v in kandidater.items()]})


def _bunt(sti: Path, filer: list[tuple], *,
          metode: int = zipfile.ZIP_DEFLATED,
          manifest: str | None | bool = True, **zipkw) -> Path:
    arkiv = sti / "bunt.zip"
    with zipfile.ZipFile(arkiv, "w", metode, **zipkw) as zf:
        for navn, innhold, *attr in filer:
            info = zipfile.ZipInfo(navn)
            info.compress_type = metode
            if attr:
                info.external_attr = attr[0]
            zf.writestr(info, innhold)
        # #161: manifest=True → riggen deklarerer; manifest=<str> → rå
        # innhold (negativer); manifest=None → utelatt med vilje.
        if manifest is True:
            zf.writestr("soknader.json", _manifest(filer))
        elif isinstance(manifest, str):
            zf.writestr("soknader.json", manifest)
    return arkiv


def _skad_payload(arkiv: Path, navn: bytes) -> None:
    """Skader den KOMPRIMERTE strømmen til én oppføring, og bare den.
    Katalogen, hodene og de deklarerte lengdene står urørt — gaten går
    derfor gjennom, og skaden dukker først opp i dekompressoren."""
    data = bytearray(arkiv.read_bytes())
    i = data.find(b"PK\x03\x04")
    while i != -1:
        navnlengde, ekstra = struct.unpack_from("<HH", data, i + 26)
        if data[i + 30:i + 30 + navnlengde] == navn:
            start = i + 30 + navnlengde + ekstra
            slutt = start + struct.unpack_from("<I", data, i + 18)[0]
            # INNE i den komprimerte strømmen, ikke i halen: en skade helt
            # sist dekoder gjerne til søppel som først felles av CRC-en —
            # altså `BadZipFile`, den formen som ALT var håndtert. Skaden
            # som treffer dekompressoren selv, er den porten her måler.
            for n in range(start + 16, min(start + 112, slutt)):
                data[n] ^= 0xFF
            arkiv.write_bytes(bytes(data))
            return
        i = data.find(b"PK\x03\x04", i + 4)
    raise AssertionError(f"{navn!r} ikke blant de lokale hodene")


def _patch_katalog(raa: bytes, navn: bytes, ny_storrelse: int,
                   komprimert: int | None = None) -> bytes:
    """Skriver om `uncompressed size` (og valgfritt `compressed size`) i
    SENTRALKATALOGEN for én oppføring — katalogen er en PÅSTAND, og
    nettopp det skal gaten/strømmen skille på.

    Formen er BYTE-inn/BYTE-ut fordi løgnen skal kunne plantes på begge
    nivåer: i buntens katalog og i katalogen inni en docx. En egen kopi
    for det indre nivået ville vært riggens versjon av nøyaktig den
    divergensen #155 river ut av gaten."""
    data = bytearray(raa)
    sig = b"PK\x01\x02"
    i = data.find(sig)
    while i != -1:
        navnlengde = struct.unpack_from("<H", data, i + 28)[0]
        if data[i + 46:i + 46 + navnlengde] == navn:
            struct.pack_into("<I", data, i + 24, ny_storrelse)
            if komprimert is not None:
                struct.pack_into("<I", data, i + 20, komprimert)
            return bytes(data)
        i = data.find(sig, i + 4)
    raise AssertionError(f"{navn!r} ikke i sentralkatalogen")


def _patch_deklarert(arkiv: Path, navn: bytes, ny_storrelse: int,
                     komprimert: int | None = None) -> None:
    """`_patch_katalog` på en fil."""
    arkiv.write_bytes(_patch_katalog(arkiv.read_bytes(), navn,
                                     ny_storrelse, komprimert))


def _patch_kryptert(arkiv: Path) -> None:
    """Setter kryptertbiten (0x1) i BEGGE hodene — zipfile skriver aldri
    et passordbeskyttet arkiv selv, men leser gjerne et."""
    data = bytearray(arkiv.read_bytes())
    for sig, forskyvning in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = data.find(sig)
        while i != -1:
            flagg = struct.unpack_from("<H", data, i + forskyvning)[0]
            struct.pack_into("<H", data, i + forskyvning, flagg | 0x1)
            i = data.find(sig, i + 4)
    arkiv.write_bytes(bytes(data))


def _docx(indre: list[tuple[str, bytes]] | None = None, *,
          pakke: bool = True,
          metode: int = zipfile.ZIP_DEFLATED) -> bytes:
    """En EKTE docx — altså en OPC-pakke i en zip — bygget i minnet.
    DOCX er unntaket fra «ingen nøstede arkiver», og et unntak kan bare
    måles med den ekte formen.

    `pakke=True` legger på de obligatoriske pakkemedlemmene som ikke alt
    er oppgitt, slik at fixturen er en docx og ikke bare en zip med
    riktig endelse. `pakke=False` er for testene som måler nettopp den
    forskjellen. `metode=ZIP_STORED` er for testen der containerens
    størrelse skal være ≈ summen av de indre bytene."""
    medlemmer = list(indre or [("word/document.xml", b"<w:t>CV</w:t>")])
    if pakke:
        oppgitt = {medlem[0] for medlem in medlemmer}
        medlemmer = [(navn, b"<Types/>" if navn.endswith(".xml")
                      else b"")
                     for navn in sorted(parsing.DOCX_PAKKEMEDLEMMER
                                        - oppgitt)] + medlemmer
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", metode) as zf:
        for navn, innhold, *attr in medlemmer:
            info = zipfile.ZipInfo(navn)
            info.compress_type = metode
            if attr:
                info.external_attr = attr[0]
            zf.writestr(info, innhold)
    return buf.getvalue()


def _pdf(n: int = 64) -> bytes:
    return b"%PDF-1.7\n" + b"x" * n


def test_port22_sti_utenfor_bunten(tmp_path):
    for navn in ("../unnslapp.pdf", "/etc/unnslapp.pdf",
                 "mappe/../../unnslapp.pdf", "..\\unnslapp.pdf"):
        arkiv = _bunt(tmp_path, [(navn, _pdf())])
        with pytest.raises(parsing.Buntfeil) as e:
            parsing.inspiser_bunt(arkiv)
        assert e.value.kode == "sti_utenfor_bunten", navn
        arkiv.unlink()
    # Positiv kontroll: en lovlig undermappe-sti går.
    ok = _bunt(tmp_path, [("kandidat1/cv.pdf", _pdf())])
    assert [m.navn for m in parsing.inspiser_bunt(ok)] == \
        ["kandidat1/cv.pdf", "soknader.json"]
    ok.unlink()
    # Codex P2 (runde 20): en MAPPEOPPFØRING er også en oppføring. Porten
    # måler katalogen, ikke utvalget vi pakker ut — mappene ble filtrert
    # bort før navnet ble målt, så `../../unnslapp/` sto i katalogen til
    # en bunt både gaten og strømmen godtok.
    # MUTASJONEN SOM DREPER DENNE: flytt `_sjekk_navn` tilbake bak
    # `infos = [i for i in alle if not i.is_dir()]`.
    arkiv = _bunt(tmp_path, [("../../unnslapp/", b""),
                             ("cv.pdf", _pdf())])
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "sti_utenfor_bunten"
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "sti_utenfor_bunten"
    arkiv.unlink()
    # … og inni en docx, der rekkefølgen var den samme.
    docx = _docx([("../../unnslapp/", b""),
                  ("word/document.xml", b"<w:t>CV</w:t>")])
    arkiv = _bunt(tmp_path, [("cv.docx", docx)])
    parsing.inspiser_bunt(arkiv)       # ytre gate ser en lovlig fil
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "sti_utenfor_bunten"
    arkiv.unlink()
    # Positiv kontroll begge veier: en LOVLIG mappeoppføring er ikke et
    # funn, og den skal fortsatt ikke telle som en søknad.
    ok = _bunt(tmp_path, [("kandidat1/", b""), ("kandidat1/cv.pdf", _pdf())])
    assert [m.navn for m in parsing.inspiser_bunt(ok)] == \
        ["kandidat1/cv.pdf", "soknader.json"]


def test_port23_symlenke_avvises(tmp_path):
    arkiv = _bunt(tmp_path, [("lenke.pdf", b"/etc/passwd",
                              (0o120777 << 16))])
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "symlenke"
    arkiv.unlink()
    # Cursor P3: … og INNI en docx. Uttrekket skjer i containeren, så en
    # lenke i det indre arkivet er samme klasse som en i bunten; den ene
    # gaten kan ikke være strengere enn den andre uten at forskjellen er
    # et hull.
    # MUTASJONEN SOM DREPER DENNE: fjern symlenkelinjen i `_mal_docx`.
    docx = _docx([("word/document.xml", b"<w:t>CV</w:t>"),
                  ("word/lenke.xml", b"/etc/passwd", (0o120777 << 16))])
    arkiv = _bunt(tmp_path, [("cv.docx", docx)])
    parsing.inspiser_bunt(arkiv)       # ytre gate ser en lovlig fil
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "symlenke"
    arkiv.unlink()
    # Codex P2: STIEN FELLER FØR FILTYPEN, også for en MEDLEMSOPPFØRING.
    # `_sjekk_navn` ble utsatt til `_mal_medlem` for ikke-mapper, men
    # symlenketesten ble stående på alle oppføringer — en oppføring som
    # er BEGGE deler rapporterte da `symlenke` for en sti som aldri var
    # inne i bunten. Ute måles navnet på hver oppføring før filtypen;
    # inne skal koden være den samme.
    # MUTASJONEN SOM DREPER DENNE: flytt `_sjekk_navn` i `_mal_docx`
    # tilbake inn i `if info.is_dir()`-armen.
    docx = _docx([("word/document.xml", b"<w:t>CV</w:t>"),
                  ("../../escape.xml", b"/etc/passwd", (0o120777 << 16))])
    arkiv = _bunt(tmp_path, [("cv.docx", docx)])
    parsing.inspiser_bunt(arkiv)
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "sti_utenfor_bunten"


def test_port24_nostet_arkiv_avvises(tmp_path):
    # Deklarert som arkiv → felles i gaten …
    arkiv = _bunt(tmp_path, [("indre.zip", b"PK\x03\x04hva som helst")])
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "nostet_arkiv"
    arkiv.unlink()
    # … og et arkiv i HTML-KLÆR felles i strømmen, på magien.
    arkiv = _bunt(tmp_path, [("side.html", b"PK\x03\x04forkledd")])
    parsing.inspiser_bunt(arkiv)  # gaten ser bare navnet — og det er ok
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "nostet_arkiv"
    # DOCX-unntaket: PK-magi er PÅKREVD der (det ER en zip) — positiv
    # kontroll på at unntaket ikke ble en generell åpning.
    ok = _bunt(tmp_path, [("cv.docx", _docx())])
    assert len(list(parsing.les_porsjonsvis(ok))) == 1


def test_port24_polyglot_pdf_med_pahengt_arkiv(tmp_path):
    """Codex P2: begge armene av `nostet_arkiv`-porten målte en
    BEGYNNELSE — endelsen i gaten, `_ARKIVMAGI` mot hodet i strømmen. En
    zip identifiseres av sentralkatalogen SIST i fila, så en PDF som
    begynner med `%PDF` og bærer en påhengt EOCD passerte
    innholdstypeporten, og de indre oppføringene ble aldri målt mot
    fil-, forholds- eller totalbudsjettet."""
    indre = io.BytesIO()
    with zipfile.ZipFile(indre, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bombe.txt", b"A" * 100_000)
    polyglot = _pdf() + indre.getvalue()
    assert polyglot.startswith(b"%PDF")          # magiporten er fornøyd …
    assert zipfile.is_zipfile(io.BytesIO(polyglot))   # … og det er en zip

    arkiv = _bunt(tmp_path, [("cv.pdf", polyglot)])
    parsing.inspiser_bunt(arkiv)   # gaten ser en lovlig endelse
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "nostet_arkiv"
    arkiv.unlink()

    # Samme hale i HTML-klær: hodet er lovlig HTML, så hodearmen ser
    # ingenting — halen felles likevel.
    arkiv = _bunt(tmp_path, [("cv.html", b"<p>CV</p>" + indre.getvalue())])
    parsing.inspiser_bunt(arkiv)
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "nostet_arkiv"
    arkiv.unlink()

    # Positiv kontroll begge veier: en ærlig PDF og en ærlig docx går.
    # Docx er unntatt fordi den ER en zip og har sin egen dør.
    ok = _bunt(tmp_path, [("cv.pdf", _pdf()), ("vedlegg.docx", _docx())])
    assert len(list(parsing.les_porsjonsvis(ok))) == 2


def test_port24_docx_inspiseres_som_arkivet_den_er(tmp_path):
    """Codex P1: DOCX slapp gjennom på `PK`-magien og sin egen
    KOMPRIMERTE størrelse alene. En liten docx kan bære et indre medlem
    som pakker ut til gigabyte — den ytre bunten passerer hver eneste
    2 GB/100:1-sjekk, fordi den bare inneholder de alt komprimerte
    docx-bytene, og bomben møter først tekstuttrekket.

    Unntaket er at DOCX er en av de tre lovede innholdstypene, ikke at
    grensene slutter å gjelde inni den."""
    # 4 MB nuller pakker ~1000:1 inni docx-en; den ytre bunten ser bare
    # noen få komprimerte kilobyte og ville sagt ja.
    bombe = _bunt(tmp_path, [("cv.docx", _docx(
        [("word/document.xml", b"<w:t>" + b"\0" * (4 << 20))]))])
    assert parsing.inspiser_bunt(bombe)[0].storrelse < (1 << 20), \
        "forutsetningen: den ytre bunten ser en liten fil"
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(bombe))
    assert e.value.kode == "komprimeringsforhold"
    bombe.unlink()
    # Path traversal, ABSOLUTTE stier og nøstet arkiv INNI docx-en måles
    # med samme koder. De to absolutte formene er Codex P1: medlemsnavnet
    # ble validert som `f"{ytre}/{info.filename}"`, og da ble
    # `/tmp/escape.xml` til `cv.docx//tmp/escape.xml` og `C:/escape.xml`
    # til `cv.docx/C:/escape.xml` — begge passerte gaten, fordi hverken
    # «starter med /» eller «kolon i posisjon 1» traff den SAMMENSATTE
    # strengen. `..` overlevde prefikset og var derfor grønn hele tiden;
    # nettopp derfor er det de absolutte som feller mutasjonen.
    for indre, kode in (
            ([("../../unnslapp.xml", b"<x/>")], "sti_utenfor_bunten"),
            ([("/tmp/escape.xml", b"<x/>")], "sti_utenfor_bunten"),
            ([("C:/escape.xml", b"<x/>")], "sti_utenfor_bunten"),
            ([("word/indre.zip", b"<x/>")], "nostet_arkiv")):
        arkiv = _bunt(tmp_path, [("cv.docx", _docx(indre))])
        with pytest.raises(parsing.Buntfeil) as e:
            list(parsing.les_porsjonsvis(arkiv))
        assert e.value.kode == kode, indre
        arkiv.unlink()
    # ... og en «docx» som ikke er et arkiv i det hele tatt er ikke en
    # docx, uansett hvor mye den begynner på PK.
    falsk = _bunt(tmp_path, [("cv.docx", b"PK\x03\x04" + b"d" * 32)])
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(falsk))
    assert e.value.kode == "feil_innholdstype"


def test_port26_duplikat_medlem_inni_docx_avvises(tmp_path):
    """Cursor P2: ytre gate feller to like medlemsnavn, den indre gjorde
    ikke.

    Et medlemsoppslag på navn treffer navnekartet, som bare husker den
    SISTE oppføringen. To `word/document.xml` inni samme docx betyr at
    teksten uttrekket leser, ikke er den gaten målte — samme stillhetstap
    som i ytre zip, og hvilken søknadstekst som evalueres er ikke et sted
    for stillhet.

    MUTASJONEN SOM DREPER DENNE: fjern duplikatsjekken i
    `_mal_docx`."""
    duplikat = _docx([("word/document.xml", b"<w:t>en</w:t>"),
                      ("word/document.xml", b"<w:t>to</w:t>")])
    arkiv = _bunt(tmp_path, [("cv.docx", duplikat)])
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "duplikat_medlem"
    arkiv.unlink()
    # Positiv kontroll: to ULIKE medlemmer i samme docx er helt vanlig.
    ok = _bunt(tmp_path, [("cv.docx", _docx(
        [("word/document.xml", b"<w:t>en</w:t>"),
         ("word/styles.xml", b"<w:styles/>")]))])
    assert len(list(parsing.les_porsjonsvis(ok))) == 1


def test_port21_enkeltfilgrensen_gjelder_ogsa_inni_docx(tmp_path):
    """Codex P1: de tre grensene fanger ULIKE ting, og løkken inni docx-en
    målte bare to av dem.

    Et indre medlem på 26 MB som komprimerer moderat bryter hverken
    100:1 eller 2 GB, og den ytre docx-en blir noen hundre kilobyte —
    altså langt under 25 MB, så ytre gate ser en liten, lovlig fil.
    Nøyaktig den overdimensjonerte inputen enkeltfilgrensen finnes for å
    stoppe, nådde dermed tekstuttrekket.

    MUTASJONEN SOM DREPER DENNE: fjern `MAKS_ENKELTFIL`-linjen i
    `_mal_medlem`s lesesløyfe — forholds- og totalsjekken er grønn hele
    veien."""
    # 64 tilfeldige byte per 4 KB-blokk: deflate komprimerer nullene, men
    # ikke støyen, så forholdet lander godt under 100:1 mens medlemmet
    # pakker ut til mer enn 25 MB.
    blokk = 4096
    blokker = (parsing.MAKS_ENKELTFIL + (1 << 20)) // blokk
    data = b"".join(os.urandom(64) + b"\0" * (blokk - 64)
                    for _ in range(blokker))
    assert len(data) > parsing.MAKS_ENKELTFIL
    docx = _docx([("word/document.xml", data)])
    assert len(docx) < parsing.MAKS_ENKELTFIL, \
        "forutsetningen: den ytre docx-en er liten nok til å passere gaten"
    assert len(data) / len(docx) < parsing.MAKS_KOMPRIMERINGSFORHOLD, \
        "forutsetningen: forholdsporten sier ja"
    arkiv = _bunt(tmp_path, [("cv.docx", docx)])
    parsing.inspiser_bunt(arkiv)   # ytre gate ser en liten, lovlig fil
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "enkeltfil_for_stor"
    arkiv.unlink()
    # Positiv kontroll: samme form, ett medlem UNDER grensen, går gjennom.
    liten = _docx([("word/document.xml", data[:1 << 20])])
    ok = _bunt(tmp_path, [("cv.docx", liten)])
    assert len(list(parsing.les_porsjonsvis(ok))) == 1


def test_port22_filbudsjettet_er_buntens_ikke_per_docx(tmp_path):
    """Codex P1: `MAKS_FILER` ble målt på nytt inni HVER docx.

    Grensen er buntens harde tak på 20 000 filer. Med en teller som
    nullstilles per arkiv, passerte to docx-er à 12 000 indre medlemmer
    begge portene — 24 000 filer til uttrekket, i en bunt på et par
    hundre kilobyte. Budsjettet er derfor ÉN teller: buntens egne filer
    pluss hvert nøstet medlem, aldri et friskt sett per docx.

    MUTASJONEN SOM DREPER DENNE: gi `_mal_docx` et friskt `Budsjett()`
    i stedet for buntens."""
    halv = parsing.MAKS_FILER // 2 + 2000     # 12 000 hver, 24 000 til sammen
    fyll = [(f"word/f{n}.xml", b"") for n in range(halv)]
    docx = _docx([("word/document.xml", b"<w:t>x</w:t>")] + fyll)
    assert len(docx) < parsing.MAKS_ENKELTFIL
    arkiv = _bunt(tmp_path, [("a.docx", docx), ("b.docx", docx)])
    # Ytre gate ser to små, lovlige filer — hullet lå i den indre.
    parsing.inspiser_bunt(arkiv)
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "for_mange_filer"
    arkiv.unlink()
    # Positiv kontroll: samme to docx-er, men til sammen UNDER taket.
    smaa = _docx([("word/document.xml", b"<w:t>x</w:t>")] + fyll[:4000])
    ok = _bunt(tmp_path, [("a.docx", smaa), ("b.docx", smaa)])
    assert len(list(parsing.les_porsjonsvis(ok))) == 2


def test_port25_docx_er_en_pakke_ikke_bare_en_zip(tmp_path):
    """Codex P2: innholdstypeporten målte ENDELSEN og at zipen lot seg
    lese — ikke at det fantes et dokument i den.

    En zip med ett medlem `ikke-et-dokument.txt` passerte derfor gaten
    og ble sendt videre til uttrekket, der de manglende OPC-delene ble
    en sen, rå uttrekksfeil i stedet for portens kodede
    `feil_innholdstype`.

    MUTASJONEN SOM DREPER DENNE: fjern `DOCX_PAKKEMEDLEMMER`-sjekken i
    `_mal_docx`."""
    for indre in ([("ikke-et-dokument.txt", b"hei")],
                  [("[Content_Types].xml", b"<Types/>")],
                  [("word/document.xml", b"<w:t>CV</w:t>")]):
        arkiv = _bunt(tmp_path, [("cv.docx", _docx(indre, pakke=False))])
        parsing.inspiser_bunt(arkiv)      # ytre gate ser en lovlig fil
        with pytest.raises(parsing.Buntfeil) as e:
            list(parsing.les_porsjonsvis(arkiv))
        assert e.value.kode == "feil_innholdstype", indre
        arkiv.unlink()
    # Positiv kontroll: den EKTE pakken går gjennom.
    ok = _bunt(tmp_path, [("cv.docx", _docx())])
    assert len(list(parsing.les_porsjonsvis(ok))) == 1


def test_port25_innholdstypen_er_en_paastand_begge_veier(tmp_path):
    # Ukjent endelse → gaten.
    arkiv = _bunt(tmp_path, [("skript.exe", b"MZ")])
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "ukjent_innholdstype"
    arkiv.unlink()
    # Lovlig endelse med feil innhold → strømmen (magien).
    arkiv = _bunt(tmp_path, [("cv.pdf", b"MZ ikke en pdf")])
    parsing.inspiser_bunt(arkiv)
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "feil_innholdstype"


def test_duplikate_medlemsnavn_avvises(tmp_path):
    """Codex P2: en zip KAN bære to oppføringer med samme navn, og
    `zf.open(navn)` slår opp i navnekartet — som bare husker den siste.
    To ulike `cv.html` ble derfor lest som den samme, to ganger, og den
    første søknaden forsvant i stillhet. Hvilke søknader som evalueres er
    ikke et sted for stillhet."""
    import warnings
    with warnings.catch_warnings():          # zipfile advarer selv om at
        warnings.simplefilter("ignore")      # navnet er duplisert — det
        arkiv = _bunt(tmp_path,              # er nettopp poenget her.
                      [("cv.html", b"<p>soker A</p>"),
                       ("cv.html", b"<p>soker B</p>")])
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "duplikat_medlem"
    arkiv.unlink()
    # Positiv kontroll: samme filnavn i ULIKE mapper er to forskjellige
    # medlemmer, og begge leses.
    ok = _bunt(tmp_path, [("a/cv.html", b"<p>A</p>"),
                          ("b/cv.html", b"<p>B</p>")])
    assert [d for _, _, d in parsing.les_porsjonsvis(ok)] == \
        [b"<p>A</p>", b"<p>B</p>"]


def test_port25_html_er_en_form_ikke_en_denyliste(tmp_path):
    """Codex P2: innholdsporten for HTML var en denyliste på åtte byte —
    og `%PDF` sto ikke i den, tross kommentaren som lovet det. En PDF
    omdøpt til `cv.html` gikk rett gjennom, og det samme gjorde et tomt
    zip-arkiv (`PK\\x05\\x06`), som heller ikke sto i listen.

    Porten er nå positiv: et HTML-dokument begynner med et merke."""
    for innhold, kode in ((_pdf(), "feil_innholdstype"),
                          (b"MZ kjorbar", "feil_innholdstype"),
                          (b"ren tekst uten merke", "feil_innholdstype"),
                          (b"", "feil_innholdstype"),
                          (b"PK\x05\x06" + b"\0" * 18, "nostet_arkiv"),
                          (b"PK\x07\x08tull", "nostet_arkiv"),
                          (b"\x1f\x8bgzip", "nostet_arkiv")):
        arkiv = _bunt(tmp_path, [("cv.html", innhold)])
        parsing.inspiser_bunt(arkiv)   # gaten ser bare navnet
        with pytest.raises(parsing.Buntfeil) as e:
            list(parsing.les_porsjonsvis(arkiv))
        assert e.value.kode == kode, innhold[:8]
        arkiv.unlink()
    # Positiv kontroll: de lovlige formene går — også med BOM, innledende
    # blanke og en doctype.
    #
    # Codex P2: hodet må være langt nok til at formen FINNES i det. Med
    # åtte byte inneholdt hodet ingen `<` for et dokument som begynte med
    # en BOM og et par linjeskift, eller med litt innrykk — fullt gyldig
    # HTML ble avvist som feil innholdstype. Hodet er fortsatt BUNDET:
    # porten leser et hode, aldri filen.
    for innhold in (b"<p>ok</p>", b"\xef\xbb\xbf<html></html>",
                    b"\n  <!DOCTYPE html>\n<html></html>",
                    b"<!-- kommentar --><html></html>",
                    b"\xef\xbb\xbf\r\n\r\n\r\n\r\n<html></html>",
                    b" " * 64 + b"<html></html>",
                    b"\n" * 400 + b"<html></html>"):
        arkiv = _bunt(tmp_path, [("cv.html", innhold)])
        assert len(list(parsing.les_porsjonsvis(arkiv))) == 1, innhold[:8]
        arkiv.unlink()


def test_port26_filantall(tmp_path):
    # Grensen måles VED grensen: nøyaktig taket går, én over felles —
    # med tomme oppføringer så testen er billig og fortsatt ekte.
    mange = [(f"k/{n}.html", b"<p>x</p>") for n in range(parsing.MAKS_FILER)]
    over = mange + [("k/en-til.html", b"<p>x</p>")]
    arkiv = _bunt(tmp_path, over)
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "for_mange_filer"


def test_port26_mapper_teller_i_filbudsjettet(tmp_path):
    """Codex P2: mappene ble filtrert bort FØR `MAKS_FILER` ble målt.

    Grensen bevokter arbeidet katalogen påfører oss — minnet og
    parsetiden i `infolist()` — og det arbeidet er alt gjort når
    filtreringen skjer. En bunt med 20 001 tomme mappeoppføringer og én
    HTML-søknad passerte derfor et budsjett den brøt med 20 001
    oppføringer.

    MUTASJONEN SOM DREPER DENNE: mål `len(infos)` (de filtrerte) i
    stedet for `len(alle)` i `inspiser_bunt`."""
    mapper = [(f"m{n}/", b"") for n in range(parsing.MAKS_FILER)]
    # manifest=None: porten måler KATALOGARBEIDET, og fixturet står
    # nøyaktig på taket — deklarasjonen er ikke det som måles her.
    arkiv = _bunt(tmp_path, mapper + [("cv.html", b"<p>x</p>")],
                  manifest=None)
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "for_mange_filer"
    arkiv.unlink()
    # Positiv kontroll: samme form, én oppføring under taket — mappene
    # er fortsatt ikke medlemmer, de er bare betalt for.
    faerre = mapper[:parsing.MAKS_FILER - 1]
    ok = _bunt(tmp_path, faerre + [("cv.html", b"<p>x</p>")],
               manifest=None)
    assert [m.navn for m in parsing.inspiser_bunt(ok)] == ["cv.html"]


def test_port26_mapper_i_docx_teller_i_samme_budsjett(tmp_path):
    """Samme hull i den INDRE gaten: mappeoppføringene inni en docx ble
    filtrert bort før `filer_brukt + len(...)` ble målt, og hver docx
    kunne dermed bære et ubegrenset antall katalogoppføringer forbi
    buntens ene teller.

    MUTASJONEN SOM DREPER DENNE: hopp over mappeoppføringene i
    `_mal_docx`s første løkke."""
    halv = parsing.MAKS_FILER // 2 + 2000     # 12 000 hver, 24 000 totalt
    fyll = [(f"word/m{n}/", b"") for n in range(halv)]
    docx = _docx([("word/document.xml", b"<w:t>x</w:t>")] + fyll)
    assert len(docx) < parsing.MAKS_ENKELTFIL
    arkiv = _bunt(tmp_path, [("a.docx", docx), ("b.docx", docx)])
    parsing.inspiser_bunt(arkiv)          # ytre gate ser to små filer
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "for_mange_filer"


def test_155_katalogen_inni_docx_er_en_paastand_ikke_bytene(tmp_path):
    """Runde 8, formen #155 ble skrevet for å felle.

    `_inspiser_docx` målte 25 MB, 100:1 og 2 GB på det den INDRE
    sentralkatalogen PÅSTOD (`file_size`/`compress_size`). Ingenting ble
    lest, og ingen CRC ble målt — så en patchet indre katalog kunne
    oppgi lav `file_size`, passere alle tre grensene, og la den
    faktiske ekspansjonen skje først i tekstuttrekket. Den ytre veien
    lærte dette i `les_porsjonsvis` for lenge siden; den indre kunne
    ikke lære det uten å bli den samme veien.

    Gaten leser nå strømmen med et hardt tak og teller det den FAKTISK
    fikk. Løgnen har da ingen steder å gjemme seg: enten leverer
    medlemmet bytene sine og felles på dem, eller så leverer det færre
    enn det påstår og felles som korrupt.

    MUTASJONEN SOM DREPER DENNE: la `_mal_medlem` måle
    `info.file_size` i stedet for de leste bytene."""
    stor = b"<w:t>" + b"\0" * (parsing.MAKS_ENKELTFIL + (1 << 20))
    docx = _docx([("word/document.xml", stor)])
    assert len(docx) < parsing.MAKS_ENKELTFIL, \
        "forutsetningen: den ytre docx-en er liten nok til å passere gaten"

    # Ærlig katalog: bytene selv feller medlemmet.
    aerlig = _bunt(tmp_path, [("cv.docx", docx)])
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(aerlig))
    assert e.value.kode == "enkeltfil_for_stor"
    aerlig.unlink()

    # Løgnaktig katalog: PÅSTANDEN er 1000 byte, altså grønt på alle tre
    # grensene den gamle indre gaten målte. Den nye leser, og et medlem
    # som ikke leverer det det påstår er en korrupt bunt — ikke en
    # godkjent søknad.
    logn = _patch_katalog(docx, b"word/document.xml", 1000)
    arkiv = _bunt(tmp_path, [("cv.docx", logn)])
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "korrupt_bunt"


def test_155_hver_oppforing_betaler_en_gang(tmp_path):
    """Ett budsjett er ikke det samme som én betaling.

    Da gaten begynte å telle sitt eget medlem, sto seedingen av
    `budsjett.filer` igjen på HELE den ytre katalogen — så hvert
    innholdsmedlem betalte to ganger, og taket på 20 000 slo inn ved
    drøyt 10 000. Det feiler lukket, og derfor stille: en ærlig bunt
    ble avvist for å være for stor, uten at noen port var brutt.

    MUTASJONEN SOM DREPER DENNE: seed `budsjett.filer` med
    `len(zf.infolist())` i stedet for oppføringene strømmen ikke selv
    måler."""
    antall = parsing.MAKS_FILER // 2 + 10      # 10 010 ærlige medlemmer
    filer = [(f"k{n}/cv.html", b"<p>x</p>") for n in range(antall)]
    arkiv = _bunt(tmp_path, filer)
    assert antall < parsing.MAKS_FILER, "forutsetningen: bunten er lovlig"
    assert len(list(parsing.les_porsjonsvis(arkiv))) == antall


def test_155_docx_byte_betales_av_bladene_ikke_containeren(
        tmp_path, monkeypatch):
    """Cursor P2, byte-siden av `..._betaler_en_gang`.

    `filer`-siden ble ryddet i runde 9; `byte`-siden sto igjen med
    nøyaktig samme form. `_mal_medlem` la containerens målte `lest` til
    totalen, og så betalte hvert indre medlem for de SAMME bytene en
    gang til. For `ZIP_STORED` er containeren ≈ summen av de indre
    bytene, så et docx-lag betalte omtrent DOBBELT mot klarsignalets
    «utpakket totalstørrelse | 2 GB» — og docstringen påsto samtidig
    «ingen dobbelttelling».

    Det feiler lukket, som slektningen sin: en ærlig bunt godt under
    taket avvises som `total_for_stor`, uten at noen port er brutt.
    Zip-bomben felles av den INDRE målingen — den som måler den
    faktiske ekspansjonen — ikke av det ytre tillegget.

    Grensen er skrudd ned her fordi det er FORMEN som måles, ikke
    tallet.

    MUTASJONEN SOM DREPER DENNE: gjør `budsjett.byte += lest`
    ubetinget igjen — da betaler containeren for barna sine, og den
    ærlige bunten under blir rød."""
    tekst = b"<w:t>" + os.urandom(4096).hex().encode() + b"</w:t>"
    docx = _docx([("word/document.xml", tekst)],
                 metode=zipfile.ZIP_STORED)
    with zipfile.ZipFile(io.BytesIO(docx)) as zf:
        indre = sum(i.file_size for i in zf.infolist() if not i.is_dir())
    assert indre < len(docx) < 2 * indre, \
        "forutsetningen: STORED gjør containeren ≈ summen av bladene"
    arkiv = _bunt(tmp_path, [("cv.docx", docx)])

    # Taket settes ETT byte under den dobbelte betalingen: bladene alene
    # har rikelig plass, container + blader har det ikke. Taket må
    # samtidig romme den ytre KATALOGens sum (docx-blob + manifest),
    # som `inspiser_bunt` måler for seg — og det gjør det med god margin
    # så lenge bladene er større enn manifestet.
    monkeypatch.setattr(parsing, "MAKS_TOTAL_UTPAKKET",
                        len(docx) + indre - 1)
    fasit = [f for f, _, _ in parsing.les_porsjonsvis(arkiv) if f][-1]
    assert fasit["byte_lest"] == indre, \
        "bare bladene betaler — containerens blob telles ikke i tillegg"
    arkiv.unlink()

    # SPEILET: taket gjelder fortsatt, og det er de INDRE bytene som
    # bærer det. En deflatert docx er liten utenpå og stor inni; her er
    # det bladet som sprenger taket, og bladet som navngis.
    stort = _docx([("word/document.xml", os.urandom(2048) * 40)])
    with zipfile.ZipFile(io.BytesIO(stort)) as zf:
        indre_stort = sum(i.file_size for i in zf.infolist()
                          if not i.is_dir())
    tak = (len(stort) + indre_stort) // 2
    assert len(stort) < tak < indre_stort, \
        "forutsetningen: containeren passerer, bladene gjør det ikke"
    arkiv2 = _bunt(tmp_path, [("cv.docx", stort)])
    monkeypatch.setattr(parsing, "MAKS_TOTAL_UTPAKKET", tak)
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv2))
    assert e.value.kode == "total_for_stor"
    assert e.value.args[0].endswith("cv.docx/word/document.xml")


def test_155_bytebudsjettet_er_buntens_ikke_per_docx(tmp_path, monkeypatch):
    """Cursor P2: speilet av `..._filbudsjettet_er_buntens_...` for BYTE.

    `#155`s kjerneinvariant er ETT `Budsjett` for begge feltene. `filer`-
    siden er låst mot en nullstilling per docx (port22); `byte`-siden var
    bare dekket INNEN én docx — dobbelttellingen container/blad — og
    ingen test målte at to docx-er deler den samme byte-telleren. En
    nullstilling der slipper to docx-er som hver for seg er lovlige, men
    som til sammen ekspanderer langt over taket, gjennom gaten, mens
    fil-testen fortsatt er grønn.

    Grensen er skrudd ned her fordi det er FORMEN som måles, ikke tallet.

    MUTASJONEN SOM DREPER DENNE: nullstill byte-siden i `_mal_docx`
    (`budsjett.byte = 0`, eller gi løkka `Budsjett(filer=budsjett.filer)`)
    — da betaler hver docx bare for seg selv, og bunten under slipper
    gjennom."""
    docx = _docx([("word/document.xml", os.urandom(2048) * 40)])
    with zipfile.ZipFile(io.BytesIO(docx)) as zf:
        indre = sum(i.file_size for i in zf.infolist() if not i.is_dir())
    # Taket rommer ÉN docx' blader med margin, men ikke to. Den ytre
    # katalogen (to deflaterte blober + manifestet) måles for seg mot
    # samme tak, og passerer med god margin.
    tak = indre + indre // 2
    arkiv = _bunt(tmp_path, [("a.docx", docx), ("b.docx", docx)])
    assert 2 * len(docx) < tak < 2 * indre, \
        "forutsetningen: hver docx alene er lovlig, to er det ikke"
    monkeypatch.setattr(parsing, "MAKS_TOTAL_UTPAKKET", tak)
    parsing.inspiser_bunt(arkiv)   # ytre gate ser to små, lovlige filer
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "total_for_stor"
    assert e.value.args[0].endswith("b.docx/word/document.xml"), \
        "den ANDRE docx-en sprenger taket — den første betalte alt sitt"
    arkiv.unlink()
    # Positiv kontroll: samme docx, samme tak, én av dem — går gjennom.
    # Det er summen som feller, ikke en for streng grense per docx.
    en = _bunt(tmp_path, [("a.docx", docx)])
    assert len(list(parsing.les_porsjonsvis(en))) == 1


def test_155_for_stor_docx_katalog_avvises_for_lesing(tmp_path, monkeypatch):
    """Codex P2: den inkrementelle tellingen er riktig, men for sen alene.

    En katalog som overskrider grensen med én oppføring ble ikke avvist
    før `_mal_medlem` hadde åpnet og pakket ut hver eneste foregående
    oppføring — altså opptil hele bytebudsjettet brukt på arbeid vi
    allerede visste var over taket. Antallet er kjent når `infolist()`
    er materialisert, og den fjernede implementasjonen sammenlignet
    nettopp der.

    Målingen er derfor ikke KODEN — den var riktig før også — men at
    ingen indre oppføring er LEST når den faller.

    MUTASJONEN SOM DREPER DENNE: fjern `budsjett.filer + len(alle) >
    MAKS_FILER`-porten før løkkene i `_mal_docx`; koden blir fortsatt
    `for_mange_filer`, men først etter elleve utpakkede medlemmer."""
    monkeypatch.setattr(parsing, "MAKS_FILER", 12)
    docx = _docx([(f"word/d{n}.xml", b"<w:t>" + b"x" * 512 + b"</w:t>")
                  for n in range(12)])
    arkiv = _bunt(tmp_path, [("cv.docx", docx)])

    ekte = parsing._mal_medlem
    lest: list[str] = []

    def spion(navn, aapne, **kw):
        if kw.get("dybde"):
            lest.append(navn)
        return ekte(navn, aapne, **kw)

    monkeypatch.setattr(parsing, "_mal_medlem", spion)
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "for_mange_filer"
    assert lest == [], \
        f"katalogen ble avvist, men først etter {len(lest)} utpakkede medlemmer"


def _spion_paa_indre(monkeypatch) -> list[str]:
    """Noterer hvilke INDRE docx-medlemmer som faktisk ble pakket ut."""
    ekte = parsing._mal_medlem
    lest: list[str] = []

    def spion(navn, aapne, **kw):
        if kw.get("dybde"):
            lest.append(navn)
        return ekte(navn, aapne, **kw)

    monkeypatch.setattr(parsing, "_mal_medlem", spion)
    return lest


def test_155_ugyldig_docx_katalog_avvises_for_lesing(tmp_path, monkeypatch):
    """Codex P2, runde 2: alt katalogen ALENE kan dømme, dømmes først.

    Antallet ble flyttet fram i forrige runde, men duplikatet, det
    manglende pakkemedlemmet og den forbudte endelsen sto igjen spredt
    rundt lesesløyfa. Alle tre er egenskaper ved NAVNENE i katalogen, ikke
    ved bytene — og alle tre ble likevel meldt først etter at hvert
    foregående medlem var pakket ut. En docx vi allerede visste var
    ugyldig kunne dermed bruke opptil hele bytebudsjettet på veien til
    sin egen avvisning.

    KODENE ER UENDRET — det er tidspunktet som måles. Derfor står det en
    tung `word/aaa.xml` FØRST i hver katalog: uten forhåndsdommen er den
    lest når feilen meldes.

    MUTASJONEN SOM DREPER DENNE: flytt duplikat-, pakkemedlem- eller
    endelsessjekken tilbake til (eller bak) lesesløyfa i `_mal_docx`."""
    # Ukomprimerbar med vilje: en `x` * 4096 hadde felt forholdsporten når
    # den ble lest, og da målte speilet den porten i stedet for lesingen.
    tung = ("word/aaa.xml",
            b"<w:t>" + os.urandom(4096).hex().encode() + b"</w:t>")
    for indre, pakke, kode in (
            # duplikat sist i katalogen
            ([tung, ("word/document.xml", b"<w:t>en</w:t>"),
              ("word/document.xml", b"<w:t>to</w:t>")], True,
             "duplikat_medlem"),
            # pakkemedlem som aldri kom
            ([tung, ("word/document.xml", b"<w:t>CV</w:t>")], False,
             "feil_innholdstype"),
            # nøstet arkiv bakerst
            ([tung, ("word/document.xml", b"<w:t>CV</w:t>"),
              ("word/indre.zip", b"PK\x03\x04hva som helst")], True,
             "nostet_arkiv"),
    ):
        arkiv = _bunt(tmp_path, [("cv.docx", _docx(indre, pakke=pakke))])
        lest = _spion_paa_indre(monkeypatch)
        with pytest.raises(parsing.Buntfeil) as e:
            list(parsing.les_porsjonsvis(arkiv))
        assert e.value.kode == kode
        assert lest == [], \
            f"{kode}: katalogen dømte, men {len(lest)} medlemmer var lest"
        arkiv.unlink()


def test_155_docx_i_docx_felles_av_dybdevakten(tmp_path):
    """Cursor P2: dybdevakten var det ENESTE som lukket docx-klassen, og
    ingen test bandt den.

    `.docx` er unntatt fra BEGGE de andre armene — endelsen står ikke i
    `ARKIVENDELSER`, og formporten leser `endelse != ".docx" and
    er_arkiv`. En docx inni en docx er derfor den ene nøstingen hverken
    navnet eller bytene feller; bare `if dybde` gjør det. Den forrige
    `word/indre.zip`-dekningen treffer endelsesarmen FØR lesing og sier
    ingenting om denne klassen.

    VAKTEN STÅR FØR LESINGEN (Cursor P2, runde 2). Sto den etter — der
    hele medlemmet er lest, budsjettert og kjørt gjennom magiporten —
    så avgjorde INNHOLDET klassen: en `word/nested.docx` med søppelbyte
    ble `feil_innholdstype`, ikke `nostet_arkiv`, og en ekte nøstet
    docx tvang ekspansjon opp mot 25 MB før den ble avvist. Nøstingen
    kjennes på NAVNET, som for enhver annen arkivendelse.

    MUTASJONEN SOM DREPER DENNE: fjern `endelse == ".docx" and dybde` i
    `_mal_medlem` — da rekurserer gaten videre ned i den indre docx-en i
    stedet for å avvise den. Flyttes vakten tilbake under magiporten,
    blir søppel-varianten under rød."""
    arkiv = _bunt(tmp_path, [("cv.docx", _docx([
        ("word/nested.docx", _docx())]))])   # ekte OPC-pakke, ikke bare PK
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "nostet_arkiv"
    assert e.value.args[0].endswith("cv.docx/word/nested.docx")
    arkiv.unlink()

    # Samme klasse, uten gyldig OPC: endelsen bærer nøstingen, og
    # bytene får aldri lov til å omklassifisere den.
    soppel = _bunt(tmp_path, [("cv.docx", _docx([
        ("word/nested.docx", b"not-a-docx")]))])
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(soppel))
    assert e.value.kode == "nostet_arkiv"
    assert e.value.args[0].endswith("cv.docx/word/nested.docx")
    soppel.unlink()

    # SPEILET: på dybde 0 er `.docx` en av de tre lovede typene, og da
    # er søppelbyte nettopp feil innholdstype — vakten har flyttet seg,
    # ikke vokst.
    ytre = _bunt(tmp_path, [("cv.docx", b"not-a-docx")])
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(ytre))
    assert e.value.kode == "feil_innholdstype"


def test_155_null_komprimert_i_indre_katalog_slipper_ikke_forbi(tmp_path):
    """Cursor P2: `komprimert <= 0` på strøm-/docx-veien hadde ingen
    negativ. `test_port21_null_komprimert_er_ikke_fritak` måler
    `inspiser_bunt`s KATALOGpåstand, og indre medlemmer går aldri der —
    de får `komprimert` fra `info.compress_size` i docx-ens egen katalog.

    Den løgnen er MÅLT her, og utfallet er ikke det man gjetter: `zipfile`
    begrenser lesingen til `compress_size`, så et medlem som påstår null
    leverer null byte — og CRC-en over det tomme avviker fra den
    deklarerte. Løgnen felles derfor som `korrupt_bunt`, et kodet SP-3-
    utfall, ikke som `komprimeringsforhold`. Det som betyr noe for porten
    er at den ALDRI slipper forbi: et medlem som ikke leverer det
    katalogen påstår, er en korrupt bunt — aldri en godkjent søknad.

    (Følgen for `komprimert <= 0`-armen på DENNE veien: `lest > 0` og
    `komprimert == 0` kan ikke opptre samtidig gjennom `zipfile`, så
    armen står som kontraktsvakt for `_mal_medlem`s egen signatur —
    ikke som en gren en bunt kan nå. Notert i PR-tråden.)

    MUTASJONEN SOM DREPER DENNE: fjern `lest > 0` i `_mal_medlem` — da
    felles det ÆRLIG tomme medlemmet under av `komprimert <= 0`, og
    positivkontrollen blir rød."""
    docx = _docx()
    ekte = zipfile.ZipFile(io.BytesIO(docx)).getinfo("word/document.xml")
    logn = _patch_katalog(docx, b"word/document.xml", ekte.file_size,
                          komprimert=0)
    arkiv = _bunt(tmp_path, [("cv.docx", logn)])
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "korrupt_bunt"
    arkiv.unlink()

    # POSITIV KONTROLL: en ÆRLIG tom fil inni docx-en har `compress_size
    # = 0` uten å lyve — null ut av null er ikke en bombe, og `lest > 0`
    # er det som skiller den fra løgnen over.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for navn in sorted(parsing.DOCX_PAKKEMEDLEMMER):
            zf.writestr(navn, b"<Types/>" if navn.endswith("].xml")
                        else b"<w:t>CV</w:t>")
        tom = zipfile.ZipInfo("word/tom.xml")
        tom.compress_type = zipfile.ZIP_STORED     # tom + STORED → 0 byte
        zf.writestr(tom, b"")
    med_tom = buf.getvalue()
    assert zipfile.ZipFile(io.BytesIO(med_tom)).getinfo(
        "word/tom.xml").compress_size == 0, \
        "forutsetningen: det ærlige medlemmet oppgir null komprimert"
    assert len(list(parsing.les_porsjonsvis(
        _bunt(tmp_path, [("cv.docx", med_tom)])))) == 1


def test_port21_komprimeringsforhold(tmp_path):
    # 4 MB nuller pakker ~1000:1 — langt over 100:1-taket.
    arkiv = _bunt(tmp_path, [("cv.pdf", b"%PDF" + b"\0" * (4 << 20))])
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "komprimeringsforhold"


def test_port21_null_komprimert_er_ikke_fritak(tmp_path):
    """Cursor P2: `if info.compress_size and ...` hoppet over hele
    forholdssjekken når katalogen påsto null komprimert størrelse. En
    deklarert stor fil med `compress_size = 0` er ikke ukomprimert — det
    er et uendelig forhold, og den formen slapp forbi taket."""
    arkiv = _bunt(tmp_path, [("cv.pdf", _pdf())])
    _patch_deklarert(arkiv, b"cv.pdf", 24 * 1024 * 1024, komprimert=0)
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "komprimeringsforhold"
    arkiv.unlink()
    # Positiv kontroll: en TOM fil er lovlig — null ut av null er ikke
    # en bombe, og porten skal ikke felle den.
    tom = _bunt(tmp_path, [("tom.html", b"")], manifest=None)
    assert [m.navn for m in parsing.inspiser_bunt(tom)] == ["tom.html"]


def test_port21_totalgrensen_leses_fra_katalogen(tmp_path):
    """91 filer deklarert til 24 MB hver (under både enkeltfil- og
    forholdstaket, kompresjonsfeltet patchet konsistent) summerer forbi
    2 GB — totalporten feller på KATALOGEN, før én byte pakkes ut."""
    filer = [(f"k/{n}.pdf", _pdf()) for n in range(91)]
    arkiv = _bunt(tmp_path, filer)
    for navn, _ in filer:
        _patch_deklarert(arkiv, navn.encode(), 24 * 1024 * 1024,
                         komprimert=12 * 1024 * 1024)
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "total_for_stor"


def test_port21_totalen_maales_ogsa_pa_et_medlem_uten_lesesloyfe(
        tmp_path, monkeypatch):
    """Codex P2: totalporten i strømmen sto INNE i lesesløyfa, og et
    medlem som får plass i førstelesingen kommer aldri inn i den.

    Katalogen bærer bare docx-ens KOMPRIMERTE størrelse, så en docx som
    pakker ut nær taket passerer den ytre gaten. Etterpå står totalen
    tett på grensen — og neste medlem, en HTML-fil på under 512 byte,
    ble lagt til uten at noen spurte: `f.read` gir tomt, `break` går,
    og `total += lest` var ubetinget. Én liten fil, eller mange,
    passerte dermed 2 GB-taket fritt.

    Grensen er skrudd ned her fordi det er FORMEN som måles, ikke
    tallet: å bygge 2 GB ekte byte i CI ville målt disken, ikke porten.

    FIXTUREN ER REBASERT (Cursor P2, runde 2): docx-en bærer nå
    KOMPRIMERBART innhold. Da containeren betalte for barna sine, var
    strømmens sum omtrent dobbelt så stor som katalogens, og et tak
    under strømsummen lå trygt over katalogsummen ved et uhell. Nå
    betaler bare bladene, og en ukomprimerbar docx gir strømsum <
    katalogsum — da feller `inspiser_bunt`s KATALOGport først, og
    strømporten denne testen finnes for blir aldri nådd. Komprimerbart
    innhold gjenoppretter forholdet med vilje, og forutsetningen er
    påstått under, ikke antatt.

    MUTASJONEN SOM DREPER DENNE: gjør `total += lest`-sjekken betinget
    igjen (eller fjern den) — sjekken inne i lesesløyfa er grønn hele
    veien, for den kjøres aldri for dette medlemmet."""
    docx = _docx([("word/media/bilde.bin", os.urandom(2048) * 40)])
    liten = b"<html><body>cv</body></html>"
    assert len(liten) < parsing._HODEBYTE, \
        "forutsetningen: medlemmet leses ferdig FØR lesesløyfa"
    arkiv = _bunt(tmp_path, [("a.docx", docx), ("liten.html", liten)])
    # Taket måles mot buntens EGEN sluttsum, ikke mot et gjettet tall:
    # strømmen rapporterer `byte_lest` på siste medlem, og nettopp den
    # summen er det taket ett byte under skal felle.
    fasit = [f for f, _, _ in parsing.les_porsjonsvis(arkiv) if f][-1]
    total = fasit["byte_lest"]
    katalog = sum(m.storrelse for m in parsing.inspiser_bunt(arkiv))
    assert katalog < total, \
        "forutsetningen: det er STRØMporten taket skal treffe, ikke katalogens"
    monkeypatch.setattr(parsing, "MAKS_TOTAL_UTPAKKET", total - 1)
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "total_for_stor"
    assert "liten.html" in e.value.args[0], \
        "det er medlemmet som sprengte taket som navngis"
    # Positiv kontroll: nøyaktig taket er nok — grensen er ikke strengere
    # enn den sier.
    monkeypatch.setattr(parsing, "MAKS_TOTAL_UTPAKKET", total)
    assert len(list(parsing.les_porsjonsvis(arkiv))) == 2


def _patch_katalogpost(arkiv: Path, navn: bytes, skade) -> None:
    """Skader SENTRALKATALOGENS post for én oppføring, og bare den.
    EOCD-halen står urørt — `is_zipfile` finner den og svarer JA, mens
    `ZipFile(...)` feller først når den parser posten. Nettopp det
    gapet er porten under."""
    data = bytearray(arkiv.read_bytes())
    sig = b"PK\x01\x02"
    i = data.find(sig)
    while i != -1:
        navnlengde = struct.unpack_from("<H", data, i + 28)[0]
        if data[i + 46:i + 46 + navnlengde] == navn:
            skade(data, i)
            arkiv.write_bytes(bytes(data))
            return
        i = data.find(sig, i + 4)
    raise AssertionError(f"{navn!r} ikke i sentralkatalogen")


def _katalogsignatur(data: bytearray, i: int) -> None:
    data[i + 3] = 0x09          # «Bad magic number for central directory»


def _katalogversjon(data: bytearray, i: int) -> None:
    struct.pack_into("<H", data, i + 6, 999)    # zip-versjon 99.9


def _katalognavn_lyver_utf8(data: bytearray, i: int) -> None:
    flagg = struct.unpack_from("<H", data, i + 8)[0]
    struct.pack_into("<H", data, i + 8, flagg | 0x800)   # «navnet er UTF-8»
    data[i + 46] = 0xFF                                  # … og det er det ikke


@pytest.mark.parametrize("skade, kode", [
    (_katalogsignatur, "korrupt_bunt"),
    (_katalognavn_lyver_utf8, "korrupt_bunt"),
    (_katalogversjon, "uleselig_medlem"),
])
def test_en_ulesbar_ytre_katalog_er_et_kodet_utfall(tmp_path, skade, kode):
    """Codex P2: `is_zipfile` leter opp EOCD-posten, den LESER ikke
    katalogen. En bunt med gyldig hale og en ødelagt post lenger inne
    passerer derfor `ikke_zip`-porten, og `ZipFile(...)` kaster en RÅ
    bibliotekfeil — utenfor håndteringen i `les_porsjonsvis`, som først
    begynner inne i medlemssløyfa. Kontrakten er et KODET utfall (SP-3),
    aldri en uventet arbeiderfeil, og porten måler BEGGE de ytre
    åpningsstedene: gaten og strømmen."""
    arkiv = _bunt(tmp_path, [("a.html", b"<p>a</p>"), ("b.html", b"<p>b</p>")],
                  manifest=None)
    assert len(parsing.inspiser_bunt(arkiv)) == 2   # positiv kontroll
    _patch_katalogpost(arkiv, b"b.html", skade)
    # Forutsetningen funnet hviler på: porten over sier fortsatt JA, så
    # `ikke_zip` fanger ikke dette.
    assert zipfile.is_zipfile(arkiv), "EOCD-halen er urørt"
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == kode
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == kode


def test_en_ulesbar_indre_docx_katalog_er_feil_innholdstype(tmp_path):
    """Samme dør, innsiden: et INDRE filnavn som påstår UTF-8 uten å
    være det, feller `ZipFile(...)` med en rå `ValueError` — den ene
    bibliotekformen `_mal_docx`s dør ikke kjente. En docx som ikke lar
    seg lese som arkiv er ikke en docx."""
    docx = bytearray(_docx())
    i = docx.index(b"PK\x01\x02")
    _katalognavn_lyver_utf8(docx, i)
    arkiv = _bunt(tmp_path, [("cv.docx", bytes(docx))])
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "feil_innholdstype"
    assert "cv.docx" in e.value.args[0]


def test_en_lognaktig_katalog_er_en_korrupt_bunt(tmp_path):
    """Katalogen KAN lyve — zipfile trunkerer da strømmen på den
    deklarerte lengden og feller CRC-en. Poenget porten eier: utfallet
    er en KODET avvisning (SP-3), aldri en rå zipfile-exception ut av
    generatoren, og aldri en stille, avkortet «suksess». (Strømmens egen
    bytemåling står i tillegg, for katalogformer der biblioteket ikke
    trunkerer — f.eks. zip64-avvik.)"""
    stor = b"%PDF" + b"\0" * 4096
    arkiv = _bunt(tmp_path, [("cv.pdf", stor)])
    _patch_deklarert(arkiv, b"cv.pdf", 1024, komprimert=None)
    parsing.inspiser_bunt(arkiv)  # gaten tror på katalogen — og går
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "korrupt_bunt"


@pytest.mark.parametrize("modul, metode", [
    ("zlib", zipfile.ZIP_DEFLATED),
    ("bz2", zipfile.ZIP_BZIP2),
    ("lzma", zipfile.ZIP_LZMA),
])
def test_dekompressorfeil_er_en_kodet_avvisning(tmp_path, modul, metode):
    """Codex P2: `zipfile` oversetter bare det IT selv oppdager. En skadet
    komprimert strøm feller biblioteket UNDER — `zlib.error`,
    `lzma.LZMAError`, eller bz2s errno-løse `OSError` — før CRC-en måles,
    og alle tre gikk forbi håndteringen. Katalogen er urørt her, så gaten
    går; skaden finnes bare i payloaden, og utfallet skal være kodet
    (SP-3), aldri en rå bibliotekfeil ut av generatoren."""
    pytest.importorskip(modul)
    # DELVIS komprimerbar med vilje (16 symboler ⇒ ~2:1, godt innenfor
    # 100:1-porten). Ren tilfeldig payload er ikke komprimerbar, og
    # DEFLATE legger den i STORED-blokker — en byte snudd der dekoder fint
    # og felles først av CRC-en, altså den formen som alt var håndtert.
    stor = b"%PDF-1.7\n" + bytes(b & 0x0F for b in os.urandom(300_000))
    arkiv = _bunt(tmp_path, [("cv.pdf", stor)], metode=metode)
    parsing.inspiser_bunt(arkiv)   # positiv kontroll: bunten er lesbar …
    assert len(list(parsing.les_porsjonsvis(arkiv))) == 1
    _skad_payload(arkiv, b"cv.pdf")
    parsing.inspiser_bunt(arkiv)   # … og katalogen lyver fortsatt ikke
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "korrupt_bunt"


def test_en_lesefeil_pa_disken_er_ikke_en_korrupt_bunt(tmp_path,
                                                      monkeypatch):
    """Baksiden av samme port: `OSError` MED errno er driftens feil, ikke
    buntens. Ble den fanget i samme arm, ville en disk- eller
    nettlagerfeil blitt til en avvisning av kundens leveranse — og den
    kunden hadde ingenting galt gjort. Bare den errno-løse formen
    dekompressoren kaster, er en korrupt bunt."""
    arkiv = _bunt(tmp_path, [("cv.pdf", _pdf())])
    ekte = zipfile.ZipExtFile.read

    def lesefeil(self, *a, **kw):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(zipfile.ZipExtFile, "read", lesefeil)
    with pytest.raises(OSError) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.errno == errno.EIO
    monkeypatch.setattr(zipfile.ZipExtFile, "read", ekte)
    assert len(list(parsing.les_porsjonsvis(arkiv))) == 1


def test_passordbeskyttet_medlem_er_en_kodet_avvisning(tmp_path):
    """Codex P2: et kryptert medlem passerer katalogen — grensene der er
    like målbare som ellers — men `zf.open` kaster `RuntimeError:
    password required`. Håndteringen fanget bare `BadZipFile`, så den rå
    exceptionen slapp ut av generatoren. Kontrakten er et KODET utfall
    (SP-3), aldri en bibliotekfeil."""
    arkiv = _bunt(tmp_path, [("cv.pdf", _pdf())])
    _patch_kryptert(arkiv)
    parsing.inspiser_bunt(arkiv)
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "uleselig_medlem"


def test_fremdriften_er_evidens(tmp_path):
    filer = [(f"k/{n}.html", b"<p>ok</p>") for n in range(5)]
    arkiv = _bunt(tmp_path, filer)
    ut = list(parsing.les_porsjonsvis(arkiv, porsjon=2))
    assert len(ut) == 5
    merker = [f for f, _, _ in ut if f]
    assert merker[-1] == {"filer_lest": 5, "filer_totalt": 5,
                          "byte_lest": sum(len(i[1]) for i in filer)}


def test_port13_ingen_vei_fra_modell_til_utsendingstekst():
    """Statisk (§6): malfila kjenner hverken evalueringen eller noe
    modellsymbol, og evalueringen kjenner ikke malene. Teksten i en
    utsendelse kan dermed bare komme fra malen + flettefeltene — og
    funn refereres med ID, aldri med modellens prosa.

    GRENSEN FOR HVA DENNE PORTEN BEVISER (Codex P2, #160): den måler
    IMPORTGRAFEN. Den kan si at de to modulfilene ikke når hverandre;
    den kan ikke se at en KALLER leser evalueringen og sender resultatet
    videre som `firmatekst`. Det feltet er i dag en fri streng, så §6s
    løfte er målt her og ikke i huset. Lukkingen er å binde feltet til
    kundeeid, lagret tekst (#160) — ikke å utvide denne AST-en, som
    aldri kan se en dataflyt den ikke har kildekoden til."""
    import ast
    maltre = ast.parse((MODULROT / "maler.py").read_text(encoding="utf-8"))
    evtre = ast.parse((MODULROT / "evaluering.py")
                      .read_text(encoding="utf-8"))

    def importerte(tre):
        ut = set()
        for node in ast.walk(tre):
            if isinstance(node, ast.Import):
                ut |= {a.name for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                ut |= {a.name for a in node.names}
                if node.module:
                    ut.add(node.module)
        return ut

    assert not importerte(maltre) & {"evaluering", "blinding"}, \
        "malene importerer evalueringssiden"
    assert not importerte(evtre) & {"maler"}, \
        "evalueringen når malene"
    kall = {n.func.attr if isinstance(n.func, ast.Attribute)
            else getattr(n.func, "id", None)
            for n in ast.walk(evtre) if isinstance(n, ast.Call)}
    assert "flett" not in kall
    for mal in maler.MALER.values():
        assert "funn_tekst" not in mal["felter"]


def test_port14_flettefelt_utenfor_malen():
    felter = {"stilling": "Utvikler", "kandidatnavn": "A",
              "tidsvalg_lenke": "https://x/t", "firmatekst": "Hilsen oss"}
    ut = maler.flett("invitasjon", felter)
    assert "Utvikler" in ut["tekst"] and ut["malversjon"] == "invitasjon-v1"
    with pytest.raises(maler.Malfeil) as e:
        maler.flett("invitasjon", felter | {"fritekst": "modellens prosa"})
    assert e.value.kode == "flettefelt_utenfor_malen"
    with pytest.raises(maler.Malfeil) as e:
        maler.flett("invitasjon", {k: v for k, v in felter.items()
                                   if k != "firmatekst"})
    assert e.value.kode == "flettefelt_mangler"
    # Ingen andreordens fletting: en verdi med {felt}-syntaks er avvist.
    with pytest.raises(maler.Malfeil) as e:
        maler.flett("invitasjon", felter | {"firmatekst": "{funn_id}"})
    assert e.value.kode == "ugyldig_feltverdi"
    # Codex P2: en TOM streng er et hull med riktig type. Invitasjonen
    # ber kandidaten velge tidspunkt «her:» og peker ingen steder;
    # avslaget lover en sporbar referanse som ikke finnes.
    for tomt in ("", "   ", "\n"):
        with pytest.raises(maler.Malfeil) as e:
            maler.flett("invitasjon", felter | {"tidsvalg_lenke": tomt})
        assert e.value.kode == "tomt_flettefelt"
        with pytest.raises(maler.Malfeil) as e:
            maler.flett("avslag", {"stilling": "Utvikler", "kandidatnavn": "A",
                                   "funn_id": tomt, "firmatekst": "Hilsen"})
        assert e.value.kode == "tomt_flettefelt"
    # `firmatekst` er kundens tone, og «ingen tone» er en ekte tilstand.
    assert maler.flett("invitasjon", felter | {"firmatekst": ""})["tekst"]


def test_port15_funn_uten_kildereferanse():
    tekst = "Jeg har ti års erfaring med drift."
    gyldig = {"kategori": "uklar_tidslinje",
              "kilde": {"start": 8, "slutt": 24,
                        "sitat": tekst[8:24]}}
    evaluering.valider_funn(gyldig, tekst)
    for funn in (
            {"kategori": "uklar_tidslinje"},
            {"kategori": "uklar_tidslinje", "kilde": {}},
            {"kategori": "uklar_tidslinje",
             "kilde": {"start": 0, "slutt": 4, "sitat": "feil"}},
            {"kategori": "pertentlighet",
             "kilde": gyldig["kilde"]}):
        with pytest.raises(evaluering.Evalueringsfeil):
            evaluering.valider_funn(funn, tekst)
    # Codex P2: Python-snitt klager ALDRI, så et sitat kan stemme uten at
    # offsetene er posisjoner i teksten. Negative indekser teller
    # bakfra, `slutt` utenfor lengden avkortes stille, og `False`/`True`
    # er lovlige int-er (bool er subklasse av int) som treffer tegn 0–1.
    # En mottaker som markerer stedet ville pekt feil, eller utenfor.
    bakfra = len(tekst) - 5
    for kilde in (
            {"start": bakfra - len(tekst), "slutt": len(tekst),
             "sitat": tekst[bakfra:]},
            {"start": 0, "slutt": len(tekst) + 99, "sitat": tekst},
            {"start": False, "slutt": True, "sitat": tekst[0]},
            {"start": 8, "slutt": 8, "sitat": ""},
            {"start": 24, "slutt": 8, "sitat": ""}):
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.valider_funn(
                {"kategori": "uklar_tidslinje", "kilde": kilde}, tekst)
        assert e.value.kode == "uten_kildereferanse", kilde
    # Positiv kontroll: hele teksten som sitat er en kanonisk referanse.
    evaluering.valider_funn(
        {"kategori": "uklar_tidslinje",
         "kilde": {"start": 0, "slutt": len(tekst), "sitat": tekst}}, tekst)
    # Kategoriene beskriver dokumentasjon, aldri person — settet er
    # lukket og karaktertrekk finnes ikke i det.
    for kategori in evaluering.FUNN_KATEGORIER:
        assert kategori in {
            "krav_ikke_dokumentert", "manglende_dokumentasjon",
            "motstridende_opplysning", "uklar_tidslinje",
            "utenfor_soknadsfrist"}


def test_port15_funnkontrakten_er_et_lukket_sett():
    """Codex P1/P2 (runde 5): kontrakten er SETTET, ikke de feltene noen
    kom på å måle — og porten bygger funnet den slipper gjennom.

    Et udeklarert felt (`karaktertrekk`) ble ikke sett av en validering
    som bare leste `kategori` og `kilde`, og fulgte likevel med rått inn
    i artefakten. En `kategori` modellen sendte som liste var uhashbar og
    sprengte `in frozenset` med en rå `TypeError` i stedet for modulens
    kodede utfall (SP-3)."""
    tekst = "Jeg har ti års erfaring med drift."
    kilde = {"start": 8, "slutt": 24, "sitat": tekst[8:24]}
    # Udeklarerte felter — på funnet og i kildereferansen.
    for funn, kode in (
            ({"kategori": "uklar_tidslinje", "kilde": kilde,
              "karaktertrekk": "pertentlig"}, "ukjent_funnfelt"),
            ({"kategori": "uklar_tidslinje"}, "ukjent_funnfelt"),
            ({"kilde": kilde}, "ukjent_funnfelt"),
            ({"kategori": "uklar_tidslinje", "kilde": kilde | {"vekt": 1}},
             "uten_kildereferanse")):
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.valider_funn(funn, tekst)
        assert e.value.kode == kode, funn
    # Uhashbar kategori: kodet avvisning, aldri TypeError.
    for kategori in ([], {}, set(), None, 7, True):
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.valider_funn(
                {"kategori": kategori, "kilde": kilde}, tekst)
        assert e.value.kode == "ukjent_kategori", kategori
    # Positiv kontroll: det KANONISKE funnet er returverdien.
    assert evaluering.valider_funn(
        {"kategori": "uklar_tidslinje", "kilde": dict(kilde)}, tekst) == {
            "kategori": "uklar_tidslinje", "kilde": kilde}


def test_port15_artefakten_baerer_det_kanoniske_funnet():
    """Speilbildet: et udeklarert felt kan hverken slippe umålt gjennom
    porten ELLER følge med videre — artefakten bærer funnet porten
    bygget, ikke modellens dict."""
    class _Slurvemodell(_Modell):
        def vurder(self, tekst, vekter):
            start = tekst.index("ti års")
            return {"funn": [{"kategori": "uklar_tidslinje",
                              "kilde": {"start": start,
                                        "slutt": start + 6,
                                        "sitat": "ti års"},
                              "karaktertrekk": "pertentlig"}],
                    "oppfylt": {k: True for k in vekter}}

    with pytest.raises(evaluering.Evalueringsfeil) as e:
        evaluering.evaluer_kandidat(
            _Slurvemodell(), "Kari har ti års erfaring.",
            {"navn": ["Kari"]}, {"drift": 1}, biasmaalinger=_MAALINGER)
    assert e.value.kode == "ukjent_funnfelt"


class _Modell:
    image_digest = "sha256:" + "a" * 64

    def __init__(self):
        self.sett: list[str] = []

    def vurder(self, tekst, vekter):
        self.sett.append(tekst)
        return {"funn": [], "oppfylt": {k: True for k in vekter}}


_MAALINGER = {_Modell.image_digest: Biasmaaling(
    _Modell.image_digest, "0" * 64, "2026-08-23T00:00:00+00:00")}


def test_port16_blindingen_maales_pa_faktisk_input():
    modell = _Modell()
    tekst = "Kari Nordmann, 44 år, søker. Kari kan drift."
    felter = {"navn": ["Kari Nordmann", "Kari"], "alder": ["44 år"]}
    ut = evaluering.evaluer_kandidat(
        modell, tekst, felter, {"drift": 3},
        biasmaalinger=_MAALINGER)
    assert modell.sett, "modellen ble aldri kalt"
    for klartekst in ("Kari", "44 år"):
        assert klartekst not in modell.sett[0]
    assert set(ut["avmaskering"].values()) == {"Kari Nordmann", "Kari",
                                               "44 år"}
    # Avskrudd blinding UTEN auditrad → input finnes ikke, modellen
    # kalles aldri (16b) …
    modell2 = _Modell()
    with pytest.raises(blinding.Blindingsfeil) as e:
        evaluering.evaluer_kandidat(
            modell2, tekst, felter, {"drift": 3},
            biasmaalinger=_MAALINGER, blinding_av=True)
    assert e.value.kode == "avskrudd_uten_auditrad"
    assert not modell2.sett
    # … og MED auditrad er avskruingen en auditert handling som gir
    # råteksten (den auditerte veien skal virke, ellers er porten bare
    # en av-knapp for hele funksjonen).
    ut2 = evaluering.evaluer_kandidat(
        modell2, tekst, felter, {"drift": 3},
        biasmaalinger=_MAALINGER, blinding_av=True,
        auditrad={"aktor": "eier@kunde", "ts": "2026-08-23T00:00:00Z",
                  "begrunnelse": "intern rekruttering"})
    assert modell2.sett[-1] == tekst and ut2["avmaskering"] == {}


def test_port16_blinding_uten_felter_feiler_lukket():
    """Codex P1 (eiers K2-avgjørelse): blindingen feilet ÅPENT.

    Med tomme eller manglende strukturerte felter blir `avmaskering` tom,
    og `krev_blindet` godkjenner VAKUØST — den har ingenting å lete
    etter. Råteksten gikk dermed til modellen mens kjøringen ble
    registrert som blindet, altså nøyaktig det motsatte av porten.
    Et umålt utfall er et avvist utfall (SP-3): modellen kalles aldri.

    MUTASJONEN SOM DREPER DENNE: fjern `if not avmaskering`-armen i
    `evalueringsinput`.

    GRENSEN MOT FORMPORTEN (eierdom, K2-kjennelse runde 4 på #217): kun
    den TOMME deklarasjonen (`{}`) er «ingenting å maskere». En
    deklarasjon som NEVNER et felt uten å gi det verdier — `{"navn": []}`
    eller `{"navn": [""]}` — er en ugyldig FORM, ikke et fravær, og
    `feltverdier_lukket` feller den før vi kommer hit. Begge er
    fail-closed og modellen kalles aldri; koden skiller bare hvilken port
    som felte.

    ÆRLIG OM DEKNINGEN: dette er det DEGENERERTE tilfellet. Det delvise
    (`navn` uten `adresse`) passerer fortsatt og venter på B-veien —
    målt eksplisitt i den siste asserten her, så ingen tror porten er
    sterkere enn den er."""
    tekst = "Kari Nordmann, 44 år, søker."
    modell = _Modell()
    with pytest.raises(blinding.Blindingsfeil) as e:
        evaluering.evaluer_kandidat(
            modell, tekst, {}, {"drift": 3}, biasmaalinger=_MAALINGER)
    assert e.value.kode == "blinding_uten_felter"
    assert not modell.sett
    for tomme in ({"navn": []}, {"navn": [""], "alder": ["44"]}):
        modell = _Modell()
        with pytest.raises(blinding.Blindingsfeil) as e:
            evaluering.evaluer_kandidat(
                modell, tekst, tomme, {"drift": 3},
                biasmaalinger=_MAALINGER)
        assert e.value.kode == "ugyldig_maskeringsform", tomme
        assert not modell.sett, tomme
    # Positiv kontroll: ett ekte felt, og veien er åpen som før.
    modell = _Modell()
    evaluering.evaluer_kandidat(
        modell, tekst, {"navn": ["Kari Nordmann"]}, {"drift": 3},
        biasmaalinger=_MAALINGER)
    assert modell.sett and "Kari Nordmann" not in modell.sett[0]
    # … og GRENSEN for hva porten lukker: `44 år` står igjen, fordi
    # `alder` ikke ble trukket ut. Det er den delvise lekkasjen B skal
    # dissolvere; den er ikke lukket her, og testen sier det høyt.
    assert "44 år" in modell.sett[0]


def test_port16_overlappende_verdier_maskeres_lengste_forst():
    """Codex P1: sekvensiell erstatning i FELTREKKEFØLGE lot en kortere
    verdi spise starten av en lengre, slik at resten sto igjen i
    modellinputen — og `krev_blindet` godtok det, fordi den måler hele
    klartekstverdier og den HELE verdien ikke lenger fantes.

    To former, begge målt: delvis navn (`Ola` før `Ola Nordmann`) og
    kontakt som starter med navnet (`Ann@example.com`)."""
    tekst = "Ola Nordmann søker. Kontakt: Ann@example.com. Hilsen Ola."
    felter = {"navn": ["Ola", "Ola Nordmann", "Ann"],
              "kontakt": ["Ann@example.com"]}
    blindet, avmaskering = blinding.blind(tekst, felter)
    for rest in ("Nordmann", "@example.com", "Ann", "Ola"):
        assert rest not in blindet, (rest, blindet)
    blinding.krev_blindet(blindet, avmaskering)
    # Tokennummereringen følger fortsatt feltrekkefølgen — den skal være
    # deterministisk for samme input, uavhengig av lengdesorteringen.
    assert avmaskering["[NAVN-1]"] == "Ola"
    assert avmaskering["[NAVN-2]"] == "Ola Nordmann"
    assert avmaskering["[KONTAKT-1]"] == "Ann@example.com"


def test_port16_feltformen_maales_ikke_paastas():
    """Codex P2: `blind` stolte på TYPEANNOTASJONEN til et uttrekk den
    ikke eier, og to velformede JSON-former slapp gjennom med hver sin
    skade.

    `{"navn": "Ann"}`: en streng er iterbar, så maskeringen fikk tegnene
    `A`, `n`, `n` som «verdier» — hver eneste `A` og `n` i HELE søknaden
    ble byttet ut, og `krev_blindet` godkjente det, fordi den leter etter
    nøyaktig de samme tegnene og de er borte. Modellen fikk altså korrupt
    tekst, og korrupt tekst kan endre både kravfunn og rangering.

    `{"alder": [42]}`: `re.escape(42)` er en rå `TypeError` ut av
    modulen — ikke et kodet blindingsavvik kalleren kan behandle.

    MUTASJONEN SOM DREPER DENNE: fjern formløkka i `blind`."""
    tekst = "Ann Nordmann planla en analyse av annonsen. Alder: 42."
    for ugyldig in ({"navn": "Ann"}, {"alder": [42]}, {"navn": [None]},
                    {"navn": {"Ann"}}, {"navn": {"a": "Ann"}},
                    {"navn": ["Ann"], "alder": [42]}):
        with pytest.raises(blinding.Blindingsfeil) as e:
            blinding.blind(tekst, ugyldig)
        assert e.value.kode == "ugyldig_maskeringsform", ugyldig
    # Den ENESTE veien til modellinput arver grensen, og modellen kalles
    # aldri på en form som ikke er målt.
    modell = _Modell()
    with pytest.raises(blinding.Blindingsfeil):
        evaluering.evaluer_kandidat(modell, tekst, {"navn": "Ann"},
                                    {"drift": 3}, biasmaalinger=_MAALINGER)
    assert not modell.sett
    # Positiv kontroll: kontrakten er en SEKVENS av strenger, og begge
    # sekvensformene går. (Verdien er `Kari`, ikke `Ann`, med vilje:
    # delstrengserstatningen ville truffet `ann` inni `Nordmann` også, og
    # det er den ANDRE grensen — #158 — ikke formen som måles her.)
    ren = "Kari Nordmann søker stillingen."
    for gyldig in ({"navn": ["Kari"]}, {"navn": ("Kari",)}):
        blindet, avmaskering = blinding.blind(ren, gyldig)
        assert blindet == "[NAVN-1] Nordmann søker stillingen.", gyldig
        assert avmaskering == {"[NAVN-1]": "Kari"}, gyldig


def test_avkortet_modellsvar_er_en_feil_ikke_et_tomt_resultat():
    """Codex P1: artefakten ble bygget med `.get(..., tom)` per felt, så
    `{}` — det et avbrutt eller lengdekuttet svar typisk er — ble en
    VELLYKKET evaluering med null funn, null oppfylte krav og null
    intervjuspørsmål. Kalleren kunne rangere og promotere den kandidaten
    som «oppfyller ingenting». Et avbrudd skal gi et rent feilutfall."""
    class _Avkortet(_Modell):
        def __init__(self, svar):
            super().__init__()
            self._svar = svar

        def vurder(self, tekst, vekter):
            self.sett.append(tekst)
            return self._svar

    vekter = {"drift": 3, "sikkerhet": 2}
    hele = {"funn": [], "oppfylt": {"drift": True, "sikkerhet": False}}
    for svar in (
            {},                                    # avkortet i sin helhet
            "ikke et objekt",
            {k: v for k, v in hele.items() if k != "oppfylt"},
            hele | {"funn": None},
            # Listen var målt, ELEMENTENE ikke (Cursor P2): `valider_funn`
            # leser funnet som en dict, så disse ga en rå `AttributeError`
            # ut av modulen i stedet for et kodet utfall (SP-3).
            hele | {"funn": [None]},
            hele | {"funn": ["tekst"]},
            hele | {"funn": [True]},
            # Et krav profilen har, men modellen ikke svarte på, ble
            # stille til null poeng — speilbildet av `ranger`s avvisning
            # av krav UTENFOR profilen.
            hele | {"oppfylt": {"drift": True}}):
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.evaluer_kandidat(
                _Avkortet(svar), "Kari søker.", {"navn": ["Kari"]},
                vekter, biasmaalinger=_MAALINGER)
        assert e.value.kode == "ufullstendig_modellsvar", svar
    # Oppfyllelsen er BOOLSK, og det måles der svaret LESES (Cursor P2).
    # `ranger` avviser `"false"` — den vanligste JSON-feilen en modell
    # gjør, og som streng er den sann — men den porten står lenger nede i
    # løypa enn artefakten: uten denne sjekken ble svaret bygget som en
    # vellykket evaluering, og feilen dukket opp ved rangeringen, eller
    # aldri, om kalleren lagrer artefakten først.
    for verdi in ("false", "true", 1, 0, None):
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.evaluer_kandidat(
                _Avkortet(hele | {"oppfylt": {"drift": verdi,
                                              "sikkerhet": False}}),
                "Kari søker.", {"navn": ["Kari"]}, vekter,
                biasmaalinger=_MAALINGER)
        assert e.value.kode == "ikke_boolsk_oppfyllelse", verdi
        assert "drift" in str(e.value)
    # Positiv kontroll: det HELE svaret går uendret gjennom — og
    # intervjuspørsmål er UTE av kontrakten (#225): et svar som likevel
    # bærer dem passerer, men artefakten bærer alltid tom liste —
    # spørsmål genereres ved innkalling, aldri under evalueringen.
    for svar in (hele, hele | {"intervjusporsmal": ["Fortell om drift."]}):
        ut = evaluering.evaluer_kandidat(
            _Avkortet(svar), "Kari søker.", {"navn": ["Kari"]},
            vekter, biasmaalinger=_MAALINGER)
        assert ut["oppfylt"] == {"drift": True, "sikkerhet": False}
        assert ut["intervjusporsmal"] == []


def test_port16_versalvarianter_maskeres_og_maales():
    """Codex P1: metadata og dokument er sjelden enige om versaler —
    feltet sier `Kari`, CV-overskriften skriver `KARI`. Både erstatningen
    og restsjekken var versalfølsomme, så navnet gikk umaskert til
    modellen OG porten sa god for det: den lette etter nøyaktig samme
    skrivemåte og fant den ikke."""
    tekst = ("KARI NORDMANN\nKari Nordmann søker. E-post:"
             " KARI@EXAMPLE.COM. Hilsen kari.")
    felter = {"navn": ["Kari Nordmann", "Kari"],
              "kontakt": ["kari@example.com"]}
    blindet, avmaskering = blinding.blind(tekst, felter)
    for rest in ("KARI", "Kari", "kari", "NORDMANN", "EXAMPLE.COM"):
        assert rest not in blindet, (rest, blindet)
    blinding.krev_blindet(blindet, avmaskering)
    # Avmaskeringen bærer den STRUKTURERTE skrivemåten: feltverdien er
    # kilden, dokumentets versaler er formatering.
    assert avmaskering["[NAVN-1]"] == "Kari Nordmann"
    assert avmaskering["[KONTAKT-1]"] == "kari@example.com"
    # Porten måler versaluavhengig også når blindingen er noen andres:
    # en «blindet» tekst der bare skrivemåten er endret, er ikke blindet.
    with pytest.raises(blinding.Blindingsfeil) as e:
        blinding.krev_blindet("Søkeren KARI NORDMANN er aktuell.",
                              {"[NAVN-1]": "Kari Nordmann"})
    assert e.value.kode == "maskert_felt_i_modellinput"


def test_kildereferansen_folger_teksten_den_indekserer():
    """Codex P2: blindingen ENDRER lengder («Kari» → `[NAVN-1]`), så en
    [start:slutt] validert mot den blindede teksten peker på noe annet i
    råsøknaden. Artefakten bærer derfor strengen offsetene hører til, og
    mottakeren kan verifisere sitatet med samme snitt som porten."""
    class _Sitatmodell(_Modell):
        def vurder(self, tekst, vekter):
            start = tekst.index("ti års")
            return {"funn": [{"kategori": "uklar_tidslinje",
                              "kilde": {"start": start,
                                        "slutt": start + 6,
                                        "sitat": "ti års"}}],
                    "oppfylt": {k: True for k in vekter}}

    raa = "Kari Nordmann har ti års erfaring."
    ut = evaluering.evaluer_kandidat(
        _Sitatmodell(), raa, {"navn": ["Kari Nordmann"]}, {"drift": 1},
        biasmaalinger=_MAALINGER)
    kilde = ut["funn"][0]["kilde"]
    assert ut["kildetekst"][kilde["start"]:kilde["slutt"]] == "ti års"
    # ... og nettopp forvekslingen porten finnes for: samme snitt i
    # råteksten treffer noe annet, fordi maskeringen forskjøv alt bak seg.
    assert raa[kilde["start"]:kilde["slutt"]] != "ti års"
    assert "Kari" not in ut["kildetekst"]


def test_port17_imagebytte_uten_biasmaaling_blokkerer():
    modell = _Modell()
    modell.image_digest = "sha256:" + "b" * 64  # nytt image, ingen måling
    with pytest.raises(evaluering.Evalueringsfeil) as e:
        evaluering.evaluer_kandidat(
            modell, "tekst", {}, {"drift": 1}, biasmaalinger=_MAALINGER)
    assert e.value.kode == "bias_maling_mangler_for_digest"
    assert not modell.sett, "modellen kjørte uten biasmåling"


def test_port17_en_oppforing_er_ikke_en_maaling():
    """Codex P2: porten sjekket bare at det LÅ noe under digesten og at
    objektet gjentok den. `Biasmaaling(digest, "", "")` — uten
    artefakthash og uten tidspunkt — passerte dermed som bevis, og
    akseptkravet kunne oppfylles med en plassholder."""
    digest = _Modell.image_digest
    for maaling, kode in (
            (Biasmaaling(digest, "", ""), "bias_maling_uten_artefakt"),
            (Biasmaaling(digest, "0" * 63, "2026-08-23T00:00:00+00:00"),
             "bias_maling_uten_artefakt"),
            (Biasmaaling(digest, "z" * 64, "2026-08-23T00:00:00+00:00"),
             "bias_maling_uten_artefakt"),
            (Biasmaaling(digest, "0" * 64, ""),
             "bias_maling_uten_tidspunkt"),
            (Biasmaaling(digest, "0" * 64, "i går"),
             "bias_maling_uten_tidspunkt")):
        modell = _Modell()
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.evaluer_kandidat(
                modell, "tekst", {}, {"drift": 1},
                biasmaalinger={digest: maaling})
        assert e.value.kode == kode, maaling
        assert not modell.sett, "modellen kjørte på en plassholdermåling"
    # ... og en digest som ikke er en digest er heller ikke en binding.
    falsk = "sha256:ikke-en-digest"
    with pytest.raises(evaluering.Evalueringsfeil) as e:
        evaluering.krev_biasmaaling(
            falsk, {falsk: Biasmaaling(falsk, "0" * 64,
                                       "2026-08-23T00:00:00+00:00")})
    assert e.value.kode == "bias_maling_ugyldig_digest"
    # Positiv kontroll: den ekte målingen slipper fortsatt gjennom.
    assert evaluering.krev_biasmaaling(digest, _MAALINGER).ts


def test_rangeringen_er_poeng_med_synlige_vekter():
    ut = evaluering.ranger(
        {"k2": {"drift": True, "sky": False},
         "k1": {"drift": True, "sky": True}},
        {"drift": 3, "sky": 2})
    assert [k["kandidat_id"] for k in ut] == ["k1", "k2"]
    assert ut[0]["poeng"] == 5 and ut[0]["nedbrytning"] == {"drift": 3,
                                                           "sky": 2}
    # Aldri prosent som målt egenskap — hverken som nøkkel eller verdi.
    assert all("prosent" not in k for k in ut[0])
    with pytest.raises(evaluering.Evalueringsfeil):
        evaluering.ranger({"k": {"ukjent_krav": True}}, {"drift": 1})
    # Codex P2, den andre retningen: kravsettet er PROFILENS. Et resultat
    # som mangler et krav ble stille lest som «ikke oppfylt» av `.get`, og
    # `ranger({"k": {}}, {"drift": 3})` var da en vellykket rangering med
    # null poeng. `_krev_helt_svar` måler dette for modellsvaret, men en
    # kaller som rangerer et lagret resultat går utenom den.
    for delvis, savnet in (({}, "drift,sky"), ({"sky": True}, "drift")):
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.ranger({"k": dict(delvis)}, {"drift": 3, "sky": 2})
        assert e.value.kode == "krav_mangler_i_resultatet", delvis
        # Meldingen navngir kravene som mangler, ikke bare at noe gjør det.
        assert savnet in str(e.value), delvis
    # Codex P2: modellutdata er ikke typesjekket, og `"false"` — den
    # vanligste JSON-feilen en modell gjør — er en SANN streng. Uten
    # typeporten fikk kandidaten hele vekten for et krav modellen sa nei
    # til, og rangeringen ble stille feil.
    for verdi in ("false", "true", 1, 0, None, [], {"a": 1}):
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.ranger({"k": {"drift": verdi}}, {"drift": 3})
        assert e.value.kode == "ikke_boolsk_oppfyllelse", verdi
    # Codex P2, samme klasse på VEKTSIDEN: `bool` er en subklasse av
    # `int`, så en profil som deserialiserte JSON-`true` som vekt fikk
    # vekten 1 — og `false` vekten 0. Rangeringen endret seg stille.
    for vekt in (True, False):
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.ranger({"k": {"drift": True}}, {"drift": vekt})
        assert e.value.kode == "ugyldige_vekter", vekt
    # Positiv kontroll: 0 er en lovlig vekt (et krav uten uttelling er
    # ikke en feil), og skal ikke felles av boolsk-porten.
    assert evaluering.ranger({"k": {"drift": True}},
                             {"drift": 0})[0]["poeng"] == 0
    # Codex P2 (runde 10): Python teller vilkårlig stort, men vekten og
    # nedbrytningen SKAL leses av et menneske foran en irreversibel
    # signering — og leseren er en `double`. Over det siste eksakte
    # heltallet er avrundingen alt skjedd i `JSON.parse`, så serverens
    # poengsum og den viste er to ulike tall på samme vekt.
    for vekt in (evaluering.VEKT_EKSAKT_MAKS + 1, 10 ** 400):
        with pytest.raises(evaluering.Evalueringsfeil) as e:
            evaluering.ranger({"k": {"drift": True}}, {"drift": vekt})
        assert e.value.kode == "ugyldige_vekter", vekt
    # Positiv kontroll: grensen er inklusiv, og poengsummen er vekten.
    assert evaluering.ranger(
        {"k": {"drift": True}},
        {"drift": evaluering.VEKT_EKSAKT_MAKS},
    )[0]["poeng"] == evaluering.VEKT_EKSAKT_MAKS


def test_port27_5001_avvises_ved_validering():
    payload = {"stillingsprofil_ref": "p-1@1",
               "stillingsprofil": {"profil_id": "p-1", "versjon": 1, "navn": "N",
                          "krav": [{"kravnavn": "K", "vekt": 3}]},
               "antall_soknader": 5000, "omfang": "bunt"}
    assert bryter_feltkontrakten("rekruttering.evaluering", payload) == []
    for antall in (5001, 0, -1):
        brudd = bryter_feltkontrakten(
            "rekruttering.evaluering", payload | {"antall_soknader": antall})
        assert brudd, antall
        assert any("antall_soknader" in b for b in brudd)
    # … og omfanget er en lukket enum: en ny verdi er en feil (og en
    # fristbeslutning), aldri stillhet.
    assert bryter_feltkontrakten(
        "rekruttering.evaluering", payload | {"omfang": "alt"})


def test_snapshoten_maa_vaere_referansens():
    """Codex P2 (runde 5 på #210): et velformet snapshot med en ANNEN
    profil/versjon enn `stillingsprofil_ref` lot utføreren evaluere mot
    én profil mens oppdraget og revisjonen navnga en annen. Paret må
    rekonstruere referansen nøyaktig — ellers droppes snapshoten og
    `mangler_paakrevde` feller payloaden."""
    snap = {"profil_id": "p-1", "versjon": 1, "navn": "N",
            "krav": [{"kravnavn": "K", "vekt": 3}]}
    basis = {"stillingsprofil_ref": "p-1@1", "stillingsprofil": snap,
             "antall_soknader": 1, "omfang": "bunt"}
    assert "stillingsprofil" in minimer("rekruttering.evaluering", basis)
    for gal_ref in ("p-1@2", "p-2@1", "art-1"):
        m = minimer("rekruttering.evaluering",
                    basis | {"stillingsprofil_ref": gal_ref})
        assert "stillingsprofil" not in m, gal_ref
        assert "stillingsprofil" in mangler_paakrevde(
            "rekruttering.evaluering", m)
    # …og `krav` som ikke er en LISTE droppes uten TypeError (Codex P2):
    # en skalar overlevde `or []` og ga 500 i claim-veien.
    for galt_krav in (5, True, "K", {"kravnavn": "K", "vekt": 3}):
        m = minimer("rekruttering.evaluering",
                    basis | {"stillingsprofil": {**snap,
                                                 "krav": galt_krav}})
        assert "stillingsprofil" not in m, galt_krav


def test_kundens_slettefrist_baeres_av_bestillingen():
    """Codex P1: fristvalget hadde ingen plass i det signerte oppdraget.

    057 sier «kundevalgt 30–365 døgn (standard 90)», men det lukkede
    feltsettet hadde ingen fristkolonne — så `minimer` strøk feltet, og
    `opprett_rekrutteringsprosess` fikk fristen som et kallerargument
    uten kilde i bestillingen. En kunde som avtalte 30 døgn fikk 90.
    """
    import json
    from pathlib import Path

    payload = {"stillingsprofil_ref": "p-1@1",
               "stillingsprofil": {"profil_id": "p-1", "versjon": 1, "navn": "N",
                          "krav": [{"kravnavn": "K", "vekt": 3}]},
               "antall_soknader": 10, "omfang": "bunt"}
    # Feltet OVERLEVER minimeringen (det var her det forsvant).
    minimert = minimer("rekruttering.evaluering",
                       payload | {"slettefrist_dogn": 30})
    assert minimert["slettefrist_dogn"] == 30
    # … og spennet er basens eget: `prosess_frist_i_spennet` (30–365).
    for lovlig in (30, 90, 365):
        assert bryter_feltkontrakten(
            "rekruttering.evaluering",
            payload | {"slettefrist_dogn": lovlig}) == [], lovlig
    for ulovlig in (29, 366, 0, -1, True, "90", 90.0):
        assert "slettefrist_dogn" in bryter_feltkontrakten(
            "rekruttering.evaluering",
            payload | {"slettefrist_dogn": ulovlig}), ulovlig
    # Fraværet ER standardvalget (basens `DEFAULT 90`), ikke et brudd:
    # feltet er valgfritt, og et oppdrag uten det er komplett.
    assert bryter_feltkontrakten("rekruttering.evaluering", payload) == []
    assert mangler_paakrevde(
        "rekruttering.evaluering",
        minimer("rekruttering.evaluering", payload)) == []
    # Modulens eget skjema må speile det lukkede settet: med
    # `additionalProperties: false` ville utføreren ellers avvist nettopp
    # den payloaden plattformen nå slipper gjennom.
    skjema = json.loads(
        (Path(__file__).resolve().parents[3]
         / "platform/modules/m57_ats/kontrakt/payload-skjema.json")
        .read_text(encoding="utf-8"))
    assert set(skjema["properties"]) == set(
        OPPDRAGSTYPER["rekruttering.evaluering"].felter)
    assert skjema["properties"]["slettefrist_dogn"] == {
        "type": "integer", "minimum": 30, "maximum": 365}


def test_artefaktreferansene_ma_vaere_strenger_ved_opprettelsen():
    """Codex P2: `minimer` bevarer skalarer som de er og
    `mangler_paakrevde` godtar enhver sann verdi, så
    `stillingsprofil_ref: 123` overlevde BEGGE og ble køet. Modulens
    payload-skjema krever `string, minLength 1` — men det kjører først
    når utførelsen har startet, altså etter at oppdraget var opprettet,
    claimet og talt. Referansen måles nå der bestillingen tas imot."""
    payload = {"stillingsprofil_ref": "p-1@1",
               "stillingsprofil": {"profil_id": "p-1", "versjon": 1, "navn": "N",
                          "krav": [{"kravnavn": "K", "vekt": 3}]},
               "antall_soknader": 10, "omfang": "bunt"}
    assert bryter_feltkontrakten("rekruttering.evaluering", payload) == []
    for felt in ("stillingsprofil_ref",):
        for verdi in (123, True, "", "   ", None, 4.5):
            brudd = bryter_feltkontrakten(
                "rekruttering.evaluering", payload | {felt: verdi})
            assert felt in brudd, (felt, verdi)


def test_den_kanoniske_handlingen_binder_oppdraget_til_eiermodulen():
    """Codex P1: prefikset bar et punktum den faktiske handlingen ikke har.

    Handlingen M-57-flyten faktisk bruker er NØYAKTIG
    `rekruttering.evaluering` — 057s prosessanker, 056s promoteringsvakt
    og SP-10-seeden skriver alle den strengen, uten suffiks. Prefikset
    `rekruttering.evaluering.` traff den derfor ikke, `type_for_handling`
    ga None, og `_eiermodul_for` skrev `eiermodul:ukjent` i raden. Siden
    `claim_neste_oppdrag` filtrerer på `oppdrag.eiermodul = modul_id`,
    ville modulen aldri sett sitt eget oppdrag.

    Testen måler KJEDEN, ikke strengen: fra handlingen til den id-en som
    havner i `eiermodul`-kolonnen ved opprettelsen.
    """
    import oppdragskontrakt as ok
    from m37.arbeider import _eiermodul_for

    t = ok.type_for_handling("rekruttering.evaluering")
    assert t is not None, "den kanoniske handlingen traff ingen oppdragstype"
    assert t.navn == "rekruttering.evaluering"
    assert _eiermodul_for("rekruttering.evaluering") == t.eiermodul
    assert not _eiermodul_for(
        "rekruttering.evaluering").startswith("eiermodul:")
    # … men treffet går på SEGMENTGRENSEN, ikke på tegn (Codex P2).
    # Handlings-ID-er i tenantpolicy er frie strenger, og et rent
    # `startswith` ga `rekruttering.evalueringmal` M-57s payloadkontrakt
    # og eiermodul i stedet for «ukjent».
    # MUTASJONEN SOM DREPER DENNE: bytt segmentregelen i
    # `type_for_handling` tilbake til `handling.startswith(p)`.
    for fremmed in ("rekruttering.evalueringmal",
                    "rekruttering.evalueringer",
                    "rekruttering.evaluering-2"):
        assert ok.type_for_handling(fremmed) is None, fremmed
    # Etterkommere under punktumet hører fortsatt til typen — det er den
    # samme regelen, ikke et unntak fra den.
    assert ok.type_for_handling(
        "rekruttering.evaluering.omkjoring") is t
    # Og de punktumbærende prefiksene er uendret.
    assert ok.type_for_handling("kontroll.wcag.nettsted").navn == \
        "kontroll.wcag.nettsted"
    assert ok.type_for_handling("verifiser.mva").navn == "verifikasjon"


def test_m57_har_EN_modulidentitet_i_kontrakt_migrasjon_og_artefakt():
    """Codex P2 / Cursor P1: `m_ats` mot `m57_ats` var en splitt.

    De FIRE stedene som må være enige om hvem som eier M-57-oppdragene:

    * kontrakten (`eiermodul` — det som skrives i raden ved opprettelsen
      og det `claim_neste_oppdrag` filtrerer på),
    * 056s CHECK + `opprett_frigivelsesoppdrag` (utsendingsarmen), og
    * akseptartefaktets `oppsett.modul` (hvem aksepten attesterer), og
    * modulmanifestets egen `id` (Cursor P1) — den fjerde kanonen porten
      IKKE målte, så `id: ats` sto uimotsagt ved siden av tre `m57_ats`.

    Var de uenige, kunne ingen modul claime BEGGE armene, og et
    skjemagyldig akseptartefakt ville attestert en annen identitet enn
    den som faktisk kjørte jobbene. Porten er statisk med vilje: den
    feller en splitt før noe kjøres.

    HVA MANIFEST-ARMEN FAKTISK MÅLER: `registry`-id-en og
    `auth.modul_id` er to forskjellige registre — det siste kommer fra
    `modultoken`/`modulhode`, registrert ved onboarding, ikke fra
    manifestet. M-56 kjører i produksjon med `id: wcag_audit` mot
    `eiermodul='m_wcag_audit'`, så et hus-vidt krav om likhet ville vært
    usant. Armen er derfor bundet til M-57s EGEN kanon: her skal de fire
    stemme, fordi det er den som skal leses av et menneske som
    registrerer modulen — og som alt har stavet den feil én gang.
    """
    import json

    import yaml

    import oppdragskontrakt as ok

    kjerne = Path(__file__).resolve().parents[1]
    kanonisk = ok.OPPDRAGSTYPER["rekruttering.evaluering"].eiermodul
    assert kanonisk == "m57_ats"

    skjema = json.loads(
        (kjerne / "artefakt-m57-skjema.json").read_text(encoding="utf-8"))
    assert (skjema["properties"]["oppsett"]["properties"]["modul"]["const"]
            == kanonisk)

    sql = (kjerne / "db/migrations/056_m57_utsending.sql").read_text(
        encoding="utf-8")
    assert f"eiermodul = '{kanonisk}'" in sql
    assert f"IS DISTINCT FROM '{kanonisk}'" in sql

    manifest = yaml.safe_load(
        (MODULROT / "manifest.yaml").read_text(encoding="utf-8"))
    assert manifest["id"] == kanonisk, (
        f"manifestet kaller modulen {manifest['id']!r}, kontrakten"
        f" {kanonisk!r} — den fjerde kanonen er uenig med de tre andre")
    # Og id-en er mappenavnet, som er `les_manifester` sitt eget fallback
    # når `id` mangler: da kan de to aldri komme fra hverandre i stillhet.
    assert manifest["id"] == MODULROT.name


def test_spolen_bevarer_uttrekkerens_linjeskiftbytes(tmp_path):
    """Codex P2 (#173): spolen oversatte `\\r\\n` og enslig `\\r` til `\\n`.

    `Path.write_text`/`read_text` gjør universell linjeskiftoversettelse
    når `newline` ikke settes. Spolen er ikke en logg — den er kilden til
    de EKSAKTE strengsammenligningene nedstrøms, og `lagre_dokument` har
    alt persistert uttrekkerens ORIGINALE tekst. Oversettelsen ga derfor
    to ulike sannheter om samme dokument.

    Testen måler begge følgene i én kjøring:

    1. En DEKLARERT verdi med internt `\\r\\n` — en flerlinjes adresse —
       traff den uttrukne teksten før spolingen og ikke etterpå. Da er
       deklarasjonen vakuøs, og `blinding.evalueringsinput` feller et
       fullstendig gyldig manifest som `ugyldig_maskeringsform`. At
       kjøringen fullfører er derfor selve porten.
    2. Bytene modellen ser: teksten bærer et `\\r\\n` UTENFOR de
       maskerte verdiene, og det skal stå igjen uendret.

    MUTASJONEN SOM DREPER DENNE: fjern `newline=""` fra lesningen i
    `_les_spole` (punkt 1 og 2 faller begge), eller fra skrivingen i
    `kjor_bunt` (faller på plattformer der `os.linesep != "\\n"`).
    """
    from modules.m57_ats import kjoring

    adresse = "Gate 1\r\nOslo"
    # `\r\n` både INNE i en deklarert verdi (punkt 1) og utenfor alle
    # deklarerte verdier (punkt 2) — de to måles hver for seg.
    tekst = f"Kandidat k1, {adresse}.\r\nKandidat k1 kan drift."
    arkiv = _bunt(tmp_path, [("k1/soknad.html", b"<p>irrelevant</p>")])
    felter = lambda m: {"navn": ["Kandidat k1"], "adresse": [adresse]}

    modell = _Modell()
    res = kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                            kandidatfelter_for=felter,
                            tekst_for=lambda m, d: tekst,
                            biasmaalinger=_MAALINGER, antall_soknader=1)

    assert [k["kandidat_id"] for k in res["rangering"]] == ["k1"]
    assert modell.sett, "modellen ble aldri kalt"
    assert "\r\n" in modell.sett[0], (
        "spolen oversatte linjeskiftene — modellen fikk ikke uttrekkerens"
        f" egne bytes: {modell.sett[0]!r}")
    # Og adressen ER faktisk maskert: uten dette kunne punkt 2 vært
    # grønt fordi blindingen aldri traff i det hele tatt.
    assert adresse not in modell.sett[0], modell.sett[0]


def test_deklarert_antall_bindes_til_buntens_kandidater(tmp_path):
    """Codex P1 på #210: `antall_soknader` er bestillingens signerte tall
    og ble aldri lest i kjøringen — deklarer 1, lever 2, og policyens
    arbeidsmengde-dom var forbigått. Avvik = kodet stopp, begge veier.

    Og etter #161 faller dommen FØR STRØMMEN: manifestet deklarerer
    kandidatene, så tallet måles mot det signerte uten at én byte innhold
    er pakket ut."""
    from modules.m57_ats import kjoring

    # Deklarasjonen STÅR i teksten: en deklarasjon som ikke treffer er
    # vakuøs og felles av `blind` (eierdom, K2-kjennelse runde 5, valg B).
    arkiv = _bunt(tmp_path, [
        ("k1/soknad.html", b"<p>Kandidat k1 vil ha drift hos k1</p>"),
        ("k2/soknad.html", b"<p>Kandidat k2 vil ha drift hos k2</p>"),
    ])
    felter = lambda m: {"navn": [f"Kandidat {m.navn.split('/')[0]}"]}
    uttrukket = []

    def uttrekk(m, d):
        uttrukket.append(m.navn)
        return d.decode("utf-8")

    for deklarert in (1, 3):
        uttrukket.clear()
        with pytest.raises(kjoring.Kjoringsfeil) as e:
            kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                              kandidatfelter_for=felter, tekst_for=uttrekk,
                              biasmaalinger=_MAALINGER,
                              antall_soknader=deklarert)
        assert e.value.kode == "kandidattall_avvik"
        assert e.value.fremdrift, "evidensen mangler i utfallet"
        # PORTEN ER FORAN STRØMMEN, IKKE I DEN (Cursor P2 på #161).
        # `len(uttrukket) <= 1` var runde 2s port på in-strøm-tellingen,
        # og den er AVLØST: #161 måler deklarert mot signert på
        # manifestet, før uttrekket i det hele tatt starter. Under den
        # gamle grensen slapp en regresjon som leser én fil før stopp
        # gjennom — og «deklarer 1, lever 20 000» skal ikke koste én fil
        # heller. Målingen er derfor null, begge veier.
        assert uttrukket == [], uttrukket
    # Positiv kontroll: riktig deklarasjon kjører helt igjennom.
    helt = kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                             kandidatfelter_for=felter, tekst_for=uttrekk,
                             biasmaalinger=_MAALINGER, antall_soknader=2)
    assert {k["kandidat_id"] for k in helt["rangering"]} == {"k1", "k2"}


def test_port28_avbrutt_kjoring_promoterer_ingenting(tmp_path):
    """SP-3-porten på hele kjøringen: en modell som dør på kandidat 2
    gir et KODET feilutfall med fremdrift som evidens — og ingen
    rangering, ingen artefakter, ikke noe delresultat å plukke fra.
    Positiv kontroll i samme test: samme bunt uten feilen gir helheten."""
    from modules.m57_ats import kjoring

    # «hos kN» står IGJEN etter maskeringen (det er «Kandidat kN» som er
    # deklarert), så modellen under kjenner fortsatt igjen kandidat 2 —
    # og deklarasjonen treffer, som `blind`s vakuøsitetsport krever.
    arkiv = _bunt(tmp_path, [
        ("k1/soknad.html", b"<p>Kandidat k1 vil ha drift hos k1</p>"),
        ("k2/soknad.html", b"<p>Kandidat k2 vil ha drift hos k2</p>"),
        ("k3/soknad.html", b"<p>Kandidat k3 vil ha drift hos k3</p>"),
    ])

    class _Doende(_Modell):
        def vurder(self, tekst, vekter):
            if "k2" in tekst:
                raise RuntimeError("container døde")
            return super().vurder(tekst, vekter)

    # Fail-closed-blindingen krever STRUKTURERTE felter per kandidat —
    # et tomt sett er sin egen kodede stopp (målt til slutt i testen).
    felter = lambda m: {"navn": [f"Kandidat {m.navn.split('/')[0]}"]}
    # Tekstuttrekket er containerens (§7/port 24) og INJISERES; her er
    # bunten ren HTML, så uttrekkeren er en dekoding.
    uttrekk = lambda m, d: d.decode("utf-8")

    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, _Doende(), vekter={"drift": 3},
                          kandidatfelter_for=felter, tekst_for=uttrekk,
                          biasmaalinger=_MAALINGER, antall_soknader=3)
    assert e.value.kode == "modellfeil"
    assert e.value.fremdrift, "fremdriften (evidensen) mangler i utfallet"
    # Feilutfallet KAN ikke bære et delresultat — målt på typen, ikke på
    # disiplin: Kjoringsfeil har ingen felter for kandidater eller lister.
    assert set(kjoring.Kjoringsfeil.__dataclass_fields__) == \
        {"kode", "fremdrift"}

    helt = kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                             kandidatfelter_for=felter, tekst_for=uttrekk,
                             biasmaalinger=_MAALINGER, antall_soknader=3)
    assert {k["kandidat_id"] for k in helt["rangering"]} == \
        {"k1", "k2", "k3"}
    assert helt["fremdrift"]["filer_lest"] == 3
    # …og fail-closed-blindingen gjennom kjøringen er et KODET utfall:
    # tomme strukturerte felter gir Kjoringsfeil, aldri rå Blindingsfeil
    # og aldri en kjøring som "gikk" med ublindet tekst.
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                          kandidatfelter_for=lambda m: {},
                          tekst_for=uttrekk,
                          biasmaalinger=_MAALINGER, antall_soknader=3)
    assert e.value.kode == "blinding_uten_felter"
    # RANGERINGEN er også innenfor utfallet (Codex P1): en ugyldig vekt
    # feller `evaluering.ranger` etter at hver kandidat er evaluert, og
    # den feilen skal ut som KODET Kjoringsfeil — ikke som rå
    # Evalueringsfeil bare fordi den kom fra siste steg.
    # MUTASJONEN SOM DREPER DENNE: flytt `ranger`-kallet ut av `try`.
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": True},
                          kandidatfelter_for=felter, tekst_for=uttrekk,
                          biasmaalinger=_MAALINGER, antall_soknader=3)
    assert e.value.kode == "ugyldige_vekter"


def test_flere_filer_under_samme_kandidat_blir_EN_evaluering(tmp_path):
    """Codex P1: siste medlem vant, og rekkefølgen avgjorde resultatet.

    En kandidatmappe rommer både CV og søknadsbrev. Med én evaluering per
    MEDLEM og `artefakter[kandidat_id] = resultat` overskrev filene
    hverandre: kvalifikasjonene i den første forsvant i stillhet, og
    hvilken som overlevde avhang av zip-medlemmenes rekkefølge.

    MUTASJONEN SOM DREPER DENNE: flytt `evaluer_kandidat` tilbake inn i
    lesesløyfa.
    """
    from modules.m57_ats import kjoring

    arkiv = _bunt(tmp_path, [
        ("k1/soknad.html", b"<p>Kari kan drift</p>"),
        ("k1/cv.html", b"<p>Kari har sertifisering</p>"),
        ("k2/soknad.html", b"<p>Ola kan drift</p>"),
    ])
    # Feltene er MEDLEMMETS: navnet står i søknadsbrevet, ikke i CV-en —
    # og blindingen gjelder likevel hele mappen.
    def felter(medlem):
        return ({"navn": ["Kari"]} if medlem.navn == "k1/soknad.html"
                else {"navn": ["Ola"]} if medlem.navn.startswith("k2")
                else {})

    modell = _Modell()
    ut = kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                           kandidatfelter_for=felter,
                           tekst_for=lambda m, d: d.decode("utf-8"),
                           biasmaalinger=_MAALINGER, antall_soknader=2)
    # Tre filer lest, TO kandidater evaluert — én evaluering per mappe.
    assert ut["fremdrift"]["filer_lest"] == 3
    assert set(ut["artefakter"]) == {"k1", "k2"}
    assert len(modell.sett) == 2
    k1 = next(t for t in modell.sett if "sertifisering" in t)
    # Begge filene er MED (ingen stille dropp) …
    assert "drift" in k1
    # … og navnet fra søknadsbrevet blinder også CV-ens forekomst.
    assert "Kari" not in k1


def test_tekstuttrekket_er_containerens_aldri_en_utf8_dekoding(tmp_path):
    """Codex P1: pdf og docx er BINÆRE — to av de tre lovede typene.

    `data.decode("utf-8", errors="replace")` returnerte alltid en streng,
    og strengen var nettopp derfor farlig: for en docx (komprimert
    OPC-pakke) og en pdf ga den U+FFFD-støy som modellen evaluerte som om
    det var en søknad. Uttrekket hører hjemme i den credential-frie
    containeren (§7/port 24), og kjøringen KREVER det inn — den gjetter
    aldri selv, og et uttrekk som feiler er et kodet utfall.

    MUTASJONEN SOM DREPER DENNE: gi `tekst_for` en default som dekoder
    `data` som UTF-8.
    """
    from modules.m57_ats import kjoring

    arkiv = _bunt(tmp_path, [("k1/soknad.html", b"<p>drift hos k1</p>")])
    felter = lambda m: {"navn": ["Kandidat k1"]}

    # Uten uttrekker finnes det ingen kjøring — argumentet er påkrevd.
    with pytest.raises(TypeError):
        kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                          kandidatfelter_for=felter,
                          biasmaalinger=_MAALINGER, antall_soknader=1)

    # Modellen ser NØYAKTIG det uttrekkeren ga — ikke bytene fra arkivet
    # — med blindingen som eneste bearbeiding på veien.
    modell = _Modell()
    kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                      kandidatfelter_for=felter,
                      tekst_for=lambda m, d: "uttrukket drift-tekst "
                                             "for Kandidat k1",
                      biasmaalinger=_MAALINGER, antall_soknader=1)
    assert modell.sett == ["uttrukket drift-tekst for [NAVN-1]"]

    # Et uttrekk som feiler (ødelagt pdf) er SP-3s kodede utfall, ikke en
    # rå bibliotekfeil ut av modulen …
    def _doende_uttrekk(medlem, data):
        raise ValueError("pdf-en er ødelagt")

    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                          kandidatfelter_for=felter,
                          tekst_for=_doende_uttrekk,
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "tekstuttrekk_feilet"
    # … og en uttrekker som gir tilbake bytene sine er samme feil: da
    # hadde modellen fått binærstøyen igjen, bare via en annen dør.
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                          kandidatfelter_for=felter,
                          tekst_for=lambda m, d: d,
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "tekstuttrekk_feilet"


def test_nullbyten_fra_uttrekket_naar_aldri_lagrene(tmp_path):
    """#173 (Codex P2): en nullbyte i uttrekket felte HELE evalueringen.

    PostgreSQL kan ikke lagre en nullbyte i `TEXT` eller `jsonb` i det
    hele tatt. Et uttrekk fra html eller pdf kan lovlig bære en — den
    passerer arkivgaten og uttrekket — og den felte først på INSERT, som
    en rå `psycopg.Error` API-et oversetter til `db_utilgjengelig`.
    `lever` leser 5xx som DRIFT, brenner hele retrykjeden mot en frisk
    base, og feller til slutt kjøringen som `kandidatlagring_feilet`,
    med en falsk infrastrukturalarm på veien. Én søknad med ett usynlig
    tegn tok altså ned buntens 4 999 andre.

    Rensingen står ved uttrekksgrensen, som er det ENE stedet fremmed
    uttrekkerkode kommer inn: både modellen, dokumentlageret og
    `kildetekst` i artefaktet ser da SAMME tekst. Testen måler begge
    veiene ut — modellen og dokumentsinken — for en rensing på bare den
    ene ville gitt to sannheter om samme søknad.

    Byten fjernes, den avvises ikke: den er ikke innhold. Ingen leser
    kan se den, uttrekket produserer den som kodingsartefakt, og
    «evidensen» den endrer er en byte som per konstruksjon ikke kunne
    vært lagret. Plattformdøren avviser den fortsatt
    (`request_feilformet`) — modulen er ikke lagrenes eneste vern.

    MUTASJONEN SOM DREPER DENNE: fjern `.replace(chr(0), "")` fra
    `_tekst`."""
    from modules.m57_ats import kjoring

    arkiv = _bunt(tmp_path, [("k1/soknad.html", b"<p>drift hos k1</p>")])
    felter = lambda m: {"navn": ["Kandidat k1"]}
    lagret = []

    modell = _Modell()
    ut = kjoring.kjor_bunt(
        arkiv, modell, vekter={"drift": 3}, kandidatfelter_for=felter,
        tekst_for=lambda m, d: "drift\x00tekst for Kandidat k1\x00",
        biasmaalinger=_MAALINGER, antall_soknader=1,
        lagre_dokument=lambda kid, navn, data, tekst: lagret.append(tekst))

    # Kjøringen fullfører — den falt ikke på et usynlig tegn.
    assert set(ut["artefakter"]) == {"k1"}
    # Dokumentsinken fikk tekst basen faktisk kan holde …
    assert lagret and all("\x00" not in t for t in lagret), lagret
    assert "drifttekst" in lagret[0], lagret
    # … modellen så nøyaktig det samme (blindet) …
    assert modell.sett == ["drifttekst for [NAVN-1]"], modell.sett
    # … og `kildetekst` i artefaktet, som går i jsonb, er like ren.
    assert "\x00" not in ut["artefakter"]["k1"]["kildetekst"]


def test_uttrekkerens_egen_kode_overlever_ut_av_kjoringen(tmp_path):
    """Cursor P2, runde 6: uttrekkerens SP-3-kode ble spist av `_tekst`.

    `_tekst` fanget `Exception` — og `uttrekk.Uttrekksfeil` er en av dem
    — så `uttrekk_ustottet`/`uttrekk_uleselig` kom ut av `kjor_bunt` som
    den generiske `tekstuttrekk_feilet`. Følgen sto å lese rett over i
    fila: `kjor_bunt`s egen `except uttrekk.Uttrekksfeil` var DØD kode.
    `Uttrekksfeil` reises bare i `uttrekk.py`, og eneste vei derfra inn i
    kjøringen går gjennom `_tekst`, så oversetteren ble aldri nådd.

    Prisen er feilattribusjon i drift: en deployment uten `pdftotext`, en
    docx-bombe og en html i feil koding er tre ULIKE svar til den som
    står med driftsloggen — den første er en konfigurasjon som mangler,
    de to andre er filer som er noe annet enn de utgir seg for. Alle tre
    ble rapportert som «tekstuttrekket feilet», og `kjoring_avbrutt:
    <kode>` i controller-utfallet pekte bort fra det som faktisk skjedde.

    Testen kjører den EKTE `Uttrekker`. Det er poenget: hullet fikk stå
    fordi porten over reiser `ValueError` fra en stub, og en `ValueError`
    SKAL bli `tekstuttrekk_feilet`. Bare produksjonsuttrekkeren bærer
    kodene som forsvant.

    MUTASJONEN SOM DREPER DENNE: fjern `except uttrekk.Uttrekksfeil:
    raise` i `_tekst` — eller `except uttrekk.Uttrekksfeil`-grenen i
    `kjor_bunt` den bærer koden fram til.
    """
    from modules.m57_ats import kjoring, uttrekk

    felter = lambda m: {"navn": ["Kandidat k1"]}
    # Tom `pdf_kommando`: PDF-uttrekk er utilgjengelig i DENNE
    # deploymenten — ikke en ødelagt fil.
    ekte = uttrekk.Uttrekker("")

    def kjor(arkiv):
        return kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                                 kandidatfelter_for=felter,
                                 tekst_for=ekte.tekst_for,
                                 biasmaalinger=_MAALINGER,
                                 antall_soknader=1)

    # 1) `uttrekk_ustottet`: bunten er lovlig, uttrekkeren kan bare ikke
    #    lese den her. Det er en driftskonfigurasjon som mangler.
    ustottet = tmp_path / "ustottet"
    ustottet.mkdir()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjor(_bunt(ustottet, [("k1/cv.pdf", b"%PDF-1.4 en ekte pdf")]))
    assert e.value.kode == "uttrekk_ustottet", e.value.kode

    # 2) `uttrekk_uleselig`: html-en passerer buntgaten (den SER ut som
    #    html), og først dekodingen finner at bytene ikke er UTF-8.
    uleselig = tmp_path / "uleselig"
    uleselig.mkdir()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjor(_bunt(uleselig, [("k1/soknad.html", b"<p>drift \xff\xfe</p>")]))
    assert e.value.kode == "uttrekk_uleselig", e.value.kode


def test_173_uttrekkstaket_er_sinkens_tak(tmp_path):
    """Codex P2 (#173): uttrekket var UBUNDET, sinken var bundet.

    `api.app._KANDIDAT_DOK_MAKS` avviser en parsettekst over 25 MiB som
    `request_feilformet`, og controlleren melder den 4xx-en som
    `kandidatlagring_feilet` for HELE bunten. Men `_pdf` returnerte hele
    `pdftotext`-stdout uansett størrelse, og en PDF innenfor arkivets
    `MAKS_ENKELTFIL` kan lovlig pakke ut til langt mer tekst. Arkivgaten
    sa ja, uttrekket sa ja, og sinken felte bunten på noe ingen av dem
    hadde sagt fra om.

    Grensen hører hjemme i uttrekket: der er den et KODET utfall om ETT
    dokument, mens den ved sinken er en lagringsfeil om hele
    evalueringen. Å heve sinkens tak i stedet ville sluppet ubundet
    tekst inn i `TEXT`-kolonnen og fjernet §4-budsjettet i stedet for å
    flytte det.

    To ting måles, og begge er nødvendige:

    1. Tallene er LIKE. modules/ og api/ importerer ikke hverandre, så
       konstanten er speilet — og et speil ingen måler driver. Hever
       noen det ene taket alene, er funnet tilbake.
    2. Uttrekkeren HÅNDHEVER sitt eget tak, med en kodet
       `Uttrekksfeil` som `kjor_bunt` bærer urørt videre. Kommandoen er
       en ekte prosess som skriver mer enn taket på stdout — ikke en
       stub som later som.

    MUTASJONEN SOM DREPER DENNE: fjern `MAKS_TEKST`-porten i
    `tekst_for`, eller sett taket til noe annet enn sinkens.
    """
    import shutil
    import types

    from api.app import _KANDIDAT_DOK_MAKS
    from modules.m57_ats import uttrekk

    assert uttrekk.MAKS_TEKST == _KANDIDAT_DOK_MAKS, (
        f"uttrekket bruker {uttrekk.MAKS_TEKST}, sinken"
        f" {_KANDIDAT_DOK_MAKS} — speilet har drevet, og differansen er"
        " nøyaktig den bunten felles på")

    # En EKTE pdf-kommando som skriver mer enn taket ut. Taket senkes
    # kunstig i stedet for å presse 25 MiB gjennom CI — samme form som
    # `test_173_budsjettet_dekker_alle_tre_payloadene` bruker for
    # budsjettet, og den måler nøyaktig leddet funnet gjelder: at
    # uttrekket SELV stopper på sitt eget tall.
    python = shutil.which("python3") or shutil.which("python")
    assert python, "ingen python-tolk å bygge en ekte uttrekkskommando av"
    skript = tmp_path / "falsk_pdftotext.py"
    skript.write_text(
        "import sys\n"
        "sys.stdin.buffer.read()\n"
        "sys.stdout.buffer.write(b'T' * 5000)\n",
        encoding="utf-8")
    u = uttrekk.Uttrekker(f"{python} {skript}")
    medlem = types.SimpleNamespace(navn="k1/cv.pdf")

    # Under taket: teksten kommer ut som den er.
    u_stor = uttrekk.MAKS_TEKST
    try:
        uttrekk.MAKS_TEKST = 5000
        assert len(u.tekst_for(medlem, b"%PDF-1.4")) == 5000
        # Over taket, med ETT tegn: porten måler grensen, ikke en sone.
        uttrekk.MAKS_TEKST = 4999
        with pytest.raises(uttrekk.Uttrekksfeil) as e:
            u.tekst_for(medlem, b"%PDF-1.4")
    finally:
        uttrekk.MAKS_TEKST = u_stor
    assert e.value.kode == "uttrekk_uleselig", e.value.kode


def test_173_pdf_stdout_felles_mens_den_skrives_ikke_etterpa(tmp_path):
    """Codex P1 (#173): taket sto BAK døren det skulle vokte.

    Forrige runde ga `tekst_for` et tak, men `_pdf` hentet fortsatt
    stdout med `capture_output=True` — altså materialiserte HELE
    utdataen i minnet FØR porten fikk se den. En PDF innenfor arkivets
    `MAKS_ENKELTFIL` (25 MiB) kan pakke ut til langt mer tekst enn
    unitens `MemoryMax=1G`, og da blir arbeideren OOM-drept før den
    rekker å returnere det kodede `uttrekk_uleselig`-utfallet. Porten
    var ikke feil, den sto bare for sent: en grense som først måles
    etter at minnet er brukt opp, måles aldri.

    Målingen skiller de to formene på detaljen, ikke på klokken:
    kommandoen her skriver forbi taket og SOVER så lenge — lenger enn
    fristen. Felles den mens den skriver, er utfallet «tekst for stor»
    med én gang; buffres den til slutt, kan utfallet bare bli
    `TimeoutExpired`, og da har prosessen holdt hele overskytelsen i
    minnet i mellomtiden. Klokken måles i tillegg, som en billig
    forsikring om at det faktisk var den tidlige veien.

    MUTASJONEN SOM DREPER DENNE: sett `_pdf` tilbake til
    `subprocess.run(..., capture_output=True)`, eller fjern
    størrelsesmålingen i `_kjor_bundet`s ventelokke.
    """
    import shutil
    import time
    import types

    from modules.m57_ats import uttrekk

    python = shutil.which("python3") or shutil.which("python")
    assert python, "ingen python-tolk å bygge en ekte uttrekkskommando av"
    # Skriver 4 MiB i biter — med flush, for stdout er en FIL her og
    # buffres ellers til prosessen avslutter — og sover deretter langt
    # forbi fristen uten å avslutte.
    skript = tmp_path / "pdftotext_som_spyr.py"
    skript.write_text(
        "import sys, time\n"
        "sys.stdin.buffer.read()\n"
        "for _ in range(16):\n"
        "    sys.stdout.buffer.write(b'T' * (256 * 1024))\n"
        "    sys.stdout.buffer.flush()\n"
        "time.sleep(120)\n",
        encoding="utf-8")

    frist_s = 30.0
    u = uttrekk.Uttrekker(f"{python} {skript}", frist_s=frist_s)
    medlem = types.SimpleNamespace(navn="k1/cv.pdf")

    u_stor = uttrekk.MAKS_TEKST
    start = time.monotonic()
    try:
        # Taket senkes kunstig i stedet for å presse 25 MiB gjennom CI
        # — samme form som `test_173_uttrekkstaket_er_sinkens_tak`.
        uttrekk.MAKS_TEKST = 1024 * 1024
        with pytest.raises(uttrekk.Uttrekksfeil) as e:
            u.tekst_for(medlem, b"%PDF-1.4")
    finally:
        uttrekk.MAKS_TEKST = u_stor
    brukt = time.monotonic() - start

    assert e.value.kode == "uttrekk_uleselig", e.value.kode
    assert "tekst for stor" in str(e.value), (
        "utfallet kom ikke fra størrelsesgrensen — stdout ble buffret"
        f" ferdig først: {e.value}")
    assert brukt < frist_s / 2, (
        f"uttrekket brukte {brukt:.1f}s av en frist på {frist_s:.0f}s —"
        " kommandoen ble ikke felt idet den passerte taket")


def test_tomt_tekstuttrekk_er_kodet_feil_ikke_en_tom_vurdering(tmp_path):
    """Codex P1: `isinstance(tekst, str)` slipper `""` og bare blanktegn.

    En skannet pdf uten OCR, en docx uten lesbare avsnitt og en html som
    bare er markup gir alle en STRENG — bare uten innhold. Modellen får
    da ingenting å vurdere, svarer skjemakomplett «ingen krav oppfylt»,
    og det blir en VELLYKKET artefakt: kandidaten rangeres nederst som om
    søknaden hennes var tom, uten at noen får vite at det var uttrekket
    som feilet. Kravet måles på den SAMLEDE teksten per kandidat, ikke
    per fil: et tomt søknadsbrev ved siden av en full CV er en helt
    normal mappe.

    MUTASJONEN SOM DREPER DENNE: fjern `if not tekst.strip()`-porten i
    `kjor_bunt`.
    """
    from modules.m57_ats import kjoring

    felter = lambda m: {"navn": ["Kandidat k1"]}
    tomt = tmp_path / "tomt"
    tomt.mkdir()
    arkiv = _bunt(tomt, [("k1/soknad.html", b"<p>drift hos k1</p>")])

    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                          kandidatfelter_for=felter,
                          tekst_for=lambda m, d: "   \n\t ",
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "tekstuttrekk_feilet"
    # Stoppen er FØR modellen: ingen tom vurdering ble laget.
    assert modell.sett == []

    # … men ett tomt medlem i en mappe som ellers har tekst, er ingen
    # feil — kravet gjelder kandidaten, ikke fila.
    delvis = tmp_path / "delvis"
    delvis.mkdir()
    arkiv2 = _bunt(delvis, [
        ("k1/soknad.html", b"<p>tom</p>"),
        ("k1/cv.html", b"<p>drift</p>"),
    ])
    ut = kjoring.kjor_bunt(
        arkiv2, _Modell(), vekter={"drift": 3},
        kandidatfelter_for=felter,
        tekst_for=lambda m, d: ("" if m.navn.endswith("soknad.html")
                                else "Kandidat k1 kan drift i CV-en"),
        biasmaalinger=_MAALINGER, antall_soknader=1)
    assert set(ut["artefakter"]) == {"k1"}


def test_feltene_flettes_i_medlemsrekkefolge_ikke_zip_rekkefolge(tmp_path):
    """Codex P2: teksten var determinisert, feltene var det ikke.

    C2 sorterte tekstbitene på medlemsnavn nettopp for at samme bunt skal
    gi samme resultat uansett hvordan arkivet ble pakket. Feltene ble
    likevel flettet i lesesløyfa — altså i ZIP-rekkefølge. Bidro to filer
    for samme kandidat ulike verdier til samme maskerte felt, ga en
    ombyttet arkivrekkefølge en annen listerekkefølge, og `blinding.blind`
    nummererer tokenene etter listeposisjon: `[NAVN-1]`/`[NAVN-2]` byttet
    plass, `kildetekst` ble en annen streng, og artefakten avhang igjen av
    medlemsrekkefølgen.

    MUTASJONEN SOM DREPER DENNE: flytt `_flett_felter` tilbake inn i
    lesesløyfa.
    """
    from modules.m57_ats import kjoring

    def felter(medlem):
        return ({"navn": ["Kari"]} if medlem.navn.endswith("cv.html")
                else {"navn": ["Ola"]})

    def kjor(katalog, filer):
        katalog.mkdir()
        return kjoring.kjor_bunt(
            _bunt(katalog, filer), _Modell(), vekter={"drift": 3},
            kandidatfelter_for=felter,
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)

    a = ("k1/a_cv.html", b"<p>Kari kan drift</p>")
    b = ("k1/b_soknad.html", b"<p>Ola anbefaler Kari</p>")
    forst = kjor(tmp_path / "forst", [a, b])
    omvendt = kjor(tmp_path / "omvendt", [b, a])

    # Samme dokumenter, motsatt arkivrekkefølge — samme tokentildeling …
    assert forst["artefakter"]["k1"]["avmaskering"] == {
        "[NAVN-1]": "Kari", "[NAVN-2]": "Ola"}
    assert (omvendt["artefakter"]["k1"]["avmaskering"]
            == forst["artefakter"]["k1"]["avmaskering"])
    # … og dermed nøyaktig samme kildetekst, som er strengen funnenes
    # [start:slutt] indekserer.
    assert (omvendt["artefakter"]["k1"]["kildetekst"]
            == forst["artefakter"]["k1"]["kildetekst"])


def test_skraastrekaliaser_avgjores_paa_raanavnet_ikke_arkivrekkefolgen(
        tmp_path):
    """Codex P2: det normaliserte medlemsnavnet er ikke en entydig nøkkel.

    `kjor_bunt` normaliserer `\\` til `/` for å finne kandidatmappen, og
    sorterer så bitene på DET navnet. En bunt kan lovlig bære både
    `k1/cv.html` og `k1\\cv.html` — buntgaten måler duplikater på RÅnavnet,
    og de to er forskjellige — men etter normaliseringen er de samme
    streng. Sorteringen ble da et uavgjort, og `sorted` er stabil: den
    falt tilbake på ARKIVREKKEFØLGEN, altså nøyaktig avhengigheten C2
    fjernet. Med ombyttede oppføringer flettes feltene i motsatt orden,
    `blinding.blind` nummererer etter listeposisjon, og `[NAVN-1]` peker
    på en ANNEN person: samme bunt, to ulike artefakter.

    MUTASJONEN SOM DREPER DENNE: sett sorteringsnøkkelen tilbake til
    `bit[0]` (bare det normaliserte navnet).
    """
    from modules.m57_ats import kjoring

    def felter(medlem):
        # Rånavnet er det eneste som skiller de to medlemmene.
        return ({"navn": ["Ola"]} if "\\" in medlem.navn
                else {"navn": ["Kari"]})

    def kjor(katalog, filer):
        katalog.mkdir()
        return kjoring.kjor_bunt(
            _bunt(katalog, filer), _Modell(), vekter={"drift": 3},
            kandidatfelter_for=felter,
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)

    skraa = ("k1/cv.html", b"<p>Kari kan drift</p>")
    bakover = ("k1\\cv.html", b"<p>Ola anbefaler Kari</p>")
    forst = kjor(tmp_path / "forst", [skraa, bakover])
    omvendt = kjor(tmp_path / "omvendt", [bakover, skraa])

    # Begge er samme kandidat — normaliseringen gjør sin jobb …
    assert set(forst["artefakter"]) == {"k1"}
    # … og rånavnet avgjør rekkefølgen, så tokentildelingen er den samme
    # uansett hvordan arkivet ble pakket («/» < «\» i tegnverdi).
    assert forst["artefakter"]["k1"]["avmaskering"] == {
        "[NAVN-1]": "Kari", "[NAVN-2]": "Ola"}
    assert (omvendt["artefakter"]["k1"]["avmaskering"]
            == forst["artefakter"]["k1"]["avmaskering"])
    assert (omvendt["artefakter"]["k1"]["kildetekst"]
            == forst["artefakter"]["k1"]["kildetekst"])


def test_fremdriften_teller_hvert_medlem_ikke_bare_sjekkpunktene(tmp_path):
    """Codex P2: evidensen løy om hvor langt kjøringen kom.

    `les_porsjonsvis` leverer et fremdriftsmerke bare hver 200. fil og på
    det siste medlemmet. Sto `fremdrift` stille mellom merkene, meldte et
    utfall på fil nr. 3 av 4 `filer_lest: 0` — og etter en porsjonsgrense
    kunne det underrapportere med opptil 199. Feltet er kontraktens
    EVIDENS for hvor langt kjøringen kom (§7); da må det telle det som
    faktisk er lest.

    MUTASJONEN SOM DREPER DENNE: sett `fremdrift = merke` bak
    `if merke:` igjen, uten den egne medlemstelleren.
    """
    from modules.m57_ats import kjoring

    arkiv = _bunt(tmp_path, [
        ("k1/a.html", b"<p>drift</p>"),
        ("k2/b.html", b"<p>drift</p>"),
        ("k3/c.html", b"<p>drift</p>"),
        ("k4/d.html", b"<p>drift</p>"),
    ])

    def _uttrekk(medlem, data):
        # Feiler på det TREDJE medlemmet — før det avsluttende merket.
        if medlem.navn == "k3/c.html":
            raise ValueError("pdf-en er ødelagt")
        return data.decode("utf-8")

    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                          kandidatfelter_for=lambda m: {"navn": ["N"]},
                          tekst_for=_uttrekk, biasmaalinger=_MAALINGER, antall_soknader=4)
    assert e.value.kode == "tekstuttrekk_feilet"
    assert e.value.fremdrift["filer_lest"] == 3, (
        "evidensen skal si at tre medlemmer var lest da det røk, ikke 0")


def test_lesefeil_paa_lageret_tilskrives_ikke_modellen(tmp_path, monkeypatch):
    """Codex P2: en lagringsutfall ble meldt som «modellfeil».

    `les_porsjonsvis` slipper MED VILJE en `OSError` med errno gjennom som
    seg selv — en lesefeil på disk eller nettlager er drift, ikke en
    påstand om kundens bunt. Catch-allen i `kjor_bunt` fanget den likevel
    og ga den koden `modellfeil`, så både arbeiderens retry og
    driftsdiagnostikken tilskrev MODELLEN et lagringsavbrudd. Koden er
    utfallets eneste data (SP-3), og da må den peke på det som faktisk
    røk.

    MUTASJONEN SOM DREPER DENNE: fjern `except OSError`-grenen i
    `kjor_bunt`, så lesefeilen faller til catch-allen igjen.
    """
    from modules.m57_ats import kjoring

    def _strom_som_roeyker(sti, **kw):
        yield ({"filer_lest": 1, "filer_totalt": 2, "byte_lest": 18},
               parsing.Medlem("k1/cv.html", 18), b"<p>drift</p>")
        # Nøyaktig formen `les_porsjonsvis` lar passere: errno er satt.
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(kjoring.parsing, "les_porsjonsvis",
                        _strom_som_roeyker)
    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>drift</p>")])
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                          kandidatfelter_for=lambda m: {"navn": ["N"]},
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "infrastrukturfeil", (
        "en lesefeil på lageret skal ikke bære modellens kode")
    assert isinstance(e.value.__cause__, OSError)
    # Evidensen står: ett medlem var lest da det røk.
    assert e.value.fremdrift["filer_lest"] == 1

    # Den ERRNO-LØSE formen er noe annet — den er dekompressorens, og
    # `parsing` oversetter den til `korrupt_bunt` før den kommer hit. Kommer
    # den likevel, er den fremmed kode og skal IKKE bli en driftssak.
    def _strom_uten_errno(sti, **kw):
        yield ({"filer_lest": 1, "filer_totalt": 2, "byte_lest": 18},
               parsing.Medlem("k1/cv.html", 18), b"<p>drift</p>")
        raise OSError("Invalid data stream")

    monkeypatch.setattr(kjoring.parsing, "les_porsjonsvis",
                        _strom_uten_errno)
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, _Modell(), vekter={"drift": 3},
                          kandidatfelter_for=lambda m: {"navn": ["N"]},
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "modellfeil"


def test_lesefeil_under_deklarasjonen_er_ogsaa_drift(tmp_path, monkeypatch):
    """Cursor P1 (runde 4) på #161: lagringsutfallet gjaldt bare strømmen.

    #161 la arkivlesing FORAN strømmen — `inspiser_bunt` + `les_manifest`
    — men utenfor den indre `try`-en som oversetter `OSError` MED errno
    til `infrastrukturfeil`. `les_manifest` slipper den formen rått ut med
    vilje (drift, ikke bunt), så en EIO under lesing av `soknader.json`
    fant ingen håndterer og falt til catch-allen: `modellfeil` om en bunt
    modellen aldri fikk se. Nøyaktig klassen
    `test_lesefeil_paa_lageret_tilskrives_ikke_modellen` lukket for
    strømmen, gjenåpnet av den nye porten foran den.

    MUTASJONEN SOM DREPER DENNE: flytt `les_manifest`-linja ut av den
    indre `try`-en i `kjor_bunt` igjen.
    """
    from modules.m57_ats import kjoring

    ekte_read = zipfile.ZipFile.read

    def _lageret_roeyker(self, navn, pwd=None):
        # Bare deklarasjonen røyker: gaten foran den har alt lest
        # katalogen, så feilen treffer nøyaktig den nye lesingen.
        if getattr(navn, "filename", navn) == parsing.MANIFESTNAVN:
            raise OSError(errno.EIO, "Input/output error")
        return ekte_read(self, navn, pwd)

    monkeypatch.setattr(zipfile.ZipFile, "read", _lageret_roeyker)
    modell = _Modell()
    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>drift</p>")])
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                          kandidatfelter_for=lambda m: {"navn": ["N"]},
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "infrastrukturfeil", (
        "en lesefeil under deklarasjonen er drift, ikke modellens feil")
    assert isinstance(e.value.__cause__, OSError)
    assert e.value.__cause__.errno == errno.EIO
    # Bunten nådde aldri modellen — evidensen skal si det samme.
    assert modell.sett == []
    assert e.value.fremdrift["filer_lest"] == 0


def test_modellens_egen_nettverksfeil_er_ikke_en_driftssak(tmp_path):
    """Codex P2: lagringshåndtereren dekket også modellkallet.

    En `ConnectionResetError` fra modellklienten ER en `OSError` med errno,
    akkurat som lesefeilen på lageret. Sto `except OSError` blant de øvrige
    håndtererne, dekket den hele `try`-en — også `evaluer_kandidat` — og
    modellens eget nettverksavbrudd ble meldt som `infrastrukturfeil`.
    Forrige runde flyttet lagringsfeilen ut av modellkøen; uten
    innsnevringen tok den modellens feil med seg samme vei, og driften
    leter etter et lagringsavbrudd som aldri fant sted. Kilden avgjør
    koden, og kilden er hvor unntaket oppsto.

    MUTASJONEN SOM DREPER DENNE: flytt `except OSError`-grenen ut av den
    indre `try`-en rundt arkivgaten og ned blant de øvrige igjen.
    """
    from modules.m57_ats import kjoring

    class _ModellSomMisterForbindelsen(_Modell):
        def vurder(self, tekst, vekter):
            raise ConnectionResetError(errno.ECONNRESET, "Connection reset")

    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>Kari kan drift</p>")])
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, _ModellSomMisterForbindelsen(),
                          vekter={"drift": 3},
                          kandidatfelter_for=lambda m: {"navn": ["Kari"]},
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "modellfeil", (
        "modellens eget avbrudd skal ikke sende driften på lagringssaken")
    assert isinstance(e.value.__cause__, OSError)
    # Arkivet ER lest ferdig — evidensen er ikke en lesefeil.
    assert e.value.fremdrift["filer_lest"] == 1


def test_feltuttrekket_tilskrives_ikke_modellen(tmp_path):
    """Codex P2: en vranglest strukturert søknad ble meldt som «modellfeil».

    `kandidatfelter_for` er INJISERT fremmed kode på nøyaktig samme måte
    som `tekst_for` — men bare `tekst_for` hadde vakt. Feilet feltuttrekket
    på en søknadsform det ikke forsto, falt unntaket til catch-allen og kom
    ut med modellens kode, selv om modellen aldri ble kalt. Da retryer
    arbeideren mot en deterministisk inndatafeil, og driftsdiagnostikken
    leter etter modellen som aldri kjørte.

    MUTASJONEN SOM DREPER DENNE: kall `kandidatfelter_for(medlem)` direkte
    i lesesløyfa igjen, uten `_felter`.
    """
    from modules.m57_ats import kjoring

    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>drift</p>")])

    def _doende_felter(medlem):
        raise ValueError("ukjent søknadsform")

    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                          kandidatfelter_for=_doende_felter,
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "feltuttrekk_feilet", (
        "et feltuttrekk som feiler skal ikke bære modellens kode")
    assert isinstance(e.value.__cause__, ValueError)
    # Stoppen er FØR modellen — den ble aldri spurt.
    assert modell.sett == []
    # Evidensen står: medlemmet var lest da det røk.
    assert e.value.fremdrift["filer_lest"] == 1


def test_feltuttrekk_som_gir_ikkekart_tilskrives_ikke_modellen(tmp_path):
    """Codex P2: vakten fanget bare REISTE unntak, ikke tomme returer.

    En uttrekker kan melde en vranglest søknadsform ved å GI TILBAKE
    ingenting i stedet for å reise. `_felter` slapp da verdien gjennom, og
    først nede i `_flett_felter` røk `dict(nye)` på en `None` — det
    unntaket har ingen vakt over seg og falt til catch-allen, altså ut med
    `modellfeil` for et uttrekk modellen aldri var i nærheten av. Samme
    feilattribusjon som forrige runde lukket, bare via den andre døren.

    MUTASJONEN SOM DREPER DENNE: fjern `isinstance(felter, dict)`-porten i
    `_felter` — da blir koden `modellfeil` igjen.
    """
    from modules.m57_ats import kjoring

    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>drift</p>")])
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                          kandidatfelter_for=lambda m: None,
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "feltuttrekk_feilet", (
        "en uttrekker som gir tilbake noe annet enn et kart, feilet")
    # Stoppen er FØR modellen — den ble aldri spurt.
    assert modell.sett == []


def test_feltuttrekk_som_gir_annet_kart_enn_dict_slipper_gjennom(tmp_path):
    """Codex P2: vakten over målte ÉN implementasjon, ikke kontrakten.

    `_flett_felter` normaliserer med `dict(nye)` og har derfor alltid tatt
    imot et hvilket som helst kart. Sto det `isinstance(felter, dict)` i
    `_felter`, avviste porten en `MappingProxyType` (formen du får når
    uttrekkeren leverer en uforanderlig visning av sin egen tilstand) eller
    en `UserDict` — et gyldig uttrekk ble et kodet feilutfall, og
    kontrakten for den injiserte uttrekkeren ble snevret inn av en vakt som
    bare skulle stanse `None` og annet som IKKE er et kart.

    MUTASJONEN SOM DREPER DENNE: skriv `Mapping`-porten i `_felter` om til
    `isinstance(felter, dict)` — da blir koden `feltuttrekk_feilet` for et
    uttrekk som er helt i orden.
    """
    from modules.m57_ats import kjoring

    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>Kari kan drift</p>")])
    for kart in (types.MappingProxyType({"navn": ["Kari"]}),
                 collections.UserDict({"navn": ["Kari"]})):
        modell = _Modell()
        ut = kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                               kandidatfelter_for=lambda m, k=kart: k,
                               tekst_for=lambda m, d: d.decode("utf-8"),
                               biasmaalinger=_MAALINGER, antall_soknader=1)
        assert [r["kandidat_id"] for r in ut["rangering"]] == ["k1"], (
            f"{type(kart).__name__} er et kart og skal evalueres")
        # Blindingen fikk feltene: klarteksten nådde aldri modellen.
        assert modell.sett and "Kari" not in modell.sett[0]


def test_manifestet_er_deklarasjonen_og_binder_toveis(tmp_path):
    """#161 (eiers B): kandidatene deklareres av `soknader.json`, bindes
    toveis mot katalogen, og manifestet — ikke mappenavnet — eier
    kandidat-identiteten."""
    import json as _json

    from modules.m57_ats import kjoring

    def _kjor(arkiv, antall=1):
        return kjoring.kjor_bunt(
            arkiv, _Modell(), vekter={"drift": 3},
            kandidatfelter_for=lambda m: {"navn": ["Kari"]},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=antall)

    # Manifestet er AUTORITETEN: en fil i «feil» mappe hører til den
    # kandidaten deklarasjonen sier — mappenavnet betyr ingenting.
    (tmp_path / "a").mkdir()
    arkiv = _bunt(tmp_path / "a",
                  [("k1/cv.html", b"<p>Kari kan drift</p>"),
                   ("annet/brev.html", b"<p>mer drift</p>")],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "kari",
                       "filer": ["k1/cv.html", "annet/brev.html"]}]}))
    ut = _kjor(arkiv)
    assert set(ut["artefakter"]) == {"kari"}, \
        "mappenavnet vant over deklarasjonen"

    # Toveis: deklarert fil uten medlem er rød …
    (tmp_path / "b").mkdir()
    arkiv = _bunt(tmp_path / "b", [("k1/cv.html", b"<p>x</p>")],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1",
                       "filer": ["k1/cv.html", "k1/finnes_ikke.pdf"]}]}))
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(arkiv)
    assert e.value.kode == "manifest_medlem_mangler"

    # … og medlem uten deklarasjon er like rødt.
    (tmp_path / "c").mkdir()
    arkiv = _bunt(tmp_path / "c",
                  [("k1/cv.html", b"<p>x</p>"),
                   ("smugler/ekstra.html", b"<p>y</p>")],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"]}]}))
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(arkiv)
    assert e.value.kode == "medlem_uadressert"

    # Deklarert tall mot signert tall måles FØR strømmen.
    (tmp_path / "d").mkdir()
    arkiv = _bunt(tmp_path / "d",
                  [("k1/cv.html", b"<p>x</p>"),
                   ("k2/cv.html", b"<p>y</p>")])
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(arkiv, antall=1)
    assert e.value.kode == "kandidattall_avvik"


def test_manifestfeltene_er_blindingens_kilde(tmp_path):
    """#158s strukturelle retning: personfeltene DEKLARERES i manifestet
    og driver blindingen — uten callback, uten fritekst-søk. En kandidat
    uten deklarerte felter er et kodet stopp, aldri en ublindet
    evaluering."""
    import json as _json

    from modules.m57_ats import kjoring

    def _kjor(arkiv):
        modell = _Modell()
        ut = kjoring.kjor_bunt(
            arkiv, modell, vekter={"drift": 3},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)
        return ut, modell

    (tmp_path / "a").mkdir()
    arkiv = _bunt(tmp_path / "a",
                  [("k1/cv.html", b"<p>Kari Testdal kan drift, "
                                  b"kari@eksempel.no</p>")],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                       "felter": {"navn": ["Kari Testdal"],
                                  "kontakt": ["kari@eksempel.no"]}}]}))
    ut, modell = _kjor(arkiv)
    assert set(ut["artefakter"]) == {"k1"}
    # Modellen så ALDRI de deklarerte verdiene — masken erstattet dem.
    assert modell.sett, "modellen ble aldri kalt"
    for tekst in modell.sett:
        assert "Kari Testdal" not in tekst, "navnet lakk til modellen"
        assert "kari@eksempel.no" not in tekst, "kontakten lakk"
        assert "[NAVN-1]" in tekst, "masken mangler"

    # Uten deklarerte felter: fail-closed, kodet.
    (tmp_path / "b").mkdir()
    arkiv = _bunt(tmp_path / "b", [("k1/cv.html", b"<p>drift</p>")])
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(arkiv)
    assert e.value.kode == "blinding_uten_felter"


def test_manglende_felter_felles_for_stroemmen(tmp_path, monkeypatch):
    """Codex P2 `3864238384` (eierdom 26/8 pkt. 2): utfallet var kjent
    rett etter `les_manifest`, men ble felt først i `evaluer_kandidat`.

    Prisen var ikke feil KODE, men feil REKKEFØLGE: hele arkivet ble
    pakket ut og beholdt i `biter` før dommen falt, og fordi kandidatene
    evalueres `sorted(biter)`, kunne en TIDLIGERE kandidat ha vært hos
    modellen før en SENERE kandidats manglende felter stoppet kjøringen.
    En stor bunt kunne dessuten treffe minnegrensen først og komme ut
    med en annen kode enn den avgjorte.

    Riggen måler nøyaktig det: `k1` er fullt deklarert og sorterer
    FØRST, `k2` mangler `felter`. Uttrekksteller er `les_porsjonsvis`
    selv — porten står foran strømmen, så generatoren skal aldri kalles.

    MUTASJONEN SOM DREPER DENNE: fjern `if not blinding_av`-løkken i
    `kjor_bunt` — koden blir fortsatt `blinding_uten_felter`, men
    `strommet` blir sann og modellen har sett `k1`.

    GRENSEN (samme dom, andre retning): porten flytter utfallet, den
    utvider det ikke. Med blindingen avskrudd finnes det ingen
    `blinding_uten_felter` nede i veien heller, og en kandidat uten
    deklarerte felter er da lovlig — siste del måler at den fortsatt
    kjører helt gjennom."""
    import json as _json

    from modules.m57_ats import kjoring, parsing

    strommet: list[str] = []
    ekte = parsing.les_porsjonsvis

    def teller(sti, **kw):
        strommet.append(str(sti))
        return ekte(sti, **kw)

    monkeypatch.setattr(kjoring.parsing, "les_porsjonsvis", teller)

    manifest = _json.dumps({"soknader": [
        {"kandidat_id": "k1", "filer": ["k1/cv.html"],
         "felter": {"navn": ["Kari Testdal"]}},
        {"kandidat_id": "k2", "filer": ["k2/cv.html"]}]})
    arkiv = _bunt(tmp_path,
                  [("k1/cv.html", b"<p>Kari Testdal kan drift</p>"),
                   ("k2/cv.html", b"<p>Ola Testdal kan drift</p>")],
                  manifest=manifest)

    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(
            arkiv, modell, vekter={"drift": 3},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=2)
    assert e.value.kode == "blinding_uten_felter"
    assert modell.sett == [], "en tidligere kandidat nådde modellen"
    assert strommet == [], "arkivet ble strømmet før den avgjorte dommen"

    # Avskrudd blinding: samme bunt, ingen port — utfallet flyttes,
    # ikke utvides.
    modell = _Modell()
    ut = kjoring.kjor_bunt(
        arkiv, modell, vekter={"drift": 3},
        tekst_for=lambda m, d: d.decode("utf-8"),
        biasmaalinger=_MAALINGER, antall_soknader=2, blinding_av=True,
        auditrad={"aktor": "drift", "ts": "2026-08-26T20:00:00Z",
                  "begrunnelse": "manuell kontroll"})
    assert set(ut["artefakter"]) == {"k1", "k2"}
    assert strommet, "strømmen skulle gått når porten ikke gjelder"


def test_vakuos_deklarasjon_felles_for_forste_modellkall(tmp_path):
    """Cursor P2: samme REKKEFØLGEPRIS som `blinding_uten_felter`, bare
    ett hakk senere i veien.

    `blinding_uten_felter` er avgjort av deklarasjonen alene og felles
    derfor foran strømmen. VAKUØSITETEN — en deklarasjon som ikke traff
    dokumentet — krever teksten, og var målt først inne i
    `evaluer_kandidat`, altså ETTER `modell.vurder` for hver tidligere
    kandidat. NBSP/NFD-deklarasjoner passerer `les_manifest`/
    `feltverdier_lukket` med vilje (eierdom, K2-kjennelse runde 5, valg
    B), så de lever helt frem til evalueringsløkka: med `k1` gyldig og
    `k2` vakuøs — og kandidatene evaluert `sorted` — hadde `k1` alt vært
    hos modellen når `k2` felte kjøringen.

    Riggen måler nøyaktig det: `k1` er gyldig og sorterer FØRST, `k2`
    deklarerer `Kari<NBSP>Testdal` mot en CV som skriver vanlig
    mellomrom. Blindingporten kjøres nå for HELE bunten før det første
    modellkallet, så `modell.sett` skal være tom.

    MUTASJONEN SOM DREPER DENNE: slå de to passene i `kjor_bunt` sammen
    igjen — koden blir fortsatt `ugyldig_maskeringsform`, men `k1` har
    nådd modellen.

    Kontrollen til slutt bytter NBSP-en mot et vanlig mellomrom og
    kjører samme bunt gjennom: da er utfallet rent. Uten den kunne
    testen bestått på en rigg som var ugyldig av en helt annen grunn.
    """
    import json as _json

    from modules.m57_ats import kjoring

    def _arkiv(katalog: str, mellomrom: str):
        mappe = tmp_path / katalog
        mappe.mkdir()
        manifest = _json.dumps({"soknader": [
            {"kandidat_id": "k1", "filer": ["k1/cv.html"],
             "felter": {"navn": ["Ola Testdal"]}},
            {"kandidat_id": "k2", "filer": ["k2/cv.html"],
             "felter": {"navn": [f"Kari{mellomrom}Testdal"]}}]})
        return _bunt(mappe,
                     [("k1/cv.html", b"<p>Ola Testdal kan drift</p>"),
                      ("k2/cv.html", b"<p>Kari Testdal kan drift</p>")],
                     manifest=manifest)

    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(
            _arkiv("nbsp", "\u00a0"), modell, vekter={"drift": 3},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=2)
    assert e.value.kode == "ugyldig_maskeringsform"
    assert modell.sett == [], "en tidligere kandidat nådde modellen"

    modell = _Modell()
    ut = kjoring.kjor_bunt(
        _arkiv("vanlig", " "), modell, vekter={"drift": 3},
        tekst_for=lambda m, d: d.decode("utf-8"),
        biasmaalinger=_MAALINGER, antall_soknader=2)
    assert set(ut["artefakter"]) == {"k1", "k2"}
    assert len(modell.sett) == 2, "riggen var ugyldig av en annen grunn"


def test_arkivinstansen_binder_deklarasjon_og_innhold(tmp_path, monkeypatch):
    """Codex P1 `3866252992` (eierdom 26/8 pkt. 1: K2-kjennelse runde 7,
    valg B i INODE-form): deklarasjonen og innholdet kom fra to
    uavhengige åpninger av samme STI.

    `les_manifest` henter blindingens kilde, `les_porsjonsvis` henter
    teksten — og byttes fila i vinduet mellom dem med et
    TOPOLOGI-BEVARENDE bytte (samme medlemsnavn, samme antall), blindes
    arkiv A-s deklarasjon inn i arkiv B-s dokument. De eksisterende
    portene ser det ikke: `medlem_uadressert`/`manifest_medlem_mangler`
    /`kandidattall_avvik` tar bare det topologi-ENDRENDE byttet,
    vakuøsitetsporten tier fordi A-s `Kari` FAKTISK traff B-s tekst, og
    port 16 leter bare etter deklarerte verdier — `Ola` er ikke
    deklarert i A.

    Testen er derfor TO målinger av samme bytte, og forskjellen er
    stien kalleren ga:

    * vanlig sti → klassen, DOKUMENTERT: kjøringen fullfører som
      blindet og `Ola` går i klartekst til modellen. Denne halvdelen er
      med vilje en måling av et hull, ikke av en port — lukkes klassen
      noen gang inne i modulen, SKAL den bli rød og tvinge fram en ny
      dom.
    * instansbundet sti (`/proc/self/fd/<fd>`) → LUKKINGEN, BEVIST:
      alle fire åpningene går gjennom samme inode, byttet når aldri
      kjøringen, og modellen ser A-s tekst fullt maskert.

    Inodebindingen ER beviset — ikke en `st_ino`-sammenligning, som
    hadde vært en heuristikk. Kallformen bor hos kontrolleren (PR-B),
    som er eneste produksjonskaller og eier fila den selv skrev; porten
    og kravet står her. Se KONTRAKT.md, `dom-klasse:
    arkivinstans-toctou`.

    Siste assert måler at byttet FAKTISK skjedde i begge løpene — uten
    den kunne fd-halvdelen vært grønn av at ingenting hendte."""
    import json as _json
    import os

    from modules.m57_ats import kjoring, parsing

    def _lag(mappe, felter, cv):
        mappe.mkdir()
        return _bunt(mappe, [("k1/cv.html", cv)], manifest=_json.dumps(
            {"soknader": [{"kandidat_id": "k1", "filer": ["k1/cv.html"],
                           "felter": felter}]}))

    a = _lag(tmp_path / "a", {"navn": ["Kari Testdal"]},
             b"<p>Kari Testdal kan drift</p>")
    b = _lag(tmp_path / "b", {"navn": ["Ola Testdal"]},
             b"<p>Kari Testdal og Ola Testdal kan drift</p>")
    original, byttet = a.read_bytes(), b.read_bytes()

    ekte = parsing.les_manifest

    def bytt_etter_lesing(sti, medlemmer):
        manifestet = ekte(sti, medlemmer)
        # Byttet er en ERSTATNING av stien (ny inode), slik en angriper
        # eller en samtidig skriver ville gjort det — ikke en skriving
        # inn i den åpne fila.
        (tmp_path / "ny.zip").write_bytes(byttet)
        os.replace(tmp_path / "ny.zip", a)
        return manifestet

    monkeypatch.setattr(kjoring.parsing, "les_manifest", bytt_etter_lesing)

    def _kjor(sti):
        modell = _Modell()
        ut = kjoring.kjor_bunt(
            sti, modell, vekter={"drift": 3},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)
        return ut, modell

    # 1. Vanlig sti: klassen, dokumentert.
    ut, modell = _kjor(a)
    assert set(ut["artefakter"]) == {"k1"}
    assert modell.sett, "modellen ble aldri kalt"
    assert "Ola Testdal" in modell.sett[0], (
        "byttet nådde ikke kjøringen — riggen måler ikke klassen")
    assert "Kari Testdal" not in modell.sett[0]

    # 2. Instansbundet sti: samme bytte, lukket per konstruksjon.
    a.write_bytes(original)
    with open(a, "rb") as fd:
        ut, modell = _kjor(f"/proc/self/fd/{fd.fileno()}")
    assert set(ut["artefakter"]) == {"k1"}
    assert modell.sett == ["<p>[NAVN-1] kan drift</p>"], (
        "innholdet kom ikke fra deklarasjonens egen arkivinstans")
    assert a.read_bytes() == byttet, "byttet skjedde ikke i det hele tatt"


def test_padda_feltverdi_er_ingen_deklarasjon(tmp_path):
    """Cursor P1: deklarasjonen er BÅDE det som maskeres og det porten
    leter etter, så en verdi som ikke kan stå i dokumentet gjør port 16
    vakuøs uten å gjøre den tom.

    `"Kari Testdal "` (hale) og `"Kari Testdal\\u200b"` passerte den
    gamle porten — den målte `strip()`, men LAGRET råverdien — og
    `krev_blindet` lette etter nøyaktig den padda formen. CV-en skriver
    navnet uten hale, så ingenting ble maskert OG ingenting ble funnet:
    klartekstnavnet gikk til modellen mens kjøringen telte som blindet.
    Samme klasse som `kandidat_id` før ASCII-kanonen.

    ETTER RUNDE 5 MÅLER DENNE BARE DEN STRUKTURELLE FORMEN — `verdi ==
    verdi.strip()`. Tegnkategoriene (`Cc`/`Cf`) er borte fra predikatet,
    og de USYNLIGE formene hører hjemme i
    `test_vakuos_deklarasjon_felles_paa_effekt_per_felt`: de er ikke en
    tegnliste å utvide, men en bom som måles på EFFEKT.

    MUTASJONEN SOM DREPER DENNE: fjern `verdiform_lukket`-kallet i
    `blinding.feltverdier_lukket` — da slipper begge dørene raden."""
    import json as _json

    from modules.m57_ats import kjoring

    cv = b"<p>Kari Testdal kan drift, kari@eksempel.no</p>"
    for i, verdi in enumerate(("Kari Testdal ", " Kari Testdal",
                               "Kari Testdal\n", "\tKari Testdal")):
        (tmp_path / f"p{i}").mkdir()
        arkiv = _bunt(tmp_path / f"p{i}", [("k1/cv.html", cv)],
                      manifest=_json.dumps({"soknader": [
                          {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                           "felter": {"navn": [verdi]}}]}))
        modell = _Modell()
        with pytest.raises(kjoring.Kjoringsfeil) as e:
            kjoring.kjor_bunt(
                arkiv, modell, vekter={"drift": 3},
                tekst_for=lambda m, d: d.decode("utf-8"),
                biasmaalinger=_MAALINGER, antall_soknader=1)
        assert e.value.kode == "manifest_feilformet", verdi
        # Avvisningen skjer i LESINGEN: navnet nådde aldri modellen.
        assert not modell.sett, verdi

    # Den INJISERTE veien (`kandidatfelter_for`) går utenom manifestet,
    # så grensen står i `blind` også — med samme definisjon.
    (tmp_path / "inj").mkdir()
    arkiv = _bunt(tmp_path / "inj", [("k1/cv.html", cv)])
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(
            arkiv, modell, vekter={"drift": 3},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1,
            kandidatfelter_for=lambda m: {"navn": ["Kari Testdal "]})
    assert e.value.kode == "ugyldig_maskeringsform"
    assert not modell.sett
    for padda in ("Kari Testdal ", " Kari", "Kari\n"):
        with pytest.raises(blinding.Blindingsfeil) as e:
            blinding.blind("Kari Testdal kan drift.", {"navn": [padda]})
        assert e.value.kode == "ugyldig_maskeringsform", padda


def test_manifestfeltenes_lukkede_form(tmp_path):
    """Feltdeklarasjonen er like LUKKET som resten av manifestet: ukjent
    feltnavn, feil typer, tomme og overfylte lister, for lange verdier og
    verdier som ikke er sin egen skrivemåte (padding) er alle
    `manifest_feilformet`.

    Cursor P2: enumerasjonen dekket alt UNNTATT den formen P1 levde i —
    uten negativen her regresserer `verdiform_lukket` stille."""
    import json as _json

    def _sjekk(felter, undermappe):
        (tmp_path / undermappe).mkdir()
        arkiv = _bunt(tmp_path / undermappe,
                      [("k1/cv.html", b"<p>x</p>")],
                      manifest=_json.dumps({"soknader": [
                          {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                           "felter": felter}]}))
        with pytest.raises(parsing.Buntfeil) as e:
            parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv))
        assert e.value.kode == "manifest_feilformet", felter
    for i, felter in enumerate((
        "ikke-dict",
        {},
        {"ukjent_felt": ["x"]},
        {"navn": "ikke-liste"},
        {"navn": []},
        {"navn": [""]},
        {"navn": ["x"] * (blinding.MAKS_FELTVERDIER + 1)},
        {"navn": ["   "]},
        {"navn": [7]},
        {"navn": ["x" * (blinding.MAKS_FELTVERDI_TEGN + 1)]},
        # Verdien er sin egen skrivemåte (Cursor P1/P2): en hale eller en
        # ledende blank gjør maskeringen og porten enige om noe som ikke
        # står i dokumentet. Grensen er STRUKTURELL, ikke en tegnliste —
        # de usynlige formene måles på EFFEKT (eierdom, runde 5, valg B),
        # se `test_vakuos_deklarasjon_felles_paa_effekt_per_felt`.
        {"navn": [" Kari"]},
        {"navn": ["Kari "]},
        {"navn": ["Kari\n"]},
        {"navn": ["\tKari"]},
        # Én ugyldig verdi feller hele deklarasjonen, også med gyldige
        # naboer i samme liste og i et annet felt.
        {"navn": ["Kari", "Testdal "]},
        {"navn": ["Kari"], "adresse": ["Gata 1 "]},
    )):
        _sjekk(felter, f"f{i}")

    # Positiv kontroll: gyldige felter leses ut som deklarert.
    (tmp_path / "ok").mkdir()
    arkiv = _bunt(tmp_path / "ok", [("k1/cv.html", b"<p>x</p>")],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                       "felter": {"navn": ["Kari"]}}]}))
    m = parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv))
    assert m.felter == {"k1": {"navn": ["Kari"]}}


def test_vakuos_deklarasjon_felles_paa_effekt_per_felt(tmp_path):
    """EIERDOM, K2-kjennelse runde 5 på #217 (valg B): vakuøsitet måles
    på EFFEKT, med per-FELT-semantikk.

    Rotårsaken fem runder aldri traff: `verdiform_lukket` var en
    HÅNDSKREVET SVARTELISTE over Unicode-kategorier (`Cc`/`Cf`), og en
    svarteliste er ufullstendig i ett predikat like fullt som i to.
    Runde 4 gjorde grensesettet til ETT predikat — den aksen er reelt
    død — men NBSP (`Zs`), U+2010 (`Pd`) og en NFD-dekomponert `å` er
    ingen av kategoriene, og hver av dem gir nøyaktig samme vakuum som
    en padda verdi gjorde i `7b8fa66`:

        deklarert  = "Kari" + NBSP + "Testdal"
        dokumentet = "Kari Testdal"   (vanlig mellomrom)
        -> maskeringen treffer ingenting
        -> `avmaskering` er IKKE tom, så `blinding_uten_felter` tier
        -> `krev_blindet` leter etter NBSP-formen og finner den ikke
        -> kjøringen telles som BLINDET mens klartekstnavnet går til
           modellen. Det er en LEKKASJE, ikke en fail-closed feller.

    Porten teller derfor ikke tegn: den måler at hvert DEKLARERT FELT
    traff dokumentteksten minst én gang. Da spiller det ingen rolle
    hvilket tegn som gjorde at deklarasjonen bommet — runde 6 på
    tegnaksen finnes ikke, fordi aksen ikke finnes.

    PER FELT, IKKE PER VERDI, og fortegnet er hele grunnen: en enkelt
    verdi uten treff er lovlig når en søsterverdi i samme felt traff.
    Ellers blir defensive varianter (`["Kari Testdal", "Kari"]`)
    selvmotsigende farlige, og deklarasjonen presses mot FÆRRE
    varianter — feil fortegn for personvern.

    KJENT RESTKLASSE (KONTRAKT.md, eid av #158): en forekomst i teksten
    som ingen deklarert verdi matcher MENS en annen verdi i samme felt
    traff, er udetekterbar uten NER. Den lukkes av strukturell blinding,
    ikke av en port her.

    MUTASJONEN SOM DREPER DENNE: fjern `if not all(traff.values())` i
    `blinding.blind`. Da blir hver bom-rad grønn, og E2E-delen nederst
    sender navnet i klartekst til modellen.
    """
    import json as _json
    import unicodedata as _ud

    from modules.m57_ats import kjoring

    # Tegnene bygges med `chr`, ikke som literaler: et usynlig tegn i en
    # testkilde er nettopp den forvekslingen porten handler om.
    NBSP, EMSP, HYPHEN = chr(0x00A0), chr(0x2003), chr(0x2010)
    ZWSP, RTL = chr(0x200B), chr(0x202E)
    KARE = "K" + chr(0x00E5) + "re Testdal"

    CV = "Soknad fra Kari Testdal, erfaren radgiver i Storgata 1."
    tekst = CV + " Kari-Testdal og " + KARE + " er referansene."
    BOM = (
        ("NBSP (Zs)", "Kari" + NBSP + "Testdal"),
        ("EM SPACE (Zs)", "Kari" + EMSP + "Testdal"),
        ("HYPHEN U+2010 (Pd) mot ASCII-bindestrek",
         "Kari" + HYPHEN + "Testdal"),
        ("ZWSP midt i (Cf)", "Kari" + ZWSP + "Testdal"),
        ("RTL-markor midt i (Cf)", "Kari" + RTL + "Testdal"),
        ("NFD-dekomponert mot NFC i dokumentet",
         _ud.normalize("NFD", KARE)),
        ("ren bom: verdien star ikke i teksten", "Ola Nordmann"),
    )
    for merke, verdi in BOM:
        # FORMPORTEN SLIPPER DEM GJENNOM — det er nettopp poenget: den
        # er strukturell, ikke en tegnliste. Det er EFFEKTEN som feller.
        assert blinding.feltverdier_lukket([verdi]), merke
        with pytest.raises(blinding.Blindingsfeil) as e:
            blinding.blind(tekst, {"navn": [verdi]})
        assert e.value.kode == "ugyldig_maskeringsform", merke

    # POSITIV VARIANTKONTROLL: en søsterverdi som ikke treffer noe i det
    # hele tatt holder ikke feltet nede, så lenge en annen variant traff.
    # Defensive varianter skal ikke straffes — de er riktig fortegn for
    # personvern.
    blindet, avmaskering = blinding.blind(
        CV, {"navn": ["Kari Testdal", "Kari"]})
    assert avmaskering == {"[NAVN-1]": "Kari Testdal", "[NAVN-2]": "Kari"}
    assert "Kari" not in blindet and "[NAVN-1]" in blindet
    # … og med en variant som er en ren bom (feilstavingen står ingen
    # steder i dokumentet), som er den skarpe formen av samme kontroll
    # etter at målingen flyttet til originalteksten (runde 6).
    blindet, _ = blinding.blind(CV, {"navn": ["Kari Testdahl",
                                              "Kari Testdal"]})
    assert "Kari Testdal" not in blindet and "[NAVN-2]" in blindet

    # … og NFD-formen er lovlig når det er DOKUMENTET som skriver den:
    # porten måler treffet, ikke normaliseringsformen.
    nfd = _ud.normalize("NFD", KARE)
    blindet, _ = blinding.blind("Soknad fra " + nfd + ".", {"navn": [nfd]})
    assert blindet == "Soknad fra [NAVN-1]."

    # PER FELT: `navn` treffer, `adresse` bommer — og da er DEKLARASJONEN
    # vakuøs, ikke bare den ene verdien. Uten dette kunne et felt ri
    # gratis på et annet felts treff.
    with pytest.raises(blinding.Blindingsfeil) as e:
        blinding.blind("Soknad fra Kari Testdal.",
                       {"navn": ["Kari Testdal"],
                        "adresse": ["Storgata 1"]})
    assert e.value.kode == "ugyldig_maskeringsform"
    # Treffer begge, er samme kart lovlig.
    blindet, _ = blinding.blind(
        CV, {"navn": ["Kari Testdal"], "adresse": ["Storgata 1"]})
    assert "Kari Testdal" not in blindet and "Storgata 1" not in blindet

    # DEKLARASJONSDØRA SER DEN IKKE, og skal ikke se den: `les_manifest`
    # har ingen dokumenttekst å måle mot. Formen er lovlig der …
    (tmp_path / "les").mkdir()
    cv_bytes = b"<p>Kari Testdal kan drift, kari@eksempel.no</p>"
    padda = "Kari" + NBSP + "Testdal"
    arkiv = _bunt(tmp_path / "les", [("k1/cv.html", cv_bytes)],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                       "felter": {"navn": [padda]}}]}))
    m = parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv))
    assert m.felter == {"k1": {"navn": [padda]}}

    # … og E2E felles den likevel, FØR modellen: kjøringen stopper
    # kodet, og navnet forlot aldri kjøringen.
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                          tekst_for=lambda mm, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "ugyldig_maskeringsform"
    assert modell.sett == [], "navnet nadde modellen"

    # Positiv E2E-kontroll på samme bunt: med den formen dokumentet
    # faktisk skriver, kjører den igjennom og masken står i inputen.
    (tmp_path / "e2e").mkdir()
    arkiv = _bunt(tmp_path / "e2e", [("k1/cv.html", cv_bytes)],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                       "felter": {"navn": ["Kari Testdal"],
                                  "kontakt": ["kari@eksempel.no"]}}]}))
    modell = _Modell()
    ut = kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                           tekst_for=lambda mm, d: d.decode("utf-8"),
                           biasmaalinger=_MAALINGER, antall_soknader=1)
    assert set(ut["artefakter"]) == {"k1"}
    for sett in modell.sett:
        assert "Kari Testdal" not in sett and "[NAVN-1]" in sett


def test_vakuositeten_maales_paa_originalteksten(tmp_path):
    """EIERDOM, K2-kjennelse runde 6 på #217 (valg A): `traff` måles mot
    ORIGINALTEKSTEN — søket skjer FØR noen erstatning.

    Runde 5 felte at hvert deklarert felt må treffe DOKUMENTET. Koden
    talte treffene med `subn` inne i erstatningsløkka, altså mot en tekst
    maskeringen selv nettopp hadde skrevet tokener inn i — en
    implementasjonsglipp av dommen, ikke en egen mekanisme. Følgen er en
    PORT-OMGÅELSE, målt av Codex (P1, review 19:38 på `13e7110`):

        tekst  = "Ａｌ is forty-two"        # fullbredde Ａｌ
        felter = {"navn": ["Al"], "alder": ["forty-two"]}

        -> "forty-two" erstattes først (lengste først)
        -> "Al" treffer så "AL" inni "[ALDER-1]" (`re.IGNORECASE`)
        -> traff["navn"] blir sann UTEN at navnet traff dokumentet
        -> porten sier god, og fullbredde-navnet står i klartekst i
           modellinputen mens kjøringen telles som blindet.

    Fullbredde-`Ａｌ` er ikke ASCII-`Al` under Unicodes enkle
    case-folding (`"Ａ".lower()` er `"ａ"`, ikke `"a"`), så deklarasjonen
    bommet — nøyaktig den vakuøsiteten runde 5 skulle felle.

    De to formene under er de to eier ba om regresjon på: omgåelsen
    (dokumentet skriver fullbredde-formen) og den rene bommen (`Al` står
    ikke i dokumentet i det hele tatt). Begge er nå
    `ugyldig_maskeringsform`.

    MUTASJONEN SOM DREPER DENNE: flytt `traff`-målingen i
    `blinding.blind` ned i erstatningsløkka igjen (`subn` mot `tekst`).
    """
    import json as _json

    from modules.m57_ats import kjoring

    FULLBREDDE = chr(0xFF21) + chr(0xFF4C)          # Ａｌ
    assert FULLBREDDE != "Al" and FULLBREDDE.lower() != "al"

    # FORM 1 — omgåelsen, ordrett slik den ble målt.
    with pytest.raises(blinding.Blindingsfeil) as e:
        blinding.blind(FULLBREDDE + " is forty-two",
                       {"navn": ["Al"], "alder": ["forty-two"]})
    assert e.value.kode == "ugyldig_maskeringsform"

    # FORM 2 — samme deklarasjon uten noe `Al` i teksten overhodet.
    with pytest.raises(blinding.Blindingsfeil) as e:
        blinding.blind("is forty-two",
                       {"navn": ["Al"], "alder": ["forty-two"]})
    assert e.value.kode == "ugyldig_maskeringsform"

    # E2E på omgåelsen: kjøringen stopper kodet, og fullbredde-navnet
    # forlot aldri kjøringen. Det er dette funnet handlet om — ikke
    # feilkoden, men at klarteksten ikke når modellen.
    (tmp_path / "omgaaelse").mkdir()
    cv = ("<p>" + FULLBREDDE + " is forty-two og kan drift</p>"
          ).encode("utf-8")
    arkiv = _bunt(tmp_path / "omgaaelse", [("k1/cv.html", cv)],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                       "felter": {"navn": ["Al"],
                                  "alder": ["forty-two"]}}]}))
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "ugyldig_maskeringsform"
    assert modell.sett == [], "fullbredde-navnet nadde modellen"

    # DEN LOVLIGE NABOEN: skriver dokumentet `Al`, traff deklarasjonen
    # dokumentet, og målingen skal ikke felle den.
    blindet, avmaskering = blinding.blind(
        "Al is forty-two", {"navn": ["Al"], "alder": ["forty-two"]})
    assert avmaskering == {"[NAVN-1]": "Al", "[ALDER-1]": "forty-two"}

    # … OG DA STÅR KORRUPSJONEN IGJEN, målt i stedet for bare beskrevet.
    # Erstatningen skriver inn i et token den selv la igjen, så
    # `[ALDER-1]` blir `[[NAVN-1]DER-1]` og avmaskeringstabellen er ikke
    # lenger reversibel. Port 16 passerer — klarteksten ER borte — så
    # utfallet er KORRUPT modellinput, ikke en lekkasje. Klassen er
    # UTSATT til #158 (disjunkt tokenalfabet), eierdom runde 6:
    #   dom-klasse: tokenkollisjon-korrupsjon · felt i #217 ·
    #   https://github.com/moka1980/disponit/pull/217#issuecomment-5430381316
    assert blindet == "[NAVN-1] is [[NAVN-1]DER-1]"
    blinding.krev_blindet(blindet, avmaskering)


def test_duplikate_manifestnokler_er_ingen_deklarasjon(tmp_path):
    """Codex P1 (review 15:20 på `7b8fa66`) — en LEKKASJE, ikke en
    formfeil, og den var aldri lukket før nå.

    `json.loads` lar den SISTE av to like nøkler vinne, stille. En
    deklarasjon som skriver `"navn"` to ganger taper altså den første
    verdien FØR noen port får se dokumentet: `Kari` står fortsatt i
    CV-en, hun finnes ikke i avmaskeringstabellen, og `krev_blindet`
    leter bare etter det som ER i tabellen. Modellen får dermed
    klartekstnavnet mens kjøringen telles som blindet — nøyaktig den
    vakuøse porten padding-P1-en handlet om, men med tapet ett hakk
    lenger opp.

    `object_pairs_hook` avviser duplikater i HELE manifestdokumentet, og
    det er med vilje bredere enn `felter`: en duplisert `kandidat_id`
    eller `filer` taper like stille en deklarasjon som skulle vært
    bundet toveis mot katalogen.

    MUTASJONEN SOM DREPER DENNE: fjern `object_pairs_hook`-argumentet i
    `les_manifest` (eller `if nokkel in ut`-armen i hooken)."""
    import json as _json

    from modules.m57_ats import kjoring

    cv = b"<p>Kari Testdal og Ola Nordmann kan drift.</p>"

    def _kjor(arkiv):
        modell = _Modell()
        with pytest.raises(kjoring.Kjoringsfeil) as e:
            kjoring.kjor_bunt(
                arkiv, modell, vekter={"drift": 3},
                tekst_for=lambda m, d: d.decode("utf-8"),
                biasmaalinger=_MAALINGER, antall_soknader=1)
        return e.value, modell

    # NØYAKTIG formen Codex målte: samme feltnøkkel to ganger.
    duplisert_felt = (
        '{"soknader": [{"kandidat_id": "k1", "filer": ["k1/cv.html"],'
        ' "felter": {"navn": ["Kari Testdal"],'
        ' "navn": ["Ola Nordmann"]}}]}')

    # MEKANISMEN, målt og ikke bare påstått: uten porten er `Kari` borte
    # fra deklarasjonen allerede når `json` er ferdig.
    tapt = _json.loads(duplisert_felt)["soknader"][0]["felter"]
    assert tapt == {"navn": ["Ola Nordmann"]}, tapt

    # Porten: hele bunten felles, og modellen ser ingenting.
    for i, raa in enumerate((
        duplisert_felt,
        # … og resten av dokumentet, ikke bare `felter`:
        ('{"soknader": [{"kandidat_id": "k1", "kandidat_id": "k2",'
         ' "filer": ["k1/cv.html"]}]}'),
        ('{"soknader": [{"kandidat_id": "k1", "filer": ["k1/cv.html"],'
         ' "filer": ["k1/cv.html"]}]}'),
        ('{"soknader": [{"kandidat_id": "k1", "filer": ["k1/cv.html"]}],'
         ' "soknader": [{"kandidat_id": "k1", "filer": ["k1/cv.html"]}]}'),
    )):
        (tmp_path / f"d{i}").mkdir()
        arkiv = _bunt(tmp_path / f"d{i}", [("k1/cv.html", cv)],
                      manifest=raa)
        feil, modell = _kjor(arkiv)
        assert feil.kode == "manifest_feilformet", raa
        assert not modell.sett, raa

    # POSITIV KONTROLL: porten er PER OBJEKT. To kandidater bærer hver
    # sin `kandidat_id` og `filer` — like nøkler i ULIKE objekter — og
    # det er en helt vanlig deklarasjon som fortsatt leses ut.
    (tmp_path / "ok").mkdir()
    arkiv = _bunt(tmp_path / "ok",
                  [("k1/cv.html", cv), ("k2/cv.html", cv)],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                       "felter": {"navn": ["Kari Testdal"]}},
                      {"kandidat_id": "k2", "filer": ["k2/cv.html"],
                       "felter": {"navn": ["Ola Nordmann"]}}]}))
    m = parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv))
    assert m.felter == {"k1": {"navn": ["Kari Testdal"]},
                        "k2": {"navn": ["Ola Nordmann"]}}


def test_feltgrensesettet_er_ett_predikat_begge_doerer(tmp_path):
    """Eierdom (K2-kjennelse runde 4 på #217, valg A): ETT predikat eier
    hele grensesettet, og begge dørene kaller det.

    Grensene sto før som to HÅNDSKREVNE opptellinger over samme sett —
    `les_manifest` talte sine i én løkke, `blind`s formløkke sine i en
    annen — og ingen av dem var avledet av den andre. Fire Cursor-runder
    på rad fant nøyaktig én grense som sto på den ene døra og manglet på
    den andre: padding/Cf (`7b8fa66`), lengde/antall (`4568cf09`), ukjent
    feltnavn (`be6fdd32`), tom liste/tom verdi (`f6887ce`). Den siste var
    den vonde: `all([])` og `verdiform_lukket("")` er begge `True`, så en
    tom deklarasjon ga en TOM avmaskeringstabell, og da løper
    `krev_blindet` null runder — kjøringen telles som blindet mens
    klarteksten står i modellinputen.

    Denne testen enumererer grensesettet ÉN gang og krever samme dom på
    BEGGE dørene for hver rad: den injiserte veien (`blind` direkte og
    gjennom `kjor_bunt`) og deklarasjonsveien (`soknader.json`). Bare
    feilkoden skiller dem — `ugyldig_maskeringsform` mot
    `manifest_feilformet` sier hvilken dør som felte, aldri hvilken
    grense som gjaldt.

    RUNDE 5 FLYTTET ÉN AKSE UT AV DENNE TESTEN, og det er en presisering
    av hva den noen gang målte: DIVERGENS mellom to dører, aldri
    fullstendighet i grensesettet. `verdiform_lukket`s tegnliste var
    ufullstendig i ett predikat like fullt som i to (NBSP, `U+2010`,
    NFD), og den lukkes bare der dokumentteksten finnes — se
    `test_vakuos_deklarasjon_felles_paa_effekt_per_felt`. Det som står
    igjen her er grensene begge dørene KAN måle uten teksten.

    MUTASJONEN SOM DREPER DENNE: fjern ÉN grense fra
    `blinding.feltverdier_lukket` — `bool(verdier)`, `bool(verdi)`,
    `len(verdier) <= MAKS_FELTVERDIER`, `len(verdi) <=
    MAKS_FELTVERDI_TEGN`, `isinstance`-leddene eller
    `verdiform_lukket`-kallet. Da blir raden rød på BEGGE dører samtidig,
    som er hele poenget: det finnes ikke lenger en dør som kan måle
    mindre enn den andre."""
    import json as _json

    from modules.m57_ats import kjoring

    cv = b"<p>Kari Testdal kan drift.</p>"
    GRENSER = (
        ("type: bar streng er iterbar", {"navn": "Kari"}),
        ("type: uordnet samling", {"navn": {"Kari"}}),
        ("type: verdien er ikke tekst", {"navn": [7]}),
        ("tomhet: tom liste", {"navn": []}),
        ("tomhet: tom verdi ved siden av en gyldig",
         {"navn": ["Kari", ""]}),
        ("tomhet: blank-only", {"navn": ["   "]}),
        ("antall: én over taket",
         {"navn": ["x"] * (blinding.MAKS_FELTVERDIER + 1)}),
        ("lengde: ett tegn over taket",
         {"navn": ["x" * (blinding.MAKS_FELTVERDI_TEGN + 1)]}),
        ("skrivemåte: hale", {"navn": ["Kari "]}),
        # Skrivemåten er STRUKTURELL etter runde 5 (valg B):
        # predikatet måler `verdi == verdi.strip()`, ikke
        # Unicode-kategorier. De usynlige formene er ikke borte — de
        # er flyttet til porten som faktisk lukker klassen, og den kan
        # bare måles der DOKUMENTTEKSTEN finnes:
        # `test_vakuos_deklarasjon_felles_paa_effekt_per_felt`.
        ("skrivemåte: ledende blank", {"navn": [" Kari"]}),
    )

    for i, (grense, felter) in enumerate(GRENSER):
        # Predikatet selv — den ene definisjonen begge dører deler.
        assert not blinding.feltverdier_lukket(felter["navn"]), grense

        # DØR 1, den injiserte (`kandidatfelter_for` → `blind`): den går
        # utenom manifestlesingen helt.
        with pytest.raises(blinding.Blindingsfeil) as e:
            blinding.blind("Kari Testdal kan drift.", felter)
        assert e.value.kode == "ugyldig_maskeringsform", grense
        (tmp_path / f"i{i}").mkdir()
        arkiv = _bunt(tmp_path / f"i{i}", [("k1/cv.html", cv)])
        modell = _Modell()
        with pytest.raises(kjoring.Kjoringsfeil) as e:
            kjoring.kjor_bunt(
                arkiv, modell, vekter={"drift": 3},
                tekst_for=lambda m, d: d.decode("utf-8"),
                biasmaalinger=_MAALINGER, antall_soknader=1,
                kandidatfelter_for=lambda m, f=felter: f)
        assert e.value.kode == "ugyldig_maskeringsform", grense
        assert not modell.sett, grense

        # DØR 2, deklarasjonen. Et `set` finnes ikke i JSON, så den raden
        # kan ikke NÅ denne døra — den hoppes over her framfor å bli
        # skrevet om til noe annet enn grensen den måler.
        try:
            manifest = _json.dumps({"soknader": [
                {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                 "felter": felter}]})
        except TypeError:
            continue
        (tmp_path / f"d{i}").mkdir()
        arkiv = _bunt(tmp_path / f"d{i}", [("k1/cv.html", cv)],
                      manifest=manifest)
        modell = _Modell()
        with pytest.raises(kjoring.Kjoringsfeil) as e:
            kjoring.kjor_bunt(
                arkiv, modell, vekter={"drift": 3},
                tekst_for=lambda m, d: d.decode("utf-8"),
                biasmaalinger=_MAALINGER, antall_soknader=1)
        assert e.value.kode == "manifest_feilformet", grense
        # Avvisningen skjer i LESINGEN: ingen byte nådde modellen.
        assert not modell.sett, grense

    # POSITIV KONTROLL: nøyaktig PÅ grensen er lovlig — predikatet
    # avviser det som er over, ikke det kontrakten lover. Begge
    # sekvensformene går, og maskeringen skjer som før.
    assert blinding.feltverdier_lukket(("Kari",))
    assert blinding.feltverdier_lukket(
        ["x" * blinding.MAKS_FELTVERDI_TEGN] * blinding.MAKS_FELTVERDIER)
    blindet, avmaskering = blinding.blind(
        "Kari kan drift.",
        {"navn": ["Kari"] + ["y"] * (blinding.MAKS_FELTVERDIER - 1)})
    assert avmaskering["[NAVN-1]"] == "Kari" and "Kari" not in blindet

    # … og deklarasjonsdøra leser den samme formen ut slik den står.
    (tmp_path / "ok").mkdir()
    arkiv = _bunt(tmp_path / "ok", [("k1/cv.html", cv)],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                       "felter": {"navn": ["Kari Testdal"]}}]}))
    m = parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv))
    assert m.felter == {"k1": {"navn": ["Kari Testdal"]}}


def test_en_bokstavs_deklarasjon_er_lovlig(tmp_path):
    """Eierdom (K2-kjennelsen på #217, valg A): en verdi som er
    DELSTRENG av tokenalfabetet er fortsatt en lovlig deklarasjon.

    Runde 4 svarte på tokenkollisjonen med en sonde i `verdiform_lukket`
    som avviste enhver verdi som er delstreng av et token. Kuren rammer
    normaltilfellet og gjør det diskriminerende: `kjonn: ["K"]` blir
    ulovlig mens `["M"]` er lovlig, de fjorten versalene tokennavnene
    er satt sammen av bannlyses, og én-bokstavs initialer blir umulige
    å deklarere. Kollisjonen den skulle lukke er derimot
    FAIL-CLOSED og sjelden — verste utfall er at en KORREKT maskert
    tekst felles, aldri at klartekst slipper gjennom (målt nederst) — så
    vakten hadde feil fortegn. Den ekte lukkingen er strukturell
    blinding med disjunkt tokenalfabet, og den eies av #158.

    MUTASJONEN SOM DREPER DENNE: gjeninnfør tokensonden i
    `verdiform_lukket` (avvis verdier som `_monster(verdi)` finner i et
    `[{FELT}-{nr}]`-token). Da blir `N`, `K` og `1` `ugyldig_maskerings-
    form` i `blind`, `manifest_feilformet` i deklarasjonsdøra, og hver
    bolk under blir rød."""
    import json as _json

    from modules.m57_ats import kjoring

    ENBOKSTAVS = {"navn": ["N"], "kjonn": ["K"], "alder": ["1"]}

    # Formporten måler at verdien er SIN EGEN skrivemåte, og ikke noe
    # mer: ingen bokstav er bannlyst fordi et token også bruker den.
    for verdi in ("N", "K", "1", "M", "Å", "[NAVN-1]"):
        assert blinding.verdiform_lukket(verdi), verdi

    # Deklarasjonsdøra: manifestet leser verdiene ut slik de står.
    (tmp_path / "d").mkdir()
    arkiv = _bunt(tmp_path / "d", [("k1/cv.html", b"<p>drift</p>")],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                       "felter": ENBOKSTAVS}]}))
    m = parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv))
    assert m.felter == {"k1": ENBOKSTAVS}

    # Den injiserte døra sier det samme: formløkka slipper alle tre inn,
    # og hver av dem får sitt token. Teksten SKRIVER dem, for etter runde
    # 5 er en deklarasjon som ikke treffer noe en vakuøs deklarasjon.
    #
    # De nøstede tokenene under er tokenkollisjonen selv, målt i stedet
    # for bare beskrevet: `1` treffer nummerhalen i tokenene nabofeltene
    # nettopp la inn. Utfallet er `maskert_felt_i_modellinput` nederst i
    # testen — en NEKTET evaluering, aldri en lekkasje — og klassen eies
    # av #158 (disjunkt tokenalfabet), ikke av en formport her.
    blindet, avmaskering = blinding.blind("N K 1 drift.", ENBOKSTAVS)
    assert avmaskering == {"[NAVN-1]": "N", "[KJONN-1]": "K",
                           "[ALDER-1]": "1"}
    assert blindet == "[NAVN-[ALDER-1]] [KJONN-[ALDER-1]] [ALDER-1] drift."

    # Og verdien maskeres faktisk der den står. (Ett felt om gangen: at
    # `1` også treffer nummerhalen i et token nabofeltet nettopp la inn,
    # er delstrengserstatningen i #158 — en annen klasse enn denne.)
    blindet, _ = blinding.blind("K, drift.", {"kjonn": ["K"]})
    assert blindet == "[KJONN-1], drift."

    # Hele veien igjennom: en bunt deklarert med en én-bokstavs verdi
    # evalueres, den stopper ikke i porten. Bokstaven er `M` — like mye
    # én bokstav som `K`, men UTENFOR tokenalfabetet, så E2E måler
    # lovligheten og ikke kollisjonen (den står målt rett under).
    (tmp_path / "e").mkdir()
    e2e = _bunt(tmp_path / "e", [("k1/cv.html", b"<p>M kan drift</p>")],
                manifest=_json.dumps({"soknader": [
                    {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                     "felter": {"navn": ["M"]}}]}))
    modell = _Modell()
    ut = kjoring.kjor_bunt(
        e2e, modell, vekter={"drift": 3},
        tekst_for=lambda m, d: d.decode("utf-8"),
        biasmaalinger=_MAALINGER, antall_soknader=1)
    assert set(ut["artefakter"]) == {"k1"} and modell.sett
    assert all("[NAVN-1]" in t for t in modell.sett)

    # GRENSEN, målt så fortegnet er dokumentert (KONTRAKT.md, #158):
    # står verdien igjen INNI sitt eget token, feller port 16 en tekst
    # der klarteksten faktisk ER borte. Det er en vakuøs feller — en
    # nektet evaluering — ikke en lekkasje, og derfor tåler den å vente
    # på det disjunkte tokenalfabetet.
    with pytest.raises(blinding.Blindingsfeil) as e:
        blinding.krev_blindet("[KJONN-1], drift.", {"[KJONN-1]": "K"})
    assert e.value.kode == "maskert_felt_i_modellinput"
    assert "K" not in "[KJONN-1], drift.".replace("[KJONN-1]", "")


def test_ukjent_maskeringsfelt_er_en_maalt_negativ(tmp_path):
    """Cursor P2: `ukjent_maskeringsfelt` fantes KUN i sin egen `raise`.

    Ingen test i repoet traff koden. Maskeringsløkka itererer bare
    `MASKERTE_FELTER`, så uten `ukjente`-vakten går
    `{"navn": [...], "personnummer": [...]}` gjennom: bare `navn`
    maskeres, personnummeret blir stående i klartekst, og `krev_blindet`
    ser bare avmaskeringstabellen — altså det som FAKTISK ble maskert.
    Kjøringen telles som blindet med fødselsnummeret i modellinputen.

    Lukket sett betyr lukket: et felt vi ikke har lovet å maskere, er
    ingen deklarasjon vi kan oppfylle. Vi avviser, vi ignorerer ikke.

    MUTASJONEN SOM DREPER DENNE: slett `ukjente`-vakten i
    `blinding.blind` — da blir denne testen rød, og ingen annen i suiten.

    OG ÆRLIG OM HVA MUTASJONEN VISER, fordi det ble MÅLT og ikke antatt:
    lekkasjen over er historisk. Etter runde 5 er et ukjent felt per
    konstruksjon et felt som aldri kan treffe — maskeringsløkka rører
    bare `MASKERTE_FELTER` — så vakuøsitetsporten feller det samme kartet
    som `ugyldig_maskeringsform` selv uten vakten. Målt:

        umutert:                ukjent_maskeringsfelt   | modellinput: -
        uten `ukjente`-vakten:  ugyldig_maskeringsform  | modellinput: -

    Vakten er altså ikke lenger det som stopper lekkasjen; den er det som
    gjør utfallet PRESIST. Koden sier «du deklarerte et felt vi ikke
    maskerer», ikke «deklarasjonen din traff ingenting» — og en drift som
    skal rette buntsiden trenger den forskjellen. Negativen her låser
    både koden og det lukkede settet.
    """
    import json as _json

    from modules.m57_ats import kjoring

    BLANDET = {"navn": ["Kari Testdal"], "personnummer": ["01012012345"]}
    CV = b"<p>Kari Testdal, 01012012345, kan drift</p>"

    with pytest.raises(blinding.Blindingsfeil) as e:
        blinding.blind(CV.decode("utf-8"), BLANDET)
    assert e.value.kode == "ukjent_maskeringsfelt"

    # Den INJISERTE veien går utenom manifestet, og porten står der òg —
    # som et KODET utfall gjennom kjøringen, aldri en rå Blindingsfeil.
    (tmp_path / "inj").mkdir()
    arkiv = _bunt(tmp_path / "inj", [("k1/cv.html", CV)])
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                          kandidatfelter_for=lambda m: BLANDET,
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "ukjent_maskeringsfelt"
    assert modell.sett == [], "personnummeret nadde modellen"

    # Deklarasjonsdøra feller samme kart med sin egen kode — koden sier
    # hvilken DØR som felte, aldri hvilken grense som gjaldt.
    (tmp_path / "dek").mkdir()
    arkiv = _bunt(tmp_path / "dek", [("k1/cv.html", CV)],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "k1", "filer": ["k1/cv.html"],
                       "felter": BLANDET}]}))
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv))
    assert e.value.kode == "manifest_feilformet"

    # POSITIV KONTROLL: hvert felt i det lukkede settet er lovlig alene,
    # så porten avviser det UKJENTE og ikke det katalogen lover.
    for felt in blinding.MASKERTE_FELTER:
        _, avmaskering = blinding.blind(
            "verdi-for-" + felt + " i teksten",
            {felt: ["verdi-for-" + felt]})
        assert avmaskering == {"[" + felt.upper() + "-1]":
                               "verdi-for-" + felt}


def test_manifestets_lukkede_form_avviser_alt_annet(tmp_path):
    """#161: en deklarasjon vi ikke forstår FULLT UT er ingen
    deklarasjon — ukjente nøkler, feil typer, duplikater, tomme og
    overfylte lister er alle `manifest_feilformet`.

    `kandidat_id` er i tillegg LUKKET (eierdom, K2-kjennelsen på #216 —
    valg A): `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`. En port som teller opp
    tegnklasser å avvise, lukker aldri «to ID-er som ser like ut»; en
    som sier hva den GODTAR, gjør det i én dom.

    MUTASJONEN SOM DREPER DENNE: bytt `KANDIDAT_ID_KANON.fullmatch(kid)`
    tilbake til `kid.strip() and kid == kid.strip()` — ZWSP-, RTL-,
    æøå-, homoglyf- og lengderadene blir da lovlige deklarasjoner.
    """
    import json as _json

    def _sjekk(manifest, undermappe):
        (tmp_path / undermappe).mkdir()
        arkiv = _bunt(tmp_path / undermappe,
                      [("k1/cv.html", b"<p>x</p>")], manifest=manifest)
        medlemmer = parsing.inspiser_bunt(arkiv)
        with pytest.raises(parsing.Buntfeil) as e:
            parsing.les_manifest(arkiv, medlemmer)
        return e.value.kode

    god = {"kandidat_id": "k1", "filer": ["k1/cv.html"]}
    for i, (manifest, ventet) in enumerate((
        ("ikke json i det hele tatt", "manifest_feilformet"),
        (_json.dumps(["liste"]), "manifest_feilformet"),
        (_json.dumps({"soknader": [], "ekstra": 1}), "manifest_feilformet"),
        (_json.dumps({"soknader": []}), "manifest_feilformet"),
        (_json.dumps({"soknader": [{**god, "ekstra": 1}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "", "filer":
                                    ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [god, god]}), "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "k1", "filer": []}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "k1",
                                    "filer": ["soknader.json"]}]}),
         "manifest_feilformet"),
        # BLANKTEGN ER IKKE EN IDENTITET: `strip()`-porten står i koden,
        # men den tomme strengen var eneste rad som målte den.
        (_json.dumps({"soknader": [{"kandidat_id": "   ",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        # Øvre kandidatgrense: 5001 er avvist ved deklarasjonen, aldri
        # stille avkortet (samme hardhet som `antall_soknader` i
        # bestillingen). Radene måles ikke — tallporten står foran dem.
        (_json.dumps({"soknader": [{"kandidat_id": f"k{n}",
                                    "filer": [f"k{n}/cv.html"]}
                                   for n in range(5001)]}),
         "manifest_feilformet"),
        # Duplikat INNI én rad: `navn in kart` er global, så den samme
        # fila to ganger for samme kandidat er like feilformet som delt
        # mellom to kandidater.
        (_json.dumps({"soknader": [{"kandidat_id": "k1", "filer":
                                    ["k1/cv.html", "k1/cv.html"]}]}),
         "manifest_feilformet"),
        # TYPENE ER EN DEL AV DEN LUKKEDE FORMEN: en `kandidat_id` som
        # tall, en `filer` som streng (som ville itererert TEGNVIS), og
        # et filnavn som tall.
        (_json.dumps({"soknader": [{"kandidat_id": 1,
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "k1",
                                    "filer": "k1/cv.html"}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "k1", "filer": [1]}]}),
         "manifest_feilformet"),
        # DYBDE ER OGSÅ FORM: `json` melder syntaks som `ValueError`, men
        # nøsting som `RecursionError` — som ikke er en `ValueError`. Noen
        # tusen `[` er 200 kB, godt innenfor det 4 MiB-taket.
        ("[" * 100_000 + "]" * 100_000, "manifest_feilformet"),
        # BLANKTEGN RUNDT EN IDENTITET ER OGSÅ BLANKTEGN: porten målte
        # `kid.strip()`, men lagret råverdien, så `"k1 "` var en egen
        # lovlig kandidat ved siden av `"k1"`. Vi avviser, ikke
        # kanoniserer — én vei inn.
        (_json.dumps({"soknader": [{"kandidat_id": "k1 ",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        # DEN LUKKEDE ASCII-KANONEN (eierdom, valg A på #216). `strip()`
        # rører ikke Cf, så U+200B var et lovlig tegn i en lovlig ID —
        # og etter den sto RTL, NFKC og homoglyfer i kø. Kanonen sier
        # hva vi GODTAR, og lukker dermed hele klassen: usynlige
        # format-tegn, høyre-mot-venstre-markør, æøå og kyrillisk `а`
        # (U+0430, homoglyf for `a`) er alle utenfor.
        (_json.dumps({"soknader": [{"kandidat_id": "k1\u200b",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "k1\u202e",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "kå1",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "а1",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        # …og formens tre egne kanter: første tegn må være alfanumerisk
        # (ellers er `.`/`-`/`_` en sti- eller flaggform i forkledning),
        # skilletegn utenfor `._-` er ute, og 65 tegn er ett for mange.
        (_json.dumps({"soknader": [{"kandidat_id": ".k1",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "-k1",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "k1/k2",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        (_json.dumps({"soknader": [{"kandidat_id": "k" * 65,
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
        # NYLINJE ER IKKE EN AVSLUTNING: `$` ville godtatt `"k1\n"` som
        # en lovlig ID (og gitt oss `"k1"`-tvillingen tilbake gjennom
        # bakdøren). Porten bruker `fullmatch`, så den finnes ikke.
        (_json.dumps({"soknader": [{"kandidat_id": "k1\n",
                                    "filer": ["k1/cv.html"]}]}),
         "manifest_feilformet"),
    )):
        assert _sjekk(manifest, f"m{i}") == ventet, (i, manifest)

    # POSITIV KONTROLL PÅ KANONEN: en port som avviser alt er ingen
    # port. Hele det lovede tegnsettet, akkurat 64 tegn, og et
    # skilletegn i hver lovlig form.
    for lovlig in ("k1", "K-1.v2_a", "9" + "a" * 63):
        assert parsing.KANDIDAT_ID_KANON.fullmatch(lovlig), lovlig
    (tmp_path / "lovlig").mkdir()
    arkiv = _bunt(tmp_path / "lovlig", [("k1/cv.html", b"<p>x</p>")],
                  manifest=_json.dumps({"soknader": [
                      {"kandidat_id": "K-1.v2_a",
                       "filer": ["k1/cv.html"]}]}))
    assert parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv)).kart \
        == {"k1/cv.html": "K-1.v2_a"}

    # …og de to identitetene for samme kandidat, side om side: uten
    # porten er dette en LOVLIG deklarasjon med to kandidater, og
    # `antall_soknader = 2` ville stemt (Cursor P2, runde 3 og 5 —
    # blanktegnstvillingen og ZWSP-tvillingen er samme klasse, og
    # kanonen feller dem begge).
    for nr, tvilling_id in enumerate(("k1 ", "k1\u200b")):
        (tmp_path / f"tvilling{nr}").mkdir()
        tvilling = _bunt(tmp_path / f"tvilling{nr}",
                         [("k1/cv.html", b"<p>x</p>"),
                          ("k1/brev.html", b"<p>y</p>")],
                         manifest=_json.dumps({"soknader": [
                             {"kandidat_id": "k1", "filer": ["k1/cv.html"]},
                             {"kandidat_id": tvilling_id,
                              "filer": ["k1/brev.html"]}]}))
        with pytest.raises(parsing.Buntfeil) as e:
            parsing.les_manifest(tvilling, parsing.inspiser_bunt(tvilling))
        assert e.value.kode == "manifest_feilformet", (tvilling_id,
                                                       e.value.kode)

    # …og fraværet har sin egen kode.
    (tmp_path / "uten").mkdir()
    arkiv = _bunt(tmp_path / "uten", [("k1/cv.html", b"<p>x</p>")],
                  manifest=None)
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.les_manifest(arkiv, parsing.inspiser_bunt(arkiv))
    assert e.value.kode == "manifest_mangler"


def test_manifesttaket_er_en_maalt_port_ikke_bare_en_linje(tmp_path):
    """Cursor P2 på #161: `MAKS_MANIFESTBYTES` sto i gaten uten én eneste
    negativ test — mutasjonen som sletter `file_size`-sjekken for
    `soknader.json` overlevde hele suiten, mens naboportene
    (`komprimeringsforhold`, `null compress`) har sine egne. En port
    ingen måler er en port som forsvinner i neste refaktorering.

    Taket måles på KATALOGENS påstand, som resten av gaten: det er den
    som avgjør om vi i det hele tatt trekker bytene ut.

    MUTASJONEN SOM DREPER DENNE: fjern `info.file_size >
    MAKS_MANIFESTBYTES`-sjekken i `inspiser_bunt`.
    """
    filer = [("k1/cv.html", b"<p>drift</p>")]

    # Én byte over taket: avvist ved deklarasjonen, aldri utpakket.
    arkiv = _bunt(tmp_path, filer)
    _patch_deklarert(arkiv, b"soknader.json",
                     parsing.MAKS_MANIFESTBYTES + 1)
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "manifest_feilformet", e.value.kode

    # Kontroll: NØYAKTIG taket er grønt — porten måler «over», ikke «nær».
    (tmp_path / "taket").mkdir()
    arkiv = _bunt(tmp_path / "taket", filer)
    _patch_deklarert(arkiv, b"soknader.json", parsing.MAKS_MANIFESTBYTES)
    assert any(m.navn == "soknader.json"
               for m in parsing.inspiser_bunt(arkiv))


def test_manifesttaket_maales_paa_bytene_ikke_bare_paastanden(tmp_path):
    """Cursor P2 (runde 4) på #161: taket sto BARE på katalogpåstanden.

    `inspiser_bunt` måler `info.file_size`; `les_manifest` gjorde
    `zf.read` uten å måle det den faktisk fikk. For SØKNADSINNHOLD stoler
    strømmen bevisst ikke på katalogen — `lest > MAKS_ENKELTFIL` måles på
    de utpakkede bytene — og deklarasjonsarmen manglet den speilingen.

    Katalogen `inspiser_bunt` leste er ikke nødvendigvis den `zf.read`
    åpner: fila kan være byttet i vinduet, eller `medlemmer` komme fra en
    annen lesning. Det er samme divergens `manifest_mangler`-porten
    finnes for, og her er formen målt med samme rigg — en medlemsliste
    som PÅSTÅR en liten deklarasjon over en bunt som bærer en for stor.

    (En katalog som bare lyver NEDOVER er alt dekket: `zipfile` trunkerer
    da strømmen på den deklarerte lengden og feller CRC-en, altså
    `korrupt_bunt` — se `test_en_lognaktig_katalog_er_en_korrupt_bunt`.
    Denne porten er den som står igjen når bytene faktisk kommer ut.)

    MUTASJONEN SOM DREPER DENNE: fjern `len(raa) > MAKS_MANIFESTBYTES`
    i `les_manifest` — da er den for store deklarasjonen en LOVLIG bunt.
    """
    import json as _json

    # Ærlig, gyldig JSON — bare for stor. Uten porten går den rett
    # gjennom og returnerer et kart, så mutasjonen har ingen annen død.
    kropp = _json.dumps({"soknader": [{"kandidat_id": "k1",
                                       "filer": ["k1/cv.html"]}]})
    over = kropp + " " * (parsing.MAKS_MANIFESTBYTES + 1 - len(kropp))
    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>drift</p>")],
                  manifest=over)
    # Medlemslista er en ANNEN lesnings katalog: den påstår en
    # deklarasjon innenfor taket, så gatens egen sjekk aldri feller den.
    pastand = [parsing.Medlem("soknader.json", 64),
               parsing.Medlem("k1/cv.html", 12)]
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.les_manifest(arkiv, pastand)
    assert e.value.kode == "manifest_feilformet", e.value.kode

    # Kontroll: NØYAKTIG taket er grønt — porten måler «over», ikke «nær».
    (tmp_path / "taket").mkdir()
    paa = kropp + " " * (parsing.MAKS_MANIFESTBYTES - len(kropp))
    arkiv = _bunt(tmp_path / "taket", [("k1/cv.html", b"<p>drift</p>")],
                  manifest=paa)
    assert parsing.les_manifest(arkiv, pastand).kart == {"k1/cv.html": "k1"}


def test_manifestet_har_ikke_fritak_fra_null_komprimert(tmp_path):
    """Cursor P2 (runde 3) på #161: manifest-armen målte bare taket og
    gikk `continue` — bombe-armen som feller `compress_size = 0` sto
    NEDENFOR, og deklarasjonen var dermed den ene oppføringen i bunten
    som kunne påstå innhold uten komprimert størrelse. Samme hull som
    `test_port21_null_komprimert_er_ikke_fritak` lukket for søknadene,
    gjenåpnet for fila som er billigst å forme ondsinnet.

    MUTASJONEN SOM DREPER DENNE: fjern `compress_size <= 0`-armen på
    manifest-grenen i `inspiser_bunt`.
    """
    filer = [("k1/cv.html", b"<p>drift</p>")]

    # Under taket, men null komprimert: uendelig forhold, ikke en
    # ukomprimert fil.
    arkiv = _bunt(tmp_path, filer)
    _patch_deklarert(arkiv, b"soknader.json", 1024, komprimert=0)
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "komprimeringsforhold", e.value.kode

    # Kontroll: den ÆRLIGE deklarasjonen komprimerer godt over 100:1 og
    # skal fortsatt gjennom — det er null-armen som speiles, ikke taket.
    (tmp_path / "aerlig").mkdir()
    arkiv = _bunt(tmp_path / "aerlig", filer)
    _patch_deklarert(arkiv, b"soknader.json", 1024 * 1024, komprimert=1)
    assert any(m.navn == "soknader.json"
               for m in parsing.inspiser_bunt(arkiv))


def test_manifest_som_forsvant_mellom_lesningene_er_kodet(tmp_path,
                                                          monkeypatch):
    """Cursor P1 (runde 2) på #161: `les_manifest` måler `manifest_mangler`
    mot `medlemmer` — en katalog EN ANNEN lesning laget — men trekker
    bytene ut av et arkiv den åpner PÅ NYTT. Er `soknader.json` borte der
    (fila byttet i vinduet, eller en medlemsliste fra en annen bunt),
    reiste `zf.read` en rå `KeyError` som `kjor_bunt`s catch-all meldte
    som `modellfeil` — feil kø, feil alarm, om en bunt modellen aldri fikk
    se. Fraværet av en deklarasjon er `manifest_mangler`, uansett hvilken
    av de to lesningene som oppdager det.

    MUTASJONEN SOM DREPER DENNE: fjern `except KeyError`-porten.
    """
    from modules.m57_ats import kjoring

    # Bunten har INGEN deklarasjon; medlemslista påstår at den har det.
    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>drift</p>")],
                  manifest=None)
    pastand = [parsing.Medlem("soknader.json", 64),
               *parsing.inspiser_bunt(arkiv)]
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.les_manifest(arkiv, pastand)
    assert e.value.kode == "manifest_mangler", e.value.kode

    # …og hele veien gjennom kjøringen: kodet utfall, aldri `modellfeil`,
    # og modellen ser ingenting.
    monkeypatch.setattr(parsing, "inspiser_bunt", lambda sti: pastand)
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(
            arkiv, modell, vekter={"drift": 3},
            kandidatfelter_for=lambda m: {"navn": ["N"]},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "manifest_mangler", e.value.kode
    assert modell.sett == [], "modellen så en bunt uten deklarasjon"


def test_medlem_som_forsvant_mellom_lesningene_er_kodet(tmp_path,
                                                        monkeypatch):
    """Cursor P1 på #161: SISTE stedet i klassen `les_manifest`s
    `KeyError`-port og `kart.get` i `kjoring.py` alt lukker.

    `inspiser_bunt` bygger medlemslista fra ÉN åpning av arkivet, og
    `les_porsjonsvis` åpner det PÅ NYTT og slår `medlem.navn` opp i DET
    navnekartet. Divergerer de to — fila byttet i vinduet mellom dem —
    reiser `zf.open` en rå `KeyError`, og ingen av medlems-armene kjente
    den: de fanger `BadZipFile`, zlib/LZMA, `OSError` og
    `RuntimeError`/`NotImplementedError`. Den falt dermed til
    `kjor_bunt`s catch-all og ble `modellfeil` — feil kø, feil alarm, om
    en bunt modellen aldri fikk se.

    MUTASJONEN SOM DREPER DENNE: fjern `except KeyError`-porten i
    `les_porsjonsvis` — da blir utfallet `modellfeil`.
    """
    from modules.m57_ats import kjoring

    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>drift</p>")])
    ekte_open = zipfile.ZipFile.open

    def _borte(self, navn, *a, **kw):
        # Bare UTTREKKET av medlemmet forsvinner. `infolist()` og
        # `zf.read(MANIFESTNAVN)` står urørt, så deklarasjonen leses og
        # bindes som normalt — divergensen finnes bare i strømmens
        # navnekart, nøyaktig slik et bytte i vinduet ser ut.
        if navn == "k1/cv.html":
            raise KeyError(
                "There is no item named 'k1/cv.html' in the archive")
        return ekte_open(self, navn, *a, **kw)

    monkeypatch.setattr(zipfile.ZipFile, "open", _borte)

    # Direkte på strømmen: kodet utfall, aldri en rå `KeyError`.
    with pytest.raises(parsing.Buntfeil) as e:
        list(parsing.les_porsjonsvis(arkiv))
    assert e.value.kode == "manifest_medlem_mangler", e.value.kode

    # …og hele veien gjennom kjøringen: fremdriften står som evidens, og
    # modellen ser ingenting.
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(
            arkiv, modell, vekter={"drift": 3},
            kandidatfelter_for=lambda m: {"navn": ["N"]},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "manifest_medlem_mangler", e.value.kode
    assert modell.sett == [], "modellen så en bunt uten medlemsbyte"
    assert e.value.fremdrift, "utfallet bar ingen fremdrift som evidens"


def test_manifestlesingen_er_kodet_utfall_ikke_modellfeil(tmp_path):
    """Cursor P1 på #161: `les_manifest` leste `soknader.json` UTEN
    SP-3-oversetteren `les_porsjonsvis` har. En CRC-skadet eller kryptert
    deklarasjon feller `zf.read` med bibliotekets egen form, og den boblet
    rå til `kjor_bunt`s catch-all — altså `modellfeil`, om en bunt
    modellen aldri fikk se. Lagring/dekompresjon er ikke modellen."""
    from modules.m57_ats import kjoring

    def _kjor(arkiv, modell):
        return kjoring.kjor_bunt(
            arkiv, modell, vekter={"drift": 3},
            kandidatfelter_for=lambda m: {"navn": ["N"]},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)

    filer = [(f"k1/{n}.html", b"<p>drift</p>") for n in ("cv", "brev")]

    # Skaden ligger i MANIFESTETS komprimerte strøm — søknadene er hele,
    # og katalogen lyver ikke, så gaten slipper bunten inn som før.
    arkiv = _bunt(tmp_path, filer)
    _skad_payload(arkiv, b"soknader.json")
    parsing.inspiser_bunt(arkiv)
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(arkiv, modell)
    assert e.value.kode == "korrupt_bunt", e.value.kode
    assert modell.sett == [], "modellen så en bunt vi aldri kunne lese"

    # Et kryptert manifest er ULESELIG, ikke korrupt — og fortsatt kodet.
    (tmp_path / "kryptert").mkdir()
    arkiv = _bunt(tmp_path / "kryptert", filer)
    _patch_kryptert(arkiv)
    parsing.inspiser_bunt(arkiv)
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(arkiv, modell)
    assert e.value.kode == "uleselig_medlem", e.value.kode
    assert modell.sett == []

    # …og en DYBDE vi ikke kommer gjennom er samme sak: `json` melder
    # nøsting som `RecursionError`, ikke `ValueError`, så den gikk rått
    # forbi formporten til catch-allen. Deklarasjonen er den billigste
    # delen av bunten å forme ondsinnet — feil kø her er feil kø for et
    # angrep. Gaten slipper den inn (200 kB mot et tak på 4 MiB).
    # MUTASJONEN SOM DREPER DENNE: fjern `RecursionError` fra fangsten.
    (tmp_path / "dyp").mkdir()
    arkiv = _bunt(tmp_path / "dyp", filer,
                  manifest="[" * 100_000 + "]" * 100_000)
    parsing.inspiser_bunt(arkiv)
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(arkiv, modell)
    assert e.value.kode == "manifest_feilformet", e.value.kode
    assert modell.sett == []


def test_umatchet_medlem_i_strommen_er_kodet_ikke_modellfeil(tmp_path,
                                                             monkeypatch):
    """Cursor P2 på #161: toveisbindingen måles mot `inspiser_bunt`s
    katalog, mens `les_porsjonsvis` åpner arkivet PÅ NYTT. Divergerer de to
    lesningene — TOCTOU på fila, eller intern inkonsistens — traff
    `kart[medlem.navn]` en `KeyError`, og catch-allen meldte `modellfeil`
    om en bunt modellen aldri fikk se. Et umatchet medlem er nettopp det
    `medlem_uadressert` finnes for."""
    from modules.m57_ats import kjoring

    def _kjor(arkiv, modell):
        return kjoring.kjor_bunt(
            arkiv, modell, vekter={"drift": 3},
            kandidatfelter_for=lambda m: {"navn": ["Kari"]},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)

    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>Kari kan drift</p>"),
                             ("k1/brev.html", b"<p>mer drift</p>")])
    # Positiv kontroll: bunten er hel, og begge medlemmene er adressert.
    assert set(_kjor(arkiv, _Modell())["artefakter"]) == {"k1"}

    # Kartet mister ett medlem ETTER bindingen — nøyaktig det strømmen
    # ville sett om arkivet ble byttet i vinduet mellom de to lesningene.
    # Tallporten foran strømmen ser fortsatt én kandidat og slipper
    # gjennom; divergensen dukker først opp per medlem.
    ekte = parsing.les_manifest

    def _amputert(sti, medlemmer):
        m = ekte(sti, medlemmer)
        return parsing.Manifestet(
            kart={n: k for n, k in m.kart.items()
                  if n != "k1/brev.html"},
            felter=m.felter)

    monkeypatch.setattr(parsing, "les_manifest", _amputert)
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(arkiv, modell)
    assert e.value.kode == "medlem_uadressert", e.value.kode
    assert e.value.fremdrift, "evidensen mangler i utfallet"
    assert modell.sett == [], "modellen så en bunt vi ikke kunne adressere"


def test_deklarert_medlem_som_aldri_strommes_stopper_kjoringen(tmp_path,
                                                               monkeypatch):
    """Cursor P2 på #161: toveisbindingen var TOVEIS ved lesing, men bare
    ÉN vei midt i flukt. Et medlem strømmen har og kartet mangler ble
    felt; det OMVENDTE — kartet deklarerer filer strømmen aldri yielder —
    hadde ingen måling. Mister arkivet et deklarert medlem i vinduet
    mellom bindingen og `les_porsjonsvis`, mens hver kandidat beholder
    minst én fil, treffer `len(biter)` fortsatt `antall_soknader`:
    kjøringen LYKTES, og kandidaten ble evaluert på et halvt
    dokumentsett. Et ufullstendig dokumentsett er ikke et resultat.

    MUTASJONEN SOM DREPER DENNE: fjern `lest != len(kart)`-porten.
    """
    from modules.m57_ats import kjoring

    def _kjor(arkiv, modell):
        return kjoring.kjor_bunt(
            arkiv, modell, vekter={"drift": 3},
            kandidatfelter_for=lambda m: {"navn": ["Kari"]},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)

    arkiv = _bunt(tmp_path, [("k1/cv.html", b"<p>Kari kan drift</p>"),
                             ("k1/brev.html", b"<p>mer drift</p>")])
    # Positiv kontroll: hel bunt, begge medlemmene strømmes, porten tier.
    assert set(_kjor(arkiv, _Modell())["artefakter"]) == {"k1"}

    # Strømmen mister ett DEKLARERT medlem etter bindingen. Kandidaten
    # står igjen med én fil, så kandidattallporten ser en gyldig bunt —
    # det er nettopp den stille veien porten finnes for. (Denne testen
    # måler DELVIS tap; totalt tap måles i
    # `test_bunt_uten_kandidater_er_kodet_feil_ikke_tomt_resultat`.)
    ekte = parsing.les_porsjonsvis
    monkeypatch.setattr(
        parsing, "les_porsjonsvis",
        lambda sti: ((merke, medlem, data)
                     for merke, medlem, data in ekte(sti)
                     if medlem.navn != "k1/brev.html"))
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(arkiv, modell)
    assert e.value.kode == "manifest_medlem_mangler", e.value.kode
    assert e.value.fremdrift, "evidensen mangler i utfallet"
    assert modell.sett == [], "modellen så et halvt dokumentsett"


def test_bunt_uten_kandidater_er_kodet_feil_ikke_tomt_resultat(tmp_path,
                                                               monkeypatch):
    """Codex P2: en bunt uten medlemmer ble et VELLYKKET tomt utfall.

    En tom zip — og en som bare bærer katalogoppføringer — passerer hele
    arkivgaten og yielder ingenting. Da ble `biter` tom, evalueringssløyfa
    kjørte aldri, `ranger({}, ...)` ga en tom liste, og `kjor_bunt`
    RETURNERTE: rangering `[]`, artefakter `{}`. Oppdraget «lyktes» uten at
    én eneste søknad var vurdert, og promoteringsvakten i 056 fikk en gyldig
    tom liste å slippe videre. Payload-skjemaet sier `antall_soknader` er
    1–5000, så null kandidater er per definisjon en ugyldig bunt, og en
    ugyldig bunt er SP-3s kodede utfall.

    Klassen har nå TO porter, og `tom_bunt` er ingen av dem (eierdom,
    K2-kjennelsen på #216 — valg B): en bunt uten deklarasjon felles av
    `manifest_mangler` FØR strømmen, og forsvinner deklarerte medlemmer
    etterpå, eier `lest != len(kart)` divergensen. Vakten som sto imellom
    kunne per konstruksjon aldri fyre riktig, og er fjernet.

    MUTASJONEN SOM DREPER DENNE: fjern `lest != len(kart)`-porten i
    `kjor_bunt` — da blir TOTALT tap `kandidattall_avvik` (defensen bak),
    ikke `manifest_medlem_mangler`.
    """
    from modules.m57_ats import kjoring

    def _kjor(arkiv, modell):
        return kjoring.kjor_bunt(
            arkiv, modell, vekter={"drift": 3},
            kandidatfelter_for=lambda m: {"navn": ["Kari"]},
            tekst_for=lambda m, d: d.decode("utf-8"),
            biasmaalinger=_MAALINGER, antall_soknader=1)

    tom = tmp_path / "tom.zip"
    with zipfile.ZipFile(tom, "w") as zf:
        pass
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(tom, modell)
    # #161: en bunt uten deklarasjon felles som `manifest_mangler` FØR
    # strømmen — strengere enn gamle `tom_bunt`, samme klasse (aldri et
    # vellykket tomt utfall), og nå frontdøren alene.
    assert e.value.kode == "manifest_mangler"
    # Utfallet bærer ingen halv liste — evidensen er alt Kjoringsfeil har.
    assert not hasattr(e.value, "rangering")
    assert modell.sett == []

    # Bare katalogoppføringer er samme sak: gaten går gjennom, og strømmen
    # leverer ingen medlemmer.
    bare_kataloger = tmp_path / "kataloger.zip"
    with zipfile.ZipFile(bare_kataloger, "w") as zf:
        zf.writestr(zipfile.ZipInfo("k1/"), b"")
        zf.writestr(zipfile.ZipInfo("k2/"), b"")
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(bare_kataloger, _Modell())
    assert e.value.kode == "manifest_mangler"

    # … og én kandidat er nok: porten måler NULL, ikke «få».
    hel = _bunt(tmp_path, [("k1/cv.html", b"<p>Kari kan drift</p>")])
    assert set(_kjor(hel, _Modell())["artefakter"]) == {"k1"}

    # TOTALT TAP ETTER BINDINGEN er den veien `tom_bunt` stjal (Cursor P2,
    # runde 5): deklarasjonen er lest og bundet, og så yielder strømmen
    # ingenting i det hele tatt. `biter` blir tom uten at bunten var tom —
    # den DEKLARERTE én kandidat. Utfallet er derfor
    # `manifest_medlem_mangler`, koden porten alt eier for «deklarert uten
    # medlem», og ikke et ord om en tomhet manifestet motsier.
    monkeypatch.setattr(parsing, "les_porsjonsvis", lambda sti: iter(()))
    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        _kjor(hel, modell)
    assert e.value.kode == "manifest_medlem_mangler", e.value.kode
    assert modell.sett == [], "modellen så en bunt som aldri ble strømmet"


def test_ugyldig_feltform_i_SENERE_fil_felles_ogsaa(tmp_path):
    """Codex P1: flettingen skjulte en ugyldig form bak en gyldig rad.

    CV-en leverer `{"kontakt": ["k@eksempel.no"]}` — velformet, og raden
    blir en liste. Søknadsbrevet leverer så SAMME felt som en bar streng,
    `{"kontakt": "annen@eksempel.no"}`. Formen er nettopp den `blind`
    skal felle (en streng er iterbar, så hvert TEGN ville blitt maskert),
    men fordi raden alt fantes som liste, ble den senere verdien stille
    forkastet: `blind` fikk aldri se den, kunne ikke reise
    `ugyldig_maskeringsform`, og adressen som bare sto i søknadsbrevet ble
    med UMASKERT inn i den samlede teksten til modellen. Fail-closed må
    måle den VERSTE formen feltet kom i, ikke den første.

    MUTASJONEN SOM DREPER DENNE: sett betingelsen tilbake til
    `if rad is None:` i `_flett_felter`.
    """
    from modules.m57_ats import kjoring

    forst = tmp_path / "forst"
    forst.mkdir()
    arkiv = _bunt(forst, [
        ("k1/cv.html", b"<p>drift, k@eksempel.no</p>"),
        ("k1/soknad.html", b"<p>drift, annen@eksempel.no</p>"),
    ])

    def felter(medlem):
        if medlem.navn.endswith("cv.html"):
            return {"kontakt": ["k@eksempel.no"]}
        return {"kontakt": "annen@eksempel.no"}

    modell = _Modell()
    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv, modell, vekter={"drift": 3},
                          kandidatfelter_for=felter,
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "ugyldig_maskeringsform"
    # Og stoppen er FØR modellen: ingen tekst forlot kjøringen.
    assert modell.sett == []

    # Rekkefølgen skal ikke avgjøre: den ugyldige formen FØRST felles
    # like fullt, og en gyldig etterfølger vasker den ikke bort.
    omvendt = tmp_path / "omvendt"
    omvendt.mkdir()
    arkiv2 = _bunt(omvendt, [
        ("k1/a_soknad.html", b"<p>drift, annen@eksempel.no</p>"),
        ("k1/b_cv.html", b"<p>drift, k@eksempel.no</p>"),
    ])

    def felter_omvendt(medlem):
        if medlem.navn.endswith("b_cv.html"):
            return {"kontakt": ["k@eksempel.no"]}
        return {"kontakt": "annen@eksempel.no"}

    with pytest.raises(kjoring.Kjoringsfeil) as e:
        kjoring.kjor_bunt(arkiv2, _Modell(), vekter={"drift": 3},
                          kandidatfelter_for=felter_omvendt,
                          tekst_for=lambda m, d: d.decode("utf-8"),
                          biasmaalinger=_MAALINGER, antall_soknader=1)
    assert e.value.kode == "ugyldig_maskeringsform"
