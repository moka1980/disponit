"""#162 PR-1: inndata-veien over HTTP — reservasjon + strømmet opplasting.

Ende-til-ende gjennom `klient` (ekte browserøkt, ekte pool,
test_varsel_http-formen), og sannheten måles i BASEN og på DISKEN:
filen finnes, er kryptert (ikke klartekst), og dekrypterer til nøyaktig
bytene som ble sendt — med sha-en raden bærer.
"""
import hashlib
import os
import secrets
import zipfile
from io import BytesIO

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401
from .test_rekruttering_http import _browsersesjon as _sesjon_for
from .test_rekruttering_http import _bruker as _bruker_for

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-rhttp-" + secrets.token_hex(3)   # gjenbruker naboens prefiks-vei


def _zipbytes(n_filer=3) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i in range(n_filer):
            zf.writestr(f"k{i}/soknad.html", f"<p>søker {i}</p>")
    return buf.getvalue()


@pytest.fixture()
def inndata_rot(tmp_path, monkeypatch):
    from api import inndata
    monkeypatch.setattr(inndata, "INNDATA_ROT", str(tmp_path / "inndata"))
    return tmp_path / "inndata"


def _reserver(klient, cookie, csrf):
    from api import sesjon as sesjonmodul
    return klient.post("/v1/inndata/reserver",
                       json={"eiermodul": "m57_ats",
                             "formaal": "soknadsbunt"},
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf})


def _opplast(klient, cookie, csrf, jti, kropp: bytes):
    from api import sesjon as sesjonmodul
    return klient.put(f"/v1/inndata/opplast/{jti}", content=kropp,
                      cookies={sesjonmodul.C_SESJON: cookie},
                      headers={"X-Disponit-CSRF": csrf,
                               "content-type": "application/zip"})


def _rigg(klient):
    # NB: test_rekruttering_http sitt TEN — sesjons-/brukerhjelperne er
    # bundet dit, og denne suiten deler tenantprefiks med vilje.
    from .test_rekruttering_http import TEN as NABOTEN
    bid = _bruker_for("innlaster", ["admin"])
    cookie, csrf = _sesjon_for(bid)
    return NABOTEN, bid, cookie, csrf


@pg
def test_reservasjon_og_opplasting_ende_til_ende(klient, inndata_rot):
    tenant, _bid, cookie, csrf = _rigg(klient)
    r = _reserver(klient, cookie, csrf)
    assert r.status_code == 201, r.text
    jti = r.json()["reservasjon_jti"]
    assert len(jti) >= 32 and r.json()["maks_bytes"] > 0
    ref = r.json()["inndata_ref"]
    assert ref.startswith("inndata:")

    kropp = _zipbytes()
    sha = hashlib.sha256(kropp).hexdigest()
    r2 = _opplast(klient, cookie, csrf, jti, kropp)
    assert r2.status_code == 201, r2.text
    assert r2.json()["innhold_sha256"] == sha
    assert r2.json()["faktiske_bytes"] == len(kropp)
    assert r2.json()["inndata_ref"] == ref

    # Sannheten i BASEN …
    from db.pg import koble, sett_kontekst
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, tenant, "test", "r1")
        rad = m.execute(
            "SELECT status, faktiske_bytes, innhold_sha256, lager_sti,"
            "       key_id, nonce FROM inndata_artefakt"
            " WHERE tenant=%s AND reservasjon_jti=%s",
            (tenant, jti)).fetchone()
        assert rad and rad[0] == "lastet" and rad[1] == len(kropp) \
            and rad[2] == sha
        sti, key_id, nonce = rad[3], rad[4], rad[5]
        # … og på DISKEN: kryptert (aldri klartekst-zip), og dekrypterer
        # til nøyaktig de sendte bytene.
        raa_fil = open(sti, "rb").read()
        assert not raa_fil.startswith(b"PK"), \
            "payloaden ligger i KLARTEKST på disken"
        from db import kryptering
        _kid, dek = kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
        assert kryptering.dekrypter_bytes(
            dek, raa_fil, bytes(nonce), tenant, key_id,
            formaal=b"inndata") == kropp
        m.rollback()
    finally:
        m.close()

    # Engangs-jti, men IKKE engangs-SVAR (Cursor P1-1, 017-formen). En
    # retry med SAMME kropp er den samme forespørselen: gikk 201-et tapt
    # på veien ut, skal klienten kunne hente det igjen. Denne testen
    # låste tidligere inn det motsatte — at bunten var permanent tapt.
    r3 = _opplast(klient, cookie, csrf, jti, kropp)
    assert r3.status_code == 201, r3.text
    assert r3.json() == r2.json(), "replayen ga et ANNET svar enn originalen"
    # …og fortsatt ÉN rad og ÉN fil: replayen skrev en ny ciphertext før
    # 058 fortalte at raden alt fantes, og den orphanen ryddes.
    filer = sorted((inndata_rot / tenant).glob("*.bin"))
    assert len(filer) == 1, f"replayen etterlot {len(filer)} filer: {filer}"
    assert str(filer[0]) == sti, "replayen byttet ut den lagrede filen"

    # En retry med ANNEN kropp er derimot en ekte konflikt — samme skille
    # som `bruk_artefaktkapabilitet` (017) gjør på hash.
    r4 = _opplast(klient, cookie, csrf, jti, _zipbytes(7))
    assert r4.status_code == 409 and r4.json()["feil"] == \
        "inndata_alt_lastet"
    # …og den FØRSTE raden står uendret; et avvist forsøk endrer ingenting.
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, tenant, "test", "r1")
        etter = m.execute(
            "SELECT status, innhold_sha256, lager_sti FROM inndata_artefakt"
            " WHERE tenant=%s AND reservasjon_jti=%s",
            (tenant, jti)).fetchone()
        m.rollback()
    finally:
        m.close()
    assert etter == ("lastet", sha, sti)
    assert len(sorted((inndata_rot / tenant).glob("*.bin"))) == 1


