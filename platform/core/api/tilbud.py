"""M-26 tilbudsregisterets API (migrasjon 169, ARC B tilbud PR 1).

Fire endepunkter: to leseveier og to skriveveier, alle mot dører eid av
`disponit_prisbok_eier` i 169. Egen fil ved siden av `api/prisbok.py`:
prisboka setter ingen pris og genererer inget tilbud, og det står —
doktrineporten måler den fila. Tilbudet er registeret VED SIDEN AV boka:
en avskrift av boka på en dato, mot én kunde, med standardklausulene
bundet ved sin hash.

KUNDENS ADRESSE KRYPTERES HER, i API-ets tillit (160-formen, AAD
`m26:kunde`), og forlater aldri prosessen i klartekst: svaret og lista
bærer masken. Klarteksten går til eiermodulen i claim-svaret (PR 4).

TALLENE ER BOKAS. API-et ganger ikke og runder ikke: døra slår opp prisen
på tilbudsdatoen, og en enhetspris kalleren gir må være lik eller lavere
(rabatt) — aldri høyere. Summen er dørens.

SCOPENE: lesingen bærer `okonomi:read` (som prisboka), skrivingen
`bestilling:opprett` (som 096/100–108).
"""
from __future__ import annotations

import hashlib
import re
import uuid as uuidlib

import psycopg

MAKS_TILBUD = 200
MAKS_NAVN = 200
MAKS_REF = 120
MAKS_INNLEDNING = 4000
MAKS_LINJER = 100
_EPOST = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_AAD_KUNDE = b"m26:kunde"
DOMMER = ("godkjent", "forkastet")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(uuidlib.NAMESPACE_URL,
                         f"disponit:{art}:{tenant}:{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    v = kropp.get(felt)
    if not isinstance(v, str) or not v.strip() or len(v) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid,
                             detalj=f"{felt} mangler eller er for langt"))
    return v


def _valgfri_tekst(kropp, felt: str, rid, maks: int):
    if kropp.get(felt) is None:
        return None
    return _tekst(kropp, felt, rid, maks)


def _maske(adresse: str) -> str:
    norm = adresse.strip().lower()
    if "@" in norm and norm.index("@") > 0:
        return norm[0] + "****" + norm[norm.index("@"):]
    return norm[:1] + "****" + norm[-2:]


def _hash(adresse: str) -> str:
    return hashlib.sha256(adresse.strip().lower().encode("utf-8")).hexdigest()


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params[navn]))
    except (KeyError, ValueError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _doerfeil(e, rid):
    from .policyadmin_http import _Avbrudd, _doerdetalj, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        return _Avbrudd(_feil("idempotenskonflikt", rid,
                              detalj=_doerdetalj(e)))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404,
                              detalj=_doerdetalj(e)))
    if isinstance(e, (psycopg.errors.InvalidParameterValue,
                      psycopg.errors.InsufficientPrivilege,
                      psycopg.errors.CheckViolation)):
        # Dørenes egne RAISE-er: et produkt uten pris på datoen, en pris
        # over boka, et avgjort tilbud som avgjøres igjen. Kroppen ER
        # velformet — det er innholdskravet basen håndhever som sier nei.
        return _Avbrudd(_feil("tilbud_ulovlig_tilstand", rid, 409,
                              detalj=_doerdetalj(e)))
    return None


def _skriv(tjeneste, request, bygg):
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        sql, args, svar = bygg(conn, tenant, bid, nokkel, kropp, rid, request)
        try:
            rad = conn.execute(sql, args).fetchone()
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok(svar(rad), rid)

    return _med_conn(tjeneste, rid, kjor)


def _rad_til_tilbud(r) -> dict:
    return {"tilbud_id": str(r[0]), "kunde_navn": r[1], "kunde_ref": r[2],
            "kunde_maske": r[3], "tilbudsdato": r[4].isoformat(),
            "gyldig_til": r[5].isoformat(), "valuta": r[6],
            "sum_ore": r[7], "status": r[8], "antall_linjer": r[9],
            "priser_fra_boka": bool(r[10]), "klausuler_uendret": bool(r[11]),
            "avgjort_ts": r[12].isoformat() if r[12] else None,
            "avgjort_av": r[13], "opprettet": r[14].isoformat(),
            "opprettet_av": r[15]}


