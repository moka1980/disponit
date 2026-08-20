"""Modulkatalogen på forsiden: fersk, komplett og formriktig.

Katalogen er generert fra `docs/spesifikasjon/disponit-prototype-v9.html` av
`tools/gen_katalog.py`. En generator uten en port i CI er bare en vennlig
anbefaling: den dagen noen redigerer `katalog.js` for hånd, eller endrer
spesifikasjonen uten å kjøre generatoren, driver de to kildene fra hverandre —
og forsiden viser da et produktomfang ingen har bestemt.

Testene her er derfor ni porter (Codex P2 på PR #43, #99 og #118):
  1. KILDE     — generatoren leser sannhetskilden, ikke arkivet i `prototype/`.
  2. FERSKHET  — regenerering i en temp-rot gir NØYAKTIG det som ligger i repoet.
  3. OMDØPING  — nytt navn i kilden stopper genereringen til oversettelsen er
                 vurdert på nytt, så nb og en ikke kan drive fra hverandre.
  4. FORM      — 57 moduler, elleve områder, faser 1–4, alle representert.
  5. TEKST     — hvert modul- og områdenavn har nøkkel i BEGGE locale-sett.
  6. MERKEVARE — sannhetskilden bærer produktnavnet resten av repoet bruker.
  7. FASEORDEN — ingen modul avhenger av en modul i en senere fase, så den
                 erklærte utrullingsrekkefølgen faktisk kan følges.
  8. PEKERE    — ingen fil i repoet henviser til en spesifikasjonsutgave som
                 er slettet; historikken i docs/pr/ og docs/beslutninger/ er
                 med rette unntatt.
  9. KLASSER   — `kl` og `rev` i katalogen er verdier modulregisteret godtar,
                 lest ut av CHECK-vilkårene i migrasjonene.
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


def _moduler_fra_kilden() -> dict[int, dict[str, str]]:
    """{modulnummer: {fase, dep}} lest ut av spesifikasjonen.

    Kilden bærer to skrivemåter side om side: v7-arven er JS-literaler
    (`n:38,…,p:1,…,dep:'…'`), v8-modulene er JSON (`"n": 53, …`). Porten leser
    BEGGE — leste den bare den ene, ville halve katalogen vært uvoktet uten at
    noe sa fra.
    """
    tekst = KILDE.read_text(encoding="utf-8")
    starter = [(m.start(), int(m.group(1)))
               for m in re.finditer(r'[{,]\s*(?:n:|"n":)\s*(\d+)', tekst)]
    ut: dict[int, dict[str, str]] = {}
    for i, (pos, n) in enumerate(starter):
        slutt = starter[i + 1][0] if i + 1 < len(starter) else len(tekst)
        seg = tekst[pos:slutt]
        fase = re.search(r'(?:\bp:|"p":)\s*(\d+)', seg)
        dep = re.search(r"(?:\bdep:'([^']*)'|\"dep\":\s*\"([^\"]*)\")", seg)
        if fase and dep:
            ut[n] = {"fase": int(fase.group(1)),
                     "dep": dep.group(1) or dep.group(2)}
            for felt in ("kl", "rev"):
                verdi = re.search(
                    rf"(?:\b{felt}:'([^']*)'|\"{felt}\":\s*\"([^\"]*)\")", seg)
                if verdi:
                    ut[n][felt] = verdi.group(1) or verdi.group(2)
    return ut


# Kontraktklassene katalogen bruker er de SAMME feltene modulregisteret lagrer,
# og registeret håndhever dem med CHECK-vilkår. Enumene leses derfor ut av
# migrasjonene, ikke skrevet av her: en kopi i testen ville vært nok en kilde
# som kan drive fra databasen — akkurat feilen porten finnes for å hindre.
# Migrasjonene kjøres i nummerrekkefølge, og en senere kan UTVIDE et vilkår
# (036 la `ekstern_lesing` til `sideeffektklasse`), så det siste treffet gjelder.
MIGRASJONER = ROT / "platform" / "core" / "db" / "migrations"
KONTRAKTFELT = {"kl": "sideeffektklasse", "rev": "reversibilitet"}


def _registerenum(kolonne: str) -> set[str]:
    gjeldende: set[str] | None = None
    for sql in sorted(MIGRASJONER.glob("*.sql")):
        for m in re.finditer(
                rf"CHECK\s*\(\s*{kolonne}\s+IN\s*\(([^)]*)\)",
                sql.read_text(encoding="utf-8"), re.S):
            gjeldende = set(re.findall(r"'([^']*)'", m.group(1)))
    assert gjeldende, f"fant ikke CHECK-vilkåret for {kolonne} i migrasjonene"
    return gjeldende


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


def _modulreferanser(dep: str, kjente: set[int]) -> set[int]:
    """Modulnumrene en dep-streng peker på — `M-14`, `modul 24` og intervaller
    som `1–2` eller `13–14`. Alt som ikke er et kjent modulnummer (connectorer,
    infrastruktur, «landpakke») faller utenfor: de har ingen fase å bryte."""
    ut: set[int] = set()
    for a, b in re.findall(r"(\d+)\s*[–-]\s*(\d+)", dep):
        ut.update(range(int(a), int(b) + 1))
    resten = re.sub(r"\d+\s*[–-]\s*\d+", " ", dep)
    ut.update(int(x) for x in re.findall(r"(?:M-|[Mm]odul\s+)?(\d+)", resten))
    return ut & kjente


def test_ingen_modul_avhenger_av_en_senere_fase():
    """Faseporten må være oppfyllelig (Codex P2 på PR #99).

    Spesifikasjonen sier at hver modul i en fase må være komplett før neste
    fase starter. En modul som avhenger av en modul i en SENERE fase gjør den
    regelen umulig å følge: ingen av de to kan bygges først. Det er ikke en
    smakssak i teksten — det er en utrullingsrekkefølge som ikke finnes.

    Codex fant M-53 → M-43. To til lå i samme fil (M-48 → M-23, M-38 → M-31),
    usett, fordi ingen port leste dep-feltene. Denne gjør det.

    MUTASJONEN SOM DREPER DENNE: sett `>` til `>=`. Da ville en avhengighet
    innenfor samme fase blitt et brudd — og det er den ikke: fasen er
    rekkefølgens grovkorn, modulene i den bygges i en rekkefølge fasen ikke
    bestemmer.
    """
    moduler = _moduler_fra_kilden()
    assert len(moduler) == MODULER, (
        f"leste {len(moduler)} moduler med fase og dep, forventet {MODULER}")
    kjente = set(moduler)
    brudd = [f"M-{n} (fase {d['fase']}) avhenger av "
             f"M-{r} (fase {moduler[r]['fase']})"
             for n, d in sorted(moduler.items())
             for r in sorted(_modulreferanser(d["dep"], kjente))
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

    MUTASJONEN SOM DREPER DENNE: la porten godta et hvilket som helst
    versjonsnummer. Da er den bare en stavekontroll for filnavnet, og det var
    aldri feilen — feilen var at nummeret pekte forbi fila.
    """
    gjeldende = SPEC_RE.search(KILDE.name)
    assert gjeldende, f"KILDE_REL navngir ikke en versjonert fil: {KILDE.name}"
    versjon = gjeldende.group(1)

    spor = subprocess.run(["git", "ls-files", "-z"], cwd=ROT,
                          capture_output=True, text=True, check=True)
    avvik = []
    for rel in spor.stdout.split("\0"):
        if not rel or rel.startswith(HISTORIKK):
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
