"""PR-013 CP5a: strukturert policy-diff + diff_hash (§4).

Godkjenneren attesterer diff_hash — den MÅ være kanonisk (nøkkelrekkefølge-
uavhengig) og endres nøyaktig når innholdet endres.
"""
from policy_validator import policydiff as pd


def test_diff_er_noekkelrekkefolge_uavhengig():
    a = {"x": 1, "y": {"b": 2, "a": 3}}
    b = {"y": {"a": 3, "b": 2}, "x": 1}          # samme innhold, ulik rekkefølge
    assert pd.strukturert_diff(a, b)["endringer"] == []
    assert pd.diff_hash(pd.strukturert_diff(a, b)) == \
        pd.diff_hash(pd.strukturert_diff(b, a))


def test_lagt_til_fjernet_endret():
    base = {"a": 1, "b": 2, "liste": [1, 2]}
    ny = {"a": 1, "b": 9, "c": 3, "liste": [1]}
    e = {x["sti"]: x for x in pd.strukturert_diff(base, ny)["endringer"]}
    assert e["b"]["type"] == "endret" and e["b"]["fra"] == 2 and e["b"]["til"] == 9
    assert e["c"]["type"] == "lagt_til" and e["c"]["til"] == 3
    assert e["liste[1]"]["type"] == "fjernet" and e["liste[1]"]["fra"] == 2


def test_diff_hash_endres_med_innhold_stabil_ellers():
    base = {"belop": "100"}
    d1 = pd.strukturert_diff(base, {"belop": "200"})
    d2 = pd.strukturert_diff(base, {"belop": "200"})
    d3 = pd.strukturert_diff(base, {"belop": "300"})
    assert pd.diff_hash(d1) == pd.diff_hash(d2)
    assert pd.diff_hash(d1) != pd.diff_hash(d3)


def test_forste_policy_fra_deny_all_er_bare_tillegg():
    from policy_validator.semantikk import DENY_ALL_V1
    ny = dict(DENY_ALL_V1)
    ny["handlinger"] = [{"id": "faktura.bokfor", "modus": "auto"}]
    diff = pd.strukturert_diff(DENY_ALL_V1, ny)
    assert all(e["type"] == "lagt_til" for e in diff["endringer"])
    assert diff["endringer"], "diff mot deny-all skal ikke være tom"
