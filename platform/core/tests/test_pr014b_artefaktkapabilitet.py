"""PR-014b CP5: artefakt-opplastingskapabilitet (§7).

Egen kapabilitet med eget scope, bundet til det claimede oppdragets kontrakt +
epoch. Kryssbruk mot kvitteringskapabiliteten avvises STRUKTURELT (egen tabell,
egne funksjoner). Utstedes kun for et plukket oppdrag med matchende binding;
innløses kun av den holdende modulen; forbrukes atomisk og idempotent.
"""
import secrets

import psycopg
import pytest

from .test_api import DSN, MIGRATOR_DSN, TENANT, migrator, miljo  # noqa: F401
from .test_m37 import _lag_sak, _lag_oppdrag, _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _admin():
    """runtime-tilkobling (funksjonene har EXECUTE til disponit)."""
    from db.pg import koble
    return koble(DSN)


def _mk_admin(rolle):
    """migrator SET ROLE <rolle>, committed (varig på tvers av rollback)."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute(f"SET ROLE {rolle}")
    c.commit()
    return c


def _plukket_oppdrag_med_binding(conn, modul, kh):
    """Et LEGITIMT claimet, kontraktbundet oppdrag: registrer kontrakt+release,
    aktiver modulen med en claiming-deployment, registrer oppdragstype+
    artefakttype, og claim via den herdede claim-veien (014a-final tillater kun
    claim-funksjonen å stemple bindingen). Returnerer (oppdrag_id, artefakttype)."""
    from .test_pr014a_cp5_claim import _lag_oppdrag_type
    ma = _mk_admin("disponit_modules_admin")
    try:
        ma.execute("SELECT registrer_kontrakt(%s,1,%s,'p','k','krever_outbox',"
                   "'kompenserende','sys')", (modul, kh))
        ma.execute("SELECT registrer_release(%s,'r1',1,%s,'mh','ad','sys')",
                   (modul, kh))
        ma.execute("SELECT installer_modul(%s,'sys')", (modul,))
        ma.execute("SELECT sett_modulstatus(%s,'staging_verifisert',NULL,'sys')",
                   (modul,))
        ma.execute("SELECT bytt_release(%s,'staging','r1',1,%s,'sys')", (modul, kh))
        ma.execute("SELECT sett_modulstatus(%s,'aktiv','r1','sys')", (modul,))
        ot = "cp5b-" + secrets.token_hex(4)
        ma.execute("SELECT registrer_oppdragstype(%s,%s,1,%s,'sys')", (ot, modul, kh))
        ma.commit()
    finally:
        ma.close()
    at = f"at.t{secrets.token_hex(4)}.kvittering"
    da = _mk_admin("disponit_domains_admin")
    try:
        da.execute("SELECT registrer_artefaktskjema("
                   "'{\"type\":\"object\"}',%s,'sys')",
                   ("a2c799262a3ce3c19ef5cdd983bf3d12b43ab3c426227091b909"
                    "dcb7054738c0",))
        da.execute("SELECT registrer_artefakttype(%s,%s,1,%s,"
                   "'a2c799262a3ce3c19ef5cdd983bf3d12b43ab3c426227091b909"
                   "dcb7054738c0','sys')",
                   (at, modul, kh))
        da.commit()
    finally:
        da.close()
    sak, logg = _lag_sak(conn, TENANT)
    opp, _ = _lag_oppdrag_type(conn, TENANT, sak, logg, oppdragstype=ot,
                               eiermodul=modul)
    from db.pg import koble
    c = koble(DSN)
    try:
        c.execute("SELECT set_config('disponit.aktor','m37',true),"
                  "       set_config('disponit.request_id','r',true)")
        rad = c.execute("SELECT id FROM claim_neste_oppdrag(%s,%s,%s,300,'r1',"
                        "'staging',0)",
                        (modul, ["purring."], secrets.token_hex(16))).fetchone()
        c.commit()
    finally:
        c.close()
    assert rad is not None and rad[0] == opp, "oppdraget ble ikke claimet"
    return opp, at


def _utsted(conn, opp, modul, kh, at, jti=None):
    jti = jti or secrets.token_hex(16)
    conn.execute(
        "SELECT jti FROM utsted_artefaktkapabilitet(%s,%s,%s,'r1',1,%s,0,%s,%s,900)",
        (TENANT, opp, modul, kh, at, jti))
    conn.commit()
    return jti


@pg
def test_utsted_krever_plukket_oppdrag_med_binding(migrator):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        assert len(jti) >= 32
        # feil kontrakt-hash → avvist (binding matcher ikke oppdraget).
        with pytest.raises(psycopg.errors.Error):
            a.execute("SELECT utsted_artefaktkapabilitet(%s,%s,%s,'r1',1,'feil',0,"
                      "%s,%s,900)", (TENANT, opp, modul, at, secrets.token_hex(16)))
        a.rollback()
    finally:
        a.close()


@pg
def test_innlos_kun_holdende_modul(migrator):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        rad = a.execute("SELECT tenant, oppdrag_id, release_id, artefakttype"
                        " FROM innlos_artefaktkapabilitet(%s,%s)",
                        (jti, modul)).fetchone()
        a.commit()
        assert rad == (TENANT, opp, "r1", at)
        # feil modul → ingen rad.
        assert a.execute("SELECT count(*) FROM innlos_artefaktkapabilitet(%s,%s)",
                         (jti, "annen-modul")).fetchone()[0] == 0
        a.commit()
    finally:
        a.close()


@pg
def test_frittstaende_brenner_finnes_ikke(migrator):
    """Codex: den foreldede brenneren er FJERNET, ikke bare ubrukt.

    `bruk_artefaktkapabilitet(jti, uuid)` tok bare en jti og en vilkårlig UUID —
    verken holdende modul eller at artefaktet fantes ble verifisert. Med EXECUTE
    til runtime kunne en misbrukt tilkobling merke en LEVENDE kapabilitet `brukt`
    med et oppdiktet artefakt-id, hvorpå staged-writen leste den statusen som
    autoritativ og den legitime opplastingen aldri kom inn. Forbruk skjer nå kun
    der artefaktraden faktisk skrives.

    MUTASJONEN SOM DREPER DENNE: gjeninnfør funksjonen (eller GRANT-en).
    """
    n = migrator.execute(
        "SELECT count(*) FROM pg_proc WHERE proname='bruk_artefaktkapabilitet'"
    ).fetchone()[0]
    migrator.rollback()
    assert n == 0, "den frittstående kapabilitetsbrenneren finnes fortsatt"


@pg
def test_atomisk_forbruk_avviser_utlopt_kapabilitet(migrator):
    """Utløpet håndheves der kapabiliteten faktisk brennes (017:145).

    En request kan passere `innlos` rett før utløp og deretter bruke tid på
    kanonisering og kryptering. Etter at den frittstående brenneren er fjernet
    er `lagre_artefakt_staged` det eneste forbrukspunktet, og det er DER — under
    radlåsen — utløpet må nektes.
    """
    from db import kryptering
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        _sett_kontekst(a, TENANT)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(a, TENANT)
        ct, nonce = kryptering.krypter(dek, {"r": 1}, TENANT, key_id)
        a.commit()
        # Aldre kapabiliteten forbi utløp. `utloper` er frosset av statusmaskinen,
        # så triggeren skrus av for nøyaktig denne testmutasjonen.
        migrator.execute("ALTER TABLE artefaktkapabilitet DISABLE TRIGGER"
                         " artefaktkapabilitet_overgang")
        migrator.execute("UPDATE artefaktkapabilitet SET utloper=now()"
                         " - interval '1 s' WHERE jti=%s", (jti,))
        migrator.execute("ALTER TABLE artefaktkapabilitet ENABLE TRIGGER"
                         " artefaktkapabilitet_overgang")
        migrator.commit()
        _sett_kontekst(a, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT lagre_artefakt_staged(%s,%s,%s,%s,'r1',1,%s,0,100,"
                      "%s,%s,%s,%s,%s)",
                      (TENANT, opp, at, modul, kh, "h-" + secrets.token_hex(8),
                       ct, nonce, key_id, jti))
        a.rollback()
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM artefakt WHERE kapabilitet_jti=%s",
                         (jti,)).fetchone()[0]
    migrator.rollback()
    assert n == 0, "en utløpt kapabilitet fikk skrive et artefakt"


@pg
def test_kryssbruk_mot_kvitteringskapabilitet_avvist(migrator):
    # Strukturelt: en artefakt-jti finnes ikke i kvitteringskapabiliteter-tabellen,
    # så innlos_kvitteringskapabilitet returnerer ingenting (og omvendt).
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        n = a.execute("SELECT count(*) FROM innlos_kvitteringskapabilitet(%s,%s)",
                      (jti, modul)).fetchone()[0]
        a.commit()
        assert n == 0, "artefakt-kapabilitet ble innløst som kvitteringskapabilitet"
    finally:
        a.close()


@pg
def test_utsted_krever_registrert_release(migrator):
    """Codex P2: release_id verifiseres mot modulrelease. Oppdraget stempler ikke
    release ved claim, så uten porten kunne en kapabilitet tilskrives en vilkårlig
    release som promoteringen senere leser tilbake fra artefaktet.

    MUTASJONEN SOM DREPER DENNE: fjern modulrelease-sjekken i utsted."""
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)  # registrerer 'r1'
    a = _admin()
    try:
        # 'r1' er registrert → OK.
        _utsted(a, opp, modul, kh, at)
        # 'r-bogus' er IKKE en registrert release → avvist.
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT utsted_artefaktkapabilitet(%s,%s,%s,'r-bogus',1,%s,0,"
                      "%s,%s,900)",
                      (TENANT, opp, modul, kh, at, secrets.token_hex(16)))
        a.rollback()
    finally:
        a.close()


@pg
def test_innlos_og_lagre_idempotent_etter_utlop(migrator):
    """Codex P2: en ALLEREDE `brukt` kapabilitet er innløsbar + lagre returnerer
    samme artefakt_id UANSETT utløp. Mister controlleren svaret og retryer etter
    de 15 minuttene, må flyten fortsatt kunne fullføres idempotent.

    MUTASJONEN SOM DREPER DENNE: fjern `status = 'brukt' OR` fra innlos-filteret."""
    from db import kryptering
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        _sett_kontekst(a, TENANT)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(a, TENANT)
        ct, nonce = kryptering.krypter(dek, {"r": 1}, TENANT, key_id)
        h = "h-" + secrets.token_hex(8)
        aid1 = a.execute("SELECT lagre_artefakt_staged(%s,%s,%s,%s,'r1',1,%s,0,100,"
                         "%s,%s,%s,%s,%s)",
                         (TENANT, opp, at, modul, kh, h, ct, nonce, key_id, jti)
                         ).fetchone()[0]
        a.commit()
        # Kapabiliteten er nå 'brukt'. Aldre den forbi utløp (utloper er frosset).
        migrator.execute("ALTER TABLE artefaktkapabilitet DISABLE TRIGGER"
                         " artefaktkapabilitet_overgang")
        migrator.execute("UPDATE artefaktkapabilitet SET utloper=now()-interval"
                         " '1 s' WHERE jti=%s", (jti,))
        migrator.execute("ALTER TABLE artefaktkapabilitet ENABLE TRIGGER"
                         " artefaktkapabilitet_overgang")
        migrator.commit()
        # innlos returnerer FORTSATT bindingen (brukt overstyrer utløp).
        _sett_kontekst(a, TENANT)
        rad = a.execute("SELECT tenant FROM innlos_artefaktkapabilitet(%s,%s)",
                        (jti, modul)).fetchone()
        a.commit()
        assert rad is not None, "en brukt kapabilitet ble ikke innløsbar etter utløp"
        # lagre returnerer SAMME artefakt_id idempotent, tross utløp.
        _sett_kontekst(a, TENANT)
        aid2 = a.execute("SELECT lagre_artefakt_staged(%s,%s,%s,%s,'r1',1,%s,0,100,"
                         "%s,%s,%s,%s,%s)",
                         (TENANT, opp, at, modul, kh, h, ct, nonce, key_id, jti)
                         ).fetchone()[0]
        a.commit()
        assert aid2 == aid1, "retry etter utløp ga ikke samme artefakt_id"
    finally:
        a.close()


@pg
def test_lagre_avviser_null_payload(migrator):
    """Codex P2: et `staged` artefakt MÅ ha ciphertext+nonce (nullbar KUN ved
    forkastet). En kaller med EXECUTE kunne ellers sende NULL og få en tom rad
    inn som staged og senere promotert. Håndheves FØR kapabiliteten brennes.

    MUTASJONEN SOM DREPER DENNE: fjern non-null-sjekken i lagre_artefakt_staged."""
    from db import kryptering
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        _sett_kontekst(a, TENANT)
        key_id, _ = kryptering.hent_eller_opprett_aktiv_dek(a, TENANT)
        a.commit()
        _sett_kontekst(a, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT lagre_artefakt_staged(%s,%s,%s,%s,'r1',1,%s,0,100,"
                      "%s,NULL,NULL,%s,%s)",
                      (TENANT, opp, at, modul, kh, "h-" + secrets.token_hex(8),
                       key_id, jti))
        a.rollback()
    finally:
        a.close()
    # Ingen rad skrevet, og kapabiliteten IKKE brent (sjekken kom før brenningen).
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM artefakt WHERE kapabilitet_jti=%s",
                         (jti,)).fetchone()[0]
    st = migrator.execute("SELECT status FROM artefaktkapabilitet WHERE jti=%s",
                          (jti,)).fetchone()[0]
    migrator.rollback()
    assert n == 0 and st == "utstedt", \
        "null-payload ble akseptert eller brente kapabiliteten"


@pg
@pytest.mark.parametrize("ct,nonce,hvorfor", [
    (b"", b"", "tom ciphertext + tom nonce"),
    (b"\x00" * 16, b"\x00" * 12, "ciphertext = KUN tag, ingen chiffertekst"),
    (b"\x00" * 64, b"", "tom nonce"),
    (b"\x00" * 64, b"\x00" * 11, "avkortet nonce"),
    (b"\x00" * 64, b"\x00" * 13, "for lang nonce"),
])
def test_lagre_avviser_strukturelt_ugyldig_payload(migrator, ct, nonce, hvorfor):
    """Codex P2: NULL-sjekken alene var for svak. En runtime-tilkobling med en
    GYLDIG kapabilitet kunne sende `'\\x'` (eller en avkortet nonce) — verdier
    AES-GCM aldri kan dekryptere, fordi autentiseringstaggen mangler og noncen
    er ugyldig — og likevel brenne kapabiliteten og lande som `staged`.
    Promoteringen ser bare bindinger og den PÅSTÅTTE klartekst-hashen, aldri
    nyttelasten, så raden kunne blitt permanent evidens uten gjenopprettbart
    innhold. Invariantene kommer fra db/kryptering.py: 12-byte nonce,
    ct||16-byte-tag over en klartekst som minst er `{}`.

    MUTASJONEN SOM DREPER DENNE: fjern lengdesjekken i lagre_artefakt_staged
    (tabellens `artefakt_payload_struktur` fanger den fortsatt, men da som
    check_violation ETTER at kapabiliteten er forsøkt brent — feil feilkontrakt)."""
    from db import kryptering
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        _sett_kontekst(a, TENANT)
        key_id, _ = kryptering.hent_eller_opprett_aktiv_dek(a, TENANT)
        a.commit()
        _sett_kontekst(a, TENANT)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            a.execute("SELECT lagre_artefakt_staged(%s,%s,%s,%s,'r1',1,%s,0,100,"
                      "%s,%s,%s,%s,%s)",
                      (TENANT, opp, at, modul, kh, "h-" + secrets.token_hex(8),
                       ct, nonce, key_id, jti))
        a.rollback()
    finally:
        a.close()
    # Ingen rad, og kapabiliteten står IGJEN — avvisningen kom før brenningen,
    # så den legitime opplastingen kan fortsatt bruke den.
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute("SELECT count(*) FROM artefakt WHERE kapabilitet_jti=%s",
                         (jti,)).fetchone()[0]
    st = migrator.execute("SELECT status FROM artefaktkapabilitet WHERE jti=%s",
                          (jti,)).fetchone()[0]
    migrator.rollback()
    assert n == 0 and st == "utstedt", \
        f"{hvorfor} ble akseptert eller brente kapabiliteten"


@pg
def test_tabellen_avviser_udekrypterbar_payload_uansett_skrivevei(migrator):
    """Samme invariant som en TABELL-CHECK: `lagre_artefakt_staged` er den
    eneste veien runtime har, men constrainten gjør en udekrypterbar rad umulig
    for ENHVER skrivevei (migrator, framtidig funksjon, manuell reparasjon).
    Nullingen som hører til forkastelsen tar fortsatt BEGGE feltene.

    MUTASJONEN SOM DREPER DENNE: fjern `artefakt_payload_struktur` fra 016."""
    n = migrator.execute(
        "SELECT count(*) FROM pg_constraint WHERE conname="
        "'artefakt_payload_struktur' AND conrelid='artefakt'::regclass"
    ).fetchone()[0]
    migrator.rollback()
    assert n == 1, "tabell-invarianten for ciphertext/nonce mangler"
    # Håndhevet, ikke bare erklært: en direkte INSERT med tom payload avvises.
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.CheckViolation):
        migrator.execute(
            "INSERT INTO artefakt (tenant, oppdrag_id, artefakttype, modul_id,"
            " release_id, kontraktversjon, kontrakt_hash, module_epoch,"
            " storrelse_bytes, klartekst_sha256, ciphertext, nonce, dek_ref,"
            " kapabilitet_jti) VALUES (%s,1,'x','m','r1',1,'k',0,10,'h',"
            "%s,%s,'d',%s)",
            (TENANT, b"", b"", secrets.token_hex(16)))
    migrator.rollback()


@pg
def test_brukt_kapabilitet_er_terminal(migrator):
    """Codex: `brukt` er terminal. Uten dette kunne en brukt kapabilitet settes
    tilbake til `utstedt`, hvorpå en ellers idempotent retry tar INSERT-veien i
    lagre_artefakt_staged og kolliderer på artefaktets unike kapabilitet_jti.

    MUTASJONEN SOM DREPER DENNE: fjern brukt-terminal-sjekken i statusmaskinen."""
    from db import kryptering
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
        _sett_kontekst(a, TENANT)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(a, TENANT)
        ct, nonce = kryptering.krypter(dek, {"r": 1}, TENANT, key_id)
        a.execute("SELECT lagre_artefakt_staged(%s,%s,%s,%s,'r1',1,%s,0,100,"
                  "%s,%s,%s,%s,%s)",
                  (TENANT, opp, at, modul, kh, "h-" + secrets.token_hex(8),
                   ct, nonce, key_id, jti))
        a.commit()   # kapabiliteten er nå 'brukt'
    finally:
        a.close()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefaktkapabilitet SET status='utstedt'"
                         " WHERE jti=%s", (jti,))
    migrator.rollback()


@pg
def test_bindingsfelter_uforanderlige(migrator):
    modul = "m-" + secrets.token_hex(4); kh = "k-" + secrets.token_hex(8)
    opp, at = _plukket_oppdrag_med_binding(migrator, modul, kh)
    a = _admin()
    try:
        jti = _utsted(a, opp, modul, kh, at)
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("UPDATE artefaktkapabilitet SET module_epoch=9 WHERE jti=%s",
                         (jti,))
    migrator.rollback()
    with pytest.raises(psycopg.errors.RaiseException):
        migrator.execute("DELETE FROM artefaktkapabilitet WHERE jti=%s", (jti,))
    migrator.rollback()
