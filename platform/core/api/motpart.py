"""M-48 foretaks- og kredittvaktens API (migrasjon 116).

Ni endepunkter: fire leseveier og fem skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_motpart_eier`-eid SECURITY DEFINER-dør i 116, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN SLÅR OPP ETT STED, OG BARE ETT (eierbeslutning 3/9).
Foretaksregisteret er koblet på; kredittleverandøren er det ikke, og
den finnes ikke i denne fila. Se `foretaksregister.py` for hvorfor de
to er forskjellige.

DEN VIKTIGSTE DETALJEN I HELE MODULEN STÅR I `oppslag_endepunkt`:
RESERVASJONEN COMMITTES FØR FORESPØRSELEN GÅR UT.

Det er ikke en optimalisering, det er hele designet. Gjorde vi
reservasjon, forespørsel og fullføring i ÉN transaksjon, ville en krasj
midtveis rullet tilbake reservasjonen — og da hadde forespørselen gått
ut av huset uten at det fantes en rad som sa det. Doktrinen sier at den
unødvendige forespørselen ER skaden; en skade vi ruller tilbake loggen
for, er en skade ingen kan telle.

Så: tx A reserverer og committer. Så går forespørselen. Så fullfører tx
B. Dør prosessen mellom A og B, blir reservasjonen STÅENDE — og det er
riktig utfall, ikke et hull: sveipen finner den som `oppslag_uten_svar`
etter en time og setter den til `forlatt` etter seks.

MODULEN SETTER INGEN KREDITTGRENSE OG AVSLÅR INGEN MOTPART. Det finnes
ingen dør her som gjør det, fordi det ikke finnes noen kolonne i 116 å
gjøre det i. Vurderingen er et FORSLAG — spesifikasjonens egen vakt
sier «setter aldri kredittgrensen selv», og kredittgrensen er inngang
til fordringsagenten M-23, ikke omvendt.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som M-13 (101) innførte
    og klynge 3, 4, 5 gjenbrukte.
  * SKRIVINGEN bærer `bestilling:opprett` — presedensen fra 096/100–115.

SP-2 PÅ REGISTRERINGSDØRENE: `motpart_id`, `oppslag_id`, `versjon_id`
og `vurdering_id` utledes deterministisk av Idempotency-Key-en. For
oppslaget er det STRENGT nødvendig: en gjentatt POST må ikke bli to
utgående forespørsler.
"""
from __future__ import annotations

import datetime
import uuid as uuidlib

import psycopg

MAKS_MOTPARTER = 200
MAKS_NAVN = 300
MAKS_BEGRUNNELSE = 2000
MAKS_HJEMMEL = 500

#: Grensene API-et håndhever før døra, slik at en feilformet request
#: får `request_feilformet` og ikke en CHECK-violation forkledd som
#: konflikt. Verdiene MÅ speile CHECK-ene i 116.
KRAVGRENSER = {
    "oppslag_ferskhet_timer": (0, 8760),
    "vurdering_gyldig_dogn": (1, 3650),
    "uvurdert_dogn": (0, 3650),
}
#: Øre. Speiler `maks_forslag_ore`-CHECK-en i 116.
MAKS_ORE = 100_000_000_000

FORMAAL = ("kredittvurdering", "onboarding", "periodisk_kontroll",
           "manuell_gjennomgang")
GRUNNLAG = ("foretaksregister", "manuell_gjennomgang")
FUNNTYPER = ("uvurdert_motpart", "utdatert_vurdering",
             "profil_uten_vurdering", "motpart_avviklet",
             "forslag_over_tak", "oppslag_uten_svar",
             "gjentatte_oppslagsfeil", "ingen_krav")


