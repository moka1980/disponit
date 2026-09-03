"""M-41 betalings- og abonnementsstatusagentens API (migrasjon 111).

Sju endepunkter: to leseveier og fem skriveveier, alle mot dører. Ingen
av dem rører en tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_betaling_eier`-eid SECURITY DEFINER-dør i 111, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN REFUNDERER INGENTING OG AUTORISERER INGEN BETALING.
Netthandelsmalen navngir modulen som verifikatoren `v_betaling`,
betrodd for `betaling_autorisert` og `samme_betalingsmiddel` — og
bruker den til å slippe `refusjon.utfor` gjennom som `modus: auto`,
`reversering: irreversibel`, opp til 5000 NOK.

Automatisk. Irreversibel. Penger ut døra. Gatet på en verifikator som
aldri har eksistert. Å ta den fullmakten før noen har målt hvor ofte
statusen vår stemmer med betalingsleverandørens, er å la modulen
definere sin egen troverdighet — med kundens penger som innsats.

DERFOR FINNES DET INGEN REFUSJONSDØR HER. `refundert` og `tilbakefort`
er STATUSER man registrerer når de HAR skjedd, meldt av en kilde. De er
ikke handlinger API-et kan utløse.

BETALINGSMIDDELET LAGRES ALDRI. Det går inn én gang, døren normaliserer
det, regner masken og den saltede hashen, og kaster nummeret. SVARET ER
MASKEN — API-et har ingen vei tilbake til nummeret, og logger det ikke.

HVER STATUS HAR EN KILDE. `kilde` er et lukket sett og `kilde_ref` er
påkrevd: leverandørens hendelses-id, avstemmingsraden, eller
referansen mennesket førte. En status uten kilde er en påstand, og
`betaling_autorisert` ville hvilt på påstanden.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som M-13 (101) innførte
    og klynge 3 og 4 gjenbrukte.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som
    096/100–110.

SP-2 PÅ REGISTRERINGSDØRENE: `subjekt_id` og `hendelse_id` utledes
deterministisk av Idempotency-Key-en. For statushendelsen er det
strengt nødvendig: en gjentatt POST må ikke bli to statusskift i en
historikk som ER beviset.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_SUBJEKTER = 200
MAKS_HISTORIKK = 200
MAKS_REF = 100
MAKS_NAVN = 300
MAKS_MIDDEL = 64
MAKS_NOTAT = 2000

#: Ytterpunktet for et beløp i øre (101s form, gjenbrukt i 104–111).
MAKS_ORE = 10 ** 13

#: LOVLIGE STATUSER. `refundert` og `tilbakefort` står her fordi de kan
#: ha SKJEDD og skal kunne registreres — ikke fordi modulen utfører dem.
STATUSER = ("opprettet", "autorisert", "gjennomfort", "feilet",
            "refundert", "tilbakefort")

#: LOVLIGE KILDER. «Hvor kom denne statusen fra» er hele forskjellen
#: mellom en måling og en påstand.
KILDER = ("leverandor", "avstemming", "manuell", "portal")

#: LOVLIGE ABONNEMENTSSTATUSER.
ABONNEMENTSSTATUSER = ("aktivt", "pauset", "i_restanse", "avsluttet")

#: TERSKLENES YTTERPUNKTER, ikke verdier. Speiler CHECK-ene i 111.
TERSKELGRENSER = {
    "uavklart_dogn": (0, 3650),
    "reautorisasjon_dogn": (0, 3650),
}
#: Beløpsavviket har sitt eget ytterpunkt — det er øre, ikke døgn.
MAKS_AVVIK_ORE = 100_000_000

_M41_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m41:betaling")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M41_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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
    og uten sjekken ville `{"belop_ore": true}` blitt beløpet 1 øre.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _ore(kropp, felt: str, rid) -> int:
    return _heltall(kropp, felt, rid, 0, MAKS_ORE - 1)


def _ore_valgfritt(kropp, felt: str, rid) -> int | None:
    """Det FORVENTEDE beløpet er valgfritt.

    En status kan komme uten at noen har ført hva den skulle vært, og en
    tvungen null der ville vært en påstand om at ingenting var ventet —
    altså et beløpsavvik modulen fant på selv.
    """
    if kropp.get(felt) is None:
        return None
    return _ore(kropp, felt, rid)


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
                or "en_apen" in str(e):
            return _Avbrudd(_feil("betaling_ulovlig_tilstand", rid, 409))
        # PK-kollisjon på en SP-2-utledet id: SAMME nøkkel, samme rad.
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: en status i framtida, et for kort
        # betalingsmiddel, en periode skrevet bakover.
        return _Avbrudd(_feil("betaling_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # Vaktens dommer: en frosset hendelse, to overlappende perioder.
        return _Avbrudd(_feil("betaling_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Betalingsflatens tilstand i én transaksjon, gjennom tre lesedører."""
    s = conn.execute("SELECT * FROM m41_betalingsstatus(%s)",
                     (tenant,)).fetchone()
    subjekter = [
        {"subjekt_id": str(r[0]), "ekstern_ref": r[1], "navn": r[2],
         "aktiv": r[3], "status": r[4], "belop_ore": r[5],
         "forventet_ore": r[6], "valuta": r[7],
         "betalingsmiddel_maske": r[8], "kilde": r[9],
         "inntruffet": r[10].isoformat() if r[10] else None,
         "abonnementsstatus": r[11], "hendelser": r[12],
         "apne_funn": list(r[13] or ())}
        for r in conn.execute("SELECT * FROM m41_subjektene(%s,%s)",
                              (tenant, MAKS_SUBJEKTER)).fetchall()]
    t = conn.execute("SELECT * FROM m41_tersklene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "subjekter": s[0], "aktive": s[1], "med_status": s[2],
            "gjennomforte": s[3], "apne_funn": s[4],
            "apne_avvik": s[5], "har_terskel": s[6],
            "terskelversjon": s[7], "vist": len(subjekter)},
        "subjekter": subjekter,
        "terskler": None if t is None else {
            "uavklart_dogn": t[0], "belopsavvik_ore": t[1],
            "reautorisasjon_dogn": t[2], "versjon": t[3],
            "oppdatert": t[4].isoformat(), "oppdatert_av": t[5]}}


