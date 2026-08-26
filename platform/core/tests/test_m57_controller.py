"""m57-controlleren: claim → hent bunt (060) → heartbeat (063) →
evaluer → rapport → kvittering. m56-controllertestenes form.

E2E-testen kjører HELE kjeden mot ekte plattform: bestillingen fra
#210-riggen føder oppdraget med bunten bundet (X1), controlleren
claimer med modultoken, henter bunten via resolveren, evaluerer med
fake-modellen, laster opp rapporten og kvitterer — og artefaktet står
PROMOTERT med rangeringen inni.
"""
import io
import json
import secrets
import zipfile

import pytest

from .test_api import DSN, MIGRATOR_DSN, klient, migrator, miljo  # noqa: F401
from .test_bestilling_rekruttering import (_adminsesjon, _bestill,
                                           _evalkropp, _profil,
                                           _rekr_policy,
                                           _sikre_m57_claimbar,
                                           _sett_kontekst)
from .test_inndata_http import inndata_rot  # noqa: F401
from .test_m57_modul import _MAALINGER, _Modell

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")

TENANT = "t-api"


class _Uttrekker:
    def tekst_for(self, medlem, data):
        return data.decode("utf-8")


def _buntbytes() -> bytes:
    """En LOVLIG bunt etter hele #161/#158-kontrakten: manifest med
    toveisbinding OG deklarerte personfelter."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("k1/cv.html",
                    "<!doctype html><html><body><p>Kari Testdal kan "
                    "drift</p></body></html>")
        zf.writestr("soknader.json", json.dumps({"soknader": [
            {"kandidat_id": "k1", "filer": ["k1/cv.html"],
             "felter": {"navn": ["Kari Testdal"]}}]}))
    return buf.getvalue()


def _bunt_via_http(klient, cookie, csrf) -> str:
    from api import sesjon as sesjonmodul
    r = klient.post("/v1/inndata/reserver",
                    json={"eiermodul": "m57_ats",
                          "formaal": "soknadsbunt"},
                    cookies={sesjonmodul.C_SESJON: cookie},
                    headers={"X-Disponit-CSRF": csrf,
                             "Idempotency-Key": secrets.token_hex(12)})
    assert r.status_code == 201, r.text
    r2 = klient.put(f"/v1/inndata/opplast/{r.json()['reservasjon_jti']}",
                    content=_buntbytes(),
                    cookies={sesjonmodul.C_SESJON: cookie},
                    headers={"X-Disponit-CSRF": csrf,
                             "content-type": "application/zip"})
    assert r2.status_code == 201, r2.text
    return r.json()["inndata_ref"]


def _registrer_rapporttypen(m):
    """Rapportskjemaet inn i skjema-/typeregisteret (036-formen), bundet
    til m57-kontrakten riggen laget — idempotent."""
    from modules.m57_ats import rapportskjema
    from policy_validator import jcs

    kanonisk = jcs.kanoniske_bytes(rapportskjema.SKJEMA)
    import hashlib
    h = hashlib.sha256(kanonisk).hexdigest()
    m.execute("INSERT INTO artefaktskjema (skjema_hash, kanonisk)"
              " VALUES (%s,%s) ON CONFLICT DO NOTHING",
              (h, kanonisk.decode("utf-8")))
    khash = m.execute(
        "SELECT kontrakt_hash FROM modulkontrakt"
        " WHERE modul_id='m57_ats' AND kontraktversjon=1").fetchone()[0]
    m.execute(
        "INSERT INTO artefakttype_register (artefakttype, eiermodul,"
        " kontraktversjon, kontrakt_hash, skjema_hash)"
        " VALUES ('rekruttering.evaluering.rapport','m57_ats',1,%s,%s)"
        " ON CONFLICT DO NOTHING", (khash, h))
    m.commit()


@pg
def test_controlleren_hele_veien(migrator, miljo, inndata_rot,
                                 monkeypatch):
    """Bestilling → oppdrag m/ bundet bunt → claim m/ modultoken →
    resolver → kjor_bunt (blindet, manifestdrevet) → rapport →
    promotert artefakt → utfort kvittering."""
    from starlette.testclient import TestClient
    from api.app import lag_app
    from db import kryptering
    from modules.m57_ats import controller
    from .test_m37 import _signer_kvittering
    from .test_modul_onboarding_http import _onboard_token

    _rekr_policy(migrator)
    _sikre_m57_claimbar(migrator)
    _registrer_rapporttypen(migrator)
    rel = migrator.execute(
        "SELECT release_id FROM moduldeployment WHERE modul_id='m57_ats'"
        " AND livslop='claiming' LIMIT 1").fetchone()[0]
    migrator.rollback()

    a = lag_app(DSN)
    try:
        with TestClient(a) as c:
            cookie, csrf = _adminsesjon()
            ref = _bunt_via_http(c, cookie, csrf)
            profilref = _profil(migrator)
            r = _bestill(c, cookie, csrf, _evalkropp(ref, profilref),
                         "n-" + secrets.token_hex(8))
            assert r.status_code == 200, r.text
            assert r.json()["beslutning"] == "tillat", r.text
            oppdrag_id = r.json()["oppdrag_id"]

            mtk, _ = _onboard_token(c, migrator, "m57_ats", rel)
            res = controller.kjor_en(c, mtk, _Modell(), _Uttrekker(),
                                     _MAALINGER, _signer_kvittering)
            assert res["utfall"] == "utfort", res
            assert res["kvittering_status"] == 200, res
            assert res["kandidater"] == 1

            _sett_kontekst(migrator, TENANT)
            tilstand, ct, nonce, ref_dek = migrator.execute(
                "SELECT tilstand, ciphertext, nonce, dek_ref FROM"
                " artefakt WHERE artefakt_id=%s",
                (res["artefakt_id"],)).fetchone()
            assert tilstand == "promotert", tilstand
            dek = kryptering.hent_dek(migrator, TENANT, ref_dek)
            rapport = kryptering.dekrypter(dek, bytes(ct), bytes(nonce),
                                           TENANT, ref_dek)
            assert rapport["rapporttype"] == \
                "rekruttering.evaluering.rapport"
            assert rapport["rangering"][0]["kandidat_id"] == "k1"
            # Blindingen holdt hele veien til det promoterte artefaktet.
            assert "Kari Testdal" not in json.dumps(rapport)
            status = migrator.execute(
                "SELECT status FROM oppdrag WHERE id=%s",
                (oppdrag_id,)).fetchone()[0]
            migrator.rollback()
            assert status == "utfort", status
    finally:
        pass


def test_tom_ko_er_tomt_utfall():
    from modules.m57_ats import controller

    class _K:
        def post(self, sti, json=None, headers=None):
            class _R:
                status_code = 204
            assert sti == "/v1/oppdrag/claim"
            return _R()

    res = controller.kjor_en(_K(), "tk", None, None, {}, lambda k: k)
    assert res == {"utfall": "tomt"}


def test_uhentbar_bunt_kvitteres_feilet(monkeypatch):
    """Resolveren sier nei → feilkvittering med kode, aldri taushet —
    og modellen ble aldri rørt (persondata-økonomien)."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)
    claim = {"oppdrag_id": 7, "tenant": "t-x",
             "kvittering_jti": "kj", "repair_operation_id": "r",
             "owner_claim_id": "c" * 22, "owner_generation": 1,
             "payload": {"stillingsprofil": {
                 "profil_id": "p", "versjon": 1, "navn": "N",
                 "krav": [{"kravnavn": "drift", "vekt": 3}]},
                 "antall_soknader": 1, "omfang": "bunt"},
             "opplasting": {"jti": "oj", "utloper": "2099-01-01T00:00:00+00:00"},
             "kvittering_utloper": "2099-01-01T00:00:00+00:00"}

    class _R:
        def __init__(self, status, kropp=None):
            self.status_code = status
            self._k = kropp

        def json(self):
            return self._k

        def raise_for_status(self):
            pass

    kvitteringer = []

    class _K:
        def post(self, sti, json=None, headers=None):
            if sti == "/v1/oppdrag/claim":
                return _R(200, claim)
            if sti.startswith("/v1/inndata/hent-for-oppdrag/"):
                return _R(404, {"feil": "x"})
            if sti == "/v1/oppdrag/kvittering":
                kvitteringer.append(json)
                return _R(200, {})
            raise AssertionError(sti)

    res = controller.kjor_en(_K(), "tk", None, None, {}, lambda k: k)
    assert res["utfall"] == "avbrutt"
    assert res["grunn"] == "bunt_uhentbar"
    assert kvitteringer and kvitteringer[0]["feilkode"] == "bunt_uhentbar"


