"""M-26 prisbok- og tilbudsagentens API (migrasjon 108).

Åtte endepunkter: tre leseveier og fem skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_prisbok_eier`-eid SECURITY DEFINER-dør i 108, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN SETTER INGEN PRIS, GENERERER INGET TILBUD OG ATTESTERER
INGENTING. ALLE TRE bransjemalene navngir modulen som verifikatoren
`v_prisbok`, betrodd for `priser_fra_prisbok`, `laste_klausuler_uendret`
og `standard_forbehold_inkludert` — og bruker dem til å slippe
`tilbud.generer` gjennom som `modus: auto`. `pris.endre` står som
`alltid_stopp` i policyen; det er den ene handlingen malene selv ikke tør
automatisere, og v1 er enig.

HVER PRIS I BOKA ER SKREVET AV ET MENNESKE gjennom en dør. API-et ganger
ikke, indekserer ikke og runder ikke: `listepris_ore` er tallet noen
skrev. En modul som beregnet en ny pris ville tatt en beslutning som
avgjør hva virksomheten tjener, på et grunnlag ingen har målt.

OG DET FINNES INGEN TILBUDSDØR. Et tilbud er et bindende utspill mot en
kunde; her finnes ingen tilbudstabell, ingen dokumentgenerering og ingen
kobling til M-5s maler.

EN PRIS ENDRES ALDRI — DEN ERSTATTES. Skrivedøren lukker den forrige
versjonen i samme transaksjon, så «hva sto i boka den dagen» alltid har
nøyaktig ett svar. Det er hele grunnen til at modulen finnes:
`priser_fra_prisbok` er verdiløs hvis ingen kan svare på det.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som M-13 (101) innførte
    og M-23/M-24/M-14/M-25 gjenbrukte. Hva vi tar betalt er
    virksomhetens pengestrøm.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som
    096/100–107.

SP-2 PÅ PRODUKTDØREN: `produkt_id` utledes deterministisk av
Idempotency-Key-en. Prisdøren og klausuldøren er versjonerende og
trenger ingen utledet id — versjonen er nøkkelen, og en gjentatt
prisendring gir en ny versjon fordi den ER en ny beslutning.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_PRODUKTER = 200
MAKS_KODE = 100
MAKS_NAVN = 300
MAKS_ENHET = 60
MAKS_TITTEL = 300
MAKS_TEKST = 20000
MAKS_BEGRUNNELSE = 2000

#: Ytterpunktet for et beløp i øre (101s form, gjenbrukt i 104–107).
MAKS_ORE = 10 ** 13

#: TERSKLENES YTTERPUNKTER, ikke verdier. Speiler CHECK-ene i 108.
TERSKELGRENSER = {
    "rabattgrense_promille": (0, 1000),
    "utlop_varsel_dogn": (0, 3650),
    "uten_pris_dogn": (0, 3650),
}

_M26_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m26:prisbok")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M26_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    """Tekst, TRIMMET — og lengden måles på det som faktisk lagres.

    Dørene i 108 `btrim`-er selv, så en utrimmet verdi herfra ville
    blitt lagret trimmet likevel: API-et og basen ville vært uenige om
    hva som ble skrevet. Koden er dessuten det et tilbud siterer, og
    « K-100 » og «K-100» skal ikke kunne være to rader.
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
    og uten sjekken ville `{"listepris_ore": true}` blitt prisen 1 øre.
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
        if "kode_unik" in str(e) or "en_apen" in str(e):
            return _Avbrudd(_feil("prisbok_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: en pris skrevet bakover, en prisendring
        # uten begrunnelse.
        return _Avbrudd(_feil("prisbok_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        # Vaktens dommer: en frosset pris som skulle endres, to
        # overlappende versjoner, en klausulhash som ikke stemmer.
        return _Avbrudd(_feil("prisbok_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Prisbokflatens tilstand i én transaksjon, gjennom fire lesedører."""
    s = conn.execute("SELECT * FROM m26_prisbokstatus(%s)",
                     (tenant,)).fetchone()
    produkter = [
        {"produkt_id": str(r[0]), "kode": r[1], "navn": r[2],
         "enhet": r[3], "aktiv": r[4], "versjon": r[5],
         "listepris_ore": r[6], "valuta": r[7],
         "gyldig_fra": r[8].isoformat() if r[8] else None,
         "gyldig_til": r[9].isoformat() if r[9] else None,
         "dogn_til_utlop": r[10], "versjoner": r[11],
         "apne_funn": list(r[12] or ())}
        for r in conn.execute("SELECT * FROM m26_produktene(%s,%s)",
                              (tenant, MAKS_PRODUKTER)).fetchall()]
    klausuler = [
        {"kode": r[0], "versjon": r[1], "tittel": r[2], "tekst": r[3],
         "tekst_hash": r[4], "standard": r[5],
         "gyldig_fra": r[6].isoformat(),
         "gyldig_til": r[7].isoformat() if r[7] else None}
        for r in conn.execute("SELECT * FROM m26_klausulene(%s)",
                              (tenant,)).fetchall()]
    t = conn.execute("SELECT * FROM m26_tersklene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "produkter": s[0], "aktive": s[1], "med_gyldig_pris": s[2],
            "klausuler": s[3], "standardklausuler": s[4],
            "apne_funn": s[5], "har_terskel": s[6],
            "terskelversjon": s[7],
            "vist": len(produkter)},
        "produkter": produkter,
        "klausuler": klausuler,
        "terskler": None if t is None else {
            "rabattgrense_promille": t[0], "utlop_varsel_dogn": t[1],
            "uten_pris_dogn": t[2], "versjon": t[3],
            "oppdatert": t[4].isoformat(), "oppdatert_av": t[5]}}


def prisbokbilde(tjeneste, request):
    """GET /v1/prisbok (okonomi:read) — tenantens egen prisbok."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def historikk_endepunkt(tjeneste, request):
    """GET /v1/prisbok/{produkt_id}/historikk (okonomi:read).

    HVER VERSJON, med sin gyldighet og sin begrunnelse. Dette er svaret
    på «hva sto i boka da vi ga det tilbudet» — og `priser_fra_prisbok`
    er verdiløs uten det.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        pid = _sti_uuid(request, "produkt_id", rid)
        rader = conn.execute("SELECT * FROM m26_prishistorikken(%s,%s)",
                             (auth.tenant, pid)).fetchall()
        return kanonisk_json({
            "produkt_id": str(pid),
            "versjoner": [
                {"versjon": r[0], "listepris_ore": r[1], "valuta": r[2],
                 "gyldig_fra": r[3].isoformat(),
                 "gyldig_til": r[4].isoformat() if r[4] else None,
                 "begrunnelse": r[5], "opprettet": r[6].isoformat(),
                 "opprettet_av": r[7]}
                for r in rader],
            "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def paa_dato_endepunkt(tjeneste, request):
    """GET /v1/prisbok/{produkt_id}/paa-dato?dato=… (okonomi:read).

    OPPSLAGET SOM BETYR NOE: hva sto i boka DEN dagen. Uten det er
    `priser_fra_prisbok` en attestasjon ingen kan etterprøve.

    Svaret er `null` når ingen pris gjaldt — ikke null kroner. «Gratis»
    og «ingen pris ført» er to helt forskjellige svar, og et register som
    blandet dem ville gitt bort produktet.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        from .policyadmin_http import _Avbrudd, _feil
        pid = _sti_uuid(request, "produkt_id", rid)
        dato = request.query_params.get("dato")
        if not isinstance(dato, str) or not (8 <= len(dato) <= 32):
            raise _Avbrudd(_feil("request_feilformet", rid))
        try:
            rad = conn.execute(
                "SELECT * FROM m26_pris_paa_dato(%s,%s,%s::date)",
                (auth.tenant, pid, dato)).fetchone()
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        return kanonisk_json({
            "produkt_id": str(pid), "dato": dato,
            "pris": None if rad is None else {
                "versjon": rad[0], "listepris_ore": rad[1],
                "valuta": rad[2], "gyldig_fra": rad[3].isoformat(),
                "gyldig_til": rad[4].isoformat() if rad[4] else None},
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
        return _ok({**svar, felt: ut}, rid)

    return _med_conn(tjeneste, rid, kjor)


def terskler_endepunkt(tjeneste, request):
    """POST /v1/prisbok/terskler (bestilling:opprett, idem).

    RABATTGRENSEN ER TENANTENS. «Ti prosent rabatt er for mye» er en
    forretningsbeslutning, ikke en teknisk detalj.

    ÆRLIG OM HVA DETTE IKKE ER: grensene går ikke gjennom M-1s
    policymotor. M-1 er dokumentbasert og har ingen fasilitet for en
    tenant-innstilling. Invarianten `rabattgrense_hardkodet` er oppfylt
    i den forstand som betyr noe — tenanten eier og fører verdiene — men
    koblingen til M-1 står igjen som et NAVNGITT gap.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        verdier = [_heltall(kropp, felt, rid, *TERSKELGRENSER[felt])
                   for felt in ("rabattgrense_promille",
                                "utlop_varsel_dogn", "uten_pris_dogn")]
        return ("SELECT m26_sett_terskler(%s,%s,%s,%s,%s)",
                (tenant, *verdier, bid), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_produkt_endepunkt(tjeneste, request):
    """POST /v1/prisbok/produkt (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        kode = _tekst(kropp, "kode", rid, MAKS_KODE)
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        enhet = _tekst(kropp, "enhet", rid, MAKS_ENHET)
        pid = _utled("produkt", tenant, nokkel)
        return ("SELECT m26_registrer_produkt(%s,%s,%s,%s,%s,%s)",
                (tenant, pid, kode, navn, enhet, bid),
                {"produkt_id": str(pid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def sett_pris_endepunkt(tjeneste, request):
    """POST /v1/prisbok/{produkt_id}/pris (bestilling:opprett, idem).

    EN NY PRIS ER EN NY VERSJON. Døren lukker den forrige i samme
    transaksjon, så det aldri finnes et vindu der to priser gjelder eller
    ingen gjør det.

    BEGRUNNELSEN ER OBLIGATORISK: en prisendring uten begrunnelse er en
    beslutning ingen kan etterprøve, og prisen er det virksomheten tjener
    på.

    API-ET REGNER INGENTING. `listepris_ore` er tallet et menneske skrev.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        pid = _sti_uuid(request, "produkt_id", rid)
        pris = _ore(kropp, "listepris_ore", rid)
        valuta = kropp.get("valuta", "NOK")
        if not isinstance(valuta, str) or len(valuta) != 3:
            raise _Avbrudd(_feil("request_feilformet", rid))
        fra = _tekst(kropp, "gyldig_fra", rid, 32)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        return ("SELECT m26_sett_pris(%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, pid, pris, valuta, fra, begrunnelse, bid),
                {"produkt_id": str(pid)}, "versjon")
    return _skriv(tjeneste, request, bygg)


def sett_klausul_endepunkt(tjeneste, request):
    """POST /v1/prisbok/klausul (bestilling:opprett, idem).

    HASHEN REGNES I BASEN, av teksten selv. En hash kalleren oppga ville
    vært en påstand om innholdet, ikke en måling av det — og
    `laste_klausuler_uendret` ville da vært en attestasjon om påstanden.
    API-et sender derfor ALDRI en hash.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        kode = _tekst(kropp, "kode", rid, MAKS_KODE)
        tittel = _tekst(kropp, "tittel", rid, MAKS_TITTEL)
        tekst = _tekst(kropp, "tekst", rid, MAKS_TEKST)
        standard = _bool(kropp, "standard", rid)
        fra = _tekst(kropp, "gyldig_fra", rid, 32)
        return ("SELECT m26_sett_klausul(%s,%s,%s,%s,%s,%s::date,%s)",
                (tenant, kode, tittel, tekst, standard, fra, bid),
                {"kode": kode}, "versjon")
    return _skriv(tjeneste, request, bygg)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/prisbok/{produkt_id}/aktiv (bestilling:opprett, idem).

    ET PRODUKT DEAKTIVERES, DET SLETTES ALDRI: et slettet produkt ville
    tatt prishistorikken med seg, og den er svaret på hva et gammelt
    tilbud siterte.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        from .policyadmin_http import _Avbrudd, _feil
        pid = _sti_uuid(request, "produkt_id", rid)
        # `aktiv` ER PÅKREVD HER. `_bool` faller tilbake til `false` for
        # felt som er valgfrie (klausulens `standard`), og en kropp uten
        # feltet ville derfor DEAKTIVERT produktet — altså en utelatelse
        # som utfører en handling.
        if "aktiv" not in kropp:
            raise _Avbrudd(_feil("request_feilformet", rid))
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m26_sett_produktaktiv(%s,%s,%s,%s)",
                (tenant, pid, aktiv, bid),
                {"produkt_id": str(pid)}, "endret")
    return _skriv(tjeneste, request, bygg)
