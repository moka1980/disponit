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
                 med. Prosa er dokumentteksten pluss fnutter og kommentarer i
                 skriptet; prototypens egne variabelnavn er kode, ikke påstand.
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


# Migrasjonsmappa er den ENESTE historikken i repoet som ikke kan redigeres:
# en fil som er kjørt står for alltid, også når en senere fil erstatter det den
# innførte. Alt annet — kildekode, maler, katalogen — er gjeldende tilstand,
# fordi en fjernet verdi forsvinner med redigeringen. Derfor må registerets
# gjeldende tilstand REGNES ut av migrasjonene, ikke leses som en union.
#
# Nøkkelen er (tabell, kolonne), ikke kolonnenavnet alene: `status` er CHECK-et
# i tjue tabeller med hver sine verdier, og siste-treff-vinner på bare navnet
# ville pensjonert nitten av dem. Tabellen er den siste CREATE/ALTER TABLE før
# vilkåret. Alt som ikke er en KJØRT setning blankes ut først — 038 SITERER
# formen «CHECK (hendelse IN (...))» i en kommentar, og den ville ellers slettet
# en tabells enum. Begge kommentarformene, og dollarsitert tekst, og ingen av
# delene inne i en streng: se `_kjort_sql()`.
#
# Og navnet må NORMALISERES før det brukes som nøkkel (Codex P2 på #118, femte
# runde). Migrasjonene skriver samme tabell på to måter: 026 oppretter `varsel`,
# 035 endrer `public.varsel` — konvensjonen i repoet er å kvalifisere med skjema
# når setningen står inne i en `DO`-blokk. Uten normalisering blir det to
# nøkler, og «siste vilkår gjelder» slutter å gjelde på tvers av dem: en senere
# innstramming skrevet `public.<tabell>` ville ikke pensjonert noe som helst,
# fordi den la seg ved siden av den opprinnelige i stedet for oppå. `public` er
# standardskjemaet og derfor ikke en del av identiteten; et ANNET skjema er det,
# og blir stående.
# Mellom verbet og NAVNET kan det stå nøkkelord, og lista over dem sto som en
# åpen oppramsing: bare `IF [NOT] EXISTS` (Codex P2 på #118, fjortende runde).
# `ALTER TABLE ONLY modulkontrakt …` er en helt alminnelig form — den er
# nettopp måten man endrer bare arvetabellen på — og der ble `ONLY` fanget som
# tabellNAVNET. En innstramming la seg da under nøkkelen `('only', kolonne)`
# mens det forrige, videre vilkåret på `modulkontrakt` ble stående som
# gjeldende, og katalogporten ville sluppet inn en klasse PostgreSQL avviser.
#
# Lista er derfor lukket mot grammatikken i stedet for mot tilfellene: ordene
# som KAN stå mellom `TABLE` og navnet er `IF`, `NOT`, `EXISTS` og `ONLY`, og
# det er hele mengden PostgreSQL tillater der. Alle fire er reserverte ord, så
# ingen tabell kan hete noe av det uten anførselstegn — og med anførselstegn
# begynner navnet med `"` og treffer ikke ordmengden. Modifikatorene FORAN
# `TABLE` er samme sak fra samme rot: `CREATE TEMP TABLE t` og `CREATE UNLOGGED
# TABLE t` traff ikke mønsteret i det hele tatt, så et vilkår i en slik tabell
# ville festet seg til forrige tabell i filen.
_TABELL_RE = re.compile(
    r"""\b(?:CREATE(?:\s+(?:GLOBAL|LOCAL|TEMPORARY|TEMP|UNLOGGED)\b)*"""
    r"""|ALTER)\s+TABLE(?:\s+(?:IF|NOT|EXISTS|ONLY)\b)*\s+([\w.\"]+)""",
    re.I)
# KOLONNEN i vilkåret kan være sitert (Codex P2 på #118, sekstende runde).
# `\w+` leste bare den bare formen, så `CHECK ("reversibilitet" IN (…))` ikke
# ble et treff i det hele tatt — og et vilkår porten ikke ser, er et vilkår den
# ikke snitter med. Legger en migrasjon en innstramming skrevet slik ved siden
# av det videre vilkåret som står, blir bare det videre gjeldende, og
# katalogporten slipper inn en klasse den siterte CHECK-en avviser.
#
# Anførselstegnene er samme skrivemåte som `_tabellnavn()` og `_vilkarsnavn()`
# alt normaliserer bort: `"reversibilitet"` og `reversibilitet` ER samme
# kolonne, fordi PostgreSQL folder den bare formen ned til små bokstaver.
# Mengden er lukket mot grammatikken — en identifikator er ETT sitert navn
# eller ETT bart ord, ikke en åpen tegnklasse som også ville tatt `"a" + b`.
#
# Og UTTRYKKET leses ikke lenger i det samme regexet (Codex P2 på #118,
# syttende runde). Kolonnen måtte stå rett etter `CHECK (`, så en overflødig
# parentes rundt vilkåret — `CHECK ((reversibilitet IN ('direkte')))`, en helt
# alminnelig skrivemåte — ga ikke et treff i det hele tatt, med samme utfall
# som den siterte kolonnen forrige runde: en innstramming porten ikke ser, er
# en innstramming den ikke snitter med.
#
# Parenteser lar seg ikke telle i et regex, så mønsteret rekker bare til
# ÅPNINGEN av `CHECK (`; innholdet leses av `_vilkarene()`, som finner den
# lukkende parentesen ved å telle og skreller bort dem som bare pakker inn.
# Samme lesning gir verdilista sin ekte slutt: `([^)]*)` stoppet ved den
# første `)`, også når den sto inne i en streng.
_CHECK_RE = re.compile(r"""(?:CONSTRAINT\s+([\w."]+)\s+)?CHECK\s*\(""", re.I)
# Innsiden av et lesbart vilkår: kolonnen, og åpningen av verdilista.
_IN_RE = re.compile(r"""("(?:[^"]|"")+"|\w+)\s+IN\s*\(""", re.S | re.I)
# Et vilkår kan også FJERNES (Codex P2 på #118, ellevte runde). Tilstanden ble
# regnet bare av CHECK-treff, så en `DROP CONSTRAINT` uten et nytt vilkår etter
# seg lot verdiene fra forrige migrasjon bli stående som gjeldende: katalogporten
# ville avvist verdier PostgreSQL ikke lenger begrenser, og `_registerenum()`
# ville ikke sagt fra om at vilkåret er borte. Slippet er en hendelse på linje
# med vilkåret, og leses i samme rekkefølge som det.
_DROPP_RE = re.compile(
    r"""DROP\s+CONSTRAINT(?:\s+IF\s+EXISTS)?\s+([\w."]+)""", re.I)


# Dollarsitering: `$$…$$` og `$tagg$…$tagg$`. Formen brukes til tekst som bærer
# fnutter og semikolon, og ingenting escaper der inne.
_DOLLARTAGG_RE = re.compile(r"\$(?:[A-Za-z_]\w*)?\$")

