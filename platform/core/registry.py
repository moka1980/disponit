"""Modulregister v0.1 — plugin-arkitekturen som gjør moduler flyttbare.

Regler (docs/STRUKTUR.md):
  - Core importerer aldri modulkode; registeret leser KUN manifester.
  - En modul aktiveres bare hvis alle avhengigheter finnes og er aktive.
  - Å fjerne/deaktivere en modul påvirker aldri andre moduler — men
    moduler som avhenger av den, nektes aktivering med tydelig grunn.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Modul:
    id: str
    navn: str
    versjon: str
    status: str                      # aktiv | inaktiv | under_utvikling
    #: HVOR modulen kjører. Egen akse fra `status`, som sier om den er
    #: GODKJENT. `aktiv` + `ikke_i_drift` er den vanligste tilstanden rett
    #: etter aksept — og uten dette feltet leses `aktiv` som «ute hos
    #: kunder», som er noe helt annet.
    driftstilstand: str = "ikke_i_drift"
    avhengigheter: list[str] = field(default_factory=list)
    sti: Path | None = None


@dataclass
class RegisterStatus:
    aktive: list[str]
    inaktive: list[str]
    #: Modulene som faktisk kjører et sted. Skilt fra `aktive` med vilje:
    #: en liste over godkjente moduler svarer ikke på hva som er utrullet.
    i_drift: list[str]
    feil: list[str]                  # tomme == konsistent register


def les_manifester(modul_rot: Path) -> list[Modul]:
    """Oppdager moduler ved å skanne mapper — ingen sentral liste å vedlikeholde.
    Ny mappe med manifest = ny modul. Slettet mappe = borte."""
    moduler: list[Modul] = []
    for manifest in sorted(modul_rot.glob("*/manifest.yaml")):
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        moduler.append(Modul(
            id=str(data.get("id", manifest.parent.name)),
            navn=str(data.get("navn", "")),
            versjon=str(data.get("versjon", "0")),
            status=str(data.get("status", "under_utvikling")),
            driftstilstand=str(data.get("driftstilstand", "ikke_i_drift")),
            avhengigheter=[str(a) for a in (data.get("avhengigheter") or [])],
            sti=manifest.parent,
        ))
    return moduler


def valider(moduler: list[Modul]) -> RegisterStatus:
    """Konsistenssjekk. Kjøres i CI og ved oppstart; feil blokkerer aktivering."""
    per_id = {m.id: m for m in moduler}
    feil: list[str] = []
    if len(per_id) != len(moduler):
        sett: set[str] = set()
        for m in moduler:
            if m.id in sett:
                feil.append(f"duplisert modul-id '{m.id}'")
            sett.add(m.id)
    for m in moduler:
        # DRIFT KREVER AKSEPT. En modul som kjører et sted uten å ha bestått
        # sin egen sjekkliste er nøyaktig den tilstanden hele
        # manifestrutinen finnes for å hindre — og uten denne kontrollen
        # ville det nye feltet vært en etikett uten konsekvens.
        if m.driftstilstand != "ikke_i_drift" and m.status != "aktiv":
            feil.append(f"'{m.id}' har driftstilstand '{m.driftstilstand}'"
                        f" men status '{m.status}' — drift krever aktiv")
        if m.status != "aktiv":
            continue
        for dep in m.avhengigheter:
            if dep not in per_id:
                feil.append(f"'{m.id}' avhenger av ukjent modul '{dep}'")
            elif per_id[dep].status != "aktiv":
                feil.append(f"'{m.id}' avhenger av '{dep}' som ikke er aktiv "
                            f"(status: {per_id[dep].status})")
    return RegisterStatus(
        aktive=sorted(m.id for m in moduler if m.status == "aktiv"),
        inaktive=sorted(m.id for m in moduler if m.status != "aktiv"),
        i_drift=sorted(m.id for m in moduler
                       if m.driftstilstand != "ikke_i_drift"),
        feil=feil,
    )
