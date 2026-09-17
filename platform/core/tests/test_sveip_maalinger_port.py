"""`<modul>-feilinjisering-v1` og `<modul>-ytelse-v1` — de to generiske
sveipmålingene, med porten FØR kjøringen (§0).

Begge kravene har én produsent for ALLE sveipmoduler, og grensene
registreres av `registrer_sveipgrenser`. Testen står derfor på formen,
ikke på M-19: en ny sveipmodul arver både porten og mutasjonene.

FEILINJISERING. Feilen kommer utenfra — kjøringen får en tilkobling uten
EXECUTE på sveipedøra. Mutasjonene som feller: en injeksjonsrolle som
likevel HADDE EXECUTE (da måler «feilet» noe annet enn vi tror), en frisk
kjøring som feilet (riggen virket ikke), bare én feilet av to, alarm
allerede på den første, ingen alarm på den andre, og et register som
flyttet seg — i sum, i lukkede, eller bare i fordelingen mellom
funntypene.

YTELSE. Veggklokke rundt modulens egen `kjor()`, tre kjøringer, og den
DÅRLIGSTE er dommen. Mutasjonene som feller: for få kjøringer, en
rapportert varighet som ikke er den dårligste, et tak som sprekker, en
kjøring over for få tenanter, et regnestykke som ikke stemmer, og en
kjøring som feilet.
"""
from __future__ import annotations

MODUL = "m19_adresse"
FEIL_KRAV = "m19-feilinjisering-v1"
YTELSE_KRAV = "m19-ytelse-v1"
TYPER = {"ukontrollert_adresse": [3, 1], "avvist_adresse": [1, 0]}


def _feilart(**over):
    import manifestskjema as m
    art = {
        "krav_id": FEIL_KRAV, "ts": "2026-09-18T01:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": MODUL, "vert": "disponit.com",
                    "funntabell": "adressefunn",
                    "sveipedor": "m19_sveip_adresser(int)",
                    "injeksjonsrolle": "disponit",
                    "injeksjon": "tilkobling som disponit — uten EXECUTE",
                    "bevisrot_sha256": m.sveip_feilinjisering_bevisrot_sha256()},
        "maalt": {"frisk_feilet": False,
                  "injeksjonsrolle_har_execute": False,
                  "injiserte_kjoringer": 2, "injisert_feilet": 2,
                  "alarm_etter_forste": False, "alarm_etter_andre": True,
                  "apne_for": 4, "apne_etter": 4,
                  "lukkede_for": 1, "lukkede_etter": 1,
                  "registeret_urort": True,
                  "per_type_for": dict(TYPER), "per_type_etter": dict(TYPER)},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def _ytart(**over):
    import manifestskjema as m
    art = {
        "krav_id": YTELSE_KRAV, "ts": "2026-09-18T01:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": MODUL, "vert": "disponit.com",
                    "sveip": "adressesveip",
                    "bevisrot_sha256": m.sveip_ytelse_bevisrot_sha256()},
        "maalt": {"sekunder": 0.9, "kjoringer": [0.4, 0.9, 0.5],
                  "tenanter": 9, "sekunder_per_tenant": 0.1,
                  "feilet": False,
                  "per_kjoring": [{"tenanter": 9, "nye": 2, "oppdaterte": 0,
                                   "lukkede": 0}]},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_sveipmodulen_er_registrert_med_begge_kravene():
    import manifestskjema as m
    k = m.SVEIPMODULER[MODUL]
    assert k["feilinjisering_krav"] == FEIL_KRAV
    assert k["ytelse_krav"] == YTELSE_KRAV
    # DSN-en feilen injiseres over er en ANNEN enn sveipens egen: samme
    # streng ville gitt en kjøring som lykkes, og målt ingenting.
    assert k["dsn_uten_execute"] != k["dsn_variabel"]
    assert m.ARTEFAKTSKJEMAER[FEIL_KRAV] == \
        "artefakt-sveip-feilinjisering-skjema.json"
    assert m.ARTEFAKTSKJEMAER[YTELSE_KRAV] == "artefakt-sveip-ytelse-skjema.json"
    for krav, art in ((FEIL_KRAV, _feilart()), (YTELSE_KRAV, _ytart())):
        for punkt, stier in m.KRAVGRENSER[krav]["punktbinding"].items():
            for sti in stier:
                del_, felt = sti.split(".")
                assert felt in art[del_], (punkt, sti)


