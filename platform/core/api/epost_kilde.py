"""M-6 PR-B: kilderegistrering med M365-OAuth (authorization code).

Fire ruter over 088-fundamentet (`epost_kilde`), formet av dommene 31/8
(M365 først, KUN lesende scope) og OIDC-flytens etablerte grenser:

* POST /v1/epost/kilder/start — autentisert (browsersesjon + CSRF,
  `epost:kilde:administrer`, Idempotency-Key). Bygger Microsofts
  authorize-URL med et STATE-TOKEN I KAPABILITETSFORM: MAC-et
  (PR-012-registeret — huset har alt én MAC-maskin, og en ny lages
  ikke), engangs (konsumeres atomisk i `idempotens`-lageret ved
  callback, i et RESERVERT nøkkelrom) og kort TTL (`LOGIN_TX_MIN`,
  OIDC-flytens frist). PKCE-verifieren reiser MED tokenet, men
  tenant-DEK-kryptert — payloaden er lesbar for browseren og Microsoft,
  verifieren er det ikke. Browserbindingen er OIDC-flytens (v4 §1):
  en HttpOnly-cookie satt ved /start, hash-bundet inn i statens payload.

  `oidc_logintransaksjon` kunne IKKE gjenbrukes som state-lager:
  `provider_id` har FK til `oidc_provider`, og M365-kildekonfigen er
  miljøbåren (credential/env), ikke en providerrad. Kapabilitetsformen
  + engangskonsum i `idempotens` gir samme garantier uten ny tabell.

* GET /v1/epost/kilder/callback — uautentisert NAVIGASJON fra
  Microsoft (OIDC-callback-klassen): credentialet er state-kapabiliteten
  + bindingcookien. Konsumet committes FØR nettverkskallet (v4 §3),
  kodevekslingen går UTELUKKENDE over den IP-pinnede ssrf-transporten,
  refresh-tokenet krypteres med tenant-DEK (058-formen) og raden skrives
  DIREKTE fra API-laget under RLS-kontekst — 088 har ingen definer-dør
  for kilden, og skriveveiens fødsel er kolonnegrantene i `migrer.py`
  (056/057-læren: runtime-grants bor der). Feilsiden er generisk og
  gjengir ALDRI URL-parametere (v5 §6).

* GET /v1/epost/kilder — leseflaten (`epost:read`): postboks, status,
  sist hentet. Aldri credential-trioen — web-API-rollen HAR ikke SELECT
  på den (kolonnegranten fra PR-A står), så svaret KAN ikke bære
  tokenet.

* POST /v1/epost/kilder/{id}/deaktiver — enveis (`aktiv|feilet` →
  `deaktivert`, 088-vaktens avviklingsform). Ingen reaktiveringsrute:
  veien tilbake er en FULL ny OAuth-samtykkeflyt (rekobling oppdaterer
  credential-trioen og setter `aktiv` — 088-kommentarens
  «token-refresh, PR-B»).

Token-utlevering til arbeideren (PR-C-forberedelsen) er
`hent_access_token` nederst: en ren funksjon som leser credential-
trioen på KALLERENS kobling, dekrypterer og veksler refresh→access over
ssrf-transporten. Selve veksle-ENDEPUNKTET venter til PR-C: web-API-
rollen er med vilje nektet SELECT på credential-trioen, så utleverings-
døren må bo hos arbeiderrollen — og claim-bindingen den skal bindes til
finnes først der.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import psycopg
from starlette.requests import Request
from starlette.responses import Response

from db import kryptering
from db.pg import sett_kontekst, sett_tenant

from . import ssrf

#: Modulen rutene tilhører (rollback-kontrakten, m57-formen).
EPOSTMODUL = "m06_epost"

#: Dommen pkt. 1: KUN lesende — Mail.Read + offline_access (refresh-
#: token). Ingen openid/User.Read: postboksen oppgis av administratoren
#: i /start-kroppen, ikke utledes av en identitetsclaim — å be om ETT
#: scope mer enn dommen navnga er en kontraktsendring, ikke en detalj.
M365_SCOPE = "https://graph.microsoft.com/Mail.Read offline_access"

#: Statens levetid — OIDC-flytens frist (sesjon.LOGIN_TX_MIN), samme
#: begrunnelse: en authorize-runde tar sekunder, ikke timer.
STATE_TTL_S = 10 * 60

#: Browserbindingen (v4 §1) — egen cookie, OIDC-formens navneklasse.
C_BINDING = "__Host-disponit_m365"

#: Formålsstrengene MAC-en binder. To ULIKE formål: statens payload og
#: bindingcookien signeres hver for seg, og en verdi utstedt som det ene
#: verifiserer aldri som det andre.
_FORMAAL_STATE = "m365_kilde_state"
_FORMAAL_BINDING = "m365_kilde_binding"

#: Engangskonsumets nøkkelrom i `idempotens` — RESERVERT i
#: `kjerne.RESERVERTE_NOKKELROM`: nøkkelen er plattformavledet, og en
#: kaller som fører egen Idempotency-Key videre skal ikke kunne skrive
#: i rommet callbacken leser som sitt eget spor.
NOKKELROM = "m365kilde:"

#: Azure-tenantsegmentet er en URL-PATH-komponent fra miljøet — lukket
#: tegnsett (GUID, verifisert domene, eller alias-ene), aldrig rå
#: interpolering av noe som kan bære skilletegn.
_TENANTSEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,254}$")

_POSTBOKS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class KildeFeil(Exception):
    """Enhver avvisning i kildeflyten — generisk utad, detaljert i logg."""


#: Callbackens sti — den ENE strengen ruten, konfigvalideringen og
#: fallbacken deler, så en omruting aldri kan gjøre dem uenige.
CALLBACKSTI = "/v1/epost/kilder/callback"


@dataclass(frozen=True)
class M365Konfig:
    client_id: str
    client_secret: str
    autoriser_endepunkt: str
    token_endepunkt: str
    allowlist: tuple = ()
    #: Redirect-URI-en som er REGISTRERT i Azure. Valgfri: settes den
    #: ikke, utledes den av den kanoniske hosten nginx satte. Er den
    #: satt, VINNER den — det er den eneste kilden som ikke går gjennom
    #: en forespørselsheader, og den må uansett stemme byte for byte med
    #: app-registreringen for at Microsoft skal godta runden.
    redirect_uri: str | None = None


def _allowlist() -> tuple:
    """Staging-unntak for ssrf-transporten — eksakt (scheme,host,port,
    cidr) fra miljø, samme format og av samme grunn som
    `sesjon._staging_allowlist` (test-IdP på loopback). Tom i prod."""
    raa = os.environ.get("DISPONIT_M365_ALLOWLIST", "")
    ut = []
    for post in filter(None, (p.strip() for p in raa.split(";"))):
        s, h, p, c = post.split(",")
        ut.append((s, h, int(p), c))
    return tuple(ut)


def hent_konfig() -> M365Konfig | None:
    """M365-konfigen fra miljøet (LoadCredential-klassen, PR-009 §5) —
    VALGFRITT konfigurert: mangler client_id/secret, finnes ingen flyt,
    og endepunktet svarer den ÆRLIGE koden (`m365_ikke_konfigurert`)
    i stedet for en halv flyt. Et ugyldig tenantsegment behandles som
    ukonfigurert av samme grunn: fail-closed på konfigfeil."""
    cid = os.environ.get("DISPONIT_M365_CLIENT_ID", "").strip()
    secret = os.environ.get("DISPONIT_M365_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        return None
    tenant = os.environ.get("DISPONIT_M365_TENANT", "organizations").strip()
    if not _TENANTSEGMENT.match(tenant):
        return None
    redirect_uri = os.environ.get("DISPONIT_M365_REDIRECT_URI", "").strip()
    if redirect_uri and not _gyldig_redirect_uri(redirect_uri):
        return None
    basis = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0"
    return M365Konfig(
        client_id=cid, client_secret=secret,
        autoriser_endepunkt=f"{basis}/authorize",
        token_endepunkt=f"{basis}/token",
        allowlist=_allowlist(),
        redirect_uri=redirect_uri or None)


def _gyldig_redirect_uri(raa: str) -> bool:
    """En redirect-URI er BARE gyldig hvis den er nøyaktig husets egen
    callback over https: rett sti, ingen query/fragment/userinfo, og en
    vert. Alt annet er en feilkonfigurasjon, og fail-closed er den
    samme ærlige koden — aldri en flyt som sender eier et fremmed sted."""
    delt = urlsplit(raa)
    return (delt.scheme == "https" and bool(delt.hostname)
            and not (delt.username or delt.password)
            and delt.path == CALLBACKSTI
            and not delt.query and not delt.fragment)


# ---------------------------------------------------------------------------
# Egress: begge Microsoft-endepunktene valideres FØR bruk (oidc-formen)
# ---------------------------------------------------------------------------

def _valider_egress(url: str, allowlist: tuple) -> None:
    delt = urlsplit(url)
    if delt.username or delt.password or delt.fragment:
        raise KildeFeil(f"endepunkt har userinfo/fragment: {url}")
    port = delt.port or (443 if delt.scheme == "https" else 80)
    try:
        ssrf.valider_og_pin(delt.scheme, delt.hostname or "", port, allowlist)
    except ssrf.SsrfAvvist as e:
        raise KildeFeil(f"endepunkt utenfor egresspolicy: {url}") from e


# ---------------------------------------------------------------------------
# State-kapabiliteten: MAC-et, engangs, kort TTL
# ---------------------------------------------------------------------------

def _b64url(raa: bytes) -> str:
    return base64.urlsafe_b64encode(raa).decode("ascii").rstrip("=")


def _fra_b64url(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hash(v: str) -> str:
    return hashlib.sha256(v.encode("utf-8")).hexdigest()


def binding_for(mac_register, jti: str) -> str:
    """Bindingcookiens verdi — DETERMINISTISK av jti under signerer-
    nøkkelen, så et idempotent replay av /start kan sette NØYAKTIG samme
    cookie uten at verdien lagres noe sted. En part uten MAC-nøkkelen
    kan ikke forfalske den; payloaden bærer bare hashen."""
    _, mac = mac_register.signer({"formaal": _FORMAAL_BINDING, "jti": jti})
    return mac


def bygg_state(mac_register, payload: dict) -> str:
    """-> `b64url(json({payload, key_id, mac}))` — ÉN konvolutt.

    IKKE tre punktseparerte segmenter. State-tokenet er husets egen
    kapabilitet og skal ikke KUNNE forveksles med et JWT: en punktsplitt
    pluss base64-dekoding er nøyaktig formen `test_ssrf`s klarsignal-port
    feller, fordi hjemmelaget JWS-parsing er en klassisk sårbarhet — og
    formen er dessuten skjør på et key_id som selv inneholder skilletegn.
    Konvolutten har ingen skilletegn å ta feil av.

    MAC-en regnes over den JCS-kanoniske PAYLOADEN (PR-012-formen);
    transportens base64 er presentasjon, aldri det signerte."""
    key_id, mac = mac_register.signer(payload)
    raa = json.dumps({"payload": payload, "key_id": key_id, "mac": mac},
                     ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return _b64url(raa)


def les_state(mac_register, raa: str, naa: float | None = None) -> dict:
    """Verifiserer MAC og TTL, -> payload. Kaster KildeFeil ved ethvert
    avvik — fail-closed, ingen delvis lesing."""
    try:
        konvolutt = json.loads(_fra_b64url(raa))
        payload, key_id, mac = (konvolutt["payload"], konvolutt["key_id"],
                                konvolutt["mac"])
    except (ValueError, TypeError, KeyError,
            json.JSONDecodeError) as e:
        raise KildeFeil("state feilformet") from e
    if not isinstance(payload, dict) \
            or payload.get("formaal") != _FORMAAL_STATE:
        raise KildeFeil("state har feil formål")
    if not mac_register.verifiser(payload, mac, key_id):
        raise KildeFeil("state-MAC ugyldig")
    naa = naa if naa is not None else time.time()
    utloper = payload.get("utloper")
    if not isinstance(utloper, (int, float)) or naa > utloper:
        raise KildeFeil("state utløpt")
    return payload


# ---------------------------------------------------------------------------
# POST /v1/epost/kilder/start
# ---------------------------------------------------------------------------

def _modul_inaktiv(tjeneste, rid):
    """Rollback-kontrakten (m57-formen): er `m06_epost` deaktivert,
    svarer ruten 503 FØR tilkoblingen hentes."""
    from .app import _feilsvar
    if EPOSTMODUL not in tjeneste.inaktive_moduler:
        return None
    tjeneste.logg.hendelse("modul_inaktiv", rid, art="drift",
                           modul=EPOSTMODUL)
    return _feilsvar("modul_inaktiv", rid)


def _kanonisk_host(request: Request) -> str:
    """Callback-adressens vert — SERVER-SIDE fra den kanoniske hosten
    nginx satte, aldri fra kroppen (v2 §5-formen fra sesjon.py)."""
    return (request.headers.get("x-disponit-host")
            or request.headers.get("host", "").split(":")[0]).lower()


def start_endepunkt(tjeneste, request: Request) -> Response:
    from .app import _feilsvar, _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn)
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "epost:kilde:administrer")
        nokkel = _krev_idem(request, rid)
        from . import kjerne
        if kjerne.er_reservert_nokkel(nokkel):
            # `_feilsvar`, IKKE `_feil`: statuskoden bor i feilveitabellen
            # (400), og `policyadmin_http._FEIL_HTTP` kjenner ikke denne
            # koden — den ville falt til standardsvaret 409 og gjort en
            # sikkerhetsavvisning om til en konflikt.
            tjeneste.logg.hendelse("idempotensnokkel_reservert", rid, tenant,
                                   art="sikkerhet", aktor=f"bruker:{bid}")
            raise _Avbrudd(_feilsvar("idempotensnokkel_reservert", rid))
        kropp = _kropp(request)
        postboks = kropp.get("postboks")
        if not isinstance(postboks, str) \
                or not _POSTBOKS.match(postboks.strip()) \
                or len(postboks.strip()) > 254:
            raise _Avbrudd(_feil("request_feilformet", rid))
        postboks = postboks.strip().lower()

        konfig = hent_konfig()
        if konfig is None:
            # Den ÆRLIGE koden — ingen state utstedes, ingen halv flyt.
            tjeneste.logg.hendelse("m365_ikke_konfigurert", rid, tenant,
                                   art="drift")
            raise _Avbrudd(_feilsvar("m365_ikke_konfigurert", rid))
        try:
            _valider_egress(konfig.autoriser_endepunkt, konfig.allowlist)
            _valider_egress(konfig.token_endepunkt, konfig.allowlist)
        except KildeFeil:
            tjeneste.logg.hendelse("m365_egress_avvist", rid, tenant,
                                   art="sikkerhet")
            raise _Avbrudd(_feilsvar("m365_ikke_konfigurert", rid))

        # Idempotensclaim (003-lageret, kjerne-formen light): samme
        # nøkkel + samme input → REPLAY av nøyaktig samme authorize-URL
        # (staten er fortsatt ubrukt til callbacken konsumerer den);
        # samme nøkkel + annen postboks → konflikt.
        input_hash = _hash(f"{tenant}\x1f{bid}\x1fm365start\x1f{postboks}")
        ny = conn.execute(
            "INSERT INTO idempotens (tenant, nokkel, input_hash, status,"
            " respons, request_id) VALUES (%s,%s,%s,'paagaar',NULL,%s)"
            " ON CONFLICT (tenant, nokkel) DO NOTHING RETURNING nokkel",
            (tenant, nokkel, input_hash, rid)).fetchone()
        if ny is None:
            rad = conn.execute(
                "SELECT input_hash, status, respons FROM idempotens"
                " WHERE tenant=%s AND nokkel=%s",
                (tenant, nokkel)).fetchone()
            if rad is None or rad[0] != input_hash or rad[1] != "ferdig" \
                    or rad[2] is None:
                raise _Avbrudd(_feil("idempotenskonflikt", rid))
            conn.rollback()
            return _startsvar(tjeneste, rad[2], rid)

        jti = secrets.token_hex(16)
        binding = binding_for(tjeneste.mac_register, jti)
        verifier = _b64url(secrets.token_bytes(64))
        challenge = _b64url(
            hashlib.sha256(verifier.encode("ascii")).digest())
        # Verifieren reiser i statens payload, men tenant-DEK-kryptert:
        # payloaden ses av browseren og Microsoft, verifieren skal ikke.
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
        ct, nonce = kryptering.krypter(dek, {"v": verifier}, tenant, key_id)
        redirect_uri = konfig.redirect_uri or (
            f"https://{_kanonisk_host(request)}{CALLBACKSTI}")
        payload = {
            "formaal": _FORMAAL_STATE, "tenant": tenant, "bruker": bid,
            "postboks": postboks, "jti": jti,
            "utloper": int(time.time()) + STATE_TTL_S,
            "binding_hash": _hash(binding),
            "redirect_uri": redirect_uri,
            "pkce_ct": _b64url(ct), "pkce_nonce": _b64url(nonce),
            "pkce_key_id": key_id,
        }
        state = bygg_state(tjeneste.mac_register, payload)
        url = konfig.autoriser_endepunkt + "?" + urlencode({
            "response_type": "code",
            "client_id": konfig.client_id,
            "redirect_uri": redirect_uri,
            "response_mode": "query",
            "scope": M365_SCOPE,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        respons = {"autorisasjonsurl": url, "jti": jti}
        conn.execute(
            "UPDATE idempotens SET status='ferdig', respons=%s"
            " WHERE tenant=%s AND nokkel=%s",
            (json.dumps(respons, ensure_ascii=False), tenant, nokkel))
        conn.commit()
        return _startsvar(tjeneste, respons, rid)

    return _med_conn(tjeneste, rid, kjor)


def _startsvar(tjeneste, respons, rid: str) -> Response:
    """200 med authorize-URL + bindingcookie. Ved replay REKONSTRUERES
    cookien deterministisk av jti — nøyaktig samme verdi som første
    gang, så en retry aldri etterlater en binding staten ikke matcher."""
    from .app import kanonisk_json
    if isinstance(respons, str):
        respons = json.loads(respons)
    binding = binding_for(tjeneste.mac_register, respons["jti"])
    r = kanonisk_json({"autorisasjonsurl": respons["autorisasjonsurl"]},
                      200, {"x-request-id": rid})
    r.raw_headers.append(
        (b"set-cookie", _cookie(C_BINDING, binding, STATE_TTL_S).encode()))
    return r


def _cookie(navn: str, verdi: str, max_age: int) -> str:
    return (f"{navn}={verdi}; Path=/; Secure; SameSite=Lax;"
            f" Max-Age={max_age}; HttpOnly")


# ---------------------------------------------------------------------------
# GET /v1/epost/kilder/callback
# ---------------------------------------------------------------------------

def _feil_side(rid: str) -> Response:
    """Generisk callback-feilside (v5 §6-formen fra sesjon.py):
    gjengir ALDRI URL-parametere, aldri hvorfor."""
    return Response(
        content=b'{"feil":"m365_tilkobling_feilet"}', status_code=400,
        media_type="application/json",
        headers={"Referrer-Policy": "no-referrer", "x-request-id": rid})


def _veksle_kode(konfig: M365Konfig, code: str, verifier: str,
                 redirect_uri: str) -> dict:
    """code → tokensvar hos Microsoft, UTELUKKENDE over den IP-pinnede
    ssrf-transporten (`ssrf.lag_klient`: ingen redirects, korte
    timeouts, 256 KiB-tak). -> tokensvaret (dict). Testene bytter ut
    denne funksjonen — samme snitt som OIDC-testenes `_hent_json`."""
    _valider_egress(konfig.token_endepunkt, konfig.allowlist)
    klient = ssrf.lag_klient(konfig.allowlist)
    try:
        r = klient.post(konfig.token_endepunkt, data={
            "client_id": konfig.client_id,
            "client_secret": konfig.client_secret,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "scope": M365_SCOPE,
        })
        raa = ssrf.les_begrenset(r)
        if r.status_code != 200:
            raise KildeFeil(f"tokenveksling ga status {r.status_code}")
        return json.loads(raa)
    except ssrf.SsrfAvvist as e:
        raise KildeFeil(str(e)) from e
    except (json.JSONDecodeError, OSError) as e:
        raise KildeFeil(f"tokenveksling feilet: {type(e).__name__}") from e
    finally:
        klient.close()


def callback_endepunkt(tjeneste, request: Request) -> Response:
    from .app import _feilsvar, _rid
    from .sesjon import _ip_prefiks, _rate, SesjonFeil
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    binding = request.cookies.get(C_BINDING)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        ip = _ip_prefiks(request)
        # Kontekstløst rate-vindu på ugyldig state (sesjon-formen, samme
        # faser og dermed samme bøtter som OIDC-callbacken: én angriper
        # mot begge dører deler ett vindu).
        sett_tenant(conn, "_oidc")
        try:
            if not code or not state or not binding:
                raise KildeFeil("callback mangler code/state/binding")
            payload = les_state(tjeneste.mac_register, state)
            if not secrets.compare_digest(payload.get("binding_hash", ""),
                                          _hash(binding)):
                raise KildeFeil("binding matcher ikke staten")
        except KildeFeil:
            try:
                _rate(conn, "callback_ugyldig", ip)
            except SesjonFeil:
                pass
            conn.commit()
            return _feil_side(rid)

        tenant, bruker = payload["tenant"], payload["bruker"]
        postboks, jti = payload["postboks"], payload["jti"]

        # ENGANGS: atomisk konsum i det reserverte nøkkelrommet,
        # committet FØR nettverkskallet (v4 §3). Et replay finner raden
        # og avvises FØR kodeveksling og FØR enhver kildeskriving. En
        # flyt som feiler ETTER konsumet er brukt opp — administratoren
        # starter forfra; en konsumert stat gjenoppstår aldri.
        sett_kontekst(conn, tenant, f"bruker:{bruker}", rid)
        konsumert = conn.execute(
            "INSERT INTO idempotens (tenant, nokkel, input_hash, status,"
            " respons, request_id) VALUES"
            " (%s,%s,%s,'ferdig','{}'::jsonb,%s)"
            " ON CONFLICT (tenant, nokkel) DO NOTHING RETURNING nokkel",
            (tenant, NOKKELROM + jti, _hash(state), rid)).fetchone()
        if konsumert is None:
            conn.rollback()
            sett_tenant(conn, "_oidc")
            try:
                _rate(conn, "callback_ugyldig", ip)
            except SesjonFeil:
                pass
            conn.commit()
            return _feil_side(rid)
        conn.commit()                # ingen lås under nettverkskallet

        konfig = hent_konfig()
        if konfig is None:
            return _feil_side(rid)
        # Dekrypter PKCE-verifieren (statens DEK-krypterte last) og veksle
        # koden. ALT herfra er fail-closed under ÉN generisk feilside:
        # en ukjent/destruert key_id, et forfalsket ciphertext og et
        # avvist tokensvar skal være UMULIG å skille utenfra — derfor
        # `Exception` og ikke en liste over de feilene vi kom på.
        try:
            # `hent_dek` setter selv `disponit.tenant` for lesingen.
            dek = kryptering.hent_dek(conn, tenant, payload["pkce_key_id"])
            conn.rollback()
            verifier = kryptering.dekrypter(
                dek, _fra_b64url(payload["pkce_ct"]),
                _fra_b64url(payload["pkce_nonce"]), tenant,
                payload["pkce_key_id"])["v"]
            token = _veksle_kode(konfig, code, verifier,
                                 payload["redirect_uri"])
            refresh = token.get("refresh_token")
            if not isinstance(refresh, str) or not refresh:
                raise KildeFeil("tokensvaret manglet refresh_token")
        except Exception:
            conn.rollback()
            sett_tenant(conn, "_oidc")
            try:
                _rate(conn, "callback_token", ip)
            except SesjonFeil:
                pass
            conn.commit()
            return _feil_side(rid)

        # Krypter refresh-tokenet med tenant-DEK (058-formen) og skriv
        # kilden. REKOBLING av samme postboks (UNIQUE-en tvinger én rad
        # per boks) oppdaterer credential-trioen og setter `aktiv` —
        # med PARAMETRE i SET, aldri EXCLUDED: et upsert-uttrykk som
        # LESER kolonnen ville krevd SELECT på credential-trioen, som
        # web-API-rollen med vilje ikke har.
        sett_kontekst(conn, tenant, f"bruker:{bruker}", rid)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
        ct, nonce = kryptering.krypter(dek, {"refresh_token": refresh},
                                       tenant, key_id)
        conn.execute(
            "INSERT INTO epost_kilde (tenant, leverandor, postboks,"
            " auth_kryptert, nonce, key_id) VALUES (%s,'m365',%s,%s,%s,%s)"
            " ON CONFLICT (tenant, leverandor, postboks) DO UPDATE SET"
            " auth_kryptert=%s, nonce=%s, key_id=%s, status='aktiv'",
            (tenant, postboks, ct, nonce, key_id, ct, nonce, key_id))
        conn.commit()
        tjeneste.logg.hendelse("m365_kilde_tilkoblet", rid, tenant,
                               art="drift", aktor=f"bruker:{bruker}")

        # Ren, relativ redirect (v5 §6) — fjerner code/state fra
        # historikken og rydder bindingen.
        r = Response(status_code=303, headers={
            "Location": "/#/epost",
            "Referrer-Policy": "no-referrer", "x-request-id": rid})
        r.raw_headers.append(
            (b"set-cookie", _cookie(C_BINDING, "", 0).encode()))
        return r
    finally:
        tjeneste.pool.gi_tilbake(conn)


# ---------------------------------------------------------------------------
# GET /v1/epost/kilder + POST /v1/epost/kilder/{id}/deaktiver
# ---------------------------------------------------------------------------

def _leseauth_epost(tjeneste, request, conn, rid: str):
    """Som `policyadmin_http._leseauth`, men for `epost:read`."""
    from . import kjerne
    from .app import _autentiser
    from .policyadmin_http import _Avbrudd, _feil, _gjenopprett_kontekst
    try:
        auth = _autentiser(tjeneste, request, conn, rid, "epost:read")
    except kjerne.Feilsvar as f:
        raise _Avbrudd(_feil(f.kode, rid))
    bid = auth.token_id.split("sesjon:", 1)[-1]
    conn.rollback()
    _gjenopprett_kontekst(conn, auth.tenant, bid, rid)
    return auth.tenant, bid


def liste_endepunkt(tjeneste, request: Request) -> Response:
    from .app import _rid
    from .policyadmin_http import _med_conn, _ok
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av

    def kjor(conn):
        tenant, _bid = _leseauth_epost(tjeneste, request, conn, rid)
        rader = conn.execute(
            "SELECT kilde_id, leverandor, postboks, status,"
            " sist_hentet_ts, opprettet FROM epost_kilde"
            " WHERE tenant=%s ORDER BY opprettet, kilde_id",
            (tenant,)).fetchall()
        return _ok({"kilder": [
            {"kilde_id": str(r[0]), "leverandor": r[1], "postboks": r[2],
             "status": r[3],
             "sist_hentet_ts": r[4].isoformat() if r[4] else None,
             "opprettet": r[5].isoformat()} for r in rader]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def deaktiver_endepunkt(tjeneste, request: Request) -> Response:
    import uuid as uuidlib

    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _feil, _med_conn, _ok)
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av
    try:
        kid = uuidlib.UUID(str(request.path_params["kilde_id"]))
    except (KeyError, ValueError):
        return _feil("request_feilformet", rid, 400)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "epost:kilde:administrer")
        rad = conn.execute(
            "UPDATE epost_kilde SET status='deaktivert'"
            " WHERE tenant=%s AND kilde_id=%s"
            "   AND status IN ('aktiv','feilet') RETURNING kilde_id",
            (tenant, kid)).fetchone()
        if rad is None:
            # Alt deaktivert er et idempotent gjensyn; ukjent id er 404
            # (samme svar som kryss-tenant — intet oppslagsverk).
            finnes = conn.execute(
                "SELECT status FROM epost_kilde"
                " WHERE tenant=%s AND kilde_id=%s", (tenant, kid)).fetchone()
            conn.rollback()
            if finnes is None:
                return _feil("ikke_funnet", rid, 404)
            return _ok({"kilde_id": str(kid), "status": "deaktivert"}, rid)
        conn.commit()
        tjeneste.logg.hendelse("m365_kilde_deaktivert", rid, tenant,
                               art="drift", aktor=f"bruker:{bid}",
                               kilde=str(kid))
        return _ok({"kilde_id": str(kid), "status": "deaktivert"}, rid)

    return _med_conn(tjeneste, rid, kjor)


# ---------------------------------------------------------------------------
# PR-C-forberedelsen: refresh → kortlivet access-token
# ---------------------------------------------------------------------------

def hent_access_token(conn, tenant: str, kilde_id, *,
                      konfig: M365Konfig | None = None,
                      veksler=None) -> str:
    """Pakker ut kildens refresh-token og veksler til et KORTLIVET
    access-token over ssrf-transporten. -> access-tokenet (aldri
    persistert, aldri returnert i noe HTTP-svar i PR-B).

    KALLERENS kobling og dermed KALLERENS rolle: web-API-rollen er
    bevisst nektet SELECT på credential-trioen, så denne funksjonen kan
    bare lykkes fra en rolle som har den lesingen — innhenterens
    (PR-C), som også får HTTP-/claim-bindingen sin der. `veksler` er
    testsnittet (samme form som `_veksle_kode`)."""
    konfig = konfig if konfig is not None else hent_konfig()
    if konfig is None:
        raise KildeFeil("m365 er ikke konfigurert")
    rad = conn.execute(
        "SELECT auth_kryptert, nonce, key_id, status FROM epost_kilde"
        " WHERE tenant=%s AND kilde_id=%s", (tenant, kilde_id)).fetchone()
    if rad is None:
        raise KildeFeil("ukjent kilde")
    ct, nonce, key_id, status = rad
    if status != "aktiv":
        raise KildeFeil("kilden er ikke aktiv")
    dek = kryptering.hent_dek(conn, tenant, key_id)
    refresh = kryptering.dekrypter(dek, bytes(ct), bytes(nonce),
                                   tenant, key_id)["refresh_token"]
    if veksler is not None:
        token = veksler(konfig, refresh)
    else:
        token = _veksle_refresh(konfig, refresh)
    access = token.get("access_token")
    if not isinstance(access, str) or not access:
        raise KildeFeil("tokensvaret manglet access_token")
    return access


def _veksle_refresh(konfig: M365Konfig, refresh: str) -> dict:
    """refresh → tokensvar, samme ssrf-transport som kodevekslingen."""
    _valider_egress(konfig.token_endepunkt, konfig.allowlist)
    klient = ssrf.lag_klient(konfig.allowlist)
    try:
        r = klient.post(konfig.token_endepunkt, data={
            "client_id": konfig.client_id,
            "client_secret": konfig.client_secret,
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "scope": M365_SCOPE,
        })
        raa = ssrf.les_begrenset(r)
        if r.status_code != 200:
            raise KildeFeil(f"tokenfornyelse ga status {r.status_code}")
        return json.loads(raa)
    except ssrf.SsrfAvvist as e:
        raise KildeFeil(str(e)) from e
    except (json.JSONDecodeError, OSError) as e:
        raise KildeFeil(f"tokenfornyelse feilet: {type(e).__name__}") from e
    finally:
        klient.close()
