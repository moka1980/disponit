"""Varsler: «noe venter på DEG».

Eier: «mokhtar.eliassi@gmail.com skal få epost og samtidig melding i
kundeadmin … Det skal være option.»

Behovet kommer fra fire-øyne-flyten. En runde står åpen og venter på en
UAVHENGIG godkjenner, men ingenting forteller henne det — i praksis måtte eier
si fra utenom systemet. En styringsflate der neste steg formidles på SMS er
ikke en styringsflate.

TRE REGLER SOM STYRER DENNE MODULEN:

1. **Portalen er varselet; e-posten er en kopi.** Raden i `varsel` er
   sannheten. Kan e-posten ikke sendes, står varselet fortsatt i innboksen.
   Derfor er e-poststatus en kolonne på raden og ikke en egen kø.

2. **Et varsel skal ALDRI kunne velte handlingen.** En aktiveringsrunde er en
   fullmaktsendring; den skal ikke feile fordi en e-postserver er nede eller
   fordi noen slettet en identitet. `varsle_*` fanger derfor alt og
   rapporterer i stedet for å kaste. Motsatt vei ville vært absurd: et
   varslingsproblem som blokkerer styringen.

3. **Teksten lagres ikke, bare nøkkel + parametre.** Locale-kontrakten sier at
   synlig tekst kommer fra `locales/`, og varselet skal leses på MOTTAKERENS
   språk — ikke på det avsenderen tilfeldigvis hadde. E-posten rendres når den
   sendes, ikke når den køes.

Hvem som skal varsles ved en åpen runde er IKKE «alle med rollen»: det er de
som faktisk kan bringe runden videre. Attesterer man allerede, er man ferdig —
og forfatteren alene kan ikke fullføre fire øyne. Se `mottakere_for_runde`.
"""
import json

import psycopg

from db.pg import sett_kontekst

STANDARDKANAL = "epost_og_portal"

#: Tilstandene en e-post fortsatt KAN bli sendt fra. Ett sted, fordi det er to
#: veier som avlyser en sending — avmeldingen (`sett_kanal`) og pensjoneringen
#: (`pensjoner_runde`) — og de må mene det samme.
#:
#: `koet` var lenge hele svaret, og begge veiene skrev det inn hver for seg.
#: Så ble `feilet` retrybar (027: `varsel_rekoe` løfter den tilbake til `koet`
#: når backoffen er ute), og da avlyste begge bare halvparten: en avmelding
#: gjort mens raden lå og ventet på nytt forsøk, stoppet ikke e-posten hun
#: nettopp sa nei til.
#:
#: `under_sending` står bevisst UTENFOR. Den raden er i et SMTP-kall akkurat
#: nå, og en e-post som er ute kan ikke kalles hjem — den hører til fortiden,
#: som `sendt`. Å sette den `ikke_aktuelt` ville dessuten stjålet klaimet fra
#: senderen som holder det.
#:
#: Det forutsetter at `under_sending` varer så lenge sendingen varer, og ikke
#: lenger. Senderen klaimte lenge en bunke på opptil 50 rader og committet
#: hele bunken før den første e-posten gikk ut (Codex P2); da lå den siste
#: raden utenfor dette settet i minutter uten å være i noe SMTP-kall, og en
#: avmelding gjort i det vinduet nådde den ikke. `varselsender.kjor` klaimer
#: derfor én rad om gangen og sender den med det samme.
I_KO = "('koet', 'feilet')"

#: Sentinel fra `skjermet`: steget feilet. Skilt fra `None`, som er et helt
#: gyldig resultat av et steg som gikk bra.
FEILET = object()

#: Savepoint-navnet. Fast literal — aldri satt sammen av data, siden en
#: savepoint-etikett er en identifikator og ikke kan parametriseres.
_SP = "varsel_skjerm"

#: Advisory-klassen for kanalvalget. Andre halvdel av nøkkelen er hashen av
#: tenant + bruker, så to brukere ikke venter på hverandre. Se
#: `_laas_kanalvalget`.
KANALVALGNOKKEL = 615_774_026


