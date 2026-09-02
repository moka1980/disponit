"""M-24 leverandør- og innkjøpsagentens API (migrasjon 105).

Sju endepunkter: to leseveier og fem skriveveier, alle mot dører. Ingen av
dem rører en tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_leverandor_eier`-eid SECURITY DEFINER-dør i 105, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN BETALER INGENTING. Det er v1-dommen: her finnes ingen
betalingsvei, ingen bankkonto, ingen utgående kø og ingen status som
heter `betalt` eller `utbetalt`. En utgående betaling er den ene
handlingen i hele katalogen som er umulig å angre — pengene er borte, og
de er borte hos noen andre. Katalogen sier selv «innen policygrenser», og
de grensene må VÆRE MÅLT før de kan settes.

OG MODULEN SETTER INGEN PRIS. Katalogen deler marginbeskyttelsen
eksplisitt: M-24 OPPDAGER kostnadsøkningen, M-26 FORESLÅR ny pris. Det
finnes derfor ingen returverdi her som er et prisforslag —
`prisavvik_promille` er AVVIKET mellom to målte tall, hva vi avtalte og
hva vi faktisk betalte.

BELØP ER HELTALL HELE VEIEN. Kroppen tar `_ore`-felter som `int`, aldri
et desimaltall, og et flyttall avvises med 400 — ikke rundes.

EN MÅLING ER MOT EN AVTALT VERDI. `leveranse` kan ikke registreres uten
en avtale, og målingsdatoen må ligge innenfor avtalens gyldighet. Både
døren og vakten i 105 håndhever det; API-et oversetter nektelsen til 409.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — scopet M-13 (101) innførte og M-23
    (104) gjenbrukte. Hva vi har avtalt å betale, og hva vi faktisk
    betaler, er virksomhetens pengestrøm. Gjenbrukt, ikke nytt.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som M-21
    (096), M-34 (100), M-13 (101), M-17 (102), M-18 (103) og M-23 (104).

SP-2 PÅ SKRIVEVEIENE SOM FØDER EN RAD: `leverandor_id`, `avtale_id` og
`leveranse_id` utledes deterministisk av Idempotency-Key-en. En dobbelt
registrert måling ville telt det samme bruddet to ganger — og et funn som
sier «tre brudd» der det var to, er et funn ingen kan handle på.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

#: Taket for hvor mange avtaler flaten viser. Dørens tak er 1000.
#: SAMMENDRAGET OG SLA-OVERSIKTEN TELLER LIKEVEL ALT.
MAKS_AVTALER = 200

MAKS_NAVN = 300
MAKS_YTELSE = 300
MAKS_REF = 200
MAKS_BEGRUNNELSE = 2000

#: Ytterpunktet for et beløp i øre. 10^13 øre er hundre milliarder kroner
#: — godt over enhver reell avtale, godt under `BIGINT`s tak, og godt
#: under `Number.MAX_SAFE_INTEGER` slik at tallet kommer helt fram
#: gjennom JSON til flaten (101s form, gjenbrukt i 104).
MAKS_ORE = 10 ** 13

#: Ytterpunktet for en SLA-verdi. Promille, døgn og timer ligger alle
#: godt under; taket finnes for at et absurd tall ikke skal nå basen.
MAKS_SLA_VERDI = 10 ** 7

#: SPEIL av CHECK-en i 105. Speilet finnes for at feilen skal bli 400 og
#: ikke 409: en ukjent verdi er en feilformet forespørsel, ikke en
#: tilstand som sier nei. Dørens CHECK og `m24_bryter_sla` er fortsatt
#: de bindende — og den siste RAISEr på en ukjent type framfor å svare
#: «ikke brudd», så en type som ble lagt til ett sted og glemt her kan
#: ikke bli et stille «alt er i orden».
SLA_TYPER = ("leveringstid_dogn", "responstid_timer",
             "feilrate_promille", "oppetid_promille")

#: TERSKLENE ER TENANTENS, og dette er BARE YTTERPUNKTENE — ikke
#: verdier. Et forsvar mot et tall som ikke kan ha vært ment, ikke en
#: mening om hva som er «for dyrt». Speiler CHECK-ene i 105.
TERSKELGRENSER = {
    "prisstigning_promille": (0, 100000),
    "sla_brudd_grense": (1, 1000),
    "avtale_varsel_dogn": (0, 3650),
    "maling_stillhet_dogn": (1, 3650),
}

_M24_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m24:leverandor")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M24_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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


def _heltall(kropp, felt: str, rid, minst: int, mest: int) -> int:
    """Et heltall, og DEN ENESTE veien det kommer inn.

    `isinstance(x, bool)` er ikke pedanteri: i Python er `True` en `int`,
    og uten sjekken ville `{"sla_brudd_grense": true}` blitt grensen 1 —
    altså «ett brudd er nok», satt av en typefeil ingen ville sett.
    Et flyttall avvises og rundes ALDRI.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _ore(kropp, felt: str, rid, *, minst: int = 0) -> int:
    """Beløpet i ØRE. Samme dom som `_heltall`, eget tak."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi < MAKS_ORE):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _kropp_uuid(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
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
        # TO AKTIVE AVTALER PÅ SAMME LEVERANDØR OG YTELSE lander her, via
        # `leveranseavtale_en_aktiv`. Det er en TILSTAND som sier nei —
        # ikke en idempotenskonflikt: kalleren ba om en NY avtale, og den
        # gamle er fortsatt aktiv.
        if "en_aktiv" in str(e) or "navn_unik" in str(e):
            return _Avbrudd(_feil("leverandor_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: en ukjent `sla_type`, en måling mot en
        # avsluttet avtale, en avslutning uten begrunnelse. Kroppen ER
        # velformet — det er innholdskravet basen håndhever som sier nei.
        return _Avbrudd(_feil("leverandor_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # MÅLINGEN UTENFOR AVTALENS VINDU lander her, via vaktens
        # `check_violation`. Den er en TILSTAND som sier nei, ikke en
        # feilformet kropp: datoen er lesbar, avtalen finnes, og likevel
        # er målingen et tall uten dom.
        return _Avbrudd(_feil("leverandor_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Leverandørflatens tilstand i én transaksjon, gjennom fem lesedører.

    SAMMENDRAGET OG SLA-OVERSIKTEN KOMMER FRA SINE EGNE DØRER og telles
    over ALT — ikke fra den avkortede listen. En flate som regnet
    SLA-oversikten fra de 200 viste avtalene ville tegnet et tall om et
    utvalg og kalt det leverandørforholdet.
    """
    s = conn.execute("SELECT * FROM m24_leverandorstatus(%s)",
                     (tenant,)).fetchone()
    slaoversikt = [
        {"sla_type": r[0], "avtaler": r[1], "malinger": r[2], "brudd": r[3]}
        for r in conn.execute("SELECT * FROM m24_slaoversikt(%s)",
                              (tenant,)).fetchall()]
    avtaler = [
        {"avtale_id": str(r[0]), "leverandor_id": str(r[1]),
         "leverandor_navn": r[2], "leverandor_aktiv": r[3],
         "ytelse": r[4], "sla_type": r[5], "avtalt_verdi": r[6],
         "avtalt_pris_ore": r[7], "gyldig_fra": r[8].isoformat(),
         "gyldig_til": r[9].isoformat(), "status": r[10],
         "malinger": r[11], "brudd": r[12],
         "siste_levert": r[13].isoformat() if r[13] else None,
         "siste_faktisk_verdi": r[14], "siste_faktisk_pris_ore": r[15],
         "prisavvik_promille": r[16], "dogn_til_utlop": r[17],
         "apne_funn": list(r[18] or ())}
        for r in conn.execute("SELECT * FROM m24_avtalene(%s,%s)",
                              (tenant, MAKS_AVTALER)).fetchall()]
    leverandorer = [
        {"leverandor_id": str(r[0]), "navn": r[1], "ekstern_ref": r[2],
         "aktiv": r[3], "aktive_avtaler": r[4]}
        for r in conn.execute("SELECT * FROM m24_leverandorene(%s)",
                              (tenant,)).fetchall()]
    t = conn.execute("SELECT * FROM m24_tersklene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "aktive_avtaler": s[0], "leverandorer": s[1],
            "apne_funn": s[2], "avtaler_med_brudd": s[3],
            "avtalt_ore": s[4], "har_terskel": s[5],
            "terskelversjon": s[6],
            # LISTEN ER AVKORTET, OG FLATEN SKAL KUNNE SI DET.
            "vist": len(avtaler)},
        "slaoversikt": slaoversikt,
        "avtaler": avtaler,
        "leverandorer": leverandorer,
        # TERSKLENE ER `None` NÅR DE IKKE ER SATT, ikke et sett
        # standardverdier. En flate som viste modulens standardtall som
        # om de var tenantens ville løyet om hvem som bestemte dem.
        "terskler": None if t is None else {
            "prisstigning_promille": t[0], "sla_brudd_grense": t[1],
            "avtale_varsel_dogn": t[2], "maling_stillhet_dogn": t[3],
            "versjon": t[4], "oppdatert": t[5].isoformat(),
            "oppdatert_av": t[6]}}


