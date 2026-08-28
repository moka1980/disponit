"""Evidensgrensen `m57-v1` — registrert FØR modulen bygges (M-57-
klarsignalet §0/§10), og målt her med begge retninger per invariant.

Grensens form er PARET (forsøk, brudd): null brudd beviser ingenting
uten minst ett forsøk (en fraværstest går grønn på søppel), og settet
av invarianter er PINNET i `M57_INVARIANTER` — et artefakt kan ikke
definere bort et punkt ved å utelate det. Skjemaet er lukket begge
veier: manglende felt felles av `required`, fremmede av
`additionalProperties`.
"""
from __future__ import annotations

from pathlib import Path

from manifestskjema import (KRAVGRENSER, M57_INVARIANTER, _bias_utledet,
                            _sjekk_grenser,
                            valider_artefaktformat)

ROT = Path(__file__).resolve().parents[3]


def _gront_artefakt() -> dict:
    """Bygger et artefakt der hver invariant er PRØVD og holdt."""
    maalt: dict = {}
    for navn in M57_INVARIANTER:
        maalt[f"{navn}_forsok"] = 3
        maalt[f"{navn}_brudd"] = 0
    maalt["ui_tastaturgjennomgang_dokumentert"] = True
    maalt["ddl_begge_kjoringer_gronne"] = True
    maalt["ytelse_full_bunt_soknader"] = 5000
    maalt["ytelse_full_bunt_minutter"] = 212.5
    # #167 valg B: biasinvarianten UTLEDES av disse, den leses ikke.
    # Tre digester kjørt, tre målinger — så `_forsok` er 3 og `_brudd` 0
    # fordi DATAENE viser det, ikke fordi produsenten skrev det.
    digester = [f"sha256:{str(i) * 64}" for i in (1, 2, 3)]
    maalt["bias_digester_kjort"] = digester
    maalt["bias_maalinger"] = [
        {"image_digest": d, "artefakt_sha256": f"{i}" * 64,
         "ts": "2026-08-23T00:00:00+00:00"}
        for i, d in zip("abc", digester)]
    return {
        "krav_id": "m57-v1",
        "ts": "2026-08-23T00:00:00+00:00",
        "bestatt": True,
        "oppsett": {"modul": "m57_ats", "commit": "0" * 40, "vert": "lokal"},
        "maalt": maalt,
    }


def test_grensen_dekker_klarsignalets_punkter():
    """§10 teller 8 sikkerhetsinvarianter + 11 øvrige numeriske + 2
    ja-punkter. Tallene er pinnet MOT KLARSIGNALET, ikke mot listen selv
    — krymper settet, er det denne som skal rødne, ikke bare validatoren
    som stille måler færre punkter."""
    g = KRAVGRENSER["m57-v1"]
    assert len(M57_INVARIANTER) == 19
    assert len(g["krav_ja"]) == 2
    assert g["maks_brudd"] == 0 and g["min_forsok"] == 1
    # Settet er unikt og grensen bærer det pinnede settet, ikke en kopi.
    assert len(set(M57_INVARIANTER)) == 19
    assert g["invarianter"] is M57_INVARIANTER


def test_gront_artefakt_bestar_begge_portene():
    art = _gront_artefakt()
    assert valider_artefaktformat(art, "m57-v1") == []
    assert _sjekk_grenser("m57-v1", art) == []


def test_ett_brudd_feller_uansett_hvilken_invariant():
    for navn in M57_INVARIANTER:
        art = _gront_artefakt()
        art["maalt"][f"{navn}_brudd"] = 1
        feil = _sjekk_grenser("m57-v1", art)
        assert any(f"{navn}_brudd=1" in f for f in feil), navn


def test_null_forsok_feller_selv_med_null_brudd():
    """Selve poenget med parformen: 0 brudd over 0 forsøk er en port som
    aldri kjørte, og den er RØD — for hver invariant."""
    for navn in M57_INVARIANTER:
        art = _gront_artefakt()
        art["maalt"][f"{navn}_forsok"] = 0
        feil = _sjekk_grenser("m57-v1", art)
        assert any(f"{navn}_forsok=0" in f for f in feil), navn


