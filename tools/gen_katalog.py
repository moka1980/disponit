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
POST_RE = re.compile(
    r"""\{\s*["']?n["']?\s*:\s*(\d+)\s*,"""
    r"""\s*["']?name["']?\s*:\s*(['"])(.*?)\2"""
    r"""[^{}]*?,\s*["']?area["']?\s*:\s*(['"])(.*?)\4"""
    r"""\s*,\s*["']?p["']?\s*:\s*(\d+)""",
    re.S)

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


def postslutt(tekst: str, start: int) -> int:
    """Indeksen rett ETTER krøllparentesen som lukker posten som åpner i `start`.

    Å lete etter første `}` holder bare så lenge ingen feltverdi inneholder
    en krøllparentes, og det er ingenting som sier at de ikke kan det: `guard`,
    `input` og `accept` er fri prosa, og et JSON- eller malfragment i en av dem
    ville flyttet postslutten inn i midten av en streng. Derfor teller vi dybde
    og hopper over strenger — enkelt- og dobbeltfnutt og backtick, med `\\` som
    escape. En `${...}` i en backtick-streng går opp i opp: åpnings- og
    lukkeparentesen ligger begge inne i strengen og telles ingen av dem.

    Uterminert post er ikke en tom mengde treff — det er en kilde som har
    endret form, og da er katalogen ikke lenger lesbar. Da stopper vi.
    """
    dybde = 0
    i = start
    while i < len(tekst):
        c = tekst[i]
        if c in "\"'`":
            i += 1
            while i < len(tekst) and tekst[i] != c:
                i += 2 if tekst[i] == "\\" else 1
        elif c == "{":
            dybde += 1
        elif c == "}":
            dybde -= 1
            if dybde == 0:
                return i + 1
        i += 1
    raise SystemExit(
        f"modulpost i {KILDE_NAVN} lukkes aldri (fra tegn {start}) — "
        f"kilden har endret form, sjekk parseren")


_NAVNETEGN = re.compile(r"[A-Za-z_$][\w$]*")


def toppnivafelt(post: str) -> list[str]:
    """Feltnavnene på ØVERSTE nivå i en modulpost, i den rekkefølgen de står.

    Skanner som `postslutt()`: teller dybde og hopper over strenger. Et navn
    telles bare når det står rett inne i postens egen krøllparentes — ikke i en
    liste, ikke i et nøstet objekt — og etterfølges av kolon.

    Forbudet mot en parallell statusakse (Codex P2 på #118, sjette runde) leste
    før dette rått i posten med et mønster på «`{` eller komma, så navnet, så
    kolon». Nøkkelposisjon er riktig krav, men et regex ser ikke forskjell på
    kode og tekst: en feltverdi som dokumenterer et JSON- eller API-fragment —
    `input: "Svar: {status: ok}"` — treffer mønsteret inne i fnuttene, og
    generatoren stoppet med en modulfeilmelding om en tilstandsakse modulen
    ikke har. Det er samme klasse feil som postgrensen hadde, og desto mer
    sannsynlig nå som `postslutt()` med vilje tillater krøllparenteser i prosa.

    Motsatt vei holder posisjonskravet fortsatt: ordet «status» står overalt i
    feltVERDIene — «samtykkestatus», «onboardingstatus», «resultatstatus» — og
    det er prosa om hva en modul gjør. Bare et FELT er en tilstandsakse.
    """
    felt = []
    dybde = 0
    klammer = 0
    i = 0
    while i < len(post):
        c = post[i]
        if c in "\"'`":
            j = i + 1
            while j < len(post) and post[j] != c:
                j += 2 if post[j] == "\\" else 1
            navn = post[i + 1:j]
            i = j + 1
        elif c == "{":
            dybde += 1
            i += 1
            continue
        elif c == "}":
            dybde -= 1
            i += 1
            continue
        elif c == "[":
            klammer += 1
            i += 1
            continue
        elif c == "]":
            klammer -= 1
            i += 1
            continue
        elif (treff := _NAVNETEGN.match(post, i)):
            navn = treff.group(0)
            i = treff.end()
        else:
            i += 1
            continue
        # Et navn er et felt bare på postens eget nivå, og bare foran kolon.
        if dybde == 1 and klammer == 0:
            etter = i
            while etter < len(post) and post[etter].isspace():
                etter += 1
            if etter < len(post) and post[etter] == ":":
                felt.append(navn)
    return felt


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
    # Postgrensen leses med `postslutt()`, som kjenner strenger (Codex P2 på
    # #118, femte runde). Den var «første `}` etter treffet», med begrunnelsen
    # at ingen krøllparentes finnes inne i feltverdiene. Det er sant i dag og
    # ingen regel: `guard`, `input` og `accept` er fri prosa, og et JSON- eller
    # malfragment i en av dem — `{}`, `${sak}` — hadde kuttet posten der, midt
    # inne i en streng. Alt etter kuttet, inkludert et `status`-felt, falt
    # utenfor det forbudet fikk se. Og nummerporten under fanger det ikke:
    # POST_RE er ferdig med å matche ved `p`, så posten telles som normalt.
    #
    # Innenfor posten leses FELTNAVNENE, ikke teksten (Codex P2, sjette runde):
    # `toppnivafelt()` hopper over strenger på samme måte, så et JSON-fragment
    # i en fri feltverdi ikke lenger kan se ut som en tilstandsakse.
    poster = []
    for m in POST_RE.finditer(skript):
        post = skript[m.start():postslutt(skript, m.start())]
        forbudt = [f for f in toppnivafelt(post) if f in FORBUDTE_FELT]
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
