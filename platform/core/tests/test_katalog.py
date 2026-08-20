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


@pytest.mark.parametrize("felt,i_meldingen", [
    ('"status":"planlagt"', "status"),
    ("['status']:'planlagt'", "BEREGNET"),
    ('["sta" + "tus"]:"planlagt"', "BEREGNET"),
    ("[nokkel]:'planlagt'", "BEREGNET"),
    (r"['\x73tatus']:'planlagt'", "escape"),
    (r'["\u0073tatus"]:"planlagt"', "escape"),
    (r'"\x73tatus":"planlagt"', "escape"),
    # Trettende runde. Escapen trenger ikke fnutter rundt seg, og egenskapen
    # trenger ikke være skrevet som `navn: verdi` i det hele tatt.
    (r'\u0073tatus:"planlagt"', "egenskap"),
    ('...{status:"planlagt"}', "spredning"),
    ("status", "forkortelse"),
    ('status(){return "planlagt"}', "metode"),
    ('get status(){return "planlagt"}', "accessor"),
    # Et MØNSTER med `]` i seg lukket den beregnede nøkkelen for tidlig, så
    # `status` ble aldri lest som nøkkel.
    ('[/]/.test("") ? "x" : "status"]:"planlagt"', "BEREGNET"),
    # Og motsatt vei: et mønster i VERDI-posisjon er ingen literal katalogen
    # bærer. Leser ikke generatoren verdien, vet den heller ikke hvor neste felt
    # begynner — fnutten inne i mønsteret svelget kommaet og skjulte feltet bak.
    ('x:/["\']/, "status":"planlagt"', "egenskap"),
])
def test_statusforbudet_ser_alle_skrivemaater(tmp_path, felt, i_meldingen):
    """En tilstandsakse er forbudt uansett HVORDAN egenskapen er skrevet.

    Forbudet leste feltnavn som navn eller fnuttstreng og gikk videre på alt
    annet. Det er åpent i feil ende, og Codex fant formene én for én over tre
    runder på #118: `['status']:` (ellevte), `['\\x73tatus']:` (tolvte), og i
    trettende runde `\\u0073tatus:`, spredning, forkortelse, metode, accessor
    og en beregnet nøkkel med en `]` inne i et mønster. Alle gir nettleseren
    den helt alminnelige egenskapen `status`: den frittstående siden ville
    tegnet den, mens generatoren kastet den stille, og da lyver kilden.

    Generatoren TOLKER ikke escape-sekvenser og GJETTER ikke hva en beregnet
    nøkkel kommer til å hete — den avviser dem, se `les_nokkel()`. Prøvene her
    krever derfor bare at den STOPPER og sier hva den ikke kunne lese.

    VERDIEN er med i det samme kravet, se `les_verdi()`: leses den ikke, vet
    generatoren heller ikke hvor neste felt begynner, og et `status`-felt bak
    en uleselig verdi forsvinner like stille som en uleselig nøkkel.

    Mutasjonen står i en KOPI av kilden — en port som retter fila den måler,
    kan ikke feile.
    """
    r = _med_felt_i_m57(tmp_path, felt)
    assert r.returncode != 0, (
        f"generatoren godtok «{felt}» i modulposten")
    melding = r.stderr + r.stdout
    assert "M-57" in melding and i_meldingen in melding, (
        f"feilmeldingen sier ikke hva som er galt: {melding}")


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


_SKRIPT_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.S)
_NAVN_RE = re.compile(r"[A-Za-z_$][\w$]*")
_START_RE = re.compile(r"""\{\s*["']?n["']?\s*:\s*(\d+)\s*[,}]""")


# Ord som IKKE er en verdi i seg selv. Etter dem venter JS noe mer, så
# skråstreken som følger åpner et mønster — `return /\d+/` er ikke en divisjon.
#
# Lista er snudd (Codex P2 på #118, ellevte runde). Før sto den motsatt vei og
# ramset opp de ordene et mønster kunne følge etter — `return`, `typeof`, `of`
# … — og da er ethvert ord som IKKE ble husket en verdi: `throw /["']/.test(v)`
# ble lest som divisjon, fnutten i mønsteret åpnet en «streng», og kjørende kode
# ble stående igjen som prosa. Å legge til `throw` ville løst det tilfellet og
# latt neste glemte ord stå. Snudd er lista lukket: den ER de reserverte ordene
# i JS, og et ord som ikke er reservert er per definisjon en binding eller en
# egenskap — altså en verdi.
#
# `this`, `super`, `true`, `false` og `null` er reserverte, men mangler her med
# vilje: de ER verdier, og skråstreken etter dem deler.
_ORD_UTEN_VERDI = {
    "await", "break", "case", "catch", "class", "const", "continue",
    "debugger", "default", "delete", "do", "else", "enum", "export",
    "extends", "finally", "for", "function", "if", "implements", "import",
    "in", "instanceof", "interface", "let", "new", "package", "private",
    "protected", "public", "return", "static", "switch", "throw", "try",
    "typeof", "var", "void", "while", "with", "yield",
}

# Ord som tar en parentes med en BETINGELSE, ikke en verdi. Parentesen deres
# lukker en setningsdel, så etter den venter JS igjen en verdi. `for await (…)`
# er den ene formen der kontrollordet ikke står rett foran parentesen — den
# leses i `_kodespenn()`, som lar `for` bli stående som konteksten.
_KONTROLLORD = {"if", "for", "while", "with", "switch", "catch"}

# Ord som en SETNING kan følge rett etter. En krøllparentes der er en blokk,
# ikke et objektliteral — `else {` og `try {` åpner kropper.
#
# Lista er lukket av regelen i `_kodespenn()`: et objektliteral kan bare stå
# der et UTTRYKK kan begynne, og etter en VERDI kan ikke et uttrykk begynne.
# Alt som avslutter en verdi — et navn, en streng, et tall, `]`, `}`, `)` —
# setter derfor setningsposisjon av seg selv, og det som står igjen å ramse
# opp er de reserverte ordene som ikke er en verdi. Blant dem er dette de som
# tar en KROPP. `catch` sto ikke her (Codex P2 på #118, tolvte runde): den
# valgfrie bindingen kan sløyfes, og `try {} catch {}` leste da kroppen som et
# objektliteral — `}` avsluttet en «verdi», mønsteret etter ble en divisjon, og
# fnutten inne i mønsteret åpnet en «streng» som svelget kjørende kode.
# `static {}` er samme form inne i en klasse.
_SETNINGSORD = {"else", "do", "try", "finally", "catch", "static"}

