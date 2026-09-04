"""M-54 EHF- og Peppol-avviksretterens API (migrasjon 121).

Sytten endepunkter: åtte leseveier og ni skriveveier, alle mot dører.
Ingen av dem rører en tabell direkte — hver går gjennom en
`disponit_ehf_eier`-eid SECURITY DEFINER-dør i 121, og runtime har
ingen tabellrettigheter i det hele tatt (SP-7).

DET FINNES INGEN RUTE SOM SENDER EN FAKTURA, og det er ikke en sjekk
her: 121 har ingen mottaker, ingen utboks og ingen «sendt»-kolonne å
skrive til. Spesifikasjonens vakt sier «retting klargjøres maskinelt,
utsending signeres av menneske» — og en faktura sendt to ganger er et
DOBBELT BETALINGSKRAV.

`/klar` SETTER EN TILSTAND HOS OSS, ikke en handling utad. Samme figur
som M-46s «klar til gjennomgang» (118) og M-51s ferdigstilte estimat
(119). Signaturen hører til v2, og forutsetningen for v2 er MÅLT: hvor
ofte klargjøringen er feil.

KLYNGE 7s DELTE DOM, HER: REGELEN ER MYNDIGHETENS OG DEN ENDRES.

  * `/valider` NEKTER mot et utløpt regelsett. En dom felt under en
    foreldet regel ser velformet ut og er gal.
  * `/regelsett/{id}/gyldig-til` finnes fordi et standardorgan som
    kunngjør en sluttdato i juni er nettopp den endringen modulen skal
    følge med på. Alt annet ved settet er frosset.
  * Registreringen av et ALT UTLØPT sett er LOVLIG: det er arkivet
    som gjør «hva sa standarden den gangen» mulig å svare på.

SCOPENE. LESING `okonomi:read`, SKRIVING `bestilling:opprett` — samme
presedens som 096/100–120.

SP-2 PÅ REGISTRERINGSDØRENE: id-ene utledes av Idempotency-Key-en. For
valideringen er det nødvendig av en egen grunn: en gjentatt POST må
ikke bli to dommer over samme dokument mot samme regelsett.
"""
from __future__ import annotations

import datetime
import uuid as uuidlib

import psycopg

MAKS_REGELSETT = 200
MAKS_DOKUMENTER = 200
MAKS_FELT = 5000
MAKS_TEKST = 4000
MAKS_NAVN = 500
MAKS_URL = 2000
MAKS_REF = 200
#: BIGINT-taket for dokumentets størrelse i byte.
MAKS_BYTES = 100_000_000_000
#: Øre. Samme tak som 101/106/119.
MAKS_ORE = 100_000_000_000

#: Grensene API-et håndhever før døra. Speiler CHECK-ene i 121.
KRAVGRENSER = {
    "utlopsvarsel_dogn": (1, 365),
    "avviksfrist_dogn": (1, 365),
}

STANDARDER = ("ubl", "peppol_bis", "ehf")
KRAVTYPER = ("finnes", "ikke_tom", "i_kodeliste", "lik_sum")
ALVORLIGHETER = ("feil", "advarsel")
RETNINGER = ("inngaaende", "utgaaende")
FUNNTYPER = ("regelsett_utlopt", "regelsett_utloper_snart",
             "validering_mot_utlopt_regelsett",
             "dokument_uten_validering", "avvik_uten_retting",
             "retting_ikke_klar", "ingen_krav")

