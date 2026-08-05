"""Manifestformatet (v2 Del 7 + v3-delta pkt. 7).

Skjemaet finnes for å hindre at et sjekklistepunkt blir «ja» uten bevis.
Testene her er derfor negative først: hver regel i skjemaet får en
konstruert overtredelse som MÅ avvises. En skjematest som bare validerer
den gyldige filen beviser at filen er gyldig, ikke at skjemaet virker.
"""
import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from manifestskjema import (aktiv_uten_bevis, valider_alle, valider_artefakter,
                            valider_manifest, uavklarte_punkter)

MODULROT = Path(__file__).resolve().parents[1].parent / "modules"
REPOROT = Path(__file__).resolve().parents[3]


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
    # `feilinjisering_til_unntakskø` gikk fra `blokkert_av: m37` til `ja`
    # 2026-08-05, da M-37 fantes OG kjøringen var gjort på staging. Testen
    # pinner den nye formen: et `ja` UTEN krav_id og artefakt er nettopp
    # det manifestfeltet-som-eget-bevis skjemaet finnes for å hindre.
    fi = sjekkliste["feilinjisering_til_unntakskø"]
    assert fi["status"] == "ja", fi
    assert fi["krav_id"] == "feilinjisering-m01-v1"
    assert fi["artefakt"] and fi["artefakt_sha256"]
    assert "blokkert_av" not in fi, "punktet er ja og blokkert samtidig"
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

    # Sti alene holder ikke lenger: hashen er like påkrevd, ellers er
    # pekeren ubundet til innhold (Codex' P1 på PR #8).
    m["staging_sjekkliste"]["ytelse_bestatt"]["artefakt"] = \
        "deploy/staging/artefakter/perf-m01-v1-20260802.json"
    assert valider_manifest(m), "sti uten sha256 slapp gjennom"

    m["staging_sjekkliste"]["ytelse_bestatt"]["artefakt_sha256"] = "a" * 64
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
    # Settet er TOMT fra 2026-08-05: `ytelse_bestatt` gikk til `ja`
    # 2026-08-02, og `feilinjisering_til_unntakskø` og `rollback_testet`
    # samme dag i august — hver gang fordi kjøringen faktisk var gjort på
    # staging og artefaktet fantes.
    #
    # Et tomt sett er den STRENGESTE varianten av denne testen, ikke den
    # svakeste: nå faller den hvis ETT punkt går tilbake til nei eller
    # blokkert, og porten under er ikke lenger dekket av at m01 uansett
    # hadde uavklarte punkter. Derfor står de to kontrollene på hver sin
    # kopi: `aktiv_uten_bevis` måles med et innsatt uavklart punkt, slik at
    # den fortsatt kan feile.
    assert set(uavklarte_punkter(m01)) == set()
    assert aktiv_uten_bevis(m01) == []
    aktiv = copy.deepcopy(m01)
    aktiv["status"] = "aktiv"
    # Alle punktene er `ja` nå, så en aktiv m01 er lovlig — og da måler
    # ikke porten noe. Ett punkt settes derfor tilbake til `nei`: det er
    # den tilstanden regelen finnes for.
    assert aktiv_uten_bevis(aktiv) == [], (
        "alle sjekklistepunkter er ja — en aktiv modul skal da godtas")
    aktiv["staging_sjekkliste"]["rollback_testet"] = {
        "status": "nei", "krav_id": "rollback-m01-v1"}
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


# ---------------------------------------------------------------------------
# Evidenskjeden: artefaktet må FINNES, stemme med hashen og bestå grensene
# ---------------------------------------------------------------------------
#
# Codex' P1 på PR #8: skjemaet krevde bare at `artefakt` var en ikke-tom
# streng, så `artefakt: tull.json` passerte like fint som en ekte måling.
# Testene under muterer hvert ledd i kjeden og krever RØDT. En port som bare
# er prøvd med den gyldige filen er ikke prøvd.


@pytest.fixture()
def artefakt_sti(m01):
    p = m01["staging_sjekkliste"]["ytelse_bestatt"]
    return REPOROT / p["artefakt"]


def test_ekte_manifest_har_intakt_evidenskjede(m01):
    """Positiv kontroll. Uten den ville alle mutasjonene under kunne vært
    røde av en helt annen grunn — f.eks. at stien alltid er feil."""
    assert valider_artefakter(m01) == []


