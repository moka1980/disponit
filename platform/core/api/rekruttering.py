"""M-57s leseflate + signeringsvei (utførelsesarmen, første ben).

Tre ruter, formet av flatens kontrakt (flater/rekruttering.js):

* GET  /v1/rekruttering/prosesser — en LETT indeks over alle ureapet
  prosesser (id, opprettet, evalueringsstatus, kandidatantall) og full
  payload — kandidater, vekter, innstilte lister — for ÉN: den navngitte
  med `?prosess_id=`, ellers den nyeste. En ukjent eller utløpt id er
  404, aldri «ta den nyeste» (#183). Lest RETT fra 057-lagrene under RLS.
  `decisions:read` (flatens svakeste ledd, WCAG-flate-formen).
* POST /v1/rekruttering/lister/{id}/signer — signeringen. Går gjennom
  DEN EKTE kjeden: `signer_utsendingsliste` (056) med øktens bruker som
  signatar og SP-2-nøkkel fra Idempotency-Key. Endepunktet verifiserer
  først at innholdshashen KROPPEN bærer er listens — signataren signerer
  bytene dialogen viste kortformen av, aldri bare et liste-id.
* POST /v1/rekruttering/prosesser/{id}/blinding — avskruing er en
  AUDITERT handling med varig revisjonsevidens, og evidensdesignet er
  #159 (K2: selvattestert avskruing er ikke evidens). Til #159 lander,
  svarer ruten en KODET avvisning — aldri en stille suksess uten spor.
  Og flaten TILBYR ikke handlingen så lenge det er svaret (Codex P2,
  runde 4): bryteren der er et deaktivert tilstandsmerke. Ruten står
  igjen som det ærlige svaret til en direkte API-kaller.

Alle tre bærer rollback-kontrakten: er `m57_ats` i
`DISPONIT_INAKTIVE_MODULER`, svarer ruten 503 `modul_inaktiv` FØR
tilkoblingen hentes (`_modul_inaktiv`) — deaktivering av M-57 skal stanse
M-57, den irreversible signeringen først av alt.

Vektene: den varige kilden er stillingsprofilen (#162-kjeden). Til den
finnes leses vektene av evalueringsartefaktets `vekter`-felt (skrevet av
kjøringen), med fall til vekt 3 per krav — flaten regner poeng
klientsidig av nedbrytningen uansett, og serveren lyver aldri om
opphavet: feltet `vekter_kilde` sier hvilken vei som ga tallene. Og
flaten SIER det nå videre (Codex P1): reserven er et utgangspunkt
brukeren kan skyve på, aldri en stille påstand om at rekkefølgen er
evalueringens.
"""
from __future__ import annotations

import json

import psycopg

from .autorisasjon import scopes_for_roller

#: Signeringens mutasjonsscope. ÉN konstant, fordi den måles to ganger:
#: ved autentiseringen (`_browserkontekst`) og en gang til under
#: medlemskapslåsen rett før den irreversible skrivingen (Codex P1). To
#: strenglitteraler kunne drevet fra hverandre, og da ville den andre
#: målingen sett på noe annet enn den første.
_SIGNERINGSSCOPE = "bestilling:opprett"

#: Unik-kravet SP-2-nøkkelen bor i: `UNIQUE (tenant, operasjonsnokkel)` på
#: `utsendingssignatur` (056 §2). PostgreSQL navnga det selv — 056 er
#: hash-pinnet, så navnet er like fast som resten av migrasjonen. Det
#: leses fordi FIRE unike krav på samme tabell deler SQLSTATE 23505, og to
#: av dem betyr motsatte ting for kalleren (se signeringens
#: `UniqueViolation`-arm). Testen måler navnet mot katalogen, så et
#: eventuelt avvik peker på seg selv i stedet for å falle stille tilbake.
_NOKKELBRUDD = "utsendingssignatur_tenant_operasjonsnokkel_key"

#: Modulen disse rutene tilhører — samme form som `app.BESLUTNINGSMODUL`
#: for M-1, og samme streng som `oppdrag.eiermodul` bærer for kjedens egne
#: oppdrag (`m57_ats`, seeden og HTTP-testenes fixture).
REKRUTTERINGSMODUL = "m57_ats"


def _modul_inaktiv(tjeneste, rid):
    """Er M-57 slått av? -> ferdig 503-svar, ellers None.

    ROLLBACK-KONTRAKTEN GJELDER OGSÅ HER (Codex P1). `DISPONIT_INAKTIVE_
    MODULER` er veien en modul rulles tilbake på i drift: registeret
    settes inaktivt, tjenesten restartes, og API-et skal svare DEFINERT
    (503 `modul_inaktiv`) i stedet for å utføre handlingen. Bare M-1s
    beslutningsvei konsulterte `Tjeneste.inaktive_moduler`; de tre
    rekrutteringsrutene gikk utenom. Å deaktivere `m57_ats` stanset
    altså ikke M-57 — den irreversible signeringen inkludert, som er
    nøyaktig den handlingen en rollback finnes for å stoppe.

    FØR TILKOBLINGEN, av samme grunn som i `_beslutning`: en deaktivert
    modul skal ikke bruke en poolplass, og ikke rekke å åpne en
    transaksjon som må rulles. `halvferdige_transaksjoner = 0` i
    rollback-artefaktet er en egenskap ved plasseringen.

    Lest av `tjeneste.inaktive_moduler`, som er BOOT-lest — ingen
    fillesing i request-path, og deaktivering restarter uansett
    prosessen.

    SVARET BYGGES AV `app._feilsvar`, ikke av `policyadmin_http._feil`
    som resten av modulen bruker. De to leser ULIKE tabeller: `_feilsvar`
    slår opp i `feil.FEIL` — kontrakten som DATA, der `modul_inaktiv` står
    med 503 — mens `_feil` har sin egen lokale `_FEIL_HTTP` for
    policyadmins koder og faller til 409 for alt annet. Rollback-
    kontrakten sier 503, og en 409 ville sagt «konflikt, prøv noe annet»
    om en modul som er slått av. Det er samme helper `_beslutning` bruker
    for nøyaktig denne koden.
    """
    from .app import _feilsvar
    if REKRUTTERINGSMODUL not in tjeneste.inaktive_moduler:
        return None
    tjeneste.logg.hendelse("modul_inaktiv", rid, art="drift",
                           modul=REKRUTTERINGSMODUL)
    return _feilsvar("modul_inaktiv", rid)


def _leseauth_beslutninger(tjeneste, request, conn, rid):
    """Som policyadmin_http._leseauth, men for `decisions:read` — flatens
    lese-scope. -> (tenant, bid)."""
    from . import kjerne
    from .app import _autentiser
    from .policyadmin_http import _Avbrudd, _feil, _gjenopprett_kontekst
    try:
        auth = _autentiser(tjeneste, request, conn, rid, "decisions:read")
    except kjerne.Feilsvar as f:
        raise _Avbrudd(_feil(f.kode, rid))
    bid = auth.token_id.split("sesjon:", 1)[-1]
    conn.rollback()
    _gjenopprett_kontekst(conn, auth.tenant, bid, rid)
    return auth.tenant, bid


def _vekter_lesbare(v: object) -> bool:
    """Skriveveiens egen vektport, lest på riktig side av lagringen.

    `evaluering.ranger` avviser et tomt vektsett og enhver verdi som
    ikke er et ikke-negativt heltall — `ugyldige_vekter`. `bool` er en
    `int` i Python og måles derfor eksplisitt bort: `true` er ingen vekt.
    Predikatet er skriveveiens, ikke en ny regel; API-laget importerer
    aldri `modules.*` (ingen linje her gjør det), så det står som en
    LESNING av samme dom, ikke som en delt maskin.

    …og taket er samme dom, lest samme sted (Codex P2, runde 10).
    `ranger` avviser en vekt over `Number.MAX_SAFE_INTEGER` fordi et
    heltall over det siste eksakte `double`-tallet er avrundet ALT i
    flatens `JSON.parse`: den viste vekten og poengsummen den regnes
    inn i er da ikke lenger evalueringens. Stort nok blir tallet
    `Infinity`, og skyverens tak — som utledes av de samme verdiene —
    står igjen på 10 mens poengene er uendelige. Porten står også her,
    for det er HER svaret til flaten settes sammen: en vekt vi ikke kan
    formidle er ingen opplysning, og faller til reserven under.

    Den ekte lukkingen er formen ved LAGRINGSGRENSEN — eiers K2-dom
    A (#176-tråden): CHECK/trigger på `kandidat_evalueringsartefakt.
    artefakt`, og rå INSERT trukket tilbake fra runtime. Denne porten er
    dybdeforsvaret som skal stå også etter A; det forbudte var at den
    sto ALENE.
    """
    return (isinstance(v, dict) and bool(v)
            and all(isinstance(k, str) for k in v)
            and all(isinstance(x, int) and not isinstance(x, bool)
                    and 0 <= x <= 2 ** 53 - 1 for x in v.values()))


