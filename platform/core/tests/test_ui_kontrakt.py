"""PR-011 CP4: kontrakt UI ↔ lese-API (klarsignal V1).

M-1-flaten leser NØYAKTIG bestemte felt fra de sju leseendepunktene. Denne
porten pinner dem mot handler-kilden: dropper eller omdøper backend et felt
UI-et er avhengig av, ryker denne testen FØR en drift når produksjon. Den
utfyller PR-008s egne formtester (som beviser at feltene har rett verdi) —
her beviser vi at feltene UI-et faktisk konsumerer, finnes.

Kildegrep, ikke DB: feltnavnene står som strengliteraler i handlerne.
"""
import json
import re
from pathlib import Path

import pytest

API = Path(__file__).resolve().parents[1] / "api"
UI_JS = Path(__file__).resolve().parents[1] / "ui" / "static" / "js"
MODULER = Path(__file__).resolve().parents[2] / "modules"
KILDE = "\n".join((API / f).read_text(encoding="utf-8")
                  for f in ("lesing.py", "app.py", "sesjon.py",
                            "utrulling.py"))

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
    # `arsak` (043): saker FØDT av et oppdrag bærer grunnen sin der —
    # `kompensasjon_kreves`, `irreversibel_utfort` og
    # `reversibilitet_ukjent` betyr at et menneske må rydde opp (eller
    # undersøke) utenfor systemet. Flaten viser den i både listen
    # (kolonne) og detaljen (rad + forklaring); faller feltet ut av
    # backend, er de sakene igjen ikke til å skille fra en arvet sak.
    "/v1/unntak": ["saker", "kategori", "prioritet", "status", "sakstype",
                   "arsak"],
    "/v1/unntak/{id}": ["kategori", "sakstype", "status", "prioritet",
                        "begrunnelse", "arsak"],
    "/v1/unntak/{id}/historikk": ["rader", "hendelse", "fra_status",
                                  "til_status"],
    "/v1/policy/aktiv": ["versjon", "roller", "handlinger", "modus",
                         "grenser", "vilkaar", "verifikatorer",
                         "belop_maks", "valuta", "tidsvindu", "frekvens",
                         "offentlig_id", "betrodd_for",
                         "kan_fastsla_permanent"],
    # Lista over aktive policyer: utveien når `/v1/policy/aktiv` (med rette)
    # ikke kan velge mellom flere. Flaten leser NØYAKTIG disse feltene.
    "/v1/policy/aktive": ["policyer", "policy_id", "versjon"],
    "/v1/sesjon": ["tenant", "scopes"],
    # Utrullingsplanen. Feltene leses av flater/admin.js og
    # flater/kundeadmin.js — de har ingen tenanttabell å falle tilbake på.
    "/v1/utrulling": ["plattformdrift", "tenanter", "navn", "plan", "moduler",
                      "neste"],
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


def test_hver_kanonisk_rolle_har_navn_i_begge_lokalene():
    """Skallet viser øktens roller med `t("ui.rolle.<rolle>")`, og fallbacken
    er den rå norske identifikatoren. Mangler nøkkelen, står det «sikkerhet» i
    et ellers engelsk grensesnitt — og den som skal vite hvilken fullmakt hen
    sitter med, får et ord hen ikke nødvendigvis leser.

    Rollemengden eies av `ROLLE_TIL_SCOPES`. Legges en rolle til der, skal
    denne porten si fra med én gang, ikke først når en kunde med den rollen
    logger inn."""
    from api.autorisasjon import ROLLE_TIL_SCOPES

    rot = Path(__file__).resolve().parents[3] / "locales"
    for sprak in ("nb", "en"):
        tekster = json.loads((rot / f"{sprak}.json").read_text(encoding="utf-8"))
        for rolle in ROLLE_TIL_SCOPES:
            nokkel = f"ui.rolle.{rolle}"
            assert tekster.get(nokkel), (
                f"{sprak}.json mangler {nokkel!r} — skallet ville vist den rå "
                f"identifikatoren {rolle!r} i stedet for et rollenavn")


def _valutaer_fra_ui() -> list[str]:
    """`VALUTAER` i flater/policyeditor.js — kodene nedtrekket tilbyr."""
    kilde = (UI_JS / "flater" / "policyeditor.js").read_text(encoding="utf-8")
    blokk = re.search(r"const VALUTAER = \[(.*?)\n\];", kilde, re.S)
    assert blokk, "VALUTAER finnes ikke i policyeditor.js"
    return re.findall(r'"([A-Z]{3})"', blokk.group(1))


def test_valutanedtrekket_er_den_kanoniske_mengden():
    """Valutafeltet i policyeditoren er et NEDTREKK, og et nedtrekk er både et
    tak og en bunn: koder som mangler kan eier ikke velge selv om serveren
    godtar dem, og koder som ikke skulle vært der kan velges og aktiveres —
    for så å bli lest som `policy_korrupt`, siden `_valider_grenser` måler mot
    `ISO4217` og ikke mot `^[A-Z]{3}$`.

    Mengden eies av `ISO4217`. Endres registeret der, skal denne porten si fra
    med én gang — ikke en kunde som ikke finner valutaen sin."""
    from api.lesing import ISO4217

    ui = _valutaer_fra_ui()
    assert len(ui) == len(set(ui)), "VALUTAER har dubletter"
    assert set(ui) == set(ISO4217), (
        "valutanedtrekket er ute av takt med ISO4217: "
        f"mangler={sorted(set(ISO4217) - set(ui))} "
        f"ukjente={sorted(set(ui) - set(ISO4217))}")