def test_artefaktet_ligger_faktisk_i_repoet(artefakt_sti):
    """`.gitignore` har et eksplisitt unntak for nettopp denne filen. Blir
    unntaket fjernet, forsvinner beviset og denne testen sier fra."""
    assert artefakt_sti.is_file(), f"artefaktet mangler: {artefakt_sti}"


def test_mutert_sti_gir_roedt(m01):
    m = copy.deepcopy(m01)
    m["staging_sjekkliste"]["ytelse_bestatt"]["artefakt"] = \
        "deploy/staging/artefakter/finnes-ikke.json"
    feil = valider_artefakter(m)
    assert feil and "kan ikke åpnes" in feil[0], feil


def test_sti_utenfor_repoet_avvises(m01):
    """Ellers kunne manifestet pekt på /tmp/noe-jeg-nettopp-skrev.json."""
    m = copy.deepcopy(m01)
    m["staging_sjekkliste"]["ytelse_bestatt"]["artefakt"] = "../../../tmp/x.json"
    feil = valider_artefakter(m)
    assert feil and "utenfor repoet" in feil[0], feil


def test_mutert_hash_gir_roedt(m01):
    m = copy.deepcopy(m01)
    m["staging_sjekkliste"]["ytelse_bestatt"]["artefakt_sha256"] = "0" * 64
    feil = valider_artefakter(m)
    assert feil and "sha256 stemmer ikke" in feil[0], feil


def test_ja_med_krav_id_uten_sha256_avvises_av_skjemaet(m01):
    """Skjemanivået: hash er like påkrevd som sti når punktet er `ja`."""
    m = copy.deepcopy(m01)
    del m["staging_sjekkliste"]["ytelse_bestatt"]["artefakt_sha256"]
    assert valider_manifest(m), "ja med krav_id slapp gjennom uten sha256"


def _muter_artefakt(artefakt_sti, endre) -> dict:
    """Skriver et mutert artefakt og returnerer et punkt som peker på det,
    med KORREKT hash — slik at det er innholdskontrollen som må ta det,
    ikke hashen. Ellers ville alle mutasjonene under blitt fanget av
    sha256-sjekken og innholdsvalideringen aldri vært prøvd."""
    data = json.loads(artefakt_sti.read_text(encoding="utf-8"))
    endre(data)
    raa = json.dumps(data, ensure_ascii=False).encode("utf-8")
    mappe = REPOROT / "deploy/staging/artefakter"
    fil = mappe / f"mutant-{abs(hash(raa)) % 10**12}.json"
    fil.write_bytes(raa)
    return {"fil": fil,
            "punkt": {"status": "ja", "krav_id": "perf-m01-v1",
                      "artefakt": str(fil.relative_to(REPOROT)).replace("\\", "/"),
                      "artefakt_sha256": hashlib.sha256(raa).hexdigest()}}


@pytest.mark.parametrize("navn,endring,forventet", [
    ("bestatt", lambda d: d.update(bestatt=False), "bestatt: true"),
    ("feil", lambda d: d["maalt"].update(feil=17, feiltyper=["500"]),
     "feil=17"),
    ("http_fordeling", lambda d: d["maalt"].update(feiltyper=["404"]),
     "feiltyper"),
    ("p95", lambda d: d["maalt"]["svartid_ms"].update(p95=151.0),
     "p95=151.0"),
    ("antall", lambda d: d["maalt"].update(antall=600), "antall=600"),
    ("rate_begrenset", lambda d: d["maalt"].update(rate_begrenset=5),
     "rate_begrenset=5"),
    ("en_til_en", lambda d: d["etterkontroll"].update(en_til_en=False),
     "en_til_en"),
    ("revisjonsrader", lambda d: d["etterkontroll"].update(revisjonsrader=5999),
     "revisjonsrader=5999"),
    ("routing", lambda d: d["etterkontroll"].update(routing_stemmer=False),
     "routing_stemmer"),
    ("feil_krav_id", lambda d: d.update(krav_id="perf-noe-annet"),
     "manifestet påstår"),
])
def test_mutert_artefaktinnhold_gir_roedt(m01, artefakt_sti, navn, endring,
                                          forventet):
    """Selve poenget med innholdsvalideringen.

    `bestatt: true` inne i artefaktet er produsentens EGEN påstand. Uten
    disse kontrollene ville en kjøring som skrev `bestatt: true` over 6 000
    feilsvar passert porten — nøyaktig 404-kjøringen fra denne PR-en, bare
    med ett felt endret.
    """
    m = copy.deepcopy(m01)
    laget = _muter_artefakt(artefakt_sti, endring)
    try:
        m["staging_sjekkliste"]["ytelse_bestatt"] = laget["punkt"]
        feil = valider_artefakter(m)
        assert feil, f"mutasjonen {navn!r} slapp gjennom porten"
        assert any(forventet in f for f in feil), (navn, feil)
    finally:
        laget["fil"].unlink(missing_ok=True)