@pg
def test_ukjent_reservasjon_og_tom_kropp(klient, inndata_rot):
    _tenant, _bid, cookie, csrf = _rigg(klient)
    r = _opplast(klient, cookie, csrf, "f" * 48, _zipbytes())
    assert r.status_code == 409 and r.json()["feil"] == \
        "inndata_reservasjon_ugyldig"
    r2 = _reserver(klient, cookie, csrf)
    r3 = _opplast(klient, cookie, csrf, r2.json()["reservasjon_jti"], b"")
    assert r3.status_code == 400


@pg
def test_reservasjonen_krever_kontraktens_kombinasjon(klient, inndata_rot):
    from api import sesjon as sesjonmodul
    _tenant, _bid, cookie, csrf = _rigg(klient)
    for kropp in ({"eiermodul": "m_wcag_audit", "formaal": "soknadsbunt"},
                  {"eiermodul": "m57_ats", "formaal": "noe_annet"},
                  {}):
        r = klient.post("/v1/inndata/reserver", json=kropp,
                        cookies={sesjonmodul.C_SESJON: cookie},
                        headers={"X-Disponit-CSRF": csrf})
        assert r.status_code == 400, kropp


@pg
def test_scopet_gater_reservasjonen(klient, inndata_rot):
    from api import sesjon as sesjonmodul
    bid = _bruker_for("innsyn", ["leser"])
    cookie, csrf = _sesjon_for(bid)
    r = _reserver(klient, cookie, csrf)
    assert r.status_code in (401, 403)


@pg
def test_taket_avviser_for_stor_kropp(klient, inndata_rot, monkeypatch):
    """Kontrakttaket i endepunktet (transport-taket i middleware deler
    tallet): én byte over → 413, og reservasjonen står UBRUKT — et
    avvist forsøk brenner ingenting."""
    from api import app as appmodul
    from api import inndata as inndatamodul
    monkeypatch.setattr(appmodul, "INNDATA_MAKS_FYSISK", 4096)
    tenant, _bid, cookie, csrf = _rigg(klient)
    r = _reserver(klient, cookie, csrf)
    jti = r.json()["reservasjon_jti"]
    r2 = _opplast(klient, cookie, csrf, jti, b"P" * 4097)
    assert r2.status_code == 413, r2.text
    # …og en lovlig kropp går fortsatt på SAMME reservasjon.
    liten = _zipbytes(1)
    assert len(liten) <= 4096
    r3 = _opplast(klient, cookie, csrf, jti, liten)
    assert r3.status_code == 201, r3.text


# --------------------------------------------------------------------------
# Dørene i 058, målt direkte. Fiksrunde 1 lukket funn som HTTP-veien i PR-1
# ikke kan nå (bindingen kalles først i PR-2), og en fiks uten en test som
# feller den gamle oppførselen er en påstand, ikke en port.
# --------------------------------------------------------------------------

MAKS = 64 * 1024 * 1024


