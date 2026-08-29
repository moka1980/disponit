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

#: 041: sakens eier — reservert plattformtenant, aldri en kunde (port 35).
#: Python SETTER den ikke lenger som RLS-kontekst noe sted (§9.1 stengte den
#: veien); navnet står her fordi det er sakens adresse, og fordi en leser som
#: møter `__plattform_domener` i en SQL-streng skal finne det navngitt ett sted.
PLATTFORMTENANT = "__plattform_domener"


def idempotensnokkel(hostname: str, generasjon: int) -> str:
    return f"{FAMILIE}:{hostname}:{generasjon}"


def opprett_overtakelsessak(conn, *, tenant_ny: str, hostname: str,
                            tenant_tapt: str, generasjon: int,
                            aktor: str) -> int:
    """STENGT (041, port 37): saker opprettes av `sikre_overtakelsessak()`
    i basen, i SAMME transaksjon som overtakelsen — aldri herfra.

    Funksjonen står igjen som et gjerde, ikke som en vei: to sak-skapere
    for samme hendelse er en parallell sakskilde, og python-veien skrev
    dessuten saken på UTFORDRERENS tenant med kryptert payload — begge
    deler er nå feil modell (saken bor på `__plattform_domener` med
    `payload_type='referanse'`). Et kall hit er alltid en programmeringsfeil
    og skal felle kalleren høyt, ikke lage en andre sak stille.
    """
    raise RuntimeError(
        "opprett_overtakelsessak er stengt (041): saker opprettes av "
        "sikre_overtakelsessak() i basen, i samme transaksjon som "
        "overtakelsen — python-veien kan ikke skape en sak")


#: Aktøren dreneringen skriver saken som. Ikke et menneske og ikke API-et:
#: konflikten ble oppdaget av en maskin, og revisjonssporet skal si det.
DRENERINGSAKTOR = "domenekonfliktdrenering"


