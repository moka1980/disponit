"""M-49 sanksjonskontrollens API (migrasjon 117).

Elleve endepunkter: fem leseveier og seks skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_sanksjon_eier`-eid SECURITY DEFINER-dør i 117, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN BLOKKERER INGEN HANDEL, OG DET ER EN BESLUTNING MED EN
BEGRUNNELSE — ikke en forglemmelse.

Spesifikasjonens vakt sier «treff blokkerer fail-closed og løses kun av
menneske», og samtidig «navnelikhet er aldri automatisk avfeid». De to
sammen betyr at treffene blir mange og at ingen kan lukkes maskinelt.
Den tyngste grunnen til at v1 likevel ikke blokkerer, er at DET IKKE
FINNES NOE Å BLOKKERE MED: et register stanser ingen handel; det måtte
M-23, M-14 eller M-42 spurt registeret FØR de handlet, og den
koblingen finnes ikke i v1. Et flagg ingen leser er `alarm`-feltet fra
115 om igjen — det så ut som vern i to klynger uten å være det.

Hele beslutningen, med motargumentet og utløseren, står i toppen av
`117_m49_sanksjonskontroll.sql`.

DERFOR FINNES DET INGEN `blokker`-RUTE HER, og ingen kolonne å skrive
til. Fraværet ER porten `modulen_blokkerte_motpart`.

OG INGEN RUTE AVFEIER ET TREFF. `/avklaring` krever en konklusjon fra
et lukket sett OG en begrunnelse på minst tolv tegn — «ok» er ikke en
begrunnelse for å slippe en mulig sanksjonert part gjennom. Det finnes
ingen batchrute, ingen «lukk alle under 90 %», og sveipen har ingen
avklaringsvei. Fraværet ER porten `modulen_avfeide_navnelikhet`.

SAMMENLIGNINGEN SKJER IKKE HER. Basen lagrer listeVERSJONEN, ikke
listeINNHOLDET: fila er stor, den eies av utgiver, og å kopiere den inn
ville gjort oss til distributør av en sanksjonsliste. Kalleren
sammenligner mot fila og leverer treffene til `/kontroll`. Døra vokter
det basen faktisk KAN vite — blant annet at et `eksakt_identifikator`
ikke settes på et subjekt uten identifikator.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — samme scope som 101 innførte og
    klynge 3, 4, 5 og 116 gjenbrukte.
  * SKRIVINGEN bærer `bestilling:opprett` — presedensen fra 096/100–116.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes deterministisk av
Idempotency-Key-en. For avklaringen er det strengt nødvendig: en
gjentatt POST må ikke bli to dommer over samme treff.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_SUBJEKTER = 200
MAKS_NAVN = 300
MAKS_REF = 100
MAKS_BEGRUNNELSE = 2000
MAKS_LISTEVERSJON = 100
MAKS_LISTENAVN = 500
MAKS_TREFF = 200

#: Grensene API-et håndhever før døra, så en feilformet request får
#: `request_feilformet` og ikke en CHECK-violation forkledd som
#: konflikt. Verdiene MÅ speile CHECK-ene i 117.
KRAVGRENSER = {
    "matchterskel": (50, 100),
    "kontroll_gyldig_dogn": (1, 3650),
    "uavklart_frist_dogn": (0, 365),
    "ukontrollert_dogn": (0, 3650),
}

KILDER = ("ofac", "eu", "fn")
SUBJEKTTYPER = ("person", "foretak")
MATCHTYPER = ("eksakt_identifikator", "eksakt_navn", "navnelikhet")
KONKLUSJONER = ("bekreftet_treff", "ikke_samme_part",
                "uavklart_eskalert")
FUNNTYPER = ("uavklart_treff", "ukontrollert_subjekt",
             "kontroll_utlopt", "kontroll_mot_gammel_liste",
             "bekreftet_treff", "ingen_liste", "ingen_krav")

_M49_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m49:sanksjon")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M49_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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


def _tekst_valgfri(kropp, felt: str, rid, maks: int) -> str | None:
    """Fravær er et lovlig svar. En tom streng blir `None`: «ingen
    identifikator» og «en identifikator som er tom» skal ikke være to
    tilstander noen kan skille mellom i ettertid."""
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    if isinstance(verdi, str) and not verdi.strip():
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


def _dato(kropp, felt: str, rid) -> str:
    """ISO-dato som TEKST; basen caster og avviser framtida."""
    import datetime
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.date.fromisoformat(verdi)
    except ValueError:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _dato_valgfri(kropp, felt: str, rid) -> str | None:
    if kropp.get(felt) is None:
        return None
    return _dato(kropp, felt, rid)


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


def _feltliste(kropp, felt: str, rid) -> list[str]:
    """Matchgrunnlaget: HVILKE felter ble sammenlignet."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, list) or not verdi:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for f in verdi:
        if not isinstance(f, str) or not f.strip() or len(f) > 60:
            raise _Avbrudd(_feil("request_feilformet", rid))
        if f.strip() not in ut:
            ut.append(f.strip())
    return ut


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _treffliste(kropp, felt: str, rid, nokkel: str,
                tenant: str) -> list[dict]:
    """Treffene kontrollen fant, validert FØR de når døra.

    ID-ene UTLEDES her og sendes ikke inn: en klient som fikk oppgi
    dem kunne sendt samme id to ganger, eller gjenbrukt en fra en
    tidligere kontroll — og et treff er en observasjon knyttet til
    NØYAKTIG én kontroll mot NØYAKTIG én listeversjon.

    LIKHETEN VALIDERES MOT MATCHTYPEN ALT HER, selv om basen har de
    samme CHECK-ene. De to sjekkene svarer på hver sin ting: denne gir
    et ærlig 400 til den som skrev feil, CHECK-en gjør det umulig å
    komme utenom.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return []
    if not isinstance(verdi, list) or len(verdi) > MAKS_TREFF:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for i, rad in enumerate(verdi):
        if not isinstance(rad, dict):
            raise _Avbrudd(_feil("request_feilformet", rid))
        matchtype = _valg(rad, "matchtype", rid, MATCHTYPER)
        matchfelt = _feltliste(rad, "matchfelt", rid)
        likhet = _heltall(rad, "likhet", rid, 0, 100)
        # ET EKSAKT TREFF HAR 100 % LIKHET, og en navnelikhet er under.
        # Var de ikke skilt, ville den ene klassen som en dag skal
        # kunne blokkere maskinelt, vært utvisket.
        if matchtype == "navnelikhet":
            if likhet >= 100:
                raise _Avbrudd(_feil("request_feilformet", rid))
        elif likhet != 100:
            raise _Avbrudd(_feil("request_feilformet", rid))
        if (matchtype == "eksakt_identifikator"
                and "identifikator" not in matchfelt):
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut.append({
            "treff_id": str(_utled(f"treff:{i}", tenant, nokkel)),
            "matchtype": matchtype,
            "matchfelt": matchfelt,
            "likhet": likhet,
            "listenavn": _tekst(rad, "listenavn", rid, MAKS_LISTENAVN),
            "liste_referanse": _tekst(rad, "liste_referanse", rid,
                                      MAKS_REF),
            "liste_program": _tekst_valgfri(rad, "liste_program", rid,
                                            MAKS_REF),
        })
    return ut


def _doerfeil(e, rid):
    """Dørenes dommer → API-feil. Samme form som 112/114/116."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if "versjon_unik" in str(e) or "ref_unik" in str(e) \
                or "treff_unik" in str(e):
            return _Avbrudd(_feil("sanksjon_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: et eksakt identifikatortreff på et
        # subjekt uten identifikator, en avklaring uten begrunnelse,
        # et treff som alt er avklart.
        return _Avbrudd(_feil("sanksjon_ulovlig_tilstand", rid, 409))
    if isinstance(e, (psycopg.errors.IntegrityConstraintViolation,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.InsufficientPrivilege)):
        # Vaktenes dommer, blant dem nektet mot å lukke et bekreftet
        # treff bort.
        return _Avbrudd(_feil("sanksjon_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Sanksjonsflatens tilstand i én transaksjon, gjennom fire dører."""
    s = conn.execute("SELECT * FROM m49_sanksjonsstatus(%s)",
                     (tenant,)).fetchone()
    subjekter = [
        {"subjekt_id": str(r[0]), "ekstern_ref": r[1],
         "navn_oppgitt": r[2], "subjekttype": r[3], "land": r[4],
         "har_identifikator": r[5], "aktiv": r[6],
         "opprettet": r[7].isoformat(),
         "siste_kontroll": r[8].isoformat() if r[8] else None,
         "siste_utfall": r[9], "apne_treff": r[10],
         "groveste_matchtype": r[11], "apne_funn": r[12]}
        for r in conn.execute("SELECT * FROM m49_subjektene(%s,%s)",
                              (tenant, MAKS_SUBJEKTER)).fetchall()]
    lister = [
        {"liste_id": str(r[0]), "kilde": r[1], "listeversjon": r[2],
         "gjelder_fra": r[3].isoformat(), "innhold_sha256": r[4],
         "antall_oppforinger": r[5], "registrert": r[6].isoformat(),
         "registrert_av": r[7], "er_nyeste": r[8]}
        for r in conn.execute("SELECT * FROM m49_listene(%s)",
                              (tenant,)).fetchall()]
    k = conn.execute("SELECT * FROM m49_kravene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "subjekter": s[0], "aktive": s[1], "kontrollerte": s[2],
            # DET VIKTIGSTE TALLET STÅR FØRST. Et treff ingen har sett
            # på er ikke et vern, det er en udokumentert risiko.
            "uavklarte_treff": s[3], "bekreftede_treff": s[4],
            "apne_funn": s[5], "lister": s[6],
            "nyeste_listeversjon": s[7], "har_krav": s[8],
            "kravversjon": s[9], "vist": len(subjekter)},
        "subjekter": subjekter,
        "lister": lister,
        "krav": None if k is None else {
            "matchterskel": k[0], "kontroll_gyldig_dogn": k[1],
            "uavklart_frist_dogn": k[2], "ukontrollert_dogn": k[3],
            "versjon": k[4], "oppdatert": k[5].isoformat(),
            "oppdatert_av": k[6]}}


def sanksjonsbilde(tjeneste, request):
    """GET /v1/sanksjon (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def kontroller_endepunkt(tjeneste, request):
    """GET /v1/sanksjon/{subjekt_id}/kontroller (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        sid = _sti_uuid(request, "subjekt_id", rid)
        rader = conn.execute("SELECT * FROM m49_kontrollene(%s,%s)",
                             (auth.tenant, sid)).fetchall()
        svar = {"subjekt_id": str(sid), "request_id": rid,
                "kontroller": [
                    {"kontroll_id": str(r[0]), "liste_id": str(r[1]),
                     "kilde": r[2], "listeversjon": r[3],
                     "matchterskel": r[4],
                     "sammenlignede_felt": list(r[5] or ()),
                     "kravversjon": r[6], "utfall": r[7],
                     "antall_treff": r[8],
                     "kontrollert": r[9].isoformat(),
                     "kontrollert_av": r[10]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def treff_endepunkt(tjeneste, request):
    """GET /v1/sanksjon/{subjekt_id}/treff (okonomi:read).

    AVKLARTE OG UAVKLARTE I SAMME LISTE. `konklusjon` er NULL for de
    uavklarte; en flate som bare viste de uavklarte ville skjult hva
    noen faktisk konkluderte — og det er nettopp den raden et tilsyn
    ber om å få se.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        sid = _sti_uuid(request, "subjekt_id", rid)
        rader = conn.execute("SELECT * FROM m49_treffene(%s,%s)",
                             (auth.tenant, sid)).fetchall()
        svar = {"subjekt_id": str(sid), "request_id": rid,
                "treff": [
                    {"treff_id": str(r[0]),
                     "kontroll_id": str(r[1]), "matchtype": r[2],
                     "matchfelt": list(r[3] or ()), "likhet": r[4],
                     "listenavn": r[5], "liste_referanse": r[6],
                     "liste_program": r[7], "kilde": r[8],
                     "listeversjon": r[9],
                     "registrert": r[10].isoformat(),
                     "konklusjon": r[11], "begrunnelse": r[12],
                     "avklart": r[13].isoformat() if r[13] else None,
                     "avklart_av": r[14]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/sanksjon/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m49_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid, "funn": [
            {"subjekt_id": str(r[0]), "ekstern_ref": r[1],
             "navn_oppgitt": r[2], "funntype": r[3],
             "over_grense": r[4], "siste_matchtype": r[5],
             "siste_utfall": r[6], "kravversjon": r[7],
             "forst_sett": r[8].isoformat(),
             "sist_sett_sveip": r[9].isoformat(), "apen": r[10],
             "lukket_ts": r[11].isoformat() if r[11] else None}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def lister_endepunkt(tjeneste, request):
    """GET /v1/sanksjon/lister (okonomi:read).

    HVILKEN LISTE, I HVILKEN VERSJON, MED HVILKEN INNHOLDSSUM.
    Spørsmålet et tilsyn stiller er «sto de på lista DEN DAGEN», og
    uten denne lesedøra måtte tenanten spørre oss for å svare på det.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m49_listene(%s)",
                             (auth.tenant,)).fetchall()
        svar = {"request_id": rid, "lister": [
            {"liste_id": str(r[0]), "kilde": r[1],
             "listeversjon": r[2], "gjelder_fra": r[3].isoformat(),
             "innhold_sha256": r[4], "antall_oppforinger": r[5],
             "registrert": r[6].isoformat(), "registrert_av": r[7],
             "er_nyeste": r[8]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

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
        # `felt is None` merker VOID-dørene presist: psycopg gir `''`
        # for VOID, ikke None (111s lærdom).
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def krav_endepunkt(tjeneste, request):
    """POST /v1/sanksjon/krav (bestilling:opprett, idem).

    MATCHTERSKELEN ER TENANTENS — invarianten `matchterskel_hardkodet`.
    Hvor lik en streng må være for å bli et treff er en
    risikoavveining: en bank vil ha lavere terskel enn en nettbutikk,
    og begge skal kunne begrunne sitt valg overfor et tilsyn.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, _request):
        terskel = _heltall(kropp, "matchterskel", rid,
                           *KRAVGRENSER["matchterskel"])
        gyldig = _heltall(kropp, "kontroll_gyldig_dogn", rid,
                          *KRAVGRENSER["kontroll_gyldig_dogn"])
        uavklart = _heltall(kropp, "uavklart_frist_dogn", rid,
                            *KRAVGRENSER["uavklart_frist_dogn"])
        ukontrollert = _heltall(kropp, "ukontrollert_dogn", rid,
                                *KRAVGRENSER["ukontrollert_dogn"])
        return ("SELECT m49_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, terskel, gyldig, uavklart, ukontrollert, bid),
                {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_liste_endepunkt(tjeneste, request):
    """POST /v1/sanksjon/liste (bestilling:opprett, idem).

    MODULEN LASTER INGEN LISTE SELV — porten `modulen_hentet_eksternt`.
    Et menneske har hentet fila og oppgir kilde, versjon, dato og
    innholdssum. M-48 fikk klyngens ene unntak fra «ingen utgående
    forespørsel»; M-49 fikk det ikke, og grunnen er at en
    sanksjonsliste er noe helt annet enn et organisasjonsnummer: fila
    er stor, den oppdateres uforutsigbart, og en modul som hentet den
    automatisk ville tatt ansvaret for at NØYAKTIG den versjonen er
    den gjeldende.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        kilde = _valg(kropp, "kilde", rid, KILDER)
        versjon = _tekst(kropp, "listeversjon", rid,
                         MAKS_LISTEVERSJON)
        fra = _dato(kropp, "gjelder_fra", rid)
        sum_ = _sha256(kropp, "innhold_sha256", rid)
        antall = _heltall(kropp, "antall_oppforinger", rid, 0,
                          100_000_000)
        lid = _utled("liste", tenant, nokkel)
        return ("SELECT m49_registrer_liste("
                "%s,%s,%s,%s,%s::date,%s,%s,%s)",
                (tenant, lid, kilde, versjon, fra, sum_, antall, bid),
                {"liste_id": str(lid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_subjekt_endepunkt(tjeneste, request):
    """POST /v1/sanksjon/subjekt (bestilling:opprett, idem).

    IDENTIFIKATOREN ER VALGFRI, OG DET ER MODULENS VIKTIGSTE FELT.
    Har vi et organisasjonsnummer eller en nasjonal ID, kan et treff bli
    EKSAKT — den ene klassen som en dag kan blokkere automatisk. Har vi
    bare et navn, kan treffet aldri bli mer enn en navnelikhet, uansett
    hvor likt det ser ut.

    NORMALISERINGEN SENDES IKKE INN: den regnes i basen av
    `m49_normaliser`. Sendte kalleren den selv, kunne den vært hva som
    helst, og kolonnen ville sluttet å bety «det vi sammenlignet på».
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        navn = _tekst(kropp, "navn_oppgitt", rid, MAKS_NAVN)
        subjekttype = _valg(kropp, "subjekttype", rid, SUBJEKTTYPER)
        land = _tekst_valgfri(kropp, "land", rid, 2)
        if land is not None:
            land = land.upper()
            if len(land) != 2 or not land.isalpha():
                from .policyadmin_http import _Avbrudd, _feil
                raise _Avbrudd(_feil("request_feilformet", rid))
        fodselsdato = _dato_valgfri(kropp, "fodselsdato", rid)
        ident = _tekst_valgfri(kropp, "identifikator", rid, MAKS_REF)
        sid = _utled("subjekt", tenant, nokkel)
        return ("SELECT m49_registrer_subjekt("
                "%s,%s,%s,%s,%s,%s,%s::date,%s,%s)",
                (tenant, sid, ref, navn, subjekttype, land,
                 fodselsdato, ident, bid),
                {"subjekt_id": str(sid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_kontroll_endepunkt(tjeneste, request):
    """POST /v1/sanksjon/{subjekt_id}/kontroll (bestilling:opprett).

    KONTROLLEN OG TREFFENE SKRIVES SAMMEN. Tok kalleren begge deler
    hver for seg, kunne en kontroll påstå «ingen treff» mens
    treffradene sto der — og «antall uavklarte treff» ville målt noe
    annet enn virkeligheten.

    UTFALLET SENDES IKKE INN. Døra regner det av antall treff, så de
    to aldri kan si hver sin ting.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        sid = _sti_uuid(request, "subjekt_id", rid)
        lid = _sti_uuid_kropp(kropp, "liste_id", rid)
        felt = _feltliste(kropp, "sammenlignede_felt", rid)
        treff = _treffliste(kropp, "treff", rid, nokkel, tenant)
        kid = _utled("kontroll", tenant, nokkel)
        import json as _json
        return ("SELECT m49_registrer_kontroll("
                "%s,%s,%s,%s,%s,%s::jsonb,%s)",
                (tenant, kid, sid, lid, felt, _json.dumps(treff), bid),
                {"kontroll_id": str(kid),
                 "treff_id": [t["treff_id"] for t in treff]},
                "antall_treff")
    return _skriv(tjeneste, request, bygg)


def _sti_uuid_kropp(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def avklar_endepunkt(tjeneste, request):
    """POST /v1/sanksjon/treff/{treff_id}/avklaring.

    DEN ENESTE VEIEN ET TREFF KAN LUKKES. Det finnes ingen batchrute,
    ingen «lukk alle under 90 %», og sveipen har ingen avklaringsvei.
    Fraværet av alle tre ER porten `modulen_avfeide_navnelikhet`.

    KONKLUSJONEN HAR TRE VERDIER, IKKE TO. `uavklart_eskalert` er den
    ærlige tredje: en saksbehandler som IKKE klarer å avgjøre skal
    kunne si det, i stedet for å velge en av de to for å bli ferdig.
    En modul som bare tilbød ja og nei ville presset fram gjetninger og
    kalt dem avklaringer.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        tid = _sti_uuid(request, "treff_id", rid)
        konklusjon = _valg(kropp, "konklusjon", rid, KONKLUSJONER)
        begrunnelse = _tekst(kropp, "begrunnelse", rid,
                             MAKS_BEGRUNNELSE)
        if len(begrunnelse) < 12:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        aid = _utled("avklaring", tenant, nokkel)
        return ("SELECT m49_avklar_treff(%s,%s,%s,%s,%s,%s)",
                (tenant, aid, tid, konklusjon, begrunnelse, bid),
                {"avklaring_id": str(aid), "treff_id": str(tid)}, None)
    return _skriv(tjeneste, request, bygg)


def sett_aktiv_endepunkt(tjeneste, request):
    """POST /v1/sanksjon/{subjekt_id}/aktiv (bestilling:opprett)."""
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        sid = _sti_uuid(request, "subjekt_id", rid)
        verdi = kropp.get("aktiv")
        if not isinstance(verdi, bool):
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m49_sett_subjektaktiv(%s,%s,%s,%s)",
                (tenant, sid, verdi, bid),
                {"subjekt_id": str(sid), "aktiv": verdi}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/sanksjon/{subjekt_id}/funn/lukk.

    ET BEKREFTET TREFF KAN IKKE LUKKES HER. Døra nekter det, og
    grunnen er modulens skarpeste: `bekreftet_treff` betyr at et
    menneske har sagt at parten ER sanksjonert. En knapp som gjorde
    den observasjonen borte ville vært farligere enn manglende
    blokkering, fordi den ser ut som saksbehandling.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        sid = _sti_uuid(request, "subjekt_id", rid)
        funntype = _valg(kropp, "funntype", rid, FUNNTYPER)
        notat = _tekst(kropp, "notat", rid, MAKS_BEGRUNNELSE)
        if len(notat) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m49_lukk_funn(%s,%s,%s,%s,%s)",
                (tenant, sid, funntype, notat, bid),
                {"subjekt_id": str(sid), "funntype": funntype}, None)
    return _skriv(tjeneste, request, bygg)
