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
    # `m15-v1` STO HER TIL 5/9, `m33-v1` til samme kveld. Å fjerne et
    # navn herfra er en del av å bygge modulen, ikke et separat
    # opprydningsarbeid — og `test_de_ubygde_grensene_er_faktisk_ubygde`
    # er den som krever det. Den falt i det øyeblikket 128 landet, og
    # igjen da 130 landet, som den skal.
    "m36-v1",   # M-36 optimalisator, migrasjon 131
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
