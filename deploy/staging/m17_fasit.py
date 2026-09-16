"""Det syntetiske henvendelsessettet for M-17s sertifisering.

ETT SETT, TO LEDD, samme form som `m23_fasit.py`: settet under drives
gjennom de EKTE dørene både lokalt (CI,
`platform/core/tests/test_m17_fasit_port.py` — «likt lokalt»-leddet,
stående ved hver kjøring) og på staging (`m17-fasit-artefakt.py` —
artefaktets ledd). Begge ledd bruker NØYAKTIG dette settet, og at det ER
dette settet er MÅLT: artefaktet bærer `sett_sha256` over bytene i denne
filen, og porten krever likhet med de innsjekkede bytene.

MANIFESTETS KRAV, ORDRETT: «et henvendelsessett med KJENT fasit for
klassifiseringen, kjørt likt lokalt og på staging.» Klassifiseringen er
i dag en REGEL (204/205: stille avsendere), ikke en modell — «regel
først, modell senere» (eier 15/9). Settet er derfor golden-settet for
regelen, og den dagen en modell kommer, måles den mot de samme radene.

TRE FASITER I SAMME SETT, og alle er skrevet FØR kjøringen:

  * FUNNENE (`m17_funnkandidater` gjennom sveipen): kantene er
    `> 2 døgn` uten klassifisering, `> 5 døgn` med `svar_kreves` uten
    svar, og `mistenkelig` STRAKS. Settet har en henvendelse på hver
    kant og én på hver side: 2/3 døgn, 5/6 døgn, mistenkelig på dag 0.
    Og de to unntakene: en henvendelse i UNNTAKSKØEN er tildelt, ikke
    oversett, og blir aldri et funn — og en lukket står ikke i køen.
  * KLASSIFISERINGEN (regelen): domenet, UNDERDOMENET (205), et
    LIGNENDE domene som ikke skal treffe («notstille.example» er ikke
    «stille.example»), adressen (som hash) og en ANNEN adresse på samme
    domene som ikke skal treffe — og at et menneskes dom står, selv om
    regelen ville sagt noe annet.
  * EVIDENSKJEDEN: hver mottatt henvendelse, hver adresse, hver dom
    (menneske OG regel), hver sak til køen, hver lukking og hvert utkast
    står som hendelser MED identitet.

TO TENANTER: én MED stilleregler og én UTEN. Uten reglene er den samme
avsenderen en helt vanlig uklassifisert henvendelse som eldes til et
funn — og det er nettopp den forskjellen regelen finnes for.

DØRENE ER KUNDENS, IKKE RIGGENS. Henvendelsene tas imot med `m17_ta_imot`
(samme dør som innboksbroen 203), dommene med `m17_klassifiser`, køen
med `m17_til_unntakskoe`, lukkingen med `m17_lukk`, utkastet med
`m17_lagre_utkast`/`m17_avgjor_utkast`, reglene med
`m17_sett_stilleregler`. Regelrunden går som PLANARBEIDEREN
(`plan/stilleregler.kjor_en_runde`), sveipen som SVEIPEROLLEN
(`drift/henvendelsessveip.kjor`), og alt leses gjennom
`api.kundeservice.svar_for` — det et menneske faktisk ser i køen.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from pathlib import Path

SETT_VERSJON = "m17-fasit-1"

#: Sveipens kanter, som `drift/henvendelsessveip.py` bærer dem. Settet
#: er skrevet mot DISSE tallene; endres de, er settet et annet.
DOGN_UKLASSIFISERT = 2
DOGN_UBESVART = 5

KUNDE = "kunde@kunde.example"
STILLE = "noreply@stille.example"
STILLE_UNDER = "varsel@post.stille.example"
LIGNENDE = "noreply@notstille.example"
NYHETSBREV = "nyhet@brev.example"
NYHETSBREV_ANNEN = "annen@brev.example"
PHISH = "sikkerhet@ukjent.example"

#: Reglene tenanten MED regler setter. Adressen hashes ved innsetting,
#: som API-et gjør det (`_avsenderhash`), aldri her i klartekst-form.
REGLER = [
    {"art": "domene", "monster": "stille.example",
     "handlingstype": "til_info", "prioritet": "lav", "tema": "annet"},
    {"art": "adresse", "monster": NYHETSBREV,
     "handlingstype": "nyhetsbrev", "prioritet": "lav", "tema": "annet"},
]

#: En lukket henvendelse står ikke i køen. Sentinel i funn-kolonnen.
LUKKET = "LUKKET"

#: (merke, døgn siden mottatt, avsender, menneskets dom eller None,
#:  handling eller None, ventede funn, ventet klassifisering i køen)
#:
#: Dommen er (prioritet, tema, handlingstype). Handlingen er én av
#: `unntakskoe`, `lukk`, `utkast`. Den ventede klassifiseringen er
#: (prioritet, tema, handlingstype, kilde) eller None for «ikke
#: klassifisert». Hver linje er en PÅSTAND om kantene, ikke en avlesning.
SETT_MED_REGLER: list[tuple] = [
    # --- uklassifisert: kanten er `> 2 døgn` ---
    ("fersk_uklassifisert",           0, KUNDE, None, None, set(), None),
    ("dagen_for_uklassifisert_kant",  2, KUNDE, None, None, set(), None),
    ("noyaktig_uklassifisert_kant",   3, KUNDE, None, None,
     {"uklassifisert_over_grense"}, None),
    ("gammel_uklassifisert",         10, KUNDE, None, None,
     {"uklassifisert_over_grense"}, None),
    # --- svar kreves: kanten er `> 5 døgn` ---
    ("svar_kreves_dagen_for",         5, KUNDE, ("normal", "faktura", "svar_kreves"),
     None, set(), ("normal", "faktura", "svar_kreves", "menneske")),
    ("svar_kreves_noyaktig_kant",     6, KUNDE, ("normal", "faktura", "svar_kreves"),
     None, {"ubesvart_over_grense"},
     ("normal", "faktura", "svar_kreves", "menneske")),
    # I UNNTAKSKØEN: tildelt, ikke oversett — aldri et funn.
    ("svar_kreves_i_unntakskoe",      6, KUNDE, ("normal", "klage", "svar_kreves"),
     "unntakskoe", set(), ("normal", "klage", "svar_kreves", "menneske")),
    # LUKKET: står ikke i køen i det hele tatt.
    ("svar_kreves_lukket",            6, KUNDE, ("normal", "faktura", "svar_kreves"),
     "lukk", LUKKET, None),
    # TIL INFO eldes uten å bli et funn: ingen ba om svar.
    ("til_info_gammel",              10, KUNDE, ("lav", "annet", "til_info"),
     None, set(), ("lav", "annet", "til_info", "menneske")),
    # --- mistenkelig: STRAKS, ingen aldersgrense ---
    ("mistenkelig_fersk",             0, PHISH, ("hoy", "annet", "mistenkelig"),
     None, {"mistenkelig_uten_behandling"},
     ("hoy", "annet", "mistenkelig", "menneske")),
    ("mistenkelig_i_unntakskoe",      0, PHISH, ("hoy", "annet", "mistenkelig"),
     "unntakskoe", set(), ("hoy", "annet", "mistenkelig", "menneske")),
    # --- regelen: domene, underdomene, lignende domene, adresse ---
    ("stille_domene",                10, STILLE, None, None, set(),
     ("lav", "annet", "til_info", "regel")),
    ("stille_underdomene",           10, STILLE_UNDER, None, None, set(),
     ("lav", "annet", "til_info", "regel")),
    ("lignende_domene_treffer_ikke", 10, LIGNENDE, None, None,
     {"uklassifisert_over_grense"}, None),
    ("stille_adresse",               10, NYHETSBREV, None, None, set(),
     ("lav", "annet", "nyhetsbrev", "regel")),
    ("annen_adresse_samme_domene",   10, NYHETSBREV_ANNEN, None, None,
     {"uklassifisert_over_grense"}, None),
    # ET MENNESKES DOM STÅR: regelen ville sagt til_info, mennesket sa
    # svar kreves — og da er den ubesvart etter fem døgn.
    ("stille_domene_menneske_forst", 10, STILLE, ("hoy", "klage", "svar_kreves"),
     None, {"ubesvart_over_grense"},
     ("hoy", "klage", "svar_kreves", "menneske")),
    # --- utkastet: lagret og forkastet, antallet står på raden ---
    ("med_utkast",                    1, KUNDE, ("normal", "teknisk", "svar_kreves"),
     "utkast", set(), ("normal", "teknisk", "svar_kreves", "menneske")),
]

#: UTEN regler: den stille avsenderen er en vanlig henvendelse som eldes
#: til et funn. Kantene måles én gang til, i en tenant regelen aldri rører.
SETT_UTEN_REGLER: list[tuple] = [
    ("stille_domene_uten_regel",     10, STILLE, None, None,
     {"uklassifisert_over_grense"}, None),
    ("fersk_uklassifisert",           0, KUNDE, None, None, set(), None),
    ("svar_kreves_noyaktig_kant",     6, KUNDE, ("normal", "faktura", "svar_kreves"),
     None, {"ubesvart_over_grense"},
     ("normal", "faktura", "svar_kreves", "menneske")),
]


def sett_sha256() -> str:
    """Settets identitet — BYTENE i denne filen (samme form som m23)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def bygg_sett() -> list[tuple[str, list]]:
    """-> [(rolle, rader)] — deterministisk, to tenanter."""
    return [("med_regler", list(SETT_MED_REGLER)),
            ("uten_regler", list(SETT_UTEN_REGLER))]