# Men en dollarsitert tekst er ikke ett slag (Codex P2 på #118, fjortende
# runde). ETT sted er den en KROPP som kjøres nå: `DO $$ … $$` utfører sine
# egne setninger i det migrasjonen kjører, og repoets konvensjon er nettopp å
# pakke betinget DDL slik — 035 §6 legger `varsel_art_chk` på `public.varsel`
# inne i en `DO`-blokk. Overalt ellers er den DATA: `AS $$ … $$` definerer en
# funksjonskropp uten å kjøre den, og `RAISE NOTICE $$ … $$` skriver en melding.
#
# Hvilket av de to det er, leses av det som står FORAN taggen, ikke av
# innholdet. `DO` tar valgfritt `LANGUAGE <navn>` mellom seg og kroppen.
_DOKROPP_RE = re.compile(r"\bDO\b(?:\s+LANGUAGE\s+[\w.\"]+)?\s*\Z", re.I)

# Lesningen av hva registeret SKRIVER teller også funksjonskroppen som kropp:
# `CREATE FUNCTION … AS $$ … $$` definerer den uten å kjøre den, men verdiene
# den bærer er skrevet av registeret. Se `_er_kropp()` mot `_er_dokropp()`.
_KROPP_RE = re.compile(
    r"\b(?:DO\b(?:\s+LANGUAGE\s+[\w.\"]+)?|AS)\s*\Z", re.I)

# Verdien en CHECK-liste får når porten ikke kan lese den. Backslash kan ikke
# være en enumverdi skrevet rett fram, så den kan ikke kollidere med en ekte.
ULESELIG_SQL = "\\"


def _sqlliteral(sql: str, i: int) -> int:
    """Indeksen etter strengliteralen som åpner i `i`, eller -1 om ingen gjør.

    PostgreSQL har tre former, og porten leste bare den ene:

      * `'…'` og `"…"` (sitert navn) — sitattegnet DOBLES for å escapes, så
        `'det''s'` er ÉN streng.
      * `E'…'` — der escaper `\\` også, og `\\'` lukker IKKE strengen (Codex P2
        på #118, trettende runde). Porten leste den escapede fnutten som
        slutten, og resten av `E'a\\'--b'` ble til en linjekommentar som spiste
        vilkåret linja bar. Merk at `E'\\\\_\\\\_%'` i 041 fortsatt lukker der den
        skal: `\\\\` er en escapet backslash og spises som ett par, så fnutten
        etter den er den ekte.
      * `$tagg$…$tagg$` — dollarsitering, som porten ikke kjente i det hele
        tatt. Der escaper ingenting; strengen slutter ved den samme taggen.

    `E` er bare et prefiks når det står alene: `case'x'` er et ord etterfulgt
    av en streng, ikke en escapestreng.
    """
    n = len(sql)
    c = sql[i]
    if c == "$":
        treff = _DOLLARTAGG_RE.match(sql, i)
        if not treff:
            return -1
        j = sql.find(treff.group(0), treff.end())
        return n if j < 0 else j + len(treff.group(0))
    bakstrek = False
    if c in "Ee" and i + 1 < n and sql[i + 1] == "'" \
            and not (i and (sql[i - 1].isalnum() or sql[i - 1] == "_")):
        i, c, bakstrek = i + 1, "'", True
    if c not in "'\"":
        return -1
    j = i + 1
    while j < n:
        if bakstrek and sql[j] == "\\":
            j += 2
        elif sql[j] != c:
            j += 1
        elif j + 1 < n and sql[j + 1] == c:
            j += 2
        else:
            return j + 1
    return n


def _strengkonstant(sql: str, i: int, b: int) -> tuple[str | None, int]:
    """(verdien til strengkonstanten som åpner i `i`, indeksen etter den).

    En strengkonstant i PostgreSQL kan være skrevet i FLERE fragmenter (Codex
    P2 på #118, attende runde): står to `'…'` etter hverandre med bare tomrom
    mellom seg, og tomrommet inneholder minst ett LINJESKIFT, skjøter serveren
    dem til én verdi. Formen brukes gjennomgående i migrasjonene til å bryte
    lange meldinger over flere linjer.

    Porten leste hvert fragment som en verdi for seg, og begge lesningene tok
    skade av det. `SELECT 'mangler '\\n'oppfunnet_klasse';` la navnet i lista
    over kjente identifikatorer, enda databaseverdien er hele meldingen — samme
    hull som kommentaren og den doblede fnutten. Og en `IN`-liste med en
    brutt verdi i fikk verdier registeret aldri godtar, så porten ville avvist
    en katalogmodul som bar den ekte.

    Uten linjeskift er det ikke en skjøt, men en syntaksfeil i PostgreSQL, og
    da skal porten heller ikke skjøte. Kommentarene mellom fragmentene er alt
    byttet mot mellomrom av `_uten_sqlkommentar()`, som beholder linjeskiftene,
    så en kommentar midt i skjøten teller som tomrommet den er.

    `None` betyr uleselig, og det smitter: er ETT fragment en escapestreng
    (`E'…'`) eller mangler sin lukkende fnutt, er hele den skjøtte verdien
    ukjent. Å bruke resten ville vært et gjett.
    """
    deler: list[str] = []
    lesbar = True
    while True:
        slutt = _sqlliteral(sql, i)
        if slutt < 0:
            break
        slutt = min(slutt, b)
        if sql[i] == "'" and slutt - i >= 2 and sql[slutt - 1] == "'":
            deler.append(sql[i + 1:slutt - 1].replace("''", "'"))
        else:
            lesbar = False
        i = slutt
        neste = _skjot(sql, i, b)
        if neste < 0:
            break
        i = neste
    return ("".join(deler) if lesbar else None), i


def _skjot(sql: str, i: int, b: int) -> int:
    """Der neste fragment av samme strengkonstant åpner, ellers -1.

    Kravet er PostgreSQLs eget: bare tomrom mellom fragmentene, og minst ett
    linjeskift i det. Se `_strengkonstant()`.
    """
    j = i
    while j < b and sql[j].isspace():
        j += 1
    if j >= b or "\n" not in sql[i:j]:
        return -1
    if sql[j] == "'" or (sql[j] in "Ee" and j + 1 < b and sql[j + 1] == "'"):
        return j
    return -1


def _sqlverdier(liste: str) -> set[str]:
    """Strengverdiene i en `IN (…)`-liste.

    Leses med `_sqlliteral()` og ikke med et regex på «alt mellom to fnutter»:
    SQL escaper ved å DOBLE sitattegnet, så `'det''s'` er ÉN verdi. Et regex
    delte den i to og fikk en verdi til ut av lista.

    En verdi skrevet med escape — `E'…'` — eller dollarsitert gir
    `ULESELIG_SQL`. Å tolke ville betydd å skrive PostgreSQLs escaperegler av
    i Python (`\\n`, `\\xHH`, `\\uXXXX`, oktalt), og hver glemt form er et nytt
    smutthull; her ville et gjettet innhold vært verre enn ingen. Verdien
    forplanter seg til enumet, og `_registerenum()` sier fra hvis kolonnen
    katalogen faktisk måles mot bærer den — ikke ved enhver migrasjon som
    tilfeldigvis skriver en slik verdi i en annen tabell.

    Og én verdi kan være skrevet i FLERE fragmenter, se `_strengkonstant()`.
    Ble de talt hver for seg, fikk lista verdier PostgreSQL aldri godtar, og
    porten ville avvist en katalogmodul som bar den ekte, sammenskjøtte
    verdien.
    """
    ut, i, n = set(), 0, len(liste)
    while i < n:
        j = _sqlliteral(liste, i)
        if j < 0:
            i += 1
            continue
        if liste[i] == '"':
            ut.add(liste[i + 1:j - 1].replace('""', '"'))
            i = j
            continue
        if liste[i] == "$":
            ut.add(ULESELIG_SQL)
            i = j
            continue
        verdi, i = _strengkonstant(liste, i, n)
        ut.add(ULESELIG_SQL if verdi is None else verdi)
    return ut


