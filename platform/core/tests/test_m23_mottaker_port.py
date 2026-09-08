"""Porten mot 146 (ARC B, PR 1): fordringen vet hvem purringen skal til.

Adressen lagres aldri i klartekst: API-laget krypterer den med tenantens
DEK, basen holder chiffertekst + nonce + nøkkel-id, og flaten og loggen
ser bare masken. Den kan settes ved registrering eller senere, og rettes
så lenge fordringen er åpen.

Fem ting måles mot ekte base:
  1. HTTP: registrering med `mottaker_epost` → 200 med maske; listen
     bærer masken; adressen står ikke i noe svar.
  2. HTTP: sett/rett mottaker senere → 200; ugyldig adresse → 400 med
     detalj som navngir feltet, uten adressen.
  3. Døra: chifferteksten dekrypteres til adressen med tenantens DEK
     (det utføreren i PR 4 skal gjøre), og evidensraden bærer masken.
  4. Døra: en betalt fordring får ikke ny mottaker; vakten nekter direkte
     UPDATE uten aktør.
  5. Kilden: fordring.py importerer fortsatt ingen SMTP-klient.

MUTASJONEN SOM DREPER DENNE: lagre adressen som maske uten chiffertekst.
"""
import re
import secrets
import uuid
from pathlib import Path

import psycopg
import pytest

from .test_api import (DSN, MIGRATOR_DSN, TENANT, pg,  # noqa: F401
                       app, klient, migrator, miljo, token)
from .test_m23_fordring import _betal, _fordring, _plan, _rt, _tenantnavn
from .test_m37 import _sett_kontekst

API = Path(__file__).resolve().parents[1] / "api"
ADRESSE = "Regnskap@Nordvik-AS.no"


def _post(klient, tok, sti, kropp):
    return klient.post(sti, json=kropp,
                       headers={"authorization": f"Bearer {tok}",
                                "Idempotency-Key": secrets.token_urlsafe(24)})


