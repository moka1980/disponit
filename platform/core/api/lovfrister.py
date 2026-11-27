"""Lovpålagte frister — Frist- og pliktvakten, fase 1 (237).

Eier 27/11: kjernen kunden betaler for er «Frist- og pliktvakt», og først
det som «ikke krever ki modell kall og har verdi for kunder nå».

VEIEN FOR KUNDEN, tre trinn:

1. `POST /v1/lovfrister/oppslag` {orgnummer} — Enhetsregisteret (åpne
   data) sier organisasjonsform, MVA-registrering og om firmaet har
   ansatte. Ingenting lagres; kunden ser forslaget og retter det.
2. `POST /v1/lovfrister` {orgnummer, orgform, mva, ansatte, kilde} —
   profilen lagres, og firmaets frister for de neste tolv månedene
   registreres i M-21s pliktregister (096) med kunden som EIER. Der bor
   varslene (30/7/1 døgn før, i appen og på e-post) og kvitteringen.
3. `GET /v1/lovfrister` — profilen og fristene, med status fra M-21.

PLIKT-ID-EN ER AVLEDET av (tenant, katalognøkkel, frist), så et nytt
trykk på «Oppdater» legger bare til det som er nytt i katalogen. Det som
alt står, rører vi ikke — heller ikke om eieren har rettet teksten i
katalogen etterpå (M-21s SP-2 ville kalt det en materiell konflikt; her
er det bare «fantes fra før»).

PLATTFORMEIEREN vedlikeholder katalogen: `GET /v1/plattform/pliktkatalog`,
`POST …/mal`, `POST …/frist`, `POST …/frist/slett`. Fullmakten er raden i
`plattformeier` (199), slått opp av dørene selv.

ENHETSREGISTERET nås gjennom den SSRF-sikre klienten (`ssrf.lag_klient`):
fast vert, ingen redirects, korte tidsavbrudd og en grense på svaret.
Org.nr. er ni sifre før det settes inn i stien.
"""
from __future__ import annotations

import datetime
import json
import re
import uuid as uuidlib
from zoneinfo import ZoneInfo

OSLO = ZoneInfo("Europe/Oslo")
#: Hvor langt fram fristene registreres. Litt over et år, så neste års
#: frist for en årlig plikt er med når den foregående er levert.
HORISONT_DOGN = 400
BRREG_URL = "https://data.brreg.no/enhetsregisteret/api/enheter/{orgnr}"
MYNDIGHETER = ("skatteetaten", "brreg", "altinn", "nav", "arbeidstilsynet",
               "ssb", "annen")
_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:lovfrister:237")
_ORGNR = re.compile(r"^[0-9]{9}$")
_ORGFORM = re.compile(r"^[A-Z]{2,4}$")
_NOKKEL = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def plikt_id(tenant: str, nokkel: str, frist: datetime.date) -> uuidlib.UUID:
    return uuidlib.uuid5(_NS, f"{tenant}|{nokkel}|{frist.isoformat()}")


def i_dag() -> datetime.date:
    return datetime.datetime.now(OSLO).date()


# ---------------------------------------------------------------------------
# Katalog og profil
# ---------------------------------------------------------------------------

def katalog(conn) -> list[dict]:
    """Malene med fristene sine, i nøkkelorden."""
    maler: dict[str, dict] = {}
    for (nokkel, tittel, myndighet, hjemmel, kilde_url, orgformer, mva,
         ansatte, aktiv, periode, frist) in conn.execute(
            "SELECT * FROM plattform_pliktkatalog()").fetchall():
        m = maler.setdefault(nokkel, {
            "nokkel": nokkel, "tittel": tittel, "myndighet": myndighet,
            "hjemmel": hjemmel, "kilde_url": kilde_url,
            "orgformer": list(orgformer or []), "krever_mva": mva,
            "krever_ansatte": ansatte, "aktiv": aktiv, "frister": []})
        if frist is not None:
            m["frister"].append({"periode": periode,
                                 "frist": frist.isoformat()})
    return list(maler.values())


def gjelder(mal: dict, profil: dict) -> bool:
    if not mal["aktiv"]:
        return False
    if mal["orgformer"] and profil["orgform"] not in mal["orgformer"]:
        return False
    if mal["krever_mva"] and not profil["mva"]:
        return False
    if mal["krever_ansatte"] and not profil["ansatte"]:
        return False
    return True


