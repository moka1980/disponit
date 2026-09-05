"""M-36 bedriftsoptimalisatorens API (migrasjon 132).

Sytten endepunkter: sju leseveier og ti skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_optimalisator_eier`-eid SECURITY DEFINER-dør i 132, og
runtime har ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM IVERKSETTER ET TILTAK, OG INGEN SOM ENDRER
EN POLICY.

Vaktsetningen er «kan aldri utvide egen fullmakt; korrelasjon
presenteres ikke som årsak; porteføljestopp tilgjengelig», og
fundamentet skrev hvorfor den første halvdelen er en ADVARSEL og ikke
en selvfølge:

  EN OPTIMALISATOR SOM FINNER AT DEN BESTE FORBEDRINGEN ER «GI M-36
  LOV TIL Å GJØRE X», ER IKKE ØDELAGT. DEN GJØR NØYAKTIG DET DEN BLE
  BEDT OM.

Derfor er fullmaktsutvidelse UREPRESENTERBAR, ikke frarådet: det
finnes ingen rute mot `policyer`, `policyutkast` eller
`policyaktivering`, modulrollen har ingen rettighet der, og
`tiltaksforslag.status` har ingen `iverksatt`.

SEKS NEKT SOM ER VERDT Å KJENNE:

  * `POST /rangering` NEKTER MED AKTIV PORTEFØLJESTOPP. Det er
    stoppens hele virkning, og `portefoljestopp_uten_virkning` er
    invarianten som krever at den finnes.

  * `POST /rangering` NEKTER NÅR ET FUNNREGISTER MANGLER I
    `m36_funnregister`. En rangering laget mens et register var
    usynlig ville hvilt på et grunnlag ingen visste var ufullstendig
    — og den ville sett like komplett ut som de riktige.

  * `POST /rangering` NEKTER mot en avviklet modellversjon. Arkivet
    tar imot den; det er BRUKEN som er stengt.

  * `POST /tiltak` NEKTER uten `grunnlagstype` og uten
    `reversibilitet`. Begge er `NOT NULL` med lukkede sett i basen, så
    en rad uten dem er urepresenterbar — API-et nekter først, slik at
    kalleren får en feilmelding den kan handle på.

  * `POST /tiltak` NEKTER en kilde som ikke står i funnregisteret. Et
    forslag som pekte på et register modulen ikke leser, kunne ikke
    spores tilbake til en måling.

  * `POST /effekt` NEKTER en horisont som ikke er passert. Målingen
    kan ikke rettes, så et delresultat registrert som endelig ville
    stått for alltid. MEN ET GJENSPILL MED SAMME TALL ER IKKE ET NEKT
    (131s lærdom, innebygd fra fødselen).

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett`.

Lesescopet er `okonomi:read` fordi tiltakene er anslått i ØRE og
rangeres på penger. Men merk hva det IKKE gir: rangeringen bærer
`kilde_modul` og `kilde_funntype`, ikke funnenes innhold. En
finansleser ser at det finnes tolv åpne HMS-funn, ikke hva de gjelder.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en.
"""
from __future__ import annotations

import uuid as uuidlib

import psycopg

MAKS_RANGERINGER = 200
MAKS_TILTAK = 500
MAKS_FUNN = 200
MAKS_TEKST = 4000
MAKS_NAVN = 500
#: Beskrivelsen må være SKREVET. Seksten tegn er ikke en
#: kvalitetsgaranti — det er en terskel mot «spar penger» som eneste
#: begrunnelse for et tiltak noen skal ta stilling til.
MIN_TEKST = 16

GRUNNLAGSTYPER = ("korrelasjon", "eksperiment", "regel")
REVERSIBILITET = ("reversibel", "delvis_reversibel", "irreversibel")
VURDERINGER = ("vurdert", "avvist")

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 132.
KRAVGRENSER = {
    "horisont_uker": (1, 104),
    "maalefrist_dogn": (1, 180),
    "maks_i_rangering": (1, 100),
}
MIN_USIKKERHET_BP = 1
MAKS_USIKKERHET_BP = 10000

_M36_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL,
                        "disponit:m36:optimalisator")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M36_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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
    """Ikke-tom OG lang nok.

    En begrunnelse på tre tegn er ikke en begrunnelse noen kan ta
    stilling til, og et tiltak uten den er et tall noen fant på.
    """
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


