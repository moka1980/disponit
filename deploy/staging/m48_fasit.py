"""M-48s fasitsett: åtte motparter i tre tenanter med KJENT dom.

Motpartsregisteret har åtte funntyper, alle på `motpart_id`.

  tenant A (uvurdert etter 0 døgn)
    1. `uvurdert_motpart`       — registrert, aldri vurdert
    2. (ren)                    — som 1; får oppslag, profil og vurdering
                                  MELLOM kjøringene
  tenant B (ingen krav)
    3. `ingen_krav`
  tenant C (uvurdert etter 3650 døgn, tak 10 000 kr)
    4. `profil_uten_vurdering`  — profil fra registeret, ingen vurdering
    5. `motpart_avviklet`       — profilen sier «avviklet»; vurdert
    6. `forslag_over_tak`       — vurdert med 20 000 kr mot tak 10 000
    7. `gjentatte_oppslagsfeil` — fem feilede oppslag siste døgn
    8. (ok)                     — aktiv profil, vurdert under taket

TO TENANTER MED KRAV, FORDI TO TERSKLER STRIDER. Med `uvurdert_dogn` 0
er hver motpart uten vurdering også uvurdert — også den som skal måle
`profil_uten_vurdering`. A måler uvurdert, C alt det andre.

OPPSLAGENE GÅR GJENNOM DØRENE, ikke mot Brønnøysund: riggen reserverer
og fullfører selv, med svarstatusen den trenger. `oppslag_ferskhet_timer`
er 0, så samme motpart kan slås opp fem ganger på rad.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m48-fasit"
EVIDENSKILDE = "m48_motpart"
FUNNTABELL = "motpartsfunn"
SUBJEKTKOLONNE = "motpart_id"

UNAABARE: dict[str, str] = {
    "utdatert_vurdering":
        "kandidaten krever at siste vurdering er eldre enn "
        "`vurdering_gyldig_dogn`, og gulvet for den terskelen er ett døgn "
        "(CHECK BETWEEN 1 AND 3650). `motpartsvurdering.vurdert` er "
        "DEFAULT now() og ingen dørparameter, så en vurdering kan ikke være "
        "utdatert samme dag som den ble gjort.",
    "oppslag_uten_svar":
        "kandidaten krever en reservasjon som har stått ubesvart i mer enn "
        "én time — grensen er HARDKODET i sveipen (`interval '1 hour'`), "
        "ikke en terskel, og `foretaksoppslag.reservert` er DEFAULT now(). "
        "En rigg som skulle vente en time, ville målt klokka, ikke modulen.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

#: (ferskhet timer, vurdering gyldig døgn, uvurdert døgn, tak øre,
#:  godkjente grunnlag)
KRAV_A = (0, 180, 0, 50_000_000, ["foretaksregister"])
KRAV_C = (0, 180, 3650, 1_000_000, ["foretaksregister"])

#: (merkelapp, forventet, tenantnøkkel, profilstatus eller None,
#:  vurdering i øre eller None, feilede oppslag)
SETT: tuple[tuple[str, str | None, str, str | None, int | None, int],
            ...] = (
    ("uvurdert", "uvurdert_motpart", "a", None, None, 0),
    # Den RENE fødes uvurdert: funnet den får i første sveip er nettopp
    # det andre sveip skal lukke.
    ("ren", None, "a", None, None, 0),
    ("uten_krav", "ingen_krav", "b", None, None, 0),
    ("profil_uten", "profil_uten_vurdering", "c", "aktiv", None, 0),
    ("avviklet", "motpart_avviklet", "c", "avviklet", 500_000, 0),
    ("over_tak", "forslag_over_tak", "c", "aktiv", 2_000_000, 0),
    ("feil", "gjentatte_oppslagsfeil", "c", None, None, 5),
    ("ok", None, "c", "aktiv", 500_000, 0),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m48fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m48-fasit")


def _oppslag(rt, tenant: str, mid: str, status: str) -> str:
    oid = str(uuid.uuid4())
    vert = rt.execute("SELECT m48_registrert_vert()").fetchone()[0]
    rt.rollback()
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m48_reserver_oppslag(%s,%s,%s,%s,"
               "'kredittvurdering','personvernforordningen art 6.1.f',%s)",
               (tenant, oid, mid, vert, AKTOR))
    rt.commit()
    _sk(rt, tenant)
    rt.execute("SELECT m48_fullfor_oppslag(%s,%s,%s,%s,%s)",
               (tenant, oid, status, "a" * 64 if status == "treff" else None,
                AKTOR))
    rt.commit()
    return oid


def _profil_og_vurdering(rt, tenant: str, mid: str, status: str,
                         ore: int | None) -> None:
    oid = _oppslag(rt, tenant, mid, "treff")
    vid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m48_registrer_versjon(%s,%s,%s,%s,'foretaksregister',"
               "current_date::text,'FASIT AS','AS',%s,false,false,"
               "current_date,%s)", (tenant, vid, mid, oid, status, AKTOR))
    rt.commit()
    if ore is not None:
        _sk(rt, tenant)
        rt.execute("SELECT m48_registrer_vurdering(%s,%s,%s,"
                   "'foretaksregister',%s,'Fasitvurdering',%s)",
                   (tenant, str(uuid.uuid4()), vid, ore, AKTOR))
        rt.commit()


def forbered(rt, runde: str) -> dict:
    """Alle tre tenantene — men UTEN den rene motpartens profil og
    vurdering: de kommer mellom kjøringene."""
    rigg = {t: tenantnavn(runde, t) for t in ("a", "b", "c")}
    rigg["subjekter"] = {}
    for tkey, krav in (("a", KRAV_A), ("c", KRAV_C)):
        _sk(rt, rigg[tkey])
        rt.execute("SELECT m48_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
                   (rigg[tkey], *krav, AKTOR))
        rt.commit()
    for merke, _v, tkey, status, ore, feil in SETT:
        tenant = rigg[tkey]
        mid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute("SELECT m48_registrer_motpart(%s,%s,%s,%s,%s)",
                   (tenant, mid, f"9{secrets.randbelow(10**8):08d}",
                    f"Fasit {merke} AS", AKTOR))
        rt.commit()
        if status:
            _profil_og_vurdering(rt, tenant, mid, status, ore)
        for _ in range(feil):
            _oppslag(rt, tenant, mid, "feil")
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": mid}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"], rigg["c"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Oppslag, profil og vurdering under taket — MELLOM sveipene."""
    _profil_og_vurdering(rt, rigg["a"],
                         rigg["subjekter"][RENSES]["subjekt_id"], "aktiv",
                         500_000)


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _s, _o, _fe in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
