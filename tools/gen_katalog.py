#!/usr/bin/env python3
"""Generer modulkatalogen for forsiden fra spesifikasjonen — én kilde, ikke avskrift.

Katalogen (57 moduler, 11 områder, 4 faser) lever i
`docs/spesifikasjon/disponit-prototype-v9.html`. Å taste den inn på nytt ville
gitt to sannheter som driver fra hverandre; dette skriptet leser spesifikasjonen
og skriver ut både datafila og locale-nøklene, så en endring i katalogen bare
krever en ny kjøring.

Engelske navn er OVERSATT her, ikke maskinelt: modulnavn er produktnavn, og en
maskinoversettelse av «Kundefordringsagent» blir ikke «Accounts receivable
agent» av seg selv. Hver oversettelse bærer derfor kildenavnet sitt, og en
omdøping i spesifikasjonen stopper genereringen til noen har oversatt det NYE
navnet — ellers ville den norske og den engelske katalogen drevet fra hverandre
uten at noe sa fra.
"""
import json
import pathlib
import re
import sys

# Repoet finnes ut fra SKRIPTETS egen plassering, ikke fra en absolutt sti på
# én maskin (Codex P1). En hardkodet `/home/<bruker>/...` gjør genereringen
# umulig å reprodusere i CI eller på en annen laptop — og verre: den ville lest
# en checkout som kunne ha et ANNET innhold enn commiten som ble generert fra,
# uten at noe sa fra. Kilden er sporet i repoet, så den er kilden.
ROT = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 \
    else pathlib.Path(__file__).resolve().parent.parent

# SANNHETSKILDEN, ikke arkivet (Codex P2). Generatoren leste tidligere
# `prototype/AI-bedriftsagent-prototype-v7.html` — v7.0. `README.md` peker på
# spesifikasjonen i `docs/spesifikasjon/` som sannhetskilden, og
# `docs/STRUKTUR.md` kaller `prototype/` et historisk arkiv som ALDRI endres.
# De to filene ga identisk katalog den gangen, så feilen ga ingen synlig
# forskjell — den var stille: neste kanoniske modul-, område- eller faseendring
# ville ikke nådd generatoren, og en ny kjøring ville reprodusert gammelt
# offentlig innhold uten at noe klaget. Et arkiv som aldri endres kan per
# definisjon ikke være inndata til noe som skal følge produktet.
#
# v8 erstattet v7 og slettet den gamle fila (Codex P1 på PR #99). En generator
# som peker på en slettet fil er ikke stille — den stopper på `SystemExit` — men
# den ville uansett vært feil: uten dette bytte ville den offentlige katalogen
# blitt stående på 45 moduler mens produktomfanget er 55.
KILDE = ROT / "docs" / "spesifikasjon" / "disponit-prototype-v9.html"
KILDE_NAVN = "docs/spesifikasjon/disponit-prototype-v9.html"

# Produktomfanget slik det står i spesifikasjonen. Tallet står ett sted og
# brukes både til nummerporten under og til overskriften i den genererte fila,
# så en utvidelse ikke kan gi en katalog som teller seg selv feil.
ANTALL_MODULER = 57

# Områdenavn på engelsk.
OMRADE_EN = {
    "Analyse og ledelse": "Analytics and management",
    "Data og kunnskap": "Data and knowledge",
    "Dokument og kommunikasjon": "Documents and communication",
    "IT og drift": "IT and operations",
    "Innkjøp og logistikk": "Purchasing and logistics",
    "Juridisk og compliance": "Legal and compliance",
    "Kunde og salg": "Customers and sales",
    "Markedsføring": "Marketing",
    "Plattform og sikkerhet": "Platform and security",
    "Samarbeid og HR": "Collaboration and HR",
    "Økonomi": "Finance",
}

