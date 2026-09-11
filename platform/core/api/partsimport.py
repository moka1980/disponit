"""Kundeimporten (183–186, PR 4): regnearket inn, én gang.

EIERS ORD 11/9: «Ikke gå gjennom hver modul og fylle, så hva blir vitsen
hvis alt må fylles manuelt». Registeret (183) ga ETT sted; flaten (PR 3)
ga en vei inn for én kunde. Denne gir veien inn for alle sammen.

TRE VEDTAK SOM BÆRER RESTEN:

1. SKRIVINGEN GÅR GJENNOM DE SAMME DØRENE SOM FLATEN. `part_registrer` og
   `part_sett_kontakt`, ikke en rask `INSERT`. En import som skrev forbi
   dørene ville vært en ANDRE skrivevei uten vaktene — og da er
   invariantene i 183/184/186 verdt mindre enn de ser ut.

2. TØRRKJØRING ER STANDARD. `torrkjoring` er `true` når feltet mangler:
   en kaller som glemmer flagget får en RAPPORT, ikke tusen rader. Å
   skrive er noe man ber om.

3. RAPPORTEN ER PER RAD, med linjenummeret fra fila. «Importen feilet»
   på 800 rader er ubrukelig; «linje 412: organisasjonsnummer er ikke ni
   siffer» kan rettes.

FEILENE ER KODER, ALDRI VERDIEN (088/181-formen): rapporten bærer
linjenummer og en tekstnøkkel, aldri innholdet i cellen. En importfil kan
bære personopplysninger i hver eneste rad, og en feilrapport som siterer
dem ville lagt dem i loggen, i svaret og på skjermen.
"""
from __future__ import annotations

import csv
import io

from starlette.requests import Request
from starlette.responses import Response

#: Taket er RUTENS (app.py `RUTEKROPPSGRENSER`), og dette er kontrakten
#: bak tallet: en rad med navn, referanse, orgnummer, e-post og telefon
#: er sjelden over 150 tegn, så 2 MiB rommer godt over 10 000 kunder.
MAKS_RADER = 5000

#: Kolonnenavn vi kjenner igjen, på norsk og engelsk. Ukjente kolonner
#: IGNORERES og navngis i rapporten — en import som stoppet på en ekstra
#: kolonne fra kundens eget regneark ville vært ubrukelig i praksis.
KOLONNER = {
    "part_ref": ("kundenummer", "kundenr", "referanse", "part_ref",
                 "customer_number", "reference"),
    "navn": ("navn", "kundenavn", "firma", "name", "customer", "company"),
    "orgnummer": ("organisasjonsnummer", "orgnummer", "orgnr",
                  "company_number", "org_number"),
    "epost": ("epost", "e-post", "epostadresse", "email", "e-mail"),
    "telefon": ("telefon", "telefonnummer", "mobil", "phone", "mobile"),
}


def _normaliser(navn: str) -> str:
    return navn.strip().lower().replace(" ", "_").lstrip("﻿")


def _kartlegg(felt: list[str]) -> tuple[dict, list[str]]:
    """(kolonne -> indeks, ukjente kolonnenavn)."""
    kart, ukjente = {}, []
    for i, raa in enumerate(felt or []):
        n = _normaliser(raa)
        for mal, alias in KOLONNER.items():
            if n in alias and mal not in kart:
                kart[mal] = i
                break
        else:
            if n:
                ukjente.append(raa.strip())
    return kart, ukjente


def _les(rad: list[str], kart: dict, felt: str) -> str:
    i = kart.get(felt)
    if i is None or i >= len(rad):
        return ""
    return (rad[i] or "").strip()


def _sjekk_rad(ref: str, navn: str, org: str, epost: str,
               telefon: str) -> str | None:
    """Tekstnøkkelen for det som er galt, eller None. ALDRI verdien."""
    if not ref:
        return "kundenummer_mangler"
    if len(ref) > 100:
        return "kundenummer_for_langt"
    if not navn:
        return "navn_mangler"
    if len(navn) > 200:
        return "navn_for_langt"
    if org and (len(org.replace(" ", "")) != 9
                or not org.replace(" ", "").isdigit()):
        return "orgnummer_ikke_ni_siffer"
    if epost and ("@" not in epost or epost.endswith("@")
                  or len(epost) > 320):
        return "epost_ugyldig"
    if telefon and len(telefon) > 320:
        return "telefon_for_langt"
    return None


