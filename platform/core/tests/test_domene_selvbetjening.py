"""039 — selvbetjent domeneverifisering.

Snittet som måles: API-et kan bare UTSTEDE (aldri bekrefte — det kjente
tokenet), arbeiderfunksjonene beviser mot DB-holdt hash, og
statusoverganger eies fortsatt av `verifiser_domenekontroll`.
Alle tester konstruerer egen tilstand.
"""
import hashlib
import secrets
import time

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, ANNEN_TENANT,  # noqa: F401
                       migrator, miljo)
from .test_api import dekker
from .test_m37 import _sett_kontekst
from .test_outbox_bestilling import (_adminsesjon, _rt, app,  # noqa: F401
                                     klient)

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _som_eier(migrator_, sql, args):
    migrator_.execute("SET LOCAL ROLE disponit_domene_eier")
    rad = migrator_.execute(sql, args).fetchone()
    migrator_.execute("RESET ROLE")
    return rad


def _utsted(migrator_, hostname, token=None):
    token = token or secrets.token_hex(32)
    h = hashlib.sha256(token.encode()).hexdigest()
    _sett_kontekst(migrator_, TENANT)
    _som_eier(migrator_, "SELECT utsted_challenge(%s,%s,false,%s,'test')",
              (TENANT, hostname, h))
    migrator_.commit()
    return token


@pg
def test_bekreft_krever_beviset_i_txt(migrator):
    """DB-en holder beviset: feil TXT → exception (ingen påstand om
    suksess); riktig TXT → verifisert; nytt kall → idempotent 'verifisert'
    (dobbeltplukk er et JA, aldri en dobbel overgang)."""
    vert = f"kunde{secrets.token_hex(3)}.example"
    token = _utsted(migrator, vert)
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                  (TENANT, vert, ["feil-verdi", "v=spf1 -all"]))
    migrator.rollback()
    _sett_kontekst(migrator, TENANT)
    svar = _som_eier(migrator,
                     "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                     (TENANT, vert, ["v=spf1 -all", token]))[0]
    migrator.commit()
    assert svar == "verifisert"
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, siste_vellykkede_revalidering IS NOT NULL"
        " FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (TENANT, vert)).fetchone()
    svar2 = _som_eier(migrator,
                      "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                      (TENANT, vert, ["hva-som-helst"]))[0]
    migrator.rollback()
    assert rad == ("verifisert", True), rad
    assert svar2 == "verifisert"


@pg
def test_utlopt_challenge_beviser_ingenting(migrator):
    vert = f"gammel{secrets.token_hex(3)}.example"
    token = _utsted(migrator, vert)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE domenekontroll SET"
                     " challenge_utloper=now()-interval '1 hour'"
                     " WHERE tenant=%s AND hostname=%s", (TENANT, vert))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                  (TENANT, vert, [token]))
    migrator.rollback()


@pg
def test_bekreft_overstyrer_aldri_en_avklaring(migrator):
    """En rad utenfor `ventende` (her: tilbakekalt) flyttes ALDRI av et
    DNS-bevis — bare M-37-avgjørelsen kan det. Svaret er status, ikke en
    overgang."""
    vert = f"laast{secrets.token_hex(3)}.example"
    token = _utsted(migrator, vert)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE domenekontroll SET status='tilbakekalt'"
                     " WHERE tenant=%s AND hostname=%s", (TENANT, vert))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    svar = _som_eier(migrator,
                     "SELECT bekreft_domenechallenge(%s,%s,'t',%s)",
                     (TENANT, vert, [token]))[0]
    status = migrator.execute(
        "SELECT status FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (TENANT, vert)).fetchone()[0]
    migrator.rollback()
    assert svar == "tilbakekalt"
    assert status == "tilbakekalt"


@pg
def test_ventende_plukket_er_ferskt_og_lukket(migrator):
    v1 = f"fersk{secrets.token_hex(3)}.example"
    v2 = f"utgatt{secrets.token_hex(3)}.example"
    _utsted(migrator, v1)
    _utsted(migrator, v2)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE domenekontroll SET"
                     " challenge_utloper=now()-interval '1 hour'"
                     " WHERE tenant=%s AND hostname=%s", (TENANT, v2))
    migrator.commit()
    rader = [r[1] for r in _alle_ventende(migrator)]
    assert v1 in rader
    assert v2 not in rader, "utløpt challenge skal aldri plukkes"


