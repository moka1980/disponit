"""M-13 bankavstemmingsagentens API (migrasjon 101).

Seks endepunkter: én leseflate og fem skriveveier. Ingen av dem rører en
tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_avstemming_eier`-eid SECURITY DEFINER-dør i 101, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7, 090/091/096/100-formen). At
en bankpost er avstemt høyst én gang, at fortegnet må svare til bilagets
retning, at overdekning avvises og at en manuell match koster en
begrunnelse, er derfor egenskaper ved BASEN og ikke ved denne filen — og
en flate som sjekket det selv ville vært en andre sannhet å omgå.

MODULEN BOKFØRER IKKE. Det er v1-dommen, og den er en EGENSKAP VED DENNE
FILEN og ikke bare en intensjon: her finnes ingen HTTP-klient, ingen
import av `ssrf`/`httpx`/`urllib`, ingen hovedbokskobling og ingen
utgående vei. En automatisk bokføring er en skriving i regnskapet, og et
regnskap som endres av noe ingen leste er ikke et regnskap. Porten
`postering_utenfor_registeret` i `test_m13_avstemming.py` måler fraværet
statisk (AST) og funksjonelt.

BELØP ER HELTALL HELE VEIEN. Kroppen tar `belop_ore` som `int`, aldri et
desimaltall, og et flyttall avvises med 400 — ikke rundes. `2.5` øre er
ikke et beløp, det er en enhet noen har misforstått, og en flate som
rundet ville gjort misforståelsen til et tall i et regnskap.

SCOPENE.

  * LESINGEN bærer `okonomi:read`, og det er ET NYTT SCOPE. Det er ikke
    en vane som fikk løpe — de to kandidatene passet ikke:
    `decisions:read` holdes av `leser`, altså enhver ordinær bruker, og
    kontobevegelser, motparter og beløp er ikke allmenn tilstandsinnsikt.
    `security:read` beskrives i `autorisasjon.py` med ordene
    «Compliance/ops», og et avstemmingsregister er økonomi og ikke drift;
    å låne det scopet ville gjort beskrivelsen usann for alle de andre
    flatene som bruker det. Kretsen er `admin` alene i v1 — en tenant som
    vil skille regnskapsfører fra administrator kan definere en snevrere
    rolle senere, uten skjemaendring. M-23 (104) og M-24 (105) GJENBRUKER
    dette scopet; det oppstår her fordi M-13 kommer først.
  * SKRIVINGEN bærer `bestilling:opprett` — scopet `admin` allerede har,
    og som allerede står i `BROWSER_MUTASJONSSCOPES`. Samme presedens som
    M-21 (096) og M-34 (100).

SP-2 PÅ ALLE FIRE SKRIVEVEIENE SOM FØDER EN RAD (m35/096/100-formen):
`konto_id`, `post_id`, `bilag_id` og `avstemming_id` utledes
deterministisk av Idempotency-Key-en, så en tapt respons + nytt klikk
GJENSPILLER i stedet for å føde en rad til. En dobbelt registrert
innbetaling er nøyaktig den feilen som får et regnskap til å stemme på
papiret og ikke i virkeligheten.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

#: Takene for hvor mange rader leseflaten viser. Dørenes tak er 1000;
#: dette er flatens, og begrunnelsen er en annen: et register med tusen
#: uavstemte poster skal ikke kunne gjøre ett HTTP-svar til en
#: nedlasting. SAMMENDRAGET TELLER LIKEVEL ALT — det er hele grunnen til
#: at `m13_avstemmingsstatus` er en egen dør.
MAKS_POSTER = 200
MAKS_BILAG = 200

#: Lengdegrensene på kundens egen tekst.
MAKS_NAVN = 200
MAKS_KONTONUMMER = 64
MAKS_EKSTERN_REF = 200
MAKS_TEKST = 1000
MAKS_MOTPART = 300
MAKS_BILAGSNUMMER = 100
MAKS_BEGRUNNELSE = 2000

#: Ytterpunktet for et beløp i øre. 10^13 øre er hundre milliarder kroner
#: — godt over enhver reell bevegelse, og godt under `BIGINT`s tak, slik
#: at summeringen i dørene ikke kan renne over uansett hvor mange rader
#: som legges sammen.
MAKS_ORE = 10 ** 13

#: SPEIL av CHECK-ene i 101. Speilene finnes for at feilen skal bli 400 og
#: ikke 409: en ukjent verdi er en feilformet forespørsel, ikke en
#: tilstand som sier nei. Dørenes CHECK er fortsatt den bindende.
RETNINGER = ("inn", "ut")
METODER = ("automatisk", "manuell")

#: SP-2-navnerommene. Fire arter under ett modulnavnerom: den samme
#: Idempotency-Key-en kan i prinsippet brukes mot to ULIKE endepunkter, og
#: da skal den ikke gi samme UUID i to tabeller.
_M13_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m13:avstemming")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M13_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valgfri_tekst(kropp, felt: str, rid, maks: int) -> str | None:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    if not isinstance(verdi, str) or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi.strip() or None


def _ore(kropp, felt: str, rid, *, tillat_negativ: bool) -> int:
    """Beløpet, og DEN ENESTE veien det kommer inn.

    `isinstance(x, bool)` er ikke pedanteri: i Python er `True` en `int`,
    og uten sjekken ville `{"belop_ore": true}` blitt beløpet 1 øre. Et
    flyttall avvises og rundes ALDRI — `2.5` øre er ikke et beløp, og en
    flate som rundet ville gjort misforståelsen til et tall i et regnskap.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if abs(verdi) >= MAKS_ORE:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if verdi == 0 or (verdi < 0 and not tillat_negativ):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _uuid_felt(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, TypeError, AttributeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


#: De ENESTE feilklassene dørene bruker som DOM (089/096/100s liste).
#: En tapt forbindelse eller en manglende rettighet på selve funksjonen er
#: ikke «tilstanden nekter».
_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
    psycopg.errors.InsufficientPrivilege,
)


