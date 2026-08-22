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
import subprocess
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

# Katalogen bærer STRUKTUR (nummer, navn, område, fase) — ikke tilstand.
# Et eget statusfelt her sto kort i #109 og er tatt ut igjen (Codex P1): to
# statuskilder for samme modul kan bare drive fra hverandre, og den ene som
# ikke er forankret i et manifest kan love drift ingen port har bestått.
# Hva en modul FAKTISK er, står ett sted: `MODULSTATUS` i plattformdata.js,
# avledet av manifestene og pinnet av test_ui_kontrakt.py.
#
# Forbudet gjelder MODULPOSTENE — ikke alt som står i en `<script>`-tagg.
# Siden er en levende prototype med egen UI-kode: et tilstandsobjekt med
# `status:` i en filterrutine, et API-eksempel i en hjelpetekst — alt sammen
# helt lovlig, og ingen av delene en tilstandsakse ved siden av manifestet.
# Med `les_katalog.mjs` er avgrensningen ikke lenger en regel noen måtte
# skrive: leseren gir fra seg postene i `M`, og bare dem.
FORBUDTE_FELT = ("status", "driftstilstand")

# Feltene en modulpost MÅ bære, og typen de har.
PAAKREVD = {"n": int, "name": str, "area": str, "p": int}

# Merkelappen `les_katalog.mjs` setter på en feltverdi som ikke er DATA — en
# funksjon, et mønster, en dato. Se der.
IKKE_DATA = "__ikke_data__"

# Leseren. ETT lesersteg, delt med porten i `platform/core/tests/test_katalog.py`
# (eiers beslutning 20/8 på PR #118).
#
# Katalogen sto i JavaScript, og både generatoren og porten leste den med hver
# sin håndskrevne skanner i Python. Nitten review-runder på #118 var nitten
# JavaScript-former skannerne ikke hadde — en beregnet nøkkel, en escapet
# nøkkel, en spredning, en accessor, en malstreng, en skråstrek som både er
# divisjon og mønster … Formene tar aldri slutt, for mengden er hele
# grammatikken, og en skanner som ikke kjenner en form gjør ikke noe høylytt:
# den leser noe annet enn nettleseren og sier ingenting.
#
# Nå leses katalogen av en JavaScript-motor, som per konstruksjon ikke kan ha
# en annen forestilling om JavaScript enn nettleseren har. Og porten leser den
# gjennom SAMME fil — to lesninger av samme kilde var ment å gjøre en feil i
# den ene synlig, men i praksis ga det to skannere som drev fra hverandre og
# måtte lappes hver for seg. Én leser kan ikke drive fra seg selv.
LESER = pathlib.Path(__file__).resolve().parent / "les_katalog.mjs"


def ikke_data_i(verdi) -> str | None:
    """Hva som ikke er data i `verdi`, eller `None` — HELE VEIEN NED.

    Merket ble bare lett etter i feltets egen verdi (Codex P2). Katalogen
    tillater lister og objekter av data, og leseren merker det som ikke er data
    DER DET STÅR — så en `flow: [() => 42]` fikk merket sitt inne i lista, og
    et felt med en funksjon i seg gikk rett gjennom kontrollen. Lovlig er tekst,
    tall, `true`/`false`/`null` og lister og objekter av slike, rekursivt, og da
    må kontrollen være det samme.
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
        funn = ikke_data_i(v)
        if funn is not None:
            return funn
    return None


def slug(navn: str) -> str:
    tegn = {"æ": "ae", "ø": "o", "å": "a", " ": "_"}
    ut = "".join(tegn.get(c, c) for c in navn.lower())
    return re.sub(r"[^a-z0-9_]", "", ut)


def les_katalog() -> list[dict]:
    """Modulpostene i sannhetskilden, lest av `les_katalog.mjs`."""
    if not KILDE.exists():
        raise SystemExit(f"fant ikke sannhetskilden: {KILDE_NAVN}")
    try:
        r = subprocess.run(["node", str(LESER), str(KILDE)],
                           capture_output=True, text=True)
    except FileNotFoundError:
        # FAIL-CLOSED. Uten node er katalogen ulest, og en generator som da
        # skrev noe som helst ville skrevet gammelt innhold over et nytt
        # produktomfang. Ubuntu-runneren har node preinstallert, og UI-jobben
        # krever den alt.
        raise SystemExit(
            "fant ikke `node` — modulkatalogen leses av tools/les_katalog.mjs, "
            "og uten en JavaScript-motor kan den ikke leses i det hele tatt.")
    if r.returncode != 0:
        raise SystemExit(
            f"kunne ikke lese modulkatalogen i {KILDE_NAVN}:\n{r.stderr.strip()}")
    poster = json.loads(r.stdout)["moduler"]

    for i, post in enumerate(poster, start=1):
        # Navnet en post kalles ved i en feilmelding. `n` er det leseren ga oss,
        # og er den ikke lesbar, er plassen i lista det eneste vi har.
        hvem = f"M-{post['n']}" if isinstance(post.get("n"), int) \
            else f"post {i} i katalogen"
        if isinstance(post.get("name"), str):
            hvem += f" «{post['name']}»"
        # Forbudet FØRST: et `status`-felt er forbudt uansett hva verdien er,
        # og meldingen skal navngi aksen, ikke formen den var skrevet i.
        forbudt = [f for f in post if f in FORBUDTE_FELT]
        if forbudt:
            raise SystemExit(
                f"{hvem} i {KILDE_NAVN} bærer `{forbudt[0]}` — katalogen bærer "
                f"struktur (nummer, navn, område, fase), ikke tilstand. Hva en "
                f"modul FAKTISK er, står i `MODULSTATUS` i plattformdata.js, "
                f"avledet av manifestene. Fjern feltet.")
        uleselige = sorted(f for f, v in post.items()
                           if ikke_data_i(v) is not None)
        if uleselige:
            raise SystemExit(
                f"{hvem} i {KILDE_NAVN} har egenskapen `{uleselige[0]}` som "
                f"ikke er data ({ikke_data_i(post[uleselige[0]])}). Katalogen er "
                f"en kilde som skal kunne leses av mer enn nettleseren: lovlig "
                f"er tekst, tall, `true`/`false`/`null` og lister og objekter "
                f"av slike.")
        for felt, slag in PAAKREVD.items():
            # `bool` er en `int` i Python; et `p: true` er ikke en fase.
            if not isinstance(post.get(felt), slag) \
                    or isinstance(post.get(felt), bool):
                raise SystemExit(
                    f"{hvem} i {KILDE_NAVN} mangler formen katalogen bærer: "
                    f"`{felt}` skal være {'et tall' if slag is int else 'tekst'}"
                    f", ikke {post.get(felt)!r}.")

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
            f"leseren)")
    return sorted(
        ({"n": p["n"], "navn": p["name"], "omrade": p["area"], "fase": p["p"]}
         for p in poster), key=lambda p: p["n"])



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
