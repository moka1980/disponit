"""#162 PR-3: evalueringsbestillingen — referanser inn, binding i
oppdragets fødselstransaksjon.

Hele kjeden over HTTP: kunde laster opp bunt (PR-1-veien), har en
stillingsprofil (#189/061), og POST /v1/bestilling med
`rekruttering.evaluering` + de to referansene gir TILLAT → oppdrag —
med bunten BUNDET i samme transaksjon (X1) og payloaden i B-form
(#200): profil-øyeblikksbilde bygget server-side, ingen
soknadsbunt_ref.
"""
import json
import secrets
import threading
import uuid

import psycopg
import pytest
import yaml as _yaml

from .test_api import (DSN, MIGRATOR_DSN, POLICIES, TENANT, app,  # noqa: F401
                       klient, migrator, miljo)
from .test_inndata_http import inndata_rot  # noqa: F401
from .test_m37 import _sett_kontekst
from .test_api import dekker
from .test_outbox_bestilling import _adminsesjon, _kjernenokkel

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")


def _rekr_policy(migrator_, *, ved_brudd="unntakskø",
                 tillatt_for=("bestiller",)):
    """Aktiv policy med rekrutteringshandlingen — bransjemalen +
    `rekruttering.evaluering` (modus auto, persondata tillatt: det er
    CV-er, og klassen skal være et EKSPLISITT policyvalg).

    `ved_brudd`/`tillatt_for` er parametre av samme grunn som i
    `_wcag_policy`: STOPP-veien er en egen port og må kunne måles uten en
    andre kopi av riggen."""
    from api import policyregister
    p = _yaml.safe_load(
        (POLICIES / "bransjemal-tjenestebedrift.yaml")
        .read_text(encoding="utf-8"))
    p["roller"].append({"id": "bestiller",
                        "beskrivelse": "Bestiller evalueringer"})
    p["handlinger"].append({
        "id": "rekruttering.evaluering", "modul": "M-57",
        "modus": "auto", "ved_brudd": ved_brudd,
        "tillatt_for": list(tillatt_for),
        "dataklasser_tillatt": ["persondata"],
        "reversering": {"type": "direkte"}})
    policyregister.registrer(migrator_, TENANT, p, p["meta"]["status"])
    migrator_.commit()
    _sikre_m57_claimbar(migrator_)


def _sikre_m57_claimbar(m):
    """Claim-vaktens fire vilkår for `rekruttering.evaluering`:
    registerrad m/ rett eier (finnes fra migrasjonene), aktivt
    modulhode, og en claiming-deployment i DETTE miljøet — idempotent
    (samme mønster som resolver-testenes m57-rigg)."""
    from miljo import gjeldende_miljo
    mv = gjeldende_miljo()
    m.execute("INSERT INTO modulhode (modul_id,status)"
              " VALUES ('m57_ats','aktiv') ON CONFLICT DO NOTHING")
    m.execute(
        "INSERT INTO modulkontrakt (modul_id,kontraktversjon,"
        "kontrakt_hash,payload_schema_hash,kvittering_schema_hash,"
        "sideeffektklasse,reversibilitet)"
        " VALUES ('m57_ats',1,%s,'p','k','krever_outbox','kompenserende')"
        " ON CONFLICT DO NOTHING", ("k-" + secrets.token_hex(8),))
    khash = m.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m57_ats' AND kontraktversjon=1").fetchone()[0]
    reg = m.execute(
        "SELECT eiermodul FROM oppdragstype_register"
        " WHERE oppdragstype='rekruttering.evaluering'").fetchone()
    if reg is None:
        m.execute(
            "INSERT INTO oppdragstype_register (oppdragstype,eiermodul,"
            "kontraktversjon,kontrakt_hash)"
            " VALUES ('rekruttering.evaluering','m57_ats',1,%s)", (khash,))
    rad = m.execute(
        "SELECT release_id FROM moduldeployment"
        " WHERE modul_id='m57_ats' AND miljo=%s AND livslop='claiming'"
        " LIMIT 1", (mv,)).fetchone()
    if rad is None:
        rel = f"r57b-{secrets.token_hex(6)}"
        m.execute(
            "INSERT INTO modulrelease (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,manifest_hash,artifact_digest)"
            " VALUES ('m57_ats',%s,1,%s,'mh','ad')", (rel, khash))
        m.execute(
            "INSERT INTO moduldeployment (modul_id,release_id,"
            "kontraktversjon,kontrakt_hash,miljo,livslop)"
            " VALUES ('m57_ats',%s,1,%s,%s,'claiming')", (rel, khash, mv))
    m.commit()


