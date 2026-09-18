"""M-25s fasitsett: seks prosjekter med KJENT dom, drevet gjennom dørene.

Prosjektregisteret har fem funntyper. Settet gir hver av dem nøyaktig ett
prosjekt, og ett prosjekt som skal være RENT:

  1. `milepael_over_frist`     — en unådd milepæl forbi tenantens frist
  2. `budsjett_overskredet`    — forbruk over budsjett + promille
  3. `betalingsplan_mangler`   — ingen milepæler i det hele tatt
  4. `ingen_arbeid_registrert` — stille lenger enn tenantens frist
  5. `ingen_terskel`           — egen tenant UTEN terskler (regelen
                                 gjelder tenanten, ikke prosjektet)
  6. (rent)                    — plan i rute, arbeid registrert, innenfor
                                 budsjett

FUNNTYPENE OVERLAPPER, og settet er bygget for å skille dem: hvert
prosjekt får nøyaktig ÉN av dem. Et prosjekt uten milepæler ville også
vært stille; et prosjekt som skal måles på budsjettet må derfor ha både
en plan i rute og ferskt arbeid.

Alt går gjennom modulens egne dører (`m25_sett_terskler`,
`m25_registrer_prosjekt`, `m25_sett_betalingsplan`, `m25_naa_milepael`,
`m25_registrer_arbeid`). Ingen rå DML i registerets tabeller: en rigg
som skriver forbi dørene måler riggen.

TO KJØRINGER, OG LUKKINGEN MÅLES PÅ EKTE (m19-formen): det rene
prosjektet fødes UTEN arbeid, med start 200 døgn tilbake, så første
sveip gir det `ingen_arbeid_registrert`. Arbeidet som gjør det rent
kommer MELLOM kjøringene, og andre sveip skal lukke funnet.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import sveipfasit_felles as felles

#: Tersklene settet måles mot — pinnet her, aldri utledet av kjøringen.
BUDSJETTVARSEL_PROMILLE = 100      # 10 % over budsjett før det er et funn
MILEPAEL_FRIST_DOGN = 7
STILLHET_DOGN = 30

AKTOR = "m25-fasit"
#: `kilde`-verdien modulens dører skriver i evidenskjeden.
EVIDENSKILDE = "m25_prosjekt"
#: Funntabellen og subjektkolonnen. Navnene står ÉN gang, her, og
#: leses av det delte maskineriet: en kopi der det ene ble rettet og
#: det andre ikke, ville lest et ANNET register enn modulen skriver i
#: — og talt null funn uten å si fra.
FUNNTABELL = "prosjektfunn"
SUBJEKTKOLONNE = "prosjekt_id"

#: Ingen funntype er unåbar for dette settet: alle dørene tar datoer.
UNAABARE: dict[str, str] = {}

#: (merkelapp, forventet funntype eller None, budsjett_ore,
#:  start for N døgn siden, milepæler [(navn, døgn fra i dag, beløp)],
#:  arbeid [(døgn siden, minutter, kostnad_ore)])
SETT: tuple[tuple[str, str | None, int, int, list, list], ...] = (
    # Milepælen er 30 døgn forbi planlagt dato og ikke nådd. Arbeid er
    # ferskt og forbruket langt under budsjett, så bare fristen står
    # igjen.
    ("forsinket", "milepael_over_frist", 1_000_000, 100,
     [("Levering", -30, 500_000)], [(0, 60, 10_000)]),
    # Forbruket er 20 % over budsjettet, godt over promillen. Milepælen
    # ligger fram i tid, og arbeidet er ferskt.
    ("over_budsjett", "budsjett_overskredet", 1_000_000, 100,
     [("Levering", 60, 500_000)], [(0, 600, 1_200_000)]),
    # INGEN milepæler. Arbeidet er ferskt, så stillheten ikke også slår
    # inn, og forbruket er under budsjett.
    ("uten_plan", "betalingsplan_mangler", 1_000_000, 100,
     [], [(0, 60, 10_000)]),
    # Stille i 200 døgn. Milepælen ligger fram i tid, så fristen ikke
    # også slår inn.
    ("stille", "ingen_arbeid_registrert", 1_000_000, 200,
     [("Levering", 60, 500_000)], []),
    # Det RENE prosjektet fødes UTEN arbeid og med start 200 døgn
    # tilbake: funnet det får i første sveip er nettopp det andre sveip
    # skal lukke.
    ("rent", None, 1_000_000, 200,
     [("Levering", 60, 500_000)], []),
)

#: Prosjektet i tenanten UTEN terskler — hele tenanten er funnet.
UTEN_TERSKEL = ("ingen_terskel", "ingen_terskel", 1_000_000, 100,
                [("Levering", 60, 500_000)], [(0, 60, 10_000)])

#: Arbeidet som gjør det rene prosjektet rent, registrert MELLOM sveipene.
RENT_ARBEID = (0, 60, 10_000)

#: SUBJEKTET SOM RENSES mellom de to sveipene. Navnet står her, ikke i
#: den generiske produsenten: modulene kaller det ikke det samme, og en
#: hardkodet merkelapp leste bare null.
RENSES = "rent"

#: INGEN NEGATIV KONTROLL HER. M-27 har en vare som er ren fra fødselen
#: (nådefristen for et manglende bestillingspunkt); M-25s register har
#: ingen tilsvarende regel, og et oppdiktet subjekt ville målt riggen.


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m25fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m25-fasit")


def lag_prosjekt(rt, tenant: str, merke: str, budsjett: int, start_siden: int,
                 milepaeler: list, arbeid: list) -> dict:
    """Ett prosjekt med plan og arbeid — gjennom dørene."""
    pid = uuid.uuid4()
    _sk(rt, tenant)
    rt.execute(
        "SELECT m25_registrer_prosjekt(%s,%s,%s,%s,%s,%s,"
        " current_date - %s, current_date + 365, %s)",
        (tenant, pid, f"kunde-{merke}", f"Fasit {merke}", f"kontrakt-{merke}",
         budsjett, start_siden, AKTOR))
    rt.commit()
    if milepaeler:
        # DATOENE REGNES I BASEN, ikke i Python. `date.today()` er
        # maskinens LOKALE dag, mens sveipen regner `current_date` i
        # basens sone — og en rigg som blandet de to ville rigget feil
        # antall døgn hver gang de to sto fra hverandre. Rekkefølgen
        # holdes med `WITH ORDINALITY`: døra nummererer milepælene etter
        # posisjon i arrayet.
        _sk(rt, tenant)
        rt.execute(
            "SELECT m25_sett_betalingsplan(%s,%s,"
            " (SELECT jsonb_agg(jsonb_build_object("
            "     'navn', e->>'navn',"
            "     'belop_ore', (e->>'belop_ore')::bigint,"
            "     'planlagt_dato', (current_date + (e->>'dogn')::int)::text)"
            "   ORDER BY i)"
            "  FROM jsonb_array_elements(%s::jsonb) WITH ORDINALITY AS u(e, i)),"
            " %s)",
            (tenant, pid,
             json.dumps([{"navn": navn, "belop_ore": belop, "dogn": dogn}
                         for navn, dogn, belop in milepaeler]), AKTOR))
        rt.commit()
    for siden, minutter, kostnad in arbeid:
        _sk(rt, tenant)
        rt.execute(
            "SELECT m25_registrer_arbeid(%s,%s,%s, current_date - %s,"
            " %s,%s,%s,%s)",
            (tenant, uuid.uuid4(), pid, siden, minutter, kostnad,
             f"fasitarbeid {merke}", AKTOR))
        rt.commit()
    return {"merke": merke, "subjekt_id": str(pid)}


def forbered(rt, runde: str) -> dict:
    """Begge tenantene, med og uten terskler — men UTEN det rene
    prosjektets arbeid: det kommer mellom kjøringene."""
    med = tenantnavn(runde, "med_terskel")
    uten = tenantnavn(runde, "uten_terskel")
    _sk(rt, med)
    rt.execute("SELECT m25_sett_terskler(%s,%s,%s,%s,%s)",
               (med, BUDSJETTVARSEL_PROMILLE, MILEPAEL_FRIST_DOGN,
                STILLHET_DOGN, AKTOR))
    rt.commit()
    rigg = {"med_terskel": med, "uten_terskel": uten, "subjekter": {}}
    for merke, _v, budsjett, start, milepaeler, arbeid in SETT:
        rigg["subjekter"][merke] = lag_prosjekt(
            rt, med, merke, budsjett, start, milepaeler, arbeid)
    merke, _v, budsjett, start, milepaeler, arbeid = UTEN_TERSKEL
    rigg["subjekter"][merke] = lag_prosjekt(
        rt, uten, merke, budsjett, start, milepaeler, arbeid)
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["med_terskel"], rigg["uten_terskel"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Arbeidet som gjør det rene prosjektet rent — MELLOM sveipene."""
    siden, minutter, kostnad = RENT_ARBEID
    pid = rigg["subjekter"][RENSES]["subjekt_id"]
    _sk(rt, rigg["med_terskel"])
    rt.execute(
        "SELECT m25_registrer_arbeid(%s,%s,%s, current_date - %s,"
        " %s,%s,%s,%s)",
        (rigg["med_terskel"], uuid.uuid4(), pid, siden, minutter, kostnad,
         "fasit: gjør prosjektet rent", AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _b, _s, _mp, _a in SETT}
    ut[UTEN_TERSKEL[0]] = UTEN_TERSKEL[1]
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader — aldri av riktig antall."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
