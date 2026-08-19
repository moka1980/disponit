"""PR-013 CP6 — HTTP-endepunktene for policyadministrasjon.

Tynne endepunkter: form + auth (`policy:write`/`policy:activate`, ADSKILTE) +
CSRF (browsermutasjon, dobbel-innsending som PR-012), så delegeres ALT til
`policyadmin`-orkestreringen i én eiertransaksjon. Muterende ruter krever en
browsersesjon (samme carve-out som unntaksbehandlingen); lesende ruter er rent
`policy:read`.

`Idempotency-Key` er PÅKREVD kun for attesteringen (den kan aktivere en policy
og må derfor være replay-sikker); utkast-CRUD er naturlig idempotent på
`utkastversjon`/utkast_id.
"""
from __future__ import annotations

import hashlib
import json

import psycopg

from . import policyadmin

#: Feilkode → HTTP. Ukjent kode → 409 (tilstandskonflikt), som PR-012.
_FEIL_HTTP = {
    "utkast_ukjent": 404, "utkast_feilformet": 400,
    "utkast_ulovlig_tilstand": 409, "utkastversjon_utdatert": 409,
    "utkast_ikke_validert": 409, "runde_allerede_aapen": 409,
    "ingen_aktiv_runde": 409, "runde_utlopt": 409, "diff_utdatert": 409,
    "allerede_attestert": 409, "rebasering_kreves": 409,
    "semantikk_endret": 409, "base_mangler": 409, "base_korrupt": 409,
    "aktiv_peker_usynk": 409,
    "versjon_i_bruk": 409, "versjon_mangler": 409,
    "policy_id_avvik": 409, "status_ikke_produksjon": 409,
    "policy_i_bruk": 409, "policy_ukjent": 404,
    # Ressursen er BORTE, ikke i konflikt. Koden brukes av begge veiene
    # som slår opp en versjon gjennom eier-definerne (rullbakk gjennom
    # `policyversjon_kilde`, diff gjennom `policyversjon_innhold`);
    # manglet den her, falt den ene av dem gjennom til
    # standardsvaret 409 (Codex P2). Standarden skal aldri kunne gjøre et
    # fravær om til en konflikt.
    "ikke_funnet": 404,
    # Kildeversjonen FINNES, men har aldri vært i kraft: den kan leses og
    # diffes, den er bare ingen rullbakk-kilde (`policyversjon_kilde`).
    # Egen kode, og 409 — ikke 404, som ville sagt at versjonen er borte, og
    # ikke 400, som ville sagt at forespørselen er feilformet. Den er
    # velformet; det er tilstanden til raden den peker på som er svaret.
    "rullbakk_kilde_uaktivert": 409,
    # Den aktive policyen er ikke lenger den klienten så da den ba om
    # slettingen (optimistisk lås, som `utkastversjon_utdatert`): 409, og
    # flaten laster på nytt.
    "policy_endret": 409,
    # 400, ikke 409: en `policy_id` som bryter formen er en feil i
    # FORESPØRSELEN, ikke en tilstandskonflikt. Ingenting i basen kan endre seg
    # slik at det samme kallet plutselig lykkes.
    "policy_id_ugyldig": 400,
    # Samme kategori: id-en har riktig FORM, men er for stor til å dele
    # registerets primærnøkkel med en versjon. Egen kode fordi eier ellers fikk
    # `utkast_feilformet` og ble bedt om å reparere dokumentet sitt i stedet for
    # å forkorte id-en (Codex P3).
    "policy_id_for_stor": 400,
    "dokument_avvik": 409,
    "idempotenskonflikt": 409, "sikkerhet": 409,
    "scope_mangler": 403, "mangler_medlemskap": 403,
    "token_ugyldig": 401, "rate_grense": 429,
    "dobbel_principal": 400, "sesjon_ugyldig": 401, "csrf_ugyldig": 403,
    "request_feilformet": 400, "idempotensnokkel_mangler": 400,
    # 503, ikke 422: validatoren kunne ikke KJØRE (skjemafilen mangler i en
    # halvlandet utrulling, f.eks.). Det er vår feil og den er reparerbar — å
    # svare «utkastet er ugyldig» ville sendt eier for å rette noe vi ikke har
    # målt (Codex P2).
    "db_utilgjengelig": 503, "valideringsfeil_intern": 503,
    "policy_ugyldig": 422,
    # Innholdet er ugyldig (innføringskontrakten), ikke tilstanden: 422 som
    # `policy_ugyldig` — eier må rette utkastet, ikke prøve igjen.
    "utkast_ugyldig": 422,
}


