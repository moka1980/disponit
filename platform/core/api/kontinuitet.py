"""M-35 kontinuitetsflatens API (089, dommene 1–5).

Leseveien er ÉN transaksjon som avleder hele flatens tilstand av
registeret (modellstyring-formen: aldri lagret, aldri stale). Skrive-
veien er tre browserendepunkter bak `kontinuitet:write` — hver av dem
gjør nøyaktig ett kall mot en claimer-eid SECURITY DEFINER-dør i 089;
ingen av dem rører en tabell direkte, og ingen av dem kan omgå
append-only-vakten eller etteranalyse-kravet, fordi kravet bor i
DØREN og ikke her.

ÆRLIGHETEN I NAVNENE (dom 5) er flatens, ikke basens: tallene fra
backupskriptets statusfil heter `maalt_restoretid_s` og
`maalt_backupalder_s` hele veien ut, og locale-nøklene sier «målt
restore-tid» og «målt backupalder» — aldri «RTO» og aldri «RPO» om
tall som er proxyer for dem.

Merk hva leseveien IKKE gjør: den leser aldri statusfilen. Statusfilen
er rot-lesbar driftsevidens på verten (0640), og et web-API som leste
den ville gjort en filsystemtilstand til et HTTP-svar. Tallene når
flaten gjennom ØVELSENS artefakt (PR-B promoterer det); til da viser
seksjonen ærlig at ingen øvelse er registrert.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

#: Hvor mange hendelser flaten viser, og hvor mange tidslinjeposter per
#: hendelse. Taket er flatens, ikke registerets — en krisehåndtering med
#: 500 poster skal ikke kunne gjøre ett HTTP-svar til en nedlasting.
MAKS_HENDELSER = 50
MAKS_POSTER = 200

#: SP-2-navnerommet for de deterministisk utledede id-ene (m8-formen):
#: samme Idempotency-Key + samme posisjon gir samme id, så et gjenspill
#: treffer dørens egen materialitetssjekk i stedet for å føde en ny rad.
_M35_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m35:kontinuitet")


def _utled(tenant: str, nokkel: str, ledd: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M35_NS, f"{ledd}\x1f{tenant}\x1f{nokkel}")


def svar_for(conn, tenant: str) -> dict:
    """Hele flatens lesetilstand i én transaksjon.

    Fire seksjoner, samme fire som flaten tegner: siste øvelse (tom til
    PR-B promoterer artefaktet), tjenestekartet, kontaktene og
    hendelsene med tidslinjen sin.
    """
    tjenester = [
        {"tjeneste_id": str(r[0]), "referent_type": r[1],
         "referent_id": r[2], "kritikalitet": r[3],
         "rto_maal_s": r[4], "rpo_maal_s": r[5],
         "playbook_ref": r[6], "kontaktrolle": r[7],
         "oppdatert": r[8].isoformat(), "oppdatert_av": r[9]}
        for r in conn.execute(
            "SELECT tjeneste_id, referent_type, referent_id, kritikalitet,"
            " rto_maal_s, rpo_maal_s, playbook_ref, kontaktrolle,"
            " oppdatert_ts, oppdatert_av FROM kontinuitet_tjeneste"
            " WHERE tenant=%s"
            " ORDER BY CASE kritikalitet WHEN 'kritisk' THEN 0"
            "          WHEN 'viktig' THEN 1 ELSE 2 END,"
            "          referent_type, referent_id", (tenant,)).fetchall()]

    kontakter = [
        {"kontakt_id": str(r[0]), "rolle": r[1], "prioritet": r[2],
         "bruker_id": r[3],
         "bekreftet": r[4].isoformat() if r[4] is not None else None,
         "bekreftet_av": r[5]}
        for r in conn.execute(
            "SELECT kontakt_id, rolle, prioritet, bruker_id, bekreftet_ts,"
            " bekreftet_av FROM beredskapskontakt WHERE tenant=%s"
            " ORDER BY rolle, prioritet", (tenant,)).fetchall()]

    # Åpne hendelser FØRST (lukket_ts NULL sorterer først), nyeste øverst
    # innen hver gruppe: det en beredskapsflate skal vise er det som
    # brenner nå, ikke det som brant lengst siden.
    hendelser = []
    for r in conn.execute(
            "SELECT hendelse_id, tekstnokkel, parametre, alvor, apnet_ts,"
            " apnet_av, lukket_ts, lukket_av FROM kontinuitetshendelse"
            " WHERE tenant=%s"
            " ORDER BY (lukket_ts IS NOT NULL), apnet_ts DESC,"
            "          hendelse_id LIMIT %s",
            (tenant, MAKS_HENDELSER)).fetchall():
        hendelser.append({
            "hendelse_id": str(r[0]), "tekstnokkel": r[1],
            "parametre": r[2], "alvor": r[3],
            "apnet": r[4].isoformat(), "apnet_av": r[5],
            "lukket": r[6].isoformat() if r[6] is not None else None,
            "lukket_av": r[7], "tidslinje": []})
    if hendelser:
        indeks = {h["hendelse_id"]: h for h in hendelser}
        # TAKET GJELDER PER HENDELSE, IKKE PÅ TVERS (CodeRabbit på 089).
        # Et flatt `LIMIT MAKS_HENDELSER * MAKS_POSTER` sortert på
        # hendelse_id lot ÉN hendelse med mange tusen poster spise hele
        # budsjettet, slik at hendelsene etter den kom tilbake med tom
        # tidslinje — og en tom tidslinje leses som «ingenting skjedde»,
        # ikke som «vi viste deg ikke alt». Vinduet gir hver hendelse
        # sine egne MAKS_POSTER, uansett hvor stor naboen er.
        for r in conn.execute(
                "SELECT hendelse_id, post_id, posttype, ts, aktor, tekst"
                " FROM (SELECT hendelse_id, post_id, posttype, ts, aktor,"
                "              tekst,"
                "              row_number() OVER (PARTITION BY hendelse_id"
                "                                 ORDER BY ts, post_id) AS n"
                "         FROM kontinuitetshendelse_post"
                "        WHERE tenant=%s AND hendelse_id = ANY(%s)) p"
                " WHERE p.n <= %s ORDER BY hendelse_id, ts, post_id",
                (tenant, [uuidlib.UUID(h["hendelse_id"]) for h in hendelser],
                 MAKS_POSTER)).fetchall():
            indeks[str(r[0])]["tidslinje"].append(
                {"post_id": str(r[1]), "posttype": r[2],
                 "ts": r[3].isoformat(), "aktor": r[4], "tekst": r[5]})

    return {
        # Tom til PR-B promoterer øvelsesartefaktet. `null` er ærligere
        # enn en nullstilt rapport: flaten sier «ingen øvelse
        # registrert», aldri «restore-tid 0 s».
        "siste_ovelse": None,
        "tjenester": tjenester,
        "kontakter": kontakter,
        "hendelser": hendelser,
    }


def kontinuitet(tjeneste, request):
    """GET /v1/kontinuitet (kontinuitet:read) — tenantens egen
    beredskapstilstand, avledet i lesetransaksjonen."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "kontinuitet:read", _fn)


