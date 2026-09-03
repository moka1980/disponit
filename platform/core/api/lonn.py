"""M-39 lønnsgrunnlagets API (migrasjon 113).

Ni endepunkter: fire leseveier og fem skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver gjør
nøyaktig ett kall mot en `disponit_lonn_eier`-eid SECURITY
DEFINER-dør i 113, og runtime har ingen tabellrettigheter i det hele
tatt (SP-7).

MODULEN UTBETALER INGENTING OG PRODUSERER INGEN LØNNSFIL.

Håndverk/bygg-malen navngir modulen som verifikatoren `v_lonn`, betrodd
for `timer_mot_arbeidsplan`, `prosjektkode_gyldig` og `overtid_flagget`
— og bruker ALLE TRE til å slippe `timeliste.samle_og_valider` gjennom
som `modus: auto`, på dataklassene persondata og finansiell.

DOMMEN ER TODELT, og andre halvdel er den særegne: en LØNNSFIL er ikke
en betaling — det er en fil. Den ser harmløs ut, den kan «bare
genereres», og den er nettopp derfor farligere enn en enkelt
utbetaling: den rammer ALLE på én gang, og den rammer noen som har
regnet med beløpet. En feil i en faktura oppdages av en kunde som
klager. En feil i en lønnsfil oppdages av noen som ikke fikk husleia.

DERFOR FINNES DET INGEN UTBETALINGSDØR OG INGEN EKSPORTDØR HER.
`korreksjon` er en KILDE man registrerer en time fra — aldri en
handling API-et utfører mot noens konto.

TIMER ER HELE MINUTTER (M-25s dom, 107). Konverteringen fra timer skjer
i klienten, én gang, og API-et tar imot minutter som `int`. Det finnes
ingen vei inn for et flyttall: «7,5 time» er 7.499999999999999 på veien
tilbake, og en lønnskjøring som driver noen øre per rad driver
systematisk, i samme retning, hver måned, for alle.

OVERTID ER ET FUNN, IKKE ET FLAGG. Det finnes ingen `overtid`-parameter
noe sted her. Om en dag var overtid utledes av sveipen mot tenantens
egen normaltid; et flagg API-et tok imot ville vært nøyaktig den
attestasjonen `overtid_flagget` skal hvile på.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som M-13 (101) innførte
    og klynge 3, 4, M-41 (111) og M-19 (112) gjenbrukte.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som
    096/100–112.

SP-2 PÅ REGISTRERINGSDØRENE: `taker_id`, `plan_id` og `time_id` utledes
deterministisk av Idempotency-Key-en. For timen er det strengt
nødvendig: en gjentatt POST må ikke bli to arbeidsdager i et grunnlag
noen skal få betalt etter.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_TAKERE = 200
MAKS_HISTORIKK = 400
MAKS_DAGER = 200
MAKS_PLANER = 100
MAKS_REF = 100
MAKS_NAVN = 300
MAKS_KODE = 60
MAKS_NOTAT = 2000

#: Minutter i et døgn. Ytterpunktet, ikke en grense noen har valgt —
#: de reelle grensene er TENANTENS og ligger i `lonnsterskel`.
MINUTTER_DOGN = 1440
MINUTTER_UKE = 10080

#: LOVLIGE KILDER. `korreksjon` står her fordi en feilført time rettes
#: med en NY rad — ikke fordi modulen utfører en korreksjon mot noen.
KILDER = ("fort_av_ansatt", "fort_av_leder", "import", "korreksjon")

#: GRENSENES YTTERPUNKTER, ikke verdier. Speiler CHECK-ene i 113.
TERSKELGRENSER = {
    "normaltid_minutter_dag": (0, MINUTTER_DOGN),
    "normaltid_minutter_uke": (0, MINUTTER_UKE),
    "avvik_minutter": (0, MINUTTER_DOGN),
    "uten_plan_dogn": (0, 3650),
    "vurderingsvindu_dogn": (1, 3650),
}

_M39_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m39:lonn")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M39_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    """Tekst, TRIMMET — og lengden måles på det som faktisk lagres."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valg(kropp, felt: str, rid, lovlige) -> str:
    """Ett av et LUKKET sett."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi not in lovlige:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _heltall(kropp, felt: str, rid, minst: int, mest: int) -> int:
    """Et heltall, og DEN ENESTE veien det kommer inn.

    `isinstance(x, bool)` er ikke pedanteri: i Python er `True` en `int`,
    og uten sjekken ville `{"minutter": true}` blitt ett minutt.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _minutter(kropp, felt: str, rid) -> int:
    """MINUTTER, ALDRI TIMER — og aldri et flyttall.

    Dette er dom 1s ytre gjerde. `_heltall` avviser `7.5` fordi det ikke
    er en `int`, og det er hele poenget: hadde API-et tatt imot timer
    med desimaler, måtte det ganget med 60 og rundet av — og da ville
    avrundingsfeilen bodd HER i stedet for i klienten, usynlig for den
    som førte timen.
    """
    return _heltall(kropp, felt, rid, 0, MINUTTER_DOGN)