def _profil(m):
    """En profilversjon via 061-døren (dørens eier)."""
    _sett_kontekst(m, TENANT)
    m.execute("SET LOCAL ROLE disponit_domene_eier")
    rad = m.execute(
        "SELECT ut_profil_id, ut_versjon FROM"
        " opprett_stillingsprofil_versjon(%s,NULL,%s,'test',"
        "%s::jsonb,%s)",
        (TENANT, "Driftskonsulent",
         json.dumps([{"kravnavn": "Drift", "vekt": 3},
                     {"kravnavn": "Norsk", "vekt": 1}]),
         secrets.token_hex(12))).fetchone()
    m.execute("RESET ROLE")
    m.commit()
    return f"{rad[0]}@{rad[1]}"


def _bunt(klient, m, cookie, csrf):
    """Reservert+lastet bunt over HTTP med bestiller-økten."""
    from api import sesjon as sesjonmodul
    import hashlib
    import io
    import zipfile
    r = klient.post("/v1/inndata/reserver",
                    json={"eiermodul": "m57_ats",
                          "formaal": "soknadsbunt"},
                    cookies={sesjonmodul.C_SESJON: cookie},
                    headers={"X-Disponit-CSRF": csrf,
                             "Idempotency-Key": secrets.token_hex(12)})
    assert r.status_code == 201, r.text
    jti = r.json()["reservasjon_jti"]
    ref = r.json()["inndata_ref"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("cv1.pdf", b"x" * 64)
    kropp = buf.getvalue()
    r2 = klient.put(f"/v1/inndata/opplast/{jti}", content=kropp,
                    cookies={sesjonmodul.C_SESJON: cookie},
                    headers={"X-Disponit-CSRF": csrf,
                             "content-type": "application/zip"})
    assert r2.status_code == 201, r2.text
    return ref


def _evalkropp(ref, profilref, antall=1):
    return {"bestillingstype": "rekruttering.evaluering",
            "inndata_ref": ref, "stillingsprofil_ref": profilref,
            "antall_soknader": antall, "omfang": "bunt"}


def _beslutninger(m):
    """Hvor mange bestillingsbeslutninger som er COMMITTET for tenanten.

    Én rad = én evaluering = én frekvensplass brent. Prefikset er
    `kjernenokkelprefiks`-formen, så opplastings-/reservasjonsveiens egne
    loggposter ikke telles med."""
    _sett_kontekst(m, TENANT)
    n = m.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " idempotency_key LIKE 'bestilling:%%'", (TENANT,)).fetchone()[0]
    m.rollback()
    return n


def _buntrad(m, ref):
    _sett_kontekst(m, TENANT)
    rad = m.execute(
        "SELECT status, oppdrag_id FROM inndata_artefakt"
        " WHERE tenant=%s AND inndata_id=%s",
        (TENANT, ref.split(":", 1)[1])).fetchone()
    m.rollback()
    return rad


def _bestill(klient, cookie, csrf, kropp, idem=None):
    from api import sesjon as sesjonmodul
    return klient.post("/v1/bestilling", json=kropp,
                       cookies={sesjonmodul.C_SESJON: cookie},
                       headers={"X-Disponit-CSRF": csrf,
                                "Idempotency-Key":
                                    idem or "n-" + secrets.token_hex(8)})


