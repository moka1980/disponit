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
    from drift import domenerevalidering as dr
    cookie, csrf = _adminsesjon()
    vert = f"selv{secrets.token_hex(3)}.example"
    r = klient.post("/v1/domener", json={"hostname": vert.upper()},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 201, r.text
    svar = r.json()
    assert len(svar["txt_verdi"]) == 64
    # PORTEN MELLOM UTSTEDELSEN OG ARBEIDEREN (Codex P1). Oppskriften kunden
    # får, og navnet arbeideren slår opp, er to konstanter på hver sin side av
    # api/-grensen — `drift` ligger ved siden av `platform/core` og kan ikke
    # importeres derfra. Uenighet mellom dem viser seg ikke som en feil noe
    # sted: kunden publiserer, arbeideren finner ingenting, og domenet står
    # «ikke verifisert» i det uendelige. Derfor måles de mot hverandre her.
    assert svar["txt_navn"] == dr.utfordringsnavn(vert)
    assert svar["txt_navn"] != vert, (
        "utfordringen ligger på vertsnavnet igjen — et CNAME-vertsnavn kan "
        "ikke bære en TXT-post ved siden av aliaset")
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
def test_dypt_nostet_kropp_er_request_feil(migrator, klient):
    """Codex P2: `json.loads` er REKURSIV. Et syntaktisk gyldig, dypt nøstet
    dokument på noen få kilobyte ligger godt under kroppsgrensen og treffer
    likevel rekursjonsgrensen. RecursionError er en RuntimeError, ikke en
    ValueError, så `except ValueError` alene slapp klientinput ut som generisk
    500 i stedet for det dokumenterte `request_feilformet`.

    Kroppen bygges som TEKST: `json.dumps` av en så dyp struktur ville tatt
    livet av testen selv, ikke serveren. Dybden krysses mot parseren HER, så
    testen aldri stille blir grønn av at kroppen ble for grunn til å nå
    grensen (C-parserens tak flytter seg mellom Python-versjoner).

    MUTASJONEN SOM DREPER DENNE: fjern RecursionError fra except-en rundt
    `json.loads` i `utsted_endepunkt`.
    """
    import json as jsonmodul

    from api import sesjon as sesjonmodul
    cookie, csrf = _adminsesjon()
    dybde = 15000
    kropp = '{"a":' * dybde + "1" + "}" * dybde
    assert len(kropp) < 200_000, "testkroppen skal ligge under kroppsgrensen"
    with pytest.raises(RecursionError):
        jsonmodul.loads(kropp)
    r = klient.post("/v1/domener", content=kropp,
                    headers={"X-Disponit-CSRF": csrf,
                             "content-type": "application/json"},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert (r.status_code, r.json()["feil"]) == (
        400, "request_feilformet"), r.text


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
        # Sonen svarer BARE på utfordringsnavnet (Codex P1) — nøyaktig som en
        # kunde med et CNAME-vertsnavn, der selve vertsnavnet ikke kan bære
        # posten. Slår passet opp vertsnavnet, får det None og verifiserer
        # ingenting; det er mutasjonen denne testen dreper.
        return (frozenset({token, "v=spf1 -all"})
                if hostname == dr.utfordringsnavn(vert) else None)

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


@pg
def test_alle_domeneoverganger_deler_laaserekkefolge(migrator):
    """Codex P2: to låser tatt i to rekkefølger er en vranglås som venter.

    `bekreft_domenechallenge` tok RADlåsen først og gikk deretter inn i
    `verifiser_domenekontroll`, som tar hostname-advisory-låsen.
    `tilbakekall_domenekontroll` og `avgjor_domeneovertakelse` tar dem
    motsatt: advisory-låsen først, så raden. En bekreftelse som kappløp med
    en operatørhandling på samme tenant/hostname kunne derfor vente på låsen
    den andre holdt — i begge retninger — til PostgreSQL avbrøt den ene.
    Passet teller det som `kapplop`: tapt arbeid uten annet spor enn en
    teller.

    Målt på den UTRULLEDE kroppen (`pg_get_functiondef`), ikke på filen: det
    er den formen som faktisk tar låsene. Kravet er ett og felles — advisory
    før rad — så en ny vei inn måles på samme regel.

    MUTASJONEN SOM DREPER DENNE: flytt `pg_advisory_xact_lock` i
    `bekreft_domenechallenge` ned under `FOR UPDATE` igjen.
    """
    for sign in ("bekreft_domenechallenge(text,text,text,text[])",
                 "verifiser_domenekontroll(text,text,boolean,text)",
                 "tilbakekall_domenekontroll(text,text,text,text)",
                 "avgjor_domeneovertakelse(text,text,bigint,boolean,text)",
                 "degrader_forbigatte_utfordrere(text,text)"):
        kropp = migrator.execute(
            "SELECT pg_get_functiondef(%s::regprocedure)", (sign,)).fetchone()[0]
        migrator.rollback()
        laas = kropp.find("pg_advisory_xact_lock")
        rad = kropp.find("FOR UPDATE")
        assert laas >= 0, f"{sign} tar ingen hostname-advisory-lås"
        assert rad >= 0, f"{sign} tar ingen radlås"
        assert laas < rad, \
            f"{sign} tar radlåsen før advisory-låsen — motsatt av de andre"


@pg
def test_en_avvisning_blir_staaende_til_det_finnes_nytt_bevis(migrator):
    """Codex P1: en avvisning må kunne bli STÅENDE avvist.

    `avgjor_domeneovertakelse` endret før bare status og generasjon.
    `challenge_token_hash` og utløpet sto igjen — og kundens TXT-post ligger
    jo fortsatt i sonen. Reapplikasjonsgrenen (som med vilje tar `tilbakekalt`
    MED motpart) plukket derfor raden med det samme, arbeideren godtok
    NØYAKTIG det samme beviset, det ble en ny konfliktgenerasjon, dreneringen
    laget en ny M-37-sak — og menneskene fikk samme sak i fanget hvert femte
    minutt til utfordringen utløp av seg selv, uten at kunden hadde løftet en
    finger.

    Avvisningen forbruker nå utfordringen. Veien tilbake er den 039 alt åpnet,
    og den krever en HANDLING: kunden utsteder på nytt, får et NYTT token, og
    DA — og bare da — fører beviset til en ny avklaring.

    MUTASJONEN SOM DREPER DENNE: la avvisningen la `challenge_token_hash` stå.
    """
    vert = f"avvist{secrets.token_hex(4)}.example"
    token = secrets.token_hex(32)
    h = hashlib.sha256(token.encode()).hexdigest()
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, vert))
        a.commit()
    finally:
        a.close()

    # ANNEN_TENANT utsteder selv, publiserer TXT-en, og beviset gir konflikt.
    rt = _rt()
    try:
        _sett_kontekst(rt, ANNEN_TENANT)
        rt.execute("SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                   (ANNEN_TENANT, vert, h))
        rt.commit()
    finally:
        rt.close()
    svar = _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'w',%s)",
                     (ANNEN_TENANT, vert, [token]))[0]
    migrator.commit()
    assert svar == f"konflikt:{TENANT}", svar

    # M-37 AVVISER — mens utfordringen ennå har seks døgn igjen og posten
    # fortsatt står i sonen.
    gen = _gen(migrator, ANNEN_TENANT, vert)
    a = _admin()
    try:
        a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'m37')",
                  (ANNEN_TENANT, vert, gen))
        a.commit()
    finally:
        a.close()

    _sett_kontekst(migrator, ANNEN_TENANT)
    rad = migrator.execute(
        "SELECT status, konflikt_motpart, challenge_token_hash,"
        " challenge_utloper FROM domenekontroll WHERE tenant=%s"
        " AND hostname=%s", (ANNEN_TENANT, vert)).fetchone()
    migrator.rollback()
    assert rad[0] == "tilbakekalt", rad
    assert rad[1] == TENANT, "motparten skal beholdes — den er merket 018 " \
        "kjenner reapplikasjonen igjen på"
    assert rad[2] is None and rad[3] is None, \
        "utfordringen overlevde avvisningen — den gamle TXT-posten kan " \
        "bevises på nytt uten at kunden har gjort noe"

    # 1) Arbeideren plukker henne ikke lenger...
    assert (ANNEN_TENANT, vert) not in _alle_ventende(migrator), \
        "den avviste raden køes umiddelbart på nytt"
    # 2) ...og den GAMLE TXT-verdien beviser ingenting.
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'w',%s)",
                  (ANNEN_TENANT, vert, [token]))
    migrator.rollback()

    # 3) Reapplikasjonen står fortsatt åpen: nytt token → ny avklaring.
    nytt = secrets.token_hex(32)
    rt = _rt()
    try:
        _sett_kontekst(rt, ANNEN_TENANT)
        rt.execute("SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                   (ANNEN_TENANT, vert,
                    hashlib.sha256(nytt.encode()).hexdigest()))
        rt.commit()
    finally:
        rt.close()
    assert (ANNEN_TENANT, vert) in _alle_ventende(migrator), \
        "en ny utfordring åpner ikke reapplikasjonen"
    svar = _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'w',%s)",
                     (ANNEN_TENANT, vert, [nytt]))[0]
    migrator.commit()
    assert svar == f"konflikt:{TENANT}", svar
    assert _gen(migrator, ANNEN_TENANT, vert) > gen, \
        "reapplikasjonen fikk ingen ny generasjon"


