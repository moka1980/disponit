"""#162: inndata-artefaktet — buntens vei INN, PR-1 (reservasjon +
opplasting).

To ruter, speilet av 017s utdata-form i motsatt retning:

* POST /v1/inndata/reserver (browserkontekst, `bestilling:opprett`):
  utsteder en engangs-reservasjon FØR opplasting. Taket er KONTRAKTENS
  (`INNDATA_MAKS_FYSISK` i denne v1-en) — klienten ber aldri om et tall.
* PUT /v1/inndata/opplast/{jti} (samme auth): rå zip-kropp, STRØMMET —
  middleware teller uten å bufre, endepunktet hasher og samler opp til
  reservasjonens deklarerte tak, krypterer med tenant-DEK
  (binær-AAD `inndata`) og skriver til FS-lageret; `registrer_inndata_
  lastet` (058) møter målingen mot deklarasjonen og forbruker jti-en.

Resolveren (modulens lesevei) og bestillingsbindingen er PR-2 — én dør
per PR, K3-lærdommen fra #153/#176.
"""
from __future__ import annotations

import hashlib
import os
import uuid as uuidlib

import psycopg

#: FS-roten. opp.sh oppretter den med api-brukerens eierskap; en
#: manglende rot er en deploy-feil og skal si det, ikke ENOENT dypt nede.
INNDATA_ROT = os.environ.get("DISPONIT_INNDATA_ROT",
                             "/var/lib/disponit/inndata")

#: Lukket kontraktssett (v1): nøyaktig hvilke (eiermodul, formål)-par som
#: kan reservere inndata. En ny modul/nytt formål er en kontraktsendring
#: her + i 058-CHECKene — aldri en parameter.
TILLATTE_RESERVASJONER = frozenset({("m57_ats", "soknadsbunt")})


def reserver_endepunkt(tjeneste, request):
    """POST /v1/inndata/reserver — {eiermodul, formaal}."""
    from .app import INNDATA_MAKS_FYSISK, _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        kropp = _kropp(request)
        eiermodul, formaal = kropp.get("eiermodul"), kropp.get("formaal")
        # Lukket sett: i v1 finnes nøyaktig én lovlig kombinasjon, og en
        # ny modul/nytt formål er en KONTRAKTSENDRING (058-CHECKene sier
        # det samme — dette er bare den tidlige, lesbare avvisningen).
        if (eiermodul, formaal) not in TILLATTE_RESERVASJONER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        rad = conn.execute(
            "SELECT inndata_id, reservasjon_jti FROM"
            " reserver_inndata(%s,%s,%s,%s)",
            (tenant, eiermodul, formaal, INNDATA_MAKS_FYSISK)).fetchone()
        conn.commit()
        return _ok({"inndata_ref": f"inndata:{rad[0]}",
                    "reservasjon_jti": rad[1],
                    "maks_bytes": INNDATA_MAKS_FYSISK}, rid, 201)

    return _med_conn(tjeneste, rid, kjor)


