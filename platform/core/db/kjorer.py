"""Herdet migrasjonskjører (v3-delta pkt. 1). Erstatter migrer() i pg.py.

Kontrakt: advisory-lås rundt hele kjøringen; kjører KUN manglende
versjoner; checksum (SHA-256) registreres og verifiseres — endret
historisk fil er hard feil; kjøreren eier transaksjonen for versjon >= 3;
versjon 1-2 er legacy med egne BEGIN/COMMIT og kjøres rått, men er
immutable via checksum som alle andre.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

_MIG = Path(__file__).resolve().parent / "migrations"
_LAAS = 748_291_337  # fast advisory-nøkkel for migrasjonskjøring
_LEGACY_MED_EGEN_TX = {1, 2}


def _sha(fil: Path) -> str:
    return hashlib.sha256(fil.read_bytes()).hexdigest()


def migrer(conn: psycopg.Connection) -> list[int]:
    kjort: list[int] = []
    conn.execute("SELECT pg_advisory_lock(%s)", (_LAAS,))
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS migrasjoner (
            versjon INT PRIMARY KEY,
            kjort_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
            checksum TEXT)""")
        conn.commit()
        reg = dict(conn.execute(
            "SELECT versjon, checksum FROM migrasjoner").fetchall())
        for fil in sorted(_MIG.glob("[0-9][0-9][0-9]_*.sql")):
            v = int(fil.name[:3])
            sql = fil.read_text(encoding="utf-8")
            cs = _sha(fil)
            if v in reg:
                if reg[v] is not None and reg[v] != cs:
                    raise RuntimeError(
                        f"migrasjon {v:03d} er endret etter kjøring "
                        f"(checksum-avvik) — historikk er immutable")
                continue
            if v not in _LEGACY_MED_EGEN_TX and (
                    "BEGIN;" in sql or "COMMIT;" in sql):
                raise RuntimeError(
                    f"migrasjon {v:03d}: filer >= 003 skal ikke eie "
                    f"transaksjonen (BEGIN/COMMIT funnet)")
            if v in _LEGACY_MED_EGEN_TX:
                conn.autocommit = True
                try:
                    conn.execute(sql)
                finally:
                    conn.autocommit = False
                conn.execute(
                    "INSERT INTO migrasjoner (versjon, checksum) VALUES (%s,%s)"
                    " ON CONFLICT (versjon) DO UPDATE SET checksum=EXCLUDED.checksum"
                    " WHERE migrasjoner.checksum IS NULL", (v, cs))
                conn.commit()
            else:
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute("INSERT INTO migrasjoner (versjon, checksum)"
                                 " VALUES (%s,%s)", (v, cs))
            kjort.append(v)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_LAAS,))
    return kjort
