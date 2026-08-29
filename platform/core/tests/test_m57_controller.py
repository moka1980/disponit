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
import time
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
                 buntstatus=200, frist_om_s=30 * 60, forny=None,
                 artefaktkropp=...):
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
        #: `...` = det vanlige 2xx-svaret; alt annet settes rått, så en
        #: uleselig kropp kan prøves på en 200.
        self.artefaktkropp = artefaktkropp
        #: Kallbar som gir `/v1/oppdrag/forny`-svaret. None = 200 uten
        #: fersk kapabilitet. Med standardintervallet (240 s) rekker
        #: pulsen aldri å slå i en test — den må skrus ned med vilje.
        self.forny = forny
        #: #173: skriveveien inn i kandidatlagrene. Alt fanges for
        #: assertions; `kandidatdatastatus` lar en test felle sinken.
        self.kandidatdokumenter = []
        self.kandidatartefakter = []
        self.kandidatdatastatus = 200
        #: Antall kandidatdata-kall som LYKKES før `kandidatdatastatus`
        #: slår inn. 0 = grensen gjelder fra første kall, altså den gamle
        #: oppførselen. Et tall > 0 gir den DELVISE commiten: noen skriv
        #: står i lagrene når strømmen ryker — feilmodusen «alt feiler
        #: fra første kall» aldri kan måle.
        self.kandidatdata_ok_forst = 0
        self.kvitteringer = []
        self.stier = []

    def _kandidatdatasvar(self):
        """Kallet er alt talt når dette kjører, så n = 1 på det første.
        `kandidatdata_ok_forst = 0` gir da `kandidatdatastatus` fra og med
        kall 1 — uendret standardoppførsel."""
        n = len(self.kandidatdokumenter) + len(self.kandidatartefakter)
        return 200 if n <= self.kandidatdata_ok_forst \
            else self.kandidatdatastatus

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
        if sti == "/v1/rekruttering/kandidatdokument":
            self.kandidatdokumenter.append(json)
            return _Svar(self._kandidatdatasvar(), {})
        if sti == "/v1/rekruttering/kandidatartefakt":
            self.kandidatartefakter.append(json)
            return _Svar(self._kandidatdatasvar(), {})
        if sti == "/v1/artefakt":
            if self.opplastingsstatus != 200:
                return _Svar(self.opplastingsstatus, {})
            if self.artefaktkropp is not ...:
                return _Svar(200, self.artefaktkropp)
            return _Svar(200, {"artefakt_id": "a-1",
                               "klartekst_sha256": "b" * 64})
        assert sti == "/v1/oppdrag/kvittering", sti
        self.kvitteringer.append(json)
        return self._kvitteringssvar(json or {})


def _kjor(klient, modell=None):
    from modules.m57_ats import controller
    return controller.kjor_en(klient, "tk", modell or _Modell(),
                              _Uttrekker(), _MAALINGER, lambda k: k)


def test_173_kandidatdata_stroemmes_underveis(monkeypatch):
    """#173 (eiers valg b + i): hvert medlem går til dokumentveien I DET
    det er lest, og hver kandidat til artefaktveien i det den er
    evaluert — aldri en sluttbatch. Kallene bærer claim-trippelen
    (fullmakten er claimets) og manifestets kandidat-ID; UUID-ene er
    dørens og finnes ikke i kroppen.

    MUTASJONEN SOM DREPER DENNE: fjern sink-kallene i `kjor_bunt`, eller
    slutt å sende sinkene fra `kjor_en`."""
    import base64 as b64mod

    from modules.m57_ats import controller
    monkeypatch.setattr(controller, "_sov", lambda s: None)
    k = _Stubklient()
    res = _kjor(k)
    assert res["utfall"] == "utfort", res
    assert k.kandidatdokumenter, "ingen dokumenter strømmet til lageret"
    assert k.kandidatartefakter, "ingen artefakter strømmet til lageret"
    for kall in k.kandidatdokumenter + k.kandidatartefakter:
        assert kall["tenant"] == TENANT
        assert kall["oppdrag_id"] == 1
        assert kall["owner_claim_id"] == "o" * 22
        assert kall["owner_generation"] == 0
        assert kall["kandidat_id"], kall
    dok = k.kandidatdokumenter[0]
    assert b64mod.b64decode(dok["dokument_b64"]), \
        "dokumentveien skal bære de rå bytene"
    assert isinstance(dok["tekst"], str) and dok["tekst"].strip(), \
        "dokumentveien skal bære parsetteksten"
    art = k.kandidatartefakter[0]["artefakt"]
    assert set(art) == {"funn", "oppfylt", "vekter", "kildetekst"}, \
        "artefaktveien skal bære evalueringens fire deler, intet mer"
    # AVMASKERINGEN ER EGET TOPPNIVÅFELT (Codex P1). Den skal FINNES —
    # uten den er den blindede `kildetekst` over lagret med tokener ingen
    # kan løse opp — og den skal ikke ligge INNE i `artefakt`, for da får
    # `kandidat_evalueringsartefakt` en klartekstkopi som overlever
    # nøyaktig det `kandidat_avmaskering` reapes for.
    avm = k.kandidatartefakter[0]["avmaskering"]
    assert isinstance(avm, dict) and avm, \
        "avmaskeringskartet skal følge den claim-bundne skriveveien"
    assert all(isinstance(t, str) and isinstance(v, str)
               for t, v in avm.items()), avm
    assert "avmaskering" not in art
    # Rapporten er fortsatt komplett (v1-skjemaet, til #168s v2).
    assert "/v1/artefakt" in k.stier


def test_173_vektene_folger_hver_kandidat_inn_i_lageret(monkeypatch):
    """#173 (Codex P1): profilens vekter persisteres med kandidaten.

    `rekruttering._kandidater` leser `vekter` fra HVERT
    `kandidat_evalueringsartefakt` og utleder prosessens vekting av dem
    (`vekter_kilde`). Sinken plukket funn/oppfylt/kildetekst og lot
    feltet ligge, så flaten fant ingen vekter, falt til reserven
    `{krav: 3}` og meldte `vekter_kilde="standard"` — den VISTE altså en
    annen vekting enn den `ranger` faktisk rangerte etter, foran en
    irreversibel signering.

    Vekten her er 7, ikke 3: med fixturens standardprofil er reserven og
    profilen samme tall, og testen ville vært grønn også uten feltet.
    Det er nettopp de profilene som avviker fra reserven funnet handler
    om.

    MUTASJONEN SOM DREPER DENNE: fjern `"vekter": vekter` fra
    `lagre_kandidat`s artefaktkropp."""
    from modules.m57_ats import controller
    monkeypatch.setattr(controller, "_sov", lambda s: None)
    profil = {"profil_id": "p", "versjon": 1, "navn": "N",
              "krav": [{"kravnavn": "drift", "vekt": 7}]}
    k = _Stubklient(payload=_payload(stillingsprofil=profil))
    res = _kjor(k)
    assert res["utfall"] == "utfort", res
    assert k.kandidatartefakter, "ingen artefakter strømmet til lageret"
    for kall in k.kandidatartefakter:
        assert kall["artefakt"]["vekter"] == {"drift": 7}, kall["artefakt"]