def _kandidater(conn, tenant, prosess_id):
    """Kandidatene i én prosess, lest RETT fra 057-lageret under RLS.

    AVGRENSNINGEN LIGGER I KALLEREN (#183, landet i denne PR-en). Hver
    prosess kan bære 5000 kandidater (katalogens harde løfte) i inntil
    365 døgn, så et svar som løp over alle ureapet prosesser vokste uten
    tak. Nå kalles denne funksjonen for ÉN prosess per forespørsel —
    `prosesser_endepunkt` velger den og lar resten være indeksrader — og
    målingen som holder det er `test_svaret_vokser_ikke_med_antall_prosesser`
    og `test_den_valgte_prosessen_hentes_paa_id`.

    Det som KAN gjøres uten en ny kontrakt, gjøres her: lesningen slutter
    å hente det den kaster. `kildetekst` er hele den blindede
    søknadsteksten, `avmaskering` er tokenkartet — ingen av dem leses av
    noen linje under, og de er den desidert tyngste delen av artefaktet.
    De ble likevel dratt ut av basen, over forbindelsen og inn i minnet,
    for hver kandidat i hver prosess. Nøkkelsubtraksjon (`jsonb - text`)
    og ikke en positiv projeksjon: da overlever ethvert felt en fremtidig
    produsent legger til, `status` inkludert.

    INTERVJUSPØRSMÅLENE HENTES IKKE I DET HELE TATT (eiers
    produktbeslutning 27/8, PR #224). De hører til innkallingen av de
    beste, ikke til utvelgelsen, så feltet forlater aldri serveren her —
    og da er både artefaktkopien og 057-lageret (`kandidat_intervjusporsmal`)
    noe denne lesningen kaster. Regelen over gjelder også dem: lesningen
    slutter å hente det den kaster, og på en prosess med inntil 5000
    kandidater er en JOIN mot lageret unødvendig arbeid i basen og
    unødvendig nyttelast over forbindelsen.
    Subtraksjonen av `intervjusporsmal` blir stående — artefaktet kan
    fortsatt bære en kopi, og den skal ut av svaret på samme måte som
    `kildetekst` og `avmaskering`. Lageret består urørt som
    shortlist-arcens kilde (#225).

    OG SUBTRAKSJONEN GJØRES BARE PÅ ET OBJEKT (Cursor P1). `jsonb - text`
    er definert for objekt og array; mot en JSON-SKALAR feiler den i
    BASEN (`cannot delete from scalar`, 22023) — før noen Python-linje
    ser verdien. 057 har ingen formsjekk på `artefakt`, så `'3'::jsonb`
    er en lovlig INSERT for runtime, og ett slikt artefakt ville tatt ned
    hele tenantens prosessliste. `jsonb_typeof(...) = 'object'` er porten
    der subtraksjonen står; alt annet kommer ut som NULL og møter
    type-porten under på samme form som et reapet artefakt.
    """
    rader = conn.execute(
        "SELECT a.kandidat_id,"
        "       CASE WHEN jsonb_typeof(a.artefakt) = 'object'"
        "            THEN a.artefakt - 'kildetekst' - 'avmaskering'"
        "                            - 'intervjusporsmal' END,"
        "       jsonb_typeof(a.artefakt) = 'object'"
        "  FROM kandidat_evalueringsartefakt a"
        " WHERE a.tenant=%s AND a.prosess_id=%s AND a.slettet_ts IS NULL"
        " ORDER BY a.kandidat_id", (tenant, prosess_id)).fetchall()
    kandidater, vekter, kilde, lest = [], None, "standard", []
    lesbare_vekter = []
    for kid, artefakt, er_objekt in rader:
        # TYPEN ER OGSÅ EN PORT (Cursor P1). `x or {}` verner mot NULL og
        # tomt, aldri mot FEIL TYPE: `{...}` er en sann `funn`, `["drift"]`
        # er en sann `oppfylt`, og begge er `jsonb` runtime kan INSERTe
        # (057 har ingen formsjekk på `artefakt`). Ett giftig artefakt ga
        # da `AttributeError` inne i utledningen, og siden kalleren den
        # gang løp over HVER prosess, ble svaret 500 for HELE tenantens
        # prosessliste — signeringsflaten inkludert. Etter #183 leses én
        # prosess per forespørsel, så porten verner den valgte; den er
        # like nødvendig, for det er DEN flaten signerer fra.
        #
        # OG Å NORMALISERE ER IKKE Å LESE. Å sette et ulesbart `funn` til
        # `[]` gjør kandidaten GRØNNERE enn før — «ingen funn» er nettopp
        # halve anbefalingen. Å verne mot krasjet uten dette leddet ville
        # byttet en 500 mot et falskt grønt lys, som er den dyrere feilen
        # foran en irreversibel utsendelse. Derfor bæres lesbarheten med:
        # er artefaktet ikke et objekt, eller har ett av de to feltene feil
        # type, er raden INGEN opplysning — og ingen opplysning kan ikke
        # bevise en anbefaling. Den faller til `vurderes`, samme fail-safe
        # som resten av trafikklyset.
        art = artefakt if isinstance(artefakt, dict) else {}
        # OG VEKTENE ER MER ENN EN DICT (Codex P2, runde 9). Porten
        # spurte om FORMEN og ikke om verdiene, så `{"drift": null}`,
        # `{"drift": -3}`, `{"drift": true}` og `{}` ble alle tatt imot
        # som stillingsprofilens egne tall. Skriveveien avviser nøyaktig
        # de fire (`ranger`: `ugyldige_vekter` — ikke-tom, ekte int, ikke
        # bool, ikke negativ), men den porten står i en funksjon runtime
        # kan gå utenom med en rå INSERT i lageret.
        #
        # De to sidene betaler ulikt. Trafikklyset måler `set(oppfylt) ==
        # set(vekter)`, altså bare NØKLENE: en profil med ugyldige tall
        # ga fortsatt «Anbefalt», med en vekting ingen kunne stå inne
        # for. Flaten regner `Number(verdi)` på den samme verdien og får
        # `0` av `null` og `NaN` av en streng — skyveren står ett sted og
        # tallet ved siden av sier noe annet, på flaten der signeringen
        # skjer. Er vektene ikke lesbare, er de INGEN opplysning, og
        # reserven under (`{krav: 3}`) er det ærlige svaret — den er
        # allerede merket i `vekter_kilde`, så flaten sier fra selv.
        if _vekter_lesbare(art.get("vekter")):
            lesbare_vekter.append(art["vekter"])
        raa_funn, raa_oppfylt = art.get("funn"), art.get("oppfylt")
        funn = raa_funn if isinstance(raa_funn, list) else []
        oppfylt = raa_oppfylt if isinstance(raa_oppfylt, dict) else {}
        lesbart = (bool(er_objekt) and isinstance(raa_funn, list)
                   and isinstance(raa_oppfylt, dict)
                   and all(isinstance(f, dict) for f in funn))
        # Spørsmålslagerets egen typeport falt bort sammen med JOIN-en
        # (#224): et felt som aldri forlater serveren, kan ingen giftig
        # `sporsmal`-rad nå flaten gjennom. Porten står nå i sin
        # sterkeste form — fravær.
        lest.append((kid, funn, oppfylt, lesbart))
    # VEKTENE ER ENDELIGE FØR TRAFIKKLYSET UTLEDES (Cursor P1). Reserven
    # under leser HVER kandidats krav, så den kan ikke stå etter en
    # dømming som må måle mot den — derfor to pass over de samme radene.
    #
    # OG PROSESSENS VEKTING MÅ ARTEFAKTENE VÆRE ENIGE OM (Cursor P2,
    # runde 10). Valget var «første LESBARE sett i `kandidat_id`-
    # rekkefølge» — altså avgjort av en UUID. Vekten er stillingens, ikke
    # kandidatens, så to artefakter som sier ULIKE ting er ikke et valg
    # mellom to kilder; det er beviset på at ingen av dem kan tas for
    # prosessens. Runtime har INSERT på det uformsjekkede lageret (eiers
    # K2-dom A / #162), så et smalt, gyldig sett på lav UUID vant over
    # profilens eget — og trafikklyset måler `set(oppfylt) ==
    # set(vekter)`, så nettopp et SMALERE sett gjør «Anbefalt» lettere å
    # oppnå, foran en irreversibel signering. Uenighet felles derfor til
    # reserven med `vekter_kilde="standard"`, samme fail-safe som et
    # uleselig sett får: ingen entydig opplysning er ingen opplysning.
    # Uleselige sett hoppes fortsatt over uten å telle som uenighet —
    # de er ikke et motstridende svar, de er intet svar.
    if lesbare_vekter and all(v == lesbare_vekter[0]
                              for v in lesbare_vekter[1:]):
        vekter, kilde = lesbare_vekter[0], "evalueringsartefakt"
    if vekter is None:
        krav = sorted({k for _kid, _funn, oppfylt, _les in lest
                       for k in oppfylt})
        vekter = {k: 3 for k in krav}
    for kid, funn, oppfylt, lesbart in lest:
        # ANBEFALINGEN ER OPPFYLTE KRAV, IKKE FRAVÆRET AV FUNN (Codex P1).
        # Den kanoniske evalueringsartefakten har ingen `status` i det hele
        # tatt (`evaluering.evaluer_kandidat` returnerer `funn`, `oppfylt`,
        # `intervjusporsmal`, `avmaskering`, `kildetekst`), så det er ALLTID
        # reserven som gir trafikklyset. Og funn og kravoppfyllelse er to
        # uavhengige felt: `_krev_helt_svar` godtar et komplett svar med tom
        # `funn` og bare `false` i `oppfylt` — en kandidat som ikke oppfyller
        # ET ENESTE krav i stillingsprofilen. Den fikk «Anbefalt» utelukkende
        # fordi ingen risiko var notert. Grønt lys må BEVISES av kravene:
        # anbefalt krever at alle målte krav er oppfylt, og at det finnes
        # krav å måle. Fail-safe: alt annet faller til «Bør vurderes», som er
        # en oppfordring til å lese kandidaten, ikke en påstand om henne.
        # …og utledningen tar ALDRI imot en skrevet `status` (Cursor P1
        # 10:01): runtime har INSERT på lageret, og `art.get("status")`
        # foran reserven lot en produsent skrive «anbefalt» forbi hele
        # dømmingen. Kanonisk artefakt har ikke feltet; står det der, er
        # det nettopp derfor IKKE en kilde.
        #
        # …OG OPPFYLLELSEN MÅLES SOM SKRIVEVEIEN MÅLER DEN (Cursor P1,
        # to ledd til). Dømmingen var `oppfylt and all(oppfylt.values())`,
        # og den leste noe annet enn kilden den speiler:
        #   * SANNHETSVERDIEN til hva som helst. `"false"` — den vanligste
        #     JSON-feilen en modell gjør — er en SANN streng. Skriveveien
        #     avviser den eksplisitt (`ikke_boolsk_oppfyllelse`, både i
        #     `evaluer_kandidat` og i `ranger`), men leseveien tok imot
        #     den som et ja og ga grønt lys. `is True`, ikke truthy.
        #   * BARE DE KRAVENE SOM STO DER. `{"drift": true}` mot en profil
        #     som krever drift OG sky ble «Anbefalt» fordi det ikke fantes
        #     et `sky`-oppslag å feile på. `ranger` har begge speilportene
        #     — `krav_utenfor_profilen` og krav som MANGLER — og de er
        #     samme port: kravsettet skal være NØYAKTIG profilens. Målt
        #     mot `vekter`, som nettopp derfor er endelige først.
        # Ingen av de to er nye regler; de er skriveveiens egne, lest på
        # riktig side av lagringen.
        #
        # …OG ET ULESBART ARTEFAKT BEVISER INGENTING (`lesbart`, se over):
        # grønt lys krever at raden faktisk var lesbar, ikke bare at det
        # som ble lest ut av den så greit ut.
        status = ("innstilt_avslag" if any(
                      isinstance(f, dict)
                      and f.get("kategori") == "krav_ikke_dokumentert"
                      for f in funn)
                  else "anbefalt" if lesbart and not funn and vekter
                  and set(oppfylt) == set(vekter)
                  and all(v is True for v in oppfylt.values())
                  else "vurderes")
        kandidater.append({
            "kandidat_id": str(kid),
            "oppfylt": oppfylt,
            "status": status,
            "funn": funn,
            # Ingen intervjuspørsmål i prosessflaten heller (eiers
            # produktbeslutning 27/8, PR #224): de hører til innkallingen
            # av de beste — lageret (kandidat_intervjusporsmal) består og
            # er shortlist-arcens kilde (#225).
        })
    return kandidater, vekter, kilde


