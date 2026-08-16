"""PR-013 CP6 — HTTP-portene + utkast-livssyklusen.

Behandlingslogikken er bevist på funksjonsnivå (test_pr013_policyadmin_flyt);
her sjekkes at endepunktene er koblet inn, at HTTP-portene (form/auth/idempotens)
stopper det de skal FØR noe røres, og at utkast-CRUD-funksjonene (opprett →
rediger → valider) håndhever optimistisk lås + skjemavalidering + frysing.
"""
import copy
import secrets
from pathlib import Path

import pytest
import yaml

from api import policyadmin

from .test_api import DSN, MIGRATOR_DSN, app, klient, miljo  # noqa: F401

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-phttp-" + secrets.token_hex(3)

_MAL = (Path(__file__).resolve().parents[3]
        / "policies" / "bransjemal-handverk-bygg.yaml")


def _gyldig(pid: str | None = None) -> dict:
    """En kjent skjemagyldig policy (bransjemalen som CI allerede validerer).

    `pid` gjør malen til et UTKAST slik editoren gjør det: identiteten bindes
    til utkastets policy_id (migrasjon 022), og statusen settes til den
    aktiveringen skriver (023). Malen bærer sin egen id og `status: utkast` —
    riktig for en mal, men et utkast som beholder dem kan ikke valideres.
    """
    pol = yaml.safe_load(_MAL.read_text(encoding="utf-8"))
    if pid is not None:
        pol["meta"]["policy_id"] = pid
        pol["meta"]["status"] = "produksjon"
    return pol


def _rt():
    from db.pg import koble
    return koble(DSN)


# Idempotency-Key er nå PÅKREVD på alle skriveveier (Codex P1 R3); disse
# wrapperne injiserer en fersk nøkkel per kall så funksjonstestene forblir korte.
def _opprett(rt, **kw):
    k = secrets.token_hex(8)
    return policyadmin.opprett_utkast(rt, idempotency_key=k, input_hash=k, **kw)


def _rediger(rt, **kw):
    k = secrets.token_hex(8)
    return policyadmin.rediger_utkast(rt, idempotency_key=k, input_hash=k, **kw)


def _valider(rt, **kw):
    k = secrets.token_hex(8)
    return policyadmin.valider_utkast(rt, idempotency_key=k, input_hash=k, **kw)


# ---- HTTP-porter (uten sesjon: gates skal svare før noe røres) ------------

@pg
def test_opprett_utkast_uautentisert_avvises(klient):
    r = klient.post("/v1/policyutkast",
                    json={"policy_id": "p", "innhold": {}})
    assert r.status_code == 401
    assert r.json()["feil"] == "token_ugyldig"


@pg
def test_liste_utkast_uautentisert_avvises(klient):
    r = klient.get("/v1/policyutkast")
    assert r.status_code == 401


@pg
def test_opprett_utkast_feilformet_body(klient):
    # Auth-porten ligger FØR form her (browsermutasjon): uten sesjon når vi
    # aldri formkontrollen, så et tomt objekt gir 401 — beviser at ruten finnes
    # og er gatet, ikke 404/405.
    r = klient.post("/v1/policyutkast", json={})
    assert r.status_code == 401


@pg
def test_attester_manglende_idempotency_naar_ruten(klient):
    # Uten sesjon: 401 (auth før idempotens). Ruten MÅ finnes (ikke 404/405).
    r = klient.post("/v1/policyutkast/u-abc/attester",
                    json={"diff_hash": "x"})
    assert r.status_code in (401, 403)


@pg
def test_ruter_finnes_ikke_405_paa_feil_metode(klient):
    # DELETE finnes ikke på kolleksjonen → 405, ikke 404 (ruten er registrert).
    r = klient.request("DELETE", "/v1/policyutkast")
    assert r.status_code == 405


# ---- Utkast-CRUD på funksjonsnivå -----------------------------------------

