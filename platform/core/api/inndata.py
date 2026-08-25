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


def _krev_ferskt_snapshot(conn) -> str:
    """Opplastingens transaksjon må se FERSKE data (Codex P1 på #196).

    Finaliseringen avgjør skriv-eller-gjenspill på en lesning gjort UNDER
    advisory-låsen på jti-en — hele poenget med den låsen. Men lesningen
    er bare fersk i READ COMMITTED. Under `REPEATABLE READ`/`SERIALIZABLE`
    fikserer den FØRSTE setningen i transaksjonen (DEK-oppslaget) hele
    snapshotet, og gjenlesningen etter låsen svarer da fra det samme
    fastfrosne bildet:

      1. To opplastinger på samme jti starter mens raden står `reservert`.
      2. Den første tar låsen, skriver den kanoniske filen, committer.
      3. Den andre får låsen, ser fortsatt `reservert` i sitt gamle
         snapshot, og skriver SIN ciphertext over den førstes fil.
      4. `registrer_inndata_lastet` låser raden `FOR UPDATE`, oppdager at
         den er endret, og reiser serialiseringsfeil — hvorpå
         opprydningen unlinker nettopp den kanoniske filen.

    Resultatet er en committet `lastet` rad uten fil: en vellykket
    opplasting permanent tapt. Låsen var aldri feilen — snapshotet var.

    `reserver_inndata` avviser de samme nivåene av samme grunn (059), og
    poolen kjører på basens default (READ COMMITTED), så porten er ingen
    oppførselsendring — den er fail-closed mot en fremtidig kaller eller
    en pool-konfigurasjon som setter nivået selv. `read uncommitted` er
    med fordi PostgreSQL BEHANDLER det som READ COMMITTED.

    Provisjoneringsfeil, ikke klientfeil — samme klasse som
    `_stikomponent`, og derfor `ValueError` FØR all I/O.
    """
    niva = conn.execute("SHOW transaction_isolation").fetchone()[0]
    if niva not in ("read committed", "read uncommitted"):
        raise ValueError(
            f"inndata: opplastingen krever READ COMMITTED (fikk {niva})"
            " — skriv-eller-gjenspill avgjøres av en lesning under"
            " advisory-låsen, og et fastholdt snapshot gjør den blind for"
            " en samtidig committet opplasting")
    return niva


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
        # FØR ALT ANNET: gjenlesningen under låsen lenger nede er bare
        # fersk i READ COMMITTED, og et fastholdt snapshot fikseres av
        # den FØRSTE setningen i transaksjonen. Porten må derfor stå her,
        # foran DEK-oppslaget — se `_krev_ferskt_snapshot`.
        _krev_ferskt_snapshot(conn)
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
        # B-maskinen (059): raden BÆRER den relative stien fra fødselen —
        # API-et velger aldri. Fast sti gjør samtidige opplastinger på
        # samme jti til et EKTE kappløp om én fil, så skriverne
        # serialiseres med en advisory-transaksjonslås på jti-en (låses
        # her, slippes av commit/rollback i _med_conn). Gjenspill
        # (`lastet`) skriver ALDRI: filen på disk hører til radens nonce,
        # og en overskriving med ny nonce hadde korruptert den — døren
        # validerer sha-en og svarer med det som står.
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                     (f"inndata:{tenant}:{jti}",))
        # Status og sti leses PÅ NYTT her, UNDER låsen — forsjekken i auth
        # gikk på en annen forbindelse før kroppen ble strømmet, og en
        # samtidig opplasting på samme jti kan ha fullført i mellomtiden.
        # Å avgjøre skriv-eller-gjenspill på den foreldede lesningen var
        # nettopp overskrivingen låsen skal hindre.
        rad = conn.execute(
            "SELECT status, lager_sti FROM inndata_artefakt"
            " WHERE tenant = %s AND reservasjon_jti = %s",
            (tenant, jti)).fetchone()
        if rad is None or rad[0] not in ("reservert", "lastet"):
            raise _Avbrudd(_feil("inndata_reservasjon_ugyldig", rid, 409))
        status_naa, rel = rad
        # Forsvar i dybden: komponentsjekken består selv om stien nå er
        # dørens — en rad noen ANNEN skrev skal fortsatt aldri nå join.
        komp = _stikomponent(tenant)
        if not rel or not rel.startswith(komp + "/"):
            raise _Avbrudd(_feil("intern_feil", rid, 500))
        katalog = os.path.join(INNDATA_ROT, komp)
        os.makedirs(katalog, mode=0o700, exist_ok=True)
        sti = os.path.join(INNDATA_ROT, rel)
        if status_naa == "lastet":
            # Samme errcode-kart som hovedveien under — gjenspillet kan
            # felles av utløpet (`invalid_parameter_value`) eller av en
            # ANNEN kropp på samme jti (`unique_violation`), og ingen av
            # dem skal bli en 500.
            try:
                rad = conn.execute(
                    "SELECT ut_inndata_id, ut_lager_sti FROM"
                    " registrer_inndata_lastet(%s,%s,%s,%s,%s,%s)",
                    (tenant, jti, lest, sha, key_id, nonce)).fetchone()
            except psycopg.errors.InvalidParameterValue as e:
                raise _Avbrudd(_feil("inndata_reservasjon_ugyldig", rid,
                                     409)) from e
            except psycopg.errors.UniqueViolation as e:
                raise _Avbrudd(_feil("inndata_alt_lastet", rid, 409)) from e
            conn.commit()
            return _ok({"inndata_ref": f"inndata:{rad[0]}",
                        "innhold_sha256": sha, "faktiske_bytes": lest},
                       rid, 201)
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
        # `sti` er radens faste navn, men advisory-låsen over gjør oss til
        # eneste skriver på den akkurat nå, så begge navnene er våre alene;
        # unlinken er best effort fordi den opprinnelige feilen er den som
        # skal nå kalleren.
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
                " registrer_inndata_lastet(%s,%s,%s,%s,%s,%s)",
                (tenant, jti, lest, sha, key_id, nonce)).fetchone()
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
        # Under B er `ut_lager_sti` per konstruksjon radens fødselssti —
        # den samme `rel` vi nettopp leste under låsen og skrev til. 058s
        # orphan-rydding her («døren svarte med en annen sti») er derfor
        # borte: et avvik ville betydd at DØREN var i utakt med sin egen
        # rad, og da er en unlink av den kanoniske stien sletting av en
        # nettopp committet fil — verre enn enhver orphan.
        return _ok({"inndata_ref": f"inndata:{rad[0]}",
                    "innhold_sha256": sha, "faktiske_bytes": lest}, rid,
                   201)

    return await run_in_threadpool(_med_conn, tjeneste, rid, kjor)


