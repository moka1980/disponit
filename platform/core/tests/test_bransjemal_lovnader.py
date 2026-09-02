"""LØFTENE I BRANSJEMALENE, målt mot modulene som finnes.

Vi sender ut tre bransjemaler. Hver av dem navngir **verifikatorer** —
de betrodde partene som må attestere at et vilkår holder før en
`modus: auto`-handling får skje — og hver av dem knytter handlinger til
moduler ved nummer.

FLERE AV DEM FINNES IKKE. Det er utgangspunktet for klynge 4
(`docs/KLYNGE4-FUNDAMENT.md`), og denne fila er porten som gjør gapet
til et tall i stedet for noe man må huske.

TO TING MÅLES, og de er forskjellige:

  1. AT MOTOREN FEILER LUKKET. Fundamentdokumentet påstår det. En
     påstand i et dokument er ikke en port, så den kjøres her: en
     handling med et vilkår som ingen kan attestere, blokkeres.

  2. AT GAPET ER KJENT OG KRYMPER. Listen over manglende moduler står
     eksplisitt. En NY mangel — noen legger en verifikator til i en mal
     uten en modul bak — gjør porten rød. Og en modul som BLIR bygget
     uten å tas ut av listen gjør den også rød, så listen ikke kan bli
     stående som en gammel sannhet.
"""
from __future__ import annotations

import glob
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from policy_validator.engine import (STOPP, TILLAT, UNNTAK,
                                     EvaluationContext, evaluate)

ROT = Path(__file__).resolve().parents[3]
MALER = sorted(glob.glob(str(ROT / "policies" / "bransjemal-*.yaml")))

#: Modulene bransjemalene navngir, men som ikke finnes ennå — med
#: antall referanser og hvilken klynge de er tildelt.
#:
#: KLYNGE 4 tar de fem øverste (`docs/KLYNGE4-FUNDAMENT.md`); resten
#: står igjen. Tallene er de MÅLTE, ikke en preferanse.
VENTENDE: dict[str, str] = {
    "M-11": "adressevalidering (1 ref.) — ikke tildelt",
    "M-39": "lønnsgrunnlag (2 ref.) — klynge 5",
    "M-41": "betalings- og abonnementsstatus (3 ref.) — klynge 5",
    "M-44": "kampanjeutsending (1 ref.) — ikke tildelt",
}

#: Klynge 4s fem. De har manifest fra og med fundament-commiten, så de
#: skal IKKE stå i `VENTENDE` — men de har heller ingen kode, og
#: attestasjonsfullmakten tar de ikke i v1.
KLYNGE4 = ("M-14", "M-25", "M-26", "M-27", "M-42")


def _byggde() -> set[str]:
    """Modulnumrene som har et manifest, lest fra katalogen på disk."""
    ut = set()
    for p in sorted((ROT / "platform" / "modules").iterdir()):
        if not (p / "manifest.yaml").exists():
            continue
        ut.add("M-" + str(int(p.name.split("_")[0][1:])))
    return ut


def _refererte() -> dict[str, set[str]]:
    """Modul → hvilke maler som navngir den, og hvordan."""
    ut: dict[str, set[str]] = {}
    for fil in MALER:
        kort = Path(fil).name
        d = yaml.safe_load(Path(fil).read_text(encoding="utf-8"))
        for vid, v in (d.get("verifikatorer") or {}).items():
            m = re.match(r"(M-\d+)", v.get("beskrivelse", ""))
            if m:
                ut.setdefault(m.group(1), set()).add(f"{kort}:{vid}")
        for h in (d.get("handlinger") or []):
            if h.get("modul"):
                ut.setdefault(h["modul"], set()).add(f"{kort}:{h['id']}")
    return ut


def test_malene_finnes_og_parser():
    """Porten skal måle noe. Null maler ville vært grønt på et tomt
    katalog like godt som på et riktig."""
    assert len(MALER) >= 3, MALER
    refs = _refererte()
    assert len(refs) >= 12, sorted(refs)


