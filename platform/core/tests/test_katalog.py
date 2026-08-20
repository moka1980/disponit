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
                 lest ut av CHECK-vilkårene i migrasjonene.
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
  * MIGRASJONENE leses av `pglast`, altså libpg_query: PostgreSQLs egen
    grammatikk, samme parser som serveren som skal kjøre dem.

Begge er FAIL-CLOSED. Mangler node eller pglast, er porten rød — aldri hoppet
over. En port som hopper over seg selv er grønn på noe ingen har lest.
"""
import functools
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

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


def _uleselige_felt(post: dict) -> list[str]:
    """Feltene i posten som ikke bærer data. Se `IKKE_DATA`."""
    return sorted(f for f, v in post.items()
                  if isinstance(v, dict) and IKKE_DATA in v)


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


def test_to_kataloger_stopper_leseren(tmp_path):
    """To `const M` er en redeklarasjon, og motoren avviser den selv.

    Porten og generatoren hadde hver sin regel for at ankeret skulle stå
    nøyaktig én gang, og hver sin måte å skille erklæringen fra de samme
    tegnene i en streng. Motoren trenger ingen regel: `const M` to ganger i
    samme skript er en SyntaxError ved KOMPILERING — også når den andre står i
    kode som aldri kjører, slik den gjør her, bak et `document`-kall som
    kaster.
    """
    with pytest.raises(AssertionError) as feil:
        _les_proveside(tmp_path, "const M = [{n:58,name:'D',area:'X',p:1}];")
    assert "gyldig JavaScript" in str(feil.value), str(feil.value)


@pytest.mark.parametrize("element", [
    "42",
    "'en streng'",
    "null",
    "[{n:58}]",
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
    # Skrivemåten forsvinner i lesningen: alle tre gir egenskapen `kl`.
    (r"kl:'krever_outbox'", True),
    ("['k' + 'l']:'krever_outbox'", True),
    ("get kl(){return 'krever_outbox'}", True),
    # Men en verdi som ikke er DATA, er ikke noe katalogen kan bære.
    ("kl:/krever_outbox/", False),
    ("kl:() => 'krever_outbox'", False),
    ("kl:new Date(0)", False),
])
def test_leseren_krever_at_en_feltverdi_er_data(tmp_path, felt, lesbar):
    """Katalogen skal kunne leses av mer enn nettleseren.

    Nøkkelen er motorens sak nå, og den leser alle skrivemåtene likt. VERDIEN
    er porten sin: en funksjon, et mønster eller en dato er ingen katalogverdi,
    og et felt ingen kan lese er et felt ingen kan kontrollere. Leseren merker
    det i stedet for å hoppe over det, se `IKKE_DATA`.
    """
    sti = tmp_path / "prove.html"
    sti.write_text(f"<html><script>const M = [{{n:1,name:'En',area:'X',p:1,"
                   f"{felt}}}];</script></html>", encoding="utf-8")
    post = _katalogposter(sti)[0]
    assert (not _uleselige_felt(post)) is lesbar, post


# Kontraktklassene katalogen bruker er de SAMME feltene modulregisteret lagrer,
# og registeret håndhever dem med CHECK-vilkår. Enumene leses derfor ut av
# migrasjonene, ikke skrevet av her: en kopi i testen ville vært nok en kilde
# som kan drive fra databasen — akkurat feilen porten finnes for å hindre.
# Migrasjonene kjøres i nummerrekkefølge, og en senere kan både UTVIDE et
# vilkår (036 la `ekstern_lesing` til `sideeffektklasse`) og stramme det inn,
# så det siste treffet for samme tabell og kolonne er det som gjelder.
MIGRASJONER = ROT / "platform" / "core" / "db" / "migrations"
KONTRAKTFELT = {"kl": "sideeffektklasse", "rev": "reversibilitet"}
# Tabellen kontrakten faktisk lagres i, opprettet i 014 og utvidet i 036. Den
# står her fordi enumoppslaget skal binde seg til den, ikke til kolonnenavnet
# på tvers av skjemaet — se `_registerenum()`.
MODULKONTRAKT = "modulkontrakt"


# ---------------------------------------------------------------------------
# MIGRASJONENE, LEST AV POSTGRESQLS EGEN GRAMMATIKK
#
# Migrasjonsmappa er den ENESTE historikken i repoet som ikke kan redigeres:
# en fil som er kjørt står for alltid, også når en senere fil erstatter det den
# innførte. Alt annet — kildekode, maler, katalogen — er gjeldende tilstand,
# fordi en fjernet verdi forsvinner med redigeringen. Derfor må registerets
# gjeldende tilstand REGNES ut av migrasjonene, ikke leses som en union.
#
# Regningen sto på tjue regexer og en håndskrevet SQL-skanner: strengformer,
# dollarsitering, nøstede blokkommentarer, `DO`-kropper, `IF … THEN`, parenteser
# som måtte telles, tabellnavn bak `ONLY`. Ti runder med Codex-review på #118 var
# ti SQL-former skanneren ikke hadde — og hver av dem gikk samme vei: et vilkår
# porten ikke SÅ, ble et vilkår den ikke snittet med, og katalogporten kunne
# godta en kontraktklasse databasen avviser.
#
# Eier avgjorde saken 20/8: bytt lesning, ikke legg til former. Migrasjonene
# leses nå av `pglast`, altså av libpg_query — PostgreSQLs EGEN parser, samme
# grammatikk som serveren som skal kjøre filene. «En form grammatikken har og
# lesningen ikke hadde» er da umulig per konstruksjon, ikke usannsynlig.
#
# Det som følger med av seg selv, uten en linje kode her:
#
#   * KOMMENTARER finnes ikke i et syntakstre. Både `--` og nøstede `/* … */`,
#     og ingen av dem inne i en streng.
#   * STRENGER er verdier, ikke setninger. `'ALTER TABLE … DROP CONSTRAINT r'`
#     skrevet inn i en loggtabell er tekst, og `E'…'`, `$$…$$`, doblede
#     apostrofer og fragmenter skjøtt over flere linjer er ÉN verdi hver, med
#     escapene løst opp av den som eier reglene.
#   * PARENTESER, siterte navn, `ONLY`, `TEMP`, `IF NOT EXISTS` — alt sammen
#     former grammatikken har, og de er ikke lenger noe porten «kjenner».
#
# Igjen står de spørsmålene som faktisk er porten sine, og de er semantiske:
# hvilket vilkår et slipp treffer, hva et sammensatt predikat betyr, og hva en
# betinget gren i en `DO`-kropp gjør med tilstanden. De står under.
try:
    import pglast
except ModuleNotFoundError:  # pragma: no cover - meldingen ER porten her
    raise ModuleNotFoundError(
        "pglast mangler — migrasjonene leses av PostgreSQLs egen grammatikk "
        "(libpg_query), og uten den kan registerets enum ikke leses i det hele "
        "tatt. Den står i requirements-dev.txt; en port som hopper over seg "
        "selv her ville vært grønn på et uleste register.")

# Verdien en kolonne får når porten ikke kan lese vilkåret som binder den.
# Backstrek kan ikke være en enumverdi skrevet rett fram, så den kan ikke
# kollidere med en ekte.
ULESELIG_SQL = "\\"

# Nodetypene i treet som er en KOLONNE, et VILKÅR og en VERDI. Navnene er
# libpg_querys egne.
_CHECK = "CONSTR_CHECK"


def _navn(rangevar) -> str:
    """Tabellidentiteten bak et navn slik migrasjonen skrev det.

    Grammatikken har alt skilt skjema fra tabell og foldet den usiterte formen
    ned, så det eneste som står igjen er at `public` er STANDARDSKJEMAET og
    derfor ikke en del av identiteten: 026 oppretter `varsel`, 035 endrer
    `public.varsel`, og det er samme tabell. Uten dette ville «siste vilkår
    gjelder» sluttet å gjelde på tvers av de to skrivemåtene. Et ANNET skjema
    er derimot en del av identiteten og blir stående.
    """
    if rangevar.schemaname and rangevar.schemaname != "public":
        return f"{rangevar.schemaname}.{rangevar.relname}"
    return rangevar.relname


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


# En hendelse i en migrasjon: enten et vilkår som legges på, eller et som
# slippes. `betinget` er sant når setningen står bak en gren i en `DO`-kropp.
_LEGG_PA, _SLIPP = "legg på", "slipp"


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


def _hendelsene(sql: str) -> list[tuple]:
    """[(slag, tabell, navn, uttrykk, betinget[, kolonne])] i kjørerekkefølge."""
    ut: list[tuple] = []
    for stmt, tekst in _setningene(sql):
        _lesstatement(stmt, tekst, ut, False)
    return ut


def _lesstatement(stmt, tekst: str, ut: list, betinget: bool) -> None:
    """Legg hendelsene i `stmt` til i `ut`, i rekkefølge."""
    if isinstance(stmt, pglast.ast.CreateStmt):
        tabell = _navn(stmt.relation)
        for element in stmt.tableElts or ():
            _lesvilkar(element, tabell, ut, betinget)
        return
    if isinstance(stmt, pglast.ast.AlterTableStmt):
        tabell = _navn(stmt.relation)
        for cmd in stmt.cmds or ():
            if cmd.subtype == pglast.enums.AlterTableType.AT_DropConstraint:
                ut.append((_SLIPP, tabell, cmd.name, None, betinget))
            else:
                _lesvilkar(cmd.def_, tabell, ut, betinget)
        return
    if isinstance(stmt, pglast.ast.DoStmt) and _kroppen(stmt)[1] == "plpgsql":
        _leskropp(tekst, ut, betinget)


def _lesvilkar(node, tabell: str, ut: list, betinget: bool,
               kolonne: str | None = None) -> None:
    """Et CHECK-vilkår i en kolonne- eller tabelldefinisjon.

    `kolonne` er satt når vilkåret står PÅ en kolonne. Det avgjør navnet
    PostgreSQL gir et vilkår ingen har navngitt, se `_tildelt_navn()`.
    """
    if isinstance(node, pglast.ast.ColumnDef):
        for c in node.constraints or ():
            _lesvilkar(c, tabell, ut, betinget, node.colname)
        return
    if isinstance(node, pglast.ast.Constraint) \
            and node.contype == pglast.enums.ConstrType.CONSTR_CHECK:
        ut.append((_LEGG_PA, tabell, node.conname, node.raw_expr, betinget,
                   kolonne))


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


def _leskropp(setning: str, ut: list, betinget: bool) -> None:
    """Hendelsene i en PL/pgSQL-kropp, med grener merket som betinget.

    En `DO $$ … $$` KJØRER kroppen sin i det migrasjonen går, og repoets
    konvensjon er nettopp å pakke betinget DDL slik — 035 §6 legger
    `varsel_art_chk` på `public.varsel` inne i en `DO`-blokk. Kroppen er
    PL/pgSQL, ikke SQL, og leses derfor av `parse_plpgsql`: grammatikken skiller
    selv en gren fra en setning som alltid kjører, og både `IF … THEN`,
    `ELSIF`, `ELSE`, løkker og unntakshåndtering faller ut som det de er.

    Det porten trenger å vite, er om setningen er KJENT kjørt. En betingelse er
    en spørring som først avgjøres når migrasjonen kjører, så svaret er ukjent —
    og da velger `_registerets_enums()` den tolkningen som aldri utvider.

    DYNAMISK DDL — `EXECUTE format(…)` — er ikke lesbar for noen tekstlesning,
    og gis fra seg med vilje. `_registerenum()` sier fra hvis kolonnen katalogen
    måles mot ender opp uten vilkår.
    """
    for spørring, i_gren in _plpgsql_setninger(
            _plpgsql(setning), betinget):
        for indre, indretekst in _setningene(spørring):
            _lesstatement(indre, indretekst, ut, i_gren)


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


# Setningstypene og feltene der kroppen KJØRER. Alt annet er en gren.
#
# Lista er snudd med vilje: en ukjent konstruksjon skal regnes som betinget,
# ikke som kjørt. Tar porten feil den veien, blir svaret SMALERE enn databasen
# — en verdi avvises som databasen godtar, og det stopper CI og blir rettet.
# Motsatt vei ville et slipp inne i en konstruksjon porten ikke kjente blitt
# regnet som utført, vilkåret forsvunnet ut av snittet, og katalogporten
# sluppet inn en klasse PostgreSQL avviser — i stillhet.
_KJORER = {
    "PLpgSQL_function": ("action",),
    "PLpgSQL_stmt_block": ("body",),
}


def _kjorende_felt(navn: str, innhold: dict) -> tuple:
    """Feltene i denne noden som ALLTID kjører. Se `_KJORER`.

    En blokk med `EXCEPTION`-håndterer er unntaket (Codex P2 på #118,
    tjueførste runde). PostgreSQL kjører en slik blokk i sitt eget
    underpunkt, og fanges en feil, RULLES ALT kroppen rakk å gjøre TILBAKE før
    håndtereren kjører — også DDL. Kroppen ble regnet som ubetinget, så et
    slipp inne i en slik blokk fjernet et vilkår databasen beholder: står et
    videre vilkår igjen, kan katalogporten slippe inn en klasse PostgreSQL
    avviser.

    Om kroppen fullførte kan bare avgjøres når migrasjonen kjører, så svaret er
    ukjent — og da er kroppen betinget, som alt annet vi ikke vet utfallet av.
    En blokk UTEN håndterer ruller ikke tilbake noe: da stopper feilen hele
    migrasjonen, og ingenting av den gjelder uansett.
    """
    if navn == "PLpgSQL_stmt_block" and innhold.get("exceptions"):
        return ()
    return _KJORER.get(navn, ())


def _plpgsql_setninger(node, betinget: bool):
    """(SQL-tekst, betinget) for hver setning i PL/pgSQL-treet.

    En setning er BETINGET når den står bak en gren: `IF … THEN`, `ELSIF`,
    `ELSE`, en løkke, en unntakshåndterer — eller i en blokk som kan rulles
    tilbake av sin egen `EXCEPTION`. Grammatikken skiller dem selv, så porten
    trenger ingen ordliste over hvordan de skrives — bare å vite hvilke felt
    som ALLTID kjører, se `_kjorende_felt()`.
    """
    if isinstance(node, list):
        for x in node:
            yield from _plpgsql_setninger(x, betinget)
        return
    if not isinstance(node, dict):
        return
    for navn, innhold in node.items():
        if navn == "PLpgSQL_stmt_execsql":
            spørring = (innhold.get("sqlstmt") or {}).get("PLpgSQL_expr", {})
            if spørring.get("query"):
                yield spørring["query"], betinget
            continue
        if not isinstance(innhold, dict):
            yield from _plpgsql_setninger(innhold, betinget)
            continue
        kjorer = _kjorende_felt(navn, innhold)
        for felt, verdi in innhold.items():
            yield from _plpgsql_setninger(verdi, betinget or felt not in kjorer)


def _registerets_enums(
        mappe: Path | None = None
) -> tuple[dict[tuple[str, str], set[str]], set[str]]:
    """(gjeldende verdier per (tabell, kolonne), verdier bundet noen gang).

    Migrasjonene leses i nummerrekkefølge, og hvert `CHECK` og hvert `DROP
    CONSTRAINT` er en HENDELSE som endrer hvilke vilkår som står igjen. Et
    vilkår kan både UTVIDE (036 la `ekstern_lesing` til `sideeffektklasse`) og
    stramme inn, og det kan FJERNES: et slipp uten et nytt vilkår etter seg lot
    verdiene fra forrige migrasjon bli stående som gjeldende, og katalogporten
    ville avvist verdier databasen ikke lenger begrenser. Vilkår og slipp leses
    derfor i STILLINGSREKKEFØLGE i hver fil — 036 slipper og legger på igjen i
    samme setningspar, og rekkefølgen er det eneste som skiller dem.

    Hvilket vilkår et slipp treffer, avgjøres av NAVNET: et navngitt vilkår
    huskes som det heter, og et vilkår skrevet rett på kolonnen får navnet
    PostgreSQL selv gir det — `<tabell>_<kolonne>_check`, og `_check1`,
    `_check2` … hvis navnet er opptatt. Det er den formen 036 slipper, og den
    formen 014 la inn uten å navngi.

    Og navnet er IDENTITETEN til vilkåret, ikke (tabell, kolonne). Tilstanden
    ble ført per kolonne, så et nytt vilkår på samme kolonne ERSTATTET det
    forrige. Det er ikke det databasen gjør: legger en migrasjon til en CHECK
    uten å slippe den gamle, håndhever PostgreSQL BEGGE, og en verdi må stå i
    begge for å slippe gjennom. Med erstatning kunne en modul bære en
    `rev`-verdi det nyeste vilkåret godtar og det eldste avviser — altså den
    umulige modulen porten finnes for å fange. Gjeldende verdier for en kolonne
    er derfor SNITTET av vilkårene som står igjen.

    En hendelse i en `DO`-kropp kan stå bak en BETINGELSE. Betingelsen er en
    spørring som først avgjøres når migrasjonen kjører, så porten kan ikke lese
    svaret. Den kan velge den tolkningen som aldri utvider: et betinget SLIPP
    regnes som ikke utført, så vilkåret blir stående, mens et betinget VILKÅR
    regnes som lagt på. Begge veier snevrer de inn. Tar porten feil, avviser den
    en verdi databasen godtar — det er en feil som stopper CI og blir rettet, i
    motsetning til den motsatte, som slipper en umulig modul gjennom i stillhet.

    De to reglene møtes når grenen slipper og legger på igjen under SAMME navn.
    Slippet gis fra seg, så det gamle vilkåret står — men tillegget ville skrevet
    seg på den samme nøkkelen og erstattet det likevel. Kjørte grenen, gjelder
    erstatningen; kjørte den ikke, gjelder originalen — porten vet ikke hvilken,
    så BEGGE blir stående, og snittet er da det eneste svaret som ikke utvider.
    Erstatningen legges derfor ved siden av, under en nøkkel ingen `DROP
    CONSTRAINT` kan skrive.

    `noen_gang` er unionen av alt registeret har bundet, og er noe annet: den er
    historikken en pensjonert verdi kjennes igjen på.
    """
    # {(tabell, vilkårsnavn): {kolonne: verdier}} — vilkårene som står igjen.
    vilkar: dict[tuple[str, str], dict[str, set[str]]] = {}
    noen_gang: set[str] = set()
    for sql in sorted((mappe or MIGRASJONER).glob("*.sql")):
        for hendelse in _hendelsene(sql.read_text(encoding="utf-8")):
            if hendelse[0] is _SLIPP:
                _, tabell, navn, _, betinget = hendelse
                if betinget:
                    # Et slipp bak en betingelse er ikke kjent kjørt, og et
                    # vilkår som blir stående kan bare gjøre snittet SMALERE.
                    continue
                vilkar.pop((tabell, navn), None)
                continue
            _, tabell, navn, uttrykk, betinget, kolonne = hendelse
            bindinger = _bindinger(uttrykk)
            if not bindinger:
                continue
            if navn is None:
                navn = _tildelt_navn(vilkar, tabell, kolonne)
            if betinget and (tabell, navn) in vilkar:
                navn = _sidestilt_navn(vilkar, tabell, navn)
            vilkar[(tabell, navn)] = bindinger
            noen_gang |= set().union(*bindinger.values())
    gjeldende: dict[tuple[str, str], set[str]] = {}
    for (tabell, _), bindinger in vilkar.items():
        for kolonne, verdier in bindinger.items():
            nokkel = (tabell, kolonne)
            gjeldende[nokkel] = _snitt(gjeldende[nokkel], verdier) \
                if nokkel in gjeldende else set(verdier)
    return gjeldende, noen_gang - {ULESELIG_SQL}


def _tildelt_navn(vilkar: dict, tabell: str, kolonne: str | None) -> str:
    """Navnet PostgreSQL gir et vilkår ingen har navngitt.

    `<tabell>_<kolonne>_check` for et vilkår skrevet PÅ en kolonne, og
    `<tabell>_check` for et som står for seg i tabelldefinisjonen — det er de
    formene serveren selv bruker. Er navnet opptatt, teller den opp:
    `…_check1`, `…_check2`. To vilkår uten navn på samme kolonne er derfor to
    vilkår, ikke ett som overskriver det andre, og begge teller med i snittet.

    Navnet er ikke pynt: det er dette et senere `DROP CONSTRAINT` treffer. 014
    legger vilkåret rett på kolonnen uten å navngi det, og 036 slipper det som
    `modulkontrakt_reversibilitet_check`.
    """
    stamme = f"{tabell}_{kolonne}_check" if kolonne else f"{tabell}_check"
    navn, nr = stamme, 0
    while (tabell, navn) in vilkar:
        nr += 1
        navn = f"{stamme}{nr}"
    return navn


def _sidestilt_navn(vilkar: dict, tabell: str, navn: str) -> str:
    """Nøkkelen et BETINGET vilkår får når navnet alt er i bruk.

    Et betinget tillegg som gjenbruker navnet på et vilkår som står, er to
    utfall porten ikke kan velge mellom: kjørte grenen, ble det gamle sluppet
    og erstattet, kjørte den ikke, står originalen. Erstatningen legges derfor
    SIDESTILT — begge blir stående, og `gjeldende` snitter dem per kolonne, som
    er den tolkningen som aldri utvider.

    Nøkkelen bærer et NUL-tegn, og PostgreSQL har ingen identifikator som gjør
    det: ingen `DROP CONSTRAINT` kan navngi den sidestilte oppføringen. Det er
    meningen — et senere slipp skal ikke kunne fjerne den halvdelen av snittet
    som holder porten smal.
    """
    nr = 1
    while (tabell, f"{navn}\0{nr}") in vilkar:
        nr += 1
    return f"{navn}\0{nr}"


def _registerenum(kolonne: str) -> set[str]:
    """Verdiene `modulkontrakt` godtar i `kolonne` NÅ.

    TABELLEN er en del av spørsmålet (Codex P2 på #118, sjette runde). Dette
    slo før opp på kolonnenavnet alene og unionerte over alle tabeller — og en
    union er ikke det databasen gjør. Kontrakten en katalogmodul blir til når
    den bygges, lagres i `modulkontrakt`, og det er DEN tabellens CHECK-vilkår
    som avviser den. Legger en senere migrasjon en `reversibilitet`-kolonne på
    en annen tabell med `snapshot` blant verdiene, ville unionen gjort
    `rev: "snapshot"` gyldig i katalogen mens `modulkontrakt` fortsatt sier
    nei — altså nøyaktig den umulige modulen porten finnes for å fange.

    Feiler oppslaget, er det fordi kolonnen ikke lenger CHECK-es på
    `modulkontrakt` — og da er det porten som skal rettes, ikke katalogen.

    En kolonne porten ikke kunne lese vilkåret for, se `ULESELIG_SQL`, sier fra
    HER og ikke ved enhver migrasjon som skriver noe uleselig et annet sted.
    Det er kolonnen katalogen faktisk måles mot som må være lest riktig.
    """
    gjeldende, _ = _registerets_enums()
    ut = gjeldende.get((MODULKONTRAKT, kolonne), set())
    assert ut, (f"fant ikke CHECK-vilkåret for {MODULKONTRAKT}.{kolonne} i "
                f"migrasjonene")
    assert ULESELIG_SQL not in ut, (
        f"CHECK-vilkåret for {MODULKONTRAKT}.{kolonne} er ikke en verdiliste "
        f"porten kan regne på: et sammensatt eller negativt predikat sier hva "
        f"kolonnen IKKE kan være, og hvor mye smalere det gjør vilkåret står i "
        f"den delen ingen liste bærer. Skriv vilkåret som "
        f"`{kolonne} IN ('…', '…')`; et gjettet innhold ville vært verre enn "
        f"ingen.")
    return ut


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


def _migrasjoner(tmp_path: Path, *filer: str) -> Path:
    """Skriv `filer` som nummererte migrasjoner og gi mappa tilbake."""
    for nr, sql in enumerate(filer, start=1):
        (tmp_path / f"{nr:03d}_prove.sql").write_text(sql, encoding="utf-8")
    return tmp_path


_LAGER_VILKAR = (
    "CREATE TABLE modulkontrakt (\n"
    "    reversibilitet TEXT NOT NULL\n"
    "        CHECK (reversibilitet IN ('direkte', 'kompenserende')));\n")


@pytest.mark.parametrize("slipp,star_igjen", [
    # Vilkåret 014 skriver rett på kolonnen har ikke noe navn i filen —
    # PostgreSQL gir det `<tabell>_<kolonne>_check`, og det er det navnet 036
    # slipper. Kjenner ikke porten den formen, treffer slippet ingenting.
    ("ALTER TABLE modulkontrakt\n"
     "    DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n", False),
    # `IF EXISTS` er den formen migrasjonene i repoet bruker mest.
    ("ALTER TABLE modulkontrakt DROP CONSTRAINT IF EXISTS "
     "modulkontrakt_reversibilitet_check;\n", False),
    # Et slipp som treffer et ANNET vilkår på samme tabell rører ikke enumet.
    ("ALTER TABLE modulkontrakt DROP CONSTRAINT modulkontrakt_frister;\n",
     True),
    # Og et slipp av samme navn på en annen tabell heller ikke.
    ("ALTER TABLE annen DROP CONSTRAINT "
     "modulkontrakt_reversibilitet_check;\n", True),
])
def test_enumtilstanden_folger_et_sluppet_vilkar(tmp_path, slipp, star_igjen):
    """Et vilkår som er FJERNET begrenser ingenting lenger.

    Tilstanden ble regnet av CHECK-treff alene (Codex P2 på #118, ellevte
    runde). En migrasjon som slipper et vilkår uten å legge på et nytt lot
    derfor verdiene fra forrige migrasjon stå som gjeldende: katalogporten ville
    fortsatt avvist `rev`-verdier databasen nettopp sluttet å begrense, og
    `_registerenum()` ville ikke sagt fra om at vilkåret er borte — den ville
    svart med en tilstand som ikke finnes.

    Prøvene kjøres mot syntetiske migrasjoner og ikke mot repoets egne: repoet
    slipper alltid et vilkår for å legge på et nytt i samme setningspar, så
    formen «sluppet og ikke erstattet» finnes ikke der å måle på.
    """
    mappe = _migrasjoner(tmp_path, _LAGER_VILKAR, slipp)
    gjeldende, noen_gang = _registerets_enums(mappe)
    nokkel = (MODULKONTRAKT, "reversibilitet")
    assert (nokkel in gjeldende) is star_igjen, (
        f"gjeldende tilstand etter slippet: {gjeldende}")
    # Historikken står uansett: `noen_gang` er hva registeret HAR bundet, og en
    # verdi som faller ut av et vilkår er nettopp det `pensjonert` bygges av.
    assert "direkte" in noen_gang


@pytest.mark.parametrize("senere", [
    # Et vilkår satt ut av drift i en blokkommentar er ikke gjeldende tilstand.
    "/* ALTER TABLE modulkontrakt ADD CONSTRAINT r\n"
    "     CHECK (reversibilitet IN ('oppfunnet')); */\n",
    # Blokkommentarer NØSTES i PostgreSQL: den ytre lukkes av den siste `*/`.
    "/* eksempel /* nøstet */ ALTER TABLE modulkontrakt ADD CONSTRAINT r\n"
    "     CHECK (reversibilitet IN ('oppfunnet')); */\n",
    # Og på én linje, som et eksempel midt i en forklaring.
    "-- ALTER TABLE modulkontrakt ADD CONSTRAINT r CHECK "
    "(reversibilitet IN ('oppfunnet'));\n",
])
def test_et_kommentert_vilkaar_er_ikke_gjeldende_tilstand(tmp_path, senere):
    """En SQL-setning i en kommentar er ikke kjørt, og binder ingenting.

    Bare `--` ble fjernet (Codex P2 på #118, tolvte runde). En blokkommentert
    `CHECK` ble derfor lest som gjeldende tilstand, og katalogporten ville
    sluppet inn en `kl`- eller `rev`-verdi PostgreSQL avviser ved registrering —
    altså nøyaktig den umulige modulen porten finnes for å fange.

    Formen finnes ikke i repoets migrasjoner i dag, og det er grunnen til at den
    må stå her: en port som bare måler dagens filer, går grønn helt til noen
    skriver linja.
    """
    mappe = _migrasjoner(tmp_path, _LAGER_VILKAR, senere)
    gjeldende, noen_gang = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "direkte", "kompenserende"}
    assert "oppfunnet" not in noen_gang


def test_et_kommentert_slipp_fjerner_ingenting(tmp_path):
    """Motsatt vei: et blokkommentert slipp skal ikke slette et levende vilkår.

    Da hadde porten sluttet å vokte kolonnen i det hele tatt — enhver verdi
    ville gått gjennom — og `_registerenum()` ville meldt et vilkår som borte
    mens databasen fortsatt håndhever det.
    """
    mappe = _migrasjoner(
        tmp_path, _LAGER_VILKAR,
        "/* midlertidig ute av drift:\n"
        "   ALTER TABLE modulkontrakt\n"
        "       DROP CONSTRAINT modulkontrakt_reversibilitet_check; */\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "direkte", "kompenserende"}


def test_kommentartegn_i_en_streng_er_ikke_kommentar(tmp_path):
    """`--` og `/*` inne i en verdi er data, ikke starten på en kommentar.

    Fjernet vi dem der, kappet vi vilkåret midt i verdilista — og da fant
    `_CHECK_RE` ingen ting, så kolonnen sto uvoktet uten at noe sa fra. Doblet
    sitattegn er SQLs escape og lukker ikke strengen.
    """
    mappe = _migrasjoner(
        tmp_path,
        "CREATE TABLE modulkontrakt (\n"
        "    reversibilitet TEXT NOT NULL\n"
        "        CHECK (reversibilitet IN ('a--b', 'c/*d', 'det''s')));\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "a--b", "c/*d", "det's"}


def test_sitert_kolonne_i_et_vilkaar_er_samme_kolonne(tmp_path):
    """`CHECK ("reversibilitet" IN (…))` vokter den alminnelige kolonnen.

    `_CHECK_RE` leste bare den bare formen (Codex P2 på #118, sekstende runde),
    så et vilkår skrevet slik var ikke et treff i det hele tatt. Legges en
    innstramming ved siden av det videre vilkåret som står, blir den usynlig,
    bare det videre blir gjeldende, og porten slipper inn en klasse den siterte
    CHECK-en avviser. PostgreSQL folder den bare formen ned til små bokstaver,
    så de to skrivemåtene peker på samme kolonne og må havne i samme snitt.
    """
    mappe = _migrasjoner(
        tmp_path, _LAGER_VILKAR,
        'ALTER TABLE "modulkontrakt" ADD CONSTRAINT smalere\n'
        '    CHECK ("reversibilitet" IN (\'direkte\'));\n')
    gjeldende, noen_gang = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {"direkte"}
    assert {"direkte", "kompenserende"} <= noen_gang


@pytest.mark.parametrize("vilkar,gjelder", [
    # Overflødige parenteser endrer ingenting for PostgreSQL, og skal ikke
    # endre noe for porten (Codex P2 på #118, syttende runde).
    ("CHECK ((reversibilitet IN ('direkte')))", {"direkte"}),
    ("CHECK ( ( ( reversibilitet IN ('direkte') ) ) )", {"direkte"}),
    ('CHECK (("reversibilitet" IN (\'direkte\')))', {"direkte"}),
    # En DISJUNKSJON er ikke lista si. `X OR Y` godtar unionen, så hverken det
    # første leddet eller ingenting er riktig svar: vilkåret sier ingenting
    # porten kan regne på, og den øvre grensen blir stående fra vilkåret 014
    # la inn — med uvissheten på.
    #
    # Skanneren svarte `{'direkte', ULESELIG_SQL}` her: den leste det FØRSTE
    # leddet som om det var vilkåret, og mistet `kompenserende` ut av den øvre
    # grensen. Det er et snevrere svar enn databasen gir, altså feil vei for en
    # port som skal si hva registeret godtar.
    ("CHECK ((reversibilitet IN ('direkte'))\n"
     "    OR (reversibilitet IN ('kompenserende')))",
     {"direkte", "kompenserende", ULESELIG_SQL}),
    # En parentes inne i en VERDI lukker ingen liste: `([^)]*)` stoppet ved
    # den, og resten av lista falt bort sammen med verdien den sto i.
    ("CHECK (reversibilitet IN ('a)b', 'kompenserende'))", {"kompenserende"}),
])
def test_overflodige_parenteser_er_samme_vilkaar(tmp_path, vilkar, gjelder):
    """Et vilkår leses forbi parentesene som bare pakker det inn.

    `_CHECK_RE` krevde kolonnen rett etter `CHECK (` (Codex P2 på #118,
    syttende runde), så et vilkår med et ekstra parentespar var ikke et treff i
    det hele tatt — og et vilkår porten ikke ser, snitter den ikke med. Legges
    en innstramming skrevet slik ved siden av det videre vilkåret som står,
    blir bare det videre gjeldende, og porten slipper inn en klasse registeret
    avviser.

    Parenteser er ikke lenger noe porten teller: grammatikken har alt gjort
    `((x))` og `x` til samme uttrykk, og en parentes inne i en verdi er en del
    av verdien. Prøvene står likevel — de måler at lesningen faktisk er den, og
    ikke noe som bare oppfører seg likt på dagens filer.
    """
    mappe = _migrasjoner(
        tmp_path, _LAGER_VILKAR,
        f"ALTER TABLE modulkontrakt ADD CONSTRAINT smalere\n    {vilkar};\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == gjelder


def test_enumtilstanden_leser_slipp_og_nytt_vilkaar_i_rekkefolge(tmp_path):
    """Slipp og nytt vilkår i samme fil avgjøres av rekkefølgen, ikke av slaget.

    Det er formen 036 bruker: slipp vilkåret, legg på et videre. Leste porten
    alle slipp etter alle vilkår, ville utvidelsen blitt slettet av sitt eget
    slipp — og `sideeffektklasse` stått uten vilkår i det hele tatt.
    """
    mappe = _migrasjoner(
        tmp_path, _LAGER_VILKAR,
        "ALTER TABLE modulkontrakt\n"
        "    DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
        "ALTER TABLE modulkontrakt ADD CONSTRAINT "
        "modulkontrakt_reversibilitet_check\n"
        "    CHECK (reversibilitet IN ('direkte', 'irreversibel'));\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "direkte", "irreversibel"}


@pytest.mark.parametrize("lager,endrer", [
    # `ONLY` er formen for «bare denne tabellen, ikke arvingene».
    (_LAGER_VILKAR, "ALTER TABLE ONLY modulkontrakt\n"),
    # Og den kan stå sammen med `IF EXISTS`, i den rekkefølgen PostgreSQL
    # krever.
    (_LAGER_VILKAR, "ALTER TABLE IF EXISTS ONLY modulkontrakt\n"),
    # Modifikatorene FORAN `TABLE` er samme rot: uten dem fant mønsteret ingen
    # tabell i det hele tatt, og vilkåret hadde festet seg til forrige tabell.
    ("CREATE UNLOGGED TABLE modulkontrakt (reversibilitet TEXT NOT NULL\n"
     "    CHECK (reversibilitet IN ('direkte', 'kompenserende')));\n",
     "ALTER TABLE modulkontrakt\n"),
])
def test_tabellnavnet_leses_forbi_nokkelordene(tmp_path, lager, endrer):
    """Nøkkelordene mellom verbet og navnet er ikke tabellnavnet.

    Prefikset sto som en åpen oppramsing — bare `IF [NOT] EXISTS` (Codex P2 på
    #118, fjortende runde). `ALTER TABLE ONLY modulkontrakt` ga derfor `only`
    som tabell, innstrammingen la seg under en nøkkel ingen spør om, og det
    forrige og videre vilkåret på `modulkontrakt` ble stående som gjeldende
    tilstand. Katalogporten ville da sluppet inn en `rev`-verdi PostgreSQL
    avviser ved registrering.

    Formene finnes ikke i repoets migrasjoner i dag; det er nettopp derfor de
    må stå her. En port som bare måler dagens filer, går grønn til noen skriver
    linja.
    """
    mappe = _migrasjoner(
        tmp_path, lager,
        endrer + "    DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
        + endrer + "    ADD CONSTRAINT modulkontrakt_reversibilitet_check\n"
        "    CHECK (reversibilitet IN ('direkte'));\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {"direkte"}
    assert not [n for n in gjeldende if n[0] in ("only", "if", "exists")], (
        f"et nøkkelord ble tabellnavn: {sorted(gjeldende)}")


def test_escapestrengen_lukkes_ikke_av_en_escapet_fnutt(tmp_path):
    """`E'…'` escaper med `\\`, og `\\'` er da ikke slutten på strengen.

    Porten leste den escapede fnutten som slutten (Codex P2 på #118, trettende
    runde). Resten av verdien — `--b'` — ble da lest som en linjekommentar, og
    kommentaren spiste vilkåret som sto på linja. Kolonnen sto igjen uten
    vilkår uten at noe sa fra.
    """
    mappe = _migrasjoner(
        tmp_path,
        "CREATE TABLE modulkontrakt (reversibilitet TEXT NOT NULL);\n"
        "INSERT INTO notat (tekst) VALUES (E'a\\'--b'); "
        "ALTER TABLE modulkontrakt ADD CONSTRAINT r "
        "CHECK (reversibilitet IN ('direkte', 'kompenserende'));\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "direkte", "kompenserende"}


def test_en_escapet_bakstrek_lukker_strengen_som_vanlig(tmp_path):
    """Motsatt vei: `E'\\\\_\\\\_%'` i 041 lukkes av fnutten etter parene.

    En regel om at `\\'` aldri lukker ville tatt strengen forbi sin egen slutt,
    og med den alt som står etter — inkludert et vilkår.
    """
    mappe = _migrasjoner(
        tmp_path,
        "CREATE TABLE modulkontrakt (reversibilitet TEXT NOT NULL);\n"
        "SELECT x FROM y WHERE z LIKE E'\\\\_\\\\_%';\n"
        "ALTER TABLE modulkontrakt ADD CONSTRAINT "
        "modulkontrakt_reversibilitet_check\n"
        "    CHECK (reversibilitet IN ('direkte'));\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {"direkte"}


@pytest.mark.parametrize("senere", [
    # `RAISE NOTICE` skriver en melding; den kjører ingen CHECK.
    "DO $body$ BEGIN RAISE NOTICE $$CHECK (reversibilitet IN "
    "('oppfunnet'))$$; END $body$;\n",
    # Og motsatt vei: et dollarsitert slipp fjerner ikke et levende vilkår.
    "DO $body$ BEGIN RAISE NOTICE $$ALTER TABLE modulkontrakt DROP CONSTRAINT "
    "modulkontrakt_reversibilitet_check$$; END $body$;\n",
    # Fnutter og semikolon inne i en dollarsitert streng er data, og skal ikke
    # forskyve lesningen av det som står etter.
    "DO $$ BEGIN RAISE NOTICE 'det''s -- her'; END $$;\n",
])
def test_dollarsitert_tekst_er_ikke_kjorte_setninger(tmp_path, senere):
    """En CHECK-formet streng er tekst, ikke et vilkår databasen håndhever.

    Porten kjente ikke dollarsitering i det hele tatt (Codex P2 på #118,
    trettende runde), og formen er ikke eksotisk her: konvensjonen i repoets
    migrasjoner er nettopp å pakke betinget DDL i en `DO`-blokk. En melding med
    et vilkår i seg ble derfor lest som gjeldende tilstand, og katalogporten
    ville sluppet inn en klasse PostgreSQL avviser ved registrering.
    """
    mappe = _migrasjoner(tmp_path, _LAGER_VILKAR, senere)
    gjeldende, noen_gang = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "direkte", "kompenserende"}
    assert "oppfunnet" not in noen_gang


@pytest.mark.parametrize("senere,gjelder", [
    # Formen 035 §6 bruker: betinget DDL pakket i en `DO`-blokk. Vilkåret
    # KJØRER, og strammer inn.
    ("DO $$ BEGIN\n"
     "    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'r') THEN\n"
     "        ALTER TABLE public.modulkontrakt ADD CONSTRAINT r\n"
     "            CHECK (reversibilitet IN ('direkte'));\n"
     "    END IF;\n"
     "END $$;\n", {"direkte"}),
    # Og motsatt vei: et innpakket slipp FJERNER vilkåret det peker på.
    ("DO $$ BEGIN\n"
     "    ALTER TABLE modulkontrakt\n"
     "        DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
     "END $$;\n", None),
    # `DO` tar valgfritt `LANGUAGE` mellom seg og kroppen.
    ("DO LANGUAGE plpgsql $krop$ BEGIN\n"
     "    ALTER TABLE modulkontrakt ADD CONSTRAINT r\n"
     "        CHECK (reversibilitet IN ('direkte'));\n"
     "END $krop$;\n", {"direkte"}),
    # En kommentar inne i kroppen er fortsatt en kommentar.
    ("DO $$ BEGIN\n"
     "    -- ALTER TABLE modulkontrakt ADD CONSTRAINT r\n"
     "    --     CHECK (reversibilitet IN ('oppfunnet'));\n"
     "    ALTER TABLE modulkontrakt ADD CONSTRAINT r\n"
     "        CHECK (reversibilitet IN ('direkte'));\n"
     "END $$;\n", {"direkte"}),
    # Avstanden mellom `DO` og taggen er ikke begrenset av noe: en lang
    # kommentar eller bare luft skal ikke gjøre kroppen til data.
    ("DO\n" + "-- en lang merknad om hvorfor dette står i en blokk\n" * 4
     + "$$ BEGIN\n"
     "    ALTER TABLE modulkontrakt ADD CONSTRAINT r\n"
     "        CHECK (reversibilitet IN ('direkte'));\n"
     "END $$;\n", {"direkte"}),
    # Men en funksjonsKROPP defineres, den kjøres ikke — `AS $$…$$` er data.
    ("CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$ BEGIN\n"
     "    ALTER TABLE modulkontrakt ADD CONSTRAINT r\n"
     "        CHECK (reversibilitet IN ('oppfunnet'));\n"
     "END $$;\n", {"direkte", "kompenserende"}),
])
def test_en_do_kropp_kjorer_sine_egne_setninger(tmp_path, senere, gjelder):
    """`DO $$ … $$` er en kropp som KJØRES, ikke en tekst som bæres.

    Forrige runde maskerte alt dollarsitert for å stoppe en `RAISE
    NOTICE`-melding fra å bli lest som et vilkår, og byttet dermed hullet mot
    det motsatte (Codex P2 på #118, fjortende runde). Repoets konvensjon er
    nettopp å pakke betinget DDL i en `DO`-blokk — 035 §6 legger
    `varsel_art_chk` på `public.varsel` slik — og med hele kroppen maskert ble
    et innpakket slipp usynlig, så et fjernet vilkår ble stående som gjeldende,
    mens en innpakket innstramming falt ut av snittet. Begge veier kan
    katalogporten godta en `kl`- eller `rev`-klasse PostgreSQL avviser.

    Slaget avgjøres av det som står FORAN taggen: etter `DO` er det en kropp,
    ellers er det data. Derfor står funksjonsdefinisjonen med her — `AS $$…$$`
    definerer uten å kjøre, og skal fortsatt ikke binde noe.
    """
    mappe = _migrasjoner(tmp_path, _LAGER_VILKAR, senere)
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende.get((MODULKONTRAKT, "reversibilitet")) == gjelder


@pytest.mark.parametrize("senere,gjelder", [
    # Et slipp SKREVET som en verdi er data. Vilkåret står igjen.
    ("INSERT INTO endringslogg (tekst) VALUES\n"
     "    ('ALTER TABLE modulkontrakt DROP CONSTRAINT "
     "modulkontrakt_reversibilitet_check');\n", {"direkte", "kompenserende"}),
    # Og et vilkår skrevet som en verdi binder ingen ting.
    ("INSERT INTO endringslogg (tekst) VALUES\n"
     "    ('ALTER TABLE modulkontrakt ADD CONSTRAINT r "
     "CHECK (reversibilitet IN (''oppfunnet''))');\n",
     {"direkte", "kompenserende"}),
    # Et tabellnavn i en verdi flytter ikke hvilken tabell vilkåret havner på.
    ("INSERT INTO endringslogg (tekst) VALUES ('CREATE TABLE annen (x INT)');\n"
     "ALTER TABLE modulkontrakt ADD CONSTRAINT r\n"
     "    CHECK (reversibilitet IN ('direkte'));\n", {"direkte"}),
    # Et ekte slipp rett etter en verdi som ligner leses fortsatt.
    ("INSERT INTO endringslogg (tekst) VALUES ('DROP CONSTRAINT r');\n"
     "ALTER TABLE modulkontrakt\n"
     "    DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n", None),
])
def test_en_sql_streng_er_data_og_ingen_setning(tmp_path, senere, gjelder):
    """Innholdet i en fnuttstreng er verdier, ikke DDL som er kjørt.

    Strengene ble stående uendret i den maskerte teksten, med rette — verdiene
    et vilkår binder ER strenger, og blankes de, forsvinner enumet med dem.
    Men hendelsene ble så lett etter i den samme teksten (Codex P2 på #118,
    femtende runde), og et `DROP CONSTRAINT` skrevet inn i en loggverdi fjernet
    dermed et vilkår PostgreSQL fortsatt håndhever. Snittet ble videre enn
    databasen, og katalogporten ville sluppet inn en `kl`- eller `rev`-klasse
    registeret avviser.

    Skillet går på hvor hendelsen BEGYNNER: et nøkkelord inne i en streng er
    tekst, mens verdiene i `IN (…)` hører til et vilkår som begynte utenfor.
    """
    mappe = _migrasjoner(tmp_path, _LAGER_VILKAR, senere)
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende.get((MODULKONTRAKT, "reversibilitet")) == gjelder


@pytest.mark.parametrize("senere,gjelder", [
    # Et slipp bak en betingelse er ikke kjent kjørt: vilkåret blir stående.
    ("DO $$ BEGIN\n"
     "    IF FALSE THEN\n"
     "        ALTER TABLE modulkontrakt\n"
     "            DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
     "        ALTER TABLE modulkontrakt ADD CONSTRAINT r\n"
     "            CHECK (reversibilitet IN ('oppfunnet'));\n"
     "    END IF;\n"
     "END $$;\n", set()),
    # Nøstede grener er betinget av begge, og det ytterste spennet dekker dem.
    ("DO $$ BEGIN\n"
     "    IF FALSE THEN\n"
     "        IF TRUE THEN\n"
     "            ALTER TABLE modulkontrakt\n"
     "                DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
     "        END IF;\n"
     "    END IF;\n"
     "END $$;\n", {"direkte", "kompenserende"}),
    # Et UBETINGET slipp i en kropp kjører fortsatt — det er formen porten
    # lærte forrige runde, og den skal stå.
    ("DO $$ BEGIN\n"
     "    ALTER TABLE modulkontrakt\n"
     "        DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
     "END $$;\n", None),
    # `IF EXISTS` i et slipp er ikke en gren: ordet hører til DDL-en.
    ("DO $$ BEGIN\n"
     "    ALTER TABLE modulkontrakt DROP CONSTRAINT IF EXISTS\n"
     "        modulkontrakt_reversibilitet_check;\n"
     "END $$;\n", None),
    # Et slipp etter at grenen er lukket er ubetinget igjen.
    ("DO $$ BEGIN\n"
     "    IF FALSE THEN\n"
     "        RAISE NOTICE 'ingenting';\n"
     "    END IF;\n"
     "    ALTER TABLE modulkontrakt\n"
     "        DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
     "END $$;\n", None),
    # Og formen 035 §6 bruker står: et betinget VILKÅR regnes som lagt på, for
    # et vilkår mer kan bare gjøre snittet smalere.
    ("DO $$ BEGIN\n"
     "    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'r') THEN\n"
     "        ALTER TABLE public.modulkontrakt ADD CONSTRAINT r\n"
     "            CHECK (reversibilitet IN ('direkte'));\n"
     "    END IF;\n"
     "END $$;\n", {"direkte"}),
    # Slipp og tillegg under SAMME navn: erstatningen skrev seg oppå det
    # vilkåret slippet nettopp ikke fikk fjerne, og `irreversibel` ble
    # gjeldende selv om PostgreSQL kan sitte igjen med originalen. Begge
    # utfallene står, og snittet er det de er enige om.
    ("DO $$ BEGIN\n"
     "    IF FALSE THEN\n"
     "        ALTER TABLE modulkontrakt\n"
     "            DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
     "        ALTER TABLE modulkontrakt\n"
     "            ADD CONSTRAINT modulkontrakt_reversibilitet_check\n"
     "            CHECK (reversibilitet IN ('direkte', 'irreversibel'));\n"
     "    END IF;\n"
     "END $$;\n", {"direkte"}),
    # Samme form der erstatningen er VIDERE: originalen kan stå, så den
    # nye verdien kan ikke bli gjeldende av seg selv.
    ("DO $$ BEGIN\n"
     "    IF FALSE THEN\n"
     "        ALTER TABLE modulkontrakt DROP CONSTRAINT IF EXISTS\n"
     "            modulkontrakt_reversibilitet_check;\n"
     "        ALTER TABLE modulkontrakt\n"
     "            ADD CONSTRAINT modulkontrakt_reversibilitet_check\n"
     "            CHECK (reversibilitet IN\n"
     "                ('direkte', 'kompenserende', 'oppfunnet'));\n"
     "    END IF;\n"
     "END $$;\n", {"direkte", "kompenserende"}),
    # To betingede tillegg under samme navn står også side om side: hver av
    # dem kan være den som kjørte, og ingen av dem kan utvide de andre.
    ("DO $$ BEGIN\n"
     "    IF FALSE THEN\n"
     "        ALTER TABLE modulkontrakt\n"
     "            ADD CONSTRAINT modulkontrakt_reversibilitet_check\n"
     "            CHECK (reversibilitet IN ('direkte', 'kompenserende'));\n"
     "    END IF;\n"
     "    IF FALSE THEN\n"
     "        ALTER TABLE modulkontrakt\n"
     "            ADD CONSTRAINT modulkontrakt_reversibilitet_check\n"
     "            CHECK (reversibilitet IN ('kompenserende'));\n"
     "    END IF;\n"
     "END $$;\n", {"kompenserende"}),
    # Men et UBETINGET tillegg er ett kjent utfall, og erstatter som før: et
    # navn kan bare bæres av ett vilkår, så slippet foran MÅ ha kjørt.
    ("DO $$ BEGIN\n"
     "    ALTER TABLE modulkontrakt DROP CONSTRAINT IF EXISTS\n"
     "        modulkontrakt_reversibilitet_check;\n"
     "    ALTER TABLE modulkontrakt\n"
     "        ADD CONSTRAINT modulkontrakt_reversibilitet_check\n"
     "        CHECK (reversibilitet IN ('direkte', 'irreversibel'));\n"
     "END $$;\n", {"direkte", "irreversibel"}),
])
def test_betinget_ddl_i_en_do_kropp_utvider_aldri(tmp_path, senere, gjelder):
    """En gren i en `DO`-kropp kan ikke leses, bare regnes konservativt.

    Kroppen ble lest som om ALT i den kjørte (Codex P2 på #118, femtende
    runde). Et `IF FALSE THEN` rundt et slipp og en erstatning ga porten
    erstatningens verdier mens PostgreSQL beholder originalen: snittet ble
    videre enn databasen, og katalogporten ville sluppet inn en `kl`- eller
    `rev`-klasse registeret avviser.

    Betingelsen er en spørring mot katalogen som først avgjøres når migrasjonen
    kjører, så svaret kan ikke leses ut av teksten. Porten velger derfor den
    tolkningen som aldri UTVIDER: et betinget slipp regnes som ikke utført, et
    betinget vilkår som lagt på. Feiler den, avviser den en verdi databasen
    godtar — en feil som stopper CI, ikke en som slipper en umulig modul
    gjennom i stillhet.
    """
    mappe = _migrasjoner(tmp_path, _LAGER_VILKAR, senere)
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende.get((MODULKONTRAKT, "reversibilitet")) == gjelder


@pytest.mark.parametrize("kropp,gjelder", [
    # En blokk med håndterer: kroppen kan rulles tilbake, så slippet er ikke
    # kjent utført og det opprinnelige vilkåret blir stående.
    ("BEGIN\n"
     "    ALTER TABLE modulkontrakt\n"
     "        DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
     "    PERFORM 1/0;\n"
     "EXCEPTION WHEN division_by_zero THEN NULL;\n"
     "END", {"direkte", "kompenserende"}),
    # Uten håndterer ruller ingenting tilbake: feiler kroppen, stopper hele
    # migrasjonen, og da gjelder ingenting av den uansett.
    ("BEGIN\n"
     "    ALTER TABLE modulkontrakt\n"
     "        DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n"
     "END", None),
])
def test_en_blokk_med_unntakshaandterer_kan_rulles_tilbake(tmp_path, kropp,
                                                           gjelder):
    """`EXCEPTION` gjør kroppen om til noe som kanskje ikke ble stående.

    Codex P2 på #118, tjueførste runde. PostgreSQL kjører en blokk med
    håndterer i sitt eget underpunkt, og fanges en feil, RULLES ALT kroppen
    rakk å gjøre TILBAKE — også DDL. Kroppen ble regnet som ubetinget, så et
    slipp inne i en slik blokk fjernet et vilkår databasen beholder. Sto et
    videre vilkår igjen ved siden av, ble snittet videre enn databasen, og
    katalogporten kunne slippe inn en klasse registeret avviser.

    Om kroppen fullførte, avgjøres først når migrasjonen kjører. Da er den
    betinget, og de samme konservative reglene gjelder som for enhver annen
    gren: slippet regnes som ikke utført.
    """
    mappe = _migrasjoner(tmp_path, _LAGER_VILKAR,
                         f"DO $do$\n{kropp} $do$;\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende.get((MODULKONTRAKT, "reversibilitet")) == gjelder


def test_repoets_do_blokker_leses_som_kjorte():
    """035 §6 legger vilkår på `varsel` inne i en `DO`-blokk, og de må sees.

    Prøven måler repoets egne migrasjoner og ikke en syntetisk fil: den er det
    som viser at maskeringen forrige runde faktisk gjorde en KJØRT setning
    usynlig her og nå, ikke bare i en tenkt migrasjon. `varsel` er dessuten den
    tabellen `_tabellnavn()` normaliserer `public.`-prefikset for, og den
    normaliseringen har ingen virkning hvis setningen som bærer prefikset er
    maskert bort.
    """
    gjeldende, noen_gang = _registerets_enums()
    # `tokenfamilie_utloper` og `modultoken` står BARE i de to vilkårene 035 §6
    # legger på inne i `DO $$ … $$`. Verdiene 026 alt bandt sier ingenting —
    # de står der uansett om kroppen leses eller maskeres.
    assert {"tokenfamilie_utloper", "modultoken"} <= noen_gang, (
        "vilkårene 035 §6 legger på inne i en DO-blokk er ikke lest")
    assert ("varsel", "art") in gjeldende
    # Og kolonnene katalogen faktisk måles mot står uendret.
    assert gjeldende[(MODULKONTRAKT, "sideeffektklasse")] == {
        "ekstern_lesing", "krever_outbox", "sideeffektfri"}
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "direkte", "irreversibel", "kompenserende"}


@pytest.mark.parametrize("senere,gjelder", [
    # To vilkår med hvert sitt navn står SAMMEN, og PostgreSQL håndhever begge.
    ("ALTER TABLE modulkontrakt ADD CONSTRAINT modulkontrakt_rev_smal\n"
     "    CHECK (reversibilitet IN ('direkte', 'irreversibel'));\n",
     {"direkte"}),
    # Slippes det ene, står det andre igjen alene.
    ("ALTER TABLE modulkontrakt ADD CONSTRAINT modulkontrakt_rev_smal\n"
     "    CHECK (reversibilitet IN ('direkte', 'irreversibel'));\n"
     "ALTER TABLE modulkontrakt\n"
     "    DROP CONSTRAINT modulkontrakt_reversibilitet_check;\n",
     {"direkte", "irreversibel"}),
])
def test_to_samtidige_vilkaar_gjelder_begge(tmp_path, senere, gjelder):
    """Legges et vilkår til uten at det gamle slippes, håndheves BEGGE.

    Tilstanden ble ført per (tabell, kolonne), så et nytt vilkår ERSTATTET det
    forrige (Codex P2 på #118, trettende runde). Det er ikke det databasen
    gjør: en verdi må stå i hvert eneste vilkår som gjelder for å komme
    gjennom. Med erstatning kunne katalogen bære en `rev`-verdi det nyeste
    vilkåret godtar og det eldste avviser — den umulige modulen porten finnes
    for å fange. Identiteten til et vilkår er NAVNET, og gjeldende verdier er
    snittet av dem som står igjen.
    """
    mappe = _migrasjoner(tmp_path, _LAGER_VILKAR, senere)
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == gjelder


def test_to_unavngitte_vilkaar_er_to_vilkaar(tmp_path):
    """PostgreSQL navngir det andre `…_check1`, og begge håndheves.

    Fikk de samme navn her, ville det andre overskrevet det første i porten,
    og en verdi bare det ene godtar hadde sett gyldig ut.
    """
    mappe = _migrasjoner(
        tmp_path,
        "CREATE TABLE modulkontrakt (\n"
        "    reversibilitet TEXT NOT NULL\n"
        "        CHECK (reversibilitet IN ('direkte', 'kompenserende'))\n"
        "        CHECK (reversibilitet IN ('direkte', 'irreversibel')));\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {"direkte"}


@pytest.mark.parametrize("vilkar", [
    # Lista først, predikatet etter — formen Codex fant.
    "CHECK ((reversibilitet IN ('direkte', 'irreversibel')\n"
    "        AND reversibilitet <> 'irreversibel'))",
    # Og motsatt vei: står lista sist, er hullet det samme.
    "CHECK (reversibilitet <> 'irreversibel'\n"
    "       AND reversibilitet IN ('direkte', 'irreversibel'))",
])
def test_et_sammensatt_vilkaar_leses_ikke_som_bare_lista(tmp_path, vilkar):
    """`IN`-lista er et OVERSETT av et sammensatt vilkår, ikke svaret.

    Leste porten bare lista, meldte den `irreversibel` som lov mens
    PostgreSQL avviser den — altså en umulig kontrakt gjennom porten som
    finnes for å fange den. Kolonnen er kjent, så uvissheten kan tilskrives
    den og sies fra om; å gi hele vilkåret fra seg ville tiet om det.
    """
    mappe = _migrasjoner(
        tmp_path,
        f"CREATE TABLE modulkontrakt (\n"
        f"    reversibilitet TEXT NOT NULL {vilkar});\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert ULESELIG_SQL in gjeldende[(MODULKONTRAKT, "reversibilitet")]


def test_hvert_ledd_i_et_sammensatt_vilkaar_leses(tmp_path):
    """En OG-kjede binder ALLE leddene sine, ikke det første porten fant.

    Codex P2 på #118, tjuende runde: lesningen stoppet ved det første
    `IN`-treffet i uttrykket. Et vilkår som binder to kolonner meldte da bare
    den ene, og den andre — her `reversibilitet` — ble stående på det videre
    vilkåret fra forrige migrasjon, enda PostgreSQL nå avviser resten av
    verdiene. Katalogporten kunne dermed godta en klasse databasen ikke tar.

    `A AND B` er håndhevet nøyaktig når både A og B er det, så kjeden leses
    ledd for ledd. Da er ikke rekkefølgen et spørsmål lenger.
    """
    mappe = _migrasjoner(
        tmp_path,
        "CREATE TABLE modulkontrakt (\n"
        "    flagg TEXT, reversibilitet TEXT,\n"
        "    CONSTRAINT vidt CHECK (reversibilitet IN\n"
        "        ('direkte', 'kompenserende', 'irreversibel')));\n",
        "ALTER TABLE modulkontrakt ADD CONSTRAINT smalt\n"
        "    CHECK (flagg IN ('ja') AND reversibilitet IN ('direkte'));\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {"direkte"}, (
        "leddet etter det første ble ikke lest — kolonnen sto igjen på det "
        "videre vilkåret")
    assert gjeldende[(MODULKONTRAKT, "flagg")] == {"ja"}


@pytest.mark.parametrize("vilkar,gjelder", [
    # `= ANY (ARRAY[…])` ER `IN` — grammatikken skiller dem, semantikken ikke.
    ("CHECK (reversibilitet = ANY (ARRAY['direkte', 'kompenserende']))",
     {"direkte", "kompenserende"}),
    # Og et ledd som ikke er én konstant gjør lista uleselig, ikke tre verdier.
    ("CHECK (reversibilitet IN ('direkte' || '_v2', 'kompenserende'))",
     {"direkte", "kompenserende", ULESELIG_SQL}),
])
def test_verdilista_leses_naar_den_er_verdier(tmp_path, vilkar, gjelder):
    """En liste er lesbar når hvert ledd er én strengkonstant, ellers ikke.

    Begge formene er Codex P2 på #118, tjuende runde, og de går hver sin vei:
    `= ANY (ARRAY[…])` ble forkastet i stillhet enda PostgreSQL håndhever den,
    mens `'direkte' || '_v2'` ble talt som TO verdier — hvorav den ene,
    `direkte`, er en klasse vilkåret nettopp ikke tillater.

    Å regne ut uttrykket ville vært å skrive PostgreSQLs operatorer av i
    Python; her stopper porten i stedet, og sier hvorfor.
    """
    mappe = _migrasjoner(
        tmp_path, _LAGER_VILKAR,
        f"ALTER TABLE modulkontrakt ADD CONSTRAINT smalere {vilkar};\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == gjelder


def test_en_dollarkropp_bak_en_kommentar_er_fortsatt_en_kropp(tmp_path):
    """`AS /* … */ $$ … $$` er en funksjonskropp, ikke en verdi.

    Codex P2 på #118, tjuende runde: skillet mellom kropp og verdi ble avgjort
    av hva som sto rett FORAN dollartaggen, målt i den delvis maskerte
    teksten. En helt alminnelig kommentar mellom `AS` og kroppen skjøv `AS` ut
    av synsfeltet, spennet ble lest som én verdi, og navnene i kommentarene
    inne i kroppen kom inn i hvitlisten som om registeret hadde skrevet dem.

    Grammatikken har ikke det spørsmålet: en kommentar finnes ikke i treet, og
    kroppen er kroppen.
    """
    sql = ("CREATE FUNCTION f() RETURNS text LANGUAGE sql AS /* merknad */ $$\n"
           "    -- het 'oppfunnet_klasse' før\n"
           "    SELECT 'ekte_verdi';\n"
           "$$;\n")
    verdier = _skrevne_verdier(sql)
    assert "ekte_verdi" in verdier, "kroppen ble lest som én verdi"
    assert "oppfunnet_klasse" not in verdier, (
        "en kommentar inne i kroppen ble lest som noe registeret skriver")


def test_uvissheten_snittes_ikke_bort_av_et_annet_vilkaar(tmp_path):
    """Et lesbart vilkår ved siden av kan ikke opplyse det uleselige.

    Snittet fjerner verdier vilkårene er uenige om, og `ULESELIG_SQL` ville
    forsvunnet i den operasjonen — da sto et vilkår porten bare kjenner et
    oversett av, igjen som lest, og svaret ble videre enn databasen.

    Her møtes de to reglene: `A AND B` snittes ledd for ledd, så lista bidrar
    med sine to verdier, mens `<>`-leddet gjør kolonnen uviss. Uvissheten blir
    stående gjennom snittet, både innenfor vilkåret og mellom vilkårene.
    """
    mappe = _migrasjoner(
        tmp_path, _LAGER_VILKAR,
        "ALTER TABLE modulkontrakt\n"
        "    ADD CONSTRAINT rev_smal CHECK (\n"
        "        reversibilitet IN ('direkte', 'kompenserende')\n"
        "        AND reversibilitet <> 'kompenserende');\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "direkte", "kompenserende", ULESELIG_SQL}


def test_en_verdi_brutt_over_to_linjer_er_en_verdi(tmp_path):
    """PostgreSQL skjøter fragmentene; talt hver for seg blir de to verdier.

    Da hadde enumet båret `kompen` og `serende` — verdier registeret aldri
    godtar — mens den ekte, `kompenserende`, ikke sto der i det hele tatt: en
    katalogmodul som bærer den, ville blitt avvist av porten og godtatt av
    databasen.
    """
    mappe = _migrasjoner(
        tmp_path,
        "CREATE TABLE modulkontrakt (\n"
        "    reversibilitet TEXT NOT NULL\n"
        "        CHECK (reversibilitet IN ('direkte', 'kompen'\n"
        "                                  'serende')));\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "direkte", "kompenserende"}


@pytest.mark.parametrize("vilkar", [
    # Et ledd som er et UTTRYKK og ikke én konstant (Codex P2 på #118, tjuende
    # runde). PostgreSQL godtar `direkte_v2` og `irreversibel`, ikke tre
    # verdier — porten leste tre, og en av dem var oppfunnet.
    "CHECK (reversibilitet IN ('direkte' || '_v2', 'irreversibel'))",
    # En CHECK som narrer UTEN `IN` (samme runde). Den ble forkastet i
    # stillhet, så det forrige, videre vilkåret sto igjen som gjeldende.
    "CHECK (reversibilitet = 'direkte')",
    "CHECK (reversibilitet <> 'direkte')",
    "CHECK (length(reversibilitet) < 20)",
    # Og et sammensatt predikat der lista bare er et oversett.
    "CHECK (reversibilitet IN ('direkte') AND reversibilitet <> 'direkte')",
])
def test_et_vilkaar_porten_ikke_kan_regne_paa_stopper_enumoppslaget(
        tmp_path, vilkar):
    """Et vilkår porten ikke kan regne på skal si fra, ikke gjettes på.

    Hver av formene her ble lest FEIL av skanneren, og alle på samme måte: den
    fant en `IN`-liste (eller ingen), meldte den som hele sannheten, og gikk
    videre. Da kan katalogporten godta en kontraktklasse PostgreSQL avviser —
    nøyaktig det den finnes for å fange.

    Svaret er hverken et gjett eller taushet: kolonnen merkes uviss, og
    `_registerenum()` sier fra på den kolonnen katalogen faktisk måles mot.
    Det gjør at en form ingen har tenkt på gir en RØD port, ikke en stille
    videre en — og det er den egenskapen som gjør at lista over former ikke
    trenger å være komplett.
    """
    mappe = _migrasjoner(
        tmp_path, _LAGER_VILKAR,
        f"ALTER TABLE modulkontrakt ADD CONSTRAINT smalere {vilkar};\n")
    gjeldende, noen_gang = _registerets_enums(mappe)
    assert ULESELIG_SQL in gjeldende[(MODULKONTRAKT, "reversibilitet")]
    # Historikken bærer den ikke videre — `pensjonert` skal ikke bygges på et
    # innhold porten ikke leste.
    assert ULESELIG_SQL not in noen_gang


def test_en_escapet_verdi_er_verdien_escapen_gir(tmp_path):
    """`E'…'` og dollarsitering er verdier, ikke noe porten må gjette på.

    Begge ga `ULESELIG_SQL` før, og med god grunn: å tolke dem betydde å skrive
    PostgreSQLs escaperegler av i Python — `\\n`, `\\xHH`, `\\uXXXX`, oktalt —
    og hver glemt form er et nytt smutthull. Nå er det den som EIER reglene som
    løser dem opp, så verdien er verdien, og porten trenger ingen liste.
    """
    mappe = _migrasjoner(
        tmp_path,
        "CREATE TABLE modulkontrakt (\n"
        "    reversibilitet TEXT NOT NULL\n"
        "        CHECK (reversibilitet IN (E'direk\\x74e', $$irreversibel$$,\n"
        "                                  'det''s')));\n")
    gjeldende, _ = _registerets_enums(mappe)
    assert gjeldende[(MODULKONTRAKT, "reversibilitet")] == {
        "direkte", "irreversibel", "det's"}


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

    Enumene leses fra `modulkontrakt` og ikke som en union over alle tabeller
    som tilfeldigvis har en kolonne med samme navn — det er den tabellen
    kontrakten lagres i, og altså den som sier nei. Se `_registerenum()`.

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
    registeret BINDER i et vilkår måles derfor mot gjeldende tilstand: er den
    ute av vilkåret, er den ute av lista. En verdi som aldri har stått i et
    CHECK (`alltid_stopp` er en modus, håndhevet i kode) berøres ikke — den har
    ingen registertilstand å falle ut av, og filstammene er gjeldende tilstand
    i seg selv, siden en slettet fil forsvinner fra `git ls-files`.

    Og verdiene leses ut av det migrasjonen SKRIVER, ikke ut av råteksten. Tre
    runder på #118 gikk med på hva «skriver» betyr, og hver av dem lot et navn
    ingen tabell har hørt om komme inn i lista: en KOMMENTAR som nevnte
    `oppfunnet_klasse` i forbifarten (femtende runde), en MELDING der navnet
    sto mellom doblede apostrofer (syttende), og en DOLLARSITERT melding
    (attende). Alle tre er svar grammatikken gir uten å bli spurt — en
    kommentar finnes ikke i et syntakstre, og en melding er ÉN verdi uansett
    hvordan den er sitert. Se `_skrevne_verdier()`.

    En KROPP er derimot noe registeret skriver: verdiene i `AS $$ … $$` teller,
    selv om setningen bare definerer funksjonen uten å kjøre den. Det er
    forskjellen fra `_registerets_enums()`, som spør hva som er HÅNDHEVET.
    """
    gjeldende, noen_gang = _registerets_enums()
    pensjonert = noen_gang - set().union(*gjeldende.values(), set())
    ut: set[str] = set()
    for sql in sorted(MIGRASJONER.glob("*.sql")):
        ut.update(t for t in _skrevne_verdier(sql.read_text(encoding="utf-8"))
                  if IDENT_RE.fullmatch(t))
    ut -= pensjonert
    spor = subprocess.run(["git", "ls-files", "-z"], cwd=ROT,
                          capture_output=True, text=True, check=True)
    ut.update(Path(rel).stem for rel in spor.stdout.split("\0") if rel)
    return ut


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
    # En `DO`-kropp er en kropp her på samme måte som i `_hendelsene()`.
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

    Lesningen er en annen enn den `_registerets_enums()` bruker, og det er med
    vilje: der spør vi hva registeret HÅNDHEVER nå, her hva det SKRIVER. En
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
            for v in (verdi if isinstance(verdi, list) else [verdi]):
                if isinstance(v, str):
                    stykker.append((f"M-{post['n']}.{felt}", v))
    return stykker


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
