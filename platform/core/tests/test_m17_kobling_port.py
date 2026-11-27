"""Porten for koblingen: saken koblet til det firmaet alt vet.

EIERS VEDTAK 27/11: «Bare kobling, ingen modell». Å lese e-post er ikke
noe en kunde betaler for — det gjør Gmail. Det Gmail ikke vet, er hvem
avsenderen er i firmaets egne registre og hva som står åpent mot henne.

  1. Avsenderen er kontakt på en kunde → saken vet hvem kunden er.
  2. Kundens ÅPNE fakturaer kommer med; en betalt gjør det ikke.
  3. Et fakturanummer i teksten kobler fakturaen selv uten kunde — og
     står først, fordi det er den kunden skriver om.
  4. Det som vises, følger brukerens EGNE scoper: uten `okonomi:read`
     ingen fakturaer, og «ikke undersøkt» sies, ikke «ingen».
  5. Et kort tall i løpende tekst («104 kr») kobler ingenting.

MUTASJONER SOM DREPER DENNE: dropp statusfilteret (2), dropp sorteringen
(3), dropp scopesjekken (4), dropp nøkkelordkravet (5).
"""
import secrets
import uuid

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, dekker, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst
from .test_m6_imap_port import _Postboks, _imap_kilde, _raa
from .test_m6_inntak_port import _m365, _pa, pg_plan


def test_kandidatnumrene_er_reglene_og_ikke_mer():
    from api.kundeservice_kobling import kandidatnumre
    n = kandidatnumre("Hei! Gjelder faktura 104 og F-2026-0042. "
                      "Det koster 104 kr, vi kommer kl 930. Ordre 55123.")
    assert "f-2026-0042" in n
    assert "104" in n, "«faktura 104» skal telle"
    assert "55123" in n, "fire+ siffer teller uten nøkkelord"
    assert "930" not in n, "«kl 930» er ikke et fakturanummer"
    assert "hei" not in n and "og" not in n
    # Uten nøkkelord foran er et kort tall ingenting.
    assert kandidatnumre("Det koster 104 kr") == set()
    assert "104" in kandidatnumre("Ang. #104")


def _nr():
    """Et fakturanummer med SIFFER, alltid. `token_hex` kan gi «daaebf» —
    uten et eneste siffer, og da er det (med rette) ikke et nummer."""
    return f"F-{secrets.randbelow(900000) + 100000}"


def _hode(tok):
    return {"authorization": f"Bearer {tok}"}


def _api():
    """Registerets dører er API-rollens, ikke migratorens — samme
    kobling som `test_fordring_part_port` seeder gjennom."""
    from db.pg import koble
    return koble(DSN)


def _kunde_med_adresse(adresse):
    """En kunde i partsregisteret med avsenderen som e-postkontakt —
    gjennom registerets egne dører, slik flaten gjør det."""
    ref = "K-" + secrets.token_hex(3)
    m = _api()
    _sett_kontekst(m, TENANT)
    pid = m.execute("SELECT part_registrer(%s,%s,'Nordvik Bygg AS',NULL,"
                    "'bedrift','kari')", (TENANT, ref)).fetchone()[0]
    psn = m.execute("SELECT tenant_pseudonym(%s,%s)",
                    (TENANT, adresse)).fetchone()[0]
    m.execute("SELECT part_sett_kontakt(%s,%s,'epost','pe**@nordvik.example',"
              "%s,%s,'k1',%s,true,NULL,'kari')",
              (TENANT, pid, b"\x01\x02", bytes(range(12)), psn))
    m.commit()
    m.close()
    return ref, pid


def _faktura(ref, nr, *, betalt=False):
    fid = uuid.uuid4()
    m = _api()
    _sett_kontekst(m, TENANT)
    m.execute("SELECT m23_registrer_fordring(%s,%s,%s,%s,450000,"
              " current_date - 40, current_date - 12, 'kari')",
              (TENANT, fid, ref, nr))
    if betalt:
        # Gjennom modulens egen betalingsdør — full betaling lukker den.
        m.execute("SELECT m23_registrer_betaling(%s,%s,%s,450000,"
                  " current_date,'kari')", (TENANT, uuid.uuid4(), fid))
    m.commit()
    m.close()
    return fid


