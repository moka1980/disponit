"""Demo-seeden må sette claimer-rollen i HVER transaksjon der den kaller
en claimer-eid funksjon (Codex P1 + Cursor P1-1, runde 9).

`deploy/staging/seed-rekruttering-demo.py` kobler seg som migrator
(`DISPONIT_MIGRATOR_URL`). Funksjonene den kaller — `opprett_
rekrutteringsprosess`, `lukk_rekrutteringsprosess`, `opprett_
utsendingsliste` — eies av `disponit_m37_claimer`, 057 §7 og 056 revoker
PUBLIC, og `migrer.py` gir EXECUTE bare til RUNTIME-rollen. Migrator har
`INHERIT FALSE` og altså ingen vei inn utenom `SET LOCAL ROLE`.

Og `SET LOCAL ROLE` er LOCAL: den dør ved COMMIT. Seeden committer fire
ganger, så rollen må settes på nytt i hver bolk som trenger den — nøyaktig
det seed-3 IKKE gjorde. Feilen er dyr fordi den kommer MIDT I: seed-1..2
er alt committet når `lukk_rekrutteringsprosess` kaster
`InsufficientPrivilege`, så demoen etterlater kandidatdata og et `utfort`
oppdrag uten den signerbare listen den finnes for.

Porten er statisk og leser kilden med `ast` — seeden kan ikke importeres
(den krever base og miljø ved kall), og skal kunne måles overalt suiten
kjører. Den måler kildens LINJEREKKEFØLGE, som er den samme som
kjørerekkefølgen i et flatt skript som dette: for hvert kall til en
claimer-eid funksjon skal det stå en rollesetting mellom kallet og
nærmeste foregående `commit()`. Eierskapsfasiten hentes fra
`eierskap-reparasjon.sql` selv, gjennom `test_eierskap._design_fra_sql`,
så en funksjon som bytter eier ikke etterlater en glemt kopi her.
"""
from __future__ import annotations

import ast
import pathlib

from .test_eierskap import _design_fra_sql

ROT = pathlib.Path(__file__).resolve().parents[3]
KILDE = ROT / "deploy" / "staging" / "seed-rekruttering-demo.py"

ROLLE = "SET LOCAL ROLE disponit_m37_claimer"


def _claimereide_funksjoner() -> set[str]:
    """Navnene på funksjonene `disponit_m37_claimer` eier."""
    return {ident.split("(", 1)[0]
            for (art, ident), eier in _design_fra_sql().items()
            if art == "FUNCTION" and eier == "disponit_m37_claimer"}


def _seedens_kall() -> list[tuple[int, str]]:
    """(linje, sql) for hver `<conn>.execute("…")` og hver `.commit()`.

    `.commit()` gjengis som `COMMIT`, så bolkene kan leses av samme
    liste. Kall med et ikke-konstant første argument hoppes over: de
    finnes ikke i denne fila, og en port som gjettet på dem ville vært
    en simulator, ikke en måling.
    """
    tre = ast.parse(KILDE.read_text(encoding="utf-8"))
    kall: list[tuple[int, str]] = []
    for node in ast.walk(tre):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "commit":
            kall.append((node.lineno, "COMMIT"))
        elif (node.func.attr == "execute" and node.args
              and isinstance(node.args[0], ast.Constant)
              and isinstance(node.args[0].value, str)):
            kall.append((node.lineno, node.args[0].value))
    return sorted(kall)


def test_claimerkall_star_under_claimerrollen():
    """Drepende mutasjon: fjern `SET LOCAL ROLE` foran ETT av kallene.

    Da står funksjonen igjen som migrator, som verken eier den eller har
    EXECUTE — `test_rekruttering_http._reap` dokumenterer nettopp den
    `InsufficientPrivilege`-en.
    """
    eide = _claimereide_funksjoner()
    assert "lukk_rekrutteringsprosess" in eide, (
        "eierskapsfasiten kjenner ikke lukkingen — porten måler ingenting")
    rolle_satt = False
    sett: list[str] = []
    for _linje, sql in _seedens_kall():
        if sql == "COMMIT":
            rolle_satt = False
        elif sql == ROLLE:
            rolle_satt = True
        for navn in eide:
            if navn + "(" in sql:
                sett.append(navn)
                assert rolle_satt, (
                    f"seeden kaller {navn} uten `{ROLLE}` i samme"
                    " transaksjon — migrator har ingen EXECUTE, og"
                    " seeden krasjer med alt før dette committet")
    assert sett, "porten fant ingen claimer-kall i seeden — les kilden"


def test_seeden_lukker_prosessen_i_utfort_transaksjonen():
    """Lukkingen og `utfort` er ÉN overgang (Codex P2, runde 8), og
    porten over ville vært fornøyd med en lukking som sto hvor som helst
    bak en rollesetting. Her måles at de to fortsatt deler bolk: ingen
    `commit()` mellom oppdragets `utfort` og lukkingen.
    """
    etter_utfort = False
    for _linje, sql in _seedens_kall():
        if "status='utfort'" in sql:
            etter_utfort = True
        elif etter_utfort and sql == "COMMIT":
            raise AssertionError(
                "seeden committer mellom `utfort` og lukkingen — en"
                " halv overgang etterlater den forlatte prosessen"
                " `reap_kandidatdata` måler fra fødselen")
        elif etter_utfort and "lukk_rekrutteringsprosess(" in sql:
            return
    raise AssertionError("fant ikke lukkingen etter `utfort` i seeden")