@pg
def test_evalueringsbestillingen_binder_bunten_i_fodselstransaksjonen(
        klient, migrator, miljo, inndata_rot):
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)

    r = _bestill(klient, cookie, csrf,
                 {"bestillingstype": "rekruttering.evaluering",
                  "inndata_ref": ref, "stillingsprofil_ref": profilref,
                  "antall_soknader": 1, "omfang": "bunt"})
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] == "tillat"
    oid = r.json()["oppdrag_id"]
    assert isinstance(oid, int)

    # Bindingen skjedde i SAMME transaksjon som fødselen (X1): raden er
    # bundet til nøyaktig dette oppdraget.
    _sett_kontekst(migrator, TENANT)
    rad = migrator.execute(
        "SELECT status, oppdrag_id FROM inndata_artefakt"
        " WHERE tenant=%s AND inndata_id=%s",
        (TENANT, ref.split(":", 1)[1])).fetchone()
    migrator.rollback()
    assert rad == ("bundet", oid)

    # Payloaden er B-formen (#200): øyeblikksbilde, ingen
    # soknadsbunt_ref.
    from db import kryptering
    _sett_kontekst(migrator, TENANT)
    prad = migrator.execute(
        "SELECT payload_kryptert, key_id, nonce FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    nok = migrator.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s AND"
        " key_id=%s", (TENANT, prad[1])).fetchone()[0]
    migrator.rollback()
    dek = kryptering._pakk_ut((prad[1], nok), TENANT)[1]
    payload = kryptering.dekrypter(dek, bytes(prad[0]), bytes(prad[2]),
                                   TENANT, prad[1])
    assert "soknadsbunt_ref" not in payload
    assert payload["stillingsprofil_ref"] == profilref
    snap = payload["stillingsprofil"]
    assert snap["krav"] == [{"kravnavn": "Drift", "vekt": 3},
                            {"kravnavn": "Norsk", "vekt": 1}]
    assert payload["antall_soknader"] == 1


@pg
@dekker("stillingsprofil_ukjent")
@dekker("inndata_ubrukelig")
def test_referanser_som_ikke_kan_brukes_avvises_for_beslutningen(
        klient, migrator, miljo, inndata_rot):
    """Forhåndsportene: ukjent profil 404, ubrukelig bunt 409 — begge
    FØR beslutningen (ingen kvote brent), og formfeil 400."""
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)

    r = _bestill(klient, cookie, csrf,
                 {"bestillingstype": "rekruttering.evaluering",
                  "inndata_ref": ref,
                  "stillingsprofil_ref": f"{uuid.uuid4()}@1",
                  "antall_soknader": 1, "omfang": "bunt"})
    assert r.status_code == 404, r.text
    assert r.json()["feil"] == "stillingsprofil_ukjent"

    r2 = _bestill(klient, cookie, csrf,
                  {"bestillingstype": "rekruttering.evaluering",
                   "inndata_ref": f"inndata:{uuid.uuid4()}",
                   "stillingsprofil_ref": profilref,
                   "antall_soknader": 1, "omfang": "bunt"})
    assert r2.status_code == 409, r2.text
    assert r2.json()["feil"] == "inndata_ubrukelig"

    for kropp in (
        {"bestillingstype": "rekruttering.evaluering",
         "inndata_ref": "inndata:ikke-uuid",
         "stillingsprofil_ref": profilref,
         "antall_soknader": 1, "omfang": "bunt"},
        {"bestillingstype": "rekruttering.evaluering",
         "inndata_ref": ref, "stillingsprofil_ref": profilref,
         "antall_soknader": 5001, "omfang": "bunt"},
        {"bestillingstype": "rekruttering.evaluering",
         "inndata_ref": ref, "stillingsprofil_ref": profilref,
         "antall_soknader": 1, "omfang": "alt"},
        {"bestillingstype": "rekruttering.evaluering",
         "inndata_ref": ref, "stillingsprofil_ref": profilref,
         "antall_soknader": 1, "omfang": "bunt", "hostname": "x.no"},
    ):
        rf = _bestill(klient, cookie, csrf, kropp)
        assert rf.status_code == 400, (kropp, rf.text)


def test_eksplisitt_standardfrist_er_samme_intensjon_som_fravaer():
    """Codex P2: utelatt `slettefrist_dogn` og eksplisitt 90 er samme
    kundevalg (057 `DEFAULT 90`) — de skal gi SAMME intensjonshash, ellers
    dømmes retry med utfylt standard `idempotenskonflikt` mot sin egen
    første bestilling. En IKKE-standard verdi skal fortsatt skille seg."""
    import uuid as _uuid

    from api.bestilling import intensjonshash, normaliser
    basis = {"bestillingstype": "rekruttering.evaluering",
             "inndata_ref": f"inndata:{_uuid.uuid4()}",
             "stillingsprofil_ref": f"{_uuid.uuid4()}@1",
             "antall_soknader": 3, "omfang": "bunt"}
    uten = normaliser("t-x", dict(basis))
    med90 = normaliser("t-x", {**basis, "slettefrist_dogn": 90})
    assert "slettefrist_dogn" not in med90,         "eksplisitt standard skal kanoniseres til fravær"
    assert intensjonshash(uten) == intensjonshash(med90)
    annen = normaliser("t-x", {**basis, "slettefrist_dogn": 30})
    assert annen["slettefrist_dogn"] == 30
    assert intensjonshash(annen) != intensjonshash(uten)


