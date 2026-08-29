"""m57s to YTRE klienter, prøvd direkte: modellklienten (`modell.py`) og
tekstuttrekket (`uttrekk.py`).

Cursor P2, runde 2: begge er PRODUKSJONSKODE som bare ble nådd fra
`m57_arbeider.py`. Controller-testene kjører mot `_Modell`/`_Uttrekker`-
stubs, og buntestene mot `tekst_for=lambda m, d: d.decode(...)` — så
ingen port rørte de faktiske klientene. Det som var udekket er nettopp
det de finnes for: at et YTRE, upålitelig svar blir et KODET utfall
(SP-3) i stedet for et rått unntak inn i kjøreløkka, og at et «sitat»
uten ordrett dekning i den blindede teksten ikke blir evidens.

HTTP og subprocess mockes; ingen ny maskin, ingen ekte server (K1).
"""
from __future__ import annotations

import io
import shlex
import shutil
import types
import urllib.error
import zipfile

import pytest

from modules.m57_ats import modell as m
from modules.m57_ats import uttrekk as u

VEKTER = {"drift": 3, "norsk": 1}


def _svarer(monkeypatch, innhold, *, reiser=None):
    """Mocker `urllib.request.urlopen` med ETT Ollama-svar.

    `innhold` er det modellen «skrev» — rå streng, eller noe annet enn en
    streng for å prøve den uleselige veien."""
    class _R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            import json
            return json.dumps({"message": {"content": innhold}}).encode()

    def _urlopen(req, timeout=None):
        if reiser is not None:
            raise reiser
        return _R()

    monkeypatch.setattr(m.urllib.request, "urlopen", _urlopen)


def _modell():
    return m.Ollamamodell("http://lokal:11434", "modellnavn", "sha256:d")


@pytest.mark.parametrize("feil", [
    urllib.error.URLError("nede"),
    OSError("brutt rør"),
    ValueError("uleselig json fra serveren"),
])
def test_transportfeil_er_en_kodet_modellfeil(monkeypatch, feil):
    """SP-3: en lokal modellserver som er nede, treg eller svarer søppel
    skal gi et KODET stopp. Et rått `URLError` ut av `vurder` ville nådd
    `kjor_bunt` som et ukjent unntak, og der finnes ingen feilkode å
    kvittere med — oppdraget ville stått claimet og tyst til fristen."""
    _svarer(monkeypatch, None, reiser=feil)
    with pytest.raises(m.Modellfeil) as e:
        _modell().vurder("tekst", VEKTER)
    assert e.value.kode == "modell_utilgjengelig", e.value.kode
    # Detaljen navngir unntakstypen, aldri innholdet: prompten ER
    # saksdata, og en feilmelding er ikke stedet den skal lekke.
    assert type(feil).__name__ in str(e.value)


@pytest.mark.parametrize("innhold,kode", [
    (None, "modellsvar_uleselig"),               # ikke en streng
    (42, "modellsvar_uleselig"),
    ("ikke json i det hele tatt", "modellsvar_uleselig"),
    ("[1, 2, 3]", "modellsvar_uleselig"),        # json, men ikke objekt
    ('"bare en streng"', "modellsvar_uleselig"),
])
def test_uleselig_modellsvar_er_kodet(monkeypatch, innhold, kode):
    """Modellen er bedt om ett JSON-objekt. Alt annet — tom `content`,
    prosa, en liste — er et uleselig svar, ikke en tom evaluering. Den
    forskjellen er hele poenget: en tom evaluering ville blitt en RAPPORT
    som sier «ingen funn» om en søknad ingen har lest."""
    _svarer(monkeypatch, innhold)
    with pytest.raises(m.Modellfeil) as e:
        _modell().vurder("tekst", VEKTER)
    assert e.value.kode == kode, e.value.kode


