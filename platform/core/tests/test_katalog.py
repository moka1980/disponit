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


# Ord som kan stå rett foran en regex-literal. Etter dem venter JS en VERDI,
# så skråstreken åpner et mønster — `return /\d+/` er ikke en divisjon.
_NOKKELORD_FOR_VERDI = {
    "return", "typeof", "instanceof", "in", "of", "new", "delete", "void",
    "do", "else", "case", "yield", "await",
}


def _er_regex(js: str, i: int) -> bool:
    """Om skråstreken i `i` åpner en regex-literal og ikke en divisjon.

    JS avgjør dette på det som står FORAN: kommer skråstreken der en verdi kan
    stå, er den et mønster; kommer den etter en verdi, er den deling. Vi leser
    derfor bakover forbi blanke tegn. Et navn foran er en verdi — med mindre
    det er et nøkkelord som selv venter en verdi.
    """
    if js[i] != "/" or js.startswith("//", i) or js.startswith("/*", i):
        return False
    j = i - 1
    while j >= 0 and js[j].isspace():
        j -= 1
    if j < 0:
        return True
    if js[j] in ")]":
        return False
    if js[j].isalnum() or js[j] in "_$":
        k = j
        while k >= 0 and (js[k].isalnum() or js[k] in "_$"):
            k -= 1
        return js[k + 1:j + 1] in _NOKKELORD_FOR_VERDI
    return True


def _hopp(js: str, i: int) -> int:
    """Indeksen etter tegnet i `i` når det åpner en streng, en kommentar eller
    en regex-literal.

    Strenger: enkelt- og dobbeltfnutt og backtick, `\\` som escape. Kommentarer:
    `//` til linjeskift, `/* */` til lukkingen. En uterminert streng eller
    blokkommentar går til filslutt — da er kilden uansett ikke lesbar, og
    postskanneren sier fra med modulnummeret som mangler.

    REGEX-LITERALER kom til i niende runde (Codex P2 på #118). En skråstrek var
    ikke noe skanneren kjente, så `/["']/` i UI-koden ble lest tegn for tegn —
    og fnutten INNE i mønsteret ble starten på en streng. Alt fram til neste
    fnutt, kjørende kode inkludert, lå da inne i det skanneren trodde var tekst.
    Et mønster er kode: `\\` escaper, og en tegnklasse `[...]` kan bære en `/`
    uten å avslutte. Et linjeskift kan den ikke: står det ett, var skråstreken
    en divisjon likevel, og vi går ett tegn videre som før.
    """
    n = len(js)
    if js.startswith("//", i):
        j = js.find("\n", i)
        return n if j < 0 else j + 1
    if js.startswith("/*", i):
        j = js.find("*/", i + 2)
        return n if j < 0 else j + 2
    if js[i] == "/":
        j, i_klasse = i + 1, False
        while j < n and js[j] != "\n":
            if js[j] == "\\":
                j += 2
                continue
            if i_klasse:
                i_klasse = js[j] != "]"
            elif js[j] == "[":
                i_klasse = True
            elif js[j] == "/":
                j += 1
                while j < n and js[j].isalpha():
                    j += 1
                return j
            j += 1
        return i + 1
    sitat = js[i]
    j = i + 1
    while j < n and js[j] != sitat:
        j += 2 if js[j] == "\\" else 1
    return j + 1


def _apner(js: str, i: int) -> bool:
    """Om tegnet i `i` åpner noe skanneren ikke skal lese som kode."""
    return (js[i] in "\"'`" or js.startswith("//", i)
            or js.startswith("/*", i) or _er_regex(js, i))


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
    ut: list[tuple[int, str]] = []
    i, n = 0, len(js)
    while i < n:
        if _apner(js, i):
            i = _hopp(js, i)
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
            if _apner(js, j):
                j = _hopp(js, j)
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


def _tomrom(js: str, i: int) -> int:
    """Indeksen til første tegn fra `i` som verken er blankt eller kommentar.

    Mellom en nøkkel og kolonet den hører til, og mellom kolonet og verdien,
    kan begge deler stå. JS bryr seg ikke, og for spørsmålet «hvilket felt er
    dette, og hva står i det?» er de like mye ingenting.
    """
    while i < len(js):
        if js[i].isspace():
            i += 1
        elif js.startswith("//", i) or js.startswith("/*", i):
            i = _hopp(js, i)
        else:
            break
    return i


