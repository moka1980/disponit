"""Manifestformatet (v2 Del 7 + v3-delta pkt. 7).

Skjemaet finnes for å hindre at et sjekklistepunkt blir «ja» uten bevis.
Testene her er derfor negative først: hver regel i skjemaet får en
konstruert overtredelse som MÅ avvises. En skjematest som bare validerer
den gyldige filen beviser at filen er gyldig, ikke at skjemaet virker.
"""
import copy
from pathlib import Path

import pytest
import yaml

from manifestskjema import (aktiv_uten_bevis, valider_alle, valider_manifest,
                            uavklarte_punkter)

MODULROT = Path(__file__).resolve().parents[1].parent / "modules"


@pytest.fixture(scope="module")
def m01():
    return yaml.safe_load(
        (MODULROT / "m01_policy/manifest.yaml").read_text(encoding="utf-8"))


def test_alle_manifester_i_repoet_er_gyldige():
    feil = {navn: f for navn, f in valider_alle(MODULROT).items() if f}
    assert feil == {}, feil


def test_m01_har_strukturerte_punkter(m01):
    sjekkliste = m01["staging_sjekkliste"]
    assert all(isinstance(p, dict) and "status" in p
               for p in sjekkliste.values()), \
        "et punkt står fortsatt i det gamle flate formatet"
    assert sjekkliste["feilinjisering_til_unntakskø"] == {
        "status": "blokkert", "blokkert_av": "m37"}
    assert sjekkliste["ytelse_bestatt"]["krav_id"] == "perf-m01-v1"


def test_blokkert_uten_blokkert_av_avvises(m01):
    """«Blokkert» uten å si av HVA er bare en penere måte å si nei på."""
    m = copy.deepcopy(m01)
    m["staging_sjekkliste"]["ytelse_bestatt"] = {"status": "blokkert"}
    assert valider_manifest(m), "skjemaet godtok blokkert uten blokkert_av"


def test_blokkert_av_pa_ikke_blokkert_punkt_avvises(m01):
    m = copy.deepcopy(m01)
    m["staging_sjekkliste"]["ytelse_bestatt"] = {"status": "nei",
                                                 "blokkert_av": "m37"}
    assert valider_manifest(m)


def test_ja_med_krav_id_uten_artefakt_avvises(m01):
    """Kjernen i v2 Del 6: manifestfeltet er ALDRI selv beviset.

    Uten denne regelen kan `ytelse_bestatt` settes til `ja` ved å slette
    ordet `nei` — og ytelsesporten er da bestått ved et tastetrykk.
    """
    m = copy.deepcopy(m01)
    m["staging_sjekkliste"]["ytelse_bestatt"] = {"status": "ja",
                                                 "krav_id": "perf-m01-v1"}
    feil = valider_manifest(m)
    assert feil, "et ja med krav_id slapp gjennom uten artefakt"

    m["staging_sjekkliste"]["ytelse_bestatt"]["artefakt"] = \
        "deploy/staging/artefakter/perf-m01-v1-20260802.json"
    assert valider_manifest(m) == []


def test_ukjent_status_og_ukjent_punkt_avvises(m01):
    m = copy.deepcopy(m01)
    m["staging_sjekkliste"]["ytelse_bestatt"] = {"status": "kanskje"}
    assert valider_manifest(m)

    m2 = copy.deepcopy(m01)
    m2["staging_sjekkliste"]["finnes_ikke"] = {"status": "ja"}
    assert valider_manifest(m2), "et oppdiktet sjekklistepunkt ble godtatt"


def test_manglende_punkt_avvises(m01):
    """Alle seks punktene er påkrevd. Å slette et punkt man ikke består
    ville ellers vært den enkleste veien til en grønn sjekkliste."""
    m = copy.deepcopy(m01)
    del m["staging_sjekkliste"]["rollback_testet"]
    assert valider_manifest(m)


def test_uavklarte_punkter_og_aktiv_uten_bevis(m01):
    assert set(uavklarte_punkter(m01)) == {
        "feilinjisering_til_unntakskø", "ytelse_bestatt", "rollback_testet"}
    # m01 er `under_utvikling`, ikke `aktiv` — da er uavklarte punkter greit.
    assert aktiv_uten_bevis(m01) == []
    aktiv = copy.deepcopy(m01)
    aktiv["status"] = "aktiv"
    assert aktiv_uten_bevis(aktiv), \
        "en modul kan settes aktiv med uavklarte sjekklistepunkter"


