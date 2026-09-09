"""M-23 kundefordringsagentens API (migrasjon 104).

Elleve endepunkter: fem leseveier og seks skriveveier. Ingen av dem rører en
tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_fordring_eier`-eid SECURITY DEFINER-dør i 104, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

MODULEN SENDER INGENTING TIL KUNDEN. Det er v1-dommen, og den strengeste
i klyngen: her finnes ingen SMTP-klient, ingen import av
`smtplib`/`email`/`httpx`/`urllib`, ingen mottakeradresse, ingen utgående
kø og ingen status som heter `sendt` eller `purret`. En purring sendt for
tidlig, til feil kunde, eller på en fordring som alt er betalt, er en
skade som ikke kan trekkes tilbake — den har forlatt systemet i det
øyeblikket den ble sendt.

MODULEN POSTERER HELLER IKKE. Samme snitt som M-13 (101): ingen
hovedbokskobling, ingen posteringsdør.

BELØP ER HELTALL HELE VEIEN. Kroppen tar `belop_ore` som `int`, aldri et
desimaltall, og et flyttall avvises med 400 — ikke rundes.

TRINNET FLYTTES ETT HAKK, OG DØREN TAR IKKE IMOT HVILKET. Et API som lot
kalleren be om «sett trinn 3» ville invitert til nettopp det hoppet
vakten i 104 finnes for å hindre — og et API som lar deg be om noe basen
alltid avviser, er et API som lyver. Endepunktet heter derfor
`/neste-trinn` og har ingen trinnparameter.

SCOPENE.

  * LESINGEN bærer `okonomi:read` — scopet M-13 (101) innførte, og dette
    er nøyaktig kretsen det ble laget for: hvem som skylder oss hva er
    virksomhetens pengestrøm, ikke allmenn tilstandsinnsikt. Gjenbrukt,
    ikke nytt.
  * SKRIVINGEN bærer `bestilling:opprett` — samme presedens som M-21
    (096), M-34 (100), M-13 (101), M-17 (102) og M-18 (103).

SP-2 PÅ SKRIVEVEIENE SOM FØDER EN RAD: `fordring_id` og `hendelse_id`
utledes deterministisk av Idempotency-Key-en. En dobbelt registrert
innbetaling ville gjort «hvor mye skylder de» til et tall som er for
lavt — og et krav ville blitt lukket for tidlig.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
import uuid as uuidlib

import psycopg

#: Taket for hvor mange fordringer flaten viser. Dørens tak er 1000.
#: SAMMENDRAGET OG ALDERSFORDELINGEN TELLER LIKEVEL ALT.
MAKS_FORDRINGER = 200

MAKS_KUNDE_REF = 300
MAKS_FAKTURANUMMER = 100
MAKS_NAVN = 200
MAKS_BEGRUNNELSE = 2000

#: Ytterpunktet for et beløp i øre. 10^13 øre er hundre milliarder kroner
#: — godt over enhver reell fordring, godt under `BIGINT`s tak, og godt
#: under `Number.MAX_SAFE_INTEGER` slik at tallet kommer helt fram
#: gjennom JSON til flaten (101s form).
MAKS_ORE = 10 ** 13

#: Maks antall trinn i en purreplan. Ti trinn er en eskaleringstrapp
#: ingen kunde ville forstått, og et tak hindrer at ett kall skriver
#: tusen rader.
MAKS_TRINN = 10
MAKS_DOGN = 3650

#: SPEIL av CHECK-en i 104. Speilet finnes for at feilen skal bli 400 og
#: ikke 409: en ukjent verdi er en feilformet forespørsel, ikke en
#: tilstand som sier nei. Dørens CHECK er fortsatt den bindende.
HANDLINGER = ("paaminnelse", "purring", "inkassovarsel", "inkasso")

_M23_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m23:fordring")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M23_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid,
            detalj=f"«{felt}»: mangler eller er ugyldig"))
    return verdi


def _valgfri_tekst(kropp, felt: str, rid, maks: int) -> str | None:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    if not isinstance(verdi, str) or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid,
            detalj=f"«{felt}»: mangler eller er ugyldig"))
    return verdi.strip() or None


def _ore(kropp, felt: str, rid, *, minst: int = 1) -> int:
    """Beløpet, og DEN ENESTE veien det kommer inn.

    `isinstance(x, bool)` er ikke pedanteri: i Python er `True` en `int`,
    og uten sjekken ville `{"belop_ore": true}` blitt beløpet 1 øre. Et
    flyttall avvises og rundes ALDRI — en flate som rundet ville gjort
    misforståelsen til et tall i et krav mot en kunde.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid,
            detalj=f"«{felt}»: mangler eller er ugyldig"))
    if not (minst <= verdi < MAKS_ORE):
        raise _Avbrudd(_feil("request_feilformet", rid,
            detalj=f"«{felt}»: mangler eller er ugyldig"))
    return verdi


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get(navn)))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


