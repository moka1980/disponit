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


# --------------------------------------------------------------------------
# Stubklienten (m56s `_Stubklient`-form, speilet): hele kjeden — claim →
# resolver → heartbeat → artefakt → kvittering — uten Postgres, med
# valgbar status i hvert ledd. Testene under leser `stier` for å bevise
# hva som IKKE skjedde.
# --------------------------------------------------------------------------

class _Svar:
    def __init__(self, status, kropp=None, content=None):
        self.status_code, self._kropp = status, kropp
        self.content = content

    def json(self):
        if self._kropp is None:
            raise ValueError("ikke JSON")
        return self._kropp

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"uventet {self.status_code}")


def _payload(**endringer):
    return {"stillingsprofil_ref": "p@1",
            "stillingsprofil": {"profil_id": "p", "versjon": 1,
                                "navn": "N",
                                "krav": [{"kravnavn": "drift", "vekt": 3}]},
            "antall_soknader": 1, "omfang": "bunt", **endringer}


class _Stubklient:
    """Kvitteringskroppen speiler det EKTE endepunktet (`api.app`): 200
    bærer `status: "utfort"|"feilet"` — statusskiftet skjedde — mens sen
    evidens gir 202 med `lagret_uten_statusendring`. Kroppen er ikke pynt
    her: controlleren leser den nettopp for å skille de to."""

    def __init__(self, kvitteringsstatus=200, *, opplastingsstatus=200,
                 kvitteringskropp=..., payload=..., opplasting=...,
                 buntstatus=200, frist_om_s=30 * 60, forny=None):
        from datetime import datetime, timedelta, timezone
        naa = datetime.now(timezone.utc)
        self.utforelsesfrist = (
            None if frist_om_s is None
            else (naa + timedelta(seconds=frist_om_s)).isoformat())
        self.kvittering_utloper = self.utforelsesfrist
        self.kvitteringsstatus = kvitteringsstatus
        self.opplastingsstatus = opplastingsstatus
        self.kvitteringskropp = kvitteringskropp
        self.payload = _payload() if payload is ... else payload
        self.opplasting = ({"jti": "kap",
                            "utloper": self.utforelsesfrist}
                           if opplasting is ... else opplasting)
        self.buntstatus = buntstatus
        #: Kallbar som gir `/v1/oppdrag/forny`-svaret. None = 200 uten
        #: fersk kapabilitet. Med standardintervallet (240 s) rekker
        #: pulsen aldri å slå i en test — den må skrus ned med vilje.
        self.forny = forny
        self.kvitteringer = []
        self.stier = []

    def _kvitteringssvar(self, sendt):
        if self.kvitteringskropp is not ...:
            return _Svar(self.kvitteringsstatus, self.kvitteringskropp)
        if self.kvitteringsstatus == 200:
            return _Svar(200, {"status": sendt.get("resultat"),
                               "oppdrag_id": 1})
        return _Svar(self.kvitteringsstatus, {})

    def post(self, sti, json=None, headers=None):
        self.stier.append(sti)
        if sti == "/v1/oppdrag/claim":
            return _Svar(200, {
                "oppdrag_id": 1, "tenant": TENANT, "kvittering_jti": "j",
                "repair_operation_id": "r", "owner_claim_id": "o" * 22,
                "owner_generation": 0,
                "utforelsesfrist": self.utforelsesfrist,
                "kvittering_utloper": self.kvittering_utloper,
                "payload": self.payload,
                "opplasting": self.opplasting})
        if sti.startswith("/v1/inndata/hent-for-oppdrag/"):
            if self.buntstatus != 200:
                return _Svar(self.buntstatus, {"feil": "x"})
            return _Svar(200, content=_buntbytes())
        if sti == "/v1/oppdrag/forny":
            return self.forny() if self.forny else _Svar(200, {})
        if sti == "/v1/artefakt":
            if self.opplastingsstatus != 200:
                return _Svar(self.opplastingsstatus, {})
            return _Svar(200, {"artefakt_id": "a-1",
                               "klartekst_sha256": "b" * 64})
        assert sti == "/v1/oppdrag/kvittering", sti
        self.kvitteringer.append(json)
        return self._kvitteringssvar(json or {})


def _kjor(klient, modell=None):
    from modules.m57_ats import controller
    return controller.kjor_en(klient, "tk", modell or _Modell(),
                              _Uttrekker(), _MAALINGER, lambda k: k)