def _ore(kropp, felt: str, rid, *, tillat_null: bool = True) -> int:
    """ØRE SOM HELTALL, aldri kroner som flyttall.

    `0.1 + 0.2` er ikke `0.3`, og en tiltaksliste som rangerte på
    avrundede kroner ville kunnet bytte om to forslag som ligger tett.
    Resten av huset regner i øre; det gjør denne også.
    """
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if not isinstance(verdi, int) or isinstance(verdi, bool):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if abs(verdi) > 10**15:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if not tillat_null and verdi == 0:
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


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


def _dato_valgfri(kropp, felt: str, rid) -> str | None:
    if kropp.get(felt) is None:
        return None
    return _dato(kropp, felt, rid)


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

    Uten denne ville hvert lovlige nekt i 132 blitt en 500 — og en 500
    på «porteføljen er stoppet» er en feilmelding ingen kan handle på
    (121-131s form).
    """
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, (psycopg.errors.RaiseException,
                      psycopg.errors.InvalidParameterValue,
                      psycopg.errors.InsufficientPrivilege,
                      psycopg.errors.CheckViolation,
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
        "rangeringer": r[0], "modeller": r[1],
        "gyldige_modeller": r[2], "tiltak": r[3],
        "uvurderte_tiltak": r[4], "irreversible_uvurderte": r[5],
        "poster": r[6], "maalte": r[7], "umaalte": r[8],
        "treff": r[9], "bom": r[10], "apne_funn": r[11],
        # STOPPEN STÅR I SAMMENDRAGET. En modul som er slått av skal
        # ikke se ut som en modul uten forslag.
        "stopp_aktiv": r[12], "har_krav": r[13],
        "horisont_uker": r[14], "maalefrist_dogn": r[15],
        "maks_i_rangering": r[16], "kravversjon": r[17],
        "apne_funn_i_huset": r[18], "registre": r[19],
    }


def _rangeringsrad(r) -> dict:
    return {
        "rangering_id": str(r[0]), "laget_dato": r[1].isoformat(),
        "horisont_uker": r[2], "modellversjon": r[3],
        "baselinje": r[4], "grunnlag_apne_funn": r[5],
        "grunnlag_registre": r[6], "gjelder_til": r[7].isoformat(),
        "laget_av": r[8], "antall_poster": r[9],
        "antall_maalt": r[10],
    }


def _postrad(r) -> dict:
    return {
        "plass": r[0], "tiltak_id": str(r[1]), "beskrivelse": r[2],
        # PUNKTET KOMMER ALDRI UTEN BÅNDET.
        "forventet_effekt_ore": r[3], "nedre_effekt_ore": r[4],
        "ovre_effekt_ore": r[5],
        # …OG ALDRI UTEN `grunnlagstype`. Vaktsetningen håndhevet der
        # den faktisk kan brytes: i det som forlater basen.
        "grunnlagstype": r[6], "reversibilitet": r[7],
        "ukeslutt": r[8].isoformat(), "faktisk_effekt_ore": r[9],
        "avvik_ore": r[10], "innenfor_intervall": r[11],
        "status": r[12],
        # FLATEN REGNER IKKE UT SELV om horisonten er over.
        "kan_maales": r[13],
    }


def _tiltaksrad(r) -> dict:
    return {
        "tiltak_id": str(r[0]), "beskrivelse": r[1],
        "grunnlagstype": r[2], "grunnlag": r[3],
        "reversibilitet": r[4], "kilde_modul": r[5],
        "kilde_funntype": r[6], "anslag_effekt_ore": r[7],
        "status": r[8], "vurdert_av": r[9],
        "vurderingsnotat": r[10], "opprettet": r[11].isoformat(),
        "opprettet_av": r[12],
    }


def _modellrad(r) -> dict:
    return {
        "modell_id": str(r[0]), "navn": r[1], "versjon": r[2],
        "metode": r[3], "baselinje": r[4], "usikkerhet_bp": r[5],
        "gyldig_fra": r[6].isoformat(),
        "gyldig_til": r[7].isoformat() if r[7] else None,
        "gjelder": r[8], "antall_rangeringer": r[9],
    }


def _stopprad(r) -> dict:
    return {
        "stopp_id": str(r[0]), "begrunnelse": r[1],
        "satt_ts": r[2].isoformat(), "satt_av": r[3],
        "opphevet_ts": r[4].isoformat() if r[4] else None,
        "opphevet_av": r[5], "aktiv": r[6],
    }


def _funnrad(r) -> dict:
    return {
        "funn_id": str(r[0]), "funntype": r[1], "referanse": r[2],
        "detaljer": r[3], "over_grense": r[4], "apen": r[5],
        "forst_sett": r[6].isoformat(), "sist_sett": r[7].isoformat(),
        "lukket_av": r[8],
        # FLATEN SKAL IKKE HUSKE hvilke funn som er sveipens.
        "kan_lukkes": r[9],
    }


def _signalrad(r) -> dict:
    return {"modul": r[0], "relasjon": r[1], "funntype": r[2],
            "antall": r[3]}


def svar_for(conn, tenant: str) -> dict:
    """HELE BILDET I ETT KALL (124/127/128/130s form)."""
    return {
        "sammendrag": _bilderad(
            conn.execute("SELECT * FROM m36_bildet(%s)",
                         (tenant,)).fetchone()),
        "rangeringer": [_rangeringsrad(r) for r in conn.execute(
            "SELECT * FROM m36_rangeringsregister(%s,%s)",
            (tenant, MAKS_RANGERINGER)).fetchall()],
        "tiltak": [_tiltaksrad(r) for r in conn.execute(
            "SELECT * FROM m36_tiltakene(%s,%s)",
            (tenant, MAKS_TILTAK)).fetchall()],
        "modeller": [_modellrad(r) for r in conn.execute(
            "SELECT * FROM m36_modellregister(%s)",
            (tenant,)).fetchall()],
        "stopp": [_stopprad(r) for r in conn.execute(
            "SELECT * FROM m36_stoppen(%s)", (tenant,)).fetchall()],
        "funn": [_funnrad(r) for r in conn.execute(
            "SELECT * FROM m36_optimaliseringsfunn(%s,%s)",
            (tenant, MAKS_FUNN)).fetchall()],
    }


# ---------------------------------------------------------------------
# LESEVEIENE.
# ---------------------------------------------------------------------

def optimalisatorbilde(tjeneste, request):
    """GET /v1/optimalisator (okonomi:read) — hele registeret."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def rangeringer_endepunkt(tjeneste, request):
    """GET /v1/optimalisator/rangeringer (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute(
            "SELECT * FROM m36_rangeringsregister(%s,%s)",
            (auth.tenant, MAKS_RANGERINGER)).fetchall()
        return kanonisk_json(
            {"rangeringer": [_rangeringsrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def rangering_endepunkt(tjeneste, request):
    """GET /v1/optimalisator/rangering/{rangering_id} (okonomi:read).

    HVER POST KOMMER MED SITT BÅND OG SIN `grunnlagstype`. Det siste
    er vaktsetningen håndhevet i det som forlater basen: en flate kan
    velge å ikke vise grunnlagstypen, men den kan ikke få et svar der
    den mangler.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rid_ = _sti_uuid(request, "rangering_id", rid)
        rader = conn.execute("SELECT * FROM m36_rangeringen(%s,%s)",
                             (auth.tenant, rid_)).fetchall()
        return kanonisk_json(
            {"rangering_id": str(rid_),
             "poster": [_postrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def tiltak_endepunkt(tjeneste, request):
    """GET /v1/optimalisator/tiltak (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m36_tiltakene(%s,%s)",
                             (auth.tenant, MAKS_TILTAK)).fetchall()
        return kanonisk_json(
            {"tiltak": [_tiltaksrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def modeller_endepunkt(tjeneste, request):
    """GET /v1/optimalisator/modeller (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m36_modellregister(%s)",
                             (auth.tenant,)).fetchall()
        return kanonisk_json(
            {"modeller": [_modellrad(r) for r in rader],
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/optimalisator/funn (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute(
            "SELECT * FROM m36_optimaliseringsfunn(%s,%s)",
            (auth.tenant, MAKS_FUNN)).fetchall()
        return kanonisk_json(
            {"funn": [_funnrad(r) for r in rader], "request_id": rid},
            200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def signaler_endepunkt(tjeneste, request):
    """GET /v1/optimalisator/signaler (okonomi:read).

    HVA MODULEN FAKTISK SER, per register. Ruten finnes fordi
    rangeringen ellers ville vært en konklusjon uten et synlig
    grunnlag — og fordi et register med null åpne funn skal være
    SYNLIG som lest, ikke usynlig som fraværende.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m36_apne_funn(%s)",
                             (auth.tenant,)).fetchall()
        udekket = [r[0] for r in conn.execute(
            "SELECT * FROM m36_udekkede_registre()").fetchall()]
        return kanonisk_json(
            {"signaler": [_signalrad(r) for r in rader],
             # ET USYNLIG REGISTER ER DET VIKTIGSTE TALLET HER.
             "udekkede_registre": udekket,
             "request_id": rid}, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


# ---------------------------------------------------------------------
# SKRIVEVEIENE.
# ---------------------------------------------------------------------

def _skriv(tjeneste, request, bygg):
    """Rammen ni av de ti skriveveiene deler.

    `/rangering` bruker den IKKE: den returnerer en RAD med hvor mange
    tiltak som kom med, hvor mange åpne funn grunnlaget hadde, og hvor
    mange registre som ble lest. Den som ber om en rangering skal se
    hvor bredt den så, ikke bare at den ble laget.
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
    """POST /v1/optimalisator/krav (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        h = _heltall(kropp, "horisont_uker", rid,
                     *KRAVGRENSER["horisont_uker"])
        m = _heltall(kropp, "maalefrist_dogn", rid,
                     *KRAVGRENSER["maalefrist_dogn"])
        k = _heltall(kropp, "maks_i_rangering", rid,
                     *KRAVGRENSER["maks_i_rangering"])
        return ("SELECT versjon FROM"
                " m36_sett_krav(%s,%s,%s,%s,%s,%s)",
                (tenant, h, m, k, bid, nokkel), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_modell_endepunkt(tjeneste, request):
    """POST /v1/optimalisator/modell (bestilling:opprett, idem).

    `usikkerhet_bp` ER MODELLENS, IKKE TENANTENS. To modellversjoner
    kan lese samme anslag og ha ulik tillit til det — og en modell som
    påsto null usikkerhet ville påstått å vite framtiden.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        navn = _tekst(kropp, "navn", rid, MAKS_NAVN)
        versjon = _tekst(kropp, "versjon", rid, MAKS_NAVN)
        metode = _lang_tekst(kropp, "metode", rid)
        baselinje = _tekst(kropp, "baselinje", rid, MAKS_NAVN)
        usikkerhet = _heltall(kropp, "usikkerhet_bp", rid,
                              MIN_USIKKERHET_BP, MAKS_USIKKERHET_BP)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        mid = _utled("modell", tenant, nokkel)
        return ("SELECT gjelder FROM m36_registrer_modell"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, mid, navn, versjon, metode, baselinje,
                 usikkerhet, fra, til, bid),
                {"modell_id": str(mid)}, "gjelder")
    return _skriv(tjeneste, request, bygg)


def avvikle_modell_endepunkt(tjeneste, request):
    """POST /v1/optimalisator/modell/{modell_id}/avvikle."""
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        mid = _sti_uuid(request_, "modell_id", rid)
        til = _dato(kropp, "gyldig_til", rid)
        del nokkel
        return ("SELECT gyldig_til FROM"
                " m36_avvikle_modell(%s,%s,%s,%s)",
                (tenant, mid, til, bid),
                {"modell_id": str(mid)}, "gyldig_til")
    return _skriv(tjeneste, request, bygg)


def foresla_tiltak_endepunkt(tjeneste, request):
    """POST /v1/optimalisator/tiltak (bestilling:opprett, idem).

    `grunnlagstype` OG `reversibilitet` ER OBLIGATORISKE, og det er
    hele vaktsetningen: et forslag som ikke sier hva det hviler på,
    later som det hviler på noe sterkere enn det gjør — og et tiltak
    ingen har vurdert reversibiliteten av er et tiltak ingen kan
    angre.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        beskrivelse = _lang_tekst(kropp, "beskrivelse", rid)
        grunnlagstype = _valg(kropp, "grunnlagstype", rid,
                              GRUNNLAGSTYPER)
        grunnlag = _lang_tekst(kropp, "grunnlag", rid)
        rev = _valg(kropp, "reversibilitet", rid, REVERSIBILITET)
        modul = _tekst(kropp, "kilde_modul", rid, MAKS_NAVN)
        funntype = _tekst(kropp, "kilde_funntype", rid, MAKS_NAVN)
        anslag = _ore(kropp, "anslag_effekt_ore", rid,
                      tillat_null=False)
        tid = _utled("tiltak", tenant, nokkel)
        return ("SELECT ny FROM m36_foresla_tiltak"
                "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, tid, beskrivelse, grunnlagstype, grunnlag,
                 rev, modul, funntype, anslag, bid),
                {"tiltak_id": str(tid)}, "ny")
    return _skriv(tjeneste, request, bygg)


def vurder_tiltak_endepunkt(tjeneste, request):
    """POST /v1/optimalisator/tiltak/{tiltak_id}/vurder.

    `vurdert` ELLER `avvist` — OG INGEN TREDJE VERDI. Det finnes ingen
    `iverksatt`, og det er ikke en forglemmelse: utførelsen går gjennom
    modulen som EIER handlingen, av et menneske, på M-41s
    policykontrollerte vei — og den veien vet ikke at denne tabellen
    finnes.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        tid = _sti_uuid(request_, "tiltak_id", rid)
        status = _valg(kropp, "status", rid, VURDERINGER)
        notat = _lang_tekst(kropp, "vurderingsnotat", rid)
        del nokkel
        return ("SELECT status FROM"
                " m36_vurder_tiltak(%s,%s,%s,%s,%s)",
                (tenant, tid, status, notat, bid),
                {"tiltak_id": str(tid)}, "status")
    return _skriv(tjeneste, request, bygg)


def sett_stopp_endepunkt(tjeneste, request):
    """POST /v1/optimalisator/stopp (bestilling:opprett, idem).

    STOPPEN VIRKER: med den aktiv nekter `/rangering`. Det er det
    eneste M-36 lovlig kan stanse — å stanse en annen modul ville vært
    `modulen_overstyrte_en_annen_moduls_grense` — og flaten sier det
    rett ut i stedet for å love en nødbrems for driften.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        begrunnelse = _lang_tekst(kropp, "begrunnelse", rid)
        sid = _utled("stopp", tenant, nokkel)
        return ("SELECT aktiv FROM m36_sett_stopp(%s,%s,%s,%s)",
                (tenant, sid, begrunnelse, bid),
                {"stopp_id": str(sid)}, "aktiv")
    return _skriv(tjeneste, request, bygg)


def opphev_stopp_endepunkt(tjeneste, request):
    """POST /v1/optimalisator/stopp/{stopp_id}/opphev."""
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        sid = _sti_uuid(request_, "stopp_id", rid)
        begrunnelse = _lang_tekst(kropp, "begrunnelse", rid)
        del nokkel
        return ("SELECT aktiv FROM m36_opphev_stopp(%s,%s,%s,%s)",
                (tenant, sid, begrunnelse, bid),
                {"stopp_id": str(sid)}, "aktiv")
    return _skriv(tjeneste, request, bygg)


def rangere_endepunkt(tjeneste, request):
    """POST /v1/optimalisator/rangering (bestilling:opprett, idem).

    SVARET BÆRER HVOR BREDT RANGERINGEN SÅ: antall tiltak, antall åpne
    funn i grunnlaget, og hvor mange registre som ble lest. Et
    `{"ok": true}` ville krevd et nytt kall for å finne ut om
    grunnlaget var komplett — og det kallet blir ikke alltid gjort.
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
        mid = _kropp_uuid(kropp, "modell_id", rid)
        rgid = _utled("rangering", tenant, nokkel)
        try:
            r = conn.execute(
                "SELECT * FROM m36_rangere(%s,%s,%s,%s)",
                (tenant, rgid, mid, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"rangering_id": str(rgid), "antall": r[1],
                    "apne_funn": r[2], "registre": r[3],
                    "ny": r[4]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_effekt_endepunkt(tjeneste, request):
    """POST /v1/optimalisator/rangering/{rangering_id}/effekt.

    DEN ENESTE VEIEN TIL Å LUKKE `rangering_uten_maaling`.

    `innenfor_intervall` REGNES AV BÅNDET SOM STO PÅ RADEN, ikke av
    noe kalleren oppgir. Hadde kalleren fått si «ja, dette traff»,
    ville målingen vært en karakter modulen ga seg selv.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        rgid = _sti_uuid(request_, "rangering_id", rid)
        plass = _heltall(kropp, "plass", rid, 1, 100)
        faktisk = _ore(kropp, "faktisk_effekt_ore", rid)
        del nokkel
        return ("SELECT avvik_ore FROM"
                " m36_registrer_effekt(%s,%s,%s,%s,%s)",
                (tenant, rgid, plass, faktisk, bid),
                {"rangering_id": str(rgid), "plass": plass},
                "avvik_ore")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/optimalisator/funn/{funn_id}/lukk.

    NEKTER PÅ SVEIPENS TO. `rangering_uten_maaling` lukkes av at
    effekten måles; `korrelasjon_alene_paa_topp` av at toppen endrer
    seg. Kunne et menneske lukket dem, ville vaktsetningen vært en
    anbefaling.

    `stopp_staar_uten_oppheving` KAN lukkes: «vi vet, den skal stå» er
    en legitim beslutning med et navn på.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request_):
        fid = _sti_uuid(request_, "funn_id", rid)
        begrunnelse = _lang_tekst(kropp, "begrunnelse", rid)
        del nokkel
        return ("SELECT apen FROM m36_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, begrunnelse, bid),
                {"funn_id": str(fid)}, "apen")
    return _skriv(tjeneste, request, bygg)
