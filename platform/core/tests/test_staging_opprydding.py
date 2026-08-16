"""Staging-rundturens opprydding må kjenne de samme append-only-tabellene
som testoppryddingen (Codex P2).

`deploy/staging/r1-rundtur.py` nullstiller tenanten sin før den kjører beviset.
Da `policyregister.registrer` begynte å skrive ankerraden, fikk kjøringen en
`policy_hode`-rad å rydde etter seg — og oppryddingen visste ikke om den.
Slettet den `policyer` uten å ta pekeren først, ville FK-en (eller
`hode_ingen_sletting`) veltet nullstillingen, og en rundtur som skal kunne
gjentas kunne ikke lenger sette tenanten sin tilbake.

To lister over de samme tabellene er akkurat den sortens duplikat der den ene
blir oppdatert og den andre glemt. Denne testen binder dem sammen — statisk,
uten database, så den kjører overalt.
"""
from __future__ import annotations

import ast
import pathlib

from .test_api import APPEND_ONLY_TRIGGERE

ROT = pathlib.Path(__file__).resolve().parents[3]
KILDE = ROT / "deploy" / "staging" / "r1-rundtur.py"


def _oppryddingen() -> tuple[tuple[str, ...], dict[str, str]]:
    """(sletterekkefølge, tabell → trigger) lest STATISK ut av skriptet.

    Skriptet kan ikke importeres: det krever `DISPONIT_REPO` og en base allerede
    ved import. Vi leser derfor kildekoden, som er det oppryddingen faktisk er.
    """
    tre = ast.parse(KILDE.read_text(encoding="utf-8"))
    tabeller: tuple[str, ...] | None = None
    triggere: dict[str, str] | None = None
    for node in ast.walk(tre):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "APPEND_ONLY"):
            triggere = ast.literal_eval(node.value)
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            verdier = ast.literal_eval(node.iter)
            if "policyer" in verdier:
                tabeller = verdier
    assert tabeller is not None, "fant ikke sletterekkefølgen i r1-rundtur.py"
    assert triggere is not None, "fant ikke APPEND_ONLY i r1-rundtur.py"
    return tabeller, triggere


def test_ankerraden_ryddes_for_policyer():
    """Pekeren har FK til `policyer` — den må slettes FØRST."""
    tabeller, _ = _oppryddingen()
    assert "policy_hode" in tabeller, (
        "rundturen skriver en ankerrad den aldri rydder — neste kjøring med "
        "samme tenant kan ikke nullstille seg")
    assert tabeller.index("policy_hode") < tabeller.index("policyer"), (
        "ankerraden slettes etter policyraden den peker på — FK-en velter "
        "nullstillingen")


def test_ankerraden_ryddes_med_triggeren_av():
    """`hode_ingen_sletting` hever ubetinget; den må skrus av og på igjen."""
    _, triggere = _oppryddingen()
    assert triggere.get("policy_hode") == "hode_ingen_sletting"
    kilde = KILDE.read_text(encoding="utf-8")
    assert "DISABLE TRIGGER" in kilde and "ENABLE TRIGGER" in kilde, (
        "triggeren skrus ikke på igjen — en avvæpnet sperre er ingen sperre")


def test_append_only_tabeller_ryddes_med_trigger():
    """Rydder staging en append-only-tabell, må den håndtere sperren dens.

    Dette er selve koblingen: neste tabell som får en DELETE-sperre og havner i
    rundturens sletterekkefølge, kan ikke gli gjennom med sperren på.

    Tabeller staging IKKE rydder, sier testen ingenting om — men de blir heller
    ikke hoppet over: ett hopp i CI er ett bevis mindre, og porten i CI ser
    ingen forskjell på «uaktuelt her» og «aldri kjørt».
    """
    tabeller, triggere = _oppryddingen()
    append_only = {t for t, _ in APPEND_ONLY_TRIGGERE}
    maa_handteres = [t for t in tabeller if t in append_only]
    assert maa_handteres, (
        "ingen av staging-tabellene er append-only — da måler denne testen "
        "ingenting, og listene har glidd fra hverandre")
    mangler = [t for t in maa_handteres if t not in triggere]
    assert not mangler, (
        f"{', '.join(mangler)} er append-only, men staging sletter dem med "
        "sperren på")
