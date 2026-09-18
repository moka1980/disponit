"""M-13s fasitsett: seks objekter med KJENT dom.

Avstemmingsregisteret har tre funntyper, og alle er nåbare: begge
aldersaksene leser datoer som ER dørparametre (`bokfort` på posten,
`forfall` på bilaget).

  1. `uavstemt_post_over_grense` — bokført forbi grensen, aldri avstemt
  2. `forfalt_bilag_uten_dekning` — forfalt, og ingenting dekker det
  3. `delvis_dekket_bilag`        — forfalt, og bare delvis dekket
  4. (ren)                        — uavstemt post som avstemmes MELLOM
                                    kjøringene
  5. (dekning)                    — posten som dekker delvis-bilaget;
                                    avstemt, altså ingen funn
  6. (kurbilag)                   — bilaget som renser, med forfall fram
                                    i tid; aldri en kandidat

MODULEN HAR INGEN TERSKELTABELL, og derfor heller ingen
`ingen_terskel`-funntype. Grensen er en SVEIPEPARAMETER
(`p_dogn_grense`, standard 30), så settet måles mot modulens egen
standardverdi — den samme arbeideren bruker.

DEN ANDRE TENANTEN er derfor ikke der for en regel om tenanten, slik den
er i M-19 og M-27. Den er der for å måle at sveipen faktisk er
KRYSS-TENANT: det forfalte bilaget ligger alene i tenant B, og en sveip
som bare så den første tenanten ville mistet det.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

#: Sveipens egen grense, som settet måles mot. Pinnet her, ikke lest av
#: modulen: en fasit som leste terskelen av koden ville vært enig med
#: den uansett hva den sto på.
DOGN_GRENSE = 30

AKTOR = "m13-fasit"
EVIDENSKILDE = "m13_avstemming"
FUNNTABELL = "avstemmingsfunn"
SUBJEKTKOLONNE = "objekt_id"

UNAABARE: dict[str, str] = {}
RENSES = "ren"

#: Beløpene. Alle bilag er `inn`, og da krever døra en POSITIV post.
BILAG_DELVIS = 10_000
POST_DEKNING = 4_000
BELOP_REN = 5_000
BILAG_FORFALT = 8_000

#: (merkelapp, forventet funntype eller None, tenantnøkkel, form)
#: Formen er enten ("post", bokført for N døgn siden, beløp) eller
#: ("bilag", forfall om N døgn (negativt = forfalt), beløp).
SETT: tuple[tuple[str, str | None, str, tuple], ...] = (
    # Bokført 60 døgn tilbake, altså godt forbi grensen på 30, og aldri
    # avstemt.
    ("uavstemt", "uavstemt_post_over_grense", "a", ("post", 60, 7_000)),
    # Forfalt for 10 døgn siden. Bilagsgrenen bruker IKKE døgngrensen —
    # én dag forbi forfall er nok.
    ("forfalt", "forfalt_bilag_uten_dekning", "b", ("bilag", -10, BILAG_FORFALT)),
    # Forfalt, og dekket med 4 000 av 10 000.
    ("delvis", "delvis_dekket_bilag", "a", ("bilag", -10, BILAG_DELVIS)),
    # POSTEN SOM DEKKER. Den er bokført 60 døgn tilbake, altså like
    # gammel som den uavstemte — det ENESTE som skiller dem er
    # avstemmingen. Sto den innenfor grensen i stedet, ville raden vært
    # ren uansett, og kontrollen hadde ikke målt noe.
    ("dekning", None, "a", ("post", 60, POST_DEKNING)),
    # Den RENE fødes uavstemt og 60 døgn gammel: funnet den får i første
    # sveip er nettopp det andre sveip skal lukke.
    ("ren", None, "a", ("post", 60, BELOP_REN)),
    # BILAGET SOM RENSER, med forfall FRAM i tid: aldri en kandidat,
    # verken før eller etter at det brukes.
    ("kurbilag", None, "a", ("bilag", 30, BELOP_REN)),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m13fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m13-fasit")


def _konto(rt, tenant: str, merke: str, nr: int) -> str:
    """Én bankkonto. Kontonummeret må ha minst åtte siffer og være unikt
    i tenanten — det hashes, og to like ville blitt avvist."""
    kid = uuid.uuid4()
    _sk(rt, tenant)
    rt.execute("SELECT m13_registrer_konto(%s,%s,%s,%s,'NOK',%s)",
               (tenant, kid, f"Fasitkonto {merke}", f"1234{nr:06d}", AKTOR))
    rt.commit()
    return str(kid)


def lag_objekt(rt, tenant: str, merke: str, form, konto_id: str,
               nr: int) -> dict:
    """Én post eller ett bilag — gjennom dørene.

    DATOENE REGNES I BASEN (`current_date ± n`): `date.today()` er
    maskinens lokale dag, mens sveipen regner i basens sone."""
    slag = form[0]
    oid = uuid.uuid4()
    if slag == "post":
        _, siden, belop = form
        _sk(rt, tenant)
        rt.execute(
            "SELECT m13_registrer_post(%s,%s,%s,%s, current_date - %s,"
            " %s,%s,%s,%s)",
            (tenant, oid, konto_id, f"ext-{merke}-{nr}", siden, belop,
             f"Fasitpost {merke}", f"Motpart {merke}", AKTOR))
        rt.commit()
    else:
        _, om, belop = form
        _sk(rt, tenant)
        rt.execute(
            "SELECT m13_registrer_bilag(%s,%s,%s,'inn',%s,%s,"
            " current_date - 60, current_date + %s, %s)",
            (tenant, oid, f"BIL-{merke}-{nr}", belop, f"Motpart {merke}",
             om, AKTOR))
        rt.commit()
    return {"merke": merke, "subjekt_id": str(oid), "slag": slag}


def _avstem(rt, tenant: str, post_id: str, bilag_id: str, hvorfor: str) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT m13_avstem(%s,%s,%s,%s,'manuell',%s,%s)",
               (tenant, uuid.uuid4(), post_id, bilag_id, hvorfor, AKTOR))
    rt.commit()


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN den rene postens avstemming: den kommer
    mellom kjøringene."""
    a = tenantnavn(runde, "a")
    b = tenantnavn(runde, "b")
    rigg = {"a": a, "b": b, "subjekter": {}}
    kontoer = {}
    for nr, (merke, _v, tkey, form) in enumerate(SETT):
        tenant = rigg[tkey]
        if form[0] == "post" and tkey not in kontoer:
            kontoer[tkey] = _konto(rt, tenant, tkey, nr)
        rigg["subjekter"][merke] = lag_objekt(
            rt, tenant, merke, form, kontoer.get(tkey), nr)
    # DEKNINGEN AVSTEMMES ALT NÅ: `delvis_dekket_bilag` krever at noe
    # dekker bilaget, men ikke alt.
    _avstem(rt, a, rigg["subjekter"]["dekning"]["subjekt_id"],
            rigg["subjekter"]["delvis"]["subjekt_id"],
            "fasit: delvis dekning")
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Avstemmingen som gjør den rene posten ren — MELLOM sveipene.

    Bilaget den avstemmes mot har forfall FRAM i tid, så det blir aldri
    selv en kandidat — og beløpene er like, så bilaget ikke blir delvis
    dekket i stedet."""
    _avstem(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"],
            rigg["subjekter"]["kurbilag"]["subjekt_id"],
            "fasit: gjør posten ren")


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _form in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
