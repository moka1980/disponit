#!/usr/bin/env python3
"""Engangs-bootstrap av checksum-herdingen (v3-delta pkt. 1, steg 1-5).

FASTE, reviewede checksums for 001/002 fra main 679ee9e. Skriptet feiler
hardt hvis diskfilene ikke matcher konstantene — vi stoler på review,
ikke på disk. Kjøres av migrator én gang; deretter SET NOT NULL.

BRUK: DISPONIT_MIGRATOR_URL i miljø. Skriptet gjør ALTER TABLE og krever
derfor eierrettigheter — runtime-rollen har dem ikke og skal ikke ha dem.
Claude Code fyller inn konstantene under fra
`sha256sum platform/core/db/migrations/00{1,2}_*.sql` på main 679ee9e og
verifiserer dem i PR-review (merge-port).
"""
import hashlib
import os
import sys
from pathlib import Path

import psycopg

# SHA-256 av migrasjonsfilene slik de står på main 679ee9e (PR-004-merge).
# Hentet med `git show 679ee9e:<sti> | sha256sum`, ikke fra arbeidskopien —
# poenget er å binde til den REVIEWEDE historikken, ikke til det som
# tilfeldigvis ligger på disk. Codex verifiserer disse to mot main som
# merge-port; de skal aldri endres uten at hele porten kjøres på nytt.
REVIEWEDE_CHECKSUMS = {
    1: "a2fdf8273395ca52efa805c13a72c8439a5e18ecf5572a0e017278290ab2f257",
    2: "1e5017796795e687f20d1b084a97b866132e73446cc6dbb5b326f668f0ebeb65",
}
MIG = Path(__file__).resolve().parents[2] / "platform/core/db/migrations"

class HerdingFeilet(RuntimeError):
    """Historikken kunne ikke låses. Alltid hard feil — en advarsel med
    exit 0 er ingen port."""


def herd_historikk(conn) -> None:
    """Backfill av reviewede checksums + NOT NULL. Idempotent.

    Kalles av deploy/staging/migrer.py FØR migrasjon 003, slik den bindende
    spesifikasjonen krever. Kjøres den etterpå — eller ikke i det hele tatt
    — er historikken fortsatt muterbar selv om oppsettet rapporterer
    suksess. Det var Codex' P1 i andre review-runde.
    """
    for versjon, forventet in REVIEWEDE_CHECKSUMS.items():
        fil = next(MIG.glob(f"{versjon:03d}_*.sql"))
        faktisk = hashlib.sha256(fil.read_bytes()).hexdigest()
        if faktisk != forventet:
            raise HerdingFeilet(
                f"{fil.name} matcher ikke reviewet checksum — historikken"
                f" skal bindes til det som er gjennomgått, ikke til disk")
        conn.execute("UPDATE migrasjoner SET checksum=%s"
                     " WHERE versjon=%s AND checksum IS NULL",
                     (forventet, versjon))

    mangler = conn.execute(
        "SELECT versjon FROM migrasjoner WHERE checksum IS NULL"
        " ORDER BY versjon").fetchall()
    if mangler:
        raise HerdingFeilet(
            f"registrerte migrasjoner uten checksum: {[m[0] for m in mangler]}"
            " — kan ikke låse historikken")

    conn.execute("ALTER TABLE migrasjoner"
                 " ALTER COLUMN checksum SET NOT NULL")
    conn.commit()

    nullable = conn.execute(
        "SELECT is_nullable FROM information_schema.columns"
        " WHERE table_name='migrasjoner' AND column_name='checksum'"
    ).fetchone()
    conn.rollback()
    if not nullable or nullable[0] != "NO":
        raise HerdingFeilet("checksum er fortsatt nullable etter herding")


def main() -> int:
    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not dsn:
        print("AVBRUTT: DISPONIT_MIGRATOR_URL mangler. Bootstrap gjør"
              " ALTER TABLE og må kjøre som skjemaeier — runtime-rollen"
              " (DATABASE_URL) har ikke rettighetene og skal ikke ha dem.")
        return 2
    conn = psycopg.connect(dsn)
    conn.execute("SELECT pg_advisory_lock(748291337)")
    try:
        herd_historikk(conn)
    except HerdingFeilet as e:
        print(f"AVBRUTT: {e}")
        return 1
    finally:
        conn.execute("SELECT pg_advisory_unlock(748291337)")
        conn.commit()
    print("Bootstrap OK — migrasjonshistorikken er nå immutable")
    return 0


if __name__ == "__main__":
    sys.exit(main())
