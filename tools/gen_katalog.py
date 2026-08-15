#!/usr/bin/env python3
"""Generer modulkatalogen for forsiden fra spesifikasjonen — én kilde, ikke avskrift.

Katalogen (45 moduler, 11 områder, 4 faser) lever i
`docs/spesifikasjon/disponit-prototype-v7.html`. Å taste den inn på nytt ville
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
# `docs/spesifikasjon/disponit-prototype-v7.html` (v7.2) som sannhetskilden, og
# `docs/STRUKTUR.md` kaller `prototype/` et historisk arkiv som ALDRI endres.
# De to filene gir identisk katalog i dag, så feilen ga ingen synlig forskjell —
# den var stille: neste kanoniske modul-, område- eller faseendring ville ikke
# nådd generatoren, og en ny kjøring ville reprodusert gammelt offentlig innhold
# uten at noe klaget. Et arkiv som aldri endres kan per definisjon ikke være
# inndata til noe som skal følge produktet.
KILDE = ROT / "docs" / "spesifikasjon" / "disponit-prototype-v7.html"
KILDE_NAVN = "docs/spesifikasjon/disponit-prototype-v7.html"

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
}


def slug(navn: str) -> str:
    tegn = {"æ": "ae", "ø": "o", "å": "a", " ": "_"}
    ut = "".join(tegn.get(c, c) for c in navn.lower())
    return re.sub(r"[^a-z0-9_]", "", ut)


def les_katalog() -> list[dict]:
    if not KILDE.exists():
        raise SystemExit(f"fant ikke sannhetskilden: {KILDE_NAVN}")
    skript = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>",
                                  KILDE.read_text(encoding="utf-8",
                                                  errors="replace"), re.S))
    poster = [
        {"n": int(m.group(1)), "navn": m.group(2), "omrade": m.group(4),
         "fase": int(m.group(5))}
        for m in re.finditer(
            r"\{n:(\d+),name:'([^']*)'(.*?),area:'([^']*)',p:(\d+)", skript)
    ]
    # Antallet alene er ikke en kontroll (Codex P2): en duplisert `n` sammen
    # med en manglende modul gir også 45 poster, og da hadde katalogen sett
    # komplett ut mens én modul var borte og en annen sto to ganger. Kravet er
    # derfor at nummerSETTET er nøyaktig 1..45.
    numre = [p["n"] for p in poster]
    duplikater = sorted({n for n in numre if numre.count(n) > 1})
    if duplikater:
        raise SystemExit(
            f"duplisert modulnummer i {KILDE_NAVN}: {duplikater}")
    forventet = set(range(1, 46))
    if set(numre) != forventet:
        mangler = sorted(forventet - set(numre))
        ukjente = sorted(set(numre) - forventet)
        raise SystemExit(
            f"katalogen er ikke 1..45 — mangler: {mangler}, ukjente: {ukjente}"
            f" ({KILDE_NAVN} har endret form, sjekk parseren)")
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
        "// Modulkatalogen er produktomfanget: 45 moduler i 11 områder over fire",
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