def test_ja_punktene_krever_bokstavelig_true():
    """Alt annet enn `True` er nei (§10 siste linje) — også sannhets-
    lignende verdier som 1 og "ja", som en produsent kunne skrive i god
    tro."""
    for navn in ("ui_tastaturgjennomgang_dokumentert",
                 "ddl_begge_kjoringer_gronne"):
        for verdi in (False, None, 1, "ja"):
            art = _gront_artefakt()
            art["maalt"][navn] = verdi
            assert any(navn in f for f in _sjekk_grenser("m57-v1", art)), \
                (navn, verdi)


def test_ytelsespunktet_er_en_maling_ikke_et_ja_punkt():
    """Codex P1: `staging_sjekkliste.ytelse_bestatt` pekte på `m57-v1`,
    men grensen bar bare invariantpar og to booleans. Et skjemagyldig,
    grønt artefakt kunne dermed krysse av for ytelse uten at noen hadde
    kjørt en eneste søknad — og en modul som ikke er levedyktig ville
    passert aktiveringen.

    De to tallene måles SAMMEN med vilje: en varighet uten last er en tom
    kjøring, og en full bunt uten varighet er bare en påstand om at det
    gikk. Begge retninger felles her, og skjemaet feller fraværet
    uavhengig — samme to-lags-form som invariantene."""
    g = KRAVGRENSER["m57-v1"]
    # For lite last: en prøve på 4999 er ikke den fulle bunten.
    art = _gront_artefakt()
    art["maalt"]["ytelse_full_bunt_soknader"] = g["ytelse_min_soknader"] - 1
    assert any("ytelse_full_bunt_soknader" in f
               for f in _sjekk_grenser("m57-v1", art))
    # For lang tid: ett minutt over §4s frist er ikke bestått.
    art = _gront_artefakt()
    art["maalt"]["ytelse_full_bunt_minutter"] = g["ytelse_maks_minutter"] + 1
    assert any("ytelse_full_bunt_minutter" in f
               for f in _sjekk_grenser("m57-v1", art))
    # En kjøring som varte 0 minutter har ikke skjedd.
    for verdi in (0, -1):
        art = _gront_artefakt()
        art["maalt"]["ytelse_full_bunt_minutter"] = verdi
        assert any("ytelse_full_bunt_minutter" in f
                   for f in _sjekk_grenser("m57-v1", art)), verdi
    # Og fraværet felles av BEGGE lag, som for invariantene.
    for felt in ("ytelse_full_bunt_soknader", "ytelse_full_bunt_minutter"):
        art = _gront_artefakt()
        del art["maalt"][felt]
        assert valider_artefaktformat(art, "m57-v1") != [], felt
        assert any(felt in f for f in _sjekk_grenser("m57-v1", art)), felt


def test_ytelsesgrensen_er_klarsignalets_tall():
    """Grensen skal være DE SAMME tallene kontrakten håndhever, ikke to
    tall som ligner. `antall_soknader`-taket er den fulle bunten, og
    akseptkonvolutten er klarsignalets 240 minutter (§4).

    ORDREFRISTEN var lenge KLEMT under konvolutten (Codex P1 på #210):
    den er løftet til KUNDEN, og et løfte utover autoriteten som faktisk
    utstedes (claim-leasen/opplastingskapabilitetens 3600 s) kunne ingen
    holde — etter første time kunne en annen kontrollør reclaime og
    duplisere evalueringen av samme persondatabunt. 063 (#165) bygde
    fornyelsesveien som kjeder grants forbi taket, men en dør er ikke en
    pust, så klemmen sto til noen KALTE den (Cursor P1, runde 2).

    M-57s utførerkjede er den kalleren, og klemmen er dermed løftet:
    fristen er konvolutten selv, 240 min. Porten måler nå det tallet
    direkte — og `test_frist_over_ett_grant_krever_fornyelsesveien`
    holder den andre halvdelen: hever noen fristen forbi ett grant uten
    at både døren i basen og kallstedet finnes, felles det der."""
    import oppdragskontrakt as ok
    g = KRAVGRENSER["m57-v1"]
    _, tak = ok.FELTGRENSER["rekruttering.evaluering"]["antall_soknader"]
    assert g["ytelse_min_soknader"] == tak
    _, frister = ok.UTFORELSESFRIST_VALG["rekruttering.evaluering"]
    assert frister["bunt"] == g["ytelse_maks_minutter"] * 60
    # ...og fristen er nå FAKTISK lengre enn ett grant — porten over er
    # dermed AKTIV, ikke ladd. Står denne igjen som usann, er klemmen
    # sneket inn igjen uten at kallstedet forsvant.
    assert frister["bunt"] > ok.UTSTEDT_AUTORITET_S


