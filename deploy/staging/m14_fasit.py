"""Det syntetiske fakturasettet for M-14s sertifisering.

ETT SETT, TO LEDD, samme form som m26/m44/m17/m23/m6: settet drives
gjennom de EKTE dørene både lokalt (CI, `test_m14_fasit_port.py`) og på
staging (`m14-fasit-artefakt.py`). Artefaktet bærer `sett_sha256` over
bytene i denne filen, og porten krever likhet med de innsjekkede.

MANIFESTETS KRAV, ORDRETT: «et fakturasett med KJENT fasit for hvilke
som har avvik». Sveipen (`m14_funnkandidater`) har sju funntyper.
Tre av dem er FROSSET VED REGISTRERINGEN — døra kjører kontrollene idet
fakturaen kommer inn, og sveipen leser utfallet: `mva_avvik` (avviket
`> mva_slingring_ore`), `ingen_mvasats` (ingen sats gjaldt
fakturadatoen — en EGEN type), `ukjent_leverandor` (ingen aktiv rad i
M-24). Tre regnes LIVE mot den gjeldende terskelen: `naer_dublett`
(samme leverandør, samme beløp, `|utstedt-diff| <= dublettvindu`),
`over_belopsgrense` (`brutto > belopsgrense` uten manuell kontroll),
`ukontrollert` (`i dag - mottatt > kontrollfrist`). Og `ingen_terskel`
for tenanten som aldri satte reglene sine.

TERSKELEN STRAMMES ETTER REGISTRERINGEN (beløpsgrense 100 000 → 25 000
kr, kontrollfrist 30 → 7 døgn): de tre live-typene skal følge den
GJELDENDE terskelen, de tre frosne skal IKKE endre seg. Det er slik
funnene oppstår i drift — grensen ble strammet, ikke fakturaen endret.

EN KJENT LEVERANDØR ER EN RAD I M-24 (`m24_registrer_leverandor`) —
gjennom M-24s dør, som kunden gjør det. Den ukjente er bare et navn.

TRE FASITER, skrevet FØR kjøringen: funnene (kantene 1/2 øre, 3/4
døgn, 25 000/25 001 kr, 7/8 døgn, pluss kontrollert, avvist og manuelt
kontrollert), flaten (`api.faktura.svar_for`: status, kontroller og
avvik per faktura, sammendraget, treffraten per kontrolltype), og
EVIDENSKJEDEN: hver hendelse dørene skriver (`terskler.satt`,
`mvasats.satt`, `faktura.registrert`, `kontroll.registrert`,
`faktura.avgjort`) med identitet — pluss kontrollene per faktura
(`m14_kontrollene`), som er det et tilsyn spør etter: hva ble sjekket,
med hvilket utfall, av hvem.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from pathlib import Path

SETT_VERSJON = "m14-fasit-1"
AKTOR = "m14-fasit"

#: Terskelen fakturaene registreres UNDER, og terskelen det STRAMMES TIL.
TERSKLER_FOR = {"slingring": 1, "grense": 10_000_000, "frist": 30, "vindu": 3}
TERSKLER_ETTER = {"slingring": 1, "grense": 2_500_000, "frist": 7, "vindu": 3}

#: Satsene: «hoy» gjelder, «lav» gikk ut ved nyttår — en faktura med
#: «lav» i 2026 har ingen sats som gjaldt datoen.
SATSER = [("hoy", 250, "2020-01-01", None), ("lav", 120, "2020-01-01", "2025-12-31")]

KJENT = "Fasit Leverandør AS"
UKJENT = "Aldri Sett AS"

#: (merke, leverandør, netto øre, mva øre, satskode, utstedt (dager
#:  relativt til i dag), mottatt for N døgn siden, handling etter
#:  registreringen, ventede funn)
#:
#: Rekkefølgen er dørens: fakturaene registreres i denne rekkefølgen
#: (dublettkontrollen ved registreringen ser bare de FORRIGE), så skjer
#: «handling etter» — og til slutt strammes terskelen og sveipen går.
SETT_MED_TERSKEL: list[tuple] = [
    ("ren",                      KJENT,  10_000,    2_500,   "hoy", -10, 1, None,         set()),
    ("mva_avvik_2",              KJENT,  10_000,    2_502,   "hoy", -10, 1, None,         {"mva_avvik"}),
    ("mva_paa_slingringen_1",    KJENT,  10_000,    2_501,   "hoy", -10, 1, None,         set()),
    ("ingen_sats",               KJENT,  10_000,    1_200,   "lav", -10, 1, None,         {"ingen_mvasats"}),
    ("ukjent_leverandor",        UKJENT, 10_000,    2_500,   "hoy", -10, 1, None,         {"ukjent_leverandor"}),
    # Samme leverandør, samme beløp, tre døgn mellom: PÅ vinduet — begge
    # er funn (hver finner den andre).
    ("dublett_a",                KJENT,  20_000,    5_000,   "hoy", -13, 1, None,         {"naer_dublett"}),
    ("dublett_b",                KJENT,  20_000,    5_000,   "hoy", -10, 1, None,         {"naer_dublett"}),
    # Fire døgn mellom: én utenfor vinduet.
    ("dublett_utenfor_a",        KJENT,  30_000,    7_500,   "hoy", -14, 1, None,         set()),
    ("dublett_utenfor_b",        KJENT,  30_000,    7_500,   "hoy", -10, 1, None,         set()),
    # Brutto 26 000 kr: under den romslige grensen ved registreringen,
    # over den strammede ved sveipen.
    ("over_grensen_etter_stramming", KJENT, 2_080_000, 520_000, "hoy", -10, 1, None,      {"over_belopsgrense"}),
    ("paa_grensen",              KJENT,  2_000_000, 500_000, "hoy", -10, 1, None,         set()),
    # Over grensen, men manuelt kontrollert — ikke et funn.
    ("over_grensen_manuelt",     KJENT,  2_080_000, 520_000, "hoy", -20, 1, "manuell",    set()),
    ("ukontrollert_8",           KJENT,  40_000,    10_000,  "hoy", -10, 8, None,         {"ukontrollert"}),
    ("paa_fristen_7",            KJENT,  41_000,    10_250,  "hoy", -10, 7, None,         set()),
    # Avgjort ETTER registreringen: en kontrollert faktura er ute av
    # sveipen selv med et mva-avvik, en avvist likeså.
    ("kontrollert_med_avvik",    KJENT,  10_000,    2_510,   "hoy", -10, 1, "kontroller", set()),
    ("avvist",                   UKJENT, 11_000,    2_750,   "hoy", -10, 1, "avvis",      set()),
]
SETT_UTEN_TERSKEL: list[tuple] = [
    ("mottatt_uten_terskel",     "Noen AS", 10_000, 2_500, "hoy", -10, 1, None,         {"ingen_terskel"}),
    ("mottatt_uten_terskel_2",   "Noen AS", 15_000, 3_750, "hoy", -10, 1, None,         {"ingen_terskel"}),
    ("kontrollert_uten_terskel", "Noen AS", 10_000, 2_500, "hoy", -20, 1, "kontroller", set()),
]


def sett_sha256() -> str:
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def bygg_sett() -> list[tuple[str, list]]:
    return [("med_terskel", list(SETT_MED_TERSKEL)),
            ("uten_terskel", list(SETT_UTEN_TERSKEL))]


def tenantnavn(runde_id: str, rolle: str) -> str:
    return f"t-m14fasit-{rolle}-{runde_id}"


def ny_runde() -> str:
    return secrets.token_hex(4)


def _sk(rt, tenant, aktor):
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, aktor, "m14-fasit")


def _forventet_mva(netto: int, promille: int) -> int:
    """Som `m14_forventet_mva`: heltall, halv opp."""
    return (netto * promille + 500) // 1000


def _legg_inn(rt, tenant, rader, med_terskel: bool, aktor: str,
              runde_id: str) -> dict:
    """Settet inn gjennom kundens egne dører. -> {merke: faktura_id}."""
    if med_terskel:
        _sk(rt, tenant, aktor)
        rt.execute("SELECT m14_sett_terskler(%s,%s,%s,%s,%s,%s)",
                   (tenant, TERSKLER_FOR["slingring"], TERSKLER_FOR["grense"],
                    TERSKLER_FOR["frist"], TERSKLER_FOR["vindu"], aktor))
        rt.commit()
        # Den kjente leverandøren — gjennom M-24s dør.
        _sk(rt, tenant, aktor)
        rt.execute("SELECT m24_registrer_leverandor(%s,%s,%s,NULL,%s)",
                   (tenant, uuid.uuid4(), KJENT, aktor))
        rt.commit()
    for kode, promille, fra, til in SATSER:
        _sk(rt, tenant, aktor)
        rt.execute("SELECT m14_sett_mvasats(%s,%s,%s,%s::date,%s::date,%s)",
                   (tenant, kode, promille, fra, til, aktor))
        rt.commit()
    ider = {}
    for i, (merke, ref, netto, mva, kode, utstedt, siden, handling, _f) in enumerate(rader):
        fid = uuid.uuid4()
        _sk(rt, tenant, aktor)
        rt.execute(
            "SELECT m14_registrer_faktura(%s,%s,%s,%s,%s,%s,%s,%s,'NOK',"
            "       current_date + %s::int, current_date + %s::int + 30,"
            "       current_date - %s::int, %s)",
            (tenant, fid, ref, f"F-{i:02d}-{runde_id}", netto, mva,
             netto + mva, kode, utstedt, utstedt, siden, aktor))
        rt.commit()
        if handling == "manuell":
            _sk(rt, tenant, aktor)
            rt.execute("SELECT m14_registrer_kontroll(%s,%s,%s,'ok',%s,%s)",
                       (tenant, uuid.uuid4(), fid,
                        "Fasit: beløpet er kontrollert mot bestillingen.",
                        aktor))
            rt.commit()
        elif handling in ("kontroller", "avvis"):
            _sk(rt, tenant, aktor)
            rt.execute("SELECT m14_avgjor_faktura(%s,%s,%s,%s,%s)",
                       (tenant, fid,
                        "kontrollert" if handling == "kontroller" else "avvist",
                        f"Fasit: {handling}.", aktor))
            rt.commit()
        ider[merke] = fid
    if med_terskel:
        # TERSKELEN STRAMMES: lavere beløpsgrense, kortere kontrollfrist.
        _sk(rt, tenant, aktor)
        rt.execute("SELECT m14_sett_terskler(%s,%s,%s,%s,%s,%s)",
                   (tenant, TERSKLER_ETTER["slingring"],
                    TERSKLER_ETTER["grense"], TERSKLER_ETTER["frist"],
                    TERSKLER_ETTER["vindu"], aktor))
        rt.commit()
    return ider


def les_flaten(rt, tenant: str, aktor: str) -> dict:
    from api.faktura import svar_for
    _sk(rt, tenant, aktor)
    svar = svar_for(rt, tenant)
    rt.rollback()
    return svar


def _kontroller(rader, med_terskel: bool) -> dict[str, dict]:
    """Per merke: kontrollene døra kjørte ved registreringen — utfall
    slik regelen sier, regnet av settet (mva, dublett, leverandør) pluss
    den manuelle."""
    ut = {}
    satser = {k: (p, til) for k, p, _fra, til in SATSER}
    for i, (merke, ref, netto, mva, kode, utstedt, _s, handling, _f) in enumerate(rader):
        promille, til = satser[kode]
        if til is not None:
            mva_avvik = True          # satsen gjaldt ikke datoen
        else:
            mva_avvik = abs(mva - _forventet_mva(netto, promille)) > (
                TERSKLER_FOR["slingring"] if med_terskel else 1)
        dublett = med_terskel and any(
            r[1] == ref and r[2] + r[3] == netto + mva
            and abs(r[5] - utstedt) <= TERSKLER_FOR["vindu"]
            for r in rader[:i])
        ukjent = not (med_terskel and ref == KJENT)
        ut[merke] = {"mva": mva_avvik, "dublett": dublett,
                     "leverandor": ukjent, "manuell": handling == "manuell"}
    return ut


def maal(rt, tenant: str, ider: dict, rader: list, med_terskel: bool,
         aktor: str) -> dict:
    """-> {"funnavvik": [...], "koeavvik": [...]} — RETURNERT, aldri kastet."""
    svar = les_flaten(rt, tenant, aktor)
    per_id = {f["faktura_id"]: f for f in svar["fakturaer"]}
    funnavvik: list[str] = []
    koeavvik: list[str] = []
    s = svar["sammendrag"]
    if len(svar["fakturaer"]) != s["vist"] or len(per_id) < len(rader):
        funnavvik.append(f"flaten viste {len(per_id)} av {len(rader)}"
                         f" fakturaer (vist={s['vist']})")
    kontroller = _kontroller(rader, med_terskel)
    status_for = {None: "mottatt", "manuell": "mottatt",
                  "kontroller": "kontrollert", "avvis": "avvist"}
    for merke, ref, netto, mva, kode, _u, siden, handling, ventet in rader:
        rad = per_id.get(str(ider[merke]))
        if rad is None:
            funnavvik.append(f"{merke}: fakturaen står ikke i flaten")
            continue
        fikk = set(rad["apne_funn"])
        if fikk != ventet:
            funnavvik.append(f"{merke}: ventet {sorted(ventet)}, fikk"
                             f" {sorted(fikk)}")
        k = kontroller[merke]
        if rad["status"] != status_for[handling]:
            koeavvik.append(f"{merke}: status={rad['status']!r}, ventet"
                            f" {status_for[handling]!r}")
        if rad["kontroller"] != 3 + int(k["manuell"]):
            koeavvik.append(f"{merke}: kontroller={rad['kontroller']},"
                            f" ventet {3 + int(k['manuell'])}")
        ventet_avvik = int(k["mva"]) + int(k["dublett"]) + int(k["leverandor"])
        if rad["avvik"] != ventet_avvik:
            koeavvik.append(f"{merke}: avvik={rad['avvik']}, ventet"
                            f" {ventet_avvik}")
        if rad["dogn_siden_mottatt"] != siden or rad["brutto_ore"] != netto + mva:
            koeavvik.append(f"{merke}: døgn siden mottatt="
                            f"{rad['dogn_siden_mottatt']}, brutto="
                            f"{rad['brutto_ore']}")
    mottatt = [r for r in rader if r[7] in (None, "manuell")]
    ventet_s = {
        "mottatte": len(mottatt),
        "mottatt_ore": sum(r[2] + r[3] for r in mottatt),
        "kontrollerte": sum(1 for r in rader if r[7] == "kontroller"),
        "avviste": sum(1 for r in rader if r[7] == "avvis"),
        "apne_funn": sum(len(r[8]) for r in rader),
        "ukontrollerte": sum(1 for r in rader if "ukontrollert" in r[8]),
        "har_terskel": med_terskel,
        "terskelversjon": 2 if med_terskel else None,
        "satser": len(SATSER), "vist": len(rader)}
    for n, v in ventet_s.items():
        if s.get(n) != v:
            koeavvik.append(f"sammendrag.{n}={s.get(n)!r}, ventet {v!r}")
    # Treffraten per kontrolltype — telt over ALT, fra sin egen dør.
    ventet_t = {
        "mva": (len(rader), sum(k["mva"] for k in kontroller.values())),
        "dublett": (len(rader), sum(k["dublett"] for k in kontroller.values())),
        "leverandor": (len(rader), sum(k["leverandor"] for k in kontroller.values())),
        "manuell": (sum(k["manuell"] for k in kontroller.values()), 0),
        "belopsgrense": (0, 0)}
    fikk_t = {t["kontrolltype"]: (t["kjort"], t["avvik"]) for t in svar["treffrate"]}
    for typ, v in ventet_t.items():
        if fikk_t.get(typ) != v:
            koeavvik.append(f"treffrate.{typ}={fikk_t.get(typ)}, ventet {v}")
    t = svar["terskler"]
    if med_terskel and (t is None
                        or t["belopsgrense_ore"] != TERSKLER_ETTER["grense"]
                        or t["kontrollfrist_dogn"] != TERSKLER_ETTER["frist"]
                        or t["versjon"] != 2):
        koeavvik.append(f"terskler={t!r}, ventet den strammede (versjon 2)")
    if not med_terskel and t is not None:
        koeavvik.append("terskler satt i tenanten uten terskel")
    return {"funnavvik": funnavvik, "koeavvik": koeavvik}


def forventet_evidens(rader, med_terskel: bool) -> dict[str, int]:
    """Kjeden per tenant, utledet av radene: én per dør som ble kalt."""
    return {
        "terskler.satt": 2 if med_terskel else 0,
        "mvasats.satt": len(SATSER),
        "faktura.registrert": len(rader),
        "kontroll.registrert": sum(1 for r in rader if r[7] == "manuell"),
        "faktura.avgjort": sum(1 for r in rader if r[7] in ("kontroller", "avvis")),
    }


def maal_evidens(rt, tenant: str, ider: dict, rader, med_terskel: bool,
                 aktor: str) -> list[str]:
    _sk(rt, tenant, aktor)
    rader_db = rt.execute(
        "SELECT handling, aktor, input_hash FROM revisjonslogg"
        " WHERE tenant=%s AND kilde='m14_faktura'", (tenant,)).fetchall()
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
    # KONTROLLENE PER FAKTURA — det et tilsyn spør etter: hva ble sjekket,
    # med hvilket utfall, av hvem. Lest gjennom døra, som flaten gjør det.
    kontroller = _kontroller(rader, med_terskel)
    for merke, *_rest in rader:
        fid = ider[merke]
        _sk(rt, tenant, aktor)
        hist = rt.execute("SELECT * FROM m14_kontrollene(%s,%s)",
                          (tenant, fid)).fetchall()
        rt.rollback()
        k = kontroller[merke]
        ventet_typer = {"mva", "dublett", "leverandor"} | ({"manuell"} if k["manuell"] else set())
        # kontroll_id, kontrolltype, utfall, avvik_ore, notat, kjort, kjort_av
        if {h[1] for h in hist} != ventet_typer or len(hist) != len(ventet_typer):
            avvik.append(f"{merke}: kontrollene {sorted(h[1] for h in hist)},"
                         f" ventet {sorted(ventet_typer)}")
            continue
        for h in hist:
            # Den manuelle kontrollen ble registrert med «ok»; de tre
            # døra kjørte har utfallet regelen ga dem.
            ventet_utfall = "avvik" if (h[1] != "manuell" and k[h[1]]) else "ok"
            if h[2] != ventet_utfall or not h[5] or h[6] != aktor:
                avvik.append(f"{merke}: kontrollen {h[1]} utfall={h[2]!r}"
                             f" (ventet {ventet_utfall!r}), uten tid eller"
                             " med annen aktør")
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
    fakturaer = sum(len(d["rader"]) for d in kjoring.values())
    ventede_funn = sum(len(r[8]) for d in kjoring.values() for r in d["rader"])
    evidens = sum(sum(forventet_evidens(d["rader"], rolle == "med_terskel").values())
                  for rolle, d in kjoring.items())
    return {
        "krav_id": "m14-fasit-v1",
        "ts": ts,
        "bestatt": all(v == 0 for v in talt.values()),
        "oppsett": {
            "modul": "m14_fakturakontroll", "vert": vert,
            "sett_versjon": SETT_VERSJON, "sett_sha256": sett_sha256(),
            "bevisrot_sha256": bevisrot,
            "tenanter": sorted(d["tenant"] for d in kjoring.values()),
        },
        "maalt": {
            "fakturaer": fakturaer,
            "ventede_funn": ventede_funn,
            "evidenshendelser": evidens,
            **talt,
            "sveipetid_ms": sveipetid_ms,
            "sveip_tenanter": sveip_tenanter,
        },
        "avvik": {rolle: d["avvik"] for rolle, d in sorted(kjoring.items())},
    }
