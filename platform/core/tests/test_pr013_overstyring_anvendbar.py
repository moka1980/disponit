"""Codex P1 på PR #111: en menneskelig overstyring motoren ikke KAN anvende.

`menneskelig_overstyring.godkjennbare` beskriver hvilke blokkerende utfall et
menneske kan godkjenne seg forbi. Godkjenningen virker bare hvis motoren klarer
å bygge et LØFT av den — og `_loft_policy` uttrykker nøyaktig to ting:
`belop_maks` og `valuta` (`engine.LOFTBARE_GRUNNKODER`). Alt annet gir None, og
fail-closed betyr da STOPP.

Feilen var at skjemaet slapp begge de virkningsløse formene gjennom:

  * en grunnkode motoren ikke kan løfte i det hele tatt (`utenfor_tidsvindu`,
    `frekvensgrense_naadd`, …), og
  * en løftbar grunnkode UTEN verdien å løfte til (`belop_over_grense` uten
    `belop_maks`).

Eier kunne dermed aktivere det som så ut som en konfigurert overstyring, mens
HVER matchende godkjenning endte i STOPP — uten at noe sa fra.

Kravet bor i INNFØRINGSKONTRAKTEN (`valider_ny_policy`), ikke i lastekontrakten
(`valider_policy`): en policy som allerede er aktiv med en slik oppføring virker
akkurat som før (den ene overstyringen har aldri gjort noe), og skal ikke bli
korrupt ved lasting i det utrullingen lander. Testene under fester begge
halvdelene, og at motoren og kontrakten leser SAMME kilde.

Rene validator-/motortester (ingen DB).
"""
import copy
from pathlib import Path

import yaml

from policy_validator import engine
from policy_validator.engine import LOFTBARE_GRUNNKODER
from policy_validator.schema import (IKKE_MENNESKELIG_GODKJENNBARE,
                                     valider_ny_policy, valider_policy)

_BASE = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "policies"
     / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))

_HANDLING = "faktura.bokfor"


def _med_overstyring(*oppforinger):
    """Basen med `menneskelig_overstyring` satt til de gitte oppføringene."""
    p = copy.deepcopy(_BASE)
    p["menneskelig_overstyring"] = {
        "godkjennbare": [dict(o) for o in oppforinger],
        "krever_rolle": "daglig_leder"}
    return p


def _oppforing(grunnkode, **felt):
    return {"grunnkode": grunnkode, "handling": _HANDLING, **felt}


# --- innføringskontrakten avviser det virkningsløse ------------------------

#: En oppføring per løftbar grunnkode som FAKTISK kan virke for `_HANDLING`
#: (grensene der er `belop_maks` 25000.00 og `valuta` ["NOK"]): taket må ligge
#: OVER handlingens egen grense, og valutaen som løftes inn må være en
#: handlingen ikke alt tillater.
_VIRKSOMME = {"belop_maks": {"belop_maks": "50000.00", "valuta": "NOK"},
              "valuta": {"valuta": "EUR"}}


def test_loftbar_oppforing_med_verdien_sin_passerer():
    # Fanger at innstrammingen ikke avviser den formen som FAKTISK virker.
    for gk, felt in LOFTBARE_GRUNNKODER.items():
        p = _med_overstyring(_oppforing(gk, **_VIRKSOMME[felt]))
        assert valider_ny_policy(p) == [], gk


def test_ikke_loftbar_grunnkode_avvises_ved_innforing():
    # Selve funnet: motoren har ingen gren for disse, så en godkjenning
    # ender alltid i STOPP.
    for gk in ("utenfor_tidsvindu", "frekvensgrense_naadd",
               "dataklasse_ikke_tillatt", "rolle_ikke_tillatt",
               "modus_alltid_stopp"):
        feil = valider_ny_policy(_med_overstyring(_oppforing(gk)))
        assert feil, f"{gk} kan ikke løftes og må avvises"
        assert any("menneskelig_overstyring" in f for f in feil), feil


