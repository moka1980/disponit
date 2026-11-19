"""M-30s fasitsett: seks personvernsaker i to tenanter med KJENT dom.

Personvernregisteret har tre funntyper, alle på `sak_id`. To av dem
gjelder fristen og én gjelder lagrene saken skal ryddes i — så en sak
som skal måle fristen må HA et lager, og en sak som skal måle lagrene må
ha fristen langt unna (den ordinære fristen er mottatt + én måned, og
varselvinduet er 7 døgn).

  tenant A
    1. `frist_oversittet`  — mottatt for 40 døgn siden, har lager
    2. `frist_naermer_seg` — fristen om ca. 3 døgn, har lager
    3. `sak_uten_lagre`    — mottatt i dag, ingen lagre
    4. (ren)               — som 1; fristen FORLENGES mellom kjøringene
    5. (ok)                — mottatt i dag, har lager
  tenant B
    6. `sak_uten_lagre`    — samme regel i en annen tenant

Lagrene er M-4s: `personvernsak_lager` nekter et lager som ikke står i
`retensjonslager` med klassen persondata. Riggen bruker det første som
finnes. Eieren må være et aktivt medlem; medlemskapet skrives med
migratortilkoblingen (ingen dør, som i M-18).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m30-fasit"
EVIDENSKILDE = "m30_personvern"
FUNNTABELL = "personvernfunn"
SUBJEKTKOLONNE = "sak_id"

UNAABARE: dict[str, str] = {}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

#: (merkelapp, forventet, tenantnøkkel, mottatt: SQL-uttrykk, har lager)
SETT: tuple[tuple[str, str | None, str, str, bool], ...] = (
    ("oversittet", "frist_oversittet", "a", "current_date - 40", True),
    ("naermer", "frist_naermer_seg", "a",
     "(current_date - interval '1 month')::date + 3", True),
    ("uten_lagre", "sak_uten_lagre", "a", "current_date", False),
    # Den RENE fødes med fristen oversittet: funnet den får i første sveip
    # er nettopp det andre sveip skal lukke, når fristen er forlenget.
    ("ren", None, "a", "current_date - 40", True),
    ("ok", None, "a", "current_date", True),
    ("uten_lagre_b", "sak_uten_lagre", "b", "current_date", False),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m30fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m30-fasit")


def _eier(mg, tenant: str) -> str:
    felles.sett_kontekst(mg, tenant, AKTOR, "m30-fasit")
    bid = mg.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES ('https://fasit', %s)"
        " ON CONFLICT (issuer, sub) DO UPDATE SET sub = EXCLUDED.sub"
        " RETURNING bruker_id", (f"{tenant}:eier",)).fetchone()[0]
    mg.execute("INSERT INTO brukermedlemskap (tenant, bruker_id, roller)"
               " VALUES (%s,%s, ARRAY['sikkerhet'])"
               " ON CONFLICT (tenant, bruker_id) DO UPDATE SET aktiv = true",
               (tenant, bid))
    mg.commit()
    return str(bid)


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN svaret på den rene saken: det kommer
    mellom kjøringene."""
    import psycopg
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}}
    try:
        # Registeret er M-4s og eies av lagerrollen; migratoren er medlem
        # UTEN arv, så rollen tas lokalt for ett oppslag.
        mg.execute("SET LOCAL ROLE disponit_lager_eier")
        lager = mg.execute("SELECT lager_id FROM retensjonslager"
                           " WHERE klasse = 'persondata'"
                           " ORDER BY lager_id LIMIT 1").fetchone()[0]
        mg.rollback()
        eiere = {t: _eier(mg, rigg[t]) for t in ("a", "b")}
        for merke, _v, tkey, mottatt, har_lager in SETT:
            tenant = rigg[tkey]
            sid = str(uuid.uuid4())
            _sk(rt, tenant)
            rt.execute(
                f"SELECT m30_registrer_sak(%s,%s,'innsyn',%s, {mottatt},"
                " %s,%s,%s)",
                (tenant, sid, f"subjekt-{merke}-{runde}", eiere[tkey],
                 [lager] if har_lager else [], AKTOR))
            rt.commit()
            rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": sid}
    finally:
        if egen:
            mg.close()
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Forlengelsen som gjør den rene saken ren — MELLOM sveipene.

    IKKE et svar: `m30_besvar_sak` lukker funnet i døra selv, og da ville
    sveipen ikke hatt noe å lukke. Forlengelsen rører bare saken, så det
    er sveipen som må se at fristen ikke lenger er passert. Taket er
    mottatt + tre måneder (art. 12 nr. 3); 30 døgn fram er godt innenfor."""
    _sk(rt, rigg["a"])
    rt.execute("SELECT m30_forleng_frist(%s,%s, current_date + 30,"
               " 'Fasit: kompleks forespørsel', %s)",
               (rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"], AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _mo, _l in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