class _Avbrudd(Exception):
    """Bærer et ferdig HTTP-svar ut av auth/CSRF-hjelperen."""

    def __init__(self, respons):
        self.respons = respons


def _feil(kode: str, rid: str, http: int | None = None):
    from starlette.responses import JSONResponse
    return JSONResponse({"feil": kode, "request_id": rid},
                        status_code=http or _FEIL_HTTP.get(kode, 409),
                        headers={"x-request-id": rid})


def _ok(res: dict, rid: str, http: int = 200):
    from starlette.responses import JSONResponse
    return JSONResponse(res, status_code=http, headers={"x-request-id": rid})


def _ok_lagret(conn, res: dict, rid: str, http: int = 200):
    """`_ok`, men transaksjonen committes FØRST (Codex P1).

    Poolen gir ALDRI en forbindelse tilbake med en åpen transaksjon:
    `Tilkoblingspool.gi_tilbake` ruller ubetinget tilbake, nettopp for at
    SET LOCAL-verdier og låser ikke skal følge med inn i neste tenants
    forespørsel. Et 200-svar er derfor ikke i seg selv et løfte om at noe ble
    lagret — uten commit blir svaret sendt og skrivingen kastet i samme
    åndedrag. Det er den verste feilklassen vi kan lage: en flate som viser
    lagret tilstand som ikke finnes, og en bruker som skrur av e-postvarsler og
    fortsetter å få dem.

    Policyadmin-veiene rammes ikke: hver av dem ender i `policyadmin._fullfor`,
    som committer sammen med idempotensraden. Varselmutasjonene har ingen
    idempotensrad å committe med — de er naturlig idempotente («lest» og «kanal»
    er tilstander, ikke hendelser — å sette dem to ganger er samme svar) — så
    commit-en må stå her. Den ligger i svarhjelperen og ikke i tjenesten fordi
    det er endepunktet som eier forbindelsen; `varsel`-funksjonene kalles også
    fra aktiveringsflyten, midt inne i en transaksjon de ikke får røre.
    """
    conn.commit()
    return _ok(res, rid, http)


def _krev_idem(request, rid: str) -> str:
    """`Idempotency-Key` er PÅKREVD på ALLE skriveruter (spec, Codex P1 R3).
    Reiser `_Avbrudd` med 400 hvis den mangler."""
    idem = request.headers.get("idempotency-key")
    if not idem or not idem.strip():
        raise _Avbrudd(_feil("idempotensnokkel_mangler", rid))
    return idem.strip()


def _input_hash(*deler) -> str:
    return hashlib.sha256("\x1f".join(str(d) for d in deler)
                          .encode("utf-8")).hexdigest()


