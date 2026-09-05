"""M-15 likviditets- og kostnadsagentens API (migrasjon 128).

Fjorten endepunkter: sju leseveier og sju skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_likviditet_eier`-eid SECURITY DEFINER-dør i 128, og runtime
har ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM SIER OPP NOE, OG INGEN SOM BETALER.

Katalogens vaktsetning er «forslag merkes som prognose; oppsigelser og
betalinger utføres bare via egne policykontrollerte moduler», og
modulen holder den i datamodellen: `kostnadstiltak.status` har ingen
`iverksatt`. Et tiltak kan bli `vurdert` eller `avvist` av et
menneske, og der stopper M-15. Oppsigelsen går gjennom M-41s vei, som
ikke vet at denne tabellen finnes.

KLYNGENS DELTE DOM: EN GAL PROGNOSE SER NØYAKTIG UT SOM EN RIKTIG
PROGNOSE — helt til horisonten er passert, og da har alle sluttet å
se. Derfor er `prognose_uten_maaling` et funn ingen kan lukke, og
`POST /maaling` er den ENESTE veien til å lukke det.

FEM NEKT SOM ER VERDT Å KJENNE:

  * `POST /prognose` NEKTER uten tenantens grenser. Uten horisonten
    finnes det ingen dato å måle mot, og da er `prognose_uten_maaling`
    et funn som aldri kan reises.

  * `POST /prognose` NEKTER mot en avviklet modellversjon. Arkivet tar
    imot den; det er BRUKEN som er stengt.

  * `POST /prognose` NEKTER på tomt grunnlag. En bane tegnet på
    ingenting er husets mest selvsikre løgn.

  * `POST /maaling` NEKTER en uke som ikke er over. En måling av en
    uke som fortsatt løper er et delvis tall som ser ut som et
    endelig — og den ville lukket funnet uten at noen hadde sett hva
    som skjedde.

  * `POST /tiltak` NEKTER uten reversibilitet. Et tiltak ingen har
    vurdert reversibiliteten av er et tiltak ingen kan angre, og det
    er nettopp de tiltakene som ser billigst ut.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `okonomi:read` og ikke `security:read` som M-53s, og
skillet er datasettet: dette er bank, fordringer og kontantbane —
finansleserens eget bord. Det er ingen personopplysninger her utover
navnet på den som registrerte en forpliktelse.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_PROGNOSER = 200
MAKS_POSTER = 500
MAKS_TILTAK = 200
MAKS_TEKST = 4000
MAKS_NAVN = 500
#: Beskrivelsen må være SKREVET. Seksten tegn er ikke en
#: kvalitetsgaranti — det er en terskel mot «spare penger» som eneste
#: begrunnelse for et tiltak noen skal ta stilling til.
MIN_TEKST = 16

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 128.
KRAVGRENSER = {
    "horisont_uker": (1, 104),
    "grunnlag_maks_alder_dogn": (1, 90),
    "maalefrist_dogn": (1, 180),
    "modellvarsel_dogn": (1, 365),
}

POSTTYPER = ("lonn", "husleie", "skatt", "avgift", "abonnement",
             "laan", "annet")
GJENTAKELSER = ("engang", "ukentlig", "maanedlig", "kvartalsvis",
                "aarlig")
REVERSIBILITET = ("reversibel", "delvis_reversibel", "irreversibel")
VURDERINGER = ("vurdert", "avvist")

#: USIKKERHETEN OPPGIS I BASISPUNKTER, ikke i prosent som flyttall.
#: Et flyttall ville gitt to klienter to ulike bånd for «femten
#: prosent», og båndet er det eneste som gjør prognosen etterprøvbar.
MIN_USIKKERHET_BP = 1
MAKS_USIKKERHET_BP = 10000

_M15_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL,
                        "disponit:m15:likviditet")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M15_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _lang_tekst(kropp, felt: str, rid) -> str:
    """Ikke-tom OG lang nok.

    En begrunnelse på tre tegn er ikke en begrunnelse noen kan ta
    stilling til, og et tiltak uten den er et tall noen fant på.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_TEKST)
    if len(verdi) < MIN_TEKST:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valg(kropp, felt: str, rid, lovlige) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi not in lovlige:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _heltall(kropp, felt: str, rid, minst: int, mest: int) -> int:
    """`isinstance(x, bool)` fordi `True` er en `int` i Python."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _ore(kropp, felt: str, rid) -> int:
    """ØRE SOM HELTALL, aldri kroner som flyttall.

    `0.1 + 0.2` er ikke `0.3`, og en likviditetsprognose som samler
    tolv ukers avrundingsfeil ville bommet med et beløp ingen kan
    forklare. Resten av huset regner i øre; det gjør denne også.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    # Et beløp større enn dette er en tastefeil, ikke en forpliktelse.
    if abs(verdi) > 10**15:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _ore_valgfri(kropp, felt: str, rid) -> int | None:
    if kropp.get(felt) is None:
        return None
    return _ore(kropp, felt, rid)


