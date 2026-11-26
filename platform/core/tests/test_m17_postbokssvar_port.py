"""Porten for 231: svaret går fra postboksen saken kom inn på.

Funnet i drift 26.11.2026: en sak som kom inn på en tilkoblet Gmail ble
besvart fra plattformens egen adresse.

  1. Køen sier hvilken postboks en sak kan besvares fra — bare for en
     aktiv kilde som kan sende.
  2. Svaret fra saken legges i POSTBOKSENS kø, i én transaksjon med
     koblingen; registerets utkast merkes brukt, ingen husets bestilling.
  3. Saken lukkes FØRST når svaret er ute — som besvart, av den som sendte,
     med postboksen i sporet. Feiler sendingen, står saken åpen og sporet
     sier hvorfor.
  4. «Sendte svar» viser begge utfall, med adressen.
  5. En sak uten en slik postboks får 409 — den har husets vei.

MUTASJONER SOM DREPER DENNE: lukk saken i koblingsdøra (3), dropp
`kan_svare` i oppslaget (1), la triggeren reagere på alle utkast (3).
"""
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst
from .test_m6_imap_port import _Postboks, _imap_kilde, _raa
from .test_m6_inntak_port import _m365, _pa, pg_plan

SCOPES = ("decisions:read", "kundeservice:innhold", "bestilling:opprett",
          "epost:read", "epost:utkast:behandle")


def _hode(tok):
    return {"authorization": f"Bearer {tok}",
            "Idempotency-Key": "ps-" + secrets.token_hex(8)}


def _sak(klient, tok, emne):
    koe = klient.get("/v1/kundeservice",
                     headers={"authorization": f"Bearer {tok}"}).json()["koe"]
    mine = [r for r in koe if r.get("emne") == emne]
    assert mine, f"saken «{emne}» kom ikke i køen"
    return mine[0]


def _henvendelse(m, hid):
    _sett_kontekst(m, TENANT)
    r = m.execute("SELECT lukket_utfall, lukket_av FROM henvendelse"
                  " WHERE tenant=%s AND henvendelse_id=%s",
                  (TENANT, hid)).fetchone()
    s = m.execute("SELECT handling, aktor, detalj FROM henvendelsesspor"
                  " WHERE tenant=%s AND henvendelse_id=%s ORDER BY spor_id",
                  (TENANT, hid)).fetchall()
    m.rollback()
    return r, s


