"""M-57s leseflate + signeringsvei (utførelsesarmen, første ben).

Tre ruter, formet av flatens kontrakt (flater/rekruttering.js):

* GET  /v1/rekruttering/prosesser — prosessene med kandidater, vekter og
  innstilte lister, lest RETT fra 057-lagrene under RLS. `decisions:read`
  (flatens svakeste ledd, WCAG-flate-formen).
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
    """
    from .policyadmin_http import _feil
    if REKRUTTERINGSMODUL not in tjeneste.inaktive_moduler:
        return None
    tjeneste.logg.hendelse("modul_inaktiv", rid, art="drift",
                           modul=REKRUTTERINGSMODUL)
    return _feil("modul_inaktiv", rid)


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


def _kandidater(conn, tenant, prosess_id):
    """Kandidatene i én prosess, lest RETT fra 057-lageret under RLS.

    SVARET ER IKKE AVGRENSET — UTSATT TIL #183 (Codex P2, K1). Kalleren
    løper over hver ureapet prosess, og hver prosess kan bære 5000
    kandidater (katalogens harde løfte) i inntil 365 døgn. Å binde det
    krever at endepunktet henter den VALGTE prosessen i stedet for alle,
    og det er ny kontrakt på lesesvaret pluss ny hentelogikk i flaten —
    ny maskin i en fiksrunde. Rotårsak, foreslått maskin og målingen som
    skal drepe den står i #183.

    Det som KAN gjøres uten en ny kontrakt, gjøres her: lesningen slutter
    å hente det den kaster. `kildetekst` er hele den blindede
    søknadsteksten, `avmaskering` er tokenkartet — ingen av dem leses av
    noen linje under, og de er den desidert tyngste delen av artefaktet.
    De ble likevel dratt ut av basen, over forbindelsen og inn i minnet,
    for hver kandidat i hver prosess. Nøkkelsubtraksjon (`jsonb - text`)
    og ikke en positiv projeksjon: da overlever ethvert felt en fremtidig
    produsent legger til, `status` inkludert.

    INTERVJUSPØRSMÅLENE HAR SITT EGET LAGER (Codex P2, runde 5). 057
    modellerer dem som `kandidat_intervjusporsmal` — egen tabell, egen
    `innhold_sha256`, egen rad i reaperens sletteliste. Lesningen tok dem
    likevel fra en KOPI inne i evalueringsartefakten, og en kopi og et
    lager kan si to ting: PR-ens egen demo-seed skrev to ULIKE
    spørsmålslister til de to stedene, og flaten viste artefaktkopien. En
    normalisert produsent — den 057 beskriver, som skriver lageret og
    dropper duplikatet i artefaktet — ville gitt flaten INGEN spørsmål.
    Lageret er kilden; `intervjusporsmal` subtraheres derfor ut av
    artefaktet sammen med det andre denne lesningen ikke leser.

    LEFT JOIN, ikke INNER: en kandidat kan ha artefakt uten at spørsmål
    er skrevet ennå (lagrene fylles inkrementelt mens kjøringen står på),
    og hun skal fortsatt vises — med tom liste, som er det sanne svaret.
    `slettet_ts IS NULL` i join-leddet, ikke i WHERE: et reapet
    spørsmålslager bærer `sporsmal = NULL` og skal ikke fjerne kandidaten
    fra svaret. Primærnøkkelen (tenant, prosess_id, kandidat_id) holder
    treffet på høyst én rad.
    """
    rader = conn.execute(
        "SELECT a.kandidat_id,"
        "       a.artefakt - 'kildetekst' - 'avmaskering'"
        "                 - 'intervjusporsmal',"
        "       i.sporsmal"
        "  FROM kandidat_evalueringsartefakt a"
        "  LEFT JOIN kandidat_intervjusporsmal i"
        "    ON i.tenant = a.tenant AND i.prosess_id = a.prosess_id"
        "   AND i.kandidat_id = a.kandidat_id AND i.slettet_ts IS NULL"
        " WHERE a.tenant=%s AND a.prosess_id=%s AND a.slettet_ts IS NULL"
        " ORDER BY a.kandidat_id", (tenant, prosess_id)).fetchall()
    kandidater, vekter, kilde = [], None, "standard"
    for kid, artefakt, sporsmal in rader:
        art = artefakt or {}
        if vekter is None and isinstance(art.get("vekter"), dict):
            vekter, kilde = art["vekter"], "evalueringsartefakt"
        funn = art.get("funn") or []
        oppfylt = art.get("oppfylt") or {}
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
        status = ("innstilt_avslag" if any(
                      f.get("kategori") == "krav_ikke_dokumentert"
                      for f in funn)
                  else "anbefalt" if not funn and oppfylt
                  and all(oppfylt.values()) else "vurderes")
        kandidater.append({
            "kandidat_id": str(kid),
            "oppfylt": oppfylt,
            "status": status,
            "funn": funn,
            "intervjusporsmal": sporsmal or [],
        })
    if vekter is None:
        krav = sorted({k for kand in kandidater for k in kand["oppfylt"]})
        vekter = {k: 3 for k in krav}
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
    from .policyadmin_http import _med_conn, _ok
    rid = _rid(request)
    av = _modul_inaktiv(tjeneste, rid)
    if av is not None:
        return av

    def kjor(conn):
        tenant, _bid = _leseauth_beslutninger(tjeneste, request, conn, rid)
        prosesser = []
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
        for pid, oppdrag_id, status, opprettet in conn.execute(
                "SELECT p.prosess_id, p.oppdrag_id, o.status, p.opprettet"
                "  FROM rekrutteringsprosess p"
                "  JOIN oppdrag o ON o.tenant = p.tenant"
                "                AND o.id = p.oppdrag_id"
                " WHERE p.tenant=%s AND p.slettet_ts IS NULL"
                " ORDER BY p.opprettet DESC", (tenant,)).fetchall():
            kandidater, vekter, kilde = _kandidater(conn, tenant, pid)
            prosesser.append({
                "prosess_id": str(pid),
                "opprettet": opprettet.isoformat(),
                "blinding_av": False,   # avskruing finnes ikke før #159
                "evaluering_status": status,
                "vekter": vekter,
                "vekter_kilde": kilde,
                "kandidater": kandidater,
                "lister": _lister(conn, tenant, oppdrag_id),
            })
        return _ok({"prosesser": prosesser}, rid)

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
        rad = conn.execute(
            "SELECT l.innhold_hash, l.antall, l.listetype,"
            "       EXISTS (SELECT 1 FROM utsendingsliste b"
            "                WHERE b.tenant=l.tenant"
            "                  AND b.utkast_serie=l.utkast_serie"
            "                  AND b.forrige_liste_id=l.liste_id),"
            "       (p.slettet_ts IS NOT NULL) AS reapet"
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
        replay = _fullfort_replay(conn, tenant, nokkel, liste_id, bid)
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
        if not replay:
            laast = conn.execute("SELECT roller FROM laas_godkjenner(%s,%s)",
                                 (tenant, bid)).fetchone()
            if laast is not None and _SIGNERINGSSCOPE not in \
                    scopes_for_roller(list(laast[0] or ())) \
                    and not _fullfort_replay(conn, tenant, nokkel,
                                             liste_id, bid):
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