def test_registeret_leser_fortsatt_manifestet(m01):
    """Formatendringen skal ikke røre registerets kontrakt: registeret
    leser id/status/avhengigheter og bryr seg ikke om sjekklisten."""
    from registry import les_manifester, valider
    moduler = les_manifester(MODULROT)
    assert any(m.id == "m01_policy" and m.status == "under_utvikling"
               for m in moduler)
    assert valider(moduler).feil == []


def test_rategrensen_star_ikke_i_veien_for_ytelsesporten():
    """Rate-grensen må ligge over plattformens eget ytelseskrav.

    Funnet ved å KJØRE lasttesten, ikke ved å lese koden: standardgrensen
    var 600/minutt (= 10/s) mens perf-m01-v1 krever 100/s vedvarende. 5 400
    av 6 000 forespørsler fikk 429, og artefaktet så ut som en ytelsesfeil
    mens det i virkeligheten var to tall som motsa hverandre.

    Testen binder dem sammen. Senkes grensen igjen under kravet, faller
    denne — i stedet for at neste lasttestkjøring gjør det.
    """
    from api.app import STANDARD_RATE_PER_MIN, YTELSESKRAV_PER_SEK
    assert STANDARD_RATE_PER_MIN >= 60 * YTELSESKRAV_PER_SEK, (
        f"rate-grensen ({STANDARD_RATE_PER_MIN}/min) gjør ytelseskravet"
        f" ({YTELSESKRAV_PER_SEK}/s = {60 * YTELSESKRAV_PER_SEK}/min)"
        f" uoppnåelig for én klient")


def test_lasttesten_bruker_samme_ytelseskrav_som_api_et():
    """Én sannhet for tallet 100/s — ikke to som kan gli fra hverandre."""
    import importlib.util
    from pathlib import Path
    from api.app import YTELSESKRAV_PER_SEK
    rot = Path(__file__).resolve().parents[3]
    spek = importlib.util.spec_from_file_location(
        "lasttest", rot / "deploy/staging/lasttest-m01.py")
    modul = importlib.util.module_from_spec(spek)
    spek.loader.exec_module(modul)
    assert modul.RATE == float(YTELSESKRAV_PER_SEK)
    assert modul.MALTE == 6000 and modul.P95_KRAV_MS == 150.0
    assert modul.KRAV_ID == "perf-m01-v1"


def test_skjemacachen_serverer_aldri_et_utdatert_skjema(tmp_path, monkeypatch):
    """Validatoren caches, skjemaet kan likevel endres uten omstart.

    Cachen finnes fordi API-veien revaliderer policyen ved HVER
    forespørsel, og rekompilering av JSON Schema kostet ~20 ms per kall —
    mer enn hele ytelsesbudsjettet. Men en cache som ikke merker at
    skjemafila er endret, ville håndhevet et skjema som ikke lenger finnes
    i repoet. Nøkkelen inkluderer mtime og størrelse; testen skriver et
    nytt skjema og krever at neste validering bruker DET.
    """
    import json
    from policy_validator import schema as skjemamodul

    streng = tmp_path / "skjema.json"
    streng.write_text(json.dumps({"type": "object",
                                  "required": ["finnes_ikke"]}),
                      encoding="utf-8")
    monkeypatch.setattr(skjemamodul, "_SKJEMA_STI", streng)
    skjemamodul._VALIDATOR_CACHE.clear()
    assert skjemamodul.valider_policy({"a": 1}), "det strenge skjemaet ble ikke brukt"

    # Samme sti, nytt innhold: cachen MÅ merke det.
    import os
    import time
    time.sleep(0.01)
    streng.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    os.utime(streng, None)
    feil = skjemamodul.valider_policy({"a": 1})
    assert not any(f.startswith("skjema:") for f in feil), \
        f"cachen serverte det gamle skjemaet: {feil}"
    skjemamodul._VALIDATOR_CACHE.clear()