async def opplast_endepunkt(tjeneste, request):
    """PUT /v1/inndata/opplast/{jti} — rå zip-kropp, strømmet."""
    from .app import INNDATA_MAKS_FYSISK, _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _med_conn, _ok)
    rid = _rid(request)
    jti = request.path_params["jti"]

    # Strømmen leses FØR db-arbeidet: kroppen finnes bare én gang, og
    # auth-rollback-dansen i _med_conn skal ikke stå mellom chunkene.
    # Taket håndheves p.t. to steder med samme tall: middleware-telleren
    # (transport) og samlingen her (kontrakt) — reservasjonens eget tak
    # møter målingen i 058-funksjonen til slutt.
    hasher = hashlib.sha256()
    deler: list[bytes] = []
    lest = 0
    async for chunk in request.stream():
        lest += len(chunk)
        if lest > INNDATA_MAKS_FYSISK or \
                request.scope.get("state", {}).get("inndata_for_stor"):
            return _feil("body_for_stor", rid, 413)
        hasher.update(chunk)
        deler.append(chunk)
    raa = b"".join(deler)
    del deler
    if not raa:
        return _feil("request_feilformet", rid)
    sha = hasher.hexdigest()

    def kjor(conn):
        from db import kryptering
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        # Reservasjonen slås opp via 058-funksjonen alene (den eier
        # engangs-semantikken); her trengs bare krypto + fil FØR kallet.
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
        ct, nonce = kryptering.krypter_bytes(dek, raa, tenant, key_id,
                                             formaal=b"inndata")
        katalog = os.path.join(INNDATA_ROT, tenant)
        os.makedirs(katalog, mode=0o700, exist_ok=True)
        sti = os.path.join(katalog, f"{uuidlib.uuid4()}.bin")
        # Skriv-og-flytt: en halvskrevet fil skal aldri kunne bli en
        # gyldig referanse.
        tmp = sti + ".tmp"
        with open(tmp, "wb") as f:
            f.write(ct)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, sti)
        try:
            rad = conn.execute(
                "SELECT registrer_inndata_lastet(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, jti, lest, sha, key_id, nonce, sti)).fetchone()
            conn.commit()
        except psycopg.errors.InvalidParameterValue as e:
            os.unlink(sti)
            raise _Avbrudd(_feil("inndata_reservasjon_ugyldig", rid, 409)) \
                from e
        except psycopg.errors.UniqueViolation as e:
            os.unlink(sti)
            raise _Avbrudd(_feil("inndata_alt_lastet", rid, 409)) from e
        except Exception:
            os.unlink(sti)
            raise
        return _ok({"inndata_ref": f"inndata:{rad[0]}",
                    "innhold_sha256": sha, "faktiske_bytes": lest}, rid,
                   201)

    return _med_conn(tjeneste, rid, kjor)


def hent_endepunkt(tjeneste, request):
    """POST /v1/inndata/hent/{inndata_id} — modulens hentevei (059).

    POST, ikke GET (rutescope-regelen: leseruter bærer lese-scopes, og
    modulveien er ORDRESCOPE-klassen som claim/kvittering — hentingen
    FORBRUKER deploymentens autoritet, den blar ikke).

    Autorisasjonen er CLAIMET: modultokenet identifiserer deploymenten,
    og retten til bunten er retten til det plukkede oppdraget den er
    bundet til — målt i én kryss-tenant-definer. Bytene dekrypteres og
    SHA-verifiseres mot radens måling FØR de forlater huset: en fil som
    har driftet fra sin egen deklarasjon serveres aldri.
    """
    import psycopg
    from starlette.responses import Response

    from .app import (ModulAutentisert, _feilsvar, _modultoken_revalidert,
                      _rid, preauth)
    rid = _rid(request)
    inndata_id = request.path_params["inndata_id"]
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        auth = preauth(tjeneste, conn,
                       request.headers.get("authorization"), rid)
        if not isinstance(auth, ModulAutentisert):
            tjeneste.logg.hendelse("token_ugyldig", rid)
            return _feilsvar("token_ugyldig", rid)
        # Samme to porter som hver annen modulvei (CodeRabbit major,
        # pre-commit-pass): raten, og REVALIDERINGEN — et nødstoppet
        # token skal ikke kunne hente bunter over transaksjonsgrensen.
        if not tjeneste.rate.slipp_gjennom(auth.token_id):
            tjeneste.logg.hendelse("rate_grense", rid, auth.tenant)
            return _feilsvar("rate_grense", rid)
        revalidering = _modultoken_revalidert(tjeneste, conn, auth, rid)
        if revalidering is not None:
            return revalidering
        rad = conn.execute(
            "SELECT tenant, lager_sti, key_id, nonce, innhold_sha256,"
            "       faktiske_bytes FROM hent_inndata_for_modul(%s, %s)",
            (inndata_id, auth.modul_id)).fetchone()
        conn.rollback()
        if rad is None:
            # Samme svar uansett årsak — finnes-ikke, feil modul, ikke
            # claimet: et oppslagsverk over andres bunter skal ikke finnes.
            return _feilsvar("ikke_funnet", rid)
        tenant, sti, key_id, nonce, sha, _byte = rad
        from db import kryptering
        from db.pg import sett_kontekst
        sett_kontekst(conn, tenant, auth.aktor, rid)
        nokkelrad = conn.execute(
            "SELECT wrapped_dek FROM tenant_nokler"
            " WHERE tenant=%s AND key_id=%s", (tenant, key_id)).fetchone()
        conn.rollback()
        if nokkelrad is None or nokkelrad[0] is None:
            return _feilsvar("tenantnokkel_mangler", rid)
        dek = kryptering._pakk_ut((key_id, nokkelrad[0]), tenant)[1]
        try:
            with open(sti, "rb") as f:
                ct = f.read()
        except OSError:
            tjeneste.logg.hendelse("inndata_fil_borte", rid, tenant,
                                   art="drift")
            return _feilsvar("intern_feil", rid)
        raa = kryptering.dekrypter_bytes(dek, ct, bytes(nonce), tenant,
                                         key_id, formaal=b"inndata")
        import hashlib as _h
        if _h.sha256(raa).hexdigest() != sha:
            tjeneste.logg.hendelse("inndata_sha_avvik", rid, tenant,
                                   art="sikkerhet")
            return _feilsvar("intern_feil", rid)
        return Response(raa, media_type="application/zip",
                        headers={"x-request-id": rid,
                                 "x-innhold-sha256": sha})
    finally:
        tjeneste.pool.gi_tilbake(conn)
