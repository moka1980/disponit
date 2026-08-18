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

  2. MISMATCH MELLOM KLASSE OG AUTORISASJONSKRAV, begge veier:
     * en `ekstern_lesing`-kontrakt hvis registrerte oppdragstype mangler
       `krever_malautorisasjon: true`: da har aktiveringsporten (§6) intet
       autorisasjonsbegrep å håndheve, og en handling med observerbar
       trafikk ut kunne aktiveres uten positivt autorisert mål;
     * (Codex P1) en type som KREVER målautorisasjon, registrert under en
       kontrakt som ikke er `ekstern_lesing`. Da leser
       `_krev_ekstern_lesing_port` typens klasse, ser noe annet enn
       ekstern_lesing, og hopper over hele porten — både frekvenstaket og
       målautorisasjonen. Handlingen leser fortsatt ut på nettet; det er
       bare håndhevingen som er borte. Manglende kontraktrad teller som
       avvik: den er heller ikke `ekstern_lesing`.

  3. (Codex P1) En rad hvis `eiermodul` avviker fra den kodefestede
     `Oppdragstype.eiermodul`. Autoriteten er registerraden — nettopp
     derfor er avviket farlig: claim-veien utleder handlingsprefiksene
     fra typen den REGISTRERTE modulen eier, så en rad som tildeler
     `kontroll.wcag.nettsted` til en annen modul gir den modulen
     `kontroll.wcag.`-rekkevidde og dermed payloads ment for
     `m_wcag_audit`. Porten gjelder kun når koden faktisk NAVNGIR en
     eier (`eiermodul` er None for de eierløse legacy-typene) — et krav
     kan ikke håndheves mot en kilde som ikke uttaler seg.

  4. (Codex P1) En rad i `artefakttype_register` hvis `skjema_hash` ikke
     finnes i `artefaktskjema`. 036 slår på et UBETINGET skjemaoppslag i
     `/v1/artefakt`, og bindingen er en HASH, ikke en fremmednøkkel: en
     type som ble registrert på en oppgradert base før 036 kan bære en
     hash uten skjemarad, og ville blitt fullstendig ubrukelig i det den
     nye releasen ble aktivert. Innholdet kan ikke bakfylles fra basen
     (den har hashen, ikke skjemaet), så porten stopper deployen og
     NAVNGIR type + hash.

     Porten står her og ikke som en migrasjonsport (Codex P1, runde 3).
     `opp.sh` kjører migrasjonene mot BEGGE basene, og testbasen bærer
     syntetiske typerader per konstruksjon — pre-036-tester committet
     `artefakttype_register`-rader med tilfeldige hasher det aldri har
     eksistert et skjema for. En migrasjonsport traff dermed den
     persistente testbasen med en oppskrift som ikke KAN følges: ingen
     kan produsere et skjema som hasher til `_hex64()`. Nøyaktig det
     skillet er grunnen til at steg 6b finnes fra før, og hvorfor det
     kjører mot runtime-DSN-en alene.

     Rekkefølgen gjør porten gjenopprettelig: den kjører ETTER
     migrasjonene (så `artefaktskjema` og `registrer_artefaktskjema`
     finnes) og FØR release-byttet (så det er den GAMLE koden som står
     igjen — og den slår ikke opp skjemaet, altså tar den fortsatt imot
     opplastninger). Deployen stopper, men basen er brukbar, og steg 2 i
     feilmeldingen lar seg faktisk utføre:

       1. kjør `opp.sh`; porten stopper og navngir type + hash
       2. `SELECT registrer_artefaktskjema(<kanonisk skjema>, <hash>,
          <aktør>)` (eller `api.artefaktskjema.registrer`) for hver
       3. kjør `opp.sh` på nytt

Kjøres med RUNTIME-DSN (kun SELECT). Exit 0 = grønn, 1 = stopp.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ.get(
    "DISPONIT_REPO", Path(__file__).resolve().parents[2])) / "platform/core"))