def test_ukjent_krav_id_har_ingen_grenser_og_avvises(m01):
    """Et krav_id uten rad i KRAVGRENSER kan ikke håndheves — og da skal
    det ikke kunne stå som bevist heller."""
    m = copy.deepcopy(m01)
    m["staging_sjekkliste"]["ytelse_bestatt"]["krav_id"] = "perf-oppdiktet"
    feil = valider_artefakter(m)
    assert feil and "ukjent krav_id" in " ".join(feil), feil


# ---------------------------------------------------------------------------
# Lastprofil, interne invarianter og fail-closed talltyper
# ---------------------------------------------------------------------------
#
# Codex' P1 nr. 3 på PR #8. Den forrige validatoren leste SAMMENDRAGSBOOLENE
# (`en_til_en`, `routing_stemmer`) og rørte aldri lastprofilen. To muterte
# artefakter passerte derfor porten:
#   * rate 1/s og samtidighet 1 — altså 6 000 forespørsler sendt på 100
#     minutter, som ikke er kravet i det hele tatt, og
#   * `normal = 0` med `forventede_normalsaker = 9999`, der flagget sa true
#     mens tallene motsa hverandre.
# Begge står som egne tester nedenfor, med NAVN etter det de en gang slapp
# gjennom, slik at ingen fjerner dem i vanvare.


@pytest.mark.parametrize("navn,endring,forventet", [
    # --- lastprofil ---
    ("rate_i_oppsettet", lambda d: d["oppsett"].update(rate_per_sek=1.0),
     "oppsett.rate_per_sek"),
    ("oppnadd_rate", lambda d: d["maalt"].update(oppnadd_rate=1.0),
     "maalt.oppnadd_rate"),
    ("samtidighet", lambda d: d["oppsett"].update(samtidige=1),
     "oppsett.samtidige"),
    ("varighet_uten_dekning", lambda d: d["maalt"].update(varighet_sek=3600.0),
     "passer ikke med"),
    # --- interne invarianter ---
    ("beslutningssum", lambda d: d["maalt"]["beslutninger"].update(UNNTAK=0),
     "summen av beslutningene"),
    ("auditerte_svar_avviker",
     lambda d: d["etterkontroll"].update(auditerte_svar=12000),
     "alle tre må være like"),
    ("routing_normal_null",
     lambda d: d["etterkontroll"]["unntaksrader_per_sakstype"].update(normal=0),
     "normal-kørader"),
    ("routing_forventet_lyver",
     lambda d: d["etterkontroll"].update(forventede_normalsaker=9999),
     "forventede normalsaker"),
    # --- fail-closed talltyper ---
    # Telle-felt gaar gjennom `_teller`, som er strengere enn `_tall`:
    # den avviser bool BAADE fordi det ikke er et tall og fordi det ikke er
    # en heltallstelling. Meldingen er derfor den fra domenekontrakten.
    ("feil_som_bool", lambda d: d["maalt"].update(feil=False),
     "er ikke en heltallstelling"),
    ("antall_som_bool", lambda d: d["maalt"].update(antall=True),
     "er ikke en heltallstelling"),
    ("p95_som_nan",
     lambda d: d["maalt"]["svartid_ms"].update(p95=float("nan")),
     "er ikke et endelig tall"),
    ("rate_som_nan", lambda d: d["maalt"].update(oppnadd_rate=float("nan")),
     "er ikke et endelig tall"),
    ("p95_som_streng", lambda d: d["maalt"]["svartid_ms"].update(p95="0"),
     "er ikke et tall"),
    # --- manglende blokker ---
    ("oppsett_borte", lambda d: d.pop("oppsett"), "mangler `oppsett`"),
    ("beslutninger_borte", lambda d: d["maalt"].pop("beslutninger"),
     "mangler `beslutninger`"),
])
def test_lastprofil_og_invarianter_gir_roedt(m01, artefakt_sti, navn, endring,
                                             forventet):
    m = copy.deepcopy(m01)
    laget = _muter_artefakt(artefakt_sti, endring)
    try:
        m["staging_sjekkliste"]["ytelse_bestatt"] = laget["punkt"]
        feil = valider_artefakter(m)
        assert feil, f"mutasjonen {navn!r} slapp gjennom porten"
        assert any(forventet in f for f in feil), (navn, feil)
    finally:
        laget["fil"].unlink(missing_ok=True)