@pg
def test_plukket_roterer_forbi_ubesvarte_utfordringer(migrator):
    """Codex P1: utvalget var en stabil `ORDER BY challenge_utstedt LIMIT k`.
    Står det flere gyldige utfordringer enn taket og de eldste kundene aldri
    publiserer TXT-posten sin, returnerer den de SAMME radene hvert femte
    minutt — en manglende post flytter jo ingenting, så raden blir stående
    `ventende` i opptil syv døgn. Kundene bak taket ble aldri sett på.

    Nå stempler plukket radene det tar (`challenge_forsokt`) og tar de minst
    nylig forsøkte først: kohorten roterer, og alle kommer gjennom taket.

    MUTASJONEN SOM DREPER DENNE: fjern stempelet (gjør utvalget til et rent
    SELECT igjen), eller sorter på `challenge_utstedt` alene.
    """
    # Utvalget er kryss-tenant og basen deles med resten av suiten: sett alle
    # ANDRE ventende rader «nettopp forsøkt» (fram i tid), så rotasjonen som
    # måles er vår egen. Mine rader lages ETTER og står dermed uforsøkte.
    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    migrator.execute("UPDATE domenekontroll"
                     " SET challenge_forsokt = now() + interval '1 hour'"
                     " WHERE status = 'ventende'")
    migrator.execute("RESET ROLE")
    migrator.commit()

    verter = [f"rot{i}{secrets.token_hex(3)}.example" for i in range(3)]
    for v in verter:
        _utsted(migrator, v)
    # Ingen av dem publiserer noe: hver runde plukker ÉN, og over tre runder
    # skal alle tre ha vært innom.
    sett = []
    for _ in range(3):
        migrator.execute("SET LOCAL ROLE disponit_domene_eier")
        rad = migrator.execute(
            "SELECT tenant, hostname FROM ventende_domenechallenges(1)"
        ).fetchone()
        migrator.execute("RESET ROLE")
        migrator.commit()
        sett.append(rad[1])
    assert set(verter) <= set(sett), \
        f"plukket roterer ikke — så bare {sett} av {verter}"

    # ...og en NY utfordring på et alt forsøkt navn går først igjen: «sist
    # forsøkt» er forsøket på DENNE utfordringen, ikke på navnet.
    _utsted(migrator, verter[0])
    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    forst = migrator.execute(
        "SELECT hostname FROM ventende_domenechallenges(1)").fetchone()[0]
    migrator.execute("RESET ROLE")
    migrator.commit()
    assert forst == verter[0], forst


def _alle_ventende(migrator_):
    migrator_.execute("SET LOCAL ROLE disponit_domene_eier")
    rader = migrator_.execute(
        "SELECT tenant, hostname FROM ventende_domenechallenges(500)"
    ).fetchall()
    migrator_.execute("RESET ROLE")
    migrator_.rollback()
    return rader


@pg
def test_runtime_kan_utstede_men_aldri_bekrefte(migrator):
    """Sikkerhetssnittet: API-et (runtime) genererte tokenet og skal
    derfor ALDRI kunne bekrefte det — ellers var DNS-beviset valgfritt."""
    vert = f"snitt{secrets.token_hex(3)}.example"
    token = secrets.token_hex(32)
    h = hashlib.sha256(token.encode()).hexdigest()
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                   (TENANT, vert, h))
        rt.commit()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT bekreft_domenechallenge(%s,%s,'rt',%s)",
                       (TENANT, vert, [token]))
        rt.rollback()
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT * FROM ventende_domenechallenges(10)")
        rt.rollback()
    finally:
        rt.close()