def test_gapet_er_kjent_og_ingen_ny_mangel_har_sneket_seg_inn():
    """Ingen NY verifikator uten en modul bak.

    En mal som navngir en tolvte modul som ikke finnes, er et løfte til
    en kunde som ingen har bestemt at vi skal holde. Porten tvinger den
    beslutningen fram i det referansen legges inn — ikke når noen
    oppdager at en `auto`-handling aldri har fyrt.
    """
    mangler = set(_refererte()) - _byggde()
    forventet = set(VENTENDE)
    nye = mangler - forventet
    assert not nye, (
        f"nye moduler navngis i en bransjemal uten å finnes: {sorted(nye)}."
        " Legg dem i VENTENDE med en klyngetildeling, eller bygg dem.")


def test_klynge4_har_manifest_og_star_ikke_lenger_som_ventende():
    """…og en modul som BLE bygget skal ut av listen.

    Uten denne halvdelen ville `VENTENDE` blitt stående som en gammel
    sannhet, og porten ville vært grønn på en liste ingen holdt ved like.
    """
    byggde = _byggde()
    for m in KLYNGE4:
        assert m in byggde, f"{m} har ikke manifest ennå"
        assert m not in VENTENDE, \
            f"{m} er bygget, men står fortsatt i VENTENDE"
    for m in VENTENDE:
        assert m not in byggde, \
            f"{m} står i VENTENDE, men har manifest — ta den ut"


def test_hver_ventende_modul_er_faktisk_referert():
    """En VENTENDE-oppføring som ingen mal nevner er en gammel notis, og
    den gjør tallet i fundamentdokumentet feil."""
    refs = _refererte()
    for m in VENTENDE:
        assert m in refs, f"{m} står i VENTENDE, men ingen mal nevner den"


def test_motoren_feiler_lukket_uten_attestasjon():
    """FUNDAMENTDOKUMENTETS PÅSTAND, KJØRT.

    En handling med et vilkår som ingen kan attestere skal BLOKKERES —
    ikke slippes gjennom. Det er grunnen til at fem manglende
    verifikatorer ikke er en åpen sikkerhetsfeil, og en påstand om det i
    et dokument er ikke verdt noe uten denne linjen.

    MUTASJONEN SOM DREPER DENNE: la §9 i `engine.py` hoppe over vilkår
    det ikke finnes attestasjon for.
    """
    policy = {
        "schema_version": "0.2",
        "meta": {"policy_id": "port-lukket", "versjon": "0.1.0"},
        "tidssone": "Europe/Oslo",
        "roller": [{"id": "agent", "beskrivelse": "agent"}],
        "dataklasser": ["finansiell"],
        "verifikatorer": {
            "v_finnes_ikke": {"beskrivelse": "M-99 modul som ikke finnes",
                              "betrodd_for": ["et_vilkaar"]}},
        "handlinger": [{
            "id": "test.handling", "modul": "M-99", "modus": "auto",
            "ved_brudd": "unntakskø", "tillatt_for": ["agent"],
            "vilkaar": [{"navn": "et_vilkaar",
                         "verifikator": "v_finnes_ikke"}]}],
    }
    ctx = EvaluationContext(tenant_id="t1", aktor_rolle="agent",
                            autentisert=True, kilde="api_token")
    naa = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    # NØKKELEN I HENDELSEN HETER `handling`, ikke `handling_id` — og en
    # hendelse med feil nøkkel treffer `ukjent_handling` og blir UNNTAK.
    # Det er i seg selv deny-by-default, men det er ikke det denne
    # porten skal måle, så den bruker riktig nøkkel.
    d = evaluate(policy, ctx, {"handling": "test.handling",
                               "ressurs_id": "r1"}, naa=naa)
    assert d.beslutning != TILLAT, \
        "en handling uten attestasjon ble tillatt — motoren feiler ÅPENT"
    koder = {g.kode for g in d.begrunnelse}
    assert "attestasjon_mangler" in koder, (d.beslutning, koder)
    # PRESIST HVILKEN VEI: `ved_brudd: unntakskø` gjør dette til et
    # UNNTAK, ikke en hard STOPP. Forskjellen betyr noe — et unntak
    # havner foran et menneske, en stopp gjør det ikke — og den skal
    # stå målt, ikke antatt.
    assert d.beslutning == UNNTAK, d.beslutning