def test_oppfyllelsen_er_fail_closed_over_profilens_kravsett(monkeypatch):
    """`oppfylt` dekker NØYAKTIG profilens krav — verken mer eller
    mindre — og et krav modellen ikke svarte på, eller svarte noe
    ikke-boolsk på, er IKKE oppfylt.

    Sannhetsverdien av «hva som helst» ville gjort en modell som skriver
    `"ja"`, `1` eller `{}` til en som dokumenterer kravet. Det er en
    kandidat som blir vurdert på et krav ingen har belegg for."""
    _svarer(monkeypatch, '{"oppfylt": {"drift": "ja", "fremmed": true}}')
    res = _modell().vurder("tekst", VEKTER)
    assert res["oppfylt"] == {"drift": False, "norsk": False}, res
    # Et krav modellen fant på selv følger ikke med videre.
    assert "fremmed" not in res["oppfylt"]

    _svarer(monkeypatch, '{"oppfylt": {"drift": true, "norsk": false}}')
    assert _modell().vurder("tekst", VEKTER)["oppfylt"] == {
        "drift": True, "norsk": False}

    # En `oppfylt` som ikke er et objekt gir ikke et rått unntak: hele
    # settet står da fail-closed.
    _svarer(monkeypatch, '{"oppfylt": ["drift"]}')
    assert _modell().vurder("tekst", VEKTER)["oppfylt"] == {
        "drift": False, "norsk": False}


def test_sitat_uten_ordrett_dekning_droppes_og_telles(monkeypatch):
    """KLIENTENS EGEN LOKALISERING er kravet: modellen bes om VERBATIME
    utdrag, og klienten finner offsetene med `tekst.find`. Et «sitat»
    som ikke står ordrett i den blindede teksten er ikke evidens — det
    er noe modellen skrev — og det droppes, talt i `droppede_funn` for
    driftsloggen.

    Uten dette ville en oppdiktet setning fulgt med inn i rapporten med
    en `kilde` som pekte på tekst som ikke finnes."""
    tekst = "Kandidaten har drevet drift i tre år."
    _svarer(monkeypatch, (
        '{"funn": ['
        ' {"kategori": "uklar_tidslinje", "sitat": "drevet drift"},'
        ' {"kategori": "uklar_tidslinje", "sitat": "aldri skrevet dette"}'
        ']}'))
    mo = _modell()
    res = mo.vurder(tekst, VEKTER)
    assert len(res["funn"]) == 1, res["funn"]
    kilde = res["funn"][0]["kilde"]
    assert kilde["sitat"] == "drevet drift"
    # Offsetene peker FAKTISK på sitatet i teksten.
    assert tekst[kilde["start"]:kilde["slutt"]] == "drevet drift"
    assert mo.droppede_funn == 1, mo.droppede_funn


@pytest.mark.parametrize("funn", [
    '{"kategori": "finnes_ikke", "sitat": "drift"}',   # ukjent kategori
    '{"kategori": "uklar_tidslinje", "sitat": ""}',    # tomt sitat
    '{"kategori": "uklar_tidslinje", "sitat": 7}',     # ikke en streng
    '{"kategori": "uklar_tidslinje"}',                 # uten sitat
    '"ikke et objekt"',
])
def test_ugyldig_funnform_droppes_ikke_feiler(monkeypatch, funn):
    """Et enkelt ugyldig funn skal ikke felle en ellers gyldig
    evaluering — kategorisettet er lukket (`FUNN_KATEGORIER`), og en
    modell som finner på en kategori får den droppet, ikke smuglet inn
    i rapporten."""
    _svarer(monkeypatch, '{"funn": [%s]}' % funn)
    mo = _modell()
    res = mo.vurder("drift", VEKTER)
    assert res["funn"] == [], res["funn"]
    assert mo.droppede_funn == 1


def test_funntaket_handheves_ved_grensen(monkeypatch):
    """Rapportskjemaets tak på 100 funn håndheves HER, ikke av
    skjemavalideringen: en modell som fosser funn skal ikke felle en
    ellers gyldig evaluering av en persondatabunt som alt er parset."""
    import json
    biter = [{"kategori": "uklar_tidslinje", "sitat": "drift"}] * 130
    _svarer(monkeypatch, json.dumps({"funn": biter}))
    mo = _modell()
    res = mo.vurder("drift", VEKTER)
    assert len(res["funn"]) == 100, len(res["funn"])
    assert mo.droppede_funn == 30, mo.droppede_funn