def _lister(conn, tenant, oppdrag_id):
    """Innstilte lister på evalueringsoppdraget: nyeste versjon per serie
    (den uten barn), med signaturstatus.

    SIGNATURSTATUSEN ER SERIENS, IKKE RADENS (Codex P2). Signatur-sloten
    er `en_signert_versjon_per_serie` (056) — UNIK på (tenant,
    utkast_serie), altså én per SERIE. `opprett_utsendingsliste` hindrer
    ikke at et barn lages etter at forelderen ble signert, og da er
    barnet spissen denne spørringen returnerer. Med et eksakt
    liste-treff meldte raden `signert: false`: flaten viste en
    handlingsklar knapp på en versjon som ALDRI kan signeres — det
    eneste mulige utfallet er `serien_alt_signert`. Joinen går derfor på
    serien, som er der sloten faktisk bor; unik-indeksen holder treffet
    på høyst én rad.
    """
    rader = conn.execute(
        "SELECT l.liste_id, l.listetype, l.antall, l.innhold_hash,"
        "       (s.utkast_serie IS NOT NULL) AS signert"
        "  FROM utsendingsliste l"
        "  LEFT JOIN utsendingssignatur s"
        "    ON s.tenant = l.tenant AND s.utkast_serie = l.utkast_serie"
        " WHERE l.tenant=%s AND l.oppdrag_id=%s"
        "   AND NOT EXISTS (SELECT 1 FROM utsendingsliste b"
        "                    WHERE b.tenant=l.tenant"
        "                      AND b.utkast_serie=l.utkast_serie"
        "                      AND b.forrige_liste_id=l.liste_id)"
        " ORDER BY l.opprettet", (tenant, oppdrag_id)).fetchall()
    return [{"liste_id": str(r[0]), "listetype": r[1], "antall": r[2],
             "innhold_hash": r[3], "signert": bool(r[4])} for r in rader]


def _fullfort_replay(conn, tenant, nokkel, liste_id, bid) -> bool:
    """Står signaturen denne forespørselen ber om ALLEREDE?

    Predikatet er `signer_utsendingsliste`s eget, med samme snevre likhet:
    bare den nøyaktig identiske operasjonen — nøkkel + liste + signatar —
    er et replay. En nøkkel som bærer annet innhold er ikke en gjentakelse
    av noe, og faller til 056s egen dom.
    """
    return conn.execute(
        "SELECT 1 FROM utsendingssignatur WHERE tenant=%s"
        " AND operasjonsnokkel=%s AND liste_id=%s AND signatar=%s",
        (tenant, nokkel, liste_id, bid)).fetchone() is not None


