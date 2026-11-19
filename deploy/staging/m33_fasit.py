"""M-33s fasitsett: fem subjekter i to tenanter med KJENT dom.

KJØRER BARE I DEN ISOLERTE BASEN (ADR-006). En prognose er append-only,
også for migrator: `laget_dato` settes av døra til dagens dato, og
`m33_evidensvakt` nekter enhver endring. Ingen funn som krever at
horisonten er passert kan derfor oppstå samme dag — og den eneste måten å
elde en prognose på er den testsuiten selv bruker: vakten av, alderen
flyttet, vakten på. Det gjøres ALDRI i driftsbasen. Riggen nekter derfor å
kjøre noe annet sted enn `disponit_test` på 55432.

  tenant a (horisont 1 uke, målefrist 1 døgn, domsgrunnlag 4 uker)
    1. `prognose_uten_maaling`   — eldet 30 døgn, ingen har målt uken
                                   (subjektet er `<prognose>:1`)
    2. `prognose_paa_ukjent_datakvalitet` — SAMME prognose: tenanten har
                                   aldri fått datakvaliteten sin profilert
                                   (subjektet er prognosen)
    3. (ren)                     — som 1; uken MÅLES mellom kjøringene
    4. (ok)                      — en fersk prognose, horisonten løper
  tenant b (horisont 2 uker, målefrist 2 døgn, domsgrunnlag 2 uker)
    5. `slaar_ikke_naiv_baseline` — begge ukene målt til nøyaktig
                                   basislinjens tall: modellen bommer,
                                   basislinjen treffer (subjektet er
                                   modellversjonen)

HISTORIKKEN ER STIGENDE (100, 200 … 800 minutter per uke), som i suiten:
da er snittet (450) og forrige uke (800) ulike tall, og modellen og
basislinjen kan skilles.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from pathlib import Path

import psycopg

import sveipfasit_felles as felles

AKTOR = "m33-fasit"
EVIDENSKILDE = "m33_prognose"
FUNNTABELL = "prognosefunn"
SUBJEKTKOLONNE = "referanse"

UNAABARE: dict[str, str] = {
    "modell_uten_prognose":
        "typen har ingen skriver noe sted: sveipen reiser tre funntyper, og "
        "ingen av blokkene leser modeller uten prognoser. Den står i settet "
        "for en regel som ikke er bygget, og kan ikke oppstå i registeret.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

SETT: tuple[tuple[str, str | None, str], ...] = (
    ("umaalt", "prognose_uten_maaling", "a"),
    ("ukjent", "prognose_paa_ukjent_datakvalitet", "a"),
    ("ren", None, "a"),
    ("ok", None, "a"),
    ("baseline", "slaar_ikke_naiv_baseline", "b"),
)
#: (horisont_uker, grunnlag_uker, maalefrist_dogn, domsgrunnlag_uker)
KRAV = {"a": (1, 8, 1, 4), "b": (2, 8, 2, 2)}
METODE = ("Glidende snitt over de siste hele ukene med foert tid;"
          " intervallet fra spredningen i de samme ukene.")
#: Forrige ukes førte minutter i historikken under — basislinjens tall.
BASISLINJE = 800


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m33fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m33-fasit")


def _krev_isolert(mg) -> None:
    """ADR-006: riggen slår av en vakt, og gjør det bare her."""
    db, port = mg.execute(
        "SELECT current_database(), current_setting('port')").fetchone()
    mg.rollback()
    if db != "disponit_test" or port != "55432":
        raise RuntimeError(
            f"m33_fasit NEKTER å kjøre i {db}:{port} — riggen elder en"
            " append-only prognose med vakten avslått, og det skjer bare i"
            " disponit_test på 55432 (ADR-006)")


def _historikk(mg, tenant: str) -> None:
    """Åtte uker ført tid i M-39s tabeller — suitens `_historikk`."""
    _sk(mg, tenant)
    tid = str(uuid.uuid4())
    mg.execute(
        "INSERT INTO lonnstaker (tenant, taker_id, ekstern_ref, navn,"
        " aktiv, opprettet_av) VALUES (%s,%s,%s,'Fasitperson',true,%s)",
        (tenant, tid, f"E-{secrets.token_hex(3)}", AKTOR))
    for k in range(1, 9):
        mg.execute(
            "INSERT INTO timeregistrering (tenant, time_id, taker_id,"
            " dato, minutter, prosjektkode, kilde, kilde_ref, notat,"
            " registrert_av) VALUES (%s,%s,%s,current_date - %s::int,%s,'P1',"
            "'import',%s,'fasit',%s)",
            (tenant, str(uuid.uuid4()), tid, k * 7 - 1, (9 - k) * 100,
             f"r{k}-{secrets.token_hex(3)}", AKTOR))
    mg.commit()


def _modell(rt, tenant: str, versjon: str) -> str:
    mid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m33_registrer_modell(%s,%s,'Glidende snitt',%s,%s,"
               "'samme som forrige uke',current_date - 60,NULL,%s)",
               (tenant, mid, versjon, METODE, AKTOR))
    rt.commit()
    return mid


def _prognose(rt, tenant: str, modell: str) -> str:
    pid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m33_lag_prognose(%s,%s,%s,%s)",
               (tenant, pid, modell, AKTOR))
    rt.commit()
    return pid


def _eld(mg, tenant: str, pid: str, dogn: int = 30) -> None:
    """Suitens `_aldre_prognose`, ordrett — og bare i den isolerte basen."""
    _krev_isolert(mg)
    mg.execute("ALTER TABLE bemanningsprognose DISABLE TRIGGER"
               " m33_evidensvakt")
    mg.execute("ALTER TABLE bemanningsbane DISABLE TRIGGER m33_evidensvakt")
    _sk(mg, tenant)
    mg.execute("UPDATE bemanningsprognose SET laget_dato = laget_dato - %s::int,"
               " gjelder_til = gjelder_til - %s::int WHERE tenant=%s"
               " AND prognose_id=%s", (dogn, dogn, tenant, pid))
    mg.execute("UPDATE bemanningsbane SET ukeslutt = ukeslutt - %s::int"
               " WHERE tenant=%s AND prognose_id=%s", (dogn, tenant, pid))
    mg.execute("ALTER TABLE bemanningsprognose ENABLE TRIGGER"
               " m33_evidensvakt")
    mg.execute("ALTER TABLE bemanningsbane ENABLE TRIGGER m33_evidensvakt")
    mg.commit()


def _maal_uke(rt, tenant: str, pid: str, uke: int, faktisk: int) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m33_registrer_maaling(%s,%s,%s,%s,%s)",
               (tenant, pid, uke, faktisk, AKTOR))
    rt.commit()


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN målingen av den rene uken: den kommer
    mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}}
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    try:
        _krev_isolert(mg)
        for tkey, krav in KRAV.items():
            t = rigg[tkey]
            _sk(rt, t)
            rt.execute("SELECT * FROM m33_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
                       (t, *krav, AKTOR, f"fasit-{runde}-{tkey}"))
            rt.commit()
            _historikk(mg, t)
        a, b = rigg["a"], rigg["b"]
        s: dict[str, str] = {}
        p1 = _prognose(rt, a, _modell(rt, a, f"a1-{runde}"))
        _eld(mg, a, p1)
        s["umaalt"], s["ukjent"] = f"{p1}:1", p1
        p2 = _prognose(rt, a, _modell(rt, a, f"a2-{runde}"))
        _eld(mg, a, p2)
        s["ren"] = f"{p2}:1"
        rigg["ren_prognose"] = p2
        p3 = _prognose(rt, a, _modell(rt, a, f"a3-{runde}"))
        s["ok"] = f"{p3}:1"
        versjon_b = f"b1-{runde}"
        p4 = _prognose(rt, b, _modell(rt, b, versjon_b))
        _eld(mg, b, p4)
        for uke in (1, 2):
            _maal_uke(rt, b, p4, uke, BASISLINJE)
        s["baseline"] = versjon_b
    finally:
        if egen:
            mg.close()
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Den rene uken MÅLES — mellom sveipene. Sveipen måler ingenting
    selv; den skal se at målingen står der og lukke funnet. Én målt uke
    er under tenantens domsgrunnlag, så ingen ny dom felles."""
    _maal_uke(rt, rigg["a"], rigg["ren_prognose"], 1, 450)


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
