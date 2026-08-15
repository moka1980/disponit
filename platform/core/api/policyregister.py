"""Policyregisteret (v2 Del 1.5) — lasting med revalidering.

Ingen cache i PR-005: policyen leses fra databasen per forespørsel. Det er
et bevisst valg fra spesifikasjonen — cache-invalidering er et helt eget
problem, og på dagens skala kjøper den ingenting. Cache er M-38-scope.
"""
from __future__ import annotations

import hashlib
import json
import os

import psycopg
from miljo import er_produksjon

#: Statusene en policy kan ha i `meta.status`. I produksjon er listen
#: HARDKODET og kan ikke konfigureres bort — en policy merket `utkast` skal
#: aldri kunne binde en ekte handling fordi noen satte en miljøvariabel.
PRODUKSJONSSTATUSER = frozenset({"produksjon"})
STAGING_STANDARD = "utkast,validert_pilot,produksjon"


class PolicyUkjent(Exception):
    """Ingen aktiv policy med den id-en for DENNE tenanten."""


class PolicyKorrupt(Exception):
    """Raden finnes, men innholdet består ikke valideringen på nytt."""

    def __init__(self, feil: list[str], raa: object = None) -> None:
        super().__init__("; ".join(feil[:3]))
        self.feil = feil
        self.raa = raa


def innholds_hash(policy: dict) -> str:
    """Kanonisk SHA-256 — samme regler som attestering.kanonisk_bytes."""
    return hashlib.sha256(json.dumps(
        policy, sort_keys=True, ensure_ascii=False,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def tillatte_statuser() -> frozenset[str]:
    # Samme kilde og samme eksakte tolkning som kundeflaten leser miljøet med
    # (`miljo.gjeldende_miljo` i ui/server) — se `miljo`-modulen.
    if er_produksjon():
        return PRODUKSJONSSTATUSER
    raa = os.environ.get("DISPONIT_TILLATTE_POLICYSTATUSER", STAGING_STANDARD)
    return frozenset(s.strip() for s in raa.split(",") if s.strip())


def hent_aktiv(conn: psycopg.Connection, tenant: str,
               policy_id: str) -> tuple[dict, str]:
    """-> (policy, innholds_hash). Kaster PolicyUkjent / PolicyKorrupt.

    Oppslaget er ALLTID bundet til kontekstens tenant. `policy_id` fra
    forespørselen er kun et navn innenfor den tenanten — den kan aldri peke
    ut en annen tenants policy, verken via spørringen eller via RLS.
    Krever at kalleren har satt `disponit.tenant` (db.pg.sett_kontekst).
    """
    rad = conn.execute(
        "SELECT innhold, innholds_hash, status, versjon FROM policyer"
        " WHERE tenant=%s AND policy_id=%s AND aktiv",
        (tenant, policy_id)).fetchone()
    if rad is None:
        raise PolicyUkjent(policy_id)
    innhold, lagret_hash, status, versjon = rad

    # Revalidering ved lasting (v2 1.5, fail-closed mot DB-korrupsjon).
    # At raden BESTO valideringen den dagen den ble skrevet, sier ingenting
    # om hva som står der nå.
    if not isinstance(innhold, dict):
        raise PolicyKorrupt(["policyinnholdet er ikke et objekt"], innhold)

    faktisk = innholds_hash(innhold)
    if faktisk != lagret_hash:
        raise PolicyKorrupt(
            [f"innholds_hash stemmer ikke: lagret {lagret_hash[:12]}…,"
             f" faktisk {faktisk[:12]}…"], innhold)

    from policy_validator.schema import valider_policy
    feil = valider_policy(innhold)
    if feil:
        raise PolicyKorrupt(feil, innhold)

    tillatt = tillatte_statuser()
    if status not in tillatt:
        raise PolicyKorrupt(
            [f"policystatus '{status}' er ikke tillatt i dette miljøet"
             f" (tillatt: {sorted(tillatt)})"], innhold)
    meta_status = (innhold.get("meta") or {}).get("status")
    if meta_status != status:
        # Kolonnen brukes til filtrering, meta.status havner i loggposten.
        # Spriker de, er det uklart hva beslutningen faktisk ble tatt under.
        raise PolicyKorrupt(
            [f"meta.status '{meta_status}' != registerets status '{status}'"],
            innhold)
    if (innhold.get("meta") or {}).get("versjon") != versjon:
        raise PolicyKorrupt(
            [f"meta.versjon != registerets versjon '{versjon}'"], innhold)
    return innhold, lagret_hash


def registrer(conn: psycopg.Connection, tenant: str, policy: dict,
              status: str, aktiver: bool = True) -> str:
    """Legger inn en policyversjon, eventuelt som den aktive. -> innholds_hash.

    Kun etter bestått validering (v2 1.5). Aktiveringen er atomisk:
    deaktiver forrige og aktiver ny i samme transaksjon. Delindeksen
    `en_aktiv_per_policy` er den bindende garantien — den holder selv om
    denne funksjonen skulle bli omgått.

    Brukes av token-/oppsettsveien og av testene, ikke av forespørsels-
    veien: runtime-rollen har kun SELECT på `policyer`.
    """
    from db.pg import sett_tenant
    from policy_validator.schema import valider_policy
    # Setter tenanten selv, i motsetning til `hent_aktiv`. Forskjellen er
    # tilsiktet: `hent_aktiv` kjører alltid inne i forespørselsveien, der
    # `sett_kontekst` allerede har satt den, og en ekstra setting der ville
    # skjult at noen hadde glemt den. `registrer` er en frittstående
    # administrativ operasjon uten en slik eier — uten dette treffer den
    # bare row level security med FORCE og feiler på skriving.
    sett_tenant(conn, tenant)
    feil = valider_policy(policy)
    if feil:
        raise PolicyKorrupt(feil, policy)
    meta = policy.get("meta") or {}
    if meta.get("status") != status:
        raise PolicyKorrupt(
            [f"meta.status '{meta.get('status')}' != oppgitt status '{status}'"],
            policy)
    pid, versjon, h = meta["policy_id"], meta["versjon"], innholds_hash(policy)
    if aktiver:
        conn.execute("UPDATE policyer SET aktiv=false"
                     " WHERE tenant=%s AND policy_id=%s AND aktiv",
                     (tenant, pid))
    conn.execute(
        "INSERT INTO policyer (tenant, policy_id, versjon, innholds_hash,"
        " status, innhold, aktiv) VALUES (%s,%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (tenant, policy_id, versjon) DO UPDATE"
        " SET innholds_hash=EXCLUDED.innholds_hash, status=EXCLUDED.status,"
        "     innhold=EXCLUDED.innhold, aktiv=EXCLUDED.aktiv",
        (tenant, pid, versjon, h, status, json.dumps(policy), aktiver))
    return h
