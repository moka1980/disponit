"""M-29s fasitsett: fem subjekter i to tenanter med KJENT dom.

Hendelsesregisteret har ti funntyper på `referanse`. Fire er utelukket av
datamodellen, to har ingen skriver, én nektes av døra og én måler en
hardkodet 90-døgnsfrist (se `UNAABARE`). De to andre rigges, begge på
`hendelse_id`:

  tenant a (frist 1 døgn)
    1. `hendelse_uten_forslag`    — over terskel, ingen playbook foreslått
    2. `apen_hendelse_over_frist` — over terskel MED forslag, eldet ti døgn
    3. (ren)                      — som 1; et inngrep FORESLÅS mellom
                                    kjøringene
    4. (ok)                       — over terskel med forslag, fersk
  tenant b (frist 2 døgn)
    5. `hendelse_uten_forslag`    — samme regel

HVER HENDELSE ER OVER TERSKEL: tre signaler à 50 poeng mot en terskel på
100. Forslaget er det som skiller 1 fra 2 — uten det ville den eldede
hendelsen båret begge typene.

ALDEREN SETTES SOM MIGRATOR, testsuitens vei: `oppdaget_ts` har DEFAULT
now() og settes aldri av kalleren, og `test_m29_hendelse.py` flytter den
med en vanlig UPDATE (M-12/M-40-presedensen). Ingen vakt skrus av.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import secrets
import uuid
from pathlib import Path

import psycopg

import sveipfasit_felles as felles

AKTOR = "m29-fasit"
EVIDENSKILDE = "m29_hendelse"
FUNNTABELL = "hendelsesfunn"
SUBJEKTKOLONNE = "referanse"

UNAABARE: dict[str, str] = {
    "inngrep_uten_playbook":
        "`inngrepsforslag.playbook_id` er NOT NULL med fremmednøkkel, og "
        "forslagsdøra krever en gjeldende playbook; et inngrep uten playbook "
        "er urepresenterbart.",
    "fri_kommando_kjort":
        "modulen har ingen utførelsesdør og ingen kolonne for en kjørt "
        "kommando; stegtypene er et lukket sett navn, ikke kommandoer, så "
        "tilstanden kan ikke oppstå.",
    "hendelse_uten_score":
        "`sikkerhetshendelse.score` er NOT NULL og regnes av korrelasjons"
        "døra selv; en hendelse uten score kan ikke settes inn.",
    "score_uten_regel":
        "`sikkerhetshendelse.regel_id` er NOT NULL med fremmednøkkel, og "
        "døra nekter en regel som ikke finnes eller ikke gjelder i dag; en "
        "score uten regel er urepresenterbar.",
    "playbook_uten_steg":
        "`m29_registrer_playbook` nekter en tom stegliste («en playbook uten "
        "steg forklarer ingenting»), og steg kan ikke slettes; sveipens "
        "predikat kan derfor ikke bli sant gjennom noen dør.",
    "regel_uten_treff":
        "regelen måler 90 døgn HARDKODET fra `sikkerhetsregel.opprettet`, som "
        "er DEFAULT now() uten dørparameter; fristen er ikke tenantens, og "
        "suiten elder den ikke, så riggen ville målt klokka.",
    "signaltak_naadd":
        "typen har ingen skriver noe sted: `m29_korreler` KASTER når antall "
        "signaler overstiger tenantens tak, i stedet for å reise et funn; "
        "den står i settet for å navngi grensen.",
    "krav_mangler":
        "sveipens løkke går bare over tenanter som HAR et krav "
        "(`SELECT DISTINCT tenant FROM hendelseskrav`), så typen har ingen "
        "skriver; en tenant uten krav besøkes aldri.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

SETT: tuple[tuple[str, str | None, str], ...] = (
    ("uten_forslag", "hendelse_uten_forslag", "a"),
    ("over_frist", "apen_hendelse_over_frist", "a"),
    ("ren", None, "a"),
    ("ok", None, "a"),
    ("uten_forslag_b", "hendelse_uten_forslag", "b"),
)
#: (korrelasjonsvindu_min, alvorsterskel, frist_dogn, signaltak)
KRAV = {"a": (60, 100, 1, 50), "b": (61, 101, 2, 51)}
MED_FORSLAG = ("over_frist", "ok")


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m29fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m29-fasit")


def _hendelse(rt, tenant: str, regel: str, kv: int, nr: int) -> str:
    hid = str(uuid.uuid4())
    naa = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m29_korreler(%s,%s,%s,%s,%s,%s,%s,%s)",
               (tenant, hid, regel, kv,
                [nr * 1000 + i for i in range(3)],
                [f"aktor-{i}" for i in range(3)],
                [naa + dt.timedelta(seconds=i) for i in range(3)], AKTOR))
    rt.commit()
    return hid


def _forslag(rt, tenant: str, hid: str, playbook: str) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT m29_foresla_inngrep(%s,%s,%s,%s,%s,%s)",
               (tenant, str(uuid.uuid4()), hid, playbook,
                "Mønsteret matcher playbookens vilkår", AKTOR))
    rt.commit()


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN forslaget til den rene hendelsen: det
    kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "playbook": {}}
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    try:
        s: dict[str, str] = {}
        kv, regel = {}, {}
        for tkey, krav in KRAV.items():
            t = rigg[tkey]
            _sk(rt, t)
            kv[tkey] = rt.execute(
                "SELECT m29_sett_krav(%s,%s,%s,%s,%s,%s)",
                (t, *krav, AKTOR)).fetchone()[0]
            regel[tkey], pb = str(uuid.uuid4()), str(uuid.uuid4())
            rt.execute(
                "SELECT m29_registrer_regel(%s,%s,%s,'unntak_gjentatt',50,2,"
                "%s,current_date - 1,NULL,%s)",
                (t, regel[tkey], f"regel-{secrets.token_hex(3)}",
                 "Gjentatte unntak er et mønster", AKTOR))
            rt.execute(
                "SELECT m29_registrer_playbook(%s,%s,%s,%s,true,%s,"
                "current_date - 1,NULL,%s)",
                (t, pb, f"pb-{secrets.token_hex(3)}",
                 "Når det samme unntaket gjentar seg",
                 ["varsle_sikkerhetsansvarlig", "samle_tidslinje"], AKTOR))
            rt.commit()
            rigg["playbook"][tkey] = pb
        for nr, (merke, _f, tkey) in enumerate(SETT, start=1):
            t = rigg[tkey]
            s[merke] = _hendelse(rt, t, regel[tkey], kv[tkey], nr)
            if merke in MED_FORSLAG:
                _forslag(rt, t, s[merke], rigg["playbook"][tkey])
        a = rigg["a"]
        _sk(mg, a)
        mg.execute("UPDATE sikkerhetshendelse SET oppdaget_ts = now()"
                   " - INTERVAL '10 days' WHERE tenant = %s"
                   " AND hendelse_id = %s", (a, s["over_frist"]))
        mg.commit()
    finally:
        if egen:
            mg.close()
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Et inngrep foreslås for den rene hendelsen — MELLOM sveipene.
    Sveipen foreslår ingenting selv; den skal se at forslaget finnes og
    lukke funnet."""
    _forslag(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"],
             rigg["playbook"]["a"])


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
