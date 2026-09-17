"""`m57-datasett-v1` — porten står FØR kjøringen (§0), i M-02s fordelingsform.

Datasettets bytes bæres fra begge ledd (lokal digest regnet av CI-siden,
staging-digest regnet av verten), og porten re-regner treets digest selv:
et artefakt som bærer en annen fil enn treet er rødt uansett tall.
Tallene re-summeres av per-tekst-tabellen, og hver teksts forventning må
være ankerets. Mutasjonene som feller: feil digest på hvilken som helst
side, et avvik, en for liten bunt, en umatchet søknad, en tabell som
ikke summerer, en forventning som ikke er ankerets, en bunt som ikke
fullførte.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]


def _golden():
    return json.loads((ROT / "deploy/staging/m57-golden-v2.json")
                      .read_text(encoding="utf-8"))


def _sha():
    return hashlib.sha256((ROT / "deploy/staging/m57-golden-v2.json")
                          .read_bytes()).hexdigest()


def _bevisrot():
    import manifestskjema as m
    return m.m57_datasett_bevisrot_sha256()


def _art(**over):
    per = [{"golden_id": g["id"], "antall": 10, "forventet": g["forventet_oppfylt"],
            "dommer": {json.dumps(g["forventet_oppfylt"], sort_keys=True,
                                  ensure_ascii=False): 10},
            "avvik": 0} for g in _golden()]
    art = {
        "krav_id": "m57-datasett-v1", "ts": "2026-09-17T17:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": "m57_ats", "vert": "disponit-srv",
                    "tenant": "t-m57fasit", "oppdrag_id": 146,
                    "datasett_fil": "deploy/staging/m57-golden-v2.json",
                    "bevisrot_sha256": _bevisrot(),
                    "resultat": "utfort",
                    "forste_claim_ts": "2026-09-17T15:40:00+00:00",
                    "status_ts": "2026-09-17T16:40:00+00:00"},
        "maalt": {"bunt_soknader": 10 * len(per), "fasitavvik": 0, "umatchet": 0,
                  "datasett_sha_lokal": _sha(), "datasett_sha_staging": _sha(),
                  "per_golden": per},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_grensen_finnes_og_binder_datasettpunktet():
    import manifestskjema as m
    g = m.KRAVGRENSER["m57-datasett-v1"]
    assert g["datasett_min_soknader"] == 200 and g["datasett_maks_fasitavvik"] == 0
    assert g["krev_datasett_sha_lik"] is True
    assert m.ARTEFAKTSKJEMAER["m57-datasett-v1"] == "artefakt-m57-datasett-skjema.json"
    assert set(g["punktbinding"]) == {"syntetisk_datasett_likt_lokalt"}
    assert (ROT / m.M57_DATASETT_FIL).is_file()
    assert m.M57_DATASETT_FIL == "deploy/staging/m57-golden-v2.json"


def test_gront_artefakt_bestaar_begge_portene():
    import manifestskjema as m
    art = _art()
    assert m.valider_artefaktformat(art, "m57-datasett-v1") == []
    assert m._sjekk_grenser("m57-datasett-v1", art) == []


def test_hver_akse_feller():
    import manifestskjema as m
    for sti, verdi in (("maalt.datasett_sha_lokal", "0" * 64),
                       ("maalt.datasett_sha_staging", "0" * 64),
                       ("maalt.fasitavvik", 1),
                       ("maalt.umatchet", 1),
                       ("maalt.bunt_soknader", 199),
                       ("oppsett.resultat", "feilet")):
        assert m._sjekk_grenser("m57-datasett-v1", _art(**{sti: verdi})), \
            (sti, verdi)
    # per_golden som ikke summerer, som lyver om avviket, som ikke er ankerets
    art = _art(); art["maalt"]["per_golden"][0]["antall"] = 11
    assert m._sjekk_grenser("m57-datasett-v1", art)
    art = _art(); p = art["maalt"]["per_golden"][0]
    p["dommer"] = {json.dumps({"drift": None}, sort_keys=True): 10}   # avvik 10, påstått 0
    assert m._sjekk_grenser("m57-datasett-v1", art)
    art = _art(); art["maalt"]["per_golden"][0]["forventet"] = {"drift": None}
    assert m._sjekk_grenser("m57-datasett-v1", art)
    art = _art(); art["maalt"]["per_golden"] = art["maalt"]["per_golden"][1:]
    assert m._sjekk_grenser("m57-datasett-v1", art)
    # to ulike dommer på én tekst (ikke-determinisme), og for få gjentak
    art = _art(); p = art["maalt"]["per_golden"][0]
    p["dommer"] = {list(p["dommer"])[0]: 9, json.dumps({"x": 1}): 1}; p["avvik"] = 1
    assert m._sjekk_grenser("m57-datasett-v1", art)
    art = _art(); p = art["maalt"]["per_golden"][0]
    p["dommer"] = {list(p["dommer"])[0]: 9}; p["antall"] = 9
    assert m._sjekk_grenser("m57-datasett-v1", art)
    assert m._sjekk_grenser("m57-datasett-v1", _art(**{"oppsett.bevisrot_sha256": "0" * 64}))
    # misformede dommer feller med melding, aldri med unntak
    for dommer in ({"ikke json": 10}, {list(_art()["maalt"]["per_golden"][0]["dommer"])[0]: True},
                   {list(_art()["maalt"]["per_golden"][0]["dommer"])[0]: "10"}, "x"):
        art = _art(); art["maalt"]["per_golden"][0]["dommer"] = dommer
        assert m._sjekk_grenser("m57-datasett-v1", art)
    uten = _art(); del uten["maalt"]["datasett_sha_lokal"]
    assert m.valider_artefaktformat(uten, "m57-datasett-v1") != []


def test_ankeret_er_bare_forventninger_og_tekster():
    """Ankeret bærer ingen dom fra modellen i dag — bare tekst og
    forventning per id, og hver id er unik (matchingen er på teksten)."""
    g = _golden()
    assert len(g) == 24 and len({x["id"] for x in g}) == 24
    # produsenten matcher på de første 80 tegnene (blindingen kan maskere
    # navn lenger ut) — de må være unike, ellers er matchingen tvetydig
    assert len({x["tekst"].strip()[:80] for x in g}) == 24
    for x in g:
        assert set(x["forventet_oppfylt"]) == {"drift", "sky", "norsk"}
