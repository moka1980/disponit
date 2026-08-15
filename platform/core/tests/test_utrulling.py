"""Autorisasjonsregelen for `/v1/utrulling` (P1, Codex runde 3).

`svar_for` er hele regelen for hvilke utrullingsrader som forlater serveren,
og den er ren: den testes uten DB, uten HTTP og uten klient. Det er poenget —
tidligere lå tabellen i den statisk serverte klientbunten, og «autorisasjonen»
var et filter i DOM-en hos den som allerede hadde lastet ned alt.
"""
from __future__ import annotations

from api.utrulling import PLATTFORMDRIFT, egen_rad, svar_for

KUNDESCOPES = ["decisions:read", "exceptions:read", "policy:read",
               "security:read"]


def test_kundeokt_far_bare_sin_egen_rad():
    svar = svar_for("bjorkli", KUNDESCOPES)
    assert svar["plattformdrift"] is False
    assert [r["id"] for r in svar["tenanter"]] == ["bjorkli"]
    assert svar["moduler"] == [1, 2]


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