def test_motoren_stopper_en_ubetrodd_verifikator():
    """…og en attestasjon fra noen som ikke er betrodd for VILKÅRET er
    ikke en halv attestasjon: den er en STOPP.

    Det er skillet som gjør at «M-42 attesterer svindelsjekk» ikke kan
    forfalskes ved å sende en attestasjon fra en annen verifikator.
    """
    policy = {
        "schema_version": "0.2",
        "meta": {"policy_id": "port-ubetrodd", "versjon": "0.1.0"},
        "tidssone": "Europe/Oslo",
        "roller": [{"id": "agent", "beskrivelse": "agent"}],
        "dataklasser": ["finansiell"],
        "verifikatorer": {
            "v_riktig": {"beskrivelse": "M-99 riktig",
                         "betrodd_for": ["et_vilkaar"]},
            "v_annen": {"beskrivelse": "M-98 en helt annen",
                        "betrodd_for": ["noe_annet"]}},
        "handlinger": [{
            "id": "test.handling", "modul": "M-99", "modus": "auto",
            "ved_brudd": "unntakskø", "tillatt_for": ["agent"],
            "vilkaar": [{"navn": "et_vilkaar", "verifikator": "v_riktig"}]}],
    }
    ctx = EvaluationContext(tenant_id="t1", aktor_rolle="agent",
                            autentisert=True, kilde="api_token")
    naa = datetime(2026, 9, 2, 10, 0, tzinfo=timezone.utc)
    d = evaluate(policy, ctx, {
        "handling": "test.handling", "ressurs_id": "r1",
        "attestasjoner": {"et_vilkaar": {
            "verifikator": "v_annen", "ressurs_id": "r1",
            "resultat": True,
            "utloper": "2027-01-01T00:00:00+00:00"}}}, naa=naa)
    # HER ER DET STOPP, ikke unntakskø: §9 bruker `tving_stopp`, og
    # `ved_brudd` får ikke overstyre. En forfalsket attestasjon skal
    # ikke havne i en kø noen kan godkjenne seg forbi.
    assert d.beslutning == STOPP, (d.beslutning,
                                   [g.kode for g in d.begrunnelse])
    assert "verifikator_ikke_betrodd" in {g.kode for g in d.begrunnelse}


def test_klynge4_grensene_er_registrert_for_koden():
    """§0: alle fem grensene står i KRAVGRENSER fra fundament-commiten,
    før en eneste linje kode finnes.

    OG ALLE FEM BÆRER `modulen_signerte_attestasjon`. Det er klyngens
    nye dom: klynge 1–3 holdt igjen på å UTFØRE en handling, denne
    holder igjen på å AUTORISERE en.
    """
    from manifestskjema import KRAVGRENSER
    for nr in ("m14-v1", "m25-v1", "m26-v1", "m27-v1", "m42-v1"):
        g = KRAVGRENSER[nr]
        assert g["maks_brudd"] == 0 and g["min_forsok"] == 1, nr
        assert g["punktbinding"] == {}, nr
        assert "modulen_signerte_attestasjon" in g["invarianter"], nr
        assert "ui_axe_alvorlige_brudd" in g["invarianter"], nr
        # …og ingen grense er tom. En invariantliste på null ville vært
        # grønn på alt (parform-dommen: null brudd med null forsøk).
        assert len(g["invarianter"]) >= 8, (nr, len(g["invarianter"]))