_M48_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m48:motpart")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M48_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    """Tekst, TRIMMET — og lengden måles på det som faktisk lagres."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valg(kropp, felt: str, rid, lovlige) -> str:
    """Ett av et LUKKET sett."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi not in lovlige:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _heltall(kropp, felt: str, rid, minst: int, mest: int) -> int:
    """Et heltall, og DEN ENESTE veien det kommer inn.

    `isinstance(x, bool)` er ikke pedanteri: i Python er `True` en `int`,
    og uten sjekken ville `{"uvurdert_dogn": true}` blitt ett døgn.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _orgnr(kropp, felt: str, rid) -> str:
    """Ni siffer. VALIDERES HER OG I BASEN, med vilje: den ene gir et
    ærlig 400 til den som skrev feil, den andre gjør det umulig å komme
    utenom. Formkontrollen sier ingenting om at foretaket finnes — det
    er nettopp det oppslaget er til for."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip().replace(" ", "")
    if len(verdi) != 9 or not verdi.isdigit():
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _ore(kropp, felt: str, rid) -> int:
    """Øre, HELTALL. `isinstance(x, bool)` fordi `True` er en `int` i
    Python, og et beløp på «True øre» er ikke et beløp (101s form)."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if isinstance(verdi, bool) or not isinstance(verdi, int):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if verdi < 0 or verdi > MAKS_ORE:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _grunnlagsliste(kropp, felt: str, rid) -> list[str]:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, list) or not verdi:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for g in verdi:
        if not isinstance(g, str) or g not in GRUNNLAG:
            raise _Avbrudd(_feil("request_feilformet", rid))
        if g not in ut:
            ut.append(g)
    return ut


def _doerfeil(e, rid):
    """Dørenes dommer → API-feil. Samme form som 112/114."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if "orgnr_unik" in str(e) or "oppslag_unik" in str(e):
            return _Avbrudd(_feil("motpart_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: et oppslag innenfor ferskhetsvinduet,
        # en ukjent vert, et grunnlag tenanten ikke har godkjent, et
        # oppslag som alt er fullført.
        return _Avbrudd(_feil("motpart_ulovlig_tilstand", rid, 409))
    if isinstance(e, (psycopg.errors.IntegrityConstraintViolation,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.InsufficientPrivilege)):
        return _Avbrudd(_feil("motpart_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Motpartsflatens tilstand i én transaksjon, gjennom tre lesedører."""
    s = conn.execute("SELECT * FROM m48_motpartsstatus(%s)",
                     (tenant,)).fetchone()
    motparter = [
        {"motpart_id": str(r[0]), "organisasjonsnummer": r[1],
         "navn_oppgitt": r[2], "aktiv": r[3],
         "opprettet": r[4].isoformat(),
         "siste_versjon": r[5].isoformat() if r[5] else None,
         "siste_registerstatus": r[6],
         "siste_vurdering": r[7].isoformat() if r[7] else None,
         "siste_forslag_ore": r[8], "apne_funn": r[9]}
        for r in conn.execute("SELECT * FROM m48_motpartene(%s,%s)",
                              (tenant, MAKS_MOTPARTER)).fetchall()]
    k = conn.execute("SELECT * FROM m48_kravene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "motparter": s[0], "aktive": s[1], "med_profil": s[2],
            "vurderte": s[3], "apne_funn": s[4],
            "apne_avviklet": s[5],
            # DET MEST BETENTE TALLET STÅR ØVERST. Klyngens unntak er
            # begrunnet med at forespørselen er nødvendig; da må
            # antallet forespørsler være det første noen ser.
            "oppslag_siste_dogn": s[6],
            "apne_reservasjoner": s[7],
            "har_krav": s[8], "kravversjon": s[9],
            "registrert_vert": s[10], "vist": len(motparter)},
        "motparter": motparter,
        "krav": None if k is None else {
            "oppslag_ferskhet_timer": k[0],
            "vurdering_gyldig_dogn": k[1], "uvurdert_dogn": k[2],
            "maks_forslag_ore": k[3],
            "godkjente_grunnlag": list(k[4] or ()), "versjon": k[5],
            "oppdatert": k[6].isoformat(), "oppdatert_av": k[7]}}