# Modulnavn: KILDENAVNET slik det står i spesifikasjonen, og oversettelsen av
# nettopp det navnet (Codex P2). Tabellen slo tidligere opp på nummer alene, og
# da var oversettelsen bundet til en modulPLASS, ikke til et produktnavn: fikk
# modul 42 nytt navn i spesifikasjonen uten nytt nummer, oppdaterte generatoren
# `nb.json` mens `en.json` beholdt det gamle navnet. Ingen port kunne fange det —
# utdata var byte-identisk med seg selv, og den engelske nøkkelen var ikke tom —
# så de to offentlige katalogene kunne si forskjellige ting i det uendelige.
#
# Med kildenavnet i tabellen er en omdøping ikke lenger stille: generatoren
# stopper til noen har lest det nye navnet og bestemt hva det heter på engelsk.
# Det er hele poenget — oversettelsen er en avgjørelse, ikke en oppslagsverdi
# (se modulen sin egen docstring om «Kundefordringsagent»).
MODUL_EN = {
    1: ("Policy- og fullmaktsmotor",
        "Policy and authority engine"),
    2: ("Revisjonslogg og evidens",
        "Audit log and evidence"),
    3: ("Datakvalitetsagent",
        "Data quality agent"),
    4: ("Data- og filforvalter",
        "Data and file manager"),
    5: ("Dokument- og malagent",
        "Document and template agent"),
    6: ("E-postoperasjonsagent",
        "Email operations agent"),
    7: ("Møteoperasjonsagent",
        "Meeting operations agent"),
    8: ("Kalender- og kapasitetsagent",
        "Calendar and capacity agent"),
    9: ("Kunnskaps- og ordlisteagent",
        "Knowledge and glossary agent"),
    10: ("Backup- og gjenopprettingsagent",
        "Backup and recovery agent"),
    11: ("Integrasjons- og selvtestagent",
        "Integration and self-test agent"),
    12: ("Identitets- og tilgangsagent (JML)",
        "Identity and access agent (JML)"),
    13: ("Bankavstemmingsagent",
        "Bank reconciliation agent"),
    14: ("Faktura- og utleggsagent",
        "Invoice and expense agent"),
    15: ("Likviditets- og kostnadsagent",
        "Liquidity and cost agent"),
    16: ("Rapporterings- og KPI-agent",
        "Reporting and KPI agent"),
    17: ("Kundeserviceagent",
        "Customer service agent"),
    18: ("Kunde-onboardingagent",
        "Customer onboarding agent"),
    19: ("Kundehelse- og fornyelsesagent",
        "Customer health and renewal agent"),
    20: ("Nettside- og innholdsagent",
        "Website and content agent"),
    21: ("Avtale- og fristagent",
        "Contract and deadline agent"),
    22: ("SaaS- og lisensagent",
        "SaaS and licence agent"),
    23: ("Kundefordringsagent",
        "Accounts receivable agent"),
    24: ("Leverandør- og innkjøpsagent",
        "Supplier and purchasing agent"),
    25: ("Ordre-til-betaling-agent",
        "Order-to-cash agent"),
    26: ("Tilbuds- og prisagent",
        "Quote and pricing agent"),
    27: ("Lager- og påfyllingsagent",
        "Inventory and replenishment agent"),
    28: ("Logistikk- og transportagent",
        "Logistics and transport agent"),
    29: ("Sikkerhets- og hendelsesagent",
        "Security and incident agent"),
    30: ("Personvern- og datasubjektagent",
        "Privacy and data subject agent"),
    31: ("Agentkvalitet og modellstyring",
        "Agent quality and model governance"),
    32: ("Global lokaliserings- og skatteagent",
        "Global localisation and tax agent"),
    33: ("Prediksjons- og scenarioagent",
        "Prediction and scenario agent"),
    34: ("Compliance- og sertifiseringsagent",
        "Compliance and certification agent"),
    35: ("Krise- og kontinuitetsagent",
        "Crisis and continuity agent"),
    36: ("Bedriftsoptimalisator",
        "Business optimiser"),
    37: ("Unntaks- og feilhåndteringsagent",
        "Exception and error handling agent"),
    38: ("Kapasitets-, kø- og modellruter",
        "Capacity, queue and model router"),
    39: ("Lønnsgrunnlagsagent",
        "Payroll basis agent"),
    40: ("HR- og medarbeideragent",
        "HR and employee agent"),
    41: ("Abonnements- og inntektsagent",
        "Subscription and revenue agent"),
    42: ("Svindel- og transaksjonsvakt",
        "Fraud and transaction guard"),
    43: ("Tale- og telefoniagent",
        "Voice and telephony agent"),
    44: ("Kampanje- og markedsinnsiktsagent",
        "Campaign and market insight agent"),
    45: ("Bærekrafts- og ESG-agent",
        "Sustainability and ESG agent"),
    46: ("Anbuds- og konkurransevakt",
        "Tender and bid watch"),
    47: ("Myndighetsrapporteringsagent",
        "Regulatory reporting agent"),
    48: ("Foretaks- og kredittvakt (KYB)",
        "Business and credit watch (KYB)"),
    49: ("Sanksjons- og hvitvaskingsvakt",
        "Sanctions and anti-money-laundering watch"),
    50: ("Postjournal- og innsynsvakt",
        "Public records and disclosure watch"),
    51: ("Tilskudds- og støtteagent",
        "Grants and subsidies agent"),
    52: ("Toll- og HS-kodeagent",
        "Customs and HS code agent"),
    53: ("HMS- og avviksmottak",
        "HSE and incident intake"),
    54: ("EHF- og Peppol-avviksretter",
        "EHF and Peppol rejection handler"),
    55: ("Merkevare- og IP-overvåker",
        "Brand and IP monitor"),
    56: ("Automatisk WCAG-kontroll",
        "Automatic WCAG check"),
    57: ("Rekrutteringsagent (ATS)",
        "Recruitment agent (ATS)"),
}


# Modulpostene i `M` står i TO former, og begge må leses (Codex P1 på PR #99).
# v7-postene er kompakte JS-literaler — `{n:1,name:'…',area:'…',p:1,…}` — mens
# de ti nye v8-postene er JSON-aktige: `{"n": 46, "name": "…", …}`. En parser
# bundet til den ene formen feiler ikke; den finner bare FÆRRE moduler, og det
# er den verste utgaven: `katalog.js` ville blitt regenerert til 45 moduler,
# ferskhetsporten ville vært grønn mot sitt eget utdata, og forsiden hadde vist
# et produktomfang ingen har bestemt. Nummerporten under er derfor det som
# fanger et parserbrudd, og den krever HELE 1..ANTALL_MODULER.
#
# Mellomrommene mellom feltene er `[^{}]*?`, ikke `.*?`: en post uten `area`
# ville med `.*?` latt treffet renne videre inn i NESTE post og gitt et
# nummerpar som aldri sto sammen i kilden. Ingen `{`/`}` finnes inne i
# feltverdiene, så klammeforbudet er en trygg postgrense.
#
# Mønsteret brukes bare til å hente VERDIENE ut av en post som alt er funnet
# strukturelt, og det står forankret på postens egen begynnelse (Codex P2 på
# #118, fjortende runde). Fritt søk gjennom skriptet var det som gjorde en
# postformet STRENG i UI-koden til en modul til — se `les_katalog()`.
POST_RE = re.compile(
    r"""\{\s*["']?n["']?\s*:\s*(\d+)\s*,"""
    r"""\s*["']?name["']?\s*:\s*(['"])(.*?)\2"""
    r"""[^{}]*?,\s*["']?area["']?\s*:\s*(['"])(.*?)\4"""
    r"""\s*,\s*["']?p["']?\s*:\s*(\d+)""",
    re.S)

