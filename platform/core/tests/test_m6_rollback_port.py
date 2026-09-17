"""`m6-rollback-v1` — porten står FØR drillen (§0), i KJERNE-form.

M-6 ruller med kjernen. Porten krever at en innhenting avbrutt midt i på
de drillede bytene lot side 1 stå og cursoren urørt, at forgjengerens
bytes fullførte fra samme cursor uten dubletter og satte cursoren, at de
drillede bytene deretter så ingenting nytt, at hver melding har nøyaktig
én evidensrad, og at hver kjøring bevitnet katalogen innhenteren ble
lastet fra. Mutasjonene som feller: en inflight som ikke ble avbrutt,
en cursor som flyttet seg, en dublett, en rullbakk uten nye, en kandidat
med nye, evidens som ikke matcher, feil katalog, kilde som står aktiv.
"""
from __future__ import annotations

from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
D1, D2 = "a" * 64, "b" * 64
DK, FK = "/opt/disponit/releases/x", "/opt/disponit/releases/y"


def _art(**over):
    art = {
        "krav_id": "m6-rollback-v1", "ts": "2026-09-17T18:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": "m06_epost", "miljo": "staging", "vert": "disponit-srv",
                    "tenant": "t-m6drill-0a1b2c", "kilde_id": "k",
                    "drillet_release": "x" * 40, "forgjenger_release": "y" * 40,
                    "drillet_katalog": DK, "forgjenger_katalog": FK,
                    "drillet_digest": D1, "forgjenger_digest": D2,
                    "kandidat_digest": D1, "meldinger_rigget": 10, "per_side": 5,
                    "form": "kjerne"},
        "identiteter": {"runde_id": "r", "inflight_pid": 1, "rullback_pid": 2,
                        "kandidat_pid": 3,
                        "inflight_fil": f"{DK}/platform/core/plan/epost.py",
                        "rullback_fil": f"{FK}/platform/core/plan/epost.py",
                        "kandidat_fil": f"{DK}/platform/core/plan/epost.py"},
        "maalt": {"inflight_utfall": "forbigaende:graph_503", "inflight_hentet": 5,
                  "inflight_delta_uendret": True,
                  "rullbakk_utfall": "ok", "rullbakk_nye": 5, "rullbakk_sett": 10,
                  "rullbakk_delta_satt": True, "total_meldinger": 10, "dubletter": 0,
                  "kandidat_utfall": "ok", "kandidat_nye": 0, "kandidat_sett": 0,
                  "evidens_mottatt": 10, "evidens_ulike_hash": 10,
                  "rullback_bytes_er_forgjengerens": True,
                  "kandidat_bytes_er_drillede": True,
                  "release_digest_bundet": True},
        "etterkontroll": {"kilde_deaktivert": True, "digest_likhet": True,
                          "aktiv_urort": True},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_grensen_er_kjerneformen_og_binder_punktet():
    import manifestskjema as m
    g = m.KRAVGRENSER["m6-rollback-v1"]
    assert g["maks_dubletter"] == 0 and g["min_inflight_hentet"] == 1
    assert g["maks_kandidat_nye"] == 0 and g["krev_evidens_per_melding"] is True
    assert m.ARTEFAKTSKJEMAER["m6-rollback-v1"] == "artefakt-rollback-m6-skjema.json"
    assert set(g["punktbinding"]) == {"rollback_testet"}
    for sti in g["punktbinding"]["rollback_testet"]:
        del_, felt = sti.split(".")
        assert felt in _art()[del_], sti
    for rel in m.M6_RELEASEFILER:
        assert (ROT / rel).exists(), rel


def test_gront_artefakt_bestaar_begge_portene():
    import manifestskjema as m
    art = _art()
    assert m.valider_artefaktformat(art, "m6-rollback-v1") == []
    assert m._sjekk_grenser("m6-rollback-v1", art) == []


def test_hver_akse_feller():
    import manifestskjema as m
    for sti, verdi in (("maalt.inflight_utfall", "ok"),
                       ("maalt.inflight_utfall", "feilet:graph_auth"),
                       ("maalt.kandidat_utfall", "forbigaende:graph_503"),
                       ("maalt.inflight_hentet", 0),
                       ("maalt.inflight_delta_uendret", False),
                       ("maalt.rullbakk_nye", 0),
                       ("maalt.rullbakk_utfall", "feilet"),
                       ("maalt.rullbakk_delta_satt", False),
                       ("maalt.dubletter", 1),
                       ("maalt.total_meldinger", 9),
                       ("maalt.kandidat_nye", 1),
                       ("maalt.evidens_mottatt", 9),
                       ("maalt.evidens_ulike_hash", 9),
                       ("maalt.rullback_bytes_er_forgjengerens", False),
                       ("maalt.kandidat_bytes_er_drillede", False),
                       ("maalt.release_digest_bundet", False),
                       ("oppsett.kandidat_digest", D2),
                       ("oppsett.forgjenger_katalog", DK),
                       ("etterkontroll.kilde_deaktivert", False),
                       ("etterkontroll.digest_likhet", False),
                       ("etterkontroll.aktiv_urort", False),
                       ("identiteter.rullback_fil", f"{DK}/platform/core/plan/epost.py"),
                       ("identiteter.kandidat_pid", 1)):
        assert m._sjekk_grenser("m6-rollback-v1", _art(**{sti: verdi})), (sti, verdi)
    uten = _art(); del uten["maalt"]["dubletter"]
    assert m.valider_artefaktformat(uten, "m6-rollback-v1") != []


def test_m6_digesten_er_regnbar_og_folger_bytene(tmp_path):
    import shutil
    import manifestskjema as m
    d1 = m.m6_digest(ROT)
    assert len(d1) == 64 and d1 == m.m6_digest(ROT)
    for rel in m.M6_RELEASEFILER:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        if (ROT / rel).is_dir():
            shutil.copytree(ROT / rel, tmp_path / rel,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(ROT / rel, tmp_path / rel)
    assert m.m6_digest(tmp_path) == d1
    (tmp_path / "platform/core/plan/epost.py").write_bytes(b"# mutert\n")
    assert m.m6_digest(tmp_path) != d1
