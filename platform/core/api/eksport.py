"""Firmaets egne data, som én fil (eiers vedtak 27/11: «les + eksport»).

HVORFOR: en kunde skal kunne ta med seg det hun har lagt inn — når hun
slutter, når prøveperioden er over, eller bare for å ha en kopi
(personvernforordningen art. 20 for persondataene; for resten er det
alminnelig anstendighet). Før fantes ingen vei ut uten å be oss om det.

HVA FILA ER: det samme som appen VISER, modul for modul — gjennom hver
moduls egen lesedør (`<modul>.svar_for`, samme liste som «Kom i gang»
bruker), aldri gjennom en kopi av spørringene. En eksport som leste
tabellene selv, ville drevet fra flatene den speiler, og den ville
trengt rettigheter API-et med vilje ikke har.

HVA DEN IKKE ER: en databasedump. Listene er avkortet slik flatene er;
det står i fila selv (`merknader`).

KONTAKTPUNKTENE I KLARTEKST (233): kunderegisterets adresser er
kundens egne, og en kunde som slutter, trenger dem. Døra gir ciphertext;
de dekrypteres HER, med tenantens DEK, og bare i denne veien.

SCOPET ER ADMINS ALENE (`firma:eksport`): fila samler alt firmaet har,
også det en `leser` ikke får se modul for modul. Hver modul tas likevel
bare med når økten OGSÅ har modulens eget lesescope — eksporten skal
aldri vise mer enn appen ville vist samme person.
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone

import psycopg

#: Ekstra lesedører utover «Kom i gang»-lista: firmaets kjerne som ikke er
#: en sveipmodul. (api-modul, funksjon, lesescope, kallform)
_EKSTRA = (
    ("kundeservice", "svar_for", "kundeservice:innhold", "med_emne"),
    ("epost_kilde", "kilder_for", "epost:read", "tenant"),
)

MERKNADER = {
    "nb": [
        "Fila viser det samme som appen viser, modul for modul.",
        "Lister i appen er avkortet til de nyeste radene per modul; det "
        "samme gjelder her. Feltet «vist» sier hvor mange som er med.",
    ],
}


def _modul(conn, apimodul: str, fn: str, kall: str, tenant: str):
    """Én modul i eget savepoint. -> (data, None) eller (None, feil)."""
    modul = importlib.import_module(f"{__package__}.{apimodul}")
    try:
        with conn.transaction():
            f = getattr(modul, fn)
            if kall == "med_emne":
                return f(conn, tenant, med_emne=True), None
            return f(conn, tenant), None
    except psycopg.Error as e:
        return None, type(e).__name__


def _legg_paa_kontakter(conn, tenant: str, kunder: list, utelatt: list):
    """Hver kundes kontaktpunkter, dekryptert. Et punkt som ikke lar seg
    dekryptere (en destruert nøkkel) står med masken og sier det."""
    from db import kryptering
    per_part: dict = {}
    try:
        with conn.transaction():
            rader = conn.execute(
                "SELECT * FROM part_kontakter_eksport(%s)", (tenant,)
            ).fetchall()
    except psycopg.Error:
        utelatt.append({"modul": "kontakter", "grunn": "kunne_ikke_leses"})
        return
    dekker: dict = {}
    for pid, kanal, maske, ct, nonce, key_id, primar, merkelapp in rader:
        punkt = {"kanal": kanal, "primar": primar, "merkelapp": merkelapp,
                 "verdi": None, "maske": maske}
        if ct is not None and nonce is not None and key_id:
            try:
                if key_id not in dekker:
                    dekker[key_id] = kryptering.hent_dek(conn, tenant, key_id)
                punkt["verdi"] = kryptering.dekrypter(
                    dekker[key_id], bytes(ct), bytes(nonce), tenant,
                    key_id).get("verdi")
            except Exception:                               # noqa: BLE001
                punkt["uleselig"] = True
        per_part.setdefault(str(pid), []).append(punkt)
    for k in kunder:
        k["kontakter"] = per_part.get(k["part_id"], [])


def eksport_for(conn, tenant: str, scopes) -> dict:
    from . import oppsett
    scopes = set(scopes or ())
    firma = conn.execute(
        "SELECT navn, orgnummer, status, prove_utloper, opprettet"
        "  FROM firma_hent(%s)", (tenant,)).fetchone()
    moduler: dict = {}
    utelatt: list = []
    for nr, apimodul, scope, _ in oppsett.ADAPTERE:
        if scope not in scopes:
            utelatt.append({"modul": apimodul, "grunn": "mangler_scope"})
            continue
        data, feil = _modul(conn, apimodul, "svar_for", "tenant", tenant)
        if feil:
            utelatt.append({"modul": apimodul, "grunn": "kunne_ikke_leses"})
            continue
        moduler[apimodul] = {"modulnummer": nr, "data": data}
    for apimodul, fn, scope, kall in _EKSTRA:
        if scope not in scopes:
            utelatt.append({"modul": apimodul, "grunn": "mangler_scope"})
            continue
        data, feil = _modul(conn, apimodul, fn, kall, tenant)
        if feil:
            utelatt.append({"modul": apimodul, "grunn": "kunne_ikke_leses"})
            continue
        moduler[apimodul] = {"data": data}
    kunder = None
    if "part:read" in scopes:
        try:
            with conn.transaction():
                kunder = [
                    {"part_id": str(r[0]), "part_ref": r[1], "navn": r[2],
                     "orgnummer": r[3], "parttype": r[4], "aktiv": r[5],
                     "epost_maske": r[6], "telefon_maske": r[7]}
                    for r in conn.execute(
                        "SELECT part_id, part_ref, navn, orgnummer, parttype,"
                        " aktiv, epost_maske, telefon_maske"
                        " FROM part_liste(%s,NULL,%s)", (tenant, 100000))]
        except psycopg.Error:
            utelatt.append({"modul": "parter", "grunn": "kunne_ikke_leses"})
        if kunder is not None:
            _legg_paa_kontakter(conn, tenant, kunder, utelatt)
    return {
        "format": "disponit-eksport/1",
        "eksportert": datetime.now(timezone.utc).isoformat(),
        "firma": ({"tenant": tenant, "navn": firma[0],
                   "orgnummer": firma[1], "status": firma[2],
                   "prove_utloper": firma[3], "opprettet": firma[4]}
                  if firma else {"tenant": tenant}),
        "kunder": kunder,
        "moduler": moduler,
        "utelatt": utelatt,
        "merknader": MERKNADER["nb"],
    }


def eksport_endepunkt(tjeneste, request):
    """GET /v1/firma/eksport (firma:eksport) — én JSON-fil til nedlasting."""
    from starlette.responses import Response

    from .lesing import _les

    def _fn(conn, auth, rid):
        data = eksport_for(conn, auth.tenant, auth.scopes)
        # SPORET: hvem som tok med seg alt, og når. Lesingen ruller
        # tilbake (husets lesevei), så det er loggen som husker det.
        tjeneste.logg.hendelse("firma_eksportert", rid, auth.tenant,
                               art="sikkerhet",
                               moduler=len(data["moduler"]))
        dato = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        kropp = json.dumps(data, ensure_ascii=False, indent=2, default=str,
                           sort_keys=True)
        return Response(kropp.encode("utf-8"), 200, headers={
            "content-type": "application/json; charset=utf-8",
            "content-disposition":
                f'attachment; filename="disponit-{auth.tenant}-{dato}.json"',
            "cache-control": "no-store",
            "x-request-id": rid})
    return _les(tjeneste, request, "firma:eksport", _fn)
