"""En `krav_id` skal registreres ÉN gang.

DENNE PORTEN ER SVARET PÅ ET NESTENUHELL. Klynge 5-fundamentet skrev

    M11_INVARIANTER = (...)          # adressevalidering
    KRAVGRENSER["m11-v1"] = {...}

uten å vite at begge fantes fra før: `m11-v1` er SELVTESTENS grense
(migrasjon 091), med to sikkerhetsinvarianter — `hemmelighet_i_rapport`
og `destruktiv_probe`. Python sier ingenting om en modulvariabel som
tildeles på nytt, og `dict[...] = ...` overskriver stille.

RESULTATET VAR AT SELVTESTENS SIKKERHETSGRENSE BLE BYTTET UT, og
HELE SUITEN VAR GRØNN: 3740 porter, null feil. Ingenting pinner
innholdet i en registrert grense, så et navnesammenfall kan fjerne en
sikkerhetsinvariant uten at én test merker det.

Porten leser KILDEN, ikke det ferdig evaluerte modulnivået — for på det
tidspunktet har overskrivingen alt skjedd, og bare den siste står igjen.
"""
from __future__ import annotations

import ast
import collections
from pathlib import Path

SKJEMA = (Path(__file__).resolve().parents[1] / "manifestskjema.py")


def _kilde() -> ast.Module:
    return ast.parse(SKJEMA.read_text(encoding="utf-8"))


def _registrerte_krav_id() -> list[str]:
    """Hver `KRAVGRENSER["x"] = ...` på modulnivå, i rekkefølge."""
    ut: list[str] = []
    for node in _kilde().body:
        if not isinstance(node, ast.Assign):
            continue
        for mal in node.targets:
            if (isinstance(mal, ast.Subscript)
                    and isinstance(mal.value, ast.Name)
                    and mal.value.id == "KRAVGRENSER"
                    and isinstance(mal.slice, ast.Constant)
                    and isinstance(mal.slice.value, str)):
                ut.append(mal.slice.value)
    return ut


def _invariantnavn() -> list[str]:
    """Hver `*_INVARIANTER = (...)` på modulnivå, i rekkefølge.

    BÅDE `Assign` OG `AnnAssign`: listene er skrevet med annotasjon
    (`M11_INVARIANTER: tuple[str, ...] = (...)`), og en walker som bare
    så `Assign` fant null — altså en port som var grønn fordi den ikke
    målte noe. `test_porten_maaler_noe` fanget det.
    """
    ut: list[str] = []
    for node in _kilde().body:
        mal_er = []
        if isinstance(node, ast.Assign):
            mal_er = node.targets
        elif isinstance(node, ast.AnnAssign):
            mal_er = [node.target]
        for mal in mal_er:
            if isinstance(mal, ast.Name) and mal.id.endswith("_INVARIANTER"):
                ut.append(mal.id)
    return ut


def test_porten_maaler_noe():
    assert len(_registrerte_krav_id()) >= 25, _registrerte_krav_id()
    assert len(_invariantnavn()) >= 20, _invariantnavn()


def test_ingen_krav_id_registreres_to_ganger():
    """MUTASJONEN SOM DREPER DENNE: registrer `m11-v1` en gang til."""
    tell = collections.Counter(_registrerte_krav_id())
    dubletter = sorted(k for k, n in tell.items() if n > 1)
    assert dubletter == [], (
        "KRAVGRENSER-oppføringen overskrives stille, og bare den siste"
        " står igjen — en registrert grense kan da bytte ut en annen"
        " modul sin: " + ", ".join(dubletter))


def test_ingen_invariantliste_defineres_to_ganger():
    """…og navnet over den, som er halve fellen.

    `M11_INVARIANTER` fantes; den nye tildelingen skygget den, og
    grensen som pekte på navnet fikk den nye listen.
    """
    tell = collections.Counter(_invariantnavn())
    dubletter = sorted(k for k, n in tell.items() if n > 1)
    assert dubletter == [], (
        "invariantlisten tildeles på nytt og skygger den forrige: "
        + ", ".join(dubletter))
