#!/usr/bin/env python3
"""Generer modulkatalogen for forsiden fra prototypen — én kilde, ikke avskrift.

Katalogen (45 moduler, 11 områder, 4 faser) lever i
`prototype/AI-bedriftsagent-prototype-v7.html`. Å taste den inn på nytt ville
gitt to sannheter som driver fra hverandre; dette skriptet leser prototypen og
skriver ut både datafila og locale-nøklene, så en endring i katalogen bare
krever en ny kjøring.

Engelske navn er OVERSATT her, ikke maskinelt: modulnavn er produktnavn, og en
maskinoversettelse av «Kundefordringsagent» blir ikke «Accounts receivable
agent» av seg selv.
"""
import json
import pathlib
import re
import sys

ROT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
PROTOTYPE = pathlib.Path("/home/moka/prosjekt/disponit/prototype/"
                         "AI-bedriftsagent-prototype-v7.html")

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

# Modulnavn på engelsk, per modulnummer.
MODUL_EN = {
    1: "Policy and authority engine", 2: "Audit log and evidence",
    3: "Data quality agent", 4: "Data and file manager",
    5: "Document and template agent", 6: "Email operations agent",
    7: "Meeting operations agent", 8: "Calendar and capacity agent",
    9: "Knowledge and glossary agent", 10: "Backup and recovery agent",
    11: "Integration and self-test agent", 12: "Identity and access agent (JML)",
    13: "Bank reconciliation agent", 14: "Invoice and expense agent",
    15: "Liquidity and cost agent", 16: "Reporting and KPI agent",
    17: "Customer service agent", 18: "Customer onboarding agent",
    19: "Customer health and renewal agent", 20: "Website and content agent",
    21: "Contract and deadline agent", 22: "SaaS and licence agent",
    23: "Accounts receivable agent", 24: "Supplier and purchasing agent",
    25: "Order-to-cash agent", 26: "Quote and pricing agent",
    27: "Inventory and replenishment agent", 28: "Logistics and transport agent",
    29: "Security and incident agent", 30: "Privacy and data subject agent",
    31: "Agent quality and model governance", 32: "Global localisation and tax agent",
    33: "Prediction and scenario agent", 34: "Compliance and certification agent",
    35: "Crisis and continuity agent", 36: "Business optimiser",
    37: "Exception and error handling agent", 38: "Capacity, queue and model router",
    39: "Payroll basis agent", 40: "HR and employee agent",
    41: "Subscription and revenue agent", 42: "Fraud and transaction guard",
    43: "Voice and telephony agent", 44: "Campaign and market insight agent",
    45: "Sustainability and ESG agent",
}


def slug(navn: str) -> str:
    tegn = {"æ": "ae", "ø": "o", "å": "a", " ": "_"}
    ut = "".join(tegn.get(c, c) for c in navn.lower())
    return re.sub(r"[^a-z0-9_]", "", ut)


def les_katalog() -> list[dict]:
    skript = "\n".join(re.findall(r"<script[^>]*>(.*?)</script>",
                                  PROTOTYPE.read_text(encoding="utf-8",
                                                      errors="replace"), re.S))
    poster = [
        {"n": int(m.group(1)), "navn": m.group(2), "omrade": m.group(4),
         "fase": int(m.group(5))}
        for m in re.finditer(
            r"\{n:(\d+),name:'([^']*)'(.*?),area:'([^']*)',p:(\d+)", skript)
    ]
    if len(poster) != 45:
        raise SystemExit(f"forventet 45 moduler, fant {len(poster)} — "
                         f"prototypen har endret form, sjekk parseren")
    return sorted(poster, key=lambda p: p["n"])


def main() -> None:
    katalog = les_katalog()
    omrader = sorted({p["omrade"] for p in katalog})
    manglende = [p["n"] for p in katalog if p["n"] not in MODUL_EN]
    if manglende:
        raise SystemExit(f"mangler engelsk navn for: {manglende}")

    # 1. Datafila: bare struktur, all tekst via locale-nøkler.
    linjer = [
        "// GENERERT av tools/gen_katalog.py fra",
        "// prototype/AI-bedriftsagent-prototype-v7.html — IKKE rediger for hånd.",
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
                p["navn"] if sprak == "nb" else MODUL_EN[p["n"]])
        sti.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")

    print(f"skrev katalog.js ({len(katalog)} moduler, {len(omrader)} områder)")
    print("locale-nøkler lagt inn i nb.json og en.json")


if __name__ == "__main__":
    main()