def test_loftbar_grunnkode_uten_verdien_sin_avvises():
    # Den andre halvdelen: koden er løftbar, men det finnes ingen verdi å
    # løfte TIL — like virkningsløst som en ikke-løftbar kode.
    feil = valider_ny_policy(_med_overstyring(_oppforing("belop_over_grense")))
    assert feil, "belop_over_grense uten belop_maks må avvises"
    assert any("belop_maks" in f for f in feil), feil

    feil = valider_ny_policy(_med_overstyring(_oppforing("valuta_ikke_tillatt")))
    assert feil, "valuta_ikke_tillatt uten valuta må avvises"
    assert any("valuta" in f for f in feil), feil


# --- verdien må ligge der den flytter noe (Codex P1, runde 7) -------------

def test_belopstak_som_ikke_er_hoyere_enn_grensen_avvises():
    """`belop_over_grense` oppstår KUN når beløpet er over handlingens egen
    grense. Et overstyringstak på eller under den grensen kan derfor aldri
    slippe noe gjennom: hvert beløp som utløste blokkeringen er også over
    taket, og steg 7 i motoren stopper det."""
    for tak in ("25000.00", "10000.00", "0"):
        feil = valider_ny_policy(_med_overstyring(
            _oppforing("belop_over_grense", belop_maks=tak, valuta="NOK")))
        assert feil, f"tak {tak} kan aldri løfte noe og må avvises"
        assert any("belop_maks" in f and "25000.00" in f for f in feil), feil
    # Ett øre over grensen ER et løft — innstrammingen skal ikke ta det.
    assert valider_ny_policy(_med_overstyring(
        _oppforing("belop_over_grense", belop_maks="25000.01",
                   valuta="NOK"))) == []


def test_valuta_handlingen_alt_tillater_avvises():
    """Den andre siden: en hendelse som blokkeres på `valuta_ikke_tillatt`
    bærer nødvendigvis en valuta handlingen IKKE tillater, og steg 7 krever
    at godkjenningens valuta er hendelsens. Peker oppføringen på en valuta
    som alt er lov, kan den aldri matche."""
    feil = valider_ny_policy(_med_overstyring(
        _oppforing("valuta_ikke_tillatt", valuta="NOK")))
    assert feil, "en alt tillatt valuta kan aldri løftes inn"
    assert any("NOK" in f and _HANDLING in f for f in feil), feil
    assert valider_ny_policy(_med_overstyring(
        _oppforing("valuta_ikke_tillatt", valuta="EUR"))) == []


def test_loftets_valuta_maa_vaere_tillatt_for_handlingen():
    """Løftet hever BELØPET, ikke valutaen. Krever oppføringen en valuta
    handlingen ikke tillater, stopper den gjenopptatte evalueringen på
    `valuta_ikke_tillatt` uansett hvor høyt taket er."""
    feil = valider_ny_policy(_med_overstyring(
        _oppforing("belop_over_grense", belop_maks="50000.00", valuta="EUR")))
    assert feil, "EUR er ikke tillatt for handlingen; løftet stopper der"
    assert any("EUR" in f for f in feil), feil


def test_grunnkode_handlingen_aldri_kan_gi_avvises():
    """En handling uten den grensen grunnkoden kommer FRA vil aldri
    produsere den — oppføringen venter på et utfall som ikke finnes.
    `epost.send_kjent_mottaker` har ingen `grenser` i det hele tatt."""
    uten = "epost.send_kjent_mottaker"
    for oppf in ({"grunnkode": "belop_over_grense", "handling": uten,
                  "belop_maks": "50000.00", "valuta": "NOK"},
                 {"grunnkode": "valuta_ikke_tillatt", "handling": uten,
                  "valuta": "EUR"}):
        feil = valider_ny_policy(_med_overstyring(oppf))
        assert feil, oppf
        assert any(uten in f for f in feil), feil