@pg
def test_en_forbigatt_utfordrer_forbruker_ogsaa_utfordringen(migrator):
    """Codex P1: den AUTOMATISKE utgangen av avklaringen kunne fortsatt løkke.

    En utfordrer forlater `avklaring_kreves` ad to veier. Avvisningen
    (testen over) er den et menneske tar. Den andre er
    `degrader_forbigatte_utfordrere` (019 §3.2), hengt på
    `hostname_binding`-triggeren: tar en tredje C plassen i den åpne saken,
    settes B `tilbakekalt` med ny generasjon.

    019-formen lot utfordringen stå. B ble dermed liggende med NØYAKTIG den
    signaturen reapplikasjonsplukket er bygget for — `tilbakekalt` +
    `konflikt_motpart` + levende hash — og B-s TXT-post ligger fortsatt i
    sonen, for ingen har bedt henne fjerne den. Neste pass tok derfor B med
    det samme, godtok det gamle beviset, flyttet bindingen tilbake til B, og
    triggeren degraderte C. Står begge postene igjen, veksler B og C for hvert
    pass — hver runde med en ny konfliktgenerasjon og en ny M-37-sak.

    MUTASJONEN SOM DREPER DENNE: la degraderingen la `challenge_token_hash`
    stå (altså 019-kroppen uendret).
    """
    from .test_pr015_operativt_lag import TREDJE_TENANT

    vert = f"forbigatt{secrets.token_hex(4)}.example"
    token_b = secrets.token_hex(32)
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, vert))       # A eier navnet
        a.commit()
    finally:
        a.close()

    # B utsteder selv, publiserer TXT-en, og beviset gir konflikten.
    rt = _rt()
    try:
        _sett_kontekst(rt, ANNEN_TENANT)
        rt.execute("SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                   (ANNEN_TENANT, vert,
                    hashlib.sha256(token_b.encode()).hexdigest()))
        rt.commit()
    finally:
        rt.close()
    svar = _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'w',%s)",
                     (ANNEN_TENANT, vert, [token_b]))[0]
    migrator.commit()
    assert svar == f"konflikt:{TENANT}", svar
    gen_b = _gen(migrator, ANNEN_TENANT, vert)

    # C tar plassen. Ingen manuell opprydding: triggeren på bindingen skal
    # gjøre degraderingen i samme transaksjon som overtakelsen.
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TREDJE_TENANT, vert))
        a.commit()
    finally:
        a.close()

    _sett_kontekst(migrator, ANNEN_TENANT)
    rad = migrator.execute(
        "SELECT status, konflikt_motpart, challenge_token_hash,"
        " challenge_utloper, challenge_forsokt FROM domenekontroll"
        " WHERE tenant=%s AND hostname=%s", (ANNEN_TENANT, vert)).fetchone()
    migrator.rollback()
    assert rad[0] == "tilbakekalt", rad
    assert _gen(migrator, ANNEN_TENANT, vert) > gen_b, \
        "degraderingen økte ikke generasjonen"
    assert rad[1] == TENANT, "motparten skal beholdes — det er merket " \
        "reapplikasjonsgrenen kjenner raden igjen på"
    assert rad[2] is None and rad[3] is None and rad[4] is None, \
        "utfordringen overlevde degraderingen — B-s gamle TXT-post kan " \
        "bevises på nytt og tar bindingen tilbake fra C"

    # 1) Passet plukker ikke B lenger — det er selve løkka.
    assert (ANNEN_TENANT, vert) not in _alle_ventende(migrator), \
        "den forbigåtte raden køes umiddelbart på nytt"
    # 2) ...og den gamle TXT-verdien beviser ingenting om den plukkes likevel.
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'w',%s)",
                  (ANNEN_TENANT, vert, [token_b]))
    migrator.rollback()
    # 3) C står urørt i avklaring: degraderingen rører aldri bindingshaveren.
    _sett_kontekst(migrator, TREDJE_TENANT)
    c_status = migrator.execute(
        "SELECT status FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (TREDJE_TENANT, vert)).fetchone()[0]
    migrator.rollback()
    assert c_status == "avklaring_kreves", c_status


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


def test_utrullingen_maaler_samme_invariant_som_grantet():
    """Codex P2: grantet nøkles til at ROLLEN `disponit_arbeider` finnes,
    mens `opp.sh` valgte m37-legitimasjonen på om DISPONIT_ARBEIDER_URL er
    SATT. To ulike fakta om samme rolleskille — og avviket er stille: finnes
    rollen uten variabelen (halvferdig rolleutrulling), kobler arbeideren seg
    opp som `disponit` mot funksjonen 039 nettopp REVOKET fra runtime, og hver
    domeneovertakelse blir stående i `avklaring_kreves` uten sak.

    Porten er derfor ENIGHET mellom basen og miljøfilen, målt FØR første
    mutasjon. Verdikten kjøres mot den rene funksjonen i lib-opp.sh, slik
    helsekodeporten gjør — ingen base, ingen utrulling.

    MUTASJONEN SOM DREPER DENNE: la `vurder_arbeiderskille` godta ja:nei (og
    dermed la fallbacken gjelde med rollen på plass), eller fjern kallet fra
    `opp.sh`s preflight.
    """
    import subprocess
    from pathlib import Path

    rot = Path(__file__).resolve().parents[3]
    lib = rot / "deploy/staging/lib-opp.sh"

    def verdikt(dsn, rolle):
        return subprocess.run(
            ["bash", "-c",
             f'. {lib}; vurder_arbeiderskille "{dsn}" "{rolle}"'],
            capture_output=True, text=True).returncode

    assert verdikt("ja", "ja") == 0, "dedikert rolle OG DSN: sammenhengende"
    assert verdikt("nei", "nei") == 0, "verken rolle eller DSN: fallbacken"
    assert verdikt("nei", "ja") != 0, \
        "rollen finnes, men m37 ville kjørt som disponit — grantet er borte"
    assert verdikt("ja", "nei") != 0, \
        "DSN-en peker på en rolle som ikke finnes"
    for ukjent in ("", "kanskje", "JA", "1"):
        assert verdikt(ukjent, "ja") != 0, f"{ukjent!r} skal være fail-closed"
        assert verdikt("ja", ukjent) != 0, f"{ukjent!r} skal være fail-closed"

    # ... og at porten faktisk STÅR i preflighten, før første mutasjon.
    opp = (rot / "deploy/staging/opp.sh").read_text(encoding="utf-8")
    assert "vurder_arbeiderskille" in opp, "porten kalles ikke fra opp.sh"
    assert (opp.index("vurder_arbeiderskille")
            < opp.index("HERFRA MUTERES SYSTEMET")), \
        "porten står etter første mutasjon — da er den ingen preflight"


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
def test_dreneringen_skiller_radfeil_fra_utrullingsfeil(migrator, monkeypatch):
    """Codex P2: `except psycopg.Error` gjorde HVER databasefeil til en
    radoppføring i `feilet`. En manglende grant, en funksjon som ikke er
    utrullet eller en skjemafeil rammer ALLE rader — men den ble skrevet inn i
    et resultat bare `drener_domenekonflikter` printer, mens M-37-løkkens
    heartbeat sto `ok`: hver eneste overtakelse kunne bli stående uten sak
    mens arbeideren så helt frisk ut.

    Nå fanges bare RADENS egne feil — `tenantnokkel_mangler` (KEK-en er borte
    for én tenant) og ugyldige radverdier, der andre tenanters konflikter
    fortsatt skal bli stelt. Uventede DB-feil kastes videre og feller
    prosessen, samme kontrakt som resten av M-37-hovedløkken.

    MUTASJONEN SOM DREPER DENNE: sett `psycopg.Error` tilbake i tuppelen som
    telles som radfeil.
    """
    from api import domeneovertakelse as dov
    from api import kjerne

    vert = f"drn{secrets.token_hex(4)}.example"
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

    # (1) Radens egen feil: telles, navngis, og de andre radene fortsetter.
    monkeypatch.setattr(dov, "opprett_overtakelsessak", lambda *a, **k: (_ for
                        _ in ()).throw(kjerne.Feilsvar("tenantnokkel_mangler",
                                                       "KEK borte")))
    rt = _arbeiderkonn()
    try:
        res = dov.sikre_ventende_overtakelsessaker(rt, grense=500)
        assert [f for f in res["feilet"] if f["hostname"] == vert] == [
            {"tenant": ANNEN_TENANT, "hostname": vert,
             "feiltype": "Feilsvar"}], res

        # (2) Utrullingens feil: kastes videre, ALDRI talt som en rad.
        monkeypatch.setattr(
            dov, "opprett_overtakelsessak",
            lambda *a, **k: (_ for _ in ()).throw(
                psycopg.errors.InsufficientPrivilege("grant mangler")))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            dov.sikre_ventende_overtakelsessaker(rt, grense=500)
        # Forbindelsen er rullet tilbake og brukbar: feilen er signalet, ikke
        # en ødelagt tilkobling kalleren må gjette seg til.
        assert rt.execute("SELECT 1").fetchone()[0] == 1
    finally:
        rt.close()


def test_dreneringen_navngir_utrullingsfeil_og_feller_arbeideren(monkeypatch):
    """Codex P2: M-37-innpakningen skal ikke svelge det heller.

    `drener_domenekonflikter` printer `feilet` og går videre — så en uventet
    DB-feil måtte kastes HELT ut for at heartbeat `ok` ikke skulle bli et
    friskhetstegn for en arbeider som ikke lager en eneste sak. Journalraden
    navngir årsaken før den kastes.

    MUTASJONEN SOM DREPER DENNE: bytt `raise` mot `return res` i
    `drener_domenekonflikter`.
    """
    import json as jsonmodul

    from api import domeneovertakelse as dov
    from m37 import arbeider

    monkeypatch.setattr(
        dov, "sikre_ventende_overtakelsessaker",
        lambda *a, **k: (_ for _ in ()).throw(
            psycopg.errors.UndefinedFunction("ikke utrullet")))
    linjer = []
    monkeypatch.setattr("builtins.print",
                        lambda s, **k: linjer.append(s))
    with pytest.raises(psycopg.errors.UndefinedFunction):
        arbeider.drener_domenekonflikter(None)
    hendelser = [jsonmodul.loads(x)["hendelse"] for x in linjer]
    assert hendelser == ["domenekonflikt_drenering_svikt"], linjer


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


def test_utfordringen_slaas_opp_paa_et_navn_kunden_kan_eie(monkeypatch):
    """Codex P1: en CNAME-eier kan ikke bære en TXT-post.

    `www.dittfirma.no` peker typisk på en leverandør. Eieren av et CNAME kan
    per RFC 1034 §3.6.2 ikke ha andre poster ved siden av seg, og et rekursivt
    TXT-oppslag på navnet følger aliaset inn i leverandørens sone. Kunden har
    da ingen måte å legge ut beviset på — og selvbetjeningen ber alltid om
    NØYAKTIG vertsnavnet (`wildcard=false`), så apex er ingen vei rundt.
    Slike nettsteder sto permanent uverifisert med en oppskrift som så riktig
    ut.

    De to veiene inn har hvert sitt krav, og testen måler begge:

    * FØRSTEGANGSVERIFISERINGEN slår opp utfordringsnavnet, og BARE det. Ett
      oppslag per rad — tidsbudsjettet bak `VERIFISERING_TAK` er utledet av
      nettopp det tallet.
    * REVALIDERINGEN må i tillegg tåle ARVEN: rader som ble verifisert før
      navnet fantes, har beviset på det bare vertsnavnet, og ville mistet
      autorisasjonen ved neste kjøring om bare det nye navnet ble slått opp.

    MUTASJONEN SOM DREPER DENNE: slå opp `hostname` i passet igjen, eller
    dropp `ogsa_vertsnavnet` i revalideringen.
    """
    from drift import domenerevalidering as dr

    assert dr.utfordringsnavn("www.dittfirma.no") == \
        "_disponit-challenge.www.dittfirma.no"

    # 1) Passet: bare utfordringsnavnet slås opp.
    spurt: list[str] = []

    def registrer(resolvere, hostname):
        spurt.append(hostname)
        return frozenset({"bevis"})

    monkeypatch.setattr(dr, "enig_svar", registrer)
    dr.kjor_ventende(_Tellekonn([("t", "www.dittfirma.no")]), resolvere=[])
    assert spurt == ["_disponit-challenge.www.dittfirma.no"], spurt

    # 2) Sonen svarer BARE på utfordringsnavnet — kundens virkelighet med et
    #    CNAME-vertsnavn. Passet skal likevel finne beviset.
    monkeypatch.setattr(
        dr, "enig_svar",
        lambda resolvere, h: (frozenset({"bevis"})
                              if h.startswith(dr.UTFORDRINGSPREFIKS) else None))
    konn = _Tellekonn([("t", "www.dittfirma.no")])
    res = dr.kjor_ventende(konn, resolvere=[])
    assert res["verifisert"] == 1, res
    assert konn.skrevet == ["www.dittfirma.no"], \
        "raden skrives på VERTSNAVNET; utfordringsnavnet er bare oppslaget"

    # 3) Arven: beviset ligger bare på det bare vertsnavnet (rad verifisert
    #    før navnet fantes). Revalideringen tar det med, passet gjør det ikke.
    monkeypatch.setattr(
        dr, "enig_svar",
        lambda resolvere, h: (frozenset({"arv"})
                              if h == "gammel.example" else frozenset()))
    assert dr.utfordringssvar([], "gammel.example",
                              ogsa_vertsnavnet=True) == frozenset({"arv"})
    assert dr.utfordringssvar([], "gammel.example",
                              ogsa_vertsnavnet=False) == frozenset()

    # 4) Ett navn nede river ikke et bevis vi FANT på det andre — men er
    #    begge uten svar, er svaret uenighet, ikke «ingen post».
    monkeypatch.setattr(
        dr, "enig_svar",
        lambda resolvere, h: (None if h.startswith(dr.UTFORDRINGSPREFIKS)
                              else frozenset({"arv"})))
    assert dr.utfordringssvar([], "gammel.example",
                              ogsa_vertsnavnet=True) == frozenset({"arv"})
    monkeypatch.setattr(dr, "enig_svar", lambda resolvere, h: None)
    assert dr.utfordringssvar([], "gammel.example",
                              ogsa_vertsnavnet=True) is None


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
        if hostname == dr.utfordringsnavn(treg):
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


def test_bred_resolverfeil_feller_verifiseringsunitten(monkeypatch):
    """Codex P2: `uenige` var en teller uten konsument. Var BEGGE resolverne
    nede, sto hver eneste selvbetjening stille — men passet returnerte 0, så
    `systemctl status` viste en vellykket aktivering hvert femte minutt.
    Revalideringen har hatt alarmkontrakten hele tiden; verifiseringen hadde
    den ikke.

    Terskelen er den samme (`ALARM_ANDEL`), og nevneren er radene som faktisk
    ble slått opp — rader vi ikke rakk før fristen sier ingenting om
    resolverne.

    MUTASJONEN SOM DREPER DENNE: la `main()` returnere 0 uansett, eller fjern
    `alarm_utlost` fra passet.
    """
    from drift import domenerevalidering as dr
    from drift import kjor_domeneverifisering as kv

    rader = [("t", f"h{i}.example") for i in range(5)]

    # 1) Alle oppslag feiler: terskelen slår inn.
    monkeypatch.setattr(dr, "enig_svar", lambda resolvere, hostname: None)
    res = dr.kjor_ventende(_Tellekonn(rader), resolvere=[])
    assert (res["uenige"], res["vurdert"]) == (5, 5), res
    assert res["alarm_utlost"] is True, res

    # 2) Kunder som ikke har lagt ut posten ennå er IKKE en resolverfeil:
    #    et autoritativt «ingen TXT» er et tomt SVAR, basen avviser beviset,
    #    og raden telles `ikke_bevist` — ingen alarm.
    monkeypatch.setattr(dr, "enig_svar",
                        lambda resolvere, hostname: frozenset())
    res = dr.kjor_ventende(
        _Falskkonn(psycopg.errors.InvalidParameterValue("ingen bevis")),
        resolvere=[])
    assert (res["ikke_bevist"], res["uenige"]) == (1, 0), res
    assert res["alarm_utlost"] is False, res

    # 3) ...og kontrakten er EXIT-KODEN, ikke et JSON-felt.
    class Tilkobling:
        def close(self):
            pass

    monkeypatch.setattr(kv, "resolvere", lambda: [])
    monkeypatch.setattr(kv, "_koble", lambda dsn: Tilkobling())
    monkeypatch.setenv("DISPONIT_DOMAINS_URL", "postgresql:///finnes-ikke")
    monkeypatch.setattr("db.hemmeligheter.last_credentials", lambda *a: None)
    monkeypatch.setattr(dr, "kjor_ventende",
                        lambda *a, **k: {"plukket": 5, "uenige": 5,
                                         "vurdert": 5, "alarm_utlost": True})
    assert kv.main() == 1, "bred resolverfeil ga fortsatt et vellykket pass"
    monkeypatch.setattr(dr, "kjor_ventende",
                        lambda *a, **k: {"plukket": 5, "verifisert": 5,
                                         "uenige": 0, "vurdert": 5,
                                         "alarm_utlost": False})
    assert kv.main() == 0, "et rolig pass ble rapportert som feilet"


def test_autoritativt_ingen_txt_er_et_svar_ikke_en_resolverfeil(monkeypatch):
    """Codex P2, motstykket til alarmen: NXDOMAIN/NoAnswer er et SVAR — vi vet
    at posten ikke står der. Kastet transporten på dem, havnet de i samme bøtte
    som en resolver som er nede, og alarmen over hadde stått rødt støtt: en
    fersk utfordring har jo ingen TXT-post ennå.

    Timeout og SERVFAIL slipper fortsatt ut som feil — det er nettopp dem
    alarmen skal se.

    MUTASJONEN SOM DREPER DENNE: fjern except-grenen i `_txt_oppslag`, eller
    la den fange alle DNS-unntak.
    """
    import sys
    import types

    from drift import kjor_revalidering as kr

    # dnspython er en DRIFTSavhengighet og skal ikke kreves for å måle dette
    # (samme grunn som den late importen i `_txt_oppslag`). Modulen stubbes med
    # unntaksklassene grenen faktisk navngir.
    class NXDOMAIN(Exception):
        pass

    class NoAnswer(Exception):
        pass

    class NoNameservers(Exception):
        pass

    class _FalskResolver:
        def __init__(self, feil):
            self.feil = feil
            self.nameservers: list = []
            self.lifetime = 0.0

        def resolve(self, hostname, rdtype):
            raise self.feil

    dns_pakke = types.ModuleType("dns")
    res_mod = types.ModuleType("dns.resolver")
    res_mod.NXDOMAIN, res_mod.NoAnswer = NXDOMAIN, NoAnswer
    res_mod.NoNameservers = NoNameservers
    dns_pakke.resolver = res_mod
    monkeypatch.setitem(sys.modules, "dns", dns_pakke)
    monkeypatch.setitem(sys.modules, "dns.resolver", res_mod)

    def _med(feil):
        res_mod.Resolver = lambda configure=False: _FalskResolver(feil)
        return kr._txt_oppslag("192.0.2.1")

    assert _med(NXDOMAIN())("x.example") == frozenset(), \
        "NXDOMAIN ble ikke båret som et tomt svar"
    assert _med(NoAnswer())("x.example") == frozenset(), \
        "NoAnswer ble ikke båret som et tomt svar"
    with pytest.raises(NoNameservers):
        _med(NoNameservers())("x.example")


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


@pg
def test_hver_domenestatus_har_en_etikett_i_begge_sprak(migrator):
    """Codex P3: `utlopt` er en gyldig databasetilstand, men manglet etikett i
    begge locale-filene — så flaten falt tilbake på råverdien, og den engelske
    brukeren fikk et norsk implementasjonsord som «status».

    Fasiten hentes fra CHECK-en i basen, ikke fra en liste her: en femte status
    som legges til i en fremtidig migrasjon skal FEILE her, ikke lekke som
    råtekst i UI-et.
    """
    import json as _json
    import pathlib
    import re

    defs = migrator.execute(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c"
        " WHERE c.conrelid = 'domenekontroll'::regclass AND c.contype = 'c'"
    ).fetchall()
    migrator.rollback()
    sjekk = [d[0] for d in defs if "'ventende'" in d[0]]
    assert sjekk, "fant ikke status-CHECK-en på domenekontroll"
    statuser = set(re.findall(r"'([a-z_]+)'::text", sjekk[0]))
    assert "utlopt" in statuser, statuser

    rot = pathlib.Path(__file__).resolve().parents[3]
    for fil in ("nb.json", "en.json"):
        kart = _json.loads(
            (rot / "locales" / fil).read_text(encoding="utf-8"))
        mangler = sorted(s for s in statuser
                         if not kart.get(f"domenestatus.{s}"))
        assert not mangler, f"{fil} mangler etikett for: {', '.join(mangler)}"


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