def hent_endepunkt(tjeneste, request, kropp):
    """POST /v1/inndata/hent-for-oppdrag/{oppdrag_id} — modulens
    lesevei (060, B-formen fra #200).

    POST, ikke GET (rutescope-regelen: modulveien er ORDRESCOPE-klassen
    som claim/kvittering — hentingen FORBRUKER deploymentens autoritet).

    Autorisasjonen er CLAIMET, og oppslaget går via modulens EGET
    oppdrag: bindingsraden er den eneste sannheten om hvilken bunt
    oppdraget eier (#200 valg B) — ingen payload-referanse finnes.
    Bytene dekrypteres og SHA-verifiseres mot radens måling FØR de
    forlater huset: en fil som har driftet fra sin egen deklarasjon
    serveres aldri.
    """
    import hashlib as _h
    import os

    import psycopg
    from starlette.responses import Response

    from .app import (ModulAutentisert, _feilsvar, _modultoken_revalidert,
                      _rid, preauth)
    rid = _rid(request)
    oppdrag_id = request.path_params["oppdrag_id"]
    # Claimet er en KAPABILITET (CodeRabbit major): kalleren må
    # presentere owner_claim_id-en claim-svaret ga — to deployments av
    # samme modul skal ikke kunne hente hverandres bunter.
    #
    # `isinstance(..., dict)`, ikke `kropp or {}` (Codex P2): ruten tar
    # imot vilkårlig JSON, og `[1]`/`"x"`/`3`/`true` er alle sanne og
    # uten `.get` — `AttributeError` fløy da ut FØR autentiseringen og
    # ble en ufanget 500 i stedet for kontraktens 400. Samme form som
    # kvitteringsveien alt bruker (app.py: `isinstance(kvittering, dict)`).
    # Selve verdien valideres først etter auth, som før: en uautentisert
    # kaller skal ikke kunne skille «feil kropp» fra «feil token».
    owner_claim = kropp.get("owner_claim_id") if isinstance(kropp, dict) \
        else None
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    from db import kryptering
    from db.pg import sett_kontekst
    # BASEFASEN er avgrenset, og forbindelsen forlater den (Codex P2 +
    # Cursor P2-2). To ting kommer ut av avgrensningen:
    #
    # 1. `psycopg.Error` ETTER `pool.hent()` — en frakoblet base under
    #    pre-auth, revalidering, resolveroppslaget eller nøkkellesningen —
    #    fanges og blir endepunktets `db_utilgjengelig`, som på hver
    #    annen modulvei (app.py: `_ingest_kvittering`). Uten dette var en
    #    helt ordinær driftshendelse en generisk 500 uten hendelsen.
    # 2. Forbindelsen leveres TILBAKE til poolen før fil-I/O og
    #    dekryptering. Å holde en pool-forbindelse gjennom inntil 64 MiB
    #    `read` + AES-GCM er samme klasse som opplastveien alt lukket:
    #    under samtidige hent tømmes poolen, og andre forespørsler får
    #    `db_utilgjengelig` av en årsak som ikke har noe med basen å gjøre.
    grunnlag = None
    try:
        try:
            auth = preauth(tjeneste, conn,
                           request.headers.get("authorization"), rid)
            if not isinstance(auth, ModulAutentisert):
                tjeneste.logg.hendelse("token_ugyldig", rid)
                return _feilsvar("token_ugyldig", rid)
            # Samme to porter som hver annen modulvei: raten, og
            # REVALIDERINGEN — et nødstoppet token skal ikke kunne hente
            # bunter over transaksjonsgrensen.
            if not tjeneste.rate.slipp_gjennom(auth.token_id):
                tjeneste.logg.hendelse("rate_grense", rid, auth.tenant)
                return _feilsvar("rate_grense", rid)
            revalidering = _modultoken_revalidert(tjeneste, conn, auth, rid)
            if revalidering is not None:
                return revalidering
            if not isinstance(owner_claim, str) or not owner_claim:
                return _feilsvar("request_feilformet", rid)
            # Deploymenten kommer fra TOKENET, aldri fra kroppen (Codex
            # P1): `auth.release_id`/`auth.miljo` er det verifiserte
            # modultokenets egen identitet (035), og 060 møter dem mot
            # claim-portens stempel på oppdraget. Uten dem var
            # kapabiliteten «en hvilken som helst deployment av modulen
            # som kjenner claim-strengen».
            rad = conn.execute(
                "SELECT tenant, lager_sti, key_id, nonce, innhold_sha256,"
                "       faktiske_bytes FROM"
                "       hent_inndata_for_oppdrag(%s::bigint,%s,%s,%s,%s)",
                (oppdrag_id, auth.modul_id, owner_claim,
                 auth.release_id, auth.miljo)).fetchone()
            if rad is None:
                # Samme svar uansett årsak — finnes-ikke, feil modul,
                # ikke claimet, ubundet: et oppslagsverk over andres
                # oppdrag skal ikke finnes.
                return _feilsvar("ikke_funnet", rid)
            tenant, rel, key_id, nonce, sha, faktiske = rad
            sett_kontekst(conn, tenant, auth.aktor, rid)
            nokkelrad = conn.execute(
                "SELECT wrapped_dek FROM tenant_nokler"
                " WHERE tenant=%s AND key_id=%s",
                (tenant, key_id)).fetchone()
            # ALLE autorisasjons- og oppslagslesningene over deler ÉN
            # transaksjon (CodeRabbit major: en mellom-rollback slapp
            # revalideringens garanti før oppslaget var gjort). Først NÅR
            # basen er ferdig spurt, slippes transaksjonen — fil-I/O og
            # dekryptering skal ikke holde den.
            conn.rollback()
            if nokkelrad is None or nokkelrad[0] is None:
                return _feilsvar("tenantnokkel_mangler", rid)
            dek = kryptering._pakk_ut((key_id, nokkelrad[0]), tenant)[1]
            grunnlag = (tenant, rel, key_id, nonce, sha, faktiske, dek)
        except psycopg.Error as e:
            # Rull tilbake her, som de andre modulveiene — men SKJERMET:
            # den vanligste årsaken til at vi står her er nettopp en død
            # forbindelse, og en `rollback()` som selv kaster ville gjort
            # driftshendelsen om til den 500-en dette funnet handler om.
            # `gi_tilbake` under rydder uansett (app.py:206-222).
            try:
                conn.rollback()
            except psycopg.Error:
                pass
            tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                                   feiltype=type(e).__name__)
            return _feilsvar("db_utilgjengelig", rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)

    # HERFRA HOLDER VI INGEN BASEFORBINDELSE.
    tenant, rel, key_id, nonce, sha, faktiske, dek = grunnlag
    # Radens sti er RELATIV (B-maskinen: <tenant>/<inndata_id>.bin);
    # roten settes på her, med komponentvakten som forsvar i dybden.
    komp = _stikomponent(tenant)
    if not rel or not rel.startswith(komp + "/"):
        tjeneste.logg.hendelse("inndata_sti_avvik", rid, tenant,
                               art="sikkerhet")
        return _feilsvar("intern_feil", rid)
    try:
        with open(os.path.join(INNDATA_ROT, rel), "rb") as f:
            ct = f.read()
    except OSError:
        tjeneste.logg.hendelse("inndata_fil_borte", rid, tenant,
                               art="drift")
        return _feilsvar("intern_feil", rid)
    raa = kryptering.dekrypter_bytes(dek, ct, bytes(nonce), tenant,
                                     key_id, formaal=b"inndata")
    if _h.sha256(raa).hexdigest() != sha:
        tjeneste.logg.hendelse("inndata_sha_avvik", rid, tenant,
                               art="sikkerhet")
        return _feilsvar("intern_feil", rid)
    return Response(raa, media_type="application/zip",
                    headers={"x-request-id": rid,
                             "x-innhold-sha256": sha})
