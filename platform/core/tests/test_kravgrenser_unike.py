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


# ---------------------------------------------------------------------------
# …OG DET ANDRE HULLET: en grense kan være REGISTRERT uten å være DEKKET.
# ---------------------------------------------------------------------------

#: Grenser som ennå ikke HAR en modul å dekkes av. Klynge 7s fundament
#: registrerte fem grenser før koden (§0-regelen), og fire av modulene
#: er ikke bygget. Listen er PINNET og ikke avledet: en grense som blir
#: liggende her etter at modulen er bygget, er nettopp den stillheten
#: porten under finnes for å bryte.
#:
#: Å FJERNE ET NAVN HERFRA ER EN DEL AV Å BYGGE MODULEN, ikke et
#: separat opprydningsarbeid.
#: KLYNGE 8 — PROGNOSENE. Registrert FØR koden (§0-regelen), slik
#: klynge 7 ble det, og av samme grunn: grensen skal være skrevet av
#: noen som ennå ikke vet hvor vanskelig den blir å holde.
#:
#: Lista var TOM i noen timer 5/9, mellom M-53 og dette fundamentet.
#: Det var ikke en hviledag — det var beviset på at forrige klynge
#: faktisk ble ferdig, og porten under er den som krever at navnene
#: forsvinner herfra etter hvert som modulene bygges.
#:
#: Se `docs/KLYNGE8-FUNDAMENT.md`.
UBYGDE_GRENSER = frozenset({
    # KLYNGE 10 — HANDLINGENE. Registrert 6/9, før koden.
    #
    # Lista sto TOM i noen timer 5-6/9, mellom 136 og dette
    # fundamentet. Det var ikke en hviledag — det var beviset på at
    # klynge 9 faktisk ble ferdig.
    #
    # DEN DELTE DOMMEN: en handling med virkning i den virkelige
    # verden angres ikke av en rollback. Alle fire holder tilbake
    # nøyaktig den fullmakten modulen ser ut til å trenge.
    #
    # Se docs/KLYNGE10-FUNDAMENT.md.
    # `m29-v1` gikk ut 6/9, da 137 landet med sin dekningsport.
    # `m32-v1` gikk ut 6/9, da 138 landet med sin dekningsport.
    # `m28-v1` gikk ut 6/9, da 139 landet med sin dekningsport.
    # `m40-v1` gikk ut 6/9, da 140 landet med sin dekningsport — og
    # DERMED ER LISTA TOM FOR KLYNGE 10. Katalogen er 57 av 57.
    # KLYNGE 9 — YTRINGENE. Registrert 5/9, før koden.
    #
    # Lista sto TOM i noen minutter mellom 132 og dette fundamentet.
    # `m15-v1` gikk ut da 128 landet, `m33-v1` da 130 landet, og
    # `m36-v1` da 132 landet — å fjerne et navn herfra er en del av å
    # bygge modulen, ikke et separat opprydningsarbeid.
    #
    # Se docs/KLYNGE9-FUNDAMENT.md.
    # `m7-v1` gikk ut 5/9, da 133 landet med sin dekningsport.
    # `m20-v1` gikk ut 5/9, da 134 landet med sin dekningsport.
    # `m43-v1` gikk ut 5/9, da 135 landet med sin dekningsport.
    # `m45-v1` gikk ut 5/9, da 136 landet med sin dekningsport.
})


def _grenser_med_dekningsport() -> set[str]:
    """Hver `krav_id` som en testfil FAKTISK slår opp.

    Leser alle `test_*.py` unntatt denne fila: her nevnes `m11-v1` i
    en docstring uten å dekkes, og en port som talte det som dekning
    ville vært en port som målte sin egen tekst.
    """
    import re
    mal = re.compile(r'KRAVGRENSER\[\s*["\']([a-z0-9-]+)["\']\s*\]')
    ut: set[str] = set()
    for fil in Path(__file__).resolve().parent.glob("test_*.py"):
        if fil.name == Path(__file__).name:
            continue
        ut |= set(mal.findall(fil.read_text(encoding="utf-8")))
    return ut


