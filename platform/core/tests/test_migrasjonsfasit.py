"""Kjørte migrasjoner er byte-immutable — målt i CI, ikke først i deploy.

23/8: en REN KOMMENTAR ble lagt inn i 056 etter at den var kjørt i prod.
CI bygger frisk base for hver kjøring og skriver checksummene på nytt,
så avviket var usynlig helt til `opp.sh` stoppet tjenestene og kjøreren
nektet («historikk er immutable») — prod sto nede i to minutter på en
kommentarlinje. Kjørerens vern er riktig og står; denne porten flytter
målingen dit den hører hjemme: FØR merge.

Fasiten (`migrasjons-fasit.json`) pinner sha256 for hver migrasjon som
er KJØRT i prod. En ny migrasjon legges til fasiten i SAMME commit som
den fødes (etter deploy er den kjørt); en endring i en pinnet fil er
rød her — dokumentasjon av senere vedtak hører til i issuer, PR-tråder
eller NYE filer, aldri i kjørt historikk.

Fasiten er APPEND-ONLY, og den påstanden måles mot `main` — ikke mot
fasiten i grenen som endrer den. Fil-mot-fasit alene er en port som
måler treet mot seg selv: endrer noen migrasjonen og pinnen i samme
commit, er alt grønt helt til deployet har stoppet tjenestene. En pin
som er merget er historikk på lik linje med bytene den peker på.

PROVENIENS — hvor pinnene kommer fra
------------------------------------
Fasiten er en backfill: den ble født med 57 pinner på én gang, ikke én
per migrasjon slik regelen over foreskriver for fremtiden. Da er
spørsmålet «hvilke bytes ble egentlig låst» ikke retorisk, og svaret
hører hjemme her og ikke bare i en PR-tråd.

* 001/002 er bundet til REVIEWEDE bytes, ikke til disk.
  `deploy/staging/migrasjon-bootstrap.py` holder `REVIEWEDE_CHECKSUMS`
  hentet med `git show 679ee9e:<sti> | sha256sum` fra PR-004-mergen, og
  `test_bootstrap_checksums_stemmer_med_filene` binder de konstantene
  til filene i treet. Sammen med porten under er kjeden lukket:
  fasit == disk == reviewet. De to kan ikke drifte fra hverandre stille.

* 003–057 er bytene på `main` slik de sto ved 9893cb0 (mergen rett før
  denne hotfixen), med 056 tilbakestilt til bytene fra #140 — altså de
  bytene prod faktisk kjørte, ikke #153-varianten med kommentaren.
  For 056 spesielt er ikke dette bare påstått: `KJORT_056` under binder
  pinnen maskinelt mot en hash regnet fra `git show 2aaca01:<sti>`
  (#140, kjøre-mergen), ikke bare mot disken i denne PR-en.

* Drift-revisjon på `git log origin/main` per migrasjonsfil: av 57
  pinnede filer har nøyaktig FIRE noen gang fått en andre commit —
  039 (#87), 041 (#102), 047 (#113) og 056 (#153). Resten er skrevet
  én gang og aldri rørt, så for de 53 er «bytes på main» og «bytes ved
  fødsel» det samme utsagnet. 056 er hendelsen denne porten finnes for.
  For 039/041/047 sier commit-meldingene at deployet FEILET eller traff
  kontrollene — altså at migrasjonen ikke var vellykket anvendt da den
  ble rettet, som er lovlig. Det er en indikasjon, ikke et bevis.

* IKKE avgjort i repoet: om noen av de fire faktisk rakk å bli
  registrert i prod før de ble rettet. Det står bare i
  `SELECT versjon, checksum FROM migrasjoner` i prod, som ingen test
  her kan lese. Eier verifiserer de fire mot prod ved neste deploy; se
  PR #171. Er en pin feil, er den feil på en fil som ikke har vært rørt
  siden — porten låser den fast, og en avvikende prod-checksum vil vise
  seg i kjøreren som før. Denne PR-en gjør ikke den risikoen større enn
  den var; den gjør den bare målbar.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

ROT = Path(__file__).resolve().parents[1]
GITROT = Path(__file__).resolve().parents[3]
FASITSTI = "platform/core/db/migrasjons-fasit.json"
FASIT = json.loads((ROT / "db/migrasjons-fasit.json").read_text(
    encoding="utf-8"))
KATALOG = ROT / "db/migrations"


HENDELSEN_056 = "056_m57_utsending.sql"

# Regnet 2026-08-23 av implementator, IKKE skrevet for hånd:
#   git show 2aaca01:platform/core/db/migrations/056_m57_utsending.sql \
#     | sha256sum
# 2aaca01 = "M-57 ATS: utsendingskjeden (CP1 — migrasjon 056 + SP-10)"
# (#140) — mergen som faktisk kjørte 056 i prod, FØR #153-kommentaren.
# Samme grep som REVIEWEDE_CHECKSUMS i migrasjon-bootstrap.py for
# 001/002: fasit==disk alene beviser bare intern konsistens; denne
# binder 056 i tillegg til kilde-commiten prod kjørte, uavhengig av om
# fasiten og disken skulle drifte sammen til noe annet.
KJORT_056 = "0603c65f996ff33ce24cfac9e7738f61be260ccc7d234849379a313abfdfd19d"


def _bytefeil(les):
    """Selve målingen, med fil-leseren som parameter.

    Grunnen til at den er utløst: negativtesten under må kunne spille av
    23/8-hendelsen gjennom NØYAKTIG denne løkken uten å skrive til treet.
    En negativtest som gjenskaper sammenligningen sin egen vei beviser at
    kopien virker, ikke at porten gjør det.
    """
    avvik = []
    for navn, ventet in FASIT.items():
        fil = KATALOG / navn
        if not fil.exists():
            avvik.append(f"{navn}: pinnet i fasiten, men borte fra treet")
            continue
        faktisk = hashlib.sha256(les(fil)).hexdigest()
        if faktisk != ventet:
            avvik.append(
                f"{navn}: {faktisk[:12]}… ≠ fasit {ventet[:12]}… — "
                "kjørt historikk er immutable; senere vedtak dokumenteres"
                " i issuer/nye filer, aldri her")
    return avvik


def test_kjorte_migrasjoner_er_byte_identiske_med_fasiten():
    assert not _bytefeil(Path.read_bytes), \
        "\n".join(_bytefeil(Path.read_bytes))


def test_kommentarlinje_etter_prod_felles_i_fasitporten():
    """Negativtesten: hendelsen porten ble laget for, spilt av.

    23/8 ble en REN KOMMENTAR lagt inn i 056 etter at den var kjørt i
    prod. Positivtesten over er grønn i dag uansett om porten måler noe
    som helst — det er først når mutasjonen gjør den rød at porten er
    bevist. Hendelsen spilles derfor av i minnet: 056 leses med den
    tilføyde kommentaren, resten av treet urørt.

    MUTASJONEN SOM DREPER DENNE: svekk `_bytefeil` — fjern
    sammenligningen, sammenlign noe annet enn bytene (tekst med
    normaliserte linjeskift, lengde, mtime), eller la 056-pinnen falle ut
    av fasiten. Da finner mutasjonen ingen avvik, og denne blir rød.
    """
    kommentaren = (b"\n-- AVGJORT (eier, 2026-08-23 i #153): pseudonymnokkel."
                   b"\n")

    def med_kommentaren(fil):
        byte = fil.read_bytes()
        return byte + kommentaren if fil.name == HENDELSEN_056 else byte

    avvik = _bytefeil(med_kommentaren)

    assert len(avvik) == 1, (
        "hendelsen skal treffe 056 og BARE 056 — porten fant "
        f"{len(avvik)} avvik: {avvik}")
    assert avvik[0].startswith(HENDELSEN_056), avvik[0]
    assert "immutable" in avvik[0], avvik[0]

    # …og treet slik det faktisk står er fortsatt grønt: mutasjonen levde
    # i minnet, den skrev ikke til disk.
    assert not _bytefeil(Path.read_bytes)


def test_056_matcher_prodkjoringen_i_140():
    """056 er ikke bare selvkonsistent (fasit==disk); den er bundet til
    kilde-commiten prod faktisk kjørte — ikke bare en fasit som tilfeldigvis
    stemmer med disken den ble regnet fra i samme commit.

    Uten denne kunne en feil revert (f.eks. tilbake til en ANNEN historisk
    variant enn den #140 faktisk kjørte) og en matchende, nyregnet fasit-pin
    gitt grønn CI mens prod fortsatt sto med andre bytes — hotfixens
    egentlige mål ville da vært umålt.

    MUTASJONEN SOM DREPER DENNE: `KJORT_056` endres til noe annet enn
    prod-bytene, eller 056 (fil/fasit) driftes bort fra dem igjen.
    """
    assert FASIT[HENDELSEN_056] == KJORT_056, (
        f"fasit-pinnen for {HENDELSEN_056} er ikke lenger bytene #140"
        " faktisk kjørte i prod")
    faktisk = hashlib.sha256((KATALOG / HENDELSEN_056).read_bytes()).hexdigest()
    assert faktisk == KJORT_056, (
        f"{HENDELSEN_056} på disk er ikke lenger bytene #140 faktisk"
        " kjørte i prod")


def test_fasiten_dekker_alle_migrasjonene_i_treet():
    """Motsatt retning: HVER migrasjon i katalogen må stå i fasiten — et
    hull er en fil noen kan redigere usett.

    Grensen leses fra KATALOGEN, ikke fra fasiten. En fasit som er sin
    egen øvre grense kan senkes stille: slett siste linje mens du endrer
    den migrasjonen, så slutter porten over å se filen OG grensen faller
    med den — alle tre testene blir grønne selv om en pinnet, prod-kjørt
    migrasjon er fjernet og endret. Katalogen kan ikke synke når fasiten
    gjør det: å fjerne pinnen fjerner ikke filen.
    """
    upinnet = [fil.name for fil in sorted(KATALOG.glob("*.sql"))
               if fil.name not in FASIT]
    assert not upinnet, (
        "migrasjoner i treet uten pin i fasiten: "
        + ", ".join(upinnet)
        + " — hver migrasjon pinnes i samme commit som den fødes; en"
          " upinnet fil kan endres etter at deploy har kjørt den, og"
          " avviket dukker først opp når tjenestene alt er stoppet")


def _git(*argv):
    return subprocess.run(["git", "-C", str(GITROT), *argv],
                          capture_output=True)


def _basiscommit() -> str:
    """`main` sin sha, alltid friskt hentet inn i den grunne utsjekkingen.

    Samme grep som proveniensporten i `test_aksept_projeksjon.py`:
    `actions/checkout` henter `refs/pull/<nr>/merge` på dybde 1, så
    basisgrenen er ikke nødvendigvis i objektbasen — en `--depth=1`-henting
    av `main` koster ett objektsett og utvider ikke historikken ellers.

    Henter FØR den ser etter en lokal ref, ikke bare hvis den mangler: et
    miljø som allerede har en `origin/main` liggende (f.eks. et
    gjenbrukt arbeidstre) kan ha en FORELDET ref, og en tidlig retur uten
    fetch ville latt append-only-porten måle mot den utdaterte verdien i
    stedet for den ferske — stille, uten at noen mutasjon var involvert.

    INGEN fallback til `FETCH_HEAD`: i en PR-utsjekking i GHA er
    `FETCH_HEAD` som oftest PR-ens egen merge-ref, ikke `main` — feiler
    fetchen (f.eks. offline), ville et fall til `FETCH_HEAD` fått
    `_basisfasit` til å lese PR-ens EGEN fasit som «basis», og
    append-only-porten ville vært grønn mot seg selv. Feiler fetchen,
    forsøkes `origin/main` likevel én gang (kan alt være tilstede fra en
    tidligere fetch i samme miljø); lykkes ikke det heller, er basisen
    uløst, og porten under sier fra — den passerer ikke stille.
    """
    _git("fetch", "--quiet", "--depth=1", "origin", "main")
    r = _git("rev-parse", "--verify", "--quiet", "origin/main^{commit}")
    return r.stdout.decode().strip() if r.returncode == 0 else ""


def _basisfasit(commit: str) -> dict:
    """Fasiten slik den står på basisgrenen — utenfor denne grenens rekkevidde.

    Tom dict betyr «filen finnes ikke på main ennå», som er sant nøyaktig
    i PR-en som føder den. Da er hver pin her ny, og porten under er
    riktignok stille — men den er ikke blind: den måler fra og med neste
    endring, som er den første som kan drifte.

    Fravær og LESEFEIL er ikke samme ting. `cat-file -e` avgjør bare
    OM stien finnes i treet på `commit`; finnes den ikke, er `{}` riktig
    (fasiten er nyfødt her). Finnes den, men selve blob-lesingen feiler
    (skadet objekt, avkuttet fetch), er det en uløst basis — IKKE en tom
    en — og porten skal si fra, ikke bli vacuous-grønn for alltid.
    """
    finnes = _git("cat-file", "-e", f"{commit}:{FASITSTI}")
    if finnes.returncode != 0:
        return {}
    blob = _git("cat-file", "blob", f"{commit}:{FASITSTI}")
    assert blob.returncode == 0, (
        f"{FASITSTI!r} finnes på {commit[:12]}… men lot seg ikke lese —"
        " basisen er da uløst, ikke tom; append-only-porten kan ikke"
        " passere stille på en lesefeil")
    return json.loads(blob.stdout.decode("utf-8"))


def test_fasitstien_resolver_mot_HEAD():
    """FASITSTI må faktisk peke på DENNE fasiten, sett fra repo-roten.

    `_basisfasit` fail-åpner til `{}` for ENHVER `cat-file`-feil — også en
    feilstavet FASITSTI. En tom basis gjør `_regresjon` vacuous
    (løkken går over `basis.items()`), så append-only-porten ville da
    vært stille for alltid uansett hva som skjer med fasiten videre — en
    stille variant av nøyaktig samme feilklasse som Codex P1 (#154) i
    denne fila. Testen sjekker konstanten direkte og statisk, uten å gå
    via fail-open-stien i porten selv.

    MUTASJONEN SOM DREPER DENNE: FASITSTI endres til en sti som ikke
    lenger peker på denne fila.
    """
    blob = _git("cat-file", "blob", f"HEAD:{FASITSTI}")
    assert blob.returncode == 0, (
        f"FASITSTI={FASITSTI!r} løser ikke mot HEAD i git-treet — "
        "`_basisfasit` vil fail-åpne til {} og append-only-porten blir"
        " stille for alltid")
    assert json.loads(blob.stdout.decode("utf-8")) == FASIT, (
        "fasiten lest via `git cat-file` på HEAD stemmer ikke med FASIT"
        " lest fra disk — FASITSTI peker et annet sted enn filen dette"
        " testmodulet faktisk bruker")


def _regresjon(basis, naa):
    """Append-only-målingen, med begge sider som parameter.

    Utløst av samme grunn som `_bytefeil`: negativtesten under må kunne
    spille av «bidragsyter endrer migrasjonen OG pinnen i takt» gjennom
    NØYAKTIG denne løkken, uten å skrive til treet eller til `main`.
    """
    avvik = []
    for navn, pinnet in basis.items():
        if navn not in naa:
            avvik.append(
                f"{navn}: pinnet på main, men borte fra fasiten her — "
                "en fjernet pin er en fil som kan endres usett")
        elif naa[navn] != pinnet:
            avvik.append(
                f"{navn}: pinnen endret {pinnet[:12]}… → {naa[navn][:12]}… "
                "— fasiten er APPEND-ONLY; en kjørt migrasjon får ikke ny "
                "pin fordi filen fikk nye bytes, uansett om de to endres "
                "i samme commit")
    return avvik


def test_fasiten_er_append_only_mot_basisgrenen():
    """Basisen porten måler mot er `main`, ikke fasiten i denne grenen.

    Codex P1: portene over sammenligner fil mot fasit, og BEGGE ligger i
    arbeidstreet. En bidragsyter som endrer en kjørt migrasjon og regner
    ut den nye hashen inn i fasiten i samme commit får alle tre grønne —
    og deployet oppdager avviket først når tjenestene alt er stoppet,
    altså nøyaktig 23/8 på nytt. En fasit som er sin egen fasit er ingen
    fasit; verdien det måles mot må ligge et sted denne grenen ikke kan
    skrive.

    Den er `main`. Pinner får legges TIL (hver ny migrasjon fødes med
    sin), men en pin som alt er merget er historikk: den endres ikke, og
    den fjernes ikke.

    Porten HOPPER IKKE (samme vedtak som proveniensporten i
    `test_aksept_projeksjon.py`, Codex P1 #154): er `main` uleselig, er
    påstanden umålt, og en umålt port er rød.
    """
    commit = _basiscommit()
    assert commit, (
        "basisgrenen `main` er verken i denne utsjekkingens historikk"
        " eller hentbar fra `origin` — append-only kan da ikke måles."
        " Kjør fra en utsjekking med nett eller full historikk"
        " (`fetch-depth: 0`)")
    avvik = _regresjon(_basisfasit(commit), FASIT)
    assert not avvik, "\n".join(avvik)


def test_pin_endret_i_takt_med_filen_felles_mot_basisgrenen():
    """Negativtesten for porten over: hendelsen den finnes for, spilt av
    gjennom SAMME WIRING som porten — ikke bare `_regresjon` for seg selv.

    Cursor 19:13 på 77a7a7d: en tidligere versjon kalte `_regresjon(FASIT,
    i_takt)` direkte, uten å gå via `_basiscommit`/`_basisfasit`. Da kunne
    WIRINGEN mellom dem svekkes usett — `_basisfasit` gjort til å alltid
    returnere `{}` (fail-open), eller porten over gjort til å sammenligne
    fasiten mot seg selv (`_basisfasit(commit)` byttet med `FASIT`) — uten
    at denne negativtesten merket noe, fordi den aldri rørte den koden.
    En negativtest som gjenskaper sammenligningen sin egen vei beviser at
    kopien virker, ikke at porten gjør det.

    Her kalles derfor de VIRKELIGE `_basiscommit()` og `_basisfasit()`
    uendret; kun `_git` (den ene primitiven begge bygger på, samme
    injeksjonspunkt som `_bytefeil(les)` over) er byttet med en falsk git
    som svarer «main har den UENDREDE fasiten». Angrepet er: 23/8-
    kommentaren legges i 056 OG pinnen regnes om i takt, slik en
    bidragsyter ville gjort for å få de tre andre portene grønne.

    MUTASJONEN SOM DREPER DENNE: svekk `_regresjon` (som før) — ELLER la
    `_basisfasit`/`_basiscommit` fail-åpne til `{}`, hoppe over
    `cat-file`, eller på annen måte slutte å bruke svaret fra `_git`.
    Begge veier går nå gjennom nøyaktig denne løkken.
    """
    global _git
    kommentaren = (b"\n-- AVGJORT (eier, 2026-08-23 i #153): pseudonymnokkel."
                   b"\n")
    endret = hashlib.sha256(
        (KATALOG / HENDELSEN_056).read_bytes() + kommentaren).hexdigest()

    # "main" slik den var FØR angrepet: den uendrede fasiten.
    i_takt = {**FASIT, HENDELSEN_056: endret}
    basis_json = json.dumps(FASIT).encode("utf-8")
    ekte_git = _git

    class _Svar:
        def __init__(self, returncode, stdout=b""):
            self.returncode = returncode
            self.stdout = stdout

    def falsk_git(*argv):
        if argv[0] == "fetch":
            # `_basiscommit()` sitt eget fetch-kall — svaret brukes ikke,
            # bare rev-parse-et etterpå. Simulerer et miljø der nettet
            # virker; en negativtest for offline-stien dekkes ikke her.
            return _Svar(0)
        if argv[:2] == ("rev-parse", "--verify"):
            return _Svar(0, b"f" * 40 + b"\n")
        if argv[:2] == ("cat-file", "-e"):
            return _Svar(0)
        if argv[:2] == ("cat-file", "blob"):
            return _Svar(0, basis_json)
        raise AssertionError(f"uventet git-kall i negativtesten: {argv}")

    _git = falsk_git
    try:
        commit = _basiscommit()
        basis = _basisfasit(commit)
        avvik = _regresjon(basis, i_takt)
    finally:
        _git = ekte_git

    assert len(avvik) == 1, (
        "angrepet skal treffe 056 og BARE 056 — porten fant "
        f"{len(avvik)} avvik: {avvik}")
    assert avvik[0].startswith(HENDELSEN_056), avvik[0]
    assert "APPEND-ONLY" in avvik[0], avvik[0]

    # …og den andre halvdelen: pinnen slettet i stedet for endret.
    uten = {navn: v for navn, v in FASIT.items() if navn != HENDELSEN_056}
    slettet = _regresjon(FASIT, uten)
    assert len(slettet) == 1 and slettet[0].startswith(HENDELSEN_056), slettet

    # Ny migrasjon er derimot LOVLIG: fasiten vokser, den skrives ikke om.
    assert not _regresjon(FASIT, {**FASIT, "058_ny.sql": "0" * 64})


def test_fasiten_er_regnet_ikke_skrevet():
    """Fasitverdiene skal LIGNE sha256 — en hånd som skriver «TODO» inn
    i fasiten skal felles her, ikke i deploy."""
    for navn, verdi in FASIT.items():
        assert isinstance(verdi, str) and len(verdi) == 64 and \
            all(c in "0123456789abcdef" for c in verdi), navn
