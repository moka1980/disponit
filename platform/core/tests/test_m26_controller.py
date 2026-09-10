"""Controlleren for m26_prisbok, isolert (ARC B tilbud, PR 4): stubklient
og stubsender, ingen base. Det som måles er DOMMENE — hva som kvitteres,
og at ett tilbud aldri blir to e-poster.

  1. Tomt claim → tomt, ingen sending.
  2. Gyldig claim → én sending til den DEKRYPTERTE adressen, i tenantens
     avsendernavn og svar-til; emnet bærer summen; teksten linjene med
     registerets tall, den bundne klausulteksten og signaturen;
     kvitteringen er `utfort`, ressursbundet til tilbudet, uten adresse.
  3. Hindring i claim-svaret → `feilet` med hindringen, INGEN sending.
  4. Kontraktsbrudd i payloaden → `feilet oppdrag_ugyldig`.
  5. SMTP-feil før aksept → `sending_avvist`; ellers `sending_uviss`.
  6. 202 → `ukvittert`; utløpt frist → ingen sending.
  7. Formen: tom kunde / ingen linjer / ugyldig sum er en Flettefeil;
     tallene skrives som de kom (ingen omregning).
"""
import smtplib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

TENANT = "t-m26-ctl"
ADRESSE = "styret@nordvik.example"


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _payload(**over):
    return {"tilbud_id": str(uuid.uuid4()), "sum_ore": 2850000,
            "gyldig_til": "2026-12-31", "omfang": "tilbud", **over}


def _utforelse(**over):
    return {"mottaker_epost": ADRESSE, "mottaker_maske": "s****@nordvik.example",
            "kunde_navn": "Tromsø Borettslag", "sum_ore": 2850000,
            "valuta": "NOK", "tilbudsdato": "2026-09-10",
            "gyldig_til": "2026-12-31", "innledning": "Takk for befaringen.",
            "linjer": [
                {"linje_nr": 1, "produktkode": "KAB", "produktnavn": "Kabel",
                 "enhet": "m", "antall": 40, "listepris_ore": 1250,
                 "enhetspris_ore": 1250, "linjesum_ore": 50000},
                {"linje_nr": 2, "produktkode": "VP", "produktnavn": "Varmepumpe",
                 "enhet": "stk", "antall": 1, "listepris_ore": 2890000,
                 "enhetspris_ore": 2800000, "linjesum_ore": 2800000}],
            "klausuler": [{"kode": "BET-14", "versjon": 1,
                           "tittel": "Betaling", "tekst": "14 dager"}],
            "avsender_navn": "Fjordlys Elektro AS",
            "svar_til": "post@fjordlys.example",
            "signatur": "Fjordlys Elektro AS · 77 00 00 00", **over}


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
                 kvitteringssvar=None, frist=None):
        self.tomt = tomt
        self.payload = _payload() if payload is None else payload
        self.utforelse = _utforelse() if utforelse is ... else utforelse
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
                     "oppdragstype": "tilbud.generer",
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


class _Sender:
    def __init__(self, feil=None):
        self.feil = feil
        self.sendt = []

    def __call__(self, til, emne, tekst, *, avsender_navn=None, svar_til=None):
        if self.feil is not None:
            raise self.feil
        self.sendt.append({"til": til, "emne": emne, "tekst": tekst,
                           "avsender_navn": avsender_navn, "svar_til": svar_til})
        return {"melding_id": "<m1@fjordlys.example>"}


def _kjor(klient, sender=None):
    from modules.m26_prisbok import controller
    controller._sov = lambda s: None
    sender = sender or _Sender()
    res = controller.kjor_en(klient, "tk", sender, lambda k: {**k, "sig": 1})
    return res, sender


def test_tomt_claim_sender_ingenting():
    res, sender = _kjor(_Klient(tomt=True))
    assert res == {"utfall": "tomt"} and sender.sendt == []


def test_gyldig_claim_sender_en_gang_og_kvitterer_utfort():
    k = _Klient()
    res, sender = _kjor(k)
    assert res["utfall"] == "utfort", res
    assert len(sender.sendt) == 1
    e = sender.sendt[0]
    assert e["til"] == ADRESSE
    assert e["avsender_navn"] == "Fjordlys Elektro AS"
    assert e["svar_til"] == "post@fjordlys.example"
    assert e["emne"] == "Tilbud fra Fjordlys Elektro AS: 28500,00 NOK"
    assert e["tekst"].startswith("Hei Tromsø Borettslag,")
    assert "Takk for befaringen." in e["tekst"]
    assert "KAB Kabel: 40 m à 12,50 = 500,00 NOK" in e["tekst"]
    assert "VP Varmepumpe: 1 stk à 28000,00 = 28000,00 NOK" in e["tekst"]
    assert "Sum: 28500,00 NOK" in e["tekst"]
    assert "Betaling: 14 dager" in e["tekst"]
    assert e["tekst"].rstrip().endswith("Fjordlys Elektro AS · 77 00 00 00")
    kv = k.kvitteringer[0]
    assert kv["resultat"] == "utfort"
    assert kv["ressurs_id"] == "tilbud:" + k.payload["tilbud_id"]
    assert kv["tilbud_id"] == k.payload["tilbud_id"]
    assert kv["malversjon"] == "tilbud-v1" and kv["sum_ore"] == 2850000
    assert kv["mottaker_maske"] == "s****@nordvik.example"
    assert kv["sendt_ts"] and kv["sig"] == 1
    assert ADRESSE not in str(kv) and "Takk for" not in str(kv)