@pg
def test_utkast_livssyklus_opprett_rediger_valider():
    pid = "pol-" + secrets.token_hex(3)
    base = _gyldig(pid)
    endret = copy.deepcopy(base)
    endret["roller"].append({"id": "ny_rolle", "beskrivelse": "lagt til"})
    rt = _rt()
    try:
        o = _opprett(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=base)
        uid = o["utkast_id"]
        assert o["utkastversjon"] == 1 and o["status"] == "utkast"

        # Rediger m/ riktig versjon → 2.
        red = _rediger(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
            forventet_utkastversjon=1, innhold=endret)
        assert red["utkastversjon"] == 2

        # Stale versjon → optimistisk lås slår til.
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _rediger(
                rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
                forventet_utkastversjon=1, innhold={"roller": []})
        assert e.value.kode == "utkastversjon_utdatert"

        # Valider → validert + frosset hash (versjon 2 etter redigering).
        val = _valider(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
            forventet_utkastversjon=2)
        assert val["utfall"] == "validert"
        assert val["innholds_hash"]

        # Etter validering er innholdet frosset: redigering avvises (ikke
        # lenger status 'utkast').
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            _rediger(
                rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
                forventet_utkastversjon=2, innhold={"roller": []})
        assert e.value.kode == "utkast_ulovlig_tilstand"
    finally:
        rt.close()


@pg
def test_valider_ugyldig_policy_gir_feilliste_uten_tilstandsendring():
    pid = "pol-" + secrets.token_hex(3)
    rt = _rt()
    try:
        # `roller` med feil type → skjemafeil.
        o = _opprett(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold={"roller": "ikke-en-liste"})
        uid = o["utkast_id"]
        res = _valider(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
            forventet_utkastversjon=1)
        assert res["utfall"] == "ugyldig"
        assert res["feil"]
        # Status urørt (fortsatt utkast, ingen frosset hash).
        det = policyadmin.hent_utkast_detalj(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid)
        assert det["status"] == "utkast"
        assert det["innholds_hash"] is None
    finally:
        rt.close()


@pg
def test_opprett_idempotent_replay_samme_utkast_id():
    # Codex P1 R3: Idempotency-Key på skriveveien. Samme nøkkel + input →
    # NØYAKTIG samme utkast_id, ikke et nytt utkast.
    pid = "pol-" + secrets.token_hex(3)
    k = secrets.token_hex(8)
    rt = _rt()
    try:
        a = policyadmin.opprett_utkast(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=_gyldig(), idempotency_key=k, input_hash=k)
        b = policyadmin.opprett_utkast(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=_gyldig(), idempotency_key=k, input_hash=k)
        assert b.get("replay") is True
        assert a["utkast_id"] == b["utkast_id"]
        # Nøyaktig ett utkast med den id-en (sett tenant-GUC: _fullfor committet,
        # og LOCAL-konteksten nulles ved commit → ellers skjuler RLS raden).
        rt.execute("SELECT set_config('disponit.tenant',%s,false)", (TEN,))
        n = rt.execute("SELECT count(*) FROM policyutkast WHERE tenant=%s AND"
                       " utkast_id=%s", (TEN, a["utkast_id"])).fetchone()[0]
        rt.rollback()
        assert n == 1
    finally:
        rt.close()


def test_opprett_input_hash_binder_rollback_av_versjon():
    # Codex R3: bevis at ENDEPUNKTETS hash-konstruksjon binder
    # `rollback_av_versjon` (ikke bare at funksjonen skiller ulike input_hash).
    # Endepunktet kaller nøyaktig `opprett_input_hash`.
    from api.policyadmin_http import opprett_input_hash
    felles = dict(tenant="t", bid="b", policy_id="p", innhold={"a": 1},
                  idem="k")
    h_uten = opprett_input_hash(rollback_av=None, **felles)
    h_v3 = opprett_input_hash(rollback_av="3", **felles)
    h_v4 = opprett_input_hash(rollback_av="4", **felles)
    assert h_uten != h_v3 != h_v4 and h_uten != h_v4   # rullbakk endrer hashen
    # Codex R4: null og "" MÅ gi ULIKE hasher (str(None)/str("") kolliderte ikke).
    h_tom = opprett_input_hash(rollback_av="", **felles)
    assert h_uten != h_tom, "null og tom streng gir samme idempotenshash"
    # Deterministisk: samme input → samme hash.
    assert h_v3 == opprett_input_hash(rollback_av="3", **felles)