def tenantnavn(runde_id: str, rolle: str) -> str:
    return f"t-m17fasit-{rolle}-{runde_id}"


def ny_runde() -> str:
    """Et kort, unikt rundemerke: `ekstern_ref` er unik per tenant og
    kanal, og to bevisrunder mot samme base skal aldri kollidere."""
    return secrets.token_hex(4)


def kroppstekst(merke: str) -> str:
    return f"Fasit {merke}: kundens egen tekst."


def _dek(rt, tenant, aktor):
    from db import kryptering
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, aktor, "m17-fasit")
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(rt, tenant)
    rt.commit()
    return key_id, dek


def _legg_inn(rt, tenant, rader, med_regler: bool, aktor: str,
              runde_id: str) -> dict:
    """Settet inn gjennom kundens egne dører. -> {merke: henvendelse_id}."""
    from api.kundeservice import (_AAD_AVSENDER, _AAD_KOE, _avsenderhash,
                                  _avsendermaske, _krypter)
    from db import kryptering
    from db.pg import sett_kontekst
    key_id, dek = _dek(rt, tenant, aktor)
    if med_regler:
        regler = [dict(r, monster=_avsenderhash(r["monster"]))
                  if r["art"] == "adresse" else dict(r) for r in REGLER]
        sett_kontekst(rt, tenant, aktor, "m17-fasit")
        rt.execute("SELECT m17_sett_stilleregler(%s,%s::jsonb,%s)",
                   (tenant, json.dumps(regler), aktor))
        rt.commit()
    ider = {}
    for i, (merke, dogn, avsender, dom, handling, _funn, _kl) in \
            enumerate(rader):
        hid = uuid.uuid4()
        e_ct, e_n = _krypter(dek, key_id, tenant, f"Fasit {merke}")
        k_ct, k_n = _krypter(dek, key_id, tenant, kroppstekst(merke))
        a_ct, a_n = _krypter(dek, key_id, tenant, avsender,
                             aad=_AAD_AVSENDER)
        sett_kontekst(rt, tenant, aktor, "m17-fasit")
        # SAMME DØR SOM INNBOKSBROEN (203): 15-arg-formen med adressen
        # kryptert og masken i klartekst. Mottatt-tidspunktet settes av
        # basens egen klokke, for det er den sveipen regner mot.
        rt.execute(
            "SELECT * FROM m17_ta_imot(%s,%s,'epost',%s,"
            "       now() - make_interval(days => %s),"
            "       %s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tenant, hid, f"F-{i:02d}-{runde_id}", dogn,
             _avsenderhash(avsender), e_ct, e_n, k_ct, k_n, key_id,
             aktor, _avsendermaske(avsender), a_ct, a_n))
        rt.commit()
        if dom is not None:
            sett_kontekst(rt, tenant, aktor, "m17-fasit")
            rt.execute(
                "SELECT m17_klassifiser(%s,%s,%s,%s,%s,'menneske',NULL,%s)",
                (tenant, hid, dom[0], dom[1], dom[2], aktor))
            rt.commit()
        if handling == "unntakskoe":
            ct, nonce = kryptering.krypter(
                dek, {"modul": "m17_kundeservice",
                      "henvendelse_id": str(hid),
                      "begrunnelse": "fasit: uavklart"},
                tenant, key_id, ekstra_aad=_AAD_KOE)
            sett_kontekst(rt, tenant, aktor, "m17-fasit")
            rt.execute("SELECT m17_til_unntakskoe(%s,%s,%s,%s,%s,%s,%s)",
                       (tenant, hid, "fasit: uavklart", ct, nonce, key_id,
                        aktor))
            rt.commit()
        elif handling == "lukk":
            sett_kontekst(rt, tenant, aktor, "m17-fasit")
            rt.execute("SELECT m17_lukk(%s,%s,'ikke_aktuell',%s)",
                       (tenant, hid, aktor))
            rt.commit()
        elif handling == "utkast":
            uid = uuid.uuid4()
            u_ct, u_n = _krypter(dek, key_id, tenant, "Fasit: et svarutkast.")
            sett_kontekst(rt, tenant, aktor, "m17-fasit")
            rt.execute(
                "SELECT m17_lagre_utkast(%s,%s,%s,%s,%s,%s,%s::text[],"
                "                        'menneske',NULL,%s)",
                (tenant, uid, hid, u_ct, u_n, key_id, [], aktor))
            rt.execute("SELECT m17_avgjor_utkast(%s,%s,'forkastet',%s)",
                       (tenant, uid, aktor))
            rt.commit()
        ider[merke] = hid
    return ider