@pg
def test_utstedelsen_er_bundet_til_kallerens_tenantkontekst(migrator):
    """Codex P1: 016s `utsted_challenge` er SECURITY DEFINER og STOLER på
    `p_tenant`. Gitt rått til den delte runtime-rollen var den et kryss-tenant
    skriveprimitiv — bytt `challenge_token_hash` på en annen tenants
    `ventende` rad, og DNS-beviset holdes mot ditt token, FORCE RLS til tross.

    Runtime får derfor bare den guardede formen, og den binder `p_tenant` til
    konteksten kalleren faktisk står i. Fail-closed: uten kontekst er det
    ingen tenant å være lik.

    MUTASJONEN SOM DREPER DENNE: fjern `krev_tenantkontekst`-kallet fra
    `utsted_challenge_selvbetjent`, eller gi runtime den rå formen tilbake.
    """
    vert = f"kryss{secrets.token_hex(3)}.example"
    h = hashlib.sha256(secrets.token_hex(32).encode()).hexdigest()
    rt = _rt()
    try:
        # 1) Den rå formen er ikke lenger runtimes å kalle i det hele tatt.
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute("SELECT utsted_challenge(%s,%s,false,%s,'rt')",
                       (TENANT, vert, h))
        rt.rollback()

        # 2) Innpakningen nekter en FREMMED tenant selv med egen kontekst satt.
        _sett_kontekst(rt, TENANT)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                (ANNEN_TENANT, vert, h))
        rt.rollback()

        # 3) ...og uten kontekst i det hele tatt (fail-closed).
        rt.execute("SELECT set_config('disponit.tenant','',true)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            rt.execute(
                "SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                (TENANT, vert, h))
        rt.rollback()
    finally:
        rt.close()

    # Ingen rad ble skapt for noen av tenantene.
    for t in (TENANT, ANNEN_TENANT):
        _sett_kontekst(migrator, t)
        assert migrator.execute(
            "SELECT count(*) FROM domenekontroll WHERE hostname=%s",
            (vert,)).fetchone()[0] == 0
        migrator.rollback()


@pg
def test_http_utsted_og_liste(migrator, klient):
    """POST /v1/domener → 201 med TXT-verdien (vist ÉN gang; kun hashen i
    basen); GET /v1/domener viser raden; lukket kropp og scope."""
    from api import sesjon as sesjonmodul
    cookie, csrf = _adminsesjon()
    vert = f"selv{secrets.token_hex(3)}.example"
    r = klient.post("/v1/domener", json={"hostname": vert.upper()},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 201, r.text
    svar = r.json()
    assert svar["txt_navn"] == vert and len(svar["txt_verdi"]) == 64
    _sett_kontekst(migrator, TENANT)
    h = migrator.execute(
        "SELECT challenge_token_hash, status FROM domenekontroll"
        " WHERE tenant=%s AND hostname=%s", (TENANT, vert)).fetchone()
    migrator.rollback()
    assert h[1] == "ventende"
    assert h[0] == hashlib.sha256(svar["txt_verdi"].encode()).hexdigest()
    assert svar["txt_verdi"] not in h[0], "klartekst lagres aldri"

    lr = klient.get("/v1/domener",
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert lr.status_code == 200, lr.text
    mine = {d["hostname"]: d["status"] for d in lr.json()["domener"]}
    assert mine.get(vert) == "ventende"
    for d in lr.json()["domener"]:
        assert "challenge_token_hash" not in d

    fr = klient.post("/v1/domener", json={"hostname": vert, "x": 1},
                     headers={"X-Disponit-CSRF": csrf},
                     cookies={sesjonmodul.C_SESJON: cookie})
    assert fr.status_code == 400
    ck2, cs2 = _adminsesjon(roller="leser")
    sr = klient.post("/v1/domener", json={"hostname": vert},
                     headers={"X-Disponit-CSRF": cs2},
                     cookies={sesjonmodul.C_SESJON: ck2})
    assert sr.status_code == 403


@pg
@dekker("domene_challenge_avvist")
def test_http_ukanonisk_hostname_avvises_av_basen(migrator, klient):
    """018 §0-vakten er strengere enn API-regexen (IDNA2008): et navn som
    slipper gjennom klientformen, men ikke er kanonisk (all-numerisk),
    blir 409 domene_challenge_avvist — aldri en rå DB-feil."""
    from api import sesjon as sesjonmodul
    cookie, csrf = _adminsesjon()
    r = klient.post("/v1/domener", json={"hostname": "127.0.0.1"},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert (r.status_code, r.json()["feil"]) == (
        409, "domene_challenge_avvist"), r.text


@pg
def test_verifiseringspasset_ende_til_ende(migrator, klient):
    """Arbeiderpasset med en fake-resolver: challenge utstedt over HTTP,
    TXT «i sonen», pass → verifisert — og bestillingsveiens
    hostname-port åpner seg (integrasjonen selvbetjeningen finnes for)."""
    import sys
    from api import sesjon as sesjonmodul
    from drift import domenerevalidering as dr

    cookie, csrf = _adminsesjon()
    vert = f"e2e{secrets.token_hex(3)}.example"
    r = klient.post("/v1/domener", json={"hostname": vert},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 201, r.text
    token = r.json()["txt_verdi"]

    def fake_enig(resolvere, hostname):
        return frozenset({token, "v=spf1 -all"}) if hostname == vert else None

    ekte = dr.enig_svar
    dr.enig_svar = fake_enig
    try:
        # SET ROLE (sesjon), ikke SET LOCAL: passet committer/ruller
        # tilbake underveis, og en transaksjonslokal rolle ville falt av
        # midt i løkka.
        migrator.execute("SET ROLE disponit_domene_eier")
        migrator.commit()
        res = dr.kjor_ventende(migrator, resolvere=[],
                               aktor="test-pass", grense=500)
    finally:
        dr.enig_svar = ekte
        migrator.execute("RESET ROLE")
        migrator.commit()
    assert res["verifisert"] >= 1, res
    _sett_kontekst(migrator, TENANT)
    status = migrator.execute(
        "SELECT status FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (TENANT, vert)).fetchone()[0]
    migrator.rollback()
    assert status == "verifisert"


@pg
def test_reutstedelse_koer_tilbakekalt_og_utlopt_domene(migrator, klient):
    """Codex P2: 016 lar `status` stå ved reutstedelse, og
    `ventende_domenechallenges` plukker bare `ventende`. En kunde som la til
    et hostname som sto `tilbakekalt`/`utlopt` fikk derfor 201 med en brukbar
    TXT-oppskrift INGEN arbeider ville sett på.

    Selvbetjeningens egen inngang køer raden tilbake: handlingen «kunden ba om
    en ny utfordring» ER det som gjør den ventende igjen.

    MUTASJONEN SOM DREPER DENNE: fjern UPDATE-en i
    `utsted_challenge_selvbetjent`.
    """
    from api import sesjon as sesjonmodul
    cookie, csrf = _adminsesjon()
    for start in ("tilbakekalt", "utlopt"):
        vert = f"koe{secrets.token_hex(4)}.example"
        _utsted(migrator, vert)
        _sett_kontekst(migrator, TENANT)
        migrator.execute("UPDATE domenekontroll SET status=%s"
                         " WHERE tenant=%s AND hostname=%s",
                         (start, TENANT, vert))
        migrator.commit()

        r = klient.post("/v1/domener", json={"hostname": vert},
                        headers={"X-Disponit-CSRF": csrf},
                        cookies={sesjonmodul.C_SESJON: cookie})
        assert r.status_code == 201, r.text
        _sett_kontekst(migrator, TENANT)
        status = migrator.execute(
            "SELECT status FROM domenekontroll"
            " WHERE tenant=%s AND hostname=%s", (TENANT, vert)).fetchone()[0]
        migrator.rollback()
        assert status == "ventende", f"{start} ble ikke køet på nytt"
        # ...og arbeideren ser den nå.
        assert vert in [h for _, h in _alle_ventende(migrator)]


@pg
@dekker("domene_challenge_avvist")
def test_reutstedelse_avvises_nar_raden_avventer_m37(migrator, klient):
    """Tilstanden som IKKE køes skal svare nei, ikke 201 på en utfordring som
    blir liggende: en ÅPEN avklaring. Bare `avgjor_domeneovertakelse` kan
    flytte den raden, så en TXT-oppskrift ville vært et løfte ingen arbeider
    kan innfri."""
    from api import sesjon as sesjonmodul
    cookie, csrf = _adminsesjon()
    vert = f"m37{secrets.token_hex(4)}.example"
    _utsted(migrator, vert)
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE domenekontroll SET status='avklaring_kreves'"
        " WHERE tenant=%s AND hostname=%s", (TENANT, vert))
    migrator.commit()

    r = klient.post("/v1/domener", json={"hostname": vert},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert (r.status_code, r.json()["feil"]) == (
        409, "domene_challenge_avvist"), r.text
    _sett_kontekst(migrator, TENANT)
    etter = migrator.execute(
        "SELECT status FROM domenekontroll"
        " WHERE tenant=%s AND hostname=%s", (TENANT, vert)).fetchone()[0]
    migrator.rollback()
    assert etter == "avklaring_kreves", "avvist utstedelse flyttet raden"


@pg
@dekker("db_utilgjengelig")
def test_utstedelse_skiller_dbsvikt_fra_tilstandsnekt(migrator, klient, app,
                                                     monkeypatch):
    """Codex P2: `except psycopg.Error` gjorde ALT til 409
    `domene_challenge_avvist`. En funksjon som ikke er utrullet
    (UndefinedFunction) eller et manglende grant (InsufficientPrivilege) er
    også `psycopg.Error` — så en utrullingsfeil som rammer HVER kunde ble
    fortalt kunden som «domenet ditt forbyr en utfordring», og lagt i loggen
    som en SIKKERHETSavvisning: det ene stedet ingen leter etter nedetid.

    Nå fanges bare funksjonens egen `invalid_parameter_value`; alt annet er
    drift, 503 `db_utilgjengelig`.

    MUTASJONEN SOM DREPER DENNE: bytt tilbake til `except psycopg.Error` i
    utstedelsesgrenen.
    """
    from api import sesjon as sesjonmodul

    class _Vrang:
        """Ekte forbindelse, men utstedelseskallet er «ikke utrullet»."""

        def __init__(self, ekte):
            self.ekte = ekte

        def __getattr__(self, navn):
            return getattr(self.ekte, navn)

        def execute(self, sql, args=None):
            if "utsted_challenge_selvbetjent" in sql:
                raise psycopg.errors.UndefinedFunction("ikke utrullet")
            return self.ekte.execute(sql, args)

    cookie, csrf = _adminsesjon()
    hent, gi = app.tjeneste.pool.hent, app.tjeneste.pool.gi_tilbake
    monkeypatch.setattr(app.tjeneste.pool, "hent",
                        lambda *a, **k: _Vrang(hent()))
    # Innpakningen skal ALDRI havne i poolen: neste forespørsel ville arvet den.
    monkeypatch.setattr(app.tjeneste.pool, "gi_tilbake",
                        lambda c: gi(c.ekte if isinstance(c, _Vrang) else c))

    r = klient.post("/v1/domener",
                    json={"hostname": f"drift{secrets.token_hex(3)}.example"},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert (r.status_code, r.json()["feil"]) == (503, "db_utilgjengelig"), \
        r.text


@pg
def test_avvist_kandidat_far_ny_utfordring_uten_a_rive_gjerdet(migrator):
    """Codex P2: `avgjor_domeneovertakelse` etterlater med VILJE den avviste
    kandidaten `tilbakekalt` MED motparten, nettopp for at en ny, bevist
    reapplikasjon skal kunne åpne en ny avklaringsgenerasjon — 018 har en egen
    gren for det. Men utstedelsen avviste den tilstanden, og arbeideren plukket
    bare `ventende`: grenen hadde ingen produksjonskaller, og kandidaten var
    permanent avhengig av at en operatør kalte administrasjonsfunksjonen.

    Nå får hun utstede, og raden BLIR STÅENDE `tilbakekalt`: det er statusen
    018 kjenner igjen. Beviset fører derfor til en NY avklaring med ny
    generasjon og `konflikt:<motpart>` — aldri rett til `verifisert`.

    MUTASJONEN SOM DREPER DENNE: kø raden til `ventende` ved utstedelse (da
    blir svaret 'verifisert'), eller avvis utstedelsen igjen.
    """
    vert = f"reapp{secrets.token_hex(4)}.example"
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, vert))
        a.commit()
        # ANNEN_TENANT tar over med DNS-kontroll → avklaring, motpart TENANT.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, vert))
        a.commit()
        gen = _gen(migrator, ANNEN_TENANT, vert)
        # ...og M-37 AVVISER henne: tilbakekalt, motparten beholdt.
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'m37')",
                  (ANNEN_TENANT, vert, gen))
        a.commit()
    finally:
        a.close()

    rt = _rt()
    token = secrets.token_hex(32)
    h = hashlib.sha256(token.encode()).hexdigest()
    try:
        _sett_kontekst(rt, ANNEN_TENANT)
        rt.execute("SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                   (ANNEN_TENANT, vert, h))
        rt.commit()
    finally:
        rt.close()

    _sett_kontekst(migrator, ANNEN_TENANT)
    status = migrator.execute(
        "SELECT status FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (ANNEN_TENANT, vert)).fetchone()[0]
    migrator.rollback()
    assert status == "tilbakekalt", \
        "utstedelsen rev gjerdet: raden ble flyttet ut av tilbakekalt"

    # Arbeideren SER den — ellers er utfordringen en oppskrift ingen leser.
    assert (ANNEN_TENANT, vert) in _alle_ventende(migrator), \
        "den avviste kandidaten plukkes ikke av arbeideren"

    # ...og beviset gir en NY avklaring, ikke en verifisering.
    _sett_kontekst(migrator, ANNEN_TENANT)
    svar = _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'w',%s)",
                     (ANNEN_TENANT, vert, [token]))[0]
    migrator.commit()
    assert svar == f"konflikt:{TENANT}", svar
    _sett_kontekst(migrator, ANNEN_TENANT)
    rad = migrator.execute(
        "SELECT status, autorisasjonsgenerasjon FROM domenekontroll"
        " WHERE tenant=%s AND hostname=%s",
        (ANNEN_TENANT, vert)).fetchone()
    migrator.rollback()
    assert rad[0] == "avklaring_kreves", rad
    assert rad[1] > gen, "reapplikasjonen fikk ingen ny generasjon"


def _arbeiderkonn():
    """M-37-arbeiderens forbindelse (Codex P1).

    Den DEDIKERTE rollen når den finnes — `oppsett-postgresql.sh` lager
    `disponit_arbeider` og skriver DISPONIT_TEST_ARBEIDER_DSN sammen med
    DISPONIT_ARBEIDER_URL, så «rollen finnes» og «unitten bruker den» er
    samme tilstand. Ellers runtime-DSN-en: det er `opp.sh`-fallbacken der
    m37-unitten faktisk KJØRER som runtime, og da er det den veien som skal
    måles. Grantet i 039 følger nøyaktig det samme skillet.
    """
    import os

    from db.pg import koble
    return koble(os.environ.get("DISPONIT_TEST_ARBEIDER_DSN") or DSN)


@pg
def test_konfliktoppramsingen_er_arbeiderens_alene(migrator):
    """Codex P1: `ventende_overtakelseskonflikter` er en kryss-tenant lesing
    UTEN kallerpredikat — hver tenant, hvert hostname, hver motpart og hver
    generasjon som står i en domenetvist. Ubetinget gitt til den delte
    runtime-rollen var den en oppramsingsvei web-API-et aldri kaller, på den
    credentialen som er mest eksponert.

    Grantet følger derfor rolleskillet: finnes den dedikerte arbeiderrollen,
    er den ENESTE mottaker, og runtime er eksplisitt revoket. Finnes den
    ikke, kjører m37-unitten på runtime-DSN-en (`opp.sh`-fallbacken) og
    runtime ER arbeideren.

    MUTASJONEN SOM DREPER DENNE: legg det ubetingede `GRANT ... TO disponit`
    tilbake.
    """
    from .test_pr015_operativt_lag import _execute_mottakere

    mottakere = _execute_mottakere(
        migrator, "ventende_overtakelseskonflikter(integer)")
    finnes = migrator.execute(
        "SELECT 1 FROM pg_roles WHERE rolname='disponit_arbeider'").fetchone()
    migrator.rollback()
    assert "-" not in mottakere, "PUBLIC når konfliktoppramsingen"
    if finnes:
        assert mottakere == {"disponit_arbeider"}, mottakere
    else:
        assert mottakere == {"disponit"}, mottakere


def _admin():
    """migrator SET ROLE domains_admin (committed → overlever rollback)."""
    from db.pg import koble
    c = koble(MIGRATOR_DSN)
    c.execute("SET ROLE disponit_domains_admin")
    c.commit()
    return c


def _gen(conn, tenant, hostname):
    _sett_kontekst(conn, tenant)
    g = conn.execute(
        "SELECT autorisasjonsgenerasjon FROM domenekontroll"
        " WHERE tenant=%s AND hostname=%s", (tenant, hostname)).fetchone()[0]
    conn.rollback()
    return int(g)


@pg
def test_konflikt_far_sin_m37_sak_av_dreneringen(migrator):
    """Codex P1: en overtakelse UTEN en sak er en dødvei — A har mistet
    autorisasjonen og B står i `avklaring_kreves`, som bare
    `avgjor_domeneovertakelse` kan løfte noen ut av, og den nås bare gjennom
    en sak. Verifiseringsarbeideren kan ikke lage den (ingen DEK, ingen DML),
    så TILSTANDEN er signalet: dreneringen finner raden og lager saken.

    Måler også idempotensen: to dreneringer gir ÉN sak for konflikten.
    """
    from api import domeneovertakelse as dov

    vert = f"kfl{secrets.token_hex(4)}.example"
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, vert))
        a.commit()
        svar = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (ANNEN_TENANT, vert)).fetchone()[0]
        a.commit()
    finally:
        a.close()
    assert svar == f"konflikt:{TENANT}", svar
    gen = _gen(migrator, ANNEN_TENANT, vert)

    # Ingen sak ennå: overtakelsen skjedde i basen, saken krever DEK + DML.
    _sett_kontekst(migrator, ANNEN_TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM unntak u JOIN revisjonslogg r"
        "   ON r.tenant=u.tenant AND r.id=u.loggpost_id"
        " WHERE u.tenant=%s AND r.idempotency_key=%s",
        (ANNEN_TENANT, dov.idempotensnokkel(vert, gen))).fetchone()[0] == 0
    migrator.rollback()

    # Dreneringen kjøres over ARBEIDERENS forbindelse, ikke migrator: det er
    # nøyaktig rettighetene M-37-arbeideren har — aldri migrators, som ikke
    # er medlem av noen av rollene.
    rt = _arbeiderkonn()
    try:
        res = dov.sikre_ventende_overtakelsessaker(rt, grense=500)
        mine = [s for s in res["saker"] if s["hostname"] == vert]
        assert res["feilet"] == [], res
        assert len(mine) == 1, res
        sak = mine[0]["unntak_id"]
        assert mine[0]["tenant"] == ANNEN_TENANT

        # Raden står fortsatt i `avklaring_kreves` (bare M-37 kan flytte den),
        # så neste drenering finner den igjen — og skal GJENBRUKE saken.
        res2 = dov.sikre_ventende_overtakelsessaker(rt, grense=500)
        mine2 = [s for s in res2["saker"] if s["hostname"] == vert]
        assert [s["unntak_id"] for s in mine2] == [sak], res2
    finally:
        rt.close()

    # Saken er en ekte overtakelsessak, bundet til konfliktens generasjon.
    _sett_kontekst(migrator, ANNEN_TENANT)
    assert dov.slaa_opp_sak(migrator, ANNEN_TENANT, sak) == (vert, gen)
    migrator.rollback()

    _sett_kontekst(migrator, ANNEN_TENANT)
    antall = migrator.execute(
        "SELECT count(*) FROM unntak u JOIN revisjonslogg r"
        "   ON r.tenant=u.tenant AND r.id=u.loggpost_id"
        " WHERE u.tenant=%s AND u.kategori=%s AND r.idempotency_key=%s",
        (ANNEN_TENANT, dov.FAMILIE,
         dov.idempotensnokkel(vert, gen))).fetchone()[0]
    migrator.rollback()
    assert antall == 1, "én konflikt ga mer enn én sak"


