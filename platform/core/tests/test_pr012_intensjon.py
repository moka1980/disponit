"""PR-012 §1: handlingsintensjon — lukket, minimert, kryptert, AAD-bundet.

Pure tester på `bygg_handlingsintensjon` (lukket feltliste, referanser uten
verdi, størrelsestak) + en @pg-tur som beviser at `_skriv_unntak` krypterer
intensjonen bundet i AAD til sakens `unntak_id` (flyttes ciphertextet til en
annen sak, feiler dekrypteringen) og setter `hi_integritet_hash` over
CIPHERTEXTET.
"""
import hashlib
import types

import pytest

from api.minimering import (HI_MAKS_KLARTEKST, HI_SKJEMAVERSJON,
                            IntensjonForStor, bygg_handlingsintensjon)
from .test_api import DSN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
TEN = "t-hi"


def _event(**over):
    e = {"handling": "faktura.bokfor", "belop": "45000.00", "valuta": "NOK",
         "ressurs_id": "fak-1", "tidspunkt": "2026-08-03T10:00:00+00:00",
         "dataklasser": ["finansiell"], "dataklasser_kilde": "connector",
         "attestasjoner": {"dublettsjekk": {"verifikator": "v_x", "verdi": 123,
                                            "resultat": True, "utloper": "x"}}}
    e.update(over)
    return e


def test_lukket_feltliste_og_referanser_uten_verdi():
    hi = bygg_handlingsintensjon(_event())
    assert hi["handling"] == "faktura.bokfor"
    assert hi["belop"] == "45000.00" and hi["valuta"] == "NOK"
    assert hi["ressurs_id"] == "fak-1"
    assert hi["dataklasser"] == ["finansiell"]
    assert hi["dataklasser_kilde"] == "connector"
    # Referanse = KUN vilkår + verifikator; verdi/resultat/utloper strippet.
    assert hi["attestasjoner_referanser"] == [
        {"vilkaar": "dublettsjekk", "verifikator": "v_x"}]
    # Ingen fri passthrough.
    assert set(hi) <= {"handling", "ressurs_id", "belop", "valuta", "tidspunkt",
                       "dataklasser", "dataklasser_kilde",
                       "attestasjoner_referanser"}


def test_ukjent_felt_slippes_ikke_gjennom():
    hi = bygg_handlingsintensjon(_event(hemmelig="lekk", personnr="123"))
    assert "hemmelig" not in hi and "personnr" not in hi


def test_manglende_belop_gir_ingen_belopnokkel():
    e = _event()
    del e["belop"]
    hi = bygg_handlingsintensjon(e)
    assert "belop" not in hi


def test_over_taket_avvises():
    stor = _event(ressurs_id="x" * (HI_MAKS_KLARTEKST + 10))
    with pytest.raises(IntensjonForStor):
        bygg_handlingsintensjon(stor)


@pytest.fixture()
def conn(monkeypatch):
    from db.pg import koble, migrer
    from .test_api import KEK, MIGRATOR_DSN
    monkeypatch.setenv("DISPONIT_KEK", KEK)   # per-tenant DEK pakkes av KEK-en
    m = koble(MIGRATOR_DSN)
    migrer(m)
    m.commit()
    m.close()
    c = koble(DSN)
    yield c
    c.close()


@pg
def test_skriv_unntak_krypterer_intensjon_bundet_til_saken(conn):
    from api.kjerne import _skriv_unntak
    from db import kryptering
    from db.pg import sett_tenant

    sett_tenant(conn, TEN)
    conn.execute("SELECT set_config('disponit.aktor','test',false)")  # AFTER-trigger krever aktør
    lid = conn.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','p','STOPP','[]'::jsonb) RETURNING id",
        (TEN,)).fetchone()[0]
    snap = types.SimpleNamespace(maks_auto_forsok=3, versjon="1.0.0",
                                 innholds_hash="p" * 64)
    intensjon = bygg_handlingsintensjon(_event())
    uid = _skriv_unntak(conn, TEN, lid, "faktura.bokfor", "over_grense",
                        "normal", "normal", {"handling": "faktura.bokfor"},
                        snap, intensjon)

    hi_ct, hi_key_id, hi_nonce, hi_hash, hi_ver, ipol, pakrevd = conn.execute(
        "SELECT handlingsintensjon_kryptert, hi_key_id, hi_nonce,"
        " hi_integritet_hash, hi_skjemaversjon, intensjon_policy_hash,"
        " intensjon_pakrevd FROM unntak WHERE tenant=%s AND id=%s",
        (TEN, uid)).fetchone()
    assert pakrevd is True
    assert hi_ver == HI_SKJEMAVERSJON
    assert ipol == "p" * 64
    # Integritetshash er over CIPHERTEXTET, ikke klartekst.
    assert hi_hash == hashlib.sha256(bytes(hi_ct)).hexdigest()

    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, TEN)
    aad = kryptering.intensjon_aad(uid, "faktura.bokfor", HI_SKJEMAVERSJON, ipol)
    dek_hi = kryptering.dekrypter(dek, bytes(hi_ct), bytes(hi_nonce), TEN,
                                  hi_key_id, ekstra_aad=aad)
    assert dek_hi["belop"] == "45000.00"
    assert dek_hi["attestasjoner_referanser"] == [
        {"vilkaar": "dublettsjekk", "verifikator": "v_x"}]

    # AAD-binding: samme ciphertext «flyttet» til en annen sak (feil
    # unntak_id) kan ikke dekrypteres.
    feil = kryptering.intensjon_aad(uid + 1, "faktura.bokfor",
                                    HI_SKJEMAVERSJON, ipol)
    with pytest.raises(Exception):
        kryptering.dekrypter(dek, bytes(hi_ct), bytes(hi_nonce), TEN,
                             hi_key_id, ekstra_aad=feil)
    conn.rollback()


@pg
def test_uten_intensjon_er_saken_uendret(conn):
    # Handling uten menneskelig_overstyring => ingen intensjon: hi-feltene er
    # NULL og intensjon_pakrevd er false (den ordinære veien er uendret).
    from api.kjerne import _skriv_unntak
    from db.pg import sett_tenant

    sett_tenant(conn, TEN)
    conn.execute("SELECT set_config('disponit.aktor','test',false)")
    lid = conn.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','p','STOPP','[]'::jsonb) RETURNING id",
        (TEN,)).fetchone()[0]
    snap = types.SimpleNamespace(maks_auto_forsok=3, versjon="1.0.0",
                                 innholds_hash="p" * 64)
    uid = _skriv_unntak(conn, TEN, lid, "purring.send", "over_grense",
                        "normal", "normal", {"handling": "purring.send"}, snap)
    intensjon_pakrevd, hi_ct = conn.execute(
        "SELECT intensjon_pakrevd, handlingsintensjon_kryptert FROM unntak"
        " WHERE tenant=%s AND id=%s", (TEN, uid)).fetchone()
    assert intensjon_pakrevd is False
    assert hi_ct is None
    conn.rollback()