def test_motoren_stopper_faktisk_den_avviste_formen():
    """Kontraktens påstand målt mot MOTOREN, ikke mot seg selv. Hadde en av
    de avviste formene likevel gitt TILLAT for en hendelse, ville
    innstrammingen tatt fra eier en overstyring som virket."""
    from datetime import datetime, timedelta, timezone

    from policy_validator.engine import (STOPP, EvaluationContext,
                                         MenneskeligGodkjenning,
                                         _policy_innholds_hash, evaluate,
                                         parse_belop)
    naa = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    ctx = EvaluationContext(tenant_id="t1", aktor_rolle="agent",
                            autentisert=True, kilde="api_token")
    pol = {
        "schema_version": "0.2.0",
        "meta": {"policy_id": "test-mg", "versjon": "1.0.0"},
        "tidssone": "Europe/Oslo",
        "handlinger": [{"id": "faktura.bokfor", "modus": "auto",
                        "ved_brudd": "unntakskø", "tillatt_for": ["agent"],
                        "grenser": {"belop_maks": "25000.00",
                                    "valuta": ["NOK"]}}],
        "menneskelig_overstyring": {
            "godkjennbare": [{"grunnkode": "belop_over_grense",
                              "handling": "faktura.bokfor",
                              "belop_maks": "25000.00", "valuta": "NOK"}],
            "krever_rolle": "okonomi"},
    }
    hi = "a" * 64
    # Hvert beløp som i det hele tatt utløser `belop_over_grense` — altså
    # alt over 25000.00 — må ende i STOPP med taket satt likt grensen.
    for belop in ("25000.01", "30000.00", "999999.00"):
        ev = {"handling": "faktura.bokfor", "belop": belop, "valuta": "NOK",
              "ressurs_id": "fak-1", "hi_integritet_hash": hi}
        mg = MenneskeligGodkjenning(
            tenant="t1", target_action=ev["handling"],
            ressurs_id=ev["ressurs_id"], belop=parse_belop(belop),
            valuta="NOK", hi_integritet_hash=hi,
            bundet_grunnkode="belop_over_grense", unntak_id=7, runde=1,
            godkjennere=(("bruker-a", "okonomi", 3),),
            godkjennings_policy_hash=_policy_innholds_hash(pol),
            utloper=naa + timedelta(hours=1))
        d = evaluate(pol, ctx, ev, naa=naa, menneskelig_godkjenning=mg)
        assert d.beslutning == STOPP, (belop, d.to_dict())


# --- handlingen må i det hele tatt NÅ kontrollen (Codex P1, runde 8) ------

#: Hvordan hver løftbar grunnkode utløses for `_HANDLING` (`belop_maks`
#: 25000.00, `valuta` ["NOK"]). Brukes til å BEVISE at `alltid_stopp` feller
#: handlingen før kontrollen koden kommer fra. En ny kode uten en oppskrift
#: her stopper `test_ingen_loftbar_grunnkode_naas_ved_alltid_stopp`.
_UTLOSER = {"belop_over_grense": {"belop": "99999.00", "valuta": "NOK"},
            "valuta_ikke_tillatt": {"belop": "100.00", "valuta": "EUR"}}


def _med_modus(modus, handling_id=_HANDLING):
    p = copy.deepcopy(_BASE)
    for h in p["handlinger"]:
        if h["id"] == handling_id:
            h["modus"] = modus
    return p


def test_ingen_loftbar_grunnkode_naas_ved_alltid_stopp():
    """Vakten under påstanden. Motoren feller `alltid_stopp` i steg 2, altså
    FØR beløp (steg 4) og valuta (steg 5) vurderes: en hendelse som ellers
    ville gitt en løftbar grunnkode gir `modus_alltid_stopp` i stedet.

    Flyttes modussjekken bak grensene, ryker denne — og da er avvisningen i
    `_loftet_flytter_noe` ikke lenger sann."""
    from datetime import datetime, timezone

    from policy_validator.engine import (MODUS_UTEN_LOFTBARE_UTFALL,
                                         EvaluationContext, evaluate)
    assert set(_UTLOSER) == set(LOFTBARE_GRUNNKODER), (
        "en løftbar grunnkode uten en oppskrift her er ubevist")
    naa = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
    ctx = EvaluationContext(tenant_id="t1", aktor_rolle="agent",
                            autentisert=True, kilde="api_token")
    pol = _med_modus(MODUS_UTEN_LOFTBARE_UTFALL)
    for gk, hendelse in _UTLOSER.items():
        d = evaluate(pol, ctx, {"handling": _HANDLING, "ressurs_id": "r-1",
                                **hendelse}, naa=naa)
        koder = {g.kode for g in d.begrunnelse}
        assert "modus_alltid_stopp" in koder, (gk, d.to_dict())
        assert gk not in koder, (
            f"{gk} nås likevel — avvisningen i _loftet_flytter_noe er feil")