def test_uhashbar_bestillingstype_er_400_ikke_500():
    """CodeRabbit major: `BESTILLINGSTYPER.get(liste)` reiser TypeError
    (uhashbar) — kroppen er klientens feil og skal dømmes 400, aldri 500."""
    from api.bestilling import Bestillingsfeil, normaliser
    for kropp in ({"bestillingstype": ["kontroll.wcag.nettsted"]},
                  {"bestillingstype": {"a": 1}},
                  {"bestillingstype": 7}):
        with pytest.raises(Bestillingsfeil) as ei:
            normaliser("t-x", kropp)
        assert ei.value.kode == "request_feilformet"


@pg
def test_en_bunt_kan_bare_bestilles_en_gang(klient, migrator, miljo, inndata_rot):
    """Andre bestilling på samme bunt: forhåndsporten ser `bundet` og
    svarer 409 uten å brenne kvote — og uten et andre oppdrag."""
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)
    r = _bestill(klient, cookie, csrf,
                 {"bestillingstype": "rekruttering.evaluering",
                  "inndata_ref": ref, "stillingsprofil_ref": profilref,
                  "antall_soknader": 1, "omfang": "bunt"})
    assert r.status_code == 200, r.text
    r2 = _bestill(klient, cookie, csrf,
                  {"bestillingstype": "rekruttering.evaluering",
                   "inndata_ref": ref, "stillingsprofil_ref": profilref,
                   "antall_soknader": 2, "omfang": "bunt"})
    assert r2.status_code == 409, r2.text
    assert r2.json()["feil"] == "inndata_ubrukelig"


def test_opptatt_bunt_er_forbigaende_ikke_dom():
    """Cursor P1: taperen i kappløpet om bunten har ikke fått en DOM.

    `inndata_ubrukelig` er terminal — ukjent, utløpt, forkastet, alt
    bundet — og planveien gjør en terminal kode om til en pause bare et
    menneske kan oppheve. «En annen bestilling holder bunten akkurat nå»
    er derimot et sammenstøt i tid, nøyaktig samme klasse som en opptatt
    idempotensnøkkel. Utad er de fortsatt den samme 409-en.

    MUTASJONEN SOM DREPER DENNE: la låsegrenen returnere
    `inndata_ubrukelig` igjen, eller fjern koden fra `_FORBIGAENDE`.
    """
    from api.bestilling import INNDATA_OPPTATT, KLIENTKODE
    from plan.materialiser import _FORBIGAENDE, _tick_utfall, er_forbigaende
    assert INNDATA_OPPTATT in _FORBIGAENDE
    assert er_forbigaende(INNDATA_OPPTATT)
    assert _tick_utfall(("feil", INNDATA_OPPTATT))[0] is None
    assert KLIENTKODE[INNDATA_OPPTATT] == "inndata_ubrukelig"
    assert not er_forbigaende("inndata_ubrukelig")


def test_buntlaasen_og_nokkellaasen_deler_ikke_navnerom():
    """De to låsene låser to forskjellige ting og må aldri kollapse:
    separatoren er `\\x1f`, og andreleddet skiller navnerommene."""
    from api.bestilling import inndata_laasenavn_for, laasenavn_for
    assert inndata_laasenavn_for("t", "x") != laasenavn_for("t", "x")
    assert inndata_laasenavn_for("t", "x") == "t\x1finndata\x1fx"


@pg
def test_opptatt_bunt_avvises_for_beslutningen(klient, migrator, miljo,
                                               inndata_rot):
    """Cursor P1, deterministisk: holdes buntlåsen av en ANNEN bestilling,
    stopper forespørselen FØR kjernen — ingen beslutning, ingen kvote.

    Låsen holdes av en tredje forbindelse — nøyaktig formen en bestilling
    som fortsatt arbeider har — og `utfor_bestilling` er den EKTE
    funksjonen her: det er dens egen `pg_try_advisory_lock` som avgjør.
    Og når låsen slippes, går NØYAKTIG den samme forespørselen gjennom:
    låsen er en serialisering, ikke en permanent stenging.
    """
    from api import bestilling as bm
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)
    kropp = _evalkropp(ref, profilref)
    nokkel = "n-" + secrets.token_hex(8)

    laas = psycopg.connect(DSN)
    try:
        navn = bm.inndata_laasenavn_for(TENANT, ref.split(":", 1)[1])
        assert laas.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
            (navn,)).fetchone()[0] is True
        r = _bestill(klient, cookie, csrf, kropp, nokkel)
        assert (r.status_code, r.json()["feil"]) == (
            409, "inndata_ubrukelig"), r.text
        assert _beslutninger(migrator) == 0, \
            "en forespørsel uten buntlåsen tok likevel en beslutning"
        assert _buntrad(migrator, ref) == ("lastet", None)
    finally:
        laas.execute("SELECT pg_advisory_unlock_all()")
        laas.close()

    r2 = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert r2.status_code == 200, r2.text
    assert r2.json()["beslutning"] == "tillat"
    assert _buntrad(migrator, ref) == ("bundet", r2.json()["oppdrag_id"])