def _sak(klient, tok, emne):
    koe = klient.get("/v1/kundeservice", headers=_hode(tok)).json()["koe"]
    mine = [r for r in koe if r.get("emne") == emne]
    assert mine, f"saken «{emne}» kom ikke i køen"
    return mine[0]["henvendelse_id"]


@pg_plan
def test_saken_vet_hvem_kunden_er_og_hva_som_staar_aapent(
        migrator, miljo, app, klient, token, monkeypatch):
    from plan.epost_imap import kjor_en_runde
    _m365(monkeypatch)
    _kid, _postboks, _dek = _imap_kilde(migrator)
    adresse = f"per-{secrets.token_hex(3)}@nordvik.example"
    ref, _pid = _kunde_med_adresse(adresse)
    apen = _nr()
    betalt = _nr()
    _faktura(ref, apen)
    _faktura(ref, betalt, betalt=True)
    # En faktura på en ANNEN kunde, nevnt med nummer i teksten.
    fremmed = _nr()
    _faktura("K-annen-" + secrets.token_hex(2), fremmed)

    boks = _Postboks()
    pa = _pa()
    alle = ("decisions:read", "kundeservice:innhold", "part:read",
            "okonomi:read")
    tok, _ = token(rolle="admin", scopes=alle)
    try:
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                      sender=lambda *a: None)
        emne = "Spørsmål " + secrets.token_hex(3)
        boks.legg_til(_raa(fra=f"Per Hansen <{adresse}>", emne=emne,
                           tekst=f"Hei, gjelder faktura {fremmed}. "
                                 "Det er 104 kr for mye."))
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                      sender=lambda *a: None)
        hid = _sak(klient, tok, emne)

        r = klient.get(f"/v1/kundeservice/henvendelse/{hid}/koblinger",
                       headers=_hode(tok))
        assert r.status_code == 200, r.text
        d = r.json()
        # 1. Kunden, og hvorfor.
        assert d["kunde"]["navn"] == "Nordvik Bygg AS"
        assert d["kunde"]["grunn"] == "avsender"
        nr = [f["fakturanummer"] for f in d["fakturaer"]]
        # 3. Nummeret i teksten FØRST, selv om fakturaen er en annens.
        assert nr[0] == fremmed
        assert d["fakturaer"][0]["grunn"] == "nummer"
        # 2. Kundens åpne faktura med; den betalte ikke.
        assert apen in nr
        assert betalt not in nr
        f = next(x for x in d["fakturaer"] if x["fakturanummer"] == apen)
        assert f["grunn"] == "kunde" and f["rest_ore"] == 450000
        assert f["dogn_over_forfall"] >= 12
        assert d["sett"] == {"kunde": True, "okonomi": True}
        # Adressen selv sendes aldri tilbake.
        assert adresse not in r.text

        # 4. Uten okonomi:read: ingen fakturaer, og det sies.
        tok2, _ = token(rolle="leser", scopes=(
            "decisions:read", "kundeservice:innhold", "part:read"))
        d2 = klient.get(f"/v1/kundeservice/henvendelse/{hid}/koblinger",
                        headers=_hode(tok2)).json()
        assert d2["fakturaer"] == [] and d2["sett"]["okonomi"] is False
        assert d2["kunde"]["navn"] == "Nordvik Bygg AS"

        # …og med bare innholdet: ingenting undersøkt, og det sies.
        tok3, _ = token(rolle="leser", scopes=(
            "decisions:read", "kundeservice:innhold"))
        d3 = klient.get(f"/v1/kundeservice/henvendelse/{hid}/koblinger",
                        headers=_hode(tok3)).json()
        assert d3["kunde"] is None and d3["fakturaer"] == []
        assert d3["sett"] == {"kunde": False, "okonomi": False}
        assert "Nordvik" not in str(d3)

        # Innholdsscopet er døra.
        tok4, _ = token(rolle="leser", scopes=("decisions:read",))
        assert klient.get(f"/v1/kundeservice/henvendelse/{hid}/koblinger",
                          headers=_hode(tok4)).status_code == 403
    finally:
        pa.close()


@pg
def test_ukjent_sak_er_404(klient, token):
    tok, _ = token(rolle="admin", scopes=("kundeservice:innhold",))
    r = klient.get(f"/v1/kundeservice/henvendelse/{uuid.uuid4()}/koblinger",
                   headers=_hode(tok))
    assert r.status_code == 404, r.text


