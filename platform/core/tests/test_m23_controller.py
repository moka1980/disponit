"""Controlleren for m23_fordring, isolert (ARC B, PR 4): stubklient og
stubsender, ingen base. Det som måles er DOMMENE — hva som kvitteres, og
at én e-post aldri blir to.

  1. Tomt claim → tomt, ingen sending.
  2. Gyldig claim → én sending til den DEKRYPTERTE adressen, med
     tenantens avsendernavn og svar-til; kvitteringen er `utfort`,
     ressursbundet til fordringen, og bærer sendt_ts/malversjon/trinn.
  3. Hindring i claim-svaret (mottaker mangler / fordring avsluttet) →
     `feilet` med hindringen som feilkode, INGEN sending.
  4. Kontraktsbrudd i payloaden → `feilet oppdrag_ugyldig`, ingen sending.
  5. SMTP-feil FØR aksept → `feilet sending_avvist`.
  6. Alt annet under sendingen → `feilet sending_uviss` — terminalt, og
     en NY runde sender ikke igjen (claimet er lukket).
  7. Trinnet har flyttet seg siden bestillingen → `feilet trinn_flyttet`.
  8. Kvittering 202 «lagret uten statusendring» → `ukvittert`.
  9. Malene: alle tre trinnhandlinger fletter, `inkasso` finnes ikke,
     og et tomt felt er en Flettefeil — aldri en e-post med hull.
"""
import smtplib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

TENANT = "t-m23-ctl"


def _iso(dt):
    return dt.astimezone(timezone.utc).isoformat()


def _payload(**over):
    return {"fordring_id": str(uuid.uuid4()), "fakturanummer": "F-2026-17",
            "trinn": 1, "handling_trinn": "paaminnelse",
            "rest_ore": 250000, "omfang": "trinn", **over}


