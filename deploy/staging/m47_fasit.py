"""M-47s fasitsett: fire regelverk og fem rapportplikter med KJENT dom.

Myndighetsregisteret har seks funntyper over TO subjektslag: tre gjelder
regelverket (`regelverk_id`), tre gjelder plikten (`rapportplikt_id`).
`ingen_krav` kan stå på begge, men settet gir den bare til et regelverk.
Funnraden bærer NØYAKTIG én av nøklene (`myndighetsfunn_en_noekkel`), så
subjektet leses som `coalesce(regelverk_id, rapportplikt_id)`.

  regelverk  1. `regelverk_utloper_snart` — gyldig til om 5 døgn
             2. `regelverk_utlopt`        — satt utløpt etter at en plikt
                                             ble registrert mot det
             3. `ingen_krav`              — tenant B har ingen krav
             4. (rv_gyldig)               — uten sluttdato; pliktenes hjem
  plikt      5. `frist_passert_uten_bevis` — frist for 5 døgn siden
             6. `frist_naermer_seg`        — frist om 5 døgn (varsel 14)
             7. `plikt_mot_utlopt_regelverk` — frist om 200 døgn
             8. (ren)                     — passert, får bevis MELLOM
                                             kjøringene
             9. (levert)                  — passert, men med bevis fra før

REKKEFØLGEN ER EN AVHENGIGHET. `m47_registrer_plikt` nekter uten krav
og mot et regelverk som ikke gjelder. Plikten mot det utløpte regelverket
registreres derfor MENS det gjelder, og `m47_sett_gyldig_til` gjør det
utløpt etterpå — som en myndighet som kunngjør at et skjema avvikles.

Alle datoer bygges i SQL (`current_date ± n`), aldri i Python.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m47-fasit"
EVIDENSKILDE = "m47_myndighetsrapport"
FUNNTABELL = "myndighetsfunn"
SUBJEKTKOLONNE = "coalesce(regelverk_id, rapportplikt_id)"

UNAABARE: dict[str, str] = {}
RENSES = "ren"
NEGATIV_KONTROLL = "levert"

#: Kravene i tenant A: varsel 14 døgn før frist, eskalering 3, og
#: regelverksvarsel 60 døgn før avvikling.
KRAV = (14, 3, 60)

#: Regelverkene: (merkelapp, forventet, tenantnøkkel, gyldig_til om N
#: døgn eller None, settes utløpt N døgn tilbake etter pliktene eller None)
REGELVERK: tuple[tuple[str, str | None, str, int | None, int | None],
                 ...] = (
    ("rv_gyldig", None, "a", None, None),
    ("rv_utloper", "regelverk_utloper_snart", "a", 5, None),
    ("rv_utlopt", "regelverk_utlopt", "a", None, 10),
    ("rv_uten_krav", "ingen_krav", "b", None, None),
)

#: Pliktene, alle i tenant A: (merkelapp, forventet, regelverk,
#: frist om N døgn, bevis innsendt N døgn tilbake eller None)
PLIKTER: tuple[tuple[str, str | None, str, int, int | None], ...] = (
    ("passert", "frist_passert_uten_bevis", "rv_gyldig", -5, None),
    ("naermer", "frist_naermer_seg", "rv_gyldig", 5, None),
    ("mot_utlopt", "plikt_mot_utlopt_regelverk", "rv_utlopt", 200, None),
    # Den RENE fødes passert, som `passert`: funnet den får i første
    # sveip er nettopp det andre sveip skal lukke.
    ("ren", None, "rv_gyldig", -5, None),
    ("levert", None, "rv_gyldig", -5, 6),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m47fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m47-fasit")


def _bevis(rt, tenant: str, pid: str, siden: int, merke: str) -> None:
    _sk(rt, tenant)
    rt.execute(
        "SELECT * FROM m47_registrer_bevis(%s,%s,%s, current_date - %s,"
        " %s,'Fasit',NULL,%s)",
        (tenant, uuid.uuid4(), pid, siden, f"KV-{merke}", AKTOR))
    rt.commit()


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN den rene pliktens bevis: det kommer
    mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "regelverk": {}}
    a = rigg["a"]
    _sk(rt, a)
    rt.execute("SELECT * FROM m47_sett_krav(%s,%s,%s,%s,%s,%s)",
               (a, *KRAV, AKTOR, f"m47fasit-krav-{runde}"))
    rt.commit()

    for merke, _v, tkey, til, _ut in REGELVERK:
        tenant = rigg[tkey]
        rid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute(
            "SELECT * FROM m47_registrer_regelverk(%s,%s,'skatteetaten',"
            "%s,'2026-01','skattebetalingsloven 8-1', current_date - 400,"
            " CASE WHEN %s::int IS NULL THEN NULL"
            "      ELSE current_date + %s::int END, %s, NULL, %s)",
            (tenant, rid, f"Fasit {merke}", til, til,
             hashlib.sha256(f"{runde}:{merke}".encode()).hexdigest(),
             AKTOR))
        rt.commit()
        rigg["regelverk"][merke] = rid
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": rid}

    tid = str(uuid.uuid4())
    _sk(rt, a)
    rt.execute("SELECT * FROM m47_registrer_plikttype(%s,%s,'mva_melding',"
               "'MVA-melding','to_maanedlig',NULL,%s)", (a, tid, AKTOR))
    rt.commit()

    for merke, _v, rv, frist, bevis in PLIKTER:
        pid = str(uuid.uuid4())
        _sk(rt, a)
        rt.execute(
            "SELECT * FROM m47_registrer_plikt(%s,%s,%s,%s,"
            " current_date - 60, current_date - 31, current_date + %s, %s)",
            (a, pid, tid, rigg["regelverk"][rv], frist, AKTOR))
        rt.commit()
        if bevis is not None:
            _bevis(rt, a, pid, bevis, merke)
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": pid}

    # Avviklingen kommer ETTER pliktene: døra nekter en plikt mot et
    # regelverk som ikke gjelder.
    for merke, _v, tkey, _til, utlopt in REGELVERK:
        if utlopt is None:
            continue
        _sk(rt, rigg[tkey])
        rt.execute("SELECT * FROM m47_sett_gyldig_til(%s,%s,"
                   " current_date - %s, %s)",
                   (rigg[tkey], rigg["regelverk"][merke], utlopt, AKTOR))
        rt.commit()
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Beviset som gjør den rene plikten ren — MELLOM sveipene."""
    _bevis(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"], 0,
           RENSES)


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _t, _til, _u in REGELVERK}
    ut.update({m: f for m, f, _rv, _fr, _b in PLIKTER})
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