_TALL_START_RE = re.compile(r"(?:\d[\w.]*|\.\d[\w.]*)")
# Tallet slik det står som VERDI i en modulpost. Fortegnet er med her og ikke i
# `_TALL_START_RE`: skanneren over leser `-` som operatoren den er.
_VERDITALL_RE = re.compile(r"(?:-?\d[\w.]*|-?\.\d[\w.]*)")
# Ord som er en VERDI og ikke et navn. Katalogen bruker dem ikke i dag, men de
# er literaler, og en literal kan porten hoppe trygt over.
_ORDVERDIER = ("true", "false", "null")


def _kommentarslutt(js: str, i: int, b: int) -> int:
    """Indeksen etter kommentaren som åpner i `i`."""
    if js.startswith("//", i):
        j = js.find("\n", i, b)
        return b if j < 0 else j + 1
    j = js.find("*/", i + 2, b)
    return b if j < 0 else j + 2


def _strengslutt(js: str, i: int, b: int) -> int:
    """Indeksen etter fnuttstrengen som åpner i `i`. `\\` escaper."""
    sitat, j = js[i], i + 1
    while j < b and js[j] != sitat:
        j += 2 if js[j] == "\\" else 1
    return min(j + 1, b)


def _regexslutt(js: str, i: int, b: int) -> int:
    """Indeksen etter mønsteret som åpner i `i`, eller `i` om det ikke lukkes.

    Et mønster er kode: `\\` escaper, og en tegnklasse `[…]` kan bære en `/`
    uten å avslutte. Et linjeskift kan den ikke — står det ett før lukkingen,
    var skråstreken en divisjon likevel.
    """
    j, i_klasse = i + 1, False
    while j < b and js[j] != "\n":
        if js[j] == "\\":
            j += 2
            continue
        if i_klasse:
            i_klasse = js[j] != "]"
        elif js[j] == "[":
            i_klasse = True
        elif js[j] == "/":
            j += 1
            while j < b and js[j].isalpha():
                j += 1
            return j
        j += 1
    return i


