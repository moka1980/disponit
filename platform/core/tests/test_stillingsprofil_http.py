"""#189: stillingsprofilen — kundens kravliste over HTTP.

Editorflatens kontrakt: POST oppretter profil (versjon 1) eller NY
versjon av eksisterende; GET viser siste versjon av hver profil med
kravene i lagret rekkefølge. Basen er append-only (061): redigering er
aldri en mutasjon, og en gammel versjon står urørt for evalueringer som
peker på den.
"""
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401
from .test_rekruttering_http import _browsersesjon, _bruker

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _post(klient, cookie, csrf, kropp, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post("/v1/rekruttering/stillingsprofiler", json=kropp,
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or secrets.token_hex(12)})


def _hent(klient, cookie):
    from api import sesjon as sesjonmodul
    return klient.get("/v1/rekruttering/stillingsprofiler",
                      cookies={sesjonmodul.C_SESJON: cookie})


@pg
def test_profil_opprettes_versjoneres_og_leses(klient, miljo):
    """Eierkravet ordrett: «Drift 3, Norsk 1, Skytjenester 2» — opprett,
    les tilbake, rediger (ny versjon med nytt krav), og se at siste
    versjon vises med kravene i rekkefølge — mens versjon 1 står urørt i
    basen (append-only)."""
    cookie, csrf = _browsersesjon(_bruker(f"pf-{secrets.token_hex(3)}",
                                          ["admin"]))
    krav = [{"kravnavn": "Drift", "vekt": 3},
            {"kravnavn": "Norsk", "vekt": 1},
            {"kravnavn": "Skytjenester", "vekt": 2}]
    r = _post(klient, cookie, csrf,
              {"navn": "Driftskonsulent", "krav": krav})
    assert r.status_code == 201, r.text
    pid, versjon = r.json()["profil_id"], r.json()["versjon"]
    assert versjon == 1

    r2 = _hent(klient, cookie)
    assert r2.status_code == 200, r2.text
    mine = [p for p in r2.json()["profiler"] if p["profil_id"] == pid]
    assert len(mine) == 1
    assert mine[0]["navn"] == "Driftskonsulent"
    assert mine[0]["krav"] == krav

    # Redigering = ny versjon: nytt krav inn, en vekt endret.
    krav2 = [{"kravnavn": "Drift", "vekt": 4},
             {"kravnavn": "Norsk", "vekt": 1},
             {"kravnavn": "Skytjenester", "vekt": 2},
             {"kravnavn": "Sikkerhet", "vekt": 5}]
    r3 = _post(klient, cookie, csrf,
               {"profil_id": pid, "navn": "Driftskonsulent",
                "krav": krav2})
    assert r3.status_code == 201, r3.text
    assert r3.json()["versjon"] == 2

    r4 = _hent(klient, cookie)
    mine = [p for p in r4.json()["profiler"] if p["profil_id"] == pid]
    assert mine[0]["versjon"] == 2 and mine[0]["krav"] == krav2

    # …og versjon 1 står nøyaktig som den var i basen.
    from db.pg import koble, sett_kontekst
    from .test_rekruttering_http import TEN  # noqa: E501
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, TEN, "test", "r-v1")
        v1 = m.execute(
            "SELECT kravnavn, vekt FROM stillingsprofil_krav"
            " WHERE tenant=%s AND profil_id=%s AND versjon=1"
            " ORDER BY rekkefolge", (TEN, pid)).fetchall()
        m.rollback()
    finally:
        m.close()
    assert v1 == [("Drift", 3), ("Norsk", 1), ("Skytjenester", 2)]