@pytest.mark.parametrize("utforelse,kode", [
    ({"hindring": "tilbud_ikke_godkjent"}, "tilbud_ikke_godkjent"),
    ({"hindring": "tilbud_utlopt"}, "tilbud_utlopt"),
    ({"hindring": "mottaker_uleselig"}, "mottaker_uleselig"),
    (None, "utforelse_mangler"),
    (_utforelse(mottaker_epost=""), "mottaker_mangler"),
])
def test_hindring_gir_feilet_uten_sending(utforelse, kode):
    k = _Klient(utforelse=utforelse)
    res, sender = _kjor(k)
    assert res["utfall"] == "avbrutt" and res["grunn"] == kode, res
    assert sender.sendt == []
    assert k.kvitteringer[0]["resultat"] == "feilet"
    assert k.kvitteringer[0]["feilkode"] == kode
    assert res["tilbud"] == k.payload["tilbud_id"]


def test_kontraktsbrudd_gir_feilet_uten_sending():
    k = _Klient(payload={"tilbud_id": "x", "omfang": "tilbud"})
    res, sender = _kjor(k)
    assert res["utfall"] == "avbrutt"
    assert res["grunn"].startswith("oppdrag_ugyldig"), res
    assert sender.sendt == []


def test_smtp_feil_for_aksept_er_sending_avvist_ellers_uviss():
    k = _Klient()
    res, _ = _kjor(k, _Sender(smtplib.SMTPRecipientsRefused({})))
    assert res["grunn"] == "sending_avvist"
    assert k.kvitteringer[0]["feilkode"] == "sending_avvist"
    k = _Klient()
    res, _ = _kjor(k, _Sender(TimeoutError("midt i DATA")))
    assert res["grunn"] == "sending_uviss"
    assert k.kvitteringer[0]["resultat"] == "feilet"


def test_ukvittert_og_utlopt_frist():
    k = _Klient(kvitteringssvar=lambda kv: _Svar(202, {"status": "lagret"}))
    res, sender = _kjor(k)
    assert res["utfall"] == "ukvittert" and len(sender.sendt) == 1
    k = _Klient(frist=_iso(datetime.now(timezone.utc) - timedelta(seconds=1)))
    res, sender = _kjor(k)
    assert res["grunn"] == "frist_utilstrekkelig" and sender.sendt == []


def test_formen():
    from modules.m26_prisbok import maler
    for over, kode in ((dict(kunde_navn=" "), "felt_mangler:kunde_navn"),
                       (dict(linjer=[]), "felt_mangler:linjer"),
                       (dict(sum_ore="28500"), "felt_ugyldig:sum_ore")):
        with pytest.raises(maler.Flettefeil) as e:
            maler.bygg(_utforelse(**over))
        assert e.value.kode == kode
    m = maler.bygg(_utforelse(signatur=None, innledning=None, klausuler=[]))
    assert "Betingelser" not in m["tekst"] and m["malversjon"] == "tilbud-v1"
    assert maler.belop(5) == "0,05" and maler.belop(123456799) == "1234567,99"
    k = _Klient(utforelse=_utforelse(linjer=[]))
    res, sender = _kjor(k)
    assert res["grunn"] == "malfeil:felt_mangler:linjer" and sender.sendt == []


def test_bygg_nekter_klausul_som_ikke_er_objekt_og_belop_som_ikke_er_heltall():
    from modules.m26_prisbok import maler
    """CodeRabbit PR 4: en klausul som er en streng, og en pris som er
    flyttall/streng, er ikke registerets form — Flettefeil, aldri
    stille sannhet."""
    with pytest.raises(maler.Flettefeil, match="klausul"):
        maler.bygg(_utforelse(klausuler=["BET-14"]))
    u = _utforelse()
    u["linjer"][0]["enhetspris_ore"] = 1250.0
    with pytest.raises(maler.Flettefeil, match="belop|linje"):
        maler.bygg(u)
    u = _utforelse()
    u["linjer"][0]["linjesum_ore"] = "5000"
    with pytest.raises(maler.Flettefeil, match="belop|linje"):
        maler.bygg(u)