@pg
def test_to_samtidige_bestillinger_paa_bunten_brenner_en_kvote(
        klient, app, migrator, miljo, inndata_rot, monkeypatch):
    """Cursor P1, den BINDENDE testen: to tråder, samme lastede bunt,
    ULIKE idempotensnøkler.

    Nøkkellåsen serialiserer klientens nøkkel, og to ulike nøkler er to
    ulike låser — bunten var dermed uvoktet. Begge passerte
    forhåndsporten, begge committet en TILLAT-beslutning, og bare den ene
    vant `bind_inndata`: taperen rullet oppdraget tilbake og svarte
    `inndata_ubrukelig`, men beslutningen sto igjen committet. To
    kvoteplasser brent, én jobb.

    Kravet er tre tall på én gang: nøyaktig ett `tillat`, nøyaktig ETT
    beslutningsoppdrag, og nøyaktig ÉN revisjonsrad. Det siste alene er
    hele funnet — en implementasjon som avviste taperen ETTER beslutningen
    ville bestått de to første.

    `kjerne.behandle` synkroniseres med vilje: uten det kunne testen
    bestått på flaks fordi den ene tråden rakk hele veien gjennom før den
    andre leste forhåndsporten. Med barrieren er begge trådene garantert
    forbi porten før noen av dem bestemmer seg — og da er det bare låsen
    som kan holde dem fra hverandre.

    MUTASJONEN SOM DREPER DENNE: fjern buntlåsen → to TILLAT, to
    kvoteplasser.
    """
    from api import bestilling as bm
    from api import kjerne as kjernemodul
    from db.pg import koble
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)
    assert _beslutninger(migrator) == 0

    midt = threading.Barrier(2)
    ekte = kjernemodul.behandle

    def synkronisert(*a, **kw):
        # Med låsen på plass kommer BARE den ene hit; ventetiden løper ut
        # og barrieren brekker — som er nøyaktig svaret vi vil ha.
        try:
            midt.wait(3.0)
        except threading.BrokenBarrierError:
            pass
        return ekte(*a, **kw)

    monkeypatch.setattr(kjernemodul, "behandle", synkronisert)

    start = threading.Barrier(2)
    svar: list = []
    laas = threading.Lock()

    def kjor(i):
        c = koble(DSN)
        try:
            start.wait(30)
            res = bm.utfor_bestilling(
                app.tjeneste, c, TENANT, "bruker:samtidig",
                _evalkropp(ref, profilref, antall=i + 1),
                f"n-{i}-" + secrets.token_hex(6), f"rid-{i}")
        except Exception as e:                       # pragma: no cover
            res = ("unntak", repr(e))
        finally:
            c.close()
        with laas:
            svar.append(res)

    traader = [threading.Thread(target=kjor, args=(i,)) for i in range(2)]
    for t in traader:
        t.start()
    for t in traader:
        t.join(90)

    assert len(svar) == 2, svar
    ok = [s for s in svar if s[0] == "ok"]
    feil = [s for s in svar if s[0] == "feil"]
    assert len(ok) == 1 and len(feil) == 1, svar
    assert ok[0][1]["beslutning"] == "tillat", svar
    # Taperen møtte LÅSEN, ikke bindingen: den forbigående koden, ikke den
    # terminale.
    assert feil[0][1] == bm.INNDATA_OPPTATT, svar

    oid = ok[0][1]["oppdrag_id"]
    assert _buntrad(migrator, ref) == ("bundet", oid)
    assert _beslutninger(migrator) == 1, \
        "taperen brente en frekvensplass uten å få en jobb ut av den"
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
        " oppdragstype='rekruttering.evaluering'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert n == 1, "to oppdrag på én engangsbunt"