def test_codex_mutasjon_rate_1_og_samtidighet_1(m01, artefakt_sti):
    """Nøyaktig mutasjonen Codex bekreftet at passerte forrige runde.

    6 000 forespørsler er ikke kravet. 6 000 forespørsler PÅ ETT MINUTT
    med 20 samtidige er kravet. Uten lastprofilen kunne den samme mengden
    vært sendt over hundre minutter og fortsatt sett ut som en bestått
    ytelsesport.
    """
    def endre(d):
        d["oppsett"].update(rate_per_sek=1.0, samtidige=1)
        d["maalt"].update(oppnadd_rate=1.0, varighet_sek=6000.0)

    m = copy.deepcopy(m01)
    laget = _muter_artefakt(artefakt_sti, endre)
    try:
        m["staging_sjekkliste"]["ytelse_bestatt"] = laget["punkt"]
        feil = valider_artefakter(m)
        assert any("rate_per_sek" in f for f in feil), feil
        assert any("samtidige" in f for f in feil), feil
        assert any("oppnadd_rate" in f for f in feil), feil
    finally:
        laget["fil"].unlink(missing_ok=True)


def test_codex_mutasjon_null_normalsaker_med_forventet_9999(m01, artefakt_sti):
    """Den andre mutasjonen Codex bekreftet: flagget sa true, tallene løy.

    `routing_stemmer` er produsentens egen påstand — nøyaktig som
    `bestatt`. Porten må regne ut invarianten, ikke lese konklusjonen.
    """
    def endre(d):
        d["etterkontroll"]["unntaksrader_per_sakstype"]["normal"] = 0
        d["etterkontroll"]["forventede_normalsaker"] = 9999
        d["etterkontroll"]["routing_stemmer"] = True      # flagget lyver

    m = copy.deepcopy(m01)
    laget = _muter_artefakt(artefakt_sti, endre)
    try:
        m["staging_sjekkliste"]["ytelse_bestatt"] = laget["punkt"]
        feil = valider_artefakter(m)
        assert any("9999" in f for f in feil), feil
        assert any("UNNTAK-beslutninger" in f for f in feil), feil
    finally:
        laget["fil"].unlink(missing_ok=True)


def test_bool_er_ikke_et_tall_i_noen_maaling(m01, artefakt_sti):
    """Python-fella som gjorde det nødvendig: `isinstance(False, int)` er
    True, så en boolsk `feil` ville blitt lest som 0 feil."""
    assert isinstance(False, int), "forutsetningen for testen er borte"
    m = copy.deepcopy(m01)
    laget = _muter_artefakt(artefakt_sti,
                            lambda d: d["maalt"].update(rate_begrenset=False))
    try:
        m["staging_sjekkliste"]["ytelse_bestatt"] = laget["punkt"]
        # `rate_begrenset` er en telling => `_teller` avviser den som bool.
        assert any("heltallstelling" in f for f in valider_artefakter(m))
    finally:
        laget["fil"].unlink(missing_ok=True)


def test_kravgrenser_og_lasttesten_kan_ikke_gli_fra_hverandre():
    """Porten og produsenten må håndheve SAMME tall.

    `lasttest-m01.py` setter `bestatt` selv, og `KRAVGRENSER` avgjør om
    artefaktet godtas. Sklir de to fra hverandre, får vi enten kjøringer
    som rapporterer bestått og avvises av porten, eller — verre — det
    motsatte. Samme grep som
    test_rategrensen_star_ikke_i_veien_for_ytelsesporten: to tall som er
    avhengige av hverandre bindes sammen i en test.
    """
    import importlib.util
    from manifestskjema import KRAVGRENSER
    spek = importlib.util.spec_from_file_location(
        "lasttest2", REPOROT / "deploy/staging/lasttest-m01.py")
    modul = importlib.util.module_from_spec(spek)
    spek.loader.exec_module(modul)

    g = KRAVGRENSER["perf-m01-v1"]
    assert g["min_antall"] == modul.MALTE
    assert g["maks_p95_ms"] == modul.P95_KRAV_MS
    assert g["min_rate_per_sek"] == modul.RATE
    assert g["min_samtidige"] == modul.SAMTIDIGE