def test_heartbeatet_fornyer_og_bytter_kapabilitet(monkeypatch):
    """Pulsen poster fornyelsen med claimets identitet, og en FERSK
    opplastingskapabilitet fra fornyelsen erstatter claimens."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.05)
    claim = {"oppdrag_id": 9, "owner_claim_id": "c" * 22,
             "owner_generation": 2}
    fornyelser = []

    class _K:
        def post(self, sti, json=None, headers=None):
            assert sti == "/v1/oppdrag/forny"
            fornyelser.append(json)

            class _R:
                status_code = 200

                def json(self):
                    return {"opplasting": {"jti": "fersk-jti",
                                           "utloper": "2099-01-01T00:00:00+00:00"}}
            return _R()

    import time as _t
    with controller._Heartbeat(_K(), {}, claim) as puls:
        _t.sleep(0.2)
    assert fornyelser, "pulsen slo aldri"
    assert fornyelser[0] == {"oppdrag_id": 9,
                             "owner_claim_id": "c" * 22,
                             "owner_generation": 2,
                             "lease_s": controller.FORNY_LEASE_S}
    assert puls.fersk_opplasting == {"jti": "fersk-jti",
                                     "utloper": "2099-01-01T00:00:00+00:00"}


def test_avvist_fornyelse_stopper_pulsen(monkeypatch):
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.05)
    kall = []

    class _K:
        def post(self, sti, json=None, headers=None):
            kall.append(sti)

            class _R:
                status_code = 409

                def json(self):
                    return {"feil": "lease_utlopt"}
            return _R()

    import time as _t
    with controller._Heartbeat(_K(), {}, {"oppdrag_id": 1,
                                          "owner_claim_id": "c" * 22,
                                          "owner_generation": 1}) as puls:
        _t.sleep(0.3)
    assert puls.tapt == "lease_utlopt"
    assert len(kall) == 1, "pulsen fortsatte etter en terminal avvisning"
