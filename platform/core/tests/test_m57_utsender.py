"""M-57-utsenderen (081): de signerte listene blir e-post.

Formen er varselsender-testenes: injisert `send` måler HVA som ville
gått ut, kvitteringstabellen er tilstanden, og dommene måles negative
først — usignert sendes aldri, et dødt klaim blir `uviss` og resendes
aldri, runtime når ikke dørene.
"""
import secrets
import uuid

import psycopg
import pytest

from drift import m57_utsender

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401
from .test_m37 import _sett_kontekst
from .test_m57_utsending import (TENANT, _prosess_med_kandidater, _sender,
                                 _signatar, _signer, pg)


def _utsendingsdata(m, pid, kids):
    """Adressene og flettefeltene bak manifestmedlemmene (057-lageret;
    vakten utleder sha selv)."""
    _sett_kontekst(m, TENANT)
    adresser = []
    for i, kid in enumerate(kids):
        adresse = f"kandidat-{i}-{secrets.token_hex(3)}@eksempel.invalid"
        adresser.append(adresse)
        m.execute(
            "INSERT INTO kandidat_utsendingsdata (tenant, prosess_id,"
            " kandidat_id, mottaker_ref, flettefelt, innhold_sha256)"
            " VALUES (%s,%s,%s,%s,%s,%s)",
            (TENANT, pid, kid, adresse,
             __import__("json").dumps(
                 {"kandidatnavn": "[NAVN-1]", "stilling": "Demo-stilling",
                  "tidsvalg_lenke": f"https://tid.example.invalid/{kid}"}),
             "0" * 64))
    m.commit()
    return adresser


def _signert_liste(m, kids, oid):
    """Manifestliste gjennom døren + signatur — kjedens lovlige vei."""
    _sett_kontekst(m, TENANT)
    m.execute("SET ROLE disponit_m37_claimer")
    serie = uuid.uuid4()
    lid, ihash = m.execute(
        "SELECT * FROM opprett_utsendingsliste(%s,%s,NULL,%s,"
        "'invitasjon','invitasjon-v1',%s::uuid[])",
        (TENANT, serie, oid, kids)).fetchone()
    m.execute("RESET ROLE")
    m.commit()
    bid = _signatar(m)
    _signer(m, (lid, serie, ihash), bid)
    return lid


@pg
def test_utsenderen_sender_signert_liste_en_gang(migrator, monkeypatch):
    """Ende-til-ende: manifest + signatur + utsendingsdata → e-post per
    medlem, kvittering `sendt`, frigivelsen pseudonym — og en ny kjøring
    sender INGENTING (idempotensen bor i transporten)."""
    # M-8 (082): invitasjoner sendes bare når tidsvalg-lenken
    # kan myntes (pepper + host) — riggen speiler driften.
    monkeypatch.setenv("DISPONIT_HOST", "kunde.example")
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 2)
    adresser = _utsendingsdata(migrator, pid, kids)
    lid = _signert_liste(migrator, kids, oid)
    sendte = []
    snd = _sender()
    try:
        res = m57_utsender.kjor(
            snd, send=lambda til, emne, tekst: sendte.append(
                (til, emne, tekst)))
    finally:
        snd.close()
    assert res["sendt"] == 2, res
    assert sorted(t for t, _e, _x in sendte) == sorted(adresser)
    for _til, emne, tekst in sendte:
        assert "vi inviterer deg til intervju" in tekst
        assert "Demo-stilling" in emne
        # Firmateksten er None («ingen tone») til kunden kobler en.
        assert "{firmatekst}" not in tekst
    _sett_kontekst(migrator, TENANT)
    rader = migrator.execute(
        "SELECT k.status, k.frigivelse_id, f.mottaker_ref"
        "  FROM m57_utsendingskvittering k"
        "  JOIN utsendingsfrigivelse f"
        "    ON f.tenant = k.tenant AND f.frigivelse_id = k.frigivelse_id"
        " WHERE k.tenant=%s AND k.liste_id=%s", (TENANT, lid)).fetchall()
    migrator.rollback()
    assert len(rader) == 2
    for status, fid, psn in rader:
        assert status == "sendt" and fid is not None
        assert psn.startswith("psn-"), "frigivelsen bærer klartekst!"
    # Idempotensen: kjøring nummer to har ingenting å gjøre.
    snd = _sender()
    try:
        res2 = m57_utsender.kjor(
            snd, send=lambda *a: (_ for _ in ()).throw(
                AssertionError("resend!")))
    finally:
        snd.close()
    assert res2["sendt"] == 0 and res2["feilet"] == 0


