"""`m37-rollback-v1` — porten står FØR drillen (§0), i LEASE-form.

M-37 er ingen sveipmodul: arbeideren claimer med lease og skriver bare
gjennom fencing-WHERE. Porten krever at saken som var claimet da rullingen
traff ble re-claimet av rullbakken med generasjon + 1, behandlet nøyaktig
én gang, at det gamle tokenet traff null rader, at rullbakken kjørte fra
forgjengerens katalog og at kandidaten (de drillede bytene) overtok.
Mutasjonene som feller: en tapt sak (null behandlinger), en dobbel (to),
et gammelt token som traff, en overtakelse som ikke er neste generasjon,
en rullbakk fra feil katalog, en override som står igjen, en restart
drillen ikke gjorde.
"""
from __future__ import annotations

from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
D1, D2 = "a" * 64, "b" * 64


def _art(**over):
    art = {
        "krav_id": "m37-rollback-v1", "ts": "2026-09-17T15:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": "m37_unntak", "miljo": "staging",
                    "vert": "disponit-srv", "unit": "disponit-m37",
                    "tenant": "t-m37drill-0a1b2c",
                    "drillet_release": "x" * 40, "forgjenger_release": "y" * 40,
                    "drillet_katalog": "/opt/disponit/releases/x",
                    "forgjenger_katalog": "/opt/disponit/releases/y",
                    "drillet_digest": D1, "forgjenger_digest": D2,
                    "kandidat_digest": D1, "lease_s": 120.0,
                    "instrument": "SIGSTOP/SIGCONT; ACCESS EXCLUSIVE på policyer",
                    "sakskilde": "planrunde → frekvensgrense_naadd"},
        "identiteter": {"probe_sak_id": "301",
                        "inflight_sak_id": "302", "kandidat_sak_id": "303",
                        "drillet_pid": 1001, "rullback_pid": 1002,
                        "kandidat_pid": 1003,
                        "inflight_claim_id_prefiks": "deadbeef"},
        "maalt": {"probe_utfall": "manuell", "probe_claim_generation": 1,
                  "inflight_status_ved_rulling": "under_behandling",
                  "inflight_claim_generation_ved_rulling": 1,
                  "inflight_lease_rest_s_ved_rulling": 117.2,
                  "arbeider_ventet_paa_policyer": 1,
                  "fencing_treff_for_rulling": 1,
                  "drillet_dod_innen_s": 0.4, "laasvindu_s": 3.1,
                  "overtakelse_claim_generation": 2,
                  "overtakelse_ventetid_s": 121.0,
                  "claim_utlopt_hendelser": 1, "claims_totalt": 2,
                  "fencing_treff_etter_overtakelse": 0,
                  "gammel_claim_traff_rader": 0,
                  "inflight_utfall": "manuell",
                  "behandlinger_av_inflight": 1,
                  "behandlinger_under_gammel_claim": 0,
                  "oppdrag_for_inflight": 0,
                  "rullback_cwd_er_forgjengerens": True,
                  "kandidat_cwd_er_aktiv": True,
                  "kandidat_claim_generation": 1, "kandidat_utfall": "manuell",
                  "release_digest_bundet": True},
        "etterkontroll": {"unit_aktiv": True, "unit_override_fjernet": True,
                          "heartbeat_alder_s": 1.2, "uventede_restarter": 0,
                          "digest_likhet": True,
                          "rullback_bytes_er_forgjengerens": True},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_grensen_finnes_og_binder_rollbackpunktet():
    import manifestskjema as m
    g = m.KRAVGRENSER["m37-rollback-v1"]
    assert g["maks_gammel_claim_traff_rader"] == 0
    assert g["min_overtakelse_generasjon"] == 2
    assert g["behandlinger_av_inflight"] == 1
    assert g["krev_release_digest_bundet"] is True
    assert m.ARTEFAKTSKJEMAER["m37-rollback-v1"] == "artefakt-rollback-m37-skjema.json"
    assert set(g["punktbinding"]) == {"rollback_testet"}
    for sti in g["punktbinding"]["rollback_testet"]:
        del_, felt = sti.split(".")
        assert felt in _art()[del_], sti


def test_gront_artefakt_bestaar_begge_portene():
    import manifestskjema as m
    art = _art()
    assert m.valider_artefaktformat(art, "m37-rollback-v1") == []
    assert m._sjekk_grenser("m37-rollback-v1", art) == []


def test_hver_akse_feller():
    import manifestskjema as m
    for sti, verdi in (("maalt.behandlinger_av_inflight", 0),      # tapt
                       ("maalt.behandlinger_av_inflight", 2),      # dobbelt
                       ("maalt.behandlinger_under_gammel_claim", 1),
                       ("maalt.gammel_claim_traff_rader", 1),
                       ("maalt.fencing_treff_etter_overtakelse", 1),
                       ("maalt.fencing_treff_for_rulling", 0),
                       ("maalt.arbeider_ventet_paa_policyer", 0),
                       ("maalt.overtakelse_claim_generation", 1),
                       ("maalt.overtakelse_claim_generation", 3),
                       ("maalt.claim_utlopt_hendelser", 0),
                       ("maalt.inflight_status_ved_rulling", "ny"),
                       ("maalt.inflight_lease_rest_s_ved_rulling", 0.5),
                       ("maalt.overtakelse_ventetid_s", 700.0),
                       ("maalt.inflight_utfall", "under_behandling"),
                       ("maalt.kandidat_utfall", "ny"),
                       ("maalt.probe_utfall", ""),
                       ("maalt.oppdrag_for_inflight", 2),
                       ("maalt.rullback_cwd_er_forgjengerens", False),
                       ("maalt.kandidat_cwd_er_aktiv", False),
                       ("maalt.release_digest_bundet", False),
                       ("oppsett.kandidat_digest", D2),
                       ("oppsett.forgjenger_katalog", "/opt/disponit/releases/x"),
                       ("oppsett.forgjenger_release", "x" * 40),
                       ("etterkontroll.rullback_bytes_er_forgjengerens", False),
                       ("etterkontroll.unit_aktiv", False),
                       ("etterkontroll.unit_override_fjernet", False),
                       ("etterkontroll.digest_likhet", False),
                       ("etterkontroll.heartbeat_alder_s", 95.0),
                       ("etterkontroll.uventede_restarter", 1),
                       ("identiteter.kandidat_sak_id", "302"),
                       ("identiteter.rullback_pid", 1001)):
        assert m._sjekk_grenser("m37-rollback-v1", _art(**{sti: verdi})), \
            (sti, verdi)
    uten = _art(); del uten["maalt"]["gammel_claim_traff_rader"]
    assert m.valider_artefaktformat(uten, "m37-rollback-v1") != []
    feil_lease = _art(**{"oppsett.lease_s": 5})
    assert m.valider_artefaktformat(feil_lease, "m37-rollback-v1") != []


def test_m37_digesten_er_regnbar_og_folger_bytene(tmp_path):
    import manifestskjema as m
    d1 = m.m37_digest(ROT)
    assert d1 == m.m37_digest(ROT) and len(d1) == 64
    assert d1 != m.sveipmodul_digest(ROT, "m14_fakturakontroll")
    for rel in m.M37_RELEASEFILER:
        assert (ROT / rel).exists(), rel
    import shutil
    for rel in m.M37_RELEASEFILER:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(ROT / rel, tmp_path / rel,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    assert m.m37_digest(tmp_path) == d1
    (tmp_path / "platform/core/m37/arbeider.py").write_bytes(b"# mutert\n")
    assert m.m37_digest(tmp_path) != d1
