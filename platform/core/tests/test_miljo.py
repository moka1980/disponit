"""Miljøavlesningen — ÉN kilde for `DISPONIT_MILJO` (Codex P2 til PR #42).

Verdien styrer to ting som aldri kan få lov til å sprike: hvilke policystatuser
som binder ekte beslutninger (`api/policyregister.tillatte_statuser`), og hva
forsiden lover kunden (`/ui/oppsett.json` → «Tilgjengelig» eller «Kommer»).
De ble lest hver for seg, med hver sin tolkning — rå sammenligning i registeret,
`.strip()` i UI-endepunktet — og en padded verdi leste de derfor motsatt:
forsiden lovet produksjon mens `utkast` fortsatt bandt beslutninger.

Fila har to porter. Den første måler regelen (fail-closed, eksakt match), den
andre at det fortsatt bare finnes ÉN leser: en ny `os.environ.get(
"DISPONIT_MILJO")` et sted i core er nøyaktig måten spriket oppsto på, og den
skal koste en rød test — ikke en runde i produksjon.

Ingen av dem trenger Postgres eller nettverk.
"""
import re
from pathlib import Path

import miljo

CORE = Path(__file__).resolve().parents[1]


def _sett(monkeypatch, verdi):
    if verdi is None:
        monkeypatch.delenv("DISPONIT_MILJO", raising=False)
    else:
        monkeypatch.setenv("DISPONIT_MILJO", verdi)


def test_miljo_er_fail_closed_og_eksakt(monkeypatch):
    """Kun den nøyaktige strengen `produksjon` er produksjon.

    Padding er med i matrisen med vilje: `opp.sh` avviser en padded verdi ved
    utrulling, men en prosess startet for hånd (systemd-override, en operatør i
    et skall) ser den. Da skal den koste et løfte, ikke gi et.
    """
    for verdi, forventet in (("produksjon", "produksjon"),
                             ("staging", "staging"),
                             ("produksjonn", "staging"),
                             ("PRODUKSJON", "staging"),
                             (" produksjon ", "staging"),
                             ("produksjon\n", "staging"),
                             ("", "staging"),
                             (None, "staging")):
        _sett(monkeypatch, verdi)
        assert miljo.miljo() == forventet, repr(verdi)
        assert miljo.er_produksjon() is (forventet == "produksjon"), repr(verdi)


def test_forsiden_og_policyregisteret_leser_samme_miljo(monkeypatch):
    """Løftet og regelverket bak det svarer likt på hver eneste verdi.

    Dette er selve funnet: `/ui/oppsett.json` sa «produksjon» for
    `" produksjon "`, mens `tillatte_statuser()` ga staging-settet — altså
    `utkast` og `validert_pilot` bindende, bak en forside som lovet drift.
    Testen spør det EKTE endepunktet — svaret forsiden faktisk får — og det
    ekte registeret, og krever at de er enige på hver eneste verdi.
    """
    from starlette.applications import Starlette
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from api.policyregister import PRODUKSJONSSTATUSER, tillatte_statuser
    from ui import server as uiserver

    klient = TestClient(Starlette(routes=[
        Route("/ui/oppsett.json", uiserver.ui_oppsett, methods=["GET"])]))

    monkeypatch.delenv("DISPONIT_TILLATTE_POLICYSTATUSER", raising=False)
    for verdi in ("produksjon", "staging", "produksjonn", " produksjon ",
                  "PRODUKSJON", "", None):
        _sett(monkeypatch, verdi)
        forsiden = klient.get("/ui/oppsett.json").json()["miljo"]
        statuser = tillatte_statuser()
        assert (forsiden == "produksjon") is (statuser == PRODUKSJONSSTATUSER), (
            f"DISPONIT_MILJO={verdi!r}: forsiden og policyregisteret er uenige "
            f"om miljøet — forsiden={forsiden!r}, "
            f"tillatte statuser={sorted(statuser)}")


def test_kun_miljomodulen_leser_miljovariabelen():
    """Én kilde per bekymring (STRUKTUR §5), håndhevet.

    `DISPONIT_MILJO` skal leses fra miljøet ETT sted i core. Alle andre går
    gjennom `miljo.miljo()` / `miljo.er_produksjon()`. Tester er unntatt: de
    SETTER variabelen, og `test_deploy_miljofil.py` måler deploy-skriptenes
    egen port mot den.
    """
    monster = re.compile(r"""environ(?:\.get\(|\[)\s*["']DISPONIT_MILJO["']""")
    lesere = []
    for sti in CORE.rglob("*.py"):
        if "tests" in sti.relative_to(CORE).parts or sti.name == "miljo.py":
            continue
        if monster.search(sti.read_text(encoding="utf-8")):
            lesere.append(str(sti.relative_to(CORE)))
    assert not lesere, (
        "leser DISPONIT_MILJO direkte i stedet for gjennom miljo.py: "
        + ", ".join(lesere))
