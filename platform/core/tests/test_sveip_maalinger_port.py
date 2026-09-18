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
                    "tenantkilde": ["adressesubjekt"],
                    "maalerolle": "disponit_adresse_eier",
                    "riggmodul": "m19_fasit", "runde": "abcd1234",
                    "riggtenanter": ["t-m19fasit-med_krav-abcd1234",
                                     "t-m19fasit-uten_krav-abcd1234"],
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
                  "registeret_urort": True, "tenanter": 3,
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
            # ET TOMT REGISTER står stille uansett hva kjøringen gjorde:
            # null åpne før og null etter er sant av feil grunn.
            ("maalt.apne_for", 0),

            ("maalt.apne_etter", 5),
            ("maalt.lukkede_etter", 2),
            ("oppsett.modul", "m44_purring"),
            ("oppsett.bevisrot_sha256", "0" * 64)):
        assert m._sjekk_grenser(FEIL_KRAV, _feilart(**{sti: verdi})), (sti, verdi)
    tomt = _feilart(**{"maalt.apne_for": 0, "maalt.apne_etter": 0,
                       "maalt.per_type_for": {}, "maalt.per_type_etter": {}})
    assert m._sjekk_grenser(FEIL_KRAV, tomt) == [
        "apne_for=0 — et tomt register står stille uansett hva kjøringen"
        " gjorde; målingen krever minst 1 åpent funn"]
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


ROLLBACK_KRAV = "m19-rollback-v1"