def _kodespenn(js: str, a: int, b: int, avslutt: bool = False):
    """Les js[a:b] som kode og gi fra deg hvert spenn som IKKE er kode.

    Gir `(start, slutt, slag)` med slag «streng», «mal» (tekstbiten i en
    malstreng), «kommentar» eller «regex». Alt mellom spennene er kjørende
    kode — også uttrykkene i `${…}`. Returverdien er indeksen der lesingen
    stoppet. Med `avslutt` stopper vi ved den `}` som lukker uttrykket vi står
    i — det er slik `${…}` i en malstreng leses.

    SKRÅSTREKEN er det vanskelige: `/` er både divisjon og starten på et
    mønster, og JS skiller dem på hva som står foran. Tidligere leste vi bakover
    fra hver skråstrek (Codex P2 på #118, niende og tiende runde). Bakover er
    feil vei: `)` så ut som slutten på en verdi uansett hva parentesen åpnet, så
    `if (klar) /["']/.test(x)` ble lest som divisjon, fnutten i mønsteret ble
    starten på en «streng», og kjørende kode ble stående igjen som prosa.

    Derfor leser vi nå FOROVER, med det JS selv holder rede på: `verdi` sier om
    forrige betydningsbærende tegn avsluttet en verdi (da deler skråstreken) og
    `parenteser` husker for hver åpne `(` om den bar en BETINGELSE — `if (…)`
    lukker en setningsdel og et mønster kan følge, `f(…)` gir en verdi og
    skråstreken etter er deling. Retningen er poenget: forover VET vi hva vi har
    passert, bakover må vi gjette.

    KRØLLPARENTESEN har samme tvetydighet som parentesen (Codex P2 på #118,
    ellevte runde): `}` lukker enten et OBJEKTLITERAL, som er en verdi, eller en
    BLOKK, som ikke er det. Vi leste den alltid som en blokk, og da så
    divisjonen i `` `${{x: 1} / 2} sti / hale` `` ut som starten på et mønster —
    skanningen spiste seg forbi malstrengens slutt og etterlot kjørende kode som
    prosa. `blokker` husker derfor for hver åpne `{` hva den åpnet, og
    `blokkposisjon` sier hvor vi står.

    Den regelen sto først som en LISTE over hva en kropp kunne følge etter, og
    en liste over former er åpen: `try {} catch {}` uten binding manglet, så
    kroppen ble lest som et objektliteral og mønsteret etter den som divisjon
    (Codex P2 på #118, tolvte runde). Nå står regelen som det den er: et
    objektliteral kan bare stå der et UTTRYKK kan begynne, og etter en VERDI kan
    ikke et uttrykk begynne — `x {`, `"s" {`, `1 {`, `] {` finnes ikke i JS.
    Derfor setter ALT som avslutter en verdi setningsposisjon, sammen med `;`,
    `)`, `=>` og `_SETNINGSORD`. Å ramse opp igjen står bare de reserverte
    ordene som ikke er en verdi — en lukket mengde, ikke en liste over former.
    `${…}` starter i uttrykksposisjon; det er derfor `avslutt` også setter
    startposisjonen.

    Sidegevinst: en klassekropp (`class A {`) leses nå som den blokka den er.
    Den sto før som et objektliteral, fordi navnet foran den er en verdi, og en
    skråstrek rett etter `}` ble derfor lest som divisjon.

    KOLONET er den tredje formen for samme tvetydighet (Codex P2 på #118,
    trettende runde). Det falt før ut i den siste linja, som setter
    uttrykksposisjon — riktig for et objektfelt (`{a: 1}`) og for et spørsmål
    (`a ? b : c`), men galt for en etikett og for `case`: der følger en SETNING,
    og `switch (x) { case 1: {} /["']/.test(v); }` leste derfor `{}` som et
    objektliteral. `}` avsluttet da en «verdi», mønsteret etter ble en divisjon,
    og fnutten inne i mønsteret åpnet en «streng» som svelget kjørende kode.
    Hvilket av de tre kolonene det er, avgjøres av RAMMEN det står i, og av om
    et `?` venter på svar inne i den.

    Derfor er stakkene slått sammen til én. De var tre — en for parenteser, en
    for krøllparenteser, ingen for klammer — og tre stakker over samme nøsting
    er tre steder å komme i utakt. `rammer` har én post per åpen `(`, `[` eller
    `{`: hva den åpnet, hva den betyr, og hvor mange `?` som venter på kolonet
    sitt inne i den. Nederst ligger rammen for teksten selv, som aldri lukkes.
    """
    verdi, siste_ord = False, ""
    rammer: list[list] = [["", False, 0]]
    blokkposisjon, i = not avslutt, a
    while i < b:
        c = js[i]
        if c.isspace():
            i += 1
            continue
        if js.startswith("//", i) or js.startswith("/*", i):
            j = _kommentarslutt(js, i, b)
            yield i, j, "kommentar"
            i = j
            continue
        if c in "\"'":
            j = _strengslutt(js, i, b)
            yield i, j, "streng"
            i, verdi, siste_ord, blokkposisjon = j, True, "", True
            continue
        if c == "`":
            i = yield from _malspenn(js, i, b)
            verdi, siste_ord, blokkposisjon = True, "", True
            continue
        if c == "/":
            j = i if verdi else _regexslutt(js, i, b)
            if j > i:
                yield i, j, "regex"
                i, verdi, siste_ord, blokkposisjon = j, True, "", True
                continue
            i, verdi, siste_ord, blokkposisjon = i + 1, False, "", False
            continue
        if (treff := _NAVN_RE.match(js, i, b)):
            # Etter et punktum er ordet en EGENSKAP, ikke et nøkkelord: `x.in`
            # og `x.default` er verdier selv om ordene er reserverte.
            etter_punktum = siste_ord == "."
            ordet = treff.group(0)
            # `for await (…)` er ÉN kontrollform: `await` hører til løkka og
            # ikke til et uttrykk, så `for` blir stående som den konteksten
            # parentesen skal leses i.
            if not (ordet == "await" and siste_ord == "for"):
                siste_ord = ordet
            i, verdi = treff.end(), (etter_punktum
                                     or ordet not in _ORD_UTEN_VERDI)
            blokkposisjon = verdi or ordet in _SETNINGSORD
            continue
        if (treff := _TALL_START_RE.match(js, i, b)):
            i, verdi, siste_ord, blokkposisjon = treff.end(), True, "", True
            continue
        if c == ".":
            i, verdi, siste_ord, blokkposisjon = i + 1, False, ".", False
            continue
        if c == "(":
            rammer.append(["(", siste_ord in _KONTROLLORD, 0])
            i, verdi, siste_ord, blokkposisjon = i + 1, False, "", False
            continue
        if c == ")":
            betingelse = _lukk(rammer)[1]
            # Står det en krøllparentes etter en `)`, er den en KROPP: `if (…)
            # {`, `function f() {`, `m() {`. Et objektliteral står aldri rett
            # etter en parentes — det står etter `=`, `(`, `,`, `:` eller `${`.
            i, verdi, siste_ord, blokkposisjon = i + 1, not betingelse, "", True
            continue
        if c == "[":
            rammer.append(["[", False, 0])
            i, verdi, siste_ord, blokkposisjon = i + 1, False, "", False
            continue
        if c == "]":
            _lukk(rammer)
            i, verdi, siste_ord, blokkposisjon = i + 1, True, "", True
            continue
        if c == "}":
            if len(rammer) == 1 and avslutt:
                return i
            # Lukker den et objektliteral, har den avsluttet en VERDI, og
            # skråstreken etter deler. Lukker den en blokk, kan et mønster følge.
            # Uansett hva den lukket, kan et objektliteral ikke begynne rett
            # etter en `}` — der står enten en ny setning eller en operator.
            objekt = _lukk(rammer)[1]
            i, siste_ord = i + 1, ""
            verdi, blokkposisjon = objekt, True
            continue
        if c == "{":
            objekt = not blokkposisjon
            rammer.append(["{", objekt, 0])
            i, verdi, siste_ord = i + 1, False, ""
            blokkposisjon = not objekt
            continue
        if js.startswith("=>", i):
            # Etter en pilfunksjon kommer enten en kropp (`=> {`) eller et
            # uttrykk (`=> /mønster/`). Begge deler, aldri et objektliteral —
            # det må skrives `=> ({…})`.
            i, verdi, siste_ord, blokkposisjon = i + 2, False, "", True
            continue
        if js.startswith("?.", i):
            # Valgfri kjeding: ordet etter er en EGENSKAP, som etter et punktum.
            i, verdi, siste_ord, blokkposisjon = i + 2, False, ".", False
            continue
        if js.startswith("??", i):
            # Nullslusing er en operator, ikke et spørsmål — den venter ikke på
            # noe kolon.
            i, verdi, siste_ord, blokkposisjon = i + 2, False, "", False
            continue
        if c == "?":
            # Et spørsmål venter på kolonet sitt, og det kolonet hører til
            # UTTRYKKET — ikke til en etikett. Telleren står i rammen, for
            # `f(a ? b : c)` og `{a: x ? y : z}` nøster hver for seg.
            rammer[-1][2] += 1
            i, verdi, siste_ord, blokkposisjon = i + 1, False, "", False
            continue
        if c == ":":
            ramme = rammer[-1]
            if ramme[2]:
                # Svaret på et `?`: et uttrykk følger.
                ramme[2] -= 1
                setning = False
            else:
                # Ellers avgjør rammen. Inne i et objektliteral er kolonet en
                # nøkkels, og en verdi følger. Inne i en BLOKK — eller i
                # teksten selv — finnes ingen nøkler: da er det en etikett
                # eller en `case`, og en SETNING følger.
                setning = ramme[0] in ("", "{") and not ramme[1]
            i, verdi, siste_ord, blokkposisjon = i + 1, False, "", setning
            continue
        if js.startswith("++", i) or js.startswith("--", i):
            # Postfiks `x++` avsluttet en verdi, og skråstreken etter deler;
            # prefiks `++x` gjør ikke det. `verdi` bæres derfor uendret
            # gjennom operatoren i stedet for å nullstilles av siste linje
            # (Codex P2 på #118, trettende runde). Et objektliteral kan uansett
            # ikke begynne rett etter den.
            i, siste_ord, blokkposisjon = i + 2, "", True
            continue
        if c == ";":
            i, verdi, siste_ord, blokkposisjon = i + 1, False, "", True
            continue
        i, verdi, siste_ord, blokkposisjon = i + 1, False, "", False
    return b


def _lukk(rammer: list[list]) -> list:
    """Lukk innerste ramme og gi den fra deg. Bunnrammen lukkes aldri.

    En lukking uten åpning er kode som ikke går i hop; da er bunnrammen svaret,
    og den betyr «ingen betingelse, ingen objektverdi» — det samme skanneren
    antok før stakkene ble slått sammen.
    """
    return rammer.pop() if len(rammer) > 1 else rammer[0]