@pg
def test_konfliktutvalget_roterer_forbi_dem_som_venter_paa_mennesker(migrator):
    """Codex P2: en konflikt står `avklaring_kreves` til et MENNESKE har
    avgjort saken — dager, ikke minutter — og dreneringen flytter den ikke.
    Med en stabil `ORDER BY hostname, tenant` + `LIMIT` okkuperte de første
    `grense` konfliktene hele utvalget ved HVER drenering: konflikt nummer
    `grense`+1 ble aldri valgt, fikk aldri sin sak, og var like uløselig som
    før dreneringen fantes.

    Nå stempler plukket radene det tar (`konflikt_drenert`) og tar de minst
    nylig drenerte først: hele populasjonen vandrer gjennom taket.

    MUTASJONEN SOM DREPER DENNE: fjern stempelet, eller sorter på
    `hostname, tenant` alene.
    """
    # Basen deles med resten av suiten: sett alle ANDRE konflikter «nettopp
    # drenert» (fram i tid), så rotasjonen som måles er vår egen.
    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    migrator.execute("UPDATE domenekontroll"
                     " SET konflikt_drenert = now() + interval '1 hour'"
                     " WHERE status = 'avklaring_kreves'")
    migrator.execute("RESET ROLE")
    migrator.commit()

    verter = [f"kro{i}{secrets.token_hex(3)}.example" for i in range(2)]
    for v in verter:
        _utsted(migrator, v)
        _sett_kontekst(migrator, TENANT)
        migrator.execute(
            "UPDATE domenekontroll SET status='avklaring_kreves',"
            " konflikt_motpart=%s WHERE tenant=%s AND hostname=%s",
            (ANNEN_TENANT, TENANT, v))
        migrator.commit()

    sett = []
    for _ in range(2):
        migrator.execute("SET LOCAL ROLE disponit_domene_eier")
        rad = migrator.execute(
            "SELECT hostname FROM ventende_overtakelseskonflikter(1)"
        ).fetchone()
        migrator.execute("RESET ROLE")
        migrator.commit()
        sett.append(rad[0])
    assert set(sett) == set(verter), \
        f"utvalget roterer ikke — så bare {sett} av {verter}"