def opprett_input_hash(tenant, bid, policy_id, innhold, rollback_av, idem) -> str:
    """Idempotens-inputhash for utkastopprettelse. `rollback_av_versjon` INNGÅR
    (Codex R2/R3): en rullbakk og en ordinær opprettelse med samme nøkkel er
    ULIKE operasjoner og MÅ gi konflikt, ikke replay. Egen funksjon så bindingen
    er direkte testbar. `rollback_av` JSON-kodes så `null` og `""` gir ULIKE
    representasjoner (Codex R4: `str(None)`/`str("")` kolliderte ikke lenger).

    For en RULLBAKK er `innhold` ikke bestillingen, men klientens PÅSTAND om
    hva kildeversjonen inneholder (047, Codex P2). Det utkastet faktisk får,
    er serverens egen kopi av versjonen — den hentes etterpå og inngår derfor
    ikke her. Poenget er at hashen da kan REGNES UT FØR kildeversjonen slås
    opp, og at en retry dermed kan replaye et alt opprettet utkast selv om
    kilden er arkivert i mellomtiden. Med det HENTEDE innholdet i hashen var
    den rekkefølgen umulig: nøkkelen krevde innholdet, innholdet krevde
    kilden, og en arkivert kilde ga 404 på en operasjon som for lengst hadde
    lyktes.

    Men påstanden selv BINDES (Codex R4). «Rull tilbake til N» og «rull
    tilbake til N, og jeg påstår at N inneholder X» er ulike bestillinger:
    den andre ber i tillegg om en kontroll. Uten bindingen kunne en retry med
    samme nøkkel og en LØGN om innholdet replaye det gamle 201-svaret uten at
    påstanden noen gang ble målt mot kilden — samme nøkkel, annen kropp, og
    verken 400 eller konflikt. Nå gir en endret påstand ulik hash, altså
    `idempotenskonflikt`, og en uendret påstand replayer som før. At påstanden
    er klientens rå felt (ikke det hentede innholdet) er nettopp det som lar
    hashen fortsatt regnes ut før oppslaget."""
    return _input_hash(tenant, bid, "opprett", policy_id,
                       ("rullbakk:" + json.dumps(innhold, sort_keys=True))
                       if rollback_av is not None
                       else json.dumps(innhold, sort_keys=True),
                       json.dumps(rollback_av), idem)


def _kropp(request) -> dict:
    raa = request.scope.get("state", {}).get("kropp", b"")
    try:
        body = json.loads(raa.decode("utf-8"))
    except Exception:
        raise _Avbrudd(None)
    if not isinstance(body, dict):
        raise _Avbrudd(None)
    return body


def _gjenopprett_kontekst(conn, tenant: str, bid: str, rid: str) -> None:
    """Sett `disponit.*` på nytt etter at auth rullet tilbake (Codex P1).

    BEGGE auth-hjelperne under må `conn.rollback()`: sesjonsoppslaget kjører
    som en egen liten transaksjon, og den skal ikke bli hengende åpen inn i
    selve arbeidet. Men `sett_kontekst` er `SET LOCAL` — den dør med nøyaktig
    den rollbacken. Etter auth er `disponit.tenant` altså UNSET, og med FORCE
    RLS på (migrasjon 026 for `varsel`/`varselvalg`) betyr unset «ingen rader»:
    innboksen blir tom, `merk_lest` treffer null rader, og en `INSERT` feiler
    WITH CHECK. Fail-closed — man får ikke feil tenants data, man får ingen.

    `policyadmin`-tjenestene skjuler dette ved at hver av dem setter konteksten
    selv med én gang. Varselveien kaller tabellene direkte og hadde ingen slik
    linje, og det er ikke tilfeldig: en invariant som hver enkelt kaller må
    huske på, blir før eller siden glemt. Derfor settes den her, i den samme
    funksjonen som river den ned. Tjenestenes egne kall blir da en ufarlig
    gjentakelse av nøyaktig de samme tre verdiene.
    """
    from db.pg import sett_kontekst
    sett_kontekst(conn, tenant, bid, rid)


def _browserkontekst(tjeneste, request, conn, rid: str, scope: str):
    """auth (browsersesjon, gitt scope) + CSRF. -> (tenant, bid). Reiser
    `_Avbrudd` med ferdig feilsvar. Speiler `handling_endepunkt` (PR-012).

    Ved retur er `disponit.tenant/aktor/request_id` satt for den nye
    transaksjonen — se `_gjenopprett_kontekst`."""
    from . import kjerne
    from . import sesjon as sesjonmodul
    from .app import _autentiser

    try:
        auth = _autentiser(tjeneste, request, conn, rid, scope)
    except kjerne.Feilsvar as f:
        raise _Avbrudd(_feil(f.kode, rid))
    sesjon_cookie = request.cookies.get(sesjonmodul.C_SESJON)
    rad = conn.execute("SELECT csrf_hash FROM slaa_opp_sesjon(%s)",
                       (sesjonmodul._hash(sesjon_cookie),)).fetchone() \
        if sesjon_cookie else None
    conn.rollback()
    if rad is None or not sesjonmodul.csrf_matcher(rad[0], request):
        tjeneste.logg.hendelse("csrf_ugyldig", rid)
        raise _Avbrudd(_feil("csrf_ugyldig", rid))
    tenant = auth.tenant
    bid = auth.token_id.split("sesjon:", 1)[-1]
    _gjenopprett_kontekst(conn, tenant, bid, rid)
    return tenant, bid


