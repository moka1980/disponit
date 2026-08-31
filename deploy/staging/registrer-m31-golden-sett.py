#!/usr/bin/env python3
"""Registrer et golden-sett-HODE gjennom den herdede døren
(registrer-m57-ats-malen — PR-014c §3: registreringer skjer gjennom
funksjonene ved deploy, aldri som rå INSERT).

    DISPONIT_MIGRATOR_URL=… python3 deploy/staging/registrer-m31-golden-sett.py \
        <modul> <sett_id> <versjon> <sti-til-sett.json>

Settet bor på DISK (dom 1, biasmaalinger.json-presedensen): basen får
kun hodet — modul, sett, versjon, KANONISK hash (parset JSON,
sort_keys — `m31.golden.kanonisk_hash`), antall eksempler. Eksemplene
skal være SYNTETISKE tekster i blindet form; persondata i et sett er et
brudd (KRAVGRENSER m31-v1: sett_persondata_i_eksempler = 0), og det er
reviewansvar for settfila.

Formen valideres FØR skriving (`m31.golden.les_sett`): raden er
immutabel, så en feilformet fil må stoppe her — etterpå finnes ingen
retting, bare en immutabilitetskonflikt. Idempotent: identisk innhold
er no-op i døren.
"""
import os
import sys
from pathlib import Path

REPO = Path(os.environ.get("DISPONIT_REPO",
                           Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "platform/core"))

import psycopg  # noqa: E402

from m31 import golden  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    modul, sett_id, versjon_raa, sti = argv
    try:
        versjon = int(versjon_raa)
    except ValueError:
        raise SystemExit(f"versjon må være et heltall, fikk {versjon_raa!r}")
    if versjon < 1:
        raise SystemExit("versjon må være >= 1")
    try:
        eksempler, innhold_hash = golden.les_sett(Path(sti))
    except golden.Settfeil as feil:
        raise SystemExit(f"settet avvist: {feil}")
    dsn = os.environ["DISPONIT_MIGRATOR_URL"]
    with psycopg.connect(dsn) as c:
        c.execute("SET ROLE disponit_modules_admin")
        c.execute(
            "SELECT registrer_golden_sett(%s, %s, %s, %s, %s, %s,"
            " 'deploy')",
            (modul, sett_id, versjon, innhold_hash, len(eksempler),
             f"registrert fra {Path(sti).name}"))
        c.commit()
    print(f"registrert: {modul} {sett_id} v{versjon} — "
          f"{len(eksempler)} eksempler, hash {innhold_hash[:12]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
