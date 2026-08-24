"""A-maskinen (K2-vedtaket på #152): aksepten gjelder generasjonen den
målte, og identiteten er den KANONISKE PROJEKSJONEN — parset YAML minus
katalogaksene (`status`, `driftstilstand`).

Portene her er den tillatte-delta-porten etter aksept: for hver modul
med en skrevet aksepthendelse skal HEAD-manifestets projeksjon være
NØYAKTIG den aksepten målte. Katalogaksene og kommentarer kan flippes og
skrives (det er flippens definisjon: en avlesning); én strukturell
endring — `kjerne`, `avhengigheter`, `id`, et sjekklistepunkt — er en ny
identitet som krever ny aksept.

Hver port har sin motpart: invariansen måles MED en mutasjonskontroll i
samme test, så en projeksjon som sluttet å se, feller seg selv.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from manifestskjema import (AKSEPTERTE_GENERASJONER, KATALOGAKSER,
                            kanonisk_projeksjon)

ROT = Path(__file__).resolve().parents[3]


def _les(mod: str) -> str:
    return (ROT / AKSEPTERTE_GENERASJONER[mod]["manifest"]).read_text(
        encoding="utf-8")


def test_projeksjonen_er_blind_for_aksene_og_kommentarer():
    """Invariansen OG dens grense, i samme test: katalogaksene og
    kommentarer flytter ikke projeksjonen; en strukturell endring gjør
    det — for HVER av aksene og for en representativ strukturendring."""
    import yaml
    for mod in AKSEPTERTE_GENERASJONER:
        tekst = _les(mod)
        basis = kanonisk_projeksjon(tekst)
        data = yaml.safe_load(tekst)
        # Aksene: enhver verdi, samme projeksjon.
        flippet = dict(data, status="aktiv", driftstilstand="produksjon")
        assert kanonisk_projeksjon(yaml.safe_dump(flippet,
                                                  allow_unicode=True)) \
            == basis, f"{mod}: katalogaksene flyttet projeksjonen"
        # Kommentarer/formatering: en re-dump uten kommentarer er samme
        # projeksjon som originalen med alle sine.
        assert kanonisk_projeksjon(yaml.safe_dump(data,
                                                  allow_unicode=True)) \
            == basis, f"{mod}: formatering flyttet projeksjonen"
        # Mutasjonskontrollene: struktur FLYTTER den.
        # (`+ [x]`, aldri `[]`: m02s avhengighetsliste ER tom, og en
        # mutasjon som treffer eksisterende verdi er ingen mutasjon.)
        for mutert in (dict(data, kjerne="et/annet/sted"),
                       dict(data, avhengigheter=list(data["avhengigheter"])
                            + ["x_finnes_ikke"]),
                       dict(data, id=data["id"] + "x")):
            assert kanonisk_projeksjon(yaml.safe_dump(
                mutert, allow_unicode=True)) != basis, \
                f"{mod}: en strukturell endring var usynlig"
        # …og et sjekklistepunkt er STRUKTUR (evidensbindingen er del av
        # identiteten aksepten målte).
        sjekk = {**data, "staging_sjekkliste": {
            **data["staging_sjekkliste"],
            "rollback_testet": {"status": "nei"}}}
        assert kanonisk_projeksjon(yaml.safe_dump(
            sjekk, allow_unicode=True)) != basis, \
            f"{mod}: sjekklisten falt ut av identiteten"


def test_head_baerer_de_aksepterte_generasjonene():
    """Selve delta-porten: HEAD-manifestet for hver akseptert modul har
    projeksjonen aksepten målte. Rødner denne, har noen endret modulens
    identitet ETTER aksept — det er en ny aksept, ikke en commit."""
    for mod, info in AKSEPTERTE_GENERASJONER.items():
        assert kanonisk_projeksjon(_les(mod)) == info["projeksjon"], (
            f"{mod}: manifestets projeksjon er ikke generasjonen "
            f"aksepten på {info['commit'][:12]}… målte — en strukturell "
            "endring etter aksept krever NY aksept (A-vedtaket, #152)")


def _git_blob(commit: str, sti: str) -> str | None:
    r = subprocess.run(["git", "-C", str(ROT), "cat-file", "blob",
                        f"{commit}:{sti}"], capture_output=True)
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def _hent_commit(commit: str) -> None:
    """Utdyper den grunne utsjekkingen med NØYAKTIG denne ene commiten.

    `actions/checkout` henter `refs/pull/<nr>/merge` på dybde 1, så
    akseptcommiten er ikke i objektbasen — men den er nåbar fra `main`,
    og en henting av selve sha-en koster ett objektsett og
    utvider ikke historikken ellers. Feiler den (offline), sier porten
    under fra; den passerer ikke stille.
    """
    subprocess.run(["git", "-C", str(ROT), "fetch", "--quiet",
                    "origin", commit], capture_output=True)


def test_pinnene_er_akseptcommitens_egne_projeksjoner():
    """Proveniensporten: pinnene i AKSEPTERTE_GENERASJONER er REGNET av
    akseptcommitens innsjekkede bytes, ikke skrevet etter hukommelsen.

    Porten HOPPER IKKE (Codex P1, #154). Den gjorde det i grunne
    utsjekkinger — og siden CI kjører suiten en gang til bak porten
    «ingen tester ble hoppet over», felte hoppet hver eneste kjøring.
    Verre var det at et hopp uansett ville gjort proveniensen umålt
    NØYAKTIG der den betyr noe: en pin skrevet etter hukommelsen slipper
    gjennom en CI som aldri leste akseptcommiten.

    Mangler commiten, hentes den derfor — selve sha-en, som
    er nåbar fra `main`. Er den fortsatt borte, er porten RØD: en pin som
    ikke kan måles mot innsjekkede bytes er ikke en bevist pin."""
    for mod, info in AKSEPTERTE_GENERASJONER.items():
        blob = _git_blob(info["commit"], info["manifest"])
        if blob is None:
            _hent_commit(info["commit"])
            blob = _git_blob(info["commit"], info["manifest"])
        assert blob is not None, (
            f"{mod}: akseptcommiten {info['commit'][:12]}… er verken i"
            " denne utsjekkingens historikk eller hentbar fra `origin`."
            " Pinnen kan da ikke måles mot innsjekkede bytes — kjør fra"
            " en utsjekking med nett eller full historikk"
            " (`fetch-depth: 0`)")
        assert kanonisk_projeksjon(blob) == info["projeksjon"], (
            f"{mod}: pinnen stemmer ikke med akseptcommitens manifest")


def test_aksene_er_de_to_og_bare_de_to():
    """Projeksjonens unntak er PINNET: vokser KATALOGAKSER, har noen
    flyttet mer av manifestet ut av den aksepterte identiteten — det er
    en arkitekturendring (C-grenen fra #152), ikke en vedlikeholdslinje."""
    assert KATALOGAKSER == ("status", "driftstilstand")
