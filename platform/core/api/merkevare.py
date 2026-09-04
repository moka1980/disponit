"""M-55 merkevare- og IP-overvåkerens API (migrasjon 120).

Fjorten endepunkter: seks leseveier og åtte skriveveier, alle mot
dører. Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_merkevare_eier`-eid SECURITY DEFINER-dør i 120, og runtime
har ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM SENDER NOE UT AV HUSET, og det er ikke en
sjekk her: 120 har ingen mottaker, ingen kravtekst og ingen utboks å
skrive til. `modulen_sendte_krav` er ikke en regel vi håndhever — det
er en handling som ikke finnes.

Grunnen står i spesifikasjonens egen parkering av «automatisk
varselbrev ved IP-brudd»: et krav sendt på et automatisk funn er en
ANKLAGE MOT EN NAVNGITT PART, og en feilaktig anklage er ikke
reversibel ved å trekke den. Modulens eneste utgang er
`POST /henvis` — en peker inn i M-37s unntakskø, der et menneske
beslutter.

TRE NEKT SOM ER VERDT Å KJENNE:

  * `POST /funn` KREVER EN `kopi_id`. Ikke fordi API-et sjekker det,
    men fordi `merkevarefunn.kopi_id` er NOT NULL med fremmednøkkel.
    Et funn uten bevaringskopi kan ikke uttrykkes.

  * `POST /vurder` NEKTER uten at tenanten har satt en terskel. Et
    vennlig standardtall ville vært nettopp den hardkodede terskelen
    invarianten `forvekslingsterskel_hardkodet` forbyr.

  * `POST /lukk` NEKTER på et funn vurdert over tenantens egen
    terskel som ikke er henvist. Modulen har én utgang, og kunne den
    lukkes forbi, ville modulens eneste virkning vært viskbar.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett` — samme
presedens som 096/100–119.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en. For
vurderingen er det nødvendig av en egen grunn: en gjentatt POST må
ikke bli to vurderinger av samme funn, for da ville «hva mente vi»
hatt to svar på samme tidspunkt.
"""
from __future__ import annotations

import datetime
import uuid as uuidlib

import psycopg

MAKS_MERKER = 200
MAKS_FUNN = 300
MAKS_KOPIER = 300
MAKS_TEKST = 4000
MAKS_NAVN = 500
MAKS_URL = 2000
MAKS_REF = 200
#: BIGINT-taket for kopiens størrelse i byte.
MAKS_BYTES = 100_000_000_000

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 120.
KRAVGRENSER = {
    "forvekslingsterskel": (1, 100),
    "funnfrist_dogn": (1, 365),
    "henvisningsfrist_dogn": (1, 365),
}

ARTER = ("varemerke", "domenenavn", "firmanavn", "produktnavn",
         "logo", "slagord")
BRUKSFORMER = ("domenenavn", "annonsetekst", "produktnavn",
               "firmanavn", "sosial_konto",
               "markedsplassoppforing", "annet")
VARSELTYPER = ("funn_uten_vurdering", "forveksling_ikke_henvist",
               "vurdering_med_utdatert_terskel",
               "funn_eldre_enn_frist", "merkevare_uten_funn",
               "ingen_terskler")

_M55_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m55:merkevare")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M55_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _tekst_valgfri(kropp, felt: str, rid, maks: int) -> str | None:
    """NULL er et ærlig svar. En motpart vi ikke kjenner er ikke det
    samme som «ingen motpart», og en tom streng ville sett ut som
    det."""
    if kropp.get(felt) is None:
        return None
    return _tekst(kropp, felt, rid, maks)


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


