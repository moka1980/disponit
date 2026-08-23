"""M-57-modulen: arkivgaten (portene 21–26), innhold og modell (13–17)
og 5000-taket (27).

Alle tester konstruerer egen tilstand — buntene bygges byte for byte i
tmp_path, og der en header kan LYVE, patches katalogen bevisst så begge
lagene måles: gaten mot deklarasjonen, strømmen mot de faktiske bytene.
"""
from __future__ import annotations

import io
import os
import struct
import zipfile
from pathlib import Path

import pytest

from modules.m57_ats import blinding, evaluering, maler, parsing
from modules.m57_ats.evaluering import Biasmaaling
from oppdragskontrakt import bryter_feltkontrakten

ROT = Path(__file__).resolve().parents[3]
MODULROT = ROT / "platform/modules/m57_ats"


def _bunt(sti: Path, filer: list[tuple], **zipkw) -> Path:
    arkiv = sti / "bunt.zip"
    with zipfile.ZipFile(arkiv, "w", zipfile.ZIP_DEFLATED, **zipkw) as zf:
        for navn, innhold, *attr in filer:
            info = zipfile.ZipInfo(navn)
            info.compress_type = zipfile.ZIP_DEFLATED
            if attr:
                info.external_attr = attr[0]
            zf.writestr(info, innhold)
    return arkiv


def _patch_deklarert(arkiv: Path, navn: bytes, ny_storrelse: int,
                     komprimert: int | None = None) -> None:
    """Skriver om `uncompressed size` (og valgfritt `compressed size`) i
    SENTRALKATALOGEN for én oppføring — katalogen er en PÅSTAND, og
    nettopp det skal gaten/strømmen skille på."""
    data = bytearray(arkiv.read_bytes())
    sig = b"PK\x01\x02"
    i = data.find(sig)
    while i != -1:
        navnlengde = struct.unpack_from("<H", data, i + 28)[0]
        if data[i + 46:i + 46 + navnlengde] == navn:
            struct.pack_into("<I", data, i + 24, ny_storrelse)
            if komprimert is not None:
                struct.pack_into("<I", data, i + 20, komprimert)
            arkiv.write_bytes(bytes(data))
            return
        i = data.find(sig, i + 4)
    raise AssertionError(f"{navn!r} ikke i sentralkatalogen")


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


def _docx(indre: list[tuple[str, bytes]] | None = None) -> bytes:
    """En EKTE docx — altså en zip — bygget i minnet. DOCX er unntaket
    fra «ingen nøstede arkiver», og et unntak kan bare måles med den
    ekte formen."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for navn, innhold in (indre or [("word/document.xml",
                                         b"<w:t>CV</w:t>")]):
            zf.writestr(navn, innhold)
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
        ["kandidat1/cv.pdf"]


def test_port23_symlenke_avvises(tmp_path):
    arkiv = _bunt(tmp_path, [("lenke.pdf", b"/etc/passwd",
                              (0o120777 << 16))])
    with pytest.raises(parsing.Buntfeil) as e:
        parsing.inspiser_bunt(arkiv)
    assert e.value.kode == "symlenke"


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
    `_inspiser_docx`."""
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
    `_inspiser_docx` — forholds- og totalsjekken er grønn hele veien."""
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
    tom = _bunt(tmp_path, [("tom.html", b"")])
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
    funn refereres med ID, aldri med modellens prosa."""
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


class _Modell:
    image_digest = "sha256:" + "a" * 64

    def __init__(self):
        self.sett: list[str] = []

    def vurder(self, tekst, vekter):
        self.sett.append(tekst)
        return {"funn": [], "oppfylt": {k: True for k in vekter},
                "intervjusporsmal": ["Fortell om drift."]}


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
    hele = {"funn": [], "oppfylt": {"drift": True, "sikkerhet": False},
            "intervjusporsmal": ["Fortell om drift."]}
    for svar in (
            {},                                    # avkortet i sin helhet
            "ikke et objekt",
            {k: v for k, v in hele.items() if k != "oppfylt"},
            {k: v for k, v in hele.items() if k != "intervjusporsmal"},
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
            hele | {"oppfylt": {"drift": True}},
            hele | {"intervjusporsmal": [None]},
            hele | {"intervjusporsmal": [""]}):
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
    # Positiv kontroll: det HELE svaret går uendret gjennom.
    ut = evaluering.evaluer_kandidat(
        _Avkortet(hele), "Kari søker.", {"navn": ["Kari"]},
        vekter, biasmaalinger=_MAALINGER)
    assert ut["oppfylt"] == {"drift": True, "sikkerhet": False}
    assert ut["intervjusporsmal"] == ["Fortell om drift."]


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
                    "oppfylt": {k: True for k in vekter},
                    "intervjusporsmal": []}

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


def test_port27_5001_avvises_ved_validering():
    payload = {"stillingsprofil_ref": "art-1", "soknadsbunt_ref": "art-2",
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


def test_artefaktreferansene_ma_vaere_strenger_ved_opprettelsen():
    """Codex P2: `minimer` bevarer skalarer som de er og
    `mangler_paakrevde` godtar enhver sann verdi, så
    `stillingsprofil_ref: 123` overlevde BEGGE og ble køet. Modulens
    payload-skjema krever `string, minLength 1` — men det kjører først
    når utførelsen har startet, altså etter at oppdraget var opprettet,
    claimet og talt. Referansen måles nå der bestillingen tas imot."""
    payload = {"stillingsprofil_ref": "art-1", "soknadsbunt_ref": "art-2",
               "antall_soknader": 10, "omfang": "bunt"}
    assert bryter_feltkontrakten("rekruttering.evaluering", payload) == []
    for felt in ("stillingsprofil_ref", "soknadsbunt_ref"):
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


def test_m57_har_EN_modulidentitet_i_kontrakt_migrasjon_og_artefakt():
    """Codex P2 / Cursor P1: `m_ats` mot `m57_ats` var en splitt.

    De tre stedene som må være enige om hvem som eier M-57-oppdragene:

    * kontrakten (`eiermodul` — det som skrives i raden ved opprettelsen
      og det `claim_neste_oppdrag` filtrerer på),
    * 056s CHECK + `opprett_frigivelsesoppdrag` (utsendingsarmen), og
    * akseptartefaktets `oppsett.modul` (hvem aksepten attesterer).

    Var de uenige, kunne ingen modul claime BEGGE armene, og et
    skjemagyldig akseptartefakt ville attestert en annen identitet enn
    den som faktisk kjørte jobbene. Porten er statisk med vilje: den
    feller en splitt før noe kjøres.
    """
    import json
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
