"""Porten for broen innboks → kundeserviceregister (migrasjon 203).

MÅLT I PRODUKSJON 15/9, og det er grunnen til at denne filen finnes: en
kunde hadde 18 innkomne e-poster og NULL henvendelser. M-6 hentet dem inn
og la dem i `epost_melding`; der ble de liggende. Hele kjeden videre —
sveipen som finner de oversette, klassifiseringen, svarutkastet og
utsendingen — sto ferdig i M-17 uten noe å jobbe på.

FIRE PORTER:

  1. En innkommen e-post BLIR en henvendelse, med kanalens egen id som
     `ekstern_ref`.
  2. Samme melding to ganger → ÉN henvendelse.
  3. EN FEIL I BROEN KOSTER ALDRI E-POSTEN. Runden committer én gang per
     SIDE, så en DATABASEFEIL fra broen ville forgiftet transaksjonen og
     rullet tilbake hele sidens meldinger — innhentingen ville tapt data
     fordi et ledd ETTER den feilet. Meldingen skal stå, og linjen skal
     si hvorfor.
  4. Kill-switch: `DISPONIT_EPOST_TIL_KUNDESERVICE=av` → e-posten hentes
     som før, ingen henvendelse.
  5. ÉN NORMALISERING, ikke to: broen bruker M-17s egen `_avsenderhash`.

TRE MUTASJONER PRØVD FØRST, OG DE FALT IKKE — de står her fordi hver av
dem lærte noe om hva porten faktisk kan måle:

  * «fjern savepointet» falt ikke, fordi feilinjeksjonen var en REN
    PYTHON-FEIL. Den forgifter ingen transaksjon, og savepointet var
    derfor uten betydning. Port 3 injiserer nå en DATABASEFEIL, og DA
    faller den — det er den eneste feilen savepointet finnes for.
  * «ikke-deterministisk henvendelse-id» falt ikke, og skal ikke det:
    idempotensen er DØRENS, ikke broens. `henvendelse_inntak_unik` på
    (tenant, kanal, ekstern_ref) fanger den andre innlesingen uansett
    hvilken id kalleren utleder. Port 2 måler at broen ikke DEFEATER
    den garantien — ikke at broen lager den.
  * «bruk M-6s `_hash` i stedet for M-17s `_avsenderhash`» falt ikke,
    fordi de to funksjonene er MÅLT IDENTISKE i dag (begge sha256 over
    trim+lowercase). En verdisammenligning kan derfor aldri skille dem.
    Port 5 måler i stedet KOBLINGEN statisk: broen skal IMPORTERE
    M-17s funksjon, så den følger med den dagen M-17 endrer sin
    normalisering. Det er invarianten som faktisk betyr noe.
"""
import os
import secrets

import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT,  # noqa: F401
                       app, klient, migrator, miljo, pg, token)
from .test_m37 import _sett_kontekst

PLAN_DSN = os.environ.get("DISPONIT_TEST_PLAN_DSN")
pg_plan = pytest.mark.skipif(not (DSN and PLAN_DSN),
                             reason="DISPONIT_TEST_PLAN_DSN ikke satt")

REFRESH = "refresh-" + secrets.token_hex(8)
ADRESSE = "Kunde-" + secrets.token_hex(3) + "@Nordvik.Example"
EMNE = "Spørsmål om faktura " + secrets.token_hex(2)
KROPP = "Hei, jeg lurer på om fakturaen er betalt. Hilsen Kari"


def _pa():
    from db.pg import koble
    return koble(PLAN_DSN)


def _kilde(m):
    from db import kryptering
    _sett_kontekst(m, TENANT)
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, TENANT)
    ct, nonce = kryptering.krypter(dek, {"refresh_token": REFRESH},
                                   TENANT, key_id)
    kid = m.execute(
        "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
        " auth_kryptert, nonce, key_id, status) VALUES"
        " (%s,'m365',%s,%s,%s,%s,'aktiv') RETURNING kilde_id",
        (TENANT, f"postboks-{secrets.token_hex(4)}@example.org",
         ct, nonce, key_id)).fetchone()[0]
    m.commit()
    return kid