# Selve katalogen: `const M = [ … ]`. Den er ankeret postene leses ut av, og
# den skal stå nøyaktig én gang — to `const M` er en redeklarasjon nettleseren
# selv avviser. `let` og `var` er med fordi de erklærer det samme; endres
# formen til noe annet enn en erklært liste, stopper generatoren HØYT i stedet
# for å lete videre i skriptet.
KATALOG_RE = re.compile(r"\b(?:const|let|var)\s+M\s*=\s*\[")

# Katalogen bærer STRUKTUR (nummer, navn, område, fase) — ikke tilstand.
# Et eget statusfelt her sto kort i #109 og er tatt ut igjen (Codex P1): to
# statuskilder for samme modul kan bare drive fra hverandre, og den ene som
# ikke er forankret i et manifest kan love drift ingen port har bestått.
# Hva en modul FAKTISK er, står ett sted: `MODULSTATUS` i plattformdata.js,
# avledet av manifestene og pinnet av test_ui_kontrakt.py.
#
# Forbudet håndheves av `les_katalog()`, og det gjelder MODULPOSTENE — ikke alt
# som står i en `<script>`-tagg (Codex P2 på #118). Siden er en levende
# prototype med egen UI-kode: et tilstandsobjekt med `status:` i en
# filterrutine, et API-eksempel i en hjelpetekst — alt sammen helt lovlig, og
# ingen av delene en tilstandsakse ved siden av manifestet. Et forbud som
# stoppet dem ville stoppet ferskhetsporten med en modulfeilmelding om noe som
# ikke er en modul, og et forbud man må slå av for å skrive UI-kode blir slått
# av.
FORBUDTE_FELT = ("status", "driftstilstand")


def slug(navn: str) -> str:
    tegn = {"æ": "ae", "ø": "o", "å": "a", " ": "_"}
    ut = "".join(tegn.get(c, c) for c in navn.lower())
    return re.sub(r"[^a-z0-9_]", "", ut)


def slutt_ikkekode(tekst: str, i: int) -> int:
    """Indeksen etter strengen eller kommentaren som åpner i `i`.

    Strenger: enkelt- og dobbeltfnutt og backtick, med `\\` som escape.
    Kommentarer: `//` til linjeskift, `/* */` til lukkingen. Uterminert går
    til tekstslutt — da sier `les_post()` fra om at posten aldri lukkes.
    """
    if tekst.startswith("//", i):
        j = tekst.find("\n", i)
        return len(tekst) if j < 0 else j + 1
    if tekst.startswith("/*", i):
        j = tekst.find("*/", i + 2)
        return len(tekst) if j < 0 else j + 2
    sitat = tekst[i]
    j = i + 1
    while j < len(tekst) and tekst[j] != sitat:
        j += 2 if tekst[j] == "\\" else 1
    return j + 1


_NAVNETEGN = re.compile(r"[A-Za-z_$][\w$]*")
_TALL = re.compile(r"-?\d[\w.]*")
# Ord som er en VERDI og ikke et navn. Katalogen bruker dem ikke i dag, men de
# er literaler, og en literal kan generatoren hoppe trygt over.
ORDVERDIER = ("true", "false", "null")


def hopp_over_tomrom(tekst: str, i: int) -> int:
    """Indeksen til første tegn fra `i` som verken er blankt eller kommentar.

    Mellom en nøkkel og kolonet den hører til kan det stå begge deler — JS
    bryr seg ikke — og for spørsmålet «er dette et felt?» er de like mye
    ingenting.
    """
    while i < len(tekst):
        if tekst[i].isspace():
            i += 1
        elif tekst.startswith("//", i) or tekst.startswith("/*", i):
            i = slutt_ikkekode(tekst, i)
        else:
            break
    return i


# Navnet et felt får når generatoren IKKE kan lese egenskapen. Et navn den ikke
# vet, kan den heller ikke forby — og en verdi den ikke kan lese, kan den ikke
# hoppe trygt OVER, så et `status`-felt bak den forsvinner like stille.
# Tegnet kan ikke kollidere med et ekte feltnavn: `?` er ikke lovlig i en
# JS-identifikator, og en strengnøkkel som inneholder det leses som seg selv.
UKJENT_FELT = "?"