@pg
def test_ugyldige_kravsett_avvises_uten_spor(klient, miljo):
    """Feilkontrakten: vekt utenfor 0–10, duplikatkrav, tomt sett,
    ukjent profil-id og misformet id gir 400 — og ingenting skrives."""
    cookie, csrf = _browsersesjon(_bruker(f"pg-{secrets.token_hex(3)}",
                                          ["admin"]))
    for kropp in (
        {"navn": "X", "krav": [{"kravnavn": "A", "vekt": 11}]},
        {"navn": "X", "krav": [{"kravnavn": "A", "vekt": -1}]},
        {"navn": "X", "krav": [{"kravnavn": "A", "vekt": 2},
                               {"kravnavn": "A", "vekt": 3}]},
        {"navn": "X", "krav": []},
        {"navn": "", "krav": [{"kravnavn": "A", "vekt": 2}]},
        {"navn": "X", "krav": [{"kravnavn": "", "vekt": 2}]},
        {"navn": "X", "krav": "ikke-liste"},
        # MANGLENDE nøkkel, ikke bare feil type (CodeRabbit): med `<>`
        # var jsonb_typeof(NULL) <> 'string' NULL og vakta i 061 hoppet
        # over — dommen ble en CheckViolation i stedet for
        # invalid_parameter_value. IS DISTINCT FROM feller begge.
        {"navn": "X", "krav": [{"vekt": 2}]},
        {"navn": "X", "krav": [{"kravnavn": "A"}]},
        {"navn": "X", "krav": [{"kravnavn": "A", "vekt": 2}],
         "profil_id": "ikke-uuid"},
        {"navn": "X", "krav": [{"kravnavn": "A", "vekt": 2}],
         "profil_id": "00000000-0000-4000-8000-000000000000"},
    ):
        r = _post(klient, cookie, csrf, kropp)
        assert r.status_code == 400, (kropp, r.text)
    # «uten spor» BEVISES (Cursor P2-6): ingen av avvisningene skrev.
    from db.pg import koble, sett_kontekst
    from .test_rekruttering_http import TEN
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, TEN, "test", "r-spor")
        n = m.execute("SELECT count(*) FROM stillingsprofil"
                      " WHERE tenant=%s AND navn IN ('X','')",
                      (TEN,)).fetchone()[0]
        m.rollback()
    finally:
        m.close()
    assert n == 0


@pg
def test_versjonene_er_append_only_i_basen(klient, miljo):
    """061-vaktene: UPDATE og DELETE avvises for begge tabellene — også
    for migrator. Redigering finnes bare som ny versjon."""
    import psycopg

    cookie, csrf = _browsersesjon(_bruker(f"pa-{secrets.token_hex(3)}",
                                          ["admin"]))
    r = _post(klient, cookie, csrf,
              {"navn": "Låst", "krav": [{"kravnavn": "K", "vekt": 2}]})
    assert r.status_code == 201, r.text
    pid = r.json()["profil_id"]
    from db.pg import koble, sett_kontekst
    from .test_rekruttering_http import TEN  # noqa: E501
    m = koble(MIGRATOR_DSN)
    try:
        for sql in (
            "UPDATE stillingsprofil SET navn='endret'"
            " WHERE tenant=%s AND profil_id=%s",
            "DELETE FROM stillingsprofil_krav"
            " WHERE tenant=%s AND profil_id=%s",
            "UPDATE stillingsprofil_krav SET vekt=9"
            " WHERE tenant=%s AND profil_id=%s",
        ):
            sett_kontekst(m, TEN, "test", "r-ao")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                m.execute(sql, (TEN, pid))
            m.rollback()
    finally:
        m.close()


@pg
def test_gjenspill_gir_samme_profil(klient, miljo):
    """CodeRabbit major: et tapt 201 + retry med SAMME nøkkel er samme
    operasjon — samme profil_id/versjon tilbake, ingen ny rad."""
    cookie, csrf = _browsersesjon(_bruker(f"pi-{secrets.token_hex(3)}",
                                          ["admin"]))
    idem = secrets.token_hex(12)
    kropp = {"navn": "Idem", "krav": [{"kravnavn": "K", "vekt": 2}]}
    r1 = _post(klient, cookie, csrf, kropp, idem=idem)
    r2 = _post(klient, cookie, csrf, kropp, idem=idem)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json() == r2.json()
    # …og uten nøkkel: 400.
    from api import sesjon as sesjonmodul
    r3 = klient.post("/v1/rekruttering/stillingsprofiler", json=kropp,
                     cookies={sesjonmodul.C_SESJON: cookie},
                     headers={"X-Disponit-CSRF": csrf})
    assert r3.status_code == 400


