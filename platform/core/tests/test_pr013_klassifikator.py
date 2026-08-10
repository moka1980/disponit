"""PR-013 CP3: KLASSIFIKATOR_V1 — UTVIDER/INNSNEVRER/NØYTRAL.

Hver UTVIDER-regel har en test som blir RØD hvis regelen fjernes (mutasjon).
Frekvens = burst, tidsvindu = mengdeinklusjon (delt kodevei m/ motoren, port
14), deny-all-baseline gjør første policy til UTVIDER, ukjent → UTVIDER.
"""
import copy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from policy_validator import engine, klassifikator as kl, tidsvindu

U, I, N = kl.UTVIDER, kl.INNSNEVRER, kl.NØYTRAL


def _pol():
    return {
        "tidssone": "Europe/Oslo",
        "roller": [{"id": "agent"}],
        "verifikatorer": {"v1": {"betrodd_for": ["belop"],
                                 "kan_fastsla_permanent": False}},
        "unntak": {"kategorier": ["over_grense"]},
        "handlinger": [{"id": "faktura.bokfor", "modus": "auto_med_vilkaar",
                        "ved_brudd": "unntakskø",
                        "reversering": {"type": "kompenserende"},
                        "vilkaar": [{"navn": "fire_oyne", "verifikator": "v1"}],
                        "tillatt_for": ["agent"],
                        "grenser": {"belop_maks": "25000.00", "valuta": ["NOK"]}}],
    }


def _klasse(muter):
    g = _pol()
    n = copy.deepcopy(g)
    muter(n)
    return kl.klassifiser(g, n)["klasse"]


def _h(p):
    return p["handlinger"][0]


# --- skalar / gitter / mengde ---------------------------------------------
def test_belop_opp_utvider_ned_innsnevrer():
    assert _klasse(lambda n: _h(n)["grenser"].__setitem__("belop_maks", "30000.00")) == U
    assert _klasse(lambda n: _h(n)["grenser"].__setitem__("belop_maks", "20000.00")) == I
    assert _klasse(lambda n: _h(n)["grenser"].pop("belop_maks")) == U   # fjernet = ubegrenset


def test_modus_mot_auto_utvider():
    assert _klasse(lambda n: _h(n).__setitem__("modus", "auto")) == U
    assert _klasse(lambda n: _h(n).__setitem__("modus", "alltid_stopp")) == I


def test_ved_brudd_mot_mildere_utvider():
    assert _klasse(lambda n: _h(n).__setitem__("ved_brudd", "frys")) == U
    assert _klasse(lambda n: _h(n).__setitem__("ved_brudd", "stopp_og_varsle")) == U  # unntakskø→ mildere


def test_reversering_mot_mer_reversibel_utvider():
    assert _klasse(lambda n: _h(n)["reversering"].__setitem__("type", "direkte")) == U
    assert _klasse(lambda n: _h(n)["reversering"].__setitem__("type", "irreversibel")) == I


def test_valuta_endring_utvider():
    assert _klasse(lambda n: _h(n)["grenser"].__setitem__("valuta", ["NOK", "EUR"])) == U
    assert _klasse(lambda n: _h(n)["grenser"].__setitem__("valuta", ["EUR"])) == U


def test_vilkaar_fjernet_utvider_lagt_til_innsnevrer():
    assert _klasse(lambda n: _h(n).__setitem__("vilkaar", [])) == U
    assert _klasse(lambda n: _h(n)["vilkaar"].append(
        {"navn": "manuell", "verifikator": "v1"})) == I


def test_handling_lagt_til_utvider_fjernet_innsnevrer():
    assert _klasse(lambda n: n["handlinger"].append({"id": "ny.handling"})) == U
    assert _klasse(lambda n: n["handlinger"].clear()) == I


def test_rolle_og_kategori_og_verifikator_mengder():
    assert _klasse(lambda n: n["roller"].append({"id": "admin"})) == U
    assert _klasse(lambda n: n["roller"].clear()) == I
    assert _klasse(lambda n: n["unntak"]["kategorier"].append("manglende_data")) == U
    assert _klasse(lambda n: n["verifikatorer"].__setitem__("v2", {"betrodd_for": []})) == U
    assert _klasse(lambda n: n["verifikatorer"]["v1"].__setitem__("kan_fastsla_permanent", True)) == U
    assert _klasse(lambda n: n["verifikatorer"]["v1"]["betrodd_for"].append("valuta")) == U


def test_tidssone_endring_utvider():
    assert _klasse(lambda n: n.__setitem__("tidssone", "UTC")) == U


# --- frekvens (burst) ------------------------------------------------------
def _med_frekvens(p, **fr):
    _h(p)["grenser"]["frekvens"] = {"maks": fr.get("maks", 5),
                                    "periode_antall": fr.get("periode_antall", 1),
                                    "periode_enhet": fr.get("periode_enhet", "dager"),
                                    "grupperingsnokkel": fr.get("nokkel", "ressurs_id")}


def test_frekvens_maks_ned_uendret_vindu_innsnevrer():
    g = _pol(); _med_frekvens(g, maks=5)
    n = copy.deepcopy(g); _med_frekvens(n, maks=3)
    assert kl.klassifiser(g, n)["klasse"] == I


