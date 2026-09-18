"""`<modul>-fasit-v1` — den GENERISKE fasitporten, som står FØR kjøringen.

Én produsent og én port for alle sveipmoduler: modulens eget sett kjøres
gjennom modulens egne dører, sveipen kalles gjennom `modul.kjor()`, og
dommen RE-REGNES av per-subjekt-tabellen artefaktet bærer. Et
`funnavvik: 0` uten radene bak seg er produsentens påstand, ikke en
måling.

Mutasjonene som feller: et subjekt som fikk feil funntype, et rent
subjekt som fikk et funn, en andre kjøring som fant noe nytt, en andre
kjøring som ikke lukket noe, et sett uten en ren rad, evidens uten
aktør, to hendelser som deler `input_hash`, én tenant i stedet for to,
og et artefakt fra en annen produsentflate eller et annet sett.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROT = Path(__file__).resolve().parents[3]
MODUL = "m27_lager"
KRAV = "m27-fasit-v1"
TYPER = ("under_bestillingspunkt", "uten_bevegelse", "ikke_talt",
         "ingen_terskel")


def _per():
    rader = [{"merke": t, "ventet": t, "fikk": [t], "subjekt_id": f"v-{i}"}
             for i, t in enumerate(TYPER)]
    rader.append({"merke": "uten_punkt_ny", "ventet": None, "fikk": [],
                  "subjekt_id": "v-ny"})
    rader.append({"merke": "ren", "ventet": None, "fikk": [],
                  "subjekt_id": "v-ren"})
    return rader


def _art(**over):
    import manifestskjema as m
    art = {
        "krav_id": KRAV, "ts": "2026-09-18T03:00:00+00:00", "bestatt": True,
        "oppsett": {"modul": MODUL, "vert": "disponit.com",
                    "runde": "c0ffee11",
                    "tenanter": ["t-m27fasit-med_terskel-c0ffee11",
                                 "t-m27fasit-uten_terskel-c0ffee11"],
                    "riggmodul": "m27_fasit",
                    "sett_sha256": "a" * 64,
                    "bevisrot_sha256": m.sveip_fasit_bevisrot_sha256(MODUL),
                    "evidenskilde": "m27_lager"},
        "maalt": {"subjekter": 6, "funnavvik": 0, "avviksliste": [],
                  "per_subjekt": _per(),
                  "sveip1_nye": 5, "sveip1_tenanter": 2,
                  "sveip2_nye": 0, "sveip2_oppdaterte": 4,
                  "sveip2_lukkede": 1, "funnavvik_etter_andre": 0,
                  "rent_funn_for_kontroll": 1,
                  "evidens_totalt": 20, "evidens_uten_aktor": 0,
                  "evidens_delte_input_hash": 0,
                  "evidens_per_handling": {"vare.registrert": 6}},
    }
    for sti, verdi in over.items():
        del_, felt = sti.split(".")
        art[del_][felt] = verdi
    return art


def test_grensen_finnes_og_binder_to_punkter():
    import manifestskjema as m
    g = m.KRAVGRENSER[KRAV]
    assert g["maks_funnavvik"] == 0 and g["maks_sveip2_nye"] == 0
    assert m.ARTEFAKTSKJEMAER[KRAV] == "artefakt-sveip-fasit-skjema.json"
    # ETT ARTEFAKT, TO PUNKTER: fasiten måler både dommen og evidensen.
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
                       ("maalt.sveip2_lukkede", 0),
                       ("maalt.rent_funn_for_kontroll", 0),
                       ("maalt.funnavvik_etter_andre", 1),
                       ("maalt.evidens_uten_aktor", 1),
                       ("maalt.evidens_delte_input_hash", 1),
                       ("maalt.evidens_totalt", 5),
                       ("maalt.sveip1_nye", 4),
                       ("maalt.sveip1_tenanter", 1),
                       ("maalt.subjekter", 5),
                       ("oppsett.modul", "m19_adresse"),
                       ("oppsett.bevisrot_sha256", "0" * 64),
                       ("oppsett.tenanter", ["t-m27fasit-bare-en"]),
                       ("oppsett.tenanter", ["t-annet-a", "t-annet-b"])):
        assert m._sjekk_grenser(KRAV, _art(**{sti: verdi})), (sti, verdi)
    # DOMMEN RE-REGNES: feil funntype felles selv om produsenten påstår
    # null avvik.
    art = _art(); art["maalt"]["per_subjekt"][0]["fikk"] = ["ikke_talt"]
    assert m._sjekk_grenser(KRAV, art)
    # …og et RENT subjekt som fikk et funn.
    art = _art(); art["maalt"]["per_subjekt"][-1]["fikk"] = ["uten_bevegelse"]
    assert m._sjekk_grenser(KRAV, art)
    # Et sett uten en eneste ren rad måler bare at sveipen finner NOE.
    art = _art()
    art["maalt"]["per_subjekt"] = [r for r in _per() if r["ventet"]]
    assert m._sjekk_grenser(KRAV, art)
    uten = _art(); del uten["maalt"]["sveip2_lukkede"]
    assert m.valider_artefaktformat(uten, KRAV) != []


def test_riggkontrakten_er_hel_for_hver_sveipmodul():
    """Den generiske produsenten kaller seks ting på riggmodulen. Mangler
    én, faller kjøringen først på verten — porten skal se det her."""
    import manifestskjema as m
    sys.path.insert(0, str(ROT / "deploy/staging"))
    for modul_id, k in m.SVEIPMODULER.items():
        if "fasit_krav" not in k:
            continue
        rigg = __import__(k["riggmodul"])
        for navn in ("forbered", "riggtenanter", "kontroller_ren", "maal",
                     "sett_sha256"):
            assert callable(getattr(rigg, navn, None)), (modul_id, navn)
        for navn in ("AKTOR", "EVIDENSKILDE", "RENSES"):
            assert getattr(rigg, navn, None), (modul_id, navn)
        # SUBJEKTET SOM RENSES må finnes i settet og være ventet RENT —
        # ellers måler lukkeaksen ingenting, og produsenten leser null.
        assert rigg.forventet().get(rigg.RENSES, "x") is None, \
            (modul_id, rigg.RENSES)


def _funntyper_fra_migrasjonen(funntabell: str) -> set[str]:
    """Registerets LUKKEDE mengde funntyper, lest av CHECK-beskrankningen
    i funntabellens egen `CREATE TABLE`.

    Utledet, ikke pinnet: en liste her ville vært en KOPI av det den skal
    kontrollere, og ville stått stille den dagen registeret fikk en
    sjette funntype.

    LESER BEGGE SKRIVEMÅTENE. Noen registre navngir beskrankningen
    (`<tabell>_type_lukket`), andre skriver den rett på kolonnen. En
    port som bare kjente den ene ville sagt «fant ingen CHECK» om et
    register som har en helt gyldig — og da måler porten formen på
    SQL-en, ikke mengden."""
    for sti in sorted((ROT / "platform/core/db/migrations").glob("*.sql")):
        sql = sti.read_text(encoding="utf-8")
        for start in (f"CREATE TABLE {funntabell} (",
                      f"CREATE TABLE public.{funntabell} ("):
            if start not in sql:
                continue
            blokk = sql.split(start, 1)[1].split("\n);", 1)[0]
            if "funntype IN (" not in blokk:
                continue
            liste = blokk.split("funntype IN (", 1)[1]
            # Balanser parentesene: lista slutter der den åpnende lukkes.
            dybde, ut = 1, []
            for tegn in liste:
                if tegn == "(":
                    dybde += 1
                elif tegn == ")":
                    dybde -= 1
                    if dybde == 0:
                        break
                ut.append(tegn)
            return {x.strip().strip("',") for x in "".join(ut).split()} - {""}
    raise AssertionError(f"fant ingen CHECK for {funntabell}")


def test_settet_dekker_registerets_funntyper_eller_sier_hvorfor_ikke():
    """Settet må dekke funntypene registeret kan skrive. En type som IKKE
    er nåbar skal stå i `UNAABARE` med en grunn som handler om REGELEN —
    et unntak som bare fantes i en dokumentstreng ville vært et hull
    ingen kunne se.

    Porten går over ALLE moduler på den generiske fasiten: en ny modul
    arver kontrollen uten at noen husker å legge den til."""
    import manifestskjema as m
    sys.path.insert(0, str(ROT / "deploy/staging"))
    sett_noen = False
    for modul_id, k in m.SVEIPMODULER.items():
        if "fasit_krav" not in k:
            continue
        sett_noen = True
        rigg = __import__(k["riggmodul"])
        registerets = _funntyper_fra_migrasjonen(k["funntabell"])
        settets = {v for v in rigg.forventet().values() if v}
        unaabare = dict(getattr(rigg, "UNAABARE", {}))
        assert settets | set(unaabare) == registerets, \
            (modul_id, settets, set(unaabare), registerets)
        assert not (settets & set(unaabare)), \
            f"{modul_id}: en funntype står både som dekket og som unåbar"
        for typen, grunn in unaabare.items():
            assert len(grunn) > 80, \
                f"{modul_id}/{typen}: unåbar uten en ekte grunn"
        # …OG SETTET MÅ HA ET RENT SUBJEKT, ellers måler fasiten bare at
        # sveipen finner NOE.
        assert any(v is None for v in rigg.forventet().values()), modul_id
        # EN NEGATIV KONTROLL er valgfri — ikke alle registre har et
        # subjekt som er rent fra fødselen — men finnes den, skal den
        # være ren og en ANNEN enn den som renses underveis.
        negativ = getattr(rigg, "NEGATIV_KONTROLL", None)
        if negativ:
            assert rigg.forventet().get(negativ, "x") is None, modul_id
            assert negativ != rigg.RENSES, \
                f"{modul_id}: den negative kontrollen er den som renses"
    assert sett_noen, "ingen moduler på den generiske fasiten"


def test_den_rene_varen_fodes_med_et_funn():
    """Lukkingen kan bare måles hvis det rene subjektet først FÅR et funn:
    rettelsen kommer mellom kjøringene. Sto varen ren fra starten, ville
    `sveip2_lukkede` alltid vært null."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m27_fasit as f
    ren = next(r for r in f.SETT if r[0] == "ren")
    _merke, ventet, _punkt, bevegelser, _telling = ren
    assert ventet is None
    # Siste bevegelse er ELDRE enn stille_dogn → første sveip gir funnet.
    assert max(siden for _t, _a, siden in bevegelser) > f.STILLE_DOGN
    # …og rettelsen er fersk.
    assert f.REN_BEVEGELSE[2] == 0
    # EN TELLING ER OGSÅ EN BEVEGELSE. En fersk telling på den rene varen
    # ville gjort den «i bevegelse», funnet hadde aldri oppstått, og
    # lukkeaksen hadde vært umålt. Riggen falt i nettopp den fellen på
    # første kjøring mot verten.
    assert ren[4] is None, \
        "den rene varen har en telling — da er den ikke stille"


