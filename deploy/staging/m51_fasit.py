"""M-51s fasitsett: åtte støtteordninger med KJENT dom.

Tilskuddsregisteret har sju funntyper, alle på `ordning_id`. Hver aktiv
ordning har en søknadsfrist, og to av typene leser den — så ordninger
som skal måle noe annet har fristen om 200 døgn, langt utenfor varselet
på 21. Et estimat med poster som ikke er ferdigstilt er den nøytrale
tilstanden: det gir verken `ingen_estimat` eller `estimat_uten_poster`.

  1. `ingen_estimat`            — aktiv ordning uten estimat
  2. `estimat_uten_poster`      — estimat uten et eneste beløp
  3. `estimat_over_ordningstak` — sum 500 kr mot et tak på 300
  4. `frist_naermer_seg`        — frist om 5 døgn, estimatet ikke klart
  5. `frist_passert`            — frist for 5 døgn siden, ikke klart
  6. `ingen_krav`               — tenant B har ingen krav
  7. (ren)                      — uten estimat, får estimat og post
                                  MELLOM kjøringene
  8. (beholdes)                 — estimat med post, frist om 200 døgn

Søknadsfristen er en dørparameter uten nedre grense, så både nær og
passert frist lages på dagen.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m51-fasit"
EVIDENSKILDE = "m51_tilskudd"
FUNNTABELL = "tilskuddsfunn"
SUBJEKTKOLONNE = "ordning_id"

UNAABARE: dict[str, str] = {
    "utdatert_kildepost":
        "kandidaten krever at en kildepost er registrert for mer enn "
        "`kildepost_gyldig_dogn` siden, og gulvet for den terskelen er ett "
        "døgn (CHECK BETWEEN 1 AND 3650). `kildepost.registrert` er DEFAULT "
        "now() og ingen dørparameter, og `m51_legg_til_post` nekter i "
        "tillegg å knytte en post som alt er utdatert. Funnet kan derfor "
        "ikke lages på én dag uten å skrive utenom dørene.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "beholdes"

#: Kravene i tenant A: fristvarsel 21 døgn, kildepost gyldig 400,
#: usikkerhet 20 %.
KRAV = (21, 400, 20)

#: (merkelapp, forventet, tenantnøkkel, frist om N døgn, tak i øre eller
#:  None, estimat: "nei" | "tom" | "post")
SETT: tuple[tuple[str, str | None, str, int, int | None, str], ...] = (
    ("ingen_estimat", "ingen_estimat", "a", 200, None, "nei"),
    ("uten_poster", "estimat_uten_poster", "a", 200, None, "tom"),
    ("over_tak", "estimat_over_ordningstak", "a", 200, 30_000, "post"),
    ("naermer", "frist_naermer_seg", "a", 5, None, "post"),
    ("passert", "frist_passert", "a", -5, None, "post"),
    ("uten_krav", "ingen_krav", "b", 200, None, "nei"),
    # Den RENE fødes uten estimat, som `ingen_estimat`: funnet den får i
    # første sveip er nettopp det andre sveip skal lukke.
    ("ren", None, "a", 200, None, "nei"),
    ("beholdes", None, "a", 200, None, "post"),
)

#: Beløpet i hver kildepost og andelen hver post tar av den (øre).
KILDEBELOP = 100_000
ANDEL = 50_000


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m51fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m51-fasit")


def _estimat_med(rt, tenant: str, oid: str, merke: str, *,
                 med_post: bool) -> str:
    """Et estimat over de siste 180 døgnene — med én post, eller uten."""
    eid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m51_opprett_estimat(%s,%s,%s, current_date - 180,"
               " current_date - 1, %s)", (tenant, eid, oid, AKTOR))
    rt.commit()
    if med_post:
        kid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute(
            "SELECT m51_registrer_kildepost(%s,%s,'lonn',%s,%s,%s,"
            " current_date - 180, current_date - 1, %s)",
            (tenant, kid, f"KP-{merke}", f"Fasit {merke}", KILDEBELOP,
             AKTOR))
        rt.commit()
        _sk(rt, tenant)
        rt.execute("SELECT m51_legg_til_post(%s,%s,%s,%s,%s,%s,%s)",
                   (tenant, str(uuid.uuid4()), eid, kid, ANDEL,
                    f"fasit {merke}", AKTOR))
        rt.commit()
    return eid


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN den rene ordningens estimat: det kommer
    mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}}
    _sk(rt, rigg["a"])
    rt.execute("SELECT m51_sett_krav(%s,%s,%s,%s,%s,%s)",
               (rigg["a"], *KRAV, AKTOR, f"m51fasit-krav-{runde}"))
    rt.commit()
    for merke, _v, tkey, frist, tak, estimat in SETT:
        tenant = rigg[tkey]
        oid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute(
            "SELECT m51_registrer_ordning(%s,%s,%s,%s,'Forskningsradet',"
            "'v1',%s,%s,NULL, now() + make_interval(days => %s), %s)",
            (tenant, oid, f"ORD-{merke}-{runde}", f"Fasit {merke}",
             hashlib.sha256(f"{runde}:{merke}".encode()).hexdigest(),
             tak, frist, AKTOR))
        rt.commit()
        if estimat != "nei":
            _estimat_med(rt, tenant, oid, merke,
                         med_post=(estimat == "post"))
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": oid}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Estimatet med post som gjør den rene ordningen ren — MELLOM
    sveipene. Fristen er om 200 døgn, så ingen frist slår inn."""
    _estimat_med(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"],
                 RENSES, med_post=True)


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _fr, _tak, _e in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
