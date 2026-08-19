"""PR-012 Increment 2b: `opprett_godkjenningsrunde`.

Server-utleder `bundet_grunnkode` fra sakens begrunnelseskjede, åpner en apen
runde og gjør den kontrollerte overgangen manuell→venter_godkjenning — og
NEKTER når saken ikke er manuell, mangler intensjon, eller den blokkerende
grunnkoden ikke er godkjennbar.
"""
from datetime import datetime, timezone

import pytest

from api.unntaksbehandling import Godkjenningsfeil, opprett_godkjenningsrunde
from .test_api import DSN, migrator, miljo  # noqa: F401
from .test_pr010_db import _ctx, T as TEN

pg = pytest.mark.skipif(not DSN, reason="DISPONIT_TEST_DSN ikke satt")
NAA = datetime(2026, 8, 3, 10, 0, tzinfo=timezone.utc)

POL = {
    "meta": {"policy_id": "p", "versjon": "1.0.0"},
    "menneskelig_overstyring": {
        "godkjennbare": [{"grunnkode": "belop_over_grense",
                          "handling": "faktura.bokfor"}],
        "krever_rolle": "okonomi"},
}


def _sak(conn, *, begrunnelse, intensjon=True, status="manuell"):
    """Manuell sak med loggpost (m/ begrunnelseskjede) + evt. intensjonsfelt."""
    _ctx(conn, TEN)
    conn.execute("INSERT INTO tenant_nokler (tenant,key_id,wrapped_dek,aktiv)"
                 " VALUES (%s,'k1',%s,true) ON CONFLICT DO NOTHING",
                 (TEN, b"\x00" * 44))
    lid = conn.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h','p','STOPP',%s::jsonb) RETURNING id",
        (TEN, begrunnelse)).fetchone()[0]
    if intensjon:
        felt = (", handlingsintensjon_kryptert, hi_key_id, hi_nonce,"
                " hi_integritet_hash, hi_skjemaversjon, intensjon_policy_hash,"
                " intensjon_pakrevd")
        verdier = ", %s,'k1',%s,'hih',1,'ph',true"
        args_ekstra = (b"\x00", b"\x00" * 12)
    else:
        felt, verdier, args_ekstra = "", "", ()
    uid = conn.execute(
        "INSERT INTO unntak (tenant,loggpost_id,handling,kategori,"
        "payload_kryptert,key_id,nonce,maks_auto_forsok_snapshot,policy_versjon,"
        "policy_content_hash,status,sakskilde" + felt + ") VALUES (%s,%s,"
        "'faktura.bokfor',"
        "'over_grense',%s,'k1',%s,3,'0.2.0','ph',%s,'policybrudd'"
        + verdier + ") RETURNING id",
        (TEN, lid, b"\x00", b"\x00" * 12, status, *args_ekstra)).fetchone()[0]
    return uid


@pg
def test_apner_runde_og_utleder_bunden_grunnkode(migrator):
    uid = _sak(migrator, begrunnelse='[{"kode":"belop_ok"},'
               '{"kode":"belop_over_grense"}]')
    runde = opprett_godkjenningsrunde(migrator, tenant=TEN, unntak_id=uid,
                                      aktor="op", request_id="r1", policy=POL,
                                      policy_hash="ph", naa=NAA)
    assert runde == 1
    r = migrator.execute(
        "SELECT status, bundet_grunnkode, godkjennings_policy_hash FROM"
        " godkjenningsrunde WHERE tenant=%s AND unntak_id=%s AND runde=1",
        (TEN, uid)).fetchone()
    assert r == ("apen", "belop_over_grense", "ph")
    st = migrator.execute("SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                          (TEN, uid)).fetchone()[0]
    assert st == "venter_godkjenning"
    # Andre forsøk: saken er ikke lenger manuell => nektes.
    with pytest.raises(Godkjenningsfeil) as ei:
        opprett_godkjenningsrunde(migrator, tenant=TEN, unntak_id=uid,
                                  aktor="op", request_id="r2", policy=POL,
                                  policy_hash="ph", naa=NAA)
    assert ei.value.kode == "runde_ulovlig_tilstand"
    migrator.rollback()


@pg
def test_ikke_godkjennbar_grunnkode_nektes(migrator):
    # Blokkert på en grunnkode som ikke er i policyens godkjennbare.
    uid = _sak(migrator, begrunnelse='[{"kode":"rolle_ikke_tillatt"}]')
    with pytest.raises(Godkjenningsfeil) as ei:
        opprett_godkjenningsrunde(migrator, tenant=TEN, unntak_id=uid,
                                  aktor="op", request_id="r1", policy=POL,
                                  policy_hash="ph", naa=NAA)
    assert ei.value.kode == "godkjenn_utilgjengelig"
    migrator.rollback()


@pg
def test_uten_intensjon_nektes(migrator):
    uid = _sak(migrator, begrunnelse='[{"kode":"belop_over_grense"}]',
               intensjon=False)
    with pytest.raises(Godkjenningsfeil) as ei:
        opprett_godkjenningsrunde(migrator, tenant=TEN, unntak_id=uid,
                                  aktor="op", request_id="r1", policy=POL,
                                  policy_hash="ph", naa=NAA)
    assert ei.value.kode == "godkjenn_utilgjengelig"
    migrator.rollback()