class _Svar:
    def __init__(self, rader):
        self._rader = rader

    def fetchone(self):
        return self._rader[0] if self._rader else None

    def fetchall(self):
        return self._rader


class _Falskkonn:
    """Minimal conn: låsen gis, ÉN rad plukkes, bekreftelsen reiser `feil`."""

    def __init__(self, feil):
        self.feil = feil
        self.rullet = 0
        self.laast_opp = False

    def execute(self, sql, args=None):
        if "pg_try_advisory_lock" in sql:
            return _Svar([(True,)])
        if "ventende_domenechallenges" in sql:
            return _Svar([("t", "en.example")])
        if "bekreft_domenechallenge" in sql:
            raise self.feil
        if "pg_advisory_unlock" in sql:
            self.laast_opp = True
            return _Svar([(True,)])
        return _Svar([])

    def commit(self):
        pass

    def rollback(self):
        self.rullet += 1


def test_uventet_dbfeil_feller_verifiseringspasset(monkeypatch):
    """Codex P2: `except Exception` gjorde ALT til «ikke bevist» — en funksjon
    som ikke er utrullet, et feil grant, en SQL-feil. Hver rad ble rullet
    tilbake, telleren gikk opp, og passet returnerte 0: systemd noterte et
    vellykket pass mens HVER challenge sto ubehandlet, i det uendelige, uten
    en rød unit.

    Nå fanges bare de forventede utfallene, og de tre klassene holdes fra
    hverandre: manglende bevis, kappløp, og alt annet.

    MUTASJONEN SOM DREPER DENNE: bytt `except MANGLENDE_BEVIS` tilbake til
    `except Exception`.
    """
    from drift import domenerevalidering as dr

    monkeypatch.setattr(dr, "enig_svar",
                        lambda resolvere, hostname: frozenset({"t"}))

    # 1) Uventet: slipper ut, og låsen slippes likevel (ingen maskering av
    #    årsaken med InFailedSqlTransaction fra opplåsingen).
    uventet = _Falskkonn(psycopg.errors.UndefinedFunction("finnes ikke"))
    with pytest.raises(psycopg.errors.UndefinedFunction):
        dr.kjor_ventende(uventet, resolvere=[])
    assert uventet.laast_opp, "advisory-låsen ble ikke sluppet"

    # 2) Forventet nei: telles, passet fortsetter.
    nei = _Falskkonn(psycopg.errors.InvalidParameterValue("ingen bevis"))
    res = dr.kjor_ventende(nei, resolvere=[])
    assert (res["ikke_bevist"], res["kapplop"]) == (1, 0), res

    # 3) Kappløp: egen teller — det er ikke det samme som «beviset manglet».
    race = _Falskkonn(psycopg.errors.UniqueViolation("en_verifisert"))
    res = dr.kjor_ventende(race, resolvere=[])
    assert (res["kapplop"], res["ikke_bevist"]) == (1, 0), res