def vokt_ventende_overtakelseskonflikter(conn, *, grense: int = 100) -> dict:
    """VAKTBIKKJE (041): hver konflikt skal ALLEREDE ha sin sak.

    Før 041 måtte saken lages i etterkant, herfra, med tenantens DEK —
    `sikre_overtakelsessaker`-dreneringen. Nå lages den av
    `sikre_overtakelsessak()` i SAMME transaksjon som overtakelsen, og
    `domenekontroll_avklaring_krever_sak` (041 §7) avviser enhver ny
    `avklaring_kreves` uten gjeldende sak ved commit. En konflikt uten sak
    kan altså bare være en rad fra FØR 041 — en utrullingsanomali, ikke en
    kø som skal dreneres.

    Og de radene har migrasjonen selv ryddet: 041 §20 lager saken for hver
    pre-041-konflikt og FELLER migrasjonen på enhver rad som ikke kan få
    en. Et funn her betyr derfor at noe har oppstått ETTER den ryddingen —
    en tilstand systemet mener er umulig. Reparasjonsveien er den samme
    funksjonen, `migrer_pre041_overtakelseskonflikter()` (EXECUTE for
    `disponit_domains_admin`), som er idempotent og trygg å kjøre på nytt.

    Vakten beholder plukket (`ventende_overtakelseskonflikter`, 039):
    stempelet gjør utvalget roterende, så hver konflikt blir sett. For hver
    plukket rad verifiseres at saken finnes på `__plattform_domener` med
    RADENS utfordrer og generasjon. Mangler den, TELLES og NAVNGIS den —
    det finnes ingen python-vei til å lage den (port 37), og en stille
    teller ville vært å flagge et hull uten å lukke det: raden krever en
    operatør, og journalen skal si det hver syklus til den er borte.

    MEN ET FUNN MÅ VÆRE ET FUNN (Codex P2). Plukket COMMITTER (stempelet er
    rotasjonen), og slipper dermed radlåsen før oppslaget. I mellomrommet
    kan en tredje tenant ta hostnavnet: den nye overtakelsen reviderer den
    ÅPNE saken til sin egen utfordrer og generasjon i samme transaksjon,
    og tuppelen løkka holder er da historie. Et oppslag på den gamle
    tuppelen svarer nei — korrekt — men konklusjonen «konflikt uten sak»
    er feil: den konflikten finnes ikke lenger, og den som finnes HAR sin
    sak. Alarmen ville altså navngitt en tenant og et hostnavn som ikke
    feiler noe, og krevd en operatør for et kappløp.

    Porten mot det er 039s egen `bekreft_overtakelseskonflikt`, skrevet
    for nøyaktig dette vinduet i dreneringens tid og etterlatt ubrukt av
    041: den tar hostnavnets advisory-lås, leser domeneraden på nytt og
    svarer om status, motpart OG generasjon fortsatt står som plukket så
    dem. Låsen er transaksjonell, så saksoppslaget som gjøres ETTER den —
    i samme transaksjon — er atomisk mot enhver overtakelse. Rekkefølgen
    er derfor: billig oppslag først (normaltilfellet, ingen lås), og bare
    når det svarer nei eskaleres det til lås + revalidering + et ANDRE,
    autoritativt oppslag. En foreldet tuppel telles som `foreldet`, ikke
    som brudd: neste syklus plukker den gjeldende konflikten.
    """
    rader = conn.execute(
        "SELECT tenant, hostname, motpart, generasjon"
        " FROM ventende_overtakelseskonflikter(%s)", (grense,)).fetchall()
    # COMMIT, ikke rollback: plukket STEMPLER radene, og stempelet er
    # rotasjonen (039) — uten den okkuperte de første `grense` konfliktene
    # hvert utvalg.
    conn.commit()
    res = {"funnet": len(rader), "med_sak": 0, "foreldet": 0, "uten_sak": []}
    for tenant, hostname, motpart, generasjon in rader:
        # `overtakelsessak_finnes` (041 §9.2), ikke et direkte oppslag mot
        # `unntak` (Codex P2). Vakten leste tidligere saken ved å sette
        # `disponit.tenant = '__plattform_domener'` — altså gjennom
        # `tenant_isolasjon` og den fritt skrivbare GUC-en. 041 §9.1 stenger
        # den veien for runtime- og arbeiderrollen, og at det var VÅR EGEN
        # vakt som gikk den, er ikke et argument for å la den stå åpen: det
        # er funnet, sett fra innsiden.
        #
        # Adjudikatorrollen ville også vært feil pris. Vakten trenger ett
        # svar, ikke et snitt — en rolle som ser hver overtakelsessak i
        # klyngen, gitt til en bakgrunnsløkke for å svare ja eller nei, er å
        # betale i leseflate for en boolean. Funksjonen er claimer-eid og
        # SECURITY DEFINER, tar konfliktens fire ledd og gir tilbake nøyaktig
        # den ene booleanen.
        #
        # De fire leddene er §7-vaktens egne, og MOTPARTEN er ett av dem
        # (Codex P2): en sak som navngir en annen tapende part enn raden står
        # i konflikt med, er ikke RADENS sak — og en vakt som godtok den
        # ville meldt «alt i orden» om nøyaktig den forvekslingen den finnes
        # for å oppdage.
        har_sak = conn.execute(
            "SELECT overtakelsessak_finnes(%s,%s,%s,%s)",
            (hostname, tenant, motpart, int(generasjon))).fetchone()[0]
        conn.rollback()
        if har_sak:
            res["med_sak"] += 1
            continue

        # Ingen sak på DENNE tuppelen. Før det kalles et brudd: står
        # konflikten fortsatt slik plukket så den? `bekreft_...` tar
        # hostnavnets advisory-lås og leser domeneraden på nytt (039), og
        # låsen holdes ut transaksjonen — så oppslaget under er atomisk mot
        # enhver overtakelse, ikke bare et smalere vindu.
        fortsatt = conn.execute(
            "SELECT bekreft_overtakelseskonflikt(%s,%s,%s,%s)",
            (tenant, hostname, motpart, int(generasjon))).fetchone()[0]
        if not fortsatt:
            conn.rollback()
            res["foreldet"] += 1
            continue
        # Konflikten står. Da er DETTE oppslaget det autoritative: det
        # første kan ha vært et øyeblikk for tidlig.
        har_sak = conn.execute(
            "SELECT overtakelsessak_finnes(%s,%s,%s,%s)",
            (hostname, tenant, motpart, int(generasjon))).fetchone()[0]
        conn.rollback()
        if har_sak:
            res["med_sak"] += 1
        else:
            res["uten_sak"].append({"tenant": tenant, "hostname": hostname,
                                    "generasjon": int(generasjon)})
    return res