@pg
def test_usignert_liste_sendes_aldri(migrator):
    """Port 7-speilet på senderbenen: uten signatur finnes ingen
    sendeklar rad — listen ligger uåpnet."""
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    _utsendingsdata(migrator, pid, kids)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    migrator.execute(
        "SELECT * FROM opprett_utsendingsliste(%s,%s,NULL,%s,"
        "'invitasjon','invitasjon-v1',%s::uuid[])",
        (TENANT, uuid.uuid4(), oid, kids))
    migrator.execute("RESET ROLE")
    migrator.commit()
    snd = _sender()
    try:
        res = m57_utsender.kjor(
            snd, send=lambda *a: (_ for _ in ()).throw(
                AssertionError("usignert sendt!")))
    finally:
        snd.close()
    assert res["sendt"] == 0


@pg
def test_uten_smtp_oppsett_roeres_ingenting(migrator, monkeypatch):
    """Manglende oppsett er en DRIFTSTILSTAND: ingen klaim, ingen
    forsøksteller, exit-vennlig stans (varselsender-dommen)."""
    for k in ("VERT", "PORT", "BRUKER", "PASSORD", "AVSENDER"):
        monkeypatch.delenv(f"DISPONIT_SMTP_{k}", raising=False)
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    _utsendingsdata(migrator, pid, kids)
    lid = _signert_liste(migrator, kids, oid)
    snd = _sender()
    try:
        res = m57_utsender.kjor(snd)
    finally:
        snd.close()
    assert res["stanset"] == "smtp_ikke_konfigurert"
    _sett_kontekst(migrator, TENANT)
    antall = migrator.execute(
        "SELECT count(*) FROM m57_utsendingskvittering WHERE tenant=%s"
        " AND liste_id=%s", (TENANT, lid)).fetchone()[0]
    migrator.rollback()
    assert antall == 0, "køen ble rørt uten SMTP-oppsett"


@pg
def test_en_feilende_adresse_stopper_ikke_resten(migrator, monkeypatch):
    """En avvist sending er data på raden: resten går ut, den feilede
    står `feilet` og plukkes igjen av NESTE kjøring."""
    # M-8 (082): invitasjoner sendes bare når tidsvalg-lenken
    # kan myntes (pepper + host) — riggen speiler driften.
    monkeypatch.setenv("DISPONIT_HOST", "kunde.example")
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 2)
    adresser = _utsendingsdata(migrator, pid, kids)
    _signert_liste(migrator, kids, oid)
    doed = adresser[0]

    def send(til, _emne, _tekst):
        if til == doed:
            # En FØR-AKSEPT-klasse: mottakeren ble avvist — beviselig
            # ingen e-post ute, trygt å prøve igjen.
            import smtplib
            raise smtplib.SMTPRecipientsRefused({til: (550, b"nei")})

    snd = _sender()
    try:
        res = m57_utsender.kjor(snd, send=send)
        assert res["sendt"] == 1 and res["feilet"] == 1
        # Neste kjøring: bare den feilede er sendeklar, og nå går den.
        res2 = m57_utsender.kjor(snd, send=lambda *a: None)
    finally:
        snd.close()
    assert res2["sendt"] == 1 and res2["feilet"] == 0