def firmaets_frister(maler: list[dict], profil: dict,
                     fra: datetime.date,
                     til: datetime.date | None = None) -> list[dict]:
    """Fristene som gjelder firmaet fra `fra` til `til` (standard:
    HORISONT_DOGN fra i dag)."""
    til = til or i_dag() + datetime.timedelta(days=HORISONT_DOGN)
    ut = []
    for m in maler:
        if not gjelder(m, profil):
            continue
        for f in m["frister"]:
            dato = datetime.date.fromisoformat(f["frist"])
            if fra <= dato <= til:
                ut.append({"nokkel": m["nokkel"], "tittel": m["tittel"],
                           "periode": f["periode"], "frist": dato,
                           "myndighet": m["myndighet"],
                           "hjemmel": m["hjemmel"],
                           "kilde_url": m["kilde_url"]})
    ut.sort(key=lambda x: (x["frist"], x["nokkel"]))
    return ut


def profil_for(conn, tenant: str) -> dict | None:
    r = conn.execute("SELECT * FROM firma_pliktprofil_les(%s)",
                     (tenant,)).fetchone()
    if r is None:
        return None
    return {"orgnummer": r[0], "orgform": r[1], "mva": r[2],
            "ansatte": r[3], "kilde": r[4], "oppdatert": r[5].isoformat()}


def _firmaets_orgnummer(conn, tenant: str) -> str | None:
    try:
        with conn.transaction():
            r = conn.execute("SELECT orgnummer FROM firma_hent(%s)",
                             (tenant,)).fetchone()
        return r[0] if r else None
    except Exception:                                       # noqa: BLE001
        return None


def svar_for(conn, tenant: str) -> dict:
    profil = profil_for(conn, tenant)
    maler = katalog(conn)
    status: dict[str, dict] = {}
    for r in conn.execute("SELECT plikt_id, status, dogn_til_frist,"
                          " kvittering_ref FROM m21_plikter(%s,%s)",
                          (tenant, 500)).fetchall():
        status[str(r[0])] = {"status": r[1], "dogn_til_frist": r[2],
                             "kvittering_ref": r[3]}
    frister = []
    nye = 0
    if profil is not None:
        # Fra 60 dager tilbake: en frist som nettopp gikk ut og ikke er
        # levert, skal fortsatt stå der.
        fra = i_dag() - datetime.timedelta(days=60)
        for f in firmaets_frister(maler, profil, fra):
            pid = str(plikt_id(tenant, f["nokkel"], f["frist"]))
            st = status.get(pid)
            if st is None and f["frist"] >= i_dag():
                nye += 1
            if st is None and f["frist"] < i_dag():
                continue            # aldri registrert, og alt forbi
            frister.append({**f, "frist": f["frist"].isoformat(),
                            "plikt_id": pid, "registrert": st is not None,
                            **(st or {"status": None,
                                      "dogn_til_frist": None,
                                      "kvittering_ref": None})})
    return {"profil": profil, "frister": frister, "nye_i_katalogen": nye,
            "orgnummer_firma": _firmaets_orgnummer(conn, tenant)}


# ---------------------------------------------------------------------------
# Enhetsregisteret
# ---------------------------------------------------------------------------

class OppslagFeil(Exception):
    def __init__(self, kode: str):
        super().__init__(kode)
        self.kode = kode


def tolk_enhet(d: dict) -> dict:
    """Enhetsregisterets svar → forslaget kunden ser. Ren funksjon."""
    form = (d.get("organisasjonsform") or {})
    return {"orgnummer": d.get("organisasjonsnummer"),
            "navn": d.get("navn"),
            "orgform": form.get("kode"),
            "orgform_navn": form.get("beskrivelse"),
            "mva": bool(d.get("registrertIMvaregisteret")),
            "ansatte": bool(d.get("harRegistrertAntallAnsatte"))
                       or bool(d.get("antallAnsatte")),
            "konkurs": bool(d.get("konkurs")),
            "under_avvikling": bool(d.get("underAvvikling"))}


def slaa_opp(orgnr: str) -> dict:
    """Én GET mot Enhetsregisteret. Kaster OppslagFeil med en kode
    flaten kan si noe om."""
    import httpx

    from . import ssrf
    if not _ORGNR.match(orgnr or ""):
        raise OppslagFeil("orgnummer")
    try:
        with ssrf.lag_klient() as k:
            r = k.get(BRREG_URL.format(orgnr=orgnr),
                      headers={"Accept": "application/json"})
            if r.status_code in (404, 410):
                raise OppslagFeil("ikke_funnet")
            if r.status_code != 200:
                raise OppslagFeil("utilgjengelig")
            d = json.loads(ssrf.les_begrenset(r))
    except OppslagFeil:
        raise
    except (httpx.HTTPError, ssrf.SsrfAvvist, ValueError) as e:
        raise OppslagFeil("utilgjengelig") from e
    return tolk_enhet(d)


