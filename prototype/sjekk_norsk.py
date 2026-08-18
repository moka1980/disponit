#!/usr/bin/env python3
"""Port mot ASCII-normalisert norsk.

Historikken forklarer formen: v8 ble bygget av et skript som skrev norsk
uten æøå. Feilen overlevde to kontrollrunder, begge ganger fordi
kontrollen var en liste over feil noen husket å lete etter. Forrige
versjon av denne filen påstod i sin egen docstring at den slo ord opp mot
en ordliste — den gjorde ikke det. Dette er mekanismen.

To lag, med ulik rolle:

  1. BEKREFTET  — ord som i våre dokumenter alltid er feil uten æøå, også
     når ASCII-formen er et ekte norsk ord i andre sammenhenger («bor»
     finnes, men vi mener «bør»). Deterministisk oppslag.

  2. HEURISTIKK — for hvert ASCII-ord på tre bokstaver eller mer som
     ikke står i lag 1: «ae»/«oe» erstattes først med æ/ø, deretter
     byttes inntil tre av ordets o/a til ø/å, i alle kombinasjoner av de
     posisjonene. Er en variant et vanlig norsk ord mens originalen ikke
     er det, meldes ordet til gjennomsyn. Dette fanger feil ingen har
     listet opp, som «Hoyere». Grensen på tre posisjoner er praktisk, ikke
     prinsipiell: den holder antall varianter nede.

  3. KORTFORM — heuristikken ser bort fra ord under tre bokstaver, fordi
     ettbokstavsord ellers ville meldt hver CSS-verdi og hver
     JS-variabel. «Å» er den eneste norske ettbokstavsformen som
     realistisk blir ASCII-normalisert, og den håndheves derfor
     deterministisk: en frittstående «A»/«a» som følges av tomrom og et
     ord på minst to bokstaver er alltid feil i norsk prosa. Tomrommet
     kan være mellomrom, linjeskift eller den lange rekken mellomrom
     `_prosa` legger igjen etter en tagg. Unntak for «A/B», «A-label»,
     «type a)» og oppstillinger som «kolonne A B C» følger av at de
     enten ikke er frittstående eller ikke følges av et ord.

Selvtesten dekker alle tre lagene POSITIVT. Et lag ingen test kan se, er
et lag som kan forsvinne mens porten fortsatt melder grønt.

Tre tekstkanaler dekkes: HTML-prosa og sitatmerkede attributtverdier,
vanlige strenglitteraler, og backtick-maler (template literals) som
bygger modulkortene. Interpolasjoner (${...}) blankes ut.

Fail-closed: mangler ordlisten, er porten rød. En kontroll som ikke kan
kjøre skal ikke rapportere grønt.

Bruk:
    python3 sjekk_norsk.py fil.html [...]
    python3 sjekk_norsk.py --selftest
Exit 1 ved treff eller manglende forutsetning.
"""
import re, sys, pathlib, itertools

# ---- lag 1: bekreftet feil i våre dokumenter -----------------------------
BEKREFTET = {
 "bor":"bør","hoy":"høy","ma":"må","na":"nå","sa":"så","pa":"på","fa":"få",
 "gar":"går","nar":"når","far":"får","star":"står","slar":"slår","bade":"både",
 "ogsa":"også","etterpa":"etterpå","gaar":"går","atte":"åtte","oyne":"øyne",
 "loses":"løses","taler":"tåler","gjor":"gjør","kjorer":"kjører","folger":"følger",
 "soker":"søker","sok":"søk","soknad":"søknad","soket":"søket","belop":"beløp",
 "innkjop":"innkjøp","leverandor":"leverandør","bokforing":"bokføring",
 "utloper":"utløper","overvaker":"overvåker","foreslar":"foreslår",
 "foreslatt":"foreslått","foresla":"foreslå","radgivende":"rådgivende",
 "lovpalagt":"lovpålagt","palagt":"pålagt","maned":"måned","arsrapport":"årsrapport",
 "nokler":"nøkler","nokkelord":"nøkkelord","verktoy":"verktøy","vaere":"være",
 "vaert":"vært","kraever":"krever","hoerer":"hører","apnet":"åpnet",
 "gjennomfores":"gjennomføres","forhandsgodkjente":"forhåndsgodkjente",
 "formal":"formål","inngatt":"inngått","dikters":"diktes","utgaende":"utgående",
 "speditor":"speditør","tilfore":"tilføre","ordinaer":"ordinær","vart":"vårt",
 "droftingsplikt":"drøftingsplikt","fravaerende":"fraværende","blast":"blåst",
 "kunngjoringer":"kunngjøringer","klargjor":"klargjør","unntakskoen":"unntakskøen",
 "feilsoking":"feilsøking","innelasing":"innelåsing","lonn":"lønn",
 "okonomi":"økonomi","kjopt":"kjøpt","sma":"små",
}

