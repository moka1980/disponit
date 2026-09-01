"""M-22 SaaS- og lisensagentens API (migrasjon 098).

Fire endepunkter: én leseflate og tre skriveveier. Ingen av dem rører en
tabell direkte — hver gjør nøyaktig ett kall mot en `disponit_lisens_eier`-
eid SECURITY DEFINER-dør i 098, og runtime har ingen tabellrettigheter i
det hele tatt (SP-7, 090/091/096-formen). At en lisens ikke kan
registreres uten eier, at en avslutning koster en begrunnelse og at en
fornyelsesdato bare kan flyttes framover er derfor egenskaper ved BASEN,
ikke ved denne filen — og en flate som sjekket det selv ville vært en
andre sannhet å omgå.

DET FINNES INGEN OPPSIGELSESVEI HER, og det er v1-dommen: modulen
avslutter ingenting av seg selv. `POST .../avslutt` er et MENNESKE som
skriver at lisensen er avsluttet, med en begrunnelse — ikke modulen som
sier den opp. Katalogens egen guard krever unntaksregister, angrefrist og
gjenopprettingsvei før noe kan fjernes automatisk; tre mekanismer som
ikke finnes.

SCOPENE ER GJENBRUKT, IKKE NYE — og de er M-21s, verifisert mot
`autorisasjon.py` og `app.py`: registrering, fornyelse og avslutning er
BESTILLINGER i plattformens forstand og bærer `bestilling:opprett`
(scopet `admin` allerede har, og som allerede står i
`BROWSER_MUTASJONSSCOPES`). Lesingen bærer `decisions:read`: den er
kundens egen tilstandsflate, samme klasse som pliktregisteret og
rapportene, og `LESESCOPES`-porten i `test_pr008` krever uansett at en
`/v1/`-GET bærer et av de registrerte lesescopene. Et nytt scope skal
ikke oppstå av vane.

SP-2 PÅ REGISTRERINGEN (m35/m21-formen): `lisens_id` utledes
deterministisk av Idempotency-Key-en, så en tapt respons + nytt klikk
GJENSPILLER i stedet for å føde en lisens til. Fornyelsen har sin egen
gjenspillgren i døren (samme dato igjen er et stille ja), og avslutningen
er idempotent av tilstanden sin — døren avviser en lisens som ikke er
`aktiv`.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

#: Taket for hvor mange lisenser leseflaten viser. Registerets tak er
#: dørens (500); dette er flatens, og det er en annen begrunnelse: et
#: register med tusen abonnementer skal ikke kunne gjøre ett HTTP-svar til
#: en nedlasting.
MAKS_LISENSER = 200

#: Lengdegrensene på kundens egen tekst. `leverandor` og `produkt` er
#: NAVN, `kilde` er en HENVISNING (en avtale, et ordrenummer, en faktura)
#: og ikke et sammendrag — alle tre er korte av natur. Begrunnelsen for en
#: avslutning er den ene som faktisk er en setning et menneske skal lese
#: senere, gjerne et år etterpå når noen spør hvorfor verktøyet er borte.
MAKS_LEVERANDOR = 200
MAKS_PRODUKT = 200
MAKS_KILDE = 500
MAKS_BEGRUNNELSE = 2000

#: De lovlige fornyelsestypene og valutaene — SPEIL av CHECK-ene i 098.
#: Speilet finnes for at feilen skal bli 400 og ikke 409: en ukjent verdi
#: er en feilformet forespørsel, ikke en tilstand som sier nei. Dørens
#: CHECK er fortsatt den bindende.
FORNYELSESTYPER = ("automatisk", "manuell", "engang")
VALUTAER = ("NOK", "EUR", "USD", "GBP", "SEK", "DKK", "CHF")

#: Dørens egne grenser på setene og oppsigelsesfristen, speilet av samme
#: grunn. Taket på seter er flatens fornuft og ikke en dom fra basen:
#: et sekssifret setetall er en skrivefeil, ikke et abonnement.
MAKS_SETER = 1_000_000
MAKS_OPPSIGELSESFRIST = 3650

#: SP-2-navnerommet for de deterministisk utledede id-ene (m8/m21/m35-formen).
_M22_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m22:lisens")


def _utled(tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M22_NS, f"lisens\x1f{tenant}\x1f{nokkel}")


def _tekst(kropp, felt: str, rid, maks: int) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, str) or not verdi.strip() or len(verdi) > maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _heltall(kropp, felt: str, rid, *, minst: int, maks: int):
    """Et valgfritt heltall i et lukket spenn, eller None.

    `isinstance(x, bool)` avvises eksplisitt: `True` ER et heltall i
    Python, og en `antall_seter: true` som ble til ett sete er nøyaktig
    den feilen ingen ser i en diff.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return None
    if isinstance(verdi, bool) or not isinstance(verdi, int) \
            or not minst <= verdi <= maks:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _lisens_id(request, rid) -> uuidlib.UUID:
    from .policyadmin_http import _Avbrudd, _feil
    try:
        return uuidlib.UUID(str(request.path_params.get("lisens_id")))
    except (ValueError, TypeError):
        raise _Avbrudd(_feil("request_feilformet", rid))


