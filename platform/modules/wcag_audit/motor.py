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
from dataclasses import dataclass, field


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
        try:
            p = subprocess.run(
                self.kommando, input=json.dumps(payload).encode("utf-8"),
                capture_output=True, timeout=self.tidsavbrudd_s, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise Motorfeil(f"motorkjøring: {type(e).__name__}") from e
        if p.returncode != 0:
            raise Motorfeil(f"motor exit {p.returncode}")
        try:
            d = json.loads(p.stdout.decode("utf-8"))
            return Motorresultat(
                regelsett_versjon=str(d["regelsett_versjon"])[:64],
                varighet_ms=max(0, int(d["varighet_ms"])),
                sider=tuple(d.get("sider") or ()),
                funn=tuple(d.get("funn") or ()),
                blokkert=tuple(d.get("blokkert") or ()),
                avkortet=tuple(d.get("avkortet") or (False, None, None)))
        except (ValueError, KeyError, TypeError) as e:
            raise Motorfeil("motorutdata uleselig") from e