def importer_endepunkt(tjeneste, request: Request) -> Response:
    """POST /v1/parter/import (part:administrer, idem).

    Kropp: `{"csv": "<tekst>", "torrkjoring": true|false}`.
    Svar: `{"lest": n, "gyldige": n, "feil": [{"linje": n, "grunn": kode}],
    "ukjente_kolonner": [...], "skrevet": n, "torrkjoring": bool}`.
    """
    from db import kryptering
    from db.pg import sett_kontekst

    from .app import _rid
    from .policyadmin_http import _feil, _kropp, _med_conn, _ok, _ok_lagret
    from .parter import _krev_csrf, _maske
    rid = _rid(request)

    def kjor(conn):
        import psycopg

        from . import kjerne
        from .app import _autentiser
        try:
            auth = _autentiser(tjeneste, request, conn, rid,
                               "part:administrer")
        except kjerne.Feilsvar as f:
            return _feil(f.kode, rid)
        avbrudd = _krev_csrf(tjeneste, request, conn, rid, auth)
        if avbrudd is not None:
            return avbrudd
        k = _kropp(request)
        tekst = k.get("csv")
        if not isinstance(tekst, str) or not tekst.strip():
            return _feil("request_feilformet", rid, 400, detalj="csv")
        # STANDARD ER TØRRKJØRING: bare et uttrykkelig `false` skriver.
        torr = k.get("torrkjoring", True) is not False

        # `csv.Sniffer` er BEVISST IKKE BRUKT: den gjetter, og den gjetter
        # feil på filer med få rader. Semikolon er norsk Excels standard
        # og komma er resten av verdens; vi teller hvilken som finnes i
        # overskriftslinja og velger den.
        forste = tekst.lstrip().splitlines()[0] if tekst.strip() else ""
        skilletegn = ";" if forste.count(";") > forste.count(",") else ","
        # LINJENUMMERET ER FILAS, ikke radens plass etter filtrering
        # (CodeRabbit). Første utgave kastet tomme linjer FØRST og
        # nummererte etterpå — så en fil med én blank linje på toppen ga
        # «linje 411» om feilen på linje 412, og hele poenget med en
        # rapport per rad falt. `csv.reader.line_num` teller FILAS
        # linjer, og for et felt som går over flere linjer peker den på
        # den SISTE av dem; det er den ærlige lesningen, ikke en penere.
        try:
            leser = csv.reader(io.StringIO(tekst), delimiter=skilletegn)
            rader = [(leser.line_num, r) for r in leser
                     if any((c or "").strip() for c in r)]
        except csv.Error:
            return _feil("request_feilformet", rid, 400, detalj="csv")
        if not rader:
            return _feil("request_feilformet", rid, 400, detalj="csv er tom")
        kart, ukjente = _kartlegg(rader[0][1])
        if "part_ref" not in kart or "navn" not in kart:
            return _feil("request_feilformet", rid, 400,
                         detalj="overskriftslinjen mangler kundenummer"
                                " eller navn")
        datarader = rader[1:]
        if len(datarader) > MAKS_RADER:
            return _feil("request_feilformet", rid, 400,
                         detalj=f"over {MAKS_RADER} rader")

        feil: list[dict] = []
        gyldige: list[tuple] = []
        sett_ref: set[str] = set()
        for nr, rad in datarader:
            ref = _les(rad, kart, "part_ref")
            navn = _les(rad, kart, "navn")
            org = _les(rad, kart, "orgnummer")
            epost = _les(rad, kart, "epost")
            telefon = _les(rad, kart, "telefon")
            grunn = _sjekk_rad(ref, navn, org, epost, telefon)
            if grunn is None and ref in sett_ref:
                # SAMME KUNDENUMMER TO GANGER I SAMME FIL er en feil i
                # FILA, ikke i registeret: dørene ville tatt den siste
                # stille, og brukeren ville aldri fått vite at to rader
                # kolliderte.
                grunn = "kundenummer_gjentatt_i_fila"
            if grunn is not None:
                feil.append({"linje": nr, "grunn": grunn})
                continue
            sett_ref.add(ref)
            gyldige.append((ref, navn, org.replace(" ", "") or None,
                            epost, telefon))

        rapport = {"lest": len(datarader), "gyldige": len(gyldige),
                   "feil": feil[:MAKS_RADER],
                   "ukjente_kolonner": ukjente,
                   "skilletegn": skilletegn,
                   "torrkjoring": torr, "skrevet": 0}
        if torr:
            return _ok(rapport, rid)

        # ALT ELLER INGENTING. En import som skrev de 700 første og stoppet
        # på rad 701 ville etterlatt registeret i en tilstand brukeren
        # ikke ba om, og en ny kjøring ville vært umulig å resonnere om.
        # Dørene er idempotente hver for seg; transaksjonen gjør hele
        # importen det.
        conn.rollback()
        sett_kontekst(conn, auth.tenant, auth.aktor, rid)
        key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, auth.tenant)
        try:
            with conn.transaction():
                for ref, navn, org, epost, telefon in gyldige:
                    pid = conn.execute(
                        "SELECT part_registrer(%s,%s,%s,%s,'bedrift',%s)",
                        (auth.tenant, ref, navn, org,
                         auth.aktor)).fetchone()[0]
                    for kanal, verdi in (("epost", epost),
                                         ("telefon", telefon)):
                        if not verdi:
                            continue
                        ct, nonce = kryptering.krypter(
                            dek, {"verdi": verdi}, auth.tenant, key_id)
                        psn = conn.execute("SELECT tenant_pseudonym(%s,%s)",
                                           (auth.tenant,
                                            verdi)).fetchone()[0]
                        conn.execute(
                            "SELECT part_sett_kontakt(%s,%s,%s,%s,%s,%s,%s,"
                            "%s,true,NULL,%s)",
                            (auth.tenant, pid, kanal, _maske(verdi, kanal),
                             ct, nonce, key_id, psn, auth.aktor))
        except psycopg.errors.IntegrityConstraintViolation:
            return _feil("part_ulovlig_tilstand", rid, 409)
        rapport["skrevet"] = len(gyldige)
        return _ok_lagret(conn, rapport, rid)

    return _med_conn(tjeneste, rid, kjor)