def _postfelt(post: str) -> dict[str, str]:
    """{feltnavn: verdi} for feltene på postens ØVERSTE nivå.

    Verdien er råteksten fram til neste komma på samme nivå; er den en streng,
    faller fnuttene bort. Felt i nøstede objekter og lister hører til dem, ikke
    til posten, og telles ikke — og tekst inne i en feltverdi er tekst, ikke
    felt. Kilden bærer to skrivemåter side om side: v7-arven er JS-literaler
    (`n:38,…,p:1,…,dep:'…'`), v8-modulene er JSON (`"n": 53, …`). Begge leses —
    leste porten bare den ene, ville halve katalogen vært uvoktet uten at noe
    sa fra.

    KOMMENTARER mellom nøkkel og kolon var før dette usynlige for parseren
    (Codex P2 på #118, niende runde), som bare spiste blanke tegn. Da forlot
    den nøkkelen: `"kl" /* begrunnelse */: "oppfunnet"` ga en post UTEN `kl`,
    og enum-porten sjekker bare de feltene som faktisk finnes. En klasse
    modulregisteret avviser hadde altså gått grønt gjennom CI — mens nettleseren
    leste feltet som et helt vanlig felt. Å miste et felt er farligere enn å
    misforstå det: det som ikke finnes, blir ikke kontrollert.
    """
    ut: dict[str, str] = {}
    i, n = 1, len(post)
    while i < n:
        c = post[i]
        if c.isspace() or c == ",":
            i += 1
            continue
        if post.startswith("//", i) or post.startswith("/*", i):
            i = _hopp(post, i)
            continue
        if c == "}":
            break
        if c in "\"'`":
            j = _hopp(post, i)
            navn, i = post[i + 1:j - 1], j
        elif (treff := _NAVN_RE.match(post, i)):
            navn, i = treff.group(0), treff.end()
        else:
            i += 1
            continue
        i = _tomrom(post, i)
        if i >= n or post[i] != ":":
            continue
        i = _tomrom(post, i + 1)
        start, dybde = i, 0
        while i < n:
            if _apner(post, i):
                i = _hopp(post, i)
                continue
            if post[i] in "{[":
                dybde += 1
            elif post[i] in "}]":
                if dybde == 0:
                    break
                dybde -= 1
            elif post[i] == "," and dybde == 0:
                break
            i += 1
        verdi = post[start:i].strip()
        if len(verdi) >= 2 and verdi[0] in "\"'`" and verdi[-1] == verdi[0]:
            verdi = verdi[1:-1]
        ut[navn] = verdi
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
# IN (...))» i en kommentar, og den ville ellers slettet en tabells enum.
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
_CHECK_RE = re.compile(r"CHECK\s*\(\s*(\w+)\s+IN\s*\(([^)]*)\)", re.S)
_SQL_KOMMENTAR_RE = re.compile(r"--[^\n]*")


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


def _registerets_enums() -> tuple[dict[tuple[str, str], set[str]], set[str]]:
    """(gjeldende verdier per (tabell, kolonne), verdier bundet noen gang).

    Migrasjonene leses i nummerrekkefølge; siste vilkår for samme kolonne i
    samme tabell er det som gjelder — en senere kan både UTVIDE (036 la
    `ekstern_lesing` til `sideeffektklasse`) og STRAMME INN.
    """
    gjeldende: dict[tuple[str, str], set[str]] = {}
    noen_gang: set[str] = set()
    for sql in sorted(MIGRASJONER.glob("*.sql")):
        tekst = _SQL_KOMMENTAR_RE.sub("", sql.read_text(encoding="utf-8"))
        tabeller = [(m.start(), _tabellnavn(m.group(1)))
                    for m in _TABELL_RE.finditer(tekst)]
        for m in _CHECK_RE.finditer(tekst):
            verdier = set(re.findall(r"'([^']*)'", m.group(2)))
            if not verdier:
                continue
            tabell = ""
            for pos, navn in tabeller:
                if pos > m.start():
                    break
                tabell = navn
            gjeldende[(tabell, m.group(1))] = verdier
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

    MUTASJONEN SOM DREPER DENNE: hardkod enumene i testen. Da vokter porten en
    kopi, og en migrasjon som strammer inn et vilkår går rett forbi den.
    """
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

    Et MØNSTER er kode, ikke prosa: `_apner()` spenner over regex-literaler
    for at en fnutt inne i dem ikke skal se ut som en streng (niende runde),
    men det som står der er tegnklasser og kvantorer — ingen påstand om
    registeret. De hoppes derfor over uten å beholdes.

    Maskeringen bytter tegn mot mellomrom i stedet for å klippe dem ut, og
    lar linjeskift stå: linjenummeret porten melder skal peke på linja i fila,
    ikke i et utsnitt.
    """
    tekst = KILDE.read_text(encoding="utf-8")
    ut = list(tekst)
    for del_ in _SKRIPTDEL_RE.finditer(tekst):
        a, b = del_.start(1), del_.end(1)
        behold: set[int] = set()
        i = a
        while i < b:
            if _apner(tekst, i):
                j = min(_hopp(tekst, i), b)
                if not _er_regex(tekst, i):
                    behold.update(range(i, j))
                i = j
                continue
            i += 1
        for k in range(a, b):
            if k not in behold and ut[k] != "\n":
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