def betalingsbilde(tjeneste, request):
    """GET /v1/betaling (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def historikk_endepunkt(tjeneste, request):
    """GET /v1/betaling/{subjekt_id}/historikk (okonomi:read).

    HVER STATUS, med sin kilde. `endret` sier hvilken linje som var et
    STATUSSKIFT, og `middel_endret` hvilken som byttet betalingsmiddel —
    det siste er grunnlaget `samme_betalingsmiddel` en dag skal hvile på.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        sid = _sti_uuid(request, "subjekt_id", rid)
        rader = conn.execute(
            "SELECT * FROM m41_statushistorikken(%s,%s,%s)",
            (auth.tenant, sid, MAKS_HISTORIKK)).fetchall()
        return kanonisk_json({
            "subjekt_id": str(sid),
            "hendelser": [
                {"hendelse_id": str(r[0]), "status": r[1],
                 "belop_ore": r[2], "forventet_ore": r[3],
                 "valuta": r[4], "betalingsmiddel_maske": r[5],
                 "kilde": r[6], "kilde_ref": r[7],
                 "inntruffet": r[8].isoformat(), "notat": r[9],
                 "registrert": r[10].isoformat(), "registrert_av": r[11],
                 "endret": r[12], "middel_endret": r[13]}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen alle fem skriveveiene deler."""
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
        # `m41_registrer_subjekt` er en VOID-dør og har ingenting å
        # melde utover id-en (CodeRabbit, 108). `felt is None` er den
        # PRESISE måten å si det: psycopg gir `''` for VOID, ikke None,
        # og en verdibasert sjekk ville dessuten kollidert med
        # statusdøren — den kan lovlig svare tomt når ingen
        # betalingsmiddel ble oppgitt.
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def terskler_endepunkt(tjeneste, request):
    """POST /v1/betaling/terskler (bestilling:opprett, idem).

    GRENSENE ER TENANTENS. «To kroner i avvik er greit» er en
    forretningsbeslutning, ikke en teknisk detalj — og en konstant i
    koden ville vært nøyaktig den fullmakten invarianten
    `belopsgrense_hardkodet` forbyr.

    ÆRLIG OM HVA DETTE IKKE ER: grensene går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert og har ingen fasilitet for en
    tenant-innstilling. Invarianten er oppfylt i den forstand som betyr
    noe — tenanten eier og fører verdiene — men koblingen til M-1 står
    igjen som et NAVNGITT gap.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        uavklart = _heltall(kropp, "uavklart_dogn", rid,
                            *TERSKELGRENSER["uavklart_dogn"])
        avvik = _heltall(kropp, "belopsavvik_ore", rid, 0,
                         MAKS_AVVIK_ORE)
        reaut = _heltall(kropp, "reautorisasjon_dogn", rid,
                         *TERSKELGRENSER["reautorisasjon_dogn"])
        return ("SELECT m41_sett_terskler(%s,%s,%s,%s,%s)",
                (tenant, uavklart, avvik, reaut, bid), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_subjekt_endepunkt(tjeneste, request):
    """POST /v1/betaling/subjekt (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        sid = _utled("subjekt", tenant, nokkel)
        return ("SELECT m41_registrer_subjekt(%s,%s,%s,%s,%s)",
                (tenant, sid, ref, navn, bid),
                {"subjekt_id": str(sid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_status_endepunkt(tjeneste, request):
    """POST /v1/betaling/{subjekt_id}/status (bestilling:opprett, idem).

    DEN REGISTRERER, DEN BESTEMMER IKKE. `status` er hva kilden meldte,
    ikke hva modulen mener. `refundert` kan føres her — fordi en
    refusjon KAN ha skjedd — men ingenting i modulen kan UTLØSE en.

    KILDEN ER OBLIGATORISK, og `kilde_ref` med den: leverandørens
    hendelses-id, avstemmingsraden, eller referansen mennesket førte.

    BETALINGSMIDDELET ER VALGFRITT og lagres aldri. Går det inn, kommer
    masken ut — nummeret gjør det ikke.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        sid = _sti_uuid(request, "subjekt_id", rid)
        status = _valg(kropp, "status", rid, STATUSER)
        belop = _ore(kropp, "belop_ore", rid)
        forventet = _ore_valgfritt(kropp, "forventet_ore", rid)
        valuta = kropp.get("valuta", "NOK")
        if not isinstance(valuta, str) or len(valuta) != 3:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        middel = kropp.get("betalingsmiddel")
        if middel is not None:
            middel = _tekst(kropp, "betalingsmiddel", rid, MAKS_MIDDEL)
        kilde = _valg(kropp, "kilde", rid, KILDER)
        kilde_ref = _tekst(kropp, "kilde_ref", rid, MAKS_REF)
        inntruffet = _tekst(kropp, "inntruffet", rid, 32)
        notat = _tekst(kropp, "notat", rid, MAKS_NOTAT)
        hid = _utled("hendelse", tenant, nokkel)
        return ("SELECT m41_registrer_status("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, hid, sid, status, belop, forventet, valuta,
                 middel, kilde, kilde_ref, inntruffet, notat, bid),
                {"subjekt_id": str(sid), "hendelse_id": str(hid)},
                "betalingsmiddel_maske")
    return _skriv(tjeneste, request, bygg)


def sett_abonnement_endepunkt(tjeneste, request):
    """POST /v1/betaling/{subjekt_id}/abonnement (bestilling:opprett).

    EN NY PERIODE ER EN NY VERSJON. Døren lukker den forrige i samme
    transaksjon, så «hvilken abonnementsstatus gjaldt den dagen» alltid
    har nøyaktig ett svar.

    BEGRUNNELSEN ER OBLIGATORISK: statusen avgjør om kunden får
    tjenesten, og en slik beslutning skal kunne etterprøves.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        sid = _sti_uuid(request, "subjekt_id", rid)
        status = _valg(kropp, "status", rid, ABONNEMENTSSTATUSER)
        fra = _tekst(kropp, "gyldig_fra", rid, 32)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_NOTAT)
        return ("SELECT m41_sett_abonnementsstatus("
                "%s,%s,%s,%s::date,%s,%s)",
                (tenant, sid, status, fra, begrunnelse, bid),
                {"subjekt_id": str(sid)}, "versjon")
    return _skriv(tjeneste, request, bygg)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/betaling/{subjekt_id}/aktiv (bestilling:opprett, idem).

    ET SUBJEKT DEAKTIVERES, DET SLETTES ALDRI: et slettet subjekt ville
    tatt betalingshistorikken med seg.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        sid = _sti_uuid(request, "subjekt_id", rid)
        # `aktiv` ER PÅKREVD HER. `_bool` faller tilbake til `false`, og
        # en kropp uten feltet ville derfor DEAKTIVERT subjektet — altså
        # en utelatelse som utfører en handling (CodeRabbit, 108).
        if "aktiv" not in kropp:
            raise _Avbrudd(_feil("request_feilformet", rid))
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m41_sett_subjektaktiv(%s,%s,%s,%s)",
                (tenant, sid, aktiv, bid),
                {"subjekt_id": str(sid)}, "endret")
    return _skriv(tjeneste, request, bygg)
