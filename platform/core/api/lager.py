"""M-27 lager- og logistikkagentens API (migrasjon 109).

Ni endepunkter: tre leseveier og seks skriveveier, alle mot dører. Ingen
av dem rører en tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_beholdning_eier`-eid SECURITY DEFINER-dør i 109, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN BESTILLER INGENTING, BEREGNER INGEN PROGNOSE OG ATTESTERER
INGENTING. To av tre bransjemaler navngir modulen som verifikatoren
`v_lager`, betrodd for `lager_reservert`, `retur_registrert` og
`prognose_konfidens` — og bruker dem til å slippe
`lager.bestill_pafyll` og `materiell.bestill` gjennom som `modus: auto`.

ET PASSERT BESTILLINGSPUNKT ER ET FUNN, IKKE EN BESTILLING. En modul som
bestilte påfyll ville bundet virksomheten økonomisk på et grunnlag ingen
har målt — og gjort det mot en leverandøravtale den ikke eier (M-24).

OG DEN BEREGNER INGEN PROGNOSE. `prognose_konfidens` er en påstand om
hvor sikkert et framtidig forbruk er anslått. Her finnes ikke ett
glidende gjennomsnitt, ingen forbruksrate, ingen ekstrapolering. Det er
en bevisst tom plass: en konfidens uten en målt treffrate bak seg er et
tall som ser ut som kunnskap.

BEHOLDNINGEN ER IKKE ET FELT. Den er summen av bevegelser, og API-et har
ingen vei til å SETTE den — heller ikke en telling gjør det. En telling
skriver DIFFERANSEN som en linje, slik at «hvorfor står det 7 her»
alltid har et svar i hovedboken.

ANTALL ER HELTALL i varens minste enhet, og beløp er heltall i øre.
API-et ganger ikke og runder ikke.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som M-13 (101) innførte
    og M-23/M-24/M-14/M-25/M-26 gjenbrukte. En beholdning er bundet
    kapital.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som
    096/100–108.

SP-2 PÅ VAREDØREN OG BEVEGELSESDØRENE: `vare_id` og `bevegelse_id`
utledes deterministisk av Idempotency-Key-en. For bevegelsene er det
STRENGT NØDVENDIG: en gjentatt POST må ikke bli to linjer i hovedboken,
for da er beholdningen feil. Punktdøren er versjonerende og trenger
ingen utledet id — versjonen er nøkkelen, og et gjentatt punkt er en ny
beslutning.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_VARER = 200
MAKS_BEVEGELSER = 200
MAKS_KODE = 100
MAKS_NAVN = 300
MAKS_ENHET = 60
MAKS_NOTAT = 2000
MAKS_BEGRUNNELSE = 2000

#: Ytterpunktet for et beløp i øre (101s form, gjenbrukt i 104–108).
MAKS_ORE = 10 ** 13
#: Ytterpunktet for et ANTALL i varens minste enhet. Et lager på tusen
#: milliarder enheter finnes ikke; en tastefeil gjør det.
MAKS_ANTALL = 10 ** 12

#: LOVLIGE BEVEGELSESTYPER over API-et. `telling` står IKKE her: en
#: telling er ikke en bevegelse noen observerte, den er differansen
#: mellom det talte og det bokførte — og den har sin egen dør.
BEVEGELSESTYPER = ("mottak", "uttak", "retur", "svinn")

#: TERSKLENES YTTERPUNKTER, ikke verdier. Speiler CHECK-ene i 109.
TERSKELGRENSER = {
    "stille_dogn": (0, 3650),
    "uten_punkt_dogn": (0, 3650),
    "telleintervall_dogn": (0, 3650),
}

_M27_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m27:lager")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M27_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    """Tekst, TRIMMET — og lengden måles på det som faktisk lagres.

    Dørene i 109 `btrim`-er selv, så en utrimmet verdi herfra ville blitt
    lagret trimmet likevel: API-et og basen ville vært uenige om hva som
    ble skrevet (CodeRabbit, 108).
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _heltall(kropp, felt: str, rid, minst: int, mest: int) -> int:
    """Et heltall, og DEN ENESTE veien det kommer inn.

    `isinstance(x, bool)` er ikke pedanteri: i Python er `True` en `int`,
    og uten sjekken ville `{"antall": true}` blitt bevegelsen 1 enhet.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not (minst <= verdi <= mest):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _antall(kropp, felt: str, rid, minst: int = 1) -> int:
    return _heltall(kropp, felt, rid, minst, MAKS_ANTALL)


def _ore_valgfritt(kropp, felt: str, rid) -> int | None:
    """Enhetskosten er VALGFRI — men den er aldri et flyttall.

    Et uttak har ingen kostpris, og en tvungen null der ville vært en
    påstand om at varen var gratis.
    """
    if kropp.get(felt) is None:
        return None
    return _heltall(kropp, felt, rid, 0, MAKS_ORE - 1)


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
        if "kode_unik" in str(e) or "ett_apent" in str(e):
            return _Avbrudd(_feil("lager_ulovlig_tilstand", rid, 409))
        # PK-kollisjon på en SP-2-utledet id: SAMME nøkkel, samme rad.
        # En gjentatt POST må ikke bli to linjer i hovedboken.
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: et punkt skrevet bakover, en ukjent
        # bevegelsestype, en bevegelse på en deaktivert vare.
        return _Avbrudd(_feil("lager_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # Vaktens dommer: en NEGATIV BEHOLDNING, en frosset
        # hovedbokslinje, to overlappende punktversjoner.
        return _Avbrudd(_feil("lager_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Lagerflatens tilstand i én transaksjon, gjennom tre lesedører."""
    s = conn.execute("SELECT * FROM m27_lagerstatus(%s)",
                     (tenant,)).fetchone()
    varer = [
        {"vare_id": str(r[0]), "kode": r[1], "navn": r[2], "enhet": r[3],
         "aktiv": r[4], "beholdning": r[5], "punkt_antall": r[6],
         "punktversjon": r[7], "dogn_siden_bevegelse": r[8],
         "dogn_siden_telling": r[9], "apne_funn": list(r[10] or ())}
        for r in conn.execute("SELECT * FROM m27_varene(%s,%s)",
                              (tenant, MAKS_VARER)).fetchall()]
    t = conn.execute("SELECT * FROM m27_tersklene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "varer": s[0], "aktive": s[1], "med_punkt": s[2],
            "under_punkt": s[3], "apne_funn": s[4], "har_terskel": s[5],
            "terskelversjon": s[6], "vist": len(varer)},
        "varer": varer,
        "terskler": None if t is None else {
            "stille_dogn": t[0], "uten_punkt_dogn": t[1],
            "telleintervall_dogn": t[2], "versjon": t[3],
            "oppdatert": t[4].isoformat(), "oppdatert_av": t[5]}}