def test_m25s_rene_prosjekt_fodes_med_et_funn_og_bare_ett():
    """Det rene prosjektet skal få NØYAKTIG ett funn i første sveip —
    `ingen_arbeid_registrert` — og ingen av de tre andre. Feilet en av
    avgrensningene, ville dommen blitt riktig av feil grunn, eller
    lukkeaksen umålt."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m25_fasit as f
    rent = next(r for r in f.SETT if r[0] == f.RENSES)
    _merke, ventet, budsjett, start_siden, milepaeler, arbeid = rent
    assert ventet is None
    # STILLE: ingen arbeid, og start eldre enn tenantens frist.
    assert arbeid == [] and start_siden > f.STILLHET_DOGN
    # …MEN IKKE UTEN PLAN: en milepæl finnes, ellers hadde
    # `betalingsplan_mangler` også slått inn.
    assert milepaeler, "et prosjekt uten milepæler har to funn, ikke ett"
    # …OG PLANEN ER I RUTE: milepælen ligger fram i tid, godt forbi
    # fristen, så `milepael_over_frist` ikke også slår inn.
    assert min(dogn for _n, dogn, _b in milepaeler) > f.MILEPAEL_FRIST_DOGN
    # RETTELSEN er fersk og langt under budsjett, så andre sveip lukker
    # funnet uten å åpne et nytt.
    siden, _min, kostnad = f.RENT_ARBEID
    assert siden == 0
    assert kostnad * 1000 <= budsjett * (1000 + f.BUDSJETTVARSEL_PROMILLE)


def test_m24s_rene_avtale_fodes_med_et_funn_og_bare_ett():
    """Den rene avtalen skal få NØYAKTIG ett funn i første sveip —
    `avtale_uten_maling` — og ingen av de tre andre."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m24_fasit as f
    ren = next(r for r in f.SETT if r[0] == f.RENSES)
    _merke, ventet, fra_siden, til_om, leveranser = ren
    assert ventet is None
    # STILLE: ingen leveranser, og avtalen har løpt forbi grensen.
    assert leveranser == [] and fra_siden > f.MALING_STILLHET_DOGN
    # …MEN IKKE NÆR UTLØP: gyldigheten ligger godt utenfor varselvinduet,
    # ellers hadde `avtale_utlopt` også slått inn.
    assert til_om > f.AVTALE_VARSEL_DOGN
    # RETTELSEN er innenfor både SLA og pris, så andre sveip lukker
    # funnet uten å åpne et nytt.
    siden, verdi, pris = f.REN_LEVERANSE
    assert siden == 0 and verdi <= f.AVTALT_VERDI
    assert pris * 1000 <= f.AVTALT_PRIS * (1000 + f.PRISSTIGNING_PROMILLE)