def skjermet(conn: psycopg.Connection, arbeid):
    """Kjør ett steg under en SAVEPOINT. -> resultatet, eller `FEILET`.

    Offentlig fordi regel 2 ikke stopper ved modulgrensen: en kaller som skal
    avgjøre OM det er noe å varsle om, må stille et spørsmål til databasen, og
    det spørsmålet kan feile på nøyaktig samme måte som varslingen selv. Sto
    det uskjermet, ville oppslaget veltet handlingen som varslingen lovte å
    ikke røre — bare ett skritt tidligere i sløyfa.

    Dette er hele mekanismen bak «et varsel velter aldri handlingen». Kalleren
    eier transaksjonen: uten savepoint ville én feilet spørring her latt
    PostgreSQL stå i abortert tilstand, og kallerens neste setning — og
    `commit()` — ville feilet selv om vi svelget unntaket pent i Python.

    SAVEPOINT skrives ut som SQL, IKKE med `conn.transaction()`. Husets idiom
    ellers, men det kan ikke brukes her: psycopg teller opp transaksjonsstabelen
    FØR den sender SAVEPOINT, så entringen på en alt abortert forbindelse
    etterlater telleren hevet — og da kaster kallerens `conn.rollback()`
    «Explicit rollback() forbidden within a Transaction context». Nøyaktig det
    denne funksjonen finnes for å hindre: varslingen som ødelegger kallerens
    ryddevei. Med ren SQL finnes ikke den skjulte tilstanden.

    Er transaksjonen alt abortert når vi kommer hit, feiler SAVEPOINT-en selv,
    og vi melder `FEILET` uten å røre noe. Å reparere en transaksjon kalleren
    alt har mistet er ikke varslingens oppgave.
    """
    try:
        conn.execute(f"SAVEPOINT {_SP}")
    except Exception:                                         # noqa: BLE001
        return FEILET
    try:
        res = arbeid()
    except Exception:                                         # noqa: BLE001
        try:
            # Tilbake til savepointet: varselet angres, kallerens arbeid ikke.
            conn.execute(f"ROLLBACK TO SAVEPOINT {_SP}")
            conn.execute(f"RELEASE SAVEPOINT {_SP}")
        except Exception:                                     # noqa: BLE001
            pass   # forbindelsen er borte; kalleren ser det uansett selv
        return FEILET
    try:
        conn.execute(f"RELEASE SAVEPOINT {_SP}")
    except Exception:                                         # noqa: BLE001
        return FEILET
    return res


def _kanal(conn: psycopg.Connection, tenant: str, bruker_id: str) -> str:
    """Brukerens valg, eller standarden. Fraværende rad er IKKE «av»: ingen
    skal gå glipp av at noe venter på dem fordi de aldri åpnet
    innstillingene."""
    rad = conn.execute(
        "SELECT kanal FROM varselvalg WHERE tenant=%s AND bruker_id=%s",
        (tenant, bruker_id)).fetchone()
    return rad[0] if rad else STANDARDKANAL


