"""Controlleren for m14_fakturakontroll, isolert (ARC B bokføring, PR 3):
stubklient, ingen base. Det som måles er DOMMENE — hva som kvitteres, og
at bilaget er en avskrift av registerets tall.

  1. Tomt claim → tomt.
  2. Gyldig claim → kvitteringen er `utfort`, ressursbundet til fakturaen,
     og bærer bilaget SLIK claim-svaret ga det: nummer `LF-<faktura>`,
     retning ut, brutto, motpart, datoer, malversjon `bilag-v1`.
  3. Hindring i claim-svaret (avvist, alt bokført, kontroll ikke ren,
     manuell mangler …) → `feilet` med hindringen som feilkode.
  4. Claim-svar som beskriver en ANNEN faktura eller et annet beløp enn
     payloaden → `feilet utforelse_feil_*` — modulen bokfører aldri noe
     annet enn oppdragets faktura.
  5. Kontraktsbrudd i payloaden → `feilet oppdrag_ugyldig`; ukjent
     oppdragstype → `oppdragstype_ukjent`.
  6. 202 «lagret uten statusendring» → `ukvittert`; utløpt frist →
     `frist_utilstrekkelig`.
  7. Formen: ugyldig retning, beløp ≤ 0, forfall før utstedt eller
     manglende nummer er en Bilagsfeil.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest

TENANT = "t-m14-ctl"


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _payload(**over):
    return {"faktura_id": str(uuid.uuid4()), "fakturanummer": "NK-4471",
            "leverandor_ref": "lev-nordkabel", "brutto_ore": 2312500,
            "omfang": "bilag", **over}


def _utforelse(payload, **over):
    return {"faktura_id": payload.get("faktura_id"),
            "fakturanummer": payload.get("fakturanummer"),
            "leverandor_ref": payload.get("leverandor_ref"),
            "bilagsnummer": "LF-" + str(payload.get("fakturanummer")),
            "retning": "ut", "belop_ore": payload.get("brutto_ore"),
            "valuta": "NOK", "motpart": payload.get("leverandor_ref"),
            "utstedt": "2026-09-01", "forfall": "2026-10-01", **over}


class _Svar:
    def __init__(self, status, kropp=None):
        self.status_code = status
        self._kropp = kropp

    def json(self):
        if self._kropp is None:
            raise ValueError("tom")
        return self._kropp

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _Klient:
    def __init__(self, *, tomt=False, payload=None, utforelse=...,
                 kvitteringssvar=None, frist=None,
                 oppdragstype="faktura.bokfor"):
        self.tomt = tomt
        self.payload = _payload() if payload is None else payload
        self.utforelse = (_utforelse(self.payload) if utforelse is ...
                          else utforelse)
        self.oppdragstype = oppdragstype
        self.kvitteringer = []
        self.kvitteringssvar = kvitteringssvar or (
            lambda k: _Svar(200, {"status": k.get("resultat", "utfort"),
                                  "oppdrag_id": 1}))
        naa = datetime.now(timezone.utc)
        self.frist = frist or _iso(naa + timedelta(minutes=15))
        self.kvittering_utloper = _iso(naa + timedelta(minutes=20))

    def post(self, sti, json=None, headers=None, **kw):
        if sti == "/v1/oppdrag/claim":
            if self.tomt:
                return _Svar(204)
            kropp = {"oppdrag_id": 1, "tenant": TENANT,
                     "oppdragstype": self.oppdragstype,
                     "kvittering_jti": "j", "repair_operation_id": "r",
                     "owner_claim_id": "o" * 22, "owner_generation": 0,
                     "utforelsesfrist": self.frist,
                     "kvittering_utloper": self.kvittering_utloper,
                     "payload": self.payload}
            if self.utforelse is not None:
                kropp["utforelse"] = self.utforelse
            return _Svar(200, kropp)
        assert sti == "/v1/oppdrag/kvittering", sti
        self.kvitteringer.append(json)
        return self.kvitteringssvar(json or {})


def _kjor(klient):
    from modules.m14_fakturakontroll import controller
    controller._sov = lambda s: None
    return controller.kjor_en(klient, "tk", lambda k: {**k, "sig": 1})


def test_tomt_claim():
    assert _kjor(_Klient(tomt=True)) == {"utfall": "tomt"}


def test_gyldig_claim_kvitterer_bilaget_som_registeret_ga_det():
    k = _Klient()
    res = _kjor(k)
    assert res["utfall"] == "utfort", res
    assert res["bilagsnummer"] == "LF-NK-4471"
    kv = k.kvitteringer[0]
    assert kv["resultat"] == "utfort" and kv["sig"] == 1
    assert kv["ressurs_id"] == "faktura:" + k.payload["faktura_id"]
    assert kv["faktura_id"] == k.payload["faktura_id"]
    assert kv["bilagsnummer"] == "LF-NK-4471" and kv["retning"] == "ut"
    assert kv["belop_ore"] == 2312500 and kv["motpart"] == "lev-nordkabel"
    assert kv["utstedt"] == "2026-09-01" and kv["forfall"] == "2026-10-01"
    assert kv["malversjon"] == "bilag-v1" and kv["bokfort_ts"]
    # Den store bokføringen er samme arbeid.
    k2 = _Klient(oppdragstype="faktura.bokfor_stor")
    assert _kjor(k2)["utfall"] == "utfort"


@pytest.mark.parametrize("utforelse,kode", [
    ({"hindring": "faktura_avvist"}, "faktura_avvist"),
    ({"hindring": "alt_bokfort"}, "alt_bokfort"),
    ({"hindring": "kontroll_ikke_ren"}, "kontroll_ikke_ren"),
    ({"hindring": "manuell_kontroll_mangler"}, "manuell_kontroll_mangler"),
    (None, "utforelse_mangler"),
])
def test_hindring_gir_feilet(utforelse, kode):
    k = _Klient(utforelse=utforelse)
    res = _kjor(k)
    assert res["utfall"] == "avbrutt" and res["grunn"] == kode, res
    assert k.kvitteringer[0]["resultat"] == "feilet"
    assert k.kvitteringer[0]["feilkode"] == kode
    assert res["faktura"] == k.payload["faktura_id"]


def test_claim_svar_om_en_annen_faktura_bokfores_aldri():
    p = _payload()
    annen = _utforelse(_payload())
    k = _Klient(payload=p, utforelse=annen)
    res = _kjor(k)
    assert res["grunn"] == "utforelse_feil_faktura", res
    assert k.kvitteringer[0]["feilkode"] == "utforelse_feil_faktura"
    k = _Klient(payload=p, utforelse=_utforelse(p, belop_ore=1))
    res = _kjor(k)
    assert res["grunn"] == "utforelse_feil_belop", res


def test_kontraktsbrudd_og_ukjent_type():
    k = _Klient(payload={"faktura_id": "x", "omfang": "bilag"})
    res = _kjor(k)
    assert res["utfall"] == "avbrutt"
    assert res["grunn"].startswith("oppdrag_ugyldig"), res
    k = _Klient(oppdragstype="kundeservice.svar.send")
    res = _kjor(k)
    assert res["grunn"] == "oppdragstype_ukjent"
    assert k.kvitteringer[0]["feilkode"] == "oppdragstype_ukjent"


def test_ukvittert_og_utlopt_frist():
    k = _Klient(kvitteringssvar=lambda kv: _Svar(202, {"status": "lagret"}))
    res = _kjor(k)
    assert res["utfall"] == "ukvittert"
    k = _Klient(frist=_iso(datetime.now(timezone.utc)
                           - timedelta(seconds=1)))
    res = _kjor(k)
    assert res["grunn"] == "frist_utilstrekkelig"
    assert k.kvitteringer[0]["feilkode"] == "frist_utilstrekkelig"


def test_formen():
    from modules.m14_fakturakontroll import bilag
    p = _payload()
    for over, kode in ((dict(bilagsnummer=" "), "felt_mangler:bilagsnummer"),
                       (dict(retning="opp"), "felt_ugyldig:retning"),
                       (dict(belop_ore=0), "felt_ugyldig:belop_ore"),
                       (dict(belop_ore="100"), "felt_ugyldig:belop_ore"),
                       (dict(belop_ore=True), "felt_ugyldig:belop_ore"),
                       (dict(motpart=""), "felt_mangler:motpart"),
                       (dict(utstedt="i går"), "felt_ugyldig:utstedt"),
                       (dict(forfall="2026-08-01"), "felt_ugyldig:forfall")):
        with pytest.raises(bilag.Bilagsfeil) as e:
            bilag.bygg(_utforelse(p, **over))
        assert e.value.kode == kode, (over, e.value.kode)
    b = bilag.bygg(_utforelse(p, forfall=None))
    assert b["forfall"] is None and b["malversjon"] == "bilag-v1"
    k = _Klient(payload=p, utforelse=_utforelse(p, retning="opp"))
    res = _kjor(k)
    assert res["grunn"] == "bilagfeil:felt_ugyldig:retning"
    assert k.kvitteringer[0]["feilkode"] == "bilagfeil"