def test_utelatt_invariant_felles_av_begge_lag():
    """Et artefakt uten et av parfeltene: skjemaet feller det
    (`required`), og grensesjekken feller det uavhengig — to lag, samme
    dom, så ingen av dem kan råtne usett."""
    art = _gront_artefakt()
    del art["maalt"]["arkiv_utpakking_utenfor_grense_brudd"]
    assert valider_artefaktformat(art, "m57-v1") != []
    assert any("arkiv_utpakking_utenfor_grense_brudd" in f
               for f in _sjekk_grenser("m57-v1", art))


def test_fremmede_felter_avvises_av_skjemaet():
    """Lukket skjema: en produsent kan ikke smugle inn en «egen»
    måling og senere sitere den som om grensen dekket den."""
    art = _gront_artefakt()
    art["maalt"]["egen_maaling_forsok"] = 5
    assert valider_artefaktformat(art, "m57-v1") != []


def test_skjemaets_feltsett_er_generert_fra_settet():
    """Skjemafilen er avledet av `M57_INVARIANTER` — driver de fra
    hverandre, er det denne porten som sier ifra, ikke en aksept-
    kjøring måneder senere."""
    import json
    from pathlib import Path
    skjema = json.loads(
        (Path(__file__).resolve().parents[1] / "artefakt-m57-skjema.json")
        .read_text(encoding="utf-8"))
    felter = set(skjema["properties"]["maalt"]["properties"])
    ventet = {f"{n}_{s}" for n in M57_INVARIANTER for s in ("forsok", "brudd")}
    ventet |= {"ui_tastaturgjennomgang_dokumentert",
               "ddl_begge_kjoringer_gronne",
               "ytelse_full_bunt_soknader", "ytelse_full_bunt_minutter",
               # #167 valg B: dataene biasinvarianten UTLEDES av. De står
               # her, ikke i invariantparet, fordi de ikke er en invariant
               # — de er grunnlaget ett av parene regnes fra.
               "bias_digester_kjort", "bias_maalinger"}
    assert felter == ventet
    assert set(skjema["properties"]["maalt"]["required"]) == ventet


# ===========================================================================
# #167 valg B — biasinvarianten utledes, den leses ikke
# ===========================================================================

def _m57_feil(art):
    from manifestskjema import KRAVGRENSER, _grenser_m57
    return _grenser_m57(KRAVGRENSER["m57-v1"], art)


def test_en_digest_uten_maaling_felles_selv_om_modulen_rapporterer_null():
    """#167 (Codex P1 ×3 på #153): invarianten var to selvrapporterte tall.

    `bias_maling_mangler_for_digest` besto av `_forsok` og `_brudd` i
    artefaktet. Modulen skrev «0 brudd», grensen leste «0 brudd», og
    kjøretidsporten `krev_biasmaaling` måler bare FORMEN på en måling — så
    ingen ledd i kjeden målte at det fantes en biasmåling for digesten.

    Nå bærer artefaktet dataene, og bruddtallet regnes på nytt. Rapporterer
    modulen null mens en digest står udekket, er avviket selve funnet —
    samme disiplin som `_grenser_rollback`: tallene mot hverandre, ikke mot
    flagg.

    MUTASJONEN SOM DREPER DENNE: les `_brudd` i stedet for å utlede det.
    """
    art = _gront_artefakt()
    # Fjern målingen for én digest, men la modulen fortsette å påstå null.
    art["maalt"]["bias_maalinger"] = art["maalt"]["bias_maalinger"][:2]
    feil = _m57_feil(art)
    assert any("uten måling" in f for f in feil), \
        f"en udekket digest slapp gjennom med rapportert null: {feil}"


