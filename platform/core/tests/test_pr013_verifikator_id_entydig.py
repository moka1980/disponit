"""Codex P2 på PR #61: verifikator-id-en må gi en ENTYDIG diffsti.

`verifikatorer` (og `verifikator_prioritet`) er de eneste frie nøklene i en
ellers lukket policy — alt annet er faste feltnavn eller id-er med mønster.
Nøkkelen havner utolket i `policydiff._flat`-stien `verifikatorer.<id>.<felt>`,
der punktum og klammer ER skilletegnene. Med id-ene `foo` og `foo.beskrivelse`
peker `verifikatorer.foo.beskrivelse` på to forskjellige ting samtidig, og
godkjenneren kan tilskrive en tillitsendring feil verifikator.

Kravet håndheves i INNFØRINGSKONTRAKTEN (`valider_ny_policy`), ikke i
lastekontrakten (`valider_policy`). Codex P1 på PR #63: skjemaet er også det
`hent_aktiv` revaliderer den LAGREDE policyen mot ved hver forespørsel, så en
innstramming der ville gjort allerede aktive policyer med en slik id korrupte i
det utrullingen landet — tenanten mister alle policystyrte beslutninger uten å
rekke å rette id-en. Testene under fester begge halvdelene: nye policyer avvises,
gamle leses fortsatt.

Rene validatortester (ingen DB).
"""
import copy
from pathlib import Path

import yaml

from policy_validator.schema import valider_ny_policy, valider_policy

_BASE = yaml.safe_load(
    (Path(__file__).resolve().parents[3] / "policies"
     / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))


def _med_verifikator(vid):
    """Basen med én ekstra verifikator under id-en `vid`. Ingen handling peker
    på den, så eneste mulige feil er selve nøkkelen."""
    p = copy.deepcopy(_BASE)
    p["verifikatorer"][vid] = {"betrodd_for": ["fire_oyne"],
                               "beskrivelse": "test"}
    return p


def test_basen_er_gyldig():
    # Fanger at innstrammingen ikke avviser malene vi selv leverer.
    assert valider_policy(copy.deepcopy(_BASE)) == []
    assert valider_ny_policy(copy.deepcopy(_BASE)) == []


def test_vanlig_verifikator_id_passerer():
    assert valider_ny_policy(_med_verifikator("v_ny")) == []


def test_verifikator_id_med_punktum_avvises():
    # Selve funnet: `foo` finnes allerede (v_register ...), og en id som
    # inneholder punktum kan skjøte seg på et feltnavn.
    feil = valider_ny_policy(_med_verifikator("foo.beskrivelse"))
    assert feil, "id med punktum må avvises"
    assert any("verifikatorer" in f for f in feil), feil


def test_verifikator_id_med_klammer_avvises():
    # `[` er det andre skilletegnet: med id-ene `foo` og `foo[0]` treffer
    # `verifikatorer.foo[0].beskrivelse` begge på en ekte nøkkelgrense.
    assert valider_ny_policy(_med_verifikator("foo[0]")), "id med `[` må avvises"


def test_tom_verifikator_id_avvises():
    assert valider_ny_policy(_med_verifikator("")), "tom id må avvises"


def test_verifikator_prioritet_har_samme_krav():
    p = copy.deepcopy(_BASE)
    p["verifikator_prioritet"] = {"v_register.beskrivelse": 1}
    assert valider_ny_policy(p), "prioritetsnøkkel med punktum må avvises"


def test_verifikator_prioritet_med_vanlig_id_passerer():
    p = copy.deepcopy(_BASE)
    p["verifikator_prioritet"] = {next(iter(p["verifikatorer"])): 1}
    assert valider_ny_policy(p) == []


def test_ingen_diffsti_er_flertydig_naar_id_ene_er_lovlige():
    """Invarianten innføringskontrakten skal gi: ingen verifikator-id kan være
    et prefiks av en annen fram til en nøkkelgrense i den flate stien."""
    from policy_validator.policydiff import _flat

    p = _med_verifikator("v_ny")
    stier = list(_flat(p))
    assert len(stier) == len(set(stier)), "flat sti må være unik per blad"
    ider = list(p["verifikatorer"])
    for a in ider:
        for b in ider:
            if a is b:
                continue
            assert not b.startswith(f"{a}.") and not b.startswith(f"{a}["), \
                f"'{b}' skjøter seg på '{a}' — stien blir flertydig"


# --- Codex P1 på #63: innstrammingen er FRAMOVERRETTET ---------------------
# En allerede aktiv policy med en slik id ble skrevet den gangen den var
# lovlig. `hent_aktiv` revaliderer den ved HVER forespørsel; ville den samme
# innstrammingen slått til der, hadde tenanten mistet alle policystyrte
# beslutninger i det utrullingen landet — uten mulighet til å aktivere en rettet
# versjon først, siden aktiveringsveien selv går gjennom `hent_aktiv`.

def test_lagret_policy_med_punktum_i_id_lastes_fortsatt():
    assert valider_policy(_med_verifikator("foo.beskrivelse")) == [], \
        "lastekontrakten må ikke gjøre en aktiv policy korrupt i ettertid"


def test_lagret_policy_med_klammer_i_id_lastes_fortsatt():
    assert valider_policy(_med_verifikator("foo[0]")) == []


def test_lagret_prioritetsnokkel_med_punktum_lastes_fortsatt():
    p = copy.deepcopy(_BASE)
    p["verifikator_prioritet"] = {"v_register.beskrivelse": 1}
    assert valider_policy(p) == []


def test_innforingskontrakten_returnerer_lastefeil_alene():
    """Er strukturen brøt, skal svaret være strukturfeilene — ikke id-kravet på
    toppen av dem. Ellers drukner rotårsaken i støy."""
    p = _med_verifikator("foo.beskrivelse")
    del p["tidssone"]
    feil = valider_ny_policy(p)
    assert feil == valider_policy(p), feil
    assert not any("flertydig" in f for f in feil), feil