def nokkelnavn(innhold: str) -> str:
    """Feltnavnet en strengnøkkel med dette innholdet gir — eller `UKJENT_FELT`.

    Råteksten mellom fnuttene ER navnet bare så lenge den ikke inneholder en
    escape-sekvens (Codex P2 på #118, tolvte runde). `['\\x73tatus']` og
    `"\\u0073tatus":` gir begge den helt alminnelige egenskapen `status` i
    nettleseren, mens en sammenligning mot råteksten ser `\\x73tatus` og lar
    dem passere statusforbudet. Siden ville tegnet aksen; generatoren ville
    kastet den i stillhet. Samme hull lot en escapet `kl`- eller `rev`-nøkkel
    forsvinne for kontraktparseren, og et felt som ikke finnes blir ikke
    kontrollert.

    Vi AVVISER i stedet for å tolke. Å tolke betyr å skrive JS-strengregler i
    Python — `\\xHH`, `\\uHHHH`, `\\u{…}`, linjefortsettelse, og de gamle
    oktale escapene som fortsatt gjelder utenfor «use strict» — og hver form
    som blir glemt er et nytt smutthull av nøyaktig denne typen. Avvisningen er
    lukket: en nøkkel som inneholder en omvendt skråstrek er ikke et navn
    generatoren kan lese, punktum. Prisen er null, for katalogens feltnavn er
    `n`, `name`, `area`, `p`, `kl`, `rev`, `dep`, `guard` — ingen av dem
    trenger en escape for å skrives.
    """
    return UKJENT_FELT if "\\" in innhold else innhold


def les_nokkel(post: str, i: int) -> tuple[str, int]:
    """(feltnavn, indeksen etter nøkkelen) for nøkkelen som begynner i `i`.

    `(UKJENT_FELT, -1)` når det som står der ikke er et navn generatoren kan
    lese.

    JS godtar langt flere former i nøkkelposisjon enn de to katalogen bruker, og
    hver av dem gir nettleseren en helt alminnelig egenskap: beregnet nøkkel
    (`[k]:`, `['status']:`, `[/]/.test("") ? "x" : "status"]:`), malstrengnøkkel,
    escapet nøkkel (`\\u0073tatus:`, `'\\x73tatus':`), spredning (`...o`),
    forkortelse (`{status}`), metode (`status() {}`) og accessor. Codex fant dem
    én for én på #118, ellevte til trettende runde, og hullet var hver gang det
    samme: et felt generatoren ikke SÅ, kunne den heller ikke forby.

    Derfor står regelen nå som en LUKKET liste over det som går an, ikke som en
    åpen liste over det som ikke gjør det — den forrige typen liste er nettopp
    det som ga fem runder med nye former. Nøkkelen er ENTEN et navn skrevet med
    bokstaver ELLER en fnuttstreng uten escape. Alt annet er uleselig, og en
    uleselig nøkkel stopper generatoren. Prisen er null: katalogens feltnavn er
    `n`, `name`, `area`, `p`, `kl`, `rev`, `dep`, `guard` — ingen av dem trenger
    noen annen form for å skrives.
    """
    if post[i] in "\"'":
        j = slutt_ikkekode(post, i)
        return (UKJENT_FELT, -1) if j > len(post) \
            else (nokkelnavn(post[i + 1:j - 1]), j)
    if (treff := _NAVNETEGN.match(post, i)):
        return treff.group(0), treff.end()
    return UKJENT_FELT, -1


def les_verdi(post: str, i: int) -> int:
    """Indeksen etter LITERALEN som begynner i `i`, eller -1.

    Literal er fnuttstreng, tall, `true`/`false`/`null`, og lister og objekter
    av slike — altså nøyaktig det katalogen bærer. Alt annet, som et mønster
    (`/["']/`), en malstreng eller et regnestykke, er -1.

    Hvorfor verdien i det hele tatt må LESES og ikke bare hoppes over med en
    dybdeteller: skal generatoren finne neste felt, må den vite hvor denne
    verdien slutter, og en teller som ikke kjenner mønstre gjetter feil på
    `x: /["']/, status: 'planlagt'` — fnutten i mønsteret ser ut som starten på
    en streng, strengen spiser kommaet, og `status` blir aldri et felt. En verdi
    generatoren ikke kan lese, er derfor en post den ikke kan uttale seg om.
    """
    if i >= len(post):
        return -1
    c = post[i]
    if c in "\"'":
        j = slutt_ikkekode(post, i)
        return -1 if j > len(post) else j
    if (treff := _TALL.match(post, i)):
        return treff.end()
    if (treff := _NAVNETEGN.match(post, i)) and treff.group(0) in ORDVERDIER:
        return treff.end()
    if c in "[{":
        return les_samling(post, i)
    return -1


def les_samling(post: str, i: int) -> int:
    """Indeksen etter lista eller objektet som åpner i `i`, eller -1.

    Feltene i et NØSTET objekt hører til det objektet og ikke til modulposten,
    så navnene brukes ikke til noe her. De må likevel leses: uten dem vet vi
    ikke hvor den nøstede verdien slutter, og da vet vi heller ikke hvor postens
    neste felt begynner.
    """
    lukk = "]" if post[i] == "[" else "}"
    n = len(post)
    j = hopp_over_tomrom(post, i + 1)
    while j < n:
        if post[j] == lukk:
            return j + 1
        if lukk == "}":
            _, j = les_nokkel(post, j)
            if j < 0:
                return -1
            j = hopp_over_tomrom(post, j)
            if j >= n or post[j] != ":":
                return -1
            j = hopp_over_tomrom(post, j + 1)
        j = les_verdi(post, j)
        if j < 0:
            return -1
        j = hopp_over_tomrom(post, j)
        if j < n and post[j] == ",":
            j = hopp_over_tomrom(post, j + 1)
        elif j >= n or post[j] != lukk:
            return -1
    return -1