def _bool(kropp, felt: str, rid) -> bool:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt, False)
    if not isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
    psycopg.errors.InsufficientPrivilege,
)


def _doerfeil(e, rid):
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if "ref_unik" in str(e) or "kilde_unik" in str(e) \
                or "versjon_unik" in str(e):
            return _Avbrudd(_feil("lonn_ulovlig_tilstand", rid, 409))
        # PK-kollisjon på en SP-2-utledet id: SAMME nøkkel, samme rad.
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: en time i framtida, en plan skrevet
        # bakover, en uke kortere enn en dag.
        return _Avbrudd(_feil("lonn_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # Vaktenes dommer: en frosset time, to overlappende planer.
        return _Avbrudd(_feil("lonn_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Lønnsflatens tilstand i én transaksjon, gjennom tre lesedører."""
    s = conn.execute("SELECT * FROM m39_lonnsstatus(%s)",
                     (tenant,)).fetchone()
    takere = [
        {"taker_id": str(r[0]), "ekstern_ref": r[1], "navn": r[2],
         "aktiv": r[3],
         "plan_id": str(r[4]) if r[4] else None,
         "planlagt_minutter_dag": r[5], "plan_prosjektkode": r[6],
         "plan_fra": r[7].isoformat() if r[7] else None,
         "sum_minutter": r[8], "dager": r[9],
         "siste_dato": r[10].isoformat() if r[10] else None,
         "apne_funn": list(r[11] or ())}
        for r in conn.execute("SELECT * FROM m39_takerne(%s,%s)",
                              (tenant, MAKS_TAKERE)).fetchall()]
    t = conn.execute("SELECT * FROM m39_tersklene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "takere": s[0], "aktive": s[1], "med_timer": s[2],
            "med_plan": s[3], "apne_funn": s[4],
            "apne_overtid": s[5], "har_terskel": s[6],
            "terskelversjon": s[7], "vist": len(takere)},
        "takere": takere,
        "terskler": None if t is None else {
            "normaltid_minutter_dag": t[0],
            "normaltid_minutter_uke": t[1],
            "avvik_minutter": t[2], "uten_plan_dogn": t[3],
            "vurderingsvindu_dogn": t[4],
            "versjon": t[5], "oppdatert": t[6].isoformat(),
            "oppdatert_av": t[7]}}


def lonnsbilde(tjeneste, request):
    """GET /v1/lonn (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def dager_endepunkt(tjeneste, request):
    """GET /v1/lonn/{taker_id}/dager (okonomi:read).

    MODULENS EGENTLIGE SVAR. `timer_mot_arbeidsplan` er et spørsmål om
    en SAMMENLIGNING, og her står begge tallene på samme linje: hva som
    ble ført, hva som var planlagt, og differansen — i minutter, som et
    heltall, aldri som en prosent.

    `planlagt_minutter` er `null` når ingen plan gjaldt den dagen. Det
    er ikke det samme som null minutter, og en flate som viste dem likt
    ville gjort «ingen plan» om til «planlagt fri».
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        tid = _sti_uuid(request, "taker_id", rid)
        rader = conn.execute("SELECT * FROM m39_dagene(%s,%s,%s)",
                             (auth.tenant, tid,
                              MAKS_DAGER)).fetchall()
        return kanonisk_json({
            "taker_id": str(tid),
            "dager": [
                {"dato": r[0].isoformat(), "minutter": r[1],
                 "planlagt_minutter": r[2], "avvik_minutter": r[3],
                 "prosjektkoder": list(r[4] or ()),
                 "plan_prosjektkode": r[5], "poster": r[6],
                 "ukjent_prosjektkode": r[7]}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def historikk_endepunkt(tjeneste, request):
    """GET /v1/lonn/{taker_id}/historikk (okonomi:read).

    HVER TIMEREGISTRERING, med sin kilde. En feilført time rettes med
    en NY rad (`kilde = korreksjon`), så begge står her — og det er
    nettopp det sporet en lønnstvist står på.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        tid = _sti_uuid(request, "taker_id", rid)
        rader = conn.execute(
            "SELECT * FROM m39_timehistorikken(%s,%s,%s)",
            (auth.tenant, tid, MAKS_HISTORIKK)).fetchall()
        return kanonisk_json({
            "taker_id": str(tid),
            "timer": [
                {"time_id": str(r[0]), "dato": r[1].isoformat(),
                 "minutter": r[2], "prosjektkode": r[3],
                 "kilde": r[4], "kilde_ref": r[5], "notat": r[6],
                 "registrert": r[7].isoformat(), "registrert_av": r[8]}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def planer_endepunkt(tjeneste, request):
    """GET /v1/lonn/{taker_id}/planer (okonomi:read).

    PLANENE, nyeste først. «Hvilken plan gjaldt den dagen» har nøyaktig
    ett svar — periodene overlapper ikke — og det er den egenskapen
    `timer_mot_arbeidsplan` til slutt må hvile på.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        tid = _sti_uuid(request, "taker_id", rid)
        rader = conn.execute("SELECT * FROM m39_planene(%s,%s,%s)",
                             (auth.tenant, tid,
                              MAKS_PLANER)).fetchall()
        return kanonisk_json({
            "taker_id": str(tid),
            "planer": [
                {"plan_id": str(r[0]), "versjon": r[1],
                 "planlagt_minutter_dag": r[2], "prosjektkode": r[3],
                 "gyldig_fra": r[4].isoformat(),
                 "gyldig_til": r[5].isoformat() if r[5] else None,
                 "begrunnelse": r[6], "opprettet": r[7].isoformat(),
                 "opprettet_av": r[8]}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen alle fire skriveveiene deler."""
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
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        # ET FELT SOM ALLTID ER NULL ER ET LØFTE API-ET IKKE HOLDER.
        # `felt is None` merker VOID-dørene presist: psycopg gir `''`
        # for VOID, ikke None (111s lærdom).
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def terskler_endepunkt(tjeneste, request):
    """POST /v1/lonn/terskler (bestilling:opprett, idem).

    GRENSENE ER TENANTENS. En bedrift med 37,5-timers uke og en med
    rotasjonsturnus har ikke samme normaltid, og en konstant i koden
    ville vært nøyaktig den fullmakten invarianten
    `timegrense_hardkodet` forbyr.

    ÆRLIG OM HVA DETTE IKKE ER: grensene går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert og har ingen fasilitet for en
    tenant-innstilling. Invarianten er oppfylt i den forstand som betyr
    noe — tenanten eier og fører verdiene — men koblingen til M-1 står
    igjen som et NAVNGITT gap, samme gap som 111 og 112 navnga.

    `vurderingsvindu_dogn` ER OGSÅ TENANTENS, og det er ikke en
    bekvemmelighet: `overtid`, `avvik_mot_plan` og
    `ukjent_prosjektkode` kan IKKE rettes — timeregistreringene er
    frosset — så uten et vindu ville de tre funnene aldri kunne lukkes.
    Et funnregister som alltid sier ja sier ingenting.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        dag = _heltall(kropp, "normaltid_minutter_dag", rid,
                       *TERSKELGRENSER["normaltid_minutter_dag"])
        uke = _heltall(kropp, "normaltid_minutter_uke", rid,
                       *TERSKELGRENSER["normaltid_minutter_uke"])
        avvik = _heltall(kropp, "avvik_minutter", rid,
                         *TERSKELGRENSER["avvik_minutter"])
        uten_plan = _heltall(kropp, "uten_plan_dogn", rid,
                             *TERSKELGRENSER["uten_plan_dogn"])
        vindu = _heltall(kropp, "vurderingsvindu_dogn", rid,
                         *TERSKELGRENSER["vurderingsvindu_dogn"])
        return ("SELECT m39_sett_terskler(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, dag, uke, avvik, uten_plan, vindu, bid), {},
                "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_taker_endepunkt(tjeneste, request):
    """POST /v1/lonn/taker (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        tid = _utled("taker", tenant, nokkel)
        return ("SELECT m39_registrer_taker(%s,%s,%s,%s,%s)",
                (tenant, tid, ref, navn, bid),
                {"taker_id": str(tid)}, None)
    return _skriv(tjeneste, request, bygg)


def sett_plan_endepunkt(tjeneste, request):
    """POST /v1/lonn/{taker_id}/plan (bestilling:opprett, idem).

    EN NY PLAN AVLØSER DEN FORRIGE i samme transaksjon, så «hvilken plan
    gjaldt den dagen» alltid har nøyaktig ett svar. Uten den
    egenskapen er `timer_mot_arbeidsplan` ikke et spørsmål man kan
    svare på i det hele tatt.

    BEGRUNNELSEN ER OBLIGATORISK: planen avgjør hva noens timer måles
    mot, og en slik beslutning skal kunne etterprøves.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        tid = _sti_uuid(request, "taker_id", rid)
        minutter = _minutter(kropp, "planlagt_minutter_dag", rid)
        kode = _tekst(kropp, "prosjektkode", rid, MAKS_KODE)
        fra = _tekst(kropp, "gyldig_fra", rid, 32)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_NOTAT)
        pid = _utled("plan", tenant, nokkel)
        return ("SELECT m39_sett_arbeidsplan("
                "%s,%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, pid, tid, minutter, kode, fra, begrunnelse,
                 bid),
                {"taker_id": str(tid), "plan_id": str(pid)}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_timer_endepunkt(tjeneste, request):
    """POST /v1/lonn/{taker_id}/timer (bestilling:opprett, idem).

    MINUTTER GÅR INN, aldri timer med desimaler. Se `_minutter`.

    DEN SETTER INGEN OVERTIDSFLAGG. Det finnes ingen `overtid`-parameter
    her: om dagen var overtid utledes av sveipen mot tenantens egen
    normaltid, og blir et funn noen må se på. Et flagg API-et tok imot
    ville vært nøyaktig den attestasjonen `overtid_flagget` skal hvile
    på — og det ville stått der som et faktum ingen hadde vurdert.

    SVARET SIER OM TIMEN HAR EN PLAN Å MÅLES MOT. Den som fører en time
    skal få vite med én gang at den ikke måles mot noe — ikke først når
    sveipen har gått en uke senere.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        tid = _sti_uuid(request, "taker_id", rid)
        dato = _tekst(kropp, "dato", rid, 32)
        minutter = _minutter(kropp, "minutter", rid)
        kode = _tekst(kropp, "prosjektkode", rid, MAKS_KODE)
        kilde = _valg(kropp, "kilde", rid, KILDER)
        kilde_ref = _tekst(kropp, "kilde_ref", rid, MAKS_REF)
        notat = _tekst(kropp, "notat", rid, MAKS_NOTAT)
        hid = _utled("time", tenant, nokkel)
        return ("SELECT m39_registrer_timer("
                "%s,%s,%s,%s::date,%s,%s,%s,%s,%s,%s)",
                (tenant, hid, tid, dato, minutter, kode, kilde,
                 kilde_ref, notat, bid),
                {"taker_id": str(tid), "time_id": str(hid)},
                "har_plan")
    return _skriv(tjeneste, request, bygg)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/lonn/{taker_id}/aktiv (bestilling:opprett, idem).

    EN LØNNSTAKER DEAKTIVERES, HEN SLETTES ALDRI: en slettet taker ville
    tatt timegrunnlaget med seg — og det er det eneste som kan avgjøre
    en lønnstvist i ettertid.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        tid = _sti_uuid(request, "taker_id", rid)
        # `aktiv` ER PÅKREVD HER. `_bool` faller tilbake til `false`, og
        # en kropp uten feltet ville derfor DEAKTIVERT takeren — altså
        # en utelatelse som utfører en handling (CodeRabbit, 108).
        if "aktiv" not in kropp:
            raise _Avbrudd(_feil("request_feilformet", rid))
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m39_sett_takeraktiv(%s,%s,%s,%s)",
                (tenant, tid, aktiv, bid),
                {"taker_id": str(tid)}, "endret")
    return _skriv(tjeneste, request, bygg)