@pg_plan
def test_svaret_gaar_fra_postboksen_og_saken_lukkes_naar_det_er_ute(
        migrator, miljo, app, klient, token, monkeypatch):
    from plan.epost_imap import ImapAvvist, kjor_en_runde
    _m365(monkeypatch)
    kid, postboks, _dek = _imap_kilde(migrator)
    boks = _Postboks()
    pa = _pa()
    tok, bid = token(rolle="admin", scopes=SCOPES)
    sendt = []

    def sender(tjeneste, bruker, passord, melding):
        sendt.append((bruker, melding))
    try:
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(), sender=sender)
        emne = "Ordre " + secrets.token_hex(4)
        boks.legg_til(_raa(emne=emne, mid=f"<{secrets.token_hex(6)}@k.example>"))
        emne2 = "Retur " + secrets.token_hex(4)
        boks.legg_til(_raa(emne=emne2, mid=f"<{secrets.token_hex(6)}@k.example>"))
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(), sender=sender)

        # 1. Køen vet hvor svaret kan gå fra.
        sak = _sak(klient, tok, emne)
        assert sak["svarpostboks"] == postboks
        hid = sak["henvendelse_id"]

        # 2. Svaret legges i postboksens kø.
        r = klient.post(
            f"/v1/kundeservice/henvendelse/{hid}/svar-fra-postboks",
            json={"tekst": "Takk, vi sender i dag."}, headers=_hode(tok))
        assert r.status_code == 200, r.text
        assert r.json()["postboks"] == postboks
        assert r.json()["status"] == "sendes"
        (lukket, _av), spor = _henvendelse(migrator, hid)
        assert lukket is None, "saken ble lukket før svaret var ute"
        assert [s[0] for s in spor][-1] == "svar.til_postboks"
        _sett_kontekst(migrator, TENANT)
        status = migrator.execute(
            "SELECT status FROM svarutkast WHERE tenant=%s"
            " AND henvendelse_id=%s", (TENANT, hid)).fetchall()
        bestilt = migrator.execute(
            "SELECT count(*) FROM svarbestilling WHERE tenant=%s"
            " AND henvendelse_id=%s", (TENANT, hid)).fetchone()[0]
        migrator.rollback()
        assert [x[0] for x in status] == ["brukt_manuelt"]
        assert bestilt == 0, "husets vei ble bestilt i tillegg"

        # 3. Bakgrunnsprosessen sender — fra postboksen, i tråden.
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(), sender=sender)
        assert len(sendt) == 1
        bruker, melding = sendt[0]
        assert bruker == postboks and melding["From"] == postboks
        assert melding["Subject"] == f"Re: {emne}"
        assert "Takk, vi sender i dag." in melding.get_content()
        (lukket, av), spor = _henvendelse(migrator, hid)
        assert lukket == "besvart"
        assert av == bid or av.endswith(bid) or bid in av, (av, bid)
        siste = spor[-1]
        assert siste[0] == "svar.sendt"
        assert siste[2]["via"] == "postboks"
        assert siste[2]["postboks"] == postboks
        assert siste[2]["lukket"] is True

        # 3b. Feiler sendingen, står saken åpen og sporet sier hvorfor.
        sak2 = _sak(klient, tok, emne2)
        hid2 = sak2["henvendelse_id"]
        r = klient.post(
            f"/v1/kundeservice/henvendelse/{hid2}/svar-fra-postboks",
            json={"tekst": "Vi ser på returen."}, headers=_hode(tok))
        assert r.status_code == 200, r.text

        def avvist(*a):
            raise ImapAvvist("nei")
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(), sender=avvist)
        (lukket2, _), spor2 = _henvendelse(migrator, hid2)
        assert lukket2 is None
        assert spor2[-1][0] == "svar.feilet"
        assert spor2[-1][2]["grunn"] == "sendetilgang_avvist"

        # 4. «Sendte svar» viser begge, med adressen.
        d = klient.get("/v1/kundeservice/sendte",
                       headers={"authorization": f"Bearer {tok}"}).json()
        mine = {(x["henvendelse_id"], x["handling"]): x for x in d["sendte"]
                if x["henvendelse_id"] in (hid, hid2)}
        assert mine[(hid, "svar.sendt")]["postboks"] == postboks
        assert mine[(hid, "svar.sendt")]["emne"] == emne
        assert mine[(hid2, "svar.feilet")]["grunn"] == "sendetilgang_avvist"
        assert (hid, "svar.til_postboks") in mine

        # Sporet per sak sier det samme, med postboksen.
        sp = klient.get(f"/v1/kundeservice/henvendelse/{hid}/spor",
                        headers={"authorization": f"Bearer {tok}"}).json()
        assert sp["spor"][-1]["postboks"] == postboks

        # 2b. En lukket sak tar ikke imot et nytt svar.
        r = klient.post(
            f"/v1/kundeservice/henvendelse/{hid}/svar-fra-postboks",
            json={"tekst": "En gang til."}, headers=_hode(tok))
        assert r.status_code == 409, r.text
    finally:
        pa.close()


@pg_plan
def test_uten_postboks_som_kan_sende_er_veien_husets(
        migrator, miljo, app, klient, token, monkeypatch):
    """En kilde som bare kan LESE gir ingen postboksvei — og et kall likevel
    er 409, ikke en sending fra feil sted."""
    from api.epost_kilde import IMAP_SCOPE  # noqa: F401
    from plan.epost_imap import kjor_en_runde
    _m365(monkeypatch)
    kid, postboks, _dek = _imap_kilde(migrator)
    _sett_kontekst(migrator, TENANT)
    migrator.execute("UPDATE epost_kilde SET scope='imap' WHERE tenant=%s"
                     " AND kilde_id=%s", (TENANT, kid))
    migrator.commit()
    boks = _Postboks()
    pa = _pa()
    tok, _ = token(rolle="admin", scopes=SCOPES)
    try:
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                      sender=lambda *a: None)
        emne = "Lesende " + secrets.token_hex(4)
        boks.legg_til(_raa(emne=emne, mid=f"<{secrets.token_hex(6)}@k.example>"))
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                      sender=lambda *a: None)
        sak = _sak(klient, tok, emne)
        assert sak["svarpostboks"] is None
        r = klient.post(
            f"/v1/kundeservice/henvendelse/{sak['henvendelse_id']}"
            "/svar-fra-postboks", json={"tekst": "Hei"}, headers=_hode(tok))
        assert r.status_code == 409, r.text
        (lukket, _), spor = _henvendelse(migrator, sak["henvendelse_id"])
        assert lukket is None
        assert "svar.til_postboks" not in [s[0] for s in spor]
    finally:
        pa.close()


@pg
def test_svaret_krever_e_postscopet(klient, token):
    tok, _ = token(rolle="leser", scopes=("decisions:read",
                                          "bestilling:opprett"))
    r = klient.post(
        "/v1/kundeservice/henvendelse/"
        "00000000-0000-0000-0000-000000000001/svar-fra-postboks",
        json={"tekst": "Hei"}, headers=_hode(tok))
    assert r.status_code == 403, r.text
