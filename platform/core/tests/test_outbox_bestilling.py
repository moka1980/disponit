"""038 — outbox-generaliseringen: opphav, saker og idempotenslageret.

Outboxen var M-37-forankret på skjemanivå; her prøves at oppmykingen ikke
mykner noe annet: CHECK-en dekker begge opphavskombinasjonene uttømmende
(portene 1–3), `opprinnelse` er immutabel (4), backfillen traff alle
eksisterende rader (5 — bevist i pr008s rebuild-tester som sår 007-æra-
rader og migrerer forbi 038), DEFAULT er fjernet (6), runtime har ingen
INSERT (7), og M-37-veien er urørt ende-til-ende (8 — regresjonsporten
ER de eksisterende m37-/pr007-/pr012-suitene, som kjører uendret mot 038;
arbeiderveien går nå gjennom `opprett_reparasjonsoppdrag`).

Sakene (23–27): `sikre_sak_for_oppdrag` er idempotent fordi UNIK-indeksen
gjør den det; terminale saker gjenbrukes aldri; `oppdrag.unntak_id`
forblir NULL gjennom hele sakslivsløpet.

Alle tester konstruerer egen tilstand. Ingen delt fixture.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _lag_sak, _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _rt():
    from db.pg import koble
    return koble(DSN)


def _beslutningsgrunnlag(migrator_):
    """En TILLAT-loggpost + kryptert payload — det en bestilling etterlater."""
    from db import kryptering
    _sett_kontekst(migrator_, TENANT)
    logg = migrator_.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT','[]',%s)"
        " RETURNING id", (TENANT, secrets.token_hex(8))).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator_, TENANT)
    ct, nonce = kryptering.krypter(
        dek, {"mal_url": "https://k.example/", "kravsett": "wcag21_aa",
              "omfang": "enkeltside"}, TENANT, key_id)
    migrator_.commit()
    return logg, ct, key_id, nonce


def _beslutningsoppdrag(rt, migrator_):
    logg, ct, key_id, nonce = _beslutningsgrunnlag(migrator_)
    _sett_kontekst(rt, TENANT)
    oid = rt.execute(
        "SELECT opprett_beslutningsoppdrag(%s,%s,'kontroll.wcag.nettsted',"
        "'kontroll.wcag.nettsted','m_wcag_audit',%s,%s,%s,"
        "now()+interval '30 minutes',now()+interval '30 minutes')",
        (TENANT, logg, ct, key_id, nonce)).fetchone()[0]
    rt.commit()
    return int(oid), logg


@pg
def test_opphavskombinasjonene_er_uttommende(migrator):
    """Portene 1–3: M-37-oppdrag uten trio avvises; beslutningsoppdrag med
    unntak_id avvises; beslutningsoppdrag uten beslutnings-FK avvises."""
    from db import kryptering
    sak, logg = _lag_sak(migrator, TENANT)
    _sett_kontekst(migrator, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(migrator, TENANT)
    ct, nonce = kryptering.krypter(dek, {"x": 1}, TENANT, key_id)
    basis = ("payload_kryptert, key_id, nonce, utforelsesfrist,"
             " evidensfrist")
    verdier = "%s,%s,%s,now()+interval '1 hour',now()+interval '1 day'"
    # 1: reparasjon uten trio
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            f"INSERT INTO oppdrag (opprinnelse, tenant, oppdragstype,"
            f" handling, eiermodul, {basis}) VALUES ('m37_reparasjon',%s,"
            f"'reinnsending','purring.send','e:r',{verdier})",
            (TENANT, ct, key_id, nonce))
    migrator.rollback()
    # 2: beslutning MED unntak_id. BEFORE-vakta (koblingsvakta, som nå
    # kjenner opphavet) kan nå å si nei før CHECK-en — begge er lagringens
    # avvisning, og loggposten her er dessuten ikke en TILLAT.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.RaiseException)):
        migrator.execute(
            f"INSERT INTO oppdrag (opprinnelse, tenant, unntak_id,"
            f" beslutning_loggpost_id, oppdragstype, handling, eiermodul,"
            f" {basis}, koblingsstatus) VALUES ('beslutning',%s,%s,%s,"
            f"'reinnsending','purring.send','e:r',{verdier},'KOBLET')",
            (TENANT, sak, logg, ct, key_id, nonce))
    migrator.rollback()
    # 3: beslutning UTEN beslutnings-FK — vakta sier nei (EXISTS mot NULL
    # er tomt) før CHECK-en; begge er lagringens avvisning.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises((psycopg.errors.CheckViolation,
                        psycopg.errors.RaiseException)):
        migrator.execute(
            f"INSERT INTO oppdrag (opprinnelse, tenant, oppdragstype,"
            f" handling, eiermodul, {basis}) VALUES ('beslutning',%s,"
            f"'reinnsending','purring.send','e:r',{verdier})",
            (TENANT, ct, key_id, nonce))
    migrator.rollback()
    # 6: DEFAULT er fjernet — INSERT uten opprinnelse feiler
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.NotNullViolation):
        migrator.execute(
            f"INSERT INTO oppdrag (tenant, unntak_id, loggpost_id,"
            f" repair_operation_id, oppdragstype, handling, eiermodul,"
            f" {basis}) VALUES (%s,%s,%s,%s,'reinnsending','purring.send',"
            f"'e:r',{verdier})",
            (TENANT, sak, logg, secrets.token_hex(16), ct, key_id, nonce))
    migrator.rollback()


@pg
def test_opprinnelse_er_immutabel_og_runtime_uten_insert(migrator):
    """Port 4 + 7. Kontroll: fjern `oppdrag_opprinnelse_immutable`-triggeren,
    så blir første halvdel grønn på feil grunnlag."""
    rt = _rt()
    try:
        oid, _ = _beslutningsoppdrag(rt, migrator)
        _sett_kontekst(migrator, TENANT)
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute("UPDATE oppdrag SET opprinnelse="
                             "'m37_reparasjon' WHERE tenant=%s AND id=%s",
                             (TENANT, oid))
        migrator.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("INSERT INTO oppdrag (opprinnelse, tenant,"
                       " oppdragstype, handling, eiermodul, payload_kryptert,"
                       " key_id, nonce, utforelsesfrist, evidensfrist)"
                       " VALUES ('beslutning',%s,'t','h','e','\\x00','k',"
                       "'\\x00',now(),now())", (TENANT,))
        rt.rollback()
    finally:
        rt.close()


@pg
def test_sikre_sak_er_idempotent_og_terminal_gjenbrukes_aldri(migrator):
    """Portene 23–27. Kontroll: fjern UNIK-indeksen
    `en_apen_sak_per_oppdrag_arsak`, så tåler ikke kappløpsgrenen lenger
    to samtidige — og gjenbruksgrenen mister beviset sitt."""
    rt = _rt()
    try:
        oid, logg = _beslutningsoppdrag(rt, migrator)
        _sett_kontekst(rt, TENANT)
        s1 = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                        "'reaper','r1')", (TENANT, oid)).fetchone()[0]
        s2 = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                        "'reaper','r2')", (TENANT, oid)).fetchone()[0]
        rt.commit()
        assert s1 == s2, "gjentatt kall ga ny sak (port 25)"
        # Saken arver beslutningsloggposten som lineage, og en ANNEN
        # årsaksfamilie får sin EGEN sak (26-motstykket).
        _sett_kontekst(migrator, TENANT)
        rad = migrator.execute(
            "SELECT loggpost_id, sakstype, arsak FROM unntak WHERE id=%s",
            (s1,)).fetchone()
        assert rad == (logg, "normal", "evidensfrist"), rad
        s3 = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'sikkerhet',"
                        "'kvitteringsport','r3')", (TENANT, oid)).fetchone()[0]
        rt.commit()
        assert s3 != s1
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute(
            "SELECT sakstype, prioritet FROM unntak WHERE id=%s",
            (s3,)).fetchone() == ("sikkerhet", "hoy")
        # Terminal sak gjenbrukes aldri: løs den første (via statemaskinens
        # lovlige vei ny→under_behandling→løst) → nytt kall gir NY.
        migrator.execute("UPDATE unntak SET status='under_behandling'"
                         " WHERE id=%s", (s1,))
        migrator.execute("UPDATE unntak SET status='løst' WHERE id=%s",
                         (s1,))
        migrator.commit()
        s4 = rt.execute("SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                        "'reaper','r4')", (TENANT, oid)).fetchone()[0]
        rt.commit()
        assert s4 not in (s1, s3), "terminal sak ble gjenbrukt (port 26)"
        _sett_kontekst(migrator, TENANT)
        assert migrator.execute("SELECT status FROM unntak WHERE id=%s",
                                (s1,)).fetchone() == ("løst",), \
            "den terminale saken ble rørt"
        # Port 27: oppdragets unntak_id forble NULL gjennom hele livsløpet.
        assert migrator.execute(
            "SELECT unntak_id FROM oppdrag WHERE tenant=%s AND id=%s",
            (TENANT, oid)).fetchone() == (None,)
        migrator.rollback()
    finally:
        rt.close()


@pg
def test_to_samtidige_sikre_sak_gir_noyaktig_en(migrator):
    """Kappløpshalvdelen av port 25: indeksen serialiserer; taperen leser
    vinnerens rad i unique_violation-grenen."""
    import threading
    rt0 = _rt()
    try:
        oid, _ = _beslutningsoppdrag(rt0, migrator)
    finally:
        rt0.close()
    resultater = []

    def prov(n):
        rt = _rt()
        try:
            _sett_kontekst(rt, TENANT)
            resultater.append(rt.execute(
                "SELECT sikre_sak_for_oppdrag(%s,%s,'evidensfrist',"
                "'reaper',%s)", (TENANT, oid, f"r{n}")).fetchone()[0])
            rt.commit()
        finally:
            rt.close()

    t1 = threading.Thread(target=prov, args=(1,))
    t2 = threading.Thread(target=prov, args=(2,))
    t1.start(); t2.start(); t1.join(); t2.join()
    assert len(set(resultater)) == 1, resultater
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s AND oppdrag_id=%s"
        " AND arsak='evidensfrist'", (TENANT, oid)).fetchone()[0]
    migrator.rollback()
    assert n == 1, n


@pg
def test_bestillingsidempotens_er_immutabel_ogsaa_mot_delete(migrator):
    """§2.3: UPDATE og DELETE avvises — en slettbar rad ville latt en nøkkel
    gjenbrukes med ny intensjon (V4-1 omgått via en annen skrivevei)."""
    _sett_kontekst(migrator, TENANT)
    nokkel = "n-" + secrets.token_hex(8)
    migrator.execute(
        "INSERT INTO bestilling_idempotens (tenant, idempotensnokkel,"
        " intensjonshash, beslutning) VALUES (%s,%s,%s,'stopp')",
        (TENANT, nokkel, "a" * 64))
    migrator.commit()
    for sql in ["UPDATE bestilling_idempotens SET intensjonshash=%s"
                " WHERE idempotensnokkel=%s",
                "DELETE FROM bestilling_idempotens WHERE"
                " idempotensnokkel=%s"]:
        _sett_kontekst(migrator, TENANT)
        params = (("b" * 64, nokkel) if sql.count("%s") == 2 else (nokkel,))
        with pytest.raises(psycopg.errors.CheckViolation):
            migrator.execute(sql, params)
        migrator.rollback()