@pg
def test_gjenspill_av_evalueringsbestillingen_binder_ikke_paa_nytt(
        klient, migrator, miljo, inndata_rot):
    """Cursor P2 (b): en retry som mistet svaret får BESLUTNINGEN sin
    igjen — ikke en ny.

    Formen er outbox-suitens `test_gjenspill_...`, målt på den nye armen:
    `idempotent-replay: 1`, HELE svaret byte for byte, samme
    `oppdrag_id`, bunten fortsatt bundet til nøyaktig det oppdraget, og
    fortsatt bare ÉN committet beslutning. Gjenspillet leses ut av
    `bestilling_idempotens` FØR forhåndsporten, og det er nettopp derfor
    en bunt som nå står `bundet` ikke gjør retryen til en 409: porten er
    en OPPRETTELSES-regel, og et gjenspill oppretter ingenting.
    """
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)
    kropp = _evalkropp(ref, profilref)
    nokkel = "n-" + secrets.token_hex(8)

    r = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert r.status_code == 200, r.text
    oid = r.json()["oppdrag_id"]
    assert r.headers.get("idempotent-replay") is None

    r2 = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert r2.status_code == 200, r2.text
    assert r2.headers.get("idempotent-replay") == "1", dict(r2.headers)
    assert {k: v for k, v in r2.json().items() if k != "request_id"} == \
        {k: v for k, v in r.json().items() if k != "request_id"}, r2.text
    assert r2.json()["oppdrag_id"] == oid
    assert _buntrad(migrator, ref) == ("bundet", oid)
    assert _beslutninger(migrator) == 1, \
        "gjenspillet tok en ny beslutning og brente en kvoteplass til"


@pg
def test_stopp_binder_ikke_bunten(klient, migrator, miljo, inndata_rot):
    """Cursor P2 (c): en beslutning som IKKE er TILLAT rører aldri bunten.

    Bindingen bor i TILLAT-armen, og det er lett å tro at det holder å
    lese koden. Her måles det: policyen tillater ikke bestilleren og
    svarer `stopp_og_varsle` → STOPP med strukturert kode, intet oppdrag,
    og bunten står igjen `lastet` med `oppdrag_id IS NULL` — altså
    fortsatt fri til å bli bestilt av en lovlig bestilling. Ble den
    bundet her, var engangsbunten forbrukt av et avslag.
    """
    _rekr_policy(migrator, tillatt_for=("konsulent",),
                 ved_brudd="stopp_og_varsle")
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)

    r = _bestill(klient, cookie, csrf, _evalkropp(ref, profilref))
    assert r.status_code == 200, r.text
    svar = r.json()
    assert svar["beslutning"] == "stopp" and svar["oppdrag_id"] is None, svar
    assert svar["begrunnelse"], "STOPP uten strukturert kode"
    assert _buntrad(migrator, ref) == ("lastet", None), \
        "et avslag forbrukte engangsbunten"
    _sett_kontekst(migrator, TENANT)
    n = migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
        " oppdragstype='rekruttering.evaluering'", (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert n == 0


@pg
def test_krasj_mellom_beslutning_og_binding_fullfores_av_retryen(
        klient, migrator, miljo, inndata_rot, monkeypatch):
    """Cursor P2 (d): dør halen ETTER at kjernen har committet, men FØR
    oppdraget og bindingen, er bunten fortsatt fri — og retryen fullfører
    den beslutningen som alt er tatt.

    Krasjet simuleres med den ene veien som allerede ruller tilbake etter
    kjernens commit (outbox-suitens form): en oppdragstype uten deklarert
    frist gir 500 og ingen bokføring. Da står kjernens egen idempotensrad
    som det eneste sporet av forsøket, og gjenopprettingslesingen på
    klientnøkkelens prefiks er det som får bestillingen i mål.

    Det som måles er kvoteøkonomien: ÉN beslutning totalt, ikke to.
    Tok retryen en ny beslutning, ville et krasj i dette vinduet kostet
    kunden to frekvensplasser for én evaluering.
    """
    import oppdragskontrakt
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)
    kropp = _evalkropp(ref, profilref)
    nokkel = "n-" + secrets.token_hex(8)

    with monkeypatch.context() as mp:
        mp.setattr(oppdragskontrakt, "utforelsesfrist_s",
                   lambda *a, **k: None)
        r = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert (r.status_code, r.json()["feil"]) == (500, "intern_feil"), r.text
    assert _beslutninger(migrator) == 1, \
        "beslutningen skulle vært committet av kjernen"
    # Bunten er URØRT: bindingen skjer i oppdragets fødselstransaksjon, og
    # den transaksjonen ble rullet tilbake.
    assert _buntrad(migrator, ref) == ("lastet", None)
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM bestilling_idempotens WHERE tenant=%s AND"
        " idempotensnokkel=%s", (TENANT, nokkel)).fetchone()[0] == 0
    migrator.rollback()

    r2 = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert r2.status_code == 200, r2.text
    assert r2.json()["beslutning"] == "tillat", r2.text
    oid = r2.json()["oppdrag_id"]
    assert oid, r2.text
    assert _buntrad(migrator, ref) == ("bundet", oid)
    assert _beslutninger(migrator) == 1, \
        "retryen tok en NY beslutning i stedet for å gjenspille den gamle"