def _leseauth(tjeneste, request, conn, rid: str):
    """auth for en lesende rute (`policy:read`, ingen CSRF). -> (tenant, bid).

    Setter konteksten på nytt etter rollbacken, som `_browserkontekst`."""
    from . import kjerne
    from .app import _autentiser
    try:
        auth = _autentiser(tjeneste, request, conn, rid, "policy:read")
    except kjerne.Feilsvar as f:
        raise _Avbrudd(_feil(f.kode, rid))
    bid = auth.token_id.split("sesjon:", 1)[-1]
    conn.rollback()
    _gjenopprett_kontekst(conn, auth.tenant, bid, rid)
    return auth.tenant, bid


def _med_conn(tjeneste, rid: str, fn):
    """Hent en forbindelse, kjør `fn(conn)`, håndter Aktiveringsfeil + drift."""
    from .app import _rid  # noqa: F401  (holder importgrafen lik app.py)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feil("db_utilgjengelig", rid)
    try:
        return fn(conn)
    except _Avbrudd as a:
        conn.rollback()
        return a.respons if a.respons is not None else _feil(
            "request_feilformet", rid)
    except policyadmin.Aktiveringsfeil as g:
        conn.rollback()
        return _feil(g.kode, rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)


# --------------------------------------------------------------------------
# Muterende ruter (browsersesjon + CSRF).
# --------------------------------------------------------------------------

def opprett_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        policy_id = body.get("policy_id")
        innhold = body.get("innhold")
        rollback_av = body.get("rollback_av_versjon")
        # Kilderadens GENERASJON, hentet sammen med innholdet under. Den
        # er opphavet utkastet lagrer (047, Codex P2) — et sekvenstall
        # ingen får igjen, i motsetning til nummeret og innholdet.
        kilde_gen = None
        # 047 (§3, port 22): en rullbakk er en KOPI av versjonen den peker
        # på — serveren henter innholdet selv gjennom eier-defineren, og
        # et klientinnhold som avviker avvises: `rollback_av_versjon = N`
        # med annet innhold enn N ville vært en løgn i lineagen. Uten
        # feltet er kontrakten som før (innhold påkrevd fra klienten).
        ih = None
        if rollback_av is not None:
            if not isinstance(policy_id, str) or not policy_id.strip() \
                    or not isinstance(rollback_av, str):
                return _feil("request_feilformet", rid)
            # REPLAY FØR KILDEOPPSLAG (047, Codex P2). Lyktes det første
            # forsøket og svaret gikk tapt på veien, finnes utkastet — og
            # da skal retryen få id-en tilbake, ikke en 404 fordi den
            # inaktive kildeversjonen er arkivert i mellomtiden. Hashen kan
            # regnes ut her nettopp fordi den ikke inneholder det HENTEDE
            # innholdet; se `opprett_input_hash`.
            #
            # Hashen bindes ÉN gang og gjenbrukes til opprettelsen under
            # (Codex R4). Prøven og raden som lagres må være samme hash —
            # ellers kan en retry med en annen påstand om kildeinnholdet
            # replaye et 201 uten at påstanden noen gang måles mot kilden.
            # Klientens `innhold` inngår derfor her, rått, slik det kom.
            #
            # Ingen `rollback()` når vi faller gjennom: `sett_kontekst` er
            # `SET LOCAL`, og `policyversjon_kilde` under er en definer
            # som KREVER `disponit.tenant`. Å rulle tilbake her ville tatt
            # konteksten med seg og gjort oppslaget til en 403.
            ih = opprett_input_hash(tenant, bid, policy_id, innhold,
                                    rollback_av, idem)
            tilstand, lagret = policyadmin.idempotent_svar(
                conn, tenant, idem, ih)
            if tilstand == "replay":
                conn.rollback()
                return _ok(lagret, rid, 201)
            if tilstand == "konflikt":
                conn.rollback()
                return _feil("idempotenskonflikt", rid)
            try:
                # Innholdet OG generasjonen i ETT oppslag: de to må komme
                # fra samme rad i samme snapshot, ellers kan opphavet peke
                # på en annen generasjon enn kopien (047, Codex P2). Se
                # `policyversjon_kilde`.
                hentet, kilde_gen = conn.execute(
                    "SELECT innhold, generasjon FROM"
                    " policyversjon_kilde(%s, %s, %s)",
                    (tenant, policy_id, rollback_av)).fetchone()
            except psycopg.errors.NoDataFound:
                conn.rollback()
                return _feil("ikke_funnet", rid, 404)
            except psycopg.errors.InvalidParameterValue:
                # Versjonen finnes, men har aldri vært i kraft, og en
                # rullbakk til noe som aldri virket er ingen rullbakk
                # (Codex P2). Flaten tilbyr ikke knappen for slike rader —
                # dette er porten bak den, for kallere som ikke går via
                # flaten og for en visning som har blitt foreldet.
                conn.rollback()
                return _feil("rullbakk_kilde_uaktivert", rid)
            except psycopg.errors.InsufficientPrivilege:
                conn.rollback()
                return _feil("ingen_tilgang", rid)
            conn.rollback()
            if innhold is not None and innhold != hentet:
                return _feil("request_feilformet", rid)
            innhold = hentet
        if not isinstance(policy_id, str) or not policy_id.strip() \
                or not isinstance(innhold, dict):
            return _feil("request_feilformet", rid)
        if ih is None:
            ih = opprett_input_hash(tenant, bid, policy_id, innhold,
                                    rollback_av, idem)
        res = policyadmin.opprett_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid,
            policy_id=policy_id, innhold=innhold,
            idempotency_key=idem, input_hash=ih,
            rollback_av_versjon=rollback_av,
            rollback_av_generasjon=kilde_gen)
        return _ok(res, rid, 201)

    return _med_conn(tjeneste, rid, kjor)