def _rbart(**over):
    import manifestskjema as m
    art = {
        "krav_id": ROLLBACK_KRAV, "ts": "2026-09-18T02:00:00+00:00",
        "bestatt": True,
        "oppsett": {
            "modul": MODUL, "miljo": "staging", "vert": "disponit.com",
            "runde": "abcd1234",
            "tenanter": ["t-m19fasit-med_krav-abcd1234",
                         "t-m19fasit-uten_krav-abcd1234"],
            "funntabell": "adressefunn", "maalerolle": "disponit_adresse_eier",
            "drillet_release": "aa11", "forgjenger_release": "bb22",
            "drillet_katalog": "/opt/disponit/releases/aa11",
            "forgjenger_katalog": "/opt/disponit/releases/bb22",
            "drillet_digest": "a" * 64, "forgjenger_digest": "a" * 64,
            "drillet_kjernedigest": "c" * 64,
            "forgjenger_kjernedigest": "d" * 64,
            "arbeidernokkel": 619204773, "avbrutt_backend_pid": 4242,
            "bevisrot_sha256": m.sveip_rollback_bevisrot_sha256(MODUL),
            "form": "kjerne"},
        "identiteter": {
            "avbrutt_pid": 11, "rullback_pid": 12, "kandidat_pid": 13,
            "avbrutt_fil": "/opt/disponit/releases/aa11/platform/drift/adressesveip.py",
            "rullback_fil": "/opt/disponit/releases/bb22/platform/drift/adressesveip.py",
            "kandidat_fil": "/opt/disponit/releases/aa11/platform/drift/adressesveip.py"},
        "maalt": {
            "inflight_drept": True, "inflight_returkode": -9,
            "inflight_blokkerte_paa_laas": True,
            "for_rader": 1, "for_apne": 1, "inflight_funn": 0,
            "inflight_backend_levde_etter_drap": True,
            "inflight_backend_avsluttet": True,
            "arbeidernokkel_fri": True,
            "rullbakk_funn": 6, "rullbakk_rader": 6,
            "dubletter": 0, "kandidat_nye": 0, "kandidat_apne": 6,
            "rullback_bytes_er_forgjengerens": True,
            "kandidat_bytes_er_drillede": True,
            "release_digest_bundet": True, "modul_digest_likt": True},
        "etterkontroll": {"aktiv_urort": True, "kandidat_feilet": False},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_rollbackporten_star_for_drillen():
    import manifestskjema as m
    g = m.KRAVGRENSER[ROLLBACK_KRAV]
    assert g["maks_dubletter"] == 0 and g["maks_kandidat_nye"] == 0
    assert m.ARTEFAKTSKJEMAER[ROLLBACK_KRAV] == "artefakt-sveip-rollback-skjema.json"
    assert set(g["punktbinding"]) == {"rollback_testet"}
    art = _rbart()
    assert m.valider_artefaktformat(art, ROLLBACK_KRAV) == []
    assert m._sjekk_grenser(ROLLBACK_KRAV, art) == []


def test_rollbackens_akser_feller():
    import manifestskjema as m
    for sti, verdi in (
            # DEN DREPTE KJØRINGEN må faktisk ha nådd skrivingen, ellers
            # måler drillen en prosess som aldri rakk noe.
            ("maalt.inflight_drept", False),
            ("maalt.inflight_blokkerte_paa_laas", False),
            # Å DREPE KLIENTEN STOPPER IKKE ARBEIDET: backenden venter
            # videre på låsen og committer så snart den slipper.
            ("maalt.inflight_backend_avsluttet", False),
            # …og den må ikke ha skrevet et halvt funn.
            ("maalt.inflight_funn", 1),
            # ARBEIDERNØKKELEN må slippe, ellers er sveipen stengt ute av
            # sin egen døde sesjon.
            ("maalt.arbeidernokkel_fri", False),
            ("maalt.rullbakk_funn", 5),
            ("maalt.dubletter", 1),
            ("maalt.kandidat_nye", 1),
            ("maalt.rullback_bytes_er_forgjengerens", False),
            ("maalt.kandidat_bytes_er_drillede", False),
            ("maalt.release_digest_bundet", False),
            ("oppsett.modul", "m44_purring"),
            ("oppsett.bevisrot_sha256", "0" * 64),
            # TO IDENTISKE KJERNER ruller ingenting…
            ("oppsett.forgjenger_kjernedigest", "c" * 64),
            # …og det gjør heller ikke én og samme release.
            ("oppsett.forgjenger_release", "aa11")):
        assert m._sjekk_grenser(ROLLBACK_KRAV, _rbart(**{sti: verdi})), (sti, verdi)
    # MODULENS EGNE FILER er som regel uendret over en rulling — det er
    # nettopp derfor kjerneformen finnes, og skal IKKE felle drillen.
    assert m._sjekk_grenser(ROLLBACK_KRAV, _rbart(
        **{"maalt.modul_digest_likt": True})) == []
    uten = _rbart(); del uten["maalt"]["arbeidernokkel_fri"]
    assert m.valider_artefaktformat(uten, ROLLBACK_KRAV) != []


def test_drillen_avbryter_utenfra_og_bevitner_bytene():
    """Avbruddet skal komme fra transporten, ikke fra en bryter i
    modulen, og hver kjøring skal si hvilken FIL sveipen ble lastet fra.
    En drill som bare stoler på PYTHONPATH måler oppsettet sitt."""
    from pathlib import Path
    kilde = (Path(__file__).resolve().parents[3]
             / "deploy/staging/rollback-sveipkjerne.py").read_text(encoding="utf-8")
    assert "ACCESS EXCLUSIVE MODE" in kilde and "SIGKILL" in kilde
    assert "modul.__file__" in kilde
    assert 'klar["fil"].startswith' in kilde, \
        "drillen sjekker ikke at bytene kom fra katalogen den drillet"
    # …og den nekter å drepe en prosess som aldri blokkerte.
    assert "blokkerte aldri på tabellåsen" in kilde


def test_riggkontrakten_er_oppfylt_av_modulens_fasitdriver():
    """Den generiske drillen krever `forbered` og `riggtenanter` av
    riggmodulen. Mangler én av dem, faller drillen først på verten."""
    import sys
    from pathlib import Path
    rot = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(rot / "deploy/staging"))
    import manifestskjema as m
    for modul_id, k in m.SVEIPMODULER.items():
        rigg = __import__(k["riggmodul"])
        assert callable(getattr(rigg, "forbered", None)), modul_id
        assert callable(getattr(rigg, "riggtenanter", None)), modul_id
        for rel in k["releasefiler"]:
            assert (rot / rel).exists(), (modul_id, rel)


def test_tenantkilden_kan_vaere_flere_tabeller():
    """Noen sveip henter tenantlisten fra en UNION. M-13 er den første:
    en tenant kan finnes bare i `bilag`, uten en eneste bankpost. En
    måling som bare kjente den ene tabellen ville talt null funn i den
    tenanten — og null lik null ser ut som et urørt register.

    Produsenten må derfor bygge en union, og registeret må kunne bære
    flere tabeller."""
    import manifestskjema as m
    from pathlib import Path
    flere = {mid: k["tenantkilde"] for mid, k in m.SVEIPMODULER.items()
             if not isinstance(k["tenantkilde"], str)}
    assert flere, "ingen modul har flere tenantkilder — testen måler ingenting"
    kilde = (Path(__file__).resolve().parents[3]
             / "deploy/staging/sveip-feilinjisering.py").read_text(encoding="utf-8")
    assert "UNION" in kilde and "isinstance(kilde, str)" in kilde