@pg
def test_doedt_klaim_blir_uviss_og_resendes_aldri(migrator):
    """056-funn 2: krasjer senderen mellom SMTP-aksept og kvittering,
    er utfallet UVISST — raden merkes terminalt og går aldri tilbake i
    køen. Dobbeltsending av en irreversibel utsendelse finnes ikke."""
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    _utsendingsdata(migrator, pid, kids)
    lid = _signert_liste(migrator, kids, oid)
    # Klaim uten kvittering — krasjet, simulert gjennom senderens egen
    # dør (aldri direkte DML mot tilstanden).
    snd = _sender()
    try:
        _sett_kontekst(snd, TENANT)
        rad = snd.execute(
            "SELECT ut_frigivelse FROM m57_start_sending(%s,%s,%s,%s,3)",
            (TENANT, lid, kids[0], uuid.uuid4())).fetchone()
        assert rad is not None, "klaimet feilet"
        snd.commit()
    finally:
        snd.close()
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE m57_utsendingskvittering SET oppdatert ="
        " now() - interval '2 hours' WHERE tenant=%s AND liste_id=%s",
        (TENANT, lid))
    migrator.commit()
    snd = _sender()
    try:
        res = m57_utsender.kjor(
            snd, send=lambda *a: (_ for _ in ()).throw(
                AssertionError("uviss resendt!")))
    finally:
        snd.close()
    assert res["uviss_merket"] == 1 and res["sendt"] == 0
    _sett_kontekst(migrator, TENANT)
    status = migrator.execute(
        "SELECT status FROM m57_utsendingskvittering WHERE tenant=%s"
        " AND liste_id=%s", (TENANT, lid)).fetchone()[0]
    migrator.rollback()
    assert status == "uviss"


@pg
def test_udefinerbar_sendefeil_kvitteres_aldri(migrator, monkeypatch):
    """CodeRabbit på 081: en feil som IKKE beviser «før aksept» (timeout
    midt i dialogen) kvitteres ikke — klaimet står, lease-utløpet feller
    `uviss`-dommen, og raden resendes aldri automatisk."""
    # M-8 (082): invitasjoner sendes bare når tidsvalg-lenken
    # kan myntes (pepper + host) — riggen speiler driften.
    monkeypatch.setenv("DISPONIT_HOST", "kunde.example")
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    _utsendingsdata(migrator, pid, kids)
    lid = _signert_liste(migrator, kids, oid)

    def send(*_a):
        raise TimeoutError("SMTP-dialogen døde etter DATA")

    snd = _sender()
    try:
        res = m57_utsender.kjor(snd, send=send)
    finally:
        snd.close()
    assert res["sendt"] == 0 and res["feilet"] == 0
    assert res.get("uviss_underveis") == 1
    _sett_kontekst(migrator, TENANT)
    status, klaim = migrator.execute(
        "SELECT status, klaim FROM m57_utsendingskvittering"
        " WHERE tenant=%s AND liste_id=%s", (TENANT, lid)).fetchone()
    migrator.rollback()
    assert status == "under_sending" and klaim is not None,         "usikkerheten ble kvittert bort — dobbeltsending mulig"


@pg
def test_runtime_naar_ikke_senderdorene(migrator):
    """Rollegrensen (027-formen): web-API-et har verken sendeklar-
    lesingen, klaimet eller uviss-merkingen — senderrollen alene."""
    from db.pg import koble
    rt = koble(DSN)
    try:
        _sett_kontekst(rt, TENANT)
        for kall, args in (
                ("SELECT * FROM m57_neste_sendinger(%s, 1, 3)", (TENANT,)),
                ("SELECT * FROM m57_start_sending(%s,%s,%s,%s,3)",
                 (TENANT, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())),
                ("SELECT m57_fullfor_sending(%s,%s,%s,%s,'sendt',NULL)",
                 (TENANT, uuid.uuid4(), uuid.uuid4(), uuid.uuid4())),
                ("SELECT m57_sendeklare_tenanter(1, 3)", ()),
                ("SELECT m57_merk_uviss(interval '30 minutes')", ())):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                rt.execute(kall, args)
            rt.rollback()
            _sett_kontekst(rt, TENANT)
    finally:
        rt.close()


