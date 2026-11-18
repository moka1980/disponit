"""M-34s fasitsett: fem kontroller med KJENT dom.

Kontrollregisteret har tre funntyper. To er nåbare gjennom dørene, den
tredje er urepresenterbar med vilje:

  1. `etterproving_forbigatt` — siste etterprøving er eldre enn
                                 intervallet
  2. `kontroll_uten_eier`     — eieren er ikke lenger aktivt medlem
  3. (ren)                    — forbigått, og får en fersk etterprøving
                                 MELLOM kjøringene
  4. (ikke_relevant)          — forbigått, men valgt bort med begrunnelse:
                                 unntaket fra alle tre kandidatene
  5. (rent_b)                 — fersk etterprøving i tenant B, uten funn

MODULEN HAR INGEN TERSKELTABELL. Intervallet står på hver kontroll
(`etterproving_dogn`), og fristen løper fra siste etterprøving.

FORBIGÅTT KREVER EN TILBAKEDATERT ETTERPRØVING. `kontroll.opprettet` er
`DEFAULT now()` og ingen dørparameter, så en kontroll som aldri er
etterprøvd blir først forbigått når intervallet har løpt fra i dag.
`m34_registrer_etterproving` tar derimot datoen, og avviser bare
fremtiden — en etterprøving for 40 døgn siden på et 30-døgnsintervall er
forbigått i dag.

`kontroll_uten_eier` KREVER DIREKTE DML, som i M-18: det finnes ingen dør
som deaktiverer et medlemskap. Riggen skriver i `brukermedlemskap`, en
annen tabell enn den modulen måles på.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m34-fasit"
EVIDENSKILDE = "m34_compliance"
FUNNTABELL = "kontrollfunn"
SUBJEKTKOLONNE = "kontroll_id"

UNAABARE: dict[str, str] = {
    "oppfylt_uten_evidens":
        "urepresenterbar gjennom hver skrivevei med vilje: statusen "
        "`oppfylt` settes bare av `m34_registrer_etterproving`, som samtidig "
        "skriver datoen og evidenshenvisningen fra samme rad, og vakten i §2 "
        "nekter enhver annen vei. Sveipen stiller spørsmålet likevel hver "
        "natt for å fange en skriver utenom vakten — en rigg som kunne "
        "produsere funnet, ville bevist at vakten var brutt.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ikke_relevant"

RAMMEVERK = ("Fasitrammeverk", "1")

#: (merkelapp, forventet funntype eller None, tenantnøkkel,
#:  etterprøvingsintervall i døgn, etterprøvd for N døgn siden eller None,
#:  egen eier som deaktiveres?, markeres ikke_relevant?)
SETT: tuple[tuple[str, str | None, str, int, int | None, bool, bool],
            ...] = (
    # 40 døgn siden på et 30-døgnsintervall: ti døgn over fristen.
    ("forbigatt", "etterproving_forbigatt", "a", 30, 40, False, False),
    # Den RENE fødes forbigått, som `forbigatt`: funnet den får i første
    # sveip er nettopp det andre sveip skal lukke.
    ("ren", None, "a", 30, 40, False, False),
    # Forbigått på samme måte — men VALGT BORT. `ikke_relevant` unntar
    # kontrollen fra alle tre kandidatene, og det er unntaket som måles.
    ("ikke_relevant", None, "a", 30, 40, False, True),
    # UTEN AKTIV EIER: aldri etterprøvd, men registrert i dag med et
    # intervall på 365 døgn, så den kan ikke være forbigått ved siden av.
    # Eieren er EGEN — ellers hadde de andre kontrollene mistet sin også.
    ("uten_eier", "kontroll_uten_eier", "b", 365, None, True, False),
    # RENT I TENANT B: etterprøvd i dag, aktiv eier.
    ("rent_b", None, "b", 30, 0, False, False),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m34fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m34-fasit")


def _bruker(mg, tenant: str, merke: str) -> str:
    """En bruker med AKTIVT medlemskap, skrevet med migratortilkoblingen.

    DET FINNES INGEN DØR — medlemskap er en plattformhandling. Riggen
    skriver derfor i en ANNEN tabell enn den modulen måles på."""
    # TENANTKONTEKST FØRST: `brukermedlemskap` har RLS, også mot
    # migratoren.
    felles.sett_kontekst(mg, tenant, AKTOR, "m34-fasit")
    rad = mg.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES ('https://fasit', %s)"
        " ON CONFLICT (issuer, sub) DO UPDATE SET sub = EXCLUDED.sub"
        " RETURNING bruker_id", (f"{tenant}:{merke}",)).fetchone()
    bid = rad[0]
    mg.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller)"
        " VALUES (%s,%s, ARRAY['sikkerhet'])"
        " ON CONFLICT (tenant, bruker_id) DO UPDATE SET aktiv = true",
        (tenant, bid))
    mg.commit()
    return str(bid)


def _deaktiver(mg, tenant: str, bruker_id: str) -> None:
    felles.sett_kontekst(mg, tenant, AKTOR, "m34-fasit")
    mg.execute("UPDATE brukermedlemskap SET aktiv = false"
               " WHERE tenant=%s AND bruker_id=%s", (tenant, bruker_id))
    mg.commit()


def _etterprov(rt, tenant: str, kid: str, siden: int, revisor: str,
               merke: str) -> None:
    _sk(rt, tenant)
    rt.execute(
        "SELECT m34_registrer_etterproving(%s,%s,%s, current_date - %s,"
        " %s,%s,'oppfylt',NULL,%s)",
        (tenant, uuid.uuid4(), kid, siden, revisor,
         f"fasit:{merke}:{siden}", AKTOR))
    rt.commit()


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN den rene kontrollens ferske
    etterprøving: den kommer mellom kjøringene.

    `mg` er migratortilkoblingen, som trengs til medlemskapene. Den
    generiske produsenten gir den ikke, så riggen åpner sin egen."""
    import os
    import psycopg
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "eiere": {}, "revisorer": {}}
    try:
        felles_eier = {t: _bruker(mg, rigg[t], "eier") for t in ("a", "b")}
        rigg["revisorer"] = {t: _bruker(mg, rigg[t], "revisor")
                             for t in ("a", "b")}
        for (merke, _v, tkey, dogn, siden, egen_eier,
             ikke_relevant) in SETT:
            tenant = rigg[tkey]
            eier = (_bruker(mg, tenant, merke) if egen_eier
                    else felles_eier[tkey])
            kid = str(uuid.uuid4())
            _sk(rt, tenant)
            rt.execute(
                "SELECT m34_registrer_kontroll(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, kid, RAMMEVERK[0], RAMMEVERK[1], f"F.{merke}",
                 f"Fasitkontroll {merke}", eier, dogn, AKTOR))
            rt.commit()
            if siden is not None:
                _etterprov(rt, tenant, kid, siden,
                           rigg["revisorer"][tkey], merke)
            if ikke_relevant:
                _sk(rt, tenant)
                rt.execute("SELECT m34_marker_ikke_relevant(%s,%s,%s,%s)",
                           (tenant, kid, "fasit: valgt bort", AKTOR))
                rt.commit()
            if egen_eier:
                _deaktiver(mg, tenant, eier)
            rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": kid}
            rigg["eiere"][merke] = eier
    finally:
        if egen:
            mg.close()
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Etterprøvingen som gjør den rene kontrollen ren — MELLOM sveipene.

    `sist_etterprovd` avledes av den SENESTE etterprøvingen, så en
    etterprøving i dag flytter fristen 30 døgn fram."""
    _etterprov(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"], 0,
               rigg["revisorer"]["a"], RENSES)


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _d, _s, _e, _i in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
