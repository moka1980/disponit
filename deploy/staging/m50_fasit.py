"""M-50s fasitsett: fire kilder, to poster og fem personer med KJENT dom.

Postjournalregisteret har seks funntyper over TRE subjektslag: kilden
(`kilde_id`), posten (`post_id`) og personen i posten (`person_id`).
Funnraden bærer NØYAKTIG én av nøklene (`journalfunn_en_noekkel`), så
subjektet leses som `coalesce(kilde_id, post_id, person_id)`.

  kilde   1. `kilde_utloper_snart`   — gyldig til om 5 døgn (varsel 60)
          2. `kilde_utlopt`          — satt utløpt etter at en post ble
                                       registrert mot den
          3. `ingen_krav`            — tenant B har ingen krav
          4. (k_gyldig)              — uten sluttdato; postenes hjem
  post    5. `post_mot_utlopt_kilde` — posten mot den utløpte kilden
          6. (p_gyldig)              — posten mot den gyldige kilden
  person  7. `slettefrist_passert`   — frist for 5 døgn siden
          8. `slettefrist_naermer_seg` — frist om 10 døgn (varsel 30)
          9. (ren)                   — passert, ANONYMISERES mellom
                                       kjøringene
         10. (beholdes)              — frist om 200 døgn
         11. (i_utlopt)              — personen i posten mot den utløpte
                                       kilden, frist om 200 døgn

REKKEFØLGEN ER EN AVHENGIGHET, som i M-47: `m50_registrer_post` nekter
uten krav og mot en kilde som ikke gjelder. Posten mot den utløpte
kilden registreres mens den gjelder, og kilden avvikles etterpå.

PERSON-ID-ENE LAGES AV DØRA. Riggen slår dem opp etter navn gjennom
`m50_personene`, som er lesedøra flaten bruker.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m50-fasit"
EVIDENSKILDE = "m50_postjournal"
FUNNTABELL = "journalfunn"
SUBJEKTKOLONNE = "coalesce(kilde_id, post_id, person_id)"

UNAABARE: dict[str, str] = {}
RENSES = "ren"
NEGATIV_KONTROLL = "beholdes"

#: Kravene i tenant A: tak 365 døgn, slettevarsel 30, kildevarsel 60.
KRAV = (365, 30, 60)
FORMAAL = "kartlegging av byggesaker i kommunen"

#: (merkelapp, forventet, tenantnøkkel, gyldig_til om N døgn eller None,
#:  avvikles N døgn tilbake etter postene eller None)
KILDER: tuple[tuple[str, str | None, str, int | None, int | None], ...] = (
    ("k_gyldig", None, "a", None, None),
    ("k_utloper", "kilde_utloper_snart", "a", 5, None),
    ("k_utlopt", "kilde_utlopt", "a", None, 10),
    ("k_uten_krav", "ingen_krav", "b", None, None),
)

#: (merkelapp, forventet, kilde, [(personmerke, forventet, frist om N)])
POSTER: tuple[tuple[str, str | None, str,
                    tuple[tuple[str, str | None, int], ...]], ...] = (
    ("p_gyldig", None, "k_gyldig", (
        ("passert", "slettefrist_passert", -5),
        ("naermer", "slettefrist_naermer_seg", 10),
        # Den RENE fødes passert: funnet den får i første sveip er nettopp
        # det andre sveip skal lukke, når personen er anonymisert.
        ("ren", None, -5),
        ("beholdes", None, 200),
    )),
    ("mot_utlopt", "post_mot_utlopt_kilde", "k_utlopt", (
        ("i_utlopt", None, 200),
    )),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m50fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m50-fasit")


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN anonymiseringen av den rene personen:
    den kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "kilder": {}}
    a = rigg["a"]
    _sk(rt, a)
    rt.execute("SELECT * FROM m50_sett_krav(%s,%s,%s,%s,%s,%s)",
               (a, *KRAV, AKTOR, f"m50fasit-krav-{runde}"))
    rt.commit()

    for merke, _v, tkey, til, _ut in KILDER:
        tenant = rigg[tkey]
        kid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute(
            "SELECT * FROM m50_registrer_kilde(%s,%s,%s,'958935420',"
            "'noark5','2026.1', current_date - 400,"
            " CASE WHEN %s::int IS NULL THEN NULL"
            "      ELSE current_date + %s::int END, %s, NULL, %s)",
            (tenant, kid, f"Fasitkommune {merke}", til, til,
             hashlib.sha256(f"{runde}:{merke}".encode()).hexdigest(),
             AKTOR))
        rt.commit()
        rigg["kilder"][merke] = kid
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": kid}

    sid = str(uuid.uuid4())
    _sk(rt, a)
    rt.execute("SELECT * FROM m50_opprett_sak(%s,%s,'Fasitsak',%s,"
               "'berettiget_interesse',%s)", (a, sid, FORMAAL, AKTOR))
    rt.commit()

    for merke, _v, kilde, personer in POSTER:
        pid = str(uuid.uuid4())
        navn = [f"Fasit {p}" for p, _f, _d in personer]
        _sk(rt, a)
        rt.execute(
            "SELECT * FROM m50_registrer_post(%s,%s,%s,%s,%s,"
            " current_date,'Fasitpost',%s,'Fasit',current_date,"
            " %s,%s,"
            " ARRAY(SELECT current_date + d FROM unnest(%s::int[]) d),"
            " %s)",
            (a, pid, sid, rigg["kilder"][kilde], f"FASIT-{merke}", FORMAAL,
             navn, ["part"] * len(personer),
             [d for _p, _f, d in personer], AKTOR))
        rt.commit()
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": pid}
        # PERSON-ID-ENE ER DØRAS — de slås opp etter navn.
        _sk(rt, a)
        ider = {n: str(p) for p, n in rt.execute(
            "SELECT person_id, navn FROM m50_personene(%s,%s)",
            (a, pid)).fetchall()}
        rt.rollback()
        for p, _f, _d in personer:
            rigg["subjekter"][p] = {"merke": p,
                                    "subjekt_id": ider[f"Fasit {p}"]}

    # Avviklingen kommer ETTER postene: døra nekter en post mot en kilde
    # som ikke gjelder.
    for merke, _v, tkey, _til, utlopt in KILDER:
        if utlopt is None:
            continue
        _sk(rt, rigg[tkey])
        rt.execute("SELECT * FROM m50_sett_gyldig_til(%s,%s,"
                   " current_date - %s, %s)",
                   (rigg[tkey], rigg["kilder"][merke], utlopt, AKTOR))
        rt.commit()
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Anonymiseringen som gjør den rene personen ren — MELLOM sveipene.
    Sveipen leser bare personer med `anonymisert_ts IS NULL`."""
    _sk(rt, rigg["a"])
    rt.execute("SELECT * FROM m50_anonymiser(%s,%s,%s)",
               (rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"], AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _t, _til, _u in KILDER}
    for m, f, _k, personer in POSTER:
        ut[m] = f
        ut.update({p: pf for p, pf, _d in personer})
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
