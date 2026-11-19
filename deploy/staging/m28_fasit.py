"""M-28s fasitsett: fem subjekter i to tenanter med KJENT dom.

Transportregisteret har ni funntyper. Fire er utelukket av datamodellen,
`krav_mangler` har ingen skriver, `land_uten_pakke` krever at en
plattformdom (landpakken) utløper, og `tungt_kolli_ukontrollert` kan
aldri stå alene på subjektet sitt (se `UNAABARE`). De to andre rigges:

  tenant a (forslagsfrist 1 døgn)
    1. `apent_forslag_over_frist` — en åpen plan, eldet ti døgn
                                    (subjektet er planen)
    2. `kolli_uten_forslag`       — et kolli, eldet ti døgn, uten plan
                                    (subjektet er kolliet)
    3. (ren)                      — som 1; planen FORKASTES mellom
                                    kjøringene
    4. (ok)                       — en fersk plan, innenfor fristen
  tenant b (forslagsfrist 2 døgn)
    5. `kolli_uten_forslag`       — samme regel

ALDEREN SETTES SOM MIGRATOR, testsuitens vei: `foreslatt_ts` og
`registrert` har DEFAULT now() og ingen dørparameter, og
`test_m28_transport.py` flytter dem med en vanlig UPDATE (M-12/M-40-
presedensen). Ingen vakt skrus av — tabellene har ingen på disse feltene.
Adressen skrives også som migrator, som i suiten: M-19s dører er ikke
M-28s.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from pathlib import Path

import psycopg

import sveipfasit_felles as felles

AKTOR = "m28-fasit"
EVIDENSKILDE = "m28_transport"
FUNNTABELL = "transportfunn"
SUBJEKTKOLONNE = "referanse"

UNAABARE: dict[str, str] = {
    "kolli_bestilt_to_ganger":
        "`transportforslag` har ingen bestillingskolonne og modulen ingen "
        "bestillingsdør; en dobbel bestilling er urepresenterbar, og "
        "funntypen står i settet for å navngi vernet.",
    "fareklasse_utledet_av_maskin":
        "`kolli.fareklasse_oppgitt_av` er NOT NULL og døra krever et navn; "
        "en fareklasse uten et menneske bak kan ikke settes inn, og sveipen "
        "reiser aldri typen.",
    "farlig_gods_uten_landregel":
        "forslagsdøra nekter farlig gods til et land uten gjeldende "
        "landregel, og `landpakke_regelversjon` er NOT NULL med "
        "fremmednøkkel; tilstanden kan ikke oppstå i registeret.",
    "forslag_uten_validert_adresse":
        "forslagsdøra krever en adresseversjon med godkjent kontroll, og "
        "kolonnen er NOT NULL med fremmednøkkel; et forslag uten validert "
        "adresse er urepresenterbart.",
    "krav_mangler":
        "sveipens løkke går bare over tenanter som HAR et krav "
        "(`SELECT DISTINCT tenant FROM transportkrav`), så typen har ingen "
        "skriver noe sted; den står utenfor `m28_funn_er_sveipens` med vilje.",
    "land_uten_pakke":
        "funnet oppstår bare når en landpakke — en plattformdom felt i "
        "migrasjon, eid av M-32 — utløper etter at en plan er laget; døra "
        "nekter en plan til et land uten gjeldende pakke, og riggen endrer "
        "ikke plattformdommer.",
    "tungt_kolli_ukontrollert":
        "regelen krever en åpen plan eldre enn fristen — nøyaktig vilkåret "
        "for `apent_forslag_over_frist` på samme referanse. Typen kan derfor "
        "aldri stå alene på subjektet, og fasiten dømmer én type per subjekt.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

SETT: tuple[tuple[str, str | None, str], ...] = (
    ("forslag", "apent_forslag_over_frist", "a"),
    ("kolli", "kolli_uten_forslag", "a"),
    ("ren", None, "a"),
    ("ok", None, "a"),
    ("kolli_b", "kolli_uten_forslag", "b"),
)
#: (avsenderland, maks_kolli_gram, manuell_over_gram, forslagsfrist_dogn)
KRAV = {"a": ("NO", 50_000, 20_000, 1), "b": ("NO", 51_000, 21_000, 2)}


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m28fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m28-fasit")


def _adresse(mg, tenant: str) -> str:
    """En adresseversjon med godkjent kontroll — suitens `_adresse`."""
    _sk(mg, tenant)
    sid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    mg.execute(
        "INSERT INTO adressesubjekt (tenant, subjekt_id, ekstern_ref,"
        " navn, opprettet_av) VALUES (%s,%s,%s,'Fasitkunde AS',%s)",
        (tenant, sid, f"k-{secrets.token_hex(3)}", AKTOR))
    mg.execute(
        "INSERT INTO adresseversjon (tenant, versjon_id, subjekt_id,"
        " linje1_original, postnr_original, poststed_original, land,"
        " linje1_normalisert, postnr_normalisert, poststed_normalisert,"
        " kilde, kilde_ref, notat, gjelder_fra, registrert_av)"
        " VALUES (%s,%s,%s,'Storgata 1','0155','Oslo','NO',"
        " 'STORGATA 1','0155','OSLO','manuell',%s,'fasit',"
        " current_date - 30,%s)", (tenant, vid, sid, AKTOR, AKTOR))
    mg.execute(
        "INSERT INTO adressekontroll (tenant, kontroll_id, versjon_id,"
        " metode, utfall, kontrollor, kilde_ref, begrunnelse, kontrollert,"
        " registrert_av) VALUES (%s,%s,%s,'dokumentert','godkjent',%s,"
        " 'fasit-1','adressen er bekreftet',now(),%s)",
        (tenant, str(uuid.uuid4()), vid, AKTOR, AKTOR))
    mg.commit()
    return vid


def _kolli(rt, tenant: str, kv: int) -> str:
    kid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m28_registrer_kolli(%s,%s,%s,1000,300,200,150,"
               "'ingen',%s,%s,%s)",
               (tenant, kid, f"kolli-{secrets.token_hex(4)}", AKTOR, kv,
                AKTOR))
    rt.commit()
    return kid


def _foresla(rt, tenant: str, kv: int, kid: str, vid: str) -> str:
    fid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m28_foresla(%s,%s,%s,%s,%s,%s,%s)",
               (tenant, fid, kid, kv, vid, "raskeste rute innen SLA", AKTOR))
    rt.commit()
    return fid


def _eld(mg, tenant: str, tabell: str, kolonne: str, nokkel: str,
         verdi: str) -> None:
    _sk(mg, tenant)
    mg.execute(f"UPDATE {tabell} SET {kolonne} = now() - INTERVAL '10 days'"
               f" WHERE tenant = %s AND {nokkel} = %s", (tenant, verdi))
    mg.commit()


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN forkastingen av den rene planen: den
    kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}}
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    try:
        kv: dict[str, int] = {}
        for tkey, krav in KRAV.items():
            _sk(rt, rigg[tkey])
            kv[tkey] = rt.execute("SELECT m28_sett_krav(%s,%s,%s,%s,%s,%s)",
                                  (rigg[tkey], *krav, AKTOR)).fetchone()[0]
            rt.commit()
        a, b = rigg["a"], rigg["b"]
        s: dict[str, str] = {}
        vid = _adresse(mg, a)
        for merke in ("forslag", "ren"):
            s[merke] = _foresla(rt, a, kv["a"], _kolli(rt, a, kv["a"]), vid)
            _eld(mg, a, "transportforslag", "foreslatt_ts", "forslag_id",
                 s[merke])
        s["kolli"] = _kolli(rt, a, kv["a"])
        _eld(mg, a, "kolli", "registrert", "kolli_id", s["kolli"])
        s["ok"] = _foresla(rt, a, kv["a"], _kolli(rt, a, kv["a"]), vid)
        s["kolli_b"] = _kolli(rt, b, kv["b"])
        _eld(mg, b, "kolli", "registrert", "kolli_id", s["kolli_b"])
    finally:
        if egen:
            mg.close()
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Den rene planen forkastes — MELLOM sveipene. Sveipen forkaster
    ingenting selv; den skal se at planen ikke lenger er åpen og lukke
    funnet."""
    _sk(rt, rigg["a"])
    rt.execute("SELECT m28_forkast(%s,%s,%s,%s)",
               (rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"],
                "Kunden avbestilte før henting", AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