def _utforelse(**over):
    return {"mottaker_epost": "kunde@nordvik.example",
            "mottaker_maske": "k****@nordvik.example",
            "kunde_ref": "Nordvik AS", "fakturanummer": "F-2026-17",
            "rest_ore": 250000, "forfall": "2026-08-20", "trinn": 0,
            "neste_trinn": 1, "handling_trinn": "paaminnelse",
            "gebyr_ore": 0, "avsender_navn": "Fjordlys Elektro AS",
            "svar_til": "regnskap@fjordlys.example", **over}


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
        self.claims = 0
        self.kvitteringssvar = kvitteringssvar or (
            lambda k: _Svar(200, {"status": k.get("resultat", "utfort"),
                                  "oppdrag_id": 1}))
        naa = datetime.now(timezone.utc)
        self.frist = frist or _iso(naa + timedelta(minutes=15))
        self.kvittering_utloper = _iso(naa + timedelta(minutes=20))

    def post(self, sti, json=None, headers=None, **kw):
        if sti == "/v1/oppdrag/claim":
            self.claims += 1
            if self.tomt:
                return _Svar(204)
            kropp = {"oppdrag_id": 1, "tenant": TENANT,
                     "oppdragstype": "purring.send",
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
    from modules.m23_fordring import controller
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
    assert e["til"] == "kunde@nordvik.example"
    assert e["avsender_navn"] == "Fjordlys Elektro AS"
    assert e["svar_til"] == "regnskap@fjordlys.example"
    assert e["emne"] == "Påminnelse om faktura F-2026-17"
    assert "2 500,00 kr" in e["tekst"] and "2026-08-20" in e["tekst"]
    assert e["tekst"].rstrip().endswith("Fjordlys Elektro AS")
    assert len(k.kvitteringer) == 1
    kv = k.kvitteringer[0]
    assert kv["resultat"] == "utfort"
    assert kv["ressurs_id"] == "fordring:" + k.payload["fordring_id"]
    assert kv["trinn"] == 1 and kv["handling_trinn"] == "paaminnelse"
    assert kv["malversjon"] == "paaminnelse-v1"
    assert kv["melding_id"] == "<m1@fjordlys.example>"
    assert kv["mottaker_maske"] == "k****@nordvik.example"
    assert kv["sendt_ts"] and kv["sig"] == 1
    # Adressen står ALDRI i kvitteringen.
    assert "kunde@nordvik.example" not in str(kv)


@pytest.mark.parametrize("utforelse,kode", [
    ({"hindring": "mottaker_mangler"}, "mottaker_mangler"),
    ({"hindring": "fordring_avsluttet"}, "fordring_avsluttet"),
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
    k = _Klient(payload=_payload(handling_trinn="inkasso"))
    res, sender = _kjor(k)
    assert res["grunn"].startswith("oppdrag_ugyldig"), res
    assert sender.sendt == []
    assert k.kvitteringer[0]["feilkode"] == "oppdrag_ugyldig"
    k = _Klient(payload={"fordring_id": "x"})
    res, sender = _kjor(k)
    assert res["grunn"].startswith("oppdrag_ugyldig"), res
    assert sender.sendt == []


def test_smtp_feil_for_aksept_er_sending_avvist():
    k = _Klient()
    res, _ = _kjor(k, _Sender(feil=smtplib.SMTPRecipientsRefused({})))
    assert res["grunn"] == "sending_avvist", res
    assert k.kvitteringer[0]["feilkode"] == "sending_avvist"


def test_uvisst_utfall_er_terminalt_og_sender_aldri_igjen():
    k = _Klient()
    res, _ = _kjor(k, _Sender(feil=TimeoutError("midt i DATA")))
    assert res["grunn"] == "sending_uviss", res
    assert k.kvitteringer[0]["resultat"] == "feilet"
    assert k.kvitteringer[0]["feilkode"] == "sending_uviss"
    # Kvitteringen STENGER oppdraget: neste runde finner ingenting.
    k.tomt = True
    res2, sender2 = _kjor(k)
    assert res2["utfall"] == "tomt" and sender2.sendt == []


def test_flyttet_trinn_sendes_ikke():
    k = _Klient(utforelse=_utforelse(trinn=1, neste_trinn=2,
                                     handling_trinn="purring"))
    res, sender = _kjor(k)
    assert res["grunn"] == "trinn_flyttet", res
    assert sender.sendt == []


def test_lagret_uten_statusendring_er_ukvittert():
    k = _Klient(kvitteringssvar=lambda kv: _Svar(
        202, {"status": "lagret_uten_statusendring", "oppdrag_id": 1}))
    res, sender = _kjor(k)
    assert res["utfall"] == "ukvittert", res
    assert len(sender.sendt) == 1          # sendt ÉN gang, uansett


def test_utlopt_utforelsesfrist_sender_ikke():
    k = _Klient(frist=_iso(datetime.now(timezone.utc)
                           - timedelta(seconds=1)))
    res, sender = _kjor(k)
    assert res["grunn"] == "frist_utilstrekkelig", res
    assert sender.sendt == []


def test_fremmed_claimform_gir_kodet_nei_ikke_keyerror():
    """CodeRabbit på PR 4: en konvolutt uten feltene våre skal nå den
    kodede kvitteringen, ikke dø i en KeyError."""
    from modules.m23_fordring import controller

    class _K:
        def __init__(self):
            self.kvitteringer = []

        def post(self, sti, json=None, headers=None, **kw):
            if sti == "/v1/oppdrag/claim":
                return _Svar(200, {"oppdragstype": "kontroll.wcag.nettsted",
                                   "payload": {"mal_url": "https://x"}})
            self.kvitteringer.append(json)
            return _Svar(200, {"status": "feilet"})
    k = _K()
    res = controller.kjor_en(k, "tk", _Sender(), lambda x: x)
    assert res["grunn"] == "oppdragstype_ukjent", res
    assert k.kvitteringer[0]["feilkode"] == "oppdragstype_ukjent"
    res = controller.kjor_en(
        type("_L", (), {"post": lambda self, *a, **kw: _Svar(200, ["x"])})(),
        "tk", _Sender(), lambda x: x)
    assert res["grunn"] == "claim_uleselig", res


def test_ugyldig_trinn_sendes_ikke():
    for trinn in ("x", None, True, 2.5):
        k = _Klient(payload=_payload(trinn=trinn))
        res, sender = _kjor(k)
        assert res["grunn"].startswith("oppdrag_ugyldig"), (trinn, res)
        assert sender.sendt == []


def test_belop_som_ikke_er_belop_er_flettefeil():
    from modules.m23_fordring import maler
    for rest in (0, -5, None, "2500", True, 2.5):
        with pytest.raises(maler.Flettefeil) as e:
            maler.felter_fra(_utforelse(rest_ore=rest))
        assert "rest_ore" in e.value.kode, rest
    with pytest.raises(maler.Flettefeil):
        maler.felter_fra(_utforelse(gebyr_ore="70"))
    assert maler.felter_fra(_utforelse(gebyr_ore=None))["gebyr"] == ""
    # …og controlleren kvitterer malfeil uten å sende.
    k = _Klient(utforelse=_utforelse(rest_ore=0))
    res, sender = _kjor(k)
    assert res["grunn"].startswith("malfeil"), res
    assert sender.sendt == [] and k.kvitteringer[0]["feilkode"] == "malfeil"


def test_malene_fletter_alle_tre_trinn_og_aldri_inkasso():
    from modules.m23_fordring import maler
    felter = maler.felter_fra(_utforelse(gebyr_ore=7000))
    for h in ("paaminnelse", "purring", "inkassovarsel"):
        m = maler.flett(h, felter)
        assert "F-2026-17" in m["emne"] and "2 500,00" in m["tekst"], m
        assert "{" not in m["tekst"] and "{" not in m["emne"]
    assert "70,00" in maler.flett("purring", felter)["tekst"]
    assert "70,00" not in maler.flett("paaminnelse", felter)["tekst"]
    assert "inkasso" not in maler.MALER
    with pytest.raises(maler.Flettefeil):
        maler.flett("inkasso", felter)
    with pytest.raises(maler.Flettefeil) as e:
        maler.flett("purring", {**felter, "fakturanummer": " "})
    assert "fakturanummer" in e.value.kode
    assert maler.kroner(0) == "0,00" and maler.kroner(105) == "1,05"