def test_173_sinkfeil_er_kodet_avbrudd(monkeypatch):
    """En feilet kandidatlagring er et KODET utfall — kjøringen stopper
    før flere medlemmer pakkes ut, ingenting lastes opp, og
    kvitteringen sier `kjoring_avbrutt` (SP-3, aldri en rå exception).

    MUTASJONEN SOM DREPER DENNE: la sinken svelge ikke-2xx i
    `kjor_en`s `lagre_dokument`."""
    from modules.m57_ats import controller
    monkeypatch.setattr(controller, "_sov", lambda s: None)
    k = _Stubklient()
    k.kandidatdatastatus = 500
    res = _kjor(k)
    assert res["utfall"] == "avbrutt", res
    assert res["grunn"] == "kjoring_avbrutt:kandidatlagring_feilet", res
    assert "/v1/artefakt" not in k.stier, \
        "en kjøring som ikke fikk lagret kandidatdata leverte likevel"
    assert k.kvitteringer and \
        k.kvitteringer[0]["feilkode"] == "kjoring_avbrutt"


def test_173_delvis_stroem_feller_kjoringen_uten_promotering(monkeypatch):
    """#173 (Cursor P2-5): den FAKTISKE strømmefeilmodusen — noen skriv
    står alt i lagrene når neste ryker.

    `test_173_sinkfeil_er_kodet_avbrudd` feller sinken fra FØRSTE kall,
    og da er «ingenting ble skrevet» sant uten at noen kode sørget for
    det. Her lykkes dokumentveien og artefaktveien feiler: kjøringen har
    en delvis commit bak seg, og porten er at den likevel ikke leverer.
    Delvis lagret kandidatdata reapes med prosessen (057), mens et
    promotert artefakt er en påstand om en FULLFØRT evaluering — det er
    forskjellen på et avbrudd og en løgn.

    MUTASJONEN SOM DREPER DENNE: la `kjor_bunt` fortsette til
    rapportbygging når `lagre_kandidat` reiser, eller la `kjor_en`
    promotere før utfallet er kjent."""
    from modules.m57_ats import controller
    monkeypatch.setattr(controller, "_sov", lambda s: None)
    k = _Stubklient()
    k.kandidatdatastatus = 500
    k.kandidatdata_ok_forst = 1         # dokumentet lander, artefaktet ryker
    res = _kjor(k)
    assert res["utfall"] == "avbrutt", res
    assert res["grunn"] == "kjoring_avbrutt:kandidatlagring_feilet", res
    # DELVIS: dokumentveien svarte 200, så et skriv står i lagrene — det
    # er nettopp den tilstanden den gamle porten ikke kunne konstruere.
    assert len(k.kandidatdokumenter) == 1, k.kandidatdokumenter
    # MINST ett artefaktforsøk — aldri nøyaktig ett: `lever` retrier
    # transiente 5xx, og antall FORSØK er transportens tall, ikke
    # portens. Porten er at kjøringen avbrytes kodet og aldri leverer;
    # retry mot en idempotent dør er samme skriv, ikke et nytt.
    assert len(k.kandidatartefakter) >= 1, k.kandidatartefakter
    assert "/v1/artefakt" not in k.stier, \
        "en kjøring med delvis lagret kandidatdata promoterte likevel"
    assert k.kvitteringer and \
        k.kvitteringer[0]["feilkode"] == "kjoring_avbrutt"


class _LeasetapMidtIStroemmen(_Stubklient):
    """Døren slik plattformen faktisk oppfører seg når leasen er tapt.

    Det FØRSTE skrivet lander — det er den delvise commiten som gjør
    dette til et tap MIDT i strømmen og ikke før den. Fra og med det
    andre venter døren til pulsen HAR registrert tapet, og svarer så
    409 `kandidatdata_avvist`.

    Ventingen er det som gjør porten deterministisk: `_Heartbeat` lever
    i en egen tråd, og `except Kjoringsfeil` leser `puls.tapt` INNE i
    `with`-blokka — altså før `__exit__` joiner tråden. Uten den ville
    testen målt kappløpet mellom tråden og strømmen i stedet for
    feilattribusjonen den finnes for. 409 er 4xx, så `lever` retryer
    den ikke: nøyaktig ett avvist skriv feller kjøringen."""

    def __init__(self, pulser, **kw):
        super().__init__(**kw)
        self._pulser = pulser

    def _kandidatdatasvar(self):
        n = len(self.kandidatdokumenter) + len(self.kandidatartefakter)
        if n <= 1:
            return 200
        frist = time.monotonic() + 10
        while not (self._pulser and self._pulser[0].tapt) \
                and time.monotonic() < frist:
            time.sleep(0.001)
        assert self._pulser and self._pulser[0].tapt, \
            "pulsen registrerte aldri lease-tapet"
        return 409


