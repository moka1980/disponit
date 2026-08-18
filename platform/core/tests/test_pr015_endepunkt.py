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
