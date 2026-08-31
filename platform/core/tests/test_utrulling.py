"""Autorisasjonsregelen for `/v1/utrulling` (P1, Codex runde 3).

`svar_for` er hele regelen for hvilke utrullingsrader som forlater serveren,
og den er ren: den testes uten DB, uten HTTP og uten klient. Det er poenget —
tidligere lå tabellen i den statisk serverte klientbunten, og «autorisasjonen»
var et filter i DOM-en hos den som allerede hadde lastet ned alt.
"""
from __future__ import annotations

import json
import pathlib

from api.utrulling import PLATTFORMDRIFT, SPRAK, egen_rad, svar_for

ROT = pathlib.Path(__file__).resolve().parents[3]

KUNDESCOPES = ["decisions:read", "exceptions:read", "policy:read",
               "security:read"]


def test_kundeokt_far_bare_sin_egen_rad():
    svar = svar_for("bjorkli", KUNDESCOPES)
    assert svar["plattformdrift"] is False
    assert [r["id"] for r in svar["tenanter"]] == ["bjorkli"]
    assert svar["moduler"] == [1, 2, 16]


def test_security_read_er_ikke_plattformautoritet():
    """`security:read` er en TENANTBUNDET ops/compliance-scope (PR-008 §1).
    Den skal ikke kunne lese kontrollplanet på tvers av kunder."""
    svar = svar_for("bjorkli", ["security:read"])
    assert svar["plattformdrift"] is False
    assert len(svar["tenanter"]) == 1


def test_plattformdrift_ser_kontrollplanet():
    svar = svar_for("bjorkli", [PLATTFORMDRIFT])
    assert svar["plattformdrift"] is True
    assert len(svar["tenanter"]) >= 3
    assert "bjorkli" in [r["id"] for r in svar["tenanter"]]


def test_ukjent_tenant_gir_ingenting_a_gjette_fra():
    """Ukjent tenant er «vet ikke», ikke «her er alle»: en tom liste kan ikke
    misforstås av en flate, en full liste kan."""
    svar = svar_for("acme", KUNDESCOPES)
    assert svar["moduler"] is None
    assert svar["tenanter"] == []
    for tom in ("", None, "   "):
        assert svar_for(tom, KUNDESCOPES)["tenanter"] == []


def test_egen_rad_er_ufolsom_for_store_bokstaver():
    assert egen_rad("Bjorkli")["id"] == "bjorkli"
    assert egen_rad(" bjorkli ")["id"] == "bjorkli"
    assert egen_rad("finnes_ikke") is None


def test_svaret_kopieres_ut_og_kan_ikke_mutere_registeret():
    """Handleren serialiserer det den får. Returnerte vi de interne radene,
    kunne en senere kaller ha endret registeret for alle andre økter."""
    svar = svar_for("bjorkli", [PLATTFORMDRIFT])
    svar["tenanter"][0]["navn"] = "endret"
    svar["tenanter"][0]["moduler"].append(99)
    friskt = svar_for("bjorkli", [PLATTFORMDRIFT])
    assert friskt["tenanter"][0]["navn"] != "endret"
    assert 99 not in friskt["tenanter"][0]["moduler"]


def test_raden_har_formen_flaten_leser():
    """Feltene admin-flaten rendrer som DATA: `navn`, `plan` og `neste` er
    verdier (ikke locale-nøkler — kundenavn er ikke oversettelser), og
    `moduler` er ID-er flaten slår opp i modulkatalogen med `modulerFraIder`."""
    for rad in svar_for("bjorkli", [PLATTFORMDRIFT])["tenanter"]:
        assert set(rad) == {"id", "navn", "plan", "moduler", "neste"}
        assert rad["navn"] and rad["plan"] and rad["neste"]
        assert all(isinstance(m, int) for m in rad["moduler"])


# ---------------------------------------------------------------------------
# Språk (P2, Codex runde 4)
#
# `plan` og `neste` var norske literaler som admin-flaten rendret verbatim: på
# engelsk viste tabellen «Internt» og norske setninger. De to feltene er ikke
# samme slags verdi, og løses derfor ikke likt:
#
#   * `plan` er et LUKKET vokabular → serveren sender koden, klienten slår opp
#     `site.plan.<kode>`. Etiketten er chrome og hører hjemme i locale-settet.
#   * `neste` er FRITEKST per kunde → den kan ikke være en locale-nøkkel uten
#     å legge tenantdata tilbake i en anonymt nedlastbar fil (`/ui/locale/nb`
#     svarer 200 uten cookie), så oversettelsen følger raden ut herfra.
# ---------------------------------------------------------------------------

def test_plankoden_har_en_etikett_i_hvert_sprak():
    """Porten mellom serverens vokabular og klientens locale-sett. Uten den
    kan en ny plan legges inn her og vises som råkoden `internt` ute i
    tabellen — locale-kontrakten fanger den ikke, fordi flaten bygger nøkkelen
    `site.plan.${kode}` dynamisk."""
    koder = {r["plan"] for r in svar_for("x", [PLATTFORMDRIFT])["tenanter"]}
    assert koder, "ingen plankoder å binde"
    for sprak in SPRAK:
        locale = json.loads((ROT / "locales" / f"{sprak}.json")
                            .read_text(encoding="utf-8"))
        for kode in koder:
            assert kode == kode.lower(), f"{kode} er en etikett, ikke en kode"
            assert locale.get(f"site.plan.{kode}"), \
                f"{sprak}.json mangler site.plan.{kode}"


def test_fritekst_oversettes_og_faller_til_norsk():
    nb = svar_for("granmo", [], "nb")["tenanter"][0]["neste"]
    en = svar_for("granmo", [], "en")["tenanter"][0]["neste"]
    assert nb and en and nb != en, "«neste steg» er ikke oversatt"
    # Ukjent, tomt og manglende språk skal gi norsk tekst — aldri en tom celle.
    for ukjent in ("de", "", None, "nb-NO"):
        assert svar_for("granmo", [], ukjent)["tenanter"][0]["neste"] == nb


def test_spraket_velger_tekst_men_aldri_rader():
    """`sprak` er en PRESENTASJONSparameter. Den kommer fra spørrestrengen, og
    skal ikke kunne påvirke autorisasjonen: samme rader uansett verdi."""
    for sprak in ("nb", "en", "../../etc", None):
        svar = svar_for("bjorkli", [], sprak)
        assert [r["id"] for r in svar["tenanter"]] == ["bjorkli"]
        assert svar["plattformdrift"] is False


def test_egen_rad_oversettes_ogsa():
    assert egen_rad("granmo", "en")["neste"] != egen_rad("granmo", "nb")["neste"]


def test_disponit_tenanten_har_modultildeling():
    """Regresjon (målt 24/8): plattformens egen tenant manglet i tabellen, og
    eierens innlogging ga «modultildelingen er ikke tilgjengelig» —
    `moduler is None` og `ui.shell.moduler_ukjent` i klienten. `svar_for`
    er hele autorisasjonsregelen, så raden er reelt dekket her uten HTTP."""
    svar = svar_for("disponit", KUNDESCOPES)
    assert svar["moduler"] is not None and svar["moduler"]
    assert svar["tenanter"][0]["id"] == "disponit"
