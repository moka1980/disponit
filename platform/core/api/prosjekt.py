"""M-25 prosjekt- og kontraktagentens API (migrasjon 107).

Ni endepunkter: tre leseveier og seks skriveveier, alle mot dører. Ingen
av dem rører en tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_prosjekt_eier`-eid SECURITY DEFINER-dør i 107, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN FAKTURERER INGENTING OG ATTESTERER INGENTING.
`bransjemal-handverk-bygg.yaml` og `bransjemal-netthandel.yaml` navngir
modulen som verifikatoren `v_prosjekt`, betrodd for
`milepael_dokumentert`, `kontraktsfestet_betalingsplan`,
`prosjektbudsjett_ok`, `arbeid_dokumentert` og `befaring_dokumentert` —
og bruker dem til å slippe `ordre.bekreft_og_fakturer` gjennom som
`modus: auto`.

En automatisk faktura på en milepæl ingen har dokumentert er penger
krevd for arbeid som kanskje ikke er gjort. Kravet har forlatt systemet i
det øyeblikket det ble sendt, og en kunde som får en faktura for noe som
ikke skjedde husker det lenger enn vi husker feilen.

Her finnes derfor ingen fakturadør, ingen kobling til M-23s
fordringsregister, ingen status som heter `fakturert` — og ingen
signering.

DOKUMENTASJONEN ER OBLIGATORISK NÅR EN MILEPÆL MERKES NÅDD. Det er
modulens skarpeste regel: `milepael_dokumentert` i policyen kan aldri bli
sant om noe som ikke har en dokumentasjon å peke på.

FORBRUK OG BETALINGSPLAN ER TO FORSKJELLIGE STØRRELSER. `budsjett_ore`
er hva prosjektet får KOSTE; milepælenes `belop_ore` er hva kontrakten
lar oss KREVE. API-et holder dem i hver sin kolonne hele veien.

TIMER FØRES I HELE MINUTTER, ikke desimaltimer: «1,5 time» er 90
minutter og ikke 1.4999999999999998.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — scopet M-13 (101) innførte og
    M-23/M-24/M-14 gjenbrukte. Hva et prosjekt koster og hva vi kan
    kreve for det er virksomhetens pengestrøm.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som
    096/100–106.

SP-2 PÅ SKRIVEVEIENE SOM FØDER EN RAD: `prosjekt_id` og `arbeid_id`
utledes deterministisk av Idempotency-Key-en. En dobbelt ført time er et
forbruk som er for høyt, og et budsjett som ser sprukket ut uten å være
det.
"""
from __future__ import annotations

import json
import uuid as uuidlib

import psycopg

#: Taket for hvor mange prosjekter flaten viser. Dørens tak er 1000.
MAKS_PROSJEKTER = 200

MAKS_REF = 300
MAKS_NAVN = 300
MAKS_DOKREF = 500
MAKS_BESKRIVELSE = 2000
MAKS_BEGRUNNELSE = 2000

#: Ytterpunktet for et beløp i øre (101s form, gjenbrukt i 104–106).
MAKS_ORE = 10 ** 13

#: Maks antall milepæler i én betalingsplan. Femti milepæler er en plan
#: ingen kunde ville forstått, og et tak hindrer at ett kall skriver
#: tusen rader.
MAKS_MILEPAELER = 50

#: Et døgn har 1440 minutter, og en føring gjelder ÉN dag. Speiler
#: CHECK-en i 107.
MAKS_MINUTTER = 1440

#: TERSKLENES YTTERPUNKTER, ikke verdier. Speiler CHECK-ene i 107.
TERSKELGRENSER = {
    "budsjettvarsel_promille": (0, 10000),
    "milepael_frist_dogn": (0, 3650),
    "stillhet_dogn": (1, 3650),
}

_M25_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m25:prosjekt")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M25_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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
    og uten sjekken ville `{"minutter": true}` blitt ett minutts arbeid.
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