def test_gronne_artefakter_bestaar_portene():
    import manifestskjema as m
    for krav, art in ((FEIL_KRAV, _feilart()), (YTELSE_KRAV, _ytart())):
        assert m.valider_artefaktformat(art, krav) == [], krav
        assert m._sjekk_grenser(krav, art) == [], krav


def test_feilinjiseringens_akser_feller():
    import manifestskjema as m
    for sti, verdi in (
            # DEN POSITIVE KONTROLLEN: hadde rollen EXECUTE, er «feilet»
            # en annen feil enn den vi mener å ha injisert.
            ("maalt.injeksjonsrolle_har_execute", True),
            ("maalt.frisk_feilet", True),
            ("maalt.injisert_feilet", 1),
            ("maalt.alarm_etter_forste", True),
            ("maalt.alarm_etter_andre", False),
            ("maalt.registeret_urort", False),
            ("maalt.apne_etter", 5),
            ("maalt.lukkede_etter", 2),
            ("oppsett.modul", "m44_purring"),
            ("oppsett.bevisrot_sha256", "0" * 64)):
        assert m._sjekk_grenser(FEIL_KRAV, _feilart(**{sti: verdi})), (sti, verdi)
    # SUMMEN KAN STEMME mens ett funn ble lukket og et annet åpnet. Bare
    # fordelingen per funntype fanger det.
    art = _feilart(**{"maalt.per_type_etter":
                      {"ukontrollert_adresse": [2, 1], "avvist_adresse": [2, 0]}})
    assert m._sjekk_grenser(FEIL_KRAV, art)
    uten = _feilart(); del uten["maalt"]["injeksjonsrolle_har_execute"]
    assert m.valider_artefaktformat(uten, FEIL_KRAV) != []


def test_ytelsens_akser_feller():
    import manifestskjema as m
    g = m.KRAVGRENSER[YTELSE_KRAV]
    for sti, verdi in (
            # DEN DÅRLIGSTE ER DOMMEN: rapporterer produsenten den beste
            # kjøringen, måler tallet flaks.
            ("maalt.sekunder", 0.4),
            ("maalt.kjoringer", [0.9]),
            ("maalt.tenanter", 1),
            ("maalt.sekunder_per_tenant", 0.5),
            ("maalt.feilet", True),
            ("oppsett.modul", "m44_purring"),
            ("oppsett.bevisrot_sha256", "0" * 64)):
        assert m._sjekk_grenser(YTELSE_KRAV, _ytart(**{sti: verdi})), (sti, verdi)
    # TAKET: en kjøring over grensen felles, og den er dommens dårligste.
    over = g["maks_sekunder"] + 1
    art = _ytart(**{"maalt.sekunder": over,
                    "maalt.kjoringer": [0.4, over, 0.5],
                    "maalt.sekunder_per_tenant": round(over / 9, 4)})
    assert m._sjekk_grenser(YTELSE_KRAV, art)
    uten = _ytart(); del uten["maalt"]["kjoringer"]
    assert m.valider_artefaktformat(uten, YTELSE_KRAV) != []


def test_produsentene_kaller_modulens_egen_kjor():
    """Begge målingene går gjennom `modul.kjor()`, ikke rett på
    SQL-funksjonen. En måling som kaller døra direkte hopper over låsen,
    kontraktvalideringen og alarmen — altså nettopp det den måler."""
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3] / "deploy/staging"
    for navn in ("sveip-feilinjisering.py", "sveip-ytelse.py"):
        kilde = (rot / navn).read_text(encoding="utf-8")
        assert "modul.kjor(" in kilde, navn
        assert "m19_sveip_adresser" not in kilde, \
            f"{navn} kaller sveipedøra direkte og hopper over modulen"
        # HOPPET OVER er ikke en kjøring: en måling mot en sveip som
        # aldri kjørte måler ingenting, og skal avbryte.
        assert "hoppet_over" in kilde, navn
