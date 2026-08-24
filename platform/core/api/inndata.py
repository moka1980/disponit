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
        if (eiermodul, formaal) != ("m57_ats", "soknadsbunt"):
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
                                   _gjenopprett_kontekst, _med_conn, _ok)
    rid = _rid(request)
    jti = request.path_params["jti"]

    # AUTH FØRST, KROPP ETTERPÅ (Codex P1). Denne ruten tar imot inntil 64
    # MiB, og strømmen ble tidligere lest, hashet og `join`-et FØR
    # `_browserkontekst` — `join` dupliserer i tillegg bufferet et kort
    # øyeblikk. En klient UTEN gyldig sesjon kunne dermed binde hundrevis
    # av MiB i API-prosessen per samtidige forespørsel, mot ~256 KiB / ~6
    # MiB for alle andre ruter: en uautentisert flate 10-250x større enn
    # noen annen. Rate-grensen i `_autentiser` hjalp ikke, for den ligger
    # BAK auth-en som ikke hadde skjedd ennå.
    #
    # Transaksjonen her ser kun headere og cookies, og slippes tilbake til
    # poolen (rollback i `gi_tilbake`) før første byte av kroppen leses.
    def autentiser(conn):
        return _browserkontekst(tjeneste, request, conn, rid,
                                "bestilling:opprett")

    kontekst = _med_conn(tjeneste, rid, autentiser)
    if not isinstance(kontekst, tuple):
        return kontekst      # ferdig kodet feilsvar: 401/403/csrf/drift
    tenant, bid = kontekst

    # Kroppen finnes bare én gang, og leses først NÅ — etter at det er
    # avgjort at avsenderen har lov til å sende den. Taket håndheves to
    # steder med samme tall: middleware-telleren (transport) og samlingen
    # her (kontrakt) — reservasjonens eget tak møter målingen i
    # 058-funksjonen til slutt.
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
        # Auth er alt avgjort over. Her settes bare `disponit.*` på nytt:
        # `sett_kontekst` er SET LOCAL og lever ikke på tvers av
        # forbindelser, og dette er en ANNEN forbindelse enn auth-en
        # brukte. Å kjøre `_browserkontekst` en gang til ville brent en
        # ekstra rate-grense-enhet på den samme forespørselen.
        _gjenopprett_kontekst(conn, tenant, bid, rid)
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
                "SELECT ut_inndata_id, ut_lager_sti FROM"
                " registrer_inndata_lastet(%s,%s,%s,%s,%s,%s,%s)",
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
        # Replay: 058 svarte med en ANNEN sti enn den vi nettopp skrev,
        # altså sto raden alt som `lastet` med samme sha — svaret er det
        # samme (sha-en er den samme kroppen), men filen vår er en orphan
        # og ryddes her. En unlink som ikke går skal ikke gjøre en
        # vellykket opplasting til en 500; da er den reaperens jobb.
        if rad[1] != sti:
            try:
                os.unlink(sti)
            except OSError:
                pass
        return _ok({"inndata_ref": f"inndata:{rad[0]}",
                    "innhold_sha256": sha, "faktiske_bytes": lest}, rid,
                   201)

    return _med_conn(tjeneste, rid, kjor)