def test_173_leasetap_midt_i_stroemmen_meldes_som_lease_tapt(monkeypatch):
    """#173 (Cursor P2-1/P2-2): et lease-tap midtveis heter `lease_tapt`
    på kvitteringen, ikke `kandidatlagring_feilet`.

    Før strømmingen traff et tap midtveis LEVERINGSPORTEN etter
    `with _Heartbeat` — den som navngir at det var AUTORITETEN som falt
    bort. Nå er skriveveien den faktiske aborten: døren feller neste
    kandidatskriv med 409, sinken reiser, og `kjor_bunt` pakker enhver
    sinkfeil som `kandidatlagring_feilet`. `except Kjoringsfeil`
    returnerte da FØR porten under rakk å lese `puls.tapt`, som alt sto
    satt — drift leste en lagringsfeil, og kvitteringen sa
    `kjoring_avbrutt` om et oppdrag plattformen hadde gitt til noen
    andre.

    `test_173_doed_lease_stenger_doren_midt_i_stroemmen` måler at DØREN
    avviser; ingen port målte hvilket ORD utfallet fikk. Uten denne kan
    en regresjon passere CI med døren fortsatt lukket.

    MUTASJONEN SOM DREPER DENNE: fjern `puls.tapt`-grenen i
    `except kjoring.Kjoringsfeil` — da blir `grunn`
    `kjoring_avbrutt:kandidatlagring_feilet` og feilkoden
    `kjoring_avbrutt`."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.01)
    monkeypatch.setattr(controller, "_sov", lambda s: None)

    # Instansen fanges fordi BÅDE døren og porten må snakke om SAMME
    # puls: det er `puls.tapt` fiksen leser, så det er `puls.tapt`
    # testen må synkronisere mot.
    pulser: list = []
    ekte = controller._Heartbeat

    class _Fanget(ekte):                            # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            pulser.append(self)

    monkeypatch.setattr(controller, "_Heartbeat", _Fanget)

    k = _LeasetapMidtIStroemmen(
        pulser, forny=lambda: _Svar(409, {"feil": "lease_utlopt"}))
    res = _kjor(k)

    assert res["utfall"] == "avbrutt", res
    assert res["grunn"] == "lease_tapt", res
    assert res["lease_tapt"] == "lease_utlopt", res
    assert k.kvitteringer and \
        k.kvitteringer[0]["feilkode"] == "lease_tapt", k.kvitteringer
    # MIDT i strømmen: ett skriv sto alt i lagrene da fullmakten døde.
    assert len(k.kandidatdokumenter) == 1, k.kandidatdokumenter
    assert "/v1/artefakt" not in k.stier, \
        "en kjøring uten gyldig lease promoterte likevel"


def test_173_doeren_avviser_foer_pulsen_slaar_og_utfallet_er_lease_tapt(
        monkeypatch):
    """#173 (Cursor P1-1): den EKTE rekkefølgen — døren avviser FØRST,
    pulsen ville slått minutter senere.

    Testen over lar stubbdøren vente på `puls.tapt` før den svarer 409,
    og måler derfor bare grenen etter at tråden alt har slått. Drift er
    motsatt: `_lopp` sover `FORNY_INTERVALL_S` = 240 s mellom hvert kall,
    mens døren måler leasen på veggklokken ved HVERT skriv. Avvisningen
    kommer altså først, og `except Kjoringsfeil` leser en `puls.tapt` som
    ennå er `None` — remappen fra runde 1 traff aldri der den skulle, og
    kvitteringen sa `kjoring_avbrutt` om et autoritetstap.

    Her står `FORNY_INTERVALL_S` urørt på 240 s: tråden REKKER ikke å
    pulse i løpet av testen, så `puls.tapt` er beviselig usatt når
    utfallet avgjøres. At `/v1/oppdrag/forny` likevel er kalt, er selve
    porten — det kallet kan bare komme fra den synkrone sonden.

    MUTASJONEN SOM DREPER DENNE: fjern `or puls.sonder()` fra
    `except kjoring.Kjoringsfeil` — da blir `grunn`
    `kjoring_avbrutt:kandidatlagring_feilet`."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)

    pulser: list = []
    ekte = controller._Heartbeat

    class _Fanget(ekte):                            # type: ignore[misc,valid-type]
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            pulser.append(self)

    monkeypatch.setattr(controller, "_Heartbeat", _Fanget)

    k = _Stubklient(forny=lambda: _Svar(409, {"feil": "lease_utlopt"}))
    k.kandidatdatastatus = 409          # døren: `kandidatdata_avvist`
    k.kandidatdata_ok_forst = 1         # ett skriv sto alt i lagrene
    res = _kjor(k)

    assert res["utfall"] == "avbrutt", res
    assert res["grunn"] == "lease_tapt", res
    assert res["lease_tapt"] == "lease_utlopt", res
    assert k.kvitteringer and \
        k.kvitteringer[0]["feilkode"] == "lease_tapt", k.kvitteringer
    # Sonden er den ENESTE mulige kilden til dette kallet: med 240 s
    # intervall og en kjøring på millisekunder pulset tråden aldri.
    assert "/v1/oppdrag/forny" in k.stier, k.stier
    assert len(k.kandidatdokumenter) == 1, k.kandidatdokumenter
    assert "/v1/artefakt" not in k.stier, \
        "en kjøring uten gyldig lease promoterte likevel"


def test_173_avvist_skriv_med_levende_lease_er_fortsatt_lagringsfeil(
        monkeypatch):
    """Baksiden av sonden: en 409 fra døren er IKKE i seg selv et
    autoritetstap.

    `kandidatdata_konflikt` (hashavvik, dobbeltskriv) mens leasen lever
    er en ekte lagringsfeil. Svarer `/v1/oppdrag/forny` 2xx, eier vi
    fortsatt oppdraget, og utfallet beholder sitt eget ord — ellers ville
    sonden døpt om hver eneste dør-avvisning til `lease_tapt`, og drift
    mistet konflikten.

    MUTASJONEN SOM DREPER DENNE: la `sonder` melde tap på alt som ikke er
    2xx, eller la den returnere en sannhetsverdi uavhengig av svaret."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)
    k = _Stubklient(forny=lambda: _Svar(200, {}))
    k.kandidatdatastatus = 409          # `kandidatdata_konflikt`
    k.kandidatdata_ok_forst = 1
    res = _kjor(k)

    assert res["grunn"] == "kjoring_avbrutt:kandidatlagring_feilet", res
    assert k.kvitteringer and \
        k.kvitteringer[0]["feilkode"] == "kjoring_avbrutt", k.kvitteringer
    assert "lease_tapt" not in res, res
    # Sonden ble faktisk spurt — den negative porten måler svaret dens,
    # ikke at den uteble.
    assert "/v1/oppdrag/forny" in k.stier, k.stier


def test_173_sinkfeil_uten_leasetap_beholder_sitt_eget_ord(monkeypatch):
    """Baksiden av porten over: `puls.tapt`-grenen skal treffe SMALT.

    En ekte lagringsfeil mens leasen lever er fortsatt
    `kjoring_avbrutt:kandidatlagring_feilet` — ellers ville fiksen
    døpt om hver eneste 5xx fra kandidatlagrene til et autoritetstap,
    og drift ville mistet lagringsfeilen helt.

    MUTASJONEN SOM DREPER DENNE: la `puls.tapt`-grenen slå på koden
    alene, uten å spørre pulsen."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)
    k = _Stubklient()
    k.kandidatdatastatus = 500
    res = _kjor(k)
    assert res["grunn"] == "kjoring_avbrutt:kandidatlagring_feilet", res
    assert k.kvitteringer and \
        k.kvitteringer[0]["feilkode"] == "kjoring_avbrutt", k.kvitteringer
    assert "lease_tapt" not in res, res


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


def test_manglende_opplastingskapabilitet_stopper_for_bunten():
    """m56s port: en levering vi VET er umulig skal ikke koste
    persondata. Gir claim-API-et bevisst ingen `opplasting` — fordi
    artefakttypen mangler, er tvetydig eller er filtrert bort for
    deploymenten — kan rapporten aldri leveres, og bunten skal da ikke
    hentes ut av lageret i det hele tatt."""
    for uten in (None, {}):
        modell = _Modell()
        k = _Stubklient(opplasting=uten)
        res = _kjor(k, modell=modell)
        assert modell.sett == [], uten
        assert k.stier == ["/v1/oppdrag/claim", "/v1/oppdrag/kvittering"]
        assert res["utfall"] == "avbrutt", res
        assert res["grunn"] == "ingen_kapabilitet", res
        assert k.kvitteringer[0]["feilkode"] == \
            "ingen_opplastingskapabilitet"