def _balansert(sql: str, i: int) -> int:
    """Indeksen til parentesen som lukker den som åpner i `i`, ellers -1.

    Parenteser lar seg ikke telle i et regex, og et vilkår er nettopp nøstede
    parenteser: `CHECK ((kolonne IN ('a')))` er samme vilkår som uten det ytre
    paret. Tellingen hopper over strengliteralene med `_sqlliteral()`, slik at
    en parentes inne i en verdi — `'a)b'` — ikke lukker noe.
    """
    if i >= len(sql) or sql[i] != "(":
        return -1
    dybde, j, n = 0, i, len(sql)
    while j < n:
        slutt = _sqlliteral(sql, j)
        if slutt >= 0:
            j = slutt
            continue
        if sql[j] == "(":
            dybde += 1
        elif sql[j] == ")":
            dybde -= 1
            if not dybde:
                return j
        j += 1
    return -1


def _uten_ytre_parentes(uttrykk: str) -> str:
    """Uttrykket uten parentesparene som bare pakker det inn.

    Bare et par som spenner over HELE uttrykket er overflødig. `(a IN ('x'))
    OR (b IN ('y'))` begynner også med en parentes, men den lukkes midtveis —
    der ville en skrelling gjort en disjunksjon om til det første leddet, altså
    lest et vilkår som strengere enn det er.
    """
    s = uttrykk.strip()
    while s.startswith("(") and _balansert(s, 0) == len(s) - 1:
        s = s[1:-1].strip()
    return s


def _vilkarene(tekst: str) -> list[tuple[int, str | None, str, set[str]]]:
    """(posisjon, vilkårsnavn, kolonne, verdier) for hvert lesbart `CHECK`.

    Lesbart betyr her at HELE vilkåret er `<kolonne> IN (…)`, eventuelt pakket
    i overflødige parenteser. Et vilkår av en annen form — `CHECK (beløp > 0)`
    — hører ikke til noe enum og gis fra seg, som før: der finnes ingen kolonne
    å tilskrive noe.

    Men et vilkår som BÅDE har en `IN`-liste og noe mer, er en tredje ting
    (Codex P2 på #118, attende runde). `IN`-lista ble lest, og resten av
    predikatet gitt fra seg i stillhet: `CHECK ((reversibilitet IN ('direkte',
    'irreversibel') AND reversibilitet <> 'irreversibel'))` ble meldt som om
    begge verdiene var lov, mens PostgreSQL avviser den ene. Da kan
    katalogporten godta en umulig kontrakt — nøyaktig det den finnes for å
    fange.

    Et sammensatt predikat kan bare gjøre vilkåret SMALERE enn lista, aldri
    videre, men hvor mye smalere står i den delen porten ikke leser. Kolonnen
    kjenner vi likevel, og da er `ULESELIG_SQL` svaret som hverken gjetter
    eller tier: verdien forplanter seg til enumet, og `_registerenum()` sier
    fra på den kolonnen katalogen faktisk måles mot. Det gjelder også når
    lista står SIST — `col <> 'x' AND col IN (…)` — derfor søkes det etter
    `IN` i stedet for å kreve den fremst; kravet er at treffet fyller
    uttrykket.
    """
    ut = []
    for m in _CHECK_RE.finditer(tekst):
        slutt = _balansert(tekst, m.end() - 1)
        if slutt < 0:
            continue
        uttrykk = _uten_ytre_parentes(tekst[m.end():slutt])
        inn = _IN_RE.search(uttrykk)
        if not inn:
            continue
        liste = _balansert(uttrykk, inn.end() - 1)
        if liste < 0:
            continue
        verdier = _sqlverdier(uttrykk[inn.end():liste])
        if inn.start() or uttrykk[liste + 1:].strip():
            verdier = verdier | {ULESELIG_SQL}
        ut.append((m.start(), m.group(1), inn.group(1), verdier))
    return ut


