"""M-36s fasitsett: fire subjekter i fire tenanter med KJENT dom.

Optimalisatorens register har fire funntyper på `referanse`. Én er
utelukket av datamodellen, og to kan ikke nås samme dag uten å slå av
en vakt (se `UNAABARE`). Den siste, `korrelasjon_alene_paa_topp`,
rigges på `rangering_id`:

  tenant a  1. `korrelasjon_alene_paa_topp` — ett korrelasjonstiltak,
                                 rangert
  tenant b  2. (ren)           — som 1; mellom kjøringene foreslås et
                                 større regeltiltak og det rangeres på
                                 nytt, så den ferskeste toppen ikke
                                 lenger hviler bare på korrelasjon
  tenant c  3. (ok)            — regeltiltaket er størst og står øverst;
                                 korrelasjonstiltaket står på plass 2
  tenant d  4. `korrelasjon_alene_paa_topp` — samme regel, andre grenser

ÉN TENANT PER SUBJEKT. Sveipen leser bare den FERSKESTE rangeringen i
hver tenant, så to subjekter i samme tenant ville gjort det eldste til
et subjekt sveipen med vilje aldri ser.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m36-fasit"
EVIDENSKILDE = "m36_optimalisator"
FUNNTABELL = "optimaliseringsfunn"
SUBJEKTKOLONNE = "referanse"

UNAABARE: dict[str, str] = {
    "tiltak_uten_reversibilitet":
        "`tiltaksforslag.reversibilitet` er NOT NULL uten standardverdi med "
        "et lukket sett, og rangeringsposten kopierer den; et tiltak uten "
        "vurdert reversibilitet kan ikke settes inn, og sveipen reiser "
        "aldri typen — den står i settet for å navngi vernet.",
    "rangering_uten_maaling":
        "`rangering.laget_dato` settes av døra til current_date og er "
        "append-only (m36_evidensvakt, også for migrator); horisonten er "
        "minst én uke og målefristen minst ett døgn, så en post kan ikke "
        "være over målefristen samme dag den ble rangert (M-48-presedensen).",
    "stopp_staar_uten_oppheving":
        "`portefoljestopp.satt_ts` er DEFAULT now() uten dørparameter og "
        "frosset av m36_stoppvakt, og målefristen har gulv ett døgn (CHECK "
        "1–180); en stopp kan ikke ha stått over fristen samme dag den ble "
        "satt, og riggen ville målt klokka (M-48-presedensen).",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

SETT: tuple[tuple[str, str | None, str], ...] = (
    ("korr", "korrelasjon_alene_paa_topp", "a"),
    ("ren", None, "b"),
    ("ok", None, "c"),
    ("korr_d", "korrelasjon_alene_paa_topp", "d"),
)
# (horisont_uker, maalefrist_dogn, maks_i_rangering) — ulike per tenant.
KRAV = {"a": (12, 14, 10), "b": (13, 15, 11), "c": (14, 16, 12),
        "d": (15, 17, 13)}
KILDE_MODUL = "m13_avstemming"


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m36fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m36-fasit")


def _modell(rt, tenant: str) -> str:
    mid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m36_registrer_modell(%s,%s,'Fasitmodell',"
               " %s, 'Størst anslag først, reversibelt foran',"
               " 'ingen rangering', 1000, current_date, NULL, %s)",
               (tenant, mid, f"v-{mid[:8]}", AKTOR))
    rt.commit()
    return mid


def _tiltak(rt, tenant: str, grunnlagstype: str, anslag: int) -> str:
    tid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m36_foresla_tiltak(%s,%s,%s,%s,%s,"
               " 'reversibel', %s, 'avvik_over_terskel', %s, %s)",
               (tenant, tid, f"Fasittiltak {grunnlagstype} {tid[:8]}",
                grunnlagstype, f"Fasitgrunnlag: {grunnlagstype} målt",
                KILDE_MODUL, anslag, AKTOR))
    rt.commit()
    return tid


def _rangere(rt, tenant: str, modell: str) -> str:
    rid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m36_rangere(%s,%s,%s,%s)",
               (tenant, rid, modell, AKTOR))
    rt.commit()
    return rid


def forbered(rt, runde: str) -> dict:
    """Alle fire tenantene — men UTEN omrangeringen i tenant b: den
    kommer mellom kjøringene."""
    rigg: dict = {t: tenantnavn(runde, t) for t in KRAV}
    rigg["modeller"] = {}
    s: dict[str, str] = {}
    for tkey, krav in KRAV.items():
        t = rigg[tkey]
        _sk(rt, t)
        rt.execute("SELECT * FROM m36_sett_krav(%s,%s,%s,%s,%s,%s)",
                   (t, *krav, AKTOR, f"fasit-{runde}-{tkey}"))
        rt.commit()
        rigg["modeller"][tkey] = _modell(rt, t)
    for merke, _f, tkey in SETT:
        t = rigg[tkey]
        if merke == "ok":
            _tiltak(rt, t, "regel", 9000)
        _tiltak(rt, t, "korrelasjon", 5000)
        s[merke] = _rangere(rt, t, rigg["modeller"][tkey])
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg[t] for t in KRAV]


def kontroller_ren(rt, rigg: dict) -> None:
    """Et større regeltiltak foreslås og tenant b rangeres på nytt —
    MELLOM sveipene. Den gamle rangeringen er append-only; sveipen skal
    se at den ikke lenger er den ferskeste og lukke funnet."""
    t = rigg["b"]
    _tiltak(rt, t, "regel", 9000)
    _rangere(rt, t, rigg["modeller"]["b"])


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