def _laas_kanalvalget(conn: psycopg.Connection, tenant: str,
                      bruker_id: str) -> None:
    """Ta brukerens kanalvalg-lås for resten av transaksjonen.

    De to veiene som må være enige om kanalen — `opprett`, som LESER valget og
    så skriver en rad, og `sett_kanal`, som SKRIVER valget og så rydder køen —
    kappløp ellers om et varsel som ikke fantes for noen av dem (Codex P2):
    `opprett` leser `epost_og_portal`, avmeldingen oppdaterer alle rader den
    kan SE, og først etterpå settes den nye raden inn med `koet`. Valget sier
    kun portal, og e-posten går ut likevel. Vinduet er nøyaktig det brukeren
    tilbringer i innstillingene mens en runde åpnes.

    Låsen er en advisory-lås og ikke en radlås på `varselvalg`, fordi den
    farligste varianten er den der raden IKKE finnes ennå: standarden er et
    fravær, så førstegangsavmeldingen — den vanligste av alle — INSERTer, og en
    `FOR SHARE` på ingenting serialiserer ingenting. En advisory-nøkkel finnes
    uansett om raden gjør det.

    `xact`-varianten slippes av commit eller rollback, aldri av en savepoint
    som rulles tilbake. Det er riktig her: `opprett` kjøres under `skjermet`,
    og en lås som forsvant med savepointet ville sluppet avmeldingen inn i
    vinduet igjen.

    Nøkkelen er (klasse, hash av tenant+bruker). En hash-kollisjon mellom to
    brukere koster litt unødig serialisering og ingenting annet.
    """
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))",
                 (KANALVALGNOKKEL, f"{tenant}\x1f{bruker_id}"))


def mottakere_for_runde(conn: psycopg.Connection, tenant: str, utkast_id: str,
                        runde: int) -> list[str]:
    """Hvem kan bringe DENNE runden videre?

    Ikke «alle policyforvaltere». Den som allerede har attestert er ferdig, og
    å varsle henne igjen lærer henne bare å overse varsler. Forfatteren tas med
    KUN hvis hun ikke alt har attestert — hun teller, men kan ikke fullføre
    fire øyne alene, så terskelen avgjør uansett.

    «Allerede attestert» måles PÅ RUNDEN, ikke på utkastet. En attestasjon
    binder én rundes diff (migrasjon 012: unik på tenant+utkast+runde+bruker),
    og den følger ikke med over i neste runde: forfaller eller avbrytes runde
    1, må runde 2 attesteres på nytt av de samme menneskene. Uten `a.runde`
    her ville alle som deltok i en tidligere runde vært utelukket fra varselet
    for den nye — permanent, og nettopp de som har vist at de svarer.

    Rekkefølgen er FAST på `bruker_id`, ikke fordi noen leser den, men fordi
    `opprett` tar en lås per mottaker: to runder som åpnes samtidig og deler
    godkjennere kunne ellers ta de samme låsene i motsatt rekkefølge og gå i
    vranglås. Én sorteringsnøkkel er hele vernet.
    """
    return [r[0] for r in conn.execute(
        "SELECT m.bruker_id FROM brukermedlemskap m"
        " WHERE m.tenant=%s AND m.aktiv"
        "   AND 'policyforvalter' = ANY(m.roller)"
        "   AND NOT EXISTS (SELECT 1 FROM aktiveringsattestasjon a"
        "                    WHERE a.tenant=m.tenant AND a.utkast_id=%s"
        "                      AND a.runde=%s AND a.bruker_id=m.bruker_id)"
        " ORDER BY m.bruker_id",
        (tenant, utkast_id, runde)).fetchall()]


def opprett(conn: psycopg.Connection, *, tenant: str, bruker_id: str, art: str,
            ressurs_type: str, ressurs_id: str, tekstnokkel: str,
            hendelse: str = "", parametre: dict | None = None) -> bool:
    """Ett varsel til én mottaker. -> True hvis det ble opprettet.

    Idempotent på (tenant, bruker, art, ressurs, hendelse): en retry av
    handlingen som utløste varselet skal ikke fylle innboksen med duplikater.
    `ON CONFLICT DO NOTHING` er hele mekanismen — det unike indekset i
    migrasjon 026 er det som faktisk håndhever den.

    `hendelse` er grensen mellom «samme hendelse igjen» og «en ny hendelse på
    samme ressurs»: for en aktiveringsrunde er det rundenummeret. Utelates den,
    ville runde 2 på et utkast delt nøkkel med runde 1 og blitt slukt som en
    dublett — og godkjenneren som alt hadde lest det gamle varselet ville ikke
    sett noe nytt i det hele tatt.

    Har mottakeren valgt kun portal, settes `epost_status='ikke_aktuelt'` med
    én gang: et bevisst fravær skal ikke se ut som en sending som aldri kom.

    Lesningen av kanalen og innsettingen av raden skjer under mottakerens
    kanalvalg-lås (`_laas_kanalvalget`), så en avmelding som skjer akkurat nå
    enten er lest av oss eller rekker å rydde raden vi setter inn.
    """
    _laas_kanalvalget(conn, tenant, bruker_id)
    kanal = _kanal(conn, tenant, bruker_id)
    status = "koet" if kanal == "epost_og_portal" else "ikke_aktuelt"
    rad = conn.execute(
        "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type, ressurs_id,"
        " hendelse, tekstnokkel, parametre, epost_status)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)"
        " ON CONFLICT (tenant, bruker_id, art, ressurs_type, ressurs_id,"
        " hendelse) DO NOTHING RETURNING id",
        (tenant, bruker_id, art, ressurs_type, ressurs_id, hendelse,
         tekstnokkel, json.dumps(parametre or {}), status)).fetchone()
    return rad is not None