def _trinnliste(kropp, rid) -> str:
    """Purreplanens trinn, validert HER og serialisert for døren.

    VALIDERINGEN ER FLATENS, IKKE BASENS: døren tar imot en `jsonb` og
    lar CHECK-ene avvise det som ikke holder mål — men en liste med en
    ukjent `handling` ville da blitt en `check_violation` uten et ord om
    hvilket trinn som var galt. Her blir den 400 med en gang.

    REKKEFØLGEN VALIDERES IKKE HER. At trinnene må stige i tid er
    vaktens dom (104), og den gjelder da også for direkte DML — en
    dublettsjekk her ville vært en andre sannhet å holde i takt.
    """
    from .policyadmin_http import _Avbrudd, _feil
    trinn = kropp.get("trinn")
    if not isinstance(trinn, list) or not (1 <= len(trinn) <= MAKS_TRINN):
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for t in trinn:
        if not isinstance(t, dict):
            raise _Avbrudd(_feil("request_feilformet", rid))
        navn = t.get("navn")
        dogn = t.get("dogn_etter_forfall")
        handling = t.get("handling")
        gebyr = t.get("gebyr_ore", 0)
        if not isinstance(navn, str) or not navn.strip() \
                or len(navn) > MAKS_NAVN:
            raise _Avbrudd(_feil("request_feilformet", rid))
        if not isinstance(dogn, int) or isinstance(dogn, bool) \
                or not (0 <= dogn <= MAKS_DOGN):
            raise _Avbrudd(_feil("request_feilformet", rid))
        if handling not in HANDLINGER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        if not isinstance(gebyr, int) or isinstance(gebyr, bool) \
                or not (0 <= gebyr < MAKS_ORE):
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut.append({"navn": navn, "dogn_etter_forfall": dogn,
                   "handling": handling, "gebyr_ore": gebyr})
    return json.dumps(ut)