def _malspenn(js: str, i: int, b: int):
    """Les malstrengen som åpner i `i`. Returner indeksen etter den.

    En malstreng er ikke ett spenn, men vekselvis TEKST og KODE: `${…}` bærer
    et uttrykk, ikke noe noen har skrevet til en leser. Vi gir derfor fra oss
    tekstbitene hver for seg og leser uttrykkene som kode, slik at
    `` `Tilstand: ${filter_state}` `` etterlater «Tilstand: » som prosa og
    `filter_state` som det navnet på en binding det er (Codex P2 på #118,
    tiende runde). Ble hele spennet gitt som tekst, meldte identifikatorporten
    uttrykket som en oppfunnet registerklasse.

    Uttrykkene leses av `_kodespenn()` og ikke ved en råskanning til neste
    backtick, fordi de kan bære NØSTEDE malstrenger — da ville skanningen
    lukket på feil sted.
    """
    j = tekst = i + 1
    while j < b:
        c = js[j]
        if c == "\\":
            j += 2
            continue
        if c == "`":
            yield tekst, j, "mal"
            return j + 1
        if js.startswith("${", j):
            yield tekst, j, "mal"
            j = yield from _kodespenn(js, j + 2, b, avslutt=True)
            tekst = j = min(j + 1, b)
            continue
        j += 1
    yield tekst, b, "mal"
    return b


def _spennkart(js: str, a: int = 0, b: int | None = None) -> dict[int, tuple]:
    """{startindeks: (sluttindeks, slag)} for ikke-kode-spennene i js[a:b].

    Skannerne under går tegn for tegn gjennom KODEN og slår opp her for å hoppe
    over det som ikke er kode.
    """
    b = len(js) if b is None else b
    return {s: (e, slag) for s, e, slag in _kodespenn(js, a, b) if e > s}


def _prosaindekser(js: str, a: int, b: int) -> set[int]:
    """Indeksene i js[a:b] som er prosa og ikke kjørende kode.

    Fnutter og kommentarer er tekst noen har SKREVET. Et MØNSTER er kode, ikke
    prosa: skanneren spenner over regex-literaler for at en fnutt inne i dem
    ikke skal se ut som en streng (Codex P2 på #118, niende runde), men det som
    står der er tegnklasser og kvantorer — ingen påstand om registeret.
    """
    ut: set[int] = set()
    for start, slutt, slag in _kodespenn(js, a, b):
        if slag != "regex":
            ut.update(range(start, slutt))
    return ut


def _prosa_av(js: str) -> str:
    """Skriptet med kjørende kode byttet mot mellomrom. Se `_prosetekst()`."""
    prosa = _prosaindekser(js, 0, len(js))
    return "".join(c if i in prosa else " " for i, c in enumerate(js))


# (skriptbit, om `filter_state` er PROSA etterpå). Navnet er valgt fordi det er
# et helt vanlig JS-navn som ser ut som en registerklasse: står det igjen som
# prosa, melder identifikatorporten det som oppfunnet og blokkerer UI-arbeid
# som ikke har noe med registeret å gjøre.
_SKANNERPROEVER = [
    ('if (klar) /["\']/.test(v); const filter_state = {};', False),
    ('for (const x of xs) /["\']/.test(x); const filter_state = {};', False),
    ('for await (const x of xs) /["\']/.test(x); const filter_state = {};',
     False),
    ('const m = /["\']/; const filter_state = {};', False),
    ('throw /["\']/.test(v); const filter_state = {};', False),
    ('const a = x.in / 2; const filter_state = {};', False),
    ('const a = (b + c) / d; const filter_state = {};', False),
    ('const a = f(b) / 2; const filter_state = {};', False),
    ('const a = arr[0] / 2; const filter_state = {};', False),
    ('const s = "en streng med filter_state i", filter_state = 1;', True),
    ('// en kommentar om filter_state\nconst filter_state = {};', True),
    ('const t = `Tilstand: ${filter_state}`;', False),
    ('const t = `ytre ${p ? `indre ${filter_state}` : ""} slutt`;', False),
    ('const t = `en mal som nevner filter_state i teksten`;', True),
    ('const t = `${{x: 1} / 2} sti / hale`; const filter_state = {};', False),
    ('const o = {a: 1} / 2; const filter_state = {};', False),
    ('function f() {} /["\']/.test(v); const filter_state = {};', False),
    ('if (a) {} else /["\']/.test(v); const filter_state = {};', False),
    ('try {} catch {} /["\']/.test(v); const filter_state = {};', False),
    ('try {} catch (e) {} /["\']/.test(v); const filter_state = {};', False),
    ('class A {} /["\']/.test(v); const filter_state = {};', False),
    ('const o = {a: "nevner filter_state"} / 2; const x = 1;', True),
    # Trettende runde: kolonet. Etter `case` og en etikett følger en SETNING,
    # så `{}` der er en blokk og mønsteret etter den er et mønster.
    ('switch (x) { case 1: {} /["\']/.test(v); } const filter_state = {};',
     False),
    ('switch (x) { default: {} /["\']/.test(v); } const filter_state = {};',
     False),
    ('ute: {} /["\']/.test(v); const filter_state = {};', False),
    # Men objektnøkkelens kolon og spørsmålets kolon følges av en VERDI, og
    # krøllparentesen etter dem er et objektliteral.
    ('const o = {a: {b: 1} / 2}; const filter_state = {};', False),
    ('const o = p ? {a: 1} / 2 : 0; const filter_state = {};', False),
    ('const o = p ? 0 : {a: 1} / /["\']/.test(v); const filter_state = {};',
     False),
    ('const o = f(p ? 1 : 2) / 2; const filter_state = {};', False),
    # Nullslusing og valgfri kjeding bærer et `?` som IKKE venter på et kolon.
    # Ble det talt som et spørsmål, spiste det kolonet til neste `case`, og
    # kroppen etter ble lest som et objektliteral.
    ('switch (x) { case a ?? b: {} /["\']/.test(v); } const filter_state = {};',
     False),
    ('switch (x) { case a?.b: {} /["\']/.test(v); } const filter_state = {};',
     False),
    # Trettende runde: postfiks `++`/`--` avslutter en verdi, så skråstreken
    # etter dem deler.
    ('const y = x++ / /["\']/.test(v); const filter_state = {};', False),
    ('const y = x-- / /["\']/.test(v); const filter_state = {};', False),
    # Prefiks gjør ikke det — der kan et mønster fortsatt følge.
    ('++x; /["\']/.test(v); const filter_state = {};', False),
    # En etikett i prosa er fortsatt prosa: kolonet inne i en streng er ikke
    # skannerens bord.
    ('const s = "case 1: filter_state"; const x = 1;', True),
]