@pg_plan
def test_ukjent_avsender_legges_til_kunde_fra_saken(
        migrator, miljo, app, klient, token, monkeypatch):
    """DEN SISTE MILEN: «avsenderen er ikke registrert» er ingen blindvei.
    Ett kall fra saken, og NESTE lesing kjenner kunden — uten at adressen
    noen gang gikk via nettleseren.

    Ny kunde: opprettes med navnet, adressen som primær kontakt, og et nytt
    kall gir den SAMME kunden. Eksisterende kunde: adressen blir et ekstra
    kontaktpunkt, ALDRI det primære (purringene skal ikke flytte seg)."""
    from plan.epost_imap import kjor_en_runde
    _m365(monkeypatch)
    _imap_kilde(migrator)
    boks = _Postboks()
    pa = _pa()
    tok, _ = token(rolle="admin", scopes=(
        "decisions:read", "kundeservice:innhold", "part:read",
        "part:administrer", "okonomi:read"))
    try:
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                      sender=lambda *a: None)
        a1 = f"ny-{secrets.token_hex(3)}@fjordvik.example"
        a2 = f"ansatt-{secrets.token_hex(3)}@nordvik.example"
        e1, e2 = ("Hei " + secrets.token_hex(3), "Hallo " + secrets.token_hex(3))
        boks.legg_til(_raa(fra=f"Siri Fjord <{a1}>", emne=e1))
        boks.legg_til(_raa(fra=f"Ola Ansatt <{a2}>", emne=e2))
        kjor_en_runde(app.tjeneste, pa, kobler=boks.kobler(),
                      sender=lambda *a: None)
        h1, h2 = _sak(klient, tok, e1), _sak(klient, tok, e2)

        d = klient.get(f"/v1/kundeservice/henvendelse/{h1}/koblinger",
                       headers=_hode(tok)).json()
        assert d["kunde"] is None and d["kan_kobles"] is True
        assert d["avsender_navn"] == "Siri Fjord"

        def koble(hid, kropp):
            return klient.post(
                f"/v1/kundeservice/henvendelse/{hid}/avsender-til-kunde",
                json=kropp, headers={**_hode(tok), "Idempotency-Key":
                                     "ak-" + secrets.token_hex(8)})

        navn = "Fjordvik " + secrets.token_hex(2) + " AS"
        r = koble(h1, {"navn": navn})
        assert r.status_code == 200, r.text
        assert r.json()["ny"] is True
        assert a1 not in r.text
        pid = r.json()["part_id"]
        d = klient.get(f"/v1/kundeservice/henvendelse/{h1}/koblinger",
                       headers=_hode(tok)).json()
        assert d["kunde"]["navn"] == navn and d["kan_kobles"] is False
        # Et nytt klikk gir samme kunde, ikke en tvilling.
        r2 = koble(h1, {"navn": navn})
        assert r2.status_code == 200, r2.text
        assert r2.json()["part_id"] == pid

        # Eksisterende kunde: ekstra kontakt, ikke primær.
        ref, eksisterende = _kunde_med_adresse(
            f"faktura-{secrets.token_hex(3)}@nordvik.example")
        r = koble(h2, {"part_id": str(eksisterende)})
        assert r.status_code == 200, r.text
        assert r.json()["ny"] is False
        _sett_kontekst(migrator, TENANT)
        prim = migrator.execute(
            "SELECT primar FROM partkontakt WHERE tenant=%s AND part_id=%s"
            " ORDER BY opprettet", (TENANT, eksisterende)).fetchall()
        migrator.rollback()
        assert [p[0] for p in prim] == [True, False], prim
        d = klient.get(f"/v1/kundeservice/henvendelse/{h2}/koblinger",
                       headers=_hode(tok)).json()
        assert d["kunde"]["part_ref"] == ref

        # Uten part:administrer: 403, og ingenting skrevet.
        tok2, _ = token(rolle="leser", scopes=("kundeservice:innhold",
                                               "part:read"))
        r = klient.post(
            f"/v1/kundeservice/henvendelse/{h1}/avsender-til-kunde",
            json={"navn": "X"}, headers={**_hode(tok2), "Idempotency-Key":
                                         "ak-" + secrets.token_hex(8)})
        assert r.status_code == 403, r.text
    finally:
        pa.close()