def rediger_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        innhold = body.get("innhold")
        uv = body.get("utkastversjon")
        if not isinstance(innhold, dict) or not isinstance(uv, int) \
                or isinstance(uv, bool):
            return _feil("request_feilformet", rid)
        ih = _input_hash(tenant, bid, "rediger", utkast_id, uv,
                         json.dumps(innhold, sort_keys=True), idem)
        res = policyadmin.rediger_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid,
            utkast_id=utkast_id, forventet_utkastversjon=uv, innhold=innhold,
            idempotency_key=idem, input_hash=ih)
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def valider_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        uv = body.get("utkastversjon")
        if not isinstance(uv, int) or isinstance(uv, bool):
            return _feil("request_feilformet", rid)
        # Versjonen inngår i input-hashen → nøkkelen er bundet til utkastets
        # tilstand (Codex R3).
        ih = _input_hash(tenant, bid, "valider", utkast_id, uv, idem)
        res = policyadmin.valider_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid, utkast_id=utkast_id,
            forventet_utkastversjon=uv, idempotency_key=idem, input_hash=ih)
        if res.get("utfall") == "ugyldig":
            from starlette.responses import JSONResponse
            return JSONResponse({"feil": "policy_ugyldig", "detaljer": res["feil"],
                                 "request_id": rid}, status_code=422,
                                headers={"x-request-id": rid})
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def varsel_liste_endepunkt(tjeneste, request):
    """Mine varsler. `policy:read` — å se at noe venter på deg krever ikke
    fullmakt til å endre noe.

    LESEAUTH, ikke `_browserkontekst` (Codex P2). Ruten er en GET og endrer
    ingenting, så CSRF-vernet hører ikke hjemme her: det finnes for å hindre at
    et annet nettsted får browseren til å UTFØRE noe med brukerens cookie, og en
    liste over hva som venter på deg er ikke noe å utføre. Kravet var heller
    ikke gratis — `hentJson` sender bevisst bare `Accept`, som hver eneste andre
    GET i flaten, så innboksen svarte `403 csrf_ugyldig` på en helt gyldig
    forespørsel. CSRF beholdes på de to POST-rutene, der den faktisk verner noe.
    """
    from .app import _rid
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _leseauth(tjeneste, request, conn, rid)
        from . import varsel as v
        kun = request.query_params.get("uleste") == "1"
        return _ok({"varsler": v.innboks(conn, tenant=tenant, bruker_id=bid,
                                         kun_uleste=kun),
                    "uleste": v.antall_uleste(conn, tenant=tenant,
                                              bruker_id=bid),
                    "kanal": v.hent_kanal(conn, tenant=tenant, bruker_id=bid)},
                   rid)

    return _med_conn(tjeneste, rid, kjor)