def test_hver_registrert_grense_dekkes_av_en_port():
    """EN GRENSE INGEN LESER ER EN GRENSE SOM IKKE HOLDER NOE.

    `test_ingen_krav_id_registreres_to_ganger` over pinner at en grense
    ikke OVERSKRIVES. Denne pinner at den er DEKKET — at det finnes en
    port som faktisk slår den opp og måler invariantene mot koden.

    HULLET VAR EKTE OG BLE MÅLT: fire bygde moduler — M-3, M-10, M-11
    og M-31 — hadde grenser i `KRAVGRENSER` og porter i testfilene, men
    INGENTING som bandt de to sammen. En invariant kunne fjernes fra
    grensen, eller en port slettes, uten at én test merket det.

    Det er samme feilklasse som nestenuhellet denne fila ble skrevet
    for, sett fra den andre siden: der forsvant innholdet i en grense
    ved overskriving, her kunne det forsvinne ved forsømmelse.

    MUTASJONEN SOM DREPER DENNE: registrer en ny grense i
    `manifestskjema.py` uten å skrive en port for den.
    """
    from manifestskjema import KRAVGRENSER
    dekket = _grenser_med_dekningsport()
    udekket = sorted(
        k for k, g in KRAVGRENSER.items()
        if (g.get("invarianter") or ()) and k not in dekket
        and k not in UBYGDE_GRENSER)
    assert udekket == [], (
        "grenser uten en port som leser dem — en registrert grense"
        " ingen måler mot koden er en grense som ikke holder noe: "
        + ", ".join(udekket))


def test_de_ubygde_grensene_er_faktisk_ubygde():
    """…OG LISTEN OVER SKAL KRYMPE, ALDRI VOKSE I STILLHET.

    Et navn som blir liggende i `UBYGDE_GRENSER` etter at modulen er
    bygget, er et unntak som har overlevd grunnen sin — og da måler
    porten over mindre enn den ser ut til.

    MUTASJONEN SOM DREPER DENNE: bygg M-52 uten å stryke `m52-v1`.
    """
    from manifestskjema import KRAVGRENSER
    ukjente = sorted(k for k in UBYGDE_GRENSER if k not in KRAVGRENSER)
    assert ukjente == [], f"navn uten grense: {ukjente}"
    dekket = _grenser_med_dekningsport()
    overlevd = sorted(UBYGDE_GRENSER & dekket)
    assert overlevd == [], (
        "disse har fått en dekningsport og skal ut av"
        " UBYGDE_GRENSER: " + ", ".join(overlevd))


#: KLYNGE 8s FIRE DELTE DOMMER, som en liste og ikke som en setning i
#: et dokument. `docs/KLYNGE8-FUNDAMENT.md` sier at alle tre modulene
#: hviler på dem — og INGENTING MÅLTE DET.
KLYNGE9 = ("m7-v1", "m20-v1", "m43-v1", "m45-v1")
#: M-7 OG M-43 DELER ÉN OPPTAKSHJEMMEL, IKKE TO.
#:
#: `samtykkehendelse` (M-44, 114) finnes, men den er markedsføringens:
#: `mottaker_id`, `kanal`, `formal`. Den svarer på «har vi lov til å
#: sende dette», ikke på «har vi lov til å ta opp denne samtalen».
#: To modeller for samme hjemmel ville gitt to svar på «hadde vi lov».
KLYNGE9_OPPTAK = ("opptak_uten_hjemmel", "opptak_uten_varsling")
#: KILDEKRAVET er klyngens delte dom, og det står i de to modulene som
#: PÅSTÅR noe utad om verden. M-7 har `referat_uten_kilde` og M-43
#: `transkripsjon_uten_usikkerhet` — samme form, andre ord, fordi et
#: referat og en transkripsjon ikke er påstander om produktet.
KLYNGE9_KILDE = ("m20-v1", "m45-v1")


