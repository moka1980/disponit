"""M-12s fasitsett: fem tilganger i to tenanter med KJENT dom.

Tilgangsregisteret har fire funntyper, alle på `tilgang_id`. Sveipen
kan i v1 bare reise én av dem, `gjennomgang_utlopt`. De tre andre er
utelukket av skjemaet (se `UNAABARE`).

  tenant A
    1. `gjennomgang_utlopt` — opprettet for 400 døgn siden, frist 1 døgn
    2. (ren)                — som 1; GJENNOMGÅS mellom kjøringene
    3. (ok)                 — registrert i dag gjennom døra, frist 365
  tenant B
    4. `gjennomgang_utlopt` — samme regel i en annen tenant
    5. (ok)                 — som 3

BAKDATERINGEN SKJER VED INNSETTINGEN, som eieren — og det er ikke en
snarvei. `opprettet_dato` er DEFAULT current_date, døra tar ingen dato,
og vakten fryser kolonnen. Den eneste veien registeret tillater til en
tilgang som er gammel, er å bli født gammel; testsuitens `_forfalt` går
samme vei og begrunner det samme sted. Tilstanden er heller ikke
kunstig: den oppstår av seg selv i drift, når tiden går.

De bakdaterte radene går derfor UTENOM evidenskjeden. Det er riktig:
de simulerer en registrering som skjedde for 400 døgn siden, og den
ville hatt sin hendelse den gang. Evidensen som måles er dørenes egen —
objektene, tilgangene registrert i dag, og gjennomgangen som renser.

Eieren må være et aktivt medlem; medlemskapet skrives med
migratortilkoblingen (ingen dør, som i M-18 og M-30).
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m12-fasit"
EVIDENSKILDE = "m12_tilgang"
FUNNTABELL = "tilgangsfunn"
SUBJEKTKOLONNE = "tilgang_id"

UNAABARE: dict[str, str] = {
    "uten_eier":
        "`tilgang.eier_bruker_id` er NOT NULL med fremmednøkkel mot "
        "brukeridentitet, så registeret kan ikke romme en tilgang uten "
        "eier. Funntypen står i settet for v2, når tilgangene leses inn "
        "fra en identitetsleverandør i stedet for å skrives inn.",
    "uten_hjemmel":
        "`tilgang.hjemmel` er NOT NULL med en CHECK som krever minst ett "
        "tegn som ikke er blanktegn, så en tilgang uten hjemmel avvises "
        "ved innsetting. Funntypen venter på v2s innlesing fra en "
        "identitetsleverandør.",
    "ukjent_objekt":
        "den sammensatte fremmednøkkelen `tilgang_objekt_fk` (tenant, "
        "objekt_id) mot tilgangsobjekt gjør en tilgang til et system "
        "ingen har registrert urepresenterbar. Funntypen venter på v2s "
        "innlesing fra en identitetsleverandør.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

#: (merkelapp, forventet, tenantnøkkel, bakdatert døgn eller None,
#:  gjennomgang_dogn)
SETT: tuple[tuple[str, str | None, str, int | None, int], ...] = (
    ("utlopt", "gjennomgang_utlopt", "a", 400, 1),
    # Den RENE fødes forfalt: funnet den får i første sveip er nettopp
    # det andre sveip skal lukke, når gjennomgangen er gjort.
    ("ren", None, "a", 400, 1),
    ("ok", None, "a", None, 365),
    ("utlopt_b", "gjennomgang_utlopt", "b", 400, 1),
    ("ok_b", None, "b", None, 365),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m12fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m12-fasit")


def _eier(mg, tenant: str) -> str:
    _sk(mg, tenant)
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


def _objekt(rt, tenant: str, runde: str) -> str:
    oid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m12_registrer_objekt(%s,%s,%s,%s,'hoy',%s)",
               (tenant, oid, "Fasitsystem", f"Fasit {runde}", AKTOR))
    rt.commit()
    return oid


def _forfalt(mg, tenant: str, oid: str, eier: str, merke: str,
             dager: int, dogn: int) -> str:
    tid = str(uuid.uuid4())
    _sk(mg, tenant)
    mg.execute("SET LOCAL ROLE disponit_tilgang_eier")
    mg.execute(
        "INSERT INTO tilgang (tenant, tilgang_id, objekt_id, subjekt,"
        " subjekttype, niva, eier_bruker_id, hjemmel, gjennomgang_dogn,"
        " opprettet_dato, opprettet_av)"
        " VALUES (%s,%s,%s,%s,'person','les',%s,'Fasit: rolle R-1',%s,"
        "         current_date - %s, %s)",
        (tenant, tid, oid, f"{merke}@fasit.test", eier, dogn, dager, AKTOR))
    mg.commit()
    return tid


def _registrert(rt, tenant: str, oid: str, eier: str, merke: str,
                dogn: int) -> str:
    tid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m12_registrer_tilgang(%s,%s,%s,%s,'person','les',"
               "%s,'Fasit: rolle R-1',%s,%s)",
               (tenant, tid, oid, f"{merke}@fasit.test", eier, dogn, AKTOR))
    rt.commit()
    return tid


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN gjennomgangen av den rene tilgangen:
    den kommer mellom kjøringene."""
    import psycopg
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}}
    try:
        eiere = {t: _eier(mg, rigg[t]) for t in ("a", "b")}
        objekter = {t: _objekt(rt, rigg[t], runde) for t in ("a", "b")}
        for merke, _v, tkey, dager, dogn in SETT:
            tenant = rigg[tkey]
            if dager is None:
                tid = _registrert(rt, tenant, objekter[tkey], eiere[tkey],
                                  merke, dogn)
            else:
                tid = _forfalt(mg, tenant, objekter[tkey], eiere[tkey],
                               merke, dager, dogn)
            rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": tid}
    finally:
        if egen:
            mg.close()
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Gjennomgangen som gjør den rene tilgangen ren — MELLOM sveipene.

    Døra lukker ikke funnet selv: den flytter bare `sist_gjennomgatt` til
    i dag, så fristen blir i morgen. Det er sveipen som må se at fristen
    ikke lenger er passert, og lukke."""
    _sk(rt, rigg["a"])
    rt.execute("SELECT m12_registrer_gjennomgang(%s,%s,%s)",
               (rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"], AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _d, _g in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
