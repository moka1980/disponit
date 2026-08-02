"""PR-004-tester: PostgreSQL-tilstandslag (ADR-001) + attestasjonssignatur.

DB-testene krever DISPONIT_TEST_DSN og markeres pg — de kjører på staging
og lokalt med PostgreSQL, og hoppes over ellers. Signaturtestene kjører
alltid.
"""
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from .conftest import POLICIES
from policy_validator import attestering
from policy_validator.engine import STOPP, TILLAT, UNNTAK, EvaluationContext, Grunn

DSN = os.environ.get("DISPONIT_TEST_DSN")
pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)
CTX = EvaluationContext("t-pg", "agent", True, "api_token")
NOKLER = {"v_fordring": {"k1": "x" * 40}, "v_regnskap": {"k1": "y" * 40}}


# ---------------- Attestering (kjører alltid) ----------------------------

def att_usignert(verifikator="v_fordring", ressurs="fak-1", **felt):
    a = {"verifikator": verifikator, "ressurs_id": ressurs,
         "utloper": (NAA + timedelta(hours=1)).isoformat(), "resultat": True}
    a.update(felt)
    return a


def test_signer_og_verifiser_rundtur():
    a = attestering.signer(att_usignert(), "k1", NOKLER["v_fordring"]["k1"])
    assert attestering.verifiser(a, NOKLER) is True


def test_manipulert_innhold_avvises():
    a = attestering.signer(att_usignert(), "k1", NOKLER["v_fordring"]["k1"])
    a["resultat"] = False  # tukling etter signering
    assert attestering.verifiser(a, NOKLER) is False


def test_feil_nokkel_og_ukjent_verifikator_avvises():
    a = attestering.signer(att_usignert(), "k1", "feil-hemmelighet-" + "z" * 20)
    assert attestering.verifiser(a, NOKLER) is False
    b = attestering.signer(att_usignert(verifikator="v_ukjent"), "k1", "w" * 40)
    assert attestering.verifiser(b, NOKLER) is False


def test_kontroller_hendelse_krever_signatur_pa_alle():
    god = attestering.signer(att_usignert(), "k1", NOKLER["v_fordring"]["k1"])
    ok = attestering.kontroller_hendelse({"attestasjoner": {"a": god}}, NOKLER)
    assert ok is None
    brudd = attestering.kontroller_hendelse(
        {"attestasjoner": {"a": god, "b": att_usignert()}}, NOKLER)
    assert isinstance(brudd, Grunn)
    assert brudd.kode == "attestasjon_uten_signatur"


def test_nokkelregister_avviser_svake_nokler():
    with pytest.raises(ValueError):
        attestering._valider_register({"v_x": {"k1": "kort"}})


def test_nokkelfil_med_apne_rettigheter_avvises(tmp_path):
    fil = tmp_path / "nokler.json"
    fil.write_text('{"v_x": {"k1": "' + "a" * 40 + '"}}', encoding="utf-8")
    fil.chmod(0o644)
    with pytest.raises(PermissionError):
        attestering.last_nokler(str(fil))
    fil.chmod(0o600)
    assert attestering.last_nokler(str(fil))["v_x"]["k1"] == "a" * 40


# ---------------- PostgreSQL (ADR-001) -----------------------------------

@pytest.fixture()
def conn():
    from db.pg import koble, migrer
    c = koble(DSN)
    migrer(c)
    c.execute("DELETE FROM frekvens_hendelser"); c.commit()
    yield c
    c.close()


@pytest.fixture(scope="module")
def tjeneste():
    return yaml.safe_load((POLICIES / "bransjemal-tjenestebedrift.yaml").read_text(encoding="utf-8"))


def purrehendelse(fak="fak-pg-1", signert=True):
    a1 = att_usignert("v_fordring", fak, verdi=20); a1.pop("resultat")
    a2 = att_usignert("v_fordring", fak)
    if signert:
        a1 = attestering.signer(a1, "k1", NOKLER["v_fordring"]["k1"])
        a2 = attestering.signer(a2, "k1", NOKLER["v_fordring"]["k1"])
    return {"handling": "purring.send", "ressurs_id": fak, "faktura_id": fak,
            "dataklasser": ["finansiell"], "dataklasser_kilde": "connector",
            "attestasjoner": {"forfall_passert_dager": a1,
                              "ingen_aktiv_tvist": a2}}


