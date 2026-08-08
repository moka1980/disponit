"""systemd-credentials -> miljø (PR-009 v3 §5).

`LoadCredential=` er valgt for ALLE hemmeligheter: filene er root-eide,
eksponeres kun for riktig unit i `$CREDENTIALS_DIRECTORY`, og havner aldri
i `systemctl show-environment` eller i et environment-dump. Prosessene
leser dem HER, én gang ved oppstart — `EnvironmentFile` brukes kun for
ikke-hemmelig konfigurasjon.

Utenfor systemd (tester, CLI, utviklermaskin) finnes ikke katalogen, og
funksjonen er en no-op — miljøvariablene settes da som før. `setdefault`
gjør rekkefølgen ufarlig: en eksplisitt satt variabel vinner alltid over
credential-fila, så en test aldri kan arve stagings hemmeligheter.
"""
from __future__ import annotations

import os
from pathlib import Path


def last_credentials() -> int:
    """-> antall variabler hentet fra $CREDENTIALS_DIRECTORY."""
    katalog = os.environ.get("CREDENTIALS_DIRECTORY")
    if not katalog:
        return 0
    antall = 0
    for fil in Path(katalog).iterdir():
        if fil.is_file() and fil.name not in os.environ:
            os.environ[fil.name] = fil.read_text(encoding="utf-8").strip()
            antall += 1
    return antall