def varsle_runde_venter(conn: psycopg.Connection, *, tenant: str, aktor: str,
                        request_id: str, utkast_id: str, runde: int,
                        policy_id: str, risikoklasse: str,
                        gjenstaar: int) -> int:
    """Varsle dem som kan bringe runden videre. -> antall varsler opprettet.

    KASTER ALDRI. Kalles fra aktiveringsflyten, og en fullmaktsendring skal
    ikke kunne feile fordi varslingen gjorde det. Feiler noe her, er
    konsekvensen at et menneske ikke får en påminnelse — ikke at styringen
    stopper. Den motsatte avveiningen ville vært uforsvarlig.

    Å fange unntaket er IKKE nok til å holde det løftet. Kalleren eier
    transaksjonen og deler forbindelse med oss: feiler en spørring her, står
    PostgreSQL i abortert tilstand, og da feiler kallerens NESTE setning — og
    `commit()` — uansett hvor pent vi svelget feilen i Python. Varslingen ville
    altså veltet nøyaktig den handlingen den lovte å ikke røre. Derfor kjøres
    hvert steg under en SAVEPOINT (`skjermet`), som rulles tilbake ved feil
    slik at forbindelsen er brukbar igjen og kallerens eget arbeid står urørt.

    Savepointet er PER MOTTAKER, ikke ett rundt hele sløyfa. Den typiske
    feilen rammer én rad (en identitet er slettet, en rad kolliderer), og de
    andre godkjennerne skal ikke miste varselet sitt fordi den ene feilet.
    """
    if skjermet(conn, lambda: sett_kontekst(conn, tenant, aktor, request_id)) \
            is FEILET:
        return 0
    mottakere = skjermet(
        conn, lambda: mottakere_for_runde(conn, tenant, utkast_id, runde))
    if mottakere is FEILET:
        return 0
    n = 0
    for bid in mottakere:
        if skjermet(conn, lambda bid=bid: opprett(
                conn, tenant=tenant, bruker_id=bid,
                art="attestering_venter", ressurs_type="policyutkast",
                ressurs_id=utkast_id, hendelse=str(runde),
                tekstnokkel="varsel.attestering_venter",
                parametre={"policy_id": policy_id, "runde": runde,
                           "risikoklasse": risikoklasse,
                           "gjenstaar": gjenstaar})) is True:
            n += 1
    return n