def _dato(kropp, felt: str, rid) -> str:
    """ISO-dato som STRENG. Døra parser den; API-et validerer formen.

    En dato frosset ved import ville råtnet med kalenderen (124s
    CodeRabbit-funn), så `date.today()` står ingen steder her.
    """
    import datetime
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.date.fromisoformat(verdi)
    except ValueError as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e
    return verdi


def _dato_valgfri(kropp, felt: str, rid) -> str | None:
    if kropp.get(felt) is None:
        return None
    return _dato(kropp, felt, rid)


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(request.path_params[navn])
    except (KeyError, ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _kropp_uuid(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _doerfeil(e, rid):
    """Dørens NEKT er brukerens feil, ikke serverens.

    Uten denne ville hvert lovlige nekt i 128 blitt en 500 — og en 500
    på «du kan ikke måle en uke som ikke er over» er en feilmelding
    ingen kan handle på (121–127s form).
    """
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, (psycopg.errors.RaiseException,
                      psycopg.errors.InvalidParameterValue,
                      psycopg.errors.InsufficientPrivilege,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.NoDataFound,
                      psycopg.errors.UniqueViolation,
                      psycopg.errors.ForeignKeyViolation)):
        return _Avbrudd(_feil("request_feilformet", rid,
                              detalj=str(e).split("\n")[0]))
    return None


# ---------------------------------------------------------------------
# RADBYGGERNE — ÉN FORM, ETT STED.
# ---------------------------------------------------------------------

def _bilderad(r) -> dict:
    return {
        "prognoser": r[0], "aktive": r[1], "maalte": r[2],
        "umaalte": r[3], "treff": r[4], "bom": r[5],
        "modeller": r[6], "gyldige_modeller": r[7], "poster": r[8],
        "tiltak": r[9], "uvurderte_tiltak": r[10],
        "apne_funn": r[11], "laveste_ore": r[12], "har_krav": r[13],
        # ALLE FIRE GRENSENE (123s lærdom: et skjema som viser mindre
        # enn det lagrer er en felle — flaten forhåndsutfyller herfra).
        "horisont_uker": r[14],
        "grunnlag_maks_alder_dogn": r[15],
        "maalefrist_dogn": r[16], "modellvarsel_dogn": r[17],
        "kravversjon": r[18],
    }


def _prognoserad(r) -> dict:
    return {
        "prognose_id": str(r[0]), "laget_dato": r[1].isoformat(),
        "horisont_uker": r[2], "gjelder_til": r[3].isoformat(),
        "modellversjon": r[4], "baselinje": r[5],
        "startsaldo_ore": r[6], "laveste_ore": r[7],
        "grunnlag_alder_dogn": r[8], "antall_uker": r[9],
        "antall_maalinger": r[10], "treff": r[11],
        "kravversjon": r[12], "opprettet_av": r[13], "aktiv": r[14],
    }


def _banerad(r) -> dict:
    return {
        "uke_nr": r[0], "ukeslutt": r[1].isoformat(),
        "punkt_ore": r[2], "nedre_ore": r[3], "ovre_ore": r[4],
        "inn_ore": r[5], "ut_ore": r[6], "faktisk_ore": r[7],
        "avvik_ore": r[8], "innenfor_intervall": r[9],
        "maalt_av": r[10],
        # FLATEN REGNER IKKE UT SELV om uken er over — regelen bor i
        # basen og følger med hver rad (124s `kan_lukkes`-form).
        "kan_maales": r[11],
    }


def _postrad(r) -> dict:
    return {
        "post_id": str(r[0]), "posttype": r[1], "beskrivelse": r[2],
        "belop_ore": r[3], "forste_forfall": r[4].isoformat(),
        "gjentakelse": r[5],
        "gjelder_til": r[6].isoformat() if r[6] else None,
        "aktiv": r[7], "registrert": r[8].isoformat(),
        "registrert_av": r[9],
    }


def _modellrad(r) -> dict:
    return {
        "modell_id": str(r[0]), "navn": r[1], "versjon": r[2],
        "metode": r[3], "baselinje": r[4],
        "gyldig_fra": r[5].isoformat(),
        "gyldig_til": r[6].isoformat() if r[6] else None,
        "gyldig_naa": r[7], "dogn_til_utlop": r[8],
        "antall_prognoser": r[9],
    }


def _tiltaksrad(r) -> dict:
    return {
        "tiltak_id": str(r[0]), "beskrivelse": r[1],
        "forventet_effekt_ore": r[2], "reversibilitet": r[3],
        "grunnlag": r[4], "status": r[5], "vurdert_av": r[6],
        "vurderingsnotat": r[7], "opprettet": r[8].isoformat(),
        "opprettet_av": r[9],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1],
        "prognose_id": str(r[2]) if r[2] else None,
        "modell_id": str(r[3]) if r[3] else None,
        "over_grense": r[4], "detalj": r[5], "kravversjon": r[6],
        "forst_sett": r[7].isoformat(),
        "sist_sett_sveip": r[8].isoformat(), "apen": r[9],
        "lukket_ts": r[10].isoformat() if r[10] else None,
        "lukket_av": r[11], "lukkenotat": r[12], "kan_lukkes": r[13],
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124/127s form).

    Flaten tegner sammendraget, funnene, prognosene, modellene,
    postene og tiltakene i samme runde. Seks runder ville gitt seks
    mulige halvtegnede skjermer — og en flate der funnene og
    prognosene kunne komme fra ulike øyeblikk.
    """
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m15_bildet(%s)",
                         (tenant,)).fetchone()),
        "prognoser": [_prognoserad(r) for r in conn.execute(
            "SELECT * FROM m15_prognosene(%s,%s)",
            (tenant, MAKS_PROGNOSER)).fetchall()],
        "modeller": [_modellrad(r) for r in conn.execute(
            "SELECT * FROM m15_modellene(%s)", (tenant,)).fetchall()],
        "poster": [_postrad(r) for r in conn.execute(
            "SELECT * FROM m15_postene(%s,%s)",
            (tenant, MAKS_POSTER)).fetchall()],
        "tiltak": [_tiltaksrad(r) for r in conn.execute(
            "SELECT * FROM m15_tiltakene(%s,%s)",
            (tenant, MAKS_TILTAK)).fetchall()],
        # BARE DE ÅPNE. En funnliste som viste de lukkede med ville
        # vokst til den ble uleselig.
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m15_funnene(%s,%s)",
            (tenant, True)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def likviditetsbilde(tjeneste, request):
    """GET /v1/likviditet (okonomi:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def prognoser_endepunkt(tjeneste, request):
    """GET /v1/likviditet/prognoser (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m15_prognosene(%s,%s)",
                             (auth.tenant, MAKS_PROGNOSER)).fetchall()
        svar = {"request_id": rid, "vist": len(rader),
                "grense": MAKS_PROGNOSER,
                "prognoser": [_prognoserad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def bane_endepunkt(tjeneste, request):
    """GET /v1/likviditet/prognose/{prognose_id}/bane (okonomi:read).

    BANEN OG MÅLINGEN I SAMME SVAR. En bane uten målingen viser hva vi
    TRODDE, og det er halve historien — den andre halvdelen er om vi
    traff.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        pid = _sti_uuid(request, "prognose_id", rid)
        rader = conn.execute("SELECT * FROM m15_banen(%s,%s)",
                             (auth.tenant, pid)).fetchall()
        svar = {"prognose_id": str(pid), "request_id": rid,
                "bane": [_banerad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def poster_endepunkt(tjeneste, request):
    """GET /v1/likviditet/poster (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m15_postene(%s,%s)",
                             (auth.tenant, MAKS_POSTER)).fetchall()
        svar = {"request_id": rid, "vist": len(rader),
                "grense": MAKS_POSTER,
                "poster": [_postrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def modeller_endepunkt(tjeneste, request):
    """GET /v1/likviditet/modeller (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m15_modellene(%s)",
                             (auth.tenant,)).fetchall()
        svar = {"request_id": rid,
                "modeller": [_modellrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def tiltak_endepunkt(tjeneste, request):
    """GET /v1/likviditet/tiltak (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m15_tiltakene(%s,%s)",
                             (auth.tenant, MAKS_TILTAK)).fetchall()
        svar = {"request_id": rid, "vist": len(rader),
                "grense": MAKS_TILTAK,
                "tiltak": [_tiltaksrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/likviditet/funn (okonomi:read).

    `kan_lukkes` KOMMER FRA BASEN. To funntyper lukkes ikke av et
    menneske, og regelen bor ÉTT sted (`m15_funn_er_sveipens`).
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m15_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid,
                "funn": [_funnrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen seks av de sju skriveveiene deler.

    `/prognose` bruker den IKKE: den returnerer en RAD med horisonten,
    startsaldoen og det laveste punktet — den som ber om en prognose
    skal se hva den sier, ikke bare at den ble laget.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        sql, args, svar, felt = bygg(tenant, bid, nokkel, kropp, rid,
                                     request)
        try:
            ut = conn.execute(sql, args).fetchone()[0]
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def krav_endepunkt(tjeneste, request):
    """POST /v1/likviditet/krav (bestilling:opprett, idem).

    HORISONTEN ER TENANTENS BESLUTNING. Katalogen sier «13-ukers
    prognose», og 13 er standardverdien — men et byggefirma med
    kvartalsvise innbetalinger og en abonnementsbedrift med månedlig
    inntekt har ikke samme planleggingshorisont.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        h = _heltall(kropp, "horisont_uker", rid,
                     *KRAVGRENSER["horisont_uker"])
        g = _heltall(kropp, "grunnlag_maks_alder_dogn", rid,
                     *KRAVGRENSER["grunnlag_maks_alder_dogn"])
        m = _heltall(kropp, "maalefrist_dogn", rid,
                     *KRAVGRENSER["maalefrist_dogn"])
        v = _heltall(kropp, "modellvarsel_dogn", rid,
                     *KRAVGRENSER["modellvarsel_dogn"])
        return ("SELECT versjon FROM"
                " m15_sett_krav(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, h, g, m, v, bid, nokkel), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_modell_endepunkt(tjeneste, request):
    """POST /v1/likviditet/modell (bestilling:opprett, idem).

    EN ALT AVVIKLET MODELLVERSJON KAN REGISTRERES: arkivet skal kunne
    svare på hvilken modell som gjaldt den gangen. Skillet går ved
    PROGNOSEN — `/prognose` nekter mot en versjon som ikke gjelder i
    dag (124/127s form).

    `metode` og `baselinje` ER OBLIGATORISKE, og det er klyngens dom:
    en modell uten en skrevet metode kan ikke etterprøves, og en uten
    en navngitt baselinje kan ikke sammenlignes med «samme som forrige
    uke» — og da bærer den autoritet den ikke har fortjent.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        versjon = _tekst(kropp, "versjon", rid, MAKS_NAVN)
        metode = _lang_tekst(kropp, "metode", rid)
        baselinje = _tekst(kropp, "baselinje", rid, MAKS_NAVN)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        mid = _utled("modell", tenant, nokkel)
        return ("SELECT m15_registrer_modell(%s,%s,%s,%s,%s,%s,%s,"
                "%s,%s)",
                (tenant, mid, navn, versjon, metode, baselinje, fra,
                 til, bid),
                {"modell_id": str(mid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def registrer_post_endepunkt(tjeneste, request):
    """POST /v1/likviditet/post (bestilling:opprett, idem).

    RUTEN SOM FINNES FORDI HUSET IKKE KAN PRISE LØNN.

    M-39 har `arbeidsplan.planlagt_minutter_dag` og `lonnstaker`, og
    ingen sats. En modul som «utledet» lønnskostnaden fra timer uten
    pris ville regnet på et tall den fant på — og et oppfunnet tall i
    en likviditetsprognose er verre enn ingen prognose, fordi det ser
    like presist ut som de riktige.

    Derfor registreres forpliktelsen av et MENNESKE, og navnet står på
    raden.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        posttype = _valg(kropp, "posttype", rid, POSTTYPER)
        beskrivelse = _tekst(kropp, "beskrivelse", rid, MAKS_TEKST)
        belop = _ore(kropp, "belop_ore", rid)
        forfall = _dato(kropp, "forste_forfall", rid)
        gjentakelse = _valg(kropp, "gjentakelse", rid, GJENTAKELSER)
        til = _dato_valgfri(kropp, "gjelder_til", rid)
        pid = _utled("post", tenant, nokkel)
        return ("SELECT m15_registrer_post(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, pid, posttype, beskrivelse, belop, forfall,
                 gjentakelse, til, bid),
                {"post_id": str(pid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def lag_prognose_endepunkt(tjeneste, request):
    """POST /v1/likviditet/prognose (bestilling:opprett, idem).

    SVARET BÆRER HVA PROGNOSEN SIER, ikke bare at den ble laget: den
    som ber om en kontantbane skal se det laveste punktet med én gang.
    Et `{"ok": true}` ville krevd et nytt kall for å finne ut om
    banen går under null — og det kallet blir ikke alltid gjort.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        mid = _kropp_uuid(kropp, "modell_id", rid)
        usikkerhet = _heltall(kropp, "usikkerhet_bp", rid,
                              MIN_USIKKERHET_BP, MAKS_USIKKERHET_BP)
        pid = _utled("prognose", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM m15_lag_prognose(%s,%s,%s,%s,%s)",
                (tenant, pid, mid, usikkerhet, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"prognose_id": str(pid), "horisont_uker": r[0],
                    "gjelder_til": r[1].isoformat(),
                    "startsaldo_ore": r[2], "uker": r[3],
                    "laveste_ore": r[4], "modellversjon": r[5],
                    "kravversjon": r[6], "ny": r[7]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_maaling_endepunkt(tjeneste, request):
    """POST /v1/likviditet/prognose/{prognose_id}/maaling.

    DEN ENESTE VEIEN TIL Å LUKKE `prognose_uten_maaling`.

    `innenfor_intervall` REGNES AV BÅNDET SOM STO PÅ RADEN, ikke av
    noe kalleren oppgir. Hadde kalleren fått si «ja, dette var
    innenfor», ville målingen vært en karakter modulen ga seg selv.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        pid = _sti_uuid(request_, "prognose_id", rid)
        uke = _heltall(kropp, "uke_nr", rid, 1, 104)
        faktisk = _ore(kropp, "faktisk_ore", rid)
        baselinje = _ore_valgfri(kropp, "baselinje_ore", rid)
        return ("SELECT avvik_ore FROM"
                " m15_registrer_maaling(%s,%s,%s,%s,%s,%s)",
                (tenant, pid, uke, faktisk, baselinje, bid),
                {"prognose_id": str(pid), "uke_nr": uke},
                "avvik_ore")
    return _skriv(tjeneste, request, bygg)


def foresla_tiltak_endepunkt(tjeneste, request):
    """POST /v1/likviditet/tiltak (bestilling:opprett, idem).

    `reversibilitet` ER OBLIGATORISK. Et tiltak ingen har vurdert
    reversibiliteten av er et tiltak ingen kan angre — og det er
    nettopp de tiltakene som foreslås først, fordi de ser billigst ut.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        beskrivelse = _lang_tekst(kropp, "beskrivelse", rid)
        effekt = _ore(kropp, "forventet_effekt_ore", rid)
        rev = _valg(kropp, "reversibilitet", rid, REVERSIBILITET)
        grunnlag = _lang_tekst(kropp, "grunnlag", rid)
        tid = _utled("tiltak", tenant, nokkel)
        return ("SELECT m15_foresla_tiltak(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, tid, beskrivelse, effekt, rev, grunnlag, bid),
                {"tiltak_id": str(tid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def vurder_tiltak_endepunkt(tjeneste, request):
    """POST /v1/likviditet/tiltak/{tiltak_id}/vurder.

    DET FINNES INGEN `iverksatt`, OG DET ER V1-DOMMEN. Et menneske kan
    si at tiltaket er VURDERT eller AVVIST, og der stopper modulen.
    Oppsigelsen av abonnementet går gjennom M-41s policykontrollerte
    vei, og den veien vet ingenting om denne tabellen.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        tid = _sti_uuid(request_, "tiltak_id", rid)
        status = _valg(kropp, "status", rid, VURDERINGER)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        return ("SELECT m15_vurder_tiltak(%s,%s,%s,%s,%s)",
                (tenant, tid, status, notat, bid),
                {"tiltak_id": str(tid)}, "status")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/likviditet/funn/{funn_id}/lukk.

    TO FUNNTYPER NEKTES, og regelen bor i basen
    (`m15_funn_er_sveipens`). `prognose_uten_maaling` lukkes av at
    MÅLINGEN registreres — ikke av at noen klikker den bort.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        fid = _sti_uuid(request_, "funn_id", rid)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        return ("SELECT m15_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, notat, bid),
                {"funn_id": str(fid), "lukket": True}, None)
    return _skriv(tjeneste, request, bygg)