def _kjort_sql(sql: str) -> tuple[str, list[tuple[int, int]],
                                  list[tuple[int, int]]]:
    """(SQL-en uten alt som ikke er kjørt, DATA-spennene, de BETINGEDE).

    Bare `--` ble fjernet før, med et regex per linje (Codex P2 på #118, tolvte
    runde). Tre hull fulgte av det.

    BLOKKOMMENTAREN sto igjen som kode. En migrasjon som setter en setning ut av
    drift eller viser et eksempel — `/* … CHECK (reversibilitet IN ('oppfunnet'))
    … */` — ble da lest som gjeldende tilstand, og katalogporten ville sluppet
    inn en klasse PostgreSQL aldri godtar. Et blokkommentert `DROP CONSTRAINT`
    gikk motsatt vei og slettet et vilkår som fortsatt gjelder. Blokkommentarer
    NØSTES i PostgreSQL, så dybden telles.

    Kommentartegnene ble lest også inne i STRENGER: en verdi som `'a--b'` kappet
    resten av linja, og med den et vilkår som sto der. Strenger og siterte navn
    hoppes derfor over først — de er data, ikke kommentarer.

    Og DOLLARSITERT tekst ble lest som setninger (Codex P2 på #118, trettende
    runde). `RAISE NOTICE $$CHECK (reversibilitet IN ('oppfunnet'))$$` KJØRER
    ingen slik CHECK — PostgreSQL skriver en melding — men porten leste vilkåret
    som gjeldende tilstand og ville sluppet klassen inn.

    Så ble ALT dollarsitert maskert, og det var å bytte hullet mot det motsatte
    hullet (Codex P2 på #118, fjortende runde). `DO $$ BEGIN … END $$` er ikke
    data: PostgreSQL KJØRER kroppen der og da, og repoets konvensjon er nettopp
    å pakke betinget DDL slik — 035 §6 legger `varsel_art_chk` på
    `public.varsel` inne i en `DO`-blokk, og 035 er også grunnen til at
    tabellnavn må normaliseres over `public.`. Med hele kroppen maskert ble et
    innpakket `DROP CONSTRAINT` usynlig, så et sluppet vilkår ble stående som
    gjeldende, og en innpakket innstramming forsvant ut av snittet. Begge veier
    kan katalogporten godta en klasse PostgreSQL avviser.

    Slaget avgjøres av det som står FORAN taggen, ikke av innholdet: en kropp
    etter `DO` leses videre som kjørt SQL — bare taggene blankes — og alt annet
    dollarsitert er data og blankes helt. `AS $$ … $$` DEFINERER en funksjon
    uten å kjøre den, og hører derfor til data sammen med meldingsteksten.
    Lesningen av kroppen er den samme funksjonen om igjen, så en dollarsitert
    tekst INNE i kroppen er data på nytt — `RAISE NOTICE $$…$$` binder
    fortsatt ingenting.

    Dynamisk DDL som FAKTISK kjøres — `EXECUTE format(...)` med et vilkår i en
    vanlig streng — blir dermed usynlig for porten. Det er med vilje: en
    setning som først blir til når migrasjonen kjører, kan ikke leses av en
    tekstlesning uansett, og et gjettet vilkår er verre enn ingen. `_registerenum()`
    sier fra hvis kolonnen katalogen måles mot ender opp uten vilkår.

    Lengden holdes, for hver maskering byttes tegn for tegn mot mellomrom.
    Rekkefølgen mellom `CREATE TABLE`, `CHECK` og `DROP CONSTRAINT` leses av
    posisjonene i teksten, og linjeskift beholdes så `--` fortsatt slutter der
    linja slutter.

    STRENGENE blir stående, og derfor gis spennene deres fra seg (Codex P2 på
    #118, femtende runde). De må stå: verdiene et vilkår binder ER strenger, og
    blankes de, forsvinner enumet sammen med dem. Men innholdet i en streng er
    DATA, ikke setninger — en verdi som `'ALTER TABLE modulkontrakt DROP
    CONSTRAINT r'` skrevet inn i en loggtabell ble lest som et slipp, og et
    vilkår PostgreSQL fortsatt håndhever forsvant ut av snittet. Da kan
    katalogporten godta en klasse databasen avviser.

    Skillet går på hvor en HENDELSE begynner: `CHECK`, `DROP CONSTRAINT` og
    `CREATE`/`ALTER TABLE` er nøkkelord, og et nøkkelord inne i en streng er
    tekst. Verdiene i `IN (…)` står inne i strenger, men hendelsen de hører til
    begynner utenfor — så et vilkår leses fortsatt helt, mens data ikke kan
    utgi seg for å være en setning.

    Og en setning i en `DO`-kropp er ikke nødvendigvis KJØRT (Codex P2 på #118,
    femtende runde): kroppen er PL/pgSQL, og repoets konvensjon er å pakke DDL
    i `IF … THEN … END IF`. Hele kroppen ble lest som ubetinget, så et
    `IF FALSE THEN` rundt et slipp og et nytt vilkår ga porten erstatningen
    mens PostgreSQL beholder originalen. Betingelsen er vilkårlig SQL som først
    avgjøres når migrasjonen kjører, og et gjettet svar er verre enn ingen — så
    de betingede spennene gis fra seg, og `_registerets_enums()` velger den
    tolkningen som ALDRI utvider mengden verdier. Se der.
    """
    ut = list(sql)
    data: list[tuple[int, int]] = []
    kropper: list[tuple[int, int]] = []
    _mask_ikkekjort(sql, ut, 0, len(sql), data, kropper)
    tekst = "".join(ut)
    betinget: list[tuple[int, int]] = []
    for a, b in kropper:
        betinget += _betingede_spenn(tekst, a, b, data)
    return tekst, data, betinget


def _i_data(spenn: list[tuple[int, int]], pos: int) -> bool:
    """Står `pos` inne i et av spennene? Se `_kjort_sql()`."""
    return any(a <= pos < b for a, b in spenn)


# `IF … THEN … END IF` i en PL/pgSQL-kropp. `ELSIF` treffer ikke, for `\b`
# krever ordgrense foran `IF`.
_IFORD_RE = re.compile(r"\bEND\s+IF\b|\bIF\b", re.I)
# Det som avgjør om et `IF` åpner en BETINGELSE: står det `THEN` før setningen
# slutter, er det en if-setning. `DROP CONSTRAINT IF EXISTS c;` og `CREATE
# TABLE IF NOT EXISTS t (…);` bærer samme ord uten å være en gren.
_THEN_ELLER_SLUTT_RE = re.compile(r"\bTHEN\b|;", re.I)