def lagerbilde(tjeneste, request):
    """GET /v1/lager (okonomi:read) — tenantens egen beholdning."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def bevegelser_endepunkt(tjeneste, request):
    """GET /v1/lager/{vare_id}/bevegelser (okonomi:read).

    HOVEDBOKEN FOR ÉN VARE, med den løpende beholdningen på hver linje.
    Dette er svaret på «hvorfor står det 7 her» — og uten det er
    `lager_reservert` en attestasjon om et tall ingen kan spore.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        vid = _sti_uuid(request, "vare_id", rid)
        rader = conn.execute("SELECT * FROM m27_bevegelsene(%s,%s,%s)",
                             (auth.tenant, vid,
                              MAKS_BEVEGELSER)).fetchall()
        return kanonisk_json({
            "vare_id": str(vid),
            "bevegelser": [
                {"bevegelse_id": str(r[0]), "bevegelsestype": r[1],
                 "endring": r[2], "enhetskost_ore": r[3],
                 "utfort": r[4].isoformat(), "notat": r[5],
                 "registrert": r[6].isoformat(), "registrert_av": r[7],
                 "beholdning_etter": r[8]}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def paa_dato_endepunkt(tjeneste, request):
    """GET /v1/lager/{vare_id}/paa-dato?dato=… (okonomi:read).

    HVA STO PÅ LAGER DEN DAGEN, og hva var bestillingspunktet da. Uten
    begge tallene er et eldre funn ikke etterprøvbart.

    `punkt` er `null` når varen ikke HADDE et punkt den dagen — ikke
    null enheter. «Vi holder ikke lager på denne» og «ingen har satt et
    punkt» er to helt forskjellige svar, og det siste er nettopp det
    `uten_bestillingspunkt` finnes for å avsløre.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        from .policyadmin_http import _Avbrudd, _feil
        vid = _sti_uuid(request, "vare_id", rid)
        dato = request.query_params.get("dato")
        if not isinstance(dato, str) or not (8 <= len(dato) <= 32):
            raise _Avbrudd(_feil("request_feilformet", rid))
        try:
            beholdning = conn.execute(
                "SELECT m27_beholdning_paa_dato(%s,%s,%s::date)",
                (auth.tenant, vid, dato)).fetchone()[0]
            punkt = conn.execute(
                "SELECT * FROM m27_punkt_paa_dato(%s,%s,%s::date)",
                (auth.tenant, vid, dato)).fetchone()
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        return kanonisk_json({
            "vare_id": str(vid), "dato": dato,
            "beholdning": beholdning,
            "punkt": None if punkt is None else {
                "versjon": punkt[0], "punkt_antall": punkt[1],
                "gyldig_fra": punkt[2].isoformat(),
                "gyldig_til": punkt[3].isoformat() if punkt[3] else None,
                "begrunnelse": punkt[4], "opprettet_av": punkt[5]},
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen alle seks skriveveiene deler."""
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
        return _ok({**svar, felt: ut}, rid)

    return _med_conn(tjeneste, rid, kjor)


def terskler_endepunkt(tjeneste, request):
    """POST /v1/lager/terskler (bestilling:opprett, idem).

    GRENSENE ER TENANTENS. «Et halvt år uten bevegelse er dødt lager» er
    en forretningsbeslutning, ikke en teknisk detalj — og en konstant i
    koden ville vært nøyaktig den fullmakten invarianten
    `bestillingspunkt_hardkodet` forbyr.

    ÆRLIG OM HVA DETTE IKKE ER: grensene går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert og har ingen fasilitet for en
    tenant-innstilling. Invarianten er oppfylt i den forstand som betyr
    noe — tenanten eier og fører verdiene — men koblingen til M-1 står
    igjen som et NAVNGITT gap.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        verdier = [_heltall(kropp, felt, rid, *TERSKELGRENSER[felt])
                   for felt in ("stille_dogn", "uten_punkt_dogn",
                                "telleintervall_dogn")]
        return ("SELECT m27_sett_terskler(%s,%s,%s,%s,%s)",
                (tenant, *verdier, bid), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_vare_endepunkt(tjeneste, request):
    """POST /v1/lager/vare (bestilling:opprett, idem).

    ENHETEN ER TENANTENS ORD, men plattformens heltall: antall telles i
    HELE slike enheter, og enheten er frosset etter registrering — en
    endret enhet ville gjort hele bevegelseshistorikken til tall uten
    måleenhet.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        kode = _tekst(kropp, "kode", rid, MAKS_KODE)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        enhet = _tekst(kropp, "enhet", rid, MAKS_ENHET)
        vid = _utled("vare", tenant, nokkel)
        return ("SELECT m27_registrer_vare(%s,%s,%s,%s,%s,%s)",
                (tenant, vid, kode, navn, enhet, bid),
                {"vare_id": str(vid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def sett_punkt_endepunkt(tjeneste, request):
    """POST /v1/lager/{vare_id}/punkt (bestilling:opprett, idem).

    ET NYTT BESTILLINGSPUNKT ER EN NY VERSJON. Døren lukker den forrige i
    samme transaksjon, så «hva var punktet den dagen» alltid har nøyaktig
    ett svar — og et eldre funn forblir etterprøvbart.

    NULL ER LOVLIG: «vi holder ikke lager på denne» er et svar, og det er
    noe annet enn å mangle et punkt.

    BEGRUNNELSEN ER OBLIGATORISK: det er punktet som avgjør når noen blir
    bedt om å bruke penger.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        vid = _sti_uuid(request, "vare_id", rid)
        antall = _antall(kropp, "punkt_antall", rid, minst=0)
        fra = _tekst(kropp, "gyldig_fra", rid, 32)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        return ("SELECT m27_sett_bestillingspunkt(%s,%s,%s,%s::date,%s,%s)",
                (tenant, vid, antall, fra, begrunnelse, bid),
                {"vare_id": str(vid)}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_bevegelse_endepunkt(tjeneste, request):
    """POST /v1/lager/{vare_id}/bevegelse (bestilling:opprett, idem).

    DEN ENESTE VEIEN BEHOLDNINGEN ENDRER SEG. `antall` er en STØRRELSE;
    fortegnet følger av TYPEN, ett sted i basen, slik at ingen kaller kan
    snu det.

    SP-2 ER STRENGT NØDVENDIG HER: en gjentatt POST må ikke bli to
    linjer i hovedboken, for da er beholdningen feil. `bevegelse_id`
    utledes derfor av Idempotency-Key-en, og et gjentatt kall møter
    primærnøkkelen.

    SVARET ER DEN NYE BEHOLDNINGEN — regnet av basen som summen av
    bevegelser, ikke av API-et.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        vid = _sti_uuid(request, "vare_id", rid)
        type_ = kropp.get("bevegelsestype")
        if type_ not in BEVEGELSESTYPER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        antall = _antall(kropp, "antall", rid)
        kost = _ore_valgfritt(kropp, "enhetskost_ore", rid)
        utfort = _tekst(kropp, "utfort", rid, 32)
        notat = _tekst(kropp, "notat", rid, MAKS_NOTAT)
        bid_bevegelse = _utled("bevegelse", tenant, nokkel)
        return ("SELECT m27_registrer_bevegelse("
                "%s,%s,%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, bid_bevegelse, vid, type_, antall, kost,
                 utfort, notat, bid),
                {"vare_id": str(vid),
                 "bevegelse_id": str(bid_bevegelse)}, "beholdning")
    return _skriv(tjeneste, request, bygg)


def registrer_telling_endepunkt(tjeneste, request):
    """POST /v1/lager/{vare_id}/telling (bestilling:opprett, idem).

    EN TELLING SETTER INGEN BEHOLDNING. Den skriver DIFFERANSEN mellom
    det talte og det bokførte som en linje i hovedboken, og dermed
    forblir beholdningen summen av bevegelser.

    EN TELLING SOM BEKREFTET TALLET GIR EN LINJE MED ENDRING 0, og den
    linjen er svaret på «når ble dette sist talt». Å droppe den ville
    gjort `ikke_talt` ubesvarlig.

    SVARET ER DIFFERANSEN — altså hvor mye lageret var feil.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        vid = _sti_uuid(request, "vare_id", rid)
        talt = _antall(kropp, "talt_antall", rid, minst=0)
        utfort = _tekst(kropp, "utfort", rid, 32)
        notat = _tekst(kropp, "notat", rid, MAKS_NOTAT)
        bid_bevegelse = _utled("telling", tenant, nokkel)
        return ("SELECT m27_registrer_telling(%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, bid_bevegelse, vid, talt, utfort, notat, bid),
                {"vare_id": str(vid),
                 "bevegelse_id": str(bid_bevegelse)}, "endring")
    return _skriv(tjeneste, request, bygg)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/lager/{vare_id}/aktiv (bestilling:opprett, idem).

    EN VARE DEAKTIVERES, DEN SLETTES ALDRI: en slettet vare ville tatt
    bevegelseshistorikken med seg, og den er hele beholdningen.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        vid = _sti_uuid(request, "vare_id", rid)
        # `aktiv` ER PÅKREVD HER. `_bool` faller tilbake til `false`, og
        # en kropp uten feltet ville derfor DEAKTIVERT varen — altså en
        # utelatelse som utfører en handling (CodeRabbit, 108).
        if "aktiv" not in kropp:
            raise _Avbrudd(_feil("request_feilformet", rid))
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m27_sett_vareaktiv(%s,%s,%s,%s)",
                (tenant, vid, aktiv, bid),
                {"vare_id": str(vid)}, "endret")
    return _skriv(tjeneste, request, bygg)
