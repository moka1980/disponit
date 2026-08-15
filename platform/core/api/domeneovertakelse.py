"""PR-014b CP2b: idempotent M-37-sak ved domeneovertakelse (B4).

`verifiser_domenekontroll` returnerer `'konflikt:<tapt-tenant>'` når en aktiv
verifisering overtas: DB-en fjerner A (tilbakekalt) og setter B `avklaring_kreves`
i samme transaksjon, men gir ALDRI B autorisasjonen der og da. Denne modulen
oppretter den ENE M-37-saken (familie `domeneovertakelse`) som avgjøres i
unntaksbehandlingen (PR-012) og til slutt kaller `avgjor_domeneovertakelse`.

Samme signal kommer når en AVVIST kandidat søker på nytt (DB-en bærer motparten
på raden i `konflikt_motpart`): ny generasjon, og dermed en ny sak — ellers ble
reapplikasjonen stående i `avklaring_kreves` uten noen sak som kunne avgjøre den.

Saken er idempotent PER overtakelsesgenerasjon: samme konflikt (samme B-rads
`autorisasjonsgenerasjon`) gir SAMME sak; en ny overtakelse (ny, monoton
generasjon) gir en ny sak. Generasjonen i idempotensnøkkelen er nettopp det som
lar en terminal sak ligge urørt mens en fremtidig, uavhengig konflikt får sin
egen sak — «gjenbruk kun av ikke-terminal, samme familie; terminale saker endres
aldri» faller ut av at nøkkelen er unik per konflikt.

Saken er `sakstype='sikkerhet'` (normalarbeideren claimer den aldri) med
`UKJENT_SNAPSHOT` (maks_auto_forsok=0 → kan ikke auto-behandles): en overtakelse
er per definisjon en menneskelig/sikkerhetsavgjørelse.
"""
import hashlib
import json

from api import kjerne

#: PR-015 §3: EGET scope. `exceptions:handle` alene gir ALDRI cross-tenant
#: domeneautoritet — den som kan behandle unntak skal ikke dermed kunne avgjøre
#: hvem plattformen autoriserer for et domene. Port 13 måler nettopp det.
ADJUDIKASJONSSCOPE = "domains:adjudicate"

#: Familien saken merkes med (unntak.kategori). Lineage til begge domenerader
#: ligger i den krypterte payloaden.
FAMILIE = "domeneovertakelse"

#: Handlingen saken bærer (unntak.handling). Sammen med FAMILIE er dette det
#: som skiller VÅRE rader fra alt annet som deler idempotensnavnerommet.
HANDLING = "domene.overtakelse"


def idempotensnokkel(hostname: str, generasjon: int) -> str:
    return f"{FAMILIE}:{hostname}:{generasjon}"


def opprett_overtakelsessak(conn, *, tenant_ny: str, hostname: str,
                            tenant_tapt: str, generasjon: int,
                            aktor: str) -> int:
    """Opprett (eller gjenbruk) overtakelsessaken. Returnerer `unntak_id`.

    Kalleren MÅ ha satt `disponit.tenant = tenant_ny` (RLS). `generasjon` er
    B-radens `autorisasjonsgenerasjon` etter overtakelsen — monoton, altså unik
    per konflikt.

    `hostname` er alltid kanonisk her: nøkkelen bygges på det samme navnet som
    ble sendt til `verifiser_domenekontroll`, og migrasjon 018 (§0) avviser
    enhver annen tekstlig form FØR konflikten i det hele tatt kan oppstå. Det
    er nettopp derfor §0 validerer i stedet for å normalisere — ellers kunne to
    former av samme navn gitt to idempotensnøkler for én konflikt.

    Kaller ALLTID `degrader_forbigatte_utfordrere` FØRST (Codex): dette er den
    reelle kalleren `verifiser_domenekontroll()` sin `konflikt:*`-gren mangler
    (016 lar en forbigått B stå urørt i `avklaring_kreves` når en tredje C tar
    over). Uten kallet her fantes det INGEN produksjonsvei til funksjonen —
    kun tester ringte den. Idempotent og hostname-låst i seg selv, så et
    retry på en allerede eksisterende sak endrer ingenting.
    """
    conn.execute("SELECT degrader_forbigatte_utfordrere(%s, %s)",
                 (hostname, aktor))
    key = idempotensnokkel(hostname, generasjon)
    # Codex: serialiser på den avledede nøkkelen. `revisjonslogg` har kun en
    # IKKE-unik indeks på (tenant, idempotency_key), så to samtidige retry-arbeidere
    # kunne begge se «ingen rad» og opprette hver sin sak. Advisory-låsen (transaks-
    # jonsscopet) gjør sjekk-og-opprett atomisk per (tenant, hostname, generasjon).
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (tenant_ny + ":" + key,))
    # Codex: slå opp SAKEN direkte, scopet til overtakelsesfamilien — ikke en
    # vilkårlig loggpost med samme nøkkel. `revisjonslogg.idempotency_key` er et
    # DELT, KALLERSTYRT navnerom (`/v1/beslutning` skriver klientens
    # Idempotency-Key rett inn) med kun en IKKE-unik indeks. Gikk oppslaget via
    # loggposten først, kunne en fremmed rad som alt het
    # `domeneovertakelse:<hostname>:<generasjon>` kapre idempotensen på to måter:
    # uten `unntak` fant vi ingen sak og opprettet en NY ved hvert retry (én
    # konflikt → mange M-37-saker), og MED et urelatert `unntak` returnerte vi
    # den fremmede saken som om den var overtakelsessaken — og konflikten fikk
    # aldri sin egen sak, mens B ble stående i `avklaring_kreves` for alltid.
    # Joinen bærer både `kilde`/`kategori` og `handling`, altså nøyaktig det
    # denne funksjonen selv skriver; en fremmed rad kan ikke matche.
    sak = conn.execute(
        "SELECT u.id FROM unntak u"
        " JOIN revisjonslogg r ON r.tenant = u.tenant AND r.id = u.loggpost_id"
        " WHERE u.tenant=%s AND u.kategori=%s AND u.handling=%s"
        "   AND r.kilde=%s AND r.idempotency_key=%s"
        " ORDER BY u.id LIMIT 1",
        (tenant_ny, FAMILIE, HANDLING, FAMILIE, key)).fetchone()
    if sak is not None:
        return int(sak[0])   # idempotent: saken finnes alt for denne konflikten

    loggpost = int(conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash, policy_id,"
        " beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,%s,%s,%s,%s,'UNNTAK','[]',%s) RETURNING id",
        (tenant_ny, aktor, FAMILIE,
         hashlib.sha256(key.encode()).hexdigest(), FAMILIE, key)).fetchone()[0])

    payload = {
        "hostname": hostname,
        "tenant_tapt": tenant_tapt,     # A — bevis bevart, men tilbakekalt
        "tenant_ny": tenant_ny,         # B — avklaring_kreves inntil avgjørelse
        "generasjon": generasjon,
        "familie": FAMILIE,
    }
    return kjerne._skriv_unntak(
        conn, tenant_ny, loggpost, handling=HANDLING,
        kategori=FAMILIE, sakstype="sikkerhet", prioritet="hoy",
        payload=payload, snapshot=kjerne.UKJENT_SNAPSHOT)