def test_m42s_rene_mottaker_kureres_av_sveipen_ikke_av_dora():
    """M-42s verifikasjonsdør LUKKER funnene selv. Hadde kuren vært en
    verifikasjon, ville sveipen ikke hatt noe å lukke, og lukkeaksen vært
    umålt selv om alt så grønt ut.

    Kuren er derfor en ny oppgave med SAMME nummer: døra skriver
    ingenting når nummeret står stille, og kandidaten forsvinner bare
    fordi datoen flyttet seg."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m42_fasit as f
    ren = next(r for r in f.SETT if r[0] == f.RENSES)
    _merke, ventet, oppgaver, verifikasjon = ren
    assert ventet is None
    # Fødes UVERIFISERT og eldre enn grensen → funn i første sveip.
    assert verifikasjon is None
    assert len(oppgaver) == 1 and oppgaver[0][1] > f.UVERIFISERT_DOGN
    # KUREN er en oppgave, ikke en verifikasjon…
    kilde = (ROT / "deploy/staging/m42_fasit.py").read_text(encoding="utf-8")
    kur = kilde.split("def kontroller_ren(", 1)[1].split("def ", 1)[0]
    assert "m42_oppgi_konto" in kur
    assert "m42_verifiser_konto" not in kur, \
        "kuren verifiserer — da lukker DØRA funnet, ikke sveipen"
    # …med SAMME nummer, så døra ikke skriver et `kontoendring`-funn.
    assert f.REN_OPPGAVE[0] == oppgaver[0][0]
    assert f.REN_OPPGAVE[1] == 0


def test_m42s_terskellose_tenant_har_bare_en_oppgave():
    """Døra skriver `kontoendring` SELV ved den andre oppgaven, uansett
    om tenanten har terskler. To oppgaver i den terskelløse tenanten
    ville gitt den et funn døra skrev — og settet hadde målt døra."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m42_fasit as f
    _merke, ventet, oppgaver, _v = f.UTEN_TERSKEL
    assert ventet == "ingen_terskel"
    assert len(oppgaver) == 1