def test_klynge9_deler_opptakshjemmelen():
    """EN DELT HJEMMEL SOM BARE STÅR I ET DOKUMENT ER IKKE DELT.

    M-7 og M-43 tar begge opp samtaler, og fundamentet slo fast at de
    skal dele ÉN hjemmel — bygget i M-7s runde, arvet av M-43. Står
    invarianten bare i den ene grensen, kan den andre modulen landes
    med sin egen opptaksmodell uten at én port faller.

    Det er samme feilform som klynge 8s: M-36 hadde først bare
    `prognose_uten_maaling`, som om den målte uten å prognostisere.

    MUTASJONEN SOM DREPER DENNE: ta `opptak_uten_hjemmel` ut av
    `m43-v1`.
    """
    from manifestskjema import KRAVGRENSER
    mangler = {}
    for krav_id in ("m7-v1", "m43-v1"):
        inv = set(KRAVGRENSER[krav_id]["invarianter"])
        savnet = [d for d in KLYNGE9_OPPTAK if d not in inv]
        if savnet:
            mangler[krav_id] = savnet
    assert mangler == {}, (
        "opptakshjemmelen er delt i fundamentet, men ikke i grensen:"
        f" {mangler}")


def test_klynge9_krever_kilde_der_modulen_paastaar_noe():
    """KLYNGENS DELTE DOM, MÅLT.

    «Ingen udokumenterte produktpåstander» (M-20) og «ingen påstand
    uten datagrunnlag» (M-45) er den SAMME setningen, skrevet av to
    forfattere som ikke visste om hverandre. Derfor bærer begge
    grensene `paastand_uten_kilde` med nøyaktig samme navn.

    En påstand uten kilde kan ikke etterprøves, og den som skal svare
    for den finner ikke hva den hviler på.
    """
    from manifestskjema import KRAVGRENSER
    mangler = [k for k in KLYNGE9_KILDE
               if "paastand_uten_kilde"
               not in KRAVGRENSER[k]["invarianter"]]
    assert mangler == [], (
        f"klyngens kildekrav mangler i: {mangler}")


def test_klynge9_star_i_ubygde_til_modulene_er_bygget():
    """De fire er registrert FØR koden, og står her til de landes.

    EN GRENSE SOM ER UTE AV LISTA MÅ HA EN PORT SOM DEKKER DEN — det
    er hele bytteforholdet, og det er den samme porten som tømte lista
    tre ganger for klynge 8.
    """
    dekket = _grenser_med_dekningsport()
    for krav_id in KLYNGE9:
        if krav_id in UBYGDE_GRENSER:
            assert krav_id not in dekket, (
                f"{krav_id} har en dekningsport, men står fortsatt"
                " som ubygd")
        else:
            assert krav_id in dekket, (
                f"{krav_id} er ute av UBYGDE_GRENSER uten en port som"
                " dekker grensen")


KLYNGE8 = ("m15-v1", "m33-v1", "m36-v1")
KLYNGE8_DELTE = (
    "prognose_uten_horisont",
    "prognose_uten_modellversjon",
    "prognose_uten_intervall",
    "prognose_uten_maaling",
)


def test_klynge8_deler_prognosedommene():
    """EN DELT DOM SOM BARE STÅR I ET DOKUMENT ER IKKE DELT.

    M-36 hadde i første utgave bare `prognose_uten_maaling` — som om
    modulen målte uten å prognostisere. Den gjør begge deler:
    katalogens output er «tiltakskø, SCENARIO, eksperiment, EFFEKT»,
    og et effektestimat er en prognose uansett hva den kalles.

    AT DEN ENE INVARIANTEN STO DER, VAR SELVE INNRØMMELSEN. En modul
    som må måle prognosene sine, lager prognoser. Uten denne porten
    kunne akseptporten blitt grønn på en M-36 som lagret prognoser
    uten horisont, uten modellversjon og uten intervall — altså i
    brudd med klyngens egen kontrakt, mens fundamentdokumentet sa at
    kontrakten gjaldt (CodeRabbit).

    MUTASJONEN SOM DREPER DENNE: ta ett navn ut av én av de tre.
    """
    from manifestskjema import KRAVGRENSER
    mangler = {}
    for krav_id in KLYNGE8:
        inv = set(KRAVGRENSER[krav_id]["invarianter"])
        savnet = [d for d in KLYNGE8_DELTE if d not in inv]
        if savnet:
            mangler[krav_id] = savnet
    assert mangler == {}, (
        "klynge 8s delte dommer står i fundamentet, men ikke i"
        f" grensen: {mangler}")