def slaa_opp_sak(conn, unntak_id: int,
                 utfordrer_tenant: str) -> tuple[str, int, str, int] | None:
    """(hostname, generasjon, utfordrer_tenant, saksrevisjon) for en sak.

    041: saken bor på `__plattform_domener` og bærer feltene sine som
    KOLONNER (`payload_type='referanse'`) — hostnavnet og generasjonen leses
    derfor rett av raden, ikke lenger ut av en idempotensnøkkel.

    042 (Codex P1): SYNLIGHETEN ER IKKE LENGER EN ROLLE. Oppslaget gikk før
    under `SET LOCAL ROLE disponit_domains_adjudicator`, og tenant-
    avgrensningen sto som `utfordrer_tenant=%s` HER, i applikasjonen. Rollen
    ser hver overtakelsessak i klyngen, runtime-rollen har `SET`-medlemskap i
    den, og et WHERE-ledd i Python er ikke en grense mot noen som kan skrive
    SQL: én injeksjon gjorde `SET ROLE`, utelot leddet og fikk hele
    saksflaten. Avgrensningen er derfor FLYTTET INN i databasen —
    `overtakelsessak_for_utfordrer` er claimer-eid, SECURITY DEFINER, og
    leser omfanget sitt av `disponit.tenant` i stedet for av kallet. Den tar
    ingen tenant som argument, nettopp fordi et argument er like fritt som
    predikatet det skulle erstatte.

    `sett_tenant` + `rollback` hos kalleren: GUC-en er `SET LOCAL` og
    forlates med transaksjonen, så ingen senere spørring på samme forbindelse
    arver snittet. Er den ikke satt, gir `NULLIF(...)` NULL inne i
    funksjonen og oppslaget svarer tomt — fail-closed.

    TERMINALE saker returneres OGSÅ: adjudikatoren skal få vite at saken
    er avgjort (`avgi` avviser den med attestasjon_avvist), ikke at den
    «ikke finnes» — et 404 på en sak man nettopp avgjorde er stillhet
    der svaret finnes.

    `utfordrer_tenant` er den AUTENTISERTE tenanten, og den er et FILTER,
    ikke et etterpå-sjekket felt (Codex P1). `avgi_overtakelse_attestasjon`
    autoriserer kun et medlem av UTFORDRERENS tenant — et oppslag uten
    filteret gjorde derfor sak-id-rommet til et orakel: en adjudikator hos
    A kunne skille «finnes ikke» fra «finnes, men er ikke din» på Bs og Cs
    saker, og lese ut vertsnavn og parter for tvister hen aldri kunne
    røre. Filteret gir samme svar for begge: `None`.

    `saksrevisjon` leses MED (Codex P1): den er «hvilken konflikt saken
    bærer nå», og kalleren skal kunne holde den mot revisjonen klienten
    faktisk fikk vist før stemmen sendes videre.
    """
    from db.pg import sett_tenant
    sett_tenant(conn, utfordrer_tenant)
    rad = conn.execute(
        "SELECT hostname_ref, autorisasjonsgenerasjon, utfordrer_tenant,"
        "       saksrevisjon"
        "  FROM overtakelsessak_for_utfordrer(%s)",
        (unntak_id,)).fetchone()
    if rad is None or any(v is None for v in rad):
        return None
    return rad[0], int(rad[1]), rad[2], int(rad[3])


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

    UENIGHET ER IKKE EN MANGLENDE STEMME (Codex P1). Peker de avgitte
    stemmene hver sin vei, svarer motoren `uenighet`, og endepunktet gir
    `attestasjoner_uenige` — ikke `krever_to_attestasjoner`. Terskelsvaret
    ba om en stemme som ikke fantes: tellingen inkluderte den uenige raden,
    så flaten meldte «2 av 2» på en sak ingen ny stemme kunne avgjøre.
    Resolusjonen står i svaret (`resolusjon: avvis`) og håndheves av
    motoren: en avvisning avgjør saken fail-closed.
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
    # REVISJONEN ER OBLIGATORISK (Codex P1). Stemmen skal telles i den
    # konflikten attestanten LESTE, ikke i den saken tilfeldigvis bærer når
    # kroppen ankommer — og et felt som var valgfritt ville gjort gjerdet
    # til noe en klient kunne velge bort ved å utelate det. `bool` er en
    # `int` i Python og lukkes ute eksplisitt: `True` er ikke en revisjon.
    saksrevisjon = body.get("saksrevisjon")
    if isinstance(saksrevisjon, bool) or not isinstance(saksrevisjon, int) \
            or saksrevisjon < 0:
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

        # 042: saken leses tenantbundet i databasegrensen (den bor på
        # `__plattform_domener`, men snittet er utfordrerens); `SET LOCAL`
        # gjør at rollback forlater tenantkonteksten med transaksjonen.
        sak = slaa_opp_sak(conn, unntak_id, auth.tenant)
        conn.rollback()
        if sak is None:
            return _feilsvar("ikke_funnet", rid)
        hostname, generasjon_ved_opprettelse, utfordrer_tenant, rev = sak
        # SAKEN MÅ STÅ DER FLATEN SÅ DEN (Codex P1). `unntak_id` er stabil
        # gjennom A→B→C→B: en adjudikatorfane som har stått åpen peker på
        # samme sak, men på en helt annen tvist — annen motpart, annen
        # generasjon. Uten dette leddet leste endepunktet BEGGE feltene
        # ferskt av raden og avga stemmen i den nye konflikten, så to gamle
        # faner kunne fullføre en positiv tildeling ingen av dem hadde
        # sett. Nettopp det fire øyne finnes for.
        #
        # Svaret er `attestasjon_avvist`: saken er avløst av en nyere
        # konflikt, som er akkurat det den feilveien beskriver — og flaten
        # laster køen på nytt uansett utfall, så neste forsøk står på den
        # gjeldende revisjonen. Det ENDELIGE gjerdet er ikke her, men i
        # `avgi_overtakelse_attestasjon` under hostname-låsen: et skifte som
        # commiter etter dette oppslaget kan ikke smyge seg inn foran
        # stemmen. Dette leddet gir den billige, legible avvisningen før
        # noen kontekst settes.
        if rev != saksrevisjon:
            return _feilsvar("attestasjon_avvist", rid)
        # Attestasjonen avgis i UTFORDRERENS saksunivers: `p_tenant` er
        # utfordreren saken navngir (019-kontrakten). Den leses fra RADEN og
        # ikke fra sesjonen, selv om oppslaget over alt har gjerdet dem
        # sammen — 019 gjerder på sakens egen utfordrer, og API-et skal sende
        # nøyaktig det feltet motoren gjerder på.
        from db.pg import sett_kontekst
        sett_kontekst(conn, utfordrer_tenant, auth.token_id, rid)

        # Aktøren er sesjonens bruker-id, ikke noe klienten oppgir: «ingen
        # enkelt aktør produserer begge» er håndhevet av primærnøkkelen, og en
        # klientoppgitt aktør ville gjort den nøkkelen til et forslag.
        aktor = auth.token_id
        try:
            # Revisjonen SAKEN ble opprettet for sendes med og håndheves under
            # hostname-låsen i funksjonen (Codex): uten den ville en sak som
            # overlevde en reapplikasjon fått stemmer telt mot den GJELDENDE
            # generasjonen — en konflikt ingen attestant faktisk har sett.
            # `saksrevisjon` er KLIENTENS tall, ikke radens: gjerdet skal måle
            # det flaten viste mot det saken står på, og et tall lest herfra
            # ville bare målt raden mot seg selv.
            svar = conn.execute(
                "SELECT avgi_overtakelse_attestasjon"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (utfordrer_tenant, unntak_id, hostname, utfall,
                 vinnende.strip(), aktor, generasjon_ved_opprettelse,
                 bruker_id, saksrevisjon)).fetchone()[0]
            # Codex (P2): tallet leses i SAMME transaksjon som stemmen, mens
            # domeneraden fortsatt er låst av funksjonen — ikke etter commit.
            # Etter commit kunne en samtidig andre godkjenning ha fullført
            # overtakelsen og økt `autorisasjonsgenerasjon`; et oppslag mot den
            # GJELDENDE generasjonen telte da den nye revisjonen (null avgitte)
            # og svarte `409 krever_to_attestasjoner` på en sak som nettopp var
            # avgjort. Revisjonen er den funksjonen selv håndhevet under låsen,
            # så tallet hører til nøyaktig den konflikten stemmen ble avgitt i.
            # `uenighet` telles ikke (Codex P1): tallet er «hvor mange
            # stemmer finnes», ikke «hvor langt er vi mot terskelen», og en
            # uenig sak er ikke på vei mot noen terskel i det hele tatt.
            antall = int(conn.execute(
                "SELECT antall_avgitte_attestasjoner(%s,%s)",
                (unntak_id, generasjon_ved_opprettelse)).fetchone()[0]) \
                if svar == "venter" else 0
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

        # UENIGHET HAR SITT EGET SVAR (Codex P1). Motoren skiller nå «saken
        # mangler en andre stemme» fra «de avgitte stemmene peker hver sin
        # vei», og flaten må skille dem også: `krever_to_attestasjoner` med
        # `avgitt = 2, krever = 2` er en umulig setning som ba adjudikatoren
        # om en stemme hen ikke kunne avgi. Resolusjonsveien er den samme
        # som motoren nå håndhever — en avvisning avgjør saken — og den
        # SIES her, i stedet for å ligge implisitt i en teller.
        if svar == "uenighet":
            return kanonisk_json(
                {"feil": "attestasjoner_uenige", "hostname": hostname,
                 "resolusjon": "avvis", "request_id": rid},
                409, {"x-request-id": rid})

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


