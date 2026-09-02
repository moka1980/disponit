"""M-14 fakturakontrollagentens API (migrasjon 106).

Sju endepunkter: to leseveier og fem skriveveier, alle mot dører. Ingen
av dem rører en tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_faktura_eier`-eid SECURITY DEFINER-dør i 106, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN BOKFØRER INGENTING OG ATTESTERER INGENTING. Det er v1-dommen, og
den er klyngens nye: `bransjemal-tjenestebedrift.yaml` navngir modulen
som verifikatoren `v_regnskap`, betrodd for `dublettsjekk`,
`mva_validert` og `faktura_godkjent` — og bruker de tre til å slippe
`faktura.bokfor` gjennom som `modus: auto`. En attestasjon er nettopp
det som slipper den automatiske bokføringen gjennom, og å ta den
fullmakten før treffraten under den er målt, er å la modulen definere
sin egen troverdighet.

Her finnes derfor ingen import av `policy_validator.attestering`, ingen
signeringsnøkkel, ingen hovedbokskobling og ingen kontoplan. Statusen
`kontrollert` sier at NOEN HAR SETT PÅ fakturaen; den sier ingenting om
at penger har flyttet seg.

BELØP ER HELTALL HELE VEIEN, og MVA-KONTROLLEN REGNES I BASEN med en
skrevet avrundingsregel — `(netto * promille + 500) / 1000`, halv-opp.
API-et regner den ikke: en andre avrundingsregel å holde i takt er
nøyaktig slik en mva-kontroll blir stille gal.

MVA-SATSENE ER TENANTENS, OG DE ER DATERTE. Satsen leses etter
FAKTURAENS dato, ikke dagens — en satsendring skal ikke gjøre gamle
fakturaer gale med tilbakevirkende kraft.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — scopet M-13 (101) innførte og M-23
    (104) og M-24 (105) gjenbrukte. Hva noen krever av oss er
    virksomhetens pengestrøm. Gjenbrukt, ikke nytt.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som
    096/100/101/102/103/104/105.

SP-2 PÅ SKRIVEVEIENE SOM FØDER EN RAD: `faktura_id` og `kontroll_id`
utledes deterministisk av Idempotency-Key-en. En dobbelt registrert
faktura er nøyaktig det modulen finnes for å hindre.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

#: Taket for hvor mange fakturaer flaten viser. Dørens tak er 1000.
#: SAMMENDRAGET OG TREFFRATEN TELLER LIKEVEL ALT.
MAKS_FAKTURAER = 200

MAKS_REF = 300
MAKS_NUMMER = 100
MAKS_SATSKODE = 60
MAKS_NOTAT = 2000

#: Ytterpunktet for et beløp i øre. 10^13 øre er hundre milliarder kroner
#: (101s form, gjenbrukt i 104 og 105).
MAKS_ORE = 10 ** 13

#: SPEIL av CHECK-en i 106. Speilet finnes for at feilen skal bli 400 og
#: ikke 409: en ukjent verdi er en feilformet forespørsel, ikke en
#: tilstand som sier nei. Dørens CHECK er fortsatt den bindende.
#:
#: LEGG MERKE TIL HVA SOM IKKE STÅR HER: `bokfort`. Det er ikke en
#: utelatelse — det er dommen.
AVGJORELSER = ("kontrollert", "avvist")
KONTROLLUTFALL = ("ok", "avvik")

#: TERSKLENES YTTERPUNKTER, ikke verdier. Et forsvar mot et tall som ikke
#: kan ha vært ment, ikke en mening om hva som er «for stort». Speiler
#: CHECK-ene i 106.
TERSKELGRENSER = {
    "mva_slingring_ore": (0, 1000),
    "belopsgrense_ore": (0, 10000000000000),
    "kontrollfrist_dogn": (0, 3650),
    "dublettvindu_dogn": (0, 365),
}

_M14_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m14:faktura")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M14_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _heltall(kropp, felt: str, rid, minst: int, mest: int) -> int:
    """Et heltall, og DEN ENESTE veien det kommer inn.

    `isinstance(x, bool)` er ikke pedanteri: i Python er `True` en `int`,
    og uten sjekken ville `{"mva_ore": true}` blitt 1 øre mva.
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
        # DEN EKSAKTE DUBLETTEN lander her, via `faktura_en_per_nummer`.
        # Det er en TILSTAND som sier nei, ikke en idempotenskonflikt:
        # kalleren ba om en NY faktura, og den finnes fra før. Det er
        # dessuten selve kontrollen modulen er navngitt for.
        if "en_per_nummer" in str(e):
            return _Avbrudd(_feil("faktura_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: en avgjørelse uten begrunnelse, et
        # ukjent utfall, en manuell kontroll uten notat.
        return _Avbrudd(_feil("faktura_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # `netto + mva <> brutto`, en overlappende satsperiode, en
        # gjenåpnet faktura. Alle er TILSTANDER som sier nei.
        return _Avbrudd(_feil("faktura_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Fakturaflatens tilstand i én transaksjon, gjennom fem lesedører.

    SAMMENDRAGET OG TREFFRATEN KOMMER FRA SINE EGNE DØRER og telles over
    ALT — ikke fra den avkortede listen. En flate som regnet treffraten
    fra de 200 viste fakturaene ville tegnet et tall om et utvalg og
    kalt det kontrollens kvalitet.
    """
    s = conn.execute("SELECT * FROM m14_fakturastatus(%s)",
                     (tenant,)).fetchone()
    treffrate = [
        {"kontrolltype": r[0], "kjort": r[1], "avvik": r[2]}
        for r in conn.execute("SELECT * FROM m14_treffrate(%s)",
                              (tenant,)).fetchall()]
    fakturaer = [
        {"faktura_id": str(r[0]), "leverandor_ref": r[1],
         "fakturanummer": r[2], "netto_ore": r[3], "mva_ore": r[4],
         "brutto_ore": r[5], "sats_kode": r[6], "valuta": r[7],
         "utstedt": r[8].isoformat(), "forfall": r[9].isoformat(),
         "mottatt": r[10].isoformat(), "status": r[11],
         "dogn_siden_mottatt": r[12], "kontroller": r[13],
         "avvik": r[14], "apne_funn": list(r[15] or ())}
        for r in conn.execute("SELECT * FROM m14_fakturaene(%s,%s)",
                              (tenant, MAKS_FAKTURAER)).fetchall()]
    satser = [
        {"sats_kode": r[0], "promille": r[1],
         "gyldig_fra": r[2].isoformat(),
         "gyldig_til": r[3].isoformat() if r[3] else None,
         "gjelder_i_dag": r[4]}
        for r in conn.execute("SELECT * FROM m14_satsene(%s)",
                              (tenant,)).fetchall()]
    t = conn.execute("SELECT * FROM m14_tersklene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "mottatte": s[0], "mottatt_ore": s[1], "kontrollerte": s[2],
            "avviste": s[3], "apne_funn": s[4], "ukontrollerte": s[5],
            "har_terskel": s[6], "terskelversjon": s[7], "satser": s[8],
            # LISTEN ER AVKORTET, OG FLATEN SKAL KUNNE SI DET.
            "vist": len(fakturaer)},
        "treffrate": treffrate,
        "fakturaer": fakturaer,
        "satser": satser,
        # TERSKLENE ER `None` NÅR DE IKKE ER SATT, ikke et sett
        # standardverdier. En flate som viste modulens standardtall som
        # om de var tenantens ville løyet om hvem som bestemte dem.
        "terskler": None if t is None else {
            "mva_slingring_ore": t[0], "belopsgrense_ore": t[1],
            "kontrollfrist_dogn": t[2], "dublettvindu_dogn": t[3],
            "versjon": t[4], "oppdatert": t[5].isoformat(),
            "oppdatert_av": t[6]}}


def fakturabilde(tjeneste, request):
    """GET /v1/faktura (okonomi:read) — tenantens inngående fakturaer."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def kontrollene_endepunkt(tjeneste, request):
    """GET /v1/faktura/{faktura_id}/kontroller (okonomi:read).

    Kontrollene er append-only i basen, og dette er veien til å lese dem:
    hva hver kontroll så, med hvem og når. Det er svaret på «hvorfor står
    denne som avvik», og uten det er utfallet et ord uten opphav.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        fid = _sti_uuid(request, "faktura_id", rid)
        rader = conn.execute("SELECT * FROM m14_kontrollene(%s,%s)",
                             (auth.tenant, fid)).fetchall()
        return kanonisk_json({
            "faktura_id": str(fid),
            "kontroller": [
                {"kontroll_id": str(r[0]), "kontrolltype": r[1],
                 "utfall": r[2], "avvik_ore": r[3], "notat": r[4],
                 "kjort": r[5].isoformat(), "kjort_av": r[6]}
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
    """POST /v1/faktura/terskler (bestilling:opprett, idem).

    KONTROLLGRENSENE ER TENANTENS. Hvor mye mva-beløpet kan avvike, hvor
    stort et beløp må være før et menneske skal ha sett på det, og hvor
    lenge en faktura kan stå ukontrollert, er forretningsbeslutninger.

    ÆRLIG OM HVA DETTE IKKE ER: grensene går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert (utkast → attestering →
    aktivering) og har ingen fasilitet for en tenant-innstilling.
    Invarianten `mvasats_hardkodet` er oppfylt i den forstand som betyr
    noe — tenanten eier og fører verdiene, og de er revisjonssporet —
    men koblingen til M-1 står igjen som et NAVNGITT gap.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        verdier = [_heltall(kropp, felt, rid, *TERSKELGRENSER[felt])
                   for felt in ("mva_slingring_ore", "belopsgrense_ore",
                                "kontrollfrist_dogn",
                                "dublettvindu_dogn")]
        return ("SELECT m14_sett_terskler(%s,%s,%s,%s,%s,%s)",
                (tenant, *verdier, bid), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def mvasats_endepunkt(tjeneste, request):
    """POST /v1/faktura/mvasats (bestilling:opprett, idem).

    SATSEN ER TENANTENS OG DEN ER DATERT. `gyldig_fra` er obligatorisk,
    `gyldig_til` er valgfri — den gjeldende satsen har ingen sluttdato.
    En sats som ikke var datert ville gjort hver gammel faktura gal i det
    øyeblikket staten endret satsen.

    PROMILLE, IKKE PROSENT: 25 % er 250. En sats på 12,5 % finnes, og
    125 er eksakt der 12.5 ikke er.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        kode = _tekst(kropp, "sats_kode", rid, MAKS_SATSKODE)
        promille = _heltall(kropp, "promille", rid, 0, 1000)
        fra = _tekst(kropp, "gyldig_fra", rid, 32)
        til = kropp.get("gyldig_til")
        if til is not None and (not isinstance(til, str) or len(til) > 32):
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m14_sett_mvasats(%s,%s,%s,%s::date,%s::date,%s)",
                (tenant, kode, promille, fra, til, bid),
                {"sats_kode": kode}, "ny")
    return _skriv(tjeneste, request, bygg)


def registrer_endepunkt(tjeneste, request):
    """POST /v1/faktura (bestilling:opprett, idem).

    DE TRE MASKINELLE KONTROLLENE KJØRER I SAMME TRANSAKSJON som
    registreringen. En faktura som lå ukontrollert i et vindu mellom to
    kall er en faktura noen kunne betalt i mellomtiden.

    DEN EKSAKTE DUBLETTEN AVVISES med 409: samme leverandør og samme
    fakturanummer er ÉN faktura, og den skal ikke kunne betales to
    ganger fordi noen importerte den fra to kanaler. Den NÆRE dubletten
    — samme beløp, samme dato, ulikt nummer — blir et FUNN, fordi den er
    en menneskelig vurdering og ikke en regel basen kan felle.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "leverandor_ref", rid, MAKS_REF)
        nummer = _tekst(kropp, "fakturanummer", rid, MAKS_NUMMER)
        netto = _ore(kropp, "netto_ore", rid)
        mva = _ore(kropp, "mva_ore", rid)
        brutto = _ore(kropp, "brutto_ore", rid)
        kode = _tekst(kropp, "sats_kode", rid, MAKS_SATSKODE)
        valuta = kropp.get("valuta", "NOK")
        if not isinstance(valuta, str) or len(valuta) != 3:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        utstedt = _tekst(kropp, "utstedt", rid, 32)
        forfall = _tekst(kropp, "forfall", rid, 32)
        mottatt = _tekst(kropp, "mottatt", rid, 32)
        fid = _utled("faktura", tenant, nokkel)
        return ("SELECT m14_registrer_faktura(%s,%s,%s,%s,%s,%s,%s,%s,"
                "       %s,%s::date,%s::date,%s::date,%s)",
                (tenant, fid, ref, nummer, netto, mva, brutto, kode,
                 valuta, utstedt, forfall, mottatt, bid),
                {"faktura_id": str(fid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def kontroll_endepunkt(tjeneste, request):
    """POST /v1/faktura/{faktura_id}/kontroll (bestilling:opprett, idem).

    DEN MENNESKELIGE KONTROLLEN. De tre maskinelle måler det som kan
    måles; denne er vurderingen — og den koster et notat, fordi en
    kontroll uten et ord om hva som ble sett er en kontroll ingen kan
    etterprøve.

    DEN ATTESTERER IKKE. `faktura_godkjent` i policyen hviler til slutt
    på en slik vurdering, men v1 registrerer den — den signerer den ikke.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        fid = _sti_uuid(request, "faktura_id", rid)
        utfall = kropp.get("utfall")
        if utfall not in KONTROLLUTFALL:
            raise _Avbrudd(_feil("request_feilformet", rid))
        notat = _tekst(kropp, "notat", rid, MAKS_NOTAT)
        kid = _utled("kontroll", tenant, nokkel)
        return ("SELECT m14_registrer_kontroll(%s,%s,%s,%s,%s,%s)",
                (tenant, kid, fid, utfall, notat, bid),
                {"faktura_id": str(fid), "kontroll_id": str(kid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def avgjor_endepunkt(tjeneste, request):
    """POST /v1/faktura/{faktura_id}/avgjor (bestilling:opprett, idem).

    `kontrollert` ELLER `avvist`, og INGENTING ANNET. Ordet `bokfort`
    finnes ikke i settet, og fraværet er hele v1-snittet: statusen sier
    at noen har sett på fakturaen, ikke at penger har flyttet seg.

    AVGJØRELSEN LUKKER FUNNENE i samme transaksjon. Et åpent funn om en
    faktura som er avgjort er et varsel ingen kan gjøre noe med.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        fid = _sti_uuid(request, "faktura_id", rid)
        status = kropp.get("status")
        if status not in AVGJORELSER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_NOTAT)
        return ("SELECT m14_avgjor_faktura(%s,%s,%s,%s,%s)",
                (tenant, fid, status, begrunnelse, bid),
                {"faktura_id": str(fid)}, "ny")
    return _skriv(tjeneste, request, bygg)
