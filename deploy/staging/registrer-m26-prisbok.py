#!/usr/bin/env python3
"""Registrer m26_prisbok-kjeden gjennom de HERDEDE funksjonene
(m56/m57-malen, speilet — PR-014c §3: «kontrakt-, release- og
typeregistreringer skjer gjennom de herdede funksjonene ved deploy, ikke
som rå INSERT i migrasjonen»).

Kjøres på verten når tilbudsmodulen skal i drift (ARC B tilbud, PR 4):

    DISPONIT_MIGRATOR_URL=… python3 deploy/staging/registrer-m26-prisbok.py \\
        <release_id> <kontrakt_hash> <artifact_digest> \\
        <payload_skjema_hash> <kvittering_skjema_hash>

Kontraktklassen er `krever_outbox`/`kompenserende`: et tilbud er sendt
når det er sendt; en rettelse er et NYTT tilbud, ikke en angring.

HVER HASH ER SITT EGET DOKUMENT (Codex P1 på m57): manifestet regnes ut
her fra `manifest.yaml` på disk (kanonisk projeksjon); de andre TAS
IMOT. Radene er immutable — en feilformet hash stopper FØR skriving.
Ingen artefakttype: tilbudet produserer ingen rapport, kvitteringen ER
evidensen (sendt_ts, malversjon, melding_id, mottakermaske, sum).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO = Path(os.environ.get("DISPONIT_REPO",
                           Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

MODUL = "m26_prisbok"
OPPDRAGSTYPE = "tilbud.generer"
MANIFEST = Path("platform/modules/m26_prisbok/manifest.yaml")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _hex64(navn: str, verdi: str) -> str:
    if not _HEX64.match(verdi or ""):
        raise SystemExit(f"{navn} må være 64 hex-tegn (sha256), fikk"
                         f" {verdi!r}")
    return verdi


def manifest_hash() -> str:
    import manifestskjema
    return manifestskjema.kanonisk_projeksjon(
        (REPO / MANIFEST).read_text(encoding="utf-8"))


def main() -> int:
    if len(sys.argv) != 6:
        print(__doc__, file=sys.stderr)
        return 2
    (release_id, kontrakt_hash, digest, payload_hash,
     kvittering_hash) = sys.argv[1:6]
    _hex64("kontrakt_hash", kontrakt_hash)
    _hex64("payload_skjema_hash", payload_hash)
    _hex64("kvittering_skjema_hash", kvittering_hash)
    import oppdragskontrakt
    t = oppdragskontrakt.OPPDRAGSTYPER[OPPDRAGSTYPE]
    assert t.eiermodul == MODUL, t.eiermodul
    dsn = os.environ["DISPONIT_MIGRATOR_URL"]
    m_hash = manifest_hash()
    with psycopg.connect(dsn) as c:
        c.execute("SET ROLE disponit_modules_admin")
        c.execute("SELECT installer_modul(%s, 'deploy')", (MODUL,))
        c.execute("SELECT registrer_kontrakt(%s, 1, %s, %s, %s,"
                  " 'krever_outbox', 'kompenserende', 'deploy')",
                  (MODUL, kontrakt_hash, payload_hash, kvittering_hash))
        c.execute("SELECT registrer_release(%s, %s, 1, %s, %s, %s,"
                  " 'deploy')",
                  (MODUL, release_id, kontrakt_hash, m_hash, digest))
        c.execute("SELECT registrer_oppdragstype(%s, %s, 1, %s, 'deploy')",
                  (OPPDRAGSTYPE, MODUL, kontrakt_hash))
        c.execute("RESET ROLE")
        c.commit()
    print(f"registrert: {MODUL} {release_id} — {OPPDRAGSTYPE}"
          f" (manifest {m_hash[:12]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