MAKS_MOTTAKER_EPOST = 254
_AAD_MOTTAKER = b"m23:mottaker"
_SVAR_TIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _mottaker(conn, tenant, kropp, rid):
    """Mottakerens e-post: normalisert, MASKERT for flaten og KRYPTERT
    for basen. Returnerer (None, None, None, None) når feltet ikke er
    med — mottakeren er valgfri ved registrering, og et fravær er et
    fravær, ikke en tom streng."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get("mottaker_epost")
    if verdi is None:
        return None, None, None, None, None
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid,
            detalj="«mottaker_epost»: mangler eller er ugyldig"))
    adresse = verdi.strip().lower()
    lokal, skille, domene = adresse.partition("@")
    if (not skille or not lokal or "." not in domene or "@" in domene
            or len(adresse) > MAKS_MOTTAKER_EPOST
            or any(c.isspace() for c in adresse)):
        raise _Avbrudd(_feil("request_feilformet", rid,
            detalj="«mottaker_epost»: må være én e-postadresse"))
    # MASKEN, M-44s form: første tegn, fire stjerner, hele domenet.
    maske = lokal[0] + "****@" + domene
    # HASHEN, tenant-saltet: likhet uten klartekst, og ikke et oppslagsverk
    # på tvers av tenanter.
    import hashlib
    hasj = hashlib.sha256(f"{tenant}\n{adresse}".encode("utf-8")).hexdigest()
    from db import kryptering
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    ct, nonce = kryptering.krypter(dek, {"e": adresse}, tenant, key_id,
                                   ekstra_aad=_AAD_MOTTAKER)
    return maske, ct, nonce, key_id, hasj


_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
    psycopg.errors.InsufficientPrivilege,
)


def _doerfeil(e, rid):
    from .policyadmin_http import _Avbrudd, _doerdetalj, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        return _Avbrudd(_feil("idempotenskonflikt", rid,
                              detalj=_doerdetalj(e)))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404,
                              detalj=_doerdetalj(e)))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: overbetaling, en eskalering på et
        # avsluttet krav, et trinn planen ikke har, en ettergivelse uten
        # begrunnelse. Kroppen ER velformet — det er innholdskravet
        # basen håndhever som sier nei.
        return _Avbrudd(_feil("fordring_ulovlig_tilstand", rid, 409,
                              detalj=_doerdetalj(e)))
    if isinstance(e, _DOERDOMMER):
        # TRINNHOPPET LANDER HER, via vaktens `insufficient_privilege`,
        # og bakoverplanen via `check_violation`. Begge er TILSTANDER som
        # sier nei, ikke feilformede kropper.
        return _Avbrudd(_feil("fordring_ulovlig_tilstand", rid, 409,
                              detalj=_doerdetalj(e)))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Fordringsflatens tilstand i én transaksjon, gjennom fire lesedører.

    SAMMENDRAGET OG ALDERSFORDELINGEN KOMMER FRA SINE EGNE DØRER og
    telles over ALT — ikke fra den avkortede listen. En flate som regnet
    aldersfordelingen fra de 200 viste radene ville tegnet et diagram om
    et utvalg og kalt det virksomhetens utestående.
    """
    s = conn.execute("SELECT * FROM m23_fordringsstatus(%s)",
                     (tenant,)).fetchone()
    aldersfordeling = [
        {"botte": r[0], "antall": r[1], "ore": r[2]}
        for r in conn.execute("SELECT * FROM m23_aldersfordeling(%s)",
                              (tenant,)).fetchall()]
    fordringer = [
        {"fordring_id": str(r[0]), "kunde_ref": r[1],
         "fakturanummer": r[2], "belop_ore": r[3], "betalt_ore": r[4],
         "rest_ore": r[5], "utstedt": r[6].isoformat(),
         "forfall": r[7].isoformat(), "dogn_over_forfall": r[8],
         "status": r[9], "trinn": r[10], "trinn_navn": r[11],
         "moden_for_trinn": r[12], "apne_funn": list(r[13] or ()),
         "mottaker_maske": r[14]}
        for r in conn.execute("SELECT * FROM m23_fordringene(%s,%s)",
                              (tenant, MAKS_FORDRINGER)).fetchall()]
    purreplan = [
        {"versjon": r[0], "trinn_nr": r[1], "navn": r[2],
         "dogn_etter_forfall": r[3], "handling": r[4], "gebyr_ore": r[5]}
        for r in conn.execute("SELECT * FROM m23_purreplanen(%s)",
                              (tenant,)).fetchall()]
    return {
        "sammendrag": {
            "apne": s[0], "apent_ore": s[1], "forfalte": s[2],
            "forfalt_ore": s[3], "i_purring": s[4], "apne_funn": s[5],
            "har_purreplan": s[6],
            # LISTEN ER AVKORTET, OG FLATEN SKAL KUNNE SI DET.
            "vist": len(fordringer)},
        "aldersfordeling": aldersfordeling,
        "fordringer": fordringer,
        "purreplan": purreplan}