def _kontekst(c, tenant):
    """`sett_kontekst` er SET LOCAL og dør med hver commit/rollback — og
    med FORCE RLS på `inndata_artefakt` betyr unset tenant «ingen rader»,
    ikke «feil rader». En test som glemmer dette blir vacuous-grønn, ikke
    rød. Derfor settes den på nytt etter HVER transaksjonsgrense her."""
    from db.pg import sett_kontekst
    sett_kontekst(c, tenant, "test", "r1")
    return c


def _migrator(tenant):
    from db.pg import koble
    return _kontekst(koble(MIGRATOR_DSN), tenant)


def _runtime(tenant):
    """Runtime-tilkoblingen: dørene har EXECUTE til `disponit`, og det er
    NØYAKTIG den rettigheten funnene handler om — en kaller med EXECUTE
    som sender inn sine egne argumenter."""
    from db.pg import koble
    return _kontekst(koble(DSN), tenant)


def _reservasjon(m, tenant, *, utloper="now() + interval '1 hour'",
                 eiermodul="m57_ats"):
    """En reservasjon satt inn DIREKTE av migrator.

    Ikke via `reserver_inndata`: flere av testene under trenger en frist i
    fortiden, og `utloper` er bindingsfelt — `inndata_artefakt_vakt`
    nekter å endre den i ettertid, også for migrator. Vakten er BEFORE
    UPDATE OR DELETE, så innsettingen er den ene lovlige veien til en rad
    som alt er utløpt, uten å skru av en trigger som skal stå på."""
    jti = secrets.token_hex(32)
    m.execute(
        "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
        " innholdstype, maks_bytes, reservasjon_jti, utloper)"
        f" VALUES (%s,%s,'soknadsbunt','application/zip',%s,%s,{utloper})",
        (tenant, eiermodul, MAKS, jti))
    return jti


def _lastet(m, tenant, *, utloper="now() + interval '1 hour'",
            eiermodul="m57_ats"):
    """En ferdig `lastet` bunt — bindingens utgangspunkt. Returnerer
    inndata_id."""
    from db import kryptering
    key_id, _dek = kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
    jti = secrets.token_hex(32)
    return m.execute(
        "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
        " innholdstype, maks_bytes, faktiske_bytes, innhold_sha256,"
        " key_id, nonce, lager_sti, status, reservasjon_jti, utloper,"
        " lastet_ts)"
        " VALUES (%s,%s,'soknadsbunt','application/zip',%s,10,%s,%s,%s,"
        f" %s,'lastet',%s,{utloper},now()) RETURNING inndata_id",
        (tenant, eiermodul, MAKS, "a" * 64, key_id, b"n" * 12,
         f"/tmp/{jti}.bin", jti)).fetchone()[0]


def _oppdrag(m, tenant, eiermodul):
    """Et minimalt beslutningsoppdrag (samme form som
    `test_m57_utsending._grunnlag`): en TILLAT-loggpost hos tenanten er
    alt `oppdrag_koblingsvakt` (038 §5) krever av beslutningsopphavet."""
    from db import kryptering
    logg = m.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,'test','api_token','ih','p@1.0.0/x.y','TILLAT','[]',%s)"
        " RETURNING id", (tenant, secrets.token_hex(8))).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
    ct, nonce = kryptering.krypter(dek, {"inndata": True}, tenant, key_id)
    return int(m.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, beslutning_loggpost_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus)"
        " VALUES ('beslutning',%s,%s,'inndata.test','inndata.test',%s,%s,"
        " %s,%s,now()+interval '1 hour',now()+interval '1 day','KOBLET')"
        " RETURNING id",
        (tenant, logg, eiermodul, ct, key_id, nonce)).fetchone()[0])