@pg
def test_opprett_samme_nokkel_annet_input_gir_konflikt():
    # Mekanismen bak: opprett_utkast gir konflikt når samme nøkkel møter et
    # ANNET input_hash (endepunktet produserer ulik hash for ulik rullbakk).
    pid = "pol-" + secrets.token_hex(3)
    k = secrets.token_hex(8)
    rt = _rt()
    try:
        policyadmin.opprett_utkast(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=_gyldig(), idempotency_key=k, input_hash=k + "-a")
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin.opprett_utkast(
                rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
                innhold=_gyldig(), idempotency_key=k, input_hash=k + "-b")
        assert e.value.kode == "idempotenskonflikt"
    finally:
        rt.close()


@pg
def test_valider_ugyldig_caches_og_binder_versjon():
    # Codex R3: ugyldig validering CACHES (ikke stille rulletilbake), og nøkkelen
    # er bundet til utkastversjonen.
    pid = "pol-" + secrets.token_hex(3)
    rt = _rt()
    try:
        o = _opprett(rt, tenant=TEN, aktor="forf", request_id="r",
                     policy_id=pid, innhold={"roller": "ikke-en-liste"})
        uid = o["utkast_id"]
        k = secrets.token_hex(8)
        r1 = policyadmin.valider_utkast(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
            forventet_utkastversjon=1, idempotency_key=k, input_hash=k)
        assert r1["utfall"] == "ugyldig"
        # Replay samme nøkkel → SAMME cachede ugyldig (ikke en stille ny kjøring).
        r2 = policyadmin.valider_utkast(
            rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
            forventet_utkastversjon=1, idempotency_key=k, input_hash=k)
        assert r2.get("replay") is True and r2["utfall"] == "ugyldig"
        # Feil versjon → utkastversjon_utdatert (nøkkelen er versjonsbundet).
        with pytest.raises(policyadmin.Aktiveringsfeil) as e:
            policyadmin.valider_utkast(
                rt, tenant=TEN, aktor="forf", request_id="r", utkast_id=uid,
                forventet_utkastversjon=99, idempotency_key=secrets.token_hex(8),
                input_hash="x")
        assert e.value.kode == "utkastversjon_utdatert"
    finally:
        rt.close()


def test_hent_maler_gir_bransjemaler():
    # PR-014: editoren starter fra en komplett bransjemal.
    maler = policyadmin.hent_maler()
    ider = {m["mal_id"] for m in maler}
    assert {"handverk-bygg", "netthandel", "tjenestebedrift"} <= ider
    from policy_validator import schema
    for m in maler:
        assert isinstance(m["innhold"], dict)
        assert m["innhold"].get("handlinger")        # komplett policy
        # Codex R2: en servert mal MÅ passere den KANONISKE validatoren — den er
        # et «gyldig utgangspunkt», ikke bare velformet.
        assert schema.valider_policy(m["innhold"]) == []


