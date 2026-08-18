"""Selvbetjent domeneverifisering (039, eiers krav 18/8).

Kundens egen flate for målautorisasjon: se domenene sine, legge til et
nytt (challenge utstedes, TXT-verdien vises ÉN gang), og følge statusen
til DOMENER-arbeideren har funnet beviset i DNS og
`verifiser_domenekontroll` har gjort resten.

Snittet er bevisst SMALT:
  * API-et kan bare UTSTEDE (skape en `ventende` rad + hash) — aldri
    bekrefte: det genererte tokenet selv og kunne ellers ha «bevist» det
    uten at noen DNS-sone noensinne bar det. Bekreftelsen tilhører
    arbeideren (039).
  * Scope `bestilling:opprett` — domeneregisteret er nøyaktig porten
    bestillingsveien håndhever, og den som kan bestille kontroller er
    den som trenger å autorisere mål. Ingen ny rolle for én flate.
  * Tokenet lagres ALDRI (016: kun sha256); svaret er eneste visning.
"""
from __future__ import annotations

import hashlib
import json
import secrets

import psycopg
from starlette.requests import Request
from starlette.responses import Response

from .bestilling import DOMENE_GYLDIG_SQL, _HOSTNAME

#: Challenge-tokenets form: 32 byte entropi, hex — enkel å lime inn i en
#: sonefil, umulig å gjette, og trimmes robust av bekreftelsens btrim.
_TOKENBYTES = 32

#: Underetiketten utfordrings-TXT-en skal ligge på (Codex P1). DUPLISERT, ikke
#: importert: `drift` ligger ved SIDEN av `platform/core` og er ikke på
#: API-ens sti — samme grense som holder m37/ ute av api/. Invarianten holdes
#: derfor av en PORT i stedet for av et import: `test_domene_selvbetjening`
#: krever at `txt_navn` i dette svaret er nøyaktig
#: `domenerevalidering.utfordringsnavn(hostname)`. Endres den ene uten den
#: andre, faller porten — og det er hele poenget, for i drift ville uenigheten
#: bare vist seg som domener som aldri ble verifisert.
_UTFORDRINGSPREFIKS = "_disponit-challenge"

#: Lengste vertsnavnet en utfordring kan utstedes for (Codex P2). DNS-navn er
#: ≤ 253 tegn, og grensen gjelder navnet som faktisk slås opp — altså
#: utfordringsnavnet, som er `len(prefiks) + 1` tegn lengre enn vertsnavnet.
#: Et vertsnavn på 234–253 tegn er selv fullt lovlig og lagres av basen (018
#: gjerder på 253), men oppskriften for det navnet er umulig å følge: kunden
#: kan ikke publisere navnet, og arbeiderens oppslag kan bare feile. Uten
#: dette svarte utstedelsen 201 med en TXT-instruks som så riktig ut, og
#: domenet sto uverifisert til utfordringen utløp — hver gang på nytt.
#:
#: Regnet av prefikset, ikke skrevet som 233: byttes prefikset, flytter
#: grensen seg med det. Speiler `domenerevalidering.MAKS_UTFORDRET_VERTSNAVN`,
#: og porten som holder de to formene like er den samme som holder prefikset.
_MAKS_UTFORDRET_VERTSNAVN = 253 - len(_UTFORDRINGSPREFIKS) - 1


def _rader(conn, tenant: str) -> list[dict]:
    # `gyldig` regnes av BASEN, med `DOMENE_GYLDIG_SQL` (Codex P2) — samme
    # tekst bestillingsporten stiller sitt spørsmål med, og den er mekanisk
    # krysset mot `v_domeneautorisasjon.gyldig` av
    # `test_domenepredikatet_speiler_visningen`. Uten den svarte listen bare
    # `status`, og `status` LYVER med vilje: basen lar en rad stå `verifisert`
    # når `utloper` har passert eller den daglige revalideringen har vært
    # borte i mer enn 72 timer — det er `v_domeneautorisasjon` som avgjør, og
    # egress og bestillingsveien avviser da domenet. Kunden så «Verifisert» og
    # fikk `bestilling_hostname_uverifisert`.
    #
    # Regelen dupliseres IKKE i klienten: en tredje kopi ville kunnet gli fra
    # de to andre, og en flate som lyver om autorisasjon er nettopp det denne
    # runden retter. `coalesce(..., false)` fordi predikatet er NULL for en
    # `verifisert` rad uten vindu — fail-closed, som i porten.
    rader = conn.execute(
        "SELECT hostname, status, wildcard, verifisert_ts, utloper,"
        " siste_vellykkede_revalidering, challenge_utstedt,"
        " challenge_utloper, coalesce(" + DOMENE_GYLDIG_SQL + ", false)"
        " FROM domenekontroll WHERE tenant=%s"
        " ORDER BY hostname", (tenant,)).fetchall()
    ut = []
    for (host, status, wildcard, vts, utl, srv, cu, cul, gyldig) in rader:
        ut.append({
            "hostname": host, "status": status, "wildcard": wildcard,
            "gyldig": bool(gyldig),
            "verifisert_ts": vts.isoformat() if vts else None,
            "utloper": utl.isoformat() if utl else None,
            "siste_vellykkede_revalidering":
                srv.isoformat() if srv else None,
            "challenge_utstedt": cu.isoformat() if cu else None,
            "challenge_utloper": cul.isoformat() if cul else None,
        })
    return ut