# Engelske og tekniske ord som ligner, men skal stå urørt.
FRITATT = {
 "outbox","shadow","execution","fail","closed","circuit","breaker","knowledge",
 "zero","list","status","support","server","system","match","standard","format",
 "post","start","data","type","side","sats","vert","veis","rev","policy","scope",
 "token","tenant","backup","cache","stage","staged","case","core","form","load",
 "sla","api","xml","html","json","zip","ocr","pdf","docx","doffin","ted","brreg",
 "peppol","ehf","altinn","taric","ofac","nace","vies","kyb","aml","hms",
}

GRENSE_EKTE = 3.0    # varianten må være et vanlig norsk ord
GRENSE_FALSK = 2.6   # originalen må være uvanlig

def _last_ordliste():
    try:
        from wordfreq import zipf_frequency
        return zipf_frequency
    except ImportError:
        return None

def _varianter(w):
    """Alle kombinasjoner av o->ø, a->å, ae->æ, oe->ø."""
    grunn = w.replace("ae", "æ").replace("oe", "ø")
    ut = {grunn}
    pos = [i for i, c in enumerate(grunn) if c in "oa"]
    for antall in range(1, min(len(pos), 3) + 1):
        for valg in itertools.combinations(pos, antall):
            bokstaver = list(grunn)
            for i in valg:
                bokstaver[i] = "ø" if bokstaver[i] == "o" else "å"
            ut.add("".join(bokstaver))
    ut.discard(w)
    return ut

# Frittstående ettbokstavsord som alltid er feil i norsk prosa.
# «A/B», «A-label», «type a)» treffes ikke: de er ikke frittstående.
#
# Skillet mellom ordene er `[\s\u00a0]+`, ikke ETT mellomrom (Codex P2 på
# PR #99). `_prosa` erstatter hver tagg med like mange mellomrom som taggen
# er lang, så «<p>A <b>gjøre</b> dette</p>» gir «A» etterfulgt av fire.
# Kravet om nøyaktig ett mellomrom slapp derfor gjennom enhver normalisert
# «Å» som sto rett foran markup — og det er nettopp der teksten i denne
# prototypen bor. Linjeskift og dobbelt mellomrom skjulte den like godt.
#
# Ordet etter må ha minst TO bokstaver. Det skiller prosa fra oppstilling:
# «Å gi» og «Å gjøre» treffes — det finnes ikke et norsk infinitivsuttrykk
# med ettbokstavsverb — mens «kolonne A B C» og en tabellrad med «A» og «B»
# i hver sin celle ikke gjør det. Store bokstaver godtas nå: markup mellom
# ordene skjuler «A Gjøre» like effektivt som små bokstaver gjorde.
KORTFORM = re.compile(r"(?<![\w/§.\-])([Aa])(?=[\s\u00a0]+[A-Za-zÆØÅæøå]{2})")