@pg
def test_utlopt_reservasjon_avvises_og_brenner_ingenting(klient, inndata_rot):
    """Cursor P2-4: utløpet var KODET i 058, men ubevist. En fiksrunde på
    errcode-mappingen (`InvalidParameterValue` →
    `inndata_reservasjon_ugyldig`) kunne stille gjort utløpet til en 500,
    eller verre, til et 201.

    MUTASJONEN SOM DREPER DENNE: fjern utløpssjekken i
    `registrer_inndata_lastet`, eller la den reise en annen errcode enn
    den API-et oversetter."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        jti = _reservasjon(m, tenant, utloper="now() - interval '1 second'")
        m.commit()
        r = _opplast(klient, cookie, csrf, jti, _zipbytes())
        assert r.status_code == 409, r.text
        assert r.json()["feil"] == "inndata_reservasjon_ugyldig"
        # Reservasjonen står UBRUKT — et avvist forsøk brenner ingenting …
        _kontekst(m, tenant)
        rad = m.execute(
            "SELECT status, lager_sti FROM inndata_artefakt"
            " WHERE tenant=%s AND reservasjon_jti=%s",
            (tenant, jti)).fetchone()
        m.rollback()
    finally:
        m.close()
    assert rad == ("reservert", None)
    # … og ciphertexten API-et rakk å skrive før 058 sa nei, er ryddet.
    assert not sorted((inndata_rot / tenant).glob("*.bin"))


@pg
def test_sql_lukker_eiermodulen_ikke_bare_http(klient):
    """Cursor P2-3: `formaal` var CHECKet, `eiermodul` ikke — og
    `disponit` har EXECUTE på `reserver_inndata`. HTTP-laget lukket settet
    i `reserver_endepunkt`, men en annen kaller går ikke gjennom HTTP.

    MUTASJONEN SOM DREPER DENNE: fjern CHECKen på kolonnen ELLER guarden
    i funksjonen — begge armene måles."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    c = _runtime(tenant)
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT * FROM reserver_inndata(%s,%s,%s,%s)",
                      (tenant, "m_wcag_audit", "soknadsbunt", MAKS))
        c.rollback()
    finally:
        c.close()
    m = _migrator(tenant)
    try:
        with pytest.raises(psycopg.errors.CheckViolation):
            _reservasjon(m, tenant, eiermodul="m_wcag_audit")
        m.rollback()
    finally:
        m.close()


@pg
def test_kryptostrukturen_avvises_uten_a_brenne_jti(klient):
    """Cursor P2-6 (016/017-klassen): et direkte kall kunne lagre en
    avkortet nonce — en verdi AES-GCM aldri kan dekryptere — brenne
    jti-en og lande raden som `lastet`. Resolveren i PR-2 ville da funnet
    permanent uleselig evidens.

    MUTASJONEN SOM DREPER DENNE: fjern `octet_length(p_nonce) <> 12` fra
    funksjonsguarden eller `inndata_krypto_struktur` fra tabellen."""
    from db import kryptering
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        jti = _reservasjon(m, tenant)
        key_id, _dek = kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
        m.commit()
    finally:
        m.close()
    c = _runtime(tenant)
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute(
                "SELECT * FROM registrer_inndata_lastet(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, jti, 10, "a" * 64, key_id, b"\x01", "/tmp/x.bin"))
        c.rollback()
    finally:
        c.close()
    m = _migrator(tenant)
    try:
        assert m.execute("SELECT status FROM inndata_artefakt WHERE"
                         " tenant=%s AND reservasjon_jti=%s",
                         (tenant, jti)).fetchone()[0] == "reservert"
        # Tabellen bærer den samme invarianten, uansett skrivevei.
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute("INSERT INTO inndata_artefakt (tenant, eiermodul,"
                      " formaal, innholdstype, maks_bytes, reservasjon_jti,"
                      " utloper, nonce) VALUES (%s,'m57_ats','soknadsbunt',"
                      "'application/zip',%s,%s,now()+interval '1 h',%s)",
                      (tenant, MAKS, secrets.token_hex(32), b"\x01"))
        m.rollback()
    finally:
        m.close()


@pg
def test_dek_referansen_er_bundet_til_tenantens_nokler(klient):
    """Codex P2: `key_id` kommer fra en runtime-kaller. Uten den
    sammensatte FK-en (003/005/007/011/016-formen) kunne en `lastet` rad
    lande med en ukjent eller krysstenant nøkkel-id — komplett på
    overflaten, udekrypterbar i praksis.

    MUTASJONEN SOM DREPER DENNE: fjern `inndata_dek_fk`."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            m.execute(
                "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
                " innholdstype, maks_bytes, faktiske_bytes, innhold_sha256,"
                " key_id, nonce, lager_sti, status, reservasjon_jti,"
                " utloper, lastet_ts) VALUES (%s,'m57_ats','soknadsbunt',"
                "'application/zip',%s,10,%s,'finnes-ikke',%s,'/tmp/x.bin',"
                "'lastet',%s,now()+interval '1 h',now())",
                (tenant, MAKS, "a" * 64, b"n" * 12, secrets.token_hex(32)))
        m.rollback()
    finally:
        m.close()


@pg
def test_bindingen_avleder_eiermodulen_fra_oppdraget(klient):
    """Codex P1: `bind_inndata` sammenlignet bunten mot `p_eiermodul` —
    kallerens EGET argument. En kaller med EXECUTE som kjente buntens
    eiermodul kunne ekko-e den tilbake og samtidig peke på et hvilket som
    helst oppdrag i egen tenant.

    Angrepet spilles av ordrett: riktig `p_eiermodul`, FEIL oppdrag.

    MUTASJONEN SOM DREPER DENNE: sammenlign igjen bare `r.eiermodul` mot
    `p_eiermodul` og slutt å slå opp `oppdrag.eiermodul`."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        bunt = _lastet(m, tenant)
        fremmed = _oppdrag(m, tenant, "m_wcag_audit")
        eget = _oppdrag(m, tenant, "m57_ats")
        m.commit()
    finally:
        m.close()
    c = _runtime(tenant)
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT bind_inndata(%s,%s,%s,%s)",
                      (tenant, bunt, fremmed, "m57_ats"))
        c.rollback()
        # …og den ekte kombinasjonen går fortsatt igjennom.
        _kontekst(c, tenant)
        c.execute("SELECT bind_inndata(%s,%s,%s,%s)",
                  (tenant, bunt, eget, "m57_ats"))
        c.commit()
    finally:
        c.close()
    m = _migrator(tenant)
    try:
        assert m.execute("SELECT status, oppdrag_id FROM inndata_artefakt"
                         " WHERE tenant=%s AND inndata_id=%s",
                         (tenant, bunt)).fetchone() == ("bundet", eget)
        m.rollback()
    finally:
        m.close()


