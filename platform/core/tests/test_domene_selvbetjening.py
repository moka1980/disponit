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
    vert = f"kunde{secrets.token_hex(3)}.example.com"
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
    vert = f"gammel{secrets.token_hex(3)}.example.com"
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
    vert = f"laast{secrets.token_hex(3)}.example.com"
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
    v1 = f"fersk{secrets.token_hex(3)}.example.com"
    v2 = f"utgatt{secrets.token_hex(3)}.example.com"
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

    verter = [f"rot{i}{secrets.token_hex(3)}.example.com" for i in range(3)]
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
def test_209_reservert_tld_koes_aldri_i_verifiseringspasset(migrator):
    """#209, andre halvdel: samme klasse i utfordringskøen.

    Verifiseringspasset regner sin egen `uenige / vurdert > 0.20` med samme
    terskel som revalideringen, og en ventende utfordring på et navn som
    aldri kan resolves ville dratt den nevneren på nøyaktig samme måte. At
    det ikke blør i dag er en egenskap ved dagens fixturdata, ikke ved
    koden — så porten står her før hendelsen, ikke etter.

    Den ekte verten i samme kø er porten mot overfiksing: køen skal
    fortsatt levere den.
    """
    reservert = f"fasit{secrets.token_hex(3)}.test"
    ekte = f"kunde{secrets.token_hex(3)}.example.com"
    _utsted(migrator, reservert)
    _utsted(migrator, ekte)
    verter = {h for _, h in _alle_ventende(migrator)}
    assert ekte in verter, "den ekte utfordringen forsvant ut av køen"
    assert reservert not in verter, (
        f"reservert navn køet for verifisering: {reservert}")


