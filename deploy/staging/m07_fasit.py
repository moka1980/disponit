"""M-07s fasitsett: seks subjekter i to tenanter med KJENT dom.

Møteregisteret har fire funntyper på `referanse` (møte-, aksjons- eller
punkt-id som tekst). Sveipen kan reise tre av dem; den fjerde er
utelukket av datamodellen (se `UNAABARE`). Kravene er satt så lavt
dørene tillater: referat- og aksjonsfrist 1 døgn, sikkerhetsterskel 7000
basispunkter.

  tenant A
    1. `mote_uten_referat`         — møtet sluttet for 5 døgn siden
    2. `aksjon_over_frist`         — frist for 5 døgn siden, på et møte
                                     med et sikkert referatpunkt
    3. `ubekreftet_punkt_uavklart` — punkt ført med 1000 bp sikkerhet
    4. (ren)                       — som 1; får et referatpunkt MELLOM
                                     kjøringene
    5. (ok)                        — møtet sluttet i går for 5 døgn siden
                                     og har et sikkert referatpunkt
  tenant B
    6. `mote_uten_referat`         — samme regel i en annen tenant

DØRENE TAR DATOENE: møtets start og slutt og aksjonens frist er
parametre, så riggen trenger verken bakdatering eller å vente et døgn.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m07-fasit"
EVIDENSKILDE = "m07_moteoperasjon"
FUNNTABELL = "motefunn"
SUBJEKTKOLONNE = "referanse"

UNAABARE: dict[str, str] = {
    "opptak_uten_hjemmel":
        "`moteopptak.hjemmel_id` er NOT NULL med fremmednøkkel mot "
        "opptakshjemmel, og `m7_start_opptak` nekter før raden finnes. "
        "Et opptak uten hjemmel er urepresenterbart; at sveipen aldri "
        "reiser typen er beviset på at vernet ligger i datamodellen.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

#: (merkelapp, forventet, tenantnøkkel)
SETT: tuple[tuple[str, str | None, str], ...] = (
    ("uten_referat", "mote_uten_referat", "a"),
    ("aksjon", "aksjon_over_frist", "a"),
    ("ubekreftet", "ubekreftet_punkt_uavklart", "a"),
    # Den RENE fødes uten referat: funnet den får i første sveip er
    # nettopp det andre sveip skal lukke, når referatet er skrevet.
    ("ren", None, "a"),
    ("ok", None, "a"),
    ("uten_referat_b", "mote_uten_referat", "b"),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m07fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m07-fasit")


def _mote(rt, tenant: str, merke: str) -> str:
    mid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m7_registrer_mote(%s,%s,%s,"
               " now() - interval '5 days 1 hour', now() - interval '5 days',"
               " %s, ARRAY['fasit@x.test'], %s, %s)",
               (tenant, mid, f"Fasitmøte {merke}", AKTOR, "Agenda", AKTOR))
    rt.commit()
    return mid


def _punkt(rt, tenant: str, mid: str, sikkerhet: int) -> str:
    pid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m7_registrer_referatpunkt(%s,%s,%s,1,"
               "'Fasitpunkt','manuell','fasit',%s,NULL,%s)",
               (tenant, pid, mid, sikkerhet, AKTOR))
    rt.commit()
    return pid


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN referatet til det rene møtet: det
    kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}}
    for tkey in ("a", "b"):
        _sk(rt, rigg[tkey])
        rt.execute("SELECT * FROM m7_sett_krav(%s,1,1,7000,%s,%s)",
                   (rigg[tkey], AKTOR, f"fasit-{runde}-{tkey}"))
        rt.commit()
    a, b = rigg["a"], rigg["b"]
    s = rigg["subjekter"]
    s["uten_referat"] = _mote(rt, a, "uten_referat")
    m_aksjon = _mote(rt, a, "aksjon")
    _punkt(rt, a, m_aksjon, 9000)
    aid = str(uuid.uuid4())
    _sk(rt, a)
    rt.execute("SELECT * FROM m7_registrer_aksjon(%s,%s,%s,'Fasitaksjon',"
               "'eier@x.test', current_date - 5, %s)",
               (a, aid, m_aksjon, AKTOR))
    rt.commit()
    s["aksjon"] = aid
    m_ub = _mote(rt, a, "ubekreftet")
    s["ubekreftet"] = _punkt(rt, a, m_ub, 1000)
    s["ren"] = _mote(rt, a, "ren")
    m_ok = _mote(rt, a, "ok")
    _punkt(rt, a, m_ok, 9000)
    s["ok"] = m_ok
    s["uten_referat_b"] = _mote(rt, b, "uten_referat_b")
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Referatet til det rene møtet skrives — MELLOM sveipene. Døra rører
    ikke funnet; sveipen må se at møtet har fått referat og lukke."""
    _punkt(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"], 9000)


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