@pg
def test_migrasjon_er_idempotent(conn):
    from db.pg import migrer
    assert migrer(conn) == [1]  # andre kjøring — ingen feil


@pg
def test_revisjonslogg_er_append_only_i_databasen(conn):
    import psycopg
    conn.execute("INSERT INTO revisjonslogg (input_hash, policy_id,"
                 " beslutning, begrunnelse) VALUES ('h','p','STOPP','[]')")
    conn.commit()
    for sql in ("UPDATE revisjonslogg SET beslutning='TILLAT'",
                "DELETE FROM revisjonslogg",
                "TRUNCATE revisjonslogg"):
        with pytest.raises(psycopg.Error):
            conn.execute(sql)
        conn.rollback()


@pg
def test_atomisk_reservasjon_under_ekte_kappløp(conn):
    """20 tråder, egne tilkoblinger, maks=3 — nøyaktig 3 skal vinne."""
    from db.pg import PgTellerLager, koble
    nokkel = ("t-pg", "purring.send", "faktura_id", "kapplop")
    resultater = []

    def prov():
        c = koble(DSN)
        try:
            resultater.append(PgTellerLager(c).reserver(
                nokkel, NAA - timedelta(days=14), 3, NAA))
        finally:
            c.close()

    traader = [threading.Thread(target=prov) for _ in range(20)]
    for t in traader: t.start()
    for t in traader: t.join()
    assert resultater.count(True) == 3 and resultater.count(False) == 17


@pg
def test_reservasjon_og_logg_i_samme_transaksjon(conn, tjeneste):
    """ADR-001 krav 2: TILLAT gir nøyaktig én reservasjon OG én loggpost;
    kappløpstaper gir loggpost med blokkert utfall og INGEN reservasjon."""
    from db.pg import sikker_beslutning_pg
    d1 = sikker_beslutning_pg(tjeneste, CTX, purrehendelse(), conn,
                              naa=NAA, nokler=NOKLER)
    assert d1.beslutning == TILLAT
    d2 = sikker_beslutning_pg(tjeneste, CTX, purrehendelse(), conn,
                              naa=NAA + timedelta(days=3), nokler=NOKLER)
    assert d2.beslutning == UNNTAK  # frekvensgrense (maks 1 per 14 dager)
    ant = conn.execute("SELECT count(*) FROM frekvens_hendelser").fetchone()[0]
    logg = conn.execute("SELECT beslutning FROM revisjonslogg"
                        " WHERE tenant='t-pg' ORDER BY id").fetchall()
    conn.rollback()
    assert ant == 1                       # taperen reserverte ingenting
    assert [r[0] for r in logg][-2:] == ["TILLAT", "UNNTAK"]  # begge logget


@pg
def test_signaturport_stopper_usignert_hendelse(conn, tjeneste):
    from db.pg import sikker_beslutning_pg
    d = sikker_beslutning_pg(tjeneste, CTX, purrehendelse(signert=False),
                             conn, naa=NAA, nokler=NOKLER)
    assert d.beslutning == STOPP
    assert d.begrunnelse[-1].kode == "attestasjon_uten_signatur"
    siste = conn.execute("SELECT beslutning FROM revisjonslogg"
                         " ORDER BY id DESC LIMIT 1").fetchone()[0]
    conn.rollback()
    assert siste == STOPP  # også signaturbrudd revisjonslogges


@pg
def test_db_nede_gir_stopp_aldri_sideeffekt(tjeneste):
    from db.pg import koble, sikker_beslutning_pg
    c = koble(DSN)
    c.close()  # simuler DB borte
    d = sikker_beslutning_pg(tjeneste, CTX, purrehendelse(), c,
                             naa=NAA, nokler=NOKLER)
    assert d.beslutning == STOPP