def pensjoner_runde(conn: psycopg.Connection, *, tenant: str, utkast_id: str,
                    runde: int, bruker_id: str | None = None) -> int:
    """Et varsel skal slutte å vente når handlingen er gjort. -> antall ryddet.

    Varselet er en OPPFORDRING, ikke en kvittering: «attesteringen venter på
    deg». I det du har attestert — eller runden er brukt, kansellert eller
    forfalt — er oppfordringen ikke lenger sann. Uten denne funksjonen ble den
    stående likevel, og det er ikke en skjønnhetsfeil:

      * i den vanligste flyten attesterer forfatteren med én gang runden er
        åpnet. Innboksen hennes sier da at hennes egen, alt utførte
        attestering venter på henne;
      * når runden lukkes, blir alle de andres varsler stående uleste for
        alltid. Eneste vei ut var at hver enkelt trykket «merk som lest» på et
        varsel om noe som ikke finnes lenger.

    En innboks som lyver om hva som venter, blir en innboks folk slutter å se
    på — og da er hele modulen tapt.

    `bruker_id` avgrenser til ÉN mottaker (aktøren som nettopp attesterte);
    utelates den, ryddes hele rundens varsler.

    E-post som ennå står i kø settes samtidig til `ikke_aktuelt` — hele køen,
    `I_KO`, og ikke bare `koet`: en rad som venter på nytt forsøk er like mye
    på vei ut som en som aldri har vært prøvd. Uten dette ville senderen
    fortsatt sendt «venter på din attestering» om en runde som er lukket —
    samme løgn, bare i den kanalen mottakeren ikke kan lukke selv. Alt som ER
    sendt står urørt: `sendt` er et faktum om fortiden, ikke en tilstand vi kan
    angre, og det samme gjelder en sending som pågår nå.

    KASTER ALDRI, av nøyaktig samme grunn som `varsle_runde_venter`: dette
    kalles fra attesterings- og aktiveringsflyten, og en fullmaktsendring skal
    ikke kunne feile fordi en opprydding gjorde det. Derfor `skjermet` — og
    derfor er dette et Python-kall og ikke en databasetrigger på
    `aktiveringsrunde`: en trigger kan ikke skjermes av en savepoint, så en
    feilende opprydding ville veltet selve aktiveringen. Kalleren eier
    transaksjonen og har alt satt `disponit.*`.
    """
    sql = ("UPDATE varsel SET lest_ts=coalesce(lest_ts, now()),"
           f" epost_status=CASE WHEN epost_status IN {I_KO}"
           "                    THEN 'ikke_aktuelt'"
           "                    ELSE epost_status END"
           " WHERE tenant=%s AND art='attestering_venter'"
           "   AND ressurs_type='policyutkast' AND ressurs_id=%s"
           f"   AND hendelse=%s AND (lest_ts IS NULL"
           f"                        OR epost_status IN {I_KO})")
    p: tuple = (tenant, utkast_id, str(runde))
    if bruker_id is not None:
        sql += " AND bruker_id=%s"
        p += (bruker_id,)
    res = skjermet(conn, lambda: conn.execute(sql + " RETURNING id",
                                               p).fetchall())
    return 0 if res is FEILET else len(res)


def oppdater_gjenstaar(conn: psycopg.Connection, *, tenant: str,
                       utkast_id: str, runde: int, gjenstaar: int) -> int:
    """«{gjenstaar} attestasjon(er) gjenstår» må fortsatt være sant. -> antall.

    Tallet er et PARAMETER på raden, og parametrene ble skrevet én gang, da
    runden åpnet (Codex P2). Krever runden to godkjenninger, sto det `2` i
    hvert varsel for alltid — også etter at forfatteren attesterte. Den
    uavhengige godkjenneren leste da at to gjenstår når det bare var hans egen
    igjen, og e-posten som gikk ut etterpå sa det samme: den rendres fra de
    samme parametrene, ved sending. Tallet er ikke pynt, det er det som skiller
    «du er den siste» fra «dette kan vente».

    Alternativet var å utlede tallet ved visning. Det ville flyttet
    rundetilstanden inn i innboksen OG inn i senderen — som er kryss-tenant og
    med vilje ikke vet noe om aktiveringsrunder. Parameteret oppdateres derfor
    der tallet faktisk endrer seg: ved hver attestering.

    Bare ULESTE varsler røres. Et lest varsel er historie — mottakeren har
    kvittert det ut, og å skrive om teksten under henne ville vært å endre noe
    hun alt har sett. `pensjoner_runde` har dessuten nettopp satt aktørens egen
    rad lest, så hennes varsel faller ut av dette av seg selv.

    KASTER ALDRI (regel 2): dette kalles midt i attesteringsflyten, og en
    fullmaktsendring skal ikke kunne feile fordi en varseltekst ikke lot seg
    oppdatere. Derfor `skjermet`, som resten av modulen.
    """
    res = skjermet(conn, lambda: conn.execute(
        "UPDATE varsel SET parametre ="
        " jsonb_set(parametre, '{gjenstaar}', to_jsonb(%s::int))"
        " WHERE tenant=%s AND art='attestering_venter'"
        "   AND ressurs_type='policyutkast' AND ressurs_id=%s"
        "   AND hendelse=%s AND lest_ts IS NULL"
        "   AND parametre->>'gjenstaar' IS DISTINCT FROM %s::text"
        " RETURNING id",
        (gjenstaar, tenant, utkast_id, str(runde), gjenstaar)).fetchall())
    return 0 if res is FEILET else len(res)