def test_frekvens_1pr_dag_til_7pr_uke_utvider():
    g = _pol(); _med_frekvens(g, maks=1, periode_enhet="dager", periode_antall=1)
    n = copy.deepcopy(g); _med_frekvens(n, maks=7, periode_enhet="uker", periode_antall=1)
    # samme gjennomsnittsrate, men burst-fullmakten er en helt annen → UTVIDER
    assert kl.klassifiser(g, n)["klasse"] == U


def test_frekvens_fjernet_utvider():
    g = _pol(); _med_frekvens(g, maks=5)
    n = copy.deepcopy(g); _h(n)["grenser"].pop("frekvens")
    assert kl.klassifiser(g, n)["klasse"] == U


# --- tidsvindu (mengdeinklusjon, delt kodevei) -----------------------------
def test_nattvindu_utvidet_utvider():
    g = _pol(); _h(g)["grenser"]["tidsvindu"] = "man-fre 22:00-06:00"
    n = copy.deepcopy(g); _h(n)["grenser"]["tidsvindu"] = "man-fre 21:00-07:00"
    assert kl.klassifiser(g, n)["klasse"] == U      # gammelt ⊊ nytt
    # motsatt vei: smalnet → INNSNEVRER
    assert kl.klassifiser(n, g)["klasse"] == I


def test_tidsvindu_lagt_til_innsnevrer_fjernet_utvider():
    g = _pol()
    n = copy.deepcopy(g); _h(n)["grenser"]["tidsvindu"] = "man-fre 08:00-16:00"
    assert kl.klassifiser(g, n)["klasse"] == I      # vindu innført = innsnevring
    assert kl.klassifiser(n, g)["klasse"] == U      # vindu fjernet = alltid tillatt


def test_port14_klassifikator_og_motor_gir_samme_tidsmengde_med_dst():
    """Delt kodevei: motorens medlemskap == medlemskap i klassifikatorens
    mengde, for hvert minutt over en sommertidsovergang (Oslo, 30. mars 2025
    02:00→03:00). Divergens her ville brutt hele monotonigarantien."""
    sone = ZoneInfo("Europe/Oslo")
    vindu = "man-son 01:00-04:00"
    mengde = tidsvindu.tillatte_ukeminutter(vindu, "Europe/Oslo")
    start = datetime(2025, 3, 30, 0, 0, tzinfo=timezone.utc)
    for i in range(6 * 60):                          # seks timer, minutt for minutt
        t = start + timedelta(minutes=i)
        assert engine._i_vindu(vindu, t, sone) == (
            tidsvindu.lokal_ukeminutt(t, sone) in mengde)


# --- menneskelig_overstyring ----------------------------------------------
def _med_mo(p, **over):
    mo = {"godkjennbare": [{"grunnkode": "belop_over_grense",
                            "handling": "faktura.bokfor", "belop_maks": "50000.00",
                            "valuta": "NOK"}],
          "krever_rolle": "okonomi", "krever_fire_oyne": True}
    mo.update(over)
    p["menneskelig_overstyring"] = mo


def test_mo_lagt_til_utvider():
    g = _pol()
    n = copy.deepcopy(g); _med_mo(n)
    assert kl.klassifiser(g, n)["klasse"] == U


def test_mo_fire_oyne_av_utvider_belop_opp_utvider():
    g = _pol(); _med_mo(g)
    n = copy.deepcopy(g); n["menneskelig_overstyring"]["krever_fire_oyne"] = False
    assert kl.klassifiser(g, n)["klasse"] == U
    n2 = copy.deepcopy(g); n2["menneskelig_overstyring"]["godkjennbare"][0]["belop_maks"] = "90000.00"
    assert kl.klassifiser(g, n2)["klasse"] == U


# --- samlet / metadata / fail-closed / deny-all ----------------------------
def test_samlet_er_strengeste():
    g = _pol()
    n = copy.deepcopy(g)
    _h(n)["grenser"]["belop_maks"] = "20000.00"      # INNSNEVRER
    n["roller"].append({"id": "admin"})              # UTVIDER
    assert kl.klassifiser(g, n)["klasse"] == U       # strengeste dominerer


def test_metadata_er_noytral():
    g = _pol()
    n = copy.deepcopy(g); n["metadata"] = {"notat": "x"}
    assert kl.klassifiser(g, n)["klasse"] == N


def test_forste_policy_mot_deny_all_er_utvider():
    # Tom/deny-all-baseline → enhver reell policy er ren utvidelse.
    assert kl.klassifiser({}, _pol())["klasse"] == U


def test_hash_og_versjon_deterministisk():
    g, n = _pol(), _pol()
    n["roller"].append({"id": "admin"})
    r1, r2 = kl.klassifiser(g, n), kl.klassifiser(g, n)
    assert r1["klassifisering_hash"] == r2["klassifisering_hash"]
    assert r1["klassifikatorversjon"] == kl.KLASSIFIKATORVERSJON
    # ulik endring → ulik hash
    n2 = _pol(); _h(n2)["grenser"]["belop_maks"] = "99999.00"
    assert kl.klassifiser(g, n2)["klassifisering_hash"] != r1["klassifisering_hash"]
