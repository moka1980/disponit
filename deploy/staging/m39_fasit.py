"""M-39s fasitsett: seks lønnstakere med KJENT dom.

Lønnsgrunnlaget har fem funntyper, og alle er nåbare: hele alders-aksen
går gjennom `timeregistrering.dato`, som er en dørparameter.

  1. `time_uten_arbeidsplan` — en dag uten plan å måles mot
  2. `avvik_mot_plan`        — førte timer avviker fra planen
  3. `overtid`               — over normaltiden for dagen
  4. `ukjent_prosjektkode`   — timen er ført på en annen kode enn planens
  5. `ingen_terskel`         — egen tenant UTEN terskler
  6. (ren)                   — dag uten plan, som får en plan MELLOM
                               kjøringene

FUNNENE AGGREGERES PER LØNNSTAKER, ikke per dag. Derfor har hver
merkelapp sin EGEN taker med NØYAKTIG én dag: to dager på samme taker
ville blandet to dommer i én rad, og settet kunne ikke lest per subjekt.

`overtid` ER DEN PROMISKUØSE: den krever ikke plan, så en planløs dag
over normaltiden ville gitt BÅDE den og `time_uten_arbeidsplan`. Derfor
ligger den planløse dagen under normaltiden, og overtidsdagen har en
plan som er ført NØYAKTIG som planlagt (så avviket er null).
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

#: Tersklene settet måles mot — pinnet her, aldri utledet av kjøringen.
NORMALTID_DAG = 450          # 7,5 time
NORMALTID_UKE = 2250         # 37,5 time
AVVIK_MINUTTER = 0
UTEN_PLAN_DOGN = 7
VURDERINGSVINDU_DOGN = 60

#: Planens kode, og den koden en time kan føres på ved en feil.
PLANKODE = "PROSJ-A"
FEILKODE = "PROSJ-B"

AKTOR = "m39-fasit"
EVIDENSKILDE = "m39_lonn"
FUNNTABELL = "lonnsfunn"
SUBJEKTKOLONNE = "taker_id"

UNAABARE: dict[str, str] = {}
RENSES = "ren"

#: (merkelapp, forventet funntype eller None,
#:  plan: (planlagt_minutter, prosjektkode, gyldig_fra for N døgn siden)
#:        eller None,
#:  dag: (døgn siden, minutter, prosjektkode))
SETT: tuple[tuple[str, str | None, tuple | None, tuple], ...] = (
    # INGEN PLAN, og dagen er eldre enn nådefristen. Minuttene ligger
    # under normaltiden, ellers ville `overtid` også slått inn.
    ("uten_plan", "time_uten_arbeidsplan", None, (30, 400, PLANKODE)),
    # PLAN PÅ 450, FØRT 200: avviket er 250 minutter, godt over grensen
    # på 0. Under normaltiden, så ingen overtid. Samme kode som planen.
    ("avvik", "avvik_mot_plan", (450, PLANKODE, 200), (10, 200, PLANKODE)),
    # PLAN PÅ 600, FØRT 600: avviket er null, men dagen ligger over
    # normaltiden på 450.
    ("over", "overtid", (600, PLANKODE, 200), (10, 600, PLANKODE)),
    # PLAN PÅ 400, FØRT 400 — men på en ANNEN kode enn planens.
    ("feilkode", "ukjent_prosjektkode", (400, PLANKODE, 200),
     (10, 400, FEILKODE)),
    # Den RENE fødes UTEN plan, med en dag eldre enn nådefristen: funnet
    # den får i første sveip er nettopp det andre sveip skal lukke.
    ("ren", None, None, (30, 400, PLANKODE)),
)

#: Lønnstakeren i tenanten UTEN terskler. Tenanten må ha minst én AKTIV
#: taker, ellers besøker ikke sveipen den i det hele tatt.
UTEN_TERSKEL = ("ingen_terskel", "ingen_terskel", (400, PLANKODE, 200),
                (10, 400, PLANKODE))

#: Planen som gjør den rene takeren ren, satt MELLOM sveipene: den dekker
#: dagen, er ført nøyaktig som planlagt, og på samme kode — så ingen av
#: de tre andre funntypene tar plassen til den som lukkes.
REN_PLAN = (400, PLANKODE, 200)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m39fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m39-fasit")


def _plan(rt, tenant: str, tid, merke: str, plan) -> None:
    """Én arbeidsplan gjennom døra.

    EN PLAN SKRIVES IKKE BAKOVER: døra nekter en `gyldig_fra` som ikke er
    senere enn takerens forrige. Settet gir derfor hver taker HØYST ÉN
    plan, og den rene får sin først mellom kjøringene."""
    minutter, kode, fra_siden = plan
    _sk(rt, tenant)
    rt.execute(
        "SELECT m39_sett_arbeidsplan(%s,%s,%s,%s,%s,"
        " current_date - %s, %s, %s)",
        (tenant, uuid.uuid4(), tid, minutter, kode, fra_siden,
         f"fasitplan {merke}", AKTOR))
    rt.commit()


def lag_taker(rt, tenant: str, merke: str, plan, dag) -> dict:
    """Én lønnstaker med høyst én plan og NØYAKTIG én dag."""
    tid = uuid.uuid4()
    _sk(rt, tenant)
    rt.execute("SELECT m39_registrer_taker(%s,%s,%s,%s,%s)",
               (tenant, tid, f"ref-{merke}", f"Fasit {merke}", AKTOR))
    rt.commit()
    if plan is not None:
        _plan(rt, tenant, tid, merke, plan)
    siden, minutter, kode = dag
    _sk(rt, tenant)
    rt.execute(
        "SELECT m39_registrer_timer(%s,%s,%s, current_date - %s,"
        " %s,%s,'import',%s,%s,%s)",
        (tenant, uuid.uuid4(), tid, siden, minutter, kode,
         f"fasit-{merke}-0", f"fasittime {merke}", AKTOR))
    rt.commit()
    return {"merke": merke, "subjekt_id": str(tid)}


def forbered(rt, runde: str) -> dict:
    """Begge tenantene, med og uten terskler — men UTEN den rene
    takerens plan: den kommer mellom kjøringene."""
    med = tenantnavn(runde, "med_terskel")
    uten = tenantnavn(runde, "uten_terskel")
    _sk(rt, med)
    rt.execute("SELECT m39_sett_terskler(%s,%s,%s,%s,%s,%s,%s)",
               (med, NORMALTID_DAG, NORMALTID_UKE, AVVIK_MINUTTER,
                UTEN_PLAN_DOGN, VURDERINGSVINDU_DOGN, AKTOR))
    rt.commit()
    rigg = {"med_terskel": med, "uten_terskel": uten, "subjekter": {}}
    for merke, _v, plan, dag in SETT:
        rigg["subjekter"][merke] = lag_taker(rt, med, merke, plan, dag)
    merke, _v, plan, dag = UTEN_TERSKEL
    rigg["subjekter"][merke] = lag_taker(rt, uten, merke, plan, dag)
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["med_terskel"], rigg["uten_terskel"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Planen som gjør den rene takeren ren — MELLOM sveipene.

    Planen dekker dagen, og dagen er ført nøyaktig som planlagt på samme
    kode: da forsvinner `time_uten_arbeidsplan` uten at `avvik_mot_plan`,
    `overtid` eller `ukjent_prosjektkode` tar plassen."""
    _plan(rt, rigg["med_terskel"], rigg["subjekter"][RENSES]["subjekt_id"],
          RENSES, REN_PLAN)


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _p, _d in SETT}
    ut[UTEN_TERSKEL[0]] = UTEN_TERSKEL[1]
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