def slaa_opp_sak(conn, tenant: str, unntak_id: int) -> tuple[str, int] | None:
    """(hostname, saksrevisjon) for en overtakelsessak. None hvis det ikke ER en.

    Leses av IDEMPOTENSNØKKELEN, ikke av den krypterte payloaden:
    `domeneovertakelse:<hostname>:<generasjon>` er nøyaktig de to feltene vi
    trenger, i klartekst, og den er skrevet av `opprett_overtakelsessak` selv.
    Å dekryptere payloaden for å lese to felter som alt står i nøkkelen ville
    vært en omvei med en DEK i hånda.

    Joinen bærer `kategori`/`handling`/`kilde` slik oppslaget i
    `opprett_overtakelsessak` gjør — en fremmed rad i det DELTE
    idempotensnavnerommet kan ikke matche og dermed ikke låne seg
    adjudikasjonsveien.
    """
    rad = conn.execute(
        "SELECT r.idempotency_key FROM unntak u"
        " JOIN revisjonslogg r ON r.tenant = u.tenant AND r.id = u.loggpost_id"
        " WHERE u.id=%s AND u.tenant=%s AND u.kategori=%s AND u.handling=%s"
        "   AND r.kilde=%s",
        (unntak_id, tenant, FAMILIE, HANDLING, FAMILIE)).fetchone()
    if rad is None:
        return None
    key = rad[0] or ""
    # `<familie>:<hostname>:<generasjon>` — hostnavnet er kanonisk (018 avviser
    # alt annet før konflikten kan oppstå), så det inneholder aldri kolon.
    biter = key.split(":")
    if len(biter) != 3 or biter[0] != FAMILIE:
        return None
    try:
        return biter[1], int(biter[2])
    except ValueError:
        return None