def test_m42s_rigg_respekterer_fireoyne():
    """`m42_verifikasjon_vakt` NEKTER at den som oppga kontoen verifiserer
    den. Riggen må ha to personer — og det er ikke en formalitet: hele
    modulens grunn til å finnes er at noen SÅ på kontoen, uavhengig av
    den som oppga den. Riggen falt på nettopp dette første gang."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m42_fasit as f
    assert f.OPPGIR != f.VERIFISERER
    kilde = (ROT / "deploy/staging/m42_fasit.py").read_text(encoding="utf-8")
    assert '"u-fasit"' not in kilde, \
        "én og samme aktør oppgir og verifiserer — vakten nekter"


def test_m41s_tersklene_gir_et_vindu_for_utlopt_autorisasjon():
    """`uavklart_betaling` og `autorisasjon_utlopt` leser BEGGE den siste
    hendelsen, og begge treffer en autorisert betaling som har stått
    lenge. Skal den ene kunne måles alene, må reautorisasjonsfristen være
    STRENGT KORTERE enn uavklart-fristen — ellers finnes det ikke noe
    aldersvindu der en autorisasjon er utløpt uten også å være uavklart.

    Standardverdiene er motsatt (7 mot 3), så dette er et valg riggen
    må ta, ikke noe den arver."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m41_fasit as f
    assert f.REAUTORISASJON_DOGN < f.UAVKLART_DOGN
    utlopt = next(r for r in f.SETT if r[1] == "autorisasjon_utlopt")
    _m, _v, hendelser = utlopt
    status, _belop, forventet, siden = hendelser[-1]
    assert status == "autorisert"
    # I VINDUET: forbi reautorisasjonsfristen, men innenfor uavklart.
    assert f.REAUTORISASJON_DOGN < siden <= f.UAVKLART_DOGN
    # …og uten forventet beløp, ellers slår `belopsavvik` også inn.
    assert forventet is None


