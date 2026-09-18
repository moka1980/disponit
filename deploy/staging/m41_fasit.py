"""M-41s fasitsett: seks betalingssubjekter med KJENT dom.

Betalingsregisteret har fire funntyper, og ALLE er nåbare for et subjekt
opprettet i dag: hele alders-aksen går gjennom `inntruffet`, som er en
dørparameter, ikke en `opprettet`-kolonne ingen dør rører.

  1. `uavklart_betaling`   — står `opprettet` lenger enn fristen
  2. `belopsavvik`         — betalt beløp avviker fra det forventede
  3. `autorisasjon_utlopt` — autorisert for lenge siden
  4. `ingen_terskel`       — egen tenant UTEN terskler
  5. (ren)                 — står `opprettet` for lenge, og gjøres opp
                             MELLOM kjøringene
  6. (fersk)               — gjennomført i dag til forventet beløp

ALLE TRE PREDIKATENE LESER DEN SISTE HENDELSEN, og de overlapper:
`uavklart_betaling` og `autorisasjon_utlopt` treffer begge en autorisert
betaling som har stått lenge, og `belopsavvik` treffer den også hvis et
forventet beløp står. Settet skiller dem på STATUS og på alder.

TERSKLENE ER SATT MOTSATT AV STANDARDEN, med vilje: `reautorisasjon_dogn`
er STRENGT MINDRE enn `uavklart_dogn`. Uten det finnes det ikke noe
aldersvindu der en autorisert betaling er utløpt UTEN også å være
uavklart, og funntypen kunne ikke isoleres.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

#: Tersklene settet måles mot — pinnet her, aldri utledet av kjøringen.
UAVKLART_DOGN = 30
BELOPSAVVIK_ORE = 0
REAUTORISASJON_DOGN = 7

AKTOR = "m41-fasit"
EVIDENSKILDE = "m41_betaling"
FUNNTABELL = "betalingsfunn"
SUBJEKTKOLONNE = "subjekt_id"

#: Ingen funntype er unåbar: alders-aksen er en dørparameter.
UNAABARE: dict[str, str] = {}

RENSES = "ren"
NEGATIV_KONTROLL = "fersk"

#: (merkelapp, forventet funntype eller None,
#:  hendelser [(status, belop_ore, forventet_ore eller None, døgn siden)])
SETT: tuple[tuple[str, str | None, list], ...] = (
    # `opprettet` i 60 døgn — forbi fristen på 30. Statusen er verken
    # `autorisert` eller `gjennomfort`, så de to andre faller bort.
    ("uavklart", "uavklart_betaling", [("opprettet", 10_000, None, 60)]),
    # GJENNOMFØRT, altså utenfor begge tidspredikatene, men betalt 2000
    # øre over det forventede.
    ("avvik", "belopsavvik", [("gjennomfort", 12_000, 10_000, 0)]),
    # AUTORISERT for 10 døgn siden: forbi reautorisasjonsfristen på 7,
    # men innenfor uavklart-fristen på 30. Uten forventet beløp, så
    # `belopsavvik` ikke også slår inn.
    ("utlopt", "autorisasjon_utlopt", [("autorisert", 10_000, None, 10)]),
    # Den RENE fødes `opprettet` og 60 døgn gammel: funnet den får i
    # første sveip er nettopp det andre sveip skal lukke.
    ("ren", None, [("opprettet", 10_000, None, 60)]),
    # REN FRA FØDSELEN: gjennomført i dag, til nøyaktig forventet beløp.
    ("fersk", None, [("gjennomfort", 10_000, 10_000, 0)]),
)

#: Subjektet i tenanten UTEN terskler — hele tenanten er funnet.
UTEN_TERSKEL = ("ingen_terskel", "ingen_terskel",
                [("gjennomfort", 10_000, 10_000, 0)])

#: Hendelsen som gjør det rene subjektet rent, MELLOM sveipene:
#: gjennomført i dag, uten forventet beløp å avvike fra.
REN_HENDELSE = ("gjennomfort", 10_000, None, 0)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m41fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m41-fasit")


def _hendelse(rt, tenant: str, sid, merke: str, hendelse, teller: int) -> None:
    """Én statushendelse gjennom døra.

    `kilde_ref` VARIERES: `betalingshendelse_kilde_unik` er en unik
    beskrankning over (tenant, subjekt, kilde, kilde_ref), og to
    hendelser med samme referanse ville blitt avvist — ikke som en
    feil i modulen, men som en feil i riggen."""
    status, belop, forventet, siden = hendelse
    _sk(rt, tenant)
    rt.execute(
        "SELECT m41_registrer_status(%s,%s,%s,%s,%s,%s,'NOK',%s,'portal',%s,"
        " current_date - %s, %s, %s)",
        (tenant, uuid.uuid4(), sid, status, belop, forventet,
         f"kort-{merke}-9999", f"fasit-{merke}-{teller}", siden,
         f"fasithendelse {merke}", AKTOR))
    rt.commit()


def lag_subjekt(rt, tenant: str, merke: str, hendelser: list) -> dict:
    """Ett betalingssubjekt med sine statushendelser — gjennom dørene.

    DATOENE REGNES I BASEN (`current_date - n`): døra nekter en dato i
    framtida, og `date.today()` er maskinens lokale dag, ikke basens."""
    sid = uuid.uuid4()
    _sk(rt, tenant)
    rt.execute("SELECT m41_registrer_subjekt(%s,%s,%s,%s,%s)",
               (tenant, sid, f"ref-{merke}", f"Fasit {merke}", AKTOR))
    rt.commit()
    for i, h in enumerate(hendelser):
        _hendelse(rt, tenant, sid, merke, h, i)
    return {"merke": merke, "subjekt_id": str(sid)}


def forbered(rt, runde: str) -> dict:
    """Begge tenantene, med og uten terskler — men UTEN den rene
    betalingens oppgjør: det kommer mellom kjøringene."""
    med = tenantnavn(runde, "med_terskel")
    uten = tenantnavn(runde, "uten_terskel")
    _sk(rt, med)
    rt.execute("SELECT m41_sett_terskler(%s,%s,%s,%s,%s)",
               (med, UAVKLART_DOGN, BELOPSAVVIK_ORE, REAUTORISASJON_DOGN,
                AKTOR))
    rt.commit()
    rigg = {"med_terskel": med, "uten_terskel": uten, "subjekter": {}}
    for merke, _v, hendelser in SETT:
        rigg["subjekter"][merke] = lag_subjekt(rt, med, merke, hendelser)
    merke, _v, hendelser = UTEN_TERSKEL
    rigg["subjekter"][merke] = lag_subjekt(rt, uten, merke, hendelser)
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["med_terskel"], rigg["uten_terskel"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Oppgjøret som gjør den rene betalingen ren — MELLOM sveipene.

    En NY hendelse, ikke en retting: `betalingshendelse` er totalt
    frosset, og det er riktig — en betaling som kunne skrives om i
    ettertid ville gjort hvert eldre funn til en gjetning."""
    s = rigg["subjekter"][RENSES]
    _hendelse(rt, rigg["med_terskel"], s["subjekt_id"], RENSES,
              REN_HENDELSE, 99)


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _h in SETT}
    ut[UTEN_TERSKEL[0]] = UTEN_TERSKEL[1]
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
