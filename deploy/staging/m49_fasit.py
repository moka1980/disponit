"""M-49s fasitsett: åtte subjekter i tre tenanter med KJENT dom.

Sanksjonsregisteret har sju funntyper, alle på `subjekt_id`.

  tenant A (krav: matchterskel 85, gyldig 90, uavklart 0, ukontrollert 0)
    1. `ukontrollert_subjekt`      — aldri kontrollert
    2. `uavklart_treff`            — kontrollert med et treff ingen har
                                      avklart
    3. `bekreftet_treff`           — treffet er avklart som samme part
    4. `kontroll_mot_gammel_liste` — kontrollert mot OFAC-lista som så
                                      ble avløst av en nyere
    5. (ren)                       — aldri kontrollert; kontrolleres uten
                                      treff MELLOM kjøringene
    6. (ok)                        — kontrollert uten treff mot EU-lista
  tenant B (ingen krav)
    7. `ingen_krav`
  tenant C (krav, men ingen lister)
    8. `ingen_liste`

TERSKLENE PÅ NULL ER POENGET, IKKE EN SNARVEI. `ukontrollert_dogn` og
`uavklart_frist_dogn` har gulv 0, og da er «opprettet før nå» og
«registrert før nå» nok — begge tidsfunnene kan måles på én dag. Tenant
C har derfor `ukontrollert_dogn` 3650: subjektet der skal måle
`ingen_liste` og ingenting annet.

TO LISTEKILDER: de andre kontrollene går mot EU-lista, som aldri
avløses. Bare subjektet som skal måle den gamle lista, kontrolleres mot
OFAC — og den nye OFAC-lista registreres ETTER kontrollen.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m49-fasit"
EVIDENSKILDE = "m49_sanksjon"
FUNNTABELL = "sanksjonsfunn"
SUBJEKTKOLONNE = "subjekt_id"

UNAABARE: dict[str, str] = {
    "kontroll_utlopt":
        "kandidaten krever at siste kontroll er eldre enn "
        "`kontroll_gyldig_dogn`, og gulvet for den terskelen er ett døgn "
        "(CHECK BETWEEN 1 AND 3650). `sanksjonskontroll.kontrollert` er "
        "DEFAULT now() og ingen dørparameter i `m49_registrer_kontroll`, så "
        "en kontroll kan ikke være utløpt samme dag som den ble gjort.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

KRAV_A = (85, 90, 0, 0)
KRAV_C = (85, 90, 3, 3650)

#: (merkelapp, forventet, tenantnøkkel, kontroll: None | "eu" | "ofac",
#:  treff: None | "uavklart" | "bekreftet")
SETT: tuple[tuple[str, str | None, str, str | None, str | None], ...] = (
    ("ukontrollert", "ukontrollert_subjekt", "a", None, None),
    ("uavklart", "uavklart_treff", "a", "eu", "uavklart"),
    ("bekreftet", "bekreftet_treff", "a", "eu", "bekreftet"),
    ("gammel", "kontroll_mot_gammel_liste", "a", "ofac", None),
    # Den RENE fødes ukontrollert: funnet den får i første sveip er
    # nettopp det andre sveip skal lukke.
    ("ren", None, "a", None, None),
    ("ok", None, "a", "eu", None),
    ("uten_krav", "ingen_krav", "b", None, None),
    ("uten_liste", "ingen_liste", "c", None, None),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m49fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m49-fasit")


def _liste(rt, tenant: str, kilde: str, dager_siden: int, runde: str,
           merke: str) -> str:
    lid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m49_registrer_liste(%s,%s,%s,%s, current_date - %s,"
               " %s, 12000, %s)",
               (tenant, lid, kilde, f"{merke}-{runde}", dager_siden,
                hashlib.sha256(f"{runde}:{merke}".encode()).hexdigest(),
                AKTOR))
    rt.commit()
    return lid


def _kontroll(rt, tenant: str, sid: str, lid: str,
              treff: list[dict]) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT m49_registrer_kontroll(%s,%s,%s,%s,%s,%s::jsonb,%s)",
               (tenant, str(uuid.uuid4()), sid, lid, ["navn", "land"],
                json.dumps(treff), AKTOR))
    rt.commit()


def forbered(rt, runde: str) -> dict:
    """Alle tre tenantene — men UTEN kontrollen av det rene subjektet:
    den kommer mellom kjøringene."""
    rigg = {t: tenantnavn(runde, t) for t in ("a", "b", "c")}
    rigg["subjekter"] = {}
    for tkey, krav in (("a", KRAV_A), ("c", KRAV_C)):
        _sk(rt, rigg[tkey])
        rt.execute("SELECT m49_sett_krav(%s,%s,%s,%s,%s,%s)",
                   (rigg[tkey], *krav, AKTOR))
        rt.commit()
    a = rigg["a"]
    lister = {"eu": _liste(rt, a, "eu", 30, runde, "eu"),
              "ofac": _liste(rt, a, "ofac", 30, runde, "ofac-1")}
    rigg["eu"] = lister["eu"]

    for merke, _v, tkey, kilde, treff in SETT:
        tenant = rigg[tkey]
        sid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute("SELECT m49_registrer_subjekt(%s,%s,%s,'Fasit Person',"
                   "'person','NO','1970-01-01',NULL,%s)",
                   (tenant, sid, f"K-{merke}-{runde}", AKTOR))
        rt.commit()
        if kilde:
            rader = []
            if treff:
                rader = [{"treff_id": str(uuid.uuid4()),
                          "matchtype": "navnelikhet", "matchfelt": ["navn"],
                          "likhet": 92, "listenavn": "PERSON, Fasit",
                          "liste_referanse": f"SDN-{merke}",
                          "liste_program": None}]
            _kontroll(rt, tenant, sid, lister[kilde], rader)
            if treff == "bekreftet":
                _sk(rt, tenant)
                rt.execute("SELECT m49_avklar_treff(%s,%s,%s,"
                           "'bekreftet_treff','Fodselsdato og land stemmer',"
                           "%s)", (tenant, str(uuid.uuid4()),
                                   rader[0]["treff_id"], AKTOR))
                rt.commit()
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": sid}

    # DEN NYE OFAC-LISTA kommer ETTER kontrollen som skal bli gammel.
    _liste(rt, a, "ofac", 1, runde, "ofac-2")
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"], rigg["c"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Kontrollen uten treff som gjør det rene subjektet rent — MELLOM
    sveipene."""
    _kontroll(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"],
              rigg["eu"], [])


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t, _k, _tr in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