def test_overstyring_paa_alltid_stopp_handling_avvises():
    """Selve funnet: grensene på handlingen kan være aldri så velegnet — når
    modusen feller den før de vurderes, kan overstyringen aldri anvendes."""
    from policy_validator.engine import MODUS_UTEN_LOFTBARE_UTFALL
    for gk, felt in LOFTBARE_GRUNNKODER.items():
        p = _med_modus(MODUS_UTEN_LOFTBARE_UTFALL)
        p["menneskelig_overstyring"] = {
            "godkjennbare": [_oppforing(gk, **_VIRKSOMME[felt])],
            "krever_rolle": "daglig_leder"}
        feil = valider_ny_policy(p)
        assert feil, f"{gk} kan aldri oppstå for en alltid_stopp-handling"
        assert any(_HANDLING in f and MODUS_UTEN_LOFTBARE_UTFALL in f
                   for f in feil), feil


def test_de_andre_modusene_slipper_overstyringen_gjennom():
    # Innstrammingen skal treffe NØYAKTIG den modusen som kortslutter.
    for modus in ("auto", "auto_med_vilkaar"):
        for gk, felt in LOFTBARE_GRUNNKODER.items():
            p = _med_modus(modus)
            p["menneskelig_overstyring"] = {
                "godkjennbare": [_oppforing(gk, **_VIRKSOMME[felt])],
                "krever_rolle": "daglig_leder"}
            assert valider_ny_policy(p) == [], (modus, gk)


def test_lastekontrakten_slipper_alltid_stopp_overstyringen():
    """Framoverrettet som resten: en alt aktiv policy med en slik oppføring
    har aldri løftet noe, og skal ikke bli korrupt ved lasting."""
    from policy_validator.engine import MODUS_UTEN_LOFTBARE_UTFALL
    p = _med_modus(MODUS_UTEN_LOFTBARE_UTFALL)
    p["menneskelig_overstyring"] = {
        "godkjennbare": [_oppforing("belop_over_grense",
                                    **_VIRKSOMME["belop_maks"])],
        "krever_rolle": "daglig_leder"}
    assert valider_policy(p) == []


def test_hver_loftbar_grunnkode_maales_mot_handlingen():
    """Vakten mot stille fail-open. En ny kode i `LOFTBARE_GRUNNKODER` uten
    en gren i `_loftet_flytter_noe` ville sluppet gjennom nøyaktig de
    virkningsløse verdiene runde 7 stengte — bare for den nye koden."""
    from policy_validator.schema import ANVENDBARHET_MALT
    assert ANVENDBARHET_MALT == set(LOFTBARE_GRUNNKODER)


def test_lastekontrakten_slipper_ogsaa_den_virkningslose_verdien():
    """Framoverrettet, som resten: en alt aktiv policy med et for lavt tak
    har aldri løftet noe, og skal ikke bli korrupt ved lasting."""
    for oppf in (_oppforing("belop_over_grense", belop_maks="10000.00",
                            valuta="NOK"),
                 _oppforing("valuta_ikke_tillatt", valuta="NOK")):
        assert valider_policy(_med_overstyring(oppf)) == [], oppf


def test_feilmeldingen_navngir_de_loftbare_kodene():
    # Eier skal kunne rette uten å lese motorkoden.
    feil = valider_ny_policy(_med_overstyring(_oppforing("utenfor_tidsvindu")))
    assert any(all(gk in f for gk in LOFTBARE_GRUNNKODER) for f in feil), feil