def motpartsbilde(tjeneste, request):
    """GET /v1/motpart (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def historikk_endepunkt(tjeneste, request):
    """GET /v1/motpart/{motpart_id}/historikk (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        mid = _sti_uuid(request, "motpart_id", rid)
        rader = conn.execute("SELECT * FROM m48_versjonene(%s,%s)",
                             (auth.tenant, mid)).fetchall()
        svar = {"motpart_id": str(mid), "request_id": rid,
                "versjoner": [
                    {"versjon_id": str(r[0]),
                     "oppslag_id": str(r[1]) if r[1] else None,
                     "kilde": r[2], "kildeversjon": r[3],
                     "navn_registrert": r[4], "organisasjonsform": r[5],
                     "registerstatus": r[6], "konkurs": r[7],
                     "under_tvangsavvikling": r[8],
                     "gjelder_fra": r[9].isoformat(),
                     "registrert": r[10].isoformat(),
                     "registrert_av": r[11]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def oppslagslogg_endepunkt(tjeneste, request):
    """GET /v1/motpart/{motpart_id}/oppslag (okonomi:read).

    LOGGEN ER TENANTENS, IKKE BARE VÅR. Spørsmålet «hvilke
    organisasjonsnumre har dere sendt ut, når, mot hvilken vert og med
    hvilken hjemmel» skal kunne besvares av den som eier dataene, uten
    å måtte spørre oss. Det er hele grunnlaget klyngens unntak hviler
    på — et unntak ingen kan etterprøve er ikke et unntak, det er et
    løfte.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        mid = _sti_uuid(request, "motpart_id", rid)
        rader = conn.execute("SELECT * FROM m48_oppslagene(%s,%s)",
                             (auth.tenant, mid)).fetchall()
        svar = {"motpart_id": str(mid), "request_id": rid,
                "oppslag": [
                    {"oppslag_id": str(r[0]),
                     "organisasjonsnummer": r[1], "vert": r[2],
                     "formaal": r[3], "hjemmel": r[4],
                     "svarstatus": r[5], "svar_sha256": r[6],
                     "reservert": r[7].isoformat(),
                     "reservert_av": r[8],
                     "fullfort": r[9].isoformat() if r[9] else None}
                    for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/motpart/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m48_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid, "funn": [
            {"motpart_id": str(r[0]), "organisasjonsnummer": r[1],
             "navn_oppgitt": r[2], "funntype": r[3],
             "over_grense": r[4], "siste_registerstatus": r[5],
             "siste_forslag_ore": r[6], "kravversjon": r[7],
             "forst_sett": r[8].isoformat(),
             "sist_sett_sveip": r[9].isoformat(), "apen": r[10],
             "lukket_ts": r[11].isoformat() if r[11] else None}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen de fire vanlige skriveveiene deler.

    Oppslaget bruker den IKKE — se `oppslag_endepunkt`.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        sql, args, svar, felt = bygg(tenant, bid, nokkel, kropp, rid,
                                     request)
        try:
            ut = conn.execute(sql, args).fetchone()[0]
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        # `felt is None` merker VOID-dørene presist: psycopg gir `''`
        # for VOID, ikke None (111s lærdom).
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def krav_endepunkt(tjeneste, request):
    """POST /v1/motpart/krav (bestilling:opprett, idem).

    POLICYEN ER TENANTENS — invarianten `kredittpolicy_hardkodet`.
    Ferskhetsvinduet særlig: en tenant som handler med byggebransjen
    vil ha kortere vindu enn en som selger abonnement, og en konstant
    i koden ville vært nøyaktig den fullmakten invarianten forbyr.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        ferskhet = _heltall(kropp, "oppslag_ferskhet_timer", rid,
                            *KRAVGRENSER["oppslag_ferskhet_timer"])
        gyldig = _heltall(kropp, "vurdering_gyldig_dogn", rid,
                          *KRAVGRENSER["vurdering_gyldig_dogn"])
        uvurdert = _heltall(kropp, "uvurdert_dogn", rid,
                            *KRAVGRENSER["uvurdert_dogn"])
        tak = _ore(kropp, "maks_forslag_ore", rid)
        grunnlag = _grunnlagsliste(kropp, "godkjente_grunnlag", rid)
        return ("SELECT m48_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, ferskhet, gyldig, uvurdert, tak, grunnlag,
                 bid),
                {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_motpart_endepunkt(tjeneste, request):
    """POST /v1/motpart (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        orgnr = _orgnr(kropp, "organisasjonsnummer", rid)
        navn = _tekst(kropp, "navn_oppgitt", rid, MAKS_NAVN)
        mid = _utled("motpart", tenant, nokkel)
        return ("SELECT m48_registrer_motpart(%s,%s,%s,%s,%s)",
                (tenant, mid, orgnr, navn, bid),
                {"motpart_id": str(mid)}, None)
    return _skriv(tjeneste, request, bygg)


def vurdering_endepunkt(tjeneste, request):
    """POST /v1/motpart/versjon/{versjon_id}/vurdering.

    ET FORSLAG, IKKE EN GRENSE. Endepunktet heter `vurdering` og ikke
    `kredittgrense` fordi det ikke finnes noen kredittgrense å sette:
    116 har ingen kolonne for den. Spesifikasjonens vakt — «setter
    aldri kredittgrensen selv» — er oppfylt av datamodellen, ikke av
    en sjekk her.

    POLICYVERSJONEN SENDES IKKE INN. Døra leser den i basen, fordi en
    kaller som fikk oppgi den kunne oppgitt en annen enn den som
    faktisk gjaldt — og da ville `vurdering_uten_policyversjon` vært
    en port man kunne gå utenom ved å lyve.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        vid = _sti_uuid(request, "versjon_id", rid)
        grunnlag = _valg(kropp, "grunnlag", rid, GRUNNLAG)
        belop = _ore(kropp, "foreslatt_grense_ore", rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid,
                             MAKS_BEGRUNNELSE)
        if len(begrunnelse) < 8:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        uid = _utled("vurdering", tenant, nokkel)
        return ("SELECT m48_registrer_vurdering(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, uid, vid, grunnlag, belop, begrunnelse, bid),
                {"vurdering_id": str(uid)}, "policyversjon")
    return _skriv(tjeneste, request, bygg)


def deaktiver_endepunkt(tjeneste, request):
    """POST /v1/motpart/{motpart_id}/deaktiver.

    HISTORIKKEN BLIR STÅENDE. «Vi handler ikke med denne lenger» er
    ikke «dette skjedde aldri».
    """
    def bygg(tenant, bid, _nokkel, _kropp, rid, request):
        mid = _sti_uuid(request, "motpart_id", rid)
        return ("SELECT m48_deaktiver_motpart(%s,%s,%s)",
                (tenant, mid, bid), {"motpart_id": str(mid)}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/motpart/{motpart_id}/funn/lukk.

    KREVER ET NOTAT. Et funn som lukkes uten at noen sier hvorfor, er
    et funn som ble gjemt.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        mid = _sti_uuid(request, "motpart_id", rid)
        funntype = _valg(kropp, "funntype", rid, FUNNTYPER)
        notat = _tekst(kropp, "notat", rid, MAKS_BEGRUNNELSE)
        if len(notat) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m48_lukk_funn(%s,%s,%s,%s,%s)",
                (tenant, mid, funntype, notat, bid),
                {"motpart_id": str(mid), "funntype": funntype}, None)
    return _skriv(tjeneste, request, bygg)


def oppslag_endepunkt(tjeneste, request):
    """POST /v1/motpart/{motpart_id}/oppslag (bestilling:opprett, idem).

    MODULENS ENE UTGÅENDE FORESPØRSEL, og det eneste endepunktet som
    ikke går gjennom `_skriv`. Grunnen er ikke stilistisk.

    `_skriv` gjør ETT dørkall og committer. Her er det tre ting som må
    skje i riktig rekkefølge, og TO av dem er transaksjoner:

      A. RESERVER, OG COMMIT. Døra sjekker ferskhetsvinduet og skriver
         raden. Committen er ikke valgfri: gjorde vi alt i én
         transaksjon, ville en krasj under forespørselen rullet
         reservasjonen tilbake — og da hadde forespørselen gått ut av
         huset uten at det fantes en rad som sa det. Doktrinen sier at
         den unødvendige forespørselen ER skaden; en skade vi sletter
         loggen for, kan ingen telle.

      B. FORESPØRSELEN, utenfor enhver transaksjon. Over den
         IP-pinnede ssrf-transporten: ingen redirects, korte timeouts,
         256 KiB-tak, ingen hemmeligheter.

      C. FULLFØR, og registrer versjonen hvis det ble et treff.

    DØR PROSESSEN MELLOM A OG C, BLIR RESERVASJONEN STÅENDE. Det er
    riktig utfall og ikke et hull: sveipen finner den som
    `oppslag_uten_svar` etter en time og setter den til `forlatt`
    etter seks. Et oppslag vi ikke vet utfallet av, skal se ut som
    nettopp det.

    VERTEN LESES FRA BASEN og sendes til BEGGE sider — døra som
    validerer den, og klienten som bruker den. Da kan de to aldri mene
    forskjellige ting, og porten `oppslag_mot_uregistrert_vert` er en
    regel og ikke en påstand om Python-kode.

    SP-2: `oppslag_id` utledes av Idempotency-Key-en. Det er strengt
    nødvendig her og ikke bare pent — en gjentatt POST må ikke bli to
    utgående forespørsler. Er nøkkelen sett før, kolliderer INSERT-en
    på primærnøkkelen og svaret blir `idempotenskonflikt`.
    """
    from . import foretaksregister as fr
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        mid = _sti_uuid(request, "motpart_id", rid)
        formaal = _valg(kropp, "formaal", rid, FORMAAL)
        hjemmel = _tekst(kropp, "hjemmel", rid, MAKS_HJEMMEL)
        if len(hjemmel) < 8:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        oid = _utled("oppslag", tenant, nokkel)

        # --- A: reserver, og commit før noe går ut. ---
        try:
            vert = conn.execute(
                "SELECT m48_registrert_vert()").fetchone()[0]
            # Døra gir organisasjonsnummeret tilbake — den leste det
            # under låsen, så kalleren slipper en rundtur som kunne
            # sett en annen verdi enn den reservasjonen ble skrevet på.
            rad = conn.execute(
                "SELECT organisasjonsnummer FROM"
                " m48_reserver_oppslag(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, oid, mid, vert, formaal, hjemmel,
                 bid)).fetchone()
            orgnr = rad[0]
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()

        # --- B: forespørselen. Ingen transaksjon åpen. ---
        lest = datetime.date.today().isoformat()
        foretak = None
        try:
            foretak = fr.hent(vert, orgnr, lest)
            svarstatus = "treff" if foretak else "ikke_funnet"
        except fr.OppslagFeil:
            # FEILEN LOGGES SOM ET UTFALL, IKKE SOM EN UNNTAKSTILSTAND.
            # Forespørselen gikk ut; det skal telles. Sveipen finner en
            # motpart vi har prøvd for mange ganger.
            svarstatus = "feil"

        # --- C: fullfør, og bygg versjonen hvis det ble et treff. ---
        try:
            conn.execute(
                "SELECT m48_fullfor_oppslag(%s,%s,%s,%s,%s)",
                (tenant, oid, svarstatus,
                 foretak.raa_sha256 if foretak else None, bid))
            svar = {"oppslag_id": str(oid), "vert": vert,
                    "svarstatus": svarstatus, "versjon_id": None}
            if foretak is not None:
                versjon_id = _utled("versjon", tenant, nokkel)
                conn.execute(
                    "SELECT m48_registrer_versjon("
                    "%s,%s,%s,%s,'foretaksregister',%s,%s,%s,%s,%s,"
                    "%s,%s,%s)",
                    (tenant, versjon_id, mid, oid,
                     foretak.kildeversjon, foretak.navn,
                     foretak.organisasjonsform,
                     foretak.registerstatus, foretak.konkurs,
                     foretak.under_tvangsavvikling,
                     datetime.date.today(), bid))
                svar["versjon_id"] = str(versjon_id)
                svar["navn_registrert"] = foretak.navn
                svar["registerstatus"] = foretak.registerstatus
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)