@pytest.mark.parametrize("js,prosa", _SKANNERPROEVER)
def test_skanneren_skiller_kode_fra_prosa(js, prosa):
    """Skanneren må lese JS som JS — ellers stopper porten uskyldig UI-arbeid.

    Prøvene her står fordi sannhetskilden ikke inneholder dem: `if (klar) /…/`
    finnes ikke i prototypen i dag, så en skanner som leser den feil går grønt
    gjennom hele suiten fram til noen skriver linja. Tre runder med Codex-funn
    på #118 handlet om nettopp slike former (mønster etter `=`, mønster etter
    en betingelse, uttrykk i malstreng), og hver gang var det kilden som måtte
    endre seg for at feilen skulle vises. Nå viser prøvene den.
    """
    assert ("filter_state" in _prosa_av(js)) is prosa


def _modulposter() -> list[tuple[int, str]]:
    """[(modulnummer, posttekst)] for hver modulpost i sannhetskilden.

    Postene ble før funnet med et repo-bredt regex mot rå filtekst, og posten
    strakk seg til NESTE treff (Codex P2 på #118, sjuende runde). Da er enhver
    postformet tekst en modulgrense: et fritekstfelt som dokumenterer et
    API-svar — `input: "API-eksempel: {n:1}"` — ble lest som starten på en ny
    modul, den ekte modulen ble kuttet før `dep`, og faseporten og enumporten
    mistet den stille. Generatoren godtar samme prosa siden `postslutt()` ble
    strengbevisst, så porten ville falt på en kilde generatoren var fornøyd
    med.

    Skanneren her hopper derfor over strenger og kommentarer, og avgrenser
    posten ved dybdetelling i stedet for ved neste treff. Den er skrevet på nytt
    og ikke importert fra generatoren med vilje: to uavhengige lesninger av
    samme kilde er det som gjør at en feil i den ene blir SETT. Bare
    `<script>`-innholdet leses — fnutter i HTML-prosa er ikke strenger.
    """
    js = "\n".join(_SKRIPT_RE.findall(KILDE.read_text(encoding="utf-8")))
    spenn = _spennkart(js)
    ut: list[tuple[int, str]] = []
    i, n = 0, len(js)
    while i < n:
        if i in spenn:
            i = spenn[i][0]
            continue
        if js[i] != "{":
            i += 1
            continue
        treff = _START_RE.match(js, i)
        if not treff:
            i += 1
            continue
        start, dybde, j = i, 0, i
        while j < n:
            if j in spenn:
                j = spenn[j][0]
                continue
            if js[j] == "{":
                dybde += 1
            elif js[j] == "}":
                dybde -= 1
                if dybde == 0:
                    j += 1
                    break
            j += 1
        else:
            raise AssertionError(
                f"modulpost M-{treff.group(1)} i {KILDE.name} lukkes aldri — "
                f"kilden har endret form, sjekk parseren")
        ut.append((int(treff.group(1)), js[start:j]))
        i = j
    return ut


def _tomrom(js: str, i: int, spenn: dict[int, tuple]) -> int:
    """Indeksen til første tegn fra `i` som verken er blankt eller kommentar.

    Mellom en nøkkel og kolonet den hører til, og mellom kolonet og verdien,
    kan begge deler stå. JS bryr seg ikke, og for spørsmålet «hvilket felt er
    dette, og hva står i det?» er de like mye ingenting.
    """
    while i < len(js):
        if js[i].isspace():
            i += 1
        elif i in spenn and spenn[i][1] == "kommentar":
            i = spenn[i][0]
        else:
            break
    return i


# Navnet en strengnøkkel får når råteksten mellom fnuttene IKKE er navnet:
# `['\x6bl']` og `"kl":` gir begge den alminnelige egenskapen `kl` i
# nettleseren (Codex P2 på #118, tolvte runde). Leste porten råteksten, fikk
# posten et felt som heter noe annet — og et felt som ikke finnes, sjekker
# enumporten ikke. Escapene TOLKES ikke: å skrive JS-strengregler i Python
# (`\xHH`, `\uHHHH`, `\u{…}`, oktalt utenfor «use strict») er en åpen liste der
# hver glemt form er et nytt smutthull. De avvises, og navnet blir dette —
# en omvendt skråstrek er ikke et lovlig feltnavn, så det kan ikke kollidere
# med et ekte felt.
ULESELIG = "\\"


def _nokkelnavn(innhold: str) -> str:
    """Feltnavnet en strengnøkkel med dette innholdet gir. Se `ULESELIG`."""
    return ULESELIG if "\\" in innhold else innhold


def _les_nokkel(post: str, i: int,
                spenn: dict[int, tuple]) -> tuple[str, int]:
    """(feltnavn, indeksen etter nøkkelen). `(ULESELIG, -1)` når den ikke er et
    navn porten kan lese.

    Lista er LUKKET, som i generatoren: nøkkelen er enten et navn skrevet med
    bokstaver eller en fnuttstreng uten escape. Alt annet JS godtar i
    nøkkelposisjon — `['kl']:`, `['\\x6bl']:`, `\\u006bl:`, en malstrengnøkkel,
    en beregnet nøkkel med en `]` inne i et mønster — gir nettleseren en helt
    alminnelig egenskap, men gir porten et felt som heter noe annet eller ikke
    finnes. Og et felt porten ikke ser, kontrollerer den ikke: enumporten
    sjekker de feltene som FINNES, så en `kl`-verdi registeret avviser ville
    gått grønt gjennom CI.

    Codex fant formene én for én på #118 (ellevte til trettende runde). Å legge
    til én til for hver runde er å holde en åpen liste over det som ikke går an;
    denne veien er lukket.
    """
    if i in spenn and spenn[i][1] == "streng":
        j = spenn[i][0]
        return _nokkelnavn(post[i + 1:j - 1]), j
    if i not in spenn and (treff := _NAVN_RE.match(post, i)):
        return treff.group(0), treff.end()
    return ULESELIG, -1


