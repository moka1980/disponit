"""Tester v0.2 — hver seksjon mapper til et funn i ChatGPT-review PR-001.
Negative tester er obligatoriske i CI og kan aldri fjernes/svekkes."""
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from .conftest import POLICIES
from policy_validator.audit import lag_loggpost, sikker_beslutning
from policy_validator.engine import (
    STOPP, TILLAT, UNNTAK, Decision, EvaluationContext, MinneTellerLager,
    evaluate, parse_belop)
from policy_validator.schema import valider_policy

NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)  # mandag
CTX = EvaluationContext(tenant_id="t1", aktor_rolle="agent",
                        autentisert=True, kilde="api_token")


@pytest.fixture(scope="module")
def tjeneste():
    return yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def netthandel():
    return yaml.safe_load((POLICIES / "bransjemal-netthandel.yaml").read_text(encoding="utf-8"))


def att(verifikator, ressurs="fak-1", resultat=True, verdi=None, timer=1):
    a = {"verifikator": verifikator, "ressurs_id": ressurs,
         "utloper": (NAA + timedelta(hours=timer)).isoformat()}
    if verdi is not None:
        a["verdi"] = verdi
    else:
        a["resultat"] = resultat
    return a


def hendelse(**over):
    e = {"handling": "faktura.bokfor", "belop": "12000.00", "valuta": "NOK",
         "ressurs_id": "fak-1",
         "dataklasser": ["finansiell"], "dataklasser_kilde": "connector",
         "attestasjoner": {
             "dublettsjekk": att("v_regnskap"),
             "leverandor_i_register": att("v_register"),
             "mva_validert": att("v_regnskap")}}
    e.update(over)
    return e


# ---------- Skjema (funn B: formelt JSON Schema) --------------------------

def test_alle_maler_gyldige():
    for f in POLICIES.glob("bransjemal-*.yaml"):
        assert valider_policy(yaml.safe_load(f.read_text(encoding="utf-8"))) == [], f.name


def test_ukjente_felter_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    p["hemmelig_bakdor"] = True
    assert any("hemmelig_bakdor" in f for f in valider_policy(p))


def test_tekstlig_frekvens_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    p["handlinger"][3]["grenser"]["frekvens"] = "1 per faktura per 14 dager"
    assert valider_policy(p)


def test_manglende_tidssone_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    del p["tidssone"]
    assert valider_policy(p)


def test_ukjent_iana_tidssone_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    p["tidssone"] = "Norge/Narvik"
    assert any("IANA" in f for f in valider_policy(p))


def test_uregistrert_verifikator_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    p["handlinger"][0]["vilkaar"][0]["verifikator"] = "v_finnes_ikke"
    assert any("uregistrert verifikator" in f for f in valider_policy(p))


def test_feilformet_policy_gir_feilliste_ikke_exception():
    assert valider_policy(None)
    assert valider_policy({"handlinger": "ikke en liste"})
    assert valider_policy({"handlinger": [42]})
    assert valider_policy("bare en streng")


def test_irreversibel_uten_rammer_avvises(tjeneste):
    p = yaml.safe_load(yaml.safe_dump(tjeneste))
    h = p["handlinger"][0]
    del h["grenser"]; del h["vilkaar"]
    h["reversering"] = {"type": "irreversibel"}
    assert any("irreversibel" in f for f in valider_policy(p))


# ---------- Autentisert kontekst (funn: uautentisert rolle) ---------------

def test_uten_kontekst_stopp(tjeneste):
    d = evaluate(tjeneste, None, hendelse(), naa=NAA)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "uautentisert_kontekst"


def test_uautentisert_kontekst_stopp(tjeneste):
    ctx = EvaluationContext("t1", "agent", autentisert=False, kilde="x")
    assert evaluate(tjeneste, ctx, hendelse(), naa=NAA).beslutning == STOPP


def test_rolle_fra_event_ignoreres(tjeneste):
    # Angriper later som daglig_leder i payload — konteksten avgjør
    ctx = EvaluationContext("t1", "konsulent", True, "api_token")
    d = evaluate(tjeneste, ctx, hendelse(aktor_rolle="daglig_leder"), naa=NAA)
    assert d.beslutning != TILLAT