def varsel_lest_endepunkt(tjeneste, request):
    """Merk ETT av MINE varsler som lest."""
    from .app import _rid
    rid = _rid(request)
    try:
        vid = int(request.path_params["varsel_id"])
    except (TypeError, ValueError):
        return _feil("request_feilformet", _rid(request))

    def kjor(conn):
        # `policy:read`, ikke `policy:write` (Codex P2): raden er MIN, og
        # bruker-id-en kommer fra økten. Å kvittere ut sitt eget varsel er
        # ikke en policyfullmakt — og etter 044 varsles administratoren som
        # aktiverte planen, en rolle uten `policy:write`. CSRF-vernet står.
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:read")
        from . import varsel as v
        return _ok_lagret(
            conn, {"lest": v.merk_lest(conn, tenant=tenant, bruker_id=bid,
                                       varsel_id=vid)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def varselvalg_endepunkt(tjeneste, request):
    """Valget eier ba om: e-post + portal, eller kun portal."""
    from .app import _rid
    rid = _rid(request)

    def kjor(conn):
        # Kanalvalget er MITT (Codex P2): samme scope som å se innboksen.
        # Krevde det `policy:write`, kunne en mottaker uten den fullmakten
        # — planadministratoren etter 044 — låses inne i `kun_portal`.
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:read")
        from . import varsel as v
        kropp = _kropp(request) or {}
        try:
            satt = v.sett_kanal(conn, tenant=tenant, bruker_id=bid,
                                kanal=kropp.get("kanal"),
                                sprak=kropp.get("sprak"))
        except ValueError:
            return _feil("request_feilformet", rid)
        return _ok_lagret(conn, {"kanal": satt}, rid)

    return _med_conn(tjeneste, rid, kjor)


def slett_policy_endepunkt(tjeneste, request):
    """Angre en feilopprettet policy: slett den som ALDRI har styrt en
    beslutning. Vilkårene håndheves i `slett_ubrukt_policy` (032), idempotensen
    i `policyadmin.slett_policy` — endepunktet binder bare nøkkelen til
    operasjonen og lar `_med_conn` oversette feilkodene.

    Kroppen bærer den aktive policyen klienten SÅ (`versjon` +
    `innholds_hash`), som `forkast`/`valider` bærer `utkastversjon` (Codex P1).
    Begge er PÅKREVD: en kropp uten dem er en sletting uten binding, og det er
    nettopp den formen som kunne rive en policy som ble aktivert etter at siden
    ble lastet. Verdiene er de leseendepunktene ga ut; de sammenlignes ikke
    her, men under policylåsen inne i funksjonen."""
    from .app import _rid
    rid = _rid(request)
    policy_id = request.path_params["policy_id"]

    def kjor(conn):
        from datetime import datetime, timezone
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        versjon = body.get("versjon")
        innholds_hash = body.get("innholds_hash")
        if not isinstance(versjon, str) or not versjon.strip() \
                or not isinstance(innholds_hash, str) \
                or not innholds_hash.strip():
            return _feil("request_feilformet", rid)
        # `policy_id` INNGÅR i hashen: samme nøkkel brukt på en ANNEN policy er
        # en annen operasjon og skal gi konflikt, ikke replay av forrige svar.
        # Den forventede identiteten INNGÅR av samme grunn (som i `valider`):
        # samme nøkkel mot en annen aktiv versjon er en annen operasjon, og
        # skal ikke replaye svaret fra slettingen av den forrige.
        ih = _input_hash(tenant, bid, "slett_policy", policy_id, versjon,
                         innholds_hash, idem)
        res = policyadmin.slett_policy(
            conn, tenant=tenant, aktor=bid, request_id=rid,
            policy_id=policy_id, forventet_versjon=versjon,
            forventet_hash=innholds_hash, idempotency_key=idem, input_hash=ih,
            naa=datetime.now(timezone.utc))
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def forkast_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        from datetime import datetime, timezone
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        uv = body.get("utkastversjon")
        if not isinstance(uv, int) or isinstance(uv, bool):
            return _feil("request_feilformet", rid)
        ih = _input_hash(tenant, bid, "forkast", utkast_id, uv, idem)
        res = policyadmin.forkast_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid, utkast_id=utkast_id,
            forventet_utkastversjon=uv, idempotency_key=idem, input_hash=ih,
            naa=datetime.now(timezone.utc))
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def gjenapne_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        from datetime import datetime, timezone
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:write")
        idem = _krev_idem(request, rid)
        body = _kropp(request)
        uv = body.get("utkastversjon")
        if not isinstance(uv, int) or isinstance(uv, bool):
            return _feil("request_feilformet", rid)
        ih = _input_hash(tenant, bid, "gjenapne", utkast_id, uv, idem)
        res = policyadmin.gjenapne_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid, utkast_id=utkast_id,
            forventet_utkastversjon=uv, idempotency_key=idem, input_hash=ih,
            naa=datetime.now(timezone.utc))
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def apne_runde_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        from datetime import datetime, timezone
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:activate")
        idem = _krev_idem(request, rid)
        ih = _input_hash(tenant, bid, "aapne_runde", utkast_id, idem)
        res = policyadmin.opprett_aktiveringsrunde(
            conn, tenant=tenant, aktor=bid, request_id=rid,
            utkast_id=utkast_id, idempotency_key=idem, input_hash=ih,
            naa=datetime.now(timezone.utc))
        return _ok(res, rid, 201)

    return _med_conn(tjeneste, rid, kjor)


