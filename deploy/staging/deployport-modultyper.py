#!/usr/bin/env python3
"""Deploy-portene fra PR-014c §5 — kjøres av opp.sh ETTER migrasjonene,
FØR den nye releasen aktiveres. Rød port = deploy stopper (samme mønster
som «manifest på disk ≠ register», 014a §7).

Tre porter, alle kryssjekker REGISTERET (databasen) mot den KODEFESTEDE
typeregistreringen (`platform/core/oppdragskontrakt.OPPDRAGSTYPER`):

  1. En rad i `oppdragstype_register` uten kodefestet type: raden gir
     ingen prefikser og dermed et modultoken uten rekkevidde — modulen
     onboardes, claimer ingenting, og ingen sier fra. Deploy stopper.
     (Legacy-unntak: `reinnsending`/`verifikasjon`-radene fra CP5-testene
     bærer UNIKE navn nettopp for å ikke smitte — de fanges også.)

  2. En `ekstern_lesing`-kontrakt hvis registrerte oppdragstype mangler
     `krever_malautorisasjon: true`: da har aktiveringsporten (§6) intet
     autorisasjonsbegrep å håndheve, og en handling med observerbar
     trafikk ut kunne aktiveres uten positivt autorisert mål.

  3. (Codex P1) En rad hvis `eiermodul` avviker fra den kodefestede
     `Oppdragstype.eiermodul`. Autoriteten er registerraden — nettopp
     derfor er avviket farlig: claim-veien utleder handlingsprefiksene
     fra typen den REGISTRERTE modulen eier, så en rad som tildeler
     `kontroll.wcag.nettsted` til en annen modul gir den modulen
     `kontroll.wcag.`-rekkevidde og dermed payloads ment for
     `m_wcag_audit`. Porten gjelder kun når koden faktisk NAVNGIR en
     eier (`eiermodul` er None for de eierløse legacy-typene) — et krav
     kan ikke håndheves mot en kilde som ikke uttaler seg.

Kjøres med RUNTIME-DSN (kun SELECT). Exit 0 = grønn, 1 = stopp.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ.get(
    "DISPONIT_REPO", Path(__file__).resolve().parents[2])) / "platform/core"))

import psycopg  # noqa: E402

import oppdragskontrakt  # noqa: E402


def kontroller(conn) -> list[str]:
    """-> feilliste (tom = grønn). Ren lesing, egen funksjon så testene kan
    kjøre porten mot konstruert tilstand."""
    feil = []
    rader = conn.execute(
        "SELECT r.oppdragstype, r.eiermodul, k.sideeffektklasse"
        "  FROM oppdragstype_register r"
        "  LEFT JOIN modulkontrakt k ON k.modul_id = r.eiermodul"
        "   AND k.kontraktversjon = r.kontraktversjon"
        "   AND k.kontrakt_hash = r.kontrakt_hash"
        " ORDER BY r.oppdragstype").fetchall()
    for typenavn, eiermodul, klasse in rader:
        t = oppdragskontrakt.OPPDRAGSTYPER.get(typenavn)
        if t is None:
            feil.append(
                f"oppdragstype_register har '{typenavn}' (eiermodul"
                f" {eiermodul}) uten kodefestet type i OPPDRAGSTYPER —"
                " raden gir ingen prefikser og et token uten rekkevidde")
            continue
        if t.eiermodul is not None and eiermodul != t.eiermodul:
            feil.append(
                f"'{typenavn}' er registrert med eiermodul {eiermodul!r},"
                f" men den kodefestede typen eies av {t.eiermodul!r} —"
                " claim-veien utleder prefiksene fra registerraden, så den"
                " registrerte modulen ville fått rekkevidde over payloads"
                " ment for den kodefestede eieren")
        if klasse == "ekstern_lesing" and not t.krever_malautorisasjon:
            feil.append(
                f"'{typenavn}' er registrert under en ekstern_lesing-"
                f"kontrakt ({eiermodul}) men den kodefestede typen mangler"
                " krever_malautorisasjon — aktiveringsporten har da intet"
                " autorisasjonsbegrep å håndheve")
    return feil


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get(
        "DISPONIT_DATABASE_URL")
    if not dsn:
        print("deployport-modultyper: DATABASE_URL mangler", file=sys.stderr)
        return 1
    with psycopg.connect(dsn) as conn:
        feil = kontroller(conn)
        conn.rollback()
    if feil:
        for f in feil:
            print(f"DEPLOY-PORT RØD: {f}", file=sys.stderr)
        return 1
    print("deployport-modultyper: grønn "
          f"({len(oppdragskontrakt.OPPDRAGSTYPER)} kodefestede typer)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
