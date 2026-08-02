"""Envelope-kryptering av unntaks-payload (v2 Del 5 + v3-delta pkt. 2-3).

Per-tenant DEK (AES-256-GCM) pakket av KEK fra DISPONIT_KEK (miljø).
payload_kryptert = ciphertext || 16-byte GCM-tag; nonce i egen kolonne.
Crypto-shredding: destruer() nuller wrapped_dek og setter destruert_ts +
aktiv=false i ÉN UPDATE (GO-vilkår 1); ciphertext består som artefakt.
Persondata SKAL være erstattet med kildereferanser FØR kryptering —
minimeringen skjer i api_kjerne.minimer_payload, ikke her.
"""
from __future__ import annotations

import json
import os
import secrets

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _kek() -> AESGCM:
    raa = os.environ.get("DISPONIT_KEK", "")
    if len(raa) < 64:
        raise RuntimeError("DISPONIT_KEK mangler/for kort (krever >= 32 byte hex)")
    return AESGCM(bytes.fromhex(raa[:64]))


def hent_eller_opprett_aktiv_dek(conn: psycopg.Connection,
                                 tenant: str) -> tuple[str, bytes]:
    # RLS: uten sesjonsvariabelen ser vi null rader og ville laget en NY
    # DEK for en tenant som allerede har en — og da blir gamle unntak
    # uleselige uten at noe feiler høylytt.
    from .pg import sett_tenant
    sett_tenant(conn, tenant)
    rad = conn.execute(
        "SELECT key_id, wrapped_dek FROM tenant_nokler"
        " WHERE tenant=%s AND aktiv", (tenant,)).fetchone()
    if rad:
        key_id, wrapped = rad
        nonce, ct = bytes(wrapped[:12]), bytes(wrapped[12:])
        return key_id, _kek().decrypt(nonce, ct, tenant.encode())
    dek = AESGCM.generate_key(256)
    nonce = secrets.token_bytes(12)
    wrapped = nonce + _kek().encrypt(nonce, dek, tenant.encode())
    key_id = "dek-" + secrets.token_hex(8)
    conn.execute("INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek)"
                 " VALUES (%s,%s,%s)", (tenant, key_id, wrapped))
    return key_id, dek


def _aad(tenant: str, key_id: str) -> bytes:
    """Tilleggsdata som bindes inn i GCM-taggen.

    Uten AAD er et ciphertext bare en pose bytes: flyttes raden til en annen
    tenant, dekrypterer den fint så lenge nøkkelen er den samme. Med tenant
    og key_id som AAD feiler dekrypteringen i det konteksten er en annen enn
    da det ble kryptert. Det koster ingenting og lukker en stille
    kryss-tenant-vei som RLS alene ikke dekker (RLS beskytter raden, ikke
    bytene hvis de kopieres).
    """
    return f"{tenant}|{key_id}".encode("utf-8")


def krypter(dek: bytes, payload: dict, tenant: str,
            key_id: str) -> tuple[bytes, bytes]:
    """-> (payload_kryptert = ct||tag, nonce)"""
    nonce = secrets.token_bytes(12)
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()
    return AESGCM(dek).encrypt(nonce, data, _aad(tenant, key_id)), nonce


def dekrypter(dek: bytes, ct_og_tag: bytes, nonce: bytes, tenant: str,
              key_id: str) -> dict:
    return json.loads(AESGCM(dek).decrypt(bytes(nonce), bytes(ct_og_tag),
                                          _aad(tenant, key_id)))


def destruer(conn: psycopg.Connection, tenant: str, key_id: str) -> None:
    """Crypto-shredding — logging av handlingen gjøres av kalleren
    (revisjonslogg + unntak_historikk per berørt sak)."""
    from .pg import sett_tenant
    sett_tenant(conn, tenant)
    res = conn.execute("UPDATE tenant_nokler SET wrapped_dek=NULL,"
                       " destruert_ts=now(), aktiv=false"
                       " WHERE tenant=%s AND key_id=%s", (tenant, key_id))
    if res.rowcount != 1:
        # Stille no-op her ville betydd at sletting av persondata IKKE
        # skjedde, mens kalleren logget at den gjorde det.
        raise RuntimeError(
            f"crypto-shredding traff {res.rowcount} rader for {tenant}/{key_id}"
            " — forventet nøyaktig 1")
