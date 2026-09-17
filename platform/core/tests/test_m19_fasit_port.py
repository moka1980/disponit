"""`m19-fasit-v1` — porten står FØR kjøringen (§0).

Settet gir hver av adressekontrollens fem funntyper nøyaktig ett subjekt,
og ett subjekt som skal være RENT. Porten re-regner dommen av
per-subjekt-tabellen artefaktet bærer: et `funnavvik: 0` uten radene bak
seg er produsentens påstand, ikke en måling.

Mutasjonene som feller: et subjekt som fikk feil funntype, et rent
subjekt som fikk et funn, en andre kjøring som fant noe nytt (sveipen er
ikke idempotent), evidens uten aktør, to hendelser som deler
`input_hash`, et sett som ikke dekker alle fem typene, et sett uten den
rene raden, og et artefakt fra en annen produsentflate.
"""
from __future__ import annotations

from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
KRAV = "m19-fasit-v1"
TYPER = ("ukontrollert_adresse", "kontroll_utlopt", "avvist_adresse",
         "utilstrekkelig_metode", "ingen_krav")


def _per():
    rader = [{"merke": t, "ventet": t, "fikk": [t],
              "subjekt_id": f"s-{i}"} for i, t in enumerate(TYPER)]
    rader.append({"merke": "ren", "ventet": None, "fikk": [],
                  "subjekt_id": "s-ren"})
    return rader