def test_forsoket_er_antallet_digester_ikke_et_tall_modulen_velger():
    """Null brudd beviser ingenting uten at porten ble stilt spørsmålet.

    `_forsok` skulle si at invarianten ble PRØVD. Var det et fritt tall,
    kunne en kjøring med én digest rapportere tre forsøk og se grundigere
    ut enn den var.

    MUTASJONEN SOM DREPER DENNE: slutt å sammenligne `_forsok` med
    antallet digester.
    """
    art = _gront_artefakt()
    art["maalt"]["bias_maling_mangler_for_digest_forsok"] = 7
    feil = _m57_feil(art)
    assert any("digest(er)" in f and "forsok" in f for f in feil), \
        f"forsøkstallet var frikoblet fra digestene: {feil}"


def test_grunnlaget_kan_ikke_utelates():
    """En uutledbar invariant er ingen port.

    Uten `bias_digester_kjort` finnes det ingenting å regne fra, og da er
    vi tilbake til å lese modulens eget tall. Fraværet må derfor felles,
    ikke hoppes over — samme fail-closed-form som punktbindingen i #166.

    MUTASJONEN SOM DREPER DENNE: returner tom liste når grunnlaget mangler.
    """
    for felt in ("bias_digester_kjort", "bias_maalinger"):
        art = _gront_artefakt()
        del art["maalt"][felt]
        feil = _m57_feil(art)
        assert any(felt in f for f in feil), \
            f"artefaktet passerte uten `{felt}`: {feil}"

    art = _gront_artefakt()
    art["maalt"]["bias_digester_kjort"] = []
    assert any("tom" in f for f in _m57_feil(art)), \
        "en tom digestliste utleder null brudd av ingenting"


def test_en_gjentatt_digest_er_ikke_et_forsok_til():
    """Hullet #167 stengte, gjenåpnet gjennom sitt eget grunnlag.

    Forsøkstallet måles mot `len(bias_digester_kjort)`, mens dekningen
    regnes mot `set(...)`. Står én digest tre ganger, er `forsok=3` sant
    med ÉN måling og null brudd — og artefaktet ser ut som en kjøring mot
    tre modellversjoner når den prøvde én. Det er ordrett det
    `test_forsoket_er_antallet_digester_ikke_et_tall_modulen_velger`
    påstår er stengt, så uten denne porten løy den testen.

    Begge lag feller det, som for de andre feltene: skjemaet på
    `uniqueItems`, grensesjekken uavhengig.

    MUTASJONEN SOM DREPER DENNE: tell duplikater som forsøk igjen (regn
    `_forsok` mot `len(digester)` uten å avvise gjentakelser).
    """
    art = _gront_artefakt()
    d = art["maalt"]["bias_digester_kjort"][0]
    # Én digest, prøvd én gang, utgitt for tre forsøk.
    art["maalt"]["bias_digester_kjort"] = [d, d, d]
    art["maalt"]["bias_maalinger"] = art["maalt"]["bias_maalinger"][:1]
    art["maalt"]["bias_maling_mangler_for_digest_forsok"] = 3
    art["maalt"]["bias_maling_mangler_for_digest_brudd"] = 0
    feil = _m57_feil(art)
    assert any("gjentar" in f for f in feil), \
        f"tre kopier av én digest passerte som tre forsøk: {feil}"
    assert valider_artefaktformat(art, "m57-v1") != [], \
        "skjemaet slapp gjennom en gjentatt digest (uniqueItems)"


def test_to_malinger_for_samme_digest_er_tvetydig_bevis():
    """Kjøretidssiden er `dict[str, Biasmaaling]` — én måling per digest.

    En liste med to ulike målinger for samme digest kan derfor ikke være
    en tro gjengivelse av kartet porten faktisk ble stilt. Å plukke den
    første ville gjort grensen til en som VELGER hvilket bevis som
    gjelder; det valget hører ikke hjemme her. Tvetydig bevis felles.

    MUTASJONEN SOM DREPER DENNE: la `dekket` svelge gjentatte digester
    stille (`dekket.add(d)` uten å se om den alt er der).
    """
    art = _gront_artefakt()
    maalinger = art["maalt"]["bias_maalinger"]
    d = maalinger[0]["image_digest"]
    # Samme digest, to ulike artefakthasher — hvilken er beviset?
    art["maalt"]["bias_maalinger"] = maalinger + [
        {"image_digest": d, "artefakt_sha256": "f" * 64,
         "ts": "2026-08-23T00:00:00+00:00"}]
    feil = _m57_feil(art)
    assert any("mer enn én gang" in f for f in feil), \
        f"to målinger for samme digest passerte som bevis: {feil}"


