"""Controlleren for m44_kampanje, isolert (ARC B kampanje, PR 4):
stubklient og stubsender, ingen base. Det som måles er DOMMENE — hva som
kvitteres, og at én e-post aldri blir to.

  1. Tomt claim → tomt, ingen sending.
  2. Gyldig claim → én sending til den DEKRYPTERTE adressen, i tenantens
     avsendernavn og svar-til, med navnet flettet inn og
     avmeldingslenken i teksten; kvitteringen er `utfort`, ressursbundet
     til (kampanje, mottaker), uten adressen.
  3. Hindring i claim-svaret (samtykke trukket, avlyst, deaktivert …) →
     `feilet` med hindringen som feilkode, INGEN sending.
  4. Kontraktsbrudd i payloaden → `feilet oppdrag_ugyldig`, ingen sending.
  5. SMTP-feil FØR aksept → `feilet sending_avvist`; alt annet →
     `feilet sending_uviss`, terminalt.
  6. Kvittering 202 «lagret uten statusendring» → `ukvittert`.
  7. Utløpt frist → `feilet frist_utilstrekkelig`, ingen sending.
  8. Malene: uten avmeldingslenke leveres ingenting; et ukjent felt i
     teksten er en Flettefeil; lenken legges til som fot bare når teksten
     ikke alt bærer den; klammer som ikke er felter står urørt.
"""
import smtplib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

TENANT = "t-m44-ctl"
ADRESSE = "kari.nordmann@example.com"


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _payload(**over):
    return {"kampanje_id": str(uuid.uuid4()),
            "mottaker_id": str(uuid.uuid4()), "omfang": "mottaker",
            "planlagt_sendt": "2026-09-09", **over}


def _utforelse(**over):
    return {"mottaker_epost": ADRESSE, "mottaker_maske": "k****@example.com",
            "mottaker_navn": "Kari Nordmann", "emne": "Høstsjekk {navn}",
            "tekst": "Hei {navn},\n\nvi tilbyr høstsjekk av anlegget.",
            "avmeldingslenke": "https://fjordlys.example/avmeld/abc",
            "planlagt_sendt": "2026-09-09",
            "avsender_navn": "Fjordlys Elektro AS",
            "svar_til": "post@fjordlys.example", **over}


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
                     "oppdragstype": "kampanje.send",
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
    from modules.m44_kampanje import controller
    controller._sov = lambda s: None
    sender = sender or _Sender()
    res = controller.kjor_en(klient, "tk", sender, lambda k: {**k, "sig": 1})
    return res, sender


def test_tomt_claim_leverer_ingenting():
    res, sender = _kjor(_Klient(tomt=True))
    assert res == {"utfall": "tomt"} and sender.sendt == []


def test_gyldig_claim_leverer_en_gang_og_kvitterer_utfort():
    k = _Klient()
    res, sender = _kjor(k)
    assert res["utfall"] == "utfort", res
    assert len(sender.sendt) == 1
    e = sender.sendt[0]
    assert e["til"] == ADRESSE
    assert e["avsender_navn"] == "Fjordlys Elektro AS"
    assert e["svar_til"] == "post@fjordlys.example"
    assert e["emne"] == "Høstsjekk Kari Nordmann"
    assert e["tekst"].startswith("Hei Kari Nordmann,")
    assert "https://fjordlys.example/avmeld/abc" in e["tekst"]
    assert len(k.kvitteringer) == 1
    kv = k.kvitteringer[0]
    assert kv["resultat"] == "utfort"
    assert kv["ressurs_id"] == (f"kampanje:{k.payload['kampanje_id']}:"
                                f"{k.payload['mottaker_id']}")
    assert kv["kampanje_id"] == k.payload["kampanje_id"]
    assert kv["mottaker_id"] == k.payload["mottaker_id"]
    assert kv["malversjon"] == "kampanje-v1"
    assert kv["melding_id"] == "<m1@fjordlys.example>"
    assert kv["mottaker_maske"] == "k****@example.com"
    assert kv["planlagt_sendt"] == "2026-09-09"
    assert kv["sendt_ts"] and kv["sig"] == 1
    # Adressen, navnet og teksten står ALDRI i kvitteringen.
    assert ADRESSE not in str(kv) and "Kari" not in str(kv)
    assert "høstsjekk" not in str(kv).lower()


