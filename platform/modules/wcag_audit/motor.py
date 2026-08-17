"""Motorgrensesnittet (PR-014c §2): modulen er KUNDE av kontrollmotoren,
ikke en kopi. Motoren (axe-core i headless Chromium) bor i
wcag_checker-repoet og kjører i browser-containeren (014b §6) uten
credentials; controlleren styrer den og stoler ALDRI på utdataene —
alt herfra er ubetrodd inndata som skjemavalideres av controlleren.

`Motorresultat` er den RÅ tellingen; sanitering og ærlighetsfelter legges
av `rapport.bygg()`. Versjons- og containerdigester er BEVISST ikke en
del av resultatet: de kommer fra serverkonteksten (controllerens config),
aldri fra motoren — en kompromittert motor skal ikke kunne attestere sin
egen identitet.
"""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass

#: Taket på hva controlleren i det hele tatt TAR IMOT fra motoren.
#: Rapportskjemaets 1 MiB-grense gjelder det ferdige artefaktet og
#: beskytter ingenting her: minnet er brukt lenge før JSON-parsingen
#: begynner. En kompromittert — eller bare altfor pratsom — motor skal
#: ikke kunne spise minnet i den CREDENTIAL-BÆRENDE prosessen.
MAKS_STDOUT = 1 << 20
#: stderr leses aldri som data; vi beholder en snipp til feilmeldingen og
#: kaster resten.
MAKS_STDERR = 4096


def _les_med_tak(strom, tak: int) -> tuple[bytes, bool]:
    """-> (bytes, sprengt). Leser ETT byte over taket, slik at «akkurat
    innenfor» kan skilles fra «for stort» uten å bufre resten."""
    biter, n = [], 0
    while n <= tak:
        b = strom.read(min(65536, tak + 1 - n))
        if not b:
            break
        biter.append(b)
        n += len(b)
    return b"".join(biter), n > tak


def _drener(strom, behold: int, ut: list) -> None:
    """Les strømmen TOM, men behold bare de første `behold` bytene.

    Å slutte å lese ville blokkert motoren så snart rørbufferen fylles —
    da henger kjøringen til tidsavbruddet i stedet for å feile ærlig — og
    å beholde alt er nettopp det taket skal hindre."""
    biter, n = [], 0
    try:
        while True:
            b = strom.read(65536)
            if not b:
                break
            if n < behold:
                del_ = b[:behold - n]
                biter.append(del_)
                n += len(del_)
    except (OSError, ValueError):
        pass                       # strømmen ble lukket under oss
    ut.append(b"".join(biter))


@dataclass(frozen=True)
class Motorresultat:
    regelsett_versjon: str
    varighet_ms: int
    #: [{url, status}]
    sider: tuple = ()
    #: [{regel_id, alvorlighet, antall, eksempler[]}] — RÅTT, usanert.
    funn: tuple = ()
    #: [{vert, antall, art}] — blokkerte subressurser fra proxyens telling.
    blokkert: tuple = ()
    #: proxyens taktelling: (truffet, tak, verdi)
    avkortet: tuple = (False, None, None)


class Motorfeil(Exception):
    """Motoren fullførte ikke — oppdraget skal FEILE (status avbrutt i
    kvitteringen), aldri produsere et delvis artefakt (§10 siste rad)."""


class Kommandomotor:
    """Kjør den konfigurerte motorkommandoen (containeren) og les JSON på
    stdout. Kommandoen kommer fra drift-config (DISPONIT_WCAG_MOTOR), aldri
    fra oppdraget. Utdata er ubetrodd: alt går videre til `rapport.bygg` +
    skjemavalidering; en motor som skriver søppel gir Motorfeil, ikke en
    rapport."""

    def __init__(self, kommando: list[str], tidsavbrudd_s: int = 3600):
        self.kommando = list(kommando)
        self.tidsavbrudd_s = tidsavbrudd_s

    def kjor(self, payload: dict) -> Motorresultat:
        # BUNDET fangst (Codex P1): `subprocess.run(capture_output=True)`
        # bufret stdout og stderr uten tak i opptil en time. Her leses
        # stdout mot MAKS_STDOUT, stderr dreneres mot MAKS_STDERR, og en
        # vakthund dreper prosessen ved tidsavbruddet — så en motor som
        # spyr ut data møter en Motorfeil, ikke en tom controllerhost.
        try:
            p = subprocess.Popen(
                self.kommando, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as e:
            raise Motorfeil(f"motorkjøring: {type(e).__name__}") from e

        drept = threading.Event()

        def _tidsavbrudd():
            drept.set()
            p.kill()

        vakt = threading.Timer(self.tidsavbrudd_s, _tidsavbrudd)
        vakt.daemon = True
        vakt.start()
        stderr_ut: list[bytes] = []
        drener = threading.Thread(
            target=_drener, args=(p.stderr, MAKS_STDERR, stderr_ut),
            daemon=True)
        drener.start()
        try:
            try:
                # Payloaden er de fire lukkede feltene — langt under
                # rørbufferen, så skriv-så-les kan ikke vranglåse her.
                p.stdin.write(json.dumps(payload).encode("utf-8"))
                p.stdin.close()
                ut, sprengt = _les_med_tak(p.stdout, MAKS_STDOUT)
            except OSError as e:
                raise Motorfeil(f"motorkjøring: {type(e).__name__}") from e
            if sprengt:
                raise Motorfeil(
                    f"motoren skrev mer enn {MAKS_STDOUT} byte på stdout")
            p.wait()          # stdout er tom; vakthunden bærer fristen
        finally:
            vakt.cancel()
            if p.poll() is None:
                p.kill()
                p.wait()
            drener.join(timeout=1)
            for s in (p.stdin, p.stdout, p.stderr):
                try:
                    s.close()
                except OSError:
                    pass

        if drept.is_set():
            raise Motorfeil("motorkjøring: TimeoutExpired")
        if p.returncode != 0:
            # Snippen fra stderr er DIAGNOSTIKK, ikke data: kappet, kun
            # printbare tegn, og den når aldri lenger enn til driftsloggen
            # (controlleren rapporterer bare unntakstypen videre).
            raa = (stderr_ut[0] if stderr_ut else b"").decode("utf-8",
                                                              "replace")
            hale = "".join(c for c in raa if c.isprintable())[:200].strip()
            raise Motorfeil(f"motor exit {p.returncode}"
                            + (f": {hale}" if hale else ""))
        try:
            d = json.loads(ut.decode("utf-8"))
            return Motorresultat(
                regelsett_versjon=str(d["regelsett_versjon"])[:64],
                varighet_ms=max(0, int(d["varighet_ms"])),
                sider=tuple(d.get("sider") or ()),
                funn=tuple(d.get("funn") or ()),
                blokkert=tuple(d.get("blokkert") or ()),
                avkortet=tuple(d.get("avkortet") or (False, None, None)))
        except (ValueError, KeyError, TypeError) as e:
            raise Motorfeil("motorutdata uleselig") from e
