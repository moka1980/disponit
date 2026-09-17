"""`m<X>-rollback-v1` for sveipmodulene — porten står FØR drillen (§0).

Formen er `rollback-m56-v1`s på de fem tallene grensene bærer, med
digestene regnet av treet (`sveipmodul_digest`). Mutasjonene som feller
denne: la validatoren godta et claim etter dreneringen, et falskt
verdikt, en rullbakk som ikke claimet, en kandidat med andre bytes, en
forgjenger som er den drillede, eller en override som står igjen.
"""
from __future__ import annotations

from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
D1, D2 = "a" * 64, "b" * 64


def _art(m, **over):
    art = {
        "krav_id": "m14-rollback-v1", "ts": "2026-09-17T12:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": "m14_fakturakontroll", "miljo": "staging",
                    "vert": "disponit-srv", "tenant": "t-m14drill-x",
                    "drillet_release": "m14-r3-x", "rullback_release": "m14-drill-rb-1",
                    "kandidat_release": "m14-drill-k-1", "forgjenger_release": "m14-r2-y",
                    "drillet_digest": D1, "kandidat_digest": D1,
                    "rullback_digest": D2, "forgjenger_digest": D2,
                    "forgjenger_katalog": "/opt/disponit/releases/y",
                    "drillet_katalog": "/opt/disponit/releases/x",
                    "module_epoch": 1, "kontraktversjon": 1, "kontrakt_hash": "c" * 64},
        "identiteter": {"inflight_oppdrag_id": "11", "rullback_oppdrag_id": "12",
                        "kandidat_oppdrag_id": "13", "probe_oppdrag_id": "10"},
        "maalt": {"inflight_oppdrag": 1, "inflight_utfall": "utfort",
                  "inflight_har_signert_kvittering": True, "falske_verdikter": 0,
                  "claims_etter_drenering": 0, "ventetid_ubehandlet_s": 25.0,
                  "rullback_claimet_oppdrag": 1, "rullback_har_signert_kvittering": True,
                  "rullback_promoterte": 0, "rullback_overtakelse_s": 12.0,
                  "kandidat_claimet_oppdrag": 1, "kandidat_har_signert_kvittering": True,
                  "overtakelse_s": 9.0, "release_digest_bundet": True},
        "etterkontroll": {"drillet_livslop": "draining", "rullback_livslop": "draining",
                          "kandidat_livslop": "claiming", "modulstatus": "aktiv",
                          "digest_likhet": True, "rullback_bytes_er_forgjengerens": True,
                          "unit_override_fjernet": True},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_grensene_finnes_for_alle_fem_og_deler_form():
    import manifestskjema as m
    for krav in m.ROLLBACK_MODUL_GRENSER:
        g = m.KRAVGRENSER[krav]
        assert g["maks_claims_etter_drenering"] == 0 and g["min_inflight"] == 1
        assert g["krev_release_digest_bundet"] is True
        assert m.ARTEFAKTSKJEMAER[krav] == "artefakt-rollback-modul-skjema.json"
        assert set(g["punktbinding"]) == {"rollback_testet"}


def test_gront_artefakt_bestaar_begge_portene():
    import manifestskjema as m
    art = _art(m)
    assert m.valider_artefaktformat(art, "m14-rollback-v1") == []
    assert m._sjekk_grenser("m14-rollback-v1", art) == []


def test_hver_akse_feller():
    import manifestskjema as m
    for sti, verdi in (("maalt.claims_etter_drenering", 1),
                       ("maalt.falske_verdikter", 1),
                       ("maalt.inflight_oppdrag", 0),
                       ("maalt.rullback_claimet_oppdrag", 0),
                       ("maalt.kandidat_claimet_oppdrag", 0),
                       ("maalt.inflight_har_signert_kvittering", False),
                       ("maalt.rullback_har_signert_kvittering", False),
                       ("maalt.inflight_utfall", "avbrutt"),
                       ("maalt.ventetid_ubehandlet_s", 5.0),
                       ("maalt.release_digest_bundet", False),
                       ("oppsett.kandidat_digest", D2),
                       ("oppsett.rullback_digest", D1),
                       ("oppsett.forgjenger_release", "m14-r3-x"),
                       ("etterkontroll.rullback_bytes_er_forgjengerens", False),
                       ("etterkontroll.drillet_livslop", "claiming"),
                       ("etterkontroll.kandidat_livslop", "draining"),
                       ("etterkontroll.modulstatus", "inaktiv"),
                       ("etterkontroll.unit_override_fjernet", False),
                       ("etterkontroll.digest_likhet", False),
                       ("identiteter.rullback_oppdrag_id", "11")):
        assert m._sjekk_grenser("m14-rollback-v1", _art(m, **{sti: verdi})), \
            (sti, verdi)
    uten = _art(m); del uten["maalt"]["release_digest_bundet"]
    assert m.valider_artefaktformat(uten, "m14-rollback-v1") != []


def test_digesten_er_regnbar_og_folger_bytene(tmp_path):
    import manifestskjema as m
    d1 = m.sveipmodul_digest(ROT, "m14_fakturakontroll")
    assert d1 == m.sveipmodul_digest(ROT, "m14_fakturakontroll")
    # Et annet tre med samme filer → samme digest; én byte endret → en annen.
    import shutil
    for rel in m.SVEIPMODUL_RELEASEFILER["m14_fakturakontroll"]:
        kilde = ROT / rel
        maal = tmp_path / rel
        if kilde.is_dir():
            shutil.copytree(kilde, maal, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            maal.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(kilde, maal)
    assert m.sveipmodul_digest(tmp_path, "m14_fakturakontroll") == d1
    (tmp_path / "platform/drift/m14_arbeider.py").write_bytes(
        (tmp_path / "platform/drift/m14_arbeider.py").read_bytes() + b"\n# x\n")
    assert m.sveipmodul_digest(tmp_path, "m14_fakturakontroll") != d1
    for modul in m.SVEIPMODUL_RELEASEFILER:
        assert len(m.sveipmodul_digest(ROT, modul)) == 64
