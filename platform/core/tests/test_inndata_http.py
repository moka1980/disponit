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

from .test_api import (DSN, MIGRATOR_DSN, app, dekker,  # noqa: F401
                       klient, miljo)
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


def _reserver(klient, cookie, csrf, idem=None, **kropp):
    from api import sesjon as sesjonmodul
    data = {"eiermodul": "m57_ats", "formaal": "soknadsbunt"}
    data.update(kropp)
    hoder = {"X-Disponit-CSRF": csrf}
    if idem is not False:                       # False = utelat den bevisst
        hoder["Idempotency-Key"] = idem or secrets.token_hex(12)
    return klient.post("/v1/inndata/reserver", json=data,
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers=hoder)


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
@dekker("inndata_alt_lastet")
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
        # Raden bærer den RELATIVE stien, `<tenant>/<uuid>.bin` (Cursor P1
        # runde 2) — roten er lesernes, og her er leseren testen.
        assert sti.startswith(f"{tenant}/") and sti.endswith(".bin")
        # … og på DISKEN: kryptert (aldri klartekst-zip), og dekrypterer
        # til nøyaktig de sendte bytene.
        raa_fil = (inndata_rot / sti).read_bytes()
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
    assert filer[0] == inndata_rot / sti, \
        "replayen byttet ut den lagrede filen"

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
@dekker("inndata_reservasjon_ugyldig")
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
        # MED idempotensnøkkel: uten den ville alle tre svart 400 på
        # nøkkelen i stedet, og porten målt noe annet enn den lover.
        r = klient.post("/v1/inndata/reserver", json=kropp,
                        cookies={sesjonmodul.C_SESJON: cookie},
                        headers={"X-Disponit-CSRF": csrf,
                                 "Idempotency-Key": secrets.token_hex(12)})
        assert r.status_code == 400, kropp
        assert r.json()["feil"] == "request_feilformet", kropp


@pg
def test_reservasjonen_er_replay_trygg_paa_idempotensnokkelen(klient,
                                                              inndata_rot):
    """Codex P2: reservasjonen er en opprettelse som IKKE er naturlig
    idempotent.

    Gikk 201-svaret tapt på veien ut — eller ga `commit()` en tvetydig
    forbindelsesfeil — hadde klienten ingenting å slå den genererte
    `inndata_ref` og jti-en opp med. En retry laget da en ANDRE levende
    reservasjon med en annen referanse, mens den første lå uleselig for
    alle til reaperen (egen PR) tok den.

    Tre halvdeler måles: nøkkelen er påkrevd, samme nøkkel gjenspiller
    NØYAKTIG det første svaret, og forskjellige nøkler er forskjellige
    reservasjoner.

    MUTASJONEN SOM DREPER DENNE: la `reserver_inndata` sette inn uten
    `ON CONFLICT`, eller la gjenspillet utstede en ny jti."""
    tenant, _bid, cookie, csrf = _rigg(klient)

    # 1) Påkrevd.
    r = _reserver(klient, cookie, csrf, idem=False)
    assert r.status_code == 400
    assert r.json()["feil"] == "idempotensnokkel_mangler"

    # 2) Gjenspill: samme nøkkel, samme svar — ikke en ny reservasjon.
    nokkel = secrets.token_hex(12)
    forste = _reserver(klient, cookie, csrf, idem=nokkel)
    assert forste.status_code == 201, forste.text
    igjen = _reserver(klient, cookie, csrf, idem=nokkel)
    assert igjen.status_code == 201, igjen.text
    assert igjen.json() == forste.json()

    # 3) Ny nøkkel = ny reservasjon.
    ny = _reserver(klient, cookie, csrf)
    assert ny.status_code == 201, ny.text
    assert ny.json()["reservasjon_jti"] != forste.json()["reservasjon_jti"]

    # … og basen har nøyaktig to rader for de to nøklene, ikke tre.
    m = _migrator(tenant)
    try:
        _kontekst(m, tenant)
        antall = m.execute(
            "SELECT count(*) FROM inndata_artefakt WHERE tenant=%s AND"
            " reservasjon_jti IN (%s,%s)",
            (tenant, forste.json()["reservasjon_jti"],
             ny.json()["reservasjon_jti"])).fetchone()[0]
        m.rollback()
    finally:
        m.close()
    assert antall == 2


@pg
@dekker("idempotenskonflikt")
def test_gjenspill_av_utlopt_reservasjon_laser_ikke_nokkelen(klient,
                                                            inndata_rot):
    """Cursor P2: gjenspillet svarte 201 med en UBRUKELIG jti.

    En reservasjon som fortsatt står `reservert` etter fristen er død:
    `registrer_inndata_lastet` avviser jti-en på `utloper`, og UNIQUE på
    `(tenant, idempotensnokkel)` sperrer en ny rad under den samme
    nøkkelen. Klienten som mistet 201-et og prøver igjen etter fristen
    fikk altså tilbake nøyaktig den jti-en som ikke lenger virker, og
    hadde ingen vei videre — verken opplasting eller ny reservasjon.
    Nettopp det tapet er grunnen til at gjenspillet finnes.

    Konflikt er det ærlige svaret: nøkkelen er oppbrukt, ta en ny.

    MUTASJONEN SOM DREPER DENNE: fjern utløps-grenen i gjenspillet — da
    blir dette en 201 igjen."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    nokkel = secrets.token_hex(12)
    m = _migrator(tenant)
    try:
        _kontekst(m, tenant)
        jti = _reservasjon(m, tenant, idem=nokkel,
                           utloper="now() - interval '1 second'")
        m.commit()
    finally:
        m.close()

    r = _reserver(klient, cookie, csrf, idem=nokkel)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "idempotenskonflikt"
    # …og den utløpte raden er URØRT: konflikten forlenger ingenting og
    # brenner ingenting. Reaperen (egen PR) er den som rydder den.
    m = _migrator(tenant)
    try:
        _kontekst(m, tenant)
        rad = m.execute(
            "SELECT status, lager_sti FROM inndata_artefakt"
            " WHERE tenant=%s AND reservasjon_jti=%s",
            (tenant, jti)).fetchone()
        m.rollback()
    finally:
        m.close()
    # B (059): stien står på raden fra FØDSELEN — at den finnes uten fil
    # er ingen løgn før raden er `lastet`; lagerfeilen brant ingenting.
    assert rad[0] == "reservert" and rad[1], rad

    # En NY nøkkel er veien ut, og den virker.
    ny = _reserver(klient, cookie, csrf)
    assert ny.status_code == 201, ny.text
    assert ny.json()["reservasjon_jti"] != jti


@pg
@dekker("idempotenskonflikt")
def test_gjenspill_av_forkastet_reservasjon_laser_ikke_nokkelen(klient,
                                                                inndata_rot):
    """Cursor P2, runde 2 på gjenspillet: `forkastet` var ikke dekket.

    Runde 1 tok `reservert` + over fristen og lot `forkastet` stå, med den
    begrunnelsen at ingen dør i PR-1 skriver den statusen. Men vakten
    TILLATER `reservert -> forkastet` og `lastet -> forkastet` — reaperen
    i PR-2 er bare den første som skal bruke dem — og en `forkastet` rad
    er død av nøyaktig samme grunn som den utløpte: jti-en avvises av
    `registrer_inndata_lastet`, og UNIQUE på `(tenant, idempotensnokkel)`
    sperrer en ny rad under den samme nøkkelen. Gjenspillet ville altså
    svart 201 med en referanse til en kastet bunt.

    Merk fristen her: raden er `forkastet` MED en frist i framtiden, så
    utløps-armen fra runde 1 kan ikke være den som feller den. Det er
    status alene som gjør raden død.

    MUTASJONEN SOM DREPER DENNE: ta `forkastet` ut av dødtilstands-grenen
    — da blir dette en 201 igjen."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    nokkel = secrets.token_hex(12)
    m = _migrator(tenant)
    try:
        _kontekst(m, tenant)
        jti = _reservasjon(m, tenant, idem=nokkel)      # frist i framtiden
        m.execute("UPDATE inndata_artefakt SET status='forkastet'"
                  " WHERE tenant=%s AND reservasjon_jti=%s", (tenant, jti))
        m.commit()
    finally:
        m.close()

    r = _reserver(klient, cookie, csrf, idem=nokkel)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "idempotenskonflikt"
    # …og den forkastede raden er URØRT: konflikten gjenoppliver ingenting.
    m = _migrator(tenant)
    try:
        _kontekst(m, tenant)
        rad = m.execute(
            "SELECT status, oppdrag_id FROM inndata_artefakt"
            " WHERE tenant=%s AND reservasjon_jti=%s",
            (tenant, jti)).fetchone()
        m.rollback()
    finally:
        m.close()
    assert rad == ("forkastet", None)

    # En NY nøkkel er veien ut, og den virker.
    ny = _reserver(klient, cookie, csrf)
    assert ny.status_code == 201, ny.text
    assert ny.json()["reservasjon_jti"] != jti


