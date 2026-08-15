"""PR-011 CP4: kontrakt UI ↔ lese-API (klarsignal V1).

M-1-flaten leser NØYAKTIG bestemte felt fra de sju leseendepunktene. Denne
porten pinner dem mot handler-kilden: dropper eller omdøper backend et felt
UI-et er avhengig av, ryker denne testen FØR en drift når produksjon. Den
utfyller PR-008s egne formtester (som beviser at feltene har rett verdi) —
her beviser vi at feltene UI-et faktisk konsumerer, finnes.

Kildegrep, ikke DB: feltnavnene står som strengliteraler i handlerne.
"""
import re
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1] / "api"
UI_JS = Path(__file__).resolve().parents[1] / "ui" / "static" / "js"
KILDE = "\n".join((API / f).read_text(encoding="utf-8")
                  for f in ("lesing.py", "app.py", "sesjon.py"))

# Feltene UI-et leser, per endepunkt (se platform/core/ui/static/js/flater/*).
KONTRAKT = {
    "/v1/oversikt": ["vindu_slutt", "tidssone", "tillatt", "stoppet",
                     "unntak", "totalt"],
    "/v1/beslutninger": ["rader", "neste_cursor", "policybeslutning",
                         "begrunnelse", "handling"],
    "/v1/beslutninger/{id}": ["resultat", "art", "evidensstatus",
                              "sen_evidens", "konflikt_evidens",
                              "policy_versjon", "sikkerhet", "sak_finnes",
                              "feil_aarsak"],
    "/v1/unntak": ["saker", "kategori", "prioritet", "status", "sakstype"],
    "/v1/unntak/{id}": ["kategori", "sakstype", "status", "prioritet",
                        "begrunnelse"],
    "/v1/unntak/{id}/historikk": ["rader", "hendelse", "fra_status",
                                  "til_status"],
    "/v1/policy/aktiv": ["versjon", "roller", "handlinger", "modus",
                         "grenser", "vilkaar", "verifikatorer",
                         "belop_maks", "valuta", "tidsvindu", "frekvens",
                         "offentlig_id", "betrodd_for",
                         "kan_fastsla_permanent"],
    "/v1/sesjon": ["tenant", "scopes"],
}


@pytest.mark.parametrize("endepunkt,felt", [
    (e, f) for e, felt in KONTRAKT.items() for f in felt
])
def test_backend_leverer_feltet_ui_leser(endepunkt, felt):
    assert f'"{felt}"' in KILDE, \
        f"UI leser {felt!r} fra {endepunkt}, men feltet finnes ikke i handler-kilden"


def test_resultat_arter_er_de_ni_ui_kjenner():
    """UI-ets KJENTE_ARTER (gate 9) må dekke akkurat backends _ART_FOR_STATUS
    + de fire direkte artene. Nye motorarter skal tvinge en bevisst
    UI-oppdatering, ikke gjettes."""
    ui = (Path(__file__).resolve().parents[1] / "ui" / "static" / "js"
          / "flater" / "beslutninger.js").read_text(encoding="utf-8")
    for art in ("policy_stoppet", "sideeffektfri_tillatt", "til_unntak",
                "utforelsesdata_ikke_tilgjengelig", "outbox_opprettet",
                "outbox_plukket", "outbox_utfort", "outbox_feilet",
                "outbox_kansellert"):
        assert art in KILDE, f"{art} finnes ikke i backend?"
        assert art in ui, f"UI KJENTE_ARTER mangler {art}"


def _kunderoller_fra_ui() -> dict[str, frozenset[str]]:
    """`KUNDEROLLER` i plattformdata.js, som {rolle: scopes}."""
    kilde = (UI_JS / "plattformdata.js").read_text(encoding="utf-8")
    blokk = re.search(r"export const KUNDEROLLER = \[(.*?)\n\];", kilde, re.S)
    assert blokk, "KUNDEROLLER finnes ikke i plattformdata.js"
    ut: dict[str, frozenset[str]] = {}
    for rolle in re.finditer(r'id:\s*"([a-z]+)",.*?scopes:\s*\[(.*?)\]',
                             blokk.group(1), re.S):
        ut[rolle.group(1)] = frozenset(re.findall(r'"([a-z:]+)"',
                                                  rolle.group(2)))
    return ut


def test_rolleguiden_lover_bare_fullmakter_rollen_faktisk_har():
    """Kundeflatens rolleguide er kundens grunnlag for å TILDELE roller. Lover
    den mer enn rollen har, oppdager kunden det først på en 403 — slik
    `godkjenner` ble beskrevet som å attestere policy, mens attestasjon krever
    `policy:activate` og bare `policyforvalter` har den. Guiden pinnes derfor
    mot den kanoniske utledningen, ikke mot prosa."""
    from api.autorisasjon import ROLLE_TIL_SCOPES

    guide = _kunderoller_fra_ui()
    assert guide, "rolleguiden er tom — regexen eller kilden har flyttet seg"
    for rolle, scopes in guide.items():
        assert rolle in ROLLE_TIL_SCOPES, \
            f"rolleguiden viser {rolle!r}, som ikke finnes i ROLLE_TIL_SCOPES"
        assert scopes == set(ROLLE_TIL_SCOPES[rolle]), (
            f"rolleguiden for {rolle!r} er ute av takt med autorisasjon.py: "
            f"guide={sorted(scopes)} kanonisk={sorted(ROLLE_TIL_SCOPES[rolle])}")