def prosesser_endepunkt(tjeneste, request):
    """GET /v1/rekruttering/prosesser."""
    from .app import _rid
    from .policyadmin_http import _feil, _med_conn, _ok
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av

    def kjor(conn):
        tenant, _bid = _leseauth_beslutninger(tjeneste, request, conn, rid)
        prosesser = []
        rader = []
        # EVALUERINGENS TILSTAND FØLGER MED (Codex P2). Prosessen FØDES
        # mens kjøringen står på (`plukket` — 057s fødselsport), og
        # kandidatartefaktene skrives inkrementelt etterpå. Spørringen
        # returnerte hver ureapet prosess uten et ord om oppdraget, så en
        # evaluering midt i løpet ble presentert som en FERDIG rangering:
        # kandidater som ennå ikke er vurdert, mangler rett og slett, og
        # ingenting på skjermen sa det. Samme for en `feilet` eller
        # `kansellert` kjøring — der kommer resten aldri.
        #
        # Statusen returneres i stedet for å filtreres: en prosess som
        # forsvinner fra flaten er sin egen løgn («ingen aktiv
        # rekrutteringsprosess»), og oppdraget er dessuten den ENESTE
        # veien inn til å se at noe kjører. Joinen er trygg — `prosess_
        # oppdrag_fk` (057) garanterer nøyaktig én treffende rad.
        #
        # ...OG DET GJØR STARTTIDSPUNKTET (Codex P2). Med flere prosesser
        # tegner flaten en velger, og den hadde bare `prosess_id` å sette
        # på hver oppføring: brukeren måtte velge mellom rå UUID-er før
        # hun kunne lese kandidater eller signere en irreversibel
        # utsendelse. Det NAVNET velgeren ber om — stillingens tittel —
        # finnes ikke å hente: `rekrutteringsprosess` har ingen
        # navnekolonne, og oppdragets payload er kryptert og bærer bare en
        # `stillingsprofil_ref`. Selve tittelen bor i stillingsprofilen
        # (#162-kjeden), som ikke finnes ennå; å grave den fram herfra
        # ville krevd dekrypteringsvei og profiloppslag — ny maskin i en
        # fiksrunde (K1).
        #
        # Det som FINNES i klartekst, og som skiller prosessene fra
        # hverandre for et menneske, er når de startet. `p.opprettet` sto
        # alt i ORDER BY-en; nå følger den med ut, og flaten setter «Startet
        # <dato> · kandidater: N» på oppføringen. Feltet `navn` sendes
        # bevisst ikke: flaten bruker `p.navn` når det en dag finnes, og
        # serveren skal ikke kalle et tidsstempel for et navn.
        # RETENSJONSANKRE UTEN INNHOLD HOLDES UTE AV VELGEREN (Codex
        # P2, #220). Claimen føder nå en prosess for HVER evaluering,
        # men den skipede controlleren skriver ingen kandidatlagre — et
        # terminalt oppdrag med tom prosess har derfor ingenting denne
        # flaten kan vise, og ville fortrengt en ekte prosess som
        # standardvalg i 30–365 døgn. En PÅGÅENDE tom prosess vises
        # fortsatt (evalueringens tilstand-doktrine over); et terminalt
        # løps status og rapport bor i evalueringsseksjonen.
        #
        # OG FRISTEN HÅNDHEVES HER OGSÅ, IKKE BARE REAPERENS MERKE
        # (Cursor P1, #220). `slettet_ts IS NULL` alene måler når
        # `reap_kandidatdata` RAKK å kjøre, ikke når kundens frist gikk
        # ut — og reaperen er en batch. I vinduet mellom fristen og
        # batchen sa rapportveien (`rekrutteringsrapport_detalj`,
        # `_anker_lever`) og evalueringslisten alt `slettet: true` /
        # `rapport_klar: false`, mens DENNE flaten fortsatt serverte
        # funn, sitater og intervjuspørsmål for hver kandidat gjennom
        # `_kandidater`. Samme grense som reaperen og som leseveiene:
        # lukket_ts (avslutningen) eller opprettet (forlatt-fallbacken)
        # pluss kundens døgn. Prosessen faller UT av velgeren når
        # fristen er ute — `_kandidater` kalles aldri for den, så det er
        # samme port ett ledd tidligere, ikke en ny.
        # ANTALLET ER INDEKSENS EGET TALL (Cursor P2, #183). Velgerens
        # etikett er «Startet <dato> · kandidater: N», og N ble lest av
        # `kandidater`-listen — som etter #183 bare finnes på den VALGTE
        # raden. Hver andre prosess i nedtrekket sa derfor «kandidater: 0»
        # om en prosess som kan bære tusenvis, og det er ikke en manglende
        # opplysning: det er en gal en, på den ene kontrollen som skal
        # skille prosessene fra hverandre foran en irreversibel signering.
        #
        # Tellingen er en SKALAR per rad, ikke payloaden tilbake: det er
        # nettopp forskjellen #183 finnes for. Predikatet er ordrett
        # `_kandidater`s eget (`slettet_ts IS NULL`), så indeksens tall og
        # den valgte radens liste kan ikke si ulike ting om samme prosess.
        for pid, oppdrag_id, status, opprettet, antall in conn.execute(
                "SELECT p.prosess_id, p.oppdrag_id, o.status, p.opprettet,"
                "       (SELECT count(*) FROM kandidat_evalueringsartefakt t"
                "         WHERE t.tenant = p.tenant"
                "           AND t.prosess_id = p.prosess_id"
                "           AND t.slettet_ts IS NULL)"
                "  FROM rekrutteringsprosess p"
                "  JOIN oppdrag o ON o.tenant = p.tenant"
                "                AND o.id = p.oppdrag_id"
                " WHERE p.tenant=%s AND p.slettet_ts IS NULL"
                # 069: bestilt tidligsletting er kundens egen grense —
                # samme dom som merket og fristen, fra bestillingsøyeblikket.
                "   AND p.slett_bestilt_ts IS NULL"
                "   AND now() < coalesce(p.lukket_ts, p.opprettet)"
                "               + p.slettefrist_dogn * interval '1 day'"
                "   AND (o.status IN ('opprettet','plukket')"
                "        OR EXISTS (SELECT 1 FROM kandidat_evalueringsartefakt k"
                "             WHERE k.tenant = p.tenant"
                "               AND k.prosess_id = p.prosess_id))"
                " ORDER BY p.opprettet DESC", (tenant,)).fetchall():
            rader.append((pid, oppdrag_id, status, opprettet, antall))

        # ÉN PROSESS BÆRER DATA, RESTEN ER EN INDEKS (#183, Codex P2 fra
        # #176). Løkka kalte `_kandidater` og `_lister` for HVER ureapet
        # prosess. Katalogens løfte er 5000 søknader per bestilling, og
        # prosessraden lever til slettefristen — inntil 365 døgn — så én
        # GET kunne skanne og serialisere titusener av funn- og
        # spørsmålspayloader, holde en pool-forbindelse hele veien, og i
        # verste fall ta knekken på worker-minnet. Det er ikke en
        # spesialkonstruert forespørsel; det er flaten som åpnes.
        #
        # Svaret bærer nå alltid en LETT indeks over prosessene, og full
        # data for ÉN — den navngitte, ellers den nyeste. Da vokser svaret
        # med antall prosesser bare i indeksen, ikke i payloaden.
        bedt = request.query_params.get("prosess_id")
        if bedt is not None:
            # EN UKJENT ID ER «FINNES IKKE», IKKE «ta den nyeste». Å
            # servere en annen prosess' kandidater under den id-en
            # klienten ba om, er en løgn flaten ikke kan oppdage — og
            # prosessen KAN være borte helt lovlig: fristen løp ut mellom
            # to klikk. Samme doktrine som rapportveien: identisk 404,
            # uansett om den aldri fantes eller nettopp falt ut.
            valgt = next((r for r in rader if str(r[0]) == bedt), None)
            if valgt is None:
                return _feil("ikke_funnet", rid, 404)
        else:
            valgt = rader[0] if rader else None

        for pid, oppdrag_id, status, opprettet, antall in rader:
            post = {
                "prosess_id": str(pid),
                "opprettet": opprettet.isoformat(),
                "evaluering_status": status,
                "kandidat_antall": antall,
            }
            if valgt is not None and pid == valgt[0]:
                kandidater, vekter, kilde = _kandidater(conn, tenant, pid)
                post |= {
                    "blinding_av": False,  # avskruingen krever en revisjonshendelse (#159)
                    "vekter": vekter,
                    "vekter_kilde": kilde,
                    "kandidater": kandidater,
                    # DEN VALGTE RADEN TELLER SIN EGEN LISTE (Codex P2).
                    # Tellingen over og `_kandidater` er TO setninger, og
                    # forbindelsen står på psycopg-standarden READ
                    # COMMITTED (`koble`: `autocommit=False`, intet
                    # isolasjonsnivå satt), så hver setning tar sitt eget
                    # øyeblikksbilde. En `plukket` prosess får artefaktene
                    # sine skrevet ETTER HVERT, og et artefakt som
                    # committes mellom de to setningene står i
                    # `kandidater` uten å være med i `kandidat_antall` —
                    # reapingen gir det motsatte. Da sier velgerens
                    # etikett og tabellen under den ULIKE ting om samme
                    # prosess, i samme svar, på flaten der signeringen
                    # skjer: nøyaktig den løgnen indekstallet ble innført
                    # for å fjerne (Cursor P2, «kandidater: 0»).
                    #
                    # Predikatet var alt ordrett `_kandidater`s eget, så
                    # de to kan bare være uenige om TIDEN. Den valgte
                    # raden har en liste å telle, og et tall utledet av
                    # den lesningen kan per konstruksjon ikke motsi den.
                    # Indeksradene beholder skalaren: de bærer ingen liste
                    # å være uenige med, og et øyeblikksbilde er det
                    # ærligste en indeks kan love.
                    "kandidat_antall": len(kandidater),
                    "lister": _lister(conn, tenant, oppdrag_id),
                }
            prosesser.append(post)
        return _ok({"prosesser": prosesser,
                    "valgt_prosess_id": str(valgt[0]) if valgt else None},
                   rid)

    return _med_conn(tjeneste, rid, kjor)