def _les_verdi(post: str, i: int,
               spenn: dict[int, tuple]) -> tuple[str, int]:
    """(verdien som tekst, indeksen etter den). `("", -1)` når den ikke er en
    literal.

    Literal er fnuttstreng, tall, `true`/`false`/`null`, og lister og objekter
    av slike — det katalogen faktisk bærer. En verdi porten ikke kan lese, kan
    den heller ikke hoppe trygt OVER, og da vet den ikke hvor neste felt
    begynner: `x: /["']/, kl: "oppfunnet"` ville skjult `kl` helt.

    Fnuttene faller bort av en streng; en liste og et objekt gir råteksten sin,
    for `dep` leses som prosa av `_moduler_fra_kilden()`.
    """
    n = len(post)
    if i >= n:
        return "", -1
    if i in spenn:
        if spenn[i][1] != "streng":
            return "", -1
        j = spenn[i][0]
        return post[i + 1:j - 1], j
    if (treff := _VERDITALL_RE.match(post, i)):
        return treff.group(0), treff.end()
    if (treff := _NAVN_RE.match(post, i)) and treff.group(0) in _ORDVERDIER:
        return treff.group(0), treff.end()
    if post[i] in "[{":
        j = _les_samling(post, i, spenn)
        return (post[i:j], j) if j > 0 else ("", -1)
    return "", -1


def _les_samling(post: str, i: int, spenn: dict[int, tuple]) -> int:
    """Indeksen etter lista eller objektet som åpner i `i`, eller -1.

    Feltene i et NØSTET objekt hører til det objektet, ikke til modulposten, så
    navnene brukes ikke. De må likevel leses: uten dem vet vi ikke hvor den
    nøstede verdien slutter.
    """
    lukk = "]" if post[i] == "[" else "}"
    n = len(post)
    j = _tomrom(post, i + 1, spenn)
    while j < n:
        if j not in spenn and post[j] == lukk:
            return j + 1
        if lukk == "}":
            _, j = _les_nokkel(post, j, spenn)
            if j < 0:
                return -1
            j = _tomrom(post, j, spenn)
            if j >= n or post[j] != ":":
                return -1
            j = _tomrom(post, j + 1, spenn)
        _, j = _les_verdi(post, j, spenn)
        if j < 0:
            return -1
        j = _tomrom(post, j, spenn)
        if j < n and j not in spenn and post[j] == ",":
            j = _tomrom(post, j + 1, spenn)
        elif j >= n or j in spenn or post[j] != lukk:
            return -1
    return -1


def _postfelt(post: str) -> dict[str, str]:
    """{feltnavn: verdi} for feltene på postens ØVERSTE nivå.

    Posten leses som en følge av `nøkkel: literal` skilt med komma. Felt i
    nøstede objekter og lister hører til dem, ikke til posten, og tekst inne i
    en feltverdi er tekst og ikke felt. Kilden bærer to skrivemåter side om
    side: v7-arven er JS-literaler (`n:38,…,p:1,…,dep:'…'`), v8-modulene er
    JSON (`"n": 53, …`). Begge leses — leste porten bare den ene, ville halve
    katalogen vært uvoktet uten at noe sa fra.

    Parseren gikk før VIDERE på alt den ikke kjente igjen, og hver Codex-runde
    på #118 fant én form til som slapp gjennom akkurat der: kommentar mellom
    nøkkel og kolon (niende runde), `['kl']:` (ellevte), `['\\x6bl']:`
    (tolvte), og i trettende `\\u006bl:`, spredning, forkortelse, metode og
    accessor. Alle gir nettleseren et helt vanlig felt.

    Å miste et felt er farligere enn å misforstå det: det som ikke finnes, blir
    ikke kontrollert. Derfor sier `_les_nokkel()` og `_les_verdi()` nå hva som
    ER lesbart, og en post med noe utenfor får `ULESELIG` — som er det
    kontraktporten faller på, med modulnummeret.
    """
    ut: dict[str, str] = {}
    spenn = _spennkart(post)
    n = len(post)
    i = _tomrom(post, 1, spenn)
    while i < n:
        if i not in spenn and post[i] == "}":
            return ut
        navn, i = _les_nokkel(post, i, spenn)
        i = _tomrom(post, i, spenn) if i >= 0 else i
        if i < 0 or i >= n or post[i] != ":":
            ut[ULESELIG] = ""
            return ut
        verdi, i = _les_verdi(post, _tomrom(post, i + 1, spenn), spenn)
        if i < 0:
            ut[ULESELIG] = ""
            return ut
        ut[navn] = verdi
        i = _tomrom(post, i, spenn)
        if i < n and i not in spenn and post[i] == ",":
            i = _tomrom(post, i + 1, spenn)
        elif i < n and (i in spenn or post[i] != "}"):
            ut[ULESELIG] = ""
            return ut
    ut[ULESELIG] = ""
    return ut


def _moduler_fra_kilden() -> dict[int, dict[str, str]]:
    """{modulnummer: {fase, dep, kl, rev}} lest ut av spesifikasjonen."""
    ut: dict[int, dict[str, str]] = {}
    for nummer, post in _modulposter():
        felt = _postfelt(post)
        if "p" not in felt or "dep" not in felt:
            continue
        ut[nummer] = {"fase": int(felt["p"]), "dep": felt["dep"]}
        for navn in ("kl", "rev"):
            if navn in felt:
                ut[nummer][navn] = felt[navn]
    return ut


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
# vilkåret. SQL-kommentarer fjernes først — 038 SITERER formen «CHECK (hendelse
# IN (...))» i en kommentar, og den ville ellers slettet en tabells enum. BEGGE
# kommentarformene, og ikke inne i strenger: se `_uten_sqlkommentar()`.
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
_TABELL_RE = re.compile(
    r"\b(?:CREATE|ALTER)\s+TABLE(?:\s+IF\s+(?:NOT\s+)?EXISTS)?\s+([\w.\"]+)",
    re.I)
_CHECK_RE = re.compile(
    r"""(?:CONSTRAINT\s+([\w."]+)\s+)?CHECK\s*\(\s*(\w+)\s+IN\s*\(([^)]*)\)""",
    re.S | re.I)
# Et vilkår kan også FJERNES (Codex P2 på #118, ellevte runde). Tilstanden ble
# regnet bare av CHECK-treff, så en `DROP CONSTRAINT` uten et nytt vilkår etter
# seg lot verdiene fra forrige migrasjon bli stående som gjeldende: katalogporten
# ville avvist verdier PostgreSQL ikke lenger begrenser, og `_registerenum()`
# ville ikke sagt fra om at vilkåret er borte. Slippet er en hendelse på linje
# med vilkåret, og leses i samme rekkefølge som det.
_DROPP_RE = re.compile(
    r"""DROP\s+CONSTRAINT(?:\s+IF\s+EXISTS)?\s+([\w."]+)""", re.I)