# ---------------------------------------------------------------------------
# Domenet tallene representerer — ikke bare at de er endelige
# ---------------------------------------------------------------------------
#
# Codex' P1 nr. 4 på PR #8. Runde 3 håndhevet at hver måling var et endelig
# tall, men ikke hva tallet BETYR. Fire umulige artefakter passerte:
#   * varighet 0 s — og verre: kontrollen `elif varighet > 0` gjorde at en
#     nullvarighet HOPPET OVER sin egen kontroll. Fail-open i vakten selv.
#   * negative feil-/rate_begrenset-tellinger — −5 er «<= 0» og besto taket.
#   * negative beslutnings- og routingtellinger som utlignet hverandre til
#     riktig sum. Aritmetikken stemmer; virkeligheten gjør det ikke.
#   * brøkdeler av forespørsler, beslutninger og revisjonsrader.
#
# Skillet som løser alle fire: `_teller` (heltall >= 0) for det som TELLES,
# `_positiv` (endelig > 0) for det som MÅLES.


@pytest.mark.parametrize("navn,endring,forventet", [
    # --- Codex' fire ---
    ("null_varighet", lambda d: d["maalt"].update(varighet_sek=0),
     "må være > 0"),
    ("negative_feil", lambda d: d["maalt"].update(feil=-5),
     "er negativ"),
    ("negativ_rate_begrenset", lambda d: d["maalt"].update(rate_begrenset=-3),
     "er negativ"),
    ("negative_beslutninger_utligner",
     lambda d: d["maalt"]["beslutninger"].update(TILLAT=6000, STOPP=-1200,
                                                 UNNTAK=1200),
     "beslutninger.STOPP=-1200 er negativ"),
    ("negativ_routing_utligner",
     lambda d: (d["etterkontroll"]["unntaksrader_per_sakstype"]
                .update(normal=-1200)),
     "unntaksrader normal=-1200 er negativ"),
    ("fraksjonelt_antall", lambda d: d["maalt"].update(antall=6000.5),
     "er ikke en heltallstelling"),
    ("fraksjonelle_revisjonsrader",
     lambda d: d["etterkontroll"].update(revisjonsrader=6000.5),
     "er ikke en heltallstelling"),
    ("fraksjonelle_beslutninger",
     lambda d: d["maalt"]["beslutninger"].update(TILLAT=4800.5),
     "er ikke en heltallstelling"),
    # --- de tre Codex ba om i tillegg ---
    ("negativ_samtidighet", lambda d: d["oppsett"].update(samtidige=-20),
     "er negativ"),
    ("negativ_p95", lambda d: d["maalt"]["svartid_ms"].update(p95=-1.0),
     "må være > 0"),
    ("oppsett_antall_avviker", lambda d: d["oppsett"].update(antall=3000),
     "oppsett.antall=3000 != maalt.antall=6000"),
    # --- nabotilfeller samme skille dekker ---
    ("null_rate", lambda d: d["oppsett"].update(rate_per_sek=0),
     "må være > 0"),
    ("negativ_oppnadd_rate", lambda d: d["maalt"].update(oppnadd_rate=-100.0),
     "må være > 0"),
    ("negativ_varighet", lambda d: d["maalt"].update(varighet_sek=-60.0),
     "må være > 0"),
    ("fraksjonell_samtidighet", lambda d: d["oppsett"].update(samtidige=20.5),
     "er ikke en heltallstelling"),
])
def test_domenegrenser_gir_roedt(m01, artefakt_sti, navn, endring, forventet):
    m = copy.deepcopy(m01)
    laget = _muter_artefakt(artefakt_sti, endring)
    try:
        m["staging_sjekkliste"]["ytelse_bestatt"] = laget["punkt"]
        feil = valider_artefakter(m)
        assert feil, f"mutasjonen {navn!r} slapp gjennom porten"
        assert any(forventet in f for f in feil), (navn, feil)
    finally:
        laget["fil"].unlink(missing_ok=True)


