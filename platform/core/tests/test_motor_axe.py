"""Motor_axe — de rene hjelperne og fasitkontrakten (CI-trygt: ingen
playwright-import; browserkjøringen selv måles i staging-runden, ikke her).
"""
import importlib.util
import json
import sys
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
MOTOR = ROT / "platform/modules/wcag_audit/motor_axe"


def _last(navn: str):
    spec = importlib.util.spec_from_file_location(navn, MOTOR / f"{navn}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[navn] = mod
    spec.loader.exec_module(mod)
    return mod


kjor = _last("kjor")
fasitkontroll = _last("fasitkontroll")


def test_robots_parsing_og_tillatt():
    tekst = "# k\nUser-agent: googlebot\nDisallow: /alt/\n" \
            "User-agent: *\nDisallow: /privat/\nDisallow: /tmp\n"
    disallow = kjor._parse_robots(tekst)
    assert disallow == ["/privat/", "/tmp"]
    assert kjor._tillatt("/privat/hemmelig.html", disallow) is False
    assert kjor._tillatt("/tmpfil", disallow) is False   # prefiks, som robots
    assert kjor._tillatt("/index.html", disallow) is True
    assert kjor._tillatt("/alt/x", disallow) is True     # gjaldt googlebot


def test_lenkenormalisering_er_lukket():
    n = kjor._normaliser_lenke
    o = "http://127.0.0.1:8093"
    assert n(o, f"{o}/index.html", "/om.html") == f"{o}/om.html"
    assert n(o, f"{o}/index.html", "om.html") == f"{o}/om.html"
    assert n(o, f"{o}/index.html", "/om.html#seksjon") == f"{o}/om.html"
    assert n(o, f"{o}/index.html", "https://annen.example/") is None
    assert n(o, f"{o}/index.html", "/sok?q=1") is None
    assert n(o, f"{o}/index.html", "mailto:x@y") is None


def test_kravsett_og_alvorlighet_dekker_kontrakten():
    # Enum-ene speiler rapportskjemaet — driver de fra hverandre, produserer
    # motoren verdier skjemavalideringen avviser.
    assert set(kjor.KRAVSETT_TAGS) == {"wcag21_aa"}
    assert set(kjor.ALVORLIGHET.values()) == {"kritisk", "alvorlig",
                                              "moderat", "lav"}
    assert set(kjor.ART.values()) <= {"stilark", "font", "skript", "bilde"}


def test_axe_pinnen_er_ekte_hex():
    assert len(kjor.AXE_SHA256) == 64
    int(kjor.AXE_SHA256, 16)
    assert kjor.AXE_VERSJON in kjor.AXE_URL


def test_fasitkontroll_finner_hver_avviksklasse():
    fasit = json.loads(
        (ROT / "platform/modules/wcag_audit/testnettsted/fasit.json")
        .read_text(encoding="utf-8"))
    s = fasit["scenarier"]["enkeltside"]
    # Perfekt motorutdata konstruert FRA fasiten → null avvik.
    motor = {
        "funn": [{"regel_id": rid, "alvorlighet": v["alvorlighet"],
                  "antall": v["antall"], "eksempler": []}
                 for rid, v in s["funn"].items()],
        "blokkert": [{"vert": vert, "art": art, "antall": n}
                     for vert, arter in s["blokkert"].items()
                     for art, n in arter.items()],
        "avkortet": list(s["avkortet"]),
        "sider": [{"url": "http://t/index.html", "status": "ok"}]
                 * s["sider_ok"],
    }
    assert fasitkontroll.avvik(s, motor) == []
    # …og hver klasse av avvik navngis.
    b = json.loads(json.dumps(motor))
    b["funn"][0]["antall"] += 1
    assert any(a.startswith("funn:") for a in fasitkontroll.avvik(s, b))
    b = json.loads(json.dumps(motor))
    b["blokkert"] = []
    assert any(a.startswith("blokkert:") for a in fasitkontroll.avvik(s, b))
    b = json.loads(json.dumps(motor))
    b["avkortet"] = [True, 1, 2]
    assert any(a.startswith("avkortet:") for a in fasitkontroll.avvik(s, b))
    b = json.loads(json.dumps(motor))
    b["sider"][0]["status"] = "feilet"
    assert any(a.startswith("sider:") for a in fasitkontroll.avvik(s, b))


def test_fasiten_er_konsistent_med_seg_selv():
    """Avkortingsregnskapet i fasiten: 13 = 4 besøkte + 9 i kø, og
    robots-siden er aldri en del av regnskapet."""
    fasit = json.loads(
        (ROT / "platform/modules/wcag_audit/testnettsted/fasit.json")
        .read_text(encoding="utf-8"))
    s = fasit["scenarier"]["nettsted_maks4"]
    truffet, tak, verdi = s["avkortet"]
    assert truffet is True and tak == s["payload"]["maks_sider"]
    assert verdi == 13
    assert "/privat/hemmelig.html" not in s["_crawlrekkefolge"]
    sider = ROT / "platform/modules/wcag_audit/testnettsted/sider"
    assert (sider / "privat/hemmelig.html").exists()
    assert "Disallow: /privat/" in (sider / "robots.txt").read_text()


# ---------------------------------------------------------------------------
# Kontraktsdokumentene (kontrakt/): proveniens som ikke får drive fra koden
# ---------------------------------------------------------------------------

def test_kontraktsdokumentets_hasher_matcher_skjemafilene():
    """KONTRAKT.md navngir payload-/kvitteringsskjemaets sha256 — driver
    dokument og fil fra hverandre, registreres feil proveniens immutabelt."""
    import hashlib
    kdir = ROT / "platform/modules/wcag_audit/kontrakt"
    md = (kdir / "KONTRAKT.md").read_text(encoding="utf-8")
    for fil, felt in (("payload-skjema.json", "payload_schema_hash"),
                      ("kvittering-skjema.json", "kvittering_schema_hash")):
        h = hashlib.sha256((kdir / fil).read_bytes()).hexdigest()
        assert f"**{felt}**: `{h}`" in md, \
            f"{felt} i KONTRAKT.md matcher ikke {fil}"


def test_kvitteringsskjemaet_speiler_controllerens_feilkoder():
    kdir = ROT / "platform/modules/wcag_audit/kontrakt"
    skjema = json.loads((kdir / "kvittering-skjema.json")
                        .read_text(encoding="utf-8"))
    i_skjema = set(skjema["properties"]["feilkode"]["enum"])
    import re
    kode = (ROT / "platform/modules/wcag_audit/controller.py") \
        .read_text(encoding="utf-8")
    i_koden = set(re.findall(r'"feilkode": "([a-z_]+)"', kode))
    assert i_skjema == i_koden, (i_skjema ^ i_koden)


def test_payloadskjemaet_speiler_oppdragskontrakten():
    import oppdragskontrakt
    kdir = ROT / "platform/modules/wcag_audit/kontrakt"
    skjema = json.loads((kdir / "payload-skjema.json")
                        .read_text(encoding="utf-8"))
    t = oppdragskontrakt.OPPDRAGSTYPER["kontroll.wcag.nettsted"]
    assert set(skjema["properties"]) == set(t.felter)
    assert set(skjema["required"]) == set(t.felter), \
        "normaliseringen fyller alltid alle fire feltene"
    assert skjema["additionalProperties"] is False
