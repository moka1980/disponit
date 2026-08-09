"""PR-012 CP4b: motorens menneskelige godkjenningsgren (form C).

Den bindende negative suiten fra v7 §5 + v8 §4 + CP4-klarsignalets sju porter.
Kjernekravet (presisering 3 / port 6): UTEN `menneskelig_godkjenning` er
`evaluate` bit-identisk med `_evaluer` — samme utfall OG begrunnelseskjede.
Den nye grenen legger seg ETTER den ordinære evalueringen og kan aldri endre
når eller i hvilken rekkefølge de eksisterende kontrollene kjører.

Godkjenningen løfter KUN én bunden grunnkode, innenfor motorens egen
`belop_maks`/`valuta`; alle øvrige kontroller må fortsatt passere.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from api import policyregister
from policy_validator.engine import (
    STOPP, TILLAT, UNNTAK, EvaluationContext, MenneskeligGodkjenning,
    _evaluer, _policy_innholds_hash, evaluate, parse_belop)

NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
CTX = EvaluationContext(tenant_id="t1", aktor_rolle="agent",
                        autentisert=True, kilde="api_token")


def policy(**over):
    p = {
        "schema_version": "0.2.0",
        "meta": {"policy_id": "test-mg", "versjon": "1.0.0"},
        "tidssone": "Europe/Oslo",
        "handlinger": [{
            "id": "faktura.bokfor",
            "modus": "auto",
            "ved_brudd": "unntakskø",
            "tillatt_for": ["agent"],
            "grenser": {"belop_maks": "25000.00", "valuta": ["NOK"]},
        }],
        "menneskelig_overstyring": {
            "godkjennbare": [{
                "grunnkode": "belop_over_grense", "handling": "faktura.bokfor",
                "belop_maks": "50000.00", "valuta": "NOK"}],
            "krever_rolle": "okonomi",
        },
    }
    p.update(over)
    return p


POL = policy()
HI = "a" * 64  # sakens handlingsintensjon-integritetshash


def hendelse(**over):
    """En hendelse porten bygger fra den DEKRYPTERTE handlingsintensjonen,
    inkl. de feltene motorens likhetskontroll trenger."""
    e = {"handling": "faktura.bokfor", "belop": "45000.00", "valuta": "NOK",
         "ressurs_id": "fak-1", "hi_integritet_hash": HI}
    e.update(over)
    return e


def godkjenning(pol=POL, ev=None, **over):
    ev = ev or hendelse()
    felt = {
        "tenant": "t1",
        "target_action": ev["handling"],
        "ressurs_id": ev["ressurs_id"],
        "belop": parse_belop(ev["belop"]),
        "valuta": ev["valuta"],
        "hi_integritet_hash": ev["hi_integritet_hash"],
        "bundet_grunnkode": "belop_over_grense",
        "unntak_id": 7,
        "runde": 1,
        "godkjennere": (("bruker-a", "okonomi", 3),),
        "godkjennings_policy_hash": _policy_innholds_hash(pol),
        "utloper": NAA + timedelta(hours=1),
    }
    felt.update(over)
    return MenneskeligGodkjenning(**felt)


# ---------- Port 6: fravær => bit-identisk (regresjonsporten) --------------

def test_fravaer_er_bit_identisk_med_evaluer():
    # For et representativt sett hendelser skal evaluate() uten godkjenning
    # gi NØYAKTIG samme beslutning og begrunnelseskjede som _evaluer().
    for ev in (hendelse(),                              # over grense -> UNNTAK
               hendelse(belop="10000.00"),              # innenfor -> TILLAT
               hendelse(valuta="EUR"),                  # belop-blokk uansett
               hendelse(handling="ukjent.handling")):   # ukjent handling
        a = evaluate(POL, CTX, ev, naa=NAA).to_dict()
        b = _evaluer(POL, CTX, ev, naa=NAA).to_dict()
        assert a == b, ev


def test_smugling_via_attestasjoner_ignoreres():
    # En «menneskelig_godkjenning» plassert i event["attestasjoner"] har null
    # effekt: motoren ser den aldri (den kan bare komme via parameteren).
    ev = hendelse(attestasjoner={"menneskelig_godkjenning": {"belop_maks": "999999"}})
    assert evaluate(POL, CTX, ev, naa=NAA).beslutning == UNNTAK


def test_krever_sikkerhetsrouting_default_av_og_ikke_i_logg():
    d = _evaluer(POL, CTX, hendelse(), naa=NAA)
    assert d.krever_sikkerhetsrouting is False
    assert "krever_sikkerhetsrouting" not in d.to_dict()


# ---------- Lykkelig sti + evidens -----------------------------------------

def test_gyldig_godkjenning_gir_tillat_med_bunden_grunnkode():
    d = evaluate(POL, CTX, hendelse(), naa=NAA, menneskelig_godkjenning=godkjenning())
    assert d.beslutning == TILLAT
    anvendt = [g for g in d.begrunnelse if g.kode == "menneskelig_godkjenning_anvendt"]
    assert len(anvendt) == 1
    p = anvendt[0].params
    assert p["bundet_grunnkode"] == "belop_over_grense"
    assert p["belop_maks"] == "50000.00"
    assert p["godkjennere"] == ["bruker-a"]
    assert p["runde"] == 1


def test_uten_godkjenning_er_saken_unntak():
    # Kontroll: samme hendelse UTEN godkjenning er og blir UNNTAK.
    assert evaluate(POL, CTX, hendelse(), naa=NAA).beslutning == UNNTAK


# ---------- Motoren eier grensen (v7 §2, v8 §1) ----------------------------

def test_belop_over_menneskelig_maks_gir_stopp():
    ev = hendelse(belop="55000.00")
    g = godkjenning(ev=ev)  # konvolutten matcher hendelsen (55000)
    d = evaluate(POL, CTX, ev, naa=NAA, menneskelig_godkjenning=g)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "godkjenning_belop_over_maks"


def test_rolle_mangler_gir_stopp():
    g = godkjenning(godkjennere=(("bruker-a", "leser", 3),))
    d = evaluate(POL, CTX, hendelse(), naa=NAA, menneskelig_godkjenning=g)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "godkjenning_rolle_mangler"
    assert d.krever_sikkerhetsrouting is True


def test_valuta_avvik_mot_godkjennbar_gir_stopp():
    # Blokken er belop_over_grense (belop sjekkes før valuta i motoren), men
    # den godkjennbare oppføringen tillater bare NOK — hendelsen er EUR.
    ev = hendelse(valuta="EUR")
    g = godkjenning(ev=ev)  # mg.valuta = EUR (matcher hendelsen)
    d = evaluate(POL, CTX, ev, naa=NAA, menneskelig_godkjenning=g)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "godkjenning_valuta_avvik"


# ---------- Seks feltavvik => STOPP + sikkerhetsrouting (v8 §1) -------------

def test_feltavvik_per_felt_gir_stopp_og_sikkerhetsrouting():
    ev = hendelse()
    avvik = {
        "tenant": dict(tenant="ANNEN"),
        "target_action": dict(target_action="annen.handling"),
        "ressurs_id": dict(ressurs_id="fak-999"),
        "belop": dict(belop=Decimal("1.00")),
        "valuta": dict(valuta="EUR"),
        "hi_integritet_hash": dict(hi_integritet_hash="b" * 64),
    }
    for felt, over in avvik.items():
        g = godkjenning(ev=ev, **over)
        d = evaluate(POL, CTX, ev, naa=NAA, menneskelig_godkjenning=g)
        assert d.beslutning == STOPP, felt
        assert d.begrunnelse[-1].kode == "godkjenning_feltavvik", felt
        assert d.begrunnelse[-1].params["felt"] == felt, felt
        assert d.krever_sikkerhetsrouting is True, felt


def test_hvert_felt_er_mutasjonstestet():
    # Fjernes én likhetskontroll (her: vi muterer hendelsen bort fra
    # konvolutten), MÅ utfallet endre seg fra TILLAT til STOPP. Dette er
    # port 2: ingen av de seks kan fjernes uten en rød test.
    for over in (dict(ressurs_id="fak-annen"), dict(belop="44000.00"),
                 dict(valuta="EUR"), dict(hi_integritet_hash="c" * 64)):
        ev = hendelse(**over)  # hendelsen avviker fra konvolutten (bygd på HI/45000/NOK/fak-1)
        d = evaluate(POL, CTX, ev, naa=NAA, menneskelig_godkjenning=godkjenning())
        assert d.beslutning == STOPP, over


# ---------- Policyhash (v7 §4) ---------------------------------------------

def test_policy_hash_avvik_gir_stopp():
    g = godkjenning(godkjennings_policy_hash="0" * 64)
    d = evaluate(POL, CTX, hendelse(), naa=NAA, menneskelig_godkjenning=g)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "godkjenning_policy_avvik"
    assert d.krever_sikkerhetsrouting is True


def test_policy_innholds_hash_speiler_registeret():
    # Motorens hash MÅ være bit-identisk med policyregisterets, ellers ville
    # en gyldig godkjenning blitt avvist på policy_avvik.
    assert _policy_innholds_hash(POL) == policyregister.innholds_hash(POL)


def test_utlopt_godkjenning_gir_stopp():
    g = godkjenning(utloper=NAA - timedelta(seconds=1))
    d = evaluate(POL, CTX, hendelse(), naa=NAA, menneskelig_godkjenning=g)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "godkjenning_utlopt"


# ---------- Én grunnkode, aldri alt (v8 §2) --------------------------------

def test_grunnkode_utenfor_godkjennbare_er_usynlig():
    # Policy uten en godkjennbar oppføring for belop_over_grense => ingen
    # overstyring; utfallet er nøyaktig det motoren ellers ga.
    pol = policy(menneskelig_overstyring={
        "godkjennbare": [{"grunnkode": "utenfor_tidsvindu",
                          "handling": "faktura.bokfor"}],
        "krever_rolle": "okonomi"})
    g = godkjenning(pol=pol)
    d = evaluate(pol, CTX, hendelse(), naa=NAA, menneskelig_godkjenning=g)
    assert d.to_dict() == _evaluer(pol, CTX, hendelse(), naa=NAA).to_dict()


def test_bunden_grunn_ikke_lenger_blokkerende_gir_uendret():
    # Hendelsen er nå innenfor grensen => pass 1 er TILLAT allerede;
    # godkjenningen anvendes ikke (ingen menneskelig_godkjenning_anvendt).
    ev = hendelse(belop="10000.00")
    d = evaluate(POL, CTX, ev, naa=NAA, menneskelig_godkjenning=godkjenning(ev=ev))
    assert d.beslutning == TILLAT
    assert all(g.kode != "menneskelig_godkjenning_anvendt" for g in d.begrunnelse)


def test_bunden_grunn_matcher_ikke_faktisk_blokk_gir_uendret():
    # Saken blokkerer på rolle (ikke tillatt), men konvolutten er bundet til
    # belop_over_grense => grunnkoden er ikke den som blokkerer => uendret.
    ctx = EvaluationContext(tenant_id="t1", aktor_rolle="fremmed",
                            autentisert=True, kilde="api_token")
    g = godkjenning()
    d = evaluate(POL, ctx, hendelse(), naa=NAA, menneskelig_godkjenning=g)
    assert d.to_dict() == _evaluer(POL, ctx, hendelse(), naa=NAA).to_dict()


def test_flere_blokkerende_grunner_gir_ingen_tillat():
    # Blokk 1 (belop) løftes, men hendelsen er også EUR, som den godkjennbare
    # oppføringen her tillater — så pass 2 treffer valuta_ikke_tillatt i
    # policyen (NOK) og stopper. Godkjenningen dekker ikke den andre grunnen.
    pol = policy(menneskelig_overstyring={
        "godkjennbare": [{"grunnkode": "belop_over_grense",
                          "handling": "faktura.bokfor",
                          "belop_maks": "50000.00", "valuta": "EUR"}],
        "krever_rolle": "okonomi"})
    ev = hendelse(valuta="EUR")
    g = godkjenning(pol=pol, ev=ev)
    d = evaluate(pol, CTX, ev, naa=NAA, menneskelig_godkjenning=g)
    assert d.beslutning != TILLAT
    assert d.begrunnelse[-1].kode == "valuta_ikke_tillatt"