def fordringsbilde(tjeneste, request):
    """GET /v1/fordring (okonomi:read) — tenantens eget utestående."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "okonomi:read", _fn)


def hendelsene_endepunkt(tjeneste, request):
    """GET /v1/fordring/{fordring_id}/hendelser (okonomi:read).

    Historikken er append-only i basen, og dette er veien til å lese den:
    hver innbetaling og hver trinnflytting, med hvem og når. Det er
    svaret på «hvorfor står denne på inkassovarsel», og uten det er
    trinnet et tall uten opphav.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        fid = _sti_uuid(request, "fordring_id", rid)
        rader = conn.execute("SELECT * FROM m23_hendelsene(%s,%s)",
                             (auth.tenant, fid)).fetchall()
        return kanonisk_json({
            "fordring_id": str(fid),
            "hendelser": [
                {"hendelse_id": str(r[0]), "art": r[1],
                 "belop_ore": r[2], "trinn": r[3], "begrunnelse": r[4],
                 "inntruffet": r[5].isoformat(),
                 "opprettet": r[6].isoformat(), "opprettet_av": r[7]}
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
        sql, args, svar, felt = bygg(conn, tenant, bid, nokkel, kropp, rid,
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


def purreplan_endepunkt(tjeneste, request):
    """POST /v1/fordring/purreplan (bestilling:opprett, idem).

    PURRETRINNENE ER TENANTENS EGNE, ikke en konstant i koden. «Etter 14
    døgn purrer vi» er en forretningsbeslutning, og et trinn kodet inn
    ville vært en fullmakt modulen ga seg selv.

    ÆRLIG OM HVA DETTE IKKE ER: planen går ikke gjennom M-1s policymotor.
    M-1 er dokumentbasert (utkast → attestering → aktivering) og har
    ingen fasilitet for en tenant-innstilling. Invarianten
    `purretrinn_hardkodet` er oppfylt i den forstand som betyr noe —
    tenanten eier og fører verdiene, og de er revisjonssporet — men
    koblingen til M-1 står igjen som et NAVNGITT gap.

    HELE SETTET I ETT KALL, og versjonen øker. En dør som la til ett
    trinn om gangen ville latt planen stå halvferdig, og sveipen ville
    vurdert fordringer mot den i det vinduet.
    """
    def bygg(_conn, tenant, bid, _nokkel, kropp, rid, _request):
        trinn = _trinnliste(kropp, rid)
        return ("SELECT m23_sett_purreplan(%s,%s::jsonb,%s)",
                (tenant, trinn, bid), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_endepunkt(tjeneste, request):
    """POST /v1/fordring (bestilling:opprett, idem)."""
    def bygg(conn, tenant, bid, nokkel, kropp, rid, _request):
        kunde = _tekst(kropp, "kunde_ref", rid, MAKS_KUNDE_REF)
        nummer = _tekst(kropp, "fakturanummer", rid, MAKS_FAKTURANUMMER)
        belop = _ore(kropp, "belop_ore", rid)
        utstedt = _tekst(kropp, "utstedt", rid, 32)
        forfall = _tekst(kropp, "forfall", rid, 32)
        maske, ct, nonce, key_id, hasj = _mottaker(conn, tenant, kropp, rid)
        fid = _utled("fordring", tenant, nokkel)
        return ("SELECT m23_registrer_fordring(%s,%s,%s,%s,%s,%s::date,"
                "                              %s::date,%s,%s,%s,%s,%s,%s)",
                (tenant, fid, kunde, nummer, belop, utstedt, forfall, bid,
                 maske, ct, nonce, key_id, hasj),
                {"fordring_id": str(fid), "mottaker_maske": maske}, "ny")
    return _skriv(tjeneste, request, bygg)


def mottaker_endepunkt(tjeneste, request):
    """POST /v1/fordring/{fordring_id}/mottaker (bestilling:opprett, idem).

    ADRESSEN PURRINGEN SKAL TIL (146, ARC B). Kroppen bærer
    `mottaker_epost`; svaret og listen bærer bare MASKEN. Adressen
    krypteres her med tenantens DEK, og basen ser aldri klartekst.
    Kan settes og rettes så lenge fordringen er åpen.
    """
    def bygg(conn, tenant, bid, nokkel, kropp, rid, request_):
        fid = _sti_uuid(request_, "fordring_id", rid)
        if kropp.get("mottaker_epost") is None:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid,
                detalj="«mottaker_epost»: mangler eller er ugyldig"))
        maske, ct, nonce, key_id, hasj = _mottaker(conn, tenant, kropp, rid)
        return ("SELECT m23_sett_mottaker(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, fid, maske, ct, nonce, key_id, hasj, bid),
                {"fordring_id": str(fid), "mottaker_maske": maske},
                "endret")
    return _skriv(tjeneste, request, bygg)


def avsender_endepunkt(tjeneste, request):
    """POST /v1/fordring/avsender (bestilling:opprett, idem).

    TENANTENS AVSENDERPROFIL (149, ARC B): navnet purringen sendes i og
    adressen kunden kan svare til. E-posten går fra husets SMTP
    (eiervedtaket 9/9, valg 3) — profilen er det kunden ser.
    """
    def bygg(_conn, tenant, bid, _nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "avsender_navn", rid, 120)
        svar_til = _valgfri_tekst(kropp, "svar_til", rid, 254)
        # SAMME regel som CHECK-en i 149 — én form, to steder som er enige.
        if svar_til is not None and not _SVAR_TIL.fullmatch(svar_til):
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid,
                detalj="«svar_til»: ikke en e-postadresse"))
        return ("SELECT m23_sett_avsender(%s,%s,%s,%s)",
                (tenant, navn.strip(), svar_til, bid),
                {"avsender_navn": navn.strip(), "svar_til": svar_til},
                "endret")
    return _skriv(tjeneste, request, bygg)


def utforelse_for_sending(conn, tenant: str, fordring_id) -> dict:
    """Det claim-veien gir purringsmodulen ved siden av payloaden (149).

    ADRESSEN DEKRYPTERES HER, i API-ets tillit, med tenantens DEK — og
    lever bare i claim-svaret, aldri i oppdraget. Alt annet er tallene
    e-posten trenger og tenantens avsenderprofil. En `hindring` betyr at
    modulen skal kvittere `feilet` uten å sende: fordringen er borte,
    avsluttet, uten adresse, eller adressen lar seg ikke lese.
    """
    if not fordring_id:
        return {"hindring": "fordring_ukjent"}
    rad = conn.execute("SELECT * FROM m23_for_sending(%s,%s)",
                       (tenant, fordring_id)).fetchone()
    if rad is None:
        return {"hindring": "fordring_ukjent"}
    (status, kunde_ref, fakturanummer, rest_ore, forfall, trinn,
     neste_trinn, handling_trinn, gebyr_ore, maske, ct, nonce, key_id,
     avsender_navn, svar_til) = rad
    if status != "apen":
        return {"hindring": "fordring_avsluttet"}
    if ct is None or nonce is None or key_id is None:
        return {"hindring": "mottaker_mangler"}
    from db import kryptering
    nok = conn.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s"
        " AND key_id=%s", (tenant, key_id)).fetchone()
    if nok is None or nok[0] is None:
        return {"hindring": "mottaker_uleselig"}
    try:
        dek = kryptering._pakk_ut((key_id, nok[0]), tenant)[1]
        adresse = kryptering.dekrypter(dek, bytes(ct), bytes(nonce), tenant,
                                       key_id, ekstra_aad=_AAD_MOTTAKER)["e"]
    except Exception:                                   # noqa: BLE001
        return {"hindring": "mottaker_uleselig"}
    return {"mottaker_epost": adresse, "mottaker_maske": maske,
            "kunde_ref": kunde_ref, "fakturanummer": fakturanummer,
            "rest_ore": int(rest_ore), "forfall": forfall.isoformat(),
            "trinn": int(trinn),
            "neste_trinn": (int(neste_trinn) if neste_trinn is not None
                            else None),
            "handling_trinn": handling_trinn,
            "gebyr_ore": int(gebyr_ore or 0),
            "avsender_navn": avsender_navn, "svar_til": svar_til}


