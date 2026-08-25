"""#162: inndata-artefaktet — buntens vei INN, PR-1 (reservasjon +
opplasting).

To ruter, speilet av 017s utdata-form i motsatt retning:

* POST /v1/inndata/reserver (browserkontekst, `bestilling:opprett`,
  `Idempotency-Key` PÅKREVD): utsteder en engangs-reservasjon FØR
  opplasting. Taket er KONTRAKTENS (`INNDATA_MAKS_FYSISK` i denne v1-en)
  — klienten ber aldri om et tall. Nøkkelen bæres av RADEN (058), så en
  retry etter et tapt 201 får den samme referansen og jti-en tilbake.
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
from starlette.concurrency import run_in_threadpool

#: FS-roten. Api-unitens EGEN `StateDirectory=disponit-inndata` (Codex P1):
#: /var/lib/disponit er ryddeunitens state-katalog, eid av
#: `disponit-domener` med 0750, og en gren under den kunne API-brukeren
#: verken tas eierskap over eller traverseres ned i. opp.sh oppretter den
#: også ved førstegangsdeploy; en manglende rot er en deploy-feil og skal si
#: det, ikke ENOENT dypt nede.
INNDATA_ROT = os.environ.get("DISPONIT_INNDATA_ROT",
                             "/var/lib/disponit-inndata")


def _stikomponent(tenant: str) -> str:
    """Tenant-ID-en som ÉN trygg stikomponent, ellers stopp (Codex P2).

    `brukermedlemskap.tenant` er ubegrenset `TEXT`, og dette er det eneste
    stedet i repoet der tenant-strengen blir til en FILSTI — alle andre
    bruk (`kryptering._aad`, `pg.policylasnokkel`, idempotensnøkler) legger
    den i en streng der form ikke betyr noe. Starter den med `/`, kaster
    `os.path.join(INNDATA_ROT, tenant)` roten, og den samme `rel`/`sti`-
    konstruksjonen under gjør det igjen: bunten havner UTENFOR unitens
    state-katalog og kan forsvinne uavhengig av basen.

    Navnerom-CHECKen i 058 fanger det ikke, og kan ikke: den sammenligner
    `lager_sti` mot NØYAKTIG den samme tenant-strengen stien ble bygget av
    (`left(lager_sti, length(tenant)+1) = tenant||'/'`), så `/tmp/acme`
    + `/tmp/acme/<uuid>.bin` passerer. En vakt som måler en verdi mot
    strengen den selv er avledet fra, kan ikke se at strengen rømmer.

    Positiv form, ikke svarteliste: komponenten må være seg selv etter
    `basename`. Det avviser `/`, tomt, `.`, `..` og NUL i ett uttrykk.
    Feilen er en provisjoneringsfeil, ikke en klientfeil — den skal aldri
    kunne skrive noe, derfor reises den FØR all I/O.
    """
    if not tenant or "\x00" in tenant or tenant in (".", "..") \
            or os.path.basename(tenant) != tenant:
        raise ValueError("inndata: tenant-ID er ikke en trygg stikomponent")
    return tenant


def reserver_endepunkt(tjeneste, request):
    """POST /v1/inndata/reserver — {eiermodul, formaal} + Idempotency-Key."""
    from .app import INNDATA_MAKS_FYSISK, _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        # `Idempotency-Key` er PÅKREVD (Codex P2). Reservasjonen er en
        # opprettelse som ikke er naturlig idempotent: gikk 201-svaret tapt
        # på veien ut — eller ga `commit()` en tvetydig forbindelsesfeil —
        # hadde klienten ingenting å slå den genererte `inndata_ref` og
        # jti-en opp med, og en retry laget en ANDRE levende reservasjon
        # med en annen referanse mens den første lå uleselig til reaperen
        # tok den. Samme krav som de andre opprettelsesrutene, samme
        # helper.
        idem = _krev_idem(request, rid)
        kropp = _kropp(request)
        eiermodul, formaal = kropp.get("eiermodul"), kropp.get("formaal")
        # Lukket sett: i v1 finnes nøyaktig én lovlig kombinasjon, og en
        # ny modul/nytt formål er en KONTRAKTSENDRING (058-CHECKene sier
        # det samme — dette er bare den tidlige, lesbare avvisningen).
        if (eiermodul, formaal) != ("m57_ats", "soknadsbunt"):
            raise _Avbrudd(_feil("request_feilformet", rid))
        try:
            rad = conn.execute(
                "SELECT inndata_id, reservasjon_jti FROM"
                " reserver_inndata(%s,%s,%s,%s,%s)",
                (tenant, eiermodul, formaal, INNDATA_MAKS_FYSISK,
                 idem)).fetchone()
        except psycopg.errors.UniqueViolation as e:
            # Nøkkelen er brukt for en ANNEN reservasjon. Gjenspill av den
            # SAMME svarer 058 med den opprinnelige raden, så det er bare
            # kollisjonen som når hit.
            raise _Avbrudd(_feil("idempotenskonflikt", rid)) from e
        except psycopg.errors.InvalidParameterValue as e:
            # Nøkkellengde/kombinasjon avvist av kontrakten i SQL.
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
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
    #
    # `run_in_threadpool` av samme grunn som finaliseringen nederst (Codex
    # P1): dette er den ENESTE `async def`-ruten i API-et, og alt db-
    # arbeidet under er blokkerende (`pool.hent` venter med timeout,
    # psycopg er synkron). Uten tråden ville en treg base stanset HELE
    # event-loopen — også `/live` og alle andre samtidige forespørsler.
    # Starlette kjører sync-ruter i threadpoolen selv; denne ruten er
    # async fordi den må lese `request.stream()`, og betaler derfor for
    # den vekslingen eksplisitt.
    def autentiser(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        # RESERVASJONEN SEES OGSÅ FØR KROPPEN (Codex P2, runde 8). Auth
        # avgjorde at avsenderen har LOV til å sende 64 MiB; den sa
        # ingenting om at det finnes noe å sende dem TIL. En ukjent,
        # utløpt eller alt forbrukt jti — en gammel lenke, en retry etter
        # fristen, en avkortet token — ble derfor strømmet, hashet,
        # kryptert, skrevet, fsynket i to katalognivåer, og FØRST DA
        # avvist av `registrer_inndata_lastet`. Den mest forutsigbare
        # feilen på ruten var samtidig den dyreste: full pris i minne,
        # CPU og disk-I/O for et svar vi kunne gitt på ett indeksoppslag.
        #
        # Sjekken FORBRUKER ingenting og AVGJØR ingenting: 058 eier
        # fortsatt engangs-semantikken, låser raden `FOR UPDATE` og gjør
        # den samme vurderingen om igjen ved registreringen — kappløpet
        # mellom denne lesningen og den låste skrivingen er nettopp
        # derfor ufarlig. Dette er en billig FORHÅNDSAVVISNING, ikke en
        # ny port. Den kan bare si nei til det 058 uansett sier nei til.
        #
        # Grensesnittet er ett ord: `inndata_reservasjon_ugyldig`, det
        # samme som døren gir for alle tre dødtilstandene
        # (`feil.py:233-237`: «ukjent, utløpt eller alt forbrukt» skal ha
        # SAMME svar, ellers er svaret et orakel på hvilke jti-er som
        # finnes). `lastet` slipper igjennom fordi et gjenspill med samme
        # kropp er en LOVLIG forespørsel — hash-grenen krever kroppen og
        # blir liggende der den er.
        #
        # RLS (`tenant_isolasjon`, 058:278) snevrer lesningen til
        # kallerens egen tenant; `tenant`-predikatet står likevel
        # eksplisitt, både fordi det treffer `inndata_jti_en_gang`-
        # indeksen og fordi en tenantvakt ikke skal være usynlig.
        rad = conn.execute(
            "SELECT status, pg_catalog.now() > utloper"
            "  FROM inndata_artefakt"
            " WHERE tenant = %s AND reservasjon_jti = %s",
            (tenant, jti)).fetchone()
        if rad is None or rad[1] or rad[0] not in ("reservert", "lastet"):
            raise _Avbrudd(_feil("inndata_reservasjon_ugyldig", rid, 409))
        return tenant, bid

    kontekst = await run_in_threadpool(_med_conn, tjeneste, rid, autentiser)
    if not isinstance(kontekst, tuple):
        return kontekst      # ferdig kodet feilsvar: 401/403/csrf/409/drift
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

    # Finaliseringen: pool-uttak, DEK-oppslag, AES-GCM over hele bufferet
    # (inntil 64 MiB), filskriving, to fsync-er og en sync-commit — alt
    # blokkerende, og alt på den ENE event-loopen dersom den kalles
    # direkte fra denne async-ruten (Codex P1). Én stor, vellykket
    # opplasting ville da forsinket hver eneste samtidige forespørsel,
    # `/live` inkludert. Den går derfor i threadpoolen, der Starlette
    # uansett kjører alle sync-rutene i dette API-et.
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
        # FØR første I/O: tenant-strengen må være én trygg stikomponent.
        # `komp` brukes i BEGGE sammensetningene under — den rå `tenant`
        # skal ikke nå `os.path.join` noe sted i denne funksjonen.
        komp = _stikomponent(tenant)
        katalog = os.path.join(INNDATA_ROT, komp)
        os.makedirs(katalog, mode=0o700, exist_ok=True)
        # RADEN bærer den RELATIVE stien, `<tenant>/<uuid>.bin` (Cursor P1
        # runde 2); roten settes på her og bare her. Med roten i raden
        # måtte 058 kjent den for å kunne anker-sjekke navnerommet, og
        # gjorde det ikke — den lette etter `/<tenant>/` som delstreng, som
        # en sti ned i en FREMMED tenants katalog også inneholder.
        rel = os.path.join(komp, f"{uuidlib.uuid4()}.bin")
        sti = os.path.join(INNDATA_ROT, rel)
        # Skriv-og-flytt: en halvskrevet fil skal aldri kunne bli en
        # gyldig referanse.
        tmp = sti + ".tmp"
        # HELE I/O-en før registreringen rydder etter seg (Codex P2). Den lå
        # utenfor `try`-en under, så en full disk (ENOSPC i `write`/`fsync`)
        # reiste FØR ryddingen fantes og etterlot en delvis `.tmp`; en feil i
        # katalog-fsyncen etter `os.replace` etterlot en komplett, foreldreløs
        # `.bin`. Ingen av dem har en rad, ingen av dem har en eier, og en
        # klient som prøver på nytt under den samme lagerfeilen legger på en
        # ny for hvert forsøk — feilen som fylte disken spiser altså mer disk.
        # `sti` er en fersk uuid i denne kallet, så begge navnene er våre
        # alene; unlinken er best effort fordi den opprinnelige feilen er den
        # som skal nå kalleren.
        try:
            with open(tmp, "wb") as f:
                f.write(ct)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, sti)
            # Å fsync-e FILEN gjør ikke KATALOGOPPFØRINGEN varig (Codex P2):
            # mister verten strømmen etter db-commiten, men før
            # katalogmetadataen har nådd stabilt lager, står raden igjen som
            # `lastet` mens den omdøpte ciphertexten er borte etter omstart.
            # Samme grep som `kjor_artefaktrydding._skriv_feiltelling`, men
            # IKKE best effort her: feiler den, har vi ennå ikke committet, og
            # ryddingen her tar filen. En bunt vi ikke kan love er varig, skal
            # ikke kvitteres som lastet.
            #
            # BEGGE nivåene, ikke bare barnet (Codex P2 runde 5): første
            # opplasting for en tenant OPPRETTER `katalog` her. Å fsync-e
            # `katalog` gjør filens oppføring I den varig — ikke katalogens
            # egen oppføring i `INNDATA_ROT`. Et strømbrudd etter commiten
            # kunne dermed ta hele tenantkatalogen og etterlate den samme
            # `lastet`-raden uten fil. Roten fsync-es ubetinget og ikke bare
            # når `makedirs` skapte katalogen: to samtidige førsteopplastinger
            # ser hver sin halvdel av den betingelsen, og en fsync av en
            # uendret katalog koster ingenting mot 64 MiB ciphertext.
            for kat_sti in (katalog, INNDATA_ROT):
                kat = os.open(kat_sti, os.O_RDONLY)
                try:
                    os.fsync(kat)
                finally:
                    os.close(kat)
        except Exception:
            for spor in (tmp, sti):
                try:
                    os.unlink(spor)
                except OSError:
                    pass
            raise
        try:
            rad = conn.execute(
                "SELECT ut_inndata_id, ut_lager_sti FROM"
                " registrer_inndata_lastet(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, jti, lest, sha, key_id, nonce, rel)).fetchone()
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
        # `commit()` står UTENFOR ryddingen over (Codex P1). En commit som
        # reiser er TVETYDIG: forbindelsen kan ha falt etter at Postgres
        # tok imot COMMIT, men før kvitteringen kom tilbake. Lå unlinken i
        # den samme except-en, ville en committet `lastet` rad blitt
        # stående og pekt på en ciphertext vi nettopp slettet — en
        # vellykket opplasting permanent tapt. Feilene over er derimot
        # ENTYDIGE: setningen selv feilet, transaksjonen er abortert, og
        # ingenting ble committet — der er filen trygt en orphan.
        #
        # Prisen er motsatt vei: ruller commiten likevel tilbake, ligger
        # en foreldreløs `.bin` igjen. Det er reaperens arbeid (egen PR),
        # og en orphan-fil er en billigere feil enn tapte data.
        conn.commit()
        # Replay: 058 svarte med en ANNEN sti enn den vi nettopp skrev,
        # altså sto raden alt som `lastet` med samme sha — svaret er det
        # samme (sha-en er den samme kroppen), men filen vår er en orphan
        # og ryddes her. En unlink som ikke går skal ikke gjøre en
        # vellykket opplasting til en 500; da er den reaperens jobb.
        if rad[1] != rel:
            try:
                os.unlink(sti)
            except OSError:
                pass
        return _ok({"inndata_ref": f"inndata:{rad[0]}",
                    "innhold_sha256": sha, "faktiske_bytes": lest}, rid,
                   201)

    return await run_in_threadpool(_med_conn, tjeneste, rid, kjor)
