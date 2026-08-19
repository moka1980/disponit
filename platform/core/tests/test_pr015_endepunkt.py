"""PR-015 §3/§4 — POST /v1/unntak/{id}/domeneattestasjon, ende til ende.

Funksjonslogikken (opptelling, terskler, foreldelse) er bevist på DB-nivå i
test_pr015_operativt_lag. Her bevises HTTP-kontrakten gjennom en EKTE
autentisert browserøkt med CSRF: at scopet er eget og fail-closed (port 13), at
den ikke-avgjorte tilstanden er LEGIBEL og ikke stille (§4 siste kule), og at
de to feilveiene PR-015 legger til faktisk produseres.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo, migrator, token  # noqa: F401
from .test_api import dekker
from .test_pr012_behandle import TEN, _medlem  # noqa: F401
from .test_pr012_gate14a import _browsersesjon
from .test_m37 import _sett_kontekst

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _host():
    return "e" + secrets.token_hex(6) + ".example"


def _mig():
    """Ren migrator: bordtilgang (lesing + saksskriving)."""
    from db.pg import koble
    return koble(MIGRATOR_DSN)


def _admin():
    """migrator SET ROLE domains_admin: EXECUTE på funksjonene, INGEN bord.

    Skillet er ikke pynt — arbeider-/adminrollen skal ikke kunne røre
    `domenekontroll` direkte, og en test som brukte én forbindelse til begge
    deler ville skjult nettopp det.
    """
    c = _mig()
    c.execute("SET ROLE disponit_domains_admin")
    c.commit()
    return c


def _overtakelsessak(hostname, taper="taper-tenant-pr015"):
    """Kjør taper→TEN-overtakelsen. -> unntak_id.

    041: saken lages av `sikre_overtakelsessak()` i SAMME transaksjon som
    konflikten — fixturen skal ikke (og kan ikke, port 37) lage den selv.
    Den slås opp der den bor: på plattformtenanten, som kolonner.
    """
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (taper, hostname))
        a.commit()
        svar = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (TEN, hostname)).fetchone()[0]
        a.commit()
    finally:
        a.close()
    assert svar.startswith("konflikt:"), svar
    m = _mig()
    try:
        _sett_kontekst(m, "__plattform_domener")
        sak = int(m.execute(
            "SELECT id FROM unntak WHERE hostname_ref=%s"
            "  AND sakskilde='domeneovertakelse' AND NOT terminal",
            (hostname,)).fetchone()[0])
        m.rollback()
        return sak
    finally:
        m.close()


def _post(klient, sak, cookie, csrf, **over):
    from api import sesjon as sesjonmodul
    body = {"utfall": "godkjenn", "vinnende_tenant": TEN}
    body.update(over)
    return klient.post(f"/v1/unntak/{sak}/domeneattestasjon", json=body,
                       headers={"X-Disponit-CSRF": csrf},
                       cookies={sesjonmodul.C_SESJON: cookie})


@pg
def test_uautentisert_attestasjon_avvises(klient):
    r = klient.post("/v1/unntak/1/domeneattestasjon",
                    json={"utfall": "godkjenn", "vinnende_tenant": "t"})
    assert r.status_code == 401
    assert r.json()["feil"] == "token_ugyldig"


@pg
def test_ukjent_utfall_er_feilformet(klient):
    """Formkontrollen ligger FØR autentisering — og `utfall` er en LUKKET enum."""
    r = klient.post("/v1/unntak/1/domeneattestasjon",
                    json={"utfall": "kanskje", "vinnende_tenant": "t"})
    assert r.status_code == 400
    assert r.json()["feil"] == "request_feilformet"


@pg
def test_port13_uten_domains_adjudicate_nektes_selv_med_exceptions_handle(klient):
    """Port 13: avgjørelse uten `domains:adjudicate` → NEKTET, selv med
    `exceptions:handle`.

    `godkjenner` bærer exceptions:approve/reject/escalate og er den rollen som
    behandler unntakskøen. Den skal ikke kunne avgjøre hvem plattformen
    autoriserer for et domene — det er hele grunnen til at scopet er eget.
    """
    h = _host()
    sak = _overtakelsessak(h)
    bid = _medlem(None, "pr015-godkjenner", roller="ARRAY['godkjenner']")
    cookie, csrf = _browsersesjon(bid)
    r = _post(klient, sak, cookie, csrf)
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "scope_mangler"


@pg
def test_en_attestasjon_gir_legibel_krever_to(klient):
    """§4 siste kule: fail-closed skal SIES, ikke oppleves som stillhet.

    Svaret bærer antallet avgitte attestasjoner, så en tenant med bare én
    autorisert aktør ser HVORFOR det ikke gikk.
    """
    h = _host()
    sak = _overtakelsessak(h)
    bid = _medlem(None, "pr015-adj1", roller="ARRAY['domeneadjudikator']")
    cookie, csrf = _browsersesjon(bid)
    r = _post(klient, sak, cookie, csrf)
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["feil"] == "krever_to_attestasjoner"
    assert body["avgitt"] == 1 and body["krever"] == 2
    assert body["hostname"] == h


@pg
@dekker("dobbel_attestasjon")
def test_samme_aktor_to_ganger_gir_dobbel_attestasjon(klient):
    """Port 15 på HTTP-nivå: andre stemme fra samme aktør → `dobbel_attestasjon`.

    Avvist av primærnøkkelen i basen; endepunktet oversetter unikbruddet til
    en lukket feilkode i stedet for en generisk 500.
    """
    h = _host()
    sak = _overtakelsessak(h)
    bid = _medlem(None, "pr015-dobbel", roller="ARRAY['domeneadjudikator']")
    cookie, csrf = _browsersesjon(bid)
    r1 = _post(klient, sak, cookie, csrf)
    assert r1.status_code == 409 and r1.json()["feil"] == "krever_to_attestasjoner"
    r2 = _post(klient, sak, cookie, csrf)
    assert r2.status_code == 409, r2.text
    assert r2.json()["feil"] == "dobbel_attestasjon"


@pg
@dekker("attestasjon_avvist")
def test_attestasjon_pa_avgjort_sak_avvises(klient):
    """`attestasjon_avvist`: saken er ikke lenger i `avklaring_kreves`.

    Etter en avvisning er raden `tilbakekalt`, og motoren nekter å ta imot
    flere stemmer på den. Attestasjonsraden som alt er avgitt, står igjen som
    evidens — den ryddes ikke bort av at saken lukkes.
    """
    h = _host()
    sak = _overtakelsessak(h)
    m = _mig()
    try:
        _sett_kontekst(m, TEN)
        gen = int(m.execute(
            "SELECT autorisasjonsgenerasjon FROM domenekontroll"
            " WHERE tenant=%s AND hostname=%s", (TEN, h)).fetchone()[0])
    finally:
        m.close()
    # Avgjør saken (avvis krever ÉN attestasjon) rett på motoren. Stemmen må
    # ha en ekte prinsipal bak seg — motoren reautoriserer de tellende
    # aktørene mot `brukermedlemskap` (Codex).
    avviser = _medlem(None, "pr015-avvis", roller="ARRAY['domeneadjudikator']")
    a = _admin()
    try:
        a.execute(
            "SELECT avgi_overtakelse_attestasjon(%s,%s,%s,'avvis',%s,%s,%s,%s)",
            (TEN, sak, h, TEN, "aktor-avvis", gen, avviser))
        a.commit()
    finally:
        a.close()

    bid = _medlem(None, "pr015-etterpa", roller="ARRAY['domeneadjudikator']")
    cookie, csrf = _browsersesjon(bid)
    r = _post(klient, sak, cookie, csrf)
    assert r.status_code == 409, r.text
    assert r.json()["feil"] == "attestasjon_avvist"


# ===========================================================================
# Adjudikatorkøen (041 §5) — omfanget, ikke bare synligheten (Codex P1).
# ===========================================================================

#: En FREMMED kundetenant med sin egen adjudikator. Poenget er nettopp at den
#: er en helt vanlig kunde: `domeneadjudikator` er en kunde-lokal rolle enhver
#: tenant kan gi sin egen bruker, så «har rollen» kan aldri bety «ser alt».
FREMMED = "t-adj-fremmed"


def _adjudikator_i(tenant, sub):
    """Aktiv `domeneadjudikator` i `tenant`. -> (sesjonscookie, csrf).

    Egen helper fordi `_medlem`/`_browsersesjon` er bundet til TEN — og det
    er nettopp en ANNEN tenant enn utfordreren som måles her.
    """
    import secrets as _s
    from db.pg import koble, sett_kontekst
    from api import sesjon as sesjonmodul
    from .test_pr010_db import _identitet
    cookie, csrf = _s.token_urlsafe(24), _s.token_urlsafe(24)
    m = _mig()
    try:
        sett_kontekst(m, tenant, "sys", "r0")
        bid = _identitet(m, sub=f"{tenant}-{sub}")
        ver = m.execute(
            "INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
            " VALUES (%s,%s,ARRAY['domeneadjudikator'])"
            " ON CONFLICT (tenant,bruker_id) DO UPDATE SET"
            " roller=EXCLUDED.roller, aktiv=true"
            " RETURNING authz_version", (tenant, bid)).fetchone()[0]
        m.execute(
            "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
            " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
            " tilbakekalt) VALUES (%s,%s,%s,%s,%s, now(), now(),"
            " now()+interval '12 hour', false)",
            (sesjonmodul._hash(cookie), tenant, bid, ver,
             sesjonmodul._hash(csrf)))
        m.commit()
        return cookie, csrf
    finally:
        m.close()


@pg
def test_adjudikatorkoen_er_utfordrerens_ikke_klyngens(klient):
    """Codex P1: rollen gir synligheten, tenanten gir omfanget.

    Saken er TENs (TEN er utfordreren). En adjudikator hos en HELT ANNEN
    kunde har det samme scopet og den samme databaserollen — og skal likevel
    ikke se saken, for `avgi_overtakelse_attestasjon` (019) autoriserer bare
    medlemmer av utfordrerens tenant. Uten filteret var køen klyngens: den
    ga fremmede både vertsnavnet og BEGGE partsidentitetene for tvister de
    aldri kunne røre.
    """
    h = _host()
    sak = _overtakelsessak(h)

    from api import sesjon as sesjonmodul
    fremmed, _ = _adjudikator_i(FREMMED, "utenfor")
    r = klient.get("/v1/domeneovertakelse/saker",
                   cookies={sesjonmodul.C_SESJON: fremmed})
    assert r.status_code == 200, r.text
    verter = [s["hostname"] for s in r.json()["saker"]]
    assert h not in verter, "fremmed adjudikator ser en annen tenants sak"

    # ... og utfordrerens egen adjudikator ser den, med begge parter: køen
    # er avgrenset, ikke avskrudd.
    egen, _ = _adjudikator_i(TEN, "egen")
    r = klient.get("/v1/domeneovertakelse/saker",
                   cookies={sesjonmodul.C_SESJON: egen})
    assert r.status_code == 200, r.text
    mine = [s for s in r.json()["saker"] if s["hostname"] == h]
    assert len(mine) == 1, r.text
    assert mine[0]["unntak_id"] == sak
    assert mine[0]["utfordrer_tenant"] == TEN
    assert mine[0]["tapt_tenant"] == "taper-tenant-pr015"


@pg
def test_adjudikatorkoen_er_paginert_og_cursoren_er_tenantbundet(klient):
    """Codex P2: køen er UBUNDET i tid — saker står åpne til et menneske
    avgjør dem — så siden må være bundet.

    Samme kontrakt som `/v1/unntak`: `limit` ≤ 100, signert v2-cursor
    bundet til tenant/endepunkt/retning/filtre, ærlig keyset. Retningen er
    `asc`: en adjudikatorkø tømmes fra bunnen.
    """
    from api import sesjon as sesjonmodul
    h1, h2 = _host(), _host()
    _overtakelsessak(h1)
    _overtakelsessak(h2)
    egen, _ = _adjudikator_i(TEN, "paginering")
    kake = {sesjonmodul.C_SESJON: egen}

    def side(cursor=None):
        q = "?limit=1" + (f"&cursor={cursor}" if cursor else "")
        r = klient.get("/v1/domeneovertakelse/saker" + q, cookies=kake)
        assert r.status_code == 200, r.text
        return r.json()

    sett, cursor, runder = [], None, 0
    while True:
        d = side(cursor)
        assert len(d["saker"]) <= 1, d
        sett += [s["hostname"] for s in d["saker"]]
        cursor = d["neste_cursor"]
        runder += 1
        if cursor is None or runder > 50:
            break
    assert cursor is None, "køen tok aldri slutt — keysettet står stille"
    assert h1 in sett and h2 in sett, sett
    assert len(sett) == len(set(sett)), f"duplikat over sidene: {sett}"

    # Cursoren er TENANTBUNDET: en fremmed sesjon kan ikke bruke den.
    d = side()
    if d["neste_cursor"]:
        fremmed, _ = _adjudikator_i(FREMMED, "laant-cursor")
        r = klient.get(
            f"/v1/domeneovertakelse/saker?limit=1&cursor={d['neste_cursor']}",
            cookies={sesjonmodul.C_SESJON: fremmed})
        assert r.status_code == 400, r.text
        assert r.json()["feil"] == "cursor_ugyldig"

    # ... og taket er ekte: en `limit` utenfor kontrakten er en formfeil.
    r = klient.get("/v1/domeneovertakelse/saker?limit=101", cookies=kake)
    assert r.status_code == 400 and r.json()["feil"] == "request_feilformet"


@pg
def test_saken_som_skifter_til_meg_kommer_foran_cursoren(klient):
    """Codex P2: en sak kommer inn i MIN kø på to måter — den opprettes,
    eller A→B→C gir den meg.

    Den andre veien lager ingen ny rad: `sikre_overtakelsessak` skifter
    utfordrer på den EKSISTERENDE saken, og `ts` er kolonnelåst (§11) og
    blir stående. Med `(ts, id)` som keyset la saken seg da BAK en cursor
    jeg alt hadde fått utstedt — og forsvant fra hver gjenstående side, uten
    noen gang å ha stått på en tidligere. En tvist om mitt eget vertsnavn,
    usynlig i min egen kø, helt til noen tømte den og begynte forfra.

    Nøkkelen er derfor `saksrevisjon_ts`, som går fram med skiftet (§6
    håndhever det). «Eldste først» betyr «eldst som DENNE konflikten».

    MUTASJONEN SOM DREPER DENNE: sett keysettet tilbake til `(ts, id)`, eller
    la `sikre_overtakelsessak` beholde `saksrevisjon_ts` over et skifte.
    """
    from api import sesjon as sesjonmodul
    h_gammel, h_ny = _host(), _host()

    # 1) En ELDRE sak som tilhører en annen utfordrer enn meg.
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  ("taper-tenant-pr015", h_gammel))
        a.commit()
        svar = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (FREMMED, h_gammel)).fetchone()[0]
        a.commit()
        assert svar.startswith("konflikt:"), svar

        # 2) ... og en FERSKERE sak som er min.
        _overtakelsessak(h_ny)

        egen, _ = _adjudikator_i(TEN, "skifte-cursor")
        kake = {sesjonmodul.C_SESJON: egen}

        def side(cursor=None):
            q = "?limit=1" + (f"&cursor={cursor}" if cursor else "")
            r = klient.get("/v1/domeneovertakelse/saker" + q, cookies=kake)
            assert r.status_code == 200, r.text
            return r.json()

        # 3) Jeg blar til jeg har sett den ferske — cursoren står nå ETTER
        #    den, altså etter alt som er eldre enn den.
        cursor, funnet = None, False
        for _ in range(60):
            d = side(cursor)
            cursor = d["neste_cursor"]
            if h_ny in [s["hostname"] for s in d["saker"]]:
                funnet = True
                break
            if cursor is None:
                break
        assert funnet and cursor, "fant aldri den ferske saken i egen kø"

        # 4) NÅ tar jeg over det gamle vertsnavnet: A→B→C. Saken skifter
        #    utfordrer — samme rad, samme `ts`, ny revisjon.
        svar = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (TEN, h_gammel)).fetchone()[0]
        a.commit()
        assert svar.startswith("konflikt:"), svar
    finally:
        a.close()

    m = _mig()
    try:
        _sett_kontekst(m, "__plattform_domener")
        rad = m.execute(
            "SELECT utfordrer_tenant, saksrevisjon, ts < saksrevisjon_ts"
            "  FROM unntak WHERE hostname_ref=%s"
            "   AND sakskilde='domeneovertakelse' AND NOT terminal",
            (h_gammel,)).fetchone()
        m.rollback()
    finally:
        m.close()
    assert rad is not None and rad[0] == TEN, rad
    assert rad[1] >= 1, f"skiftet ga ingen ny revisjon: {rad}"
    assert rad[2] is True, "saksrevisjon_ts fulgte ikke skiftet"

    # 5) ... og den kommer FORAN cursoren jeg alt hadde fått.
    sett, c = [], cursor
    for _ in range(60):
        d = side(c)
        sett += [s["hostname"] for s in d["saker"]]
        c = d["neste_cursor"]
        if c is None:
            break
    assert h_gammel in sett, \
        f"saken som skiftet til meg lå bak cursoren og ble aldri vist: {sett}"


@pg
def test_attestasjon_pa_fremmed_sak_er_ikke_funnet(klient):
    """Samme rot i attestasjonsveien: sak-id-rommet er ikke et orakel.

    En fremmed adjudikator kunne før skille «finnes ikke» fra «finnes, men
    er ikke din» — og oppslaget ga hen vertsnavn og utfordrer for saken.
    Nå er begge `ikke_funnet`.
    """
    h = _host()
    sak = _overtakelsessak(h)
    cookie, csrf = _adjudikator_i(FREMMED, "orakel")
    r = _post(klient, sak, cookie, csrf)
    assert r.status_code == 404, r.text
    assert r.json()["feil"] == "ikke_funnet"
    # Nøyaktig samme svar for en sak som ikke finnes i det hele tatt.
    r2 = _post(klient, sak + 10_000_000, cookie, csrf)
    assert r2.status_code == 404 and r2.json()["feil"] == "ikke_funnet"


# ===========================================================================
# Opplastingskapabilitet ved claim (§5) — port 21, 22.
# ===========================================================================

@pg
def test_port22_uten_registrert_artefakttype_ingen_kapabilitet(migrator, miljo,
                                                               token):
    """Port 22: oppdrag uten registrert artefakttype → claim OK, INGEN kapabilitet.

    Og port 21s halvdel som kan måles her: `opplasting` er et SEPARAT felt ved
    siden av kvitteringskapabiliteten, ikke utledet av den. Er det `null`, har
    modulen ingen opplastingsrett — «en modul som ikke skal laste opp, får ikke
    lov», og claimen lykkes likevel.
    """
    from starlette.testclient import TestClient
    from api.app import lag_app
    from .test_m37 import _lag_sak, _lag_oppdrag, TENANT as M37_TENANT

    sak, logg = _lag_sak(migrator, M37_TENANT)
    _lag_oppdrag(migrator, M37_TENANT, sak, logg)

    app = lag_app(DSN)
    with TestClient(app) as c:
        tok, _ = token(rolle="eiermodul:reinnsending",
                       scopes=("orders:execute:purring.",))
        r = c.post("/v1/oppdrag/claim", json={},
                   headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    kropp = r.json()
    assert kropp["opplasting"] is None, (
        "det ble utstedt opplastingskapabilitet uten registrert artefakttype")
    # Kvitteringskapabiliteten er upåvirket — de to er uavhengige.
    assert kropp["kvittering_jti"]
    assert kropp["kvittering_jti"] != kropp.get("opplasting")


@pg
def test_ukjent_sak_er_ikke_funnet(klient):
    """En sak som ikke er en overtakelsessak låner seg ikke adjudikasjonsveien.

    Oppslaget bærer kategori/handling/kilde, så en fremmed rad i det DELTE
    idempotensnavnerommet kan ikke matche.
    """
    bid = _medlem(None, "pr015-ukjent", roller="ARRAY['domeneadjudikator']")
    cookie, csrf = _browsersesjon(bid)
    r = _post(klient, 99_999_999, cookie, csrf)
    assert r.status_code == 404, r.text
    assert r.json()["feil"] == "ikke_funnet"