def test_bias_maaling_med_ugyldig_ts_felles():
    """`format: date-time` er inert i skjemaet — grensen må lese datoen.

    `valider_artefaktformat` bygger `Draft202012Validator` UTEN
    `FormatChecker`, så `"format"` er en annotasjon, ikke en port: `ts:
    ""` og `ts: "ikke-en-dato"` passerer skjemalaget. Kjøretidsporten
    `krev_biasmaaling` feller dem (`bias_maling_uten_tidspunkt`), og
    docstringen her lover at formkravene er DE SAMME — så uten denne
    lesningen løy løftet, og en udatert oppføring telte som måling.
    Nøyaktig samme klasse som «en oppføring er ikke en måling» (Codex P2
    på port 17), gjenåpnet i evidenslaget.

    MUTASJONEN SOM DREPER DENNE: dropp `fromisoformat`-lesningen og stol
    på `"format": "date-time"`.
    """
    for daarlig in ("", "ikke-en-dato", "2026/01/01 00:00", None):
        art = _gront_artefakt()
        art["maalt"]["bias_maalinger"][0]["ts"] = daarlig
        feil = _m57_feil(art)
        assert any("RFC 3339" in f for f in feil), \
            f"ts={daarlig!r} passerte som datert bevis: {feil}"
    # ... OG KALENDEREN MÅLES SEPARAT. Formen kan stemme til punkt og
    # prikke mens datoen ikke finnes; da er det `fromisoformat` som
    # feller, ikke mønsteret. Uten denne halvdelen kunne kalenderleddet
    # slettes uten at noe ble rødt.
    art = _gront_artefakt()
    for umulig in ("2026-02-30T00:00:00Z", "2026-13-45T99:00:00Z"):
        art = _gront_artefakt()
        art["maalt"]["bias_maalinger"][0]["ts"] = umulig
        feil = _m57_feil(art)
        assert any("kalenderen sier nei" in f for f in feil), \
            f"{umulig} passerte som datert bevis: {feil}"

    # Og den gyldige formen porten selv bruker (`Z`-suffiks) består.
    art = _gront_artefakt()
    art["maalt"]["bias_maalinger"][0]["ts"] = "2026-01-01T00:00:00Z"
    assert not _m57_feil(art), \
        "en gyldig ISO 8601 med Z-suffiks ble felt — grensen leser" \
        " strengere enn kjøretidsporten den speiler"


def test_ts_uten_utc_offset_er_ikke_datert_bevis():
    """`fromisoformat` er ISO 8601, ikke RFC 3339.

    Skjemaet erklærer `ts` som `format: date-time`, og RFC 3339 gjør
    UTC-offset obligatorisk. `fromisoformat` er romsligere enn den
    erklæringen: `"2026-01-01"` (bare dato), `"2026-W01-1"` (ukedato) og
    `"2026-01-01T12:00:00"` (uten sone) leses alle uten å heve. Alle tre
    ble derfor talt som datert bevis, mens `format` samtidig var inert i
    skjemalaget — så feltet var i praksis ustyrt i begge lag. Et
    tidspunkt uten sone er ikke et tidspunkt, men en påstand om ett: to
    målinger fra hver sin verdensdel kan ikke ordnes mot hverandre.

    Kravet leses fortsatt med kalenderen, ikke med en håndskrevet
    ISO-grammatikk (K4): en dato uten klokkeslett KAN ikke bære offset,
    så det samme leddet feller alle tre formene.

    MUTASJONEN SOM DREPER DENNE: slett `if lest.tzinfo is None`-leddet.
    """
    for ikke_rfc in ("2026-01-01", "2026-W01-1", "2026-01-01T12:00:00",
                     "20260101T120000", "2026-01-01x00:00:00+00:00",
                     "2026-01-01T00:00:00+00:00:30"):
        art = _gront_artefakt()
        art["maalt"]["bias_maalinger"][0]["ts"] = ikke_rfc
        feil = _m57_feil(art)
        assert any("RFC 3339" in f for f in feil), \
            f"ts={ikke_rfc!r} passerte som datert bevis: {feil}"

    # Og begge de gyldige skrivemåtene består — porten skal ikke være
    # strengere enn RFC 3339, bare like streng.
    for gyldig in ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00+02:00"):
        art = _gront_artefakt()
        art["maalt"]["bias_maalinger"][0]["ts"] = gyldig
        assert not _m57_feil(art), f"{gyldig!r} ble felt — porten leser" \
            " strengere enn standarden den påberoper seg"