def test_klynge8_star_i_ubygde_til_modulene_er_bygget():
    """De tre er registrert FØR koden, og står her til de landes.

    EN GRENSE SOM ER UTE AV LISTA MÅ HA EN PORT SOM DEKKER DEN. Det er
    hele bytteforholdet: navnet forsvinner herfra i det øyeblikket en
    testfil faktisk slår grensen opp og måler invariantene mot koden.

    `m15-v1` gikk ut 5/9, da 128 landet med
    `test_m15_likviditet.py`.
    """
    dekket = _grenser_med_dekningsport()
    for krav_id in KLYNGE8:
        if krav_id in UBYGDE_GRENSER:
            assert krav_id not in dekket, (
                f"{krav_id} har en dekningsport, men står fortsatt"
                " som ubygd")
        else:
            assert krav_id in dekket, (
                f"{krav_id} er ute av UBYGDE_GRENSER uten en port som"
                " dekker grensen")


# ---------------------------------------------------------------------------
# KLYNGE 10 — HANDLINGENE (M-29, M-32, M-28, M-40).
#
# DEN DELTE DOMMEN: EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN
# ANGRES IKKE AV EN ROLLBACK.
#
# Se docs/KLYNGE10-FUNDAMENT.md.
# ---------------------------------------------------------------------------

KLYNGE10 = ("m29-v1", "m32-v1", "m28-v1", "m40-v1")

#: DEN TILBAKEHOLDTE FULLMAKTEN, PER MODUL.
#:
#: Alle fire vaktsetningene holder tilbake nøyaktig den fullmakten
#: modulen ser ut til å trenge — M-28 skal bestille transport, M-29
#: skal isolere kontoer, M-32 skal innberette skatt, M-40 skal avgjøre
#: noe om et menneske. Ingen av dem gjør det i v1.
#:
#: Navnene står her og ikke bare i dokumentet, fordi en dom som bare
#: står i et dokument kan landes rundt.
KLYNGE10_TILBAKEHOLDT = {
    "m29-v1": ("modulen_isolerte_konto", "modulen_roterte_hemmelighet"),
    "m32-v1": ("modulen_innberettet_skatt",),
    "m28-v1": ("modulen_bestilte_transport", "modulen_ombooket"),
    "m40-v1": ("beslutning_med_rettsvirkning", "individprofil_bygget"),
}

#: FUNNENE SOM STÅR I SETTET OG ALDRI KAN REISES.
#:
#: Formen er klynge 9s, og den har nå gjentatt seg i åtte moduler: et
#: sett som ikke navnga dem ville ikke sagt noe, og et sett som navnga
#: dem og kunne fylles ville sagt at vernet er en sveip.
KLYNGE10_UMULIGE = {
    "m29-v1": ("inngrep_uten_playbook", "fri_kommando_kjort"),
    "m32-v1": ("transaksjon_uten_jurisdiksjon",),
    "m28-v1": ("kolli_bestilt_to_ganger",),
    "m40-v1": ("puls_identifiserte_en_person",),
}

#: ARVEFORHOLDENE, MÅLT MOT MANIFESTET OG IKKE MOT DOKUMENTET.
#:
#: `lonnstaker` (M-39) er husets eneste ansattregister, og `mvasats`
#: (M-14) er tenantens egen — ikke en landpakke. M-28 arver M-32s
#: landregister fordi «farlig gods og toll følger LANDregler» er
#: nøyaktig det registeret M-32 bygger.
KLYNGE10_ARV = {
    "m28_transport": "m32_skatt",
    "m40_medarbeider": "m39_lonnsgrunnlag",
}


def test_klynge10_holder_tilbake_en_fullmakt_hver():
    """KLYNGENS DELTE DOM, MÅLT I GRENSENE.

    Det er ikke fire tilfeldig like vaktsetninger. Det er den samme
    setningen fire ganger: modulen er bygget for å handle, og v1
    handler ikke.

    En grense som mistet sin `modulen_*`-invariant ville sluppet en
    akseptport grønn på en modul som FAKTISK bestilte transporten,
    stengte kontoen eller innberettet skatten — mens
    fundamentdokumentet fortsatt sa at den ikke gjorde det.

    MUTASJONEN SOM DREPER DENNE: ta `modulen_isolerte_konto` ut av
    `m29-v1`.
    """
    from manifestskjema import KRAVGRENSER
    mangler = {}
    for krav_id, holdt in KLYNGE10_TILBAKEHOLDT.items():
        inv = set(KRAVGRENSER[krav_id]["invarianter"])
        savnet = [d for d in holdt if d not in inv]
        if savnet:
            mangler[krav_id] = savnet
    assert mangler == {}, (
        "klynge 10 holder tilbake fullmakten i fundamentet, men ikke"
        f" i grensen: {mangler}")


