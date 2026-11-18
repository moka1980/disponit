"""M-54s fasitsett: fire regelsett og fem valideringer med KJENT dom.

EHF-registeret har sju funntyper over to subjektslag her: regelsettet
(`regelsett_id`) og valideringen (`validering_id`). Funnraden bærer
NØYAKTIG én nøkkel (`ehffunn_en_noekkel`), så subjektet leses som
`coalesce(regelsett_id, dokument_id, validering_id)`.

  regelsett  1. `regelsett_utloper_snart` — gyldig til om 5 døgn (varsel 30)
             2. `regelsett_utlopt`        — avviklet etter at et dokument
                                            ble validert mot det
             3. `ingen_krav`              — tenant B har ingen krav
             4. (rs_gyldig)               — uten sluttdato
  validering 5. `avvik_uten_retting`      — et formfeil-avvik uten retting
             6. `retting_ikke_klar`       — rettet, men ikke merket klar
             7. `validering_mot_utlopt_regelsett` — feilfri, men mot det
                                            avviklede regelsettet
             8. (ren)                     — avvik uten retting; rettes og
                                            merkes klar MELLOM kjøringene
             9. (ok)                      — feilfri mot det gyldige settet

AVVIKET LAGES SOM I MODULENS EGNE TESTER: regelen krever at
`Invoice/A` finnes, og dokumentet har bare `Invoice/C`. Det gir
alvorlighet `feil` — ikke `uten_grunnlag`, som ikke kan rettes.

REKKEFØLGEN ER EN AVHENGIGHET: `m54_valider_dokument` nekter mot et
regelsett som ikke gjelder, så dokumentet valideres mens det gjelder og
regelsettet avvikles etterpå.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m54-fasit"
EVIDENSKILDE = "m54_ehf"
FUNNTABELL = "ehffunn"
SUBJEKTKOLONNE = "coalesce(regelsett_id, dokument_id, validering_id)"

UNAABARE: dict[str, str] = {
    "dokument_uten_validering":
        "kandidaten krever at et dokument er registrert for mer enn "
        "`avviksfrist_dogn` siden uten validering, og gulvet for den "
        "terskelen er ett døgn (CHECK BETWEEN 1 AND 365). "
        "`ehfdokument.registrert` er DEFAULT now() og ingen dørparameter "
        "— `m54_registrer_dokument` tar fakturadatoen, ikke "
        "registreringen — så funnet kan ikke lages på én dag.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

#: Kravene i tenant A: utløpsvarsel 30 døgn, avviksfrist 7.
KRAV = (30, 7)

#: (merkelapp, forventet, tenantnøkkel, gyldig_til om N døgn eller None,
#:  avvikles N døgn tilbake etter valideringene eller None)
REGELSETT: tuple[tuple[str, str | None, str, int | None, int | None],
                 ...] = (
    ("rs_gyldig", None, "a", None, None),
    ("rs_utloper", "regelsett_utloper_snart", "a", 5, None),
    ("rs_utlopt", "regelsett_utlopt", "a", None, 10),
    ("rs_uten_krav", "ingen_krav", "b", None, None),
)

#: (merkelapp, forventet, regelsett, feltet dokumentet har,
#:  "ingen" | "retting" — hva som skjer med avviket i riggen)
VALIDERINGER: tuple[tuple[str, str | None, str, str, str], ...] = (
    ("uten_retting", "avvik_uten_retting", "rs_gyldig", "Invoice/C",
     "ingen"),
    ("ikke_klar", "retting_ikke_klar", "rs_gyldig", "Invoice/C", "retting"),
    ("mot_utlopt", "validering_mot_utlopt_regelsett", "rs_utlopt",
     "Invoice/A", "ingen"),
    # Den RENE fødes med et urettet avvik: funnet den får i første sveip
    # er nettopp det andre sveip skal lukke.
    ("ren", None, "rs_gyldig", "Invoice/C", "ingen"),
    ("ok", None, "rs_gyldig", "Invoice/A", "ingen"),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m54fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m54-fasit")


def _sha(*deler: str) -> str:
    return hashlib.sha256(":".join(deler).encode()).hexdigest()


def _avviket(rt, tenant: str, vid: str) -> str:
    _sk(rt, tenant)
    aid = rt.execute("SELECT avvik_id FROM m54_avvikene(%s,%s)"
                     " WHERE alvorlighet = 'feil'", (tenant, vid)).fetchone()[0]
    rt.rollback()
    return str(aid)


def _rett(rt, tenant: str, vid: str, merke: str, *, klar: bool) -> None:
    rid = str(uuid.uuid4())
    # Avviket FØR konteksten: oppslaget ruller tilbake, og tenant-
    # konteksten er transaksjonslokal — den ville forsvunnet med det.
    aid = _avviket(rt, tenant, vid)
    _sk(rt, tenant)
    rt.execute("SELECT m54_registrer_retting(%s,%s,%s,'Invoice/A',NULL,'a',"
               "%s,%s)", (tenant, rid, aid, f"fasit {merke}", AKTOR))
    rt.commit()
    if klar:
        _sk(rt, tenant)
        rt.execute("SELECT m54_merk_klar(%s,%s,%s)", (tenant, rid, AKTOR))
        rt.commit()


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN rettingen av den rene valideringen: den
    kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "regelsett": {}}
    a = rigg["a"]
    _sk(rt, a)
    rt.execute("SELECT m54_sett_krav(%s,%s,%s,%s,%s)",
               (a, *KRAV, AKTOR, f"m54fasit-krav-{runde}"))
    rt.commit()

    for nr, (merke, _v, tkey, til, _ut) in enumerate(REGELSETT):
        tenant = rigg[tkey]
        sid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute(
            "SELECT m54_registrer_regelsett(%s,%s,'ehf',%s,"
            " current_date - 400, CASE WHEN %s::int IS NULL THEN NULL"
            " ELSE current_date + %s::int END, %s, NULL, %s)",
            (tenant, sid, f"3.{nr}-{runde}", til, til,
             _sha(runde, merke), AKTOR))
        rt.commit()
        # Én regel i hvert sett: valideringen nekter et tomt regelsett.
        _sk(rt, tenant)
        rt.execute(
            "SELECT m54_registrer_regel(%s,%s,%s,'R-A','Invoice/A','finnes',"
            "%s,NULL,'feil','Invoice/A må finnes',%s)",
            (tenant, str(uuid.uuid4()), sid, [], AKTOR))
        rt.commit()
        rigg["regelsett"][merke] = sid
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": sid}

    for merke, _v, rs, felt, steg in VALIDERINGER:
        did, vid = str(uuid.uuid4()), str(uuid.uuid4())
        _sk(rt, a)
        rt.execute(
            "SELECT m54_registrer_dokument(%s,%s,'utgaaende',%s,'Kunde AS',"
            " current_date, %s, 8192, %s, %s)",
            (a, did, f"F-{merke}-{runde}", _sha(runde, merke, "dok"),
             f"artefakt/{runde}/{merke}", AKTOR))
        rt.commit()
        _sk(rt, a)
        rt.execute("SELECT m54_registrer_felter(%s,%s,%s,%s,%s,%s,%s)",
                   (a, did, [felt], [0], ["x"], [None], AKTOR))
        rt.commit()
        _sk(rt, a)
        rt.execute("SELECT * FROM m54_valider_dokument(%s,%s,%s,%s,%s)",
                   (a, did, rigg["regelsett"][rs], vid, AKTOR))
        rt.commit()
        if steg == "retting":
            _rett(rt, a, vid, merke, klar=False)
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": vid}

    # Avviklingen kommer ETTER valideringene: døra nekter en validering
    # mot et regelsett som ikke gjelder.
    for merke, _v, tkey, _til, utlopt in REGELSETT:
        if utlopt is None:
            continue
        _sk(rt, rigg[tkey])
        rt.execute("SELECT * FROM m54_sett_gyldig_til(%s,%s,"
                   " current_date - %s, %s)",
                   (rigg[tkey], rigg["regelsett"][merke], utlopt, AKTOR))
        rt.commit()
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Rettingen OG klarmerkingen som gjør den rene valideringen ren —
    MELLOM sveipene. Retting alene ville byttet funnet ut med
    `retting_ikke_klar`."""
    _rett(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"], RENSES,
          klar=True)


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _t, _til, _u in REGELSETT}
    ut.update({m: f for m, f, _rs, _felt, _s in VALIDERINGER})
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