def innboks(conn: psycopg.Connection, *, tenant: str, bruker_id: str,
            kun_uleste: bool = False, grense: int = 50) -> list[dict]:
    """Mottakerens egne varsler: ULESTE FØRST, deretter nyeste først.

    Uleste først er ikke en smakssak, det er det som gjør telleren sann
    (Codex P2). Med ren `opprettet DESC` og en side på `grense` rader var et
    gammelt ulest varsel skjøvet ut av siden så snart det lå `grense` NYERE
    leste over det — mens `antall_uleste` fortsatt talte det. Innboksen sa da
    «1 ulest» og viste ikke ett eneste ulest varsel, uten paginering eller
    ulest-filter å finne det med. Og det er nettopp det uleste som er hele
    poenget: varselet er en oppfordring om noe som VENTER.

    Er det flere uleste enn `grense`, viser siden `grense` uleste — ikke en
    løgn, men heller ikke alt. Køen tømmes likevel: merkes ett som lest,
    faller det ned under de uleste ved neste henting, og det neste kommer til
    syne. Før fantes ingen slik vei i det hele tatt.

    Sorteringen er dekket av `varsel_innboks` (migrasjon 026), som har
    `(lest_ts IS NULL) DESC` som eget ledd — uten det ville standardvisningen
    sortert en voksende flertenant-tabell for hver henting.
    """
    sql = ("SELECT id, art, ressurs_type, ressurs_id, tekstnokkel, parametre,"
           " opprettet, lest_ts FROM varsel"
           " WHERE tenant=%s AND bruker_id=%s")
    if kun_uleste:
        sql += " AND lest_ts IS NULL"
    sql += " ORDER BY (lest_ts IS NULL) DESC, opprettet DESC LIMIT %s"
    return [{"id": r[0], "art": r[1], "ressurs_type": r[2], "ressurs_id": r[3],
             "tekstnokkel": r[4], "parametre": r[5],
             "opprettet": r[6].isoformat(),
             "lest": r[7] is not None}
            for r in conn.execute(sql, (tenant, bruker_id, grense)).fetchall()]


def antall_uleste(conn: psycopg.Connection, *, tenant: str,
                  bruker_id: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM varsel WHERE tenant=%s AND bruker_id=%s"
        " AND lest_ts IS NULL", (tenant, bruker_id)).fetchone()[0]