def les_elementer(tekst: str, i: int):
    """Gi fra deg startindeksen til hvert element i lista som åpner i `i`.

    Lista leses som en liste: element, komma, element, `]`. Går det ikke i hop,
    stopper generatoren — en katalog som ikke lar seg lese som det den er, er
    ikke en kilde.

    Elementet gis fra seg FØR det leses ferdig, slik at `les_katalog()` rekker
    å si hva som er galt med akkurat den posten. Meldingen «M-57 har en
    egenskap generatoren ikke kan lese» er den som forteller hva som må rettes;
    «element 57 lar seg ikke lese» sier bare hvor man skal se.
    """
    nr, n = 0, len(tekst)
    j = hopp_over_tomrom(tekst, i + 1)
    while j < n:
        if tekst[j] == "]":
            return
        if tekst[j] != "{":
            raise SystemExit(
                f"element {nr + 1} i modulkatalogen i {KILDE_NAVN} er ikke en "
                f"modulpost. Katalogen er en liste av poster, og bare det.")
        nr += 1
        yield j
        _, j = les_post(tekst, j)
        if j < 0:
            raise SystemExit(
                f"element {nr} i modulkatalogen i {KILDE_NAVN} lar seg ikke "
                f"lese som en post.")
        j = hopp_over_tomrom(tekst, j)
        if j < n and tekst[j] == ",":
            j = hopp_over_tomrom(tekst, j + 1)
        elif j >= n or tekst[j] != "]":
            raise SystemExit(
                f"element {nr} i modulkatalogen i {KILDE_NAVN} følges hverken "
                f"av komma eller av listens slutt.")
    raise SystemExit(
        f"modulkatalogen i {KILDE_NAVN} lukkes aldri — kilden har endret form.")


def les_post(tekst: str, start: int) -> tuple[list[str], int]:
    """(feltnavnene på ØVERSTE nivå, indeksen etter posten som åpner i `start`).

    Sluttindeksen er -1 når posten ikke lar seg lese.

    Posten leses som det den er: en følge av `nøkkel: verdi` skilt med komma.
    Et navn telles bare når det står rett inne i postens egen krøllparentes —
    ikke i en liste, ikke i et nøstet objekt — og bærer en verdi.

    POSTGRENSEN og FELTNAVNENE leses av samme lesning. De var to skanninger med
    hvert sitt regelsett: en dybdeteller fant `}`, og en feltskanner leste
    navnene innenfor. To grammatikker over samme tekst er to steder å ta feil,
    og de tar feil hver for seg — en verdi dybdetelleren leste galt flyttet
    postgrensen, og da fikk feltskanneren aldri se resten av posten. Med én
    lesning er en post enten lest i sin helhet eller avvist ved navn.

    Forbudet mot en parallell statusakse (Codex P2 på #118, sjette runde) leste
    før dette rått i posten med et mønster på «`{` eller komma, så navnet, så
    kolon». Nøkkelposisjon er riktig krav, men et regex ser ikke forskjell på
    kode og tekst: en feltverdi som dokumenterer et JSON- eller API-fragment —
    `input: "Svar: {status: ok}"` — traff mønsteret inne i fnuttene, og
    generatoren stoppet med en modulfeilmelding om en tilstandsakse modulen
    ikke har. Motsatt vei holder posisjonskravet fortsatt: ordet «status» står
    overalt i feltVERDIene — «samtykkestatus», «onboardingstatus» — og det er
    prosa om hva en modul gjør. Bare et FELT er en tilstandsakse.

    Deretter fulgte fem runder der Codex fant én ny SKRIVEMÅTE av gangen —
    kommentar mellom nøkkel og kolon, `['status']:`, `['\\x73tatus']:`, et
    mønster med `]` i en beregnet nøkkel, `\\u0073tatus:`, spredning og
    forkortelse. Hver retting la til ett tilfelle i en skanner som ellers gikk
    videre på alt den ikke kjente igjen, og en skanner som går videre på det
    ukjente er åpen i feil ende: det neste tilfellet er alltid ett til.

    Derfor er retningen snudd. `les_nokkel()` og `les_verdi()` sier hva som ER
    lesbart, og alt utenfor blir `UKJENT_FELT` — en post generatoren stopper på.
    Katalogen er en sannhetskilde som skal kunne leses av mer enn nettleseren;
    en post som bare nettleseren forstår, er ikke en kilde.
    """
    felt: list[str] = []
    n = len(tekst)
    i = hopp_over_tomrom(tekst, start + 1)
    while i < n:
        if tekst[i] == "}":
            return felt, i + 1
        navn, j = les_nokkel(tekst, i)
        j = hopp_over_tomrom(tekst, j) if j >= 0 else j
        if j < 0 or j >= n or tekst[j] != ":":
            # Ikke `nøkkel: verdi`: spredning, forkortelse, metode, accessor —
            # eller en nøkkel bare nettleseren kan stave ut.
            return felt + [UKJENT_FELT], -1
        j = les_verdi(tekst, hopp_over_tomrom(tekst, j + 1))
        if j < 0:
            return felt + [UKJENT_FELT], -1
        felt.append(navn)
        i = hopp_over_tomrom(tekst, j)
        if i < n and tekst[i] == ",":
            i = hopp_over_tomrom(tekst, i + 1)
        elif i < n and tekst[i] != "}":
            return felt + [UKJENT_FELT], -1
    # Uterminert post er ikke en tom mengde treff — det er en kilde som har
    # endret form, og da er katalogen ikke lenger lesbar.
    return felt + [UKJENT_FELT], -1