def test_kvitteringen_gjentas_ved_forbigaaende_feil(monkeypatch):
    """m56s port: idempotensen er bygget for retry, og skal BRUKES.

    Ingen kaller retryer `kjor_en`, returverdien bærer hverken
    `kvittering_jti`, eiergenerasjonen eller den signerte kroppen som
    skal til for å bygge forespørselen på nytt, og leasen sperrer et
    ferskt claim frem til utførelsesfristen. Ett tapt svar koster altså
    hele oppdraget — her inkludert en ferdig evaluering av opptil 5000
    søknader."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)

    class _FeilerForst(_Stubklient):
        def __init__(self, feil_antall, **kw):
            super().__init__(200, **kw)
            self.feil_igjen = feil_antall

        def _kvitteringssvar(self, sendt):
            if self.feil_igjen:
                self.feil_igjen -= 1
                return _Svar(503, {})
            return super()._kvitteringssvar(sendt)

    k = _FeilerForst(2)
    res = _kjor(k)
    assert res["utfall"] == "utfort", res
    assert len(k.kvitteringer) == 3, k.kvitteringer
    # KROPPEN ER IDENTISK hver gang. Det er hele grunnen til at retryen
    # er trygg: samme `kvittering_jti`, samme signerte bytes, så
    # plattformen ser én gjentatt kvittering og ikke tre forskjellige.
    assert k.kvitteringer[0] == k.kvitteringer[1] == k.kvitteringer[2]

    class _Mister(_Stubklient):
        def __init__(self, mist_antall, **kw):
            super().__init__(200, **kw)
            self.mist_igjen = mist_antall
            self.forsok = 0

        def post(self, sti, json=None, headers=None):
            if sti == "/v1/oppdrag/kvittering":
                self.forsok += 1
                if self.mist_igjen:
                    self.mist_igjen -= 1
                    raise ConnectionError("svaret kom aldri")
            return super().post(sti, json=json, headers=headers)

    m = _Mister(1)
    assert _kjor(m)["utfall"] == "utfort"
    assert m.forsok == 2, m.forsok

    # Gir ALLE forsøkene tapt svar, er utfallet `ukvittert` med en ærlig
    # `kvittering_status: 0` — ikke et unntak ut av kjøreløkka som
    # etterlater oppdraget claimet uten et ord til plattformen.
    m = _Mister(controller.LEVERINGSFORSOK)
    res = _kjor(m)
    assert res["utfall"] == "ukvittert", res
    assert res["kvittering_status"] == 0, res
    assert m.forsok == controller.LEVERINGSFORSOK, m.forsok

    # 4xx retryes ALDRI: 409 er plattformens overlagte avvisning
    # (fencing, hashavvik), ikke en forbigående feil, og å gjenta den er
    # å mase om et svar som ikke endrer seg.
    for status in (400, 409, 422):
        k = _Stubklient(status)
        res = _kjor(k)
        assert res["utfall"] == "ukvittert", (status, res)
        assert len(k.kvitteringer) == 1, (status, k.kvitteringer)


def test_gjentatt_kvittering_leses_som_det_den_forrige_gjorde():
    """m56s port: `idempotent` er en dokumentert SUKSESSVEI — en utfører
    som mistet svaret skal kunne sende NØYAKTIG den samme kvitteringen på
    nytt, og plattformen svarer 200 `idempotent`.

    Men ordet betyr to ting (`_idempotent_svar` i `api.app`): en
    gjentakelse av en SEN kvittering treffer samme gren, og der står
    oppdraget bevisst ufullført. Begge sidene holdes fast her — også på
    feilveien, som leser den samme regelen gjennom `_feilutfall`."""
    assert _kjor(_Stubklient(200, kvitteringskropp={
        "status": "idempotent", "oppdrag_id": 1}))["utfall"] == "utfort"
    assert _kjor(_Stubklient(200, kvitteringskropp={
        "status": "idempotent_uten_statusendring",
        "oppdrag_id": 1}))["utfall"] == "ukvittert"

    assert _kjor(_Stubklient(200, buntstatus=404, kvitteringskropp={
        "status": "idempotent", "oppdrag_id": 1}))["utfall"] == "avbrutt"
    assert _kjor(_Stubklient(200, buntstatus=404, kvitteringskropp={
        "status": "idempotent_uten_statusendring",
        "oppdrag_id": 1}))["utfall"] == "ukvittert"


def test_avvist_opplasting_gir_feilkvittering(monkeypatch):
    """m56s port: plattformen avviste artefaktet (400/413 på taket, 409
    på fencing, 5xx). Plattformen skal da FÅ VITE det — taushet lar
    oppdraget stå claimet til fristen — og 5xx skal gjentas med samme
    kropp mens 4xx sendes én gang."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)
    for status in (400, 409, 413, 500):
        k = _Stubklient(200, opplastingsstatus=status)
        res = _kjor(k)
        assert res["utfall"] == "avbrutt", res
        assert res["opplasting_status"] == status
        assert res["kvittert"] is True
        assert len(k.kvitteringer) == 1
        assert k.kvitteringer[0]["resultat"] == "feilet"
        assert k.kvitteringer[0]["feilkode"] == "opplasting_avvist"
        # Aldri et artefakt-id: det finnes ikke noe artefakt å vise til.
        assert "artefakt_id" not in res
        forsok = len([s for s in k.stier if s == "/v1/artefakt"])
        assert forsok == (controller.LEVERINGSFORSOK
                          if status == 500 else 1), (status, forsok)