def _m365(monkeypatch, *, bro=True):
    monkeypatch.setenv("DISPONIT_M365_CLIENT_ID", "klient-id")
    monkeypatch.setenv("DISPONIT_M365_CLIENT_SECRET", "hemmelig")
    monkeypatch.setenv("DISPONIT_M365_TENANT", "common")
    monkeypatch.delenv("DISPONIT_EPOST_INNTAK", raising=False)
    if bro:
        monkeypatch.delenv("DISPONIT_EPOST_TIL_KUNDESERVICE", raising=False)
    else:
        monkeypatch.setenv("DISPONIT_EPOST_TIL_KUNDESERVICE", "av")


def _veksler(sett):
    def veksler(konfig, refresh):
        sett["refresh"] = refresh
        return {"access_token": "kortlivet-" + secrets.token_hex(4)}
    return veksler


def _melding(mid):
    return {"id": mid, "conversationId": "c-1",
            "receivedDateTime": "2026-09-15T10:00:00Z",
            "from": {"emailAddress": {"address": ADRESSE, "name": "Kari"}},
            "toRecipients": [
                {"emailAddress": {"address": "post@fjordlys.example"}}],
            "subject": EMNE, "bodyPreview": KROPP[:40],
            "hasAttachments": False}


def _graf_for(sider):
    def graf(access, url, *, tekstkropp=False):
        if "/me/messages/" in url:
            return {"body": {"contentType": "text", "content": KROPP}}
        return sider.pop(0) if sider else {"value": [],
                                           "@odata.deltaLink": "x"}
    return graf


def _kjor(app, sider, monkeypatch, **kw):
    from plan.epost import kjor_en_runde
    pa = _pa()
    try:
        return kjor_en_runde(app.tjeneste, pa, graf=_graf_for(sider),
                             veksler=_veksler({}), **kw)
    finally:
        pa.close()


def _side(meldinger):
    return [{"value": meldinger,
             "@odata.deltaLink": "d-" + secrets.token_hex(3)}]


def _henvendelser(m, ref):
    _sett_kontekst(m, TENANT)
    r = m.execute(
        "SELECT henvendelse_id, kanal, ekstern_ref, avsender_hash"
        "  FROM henvendelse WHERE tenant=%s AND ekstern_ref=%s",
        (TENANT, ref)).fetchall()
    m.rollback()
    return r


def _meldinger(m, mid):
    _sett_kontekst(m, TENANT)
    r = m.execute(
        "SELECT melding_id FROM epost_melding"
        " WHERE tenant=%s AND leverandor_melding_id=%s",
        (TENANT, mid)).fetchall()
    m.rollback()
    return r


@pg_plan
def test_innkommen_epost_blir_en_henvendelse(migrator, miljo, app,
                                             monkeypatch):
    """PORT 1. Og avsenderhashen er M-17s EGEN — en kopi av formen ville
    normalisert ulikt, og den samme kunden blitt to i registeret."""
    from api.kundeservice import _avsenderhash
    _m365(monkeypatch)
    _kilde(migrator)
    mid = "AAMk-" + secrets.token_hex(6)
    _kjor(app, _side([_melding(mid)]), monkeypatch)

    rader = _henvendelser(migrator, mid)
    assert len(rader) == 1, f"e-posten ble ikke en henvendelse: {rader}"
    _hid, kanal, ref, avs = rader[0]
    assert kanal == "epost"
    assert ref == mid, "ekstern_ref er ikke leverandørens melding-id"
    # NORMALISERINGEN: adressen i settet har store bokstaver med vilje —
    # hashen skal være den av den SMÅSKREVNE, ellers blir «Kunde@» og
    # «kunde@» to kunder.
    assert avs == _avsenderhash(ADRESSE), \
        "avsenderhashen er ikke M-17s egen normalisering"
    assert avs != _avsenderhash(ADRESSE + "x"), "hashen skiller ikke"


@pg_plan
def test_samme_melding_to_ganger_blir_en_henvendelse(migrator, miljo, app,
                                                     monkeypatch):
    """PORT 2. Den samme innboksen leses to ganger — det er ikke et
    kantfall, det er det som faktisk skjer hver runde."""
    _m365(monkeypatch)
    _kilde(migrator)
    mid = "AAMk-" + secrets.token_hex(6)
    _kjor(app, _side([_melding(mid)]), monkeypatch)
    _kjor(app, _side([_melding(mid)]), monkeypatch)

    assert len(_henvendelser(migrator, mid)) == 1, "to henvendelser av én"
    assert len(_meldinger(migrator, mid)) == 1, "to meldinger av én"


