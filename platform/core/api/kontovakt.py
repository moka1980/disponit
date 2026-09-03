"""M-42 kontoverifikasjon og transaksjonsvaktens API (migrasjon 110).

Sju endepunkter: to leseveier og fem skriveveier, alle mot dører. Ingen
av dem rører en tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_kontovakt_eier`-eid SECURITY DEFINER-dør i 110, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN STOPPER INGEN BETALING, VERIFISERER INGENTING MOT EN EKSTERN
KANAL, OG ATTESTERER INGENTING. To av tre bransjemaler navngir modulen
som verifikatoren `v_kontovakt`, betrodd for `konto_verifisert`,
`konto_verifisert_uavhengig` og `svindelsjekk_bestatt`.

DET FARLIGSTE EN BETALINGSVAKT KAN GJØRE ER IKKE Å SLIPPE NOE GJENNOM —
DET ER Å STOPPE NOE. En vakt som blokkerer feil er sin egen skade: en
leverandør som ikke får betalt, en lønn som uteblir, en frist som ryker.
Og en vakt ingen har målt vet ikke hvor ofte den tar feil. Derfor
skriver v1 ned, og bare det.

KONTONUMMERET LAGRES ALDRI. Det går inn som en parameter til døren, som
normaliserer det, regner masken og den saltede hashen — og kaster
nummeret. API-et logger det ikke, svarer aldri med det, og har ingen vei
tilbake til det. Svaret på en registrering er MASKEN.

EN KONTOENDRING BLIR ET FUNN I SAMME TRANSAKSJON, skrevet av døren. Den
venter ikke på nattens sveip: en endret utbetalingskonto er det høyeste
svindelsignalet vi har, og et døgns forsinkelse er et døgn der pengene
kan gå.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som M-13 (101) innførte
    og M-23/M-24/M-14/M-25/M-26/M-27 gjenbrukte. Den som handler på «en
    leverandør har byttet konto» er den som betaler.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som
    096/100–109.

SP-2 PÅ ALLE TRE REGISTRERINGSDØRENE: `mottaker_id`, `oppgave_id` og
`verifikasjon_id` utledes deterministisk av Idempotency-Key-en. For
kontooppgaven er det strengt nødvendig: en gjentatt POST må ikke bli to
linjer i en historikk som ER beviset.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_MOTTAKERE = 200
MAKS_HISTORIKK = 200
MAKS_REF = 100
MAKS_NAVN = 300
MAKS_KONTO = 64
MAKS_NOTAT = 2000

#: LOVLIGE KANALER. «Hvordan kom denne kontoen inn» er det første
#: spørsmålet i enhver etterforskning av fakturasvindel, og et fritt
#: tekstfelt ville gjort spørsmålet til en tekstsøk-oppgave.
KANALER = ("faktura", "epost", "telefon", "portal", "brev", "annet")

#: LOVLIGE VERIFIKASJONSMETODER. Rekkefølgen er ikke tilfeldig: å ringe
#: et nummer man hadde FRA FØR er den eneste metoden som ikke kan
#: forfalskes av den som sendte fakturaen.
METODER = ("ringte_kjent_nummer", "fysisk_mote", "signert_dokument",
           "bankbekreftelse", "annet")

#: TERSKLENES YTTERPUNKTER, ikke verdier. Speiler CHECK-ene i 110.
TERSKELGRENSER = {
    "reverifikasjon_dogn": (0, 3650),
    "uverifisert_dogn": (0, 3650),
}

_M42_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m42:kontovakt")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M42_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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
    """Ett av et LUKKET sett. Fritekst her ville gjort funnene uleselige."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi not in lovlige:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _heltall(kropp, felt: str, rid, minst: int, mest: int) -> int:
    """Et heltall, og DEN ENESTE veien det kommer inn.

    `isinstance(x, bool)` er ikke pedanteri: i Python er `True` en `int`,
    og uten sjekken ville `{"uverifisert_dogn": true}` blitt ett døgn.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


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
        if "ref_unik" in str(e):
            return _Avbrudd(_feil("kontovakt_ulovlig_tilstand", rid, 409))
        # PK-kollisjon på en SP-2-utledet id: SAMME nøkkel, samme rad.
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: et for kort kontonummer, en konto på en
        # deaktivert mottaker.
        return _Avbrudd(_feil("kontovakt_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # Vaktens dommer: en frosset oppgave som skulle endres, og — den
        # som betyr mest — den som oppga kontoen som prøver å verifisere
        # den selv.
        return _Avbrudd(_feil("kontovakt_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Kontovaktflatens tilstand i én transaksjon, gjennom tre lesedører."""
    s = conn.execute("SELECT * FROM m42_kontostatus(%s)",
                     (tenant,)).fetchone()
    mottakere = [
        {"mottaker_id": str(r[0]), "ekstern_ref": r[1], "navn": r[2],
         "aktiv": r[3], "kontonummer_maske": r[4], "oppgitt_av": r[5],
         "oppgitt_kanal": r[6],
         "oppgitt_dato": r[7].isoformat() if r[7] else None,
         "verifisert_av": r[8], "metode": r[9],
         "verifisert_dato": r[10].isoformat() if r[10] else None,
         "oppgaver": r[11], "apne_funn": list(r[12] or ())}
        for r in conn.execute("SELECT * FROM m42_mottakerne(%s,%s)",
                              (tenant, MAKS_MOTTAKERE)).fetchall()]
    t = conn.execute("SELECT * FROM m42_tersklene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "mottakere": s[0], "aktive": s[1], "med_konto": s[2],
            "verifiserte": s[3], "apne_funn": s[4],
            "apne_endringer": s[5], "har_terskel": s[6],
            "terskelversjon": s[7], "vist": len(mottakere)},
        "mottakere": mottakere,
        "terskler": None if t is None else {
            "reverifikasjon_dogn": t[0], "uverifisert_dogn": t[1],
            "versjon": t[2], "oppdatert": t[3].isoformat(),
            "oppdatert_av": t[4]}}