def test_opplastingen_gjentas_som_kvitteringen(monkeypatch):
    """m56s `test_opplastingen_gjentas_som_kvitteringen`, speilet (Cursor
    P2, runde 2): m57 målte bare ANTALL forsøk ved 500. Suksess etter et
    forbigående avslag, og tapt transport på opplastingen, var udekket —
    og det er nettopp der en ferdig evaluering kan kastes ETT steg før
    kvitteringen.

    Endepunktet er idempotent på `kapabilitet_jti` og den kanoniske
    rapporten, nettopp for at en utfører som mistet svaret skal kunne
    spørre igjen. Kroppen er derfor IDENTISK hvert forsøk — det er hele
    grunnen til at plattformen kjenner den igjen framfor å lage et nytt
    artefakt av samme persondatabunt.

    Kontroll: bytt `lever("/v1/artefakt", ...)` mot et enkelt
    `klient.post(...)`, så blir hver gren under rød."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)

    class _MisterOpplasting(_Stubklient):
        """Opplastingen committer hos plattformen, men svaret tapes."""

        def __init__(self, mist_antall, **kw):
            super().__init__(200, **kw)
            self.mist_igjen = mist_antall
            self.opplastinger = []

        def post(self, sti, json=None, headers=None):
            if sti == "/v1/artefakt":
                self.opplastinger.append(json)
                if self.mist_igjen:
                    self.mist_igjen -= 1
                    raise ConnectionError("svaret kom aldri")
            return super().post(sti, json=json, headers=headers)

    # 1) Et tapt svar gjentas, og lykkes det, er oppdraget UTFØRT — ikke
    # et unntak ut av kjøreløkka med bunten alt evaluert.
    k = _MisterOpplasting(2)
    res = _kjor(k)
    assert res["utfall"] == "utfort", res
    assert len(k.opplastinger) == 3, k.opplastinger
    assert k.opplastinger[0] == k.opplastinger[1] == k.opplastinger[2]

    # 2) Gir ALLE forsøkene tapt svar, blir det en ærlig feilkvittering
    # med status 0: plattformen får vite at kjøringen ikke ble avsluttet,
    # framfor at oppdraget står claimet og tyst til fristen.
    k = _MisterOpplasting(controller.LEVERINGSFORSOK)
    res = _kjor(k)
    assert res["utfall"] == "avbrutt", res
    assert res["opplasting_status"] == 0, res
    assert len(k.opplastinger) == controller.LEVERINGSFORSOK
    assert k.kvitteringer[0]["feilkode"] == "opplasting_avvist"

    # 3) Et forbigående 503 gjentas også, og lykkes det, er rapporten
    # LEVERT — ikke kvittert `feilet` for en bunt som alt er evaluert.
    class _FeilerForstOpplasting(_Stubklient):
        def __init__(self, feil_antall, **kw):
            super().__init__(200, **kw)
            self.feil_igjen = feil_antall
            self.opplastinger = 0

        def post(self, sti, json=None, headers=None):
            if sti == "/v1/artefakt":
                self.opplastinger += 1
                if self.feil_igjen:
                    self.feil_igjen -= 1
                    return _Svar(503, {})
            return super().post(sti, json=json, headers=headers)

    k = _FeilerForstOpplasting(2)
    res = _kjor(k)
    assert res["utfall"] == "utfort", res
    assert k.opplastinger == 3, k.opplastinger


def test_opplastingsretryen_stanser_ikke_ved_nominelt_utlop(monkeypatch):
    """m56s port, speilet: retryen skal IKKE gi opp på nøyaktig det
    kappløpet plattformen har en gjenspillingsvei for.

    Mister utføreren svaret på en opplasting som ALLEREDE er committet,
    og passerer `opplasting.utloper` imens, sier databasen at artefaktet
    kan hentes inn igjen: `innlos_artefaktkapabilitet` (035) tar
    `k.status = 'brukt' OR k.utloper > now()`, og `lagre_artefakt_staged`
    (017) gir det opprinnelige `artefakt_id` for samme hash. Uten
    `gjenlosbar_etter_utlop` ville controlleren kvittert
    `opplasting_avvist` — «rapporten kom ikke frem» — for et artefakt som
    lå staget på plattformen, og hele evalueringen av persondatabunten
    måtte gjøres om.

    Asymmetrien er ekte og går bare den ene veien:
    `innlos_kvitteringskapabilitet` (035) krever `utloper > now()` uten
    unntak, så KVITTERINGEN stanser fortsatt ved sitt eget utløp.

    AVVIK FRA m56s FORM, med vilje: m56 flytter klokka via en
    `controller.datetime`-patch. m57 importerer `datetime` inne i
    `_vindu_apent` og `_evalueringsfrist`, så den patchen ville ikke
    bitt. Vinduet lukkes derfor på predikatet selv — det er nøyaktig det
    `lever` spør, og porten måler dermed samme gren. Utløpet må uansett
    passere UNDERVEIS: et claim hvis kapabilitet alt er utløpt når det
    leses, stoppes av `_evalueringsfrist` før bunten hentes.

    Kontroll: fjern `gjenlosbar_etter_utlop=True` fra `lever`-kallet på
    `/v1/artefakt`, så blir gren 1 rød; fjern `gjenlosbar_etter_utlop`
    fra guarden i `lever`, så blir gren 3 rød."""
    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "_sov", lambda s: None)
    apent = {"na": True}
    monkeypatch.setattr(controller, "_vindu_apent",
                        lambda raa: apent["na"])

    class _MisterForstOpplasting(_Stubklient):
        """Første opplasting committer; svaret tapes, og imens passerer
        kapabilitetens utløp."""

        def __init__(self, **kw):
            super().__init__(200, **kw)
            self.opplastinger = []

        def post(self, sti, json=None, headers=None):
            if sti == "/v1/artefakt":
                self.opplastinger.append(json)
                if len(self.opplastinger) == 1:
                    apent["na"] = False        # utløpet passerte imens
                    raise ConnectionError("svaret kom aldri")
            return super().post(sti, json=json, headers=headers)

    # 1) Utløpet passerte mens svaret var borte: retryen fortsetter
    # likevel, plattformen gjenspiller det staged artefaktet, og
    # oppdraget er UTFØRT.
    k = _MisterForstOpplasting()
    res = _kjor(k)
    assert res["utfall"] == "utfort", res
    assert res["artefakt_id"] == "a-1", res
    assert len(k.opplastinger) == 2, k.opplastinger
    # Identisk kropp — grunnen til at gjenspillingen gir SAMME artefakt.
    assert k.opplastinger[0] == k.opplastinger[1]

    # 2) Var kapabiliteten IKKE forbrukt, finner innløsningen ingen rad
    # og endepunktet svarer `kapabilitet_ugyldig` (401). Det er en 4xx, så
    # løkka bryter på første forsøk: prisen for å prøve forbi utløpet er
    # ÉN forespørsel mot vår egen plattform.
    apent["na"] = True
    k = _Stubklient(200, opplastingsstatus=401)
    res = _kjor(k)
    assert res["utfall"] == "avbrutt", res
    assert res["opplasting_status"] == 401, res
    assert len([s for s in k.stier if s == "/v1/artefakt"]) == 1, k.stier
    assert k.kvitteringer[0]["feilkode"] == "opplasting_avvist"

    # 3) KVITTERINGEN har ingen slik gjenspillingsvei og stanser fortsatt
    # ved sitt eget utløp. Flagget gjelder KUN opplastingen.
    class _MisterKvittering(_Stubklient):
        def __init__(self, **kw):
            super().__init__(200, **kw)
            self.forsok = 0

        def post(self, sti, json=None, headers=None):
            if sti == "/v1/oppdrag/kvittering":
                self.forsok += 1
                apent["na"] = False
                raise ConnectionError("svaret kom aldri")
            return super().post(sti, json=json, headers=headers)

    apent["na"] = True
    k = _MisterKvittering()
    res = _kjor(k)
    assert res["utfall"] == "ukvittert", res
    assert k.forsok == 1, k.forsok


def test_uleselig_opplastingssvar_er_ingen_kvitteringsgrunn():
    """2xx med en kropp vi ikke kan lese er ingen kvitteringsgrunn: uten
    `artefakt_id` og hashen finnes det ikke en `utfort`-kvittering å
    signere. Den nakne feilen ville ellers gått ut av kjøreløkka."""
    for kropp in (None, {"artefakt_id": "a-1"}):
        k = _Stubklient(200, artefaktkropp=kropp)
        res = _kjor(k)
        assert res["utfall"] == "avbrutt", (kropp, res)
        assert res["grunn"].startswith("opplasting_uleselig:"), res
        assert k.kvitteringer[0]["feilkode"] == "opplasting_avvist"


def test_vinduet_leses_per_forsok():
    """`_vindu_apent` er predikatet retryen stanser på. Det er UNDERVEIS
    i en lang evaluering vinduet lukker seg — et claim som alt er utløpt
    når det leses, stoppes av `_evalueringsfrist` lenge før — så
    predikatet prøves direkte."""
    from datetime import datetime, timedelta, timezone

    from modules.m57_ats import controller

    naa = datetime.now(timezone.utc)
    assert controller._vindu_apent(
        (naa + timedelta(seconds=60)).isoformat())
    assert not controller._vindu_apent(
        (naa - timedelta(seconds=1)).isoformat())
    # En naiv ISO-form leses som UTC — plattformens tider ER UTC — i
    # stedet for å felle sammenligningen med TypeError.
    assert controller._vindu_apent("2099-01-01T00:00:00")
    for uleselig in (None, "i morgen", 42, {}):
        assert not controller._vindu_apent(uleselig), uleselig


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


def test_oppstarten_nekter_for_claim_lokka():
    """m56s `wcag_audit_arbeider`-port, speilet (Cursor P2, runde 2):
    m57 validerte nøkkel og biasmålinger i riktig rekkefølge i KODEN,
    men ingen port holdt rekkefølgen fast.

    Den er hele poenget. Blir en av sjekkene flyttet ned i løkka — eller
    faller bort — leaser arbeideren oppdrag den ikke kan avslutte:
    kvitteringer ingen kan verifisere (nøkkelen), eller en evaluering
    kjørt på en modell uten biasmåling knyttet til sin digest. Prisen
    betales da ett CLAIMET oppdrag om gangen, med persondata alt utlevert
    — i stedet for én gang, før noe hentes (port 17-økonomien).

    Statisk, som m56s: en importtest ville krevd hele driftsmiljøet."""
    from pathlib import Path

    kilde = (Path(__file__).resolve().parents[2]
             / "drift/m57_arbeider.py").read_text(encoding="utf-8")
    lokka = kilde.index("while True:")
    # Alle tre nektene skjer FØR løkka, og de nekter med exit-kode.
    assert "oppstart_nektet" in kilde
    assert kilde.index("nokkelfeil(nk)") < lokka
    assert kilde.index("krev_biasmaaling(digest, biasmaalinger)") < lokka
    assert kilde.index("oppstart_nektet") < lokka
    # ... og nekten er en RETUR, ikke en advarsel som lar løkka starte.
    assert kilde.count("return 2") >= 3, kilde.count("return 2")


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


def test_fersk_kapabilitet_brukes_faktisk_i_opplastingen(monkeypatch):
    """Cursor P2, runde 2: at `_Heartbeat` PLUKKER OPP en fersk
    kapabilitet var bevist isolert (`test_heartbeatet_fornyer_og_bytter_-
    kapabilitet`), men ingen port fulgte den helt ut på `/v1/artefakt`.

    Det er det siste steget som betyr noe. Claimets opprinnelige
    `opplasting.jti` er utstedt med `min(igjen, UTSTEDT_AUTORITET_S)` og
    kan være DØD lenge før en 240-minutters evaluering er ferdig — det er
    hele grunnen til at fornyelsen re-utsteder den. Brukte opplastingen
    likevel claimets jti, ville en lang, ellers vellykket evaluering blitt
    avvist på kapabiliteten, og bunten måttet evalueres om igjen.

    Kontroll: fjern `opplasting = puls.fersk_opplasting` i `kjor_en`, så
    står `kap` igjen i kroppen og porten feller."""
    import threading

    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.01)
    slo = threading.Event()

    def forny():
        slo.set()
        return _Svar(200, {"opplasting": {
            "jti": "fersk-jti",
            "utloper": "2099-01-01T00:00:00+00:00"}})

    class _Registrerer(_Stubklient):
        def __init__(self, **kw):
            super().__init__(200, **kw)
            self.opplastinger = []

        def post(self, sti, json=None, headers=None):
            if sti == "/v1/artefakt":
                self.opplastinger.append(json)
            return super().post(sti, json=json, headers=headers)

    k = _Registrerer(forny=forny)
    res = _kjor(k, modell=_VenterModell(slo))
    assert res["utfall"] == "utfort", res
    assert k.opplastinger, "artefaktet ble aldri lastet opp"
    assert k.opplastinger[0]["kapabilitet_jti"] == "fersk-jti", \
        k.opplastinger
    # ... og claimets egen — den som kan være død — sto uendret i
    # stubben, så porten sammenligner faktisk to FORSKJELLIGE verdier.
    assert k.opplasting["jti"] == "kap"


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


def test_tom_bunt_er_uhentbar_og_naar_aldri_modellen():
    """Cursor P2-2, runde 3: resolveren kan svare 200 med TOM kropp —
    et 0-byte-svar, en trunkert overføring, et objekt som forsvant
    mellom katalogoppslaget og utleveringen. Grenen finnes i
    `kjor_en` (`raa` er falsy), men ingen port sto på den, og 200-veien
    er nettopp den som fører VIDERE: uten porten skrives null byte til
    `bunt.zip` og `kjor_bunt` møter en «zip» uten sentralkatalog.

    Grunnen skiller seg fra `bunt_uhentbar` med vilje — status var 200,
    så driften skal se at det var INNHOLDET som manglet — mens
    feilkoden til plattformen er den samme: bunten kom ikke frem.

    Kontroll: fjern `if not raa`-porten i `kjor_en`, så faller denne på
    `kjoring_avbrutt` i stedet, med bunten alt skrevet til disk."""
    class _TomBunt(_Stubklient):
        def post(self, sti, json=None, headers=None):
            if sti.startswith("/v1/inndata/hent-for-oppdrag/"):
                self.stier.append(sti)
                return _Svar(200, content=b"")
            return super().post(sti, json=json, headers=headers)

    k, modell = _TomBunt(), _Modell()
    res = _kjor(k, modell=modell)
    assert res["utfall"] == "avbrutt", res
    assert res["grunn"] == "bunt_tom", res
    assert k.kvitteringer[0]["feilkode"] == "bunt_uhentbar", k.kvitteringer
    # Persondata-økonomien: ingenting ble evaluert, ingenting lastet opp.
    assert modell.sett == [], modell.sett
    assert "/v1/artefakt" not in k.stier


def test_kjoringsfeil_kvitteres_med_sin_egen_kode(monkeypatch):
    """Cursor P2-2, runde 3: `Kjoringsfeil` er kjøringens ENE feilutfall
    (`kjoring.py`: «eller Kjoringsfeil, aldri noe imellom»), og
    controllerens gren for den var udekket.

    To ting skilles her, og begge er kontrakt. Plattformen får den
    grovkornede `kjoring_avbrutt` — den er et kvitteringsfelt med lukket
    verdisett — mens driften får `kjoring_avbrutt:<kode>` i utfallet, med
    kjøringens egen kode intakt. Uten den ville `manifest_feilformet`
    (kundens bunt) og `modellfeil` (vår modellserver) sett like ut i
    driftsloggen.

    Kontroll: la grenen kaste videre, så blir oppdraget stående claimet
    uten kvittering i det hele tatt — taushet er det §10 forbyr."""
    from modules.m57_ats import controller, kjoring

    def eksploder(*a, **kw):
        raise kjoring.Kjoringsfeil("modellfeil", {"filer_lest": 1})

    monkeypatch.setattr(kjoring, "kjor_bunt", eksploder)
    k = _Stubklient()
    res = controller.kjor_en(k, "tk", _Modell(), _Uttrekker(),
                             _MAALINGER, lambda x: x)
    assert res["utfall"] == "avbrutt", res
    # Kjøringens egen kode overlever helt ut i utfallet …
    assert res["grunn"] == "kjoring_avbrutt:modellfeil", res
    # … mens plattformen får det lukkede ordet.
    assert k.kvitteringer[0]["feilkode"] == "kjoring_avbrutt", k.kvitteringer
    assert "/v1/artefakt" not in k.stier


def test_rapport_som_bryter_skjemaet_lastes_aldri_opp(monkeypatch):
    """Cursor P2-2, runde 3: skjemavalideringen er den siste porten før
    rapporten forlater containeren, og grenen for et BRUDD var udekket.

    Porten er ikke seremoni: artefaktet promoteres mot
    `artefakttype_register`s `skjema_hash`, så en rapport som bryter
    formen ville uansett blitt avvist — men først ETTER at den var
    kryptert, lastet opp og staged hos plattformen. Her stoppes den før
    `/v1/artefakt` overhodet kalles, og oppdraget kvitteres feilet med
    en gang.

    Validatoren kjøres ekte: `bygg` byttes ut, ikke porten. En test som
    hadde patchet selve validatoren ville bevist at controlleren fanger
    `ValidationError`, ikke at skjemaet faktisk måler rapporten.

    Kontroll: fjern `except jsonschema.ValidationError`-grenen, så slår
    unntaket ut av `kjor_en` og oppdraget står claimet uten kvittering."""
    from modules.m57_ats import controller, rapportskjema

    monkeypatch.setattr(rapportskjema, "bygg",
                        lambda *a, **kw: {"rangering": "ikke en liste"})
    k = _Stubklient()
    res = controller.kjor_en(k, "tk", _Modell(), _Uttrekker(),
                             _MAALINGER, lambda x: x)
    assert res["utfall"] == "avbrutt", res
    assert res["grunn"] == "rapport_ugyldig", res
    assert k.kvitteringer[0]["feilkode"] == "kjoring_avbrutt", k.kvitteringer
    assert "/v1/artefakt" not in k.stier


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


def test_stum_fornyelse_taper_leasen_og_stopper_leveransen(monkeypatch):
    """Cursor P2, runde 2: TAUSHET ER OGSÅ TAP. En 4xx sier «oppdraget er
    ikke ditt»; en 5xx eller et tapt svar sier ingenting — men leasen
    løper ut like fullt. Pulsen fortsatte tidligere i det uendelige på
    5xx/transport, så en lang evaluering kunne kjøre ferdig på en DØD
    lease: hele bunten parset, modellen kalt, artefaktet staget — og
    autoritetstapet oppdaget først på opplastingen eller kvitteringen.

    Plattformen fencer riktignok kvitteringen, så ingenting blir GALT.
    Kostnaden er poenget: persondata og regnekraft brukt på et oppdrag
    plattformen alt kunne ha gitt til noen andre — samme regnestykke som
    `_payloadbrudd`, kapabilitetssjekken og fristsjekken stopper FØR
    arbeidet for.

    Kontroll: fjern `stumme`-telleren i `_lopp`, så laster denne opp."""
    import threading

    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.01)
    slo = threading.Event()
    forsok = []

    def forny():
        forsok.append(1)
        if len(forsok) >= controller.FORNY_TAPT_ETTER:
            slo.set()           # slipper evalueringen fram til utgangen
        return _Svar(503, {})

    k = _Stubklient(forny=forny)
    res = _kjor(k, modell=_VenterModell(slo))
    assert res["utfall"] == "avbrutt", res
    assert res["grunn"] == "lease_tapt", res
    #: Koden skiller de to tapsveiene: plattformen SA fra (4xx) versus
    #: plattformen svarte ikke (5xx/transport). Driftsloggen trenger
    #: forskjellen — den ene er et eierskifte, den andre en nedetid.
    assert res["lease_tapt"] == "forny_utilgjengelig", res
    assert k.kvitteringer[0]["feilkode"] == "lease_tapt"
    assert "/v1/artefakt" not in k.stier
    # Pulsen ga seg da terskelen var nådd, den fortsatte ikke å banke.
    assert len(forsok) == controller.FORNY_TAPT_ETTER, forsok


def test_stumme_pulser_nullstilles_av_en_bekreftet_fornyelse(monkeypatch):
    """Terskelen teller PÅFØLGENDE taushet, ikke taushet totalt.

    Uten nullstillingen ville porten over vært en ny skade: to
    forbigående blip timer fra hverandre — en 503 under en deploy, et
    tapt svar under en nettverkshikke — ville avbrutt en evaluering som
    hele tiden hadde gyldig lease. Terskelen er avledet av forholdet
    mellom `FORNY_LEASE_S` og `FORNY_INTERVALL_S` nettopp fordi den skal
    treffe der leasen FAKTISK er brukt opp.

    Kontroll: fjern `stumme = 0` på 2xx-grenen, så feller denne."""
    import threading

    from modules.m57_ats import controller

    assert controller.FORNY_TAPT_ETTER >= 2, (
        "med terskel 1 finnes ingen nullstilling å bevise — leasen tåler"
        " da ikke én eneste stum puls, og det er et annet vedtak enn det"
        " denne porten dekker")
    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.01)
    #: Én stum puls FÆRRE enn terskelen, så en bekreftet fornyelse —
    #: gjentatt. Både 5xx og tapt transport er med: begge er «stum».
    runde = ["transport"] + [503] * (controller.FORNY_TAPT_ETTER - 2) + [200]
    plan = runde * 3
    sett = []
    ferdig = threading.Event()

    class _K:
        def post(self, sti, json=None, headers=None):
            assert sti == "/v1/oppdrag/forny", sti
            steg = plan[len(sett)] if len(sett) < len(plan) else 200
            sett.append(steg)
            if len(sett) >= len(plan):
                ferdig.set()
            if steg == "transport":
                raise ConnectionError("intet svar")
            return _Svar(steg, {})

    with controller._Heartbeat(_K(), {}, {"oppdrag_id": 1,
                                          "owner_claim_id": "c" * 22,
                                          "owner_generation": 1}) as puls:
        assert ferdig.wait(10), f"pulsen kom aldri gjennom planen: {sett}"
    assert puls.tapt is None, (puls.tapt, sett)


def _om(sekunder):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc)
            + timedelta(seconds=sekunder)).isoformat()


def test_serverens_horisont_slaar_den_avledede_telleren(monkeypatch):
    """Cursor P2, runde 5: telleren felte leasen FØR den var brukt opp.

    `FORNY_TAPT_ETTER` er 600 // 240 = 2, så to stumme pulser (480 s)
    erklærte tap mens grant-vinduet ennå hadde 120 s igjen. Verre er
    forutsetningen under tallet: 063 skriver `owner_lease_utloper` med
    `greatest(gammel, nå + lease_s)`, og på et claim der 037 alt strakk
    leasen til `utforelsesfrist`, er fornyelsen en no-op og horisonten
    ligger TIMER fram — akkurat tilfellet #165 finnes for. Et avledet
    tall kan ikke vite det; serverens eget felt kan.

    Porten: én bekreftet fornyelse som oppgir en horisont langt fram,
    deretter mer taushet enn den gamle terskelen tålte. Autoriteten
    lever, evalueringen får kjøre ferdig, og oppdraget blir UTFØRT.

    Kontroll: sett `_horisont` tilbake til en teller — altså la
    `_utlopt` returnere `stumme >= FORNY_TAPT_ETTER` uansett — så
    kvitterer denne `lease_tapt` i stedet."""
    import threading

    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.01)
    slo = threading.Event()
    forsok = []

    def forny():
        forsok.append(1)
        if len(forsok) == 1:
            #: Bekreftet fornyelse: horisonten er serverens, og den
            #: rekker langt utover det telleren ville tålt.
            return _Svar(200, {"owner_lease_utloper": _om(3600)})
        #: Godt forbi den gamle terskelen — leasen lever like fullt.
        if len(forsok) >= controller.FORNY_TAPT_ETTER + 3:
            slo.set()           # slipper evalueringen fram til utgangen
        return _Svar(503, {})

    k = _Stubklient(forny=forny)
    res = _kjor(k, modell=_VenterModell(slo))
    assert res["utfall"] == "utfort", res
    assert res.get("lease_tapt") is None, res
    assert "/v1/artefakt" in k.stier


def test_passert_horisont_taper_leasen_paa_forste_stumme_puls(monkeypatch):
    """Den andre halvdelen av samme dom: horisonten skal ikke bare være
    romsligere enn telleren, den skal være SANN begge veier.

    Oppgir serveren en horisont som alt er passert, er det ingen lease
    igjen å redde — og da venter pulsen ikke på en teller som ennå ikke
    har talt ferdig. Uten dette ville fiksen over vært en ren
    oppmykning: fail-open der porten skal felle.

    Kontroll: bytt `datetime.now(timezone.utc) >= self._horisont` mot
    `stumme >= FORNY_TAPT_ETTER`, så trengs det flere pulser og denne
    feller på antallet."""
    import threading

    from modules.m57_ats import controller

    assert controller.FORNY_TAPT_ETTER >= 2, (
        "med terskel 1 skiller ikke denne porten horisonten fra"
        " telleren — begge ville felt på første stumme puls")
    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.01)
    stoppet = threading.Event()
    forsok = []

    def forny():
        forsok.append(1)
        if len(forsok) == 1:
            return _Svar(200, {"owner_lease_utloper": _om(-1)})
        stoppet.set()
        return _Svar(503, {})

    with controller._Heartbeat(_Stubklient(forny=forny), {},
                               {"oppdrag_id": 1,
                                "owner_claim_id": "c" * 22,
                                "owner_generation": 1}) as puls:
        assert stoppet.wait(10), "den stumme pulsen kom aldri"
    assert puls.tapt == "forny_utilgjengelig", (puls.tapt, forsok)
    #: ÉN stum puls holdt — telleren ville krevd `FORNY_TAPT_ETTER`.
    assert len(forsok) == 2, forsok


def test_uleselig_horisont_faller_tilbake_paa_telleren(monkeypatch):
    """En `owner_lease_utloper` vi ikke kan lese — feilformet, eller uten
    tidssone — er ingen horisont, og skal ikke late som den er det.

    Fail-closed på samme måte som `_evalueringsfrist`: uten et lesbart
    tidspunkt gjelder den avledede telleren, ikke et gjettet vindu."""
    import threading

    from modules.m57_ats import controller

    monkeypatch.setattr(controller, "FORNY_INTERVALL_S", 0.01)
    stoppet = threading.Event()
    forsok = []

    def forny():
        forsok.append(1)
        if len(forsok) == 1:
            #: Uten UTC-offset: plattformen sender alltid sone, så dette
            #: er et brudd — ikke noe å tolke.
            return _Svar(200, {"owner_lease_utloper": "2099-01-01T00:00:00"})
        if len(forsok) >= controller.FORNY_TAPT_ETTER + 1:
            stoppet.set()
        return _Svar(503, {})

    with controller._Heartbeat(_Stubklient(forny=forny), {},
                               {"oppdrag_id": 1,
                                "owner_claim_id": "c" * 22,
                                "owner_generation": 1}) as puls:
        assert stoppet.wait(10), "pulsen kom aldri gjennom planen"
    assert puls.tapt == "forny_utilgjengelig", (puls.tapt, forsok)
    #: Telleren startet på nytt etter den bekreftede fornyelsen.
    assert len(forsok) == 1 + controller.FORNY_TAPT_ETTER, forsok