def merk_lest(conn: psycopg.Connection, *, tenant: str, bruker_id: str,
              varsel_id: int) -> bool:
    """Bare MINE varsler, og bare én gang.

    `bruker_id` i WHERE er ikke pynt oppå RLS: RLS skiller tenanter, ikke
    mennesker inne i samme tenant. Uten den kunne én bruker merket en kollegas
    varsel som lest — og dermed skjult at noe ventet på henne.

    Å LESE VARSELET AVLYSER E-POSTEN (Codex P2). Timeren går hvert 5. minutt,
    så det vanligste tilfellet av alle er at mottakeren sitter i portalen når
    varselet dukker opp: hun ser det, merker det lest, og fikk før likevel
    e-posten noen minutter senere — om noe hun allerede hadde kvittert ut. Det
    er samme løgn som `pensjoner_runde` finnes for å hindre, bare i den
    kanalen hun ikke kan lukke selv.

    Køen er `I_KO`, som hos de to andre veiene som avlyser en sending: også en
    rad som venter på nytt forsøk skal stoppes, ellers ville `varsel_rekoe`
    løftet den tilbake til `koet` etter at hun leste den. `under_sending` står
    utenfor av samme grunn som der — den raden er i et SMTP-kall akkurat nå.
    Den fanges i stedet av klaimets eget `lest_ts IS NULL` (migrasjon 027),
    som er den siste porten før SMTP.
    """
    return conn.execute(
        "UPDATE varsel SET lest_ts=now(),"
        f" epost_status=CASE WHEN epost_status IN {I_KO}"
        "                    THEN 'ikke_aktuelt'"
        "                    ELSE epost_status END"
        " WHERE tenant=%s AND bruker_id=%s"
        " AND id=%s AND lest_ts IS NULL RETURNING id",
        (tenant, bruker_id, varsel_id)).fetchone() is not None


def sett_kanal(conn: psycopg.Connection, *, tenant: str, bruker_id: str,
               kanal: str) -> str:
    """Valget eier ba om. Ukjent verdi avvises — en feilstavet kanal skal ikke
    stille slå av varslingen.

    «Kun portal» gjelder OGSÅ e-post som alt ligger i kø (Codex P2). `_kanal`
    leses når varselet OPPRETTES — så uten dette ville et valg tatt i dag ikke
    rørt de varslene som ble laget i går, og brukeren ville fått nettopp den
    e-posten hun nettopp sa nei til. En avmelding som først virker på neste
    hendelse er ikke en avmelding; den er en forklaring på hvorfor det
    fortsatte litt til. Samme transaksjon som valget: enten gjelder begge
    deler, eller ingen.

    Køen er `I_KO`, ikke `koet` alene: en rad som feilet og venter på nytt
    forsøk blir løftet tilbake i køen av `varsel_rekoe`, og en avmelding som
    ikke tar den, avlyser bare det som tilfeldigvis ikke hadde feilet ennå.

    Motsatt vei re-køes INGENTING. `ikke_aktuelt` er et bevisst fravær, og å
    vekke det opp igjen ville sendt e-post om runder som kan ha rukket å bli
    både attestert og lukket i mellomtiden. Nye varsler etter omvalget får
    `koet` som normalt — det er den kanalen valget gjelder for.

    «Alt ligger i kø» rakk likevel ikke det som ble opprettet i det samme
    øyeblikket (Codex P2). Derfor tas mottakerens kanalvalg-lås FØR valget
    skrives: en samtidig `opprett` er da enten ferdig — og raden dens synlig
    for oppryddingen under — eller den venter, og leser det nye valget når den
    slipper til.
    """
    if kanal not in ("epost_og_portal", "kun_portal"):
        raise ValueError(f"ukjent varselkanal: {kanal!r}")
    _laas_kanalvalget(conn, tenant, bruker_id)
    conn.execute(
        "INSERT INTO varselvalg (tenant, bruker_id, kanal) VALUES (%s,%s,%s)"
        " ON CONFLICT (tenant, bruker_id) DO UPDATE"
        " SET kanal=EXCLUDED.kanal, oppdatert=now()",
        (tenant, bruker_id, kanal))
    if kanal == "kun_portal":
        conn.execute(
            "UPDATE varsel SET epost_status='ikke_aktuelt'"
            f" WHERE tenant=%s AND bruker_id=%s AND epost_status IN {I_KO}",
            (tenant, bruker_id))
    return kanal


def hent_kanal(conn: psycopg.Connection, *, tenant: str,
               bruker_id: str) -> str:
    return _kanal(conn, tenant, bruker_id)