def test_klynge10_navngir_funnene_den_aldri_kan_reise():
    """AT DE STÅR DER OG ER UMULIGE ER BEVISET.

    `fri_kommando_kjort` kan aldri reises fordi ingen dør tar en
    kommandostreng. `puls_identifiserte_en_person` kan aldri reises
    fordi svaret ikke bærer noen personnøkkel. `kolli_bestilt_to_ganger`
    kan aldri reises fordi datamodellen gir én frigivelse per kolli.

    Et sett som ikke navnga dem ville ikke sagt noe. Et sett som
    navnga dem og kunne fylles ville sagt at vernet er en sveip.

    MUTASJONEN SOM DREPER DENNE: ta `fri_kommando_kjort` ut av
    `m29-v1`.
    """
    from manifestskjema import KRAVGRENSER
    mangler = {}
    for krav_id, umulige in KLYNGE10_UMULIGE.items():
        inv = set(KRAVGRENSER[krav_id]["invarianter"])
        savnet = [d for d in umulige if d not in inv]
        if savnet:
            mangler[krav_id] = savnet
    assert mangler == {}, (
        f"de umulige funnene mangler i grensen: {mangler}")


def test_klynge10_arver_framfor_aa_bygge_et_register_til():
    """ET ARVEFORHOLD SOM BARE STÅR I ET DOKUMENT ER IKKE ARVET.

    `lonnstaker` (M-39) er husets ENESTE register over mennesker som
    jobber i bedriften. Et fundament som leste katalogen og ikke basen
    ville bygget et ANDRE — og to registre over de samme menneskene
    gir to svar på «jobber hun her».

    Det er nøyaktig argumentet som ga M-7 og M-43 én delt
    opptakshjemmel, og porten er den samme: står arven bare i prosaen,
    kan modulen landes med sitt eget register uten at noe faller.

    MUTASJONEN SOM DREPER DENNE: ta `m39_lonnsgrunnlag` ut av M-40s
    `avhengigheter`.
    """
    import yaml
    mangler = {}
    for modul, arvet in KLYNGE10_ARV.items():
        sti = (SKJEMA.parents[1] / "modules" / modul
               / "manifest.yaml")
        assert sti.exists(), f"{modul} har intet manifest"
        d = yaml.safe_load(sti.read_text(encoding="utf-8"))
        if arvet not in (d.get("avhengigheter") or []):
            mangler[modul] = arvet
    assert mangler == {}, (
        "arveforholdet står i fundamentet, men ikke i manifestets"
        f" avhengigheter: {mangler}")
    # OG M-40 SKAL BÆRE INVARIANTEN SOM SIER DET, ikke bare pekeren:
    # en avhengighet forteller hva modulen LESER, invarianten hva den
    # ikke får LAGE.
    from manifestskjema import KRAVGRENSER
    assert ("modulen_bygget_eget_ansattregister"
            in KRAVGRENSER["m40-v1"]["invarianter"]), (
        "M-40 peker på M-39, men grensen forbyr ikke et eget register")


def test_klynge10_star_i_ubygde_til_modulene_er_bygget():
    """De fire er registrert FØR koden, og står her til de landes.

    EN GRENSE SOM ER UTE AV LISTA MÅ HA EN PORT SOM DEKKER DEN — det
    er hele bytteforholdet, og det er den samme porten som tømte lista
    for klynge 8 og klynge 9.
    """
    dekket = _grenser_med_dekningsport()
    for krav_id in KLYNGE10:
        if krav_id in UBYGDE_GRENSER:
            assert krav_id not in dekket, (
                f"{krav_id} har en dekningsport, men står fortsatt"
                " som ubygd")
        else:
            assert krav_id in dekket, (
                f"{krav_id} er ute av UBYGDE_GRENSER uten en port som"
                " dekker grensen")