def attester_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        from datetime import datetime, timezone
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:activate")
        body = _kropp(request)
        diff_hash = body.get("diff_hash")
        if not isinstance(diff_hash, str) or not diff_hash:
            return _feil("request_feilformet", rid)
        idem = request.headers.get("idempotency-key")
        if not idem or not idem.strip():
            return _feil("idempotensnokkel_mangler", rid)
        idem = idem.strip()
        input_hash = hashlib.sha256(
            f"{tenant}\x1f{bid}\x1f{utkast_id}\x1f{diff_hash}\x1f{idem}"
            .encode("utf-8")).hexdigest()
        res = policyadmin.attester_aktivering(
            conn, tjeneste.mac_register, tenant=tenant, aktor=bid,
            request_id=rid, utkast_id=utkast_id, forventet_diff_hash=diff_hash,
            idempotency_key=idem, input_hash=input_hash,
            naa=datetime.now(timezone.utc))
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


# --------------------------------------------------------------------------
# Lesende ruter (policy:read, ingen CSRF).
# --------------------------------------------------------------------------

def hent_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)
    utkast_id = request.path_params["utkast_id"]

    def kjor(conn):
        from datetime import datetime, timezone
        tenant, bid = _leseauth(tjeneste, request, conn, rid)
        res = policyadmin.hent_utkast_detalj(
            conn, tenant=tenant, aktor=bid, request_id=rid, utkast_id=utkast_id,
            naa=datetime.now(timezone.utc))
        return _ok(res, rid)

    return _med_conn(tjeneste, rid, kjor)


def maler_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)

    def kjor(conn):
        _leseauth(tjeneste, request, conn, rid)      # policy:read
        return _ok({"maler": policyadmin.hent_maler()}, rid)

    return _med_conn(tjeneste, rid, kjor)


def list_utkast_endepunkt(tjeneste, request):
    from .app import _rid
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _leseauth(tjeneste, request, conn, rid)
        policy_id = request.query_params.get("policy_id")
        res = policyadmin.list_utkast(
            conn, tenant=tenant, aktor=bid, request_id=rid, policy_id=policy_id)
        return _ok({"utkast": res}, rid)

    return _med_conn(tjeneste, rid, kjor)