def er_en_modulliste(skript: str, i: int) -> bool:
    """Lar lista som åpner i `i` seg lese som en liste av modulposter?

    Spørsmålet stilles bare når ankeret `const M = [` finnes mer enn én gang,
    og skiller da en ERKLÆRING fra tegnene som bare ser ut som en. Se
    `les_katalog()`.

    Formen er svaret, ikke plasseringen. En liste er en liste — element,
    komma, element, `]` — og hvert element er en modulpost. Tegnene `const M =
    [` skrevet inne i en streng eller en kommentar er fulgt av en fnutt, et
    linjeskift eller hva som helst annet, og faller derfor på sin egen form,
    uten at generatoren trenger å vite hvor i skriptet de står.

    Men en TOM liste er ikke en katalog (Codex P2 på #118, sekstende runde).
    Spørsmålet «lar hvert element seg lese som en post?» er sant uten videre
    når det ikke finnes noen elementer, og en helt alminnelig hjelpetekst —
    `const hjelp = "const M = []";` eller en kommentar med samme innhold — ble
    derfor stående som en katalog nummer to. Generatoren stoppet på en
    redeklarasjon nettleseren ikke ser, og ferskhetsporten ble rød av en
    tekstendring som ikke rører katalogen. Den tomme sannheten er den samme
    åpne enden som før, bare snudd: et krav ingenting kan bryte, kan heller
    ikke skille.

    Antallet spørres det fortsatt ikke om utover dette — en HALV katalog
    nummer to er en erklæring og stopper generatoren som før. Og at en tom
    andre-erklæring i KODEN ikke lenger stopper her, er ikke et hull som blir
    stående: to `const M` er en redeklarasjon nettleseren avviser, siden er da
    død i nettleseren, og porten i `test_katalog.py` krever nøyaktig ett anker
    i KODE — den har JS-skanneren som skiller kode fra tekst, og det er den
    lesningen som kan svare på spørsmålet generatoren her ikke kan stille.
    """
    poster = 0
    try:
        for start in les_elementer(skript, i):
            if not POST_RE.match(skript, start):
                return False
            poster += 1
    except SystemExit:
        return False
    return poster > 0


