"""M-19s fasitsett: seks subjekter med KJENT dom, drevet gjennom dørene.

Adressekontrollen har fem funntyper og én ren tilstand. Settet gir hver
av dem nøyaktig ett subjekt, slik at dommen er avlesbar per rad — ikke et
aggregat som kan stemme av feil grunner:

  1. `ukontrollert_adresse`   — adresse eldre enn kravets døgn, aldri kontrollert
  2. `kontroll_utlopt`        — godkjent metode, men kontrollen er for gammel
  3. `avvist_adresse`         — siste kontroll endte `avvist`
  4. `utilstrekkelig_metode`  — godkjent utfall, men metoden står ikke i kravet
  5. `ingen_krav`             — egen tenant UTEN krav (regelen gjelder tenanten)
  6. (ren)                    — nylig godkjent med godkjent metode → ingen funn

Alt går gjennom modulens egne dører (`m19_sett_krav`,
`m19_registrer_subjekt`, `m19_registrer_adresse`, `m19_registrer_kontroll`)
og sveipen kalles med sin egen funksjon uten tenantkontekst, som
arbeideren gjør. Ingen rå DML i registerets tabeller: en rigg som skriver
forbi dørene måler riggen.

To kjøringer, og LUKKINGEN måles på ekte (CodeRabbit): det rene
subjektet registreres UTEN kontroll, så første sveip gir det et
`ukontrollert_adresse`-funn som alle de andre. Kontrollen kommer MELLOM
kjøringene, og andre sveip skal da lukke funnet — kandidaten er borte.
Samtidig skal andre kjøring gi NULL nye: en sveip som finner det samme
på nytt hver runde fyller køen med duplikater av samme sannhet.

Fasiten dømmes derfor på tilstanden ETTER andre kjøring. Sto kontrollen
inne fra starten, ville `lukkede` alltid vært null, og aksen hadde vært
en påstand i en dokumentstreng.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

#: Kravet settet måles mot — pinnet her, aldri utledet av kjøringen.
#: Metodene er registerets lukkede mengde (112): `visuell`,
#: `bekreftet_av_kunde`, `dokumentert`, `levering_bekreftet`. To av dem
#: er godkjente, så `utilstrekkelig_metode` kan måles med en ekte metode
#: som bare ikke står i kravet — ikke med en oppdiktet.
UKONTROLLERT_DOGN = 30
GYLDIG_DOGN = 180
GODKJENTE_METODER = ["dokumentert", "levering_bekreftet"]

#: (merkelapp, forventet funntype eller None, dager siden adressen gjaldt,
#:  kontroll: (metode, utfall, dager siden) eller None)
SETT: tuple[tuple[str, str | None, int, tuple[str, str, int] | None], ...] = (
    ("ukontrollert", "ukontrollert_adresse", 90, None),
    ("utlopt", "kontroll_utlopt", 400, ("dokumentert", "godkjent", 200)),
    ("avvist", "avvist_adresse", 90, ("dokumentert", "avvist", 5)),
    ("feil_metode", "utilstrekkelig_metode", 90, ("visuell", "godkjent", 5)),
    ("ren", None, 90, ("levering_bekreftet", "godkjent", 5)),
)

#: Subjektet i tenanten UTEN krav — hele tenanten er funnet.
UTEN_KRAV = ("ingen_krav", "ingen_krav", 90, None)

AKTOR = "m19-fasit"


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen): to
    kjøringer som mener de driver samme sett, men ikke gjør det, skal
    ikke kunne kalles like."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m19fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    conn.execute("SELECT set_config('disponit.tenant', %s, true),"
                 " set_config('disponit.aktor', %s, true),"
                 " set_config('disponit.request_id', %s, true)",
                 (tenant, AKTOR, "m19-fasit"))


def lag_subjekt(rt, tenant: str, merke: str, dager: int,
                kontroll: tuple[str, str, int] | None) -> dict:
    """Ett subjekt med adresse (og evt. kontroll) gjennom dørene."""
    sid, vid = uuid.uuid4(), uuid.uuid4()
    _sk(rt, tenant)
    rt.execute("SELECT m19_registrer_subjekt(%s,%s,%s,%s,%s)",
               (tenant, sid, f"ref-{merke}", f"Fasit {merke}", AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute(
        "SELECT m19_registrer_adresse(%s,%s,%s,%s,NULL,%s,%s,'NO',"
        " 'import',%s, current_date - %s, %s, %s)",
        (tenant, vid, sid, f"  Fasitveien 1{merke[:1]}  ", "0150", "Oslo",
         f"kilde-{merke}", dager, "fasitrad", AKTOR))
    rt.commit()
    kid = None
    if kontroll is not None:
        metode, utfall, siden = kontroll
        kid = uuid.uuid4()
        _sk(rt, tenant)
        rt.execute(
            "SELECT m19_registrer_kontroll(%s,%s,%s,%s,%s,%s,%s,%s,"
            " current_date - %s, %s)",
            (tenant, kid, vid, metode, utfall, "kontrollor@fasit",
             f"kref-{merke}", "fasit", siden, AKTOR))
        rt.commit()
    return {"merke": merke, "subjekt_id": str(sid), "versjon_id": str(vid),
            "kontroll_id": str(kid) if kid else None}


def forbered(rt, runde: str) -> dict:
    """Begge tenantene, med og uten krav — men UTEN det rene subjektets
    kontroll: den registreres mellom kjøringene (`kontroller_ren`), så
    lukkingen kan måles. -> riggen."""
    med = tenantnavn(runde, "med_krav")
    uten = tenantnavn(runde, "uten_krav")
    _sk(rt, med)
    rt.execute("SELECT m19_sett_krav(%s,%s,%s,%s,%s)",
               (med, UKONTROLLERT_DOGN, GYLDIG_DOGN, GODKJENTE_METODER, AKTOR))
    rt.commit()
    rigg = {"med_krav": med, "uten_krav": uten, "subjekter": {}}
    for merke, ventet, dager, kontroll in SETT:
        # Det RENE subjektet fødes ukontrollert: funnet det får i første
        # sveip er nettopp det andre sveip skal lukke.
        rigg["subjekter"][merke] = lag_subjekt(
            rt, med, merke, dager, None if ventet is None else kontroll)
    merke, _v, dager, kontroll = UTEN_KRAV
    rigg["subjekter"][merke] = lag_subjekt(rt, uten, merke, dager, kontroll)
    return rigg


def kontroller_ren(rt, rigg: dict) -> None:
    """Det rene subjektets kontroll — MELLOM de to sveipene."""
    merke, _v, _d, kontroll = next(r for r in SETT if r[1] is None)
    metode, utfall, siden = kontroll
    vid = rigg["subjekter"][merke]["versjon_id"]
    _sk(rt, rigg["med_krav"])
    rt.execute(
        "SELECT m19_registrer_kontroll(%s,%s,%s,%s,%s,%s,%s,%s,"
        " current_date - %s, %s)",
        (rigg["med_krav"], uuid.uuid4(), vid, metode, utfall,
         "kontrollor@fasit", f"kref-{merke}", "fasit", siden, AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    """Fasiten: merkelapp → funntype (None = ingen funn)."""
    ut = {m: f for m, f, _d, _k in SETT}
    ut[UTEN_KRAV[0]] = UTEN_KRAV[1]
    return ut


def apne_funn(m, tenant: str) -> dict[str, list[str]]:
    """subjekt_id → åpne funntyper, lest med MIGRATORENS tilkobling (som
    modulens egne tester): sveiperollen har bare EXECUTE på sveipedøra,
    og runtime har ingen lesevei inn i funntabellen i det hele tatt.
    Lesingen er en MÅLING av registeret, ikke en del av driftsveien."""
    _sk(m, tenant)
    rader = m.execute(
        "SELECT subjekt_id::text, funntype FROM adressefunn"
        " WHERE tenant=%s AND apen ORDER BY 1,2", (tenant,)).fetchall()
    m.rollback()
    ut: dict[str, list[str]] = {}
    for sid, funntype in rader:
        ut.setdefault(sid, []).append(funntype)
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader — aldri av riktig antall."""
    fasit = forventet()
    apne = {**apne_funn(m, rigg["med_krav"]),
            **apne_funn(m, rigg["uten_krav"])}
    avvik: list[str] = []
    per: list[dict] = []
    for merke, ventet in fasit.items():
        sid = rigg["subjekter"][merke]["subjekt_id"]
        fikk = sorted(apne.get(sid, []))
        ok = (fikk == [ventet]) if ventet else (fikk == [])
        if not ok:
            avvik.append(f"{merke}: ventet {ventet or 'ingen funn'},"
                         f" fikk {fikk or 'ingen'}")
        per.append({"merke": merke, "ventet": ventet, "fikk": fikk,
                    "subjekt_id": sid})
    return {"per_subjekt": per, "avvik": avvik}
