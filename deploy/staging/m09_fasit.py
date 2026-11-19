"""M-9s fasitsett: seks begreper i to tenanter med KJENT dom.

Begrepsregisteret har to funntyper, begge på `begrep_id`, og sveipen ser
bare den GJELDENDE versjonen av hvert begrep. Varselvinduet er 30 døgn
(`begrepssveip.VARSELVINDU_DOGN`).

  tenant A
    1. `utlopt`         — gyldig til for 5 døgn siden
    2. `utloper_snart`  — gyldig til om 10 døgn
    3. (ren)            — som 1; får en NY VERSJON mellom kjøringene,
                          gyldig ett år fram
    4. (ok)             — gyldig til om ett år
  tenant B
    5. `utlopt`         — samme regel i en annen tenant
    6. (ok)             — som 4

DØRENE ER DEPLOYENS, IKKE RUNTIMES. v1 har ingen HTTP-skrivevei (095):
ordlisten fylles gjennom dørene av en sporet deploy-handling. Riggen
kaller dem derfor som registerets eier, med migratortilkoblingen — den
samme veien en deploy går. Evidensen skrives av triggeren i 212, ikke av
riggen.

DEN RENE ER EN NY VERSJON, ikke en redigering: den gamle raden består
uendret med `gjeldende = false`, og funnet på den gamle `begrep_id`
skal lukkes fordi den ikke lenger er gjeldende. Den nye versjonen får
ingen funn.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m09-fasit"
EVIDENSKILDE = "m09_kunnskap"
FUNNTABELL = "begrepsfunn"
SUBJEKTKOLONNE = "begrep_id"

UNAABARE: dict[str, str] = {}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

#: (merkelapp, forventet, tenantnøkkel, gyldig_til som SQL-uttrykk)
SETT: tuple[tuple[str, str | None, str, str], ...] = (
    ("utlopt", "utlopt", "a", "current_date - 5"),
    ("snart", "utloper_snart", "a", "current_date + 10"),
    # Den RENE fødes utløpt: funnet den får i første sveip er nettopp det
    # andre sveip skal lukke, når en ny versjon har avløst den.
    ("ren", None, "a", "current_date - 5"),
    ("ok", None, "a", "current_date + 365"),
    ("utlopt_b", "utlopt", "b", "current_date - 5"),
    ("ok_b", None, "b", "current_date + 365"),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m09fasit-{rolle}-{runde}"


def _dor(mg, tenant: str, sql: str, args: tuple):
    felles.sett_kontekst(mg, tenant, AKTOR, "m09-fasit")
    mg.execute("SET LOCAL ROLE disponit_kunnskap_eier")
    rad = mg.execute(sql, args).fetchone()
    mg.commit()
    return rad[0]


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN den nye versjonen av den rene: den
    kommer mellom kjøringene."""
    import psycopg
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "termer": {}}
    try:
        for merke, _v, tkey, gyldig in SETT:
            tenant = rigg[tkey]
            term = f"Fasitbegrep {merke} {runde}"
            bid = _dor(mg, tenant,
                       "SELECT m9_registrer_begrep(%s,%s,%s,%s,%s,"
                       f" {gyldig}, %s, %s)",
                       (tenant, term, f"Forklaring av {merke}",
                        "Fasiteier", "Fasitkilde 1", AKTOR,
                        str(uuid.uuid4())))
            rigg["subjekter"][merke] = {"merke": merke,
                                        "subjekt_id": str(bid)}
            rigg["termer"][merke] = term
    finally:
        if egen:
            mg.close()
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """En ny versjon av det rene begrepet, gyldig ett år fram — MELLOM
    sveipene. Døra rører ikke funnet; sveipen må se at den gamle
    versjonen ikke lenger er gjeldende og lukke."""
    import psycopg
    with psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"]) as mg:
        _dor(mg, rigg["a"],
             "SELECT m9_ny_begrepsversjon(%s,%s,%s,%s,%s,"
             " current_date + 365, %s, %s)",
             (rigg["a"], rigg["termer"][RENSES], "Forklaring av ren, v2",
              "Fasiteier", "Fasitkilde 2", AKTOR, str(uuid.uuid4())))


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _g in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