@pg
@dekker("idempotenskonflikt")
def test_nokkel_brukt_for_annen_reservasjon_gir_idempotenskonflikt(
        klient, inndata_rot):
    """Cursor P2: `unique_violation` → 409 `idempotenskonflikt` var
    umålt over HTTP.

    058 reiser den når en nøkkel gjenbrukes for en ANNEN kombinasjon, og
    `reserver_endepunkt` mapper den — men ingen test gikk gjennom ruten,
    så en regresjon som gjorde mappingen til en 500 (eller til
    `request_feilformet`, siden `InvalidParameterValue` fanges rett ved
    siden av) ville passert CI. Nabokontrakten for bestilling måles i
    `test_outbox_bestilling.py`.

    HTTP-laget låser `eiermodul`/`formaal` til den ene lovlige
    kombinasjonen, så den ANDRE reservasjonen fabrikkeres på `maks_bytes`
    — det tredje feltet 058 sammenligner.

    MUTASJONEN SOM DREPER DENNE: fjern `except UniqueViolation` i
    `reserver_endepunkt`, eller kombinasjonsvakten i 058."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    nokkel = secrets.token_hex(12)
    m = _migrator(tenant)
    try:
        _kontekst(m, tenant)
        _reservasjon(m, tenant, idem=nokkel, maks=MAKS - 1)
        m.commit()
    finally:
        m.close()

    r = _reserver(klient, cookie, csrf, idem=nokkel)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "idempotenskonflikt"


@pg
def test_reservasjonen_krever_read_committed(klient):
    """Cursor P2 runde 8: 058 manglet isolasjonsporten 056:821-827 og
    057:879-886 har.

    Idempotensløftet «samme nøkkel ⇒ samme reservasjon» er utledet av en
    LESNING: `ON CONFLICT DO NOTHING` svelger taperens unik-brudd, og
    gjenlesningen rett etter må se VINNERENS rad. Under REPEATABLE READ
    eller SERIALIZABLE står snapshotet fast fra første setning, så
    gjenlesningen er blind for en samtidig committet reservasjon — `v_id`
    blir NULL, `NOT FOUND` treffer, og «idempotenskonflikt uten lesbar
    rad» reises for en tilstand som ikke er den feilen beskriver.
    `api/inndata.py` mapper den til 409 `idempotenskonflikt`, altså
    «nøkkelen er brukt for en ANNEN reservasjon», der kontrakten lover
    201 med den samme referansen.

    Porten avviser NIVÅET, ikke kallet — derfor måles den positive
    kontrollen på den samme nøkkelen etterpå: gjenspillet på READ
    COMMITTED gir fortsatt den FØRSTE reservasjonen tilbake.

    MUTASJONEN SOM DREPER DENNE: fjern isolasjonsporten i
    `reserver_inndata`. Da svarer begge nivåene `unique_violation` i
    stedet, og feilen er igjen usann."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    nokkel = secrets.token_hex(12)
    kall = ("SELECT inndata_id FROM reserver_inndata"
            "(%s,'m57_ats','soknadsbunt',%s,%s)")
    c = _runtime(tenant)
    try:
        forste = c.execute(kall, (tenant, MAKS, nokkel)).fetchone()[0]
        c.commit()
    finally:
        c.close()
    for niva in (psycopg.IsolationLevel.REPEATABLE_READ,
                 psycopg.IsolationLevel.SERIALIZABLE):
        c = _runtime(tenant)
        try:
            # Nivået kan bare byttes utenfor en åpen transaksjon, og
            # `sett_kontekst` er SET LOCAL — den må settes på nytt etter.
            c.commit()
            c.isolation_level = niva
            _kontekst(c, tenant)
            with pytest.raises(psycopg.errors.InvalidTransactionState):
                c.execute(kall, (tenant, MAKS, nokkel))
            c.rollback()
        finally:
            c.close()
    c = _runtime(tenant)
    try:
        assert c.execute(kall, (tenant, MAKS, nokkel)).fetchone()[0] \
            == forste
        c.rollback()
    finally:
        c.close()


@pg
def test_scopet_gater_reservasjonen(klient, inndata_rot):
    from api import sesjon as sesjonmodul
    bid = _bruker_for("innsyn", ["leser"])
    cookie, csrf = _sesjon_for(bid)
    r = _reserver(klient, cookie, csrf)
    assert r.status_code in (401, 403)


@pg
def test_auth_avgjores_for_kroppen_leses(klient, inndata_rot, monkeypatch):
    """Codex P1 / Cursor P1-2: hele kroppen — inntil 64 MiB — ble
    strømmet, hashet og `join`-et FØR `_browserkontekst`. En avsender uten
    lov til å laste opp kunne dermed binde hundrevis av MiB i
    API-prosessen per samtidige forespørsel.

    Statuskoden alene beviser ingen REKKEFØLGE: den gamle koden svarte
    også 403, bare etter å ha bufret. Sensoren under måler ordenen — alt
    som rører kroppen i endepunktet går gjennom `hashlib.sha256`.

    MUTASJONEN SOM DREPER DENNE: flytt strøm-løkken tilbake foran
    auth-kallet. Da konstrueres hasheren først, og sensoren svarer i
    stedet for ruten."""
    from api import inndata as inndatamodul

    class _Sensor:
        @staticmethod
        def sha256(*_a, **_k):
            raise AssertionError("kroppen ble hashet FØR auth var avgjort")

    monkeypatch.setattr(inndatamodul, "hashlib", _Sensor)
    from api import sesjon as sesjonmodul
    bid = _bruker_for("innsyn", ["leser"])          # mangler bestilling:opprett
    cookie, csrf = _sesjon_for(bid)
    r = klient.put("/v1/inndata/opplast/" + "f" * 48,
                   content=b"P" * (1024 * 1024),
                   cookies={sesjonmodul.C_SESJON: cookie},
                   headers={"X-Disponit-CSRF": csrf,
                            "content-type": "application/zip"})
    assert r.status_code in (401, 403), r.text


@pg
@dekker("inndata_reservasjon_ugyldig")
def test_ugyldig_reservasjon_avvises_for_kroppen_leses(klient, inndata_rot,
                                                      monkeypatch):
    """Codex P2 runde 8: en ugyldig reservasjon ble oppdaget FØRST i
    `registrer_inndata_lastet` — etter at inntil 64 MiB var strømmet,
    hashet, kryptert, skrevet og fsynket i to katalognivåer.

    Statuskoden alene beviser ingenting her: den gamle koden svarte også
    409 på alle tre dødtilstandene, bare etter å ha betalt full pris i
    minne, CPU og disk for en helt forutsigbar avvisning. Sensoren måler
    REKKEFØLGEN — alt som rører kroppen i endepunktet går gjennom
    `hashlib.sha256`, akkurat som i auth-rekkefølgetesten over.

    Alle tre dødtilstandene `feil.py:233-237` krever det samme svaret for
    — ukjent, utløpt, forbrukt — måles hver for seg: en forhåndssjekk som
    bare kjente «ukjent» ville sendt utløp og forbruk ned den dyre veien
    igjen, og skillet i pris ville dessuten vært det jti-orakelet runde 7
    lukket i selve svaret.

    MUTASJONEN SOM DREPER DENNE: fjern forhåndssjekken i `autentiser`
    (eller snevre den til `rad is None`). Da konstrueres hasheren først,
    og sensoren svarer i stedet for ruten."""
    from api import inndata as inndatamodul

    tenant, _bid, cookie, csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        utlopt = _reservasjon(m, tenant, utloper="now() - interval '1 second'")
        jti_forkastet = secrets.token_hex(32)
        _lastet(m, tenant, jti=jti_forkastet)
        m.commit()
        _kontekst(m, tenant)
        m.execute("UPDATE inndata_artefakt SET status='forkastet'"
                  " WHERE tenant=%s AND reservasjon_jti=%s",
                  (tenant, jti_forkastet))
        m.commit()
    finally:
        m.close()

    class _Sensor:
        @staticmethod
        def sha256(*_a, **_k):
            raise AssertionError("kroppen ble hashet FØR reservasjonen"
                                 " var slått opp")

    monkeypatch.setattr(inndatamodul, "hashlib", _Sensor)
    for jti in ("f" * 48, utlopt, jti_forkastet):
        r = _opplast(klient, cookie, csrf, jti, b"P" * (1024 * 1024))
        assert r.status_code == 409, r.text
        assert r.json()["feil"] == "inndata_reservasjon_ugyldig", r.text
    # Ingen av de tre forsøkene rakk å skrive en ciphertext heller.
    assert not sorted((inndata_rot / tenant).glob("*.bin"))


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