@pg
def test_committet_dom_gjenopprettes_selv_om_bunten_dode(
        klient, migrator, miljo, inndata_rot):
    """Codex P2 (runde 2): dør prosessen etter at kjernen committet en
    STOPP, men før `bestilling_idempotens` ble skrevet, står bunten
    ubundet — og dør DEN så (utløp/forkasting) før klienten retryer,
    dømte forhåndsporten `inndata_ubrukelig` FØR gjenopprettingen rakk å
    svare med dommen som alt er tatt. Buntens tilstand er muterbar;
    dommen er det ikke.

    Krasjvinduet plantes direkte (migrator er basens eier — kalleveien
    kan ikke plante, se `test_kaller_kan_ikke_plante_raden_…`): en ferdig
    kjernerad med STOPP under nøyaktig kjernenøkkelen, ingen
    `bestilling_idempotens`-rad, og en bunt som er forkastet.

    MUTASJONEN SOM DREPER DENNE: fjern `gjenopprettbar`-porten foran
    buntdommen i `bestilling.py`, så svarer retryen 409 i stedet for den
    committede dommen."""
    import json as _json

    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)
    kropp = _evalkropp(ref, profilref)
    nokkel = "n-" + secrets.token_hex(8)

    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE inndata_artefakt SET status='forkastet'"
                     " WHERE tenant=%s AND inndata_id=%s",
                     (TENANT, ref.split(":", 1)[1]))
    migrator.execute(
        "INSERT INTO idempotens (tenant, nokkel, input_hash, status,"
        " respons) VALUES (%s,%s,%s,'ferdig',%s)",
        (TENANT, _kjernenokkel(nokkel, kropp), "plantet-krasjvindu",
         _json.dumps({"http": 200, "beslutning": "stopp",
                      "begrunnelse": "plantet dom"})))
    migrator.commit()

    r = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert r.status_code == 200, r.text
    assert r.json()["beslutning"] == "stopp", r.text
    # …og gjenopprettingen tok ingen NY beslutning: ingen loggpost ble
    # noensinne skrevet (dommen var plantet, aldri tatt her), så en
    # gjenoppretting som i det stille hadde kjørt kjernen på nytt ville
    # synes som en revisjonsrad — og som en kvoteplass.
    assert _beslutninger(migrator) == 0, \
        "gjenopprettingen tok en NY beslutning i stedet for den plantede"

    # SAMME nøkkel, ANNEN intensjon (Codex P2, runde 3): løftet er
    # `idempotenskonflikt` — og det skal ikke avhenge av at retry-kroppens
    # referanser fortsatt er i live. Bunten er alt forkastet; med den
    # eksakte nøkkelformen i porten falt denne i 409 `inndata_ubrukelig`
    # før prefikslesingen rakk å se den committede dommen.
    kropp2 = dict(kropp)
    kropp2["antall_soknader"] = int(kropp["antall_soknader"]) + 1
    r3 = _bestill(klient, cookie, csrf, kropp2, nokkel)
    assert (r3.status_code, r3.json()["feil"]) == (
        409, "idempotenskonflikt"), r3.text
    assert _beslutninger(migrator) == 0
    # …og heller ikke PROFILPORTEN får dømme først (Codex P2, runde 4):
    # annen intensjon med en velformet, ikke-eksisterende profilref er
    # fortsatt samme konflikt — aldri 404.
    kropp3 = dict(kropp)
    kropp3["stillingsprofil_ref"] = \
        "00000000-0000-4000-8000-000000000000@1"
    r4 = _bestill(klient, cookie, csrf, kropp3, nokkel)
    assert (r4.status_code, r4.json()["feil"]) == (
        409, "idempotenskonflikt"), r4.text
    assert _beslutninger(migrator) == 0