def _sqlstrengslutt(sql: str, i: int) -> int:
    """Indeksen etter strengen eller det siterte navnet som åpner i `i`.

    SQL escaper ikke med omvendt skråstrek, men ved å DOBLE sitattegnet:
    `'det''s'` er én streng. `E'…'` er unntaket der `\\` også escaper, men den
    formen bruker migrasjonene bare til tegn som `\\n` og `\\x1f` — aldri til et
    sitattegn — og en regel om at `\\'` ikke lukker ville brutt `E'\\\\_\\\\_%'`
    i 041, der skråstrekene er escapet og fnutten lukker som vanlig.
    """
    sitat, j, n = sql[i], i + 1, len(sql)
    while j < n:
        if sql[j] != sitat:
            j += 1
        elif j + 1 < n and sql[j + 1] == sitat:
            j += 2
        else:
            return j + 1
    return n


def _sqlverdier(liste: str) -> set[str]:
    """Strengverdiene i en `IN (…)`-liste.

    Leses med `_sqlstrengslutt()` og ikke med et regex på «alt mellom to
    fnutter»: SQL escaper ved å DOBLE sitattegnet, så `'det''s'` er ÉN verdi.
    Et regex delte den i to og fikk en verdi til ut av lista.
    """
    ut, i, n = set(), 0, len(liste)
    while i < n:
        if liste[i] == "'":
            j = _sqlstrengslutt(liste, i)
            ut.add(liste[i + 1:j - 1].replace("''", "'"))
            i = j
        else:
            i += 1
    return ut