def signer_endepunkt(tjeneste, request):
    """POST /v1/rekruttering/lister/{liste_id}/signer — 056-kjeden.

    Signeringen er den irreversible handlingen i M-57, og endepunktet
    legger ingenting til kjeden: medlemskaps- og materialitetsportene bor
    i `signer_utsendingsliste` (056). Laget HER er at raden er den
    signataren faktisk leste — serie-spissen (ingen barn i serien),
    hash-ekkoet (kroppen bærer innholdshashen dialogen viste) og at
    kandidatdataene bak utsendelsen ikke er reapet bort. Alle svarer 409;
    ingen av dem skriver noe.

    Og fullmakten: 056 låser medlemskapet, men leser med vilje ikke
    `roller` — «rolle- og scope-nivået hører til flatens egen
    autorisasjon (CP3)» (§7b). Den målingen hører altså HIT, og den tas
    under 056s egen lås rett før skrivingen (403). En FULLFØRT operasjon
    passerer alle tre: et replay svarer det den svarte.
    """
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av
    # `:uuid`-konverteren i ruten avviser misformede id-er FØR basen
    # (CodeRabbit major, pre-commit-pass 24/8) — 404 fra ruteren, aldri
    # en psycopg-feil på en tekst som ikke er en UUID.
    liste_id = request.path_params["liste_id"]

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       _SIGNERINGSSCOPE)
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        if not isinstance(kropp.get("innhold_hash"), str):
            raise _Avbrudd(_feil("request_feilformet", rid))
        # SERIELÅSEN FØR PORTEN LESER (#180). Spissjekken under og
        # `signer_utsendingsliste` er to steg i samme READ COMMITTED-
        # transaksjon. Committer `opprett_utsendingsliste` en barnversjon i
        # mellomrommet, stemmer hash-ekkoet fortsatt (forelderens
        # `innhold_hash` er uendret), funksjonen verifiserer ikke spiss —
        # den signerer hvilken som helst `liste_id` — og
        # `en_signert_versjon_per_serie` gir serien nøyaktig ÉN
        # signatur-slot. Feil innhold blir irreversibelt autorisert, og den
        # faktiske spissen permanent usignerbar.
        #
        # Migrasjon 065 tar den SAMME låsen i `opprett_utsendingsliste`, så
        # de to veiene serialiseres på serien. Advisory og ikke `FOR
        # UPDATE`: PostgreSQL krever UPDATE-privilegium for enhver
        # radlåsklausul, også `FOR SHARE` (grensen 019 skrev ned, sitert i
        # 056 §7b), og runtime har kun SELECT på `utsendingsliste`. En
        # advisory-lås krever ingen privilegier — det er derfor den er
        # nåbar fra BEGGE sider.
        #
        # Serien leses og låses i ETT uttrykk: å lese den først og låse
        # etterpå ville vært nok et vindu, bare ett hakk mindre. Finnes
        # ikke listen, låses ingenting — porten under svarer `ikke_funnet`
        # som før, og en lås på en rad som ikke finnes ville uansett ikke
        # vernet noe.
        conn.execute(
            "SELECT pg_advisory_xact_lock("
            "         hashtextextended('m57:serie:' || %s || ':'"
            "                          || utkast_serie::text, 0))"
            "  FROM utsendingsliste"
            " WHERE tenant=%s AND liste_id=%s",
            (tenant, tenant, liste_id))
        rad = conn.execute(
            "SELECT l.innhold_hash, l.antall, l.listetype,"
            "       EXISTS (SELECT 1 FROM utsendingsliste b"
            "                WHERE b.tenant=l.tenant"
            "                  AND b.utkast_serie=l.utkast_serie"
            "                  AND b.forrige_liste_id=l.liste_id),"
            # REAPET MÅLES PÅ FRISTEN, IKKE PÅ MERKET (Cursor P2, #220).
            # `slettet_ts` settes av `reap_kandidatdata` i batcher; leser
            # denne porten bare merket, står vinduet mellom kundens frist
            # og batchen åpent foran den IRREVERSIBLE handlingen: 201 på
            # en utsendelse hvis mottakerdata rapportflaten alt behandler
            # som slettet, og seriens ene signatur-slot brent på den.
            # Samme formel som evalueringslistens `slettet`-felt
            # (`lesing.py`) — merket ELLER fristen, samme grense sett fra
            # kunden. `coalesce(..., false)`: LEFT JOIN-en gir NULL-rader
            # for en 056-liste uten 057-prosess bak seg, og den skal
            # dømmes NØYAKTIG som før (`NULL IS NOT NULL` var false, mens
            # `now() >= NULL` er NULL) — porten utvides, den flyttes ikke.
            "       coalesce(p.slettet_ts IS NOT NULL"
            # 069: … og den BESTILTE tidligslettingen — vinduet mellom
            # kundens bestilling og reaperens sveip skal ikke stå åpent
            # foran den irreversible signeringen, akkurat som fristens.
            "                OR p.slett_bestilt_ts IS NOT NULL"
            "                OR now() >= coalesce(p.lukket_ts, p.opprettet)"
            "                    + p.slettefrist_dogn * interval '1 day',"
            "                false) AS reapet"
            "  FROM utsendingsliste l"
            "  LEFT JOIN rekrutteringsprosess p"
            "    ON p.tenant = l.tenant AND p.oppdrag_id = l.oppdrag_id"
            " WHERE l.tenant=%s AND l.liste_id=%s",
            (tenant, liste_id)).fetchone()
        if rad is None:
            raise _Avbrudd(_feil("liste_ukjent", rid, 404))
        # SERIE-SPISSEN, IKKE BARE ET LISTE-ID (Cursor P1). `_lister`
        # viser kun versjonen UTEN barn, men et `liste_id` overlever
        # redigeringen: en dialog som sto åpen, en parallell editor, et
        # direkte API-kall. Hashen alene fanger det ikke — den GAMLE
        # radens hash er uendret, så ekkoet stemmer fortsatt. Serien har
        # nøyaktig én signatur-slot (`en_signert_versjon_per_serie`, 056):
        # signeres en foreldet versjon, er feil innhold irreversibelt
        # autorisert OG det nye utkastet permanent usignerbart. Samme
        # predikat som `_lister` bruker, målt i samme lesning.
        #
        # SEKVENSIELT, IKKE SERIALISERT — UTSATT TIL #180 (Codex P1 +
        # Cursor P1, runde 2). Denne lesningen og `signer_utsendingsliste`
        # er to steg i samme READ COMMITTED-transaksjon uten lås på
        # serien, så en `opprett_utsendingsliste` som committer i
        # mellomrommet er usynlig her. Å lukke det krever at BEGGE veier
        # tar SAMME lås, og ingen av dem kan det i dag:
        #   * signeringsveien kan ikke ta en radlås — PostgreSQL krever
        #     UPDATE-privilegium for ENHVER radlåsklausul, også
        #     `FOR SHARE` (019-grensen, sitert i 056 §7b som grunnen til
        #     at medlemskapslåsen går gjennom `laas_godkjenner()`), og
        #     runtime har kun SELECT på `utsendingsliste` (migrer.py).
        #     Ellers hadde `FOR UPDATE` på forelderraden holdt: self-FK-en
        #     gjør at barne-INSERT-en tar `FOR KEY SHARE` på nettopp den;
        #   * `opprett_utsendingsliste` bor i 056, som er hash-pinnet til
        #     akseptcommiten (`KJORT_056`) og ikke kan redigeres.
        # Låsen krever altså en NY migrasjon — ny maskin i en fiksrunde
        # (K1), samme dom som 056 selv felte over flerrads-sykelen.
        # REACHABILITY, ærlig: ingen produksjonsvei oppretter en
        # barnversjon ennå — rutene er lesing, en kodet blinding-
        # avvisning og denne, og seeden lager en ROT. Vinduet åpner seg
        # med redigeringsbenet, og #180 er den PR-en som må ta låsen.
        #
        # ...MEN EN FULLFØRT SIGNATUR KJENNES IGJEN FØRST (Codex P1, runde
        # 3). Portene under er TILSTANDSPORTER: de spør «kan denne raden
        # signeres nå?». For et replay er det spørsmålet allerede besvart —
        # signaturen STÅR. Tapte svaret veien hjem, ber
        # `ui.rekruttering.usikkert_utfall` brukeren prøve igjen med løftet
        # om at forsøket gjentar den SAMME operasjonen, og `api.js` sender
        # samme nøkkel. Ble serien redigert videre i mellomtiden, svarte
        # spissporten `liste_utdatert` (409) på en operasjon som var
        # ferdig: flaten leser 409 som et definitivt avslag, og brukeren
        # sitter igjen uten noen måte å vite om den irreversible
        # autorisasjonen gikk igjennom. Nøyaktig samme klasse som 056 selv
        # lukket i runde 12 på #140, bare ett lag lenger ut.
        #
        # ...MEN BARE SPISSPORTEN (Codex P2, runde 4). Runde 3 la
        # forbigangen på BEGGE portene, og det var ett ledd for mye:
        # spissporten spør om RADENS tilstand («er denne versjonen fortsatt
        # den serien peker på?»), og det spørsmålet er avlyst av at
        # signaturen står. Hashen spør om noe annet — den er
        # INNHOLDSBINDINGEN, kroppens påstand om HVA signataren leste — og
        # den påstanden er kallerens, ikke basens. Med forbigangen på
        # begge fikk et replay med samme nøkkel, liste og signatar, men en
        # ANNEN `innhold_hash`, 201: endepunktet bekreftet innhold kalleren
        # aldri hadde signert, og brøt samtidig SP-2-regelen om at en
        # gjenbrukt nøkkel med endret inndata er en konflikt.
        # `utsendingsliste` er append-only (056: `utsendingsliste_append_
        # only`), så radens hash kan ikke endre seg under en ekte
        # gjentakelse — den lovlige replayen bærer alltid samme hash og
        # merker ingenting til at porten er tilbake.
        #
        # ...OG MOTTAKERDATAENE MÅ FORTSATT FINNES (Codex P2, runde 4).
        # `reap_kandidatdata` (057) er timerens sletting: når slettefristen
        # løper ut settes `slettet_ts` på prosessen og kandidat- og
        # mottakerdataene tømmes. Listeraden i 056 overlever — den er
        # append-only — og oppslaget over spurte bare etter tenant og
        # liste_id. En flate eller en bekreftelsesdialog som sto åpen over
        # fristen kunne derfor signere en utsendelse hvis mottakere ikke
        # lenger finnes: 201 på en autorisasjon uten innhold, og seriens
        # ENE signatur-slot brent for godt. Prosessen er lesesvarets eget
        # filter (`slettet_ts IS NULL`), så flaten viser den aldri — dette
        # er nettopp vinduet mellom lesningen og klikket. LEFT JOIN, ikke
        # INNER: en 056-liste trenger ikke ha en 057-prosess bak seg, og en
        # liste uten prosess skal dømmes som før; `prosess_en_per_oppdrag`
        # (057) holder treffet på høyst én rad.
        #
        # ...MEN DEN PORTEN ER ULÅST — UTSATT TIL #180 (Codex P2, runde 5).
        # Joinen over dømmer en reaping som var FERDIG da setningen leste.
        # Starter `reap_kandidatdata` etterpå, låser den prosessraden,
        # tømmer lagrene og committer mens denne forespørselen fortsetter
        # ned i `signer_utsendingsliste` — og 201-et brenner seriens ene
        # slot på data som ikke lenger finnes. Det er NØYAKTIG samme klasse
        # som spissporten rett over: en ulåst lesning fulgt av en skriving,
        # med samme to grunner til at den ikke kan lukkes herfra (runtime
        # har ikke UPDATE-privilegiet enhver radlåsklausul krever, og
        # motparten bor i hash-pinnet SQL). En etterlesning ville krympet
        # vinduet uten å lukke det, og et halvt lukket kappløp som SER
        # lukket ut er verre enn et navngitt åpent. Codex sier det samme:
        # «signing and reaping need a shared database serialization
        # mechanism» — den maskinen er #180s, ikke denne rundens (K1/K2).
        # REACHABILITY: fristen er 90 døgn i seeden og settes per prosess;
        # vinduet er de millisekundene reaperen og et signeringsklikk må
        # treffe hverandre på, etter at fristen alt er løpt ut.
        #
        # ...OG GJENKJENNINGEN MÅLES FØRST BAK VENTEPUNKTET (Codex P1,
        # runde 8). De to lesningene over er tilstandsporter som spør «kan
        # denne raden signeres nå?», og runde 3 avlyste det spørsmålet for
        # et replay — men bare for et replay VI KUNNE SE. Originalen som
        # har SATT INN signaturen sin og ennå ikke committet, er usynlig
        # for `_fullfort_replay`: under READ COMMITTED finnes ikke en
        # ucommittet rad. Retryen leste altså `replay = False` på en
        # operasjon som var i ferd med å lykkes, og var serien redigert
        # videre i mellomtiden, svarte spissporten `liste_utdatert` (409)
        # rett foran låsen. Flaten leser 409 som et DEFINITIVT avslag på
        # en irreversibel autorisasjon som sekunder senere står i basen —
        # nøyaktig det idempotensløftet `ui.rekruttering.usikkert_utfall`
        # gir når den ber brukeren prøve igjen med samme nøkkel.
        #
        # Codex ber om «a shared synchronization point» for
        # gjenkjenningen og disse portene. Det punktet finnes ALLEREDE i
        # håndtereren, og er ingen ny maskin (K1): et replay er per
        # definisjon SAMME signatar — `_fullfort_replay` måler nettopp
        # `signatar = bid` — og `laas_godkjenner` tar `FOR UPDATE` på
        # nøyaktig den medlemskapsraden originalen holder til commit.
        # Låsen var alt her, bare ETTER portene; den flyttes foran dem, og
        # etteroppslaget som til nå bare vernet 403-armen blir det ENE
        # ferske svaret alle portene måles mot. Retryen stiller seg i køen
        # bak originalen, våkner til et snapshot der signaturen står, og
        # svarer det den svarte.
        #
        # 403-armen blir stående under portene, så feilrekkefølgen er
        # uendret: en foreldet eller reapet liste dømmes fortsatt som 409
        # før fullmakten måles. `laast` bæres bare over de tre linjene.
        #
        # ÉN FLIS BLIR IGJEN, og den er 056s: er medlemskapet gjort
        # INAKTIVT mellom originalens innsetting og retryen, finner låsen
        # ingen rad, det finnes intet ventepunkt her, og portene dømmer
        # ucommittet som før. Den dommen eier 056 — dens egen
        # medlemskapsport låser og bærer sitt eget etteroppslag for
        # replayet (§7b) — og veien dit går gjennom #180s felles
        # serialisering, ikke gjennom en fjerde arm her.
        replay = _fullfort_replay(conn, tenant, nokkel, liste_id, bid)
        laast = None
        if not replay:
            laast = conn.execute("SELECT roller FROM laas_godkjenner(%s,%s)",
                                 (tenant, bid)).fetchone()
            replay = _fullfort_replay(conn, tenant, nokkel, liste_id, bid)
        # ...OG `rad` SELV ER LEST FØR VENTEPUNKTET — UTSATT TIL #180
        # (Cursor P1, runde 10). Portene under måler `rad[3]`/`rad[4]` fra
        # SELECT-en over `_fullfort_replay`, altså fra FØR låsen. Runde 8
        # flyttet `laas_godkjenner` foran portene, og det gjorde vinduet
        # mellom lesningen og dommen sekunder langt i stedet for
        # mikrosekunder: køen på medlemskapsraden er nå en ventetid en
        # `opprett_utsendingsliste` eller en `reap_kandidatdata` kan
        # committe inni. Vinduet er altså UTVIDET av denne PR-en, og det
        # skal stå her — #180 arver det med åpne øyne.
        #
        # Det er likevel samme klasse som de to utsettelsene over, og
        # eiers forhåndsdom (#176-tråden, 24/8) felte nettopp den:
        # «reises replay-vs-porter-klassen en fjerde gang i denne
        # håndtereren, er svaret allerede felt — utsett til #180 ...
        # ulåste forhåndslesninger inkludert». To grunner bærer den her:
        #   * EN ETTERLESNING LUKKER IKKE. Etter et ferskt `SELECT` løper
        #     forespørselen fortsatt videre ned i `signer_utsendingsliste`
        #     uten felles lås på serien eller prosessen, så motparten kan
        #     committe der i stedet. Vinduet krymper til det det var før
        #     runde 8; det forsvinner ikke. Huset felte alt den formen på
        #     reap-porten over: et halvt lukket kappløp som SER lukket ut
        #     er verre enn et navngitt åpent.
        #   * OG BREDDEN ER IKKE DET SOM AVGJØR HER. Begge bitene vinduet
        #     kan snu mangler en produsent i drift — ingen rute oppretter
        #     barnversjoner (redigeringsbenet er #180-sperret), og reapen
        #     krever en prosess forbi slettefristen i signeringsøyeblikket
        #     (seeden setter 90 døgn). Det er samme reachability eiers
        #     merge-vedtak (K2-dommen 09:35Z) hviler på, og den endres
        #     ikke av at ventepunktet gjør vinduet lengre.
        # #180 tar låsen for hele klassen; til da er dette navngitt åpent.
        if not replay and rad[3]:
            raise _Avbrudd(_feil("liste_utdatert", rid, 409))
        if not replay and rad[4]:
            raise _Avbrudd(_feil("kandidatdata_slettet", rid, 409))
        if rad[0] != kropp["innhold_hash"]:
            raise _Avbrudd(_feil("innhold_endret", rid, 409))
        # FULLMAKTEN MÅLES PÅ NYTT UNDER LÅSEN (Codex P1, runde 3).
        # `_browserkontekst` måler scopet mot sesjonens `authz_snapshot` ved
        # inngangen til forespørselen. 056s port låser medlemskapet, men
        # spør bare om det er AKTIVT — den leser aldri `roller`, med vilje:
        # «rolle- og scope-nivået hører til flatens egen autorisasjon
        # (CP3)» (056 §7b). Ingen av de to målte altså rollen PÅ det
        # tidspunktet signaturen ble skrevet. Fratas en administrator
        # `bestilling:opprett` mellom autentiseringen og skrivingen, står
        # medlemskapet fortsatt `aktiv`, og en tilbakekalt fullmakt kunne
        # committe en irreversibel autorisasjon — i en append-only tabell
        # som aldri kan rettes.
        #
        # Målingen tas gjennom SAMME lås som 056 bruker: `laas_godkjenner`
        # (013) er SECURITY DEFINER og tar `FOR UPDATE` på medlemskapsraden
        # — den låsen 019-grensen sier runtime ikke kan ta selv — og den
        # RETURNERER de låste rollene, nettopp for denne sammenlikningen
        # (`_reautoriser_godkjennere` i policyadmin bruker den slik).
        # Låsen holdes ut transaksjonen, så 056s eget kall arver den, og en
        # tilbakekalling kan hverken snike seg inn foran eller etter: den
        # serialiseres bak oss.
        #
        # IKKE for et replay. En ferdig operasjon skal svare det den svarte
        # — 056 selv slipper replayet forbi medlemskapsporten av nøyaktig
        # den grunnen (runde 11/12 på #140), og en rolleendring etterpå
        # gjør ikke en signatur som STÅR om til et avslag.
        #
        # MEDLEMSKAPET dømmes ikke her. Finner låsen ingen aktiv rad, faller
        # kallet igjennom til 056s egen port — den EIER den dommen, og den
        # bærer sitt eget etteroppslag for replayet som ble ferdig mens vi
        # ventet. Her måles bare det 056 med vilje ikke måler: rollen.
        #
        # ...OG OPPSLAGET GJENTAS ETTER VENTINGEN, av samme grunn (056 §7b,
        # runde 12 på #140): låsen er et VENTEPUNKT. Et replay som gjorde
        # sitt tidlige oppslag mens originalen ennå var ucommittet, stiller
        # seg i køen; våkner det til en rolle som er trukket tilbake, ville
        # dommen vært 403 på en signatur som NÅ står. Under READ COMMITTED
        # får setningen et ferskt snapshot, så originalens rad er synlig.
        # Etteroppslaget står nå OVER portene (runde 8) og er ett og det
        # samme for alle tre: `replay` bærer det ferske svaret hit, og
        # `laast` de låste rollene. At de to leddene er samme lesning er
        # selve poenget — gjenkjenningen og tilstandsportene skal ikke
        # kunne dømme fra hvert sitt tidspunkt.
        if not replay and laast is not None and _SIGNERINGSSCOPE not in \
                scopes_for_roller(list(laast[0] or ())):
            tjeneste.logg.hendelse("signatar_avvist", rid)
            raise _Avbrudd(_feil("signatar_uten_fullmakt", rid, 403))
        try:
            conn.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
                         (tenant, liste_id, bid, nokkel))
        except psycopg.errors.InsufficientPrivilege as e:
            tjeneste.logg.hendelse("signatar_avvist", rid)
            raise _Avbrudd(_feil("signatar_uten_medlemskap", rid, 403)) \
                from e
        except psycopg.errors.UniqueViolation as e:
            # HVILKET unik-krav SOM BRAST ER TO ULIKE DOMMER (Codex P2,
            # runde 6). Armen her dømte alle 23505 fra 056 som «serien er
            # alt signert» — men `utsendingssignatur` bærer flere unike
            # krav, og ett av dem er SP-2-nøkkelen: `UNIQUE (tenant,
            # operasjonsnokkel)`.
            #
            # SEKVENSIELT nås det aldri: gjenbrukes nøkkelen på en annen
            # liste, finner funksjonens eget nøkkel-oppslag den forrige
            # raden og reiser `invalid_parameter_value` (armen under).
            # SAMTIDIG kan det: to ULIKE signatarer låser hver sin
            # medlemskapsrad, så `laas_godkjenner` serialiserer dem ikke
            # mot hverandre, og begge passerer nøkkel-oppslaget mens den
            # andres rad ennå er ucommittet. Taperen blokkerer på unik-
            # indeksen, får 23505 når vinneren committer, og 056s egen
            # exception-arm gjenleser: raden bærer en ANNEN liste, altså
            # ikke et replay, og bruddet reises videre — som det skal.
            # Da er dommen her feil: taperens serie er URØRT og fullt
            # signerbar; det som kolliderte var `Idempotency-Key`-en.
            # `serien_alt_signert` sier «denne utsendelsen er alt
            # autorisert» — kalleren slutter å prøve på en signatur som
            # aldri ble skrevet. Kanonisk svar er `idempotenskonflikt`:
            # samme dom som den sekvensielle halvdelen, «nøkkelen din
            # betyr noe annet — prøv igjen med en fersk».
            #
            # Skillet leses maskinelt av `diag.constraint_name` — samme
            # form som `policyadmin.py` alt bruker for `policyer_pkey`,
            # og av samme grunn: feilteksten er ikke et grensesnitt.
            # Ukjent/uten navn beholder den gamle dommen: PK-en og
            # serie-indeksen er de andre kildene til 23505 her, og begge
            # ER «alt signert».
            if e.diag.constraint_name == _NOKKELBRUDD:
                raise _Avbrudd(_feil("idempotenskonflikt", rid, 409)) from e
            raise _Avbrudd(_feil("serien_alt_signert", rid, 409)) from e
        except psycopg.errors.InvalidParameterValue as e:
            # SP-2-KONFLIKTEN ER EN DOM, IKKE EN 500 (Codex P2). Gjenbrukes
            # en `Idempotency-Key` på en ANNEN liste eller signatar, reiser
            # `signer_utsendingsliste` `invalid_parameter_value` (056 §7b) —
            # den ENESTE kilden til den koden i funksjonen (`krev_
            # tenantkontekst` reiser `insufficient_privilege`, og
            # isolasjonsporten `invalid_transaction_state`). Uoversatt
            # escaper den `_med_conn`, som bare kjenner `_Avbrudd` og
            # `Aktiveringsfeil`, og blir en 500: klienten ser en serverfeil
            # der plattformens kanoniske svar er 409 `idempotenskonflikt`,
            # og kan ikke skille «nøkkelen din betyr noe annet» fra «vi er
            # nede» — den ene skal rettes av kalleren, den andre prøves om
            # igjen.
            raise _Avbrudd(_feil("idempotenskonflikt", rid, 409)) from e
        conn.commit()
        # 201 BETYR AUTORISERT, IKKE SENDT (Codex P1). Det eneste som har
        # skjedd, er at signaturraden står. Frigivelsen er en egen kjede —
        # `frigi_utsendelse` per mottaker (056 §7c) og en jobb som sender —
        # og den har ingen produksjonskaller i dag: den konsumerende benen
        # er #151. Flaten lovet «Signer og send … Dette sender N e-poster»,
        # og det løftet er trukket tilbake der det ble gitt (locales), ikke
        # innfridd med ny maskin i en fiksrunde (K1). Kommer senderbenet,
        # er det HER kvitteringsveien kobles på.
        return _ok({"innhold_hash": rad[0], "antall": rad[1],
                    "listetype": rad[2]}, rid, 201)

    return _med_conn(tjeneste, rid, kjor)