def les_katalog() -> list[dict]:
    if not KILDE.exists():
        raise SystemExit(f"fant ikke sannhetskilden: {KILDE_NAVN}")
    skript = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>",
                                  KILDE.read_text(encoding="utf-8",
                                                  errors="replace"), re.S))
    # Statusforbudet over sto bare som prosa, og prosa er ikke en port: feltet
    # kom tilbake i v9 for M-57 etter at det ble tatt ut for M-56 i #109 (Codex
    # P2 på #118). Generatoren KASTER feltet stille, så den siden som tegner det
    # rett fra kilden blir stående på gammel tilstand mens `MODULSTATUS` fører
    # modulen videre — ingenting knekker, kilden bare lyver. Nå stopper det her,
    # og kun der en modulpost faktisk står.
    #
    # Postgrensen var «første `}` etter treffet», med begrunnelsen at ingen
    # krøllparentes finnes inne i feltverdiene (Codex P2 på #118, femte runde).
    # Det er sant i dag og ingen regel: `guard`, `input` og `accept` er fri
    # prosa, og et JSON- eller malfragment i en av dem — `{}`, `${sak}` — hadde
    # kuttet posten der, midt inne i en streng. Alt etter kuttet, inkludert et
    # `status`-felt, falt utenfor det forbudet fikk se. Og nummerporten under
    # fanger det ikke: POST_RE er ferdig med å matche ved `p`, så posten telles
    # som normalt.
    #
    # Innenfor posten leses FELTNAVNENE, ikke teksten (Codex P2, sjette runde),
    # så et JSON-fragment i en fri feltverdi ikke kan se ut som en tilstandsakse.
    # Grense og feltnavn kommer fra SAMME lesning, se `les_post()`.
    #
    # Men HVOR postene står ble fortsatt funnet med et fritt søk gjennom hele
    # skriptet (Codex P2 på #118, fjortende runde), og et regex mot rå tekst
    # ser ikke forskjell på kode og streng. Siden er en levende prototype med
    # egen UI-kode, og en helt lovlig linje som
    # `const demo = "{n:58,name:'Demo',area:'X',p:1}";` ble lest som en modul
    # til: nummerporten under stoppet genereringen med en melding om en modul
    # ingen har skrevet. Motsatt vei kunne en post inne i en kommentar telle
    # med på samme måte.
    #
    # Å skanne skriptet for hva som er kode og hva som er tekst ville betydd å
    # skrive JS-leseren fra `test_katalog.py` en gang til her, med
    # skråstrek-tvetydigheten og alt annet som fulgte med den. Roten er en
    # annen: postene skal ikke LETES ETTER i det hele tatt. Katalogen er en
    # liste, `const M = [ … ]`, og en liste har elementer. Vi leser listen som
    # det den er, og da kan en tekst som ser ut som en post ikke være en post —
    # den er en verdi inne i en av dem, eller den står et helt annet sted i
    # skriptet.
    #
    # Ankeret må finnes NØYAKTIG én gang. To `const M` ville uansett vært en
    # redeklarasjon nettleseren avviser, så kravet er det samme som JS stiller.
    #
    # Men ANKERET ble fortsatt lett etter i rå skripttekst (Codex P2 på #118,
    # femtende runde), og et regex mot rå tekst ser ikke forskjell på kode og
    # streng. En helt lovlig linje i UI-koden eller i dokumentasjonen —
    # `const feil = "const M = [ … ] må stå én gang";` — ga et treff til, og
    # generatoren stoppet på en redeklarasjon nettleseren ikke ser. Det er en
    # falsk stopp, men en stopp: ferskhetsporten hadde blitt rød av en
    # tekstendring som ikke rører katalogen.
    #
    # Å skanne skriptet for hva som er kode ville betydd å skrive JS-leseren
    # fra `test_katalog.py` en gang til her, med skråstrek-tvetydigheten og alt
    # som fulgte med den. Det trengs ikke: samme regel som gjorde postene
    # strukturelle gjelder ankeret. En erklæring er fulgt av en LISTE av
    # modulposter — så tegnene `const M = [` i en streng eller en kommentar
    # faller på sin egen form, uten at generatoren trenger å vite hvor de står.
    # En HALV katalog nummer to er derimot fortsatt en erklæring, og stopper
    # generatoren som før.
    #
    # Med ett unntak: en TOM liste (Codex P2 på #118, sekstende runde). Formen
    # «hvert element er en post» er sann uten videre når det ikke står noen
    # elementer der, så `const hjelp = "const M = []";` ble en katalog nummer
    # to — enda en falsk stopp av samme slag som den forrige runde fjernet. Se
    # `er_en_modulliste()` for hvorfor det hullet ikke blir stående ulukket.
    #
    # Bare når ankeret finnes flere ganger må formen avgjøre; med ett anker
    # leses det som før, så en ekte katalog som har endret form fortsatt gir
    # den presise meldingen om HVA som er galt med den.
    ankre = list(KATALOG_RE.finditer(skript))
    if len(ankre) > 1:
        ankre = [m for m in ankre if er_en_modulliste(skript, m.end() - 1)]
    if len(ankre) != 1:
        raise SystemExit(
            f"fant {len(ankre)} modulkataloger i {KILDE_NAVN} — det skal være "
            f"nøyaktig én `const M = [ … ]`. Katalogen er kilden; finner ikke "
            f"generatoren den, eller finner den to, kan den ikke si hvilken "
            f"som gjelder.")
    poster = []
    for i in les_elementer(skript, ankre[0].end() - 1):
        m = POST_RE.match(skript, i)
        if not m:
            raise SystemExit(
                f"en modulpost i {KILDE_NAVN} mangler formen katalogen bærer: "
                f"`n`, `name`, `area` og `p` i den rekkefølgen. Posten "
                f"begynner med «{skript[i:i + 60]}».")
        feltnavn, _ = les_post(skript, i)
        if UKJENT_FELT in feltnavn:
            raise SystemExit(
                f"M-{m.group(1)} «{m.group(3)}» i {KILDE_NAVN} har en egenskap "
                f"generatoren ikke kan lese. Lovlig er `navn: literal` — "
                f"feltnavnet i bokstaver eller som fnuttstreng uten escape, og "
                f"verdien en streng, et tall, `true`/`false`/`null` eller en "
                f"liste eller et objekt av slike. Uleselig er blant annet en "
                f"BEREGNET nøkkel (`['status']:`), en nøkkel skrevet med "
                f"escape (`\\u0073tatus:`), spredning (`...o`), forkortelse "
                f"(`{{status}}`), metode og accessor. Katalogen er en kilde som "
                f"skal kunne leses av mer enn nettleseren; en egenskap som "
                f"først blir til når siden kjører kan hverken leses eller "
                f"forbys her.")
        # En egenskap skrevet TO ganger har to svar (Codex P2 på #118,
        # femtende runde). `{n:57, …, p:3, p:4}` er lovlig JS, og nettleseren
        # bruker den SISTE — mens `POST_RE` henter den første. Katalogen ville
        # da tegnet M-57 i fase 4 mens `katalog.js` sa fase 3, og
        # ferskhetsporten hadde vært grønn mot sitt eget utdata hele veien.
        # Å hente verdien strukturelt ville flyttet svaret fra det første til
        # det siste, og det er ikke bedre: en kilde skal ikke ha to svar å
        # velge mellom. Derfor stopper doblingen her, uansett hvilket felt det
        # gjelder — postene bærer få nok felt til at det aldri er en pris.
        doblet = sorted({f for f in feltnavn if feltnavn.count(f) > 1})
        if doblet:
            raise SystemExit(
                f"M-{m.group(1)} «{m.group(3)}» i {KILDE_NAVN} skriver "
                f"`{doblet[0]}` mer enn én gang. Nettleseren bruker den siste "
                f"verdien; en kilde kan ikke ha to svar på samme spørsmål. "
                f"Fjern den ene.")
        forbudt = [f for f in feltnavn if f in FORBUDTE_FELT]
        if forbudt:
            raise SystemExit(
                f"M-{m.group(1)} «{m.group(3)}» i {KILDE_NAVN} bærer "
                f"`{forbudt[0]}` — katalogen bærer struktur (nummer, "
                f"navn, område, fase), ikke tilstand. Hva en modul FAKTISK er, "
                f"står i `MODULSTATUS` i plattformdata.js, avledet av "
                f"manifestene. Fjern feltet.")
        poster.append(
            {"n": int(m.group(1)), "navn": m.group(3), "omrade": m.group(5),
             "fase": int(m.group(6))})
    # Antallet alene er ikke en kontroll (Codex P2): en duplisert `n` sammen
    # med en manglende modul gir også riktig antall poster, og da hadde
    # katalogen sett komplett ut mens én modul var borte og en annen sto to
    # ganger. Kravet er derfor at nummerSETTET er nøyaktig 1..ANTALL_MODULER.
    numre = [p["n"] for p in poster]
    duplikater = sorted({n for n in numre if numre.count(n) > 1})
    if duplikater:
        raise SystemExit(
            f"duplisert modulnummer i {KILDE_NAVN}: {duplikater}")
    forventet = set(range(1, ANTALL_MODULER + 1))
    if set(numre) != forventet:
        mangler = sorted(forventet - set(numre))
        ukjente = sorted(set(numre) - forventet)
        raise SystemExit(
            f"katalogen er ikke 1..{ANTALL_MODULER} — mangler: {mangler}, "
            f"ukjente: {ukjente} ({KILDE_NAVN} har endret form, sjekk "
            f"parseren)")
    return sorted(poster, key=lambda p: p["n"])