def attester_endepunkt(tjeneste, request, unntak_id: int):
    """POST /v1/unntak/{id}/domeneattestasjon — fire øyne ved positiv tildeling.

    Endepunktet SKRIVER ALDRI STATUS (invariant 3). Det avgir én attestasjon;
    `avgi_overtakelse_attestasjon()` teller under hostname-låsen og kaller
    `avgjor_domeneovertakelse()` når terskelen er nådd:

        avvis    → ÉN attestasjon
        godkjenn → TO DISTINKTE aktører

    Blir det ikke avgjort, er svaret `krever_to_attestasjoner` MED antall
    avgitte — «én autorisert aktør → positiv tildeling er umulig» er riktig
    fail-closed, men det skal sies, ikke oppleves som stillhet (§4).
    """
    import psycopg
    from starlette.responses import JSONResponse   # noqa: F401  (kanonisk_json under)

    from . import sesjon as sesjonmodul
    from .app import _autentiser, _feilsvar, _rid, kanonisk_json

    rid = _rid(request)
    raa = request.scope.get("state", {}).get("kropp", b"")
    try:
        body = json.loads(raa.decode("utf-8"))
    except Exception:
        return _feilsvar("request_feilformet", rid)
    if not isinstance(body, dict):
        return _feilsvar("request_feilformet", rid)
    utfall = body.get("utfall")
    vinnende = body.get("vinnende_tenant")
    if utfall not in ("godkjenn", "avvis") or not isinstance(vinnende, str) \
            or not vinnende.strip():
        return _feilsvar("request_feilformet", rid)

    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            auth = _autentiser(tjeneste, request, conn, rid, ADJUDIKASJONSSCOPE)
        except kjerne.Feilsvar as f:
            return _feilsvar(f.kode, rid)

        # CSRF (dobbel-innsending) — samme browsermuterende vei som PR-012.
        sesjon_cookie = request.cookies.get(sesjonmodul.C_SESJON)
        rad = conn.execute("SELECT csrf_hash FROM slaa_opp_sesjon(%s)",
                           (sesjonmodul._hash(sesjon_cookie),)).fetchone() \
            if sesjon_cookie else None
        conn.rollback()
        if rad is None or not sesjonmodul.csrf_matcher(rad[0], request):
            tjeneste.logg.hendelse("csrf_ugyldig", rid)
            return _feilsvar("csrf_ugyldig", rid)

        from db.pg import sett_kontekst
        tenant = auth.tenant
        sett_kontekst(conn, tenant, auth.token_id, rid)
        sak = slaa_opp_sak(conn, tenant, unntak_id)
        if sak is None:
            conn.rollback()
            return _feilsvar("ikke_funnet", rid)
        hostname, generasjon_ved_opprettelse = sak

        # Aktøren er sesjonens bruker-id, ikke noe klienten oppgir: «ingen
        # enkelt aktør produserer begge» er håndhevet av primærnøkkelen, og en
        # klientoppgitt aktør ville gjort den nøkkelen til et forslag.
        aktor = auth.token_id
        try:
            # Revisjonen SAKEN ble opprettet for sendes med og håndheves under
            # hostname-låsen i funksjonen (Codex): uten den ville en sak som
            # overlevde en reapplikasjon fått stemmer telt mot den GJELDENDE
            # generasjonen — en konflikt ingen attestant faktisk har sett.
            svar = conn.execute(
                "SELECT avgi_overtakelse_attestasjon(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, unntak_id, hostname, utfall, vinnende.strip(),
                 aktor, generasjon_ved_opprettelse)).fetchone()[0]
            conn.commit()
        except psycopg.errors.UniqueViolation:
            # Samme aktør, samme revisjon, andre gang. Avvist av PRIMÆRNØKKELEN
            # (port 15) — ikke av en UI-sjekk, og ikke stille.
            conn.rollback()
            return _feilsvar("dobbel_attestasjon", rid)
        except (psycopg.errors.NoDataFound, psycopg.errors.InvalidParameterValue):
            # Motorens EGNE avvisninger (ukjent domenekontroll, eller status !=
            # avklaring_kreves — saken ble foreldet av en nyere overtakelse).
            # Dette er den normale, forventede utgangen når en konflikt
            # rekker å bli avløst — ikke en basefeil, jf. samme skille app.py
            # gjør for kapabilitetsvalidering (Codex).
            conn.rollback()
            return _feilsvar("attestasjon_avvist", rid)
        except psycopg.Error:
            # EKTE basefeil (tilkobling tapt, e.l.) — skal IKKE se ut som at
            # motoren tok en avgjørelse. `attestasjon_avvist` her ville fortalt
            # klienten at attestasjonen ble vurdert og nektet, når tjenesten i
            # virkeligheten var utilgjengelig.
            conn.rollback()
            tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
            return _feilsvar("db_utilgjengelig", rid)

        if svar == "avgjort":
            return kanonisk_json({"status": "avgjort", "utfall": utfall,
                                  "hostname": hostname, "request_id": rid},
                                 200, {"x-request-id": rid})

        # Ikke avgjort. Tallet gjør feilen legibel: står det 1 av 2, vet
        # tenanten at den mangler en andre autorisert aktør — ikke at systemet
        # er i stykker.
        #
        # Konteksten settes PÅ NYTT: `sett_kontekst` er transaksjonslokal, og
        # commiten over nullstilte den. Uten dette filtrerer RLS bort raden, og
        # oppslaget gir `None` — altså et krasj nøyaktig i den grenen som
        # finnes for å unngå at brukeren møter stillhet.
        sett_kontekst(conn, tenant, auth.token_id, rid)
        antall = int(conn.execute(
            "SELECT antall_avgitte_attestasjoner(%s, autorisasjonsgenerasjon)"
            "  FROM domenekontroll WHERE tenant=%s AND hostname=%s",
            (unntak_id, tenant, hostname)).fetchone()[0])
        conn.rollback()
        return kanonisk_json(
            {"feil": "krever_to_attestasjoner", "avgitt": antall, "krever": 2,
             "hostname": hostname, "request_id": rid},
            409, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)