def blinding_endepunkt(tjeneste, request):
    """POST /v1/rekruttering/prosesser/{id}/blinding — se modul-docstring:
    KODET avvisning til #159s evidensdesign er landet. Autentiserer og
    CSRF-verner likevel, så avvisningen aldri blir en anonym probe."""
    from .app import _rid
    from .policyadmin_http import _browserkontekst, _feil, _med_conn
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av

    def kjor(conn):
        _browserkontekst(tjeneste, request, conn, rid, "bestilling:opprett")
        return _feil("blinding_avskruing_krever_159", rid, 409)

    return _med_conn(tjeneste, rid, kjor)


def evaluering_slett_endepunkt(tjeneste, request):
    """POST /v1/rekruttering/evaluering/{oppdrag_id}/slett (069).

    Bestiller TIDLIGSLETTING av evalueringens kandidatdata: døren
    `bestill_tidligsletting` setter det enveise merket (og lukker en
    ulukket prosess så fristen løper fra avslutningen), og reaperen
    fullbyrder i første sveip — seks lagre + makulert rapportartefakt,
    med alle portene reapingen alt har. Idempotent: knappen kan trykkes
    to ganger. Fristen og lukkingen er fortsatt immutable (port 20) —
    dette er kundens egen KORTING av fristen, paragraf 5s ene lovlige
    retning.
    """
    from .app import _rid
    from .policyadmin_http import _browserkontekst, _feil, _med_conn, _ok
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av
    try:
        oppdrag_id = int(request.path_params["oppdrag_id"])
    except (KeyError, ValueError):
        return _feil("request_feilformet", rid, 400)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        rad = conn.execute(
            "SELECT prosess_id FROM rekrutteringsprosess"
            " WHERE tenant=%s AND oppdrag_id=%s",
            (tenant, oppdrag_id)).fetchone()
        if rad is None:
            # En evaluering uten retensjonsanker har ingen kandidatdata
            # aa slette — samme ord som leseveien bruker om fravaeret.
            return _feil("ikke_funnet", rid, 404)
        conn.execute("SELECT bestill_tidligsletting(%s,%s)",
                     (tenant, rad[0]))
        conn.commit()
        tjeneste.logg.hendelse("tidligsletting_bestilt", rid, tenant,
                               art="drift", oppdrag_id=oppdrag_id)
        return _ok({"slett_bestilt": True}, rid)

    return _med_conn(tjeneste, rid, kjor)