# ---------- Beløp (funn D: Decimal, bool, negativ) ------------------------

def test_parse_belop_grensetilfeller():
    assert parse_belop("12000.00") == Decimal("12000.00")
    assert parse_belop(500) == Decimal("500")
    assert parse_belop(True) is None          # bool er int i Python
    assert parse_belop(False) is None
    assert parse_belop(-1) is None
    assert parse_belop("-0.01") is None
    assert parse_belop("NaN") is None
    assert parse_belop("Infinity") is None
    assert parse_belop("10.999") is None      # > 2 desimaler
    assert parse_belop(10.5) is None          # float avvises for penger
    assert parse_belop("abc") is None


def test_bool_belop_gir_ugyldig_data(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(belop=True), naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "ugyldig_data"


def test_negativt_belop_gir_ugyldig_data(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(belop="-500.00"), naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "ugyldig_data"


def test_over_grense_og_inklusiv(tjeneste):
    assert evaluate(tjeneste, CTX, hendelse(belop="25000.00"),
                    naa=NAA).beslutning == TILLAT
    d = evaluate(tjeneste, CTX, hendelse(belop="25000.01"), naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "over_grense"


# ---------- Dataklasser (funn C: fail-closed) -----------------------------

def test_tom_dataklassifisering_gir_unntak(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(dataklasser=[]), naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "manglende_data"


def test_selvrapportert_kilde_avvises(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(dataklasser_kilde="selvrapportert"),
                 naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "manglende_data"


def test_ulovlig_dataklasse(tjeneste):
    d = evaluate(tjeneste, CTX,
                 hendelse(dataklasser=["finansiell", "sensitiv"]), naa=NAA)
    assert d.beslutning != TILLAT


# ---------- Attestasjoner (funn: selvattestering) -------------------------

def test_gyldig_hendelse_tillates(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(), naa=NAA)
    assert d.beslutning == TILLAT


def test_manglende_attestasjon(tjeneste):
    e = hendelse(); del e["attestasjoner"]["mva_validert"]
    d = evaluate(tjeneste, CTX, e, naa=NAA)
    assert d.beslutning == UNNTAK and d.unntak_kategori == "manglende_data"


def test_ubetrodd_verifikator_stopper(tjeneste):
    e = hendelse()
    e["attestasjoner"]["mva_validert"] = att("v_bank")  # ikke betrodd for mva
    assert evaluate(tjeneste, CTX, e, naa=NAA).beslutning == STOPP


def test_utlopt_attestasjon(tjeneste):
    e = hendelse()
    e["attestasjoner"]["mva_validert"] = att("v_regnskap", timer=-1)
    assert evaluate(tjeneste, CTX, e, naa=NAA).beslutning != TILLAT


def test_attestasjon_for_feil_ressurs(tjeneste):
    e = hendelse()
    e["attestasjoner"]["mva_validert"] = att("v_regnskap", ressurs="fak-999")
    assert evaluate(tjeneste, CTX, e, naa=NAA).beslutning != TILLAT


def test_negativ_attestasjon(tjeneste):
    e = hendelse()
    e["attestasjoner"]["dublettsjekk"] = att("v_regnskap", resultat=False)
    assert evaluate(tjeneste, CTX, e, naa=NAA).beslutning != TILLAT


def test_terskelvilkaar_under_min(netthandel):
    e = {"handling": "lager.bestill_pafyll", "belop": "20000.00",
         "valuta": "NOK", "ressurs_id": "ord-1", "leverandor_id": "lev-1",
         "dataklasser": ["intern"], "dataklasser_kilde": "connector",
         "attestasjoner": {
             "leverandor_i_register": att("v_register", "ord-1"),
             "pris_innen_avtale": att("v_register", "ord-1"),
             "prognose_konfidens": att("v_prognose", "ord-1", verdi=0.75)}}
    d = evaluate(netthandel, CTX, e, teller=MinneTellerLager(), naa=NAA)
    assert d.beslutning != TILLAT
    e["attestasjoner"]["prognose_konfidens"] = att("v_prognose", "ord-1", verdi=0.85)
    d = evaluate(netthandel, CTX, e, teller=MinneTellerLager(), naa=NAA)
    assert d.beslutning == TILLAT


# ---------- Frekvens (funn A: strukturert + betrodd teller) ---------------

def purrehendelse():
    return {"handling": "purring.send", "ressurs_id": "fak-7",
            "faktura_id": "fak-7",
            "dataklasser": ["finansiell"], "dataklasser_kilde": "connector",
            "attestasjoner": {
                "forfall_passert_dager": att("v_fordring", "fak-7", verdi=20, timer=24*30),
                "ingen_aktiv_tvist": att("v_fordring", "fak-7", timer=24*30)}}


def test_frekvensregel_uten_tellerlager_stopper(tjeneste):
    d = evaluate(tjeneste, CTX, purrehendelse(), teller=None, naa=NAA)
    assert d.beslutning == STOPP  # fail-closed, aldri hopp over kontrollen


def test_frekvens_haandheves_av_betrodd_teller(tjeneste):
    lager = MinneTellerLager()
    d1 = evaluate(tjeneste, CTX, purrehendelse(), teller=lager, naa=NAA)
    assert d1.beslutning == TILLAT
    lager.registrer(d1.frekvensnokkel, NAA)
    d2 = evaluate(tjeneste, CTX, purrehendelse(), teller=lager,
                  naa=NAA + timedelta(days=3))
    assert d2.beslutning == UNNTAK and d2.unntak_kategori == "over_grense"
    d3 = evaluate(tjeneste, CTX, purrehendelse(), teller=lager,
                  naa=NAA + timedelta(days=15))  # vinduet passert
    assert d3.beslutning == TILLAT


def test_frekvensteller_fra_event_ignoreres(tjeneste):
    lager = MinneTellerLager()
    lager.registrer(("t1", "purring.send", "faktura_id", "fak-7"), NAA)
    e = purrehendelse(); e["frekvens_teller"] = 0  # angriper påstår null
    d = evaluate(tjeneste, CTX, e, teller=lager, naa=NAA + timedelta(days=3))
    assert d.beslutning == UNNTAK


# ---------- Tidssone (funn: DST/naive tidsstempler) -----------------------

def betalingshendelse(ts):
    return {"handling": "betaling.utfor", "belop": "5000.00", "valuta": "NOK",
            "ressurs_id": "fak-1", "tidspunkt": ts,
            "dataklasser": ["finansiell"], "dataklasser_kilde": "connector",
            "attestasjoner": {"faktura_godkjent": att("v_regnskap"),
                              "konto_verifisert": att("v_bank"),
                              "svindelsjekk_bestatt": att("v_svindel")}}


def test_naivt_tidsstempel_avvises(tjeneste):
    d = evaluate(tjeneste, CTX, betalingshendelse("2026-08-03T10:00:00"), naa=NAA)
    assert d.beslutning != TILLAT


def test_tidsvindu_i_policyens_sone(tjeneste):
    # 05:30 UTC = 07:30 Oslo sommertid — innenfor vinduet 07:00-17:00
    d = evaluate(tjeneste, CTX,
                 betalingshendelse("2026-08-03T05:30:00+00:00"), naa=NAA)
    assert d.beslutning == TILLAT
    # 16:00 UTC = 18:00 Oslo — utenfor
    d = evaluate(tjeneste, CTX,
                 betalingshendelse("2026-08-03T16:00:00+00:00"), naa=NAA)
    assert d.beslutning == STOPP and d.effekt == "frys"


# ---------- Logg-før-utførelse (funn: revisjonsloggfeil) ------------------

def test_sikker_beslutning_logger_for_tillat(tjeneste, tmp_path):
    logg = tmp_path / "audit.jsonl"
    d = sikker_beslutning(tjeneste, CTX, hendelse(), logg, naa=NAA)
    assert d.beslutning == TILLAT
    assert logg.exists() and logg.read_text(encoding="utf-8").count("\n") == 1


def test_loggfeil_gir_stopp_aldri_utforelse(tjeneste, tmp_path):
    ulovlig = tmp_path / "finnes_ikke"
    ulovlig.write_text("", encoding="utf-8")  # fil der katalog forventes -> OSError
    d = sikker_beslutning(tjeneste, CTX, hendelse(),
                          ulovlig / "audit.jsonl", naa=NAA)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "logging_feilet"


def test_motor_exception_gir_stopp(tjeneste, tmp_path):
    # Feilformet policy som får evaluate til å feile internt
    odelagt = {"meta": "ikke et objekt", "handlinger": [{"id": None}]}
    d = sikker_beslutning(odelagt, CTX, hendelse(), tmp_path / "a.jsonl", naa=NAA)
    assert d.beslutning != TILLAT


def test_loggpost_strukturert_begrunnelse(tjeneste):
    d = evaluate(tjeneste, CTX, hendelse(), naa=NAA)
    post = lag_loggpost(d, hendelse(), tjeneste)
    assert all("kode" in g for g in post["begrunnelse"])
    assert post["input_hash"] and post["policy_id"]


# ---------- Codex-review PR-002: tre P1-funn ------------------------------
# Alle tre testene under er negative: de skal falle hvis fiksen rulles
# tilbake. De kan aldri fjernes eller svekkes (merge-port nr. 1).

def test_reservasjonen_taaler_at_skrivingen_er_treg(tjeneste, tmp_path,
                                                    monkeypatch):
    """P1, DETERMINISTISK bevis på atomisitet.

    En ren trådtest beviser ingenting her: kjørte jeg mutasjonen «reserver =
    antall() så registrer()» mot 20 tråder med barriere, passerte suiten
    likevel — kappløpet oppsto bare ikke. Testen ville altså ha godkjent
    nøyaktig den koden Codex underkjente.

    Derfor tvinges vinduet åpent i stedet: `registrer` gjøres treg. En
    implementasjon som leser og skriver i to trinn slipper da garantert to
    tråder forbi grensen. Den ekte implementasjonen kaller aldri `registrer`
    fra `reserver` — den skriver inne i låsen — så den er upåvirket av at
    `registrer` er treg, og slipper gjennom nøyaktig én."""
    import threading
    import time
    original = MinneTellerLager.registrer

    def treg_registrer(self, nokkel, tidspunkt):
        time.sleep(0.05)          # holder vinduet åpent utenfor låsen
        original(self, nokkel, tidspunkt)

    monkeypatch.setattr(MinneTellerLager, "registrer", treg_registrer)

    lager = MinneTellerLager()
    logg = tmp_path / "audit.jsonl"
    start = threading.Barrier(2)
    resultat: list[str] = []
    laas = threading.Lock()

    def kjor():
        start.wait()
        d = sikker_beslutning(tjeneste, CTX, purrehendelse(), logg,
                              teller=lager, naa=NAA)
        with laas:
            resultat.append(d.beslutning)

    traader = [threading.Thread(target=kjor) for _ in range(2)]
    for t in traader:
        t.start()
    for t in traader:
        t.join()

    assert resultat.count(TILLAT) == 1, resultat


def test_frekvensreservasjon_under_samtidighet_royktest(tjeneste, tmp_path):
    """Røyktest, ikke atomisitetsbevis (se testen over for det).
    Maks er 1 purring per faktura per 14 dager; med 20 tråder skal nøyaktig
    ÉN få TILLAT, og loggen skal ha én post per beslutning — også de tapte."""
    import threading
    lager = MinneTellerLager()
    logg = tmp_path / "audit.jsonl"
    start = threading.Barrier(20)
    resultat: list[str] = []
    laas = threading.Lock()

    def kjor():
        start.wait()  # maksimer sjansen for ekte kappløp
        d = sikker_beslutning(tjeneste, CTX, purrehendelse(), logg,
                              teller=lager, naa=NAA)
        with laas:
            resultat.append(d.beslutning)

    traader = [threading.Thread(target=kjor) for _ in range(20)]
    for t in traader:
        t.start()
    for t in traader:
        t.join()

    assert resultat.count(TILLAT) == 1, resultat
    assert len(resultat) == 20
    # Loggen skal ha én post per beslutning — også de tapte kappløpene
    assert logg.read_text(encoding="utf-8").count("\n") == 20


def test_loggen_bruker_autentisert_aktor_ikke_payload(tjeneste):
    """P1: loggposten hentet aktør fra hendelsen. En innsender kunne dermed
    skrive hvilken som helst aktør inn i revisjonssporet."""
    d = evaluate(tjeneste, CTX, hendelse(), naa=NAA)
    e = hendelse()
    e["aktor_rolle"] = "daglig_leder"        # forfalsket i payloaden
    post = lag_loggpost(d, e, tjeneste, CTX)
    assert post["aktor"] == "agent"          # fra EvaluationContext
    assert post["tenant"] == "t1" and post["kilde"] == "api_token"


def test_tellerfeil_gir_stopp_og_aldri_tillat(tjeneste, tmp_path):
    """P1: teller.registrer() lå utenfor try og kunne kaste ETTER at TILLAT
    var logget — exception unnslapp fail-closed-kontrakten."""
    class OedelagtTeller(MinneTellerLager):
        def reserver(self, *a, **k):
            raise RuntimeError("teller nede")

    logg = tmp_path / "audit.jsonl"
    d = sikker_beslutning(tjeneste, CTX, purrehendelse(), logg,
                          teller=OedelagtTeller(), naa=NAA)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "tellerfeil"
    assert '"beslutning": "STOPP"' in logg.read_text(encoding="utf-8")


def test_alle_filkall_har_eksplisitt_utf8():
    """P2: policyfiler ble lest uten encoding og feilet på Windows der
    standardkodingen ikke er UTF-8. Denne testen hindrer at det siger inn
    igjen."""
    import re
    tekstkall = re.compile(r"\.(read_text|write_text|open)\(")
    binaer = re.compile(r"""["'][rwa]b\+?["']""")   # binærmodus tar ikke encoding
    # os.open gir en rå fildeskriptor og har ingen encoding-parameter i det
    # hele tatt — revisjonsloggen skriver ferdig UTF-8-kodede bytes dit med
    # vilje, for å styre skrivingen selv (se audit.skriv).
    raa_fd = re.compile(r"\bos\.(open|write|fdopen)\(")
    rot = Path(__file__).resolve().parents[3]
    synder = []
    for fil in (rot / "platform").rglob("*.py"):
        if "_v01_deprecated" in fil.name:
            continue
        for nr, linje in enumerate(fil.read_text(encoding="utf-8").splitlines(), 1):
            if tekstkall.search(linje) and "encoding" not in linje \
                    and not binaer.search(linje) and not raa_fd.search(linje):
                synder.append(f"{fil.relative_to(rot)}:{nr}: {linje.strip()}")
    assert not synder, "filkall uten eksplisitt encoding:\n" + "\n".join(synder)


# ---------- Codex-review runde 2: ved_brudd ved tapt reservasjon ----------

def _med_ved_brudd(policy, handling_id, verdi):
    """Kopi av policyen der én handling får et annet ved_brudd."""
    import copy
    p = copy.deepcopy(policy)
    for h in p["handlinger"]:
        if h["id"] == handling_id:
            h["ved_brudd"] = verdi
    return p


class TaptKapplopTeller(MinneTellerLager):
    """Simulerer at kappløpet ble tapt MELLOM evaluering og reservasjon.

    Dette er den eneste måten å treffe reservasjonsgrenen på: det rådgivende
    oppslaget må se ledig plass (ellers blokkerer `evaluate` først og grenen
    nås aldri), mens den bindende reservasjonen må feile.

    Første forsøk på denne testen brukte to ekte kall mot et vanlig lager.
    Den passerte — men den passerte OGSÅ med den hardkodede UNNTAK-en Codex
    fant, fordi `evaluate` allerede hadde blokkert på det rådgivende
    oppslaget. Den testet altså feil kodevei.
    """

    def antall(self, nokkel, siden):
        return 0                      # rådgivende: ser ledig plass

    def reserver(self, nokkel, siden, maks, tidspunkt):
        return False                  # bindende: plassen ble tatt av en annen


@pytest.mark.parametrize("ved_brudd,forventet,effekt", [
    ("stopp_og_varsle", STOPP, "varsle"),
    ("frys", STOPP, "frys"),
    ("unntakskø", UNNTAK, None),
])
def test_tapt_reservasjon_folger_ved_brudd(tjeneste, tmp_path,
                                           ved_brudd, forventet, effekt):
    """P1 (Codex runde 2): reservasjonsgrenen hardkodet UNNTAK, så en
    handling med stopp_og_varsle eller frys ble nedgradert til unntakskø
    nettopp når den tapte kappløpet om siste plass — der den strengeste
    håndteringen er mest påkrevd."""
    policy = _med_ved_brudd(tjeneste, "purring.send", ved_brudd)
    d = sikker_beslutning(policy, CTX, purrehendelse(), tmp_path / "a.jsonl",
                          teller=TaptKapplopTeller(), naa=NAA)
    assert d.beslutning == forventet
    assert d.effekt == effekt
    assert d.begrunnelse[-1].kode == "frekvensgrense_naadd_ved_reservasjon"
    if forventet == UNNTAK:
        assert d.unntak_kategori == "over_grense"
    else:
        assert d.unntak_kategori is None


@pytest.mark.parametrize("ved_brudd", ["stopp_og_varsle", "frys", "unntakskø"])
def test_reservasjonstap_og_vanlig_frekvensbrudd_gir_samme_utfall(tjeneste,
                                                                  tmp_path,
                                                                  ved_brudd):
    """Invariant: uansett om grensen fanges av det rådgivende oppslaget
    eller av den bindende reservasjonen, skal beslutning og effekt være
    identiske. Ellers finnes det to sannheter om samme regel."""
    policy = _med_ved_brudd(tjeneste, "purring.send", ved_brudd)

    via_reservasjon = sikker_beslutning(policy, CTX, purrehendelse(),
                                        tmp_path / "a.jsonl",
                                        teller=TaptKapplopTeller(), naa=NAA)

    brukt = MinneTellerLager()                 # grensen alt nådd i oppslaget
    brukt.registrer(("t1", "purring.send", "faktura_id", "fak-7"), NAA)
    via_oppslag = evaluate(policy, CTX, purrehendelse(), teller=brukt, naa=NAA)

    assert (via_reservasjon.beslutning, via_reservasjon.effekt) == \
           (via_oppslag.beslutning, via_oppslag.effekt), ved_brudd


# ---------- Codex-review runde 3: kun True er en gyldig reservasjon -------

@pytest.mark.parametrize("returverdi", [None, 1, "ja", [], 0, object()])
def test_ugyldig_returverdi_fra_reserver_gir_stopp(tjeneste, tmp_path,
                                                   returverdi):
    """P1 (Codex runde 3): grenen behandlet «ikke False» som suksess. En
    implementasjon som glemmer å returnere gir None — og None ga TILLAT uten
    at noen plass var reservert. Nøyaktig tre utfall er lovlige:
    True => reservert, False => frekvensbrudd, alt annet => STOPP tellerfeil.

    Sannhetsverdier som 1 og "ja" er også STOPP, med vilje: en slurvete
    implementasjon skal feile lukket, ikke slippe gjennom."""
    class RarTeller(MinneTellerLager):
        def antall(self, nokkel, siden):
            return 0                       # rådgivende: ledig plass
        def reserver(self, nokkel, siden, maks, tidspunkt):
            return returverdi

    logg = tmp_path / "audit.jsonl"
    d = sikker_beslutning(tjeneste, CTX, purrehendelse(), logg,
                          teller=RarTeller(), naa=NAA)
    assert d.beslutning == STOPP, f"{returverdi!r} ble behandlet som suksess"
    assert d.begrunnelse[-1].kode == "tellerfeil"
    assert d.begrunnelse[-1].params["type"] == "ugyldig_returverdi"
    assert '"beslutning": "STOPP"' in logg.read_text(encoding="utf-8")


def test_ekte_true_reserverer_og_gir_tillat(tjeneste, tmp_path):
    """Motstykket: en korrekt implementasjon som returnerer ekte True skal
    gi TILLAT. Uten denne kunne fiksen over «bestås» ved å alltid stoppe."""
    d = sikker_beslutning(tjeneste, CTX, purrehendelse(),
                          tmp_path / "audit.jsonl",
                          teller=MinneTellerLager(), naa=NAA)
    assert d.beslutning == TILLAT


# ---------- Codex-review runde 4: 1:1-kontrakten under samtidighet -------

def test_loggskriving_serialiseres_selv_naar_skrivingen_deles(tjeneste,
                                                              tmp_path,
                                                              monkeypatch):
    """P1 (Codex runde 4): 20 samtidige beslutninger ga 19 loggposter. Hvert
    kall åpnet sitt eget bufrede filhåndtak, så to skrivinger kunne flettes
    inn i hverandre og ødelegge en linje — og da kan TILLAT returneres uten
    tilhørende revisjonspost. Det er brudd på selve 1:1-kontrakten.

    DETERMINISTISK, ikke tidsavhengig: Codex' egen reproduksjon feilet 16 av
    20 kjøringer, altså ville den ha «bestått» i 4 av 20. Her deles hver
    skriving i to med en pause imellom, slik at en userialisert
    implementasjon GARANTERT fletter. Holder låsen rundt hele operasjonen,
    er delingen uten betydning."""
    import json as _json
    import threading
    import time
    from policy_validator import audit as audit_modul

    def delt_skriving(fd, data):
        midt = len(data) // 2
        os.write(fd, data[:midt])
        time.sleep(0.01)          # vindu der en annen tråd kan flette seg inn
        os.write(fd, data[midt:])

    monkeypatch.setattr(audit_modul, "_skriv_raa", delt_skriving)

    logg = tmp_path / "audit.jsonl"
    antall = 20
    start = threading.Barrier(antall)

    def kjor():
        start.wait()
        sikker_beslutning(tjeneste, CTX, hendelse(), logg, naa=NAA)

    traader = [threading.Thread(target=kjor) for _ in range(antall)]
    for t in traader:
        t.start()
    for t in traader:
        t.join()

    linjer = [l for l in logg.read_text(encoding="utf-8").splitlines() if l]
    assert len(linjer) == antall, f"{len(linjer)} loggposter for {antall} beslutninger"
    for nr, linje in enumerate(linjer, 1):
        try:
            post = _json.loads(linje)
        except ValueError:
            raise AssertionError(f"loggpost {nr} er ødelagt av fletting: {linje[:120]!r}")
        assert post["beslutning"] == TILLAT and post["aktor"] == "agent"


def test_hver_beslutning_har_noeyaktig_en_loggpost(tjeneste, tmp_path):
    """1:1-kontrakten uten kunstig deling — fanger tap som skjer av andre
    grunner enn fletting (f.eks. at en gren returnerer uten å logge)."""
    import threading
    logg = tmp_path / "audit.jsonl"
    beslutninger: list[str] = []
    laas = threading.Lock()
    start = threading.Barrier(20)

    def kjor():
        start.wait()
        d = sikker_beslutning(tjeneste, CTX, hendelse(), logg, naa=NAA)
        with laas:
            beslutninger.append(d.beslutning)

    traader = [threading.Thread(target=kjor) for _ in range(20)]
    for t in traader:
        t.start()
    for t in traader:
        t.join()

    linjer = [l for l in logg.read_text(encoding="utf-8").splitlines() if l]
    assert len(beslutninger) == 20
    assert len(linjer) == len(beslutninger), \
        f"{len(linjer)} loggposter for {len(beslutninger)} beslutninger"


def test_skriv_holder_fillaasen_rundt_hele_skrivingen(tjeneste, tmp_path,
                                                      monkeypatch):
    """Kontrakttest for serialiseringen — og den eneste av disse som er
    plattformuavhengig.

    Bakgrunn: de rene samtidighetstestene over fanget IKKE at låsen ble
    fjernet, fordi pytest sin tmp_path ligger på ext4, der kjernen holder
    O_APPEND-skrivinger atomiske uansett. På `/mnt/c` (drvfs) — der dette
    repoet faktisk bor — mistet den gamle implementasjonen 16 av 20 poster.
    Feilen er altså usynlig på ett filsystem og katastrofal på et annet.
    Derfor testes egenskapen direkte i stedet for å håpe på et kappløp:

      1. skrivingen SKAL gå gjennom `_skriv_raa` (den serialiserte veien)
      2. fillåsen SKAL være holdt mens den skjer
    """
    from policy_validator import audit as audit_modul

    logg = tmp_path / "audit.jsonl"
    kall = []

    def kontrollert(fd, data):
        kall.append(len(data))
        assert audit_modul._fillaas(logg).locked(), \
            "skriv() holdt ikke fillåsen mens den skrev"
        os.write(fd, data)

    monkeypatch.setattr(audit_modul, "_skriv_raa", kontrollert)
    sikker_beslutning(tjeneste, CTX, hendelse(), logg, naa=NAA)

    assert kall, "skriv() gikk utenom den serialiserte skrivingen"
    assert logg.read_text(encoding="utf-8").count("\n") == 1


# ---------- Codex-review runde 5: os.write er ikke write-all -------------

def test_delvis_skriving_fullfores(tjeneste, tmp_path, monkeypatch):
    """P1 (Codex runde 5): os.write kan skrive færre bytes enn bedt om uten
    å kaste. Ignoreres returverdien, blir en avkortet loggpost behandlet som
    vellykket — den ser ut som evidens, men er en halv post."""
    ekte_write = os.write

    def kort_write(fd, data):
        return ekte_write(fd, data[:7])       # skriver alltid maks 7 bytes

    monkeypatch.setattr(os, "write", kort_write)

    logg = tmp_path / "audit.jsonl"
    d = sikker_beslutning(tjeneste, CTX, hendelse(), logg, naa=NAA)
    monkeypatch.undo()

    assert d.beslutning == TILLAT
    innhold = logg.read_text(encoding="utf-8")
    assert innhold.endswith("\n"), "loggposten mangler avsluttende linjeskift"
    linjer = [l for l in innhold.splitlines() if l]
    assert len(linjer) == 1
    import json as _json
    post = _json.loads(linjer[0])             # kaster hvis posten er avkortet
    assert post["beslutning"] == TILLAT and post["aktor"] == "agent"


def test_null_bytes_skrevet_gir_stopp_og_ingen_evig_lokke(tjeneste, tmp_path,
                                                          monkeypatch):
    """Null bytes skrevet er ingen fremgang. Da skal det kastes — ikke
    løkkes evig — og sikker_beslutning skal gjøre det til STOPP."""
    ekte_write = os.write

    def null_write(fd, data):
        return 0

    monkeypatch.setattr(os, "write", null_write)
    logg = tmp_path / "audit.jsonl"
    d = sikker_beslutning(tjeneste, CTX, hendelse(), logg, naa=NAA)
    monkeypatch.setattr(os, "write", ekte_write)

    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "logging_feilet"


def test_avkortet_post_sluker_ikke_neste_post(tjeneste, tmp_path, monkeypatch):
    """Feiler skrivingen etter delvis fremgang, står det en halv linje i
    filen. Den skal ikke kunne smelte sammen med NESTE post og gjøre to
    poster uleselige i stedet for én."""
    ekte_write = os.write
    tilstand = {"bytes_igjen": 30}

    def sviktende_write(fd, data):
        if tilstand["bytes_igjen"] <= 0:
            raise OSError("disken er full")
        n = min(len(data), tilstand["bytes_igjen"])
        tilstand["bytes_igjen"] -= n
        return ekte_write(fd, data[:n])

    logg = tmp_path / "audit.jsonl"
    monkeypatch.setattr(os, "write", sviktende_write)
    d1 = sikker_beslutning(tjeneste, CTX, hendelse(), logg, naa=NAA)
    monkeypatch.setattr(os, "write", ekte_write)
    assert d1.beslutning == STOPP             # fail-closed

    d2 = sikker_beslutning(tjeneste, CTX, hendelse(), logg, naa=NAA)
    assert d2.beslutning == TILLAT

    import json as _json
    linjer = [l for l in logg.read_text(encoding="utf-8").splitlines() if l]
    gyldige = [l for l in linjer if _lesbar(l, _json)]
    assert len(gyldige) == 1, f"den hele posten er uleselig: {linjer!r}"
    assert gyldige[0].count('"beslutning"') == 1


def _lesbar(linje, _json):
    try:
        _json.loads(linje)
        return True
    except ValueError:
        return False