def svar_for(conn, tenant: str) -> dict:
    """Tilbudsflatens tilstand: lista med de to faktaene regnet av
    registeret. Aldri adressen — bare masken."""
    rader = [_rad_til_tilbud(r) for r in conn.execute(
        "SELECT * FROM m26_tilbudene(%s,%s)", (tenant, MAKS_TILBUD)).fetchall()]
    return {"avsenderprofil": avsenderprofil_for(conn, tenant),
            "sammendrag": {
                "vist": len(rader),
                "utkast": sum(1 for t in rader if t["status"] == "utkast"),
                "godkjente": sum(1 for t in rader if t["status"] == "godkjent"),
                "sum_godkjent_ore": sum(t["sum_ore"] for t in rader
                                        if t["status"] == "godkjent")},
            "tilbud": rader}


def bilde(tjeneste, request):
    """GET /v1/tilbud (okonomi:read) — lista og avsenderprofilen."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def detalj_endepunkt(tjeneste, request):
    """GET /v1/tilbud/{tilbud_id} (okonomi:read): linjene og klausulene
    slik de ble bundet — teksten fra klausulversjonen tilbudet siterer."""
    from .lesing import _les, kanonisk_json
    from .policyadmin_http import _feil

    def _fn(conn, auth, rid):
        tid = _sti_uuid(request, "tilbud_id", rid)
        hode = conn.execute("SELECT * FROM m26_tilbudshodet(%s,%s)",
                            (auth.tenant, tid)).fetchone()
        rad = conn.execute("SELECT * FROM m26_tilbudet(%s,%s)",
                           (auth.tenant, tid)).fetchone()
        if rad is None or hode is None:
            return _feil("ikke_funnet", rid, 404)
        mine = [_rad_til_tilbud(hode)]
        b = conn.execute("SELECT * FROM m26_tilbudsbestillingen(%s,%s)",
                         (auth.tenant, tid)).fetchone()
        svar = {**mine[0], "innledning": rad[0], "linjer": rad[1],
                "klausuler": rad[2], "request_id": rid,
                "bestilling": ({"utfall": b[0], "oppdrag_id": b[1],
                                "unntak_id": b[2], "bestilt_ts": b[4].isoformat()}
                               if b else None)}
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def lag_endepunkt(tjeneste, request):
    """POST /v1/tilbud (bestilling:opprett, idem): kunden, datoene,
    linjene (produkt_id, antall, valgfri enhetspris_ore). Prisen slås opp
    i boka av døra; svaret er summen og masken."""
    from .policyadmin_http import _Avbrudd, _feil

    def bygg(conn, tenant, bid, nokkel, kropp, rid, _request):
        from db import kryptering
        navn = _tekst(kropp, "kunde_navn", rid, MAKS_NAVN)
        epost = _tekst(kropp, "kunde_epost", rid, 254)
        if not _EPOST.fullmatch(epost.strip()):
            raise _Avbrudd(_feil("request_feilformet", rid,
                                 detalj="kunde_epost er ikke en adresse"))
        ref = _valgfri_tekst(kropp, "kunde_ref", rid, MAKS_REF)
        dato = _valgfri_tekst(kropp, "tilbudsdato", rid, 32)
        gyldig = _tekst(kropp, "gyldig_til", rid, 32)
        innledning = _valgfri_tekst(kropp, "innledning", rid, MAKS_INNLEDNING)
        linjer = kropp.get("linjer")
        if not isinstance(linjer, list) or not linjer \
                or len(linjer) > MAKS_LINJER \
                or not all(isinstance(x, dict) for x in linjer):
            raise _Avbrudd(_feil("request_feilformet", rid,
                                 detalj="linjer mangler eller er feil formet"))
        rene = []
        for x in linjer:
            try:
                pid = str(uuidlib.UUID(str(x.get("produkt_id"))))
            except ValueError as e:
                raise _Avbrudd(_feil("request_feilformet", rid,
                                     detalj="produkt_id")) from e
            antall = x.get("antall")
            pris = x.get("enhetspris_ore")
            if isinstance(antall, bool) or not isinstance(antall, int) \
                    or antall < 1 or antall > 1_000_000:
                raise _Avbrudd(_feil("request_feilformet", rid,
                                     detalj="antall"))
            if pris is not None and (isinstance(pris, bool)
                                     or not isinstance(pris, int)
                                     or pris < 0 or pris > 10 ** 13):
                raise _Avbrudd(_feil("request_feilformet", rid,
                                     detalj="enhetspris_ore"))
            rene.append({"produkt_id": pid, "antall": antall,
                         "enhetspris_ore": pris})
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
        ct, nonce = kryptering.krypter(dek, {"e": epost.strip()}, tenant,
                                       key_id, ekstra_aad=_AAD_KUNDE)
        tid = _utled("tilbud", tenant, nokkel)
        import json
        maske = _maske(epost)
        return ("SELECT * FROM m26_lag_tilbud(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                "coalesce(%s::date, current_date),%s::date,%s,%s::jsonb,%s)",
                (tenant, tid, navn.strip(), ref, _hash(epost), maske, ct, nonce,
                 key_id, dato, gyldig, innledning,
                 json.dumps(rene), bid),
                lambda rad: {"tilbud_id": str(rad[1]), "ny": bool(rad[0]),
                             "sum_ore": int(rad[2]), "kunde_maske": maske})
    return _skriv(tjeneste, request, bygg)


def dom_endepunkt(tjeneste, request):
    """POST /v1/tilbud/{tilbud_id}/dom (bestilling:opprett, idem):
    `godkjent` eller `forkastet`. «Sendt» er aldri en dom — det er det
    som skjer (PR 5)."""
    from .policyadmin_http import _Avbrudd, _feil

    def bygg(_conn, tenant, bid, _nokkel, kropp, rid, request):
        tid = _sti_uuid(request, "tilbud_id", rid)
        status = kropp.get("status")
        if status not in DOMMER:
            raise _Avbrudd(_feil("request_feilformet", rid, detalj="status"))
        return ("SELECT m26_avgjor_tilbud(%s,%s,%s,%s)",
                (tenant, tid, status, bid),
                lambda rad: {"tilbud_id": str(tid), "status": status,
                             "ny": bool(rad[0])})
    return _skriv(tjeneste, request, bygg)


MAKS_AVSENDERNAVN = 120
MAKS_SIGNATUR = 500


def avsenderprofil_endepunkt(tjeneste, request):
    """POST /v1/tilbud/avsender (bestilling:opprett, idem): navnet
    tilbudet sendes i, svar-til og signatur. Tenantens, ikke tilbudets."""
    from .policyadmin_http import _Avbrudd, _feil

    def bygg(_conn, tenant, bid, _nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "avsender_navn", rid, MAKS_AVSENDERNAVN)
        svar_til = _valgfri_tekst(kropp, "svar_til", rid, 254)
        if svar_til is not None and not _EPOST.fullmatch(svar_til.strip()):
            raise _Avbrudd(_feil("request_feilformet", rid,
                                 detalj="svar_til er ikke en adresse"))
        signatur = _valgfri_tekst(kropp, "signatur", rid, MAKS_SIGNATUR)
        return ("SELECT m26_sett_avsenderprofil(%s,%s,%s,%s,%s)",
                (tenant, navn, svar_til, signatur, bid),
                lambda rad: {"avsender_navn": navn.strip(),
                             "svar_til": svar_til, "signatur": signatur,
                             "ny": bool(rad[0])})
    return _skriv(tjeneste, request, bygg)


def avsenderprofil_for(conn, tenant: str):
    rad = conn.execute("SELECT * FROM m26_avsenderprofilen(%s)",
                       (tenant,)).fetchone()
    if rad is None:
        return None
    return {"avsender_navn": rad[0], "svar_til": rad[1], "signatur": rad[2],
            "oppdatert": rad[3].isoformat()}


def utforelse_for_sending(conn, tenant: str, tilbud_id) -> dict:
    """Det claim-veien gir tilbudsmodulen ved siden av payloaden (172).

    ADRESSEN DEKRYPTERES HER, i API-ets tillit, med tenantens DEK — og
    lever bare i claim-svaret, aldri i oppdraget. Linjene og
    klausulteksten er den bundne versjonen (det tilbudet siterte). En
    `hindring` betyr at modulen skal kvittere `feilet` uten å sende.
    """
    if not tilbud_id:
        return {"hindring": "tilbud_ukjent"}
    rad = conn.execute("SELECT f.*, current_date FROM m26_for_sending(%s,%s) f",
                       (tenant, tilbud_id)).fetchone()
    if rad is None:
        return {"hindring": "tilbud_ukjent"}
    (status, gyldig, navn, maske, ct, nonce, key_id, sum_ore, valuta, dato,
     innledning, linjer, klausuler, p_navn, p_svar_til, p_sign, i_dag) = rad
    if status != "godkjent":
        return {"hindring": "tilbud_ikke_godkjent"}
    if gyldig < i_dag:
        return {"hindring": "tilbud_utlopt"}
    if not linjer:
        return {"hindring": "tilbud_uten_linjer"}
    from db import kryptering
    try:
        dek = kryptering.hent_dek(conn, tenant, key_id)
        adresse = kryptering.dekrypter(dek, bytes(ct), bytes(nonce), tenant,
                                       key_id, ekstra_aad=_AAD_KUNDE)["e"]
    except psycopg.Error:
        raise
    except Exception:                                   # noqa: BLE001
        return {"hindring": "mottaker_uleselig"}
    if not isinstance(adresse, str) or not _EPOST.fullmatch(adresse):
        return {"hindring": "mottaker_uleselig"}
    return {"mottaker_epost": adresse, "mottaker_maske": maske,
            "kunde_navn": navn, "sum_ore": int(sum_ore), "valuta": valuta,
            "tilbudsdato": str(dato), "gyldig_til": str(gyldig),
            "innledning": innledning, "linjer": linjer,
            "klausuler": klausuler, "avsender_navn": p_navn,
            "svar_til": p_svar_til, "signatur": p_sign}


def bokfor_tilbud_sendt(conn, tenant: str, oppdrag_id: int,
                        kvittering: dict, aktor: str) -> dict:
    """Kvitteringens vei tilbake til registeret (173). -> {bokfort} eller
    {avvik: <grunn>} — aldri et unntak ut: kalleren har alt et sendt
    tilbud å stå inne for.

    RESSURSEN MÅ VÆRE OPPDRAGETS: kvitteringen er signert av modulen, men
    modulen kunne navngi et annet tilbud enn det oppdraget gjaldt.
    Payloaden dekrypteres her, som ved claim, og sammenlignes FØR noe
    bokføres.
    """
    from datetime import datetime

    ressurs = str(kvittering.get("ressurs_id") or "")
    if not ressurs.startswith("tilbud:"):
        return {"avvik": "ressurs_id_ikke_tilbud"}
    try:
        tid = uuidlib.UUID(ressurs.split(":", 1)[1])
    except ValueError:
        return {"avvik": "kvittering_uten_gyldig_ressurs"}
    from db import kryptering
    orad = conn.execute(
        "SELECT payload_kryptert, key_id, nonce FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (tenant, int(oppdrag_id))).fetchone()
    if orad is None:
        return {"avvik": "oppdrag_ukjent"}
    nok = conn.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s"
        " AND key_id=%s", (tenant, orad[1])).fetchone()
    try:
        dek = kryptering._pakk_ut((orad[1], nok[0]), tenant)[1]
        payload = kryptering.dekrypter(dek, bytes(orad[0]), bytes(orad[2]),
                                       tenant, orad[1])
    except Exception:                                   # noqa: BLE001
        return {"avvik": "oppdrag_uleselig"}
    if str(payload.get("tilbud_id") or "") != str(tid):
        return {"avvik": "ressurs_avvik"}
    raa = kvittering.get("sendt_ts")
    try:
        sendt = (datetime.fromisoformat(str(raa).replace("Z", "+00:00"))
                 if raa else None)
        if sendt is not None and sendt.tzinfo is None:
            sendt = None
    except ValueError:
        sendt = None
    try:
        with conn.transaction():
            rad = conn.execute(
                "SELECT m26_tilbud_sendt(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, tid, int(oppdrag_id), sendt,
                 str(kvittering.get("malversjon") or "")[:64] or None,
                 str(kvittering.get("mottaker_maske") or "")[:254] or None,
                 aktor)).fetchone()
    except psycopg.Error as e:
        return {"avvik": f"dor_nektet:{type(e).__name__}"}
    if rad is None:
        return {"avvik": "dor_uten_svar"}
    return {"bokfort": bool(rad[0])}