def main() -> None:
    katalog = les_katalog()
    omrader = sorted({p["omrade"] for p in katalog})
    manglende = [p["n"] for p in katalog if p["n"] not in MODUL_EN]
    if manglende:
        raise SystemExit(f"mangler engelsk navn for: {manglende}")
    # OVERSETTELSEN ER BUNDET TIL KILDENAVNET (Codex P2). Stemmer ikke navnet i
    # tabellen med navnet i spesifikasjonen, er modulen døpt om — og da er den
    # engelske teksten en oversettelse av noe som ikke lenger står der. Å la den
    # passere ville gitt en norsk og en engelsk katalog som sier hver sin ting,
    # helt stille: utdata er byte-identisk med seg selv, og nøkkelen er ikke tom.
    # Derfor stopper genereringen her til noen har oversatt det NYE navnet.
    dopt_om = [(p["n"], MODUL_EN[p["n"]][0], p["navn"]) for p in katalog
               if MODUL_EN[p["n"]][0] != p["navn"]]
    if dopt_om:
        rader = "\n".join(
            f"  M-{n}: «{gammelt}» → «{nytt}»" for n, gammelt, nytt in dopt_om)
        raise SystemExit(
            f"modulnavn er endret i {KILDE_NAVN}:\n{rader}\n"
            "oppdater MODUL_EN med det nye kildenavnet OG en oversettelse av "
            "det — modulnavn er produktnavn, og oversettelsen er en avgjørelse.")
    # Områdenavnene slår opp på navnet sitt, så en omdøping treffer her. Uten
    # denne linjen ville den kommet som en rå KeyError langt nede i skrivingen.
    ukjente_omrader = [o for o in omrader if o not in OMRADE_EN]
    if ukjente_omrader:
        raise SystemExit(
            f"mangler engelsk navn for område: {ukjente_omrader} — nytt eller "
            f"omdøpt område i {KILDE_NAVN}, legg det inn i OMRADE_EN.")

    # 1. Datafila: bare struktur, all tekst via locale-nøkler.
    linjer = [
        "// GENERERT av tools/gen_katalog.py fra",
        f"// {KILDE_NAVN} — IKKE rediger for hånd.",
        "//",
        f"// Modulkatalogen er produktomfanget: {len(katalog)} moduler i "
        f"{len(omrader)} områder over fire",
        "// faser. Den er OFFENTLIG informasjon (hva vi tilbyr), i motsetning til",
        "// tenantdata, som aldri skal ligge i en anonymt nedlastbar fil.",
        "//",
        "// Navnene ligger i locales/ som `site.katalog.m<n>.navn` og",
        "// `site.omrade.<slug>` — teksten er oversettelse, strukturen er data.",
        "",
        "export const KATALOG = [",
    ]
    for p in katalog:
        linjer.append(
            f'  {{ n: {p["n"]}, omrade: "{slug(p["omrade"])}", fase: {p["fase"]} }},')
    linjer += [
        "];",
        "",
        "// Områdene i fast rekkefølge, med modulene sine.",
        "export const OMRADER = [",
    ]
    for o in omrader:
        ns = [p["n"] for p in katalog if p["omrade"] == o]
        linjer.append(f'  {{ id: "{slug(o)}", moduler: {json.dumps(ns)} }},')
    linjer += [
        "];",
        "",
        "export const KATALOG_ANTALL = KATALOG.length;",
        "",
    ]
    (ROT / "platform/core/ui/static/js/katalog.js").write_text(
        "\n".join(linjer), encoding="utf-8")

    # 2. Locale-nøkler.
    for sprak in ("nb", "en"):
        sti = ROT / f"locales/{sprak}.json"
        d = json.loads(sti.read_text(encoding="utf-8"))
        for o in omrader:
            d[f"site.omrade.{slug(o)}"] = o if sprak == "nb" else OMRADE_EN[o]
        for p in katalog:
            d[f"site.katalog.m{p['n']}.navn"] = (
                p["navn"] if sprak == "nb" else MODUL_EN[p["n"]][1])
        sti.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    print(f"skrev katalog.js ({len(katalog)} moduler, {len(omrader)} områder)")
    print("locale-nøkler lagt inn i nb.json og en.json")


if __name__ == "__main__":
    main()