def _doerfeil(e, rid):
    """Dørens ERRCODE → flatens feilkode. ÉN kilde, så alle skriveveiene
    svarer likt på samme dom. `None` = ikke en dom; kalleren kaster
    originalen videre."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        # Kontoen, posten eller bilaget finnes ikke. Det er ikke en
        # feilformet kropp — id-en er velformet — men en henvisning til
        # noe som ikke er der.
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: feil fortegn, overdekning, for kort
        # kontonummer, tom bankreferanse, beløp på null. Kroppen ER
        # velformet — det er innholdskravet basen håndhever som sier nei.
        return _Avbrudd(_feil("avstemming_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        return _Avbrudd(_feil("avstemming_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Hele leseflatens tilstand i én transaksjon, gjennom tre lesedører.

    SAMMENDRAGET KOMMER FRA SIN EGEN DØR og telles over ALT — ikke fra de
    to avkortede listene. En flate som regnet totalen fra listen ville
    sagt «tre uavstemte poster» når det var tre hundre, og tallet ville
    vært mest galt nettopp den dagen det betydde mest.

    Rekkefølgen er DØRENES (eldst først, forfalte først) — flaten sorterer
    ikke om. `alder_dogn` og `dogn_over_forfall` er regnet i BASEN, i
    samme skann som raden, nettopp for at flaten ikke skal trekke to
    datoer fra hverandre (M-16-regelen).
    """
    s = conn.execute("SELECT * FROM m13_avstemmingsstatus(%s)",
                     (tenant,)).fetchone()
    # KONTOENE KOMMER FRA SIN EGEN DØR og ikke fra postlisten. En
    # nyopprettet konto har ingen poster ennå, så en flate som utledet
    # listen fra postene ville hatt en tom nedtrekk nøyaktig den gangen
    # brukeren skulle registrere sin FØRSTE bankpost. Kontonummeret er
    # ikke med — det finnes ikke i basen, bare halen.
    kontoer = [
        {"konto_id": str(r[0]), "navn": r[1], "kontonummer_hale": r[2],
         "valuta": r[3], "aktiv": r[4], "poster": r[5]}
        for r in conn.execute("SELECT * FROM m13_kontoer(%s)",
                              (tenant,)).fetchall()]
    poster = [
        {"post_id": str(r[0]), "konto_navn": r[1], "konto_hale": r[2],
         "valuta": r[3], "ekstern_ref": r[4],
         "bokfort": r[5].isoformat(), "belop_ore": r[6], "tekst": r[7],
         "motpart": r[8], "alder_dogn": r[9],
         "apne_funn": list(r[10] or ())}
        for r in conn.execute(
            "SELECT * FROM m13_uavstemte_poster(%s,%s)",
            (tenant, MAKS_POSTER)).fetchall()]
    bilag = [
        {"bilag_id": str(r[0]), "bilagsnummer": r[1], "retning": r[2],
         "belop_ore": r[3], "dekket_ore": r[4], "rest_ore": r[5],
         "motpart": r[6], "utstedt": r[7].isoformat(),
         "forfall": r[8].isoformat() if r[8] is not None else None,
         "dogn_over_forfall": r[9], "apne_funn": list(r[10] or ())}
        for r in conn.execute(
            "SELECT * FROM m13_apne_bilag(%s,%s)",
            (tenant, MAKS_BILAG)).fetchall()]
    return {
        "sammendrag": {
            "poster_totalt": s[0], "poster_uavstemt": s[1],
            "uavstemt_ore": s[2], "bilag_apne": s[3], "rest_ore": s[4],
            "apne_funn": s[5],
            # LISTENE ER AVKORTET, OG FLATEN SKAL KUNNE SI DET. Uten
            # disse to måtte den sammenlignet `len(poster)` med
            # `poster_uavstemt` og gjettet — og en flate som gjetter på
            # om den viser alt, sier «alt» når den ikke gjør det.
            "poster_vist": len(poster), "bilag_vist": len(bilag)},
        "kontoer": kontoer, "poster": poster, "bilag": bilag}


