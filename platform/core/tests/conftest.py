"""Testoppsett: finn repo-rot og gjør core importerbar + policies synlig."""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CORE = REPO_ROOT / "platform" / "core"
POLICIES = REPO_ROOT / "policies"
sys.path.insert(0, str(CORE))
