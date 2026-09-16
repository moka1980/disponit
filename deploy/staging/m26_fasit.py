"""Det syntetiske prisboksettet for M-26s sertifisering.

ETT SETT, TO LEDD, samme form som m44/m17/m23/m6: settet drives gjennom
de EKTE dørene både lokalt (CI, `test_m26_fasit_port.py`) og på staging
(`m26-fasit-artefakt.py`). Artefaktet bærer `sett_sha256` over bytene i
denne filen, og porten krever likhet med de innsjekkede.

MANIFESTETS KRAV, ORDRETT: «en prisbok med KJENT fasit for hvilke
versjoner som gjaldt når». Sveipen (`m26_funnkandidater`) har tre
funntyper: `pris_utloper_snart` (kanten `gyldig_til <= i dag +
utlop_varsel_dogn`), `uten_gyldig_pris` (kanten `> uten_pris_dogn` siden
produktet ble opprettet eller siste pris gikk ut) og `ingen_terskel`.

DØRENE ER KUNDENS. En pris får `gyldig_til` på ÉN måte: en NY versjon
med senere `gyldig_fra` lukker den forrige dagen før (`m26_sett_pris`).
Slik oppstår «utløper snart» i virkeligheten — og slik lages den her.
`uten_gyldig_pris` kan IKKE oppstå samme dag som produktet registreres:
regelen teller døgn fra `opprettet` eller siste pris' `gyldig_til`, og
begge er i dag. Kanten måles av modulens egen port
(`test_m26_prisbok.py`); her måles at raden UTEN pris IKKE er et funn.

TERSKELEN STRAMMES ETTER PRISENE ER SATT (varselvinduet 10 → 30 døgn):
dørene nekter ingenting i prisboka, men funnene skal følge den
GJELDENDE terskelen, ikke den prisen ble satt under. Uten terskel er
hvert aktivt produkt et `ingen_terskel`-funn — sveipen sier fra om at
tenanten aldri satte reglene sine.

TRE FASITER, skrevet FØR kjøringen: funnene (kantene 30/31 døgn, i dag,
om fem, deaktivert, uten pris, kun framtidig pris), boka slik flaten
viser den (`api.prisbok.svar_for`: gjeldende versjon, antall versjoner,
døgn til utløp, funn per produkt, sammendraget), og EVIDENSKJEDEN: hver
hendelse dørene skriver (`terskler.satt`, `produkt.registrert`,
`pris.satt`, `klausul.satt`, `produkt.aktiv`) med identitet — pluss
prishistorikken per produkt, som er det et tilsyn spør etter: hva
gjaldt når, satt av hvem.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from pathlib import Path

SETT_VERSJON = "m26-fasit-1"
AKTOR = "m26-fasit"

#: Terskelen prisene settes UNDER, og terskelen det STRAMMES TIL.
TERSKLER_FOR = {"rabatt": 100, "varsel": 10, "utenpris": 7}
TERSKLER_ETTER = {"rabatt": 100, "varsel": 30, "utenpris": 7}

#: (merke, priser [(øre, gyldig fra i dager relativt til i dag)],
#:  handling etter prisene, ventede funn)
#:
#: Rekkefølgen er dørens: produktet registreres, prisene settes i
#: rekkefølge (hver ny versjon lukker den forrige dagen før), så skjer
#: «handling etter» — og til slutt strammes terskelen og sveipen går.
SETT_MED_TERSKEL: list[tuple] = [
    ("uten_utlop",          [(10_000, -100)],                    None,        set()),
    # Neste versjon fra +31 → gjeldende pris løper til +30: PÅ kanten.
    ("utloper_kant_30",     [(10_000, -100), (11_000, 31)],      None,        {"pris_utloper_snart"}),
    # Neste versjon fra +32 → løper til +31: én utenfor vinduet.
    ("utloper_kant_31",     [(10_000, -100), (11_000, 32)],      None,        set()),
    ("utloper_om_5",        [(10_000, -100), (12_000, 6)],       None,        {"pris_utloper_snart"}),
    ("utloper_i_dag",       [(10_000, -100), (12_000, 1)],       None,        {"pris_utloper_snart"}),
    ("deaktivert_utloper",  [(10_000, -100), (12_000, 6)],       "deaktiver", set()),
    # Ingen pris — og INGEN funn i dag: regelen teller døgn fra
    # registreringen, og den var i dag.
    ("uten_pris",           [],                                  None,        set()),
    ("kun_framtidig_pris",  [(10_000, 5)],                       None,        set()),
    ("tre_versjoner",       [(9_000, -300), (9_500, -200), (10_000, -100)], None, set()),
]
SETT_UTEN_TERSKEL: list[tuple] = [
    ("aktiv_uten_terskel",      [(10_000, -100)], None,        {"ingen_terskel"}),
    ("aktiv_uten_terskel_2",    [],               None,        {"ingen_terskel"}),
    ("deaktivert_uten_terskel", [(10_000, -100)], "deaktiver", set()),
]


def sett_sha256() -> str:
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def bygg_sett() -> list[tuple[str, list]]:
    return [("med_terskel", list(SETT_MED_TERSKEL)),
            ("uten_terskel", list(SETT_UTEN_TERSKEL))]


def tenantnavn(runde_id: str, rolle: str) -> str:
    return f"t-m26fasit-{rolle}-{runde_id}"


def ny_runde() -> str:
    return secrets.token_hex(4)


def _sk(rt, tenant, aktor):
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, aktor, "m26-fasit")


def _legg_inn(rt, tenant, rader, med_terskel: bool, aktor: str,
              runde_id: str) -> dict:
    """Settet inn gjennom kundens egne dører. -> {merke: produkt_id}."""
    if med_terskel:
        _sk(rt, tenant, aktor)
        rt.execute("SELECT m26_sett_terskler(%s,%s,%s,%s,%s)",
                   (tenant, TERSKLER_FOR["rabatt"], TERSKLER_FOR["varsel"],
                    TERSKLER_FOR["utenpris"], aktor))
        rt.commit()
    _sk(rt, tenant, aktor)
    rt.execute("SELECT m26_sett_klausul(%s,%s,%s,%s,true,current_date,%s)",
               (tenant, "FASIT-LEV", "Levering",
                "Levering skjer innen 30 dager.", aktor))
    rt.commit()
    ider = {}
    for i, (merke, priser, handling, _funn) in enumerate(rader):
        pid = uuid.uuid4()
        _sk(rt, tenant, aktor)
        rt.execute("SELECT m26_registrer_produkt(%s,%s,%s,%s,%s,%s)",
                   (tenant, pid, f"P-{i:02d}-{runde_id}", f"Fasit {merke}",
                    "stk", aktor))
        rt.commit()
        for ore, dager in priser:
            _sk(rt, tenant, aktor)
            rt.execute(
                "SELECT m26_sett_pris(%s,%s,%s,'NOK',current_date + %s::int,"
                "       %s,%s)",
                (tenant, pid, ore, dager, f"fasit {merke}", aktor))
            rt.commit()
        if handling == "deaktiver":
            _sk(rt, tenant, aktor)
            rt.execute("SELECT m26_sett_produktaktiv(%s,%s,false,%s)",
                       (tenant, pid, aktor))
            rt.commit()
        ider[merke] = pid
    if med_terskel:
        # TERSKELEN STRAMMES: varselvinduet 10 → 30 døgn. Funnene skal
        # følge den gjeldende terskelen, ikke den prisen ble satt under.
        _sk(rt, tenant, aktor)
        rt.execute("SELECT m26_sett_terskler(%s,%s,%s,%s,%s)",
                   (tenant, TERSKLER_ETTER["rabatt"],
                    TERSKLER_ETTER["varsel"], TERSKLER_ETTER["utenpris"],
                    aktor))
        rt.commit()
    return ider


def les_flaten(rt, tenant: str, aktor: str) -> dict:
    from api.prisbok import svar_for
    _sk(rt, tenant, aktor)
    svar = svar_for(rt, tenant)
    rt.rollback()
    return svar


def _gjeldende(priser) -> tuple[int | None, int | None]:
    """(gjeldende versjon, døgn til utløp) slik boka svarer i dag."""
    versjon = None
    for i, (_ore, dager) in enumerate(priser, start=1):
        if dager <= 0:
            versjon = i
    if versjon is None:
        return None, None
    if versjon < len(priser):
        return versjon, priser[versjon][1] - 1
    return versjon, None


def maal(rt, tenant: str, ider: dict, rader: list, med_terskel: bool,
         aktor: str) -> dict:
    """-> {"funnavvik": [...], "koeavvik": [...]} — RETURNERT, aldri kastet."""
    svar = les_flaten(rt, tenant, aktor)
    per_id = {p["produkt_id"]: p for p in svar["produkter"]}
    funnavvik: list[str] = []
    koeavvik: list[str] = []
    s = svar["sammendrag"]
    if len(svar["produkter"]) != s["vist"] or len(per_id) < len(rader):
        funnavvik.append(f"flaten viste {len(per_id)} av {len(rader)}"
                         f" produkter (vist={s['vist']})")
    for merke, priser, handling, ventet in rader:
        rad = per_id.get(str(ider[merke]))
        if rad is None:
            funnavvik.append(f"{merke}: produktet står ikke i flaten")
            continue
        fikk = set(rad["apne_funn"])
        if fikk != ventet:
            funnavvik.append(f"{merke}: ventet {sorted(ventet)}, fikk"
                             f" {sorted(fikk)}")
        # Boka: aktiv-flagget, antall versjoner, gjeldende versjon og
        # døgn til utløp — «hva gjaldt når».
        if rad["aktiv"] != (handling != "deaktiver"):
            koeavvik.append(f"{merke}: aktiv={rad['aktiv']}")
        if rad["versjoner"] != len(priser):
            koeavvik.append(f"{merke}: versjoner={rad['versjoner']}, ventet"
                            f" {len(priser)}")
        versjon, dogn = _gjeldende(priser)
        if rad["versjon"] != versjon or rad["dogn_til_utlop"] != dogn:
            koeavvik.append(f"{merke}: gjeldende versjon={rad['versjon']},"
                            f" døgn til utløp={rad['dogn_til_utlop']},"
                            f" ventet {versjon}/{dogn}")
        if versjon is not None and rad["listepris_ore"] != priser[versjon - 1][0]:
            koeavvik.append(f"{merke}: listepris={rad['listepris_ore']},"
                            f" ventet {priser[versjon - 1][0]}")
    aktive = sum(1 for r in rader if r[2] != "deaktiver")
    med_pris = sum(1 for r in rader if r[2] != "deaktiver"
                   and _gjeldende(r[1])[0] is not None)
    ventet_s = {"produkter": len(rader), "aktive": aktive,
                "med_gyldig_pris": med_pris,
                "apne_funn": sum(len(r[3]) for r in rader),
                "har_terskel": med_terskel,
                "terskelversjon": 2 if med_terskel else None,
                "klausuler": 1, "standardklausuler": 1}
    for n, v in ventet_s.items():
        if s.get(n) != v:
            koeavvik.append(f"sammendrag.{n}={s.get(n)!r}, ventet {v!r}")
    t = svar["terskler"]
    if med_terskel and (t is None
                        or t["utlop_varsel_dogn"] != TERSKLER_ETTER["varsel"]
                        or t["versjon"] != 2):
        koeavvik.append(f"terskler={t!r}, ventet den strammede (versjon 2)")
    if not med_terskel and t is not None:
        koeavvik.append("terskler satt i tenanten uten terskel")
    return {"funnavvik": funnavvik, "koeavvik": koeavvik}


def forventet_evidens(rader, med_terskel: bool) -> dict[str, int]:
    """Kjeden per tenant, utledet av radene: én per dør som ble kalt."""
    return {
        "terskler.satt": 2 if med_terskel else 0,
        "klausul.satt": 1,
        "produkt.registrert": len(rader),
        "pris.satt": sum(len(r[1]) for r in rader),
        "produkt.aktiv": sum(1 for r in rader if r[2] == "deaktiver"),
    }


def maal_evidens(rt, tenant: str, ider: dict, rader, med_terskel: bool,
                 aktor: str) -> list[str]:
    _sk(rt, tenant, aktor)
    rader_db = rt.execute(
        "SELECT handling, aktor, input_hash FROM revisjonslogg"
        " WHERE tenant=%s AND kilde='m26_prisbok'", (tenant,)).fetchall()
    rt.rollback()
    ventet = {k: v for k, v in forventet_evidens(rader, med_terskel).items() if v}
    talt: dict[str, int] = {}
    hasher: dict[str, set] = {}
    # Aktøren skal være DEN som gikk gjennom døra — ikke bare «noen».
    annen_aktor = 0
    for handling, akt, ih in rader_db:
        talt[handling] = talt.get(handling, 0) + 1
        hasher.setdefault(handling, set()).add(ih)
        if akt != aktor:
            annen_aktor += 1
    avvik = []
    for handling, antall in ventet.items():
        if talt.get(handling, 0) != antall:
            avvik.append(f"{handling}: ventet {antall}, fikk {talt.get(handling, 0)}")
        elif len(hasher.get(handling, ())) != antall:
            avvik.append(f"{handling}: {len(hasher[handling])} ulike input_hash"
                         f" for {antall} hendelser")
    ukjent = set(talt) - set(ventet)
    if ukjent:
        avvik.append(f"handlinger fasiten ikke venter: {sorted(ukjent)}")
    if annen_aktor:
        avvik.append(f"{annen_aktor} hendelser med annen eller ingen aktør")
    # PRISHISTORIKKEN — det et tilsyn spør etter: hva gjaldt når, satt av
    # hvem. Lest gjennom døra, som flaten gjør det.
    for merke, priser, _h, _f in rader:
        pid = ider[merke]
        _sk(rt, tenant, aktor)
        hist = rt.execute("SELECT * FROM m26_prishistorikken(%s,%s)",
                          (tenant, pid)).fetchall()
        rt.rollback()
        if len(hist) != len(priser):
            avvik.append(f"{merke}: {len(hist)} versjoner i historikken,"
                         f" ventet {len(priser)}")
            continue
        for h, (ore, _d) in zip(sorted(hist, key=lambda r: r[0]), priser):
            # versjon, listepris_ore, valuta, gyldig_fra, gyldig_til,
            # begrunnelse, opprettet, opprettet_av
            if h[1] != ore or not h[3] or not h[5] or h[7] != aktor:
                avvik.append(f"{merke}: versjon {h[0]} uten pris, dato,"
                             " begrunnelse eller med annen aktør")
        # Bare den siste versjonen står åpen; hver forrige ble lukket
        # dagen før den neste.
        lukkede = [h for h in hist if h[4] is not None]
        if len(lukkede) != max(len(priser) - 1, 0):
            avvik.append(f"{merke}: {len(lukkede)} lukkede versjoner,"
                         f" ventet {max(len(priser) - 1, 0)}")
    return avvik


def kjor_sett(runde_id: str, rt, sveip, aktor: str = AKTOR) -> dict:
    ut = {}
    for rolle, rader in bygg_sett():
        tenant = tenantnavn(runde_id, rolle)
        ider = _legg_inn(rt, tenant, rader, rolle == "med_terskel", aktor,
                         runde_id)
        ut[rolle] = {"tenant": tenant, "ider": ider, "rader": rader}
    sveip()
    for rolle, d in ut.items():
        d["avvik"] = maal(rt, d["tenant"], d["ider"], d["rader"],
                          rolle == "med_terskel", aktor)
        d["avvik"]["evidensavvik"] = maal_evidens(
            rt, d["tenant"], d["ider"], d["rader"], rolle == "med_terskel",
            aktor)
    return ut


AKSER = ("funnavvik", "koeavvik", "evidensavvik")


def artefakt(kjoring: dict, vert: str, ts: str, sveipetid_ms: int,
             sveip_tenanter: int, bevisrot: str) -> dict:
    talt = {akse: sum(len(d["avvik"][akse]) for d in kjoring.values())
            for akse in AKSER}
    produkter = sum(len(d["rader"]) for d in kjoring.values())
    ventede_funn = sum(len(r[3]) for d in kjoring.values() for r in d["rader"])
    evidens = sum(sum(forventet_evidens(d["rader"], rolle == "med_terskel").values())
                  for rolle, d in kjoring.items())
    return {
        "krav_id": "m26-fasit-v1",
        "ts": ts,
        "bestatt": all(v == 0 for v in talt.values()),
        "oppsett": {
            "modul": "m26_prisbok", "vert": vert,
            "sett_versjon": SETT_VERSJON, "sett_sha256": sett_sha256(),
            "bevisrot_sha256": bevisrot,
            "tenanter": sorted(d["tenant"] for d in kjoring.values()),
        },
        "maalt": {
            "produkter": produkter,
            "ventede_funn": ventede_funn,
            "evidenshendelser": evidens,
            **talt,
            "sveipetid_ms": sveipetid_ms,
            "sveip_tenanter": sveip_tenanter,
        },
        "avvik": {rolle: d["avvik"] for rolle, d in sorted(kjoring.items())},
    }