def test_m41s_rene_subjekt_kureres_av_en_ny_hendelse():
    """`betalingshendelse` er totalt frosset — en betaling kan ikke
    skrives om i ettertid. Kuren må derfor være en NY hendelse som tar
    subjektet ut av predikatet."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m41_fasit as f
    ren = next(r for r in f.SETT if r[0] == f.RENSES)
    assert ren[1] is None
    status, _b, forventet, siden = ren[2][-1]
    assert status == "opprettet" and siden > f.UAVKLART_DOGN
    kur_status, _kb, kur_forventet, kur_siden = f.REN_HENDELSE
    assert kur_status == "gjennomfort" and kur_siden == 0
    # INGEN FORVENTET SUM å avvike fra, ellers bytter funnet bare type.
    assert kur_forventet is None


def test_m39s_takere_har_nøyaktig_en_dag_hver():
    """Funnene aggregeres PER LØNNSTAKER, ikke per dag. To dager på samme
    taker ville blandet to dommer i én rad, og settet kunne ikke lest
    dommen per subjekt."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m39_fasit as f
    for merke, _v, _plan, dag in f.SETT:
        assert isinstance(dag, tuple) and len(dag) == 3, merke
    merker = [r[0] for r in f.SETT] + [f.UTEN_TERSKEL[0]]
    assert len(merker) == len(set(merker))


