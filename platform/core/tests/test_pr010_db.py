"""PR-010 (DB-laget): migrasjon 010s kontrakter — authz-trigger,
login-statusmaskin, sesjonslås, herdet oppslag, roller→scopes.

Kjøres med tenantkontekst satt INNE i transaksjonen (RLS+FORCE gjelder også
migrator), i motsetning til en psql-heredoc der konteksten forsvinner
mellom autocommit-statements.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, migrator, miljo  # noqa: F401
from .test_kjorer_og_kryptering import _nullstill  # noqa: F401
from .test_pr008 import _gjenopprett_rettigheter  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
T = "t-oidc-db"


def _ctx(conn, tenant=T):
    conn.execute("SELECT set_config('disponit.tenant',%s,true),"
                 "       set_config('disponit.aktor','test',true)", (tenant,))


def _rydd_oidc(conn):
    """Rydder tenantens OIDC-rader — de nye tabellene er ikke i test_apis
    faste ryddeliste, så hver test starter fra bunn."""
    _ctx(conn)
    conn.execute("DELETE FROM brukersesjon WHERE tenant=%s", (T,))
    conn.execute("DELETE FROM brukermedlemskap WHERE tenant=%s", (T,))
    conn.commit()


def _identitet(conn, issuer="https://idp.example", sub=None):
    sub = sub or secrets.token_hex(4)
    bid = conn.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
        " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
        " RETURNING bruker_id", (issuer, sub)).fetchone()[0]
    return bid


# ---------------------------------------------------------------------------
# authz_version-trigger (v5 §4)
# ---------------------------------------------------------------------------

@pg
def test_authz_version_bumpes_ved_rolle_og_aktiv_ikke_ved_noop(migrator):
    _rydd_oidc(migrator)
    _ctx(migrator)
    bid = _identitet(migrator)
    migrator.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller)"
        " VALUES (%s,%s,ARRAY['leser'])", (T, bid))
    v0 = migrator.execute("SELECT authz_version FROM brukermedlemskap"
                          " WHERE bruker_id=%s", (bid,)).fetchone()[0]
    # Rolleendring → bump.
    migrator.execute("UPDATE brukermedlemskap SET roller=ARRAY['leser','admin']"
                     " WHERE bruker_id=%s", (bid,))
    v1 = migrator.execute("SELECT authz_version FROM brukermedlemskap"
                          " WHERE bruker_id=%s", (bid,)).fetchone()[0]
    assert v1 == v0 + 1
    # Deaktivering → bump.
    migrator.execute("UPDATE brukermedlemskap SET aktiv=false"
                     " WHERE bruker_id=%s", (bid,))
    v2 = migrator.execute("SELECT authz_version FROM brukermedlemskap"
                          " WHERE bruker_id=%s", (bid,)).fetchone()[0]
    assert v2 == v1 + 1
    # No-op (samme verdier) → INGEN bump.
    migrator.execute("UPDATE brukermedlemskap SET aktiv=false"
                     " WHERE bruker_id=%s", (bid,))
    v3 = migrator.execute("SELECT authz_version FROM brukermedlemskap"
                          " WHERE bruker_id=%s", (bid,)).fetchone()[0]
    assert v3 == v2, "et no-op UPDATE skal ikke bumpe versjonen"
    migrator.rollback()


# ---------------------------------------------------------------------------
# login-transaksjonens statusmaskin (v3 §1 + v4 §3)
# ---------------------------------------------------------------------------

def _provider(conn, pid="p-db"):
    conn.execute(
        "INSERT INTO oidc_provider (provider_id, issuer, discovery_url,"
        " client_id, client_secret_ref, tillatte_algoritmer, aktiv)"
        " VALUES (%s,%s,%s,'cid',%s,ARRAY['RS256'],true)"
        " ON CONFLICT (provider_id) DO NOTHING",
        (pid, f"https://{pid}.example", f"https://{pid}.example/.well-known",
         f"{pid}_secret"))
    return pid


def _logintx(conn, state):
    pid = _provider(conn)
    conn.execute(
        "INSERT INTO oidc_logintransaksjon (state_hash, binding_hash, nonce,"
        " pkce_kryptert, pkce_nonce, pkce_key_id, provider_id,"
        " tenant_kandidat, retursti, utloper) VALUES"
        " (%s,%s,'n','\\x00','\\x00','k',%s,%s,'/',now()+interval '10 min')",
        (state, "b" * 64, pid, T))


@pg
def test_logintransaksjon_lovlige_og_ulovlige_overganger(migrator):
    _ctx(migrator)
    st = "a" * 64
    _logintx(migrator, st)
    # NY → KONSUMERT ok.
    migrator.execute("UPDATE oidc_logintransaksjon SET status='KONSUMERT'"
                     " WHERE state_hash=%s", (st,))
    # KONSUMERT → FULLFØRT ok.
    migrator.execute("UPDATE oidc_logintransaksjon SET status='FULLFØRT'"
                     " WHERE state_hash=%s", (st,))
    # FULLFØRT er terminal → replay/endring avvist.
    with pytest.raises(Exception, match="terminal|ulovlig"):
        migrator.execute("UPDATE oidc_logintransaksjon SET status='KONSUMERT'"
                         " WHERE state_hash=%s", (st,))
    migrator.rollback()


@pg
def test_logintransaksjon_ny_kan_ikke_hoppe_til_fullfort(migrator):
    _ctx(migrator)
    st = "c" * 64
    _logintx(migrator, st)
    with pytest.raises(Exception, match="ulovlig overgang"):
        migrator.execute("UPDATE oidc_logintransaksjon SET status='FULLFØRT'"
                         " WHERE state_hash=%s", (st,))
    migrator.rollback()


@pg
def test_atomisk_konsum_er_engangs(migrator):
    """Callback konsumerer state ATOMISK (v3 §1): UPDATE ... WHERE status='NY'
    RETURNING. Andre forsøk treffer null rader → replay avvist."""
    _ctx(migrator)
    st = "d" * 64
    _logintx(migrator, st)
    r1 = migrator.execute(
        "UPDATE oidc_logintransaksjon SET status='KONSUMERT'"
        " WHERE state_hash=%s AND status='NY' AND utloper > now()"
        " RETURNING tenant_kandidat", (st,)).fetchone()
    assert r1 is not None, "første konsum skal lykkes"
    r2 = migrator.execute(
        "UPDATE oidc_logintransaksjon SET status='KONSUMERT'"
        " WHERE state_hash=%s AND status='NY' AND utloper > now()"
        " RETURNING tenant_kandidat", (st,)).fetchone()
    assert r2 is None, "andre konsum (replay) skal treffe null rader"
    migrator.rollback()


# ---------------------------------------------------------------------------
# brukersesjon: kolonnelås + herdet oppslag (v1 §1 + v2)
# ---------------------------------------------------------------------------

def _sesjon(conn, sesjon_hash, csrf_hash="e" * 64, minutter_gammel=0,
            utloper_timer=12, tilbakekalt=False):
    bid = _identitet(conn)
    conn.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller)"
        " VALUES (%s,%s,ARRAY['leser']) ON CONFLICT DO NOTHING", (T, bid))
    conn.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
        " tilbakekalt) VALUES (%s,%s,%s,1,%s,"
        " now() - make_interval(mins => %s),"
        " now() - make_interval(mins => %s),"
        " now() + make_interval(hours => %s), %s)",
        (sesjon_hash, T, bid, csrf_hash, minutter_gammel, minutter_gammel,
         utloper_timer, tilbakekalt))
    return bid


@pg
def test_slaa_opp_sesjon_gir_frisk_men_ikke_utlopt_inaktiv_tilbakekalt(
        migrator):
    _ctx(migrator)
    frisk = "1" * 64
    _sesjon(migrator, frisk)
    migrator.commit()
    # Herdet oppslag returnerer tenant/bruker/snapshot — aldri hashene.
    rad = migrator.execute("SELECT tenant, bruker_id, authz_snapshot"
                           " FROM slaa_opp_sesjon(%s)", (frisk,)).fetchone()
    assert rad is not None and rad[0] == T
    # Inaktiv > 30 min → ingen rad.
    inaktiv = "2" * 64
    _ctx(migrator)
    _sesjon(migrator, inaktiv, minutter_gammel=31)
    migrator.commit()
    assert migrator.execute("SELECT tenant FROM slaa_opp_sesjon(%s)",
                            (inaktiv,)).fetchone() is None
    # Tilbakekalt → ingen rad.
    tk = "3" * 64
    _ctx(migrator)
    _sesjon(migrator, tk, tilbakekalt=True)
    migrator.commit()
    assert migrator.execute("SELECT tenant FROM slaa_opp_sesjon(%s)",
                            (tk,)).fetchone() is None
    # Absolutt utløp (utloper i fortiden) → ingen rad.
    utlopt = "4" * 64
    _ctx(migrator)
    _sesjon(migrator, utlopt, utloper_timer=-1)
    migrator.commit()
    assert migrator.execute("SELECT tenant FROM slaa_opp_sesjon(%s)",
                            (utlopt,)).fetchone() is None
    _ctx(migrator)
    migrator.execute("DELETE FROM brukersesjon WHERE tenant=%s", (T,))
    migrator.commit()


@pg
def test_sesjon_er_uforanderlig_unntatt_siste_bruk_og_tilbakekalt(migrator):
    _rydd_oidc(migrator)
    _ctx(migrator)
    h = "5" * 64
    bid = _sesjon(migrator, h)
    migrator.commit()
    # Endring av bruker_id → avvist av kolonnelåsen. (Bruk en EKSISTERENDE
    # bruker_id så det er kolonnelåsen, ikke FK-en, som beviselig stopper.)
    _ctx(migrator)
    annen = _identitet(migrator, sub="annen-sub")
    with pytest.raises(Exception, match="kun siste_bruk"):
        migrator.execute("UPDATE brukersesjon SET bruker_id=%s"
                         " WHERE sesjon_id_hash=%s", (annen, h))
    migrator.rollback()
    _ctx(migrator)
    # tilbakekalt=true tillatt; å gjenopplive avvist.
    migrator.execute("UPDATE brukersesjon SET tilbakekalt=true"
                     " WHERE sesjon_id_hash=%s", (h,))
    with pytest.raises(Exception, match="gjenopplives"):
        migrator.execute("UPDATE brukersesjon SET tilbakekalt=false"
                         " WHERE sesjon_id_hash=%s", (h,))
    migrator.rollback()
    _ctx(migrator)
    migrator.execute("DELETE FROM brukersesjon WHERE sesjon_id_hash=%s", (h,))
    migrator.commit()


# ---------------------------------------------------------------------------
# provider-kontrakter: 'none'-algoritme og credential-ref-format avvist
# ---------------------------------------------------------------------------

@pg
def test_provider_avviser_none_algoritme_og_sti_i_credential_ref(migrator):
    _ctx(migrator)
    with pytest.raises(Exception):
        migrator.execute(
            "INSERT INTO oidc_provider (provider_id,issuer,discovery_url,"
            " client_id,client_secret_ref,tillatte_algoritmer)"
            " VALUES ('bad1','https://i1','https://i1/d','c','ref',"
            " ARRAY['RS256','none'])")
    migrator.rollback()
    _ctx(migrator)
    with pytest.raises(Exception):
        migrator.execute(
            "INSERT INTO oidc_provider (provider_id,issuer,discovery_url,"
            " client_id,client_secret_ref,tillatte_algoritmer)"
            " VALUES ('bad2','https://i2','https://i2/d','c','../etc/passwd',"
            " ARRAY['RS256'])")
    migrator.rollback()


# ---------------------------------------------------------------------------
# roller → scopes (v5 §4)
# ---------------------------------------------------------------------------

def test_rolle_scopes_er_kjente_og_leser_ikke_sikkerhet():
    from api.autorisasjon import scopes_for_roller, ROLLE_TIL_SCOPES
    from api.app import BROWSER_MUTASJONSSCOPES, LESESCOPES
    # Ingen rolle kan gi et scope utenfor lese-mengden + PR-012s muterende
    # unntaksbehandlingsscopes (de eneste browser-mutasjonene).
    kjente = LESESCOPES | BROWSER_MUTASJONSSCOPES
    for scopes in ROLLE_TIL_SCOPES.values():
        assert scopes <= kjente, f"ukjent scope: {scopes - kjente}"
    # Kun leseroller er rene lese-roller; godkjenner er den muterende.
    for rolle in ("leser", "sikkerhet", "admin"):
        assert ROLLE_TIL_SCOPES[rolle] <= LESESCOPES
    assert scopes_for_roller(["leser"]) == {"decisions:read",
                                            "exceptions:read", "policy:read"}
    assert "security:read" not in scopes_for_roller(["leser"])
    assert "security:read" in scopes_for_roller(["sikkerhet"])
    # Ukjent rolle → ingen scopes (default-deny).
    assert scopes_for_roller(["finnesikke"]) == frozenset()
    # Union av flere roller.
    assert scopes_for_roller(["leser", "sikkerhet"]) == \
        scopes_for_roller(["sikkerhet"])
