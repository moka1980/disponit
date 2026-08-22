"""Modulkatalogen på forsiden: fersk, komplett og formriktig.

Katalogen er generert fra `docs/spesifikasjon/disponit-prototype-v9.html` av
`tools/gen_katalog.py`. En generator uten en port i CI er bare en vennlig
anbefaling: den dagen noen redigerer `katalog.js` for hånd, eller endrer
spesifikasjonen uten å kjøre generatoren, driver de to kildene fra hverandre —
og forsiden viser da et produktomfang ingen har bestemt.

Testene her er derfor ti porter (Codex P2 på PR #43, #99 og #118):
  1. KILDE     — generatoren leser sannhetskilden, ikke arkivet i `prototype/`.
  2. FERSKHET  — regenerering i en temp-rot gir NØYAKTIG det som ligger i repoet.
  3. OMDØPING  — nytt navn i kilden stopper genereringen til oversettelsen er
                 vurdert på nytt, så nb og en ikke kan drive fra hverandre.
  4. FORM      — 57 moduler, elleve områder, faser 1–4, alle representert.
  5. TEKST     — hvert modul- og områdenavn har nøkkel i BEGGE locale-sett.
  6. MERKEVARE — sannhetskilden bærer produktnavnet resten av repoet bruker.
  7. FASEORDEN — ingen modul avhenger av en modul i en senere fase, så den
                 erklærte utrullingsrekkefølgen faktisk kan følges.
  8. PEKERE    — spesifikasjonsmappa inneholder nøyaktig én utgave, og ingen
                 fil i repoet henviser til en annen; historikken i docs/pr/ og
                 docs/beslutninger/ og arkivet i prototype/ er med rette
                 unntatt.
  9. KLASSER   — `kl` og `rev` i katalogen er verdier modulregisteret godtar,
                 målt i den MIGRERTE BASEN: vilkårene slik de står der nå,
                 og basens egen dom over hver verdi som motprøve.
 10. IDENTER   — også PROSAEN rundt postene: en identifikator skrevet i
                 maskinform må finnes i registeret slik det ER NÅ, eller som en
                 fil, så forklaringen ikke kan finne opp klasser posten ikke
                 har — og ikke leve videre på en verdi registeret har sluttet
                 med. Prosa er dokumentteksten og modulpostenes egne felt;
                 prototypens UI-kode er kode, ikke påstand.

TO LESNINGER, INGEN SKANNERE. Katalogen står i JavaScript og registeret i SQL,
og porten leste begge med håndskrevne skannere i Python. Nitten review-runder
på PR #118 var nitten språkformer skannerne ikke hadde — og en skanner som
ikke kjenner en form gjør ikke noe høylytt: den leser noe annet enn den som
skal kjøre filen, og sier ingenting. Eier avgjorde saken 20/8: bytt lesning,
ikke legg til former.

  * KATALOGEN leses av `tools/les_katalog.mjs` — en JavaScript-motor, delt med
    generatoren, så de to ikke kan lese hver sin katalog.
  * REGISTERET spørres der det BOR (SP-13): gjeldende enum leses av basens
    egen `pg_get_constraintdef` i den migrerte testbasen, og dommen over
    hver verdi måles i tillegg med en INSERT-probe mot en kopi av vilkårene
    — aldri regnet fram av en simulator over migrasjonshistorikken. En
    simulator måler sin egen fullstendighet, ikke virkeligheten: #118s
    seks siste funn gjaldt migrasjoner som ikke fantes. `pglast` —
    libpg_query, PostgreSQLs egen grammatikk — leser bare det som ER
    syntaks: vilkårsdefinisjonen basen selv skriver ut, og strengverdiene
    migrasjonsfilene skriver.

Begge er FAIL-CLOSED. Mangler node eller pglast, er porten rød — aldri hoppet
over. En port som hopper over seg selv er grønn på noe ingen har lest.
"""
import functools
import os
import itertools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest

ROT = Path(__file__).resolve().parents[3]
GENERATOR = ROT / "tools" / "gen_katalog.py"
KATALOG_JS = ROT / "platform" / "core" / "ui" / "static" / "js" / "katalog.js"
# Sannhetskilden slik README.md og docs/STRUKTUR.md utpeker den. Stien står
# som en LITERAL her, ikke importert fra generatoren: en port som henter
# kildestien fra det den skal vokte, godkjenner enhver sti generatoren måtte
# bytte til.
KILDE_REL = ("docs", "spesifikasjon", "disponit-prototype-v9.html")
KILDE = ROT.joinpath(*KILDE_REL)
# Mappa instruksene i README-arbeidsflyt.md og RUTINER.md peker på i stedet for
# å bake versjonsnummeret inn i teksten. Den er avledet av KILDE_REL, ikke
# skrevet av: to literaler for samme mappe ville kunnet drive fra hverandre.
SPEKMAPPE = "/".join(KILDE_REL[:-1]) + "/"
ARKIV = ROT / "prototype"
LOCALER = {s: ROT / "locales" / f"{s}.json" for s in ("nb", "en")}

MODULER = 57
OMRADER = 11
FASER = {1, 2, 3, 4}


def _katalog_js() -> tuple[list[dict], list[dict]]:
    """(KATALOG, OMRADER) lest ut av den genererte JS-fila.

    Fila er data i JS-syntaks; her leses den med regex i stedet for en
    JS-motor, slik at porten ikke trenger node for å kjøre.
    """
    tekst = KATALOG_JS.read_text(encoding="utf-8")
    katalog = [
        {"n": int(n), "omrade": o, "fase": int(f)}
        for n, o, f in re.findall(
            r"\{\s*n:\s*(\d+),\s*omrade:\s*\"([^\"]+)\",\s*fase:\s*(\d+)\s*\}",
            tekst)
    ]
    omrader = [
        {"id": i, "moduler": json.loads(m)}
        for i, m in re.findall(
            r"\{\s*id:\s*\"([^\"]+)\",\s*moduler:\s*(\[[^\]]*\])\s*\}", tekst)
    ]
    return katalog, omrader


def test_generatoren_leser_sannhetskilden():
    """Kilden skal være spesifikasjonen, ikke arkivet (Codex P2 på PR #43).

    Generatoren leste `prototype/AI-bedriftsagent-prototype-v7.html` — v7.0 —
    mens `README.md` peker på spesifikasjonen i `docs/spesifikasjon/` som
    sannhetskilden og `docs/STRUKTUR.md` kaller `prototype/` et historisk arkiv
    som aldri endres. De to filene ga identisk katalog den gangen, så
    ferskhetsporten under ville stått grønn uansett: den måler at
    `katalog.js` stemmer med det generatoren leser, ikke at generatoren leser
    riktig fil. Derfor denne, som er den eneste som fanger at kilden peker feil.
    """
    kilde = "/".join(KILDE_REL)
    assert KILDE.exists(), f"sannhetskilden mangler: {kilde}"
    tekst = GENERATOR.read_text(encoding="utf-8")
    assert kilde in tekst, (
        f"generatoren nevner ikke sannhetskilden {kilde}")
    # Arkivet skal ikke være INNDATA. Det kan nevnes i prosa (kommentaren som
    # forklarer hvorfor kilden ble byttet), men ingen filsti dit skal bygges.
    for arkivfil in sorted(ARKIV.glob("*.html")):
        assert f'"{arkivfil.name}"' not in tekst, (
            f"generatoren bygger fortsatt en sti til arkivet: {arkivfil.name}")


def _temprot(tmp_path: Path) -> Path:
    """Kopi av det generatoren leser og skriver, utenfor repoet.

    Generatoren kjøres alltid mot en KOPI: en test som «verifiserer» ved å
    oppdatere fila den sjekker, kan ikke feile.
    """
    (tmp_path / "docs" / "spesifikasjon").mkdir(parents=True)
    (tmp_path / "locales").mkdir()
    (tmp_path / "platform/core/ui/static/js").mkdir(parents=True)
    shutil.copy2(KILDE, tmp_path.joinpath(*KILDE_REL))
    for sprak, sti in LOCALER.items():
        shutil.copy2(sti, tmp_path / "locales" / f"{sprak}.json")
    return tmp_path


