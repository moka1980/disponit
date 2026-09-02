"""M-34 compliance- og sertifiseringsagentens API (migrasjon 100).

Fire endepunkter: én leseflate og tre skriveveier. Ingen av dem rører en
tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_compliance_eier`-eid SECURITY DEFINER-dør i 100, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7, 090/091/096-formen). At en
kontroll ikke kan stå som «oppfylt» uten evidenshenvisning og dato, at en
kontroll ikke kan registreres uten eier, og at «ikke relevant» koster en
begrunnelse, er derfor egenskaper ved BASEN og ikke ved denne filen — og
en flate som sjekket det selv ville vært en andre sannhet å omgå.

MODULEN SENDER IKKE INN NOE. Det er v1-dommen, og den er en EGENSKAP VED
DENNE FILEN og ikke bare en intensjon: her finnes ingen HTTP-klient,
ingen import av `ssrf`/`httpx`/`urllib`, ingen mottakeradresse og ingen
utgående vei. Et compliance-verktøy som sender inn noe på egen hånd
skaper en påstand ingen har lest, og porten
`modulen_sendte_inn_evidens` i `test_m34_compliance.py` måler fraværet
statisk (AST) og funksjonelt.

SCOPENE ER GJENBRUKT, IKKE NYE.

  * LESINGEN bærer `security:read`. Det er PR-008 §1s ops/compliance-
    scope på en TENANTBUNDET brukersesjon, og `autorisasjon.py` beskriver
    rollen `sikkerhet` med nøyaktig de ordene («Compliance/ops»).
    Kontrollregisteret er den flaten det scopet ble laget for. Det er
    også en snevrere krets enn `decisions:read` — og med vilje:
    avviksbeskrivelser og evidenshenvisninger er revisjonsmateriale, og
    en `godkjenner` eller `policyforvalter` har ingenting i dem å gjøre.
    Samme presedens som `modellstyring`, `driftstatus`, `datakvalitet` og
    `retensjon`, som alle er BASISRUTER bak dette scopet.
  * SKRIVINGEN bærer `bestilling:opprett` — scopet `admin` allerede har,
    og som allerede står i `BROWSER_MUTASJONSSCOPES`. Registrering,
    etterprøving og ikke-relevant-beslutningen er BESTILLINGER i
    plattformens forstand (M-21-presedensen, 096). Et nytt scope skal
    ikke oppstå av vane.

SP-2 PÅ BEGGE SKRIVEVEIENE SOM FØDER EN RAD (m35/096-formen): `kontroll_id`
og `etterproving_id` utledes deterministisk av Idempotency-Key-en, så en
tapt respons + nytt klikk GJENSPILLER i stedet for å føde en kontroll —
eller, verre, en etterprøving — til. En dobbelt bokført etterprøving ville
vært et revisjonsspor som lyver om hvor mange ganger noe faktisk ble
kontrollert.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

#: Taket for hvor mange kontroller leseflaten viser. Registerets tak er
#: dørens (500); dette er flatens, og det er en annen begrunnelse: et
#: register med tusen kontroller skal ikke kunne gjøre ett HTTP-svar til
#: en nedlasting.
MAKS_KONTROLLER = 200

#: Lengdegrensene på kundens egen tekst. `krav_ref` er en HENVISNING (et
#: kontrollnummer, en paragraf) og ikke et sammendrag; `evidens_ref` er en
#: referanse til noe som finnes et annet sted. Begge er korte av natur.
#: Beskrivelsen, avviket og ikke-relevant-begrunnelsen er de tre som
#: faktisk er setninger et menneske skal lese senere.
MAKS_RAMMEVERK = 200
MAKS_VERSJON = 60
MAKS_KRAV_REF = 200
MAKS_BESKRIVELSE = 2000
MAKS_EVIDENS_REF = 500
MAKS_AVVIK = 4000
MAKS_BEGRUNNELSE = 2000

#: Ytterpunktene for etterprøvingsintervallet. Nedre grense er dørens
#: (> 0); den øvre er flatens og sier at et intervall på mer enn ti år
#: ikke er en frekvens — det er en måte å aldri bli et funn på.
MAKS_ETTERPROVING_DOGN = 3650

#: De lovlige utfallene — SPEIL av CHECK-en i 100. Speilet finnes for at
#: feilen skal bli 400 og ikke 409: en ukjent verdi er en feilformet
#: forespørsel, ikke en tilstand som sier nei. Dørens CHECK er fortsatt
#: den bindende.
UTFALL = ("oppfylt", "avvik")

#: SP-2-navnerommene for de deterministisk utledede id-ene (m8/m35/096-
#: formen). To navnerom, ikke ett: den samme Idempotency-Key-en kan i
#: prinsippet brukes mot to ULIKE endepunkter, og da skal den ikke gi
#: samme UUID i to tabeller.
_M34_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m34:compliance")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M34_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _valgfri_tekst(kropp, felt: str, rid, maks: int) -> str | None:
    """Et felt som kan mangle helt, men ikke kan være søppel når det er
    der. `None` og «tom streng» kollapser til `None`: en versjon ingen
    skrev og en versjon noen skrev som mellomrom er samme fravær."""
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    if not isinstance(verdi, str) or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi.strip() or None


def _kontroll_id(request, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get("kontroll_id")))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


#: De ENESTE feilklassene dørene bruker som DOM (089/096s liste, samme
#: begrunnelse): en tapt forbindelse eller en manglende rettighet på selve
#: funksjonen er ikke «tilstanden nekter».
#:
#: `InsufficientPrivilege` står her, til forskjell fra 096s liste — og det
#: er en dom, ikke en slurv. M-34s vakt feller sin SKARPESTE dom med
#: nettopp den ERRCODE-en: evidenshenvisningen som ikke svarer til en
#: faktisk etterprøving. Den veien kan ikke nås gjennom dørene (de skriver
#: historikken først), så en `insufficient_privilege` herfra er i praksis
#: alltid vaktens — men skulle den likevel være et manglende grant, er 409
#: fortsatt et ærligere svar enn 200: noe ble ikke skrevet.
_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
    psycopg.errors.InsufficientPrivilege,
)


def _doerfeil(e, rid):
    """Dørens ERRCODE → flatens feilkode. ÉN kilde, så alle tre
    skriveveiene svarer likt på samme dom. `None` = ikke en dom;
    kalleren kaster originalen videre."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: tom evidenshenvisning, manglende dato,
        # avvik uten beskrivelse, tom begrunnelse, eier som ikke er
        # medlem. Kroppen ER velformet — det er innholdskravet basen
        # håndhever som sier nei.
        return _Avbrudd(_feil("kontroll_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        return _Avbrudd(_feil("kontroll_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Hele leseflatens tilstand i én transaksjon, gjennom lesedøren.

    Rekkefølgen er DØRENS (mest over fristen først, `ikke_relevant`
    sist) — flaten sorterer ikke om. `dogn_over_frist` er regnet i BASEN,
    i samme skann som raden, nettopp for at flaten ikke skal trekke to
    datoer fra hverandre.
    """
    kontroller = [
        {"kontroll_id": str(r[0]), "rammeverk": r[1],
         "rammeverk_versjon": r[2], "krav_ref": r[3], "beskrivelse": r[4],
         "eier_bruker_id": r[5], "eier_navn": r[6], "eier_aktiv": r[7],
         "etterproving_dogn": r[8],
         "sist_etterprovd": r[9].isoformat() if r[9] is not None else None,
         "evidens_ref": r[10],
         "forfaller": r[11].isoformat() if r[11] is not None else None,
         "dogn_over_frist": r[12], "status": r[13],
         "ikke_relevant_begrunnelse": r[14],
         "antall_etterprovinger": r[15], "siste_utfall": r[16],
         "siste_avvik": r[17], "apne_funn": list(r[18] or ())}
        for r in conn.execute("SELECT * FROM m34_kontrollbilde(%s,%s)",
                              (tenant, MAKS_KONTROLLER)).fetchall()]
    return {"kontroller": kontroller}


def kontrollbilde(tjeneste, request):
    """GET /v1/compliance (security:read) — tenantens eget
    kontrollregister."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "security:read", _fn)


def registrer_endepunkt(tjeneste, request):
    """POST /v1/compliance/kontroll (bestilling:opprett, idem).

    EIEREN ER PÅKREVD I KROPPEN, ikke utledet av innloggingen. Den som
    skriver ned en kontroll er ofte ikke den som skal utføre den, og en
    flate som stille satte innloggeren som eier ville gjort «kontroller
    uten eier» sann på papiret og falsk i praksis. Døren avviser en eier
    som ikke er aktivt medlem av tenanten.

    RAMMEVERKET HAR INGEN EGEN OPPRETTELSE. Det oppstår når den første
    kontrollen under det registreres — et rammeverk uten en eneste
    kontroll er en tom overskrift.
    """
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        rammeverk = _tekst(kropp, "rammeverk", rid, MAKS_RAMMEVERK)
        versjon = _valgfri_tekst(kropp, "rammeverk_versjon", rid,
                                 MAKS_VERSJON)
        krav_ref = _tekst(kropp, "krav_ref", rid, MAKS_KRAV_REF)
        beskrivelse = _tekst(kropp, "beskrivelse", rid, MAKS_BESKRIVELSE)
        eier = _tekst(kropp, "eier_bruker_id", rid, 128)
        dogn = kropp.get("etterproving_dogn")
        if not isinstance(dogn, int) or isinstance(dogn, bool) \
                or not (1 <= dogn <= MAKS_ETTERPROVING_DOGN):
            raise _Avbrudd(_feil("request_feilformet", rid))
        kid = _utled("kontroll", tenant, nokkel)
        try:
            ny = conn.execute(
                "SELECT m34_registrer_kontroll(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, kid, rammeverk, versjon, krav_ref, beskrivelse,
                 eier, dogn, _bid)).fetchone()[0]
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        # `ny=false` er et STILLE JA (SP-2): samme nøkkel og samme innhold
        # ga samme kontroll. Kalleren får den samme id-en, og ingenting
        # ble skrevet to ganger.
        return _ok({"kontroll_id": str(kid), "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def etterproving_endepunkt(tjeneste, request):
    """POST /v1/compliance/kontroll/{kontroll_id}/etterproving
    (bestilling:opprett, idem).

    EVIDENSHENVISNINGEN HÅNDHEVES IKKE HER. Døren krever den, CHECK-en i
    100 krever den, vakten krever at den svarer til en faktisk rad, og
    flaten krever den i skjemaet — men den bindende er basens. Et forsøk
    uten henvisning blir 409 fordi BASEN nektet, ikke fordi API-et
    sjekket.
    """
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        nokkel = _krev_idem(request, rid)
        kropp = _kropp(request)
        kid = _kontroll_id(request, rid)
        utfort = _tekst(kropp, "utfort", rid, 32)
        utfort_av = _tekst(kropp, "utfort_av_bruker_id", rid, 128)
        evidens = _tekst(kropp, "evidens_ref", rid, MAKS_EVIDENS_REF)
        utfall = kropp.get("utfall")
        if utfall not in UTFALL:
            raise _Avbrudd(_feil("request_feilformet", rid))
        avvik = _valgfri_tekst(kropp, "avviksbeskrivelse", rid, MAKS_AVVIK)
        eid = _utled("etterproving", tenant, nokkel)
        try:
            ny = conn.execute(
                "SELECT m34_registrer_etterproving(%s,%s,%s,%s::date,%s,"
                "                                  %s,%s,%s,%s)",
                (tenant, eid, kid, utfort, utfort_av, evidens, utfall,
                 avvik, _bid)).fetchone()[0]
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            # Datoen er kallerens tekst, og castet skjer i basen. En
            # ulesbar eller umulig dato er 400, ikke 409: det er KROPPEN
            # som er feil, ikke tilstanden. Fanget som to navngitte
            # klasser og ikke som `DataError`, fordi `DataError` også
            # dekker 22023 — dørenes egen dom, som skal bli 409.
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"kontroll_id": str(kid), "etterproving_id": str(eid),
                    "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def ikke_relevant_endepunkt(tjeneste, request):
    """POST /v1/compliance/kontroll/{kontroll_id}/ikke-relevant
    (bestilling:opprett, idem).

    Den BILLIGSTE utgangen av et kontrollregister, og derfor den som
    koster en setning: uten begrunnelsen ville «ikke relevant» vært en
    gratis vei ut av enhver kontroll, og registeret en liste over ting man
    kan klikke bort.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _krev_idem, _kropp,
                                   _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        _krev_idem(request, rid)
        kropp = _kropp(request)
        kid = _kontroll_id(request, rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        try:
            conn.execute("SELECT m34_marker_ikke_relevant(%s,%s,%s,%s)",
                         (tenant, kid, begrunnelse, _bid))
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"kontroll_id": str(kid), "ikke_relevant": True}, rid)

    return _med_conn(tjeneste, rid, kjor)