@pg
def test_bindefeil_etter_beslutningen_gir_stabil_retry(
        klient, migrator, miljo, inndata_rot, monkeypatch):
    """Cursor P2 (3): feiler `bind_inndata` ETTER en committet TILLAT,
    skrives ingen `bestilling_idempotens`-rad — og nøkkelens kontrakt må
    likevel være ENTYDIG.

    Buntlåsen (P1) fjerner kappløpstapet som årsak; det som står igjen er
    at bunten kan dø av seg selv i vinduet. Utløp lar seg ikke iscenesette
    — `utloper` er et BINDINGSFELT i `inndata_artefakt_vakt()` og kan ikke
    endres på en levende rad — så terminalen måles med den andre veien inn
    i nøyaktig samme `bind_inndata`-raise: `lastet -> forkastet`, satt av
    en annen forbindelse mellom beslutningen og bindingen. Utløpet er
    samme funksjon fem linjer lenger ned.

    Kravet er at svaret er ENDELIG og BILLIG: én kode, kvoten brent
    nøyaktig én gang (prisen for en beslutning som ble tatt), og retryen
    med samme nøkkel svarer det samme uten å ta en ny beslutning — den
    stoppes av forhåndsporten, ikke av kjernen. Den foreldreløse TILLAT-en
    i `idempotens` er den KJENTE resten, og står her som en måling og ikke
    som en antakelse.
    """
    from api import kjerne as kjernemodul
    from db.pg import koble
    _rekr_policy(migrator)
    cookie, csrf = _adminsesjon()
    ref = _bunt(klient, migrator, cookie, csrf)
    profilref = _profil(migrator)
    kropp = _evalkropp(ref, profilref)
    nokkel = "n-" + secrets.token_hex(8)
    ekte = kjernemodul.behandle

    def river_bunten_etter_beslutningen(*a, **kw):
        svar = ekte(*a, **kw)          # beslutningen er nå COMMITTET
        c = koble(MIGRATOR_DSN)
        try:
            _sett_kontekst(c, TENANT)
            c.execute("UPDATE inndata_artefakt SET status='forkastet'"
                      " WHERE tenant=%s AND inndata_id=%s",
                      (TENANT, ref.split(":", 1)[1]))
            c.commit()
        finally:
            c.close()
        return svar

    with monkeypatch.context() as mp:
        mp.setattr(kjernemodul, "behandle",
                   river_bunten_etter_beslutningen)
        r = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert (r.status_code, r.json()["feil"]) == (
        409, "inndata_ubrukelig"), r.text
    assert _beslutninger(migrator) == 1
    _sett_kontekst(migrator, TENANT)
    assert migrator.execute(
        "SELECT count(*) FROM oppdrag WHERE tenant=%s AND"
        " oppdragstype='rekruttering.evaluering'",
        (TENANT,)).fetchone()[0] == 0, "oppdraget ble ikke rullet tilbake"
    assert migrator.execute(
        "SELECT count(*) FROM bestilling_idempotens WHERE tenant=%s AND"
        " idempotensnokkel=%s", (TENANT, nokkel)).fetchone()[0] == 0
    # Den KJENTE resten: kjernens egen rad står igjen som en TILLAT uten
    # oppdrag. Kvoten er brent én gang, og det er hele prisen.
    assert migrator.execute(
        "SELECT count(*) FROM idempotens WHERE tenant=%s AND nokkel=%s"
        " AND status='ferdig'",
        (TENANT, _kjernenokkel(nokkel, kropp))).fetchone()[0] == 1
    migrator.rollback()

    # Retryen er STABIL: samme kode, og forhåndsporten stopper den før
    # kjernen — ingen andre kvoteplass.
    r2 = _bestill(klient, cookie, csrf, kropp, nokkel)
    assert (r2.status_code, r2.json()["feil"]) == (
        409, "inndata_ubrukelig"), r2.text
    assert _beslutninger(migrator) == 1, \
        "retryen tok en ny beslutning på en bunt som ikke kan bindes"