def test_katalogen_er_fersk(tmp_path):
    """Regenerering skal gi byte-identisk resultat.

    Uten denne porten kunne `katalog.js` vært håndredigert, eller
    spesifikasjonen endret uten en ny kjøring, og ingenting ville sagt fra.
    """
    _temprot(tmp_path)
    r = subprocess.run([sys.executable, str(GENERATOR), str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

    ny = (tmp_path / "platform/core/ui/static/js/katalog.js").read_text(encoding="utf-8")
    assert ny == KATALOG_JS.read_text(encoding="utf-8"), (
        "katalog.js er ikke fersk — kjør tools/gen_katalog.py")
    for sprak, sti in LOCALER.items():
        forventet = json.loads((tmp_path / "locales" / f"{sprak}.json")
                               .read_text(encoding="utf-8"))
        faktisk = json.loads(sti.read_text(encoding="utf-8"))
        nokler = {k: v for k, v in forventet.items()
                  if k.startswith(("site.katalog.m", "site.omrade."))}
        for k, v in nokler.items():
            assert faktisk.get(k) == v, f"{sprak}: {k} er ikke fersk"


def _med_felt_i_m57(tmp_path: Path, felt: str) -> subprocess.CompletedProcess:
    """Kjør generatoren mot en kopi der M-57 har fått `felt` satt inn."""
    rot = _temprot(tmp_path)
    spek = rot.joinpath(*KILDE_REL)
    tekst = spek.read_text(encoding="utf-8")
    anker = '"kl":"krever_outbox"'
    assert anker in tekst, "fant ikke ankeret i M-57 — kilden har endret form"
    spek.write_text(tekst.replace(anker, felt + "," + anker, 1),
                    encoding="utf-8")
    return subprocess.run([sys.executable, str(GENERATOR), str(rot)],
                          capture_output=True, text=True)


# Egenskapen `status` skrevet på tolv måter. Alle gir nettleseren den helt
# alminnelige egenskapen `status` — og det er nettopp poenget: leseren er en
# JavaScript-motor, så skrivemåten er ikke lenger et spørsmål den kan svare
# feil på. Andre kolonne er hva feilmeldingen må si.
_STATUSFORMER = [
    ('"status":"planlagt"', "status"),
    ("['status']:'planlagt'", "status"),
    ('["sta" + "tus"]:"planlagt"', "status"),
    (r"['\x73tatus']:'planlagt'", "status"),
    (r'["\u0073tatus"]:"planlagt"', "status"),
    (r'"\x73tatus":"planlagt"', "status"),
    (r'\u0073tatus:"planlagt"', "status"),
    ('...{status:"planlagt"}', "status"),
    ('status(){return "planlagt"}', "status"),
    ('get status(){return "planlagt"}', "status"),
    ('[/]/.test("") ? "x" : "status"]:"planlagt"', "status"),
    ('x:/["\']/, "status":"planlagt"', "status"),
    # To former navngir noe som ikke finnes: forkortelsen `status` og den
    # beregnede nøkkelen `[nokkel]` leser begge en variabel ingen har erklært.
    # Nettleseren kaster `ReferenceError` der, og da er det ingen katalog å
    # tegne i det hele tatt — siden er død. Leseren sier det samme, og det er
    # høyere enn det gamle svaret: generatoren kastet feltet i stillhet.
    ("status", "fant ingen modulkatalog"),
    ("[nokkel]:'planlagt'", "fant ingen modulkatalog"),
]


@pytest.mark.parametrize("felt,i_meldingen", _STATUSFORMER)
def test_statusforbudet_ser_alle_skrivemaater(tmp_path, felt, i_meldingen):
    """En tilstandsakse er forbudt uansett HVORDAN egenskapen er skrevet.

    Forbudet leste feltnavn som navn eller fnuttstreng og gikk videre på alt
    annet. Det er åpent i feil ende, og Codex fant formene én for én over tre
    runder på #118: `['status']:` (ellevte), `['\\x73tatus']:` (tolvte), og i
    trettende runde `\\u0073tatus:`, spredning, forkortelse, metode, accessor
    og en beregnet nøkkel med en `]` inne i et mønster. Alle gir nettleseren
    den helt alminnelige egenskapen `status`: den frittstående siden ville
    tegnet den, mens generatoren kastet den stille, og da lyver kilden.

    Svaret var å AVVISE hver form generatoren ikke kunne lese, og lista over
    dem vokste med én for hver runde. Nå leses katalogen av `les_katalog.mjs`,
    altså av en JavaScript-motor: skrivemåten forsvinner i lesningen, og
    egenskapen HETER `status` når forbudet spør. Derfor krever prøvene her at
    meldingen navngir AKSEN og ikke formen — formen er ikke lenger noe
    generatoren har en mening om.

    Mutasjonen står i en KOPI av kilden — en port som retter fila den måler,
    kan ikke feile.
    """
    r = _med_felt_i_m57(tmp_path, felt)
    assert r.returncode != 0, (
        f"generatoren godtok «{felt}» i modulposten")
    melding = r.stderr + r.stdout
    assert i_meldingen in melding, (
        f"feilmeldingen sier ikke hva som er galt: {melding}")


def test_en_doblet_egenskap_leses_som_nettleseren_leser_den(tmp_path):
    """`{…, p:3, p:4}` er fase 4 — for generatoren som for nettleseren.

    Doblingen var et STOPP før (Codex P2 på #118, femtende runde), og med god
    grunn: `POST_RE` hentet den FØRSTE verdien mens nettleseren bruker den
    siste. Den frittstående siden ville tegnet M-57 i fase 4 mens `katalog.js`
    sa fase 3, og ferskhetsporten hadde vært grønn hele veien — den måler
    regenerering mot sitt eget utdata, og begge sider leste da den samme
    første verdien.

    Det avviket kan ikke oppstå lenger. `les_katalog.mjs` ER en JavaScript-
    motor, så generatoren og nettleseren leser ikke to verdier de kan velge
    ulikt mellom; de leser den samme. Da er doblingen slurv og ikke en
    tvetydighet, og porten måler det som faktisk sto på spill: at katalogen
    får verdien siden viser. Går denne i stykker, har lesningen sluttet å være
    nettleserens.
    """
    rot = _temprot(tmp_path)
    spek = rot.joinpath(*KILDE_REL)
    tekst = spek.read_text(encoding="utf-8")
    anker = '"kl":"krever_outbox"'
    assert anker in tekst, "fant ikke ankeret i M-57 — kilden har endret form"
    spek.write_text(tekst.replace(anker, '"p":4,' + anker, 1), encoding="utf-8")
    r = subprocess.run([sys.executable, str(GENERATOR), str(rot)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout
    katalog = (rot / "platform/core/ui/static/js/katalog.js").read_text(
        encoding="utf-8")
    assert "{ n: 57, omrade: \"samarbeid_og_hr\", fase: 4 }" in katalog, (
        "generatoren leste ikke den siste verdien, slik nettleseren gjør")


def test_statusforbudet_tar_ikke_en_verdiliste_for_en_nokkel(tmp_path):
    """En klamme i VERDI-posisjon er ikke en nøkkel.

    `dep: ['M-6']` og `flow: [...]` står i hver eneste modulpost. Leste
    nøkkellesningen dem som beregnede nøkler, ville generatoren stoppet på en
    kilde som er helt i orden. Prøven her er den samme kilden uendret: den skal
    gå gjennom.
    """
    rot = _temprot(tmp_path)
    r = subprocess.run([sys.executable, str(GENERATOR), str(rot)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr + r.stdout


def _med_uikode(tmp_path: Path, linje: str) -> subprocess.CompletedProcess:
    """Kjør generatoren mot en kopi der `linje` er satt inn i UI-koden.

    Ankeret står UTENFOR modulkatalogen, i den vanlige skriptkoden på siden —
    det er nettopp det som er poenget: dette er kode som ikke har noe med
    katalogen å gjøre.
    """
    rot = _temprot(tmp_path)
    spek = rot.joinpath(*KILDE_REL)
    tekst = spek.read_text(encoding="utf-8")
    anker = "const MERKEORD = "
    assert anker in tekst, "fant ikke ankeret i UI-koden — kilden har endret form"
    spek.write_text(tekst.replace(anker, linje + "\n" + anker, 1),
                    encoding="utf-8")
    return subprocess.run([sys.executable, str(GENERATOR), str(rot)],
                          capture_output=True, text=True)


@pytest.mark.parametrize("linje", [
    # En postformet STRENG i UI-koden. Helt lovlig JS, og ingen modul.
    """const demo = "{n:58,name:'Demo',area:'X',p:1}";""",
    # Samme form i en linjekommentar, for eksempel som dokumentasjon av
    # hvordan en post ser ut.
    "// {n:58,name:'Demo',area:'X',p:1}",
    # Og i en blokkommentar.
    "/* {n:58,name:'Demo',area:'X',p:1} */",
    # En malstreng er samme sak.
    "const demo = `{n:58,name:'Demo',area:'X',p:1}`;",
])
def test_en_postformet_streng_i_ui_koden_er_ingen_modul(tmp_path, linje):
    """Bare elementene i `const M = [ … ]` er moduler.

    Generatoren fant postene med et fritt søk gjennom hele skriptet (Codex P2
    på #118, fjortende runde), og et regex mot rå tekst ser ikke forskjell på
    kode og tekst. Siden er en levende prototype med egen UI-kode, så en helt
    lovlig linje ble lest som en modul til, og nummerporten stoppet
    genereringen med en melding om en modul ingen har skrevet. Motsatt vei ville
    en post satt ut av drift i en kommentar telt med på samme måte.

    Roten var ikke hvilke tekstformer som ble husket, men at postene ble LETT
    ETTER i det hele tatt. Katalogen er en liste, og en liste har elementer.
    """
    r = _med_uikode(tmp_path, linje)
    assert r.returncode == 0, r.stderr + r.stdout


@pytest.mark.parametrize("linje", [
    # Tegnene `const M = [` i en helt alminnelig streng — for eksempel en
    # feilmelding eller en hjelpetekst som forklarer hva katalogen heter.
    '''const hjelp = "katalogen står som const M = [ … ] i skriptet";''',
    # Samme tegn i en linjekommentar.
    "// katalogen står som const M = [ … ] lenger nede",
    # Og i en blokkommentar.
    "/* katalogen står som const M = [ … ] lenger nede */",
    # En malstreng er samme sak.
    "const hjelp = `katalogen står som const M = [ … ]`;",
    # Og en KOMPLETT tom liste i prosa er fortsatt prosa (Codex P2 på #118,
    # sekstende runde). Formen «hvert element er en post» er sann uten videre
    # når det ikke står noen elementer der, så disse fire ble stående som en
    # katalog nummer to og stoppet generatoren på en redeklarasjon
    # nettleseren ikke ser.
    'const hjelp = "const M = []";',
    "// const M = []",
    "/* const M = [] */",
    "const hjelp = `const M = []`;",
])
def test_et_ankerformet_ord_i_ui_koden_er_ingen_katalog(tmp_path, linje):
    """Bare en ERKLÆRING er en katalog, ikke tegnene som ser ut som en.

    Postene ble gjort strukturelle forrige runde, men ANKERET ble fortsatt lett
    etter i rå skripttekst (Codex P2 på #118, femtende runde), og et regex mot
    rå tekst ser ikke forskjell på kode og streng. En helt lovlig hjelpetekst
    ga da et treff til, og generatoren stoppet på en redeklarasjon nettleseren
    ikke ser — altså rød ferskhetsport av en tekstendring som ikke rører
    katalogen.

    Samme regel som gjorde postene strukturelle gjelder ankeret: en erklæring
    er fulgt av en LISTE av modulposter, og tegnene i en streng er det ikke.
    """
    r = _med_uikode(tmp_path, linje)
    assert r.returncode == 0, r.stderr + r.stdout


def test_to_modulkataloger_er_et_stopp(tmp_path):
    """Ankeret må finnes nøyaktig én gang, ellers vet ingen hvilken som gjelder.

    To `const M` er en redeklarasjon nettleseren selv avviser, så kravet er det
    samme som JS stiller. Uten det ville en halv katalog nummer to stilltiende
    avgjort hva generatoren leste.

    Prøven står ved siden av den over, og de to måler hver sin side av det
    samme skillet: tegnene `const M = [` i en streng er ikke en erklæring, mens
    en HALV katalog er det — og skal fortsatt stoppe, uansett hvor få poster
    den bærer. Den TOMME lista er den ene formen generatoren ikke kan svare på
    fra formen alene; den vokter porten i stedet, se
    `test_to_ekte_kataloger_stopper_porten()`.
    """
    r = _med_uikode(tmp_path, "const M = [{n:58,name:'D',area:'X',p:1}];")
    assert r.returncode != 0, "generatoren valgte én av to kataloger"
    assert "modulkatalog" in (r.stderr + r.stdout)


def test_navneendring_krever_ny_oversettelse(tmp_path):
    """Et omdøpt modulnavn i kilden skal stoppe genereringen (Codex P2 på #43).

    `MODUL_EN` slo tidligere opp på modulnummer alene. Fikk en modul nytt navn i
    spesifikasjonen uten nytt nummer, skrev generatoren det nye navnet i
    `nb.json` og BEHOLDT det gamle produktnavnet i `en.json` — og ingen av de
    andre portene her kunne se det: utdata var byte-identisk med seg selv
    (ferskhetsporten), og den engelske nøkkelen var ikke tom (tekstporten). De
    to offentlige katalogene kunne altså si hver sin ting i det uendelige.
    Denne porten er derfor den eneste som fanger drift MELLOM språkene.

    Navnet som døpes om leses ut av `nb.json`, ikke skrevet inn her: en literal
    ville vært en tredje avskrift av katalogen, og den ville råtnet stille den
    dagen modulen faktisk fikk nytt navn.
    """
    rot = _temprot(tmp_path)
    spek = rot.joinpath(*KILDE_REL)
    navn = json.loads(LOCALER["nb"].read_text(encoding="utf-8"))[
        "site.katalog.m42.navn"]
    tekst = spek.read_text(encoding="utf-8")
    omdopt = tekst.replace(f"n:42,name:'{navn}'", "n:42,name:'Transaksjonsvakt'")
    assert omdopt != tekst, f"fant ikke «{navn}» som modul 42 i kilden"
    spek.write_text(omdopt, encoding="utf-8")

    r = subprocess.run([sys.executable, str(GENERATOR), str(rot)],
                       capture_output=True, text=True)
    assert r.returncode != 0, (
        "generatoren godtok et omdøpt modulnavn — den engelske katalogen ville "
        "beholdt det gamle produktnavnet uten at noe sa fra")
    melding = r.stderr + r.stdout
    assert "M-42" in melding, f"feilmeldingen navngir ikke modulen: {melding}"
    assert navn in melding and "Transaksjonsvakt" in melding, (
        f"feilmeldingen viser ikke hva navnet ble endret fra og til: {melding}")
    # Ingenting skal være skrevet: en generator som stopper halvveis ville
    # etterlatt en katalog og et locale-sett som ikke hører sammen.
    assert not (rot / "platform/core/ui/static/js/katalog.js").exists(), (
        "generatoren skrev katalog.js selv om den avviste kilden")
    for sprak, sti in LOCALER.items():
        assert (rot / "locales" / f"{sprak}.json").read_text(encoding="utf-8") \
            == sti.read_text(encoding="utf-8"), (
                f"{sprak}.json ble skrevet selv om genereringen ble avvist")


def test_katalogen_har_forventet_form():
    katalog, omrader = _katalog_js()
    assert len(katalog) == MODULER, f"forventet {MODULER} moduler"
    assert {m["n"] for m in katalog} == set(range(1, MODULER + 1)), (
        f"modulnumrene er ikke 1..{MODULER} — duplikat eller hull")
    assert len(omrader) == OMRADER, f"forventet {OMRADER} områder"
    assert {m["fase"] for m in katalog} == FASER, (
        "katalogen dekker ikke fase 1–4")
    # Hvert område må ha minst én modul, og områdelistene må til sammen dekke
    # katalogen nøyaktig én gang: en modul i to områder ville stått to steder
    # på forsiden, og en modul i ingen ville vært usynlig.
    fra_omrader: list[int] = []
    for o in omrader:
        assert o["moduler"], f"området {o['id']} har ingen moduler"
        fra_omrader += o["moduler"]
    assert sorted(fra_omrader) == sorted(m["n"] for m in katalog), (
        "områdene dekker ikke katalogen nøyaktig én gang")


def test_spesifikasjonen_baerer_produktnavnet():
    """Sannhetskilden skal hete det produktet heter (Codex P2 på PR #99).

    Rebrandet til Disponit gikk gjennom README, STRUKTUR, begge locale-sett og
    applikasjonsskallet — men v8-spesifikasjonen kom tilbake med det gamle
    navnet i tittel, overskrift og bunntekst. Det er ikke en skrivefeil: det er
    sannhetskilden som spesifiserer et ANNET produkt enn det som bygges, og
    ingen av portene over kunne se det, fordi de bare måler modulkatalogen.

    Navnet leses ut av `nb.json`, ikke skrevet inn her: en literal ville vært
    enda en kopi å holde i takt, og porten skal måle mot det produktet FAKTISK
    heter i dag.
    """
    navn = json.loads(LOCALER["nb"].read_text(encoding="utf-8"))["app.navn"]
    tekst = KILDE.read_text(encoding="utf-8")

    tittel = re.search(r"<title>(.*?)</title>", tekst, re.S)
    assert tittel and navn in tittel.group(1), (
        f"<title> sier «{tittel.group(1) if tittel else '(mangler)'}», "
        f"produktet heter «{navn}»")
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", tekst, re.S)
    assert h1 and h1.group(1).strip() == navn, (
        f"<h1> sier «{h1.group(1).strip() if h1 else '(mangler)'}», "
        f"produktet heter «{navn}»")
    bunn = re.search(r'<p class="fin">(.*?)</p>', tekst, re.S)
    assert bunn and navn in bunn.group(1), (
        f"bunnteksten navngir ikke «{navn}»")


# ---------------------------------------------------------------------------
# MODULKATALOGEN, LEST AV EN JAVASCRIPT-MOTOR
#
# Katalogen står i JavaScript i sannhetskilden, og porten leste den med en
# håndskrevet skanner i Python — tusen linjer som skulle avgjøre hva som er
# kode og hva som er tekst: strenger, malstrenger, mønstre mot divisjon,
# kommentarer, ASI, `for await`, `catch` uten binding, beregnede nøkler,
# escapede nøkler, accessorer. Generatoren hadde sin egen, litt annerledes.
#
# Nitten runder med Codex-review på #118 var nitten former skannerne ikke
# hadde. Formene tar aldri slutt, for mengden er hele grammatikken, og en
# skanner som ikke kjenner en form gjør ikke noe høylytt: den leser noe annet
# enn nettleseren og sier ingenting. Eier avgjorde saken 20/8: bytt lesning,
# ikke legg til former.
#
# `tools/les_katalog.mjs` er nå det ENESTE lesersteget, og både porten og
# generatoren går gjennom det. Det var før et POENG at de to leste hver for
# seg — to lesninger av samme kilde gjør en feil i den ene synlig. I praksis
# ga det to skannere som drev fra hverandre og måtte lappes hver for seg,
# nitten ganger. Én leser kan ikke drive fra seg selv, og den leseren er en
# JavaScript-motor: den kan per konstruksjon ikke ha en annen forestilling om
# JavaScript enn nettleseren har.
LESER = ROT / "tools" / "les_katalog.mjs"


@functools.lru_cache(maxsize=None)
def _katalogposter(kilde: Path = None) -> tuple[dict, ...]:
    """Modulpostene i sannhetskilden, slik nettleseren ser dem.

    FAIL-CLOSED. Uten node er katalogen ulest, og en port som da hoppet over
    seg selv ville vært grønn på en kilde ingen har lest. Ubuntu-runneren har
    node preinstallert, og UI-jobben krever den alt.
    """
    kilde = kilde or KILDE
    try:
        r = subprocess.run(["node", str(LESER), str(kilde)],
                           capture_output=True, text=True)
    except FileNotFoundError:
        raise AssertionError(
            "fant ikke `node` — modulkatalogen leses av tools/les_katalog.mjs, "
            "og uten en JavaScript-motor kan den ikke leses i det hele tatt. "
            "En port som hopper over seg selv her er grønn på en ulest kilde.")
    assert r.returncode == 0, (
        f"kunne ikke lese modulkatalogen i {kilde.name}:\n{r.stderr.strip()}")
    return tuple(json.loads(r.stdout)["moduler"])


# Merkelappen `les_katalog.mjs` setter på en feltverdi som ikke er DATA — en
# funksjon, et mønster, en dato. Katalogen er en kilde som skal kunne leses av
# mer enn nettleseren, og en egenskap som først blir til når siden kjører kan
# hverken leses eller måles.
IKKE_DATA = "__ikke_data__"


def _ikke_data_i(verdi) -> str | None:
    """Hva som ikke er data i `verdi`, eller `None` — HELE VEIEN NED.

    Merket ble bare lett etter i feltets egen verdi (Codex P2). Katalogen
    tillater lister og objekter av data, og leseren merker det som ikke er data
    DER DET STÅR — så en `flow: [() => 42]` bar merket sitt inne i lista, og
    feltet gikk for lesbart. Lovlig er tekst, tall, `true`/`false`/`null` og
    lister og objekter av slike, rekursivt, og kontrollen må gå like dypt som
    tillatelsen.
    """
    if isinstance(verdi, dict):
        if IKKE_DATA in verdi:
            return str(verdi[IKKE_DATA])
        kilder = verdi.values()
    elif isinstance(verdi, list):
        kilder = verdi
    else:
        return None
    for v in kilder:
        funn = _ikke_data_i(v)
        if funn is not None:
            return funn
    return None


def _uleselige_felt(post: dict) -> list[str]:
    """Feltene i posten som ikke bærer data. Se `IKKE_DATA`."""
    return sorted(f for f, v in post.items() if _ikke_data_i(v) is not None)


def _moduler_fra_kilden() -> dict[int, dict[str, str]]:
    """{modulnummer: {fase, dep, kl, rev}} lest ut av spesifikasjonen."""
    ut: dict[int, dict[str, str]] = {}
    for post in _katalogposter():
        if not isinstance(post.get("p"), int) or not isinstance(
                post.get("dep"), str):
            continue
        ut[post["n"]] = {"fase": post["p"], "dep": post["dep"]}
        for navn in ("kl", "rev"):
            if isinstance(post.get(navn), str):
                ut[post["n"]][navn] = post[navn]
    return ut


# En katalog med to poster, slik kilden skriver dem, og en side rundt den.
# Prøvene under legger UI-kode inn i `{ui}` og måler at leseren fortsatt gir
# fra seg NØYAKTIG disse to.
_PROEVESIDE = ("<html><body><p>prosa</p><script>\n"
               "const M = [\n"
               "  {{n:1,name:'En',area:'X',p:1,dep:'',kl:'sideeffektfri'}},\n"
               "  {{n:2,name:'To',area:'X',p:2,dep:'M-1',rev:'direkte'}}\n"
               "];\n"
               "{ui}\n"
               "</script></body></html>\n")


def _les_proveside(tmp_path: Path, ui: str) -> tuple[dict, ...]:
    """Kjør leseren mot en side med `ui` etter katalogen."""
    sti = tmp_path / "prove.html"
    sti.write_text(_PROEVESIDE.format(ui=ui), encoding="utf-8")
    return _katalogposter(sti)


@pytest.mark.parametrize("ui", [
    # En postformet KODEKLAMME i UI-koden. Helt lovlig JS, og ingen modul.
    'const demo = {n:2, p:4, dep:"M-56"};',
    # Samme form i en STRENG, i en malstreng, i en kommentar.
    """const demo = "{n:58,name:'Demo',area:'X',p:1}";""",
    "const demo = `{n:58,name:'Demo',area:'X',p:1}`;",
    "// {n:58,name:'Demo',area:'X',p:1}",
    "/* {n:58,name:'Demo',area:'X',p:1} */",
    # Tegnene `const M = [` i en streng, en malstreng og en kommentar.
    'const hjelp = "katalogen står som const M = [ … ] i skriptet";',
    "const hjelp = `katalogen står som const M = [ … ]`;",
    "// katalogen står som const M = [ … ] lenger nede",
    "/* const M = [] */",
    'const hjelp = "const M = []";',
    # Og en helt vanlig linje som rører DOM-en. Den KASTER i leseren, som
    # ventet — bindingen `M` er alt gjort, og katalogen står.
    "document.getElementById('x').textContent = M.length;",
])
def test_leseren_gir_fra_seg_bare_elementene_i_katalogen(tmp_path, ui):
    """Bare elementene i `const M = [ … ]` er moduler.

    Postene ble før LETT ETTER — først i råtekst, så i det skanneren mente var
    kode (Codex P2 på #118, sjuende og sekstende runde). Siden er en levende
    prototype med egen UI-kode, og en postformet klamme eller streng der ble
    lest som en modul til; `_moduler_fra_kilden()` lagrer per modulnummer, så
    en demo etter katalogen overskrev den ekte modulen, og fase- og enumporten
    målte UI-data.

    Nå er spørsmålet ikke lenger «hva ser ut som en post?». Katalogen er en
    LISTE, motoren gir oss verdien av den, og en tekst som ser ut som en post
    er en tekst.
    """
    poster = _les_proveside(tmp_path, ui)
    assert [p["n"] for p in poster] == [1, 2], (
        f"leseren fant andre poster enn katalogens to: {poster}")
    assert poster[1]["dep"] == "M-1" and poster[1]["p"] == 2, (
        "UI-koden overskrev en ekte modulpost")


@pytest.mark.parametrize("fremmed", [
    # En DATABLOKK. Nettleseren kjører den ikke, og innholdet er JSON-LD — som
    # klassisk JavaScript er `{"@context": …}` en blokk med en etikett, altså
    # en syntaksfeil.
    '<script type="application/ld+json">{"@context":"https://schema.org"}'
    '</script>',
    # En ES-MODUL. Den kjøres, men med egen syntaks og eget skop: `export` er
    # en syntaksfeil i et klassisk skript, og en `const` der er usynlig ute.
    '<script type="module">export const hjelp = 1;</script>',
    # EKSTERN kode. Kroppen kjøres ikke i det hele tatt når `src` står.
    '<script src="ui.js">dette er ikke JavaScript</script>',
])
def test_leseren_hopper_over_skript_nettleseren_ikke_kjorer(tmp_path, fremmed):
    """Bare klassiske innskript er JavaScript (Codex P2).

    Leseren tok før innholdet i ALLE `<script>` og ga det til en klassisk
    `vm.Script`. Legger noen en JSON-LD-blokk eller en modul på siden — begge
    helt vanlige og helt riktig håndtert av nettleseren — er det en syntaksfeil
    for den leseren, og katalogen kan ikke genereres i det hele tatt. Feilen
    ville stått i et element som ikke har noe med katalogen å gjøre.
    """
    sti = tmp_path / "prove.html"
    sti.write_text(
        _PROEVESIDE.format(ui="").replace("<script>", fremmed + "<script>", 1),
        encoding="utf-8")
    poster = _katalogposter(sti)
    assert [p["n"] for p in poster] == [1, 2], (
        f"leseren felte katalogen på et skript nettleseren ikke kjører som "
        f"klassisk JavaScript: {poster}")


@pytest.mark.parametrize("tagg", [
    # Egne `data-`-attributter. Nettleseren ser ingen `src` og ingen `type`
    # her, og kjører taggen som helt vanlig innskript.
    '<script data-src="documentation">',
    '<script data-type="module">',
    '<script data-kilde="spec" data-type="application/ld+json">',
    # Navnet står helt, men skrivemåten er sidens egen: versaler, mellomrom
    # rundt likhetstegnet, apostrofer.
    "<script TYPE = 'text/javascript'>",
    # Et navn uten verdi, og et navn som ENDER på `type` uten å være det.
    "<script async data-subtype=module>",
])
def test_leseren_leser_attributtnavnene_hele(tmp_path, tagg):
    """Et suffiks er ikke et attributtnavn (Codex P2).

    Silingen fra forrige runde søkte i råteksten etter attributtlista:
    `\\bsrc\\s*=` og `\\btype\\s*=`. Ordgrensen fester seg like godt midt i et
    navn, så `data-src="documentation"` ble lest som ekstern kode og
    `data-type="module"` som en ES-modul. Nettleseren kjører taggen — den har
    hverken `src` eller `type` — mens leseren hoppet over den og meldte at
    sannhetskilden ikke har noen katalog. Katalogen kunne da ikke genereres i
    det hele tatt, på grunn av et attributt siden selv håndterer riktig.

    Silingen ble strammet inn for å slippe fremmede skript UT; den må ikke
    samtidig slippe kilden selv ut. Attributtene leses derfor navn for navn.
    """
    sti = tmp_path / "prove.html"
    sti.write_text(_PROEVESIDE.format(ui="").replace("<script>", tagg, 1),
                   encoding="utf-8")
    poster = _katalogposter(sti)
    assert [p["n"] for p in poster] == [1, 2], (
        f"leseren hoppet over katalogens egen tagg «{tagg}»: {poster}")


def test_en_tagg_som_kaster_stopper_ikke_en_senere_katalog(tmp_path):
    """Hver `<script>` er sitt eget skript (Codex P2).

    Leseren skjøtte taggene sammen til ett skript, og da arvet den noe
    nettleseren ikke gjør: et unntak i en tidligere tagg stoppet resten.
    Nettleseren kompilerer og kjører klassiske skript hver for seg på samme
    globale skop — et oppsettsskript som rører `document` og kaster hindrer
    ikke en senere tagg i å erklære katalogen.

    Skjøten gjorde en helt vanlig sideform ulesbar: leseren meldte at det ikke
    fantes noen katalog i en kilde som fungerer i nettleseren, og da kunne
    hverken generatoren eller porten komme videre.
    """
    sti = tmp_path / "prove.html"
    sti.write_text(
        "<html><body><script>document.title = 'oppsett';</script>\n"
        "<script>\nconst M = [\n"
        "  {n:1,name:'En',area:'X',p:1,dep:'',kl:'sideeffektfri'}\n"
        "];\n</script></body></html>\n", encoding="utf-8")
    poster = _katalogposter(sti)
    assert [p["n"] for p in poster] == [1], (
        f"en tidligere tagg som kastet tok katalogen med seg: {poster}")


@pytest.mark.parametrize("todelt", [False, True])
def test_to_kataloger_stopper_leseren(tmp_path, todelt):
    """To `const M` er en redeklarasjon, og motoren avviser den selv.

    Porten og generatoren hadde hver sin regel for at ankeret skulle stå
    nøyaktig én gang, og hver sin måte å skille erklæringen fra de samme
    tegnene i en streng. Motoren trenger ingen regel: `const M` to ganger i
    samme skript er en SyntaxError ved KOMPILERING — også når den andre står i
    kode som aldri kjører, slik den gjør her, bak et `document`-kall som
    kaster.

    STÅR DE I HVER SIN TAGG, kommer den samme feilen når den andre taggen
    bindes til det globale skopet. Nettleseren ville tiet og beholdt den
    første katalogen; en kilde med to kataloger er en kilde ingen skal gjette
    i, så leseren stopper — det er den ene tingen den gjør strengere enn
    nettleseren, og den står her.
    """
    andre = "const M = [{n:58,name:'D',area:'X',p:1}];"
    with pytest.raises(AssertionError) as feil:
        if todelt:
            sti = tmp_path / "prove.html"
            sti.write_text(
                _PROEVESIDE.format(ui="") + f"<script>{andre}</script>\n",
                encoding="utf-8")
            _katalogposter(sti)
        else:
            _les_proveside(tmp_path, andre)
    assert "gyldig JavaScript" in str(feil.value), str(feil.value)


@pytest.mark.parametrize("element", [
    "42",
    "'en streng'",
    "null",
    "[{n:58}]",
    # Et ledd som ikke er DATA i det hele tatt — merket, ikke en post.
    "() => ({n:58})",
])
def test_et_element_som_ikke_er_en_post_stopper_porten(tmp_path, element):
    """Katalogen er en liste av POSTER, og bare det."""
    sti = tmp_path / "prove.html"
    sti.write_text(f"<html><script>const M = [{{n:1,name:'En',area:'X',p:1}}, "
                   f"{element}];</script></html>", encoding="utf-8")
    with pytest.raises(AssertionError) as feil:
        _katalogposter(sti)
    assert "modulpost" in str(feil.value), str(feil.value)


@pytest.mark.parametrize("felt,lesbar", [
    ("kl:'krever_outbox'", True),
    ('"kl": "krever_outbox"', True),
    # Skrivemåten forsvinner i lesningen: begge gir egenskapen `kl`.
    (r"kl:'krever_outbox'", True),
    ("['k' + 'l']:'krever_outbox'", True),
    # Men en verdi som ikke er DATA, er ikke noe katalogen kan bære.
    ("kl:/krever_outbox/", False),
    ("kl:() => 'krever_outbox'", False),
    ("kl:new Date(0)", False),
    # En ACCESSOR er ikke en skrivemåte, den er sidens kode (Codex P2).
    ("get kl(){return 'krever_outbox'}", False),
    # NEDE I EN BEHOLDER teller like mye (Codex P2). Lister og objekter av
    # data er lovlig, så kontrollen må gå like dypt som tillatelsen.
    ("flow:[['Steg'],{note:'x'}]", True),
    ("flow:[() => 42]", False),
    ("flow:{steg:[new Date(0)]}", False),
])
def test_leseren_krever_at_en_feltverdi_er_data(tmp_path, felt, lesbar):
    """Katalogen skal kunne leses av mer enn nettleseren.

    Nøkkelen er motorens sak nå, og den leser alle skrivemåtene likt. VERDIEN
    er porten sin: en funksjon, et mønster eller en dato er ingen katalogverdi,
    og et felt ingen kan lese er et felt ingen kan kontrollere. Leseren merker
    det i stedet for å hoppe over det, se `IKKE_DATA`.

    EN ACCESSOR HAR INGEN VERDI Å LESE. Den ble før lest som en vanlig
    egenskap, og da kjørte getteren — sidens kode — her ute i Node, etter at
    motorens `timeout` var over (Codex P2). Nå leses egenskapen som deskriptor
    og getteren påkalles aldri. Det er også det svaret merket alt gir for en
    funksjon: en egenskap som først blir til når siden kjører er ikke data.
    """
    sti = tmp_path / "prove.html"
    sti.write_text(f"<html><script>const M = [{{n:1,name:'En',area:'X',p:1,"
                   f"{felt}}}];</script></html>", encoding="utf-8")
    post = _katalogposter(sti)[0]
    assert (not _uleselige_felt(post)) is lesbar, post


def test_en_getter_som_ikke_terminerer_henger_ikke_leseren(tmp_path):
    """Getteren kjøres ikke, så den kan ikke henge noen (Codex P2).

    `runInContext` har en `timeout`, men den verner bare det som kjører INNE i
    motoren. Egenskapene ble lest etterpå, her ute i Node, og en accessor ble
    da påkalt uten noen som helst grense: én `get kl(){for(;;);}` i kilden
    hadde hengt generatoren og CI til noe annet slo dem av — ikke feilet,
    hengt.

    Leseren kalles direkte her, med en frist, fordi en regresjon ellers ikke
    ville vist seg som en rød test men som en jobb som aldri blir ferdig.
    """
    sti = tmp_path / "prove.html"
    sti.write_text("<html><script>const M = [{n:1,name:'En',area:'X',p:1,"
                   "get kl(){for(;;);}}];</script></html>", encoding="utf-8")
    r = subprocess.run(["node", str(LESER), str(sti)],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert _uleselige_felt(json.loads(r.stdout)["moduler"][0]) == ["kl"], r.stdout


# Kontraktklassene katalogen bruker er de SAMME feltene modulregisteret lagrer,
# og registeret håndhever dem med CHECK-vilkår. Enumene leses derfor ut av
# den MIGRERTE BASEN, ikke skrevet av her: en kopi i testen ville vært nok en
# kilde som kan drive fra databasen — akkurat feilen porten finnes for å
# hindre. Og de regnes ikke fram av migrasjonshistorikken: hva som står igjen
# etter utvidelser, innstramminger og slipp er nøyaktig det basen VET, fordi
# den har kjørt filene.
MIGRASJONER = ROT / "platform" / "core" / "db" / "migrations"
KONTRAKTFELT = {"kl": "sideeffektklasse", "rev": "reversibilitet"}
# Tabellen kontrakten faktisk lagres i, opprettet i 014 og utvidet i 036. Den
# står her fordi enumoppslaget skal binde seg til den, ikke til kolonnenavnet
# på tvers av skjemaet — se `_registerenum()`.
MODULKONTRAKT = "modulkontrakt"



# ---------------------------------------------------------------------------
# REGISTERETS ENUM, MÅLT I DEN MIGRERTE BASEN
#
# Porten regnet før registerets gjeldende tilstand ut av migrasjonshistorikken:
# hvert CHECK og hvert slipp en hendelse, `DO`-grener, omdøp, arv og dynamisk
# DDL modellert i Python. Tjue runder med Codex-review på #118 var tjue hull i
# den modellen, og de seks siste funnene gjaldt migrasjoner som ikke fantes —
# simulatorens hull, ikke basens. En simulator måler sin egen fullstendighet.
#
# SP-13 avgjorde saken: semantikk verifiseres som OPPSLAG i den virkelige
# tilstanden. Testbasen HAR kjørt migrasjonene — hva som står igjen etter
# utvidelser, innstramminger, slipp og betingede grener er ikke et
# regnestykke her, det er et faktum der. Porten spør derfor basen, to veier
# som må være enige:
#
#   * LESNINGEN: `pg_get_constraintdef` gir vilkårene som står på
#     `modulkontrakt` NÅ, i basens egen kanoniske form, og `pglast` —
#     PostgreSQLs egen grammatikk — leser verdilistene ut av dem. Et vilkår
#     som ikke er en verdiliste gjør kolonnen UVISS, høylytt og aldri stille
#     bredere (`ULESELIG_SQL`).
#   * DOMMEN: hver verdi katalogen bruker prøves med en INSERT mot en kopi av
#     vilkårene (`LIKE … INCLUDING CONSTRAINTS`), så det er PostgreSQL selv
#     som sier ja eller nei — også den dagen et vilkår får en form lesningen
#     ikke kan regne på.
#
# Basen er den samme migrerte testbasen resten av testene bruker
# (`DISPONIT_TEST_*`-DSN-ene); uten den hopper DB-portene her over på samme
# vilkår som i søstertestene — CI setter alltid DSN-ene, så i CI er porten
# aldri hoppet over.
try:
    import pglast
except ModuleNotFoundError:  # pragma: no cover - meldingen ER porten her
    raise ModuleNotFoundError(
        "pglast mangler — vilkårsdefinisjonene og migrasjonenes strengverdier "
        "leses av PostgreSQLs egen grammatikk (libpg_query), og uten den kan "
        "registerets enum ikke leses i det hele tatt. Den står i "
        "requirements-dev.txt; en port som hopper over seg selv her ville "
        "vært grønn på et ulest register.")

DSN = os.environ.get("DISPONIT_TEST_MIGRATOR_DSN") \
    or os.environ.get("DISPONIT_TEST_DSN")
pg = pytest.mark.skipif(
    not DSN, reason="DISPONIT_TEST_MIGRATOR_DSN/DISPONIT_TEST_DSN ikke satt")

# Verdien en kolonne får når porten ikke kan lese vilkåret som binder den.
# Backstrek kan ikke være en enumverdi skrevet rett fram, så den kan ikke
# kollidere med en ekte.
ULESELIG_SQL = "\\"


def _samle(node, ut: set[str]) -> None:
    """Legg kolonnenavnene i `node` i `ut`. Går gjennom hele undertreet."""
    if isinstance(node, (list, tuple)):
        for x in node:
            _samle(x, ut)
        return
    if isinstance(node, pglast.ast.ColumnRef):
        felt = [f.sval for f in node.fields
                if isinstance(f, pglast.ast.String)]
        if felt:
            ut.add(felt[-1])
        return
    if isinstance(node, pglast.ast.Node):
        for navn in node:
            _samle(getattr(node, navn), ut)


def _verdiliste(node) -> list[str] | None:
    """Strengverdiene i `node` hvis HVERT ledd er én strengkonstant.

    Et ledd som er et UTTRYKK gir `None` for hele lista (Codex P2 på #118,
    tjuende runde): `IN ('direkte' || '_v2', 'irreversibel')` tillater `direkte_v2`
    og `irreversibel`, ikke tre verdier. Å regne ut uttrykket ville vært å
    skrive PostgreSQLs operatorer av i Python, og et gjettet innhold er verre
    enn ingen når svaret brukes til å godta en kontrakt.
    """
    ut = []
    for ledd in node or ():
        # Basens egen rendering (`pg_get_constraintdef`) skriver hvert ledd
        # som `'verdi'::text`. Kastet endrer ingen tekstverdi, så det pakkes
        # ut — men bare ETT lag, og bare rundt en konstant: et kast rundt et
        # uttrykk er fortsatt et uttrykk.
        if isinstance(ledd, pglast.ast.TypeCast):
            ledd = ledd.arg
        if not isinstance(ledd, pglast.ast.A_Const) \
                or not isinstance(ledd.val, pglast.ast.String):
            return None
        ut.append(ledd.val.sval)
    return ut


def _enkolonne(node) -> str | None:
    """Kolonnen `node` er, hvis den er ÉN kolonnereferanse."""
    if not isinstance(node, pglast.ast.ColumnRef):
        return None
    felt = [f.sval for f in node.fields if isinstance(f, pglast.ast.String)]
    return felt[-1] if len(felt) == len(node.fields) and felt else None


def _bindinger(uttrykk) -> dict[str, set[str]]:
    """{kolonne: verdiene vilkåret tillater} for ett CHECK-uttrykk.

    Tre regler, og de er semantiske — ikke former:

    ET SAMMENSATT PREDIKAT er ikke bare lista si (Codex P2 på #118, attende og
    tjuende runde). `IN`-lista ble lest og resten av uttrykket gitt fra seg i
    stillhet, så `CHECK (rev IN ('a','b') AND rev <> 'b')` ble meldt som om
    begge verdiene var lov. Verre: med flere `IN`-ledd ble bare det FØRSTE
    lest, og et videre vilkår på den andre kolonnen ble stående som gjeldende.

    `A AND B` er håndhevet nøyaktig når både A og B er det, så en OG-kjede
    leses ledd for ledd og bidragene snittes. Alt ANNET — `OR`, `NOT`, en
    sammenligning, et funksjonskall — kan porten ikke regne på, og da er
    `ULESELIG_SQL` svaret for hver kolonne uttrykket nevner: det hverken
    gjetter eller tier, og `_registerenum()` sier fra på den kolonnen katalogen
    faktisk måles mot.

    Det gjelder også en CHECK som narrer uten `IN` (Codex P2 på #118, tjuende
    runde): `CHECK (rev = 'direkte')` er håndhevet av PostgreSQL, og ble før
    forkastet i stillhet slik at det forrige, videre vilkåret sto igjen som
    gjeldende. Nå faller den i «alt annet» og gjør kolonnen uviss.

    `= ANY (ARRAY[…])` leses som `IN`, for det ER `IN` — grammatikken skiller
    dem, semantikken ikke. Å lese den er derfor ingen ny FORM å vedlikeholde:
    tar porten feil om en node den ikke kjenner, blir svaret uvisst og høylytt,
    aldri stille bredere.
    """
    if isinstance(uttrykk, pglast.ast.BoolExpr) \
            and uttrykk.boolop == pglast.enums.BoolExprType.AND_EXPR:
        ut: dict[str, set[str]] = {}
        for ledd in uttrykk.args:
            for kol, verdier in _bindinger(ledd).items():
                ut[kol] = _snitt(ut[kol], verdier) if kol in ut else verdier
        return ut
    if isinstance(uttrykk, pglast.ast.A_Expr) \
            and [s.sval for s in uttrykk.name] == ["="]:
        kolonne = _enkolonne(uttrykk.lexpr)
        verdier = None
        if uttrykk.kind == pglast.enums.A_Expr_Kind.AEXPR_IN:
            verdier = _verdiliste(uttrykk.rexpr)
        elif uttrykk.kind == pglast.enums.A_Expr_Kind.AEXPR_OP_ANY \
                and isinstance(uttrykk.rexpr, pglast.ast.A_ArrayExpr):
            verdier = _verdiliste(uttrykk.rexpr.elements)
        if kolonne and verdier:
            return {kolonne: set(verdier)}
    nevnte: set[str] = set()
    _samle(uttrykk, nevnte)
    return {kol: {ULESELIG_SQL} for kol in nevnte}


def _snitt(a: set[str], b: set[str]) -> set[str]:
    """Verdiene to vilkår er enige om — med uvissheten i behold.

    En mengde her er en ØVRE GRENSE med et flagg: verdiene, og `ULESELIG_SQL`
    hvis den øvre grensen kan være videre enn det som faktisk gjelder. En
    mengde som BARE er `{ULESELIG_SQL}` er «vi vet ingenting», altså ingen
    grense i det hele tatt.

    Da er snittet det opplagte: grensene snittes, og et vilkår vi ikke vet noe
    om snevrer ingenting inn. Uvissheten forplanter seg, for det ULESELIGE er
    ikke en verdi — det er en manglende opplysning, og et annet vilkår kan ikke
    opplyse den. Snittes den bort, blir et vilkår porten bare kjenner et
    oversett av, meldt som lest, og da er svaret videre enn databasen.
    """
    kjent_a, kjent_b = a - {ULESELIG_SQL}, b - {ULESELIG_SQL}
    kjent = kjent_a & kjent_b if kjent_a and kjent_b else kjent_a or kjent_b
    return kjent | ({ULESELIG_SQL} & (a | b))




def _setningene(sql: str):
    """(setningen, teksten den står som) for hver setning i `sql`.

    TEKSTEN følger med fordi en `DO`- eller funksjonsKROPP må leses av
    `parse_plpgsql`, og den vil ha HELE setningen: kroppen alene sier ikke om
    funksjonen har parametre, om den returnerer noe, eller om `NEW` finnes.
    Å pakke kroppen inn i en `DO` for anledningen — som er den nærliggende
    snarveien — gjør en helt alminnelig triggerfunksjon usyntaktisk, og da
    ville lesningen hoppet over den i stillhet. Det er nøyaktig feilklassen
    denne runden fjerner.
    """
    for rå in pglast.parse_sql(sql):
        a = rå.stmt_location
        yield rå.stmt, sql[a:a + rå.stmt_len] if rå.stmt_len else sql[a:]



def _kroppen(stmt) -> tuple[str, str]:
    """(teksten i kroppen, språket den er skrevet i).

    Kroppen står som en STRENGKONSTANT i treet, men den er kode og ikke en
    verdi. Språket avgjør hvordan den skal leses videre: `DO` er PL/pgSQL med
    mindre noe annet står, og en funksjon sier det selv.
    """
    kropp, sprak = "", "plpgsql"
    for arg in (stmt.args if isinstance(stmt, pglast.ast.DoStmt)
                else stmt.options) or ():
        if arg.defname == "as":
            kropp = arg.arg.sval if isinstance(arg.arg, pglast.ast.String) \
                else " ".join(s.sval for s in arg.arg
                              if isinstance(s, pglast.ast.String))
        elif arg.defname == "language" and isinstance(arg.arg,
                                                      pglast.ast.String):
            sprak = arg.arg.sval
    return kropp, sprak.lower()



def _plpgsql(setning: str) -> list:
    """PL/pgSQL-treet for `setning` — en hel `DO` eller `CREATE FUNCTION`.

    `pglast.parse_plpgsql()` er `json.loads()` på det libpg_query skriver, og
    for en TRIGGERFUNKSJON skriver den ugyldig JSON: hver ubrukt plass i
    `datums` kommer ut som `{}}` med en klammeparentes for mye. 76 av repoets
    296 PL/pgSQL-setninger treffer det.

    Reparasjonen er smal og kan sies helt ut: `[{}}` og `,{}}` finnes ikke i
    gyldig JSON. Etter `[` eller `,` står et ELEMENT, så `{}` der er et tomt
    objekt — og da må neste tegn være `,` eller `]`, aldri `}`. De to
    sekvensene kan derfor bare være denne defekten, og ingenting annet.

    Feltet vi retter i, `datums`, er navnene på funksjonens variabler; porten
    leser aldri der. Alternativet — å hoppe over setninger som ikke lot seg
    lese — er nøyaktig den stille lesningen denne runden fjerner: 120 av
    hvitlistens identifikatorer, `alltid_stopp` blant dem, står i en
    funksjonskropp og ville forsvunnet uten et ord.
    """
    rå = pglast.parser.parse_plpgsql_json(setning)
    try:
        return json.loads(rå)
    except json.JSONDecodeError:
        return json.loads(rå.replace("[{}}", "[{}").replace(",{}}", ",{}"))



def _skrevne_verdier(sql: str) -> list[str]:
    """Hver strengverdi migrasjonen SKRIVER — kropper med, kommentarer uten.

    Grunnlaget for hvitlisten i `_kjente_identifikatorer()`, og skillet det
    hviler på er: hva HAR registeret skrevet ned? Et navn nevnt i en merknad er
    ikke skrevet — en kommentar finnes ikke i et syntakstre, så den forsvinner
    av seg selv nå. En melding er ÉN tekst, ikke setninger med navn i, uansett
    om den er skrevet med doblede apostrofer, dollarsitert eller skjøtt over
    flere linjer; grammatikken løser opp alle tre til den ene verdien.

    En KROPP leses derimot videre inn i: `CREATE FUNCTION … AS $$ … $$`
    definerer den uten å kjøre den, men verdiene den bærer er skrevet av
    registeret. Det samme gjelder `DO $$ … $$`.
    """
    ut: list[str] = []
    for stmt, tekst in _setningene(sql):
        _samleverdier(stmt, ut, tekst)
    return ut


def _samleverdier(node, ut: list[str], tekst: str = None) -> None:
    """Legg strengkonstantene i `node` i `ut`, kropper regnet med."""
    if isinstance(node, (list, tuple)):
        for x in node:
            _samleverdier(x, ut)
        return
    if isinstance(node, (pglast.ast.DoStmt, pglast.ast.CreateFunctionStmt)):
        _samleikropp(node, ut, tekst)
        return
    if isinstance(node, pglast.ast.A_Const) \
            and isinstance(node.val, pglast.ast.String):
        ut.append(node.val.sval)
        return
    if isinstance(node, pglast.ast.Node):
        for navn in node:
            _samleverdier(getattr(node, navn), ut)


def _samleikropp(stmt, ut: list[str], tekst: str) -> None:
    """Verdiene i en `DO`- eller funksjonskropp.

    Kroppen står som en strengkonstant i treet, men den er KODE og ikke en
    verdi: leses den som en verdi, blir hele kroppen ett ord ingen kjenner
    igjen, og verdiene i den blir usynlige. Den leses derfor som det den er —
    PL/pgSQL med `parse_plpgsql`, ren SQL med `parse_sql`.
    """
    kropp, sprak = _kroppen(stmt)
    if not kropp:
        return
    if sprak != "plpgsql":
        for rå in pglast.parse_sql(kropp):
            _samleverdier(rå.stmt, ut)
        return
    assert tekst, ("en PL/pgSQL-kropp uten setningen sin — `parse_plpgsql` "
                   "trenger hele erklæringen, se `_setningene()`")
    _samleiplpgsql(_plpgsql(tekst), ut)


# Feltene i PL/pgSQL-treet som bærer TEKST registeret skriver, men som ikke er
# SQL: meldingen i en `RAISE` er én tekst, ikke setninger med verdier i.
_TEKSTFELT = ("message", "condname", "value")
# PL/pgSQL merker selv hva en innebygd spørring ER, og merkelappen er
# PostgreSQLs egen `RawParseMode`. Bare den første er en hel SETNING; resten må
# gjøres til gyldig SQL før de kan leses, for de er skrevet slik PL/pgSQL leser
# dem: betingelsen i et `IF` står som `FALSE`, ikke `SELECT FALSE`, og en
# tildeling som `v_tenant := 1`, ikke som en setning.
#
# Modusen leses av treet, ikke gjettet av formen. En modus porten ikke kjenner
# er en `KeyError` med tallet i — høyt, ikke stille.
_SETNINGSMODUS = 0
_TILDELINGSMODI = (3, 4, 5)


def _somsql(uttrykk: dict) -> str:
    """En innebygd PL/pgSQL-spørring som en hel SQL-setning. Se `_SETNINGSMODUS`."""
    spørring, modus = uttrykk.get("query", ""), uttrykk.get("parseMode", 0)
    if modus == _SETNINGSMODUS:
        return spørring
    if modus in _TILDELINGSMODI:
        # `mål := uttrykk`. Målet er en variabel med eventuelle ledd, og
        # verdien er alt etter tildelingstegnet.
        return "SELECT " + spørring.split(":=", 1)[1]
    return "SELECT " + spørring


def _samleiplpgsql(node, ut: list[str]) -> None:
    """Verdiene i et PL/pgSQL-tre. Spørringene i det leses som SQL."""
    if isinstance(node, list):
        for x in node:
            _samleiplpgsql(x, ut)
        return
    if not isinstance(node, dict):
        return
    for navn, innhold in node.items():
        if navn == "PLpgSQL_expr" and isinstance(innhold, dict):
            for rå in pglast.parse_sql(_somsql(innhold)):
                _samleverdier(rå.stmt, ut)
            continue
        if navn in _TEKSTFELT and isinstance(innhold, str):
            ut.append(innhold)
            continue
        _samleiplpgsql(innhold, ut)


@functools.cache
def _kontraktvilkar() -> tuple[tuple[str, str], ...]:
    """(navn, definisjon) for hvert CHECK-vilkår på `modulkontrakt` — NÅ.

    Definisjonen er basens egen (`pg_get_constraintdef`), altså den kanoniske
    formen av det som faktisk håndheves: legger en migrasjon til en CHECK uten
    å slippe den gamle, står begge her, og et vilkår som er sluppet er borte.
    Alt simulatoren regnet på — rekkefølge, grener, omdøp — er allerede
    avgjort av at basen kjørte filene.
    """
    with psycopg.connect(DSN) as k:
        try:
            rader = k.execute(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
                " WHERE conrelid = %s::regclass AND contype = 'c'"
                " ORDER BY conname", (MODULKONTRAKT,)).fetchall()
        finally:
            k.rollback()
    return tuple((navn, definisjon) for navn, definisjon in rader)


def _enum_av(vilkar, kolonne: str) -> set[str]:
    """Verdiene `vilkar` tillater i `kolonne` — snittet, med uvissheten i behold.

    Hver definisjon er ETT uttrykk; `_bindinger()` leser det, og bidragene fra
    flere vilkår på samme kolonne snittes fordi PostgreSQL håndhever ALLE:
    en verdi må stå i hvert av dem for å slippe gjennom.
    """
    ut: set[str] | None = None
    for _navn, definisjon in vilkar:
        uttrykk = pglast.parse_sql(
            f"ALTER TABLE {MODULKONTRAKT} ADD CONSTRAINT p {definisjon}"
        )[0].stmt.cmds[0].def_.raw_expr
        bind = _bindinger(uttrykk)
        if kolonne in bind:
            ut = bind[kolonne] if ut is None else _snitt(ut, bind[kolonne])
    return ut if ut is not None else set()


def _registerenum(kolonne: str) -> set[str]:
    """Verdiene `modulkontrakt` godtar i `kolonne` NÅ, lest av basen.

    TABELLEN er en del av spørsmålet (Codex P2 på #118, sjette runde): dette
    slo før opp på kolonnenavnet alene og unionerte over alle tabeller — og en
    union er ikke det databasen gjør. Kontrakten en katalogmodul blir til når
    den bygges, lagres i `modulkontrakt`, og det er DEN tabellens CHECK-vilkår
    som avviser den; oppslaget binder seg derfor til `conrelid`.

    Feiler oppslaget, er det fordi kolonnen ikke lenger CHECK-es på
    `modulkontrakt` — og da er det porten som skal rettes, ikke katalogen.

    En kolonne porten ikke kunne lese vilkåret for, se `ULESELIG_SQL`, sier
    fra HER: det er kolonnen katalogen faktisk måles mot som må være lest
    riktig, og et gjettet innhold ville vært verre enn ingen.
    """
    ut = _enum_av(_kontraktvilkar(), kolonne)
    assert ut, (f"fant ikke noe CHECK-vilkår for {MODULKONTRAKT}.{kolonne} i "
                f"basen — kolonnen er ikke lenger bundet der, og da er det "
                f"porten som må rettes, ikke katalogen")
    assert ULESELIG_SQL not in ut, (
        f"CHECK-vilkåret for {MODULKONTRAKT}.{kolonne} er ikke en verdiliste "
        f"porten kan regne på — et sammensatt eller negativt predikat, eller "
        f"et uttrykk i lista. Skriv vilkåret som `{kolonne} IN ('…', '…')`; "
        f"proben (`_proben_godtar`) dømmer fortsatt riktig, men lesningen som "
        f"skal NAVNGI de tillatte verdiene kan ikke gjette.")
    return ut


def _proben_godtar(kolonne: str, verdi: str) -> bool:
    """Basens egen dom over `verdi` i `modulkontrakt.kolonne`.

    Vilkårene kopieres til en temp-tabell (`LIKE … INCLUDING CONSTRAINTS`) av
    PostgreSQL selv, NOT NULL på de andre kolonnene løsnes så én kolonne kan
    prøves alene, og så er dommen en INSERT: `CheckViolation` er nei, alt
    annet er ja. Ingen lesning av vilkåret i det hele tatt — dette er
    motprøven som holder `_registerenum()` ærlig den dagen et vilkår får en
    form lesningen ikke kan regne på.

    Et vilkår som binder FLERE kolonner sammen dømmer her med de andre
    kolonnene som NULL — og et CHECK med ukjent operand slipper gjennom, slik
    PostgreSQL selv definerer det. Det er samme dom som basen feller; proben
    dikter ikke en strengere.
    """
    with psycopg.connect(DSN) as k:
        try:
            k.execute(f"CREATE TEMP TABLE katalogprobe "
                      f"(LIKE {MODULKONTRAKT} INCLUDING CONSTRAINTS)")
            for (kol,) in k.execute(
                    "SELECT attname FROM pg_attribute"
                    " WHERE attrelid = 'katalogprobe'::regclass"
                    " AND attnum > 0 AND NOT attisdropped AND attnotnull"
                    ).fetchall():
                k.execute(f'ALTER TABLE katalogprobe '
                          f'ALTER COLUMN "{kol}" DROP NOT NULL')
            try:
                k.execute(
                    f'INSERT INTO katalogprobe ("{kolonne}") VALUES (%s)',
                    (verdi,))
            except psycopg.errors.CheckViolation:
                return False
            return True
        finally:
            k.rollback()


def _modulkontraktens_vilkarsuttrykk(stmt) -> list:
    """CHECK-uttrykkene én setning legger på `modulkontrakt` — ren syntaks.

    Ingen tilstand føres: ikke rekkefølge, ikke slipp, ikke grener. Dette er
    grunnlaget for `_noen_gang_bundet()`, som bare trenger UNIONEN over
    historikken — hva som gjelder NÅ svarer basen på, se `_registerenum()`.
    """
    ut = []
    sjekk = pglast.enums.ConstrType.CONSTR_CHECK
    if isinstance(stmt, pglast.ast.CreateStmt) \
            and stmt.relation.relname == MODULKONTRAKT:
        for elt in stmt.tableElts or ():
            if isinstance(elt, pglast.ast.ColumnDef):
                ut.extend(c.raw_expr for c in elt.constraints or ()
                          if c.contype == sjekk and c.raw_expr is not None)
            elif isinstance(elt, pglast.ast.Constraint) \
                    and elt.contype == sjekk and elt.raw_expr is not None:
                ut.append(elt.raw_expr)
    if isinstance(stmt, pglast.ast.AlterTableStmt) \
            and stmt.relation.relname == MODULKONTRAKT:
        for cmd in stmt.cmds or ():
            definisjon = getattr(cmd, "def_", None)
            if isinstance(definisjon, pglast.ast.Constraint) \
                    and definisjon.contype == sjekk \
                    and definisjon.raw_expr is not None:
                ut.append(definisjon.raw_expr)
    return ut


@functools.cache
def _noen_gang_bundet() -> frozenset[str]:
    """Verdier som noen gang har stått i et CHECK på kontraktkolonnene.

    En ren union over migrasjonsfilene, lest som syntaks setning for setning —
    med vilje uten tilstand: spørsmålet er «har registeret noen gang bundet
    dette navnet?», og for det er historikken svaret uansett hva senere filer
    gjorde med vilkåret. Hva som gjelder NÅ er basens spørsmål, og
    pensjonert = noen gang − nå regnes der de to møtes, se
    `_kjente_identifikatorer()`.

    RETNINGEN når lesningen ikke ser alt, sagt rett ut: et vilkår lagt på
    gjennom dynamisk DDL eller i en `DO`-kropp fanges ikke her. Da mangler
    verdien i `noen gang`, blir ikke regnet som pensjonert, og prosaporten
    forblir MILDERE — den avviser aldri legitim tekst på grunn av et hull her.
    Selve enum-dommen over katalogen tar ingen omvei om denne lista; den bor i
    basen.
    """
    ut: set[str] = set()
    kolonner = set(KONTRAKTFELT.values())
    for fil in sorted(MIGRASJONER.glob("*.sql")):
        for stmt, _tekst in _setningene(fil.read_text(encoding="utf-8")):
            for uttrykk in _modulkontraktens_vilkarsuttrykk(stmt):
                for kol, verdier in _bindinger(uttrykk).items():
                    if kol in kolonner:
                        ut |= verdier - {ULESELIG_SQL}
    return frozenset(ut)


@pg
def test_kontraktklassene_i_katalogen_finnes_i_modulregisteret():
    """`kl` og `rev` i katalogen må være verdier registeret godtar.

    Codex P2 på PR #118: M-57 kom inn med `kl: "dokumentbehandling"` og
    `rev: "rådgivende_pluss_signert_utsendelse"`. Begge er utenfor CHECK-
    vilkårene i migrasjon 014 og 036, og de står i nøyaktig de feltene hver
    annen modul bruker til maskinlesbare klasser. En modul beskrevet slik kan
    ikke registreres når den skal bygges: kontrakten avvises av databasen, og
    den som implementerer må enten se bort fra sannhetskilden eller endre den.

    Prosa om hvorfor en klasse ble valgt hører hjemme i `merknad` og `guard` —
    de feltene har ingen enum og ingen maskin leser dem.

    Enumene leses fra `modulkontrakt` i den MIGRERTE BASEN og ikke som en
    union over alle tabeller som tilfeldigvis har en kolonne med samme navn —
    det er den tabellen kontrakten lagres i, og altså den som sier nei. Se
    `_registerenum()`.

    Porten sjekker de feltene som FINNES, og derfor må den også kreve at hvert
    felt lar seg lese (Codex P2 på #118, tolvte runde). En nøkkel skrevet
    `['\\x6bl']` er `kl` for nettleseren, men noe annet for en parser som leser
    råteksten — og det feltet porten ikke ser, kontrollerer den ikke. Å mangle
    et felt er farligere enn å misforstå det. NØKKELEN er ikke lenger et
    spørsmål: `les_katalog.mjs` er en JavaScript-motor, så egenskapen heter det
    nettleseren kaller den. VERDIEN er det fortsatt — en funksjon eller et
    mønster i et `kl`-felt er ingen klasse noen kan måle, se `IKKE_DATA`.

    MUTASJONEN SOM DREPER DENNE: hardkod enumene i testen. Da vokter porten en
    kopi, og en migrasjon som strammer inn et vilkår går rett forbi den.
    """
    uleselige = [f"M-{p['n']}.{felt}" for p in _katalogposter()
                 for felt in _uleselige_felt(p)]
    assert not uleselige, (
        "modulposter med en feltverdi som ikke er data: "
        + ", ".join(uleselige) + " — katalogen bærer tekst, tall og lister av "
        "slike, ikke noe som først blir til når siden kjører")
    tillatt = {felt: _registerenum(kol) for felt, kol in KONTRAKTFELT.items()}
    avvik = [f"M-{n}.{felt}={d[felt]!r} (godtatt: "
             f"{', '.join(sorted(tillatt[felt]))})"
             for n, d in sorted(_moduler_fra_kilden().items())
             for felt in KONTRAKTFELT
             if felt in d and d[felt] not in tillatt[felt]]
    assert not avvik, (
        "kontraktklasser i katalogen som modulregisteret vil avvise: "
        + "; ".join(avvik))


@pg
def test_basens_egen_dom_bekrefter_lesningen():
    """To lesninger av samme register må dømme likt — ellers er porten rød.

    `_registerenum()` NAVNGIR de tillatte verdiene ved å lese basens egen
    vilkårsdefinisjon; `_proben_godtar()` spør basen direkte, verdi for verdi,
    uten å lese noe. Er de uenige om én eneste verdi katalogen bruker, har
    lesningen et hull — og da skal porten si det HER, ikke la katalogporten
    stå grønn på en lesning ingen har motprøvd.

    Den negative kontrollen er med fordi to lesninger som begge sier ja til
    alt også er «enige»: en verdi ingen har, må få nei fra BEGGE.
    """
    for felt, kolonne in KONTRAKTFELT.items():
        tillatt = _registerenum(kolonne)
        brukte = {d[felt] for _, d in sorted(_moduler_fra_kilden().items())
                  if felt in d}
        assert brukte, f"ingen modulpost bærer feltet {felt!r} — porten er tom"
        for verdi in sorted(brukte | {"klasse_ingen_har"}):
            dom = _proben_godtar(kolonne, verdi)
            assert dom is (verdi in tillatt), (
                f"basen og lesningen er uenige om {kolonne}={verdi!r}: "
                f"proben sier {'ja' if dom else 'nei'}, lesningen "
                f"{'ja' if verdi in tillatt else 'nei'} (lest: "
                f"{', '.join(sorted(tillatt))})")
        assert not _proben_godtar(kolonne, "klasse_ingen_har")


# Formene under er basens egen rendering (`pg_get_constraintdef`-fasong, med
# kast og doble parenteser) og de rene kildeformene om hverandre — lesningen
# skal dømme likt uansett hvem som skrev vilkåret ned.
@pytest.mark.parametrize("definisjon,forventet", [
    # 036-vilkårets fasong slik basen selv skriver den ut.
    ("CHECK ((sideeffektklasse = ANY (ARRAY['sideeffektfri'::text, "
     "'ekstern_lesing'::text, 'krever_outbox'::text])))",
     {"sideeffektfri", "ekstern_lesing", "krever_outbox"}),
    # Kildeformen fra 014.
    ("CHECK (reversibilitet IN ('direkte', 'kompenserende', 'irreversibel'))",
     {"direkte", "kompenserende", "irreversibel"}),
    # Et vilkår som ikke er en verdiliste gjør kolonnen UVISS — aldri gjettet,
    # aldri stille videre. Hver av disse formene ble lest FEIL av skanneren
    # før #118: den fant en liste (eller ingen), meldte den som hele
    # sannheten, og gikk videre.
    ("CHECK ((length(sideeffektklasse) < 20))", {ULESELIG_SQL}),
    ("CHECK ((sideeffektklasse <> 'sideeffektfri'))", {ULESELIG_SQL}),
    # En OG-kjede leses ledd for ledd og snittes — og uvissheten fra leddet
    # lesningen ikke kan regne på, forplanter seg i stedet for å snittes bort.
    ("CHECK (((sideeffektklasse = ANY (ARRAY['sideeffektfri'::text])) "
     "AND (sideeffektklasse <> 'krever_outbox')))",
     {"sideeffektfri", ULESELIG_SQL}),
    # Et ledd som er et UTTRYKK gir ingen liste: å regne det ut ville vært å
    # skrive PostgreSQLs operatorer av i Python (Codex P2 på #118, tjuende
    # runde).
    ("CHECK ((sideeffektklasse = ANY (ARRAY[('side'::text || "
     "'effektfri'::text)])))", {ULESELIG_SQL}),
])
def test_et_vilkaar_som_ikke_er_en_verdiliste_gjor_kolonnen_uviss(
        definisjon, forventet):
    """Lesningen navngir verdier eller melder uvisst — aldri noe imellom.

    Dette er den rene lesningen, uten base: `_enum_av()` får definisjonen som
    tekst, nøyaktig slik `_kontraktvilkar()` ville gitt den videre. Uvisshet
    her er ikke en feil i seg selv — proben dømmer fortsatt — men
    `_registerenum()` skal stoppe HØYT på den i stedet for å gjette, se
    testen under.
    """
    assert _enum_av((("p", definisjon),),
                    "sideeffektklasse" if "sideeffektklasse" in definisjon
                    else "reversibilitet") == forventet


def test_et_uleselig_vilkaar_stopper_enumoppslaget(monkeypatch):
    """`_registerenum()` skal si fra HØYT, ikke gjette eller tie.

    Den dagen et vilkår på `modulkontrakt` får en form lesningen ikke kan
    regne på, er riktig svar en rød port med beskjed om å skrive vilkåret som
    en verdiliste — aldri et gjettet innhold, og aldri en stille videre en.
    """
    monkeypatch.setattr(
        sys.modules[__name__], "_kontraktvilkar",
        lambda: (("p", "CHECK ((length(sideeffektklasse) < 20))"),))
    with pytest.raises(AssertionError, match="ikke en verdiliste"):
        _registerenum("sideeffektklasse")


def test_en_ubundet_kolonne_stopper_enumoppslaget(monkeypatch):
    """Forsvinner vilkåret helt, er det porten som skal rettes — høylytt."""
    monkeypatch.setattr(sys.modules[__name__], "_kontraktvilkar", lambda: ())
    with pytest.raises(AssertionError, match="ikke noe CHECK-vilkår"):
        _registerenum("sideeffektklasse")


# Port 9 leser modulpostene. Den forklarende prosaen rundt dem leser den ikke —
# og det var der de to oppfunne klassene ble stående etter at postene var
# rettet (Codex P2 på #118, tredje runde): endringsloggen presenterte fortsatt
# `dokumentbehandling` og `rådgivende_pluss_signert_utsendelse` som nye
# katalogbegreper. Sannhetskilden sa da to ting samtidig, og den som
# implementerer leser prosaen først.
#
# Regelen: skriver dokumentet en identifikator i maskinform, må maskinen ha
# den. Ordskiller er understrek — vanlig norsk prosa skriver ikke sånn, så
# mønsteret treffer nettopp de ordene som utgir seg for å være noe systemet
# kjenner. Sammensetninger med bindestrek (`axe-core`, `robots.txt`) er prosa
# og faller utenfor med vilje.
#
# Siffer teller med etter første bokstav (Codex P2 på #118, fjerde runde). Kun
# bokstaver i hvert ledd betydde at enhver maskinidentifikator med et tall i
# seg gikk usett forbi: den avviste klassen kunne kommet tilbake som
# `rådgivende_pluss_signert_utsendelse_v2`, og et oppfunnet `sha_256_digest`
# leste porten som ingenting. Første tegn må fortsatt være en bokstav, slik at
# `\b` har noe å feste seg i og tallgrupper i vanlig tekst ikke blir treff.
IDENT_RE = re.compile(r"\b[a-zæøå][a-zæøå0-9]*(?:_[a-zæøå0-9]+)+\b")




def _kjente_identifikatorer() -> set[str]:
    """Identifikatorer i maskinform som faktisk finnes i repoet — NÅ.

    To kilder, begge lest ut av repoet i stedet for skrevet av her: verdier
    migrasjonene skriver i apostrofer (registerets egne klasser, moduser og
    tilstander), og stammen i navnet på en sporet fil (spesifikasjonen navngir
    porter og verktøy, som `test_ui_kontrakt`).

    Migrasjonene er historikk (Codex P2 på #118, fjerde runde). En ren union
    over dem gjorde enhver verdi gyldig for alltid: strammet en senere
    migrasjon inn et CHECK-vilkår, sto den fjernede verdien igjen i lista fordi
    filen som innførte den fortsatt ligger der — og prosaen kunne fortsette å
    presentere en klasse registeret nettopp hadde sluttet å godta. Verdier
    registeret BINDER i et vilkår måles derfor mot gjeldende tilstand, og den
    leses av BASEN (`_registerenum()`): har verdien stått i et vilkår på
    kontraktkolonnene (`_noen_gang_bundet()`) og basen ikke godtar den nå, er
    den pensjonert og ute av lista. En verdi som aldri har stått i et CHECK
    (`alltid_stopp` er en modus, håndhevet i kode) berøres ikke — den har
    ingen registertilstand å falle ut av, og filstammene er gjeldende tilstand
    i seg selv, siden en slettet fil forsvinner fra `git ls-files`.

    Og verdiene leses ut av det migrasjonen SKRIVER, ikke ut av råteksten: en
    kommentar finnes ikke i et syntakstre, en melding er ÉN verdi uansett
    sitering, og en funksjonskropp er skrevet selv om `AS $$…$$` ikke kjører
    den. Se `_skrevne_verdier()` og prøvene på den under.
    """
    gjeldende = set().union(
        *(_registerenum(kol) for kol in KONTRAKTFELT.values()))
    pensjonert = set(_noen_gang_bundet()) - gjeldende
    ut: set[str] = set()
    for sql in sorted(MIGRASJONER.glob("*.sql")):
        ut.update(t for t in _skrevne_verdier(sql.read_text(encoding="utf-8"))
                  if IDENT_RE.fullmatch(t))
    ut -= pensjonert
    spor = subprocess.run(["git", "ls-files", "-z"], cwd=ROT,
                          capture_output=True, text=True, check=True)
    ut.update(Path(rel).stem for rel in spor.stdout.split("\0") if rel)
    return ut


@pg
def test_en_pensjonert_verdi_er_ute_av_de_kjente(monkeypatch):
    """Subtraksjonen som holder lista gjeldende må bevises, ikke antas.

    Historikken i dag er et rent superset (036 utvidet vilkåret uten å fjerne
    noe), så pensjonert-mengden er TOM — og en mekanisme ingen prøve driver,
    er stille verdiløs den dagen den trengs. Prøven setter derfor en verdi som
    beviselig er skrevet i migrasjonene (`alltid_stopp`, en modus) inn i
    «noen gang bundet»-mengden: basen godtar den ikke i kontraktkolonnene, så
    den SKAL regnes som pensjonert og falle ut av de kjente — mens en verdi
    basen fortsatt godtar (`ekstern_lesing`) står urørt.
    """
    ekte = _kjente_identifikatorer()
    assert {"alltid_stopp", "ekstern_lesing"} <= ekte, (
        "prøvens forutsetning røk: verdiene finnes ikke lenger i migrasjonene")
    monkeypatch.setattr(
        sys.modules[__name__], "_noen_gang_bundet",
        lambda: frozenset({"alltid_stopp", "ekstern_lesing"}))
    kjente = _kjente_identifikatorer()
    assert "alltid_stopp" not in kjente, (
        "en verdi registeret har sluttet å binde ble stående som kjent — "
        "pensjonert-subtraksjonen virker ikke")
    assert "ekstern_lesing" in kjente




@pytest.mark.parametrize("sql,star_igjen", [
    # En merknad skriver ingenting, og navnet i den finnes ikke av den grunn.
    ("-- en gang het den 'oppfunnet_klasse'\nSELECT 1;\n", False),
    ("/* en gang het den 'oppfunnet_klasse' */\nSELECT 1;\n", False),
    # Men en verdi i en funksjonsKROPP er skrevet av registeret, selv om
    # `AS $$…$$` bare definerer kroppen uten å kjøre den.
    ("CREATE FUNCTION f() RETURNS text LANGUAGE sql AS $$\n"
     "    SELECT 'oppfunnet_klasse';\n"
     "$$;\n", True),
    # Og en kommentar INNE i en slik kropp er fortsatt en kommentar.
    ("CREATE FUNCTION f() RETURNS text LANGUAGE sql AS $$\n"
     "    -- het 'oppfunnet_klasse' før\n"
     "    SELECT 'noe';\n"
     "$$;\n", False),
    # Det samme i en PL/pgSQL-kropp, der lesningen går en annen vei.
    ("CREATE FUNCTION f() RETURNS text LANGUAGE plpgsql AS $$\n"
     "BEGIN RETURN 'oppfunnet_klasse'; END $$;\n", True),
    ("CREATE FUNCTION f() RETURNS text LANGUAGE plpgsql AS $$\n"
     "BEGIN -- het 'oppfunnet_klasse' før\n"
     "  RETURN 'noe'; END $$;\n", False),
    # En TRIGGERFUNKSJON leses som alle andre. Den er formen libpg_query
    # skriver ugyldig JSON for, se `_plpgsql()`; uten reparasjonen der ville
    # verdien forsvunnet i stillhet — og med den 120 andre.
    ("CREATE FUNCTION f() RETURNS trigger LANGUAGE plpgsql AS $$\n"
     "BEGIN NEW.a := 'oppfunnet_klasse'; RETURN NEW; END $$;\n", True),
    # Kommentartegn inne i en STRENG er data, ikke starten på en kommentar.
    ("INSERT INTO t (a, b) VALUES ('a--b', 'oppfunnet_klasse');\n", True),
    # En doblet fnutt er en ESCAPE, ikke slutten på én verdi og starten på en
    # ny (Codex P2 på #118, syttende runde): meldingen her er ÉN literal, og
    # navnet i den står ikke for seg selv noe sted.
    ("DO $do$ BEGIN\n"
     "    RAISE EXCEPTION 'mangler ''oppfunnet_klasse''';\n"
     "END $do$;\n", False),
    ("SELECT 'det''s ''oppfunnet_klasse'' som mangler';\n", False),
    # Men en verdi som ER navnet, med en escapet nabo, står fortsatt.
    ("INSERT INTO t (a, b) VALUES ('det''s', 'oppfunnet_klasse');\n", True),
    # En ESCAPESTRENG er verdien escapen gir. Den ble gitt fra seg før, fordi å
    # tolke den betydde å skrive PostgreSQLs escaperegler av i Python; nå løser
    # den som EIER reglene dem opp, og da er verdien verdien.
    ("SELECT E'oppfunnet_klasse';\n", True),
    ("SELECT E'oppfunnet\\x5fklasse';\n", True),
    # Et dollarsitert spenn som ikke er en kropp, er ÉN tekst (Codex P2 på
    # #118, attende runde). Meldingen her nevner navnet, den skriver det ikke.
    ("DO $do$ BEGIN\n"
     "    RAISE EXCEPTION $$mangler 'oppfunnet_klasse'$$;\n"
     "END $do$;\n", False),
    # Og kommentartegn inne i en slik tekst er data: blankes de, blir en annen
    # verdi enn den registeret skriver stående igjen.
    ("INSERT INTO t (a) VALUES ($$oppfunnet_klasse -- ikke en kommentar$$);\n",
     False),
    # Men motsatt vei: en VERDI skrevet dollarsitert er skrevet, selv om det
    # ikke står en apostrof i den.
    ("INSERT INTO t (a) VALUES ($$oppfunnet_klasse$$);\n", True),
    ("INSERT INTO t (a) VALUES ($tagg$oppfunnet_klasse$tagg$);\n", True),
    # En `DO`-kropp er en kropp — verdiene i den er skrevet av registeret.
    ("DO $$ BEGIN\n"
     "    PERFORM 'oppfunnet_klasse';\n"
     "END $$;\n", True),
    # To fragmenter med linjeskift mellom seg er ÉN verdi (Codex P2 på #118,
    # attende runde), og formen brukes gjennomgående til lange meldinger.
    ("SELECT 'mangler '\n"
     "       'oppfunnet_klasse';\n", False),
    # Uten linjeskift skjøter ikke PostgreSQL — det er en syntaksfeil der.
    ("SELECT 'oppfunnet_klasse' 'og mer';\n", None),
    # Og en verdi som står alene på linja, står fortsatt.
    ("SELECT 'mangler',\n"
     "       'oppfunnet_klasse';\n", True),
])
def test_en_sqlkommentar_skriver_ingen_identifikator(sql, star_igjen):
    """Lista over kjente identifikatorer leses av det migrasjonen SKRIVER.

    Verdiene ble hentet ut av råteksten (Codex P2 på #118, femtende runde), og
    et maskinformet navn nevnt i en merknad havnet dermed i lista uten å finnes
    noe sted. Prosaen i sannhetskilden kunne så presentere en klasse ingen
    tabell har hørt om, og porten sa ingenting fordi navnet «finnes». Runde
    sytten og atten fant to former til av samme sak: den doblede apostrofen og
    den dollarsiterte meldingen.

    Lesningen er en annen enn enum-dommen i `_registerenum()`, og det er med
    vilje: der spør vi basen hva registeret HÅNDHEVER nå, her hva det SKRIVER. En
    funksjonskropp er skrevet selv om `AS $$…$$` ikke kjører den.

    `star_igjen=None` betyr at fragmentet ikke er gyldig SQL. PostgreSQL skjøter
    ikke to fragmenter uten linjeskift mellom seg, og da skal porten heller ikke
    gjøre det — men den skal si fra HØYT, ikke lese noe annet enn serveren.
    """
    if star_igjen is None:
        with pytest.raises(pglast.parser.ParseError):
            _skrevne_verdier(sql)
        return
    assert ("oppfunnet_klasse" in _skrevne_verdier(sql)) is star_igjen


_SKRIPTDEL_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.S)

# Etiketten dokumentteksten bærer. Den er det ene stykket som har en PLASS i
# fila, så porten melder linjenummer for den og feltnavn for resten.
DOKUMENTET = "dokumentet"


def _tekstene_i(verdi) -> list[str]:
    """Alle tekstene i en feltverdi — HELE VEIEN NED.

    Prosaen ble hentet fra en direkte streng eller et direkte listeledd (Codex
    P2). Leseren tillater lister og objekter av data i vilkårlig dybde, så en
    `flow:[['…']]` eller et objektfelt bar prosa porten aldri fikk se, og en
    oppfunnet maskinidentifikator kunne stå der med grønn port. Det som samles
    inn må gå like dypt som det som er lov å skrive.

    NØKLENE er ikke med. `kl`/`rev` er maskinform på nøkkelposisjon og vokter
    seg selv i port 9; det som måles her er hva kilden SIER.
    """
    if isinstance(verdi, str):
        return [verdi]
    if isinstance(verdi, list):
        return [t for v in verdi for t in _tekstene_i(v)]
    if isinstance(verdi, dict):
        return [t for v in verdi.values() for t in _tekstene_i(v)]
    return []


def _prosastykker(kilde: Path = None) -> list[tuple[str, str]]:
    """[(hvor, tekst)] — alt sannhetskilden SIER til den som skal bygge.

    To kilder, og grensen mellom dem er en grense i dokumentet, ikke i en
    skanners ordliste:

      * DOKUMENTTEKSTEN, alt utenfor `<script>`. Det er der regresjonen i
        tredje runde sto: endringsloggen forklarte `dokumentbehandling` og
        `rådgivende_pluss_signert_utsendelse` som to nye katalogbegreper etter
        at modulposten var rettet.
      * MODULPOSTENES egne felt, lest av `les_katalog.mjs`. `merknad`, `guard`,
        `goal`, `input`, `accept` og `flow` er prosa i katalogen, og `kl`/`rev`
        er maskinform på nøkkelposisjon.

    KJØRENDE JAVASCRIPT er ikke prosa (Codex P2 på #118, åttende runde). Porten
    leste hele fila, også prototypens `<script>`, og et helt vanlig JS-navn —
    `const filter_state = {}` — ble meldt som en oppfunnet registerklasse. Et
    variabelnavn i kode PÅSTÅR ingenting om registeret; det er navnet på en
    binding som lever og dør i denne fila.

    Skillet ble den gangen trukket av JS-skanneren: kode maskert bort, fnutter
    og kommentarer stående. Skanneren er borte, og med den nitten runders
    former den ikke kjente. Grensen står i stedet der den kan sies rett ut, og
    det er forskjellen fra en skanner med hull: UI-KODEN ER UTENFOR PORTEN —
    også strengene og kommentarene i den. Den er dokumentasjon av prototypens
    kode, skrevet til den som vedlikeholder den koden, og navnene den bruker er
    dens egne bindinger og repoets egne filnavn. Det kildens LESER skal bygge
    etter, står i dokumentteksten og i modulpostene, og begge måles her.

    Grensen er en påstand hvem som helst kan etterprøve ved å lese den, ikke en
    mengde grammatikkformer som stilltiende ikke ble kjent igjen. Skriver noen
    en oppfunnet klasse i en kommentar i UI-koden, sier porten ingenting — og
    det er sagt her, ikke oppdaget om atten runder.
    """
    kilde = kilde or KILDE
    tekst = kilde.read_text(encoding="utf-8")
    # Skriptet maskeres bort, men linjeskiftene blir stående: linjenummeret
    # porten melder skal peke på linja i fila, ikke i et utsnitt.
    biter, forrige = [], 0
    for del_ in _SKRIPTDEL_RE.finditer(tekst):
        biter.append(tekst[forrige:del_.start(1)])
        biter.append("\n" * tekst.count("\n", del_.start(1), del_.end(1)))
        forrige = del_.end(1)
    biter.append(tekst[forrige:])
    stykker: list[tuple[str, str]] = [(DOKUMENTET, "".join(biter))]
    for post in _katalogposter(kilde):
        for felt, verdi in sorted(post.items()):
            for tekst in _tekstene_i(verdi):
                stykker.append((f"M-{post['n']}.{felt}", tekst))
    return stykker


@pg
def test_ingen_oppfunne_identifikatorer_i_sannhetskilden():
    """Maskinform i spesifikasjonen må peke på noe som finnes.

    Codex P2 på PR #118, tredje runde: modulposten for M-57 var rettet til
    `krever_outbox`/`irreversibel`, men endringsloggen i samme fil forklarte
    fortsatt `dokumentbehandling` og `rådgivende_pluss_signert_utsendelse` som
    to nye katalogbegreper. Port 9 så det ikke — den leser postene, og dette
    sto i prosaen. En oppfunnet klasse trenger ikke stå i et `kl`-felt for å
    gjøre skade; den trenger bare å stå i dokumentet alle leser før de bygger.

    Porten sier ikke at spesifikasjonen må slutte å forklare. Den sier at en
    forklaring ikke får låne maskinens skrivemåte for noe maskinen ikke har:
    prosa om hvorfor en klasse ble valgt skrives som prosa, og en identifikator
    kommer inn ved å finnes — i en migrasjon eller som en fil i repoet.

    GRENSEN, sagt rett ut: understreken er signalet, så en oppfunnet klasse som
    er ETT ord (`dokumentbehandling` var det) går forbi her. Å skille den fra
    vanlig norsk krever at porten leser hva setningen PÅSTÅR, og en slik
    prosaregel ville enten avvist legitim dokumentasjon av en klasse som
    faktisk kom (036 la `ekstern_lesing` til) eller vært lett å skrive seg
    rundt. Det ettordstilfellet vokter port 9 der det gjør skade — i `kl`/`rev`
    på selve posten.

    KJØRENDE JAVASCRIPT er ikke prosa (Codex P2 på #118, åttende runde). Porten
    leste hele fila, også prototypens `<script>`, og et helt vanlig JS-navn —
    `const filter_state = {}` — ble meldt som en oppfunnet registerklasse. Et
    variabelnavn i kode PÅSTÅR ingenting om registeret; det er navnet på en
    binding som lever og dør i denne fila. Porten leser derfor
    `_prosastykker()`: dokumentteksten, og modulpostenes egne felt — der kilden
    faktisk sier noe til den som skal bygge.

    MUTASJONEN SOM DREPER DENNE: la porten lese modulpostene i stedet for hele
    prosaen. Da vokter den det port 9 allerede vokter, og dokumentteksten — som
    er der regresjonen faktisk sto — er igjen uten port.
    """
    avvik = _oppfunne_identifikatorer()
    assert not avvik, (
        f"identifikatorer i {KILDE.name} som ikke finnes i registeret eller "
        "som fil: " + "; ".join(f"«{ident}» ({sted})"
                                for ident, sted in sorted(avvik.items())))


@pg
@pytest.mark.parametrize("fra,til,ident,sted", [
    # DOKUMENTTEKSTEN. Dette er regresjonen fra tredje runde, satt tilbake:
    # modulposten er rettet, endringsloggen står igjen med den avviste klassen.
    ("<b>krever_outbox</b>", "<b>rådgivende_pluss_signert_utsendelse</b>",
     "rådgivende_pluss_signert_utsendelse", "linje"),
    # MODULPOSTENS FELT — prosafeltet, maskinfeltet og et ledd i en liste.
    ("Byggerekkefølge: etter M-16", "Klassen er oppfunnet_klasse. Etter M-16",
     "oppfunnet_klasse", "M-57.merknad"),
    ('"kl":"krever_outbox"', '"kl":"oppfunnet_klasse"',
     "oppfunnet_klasse", "M-57.kl"),
    ("'Importerer standardpolicy", "'Kjører i modus alltid_stoppp",
     "alltid_stoppp", "M-1.flow"),
    # NEDE I FELTET. Leseren tillater lister og objekter i vilkårlig dybde, og
    # prosa som står der er prosa (Codex P2).
    ("'Importerer standardpolicy for kundens bransje og plan'",
     "[{steg:'Kjører i modus dypt_oppfunnet'}]",
     "dypt_oppfunnet", "M-1.flow"),
])
def test_prosaporten_maaler_dokumentet_og_modulpostene(tmp_path, fra, til,
                                                       ident, sted):
    """Begge halvdelene av `_prosastykker()` skal faktisk måles.

    Porten leste før hele fila gjennom én maskering, og da var det ett sted å
    ta feil. Nå er kilden to: dokumentteksten utenfor `<script>`, og feltene
    `les_katalog.mjs` gir fra seg. Faller den ene ut — en sammenskjøting som
    glemmer et ledd i en liste, en maskering som tar for mye — er porten grønn
    på halve dokumentet uten at noe sier fra.

    Prøvene setter derfor regresjonen tilbake i hver av dem, og krever at
    porten melder BÅDE navnet og hvor det står. Stedet er med fordi det er det
    som gjør meldingen brukbar: et linjenummer i dokumentet, et feltnavn i en
    modulpost.
    """
    kilde = tmp_path / KILDE.name
    tekst = KILDE.read_text(encoding="utf-8")
    assert fra in tekst, f"fant ikke «{fra}» i kilden — den har endret form"
    kilde.write_text(tekst.replace(fra, til, 1), encoding="utf-8")
    avvik = _oppfunne_identifikatorer(kilde)
    assert ident in avvik, (
        f"porten så ikke «{ident}» i {sted} — halve prosaen er umålt")
    assert avvik[ident].startswith(sted), (
        f"porten melder «{ident}» i {avvik[ident]}, ikke i {sted}")


def _oppfunne_identifikatorer(kilde: Path = None) -> dict[str, str]:
    """{identifikator: hvor} for maskinform i kilden som ikke finnes."""
    kjente = _kjente_identifikatorer()
    avvik: dict[str, str] = {}
    for hvor, tekst in _prosastykker(kilde):
        for treff in IDENT_RE.finditer(tekst):
            if treff.group(0) in kjente:
                continue
            sted = hvor
            if hvor == DOKUMENTET:
                sted = f"linje {tekst.count(chr(10), 0, treff.start()) + 1}"
            avvik.setdefault(treff.group(0), sted)
    return avvik


# Markøren som innfører et tall som modulnummer, hvor som helst i leddet. Den
# må stå fritt: uten det venstre gjerdet ville «ARM-16» og «GSM-2» båret hver
# sin M- inne i et ord og laget kanter av maskinvarenavn.
_MODULMARKOR_RE = re.compile(
    r"(?<![0-9A-Za-zÆØÅæøå_-])(?:M-|[Mm]odul(?:ene)?\s+)(?=\d)")
# Et tall eller et intervall. Et ledd som BARE er dette (bortsett fra
# sluttpunktum) kan være neste ledd i en påbegynt oppramsing.
_TALL_RE = re.compile(r"\d+(?:\s*[–-]\s*(\d+))?")


def _modulreferanser(dep: str) -> set[int]:
    """Modulnumrene en dep-streng peker på — `M-14`, `modul 24` og intervaller
    som `1–2` eller `13–14`.

    Markøren `M-`/`modul` var VALGFRI (Codex P2 på #118, sjuende runde), så
    ethvert tall i teksten ble et modulnummer. `dep` er prosa og navngir også
    infrastruktur: «PostgreSQL 16» ble til M-16, og en fase 1-modul som nevnte
    den fikk en oppdiktet kant til en fase 2-modul og felte faseporten på noe
    som ikke står i kilden. Connectorversjoner («Peppol 3», «HTTP 2») ville gitt
    flere av samme sort.

    Et tall teller derfor bare når det er INNFØRT som modul: enten med sin egen
    markør, eller som et senere ledd i en oppramsing en markør åpnet — «Modul 1,
    5, 9 og HRIS-connector» er tre moduler og en connector. Oppramsingen brytes
    av det første leddet som ikke er rene tall, så «Modul 3, HRIS-connector og
    lønnssystem» ikke drar noe med seg videre. Alt annet (connectorer,
    infrastruktur, «landpakke») faller utenfor: de har ingen fase å bryte.

    Markøren ble samtidig ANKRET i starten av leddet (Codex P2 på #118, åttende
    runde), og da forsvant hele setningsformen: «Kjører via M-16» og «Avhenger
    av modul 16» ga ingen kant i det hele tatt. En modul i fase 1 kunne dermed
    navngi en modul i fase 3 i klartekst mens porten sto grønn — nøyaktig det
    hullet porten finnes for. Kravet er at tallet er innført som modul, ikke at
    innføringen står først, så markøren SØKES nå fram hvor som helst i leddet.
    Den må stå fritt, ellers ville «ARM-16» og «GSM-2» blitt modulnumre.

    Funksjonen SILER IKKE mot katalogen (Codex P2 på #118, niende runde). Den
    returnerte før `ut & kjente`, og da forsvant en markert referanse til en
    modul som ikke finnes — `M-58`, en tastefeil eller en modul noen planla
    men aldri tok opp — stille ut av porten. Det er den verste formen for
    grønn: dep peker på noe som ikke kan tildeles en fase, og porten sier
    ingenting. Siling ga mening da markøren var valgfri og «PostgreSQL 16»
    ville blitt meldt som M-16; nå er infrastrukturtall allerede ute, så det
    som står igjen er referanser noen har MENT som moduler. Kalleren melder
    dem som brudd.

    MUTASJONEN SOM DREPER DENNE: gjør markøren valgfri igjen. Da er «PostgreSQL
    16» en kant til M-16 på nytt, og porten faller på infrastruktur.
    """
    ut: set[int] = set()
    i_liste = False
    for ledd in re.split(r",|\bog\b", dep):
        ledd = ledd.strip()
        if not ledd:
            continue
        markerte = [_TALL_RE.match(ledd, m.end())
                    for m in _MODULMARKOR_RE.finditer(ledd)]
        if markerte:
            i_liste = True
        else:
            treff = _TALL_RE.fullmatch(ledd.rstrip("."))
            if not (i_liste and treff):
                i_liste = False
                continue
            markerte = [treff]
        for treff in markerte:
            forste = int(re.match(r"\d+", treff.group(0)).group(0))
            siste = int(treff.group(1)) if treff.group(1) else forste
            ut.update(range(forste, siste + 1))
    return ut


def test_ingen_modul_avhenger_av_en_senere_fase():
    """Faseporten må være oppfyllelig (Codex P2 på PR #99).

    Spesifikasjonen sier at hver modul i en fase må være komplett før neste
    fase starter. En modul som avhenger av en modul i en SENERE fase gjør den
    regelen umulig å følge: ingen av de to kan bygges først. Det er ikke en
    smakssak i teksten — det er en utrullingsrekkefølge som ikke finnes.

    Codex fant M-53 → M-43. To til lå i samme fil (M-48 → M-23, M-38 → M-31),
    usett, fordi ingen port leste dep-feltene. Denne gjør det.

    En referanse til en modul som IKKE finnes meldes samme sted (Codex P2 på
    #118, niende runde). `M-58` kan ikke tildeles en fase, så porten kan ikke
    avgjøre om rekkefølgen holder — og «kan ikke avgjøres» er ikke det samme
    som «i orden». Før ble den silt bort i stillhet.

    MUTASJONEN SOM DREPER DENNE: sett `>` til `>=`. Da ville en avhengighet
    innenfor samme fase blitt et brudd — og det er den ikke: fasen er
    rekkefølgens grovkorn, modulene i den bygges i en rekkefølge fasen ikke
    bestemmer.
    """
    moduler = _moduler_fra_kilden()
    assert len(moduler) == MODULER, (
        f"leste {len(moduler)} moduler med fase og dep, forventet {MODULER}")
    ukjente = [f"M-{n} peker på M-{r}, som ikke finnes i katalogen"
               for n, d in sorted(moduler.items())
               for r in sorted(_modulreferanser(d["dep"]))
               if r not in moduler]
    assert not ukjente, "dep peker utenfor katalogen: " + "; ".join(ukjente)
    brudd = [f"M-{n} (fase {d['fase']}) avhenger av "
             f"M-{r} (fase {moduler[r]['fase']})"
             for n, d in sorted(moduler.items())
             for r in sorted(_modulreferanser(d["dep"]))
             if moduler[r]["fase"] > d["fase"]]
    assert not brudd, "uoppfyllelig utrullingsrekkefølge: " + "; ".join(brudd)


# Filer som ikke skal måles mot dagens versjon: `docs/pr/` og
# `docs/beslutninger/` ER historikk — en PR-beskrivelse eller en ADR skal si
# hva som gjaldt DA, ikke skrives om når versjonen bumpes. Arkivet i
# `prototype/` er gamle utgaver på disk. `.git` er ikke tekst.
HISTORIKK = ("docs/pr/", "docs/beslutninger/", "prototype/", ".git/")
SPEC_RE = re.compile(r"disponit-prototype-v(\d+)\.html")

# Regresjonen porten finnes for var bare DELVIS et filnavn (Codex P2 på #118):
# den obligatoriske arbeidsflyten krevde utkast «mot prototypen» med utgaven
# skrevet i klartekst ved siden av ordet, ikke som filnavn. Et mønster bundet
# til filnavnet leser rett forbi den formen, og da er porten grønn mens
# instruksen fortsatt peker på noe som er slettet.
#
# Mellomrommet er hele avgrensningen mot arkivet: de gamle utgavene på disk
# heter `…-prototype-v7.html` med bindestrek, og de skal fortsatt kunne
# navngis. Prosa skriver ordet, mellomrom, versjonen.
PROSA_RE = re.compile(r"(?i)prototypen?\s+v(\d+)")


def test_ingen_peker_paa_en_slettet_spesifikasjon():
    """Hver henvisning til spesifikasjonsutgaven må treffe den som finnes.

    Codex P2 på PR #118: v9 gjorde spesifikasjonen til eneste sannhetskilde og
    slettet v8-fila, men den OBLIGATORISKE arbeidsflyten krevde fortsatt utkast
    og tester mot v8 (`docs/README-arbeidsflyt.md`, `docs/RUTINER.md`), og
    PR-malen krevde akseptansemapping mot v8. En implementasjons-PR for M-57
    kunne umulig oppfylle det: kriteriene finnes bare i v9. Instruksen pekte
    altså på en fil som ikke er der — og ingenting knakk, for prosa kompilerer
    ikke.

    Kuren i selve dokumentene er å slutte å bake versjonsnummeret inn i
    instruksen: de peker nå på `docs/spesifikasjon/`, som alltid inneholder
    gjeldende utgave. Denne porten dekker resten — stedene som med rett MÅ
    navngi utgaven (README, STRUKTUR, generatoren, denne testen), og fanger
    neste versjonsbump som glemmer ett av dem.

    Den måler BEGGE skrivemåtene, for regresjonen sto i begge: filnavnet, og
    ordet «prototype» med utgaven skrevet ved siden av seg (Codex P2, andre
    runde). Et mønster som bare kjente filnavnet ville latt den gamle
    obligatoriske formuleringen komme rett tilbake, grønt.

    Og den måler MAPPA før den måler teksten (Codex P2, sjette runde). Alt over
    leser innholdet i sporede filer; ingenting leste filNAVNENE i
    `docs/spesifikasjon/`. Legges v10 til uten at v9 slettes, blir porten
    stående grønn — en gammel spesifikasjon trenger ikke nevne sitt eget
    filnavn i teksten sin, så ingen henvisning peker feil. Men instruksene
    peker nå på MAPPA nettopp fordi den skal inneholde gjeldende utgave og bare
    den; med to filer der er «gjeldende utgave» ikke lenger et entydig svar, og
    kuren i dokumentene slutter å virke. Arkivet i `prototype/` er unntatt: der
    er flere utgaver hele poenget.

    MUTASJONEN SOM DREPER DENNE: la porten godta et hvilket som helst
    versjonsnummer. Da er den bare en stavekontroll for filnavnet, og det var
    aldri feilen — feilen var at nummeret pekte forbi fila.
    """
    gjeldende = SPEC_RE.search(KILDE.name)
    assert gjeldende, f"KILDE_REL navngir ikke en versjonert fil: {KILDE.name}"
    versjon = gjeldende.group(1)

    spor = subprocess.run(["git", "ls-files", "-z"], cwd=ROT,
                          capture_output=True, text=True, check=True)
    sporede = [r for r in spor.stdout.split("\0") if r]

    utgaver = sorted(r for r in sporede
                     if r.startswith(SPEKMAPPE) and SPEC_RE.search(Path(r).name))
    assert utgaver == ["/".join(KILDE_REL)], (
        f"{SPEKMAPPE} skal inneholde NØYAKTIG én spesifikasjonsutgave, og den "
        f"skal være sannhetskilden {'/'.join(KILDE_REL)} — fant: "
        f"{utgaver or 'ingen'}. Er en ny utgave lagt til, skal den gamle "
        f"slettes; skal den bevares, hører den hjemme i arkivet {ARKIV.name}/.")

    avvik = []
    for rel in sporede:
        if rel.startswith(HISTORIKK):
            continue
        sti = ROT / rel
        if not sti.is_file():
            continue
        tekst = sti.read_text(encoding="utf-8", errors="ignore")
        for monster in (SPEC_RE, PROSA_RE):
            for treff in monster.finditer(tekst):
                if treff.group(1) != versjon:
                    linje = tekst.count("\n", 0, treff.start()) + 1
                    avvik.append(
                        f"{rel}:{linje} peker på «{treff.group(0)}»")
    assert not avvik, (
        f"henvisninger til en spesifikasjonsutgave som ikke finnes (gjeldende "
        f"er v{versjon}, {KILDE.name}): " + "; ".join(avvik))


@pytest.mark.parametrize("sprak", sorted(LOCALER))
def test_hvert_navn_finnes_paa_begge_sprak(sprak):
    katalog, omrader = _katalog_js()
    d = json.loads(LOCALER[sprak].read_text(encoding="utf-8"))
    for m in katalog:
        nokkel = f"site.katalog.m{m['n']}.navn"
        assert d.get(nokkel), f"{nokkel} mangler i {sprak}.json"
    for o in omrader:
        nokkel = f"site.omrade.{o['id']}"
        assert d.get(nokkel), f"{nokkel} mangler i {sprak}.json"
