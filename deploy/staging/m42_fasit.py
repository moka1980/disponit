"""M-42s fasitsett: seks mottakere med KJENT dom, drevet gjennom dørene.

Kontoregisteret har fire funntyper, og ALLE er nåbare for et subjekt
opprettet i dag — begge tidsmålingene leser DATO-PARAMETRE
(`oppgitt_dato`, `verifisert_dato`), ikke en `opprettet`-kolonne ingen
dør rører:

  1. `kontoendring`        — to oppgaver med ulikt nummer, siste uverifisert
  2. `uverifisert_konto`   — oppgitt for lenge siden, aldri verifisert
  3. `verifikasjon_utlopt` — verifisert, men for lenge siden
  4. `ingen_terskel`       — egen tenant UTEN terskler
  5. (ren)                 — oppgitt for lenge siden, og en NY oppgave med
                             SAMME nummer kommer mellom kjøringene
  6. (fersk)               — oppgitt og verifisert i dag; ren fra fødselen

TO AV TYPENE DELER VILKÅR, og settet er bygget for å skille dem: både
`kontoendring` og `uverifisert_konto` krever at siste oppgave er
UVERIFISERT. Den som skal måles på endringen får derfor sin siste
oppgave I DAG, godt innenfor `uverifisert_dogn`.

DØRA SKRIVER `kontoendring` SELV, i samme transaksjon som den andre
oppgaven, uavhengig av sveipen. Subjektet i tenanten uten terskler får
derfor NØYAKTIG ÉN oppgave — ellers ville det fått et funn døra skrev,
og settet hadde målt døra i stedet for sveipen.

DEN RENE KURERES MED EN NY OPPGAVE, IKKE MED EN VERIFIKASJON, og det er
hele poenget med raden: `m42_verifiser_konto` LUKKER funnene selv, i
døra. Hadde kuren vært en verifikasjon, ville sveipen ikke hatt noe å
lukke, og lukkeaksen vært umålt selv om alt så grønt ut. En ny oppgave
med SAMME nummer gjør derimot ingenting i døra — den flytter bare
`oppgitt_dato` fram, så kandidaten forsvinner og SVEIPEN lukker.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

#: Tersklene settet måles mot — pinnet her, aldri utledet av kjøringen.
REVERIFIKASJON_DOGN = 365
UVERIFISERT_DOGN = 7

AKTOR = "m42-fasit"
#: FIRE ØYNE: `m42_verifikasjon_vakt` NEKTER at den som oppga kontoen
#: verifiserer den — «er de samme, er ingenting verifisert». Riggen må
#: derfor ha to personer, og det er ikke en formalitet: hele modulens
#: grunn til å finnes er at et menneske SÅ på kontoen, uavhengig av den
#: som oppga den.
OPPGIR = "u-fasit-oppgir"
VERIFISERER = "u-fasit-verifiserer"
EVIDENSKILDE = "m42_kontovakt"
FUNNTABELL = "kontofunn"
SUBJEKTKOLONNE = "mottaker_id"

#: Ingen funntype er unåbar: begge tidsmålingene leser datoparametre.
UNAABARE: dict[str, str] = {}

#: SUBJEKTET SOM RENSES mellom de to sveipene.
RENSES = "ren"
#: …og det som er rent fra fødselen.
NEGATIV_KONTROLL = "fersk"

#: (merkelapp, forventet funntype eller None,
#:  oppgaver [(kontonummer, døgn siden)],
#:  verifikasjon: (oppgaveindeks, metode, døgn siden) eller None)
SETT: tuple[tuple[str, str | None, list, tuple | None], ...] = (
    # To oppgaver med ULIKT nummer. Den siste er fra I DAG, så
    # `uverifisert_konto` ikke også slår inn.
    ("endret", "kontoendring",
     [("11112233333", 40), ("44445566666", 0)], None),
    # ÉN oppgave — ingen forrige, altså ingen endring — og den er 30 døgn
    # gammel og uverifisert.
    ("uverifisert", "uverifisert_konto", [("22223344444", 30)], None),
    # Verifisert for 400 døgn siden, altså forbi gyldighetsvinduet.
    # Én oppgave, så ingen endring.
    ("utlopt", "verifikasjon_utlopt", [("33334455555", 420)],
     (0, "bankbekreftelse", 400)),
    # Den RENE fødes uverifisert og 30 døgn gammel, så første sveip gir
    # den `uverifisert_konto`. Kuren er en ny oppgave med SAMME nummer
    # (se filhodet).
    ("ren", None, [("55556677777", 30)], None),
    # REN FRA FØDSELEN: oppgitt og verifisert i dag. Uten denne raden
    # måler settet bare at sveipen finner NOE.
    ("fersk", None, [("66667788888", 0)], (0, "signert_dokument", 0)),
)

#: Mottakeren i tenanten UTEN terskler — hele tenanten er funnet, og
#: den får NØYAKTIG én oppgave (se filhodet).
UTEN_TERSKEL = ("ingen_terskel", "ingen_terskel", [("77778899999", 0)], None)

#: Oppgaven som gjør den rene mottakeren ren, MELLOM sveipene: SAMME
#: nummer som den har fra før, oppgitt i dag. Samme nummer betyr ingen
#: `kontoendring`; fersk dato betyr ingen `uverifisert_konto`.
REN_OPPGAVE = ("55556677777", 0)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m42fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m42-fasit")


def lag_mottaker(rt, tenant: str, merke: str, oppgaver: list,
                 verifikasjon: tuple | None) -> dict:
    """Én mottaker med sine kontooppgaver — gjennom dørene.

    DATOENE REGNES I BASEN (`current_date - n`): dørene nekter en dato i
    framtida, og `date.today()` er maskinens lokale dag, ikke basens."""
    mid = uuid.uuid4()
    _sk(rt, tenant)
    rt.execute("SELECT m42_registrer_mottaker(%s,%s,%s,%s,%s)",
               (tenant, mid, f"ref-{merke}", f"Fasit {merke}", AKTOR))
    rt.commit()
    oppgave_ider = []
    for nummer, siden in oppgaver:
        oid = uuid.uuid4()
        _sk(rt, tenant)
        rt.execute(
            "SELECT m42_oppgi_konto(%s,%s,%s,%s,%s,%s,"
            " current_date - %s, %s, %s)",
            (tenant, oid, mid, nummer, OPPGIR, "portal", siden,
             f"fasitoppgave {merke}", AKTOR))
        rt.commit()
        oppgave_ider.append(str(oid))
    if verifikasjon is not None:
        indeks, metode, siden = verifikasjon
        _sk(rt, tenant)
        rt.execute(
            "SELECT m42_verifiser_konto(%s,%s,%s,%s,%s,%s,"
            " current_date - %s, %s)",
            (tenant, uuid.uuid4(), oppgave_ider[indeks], metode, VERIFISERER,
             f"fasitverifikasjon {merke}", siden, AKTOR))
        rt.commit()
    return {"merke": merke, "subjekt_id": str(mid), "oppgaver": oppgave_ider}


def forbered(rt, runde: str) -> dict:
    """Begge tenantene, med og uten terskler — men UTEN den rene
    mottakerens ANDRE oppgave: den kommer mellom kjøringene, og er det
    sveipen skal lukke funnet på."""
    med = tenantnavn(runde, "med_terskel")
    uten = tenantnavn(runde, "uten_terskel")
    _sk(rt, med)
    rt.execute("SELECT m42_sett_terskler(%s,%s,%s,%s)",
               (med, REVERIFIKASJON_DOGN, UVERIFISERT_DOGN, AKTOR))
    rt.commit()
    rigg = {"med_terskel": med, "uten_terskel": uten, "subjekter": {}}
    for merke, _v, oppgaver, verifikasjon in SETT:
        rigg["subjekter"][merke] = lag_mottaker(rt, med, merke, oppgaver,
                                                verifikasjon)
    merke, _v, oppgaver, verifikasjon = UTEN_TERSKEL
    rigg["subjekter"][merke] = lag_mottaker(rt, uten, merke, oppgaver,
                                            verifikasjon)
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["med_terskel"], rigg["uten_terskel"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Oppgaven som gjør den rene mottakeren ren — MELLOM sveipene.

    SAMME NUMMER som den har fra før: da skriver døra ingenting, og det
    er SVEIPEN som må lukke funnet. En verifikasjon ville lukket det i
    døra, og aksen hadde vært umålt."""
    nummer, siden = REN_OPPGAVE
    s = rigg["subjekter"][RENSES]
    _sk(rt, rigg["med_terskel"])
    rt.execute(
        "SELECT m42_oppgi_konto(%s,%s,%s,%s,%s,%s,"
        " current_date - %s, %s, %s)",
        (rigg["med_terskel"], uuid.uuid4(), s["subjekt_id"], nummer,
         OPPGIR, "portal", siden, "fasit: gjør mottakeren ren", AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _o, _v in SETT}
    ut[UTEN_TERSKEL[0]] = UTEN_TERSKEL[1]
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
