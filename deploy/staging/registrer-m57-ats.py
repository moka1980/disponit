#!/usr/bin/env python3
"""Registrer m57_ats-kjeden gjennom de HERDEDE funksjonene (m56-malen,
speilet — PR-014c §3:
«kontrakt-, release- og typeregistreringer skjer gjennom de herdede
funksjonene ved deploy, ikke som rå INSERT i migrasjonen» — auditerte
overganger, som alt annet i 014a).

Kjøres på staging når modulen skal inn i sjekklisterunden:

    DISPONIT_MIGRATOR_URL=… python3 deploy/staging/registrer-m57-ats.py \
        <release_id> <kontrakt_hash> <artifact_digest> \
        <payload_skjema_hash> <kvittering_skjema_hash>

M57-AVVIKET fra m56-malen: `artifact_digest` er MODELLENS digest
(manifest-sha256 fra modellageret, f.eks. Ollamas registry-manifest) —
denne modulens «image» ER modellen, og det er samme verdi
biasmålingene (port 17) og kvitteringenes attestasjon binder seg til.
Kontraktklassen er `krever_outbox`/`kompenserende` (057-kjeden), ikke
m56s `ekstern_lesing`/`direkte`.

HVER HASH ER SITT EGET DOKUMENT (Codex P1). WCAG-payloaden, PR-006-
kvitteringen, rapportskjemaet og manifestet er fire forskjellige
dokumenter; gjenbrukte man rapportskjemaets digest for alle fire, skrev
skriptet FALSK proveniens i rader som er immutable for alltid — og et
senere forsøk med de riktige hashene ville blitt avvist som
immutabilitetskonflikt, uten vei tilbake. Derfor:

  * rapportskjemaet — REGNES UT her (`rapportskjema.skjema_hash()`), det
    er modulens eget dokument og ligger i koden;
  * manifestet — REGNES UT her fra `manifest.yaml` på disk, som dens
    KANONISKE PROJEKSJON (A-vedtaket på #152: parset YAML minus
    katalogaksene, `manifestskjema.kanonisk_projeksjon`) — samme
    «manifest på disk = register»-disiplin som deploy-porten (014a §7),
    men på identiteten, ikke på formateringen;
  * payload- og kvitteringsskjemaet — TAS IMOT, de eies av
    release-materialet (014b) og plattformkontrakten (PR-006), ikke av
    denne fila. Formen valideres før noe skrives.

Idempotent: alle funksjonene er no-op på identisk innhold. Skriptet
registrerer ALDRI status `aktiv` — aksept er sjekklistens (manifestet).
"""
import hashlib
import os
import re
import sys
from pathlib import Path

REPO = Path(os.environ.get("DISPONIT_REPO",
                           Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

import oppdragskontrakt  # noqa: E402
from api.artefaktskjema import Skjemaugyldig, registrer  # noqa: E402
from modules.m57_ats import rapportskjema  # noqa: E402

MODUL = "m57_ats"
OPPDRAGSTYPE = "rekruttering.evaluering"
# Artefakttypen leses fra KONTRAKTEN, ikke som en streng her (Codex P2):
# rapport-lese-API-et kjenner igjen paret (oppdragstype, artefakttype)
# derfra, og to skrivemåter av det samme navnet ville betydd at det
# registrerte artefaktet var uleselig for flaten det er til for.
ARTEFAKTTYPE = oppdragskontrakt.OPPDRAGSTYPER[
    OPPDRAGSTYPE].rapport_artefakttype
MANIFEST = Path("platform/modules/m57_ats/manifest.yaml")

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _hex64(navn: str, verdi: str) -> str:
    """Formsjekk FØR skriving: radene under er immutable, så en feilformet
    hash må stoppe her — etterpå finnes det ingen retting, bare en
    konflikt."""
    if not _HEX64.match(verdi or ""):
        raise SystemExit(f"{navn} må være 64 hex-tegn (sha256), fikk"
                         f" {verdi!r}")
    return verdi


def manifest_hash() -> str:
    """Manifestets KANONISKE PROJEKSJON (A-vedtaket på #152): parset
    YAML minus katalogaksene. Raden er immutable, og med byte-hashen her
    ville en ren kommentarlinje mellom to runder på samme release-id
    dødd i «release er immutable» — identiteten releasen bærer er den
    strukturelle, ikke formateringen. Eldre rader (t.o.m. wcag-r23)
    bærer byte-hashen; akseptens oppslag er dobbeltnøklet og leser
    begge formene."""
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
    dsn = os.environ["DISPONIT_MIGRATOR_URL"]
    m_hash = manifest_hash()
    with psycopg.connect(dsn) as c:
        c.execute("SET ROLE disponit_modules_admin")
        c.execute("SELECT installer_modul(%s, 'deploy')", (MODUL,))
        # Kontrakten: ekstern_lesing + direkte reversibel (§4). payload- og
        # kvitteringsskjemahashene er de OPPGITTE — de binder
        # release-materialets payloadform og PR-006-kvitteringen, to
        # dokumenter denne fila verken eier eller kan regne ut.
        c.execute("SELECT registrer_kontrakt(%s, 1, %s, %s, %s,"
                  " 'krever_outbox', 'kompenserende', 'deploy')",
                  (MODUL, kontrakt_hash, payload_hash, kvittering_hash))
        # Releasen bærer MANIFESTETS hash, ikke rapportskjemaets.
        c.execute("SELECT registrer_release(%s, %s, 1, %s, %s, %s,"
                  " 'deploy')",
                  (MODUL, release_id, kontrakt_hash, m_hash, digest))
        c.execute("SELECT registrer_oppdragstype(%s, %s, 1, %s, 'deploy')",
                  (OPPDRAGSTYPE, MODUL, kontrakt_hash))
        # Gjennom DEN DELTE registreringsveien (Codex P2): den eier
        # metasjekken, kanoniseringen og hashen samlet, så neste
        # deploy-verktøy arver dem i stedet for å gjenta dem — kanskje
        # ulikt, mot en rad som er immutabel for alltid.
        try:
            h = registrer(c, rapportskjema.SKJEMA, "deploy")
        except Skjemaugyldig as e:
            raise SystemExit(f"rapportskjemaet kan ikke registreres: {e}")
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