@pg
def test_samtidige_redigeringer_faar_hver_sin_versjon(klient, miljo):
    """CodeRabbit major: versjonstildelingen serialiseres med
    advisory-lås — to samtidige lagringer av samme profil skal gi
    versjon 2 OG 3, aldri kollisjon eller tapt max."""
    import threading

    cookie, csrf = _browsersesjon(_bruker(f"ps-{secrets.token_hex(3)}",
                                          ["admin"]))
    r = _post(klient, cookie, csrf,
              {"navn": "Sam", "krav": [{"kravnavn": "K", "vekt": 2}]})
    assert r.status_code == 201, r.text
    pid = r.json()["profil_id"]
    svar = []

    def lagre():
        svar.append(_post(klient, cookie, csrf,
                          {"profil_id": pid, "navn": "Sam",
                           "krav": [{"kravnavn": "K", "vekt": 5}]}))

    t1, t2 = threading.Thread(target=lagre), threading.Thread(target=lagre)
    t1.start(); t2.start(); t1.join(10); t2.join(10)
    koder = sorted(x.status_code for x in svar)
    assert koder == [201, 201], [x.text for x in svar]
    versjoner = sorted(x.json()["versjon"] for x in svar)
    assert versjoner == [2, 3], versjoner


@pg
def test_desimalvekt_avvises(klient, miljo):
    """CodeRabbit major: 2.7 er ikke et valg kunden tok — avvis, aldri
    avrund."""
    cookie, csrf = _browsersesjon(_bruker(f"pd-{secrets.token_hex(3)}",
                                          ["admin"]))
    r = _post(klient, cookie, csrf,
              {"navn": "D", "krav": [{"kravnavn": "K", "vekt": 2.7}]})
    assert r.status_code == 400, r.text


@pg
def test_idempotenskonflikt_annet_innhold(klient, miljo):
    """Cursor P1-2: samme nøkkel med ANNET innhold er en kollisjon —
    409 idempotenskonflikt, aldri stille replay av noe annet enn det
    kalleren ba om — og ingen ny rad."""
    cookie, csrf = _browsersesjon(_bruker(f"pk-{secrets.token_hex(3)}",
                                          ["admin"]))
    idem = secrets.token_hex(12)
    r1 = _post(klient, cookie, csrf,
               {"navn": "A", "krav": [{"kravnavn": "K", "vekt": 2}]},
               idem=idem)
    assert r1.status_code == 201, r1.text
    r2 = _post(klient, cookie, csrf,
               {"navn": "B", "krav": [{"kravnavn": "K", "vekt": 9}]},
               idem=idem)
    assert r2.status_code == 409, r2.text
    assert r2.json()["feil"] == "idempotenskonflikt"


@pg
def test_samtidig_idempotens_gir_replay_ikke_400(klient, miljo):
    """Cursor P1-3: to samtidige POST med samme nøkkel og samme kropp —
    taperen får vinnerens svar (201, samme JSON), aldri 400; nøyaktig
    én profil skrives."""
    import threading

    cookie, csrf = _browsersesjon(_bruker(f"pr-{secrets.token_hex(3)}",
                                          ["admin"]))
    idem = secrets.token_hex(12)
    kropp = {"navn": "Kappløp", "krav": [{"kravnavn": "K", "vekt": 2}]}
    svar = []

    def lagre():
        svar.append(_post(klient, cookie, csrf, kropp, idem=idem))

    t1, t2 = threading.Thread(target=lagre), threading.Thread(target=lagre)
    t1.start(); t2.start(); t1.join(10); t2.join(10)
    assert [x.status_code for x in svar] == [201, 201],         [x.text for x in svar]
    assert svar[0].json() == svar[1].json()
    from db.pg import koble, sett_kontekst
    from .test_rekruttering_http import TEN
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, TEN, "test", "r-kapp")
        n = m.execute("SELECT count(*) FROM stillingsprofil"
                      " WHERE tenant=%s AND operasjonsnokkel=%s",
                      (TEN, idem)).fetchone()[0]
        m.rollback()
    finally:
        m.close()
    assert n == 1