class _Plukkonn:
    """Minimal conn som noterer REKKEFØLGEN av plukk og transaksjonsslutt."""

    def __init__(self):
        self.spor = []

    def execute(self, sql, args=None):
        if "pg_try_advisory_lock" in sql:
            return _Svar([(True,)])
        if "ventende_domenechallenges" in sql:
            self.spor.append("plukk")
            return _Svar([])
        return _Svar([(True,)])

    def commit(self):
        self.spor.append("commit")

    def rollback(self):
        self.spor.append("rollback")


def test_plukket_committes_saa_stempelet_overlever():
    """Codex P1: rotasjonen ligger i at plukket STEMPLER radene det tar
    (`challenge_forsokt`). Rulles plukk-transaksjonen tilbake, forsvinner
    stempelet, og utvalget er igjen de samme eldste radene hver kjøring — med
    kundene bak taket like usett som før.

    MUTASJONEN SOM DREPER DENNE: bytt `conn.commit()` etter plukket tilbake
    til `conn.rollback()`.
    """
    from drift import domenerevalidering as dr

    konn = _Plukkonn()
    dr.kjor_ventende(konn, resolvere=[])
    assert konn.spor[0] == "plukk", konn.spor
    assert konn.spor[1] == "commit", \
        f"plukket ble ikke committet — stempelet gikk tapt: {konn.spor}"