def test_oppforingens_indeks_star_i_feilen():
    # Med flere oppføringer må eier få vite HVILKEN som er ubrukelig.
    p = _med_overstyring(
        _oppforing("belop_over_grense", **_VIRKSOMME["belop_maks"]),
        _oppforing("utenfor_tidsvindu"))
    feil = valider_ny_policy(p)
    assert any("[1]" in f for f in feil), feil
    assert not any("[0]" in f for f in feil), feil


# --- lastekontrakten er urørt (alt aktive policyer virker som før) ---------

def test_lastekontrakten_slipper_den_alt_aktive_policyen_gjennom():
    """Kravet er FRAMOVERRETTET. `hent_aktiv` revaliderer den LAGREDE policyen
    ved hver forespørsel; en innstramming i lastekontrakten ville gjort en alt
    aktiv policy korrupt i det utrullingen landet — tenanten mister da alle
    policystyrte beslutninger på grunn av én overstyring som uansett aldri har
    gjort noe."""
    for oppf in (_oppforing("utenfor_tidsvindu"),
                 _oppforing("belop_over_grense")):
        assert valider_policy(_med_overstyring(oppf)) == [], oppf


def test_basen_er_fortsatt_gyldig():
    assert valider_policy(copy.deepcopy(_BASE)) == []
    assert valider_ny_policy(copy.deepcopy(_BASE)) == []


# --- kontrakten og motoren kan ikke komme fra hverandre --------------------

def test_hver_loftbar_grunnkode_gir_faktisk_et_loft():
    """Kilden kontrakten avviser mot ER motorens uttrykkskraft. Står en kode i
    `LOFTBARE_GRUNNKODER` uten en gren i `_loft_policy`, slipper kontrakten
    gjennom en oppføring som likevel ender i STOPP — nøyaktig feilen dette
    fikset. Da skal denne bli rød."""
    for gk, felt in LOFTBARE_GRUNNKODER.items():
        verdi = {"belop_maks": "50000.00", "valuta": "NOK"}[felt]
        entry = _oppforing(gk, **{felt: verdi})
        loftet = engine._loft_policy(copy.deepcopy(_BASE), _HANDLING, gk, entry)
        assert loftet is not None, f"{gk} står som løftbar, men gir ikke løft"


def test_en_kode_utenfor_lista_gir_ingen_loft():
    # Den andre retningen: fail-closed er fortsatt fail-closed.
    entry = _oppforing("utenfor_tidsvindu")
    assert engine._loft_policy(
        copy.deepcopy(_BASE), _HANDLING, "utenfor_tidsvindu", entry) is None


def test_loftbar_uten_verdi_gir_ingen_loft():
    for gk in LOFTBARE_GRUNNKODER:
        assert engine._loft_policy(
            copy.deepcopy(_BASE), _HANDLING, gk, _oppforing(gk)) is None, gk


def test_editorgrunnlaget_leser_samme_kilde_som_kontrakten():
    """Den tredje leseren. Tilbyr flaten en kode kontrakten avviser, kan eier
    bygge et utkast som ikke lar seg aktivere; tilbyr den for få, er en lovlig
    overstyring uoppnåelig fra editoren. Begge deler er utelukket så lenge alle
    tre leser `LOFTBARE_GRUNNKODER`."""
    from api.policy_historikk import _loftbare
    assert _loftbare() is LOFTBARE_GRUNNKODER


def test_de_loftbare_kodene_er_ikke_i_deny_settet():
    """De to listene er forskjellige spørsmål — «SKAL et menneske få godkjenne
    dette?» og «KAN motoren løfte det?» — men de må ikke motsi hverandre: en
    kode vi tilbyr og validerer mot kan ikke samtidig være en et menneske aldri
    skal få godkjenne."""
    assert not (set(LOFTBARE_GRUNNKODER) & IKKE_MENNESKELIG_GODKJENNBARE)
