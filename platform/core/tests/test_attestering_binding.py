"""PR-005a: bindingsfelter i attestasjoner.

En gyldig signatur beviser at attestasjonen er UTSTEDT av en betrodd
verifikator. Den beviser ikke at den gjelder DENNE forespørselen. Uten
binding kan en ekte, signert attestasjon gjenbrukes på en annen tenant, en
annen handling, en annen ressurs eller på et senere tidspunkt — og
allowlisten fra PR-004 ser ingenting galt, fordi verifikatoren er kjent og
signaturen ekte.

Én negativ test per feilvei. Hver av dem skal falle hvis vakten fjernes.
"""
from datetime import datetime, timedelta, timezone

import pytest

from policy_validator import attestering
from policy_validator.engine import EvaluationContext

NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
HEM = "h" * 48
NOKLER = {"v_regnskap": {"k1": HEM}}
CTX = EvaluationContext("t-1", "agent", True, "api_token")
HANDLING = "faktura.bokfor"
POLICY_ID = "tjenestebedrift-no@0.2.0"


def att(**over):
    a = {
        "verifikator": "v_regnskap",
        "tenant_id": "t-1",
        "handling": HANDLING,
        "vilkaar": "dublettsjekk",
        "ressurs_id": "fak-1",
        "policy_id": POLICY_ID,
        "utstedt": (NAA - timedelta(minutes=5)).isoformat(),
        "utloper": (NAA + timedelta(hours=1)).isoformat(),
        "jti": "jti-0123456789abcdef-0123",
        "resultat": True,
    }
    a.update(over)
    return attestering.signer(a, "k1", HEM)


def hendelse(a=None, ressurs="fak-1"):
    return {"handling": HANDLING, "ressurs_id": ressurs,
            "attestasjoner": {"dublettsjekk": a if a is not None else att()}}


def kontroller(event):
    return attestering.kontroller_binding(event, CTX, HANDLING, POLICY_ID, NAA)


def test_riktig_bundet_attestasjon_slipper_gjennom():
    assert kontroller(hendelse()) is None


def test_signaturen_daekker_bindingsfeltene():
    """Selve premisset: endres et bindingsfelt etter signering, ryker
    signaturen. Uten dette kunne en angriper bare skrive om feltene."""
    a = att()
    a["tenant_id"] = "t-2"
    assert attestering.verifiser(a, NOKLER) is False


@pytest.mark.parametrize("felt", attestering.BINDINGSFELT)
def test_manglende_bindingsfelt_avvises(felt):
    a = att()
    del a[felt]
    g = kontroller(hendelse(a))
    assert g is not None and g.kode == "attestasjon_mangler_binding"
    assert felt in g.params["felt"]


def test_pr004_format_avvises_paa_api_veien():
    """En attestasjon fra PR-004 har verifikator, ressurs_id, utloper og
    signatur — men ingen binding. Den skal ikke slippe inn over nettverk."""
    gammel = attestering.signer(
        {"verifikator": "v_regnskap", "ressurs_id": "fak-1",
         "utloper": (NAA + timedelta(hours=1)).isoformat(), "resultat": True},
        "k1", HEM)
    assert attestering.verifiser(gammel, NOKLER) is True   # signaturen er ekte
    g = kontroller(hendelse(gammel))
    assert g is not None and g.kode == "attestasjon_mangler_binding"


@pytest.mark.parametrize("felt,verdi,kode", [
    ("tenant_id", "t-2", "attestasjon_feil_tenant"),
    ("handling", "betaling.utfor", "attestasjon_feil_handling"),
    ("vilkaar", "noe_annet", "attestasjon_feil_vilkaar"),
    ("policy_id", "annen-policy@1.0", "attestasjon_feil_policy"),
    ("ressurs_id", "fak-999", "attestasjon_feil_ressurs"),
])
def test_attestasjon_for_en_annen_forespoersel_avvises(felt, verdi, kode):
    """Gjenbruk på tvers: signaturen er gyldig, men bindingen stemmer ikke."""
    a = att(**{felt: verdi})
    assert attestering.verifiser(a, NOKLER) is True
    g = kontroller(hendelse(a))
    assert g is not None and g.kode == kode


def test_utloept_attestasjon_avvises():
    a = att(utstedt=(NAA - timedelta(hours=2)).isoformat(),
            utloper=(NAA - timedelta(hours=1)).isoformat())
    g = kontroller(hendelse(a))
    assert g is not None and g.kode == "attestasjon_utenfor_gyldighet"


def test_attestasjon_fra_framtiden_avvises():
    a = att(utstedt=(NAA + timedelta(hours=1)).isoformat(),
            utloper=(NAA + timedelta(hours=2)).isoformat())
    g = kontroller(hendelse(a))
    assert g is not None and g.kode == "attestasjon_utenfor_gyldighet"


def test_gyldighet_er_halvaapent_intervall():
    """utloper er eksklusiv: nøyaktig på utløpstidspunktet er den ute."""
    a = att(utloper=NAA.isoformat())
    g = kontroller(hendelse(a))
    assert g is not None and g.kode == "attestasjon_utenfor_gyldighet"


@pytest.mark.parametrize("utstedt,utloper", [
    ("2026-08-03T09:00:00", "2026-08-03T11:00:00+00:00"),   # naiv utstedt
    ("2026-08-03T09:00:00+00:00", "2026-08-03T11:00:00"),   # naiv utloper
    ("ikke en dato", "2026-08-03T11:00:00+00:00"),
    ("2026-08-03T11:00:00+00:00", "2026-08-03T09:00:00+00:00"),  # baklengs
])
def test_ugyldige_tidsstempler_avvises(utstedt, utloper):
    g = kontroller(hendelse(att(utstedt=utstedt, utloper=utloper)))
    assert g is not None and g.kode == "attestasjon_tid_ugyldig"


@pytest.mark.parametrize("jti", [None, "", "   ", "kort",
                                 "jti-for-kort-21tegn", 12345])
def test_ubrukelig_jti_avvises(jti):
    g = kontroller(hendelse(att(jti=jti)))
    assert g is not None and g.kode in ("attestasjon_jti_ugyldig",
                                        "attestasjon_mangler_binding")


def test_jti_liste_er_stabilt_sortert():
    """API-veien tar låser i denne rekkefølgen. To samtidige forespørsler
    med samme attestasjoner må ta dem i samme rekkefølge, ellers kan de
    vranglåse hverandre."""
    e = {"attestasjoner": {
        "b": att(vilkaar="b", jti="jti-bbbbbbbbbbbbbbbbbbbbbb"),
        "a": att(vilkaar="a", jti="jti-aaaaaaaaaaaaaaaaaaaaaa")}}
    assert attestering.jti_liste(e) == [("a", "jti-aaaaaaaaaaaaaaaaaaaaaa"),
                                        ("b", "jti-bbbbbbbbbbbbbbbbbbbbbb")]
    omvendt = {"attestasjoner": dict(reversed(list(e["attestasjoner"].items())))}
    assert attestering.jti_liste(omvendt) == attestering.jti_liste(e)


def test_hendelse_uten_attestasjoner_passerer_bindingsporten():
    """Motoren håndhever selv at påkrevde vilkår HAR attestasjon; denne
    porten sier bare noe om dem som finnes."""
    assert attestering.kontroller_binding(
        {"handling": HANDLING}, CTX, HANDLING, POLICY_ID, NAA) is None