import psycopg  # noqa: E402

import oppdragskontrakt  # noqa: E402


def kontroller_bestillingstyper(conn) -> list[str]:
    """Port 14 (038 §6): hver kodefestet bestillingstype må peke på en
    registrert oppdragstype — ellers gir TILLAT et oppdrag ingen modul
    noensinne kan claime, og bestillingen ser vellykket ut mens arbeidet
    dør stille i køen."""
    from api.bestilling import BESTILLINGSTYPER
    feil = []
    for navn, bt in sorted(BESTILLINGSTYPER.items()):
        rad = conn.execute(
            "SELECT eiermodul FROM oppdragstype_register WHERE"
            " oppdragstype=%s", (bt.oppdragstype,)).fetchone()
        if rad is None:
            feil.append(
                f"bestillingstype '{navn}' peker på oppdragstypen"
                f" '{bt.oppdragstype}' som IKKE er registrert i"
                " oppdragstype_register — TILLAT ville gitt et"
                " uclaimbart oppdrag")
        elif rad[0] != bt.eiermodul:
            feil.append(
                f"bestillingstype '{navn}': eiermodul-avvik"
                f" ({bt.eiermodul} i koden, {rad[0]} i registeret)")
    return feil


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
        elif t.krever_malautorisasjon and klasse != "ekstern_lesing":
            # (Codex P1) DEN ANDRE RETNINGEN, og den farligste av de to:
            # porten så bare avviket over, så en type som KREVER
            # målautorisasjon kunne registreres under en sideeffektfri
            # kontrakt og passere. `_krev_ekstern_lesing_port` leser da
            # typens klasse, ser at den ikke er ekstern_lesing, og hopper
            # over HELE porten — både frekvenstaket og målautorisasjonen.
            # Handlingen leser fortsatt ut på nettet; det er bare
            # håndhevingen som forsvant.
            feil.append(
                f"'{typenavn}' krever målautorisasjon, men er registrert"
                f" under en {klasse or 'ukjent/manglende'}-kontrakt"
                f" ({eiermodul}) — aktiveringsporten hopper da over både"
                " frekvens og målautorisasjon, og handlingen kan aktiveres"
                " uten noen av dem")
    feil += _skjemaporten(conn)
    return feil


def _skjemaporten(conn) -> list[str]:
    """Port 4: hver registrerte artefakttype må ha et OPPSLAGBART skjema.

    Se modul-docstringen for hvorfor kontrollen bor i deploy-porten og
    ikke i en migrasjon. Meldingen navngir både typen og hashen: den som
    kjører deployen trenger begge for å registrere riktig skjema.
    """
    mangler = conn.execute(
        "SELECT r.artefakttype, r.skjema_hash"
        "  FROM artefakttype_register r"
        " WHERE NOT EXISTS (SELECT 1 FROM artefaktskjema s"
        "                    WHERE s.skjema_hash = r.skjema_hash)"
        " ORDER BY r.artefakttype").fetchall()
    return [
        f"artefakttypen '{at}' er registrert med skjema_hash {h}, men den"
        " finnes ikke i artefaktskjema — /v1/artefakt slår opp skjemaet"
        " ubetinget fra 036, så hver opplastning for typen ville blitt"
        " avvist med artefaktskjema_mangler. Registrer skjemaet med"
        " registrer_artefaktskjema(<kanonisk skjema>, <hash>, <aktør>) og"
        " kjør opp.sh på nytt"
        for at, h in mangler]


def main() -> int:
    dsn = os.environ.get("DATABASE_URL") or os.environ.get(
        "DISPONIT_DATABASE_URL")
    if not dsn:
        print("deployport-modultyper: DATABASE_URL mangler", file=sys.stderr)
        return 1
    with psycopg.connect(dsn) as conn:
        feil = kontroller(conn) + kontroller_bestillingstyper(conn)
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