def saker_endepunkt(tjeneste, request):
    """GET /v1/domeneovertakelse/saker — adjudikatorkøen (041 §5).

    EGEN visning, atskilt fra tenantens unntakskø: adjudikatoren skal se
    partene og revisjonen for å kunne avgjøre, og nettopp derfor kan denne
    listen aldri gå gjennom en kundesesjons RLS-snitt. Scope-gatet
    (`domains:adjudicate`), lesingen under `SET LOCAL ROLE` — samme to-lags
    gjerde som attestasjonsendepunktet. Lesende GET uten CSRF (pr008-
    invarianten: en GET bærer aldri et mutasjonsscope).

    KØEN ER UTFORDRERENS, IKKE KLYNGENS (Codex P1). `domeneadjudikator` er
    en KUNDE-lokal rolle: enhver tenant kan gi den til sin egen bruker
    (autorisasjon.py), og `__plattform_domener` kan per §8 aldri bære et
    medlemskap — det finnes altså ingen plattformglobal prinsipal å måle
    «global kø» mot. Uten filteret her ga rollen sitt eget omfang:
    `SET LOCAL ROLE` løfter lesingen ut av tenant-GUC-en, og As adjudikator
    leste vertsnavn OG begge partsidentiteter for tvister mellom B og C —
    saker hen dessuten aldri kunne røre, siden
    `avgi_overtakelse_attestasjon` (019) krever aktivt medlemskap i
    UTFORDRERENS tenant. Køen speiler derfor nøyaktig den autoriteten:
    sakene der sesjonens tenant ER utfordreren. Kryssidentiteten flaten
    viser (`tapt_tenant`) er da motparten i din EGEN tvist — den samme
    §5-visningen som før, men uten andres.

    BUNDET SIDE (Codex P2). Overtakelsessaker står ÅPNE til et menneske
    avgjør dem, så køen har ingen naturlig øvre grense: en etterslepende
    tenant, eller en som produserer mange legitime DNS-konflikter, ville
    latt hver eneste henting materialisere hele beholdningen med
    `fetchall()` — base, svar og nettleser vokser i takt uten tak. Siden
    er derfor keyset-paginert med NØYAKTIG samme kontrakt som `/v1/unntak`
    (`limit` ≤ 100, standard 50, signert v2-cursor bundet til tenant,
    endepunkt, retning og filtre). Retningen er `asc`: eldste sak først —
    en adjudikatorkø skal tømmes fra bunnen, ikke vise det ferskeste.

    Keysettet er `(saksrevisjon_ts, id)` og ikke `(ts, id)` (Codex P2):
    A→B→C flytter en ÅPEN sak til en ny utfordrer uten å lage en ny rad,
    og `ts` er kolonnelåst. «Eldste først» betyr derfor «eldst som DENNE
    konflikten», ikke «eldst som rad» — ellers kommer arbeidet inn bak en
    cursor tenanten alt har fått, og blir aldri vist.
    """
    import psycopg

    from . import cursor as cursormodul
    from . import kjerne
    from . import lesing
    from .app import _autentiser, _feilsvar, _rid, kanonisk_json

    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               ADJUDIKASJONSSCOPE)
        except kjerne.Feilsvar as f:
            return _feilsvar(f.kode, rid)
        # Samme grensekontrakt som lesekøene — én implementasjon, så
        # taket ikke kan drive fra hverandre endepunktene imellom.
        grense = lesing._grense(request)
        if grense is None:
            return _feilsvar("request_feilformet", rid)
        etter = None
        raa = request.query_params.get("cursor")
        if raa:
            try:
                etter = cursormodul.les_v2(
                    raa, tjeneste.cursorpepper, tenant=auth.tenant,
                    endepunkt="domeneovertakelse_saker", retning="asc",
                    filtre={})
            except cursormodul.CursorUgyldig:
                tjeneste.logg.hendelse("cursor_ugyldig", rid, auth.tenant)
                return _feilsvar("cursor_ugyldig", rid)

        # 042 (Codex P1): OMFANGET ER IKKE LENGER ET WHERE-LEDD HER.
        #
        # Formen var «rollen gir SYNLIGHETEN, filteret gir OMFANGET»: køen
        # gjorde `SET LOCAL ROLE disponit_domains_adjudicator` og la
        # `utfordrer_tenant=%s` på i applikasjonens SQL. Rollen ser hver
        # overtakelsessak i klyngen, og runtime-rollen har `SET`-medlemskap i
        # den — så avgrensningen holdt bare så lenge denne strengen var den
        # eneste SQL-en som ble kjørt. Én injeksjon skiftet rolle, droppet
        # leddet og fikk alle kunders saker i ett svar.
        #
        # Begge deler ligger nå i `overtakelsessaker_for_utfordrer` (042 §1.2):
        # claimer-eid, SECURITY DEFINER, og den leser tenanten sin av
        # `disponit.tenant` — ikke av et argument, som ville vært like fritt
        # som leddet det erstattet. KEYSETTET FULGTE MED INN, med sin egen
        # begrunnelse: nøkkelen er `saksrevisjon_ts` og ikke `ts` (Codex P2),
        # fordi A→B→C skifter utfordrer på den EKSISTERENDE raden mens `ts`
        # er kolonnelåst (§11) — med `ts` la saken seg BAK en cursor den nye
        # utfordreren alt hadde fått utstedt, og forsvant fra hver gjenstående
        # side uten noen gang å ha vært på en tidligere. Filteret og nøkkelen
        # svarer på samme spørsmål; blir de liggende hver sin side av
        # grensen, kan de drive fra hverandre.
        #
        # Ærlig keyset (v4 pkt. 3): ingen duplikater for uendrede rader. En
        # sak som blir avgjort mens noen blar, forsvinner ut av `NOT terminal`
        # — det er køens poeng, ikke et brudd.
        from db.pg import sett_tenant
        sett_tenant(conn, auth.tenant)
        rader = conn.execute(
            "SELECT id, hostname_ref, saksrevisjon,"
            "       autorisasjonsgenerasjon, utfordrer_tenant,"
            "       tapt_tenant, status, ts, saksrevisjon_ts"
            "  FROM overtakelsessaker_for_utfordrer(%s,%s,%s)",
            (etter[0] if etter is not None else None,
             etter[1] if etter is not None else None,
             grense)).fetchall()
        conn.rollback()
        saker = [{"unntak_id": int(r[0]), "hostname": r[1],
                  "saksrevisjon": int(r[2]),
                  "autorisasjonsgenerasjon": int(r[3]),
                  "utfordrer_tenant": r[4], "tapt_tenant": r[5],
                  "status": r[6], "ts": r[7].isoformat(),
                  # Begge tidene, fordi de svarer på hver sin ting: `ts`
                  # er når saken ble åpnet (alderen), `saksrevisjon_ts` er
                  # når den ble den konflikten den er nå — og det siste er
                  # det køen sorteres og pagineres på.
                  "saksrevisjon_ts": r[8].isoformat()} for r in rader]
        neste = None
        if len(rader) == grense:
            neste = cursormodul.lag_v2(
                tjeneste.cursorpepper, tenant=auth.tenant,
                endepunkt="domeneovertakelse_saker", retning="asc",
                filtre={}, ts=rader[-1][8], rad_id=rader[-1][0])
        return kanonisk_json({"saker": saker, "neste_cursor": neste,
                              "request_id": rid}, 200, {"x-request-id": rid})
    except psycopg.Error as e:
        conn.rollback()
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift",
                               feiltype=type(e).__name__)
        return _feilsvar("db_utilgjengelig", rid)
    finally:
        tjeneste.pool.gi_tilbake(conn)