# ---------------------------------------------------------------------------
# Kundens ruter
# ---------------------------------------------------------------------------

def les_endepunkt(tjeneste, request):
    """GET /v1/lovfrister (decisions:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


def oppslag_endepunkt(tjeneste, request):
    """POST /v1/lovfrister/oppslag (bestilling:opprett) — {orgnummer}."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _feil, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        _browserkontekst(tjeneste, request, conn, rid, "bestilling:opprett")
        orgnr = str(_kropp(request).get("orgnummer") or "").replace(" ", "")
        if not _ORGNR.match(orgnr):
            return _feil("request_feilformet", rid, 400,
                         detalj="orgnummer er ni siffer")
        # Basen trengs ikke under oppslaget; transaksjonen skal ikke stå
        # åpen mens vi venter på Enhetsregisteret.
        conn.rollback()
        try:
            forslag = slaa_opp(orgnr)
        except OppslagFeil as e:
            if e.kode == "orgnummer":
                return _feil("request_feilformet", rid, 400,
                             detalj="orgnummer er ni siffer")
            if e.kode == "ikke_funnet":
                return _feil("ikke_funnet", rid, 404, detalj="orgnummer")
            return _feil("register_utilgjengelig", rid, 503)
        return _ok({"forslag": forslag}, rid)
    return _med_conn(tjeneste, rid, kjor)


def registrer(conn, tenant: str, bid: str, profil: dict) -> dict:
    """Profilen lagres og fristene registreres i M-21. I kallerens
    transaksjon; hver plikt i sitt eget savepoint."""
    import psycopg
    conn.execute("SELECT firma_pliktprofil_sett(%s,%s,%s,%s,%s,%s,%s)",
                 (tenant, profil.get("orgnummer"), profil["orgform"],
                  profil["mva"], profil["ansatte"], profil["kilde"],
                  f"bruker:{bid}"))
    nye = fantes = 0
    for f in firmaets_frister(katalog(conn), profil, i_dag()):
        frist_ts = datetime.datetime.combine(
            f["frist"], datetime.time(23, 59), tzinfo=OSLO)
        kilde = f"{f['hjemmel']} — {f['kilde_url']}"[:500]
        tittel = f"{f['tittel']} – {f['periode']}"[:200]
        try:
            with conn.transaction():
                ny = conn.execute(
                    "SELECT m21_registrer_plikt(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (tenant, plikt_id(tenant, f["nokkel"], f["frist"]),
                     tittel, bid, kilde, frist_ts, "engang", None,
                     bid)).fetchone()[0]
        except psycopg.errors.UniqueViolation:
            ny = False      # står fra før med annen tekst — rør den ikke
        if ny:
            nye += 1
        else:
            fantes += 1
    return {"nye": nye, "fantes": fantes}