def _analyser(tekst, zipf):
    bekreftet, gjennomsyn = {}, {}
    for m in KORTFORM.finditer(tekst):
        kontekst = tekst[max(0, m.start() - 45):m.end() + 45].replace("\n", " ").strip()
        bekreftet.setdefault((m.group(1), "å"), kontekst)
    for m in re.finditer(r"[A-Za-zÆØÅæøå]+", tekst):
        w = m.group(0); lav = w.lower()
        if re.search(r"[æøå]", lav) or len(lav) < 3:
            continue
        kontekst = tekst[max(0, m.start() - 45):m.end() + 45].replace("\n", " ").strip()
        if lav in BEKREFTET:
            bekreftet.setdefault((w, BEKREFTET[lav]), kontekst); continue
        if lav in FRITATT or zipf is None:
            continue
        if zipf(lav, "nb") >= GRENSE_FALSK:
            continue                       # originalen er selv et vanlig norsk ord
        treff = [v for v in _varianter(lav) if zipf(v, "nb") >= GRENSE_EKTE]
        if treff:
            gjennomsyn.setdefault((w, max(treff, key=lambda v: zipf(v, "nb"))), kontekst)
    return bekreftet, gjennomsyn

def _skjul_utenom_strenger(bit):
    """Blank ut alt utenom sitatmerkede verdier (attributter, JS-kode)."""
    ut = []
    for del_ in re.split(r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\")", bit):
        ut.append(del_ if del_[:1] in "'\"" else re.sub(r"\S", " ", del_))
    return "".join(ut)


def _prosa(tekst):
    """Behold synlig tekst og sitatmerkede verdier; blank ut markup og kode.

    Taggnavn maa vekk: <a class=...> ville ellers meldt taggen som
    frittstaaende «a» i kortformkontrollen.
    """
    def blank_interpolasjon(mal):
        """Blank ut ${...} slik at JS-identifikatorer ikke blir norsk prosa."""
        forrige = None
        while forrige != mal:
            forrige = mal
            mal = re.sub(r"\$\{[^{}]*\}", lambda m: re.sub(r"\S", " ", m.group(0)), mal)
        return mal

    def skjul_js(js):
        """Behold strenglitteraler; blank ut kode.

        Tre tekstkanaler produserer kundevendt tekst i prototypen:
        HTML-prosa, vanlige strenger, og backtick-maler som bygger
        modulkortene. Alle tre maa kontrolleres — ellers kan en
        feilskriving leve i UI-et uten at porten ser den.
        """
        ut = []
        deler = re.split(r"('(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"|`(?:[^`\\]|\\.)*`)", js)
        for bit in deler:
            if bit[:1] == "`":
                ut.append(blank_interpolasjon(bit))
            elif bit[:1] in "'\"":
                ut.append(bit)
            else:
                ut.append(re.sub(r"\S", " ", bit))
        return "".join(ut)
    tekst = re.sub(r"<(script|style)[^>]*>.*?</\1>",
                   lambda m: re.sub(r"\S", " ", m.group(0)) if m.group(1) == "style"
                   else skjul_js(m.group(0)), tekst, flags=re.S)
    return re.sub(r"<[^>]+>", lambda m: _skjul_utenom_strenger(m.group(0)), tekst)

def sjekk(sti, zipf):
    bek, gj = _analyser(_prosa(sti.read_text(encoding="utf-8")), zipf)
    if not bek and not gj:
        print(f"OK    {sti.name}"); return 0
    print(f"FEIL  {sti.name}: {len(bek)} bekreftet, {len(gj)} til gjennomsyn")
    for tittel, funn in (("BEKREFTET", bek), ("TIL GJENNOMSYN", gj)):
        for (w, r), k in sorted(funn.items()):
            print(f"  [{tittel}] {w} -> {r}\n      … {k} …")
    return 1