@pg
def test_en_bunt_ett_oppdrag(klient):
    """Cursor P1-3: kommentaren i 058 lovte 1:1, men indeksen var ikke
    UNIQUE. To `lastet`-rader kunne bindes til samme oppdrag, og lineage
    forgrenet seg — to bunter bak én bestilling, uten at noe sier hvilken
    som gjelder.

    MUTASJONEN SOM DREPER DENNE: gjør `inndata_artefakt_oppdrag`
    ikke-unik igjen."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        en, to = _lastet(m, tenant), _lastet(m, tenant)
        opp = _oppdrag(m, tenant, "m57_ats")
        m.commit()
    finally:
        m.close()
    c = _runtime(tenant)
    try:
        c.execute("SELECT bind_inndata(%s,%s,%s,%s)",
                  (tenant, en, opp, "m57_ats"))
        c.commit()
        _kontekst(c, tenant)
        with pytest.raises(psycopg.errors.UniqueViolation):
            c.execute("SELECT bind_inndata(%s,%s,%s,%s)",
                      (tenant, to, opp, "m57_ats"))
        c.rollback()
    finally:
        c.close()


@pg
def test_bindingen_avviser_utlopt_bunt(klient):
    """Cursor P2-2: `registrer_inndata_lastet` sjekket utløp,
    `bind_inndata` ikke — og `inndata_artefakt_utlop` lover at en `lastet`
    bunt løper ut. En utgått bunt kunne dermed bindes for alltid, helt til
    reaperen (egen PR) fantes.

    MUTASJONEN SOM DREPER DENNE: fjern utløpssjekken i `bind_inndata`."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        bunt = _lastet(m, tenant, utloper="now() - interval '1 second'")
        opp = _oppdrag(m, tenant, "m57_ats")
        m.commit()
    finally:
        m.close()
    c = _runtime(tenant)
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT bind_inndata(%s,%s,%s,%s)",
                      (tenant, bunt, opp, "m57_ats"))
        c.rollback()
    finally:
        c.close()


@pg
def test_bundet_krever_krypto_og_lastet_ts(klient):
    """Cursor P2-1: `inndata_tilstand_totalt` het «totalt», men
    `bundet`-grenen krevde verken `key_id`, `nonce` eller `lastet_ts`. En
    `bundet` rad er en `lastet` som har fått et oppdrag; den mister ikke
    krypto på veien.

    MUTASJONEN SOM DREPER DENNE: ta de tre NOT NULL-ene ut av
    `bundet`-grenen igjen."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        opp = _oppdrag(m, tenant, "m57_ats")
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute(
                "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
                " innholdstype, maks_bytes, faktiske_bytes, innhold_sha256,"
                " lager_sti, status, reservasjon_jti, oppdrag_id, utloper,"
                " bundet_ts) VALUES (%s,'m57_ats','soknadsbunt',"
                "'application/zip',%s,10,%s,'/tmp/x.bin','bundet',%s,%s,"
                " now()+interval '1 h',now())",
                (tenant, MAKS, "a" * 64, secrets.token_hex(32), opp))
        m.rollback()
    finally:
        m.close()