@pg
def test_runtime_kan_utstede_men_aldri_bekrefte(migrator):
    """Sikkerhetssnittet: API-et (runtime) genererte tokenet og skal
    derfor ALDRI kunne bekrefte det — ellers var DNS-beviset valgfritt."""
    vert = f"snitt{secrets.token_hex(3)}.example.com"
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
    vert = f"kryss{secrets.token_hex(3)}.example.com"
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
    vert = f"selv{secrets.token_hex(3)}.example.com"
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
    # Samme port, andre halvdel (Codex P2): lengdegrensen er REGNET av
    # prefikset på begge sider, så den kan ikke drifte fra navneformen.
    from api import domener as apidom
    assert apidom._MAKS_UTFORDRET_VERTSNAVN == dr.MAKS_UTFORDRET_VERTSNAVN
    assert len(dr.utfordringsnavn("x" * dr.MAKS_UTFORDRET_VERTSNAVN)) \
        == dr.MAKS_DNS_NAVN, "grensen slipper igjennom et for langt DNS-navn"
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
def test_listen_svarer_den_EFFEKTIVE_autorisasjonen(migrator, klient):
    """Codex P2: `status` alene LYVER, med vilje.

    Basen lar en rad stå `verifisert` etter at 90-dagersvinduet (`utloper`)
    har passert ELLER den daglige revalideringen har vært borte i mer enn 72
    timer — det er `v_domeneautorisasjon.gyldig` som avgjør, og både egress og
    bestillingsporten avviser domenet da. Svarte listen bare `status`, fulgte
    kunden flaten hit, så «Verifisert», og fikk
    `bestilling_hostname_uverifisert` på neste bestilling.

    Regelen regnes av BASEN, med `DOMENE_GYLDIG_SQL` — samme tekst porten
    stiller sitt spørsmål med, og allerede mekanisk krysset mot visningen. En
    tredje kopi i klienten ville kunnet gli fra begge.

    MUTASJONEN SOM DREPER DENNE: la `_rader` svare `status` alene igjen.
    """
    from api import sesjon as sesjonmodul

    cookie, _ = _adminsesjon()
    ferskt = f"fersk{secrets.token_hex(3)}.example.com"
    foreldet = f"gml{secrets.token_hex(3)}.example.com"
    utlopt = f"utl{secrets.token_hex(3)}.example.com"
    a = _admin()
    try:
        for v in (ferskt, foreldet, utlopt):
            a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                      (TENANT, v))
        a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    # Foreldet revalidering (>72 t) og passert 90-dagersvindu — begge lar
    # raden stå `verifisert`, og begge gjør autorisasjonen ubrukelig.
    migrator.execute(
        "UPDATE domenekontroll SET siste_vellykkede_revalidering="
        "now()-interval '73 hours' WHERE tenant=%s AND hostname=%s",
        (TENANT, foreldet))
    migrator.execute(
        "UPDATE domenekontroll SET utloper=now()-interval '1 day'"
        " WHERE tenant=%s AND hostname=%s", (TENANT, utlopt))
    migrator.commit()

    r = klient.get("/v1/domener", cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    kart = {d["hostname"]: d for d in r.json()["domener"]}
    for v in (ferskt, foreldet, utlopt):
        assert kart[v]["status"] == "verifisert", \
            f"forutsetningen holder ikke — {v} står ikke `verifisert`"
    assert kart[ferskt]["gyldig"] is True, kart[ferskt]
    assert kart[foreldet]["gyldig"] is False, \
        "en foreldet revalidering ble meldt som gyldig autorisasjon"
    assert kart[utlopt]["gyldig"] is False, \
        "et passert 90-dagersvindu ble meldt som gyldig autorisasjon"


@pg
def test_listen_svarer_arsaken_bak_tilbakekallingen(migrator, klient):
    """Codex P2: `tilbakekalt` har TO opphav, og flaten forklarte begge som ett.

    Overtakelsen — en annen konto beviste DNS-kontroll — og den ORDINÆRE
    tilbakekallingen (`tilbakekall_domenekontroll`, 018: operatørens vei,
    som ikke rører `konflikt_motpart`) ender i samme statusord. Domenefanen
    valgte forklaring på det ordet alene og ga derfor kunden en falsk
    overtakelsesadvarsel — «DNS-kontroll er bevist av en annen konto» — for
    en administrativ eller driftsmessig tilbakekalling ingen har utfordret.

    Skillet finnes i basen fra før: `konflikt_motpart` settes av konflikten,
    beholdes gjennom avvisning og degradering, og NULLES av
    `verifiser_domenekontroll`. Svaret bærer det som en BOOLEAN — klienten
    skal vite AT det står en motpart bak, aldri HVEM.

    MUTASJONEN SOM DREPER DENNE: la `_rader` utelate `konflikt` igjen, eller
    utled den av `status`.
    """
    from api import sesjon as sesjonmodul

    from .test_pr015_operativt_lag import TREDJE_TENANT

    cookie, _ = _adminsesjon()
    ordinaer = f"ord{secrets.token_hex(3)}.example.com"
    avklaring = f"avk{secrets.token_hex(3)}.example.com"
    forbigatt = f"fbg{secrets.token_hex(3)}.example.com"
    # ÉN OVERGANG PER TRANSAKSJON, som resten av suiten: 041 §7-vakten er
    # en DEFERRABLE constraint-trigger, altså commit-tidspunktet. Slås flere
    # overganger for samme vertsnavn sammen, måles mellomtilstandene mot den
    # siste sakstilstanden, og vakten feller et oppsett som er lovlig steg
    # for steg.
    a = _admin()
    try:
        # 1) Ordinær tilbakekalling: TENANT eier navnet, operatøren trekker
        #    autorisasjonen. Ingen motpart, ingen konflikt.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, ordinaer))
        a.commit()
        a.execute("SELECT tilbakekall_domenekontroll(%s,%s,'opprydding','sys')",
                  (TENANT, ordinaer))
        a.commit()
        # 2) Åpen konflikt: ANNEN_TENANT eier, TENANT utfordrer.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, avklaring))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, avklaring))
        a.commit()
        # 3) Forbigått utfordrer: TENANT utfordrer, en TREDJE tar navnet, og
        #    degraderingen (019 §3.2) setter TENANT `tilbakekalt` — MED
        #    motparten i behold. Dette er den tilbakekallingen som FAKTISK
        #    er en overtakelse, og som skal beholde overtakelsesteksten.
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (ANNEN_TENANT, forbigatt))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, forbigatt))
        a.commit()
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TREDJE_TENANT, forbigatt))
        a.commit()
    finally:
        a.close()

    r = klient.get("/v1/domener", cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 200, r.text
    kart = {d["hostname"]: d for d in r.json()["domener"]}

    assert kart[ordinaer]["status"] == "tilbakekalt", kart[ordinaer]
    assert kart[forbigatt]["status"] == "tilbakekalt", kart[forbigatt]
    assert kart[avklaring]["status"] == "avklaring_kreves", kart[avklaring]

    assert kart[ordinaer]["konflikt"] is False, \
        "en ordinær tilbakekalling ble meldt som en overtakelse"
    assert kart[forbigatt]["konflikt"] is True, \
        "en forbigått utfordrer mistet overtakelsens årsak"
    assert kart[avklaring]["konflikt"] is True, kart[avklaring]

    # Årsaken, ALDRI motparten: ingen tenant-identitet i svaret (§6).
    kropp = r.text
    for fremmed in (ANNEN_TENANT, TREDJE_TENANT):
        assert fremmed not in kropp, \
            f"motpartens identitet lekket i domenelisten: {fremmed}"


def _vert_av_lengde(n: int) -> str:
    """Et lovlig, UNIKT vertsnavn på nøyaktig `n` tegn.

    Hver label ≤ 63 tegn og starter på en bokstav (så ingen label blir
    all-numerisk, som 018 avviser), og det er alltid minst to labels.
    """
    assert n > 64, "for kort til å bli mer enn én label"

    def label(lengde: int) -> str:
        return ("a" + secrets.token_hex(32))[:lengde]

    biter, igjen = [], n
    while igjen > 63:
        ta = min(63, igjen - 2)          # la alltid ≥ 1 tegn stå til slutt
        biter.append(label(ta))
        igjen -= ta + 1                  # labelen + punktumet
    biter.append(label(igjen))
    vert = ".".join(biter)
    assert len(vert) == n, (len(vert), n)
    return vert


@pg
def test_vertsnavn_uten_plass_til_utfordringen_avvises(migrator, klient):
    """Codex P2: en oppskrift som ikke KAN følges skal ikke svares med 201.

    Et vertsnavn på 234–253 tegn er selv fullt lovlig — API-regexen tar det,
    og `er_kanonisk_hostname` (018) gjerder på 253 — men
    `_disponit-challenge.` foran gir et navn over DNS-navnegrensen. Kunden
    kan ikke publisere det, og arbeiderens oppslag kan bare feile. Svaret var
    likevel 201 med en TXT-instruks som så helt riktig ut, og domenet ble
    stående uverifisert til utfordringen utløp — hver runde på nytt, uten at
    noe sted sa hvorfor.

    Grensen er REGNET av prefikset på begge sider av api/-grensen, så den
    følger navneformen i stedet for å være et tall som må huskes.

    MUTASJONEN SOM DREPER DENNE: fjern lengdeleddet i valideringen, eller
    sett `_MAKS_UTFORDRET_VERTSNAVN` til 253.
    """
    from api import sesjon as sesjonmodul
    from api.domener import _MAKS_UTFORDRET_VERTSNAVN as MAKS
    from drift import domenerevalidering as dr

    cookie, csrf = _adminsesjon()
    # Ett tegn for langt: navnet er lovlig, utfordringen for det er det ikke.
    for_langt = _vert_av_lengde(MAKS + 1)
    r = klient.post("/v1/domener", json={"hostname": for_langt},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert (r.status_code, r.json()["feil"]) == (400, "request_feilformet"), \
        r.text
    assert len(dr.utfordringsnavn(for_langt)) > dr.MAKS_DNS_NAVN
    # Ingen rad ble skrevet: avvisningen skjer FØR utstedelsen.
    _sett_kontekst(migrator, TENANT)
    n = int(migrator.execute(
        "SELECT count(*) FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (TENANT, for_langt)).fetchone()[0])
    migrator.rollback()
    assert n == 0, "utfordringen ble utstedt for et navn den ikke får plass i"

    # Nøyaktig på grensen slipper igjennom — gjerdet skal ikke være for stramt.
    akkurat = _vert_av_lengde(MAKS)
    r = klient.post("/v1/domener", json={"hostname": akkurat},
                    headers={"X-Disponit-CSRF": csrf},
                    cookies={sesjonmodul.C_SESJON: cookie})
    assert r.status_code == 201, r.text
    assert len(r.json()["txt_navn"]) == dr.MAKS_DNS_NAVN


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




def _dyp_kropp() -> tuple[str, bool]:
    """(kropp, feller_parseren): dypeste kropp under kroppsgrensen, og om
    `json.loads` HER faktisk kaster RecursionError på den.

    Taket flytter seg mellom Python-versjoner — MÅLT: 3.12 feller ved
    15 000 nivåer (~90 kB); 3.14 først ved ~200 000, som er 1,2 MB kropp og
    ligger OVER kroppsgrensen. Der taket ikke kan nås under grensen, er
    RecursionError-veien UOPPNÅELIG for klientinput i dette miljøet, og
    løftet som gjenstår å måle er at den dypeste lovlige kroppen får det
    dokumenterte 400-svaret — aldri en 500. Målt her, aldri antatt.

    Codex P2: «lovlig» er KROPPSGRENSEN, ikke et rundt tall i nærheten av
    den — og «dypest» er klientens BILLIGSTE nøstingsform, ikke vår.
    Dypeste probe var først 33 000 nivåer = 198 001 B mot en grense på
    262 144 B; så ble dybden utledet AV grensen, men fremdeles i objektform
    (`{"a":` + `}` = 6 byte per nivå), som stanser på 43 690 nivåer.
    Nøstede ARRAYER koster 2 byte per nivå, så den samme grensen slipper
    gjennom 131 072 nivåer — og på 3.14 parses 43 690 objektnivåer greit
    mens array-formen feller parseren. En probe som måler sin egen dyre
    form melder altså «uoppnåelig» om et tak klienten fortsatt når, og
    testen blir grønn selv om `RecursionError` fjernes fra except-en.
    Formen er derfor arrayer, og dybden utledes av grensen.
    """
    import json as jsonmodul

    from api.app import MAKS_KROPP
    dypest = MAKS_KROPP // 2            # 2*d <= MAKS_KROPP (`[`+`]`)
    kropp = ""
    for dybde in (15000, 33000, dypest):
        kropp = "[" * dybde + "]" * dybde
        assert len(kropp) <= MAKS_KROPP, \
            "testkroppen skal ligge innenfor kroppsgrensen"
        try:
            jsonmodul.loads(kropp)
        except RecursionError:
            return kropp, True
    return kropp, False


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
    `json.loads` i `utsted_endepunkt` — på en Python der taket kan nås
    under kroppsgrensen (se `_dyp_kropp`).
    """
    from api import sesjon as sesjonmodul
    cookie, csrf = _adminsesjon()
    kropp, feller = _dyp_kropp()
    r = klient.post("/v1/domener", content=kropp,
                    headers={"X-Disponit-CSRF": csrf,
                             "content-type": "application/json"},
                    cookies={sesjonmodul.C_SESJON: cookie})
    # ÉN kontrakt, to veier inn til den (Codex P2): feller parseren HER, er
    # dette RecursionError-veien; makter den kroppen, er dokumentet gyldig
    # JSON med ugyldig toppform — en liste, ikke et objekt, som
    # `not isinstance(data, dict)` avviser. Begge veier
    # lover NØYAKTIG `request_feilformet` — aldri en generisk 500, og aldri
    # en annen feilkode som en regresjon måtte finne på. Fallbacken sluttet
    # å måle koden og godtok enhver ikke-tom `feil`; da var den grønn også
    # for feil den var satt til å fange. `feller` følger med i
    # feilmeldingen, så en rød test sier hvilken vei den tok.
    assert (r.status_code, r.json().get("feil")) == (
        400, "request_feilformet"), (feller, r.text)


@pg
def test_verifiseringspasset_ende_til_ende(migrator, klient):
    """Arbeiderpasset med en fake-resolver: challenge utstedt over HTTP,
    TXT «i sonen», pass → verifisert — og bestillingsveiens
    hostname-port åpner seg (integrasjonen selvbetjeningen finnes for)."""
    import sys
    from api import sesjon as sesjonmodul
    from drift import domenerevalidering as dr

    cookie, csrf = _adminsesjon()
    vert = f"e2e{secrets.token_hex(3)}.example.com"
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
        vert = f"koe{secrets.token_hex(4)}.example.com"
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
    vert = f"m37{secrets.token_hex(4)}.example.com"
    _utsted(migrator, vert)
    _sett_kontekst(migrator, TENANT)
    # 041: avklaring uten sak avvises ved commit (port 2) — fixturen
    # gjenskaper pre-041-tilstanden med vakten av, som _rydd gjør for
    # append-only-triggerne. Selve porten måles i 041-suiten.
    migrator.execute("ALTER TABLE domenekontroll DISABLE TRIGGER"
                     " domenekontroll_avklaring_krever_sak")
    migrator.execute(
        "UPDATE domenekontroll SET status='avklaring_kreves'"
        " WHERE tenant=%s AND hostname=%s", (TENANT, vert))
    migrator.execute("ALTER TABLE domenekontroll ENABLE TRIGGER"
                     " domenekontroll_avklaring_krever_sak")
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
                    json={"hostname": f"drift{secrets.token_hex(3)}.example.com"},
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
    vert = f"reapp{secrets.token_hex(4)}.example.com"
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
def test_209_reservert_tld_koes_aldri_paa_reapplikasjonsarmen(migrator):
    """Cursor P2-1 (runde 2): plukket har TO innganger, ikke én.

    `ventende_domenechallenges` slipper inn `ventende` ELLER `tilbakekalt`
    med motpart (reapplikasjonsgrenen 018 kjenner igjen). Predikatet står i
    dag etter hele OR-gruppen og dekker begge — men den forrige #209-porten
    seeder kun `ventende` via `_utsted`, så bare den ene armen er målt.

    MUTASJONEN SOM DREPER DENNE: flytt `AND NOT er_reservert_tld` inn i
    `ventende`-grenen inne i OR-paren. AND binder tettere enn OR, så
    uttrykket blir `(ventende ∧ ¬reservert) ∨ (tilbakekalt ∧ motpart)`:
    reserverte reapplikasjonsrader plukkes igjen, stemples, DNS-feiler og
    drar `uenige/vurdert` mot ALARM_ANDEL. Den forrige porten forblir grønn.

    Samme defektklasse som kø1-vs-kø2/3-splitten på revalideringssiden — én
    arm målt, den andre ikke.
    """
    verter = {"reservert": f"reapp{secrets.token_hex(4)}.test",
              "ekte": f"reapp{secrets.token_hex(4)}.example.com"}
    for vert in verter.values():
        a = _admin()
        try:
            a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                      (TENANT, vert))
            a.commit()
            a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                      (ANNEN_TENANT, vert))
            a.commit()
            gen = _gen(migrator, ANNEN_TENANT, vert)
            a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'m37')",
                      (ANNEN_TENANT, vert, gen))
            a.commit()
        finally:
            a.close()
        rt = _rt()
        try:
            _sett_kontekst(rt, ANNEN_TENANT)
            rt.execute("SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                       (ANNEN_TENANT, vert,
                        hashlib.sha256(secrets.token_hex(32).encode()).hexdigest()))
            rt.commit()
        finally:
            rt.close()

    # Begge står nå `tilbakekalt` med motpart og et levende challenge-vindu —
    # altså på reapplikasjonsarmen, ikke på `ventende`.
    _sett_kontekst(migrator, ANNEN_TENANT)
    statuser = {h: s for h, s in migrator.execute(
        "SELECT hostname, status FROM domenekontroll WHERE hostname = ANY(%s)",
        (list(verter.values()),)).fetchall()}
    migrator.rollback()
    assert set(statuser.values()) == {"tilbakekalt"}, statuser

    koet = {h for _, h in _alle_ventende(migrator)}
    assert verter["ekte"] in koet, (
        "den ekte reapplikasjonen falt ut av køen — overfiksing")
    assert verter["reservert"] not in koet, (
        f"reservert navn køet på reapplikasjonsarmen: {verter['reservert']}")


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
                 "degrader_forbigatte_utfordrere(text,text)",
                 # Selvbetjeningens inngang skriver NED en naturlig utløpt
                 # verifisering (Codex P1) og er dermed en statusovergang som
                 # alle andre — den måles på samme regel.
                 "utsted_challenge_selvbetjent(text,text,boolean,text,text)"):
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
def test_naturlig_utlopt_verifisering_fornyes_selvbetjent(migrator):
    """Codex P1: 90-dagersvinduet må ha en vei UT, ikke bare inn.

    `utloper` er `verifisert_ts + 90 døgn`, og ingen jobb flytter statusen
    når den passerer — revalideringen friskmelder bare
    `siste_vellykkede_revalidering`. Raden ble derfor stående `verifisert`
    med en autorisasjon `v_domeneautorisasjon.gyldig` (og dermed
    bestillingsveien) forkastet, og den ENE selvbetjente handlingen som
    finnes — legg domenet til på nytt — byttet hashen uten å flytte
    statusen. `ventende_domenechallenges` plukker ikke `verifisert`, så
    beviset ble aldri lest: 201 med fersk oppskrift, og domenet permanent
    ubrukelig etter 90 døgn.

    Målt her hele veien: utstedelsen skriver raden ned til `utlopt` (samme
    overgang 018 gjør ved overføring, med ny generasjon), køingen tar den
    derfra, arbeideren SER den, og beviset gir et NYTT vindu.

    MUTASJONEN SOM DREPER DENNE: fjern nedskrivningen i
    `utsted_challenge_selvbetjent` — raden blir da stående `verifisert`, og
    plukket er tomt.
    """
    vert = f"fornyelse{secrets.token_hex(4)}.example.com"
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, vert))
        a.commit()
    finally:
        a.close()
    # 90 døgn er gått. Ingenting annet endres: det er nettopp poenget at
    # raden fortsatt PÅSTÅR `verifisert`.
    _sett_kontekst(migrator, TENANT)
    migrator.execute(
        "UPDATE domenekontroll SET verifisert_ts=now()-interval '91 days',"
        " utloper=now()-interval '1 day' WHERE tenant=%s AND hostname=%s",
        (TENANT, vert))
    migrator.commit()
    _sett_kontekst(migrator, TENANT)
    for_ = migrator.execute(
        "SELECT d.status, v.gyldig FROM domenekontroll d"
        " JOIN v_domeneautorisasjon v USING (tenant, hostname)"
        " WHERE d.tenant=%s AND d.hostname=%s", (TENANT, vert)).fetchone()
    migrator.rollback()
    assert for_ == ("verifisert", False), \
        f"forutsetningen holder ikke — blindveien finnes ikke: {for_}"
    gen0 = _gen(migrator, TENANT, vert)

    token = secrets.token_hex(32)
    h = hashlib.sha256(token.encode()).hexdigest()
    rt = _rt()
    try:
        _sett_kontekst(rt, TENANT)
        rt.execute("SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                   (TENANT, vert, h))
        rt.commit()
    finally:
        rt.close()

    _sett_kontekst(migrator, TENANT)
    etter = migrator.execute(
        "SELECT status, autorisasjonsgenerasjon FROM domenekontroll"
        " WHERE tenant=%s AND hostname=%s", (TENANT, vert)).fetchone()
    # Nedskrivningen skal stå i den append-only historikken, ikke bare i
    # statusen: en autorisasjon som opphører er en hendelse, ikke en detalj.
    utlopshendelse = migrator.execute(
        "SELECT count(*) FROM domenekontroll_hendelse WHERE tenant=%s"
        " AND hostname=%s AND grunn='naturlig_utlopt'",
        (TENANT, vert)).fetchone()[0]
    migrator.rollback()
    assert etter[0] == "ventende", \
        f"den utløpte verifiseringen ble ikke køet på nytt: {etter}"
    assert etter[1] > gen0, "nedskrivningen ga ingen ny autorisasjonsgenerasjon"
    assert utlopshendelse == 1, "utløpet ble skrevet ned uten revisjonsspor"

    # Arbeideren ser den — ellers er oppskriften fortsatt uten leser.
    assert (TENANT, vert) in _alle_ventende(migrator), \
        "det fornyede domenet plukkes ikke av arbeideren"

    _sett_kontekst(migrator, TENANT)
    svar = _som_eier(migrator, "SELECT bekreft_domenechallenge(%s,%s,'w',%s)",
                     (TENANT, vert, [token]))[0]
    migrator.commit()
    assert svar == "verifisert", svar
    _sett_kontekst(migrator, TENANT)
    fornyet = migrator.execute(
        "SELECT d.status, d.utloper > now(), v.gyldig FROM domenekontroll d"
        " JOIN v_domeneautorisasjon v USING (tenant, hostname)"
        " WHERE d.tenant=%s AND d.hostname=%s", (TENANT, vert)).fetchone()
    migrator.rollback()
    assert fornyet == ("verifisert", True, True), \
        f"fornyelsen ga ikke et nytt gyldig vindu: {fornyet}"


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
    vert = f"avvist{secrets.token_hex(4)}.example.com"
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

    vert = f"forbigatt{secrets.token_hex(4)}.example.com"
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


def _engangsopprydningen() -> str:
    """Engangssetningen fra 039, hentet ut av migrasjonsteksten selv.

    Den kjører ÉN gang, ved utrulling, mot en base som alt har rader — og er
    dermed umulig å måle gjennom fikstur-basen der migrasjonen for lengst er
    kjørt. Setningen leses derfor ut og kjøres mot tilstand testen bygger selv,
    på nøyaktig den formen som faktisk rulles ut.
    """
    from .conftest import CORE

    sql = (CORE / "db/migrations/039_domene_selvbetjening.sql").read_text(
        encoding="utf-8")
    anker = "UPDATE public.domenekontroll d\n   SET challenge_token_hash"
    assert sql.count(anker) == 1, \
        "engangsopprydningen finnes ikke lenger på den formen testen måler"
    start = sql.index(anker)
    return sql[start:sql.index(";", start) + 1]


@pg
def test_opprydningen_sparer_en_ventende_reapplikasjon(migrator):
    """Codex P2: engangsopprydningen slettet operatørens reapplikasjon.

    039 forbruker utfordringen i BEGGE utgangene av avklaringen, og rydder
    én gang etter de utgangene som ble gjort FØR migrasjonen. Den ryddingen
    tok hver `tilbakekalt`-rad med motpart og levende hash — på antakelsen om
    at en slik rad ikke KAN ha fått en ny utfordring, siden selvbetjeningen
    åpnes her.

    Antakelsen holdt ikke: `utsted_challenge` har hele tiden vært grantet til
    `disponit_domene_eier`, og en operatør som utstedte på nytt for en avvist
    kandidat var nettopp den manuelle reapplikasjonsveien. Den kunden har alt
    publisert det NYE tokenet sitt — og opprydningen slettet hashen under
    henne, så posten ble ubeviselig uten at noen ba henne gjøre noe.

    Skillet er hendelsesloggen: utstedt ETTER siste `avklaring_avvist`/
    `forbigatt` er en ventende reapplikasjon, utstedt før er den etterlatte
    posten. Alle radene her bærer samme signatur; bare rekkefølgen skiller.

    Rad C måler at rekkefølgen leses av hendelsenes sekvens-ID-er og ikke av
    `now()` (Codex P2): operatørens transaksjon åpnes FØR avvisningen, men
    utsteder først etter at den har committet. Transaksjonsklokka gir da et
    `challenge_utstedt` som ser eldre ut enn avvisningen det kom etter.

    MUTASJONEN SOM DREPER DENNE: la opprydningen predikere på signaturen
    alene (uten hendelsesrekkefølgen), bytt `max(id)` mot «finnes en eldre
    utgang» — da fredes en hash som nettopp ble etterlatt i runde to — eller
    bytt sekvens-ID-ene tilbake mot `ts`, som feller rad C.
    """
    def _sett_utfordring(vert, token, utstedt_sql):
        """Den ETTERLATTE hashen: satt rett på raden, uten en ny
        `challenge_utstedt`-hendelse — nøyaktig 019-formens utfall, der
        utfordringen ble stående fra FØR utgangen av avklaringen."""
        h = hashlib.sha256(token.encode()).hexdigest()
        migrator.execute("SET LOCAL ROLE disponit_domene_eier")
        migrator.execute(
            "UPDATE domenekontroll SET challenge_token_hash=%s,"
            f" challenge_utstedt={utstedt_sql},"
            " challenge_utloper=now()+interval '6 days'"
            " WHERE tenant=%s AND hostname=%s", (h, ANNEN_TENANT, vert))
        migrator.execute("RESET ROLE")
        migrator.commit()

    def _reapplikasjon(conn, vert):
        """Operatørens manuelle vei tilbake: `utsted_challenge` som
        `disponit_domene_eier`, som stempler raden OG legger hendelsen."""
        conn.execute("SELECT utsted_challenge(%s,%s,false,%s,'operator')",
                     (ANNEN_TENANT, vert,
                      hashlib.sha256(
                          secrets.token_hex(32).encode()).hexdigest()))
        conn.commit()

    def _eierkonn():
        from db.pg import koble
        c = koble(MIGRATOR_DSN)
        c.execute("SET ROLE disponit_domene_eier")
        c.commit()
        return c

    def _utstedt_og_utgang(vert):
        """(`challenge_utstedt` på raden, `ts` for siste utgang) — begge
        transaksjonsklokker, som er nettopp det opprydningen IKKE leser."""
        migrator.execute("SET LOCAL ROLE disponit_domene_eier")
        rad = migrator.execute(
            "SELECT d.challenge_utstedt,"
            " (SELECT max(h.ts) FROM domenekontroll_hendelse h"
            "   WHERE h.tenant=d.tenant AND h.hostname=d.hostname"
            "     AND h.hendelse IN ('avklaring_avvist','forbigatt'))"
            " FROM domenekontroll d WHERE d.tenant=%s AND d.hostname=%s",
            (ANNEN_TENANT, vert)).fetchone()
        migrator.execute("RESET ROLE")
        migrator.rollback()
        return rad

    def _hash(vert):
        migrator.execute("SET LOCAL ROLE disponit_domene_eier")
        rad = migrator.execute(
            "SELECT status, konflikt_motpart, challenge_token_hash,"
            " challenge_utstedt, challenge_utloper, challenge_forsokt"
            " FROM domenekontroll WHERE tenant=%s AND hostname=%s",
            (ANNEN_TENANT, vert)).fetchone()
        migrator.execute("RESET ROLE")
        migrator.rollback()
        return rad

    def _avvist_kandidat():
        """En rad i den tilstanden opprydningen ser etter: `tilbakekalt` med
        motpart, etter en ekte M-37-avvisning (som legger `avklaring_avvist`
        i hendelsesloggen)."""
        vert = f"rydd{secrets.token_hex(4)}.example.com"
        token = secrets.token_hex(32)
        a = _admin()
        try:
            a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                      (TENANT, vert))
            a.commit()
        finally:
            a.close()
        rt = _rt()
        try:
            _sett_kontekst(rt, ANNEN_TENANT)
            rt.execute(
                "SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,'rt')",
                (ANNEN_TENANT, vert,
                 hashlib.sha256(token.encode()).hexdigest()))
            rt.commit()
        finally:
            rt.close()
        svar = _som_eier(
            migrator, "SELECT bekreft_domenechallenge(%s,%s,'w',%s)",
            (ANNEN_TENANT, vert, [token]))[0]
        migrator.commit()
        assert svar == f"konflikt:{TENANT}", svar
        gen = _gen(migrator, ANNEN_TENANT, vert)
        a = _admin()
        try:
            a.execute("SELECT avgjor_domeneovertakelse(%s,%s,%s,false,'m37')",
                      (ANNEN_TENANT, vert, gen))
            a.commit()
        finally:
            a.close()
        return vert

    # A) Den ETTERLATTE posten: 019-formens utfall — utfordringen sto igjen
    #    fra konflikten, altså utstedt FØR avvisningen.
    etterlatt = _avvist_kandidat()
    _sett_utfordring(etterlatt, secrets.token_hex(32),
                     "now()-interval '1 day'")
    # B) Operatørens REAPPLIKASJON: samme signatur, men utstedt ETTER
    #    avvisningen — kunden har det nye tokenet og har publisert det.
    reapplikasjon = _avvist_kandidat()
    op = _eierkonn()
    try:
        _reapplikasjon(op, reapplikasjon)
        # C) Samme reapplikasjon, men fra en transaksjon som ALT var åpen da
        #    avvisningen ble gjort. `now()` er transaksjonens starttid, så
        #    stempelet ser eldre ut enn avvisningen det kom etter — mens
        #    hendelsens sekvens-ID, tildelt ved INSERT, står i riktig
        #    rekkefølge. Den første setningen åpner transaksjonen.
        op.execute("SELECT now()")
        forsinket = _avvist_kandidat()
        _reapplikasjon(op, forsinket)
    finally:
        op.close()

    utstedt, utgang = _utstedt_og_utgang(forsinket)
    assert utstedt < utgang, (
        "oppsettet ga ikke den inverterte klokka scenariet handler om "
        f"({utstedt} skulle vært før {utgang})")

    for vert in (etterlatt, reapplikasjon, forsinket):
        rad = _hash(vert)
        assert rad[0] == "tilbakekalt" and rad[1] == TENANT, (vert, rad)
        assert rad[2] is not None, (vert, rad)

    migrator.execute("SET LOCAL ROLE disponit_domene_eier")
    migrator.execute(_engangsopprydningen())
    migrator.execute("RESET ROLE")
    migrator.commit()

    assert _hash(etterlatt)[2] is None, \
        "den etterlatte posten overlevde opprydningen — reapplikasjons" \
        "plukket tar raden med det samme, på et bevis kunden aldri fornyet"
    for vert, hva in ((reapplikasjon, "operatørens reapplikasjon"),
                      (forsinket, "reapplikasjonen fra en transaksjon åpnet "
                                  "før avvisningen")):
        beholdt = _hash(vert)
        assert beholdt[2] is not None and beholdt[4] is not None, \
            f"opprydningen slettet {hva} — utstedt ETTER avvisningen, med " \
            "kundens nye TXT-post alt i sonen"
        # Og reapplikasjonen er fortsatt en rad arbeideren kan bevise.
        assert (ANNEN_TENANT, vert) in _alle_ventende(migrator), \
            f"{hva} ble bevart, men likevel ikke plukket"


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
    """041: en overtakelse UTEN sak kan ikke lenger OPPSTÅ — saken lages av
    `sikre_overtakelsessak()` i samme transaksjon som konflikten, og
    `domenekontroll_avklaring_krever_sak` avviser resten ved commit.
    Dreneringen er blitt en VAKT: den bekrefter at invarianten holder på
    denne basen, og navngir enhver pre-041-rad som står igjen uten sak.

    MUTASJONEN SOM DREPER DENNE: la vakten returnere tomt uten å slå opp —
    da forsvinner både `med_sak`-bekreftelsen og `uten_sak`-alarmen.
    """
    from api import domeneovertakelse as dov

    vert = f"kfl{secrets.token_hex(4)}.example.com"
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

    # Saken finnes ALLEREDE — det er hele 041-poenget.
    _sett_kontekst(migrator, "__plattform_domener")
    assert migrator.execute(
        "SELECT count(*) FROM unntak WHERE hostname_ref=%s"
        "  AND sakskilde='domeneovertakelse' AND NOT terminal",
        (vert,)).fetchone()[0] == 1
    migrator.rollback()

    # Vakten bekrefter den — over ARBEIDERENS forbindelse, som i drift.
    rt = _arbeiderkonn()
    try:
        res = dov.vokt_ventende_overtakelseskonflikter(rt, grense=500)
        # Scopet til VÅR konflikt: basen deles med andre suiters residualer.
        assert [u for u in res["uten_sak"] if u["hostname"] == vert] == [], res
        assert res["med_sak"] >= 1, res
    finally:
        rt.close()

    # ... og en pre-041-rad UTEN sak (kirurgisk gjenskapt) NAVNGIS.
    vert2 = f"kfl{secrets.token_hex(4)}.example.com"
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, vert2))
        a.commit()
    finally:
        a.close()
    _sett_kontekst(migrator, TENANT)
    migrator.execute("ALTER TABLE domenekontroll DISABLE TRIGGER"
                     " domenekontroll_avklaring_krever_sak")
    migrator.execute(
        "UPDATE domenekontroll SET status='avklaring_kreves',"
        " konflikt_motpart=%s WHERE tenant=%s AND hostname=%s",
        (ANNEN_TENANT, TENANT, vert2))
    migrator.execute("ALTER TABLE domenekontroll ENABLE TRIGGER"
                     " domenekontroll_avklaring_krever_sak")
    migrator.commit()
    rt = _arbeiderkonn()
    try:
        res = dov.vokt_ventende_overtakelseskonflikter(rt, grense=500)
    finally:
        rt.close()
    gen2 = _gen(migrator, TENANT, vert2)
    assert {"tenant": TENANT, "hostname": vert2,
            "generasjon": gen2} in res["uten_sak"], res


class _Kapplop:
    """Conn-innpakning som kjører et kappløp i ET bestemt mellomrom.

    `gjor()` kalles ÉN gang, rett FØR den setningen som inneholder `naar`
    sendes til basen — altså nøyaktig i vinduet dreneringen har mellom det
    committede plukket og porten foran saksopprettelsen. Alt annet går rett
    videre til den ekte forbindelsen.
    """

    def __init__(self, conn, naar, gjor):
        self._c, self._naar, self._gjor = conn, naar, gjor
        self.kjort = False

    def execute(self, sql, args=None):
        if not self.kjort and self._naar in sql:
            self.kjort = True
            self._gjor()
        return self._c.execute(sql, args)

    def commit(self):
        self._c.commit()

    def rollback(self):
        self._c.rollback()

    def __getattr__(self, navn):     # alt annet er den ekte forbindelsens
        return getattr(self._c, navn)


@pg
def test_foreldet_konflikt_far_ingen_sak(migrator):
    """041-formen: det gamle kappløpet (sak laget fra et FORELDET pluk) kan
    ikke lenger oppstå — saken lages under hostname-låsen i samme
    transaksjon som konflikten, og et SKIFTE (A→B→A) oppdaterer SAMME sak
    til gjeldende utfordrer og generasjon. Vakten dømmer mot de FERSKE
    verdiene plukket bærer, så en sak som fulgte skiftet bekreftes — den
    meldes ikke som manglende.
    """
    from api import domeneovertakelse as dov

    vert = f"foreldet{secrets.token_hex(4)}.example.com"
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, vert))
        a.commit()
        svar = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (ANNEN_TENANT, vert)).fetchone()[0]
        a.commit()
        assert svar == f"konflikt:{TENANT}", svar
        # TENANT beviser DNS-kontroll igjen: skiftet flytter saken til
        # TENANT som utfordrer, ny generasjon — samme sak-id (port 6).
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, vert))
        a.commit()
    finally:
        a.close()

    _sett_kontekst(migrator, "__plattform_domener")
    rad = migrator.execute(
        "SELECT utfordrer_tenant, saksrevisjon FROM unntak"
        " WHERE hostname_ref=%s AND sakskilde='domeneovertakelse'"
        "   AND NOT terminal", (vert,)).fetchone()
    migrator.rollback()
    assert rad == (TENANT, 1), f"skiftet fulgte ikke saken: {rad}"

    rt = _arbeiderkonn()
    try:
        res = dov.vokt_ventende_overtakelseskonflikter(rt, grense=500)
    finally:
        rt.close()
    assert [u for u in res["uten_sak"] if u["hostname"] == vert] == [], \
        f"en sak som fulgte skiftet ble meldt som manglende: {res}"


@pg
def test_vakten_revaliderer_plukket_for_den_roper(migrator):
    """Codex P2: et kappløp er ikke et invariantbrudd.

    Plukket COMMITTER (stempelet er rotasjonen) og slipper radlåsen før
    vakten rekker å slå opp saken. Tar en TREDJE tenant hostnavnet i det
    mellomrommet, degraderes den plukkede utfordreren til `tilbakekalt`
    med ny generasjon (019 §3.2) og den åpne saken revideres til C — og
    tuppelen vakten fortsatt holder er da historie. Oppslaget på den svarer
    korrekt nei, men konklusjonen «konflikt uten sak» er feil: konflikten
    finnes ikke lenger, og den som finnes HAR sin sak. Alarmen ville
    navngitt en tenant som ikke feiler noe og krevd en operatør for et
    kappløp — hver syklus, til noen så etter.

    Vakten spør derfor 039s `bekreft_overtakelseskonflikt` under
    hostnavnets lås før den roper: står status, motpart og generasjon
    fortsatt som plukket så dem?

    MUTASJONEN SOM DREPER DENNE: fjern revalideringen (eller la den telle
    `foreldet` OG legge raden i `uten_sak`).
    """
    from api import domeneovertakelse as dov

    from .test_pr015_operativt_lag import TREDJE_TENANT

    vert = f"kappl{secrets.token_hex(4)}.example.com"
    a = _admin()
    try:
        a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                  (TENANT, vert))       # A eier navnet
        a.commit()
        svar = a.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                         (ANNEN_TENANT, vert)).fetchone()[0]   # B utfordrer
        a.commit()
    finally:
        a.close()
    assert svar == f"konflikt:{TENANT}", svar

    def _c_tar_navnet():
        """Kappløpet: C overtar mellom det committede plukket og oppslaget."""
        k = _admin()
        try:
            k.execute("SELECT verifiser_domenekontroll(%s,%s,false,'sys')",
                      (TREDJE_TENANT, vert))
            k.commit()
        finally:
            k.close()

    class _KappPaVart(_Kapplop):
        """`_Kapplop`, men vinduet er VÅRT hostnavns.

        Basen deles med andre suiters residualer, og plukket sorterer på
        `konflikt_drenert` — hvilken rad som slås opp først er derfor ikke
        vår å bestemme. Utløseren leser argumentene, ikke bare SQL-teksten,
        så kappløpet treffer nøyaktig raden testen måler.
        """

        def execute(self, sql, args=None):
            if (not self.kjort and self._naar in sql
                    and args and vert in args):
                self.kjort = True
                self._gjor()
            return self._c.execute(sql, args)

    rt = _arbeiderkonn()
    try:
        kapp = _KappPaVart(rt, "overtakelsessak_finnes", _c_tar_navnet)
        res = dov.vokt_ventende_overtakelseskonflikter(kapp, grense=500)
    finally:
        rt.close()
    assert kapp.kjort, "kappløpet traff aldri vinduet det måler"

    # B ER foreldet: degraderingen tok raden ut av `avklaring_kreves`.
    _sett_kontekst(migrator, ANNEN_TENANT)
    assert migrator.execute(
        "SELECT status FROM domenekontroll WHERE tenant=%s AND hostname=%s",
        (ANNEN_TENANT, vert)).fetchone()[0] == "tilbakekalt"
    migrator.rollback()

    assert [u for u in res["uten_sak"] if u["hostname"] == vert] == [], \
        f"et kappløp ble meldt som konflikt uten sak: {res}"
    assert res["foreldet"] >= 1, f"den foreldede tuppelen ble ikke talt: {res}"

    # ...og C, som ER dagens konflikt, har sin sak. Neste syklus plukker den.
    rt = _arbeiderkonn()
    try:
        res2 = dov.vokt_ventende_overtakelseskonflikter(rt, grense=500)
    finally:
        rt.close()
    assert [u for u in res2["uten_sak"] if u["hostname"] == vert] == [], \
        f"dagens konflikt mangler sak: {res2}"


def test_dreneringen_skiller_radfeil_fra_utrullingsfeil(migrator):
    """041-formen: vakten har ingen radfeil igjen å telle (ingen DEK, ingen
    DML) — men utrullingens feil skal fortsatt VELTE den, aldri bli et tall
    i et resultat ingen alarmerer på. En DB-feil fra selve plukket
    propagerer til kalleren; M-37-innpakningen (testen under) navngir og
    kaster videre.

    MUTASJONEN SOM DREPER DENNE: pakk vaktens plukk i en bred
    `except psycopg.Error` som teller i stedet for å kaste.
    """
    from api import domeneovertakelse as dov

    class _Velter:
        """Konn som feiler på selve plukket — utrullingsfeilen, in situ."""

        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, args=None):
            if "ventende_overtakelseskonflikter" in sql:
                raise psycopg.errors.UndefinedFunction("ikke utrullet")
            return self._c.execute(sql, args)

        def __getattr__(self, navn):
            return getattr(self._c, navn)

    rt = _arbeiderkonn()
    try:
        with pytest.raises(psycopg.errors.UndefinedFunction):
            dov.vokt_ventende_overtakelseskonflikter(_Velter(rt), grense=10)
        # Forbindelsen er brukbar etterpå: feilen er signalet, ikke en
        # ødelagt tilkobling kalleren må gjette seg til.
        rt.rollback()
        assert rt.execute("SELECT 1").fetchone()[0] == 1
    finally:
        rt.close()


def test_dreneringen_navngir_utrullingsfeil_og_feller_arbeideren(monkeypatch):
    """M-37-innpakningen svelger ikke vaktens feil — og en konflikt uten sak
    er ALDRI stille.

    `drener_domenekonflikter` navngir en DB-feil i journalen og kaster den
    videre (heartbeat `ok` skal ikke være et friskhetstegn for en arbeider
    som ikke ser noe); et `uten_sak`-funn printes HVER syklus til det er
    borte.

    MUTASJONEN SOM DREPER DENNE: bytt `raise` mot `return`, eller fjern
    `uten_sak`-printen.
    """
    import json as jsonmodul

    from api import domeneovertakelse as dov
    from m37 import arbeider

    monkeypatch.setattr(
        dov, "vokt_ventende_overtakelseskonflikter",
        lambda *a, **k: (_ for _ in ()).throw(
            psycopg.errors.UndefinedFunction("ikke utrullet")))
    linjer = []
    monkeypatch.setattr("builtins.print",
                        lambda s, **k: linjer.append(s))
    with pytest.raises(psycopg.errors.UndefinedFunction):
        arbeider.drener_domenekonflikter(None)
    hendelser = [jsonmodul.loads(x)["hendelse"] for x in linjer]
    assert hendelser == ["domenekonflikt_vakt_svikt"], linjer

    # ... og uten_sak-funnet når journalen.
    linjer.clear()
    monkeypatch.setattr(
        dov, "vokt_ventende_overtakelseskonflikter",
        lambda *a, **k: {"funnet": 1, "med_sak": 0,
                         "uten_sak": [{"tenant": "t", "hostname": "h",
                                       "generasjon": 1}]})
    res = arbeider.drener_domenekonflikter(None)
    assert res["uten_sak"], res
    hendelser = [jsonmodul.loads(x)["hendelse"] for x in linjer]
    assert hendelser == ["domenekonflikt_uten_sak"], linjer


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

    verter = [f"kro{i}{secrets.token_hex(3)}.example.com" for i in range(2)]
    for v in verter:
        _utsted(migrator, v)
        _sett_kontekst(migrator, TENANT)
        # 041: se kommentaren i test_reutstedelse_avvises_nar_raden_avventer_m37.
        migrator.execute("ALTER TABLE domenekontroll DISABLE TRIGGER"
                         " domenekontroll_avklaring_krever_sak")
        migrator.execute(
            "UPDATE domenekontroll SET status='avklaring_kreves',"
            " konflikt_motpart=%s WHERE tenant=%s AND hostname=%s",
            (ANNEN_TENANT, TENANT, v))
        migrator.execute("ALTER TABLE domenekontroll ENABLE TRIGGER"
                         " domenekontroll_avklaring_krever_sak")
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
            return _Svar([("t", "en.example.com")])
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
                              if h == "gammel.example.com" else frozenset()))
    assert dr.utfordringssvar([], "gammel.example.com",
                              ogsa_vertsnavnet=True) == frozenset({"arv"})
    assert dr.utfordringssvar([], "gammel.example.com",
                              ogsa_vertsnavnet=False) == frozenset()

    # 4) Ett navn nede river ikke et bevis vi FANT på det andre — men er
    #    begge uten svar, er svaret uenighet, ikke «ingen post».
    monkeypatch.setattr(
        dr, "enig_svar",
        lambda resolvere, h: (None if h.startswith(dr.UTFORDRINGSPREFIKS)
                              else frozenset({"arv"})))
    assert dr.utfordringssvar([], "gammel.example.com",
                              ogsa_vertsnavnet=True) == frozenset({"arv"})
    monkeypatch.setattr(dr, "enig_svar", lambda resolvere, h: None)
    assert dr.utfordringssvar([], "gammel.example.com",
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

    treg = "treg.example.com"
    friske = [f"frisk{i}.example.com" for i in range(4)]
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

    rader = [("t", f"h{i}.example.com") for i in range(6)]
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

    rader = [("t", f"h{i}.example.com") for i in range(5)]

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

    assert _med(NXDOMAIN())("x.example.com") == frozenset(), \
        "NXDOMAIN ble ikke båret som et tomt svar"
    assert _med(NoAnswer())("x.example.com") == frozenset(), \
        "NoAnswer ble ikke båret som et tomt svar"
    with pytest.raises(NoNameservers):
        _med(NoNameservers())("x.example.com")


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
    "bekreft_overtakelseskonflikt(text,text,text,bigint)",
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
        "hovedløkken vokter ikke domenekonfliktene"
    assert "vokt_ventende_overtakelseskonflikter" in inspect.getsource(
        arbeider.drener_domenekonflikter)