def _art(**over):
    import manifestskjema as m
    art = {
        "krav_id": KRAV, "ts": "2026-09-17T22:00:00+00:00", "bestatt": True,
        "oppsett": {"modul": "m19_adresse", "vert": "disponit.com",
                    "runde": "c608b8e8",
                    "tenanter": ["t-m19fasit-med_krav-c608b8e8",
                                 "t-m19fasit-uten_krav-c608b8e8"],
                    "sett_sha256": "a" * 64,
                    "bevisrot_sha256": m.m19_fasit_bevisrot_sha256(),
                    "ukontrollert_dogn": 30, "gyldig_dogn": 180,
                    "godkjente_metoder": ["dokumentert", "levering_bekreftet"]},
        "maalt": {"subjekter": 6, "funnavvik": 0, "avviksliste": [],
                  "per_subjekt": _per(),
                  "sveip1_nye": 6, "sveip1_tenanter": 2,
                  "sveip2_nye": 0, "sveip2_oppdaterte": 5,
                  "sveip2_lukkede": 1, "funnavvik_etter_andre": 0,
                  "rent_funn_for_kontroll": 1,
                  "evidens_totalt": 16, "evidens_uten_aktor": 0,
                  "evidens_delte_input_hash": 0,
                  "evidens_per_handling": {"adresse.registrert": 6}},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_grensen_finnes_og_binder_to_punkter():
    import manifestskjema as m
    g = m.KRAVGRENSER[KRAV]
    assert g["maks_funnavvik"] == 0 and g["min_subjekter"] == 6
    assert g["maks_sveip2_nye"] == 0
    assert m.ARTEFAKTSKJEMAER[KRAV] == "artefakt-m19-fasit-skjema.json"
    # Ett artefakt, TO punkter: fasiten måler både dommen og evidensen,
    # og hvert punkt binder seg til sine egne målinger.
    assert set(g["punktbinding"]) == {"syntetisk_datasett_likt_lokalt",
                                      "revisjonslogg_korrekt"}
    for punkt, stier in g["punktbinding"].items():
        for sti in stier:
            del_, felt = sti.split(".")
            assert felt in _art()[del_], (punkt, sti)


def test_gront_artefakt_bestaar_begge_portene():
    import manifestskjema as m
    art = _art()
    assert m.valider_artefaktformat(art, KRAV) == []
    assert m._sjekk_grenser(KRAV, art) == []


def test_hver_akse_feller():
    import manifestskjema as m
    for sti, verdi in (("maalt.sveip2_nye", 1),
                       # LUKKINGEN er en egen akse: uten den kunne
                       # lukkeveien vært død uten at noen merket det.
                       ("maalt.sveip2_lukkede", 0),
                       ("maalt.rent_funn_for_kontroll", 0),
                       ("oppsett.tenanter", "to tenanter"),
                       ("maalt.funnavvik_etter_andre", 1),
                       ("maalt.evidens_uten_aktor", 1),
                       ("maalt.evidens_delte_input_hash", 1),
                       ("maalt.evidens_totalt", 5),
                       ("maalt.sveip1_nye", 5),
                       ("maalt.sveip1_tenanter", 1),
                       ("maalt.subjekter", 5),
                       ("oppsett.bevisrot_sha256", "0" * 64),
                       ("oppsett.tenanter", ["t-m19fasit-bare-en"])):
        assert m._sjekk_grenser(KRAV, _art(**{sti: verdi})), (sti, verdi)
    # DOMMEN RE-REGNES: et subjekt med feil funntype felles selv om
    # produsenten påstår null avvik.
    art = _art(); art["maalt"]["per_subjekt"][0]["fikk"] = ["avvist_adresse"]
    assert m._sjekk_grenser(KRAV, art)
    # ...og et RENT subjekt som fikk et funn.
    art = _art(); art["maalt"]["per_subjekt"][-1]["fikk"] = ["ingen_krav"]
    assert m._sjekk_grenser(KRAV, art)
    # Et sett uten den rene raden måler bare at sveipen finner NOE.
    art = _art(); art["maalt"]["per_subjekt"] = _per()[:5] + [
        {"merke": "x", "ventet": "ingen_krav", "fikk": ["ingen_krav"],
         "subjekt_id": "s-x"}]
    assert m._sjekk_grenser(KRAV, art)
    # Et sett som ikke dekker alle fem typene.
    art = _art()
    art["maalt"]["per_subjekt"] = [r for r in _per()
                                   if r["ventet"] != "kontroll_utlopt"]
    art["maalt"]["subjekter"] = 6
    assert m._sjekk_grenser(KRAV, art)
    uten = _art(); del uten["maalt"]["sveip2_lukkede"]
    assert m.valider_artefaktformat(uten, KRAV) != []


def test_det_rene_subjektet_fodes_ukontrollert():
    """Lukkingen kan bare måles hvis det RENE subjektet først får et
    funn: kontrollen registreres MELLOM kjøringene. Sto den inne fra
    starten, ville `sveip2_lukkede` alltid vært null, og aksen hadde vært
    en påstand i en dokumentstreng (CodeRabbit)."""
    import sys
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m19_fasit as f
    kilde = (ROT / "deploy/staging/m19_fasit.py").read_text(encoding="utf-8")
    assert "def kontroller_ren(" in kilde
    assert "None if ventet is None else kontroll" in kilde, \
        "det rene subjektet fødes med kontroll — da lukkes ingenting"
    driver = (ROT / "deploy/staging/m19-fasit-artefakt.py").read_text(encoding="utf-8")
    for_kontroll, etter_kontroll = driver.split("fasit.kontroller_ren(", 1)
    assert "forste = sveip(sv)" in for_kontroll, "kontrollen kom før sveip 1"
    assert "andre = sveip(sv)" in etter_kontroll, "sveip 2 kom før kontrollen"


def test_settet_dekker_registerets_funntyper_og_metoder():
    """Fasiten er bare fasit hvis den kjenner hele mengden: alle fem
    funntypene registeret kan skrive, og metoder som FINNES (en oppdiktet
    metode ville blitt avvist av døra, ikke gitt et funn)."""
    import sys
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m19_fasit as f
    sql = (ROT / "platform/core/db/migrations/112_m19_adresseregister.sql"
           ).read_text(encoding="utf-8")
    blokk = sql.split("adressefunn_type_lukket CHECK (funntype IN (", 1)[1] \
        .split("))", 1)[0]
    registerets = {t.strip().strip("',") for t in blokk.split()} - {""}
    settets = {ventet for _m, ventet, _d, _k in f.SETT if ventet}
    settets.add(f.UTEN_KRAV[1])
    assert settets == registerets, (settets, registerets)
    metoder = sql.split("adressekontroll_metode_lukket CHECK (metode IN (", 1)[1] \
        .split("))", 1)[0]
    lovlige = {t.strip().strip("',") for t in metoder.split()} - {""}
    brukt = {k[0] for _m, _v, _d, k in f.SETT if k}
    assert brukt <= lovlige, brukt - lovlige
    assert set(f.GODKJENTE_METODER) <= lovlige
    # ...og minst én BRUKT metode står UTENFOR kravet, ellers kan
    # `utilstrekkelig_metode` aldri oppstå.
    assert brukt - set(f.GODKJENTE_METODER)