@pytest.mark.parametrize("utforelse,kode", [
    ({"hindring": "samtykke_ugyldig"}, "samtykke_ugyldig"),
    ({"hindring": "kampanje_avlyst"}, "kampanje_avlyst"),
    ({"hindring": "mottaker_deaktivert"}, "mottaker_deaktivert"),
    ({"hindring": "innhold_mangler"}, "innhold_mangler"),
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


def test_kontraktsbrudd_gir_feilet_uten_sending():
    k = _Klient(payload={"kampanje_id": "x", "omfang": "mottaker"})
    res, sender = _kjor(k)
    assert res["utfall"] == "avbrutt"
    assert res["grunn"].startswith("oppdrag_ugyldig"), res
    assert sender.sendt == []
    assert k.kvitteringer[0]["feilkode"] == "oppdrag_ugyldig"


def test_fremmed_oppdragstype_gir_kodet_nei():
    class _Fremmed(_Klient):
        def post(self, sti, json=None, headers=None, **kw):
            r = super().post(sti, json=json, headers=headers)
            if sti == "/v1/oppdrag/claim":
                r._kropp["oppdragstype"] = "purring.send"
            return r
    k = _Fremmed()
    res, sender = _kjor(k)
    assert res["grunn"] == "oppdragstype_ukjent" and sender.sendt == []


def test_smtp_feil_for_aksept_er_sending_avvist():
    k = _Klient()
    res, _ = _kjor(k, _Sender(smtplib.SMTPRecipientsRefused({})))
    assert res["utfall"] == "avbrutt" and res["grunn"] == "sending_avvist"
    assert k.kvitteringer[0]["feilkode"] == "sending_avvist"


def test_uvisst_utfall_er_terminalt():
    k = _Klient()
    res, _ = _kjor(k, _Sender(TimeoutError("midt i DATA")))
    assert res["utfall"] == "avbrutt" and res["grunn"] == "sending_uviss"
    assert k.kvitteringer[0]["feilkode"] == "sending_uviss"
    assert k.kvitteringer[0]["resultat"] == "feilet"


def test_lagret_uten_statusendring_er_ukvittert():
    k = _Klient(kvitteringssvar=lambda kv: _Svar(202, {"status": "lagret"}))
    res, sender = _kjor(k)
    assert res["utfall"] == "ukvittert" and len(sender.sendt) == 1


def test_utlopt_frist_leverer_ikke():
    k = _Klient(frist=_iso(datetime.now(timezone.utc)
                           - timedelta(seconds=1)))
    res, sender = _kjor(k)
    assert res["grunn"] == "frist_utilstrekkelig" and sender.sendt == []


def test_malene():
    from modules.m44_kampanje import maler
    # Uten lenke leveres ingenting — vilkåret `avmeldingslenke`.
    with pytest.raises(maler.Flettefeil) as e:
        maler.flett(_utforelse(avmeldingslenke=""))
    assert e.value.kode == "felt_mangler:avmeldingslenke"
    # Et fremmed felt er en Flettefeil, ikke en tom streng i e-posten.
    with pytest.raises(maler.Flettefeil) as e:
        maler.flett(_utforelse(tekst="Hei {kundenummer}"))
    assert e.value.kode == "felt_ukjent:kundenummer"
    with pytest.raises(maler.Flettefeil) as e:
        maler.flett(_utforelse(tekst="Hei {navn2} {utm_2}"))
    assert e.value.kode == "felt_ukjent:navn2,utm_2"
    # {navn} uten navn er et hull.
    with pytest.raises(maler.Flettefeil) as e:
        maler.flett(_utforelse(mottaker_navn=""))
    assert e.value.kode == "felt_mangler:navn"
    # Lenken i teksten → ingen fot; ellers fot. Løse klammer står urørt.
    m = maler.flett(_utforelse(tekst="Meld av: {avmeldingslenke} {x"))
    assert m["tekst"] == ("Meld av: https://fjordlys.example/avmeld/abc"
                          " {x")
    m = maler.flett(_utforelse(emne="Tilbud"))
    assert m["tekst"].count("https://fjordlys.example/avmeld/abc") == 1
    assert "Meld deg av her" in m["tekst"] and m["emne"] == "Tilbud"
    assert m["malversjon"] == "kampanje-v1"
    # Uten innhold leveres ingenting.
    with pytest.raises(maler.Flettefeil):
        maler.flett(_utforelse(tekst=""))
    # Controlleren gjør Flettefeil til `malfeil`, uten sending.
    k = _Klient(utforelse=_utforelse(avmeldingslenke=None))
    res, sender = _kjor(k)
    assert res["grunn"] == "malfeil:felt_mangler:avmeldingslenke"
    assert sender.sendt == [] and k.kvitteringer[0]["feilkode"] == "malfeil"