@pg_plan
def test_feil_i_broen_koster_aldri_epostene(migrator, miljo, app,
                                            monkeypatch, capsys):
    """PORT 3 — den viktigste.

    Runden committer én gang per SIDE. Uten savepointet rundt broen ville
    et unntak herfra rullet tilbake HELE sidens meldinger, og
    delta-cursoren gått videre uansett: e-postene ville vært tapt for
    godt fordi et ledd ETTER innhentingen feilet.
    """
    import plan.epost as epostmodul
    _m365(monkeypatch)
    _kilde(migrator)
    a, b = "AAMk-" + secrets.token_hex(6), "AAMk-" + secrets.token_hex(6)

    # EN DATABASEFEIL, ikke en Python-feil. Første utkast reiste en
    # `RuntimeError` før noen SQL ble kjørt — transaksjonen var da helt
    # frisk, og porten besto selv med savepointet FJERNET. Det er
    # nøyaktig den forgiftede transaksjonen savepointet finnes for, så
    # det er den som må injiseres.
    def sprekk(conn, *_a, **_k):
        conn.execute("SELECT 1 / 0")
    monkeypatch.setattr(epostmodul, "_til_kundeservice", sprekk)
    _kjor(app, _side([_melding(a), _melding(b)]), monkeypatch)

    # BEGGE e-postene står — hele sidens data overlevde.
    assert len(_meldinger(migrator, a)) == 1, "melding A gikk tapt"
    assert len(_meldinger(migrator, b)) == 1, "melding B gikk tapt"
    assert _henvendelser(migrator, a) == []
    # …og feilen er SYNLIG, med typenavnet og aldri innholdet.
    ut = capsys.readouterr().out + capsys.readouterr().err
    assert "epost_til_kundeservice_feilet" in ut, \
        "broen feilet i stillhet"
    assert EMNE not in ut and ADRESSE not in ut, \
        "loggen bar emne eller adresse"


@pg_plan
def test_kill_switch_stopper_broen_men_ikke_innhentingen(migrator, miljo,
                                                         app, monkeypatch):
    """PORT 4. Bryteren skal stoppe broen ALENE — en bryter som også
    stopper innhentingen ville gjort e-post utilgjengelig for å slå av
    en videreføring."""
    _m365(monkeypatch, bro=False)
    _kilde(migrator)
    mid = "AAMk-" + secrets.token_hex(6)
    _kjor(app, _side([_melding(mid)]), monkeypatch)

    assert len(_meldinger(migrator, mid)) == 1, "innhentingen stoppet også"
    assert _henvendelser(migrator, mid) == [], "broen gikk med bryteren av"


def test_broen_bruker_m17s_egen_normalisering():
    """PORT 5 — STATISK, og det er med vilje.

    `_avsenderhash` (M-17) og `_hash` (M-6) er MÅLT IDENTISKE i dag:
    begge er sha256 over trim + lowercase. En verdisammenligning kan
    derfor ikke skille dem, og en port som «beviser» at riktig funksjon
    brukes ved å sammenligne hasher, beviser ingenting.

    Det som faktisk betyr noe er KOBLINGEN: broen skal hente funksjonen
    fra M-17, så den følger med den dagen M-17 endrer sin normalisering.
    Gjør den ikke det, blir den samme kunden to i registeret — og det
    ville skjedd stille, lenge etter at noen endret M-17.
    """
    from pathlib import Path
    kilde = (Path(__file__).resolve().parents[1] / "plan" / "epost.py"
             ).read_text(encoding="utf-8")
    assert "from api.kundeservice import" in kilde
    for navn in ("_avsenderhash", "_avsendermaske", "_krypter", "_utled"):
        assert navn in kilde, f"broen henter ikke {navn} fra M-17"
    # …og den hasher IKKE avsenderen selv i kallet til inntaksdøra.
    bro = kilde[kilde.index("def _til_kundeservice"):
                kilde.index("def _lagre")]
    assert "_hash(fra)" not in bro, \
        "broen bruker M-6s egen hash i stedet for M-17s"
