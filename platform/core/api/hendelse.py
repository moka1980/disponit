"""M-29 sikkerhets- og hendelsesagentens API (migrasjon 137).

Fjorten endepunkter: syv leseveier og syv skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_hendelse_eier`-eid SECURITY DEFINER-dør i 137, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM ISOLERER EN KONTO ELLER ROTERER EN
HEMMELIGHET.

Det er ikke en utelatelse — det er v1-dommen. Fullmaktsmålene ligger
allerede i basen (`api_tokener`, `modultoken`, `brukersesjon`,
`tenant_pseudonymnokkel`, `brukeridentitet`), og verken modulrollen
eller sveiperollen har SÅ MYE SOM SELECT på noen av dem. En rute her
ville uansett ikke hatt noe å skrive med.

KLYNGENS DELTE DOM: EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN
ANGRES IKKE AV EN ROLLBACK.

Klynge 9s ytring kunne ikke tas tilbake fordi noen hadde LEST den.
Denne trenger ingen leser: kontoen er stengt, hemmeligheten er rullet,
og tokenet den gamle klienten holdt er dødt.

INGEN RUTE TAR IMOT EN KOMMANDOSTRENG.

`POST /playbook` tar en LISTE MED NAVN fra et lukket sett, og det
finnes ingen parameter som følger med et navn. Det er forskjellen på å
forby noe og å gjøre det uuttrykkelig: `isoler_konto` pluss en fri
parameterstreng ER en fri kommando med et pent navn.

KALLEREN OPPGIR ALDRI EN SCORE.

`POST /korreler` tar signalene og regelen; scoren regnes av `poeng *
treff` mot regelens egen terskel. En rute som tok imot en score ville
gjort «forklarbare regler» til pynt — forklaringen ville pekt på en
regel mens tallet kom fra et helt annet sted. 132s lærdom.

SIGNALKILDEN FILTRERER MODULENS EGET SPOR.

`GET /signaler` går gjennom `m29_signalkilden`, som utelater
`kilde = 'm29_hendelse'`. Uten det ville hver evidensrad blitt et nytt
signal, og hendelsen ville vokst av å bli sett på.

SCOPENE. LESING `security:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `security:read`: en sikkerhetshendelse navngir aktører og
peker på rader i revisjonsloggen. Samme vurdering som M-43 for
transkripsjoner og M-12 for tilgangsfunn.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_HENDELSER = 200
MAKS_FUNN = 200
MAKS_SIGNALER = 1000
MAKS_TEKST = 8000
MAKS_NAVN = 500
#: En regelbegrunnelse må være SKREVET. Seksten tegn er ikke en
#: kvalitetsgaranti — det er en terskel mot «mistenkelig» som eneste
#: forklaring på en score noen skal svare for. 133/135s tall.
MIN_TEKST = 16
#: Maks antall signaler i ett korrelasjonskall. Tenantens eget
#: `signaltak` er den EKTE grensen; denne er et vern mot en
#: importfeil som sender en hel logg.
MAKS_LISTE = 1000
#: Maks antall steg i én playbook. Speiler CHECK-en i 137.
MAKS_STEG = 100

#: SIGNALTYPENE. Speiler det lukkede settet i 137, og settet er
#: LUKKET fordi katalogen lover kilder som ikke finnes: ingen SIEM,
#: ingen IdP-kobling, ingen EDR, ingen skanner. Alle seks leses av
#: `revisjonslogg`, som er den ene applikasjonsloggen huset har.
SIGNALTYPER = ("policy_avslag_gjentatt", "unntak_gjentatt",
               "handling_utenfor_tidsvindu", "aktor_ukjent_for_tenant",
               "beslutning_uten_policyhash", "revisjonshull")

#: STEGTYPENE. Speiler `playbooksteg_stegtype_lukket` i 137.
#:
#: LEGG MERKE TIL AT DET IKKE FINNES EN `annet`-VERDI. Et lukket sett
#: med en åpen dør er et åpent sett — 116s
#: `klassifisering_utenfor_lukket_sett` anvendt på seg selv.
STEGTYPER = ("varsle_sikkerhetsansvarlig", "varsle_daglig_leder",
             "samle_tidslinje", "kartlegg_beroerte_data",
             "isoler_konto", "isoler_token", "roter_hemmelighet",
             "tilbakestill_sesjoner", "verifiser_gjenoppretting",
             "skriv_laeringsregel")

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 137.
KRAVGRENSER = {
    "korrelasjonsvindu_min": (1, 10080),
    "alvorsterskel": (1, 10000),
    "apen_hendelse_frist_dogn": (1, 365),
    "signaltak": (2, 1000),
}
REGELGRENSER = {
    "poeng": (1, 1000),
    "terskel_treff": (1, 1000),
}

_M29_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m29:hendelse")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M29_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    verdi = verdi.strip()
    if not verdi or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _lang_tekst(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_TEKST)
    if len(verdi) < MIN_TEKST:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


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
    """`krever_tofaktor` HAR INGEN DEFAULT, VERKEN HER ELLER I BASEN.

    Et forvalg her ville vært huset som bestemte hvilke inngrep som er
    «utvidede», og det er tenantens vurdering.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _stegliste(kropp, rid) -> list[str]:
    """STEGENE — NAVN FRA ET LUKKET SETT, UTEN ARGUMENTER.

    HER ER «INGEN FRI KOMMANDOKJØRING» EN GRAMMATIKK OG IKKE EN
    POLICY: lista bærer navn, og det finnes ikke noe felt et argument
    kunne ligget i. CHECK-en i 137 er den EKTE vakten; denne gir bare
    en lesbar feil, og faller den ene bort står den andre.

    EN PLAYBOOK UTEN STEG FORKLARER INGENTING. Den ville tilfredsstilt
    fremmednøkkelen i `inngrepsforslag` og sagt ingenting.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get("steg")
    if not isinstance(verdi, list) or not verdi:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if len(verdi) > MAKS_STEG:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for x in verdi:
        if x not in STEGTYPER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        # SAMME STEG TO GANGER ER EN SKRIVEFEIL, IKKE EN PLAN.
        # `playbooksteg_ett_av_hvert` fanger det i basen; her blir det
        # en lesbar feil framfor en UniqueViolation.
        if x in ut:
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut.append(x)
    return ut


def _dato(kropp, felt: str, rid) -> str:
    """ISO-dato som STRENG. Døra parser den; API-et validerer formen.

    En dato frosset ved import ville råtnet med kalenderen (124s
    CodeRabbit-funn), så `date.today()` står ingen steder her.
    """
    import datetime
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        datetime.date.fromisoformat(verdi)
    except ValueError as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e
    return verdi


def _dato_valgfri(kropp, felt: str, rid):
    if kropp.get(felt) is None:
        return None
    return _dato(kropp, felt, rid)


def _tidspunkt_str(verdi, rid) -> str:
    """ISO-tidspunkt MED sone.

    `fromisoformat` uten sone ville gitt en naiv tid, og en naiv tid i
    en `timestamptz` tolkes i serverens sone — altså et annet tidspunkt
    enn kalleren mente. For et signal er det forskjellen på «innenfor
    korrelasjonsvinduet» og «utenfor».
    """
    import datetime
    from .policyadmin_http import _Avbrudd, _feil
    if not isinstance(verdi, str):
        raise _Avbrudd(_feil("request_feilformet", rid))
    try:
        t = datetime.datetime.fromisoformat(verdi)
    except ValueError as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e
    if t.tzinfo is None:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _tidspunkt(kropp, felt: str, rid) -> str:
    return _tidspunkt_str(kropp.get(felt), rid)


def _signalene(kropp, rid):
    """DE TRE LISTENE ER ÉN TABELL SNUDD PÅ SIDEN.

    Er de ulike lange, ville døra stilltiende brukt den korteste og
    tapt signaler — en hendelse som mangler halvparten av grunnlaget
    sitt og ikke sier fra. Døra i 137 reiser det selv; her blir det en
    lesbar feil framfor et RAISE.
    """
    from .policyadmin_http import _Avbrudd, _feil
    refs = kropp.get("kilde_refs")
    aktorer = kropp.get("aktorer")
    tider = kropp.get("observert")
    for x in (refs, aktorer, tider):
        if not isinstance(x, list) or not x:
            raise _Avbrudd(_feil("request_feilformet", rid))
    if not (len(refs) == len(aktorer) == len(tider)):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if len(refs) > MAKS_LISTE:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut_refs = []
    for r in refs:
        if not isinstance(r, int) or isinstance(r, bool) or r < 0:
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut_refs.append(r)
    ut_aktorer = []
    for a in aktorer:
        if not isinstance(a, str) or not a.strip():
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut_aktorer.append(a.strip())
    ut_tider = [_tidspunkt_str(t, rid) for t in tider]
    return ut_refs, ut_aktorer, ut_tider


def _sti_uuid(request, navn: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(request.path_params[navn])
    except (KeyError, ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _kropp_uuid(kropp, felt: str, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(kropp.get(felt)))
    except (ValueError, AttributeError, TypeError) as e:
        raise _Avbrudd(_feil("request_feilformet", rid)) from e


def _doerfeil(e, rid):
    """Dørens NEKT er brukerens feil, ikke serverens.

    Uten denne ville hvert lovlige nekt i 137 blitt en 500 — og en 500
    på «regelen gjelder ikke i dag» er en feilmelding ingen kan handle
    på (121-135s form).

    `InsufficientPrivilege` STÅR I LISTEN fordi husets tenantvakt
    (`krev_tenantkontekst`, 038) reiser nettopp den når et kall ber om
    en annen tenants data.
    """
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, (psycopg.errors.RaiseException,
                      psycopg.errors.InvalidParameterValue,
                      psycopg.errors.InsufficientPrivilege,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.NotNullViolation,
                      psycopg.errors.NoDataFound,
                      psycopg.errors.UniqueViolation,
                      psycopg.errors.ForeignKeyViolation)):
        return _Avbrudd(_feil("request_feilformet", rid,
                              detalj=str(e).split("\n")[0]))
    return None


# ---------------------------------------------------------------------
# RADBYGGERNE — ÉN FORM, ETT STED.
# ---------------------------------------------------------------------

def _bilderad(r) -> dict:
    return {
        "apne_hendelser": r[0], "over_terskel": r[1], "regler": r[2],
        "playbooker": r[3], "forslag": r[4],
        # DET VIKTIGSTE TALLET I MODULEN, OG DET ER ALLTID 0.
        #
        # Det er ikke en telling av en kolonne — det er en påstand om at
        # kolonnen ikke finnes. `inngrepsforslag` har ingen `utfort_ts`,
        # ingen `resultat` og ingen `status` som kan bli `utfort`.
        # Blir dette noen gang noe annet enn 0, er v1-dommen brutt av
        # noen som la til en tabell.
        "inngrep_utfort": r[5],
        "apne_funn": r[6],
    }


def _hendelsesrad(r) -> dict:
    return {
        "hendelse_id": str(r[0]),
        # REGELEN SOM FORKLARER SCOREN, IKKE BARE ID-EN. En score uten
        # en lesbar forklaring er en påstand.
        "regel": r[1], "signaltype": r[2],
        "score": r[3],
        # ALVORET SLIK DET STO DA. Terskelen kan ha endret seg siden;
        # hendelsen skal ikke skifte karakter av det.
        "alvor": r[4],
        "signaler": r[5],
        # HVOR MANGE FORSLAG NOEN HAR SKREVET. Null på en hendelse over
        # terskel er sveipens viktigste funn.
        "forslag": r[6],
        "status": r[7], "oppdaget_ts": r[8].isoformat(),
    }


def _signalrad(r) -> dict:
    return {
        "signal_id": str(r[0]), "signaltype": r[1], "aktor": r[2],
        # PEKEREN TIL REVISJONSLOGGEN. Ingen fremmednøkkel: loggen er
        # husets og reapes etter sin egen frist.
        "kilde_ref": r[3],
        "observert_ts": r[4].isoformat(),
    }


def _kilderad(r) -> dict:
    """En kandidatrad fra revisjonsloggen — IKKE et signal ennå.

    Den blir et signal først når noen korrelerer den mot en regel.
    """
    return {
        "logg_id": r[0], "aktor": r[1], "kilde": r[2],
        "beslutning": r[3], "policy_content_hash": r[4],
        "ts": r[5].isoformat(),
    }


def _regelrad(r) -> dict:
    return {
        "regel_id": str(r[0]), "navn": r[1], "signaltype": r[2],
        "poeng": r[3], "terskel_treff": r[4],
        "gyldig_fra": r[5].isoformat(),
        "gyldig_til": r[6].isoformat() if r[6] else None,
        "gjelder_i_dag": r[7],
        # HVOR MANGE GANGER REGELEN FAKTISK HAR TRUFFET. En regelsamling
        # der ingen regel noen gang traff, er et deteksjonsapparat som
        # ikke detekterer.
        "brukt": r[8],
    }


def _playbookrad(r) -> dict:
    return {
        "playbook_id": str(r[0]), "navn": r[1],
        "naar_gjelder_den": r[2], "krever_tofaktor": r[3],
        # STEGENE, I REKKEFØLGE. Det er ikke en utførelsesplan — det er
        # en liste noen har skrevet ned på forhånd, og v1 utfører den
        # ikke.
        "steg": list(r[4] or []),
        "gjelder_i_dag": r[5], "godkjent_av": r[6],
        "foreslatt_ganger": r[7],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1], "referanse": r[2],
        "detalj": r[3],
        # HVEM SOM KAN LUKKE DET. Sveipens egne lukkes når tilstanden er
        # borte; et menneske som kunne lukket dem ville lukket en
        # måling og ikke en sak.
        "sveipens": r[4],
        "forst_sett": r[5].isoformat(),
    }


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124/127/128/130/132/133/134/135s form)."""
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m29_bildet(%s)",
                         (tenant,)).fetchone()),
        "hendelser": [_hendelsesrad(r) for r in conn.execute(
            "SELECT * FROM m29_hendelsene(%s,%s)",
            (tenant, MAKS_HENDELSER)).fetchall()],
        "regler": [_regelrad(r) for r in conn.execute(
            "SELECT * FROM m29_reglene(%s)", (tenant,)).fetchall()],
        "playbooker": [_playbookrad(r) for r in conn.execute(
            "SELECT * FROM m29_playbookene(%s)", (tenant,)).fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m29_hendelsesfunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def hendelsesbilde(tjeneste, request):
    """GET /v1/hendelse (security:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def hendelser_endepunkt(tjeneste, request):
    """GET /v1/hendelse/hendelser (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m29_hendelsene(%s,%s)",
                             (auth.tenant, MAKS_HENDELSER)).fetchall()
        return kanonisk_json(
            {"hendelser": [_hendelsesrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def tidslinje_endepunkt(tjeneste, request):
    """GET /v1/hendelse/{hendelse_id}/tidslinje (security:read).

    SIGNALENE EN HENDELSE HVILER PÅ. Uten dem er scoren et tall ingen
    kan etterprøve.
    """
    from .app import _rid
    from .lesing import _les, kanonisk_json
    rid = _rid(request)
    hid = _sti_uuid(request, "hendelse_id", rid)

    def _fn(conn, auth, rid_):
        rader = conn.execute("SELECT * FROM m29_tidslinjen(%s,%s)",
                             (auth.tenant, hid)).fetchall()
        return kanonisk_json(
            {"hendelse_id": str(hid),
             "signaler": [_signalrad(r) for r in rader],
             "request_id": rid_}, 200, {"x-request-id": rid_})

    return _les(tjeneste, request, "security:read", _fn)


def signaler_endepunkt(tjeneste, request):
    """GET /v1/hendelse/signaler?fra=... (security:read).

    KANDIDATRADENE FRA REVISJONSLOGGEN — ikke signaler ennå.

    Døra `m29_signalkilden` utelater `kilde = 'm29_hendelse'`. Uten det
    ville hver evidensrad modulen selv skrev blitt en kandidat, og
    hendelsen ville vokst av å bli sett på.
    """
    from .app import _rid
    from .lesing import _les, kanonisk_json
    from .policyadmin_http import _Avbrudd, _feil
    rid = _rid(request)
    fra = request.query_params.get("fra")
    if not fra:
        raise _Avbrudd(_feil("request_feilformet", rid))
    fra = _tidspunkt_str(fra, rid)

    def _fn(conn, auth, rid_):
        rader = conn.execute("SELECT * FROM m29_signalkilden(%s,%s)",
                             (auth.tenant, fra)).fetchall()
        return kanonisk_json(
            {"fra": fra,
             "kandidater": [_kilderad(r) for r in rader[:MAKS_SIGNALER]],
             "avkortet": len(rader) > MAKS_SIGNALER,
             "request_id": rid_}, 200, {"x-request-id": rid_})

    return _les(tjeneste, request, "security:read", _fn)


def regler_endepunkt(tjeneste, request):
    """GET /v1/hendelse/regler (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m29_reglene(%s)",
                             (auth.tenant,)).fetchall()
        return kanonisk_json(
            {"regler": [_regelrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def playbooker_endepunkt(tjeneste, request):
    """GET /v1/hendelse/playbooker (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m29_playbookene(%s)",
                             (auth.tenant,)).fetchall()
        return kanonisk_json(
            {"playbooker": [_playbookrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/hendelse/funn (security:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m29_hendelsesfunn(%s,%s)",
                             (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "security:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen seks av de syv skriveveiene deler.

    ÉN STÅR UTENFOR: `/korreler` returnerer SCOREN, ALVORET og ANTALL
    SIGNALER. Kalleren oppgir aldri en score — den regnes av regelens
    poeng mot dens egen terskel — så den må komme tilbake, sammen med
    alvoret den ga mot tenantens terskel.
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
        if felt is not None:
            svar = {**svar, felt: ut}
        return _ok(svar, rid)

    return _med_conn(tjeneste, rid, kjor)


def krav_endepunkt(tjeneste, request):
    """POST /v1/hendelse/krav (bestilling:opprett, idem).

    ALLE FIRE GRENSENE ER TENANTENS. Hvor mange poeng som gjør fire
    uskyldige signaler til en hendelse er en vurdering av hva det
    koster å ta feil begge veier — og en tannlegeklinikk og en bank
    tåler ikke det samme antallet falske alarmer.

    VERSJONEN TILDELES AV DØRA, IKKE HER OG IKKE AV KALLEREN. Raden er
    append-only: `sikkerhetshendelse.kravversjon` peker hit, og
    «terskelen som gjaldt» må kunne slås opp i ettertid.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        v = _heltall(kropp, "korrelasjonsvindu_min", rid,
                     *KRAVGRENSER["korrelasjonsvindu_min"])
        a = _heltall(kropp, "alvorsterskel", rid,
                     *KRAVGRENSER["alvorsterskel"])
        f = _heltall(kropp, "apen_hendelse_frist_dogn", rid,
                     *KRAVGRENSER["apen_hendelse_frist_dogn"])
        s = _heltall(kropp, "signaltak", rid,
                     *KRAVGRENSER["signaltak"])
        del nokkel
        return ("SELECT m29_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, v, a, f, s, bid), {}, "kravversjon")
    return _skriv(tjeneste, request, bygg)


def registrer_regel_endepunkt(tjeneste, request):
    """POST /v1/hendelse/regel (bestilling:opprett, idem).

    DET ENESTE SOM KAN GI POENG. «Scorer hendelse med FORKLARBARE
    REGLER» er vaktsetningens eget ord, og forklarbarheten er ikke en
    egenskap ved en modell — den er en fremmednøkkel.

    `signaltype` ER ET LUKKET SETT PÅ SEKS, og settet er lukket fordi
    katalogen lover kilder som ikke finnes. Å ta imot en type huset
    ikke kan observere ville vært å love en korrelasjon som aldri
    kommer.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        typ = _valg(kropp, "signaltype", rid, SIGNALTYPER)
        poeng = _heltall(kropp, "poeng", rid, *REGELGRENSER["poeng"])
        terskel = _heltall(kropp, "terskel_treff", rid,
                           *REGELGRENSER["terskel_treff"])
        besk = _lang_tekst(kropp, "begrunnelse", rid)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        rgl = _utled("regel", tenant, nokkel)
        return ("SELECT m29_registrer_regel(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, rgl, navn, typ, poeng, terskel, besk, fra, til,
                 bid),
                {"regel_id": str(rgl)}, None)
    return _skriv(tjeneste, request, bygg)


def avvikle_regel_endepunkt(tjeneste, request):
    """POST /v1/hendelse/regel/{regel_id}/avvikle.

    ENVEIS. En regel som kunne gjenopplives ville gjort «hvilken regel
    forklarte denne scoren» til et spørsmål med to svar.

    OG REGELEN SLETTES ALDRI: en score forklart av en regel som er
    BORTE er en score uten forklaring.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        r = _sti_uuid(request_, "regel_id", rid)
        til = _dato(kropp, "gyldig_til", rid)
        return ("SELECT m29_avvikle_regel(%s,%s,%s,%s)",
                (tenant, r, til, bid), {"regel_id": str(r)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_playbook_endepunkt(tjeneste, request):
    """POST /v1/hendelse/playbook (bestilling:opprett, idem).

    DEN FORHÅNDSDEFINERTE RESPONSEN, SOM ALDRI KJØRES I v1.

    `steg` er en LISTE MED NAVN fra et lukket sett, og det finnes ingen
    parameter som følger med et navn. Det er forskjellen på å forby noe
    og å gjøre det uuttrykkelig: `isoler_konto` pluss en fri
    parameterstreng ER en fri kommando med et pent navn.

    `krever_tofaktor` HAR INGEN DEFAULT. Et forvalg ville vært huset
    som bestemte hvilke inngrep som er «utvidede».
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        naar = _lang_tekst(kropp, "naar_gjelder_den", rid)
        tofaktor = _bool(kropp, "krever_tofaktor", rid)
        steg = _stegliste(kropp, rid)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        pid = _utled("playbook", tenant, nokkel)
        return ("SELECT m29_registrer_playbook(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, pid, navn, naar, tofaktor, steg, fra, til, bid),
                {"playbook_id": str(pid), "steg": steg}, None)
    return _skriv(tjeneste, request, bygg)


def korreler_endepunkt(tjeneste, request):
    """POST /v1/hendelse/korreler (bestilling:opprett, idem).

    MODULENS HOVEDDØR — og den som IKKE bruker `_skriv`, fordi svaret
    er mer enn en kvittering.

    KALLEREN OPPGIR ALDRI EN SCORE. Den regnes av `poeng * treff` mot
    regelens egen terskel, og alvoret av scoren mot tenantens. En rute
    som tok imot et av tallene ville gjort «forklarbare regler» til
    pynt.

    `kilde_refs` er `revisjonslogg.id`-er hentet gjennom
    `GET /signaler` — altså allerede filtrert for modulens eget spor.
    Samme loggrad teller ÉN gang: en gjentatt korrelasjonskjøring
    blåser ikke opp scoren.
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
        regel = _kropp_uuid(kropp, "regel_id", rid)
        kravversjon = _heltall(kropp, "kravversjon", rid, 1, 1_000_000)
        refs, aktorer, tider = _signalene(kropp, rid)
        hid = _utled("hendelse", tenant, nokkel)
        try:
            rad = conn.execute(
                "SELECT * FROM m29_korreler(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, hid, regel, kravversjon, refs, aktorer, tider,
                 bid)).fetchone()
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
        return _ok({"hendelse_id": str(hid), "score": rad[0],
                    "alvor": rad[1], "signaler": rad[2]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def foresla_inngrep_endepunkt(tjeneste, request):
    """POST /v1/hendelse/{hendelse_id}/forslag (bestilling:opprett, idem).

    DER VEIEN SLUTTER. Ruta skriver et FORSLAG som peker på en
    playbook; den utfører ingenting, og det finnes ingen rute som gjør
    det.

    `playbook_id` er PÅKREVD, og fremmednøkkelen i 137 gjør
    `inngrep_uten_playbook` umulig. At funnet står i det lukkede settet
    OG er umulig er beviset.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        h = _sti_uuid(request_, "hendelse_id", rid)
        p = _kropp_uuid(kropp, "playbook_id", rid)
        besk = _lang_tekst(kropp, "begrunnelse", rid)
        fid = _utled("forslag", tenant, nokkel)
        return ("SELECT m29_foresla_inngrep(%s,%s,%s,%s,%s,%s)",
                (tenant, fid, h, p, besk, bid),
                {"forslag_id": str(fid), "hendelse_id": str(h)}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_hendelse_endepunkt(tjeneste, request):
    """POST /v1/hendelse/{hendelse_id}/lukk (bestilling:opprett, idem).

    ET MENNESKE SIER AT SAKEN ER OVER. Sveipen lukker aldri en
    hendelse: en sveip som gjorde det ville sagt at saken var håndtert
    fordi ingen gjorde noe.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        h = _sti_uuid(request_, "hendelse_id", rid)
        grunn = _lang_tekst(kropp, "grunn", rid)
        return ("SELECT m29_lukk_hendelse(%s,%s,%s,%s)",
                (tenant, h, grunn, bid), {"hendelse_id": str(h)}, None)
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/hendelse/funn/{funn_id}/lukk (bestilling:opprett, idem).

    OG DØRA NEKTER FOR SVEIPENS EGNE. Et menneske som kunne lukket
    `apen_hendelse_over_frist` ville lukket en måling og ikke en sak —
    og de fire umulige kan ingen lukke, fordi ingen kan reise dem.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        del nokkel
        f = _sti_uuid(request_, "funn_id", rid)
        grunn = _lang_tekst(kropp, "grunn", rid)
        return ("SELECT m29_lukk_funn(%s,%s,%s,%s)",
                (tenant, f, grunn, bid), {"funn_id": str(f)}, None)
    return _skriv(tjeneste, request, bygg)