def _betingede_spenn(tekst: str, a: int, b: int,
                     data: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Spennene i `tekst[a:b]` der en setning står bak en betingelse.

    Kroppen til en `DO`-blokk er PL/pgSQL, og der er `IF … THEN … END IF` den
    formen repoet pakker DDL i — 035 §6 legger `varsel_art_chk` på
    `public.varsel` nettopp slik. Betingelsen er en spørring mot katalogen,
    altså noe som først avgjøres når migrasjonen kjører; en tekstlesning kan
    ikke vite svaret. Den KAN vite at svaret er ukjent, og det er nok, se
    `_registerets_enums()`.

    Bare de ytterste spennene gis fra seg: en nøstet gren er betinget av begge,
    og et spenn som dekker den ytterste dekker den derfor også.
    """
    ut: list[tuple[int, int]] = []
    stabel: list[int] = []
    for m in _IFORD_RE.finditer(tekst, a, b):
        if _i_data(data, m.start()):
            continue
        if m.group(0)[0] in "Ee":  # `END IF`
            if stabel:
                start = stabel.pop()
                if not stabel:
                    ut.append((start, m.end()))
            continue
        if _apner_gren(tekst, m.end(), b, data):
            stabel.append(m.start())
    # En gren som aldri lukkes er en kropp vi ikke har lest ferdig; da er alt
    # fra den og ut betinget. Det er den trygge veien — se `_registerets_enums()`.
    if stabel:
        ut.append((stabel[0], b))
    return ut


def _apner_gren(tekst: str, i: int, b: int,
                data: list[tuple[int, int]]) -> bool:
    """Følger `THEN` før setningen slutter? Se `_betingede_spenn()`."""
    for m in _THEN_ELLER_SLUTT_RE.finditer(tekst, i, b):
        if not _i_data(data, m.start()):
            return m.group(0) != ";"
    return False


def _mask_ikkekjort(sql: str, ut: list, a: int, b: int,
                    data: list[tuple[int, int]],
                    kropper: list[tuple[int, int]]) -> None:
    """Blank ut alt i `sql[a:b]` som ikke er en kjørt setning. Se `_kjort_sql()`.

    Egen funksjon fordi kroppen til en `DO`-blokk er nøyaktig samme spørsmål om
    igjen på et mindre spenn: den kjøres, så kommentarer og strenger inne i den
    skal maskeres på samme måte som utenfor.
    """
    i = a
    while i < b:
        slutt = _sqlliteral(sql, i)
        if slutt >= 0:
            slutt = min(slutt, b)
            if sql[i] == "$":
                tagg = _DOLLARTAGG_RE.match(sql, i).group(0)
                if _er_dokropp(ut, a, i):
                    # Kroppen kjøres: taggene bort, innholdet leses videre.
                    innen = min(i + len(tagg), slutt)
                    lukk = sql.find(tagg, innen, slutt)
                    lukk = slutt if lukk < 0 else lukk
                    _blank(ut, i, innen)
                    _blank(ut, lukk, slutt)
                    _mask_ikkekjort(sql, ut, innen, lukk, data, kropper)
                    kropper.append((innen, lukk))
                else:
                    _blank(ut, i, slutt)
            else:
                # En fnuttstreng eller et sitert navn: innholdet blir stående,
                # men det er data og ingen setning. Se `_kjort_sql()`.
                data.append((i, slutt))
            i = slutt
            continue
        j = _sqlkommentar(sql, i, b)
        if j < 0:
            i += 1
            continue
        _blank(ut, i, j)
        i = j


def _sqlkommentar(sql: str, i: int, b: int) -> int:
    """Indeksen etter kommentaren som åpner i `i`, eller -1 om ingen gjør.

    `--` slutter der linja slutter. `/* … */` NØSTES i PostgreSQL, så dybden
    telles — den ytre lukkes av den siste `*/`, ikke av den første.
    """
    if sql.startswith("--", i):
        j = sql.find("\n", i, b)
        return b if j < 0 else j
    if not sql.startswith("/*", i):
        return -1
    j, dybde = i + 2, 1
    while j < b and dybde:
        if sql.startswith("/*", j):
            dybde, j = dybde + 1, j + 2
        elif sql.startswith("*/", j):
            dybde, j = dybde - 1, j + 2
        else:
            j += 1
    return j


def _uten_sqlkommentar(sql: str) -> str:
    """SQL-en med kommentarene byttet mot mellomrom, alt annet urørt.

    Et ANNET spørsmål enn `_kjort_sql()`, og derfor en annen lesning. Der
    spør vi hva migrasjonen håndhever nå; her spør vi hva den SKRIVER. En
    verdi i en funksjonskropp er skrevet av registeret selv om `AS $$…$$` bare
    definerer kroppen uten å kjøre den, mens en verdi i en kommentar ikke er
    skrevet noe sted — den er en merknad.

    Dollarsiterte KROPPER leses derfor videre i stedet for å blankes, og
    kommentarene inne i dem er kommentarer på samme måte som utenfor. En
    dollarsitert VERDI leses ikke inn i: der er `--` fire tegn i en tekst, ikke
    starten på en kommentar, og å blanke dem ville endret verdien registeret
    skriver. Se `_er_kropp()`.
    """
    ut = list(sql)
    _mask_kommentar(sql, ut, 0, len(sql))
    return "".join(ut)


def _mask_kommentar(sql: str, ut: list, a: int, b: int) -> None:
    """Blank kommentarene i `sql[a:b]`. Se `_uten_sqlkommentar()`."""
    i = a
    while i < b:
        slutt = _sqlliteral(sql, i)
        if slutt >= 0:
            slutt = min(slutt, b)
            if sql[i] == "$" and _er_kropp(sql, i):
                tagg = _DOLLARTAGG_RE.match(sql, i).group(0)
                innen = min(i + len(tagg), slutt)
                _mask_kommentar(sql, ut, innen, max(innen, slutt - len(tagg)))
            i = slutt
            continue
        j = _sqlkommentar(sql, i, b)
        if j < 0:
            i += 1
            continue
        _blank(ut, i, j)
        i = j


def _skrevne_verdier(sql: str, a: int | None = None,
                     b: int | None = None) -> list[str]:
    """Innholdet i hver HELE `'…'`-literal i `sql[a:b]`, escapene løst opp.

    Verdiene ble hentet med `'([^']*)'` (Codex P2 på #118, syttende runde), og
    et regex kan ikke se at SQL escaper ved å DOBLE fnutten: i `'mangler
    ''oppfunnet_klasse'''` er det ÉN literal med en melding i, men mønsteret
    leste den midterste biten som en literal for seg. Et oppfunnet navn nevnt i
    en feilmelding ble dermed en «kjent identifikator», og prosaen i
    sannhetskilden kunne presentere det som noe registeret har — porten under
    sa ingenting, fordi navnet «finnes».

    Lesningen er `_strengkonstant()`, og bare en HEL konstant teller — også
    når den er skrevet i flere fragmenter over like mange linjer.
    Escapestrengen `E'…'` gis fra seg med vilje: innholdet der krever
    PostgreSQLs escaperegler for å bli en verdi, og et gjettet innhold er
    verre enn ingen når svaret brukes til å godta et navn.

    Et dollarsitert spenn er ikke ett slag (Codex P2 på #118, attende runde).
    Alle ble lest VIDERE inn i, som om innholdet var setninger med literaler i,
    og begge utfall var gale. En melding — `RAISE EXCEPTION $$mangler
    'oppfunnet_klasse'$$` — ga navnet inni som en verdi for seg, selv om hele
    spennet er ÉN tekst; det er samme hull som kommentaren og den doblede
    fnutten, en klasse ingen tabell har hørt om. Og motsatt: en verdi skrevet
    dollarsitert — `VALUES ($$virkelig_klasse$$)` — ga ingenting, fordi det
    ikke står en apostrof i den. Ordet finnes da ikke i lista og kan ikke
    brukes i prosaen, enda registeret skriver det.

    Skillet er det samme som i `_kjort_sql()`, og det står FORAN taggen: en
    kropp leses videre inn i, alt annet er én verdi. Se `_er_kropp()`. Her er
    `AS $$…$$` med blant kroppene, i motsetning til der: en funksjonskropp er
    SKREVET av registeret selv om den ikke kjøres når migrasjonen går.
    """
    a, b = a or 0, len(sql) if b is None else b
    ut: list[str] = []
    i = a
    while i < b:
        slutt = _sqlliteral(sql, i)
        if slutt < 0:
            i += 1
            continue
        slutt = min(slutt, b)
        if sql[i] == "$":
            tagg = _DOLLARTAGG_RE.match(sql, i).group(0)
            innen = min(i + len(tagg), slutt)
            indre = max(innen, slutt - len(tagg))
            if _er_kropp(sql, i):
                ut += _skrevne_verdier(sql, innen, indre)
            elif sql[indre:slutt] == tagg:
                # Et spenn uten sin lukkende tagg er ikke helt, på samme måte
                # som en literal uten sin lukkende fnutt.
                ut.append(sql[innen:indre])
            i = slutt
        elif sql[i] == '"':
            # Et sitert NAVN er ikke en verdi registeret skriver.
            i = slutt
        else:
            # En literal uten sin lukkende fnutt er ikke hel, og en `E'…'` er
            # ikke lesbar: `_strengkonstant()` gir `None` for begge.
            verdi, i = _strengkonstant(sql, i, b)
            if verdi is not None:
                ut.append(verdi)
    return ut


def _er_kropp(sql: str, i: int) -> bool:
    """Er det dollarsiterte spennet som åpner i `i` en KROPP og ikke en verdi?

    Samme spørsmål som `_er_dokropp()` stiller, og det avgjøres på samme sted:
    av det som står FORAN taggen. Forskjellen er hva de to lesningene teller
    som kropp. `_kjort_sql()` spør hva migrasjonen HÅNDHEVER, og der er bare
    `DO $$…$$` en kropp — den kjører i det migrasjonen går, mens `AS $$…$$`
    bare definerer. Her spør vi hva registeret SKRIVER, og da er
    funksjonskroppen med: verdiene i den er skrevet av registeret uansett når
    den kalles. Se `_uten_sqlkommentar()`.

    Alt annet er en verdi og leses ikke inn i — der escaper ingenting, og hele
    spennet er én tekst.
    """
    return bool(_KROPP_RE.search(sql[:i]))


def _er_dokropp(ut: list, a: int, i: int) -> bool:
    """Er den dollarsiterte teksten som åpner i `i` kroppen til en `DO`?

    Spørsmålet stilles til den alt MASKERTE teksten foran: lesningen går
    forfra, så kommentarer før `i` er allerede byttet mot mellomrom, og et
    `-- DO` i en kommentar kan derfor ikke gjøre en meldingstekst til en kropp.

    HELE teksten foran leses, ikke de siste 120 tegnene (Codex P2 på #118,
    femtende runde). Grensen var et tall uten hjemmel i grammatikken: en lang
    kommentar eller bare rikelig med luft mellom `DO` og taggen skjøv `DO` ut
    av utsnittet, kroppen ble lest som data, og en innstramming inne i den
    falt ut av snittet — da kan katalogporten godta en verdi PostgreSQL
    avviser. Et lengre utsnitt kan ikke gi FLERE treff, bare slutte å miste
    det ene som finnes: mønsteret er forankret i slutten og krever at det bare
    står tomrom mellom `DO` og taggen.
    """
    return bool(_DOKROPP_RE.search("".join(ut[a:i])))


def _blank(ut: list, i: int, j: int) -> None:
    """Bytt ut[i:j] mot mellomrom. Linjeskift står, så `--` slutter der linja
    slutter og posisjonene ellers holder seg."""
    for k in range(i, j):
        if ut[k] != "\n":
            ut[k] = " "


def _tabellnavn(rå: str) -> str:
    """Tabellidentiteten bak et navn slik migrasjonen skrev det.

    Anførselstegn og bokstavstørrelse bort, og `public.`-prefikset bort fordi
    det er standardskjemaet: `public.varsel` og `varsel` ER samme tabell.

    Et navn som ikke lar seg løse opp — 018 gjør `ALTER TABLE public.%I` med
    tabellen i en løkkevariabel — beholdes som det står. Det er med vilje: da
    blir det sin egen nøkkel som aldri smelter sammen med en ekte tabell, i
    stedet for å bli tom streng og dermed dele nøkkel med et vilkår vi ikke
    fant noen tabell for i det hele tatt.
    """
    navn = rå.lower().replace('"', "")
    skjema, _, rest = navn.partition(".")
    return rest if rest and skjema == "public" else navn


def _registerets_enums(
        mappe: Path | None = None
) -> tuple[dict[tuple[str, str], set[str]], set[str]]:
    """(gjeldende verdier per (tabell, kolonne), verdier bundet noen gang).

    Migrasjonene leses i nummerrekkefølge, og hvert `CHECK` og `DROP
    CONSTRAINT` er en HENDELSE som endrer hvilke vilkår som står igjen. Et
    vilkår kan både UTVIDE (036 la `ekstern_lesing` til `sideeffektklasse`) og
    stramme inn, og det kan FJERNES (Codex P2 på #118, ellevte runde): et slipp
    uten et nytt vilkår etter seg lot verdiene fra forrige migrasjon bli stående
    som gjeldende, og katalogporten ville avvist verdier databasen ikke lenger
    begrenser. Vilkår og slipp leses derfor i STILLINGSREKKEFØLGE i hver fil —
    036 slipper og legger på igjen i samme setningspar, og rekkefølgen er det
    eneste som skiller dem.

    Hvilket vilkår et slipp treffer, avgjøres av NAVNET: et navngitt vilkår
    huskes som det heter, og et vilkår skrevet rett på kolonnen får navnet
    PostgreSQL selv gir det — `<tabell>_<kolonne>_check`, og `_check1`,
    `_check2` … hvis navnet er opptatt. Det er den formen 036 slipper, og den
    formen 014 la inn uten å navngi.

    Og navnet er IDENTITETEN til vilkåret, ikke (tabell, kolonne) (Codex P2 på
    #118, trettende runde). Tilstanden ble ført per kolonne, så et nytt vilkår
    på samme kolonne ERSTATTET det forrige. Det er ikke det databasen gjør:
    legger en migrasjon til en CHECK uten å slippe den gamle, håndhever
    PostgreSQL BEGGE, og en verdi må stå i begge for å slippe gjennom. Med
    erstatning kunne en modul bære en `rev`-verdi det nyeste vilkåret godtar og
    det eldste avviser — altså nøyaktig den umulige modulen porten finnes for å
    fange. Gjeldende verdier for en kolonne er derfor SNITTET av vilkårene som
    står igjen.

    En hendelse i en `DO`-kropp kan stå bak en BETINGELSE (Codex P2 på #118,
    femtende runde). Kroppen ble lest som om alt i den kjørte, og et `IF FALSE
    THEN` rundt et slipp og en erstatning ga porten erstatningens verdier mens
    PostgreSQL beholder originalen — altså et snitt som er VIDERE enn
    databasen, og katalogporten ville sluppet inn en klasse registeret avviser.

    Betingelsen er en spørring som først avgjøres når migrasjonen kjører, så
    porten kan ikke lese svaret. Den kan velge den tolkningen som aldri
    utvider: et betinget SLIPP regnes som ikke utført, så vilkåret blir
    stående, mens et betinget VILKÅR regnes som lagt på. Begge veier snevrer
    de inn. Tar porten feil, avviser den en verdi databasen godtar — det er en
    feil som stopper CI og blir rettet, i motsetning til den motsatte, som
    slipper en umulig modul gjennom i stillhet.

    De to reglene møtes når grenen slipper og legger på igjen under SAMME navn
    (Codex P2 på #118, sekstende runde). Slippet ble gitt fra seg, så det gamle
    vilkåret sto — men tillegget skrev seg på den samme nøkkelen og ERSTATTET
    det likevel, og da falt den ene halvdelen av det konservative valget bort.
    Kjørte grenen, gjelder erstatningen; kjørte den ikke, gjelder originalen —
    porten vet ikke hvilken, så BEGGE må bli stående, og snittet er da det
    eneste svaret som ikke utvider. Erstatningen legges derfor ved siden av,
    under en nøkkel ingen `DROP CONSTRAINT` kan skrive.

    `noen_gang` er unionen av alt registeret har bundet, og er noe annet: den
    er historikken en pensjonert verdi kjennes igjen på.
    """
    # {(tabell, vilkårsnavn): (kolonne, verdier)} — vilkårene som står igjen.
    vilkar: dict[tuple[str, str], tuple[str, set[str]]] = {}
    noen_gang: set[str] = set()
    for sql in sorted((mappe or MIGRASJONER).glob("*.sql")):
        tekst, data, betinget = _kjort_sql(sql.read_text(encoding="utf-8"))
        tabeller = [(m.start(), _tabellnavn(m.group(1)))
                    for m in _TABELL_RE.finditer(tekst)
                    if not _i_data(data, m.start())]
        hendelser = sorted(
            [(v[0], True, v[1:]) for v in _vilkarene(tekst)
             if not _i_data(data, v[0])]
            + [(m.start(), False, (m.group(1),))
               for m in _DROPP_RE.finditer(tekst)
               if not _i_data(data, m.start())],
            key=lambda h: h[0])
        for start, er_vilkar, felt in hendelser:
            tabell = ""
            for pos, navn in tabeller:
                if pos > start:
                    break
                tabell = navn
            if not er_vilkar:
                if _i_data(betinget, start):
                    # Et slipp bak en betingelse er ikke kjent kjørt, og et
                    # vilkår som blir stående kan bare gjøre snittet SMALERE.
                    continue
                vilkar.pop((tabell, _vilkarsnavn(felt[0])), None)
                continue
            rått_navn, rå_kolonne, verdier = felt
            if not verdier:
                continue
            kolonne = _kolonnenavn(rå_kolonne)
            navn = _vilkarsnavn(rått_navn) if rått_navn \
                else _tildelt_navn(vilkar, tabell, kolonne)
            if _i_data(betinget, start) and (tabell, navn) in vilkar:
                navn = _sidestilt_navn(vilkar, tabell, navn)
            vilkar[(tabell, navn)] = (kolonne, verdier)
            noen_gang |= verdier
    gjeldende: dict[tuple[str, str], set[str]] = {}
    for (tabell, _), (kolonne, verdier) in vilkar.items():
        nokkel = (tabell, kolonne)
        if nokkel not in gjeldende:
            gjeldende[nokkel] = set(verdier)
            continue
        # Snittet fjerner det ett vilkår ikke godtar — men det ULESELIGE er
        # ikke en verdi, det er en manglende opplysning, og et annet vilkår
        # kan ikke opplyse den. Snittes den bort, blir et vilkår porten bare
        # kjenner et OVERSETT av, meldt som lest, og da er svaret videre enn
        # databasen. Uvissheten blir derfor stående til `_registerenum()`.
        uvisst = {ULESELIG_SQL} & (gjeldende[nokkel] | verdier)
        gjeldende[nokkel] = (gjeldende[nokkel] & verdier) | uvisst
    return gjeldende, noen_gang - {ULESELIG_SQL}


def _vilkarsnavn(rå: str) -> str:
    """Vilkårsnavnet normalisert, slik `_tabellnavn()` gjør med tabellen."""
    return rå.lower().replace('"', "")


def _kolonnenavn(rå: str) -> str:
    """Kolonneidentiteten bak navnet slik vilkåret skrev det.

    Samme normalisering som `_tabellnavn()` og `_vilkarsnavn()`: den siterte og
    den bare formen er samme kolonne, og det er DEN kolonnen `gjeldende` er
    nøklet på. Uten dette ville et sitert vilkår fått sin egen nøkkel og aldri
    møtt det usiterte i snittet — samme feil som fjortende runde fant mellom
    `varsel` og `public.varsel`.
    """
    return rå.lower().replace('"', "")


def _tildelt_navn(vilkar: dict, tabell: str, kolonne: str) -> str:
    """Navnet PostgreSQL gir et vilkår ingen har navngitt.

    `<tabell>_<kolonne>_check`, og `_check1`, `_check2` … når navnet er opptatt
    — det er den formen serveren selv bruker. To vilkår uten navn på samme
    kolonne er derfor to vilkår, ikke ett som overskriver det andre, og begge
    teller med i snittet.
    """
    stamme = f"{tabell}_{kolonne}_check"
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

    Nøkkelen bærer et NUL-tegn. `_vilkarsnavn()` gir bare fra seg tegnene i en
    SQL-identifikator, så ingen `DROP CONSTRAINT` kan navngi den sidestilte
    oppføringen — og det er meningen: et senere slipp skal ikke kunne fjerne
    den halvdelen av snittet som holder porten smal.
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

    `_registerets_enums()` har alt tabellidentiteten i nøkkelen sin; her brukes
    den. Feiler oppslaget, er det fordi kolonnen ikke lenger CHECK-es på
    `modulkontrakt` — og da er det porten som skal rettes, ikke katalogen.

    En verdi porten ikke kunne lese, se `ULESELIG_SQL`, sier fra HER og ikke
    ved enhver migrasjon som skriver en slik verdi et annet sted. Det er
    kolonnen katalogen faktisk måles mot som må være lest riktig.
    """
    gjeldende, _ = _registerets_enums()
    ut = gjeldende.get((MODULKONTRAKT, kolonne), set())
    assert ut, (f"fant ikke CHECK-vilkåret for {MODULKONTRAKT}.{kolonne} i "
                f"migrasjonene")
    assert ULESELIG_SQL not in ut, (
        f"CHECK-vilkåret for {MODULKONTRAKT}.{kolonne} har noe porten ikke kan "
        f"lese: enten en verdi skrevet som escapestreng (`E'…'`) eller "
        f"dollarsitert, eller et sammensatt predikat der `IN`-lista bare er et "
        f"oversett. Skriv vilkåret som `{kolonne} IN ('…', '…')` med enkle "
        f"apostrofer, eller lær porten formen; et gjettet innhold ville vært "
        f"verre enn ingen")
    return ut


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
    # Men et par som lukkes MIDTVEIS pakker ikke inn hele uttrykket. Å skrelle
    # det bort ville lest en disjunksjon som sitt første ledd, altså som et
    # strengere vilkår enn det er. Uttrykket er sammensatt, og da er svaret
    # `ULESELIG_SQL`: en disjunksjon UTVIDER — `X OR Y` godtar unionen — så
    # både det første leddet og ingenting ville vært feil svar.
    ("CHECK ((reversibilitet IN ('direkte'))\n"
     "    OR (reversibilitet IN ('kompenserende')))",
     {"direkte", ULESELIG_SQL}),
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

    Grensen går ved HELE uttrykket: en disjunksjon begynner også med en
    parentes, men den lukkes midtveis, og der er ingenting overflødig. Det som
    blir stående etter skrellingen, må så FYLLE uttrykket for å være vilkåret;
    ellers er lista bare en del av det, og porten sier fra.
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


def test_uvissheten_snittes_ikke_bort_av_et_annet_vilkaar(tmp_path):
    """Et lesbart vilkår ved siden av kan ikke opplyse det uleselige.

    Snittet fjerner verdier vilkårene er uenige om, og `ULESELIG_SQL` ville
    forsvunnet i den operasjonen — da sto et vilkår porten bare kjenner et
    oversett av, igjen som lest, og svaret ble videre enn databasen.
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


def test_en_uleselig_verdi_stopper_enumoppslaget(tmp_path):
    """En verdi porten ikke kan lese skal si fra, ikke gjettes på.

    `E'…'` og dollarsitering krever PostgreSQLs escaperegler for å tolkes, og
    den lista er åpen i feil ende. Verdien merkes derfor som uleselig og følger
    med til `_registerenum()`, som er stedet der det faktisk betyr noe.
    """
    mappe = _migrasjoner(
        tmp_path,
        "CREATE TABLE modulkontrakt (\n"
        "    reversibilitet TEXT NOT NULL\n"
        "        CHECK (reversibilitet IN ('direkte', E'irre\\\\versibel')));\n")
    gjeldende, noen_gang = _registerets_enums(mappe)
    assert ULESELIG_SQL in gjeldende[(MODULKONTRAKT, "reversibilitet")]
    # Historikken bærer den ikke videre — `pensjonert` skal ikke bygges på et
    # innhold porten ikke leste.
    assert ULESELIG_SQL not in noen_gang


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

    Og verdiene leses ut av det migrasjonen SKRIVER, ikke ut av råteksten
    (Codex P2 på #118, femtende runde). En KOMMENTAR skriver ingenting: en
    merknad som nevner `'oppfunnet_klasse'` i forbifarten la ordet i lista, og
    prosaen i sannhetskilden kunne så presentere en klasse ingen tabell har
    hørt om — porten under sa ingenting, fordi navnet «finnes». Merk at
    lesningen her er en annen enn i `_registerets_enums()`: en verdi i en
    funksjonskropp ER skrevet av registeret, selv om `AS $$…$$` bare definerer
    kroppen uten å kjøre den. Se `_uten_sqlkommentar()`.

    Og en VERDI er en hel literal (Codex P2 på #118, syttende runde). Det som
    ble igjen av råtekstlesningen var mønsteret `'([^']*)'`, og det kan ikke
    se at SQL escaper ved å doble fnutten: en melding som `'mangler
    ''oppfunnet_klasse'''` ga navnet inni som om det sto for seg selv — samme
    utfall som kommentaren, en klasse ingen tabell har hørt om. Verdiene leses
    nå med `_skrevne_verdier()`.

    Det samme gjelder en DOLLARSITERT tekst (Codex P2 på #118, attende runde):
    `RAISE EXCEPTION $$mangler 'oppfunnet_klasse'$$` er én melding, ikke et
    navn. Motsatt vei var hullet at en verdi skrevet dollarsitert ikke ble
    lest i det hele tatt. Se `_er_kropp()`.
    """
    gjeldende, noen_gang = _registerets_enums()
    pensjonert = noen_gang - set().union(*gjeldende.values(), set())
    ut: set[str] = set()
    for sql in sorted(MIGRASJONER.glob("*.sql")):
        tekst = _uten_sqlkommentar(sql.read_text(encoding="utf-8"))
        ut.update(t for t in _skrevne_verdier(tekst) if IDENT_RE.fullmatch(t))
    ut -= pensjonert
    spor = subprocess.run(["git", "ls-files", "-z"], cwd=ROT,
                          capture_output=True, text=True, check=True)
    ut.update(Path(rel).stem for rel in spor.stdout.split("\0") if rel)
    return ut


@pytest.mark.parametrize("sql,star_igjen", [
    # En merknad skriver ingenting, og navnet i den finnes ikke av den grunn.
    ("-- en gang het den 'oppfunnet_klasse'\n", False),
    ("/* en gang het den 'oppfunnet_klasse' */\n", False),
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
    # Kommentartegn inne i en STRENG er data, ikke starten på en kommentar.
    ("INSERT INTO t (a, b) VALUES ('a--b', 'oppfunnet_klasse');\n", True),
    # En doblet fnutt er en ESCAPE, ikke slutten på én verdi og starten på en
    # ny (Codex P2 på #118, syttende runde): meldingen her er ÉN literal, og
    # navnet i den står ikke for seg selv noe sted.
    ("    RAISE EXCEPTION 'mangler ''oppfunnet_klasse''';\n", False),
    ("SELECT 'det''s ''oppfunnet_klasse'' som mangler';\n", False),
    # Men en verdi som ER navnet, med en escapet nabo, står fortsatt.
    ("INSERT INTO t (a, b) VALUES ('det''s', 'oppfunnet_klasse');\n", True),
    # En escapestreng gis fra seg: innholdet krever escapereglene for å bli en
    # verdi, og et gjettet innhold er verre enn ingen når svaret godtar navn.
    ("SELECT E'oppfunnet_klasse';\n", False),
    # Et dollarsitert spenn som ikke er en kropp, er ÉN tekst (Codex P2 på
    # #118, attende runde). Meldingen her nevner navnet, den skriver det ikke.
    ("    RAISE EXCEPTION $$mangler 'oppfunnet_klasse'$$;\n", False),
    ("    RAISE EXCEPTION $melding$mangler 'oppfunnet_klasse'$melding$;\n",
     False),
    # Og kommentartegn inne i en slik tekst er data: blankes de, blir en annen
    # verdi enn den registeret skriver stående igjen.
    ("INSERT INTO t (a) VALUES ($$oppfunnet_klasse -- ikke en kommentar$$);\n",
     False),
    # Men motsatt vei: en VERDI skrevet dollarsitert er skrevet, selv om det
    # ikke står en apostrof i den.
    ("INSERT INTO t (a) VALUES ($$oppfunnet_klasse$$);\n", True),
    ("INSERT INTO t (a) VALUES ($tagg$oppfunnet_klasse$tagg$);\n", True),
    # En `DO`-kropp er en kropp her på samme måte som i `_kjort_sql()`.
    ("DO $$ BEGIN\n"
     "    PERFORM 'oppfunnet_klasse';\n"
     "END $$;\n", True),
    # To fragmenter med linjeskift mellom seg er ÉN verdi (Codex P2 på #118,
    # attende runde), og formen brukes gjennomgående til lange meldinger.
    ("SELECT 'mangler '\n"
     "       'oppfunnet_klasse';\n", False),
    ("    RAISE EXCEPTION 'mangler '\n"
     "        -- brutt for lesbarhetens skyld\n"
     "        'oppfunnet_klasse';\n", False),
    # Er ETT fragment uleselig, er hele den skjøtte verdien det.
    ("SELECT 'oppfunnet_klasse'\n"
     "       E'\\\\n';\n", False),
    # Uten linjeskift skjøter ikke PostgreSQL — det er en syntaksfeil der — så
    # porten skal heller ikke gjøre det.
    ("SELECT 'oppfunnet_klasse' 'og mer';\n", True),
    # Og en verdi som står alene på linja, står fortsatt.
    ("SELECT 'mangler',\n"
     "       'oppfunnet_klasse';\n", True),
])
def test_en_sqlkommentar_skriver_ingen_identifikator(sql, star_igjen):
    """Lista over kjente identifikatorer leses av det migrasjonen SKRIVER.

    Verdiene ble hentet ut av råteksten (Codex P2 på #118, femtende runde), og
    et maskinformet navn nevnt i en merknad havnet dermed i lista uten å finnes
    noe sted. Prosaen i sannhetskilden kunne så presentere en klasse ingen
    tabell har hørt om, og porten sa ingenting fordi navnet «finnes».

    Lesningen er en annen enn den `_registerets_enums()` bruker, og det er
    med vilje: der spør vi hva registeret HÅNDHEVER nå, her hva det SKRIVER.
    En funksjonskropp er skrevet selv om `AS $$…$$` ikke kjører den.

    Og en verdi er en HEL literal (Codex P2 på #118, syttende runde). Prøven
    her målte lesningen med et regex skrevet av ved siden av den funksjonen
    bruker, så den kunne ikke se at de to sa forskjellige ting: `'mangler
    ''oppfunnet_klasse'''` er én melding, ikke et navn. Nå måler den den samme
    lesningen som lista faktisk gjøres med.
    """
    verdier = _skrevne_verdier(_uten_sqlkommentar(sql))
    assert ("oppfunnet_klasse" in verdier) is star_igjen


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