#: De ENESTE feilklassene dørene bruker som DOM (089/096s liste, samme
#: begrunnelse): en tapt forbindelse eller en manglende rettighet på selve
#: funksjonen er ikke «tilstanden nekter». Å svare 409 på dem ville
#: fortalt et menneske at lisensen er i feil tilstand, mens sannheten er
#: at basen er nede. Uoversatte feil KASTES VIDERE, så `_med_conn` svarer
#: `db_utilgjengelig` og driftsloggen får dem.
#
#: `InsufficientPrivilege` står bevisst IKKE i listen (096s CodeRabbit-
#: begrunnelse, ordrett): hver vei M-22s vakt kan avvise — terminal
#: status, manglende aktør, dato bakover, frosset identitet, frosset
#: oppsigelsesfrist — er ALT stengt av dørene selv, med
#: `invalid_parameter_value`. Da er en `insufficient_privilege` herfra
#: alltid det den ser ut som: et manglende grant eller en tapt
#: tenantkontekst. Det er en DRIFTSTILSTAND, og den skal bli 503 med en
#: driftslogg — ikke en 409 som forteller et menneske at lisensen dens er
#: i feil tilstand mens sannheten er at basen mangler en rettighet.
_DOERDOMMER = (
    psycopg.errors.IntegrityConstraintViolation,
    psycopg.errors.CheckViolation,
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
        # Dørenes egne RAISE-er på tom begrunnelse, ukjent eier, en dato
        # som flyttes bakover og en lisens som ikke er `aktiv`. Kroppen ER
        # velformet — det er tilstanden eller innholdskravet som sier nei.
        return _Avbrudd(_feil("lisens_ulovlig_tilstand", rid, 409))
    if isinstance(e, _DOERDOMMER):
        return _Avbrudd(_feil("lisens_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """Hele leseflatens tilstand i én transaksjon, gjennom lesedøren.

    Rekkefølgen er DØRENS (aktive først, deretter beslutningsdato
    stigende) — flaten sorterer ikke om. `dogn_til_beslutning` er regnet i
    BASEN, i samme skann som raden, nettopp for at flaten ikke skal
    trekke to datoer fra hverandre.

    `kostnad_aar` sendes som STRENG og ikke som tall: NUMERIC(14,2) er
    eksakt, og en JSON-flyttall-runde ville gjort 120000.00 til noe annet
    på veien ut. Flaten formaterer, den regner ikke.
    """
    lisenser = [
        {"lisens_id": str(r[0]), "leverandor": r[1], "produkt": r[2],
         "eier_bruker_id": r[3], "eier_navn": r[4], "antall_seter": r[5],
         "kostnad_aar": None if r[6] is None else str(r[6]),
         "valuta": r[7], "fornyelsesdato": r[8].isoformat(),
         "oppsigelsesfrist_dogn": r[9],
         "beslutningsdato": r[10].isoformat(),
         "dogn_til_beslutning": r[11], "fornyelsestype": r[12],
         "kilde": r[13], "status": r[14], "avslutt_begrunnelse": r[15],
         "avsluttet": r[16].isoformat() if r[16] is not None else None,
         "avsluttet_av": r[17]}
        for r in conn.execute("SELECT * FROM m22_lisenser(%s,%s)",
                              (tenant, MAKS_LISENSER)).fetchall()]
    return {"lisenser": lisenser}


def lisenser(tjeneste, request):
    """GET /v1/lisens (decisions:read) — tenantens eget lisensregister."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


def registrer_endepunkt(tjeneste, request):
    """POST /v1/lisens (bestilling:opprett, idem) — registrer en lisens.

    EIEREN ER PÅKREVD I KROPPEN, ikke utledet av innloggingen. Den som
    fører opp en lisens er ofte innkjøperen, mens eieren er den som
    faktisk forvalter verktøyet og skal ta valget ved fornyelse — og en
    flate som stille satte innloggeren som eier ville gjort eierkolonnen
    sann på papiret og falsk i praksis. Døren avviser en eier som ikke er
    aktivt medlem av tenanten.

    OPPSIGELSESFRISTEN ER MODULENS POENG, og den er valgfri: en avtale
    uten frist finnes, og NULL sier «ingen frist avtalt» der 0 ville sagt
    «kan sies opp samme dag». Er den satt, regner registeret
    varslingspunktene fra `fornyelsesdato - oppsigelsesfrist_dogn` — den
    siste dagen noen faktisk KAN velge.
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
        leverandor = _tekst(kropp, "leverandor", rid, MAKS_LEVERANDOR)
        produkt = _tekst(kropp, "produkt", rid, MAKS_PRODUKT)
        eier = _tekst(kropp, "eier_bruker_id", rid, 128)
        kilde = _tekst(kropp, "kilde", rid, MAKS_KILDE)
        fornyelsesdato = kropp.get("fornyelsesdato")
        if not isinstance(fornyelsesdato, str) or not fornyelsesdato.strip():
            raise _Avbrudd(_feil("request_feilformet", rid))
        fornyelsestype = kropp.get("fornyelsestype", "automatisk")
        if fornyelsestype not in FORNYELSESTYPER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        seter = _heltall(kropp, "antall_seter", rid, minst=1, maks=MAKS_SETER)
        frist = _heltall(kropp, "oppsigelsesfrist_dogn", rid, minst=0,
                         maks=MAKS_OPPSIGELSESFRIST)
        # KOSTNADEN ER EN STRENG PÅ VEIEN INN, som på veien ut: en JSON-
        # `number` er en IEEE-754-flyttall i de fleste klienter, og 0.1 +
        # 0.2 hører ikke hjemme i et kostnadsregister. Basen caster til
        # NUMERIC og feller dommen; et ulesbart tall er 400 (kroppen er
        # feil), ikke 409.
        kostnad = kropp.get("kostnad_aar")
        if kostnad is not None and (not isinstance(kostnad, str)
                                    or not kostnad.strip()
                                    or len(kostnad) > 32):
            raise _Avbrudd(_feil("request_feilformet", rid))
        valuta = kropp.get("valuta")
        if valuta is not None and valuta not in VALUTAER:
            raise _Avbrudd(_feil("request_feilformet", rid))
        # Et beløp uten valuta er ikke et beløp (CHECK-en i 098). Fanget
        # her OGSÅ, for at feilen skal bli 400 med en gang: det er
        # KROPPEN som er ufullstendig, ikke registeret som sier nei.
        if kostnad is not None and valuta is None:
            raise _Avbrudd(_feil("request_feilformet", rid))
        # Varslingspunktene er VALGFRIE: uten dem seeder døren husets
        # standard (60/30/7). Er de med, må de være hele døgn i dørens
        # eget spenn — en liste med en negativ verdi er en feilformet
        # forespørsel, ikke en tilstand.
        punkter = kropp.get("dogn_for")
        if punkter is not None:
            if not isinstance(punkter, list) or not punkter \
                    or not all(isinstance(d, int) and not isinstance(d, bool)
                               and 0 <= d <= MAKS_OPPSIGELSESFRIST
                               for d in punkter):
                raise _Avbrudd(_feil("request_feilformet", rid))
            punkter = sorted(set(punkter))
        lid = _utled(tenant, nokkel)
        try:
            ny = conn.execute(
                "SELECT m22_registrer_lisens(%s,%s,%s,%s,%s,%s,"
                "                            %s::numeric,%s,%s::date,%s,%s,"
                "                            %s,%s,%s)",
                (tenant, lid, leverandor, produkt, eier, seter, kostnad,
                 valuta, fornyelsesdato, fornyelsestype, frist, kilde,
                 punkter, _bid)).fetchone()[0]
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow,
                psycopg.errors.InvalidTextRepresentation,
                psycopg.errors.NumericValueOutOfRange) as e:
            # Datoen og kostnaden er kallerens tekst, og castene skjer i
            # basen. En ulesbar dato eller et beløp som ikke er et tall er
            # 400, ikke 409: det er KROPPEN som er feil, ikke tilstanden.
            # Fanget som navngitte klasser og ikke som `DataError`, fordi
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
        # innhold ga samme lisens. Kalleren får den samme id-en, og
        # ingenting ble skrevet to ganger.
        return _ok({"lisens_id": str(lid), "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def fornyelse_endepunkt(tjeneste, request):
    """POST /v1/lisens/{lisens_id}/fornyelse (bestilling:opprett, idem).

    Den ENE veien fornyelsesdatoen flyttes, og den er menneskelig: noen
    VET at avtalen løper videre, og skriver den nye perioden. Sveipen gjør
    det aldri — en jobb som rullet datoen selv ville vært modulen som
    endrer en lisensrad.

    Den nye datoen må ligge framfor den gjeldende; døren avviser resten
    med 409. Samme dato igjen er et STILLE JA (`ny: false`), så en tapt
    respons + nytt klikk ikke skriver en ny evidensrad om en fornyelse som
    bare skjedde én gang.
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
        lid = _lisens_id(request, rid)
        dato = kropp.get("fornyelsesdato")
        if not isinstance(dato, str) or not dato.strip():
            raise _Avbrudd(_feil("request_feilformet", rid))
        try:
            ny = conn.execute(
                "SELECT m22_registrer_fornyelse(%s,%s,%s::date,%s)",
                (tenant, lid, dato, _bid)).fetchone()[0]
        except (psycopg.errors.InvalidDatetimeFormat,
                psycopg.errors.DatetimeFieldOverflow) as e:
            raise _Avbrudd(_feil("request_feilformet", rid)) from e
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"lisens_id": str(lid), "fornyelsesdato": dato,
                    "ny": bool(ny)}, rid)

    return _med_conn(tjeneste, rid, kjor)


def avslutt_endepunkt(tjeneste, request):
    """POST /v1/lisens/{lisens_id}/avslutt (bestilling:opprett, idem).

    ET MENNESKE SKRIVER AT LISENSEN ER AVSLUTTET. Dette er IKKE modulen
    som sier den opp — endepunktet snakker ikke med noen leverandør, og
    det finnes ingen kodevei i M-22 som gjør det. Raden føres, og
    oppsigelsen gjør mennesket der den faktisk skjer.

    Den koster en skreven begrunnelse — uten den ville «avsluttet» vært en
    gratis vei ut av enhver kostnad, og registeret en liste over ting man
    kan klikke bort. Om et år er begrunnelsen hele svaret på hvorfor
    verktøyet er borte.
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
        lid = _lisens_id(request, rid)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_BEGRUNNELSE)
        try:
            conn.execute("SELECT m22_marker_avsluttet(%s,%s,%s,%s)",
                         (tenant, lid, begrunnelse, _bid))
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise           # driftsfeil — rammen svarer db_utilgjengelig
            raise avbrudd from e
        conn.commit()
        return _ok({"lisens_id": str(lid), "avsluttet": True}, rid)

    return _med_conn(tjeneste, rid, kjor)