def test_null_varighet_hopper_ikke_over_sin_egen_kontroll(m01, artefakt_sti):
    """Regresjonstest på selve fail-open-formen.

    Kontrollen sto som `elif antall is not None and varighet > 0:` — altså
    ble varighetsinvarianten hoppet over nettopp for de verdiene som var
    ugyldige. Samme form som `feilinjisering`-fellene i PR-005a: en vakt
    som lar det ugyldige passere forbi seg selv.
    """
    m = copy.deepcopy(m01)
    laget = _muter_artefakt(artefakt_sti,
                            lambda d: d["maalt"].update(varighet_sek=0))
    try:
        m["staging_sjekkliste"]["ytelse_bestatt"] = laget["punkt"]
        feil = valider_artefakter(m)
        assert any("varighet_sek" in f and "> 0" in f for f in feil), feil
    finally:
        laget["fil"].unlink(missing_ok=True)


def test_tellere_og_maalinger_leses_med_hver_sin_kontrakt():
    """Enhetsnivå: `_teller` og `_positiv` direkte.

    Uten denne ville alle testene over vært avhengige av at et helt
    artefakt bygges riktig — og en feil i hjelperen kunne se ut som en
    feil i domenet.
    """
    from manifestskjema import _positiv, _teller

    assert _teller({"n": 6000}, "n", "n") == (6000, "")
    assert _teller({"n": 0}, "n", "n") == (0, "")          # 0 er en gyldig telling
    for ugyldig in (-1, 6000.5, 6000.0, True, False, "6000", None):
        verdi, feil = _teller({"n": ugyldig}, "n", "n")
        assert verdi is None and feil, f"{ugyldig!r} ble godtatt som telling"

    assert _positiv({"t": 60.03}, "t", "t") == (60.03, "")
    for ugyldig in (0, -1.0, float("nan"), float("inf"), True, "60", None):
        verdi, feil = _positiv({"t": ugyldig}, "t", "t")
        assert verdi is None and feil, f"{ugyldig!r} ble godtatt som måling"


# ---------------------------------------------------------------------------
# Lukket artefaktformat — ukjente nøkler er en FEIL, ikke stillhet
# ---------------------------------------------------------------------------
#
# Codex' P1 nr. 5 på PR #8. `_sjekk_grenser` leste feltene den kjente til og
# var blind for alt annet, så tre artefakter passerte:
#   * `sikkerhet: 500` blant kø-tellingene
#   * `UKJENT: 500` blant beslutningsutfallene
#   * `feiltyper: false` i stedet for en liste (falsy ⇒ «ingen feiltyper»)
#
# To lag, og de fanger ULIKE ting — derfor står begge:
#   * artefakt-skjema.json med additionalProperties:false tar UKJENTE nøkler
#     (`UKJENT`, en oppdiktet sakstype, en ekstra toppnøkkel) og feil TYPER.
#   * summen over alle sakstyper tar en KJENT nøkkel med uventet antall.
#     `sikkerhet` ER en lovlig sakstype, så skjemaet kan ikke avvise den —
#     bare tallet avslører at kjøringen gjorde noe annet enn den rapporterer.


def test_ekte_artefakt_er_gyldig_mot_det_lukkede_skjemaet(artefakt_sti):
    """Positiv kontroll: skjemaet må slippe gjennom en ekte måling.
    Uten den kunne alle mutasjonene under vært røde fordi skjemaet er feil."""
    from manifestskjema import valider_artefaktformat
    data = json.loads(artefakt_sti.read_text(encoding="utf-8"))
    assert valider_artefaktformat(data) == []


@pytest.mark.parametrize("navn,endring,forventet", [
    ("ukjent_beslutningsutfall",
     lambda d: d["maalt"]["beslutninger"].update(UKJENT=500),
     "'UKJENT' was unexpected"),
    ("ukjent_sakstype",
     lambda d: d["etterkontroll"]["unntaksrader_per_sakstype"].update(
         finnesikke=1),
     "'finnesikke' was unexpected"),
    ("ukjent_toppnokkel", lambda d: d.update(bestatt_egentlig=True),
     "'bestatt_egentlig' was unexpected"),
    ("ukjent_maalt_felt", lambda d: d["maalt"].update(p95_egentlig=1.0),
     "'p95_egentlig' was unexpected"),
    ("feiltyper_som_bool", lambda d: d["maalt"].update(feiltyper=False),
     "is not of type 'array'"),
    ("feiltyper_som_streng", lambda d: d["maalt"].update(feiltyper="404"),
     "is not of type 'array'"),
    ("bestatt_som_streng", lambda d: d.update(bestatt="true"),
     "is not of type 'boolean'"),
    ("manglende_beslutninger", lambda d: d["maalt"].pop("beslutninger"),
     "'beslutninger' is a required property"),
])
def test_lukket_format_gir_roedt(m01, artefakt_sti, navn, endring, forventet):
    m = copy.deepcopy(m01)
    laget = _muter_artefakt(artefakt_sti, endring)
    try:
        m["staging_sjekkliste"]["ytelse_bestatt"] = laget["punkt"]
        feil = valider_artefakter(m)
        assert feil, f"mutasjonen {navn!r} slapp gjennom porten"
        assert any(forventet in f for f in feil), (navn, feil)
    finally:
        laget["fil"].unlink(missing_ok=True)