def leverandorbilde(tjeneste, request):
    """GET /v1/leverandor (okonomi:read) — tenantens egne avtaler."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def leveransene_endepunkt(tjeneste, request):
    """GET /v1/leverandor/{avtale_id}/leveranser (okonomi:read).

    Målingene er append-only i basen, og dette er veien til å lese dem:
    hver leveranse med sin faktiske verdi, sin faktiske pris og sin
    BRUDD-dom. Dommen regnes i basen av `m24_bryter_sla`, ikke her — en
    flate som regnet den selv ville hatt en andre retningstabell å holde
    i takt, og et brudd regnet med feil fortegn er STILLE.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        aid = _sti_uuid(request, "avtale_id", rid)
        rader = conn.execute("SELECT * FROM m24_leveransene(%s,%s)",
                             (auth.tenant, aid)).fetchall()
        return kanonisk_json({
            "avtale_id": str(aid),
            "leveranser": [
                {"leveranse_id": str(r[0]), "levert": r[1].isoformat(),
                 "faktisk_verdi": r[2], "faktisk_pris_ore": r[3],
                 "referanse": r[4], "brudd": r[5],
                 "registrert": r[6].isoformat(), "registrert_av": r[7]}
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
            # Datoen er kallerens tekst, og castet skjer i basen. En
            # ulesbar dato er 400, ikke 409. Fanget som to navngitte
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
        return _ok({**svar, felt: ut}, rid)

    return _med_conn(tjeneste, rid, kjor)


def terskler_endepunkt(tjeneste, request):
    """POST /v1/leverandor/terskler (bestilling:opprett, idem).

    TERSKLENE ER TENANTENS EGNE, ikke konstanter i koden. «Ti prosent
    prisøkning er for mye» er en forretningsbeslutning, og en terskel
    kodet inn ville vært en fullmakt modulen ga seg selv — samme dom som
    M-23s purretrinn.

    ÆRLIG OM HVA DETTE IKKE ER: tersklene går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert (utkast → attestering →
    aktivering) og har ingen fasilitet for en tenant-innstilling.
    Invarianten `terskel_hardkodet` er oppfylt i den forstand som betyr
    noe — tenanten eier og fører verdiene, og de er revisjonssporet — men
    koblingen til M-1 står igjen som et NAVNGITT gap.

    HELE SETTET I ETT KALL, og versjonen øker. Et funn bærer versjonen
    det ble vurdert mot, så en endret terskel ikke omskriver historien.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        verdier = [_heltall(kropp, felt, rid, *TERSKELGRENSER[felt])
                   for felt in ("prisstigning_promille",
                                "sla_brudd_grense", "avtale_varsel_dogn",
                                "maling_stillhet_dogn")]
        return ("SELECT m24_sett_terskler(%s,%s,%s,%s,%s,%s)",
                (tenant, *verdier, bid), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_part_endepunkt(tjeneste, request):
    """POST /v1/leverandor/part (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        ref = _valgfri_tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        lid = _utled("leverandor", tenant, nokkel)
        return ("SELECT m24_registrer_leverandor(%s,%s,%s,%s,%s)",
                (tenant, lid, navn, ref, bid),
                {"leverandor_id": str(lid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def registrer_avtale_endepunkt(tjeneste, request):
    """POST /v1/leverandor/avtale (bestilling:opprett, idem).

    HER SETTES DOMMEN alle senere målinger vurderes mot. `avtalt_verdi`
    og `avtalt_pris_ore` er obligatoriske av samme grunn: en avtale uten
    avtalt verdi er ingen avtale, og en måling mot den ville vært et tall
    uten dom.

    `sla_type` valideres mot speilet HER så feilen blir 400 med en gang.
    Døren slår dessuten opp retningen FØR raden finnes — en avtale med en
    type ingen kan vurdere ville stått i registeret og aldri gitt et
    funn, altså sett ut som en avtale som holdes.
    """
    from .policyadmin_http import _Avbrudd, _feil

    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        lid = _kropp_uuid(kropp, "leverandor_id", rid)
        ytelse = _tekst(kropp, "ytelse", rid, MAKS_YTELSE)
        sla_type = kropp.get("sla_type")
        if sla_type not in SLA_TYPER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        verdi = _heltall(kropp, "avtalt_verdi", rid, 0, MAKS_SLA_VERDI)
        pris = _ore(kropp, "avtalt_pris_ore", rid)
        fra = _tekst(kropp, "gyldig_fra", rid, 32)
        til = _tekst(kropp, "gyldig_til", rid, 32)
        aid = _utled("avtale", tenant, nokkel)
        return ("SELECT m24_registrer_avtale(%s,%s,%s,%s,%s,%s,%s,"
                "                            %s::date,%s::date,%s)",
                (tenant, aid, lid, ytelse, sla_type, verdi, pris, fra,
                 til, bid),
                {"avtale_id": str(aid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def registrer_leveranse_endepunkt(tjeneste, request):
    """POST /v1/leverandor/{avtale_id}/leveranse (bestilling:opprett,
    idem).

    EN MÅLING ER MOT EN AVTALT VERDI, og datoen må ligge INNENFOR
    avtalens gyldighet. Vakten i 105 håndhever begge — en regel som bare
    fantes her ville vært borte i det noen skrev direkte.

    MÅLINGEN ER APPEND-ONLY. En feilført leveranse rettes med en ny
    måling, ikke ved å skrive om den gamle: en SLA-historikk som kunne
    redigeres ville vært en påstand, ikke en måling.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        aid = _sti_uuid(request, "avtale_id", rid)
        levert = _tekst(kropp, "levert", rid, 32)
        verdi = _heltall(kropp, "faktisk_verdi", rid, 0, MAKS_SLA_VERDI)
        pris = _ore(kropp, "faktisk_pris_ore", rid)
        ref = _valgfri_tekst(kropp, "referanse", rid, MAKS_REF)
        vid = _utled("leveranse", tenant, nokkel)
        return ("SELECT m24_registrer_leveranse(%s,%s,%s,%s::date,%s,%s,"
                "                               %s,%s)",
                (tenant, vid, aid, levert, verdi, pris, ref, bid),
                {"avtale_id": str(aid), "leveranse_id": str(vid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def avslutt_avtale_endepunkt(tjeneste, request):
    """POST /v1/leverandor/{avtale_id}/avslutt (bestilling:opprett, idem).

    Å avslutte en avtale uten å si hvorfor er den ene handlingen ingen
    kan etterprøve senere. Begrunnelsen kreves av døren og av CHECK-en.

    AVSLUTNINGEN LUKKER FUNNENE i samme transaksjon. Et åpent funn om en
    avtale som ikke lenger finnes er et varsel ingen kan gjøre noe med.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        aid = _sti_uuid(request, "avtale_id", rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        return ("SELECT m24_avslutt_avtale(%s,%s,%s,%s)",
                (tenant, aid, begrunnelse, bid),
                {"avtale_id": str(aid)}, "ny")
    return _skriv(tjeneste, request, bygg)