def test_grensen_leser_versaler_SOM_kjoretidsporten():
    """Codex P2: en grense strengere enn porten den speiler.

    `_er_sha256` sammenligner `verdi.lower()`, så `krev_biasmaaling`
    godtar en måling med store heksadesimaler. Grensen avviste den — og
    da ville et akseptartefakt for en kjøring som FAKTISK gikk igjennom
    blitt felt av evidenslaget. Løftet er speilingen; da må skrivemåten
    være den samme i begge lag og i skjemaet.

    Dekningen sammenlignes på normalform, ellers ville `sha256:AB…` i
    digestlisten og `sha256:ab…` i målingen sett ut som to ulike
    modellversjoner — og porten rapportert både «mangler måling» og
    «foreldreløs måling» for én og samme.

    MUTASJONEN SOM DREPER DENNE: `[0-9a-f]` tilbake i `_bias_utledet`
    eller i skjemaet, eller `dekket.add(d)` uten `.lower()`.
    """
    def _stor(d: str) -> str:
        return d[:7] + d[7:].upper()

    art = _gront_artefakt()
    art["maalt"]["bias_digester_kjort"] = [
        _stor(d) for d in art["maalt"]["bias_digester_kjort"]]
    for m in art["maalt"]["bias_maalinger"]:
        m["image_digest"] = _stor(m["image_digest"])
        m["artefakt_sha256"] = m["artefakt_sha256"].upper()
    assert not _m57_feil(art), (
        "store heksadesimaler ble felt av grensen, mens kjøretidsporten"
        f" godtar dem: {_m57_feil(art)}")
    assert valider_artefaktformat(art, "m57-v1") == [], \
        "skjemaet avviser skrivemåten kjøretidsporten godtar"

    # ... OG BLANDET SKRIVEMÅTE er samme digest, ikke to. BEGGE
    # RETNINGENE måles: normaliseres bare den ene siden, overlever
    # mutasjonen på den andre. Første utgave av porten hadde nettopp det
    # hullet — den skrev versaler bare i digestlisten, og da holdt
    # `dekket.add(d)` uten `.lower()` fortsatt.
    art = _gront_artefakt()
    art["maalt"]["bias_digester_kjort"] = [
        _stor(d) for d in art["maalt"]["bias_digester_kjort"]]
    assert not _m57_feil(art), (
        "versaler i DIGESTLISTEN ble lest som andre digester enn"
        f" målingenes: {_m57_feil(art)}")

    art = _gront_artefakt()
    for m in art["maalt"]["bias_maalinger"]:
        m["image_digest"] = _stor(m["image_digest"])
    assert not _m57_feil(art), (
        "versaler i MÅLINGENE ble lest som andre digester enn"
        f" digestlistens — `dekket` normaliseres ikke: {_m57_feil(art)}")


def test_lowercase_z_er_et_gyldig_utc_suffiks_i_BEGGE_lag():
    """Codex P2: mønsteret og kalenderen var uenige om `z`.

    RFC 3339 tillater begge versalformer av UTC-suffikset, og mønsteret
    fra forrige runde godtar `[Zz]` — men bare den STORE ble byttet ut før
    `fromisoformat`, så en gyldig `2026-01-01t00:00:00z` besto
    grammatikken og felte på kalenderen. To ledd som måler hver sin ting
    skal ikke være uenige om hva de leser.

    Kjøretidsporten hadde nøyaktig samme uenighet i sin egen
    `krev_biasmaaling`, og er rettet med den — ellers ville grensen
    sluttet å speile den i det øyeblikket den ble riktig.

    MUTASJONEN SOM DREPER DENNE: `.replace("Z", "+00:00")` tilbake.
    """
    for gyldig in ("2026-01-01t00:00:00z", "2026-01-01T00:00:00Z"):
        art = _gront_artefakt()
        art["maalt"]["bias_maalinger"][0]["ts"] = gyldig
        assert not _m57_feil(art), \
            f"{gyldig!r} er gyldig RFC 3339, men ble felt: {_m57_feil(art)}"