def _tekst(kropp, felt: str, rid, maks: int = 4000) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() \
            or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _hendelse_id(request, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get("hendelse_id")))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


#: De ENESTE feilklassene dørene bruker som DOM. Alt annet er en
#: driftsfeil (CodeRabbit på 089): en tapt forbindelse, en syntaksfeil
#: eller en manglende rettighet på selve funksjonen er ikke «tilstanden
#: nekter» — å svare 409 på dem ville fortalt et menneske i en krise at
#: hendelsen dens er i feil tilstand, mens sannheten er at basen er
#: nede. Uoversatte feil KASTES VIDERE, så den delte rammen
#: (`_med_conn`) svarer `db_utilgjengelig` og driftsloggen får den.
_DOERDOMMER = (
    # Dørenes egne RAISE-er: etteranalyse-kravet
    # (integrity_constraint_violation) og vaktenes nei
    # (insufficient_privilege på RAD-nivå, reist av triggerne i 089).
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.InsufficientPrivilege,
    psycopg.errors.CheckViolation,
)


def _doerfeil(e, rid):
    """Oversettelsen fra dørens ERRCODE til flatens feilkode — ÉN
    kilde, så alle tre skriveveiene svarer likt på samme dom.

    Returnerer et `_Avbrudd` for de dommene dørene faktisk feller, og
    `None` for alt annet — kalleren kaster da originalfeilen videre.
    """
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, _DOERDOMMER):
        # 409, ikke 400: kroppen ER velformet — det er TILSTANDEN som
        # sier nei, og forskjellen er hele forklaringen mennesket
        # trenger.
        return _Avbrudd(_feil("kontinuitet_ulovlig_tilstand", rid, 409))
    return None


