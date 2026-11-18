"""M-53s fasitsett: fem regler og tre avvik med KJENT dom.

HMS-registeret har åtte funntyper over to nøkler: regelen (`regel_id`)
og avviket (`avvik_id`). Subjektet leses som
`coalesce(regel_id, avvik_id)` (`hmsfunn_en_noekkel`).

  regel  1. `regelverk_utloper_snart` — gyldig til om 5 døgn (varsel 60)
         2. `regelverk_utlopt`        — gyldig til for 10 døgn siden; egen
                                         avvikstype, så ingenting avløser den
         3. `ingen_krav`              — tenant B har ingen krav
         4. (ren)                     — utløpt; en ny regel for samme
                                         avvikstype registreres MELLOM
                                         kjøringene og avløser den
  avvik  5. `oppbevaring_naermer_seg` — regelen gir 10 døgns oppbevaring
         6. `for_tidlig_anonymisert`  — anonymisert før oppbevaringen gikk ut
         7. (ok)                      — meldt mot en regel med 3650 døgn

`meldt_dato` er i dag og ingen dørparameter, så de tre typene som krever
at tid har gått (`avvik_ubehandlet`, `oppbevaring_utlopt`) eller at en
regel avvikles etter et avvik (`avvik_mot_utlopt_regelverk`), står i
UNAABARE.

Avvikene meldes NAVNGITT: et anonymt avvik har ingen aktør, og en
hendelse uten aktør feller evidenskjeden.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m53-fasit"
EVIDENSKILDE = "m53_hms"
FUNNTABELL = "hmsfunn"
SUBJEKTKOLONNE = "coalesce(regel_id, avvik_id)"

UNAABARE: dict[str, str] = {
    "avvik_ubehandlet":
        "kandidaten krever `meldt_dato + tiltaksfrist_dogn < current_date`. "
        "`m53_meld_avvik` setter meldt_dato til i dag (ingen parameter), og "
        "gulvet for tiltaksfristen er ett døgn (CHECK BETWEEN 1 AND 365), "
        "så et avvik kan ikke være ubehandlet over fristen samme dag.",
    "oppbevaring_utlopt":
        "kandidaten krever `oppbevaring_til < current_date`, og "
        "oppbevaring_til regnes som meldt_dato (i dag) pluss regelens "
        "oppbevaring_dogn, som har gulv ett døgn. Tidligst i morgen.",
    "avvik_mot_utlopt_regelverk":
        "kandidaten krever et avvik meldt mot en regel som SIDEN har "
        "utløpt. Døra nekter å melde mot en regel som ikke gjelder, og det "
        "finnes ingen dør som avvikler en regel etterpå — regelen er "
        "frosset (`m53_regel_frosset`). Funnet oppstår bare når tiden går.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

#: Krav i tenant A: tak 3650, oppbevaringsvarsel 60, tiltaksfrist 14,
#: regelvarsel 60.
KRAV = (3650, 60, 14, 60)

#: (merkelapp, forventet, tenantnøkkel, avvikstype, oppbevaring døgn,
#:  gyldig_til om N døgn eller None)
REGLER: tuple[tuple[str, str | None, str, str, int, int | None], ...] = (
    ("r_lang", None, "a", "naerulykke", 3650, None),
    ("r_kort", None, "a", "materiell", 10, None),
    ("r_utloper", "regelverk_utloper_snart", "a", "sykdom", 3650, 5),
    ("r_utlopt", "regelverk_utlopt", "a", "psykososialt", 3650, -10),
    # Den RENE fødes utløpt: funnet den får i første sveip er nettopp det
    # andre sveip skal lukke, når en ny regel for samme type avløser den.
    ("ren", None, "a", "varsel", 3650, -10),
    ("r_uten_krav", "ingen_krav", "b", "naerulykke", 3650, None),
)

#: (merkelapp, forventet, avvikstype, anonymiseres i riggen)
AVVIK: tuple[tuple[str, str | None, str, bool], ...] = (
    ("naermer", "oppbevaring_naermer_seg", "materiell", False),
    ("for_tidlig", "for_tidlig_anonymisert", "naerulykke", True),
    ("ok", None, "naerulykke", False),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m53fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m53-fasit")


def _regel(rt, tenant: str, rid: str, avvikstype: str, versjon: str,
           dogn: int, til: int | None) -> None:
    _sk(rt, tenant)
    rt.execute(
        "SELECT m53_registrer_regel(%s,%s,%s,%s,'arbeidsmiljoloven 5-1',"
        "%s,false, current_date - 400,"
        " CASE WHEN %s::int IS NULL THEN NULL"
        "      ELSE current_date + %s::int END, %s)",
        (tenant, rid, avvikstype, versjon, dogn, til, til, AKTOR))
    rt.commit()


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN regelen som avløser den rene: den
    kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "runde": runde}
    a = rigg["a"]
    _sk(rt, a)
    rt.execute("SELECT * FROM m53_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
               (a, *KRAV, AKTOR, f"m53fasit-krav-{runde}"))
    rt.commit()
    for merke, _v, tkey, avvikstype, dogn, til in REGLER:
        rid = str(uuid.uuid4())
        _regel(rt, rigg[tkey], rid, avvikstype, f"v1-{merke}-{runde}",
               dogn, til)
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": rid}
    for merke, _v, avvikstype, anonym in AVVIK:
        aid = str(uuid.uuid4())
        _sk(rt, a)
        rt.execute("SELECT * FROM m53_meld_avvik(%s,%s,%s,'navngitt',"
                   "'Fasitavvik','Lageret', current_date,'Fasit Melder',"
                   "'verneombud',%s)", (a, aid, avvikstype, AKTOR))
        rt.commit()
        if anonym:
            _sk(rt, a)
            rt.execute("SELECT * FROM m53_anonymiser(%s,%s,%s,%s)",
                       (a, aid, f"M30-{runde}", AKTOR))
            rt.commit()
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": aid}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """En gyldig regel for samme avvikstype — MELLOM sveipene. Et utløpt
    regelverk er bare et funn når ingenting har avløst det."""
    _regel(rt, rigg["a"], str(uuid.uuid4()), "varsel",
           f"v2-ren-{rigg['runde']}", 3650, None)


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _t, _a, _d, _til in REGLER}
    ut.update({m: f for m, f, _a, _an in AVVIK})
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
