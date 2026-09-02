"""M-30 personvern- og datasubjektagentens API (migrasjon 099).

Fem endepunkter: én leseflate og fire skriveveier. Ingen av dem rører en
tabell direkte — hver gjør nøyaktig ett kall mot en
`disponit_personvern_eier`-eid SECURITY DEFINER-dør i 099, og runtime har
ingen tabellrettigheter på registeret i det hele tatt (SP-7, 090/091/096-
formen). At en sak ikke kan lukkes uten et skrevet svar, at et avslag
koster en begrunnelse, at forlengelsen har et tak på to måneder og at en
sak ikke kan registreres uten eier er derfor egenskaper ved BASEN, ikke
ved denne filen — og en flate som sjekket det selv ville vært en andre
sannhet å omgå.

FILA SLETTER INGENTING, OG DEN BER INGEN OM Å SLETTE NOE. Det er
v1-dommen, og den er arkitektonisk: sletting eies av M-4s
retensjonsregnskap (093) og de seks reaperne som kjører. En andre
slettevei ved siden av dem er nøyaktig det M-4 ble bygget for å hindre.
Registeret PEKER på lagrene M-4 navngir; utførelsen gjøres av den som
eier lageret, og registeret er det som gjør at noen kan SE at den ble
gjort.

SCOPEVALGET ER `security:read` PÅ LESING, IKKE `decisions:read`, og det
er en dom — ikke en avskrift av M-21. Sammenligningen mot
`autorisasjon.py` er hele begrunnelsen:

  * `decisions:read` har ALLE kunderollene: `leser`, `sikkerhet`,
    `admin`, `godkjenner`, `policyforvalter`, `domeneadjudikator`. Det
    scopet er «kundens egen tilstandsflate» — beslutninger, rapporter,
    utrullingsplanen, ordlisten, pliktregisteret. Å legge
    forespørselsregisteret der ville gitt hver eneste innlogget bruker
    lesetilgang til hvem i virksomheten som har krevd innsyn i, retting
    av eller sletting av sine egne personopplysninger. Det er blant de
    mest sensitive opplysningene huset har, og en «vanlig leser» har
    ingen tjenstlig grunn til dem.
  * `security:read` har `sikkerhet` og `admin` — compliance/ops-klassen.
    Det er den samme klassen `/v1/drift/backup`, `/v1/drift/selvtest`,
    `/v1/datakvalitet` og `/v1/retensjon` alt ligger i, og et
    personvernombuds arbeidsflate hører hjemme nettopp der. Scopet står
    dessuten i `LESESCOPES` i `app.py`, som er det `_autentiser` krever
    av en browserøkt — `platform:admin` gjør ikke det og ville gitt 403
    for hver eneste innlogging (M-4s egen lærdom, 093/`retensjon`).

SKRIVEVEIENE GJENBRUKER `bestilling:opprett`. Registrering, svar, avslag
og forlengelse er BESTILLINGER i plattformens forstand — samme scope som
pliktregisterets tre skriveveier, som stillingsprofilen og som
tidsvalg-slotene, og det står alt i `BROWSER_MUTASJONSSCOPES`. Et nytt
scope skal ikke oppstå av vane. Merk konsekvensen, som er tilsiktet:
rollen `sikkerhet` KAN se registeret og kan IKKE endre det. Å lese hvilke
frister som løper er tilsyn; å svare på vegne av virksomheten er
myndighet.

SP-2 PÅ REGISTRERINGEN (m35/096-formen): `sak_id` utledes deterministisk
av Idempotency-Key-en, så en tapt respons + nytt klikk GJENSPILLER i
stedet for å føde en sak til.

DE TRE OVERGANGENE ER IDEMPOTENTE AV TILSTANDEN SIN, ikke av et lagret
svar, og det er en dom (CodeRabbit, alvorlig — bevisst ikke fulgt).
Reviewet foreslo å persistere det ferdige resultatet per (tenant,
Idempotency-Key) og spille det av på en retry. Formen her er 096s
(`m21_lukk_plikt`/`m21_marker_bortfalt`), og den er valgt av samme grunn:

  * Det RETRYEN skal beskytte mot, er en DOBBEL SKRIVING — og den er
    urepresenterbar her. Dørene låser raden (`FOR UPDATE`) og avviser en
    sak som ikke er `apen`, så et gjenspill treffer statussjekken og
    skriver ingenting. En sak kan ikke besvares to ganger.
  * Prisen er ærlig og skal stå her: en tapt respons + nytt klikk gir
    409 «saken er alt besvart» i stedet for en stille gjentakelse av det
    første svaret. Det er en dårligere melding, men det er en SANN
    melding — og flaten oversetter den til «oppdater listen og se hva
    som står».
  * Et resultatlager per nøkkel ville vært en ny plattformmekanisme
    (`bestilling_idempotens` dekker bestillingsveien, ikke denne), og
    grensen `m30-v1` registrerer ingen invariant om den. En fullmakt
    utenfor grensen legges ikke til i forbifarten.

Skulle den dagen komme at 409-en er for dyr for et menneske i flaten, er
det ÉN endring for M-21 og M-30 sammen — ikke en form til å lære.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

#: Taket for hvor mange saker leseflaten viser. Registerets tak er
#: dørens (500); dette er flatens, og det er en annen begrunnelse: et
#: register med tusen saker skal ikke kunne gjøre ett HTTP-svar til en
#: nedlasting.
MAKS_SAKER = 200

#: Lengdegrensene på kundens egen tekst. `subjekt_ref` og `svar_ref` er
#: HENVISNINGER — et saksnummer, en arkivreferanse — og ikke innhold;
#: begge er korte av natur. Begrunnelsene er de to som faktisk er
#: setninger et menneske skal lese senere.
MAKS_SUBJEKT_REF = 200
MAKS_SVAR_REF = 200
MAKS_BEGRUNNELSE = 2000

#: GDPR-rettighetene — SPEIL av CHECK-en i 099. Speilet finnes for at
#: feilen skal bli 400 og ikke 409: en ukjent verdi er en feilformet
#: forespørsel, ikke en tilstand som sier nei. Dørens CHECK er fortsatt
#: den bindende.
#:
#: NAVNET ER `RETTIGHETER`, ikke `SAKSTYPER`, med vilje: `SAKSTYPER` i
#: `app.py` er unntakskøens tre køer (`normal`/`sikkerhet`/`drift`), og
#: en klient som parser DEN navnekonstanten ut av kildeteksten
#: (`sitekart.test.js` gjør nettopp det) skal ikke kunne treffe denne.
RETTIGHETER = ("innsyn", "retting", "sletting", "begrensning",
               "portabilitet", "innsigelse")

#: Taket for hvor mange M-4-lagre én forespørsel kan dekke. Registeret
#: har i dag under tretti lagre (093s seed), så hundre er romslig og
#: samtidig et tak: en liste uten grense er en vei til å gjøre ett
#: dørkall til en vilkårlig stor innsetting.
MAKS_LAGRE = 100

#: SP-2-navnerommet for de deterministisk utledede id-ene (m8/m21/m35-formen).
_M30_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m30:personvernsak")


def _utled(tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M30_NS, f"personvernsak\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sak_id(request, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get("sak_id")))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


#: De ENESTE feilklassene dørene bruker som DOM (089/096s liste, samme
#: begrunnelse): en tapt forbindelse eller en manglende rettighet på selve
#: funksjonen er ikke «tilstanden nekter». Å svare 409 på dem ville
#: fortalt et menneske at saken er i feil tilstand, mens sannheten er at
#: basen er nede. Uoversatte feil KASTES VIDERE, så `_med_conn` svarer
#: `db_utilgjengelig` og driftsloggen får dem.
#
#: `InsufficientPrivilege` står bevisst IKKE i listen (096s CodeRabbit-
#: lærdom). M-30s radvakt feller sine dommer med den ERRCODE-en — terminal
#: status, manglende aktør, frosset identitet, en forlengelse bakover —
#: men hver av de veiene er ALT stengt av dørene selv, med
#: `invalid_parameter_value`. En `insufficient_privilege` som når hit er
#: derfor alltid det den ser ut som: et manglende grant eller en tapt
#: tenantkontekst. Det er en DRIFTSTILSTAND, og den skal bli 503 med en
#: driftslogg — ikke en 409 som forteller et menneske at saken dens er i
#: feil tilstand mens sannheten er at basen mangler en rettighet.
_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
)


def _doerfeil(e, rid):
    """Dørens ERRCODE → flatens feilkode. ÉN kilde, så alle fire
    skriveveiene svarer likt på samme dom. `None` = ikke en dom;
    kalleren kaster originalen videre."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: tomt svar, tom begrunnelse, ukjent eier,
        # en sak som ikke er `apen`, en forlengelse forbi art. 12-taket,
        # et lager som ikke står i M-4s register. Kroppen ER velformet —
        # det er tilstanden eller innholdskravet som sier nei.
        return _Avbrudd(_feil("personvernsak_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        return _Avbrudd(_feil("personvernsak_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Hele leseflatens tilstand i én transaksjon, gjennom lesedøren.

    Rekkefølgen er DØRENS (åpne først, deretter gjeldende frist stigende)
    — flaten sorterer ikke om. `dogn_til_frist` er regnet i BASEN, i
    samme skann som raden, nettopp for at flaten ikke skal trekke to
    datoer fra hverandre: det er flatens viktigste tall, og et tall som
    regnes to steder blir to ulike tall den dagen tidssonen spriker.
    """
    saker = [
        {"sak_id": str(r[0]), "type": r[1], "subjekt_ref": r[2],
         "mottatt": r[3].isoformat(), "frist": r[4].isoformat(),
         "forlenget_til": r[5].isoformat() if r[5] is not None else None,
         "forlengelse_begrunnelse": r[6],
         "gjeldende_frist": r[7].isoformat(), "dogn_til_frist": r[8],
         "eier_bruker_id": r[9], "eier_navn": r[10], "status": r[11],
         "svar_ref": r[12],
         "svar_ts": r[13].isoformat() if r[13] is not None else None,
         "avvist_begrunnelse": r[14], "lukket_av": r[15],
         "lager_id": list(r[16] or []), "apne_funn": list(r[17] or [])}
        for r in conn.execute("SELECT * FROM m30_saker(%s,%s)",
                              (tenant, MAKS_SAKER)).fetchall()]
    return {"saker": saker}


def saker(tjeneste, request):
    """GET /v1/personvern (security:read) — tenantens forespørselsregister."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "security:read", _fn)


def registrer_endepunkt(tjeneste, request):
    """POST /v1/personvern (bestilling:opprett, idem) — registrer en sak.

    EIEREN ER PÅKREVD I KROPPEN, ikke utledet av innloggingen. Den som
    registrerer forespørselen er ofte ikke den som skal besvare den, og
    en flate som stille satte innloggeren som eier ville gjort «saker
    uten eier» sann på papiret og falsk i praksis. Døren avviser en eier
    som ikke er aktivt medlem av tenanten.

    FRISTEN SENDES IKKE. Kalleren oppgir `mottatt`; registeret regner én
    måned (art. 12 nr. 3). En frist kalleren kunne skrive fritt ville
    gjort «oversittet» til en mening i stedet for et faktum.
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
        saktype = kropp.get("type")
        if saktype not in RETTIGHETER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        subjekt = _tekst(kropp, "subjekt_ref", rid, MAKS_SUBJEKT_REF)
        eier = _tekst(kropp, "eier_bruker_id", rid, 128)
        mottatt = kropp.get("mottatt")
        if not isinstance(mottatt, str) or not mottatt.strip():
            raise _Avbrudd(_feil("request_feilformet", rid))
        # Lagerlisten er VALGFRI i formen og PÅKREVD i praksis: en sak
        # uten lagre blir et `sak_uten_lagre`-funn ved neste sveip.
        # Registreringen skal likevel ikke stoppe på den — en
        # forespørsel som er kommet inn skal kunne skrives ned FØR noen
        # har rukket å finne ut hvor den gjelder, og funnet er hvordan
        # huset husker at det gjenstår.
        lagre = kropp.get("lager_id")
        if lagre is not None:
            if not isinstance(lagre, list) or len(lagre) > MAKS_LAGRE \
                    or not all(isinstance(x, str) and x.strip()
                               and len(x) <= 128 for x in lagre):
                raise _Avbrudd(_feil("request_feilformet", rid))
            lagre = sorted({x.strip() for x in lagre})
        sid = _utled(tenant, nokkel)
        try:
            ny = conn.execute(
                "SELECT m30_registrer_sak(%s,%s,%s,%s,%s::date,%s,%s,%s)",
                (tenant, sid, saktype, subjekt, mottatt, eier, lagre,
                 _bid)).fetchone()[0]
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            # Mottaksdatoen er kallerens tekst, og castet skjer i basen.
            # En ulesbar eller umulig dato er 400, ikke 409: det er
            # KROPPEN som er feil, ikke tilstanden. Fanget som to
            # navngitte klasser og ikke som `DataError`, fordi
            # `DataError` også dekker 22023 — dørenes egen dom, som skal
            # bli 409.
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        # `ny=false` er et STILLE JA (SP-2): samme nøkkel og samme
        # innhold ga samme sak. Kalleren får den samme id-en, og
        # ingenting ble skrevet to ganger.
        return _ok({"sak_id": str(sid), "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def besvar_endepunkt(tjeneste, request):
    """POST /v1/personvern/{sak_id}/svar (bestilling:opprett, idem).

    SVARHENVISNINGEN HÅNDHEVES IKKE HER. Døren krever den, CHECK-en i 099
    krever den, og flaten krever den i skjemaet — men den bindende er
    basens. Et forsøk uten svar blir 409 fordi BASEN nektet, ikke fordi
    API-et sjekket.

    OG DEN SLETTER INGENTING. Å registrere at en sletteforespørsel er
    besvart er ikke det samme som å slette: utførelsen gjøres av den som
    eier lageret, gjennom M-4s reapere, og svaret her er henvisningen til
    at det ble gjort.
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
        sid = _sak_id(request, rid)
        svar_ref = _tekst(kropp, "svar_ref", rid, MAKS_SVAR_REF)
        try:
            conn.execute("SELECT m30_besvar_sak(%s,%s,%s,%s)",
                         (tenant, sid, svar_ref, _bid))
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"sak_id": str(sid), "status": "besvart"}, rid)

    return _med_conn(tjeneste, rid, kjor)