def test_m39s_planlose_dag_ligger_under_normaltiden():
    """`overtid` krever IKKE plan. En planløs dag over normaltiden ville
    derfor gitt BÅDE `overtid` og `time_uten_arbeidsplan`, og dommen
    hadde vært riktig av feil grunn."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m39_fasit as f
    for merke, ventet, plan, (siden, minutter, _kode) in f.SETT:
        if plan is None:
            assert minutter <= f.NORMALTID_DAG, merke
            assert siden > f.UTEN_PLAN_DOGN, merke
    # OVERTIDSDAGEN er ført NØYAKTIG som planlagt, så avviket er null.
    over = next(r for r in f.SETT if r[1] == "overtid")
    planlagt = over[2][0]
    assert over[3][1] == planlagt > f.NORMALTID_DAG


def test_m39s_rene_taker_far_planen_sin_mellom_kjoringene():
    """Den rene takeren fødes uten plan og får funnet i første sveip.
    Planen som kommer mellom kjøringene må dekke dagen, være ført
    nøyaktig som planlagt og på samme kode — ellers tar en annen
    funntype plassen til den som skulle lukkes."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m39_fasit as f
    ren = next(r for r in f.SETT if r[0] == f.RENSES)
    _m, ventet, plan, (siden, minutter, kode) = ren
    assert ventet is None and plan is None
    p_minutter, p_kode, p_fra = f.REN_PLAN
    assert p_minutter == minutter and p_kode == kode
    assert p_fra > siden, "planen dekker ikke dagen den skal forklare"
    assert p_minutter <= f.NORMALTID_DAG


def test_m18s_hvert_lop_har_noyaktig_ett_forsinket_steg():
    """`steg_over_frist` gir ÉN RAD PER FORSINKET STEG, og alle tre
    funntypene henger på samme `lop_id`. Et løp med to forsinkede steg
    ville fått to rader av samme type, og dommen «nøyaktig én funntype»
    hadde falt på riggen, ikke på modulen.

    Malen med lange frister kan derfor ALDRI gi et forsinket steg, og
    fristmalen må gi nøyaktig ett når steg 1 er fullført."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m18_fasit as f
    # LANGE FRISTER: ingen av dem kan forfalle i settets levetid.
    assert all(s["frist_dogn"] >= 365 for s in f.MAL_LANG)
    for merke, _v, _t, mal, startet, _fu, _e, _a in f.SETT:
        if mal == "lang":
            assert startet < 365, merke
    # FRISTMALEN: med steg 1 fullført og løpet fem døgn gammelt skal
    # NØYAKTIG ETT ufullført steg være forbi fristen.
    frist = next(r for r in f.SETT if r[3] == "frist")
    _m, ventet, _t, _mal, startet, fullfor, _e, _a = frist
    assert ventet == "steg_over_frist" and fullfor == [1]
    over = [i for i, s in enumerate(f.MAL_FRIST, start=1)
            if i not in fullfor and startet > s["frist_dogn"]]
    assert over == [2], f"forventet nøyaktig ett forsinket steg, fikk {over}"
    # …og løpet er ikke stille, fordi steg 1 fullføres i dag.
    assert fullfor, "et løp uten fullført steg måles fra start og blir stille"


def test_m18s_stoppede_lop_har_ingen_fullforte_steg():
    """Stillheten måles fra SISTE fullføring når det finnes en, og
    `fullfort_ts` settes til `now()` av døra — ingen dør tar den som
    parameter. Et stoppet løp med et fullført steg er derfor unåbart for
    et løp opprettet i dag, og settet må gi det NULL fullførte."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m18_fasit as f
    for merke, ventet, _t, _mal, startet, fullfor, _e, _a in f.SETT:
        if ventet == "stoppet_lop":
            assert fullfor == [], merke
            assert startet > f.DOGN_STILLE, merke


def test_m18s_eierlose_lop_har_sin_EGEN_eier():
    """Deaktiveringen treffer et MEDLEMSKAP, ikke et løp. Delte det
    eierløse løpet eier med de andre, ville alle mistet eieren sin, og
    tre subjekter hadde fått samme funn."""
    sys.path.insert(0, str(ROT / "deploy/staging"))
    import m18_fasit as f
    egne = [r[0] for r in f.SETT if r[6]]
    eierlose = [r[0] for r in f.SETT if r[1] == "lop_uten_aktiv_eier"]
    assert egne == eierlose and len(egne) == 1
    # …og det løpet er ferskt, så verken stillhet eller frist slår inn.
    r = next(x for x in f.SETT if x[1] == "lop_uten_aktiv_eier")
    assert r[4] == 0 and r[3] == "lang"