def test_hengende_linjeskift_i_en_digest_felles_i_BEGGE_lag():
    """Pythons `$` matcher rett FØR en avsluttende linjeskift.

    `^sha256:[0-9a-f]{64}$` slapp derfor `"sha256:<64 hex>\n"` gjennom —
    både i grensesjekken og i skjemaets `pattern`, som `jsonschema`
    kjører med `re.search` og samme semantikk. En digest med hengende
    data telte da som gyldig bevis, mens kjøretidssiden slår opp på den
    NØYAKTIGE strengen (`dict[str, Biasmaaling]`) og aldri finner den
    igjen: dekningen ville sagt grønt om en nøkkel ingen kan bruke.
    Anker er nå `\\A`/`\\Z`, som ikke kjenner noe slikt unntak.

    MUTASJONEN SOM DREPER DENNE: sett `^`/`$` tilbake i `_bias_utledet`
    (første halvdel) eller i skjemaet (andre halvdel).
    """
    # Meldingen MÅ være formmeldingen. Første utgave av denne porten
    # spurte bare om `_m57_feil` sa noe i det hele tatt, og da overlevde
    # mutasjonen: en digest med hengende linjeskift havner uansett
    # utenfor `dekket`, så dekningsleddet klaget om «mangler måling» og
    # porten ble grønn på feil grunn. Vi krever nå ordene fra det leddet
    # som faktisk leser formen.
    art = _gront_artefakt()
    art["maalt"]["bias_digester_kjort"][0] += "\n"
    feil = _m57_feil(art)
    assert any("ikke er digester" in f for f in feil), \
        f"en digest med hengende linjeskift passerte formleddet: {feil}"
    assert valider_artefaktformat(art, "m57-v1") != [], \
        "skjemaets pattern slapp gjennom en digest med hengende linjeskift"

    art = _gront_artefakt()
    art["maalt"]["bias_maalinger"][0]["artefakt_sha256"] = "a" * 64 + "\n"
    feil = _m57_feil(art)
    assert any("ikke en sha256" in f for f in feil), \
        f"en artefakthash med hengende linjeskift passerte: {feil}"
    assert valider_artefaktformat(art, "m57-v1") != [], \
        "skjemaets pattern slapp gjennom en hash med hengende linjeskift"

    # SKJEMAET SKAL VÆRE PORTABELT (Codex P2, runde 2). `\\A`/`\\Z` er
    # Pythons anker, ikke ECMA-262s, og en standardsorientert validator kan
    # avvise mønsteret eller lese escapene som bokstaver — da feiler
    # GYLDIGE digester. Skjemaet bruker derfor `^...$`, som i ECMA-262
    # ikke har Pythons linjeskift-unntak, og lengdegrensene lukker hullet
    # også for Pythons `re`: 71 tegn for `sha256:<64 hex>`, 64 for en bar
    # hash. Pythons egne anker bor i `_bias_utledet`, der de hører hjemme.
    import json as _json
    skjema = _json.loads(
        (ROT / "platform" / "core" / "artefakt-m57-skjema.json")
        .read_text(encoding="utf-8"))
    monstre = []

    def _samle(node):
        if isinstance(node, dict):
            if "pattern" in node:
                monstre.append(node)
            for v in node.values():
                _samle(v)
        elif isinstance(node, list):
            for v in node:
                _samle(v)

    _samle(skjema)
    assert monstre, "fant ingen mønstre i skjemaet — porten måler ingenting"
    for node in monstre:
        assert "\\A" not in node["pattern"] and "\\Z" not in node["pattern"], (
            f"Python-anker i et delt skjema: {node['pattern']!r}")
    for node in monstre:
        if "[0-9a-f]{64}" in node["pattern"]:
            assert "maxLength" in node, (
                f"{node['pattern']!r} står uten lengdegrense — `$` i"
                " Pythons `re` slipper da et hengende linjeskift igjennom")


