#!/usr/bin/env python3
"""Registrer m_wcag_audit-kjeden gjennom de HERDEDE funksjonene (PR-014c §3:
«kontrakt-, release- og typeregistreringer skjer gjennom de herdede
funksjonene ved deploy, ikke som rå INSERT i migrasjonen» — auditerte
overganger, som alt annet i 014a).

Kjøres på staging når modulen skal inn i sjekklisterunden:

    DISPONIT_MIGRATOR_URL=… python3 deploy/staging/registrer-m-wcag-audit.py \
        <release_id> <kontrakt_hash> <artifact_digest>

Idempotent: alle funksjonene er no-op på identisk innhold. Skriptet
registrerer ALDRI status `aktiv` — aksept er sjekklistens (manifestet).
"""
import os
import sys
from pathlib import Path

REPO = Path(os.environ.get("DISPONIT_REPO",
                           Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from modules.wcag_audit import rapportskjema  # noqa: E402

MODUL = "m_wcag_audit"
OPPDRAGSTYPE = "kontroll.wcag.nettsted"
ARTEFAKTTYPE = "kontroll.wcag.rapport"


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    release_id, kontrakt_hash, digest = sys.argv[1:4]
    dsn = os.environ["DISPONIT_MIGRATOR_URL"]
    kanon = rapportskjema.kanonisk().decode("utf-8")
    h = rapportskjema.skjema_hash()
    with psycopg.connect(dsn) as c:
        c.execute("SET ROLE disponit_modules_admin")
        c.execute("SELECT installer_modul(%s, 'deploy')", (MODUL,))
        # Kontrakten: ekstern_lesing + direkte reversibel (§4). payload-/
        # kvitteringsskjema-hashene er rapportskjemaets (payloaden er den
        # lukkede fire-felts-formen i oppdragskontrakt; kontraktens
        # skjemahasher binder release-materialet).
        c.execute("SELECT registrer_kontrakt(%s, 1, %s, %s, %s,"
                  " 'ekstern_lesing', 'direkte', 'deploy')",
                  (MODUL, kontrakt_hash, h, h))
        c.execute("SELECT registrer_release(%s, %s, 1, %s, %s, %s,"
                  " 'deploy')", (MODUL, release_id, kontrakt_hash, h, digest))
        c.execute("SELECT registrer_oppdragstype(%s, %s, 1, %s, 'deploy')",
                  (OPPDRAGSTYPE, MODUL, kontrakt_hash))
        c.execute("SELECT registrer_artefaktskjema(%s, %s, 'deploy')",
                  (kanon, h))
        c.execute("RESET ROLE")
        c.execute("SET ROLE disponit_domains_admin")
        c.execute("SELECT registrer_artefakttype(%s, %s, 1, %s, %s,"
                  " 'deploy')", (ARTEFAKTTYPE, MODUL, kontrakt_hash, h))
        c.commit()
    print(f"registrert: {MODUL} {release_id} — {OPPDRAGSTYPE} /"
          f" {ARTEFAKTTYPE} (skjema {h[:12]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