def les_flaten(rt, tenant: str, aktor: str) -> dict:
    """Køens eget svar — `api.kundeservice.svar_for`, det `GET
    /v1/kundeservice` kaller. Fasiten måles på det MENNESKET ser."""
    from api.kundeservice import svar_for
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, aktor, "m17-fasit")
    svar = svar_for(rt, tenant)
    rt.rollback()
    return svar


def _les_kropp(rt, tenant: str, hid, aktor: str) -> str | None:
    """Kundens tekst slik innholdsdøren gir den — intakt eller ikke."""
    from db import kryptering
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, aktor, "m17-fasit")
    rad = rt.execute("SELECT * FROM m17_hent_innhold(%s,%s)",
                     (tenant, hid)).fetchone()
    rt.rollback()
    if rad is None:
        return None
    dek = kryptering.hent_dek(rt, tenant, rad[4])
    rt.rollback()
    return kryptering.dekrypter(dek, bytes(rad[2]), bytes(rad[3]), tenant,
                                rad[4])["t"]


def maal(rt, tenant: str, ider: dict, rader: list, aktor: str) -> dict:
    """Måler settet mot fasiten. -> {funnavvik, klassifiseringsavvik,
    koeavvik} — RETURNERT, aldri kastet (fail-open er forbudt: et
    artefakt som ikke ble skrevet er et punkt ingen kan etterprøve)."""
    svar = les_flaten(rt, tenant, aktor)
    per_id = {r["henvendelse_id"]: r for r in svar["koe"]}
    funnavvik: list[str] = []
    klassifiseringsavvik: list[str] = []
    koeavvik: list[str] = []
    if len(svar["koe"]) != svar["sammendrag"]["vist"]:
        funnavvik.append(f"køen sier den viste {svar['sammendrag']['vist']}"
                         f" rader, men ga {len(svar['koe'])}")
    apne = [r for r in rader if r[5] != LUKKET]
    if len(per_id) < len(apne):
        funnavvik.append(f"køen viste {len(per_id)} henvendelser for et"
                         f" sett med {len(apne)} åpne — listen er avkortet")
    for merke, dogn, _avs, _dom, handling, ventet, ventet_kl in rader:
        rad = per_id.get(str(ider[merke]))
        if ventet == LUKKET:
            if rad is not None:
                funnavvik.append(f"{merke}: lukket, men står i køen")
            continue
        if rad is None:
            funnavvik.append(f"{merke}: henvendelsen står ikke i køen")
            continue
        fikk = set(rad["apne_funn"])
        if fikk != ventet:
            funnavvik.append(f"{merke} ({dogn} døgn): ventet"
                             f" {sorted(ventet)}, fikk {sorted(fikk)}")
        fikk_kl = ((rad["prioritet"], rad["tema"], rad["handlingstype"],
                    rad["klassifisert_av"]) if rad["prioritet"] else None)
        if fikk_kl != ventet_kl:
            klassifiseringsavvik.append(
                f"{merke}: ventet {ventet_kl}, fikk {fikk_kl}")
        if handling == "unntakskoe":
            # INTAKT I KØEN: raden er tildelt, og teksten er den samme.
            if not rad["i_unntakskoe"]:
                koeavvik.append(f"{merke}: skulle stå i unntakskøen")
            if _les_kropp(rt, tenant, ider[merke], aktor) \
                    != kroppstekst(merke):
                koeavvik.append(f"{merke}: teksten er ikke intakt etter"
                                " sak til unntakskøen")
        elif rad["i_unntakskoe"]:
            koeavvik.append(f"{merke}: står i unntakskøen uten å skulle")
        if handling == "utkast" and rad["antall_utkast"] != 1:
            klassifiseringsavvik.append(
                f"{merke}: antall_utkast {rad['antall_utkast']}, ventet 1")
    return {"funnavvik": funnavvik,
            "klassifiseringsavvik": klassifiseringsavvik,
            "koeavvik": koeavvik}


