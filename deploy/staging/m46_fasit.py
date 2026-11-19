"""M-46s fasitsett: ni anbud i to tenanter med KJENT dom.

Anbudsregisteret har åtte funntyper, alle på `anbud_id`. Hvert anbud
som skal måle noe ANNET enn dekningen, har ett absolutt krav som er
dekket av et punkt — ellers ville det også fått `ingen_krav_registrert`
eller `udekket_absolutt_krav`. Frister som ikke skal måles ligger om
200 døgn, langt utenfor varselet på 14.

  tenant A (profil: NACE 62.010, varsel 14 døgn)
    1. `frist_naermer_seg`      — frist om 5 døgn, utkast ikke klart
    2. `frist_passert`          — frist for 5 døgn siden
    3. `udekket_absolutt_krav`  — et absolutt krav uten punkt
    4. `udekket_krav`           — et vektet krav uten punkt
    5. `ingen_krav_registrert`  — anbud uten krav
    6. `utenfor_profil`         — NACE 41.200, utenfor profilen
    7. (ren)                    — som 3; kravet dekkes MELLOM kjøringene
    8. (ok)                     — dekket krav, frist om 200 døgn
  tenant B (ingen profil)
    9. `ingen_profil`
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m46-fasit"
EVIDENSKILDE = "m46_anbud"
FUNNTABELL = "anbudsfunn"
SUBJEKTKOLONNE = "anbud_id"

UNAABARE: dict[str, str] = {
    "utlopt_kilde":
        "kandidaten krever et punkt som viser til en kilde som har utløpt, "
        "men `m46_registrer_punkt` nekter å knytte en kilde som alt er "
        "ugyldig. Kilden må altså utløpe ETTER punktet: enten ved "
        "`gyldig_til` (tidligst i morgen, siden den må gjelde i dag) eller "
        "ved `kilde_gyldig_dogn`, som har gulv ett døgn og måles fra "
        "`registrert` (DEFAULT now()). Ingen av veiene gir funnet på én dag.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

PROFIL = (["62.010"], ["Oslo"], 0, 100_000_000_000, 14, 365)

#: (merkelapp, forventet, tenantnøkkel, frist om N døgn, NACE,
#:  krav: None | "absolutt" | "vektet", dekket av et punkt?)
SETT: tuple[tuple[str, str | None, str, int, str, str | None, bool],
            ...] = (
    ("naermer", "frist_naermer_seg", "a", 5, "62.010", "absolutt", True),
    ("passert", "frist_passert", "a", -5, "62.010", "absolutt", True),
    ("udekket_abs", "udekket_absolutt_krav", "a", 200, "62.010",
     "absolutt", False),
    ("udekket", "udekket_krav", "a", 200, "62.010", "vektet", False),
    ("uten_krav", "ingen_krav_registrert", "a", 200, "62.010", None, False),
    ("utenfor", "utenfor_profil", "a", 200, "41.200", "absolutt", True),
    # Den RENE fødes med et udekket absolutt krav: funnet den får i første
    # sveip er nettopp det andre sveip skal lukke.
    ("ren", None, "a", 200, "62.010", "absolutt", False),
    ("ok", None, "a", 200, "62.010", "absolutt", True),
    ("uten_profil", "ingen_profil", "b", 200, "62.010", None, False),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m46fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m46-fasit")


def _punkt(rt, tenant: str, utkast: str, krav: str, kilde: str) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT m46_registrer_punkt(%s,%s,%s,%s,%s,"
               "'ISO 9001-sertifikat gyldig','s. 1',%s)",
               (tenant, str(uuid.uuid4()), utkast, krav, kilde, AKTOR))
    rt.commit()


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN punktet som dekker den rene: det kommer
    mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "utkast": {}, "krav": {}}
    a = rigg["a"]
    _sk(rt, a)
    rt.execute("SELECT m46_sett_profil(%s,%s,%s,%s,%s,%s,%s,%s)",
               (a, *PROFIL, AKTOR))
    rt.commit()
    kilde = str(uuid.uuid4())
    _sk(rt, a)
    rt.execute("SELECT m46_registrer_kilde(%s,%s,'ISO 9001','sertifikat',"
               " current_date + 365, %s, %s)",
               (a, kilde, hashlib.sha256(runde.encode()).hexdigest(), AKTOR))
    rt.commit()
    rigg["kilde"] = kilde

    for merke, _v, tkey, frist, nace, krav, dekket in SETT:
        tenant = rigg[tkey]
        aid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute("SELECT m46_registrer_anbud(%s,%s,%s,'doffin',%s,"
                   "'Oslo kommune',%s,'Oslo',500000000,"
                   " now() + make_interval(days => %s), %s)",
                   (tenant, aid, f"DOF-{merke}-{runde}", f"Fasit {merke}",
                    nace, frist, AKTOR))
        rt.commit()
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": aid}
        if krav is None:
            continue
        kid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute("SELECT m46_registrer_krav(%s,%s,%s,'K-1','ISO 9001',"
                   "'sertifisering',%s,%s)",
                   (tenant, kid, aid, krav == "absolutt", AKTOR))
        rt.commit()
        uid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute("SELECT m46_opprett_utkast(%s,%s,%s,%s)",
                   (tenant, uid, aid, AKTOR))
        rt.commit()
        rigg["utkast"][merke], rigg["krav"][merke] = uid, kid
        if dekket:
            _punkt(rt, tenant, uid, kid, kilde)
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Punktet som dekker den rene anbudets absolutte krav — MELLOM
    sveipene."""
    _punkt(rt, rigg["a"], rigg["utkast"][RENSES], rigg["krav"][RENSES],
           rigg["kilde"])


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _fr, _n, _k, _d in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
