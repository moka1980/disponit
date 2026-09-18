"""M-24s fasitsett: seks avtaler med KJENT dom, drevet gjennom dørene.

Leverandørregisteret har fem funntyper. Settet gir hver av dem nøyaktig
én avtale, og én avtale som skal være REN:

  1. `sla_brudd`          — en leveranse over avtalt leveringstid
  2. `pris_over_terskel`  — siste leveranse 20 % over avtalt pris
  3. `avtale_utlopt`      — gyldigheten er innenfor varselvinduet
  4. `avtale_uten_maling` — ingen leveranse på stillhetsgrensen
  5. `ingen_terskel`      — egen tenant UTEN terskler (regelen gjelder
                            tenanten, ikke avtalen)
  6. (ren)                — leveranse innenfor SLA og pris, gyldig lenge

FUNNTYPENE OVERLAPPER, og settet er bygget for å skille dem. Hver avtale
som IKKE skal være utløpt får `gyldig_til` godt utenfor varselvinduet;
hver avtale som ikke skal være stille får en fersk leveranse; og
leveransene som ikke skal bryte SLA eller pris ligger innenfor begge.

Alt går gjennom modulens egne dører (`m24_sett_terskler`,
`m24_registrer_leverandor`, `m24_registrer_avtale`,
`m24_registrer_leveranse`). Ingen rå DML i registerets tabeller.

TO KJØRINGER, OG LUKKINGEN MÅLES PÅ EKTE (m19-formen): den rene avtalen
fødes UTEN leveranser og med `gyldig_fra` 200 døgn tilbake, så første
sveip gir den `avtale_uten_maling`. Leveransen som gjør den ren kommer
MELLOM kjøringene, og andre sveip skal lukke funnet.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

#: Tersklene settet måles mot — pinnet her, aldri utledet av kjøringen.
PRISSTIGNING_PROMILLE = 100        # 10 % over avtalt pris
SLA_BRUDD_GRENSE = 1
AVTALE_VARSEL_DOGN = 30
MALING_STILLHET_DOGN = 90

#: Avtalens pris og SLA — like for alle avtalene, så bare det settet
#: varierer skiller dommene.
AVTALT_PRIS = 100_000
SLA_TYPE = "leveringstid_dogn"
AVTALT_VERDI = 5

AKTOR = "m24-fasit"
EVIDENSKILDE = "m24_leverandor"
FUNNTABELL = "leverandorfunn"
SUBJEKTKOLONNE = "avtale_id"

#: Ingen funntype er unåbar: alle dørene tar datoer.
UNAABARE: dict[str, str] = {}

#: SUBJEKTET SOM RENSES mellom de to sveipene.
RENSES = "ren"

#: (merkelapp, forventet funntype eller None, gyldig_fra for N døgn
#:  siden, gyldig_til om N døgn, leveranser
#:  [(døgn siden, faktisk_verdi, pris)])
SETT: tuple[tuple[str, str | None, int, int, list], ...] = (
    # Leveringstiden er 9 døgn mot avtalt 5. Prisen er avtalt pris, og
    # leveransen er fersk, så bare SLA-bruddet står igjen.
    ("sla", "sla_brudd", 200, 365, [(0, 9, AVTALT_PRIS)]),
    # Siste leveranse koster 20 % over avtalt, godt over promillen.
    ("pris", "pris_over_terskel", 200, 365,
     [(0, AVTALT_VERDI, AVTALT_PRIS * 12 // 10)]),
    # Gyldigheten utløper om 10 døgn — innenfor varselvinduet på 30.
    ("utlopt", "avtale_utlopt", 200, 10, [(0, AVTALT_VERDI, AVTALT_PRIS)]),
    # INGEN leveranser, og avtalen har løpt i 200 døgn.
    ("stille", "avtale_uten_maling", 200, 365, []),
    # Den RENE fødes uten leveranser: funnet den får i første sveip er
    # nettopp det andre sveip skal lukke.
    ("ren", None, 200, 365, []),
)

#: Avtalen i tenanten UTEN terskler — hele tenanten er funnet.
UTEN_TERSKEL = ("ingen_terskel", "ingen_terskel", 200, 365,
                [(0, AVTALT_VERDI, AVTALT_PRIS)])

#: Leveransen som gjør den rene avtalen ren, registrert MELLOM sveipene.
REN_LEVERANSE = (0, AVTALT_VERDI, AVTALT_PRIS)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m24fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m24-fasit")


def lag_avtale(rt, tenant: str, merke: str, fra_siden: int, til_om: int,
               leveranser: list) -> dict:
    """Én leverandør med én avtale og dens leveranser — gjennom dørene.

    DATOENE REGNES I BASEN (`current_date ± n`): `date.today()` er
    maskinens lokale dag, mens sveipen regner i basens sone."""
    lid, aid = uuid.uuid4(), uuid.uuid4()
    _sk(rt, tenant)
    rt.execute("SELECT m24_registrer_leverandor(%s,%s,%s,%s,%s)",
               (tenant, lid, f"Fasit {merke}", f"ref-{merke}", AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute(
        "SELECT m24_registrer_avtale(%s,%s,%s,%s,%s,%s,%s,"
        " current_date - %s, current_date + %s, %s)",
        (tenant, aid, lid, f"ytelse-{merke}", SLA_TYPE, AVTALT_VERDI,
         AVTALT_PRIS, fra_siden, til_om, AKTOR))
    rt.commit()
    for siden, verdi, pris in leveranser:
        _sk(rt, tenant)
        rt.execute(
            "SELECT m24_registrer_leveranse(%s,%s,%s, current_date - %s,"
            " %s,%s,%s,%s)",
            (tenant, uuid.uuid4(), aid, siden, verdi, pris,
             f"lev-{merke}", AKTOR))
        rt.commit()
    return {"merke": merke, "subjekt_id": str(aid)}


def forbered(rt, runde: str) -> dict:
    """Begge tenantene, med og uten terskler — men UTEN den rene avtalens
    leveranse: den kommer mellom kjøringene."""
    med = tenantnavn(runde, "med_terskel")
    uten = tenantnavn(runde, "uten_terskel")
    _sk(rt, med)
    rt.execute("SELECT m24_sett_terskler(%s,%s,%s,%s,%s,%s)",
               (med, PRISSTIGNING_PROMILLE, SLA_BRUDD_GRENSE,
                AVTALE_VARSEL_DOGN, MALING_STILLHET_DOGN, AKTOR))
    rt.commit()
    rigg = {"med_terskel": med, "uten_terskel": uten, "subjekter": {}}
    for merke, _v, fra, til, leveranser in SETT:
        rigg["subjekter"][merke] = lag_avtale(rt, med, merke, fra, til,
                                              leveranser)
    merke, _v, fra, til, leveranser = UTEN_TERSKEL
    rigg["subjekter"][merke] = lag_avtale(rt, uten, merke, fra, til,
                                          leveranser)
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["med_terskel"], rigg["uten_terskel"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Leveransen som gjør den rene avtalen ren — MELLOM sveipene."""
    siden, verdi, pris = REN_LEVERANSE
    aid = rigg["subjekter"][RENSES]["subjekt_id"]
    _sk(rt, rigg["med_terskel"])
    rt.execute(
        "SELECT m24_registrer_leveranse(%s,%s,%s, current_date - %s,"
        " %s,%s,%s,%s)",
        (rigg["med_terskel"], uuid.uuid4(), aid, siden, verdi, pris,
         "fasit: gjør avtalen ren", AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _fra, _til, _l in SETT}
    ut[UTEN_TERSKEL[0]] = UTEN_TERSKEL[1]
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader — aldri av riktig antall."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
