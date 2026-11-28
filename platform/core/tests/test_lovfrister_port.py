"""Porten for 237: lovpålagte frister — fra org.nr. til varslede plikter.

  1. Enhetsregisterets svar tolkes riktig (ren funksjon), og et oppslag
     som feiler, sier det (503), uten å lagre noe.
  2. Kunden lagrer profilen, og NETTOPP de pliktene katalogen sier gjelder
     for den profilen, registreres i M-21 med henne som eier — et AS med
     MVA og ansatte får MVA, a-melding, aksjonærregisteroppgave …; et ENK
     uten MVA får ikke MVA-terminer.
  3. Et nytt trykk legger ikke til noe to ganger. En frist eieren legger
     til i katalogen, kommer med ved neste trykk.
  4. Uten plattformfullmakt: 403 på katalogen.

RYDDER ETTER SEG: katalogen er plattformens egen og delt av hele basen.

MUTASJONER SOM DREPER DENNE: dropp `orgformer`-sjekken i `gjelder` (2),
registrer med tilfeldig plikt-id (3), fjern `plattform_krev_eier` fra
fristdøra (4).
"""
import datetime
import secrets

import pytest

from .test_api import DSN, MIGRATOR_DSN, app, dekker, klient, miljo  # noqa: F401
from .test_plattform_http import (_browsersesjon, _bruker,  # noqa: F401
                                  _rydd_plattformeiere, pg)

I_DAG = datetime.date(2026, 11, 27)


@pytest.fixture
def fast_dag(monkeypatch):
    from api import lovfrister
    monkeypatch.setattr(lovfrister, "i_dag", lambda: I_DAG)


@pytest.fixture
def ren_katalog():
    from db.pg import koble
    m = koble(MIGRATOR_DSN)
    try:
        maler = m.execute("SELECT * FROM plattform_pliktmal").fetchall()
        frister = m.execute("SELECT * FROM plattform_pliktfrist").fetchall()
        m.rollback()
        yield
        m.execute("DELETE FROM plattform_pliktfrist")
        m.execute("DELETE FROM plattform_pliktmal")
        for r in maler:
            m.execute("INSERT INTO plattform_pliktmal VALUES"
                      " (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", r)
        for r in frister:
            m.execute("INSERT INTO plattform_pliktfrist VALUES"
                      " (%s,%s,%s,%s,%s)", r)
        m.commit()
    finally:
        m.close()


def _kunde():
    """En admin i et eget, nytt firma, med en ekte browserøkt."""
    from api import sesjon as sesjonmodul
    from db.pg import koble, sett_kontekst
    tenant = "t-lovf-" + secrets.token_hex(3)
    m = koble(MIGRATOR_DSN)
    try:
        sett_kontekst(m, tenant, "sys", "lovfrister")
        bid = m.execute(
            "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
            " RETURNING bruker_id",
            ("https://idp.example", f"{tenant}-admin")).fetchone()[0]
        m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
                  " VALUES (%s,%s,ARRAY['admin'])", (tenant, bid))
        ver = m.execute("SELECT authz_version FROM brukermedlemskap"
                        " WHERE tenant=%s AND bruker_id=%s",
                        (tenant, bid)).fetchone()[0]
        cookie, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        m.execute(
            "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
            " authz_snapshot, csrf_hash, utloper) VALUES"
            " (%s,%s,%s,%s,%s, now() + interval '2 hours')",
            (sesjonmodul._hash(cookie), tenant, bid, ver,
             sesjonmodul._hash(csrf)))
        m.commit()
    finally:
        m.close()
    return tenant, {sesjonmodul.C_SESJON: cookie}, {
        "x-disponit-csrf": csrf, "Idempotency-Key": secrets.token_hex(16)}


def test_enhetsregisterets_svar_tolkes():
    from api.lovfrister import tolk_enhet
    f = tolk_enhet({"organisasjonsnummer": "923609016", "navn": "Akme AS",
                    "organisasjonsform": {"kode": "AS",
                                          "beskrivelse": "Aksjeselskap"},
                    "registrertIMvaregisteret": True,
                    "harRegistrertAntallAnsatte": False, "antallAnsatte": 0})
    assert (f["orgform"], f["mva"], f["ansatte"]) == ("AS", True, False)
    assert tolk_enhet({"organisasjonsform": {"kode": "ENK"}})["mva"] is False