@pg
@dekker("body_for_stor")
def test_content_length_avvises_for_ruten_i_det_hele_tatt_naas(
        klient, inndata_rot, monkeypatch):
    """Cursor P2: `_stroem` avviser en ÅPENBART for stor Content-Length
    uten å lese en byte — men ingen test skilte den grenen fra telleren.

    Testen over sender også en for stor kropp, og får 413 gjennom
    NØYAKTIG den samme headeren; fjernes Content-Length-sjekken i
    `_stroem`, teller middlewaren seg i stedet fram til det samme svaret
    og den testen blir grønn likevel. Negativene som finnes for
    `/v1/beslutning` (`test_api_porter`) måler hovedveien, ikke denne.

    Skillet her er REKKEFØLGEN, ikke statuskoden: forespørselen har ingen
    sesjon. Står sjekken, svarer middlewaren 413 før ruten i det hele tatt
    kalles. Faller den bort, når forespørselen `_browserkontekst` — som
    kjører et db-oppslag på en kropp ingen har lov til å sende — og svaret
    blir 401/403.

    MUTASJONEN SOM DREPER DENNE: fjern eller svekk Content-Length-armen i
    `KroppsgrenseMiddleware._stroem`."""
    from api import app as appmodul
    monkeypatch.setattr(appmodul, "INNDATA_MAKS_FYSISK", 4096)
    r = klient.put("/v1/inndata/opplast/" + "f" * 48,
                   content=b"P" * 4097,
                   headers={"content-type": "application/zip"})
    assert r.status_code == 413, r.text
    assert r.json()["feil"] == "body_for_stor"


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
                 eiermodul="m57_ats", idem=None, maks=None):
    """En reservasjon satt inn DIREKTE av migrator.

    Ikke via `reserver_inndata`: flere av testene under trenger en frist i
    fortiden, og `utloper` er bindingsfelt — `inndata_artefakt_vakt`
    nekter å endre den i ettertid, også for migrator. Vakten er BEFORE
    UPDATE OR DELETE, så innsettingen er den ene lovlige veien til en rad
    som alt er utløpt, uten å skru av en trigger som skal stå på.

    `idem`/`maks` er for testene som måler HTTP-gjenspillet: de trenger en
    rad HTTP-ruten kan treffe med en KJENT nøkkel."""
    jti = secrets.token_hex(32)
    m.execute(
        "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
        " innholdstype, maks_bytes, reservasjon_jti, idempotensnokkel,"
        " lager_sti, utloper)"
        f" VALUES (%s,%s,'soknadsbunt','application/zip',%s,%s,%s,%s,"
        f"{utloper})",
        (tenant, eiermodul, maks or MAKS, jti,
         idem or secrets.token_hex(12), _lagersti(tenant)))
    return jti


def _lagersti(tenant, navn=None):
    """En sti i TENANTENS eget navnerom — API-ets layout.

    RELATIV, uten rot: raden bærer `<tenant>/<uuid>.bin`, og API-et setter
    `INNDATA_ROT` på først når det åpner filen (Cursor P1 runde 2).
    `inndata_lagersti_navnerom` krever den formen, så en test som vil måle
    noe ANNET må sende en lovlig sti; ellers feller sti-porten først og
    testen består av feil grunn.

    Navnet er FRISKT per kall når det ikke er oppgitt. Defaulten var
    konstant, og siden `inndata_lagersti_unik` (runde 5d) er «én fil, én
    rad» en INVARIANT: to tester i samme tenant som begge tok defaulten
    kolliderte da på STIEN i stedet for på det de faktisk målte — og
    `test_dek_referansen_er_bundet_til_tenantens_nokler` fikk
    `UniqueViolation` der den ventet `ForeignKeyViolation`, fordi
    unikhetsindeksen treffer før FK-triggeren. Utfallet avhang av
    kjørerekkefølgen, ikke av koden den skulle måle. Testene som VIL dele
    en sti sier det eksplisitt (`"delt"`/`"egen"`)."""
    return f"{tenant}/{navn or secrets.token_hex(8)}.bin"


def _lastet(m, tenant, *, utloper="now() + interval '1 hour'",
            eiermodul="m57_ats", jti=None, sha=None, sti=None):
    """En ferdig `lastet` bunt — bindingens utgangspunkt. Returnerer
    inndata_id.

    `jti`/`sha` finnes for gjenspilltesten: den må laste opp MOT raden over
    HTTP, og da er det nettopp de to verdiene kallet må treffe."""
    from db import kryptering
    key_id, _dek = kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
    jti = jti or secrets.token_hex(32)
    return m.execute(
        "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
        " innholdstype, maks_bytes, faktiske_bytes, innhold_sha256,"
        " key_id, nonce, lager_sti, status, reservasjon_jti,"
        " idempotensnokkel, utloper, lastet_ts)"
        " VALUES (%s,%s,'soknadsbunt','application/zip',%s,10,%s,%s,%s,"
        f" %s,'lastet',%s,%s,{utloper},now()) RETURNING inndata_id",
        (tenant, eiermodul, MAKS, sha or "a" * 64, key_id, b"n" * 12,
         sti or _lagersti(tenant, jti), jti,
         secrets.token_hex(12))).fetchone()[0]


def _oppdrag(m, tenant, eiermodul, oppdragstype="rekruttering.evaluering"):
    """Et minimalt beslutningsoppdrag (samme form som
    `test_m57_utsending._grunnlag`): en TILLAT-loggpost hos tenanten er
    alt `oppdrag_koblingsvakt` (038 §5) krever av beslutningsopphavet.

    Typen er kontraktens KONSUMENT som default (`rekruttering.evaluering`
    er den eneste hvis kontrakt krever `soknadsbunt_ref`) — `bind_inndata`
    krever den. Argumentet finnes for negativtesten."""
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
        " VALUES ('beslutning',%s,%s,%s,%s,%s,%s,"
        " %s,%s,now()+interval '1 hour',now()+interval '1 day','KOBLET')"
        " RETURNING id",
        (tenant, logg, oppdragstype, oppdragstype, eiermodul, ct, key_id,
         nonce)).fetchone()[0])


def _bind(m, tenant, inndata_id, oppdrag, p_eiermodul="m57_ats"):
    """`bind_inndata` i oppdragets EGEN fødselstransaksjon — X1 (#192).

    059 krever `o.fodt_xid = pg_current_xact_id()`: vinduet der et oppdrag står
    plukkbart uten bunt kan ikke finnes, for bindingen skjer FØR commiten
    som gjør oppdraget synlig. Testene må derfor kalle denne i SAMME
    transaksjon som `_oppdrag`, aldri etter en commit.

    Rollen: bare bestillingsveiens runtime-rolle har EXECUTE, men runtime
    kan ikke sette inn `oppdrag`-raden direkte (den fødes av definer-
    døren i produksjon). Migrator kan sette inn raden, og kan
    `SET LOCAL ROLE` til dørens EIER — funksjonen er SECURITY DEFINER, så
    kroppen kjører uansett som `disponit_domene_eier`; kallet måler
    nøyaktig samme kode. Ved feil er transaksjonen abortert og SET LOCAL
    dør med rollback-en — derfor ingen RESET i en finally."""
    m.execute("SET LOCAL ROLE disponit_domene_eier")
    m.execute("SELECT bind_inndata(%s,%s,%s,%s)",
              (tenant, inndata_id, oppdrag, p_eiermodul))
    m.execute("RESET ROLE")


@pg
@dekker("inndata_reservasjon_ugyldig")
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
    # B (059): stien står på raden fra FØDSELEN — at den finnes uten fil
    # er ingen løgn før raden er `lastet`; ingenting ble brent.
    assert rad[0] == "reservert" and rad[1], rad
    # … og ciphertexten API-et rakk å skrive før 058 sa nei, er ryddet.
    assert not sorted((inndata_rot / tenant).glob("*.bin"))