def test_intervjusporsmal_verken_bes_om_eller_hentes_ut(monkeypatch):
    """Eiers produktretning 27/8 (#225): rekrutterer velger de beste
    blant mange, og intervjuer skjer manuelt når de innkalles —
    evalueringen skal ikke bruke modelltid på spørsmål per søker.
    Prompten ber ikke om dem, og en modell som likevel sender dem får
    dem droppet ved grensen: fri modelltekst uten plass i kontrakten
    følger aldri med videre."""
    import json
    assert "intervjusporsmal" not in m._SYSTEM, \
        "prompten skal ikke bestille intervjuspørsmål"
    _svarer(monkeypatch, json.dumps({
        "oppfylt": {"drift": True, "norsk": False},
        "intervjusporsmal": ["Fortell om drift."]}))
    res = _modell().vurder("tekst", VEKTER)
    assert "intervjusporsmal" not in res, res


# --- uttrekket ------------------------------------------------------


def _medlem(navn):
    return types.SimpleNamespace(navn=navn)


def test_pdf_uten_konfigurert_kommando_er_et_kodet_stopp():
    """Tom `pdf_kommando` = PDF-uttrekk er utilgjengelig i denne
    deploymenten. En PDF i bunten er da et KODET stopp — aldri en stille
    tom tekst, som ville blitt en evaluering av ingenting presentert som
    en evaluering av søknaden."""
    with pytest.raises(u.Uttrekksfeil) as e:
        u.Uttrekker().tekst_for(_medlem("cv.pdf"), b"%PDF-1.4")
    assert e.value.kode == "uttrekk_ustottet", e.value.kode


def test_ukjent_endelse_er_ustottet_ikke_tom_tekst():
    """Kontrakten har tre innholdstyper. En fjerde skal si ifra."""
    for navn in ("notat.txt", "bilde.png", "arkiv.zip", "uten_endelse"):
        with pytest.raises(u.Uttrekksfeil) as e:
            u.Uttrekker().tekst_for(_medlem(navn), b"x")
        assert e.value.kode == "uttrekk_ustottet", navn


def _pdf_kommando(tmp_path, navn, kropp):
    """En EKTE uttrekkskommando av samme form som `pdftotext - -`.

    TESTEN MÅ DRIVE DEN VEIEN KODEN FAKTISK GÅR (Codex P2, #173). Denne
    testen mocket `subprocess.run`, men `_pdf` gikk over til `Popen` med
    stdout til fil da taket ble flyttet inn i skrivingen. Patchen traff
    da ingenting: testen startet den ekte, fraværende `pdftotext` og
    felte på `FileNotFoundError` i suksesstilfellet. En mock av et navn
    produksjonskoden ikke lenger kaller, er ikke en svakere test — den
    er ingen test, og den var rød i CI.

    Ekte kommando i stedet for en ny Popen-attrapp: `_kjor_bundet` måler
    en voksende FIL og dreper en LEVENDE prosess, og en attrapp av det
    er en simulator av nettopp mekanismen som skal prøves (K4/SP-13).
    Samme form som `test_173_pdf_stdout_felles_mens_den_skrives_ikke_etterpa`.
    """
    python = shutil.which("python3") or shutil.which("python")
    assert python, "ingen python-tolk å bygge en ekte uttrekkskommando av"
    skript = tmp_path / navn
    skript.write_text("import sys, time\nsys.stdin.buffer.read()\n" + kropp,
                      encoding="utf-8")
    return f"{shlex.quote(python)} {shlex.quote(str(skript))}"