def test_avvist_kvittering_er_ikke_utfort(monkeypatch):
    """m56s Codex P1, speilet: 409 (fencing, hashavvik, avvist
    promotering) eller 5xx betyr at oppdraget står IGJEN uferdig hos
    plattformen. Meldte controlleren `utfort` uansett, ville en
    planlegger tro at kjøringen var i havn — modulens ord mot
    plattformens tilstand."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)
    for status in (409, 500):
        res = _kjor(_Stubklient(status))
        assert res["utfall"] == "ukvittert", res
        assert res["kvittering_status"] == status
        # Artefaktet ER lastet opp — utfallet skjuler ikke det, det nekter
        # bare å kalle kjøringen ferdig.
        assert res["artefakt_id"] == "a-1"
    ok = _kjor(_Stubklient(200))
    assert ok["utfall"] == "utfort", ok


def test_sen_evidens_202_er_ikke_utfort():
    """m56s Codex P1, speilet: `2xx` alene er ikke bevis for at oppdraget
    ble ferdig. Fullføres kjøringen etter `utforelsesfrist` men før
    evidensfristen, svarer endepunktet 202 med
    `lagret_uten_statusendring` — evidensen bevares, `oppdrag.status`
    står urørt."""
    k = _Stubklient(202, kvitteringskropp={
        "status": "lagret_uten_statusendring", "oppdrag_id": 1})
    res = _kjor(k)
    assert res["utfall"] == "ukvittert", res
    assert res["kvittering_status"] == 202


def test_uleselig_kvitteringskropp_er_ikke_utfort():
    """Fail-closed: 200 med en kropp vi ikke kan lese sier ingenting om
    hva plattformen gjorde, og «vet ikke» er ikke «ferdig»."""
    assert _kjor(_Stubklient(200, kvitteringskropp=None))["utfall"] == \
        "ukvittert"
    assert _kjor(_Stubklient(200, kvitteringskropp=["utfort"]))["utfall"] \
        == "ukvittert"


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


def test_http_frist_passer_innenfor_avslutningsmargin():
    """m56s `test_http_frist_passer_innenfor_avslutningsmargin`, portert
    for m57s konstanter: verstefallet av retryen skal få plass i den
    marginen `_evalueringsfrist` har reservert til avslutningen.

    Fristen var `margin / (2 * LEVERINGSFORSOK)` og lot PAUSENE stå
    utenfor budsjettet — med `LEVERINGSPAUSE_S = 5.0` er de 60 sekunder
    av 120. Testen regner verstefallet av de SAMME konstantene koden
    bruker: skrus `LEVERINGSFORSOK` eller pausen opp, eller marginen ned,
    uten at fristen følger med, blir denne rød."""
    from modules.m57_ats import controller

    frist = controller.http_frist_s()
    kall = controller.LEVERINGSFORSOK * controller.LEVERINGSRUNDER
    pauser = controller.LEVERINGSRUNDER * sum(
        controller.LEVERINGSPAUSE_S * f
        for f in range(controller.LEVERINGSFORSOK))
    verstefall = kall * frist + pauser + controller.AVSLUTNINGSARBEID_S
    assert verstefall <= controller.AVSLUTNINGSMARGIN_S, (verstefall, frist)
    # ... og fristen skal være det marginen faktisk gir, ikke et vilkårlig
    # mindre tall: hele budsjettet er til for å BRUKES når plattformen er
    # treg, bare ikke til å overskrides.
    assert verstefall == pytest.approx(controller.AVSLUTNINGSMARGIN_S)
    # Gulvet: en absurd liten margin gir en kort frist, ikke en negativ.
    assert controller.http_frist_s(margin_s=1) == 1.0


def test_arbeideren_bruker_den_avledede_fristen():
    """Fristen er ingen port hvis arbeideren setter sin egen ved siden
    av: klienten skal få `controller.http_frist_s()`, aldri et fast
    tall."""
    from pathlib import Path

    kilde = (Path(__file__).resolve().parents[2]
             / "drift/m57_arbeider.py").read_text(encoding="utf-8")
    assert "KlientHTTP(api, controller.http_frist_s())" in kilde


def test_payloaden_maales_mot_plattformens_kontrakt():
    """m56s `_kontraktsbrudd`, speilet: utføreren leser den SAMME
    tabellen (`oppdragskontrakt`) som stoppet oppdraget ved
    opprettelsen. Den håndrullede sjekken var en annen port enn den —
    `antall_soknader: 5001` (klarsignalet §4, HARD grense),
    `omfang: "alt"`, en `stillingsprofil_ref` som ikke er en referanse og
    en `slettefrist_dogn` utenfor 30–365 slapp alle gjennom her.

    Kontroll: fjern kontraktsoppslaget, så henter denne bunten og kjører
    modellen på en bestilling plattformen selv kaller ulovlig."""
    for endring, felt in (({"antall_soknader": 5001}, "antall_soknader"),
                          ({"antall_soknader": 0}, "antall_soknader"),
                          ({"omfang": "alt"}, "omfang"),
                          ({"stillingsprofil_ref": 123},
                           "stillingsprofil_ref"),
                          ({"slettefrist_dogn": 3650},
                           "slettefrist_dogn")):
        k = _Stubklient(payload=_payload(**endring))
        res = _kjor(k)
        assert res["utfall"] == "avbrutt", (endring, res)
        assert res["grunn"] == f"oppdrag_ugyldig:{felt}", res
        assert k.kvitteringer[0]["feilkode"] == "oppdrag_ugyldig"
        # Bunten ble ALDRI hentet: persondata koster, og en bestilling
        # ingen rapport kan oppfylle skal ikke koste dem.
        assert not [s for s in k.stier
                    if s.startswith("/v1/inndata/hent-for-oppdrag/")]


def test_profilens_indre_form_er_fortsatt_modulens_egen():
    """Kontrakten krever at `stillingsprofil` FINNES; det er
    controlleren som leser `krav[].kravnavn/vekt` ut til vektkartet. Den
    sjekken blir derfor stående ved siden av kontraktsoppslaget."""
    for profil, felt in (({"profil_id": "p", "versjon": 1, "navn": "N",
                           "krav": []}, "krav"),
                         ({"profil_id": "p", "versjon": 1, "navn": "N",
                           "krav": [{"kravnavn": "drift", "vekt": True}]},
                          "krav"),
                         ("ikke et objekt", "stillingsprofil")):
        k = _Stubklient(payload=_payload(stillingsprofil=profil))
        assert _kjor(k)["grunn"] == f"oppdrag_ugyldig:{felt}", profil


class _VenterModell(_Modell):
    """Holder evalueringen i gang til pulsen HAR slått, slik at
    fornyelsessvaret rekker å nå controlleren mens arbeidet pågår —
    uten en sanntidspause å vente på.

    Kappløpet er lukket av `_Heartbeat.__exit__`, ikke av timing: den
    setter stoppflagget og JOINER tråden, så `tapt` er ferdig skrevet før
    `with`-blokken slipper."""

    def __init__(self, slo):
        super().__init__()
        self._slo = slo

    def vurder(self, tekst, vekter):
        assert self._slo.wait(10), "pulsen slo aldri"
        return super().vurder(tekst, vekter)


def test_tapt_lease_stopper_for_opplastingen(monkeypatch):
    """En terminal 4xx på `/v1/oppdrag/forny` betyr at autoriteten er
    borte: plattformen har gitt oppdraget til noen andre eller lukket
    det. Evalueringen ble ferdig uten gyldig lease, og da skal rapporten
    ikke lastes opp i det hele tatt — `tapt` ble tidligere bare med som
    et ekstra felt PÅ vei ut av en opplasting som allerede hadde feilet.

    Kontroll: fjern `puls.tapt`-porten i `kjor_en`, så laster denne opp
    et artefakt fra en utfører som ikke lenger eier oppdraget."""
    import threading

    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.01)
    slo = threading.Event()

    def forny():
        slo.set()
        return _Svar(409, {"feil": "lease_utlopt"})

    k = _Stubklient(forny=forny)
    res = _kjor(k, modell=_VenterModell(slo))
    assert res["utfall"] == "avbrutt", res
    assert res["grunn"] == "lease_tapt", res
    assert res["lease_tapt"] == "lease_utlopt", res
    assert k.kvitteringer[0]["feilkode"] == "lease_tapt"
    # Rapporten ble ALDRI sendt.
    assert "/v1/artefakt" not in k.stier


def test_umulig_frist_stopper_for_bunten_hentes():
    """m56s Codex P1, speilet: et claim uten brukbart vindu kan aldri bli
    et AVSLUTTET oppdrag. m57s versjon av regnestykket er strengere enn
    m56s — det som spares er ikke trafikk ut, men utleveringen av
    søknadsbunten (persondata) inn i containeren, og modellkallene på
    den.

    Kontroll: fjern `_evalueringsfrist`-porten, så henter denne bunten og
    kjører modellen for et oppdrag ingen kvittering kan avslutte."""
    from modules.m57_ats import controller

    for frist_om_s in (-60, 0, int(controller.AVSLUTNINGSMARGIN_S)):
        k = _Stubklient(frist_om_s=frist_om_s)
        res = _kjor(k)
        assert res["utfall"] == "avbrutt", (frist_om_s, res)
        assert res["grunn"] == "frist_utilstrekkelig", res
        assert k.kvitteringer[0]["feilkode"] == "frist_utilstrekkelig"
        # Bunten ble ALDRI hentet, og modellen aldri rørt.
        assert not [s for s in k.stier
                    if s.startswith("/v1/inndata/hent-for-oppdrag/")]
        assert "/v1/artefakt" not in k.stier


def test_uleselig_frist_er_ingen_frist():
    """Mangler `utforelsesfrist`, eller kommer den uten tidssone, har
    modulen intet vindu å kjøre innenfor — og et gjett på sonen er timer
    feil vei. Fail-closed, aldri fallback til taket."""
    for endring in ({"utforelsesfrist": None},
                    {"utforelsesfrist": "2099-01-01T00:00:00"},
                    {"utforelsesfrist": "i morgen"}):
        k = _Stubklient()
        for felt, verdi in endring.items():
            setattr(k, felt, verdi)
        res = _kjor(k)
        assert res["grunn"] == "frist_utilstrekkelig", (endring, res)
        assert res["frist_s"] is None, res


def test_opplastingens_utlop_er_ogsaa_en_frist():
    """Vinduet er den TIDLIGSTE av de tre grensene claimet navngir — en
    romslig `utforelsesfrist` redder ikke en kapabilitet som løper ut
    først."""
    k = _Stubklient(frist_om_s=4 * 60 * 60)
    k.opplasting = {"jti": "kap", "utloper": "2000-01-01T00:00:00+00:00"}
    res = _kjor(k)
    assert res["grunn"] == "frist_utilstrekkelig", res


def test_avvist_feilkvittering_er_heller_ikke_ferdig(monkeypatch):
    """m56s Codex P1, speilet på feilveien: bunten er uhentbar, men
    plattformen tok heller ikke imot FEIL-kvitteringen (409/5xx/tapt
    svar). Da er oppdraget verken utført eller avsluttet — det står
    claimet og uferdig, og `avbrutt` ville vært modulens ord mot
    plattformens tilstand.

    Kontroll: la `_feilutfall` returnere `avbrutt` fast igjen, så blir
    denne rød."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)
    for status in (409, 500):
        k = _Stubklient(status, buntstatus=404)
        res = _kjor(k)
        assert res["utfall"] == "ukvittert", res
        assert res["kvittert"] is False
        # Grunnen overlever utfallet: HVORFOR kjøringen feilet er like
        # sant om kvitteringen kom frem eller ikke.
        assert res["grunn"] == "bunt_uhentbar"
        assert res["kvittering_status"] == status
        assert "/v1/artefakt" not in k.stier

    # ... og en feil-kvittering plattformen FAKTISK tok imot er `avbrutt`.
    k = _Stubklient(200, buntstatus=404)
    res = _kjor(k)
    assert res["utfall"] == "avbrutt", res
    assert res["kvittert"] is True


