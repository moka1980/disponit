"""M-32s fasitsett: fire subjekter i tre tenanter med KJENT dom.

Skatteregisteret har ni funntyper på `referanse`. Fire er utelukket av
datamodellen, tre måler LANDPAKKENE — plattformdommer felt i migrasjon —
og `krav_mangler` har ingen skriver (se `UNAABARE`). Den siste,
`stor_vurdering_ukontrollert`, rigges på `vurdering_id`:

  tenant a (kontrollgrense 1 000 øre, frist 1 døgn)
    1. `stor_vurdering_ukontrollert` — 500 000 øre, eldet ti døgn
    2. (ok)                          — 500 øre, eldet ti døgn: under
                                       grensen
  tenant b (kontrollgrense 2 000 øre, frist 2 døgn)
    3. `stor_vurdering_ukontrollert` — samme regel
  tenant c (kontrollgrense 1 500 øre, frist 1 døgn)
    4. (ren)                         — som 1; GRENSEN HEVES mellom
                                       kjøringene

DEN RENE BOR I EGEN TENANT. Modulen kan ikke rette en beregning, og en
vurdering kan ikke «kontrolleres» bort: funnet lukkes bare når tenanten
hever grensen sin. Det ville lukket alle funn i samme tenant.

ALDEREN SETTES SOM MIGRATOR, testsuitens vei: `beregnet_ts` har DEFAULT
now() og settes aldri av kalleren, og `test_m32_skatt.py` flytter den med
en vanlig UPDATE (M-12/M-40-presedensen). Ingen vakt skrus av. Adressen
skrives også som migrator, som i suiten: M-19s dører er ikke M-32s.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import uuid
from pathlib import Path

import psycopg

import sveipfasit_felles as felles

AKTOR = "m32-fasit"
EVIDENSKILDE = "m32_skatt"
FUNNTABELL = "skattefunn"
SUBJEKTKOLONNE = "referanse"

UNAABARE: dict[str, str] = {
    "transaksjon_uten_jurisdiksjon":
        "`jurisdiksjonsvurdering.jurisdiksjon` er NOT NULL og utledes av "
        "beregningsdøra fra adresseversjonen; en vurdering uten jurisdiksjon "
        "kan ikke settes inn.",
    "sats_uten_regelversjon":
        "`landpakke_regelversjon` er NOT NULL med fremmednøkkel mot "
        "landpakken døra leste; en sats uten regelversjon er "
        "urepresenterbar.",
    "sats_uten_komplett_landpakke":
        "beregningsdøra nekter et land uten en gjeldende, komplett "
        "landpakke for transaksjonsdatoen; tilstanden kan ikke oppstå i "
        "registeret.",
    "landpakke_endret_gjennom_dor":
        "ingen rolle har INSERT, UPDATE eller DELETE på `landpakke` og "
        "modulen har ingen dør som skriver den; landpakker felles i "
        "migrasjon, så funnet kan ikke reises.",
    "landpakke_utloper_snart":
        "regelen måler `landpakke.gyldig_til`, en plattformdom felt i "
        "migrasjon og felles for alle kunder; riggen endrer ikke "
        "plattformdommer, og suiten gjør det bare i den isolerte basen.",
    "landpakke_uten_sats":
        "regelen måler om en gjeldende landpakke mangler satser — "
        "plattformdata felt i migrasjon, ikke tenantens; riggen kan ikke "
        "fjerne en sats uten å endre grunnlaget for alle kunder.",
    "jurisdiksjon_uten_pakke":
        "funnet oppstår bare når en landpakke utløper ETTER at en vurdering "
        "er regnet under den; døra nekter et land uten gjeldende pakke, og "
        "riggen endrer ikke plattformdommer.",
    "krav_mangler":
        "sveipens løkke går bare over tenanter som HAR et krav "
        "(`SELECT DISTINCT tenant FROM skattekrav`), så typen har ingen "
        "skriver; en tenant uten krav besøkes aldri.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

SETT: tuple[tuple[str, str | None, str, int], ...] = (
    ("stor", "stor_vurdering_ukontrollert", "a", 500_000),
    ("ok", None, "a", 500),
    ("stor_b", "stor_vurdering_ukontrollert", "b", 500_000),
    ("ren", None, "c", 500_000),
)
#: (selgerland, manuell_kontroll_over_ore, kontrollfrist_dogn)
KRAV = {"a": ("NO", 1_000, 1), "b": ("NO", 2_000, 2), "c": ("NO", 1_500, 1)}
KRAV_C_HEVET = ("NO", 9_000_000, 1)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m32fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m32-fasit")


def _krav(rt, tenant: str, krav: tuple) -> int:
    _sk(rt, tenant)
    v = rt.execute("SELECT m32_sett_krav(%s,%s,%s,%s,%s)",
                   (tenant, *krav, AKTOR)).fetchone()[0]
    rt.commit()
    return v


def _adresse(mg, tenant: str) -> str:
    """En adresseversjon å lese landet fra — suitens `_adresse`."""
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
    mg.commit()
    return vid


def _beregn(rt, tenant: str, kv: int, vid: str, belop: int) -> str:
    uid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m32_beregn(%s,%s,%s,%s,%s,'standard',%s,"
               "current_date,%s)",
               (tenant, uid, f"tx-{secrets.token_hex(4)}", kv, vid, belop,
                AKTOR))
    rt.commit()
    return uid


def forbered(rt, runde: str, mg=None) -> dict:
    """Alle tre tenantene — men UTEN hevingen av grensen i c: den kommer
    mellom kjøringene."""
    rigg: dict = {t: tenantnavn(runde, t) for t in KRAV}
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    try:
        kv = {t: _krav(rt, rigg[t], krav) for t, krav in KRAV.items()}
        vid = {t: _adresse(mg, rigg[t]) for t in KRAV}
        s: dict[str, str] = {}
        for merke, _f, tkey, belop in SETT:
            t = rigg[tkey]
            s[merke] = _beregn(rt, t, kv[tkey], vid[tkey], belop)
            _sk(mg, t)
            mg.execute("UPDATE jurisdiksjonsvurdering SET beregnet_ts ="
                       " now() - INTERVAL '10 days' WHERE tenant = %s"
                       " AND vurdering_id = %s", (t, s[merke]))
            mg.commit()
    finally:
        if egen:
            mg.close()
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg[t] for t in KRAV]


def kontroller_ren(rt, rigg: dict) -> None:
    """Tenant c HEVER kontrollgrensen — MELLOM sveipene. Sveipen endrer
    ingen grense selv; den skal måle mot den som gjelder nå og lukke
    funnet."""
    _krav(rt, rigg["c"], KRAV_C_HEVET)


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _b in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