def test_kanonisk_validator_fanger_auto_med_vilkaar_uten_vilkaar():
    # Codex R2: regelen ligger i motorens kanoniske schema.valider_policy (lag-2),
    # ikke i en parallell validator. Den kjører post-skjema → kaster aldri på
    # typer. Bevist på en komplett bransjemal mutert til den ugyldige tilstanden.
    from policy_validator import schema
    pol = _gyldig()
    pol["handlinger"][0]["modus"] = "auto_med_vilkaar"
    pol["handlinger"][0].pop("vilkaar", None)   # nøkkelen HELT borte
    assert any("auto_med_vilkaar" in f for f in schema.valider_policy(pol))
    # En uendret, gyldig mal har ingen slik feil.
    assert not any("auto_med_vilkaar" in f
                   for f in schema.valider_policy(_gyldig()))


@pg
def test_valider_fanger_hengende_rolle_referanse():
    # Skjemagyldig, men semantisk brutt: en handling peker på en rolle som ikke
    # finnes. `valider` MÅ avvise (ugyldig med semantikkfeil) — en «alltid
    # gyldig»-påstand uten port ville sluppet dette gjennom.
    pid = "pol-" + secrets.token_hex(3)
    pol = _gyldig()
    pol["handlinger"][0]["tillatt_for"] = ["finnes_ikke"]
    rt = _rt()
    try:
        o = _opprett(rt, tenant=TEN, aktor="forf", request_id="r",
                     policy_id=pid, innhold=pol)
        res = _valider(rt, tenant=TEN, aktor="forf", request_id="r",
                       utkast_id=o["utkast_id"], forventet_utkastversjon=1)
        assert res["utfall"] == "ugyldig"
        assert any("ukjent rolle" in f for f in res["feil"]), res["feil"]
    finally:
        rt.close()


@pg
def test_valider_fanger_auto_med_vilkaar_uten_vilkaar():
    pid = "pol-" + secrets.token_hex(3)
    pol = _gyldig()
    pol["handlinger"][0]["modus"] = "auto_med_vilkaar"
    pol["handlinger"][0].pop("vilkaar", None)   # nøkkelen HELT borte
    rt = _rt()
    try:
        o = _opprett(rt, tenant=TEN, aktor="forf", request_id="r",
                     policy_id=pid, innhold=pol)
        res = _valider(rt, tenant=TEN, aktor="forf", request_id="r",
                       utkast_id=o["utkast_id"], forventet_utkastversjon=1)
        assert res["utfall"] == "ugyldig"
        assert any("auto_med_vilkaar" in f for f in res["feil"]), res["feil"]
    finally:
        rt.close()


@pg
def test_valider_avviser_dokument_med_fremmed_policy_id():
    """🔴 P1: identiteten er ÉN sak, og valideringen er der den fryses.

    Malen bærer sin egen `meta.policy_id`. Lager eier et utkast under sin id
    uten å rette dokumentets, er policyen skjemagyldig — og likevel umulig å
    aktivere forsvarlig: raden ville blitt indeksert under utkastets id, mens
    motoren bygger beslutningens policyreferanse fra dokumentets
    (`engine.policyreferanse`). Revisjonsposten og M-37-gjenopprettingen ville
    slått opp en id uten aktiv rad.

    Kontroll: fjern identitetssjekken i `valider_utkast`, så blir denne rød med
    utfall `validert` — og et dokument som aldri kunne fungert, frosset.
    """
    pid = "pol-" + secrets.token_hex(3)
    pol = _gyldig()                     # malens id, IKKE utkastets
    rt = _rt()
    try:
        o = _opprett(rt, tenant=TEN, aktor="forf", request_id="r",
                     policy_id=pid, innhold=pol)
        res = _valider(rt, tenant=TEN, aktor="forf", request_id="r",
                       utkast_id=o["utkast_id"], forventet_utkastversjon=1)
        assert res["utfall"] == "ugyldig", res
        assert any("meta.policy_id" in f for f in res["feil"]), res["feil"]
        # Og innholdet er IKKE frosset: eier kan rette id-en og validere igjen.
        det = policyadmin.hent_utkast_detalj(
            rt, tenant=TEN, aktor="forf", request_id="r",
            utkast_id=o["utkast_id"])
        assert det["status"] == "utkast" and det["innholds_hash"] is None
        _rediger(rt, tenant=TEN, aktor="forf", request_id="r",
                 utkast_id=o["utkast_id"], forventet_utkastversjon=1,
                 innhold=_gyldig(pid))
        ok = _valider(rt, tenant=TEN, aktor="forf", request_id="r",
                      utkast_id=o["utkast_id"], forventet_utkastversjon=2)
        assert ok["utfall"] == "validert", ok
    finally:
        rt.close()


