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
import importlib.util
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


def last_bootstrap():
    """Bootstrap-modulen har bindestrek i navnet og kan ikke importeres
    vanlig. Egen funksjon på modulnivå slik at tester kan bytte den ut —
    herdingen skal finnes ett sted, og den er samme fil som kjøres manuelt.
    """
    spek = importlib.util.spec_from_file_location(
        "migrasjon_bootstrap",
        Path(__file__).with_name("migrasjon-bootstrap.py"))
    modul = importlib.util.module_from_spec(spek)
    spek.loader.exec_module(modul)
    return modul


def main(argv: list[str] | None = None) -> int:
    # argv som parameter, ikke sys.argv direkte: da kan tester kalle
    # inngangen som den kalles i drift, uten å rote med prosessens argumenter.
    argv = sys.argv[1:] if argv is None else argv
    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    if not dsn:
        print("AVBRUTT: DISPONIT_MIGRATOR_URL mangler — migrasjoner kjøres"
              " av skjemaeieren, aldri av runtime.")
        return 2
    rolle = argv[0] if argv else "disponit"
    if not rolle.replace("_", "").isalnum():
        print(f"AVBRUTT: ugyldig rollenavn {rolle!r}")
        return 2

    from db.kjorer import LAAS, LEGACY_MAKS, migrer
    from db.pg import koble

    bootstrap = last_bootstrap()
    conn = koble(dsn)
    try:
        # YTTERLÅS rundt HELE overgangen legacy -> herding -> 003.
        #
        # Codex' P1: hvert `migrer()`-kall tok og slapp låsen selv, og
        # `herd_historikk()` tok ingen. «Herding før 003» var derfor bare
        # sant inne i én prosess: to samtidige oppsett kunne rekke å kjøre
        # 003 i vinduet mellom stegene, og da er den bindende rekkefølgen
        # brutt selv om hvert enkelt steg var låst.
        #
        # PostgreSQL teller session-låser per sesjon, så kjørerens egne
        # lock/unlock inne i denne blokken er reentrante og holder låsen
        # oppe hele veien. Antall lås og opplås må balansere — derfor
        # slippes ytterlåsen i finally, uansett utfall.
        conn.execute("SELECT pg_advisory_lock(%s)", (LAAS,))
        conn.commit()
        # Kontraktens rekkefølge (v3-delta): legacy først, så herding av
        # historikken, så resten. Kjøres 003 før checksum-kolonnen er
        # NOT NULL, er historikken fortsatt muterbar mens den nye
        # migrasjonen legges oppå — og oppsettet ville rapportert suksess.
        legacy = migrer(conn, til_og_med=LEGACY_MAKS)
        print(f"legacy-migrasjoner: {legacy or 'ingen'}")
        bootstrap.herd_historikk(conn)
        print("historikk herdet: checksum er NOT NULL")
        kjort = legacy + migrer(conn)
        print(f"migrasjoner kjørt: {kjort or 'ingen — alt var oppdatert'}")
        conn.execute(RETTIGHETER.format(rolle=rolle))
        conn.commit()
        print(f"rettigheter satt for {rolle}")
        # Sluttkontroll. En advarsel med exit 0 er ingen port: klarer vi
        # ikke å bevise at historikken er låst, skal oppsettet feile.
        versjoner = conn.execute(
            "SELECT versjon, checksum IS NOT NULL FROM migrasjoner"
            " ORDER BY versjon").fetchall()
        nullable = conn.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_name='migrasjoner' AND column_name='checksum'"
        ).fetchone()
        conn.rollback()
        uten = [v for v, cs in versjoner if not cs]
        if uten or not nullable or nullable[0] != "NO":
            print(f"AVBRUTT: historikken er ikke låst — uten checksum: {uten},"
                  f" kolonne nullable: {nullable and nullable[0]}")
            return 1
        print("register: " + ", ".join(str(v) for v, _ in versjoner)
              + "  (alle med checksum, kolonnen er NOT NULL)")
    finally:
        try:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LAAS,))
            conn.commit()
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