@pg
def test_firmateksten_baerer_tonen_ut_i_eposten(migrator, monkeypatch):
    """083/#160 siste ledd: listen bærer kundens firmatekst-referanse,
    signaturen dekker den (hashen endres med tonen), og e-posten
    flettes med den eksakte versjonen. Skjult tekst kan aldri
    innstilles; versjonen pinnes av døren."""
    import uuid as uuidmod
    monkeypatch.setenv("DISPONIT_HOST", "kunde.example")
    oid, pid, kids, _ = _prosess_med_kandidater(migrator, 1)
    _utsendingsdata(migrator, pid, kids)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_domene_eier")
    tid, v1 = migrator.execute(
        "SELECT ut_tekst_id, ut_versjon FROM"
        " opprett_utsendingstekst_versjon(%s,NULL,'Tonen',"
        "'Med vennlig hilsen Demo AS','forf',%s)",
        (TENANT, "t-" + secrets.token_hex(4))).fetchone()
    migrator.execute("RESET ROLE")
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    # Uten tone og med tone gir ULIK hash — signaturen dekker tonen.
    _lid0, h0 = migrator.execute(
        "SELECT * FROM opprett_utsendingsliste(%s,%s,NULL,%s,"
        "'invitasjon','invitasjon-v1',%s::uuid[])",
        (TENANT, uuidmod.uuid4(), oid, kids)).fetchone()
    lid, h1 = migrator.execute(
        "SELECT * FROM opprett_utsendingsliste(%s,%s,NULL,%s,"
        "'invitasjon','invitasjon-v1',%s::uuid[],%s,NULL)",
        (TENANT, uuidmod.uuid4(), oid, kids, tid)).fetchone()
    assert h0 != h1, "hashen dekker ikke tonen"
    rad = migrator.execute(
        "SELECT firmatekst_ref, firmatekst_versjon FROM utsendingsliste"
        " WHERE tenant=%s AND liste_id=%s", (TENANT, lid)).fetchone()
    assert str(rad[0]) == str(tid) and rad[1] == v1, \
        "versjonen ble ikke pinnet av døren"
    migrator.execute("RESET ROLE")
    migrator.commit()
    bid = _signatar(migrator)
    _sett_kontekst(migrator, TENANT)
    serie = migrator.execute(
        "SELECT utkast_serie FROM utsendingsliste WHERE tenant=%s"
        " AND liste_id=%s", (TENANT, lid)).fetchone()[0]
    migrator.rollback()
    _signer(migrator, (lid, serie, h1), bid)
    sendte = []
    snd = _sender()
    try:
        res = m57_utsender.kjor(
            snd, send=lambda til, emne, tekst: sendte.append(tekst))
    finally:
        snd.close()
    # Den usignerte tonefrie listen sendes ikke; den signerte bærer tonen.
    assert res["sendt"] == 1, res
    assert "Med vennlig hilsen Demo AS" in sendte[0], \
        "kundens tone nådde aldri e-posten"
    # Skjult tekst kan aldri innstilles.
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_domene_eier")
    migrator.execute("SELECT skjul_utsendingstekst(%s,%s)", (TENANT, tid))
    migrator.execute("RESET ROLE")
    _sett_kontekst(migrator, TENANT)
    migrator.execute("SET ROLE disponit_m37_claimer")
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        migrator.execute(
            "SELECT * FROM opprett_utsendingsliste(%s,%s,NULL,%s,"
            "'invitasjon','invitasjon-v1',%s::uuid[],%s,NULL)",
            (TENANT, uuidmod.uuid4(), oid, kids, tid))
    migrator.rollback()