@pg
def test_valider_avviser_malstatus_mens_utkastet_ennaa_kan_rettes():
    """🔴 P1: en mal bærer `status: utkast` — og fryses den slik, er den låst.

    Alle tre bransjemalene oppgir `meta.status: utkast`, editoren har ingen
    statuskontroll, og aktiveringen skriver `produksjon`. Fanget vi det først
    ved rundeåpning, sto eier igjen med et VALIDERT utkast: frosset innhold hun
    ikke kunne redigere, og en runde som ikke kunne åpnes. Normale UI-lagde
    policyer kunne dermed ikke aktiveres i det hele tatt.

    Kravet står derfor der utkastet ennå er redigerbart, og editoren setter
    statusen når den bygger innholdet.

    Kontroll: fjern statusdelen av `_dokumentavvik`, så blir denne rød med
    utfall `validert` — og utkastet innelåst.
    """
    pid = "pol-" + secrets.token_hex(3)
    pol = _gyldig(pid)
    pol["meta"]["status"] = "utkast"          # slik malen kommer
    rt = _rt()
    try:
        o = _opprett(rt, tenant=TEN, aktor="forf", request_id="r",
                     policy_id=pid, innhold=pol)
        res = _valider(rt, tenant=TEN, aktor="forf", request_id="r",
                       utkast_id=o["utkast_id"], forventet_utkastversjon=1)
        assert res["utfall"] == "ugyldig", res
        assert any("meta.status" in f for f in res["feil"]), res["feil"]
        # Utkastet er IKKE frosset: eier retter statusen og validerer igjen.
        _rediger(rt, tenant=TEN, aktor="forf", request_id="r",
                 utkast_id=o["utkast_id"], forventet_utkastversjon=1,
                 innhold=_gyldig(pid))
        ok = _valider(rt, tenant=TEN, aktor="forf", request_id="r",
                      utkast_id=o["utkast_id"], forventet_utkastversjon=2)
        assert ok["utfall"] == "validert", ok
    finally:
        rt.close()


@pg
def test_maler_endepunkt_uautentisert_avvises(klient):
    r = klient.get("/v1/policymaler")
    assert r.status_code == 401


@pg
def test_detalj_eksponerer_innhold_for_redigering():
    pid = "pol-" + secrets.token_hex(3)
    rt = _rt()
    try:
        o = _opprett(rt, tenant=TEN, aktor="forf", request_id="r",
                     policy_id=pid, innhold=_gyldig())
        det = policyadmin.hent_utkast_detalj(
            rt, tenant=TEN, aktor="forf", request_id="r",
            utkast_id=o["utkast_id"])
        assert isinstance(det["innhold"], dict)
        assert det["innhold"].get("roller")          # editoren kan laste det
    finally:
        rt.close()


@pg
def test_hent_detalj_har_diff_og_klasse():
    pid = "pol-" + secrets.token_hex(3)
    rt = _rt()
    try:
        o = _opprett(
            rt, tenant=TEN, aktor="forf", request_id="r", policy_id=pid,
            innhold=_gyldig())
        det = policyadmin.hent_utkast_detalj(
            rt, tenant=TEN, aktor="forf", request_id="r",
            utkast_id=o["utkast_id"])
        assert det["risikoklasse"] == "UTVIDER"     # fra DENY_ALL
        assert det["diff"]["endringer"]
        assert det["aktiv_runde"] is None
    finally:
        rt.close()