@pg
@dekker("inndata_reservasjon_ugyldig")
def test_lastet_og_utlopt_kan_ikke_gjenspilles(klient, inndata_rot):
    """Codex P2 / Cursor P2 runde 6: replay-grenen for `lastet` svarte 201
    uten å se på `utloper`, mens `bind_inndata` avviser NØYAKTIG den samme
    raden på den.

    Utfallet var det verste av to verdener: klienten fikk «opplastingen
    din står» tilbake, idempotensnøkkelen var låst til en lineage som
    aldri kan bindes, og hver eneste bestilling som pekte på referansen
    feilet siden. Et ærlig avslag ved opplasting er derimot en klient som
    bare reserverer på nytt.

    Testen måler BEGGE endene av mismatchen på den samme raden: gjenspillet
    avvises, og bindingen som var grunnen til at det måtte avvises,
    avviser den også.

    MUTASJONEN SOM DREPER DENNE: fjern utløpssjekken i `lastet`-grenen i
    `registrer_inndata_lastet` — da blir dette et 201 igjen."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    kropp = _zipbytes()
    sha = hashlib.sha256(kropp).hexdigest()
    jti = secrets.token_hex(32)
    m = _migrator(tenant)
    try:
        # En bunt som ALT er lastet med nøyaktig denne kroppens hash — det
        # er tilstanden et tapt 201-svar etterlater — men utløpt.
        inndata_id = _lastet(m, tenant, utloper="now() - interval '1 second'",
                             jti=jti, sha=sha)
        m.commit()
    finally:
        m.close()
    # Gjenspillet: samme jti, samme kropp, samme sha — og nå et ærlig
    # avslag i stedet for et 201 på en ubrukelig referanse.
    r = _opplast(klient, cookie, csrf, jti, kropp)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "inndata_reservasjon_ugyldig"
    # Ciphertexten API-et rakk å skrive før 058 sa nei, er ryddet — et
    # avvist gjenspill skal ikke legge igjen en foreldreløs fil heller.
    assert not sorted((inndata_rot / tenant).glob("*.bin"))
    # Den andre enden: bindingen avviser den samme raden — i oppdragets
    # egen fødselstransaksjon (X1), så det er UTLØPET som feller, ikke
    # transaksjonsporten. Det er nettopp DENNE avvisningen gjenspillet
    # ikke fikk lov å motsi.
    m = _migrator(tenant)
    try:
        oppdrag = _oppdrag(m, tenant, "m57_ats")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _bind(m, tenant, inndata_id, oppdrag)
        m.rollback()
    finally:
        m.close()


@pg
@dekker("inndata_reservasjon_ugyldig")
def test_forbrukt_reservasjon_er_ikke_et_jti_orakel(klient, inndata_rot):
    """Cursor P2 runde 7: «alt forbrukt» reiste `unique_violation`.

    `inndata.py` mapper den til `inndata_alt_lastet`, mens ukjent og
    utløpt jti blir `inndata_reservasjon_ugyldig`. `feil.py:233-237` sier
    ordrett at alle tre skal ha SAMME svar, fordi «et skille ville vært et
    orakel på hvilke jti-er som finnes». Slik den sto, kunne en kaller med
    gyldig sesjon lese ut om en jti hadde nådd minst `bundet`/`forkastet`
    — og svaret var i tillegg usant, for innholdet var aldri det som
    skilte.

    Begge dødtilstandene måles, og med en kropp hvis sha MATCHER radens:
    er hashen den samme, kan det ikke være hash-grenen som feller kallet.
    `inndata_alt_lastet` beholder sin egen dekning i
    ende-til-ende-testen (ANNEN kropp på en `lastet` rad) og i
    `test_to_rader_kan_ikke_dele_den_samme_fysiske_bunten`.

    MUTASJONEN SOM DREPER DENNE: sett `unique_violation` tilbake på
    forbrukt-grenen i `registrer_inndata_lastet` — da svarer begge
    `inndata_alt_lastet`."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    kropp = _zipbytes()
    sha = hashlib.sha256(kropp).hexdigest()
    jti_bundet, jti_forkastet = secrets.token_hex(32), secrets.token_hex(32)
    m = _migrator(tenant)
    try:
        bundet = _lastet(m, tenant, jti=jti_bundet, sha=sha)
        _lastet(m, tenant, jti=jti_forkastet, sha=sha)
        m.commit()
        _kontekst(m, tenant)
        m.execute("UPDATE inndata_artefakt SET status='forkastet'"
                  " WHERE tenant=%s AND reservasjon_jti=%s",
                  (tenant, jti_forkastet))
        m.commit()
        _kontekst(m, tenant)
        oppdrag = _oppdrag(m, tenant, "m57_ats")
        _bind(m, tenant, bundet, oppdrag)
        m.commit()
    finally:
        m.close()

    for jti in (jti_bundet, jti_forkastet):
        r = _opplast(klient, cookie, csrf, jti, kropp)
        assert r.status_code == 409, r.text
        assert r.json()["feil"] == "inndata_reservasjon_ugyldig", \
            f"forbrukt jti lekker sin egen livssyklus: {r.json()}"
    # Og ingen av de to avviste forsøkene la igjen en ciphertext.
    assert not sorted((inndata_rot / tenant).glob("*.bin"))


def _lagerfeil_paa(monkeypatch, navn, rot):
    """Full disk i ÉN `os`-operasjon, og bare på inndata-lagerets egne stier.

    En global sensor ville truffet alt annet i prosessen som rører samme
    kall i det samme vinduet; her måles nøyaktig den ene veien."""
    ekte = getattr(os, navn)

    def sensor(sti, *a, **k):
        if isinstance(sti, str) and sti.startswith(str(rot)):
            raise OSError(28, "No space left on device")
        return ekte(sti, *a, **k)

    monkeypatch.setattr(os, navn, sensor)
    # Egen tilbakestilling, ikke `monkeypatch.undo()`: den ville også rullet
    # tilbake `inndata_rot`-fixturens INNDATA_ROT.
    return lambda: monkeypatch.setattr(os, navn, ekte)


@pg
@pytest.mark.parametrize("kall,spor", [("replace", ".tmp"), ("open", ".bin")])
def test_lagerfeil_for_registreringen_etterlater_ingen_fil(
        klient, inndata_rot, monkeypatch, kall, spor):
    """Codex P2: I/O-en FØR registreringen lå utenfor ryddingen.

    Feilet noe i skriv-og-flytt — en full disk er den nære årsaken —
    reiste det før `try`-en som rydder i det hele tatt var nådd, og `.tmp`
    ble liggende. Feilet katalog-fsyncen ETTER `os.replace` (den åpner
    katalogen med `os.open`, som ikke brukes til noe annet i denne veien),
    ble en komplett, foreldreløs `.bin` liggende. Ingen av dem har en rad
    og ingen av dem har en eier, så en klient som prøver igjen under den
    samme lagerfeilen legger på en ny for hvert forsøk: feilen som fylte
    disken spiser mer disk.

    Begge halvdelene måles, for de etterlater hver sin sti å rydde.

    MUTASJONEN SOM DREPER DENNE: la ryddingen dekke bare `tmp`, eller
    snevre `try`-en inn til registreringen igjen."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        jti = _reservasjon(m, tenant)
        m.commit()

        tilbake = _lagerfeil_paa(monkeypatch, kall, inndata_rot)
        with pytest.raises(OSError):
            _opplast(klient, cookie, csrf, jti, _zipbytes())
        tilbake()

        # Ingen av de to sporene ligger igjen — heller ikke det denne
        # varianten er navngitt etter.
        katalog = inndata_rot / tenant
        rester = sorted(p.name for p in katalog.glob("*")) \
            if katalog.exists() else []
        assert not rester, f"lagerfeilen etterlot {rester} (ventet ingen {spor})"

        # … og reservasjonen er urørt: ingenting ble registrert.
        _kontekst(m, tenant)
        rad = m.execute(
            "SELECT status, lager_sti FROM inndata_artefakt"
            " WHERE tenant=%s AND reservasjon_jti=%s",
            (tenant, jti)).fetchone()
        m.rollback()
    finally:
        m.close()
    # B (059): stien står på raden fra FØDSELEN — at den finnes uten fil
    # er ingen løgn før raden er `lastet`; ingenting ble brent.
    assert rad[0] == "reservert" and rad[1], rad


@pg
def test_begge_katalognivaaene_fsynces_for_commit(klient, inndata_rot,
                                                  monkeypatch):
    """Codex P2: fsyncen tok bare BARNET.

    Første opplasting for en tenant oppretter `<rot>/<tenant>` her. En
    fsync av den katalogen gjør filens oppføring I den varig — ikke
    katalogens egen oppføring i `<rot>`. Mister verten strømmen etter
    db-commiten, kunne dermed hele tenantkatalogen forsvinne og etterlate
    den samme `lastet`-raden uten fil: nøyaktig hullet katalog-fsyncen fra
    runde 1 skulle lukke, ett nivå opp.

    MUTASJONEN SOM DREPER DENNE: fjern `INNDATA_ROT` fra løkka, eller gjør
    rot-fsyncen betinget av at `makedirs` skapte katalogen (to samtidige
    førsteopplastinger ser hver sin halvdel av den betingelsen)."""
    tenant, _bid, cookie, csrf = _rigg(klient)
    rot = str(inndata_rot)
    ekte_open, ekte_fsync = os.open, os.fsync
    fd_sti, fsynket = {}, []

    def spion_open(sti, *a, **k):
        fd = ekte_open(sti, *a, **k)
        if isinstance(sti, str) and sti.startswith(rot):
            fd_sti[fd] = sti
        return fd

    def spion_fsync(fd):
        # `pop`, ikke oppslag: en lukket fd gjenbrukes av neste `os.open`,
        # og en gammel oppføring ville da tilskrevet feil sti.
        if fd in fd_sti:
            fsynket.append(fd_sti.pop(fd))
        return ekte_fsync(fd)

    monkeypatch.setattr(os, "open", spion_open)
    monkeypatch.setattr(os, "fsync", spion_fsync)
    r = _reserver(klient, cookie, csrf)
    assert r.status_code == 201, r.text
    r2 = _opplast(klient, cookie, csrf, r.json()["reservasjon_jti"],
                  _zipbytes())
    assert r2.status_code == 201, r2.text

    # Barnet FØR roten: filens oppføring skal være varig før katalogens
    # egen oppføring kvitteres.
    assert fsynket == [os.path.join(rot, tenant), rot], fsynket


@pg
def test_sql_lukker_eiermodulen_ikke_bare_http(klient):
    """Cursor P2-3: `formaal` var CHECKet, `eiermodul` ikke — og
    `disponit` har EXECUTE på `reserver_inndata`. HTTP-laget lukket settet
    i `reserver_endepunkt`, men en annen kaller går ikke gjennom HTTP.

    MUTASJONEN SOM DREPER DENNE: fjern CHECKen på kolonnen ELLER guarden
    i funksjonen — begge armene måles.

    NB (Cursor P2): kallet var firearguments etter at `reserver_inndata`
    fikk `p_idempotensnokkel`. Da reiser Postgres `UndefinedFunction`, og
    en `raises(InvalidParameterValue)` rundt den måler at funksjonen ikke
    finnes — ikke guarden testen sier den dekker."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    c = _runtime(tenant)
    try:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            c.execute("SELECT * FROM reserver_inndata(%s,%s,%s,%s,%s)",
                      (tenant, "m_wcag_audit", "soknadsbunt", MAKS,
                       secrets.token_hex(12)))
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
                "SELECT * FROM registrer_inndata_lastet(%s,%s,%s,%s,%s,%s)",
                (tenant, jti, 10, "a" * 64, key_id, b"\x01"))
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
                      " idempotensnokkel, utloper, nonce)"
                      " VALUES (%s,'m57_ats','soknadsbunt',"
                      "'application/zip',%s,%s,%s,now()+interval '1 h',%s)",
                      (tenant, MAKS, secrets.token_hex(32),
                       secrets.token_hex(12), b"\x01"))
        m.rollback()
    finally:
        m.close()