def test_ingen_tenantdata_i_offentlige_ressurser():
    """`/ui/{sti}` og `/ui/locale/{sprak}` serveres UTEN øktsjekk, og den
    anonyme landingssiden importerer klientbunten. Lå tenantregisteret der,
    kunne hvem som helst laste ned hver kundes navn, plan, modultildeling og
    neste steg — uansett hvilket filter admin-flaten gjorde i DOM-en etterpå.

    `offentlige_ressurser.test.js` håndhever den samme grensen fra JS-siden,
    men bare mot mønstre. Denne porten leser de FAKTISKE radene registeret
    serverer, så et nytt kundenavn er dekket i det øyeblikket det legges
    inn — uten at noen må huske å oppdatere et mønster.

    Ett unntak: plattformens egen tenant-id ER merkenavnet (`disponit`), og
    merkenavnet er chrome som lovlig ligger i det offentlige locale-settet
    (`app.navn`, `__Host-disponit_csrf` osv., se `sesjon.py:167`). Unntaket
    er en EKSAKT match mot `app.navn` lowercased — ikke en prefiks og ikke
    hele raden — så `navn`-feltet («Disponit (plattform)») og alle andre
    tenanters id/navn (nordvik, bjørkli, granmo) er fortsatt dekket."""
    from api.utrulling import _UTRULLING

    locale_dir = Path(__file__).resolve().parents[3] / "locales"
    merkenavn = json.loads((locale_dir / "nb.json")
                           .read_text(encoding="utf-8"))["app.navn"].lower()

    offentlig = list(UI_JS.rglob("*.js"))
    offentlig += sorted(locale_dir.glob("*.json"))
    assert offentlig, "fant ingen serverte ressurser å sjekke"
    for sti in offentlig:
        tekst = sti.read_text(encoding="utf-8").lower()
        for rad in _UTRULLING:
            for verdi in (rad["id"], rad["navn"]):
                if verdi.lower() == merkenavn:
                    continue
                assert verdi.lower() not in tekst, (
                    f"tenantdata ({verdi!r}) ligger i {sti.name}, som serveres "
                    f"uten øktsjekk")


def _modulstatus_fra_ui() -> dict[int, str]:
    """`MODULSTATUS` i plattformdata.js, som {modulnummer: status}."""
    kilde = (UI_JS / "plattformdata.js").read_text(encoding="utf-8")
    blokk = re.search(r"export const MODULSTATUS = \{(.*?)\n\};", kilde, re.S)
    assert blokk, "MODULSTATUS finnes ikke i plattformdata.js"
    return {int(m.group(1)): m.group(2)
            for m in re.finditer(r'(\d+):\s*"([a-z_]+)"', blokk.group(1))}


def _status_fra_manifest(modul_id: int) -> str:
    """Manifestets TO akser → UI-ets ene ord. Ingen manifest = `planlagt`.

    Flatens `i_drift` er STRENGERE enn registerets: her betyr det utrullet
    hos kunder, altså `driftstilstand: produksjon`. `staging` faller sammen
    med `ikke_i_drift`, fordi forskjellen mellom «kjører ingen steder» og
    «kjører på vår egen testserver» ikke er noe en besøkende kan bruke.
    """
    import yaml

    treff = sorted(MODULER.glob(f"m{modul_id:02d}_*/manifest.yaml"))
    if not treff:
        return "planlagt"
    m = yaml.safe_load(treff[0].read_text(encoding="utf-8"))
    if m.get("driftstilstand") == "produksjon":
        return "i_drift"
    return "klargjort" if m.get("status") == "aktiv" else "bygges"


def test_modulstatus_folger_manifestene():
    """Flatens modulstatus er en PÅSTAND OM DRIFT, og manifestene er
    autoriteten på den. Uten denne porten kunne landingssiden reklamere med
    «tre moduler i drift» mens hvert manifest sa `driftstilstand:
    ikke_i_drift` — nøyaktig den sammenblandingen manifestene innfører to
    akser for å unngå (`status` = godkjent, `driftstilstand` = kjører faktisk).
    Drift i én av retningene skal ryke her, ikke hos kunden."""
    ui = _modulstatus_fra_ui()
    assert ui, "MODULSTATUS er tom — regexen eller kilden har flyttet seg"
    for modul_id, status in ui.items():
        forventet = _status_fra_manifest(modul_id)
        assert status == forventet, (
            f"M-{modul_id} står som {status!r} i UI-et, men manifestet gir "
            f"{forventet!r} (status/driftstilstand i "
            f"platform/modules/m{modul_id:02d}_*/manifest.yaml)")


def test_modulstatus_dekker_manifestene():
    """Motsatt retning: et NYTT manifest skal tvinge en bevisst
    UI-oppdatering, ikke bli usynlig fordi kartet aldri ble utvidet."""
    ui = _modulstatus_fra_ui()
    for sti in sorted(MODULER.glob("m*_*/manifest.yaml")):
        modul_id = int(re.match(r"m(\d+)_", sti.parent.name).group(1))
        assert modul_id in ui, \
            f"{sti.parent.name} har manifest, men mangler i MODULSTATUS"