def avstemmingsbilde(tjeneste, request):
    """GET /v1/avstemming (okonomi:read) — tenantens eget
    avstemmingsregister."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen alle fem skriveveiene deler: browserkontekst, idempotens,
    kropp, ETT dørkall, commit. Én kopi, fordi fem nesten like kopier er
    fire steder å glemme `_doerfeil`."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        sql, args, svar, *rest = bygg(tenant, bid, nokkel, kropp, rid,
                                      request)
        # `idfelt` (valgfritt fjerde ledd) navngir feltet dørens ANDRE
        # kolonne skal fylle. Bare `m13_registrer_post` bruker det: den
        # har to idempotenser (kallerens nøkkel OG bankens referanse), så
        # den lagrede raden kan være en ANNEN enn den kalleren utledet.
        # Uten dette ville flaten fått en id ingen rad har.
        idfelt = rest[0] if rest else None
        try:
            rad = conn.execute(sql, args).fetchone()
            ny = rad[0]
            if idfelt is not None:
                svar = {**svar, idfelt: str(rad[1])}
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            # Datoen er kallerens tekst, og castet skjer i basen. En
            # ulesbar eller umulig dato er 400, ikke 409: det er KROPPEN
            # som er feil, ikke tilstanden. Fanget som to navngitte
            # klasser og ikke som `DataError`, fordi `DataError` også
            # dekker 22023 — dørenes egen dom, som skal bli 409.
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        # `ny=false` er et STILLE JA (SP-2): samme nøkkel og samme innhold
        # ga samme rad. Kalleren får den samme id-en, og ingenting ble
        # skrevet to ganger.
        return _ok({**svar, "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def konto_endepunkt(tjeneste, request):
    """POST /v1/avstemming/konto (bestilling:opprett, idem).

    KONTONUMMERET SENDES HELT INN OG LAGRES ALDRI HELT. Døren tar imot
    det, beholder de fire siste sifrene og en sha256 av det normaliserte
    hele nummeret, og glemmer originalen. Registeret trenger å kunne SI
    hvilken konto en post hører til og å kjenne igjen den samme kontoen
    på nytt — ingen av delene krever hele nummeret, og å lagre det man
    ikke trenger er hvordan et register blir et brudd.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        nummer = _tekst(kropp, "kontonummer", rid, MAKS_KONTONUMMER)
        valuta = _tekst(kropp, "valuta", rid, 8)
        kid = _utled("konto", tenant, nokkel)
        return ("SELECT m13_registrer_konto(%s,%s,%s,%s,%s,%s)",
                (tenant, kid, navn, nummer, valuta, bid),
                {"konto_id": str(kid)})
    return _skriv(tjeneste, request, bygg)


