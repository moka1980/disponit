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


def _filavvik() -> list[str]:
    """Reviewede filer som ikke matcher konstantene sine.

    Herdingens FØRSTE måling, og den er ubetinget: den gjelder uansett om
    raden er NULL og uansett om versjonen er registrert i basen i det hele
    tatt. Det er nettopp denne ubetingetheten opp.sh-porten ikke kunne
    speile (#181) — porten løkket over basens rader, denne løkker over
    det som er REVIEWET.
    """
    avvik = []
    for versjon, forventet in REVIEWEDE_CHECKSUMS.items():
        fil = next(MIG.glob(f"{versjon:03d}_*.sql"), None)
        if fil is None:
            avvik.append(f"{versjon:03d}: reviewet migrasjon mangler i treet")
            continue
        if hashlib.sha256(fil.read_bytes()).hexdigest() != forventet:
            avvik.append(
                f"{fil.name} matcher ikke reviewet checksum — historikken"
                f" skal bindes til det som er gjennomgått, ikke til disk")
    return avvik


def _uherdbare(conn) -> list[int] | None:
    """Registrerte versjoner som står UTEN checksum etter at herdingen har
    fylt de reviewede. `None` = ingen historikk å måle ennå.

    ÉN kropp for tre skjemaformer, og det er hele poenget med #181:

    * ingen `migrasjoner`-tabell — fersk base. Kjøreren lager den og
      registrerer 001/002; herdingen fyller dem. Ingenting å måle.
    * ingen `checksum`-KOLONNE — PR-004-æraens base. Kjøreren legger den
      til som NULL for HVER registrert versjon, og herdingen fyller kun de
      reviewede. Spørsmålet er derfor det samme som i normaltilfellet,
      bare stilt før kolonnen finnes.
    * kolonnen finnes — normaltilfellet.

    De to siste stiller altså SAMME spørsmål: hvilke registrerte versjoner
    kan herdingen ikke fylle? Den gamle porten hadde dem som to grener med
    hver sin begrunnelse, og hver reviewrunde fant et sted de sa
    forskjellige ting.
    """
    import psycopg  # lokal: predikatet kalles også fra skript uten toppimport
    try:
        rader = conn.execute(
            "SELECT versjon FROM migrasjoner WHERE checksum IS NULL"
            " ORDER BY versjon").fetchall()
    except psycopg.errors.UndefinedTable:
        conn.rollback()
        return None
    except psycopg.errors.UndefinedColumn:
        # Kolonnen finnes ikke: da er ALLE registrerte versjoner uten
        # checksum, og herdingen fyller de reviewede.
        conn.rollback()
        rader = conn.execute(
            "SELECT versjon FROM migrasjoner ORDER BY versjon").fetchall()
    return [v for (v,) in rader if v not in REVIEWEDE_CHECKSUMS]


def herd_historikk(conn, *, torrkjor: bool = False) -> list[str]:
    """Backfill av reviewede checksums + NOT NULL. Idempotent.

    Kalles av deploy/staging/migrer.py FØR migrasjon 003, slik den bindende
    spesifikasjonen krever. Kjøres den etterpå — eller ikke i det hele tatt
    — er historikken fortsatt muterbar selv om oppsettet rapporterer
    suksess. Det var Codex' P1 i andre review-runde.

    `torrkjor=True` gjør NØYAKTIG de samme målingene og skriver ingenting.
    Den returnerer avvikene i stedet for å kaste, og tom liste betyr at
    den skrivende veien vil lykkes.

    ÉN KROPP, IKKE TO GRENER (#181, eiervalg A fra #178s K2). Porten i
    `opp.sh` var en håndskrevet speiling av kriteriene her, og de to løkkene
    hadde forskjellig definisjonsmengde: porten løkket over basens rader,
    herdingen over `REVIEWEDE_CHECKSUMS` med ubetinget filmåling. Hver
    reviewrunde fant et nytt sted de sa forskjellige ting (R1 manglende
    kolonne, R2 NULL på ukjent versjon, R3 NULL på legacy uten filmåling),
    og grenen kunne ikke konvergere ved lapping — det er SP-13/K4-mønsteret
    målt på semantikk: porten SIMULERTE en fremmed modul i stedet for å
    spørre den.

    En torrkjøring som er en KOPI av målingene løser ingenting. Derfor deler
    de to veiene denne kroppen, og forskjellen er kun om det skrives.
    """
    avvik = _filavvik()
    if not avvik and not torrkjor:
        # Skriv bare når filene er verifisert: en UPDATE på grunnlag av en
        # konstant vi nettopp så ikke stemmer, ville bundet historikken til
        # en fil ingen har reviewet.
        for versjon, forventet in REVIEWEDE_CHECKSUMS.items():
            conn.execute("UPDATE migrasjoner SET checksum=%s"
                         " WHERE versjon=%s AND checksum IS NULL",
                         (forventet, versjon))

    uherdbare = _uherdbare(conn)
    if uherdbare:
        avvik.append(
            "registrerte migrasjoner uten checksum: "
            + ", ".join(f"{v:03d}" for v in uherdbare)
            + " — kan ikke låse historikken; herdingen fyller kun de"
            + " reviewede ("
            + ", ".join(f"{v:03d}" for v in sorted(REVIEWEDE_CHECKSUMS))
            + ")")

    if avvik:
        # Rull tilbake FØR utgangen, uansett vei ut. Backfillen over står
        # fortsatt upåbegynt-committet i transaksjonen, og den som rydder
        # opp etter et kast, committer: `main()` slipper advisory-låsen i
        # sin `finally` med `conn.commit()`. Uten denne rollbacken ville
        # opprydningen BEVART en halvveis herdet historikk fra en herding
        # som feilet — 001/002 fylt, resten NULL — og neste kjøring møter
        # en base ingen har herdet ferdig og ingen har latt være.
        conn.rollback()
        if torrkjor:
            return avvik
        raise HerdingFeilet("; ".join(avvik))

    if torrkjor:
        # Ingen skriving skal overleve en MÅLING — heller ikke en tom
        # transaksjon som holder en lås mens deployet venter.
        conn.rollback()
        return []

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
    return []


def kan_herdes(conn) -> list[str]:
    """Tom liste = `herd_historikk` vil lykkes mot denne basen.

    Lesende, sideeffektfri, og — avgjørende — ingen egen kropp: den ER
    herdingen, kjørt uten skriving. Det er forskjellen på et predikat og
    en simulator (K4/SP-13).
    """
    return herd_historikk(conn, torrkjor=True)


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