def lagre_endepunkt(tjeneste, request):
    """POST /v1/lovfrister (bestilling:opprett)."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _feil, _kropp,
                                   _med_conn, _ok_lagret)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        k = _kropp(request)
        orgnr = k.get("orgnummer")
        if orgnr is not None:
            orgnr = str(orgnr).replace(" ", "") or None
            if orgnr is not None and not _ORGNR.match(orgnr):
                return _feil("request_feilformet", rid, 400,
                             detalj="orgnummer er ni siffer")
        orgform = str(k.get("orgform") or "").strip().upper()
        if not _ORGFORM.match(orgform):
            return _feil("request_feilformet", rid, 400, detalj="orgform")
        mva, ansatte = k.get("mva"), k.get("ansatte")
        if not isinstance(mva, bool) or not isinstance(ansatte, bool):
            return _feil("request_feilformet", rid, 400,
                         detalj="mva og ansatte er ja/nei")
        kilde = k.get("kilde", "manuell")
        if kilde not in ("brreg", "manuell"):
            return _feil("request_feilformet", rid, 400, detalj="kilde")
        profil = {"orgnummer": orgnr, "orgform": orgform, "mva": mva,
                  "ansatte": ansatte, "kilde": kilde}
        with conn.transaction():
            tall = registrer(conn, tenant, bid, profil)
        return _ok_lagret(conn, {**tall, **svar_for(conn, tenant)}, rid)
    return _med_conn(tjeneste, rid, kjor)


# ---------------------------------------------------------------------------
# Plattformeieren
# ---------------------------------------------------------------------------

def katalog_endepunkt(tjeneste, request):
    """GET /v1/plattform/pliktkatalog."""
    from .app import _rid, kanonisk_json
    from .policyadmin_http import _leseauth, _med_conn
    rid = _rid(request)

    def kjor(conn):
        _leseauth(tjeneste, request, conn, rid)
        return kanonisk_json({"maler": katalog(conn),
                              "myndigheter": list(MYNDIGHETER),
                              "request_id": rid}, 200,
                             {"x-request-id": rid})
    return _med_conn(tjeneste, rid, kjor)


def _eier_skriv(tjeneste, request, fn):
    from .app import _rid
    from .plattform_http import _bid
    from .policyadmin_http import _feil, _kropp, _med_conn, _ok_lagret
    rid = _rid(request)

    def kjor(conn):
        import psycopg
        _tenant, bid = _bid(tjeneste, request, conn, rid)
        k = _kropp(request)
        try:
            with conn.transaction():
                feil = fn(conn, bid, k)
        except psycopg.errors.InsufficientPrivilege:
            return _feil("ikke_plattformeier", rid, 403)
        except (psycopg.errors.CheckViolation,
                psycopg.errors.ForeignKeyViolation):
            return _feil("request_feilformet", rid, 400)
        if feil:
            return _feil("request_feilformet", rid, 400, detalj=feil)
        return _ok_lagret(conn, {"maler": katalog(conn)}, rid)
    return _med_conn(tjeneste, rid, kjor)


def _tekst(k, felt, maks):
    v = k.get(felt)
    if not isinstance(v, str) or not v.strip() or len(v.strip()) > maks:
        return None
    return v.strip()


def _dato(k, felt):
    v = k.get(felt)
    try:
        return datetime.date.fromisoformat(v) if isinstance(v, str) else None
    except ValueError:
        return None


def mal_endepunkt(tjeneste, request):
    """POST /v1/plattform/pliktkatalog/mal — legg til eller rett en mal."""
    def fn(conn, bid, k):
        nokkel = k.get("nokkel")
        if not isinstance(nokkel, str) or not _NOKKEL.match(nokkel):
            return "nokkel"
        tittel = _tekst(k, "tittel", 200)
        hjemmel = _tekst(k, "hjemmel", 300)
        kilde = _tekst(k, "kilde_url", 500)
        if not tittel:
            return "tittel"
        if not hjemmel:
            return "hjemmel"
        if not kilde or not kilde.startswith("https://") or " " in kilde:
            return "kilde_url"
        if k.get("myndighet") not in MYNDIGHETER:
            return "myndighet"
        former = k.get("orgformer", [])
        if not isinstance(former, list) or not all(
                isinstance(f, str) and _ORGFORM.match(f) for f in former):
            return "orgformer"
        flagg = [k.get("krever_mva", False), k.get("krever_ansatte", False),
                 k.get("aktiv", True)]
        if not all(isinstance(x, bool) for x in flagg):
            return "krever_mva/krever_ansatte/aktiv"
        conn.execute(
            "SELECT plattform_pliktmal_sett(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (bid, nokkel, tittel, k["myndighet"], hjemmel, kilde,
             sorted(set(former)), *flagg))
        return None
    return _eier_skriv(tjeneste, request, fn)


def frist_endepunkt(tjeneste, request):
    """POST /v1/plattform/pliktkatalog/frist — {nokkel, periode, frist}."""
    def fn(conn, bid, k):
        periode = _tekst(k, "periode", 100)
        frist = _dato(k, "frist")
        if not isinstance(k.get("nokkel"), str):
            return "nokkel"
        if not periode:
            return "periode"
        if frist is None:
            return "frist"
        conn.execute("SELECT plattform_pliktfrist_sett(%s,%s,%s,%s)",
                     (bid, k["nokkel"], periode, frist))
        return None
    return _eier_skriv(tjeneste, request, fn)


def frist_slett_endepunkt(tjeneste, request):
    """POST /v1/plattform/pliktkatalog/frist/slett — {nokkel, frist}.
    En frist som alt er registrert hos et firma, står der fortsatt —
    katalogen er kilden for NYE registreringer, ikke for firmaenes egne."""
    def fn(conn, bid, k):
        frist = _dato(k, "frist")
        if not isinstance(k.get("nokkel"), str) or frist is None:
            return "frist"
        conn.execute("SELECT plattform_pliktfrist_slett(%s,%s,%s)",
                     (bid, k["nokkel"], frist))
        return None
    return _eier_skriv(tjeneste, request, fn)