def _uten_sqlkommentar(sql: str) -> str:
    """SQL-en med kommentarene byttet mot mellomrom.

    Bare `--` ble fjernet før, med et regex per linje (Codex P2 på #118, tolvte
    runde). To hull fulgte av det.

    BLOKKOMMENTAREN sto igjen som kode. En migrasjon som setter en setning ut av
    drift eller viser et eksempel — `/* … CHECK (reversibilitet IN ('oppfunnet'))
    … */` — ble da lest som gjeldende tilstand, og katalogporten ville sluppet
    inn en klasse PostgreSQL aldri godtar. Et blokkommentert `DROP CONSTRAINT`
    gikk motsatt vei og slettet et vilkår som fortsatt gjelder. Blokkommentarer
    NØSTES i PostgreSQL, så dybden telles.

    Og kommentartegnene ble lest også inne i STRENGER: en verdi som `'a--b'`
    kappet resten av linja, og med den et vilkår som sto der. Strenger og
    siterte navn hoppes derfor over først — de er data, ikke kommentarer.

    Lengden holdes, for hver kommentar byttes tegn for tegn mot mellomrom.
    Rekkefølgen mellom `CREATE TABLE`, `CHECK` og `DROP CONSTRAINT` leses av
    posisjonene i teksten, og linjeskift beholdes så `--` fortsatt slutter der
    linja slutter.
    """
    ut = list(sql)
    i, n = 0, len(sql)
    while i < n:
        if sql[i] in "'\"":
            i = _sqlstrengslutt(sql, i)
            continue
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            j = n if j < 0 else j
        elif sql.startswith("/*", i):
            j, dybde = i + 2, 1
            while j < n and dybde:
                if sql.startswith("/*", j):
                    dybde, j = dybde + 1, j + 2
                elif sql.startswith("*/", j):
                    dybde, j = dybde - 1, j + 2
                else:
                    j += 1
        else:
            i += 1
            continue
        for k in range(i, j):
            if ut[k] != "\n":
                ut[k] = " "
        i = j
    return "".join(ut)


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

    Migrasjonene leses i nummerrekkefølge; siste vilkår for samme kolonne i
    samme tabell er det som gjelder — en senere kan både UTVIDE (036 la
    `ekstern_lesing` til `sideeffektklasse`) og STRAMME INN.

    Og FJERNE (Codex P2 på #118, ellevte runde). Et `DROP CONSTRAINT` uten et
    nytt vilkår etter seg lot verdiene fra forrige migrasjon bli stående som
    gjeldende, fordi bare CHECK-treff ble regnet med. Da ville katalogporten
    fortsatt avvist verdier databasen ikke lenger begrenser. Vilkår og slipp
    leses derfor i STILLINGSREKKEFØLGE i hver fil — 036 slipper og legger på
    igjen i samme setningspar, og rekkefølgen er det eneste som skiller dem.

    Hvilket vilkår et slipp treffer, avgjøres av navnet: et navngitt vilkår
    huskes som det heter, og et vilkår skrevet rett på kolonnen får navnet
    PostgreSQL selv gir det — `<tabell>_<kolonne>_check`. Det er den formen 036
    slipper, og den formen 014 la inn uten å navngi.
    """
    gjeldende: dict[tuple[str, str], set[str]] = {}
    vilkarsnavn: dict[tuple[str, str], str] = {}
    noen_gang: set[str] = set()
    for sql in sorted((mappe or MIGRASJONER).glob("*.sql")):
        tekst = _uten_sqlkommentar(sql.read_text(encoding="utf-8"))
        tabeller = [(m.start(), _tabellnavn(m.group(1)))
                    for m in _TABELL_RE.finditer(tekst)]
        hendelser = sorted(
            [(m.start(), True, m) for m in _CHECK_RE.finditer(tekst)]
            + [(m.start(), False, m) for m in _DROPP_RE.finditer(tekst)],
            key=lambda h: h[0])
        for start, er_vilkar, m in hendelser:
            tabell = ""
            for pos, navn in tabeller:
                if pos > start:
                    break
                tabell = navn
            if not er_vilkar:
                sluppet = m.group(1).lower().replace('"', "")
                for nokkel, navn in list(vilkarsnavn.items()):
                    if nokkel[0] == tabell and navn == sluppet:
                        del gjeldende[nokkel]
                        del vilkarsnavn[nokkel]
                continue
            verdier = _sqlverdier(m.group(3))
            if not verdier:
                continue
            kolonne = m.group(2)
            gjeldende[(tabell, kolonne)] = verdier
            vilkarsnavn[(tabell, kolonne)] = (
                m.group(1).lower().replace('"', "") if m.group(1)
                else f"{tabell}_{kolonne}_check")
            noen_gang |= verdier
    return gjeldende, noen_gang


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
    """
    gjeldende, _ = _registerets_enums()
    ut = gjeldende.get((MODULKONTRAKT, kolonne), set())
    assert ut, (f"fant ikke CHECK-vilkåret for {MODULKONTRAKT}.{kolonne} i "
                f"migrasjonene")
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


@pytest.mark.parametrize("post,lesbar", [
    # Begge skrivemåtene kilden faktisk bærer, og en nøstet verdi.
    ("""{n:1,name:'A',p:1,dep:'B',kl:"krever_outbox"}""", True),
    ("""{"n":1,"name":"A","p":1,"dep":"B","kl":"krever_outbox"}""", True),
    ("""{n:1,name:'A',p:1,dep:['M-6'],meta:{a:1,b:[2]}}""", True),
    ("""{n:1,name:'A',p:1,kl /* begrunnelse */:"krever_outbox"}""", True),
    # Nøkkelformer nettleseren leser som `kl`, men porten leste som noe annet
    # — eller ikke i det hele tatt.
    ("""{n:1,name:'A',p:1,['kl']:"oppfunnet"}""", False),
    (r"""{n:1,name:'A',p:1,['\x6bl']:"oppfunnet"}""", False),
    (r"""{n:1,name:'A',p:1,\u006bl:"oppfunnet"}""", False),
    ("""{n:1,name:'A',p:1,[/]/.test("")?"x":"kl"]:"oppfunnet"}""", False),
    # Egenskaper som ikke er skrevet som `nøkkel: verdi`.
    ("""{n:1,name:'A',p:1,...{kl:"oppfunnet"}}""", False),
    ("""{n:1,name:'A',p:1,kl}""", False),
    ("""{n:1,name:'A',p:1,kl(){return "oppfunnet"}}""", False),
    ("""{n:1,name:'A',p:1,get kl(){return "oppfunnet"}}""", False),
    # Og verdier porten ikke kan lese, og derfor ikke kan hoppe over: et
    # mønster med en fnutt i ville svelget kommaet og skjult `kl` bak seg.
    ("""{n:1,name:'A',p:1,x:/["']/,kl:"oppfunnet"}""", False),
    ("""{n:1,name:'A',p:1,kl:`oppfunnet`}""", False),
])
def test_kontraktparseren_leser_bare_navn_og_literal(post, lesbar):
    """Porten må se HELE posten, ellers vokter den bare det den tilfeldigvis så.

    Enumporten under sjekker de feltene som FINNES. Et `kl` porten ikke leser,
    kontrollerer den ikke — og en klasse modulregisteret avviser går da grønt
    gjennom CI mens nettleseren viser feltet som et helt vanlig felt.

    Formene her sto ikke i kilden da de ble funnet, og gjør det ikke nå: en
    port som bare måler dagens fil, går grønn helt til noen skriver linja. Det
    var nettopp slik Codex fant dem én for én over tre runder på #118.
    """
    assert (ULESELIG not in _postfelt(post)) is lesbar, _postfelt(post)


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
    feltnavn lar seg lese (Codex P2 på #118, tolvte runde). En nøkkel skrevet
    `['\\x6bl']` er `kl` for nettleseren, men noe annet for en parser som leser
    råteksten — og det feltet porten ikke ser, kontrollerer den ikke. Å mangle
    et felt er farligere enn å misforstå det.

    MUTASJONEN SOM DREPER DENNE: hardkod enumene i testen. Da vokter porten en
    kopi, og en migrasjon som strammer inn et vilkår går rett forbi den.
    """
    uleselige = [f"M-{n}" for n, post in _modulposter()
                 if ULESELIG in _postfelt(post)]
    assert not uleselige, (
        "modulposter med et feltnavn porten ikke kan lese (escape i nøkkelen): "
        + ", ".join(uleselige) + " — skriv feltnavnet med bokstaver")
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
    """
    gjeldende, noen_gang = _registerets_enums()
    pensjonert = noen_gang - set().union(*gjeldende.values(), set())
    ut: set[str] = set()
    for sql in sorted(MIGRASJONER.glob("*.sql")):
        ut.update(t for t in re.findall(r"'([^']*)'",
                                        sql.read_text(encoding="utf-8"))
                  if IDENT_RE.fullmatch(t))
    ut -= pensjonert
    spor = subprocess.run(["git", "ls-files", "-z"], cwd=ROT,
                          capture_output=True, text=True, check=True)
    ut.update(Path(rel).stem for rel in spor.stdout.split("\0") if rel)
    return ut


_SKRIPTDEL_RE = re.compile(r"<script[^>]*>(.*?)</script>", re.S)


def _prosetekst() -> str:
    """Sannhetskilden med kjørende JavaScript maskert bort.

    Alt utenfor `<script>` er dokumenttekst. Inne i skriptet skiller vi prosa
    fra kode slik JS selv gjør det: fnutter og kommentarer er tekst noen har
    SKREVET — modulpostenes `dep`, `guard`, `input` og `accept`, og merknadene
    om hvor tallene kommer fra — mens resten er navn på bindinger som lever og
    dør i denne fila. `filter_state` i en `const` sier ingenting om registeret;
    `krever_outbox` i et `kl`-felt gjør det.

    Hva som er prosa inne i skriptet avgjør `_prosaindekser()`, den samme
    regelen prøvene i `test_skanneren_skiller_kode_fra_prosa()` måler.

    Maskeringen bytter tegn mot mellomrom i stedet for å klippe dem ut, og
    lar linjeskift stå: linjenummeret porten melder skal peke på linja i fila,
    ikke i et utsnitt.
    """
    tekst = KILDE.read_text(encoding="utf-8")
    ut = list(tekst)
    for del_ in _SKRIPTDEL_RE.finditer(tekst):
        a, b = del_.start(1), del_.end(1)
        prosa = _prosaindekser(tekst, a, b)
        for k in range(a, b):
            if k not in prosa and ut[k] != "\n":
                ut[k] = " "
    return "".join(ut)


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
    binding som lever og dør i denne fila. Porten leser derfor `_prosetekst()`:
    dokumentteksten, og inne i skriptet fnutter og kommentarer — der kilden
    faktisk sier noe til den som skal bygge — mens koden selv er maskert bort.

    MUTASJONEN SOM DREPER DENNE: la porten lese modulpostene i stedet for hele
    prosaen. Da vokter den det port 9 allerede vokter, og prosaen — som er der
    regresjonen faktisk sto — er igjen uten port.
    """
    kjente = _kjente_identifikatorer()
    tekst = _prosetekst()
    avvik: dict[str, int] = {}
    for treff in IDENT_RE.finditer(tekst):
        if treff.group(0) not in kjente:
            avvik.setdefault(treff.group(0),
                             tekst.count("\n", 0, treff.start()) + 1)
    assert not avvik, (
        f"identifikatorer i {KILDE.name} som ikke finnes i registeret eller "
        "som fil: " + "; ".join(f"«{ident}» (linje {linje})"
                                for ident, linje in sorted(avvik.items())))


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
