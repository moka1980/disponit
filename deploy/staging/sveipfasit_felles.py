"""Den delte halvdelen av en sveipmoduls fasitrigg.

Hver rigg består av to ting: SETTET — hvilke subjekter, med hvilken
kjent dom, gjennom hvilke dører — og MASKINERIET som teller opp
resultatet. Settet er modulens eget og kan ikke deles. Maskineriet er
identisk, og var det i tre kopier før denne filen fantes.

Kopiene var ikke bare sløsing. `apne_funn` navngir BÅDE funntabellen og
subjektkolonnen, og en kopi der det ene ble rettet og det andre ikke,
ville lest et annet register enn det modulen skriver i — og talt null
funn uten å si fra. Navnene står derfor ÉN gang, i modulens egen
oppføring, og leses herfra.

Riggen beholder selv `forbered`, `kontroller_ren`, `riggtenanter`,
`forventet`, `sett_sha256` og konstantene: det er dem som sier hva
settet ER.
"""
from __future__ import annotations


def sett_kontekst(conn, tenant: str, aktor: str, request_id: str) -> None:
    """Tenantkonteksten dørene krever. Én form, så en rigg ikke kan
    glemme aktøren og få en evidenskjede uten navn på."""
    conn.execute("SELECT set_config('disponit.tenant', %s, true),"
                 " set_config('disponit.aktor', %s, true),"
                 " set_config('disponit.request_id', %s, true)",
                 (tenant, aktor, request_id))


def apne_funn(m, tenant: str, *, funntabell: str, subjektkolonne: str,
              aktor: str) -> dict[str, list[str]]:
    """subjekt → åpne funntyper, lest med MIGRATORENS tilkobling.

    Sveiperollen har bare EXECUTE på sveipedøra, og runtime har ingen
    lesevei inn i funntabellen i det hele tatt: lesingen er en MÅLING av
    registeret, ikke en del av driftsveien."""
    sett_kontekst(m, tenant, aktor, "fasit")
    rader = m.execute(
        f"SELECT {subjektkolonne}::text, funntype FROM {funntabell}"
        " WHERE tenant=%s AND apen ORDER BY 1,2", (tenant,)).fetchall()
    m.rollback()
    ut: dict[str, list[str]] = {}
    for sid, funntype in rader:
        ut.setdefault(sid, []).append(funntype)
    return ut


def maal(m, rigg: dict, *, funntabell: str, subjektkolonne: str,
         forventet: dict, tenanter: list[str], aktor: str) -> dict:
    """Dommen, RE-REGNET av registerets rader — aldri av riktig antall.

    Et subjekt består bare hvis det fikk NØYAKTIG den ene funntypen
    fasiten sier, eller ingen når fasiten sier ingen. En liste som
    inneholder den riktige typen OG en til er et avvik."""
    apne: dict[str, list[str]] = {}
    for tenant in tenanter:
        apne.update(apne_funn(m, tenant, funntabell=funntabell,
                              subjektkolonne=subjektkolonne, aktor=aktor))
    avvik: list[str] = []
    per: list[dict] = []
    for merke, ventet in forventet.items():
        sid = rigg["subjekter"][merke]["subjekt_id"]
        fikk = sorted(apne.get(sid, []))
        ok = (fikk == [ventet]) if ventet else (fikk == [])
        if not ok:
            avvik.append(f"{merke}: ventet {ventet or 'ingen funn'},"
                         f" fikk {fikk or 'ingen'}")
        per.append({"merke": merke, "ventet": ventet, "fikk": fikk,
                    "subjekt_id": sid})
    return {"per_subjekt": per, "avvik": avvik}