class _Tellekonn:
    """Minimal conn: alle bekreftelser lykkes, og de TELLES i rekkefølge."""

    def __init__(self, rader):
        self.rader = rader
        self.skrevet = []

    def execute(self, sql, args=None):
        if "pg_try_advisory_lock" in sql:
            return _Svar([(True,)])
        if "ventende_domenechallenges" in sql:
            return _Svar(self.rader)
        if "bekreft_domenechallenge" in sql:
            self.skrevet.append(args[1])
            return _Svar([("verifisert",)])
        return _Svar([(True,)])

    def commit(self):
        pass

    def rollback(self):
        pass


def test_trege_hostnames_sulter_ikke_de_friske(monkeypatch):
    """Codex P2: passet var SERIELT — opptil 200 navn etter hverandre, hvert
    med resolverkall som har fem sekunders levetid, mot en unit som dør etter
    fire minutter. En kohort med trege navn FORAN i `challenge_utstedt`-
    rekkefølgen spiste hele vinduet, og siden utvalget alltid tar de eldste
    først, plukket neste kjøring de samme radene: kundene bak ble sultet til
    utfordringen deres utløp.

    Nå slås navnene opp med `SAMTIDIGHET` i parallell og konsumeres i
    FULLFØRINGSrekkefølge. Målingen: de friske navnene skrives FØR det trege,
    selv om det trege ligger først i plukket.

    MUTASJONEN SOM DREPER DENNE: gå tilbake til en seriell løkke, eller
    konsumér i innsendingsrekkefølge.
    """
    from drift import domenerevalidering as dr

    treg = "treg.example"
    friske = [f"frisk{i}.example" for i in range(4)]
    rader = [("t", treg)] + [("t", h) for h in friske]

    def sakte(resolvere, hostname):
        if hostname == treg:
            time.sleep(0.5)
        return frozenset({"bevis"})

    monkeypatch.setattr(dr, "enig_svar", sakte)
    konn = _Tellekonn(rader)
    res = dr.kjor_ventende(konn, resolvere=[], samtidighet=8)

    assert res["verifisert"] == 5, res
    assert res["ubehandlet"] == 0, res
    assert konn.skrevet[-1] == treg, \
        f"det trege navnet blokkerte de friske: {konn.skrevet}"