@pg
def test_registrering_med_mottaker_gir_maske_aldri_adressen(miljo, migrator,
                                                            klient, token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    nr = "F-146-" + secrets.token_hex(3)
    r = _post(klient, tok, "/v1/fordring",
              {"kunde_ref": "Nordvik AS", "fakturanummer": nr,
               "belop_ore": 250000, "utstedt": "2026-08-01",
               "forfall": "2026-08-15", "mottaker_epost": ADRESSE})
    assert r.status_code == 200, r.text
    k = r.json()
    assert k["mottaker_maske"] == "r****@nordvik-as.no", k
    assert "nordvik-as.no" in r.text and "regnskap@" not in r.text.lower()
    fid = k["fordring_id"]
    r = klient.get("/v1/fordring", headers={"authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    rad = next(f for f in r.json()["fordringer"] if f["fordring_id"] == fid)
    assert rad["mottaker_maske"] == "r****@nordvik-as.no"
    assert "regnskap@" not in r.text.lower(), "adressen lekket i listen"
    # Uten mottaker: masken er null, ikke en tom streng.
    r = _post(klient, tok, "/v1/fordring",
              {"kunde_ref": "Nordvik AS", "fakturanummer": nr + "b",
               "belop_ore": 1000, "utstedt": "2026-08-01",
               "forfall": "2026-08-15"})
    assert r.status_code == 200 and r.json()["mottaker_maske"] is None, r.text


@pg
def test_mottaker_settes_og_rettes_senere_og_ugyldig_navngis(miljo, migrator,
                                                            klient, token):
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    nr = "F-146-" + secrets.token_hex(3)
    fid = _post(klient, tok, "/v1/fordring",
                {"kunde_ref": "Nordvik AS", "fakturanummer": nr,
                 "belop_ore": 250000, "utstedt": "2026-08-01",
                 "forfall": "2026-08-15"}).json()["fordring_id"]
    r = _post(klient, tok, f"/v1/fordring/{fid}/mottaker",
              {"mottaker_epost": "okonomi@nordvik-as.no"})
    assert r.status_code == 200 and r.json()["endret"] is True, r.text
    assert r.json()["mottaker_maske"] == "o****@nordvik-as.no"
    # Rettes: ny maske, endret=True. Samme adresse igjen: stille ja.
    r = _post(klient, tok, f"/v1/fordring/{fid}/mottaker",
              {"mottaker_epost": "faktura@nordvik-as.no"})
    assert r.status_code == 200 and r.json()["endret"] is True, r.text
    r = _post(klient, tok, f"/v1/fordring/{fid}/mottaker",
              {"mottaker_epost": "FAKTURA@nordvik-as.no "})
    assert r.status_code == 200 and r.json()["endret"] is False, r.text
    # SAMME MASKE, ANNEN ADRESSE: «f2@…» maskeres likt som «faktura@…» —
    # og skal likevel lagres (likhet måles på hashen, ikke masken).
    r = _post(klient, tok, f"/v1/fordring/{fid}/mottaker",
              {"mottaker_epost": "f2@nordvik-as.no"})
    assert r.status_code == 200 and r.json()["endret"] is True, r.text
    assert r.json()["mottaker_maske"] == "f****@nordvik-as.no"
    for feil in ("ikke-en-adresse", "to@adresser@her.no", "", 42, None):
        r = _post(klient, tok, f"/v1/fordring/{fid}/mottaker",
                  {"mottaker_epost": feil})
        assert r.status_code == 400, (feil, r.text)
        assert "mottaker_epost" in r.json().get("detalj", ""), r.text
        assert "adresser@her" not in r.text


@pg
def test_chifferteksten_dekrypteres_med_tenantens_dek_og_evidensen_baerer_masken(
        miljo, migrator, klient, token):
    from db import kryptering
    tok, _ = token(rolle="bestiller",
                   scopes=("bestilling:opprett", "okonomi:read"))
    nr = "F-146-" + secrets.token_hex(3)
    fid = _post(klient, tok, "/v1/fordring",
                {"kunde_ref": "Nordvik AS", "fakturanummer": nr,
                 "belop_ore": 250000, "utstedt": "2026-08-01",
                 "forfall": "2026-08-15",
                 "mottaker_epost": ADRESSE}).json()["fordring_id"]
    _sett_kontekst(migrator, TENANT)
    ct, nonce, key_id, maske = migrator.execute(
        "SELECT mottaker_kryptert, mottaker_nonce, mottaker_key_id,"
        " mottaker_maske FROM fordring WHERE tenant=%s AND fordring_id=%s",
        (TENANT, uuid.UUID(fid))).fetchone()
    dek = kryptering.hent_dek(migrator, TENANT, key_id)
    migrator.rollback()
    assert ct is not None and nonce is not None
    assert ADRESSE.lower() not in bytes(ct).decode("latin-1")
    klar = kryptering.dekrypter(dek, bytes(ct), bytes(nonce), TENANT, key_id,
                                ekstra_aad=b"m23:mottaker")
    assert klar == {"e": ADRESSE.strip().lower()}, klar
    _sett_kontekst(migrator, TENANT)
    logg = migrator.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
        " AND kilde='m23_fordring' AND handling='fordring.mottaker_satt'",
        (TENANT,)).fetchone()[0]
    migrator.rollback()
    assert logg >= 1
    assert maske == "r****@nordvik-as.no"


@pg
def test_avsluttet_fordring_faar_ikke_ny_mottaker_og_vakten_krever_aktor(
        miljo, migrator):
    c = _rt()
    try:
        t = _tenantnavn("mottaker")
        _plan(c, t)
        fid = _fordring(c, t, belop=1000)
        _betal(c, t, fid, 1000)                       # → betalt
        _sett_kontekst(c, t)
        with pytest.raises(psycopg.errors.InvalidParameterValue) as ei:
            c.execute("SELECT m23_sett_mottaker(%s,%s,%s,%s,%s,%s,%s,%s)",
                      (t, fid, "k****@x.no", b"\x00" * 20, b"\x00" * 12,
                       "k", "0" * 64, "u-test"))
        c.rollback()
        assert "avsluttet fordring" in str(ei.value)
    finally:
        c.close()
    # Direkte UPDATE uten aktør: vakten nekter.
    _sett_kontekst(migrator, t)
    fid2 = None
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        migrator.execute(
            "UPDATE fordring SET mottaker_maske='k****@x.no',"
            " mottaker_kryptert=%s, mottaker_nonce=%s, mottaker_key_id='k',"
            " mottaker_hash=repeat('0', 64),"
            " mottaker_satt_ts=now(), mottaker_satt_av='noen'"
            " WHERE tenant=%s AND fordring_id=%s",
            (b"\x00" * 20, b"\x00" * 12, t, fid))
    migrator.rollback()


def test_fordring_py_sender_fortsatt_ingenting():
    kilde = (API / "fordring.py").read_text(encoding="utf-8")
    kode = re.sub(r'"""(.*?)"""', "", kilde, flags=re.S)
    assert not re.search(r"^\s*(import|from)\s+(smtplib|email|httpx|urllib)",
                         kode, re.M)