#: STIENS FORM. Samme mønster som CHECK-en i 121: ingen jokertegn,
#: ingen fri XPath. En sti som ikke matcher bokstavelig, matcher ikke.
_STI_TEGN = set("abcdefghijklmnopqrstuvwxyz"
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-")

_M54_NS = uuidlib.uuid5(uuidlib.NAMESPACE_URL, "disponit:m54:ehf")


def _utled(art: str, tenant: str, nokkel: str) -> uuidlib.UUID:
    return uuidlib.uuid5(_M54_NS, f"{art}\x1f{tenant}\x1f{nokkel}")


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
    """NULL er et ærlig svar, og her betyr det noe presist: en
    `fra_verdi` som er NULL sier at feltet SKAL LEGGES TIL, mens en tom
    streng sier at det fantes og var tomt."""
    if kropp.get(felt) is None:
        return None
    return _tekst(kropp, felt, rid, maks)


def _sti(kropp, felt: str, rid) -> str:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = _tekst(kropp, felt, rid, MAKS_REF)
    if any(c not in _STI_TEGN for c in verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _sti_valgfri(kropp, felt: str, rid) -> str | None:
    if kropp.get(felt) is None:
        return None
    return _sti(kropp, felt, rid)


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
    """NULL på `gyldig_til` betyr «gjelder fortsatt» — ikke «gjelder
    for alltid», og forskjellen er hele grunnen til at kolonnen
    finnes.

    UTELATT NØKKEL OG EKSPLISITT `null` ER DET SAMME HER, og det er
    riktig ved OPPRETTELSE: et nytt regelsett uten sluttdato gjelder
    fortsatt. Ved ENDRING er de IKKE det samme — se `_dato_paakrevd`.
    """
    if kropp.get(felt) is None:
        return None
    return _dato(kropp, felt, rid)


def _dato_som_kan_vaere_null(kropp, felt: str, rid) -> str | None:
    """NØKKELEN MÅ STÅ, MEN VERDIEN KAN VÆRE `null` (CodeRabbit).

    På `/gyldig-til` er forskjellen mellom «utelatt» og «eksplisitt
    null» hele saken. En klient som GLEMMER feltet ville ellers
    stilltiende NULLSTILT sluttdatoen — og gjort «utgår 31. desember»
    om til «gjelder fortsatt».

    Det er nøyaktig feilen modulen finnes for å hindre: en regel som
    ER gått ut, som ser ut som en som ikke er det. At den kunne
    oppstå fra en glemt JSON-nøkkel gjør den verre, ikke mildere.
    """
    from .policyadmin_http import _Avbrudd, _feil
    if felt not in kropp:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if kropp[felt] is None:
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


def _url_valgfri(kropp, felt: str, rid) -> str | None:
    from .policyadmin_http import _Avbrudd, _feil
    if kropp.get(felt) is None:
        return None
    verdi = _tekst(kropp, felt, rid, MAKS_URL)
    if not (verdi.startswith("http://")
            or verdi.startswith("https://")):
        raise _Avbrudd(_feil("request_feilformet", rid))
    if any(c.isspace() for c in verdi):
        raise _Avbrudd(_feil("request_feilformet", rid))
    return verdi


def _kodeverdi(kropp, felt: str, rid) -> list[str]:
    from .policyadmin_http import _Avbrudd, _feil
    verdi = kropp.get(felt)
    if verdi is None:
        return []
    if not isinstance(verdi, list) or len(verdi) > 500:
        raise _Avbrudd(_feil("request_feilformet", rid))
    ut = []
    for x in verdi:
        if not isinstance(x, str) or not x.strip() or len(x) > 200:
            raise _Avbrudd(_feil("request_feilformet", rid))
        ut.append(x.strip())
    return ut


def _felter(kropp, rid):
    """DE PARSEDE FELTENE, som fire lister av SAMME lengde.

    Fire lister og ikke en liste av objekter, fordi døra tar dem som
    fire arrayer og `unnest ... WITH ORDINALITY` parer dem på indeks.
    Ulik lengde ville stilltiende kappet den korteste — og et felt som
    forsvant i kappingen ville blitt `uten_grunnlag` uten at noen
    skrev det. Døra sjekker det samme; denne sjekken gjør feilen til
    en 400 og ikke en 409.
    """
    from .policyadmin_http import _Avbrudd, _feil
    rader = kropp.get("felter")
    if not isinstance(rader, list) or not rader:
        raise _Avbrudd(_feil("request_feilformet", rid))
    if len(rader) > MAKS_FELT:
        raise _Avbrudd(_feil("request_feilformet", rid))
    stier, forekomster, verdier, ore = [], [], [], []
    for rad in rader:
        if not isinstance(rad, dict):
            raise _Avbrudd(_feil("request_feilformet", rid))
        stier.append(_sti(rad, "sti", rid))
        forekomster.append(_heltall(rad, "forekomst", rid, 0, 100_000))
        v = rad.get("verdi")
        # TOM STRENG ER LOVLIG OG BETYR NOE: «feltet fantes, men var
        # tomt» er et annet avvik enn «feltet fantes ikke».
        if not isinstance(v, str) or len(v) > MAKS_TEKST:
            raise _Avbrudd(_feil("request_feilformet", rid))
        verdier.append(v)
        o = rad.get("verdi_ore")
        if o is None:
            ore.append(None)
        else:
            ore.append(_heltall(rad, "verdi_ore", rid, -MAKS_ORE,
                                MAKS_ORE))
    return stier, forekomster, verdier, ore


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
    """Dørenes dommer → API-feil. Samme form som 112–120."""
    from .policyadmin_http import _Avbrudd, _feil
    if isinstance(e, psycopg.errors.UniqueViolation):
        if ("ehfvalidering_unik" in str(e)
                or "ehfdokument_unik" in str(e)
                or "ehfregelsett_unik" in str(e)
                or "ehfregel_unik" in str(e)
                or "ehfretting_unik" in str(e)
                or "ehffelt_unik" in str(e)):
            return _Avbrudd(_feil("ehf_ulovlig_tilstand", rid, 409))
        return _Avbrudd(_feil("idempotenskonflikt", rid))
    if isinstance(e, psycopg.errors.ForeignKeyViolation):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.NoDataFound):
        return _Avbrudd(_feil("ikke_funnet", rid, 404))
    if isinstance(e, psycopg.errors.InvalidParameterValue):
        # Dørenes egne RAISE-er: utløpt regelsett, retting av et
        # `uten_grunnlag`-avvik, klarmerking med urettet formfeil.
        return _Avbrudd(_feil("ehf_ulovlig_tilstand", rid, 409))
    if isinstance(e, (psycopg.errors.IntegrityConstraintViolation,
                      psycopg.errors.CheckViolation,
                      psycopg.errors.InsufficientPrivilege)):
        return _Avbrudd(_feil("ehf_ulovlig_tilstand", rid, 409))
    return None


def svar_for(conn, tenant: str) -> dict:
    """EHF-flatens tilstand i én transaksjon, gjennom fire dører."""
    s = conn.execute("SELECT * FROM m54_ehfstatus(%s)",
                     (tenant,)).fetchone()
    regelsett = [
        {"regelsett_id": str(r[0]), "standard": r[1],
         "versjon": r[2], "gyldig_fra": r[3].isoformat(),
         "gyldig_til": r[4].isoformat() if r[4] else None,
         # GYLDIGHETEN REGNES I BASEN. To lesere skal ikke kunne komme
         # til hver sin konklusjon om hvorvidt regelen vi bruker
         # fortsatt gjelder.
         "gyldig_naa": r[5], "dogn_til_utlop": r[6],
         "innhold_sha256": r[7], "kilde_url": r[8],
         "registrert": r[9].isoformat(), "registrert_av": r[10],
         "antall_regler": r[11], "antall_valideringer": r[12]}
        for r in conn.execute("SELECT * FROM m54_regelsettene(%s,%s)",
                              (tenant, MAKS_REGELSETT)).fetchall()]
    dokumenter = [_dokumentrad(r) for r in conn.execute(
        "SELECT * FROM m54_dokumentene(%s,%s)",
        (tenant, MAKS_DOKUMENTER)).fetchall()]
    k = conn.execute("SELECT * FROM m54_kravene(%s)",
                     (tenant,)).fetchone()
    return {
        "sammendrag": {
            "regelsett": s[0], "gyldige_regelsett": s[1],
            "utlopte_regelsett": s[2], "dokumenter": s[3],
            "validerte": s[4], "med_feil": s[5],
            "uten_grunnlag": s[6], "uvaliderte": s[7],
            # DET ENE TALLET KLYNGEN FINNES FOR: dommer felt under en
            # regel som siden har gått ut.
            "dommer_under_utlopt": s[8], "rettinger": s[9],
            "klare_rettinger": s[10], "apne_funn": s[11],
            "har_krav": s[12], "kravversjon": s[13],
            "vist": len(dokumenter)},
        "regelsett": regelsett,
        "dokumenter": dokumenter,
        "krav": None if k is None else {
            "utlopsvarsel_dogn": k[0], "avviksfrist_dogn": k[1],
            "versjon": k[2], "oppdatert": k[3].isoformat(),
            "oppdatert_av": k[4]}}


def _dokumentrad(r) -> dict:
    """Dokumentet MED nyeste dom og regelsettversjonen den ble felt
    under. Versjonen står HER, ikke bak et ekstra oppslag: en dom uten
    versjonen den ble felt under er nettopp det klyngen finnes for å
    unngå."""
    return {
        "dokument_id": str(r[0]), "retning": r[1],
        "ekstern_ref": r[2], "motpart": r[3],
        "fakturadato": r[4].isoformat(), "innhold_sha256": r[5],
        "innhold_bytes": r[6], "registrert": r[7].isoformat(),
        "registrert_av": r[8], "antall_felt": r[9],
        "validering_id": str(r[10]) if r[10] else None,
        "standard": r[11], "versjon": r[12],
        "antall_regler": r[13], "antall_feil": r[14],
        "antall_advarsler": r[15], "antall_uten_grunnlag": r[16],
        "gyldig": r[17],
        "validert": r[18].isoformat() if r[18] else None,
        "regelsett_gyldig_naa": r[19], "antall_rettinger": r[20],
        "klare_rettinger": r[21], "antall_valideringer": r[22]}


def ehfbilde(tjeneste, request):
    """GET /v1/ehf (okonomi:read) — tenantens eget register."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def regler_endepunkt(tjeneste, request):
    """GET /v1/ehf/regelsett/{regelsett_id}/regler (okonomi:read)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rid_sett = _sti_uuid(request, "regelsett_id", rid)
        rader = conn.execute("SELECT * FROM m54_reglene(%s,%s)",
                             (auth.tenant, rid_sett)).fetchall()
        svar = {"regelsett_id": str(rid_sett), "request_id": rid,
                "regler": [
                    {"regel_id": str(r[0]), "kode": r[1],
                     "sti": r[2], "krav": r[3],
                     "kodeverdi": list(r[4] or []), "sum_sti": r[5],
                     "alvorlighet": r[6], "beskrivelse": r[7],
                     "registrert": r[8].isoformat(),
                     "registrert_av": r[9]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def avvik_endepunkt(tjeneste, request):
    """GET /v1/ehf/validering/{validering_id}/avvik (okonomi:read).

    HVERT AVVIK MED SIN RETTING, hvis den finnes. `funnet_verdi` NULL
    betyr at feltet IKKE FANTES — ikke at det var tomt, og forskjellen
    er det første et menneske spør om.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        vid = _sti_uuid(request, "validering_id", rid)
        rader = conn.execute("SELECT * FROM m54_avvikene(%s,%s)",
                             (auth.tenant, vid)).fetchall()
        svar = {"validering_id": str(vid), "request_id": rid,
                "avvik": [
                    {"avvik_id": str(r[0]), "regelkode": r[1],
                     "alvorlighet": r[2], "sti": r[3],
                     "funnet_verdi": r[4], "forventet": r[5],
                     "beskrivelse": r[6],
                     "retting_id": str(r[7]) if r[7] else None,
                     "felt_sti": r[8], "fra_verdi": r[9],
                     "til_verdi": r[10],
                     "retting_begrunnelse": r[11],
                     "klar_til_signering": r[12],
                     "klar_ts": r[13].isoformat() if r[13] else None,
                     "klar_av": r[14]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def valideringer_endepunkt(tjeneste, request):
    """GET /v1/ehf/dokument/{dokument_id}/valideringer (okonomi:read).

    HELE REKKEN, ikke bare den nyeste. En ny regelsettversjon gir en ny
    rad, og det er der «hva sa standarden den gangen» står.
    """
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        did = _sti_uuid(request, "dokument_id", rid)
        rader = conn.execute("SELECT * FROM m54_valideringene(%s,%s)",
                             (auth.tenant, did)).fetchall()
        svar = {"dokument_id": str(did), "request_id": rid,
                "valideringer": [
                    {"validering_id": str(r[0]),
                     "regelsett_id": str(r[1]), "standard": r[2],
                     "versjon": r[3], "antall_regler": r[4],
                     "antall_feil": r[5], "antall_advarsler": r[6],
                     "antall_uten_grunnlag": r[7], "gyldig": r[8],
                     "regelsett_gyldig_naa": r[9],
                     "validert": r[10].isoformat(),
                     "validert_av": r[11]} for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def funn_endepunkt(tjeneste, request):
    """GET /v1/ehf/funn (okonomi:read) — nattens funn."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        rader = conn.execute("SELECT * FROM m54_funnene(%s,%s)",
                             (auth.tenant, True)).fetchall()
        svar = {"request_id": rid, "funn": [
            {"funn_id": str(r[0]), "funntype": r[1],
             "regelsett_id": str(r[2]) if r[2] else None,
             "dokument_id": str(r[3]) if r[3] else None,
             "validering_id": str(r[4]) if r[4] else None,
             "standard": r[5], "regelsettversjon": r[6],
             "ekstern_ref": r[7], "over_grense": r[8],
             "detalj": r[9], "kravversjon": r[10],
             "forst_sett": r[11].isoformat(),
             "sist_sett_sveip": r[12].isoformat(), "apen": r[13],
             "lukket_ts": r[14].isoformat() if r[14] else None,
             "lukket_av": r[15], "lukkenotat": r[16]}
            for r in rader]}
        return kanonisk_json(svar, 200, {"x-request-id": rid})

    return _les(tjeneste, request, "okonomi:read", _fn)


def _skriv(tjeneste, request, bygg):
    """Rammen åtte av de ni skriveveiene deler.

    `/valider` bruker den IKKE: den returnerer en RAD (regler, feil,
    advarsler, uten grunnlag, dommen), ikke en skalar. Den som
    validerer skal se hva dommen SIER — særlig hvor mange regler som
    ikke hadde et grunnlag å dømme på.
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
    """POST /v1/ehf/krav (bestilling:opprett, idem).

    NØKKELEN GÅR HELT INN I DØRA (119s lærdom): `ehfkrav` er en
    singleton per tenant og har ingen id å utlede fra nøkkelen.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        utlop = _heltall(kropp, "utlopsvarsel_dogn", rid,
                         *KRAVGRENSER["utlopsvarsel_dogn"])
        avvik = _heltall(kropp, "avviksfrist_dogn", rid,
                         *KRAVGRENSER["avviksfrist_dogn"])
        return ("SELECT m54_sett_krav(%s,%s,%s,%s,%s)",
                (tenant, utlop, avvik, bid, nokkel), {}, "versjon")
    return _skriv(tjeneste, request, bygg)


def registrer_regelsett_endepunkt(tjeneste, request):
    """POST /v1/ehf/regelsett (bestilling:opprett, idem).

    ET ALT UTLØPT SETT KAN REGISTRERES, og det er med vilje: modulen
    finnes for å kunne svare på «hva sa standarden den gangen». Å forby
    arkivet er å forby spørsmålet. Skillet går ved DOMMEN —
    `/valider` nekter mot et utløpt sett.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        standard = _valg(kropp, "standard", rid, STANDARDER)
        versjon = _tekst(kropp, "versjon", rid, MAKS_REF)
        fra = _dato(kropp, "gyldig_fra", rid)
        til = _dato_valgfri(kropp, "gyldig_til", rid)
        sum_ = _sha256(kropp, "innhold_sha256", rid)
        url = _url_valgfri(kropp, "kilde_url", rid)
        sid = _utled("regelsett", tenant, nokkel)
        return ("SELECT m54_registrer_regelsett("
                "%s,%s,%s,%s,%s::date,%s::date,%s,%s,%s)",
                (tenant, sid, standard, versjon, fra, til, sum_, url,
                 bid), {"regelsett_id": str(sid)}, None)
    return _skriv(tjeneste, request, bygg)


def sett_gyldig_til_endepunkt(tjeneste, request):
    """POST /v1/ehf/regelsett/{regelsett_id}/gyldig-til.

    DENNE RUTEN FINNES FORDI REGELEN ER MYNDIGHETENS. Et standardorgan
    som kunngjør i juni at EHF 3.0 trekkes 31. desember, er nettopp den
    endringen modulen skal følge med på — og et helt frosset regelsett
    ville tvunget oss til å late som vi ikke visste.

    ALT ANNET VED SETTET ER FROSSET: standard, versjon, `gyldig_fra` og
    innholdssummen er identiteten som gjør en gammel validering
    etterprøvbar.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        sid = _sti_uuid(request, "regelsett_id", rid)
        # NØKKELEN MÅ STÅ. En glemt `gyldig_til` ville nullstilt
        # sluttdatoen i stillhet.
        til = _dato_som_kan_vaere_null(kropp, "gyldig_til", rid)
        return ("SELECT m54_sett_gyldig_til(%s,%s,%s::date,%s)",
                (tenant, sid, til, bid),
                {"regelsett_id": str(sid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_regel_endepunkt(tjeneste, request):
    """POST /v1/ehf/regel (bestilling:opprett, idem).

    KRAVET OG PARAMETEREN HENGER SAMMEN, og 121 håndhever det: en
    `i_kodeliste` uten kodeliste, eller en `lik_sum` uten noe å
    summere, ville vært STILLE GRØNN — den verste tilstanden en regel
    kan ha.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        sid = _kropp_uuid(kropp, "regelsett_id", rid)
        kode = _tekst(kropp, "kode", rid, MAKS_REF)
        sti = _sti(kropp, "sti", rid)
        krav = _valg(kropp, "krav", rid, KRAVTYPER)
        kodeverdi = _kodeverdi(kropp, "kodeverdi", rid)
        sum_sti = _sti_valgfri(kropp, "sum_sti", rid)
        alvorlighet = _valg(kropp, "alvorlighet", rid, ALVORLIGHETER)
        beskrivelse = _tekst(kropp, "beskrivelse", rid, MAKS_TEKST)
        gid = _utled("regel", tenant, nokkel)
        return ("SELECT m54_registrer_regel("
                "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, gid, sid, kode, sti, krav, kodeverdi,
                 sum_sti, alvorlighet, beskrivelse, bid),
                {"regel_id": str(gid)}, None)
    return _skriv(tjeneste, request, bygg)


def registrer_dokument_endepunkt(tjeneste, request):
    """POST /v1/ehf/dokument (bestilling:opprett, idem)."""
    def bygg(tenant, bid, nokkel, kropp, rid, _request):
        retning = _valg(kropp, "retning", rid, RETNINGER)
        ref = _tekst(kropp, "ekstern_ref", rid, MAKS_REF)
        motpart = _tekst(kropp, "motpart", rid, MAKS_NAVN)
        dato = _dato(kropp, "fakturadato", rid)
        sum_ = _sha256(kropp, "innhold_sha256", rid)
        bytes_ = _heltall(kropp, "innhold_bytes", rid, 1, MAKS_BYTES)
        lagring = _tekst(kropp, "lagringsnokkel", rid, MAKS_REF)
        did = _utled("dokument", tenant, nokkel)
        return ("SELECT m54_registrer_dokument("
                "%s,%s,%s,%s,%s,%s::date,%s,%s,%s,%s)",
                (tenant, did, retning, ref, motpart, dato, sum_,
                 bytes_, lagring, bid), {"dokument_id": str(did)},
                None)
    return _skriv(tjeneste, request, bygg)


def registrer_felter_endepunkt(tjeneste, request):
    """POST /v1/ehf/dokument/{dokument_id}/felter.

    HELE SETTET I ETT KALL. En delvis parsing ville gitt en validering
    som så komplett ut mot et halvt dokument — og de manglende feltene
    ville blitt `uten_grunnlag` uten at noen skrev hvorfor.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        did = _sti_uuid(request, "dokument_id", rid)
        stier, forekomster, verdier, ore = _felter(kropp, rid)
        return ("SELECT m54_registrer_felter(%s,%s,%s,%s,%s,%s,%s)",
                (tenant, did, stier, forekomster, verdier, ore, bid),
                {"dokument_id": str(did)}, "antall")
    return _skriv(tjeneste, request, bygg)


def valider_endepunkt(tjeneste, request):
    """POST /v1/ehf/dokument/{dokument_id}/valider.

    EGEN RAMME, fordi svaret er en RAD. Den som validerer skal se hva
    dommen SIER — og særlig `antall_uten_grunnlag`: hvor mange regler
    som nevnte et felt vi ikke har trukket ut. De er ikke stille
    grønne, og et tall som ikke sto på svaret ville vært det.

    DØRA NEKTER MOT ET UTLØPT REGELSETT.
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
        did = _sti_uuid(request, "dokument_id", rid)
        sid = _kropp_uuid(kropp, "regelsett_id", rid)
        vid = _utled("validering", tenant, nokkel)
        try:
            rad = conn.execute(
                "SELECT * FROM m54_valider_dokument(%s,%s,%s,%s,%s)",
                (tenant, did, sid, vid, bid)).fetchone()
        except psycopg.Error as e:
            avbrudd = _doerfeil(e, rid)
            if avbrudd is None:
                raise
            raise avbrudd from e
        conn.commit()
        return _ok({"dokument_id": str(did),
                    "validering_id": str(vid),
                    "antall_regler": rad[0], "antall_feil": rad[1],
                    "antall_advarsler": rad[2],
                    "antall_uten_grunnlag": rad[3],
                    "gyldig": rad[4], "standard": rad[5],
                    "versjon": rad[6]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def registrer_retting_endepunkt(tjeneste, request):
    """POST /v1/ehf/avvik/{avvik_id}/retting (bestilling:opprett).

    DØRA NEKTER PÅ ET `uten_grunnlag`-AVVIK: en retting der vi ikke
    kunne dømme, ville endret fakturaen fordi vi manglet data — ikke
    fordi noe var galt.
    """
    def bygg(tenant, bid, nokkel, kropp, rid, request):
        aid = _sti_uuid(request, "avvik_id", rid)
        felt_sti = _sti(kropp, "felt_sti", rid)
        # NULL PÅ `fra_verdi` BETYR AT FELTET SKAL LEGGES TIL; tom
        # streng at det fantes og var tomt. To ulike rettinger.
        fra = kropp.get("fra_verdi")
        if fra is not None and (not isinstance(fra, str)
                                or len(fra) > MAKS_TEKST):
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        til = _tekst(kropp, "til_verdi", rid, MAKS_TEKST)
        begrunnelse = _tekst(kropp, "begrunnelse", rid, MAKS_TEKST)
        if len(begrunnelse) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        tid = _utled("retting", tenant, nokkel)
        return ("SELECT m54_registrer_retting(%s,%s,%s,%s,%s,%s,%s,%s)",
                (tenant, tid, aid, felt_sti, fra, til, begrunnelse,
                 bid), {"retting_id": str(tid)}, None)
    return _skriv(tjeneste, request, bygg)


def merk_klar_endepunkt(tjeneste, request):
    """POST /v1/ehf/retting/{retting_id}/klar (bestilling:opprett).

    «KLAR TIL SIGNERING» ER EN TILSTAND HOS OSS. Det finnes ingen
    signatur her og ingen utsending — de hører til v2.

    DØRA NEKTER så lenge dokumentet har en formfeil uten retting: å
    merke klar mens en formfeil står urettet, er å be et menneske
    signere på at noe er i orden som ikke er det.
    """
    def bygg(tenant, bid, _nokkel, _kropp, rid, request):
        tid = _sti_uuid(request, "retting_id", rid)
        return ("SELECT m54_merk_klar(%s,%s,%s)",
                (tenant, tid, bid), {"retting_id": str(tid)},
                "udekkede_feil")
    return _skriv(tjeneste, request, bygg)


def lukk_funn_endepunkt(tjeneste, request):
    """POST /v1/ehf/funn/{funn_id}/lukk (bestilling:opprett).

    `validering_mot_utlopt_regelsett` NEKTES av døra. Det funnet
    forsvinner når dokumentet valideres på nytt mot et gyldig sett — og
    det er en HANDLING, ikke en mening.
    """
    def bygg(tenant, bid, _nokkel, kropp, rid, request):
        fid = _sti_uuid(request, "funn_id", rid)
        notat = _tekst(kropp, "notat", rid, MAKS_TEKST)
        if len(notat) < 4:
            from .policyadmin_http import _Avbrudd, _feil
            raise _Avbrudd(_feil("request_feilformet", rid))
        return ("SELECT m54_lukk_funn(%s,%s,%s,%s)",
                (tenant, fid, notat, bid), {"funn_id": str(fid)},
                None)
    return _skriv(tjeneste, request, bygg)