def evaluering_avbryt_endepunkt(tjeneste, request):
    """POST /v1/rekruttering/evaluering/{oppdrag_id}/avbryt.

    Kansellerer en evaluering som ennaa ikke er ferdig. Statusmaskinen
    (056) eier lovligheten: opprettet -> kansellert direkte, og et
    PLUKKET oppdrag gaar veien plukket -> opprettet -> kansellert i
    SAMME transaksjon — begge stegene er maskinens egne overganger, og
    en samtidig kvittering taper paa radlaasen i stedet for aa flette.
    Utfoereren som mister oppdraget midt i arbeidet stoppes av portene
    som alt finnes (lease/fencing/kvittering mot terminal status);
    kompensasjonen er den samme som ved lease-tap — arbeid uten
    leveranse, aldri feil leveranse. Et aapent retensjonsanker lukkes i
    samme transaksjon (frist fra avslutningen, sak 222-formen).
    """
    from .app import _rid
    from .policyadmin_http import _browserkontekst, _feil, _med_conn, _ok
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av
    try:
        oppdrag_id = int(request.path_params["oppdrag_id"])
    except (KeyError, ValueError):
        return _feil("request_feilformet", rid, 400)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        rad = conn.execute(
            "SELECT status FROM oppdrag"
            " WHERE tenant=%s AND id=%s"
            "   AND oppdragstype='rekruttering.evaluering'"
            " FOR UPDATE", (tenant, oppdrag_id)).fetchone()
        if rad is None:
            return _feil("ikke_funnet", rid, 404)
        status = rad[0]
        if status in ("utfort", "feilet", "kansellert"):
            conn.rollback()
            tjeneste.logg.hendelse("evaluering_terminal", rid, tenant,
                                   art="drift", oppdrag_id=oppdrag_id)
            return _feil("evaluering_terminal", rid, 409)
        if status == "plukket":
            # Maskinens egen vei: tilbake i koe, saa kansellert — to
            # lovlige overganger i samme transaksjon, aldri en ny arm i
            # kolonnelaasen.
            conn.execute(
                "UPDATE oppdrag SET status='opprettet'"
                " WHERE tenant=%s AND id=%s", (tenant, oppdrag_id))
        conn.execute(
            "UPDATE oppdrag SET status='kansellert'"
            " WHERE tenant=%s AND id=%s", (tenant, oppdrag_id))
        p = conn.execute(
            "SELECT prosess_id FROM rekrutteringsprosess"
            " WHERE tenant=%s AND oppdrag_id=%s AND lukket_ts IS NULL",
            (tenant, oppdrag_id)).fetchone()
        if p is not None:
            conn.execute("SELECT lukk_rekrutteringsprosess(%s,%s, now())",
                         (tenant, p[0]))
        conn.commit()
        tjeneste.logg.hendelse("evaluering_avbrutt", rid, tenant,
                               art="drift", oppdrag_id=oppdrag_id)
        return _ok({"avbrutt": True}, rid)

    return _med_conn(tjeneste, rid, kjor)


