"""M-18s fasitsett: seks onboardingløp med KJENT dom.

Onboardingregisteret har tre funntyper, og ALLE TRE HENGER PÅ SAMME
SUBJEKTNØKKEL — `lop_id`. Det gjør settet vanskeligere enn de andre: to
av dem gjelder løpet som helhet, og den tredje gir ÉN RAD PER FORSINKET
STEG. Et løp med to forsinkede steg ville fått to rader av samme type,
og dommen «nøyaktig én funntype» ville falt på riggen, ikke på modulen.

  1. `stoppet_lop`          — stille lenger enn sveipens døgngrense
  2. `steg_over_frist`      — ett ufullført steg forbi sin egen frist
  3. `lop_uten_aktiv_eier`  — eieren er ikke lenger aktivt medlem
  4. (ren)                  — stoppet løp som får et steg fullført
                              MELLOM kjøringene
  5. (avsluttet)            — fullført løp; status ≠ `paagaar` fjerner
                              det fra alle tre kandidatene
  6. (rent_b)               — ferskt løp i tenant B, uten funn

MODULEN HAR INGEN TERSKELTABELL. Stillhetsgrensen er en
sveipeparameter (standard 14 døgn), og stegenes frister ligger på hver
rad. Settet måles mot modulens egen standardverdi — den samme arbeideren
bruker.

HVER STEGPROFIL TRENGER SIN EGEN MAL: en mal med et pågående løp kan
ikke redigeres, så to løp som skal ha ulike frister kan ikke dele mal.

`lop_uten_aktiv_eier` KREVER DIREKTE DML. Det finnes ingen dør som
deaktiverer et medlemskap — deaktiveringen er en plattformhandling, ikke
en M-18-handling. Riggen går derfor utenom med migratortilkoblingen, og
det er en annen tabell enn den modulen måles på.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import sveipfasit_felles as felles

#: Sveipens egen stillhetsgrense, som settet måles mot.
DOGN_STILLE = 14

AKTOR = "m18-fasit"
EVIDENSKILDE = "m18_onboarding"
FUNNTABELL = "onboardingfunn"
SUBJEKTKOLONNE = "lop_id"

UNAABARE: dict[str, str] = {}
RENSES = "ren"

#: MALENE. «lang» gir alle steg en frist på 365 døgn, så ingen av dem
#: kan bli forsinket uansett hvor gammelt løpet er. «frist» gir steg 2 en
#: frist på ett døgn, og er den eneste malen som kan produsere
#: `steg_over_frist`.
MAL_LANG = [{"navn": "Steg 1", "beskrivelse": "første", "frist_dogn": 365},
            {"navn": "Steg 2", "beskrivelse": "andre", "frist_dogn": 365},
            {"navn": "Steg 3", "beskrivelse": "tredje", "frist_dogn": 365}]
MAL_FRIST = [{"navn": "Steg 1", "beskrivelse": "første", "frist_dogn": 0},
             {"navn": "Steg 2", "beskrivelse": "andre", "frist_dogn": 1},
             {"navn": "Steg 3", "beskrivelse": "tredje", "frist_dogn": 365}]

#: (merkelapp, forventet funntype eller None, tenantnøkkel, mal,
#:  startet for N døgn siden, steg som fullføres i riggen,
#:  egen eier?, avsluttes?)
SETT: tuple[tuple[str, str | None, str, str, int, list, bool, bool], ...] = (
    # STILLE i 30 døgn, ingen steg fullført, og alle frister på 365 — så
    # `steg_over_frist` kan ikke slå inn ved siden av.
    ("stoppet", "stoppet_lop", "a", "lang", 30, [], False, False),
    # FORSINKET: startet for 5 døgn siden. Steg 1 (frist 0) fullføres i
    # dag, så løpet ikke er stille. Steg 2 (frist 1) er forbi fristen.
    # Steg 3 (frist 365) er det ikke. Nøyaktig ÉN rad.
    ("frist", "steg_over_frist", "a", "frist", 5, [1], False, False),
    # Den RENE fødes stille, som `stoppet`: funnet den får i første sveip
    # er nettopp det andre sveip skal lukke.
    ("ren", None, "a", "lang", 30, [], False, False),
    # AVSLUTTET løp: status ≠ `paagaar` fjerner det fra alle tre
    # kandidatene. Alle tre stegene fullføres, så `fullfort` er lovlig.
    ("avsluttet", None, "a", "lang", 30, [1, 2, 3], False, True),
    # UTEN AKTIV EIER: startet i dag, så verken stille eller forsinket
    # (fristen er STRENGT større enn null døgn). Eieren er EGEN, og
    # deaktiveres — ellers hadde de andre løpene mistet sin eier også.
    ("uten_eier", "lop_uten_aktiv_eier", "b", "lang", 0, [], True, False),
    # RENT I TENANT B: ferskt løp med aktiv eier og lange frister.
    ("rent_b", None, "b", "lang", 0, [], False, False),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m18fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m18-fasit")


def _bruker(mg, tenant: str, merke: str) -> str:
    """En bruker med AKTIVT medlemskap, skrevet med migratortilkoblingen.

    DET FINNES INGEN DØR. Medlemskap er en plattformhandling, ikke en
    M-18-handling, og modulen leser det bare. Riggen skriver derfor i en
    ANNEN tabell enn den modulen måles på — registeret selv røres kun
    gjennom dørene."""
    # TENANTKONTEKST FØRST: `brukermedlemskap` har RLS, også mot
    # migratoren. Uten konteksten avviser policyen raden — og feilen sier
    # «insufficient privilege», ikke «du glemte konteksten».
    felles.sett_kontekst(mg, tenant, AKTOR, "m18-fasit")
    rad = mg.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES ('https://fasit', %s)"
        " ON CONFLICT (issuer, sub) DO UPDATE SET sub = EXCLUDED.sub"
        " RETURNING bruker_id", (f"{tenant}:{merke}",)).fetchone()
    bid = rad[0]
    mg.execute(
        "INSERT INTO brukermedlemskap (tenant, bruker_id, roller)"
        " VALUES (%s,%s, ARRAY['it_og_drift'])"
        " ON CONFLICT (tenant, bruker_id) DO UPDATE SET aktiv = true",
        (tenant, bid))
    mg.commit()
    return str(bid)


def _deaktiver(mg, tenant: str, bruker_id: str) -> None:
    felles.sett_kontekst(mg, tenant, AKTOR, "m18-fasit")
    mg.execute("UPDATE brukermedlemskap SET aktiv = false"
               " WHERE tenant=%s AND bruker_id=%s", (tenant, bruker_id))
    mg.commit()


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN det rene løpets fullførte steg: det
    kommer mellom kjøringene.

    `mg` er migratortilkoblingen, som trengs til medlemskapene. Den
    generiske produsenten gir den ikke, så riggen åpner sin egen når den
    mangler."""
    import os
    import psycopg
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    a = tenantnavn(runde, "a")
    b = tenantnavn(runde, "b")
    rigg = {"a": a, "b": b, "subjekter": {}, "eiere": {}}
    try:
        felles_eier = {t: _bruker(mg, rigg[t], "felles") for t in ("a", "b")}
        maler: dict[tuple[str, str], str] = {}
        for merke, _v, tkey, maltype, startet, fullfor, egen_eier, avslutt in SETT:
            tenant = rigg[tkey]
            # HVER STEGPROFIL SIN EGEN MAL: en mal med et pågående løp
            # kan ikke redigeres, så to profiler kan ikke dele én.
            nokkel = (tkey, maltype)
            if nokkel not in maler:
                mal_id = uuid.uuid4()
                _sk(rt, tenant)
                rt.execute("SELECT m18_registrer_mal(%s,%s,%s,%s)",
                           (tenant, mal_id, f"Fasitmal {maltype}", AKTOR))
                rt.commit()
                steg = MAL_LANG if maltype == "lang" else MAL_FRIST
                _sk(rt, tenant)
                rt.execute("SELECT m18_sett_malsteg(%s,%s,%s::jsonb,%s)",
                           (tenant, mal_id, json.dumps(steg), AKTOR))
                rt.commit()
                maler[nokkel] = str(mal_id)
            eier = (_bruker(mg, tenant, merke) if egen_eier
                    else felles_eier[tkey])
            lid = uuid.uuid4()
            _sk(rt, tenant)
            rt.execute(
                "SELECT m18_start_lop(%s,%s,%s,%s,%s, current_date - %s, %s)",
                (tenant, lid, maler[nokkel], f"kunde-{merke}", eier,
                 startet, AKTOR))
            rt.commit()
            for nr in fullfor:
                _sk(rt, tenant)
                rt.execute("SELECT m18_fullfor_steg(%s,%s,%s,%s,%s)",
                           (tenant, lid, nr, f"fasit {merke}", AKTOR))
                rt.commit()
            if avslutt:
                _sk(rt, tenant)
                rt.execute("SELECT m18_avslutt_lop(%s,%s,'fullfort',%s,%s)",
                           (tenant, lid, f"fasit {merke}", AKTOR))
                rt.commit()
            if egen_eier:
                _deaktiver(mg, tenant, eier)
            rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": str(lid)}
            rigg["eiere"][merke] = eier
    finally:
        if egen:
            mg.close()
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Steget som gjør det rene løpet rent — MELLOM sveipene.

    Stillheten måles fra SISTE fullføring, og `fullfort_ts` settes til
    `now()` av døra. Et fullført steg i dag gjør derfor stillheten null.
    Stegets egen frist er 365 døgn, så `steg_over_frist` tar ikke
    plassen til funnet som lukkes."""
    _sk(rt, rigg["a"])
    rt.execute("SELECT m18_fullfor_steg(%s,%s,1,%s,%s)",
               (rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"],
                "fasit: gjør løpet rent", AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _mal, _s, _fu, _e, _a in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