@pg
@dekker("register_utilgjengelig")
def test_oppslaget_som_feiler_sier_det(klient, monkeypatch):
    from api import lovfrister
    _t, c, h = _kunde()

    def nede(_orgnr):
        raise lovfrister.OppslagFeil("utilgjengelig")
    monkeypatch.setattr(lovfrister, "slaa_opp", nede)
    r = klient.post("/v1/lovfrister/oppslag", cookies=c, headers=h,
                    json={"orgnummer": "923609016"})
    assert r.status_code == 503 and r.json()["feil"] == "register_utilgjengelig"
    r = klient.post("/v1/lovfrister/oppslag", cookies=c, headers=h,
                    json={"orgnummer": "123"})
    assert r.status_code == 400
    monkeypatch.setattr(lovfrister, "slaa_opp", lambda o: {
        "orgnummer": o, "orgform": "AS", "mva": True, "ansatte": True})
    r = klient.post("/v1/lovfrister/oppslag", cookies=c, headers=h,
                    json={"orgnummer": "923 609 016"})
    assert r.status_code == 200 and r.json()["forslag"]["orgform"] == "AS"


@pg
def test_profilen_gir_nettopp_de_riktige_pliktene(klient, fast_dag,
                                                   ren_katalog):
    tenant, c, h = _kunde()
    assert klient.get("/v1/lovfrister", cookies=c).json()["profil"] is None

    r = klient.post("/v1/lovfrister", cookies=c, headers=h, json={
        "orgnummer": "923609016", "orgform": "AS", "mva": True,
        "ansatte": True, "kilde": "brreg"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["nye"] > 0
    nokler = {f["nokkel"] for f in d["frister"]}
    assert {"mva_termin", "a_melding", "aga_forskuddstrekk",
            "aksjonaerregisteroppgave", "skattemelding_selskap",
            "arsregnskap", "forskuddsskatt_selskap"} <= nokler
    assert not nokler & {"forskuddsskatt_enk", "skattemelding_enk"}
    assert all(f["registrert"] and f["status"] == "apen"
               for f in d["frister"])
    # Fristene står i M-21 — der varslene og kvitteringen bor.
    plikter = klient.get("/v1/plikt", cookies=c).json()["plikter"]
    assert len(plikter) == d["nye"]
    assert any("MVA-melding" in p["tittel"] and "1. termin 2027" in p["tittel"]
               for p in plikter)
    mva = next(p for p in plikter if "1. termin 2027" in p["tittel"])
    assert mva["frist"].startswith("2027-04-12")
    assert "§ 8-3-10" in mva["kilde"]

    # 3. Et nytt trykk legger ikke til noe.
    r = klient.post("/v1/lovfrister", cookies=c, headers=h, json={
        "orgnummer": "923609016", "orgform": "AS", "mva": True,
        "ansatte": True, "kilde": "brreg"})
    assert r.json()["nye"] == 0
    assert len(klient.get("/v1/plikt", cookies=c).json()["plikter"]) == len(
        plikter)

    # … men en ny frist i katalogen kommer med.
    ec, eh = _eier()
    r = klient.post("/v1/plattform/pliktkatalog/frist", cookies=ec,
                    headers=eh, json={"nokkel": "mva_termin",
                                      "periode": "6. termin 2027",
                                      "frist": "2027-12-20"})
    assert r.status_code == 200, r.text
    assert klient.get("/v1/lovfrister", cookies=c).json()[
        "nye_i_katalogen"] == 1
    r = klient.post("/v1/lovfrister", cookies=c, headers=h, json={
        "orgnummer": "923609016", "orgform": "AS", "mva": True,
        "ansatte": True, "kilde": "brreg"})
    assert r.json()["nye"] == 1


@pg
def test_et_enk_uten_mva_og_ansatte(klient, fast_dag, ren_katalog):
    _t, c, h = _kunde()
    r = klient.post("/v1/lovfrister", cookies=c, headers=h, json={
        "orgform": "ENK", "mva": False, "ansatte": False})
    assert r.status_code == 200, r.text
    nokler = {f["nokkel"] for f in r.json()["frister"]}
    assert nokler == {"forskuddsskatt_enk", "skattemelding_enk"}, nokler
    for kropp in ({"orgform": "x", "mva": False, "ansatte": False},
                  {"orgform": "ENK", "mva": "ja", "ansatte": False},
                  {"orgform": "ENK", "mva": False, "ansatte": False,
                   "orgnummer": "12"}):
        assert klient.post("/v1/lovfrister", cookies=c, headers=h,
                           json=kropp).status_code == 400, kropp


def _eier():
    from api import sesjon as sesjonmodul
    bid = _bruker("lovf-eier", plattformeier=True)
    cookie, csrf = _browsersesjon(bid)
    return {sesjonmodul.C_SESJON: cookie}, {"x-disponit-csrf": csrf}


@pg
def test_katalogen_er_plattformeierens(klient, ren_katalog):
    from api import sesjon as sesjonmodul
    ec, eh = _eier()
    r = klient.post("/v1/plattform/pliktkatalog/mal", cookies=ec, headers=eh,
                    json={"nokkel": "mva_aarlig", "tittel": "MVA årstermin",
                          "myndighet": "skatteetaten",
                          "hjemmel": "Skatteforvaltningsforskriften",
                          "kilde_url": "https://www.skatteetaten.no/",
                          "orgformer": [], "krever_mva": True,
                          "krever_ansatte": False, "aktiv": False})
    assert r.status_code == 200, r.text
    mal = next(m for m in r.json()["maler"] if m["nokkel"] == "mva_aarlig")
    assert mal["aktiv"] is False and mal["frister"] == []
    for kropp in ({"nokkel": "X", "tittel": "t"},
                  {**{"nokkel": "abc_d", "tittel": "t", "hjemmel": "h",
                      "myndighet": "skatteetaten"},
                   "kilde_url": "http://usikker.example"}):
        assert klient.post("/v1/plattform/pliktkatalog/mal", cookies=ec,
                           headers=eh, json=kropp).status_code == 400
    r = klient.post("/v1/plattform/pliktkatalog/frist/slett", cookies=ec,
                    headers=eh, json={"nokkel": "arsregnskap",
                                      "frist": "2027-07-31"})
    assert r.status_code == 200
    ars = next(m for m in r.json()["maler"] if m["nokkel"] == "arsregnskap")
    assert ars["frister"] == []

    bid = _bruker("lovf-uten", plattformeier=False)
    cookie, csrf = _browsersesjon(bid)
    for sti in ("/v1/plattform/pliktkatalog/frist",
                "/v1/plattform/pliktkatalog/frist/slett"):
        r = klient.post(sti, cookies={sesjonmodul.C_SESJON: cookie},
                        headers={"x-disponit-csrf": csrf},
                        json={"nokkel": "mva_termin", "periode": "x",
                              "frist": "2027-01-01"})
        assert r.status_code == 403, (sti, r.text)


@pg
def test_plikt_id_er_basens(migrator_lf):
    """Python viser fristene med samme id som basen registrerer dem med."""
    import datetime as dt

    from api.lovfrister import plikt_id
    for tenant, nokkel, frist in (("akme", "mva_termin", dt.date(2027, 4, 12)),
                                  ("t-æøå", "a_melding", dt.date(2026, 12, 7))):
        basens = migrator_lf.execute(
            "SELECT lovfrister_plikt_id(%s,%s,%s)",
            (tenant, nokkel, frist)).fetchone()[0]
        assert plikt_id(tenant, nokkel, frist) == basens
    migrator_lf.rollback()


@pytest.fixture
def migrator_lf():
    from db.pg import koble
    m = koble(DSN)
    yield m
    m.close()


@pg
def test_fristvakten_legger_inn_nye_frister_av_seg_selv(klient, ren_katalog,
                                                         migrator_lf):
    """DRIFTSVEIEN, ikke døra: `evidensreaper.kjor` — samme funksjon
    timeren kaller — legger katalogens nye frist inn hos et firma som
    ALDRI trykker på noe. Uten dette er fristvakten en liste."""
    from drift import evidensreaper

    from .test_outbox_bestilling import _reaperkobling
    tenant, c, h = _kunde()
    r = klient.post("/v1/lovfrister", cookies=c, headers=h, json={
        "orgform": "AS", "mva": True, "ansatte": False})
    assert r.status_code == 200, r.text
    foer = len(klient.get("/v1/plikt", cookies=c).json()["plikter"])
    assert foer > 0

    # Eieren legger en ny frist i katalogen …
    ec, eh = _eier()
    frist = (datetime.date.today() + datetime.timedelta(days=200)).isoformat()
    assert klient.post("/v1/plattform/pliktkatalog/frist", cookies=ec,
                       headers=eh, json={"nokkel": "mva_termin",
                                         "periode": "Ekstra termin",
                                         "frist": frist}).status_code == 200
    # … og timeren gjør resten.
    rp, _rolle = _reaperkobling()
    try:
        res = evidensreaper.kjor(rp)
        assert not res.lovfrister_feilet, (
            "timerrollen har EXECUTE — en nekt her er et rettighetshull")
        assert (tenant, 1) in res.lovfrister, res.lovfrister
        # Samme kjøring igjen: ingenting nytt, og firmaet er ikke for tur.
        assert tenant not in {t for t, _n in evidensreaper.kjor(rp).lovfrister}
    finally:
        rp.close()
    plikter = klient.get("/v1/plikt", cookies=c).json()["plikter"]
    assert len(plikter) == foer + 1
    assert any("Ekstra termin" in p["tittel"] for p in plikter)