def selftest(zipf):
    """Negativ test: et ord som IKKE står i BEKREFTET må fanges likevel."""
    feil = 0
    assert "hoyere" not in BEKREFTET, "selvtesten er verdilos hvis ordet er listet"
    _, gj = _analyser("Vi trenger en Hoyere terskel her.", zipf)
    if gj:
        print("selvtest 1 ok: ulistet feilskriving fanget —", list(gj)[0])
    else:
        print("SELVTEST FEILET: 'Hoyere' ble ikke fanget"); feil = 1
    b3, _ = _analyser("A gi dem egne numre ville blåst opp katalogen.", zipf)
    if b3:
        print("selvtest 3 ok: frittstående A fanget —", list(b3)[0])
    else:
        print("SELVTEST FEILET: 'A gi' ble ikke fanget"); feil = 1

    # Kortformen må overleve det som står MELLOM ordene (Codex P2 på PR #99).
    # Selvtest 3 over dekker bare ett enkelt mellomrom, og med den formen sto
    # porten grønn mens hver normalisert «Å» rett foran en tagg gikk gjennom.
    # Hver variant her er en måte teksten faktisk ser ut i prototypen.
    for merke, prove in (("markup", "<p>A <b>gjøre</b> dette</p>"),
                         ("linjeskift", "Vi ber deg\nA gjøre dette."),
                         ("dobbelt mellomrom", "Det er lett A  gjøre."),
                         ("stor forbokstav", "Det er lett A Gjøre.")):
        b, _ = _analyser(_prosa(prove), zipf)
        if b:
            print(f"selvtest 6 ok ({merke}): frittstående A fanget")
        else:
            print(f"SELVTEST FEILET: 'A' over {merke} ble ikke fanget"); feil = 1

    mal = _prosa("<script>const x=`<h5>Dette utfores automatisk</h5>"
                 "<p>${esc(m.goal)}</p>`;</script>")
    _, g4 = _analyser(mal, zipf)
    if g4:
        print("selvtest 4 ok: feil i backtick-mal fanget —", list(g4)[0])
    else:
        print("SELVTEST FEILET: feil i template literal ble ikke fanget"); feil = 1
    if re.search(r"\besc\b|\bm\.goal\b", mal):
        print("SELVTEST FEILET: ${...} ble ikke blanket ut"); feil = 1
    else:
        print("selvtest 5 ok: interpolasjon blanket ut")

    # Lag 1 må testes POSITIVT (Codex P2 på PR #99). Uten dette dekket
    # selvtesten bare heuristikken, kortformen og malutdragingen — så
    # `BEKREFTET`-oppslaget kunne fjernes eller forbigås mens alle fem
    # kontrollene meldte grønt. «bor» er valgt nettopp fordi heuristikken
    # IKKE kan redde den: originalen er selv et vanlig norsk ord, og
    # frekvensporten under `GRENSE_FALSK` hopper derfor over ordet. Er den
    # forutsetningen borte, er testen verdiløs, og da sier den fra.
    assert BEKREFTET.get("bor") == "bør", "selvtesten forutsetter at «bor» er listet"
    if zipf("bor", "nb") < GRENSE_FALSK:
        print("SELVTEST FEILET: «bor» er ikke lenger tvetydig — velg et annet "
              "ord, ellers måler selvtest 7 heuristikken og ikke ordlisten")
        feil = 1
    b7, _ = _analyser("Dette bor gjøres i dag.", zipf)
    if ("bor", "bør") in b7:
        print("selvtest 7 ok: bekreftet ordliste slo til — ('bor', 'bør')")
    else:
        print("SELVTEST FEILET: 'bor' ble ikke slått opp i BEKREFTET"); feil = 1

    b2, g2 = _analyser("Vi trenger en høyere terskel, og løsningen bør være enkel. "
                       "Vi tester A/B og en A-label, som skal stå urørt.", zipf)
    if b2 or g2:
        print("SELVTEST FEILET: korrekt norsk gav treff", b2, g2); feil = 1
    else:
        print("selvtest 2 ok: korrekt norsk gir ingen treff")
    return feil

if __name__ == "__main__":
    zipf = _last_ordliste()
    if zipf is None:
        print("RØD PORT: ordlisten (wordfreq) mangler. Installer: pip install wordfreq")
        sys.exit(1)
    if "--selftest" in sys.argv:
        sys.exit(selftest(zipf))
    filer = [pathlib.Path(a) for a in sys.argv[1:] if not a.startswith("-")] or \
            [pathlib.Path("/mnt/user-data/outputs/AI-bedriftsagent-prototype-v8.html")]
    sys.exit(max([selftest(zipf)] + [sjekk(f, zipf) for f in filer]))