def test_sen_evidens_paa_feilkvitteringen_er_ikke_avbrutt():
    """202 `lagret_uten_statusendring` på feil-kvitteringen: evidensen er
    bevart, men `oppdrag.status` står urørt — oppdraget er ikke
    terminert."""
    k = _Stubklient(202, buntstatus=404, kvitteringskropp={
        "status": "lagret_uten_statusendring", "oppdrag_id": 1})
    assert _kjor(k)["utfall"] == "ukvittert"


def test_uhentbar_bunt_kvitteres_feilet(monkeypatch):
    """Resolveren sier nei → feilkvittering med kode, aldri taushet —
    og modellen ble aldri rørt (persondata-økonomien)."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)
    claim = {"oppdrag_id": 7, "tenant": "t-x",
             "kvittering_jti": "kj", "repair_operation_id": "r",
             "owner_claim_id": "c" * 22, "owner_generation": 1,
             "payload": {"stillingsprofil_ref": "p@1",
                         "stillingsprofil": {
                             "profil_id": "p", "versjon": 1, "navn": "N",
                             "krav": [{"kravnavn": "drift", "vekt": 3}]},
                         "antall_soknader": 1, "omfang": "bunt"},
             "opplasting": {"jti": "oj", "utloper": "2099-01-01T00:00:00+00:00"},
             # Fristene er en del av det EKTE claim-svaret, og
             # `_evalueringsfrist` regner vinduet ut av dem: uten
             # `utforelsesfrist` her ville stubben bevist noe endepunktet
             # aldri sender.
             "utforelsesfrist": "2099-01-01T00:00:00+00:00",
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
                # Kroppen det EKTE endepunktet sender ved statusskifte.
                # Den sto tom her, og en tom kropp er ikke en kvittering
                # (`_kvittert`): stubben beviste `avbrutt` på et svar
                # plattformen aldri gir, og skjulte at feilveien meldte
                # `avbrutt` uten å ha lest svaret i det hele tatt.
                return _R(200, {"status": "feilet", "oppdrag_id": 7})
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