def test_pdf_delegeres_til_kommandoen_og_feil_kodes(tmp_path):
    """PDF delegeres til en konfigurert kommando (`pdftotext`-form) —
    samme delegasjonsmønster som m56s motor. Alt som kan gå galt der ute
    blir en kodet `Uttrekksfeil`, aldri et rått `OSError` inn i
    kjøreløkka."""
    # 1) Suksess: stdout er teksten.
    ut = u.Uttrekker(_pdf_kommando(
        tmp_path, "ok.py",
        "sys.stdout.buffer.write('drift i tre år'.encode('utf-8'))\n"))
    assert ut.tekst_for(_medlem("cv.pdf"), b"%PDF") == "drift i tre år"

    # 2) Ikke-null exit: kommandoen kjørte, men fikk ikke lest pdf-en.
    ut = u.Uttrekker(_pdf_kommando(tmp_path, "rc1.py", "sys.exit(1)\n"))
    with pytest.raises(u.Uttrekksfeil) as e:
        ut.tekst_for(_medlem("cv.pdf"), b"%PDF")
    assert e.value.kode == "uttrekk_uleselig", e.value.kode
    assert "rc=1" in str(e.value)

    # 3) Fristen løper ut: kommandoen henger uten å skrive.
    ut = u.Uttrekker(_pdf_kommando(tmp_path, "henger.py", "time.sleep(120)\n"),
                     frist_s=0.5)
    with pytest.raises(u.Uttrekksfeil) as e:
        ut.tekst_for(_medlem("cv.pdf"), b"%PDF")
    assert e.value.kode == "uttrekk_uleselig", e.value.kode

    # 4) Ugyldig UTF-8 fra kommandoen erstattes, den feller ikke: teksten
    # er kandidatens, og en enkelt rar byte skal ikke koste evalueringen.
    ut = u.Uttrekker(_pdf_kommando(
        tmp_path, "raabyte.py", "sys.stdout.buffer.write(b'drift \\xff her')\n"))
    assert "drift" in ut.tekst_for(_medlem("cv.pdf"), b"%PDF")


