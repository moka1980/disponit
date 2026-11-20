"""Playbook-katalogen for M-35 (ADR-008).

089 lar tjenestekartet bære en playbook-referanse på formen
`navn@sha256` og sier at godkjenningen er git-merge (dom 3). Det dommen
ikke sa, var HVOR filene bor og hvem som regner hashen. Svaret står her:

- filene bor i `platform/core/playbooks/kontinuitet/<navn>.yaml` og
  følger releasen — det som er merget, er godkjent;
- hashen er sha256 av filens BYTES, regnet av serveren;
- en klient sender bare NAVNET. Referansen settes sammen her, så ingen
  kan binde et kartinnslag til bytes som ikke finnes i releasen.

Katalogen leses én gang per prosess: en release bytter ikke filer under
seg, og et bytte av release er en ny prosess.
"""
from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path

import yaml

ROT = Path(__file__).resolve().parent / "playbooks" / "kontinuitet"

#: Navnedelen av 089s CHECK på `playbook_ref` — samme uttrykk, så et navn
#: katalogen godtar aldri kan avvises av basen.
NAVN_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
FELT = {"navn", "tittel", "naar", "steg"}
MAKS_STEG = 20


class Katalogfeil(ValueError):
    """En playbookfil som ikke holder formen. Felles ved lasting — en
    halv katalog er verre enn ingen."""


def _les(sti: Path) -> dict:
    raa = sti.read_bytes()
    try:
        d = yaml.safe_load(raa.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as e:
        raise Katalogfeil(f"{sti.name}: ikke gyldig YAML ({e})") from e
    if not isinstance(d, dict) or set(d) != FELT:
        raise Katalogfeil(f"{sti.name}: feltene skal være nøyaktig"
                          f" {sorted(FELT)}")
    navn = d["navn"]
    if not isinstance(navn, str) or not NAVN_RE.fullmatch(navn):
        raise Katalogfeil(f"{sti.name}: ugyldig navn")
    if sti.name != f"{navn}.yaml":
        # Navnet er pekeren i kartet. Skiller fil og navn lag, peker
        # kartet på en fil ingen finner.
        raise Katalogfeil(f"{sti.name}: filnavnet skal være {navn}.yaml")
    for felt in ("tittel", "naar"):
        if not isinstance(d[felt], str) or not d[felt].strip():
            raise Katalogfeil(f"{sti.name}: «{felt}» mangler")
    steg = d["steg"]
    if (not isinstance(steg, list) or not 1 <= len(steg) <= MAKS_STEG
            or not all(isinstance(s, str) and s.strip() for s in steg)):
        raise Katalogfeil(f"{sti.name}: «steg» skal være 1–{MAKS_STEG}"
                          " setninger")
    sha = hashlib.sha256(raa).hexdigest()
    return {"navn": navn, "tittel": d["tittel"].strip(),
            "naar": d["naar"].strip(), "steg": [s.strip() for s in steg],
            "sha256": sha, "ref": f"{navn}@{sha}"}


@lru_cache(maxsize=1)
def katalog() -> tuple[dict, ...]:
    """Alle playbookene i releasen, sortert på navn."""
    return tuple(_les(p) for p in sorted(ROT.glob("*.yaml")))


def ref_for(navn: object) -> str | None:
    """`navn@sha256` for en playbook i katalogen, ellers None."""
    if not isinstance(navn, str):
        return None
    for p in katalog():
        if p["navn"] == navn:
            return p["ref"]
    return None
