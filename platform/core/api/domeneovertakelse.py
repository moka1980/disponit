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
import secrets

import psycopg

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

    Degraderingen av forbigåtte utfordrere (A→B→C, §3) gjøres IKKE herfra
    (Codex): invarianten hører hjemme på selve overgangen, ikke hos en kaller
    som må huske den. Migrasjon 019 §3.25 henger
    `degrader_forbigatte_utfordrere` på `hostname_binding` — flyttes bindingen,
    degraderes alle andre i `avklaring_kreves` i samme transaksjon, uansett
    hvem som utløste overtakelsen. Det gjelder også dreneringen under, som
    kaller hit for konflikter den ikke selv utløste.
    """
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


#: Aktøren dreneringen skriver saken som. Ikke et menneske og ikke API-et:
#: konflikten ble oppdaget av en maskin, og revisjonssporet skal si det.
DRENERINGSAKTOR = "domenekonfliktdrenering"


def sikre_ventende_overtakelsessaker(conn, *, aktor: str = DRENERINGSAKTOR,
                                     grense: int = 100) -> dict:
    """Opprett M-37-saken for hver konflikt som står uten en (Codex P1).

    `verifiser_domenekontroll` gjør overtakelsen og adjudikasjonskravet i ÉN
    transaksjon — A tilbakekalles, B settes `avklaring_kreves` med motparten på
    raden — men SAKEN kan ikke lages der. `opprett_overtakelsessak` krypterer
    payloaden med tenantens DEK (KEK-en lever i prosessen, ikke i basen) og
    skriver `revisjonslogg` + `unntak`: runtime-autoritet med nøkkelmateriale.
    Verken basen eller verifiseringsarbeideren (`disponit_domener` — EXECUTE på
    nøyaktig to funksjoner) har den, og skal ikke ha den. Uten en vei videre
    var utfallet det verst mulige: A mistet autorisasjonen, B ble stående i
    `avklaring_kreves`, og siden bare `avgjor_domeneovertakelse` kan løfte noen
    ut av avklaring — og den nås bare gjennom en sak — kunne ingen av dem
    noensinne bli løst.

    Signalet er derfor ikke en melding som kan gå tapt, men TILSTANDEN selv:
    en rad i `avklaring_kreves` med `konflikt_motpart` ER en konflikt som
    venter på sin sak (`ventende_overtakelseskonflikter`, migrasjon 039).
    Dreneringen kjøres fra en prosess som HAR autoriteten (M-37-arbeideren),
    én rad om gangen med konteksten bundet til RADENS tenant — samme form som
    `reap_evidensfrister` (038 §5).

    Idempotent i to lag: nøkkelen er (hostname, generasjon) under advisory-lås,
    så en konflikt får ÉN sak uansett hvor mange ganger vi drenerer, og en rad
    som alt har sin sak koster kun oppslaget. Utvalget ROTERER (Codex P2):
    plukket stempler radene det tar og tar de minst nylig drenerte først, ellers
    ville de første `grense` konfliktene — som blir stående til et menneske har
    avgjort dem — okkupert hvert eneste utvalg, og konflikt nummer `grense`+1
    aldri fått sin sak. Faller prosessen ut midt i, står
    radene igjen og neste syklus finner dem på nytt — det finnes ingen kø å
    reparere, og derfor ingen kø som kan bli inkonsistent med tilstanden.

    Én rads feil stopper ikke de andre: mangler ÉN tenant sin KEK, skal ikke
    alle andres konflikter bli ustelt. Feilen telles og navngis. Men en tapt
    FORBINDELSE er ikke en radfeil — den kastes videre, slik at arbeiderens
    hovedløkke kobler opp på nytt i stedet for å rapportere «100 rader feilet».
    """
    from db.pg import sett_kontekst
    rader = conn.execute(
        "SELECT tenant, hostname, motpart, generasjon"
        " FROM ventende_overtakelseskonflikter(%s)", (grense,)).fetchall()
    # COMMIT, ikke rollback (Codex P2). Plukket STEMPLER radene
    # (`konflikt_drenert`, 039), og det er stempelet som gjør utvalget
    # roterende: en konflikt står `avklaring_kreves` til et menneske har
    # avgjort saken, så uten rotasjon ville de første `grense` radene okkupert
    # hvert eneste utvalg og konflikt nummer `grense`+1 aldri fått sin sak.
    # Egen transaksjon, før radarbeidet: saksopprettelsen under committer per
    # rad med sin egen tenantkontekst.
    conn.commit()
    res = {"funnet": len(rader), "saker": [], "feilet": []}
    for tenant, hostname, motpart, generasjon in rader:
        rid = "drenering-" + secrets.token_hex(8)
        try:
            sett_kontekst(conn, tenant, aktor, rid)
            uid = opprett_overtakelsessak(
                conn, tenant_ny=tenant, hostname=hostname,
                tenant_tapt=motpart, generasjon=int(generasjon), aktor=aktor)
            conn.commit()
        except psycopg.OperationalError:
            raise
        except (kjerne.Feilsvar, psycopg.Error, ValueError) as e:
            conn.rollback()
            res["feilet"].append({"tenant": tenant, "hostname": hostname,
                                  "feiltype": type(e).__name__})
            continue
        res["saker"].append({"tenant": tenant, "hostname": hostname,
                             "unntak_id": uid})
    return res


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
        # `bruker_id` hentes fra SAMME herdede oppslag: prinsipalen bak
        # stemmen må sendes til motoren for at de tellende stemmene skal kunne
        # reautoriseres når terskelen slår inn (Codex). Den leses her, fra
        # sesjonen, og ikke ved å parse `auth.token_id` — sesjonsstrengen er
        # evidensformatet, ikke et oppslagsnøkkelformat.
        sesjon_cookie = request.cookies.get(sesjonmodul.C_SESJON)
        rad = conn.execute(
            "SELECT csrf_hash, bruker_id FROM slaa_opp_sesjon(%s)",
            (sesjonmodul._hash(sesjon_cookie),)).fetchone() \
            if sesjon_cookie else None
        conn.rollback()
        if rad is None or not sesjonmodul.csrf_matcher(rad[0], request):
            tjeneste.logg.hendelse("csrf_ugyldig", rid)
            return _feilsvar("csrf_ugyldig", rid)
        bruker_id = rad[1]

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
                "SELECT avgi_overtakelse_attestasjon(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, unntak_id, hostname, utfall, vinnende.strip(),
                 aktor, generasjon_ved_opprettelse,
                 bruker_id)).fetchone()[0]
            # Codex (P2): tallet leses i SAMME transaksjon som stemmen, mens
            # domeneraden fortsatt er låst av funksjonen — ikke etter commit.
            # Etter commit kunne en samtidig andre godkjenning ha fullført
            # overtakelsen og økt `autorisasjonsgenerasjon`; et oppslag mot den
            # GJELDENDE generasjonen telte da den nye revisjonen (null avgitte)
            # og svarte `409 krever_to_attestasjoner` på en sak som nettopp var
            # avgjort. Revisjonen er den funksjonen selv håndhevet under låsen,
            # så tallet hører til nøyaktig den konflikten stemmen ble avgitt i.
            antall = 0 if svar == "avgjort" else int(conn.execute(
                "SELECT antall_avgitte_attestasjoner(%s,%s)",
                (unntak_id, generasjon_ved_opprettelse)).fetchone()[0])
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
        # er i stykker. Det er lest over, under låsen, så det er ikke lenger et
        # nytt oppslag som kan lande på en annen (eller foreldet) revisjon.
        return kanonisk_json(
            {"feil": "krever_to_attestasjoner", "avgitt": antall, "krever": 2,
             "hostname": hostname, "request_id": rid},
            409, {"x-request-id": rid})
    finally:
        tjeneste.pool.gi_tilbake(conn)