def hendelser_endepunkt(tjeneste, request):
    """POST /v1/kontinuitet/hendelser (kontinuitet:write, idem) — åpne en
    hendelse. Døren skriver 'opprettet'-posten i SAMME transaksjon; SP-2
    på den utledede id-en gjør et gjenspill til et stille ja."""
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "kontinuitet:write")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        tekstnokkel = _tekst(kropp, "tekstnokkel", rid, 128)
        alvor = kropp.get("alvor")
        if alvor not in ("kritisk", "alvorlig", "begrenset"):
            raise _Avbrudd(_feil("request_feilformet", rid))
        parametre = kropp.get("parametre", {})
        if not isinstance(parametre, dict):
            raise _Avbrudd(_feil("request_feilformet", rid))
        import json
        hid = _utled(tenant, nokkel, "hendelse")
        try:
            conn.execute(
                "SELECT m35_opprett_hendelse(%s,%s,%s::jsonb,%s,%s,%s)",
                (tenant, tekstnokkel, json.dumps(parametre), alvor,
                 _bid, hid))
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"hendelse_id": str(hid)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def post_endepunkt(tjeneste, request):
    """POST /v1/kontinuitet/hendelse/{hendelse_id}/post
    (kontinuitet:write, idem) — én tidslinjepost. Posttypene 'opprettet'
    og 'lukket' er DØRENES egne og avvises her av døren, ikke av et
    lokalt sett: to kilder til samme sannhet er én for mange."""
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "kontinuitet:write")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        hid = _hendelse_id(request, rid)
        posttype = kropp.get("posttype")
        if not isinstance(posttype, str):
            raise _Avbrudd(_feil("request_feilformet", rid))
        tekst = _tekst(kropp, "tekst", rid)
        pid = _utled(tenant, nokkel, "post")
        try:
            conn.execute("SELECT m35_legg_post(%s,%s,%s,%s,%s,%s)",
                         (tenant, hid, posttype, tekst, _bid, pid))
        except psycopg.errors.InvalidParameterValue as e:
            # Dørens eget nei på en posttype som er dens egen.
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"post_id": str(pid)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def lukk_endepunkt(tjeneste, request):
    """POST /v1/kontinuitet/hendelse/{hendelse_id}/lukk
    (kontinuitet:write, idem) — lukk hendelsen.

    ETTERANALYSE-KRAVET HÅNDHEVES IKKE HER. Døren leser hendelsen FOR
    UPDATE, krever en 'etteranalyse'-post i tidslinjen, skriver
    'lukket'-posten og flipper hodet i ÉN transaksjon. Et forsøk uten
    etteranalyse blir 409 — ikke fordi API-et sjekket, men fordi basen
    nektet. Det er hele poenget: en flate som sjekket selv ville vært en
    andre sannhet å omgå.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "kontinuitet:write")
        _krev_idem(request, rid)
        kropp = _kropp(request)
        hid = _hendelse_id(request, rid)
        tekst = _tekst(kropp, "tekst", rid)
        try:
            conn.execute("SELECT m35_lukk_hendelse(%s,%s,%s,%s)",
                         (tenant, hid, _bid, tekst))
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"hendelse_id": str(hid), "lukket": True}, rid)

    return _med_conn(tjeneste, rid, kjor)
