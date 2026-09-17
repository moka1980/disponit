"""Den GENERISKE suiteporten — én form for de 46 modulene som står igjen.

De sju første modulene fikk hver sin produsent, grense og skjema: 103
identiske linjer per modul. Denne porten måler at den ene formen bærer
det de sju bar — og at registreringen ikke kan bli en snarvei:

  * gulvene er reelle (en halv suite er ikke suiten, en hoppet andel er
    ikke en kjørt andel),
  * artefaktet må si hvilken MODUL det måler — ellers kunne én kjøring
    bundet et hvilket som helst punkt,
  * andelen er PINNET i treet, og en kjøring over andre filer felles,
  * produsentflaten er bundet med sin digest.

MUTASJONEN SOM DREPER DENNE: la `_grenser_generisk_suite` godta et
artefakt der `oppsett.modul` er en annen modul enn grensens.
"""
from __future__ import annotations

from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
KRAV = "m19-suite-v1"          # første modul på den generiske lesten


def _art(**over):
    import manifestskjema as m
    g = m.KRAVGRENSER[KRAV]
    art = {
        "krav_id": KRAV, "ts": "2026-09-17T22:00:00+00:00", "bestatt": True,
        "oppsett": {"modul": g["modul"], "commit": "a" * 40,
                    "vert": "disponit-srv",
                    "andel_filer": list(g["andel_pakrevd"]),
                    "bevisrot_sha256": m.generisk_suite_bevisrot_sha256(g["modul"])},
        "maalt": {"tester_totalt": 5500, "tester_feilet": 0,
                  "tester_hoppet": 0, "andel_tester": 34,
                  "andel_feilet": 0, "andel_hoppet": 0,
                  "suite_exitkode": 0, "andel_exitkode": 0},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_registreringen_gir_grense_skjema_og_andel():
    import manifestskjema as m
    g = m.KRAVGRENSER[KRAV]
    assert g["modul"] == "m19_adresse" and g["maks_feilet"] == 0
    assert g["maks_andel_hoppet"] == 0
    assert m.ARTEFAKTSKJEMAER[KRAV] == "artefakt-suite-skjema.json"
    assert set(g["punktbinding"]) == {"tester_gronne_pa_staging"}
    assert m.SUITE_ANDEL["m19_adresse"] == g["andel_pakrevd"]
    for rel in g["andel_pakrevd"]:
        assert (ROT / rel).is_file(), rel
    # ...og bindingen peker på målinger artefaktet FAKTISK bærer.
    for sti in g["punktbinding"]["tester_gronne_pa_staging"]:
        del_, felt = sti.split(".")
        assert felt in _art()[del_], sti


def test_gront_artefakt_bestaar_begge_portene():
    import manifestskjema as m
    art = _art()
    assert m.valider_artefaktformat(art, KRAV) == []
    assert m._sjekk_grenser(KRAV, art) == []


def test_hver_akse_feller():
    import manifestskjema as m
    for sti, verdi in (("maalt.tester_feilet", 1),
                       ("maalt.andel_feilet", 1),
                       ("maalt.andel_hoppet", 1),
                       ("maalt.suite_exitkode", 1),
                       ("maalt.andel_exitkode", 2),
                       ("maalt.tester_totalt", 2999),
                       ("maalt.andel_tester", 29),
                       ("oppsett.modul", "m14_fakturakontroll"),
                       ("oppsett.bevisrot_sha256", "0" * 64),
                       ("oppsett.andel_filer",
                        ["platform/core/tests/test_m14_controller.py"])):
        assert m._sjekk_grenser(KRAV, _art(**{sti: verdi})), (sti, verdi)
    # En hoppet suite er ikke en kjørt suite: totalen minus hoppede er tallet.
    art = _art(**{"maalt.tester_hoppet": 2600})
    assert m._sjekk_grenser(KRAV, art)
    # Andelen kan ikke overstige helheten.
    art = _art(**{"maalt.andel_tester": 6000})
    assert m._sjekk_grenser(KRAV, art)
    uten = _art(); del uten["maalt"]["andel_exitkode"]
    assert m.valider_artefaktformat(uten, KRAV) != []
    # ANDELEN ER MED I DIGESTEN: endres testfila etter kjøringen, gjelder
    # artefaktets påstand andre bytes (CodeRabbit).
    andre = m.generisk_suite_bevisrot_sha256("m19_adresse")
    assert andre != m.generisk_suite_bevisrot_sha256("m14_fakturakontroll")
    for feiltype in (5, {"a": 1}, "en streng"):
        assert m._sjekk_grenser(KRAV, _art(**{"oppsett.andel_filer": feiltype})), \
            feiltype


def test_produsenten_nekter_en_modul_uten_pinnet_andel():
    """Produsenten skal ikke kunne utlede andelen selv: en filliste
    funnet ved kjøring krymper i stillhet den dagen en fil døpes om."""
    kilde = (ROT / "deploy/staging/suite-artefakt.py").read_text(encoding="utf-8")
    assert "if a.modul not in SUITE_ANDEL:" in kilde
    assert "raise SystemExit" in kilde
    # ...og den teller av junit-XML, ikke av oppsummeringsteksten.
    assert "junit-xml" in kilde and "ET.parse" in kilde
    assert "passed" not in kilde.split("def _kjor", 1)[1].split("def main", 1)[0]


def test_de_sju_forste_star_urort():
    """Å skrive om en BUNDET ports form er å endre dommen etter at den
    falt. De sju modulene som alt er sertifisert beholder sine egne
    grenser, konstanter og skjemaer."""
    import manifestskjema as m
    for krav, modul in (("m14-suite-v1", "m14_fakturakontroll"),
                        ("m26-suite-v1", "m26_prisbok"),
                        ("m44-suite-v1", "m44_kampanje"),
                        ("m6-suite-v1", "m06_epost"),
                        ("m17-suite-v1", "m17_kundeservice"),
                        ("m23-suite-v1", "m23_fordring"),
                        ("m57-suite-v1", "m57_ats")):
        g = m.KRAVGRENSER[krav]
        assert krav not in m.SUITE_GRENSER, f"{krav} ble flyttet til den generiske"
        assert m.ARTEFAKTSKJEMAER[krav] != "artefakt-suite-skjema.json", krav
        assert "min_tester" in g, krav
