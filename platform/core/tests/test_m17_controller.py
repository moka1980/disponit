"""Controlleren for m17_kundeservice, isolert (ARC B kundeservice, PR 4):
stubklient og stubsender, ingen base. Det som måles er DOMMENE — hva
som kvitteres, og at ett svar aldri blir to.

  1. Tomt claim → tomt, ingen sending.
  2. Gyldig claim → én sending til den DEKRYPTERTE adressen, i tenantens
     avsendernavn og svar-til, med «Re:» foran henvendelsens emne, det
     godkjente utkastet som tekst og signaturen under; kvitteringen er
     `utfort`, ressursbundet til (henvendelse, utkast), uten adresse og
     uten tekst.
  3. Hindring i claim-svaret (lukket, i unntakskøen, ikke godkjent …) →
     `feilet` med hindringen som feilkode, INGEN sending.
  4. Kontraktsbrudd i payloaden → `feilet oppdrag_ugyldig`.
  5. SMTP-feil før aksept → `sending_avvist`; ellers `sending_uviss`.
  6. 202 «lagret uten statusendring» → `ukvittert`; utløpt frist →
     ingen sending.
  7. Formen: tom tekst eller tomt emne er en Flettefeil; «Re:» legges
     bare til én gang; signaturen er valgfri.
"""
import smtplib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

TENANT = "t-m17-ctl"
ADRESSE = "kari.nordmann@nordvik.example"
UTKAST = "Hei Kari,\n\nvi kommer tirsdag mellom 09 og 11.\n\nVennlig hilsen"


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _payload(**over):
    return {"henvendelse_id": str(uuid.uuid4()),
            "utkast_id": str(uuid.uuid4()), "omfang": "svar", **over}


def _utforelse(**over):
    return {"mottaker_epost": ADRESSE, "mottaker_maske": "k****@nordvik.example",
            "ekstern_ref": "MSG-1", "emne": "Når kommer dere?",
            "tekst": UTKAST, "avsender_navn": "Fjordlys Elektro AS",
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
                     "oppdragstype": "kundeservice.svar.send",
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

    def __call__(self, til, emne, tekst, *, avsender_navn=None,
                 svar_til=None):
        if self.feil is not None:
            raise self.feil
        self.sendt.append({"til": til, "emne": emne, "tekst": tekst,
                           "avsender_navn": avsender_navn,
                           "svar_til": svar_til})
        return {"melding_id": "<m1@fjordlys.example>"}


def _kjor(klient, sender=None):
    from modules.m17_kundeservice import controller
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
    assert e["emne"] == "Re: Når kommer dere?"
    assert e["tekst"].startswith("Hei Kari,")
    assert e["tekst"].rstrip().endswith("Fjordlys Elektro AS · 77 00 00 00")
    kv = k.kvitteringer[0]
    assert kv["resultat"] == "utfort"
    assert kv["ressurs_id"] == (f"henvendelse:{k.payload['henvendelse_id']}:"
                                f"{k.payload['utkast_id']}")
    assert kv["henvendelse_id"] == k.payload["henvendelse_id"]
    assert kv["utkast_id"] == k.payload["utkast_id"]
    assert kv["malversjon"] == "svar-v1"
    assert kv["mottaker_maske"] == "k****@nordvik.example"
    assert kv["sendt_ts"] and kv["sig"] == 1
    # Adressen, teksten og emnet står ALDRI i kvitteringen.
    assert ADRESSE not in str(kv) and "tirsdag" not in str(kv)
    assert "kommer dere" not in str(kv)


@pytest.mark.parametrize("utforelse,kode", [
    ({"hindring": "henvendelse_lukket"}, "henvendelse_lukket"),
    ({"hindring": "henvendelse_i_unntakskoe"}, "henvendelse_i_unntakskoe"),
    ({"hindring": "utkast_ikke_godkjent"}, "utkast_ikke_godkjent"),
    ({"hindring": "mottaker_mangler"}, "mottaker_mangler"),
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
    assert res["henvendelse"] == k.payload["henvendelse_id"]


def test_kontraktsbrudd_gir_feilet_uten_sending():
    k = _Klient(payload={"henvendelse_id": "x", "omfang": "svar"})
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
    k = _Klient(frist=_iso(datetime.now(timezone.utc)
                           - timedelta(seconds=1)))
    res, sender = _kjor(k)
    assert res["grunn"] == "frist_utilstrekkelig" and sender.sendt == []


def test_formen():
    from modules.m17_kundeservice import maler
    with pytest.raises(maler.Flettefeil) as e:
        maler.bygg(_utforelse(tekst="  "))
    assert e.value.kode == "felt_mangler:tekst"
    with pytest.raises(maler.Flettefeil) as e:
        maler.bygg(_utforelse(emne=""))
    assert e.value.kode == "felt_mangler:emne"
    m = maler.bygg(_utforelse(emne="Re: Alt klart", signatur=None))
    assert m["emne"] == "Re: Alt klart" and m["tekst"] == UTKAST
    assert m["malversjon"] == "svar-v1"
    k = _Klient(utforelse=_utforelse(tekst=""))
    res, sender = _kjor(k)
    assert res["grunn"] == "malfeil:felt_mangler:tekst" and sender.sendt == []