def _milepaelliste(kropp, rid) -> str:
    """Betalingsplanens milepæler, validert HER og serialisert for døren.

    VALIDERINGEN ER FLATENS, IKKE BASENS: døren tar imot en `jsonb` og
    lar CHECK-ene avvise det som ikke holder mål — men en liste med et
    ulesbart beløp ville da blitt en castfeil uten et ord om hvilken
    milepæl som var gal. Her blir den 400 med en gang.

    REKKEFØLGEN ER LISTENS: milepæl nummer én er den første i lista.
    Døren nummererer dem, så en plan ikke kan få to nummer to.
    """
    from .policyadmin_http import _Avbrudd, _feil
    liste = kropp.get("milepaeler")
    if not isinstance(liste, list) or not (1 <= len(liste)
                                           <= MAKS_MILEPAELER):
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for m in liste:
        if not isinstance(m, dict):
            raise _Avbrudd(_feil("request_feilformet", rid))
        navn = m.get("navn")
        dato = m.get("planlagt_dato")
        belop = m.get("belop_ore")
        if not isinstance(navn, str) or not navn.strip() \
                or len(navn) > MAKS_NAVN:
            raise _Avbrudd(_feil("request_feilformet", rid))
        if not isinstance(dato, str) or not (8 <= len(dato) <= 32):
            raise _Avbrudd(_feil("request_feilformet", rid))
        if not isinstance(belop, int) or isinstance(belop, bool) \
                or not (0 <= belop < MAKS_ORE):
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut.append({"navn": navn, "planlagt_dato": dato,
                   "belop_ore": belop})
    return json.dumps(ut)


_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
    psycopg.errors.InsufficientPrivilege,
)


