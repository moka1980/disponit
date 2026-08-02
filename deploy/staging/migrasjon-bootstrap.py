#!/usr/bin/env python3
"""Engangs-bootstrap av checksum-herdingen (v3-delta pkt. 1, steg 1-5).

FASTE, reviewede checksums for 001/002 fra main 679ee9e. Skriptet feiler
hardt hvis diskfilene ikke matcher konstantene — vi stoler på review,
ikke på disk. Kjøres av migrator én gang; deretter SET NOT NULL.

BRUK: DATABASE_URL i miljø. Claude Code fyller inn konstantene under fra
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

def main() -> int:
    conn = psycopg.connect(os.environ["DATABASE_URL"])
    conn.execute("SELECT pg_advisory_lock(748291337)")
    for v, forventet in REVIEWEDE_CHECKSUMS.items():
        fil = next(MIG.glob(f"{v:03d}_*.sql"))
        faktisk = hashlib.sha256(fil.read_bytes()).hexdigest()
        if faktisk != forventet:
            print(f"AVBRUTT: {fil.name} matcher ikke reviewet checksum")
            return 1
        conn.execute("UPDATE migrasjoner SET checksum=%s"
                     " WHERE versjon=%s AND checksum IS NULL", (forventet, v))
    conn.execute("ALTER TABLE migrasjoner ALTER COLUMN checksum SET NOT NULL")
    conn.commit()
    print("Bootstrap OK — migrasjonshistorikken er nå immutable")
    return 0

if __name__ == "__main__":
    sys.exit(main())