def stillingsprofiler_endepunkt(tjeneste, request):
    """GET /v1/rekruttering/stillingsprofiler — kundens kravlister (#189).

    Leseflaten viser SISTE versjon av hver profil, med hele kravsettet
    (navn + vekt) i lagret rekkefølge. Historikken er append-only i
    basen (061); eldre versjoner er oppslagbare via `?alle=1` den dagen
    flaten trenger dem — inntil da holdes svaret på det editoren viser.
    """
    from .app import _rid
    from .policyadmin_http import _med_conn, _ok
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av

    def kjor(conn):
        tenant, _bid = _leseauth_beslutninger(tjeneste, request, conn, rid)
        profiler = []
        for pid, versjon, navn, opprettet, av_ in conn.execute(
                "SELECT s.profil_id, s.versjon, s.navn, s.opprettet,"
                "       s.opprettet_av"
                "  FROM stillingsprofil s"
                " WHERE s.tenant=%s"
                "   AND s.versjon = (SELECT max(i.versjon)"
                "                      FROM stillingsprofil i"
                "                     WHERE i.tenant=s.tenant"
                "                       AND i.profil_id=s.profil_id)"
                " ORDER BY s.opprettet DESC", (tenant,)).fetchall():
            krav = [{"kravnavn": kn, "vekt": v}
                    for kn, v in conn.execute(
                        "SELECT kravnavn, vekt FROM stillingsprofil_krav"
                        " WHERE tenant=%s AND profil_id=%s AND versjon=%s"
                        " ORDER BY rekkefolge",
                        (tenant, pid, versjon)).fetchall()]
            profiler.append({
                "profil_id": str(pid), "versjon": versjon, "navn": navn,
                "opprettet": opprettet.isoformat(),
                "opprettet_av": av_, "krav": krav,
            })
        return _ok({"profiler": profiler}, rid)

    return _med_conn(tjeneste, rid, kjor)


def stillingsprofil_lagre_endepunkt(tjeneste, request):
    """POST /v1/rekruttering/stillingsprofiler — ny profil ELLER ny
    versjon (#189).

    Kroppen: `{"navn": ..., "krav": [{"kravnavn": ..., "vekt": 0-10},
    ...]}` + valgfri `"profil_id"` for å versjonere en eksisterende.
    Redigering er ALDRI en mutasjon: døren (061) skriver en ny,
    komplett versjon atomisk — en kjørt evaluering peker på profilen
    slik den var. Valideringen bor i døren og CHECKene; her mappes bare
    feilkontrakten (invalid_parameter_value/CheckViolation/
    UniqueViolation → 400 med sanert melding).
    """
    import uuid as uuidlib

    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       _SIGNERINGSSCOPE)
        # Opprettelsen er ikke naturlig idempotent (CodeRabbit major):
        # et tapt 201 + retry ville laget en NY profil/versjon. Nøkkelen
        # er PÅKREVD, og døren gjenspiller samme svar på samme nøkkel.
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        profil_id = kropp.get("profil_id")
        if profil_id is not None:
            # Formfeil er 400 her, ikke en psycopg-feil i døren.
            try:
                profil_id = uuidlib.UUID(str(profil_id))
            except ValueError:
                raise _Avbrudd(_feil("request_feilformet", rid))
        navn, krav = kropp.get("navn"), kropp.get("krav")
        if not isinstance(navn, str) or not isinstance(krav, list):
            raise _Avbrudd(_feil("request_feilformet", rid))
        try:
            rad = conn.execute(
                "SELECT ut_profil_id, ut_versjon FROM"
                " opprett_stillingsprofil_versjon(%s,%s,%s,%s,"
                "%s::jsonb,%s)",
                (tenant, profil_id, navn, bid,
                 json.dumps(krav), nokkel)).fetchone()
        except psycopg.errors.InvalidParameterValue as e:
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.errors.CheckViolation as e:
            # Range-/lengdebrudd (vekt utenfor 0–10, tomt/for langt
            # navn) håndheves av 061-CHECKene — samme 400-kontrakt.
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.errors.UniqueViolation as e:
            # TRE betydninger deler SQLSTATE 23505 (Cursor P1-3, samme
            # klasse som signeringens _NOKKELBRUDD): (a) kappløp på
            # idempotensnøkkelen — taperen SKAL få vinnerens svar, så
            # døren kjøres én gang til og treffer replay-armen; (b)
            # nøkkelen brukt for ANNET innhold (dørens egen RAISE, uten
            # constraint-navn) → 409 idempotenskonflikt; (c) duplikat
            # kravnavn i settet → 400.
            if getattr(e.diag, "constraint_name", None) ==                     "stillingsprofil_idem":
                # Rollbacken tok SET LOCAL-konteksten — settes på nytt
                # før dørens andre kjøring.
                conn.rollback()
                from db.pg import sett_kontekst
                sett_kontekst(conn, tenant, bid, rid)
                try:
                    rad = conn.execute(
                        "SELECT ut_profil_id, ut_versjon FROM"
                        " opprett_stillingsprofil_versjon(%s,%s,%s,%s,"
                        "%s::jsonb,%s)",
                        (tenant, profil_id, navn, bid,
                         json.dumps(krav), nokkel)).fetchone()
                except psycopg.errors.UniqueViolation as e2:
                    raise _Avbrudd(_feil("idempotenskonflikt", rid))                         from e2
            elif getattr(e.diag, "constraint_name", None) is None:
                raise _Avbrudd(_feil("idempotenskonflikt", rid)) from e
            else:
                raise _Avbrudd(_feil("request_feilformet", rid)) from e
        conn.commit()
        return _ok({"profil_id": str(rad[0]), "versjon": rad[1]},
                   rid, 201)

    return _med_conn(tjeneste, rid, kjor)