def _doerfeil(e, rid):
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if "navn_unik" in str(e):
            return _Avbrudd(_feil("prosjekt_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: en milepæl uten dokumentasjon, en
        # betalingsplan uten milepæler, arbeid på et avsluttet prosjekt.
        return _Avbrudd(_feil("prosjekt_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # Vaktens dommer: en nådd milepæl som skulle endres, et frosset
        # budsjett, et gjenåpnet prosjekt. TILSTANDER som sier nei.
        return _Avbrudd(_feil("prosjekt_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Prosjektflatens tilstand i én transaksjon, gjennom tre lesedører.

    SAMMENDRAGET KOMMER FRA SIN EGEN DØR og teller over ALT — ikke fra
    den avkortede listen.
    """
    s = conn.execute("SELECT * FROM m25_prosjektstatus(%s)",
                     (tenant,)).fetchone()
    prosjekter = [
        {"prosjekt_id": str(r[0]), "kunde_ref": r[1], "navn": r[2],
         "kontrakt_ref": r[3], "budsjett_ore": r[4], "forbruk_ore": r[5],
         "minutter": r[6], "start": r[7].isoformat(),
         "planlagt_slutt": r[8].isoformat(), "status": r[9],
         "dogn_til_slutt": r[10], "milepaeler": r[11], "naadde": r[12],
         "klar_ore": r[13], "plan_ore": r[14],
         "apne_funn": list(r[15] or ())}
        for r in conn.execute("SELECT * FROM m25_prosjektene(%s,%s)",
                              (tenant, MAKS_PROSJEKTER)).fetchall()]
    t = conn.execute("SELECT * FROM m25_tersklene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "aktive": s[0], "avsluttede": s[1], "budsjett_ore": s[2],
            "forbruk_ore": s[3], "klar_ore": s[4], "apne_funn": s[5],
            "over_budsjett": s[6], "har_terskel": s[7],
            "terskelversjon": s[8],
            # LISTEN ER AVKORTET, OG FLATEN SKAL KUNNE SI DET.
            "vist": len(prosjekter)},
        "prosjekter": prosjekter,
        # TERSKLENE ER `None` NÅR DE IKKE ER SATT, ikke et sett
        # standardverdier.
        "terskler": None if t is None else {
            "budsjettvarsel_promille": t[0], "milepael_frist_dogn": t[1],
            "stillhet_dogn": t[2], "versjon": t[3],
            "oppdatert": t[4].isoformat(), "oppdatert_av": t[5]}}


def prosjektbilde(tjeneste, request):
    """GET /v1/prosjekt (okonomi:read) — tenantens egne prosjekter."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def milepaelene_endepunkt(tjeneste, request):
    """GET /v1/prosjekt/{prosjekt_id}/milepaeler (okonomi:read).

    BETALINGSPLANEN med hva som er nådd, av hvem, og HVA SOM
    DOKUMENTERER DET. Uten den siste kolonnen er «milepæl nådd» en
    påstand — og den påstanden er grunnlaget for et krav mot kunden.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        pid = _sti_uuid(request, "prosjekt_id", rid)
        rader = conn.execute("SELECT * FROM m25_milepaelene(%s,%s)",
                             (auth.tenant, pid)).fetchall()
        return kanonisk_json({
            "prosjekt_id": str(pid),
            "milepaeler": [
                {"milepael_nr": r[0], "navn": r[1],
                 "planlagt_dato": r[2].isoformat(), "belop_ore": r[3],
                 "naadd_ts": r[4].isoformat() if r[4] else None,
                 "naadd_av": r[5], "dokumentasjon_ref": r[6],
                 "dogn_over_frist": r[7]}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def arbeidet_endepunkt(tjeneste, request):
    """GET /v1/prosjekt/{prosjekt_id}/arbeid (okonomi:read).

    Det som FAKTISK er gjort, append-only i basen. Det er den ene siden
    av budsjettspørsmålet, og `arbeid_dokumentert` i policyen hviler til
    slutt på at det står her.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        pid = _sti_uuid(request, "prosjekt_id", rid)
        rader = conn.execute("SELECT * FROM m25_arbeidet(%s,%s)",
                             (auth.tenant, pid)).fetchall()
        return kanonisk_json({
            "prosjekt_id": str(pid),
            "arbeid": [
                {"arbeid_id": str(r[0]), "utfort": r[1].isoformat(),
                 "minutter": r[2], "kostnad_ore": r[3],
                 "beskrivelse": r[4], "registrert": r[5].isoformat(),
                 "registrert_av": r[6]}
                for r in rader],
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
            # Datoen er kallerens tekst, og castet skjer i basen. En
            # ulesbar dato er 400, ikke 409.
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
    """POST /v1/prosjekt/terskler (bestilling:opprett, idem).

    GRENSENE ER TENANTENS. Hvor mye et prosjekt kan gå over budsjett før
    noen skal se på det, er en forretningsbeslutning.

    ÆRLIG OM HVA DETTE IKKE ER: grensene går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert og har ingen fasilitet for en
    tenant-innstilling. Invarianten `budsjettvarsel_hardkodet` er
    oppfylt i den forstand som betyr noe — tenanten eier og fører
    verdiene — men koblingen til M-1 står igjen som et NAVNGITT gap.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        verdier = [_heltall(kropp, felt, rid, *TERSKELGRENSER[felt])
                   for felt in ("budsjettvarsel_promille",
                                "milepael_frist_dogn", "stillhet_dogn")]
        return ("SELECT m25_sett_terskler(%s,%s,%s,%s,%s)",
                (tenant, *verdier, bid), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_endepunkt(tjeneste, request):
    """POST /v1/prosjekt (bestilling:opprett, idem).

    `budsjett_ore` er hva prosjektet får KOSTE. Det er ikke hva vi kan
    kreve — det står i betalingsplanen, og de to blandes aldri.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        kunde = _tekst(kropp, "kunde_ref", rid, MAKS_REF)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        kontrakt = _valgfri_tekst(kropp, "kontrakt_ref", rid, MAKS_REF)
        budsjett = _ore(kropp, "budsjett_ore", rid)
        start = _tekst(kropp, "start", rid, 32)
        slutt = _tekst(kropp, "planlagt_slutt", rid, 32)
        pid = _utled("prosjekt", tenant, nokkel)
        return ("SELECT m25_registrer_prosjekt(%s,%s,%s,%s,%s,%s,"
                "       %s::date,%s::date,%s)",
                (tenant, pid, kunde, navn, kontrakt, budsjett, start,
                 slutt, bid),
                {"prosjekt_id": str(pid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def betalingsplan_endepunkt(tjeneste, request):
    """POST /v1/prosjekt/{prosjekt_id}/betalingsplan
    (bestilling:opprett, idem).

    HELE SETTET I ETT KALL, som M-23s purreplan og av samme grunn: en
    dør som la til én milepæl om gangen ville latt planen stå halvferdig,
    og sveipen ville vurdert prosjektet mot den i det vinduet.

    MILEPÆLER SOM ALT ER NÅDD RØRES IKKE. De er frosset i basen, og en
    omskriving av planen skal ikke kunne slette grunnlaget for et krav
    som alt er stilt.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        pid = _sti_uuid(request, "prosjekt_id", rid)
        plan = _milepaelliste(kropp, rid)
        return ("SELECT m25_sett_betalingsplan(%s,%s,%s::jsonb,%s)",
                (tenant, pid, plan, bid),
                {"prosjekt_id": str(pid)}, "milepaeler")
    return _skriv(tjeneste, request, bygg)


def naa_milepael_endepunkt(tjeneste, request):
    """POST /v1/prosjekt/{prosjekt_id}/milepael (bestilling:opprett,
    idem).

    DOKUMENTASJONEN ER OBLIGATORISK, og det er modulens skarpeste regel.
    `milepael_dokumentert` i policyen kan aldri bli sant om noe som ikke
    har en dokumentasjon å peke på — og en automatisk faktura på en
    milepæl ingen har dokumentert er penger krevd for arbeid som kanskje
    ikke er gjort.

    DØREN FAKTURERER IKKE. Den merker milepælen nådd; hva som skjer med
    kravet er et menneskes beslutning i et annet register.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        pid = _sti_uuid(request, "prosjekt_id", rid)
        nr = _heltall(kropp, "milepael_nr", rid, 1, MAKS_MILEPAELER)
        dokref = _tekst(kropp, "dokumentasjon_ref", rid, MAKS_DOKREF)
        return ("SELECT m25_naa_milepael(%s,%s,%s,%s,%s)",
                (tenant, pid, nr, dokref, bid),
                {"prosjekt_id": str(pid), "milepael_nr": nr}, "ny")
    return _skriv(tjeneste, request, bygg)


def registrer_arbeid_endepunkt(tjeneste, request):
    """POST /v1/prosjekt/{prosjekt_id}/arbeid (bestilling:opprett, idem).

    TIMER I HELE MINUTTER, ikke desimaltimer: «1,5 time» er 90 minutter
    og ikke 1.4999999999999998. Et timeregnskap i flyttall viser seg
    først når summene ikke går opp — og da er det ikke lenger til å
    finne ut av.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        pid = _sti_uuid(request, "prosjekt_id", rid)
        utfort = _tekst(kropp, "utfort", rid, 32)
        minutter = _heltall(kropp, "minutter", rid, 1, MAKS_MINUTTER)
        kostnad = _ore(kropp, "kostnad_ore", rid)
        beskrivelse = _tekst(kropp, "beskrivelse", rid,
                             MAKS_BESKRIVELSE)
        aid = _utled("arbeid", tenant, nokkel)
        return ("SELECT m25_registrer_arbeid(%s,%s,%s,%s::date,%s,%s,"
                "       %s,%s)",
                (tenant, aid, pid, utfort, minutter, kostnad,
                 beskrivelse, bid),
                {"prosjekt_id": str(pid), "arbeid_id": str(aid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def avslutt_endepunkt(tjeneste, request):
    """POST /v1/prosjekt/{prosjekt_id}/avslutt (bestilling:opprett, idem).

    AVSLUTNINGEN LUKKER FUNNENE i samme transaksjon. Et åpent funn om et
    prosjekt som er avsluttet er et varsel ingen kan gjøre noe med.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        pid = _sti_uuid(request, "prosjekt_id", rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        return ("SELECT m25_avslutt_prosjekt(%s,%s,%s,%s)",
                (tenant, pid, begrunnelse, bid),
                {"prosjekt_id": str(pid)}, "ny")
    return _skriv(tjeneste, request, bygg)
