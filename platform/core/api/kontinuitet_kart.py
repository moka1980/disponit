"""M-35: veien inn til tjenestekartet og beredskapskontaktene (ADR-008).

089 bygget dørene, rettighetene og leseflaten — men ingen HTTP-vei kalte
`m35_opprett_tjeneste`, `m35_oppdater_tjeneste`, `m35_opprett_kontakt`
eller `m35_bekreft_kontakt`. En kunde kunne se et tomt kart og ingenting
gjøre med det. Denne filen er de fire veiene, pluss katalogen kunden
velger playbook fra.

TO TING GJØR API-ET SELV, fordi dørene ikke kan:

1. Playbooken kommer inn som NAVN. Referansen (`navn@sha256`) settes
   sammen av `playbookkatalog` fra bytes i releasen. Basen kjenner bare
   formen på referansen; hva som finnes i repoet vet bare serveren.
2. En kontakt må være et AKTIVT MEDLEM av tenanten. Fremmednøkkelen i 089
   peker på den globale `brukeridentitet`, så døren ville godtatt en
   bruker fra et annet firma.

Alt annet — idempotens, frosset identitet, én kontakt per rolle og
prioritet — er dørenes og vaktenes dom, oversatt av `_doerfeil`.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

from .kontinuitet import _doerfeil, _tekst, _utled

REFERENTTYPER = ("systemd_unit", "modul", "ekstern")
KRITIKALITETER = ("kritisk", "viktig", "normal")
#: Ett år. Et mål over dette er en tastefeil, ikke en ambisjon.
MAKS_MAAL_S = 366 * 86400


def playbooks(tjeneste, request):
    """GET /v1/kontinuitet/playbooks (kontinuitet:read) — katalogen."""
    from .lesing import _les, kanonisk_json
    from playbookkatalog import katalog

    def _fn(conn, auth, rid):
        return kanonisk_json({"playbooks": list(katalog()),
                              "request_id": rid}, 200,
                             {"x-request-id": rid})
    return _les(tjeneste, request, "kontinuitet:read", _fn)


def _valg(kropp, felt: str, lovlige, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi not in lovlige:
        raise _Avbrudd(_feil("request_feilformet", rid,
                             detalj=f"«{felt}»: ugyldig verdi"))
    return verdi


def _maal(kropp, felt: str, rid) -> int:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    # `bool` er en `int` i Python; `true` er ikke et antall sekunder.
    if (not isinstance(verdi, int) or isinstance(verdi, bool)
            or not 0 < verdi <= MAKS_MAAL_S):
        raise _Avbrudd(_feil("request_feilformet", rid,
                             detalj=f"«{felt}»: skal være et helt antall"
                                    " sekunder over null"))
    return verdi


def _playbook_ref(kropp, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    from playbookkatalog import ref_for
    ref = ref_for(kropp.get("playbook"))
    if ref is None:
        raise _Avbrudd(_feil("request_feilformet", rid,
                             detalj="«playbook»: finnes ikke i katalogen"))
    return ref


def _sti_id(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _kall(conn, rid, sql: str, param: tuple):
    try:
        conn.execute(sql, param)
    except psycopg.Error as e:
        avbrudd = _doerfeil(e, rid)
        if avbrudd is None:
            raise               # driftsfeil — rammen svarer db_utilgjengelig
        raise avbrudd from e


def tjenester_endepunkt(tjeneste, request):
    """POST /v1/kontinuitet/tjenester (kontinuitet:write, idem) — et nytt
    kartinnslag. Én rad per referent: et andre innslag for samme tjeneste
    er dørens `unique_violation`, og svaret er en konflikt."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "kontinuitet:write")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        rtype = _valg(kropp, "referent_type", REFERENTTYPER, rid)
        rid_ = _tekst(kropp, "referent_id", rid, 200).strip()
        krit = _valg(kropp, "kritikalitet", KRITIKALITETER, rid)
        rto, rpo = _maal(kropp, "rto_maal_s", rid), _maal(kropp,
                                                         "rpo_maal_s", rid)
        ref = _playbook_ref(kropp, rid)
        rolle = _tekst(kropp, "kontaktrolle", rid, 100).strip()
        tid = _utled(tenant, nokkel, "tjeneste")
        _kall(conn, rid,
              "SELECT m35_opprett_tjeneste(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (tenant, rtype, rid_, krit, rto, rpo, ref, rolle, bid, tid))
        conn.commit()
        return _ok({"tjeneste_id": str(tid), "playbook_ref": ref}, rid)

    return _med_conn(tjeneste, rid, kjor)


def tjeneste_endepunkt(tjeneste, request):
    """POST /v1/kontinuitet/tjeneste/{tjeneste_id} (kontinuitet:write,
    idem) — oppdater innholdet. Identiteten (hva innslaget gjelder) er
    frosset; den står ikke i kroppen og kan ikke endres her."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "kontinuitet:write")
        _krev_idem(request, rid)
        kropp = _kropp(request)
        tid = _sti_id(request, "tjeneste_id", rid)
        krit = _valg(kropp, "kritikalitet", KRITIKALITETER, rid)
        rto, rpo = _maal(kropp, "rto_maal_s", rid), _maal(kropp,
                                                         "rpo_maal_s", rid)
        ref = _playbook_ref(kropp, rid)
        rolle = _tekst(kropp, "kontaktrolle", rid, 100).strip()
        _kall(conn, rid,
              "SELECT m35_oppdater_tjeneste(%s,%s,%s,%s,%s,%s,%s,%s)",
              (tenant, tid, krit, rto, rpo, ref, rolle, bid))
        conn.commit()
        return _ok({"tjeneste_id": str(tid), "playbook_ref": ref}, rid)

    return _med_conn(tjeneste, rid, kjor)


def kontakter_endepunkt(tjeneste, request):
    """POST /v1/kontinuitet/kontakter (kontinuitet:write, idem) — en ny
    beredskapskontakt. Fødes UBEKREFTET; bekreftelsen er en egen handling.
    """
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "kontinuitet:write")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        rolle = _tekst(kropp, "rolle", rid, 100).strip()
        prioritet = kropp.get("prioritet")
        if (not isinstance(prioritet, int) or isinstance(prioritet, bool)
                or not 1 <= prioritet <= 9):
            raise _Avbrudd(_feil("request_feilformet", rid,
                                 detalj="«prioritet»: skal være 1–9"))
        bruker = _tekst(kropp, "bruker_id", rid, 100)
        # MEDLEMSKAPET SJEKKES HER: døren ser bare at brukeren finnes et
        # sted på plattformen. Uten dette kunne et firma føre opp en
        # bruker fra et annet — og lære av svaret om id-en finnes.
        if conn.execute(
                "SELECT 1 FROM brukermedlemskap WHERE tenant=%s"
                " AND bruker_id::text=%s AND aktiv",
                (tenant, bruker)).fetchone() is None:
            raise _Avbrudd(_feil("request_feilformet", rid,
                                 detalj="«bruker_id»: er ikke et aktivt"
                                        " medlem av firmaet"))
        kid = _utled(tenant, nokkel, "kontakt")
        _kall(conn, rid,
              "SELECT m35_opprett_kontakt(%s,%s,%s::smallint,%s,%s,%s)",
              (tenant, rolle, prioritet, bruker, bid, kid))
        conn.commit()
        return _ok({"kontakt_id": str(kid)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def bekreft_endepunkt(tjeneste, request):
    """POST /v1/kontinuitet/kontakt/{kontakt_id}/bekreft
    (kontinuitet:write, idem) — kontakten er bekreftet NÅ. Ny bekreftelse
    er lovlig og er hele poenget: dekningen måler ferskhet."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "kontinuitet:write")
        _krev_idem(request, rid)
        kid = _sti_id(request, "kontakt_id", rid)
        _kall(conn, rid, "SELECT m35_bekreft_kontakt(%s,%s,%s)",
              (tenant, kid, bid))
        conn.commit()
        return _ok({"kontakt_id": str(kid), "bekreftet": True}, rid)

    return _med_conn(tjeneste, rid, kjor)
