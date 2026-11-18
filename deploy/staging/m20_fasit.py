"""M-20s fasitsett: fire utkast og to kilder i ÉN tenant med KJENT dom.

Innholdsregisteret har seks funntyper på én generisk nøkkel,
`referanse` — et utkast, en kilde eller en publisering etter typen.
Sveipen skriver tre av dem; de tre andre er umulige med vilje
(migrasjonens egen kommentar: «at de står i settet OG er umulige er hele
beviset»).

  tenant A (kilder gyldige 365 døgn, visning 60 min, varsel 30 døgn)
    1. `klart_utkast_uten_forhaandsvisning` — merket klart, aldri sett
    2. `kilde_utloper_snart_uavklart`       — kilden gjelder 5 døgn til og
                                               bærer en publisert påstand
    3. (ren)                                — som 1; vises MELLOM
                                               kjøringene
    4. (k_lang)                             — kilden gjelder 365 døgn
    5. (ok)                                 — klart og vist, ikke publisert

ÉN TENANT, MED VILJE. Kravhendelsen i `m20_sett_krav` bærer bare
versjonsnummeret (ingen tenant, ingen verdier), så første krav i to
tenanter gir alltid samme `input_hash` — evidenskjeden ville målt en
kollisjon riggen selv laget. Sveipen besøker likevel flere tenanter:
den går over alle med krav, og tidligere runders tenanter står (alt er
append-only).

Hvert utkast er sin egen side: et utkast på en side som alt har et
utkast i arbeid, ville bygget på det.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m20-fasit"
EVIDENSKILDE = "m20_innhold"
FUNNTABELL = "innholdsfunn"
SUBJEKTKOLONNE = "referanse"

_UMULIG = ("står i registerets lukkede sett, men ingen skrivevei kan reise "
           "den: migrasjonen 134 kaller den en av «DE TRE SOM ALDRI KAN "
           "REISES», og `m20_funn_er_sveipens` holder den utenfor sveipen. ")
UNAABARE: dict[str, str] = {
    "paastand_uten_kilde": _UMULIG + "Påstanden har kilde_id NOT NULL med "
        "fremmednøkkel, så en påstand uten kilde er urepresenterbar.",
    "publisering_uten_forhaandsvisning": _UMULIG + "`m20_publiser` krever en "
        "forhåndsvisning av nøyaktig det utkastet som publiseres.",
    "publisering_uten_rollbackvei": _UMULIG + "Hver publisering bærer "
        "versjonen den avløste, så veien tilbake finnes alltid.",
    "publisert_paastand_uten_gyldig_kilde":
        "kandidaten krever en publisert påstand på en kilde som SIDEN har "
        "utløpt. `m20_registrer_paastand` og `m20_merk_klar` nekter en "
        "utløpt kilde, og kilden utløper tidligst i morgen: `gyldig_til` "
        "må gjelde i dag, og gyldighetsvinduet har gulv ett døgn regnet fra "
        "`registrert` (DEFAULT now()).",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

KRAV = (365, 60, 30)

#: (merkelapp, forventet, tenantnøkkel, kilde, vises?, publiseres?)
UTKAST: tuple[tuple[str, str | None, str, str, bool, bool], ...] = (
    ("klar_uten", "klart_utkast_uten_forhaandsvisning", "a", "k_lang",
     False, False),
    # Utkastet som bærer den snart utløpte kilden ut i verden. Det er
    # ikke selv et subjekt; kilden er.
    ("_publisert", None, "a", "k_snart", True, True),
    # Den RENE fødes uten visning: funnet den får i første sveip er
    # nettopp det andre sveip skal lukke.
    ("ren", None, "a", "k_lang", False, False),
    ("ok", None, "a", "k_lang", True, False),
)

#: (merkelapp, forventet, tenantnøkkel, gyldig_til om N døgn)
KILDER: tuple[tuple[str, str | None, str, int], ...] = (
    ("k_lang", None, "a", 365),
    ("k_snart", "kilde_utloper_snart_uavklart", "a", 5),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m20fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m20-fasit")


def _vis(rt, tenant: str, uid: str) -> str:
    vid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m20_registrer_visning(%s,%s,%s,'u-redaktor',%s)",
               (tenant, vid, uid, AKTOR))
    rt.commit()
    return vid


def forbered(rt, runde: str) -> dict:
    """Tenanten — men UTEN visningen av det rene utkastet: den
    kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "subjekter": {}, "kilder": {}}
    _sk(rt, rigg["a"])
    rt.execute("SELECT m20_sett_krav(%s,%s,%s,%s,%s)",
               (rigg["a"], *KRAV, AKTOR))
    rt.commit()
    for merke, _v, tkey, dogn in KILDER:
        tenant = rigg[tkey]
        _sk(rt, tenant)
        kid = rt.execute(
            "SELECT m20_registrer_kilde(%s,%s,%s,'testrapport',"
            " current_date + %s, %s, %s)",
            (tenant, str(uuid.uuid4()), f"Fasitkilde {merke}", dogn,
             hashlib.sha256(f"{runde}:{merke}".encode()).hexdigest(),
             AKTOR)).fetchone()[0]
        rt.commit()
        rigg["kilder"][merke] = str(kid)
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": str(kid)}
    for merke, _v, tkey, kilde, vises, publiseres in UTKAST:
        tenant = rigg[tkey]
        uid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute("SELECT m20_registrer_utkast(%s,%s,%s,%s,NULL,NULL,%s)",
                   (tenant, uid, f"fasit/{runde}/{merke}",
                    json.dumps({"h1": f"Fasit {merke}"}), AKTOR))
        rt.execute("SELECT m20_registrer_paastand(%s,%s,%s,1,%s,%s,%s)",
                   (tenant, str(uuid.uuid4()), uid, f"Påstand {merke}",
                    rigg["kilder"][kilde], AKTOR))
        rt.commit()
        _sk(rt, tenant)
        rt.execute("SELECT m20_merk_klar(%s,%s,%s)", (tenant, uid, AKTOR))
        rt.commit()
        if vises:
            vid = _vis(rt, tenant, uid)
            if publiseres:
                _sk(rt, tenant)
                rt.execute("SELECT * FROM m20_publiser(%s,%s,%s,%s,"
                           "'u-redaktor',%s)",
                           (tenant, str(uuid.uuid4()), uid, vid, AKTOR))
                rt.commit()
        if not merke.startswith("_"):
            rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": uid}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Visningen som gjør det rene utkastet rent — MELLOM sveipene."""
    _vis(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"])


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _t, _d in KILDER}
    ut.update({m: f for m, f, _t, _k, _v, _p in UTKAST
               if not m.startswith("_")})
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