@pg
def test_lagring_krever_bestillingsscope(klient, miljo):
    """Cursor P2-2 (runde 2): POST-ruten krever `bestilling:opprett` —
    `leser` (kun `decisions:read`) skal dømmes 403 FØR noe skrives, med
    gyldig CSRF og nøkkel, så porten som måles er scopet alene."""
    cookie, csrf = _browsersesjon(_bruker(f"ps-{secrets.token_hex(3)}",
                                          ["leser"]))
    r = _post(klient, cookie, csrf,
              {"navn": "Uten fullmakt",
               "krav": [{"kravnavn": "A", "vekt": 2}]})
    assert r.status_code == 403, r.text
    assert r.json()["feil"] == "scope_mangler"
    # …og lesing står åpen for samme bruker: porten er PER RUTE.
    assert _hent(klient, cookie).status_code == 200
    from db.pg import koble, sett_kontekst
    from .test_rekruttering_http import TEN
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, TEN, "test", "r-scope")
        n = m.execute("SELECT count(*) FROM stillingsprofil"
                      " WHERE tenant=%s AND navn=%s",
                      (TEN, "Uten fullmakt")).fetchone()[0]
        m.rollback()
    finally:
        m.close()
    assert n == 0


@pg
def test_post_krever_csrf_og_idempotensnokkel(klient, miljo):
    """Cursor P2-3 (runde 2): #189-ruten går via `_browserkontekst`
    (CSRF + nøkkel), men porten var ubevist for DENNE ruten. Uten hodet,
    med feil token, uten nøkkel og med blank nøkkel — og ingen rad."""
    import secrets as _s

    from api import sesjon as sesjonmodul
    cookie, csrf = _browsersesjon(_bruker(f"pc-{_s.token_hex(3)}",
                                          ["admin"]))
    kropp = {"navn": "CSRF-spor", "krav": [{"kravnavn": "A", "vekt": 2}]}
    sti = "/v1/rekruttering/stillingsprofiler"

    uten_csrf = klient.post(sti, json=kropp,
                            cookies={sesjonmodul.C_SESJON: cookie},
                            headers={"Idempotency-Key": _s.token_hex(12)})
    assert uten_csrf.status_code == 403, uten_csrf.text
    assert uten_csrf.json()["feil"] == "csrf_ugyldig"

    feil_csrf = klient.post(sti, json=kropp,
                            cookies={sesjonmodul.C_SESJON: cookie},
                            headers={"X-Disponit-CSRF": _s.token_hex(24),
                                     "Idempotency-Key": _s.token_hex(12)})
    assert feil_csrf.status_code == 403, feil_csrf.text
    assert feil_csrf.json()["feil"] == "csrf_ugyldig"

    uten_idem = klient.post(sti, json=kropp,
                            cookies={sesjonmodul.C_SESJON: cookie},
                            headers={"X-Disponit-CSRF": csrf})
    assert uten_idem.status_code == 400, uten_idem.text
    assert uten_idem.json()["feil"] == "idempotensnokkel_mangler"

    blank_idem = klient.post(sti, json=kropp,
                             cookies={sesjonmodul.C_SESJON: cookie},
                             headers={"X-Disponit-CSRF": csrf,
                                      "Idempotency-Key": "   "})
    assert blank_idem.status_code == 400, blank_idem.text
    assert blank_idem.json()["feil"] == "idempotensnokkel_mangler"

    from db.pg import koble, sett_kontekst
    from .test_rekruttering_http import TEN
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, TEN, "test", "r-csrf")
        n = m.execute("SELECT count(*) FROM stillingsprofil"
                      " WHERE tenant=%s AND navn=%s",
                      (TEN, "CSRF-spor")).fetchone()[0]
        m.rollback()
    finally:
        m.close()
    assert n == 0
