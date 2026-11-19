"""M-15s fasitsett: fire modeller og fire prognoser med KJENT dom.

Likviditetsregisteret har seks funntyper over to nøkler: modellen
(`modell_id`) og prognosen (`prognose_id`). Subjektet leses som
`coalesce(prognose_id, modell_id)` (`likviditetsfunn` bærer én).

  tenant A (horisont 4 uker, grunnlag 30 døgn, varsel 30 døgn)
    modell   1. `modell_utloper_snart`  — gyldig til om 5 døgn
             2. (ren)                   — som 1; forlenges MELLOM
                                          kjøringene
             3. (m_hoved)               — uten sluttdato
    prognose 4. `prognose_mot_utdatert_grunnlag` — laget da siste
                                          bankpost var 60 døgn gammel
             5. `ingen_gyldig_modell`   — modellen den bygger på er
                                          avviklet etterpå
             6. `bane_under_null`       — laget etter en stor ukentlig
                                          utgift
             7. (ok)                    — fersk bankpost, positiv bane
  tenant B (ingen krav)
    modell   8. `ingen_krav`

EN PROGNOSE ER ET ØYEBLIKKSBILDE. Grunnlaget (`grunnlag_siste_bevegelse`)
og banen fryses når den lages, så riggen bygger tenanten i rekkefølge:
gammel bankpost → utdatert prognose → fersk bankpost → ok-prognose og
prognosen mot modellen som så avvikles → stor utgift → prognosen under
null. Én tenant med krav, fordi kravhendelsen bare bærer versjonen og
ville gitt samme `input_hash` i to tenanter.

Bankpostene skrives gjennom M-13s egne dører (`m13_registrer_konto`,
`m13_registrer_post`) — det er grunnlaget M-15 leser.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m15-fasit"
EVIDENSKILDE = "m15_likviditet"
FUNNTABELL = "likviditetsfunn"
SUBJEKTKOLONNE = "coalesce(prognose_id, modell_id)"

UNAABARE: dict[str, str] = {
    "prognose_uten_maaling":
        "kandidaten krever `gjelder_til + maalefrist_dogn < current_date`, "
        "og `gjelder_til` regnes av `m15_lag_prognose` som i dag pluss "
        "horisonten (minst én uke, CHECK BETWEEN 1 AND 104). Målefristen "
        "har gulv ett døgn. En prognose kan derfor tidligst mangle måling "
        "åtte døgn etter at den ble laget.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

KRAV = (4, 30, 7, 30)
METODE = ("Startsaldo fra bankposter, fordringer inn paa"
          " forfallsuken, registrerte forpliktelser ut paa sin.")


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m15fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m15-fasit")


def _modell(rt, tenant: str, mid: str, versjon: str,
            til: int | None) -> None:
    _sk(rt, tenant)
    rt.execute(
        "SELECT m15_registrer_modell(%s,%s,'Kumulativ kontantbane',%s,%s,"
        "'samme som forrige uke', current_date - 10,"
        " CASE WHEN %s::int IS NULL THEN NULL"
        "      ELSE current_date + %s::int END, %s)",
        (tenant, mid, versjon, METODE, til, til, AKTOR))
    rt.commit()


def _bankpost(rt, tenant: str, konto: str, dager_siden: int,
              belop: int) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m13_registrer_post(%s,%s,%s,%s,"
               " current_date - %s::int, %s,'Fasit','Fasit AS',%s)",
               (tenant, str(uuid.uuid4()), konto,
                "BANK-" + secrets.token_hex(4), dager_siden, belop, AKTOR))
    rt.commit()


def _prognose(rt, tenant: str, mid: str) -> str:
    pid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m15_lag_prognose(%s,%s,%s,1500,%s)",
               (tenant, pid, mid, AKTOR))
    rt.commit()
    return pid


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN forlengelsen av den rene modellen: den
    kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}}
    a = rigg["a"]
    _sk(rt, a)
    rt.execute("SELECT * FROM m15_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
               (a, *KRAV, AKTOR, f"m15fasit-krav-{runde}"))
    rt.commit()

    def modell(merke, tenant, til):
        mid = str(uuid.uuid4())
        _modell(rt, tenant, mid, f"{runde}-{merke}", til)
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": mid}
        return mid

    hoved = modell("m_hoved", a, None)
    avvikles = modell("m_avvikles", a, None)
    modell("snart", a, 5)
    modell("ren", a, 5)
    modell("uten_krav", rigg["b"], None)
    # m_avvikles er bare grunnlaget for prognosen som skal miste modellen
    # sin; den er ikke selv et subjekt.
    del rigg["subjekter"]["m_avvikles"]

    konto = str(uuid.uuid4())
    _sk(rt, a)
    rt.execute("SELECT m13_registrer_konto(%s,%s,'Fasitkonto',%s,'NOK',%s)",
               (a, konto, "1234" + str(secrets.randbelow(10**7)).zfill(7),
                AKTOR))
    rt.commit()

    def prognose(merke, mid):
        rigg["subjekter"][merke] = {"merke": merke,
                                    "subjekt_id": _prognose(rt, a, mid)}

    _bankpost(rt, a, konto, 60, 5_000_000)
    prognose("utdatert", hoved)
    _bankpost(rt, a, konto, 0, 5_000_000)
    prognose("ok", hoved)
    prognose("uten_modell", avvikles)
    _modell(rt, a, avvikles, f"{runde}-m_avvikles", -1)
    _sk(rt, a)
    rt.execute("SELECT m15_registrer_post(%s,%s,'husleie','Fasitleie',"
               "-50000000, current_date + 1,'ukentlig',NULL,%s)",
               (a, str(uuid.uuid4()), AKTOR))
    rt.commit()
    prognose("under_null", hoved)
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Forlengelsen som gjør den rene modellen ren — MELLOM sveipene.
    Bare `gyldig_til` kan endres på en modell."""
    _modell(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"],
            f"{rigg['a'].rsplit('-', 1)[-1]}-ren", 365)


def forventet() -> dict[str, str | None]:
    return {"m_hoved": None, "snart": "modell_utloper_snart", "ren": None,
            "uten_krav": "ingen_krav",
            "utdatert": "prognose_mot_utdatert_grunnlag",
            "ok": None, "uten_modell": "ingen_gyldig_modell",
            "under_null": "bane_under_null"}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