def liste_endepunkt(tjeneste, request: Request) -> Response:
    """GET /v1/domener — tenantens egne domener med status.

    Lesende rute, lese-scope (`decisions:read` — pr008-invarianten: en
    GET bærer aldri et mutasjonsscope) og UTEN CSRF — dobbel innsending
    verner skrivinger. Å SE listen er lesing av egen tilstand; å ENDRE
    den (POST) krever bestilling:opprett + CSRF."""
    from . import kjerne
    from .app import _autentiser, _feilsvar, _rid, kanonisk_json
    from .policyadmin_http import _gjenopprett_kontekst
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               "decisions:read")
        except kjerne.Feilsvar as f:
            return _feilsvar(f.kode, rid)
        tenant = auth.tenant
        bid = auth.token_id.split("sesjon:", 1)[-1]
        conn.rollback()
        _gjenopprett_kontekst(conn, tenant, bid, rid)
        rader = _rader(conn, tenant)
        conn.rollback()
        return kanonisk_json({"domener": rader, "request_id": rid}, 200,
                             {"x-request-id": rid})
    except psycopg.Error as e:
        conn.rollback()
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                               feiltype=type(e).__name__)
        return _feilsvar("db_utilgjengelig", rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)


def utsted_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/domener {hostname} → challenge. TXT-verdien vises ÉN gang.

    Reutstedelse er gratis og idempotent på radnivå (hash/vindu oppdateres)
    — men gir selvsagt en NY verdi, og da er det den nye som gjelder.

    En rad som står `utlopt` — eller `tilbakekalt` uten motpart, altså av en
    operatør — KØES samtidig tilbake til `ventende` (039), slik at arbeideren
    faktisk ser utfordringen. En kandidat M-37 AVVISTE får også utstede, men
    raden blir stående `tilbakekalt` med motparten: arbeideren tar den likevel,
    og beviset fører til en NY avklaringsgenerasjon, aldri til `verifisert`.

    En `verifisert` rad som har passert `utloper` (90 døgn) skrives ned til
    `utlopt` av samme funksjon og køes derfra: det er FORNYELSEN, og uten den
    var 90-dagersvinduet en blindvei — statusen sto `verifisert`, autorisasjonen
    var ugyldig, og en ny utstedelse ble aldri sett på av arbeideren.

    Står raden i en pågående M-37-avklaring, svarer basen nei og klienten får
    409 `domene_challenge_avvist` — aldri 201 med en TXT-oppskrift ingen
    arbeider kommer til å lese.
    """
    from .app import _feilsvar, _rid, kanonisk_json
    from .policyadmin_http import _Avbrudd, _browserkontekst
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                           "bestilling:opprett")
        except _Avbrudd as a:
            return a.respons
        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            data = json.loads(raa.decode("utf-8"))
        except (ValueError, RecursionError):
            # `json.loads` er REKURSIV (Codex P2). Et syntaktisk gyldig, dypt
            # nøstet dokument på noen få kilobyte ligger godt under
            # kroppsgrensen og treffer likevel rekursjonsgrensen —
            # RecursionError er en RuntimeError, ikke en ValueError, så
            # `except ValueError` alene slapp klientinput ut som generisk 500
            # i stedet for det dokumenterte `request_feilformet`. DYBDE er
            # klientinput på lik linje med syntaks; naboendepunktet
            # (`bestilling`) fanger begge av nøyaktig samme grunn, og denne
            # parseren skal ikke være unntaket.
            return _feilsvar("request_feilformet", rid)
        if not isinstance(data, dict) or set(data) - {"hostname"}:
            return _feilsvar("request_feilformet", rid)
        hostname = data.get("hostname")
        if not isinstance(hostname, str):
            return _feilsvar("request_feilformet", rid)
        hostname = hostname.strip().lower()
        # Lengden måles på UTFORDRINGSNAVNET, ikke på vertsnavnet (Codex P2):
        # et vertsnavn over `_MAKS_UTFORDRET_VERTSNAVN` er selv lovlig, men
        # utfordringen for det får ikke plass innenfor DNS-navnegrensen. Den
        # avvises her, FØR utstedelsen, som feilformet input — å svare 201 med
        # en oppskrift ingen kan følge er verre enn å si nei med det samme.
        if (not _HOSTNAME.match(hostname)
                or len(hostname) > _MAKS_UTFORDRET_VERTSNAVN):
            return _feilsvar("request_feilformet", rid)

        token = secrets.token_hex(_TOKENBYTES)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            # Den GUARDEDE formen (039), aldri 016s rå `utsted_challenge`:
            # den stoler på `p_tenant`, og gitt til den delte runtime-rollen
            # var den et kryss-tenant skriveprimitiv — bytt hashen på en
            # annen tenants `ventende` rad, og DNS-beviset holdes mot ditt
            # token. Innpakningen binder `p_tenant` til den tenantkonteksten
            # `_browserkontekst` nettopp satte (`krev_tenantkontekst`, 038).
            conn.execute(
                "SELECT utsted_challenge_selvbetjent(%s,%s,false,%s,%s)",
                (tenant, hostname, token_hash, f"bruker:{bid}"))
            conn.commit()
        except psycopg.errors.InvalidParameterValue as e:
            # DEN FORVENTEDE NEIEN, og bare den (Codex P2). Funksjonens egne
            # porter — åpen M-37-avklaring, ukanonisk hostname — reiser
            # `invalid_parameter_value`. Det er en TILSTAND hos kunden, og 409
            # er riktig svar.
            conn.rollback()
            tjeneste.logg.hendelse("domene_challenge_avvist", rid, tenant,
                                   art="sikkerhet",
                                   feiltype=type(e).__name__)
            return _feilsvar("domene_challenge_avvist", rid)
        except psycopg.Error as e:
            # ALT ANNET er drift, ikke kundens tilstand: funksjonen er ikke
            # utrullet (UndefinedFunction), grantet mangler
            # (InsufficientPrivilege), basen er nede. Fanget som 409 fortalte
            # vi kunden at DOMENET hennes forbød en utfordring — mens en
            # utrullingsfeil som rammer ALLE lå i loggen som en
            # sikkerhetsavvisning, altså det ene stedet ingen leter etter en
            # nedetid.
            conn.rollback()
            tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                                   feiltype=type(e).__name__)
            return _feilsvar("db_utilgjengelig", rid)
        tjeneste.logg.hendelse("domene_challenge_utstedt", rid, tenant,
                               hostname=hostname)
        return kanonisk_json({
            "hostname": hostname,
            # OPPSKRIFTEN MÅ KUNNE FØLGES (Codex P1). Navnet lå før på selve
            # vertsnavnet, og for et typisk `www.dittfirma.no` er det et navn
            # kunden ikke KAN legge en TXT-post på: eieren av et CNAME kan
            # ikke ha andre poster ved siden av seg, og oppslaget følger
            # aliaset til leverandørens sone. Selvbetjeningen ber alltid om
            # nøyaktig vertsnavnet (`wildcard=false`), så apex var ingen vei
            # rundt — slike nettsteder sto permanent uverifisert med en
            # oppskrift som så riktig ut.
            #
            # Underetiketten ligger i kundens EGEN sone også når vertsnavnet
            # er et alias. Navnet bygges av `UTFORDRINGSPREFIKS`-formen, som
            # arbeideren eier og en port her holder dette svaret mot: leter
            # arbeideren ett sted og kunden publiserer et annet, står domenet
            # uverifisert uten at noe sier hvorfor.
            "txt_navn": f"{_UTFORDRINGSPREFIKS}.{hostname}",
            "txt_verdi": token,
            "gyldig_dager": 7,
            "request_id": rid,
        }, 201, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)
