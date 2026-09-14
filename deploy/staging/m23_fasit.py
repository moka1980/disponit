"""Det syntetiske fordringssettet for M-23s sertifisering.

ETT SETT, TO LEDD, samme form som `m02_fordeling.py`: settet under
drives gjennom de EKTE dørene både lokalt (CI,
`platform/core/tests/test_m23_fasit_port.py` — «likt lokalt»-leddet,
stående ved hver kjøring) og på staging (`m23-fasit-artefakt.py` —
artefaktets ledd). Fordi begge ledd bruker NØYAKTIG dette settet, kan de
ikke gli fra hverandre — og at det ER dette settet er MÅLT, ikke
oppgitt: artefaktet bærer `sett_sha256` over bytene i denne filen, og
porten krever likhet med de innsjekkede bytene.

MANIFESTETS KRAV, ORDRETT: «et fordringssett med KJENT fasit for
aldersbøttene og for hvilke fordringer som skal bli funn … Settet må
inneholde GRENSETILFELLENE på bøttekantene — en aldersfordeling som er
feil på kanten er feil overalt der det betyr noe.»

DERFOR TO FASITER I SAMME SETT, og begge er skrevet FØR kjøringen:

  * ALDERSBØTTENE (`m23_aldersfordeling`): kantene er `<= forfall`,
    `<= 30`, `<= 60`, `<= 90`, `ellers`. Settet har en fordring på
    NØYAKTIG hver kant og én på hver side: 0, 1, 30, 31, 60, 61, 90, 91.
  * FUNNENE (`m23_funnkandidater` gjennom sveipen): kantene er
    `>= dogn_etter_forfall`, `< p_dag` og `> 90`. Tre ULIKE operatorer,
    og det er nettopp slike blandinger som gir en feil på nøyaktig én
    dag.

EN MENGDE, IKKE ETT FUNN. Kandidatfunksjonen er tre UNION ALL-grener, og
en fordring kan oppfylle flere samtidig — `fordringsfunn` er da også
nøklet på (tenant, fordring_id, FUNNTYPE). `nittien_med_plan` bærer to
funn, og det er den raden som avslørte at et oppslag nøklet bare på
fordringen skrev det ene over det andre.

DØRENE ER KUNDENS, IKKE RIGGENS. Fordringene registreres med
`m23_registrer_fordring`, planen med `m23_sett_purreplan`, og trinnet
flyttes med `m23_neste_trinn` — ett om gangen, som et menneske gjør det.
Et direkte `UPDATE fordring SET trinn` ville vært en vei ingen kaller
har: vakten fryser raden, og runtime har ikke skriveretten. Funnene
leses gjennom `api.fordring.svar_for`, funksjonen `GET /v1/fordring`
selv kaller — altså det et menneske faktisk ser i flaten, ikke det indre
leddet inne i sveipen.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from pathlib import Path

SETT_VERSJON = "m23-fasit-1"

#: Purreplanen settet måles mot. Kantene er 3, 14 og 28 døgn.
PLAN = [
    {"navn": "Påminnelse", "dogn_etter_forfall": 3,
     "handling": "paaminnelse", "gebyr_ore": 0},
    {"navn": "Purring", "dogn_etter_forfall": 14,
     "handling": "purring", "gebyr_ore": 7000},
    {"navn": "Inkassovarsel", "dogn_etter_forfall": 28,
     "handling": "inkassovarsel", "gebyr_ore": 35000},
]

#: (merke, døgn siden forfall, trinn fordringen står på, ventede funn,
#:  ventet aldersbøtte)
#:
#: Hver linje er en PÅSTAND om kantene, ikke en avlesning. Beløpet er
#: ikke med her — det utledes av plasseringen (se `belop_ore`) slik at
#: hver bøttesum blir et fingeravtrykk: to fordringer som bytter bøtte
#: endrer BÅDE antallet og summen.
SETT_MED_PLAN: list[tuple[str, int, int, set, str]] = [
    # --- ikke forfalt: kanten er `current_date <= forfall` ---
    ("forfaller_i_morgen",  -1, 0, set(),                    "ikke_forfalt"),
    ("forfaller_i_dag",      0, 0, set(),                    "ikke_forfalt"),
    # --- forfalt, men ingen trinn modent ennå ---
    ("forfalt_en_dag",       1, 0, set(),                    "1_30"),
    ("dagen_for_trinn1",     2, 0, set(),                    "1_30"),
    # --- kanten på trinn 1 (3 døgn): `>=`, ikke `>` ---
    ("noyaktig_trinn1",      3, 0, {("trinn_forfalt", 1)},   "1_30"),
    ("dagen_etter_trinn1",   4, 0, {("trinn_forfalt", 1)},   "1_30"),
    # --- kanten på trinn 2 (14 døgn) ---
    ("dagen_for_trinn2",    13, 1, set(),                    "1_30"),
    ("noyaktig_trinn2",     14, 1, {("trinn_forfalt", 2)},   "1_30"),
    # --- kanten på trinn 3 (28 døgn) ---
    ("dagen_for_trinn3",    27, 2, set(),                    "1_30"),
    ("noyaktig_trinn3",     28, 2, {("trinn_forfalt", 3)},   "1_30"),
    # --- bøttekanten 30/31 ---
    ("botte_1_30_siste",    30, 3, set(),                    "1_30"),
    ("botte_31_60_forste",  31, 3, set(),                    "31_60"),
    # --- bøttekanten 60/61 ---
    ("botte_31_60_siste",   60, 3, set(),                    "31_60"),
    ("botte_61_90_forste",  61, 3, set(),                    "61_90"),
    # --- bøttekanten 90/91, som OGSÅ er funnkanten `> 90`.
    #     90 døgn på trinn 0: bare trinn-funnet. 91 døgn: begge.
    ("nitti_med_plan",      90, 0, {("trinn_forfalt", 3)},   "61_90"),
    ("nittien_med_plan",    91, 0, {("trinn_forfalt", 3),
                                    ("forfalt_uten_trinn", None)},
                                                             "over_90"),
    # --- TOPPTRINNET ER TERMINALT: en fordring på planens siste trinn
    #     eldes uten å bli et funn igjen. Begge grenene er stengt —
    #     `trinn_forfalt` krever et modnere trinn, `forfalt_uten_trinn`
    #     krever trinn 0. Uten denne raden ville et fjernet
    #     `AND f.trinn = 0` stå ufanget, og en kunde som har purret
    #     ferdig ville fått varselet om igjen hver natt.
    ("over_90_paa_topptrinn", 120, 3, set(),                 "over_90"),
]

#: Uten purreplan endrer ALT seg: `ingen_purreplan` er det eneste mulige
#: funnet, for BÅDE trinn-grenen og 90-døgnsgrenen krever purretrinn.
#: Kanten her er `forfall < p_dag` — en fordring som forfaller I DAG er
#: ikke forfalt.
SETT_UTEN_PLAN: list[tuple[str, int, int, set, str]] = [
    ("forfaller_i_dag",   0, 0, set(),                        "ikke_forfalt"),
    ("forfalt_en_dag",    1, 0, {("ingen_purreplan", None)},  "1_30"),
    ("forfalt_nitti",    90, 0, {("ingen_purreplan", None)},  "61_90"),
    ("forfalt_nittien",  91, 0, {("ingen_purreplan", None)},  "over_90"),
]

#: Alle bøttene står i svaret, også de tomme (`m23_aldersfordeling`).
BOTTER = ("ikke_forfalt", "1_30", "31_60", "61_90", "over_90")


def sett_sha256() -> str:
    """Settets identitet — BYTENE i denne filen.

    `SETT_VERSJON` er en streng noen skriver for hånd, og den sier bare at
    to kjøringer MENTE å drive samme sett. Et staging-ledd på en eldre
    utrulling kunne truffet de samme summene med helt andre fordringer og
    likevel valideres som «likt lokalt» — og det er nettopp den likheten
    punktet handler om. Derfor bæres bytene, og porten krever likhet med
    de innsjekkede (samme form som `m02_fordeling.sett_sha256`).
    """
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def belop_ore(i: int) -> int:
    """Et UNIKT beløp per fordring, deterministisk av plasseringen.

    Like beløp ville gjort bøttesummen til antallet ganget med en
    konstant — da måler `ore` ingenting `antall` ikke alt måler. Med
    unike beløp blir hver bøttesum et fingeravtrykk: to fordringer som
    bytter bøtte endrer summen selv om antallet står.
    """
    return 100_000 + i * 1_000


def forventet_fordeling(sett) -> dict[str, tuple[int, int]]:
    """-> {bøtte: (antall, øre)}, regnet av den HÅNDSKREVNE bøttekolonnen.

    Summen utledes, men PLASSERINGEN gjør den ikke: hvilken bøtte hver
    fordring hører til står skrevet i settet over, og det er den
    påstanden `m23_aldersfordeling` måles mot.
    """
    ut = {b: [0, 0] for b in BOTTER}
    for i, (_merke, _dogn, _trinn, _funn, botte) in enumerate(sett):
        ut[botte][0] += 1
        ut[botte][1] += belop_ore(i)
    return {b: (a, o) for b, (a, o) in ut.items()}


def bygg_sett() -> list[tuple[str, list]]:
    """-> [(rolle, rader)] — deterministisk, to tenanter."""
    return [("med_plan", list(SETT_MED_PLAN)),
            ("uten_plan", list(SETT_UTEN_PLAN))]


def tenantnavn(runde_id: str, rolle: str) -> str:
    return f"t-m23fasit-{rolle}-{runde_id}"


def _legg_inn(rt, tenant, rader, med_plan: bool, aktor: str,
              runde_id: str):
    """Settet inn gjennom kundens egne dører. -> {merke: fordring_id}."""
    from db.pg import sett_kontekst
    if med_plan:
        sett_kontekst(rt, tenant, aktor, "m23-fasit")
        rt.execute("SELECT m23_sett_purreplan(%s,%s::jsonb,%s)",
                   (tenant, json.dumps(PLAN), aktor))
        rt.commit()
    ider = {}
    for i, (merke, dogn, trinn, _funn, _botte) in enumerate(rader):
        fid = uuid.uuid4()
        sett_kontekst(rt, tenant, aktor, "m23-fasit")
        rt.execute(
            "SELECT m23_registrer_fordring(%s,%s,%s,%s,%s,"
            "       current_date - %s::int - 5, current_date - %s::int,%s)",
            (tenant, fid, f"Fasit {merke}", f"F-{i:02d}-{runde_id}",
             belop_ore(i), dogn, dogn, aktor))
        rt.commit()
        for _ in range(trinn):
            sett_kontekst(rt, tenant, aktor, "m23-fasit")
            rt.execute("SELECT m23_neste_trinn(%s,%s,%s,%s,%s)",
                       (tenant, uuid.uuid4(), fid, "fasitsett", aktor))
            rt.commit()
        ider[merke] = fid
    return ider


def ny_runde() -> str:
    """Et kort, unikt rundemerke. Fakturanummeret er unikt per tenant, og
    to bevisrunder mot samme base skal ikke kollidere — derfor bærer både
    tenantnavnet og fakturanummeret runden."""
    return secrets.token_hex(4)


def les_flaten(rt, tenant: str, aktor: str) -> dict:
    """Flatens eget svar — `api.fordring.svar_for`, det `GET /v1/fordring`
    kaller. Fasiten måles på det MENNESKET ser, ikke på kandidatleddet
    inne i sveipen."""
    from api.fordring import svar_for
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, aktor, "m23-fasit")
    svar = svar_for(rt, tenant)
    rt.rollback()
    return svar


def maal(rt, tenant: str, ider: dict, rader: list, aktor: str) -> dict:
    """Måler settet mot fasiten. -> {"funnavvik": [...], "botteavvik": [...]}

    FAIL-OPEN ER FORBUDT HER: avvikene RETURNERES, de kastes ikke. Et
    artefakt som ikke ble skrevet fordi målingen sprakk, er et punkt
    ingen kan etterprøve — og porten i KRAVGRENSER feller et rødt
    artefakt like sikkert som en exception ville gjort.
    """
    svar = les_flaten(rt, tenant, aktor)
    per_id = {r["fordring_id"]: r for r in svar["fordringer"]}

    funnavvik = []
    # FLATEN AVKORTER LISTEN sin ved `MAKS_FORDRINGER` (200), og
    # sammendraget sier selv hvor mange den viste. Vokser settet forbi
    # taket en dag, skal fasiten si «listen var avkortet» — ikke telle
    # null funn på radene som falt utenfor og kalle det en regresjon.
    if len(svar["fordringer"]) != svar["sammendrag"]["vist"]:
        funnavvik.append("flaten sier den viste"
                         f" {svar['sammendrag']['vist']} rader, men ga"
                         f" {len(svar['fordringer'])}")
    if len(per_id) < len(rader):
        funnavvik.append(
            f"flaten viste {len(per_id)} fordringer for et sett på"
            f" {len(rader)} — listen er avkortet, og en fasit målt på et"
            " utvalg er ikke fasiten")
    for merke, dogn, trinn, ventet, _botte in rader:
        rad = per_id.get(str(ider[merke]))
        if rad is None:
            funnavvik.append(f"{merke}: fordringen står ikke i flaten")
            continue
        # Flaten gir funntypene; `moden_for_trinn` står på raden og er
        # den samme verdien kandidatfunksjonen regnet.
        fikk = set(rad["apne_funn"])
        ventede_typer = {t for t, _m in ventet}
        if fikk != ventede_typer:
            funnavvik.append(
                f"{merke} ({dogn} døgn, trinn {trinn}): ventet"
                f" {sorted(ventede_typer)}, fikk {sorted(fikk)}")
            continue
        for typ, moden in ventet:
            if typ == "trinn_forfalt" and rad["moden_for_trinn"] != moden:
                funnavvik.append(
                    f"{merke}: moden_for_trinn {rad['moden_for_trinn']},"
                    f" ventet {moden}")

    ventet_botter = forventet_fordeling(rader)
    fikk_botter = {r["botte"]: (r["antall"], r["ore"])
                   for r in svar["aldersfordeling"]}
    botteavvik = []
    if set(fikk_botter) != set(BOTTER):
        botteavvik.append(f"bøttene var {sorted(fikk_botter)}, ventet"
                          f" {sorted(BOTTER)}")
    for b in BOTTER:
        if fikk_botter.get(b) != ventet_botter[b]:
            botteavvik.append(f"{b}: ventet {ventet_botter[b]}, fikk"
                              f" {fikk_botter.get(b)}")
    return {"funnavvik": funnavvik, "botteavvik": botteavvik}


def forventet_evidens(rader, med_plan: bool) -> dict[str, int]:
    """Fasiten for EVIDENSKJEDEN, utledet av de samme håndskrevne radene.

    Manifestets punkt `revisjonslogg_korrekt` krever «at hver fordring og
    hver trinnendring faktisk står i evidenskjeden, målt som hendelser
    med identitet». Her står tallene: én `fordring.registrert` per rad,
    én `fordring.trinn` per trinnflytting (og trinnet flyttes ETT om
    gangen, så en rad på trinn 3 er tre hendelser), og én
    `purreplan.satt` for tenanten som har en plan.
    """
    return {
        "purreplan.satt": 1 if med_plan else 0,
        "fordring.registrert": len(rader),
        "fordring.trinn": sum(t for _m, _d, t, _f, _b in rader),
    }


def maal_evidens(rt, tenant: str, rader, med_plan: bool,
                 aktor: str) -> list[str]:
    """Måler evidenskjeden mot fasiten. -> liste med avvik.

    TRE PÅSTANDER, og den tredje er den manifestet kaller «med
    identitet»: hver hendelse må ha SIN EGEN `input_hash`. En kjede som
    skrev samme hash for alle 21 registreringene ville hatt riktig antall
    rader og null sporbarhet — man kunne ikke pekt på hvilken fordring en
    rad gjaldt.
    """
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, aktor, "m23-fasit")
    rader_db = rt.execute(
        "SELECT handling, aktor, input_hash FROM revisjonslogg"
        " WHERE tenant=%s AND kilde='m23_fordring'", (tenant,)).fetchall()
    rt.rollback()

    ventet = forventet_evidens(rader, med_plan)
    talt: dict[str, int] = {}
    uten_aktor = 0
    hasher: dict[str, set] = {}
    for handling, akt, ih in rader_db:
        talt[handling] = talt.get(handling, 0) + 1
        if not (akt or "").strip():
            uten_aktor += 1
        hasher.setdefault(handling, set()).add(ih)

    avvik = []
    for handling, antall in ventet.items():
        if talt.get(handling, 0) != antall:
            avvik.append(f"{handling}: ventet {antall} hendelser, fikk"
                         f" {talt.get(handling, 0)}")
    ukjent = set(talt) - set(ventet)
    if ukjent:
        avvik.append(f"kjeden har handlinger fasiten ikke venter:"
                     f" {sorted(ukjent)}")
    if uten_aktor:
        avvik.append(f"{uten_aktor} hendelser uten aktør — en evidensrad"
                     " uten hvem er ikke evidens")
    for handling, antall in ventet.items():
        if antall and len(hasher.get(handling, ())) != antall:
            avvik.append(
                f"{handling}: {len(hasher.get(handling, ()))} ulike"
                f" input_hash for {antall} hendelser — kjeden har mistet"
                " identiteten, og en rad kan ikke lenger knyttes til sin"
                " fordring")
    return avvik


def kjor_sett(runde_id: str, rt, sveip, aktor: str = "m23-fasit") -> dict:
    """Driver HELE settet: legger inn, sveiper, og måler flaten.

    Avhengighetene er injisert så CI-leddet og staging-leddet bruker hver
    sin tilkobling og hver sin sveipevei, men SAMME sett og SAMME dom:
      rt        — runtime-tilkobling (rollen API-et bruker)
      sveip()   — kjører fordringssveipen; skal kaste ved feil
    """
    ut = {}
    for rolle, rader in bygg_sett():
        tenant = tenantnavn(runde_id, rolle)
        ider = _legg_inn(rt, tenant, rader, rolle == "med_plan", aktor,
                         runde_id)
        ut[rolle] = {"tenant": tenant, "ider": ider, "rader": rader}
    sveip()
    for rolle, d in ut.items():
        d["avvik"] = maal(rt, d["tenant"], d["ider"], d["rader"], aktor)
        d["avvik"]["evidensavvik"] = maal_evidens(
            rt, d["tenant"], d["rader"], rolle == "med_plan", aktor)
    return ut


def artefakt(kjoring: dict, vert: str, ts: str, sveipetid_ms: int,
             sveip_tenanter: int, bevisrot: str) -> dict:
    """Artefaktet, med dommen REGNET AV AVVIKENE — aldri av driveren.

    Er det avvik, skrives artefaktet likevel med `bestatt: false`. Porten
    i KRAVGRENSER feller det, og et rødt artefakt som finnes er ærligere
    enn et grønt som ble valgt.
    """
    funnavvik = sum(len(d["avvik"]["funnavvik"]) for d in kjoring.values())
    botteavvik = sum(len(d["avvik"]["botteavvik"]) for d in kjoring.values())
    evidensavvik = sum(len(d["avvik"]["evidensavvik"])
                       for d in kjoring.values())
    fordringer = sum(len(d["rader"]) for d in kjoring.values())
    ventede_funn = sum(len(f) for d in kjoring.values()
                       for (_m, _d, _t, f, _b) in d["rader"])
    evidenshendelser = sum(
        sum(forventet_evidens(d["rader"], rolle == "med_plan").values())
        for rolle, d in kjoring.items())
    return {
        "krav_id": "m23-fasit-v1",
        "ts": ts,
        "bestatt": funnavvik == 0 and botteavvik == 0 \
            and evidensavvik == 0,
        "oppsett": {
            "modul": "m23_fordring", "vert": vert,
            "sett_versjon": SETT_VERSJON,
            "sett_sha256": sett_sha256(),
            "bevisrot_sha256": bevisrot,
            "tenanter": sorted(d["tenant"] for d in kjoring.values()),
        },
        "maalt": {
            "fordringer": fordringer,
            "ventede_funn": ventede_funn,
            "funnavvik": funnavvik,
            "botteavvik": botteavvik,
            "evidenshendelser": evidenshendelser,
            "evidensavvik": evidensavvik,
            "sveipetid_ms": sveipetid_ms,
            # TENANTANTALLET SVEIPEN SELV RAPPORTERTE. Uten det er
            # `sveipetid_ms` et tall uten nevner: en sveip som gikk
            # hjem igjen uten å røre en eneste tenant er RASK, og ville
            # bestått ytelsestaket mens den ikke gjorde jobben.
            "sveip_tenanter": sveip_tenanter,
        },
        "avvik": {rolle: d["avvik"] for rolle, d in sorted(kjoring.items())},
    }