def kontovaktbilde(tjeneste, request):
    """GET /v1/kontovakt (okonomi:read) — tenantens eget kontoregister."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def historikk_endepunkt(tjeneste, request):
    """GET /v1/kontovakt/{mottaker_id}/historikk (okonomi:read).

    HELE HISTORIKKEN: hvem oppga hvilken konto, når, gjennom hvilken
    kanal — og hvem som eventuelt verifiserte den, med hvilken metode.

    DETTE ER BEVISET. Svindelen avsløres av historikken, ikke av
    gjeldende verdi, og `endret` sier hvilken linje som var et BYTTE.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        mid = _sti_uuid(request, "mottaker_id", rid)
        rader = conn.execute(
            "SELECT * FROM m42_kontohistorikken(%s,%s,%s)",
            (auth.tenant, mid, MAKS_HISTORIKK)).fetchall()
        return kanonisk_json({
            "mottaker_id": str(mid),
            "oppgaver": [
                {"oppgave_id": str(r[0]), "kontonummer_maske": r[1],
                 "oppgitt_av": r[2], "oppgitt_kanal": r[3],
                 "oppgitt_dato": r[4].isoformat(), "notat": r[5],
                 "registrert": r[6].isoformat(), "registrert_av": r[7],
                 "verifisert_av": r[8], "metode": r[9],
                 "verifisert_dato":
                     r[10].isoformat() if r[10] else None,
                 "verifikasjonsnotat": r[11], "endret": r[12]}
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
            # Verifikasjonsdøren svarer med MOTTAKEREN oppgaven hører
            # til — som en `uuid.UUID`, og den er ikke JSON i seg selv.
            # Konverteringen står her og ikke i døren fordi en dør som
            # returnerte TEKST der den mener en id, ville vært en dør som
            # løy om typen sin.
            if isinstance(ut, uuidlib.UUID):
                ut = str(ut)
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
        # `m42_registrer_mottaker` er en VOID-dør — den har ingenting å
        # melde utover id-en, som alt står i `svar` (CodeRabbit).
        if ut is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def terskler_endepunkt(tjeneste, request):
    """POST /v1/kontovakt/terskler (bestilling:opprett, idem).

    GRENSENE ER TENANTENS. «En verifikasjon holder i ett år» er en
    forretningsbeslutning, ikke en teknisk detalj — og en konstant i
    koden ville vært nøyaktig den fullmakten invarianten
    `verifikasjonskrav_hardkodet` forbyr.

    ÆRLIG OM HVA DETTE IKKE ER: grensene går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert og har ingen fasilitet for en
    tenant-innstilling. Invarianten er oppfylt i den forstand som betyr
    noe — tenanten eier og fører verdiene — men koblingen til M-1 står
    igjen som et NAVNGITT gap.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        verdier = [_heltall(kropp, felt, rid, *TERSKELGRENSER[felt])
                   for felt in ("reverifikasjon_dogn",
                                "uverifisert_dogn")]
        return ("SELECT m42_sett_terskler(%s,%s,%s,%s)",
                (tenant, *verdier, bid), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_mottaker_endepunkt(tjeneste, request):
    """POST /v1/kontovakt/mottaker (bestilling:opprett, idem).

    `ekstern_ref` er tenantens egen referanse til parten — FRI TEKST og
    ingen fremmednøkkel mot M-24. En hard kobling ville gjort
    kontohistorikken avhengig av at leverandørregisteret er ført, og
    historikken skal kunne stå alene: det er den som er beviset.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        mid = _utled("mottaker", tenant, nokkel)
        return ("SELECT m42_registrer_mottaker(%s,%s,%s,%s,%s)",
                (tenant, mid, ref, navn, bid),
                {"mottaker_id": str(mid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def oppgi_konto_endepunkt(tjeneste, request):
    """POST /v1/kontovakt/{mottaker_id}/konto (bestilling:opprett, idem).

    KONTONUMMERET LAGRES ALDRI. Det går inn her, døren normaliserer det,
    regner masken og den saltede hashen, og kaster nummeret. SVARET ER
    MASKEN — API-et har ingen vei tilbake til nummeret, og logger det
    ikke.

    KANALEN ER OBLIGATORISK og fra et lukket sett: «hvordan kom denne
    kontoen inn» er det første spørsmålet i enhver etterforskning av
    fakturasvindel.

    ER KONTOEN EN ANNEN ENN FORRIGE GANG, SKRIVES FUNNET I SAMME
    TRANSAKSJON. Det venter ikke på nattens sveip.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        mid = _sti_uuid(request, "mottaker_id", rid)
        konto = _tekst(kropp, "kontonummer", rid, MAKS_KONTO)
        oppgitt_av = _tekst(kropp, "oppgitt_av", rid, MAKS_NAVN)
        kanal = _valg(kropp, "oppgitt_kanal", rid, KANALER)
        dato = _tekst(kropp, "oppgitt_dato", rid, 32)
        notat = _tekst(kropp, "notat", rid, MAKS_NOTAT)
        oid = _utled("oppgave", tenant, nokkel)
        return ("SELECT m42_oppgi_konto(%s,%s,%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, oid, mid, konto, oppgitt_av, kanal, dato,
                 notat, bid),
                {"mottaker_id": str(mid), "oppgave_id": str(oid)},
                "kontonummer_maske")
    return _skriv(tjeneste, request, bygg)


def verifiser_endepunkt(tjeneste, request):
    """POST /v1/kontovakt/oppgave/{oppgave_id}/verifikasjon
    (bestilling:opprett, idem).

    DØREN VERIFISERER INGENTING. Den SKRIVER NED at et menneske gjorde
    det, med hvilken metode, og hva de faktisk gjorde. Det finnes ingen
    oppslag mot en bank, ingen ekstern kanal, ingen automatikk — og
    fraværet er dommen: en vakt som verifiserte selv, ville vært en vakt
    ingen har målt.

    DEN SOM OPPGA KONTOEN KAN IKKE VERIFISERE DEN. Er de samme, er
    ingenting verifisert — og `konto_verifisert_uavhengig` er nøyaktig
    navnet på det vilkåret. Håndhevet i basen, ikke her.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        oid = _sti_uuid(request, "oppgave_id", rid)
        metode = _valg(kropp, "metode", rid, METODER)
        av = _tekst(kropp, "verifisert_av", rid, MAKS_NAVN)
        notat = _tekst(kropp, "notat", rid, MAKS_NOTAT)
        dato = _tekst(kropp, "verifisert_dato", rid, 32)
        vid = _utled("verifikasjon", tenant, nokkel)
        return ("SELECT m42_verifiser_konto(%s,%s,%s,%s,%s,%s,%s::date,%s)",
                (tenant, vid, oid, metode, av, notat, dato, bid),
                {"oppgave_id": str(oid),
                 "verifikasjon_id": str(vid)}, "mottaker_id")
    return _skriv(tjeneste, request, bygg)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/kontovakt/{mottaker_id}/aktiv (bestilling:opprett, idem).

    EN MOTTAKER DEAKTIVERES, DEN SLETTES ALDRI: en slettet mottaker ville
    tatt kontohistorikken med seg, og den er hele beviset.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        mid = _sti_uuid(request, "mottaker_id", rid)
        # `aktiv` ER PÅKREVD HER. `_bool` faller tilbake til `false`, og
        # en kropp uten feltet ville derfor DEAKTIVERT mottakeren — altså
        # en utelatelse som utfører en handling (CodeRabbit, 108).
        if "aktiv" not in kropp:
            raise _Avbrudd(_feil("request_feilformet", rid))
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m42_sett_mottakeraktiv(%s,%s,%s,%s)",
                (tenant, mid, aktiv, bid),
                {"mottaker_id": str(mid)}, "endret")
    return _skriv(tjeneste, request, bygg)
