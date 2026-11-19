"""M-40s fasitsett: syv subjekter i to tenanter med KJENT dom.

Medarbeiderregisteret har ti funntyper på `referanse`. Seks er utelukket
av datamodellen og står i `UNAABARE` med migrasjonens egne grunner; de
fire sveipen kan reise, rigges alle:

  tenant A (frist 1 døgn, gruppeterskel 5)
    1. `ansatt_uten_lop`                — ansatt for 5 døgn siden, intet løp
    2. `apent_lop_over_frist`           — løpet startet for 5 døgn siden
    3. `maaling_uten_lesbar_gruppe`     — lukket måling med to svar i én
                                          gruppe; terskelen er 5
    4. `kontrakt_paa_tilbaketrukket_mal`— kontrakt på en M-5-versjon som
                                          trekkes tilbake etter utstedelsen
    5. (ren)                            — som 1; løpet startes MELLOM
                                          kjøringene
    6. (ok)                             — ansatt for 5 døgn siden, med et
                                          løp startet i dag
  tenant B (frist 2 døgn, gruppeterskel 6)
    7. `ansatt_uten_lop`                — samme regel i en annen tenant

ALDEREN SETTES SOM MIGRATOR, testsuitens vei: `lonnstaker` er M-39s
tabell (M-40 har bare lesing der), og `ansattlop.startet` er DEFAULT
now() uten dørparameter. `test_m40_medarbeider` bakdaterer begge på
samme måte og begrunner det der.

MALEN GÅR GJENNOM M-5S DØRER — familie, versjon, publisering og
tilbaketrekking — så kontrakten står på en mal slik den faktisk oppstår.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m40-fasit"
EVIDENSKILDE = "m40_medarbeider"
FUNNTABELL = "medarbeiderfunn"
SUBJEKTKOLONNE = "referanse"

UNAABARE: dict[str, str] = {
    "beslutning_med_rettsvirkning":
        "modulen har ingen beslutningsdør: ingen funksjon skriver en "
        "ansettelse, oppsigelse eller lønnsendring, så en beslutning med "
        "rettsvirkning kan ikke oppstå i registeret i det hele tatt.",
    "individprofil_bygget":
        "ingen tabell i modulen bærer både en person og et tall om henne; "
        "pulssvar er skilt fra personer i datamodellen, så en individprofil "
        "kan ikke settes sammen av registerets egne rader.",
    "puls_identifiserte_en_person":
        "`pulssvar` har ingen personnøkkel — bare måling, gruppe og verdi — "
        "så et svar kan ikke føres tilbake til et menneske gjennom "
        "registeret, og funntypen kan aldri bli sann.",
    "gruppeterskel_endret":
        "ingen rolle har UPDATE på `pulsmaaling.gruppeterskel`; terskelen "
        "settes ved åpningen og står fast, så en endret terskel etter at "
        "svarene kom inn er urepresenterbar.",
    "kontrakt_uten_malversjon":
        "`medarbeiderkontrakt.malversjon_id` er NOT NULL med fremmednøkkel, "
        "og døra nekter en versjon som ikke er publisert; en kontrakt uten "
        "malversjon kan ikke settes inn.",
    "krav_mangler":
        "sveipeløkka går bare over tenanter som HAR et medarbeiderkrav "
        "(M-28s lærdom), så en tenant uten krav blir aldri besøkt og "
        "funntypen kan ikke reises av sveipen.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

SETT: tuple[tuple[str, str | None, str], ...] = (
    ("ansatt", "ansatt_uten_lop", "a"),
    ("lop", "apent_lop_over_frist", "a"),
    ("maaling", "maaling_uten_lesbar_gruppe", "a"),
    ("kontrakt", "kontrakt_paa_tilbaketrukket_mal", "a"),
    ("ren", None, "a"),
    ("ok", None, "a"),
    ("ansatt_b", "ansatt_uten_lop", "b"),
)
KRAV = {"a": (5, 1), "b": (6, 2)}


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m40fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m40-fasit")


def _ansatt(mg, tenant: str, alder_dogn: int) -> str:
    tid = str(uuid.uuid4())
    _sk(mg, tenant)
    mg.execute(
        "INSERT INTO lonnstaker (tenant, taker_id, ekstern_ref, navn, aktiv,"
        " opprettet, opprettet_av) VALUES (%s,%s,%s,'Fasit Ansatt',true,"
        " now() - make_interval(days => %s), %s)",
        (tenant, tid, f"fasit-{secrets.token_hex(4)}", alder_dogn, AKTOR))
    mg.commit()
    return tid


def _lop(rt, tenant: str, taker: str, kv: int) -> str:
    lid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m40_start_lop(%s,%s,%s,%s,%s)",
               (tenant, lid, taker, kv, AKTOR))
    rt.commit()
    return lid


def _publisert_mal(rt, tenant: str) -> str:
    fid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m5_opprett_malfamilie(%s,%s,%s,%s,%s)",
               (tenant, "Fasitkontrakt", None, AKTOR, fid))
    rt.execute("SELECT * FROM m5_opprett_malversjon(%s,%s,%s::jsonb,"
               "%s::jsonb,%s,%s)",
               (tenant, fid,
                json.dumps([{"komponenttype": "tekst",
                             "innhold": "Stilling: "},
                            {"komponenttype": "felt",
                             "feltnokkel": "stilling"}]),
                json.dumps([{"feltnokkel": "stilling", "paakrevd": True,
                             "felttype": "tekst",
                             "beskrivelse": "Stillingen"}]),
                AKTOR, vid))
    rt.execute("SELECT m5_publiser_malversjon(%s,%s,%s)",
               (tenant, vid, AKTOR))
    rt.commit()
    return vid


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN løpet til den rene ansatte: det kommer
    mellom kjøringene."""
    import psycopg
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "kravversjon": {}}
    s: dict[str, str] = {}
    try:
        for tkey, (terskel, frist) in KRAV.items():
            _sk(rt, rigg[tkey])
            kv = rt.execute("SELECT m40_sett_krav(%s,%s,%s,%s)",
                            (rigg[tkey], terskel, frist, AKTOR)).fetchone()[0]
            rt.commit()
            rigg["kravversjon"][tkey] = kv
        a, b, kva = rigg["a"], rigg["b"], rigg["kravversjon"]["a"]

        s["ansatt"] = _ansatt(mg, a, 5)

        t_lop = _ansatt(mg, a, 0)
        lid = _lop(rt, a, t_lop, kva)
        _sk(mg, a)
        mg.execute("UPDATE ansattlop SET startet = now()"
                   " - make_interval(days => 5) WHERE tenant=%s"
                   " AND lop_id=%s", (a, lid))
        mg.commit()
        s["lop"] = lid

        mid = str(uuid.uuid4())
        _sk(rt, a)
        rt.execute("SELECT m40_apne_maaling(%s,%s,'Fasitpuls',5,%s)",
                   (a, mid, AKTOR))
        for verdi in (3, 4):
            rt.execute("SELECT m40_avgi_puls(%s,%s,%s,'g1',%s)",
                       (a, str(uuid.uuid4()), mid, verdi))
        rt.execute("SELECT m40_lukk_maaling(%s,%s,%s)", (a, mid, AKTOR))
        rt.commit()
        s["maaling"] = mid

        t_k = _ansatt(mg, a, 0)
        _lop(rt, a, t_k, kva)
        vid = _publisert_mal(rt, a)
        kid = str(uuid.uuid4())
        _sk(rt, a)
        rt.execute("SELECT m40_utsted_kontrakt(%s,%s,%s,%s,%s,%s)",
                   (a, kid, t_k, vid, ["stilling"], AKTOR))
        rt.execute("SELECT m5_trekk_tilbake_malversjon(%s,%s,%s)",
                   (a, vid, AKTOR))
        rt.commit()
        s["kontrakt"] = kid

        s["ren"] = _ansatt(mg, a, 5)
        t_ok = _ansatt(mg, a, 5)
        _lop(rt, a, t_ok, kva)
        s["ok"] = t_ok
        s["ansatt_b"] = _ansatt(mg, b, 5)
    finally:
        if egen:
            mg.close()
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Løpet til den rene ansatte startes — MELLOM sveipene. Døra rører
    ikke funnet; sveipen må se at den ansatte har fått et løp og lukke."""
    _lop(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"],
         rigg["kravversjon"]["a"])


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