def test_passet_stanser_seg_selv_paa_egen_frist(monkeypatch):
    """Fristen er PASSETS, ikke systemds: rekker vi ikke køen, stanser vi
    MELLOM to ferdige oppslag — det ene punktet der ingenting er halvveis
    skrevet — og de uberørte radene står `ventende` til neste kjøring. Traff
    systemd-timeouten i stedet, kunne SIGTERM landet mellom bekreftelsen og
    commiten."""
    from drift import domenerevalidering as dr

    rader = [("t", f"h{i}.example") for i in range(6)]
    monkeypatch.setattr(dr, "enig_svar",
                        lambda resolvere, hostname: frozenset({"bevis"}))
    konn = _Tellekonn(rader)
    # Fristen er alt utløpt: ingen rad skal skrives, alle telles ubehandlet.
    res = dr.kjor_ventende(konn, resolvere=[], frist_s=-1)
    assert res.get("frist_naadd") is True, res
    assert (res["verifisert"], res["ubehandlet"]) == (0, 6), res
    assert konn.skrevet == []


def test_taket_holder_seg_innenfor_unitens_timeout():
    """Taket er UTLEDET, ikke valgt: med samtidighet 8 og ~10 s verste
    tilfelle per hostname må et fullt tak rekke innenfor passets egen frist,
    som igjen ligger under unitens TimeoutStartSec. Går ett av tallene opp
    uten at de andre følger, er sultingen tilbake."""
    import math as _math

    from drift import domenerevalidering as dr

    verste_per_hostname_s = 10
    runder = _math.ceil(dr.VERIFISERING_TAK / dr.SAMTIDIGHET)
    assert runder * verste_per_hostname_s < dr.VERIFISERING_FRIST_S
    # Unitens TimeoutStartSec=4min er sikkerhetsnettet over fristen.
    assert dr.VERIFISERING_FRIST_S < 4 * 60


#: 039s funksjoner, med signatur skrevet ut. Ikke hentet fra katalogen: en
#: test som spør basen hvilke funksjoner 039 laget, ville godtatt at en av dem
#: forsvant.
DEFAULT_DENY_039 = [
    "ventende_domenechallenges(integer)",
    "bekreft_domenechallenge(text,text,text,text[])",
    "ventende_overtakelseskonflikter(integer)",
    "utsted_challenge_selvbetjent(text,text,boolean,text,text)",
]


@pg
def test_039_default_deny_gjelder_faktisk(migrator):
    """PUBLIC skal ikke nå NOEN av 039s funksjoner — målt på ACL-en.

    Porten finnes fordi en `REVOKE ... FROM PUBLIC` kan MISLYKKES stille:
    kjøres den av en rolle som ikke eier funksjonen, advarer PostgreSQL og går
    videre, men materialiserer samtidig standard-ACL-en, som for en funksjon er
    EXECUTE for PUBLIC. Da er resultatet det motsatte av det migrasjonen sier,
    og ingen funksjonell test ser det: et privilegium PUBLIC allerede har
    feiler aldri. Samme port som 019 (`test_019_default_deny_gjelder_faktisk`).

    Særlig for `utsted_challenge_selvbetjent`: hele tenantporten er verdiløs
    hvis EN VILKÅRLIG rolle i klyngen kan kalle innpakningen.
    """
    from .test_pr015_operativt_lag import _execute_mottakere

    apne = [sig for sig in DEFAULT_DENY_039
            if "-" in _execute_mottakere(migrator, sig)]
    assert not apne, f"PUBLIC har EXECUTE på: {', '.join(apne)}"


@pg
def test_raa_utsted_challenge_er_ikke_runtimes(migrator):
    """ACL-porten under den funksjonelle: runtime skal ikke stå i ACL-en til
    016s bevisløse `utsted_challenge` i det hele tatt. Et `GRANT ... TO
    disponit` som sniker seg inn igjen i en senere migrasjon skal SES her, ikke
    først når noen finner kryss-tenant-veien."""
    from .test_pr015_operativt_lag import _execute_mottakere

    mottakere = _execute_mottakere(
        migrator, "utsted_challenge(text,text,boolean,text,text)")
    assert "disponit" not in mottakere, mottakere
    assert "-" not in mottakere, "PUBLIC når utsted_challenge"
    # ...og innpakningen er runtimes ENESTE vei inn.
    assert "disponit" in _execute_mottakere(
        migrator, "utsted_challenge_selvbetjent(text,text,boolean,text,text)")


def test_arbeideren_drenerer_konflikter_i_hovedlokka():
    """Dreneringen er UTRULLET, ikke bare tilgjengelig: M-37-arbeiderens
    hovedløkke kaller den. Uten kallet ville funksjonen over hatt nøyaktig
    samme mangel som `opprett_overtakelsessak` hadde — riktig kropp, ingen
    produksjonskaller."""
    import inspect

    from m37 import arbeider

    assert "drener_domenekonflikter" in inspect.getsource(arbeider.kjor), \
        "hovedløkken drenerer ikke domenekonflikter"
    assert "sikre_ventende_overtakelsessaker" in inspect.getsource(
        arbeider.drener_domenekonflikter)