def bokfor_purring_sendt(conn, tenant: str, oppdrag_id: int,
                         kvittering: dict, aktor: str) -> dict:
    """Kvitteringens vei tilbake til registeret (150). -> {bokfort,
    flyttet, trinn_naa} eller {avvik: <grunn>} — aldri et unntak ut:
    kalleren har alt en sendt e-post å stå inne for."""
    import psycopg
    ressurs = str(kvittering.get("ressurs_id") or "")
    if not ressurs.startswith("fordring:"):
        return {"avvik": "ressurs_id_ikke_fordring"}
    try:
        fid = uuidlib.UUID(ressurs.split(":", 1)[1])
        trinn = int(kvittering.get("trinn"))
        if isinstance(kvittering.get("trinn"), bool) or trinn < 1:
            raise ValueError("trinn")
    except (ValueError, TypeError):
        return {"avvik": "kvittering_uten_gyldig_trinn"}
    # RESSURSEN MÅ VÆRE OPPDRAGETS (CodeRabbit på PR 5). Kvitteringen er
    # signert av modulen, men modulen kunne navngi en annen fordring enn
    # den oppdraget gjaldt — og døra kan ikke lese oppdraget (kryptert,
    # migrators tabell). API-et kan: payloaden dekrypteres her, som ved
    # claim, og fordringen sammenlignes FØR noe bokføres.
    from db import kryptering
    orad = conn.execute(
        "SELECT payload_kryptert, key_id, nonce FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (tenant, int(oppdrag_id))).fetchone()
    if orad is None:
        return {"avvik": "oppdrag_ukjent"}
    nok = conn.execute(
        "SELECT wrapped_dek FROM tenant_nokler WHERE tenant=%s"
        " AND key_id=%s", (tenant, orad[1])).fetchone()
    try:
        dek = kryptering._pakk_ut((orad[1], nok[0]), tenant)[1]
        payload = kryptering.dekrypter(dek, bytes(orad[0]), bytes(orad[2]),
                                       tenant, orad[1])
    except Exception:                                   # noqa: BLE001
        return {"avvik": "oppdrag_uleselig"}
    if str(payload.get("fordring_id") or "") != str(fid):
        return {"avvik": "ressurs_avvik"}
    if int(payload.get("trinn") or 0) != trinn:
        return {"avvik": "trinn_avvik"}
    sendt_ts = kvittering.get("sendt_ts")
    try:
        sendt = (datetime.fromisoformat(str(sendt_ts).replace("Z", "+00:00"))
                 if sendt_ts else None)
        if sendt is not None and sendt.tzinfo is None:
            sendt = None
    except ValueError:
        sendt = None
    try:
        with conn.transaction():
            rad = conn.execute(
                "SELECT bokfort, flyttet, trinn_naa FROM m23_purring_sendt("
                "%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, fid, trinn, int(oppdrag_id), sendt,
                 str(kvittering.get("malversjon") or "")[:64] or None,
                 str(kvittering.get("mottaker_maske") or "")[:254] or None,
                 aktor)).fetchone()
    except psycopg.Error as e:
        return {"avvik": f"dor_nektet:{type(e).__name__}"}
    if rad is None:
        return {"avvik": "dor_uten_svar"}
    return {"bokfort": bool(rad[0]), "flyttet": bool(rad[1]),
            "trinn_naa": int(rad[2])}