def test_duplikatsjekken_skalerer_lineaert_ikke_kvadratisk():
    """`bias_digester_kjort` har ingen `maxItems` — formen må tåle det.

    Duplikatsjekken kalte `list.count()` per element, altså én full
    gjennomlesning per digest. Målt her på maskinen porten kjører på:
    den kvadratiske formen bruker 1,33 s på 10 000 unike digester og
    skalerer med kvadratet, altså ~21 s på de 40 000 denne porten
    stiller. `Counter` gjør samme arbeid på 0,04 s. Budsjettet under er
    tre sekunder — 75 ganger over den lineære målingen, og sju ganger
    UNDER den kvadratiske, så porten er hverken flakete eller snill.

    Kostnaden traff også den ærlige veien: hver digest er unik, så det
    er ikke duplikatene som er dyre, men letingen etter dem.

    MUTASJONEN SOM DREPER DENNE: `sorted({d for d in digester if
    digester.count(d) > 1})`.
    """
    import time

    digester = [f"sha256:{i:064x}" for i in range(40_000)]
    m = {"bias_digester_kjort": digester,
         "bias_maalinger": [{"image_digest": digester[0],
                             "artefakt_sha256": "a" * 64,
                             "ts": "2026-01-01T00:00:00+00:00"}]}
    start = time.perf_counter()
    feil = _bias_utledet(m)
    brukt = time.perf_counter() - start
    assert brukt < 3.0, (
        f"40 000 unike digester tok {brukt:.1f} s — duplikatsjekken er"
        " kvadratisk igjen, og et artefakt uten maxItems kan da gjøre"
        " validering til en kostnad angriperen velger")
    # Og den gjør fortsatt jobben sin: 39 999 digester mangler måling.
    assert any("ikke er målt" in f or "mangler" in f for f in feil), \
        f"den raske formen sluttet å måle dekning: {feil}"


def test_bias_maaling_for_udeklarert_digest_felles():
    """Dekningen måles begge veier — ellers er den halv.

    `mangler = set(digester) - dekket` finner digester uten måling. Den
    omvendte differansen ble ikke sjekket, så `bias_digester_kjort=[d1]`
    med `bias_maalinger=[d1, d2]` ga `mangler=[]`, `forsok=1`, `brudd=0`
    — grønt, mens målingslisten dokumenterer en modellversjon kjøringen
    aldri sa den brukte. Evidenslistene motsier hverandre, og
    forsøkstallet beskriver da en annen kjøring enn den bevisene viser.

    Det er lineage-disiplinen #167 valg B innførte: forsøk = det
    DEKLARERTE digest-settet. En produsent som kan legge biasbevis ved
    siden av det settet, kan pynte på kjøringen uten at porten reagerer.

    MUTASJONEN SOM DREPER DENNE: sjekk bare `set(digester) - dekket`.
    """
    art = _gront_artefakt()
    d1 = art["maalt"]["bias_digester_kjort"][0]
    # Én digest deklarert, to målt — den andre er formgyldig og dekket.
    art["maalt"]["bias_digester_kjort"] = [d1]
    art["maalt"]["bias_maling_mangler_for_digest_forsok"] = 1
    art["maalt"]["bias_maling_mangler_for_digest_brudd"] = 0
    feil = _m57_feil(art)
    assert any("ikke står i" in f for f in feil), \
        f"en måling for en udeklarert digest passerte som grønt: {feil}"


def test_grensen_mot_valg_A_er_skrevet_ned_aerlig():
    """Det B IKKE gjør, sagt høyt — så neste leser ikke tror den er dekket.

    En form-gyldig måling for en digest som aldri ble målt passerer
    fortsatt: `artefakt_sha256` er en streng med riktig fasong, og at
    artefakten FINNES i et lager måles ingen steder. Det er #167 valg A, og
    den hører i controlleren som har tenantkontekst — ikke i en ren
    rangeringsfunksjon og ikke i et manifestskjema.

    Porten står her fordi en kommentar som lover mer enn den måler er
    verre enn ingen kommentar (Codex P1, runde 5 på #153). Denne testen ER
    påstanden om hva som ikke er dekket, målt.
    """
    art = _gront_artefakt()
    # Oppdiktet, men form-gyldig: ingen slik biasartefakt finnes noe sted.
    art["maalt"]["bias_maalinger"] = [
        {"image_digest": d, "artefakt_sha256": "0" * 64,
         "ts": "2026-01-01T00:00:00Z"}
        for d in art["maalt"]["bias_digester_kjort"]]
    assert not _m57_feil(art), \
        "B har begynt å måle eksistens — da skal denne porten byttes ut" \
        " med A sin, ikke slettes"