def test_173_pdf_skiller_spolefeil_fra_kommandofeil(tmp_path, monkeypatch):
    """Codex P2 (#173): `_pdf` la BEGGE i `uttrekk_uleselig`.

    Grenen fanget `OSError` fra hele blokken — `TemporaryFile`, `write`,
    `read` og `fstat` — og kalte alt sammen et ulesbart dokument. De to
    kildene har ikke samme eier:

    * Det MIDLERTIDIGE FILSYSTEMET er DRIFT. Er spolen full, borte eller
      svarer den EIO, ble bunten avbrutt med at søkerens pdf var
      ulesbar; arbeiderens retry og driftsalarmen leste da feil kø, mens
      dokumentet var helt i orden. Samme misattribusjon `_spoletekst`
      fikk rettet én kodevei lenger ut, og samme kode: `infrastrukturfeil`.
    * En kommando som ikke lar seg STARTE er DEPLOYMENTEN. Det er samme
      sak som en tom `pdf_kommando` — pdf-uttrekk finnes ikke her — og
      derfor `uttrekk_ustottet`. Meldt som drift ville den blitt
      retryet mot en feil som aldri går over av seg selv.

    MUTASJONEN SOM DREPER DENNE: gi begge veiene samme kode igjen.
    """
    # Spolen svikter: `infrastrukturfeil`, ikke dokumentets feil.
    ut = u.Uttrekker(_pdf_kommando(tmp_path, "aldri.py", "pass\n"))

    def _full_disk(*a, **kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(u.tempfile, "TemporaryFile", _full_disk)
    with pytest.raises(u.Uttrekksfeil) as e:
        ut.tekst_for(_medlem("cv.pdf"), b"%PDF")
    assert e.value.kode == "infrastrukturfeil", e.value.kode
    monkeypatch.undo()

    # Kommandoen finnes ikke: `uttrekk_ustottet`, ikke drift.
    ut = u.Uttrekker(shlex.quote(str(tmp_path / "pdftotext-finnes-ikke")))
    with pytest.raises(u.Uttrekksfeil) as e:
        ut.tekst_for(_medlem("cv.pdf"), b"%PDF")
    assert e.value.kode == "uttrekk_ustottet", e.value.kode


def test_html_trekkes_ut_med_ekte_parser():
    """SP-13/K4: ekte parser, aldri regex over fremmed grammatikk.
    `script`/`style` er ikke søknadstekst og faller bort — ellers ville
    et skript i en CV blitt evaluert som kandidatens egne ord."""
    ut = u.Uttrekker()
    tekst = ut.tekst_for(_medlem("soknad.html"), (
        b"<html><head><style>p{color:red}</style></head>"
        b"<body><p>Drift i tre &aring;r</p>"
        b"<script>alert('ansett meg')</script>"
        b"<p>Norsk: flytende</p></body></html>"))
    assert "Drift i tre år" in tekst
    assert "Norsk: flytende" in tekst
    assert "color:red" not in tekst
    assert "ansett meg" not in tekst


def test_html_med_ugyldig_koding_er_kodet():
    """Plattformens tekst er UTF-8. En fil som ikke er det, er en
    uleselig FIL — ikke en modellfeil."""
    with pytest.raises(u.Uttrekksfeil) as e:
        u.Uttrekker().tekst_for(_medlem("s.html"), b"<p>\xff\xfe ikke utf-8")
    assert e.value.kode == "uttrekk_uleselig", e.value.kode


def _docx_bytes(xml: bytes, *, navn="word/document.xml") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(navn, xml)
    return buf.getvalue()


_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def test_docx_avsnitt_trekkes_ut():
    ut = u.Uttrekker()
    xml = (f'<w:document xmlns:w="{_NS}"><w:body>'
           '<w:p><w:r><w:t>Drift i </w:t></w:r>'
           '<w:r><w:t>tre år</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>   </w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Norsk</w:t></w:r></w:p>'
           '</w:body></w:document>').encode("utf-8")
    tekst = ut.tekst_for(_medlem("cv.docx"), _docx_bytes(xml))
    # Løpene i ETT avsnitt settes sammen — ikke ett fragment per `w:r`.
    assert tekst == "Drift i tre år\nNorsk", tekst


@pytest.mark.parametrize("data,merknad", [
    (b"ikke en zip i det hele tatt", "BadZipFile"),
    (_docx_bytes(b"<w:document", navn="word/document.xml"), "ParseError"),
    (_docx_bytes(b"<x/>", navn="annet.xml"), "KeyError: mangler delen"),
])
def test_ulesbar_docx_er_kodet(data, merknad):
    """Alle tre veiene inn i en ulesbar docx — ikke et arkiv, ulovlig
    XML, eller uten `word/document.xml` — gir samme kodede utfall."""
    with pytest.raises(u.Uttrekksfeil) as e:
        u.Uttrekker().tekst_for(_medlem("cv.docx"), data)
    assert e.value.kode == "uttrekk_uleselig", (merknad, e.value.kode)


def test_docx_bombe_felles_pa_taket(monkeypatch):
    """En docx-bombe skal felles på GRENSEN, ikke i minnet.

    Porten dekker katalogpåstanden: `info.file_size` over taket stopper
    før noe pakkes ut. Lengdesjekken rett etter (`f.read(MAKS + 1)` og
    `len(xml) > MAKS`) er belte-og-seler mot en katalog som lyver LAVT,
    og er med vilje ikke prøvd her — `zipfile` avkorter selv lesingen på
    den deklarerte størrelsen, så den grenen er ikke nåbar gjennom det
    vanlige API-et. Å tvinge den fram ville krevd et forfalsket arkiv:
    ny maskin i en fiksrunde (K1), og for en sjekk som allerede er
    sekundær."""
    monkeypatch.setattr(u, "MAKS_DOCX_XML", 64)
    xml = (f'<w:document xmlns:w="{_NS}"><w:body><w:p><w:r><w:t>'
           + "a" * 500 + '</w:t></w:r></w:p></w:body></w:document>').encode()
    with pytest.raises(u.Uttrekksfeil) as e:
        u.Uttrekker().tekst_for(_medlem("cv.docx"), _docx_bytes(xml))
    assert e.value.kode == "uttrekk_uleselig", e.value.kode
    assert "for stor" in str(e.value)