def forventet_evidens(rader, med_regler: bool) -> dict[str, int]:
    """Fasiten for EVIDENSKJEDEN, utledet av de samme håndskrevne radene.

    Én `henvendelse.mottatt` og én `avsender.satt` per rad (alle rader
    har adresse, og 160 bokfører adressen for seg), én
    `henvendelse.klassifisert` per dom — menneskets OG regelens —, én
    sak, én lukking, ett lagret og ett forkastet utkast, og
    `stilleregler.satt` for tenanten med regler.
    """
    n = len(rader)
    menneske = sum(1 for r in rader if r[3] is not None)
    regel = sum(1 for r in rader if r[6] and r[6][3] == "regel")
    return {
        "henvendelse.mottatt": n,
        "avsender.satt": n,
        "henvendelse.klassifisert": menneske + regel,
        "henvendelse.til_unntakskoe":
            sum(1 for r in rader if r[4] == "unntakskoe"),
        "henvendelse.lukket": sum(1 for r in rader if r[4] == "lukk"),
        "utkast.lagret": sum(1 for r in rader if r[4] == "utkast"),
        "utkast.forkastet": sum(1 for r in rader if r[4] == "utkast"),
        "stilleregler.satt": 1 if med_regler else 0,
    }


def maal_evidens(rt, tenant: str, rader, med_regler: bool,
                 aktor: str) -> list[str]:
    """Måler evidenskjeden mot fasiten. -> liste med avvik.

    TRE PÅSTANDER, som m23: antallet per hendelse, aktør på hver, og
    IDENTITET — hver hendelse har sin egen `input_hash`, så en rad kan
    knyttes til sin henvendelse."""
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, aktor, "m17-fasit")
    rader_db = rt.execute(
        "SELECT handling, aktor, input_hash FROM revisjonslogg"
        " WHERE tenant=%s AND kilde='m17_kundeservice'", (tenant,)).fetchall()
    rt.rollback()
    ventet = {k: v for k, v in forventet_evidens(rader, med_regler).items()
              if v}
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
        avvik.append(f"{uten_aktor} hendelser uten aktør")
    for handling, antall in ventet.items():
        if len(hasher.get(handling, ())) != antall:
            avvik.append(f"{handling}: {len(hasher.get(handling, ()))} ulike"
                         f" input_hash for {antall} hendelser — kjeden har"
                         " mistet identiteten")
    return avvik


