"""Det syntetiske postbokssettet for M-6s sertifisering.

ETT SETT, TO LEDD, samme form som `m17_fasit.py`/`m23_fasit.py`: settet
drives gjennom de EKTE dørene både lokalt (CI,
`platform/core/tests/test_m6_fasit_port.py`) og på staging
(`m6-fasit-artefakt.py`). Artefaktet bærer `sett_sha256` over bytene i
denne filen, og porten krever likhet med de innsjekkede.

MANIFESTETS KRAV, ORDRETT: «syntetisk postboks med fasit … målt både
lokalt og på staging». Postboksen er en RIGGET GRAPH: `hent_en(conn, rad,
graf=…, veksler=…)` tar transporten som parameter, og alt annet — token-
utpakking, delta-paging, kroppshenting, kryptering, idempotens, broen til
kundeserviceregisteret (203), sending som svar i tråden — er produktets
eget. Fasiten bytter bare ut Microsoft.

ALDRI `kjor_en_runde` HER: den er kryss-tenant og ville dratt KUNDENES
postbokser gjennom den riggede transporten. Fasiten driver sine egne
kilder, én om gangen.

FIRE FASITER I SAMME SETT, alle skrevet FØR kjøringen:

  * INNTAKET: to delta-sider i runde 1 (nextLink), én i runde 2 (fra
    lagret deltaLink). Kantene: en `@removed`-oppføring (hoppes over,
    telles ikke), en melding hvis kropp Graph ikke gir (404 →
    forhåndsvisningen står), en uten avsender, en med vedlegg, og den
    samme meldingen to ganger (ON CONFLICT → én rad, `nye` teller ikke).
  * BROEN (203): hver ny melding er én henvendelse i M-17 — med maske
    når adressen finnes, uten når den mangler — og den samme meldingen
    to ganger er én henvendelse.
  * SVARET: et utkast på en melding, «send» → `sendes`, og utsendingen
    går som REPLY i tråden (`/me/messages/{id}/reply`) nøyaktig én gang —
    runde to på samme rad er `hoppet`. I tenanten UTEN sendescope nekter
    døren (`mangler sendetilgang`) og ingenting postes.
  * EVIDENSKJEDEN: M-6s egne hendelser (`epost.utkast_skrevet`,
    `epost.svar_bestilt`, `epost.svar_sendt`, `epost.melding_slettet`)
    og broens (`henvendelse.mottatt`, `avsender.satt`), med identitet.

TO TENANTER: én med sendescope på postboksen og én uten. Uten scopet er
svarveien stengt av samtykkets egne ord — og det er nettopp det
forskjellen måler.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from pathlib import Path

SETT_VERSJON = "m6-fasit-1"
AKTOR = "m6-fasit"
KUNDE = "kunde@fasit.example"
KUNDE_NAVN = "Fasit Kunde"
POSTBOKS = "post@fasit.example"
KROPP = "Hei, dette er kundens tekst i meldingen {merke}."
UTKAST = "Takk for meldingen — vi svarer i tråden."

#: Meldingene, i rekkefølge, per side: (merke, avsender?, vedlegg?,
#: kropp fra Graph?, fjernet?)
SIDE_1 = [("m1", True, False, True, False),
          ("m2", True, True, True, False),
          ("m3", True, False, True, True),      # @removed — hoppes over
          ("m4", True, False, False, False),    # kroppen gir 404
          ("m5", False, False, True, False)]    # uten avsender
SIDE_2 = [("m6", True, False, True, False),
          ("m1", True, False, True, False)]     # duplikat
SIDE_3 = [("m7", True, False, True, False),     # runde 2, fra deltaLink
          ("m6", True, False, True, False)]     # duplikat

#: Fasiten for inntaket, TELT av sidene: `sett` teller ikke `@removed`.
VENTET_RUNDE_1 = {"sett": 6, "nye": 5, "sider": 2}
VENTET_RUNDE_2 = {"sett": 2, "nye": 1, "sider": 1}
MELDINGER = ["m1", "m2", "m4", "m5", "m6", "m7"]     # radene som finnes
UTEN_AVSENDER = {"m5"}
UTEN_KROPP = {"m4"}
SLETTES = "m2"
SVARES_PAA = "m1"


def sett_sha256() -> str:
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def bygg_sett() -> list[tuple[str, bool]]:
    """-> [(rolle, kan_svare)] — deterministisk, to tenanter."""
    return [("med_sendescope", True), ("uten_sendescope", False)]


def tenantnavn(runde_id: str, rolle: str) -> str:
    return f"t-m6fasit-{rolle}-{runde_id}"


def ny_runde() -> str:
    return secrets.token_hex(4)


def lev_id(merke: str, runde_id: str) -> str:
    return f"AAMk-{merke}-{runde_id}"


def _melding(merke: str, runde_id: str, avsender: bool, vedlegg: bool,
             fjernet: bool) -> dict:
    if fjernet:
        return {"id": lev_id(merke, runde_id), "@removed": {"reason": "deleted"}}
    m = {"id": lev_id(merke, runde_id), "conversationId": f"c-{runde_id}",
         "receivedDateTime": f"2026-09-16T1{MELDINGER.index(merke) if merke in MELDINGER else 0}:00:00Z",
         "toRecipients": [{"emailAddress": {"address": POSTBOKS}}],
         "subject": f"Fasit {merke}", "bodyPreview": f"Forhåndsvisning {merke}",
         "hasAttachments": vedlegg}
    if avsender:
        m["from"] = {"emailAddress": {"address": KUNDE, "name": KUNDE_NAVN}}
    return m


def lag_graf(runde_id: str, kall: list):
    """Den riggede transporten: sidene i rekkefølge, kroppen per melding,
    404 for den ene. `kall` bærer hver URL, så porten kan telle."""
    from plan.epost import GRAPH, GraphFeil
    sider = [
        {"value": [_melding(r[0], runde_id, r[1], r[2], r[4]) for r in SIDE_1],
         "@odata.nextLink": f"{GRAPH}/fasit/side2"},
        {"value": [_melding(r[0], runde_id, r[1], r[2], r[4]) for r in SIDE_2],
         "@odata.deltaLink": f"{GRAPH}/fasit/delta1"},
        {"value": [_melding(r[0], runde_id, r[1], r[2], r[4]) for r in SIDE_3],
         "@odata.deltaLink": f"{GRAPH}/fasit/delta2"},
    ]
    uten_kropp = {lev_id(m, runde_id) for m in UTEN_KROPP}

    def graf(access, url, *, tekstkropp=False):
        kall.append(url)
        if "/me/messages/" in url:
            mid = url.split("/me/messages/")[1].split("?")[0]
            if mid in uten_kropp:
                raise GraphFeil(404, "fasit: kroppen finnes ikke")
            merke = mid.split("-")[1]
            return {"body": {"contentType": "text",
                             "content": KROPP.format(merke=merke)}}
        if not sider:
            return {"value": [], "@odata.deltaLink": f"{GRAPH}/fasit/tom"}
        return sider.pop(0)
    return graf


def lag_veksler():
    def veksler(_konfig, _refresh):
        return {"access_token": "fasit-" + secrets.token_hex(4)}
    return veksler


def lag_poster(postet: list):
    def poster(_access, url, kropp):
        postet.append((url, kropp))
    return poster


def _dek(rt, tenant):
    from db import kryptering
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, AKTOR, "m6-fasit")
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(rt, tenant)
    rt.commit()
    return key_id, dek


def lag_kilde(rt, tenant: str, kan_svare: bool) -> uuid.UUID:
    """Postboksen inn gjennom samme rad callbacken skriver — som runtime,
    med samtykkets scope som ord."""
    from api.epost_kilde import SENDESCOPE
    from db import kryptering
    from db.pg import sett_kontekst
    key_id, dek = _dek(rt, tenant)
    ct, nonce = kryptering.krypter(dek, {"refresh_token": "fasit-refresh"},
                                   tenant, key_id)
    scope = "https://graph.microsoft.com/Mail.Read offline_access"
    if kan_svare:
        scope += " " + SENDESCOPE
    sett_kontekst(rt, tenant, AKTOR, "m6-fasit")
    # SAMME KOLONNER SOM CALLBACKEN: runtime har kolonnegrant uten
    # `status` — standardverdien er `aktiv`, og det er den kilden fødes med.
    kid = rt.execute(
        "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
        " auth_kryptert, nonce, key_id, scope)"
        " VALUES (%s,'m365',%s,%s,%s,%s,%s) RETURNING kilde_id",
        (tenant, POSTBOKS, ct, nonce, key_id, scope)).fetchone()[0]
    rt.commit()
    return kid


def kilderad(pa, tenant: str, kilde_id) -> tuple:
    """Raden `hent_en` tar — samme form som `kandidater` gir den."""
    from db.pg import sett_kontekst
    from plan.epost import AKTOR as PLANAKTOR
    sett_kontekst(pa, tenant, PLANAKTOR, "m6-fasit")
    rad = pa.execute(
        "SELECT tenant, kilde_id, postboks, delta_token, sist_hentet_ts"
        " FROM epost_kilde WHERE tenant=%s AND kilde_id=%s",
        (tenant, kilde_id)).fetchone()
    pa.rollback()
    return rad


def melding_id_for(rt, tenant: str, kilde_id, merke: str, runde_id: str):
    from db.pg import sett_kontekst
    sett_kontekst(rt, tenant, AKTOR, "m6-fasit")
    rad = rt.execute(
        "SELECT melding_id FROM epost_melding WHERE tenant=%s AND kilde_id=%s"
        " AND leverandor_melding_id=%s",
        (tenant, kilde_id, lev_id(merke, runde_id))).fetchone()
    rt.rollback()
    return rad[0] if rad else None


def kjor_sett(runde_id: str, rt, pa, aktor: str = AKTOR) -> dict:
    """Driver HELE settet for begge tenantene.

      rt — runtime-tilkobling (kilden, utkastet, slettingen, lesingen)
      pa — planarbeiderens tilkobling (inntaket og utsendingen)
    """
    import time

    from api.epost_kilde import SENDESCOPE  # noqa: F401 — krever konfig
    from db import kryptering
    from db.pg import sett_kontekst
    from plan.epost import hent_en, send_ett

    ut = {}
    for rolle, kan_svare in bygg_sett():
        tenant = tenantnavn(runde_id, rolle)
        kid = lag_kilde(rt, tenant, kan_svare)
        kall: list = []
        graf = lag_graf(runde_id, kall)
        veksler = lag_veksler()
        t0 = time.monotonic()
        r1 = hent_en(pa, kilderad(pa, tenant, kid), graf=graf, veksler=veksler)
        tid1 = int((time.monotonic() - t0) * 1000)
        r2 = hent_en(pa, kilderad(pa, tenant, kid), graf=graf, veksler=veksler)
        d = {"tenant": tenant, "kilde_id": kid, "kan_svare": kan_svare,
             "runde1": r1, "runde2": r2, "innhentingstid_ms": tid1,
             "graf_kall": list(kall), "postet": [], "send_nektet": None,
             "send1": None, "send2": None}
        # SVARET på m1: utkast (runtime) → «send» → utsending (plan).
        mid = melding_id_for(rt, tenant, kid, SVARES_PAA, runde_id)
        key_id, dek = _dek(rt, tenant)
        ct, nonce = kryptering.krypter(dek, {"tekst": UTKAST}, tenant, key_id)
        sett_kontekst(rt, tenant, aktor, "m6-fasit")
        uid = rt.execute("SELECT m6_skriv_svarutkast(%s,%s,%s,%s,%s,%s)",
                         (tenant, mid, ct, nonce, key_id, aktor)).fetchone()[0]
        rt.commit()
        d["utkast_id"] = uid
        sett_kontekst(rt, tenant, aktor, "m6-fasit")
        try:
            rt.execute("SELECT m6_send_svaret(%s,%s,%s)", (tenant, uid, aktor))
            rt.commit()
        except Exception as e:                              # noqa: BLE001
            rt.rollback()
            d["send_nektet"] = type(e).__name__
        if d["send_nektet"] is None:
            poster = lag_poster(d["postet"])
            rad = (tenant, uid, mid, kid)
            d["send1"] = send_ett(pa, rad, poster=poster, veksler=veksler)
            d["send2"] = send_ett(pa, rad, poster=poster, veksler=veksler)
        # SLETTINGEN av m2 (runtime).
        mid2 = melding_id_for(rt, tenant, kid, SLETTES, runde_id)
        sett_kontekst(rt, tenant, aktor, "m6-fasit")
        rt.execute("SELECT m6_slett_melding(%s,%s,%s)", (tenant, mid2, aktor))
        rt.commit()
        ut[rolle] = d
    for rolle, d in ut.items():
        d["avvik"] = maal(rt, d, runde_id)
        d["avvik"]["evidensavvik"] = maal_evidens(rt, d)
        # AVVIKLES ETTER MÅLINGEN: en aktiv kilde med et rigget token er
        # noe planrunden ellers ville prøvd mot Microsoft hvert femte
        # minutt (og satt `feilet` på), og i den delte testbasen ville
        # `kjor_en_runde` plukket den i en annen port. `deaktivert` er
        # avviklingsformen (088-vakten) — raden og meldingene står.
        sett_kontekst(rt, d["tenant"], aktor, "m6-fasit")
        rt.execute("UPDATE epost_kilde SET status='deaktivert'"
                   " WHERE tenant=%s AND kilde_id=%s",
                   (d["tenant"], d["kilde_id"]))
        rt.commit()
    return ut


def maal(rt, d: dict, runde_id: str) -> dict:
    """Måler inntak, bro og svar mot fasiten. -> avvik per akse,
    RETURNERT — aldri kastet."""
    from api.epost_meldinger import meldinger_for, utkast_for
    from api.kundeservice import svar_for
    from db.pg import sett_kontekst
    tenant, kid = d["tenant"], d["kilde_id"]
    inntak: list[str] = []
    bro: list[str] = []
    svar: list[str] = []
    for navn, res, ventet in (("runde 1", d["runde1"], VENTET_RUNDE_1),
                              ("runde 2", d["runde2"], VENTET_RUNDE_2)):
        for k, v in ventet.items():
            if res.get(k) != v:
                inntak.append(f"{navn}: {k}={res.get(k)}, ventet {v}")
        if res.get("feilet") or res.get("forbigaende"):
            inntak.append(f"{navn}: {res}")
    # Det mennesket ser i innboksen.
    sett_kontekst(rt, tenant, AKTOR, "m6-fasit")
    liste = meldinger_for(rt, tenant, kid)
    rt.rollback()
    per_ref = {}
    sett_kontekst(rt, tenant, AKTOR, "m6-fasit")
    for r in rt.execute("SELECT melding_id, leverandor_melding_id FROM"
                        " epost_melding WHERE tenant=%s AND kilde_id=%s",
                        (tenant, kid)).fetchall():
        per_ref[r[1]] = str(r[0])
    rt.rollback()
    if liste["vist"] != len(MELDINGER) or liste["avkortet"]:
        inntak.append(f"innboksen viser {liste['vist']} rader, ventet"
                      f" {len(MELDINGER)} (avkortet={liste['avkortet']})")
    per_id = {m["melding_id"]: m for m in liste["meldinger"]}
    for merke in MELDINGER:
        mid = per_ref.get(lev_id(merke, runde_id))
        rad = per_id.get(mid)
        if rad is None:
            inntak.append(f"{merke}: raden står ikke i innboksen")
            continue
        if merke == SLETTES:
            if not rad["reapet"] or rad["emne"] is not None:
                inntak.append(f"{merke}: slettet, men teksten står")
            continue
        if rad["emne"] != f"Fasit {merke}":
            inntak.append(f"{merke}: emne {rad['emne']!r}")
        if merke in UTEN_KROPP and rad["forhandsvisning"] != f"Forhåndsvisning {merke}":
            inntak.append(f"{merke}: forhåndsvisningen skulle stått uten kropp")
        if merke in UTEN_AVSENDER and rad["fra"]:
            inntak.append(f"{merke}: skulle vært uten avsender")
        if merke not in UTEN_AVSENDER and rad["fra"] != KUNDE:
            inntak.append(f"{merke}: fra {rad['fra']!r}")
        if rad["har_vedlegg"] != (merke == "m2"):
            inntak.append(f"{merke}: har_vedlegg {rad['har_vedlegg']}")
    # BROEN: én henvendelse per melding, masken der adressen finnes.
    sett_kontekst(rt, tenant, AKTOR, "m6-fasit")
    koe = svar_for(rt, tenant)["koe"]
    rt.rollback()
    if len(koe) != len(MELDINGER):
        bro.append(f"{len(koe)} henvendelser for {len(MELDINGER)} meldinger")
    med = sum(1 for h in koe if h["har_avsender"])
    if med != len(MELDINGER) - len(UTEN_AVSENDER):
        bro.append(f"{med} henvendelser med avsender, ventet"
                   f" {len(MELDINGER) - len(UTEN_AVSENDER)}")
    for h in koe:
        if h["har_avsender"] and h["avsender_maske"] != "k****@fasit.example":
            bro.append(f"masken {h['avsender_maske']!r}")
    # SVARET.
    sett_kontekst(rt, tenant, AKTOR, "m6-fasit")
    utkast = utkast_for(rt, tenant, per_ref.get(lev_id(SVARES_PAA, runde_id)))
    rt.rollback()
    if d["kan_svare"]:
        if d["send_nektet"] is not None:
            svar.append(f"døren nektet med sendescope: {d['send_nektet']}")
        if not (d["send1"] or {}).get("sendt"):
            svar.append(f"første utsending: {d['send1']}")
        if "hoppet" not in (d["send2"] or {}):
            svar.append(f"andre utsending sendte igjen: {d['send2']}")
        if len(d["postet"]) != 1:
            svar.append(f"{len(d['postet'])} POST mot Graph, ventet 1")
        else:
            url, kropp = d["postet"][0]
            if not url.endswith(f"/me/messages/{lev_id(SVARES_PAA, runde_id)}/reply"):
                svar.append(f"svaret gikk ikke som reply i tråden: {url}")
            if kropp != {"comment": UTKAST}:
                svar.append("svarteksten er ikke utkastets")
        if [u["status"] for u in utkast] != ["sendt"]:
            svar.append(f"utkastet: {[u['status'] for u in utkast]}")
    else:
        if d["send_nektet"] is None:
            svar.append("døren slapp «send» gjennom uten sendescope")
        if d["postet"]:
            svar.append(f"{len(d['postet'])} POST mot Graph uten sendescope")
        if [u["status"] for u in utkast] != ["foreslatt"]:
            svar.append(f"utkastet: {[u['status'] for u in utkast]}")
    return {"inntaksavvik": inntak, "broavvik": bro, "svaravvik": svar}


def forventet_evidens(kan_svare: bool) -> dict[str, int]:
    """Fasiten for kjeden, per tenant: M-6s egne hendelser og broens."""
    m6 = {"epost.utkast_skrevet": 1, "epost.melding_slettet": 1}
    if kan_svare:
        m6.update({"epost.svar_bestilt": 1, "epost.svar_sendt": 1})
    bro = {"henvendelse.mottatt": len(MELDINGER),
           "avsender.satt": len(MELDINGER) - len(UTEN_AVSENDER)}
    return {"m06_epost": m6, "m17_kundeservice": bro}


def maal_evidens(rt, d: dict) -> list[str]:
    from db.pg import sett_kontekst
    tenant = d["tenant"]
    sett_kontekst(rt, tenant, AKTOR, "m6-fasit")
    rader = rt.execute(
        "SELECT kilde, handling, aktor, input_hash FROM revisjonslogg"
        " WHERE tenant=%s AND kilde IN ('m06_epost','m17_kundeservice')",
        (tenant,)).fetchall()
    rt.rollback()
    avvik = []
    for kilde, ventet in forventet_evidens(d["kan_svare"]).items():
        talt: dict[str, int] = {}
        hasher: dict[str, set] = {}
        for k, handling, akt, ih in rader:
            if k != kilde:
                continue
            talt[handling] = talt.get(handling, 0) + 1
            hasher.setdefault(handling, set()).add(ih)
            if not (akt or "").strip():
                avvik.append(f"{kilde}/{handling}: hendelse uten aktør")
        for handling, antall in ventet.items():
            if talt.get(handling, 0) != antall:
                avvik.append(f"{kilde}/{handling}: ventet {antall}, fikk"
                             f" {talt.get(handling, 0)}")
            elif len(hasher.get(handling, ())) != antall:
                avvik.append(f"{kilde}/{handling}: {len(hasher[handling])}"
                             f" ulike input_hash for {antall} hendelser")
        ukjent = set(talt) - set(ventet)
        if ukjent:
            avvik.append(f"{kilde}: handlinger fasiten ikke venter:"
                         f" {sorted(ukjent)}")
    return avvik


AKSER = ("inntaksavvik", "broavvik", "svaravvik", "evidensavvik")


def artefakt(kjoring: dict, vert: str, ts: str, bevisrot: str) -> dict:
    """Artefaktet, med dommen REGNET AV AVVIKENE — aldri av driveren."""
    talt = {akse: sum(len(d["avvik"][akse]) for d in kjoring.values())
            for akse in AKSER}
    evidens = sum(sum(sum(v.values()) for v in forventet_evidens(d["kan_svare"]).values())
                  for d in kjoring.values())
    return {
        "krav_id": "m6-fasit-v1",
        "ts": ts,
        "bestatt": all(v == 0 for v in talt.values()),
        "oppsett": {
            "modul": "m06_epost", "vert": vert,
            "sett_versjon": SETT_VERSJON,
            "sett_sha256": sett_sha256(),
            "bevisrot_sha256": bevisrot,
            "tenanter": sorted(d["tenant"] for d in kjoring.values()),
        },
        "maalt": {
            "meldinger": len(MELDINGER) * len(kjoring),
            "nye_runde1": VENTET_RUNDE_1["nye"] * len(kjoring),
            "nye_runde2": VENTET_RUNDE_2["nye"] * len(kjoring),
            "henvendelser": len(MELDINGER) * len(kjoring),
            "sendt": sum(1 for d in kjoring.values()
                         if (d["send1"] or {}).get("sendt")),
            "evidenshendelser": evidens,
            **talt,
            "innhentingstid_ms": max(1, sum(d["innhentingstid_ms"]
                                            for d in kjoring.values())),
        },
        "avvik": {rolle: d["avvik"] for rolle, d in sorted(kjoring.items())},
    }
