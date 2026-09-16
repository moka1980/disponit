"""Det syntetiske mottakersettet for M-44s sertifisering.

ETT SETT, TO LEDD, samme form som m17/m23/m6: settet drives gjennom de
EKTE dørene både lokalt (CI, `test_m44_fasit_port.py`) og på staging
(`m44-fasit-artefakt.py`). Artefaktet bærer `sett_sha256` over bytene i
denne filen, og porten krever likhet med de innsjekkede.

MANIFESTETS KRAV, ORDRETT: «en mottakerliste med KJENT fasit for hvem
som har gyldig samtykke når, og hvem som er over frekvenstaket». Sveipen
(`m44_funnkandidater`) har fem funntyper: `samtykke_trukket`,
`samtykke_utlopt` (kanten `> samtykke_gyldig_dogn`),
`over_frekvensgrense` (kanten `> maks_per_periode` innenfor
`periode_dogn`), `uten_samtykke` og `ingen_grense`.

DØRENE ER KUNDENS — OG DE NEKTER DET SVEIPEN SKAL FINNE (142): en
mottaker uten gyldig samtykke, eller over taket, kommer ikke i planen.
Fasiten lager derfor funnene slik de oppstår i virkeligheten: samtykket
TREKKES etter plasseringen, og grensen STRAMMES etter plasseringen
(kortere gyldighet, lavere tak). `uten_samtykke` kan ikke lages gjennom
dørene (142 stenger den veien) og måles ikke her — det er dørens port.
`ingen_grense` måles i tenanten UTEN grense.

TO TENANTER: én med grense (strammet underveis) og én uten. Uten grense
er hver aktiv mottaker et `ingen_grense`-funn — sveipen sier fra om at
tenanten aldri satte reglene sine.

TRE FASITER, skrevet FØR kjøringen: funnene (kantene 365/366 døgn og
2/3 i perioden, trukket, avlyst kampanje, fremtidig kampanje, deaktivert
mottaker, gitt-så-bekreftet), køen slik flaten viser den
(`api.kampanje.svar_for`: siste samtykke, antall planer, funn per
mottaker), og EVIDENSKJEDEN: hver hendelse dørene skriver
(`kampanjemottaker_opprettet`, `samtykke_registrert`,
`kampanje_registrert`, `lagt_i_kampanjeplan`, `kampanjegrense_satt`,
`kampanje_avlyst`, `kampanjemottaker_aktiv_satt`, `avsender.satt`) med
identitet — pluss samtykkehistorikken per mottaker, som er det et
tilsyn spør etter: dato, kanal og hvem.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from pathlib import Path

SETT_VERSJON = "m44-fasit-1"
AKTOR = "m44-fasit"

#: Grensen settet plasserer UNDER (romslig), og grensen det STRAMMER TIL.
GRENSE_FOR = {"maks": 3, "periode": 7, "gyldig": 730}
GRENSE_ETTER = {"maks": 2, "periode": 7, "gyldig": 365}

#: Kampanjene, som dager relativt til i dag (sveipens `p_dag`).
KAMPANJER = {"k0": 0, "k1": -1, "k2": -2, "k_morgen": 1, "k_avlyst": 0}

#: (merke, samtykkehendelser [(tilstand, dager siden)], plasseringer,
#:  handling etter plassering, ventede funn)
#:
#: Rekkefølgen er dørens: samtykker registreres, mottakeren plasseres
#: (dørene sier ja under GRENSE_FOR), så skjer «handling etter» — og til
#: slutt strammes grensen til GRENSE_ETTER og sveipen går.
SETT_MED_GRENSE: list[tuple] = [
    ("gitt_nylig",              [("gitt", -30)],                    ["k0"],             None,      set()),
    ("trukket_etter_plassering", [("gitt", -60)],                   ["k0"],             "trekk",   {"samtykke_trukket"}),
    ("utlopt_400",              [("gitt", -400)],                   ["k0"],             None,      {"samtykke_utlopt"}),
    ("utlopt_kant_365",         [("gitt", -365)],                   ["k0"],             None,      set()),
    ("utlopt_kant_366",         [("gitt", -366)],                   ["k0"],             None,      {"samtykke_utlopt"}),
    ("over_frekvens_3",         [("gitt", -30)],                    ["k0", "k1", "k2"], None,      {"over_frekvensgrense"}),
    ("paa_frekvenskanten_2",    [("gitt", -30)],                    ["k0", "k1"],       None,      set()),
    ("deaktivert_etterpaa",     [("gitt", -30)],                    ["k0"],             "deaktiver", set()),
    ("gitt_saa_bekreftet",      [("gitt", -500), ("bekreftet", -20)], ["k0"],           None,      set()),
    # Samtykket er UTLØPT under den strammede grensen — men kampanjen er
    # avlyst, og en avlyst kampanje er ingen plan. Overses avlysningen,
    # blir dette et funn.
    ("i_avlyst_kampanje",       [("gitt", -400)],                   ["k_avlyst"],       None,      set()),
    ("i_fremtidig_kampanje",    [("gitt", -30)],                    ["k_morgen"],       None,      set()),
]
SETT_UTEN_GRENSE: list[tuple] = [
    ("aktiv_uten_grense",       [("gitt", -30)],                    ["k0"],             None,      {"ingen_grense"}),
    ("aktiv_uten_grense_2",     [("gitt", -10)],                    [],                 None,      {"ingen_grense"}),
    ("deaktivert_uten_grense",  [("gitt", -30)],                    [],                 "deaktiver", set()),
]


def sett_sha256() -> str:
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def bygg_sett() -> list[tuple[str, list]]:
    return [("med_grense", list(SETT_MED_GRENSE)),
            ("uten_grense", list(SETT_UTEN_GRENSE))]


def tenantnavn(runde_id: str, rolle: str) -> str:
    return f"t-m44fasit-{rolle}-{runde_id}"


def ny_runde() -> str:
    return secrets.token_hex(4)


def _sk(rt, tenant, aktor):
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, aktor, "m44-fasit")


def _legg_inn(rt, tenant, rader, med_grense: bool, aktor: str,
              runde_id: str) -> dict:
    """Settet inn gjennom kundens egne dører. -> {merke: mottaker_id}."""
    if med_grense:
        _sk(rt, tenant, aktor)
        rt.execute("SELECT m44_sett_grense(%s,%s,%s,%s,%s)",
                   (tenant, GRENSE_FOR["maks"], GRENSE_FOR["periode"],
                    GRENSE_FOR["gyldig"], aktor))
        rt.commit()
    _sk(rt, tenant, aktor)
    rt.execute("SELECT m44_sett_avsender(%s,%s,%s,%s)",
               (tenant, "Fasit AS", "post@fasit.example", aktor))
    rt.commit()
    kamp = {}
    for navn, dager in KAMPANJER.items():
        kid = uuid.uuid4()
        _sk(rt, tenant, aktor)
        rt.execute(
            "SELECT m44_registrer_kampanje(%s,%s,%s,%s,'salg',%s,"
            "       current_date + %s::int,%s)",
            (tenant, kid, f"K-{navn}-{runde_id}", f"Fasit {navn}",
             "https://fasit.example/avmeld", dager, aktor))
        rt.commit()
        kamp[navn] = kid
    ider = {}
    for i, (merke, samtykker, plasseringer, handling, _funn) in enumerate(rader):
        mid = uuid.uuid4()
        _sk(rt, tenant, aktor)
        kontakt = f"{merke}@fasit.example"
        rt.execute("SELECT m44_registrer_mottaker(%s,%s,%s,%s,%s,%s)",
                   (tenant, mid, f"M-{i:02d}-{runde_id}", f"Fasit {merke}",
                    kontakt, aktor))
        rt.commit()
        # KONTAKTEN KRYPTERT, som API-et gjør det: døra lagrer bare masken,
        # adressen pakkes under tenantens DEK i API-laget.
        from api.kampanje import _kontakt_kryptert
        _sk(rt, tenant, aktor)
        ct, nonce, key_id = _kontakt_kryptert(rt, tenant, kontakt)
        rt.execute("SELECT m44_sett_kontakt(%s,%s,%s,%s,%s,%s)",
                   (tenant, mid, ct, nonce, key_id, aktor))
        rt.commit()
        for tilstand, dager in samtykker:
            _sk(rt, tenant, aktor)
            rt.execute(
                "SELECT m44_registrer_samtykke(%s,%s,%s,%s,'preferanseside',"
                "       %s,'nyhetsbrev',current_date + %s::int,'fasit',%s)",
                (tenant, uuid.uuid4(), mid, tilstand,
                 f"s-{merke}-{tilstand}", dager, aktor))
            rt.commit()
        for navn in plasseringer:
            _sk(rt, tenant, aktor)
            rt.execute("SELECT m44_legg_i_plan(%s,%s,%s,%s)",
                       (tenant, kamp[navn], mid, aktor))
            rt.commit()
        if handling == "trekk":
            _sk(rt, tenant, aktor)
            rt.execute(
                "SELECT m44_registrer_samtykke(%s,%s,%s,'trukket',"
                "       'preferanseside',%s,'nyhetsbrev',current_date,"
                "       'fasit',%s)",
                (tenant, uuid.uuid4(), mid, f"s-{merke}-trukket", aktor))
            rt.commit()
        elif handling == "deaktiver":
            _sk(rt, tenant, aktor)
            rt.execute("SELECT m44_sett_mottakeraktiv(%s,%s,false,%s)",
                       (tenant, mid, aktor))
            rt.commit()
        ider[merke] = mid
    # Kampanjen som avlyses — ETTER plasseringen.
    _sk(rt, tenant, aktor)
    rt.execute("SELECT m44_avlys_kampanje(%s,%s,%s)",
               (tenant, kamp["k_avlyst"], aktor))
    rt.commit()
    if med_grense:
        # GRENSEN STRAMMES: kortere gyldighet, lavere tak. Det er slik
        # utløpte samtykker og over-frekvens oppstår i virkeligheten.
        _sk(rt, tenant, aktor)
        rt.execute("SELECT m44_sett_grense(%s,%s,%s,%s,%s)",
                   (tenant, GRENSE_ETTER["maks"], GRENSE_ETTER["periode"],
                    GRENSE_ETTER["gyldig"], aktor))
        rt.commit()
    return ider


def les_flaten(rt, tenant: str, aktor: str) -> dict:
    from api.kampanje import svar_for
    _sk(rt, tenant, aktor)
    svar = svar_for(rt, tenant)
    rt.rollback()
    return svar


def maal(rt, tenant: str, ider: dict, rader: list, aktor: str) -> dict:
    """-> {"funnavvik": [...], "koeavvik": [...]} — RETURNERT, aldri kastet."""
    svar = les_flaten(rt, tenant, aktor)
    per_id = {m["mottaker_id"]: m for m in svar["mottakere"]}
    funnavvik: list[str] = []
    koeavvik: list[str] = []
    if len(svar["mottakere"]) != svar["sammendrag"]["vist"] \
            or len(per_id) < len(rader):
        funnavvik.append(f"flaten viste {len(per_id)} av {len(rader)}"
                         f" mottakere (vist={svar['sammendrag']['vist']})")
    for merke, samtykker, plasseringer, handling, ventet in rader:
        rad = per_id.get(str(ider[merke]))
        if rad is None:
            funnavvik.append(f"{merke}: mottakeren står ikke i flaten")
            continue
        fikk = set(rad["apne_funn"])
        if fikk != ventet:
            funnavvik.append(f"{merke}: ventet {sorted(ventet)}, fikk"
                             f" {sorted(fikk)}")
        # Køen: aktiv-flagget, antall planer, siste samtykke.
        if rad["aktiv"] != (handling != "deaktiver"):
            koeavvik.append(f"{merke}: aktiv={rad['aktiv']}")
        if rad["i_planer"] != len(plasseringer):
            koeavvik.append(f"{merke}: i_planer={rad['i_planer']}, ventet"
                            f" {len(plasseringer)}")
        siste = (samtykker + ([("trukket", 0)] if handling == "trekk" else []))[-1]
        if rad["tilstand"] != siste[0]:
            koeavvik.append(f"{merke}: tilstand={rad['tilstand']!r}, ventet"
                            f" {siste[0]!r}")
        # Masken er ALT flaten ser: den må finnes og IKKE være klarteksten.
        kontakt = f"{merke}@fasit.example"
        if (not rad["har_kontakt"] or not rad["kontakt_maske"]
                or rad["kontakt_maske"] == kontakt):
            koeavvik.append(f"{merke}: kontakten mangler eller vises umaskert")
    apne = sum(1 for k in svar["kampanjer"] if k["status"] != "avlyst")
    if apne != len(KAMPANJER) - 1:
        koeavvik.append(f"{apne} åpne kampanjer, ventet {len(KAMPANJER) - 1}")
    return {"funnavvik": funnavvik, "koeavvik": koeavvik}


def forventet_evidens(rader, med_grense: bool) -> dict[str, int]:
    """Kjeden per tenant, utledet av radene: én per dør som ble kalt."""
    n = len(rader)
    return {
        "kampanjemottaker_opprettet": n,
        "kontakt_satt": n,
        "samtykke_registrert": sum(len(r[1]) for r in rader)
                               + sum(1 for r in rader if r[3] == "trekk"),
        "kampanje_registrert": len(KAMPANJER),
        "lagt_i_kampanjeplan": sum(len(r[2]) for r in rader),
        "kampanjemottaker_aktiv_satt": sum(1 for r in rader if r[3] == "deaktiver"),
        "kampanje_avlyst": 1,
        "kampanjegrense_satt": 2 if med_grense else 0,
        "avsender.satt": 1,
    }


def maal_evidens(rt, tenant: str, ider: dict, rader, med_grense: bool,
                 aktor: str) -> list[str]:
    _sk(rt, tenant, aktor)
    rader_db = rt.execute(
        "SELECT handling, aktor, input_hash FROM revisjonslogg"
        " WHERE tenant=%s AND kilde='m44_kampanje'", (tenant,)).fetchall()
    rt.rollback()
    ventet = {k: v for k, v in forventet_evidens(rader, med_grense).items() if v}
    talt: dict[str, int] = {}
    hasher: dict[str, set] = {}
    # Aktøren skal være DEN som gikk gjennom døra — ikke bare «noen».
    uten_aktor = 0
    for handling, akt, ih in rader_db:
        talt[handling] = talt.get(handling, 0) + 1
        hasher.setdefault(handling, set()).add(ih)
        if akt != aktor:
            uten_aktor += 1
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
    if uten_aktor:
        avvik.append(f"{uten_aktor} hendelser med annen eller ingen aktør")
    # SAMTYKKEHISTORIKKEN — det et tilsyn spør etter: dato, kanal, hvem.
    # Lest gjennom døra, som flaten gjør det (runtime har ingen SELECT på
    # tabellene).
    for merke, samtykker, _p, handling, _f in rader:
        mid = ider[merke]
        _sk(rt, tenant, aktor)
        hist = rt.execute("SELECT * FROM m44_samtykkehistorikken(%s,%s,%s)",
                          (tenant, mid, 50)).fetchall()
        rt.rollback()
        ventet_n = len(samtykker) + (1 if handling == "trekk" else 0)
        if len(hist) != ventet_n:
            avvik.append(f"{merke}: {len(hist)} samtykkehendelser i"
                         f" historikken, ventet {ventet_n}")
        for h in hist:
            if not h[2] or not h[5] or h[8] != aktor:
                avvik.append(f"{merke}: en samtykkehendelse uten kanal, dato"
                             " eller med annen aktør")
    return avvik


def kjor_sett(runde_id: str, rt, sveip, aktor: str = AKTOR) -> dict:
    ut = {}
    for rolle, rader in bygg_sett():
        tenant = tenantnavn(runde_id, rolle)
        ider = _legg_inn(rt, tenant, rader, rolle == "med_grense", aktor, runde_id)
        ut[rolle] = {"tenant": tenant, "ider": ider, "rader": rader}
    sveip()
    for rolle, d in ut.items():
        d["avvik"] = maal(rt, d["tenant"], d["ider"], d["rader"], aktor)
        d["avvik"]["evidensavvik"] = maal_evidens(
            rt, d["tenant"], d["ider"], d["rader"], rolle == "med_grense",
            aktor)
    return ut


AKSER = ("funnavvik", "koeavvik", "evidensavvik")


def artefakt(kjoring: dict, vert: str, ts: str, sveipetid_ms: int,
             sveip_tenanter: int, bevisrot: str) -> dict:
    talt = {akse: sum(len(d["avvik"][akse]) for d in kjoring.values())
            for akse in AKSER}
    mottakere = sum(len(d["rader"]) for d in kjoring.values())
    ventede_funn = sum(len(r[4]) for d in kjoring.values() for r in d["rader"])
    evidens = sum(sum(forventet_evidens(d["rader"], rolle == "med_grense").values())
                  for rolle, d in kjoring.items())
    return {
        "krav_id": "m44-fasit-v1",
        "ts": ts,
        "bestatt": all(v == 0 for v in talt.values()),
        "oppsett": {
            "modul": "m44_kampanje", "vert": vert,
            "sett_versjon": SETT_VERSJON, "sett_sha256": sett_sha256(),
            "bevisrot_sha256": bevisrot,
            "tenanter": sorted(d["tenant"] for d in kjoring.values()),
        },
        "maalt": {
            "mottakere": mottakere,
            "ventede_funn": ventede_funn,
            "evidenshendelser": evidens,
            **talt,
            "sveipetid_ms": sveipetid_ms,
            "sveip_tenanter": sveip_tenanter,
        },
        "avvik": {rolle: d["avvik"] for rolle, d in sorted(kjoring.items())},
    }