def kjor_sett(runde_id: str, rt, regelrunde, sveip,
              aktor: str = "m17-fasit") -> dict:
    """Driver HELE settet: legger inn, regelrunde, sveip, måler køen.

      rt           — runtime-tilkobling (rollen API-et bruker)
      regelrunde() — kjører regelklassifiseringen som planarbeideren
      sveip()      — kjører henvendelsessveipen som sveiperollen
    """
    ut = {}
    for rolle, rader in bygg_sett():
        tenant = tenantnavn(runde_id, rolle)
        ider = _legg_inn(rt, tenant, rader, rolle == "med_regler", aktor,
                         runde_id)
        ut[rolle] = {"tenant": tenant, "ider": ider, "rader": rader}
    regelrunde()
    sveip()
    for rolle, d in ut.items():
        d["avvik"] = maal(rt, d["tenant"], d["ider"], d["rader"], aktor)
        d["avvik"]["evidensavvik"] = maal_evidens(
            rt, d["tenant"], d["rader"], rolle == "med_regler", aktor)
    return ut


AKSER = ("funnavvik", "klassifiseringsavvik", "koeavvik", "evidensavvik")


def artefakt(kjoring: dict, vert: str, ts: str, sveipetid_ms: int,
             sveip_tenanter: int, bevisrot: str) -> dict:
    """Artefaktet, med dommen REGNET AV AVVIKENE — aldri av driveren."""
    talt = {akse: sum(len(d["avvik"][akse]) for d in kjoring.values())
            for akse in AKSER}
    henvendelser = sum(len(d["rader"]) for d in kjoring.values())
    ventede_funn = sum(len(r[5]) for d in kjoring.values()
                       for r in d["rader"] if r[5] != LUKKET)
    regelklassifisert = sum(1 for d in kjoring.values() for r in d["rader"]
                            if r[6] and r[6][3] == "regel")
    evidenshendelser = sum(
        sum(forventet_evidens(d["rader"], rolle == "med_regler").values())
        for rolle, d in kjoring.items())
    return {
        "krav_id": "m17-fasit-v1",
        "ts": ts,
        "bestatt": all(v == 0 for v in talt.values()),
        "oppsett": {
            "modul": "m17_kundeservice", "vert": vert,
            "sett_versjon": SETT_VERSJON,
            "sett_sha256": sett_sha256(),
            "bevisrot_sha256": bevisrot,
            "tenanter": sorted(d["tenant"] for d in kjoring.values()),
        },
        "maalt": {
            "henvendelser": henvendelser,
            "ventede_funn": ventede_funn,
            "regelklassifisert": regelklassifisert,
            "evidenshendelser": evidenshendelser,
            **talt,
            "sveipetid_ms": sveipetid_ms,
            "sveip_tenanter": sveip_tenanter,
        },
        "avvik": {rolle: d["avvik"] for rolle, d in sorted(kjoring.items())},
    }
