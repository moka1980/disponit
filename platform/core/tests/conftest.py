"""Testoppsett: finn repo-rot og gjør core importerbar + policies synlig."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE = REPO_ROOT / "platform" / "core"
POLICIES = REPO_ROOT / "policies"
sys.path.insert(0, str(CORE))
# PR-015: driftslaget ligger ved siden av core (`platform/drift`), ikke inni
# det. Arbeiderne er timerdrevne prosesser, ikke en del av forespørselsveien —
# og den statiske porten «api/ importerer aldri m37/» finnes nettopp for å
# holde arbeid ute av request-veien. Å legge dem under `core/api` ville gjort
# den grensen uleselig.
sys.path.insert(0, str(REPO_ROOT / "platform"))