def avvis_endepunkt(tjeneste, request):
    """POST /v1/personvern/{sak_id}/avvis (bestilling:opprett, idem).

    Den ANDRE lovlige utgangen — og den er ikke en billig en. Art. 12
    nr. 4 krever at den registrerte får VITE hvorfor anmodningen ikke
    etterkommes; uten en skreven begrunnelse ville «avvist» vært en
    gratis vei ut av enhver frist, og registeret en liste over ting man
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
        sid = _sak_id(request, rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        try:
            conn.execute("SELECT m30_avvis_sak(%s,%s,%s,%s)",
                         (tenant, sid, begrunnelse, _bid))
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"sak_id": str(sid), "status": "avvist"}, rid)

    return _med_conn(tjeneste, rid, kjor)


def forleng_endepunkt(tjeneste, request):
    """POST /v1/personvern/{sak_id}/forleng (bestilling:opprett, idem).

    Art. 12 nr. 3: to måneder ekstra, MOT en begrunnelse. Taket regnes i
    basen fra sakens `mottatt` — API-et kjenner det ikke og skal ikke
    kjenne det, for da ville lovkravet stått to steder.
    """
    from .app import _rid
    from .policyadmin_http import (_Avbrudd, _browserkontekst, _feil,
                                   _krev_idem, _kropp, _med_conn, _ok)
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "bestilling:opprett")
        _krev_idem(request, rid)
        kropp = _kropp(request)
        sid = _sak_id(request, rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        til = kropp.get("forlenget_til")
        if not isinstance(til, str) or not til.strip():
            raise _Avbrudd(_feil("request_feilformet", rid))
        try:
            ny = conn.execute(
                "SELECT m30_forleng_frist(%s,%s,%s::date,%s,%s)",
                (tenant, sid, til, begrunnelse, _bid)).fetchone()[0]
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"sak_id": str(sid),
                    "forlenget_til": ny.isoformat() if ny else None}, rid)

    return _med_conn(tjeneste, rid, kjor)