def betaling_endepunkt(tjeneste, request):
    """POST /v1/fordring/{fordring_id}/betaling (bestilling:opprett, idem).

    OVERBETALING AVVISES. Er det betalt mer enn skyldig, er differansen en
    tilgodehavende — et annet register, og ikke noe dette skal late som
    det håndterer.

    FULLT BETALT LUKKER KRAVET i samme transaksjon. Uten det ville en
    oppgjort fordring fortsatt stått som åpen og blitt et funn i natt.
    """
    def bygg(_conn, tenant, bid, nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "fordring_id", rid)
        belop = _ore(kropp, "belop_ore", rid)
        inntruffet = _tekst(kropp, "inntruffet", rid, 32)
        hid = _utled("betaling", tenant, nokkel)
        return ("SELECT m23_registrer_betaling(%s,%s,%s,%s,%s::date,%s)",
                (tenant, hid, fid, belop, inntruffet, bid),
                {"fordring_id": str(fid), "hendelse_id": str(hid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def neste_trinn_endepunkt(tjeneste, request):
    """POST /v1/fordring/{fordring_id}/neste-trinn (bestilling:opprett,
    idem).

    INGEN TRINNPARAMETER, og det er dommen: døren flytter til NESTE
    trinn. Et API som lot kalleren be om «sett trinn 3» ville invitert
    til nettopp det hoppet vakten finnes for å hindre — og for kunden er
    forskjellen mellom en påminnelse og et inkassovarsel hele saken.

    ET MENNESKE FLYTTER TRINNET. Sveipen kunne — den vet hvilke
    fordringer som er modne — men en jobb som eskalerer om natten er
    nøyaktig den fullmakten v1 ikke gir seg selv.
    """
    def bygg(_conn, tenant, bid, nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "fordring_id", rid)
        begrunnelse = _valgfri_tekst(kropp, "begrunnelse", rid,
                                     MAKS_BEGRUNNELSE)
        hid = _utled("trinn", tenant, nokkel)
        return ("SELECT m23_neste_trinn(%s,%s,%s,%s,%s)",
                (tenant, hid, fid, begrunnelse, bid),
                {"fordring_id": str(fid)}, "trinn")
    return _skriv(tjeneste, request, bygg)


def ettergi_endepunkt(tjeneste, request):
    """POST /v1/fordring/{fordring_id}/ettergi (bestilling:opprett, idem).

    Å slette et krav uten å si hvorfor er den ene handlingen ingen kan
    etterprøve senere. Begrunnelsen kreves av døren og av CHECK-en;
    her sjekkes den ikke — et forsøk blir 409 fordi BASEN nektet.
    """
    def bygg(_conn, tenant, bid, nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "fordring_id", rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        hid = _utled("ettergi", tenant, nokkel)
        return ("SELECT m23_ettergi(%s,%s,%s,%s,%s)",
                (tenant, hid, fid, begrunnelse, bid),
                {"fordring_id": str(fid)}, "ny")
    return _skriv(tjeneste, request, bygg)