def test_codex_mutasjon_ekstra_sikkerhetskoerader(m01, artefakt_sti):
    """`sikkerhet: 500` — den ene av de tre som skjemaet IKKE kan ta.

    `sikkerhet` er en lovlig sakstype (feiltabellen definerer den), så
    `additionalProperties: false` slipper den gjennom med rette. Det som
    avslører den er summen: 1 200 normalsaker + 500 sikkerhetssaker er
    1 700 kø-rader for 1 200 UNNTAK-beslutninger. Kjøringen gjorde altså
    noe annet enn den rapporterer.

    Derfor er skjemaet ikke nok alene, og derfor står begge lagene.
    """
    m = copy.deepcopy(m01)
    laget = _muter_artefakt(
        artefakt_sti,
        lambda d: d["etterkontroll"]["unntaksrader_per_sakstype"].update(
            sikkerhet=500))
    try:
        m["staging_sjekkliste"]["ytelse_bestatt"] = laget["punkt"]
        feil = valider_artefakter(m)
        assert any("summen av alle kø-rader (1700)" in f for f in feil), feil
    finally:
        laget["fil"].unlink(missing_ok=True)


def test_skjemaet_alene_ville_ikke_tatt_sikkerhetsradene(artefakt_sti):
    """Ærlig avgrensning av hvert lag.

    Testen dokumenterer at skjemaet BEVISST slipper `sikkerhet` gjennom, og
    at det er sumkontrollen som tar den. Uten dette skillet skrevet ned
    ville neste person kunne fjerne sumkontrollen i den tro at
    `additionalProperties: false` dekker alt.
    """
    from manifestskjema import valider_artefaktformat
    data = json.loads(artefakt_sti.read_text(encoding="utf-8"))
    data["etterkontroll"]["unntaksrader_per_sakstype"]["sikkerhet"] = 500
    assert valider_artefaktformat(data) == [], \
        "skjemaet avviser nå sikkerhet — da er kommentaren over utdatert"


def test_alle_objekter_i_artefaktskjemaet_er_lukket():
    """Porten på skjemaet selv.

    Legger noen til et nytt objekt uten `additionalProperties: false`, er
    det hullet tilbake — i akkurat den delen som er ny og minst gjennomgått.
    """
    from manifestskjema import ARTEFAKTSKJEMA_STI
    skjema = json.loads(ARTEFAKTSKJEMA_STI.read_text(encoding="utf-8"))
    aapne = []

    def gaa(node, sti="<rot>"):
        if isinstance(node, dict):
            if node.get("type") == "object" \
                    and node.get("additionalProperties") is not False:
                aapne.append(sti)
            for nokkel, verdi in node.items():
                gaa(verdi, f"{sti}/{nokkel}")
        elif isinstance(node, list):
            for i, verdi in enumerate(node):
                gaa(verdi, f"{sti}[{i}]")

    gaa(skjema)
    assert aapne == [], f"objekter uten additionalProperties:false: {aapne}"


# ===========================================================================
# Codex runde 6 — rollbackporten regner selv
# ===========================================================================

ROLLBACKARTEFAKT = (REPOROT / "deploy/staging/artefakter"
                    / "rollback-m01-v1-20260805T075341.json")


def _rollback(**overstyr):
    """Det EKTE artefaktet fra staging, med én verdi endret om gangen.

    Testene bygger ikke sin egen JSON: da ville de målt en oppdiktet form,
    og den dagen artefaktformen endrer seg ville de fortsatt bestått. Her
    er utgangspunktet nøyaktig fila manifestet peker på.
    """
    art = json.loads(ROLLBACKARTEFAKT.read_text(encoding="utf-8"))
    for sti, verdi in overstyr.items():
        blokk, felt = sti.split(".", 1)
        art[blokk][felt] = verdi
    return art


