#!/usr/bin/env python3
"""Eneste vei inn for migrasjoner på en server: den herdede kjøreren.

Codex' P1 i PR-005a-reviewen: oppsett-skriptet og CI kjørte
migrasjonsfilene med `psql -f`. Det ser uskyldig ut — filene er
idempotente — men det omgår alt kjøreren finnes for: advisory-låsen
(to samtidige oppsett kan kjøre samme migrasjon), transaksjonen kjøreren
eier for versjon >= 3 (delvis anvendt migrasjon ved feil midtveis),
checksum-registreringen (historikken blir ikke immutable) og avvisningen
av endret historikk.

En herdet kjører som kan omgås av oppsettet sitt eget skript, er ikke en
kjører — den er en anbefaling.

Rettighetene til runtime settes her, ETTER migrasjonene, fordi en GRANT på
en tabell som ikke finnes ennå er stille virkningsløs. Migrator eier
tabellene og kan derfor dele ut rettigheter selv; superbruker trengs ikke.

BRUK:  DISPONIT_MIGRATOR_URL=... python3 deploy/staging/migrer.py [runtime-rolle]
"""
import os
import sys
from pathlib import Path

ROT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROT / "platform/core"))

# Runtime får nøyaktig det den trenger, aldri mer:
#   unntak_historikk  INSERT-only — historikken skal aldri kunne endres
#   policyer          lesetilgang — policyer endres av en egen vei
#   revisjonslogg     INSERT+SELECT — append-only håndheves i tillegg av trigger
RETTIGHETER = """
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {rolle};
GRANT USAGE ON SCHEMA public TO {rolle};
GRANT SELECT, INSERT ON revisjonslogg, frekvens_hendelser TO {rolle};
GRANT SELECT ON migrasjoner TO {rolle};
GRANT SELECT, INSERT ON unntak_historikk, attestasjon_jti TO {rolle};
GRANT SELECT, INSERT, UPDATE ON unntak, idempotens TO {rolle};
GRANT SELECT, INSERT, UPDATE ON tenant_nokler TO {rolle};
GRANT SELECT ON policyer TO {rolle};
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {rolle};
"""


def main() -> int:
    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not dsn:
        print("AVBRUTT: DISPONIT_MIGRATOR_URL mangler — migrasjoner kjøres"
              " av skjemaeieren, aldri av runtime.")
        return 2
    rolle = sys.argv[1] if len(sys.argv) > 1 else "disponit"
    if not rolle.replace("_", "").isalnum():
        print(f"AVBRUTT: ugyldig rollenavn {rolle!r}")
        return 2

    from db.kjorer import migrer
    from db.pg import koble

    conn = koble(dsn)
    try:
        kjort = migrer(conn)
        print(f"migrasjoner kjørt: {kjort or 'ingen — alt var oppdatert'}")
        conn.execute(RETTIGHETER.format(rolle=rolle))
        conn.commit()
        print(f"rettigheter satt for {rolle}")
        versjoner = conn.execute(
            "SELECT versjon, checksum IS NOT NULL FROM migrasjoner"
            " ORDER BY versjon").fetchall()
        conn.rollback()
        print("register: " + ", ".join(
            f"{v}{'' if cs else ' (uten checksum — kjør bootstrap)'}"
            for v, cs in versjoner))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