@pg
def test_forkasting_kan_ikke_stjele_oppdragets_bunteplass(klient):
    """Cursor P2: `forkastet`-grenen i `inndata_tilstand_totalt` var TOM,
    og `oppdrag_id` var hverken bindingsfelt eller write-once.

    En forkasting kunne derfor sette `oppdrag_id` i samme UPDATE og ta
    plassen i `inndata_artefakt_oppdrag` — som er UNIQUE på alle
    ikke-NULL `oppdrag_id` uansett status. Resultatet: bunten kastet,
    oppdraget hadde brukt opp sin ene bunteplass, og en ekte
    `bind_inndata` på det oppdraget var blokkert for alltid. Reaperen som
    skal skrive denne overgangen kommer i egen PR — invarianten må stå
    før døren.

    MUTASJONEN SOM DREPER DENNE: fjern `oppdrag_id`-guarden i vakten
    eller tøm `forkastet`-grenen igjen."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        offer = _lastet(m, tenant)
        ekte = _lastet(m, tenant)
        opp = _oppdrag(m, tenant, "m57_ats")
        m.commit()
        _kontekst(m, tenant)
        # Vakten: `oppdrag_id` kan ikke endres i noen annen overgang.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            m.execute("UPDATE inndata_artefakt SET status='forkastet',"
                      " oppdrag_id=%s WHERE tenant=%s AND inndata_id=%s",
                      (opp, tenant, offer))
        m.rollback()
        _kontekst(m, tenant)
        # CHECKen: samme invariant på enhver skrivevei, også INSERT.
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute(
                "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
                " innholdstype, maks_bytes, status, reservasjon_jti,"
                " idempotensnokkel, oppdrag_id, utloper)"
                " VALUES (%s,'m57_ats','soknadsbunt','application/zip',"
                "%s,'forkastet',%s,%s,%s,now()+interval '1 h')",
                (tenant, MAKS, secrets.token_hex(32), secrets.token_hex(12),
                 opp))
        m.rollback()
    finally:
        m.close()
    # … og en ekte binding går fortsatt: begge de avviste forsøkene over
    # rullet tilbake uten å okkupere noen bunteplass. X1 (#192) gjør at
    # bindingen må skje i et oppdrags fødselstransaksjon, så den ekte
    # veien måles med et ferskt oppdrag — `opp` over var aldri lovlig å
    # binde i ettertid, og scenariet vakten dekker (reaperens forkasting
    # som SETTER `oppdrag_id`) er uendret.
    m = _migrator(tenant)
    try:
        opp2 = _oppdrag(m, tenant, "m57_ats")
        _bind(m, tenant, ekte, opp2)
        m.commit()
    finally:
        m.close()


@pg
def test_innhold_sha256_maa_ha_hashens_form(klient):
    """Cursor P2 (049/053/054-klassen): `innhold_sha256` var bare NOT NULL
    i `lastet`-grenen, mens `registrer_inndata_lastet` tar verdien fra en
    runtime-kaller med EXECUTE.

    `''` eller `'nei'` kunne dermed brenne jti-en og lande som `lastet`
    med en hash ingen resolver kan stole på — og replay-armen fra runde 1
    sammenligner nettopp mot den, altså mot søppel: en retry med samme
    kropp ville da fått `unique_violation` i stedet for gjenspill.

    MUTASJONEN SOM DREPER DENNE: fjern regexen i funksjonsguarden eller
    `inndata_sha256_format` på tabellen."""
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
        for sha in ("", "nei", "A" * 64, "a" * 63, "a" * 65):
            with pytest.raises(psycopg.errors.InvalidParameterValue):
                c.execute("SELECT * FROM registrer_inndata_lastet"
                          "(%s,%s,%s,%s,%s,%s)",
                          (tenant, jti, 10, sha, key_id, b"n" * 12))
            c.rollback()
            _kontekst(c, tenant)
    finally:
        c.close()
    m = _migrator(tenant)
    try:
        _kontekst(m, tenant)
        # … og reservasjonen står ubrent.
        assert m.execute("SELECT status FROM inndata_artefakt WHERE"
                         " tenant=%s AND reservasjon_jti=%s",
                         (tenant, jti)).fetchone()[0] == "reservert"
        # Tabellen bærer den samme invarianten, uansett skrivevei.
        with pytest.raises(psycopg.errors.CheckViolation):
            m.execute(
                "INSERT INTO inndata_artefakt (tenant, eiermodul, formaal,"
                " innholdstype, maks_bytes, faktiske_bytes, innhold_sha256,"
                " key_id, nonce, lager_sti, status, reservasjon_jti,"
                " idempotensnokkel, utloper, lastet_ts)"
                " VALUES (%s,'m57_ats','soknadsbunt','application/zip',"
                "%s,10,'nei',%s,%s,%s,'lastet',%s,%s,"
                " now()+interval '1 h',now())",
                (tenant, MAKS, key_id, b"n" * 12, _lagersti(tenant),
                 secrets.token_hex(32), secrets.token_hex(12)))
        m.rollback()
    finally:
        m.close()


def test_tenant_iden_maa_vaere_en_trygg_stikomponent():
    """Codex P2 runde 6: `brukermedlemskap.tenant` er ubegrenset `TEXT`, og
    opplastingen var det eneste stedet i repoet der den strengen ble til en
    FILSTI.

    `os.path.join(rot, "/tmp/acme")` KASTER roten — bunten havnet utenfor
    unitens state-katalog og kunne forsvinne uavhengig av basen. 058s
    `inndata_lagersti_navnerom` fanget det ikke og KAN ikke: den måler
    `lager_sti` mot nøyaktig den samme tenant-strengen stien ble bygget av,
    så `/tmp/acme` + `/tmp/acme/<uuid>.bin` passerer den. Derfor står
    vakten i API-et, FØR første I/O — ikke som enda en CHECK.

    Ingen `@pg`: dette er en ren strengavgjørelse, og den skal måles selv
    når suiten kjører uten base.

    MUTASJONEN SOM DREPER DENNE: fjern `_stikomponent`-kallet i
    `opplast_endepunkt`, eller la `rel` bygges av rå `tenant` igjen."""
    from api import inndata
    giftige = ("/tmp/acme", "", ".", "..", "a/b", "acme/", "a\x00b",
               "/", "../acme")
    for t in giftige:
        with pytest.raises(ValueError):
            inndata._stikomponent(t)
    for t in ("acme", "t-rhttp-abc123", "a.b-c_d"):
        assert inndata._stikomponent(t) == t
    # Vakten er ikke bare en påstand om strengen: den er grunnen til at
    # sammensetningen under ikke kan rømme roten.
    assert os.path.dirname(
        os.path.join("/rot", inndata._stikomponent("acme"))) == "/rot"
    # Og den rå tenant-strengen skal ikke lenger nå NOEN `os.path.join` i
    # opplastingsveien — `komp` gjør det, i begge sammensetningene.
    import inspect
    kilde = inspect.getsource(inndata.opplast_endepunkt)
    assert "os.path.join(INNDATA_ROT, tenant)" not in kilde
    assert "os.path.join(tenant," not in kilde


@pg
def test_lagerstien_maa_ligge_i_tenantens_eget_navnerom(klient):
    """Cursor P1 (runde 1-2, 058) + B-maskinen (#192, 059).

    Under 058 tok `registrer_inndata_lastet` stien fra en runtime-kaller
    med EXECUTE, og to runder herdet guarden mot fremmede tenanters
    kataloger, `..`-traversering og delstreng-hullet
    (`en-annen-tenant/<tenant>/x.bin`). B-maskinen fjernet hele
    angrepsflaten: stien FØDES av `reserver_inndata` som
    `<tenant>/<inndata_id>.bin`, og ingen dør tar lenger en sti utenfra.

    Det som består og måles er derfor to ting: (1) dørens EGEN generering
    lander ordrett på formen navnerommet lover, og (2)
    `inndata_lagersti_navnerom` på TABELLEN feller hver av de gamle
    angrepsstiene på enhver annen skrivevei — vakter i dybden er ikke
    død kode selv om døren foran dem ble trygg per konstruksjon.

    MUTASJONEN SOM DREPER DENNE: la `reserver_inndata` bygge stien av noe
    annet enn tenant + inndata_id, eller fjern
    `inndata_lagersti_navnerom`."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    c = _runtime(tenant)
    try:
        rad = c.execute(
            "SELECT inndata_id, lager_sti FROM reserver_inndata"
            "(%s,%s,%s,%s,%s)",
            (tenant, "m57_ats", "soknadsbunt", MAKS,
             secrets.token_hex(12))).fetchone()
        c.commit()
    finally:
        c.close()
    # Fødselsstien er EKSAKT kontraktens form — ikke bare «inne i
    # navnerommet», for enhver annen form ville vært en ny kontrakt.
    assert rad[1] == f"{tenant}/{rad[0]}.bin", rad
    giftige = (
        "/tmp/x.bin",                              # absolutt, utenfor
        "en-annen-tenant/x.bin",                   # FREMMED tenant
        f"{tenant}/../en-annen-tenant/x.bin",      # traversering
        f"/var/lib/disponit-inndata/{tenant}/x.bin",   # rot i raden
        f"{tenant}/",                              # tomt filnavn
        # DELSTRENG-HULLET fra 058-runde 1: under en FREMMED tenants
        # katalog, men inneholder `/<tenant>/`.
        f"en-annen-tenant/{tenant}/x.bin",
    )
    m = _migrator(tenant)
    try:
        for sti in giftige:
            with pytest.raises(psycopg.errors.CheckViolation):
                m.execute(
                    "INSERT INTO inndata_artefakt (tenant, eiermodul,"
                    " formaal, innholdstype, maks_bytes, reservasjon_jti,"
                    " idempotensnokkel, lager_sti, utloper)"
                    " VALUES (%s,'m57_ats','soknadsbunt','application/zip',"
                    "%s,%s,%s,%s,now()+interval '1 h')",
                    (tenant, MAKS, secrets.token_hex(32),
                     secrets.token_hex(12), sti))
            m.rollback()
            _kontekst(m, tenant)
    finally:
        m.close()


@pg
def test_tenant_med_to_punktum_er_lovlig_og_traversering_er_det_ikke(klient):
    """Codex P2 runde 7 (058) + B-maskinen (#192, 059).

    `brukermedlemskap.tenant` er ubegrenset TEXT, og `acme..corp` ER én
    trygg stikomponent — men `<tenant>/<uuid>.bin` inneholder da `..`,
    og en guard som leter etter `..` i den SAMMENSATTE strengen gjør
    tenanten permanent ute av stand til å laste opp. Skillet bor i
    navnerommets to ledd: tenant-leddet må få inneholde punktum-par,
    filnavn-leddet må aldri VÆRE `..`/`.`.

    Under B fødes stien av `reserver_inndata`; begge retningene måles
    derfor der de nå bor: døren skal FØDE en lovlig sti for nettopp denne
    tenanten, og tabellens `inndata_lagersti_navnerom` skal fortsatt
    felle traversering i filnavn-leddet på enhver annen skrivevei.

    MUTASJONEN SOM DREPER DENNE: sett `position('..' in ...)` tilbake på
    den sammensatte strengen i CHECKen — da feiler den lovlige halvdelen
    (dørens INSERT feller på fødselen). Fjern filnavn-guarden — da består
    den giftige."""
    _rigg(klient)
    tenant = f"acme..corp-{secrets.token_hex(6)}"
    c = _runtime(tenant)
    try:
        rad = c.execute(
            "SELECT inndata_id, lager_sti FROM reserver_inndata"
            "(%s,%s,%s,%s,%s)",
            (tenant, "m57_ats", "soknadsbunt", MAKS,
             secrets.token_hex(12))).fetchone()
        c.commit()
    finally:
        c.close()
    # Den lovlige halvdelen: fødselen gikk, og på kontraktens form.
    assert rad[1] == f"{tenant}/{rad[0]}.bin", rad
    m = _migrator(tenant)
    try:
        # Den giftige: traversering i FILNAVN-leddet, samme tenant.
        for sti in (f"{tenant}/..", f"{tenant}/../x.bin", f"{tenant}/."):
            with pytest.raises(psycopg.errors.CheckViolation):
                m.execute(
                    "INSERT INTO inndata_artefakt (tenant, eiermodul,"
                    " formaal, innholdstype, maks_bytes, reservasjon_jti,"
                    " idempotensnokkel, lager_sti, utloper)"
                    " VALUES (%s,'m57_ats','soknadsbunt','application/zip',"
                    "%s,%s,%s,%s,now()+interval '1 h')",
                    (tenant, MAKS, secrets.token_hex(32),
                     secrets.token_hex(12), sti))
            m.rollback()
            _kontekst(m, tenant)
    finally:
        m.close()


@pg
def test_to_rader_kan_ikke_dele_den_samme_fysiske_bunten(klient):
    """Codex P1 (058) + B-maskinen (#192, 059): navnerommet sier HVOR
    stien kan peke, ikke at ingen annen rad peker samme sted.

    Under 058 kunne en kaller med EXECUTE lese en eksisterende rads sti
    og registrere sin egen reservasjon på nøyaktig den — to
    «engangs»-artefakter bar samme fysiske bunt, og ryddingen av den ene
    slettet ciphertexten den andre refererte. B-maskinen lukket den døren:
    stien fødes som `<tenant>/<inndata_id>.bin`, og `inndata_id` er
    PRIMARY KEY, så dørens stier KAN ikke kollidere.

    `inndata_lagersti_unik` består som vakt i dybden mot enhver annen
    skrivevei, og begge lagene måles: dørens fødsler er parvis ulike, og
    indeksen feller et direkte alias.

    MUTASJONEN SOM DREPER DENNE: fjern `inndata_lagersti_unik`, eller la
    fødselsstien bygges av noe som kan gjenta seg."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    c = _runtime(tenant)
    try:
        stier = [c.execute(
            "SELECT lager_sti FROM reserver_inndata(%s,%s,%s,%s,%s)",
            (tenant, "m57_ats", "soknadsbunt", MAKS,
             secrets.token_hex(12))).fetchone()[0] for _ in range(2)]
        c.commit()
    finally:
        c.close()
    assert stier[0] != stier[1], "to fødsler delte sti"
    delt = _lagersti(tenant, "delt")
    m = _migrator(tenant)
    try:
        _lastet(m, tenant, sti=delt)
        m.commit()
        _kontekst(m, tenant)
        # Aliaset: en ANNEN rad på samme fysiske fil avvises av indeksen.
        with pytest.raises(psycopg.errors.UniqueViolation):
            _lastet(m, tenant, sti=delt)
        m.rollback()
        _kontekst(m, tenant)
        # …og en EGEN sti går fortsatt igjennom.
        _lastet(m, tenant, sti=_lagersti(tenant, "egen"))
        m.commit()
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
                " idempotensnokkel, utloper, lastet_ts)"
                " VALUES (%s,'m57_ats','soknadsbunt',"
                "'application/zip',%s,10,%s,'finnes-ikke',%s,%s,"
                "'lastet',%s,%s,now()+interval '1 h',now())",
                (tenant, MAKS, "a" * 64, b"n" * 12, _lagersti(tenant),
                 secrets.token_hex(32), secrets.token_hex(12)))
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
        m.commit()
        # Angrepet i oppdragets fødselstransaksjon (X1), så det er
        # eiermodul-avledningen som feller — ikke transaksjonsporten.
        _kontekst(m, tenant)
        fremmed = _oppdrag(m, tenant, "m_wcag_audit")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _bind(m, tenant, bunt, fremmed, "m57_ats")
        m.rollback()
        # …og den ekte kombinasjonen går fortsatt igjennom.
        _kontekst(m, tenant)
        eget = _oppdrag(m, tenant, "m57_ats")
        _bind(m, tenant, bunt, eget)
        m.commit()
        _kontekst(m, tenant)
        assert m.execute("SELECT status, oppdrag_id FROM inndata_artefakt"
                         " WHERE tenant=%s AND inndata_id=%s",
                         (tenant, bunt)).fetchone() == ("bundet", eget)
        m.rollback()
    finally:
        m.close()


@pg
def test_bindingen_krever_formaalets_konsumerende_oppdragstype(klient):
    """Codex P1: eierskap er ikke formål.

    Eiersjekken over sier bare at oppdraget eies av samme modul som bunten
    ble reservert for — men `m57_ats` eier flere oppdragstyper enn den ene
    hvis kontrakt faktisk konsumerer en søknadsbunt (`soknadsbunt_ref` er
    påkrevd i `rekruttering.evaluering` alene, `FELTSTRENGER`). En kaller
    med EXECUTE kunne derfor bundet bunten til et vilkårlig annet
    m57-oppdrag: engangsbunten forbrukt, det uskyldige oppdragets ENE
    bunteplass brukt opp, og lineage pekende på en jobb som aldri skulle
    lest den.

    Angrepet spilles av ordrett: riktig tenant, riktig eiermodul, riktig
    påstand — feil oppdragstype.

    MUTASJONEN SOM DREPER DENNE: fjern `v_konsument`-sjekken, eller la
    kartet falle tilbake på å slippe gjennom et ukjent formål."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        bunt = _lastet(m, tenant)
        m.commit()
        # Eid av RIKTIG modul, men en type hvis kontrakt ikke leser bunter
        # — i oppdragets fødselstransaksjon (X1), så det er
        # konsument-porten som feller.
        _kontekst(m, tenant)
        feil_type = _oppdrag(m, tenant, "m57_ats",
                             oppdragstype="rekruttering.utsending")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _bind(m, tenant, bunt, feil_type)
        m.rollback()
        _kontekst(m, tenant)
        riktig = _oppdrag(m, tenant, "m57_ats")
        _bind(m, tenant, bunt, riktig)
        m.commit()
        _kontekst(m, tenant)
        # Bunten er urørt av det avviste forsøket og bundet til den ekte.
        assert m.execute("SELECT status, oppdrag_id FROM inndata_artefakt"
                         " WHERE tenant=%s AND inndata_id=%s",
                         (tenant, bunt)).fetchone() == ("bundet", riktig)
        m.rollback()
    finally:
        m.close()


@pg
@pytest.mark.parametrize("terminal", ["kansellert", "feilet"])
def test_bindingen_avviser_terminalt_oppdrag(klient, terminal):
    """Cursor P2 runde 6: eierskap og formål sier HVEM og HVA, ikke NÅR.

    `bind_inndata` slo opp `oppdrag.eiermodul` og `oppdragstype`, men aldri
    `oppdrag.status`. En kaller med EXECUTE kunne derfor binde en lastet
    bunt til et TERMINALT oppdrag: engangsbunten forbrukt, det terminale
    oppdragets ENE bunteplass (`inndata_artefakt_oppdrag`) brukt opp for
    alltid — 005s vakt tillater ingen overgang UT av terminal, så plassen
    kommer aldri tilbake — og lineage pekende på en jobb som var ferdig før
    bunten fantes. Nabolaget gjør dette: 017:110-111 krever `plukket`, 038s
    reaper regner (`opprettet`,`plukket`) som det aktive settet.

    `utfort` er den samme grenen, men krever `plukket` først; de to
    ett-stegs-terminalene måler porten uten å dra inn claim-veien.

    MUTASJONEN SOM DREPER DENNE: fjern statusporten i `bind_inndata`, eller
    utvid settet til å inkludere en terminaltilstand."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        dod, levende = _lastet(m, tenant), _lastet(m, tenant)
        m.commit()
        # Terminalen settes i SAMME transaksjon som oppdraget fødes (X1):
        # `fodt_xid` er stemplet av innsettingen her, så statusporten er
        # fortsatt den som feller — ikke transaksjonsporten.
        _kontekst(m, tenant)
        opp_dod = _oppdrag(m, tenant, "m57_ats")
        m.execute("UPDATE oppdrag SET status=%s WHERE tenant=%s AND id=%s",
                  (terminal, tenant, opp_dod))
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _bind(m, tenant, dod, opp_dod)
        m.rollback()
        _kontekst(m, tenant)
        # Kontrollarmen: porten stenger det terminale, ikke den ekte veien.
        opp_levende = _oppdrag(m, tenant, "m57_ats")
        _bind(m, tenant, levende, opp_levende)
        m.commit()
        _kontekst(m, tenant)
        rader = dict(
            (r[0], r[1:]) for r in m.execute(
                "SELECT inndata_id, status, oppdrag_id FROM inndata_artefakt"
                " WHERE tenant=%s AND inndata_id = ANY(%s)",
                (tenant, [dod, levende])).fetchall())
        m.rollback()
    finally:
        m.close()
    # Den avviste bunten står UBRENT — et avvist forsøk koster ingenting.
    assert rader[dod] == ("lastet", None)
    assert rader[levende] == ("bundet", opp_levende)


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
        m.commit()
        # Begge bindingene i oppdragets fødselstransaksjon (X1) — det er
        # den ENESTE transaksjonen der to bunter overhodet kan forsøkes
        # mot samme oppdrag, og indeksen skal felle den andre der.
        _kontekst(m, tenant)
        opp = _oppdrag(m, tenant, "m57_ats")
        _bind(m, tenant, en, opp)
        with pytest.raises(psycopg.errors.UniqueViolation):
            _bind(m, tenant, to, opp)
        m.rollback()
        # Rollbacken over tok også den første bindingen — den ekte veien
        # bekreftes committet, ellers målte armen bare abort-økonomien.
        _kontekst(m, tenant)
        opp = _oppdrag(m, tenant, "m57_ats")
        _bind(m, tenant, en, opp)
        m.commit()
        _kontekst(m, tenant)
        assert m.execute("SELECT status, oppdrag_id FROM inndata_artefakt"
                         " WHERE tenant=%s AND inndata_id=%s",
                         (tenant, en)).fetchone() == ("bundet", opp)
        m.rollback()
    finally:
        m.close()


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
        m.commit()
        # I fødselstransaksjonen (X1), så det er utløpet som feller.
        _kontekst(m, tenant)
        opp = _oppdrag(m, tenant, "m57_ats")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _bind(m, tenant, bunt, opp)
        m.rollback()
    finally:
        m.close()


@pg
def test_bindingen_krever_oppdragets_fodselstransaksjon(klient):
    """X1-serialiseringen (#192): bindingen kan BARE skje i oppdragets
    egen fødselstransaksjon.

    058-kjedens runde 6 aksepterte vinduet der et committet oppdrag sto
    plukkbart uten bunten sin; B-maskinen fjerner det ved konstruksjon —
    `o.fodt_xid = pg_current_xact_id()` betyr at oppdraget aldri har vært
    synlig for noen andre når bunten festes, så «plukket uten bunt» kan
    ikke observeres. Et oppdrag som alt er committet er dermed for alltid
    utenfor bindingens rekkevidde, og et avvist forsøk skal ikke koste
    kunden bunten.

    MUTASJONEN SOM DREPER DENNE: fjern `fodt_xid`-porten i
    `bind_inndata`."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        bunt = _lastet(m, tenant)
        opp = _oppdrag(m, tenant, "m57_ats")
        m.commit()          # oppdraget er født og synlig — for sent.
        _kontekst(m, tenant)
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _bind(m, tenant, bunt, opp)
        m.rollback()
        _kontekst(m, tenant)
        # Bunten står UBRENT etter avvisningen.
        assert m.execute("SELECT status, oppdrag_id FROM inndata_artefakt"
                         " WHERE tenant=%s AND inndata_id=%s",
                         (tenant, bunt)).fetchone() == ("lastet", None)
        m.rollback()
    finally:
        m.close()


@pg
def test_fodselsattesten_kan_ikke_forfalskes_med_en_update(klient):
    """Codex P1 + Cursor P1-1 på #196: X1 må måle RADENS fødsel, ikke
    tuppelversjonens.

    Første form av porten var `pg_catalog.age(o.xmin) = 0`. `xmin` bærer
    den transaksjonen som skrev DEN VERSJONEN av raden, så én UPDATE i
    bindings-transaksjonen gjorde et for lengst committet oppdrag
    «nyfødt». Veien var åpen: runtime har `GRANT SELECT, UPDATE ON
    oppdrag`, og `oppdrag_kolonnelaas` tillater eksplisitt
    `OLD.status = NEW.status` — en no-op ingen annen vakt reagerer på.
    Da var vinduet «synlig, plukkbart oppdrag uten bunt» tilbake, og det
    er nøyaktig vinduet B-maskinen finnes for å fjerne.

    Testen kjører angrepet ordrett: commit oppdraget, no-op-oppdater det i
    en ny transaksjon, bind. Med `fodt_xid` står attesten på raden og
    følger ikke versjonen, så bindingen avvises — og attesten kan heller
    ikke skrives om direkte.

    MUTASJONEN SOM DREPER DENNE: bytt X1-porten i `bind_inndata` tilbake
    til `pg_catalog.age(o.xmin) = 0` (første halvdel), eller ta
    UPDATE-grenen ut av `oppdrag_fodselsattest()` (andre halvdel)."""
    tenant, _bid, _cookie, _csrf = _rigg(klient)
    m = _migrator(tenant)
    try:
        bunt = _lastet(m, tenant)
        opp = _oppdrag(m, tenant, "m57_ats")
        m.commit()          # oppdraget er født, synlig og committet.
        _kontekst(m, tenant)
        # Nøyaktig no-op-en `oppdrag_kolonnelaas` slipper gjennom. Den
        # LYKKES — poenget er at den ikke lenger kjøper noe.
        assert m.execute("UPDATE oppdrag SET status = status"
                         " WHERE tenant=%s AND id=%s",
                         (tenant, opp)).rowcount == 1
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            _bind(m, tenant, bunt, opp)
        m.rollback()
        # Og attesten selv er ikke skrivbar — ellers ville angrepet bare
        # flyttet seg ett felt bort.
        _kontekst(m, tenant)
        with pytest.raises(psycopg.errors.RaiseException,
                           match="fødselsattest"):
            m.execute("UPDATE oppdrag SET fodt_xid ="
                      " pg_catalog.pg_current_xact_id()"
                      " WHERE tenant=%s AND id=%s", (tenant, opp))
        m.rollback()
        _kontekst(m, tenant)
        # Bunten står ubrent etter begge forsøkene.
        assert m.execute("SELECT status, oppdrag_id FROM inndata_artefakt"
                         " WHERE tenant=%s AND inndata_id=%s",
                         (tenant, bunt)).fetchone() == ("lastet", None)
        m.rollback()
    finally:
        m.close()


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
                " lager_sti, status, reservasjon_jti, idempotensnokkel,"
                " oppdrag_id, utloper, bundet_ts)"
                " VALUES (%s,'m57_ats','soknadsbunt',"
                "'application/zip',%s,10,%s,%s,'bundet',%s,%s,%s,"
                " now()+interval '1 h',now())",
                (tenant, MAKS, "a" * 64, _lagersti(tenant),
                 secrets.token_hex(32), secrets.token_hex(12), opp))
        m.rollback()
    finally:
        m.close()


def test_execute_gis_ogsaa_til_kjorerens_konfigurerte_runtimerolle():
    """Codex P1: 058 gir EXECUTE HARDKODET til `disponit`, som 017 gjør.

    En installasjon som kaller `deploy/staging/migrer.py` med en annen
    runtime-rolle fikk dermed bare `GRANT SELECT ON inndata_artefakt` fra
    kjørerens `RETTIGHETER` — og både reservasjonen og opplastingen svarte
    `permission denied`. Kjøreren er autoritativ for runtimerollens
    rettigheter (Cursor P1 på #140), så hver dør migrasjonen åpner for
    `disponit` må stå der med `{rolle}`.

    Statisk port: den kjører uten base, og signaturene sammenlignes ORDRETT
    — en signatur som gled ville gitt en stille WARNING i deploy, ikke en
    feil."""
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    # 059 (B-maskinen) DROPper og gjenskaper dørene med nye signaturer og
    # re-granter dem — det er DEN filen som eier kontrakten nå. En port
    # som fortsatt leste 058 ville krevd at migrer.py beholdt en grant
    # for en signatur som ikke lenger finnes.
    sql = (rot / "platform/core/db/migrations/059_b_maskinen.sql"
           ).read_text(encoding="utf-8")
    kjorer = (rot / "deploy/staging/migrer.py").read_text(encoding="utf-8")
    rettigheter = kjorer.split('RETTIGHETER = """', 1)[1].split('"""', 1)[0]

    def signaturer(tekst, mottaker):
        # Nyliner i signaturen er lovlig SQL og finnes i 058; normaliser
        # whitespace før sammenligning, ellers måler porten linjebrekk.
        funnet = re.findall(
            r"GRANT EXECUTE ON FUNCTION\s+(.*?)\s+TO %s\s*;" % mottaker,
            tekst, re.S)
        return {" ".join(s.split()) for s in funnet}

    doerene = signaturer(sql, "disponit")
    assert doerene, "059 gir ingen EXECUTE — porten ville vært blind"
    mangler = doerene - signaturer(rettigheter, r"\{rolle\}")
    assert not mangler, (
        "059-dører uten grant i migrer.py sin RETTIGHETER-blokk: "
        + ", ".join(sorted(mangler)))


def test_sp10_daekker_059():
    """Cursor P1-2 på #196: 059 er en BACKFILL-migrasjon, og backfiller
    krever bebodd prøvekjøring (SP-10, `ARKITEKTUR-STAENDE-PORTER.md`).

    059 gjør to masse-skrivinger tom-base-CI ikke kan se: fødselsstien
    backfilles på levende `reservert`-rader med `DISABLE TRIGGER` +
    `NO FORCE ROW LEVEL SECURITY` (FORCE RLS uten tenantkontekst gjør
    UPDATEN stille TOM, mens `ADD CONSTRAINT` skanner HELE tabellen), og
    `ADD COLUMN fodt_xid ... DEFAULT pg_current_xact_id()` skriver om
    hver eksisterende oppdragsrad. Uten seed måler prøvekjøringen at
    setningene parser.

    Porten speiler `test_sp10_daekker_049`/`_056`: CI-pekeren
    (`sp10-provekjoring.py 59` i `ci.yml`s SP-10-steg) er nå på plass
    (eierdelegert commit på #196), så porten krever den her på samme
    måte som 049/056 gjør — uten linja kjører seedet aldri i pipelinen,
    og en rød `_mal_059` ville ikke stoppet merge."""
    import re
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    ci = (rot / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8")
    assert re.search(r"sp10-provekjoring\.py 59\b", ci), (
        "SP-10-prøvekjøringen for 059 mangler i CI")
    sp10 = (rot / "deploy" / "staging" / "sp10-provekjoring.py").read_text(
        encoding="utf-8")
    assert "59: (_seed_059, _mal_059)" in sp10, (
        "059 har ingen registrert seed+måling i SEEDS")
    # Seedet må FAKTISK bebo de to klassene 059 kan felles av: en levende
    # reservasjon uten sti, og en i en tenant som ikke kan bære en sti.
    for merke in ("sp10-059-reservert", "sp10-059-utrygg",
                  "sp10-059-lastet", "sp10-059-bundet"):
        assert merke in sp10, f"SP-10-seedet for 059 mangler {merke}"
    assert re.search(r"UTRYGG_TEN\s*=\s*[\"'][^\"']*/", sp10), (
        "SP-10-seedet for 059 må bebo en tenant-ID som ikke er en lovlig"
        " stikomponent — det er raden backfillen ville felt alt på")


@pg
def test_opplastingen_krever_ferskt_snapshot():
    """Codex P1 på #196: advisory-låsen er verdiløs uten READ COMMITTED.

    Gjenlesningen under låsen (`api/inndata.py`) er det som avgjør
    skriv-eller-gjenspill. Under `REPEATABLE READ`/`SERIALIZABLE` fikserer
    DEK-oppslaget hele transaksjonens snapshot, og gjenlesningen svarer da
    fra det samme fastfrosne bildet: to opplastinger på samme jti ser
    begge `reservert`, nummer to overskriver nummer éns kanoniske fil, og
    når `registrer_inndata_lastet` felles av serialiseringsfeilen unlinker
    opprydningen nettopp den filen — en committet `lastet` rad står igjen
    uten fil.

    Målt mot en EKTE tilkobling på hvert nivå, ikke mot en attrapp: det er
    Postgres' egen `transaction_isolation` porten leser.

    MUTASJONEN SOM DREPER DENNE: fjern `_krev_ferskt_snapshot`-kallet i
    `kjor`, eller utvid settet med `repeatable read`."""
    from api.inndata import _krev_ferskt_snapshot
    from db.pg import koble
    c = koble(DSN)
    try:
        # Poolens nivå i dag — porten er ingen oppførselsendring.
        assert _krev_ferskt_snapshot(c) == "read committed"
        c.rollback()
        for niva in (psycopg.IsolationLevel.REPEATABLE_READ,
                     psycopg.IsolationLevel.SERIALIZABLE):
            c.isolation_level = niva
            with pytest.raises(ValueError, match="READ COMMITTED"):
                _krev_ferskt_snapshot(c)
            c.rollback()
    finally:
        c.close()