def test_ekte_rollbackartefakt_bestaar_porten():
    """Kontrollen til alle mutasjonene under.

    Uten denne ville de bestått også hvis porten avviste ALT — «endringen
    gjør artefaktet rødt» er trivielt sant når utgangspunktet er rødt.
    """
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    art = _rollback()
    assert valider_artefaktformat(art, "rollback-m01-v1") == []
    assert _sjekk_grenser("rollback-m01-v1", art) == []


def test_flere_forespoersler_enn_avvisninger_avvises():
    """`requests_under_rollback` 113 -> 114, resten urørt.

    Andelen står fortsatt på 1.0, men 113/114 er ikke 1.0. Leste porten
    bare det oppgitte tallet, ville den ikke sett forskjell.
    """
    from manifestskjema import _sjekk_grenser
    feil = _sjekk_grenser("rollback-m01-v1", _rollback(
        **{"oppsett.requests_under_rollback": 114}))
    assert any("stemmer ikke med" in f for f in feil), feil


def test_faerre_avvisninger_enn_oppgitt_andel_avvises():
    """`avviste_requests` 113 -> 112, andel fortsatt 1.0."""
    from manifestskjema import _sjekk_grenser
    feil = _sjekk_grenser("rollback-m01-v1", _rollback(
        **{"maalt.avviste_requests": 112}))
    assert any("stemmer ikke med" in f for f in feil), feil


def test_null_forespoersler_er_ikke_bestaatt_rollback():
    """0/0 er ikke 1.0.

    En rollback uten en eneste forespørsel i av-vinduet beviser ikke at
    forespørsler blir avvist — den beviser at ingen ble prøvd. Samme regel
    som `reparerbare = 0` i feilinjiseringen.
    """
    from manifestskjema import _sjekk_grenser
    feil = _sjekk_grenser("rollback-m01-v1", _rollback(
        **{"oppsett.requests_under_rollback": 0, "maalt.avviste_requests": 0}))
    assert any("requests_under_rollback=0" in f for f in feil), feil


def test_annen_avvisningskode_beviser_ikke_kontrakten():
    """Kontrakten er 503 `modul_inaktiv`, ikke «en eller annen feil».

    Feltet var en fri streng i skjemaet og ble ikke lest av gaten. Et
    artefakt kunne dermed påstå `annen_feil` og likevel bli lest som bevis
    for at deaktiveringen gir det DEFINERTE svaret.
    """
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    art = _rollback(**{"maalt.avvisningskode": "annen_feil"})
    assert any("avvisningskode" in f
               for f in _sjekk_grenser("rollback-m01-v1", art))
    # Låst BEGGE steder: skjemaet og gaten. En kontrakt som bare håndheves
    # ett sted er håndhevet i ett tilfelle.
    assert valider_artefaktformat(art, "rollback-m01-v1")


def test_manglende_avvisningskode_avvises():
    """Feltet er påkrevd nå. Var det valgfritt, kunne det utelates i stedet
    for å endres — samme bypass, én tast mindre."""
    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    art = json.loads(ROLLBACKARTEFAKT.read_text(encoding="utf-8"))
    del art["maalt"]["avvisningskode"]
    assert valider_artefaktformat(art, "rollback-m01-v1")
    assert _sjekk_grenser("rollback-m01-v1", art)


def test_rollbackselen_teller_alle_forespoersler_i_av_vinduet():
    """Selens egen nevner, målt statisk.

    Den var `med_svar` — de som fikk et HTTP-svar. En lukket forbindelse
    forsvant da ut av regnestykket i stedet for å telle som feil, og
    andelen kunne bli 1,0 mens halve trafikken falt på gulvet.

    MUTASJONEN SOM DREPER DENNE: sett nevneren tilbake til `med_svar`.
    """
    sele = (REPOROT / "deploy/staging/rollback-m01.py").read_text(
        encoding="utf-8")
    assert "andel = (len(korrekt) / len(i_av)) if i_av else 0.0" in sele, (
        "rollbackselen regner andelen over noe annet enn alle forespørslene"
        " i av-vinduet")
    assert "len(korrekt) / len(med_svar)" not in sele