def bankpost_endepunkt(tjeneste, request):
    """POST /v1/avstemming/bankpost (bestilling:opprett, idem).

    FORTEGNET BÆRER RETNINGEN: positivt beløp = inn på konto, negativt =
    ut. Derfor er dette den ene skriveveien der et negativt tall er
    lovlig, og `_ore` får `tillat_negativ=True` her og ingen andre
    steder.

    DEN VIRKELIGE IDEMPOTENSEN ER `ekstern_ref`, ikke Idempotency-Key-en.
    Nøkkelen beskytter mot dobbeltklikk i flaten; bankens egen referanse
    beskytter mot den samme kontoutskriften lastet inn to ganger — og det
    siste er det som faktisk skjer. Dukker referansen opp med et ANNET
    beløp, sier døren nei: kilden motsier seg selv, og registeret velger
    ikke.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        konto = _uuid_felt(kropp, "konto_id", rid)
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_EKSTERN_REF)
        bokfort = _tekst(kropp, "bokfort", rid, 32)
        belop = _ore(kropp, "belop_ore", rid, tillat_negativ=True)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        motpart = _valgfri_tekst(kropp, "motpart", rid, MAKS_MOTPART)
        pid = _utled("bankpost", tenant, nokkel)
        # `SELECT * FROM`, ikke `SELECT f(...)`: døren returnerer to
        # kolonner (ny, post_id), og `_skriv` overskriver `post_id` med
        # den andre. Er bevegelsen alt registrert under en annen nøkkel,
        # får kalleren id-en til raden som FAKTISK står der.
        return ("SELECT * FROM m13_registrer_post(%s,%s,%s,%s,%s::date,"
                "                                 %s,%s,%s,%s)",
                (tenant, pid, konto, ref, bokfort, belop, tekst, motpart,
                 bid),
                {"post_id": str(pid)}, "post_id")
    return _skriv(tjeneste, request, bygg)


def bilag_endepunkt(tjeneste, request):
    """POST /v1/avstemming/bilag (bestilling:opprett, idem).

    BELØPET ER ALLTID POSITIVT her — `retning` bærer fortegnet. To steder
    å lese fortegnet fra er ett sted for mye, og et negativt beløp på et
    `inn`-bilag ville vært en rad ingen kunne tolke.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        nummer = _tekst(kropp, "bilagsnummer", rid, MAKS_BILAGSNUMMER)
        retning = kropp.get("retning")
        if retning not in RETNINGER:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        belop = _ore(kropp, "belop_ore", rid, tillat_negativ=False)
        motpart = _tekst(kropp, "motpart", rid, MAKS_MOTPART)
        utstedt = _tekst(kropp, "utstedt", rid, 32)
        forfall = _valgfri_tekst(kropp, "forfall", rid, 32)
        bilid = _utled("bilag", tenant, nokkel)
        return ("SELECT m13_registrer_bilag(%s,%s,%s,%s,%s,%s,%s::date,"
                "                           %s::date,%s)",
                (tenant, bilid, nummer, retning, belop, motpart, utstedt,
                 forfall, bid),
                {"bilag_id": str(bilid)})
    return _skriv(tjeneste, request, bygg)


def match_endepunkt(tjeneste, request):
    """POST /v1/avstemming/match (bestilling:opprett, idem).

    OVERDEKNING OG DOBBELTMATCH HÅNDHEVES IKKE HER. Døren låser bilaget
    rådgivende og regner dekningen, den partielle unike indeksen gjør
    én-match-per-post sann for enhver skrivevei, og vakten feller
    fortegnsdommen. Et forsøk blir 409 fordi BASEN nektet, ikke fordi
    API-et sjekket — og det er hele grunnen til at reglene bor der.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        post = _uuid_felt(kropp, "post_id", rid)
        bilag = _uuid_felt(kropp, "bilag_id", rid)
        metode = kropp.get("metode")
        if metode not in METODER:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        begrunnelse = _valgfri_tekst(kropp, "begrunnelse", rid,
                                     MAKS_BEGRUNNELSE)
        aid = _utled("avstemming", tenant, nokkel)
        return ("SELECT m13_avstem(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, aid, post, bilag, metode, begrunnelse, bid),
                {"avstemming_id": str(aid), "post_id": str(post),
                 "bilag_id": str(bilag)})
    return _skriv(tjeneste, request, bygg)


def opphev_endepunkt(tjeneste, request):
    """POST /v1/avstemming/match/{avstemming_id}/opphev
    (bestilling:opprett, idem).

    OPPHEVING SLETTER IKKE. Raden får `opphevet_ts`, `opphevet_av` og en
    begrunnelse, og den partielle unike indeksen slipper da posten fri
    for en ny match. En slettet rad ville skjult at noen en gang mente
    noe annet — og det er nøyaktig det en revisor spør etter.

    `ny=false` betyr her «den var alt opphevet»: to klikk på den samme
    knappen er ikke en feil, det er en bruker som ville ha nøyaktig den
    tilstanden som alt gjelder.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        aid = _sti_uuid(request, "avstemming_id", rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        return ("SELECT m13_opphev_avstemming(%s,%s,%s,%s)",
                (tenant, aid, begrunnelse, bid),
                {"avstemming_id": str(aid)})
    return _skriv(tjeneste, request, bygg)