def _bool(kropp, felt: str, rid) -> bool:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _dato(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.date.fromisoformat(verdi)
    except ValueError:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _tidspunkt(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.datetime.fromisoformat(verdi)
    except ValueError:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _url(kropp, felt: str, rid) -> str:
    """BARE http OG https.

    En `file:`- eller `javascript:`-URL i et bevis er ikke en kilde —
    det er en feil som har fått stå. 120 har samme CHECK; denne står
    her så feilen blir en 400 og ikke en 409.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_URL)
    if not (verdi.startswith("http://")
            or verdi.startswith("https://")):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if any(c.isspace() for c in verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _medietype(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_REF).lower()
    hoved, _, under = verdi.partition("/")
    if not hoved.isalpha() or not under:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if any(c not in "abcdefghijklmnopqrstuvwxyz0123456789.+-"
           for c in under):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sha256(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip().lower()
    if len(verdi) != 64 or any(c not in "0123456789abcdef"
                               for c in verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _klasser(kropp, felt: str, rid) -> list[str]:
    """Vareklassene er registerførerens koding, ikke vår — fri tekst,
    men en LISTE av korte strenger, ikke ett felt med komma i."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return []
    if not isinstance(verdi, list) or len(verdi) > 50:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for x in verdi:
        if not isinstance(x, str) or not x.strip() or len(x) > 50:
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut.append(x.strip())
    return ut


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


def _doerfeil(e, rid):
    """Dørenes dommer → API-feil. Samme form som 112–119."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if ("merkevarefunn_unik" in str(e)
                or "forvekslingsvurdering_unik" in str(e)
                or "merkevare_navn_unik" in str(e)
                or "bevaringskopi_unik" in str(e)
                or "alt henvist" in str(e)):
            return _Avbrudd(_feil("merkevare_ulovlig_tilstand", rid,
                                  409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: manglende terskel, lukking uten
        # henvisning, et varsel som ikke kan lukkes.
        return _Avbrudd(_feil("merkevare_ulovlig_tilstand", rid, 409))
    if isinstance(e, (psycopg.errors.IntegrityConstraintViolation,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.InsufficientPrivilege)):
        return _Avbrudd(_feil("merkevare_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Merkevareflatens tilstand i én transaksjon, gjennom fire dører."""
    s = conn.execute("SELECT * FROM m55_merkevarestatus(%s)",
                     (tenant,)).fetchone()
    merker = [
        {"merkevare_id": str(r[0]), "navn": r[1], "art": r[2],
         "registernummer": r[3], "registerfoerer": r[4],
         "vareklasser": list(r[5] or []),
         "gjelder_fra": r[6].isoformat(), "aktiv": r[7],
         "registrert": r[8].isoformat(), "antall_funn": r[9],
         "apne_funn": r[10], "uvurderte": r[11],
         "over_terskel": r[12],
         # DET TALLET SOM BETYR NOE: hvor mange forvekslinger som
         # venter på et menneske.
         "uhenviste": r[13], "hoyeste_likhet": r[14],
         "apne_varsler": r[15]}
        for r in conn.execute("SELECT * FROM m55_merkene(%s,%s)",
                              (tenant, MAKS_MERKER)).fetchall()]
    kopier = [
        {"kopi_id": str(r[0]), "kilde_url": r[1],
         "hentet_ts": r[2].isoformat(), "innhold_sha256": r[3],
         "innhold_bytes": r[4], "medietype": r[5],
         "lagringsnokkel": r[6], "registrert": r[7].isoformat(),
         "registrert_av": r[8], "brukt_i_funn": r[9]}
        for r in conn.execute(
            "SELECT * FROM m55_bevaringskopiene(%s,%s)",
            (tenant, MAKS_KOPIER)).fetchall()]
    k = conn.execute("SELECT * FROM m55_kravene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "merker": s[0], "aktive": s[1], "funn": s[2],
            "apne_funn": s[3], "uvurderte": s[4],
            "over_terskel": s[5], "uhenviste": s[6],
            "henviste": s[7], "bevaringskopier": s[8],
            "ubrukte_kopier": s[9], "apne_varsler": s[10],
            "har_krav": s[11], "terskel": s[12],
            "kravversjon": s[13], "vist": len(merker)},
        "merker": merker,
        "bevaringskopier": kopier,
        "krav": None if k is None else {
            "forvekslingsterskel": k[0], "funnfrist_dogn": k[1],
            "henvisningsfrist_dogn": k[2], "versjon": k[3],
            "oppdatert": k[4].isoformat(), "oppdatert_av": k[5]}}


def _funnrad(r) -> dict:
    """Funnet MED BEVISET på samme rad.

    Bevaringskopiens URL, tidspunkt og innholdssum står her, ikke bak
    et ekstra oppslag: et funn uten sitt bevis synlig er nettopp det
    modulen finnes for å unngå.
    """
    return {
        "funn_id": str(r[0]), "merkevare_id": str(r[1]),
        "merkenavn": r[2], "observert_navn": r[3],
        "bruksform": r[4], "kontekst": r[5], "motpart": r[6],
        "registrert": r[7].isoformat(), "registrert_av": r[8],
        "kopi_id": str(r[9]), "kilde_url": r[10],
        "hentet_ts": r[11].isoformat(), "innhold_sha256": r[12],
        "innhold_bytes": r[13], "medietype": r[14],
        # VURDERINGEN: NYESTE, med alt som skal til for å regne etter.
        "likhet": r[15], "terskel_brukt": r[16],
        "over_terskel": r[17],
        "grunnlag": list(r[18] or []) if r[18] is not None else None,
        "algoritmeversjon": r[19], "kravversjon": r[20],
        "vurdert": r[21].isoformat() if r[21] else None,
        "antall_vurderinger": r[22],
        "henvist_unntak_id": str(r[23]) if r[23] else None,
        "henvist_ts": r[24].isoformat() if r[24] else None,
        "henvist_av": r[25],
        "lukket_ts": r[26].isoformat() if r[26] else None,
        "lukket_av": r[27], "lukkebegrunnelse": r[28]}


def merkevarebilde(tjeneste, request):
    """GET /v1/merkevare (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/merkevare/{merkevare_id}/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        mid = _sti_uuid(request, "merkevare_id", rid)
        rader = conn.execute("SELECT * FROM m55_funnene(%s,%s,%s)",
                             (auth.tenant, mid, MAKS_FUNN)).fetchall()
        svar = {"merkevare_id": str(mid), "request_id": rid,
                "funn": [_funnrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def alle_funn_endepunkt(tjeneste, request):
    """GET /v1/merkevare/funn (okonomi:read) — på tvers av merker."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m55_funnene(%s,%s,%s)",
                             (auth.tenant, None, MAKS_FUNN)).fetchall()
        svar = {"request_id": rid,
                "funn": [_funnrad(r) for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def vurderinger_endepunkt(tjeneste, request):
    """GET /v1/merkevare/funn/{funn_id}/vurderinger (okonomi:read).

    HELE REKKEN, ikke bare den nyeste. En ny algoritme eller en ny
    terskel gir en ny rad, og det er der «hva mente vi da» står.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        fid = _sti_uuid(request, "funn_id", rid)
        rader = conn.execute("SELECT * FROM m55_vurderingene(%s,%s)",
                             (auth.tenant, fid)).fetchall()
        svar = {"funn_id": str(fid), "request_id": rid,
                "vurderinger": [
                    {"vurdering_id": str(r[0]), "likhet": r[1],
                     "terskel_brukt": r[2], "over_terskel": r[3],
                     "grunnlag": list(r[4] or []),
                     "algoritmeversjon": r[5], "kravversjon": r[6],
                     # INNDATAENE, SNAPSHOTET. Uten dem kan ingen
                     # regne etter, og en vurdering ingen kan regne
                     # etter er en mening — ikke et bevis.
                     "merkenavn_ved_vurdering": r[7],
                     "observert_ved_vurdering": r[8],
                     "vurdert": r[9].isoformat(), "vurdert_av": r[10]}
                    for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def bevaringskopier_endepunkt(tjeneste, request):
    """GET /v1/merkevare/bevaringskopier (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute(
            "SELECT * FROM m55_bevaringskopiene(%s,%s)",
            (auth.tenant, MAKS_KOPIER)).fetchall()
        svar = {"request_id": rid, "bevaringskopier": [
            {"kopi_id": str(r[0]), "kilde_url": r[1],
             "hentet_ts": r[2].isoformat(), "innhold_sha256": r[3],
             "innhold_bytes": r[4], "medietype": r[5],
             "lagringsnokkel": r[6], "registrert": r[7].isoformat(),
             "registrert_av": r[8], "brukt_i_funn": r[9]}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def varsler_endepunkt(tjeneste, request):
    """GET /v1/merkevare/varsler (okonomi:read) — nattens funn."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m55_varslene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid, "varsler": [
            {"varsel_id": str(r[0]), "merkevare_id": str(r[1]),
             "merkenavn": r[2],
             "funn_id": str(r[3]) if r[3] else None,
             "observert_navn": r[4], "varseltype": r[5],
             "over_grense": r[6], "detalj": r[7], "likhet": r[8],
             "terskel_brukt": r[9], "kravversjon": r[10],
             "forst_sett": r[11].isoformat(),
             "sist_sett_sveip": r[12].isoformat(), "apen": r[13],
             "lukket_ts": r[14].isoformat() if r[14] else None,
             "lukket_av": r[15], "lukkenotat": r[16]}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen sju av de åtte skriveveiene deler.

    `/vurder` bruker den IKKE: den returnerer en RAD (likhet, terskel,
    dom, grunnlag), ikke en skalar. Den som vurderer skal se hva
    vurderingen faktisk SIER, ikke bare at den gikk gjennom.
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
        # `felt is None` merker VOID-dørene presist (111s lærdom).
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def krav_endepunkt(tjeneste, request):
    """POST /v1/merkevare/krav (bestilling:opprett, idem).

    TERSKELEN ER TENANTENS. Hvor likt noe må være før det er
    forveksling er en forretnings- og juridisk vurdering: et varemerke
    i en nisje tåler langt mindre likhet enn et generisk ord gjør.

    NØKKELEN GÅR HELT INN I DØRA (119s lærdom): `merkevarekrav` er en
    singleton per tenant og har ingen id å utlede fra nøkkelen, så uten
    dette ville et gjenspill bumpet `versjon` — og hver vurdering bærer
    `kravversjon`.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        terskel = _heltall(kropp, "forvekslingsterskel", rid,
                           *KRAVGRENSER["forvekslingsterskel"])
        funnfrist = _heltall(kropp, "funnfrist_dogn", rid,
                             *KRAVGRENSER["funnfrist_dogn"])
        henvfrist = _heltall(kropp, "henvisningsfrist_dogn", rid,
                             *KRAVGRENSER["henvisningsfrist_dogn"])
        return ("SELECT m55_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, terskel, funnfrist, henvfrist, bid, nokkel),
                {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_merkevare_endepunkt(tjeneste, request):
    """POST /v1/merkevare/merke (bestilling:opprett, idem).

    VÅRT EGET MERKE. `registernummer` og `registerfoerer` er valgfrie
    MEN henger sammen: et registrert varemerke og et innarbeidet
    kjennetegn har ikke samme vern, og den forskjellen skal stå på
    raden — ikke i hodet til den som leser den.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        art = _valg(kropp, "art", rid, ARTER)
        nummer = _tekst_valgfri(kropp, "registernummer", rid, MAKS_REF)
        foerer = _tekst_valgfri(kropp, "registerfoerer", rid,
                                MAKS_NAVN)
        klasser = _klasser(kropp, "vareklasser", rid)
        fra = _dato(kropp, "gjelder_fra", rid)
        mid = _utled("merke", tenant, nokkel)
        return ("SELECT m55_registrer_merkevare("
                "%s,%s,%s,%s,%s,%s,%s,%s::date,%s)",
                (tenant, mid, navn, art, nummer, foerer, klasser, fra,
                 bid), {"merkevare_id": str(mid)}, None)
    return _skriv(tjeneste, request, bygg)


def sett_merke_aktiv_endepunkt(tjeneste, request):
    """POST /v1/merkevare/{merkevare_id}/aktiv (bestilling:opprett).

    AKTIVFLAGGET, OG INGENTING ANNET. Merket selv er frosset: navnet en
    vurdering ble gjort mot kan ikke redigeres i ettertid.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        mid = _sti_uuid(request, "merkevare_id", rid)
        aktiv = _bool(kropp, "aktiv", rid)
        return ("SELECT m55_sett_merkevare_aktiv(%s,%s,%s,%s)",
                (tenant, mid, aktiv, bid), {}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_kopi_endepunkt(tjeneste, request):
    """POST /v1/merkevare/bevaringskopi (bestilling:opprett, idem).

    MODULEN HENTER IKKE. Kopien registreres av den som TOK den, med
    innholdssum og størrelse — de binder raden til bytene i
    artefaktlageret, og en bevaringskopi som ikke kan bindes til sitt
    eget innhold er ikke en bevaringskopi.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        url = _url(kropp, "kilde_url", rid)
        hentet = _tidspunkt(kropp, "hentet_ts", rid)
        sum_ = _sha256(kropp, "innhold_sha256", rid)
        bytes_ = _heltall(kropp, "innhold_bytes", rid, 1, MAKS_BYTES)
        medietype = _medietype(kropp, "medietype", rid)
        nokkel_ = _tekst(kropp, "lagringsnokkel", rid, MAKS_REF)
        kid = _utled("kopi", tenant, nokkel)
        return ("SELECT m55_registrer_bevaringskopi("
                "%s,%s,%s,%s::timestamptz,%s,%s,%s,%s,%s)",
                (tenant, kid, url, hentet, sum_, bytes_, medietype,
                 nokkel_, bid), {"kopi_id": str(kid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_funn_endepunkt(tjeneste, request):
    """POST /v1/merkevare/funn (bestilling:opprett, idem).

    `kopi_id` ER PÅKREVD, og det er ikke denne rutens fortjeneste:
    `merkevarefunn.kopi_id` er NOT NULL med fremmednøkkel i 120. Et
    funn uten bevaringskopi kan ikke uttrykkes.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        mid = _kropp_uuid(kropp, "merkevare_id", rid)
        kid = _kropp_uuid(kropp, "kopi_id", rid)
        observert = _tekst(kropp, "observert_navn", rid, MAKS_NAVN)
        bruksform = _valg(kropp, "bruksform", rid, BRUKSFORMER)
        kontekst = _tekst(kropp, "kontekst", rid, MAKS_TEKST)
        motpart = _tekst_valgfri(kropp, "motpart", rid, MAKS_NAVN)
        fid = _utled("funn", tenant, nokkel)
        return ("SELECT m55_registrer_funn("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, fid, mid, kid, observert, bruksform,
                 kontekst, motpart, bid), {"funn_id": str(fid)}, None)
    return _skriv(tjeneste, request, bygg)


def vurder_endepunkt(tjeneste, request):
    """POST /v1/merkevare/funn/{funn_id}/vurder (bestilling:opprett).

    EGEN RAMME, fordi svaret er en RAD. Den som vurderer skal se hva
    vurderingen SIER — likheten, terskelen den ble målt mot, dommen og
    grunnlaget — ikke bare at den gikk gjennom.

    DØRA NEKTER UTEN TERSKEL. Et vennlig standardtall her ville vært
    nettopp den hardkodede terskelen invarianten forbyr.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        fid = _sti_uuid(request, "funn_id", rid)
        vid = _utled("vurdering", tenant, nokkel)
        try:
            rad = conn.execute(
                "SELECT * FROM m55_vurder_funn(%s,%s,%s,%s)",
                (tenant, fid, vid, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"funn_id": str(fid), "vurdering_id": str(vid),
                    "likhet": rad[0], "terskel_brukt": rad[1],
                    "over_terskel": rad[2],
                    "grunnlag": list(rad[3] or []),
                    "kravversjon": rad[4],
                    "algoritmeversjon": rad[5]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def henvis_endepunkt(tjeneste, request):
    """POST /v1/merkevare/funn/{funn_id}/henvis (bestilling:opprett).

    MODULENS ENESTE UTGANG. Funnet får en peker inn i M-37s unntakskø,
    og DER beslutter et menneske hva som eventuelt skal skje.

    Det finnes ingen mottaker her, ingen kravtekst og ingen utboks.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "funn_id", rid)
        uid = _kropp_uuid(kropp, "unntak_id", rid)
        return ("SELECT m55_henvis_funn(%s,%s,%s,%s)",
                (tenant, fid, uid, bid), {"funn_id": str(fid)}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/merkevare/funn/{funn_id}/lukk (bestilling:opprett).

    DØRA NEKTER på et funn vurdert over tenantens egen terskel som
    ikke er henvist. Et uvurdert funn KAN lukkes — «vi så på det, det
    var ingenting» er et lovlig svar.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "funn_id", rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_TEKST)
        if len(begrunnelse) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m55_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, begrunnelse, bid),
                {"funn_id": str(fid)}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_varsel_endepunkt(tjeneste, request):
    """POST /v1/merkevare/varsel/{varsel_id}/lukk (bestilling:opprett).

    `forveksling_ikke_henvist` NEKTES av døra. Det varselet forsvinner
    når funnet HENVISES — sveipen lukker det som er løst; et menneske
    kan ikke lukke det som ikke er det.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        wid = _sti_uuid(request, "varsel_id", rid)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        if len(notat) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m55_lukk_varsel(%s,%s,%s,%s)",
                (tenant, wid, notat, bid),
                {"varsel_id": str(wid)}, None)
    return _skriv(tjeneste, request, bygg)
