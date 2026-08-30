"""Lese-API-et for M-1-kundeflaten (PR-008, spesifikasjon v1–v6).

Seks endepunkter, rent lesende: oversikt, beslutningsliste, beslutnings-
detalj, unntaksdetalj, unntakshistorikk og aktiv policy. Ingen mutasjon,
ingen ny forretningslogikk — modulen UTLEDER alt fra radene de skrivende
veiene allerede har committet, og hver utledning går via en eksplisitt
nøkkel (FK, loggpost-id, repair_operation_id), aldri via tidsnærhet eller
«siste rad» (v3 pkt. 1–2).

Tre ortogonale akser i beslutningsdetaljen (v2 pkt. 1):
  resultat        — policybeslutning + ordinær utførelsestilstand (union)
  evidensstatus   — IKKE_RELEVANT | MANGLER | GYLDIG, med `sen_evidens` og
                    `konflikt_evidens` som AVLEDEDE flagg (v5 pkt. 2)
  sikkerhet       — scope-styrt; FRAVÆR er en del av skjemaet, aldri false

Alle handlerne arver nettverksinngangens porter: pre-auth i egen
transaksjon, `sett_kontekst` som FØRSTE operasjon i den autentiserte
transaksjonen, RLS+FORCE, identisk 404 for ukjent og annen tenants ID.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import psycopg
from starlette.requests import Request
from starlette.responses import Response

from db.pg import sett_kontekst
from policy_validator.engine import les_policyref

from . import cursor as cursormodul
from . import kjerne

# Importeres av `app.lag_app()` ETTER at hjelperne under er definert —
# modulnivå-importen her er derfor trygg selv om `app` importerer oss.
from .app import (kanonisk_json, _feilsvar, _rid, _autentiser,
                  SIDE_STANDARD, LESESCOPES, _KANDIDAT_NS)

#: PR-008-listene har eget tak (spesifikasjonen: limit <= 100, default 50).
#: `SIDE_MAKS` (200) gjelder det eksisterende `/v1/unntak` og røres ikke.
LISTE_MAKS = 100


# ---------------------------------------------------------------------------
# Felles handler-ramme: pool, pre-auth, kontekst, feiloversettelse.
# ---------------------------------------------------------------------------

def _les(tjeneste, request: Request, scope: str, fn) -> Response:
    """Rammen rundt hvert leseendepunkt.

    `fn(conn, auth, rid)` kjører med tenantkontekst satt og skal returnere
    en Response. Transaksjonen rulles alltid tilbake — et leseendepunkt som
    kan committe er ett refaktoreringsuhell unna å bli et skrivende.
    """
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        tjeneste.logg.hendelse("db_utilgjengelig", rid, art="drift")
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            auth = _autentiser(tjeneste, request, conn, rid, scope)
        except kjerne.Feilsvar as f:
            return _feilsvar(f.kode, rid)
        try:
            # Kontekst FØRST i den autentiserte transaksjonen — nøyaktig
            # samme inngang som beslutningsveien og `/v1/unntak`.
            sett_kontekst(conn, auth.tenant, auth.aktor, rid)
            return fn(conn, auth, rid)
        except kjerne.Feilsvar as f:
            tjeneste.logg.hendelse(f.kode, rid, auth.tenant)
            return _feilsvar(f.kode, rid)
        except psycopg.Error as e:
            tjeneste.logg.hendelse("db_utilgjengelig", rid, auth.tenant,
                                   art="drift", feiltype=type(e).__name__)
            return _feilsvar("db_utilgjengelig", rid)
        finally:
            try:
                conn.rollback()
            except psycopg.Error:
                pass
    finally:
        tjeneste.pool.gi_tilbake(conn)


def _koder(begrunnelse) -> list[str]:
    """Begrunnelsen som display-safe KODER — aldri params (v2 Del 3.1)."""
    if not isinstance(begrunnelse, list):
        return []
    return [g["kode"] for g in begrunnelse
            if isinstance(g, dict) and isinstance(g.get("kode"), str)]


def _grense(request: Request) -> int | None:
    try:
        grense = int(request.query_params.get("limit", SIDE_STANDARD))
    except ValueError:
        return None
    if grense < 1 or grense > LISTE_MAKS:
        return None
    return grense


# ---------------------------------------------------------------------------
# GET /v1/oversikt — scope decisions:read
# ---------------------------------------------------------------------------

def oversikt(tjeneste, request: Request) -> Response:
    def _fn(conn, auth, rid):
        slutt = datetime.now(timezone.utc)
        start = slutt - timedelta(hours=24)
        # Én spørring, ett snapshot: invarianten
        # `tillatt + stoppet + unntak = totalt` holder per konstruksjon
        # fordi alle fire tallene telles i samme skann. Beregningen er UTC
        # (v2 svar 2) — tidssone er presentasjon og gjøres i UI-et.
        rad = conn.execute(
            "SELECT COUNT(*) FILTER (WHERE beslutning='TILLAT'),"
            "       COUNT(*) FILTER (WHERE beslutning='STOPP'),"
            "       COUNT(*) FILTER (WHERE beslutning='UNNTAK'),"
            "       COUNT(*)"
            "  FROM revisjonslogg WHERE tenant=%s AND ts >= %s AND ts < %s",
            (auth.tenant, start, slutt)).fetchone()
        return kanonisk_json(
            {"vindu_start": start.isoformat(), "vindu_slutt": slutt.isoformat(),
             "tidssone": "UTC", "tillatt": rad[0], "stoppet": rad[1],
             "unntak": rad[2], "totalt": rad[3], "request_id": rid},
            200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


# ---------------------------------------------------------------------------
# GET /v1/nokkeltall — scope decisions:read (M-16 v1, ren lesing)
# ---------------------------------------------------------------------------

#: DEN ENE vindusdefinisjonen (M-16 §3): UTC, halvåpent `[fra, til)`,
#: forhåndsdefinerte vinduer. En hendelse nøyaktig på `til` tilhører
#: neste vindu — det følger av `< til` i hver definer, og ingen
#: kortspørring har egen vindusaritmetikk: alle får NØYAKTIG paret
#: herfra (statisk port).
NOKKELTALL_VINDUER = {"24t": timedelta(hours=24),
                      "7d": timedelta(days=7),
                      "30d": timedelta(days=30)}

#: Radgrensen for lukkede-listen. Den er et VISNINGSTAK på radfakta, ikke
#: en telling: svaret bærer alltid `unntak_lukkede_totalt` ved siden av,
#: så et avkuttet utsnitt er synlig for klienten (M-16 §3).
NOKKELTALL_LUKKEDE_GRENSE = 50


def _nokkeltall_vindu(request: Request) -> tuple[datetime, datetime] | None:
    # Fritt intervall er BEVISST ikke implementert i v1 (§3:
    # forhåndsdefinerte vinduer). En klient som likevel ber om `fra`/`til`
    # skal få 400 — ikke et urelatert 24-timerssvar med status 200. Et
    # eksplisitt spørsmål besvares aldri stille med noe annet.
    if "fra" in request.query_params or "til" in request.query_params:
        return None
    valg = request.query_params.get("vindu", "24t")
    lengde = NOKKELTALL_VINDUER.get(valg)
    if lengde is None:
        return None
    til = datetime.now(timezone.utc)
    return til - lengde, til


def _partisjon(rader) -> dict:
    """(er_total, nokkel, antall) → {'total': n, 'deler': {nokkel: antall}}.

    Totalen og delene kommer fra SAMME skann (GROUPING SETS i defineren),
    så suminvarianten holder per konstruksjon — den KONTROLLERES likevel
    her, fail-closed: et avvik er en definerfeil og skal høres, ikke
    vises som pene tall.

    Aggregatet kjennes på `er_total` — en egenskap ved RADEN — og ikke på
    en reservert nøkkelverdi. Merket var før strengen `__total__` i samme
    kolonne som kategoriene, men `unntak.kategori` er en fri TEXT-kolonne:
    en sak med nøyaktig den kategorien var ikke til å skille fra
    aggregatet, og kontrollen under ville da slått ut på data som var helt
    i orden — eller, i et sett med bare den kategorien, gitt et kort uten
    en eneste rad. Nå finnes det ingen kategoristreng som kan kollidere.
    """
    total = 0
    deler: dict[str, int] = {}
    for er_total, nokkel, antall in rader:
        if er_total:
            total = antall
        else:
            deler[nokkel] = antall
    if sum(deler.values()) != total:
        raise kjerne.Feilsvar("intern_feil")
    return {"total": total, "deler": deler}


def nokkeltall(tjeneste, request: Request) -> Response:
    """M-16: nøkkeltall regnet fra faktiske beslutninger — telling over
    rader som finnes, radvise varigheter, aldri analyse. Generaliseringen
    av 24-timerssammendraget i `oversikt`: samme filtertelling, valgbart
    vindu, alt via definere (SP-1/SP-7)."""
    def _fn(conn, auth, rid):
        vindu = _nokkeltall_vindu(request)
        if vindu is None:
            return _feilsvar("request_feilformet", rid)
        fra, til = vindu
        arg = (auth.tenant, fra, til)
        beslutninger = _partisjon(conn.execute(
            "SELECT er_total, nokkel, antall FROM m16_beslutninger(%s,%s,%s)",
            arg).fetchall())
        reservasjoner = conn.execute(
            "SELECT m16_frekvensreservasjoner(%s,%s,%s)", arg).fetchone()[0]
        aktiveringer: dict[str, list] = {}
        for partisjon, er_total, nokkel, antall in conn.execute(
                "SELECT partisjon, er_total, nokkel, antall FROM"
                " m16_aktiveringer(%s,%s,%s)", arg).fetchall():
            aktiveringer.setdefault(partisjon, []).append(
                (er_total, nokkel, antall))
        oppdrag = _partisjon(conn.execute(
            "SELECT er_total, nokkel, antall FROM m16_oppdrag(%s,%s,%s)",
            arg).fetchall())
        # Terminalsettet er app-lagets ENE definisjon — statusmaskinen
        # kopieres aldri inn i SQL (oversikt-lærdommen fra #105-æraen).
        # Sakstypesettet kommer samme vei og av samme grunn: hvilke køer
        # DETTE tokenet får se er en scope-regel, og den har ett hjem
        # (`app.synlige_sakstyper`). Uten den ville nøkkeltallene vært en
        # sidevei rundt `security:read`: tellingene, «åpne nå» og de
        # lukkede radene leser `unntak` direkte, altså utenom
        # unntakslistens egen sakstypeport. Skjulte rader nevnes IKKE i
        # svaret — her er eksistensen det vernede (samme grunn som at
        # `_hent_unntak` svarer `ikke_funnet`, ikke 403).
        from .app import TERMINALE_UNNTAKSSTATUSER, synlige_sakstyper
        terminale = list(TERMINALE_UNNTAKSSTATUSER)
        sakstyper = list(synlige_sakstyper(auth.scopes))
        aktivitet = _partisjon(conn.execute(
            "SELECT er_total, nokkel, antall FROM"
            " m16_unntak_aktivitet(%s,%s,%s,%s)",
            (*arg, sakstyper)).fetchall())
        # Radlisten har en grense — og grensen er ALDRI stille: defineren
        # returnerer hele tellingen i vinduet (`antall_totalt`, samme
        # skann), så svaret bærer både utsnittet og hvor stort settet er.
        # Klienten kan dermed si «viser N av M»; den kan aldri komme til å
        # påstå at N ER alle lukkede saker i vinduet.
        lukkede_rader = conn.execute(
            "SELECT id, kategori, sakstype, status, opprettet,"
            " lukket, varighet_s, antall_totalt FROM"
            " m16_unntak_lukkede(%s,%s,%s,%s,%s,%s)",
            (*arg, terminale, sakstyper,
             NOKKELTALL_LUKKEDE_GRENSE)).fetchall()
        lukkede = [
            {"id": r[0], "kategori": r[1], "sakstype": r[2],
             "status": r[3], "opprettet": r[4].isoformat(),
             "lukket": r[5].isoformat(), "varighet_s": r[6]}
            for r in lukkede_rader]
        # Tom liste ⇒ 0; ellers er tellingen lik i hver rad (window over
        # hele settet), så første rad holder.
        lukkede_totalt = lukkede_rader[0][7] if lukkede_rader else 0
        # TILSTAND, ikke aktivitet: «åpne nå» står utenfor vinduet.
        apne_naa = conn.execute(
            "SELECT m16_unntak_apne(%s,%s,%s)",
            (auth.tenant, terminale, sakstyper)).fetchone()[0]
        tick = _partisjon(conn.execute(
            "SELECT er_total, nokkel, antall FROM m16_tick(%s,%s,%s)",
            arg).fetchall())
        return kanonisk_json(
            {"vindu_start": fra.isoformat(), "vindu_slutt": til.isoformat(),
             "tidssone": "UTC",
             "beslutninger": beslutninger,
             "frekvensreservasjoner": reservasjoner,
             "aktiveringer": {
                 p: _partisjon(rader)
                 for p, rader in aktiveringer.items()},
             "oppdrag": oppdrag,
             "unntak_aktivitet": aktivitet,
             "unntak_lukkede": lukkede,
             "unntak_lukkede_totalt": lukkede_totalt,
             "unntak_lukkede_grense": NOKKELTALL_LUKKEDE_GRENSE,
             "apne_naa": apne_naa,
             "tick": tick,
             "request_id": rid},
            200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


# ---------------------------------------------------------------------------
# GET /v1/beslutninger — scope decisions:read, keyset DESC, filterbundet
# ---------------------------------------------------------------------------

def beslutninger(tjeneste, request: Request) -> Response:
    def _fn(conn, auth, rid):
        grense = _grense(request)
        if grense is None:
            return _feilsvar("request_feilformet", rid)
        filter_beslutning = request.query_params.get("policybeslutning")
        if filter_beslutning is not None \
                and filter_beslutning not in ("TILLAT", "STOPP", "UNNTAK"):
            return _feilsvar("request_feilformet", rid)
        filtre = {} if filter_beslutning is None \
            else {"policybeslutning": filter_beslutning}

        etter = None
        raa = request.query_params.get("cursor")
        if raa:
            try:
                etter = cursormodul.les_v2(
                    raa, tjeneste.cursorpepper, tenant=auth.tenant,
                    endepunkt="beslutninger", retning="desc", filtre=filtre)
            except cursormodul.CursorUgyldig:
                tjeneste.logg.hendelse("cursor_ugyldig", rid, auth.tenant)
                return _feilsvar("cursor_ugyldig", rid)

        sql = ("SELECT id, ts, handling, beslutning, begrunnelse"
               "  FROM revisjonslogg WHERE tenant=%s")
        args: list = [auth.tenant]
        if filter_beslutning is not None:
            sql += " AND beslutning=%s"
            args.append(filter_beslutning)
        if etter is not None:
            # Ærlig keyset (v4 pkt. 3): ingen duplikater for uendrede rader;
            # samtidig innsetting KAN bli synlig eller utelatt mellom sider.
            # Det er dokumentert semantikk, ikke et snapshotløfte.
            sql += " AND (ts, id) < (%s, %s)"
            args += [etter[0], etter[1]]
        sql += " ORDER BY ts DESC, id DESC LIMIT %s"
        args.append(grense)
        rader = conn.execute(sql, tuple(args)).fetchall()

        ut = [{"id": r[0], "ts": r[1].isoformat(), "handling": r[2],
               "policybeslutning": r[3], "begrunnelse": _koder(r[4])}
              for r in rader]
        neste = None
        if len(rader) == grense:
            neste = cursormodul.lag_v2(
                tjeneste.cursorpepper, tenant=auth.tenant,
                endepunkt="beslutninger", retning="desc", filtre=filtre,
                ts=rader[-1][1], rad_id=rader[-1][0])
        return kanonisk_json({"rader": ut, "neste_cursor": neste,
                              "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


# ---------------------------------------------------------------------------
# GET /v1/beslutninger/{id} — de tre aksene
# ---------------------------------------------------------------------------

#: Oppdragsstatus -> resultat.art. Lukket — en ny status i databasen skal
#: gi KeyError her (og dermed sanitert 500), aldri en gjettet art.
_ART_FOR_STATUS = {"opprettet": "outbox_opprettet",
                   "plukket": "outbox_plukket",
                   "utfort": "outbox_utfort",
                   "feilet": "outbox_feilet",
                   "kansellert": "outbox_kansellert"}


def _kombinasjon_lovlig(art: str, ev: str, sen: bool, konflikt: bool) -> bool:
    """Den TOTALE matrisen (v4 pkt. 2 + v5 pkt. 2). Alt utenfor avvises.

    Dette er servermodellens egen vakt: utledningen over skal per
    konstruksjon aldri produsere noe utenfor matrisen, og nettopp derfor
    måles den — en utledning som ikke kan feile trenger ingen vakt, men en
    vakt som aldri kan utløses beviser at utledningen holder.
    """
    if art in ("policy_stoppet", "sideeffektfri_tillatt", "til_unntak",
               "utforelsesdata_ikke_tilgjengelig"):
        return ev == "IKKE_RELEVANT" and not sen and not konflikt
    if art in ("outbox_opprettet", "outbox_plukket"):
        return ev == "MANGLER"
    if art == "outbox_utfort":
        # v5: identisk sen replay er no-op, så en registrert sen kvittering
        # på et utført oppdrag MÅ være avvikende — sen uten konflikt har
        # ingen legitim databasevei.
        return ev == "GYLDIG" and (konflikt or not sen)
    if art == "outbox_feilet":
        return ev in ("GYLDIG", "MANGLER")
    if art == "outbox_kansellert":
        return ev in ("IKKE_RELEVANT", "GYLDIG")
    return False


#: Leseflaten DETTE endepunktet er — `ui/static/js/flater/rapport.js`.
#: `rapportflate` på oppdragstypen navngir hvilken rendrer som kan vise
#: rapportformen; her står navnet på den ene rendreren denne veien
#: serverer, så filteret under kan sammenligne verdier i stedet for å
#: spørre om feltet i det hele tatt er satt.
RAPPORTFLATE = "wcag"


def rapport_detalj(tjeneste, request: Request) -> Response:
    """GET /v1/rapport/{oppdrag_id} — den promoterte WCAG-rapporten (038 §7).

    Scope `decisions:read`: rapporten er evidensen bak en beslutning
    tenanten selv bestilte, og lese-API-et viser alt annet om den
    beslutningen under samme scope. Identisk 404 for «finnes ikke»,
    «ikke ditt» og «ikke promotert» — tilstanden til et annet oppdrag er
    ikke informasjon dette scopet skal bekrefte.

    Dekrypteringen skjer her og bare her på leseveien: artefaktlageret er
    kryptert i ro, og klienten får aldri ciphertext/nøkkelreferanser —
    kun rapportdokumentet som ble validert mot det lukkede skjemaet ved
    promoteringen.
    """
    def _fn(conn, auth, rid):
        import oppdragskontrakt
        oid = request.path_params["id"]
        # BARE RAPPORTBÆRENDE TYPER (Codex P2). Uten JOIN-en og de to
        # typefiltrene svarte endepunktet med det NYESTE promoterte
        # artefaktet på oppdraget, uansett artefakttype og uansett hvilken
        # oppdragstype som fødte det. `rapportInnhold` på flaten
        # dereferer `sammendrag` og `sider_kontrollert` med en gang, så et
        # artefakt fra en hvilken som helst annen registrert kontrakt ga
        # 200 og en rapportvisning som kastet under rendring. Paret
        # (oppdragstype, artefakttype) kommer fra kontrakten — samme kilde
        # registreringen skriver registerraden fra — så en ny
        # rapportbærende type blir lesbar ved å DEKLARERE seg, ikke ved at
        # noen husker å utvide en liste her.
        # … og bare typer med DENNE leseflaten (Codex P2). Å bære en
        # rapportartefakttype er ikke det samme som å ha en konsument:
        # `rapportInnhold` på flaten dereferer WCAG-formen med en gang,
        # så en ny rapportbærende kontrakt uten flate ville fått 200 her
        # og feilet under rendring hos klienten. En type uten flate er
        # ikke lesbar her ennå, og 404 er det ærlige svaret.
        #
        # `rapportflate` er en DISKRIMINATOR, ikke en boolsk (Codex P2).
        # Leddet spurte bare om feltet var satt, og da var det bare M-57s
        # manglende flate som holdt M-57-rapporten unna WCAG-rendreren.
        # Den dagen CP4 gir modulen sin egen flate, ville `"ats"` blitt
        # servert hit igjen — nøyaktig 200-og-feiler-under-rendring dette
        # leddet ble lagt til for å hindre, og med den TAUSESTE mulige
        # utløseren: at en helt annen kontrakt fylte ut sitt eget felt.
        # En ny flate blir lesbar ved å få sin EGEN vei, ikke ved å arve
        # denne.
        par = [(navn, t.rapport_artefakttype)
               for navn, t in oppdragskontrakt.OPPDRAGSTYPER.items()
               if t.rapport_artefakttype is not None
               and t.rapportflate == RAPPORTFLATE]
        rad = conn.execute(
            "SELECT a.artefakt_id, a.ciphertext, a.nonce, a.dek_ref,"
            " a.promotert_ts, a.artefakttype"
            "  FROM artefakt a JOIN oppdrag o"
            "    ON o.tenant = a.tenant AND o.id = a.oppdrag_id"
            " WHERE a.tenant=%s AND a.oppdrag_id=%s"
            "   AND a.tilstand='promotert'"
            "   AND (o.oppdragstype, a.artefakttype) IN"
            "       (SELECT * FROM unnest(%s::text[], %s::text[]))"
            " ORDER BY a.promotert_ts DESC LIMIT 1",
            (auth.tenant, oid, [p[0] for p in par],
             [p[1] for p in par])).fetchone()
        if rad is None:
            # Samme 404 som «finnes ikke» og «ikke ditt»: en type flaten
            # ikke kan vise er dokumentert som ikke-funnet, ikke som en
            # halvveis 200.
            return _feilsvar("ikke_funnet", rid)
        art_id, ct, nonce, dek_ref, ts, artefakttype = rad
        from db import kryptering
        try:
            dek = kryptering.hent_dek(conn, auth.tenant, dek_ref)
            rapport = kryptering.dekrypter(dek, ct, nonce, auth.tenant,
                                           dek_ref)
        except Exception:
            # En promotert rad som ikke lar seg dekryptere er en
            # servertilstand (nøkkel destruert, korrupsjon) — aldri noe
            # klienten skal tolke som «rapporten finnes ikke».
            tjeneste.logg.hendelse("intern_feil", rid, auth.tenant,
                                   art="drift", artefakt=str(art_id))
            return _feilsvar("intern_feil", rid)
        return kanonisk_json({
            "oppdrag_id": oid,
            "artefakt_id": str(art_id),
            "artefakttype": artefakttype,
            "promotert_ts": ts.isoformat() if ts else None,
            "rapport": rapport,
            "request_id": rid,
        }, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


RAPPORTFLATE_ATS = "ats"


def _m57_avslatt(tjeneste, request: Request):
    """Er `m57_ats` rullet tilbake? -> ferdig 503-svar, ellers None.

    ROLLBACK-KONTRAKTEN GJELDER OGSÅ LESEVEIEN (Codex P1). De skrivende og
    lesende M-57-rutene i `rekruttering.py` avviser med 503 `modul_inaktiv`
    når `DISPONIT_INAKTIVE_MODULER` navngir modulen; de to rutene her gikk
    rett i `_les`. Å deaktivere `m57_ats` i drift stanset dermed resten av
    flaten mens evalueringshistorikken og de DEKRYPTERTE rapportene sto
    åpne — og rapporten er den formen med mest kandidatdata i seg.

    SAMME PORT, IKKE EN KOPI: `rekruttering._modul_inaktiv` er kilden, så
    modulnavnet, loggformen og statusoppslaget i `feil.FEIL` ikke kan
    divergere mellom de to filene. Importen er lokal — `rekruttering`
    importerer `app`, som importerer oss.

    FØR TILKOBLINGEN, av samme grunn som der: en deaktivert modul skal
    ikke bruke en poolplass eller åpne en transaksjon som må rulles.
    `_rid` er idempotent (den cacher på `request.scope["state"]`), så
    503-svaret bærer samme request-id som `_les` ville gitt.
    """
    from . import rekruttering
    return rekruttering._modul_inaktiv(tjeneste, _rid(request))


def _anker_lever(conn, tenant, oppdrag_id) -> bool:
    """Lever retensjonsankeret NÅ? Re-sjekken bak TOCTOU-dommen (#220,
    eierdom): hovedspørringens EXISTS(levende anker) og payloadleveransen
    er to tidspunkter, og en reap kan committe i vinduet mellom dem.
    Denne leses rett før 200, ETTER dekrypteringen, i samme transaksjon
    (READ COMMITTED tar ferskt snapshot per setning) — payloaden forlater
    aldri prosessen etter en reap som var committet da beslutningen ble
    tatt. VINDUSINNSNEVRING, ikke lås: FOR SHARE fra leseveien ville
    krevd UPDATE-rett på retensjonsankeret for en READ-rute — et felt
    rettighetsvedtak (migrer.py:86-95) denne dommen nekter å reversere.
    Den interleavede bevisriggen bor i eget issue."""
    # `clock_timestamp()`, ikke `now()` (Codex P2): `now()` er
    # transaksjonens STARTTID og identisk i hovedspørringen og her — en
    # dekryptering som drar forbi fristen ville bestått re-sjekken med
    # det samme klokkeslettet den alt besto med. Re-sjekkens hele poeng
    # er et FERSKERE tidspunkt.
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM rekrutteringsprosess p"
        "  WHERE p.tenant=%s AND p.oppdrag_id=%s"
        "    AND p.slettet_ts IS NULL"
        "    AND p.slett_bestilt_ts IS NULL"
        "    AND clock_timestamp() < coalesce(p.lukket_ts, p.opprettet)"
        "                + p.slettefrist_dogn * interval '1 day')",
        (tenant, oppdrag_id)).fetchone()[0]


def _artefakt_lever(conn, tenant, artefakt_id) -> bool:
    """Er artefaktet fortsatt UMAKULERT med levende payload NÅ?

    Søsteren til `_anker_lever`, og den lukker det vinduet ankeret ikke
    ser (Codex P2 på #252). `makuler_artefakter_for_prosess` (#222) kan
    committe UTEN at prosessen merkes reapet — det er en form #252
    eksplisitt støtter og tester, siden døren kalles per oppdrag og
    reapmerket settes av et eget steg. Ankeret lever da fortsatt og
    `_anker_lever` sier true, mens raden vi alt har lest er tømt.

    Uten denne leser hovedspørringen ciphertexten rett før makuleringen
    committer, dekrypterer den, passerer ankersjekken — og leverer 200
    med kandidatpayload som er slettet i basen. Ankeret måler PROSESSENS
    frist; dette måler ARTEFAKTETS eget merke, og det er to forskjellige
    fakta.

    Samme doktrine som søsteren for øvrig: leses rett før 200, etter
    dekrypteringen, i samme transaksjon — READ COMMITTED tar ferskt
    snapshot per setning. Vindusinnsnevring, ikke lås; en FOR SHARE
    herfra ville krevd skriverett på evidenstabellen for en READ-rute.
    Ingen klokkeslett her: merket er et faktum, ikke en frist.
    """
    return conn.execute(
        "SELECT EXISTS (SELECT 1 FROM artefakt a"
        "  WHERE a.tenant=%s AND a.artefakt_id=%s"
        "    AND a.makulert_ts IS NULL AND a.ciphertext IS NOT NULL)",
        (tenant, artefakt_id)).fetchone()[0]


def _funn_fra_lageret(conn, tenant: str, oppdrag_id: int,
                      rapport: dict) -> dict:
    """v2-rapportens kandidatdetaljer, lest fra 057-lageret
    (BESLUTNING-168: beslutningssporet bærer referanser, aldri funn —
    funnene bor i `kandidat_evalueringsartefakt` og lever til kundens
    frist, nøyaktig så lenge som denne ruten i det hele tatt svarer).

    Samme lesedoktrine som `rekruttering._kandidater`: nøkkel-
    subtraksjon av `kildetekst`/`avmaskering` i basen (aldri hentet), og
    et artefakt som ikke er et objekt normaliseres ALDRI til noe
    grønnere — funnlisten serveres som lest, eller tom når raden ikke
    bærer en liste. Avgrensningen er rangeringens eget kandidatsett;
    lagernøkkelen er skrivedørens deterministiske uuid5 over
    (tenant, prosess, kandidat) — samme navnerom, samme skilletegn."""
    prad = conn.execute(
        "SELECT prosess_id FROM rekrutteringsprosess"
        " WHERE tenant=%s AND oppdrag_id=%s AND slettet_ts IS NULL",
        (tenant, oppdrag_id)).fetchone()
    rangering = rapport.get("rangering")
    kids = [r.get("kandidat_id") for r in rangering
            if isinstance(r, dict)
            and isinstance(r.get("kandidat_id"), str)] \
        if isinstance(rangering, list) else []
    if prad is None or not kids:
        return {}
    kart = {uuid.uuid5(_KANDIDAT_NS,
                       f"{tenant}\x1f{prad[0]}\x1f{kid}"): kid
            for kid in kids}
    rader = conn.execute(
        "SELECT a.kandidat_id,"
        "       CASE WHEN jsonb_typeof(a.artefakt) = 'object'"
        "            THEN a.artefakt - 'kildetekst' - 'avmaskering'"
        "       END"
        "  FROM kandidat_evalueringsartefakt a"
        " WHERE a.tenant=%s AND a.prosess_id=%s"
        "   AND a.slettet_ts IS NULL AND a.kandidat_id = ANY(%s)",
        (tenant, prad[0], list(kart))).fetchall()
    ut = {}
    for kid_uuid, art in rader:
        raa = art.get("funn") if isinstance(art, dict) else None
        ut[kart[kid_uuid]] = {"funn": raa if isinstance(raa, list)
                              else []}
    return ut


def rekrutteringsrapport_detalj(tjeneste, request: Request) -> Response:
    """GET /v1/rekruttering/rapport/{oppdrag_id} — den promoterte
    evalueringsrapporten. M-57s EGEN leseflate (kontraktens
    `rapportflate="ats"`): samme dekrypterings- og 404-doktrine som
    WCAG-rapporten (`rapport_detalj`), men med SIN diskriminator — de to
    flatene kan aldri servere hverandres former (200-og-feiler-under-
    rendring-klassen)."""
    av = _m57_avslatt(tjeneste, request)
    if av is not None:
        return av

    def _fn(conn, auth, rid):
        import oppdragskontrakt
        oid = request.path_params["id"]
        # Starlettes `:int` er ubegrenset Python-int; forbi bigint dør
        # bindingen i basen som en driftsfeil. En id ingen rad kan ha ER
        # «ikke funnet» (Codex P2) — samme svar, før tilkoblingsbruk.
        if not 0 <= oid <= 9223372036854775807:
            return _feilsvar("ikke_funnet", rid)
        par = [(navn, t.rapport_artefakttype)
               for navn, t in oppdragskontrakt.OPPDRAGSTYPER.items()
               if t.rapport_artefakttype is not None
               and t.rapportflate == RAPPORTFLATE_ATS]
        rad = conn.execute(
            "SELECT a.artefakt_id, a.ciphertext, a.nonce, a.dek_ref,"
            " a.promotert_ts, a.artefakttype, a.skjemaversjon"
            "  FROM artefakt a JOIN oppdrag o"
            "    ON o.tenant = a.tenant AND o.id = a.oppdrag_id"
            " WHERE a.tenant=%s AND a.oppdrag_id=%s"
            "   AND a.tilstand='promotert'"
            # MAKULERT ER IKKE LESBART (Cursor P2 på #252). Makuleringen
            # (#222) nuller payloaden UTEN tilstandsskifte — raden består
            # som evidensen om at rapporten fantes, og `tilstand` sier
            # fortsatt `promotert`. Uten dette leddet finner spørringen
            # den, `dekrypter` får `ct = None`, og kunden får
            # `intern_feil` (500) der #220 lovet et identisk 404.
            # Ankeret redder ikke: døren kan kalles uten reap, og da
            # lever prosessen fortsatt innenfor fristen.
            # `ciphertext IS NOT NULL` står ved siden av merket med
            # vilje: merket er årsaken vi kjenner, tom payload er
            # tilstanden leseveien faktisk ikke tåler.
            "   AND a.makulert_ts IS NULL AND a.ciphertext IS NOT NULL"
            "   AND (o.oppdragstype, a.artefakttype) IN"
            "       (SELECT * FROM unnest(%s::text[], %s::text[]))"
            # SLETTEGRENSEN GJELDER OGSÅ RAPPORTEN (Codex P1 ×2, felt
            # stengt): rapporten serveres bare med et LEVENDE
            # retensjonsanker. Kravet er EXISTS(ureapet prosess), ikke
            # NOT EXISTS(reapet): claimen føder ankeret (057-døren i
            # claim-transaksjonen), så en rapport UTEN prosess er et
            # oppdrag utenfor retensjonskontrakten og serveres ikke —
            # identisk 404, samme svar som før promotering. Etter
            # reaping faller den samme veien.
            # ... og FRISTEN håndheves her, ikke bare reaperens merke
            # (Codex P1): `slettet_ts` skrives asynkront i batcher — en
            # forsinket reaper skal aldri forlenge tilgangen til
            # kandidatdata forbi kundens frist. Samme grense som
            # reaperen: lukket_ts (avslutningen) eller opprettet
            # (forlatt-fallbacken) pluss kundens døgn.
            "   AND EXISTS (SELECT 1 FROM rekrutteringsprosess p"
            "        WHERE p.tenant = o.tenant AND p.oppdrag_id = o.id"
            "          AND p.slettet_ts IS NULL"
            # 069: bestilt tidligsletting stenger lesingen i samme
            # øyeblikk som bestillingen — ikke først ved reaperens batch.
            "          AND p.slett_bestilt_ts IS NULL"
            "          AND now() < coalesce(p.lukket_ts, p.opprettet)"
            "                      + p.slettefrist_dogn * interval '1 day')"
            " ORDER BY a.promotert_ts DESC LIMIT 1",
            (auth.tenant, oid, [p[0] for p in par],
             [p[1] for p in par])).fetchone()
        if rad is None:
            return _feilsvar("ikke_funnet", rid)
        (art_id, ct, nonce, dek_ref, ts, artefakttype,
         skjemaversjon) = rad
        from db import kryptering
        try:
            dek = kryptering.hent_dek(conn, auth.tenant, dek_ref)
            rapport = kryptering.dekrypter(dek, ct, nonce, auth.tenant,
                                           dek_ref)
        except Exception:
            tjeneste.logg.hendelse("intern_feil", rid, auth.tenant,
                                   art="drift", artefakt=str(art_id))
            return _feilsvar("intern_feil", rid)
        if not _anker_lever(conn, auth.tenant, oid):
            return _feilsvar("ikke_funnet", rid)
        # … OG ARTEFAKTET SELV MÅ FORTSATT VÆRE UMAKULERT (Codex P2 på
        # #252). Ankersjekken over måler PROSESSENS frist; makuleringen
        # er et eget faktum på artefaktraden, og døren kan committe uten
        # at prosessen merkes reapet. Da består ankeret, og bare dette
        # leddet ser at payloaden vi nettopp dekrypterte er slettet.
        # Samme svar som resten av 404-doktrinen — en makulert rapport
        # er identisk «finnes ikke», aldri et halvt svar.
        if not _artefakt_lever(conn, auth.tenant, art_id):
            return _feilsvar("ikke_funnet", rid)
        # LESNINGEN SERVERER IKKE DET FLATEN KASTER (Codex P2 — samme
        # doktrine som `_kandidater`s nøkkelsubtraksjon): `kildetekst` er
        # hele den blindede søknadsteksten per kandidat, og ingen
        # konsument av denne ruten leser den — funnene bærer sine egne
        # sitater. Den desidert tyngste delen av payloaden strippes før
        # svaret; artefaktet selv er urørt.
        #
        # `intervjusporsmal` strippes av samme grunn PLUSS eiers
        # produktbeslutning (27/8): spørsmål hører til INNKALLINGEN av de
        # 5–10 beste, ikke til rangeringen av alle søkere — rekrutterer
        # velger kandidater først, intervjuer skjer manuelt etterpå.
        # Lageret (kandidat_intervjusporsmal) og artefaktet består;
        # shortlist-arcen henter derfra når den kommer.
        for _k in (rapport.get("kandidater") or {}).values():
            if isinstance(_k, dict):
                _k.pop("kildetekst", None)
                _k.pop("intervjusporsmal", None)
        # LESEFLATE-FLYTTINGEN (BESLUTNING-168, #183-nabolaget): et
        # beslutningsspor (v2) bærer ingen `kandidater` — funnene leses
        # fra 057-lageret, som ankerpredikatet over alt har målt levende.
        # Svaret beholder formen flaten kjenner ({kid: {funn}}), så
        # leseren er den samme for begge generasjoner. Etter fristen
        # svarer ruten 404 som før — at det VARIGE sporet (rangeringen
        # uten funn) skal få en egen lesevei etter reaping er et
        # arkitektvalg som ikke tas her.
        if "kandidater" not in rapport:
            rapport["kandidater"] = _funn_fra_lageret(
                conn, auth.tenant, oid, rapport)
        # … OG PROFILENS VEKTER (samme flytting): beslutningssporet
        # bærer referansen (profil_id, versjon), aldri kravlisten —
        # uten den falt flaten til husets standardvekter med et varsel
        # om at «evalueringen lagret ikke sine egne» (eiers skjermbilde
        # 30/8). Kravene ER registrerte data i profillageret; leseveien
        # supplerer dem på v1-formen flaten kjenner. Slår oppslaget
        # feil (ukjent/uleselig referanse), står reserven som før —
        # ærlig, aldri en gjettet vekting.
        prof = rapport.get("profil")
        if isinstance(prof, dict) and "krav" not in prof:
            pver = prof.get("versjon")
            try:
                import uuid as uuidmod
                puid = uuidmod.UUID(str(prof.get("profil_id")))
            except (ValueError, TypeError):
                puid = None
            # Streng heltallsdom (CodeRabbit): int() ville tvunget både
            # True og 3.7 til lovlige versjoner — referansen skal VÆRE
            # et heltall, ellers står reserven.
            if isinstance(pver, bool) or not isinstance(pver, int):
                puid = None
            if puid is not None:
                krav = [{"kravnavn": kn, "vekt": v} for kn, v in
                        conn.execute(
                            "SELECT kravnavn, vekt FROM"
                            " stillingsprofil_krav WHERE tenant=%s"
                            " AND profil_id=%s AND versjon=%s"
                            " ORDER BY rekkefolge",
                            (auth.tenant, puid, pver)).fetchall()]
                if krav:
                    prof["krav"] = krav
        return kanonisk_json({
            "oppdrag_id": oid,
            "artefakt_id": str(art_id),
            "artefakttype": artefakttype,
            "skjemaversjon": skjemaversjon,
            "promotert_ts": ts.isoformat() if ts else None,
            "rapport": rapport,
            "request_id": rid,
        }, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


def rekrutteringsevalueringer(tjeneste, request: Request) -> Response:
    """GET /v1/rekruttering/evalueringer — tenantens evalueringsoppdrag,
    nyeste først: id, status, tidspunkt, og om rapporten er klar
    (promotert artefakt finnes). Ingen payload-dekryptering på
    listeveien — innholdet hører til detaljruten."""
    av = _m57_avslatt(tjeneste, request)
    if av is not None:
        return av

    def _fn(conn, auth, rid):
        import oppdragskontrakt
        # KEYSET-CURSOR, HUSFORMEN (#221): signert med server-pepper og
        # bundet med v2 (PR-008), som søsknene `beslutninger`,
        # `unntak_historikk` og `domeneovertakelse_saker`. En cursor er
        # ellers bare et par tall. v1 binder BARE tenant, og `les()`
        # godtar også en v2-kropp (den bærer `t`/`ts`/`id`) — en gyldig
        # cursor fra et annet endepunkt hos samme tenant ville derfor
        # være 200 her og forskyve keysetet til en fremmed (ts, id)-
        # posisjon (Cursor P2). v2 binder endepunkt, retning og filtre i
        # tillegg, og det er nettopp den forvekslingen den finnes for.
        # Keyset og ikke OFFSET: en liste som får nye evalueringer mens
        # noen blar, hopper over eller gjentar rader med OFFSET.
        etter = None
        raa_cursor = request.query_params.get("cursor")
        if raa_cursor:
            try:
                etter = cursormodul.les_v2(
                    raa_cursor, tjeneste.cursorpepper, tenant=auth.tenant,
                    endepunkt="rekruttering_evalueringer", retning="desc",
                    filtre={})
            except cursormodul.CursorUgyldig:
                tjeneste.logg.hendelse("cursor_ugyldig", rid, auth.tenant)
                return _feilsvar("cursor_ugyldig", rid)
        # SAMME KILDE SOM DETALJRUTEN (Cursor P2). Listen hardkodet paret
        # (`'rekruttering.evaluering'`, `'rekruttering.evaluering.rapport'`)
        # mens `rekrutteringsrapport_detalj` utleder det fra kontrakten.
        # To kilder for ETT spørsmål er en stille divergens: endrer en
        # kontrakt sin `rapport_artefakttype`, sier listen `rapport_klar:
        # false` mens detaljruten fortsatt svarer 200 (eller omvendt) — og
        # flaten skjuler «Vis»-knappen for en rapport som finnes. Samme
        # `par`-filter begge steder gjør divergensen umulig, og en ny
        # ats-flatet kontrakt blir listbar ved å DEKLARERE seg.
        par = [(navn, t.rapport_artefakttype)
               for navn, t in oppdragskontrakt.OPPDRAGSTYPER.items()
               if t.rapport_artefakttype is not None
               and t.rapportflate == RAPPORTFLATE_ATS]
        typer, arter = [p[0] for p in par], [p[1] for p in par]
        # ÉN KILDE TIL «KLAR» (eiers valg A på #258-A, 30/8): uttrykket
        # under brukes BÅDE som listens rapport_klar-kolonne og i
        # ferskeste-oppslaget — de to kan per konstruksjon ikke være
        # uenige om hva en klar rapport er.
        klar_sql = (
            "EXISTS (SELECT 1 FROM artefakt a"
            "          WHERE a.tenant = o.tenant AND a.oppdrag_id = o.id"
            "            AND a.tilstand='promotert'"
            # … og en MAKULERT rapport er ikke klar (samme ledd som
            # detaljruten — Cursor P2 på #252). Uten det ville listen
            # sagt `rapport_klar: true` om noe detaljruten svarer 404
            # på: divergensen #220 stengte, gjenåpnet av makuleringen.
            "            AND a.makulert_ts IS NULL"
            "            AND a.ciphertext IS NOT NULL"
            "            AND (o.oppdragstype, a.artefakttype) IN"
            "                (SELECT * FROM unnest(%s::text[], %s::text[])))"
            # … og listen reklamerer bare med et LEVENDE anker (samme
            # EXISTS-form som detaljruten — Codex P1 ×2).
            " AND EXISTS (SELECT 1 FROM rekrutteringsprosess p"
            "      WHERE p.tenant = o.tenant AND p.oppdrag_id = o.id"
            "        AND p.slettet_ts IS NULL"
            "        AND p.slett_bestilt_ts IS NULL"  # 069: som detaljruten
            "        AND now() < coalesce(p.lukket_ts, p.opprettet)"
            "                    + p.slettefrist_dogn * interval '1 day')")
        rader = conn.execute(
            "SELECT o.id, o.status, o.opprettet, " + klar_sql + ","
            # … og reapingen NAVNGIS (Codex P2): et `utfort` oppdrag med
            # `rapport_klar: false` fordi fristen har makulert det er
            # ikke «under arbeid» — uten dette feltet ville flaten vist
            # det slik i det uendelige.
            # `slettet` er sant fra FRISTEN, ikke først fra reaperens
            # batch — merket og fristen er samme grense sett fra kunden.
            " EXISTS (SELECT 1 FROM rekrutteringsprosess p"
            "      WHERE p.tenant = o.tenant AND p.oppdrag_id = o.id"
            "        AND (p.slettet_ts IS NOT NULL"
            # 069: bestillingen ER slettingen sett fra kunden — flaten
            # skal vise «slettet» i svaret på selve slett-kallet, ikke
            # først når reaperens batch har løpt.
            "             OR p.slett_bestilt_ts IS NOT NULL"
            "             OR now() >= coalesce(p.lukket_ts, p.opprettet)"
            "                 + p.slettefrist_dogn * interval '1 day'))"
            " AS slettet,"
            # HAR EVALUERINGEN NOE Å SLETTE? (eiers funn 30/8: Slett på en
            # feilet evaluering UTEN retensjonsanker ga 404, og flaten
            # kunne ikke vite bedre.) En feilet evaluering som aldri rakk
            # å føde prosessen har ingen kandidatdata — knappen skal da
            # ikke stå der. Fakta bor i basen, flaten dikter ikke.
            " EXISTS (SELECT 1 FROM rekrutteringsprosess p"
            "      WHERE p.tenant = o.tenant AND p.oppdrag_id = o.id)"
            " AS har_anker"
            "  FROM oppdrag o"
            " WHERE o.tenant=%s AND o.oppdragstype = ANY(%s::text[])"
            # 071: en rad eieren har slettet er UTE av listen — det er
            # hele betydningen av merket. Basen beholder historikken.
            "   AND o.liste_skjult_ts IS NULL"
            # Keyset-leddet: fortsettelsen er «eldre enn siste viste rad»,
            # målt på (opprettet, id) — samme par cursoren bærer, og samme
            # par sorteringen går på, så vinduene verken overlapper eller
            # hopper. Sorteringen gikk før på `o.id` alene; paret er samme
            # rekkefølge (id-er er monotone i praksis), gjort eksplisitt
            # så nøkkelen og sorteringen ikke kan divergere.
            + (" AND (o.opprettet, o.id) < (%s, %s)" if etter else "")
            # HENTER ÉN OVER VINDUET (Codex P2). `LIMIT 100` + `flere =
            # len(rader) == 100` PÅSTÅR eldre rader ved nøyaktig 100 uten
            # å ha sett én — flaten sier da «det finnes eldre» om en
            # komplett historikk. Den 101. raden er beviset; den sendes
            # aldri ut, den avgjør bare `flere` og cursoren.
            + " ORDER BY o.opprettet DESC, o.id DESC LIMIT 101",
            (typer, arter, auth.tenant, typer)
            + ((etter[0], etter[1]) if etter else ())).fetchall()
        # Cursoren peker på den SISTE VISTE raden — aldri på bevisraden:
        # neste side begynner nøyaktig der denne sluttet.
        neste = None
        if len(rader) > 100 and rader[99][2] is not None:
            neste = cursormodul.lag_v2(
                tjeneste.cursorpepper, tenant=auth.tenant,
                endepunkt="rekruttering_evalueringer", retning="desc",
                filtre={}, ts=rader[99][2], rad_id=rader[99][0])
        # FERSKESTE KLARE PEKES AV SERVEREN (eiers valg A på #258-A):
        # klienten sammenlignet (opprettet, id) selv, og JS-Date mister
        # mikrosekundene serverens nøkkel har — tredje formforsøk på en
        # nøkkel klienten ikke eier ble stoppet på K2. Nå bærer svaret
        # pekeren, målt av databasen på NØYAKTIG sorteringsnøkkelen —
        # og over HELE historikken, ikke bare vinduet: den ferskeste
        # klare kan lovlig ligge bak en side av uklare rader.
        ferskeste = conn.execute(
            "SELECT o.id FROM oppdrag o"
            " WHERE o.tenant=%s AND o.oppdragstype = ANY(%s::text[])"
            "   AND o.liste_skjult_ts IS NULL"
            "   AND " + klar_sql +
            " ORDER BY o.opprettet DESC, o.id DESC LIMIT 1",
            (auth.tenant, typer, typer, arter)).fetchone()
        return kanonisk_json({
            "ferskeste_klar_oppdrag": ferskeste[0] if ferskeste else None,
            "evalueringer": [
                {"oppdrag_id": r[0], "status": r[1],
                 "opprettet": r[2].isoformat() if r[2] else None,
                 "rapport_klar": r[3],
                 "slettet": r[4],
                 "har_anker": r[5]} for r in rader[:100]],
            # #221: fortsettelsen er et felt, ikke bare en påstand.
            "neste_cursor": neste,
            # Aldri stille avkorting: finnes rad 101, MELDER flaten det i
            # stedet for å presentere de nyeste 100 som alt.
            # Cursor (#220 P2-3, eierdom); selve pagineringen bor i #221.
            "flere": len(rader) > 100,
            "request_id": rid,
        }, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


def beslutning_detalj(tjeneste, request: Request) -> Response:
    def _fn(conn, auth, rid):
        try:
            bid = int(request.path_params["id"])
        except (KeyError, ValueError):
            return _feilsvar("request_feilformet", rid)

        rad = conn.execute(
            "SELECT id, ts, handling, beslutning, begrunnelse, kilde,"
            " policy_id, policy_content_hash"
            "  FROM revisjonslogg WHERE tenant=%s AND id=%s",
            (auth.tenant, bid)).fetchone()
        if rad is None:
            # RLS gjør annen tenants rad til null rader — ukjent og
            # kryss-tenant er dermed IDENTISKE her, uten egen kodevei.
            return _feilsvar("ikke_funnet", rid)
        (_, ts, handling, beslutning, begrunnelse, kilde,
         policyref, policy_hash) = rad

        resultat: dict = {}
        evidens, sen, konflikt = "IKKE_RELEVANT", False, False
        sak_finnes = False

        if beslutning == "STOPP":
            resultat = {"art": "policy_stoppet"}
        elif beslutning == "UNNTAK":
            u = conn.execute(
                "SELECT id, kategori, status FROM unntak"
                " WHERE tenant=%s AND loggpost_id=%s AND sakstype='normal'",
                (auth.tenant, bid)).fetchall()
            if len(u) != 1:
                # En UNNTAK-beslutning uten (eller med flere) saksrader
                # bryter skrivekontrakten fra `kjerne._avslutt`. Det er en
                # servertilstand, aldri noe klienten skal tolke.
                tjeneste.logg.hendelse("intern_feil", rid, auth.tenant,
                                       art="drift", loggpost=bid,
                                       unntaksrader=len(u))
                return _feilsvar("intern_feil", rid)
            resultat = {"art": "til_unntak", "unntak_id": u[0][0],
                        "kategori": u[0][1], "status": u[0][2]}
        else:  # TILLAT
            o = conn.execute(
                "SELECT id, status, kvittering IS NOT NULL, unntak_id,"
                " repair_operation_id, opprinnelse FROM oppdrag"
                " WHERE tenant=%s AND beslutning_loggpost_id=%s",
                (auth.tenant, bid)).fetchone()
            if o is None:
                if kilde == "arbeidskapabilitet":
                    # Beslutningsraden SELV beviser at utførelse var
                    # relevant: en fase-2-TILLAT bestiller alltid et
                    # oppdrag. Uten entydig koblet oppdrag (LEGACY_UKJENT,
                    # eller krasj før opprettelsen) konstruerer vi ALDRI et
                    # resultat (vilkår V4).
                    resultat = {"art": "utforelsesdata_ikke_tilgjengelig"}
                else:
                    resultat = {"art": "sideeffektfri_tillatt"}
            else:
                oid, ostatus, har_kvittering, ounntak, rep_id, oppr = o
                # 038 §5 (port 28): `unntak_id` er null for
                # beslutningsoppdrag — klienter må tåle det, og opphavet
                # sier hvilken vei oppdraget ble født.
                resultat = {"art": _ART_FOR_STATUS[ostatus],
                            "oppdrag_id": oid, "unntak_id": ounntak,
                            "opprinnelse": oppr}
                if ostatus == "kansellert":
                    sup = conn.execute(
                        "SELECT status='superseded'"
                        "  FROM reparasjonsoperasjoner"
                        " WHERE tenant=%s AND repair_operation_id=%s",
                        (auth.tenant, rep_id)).fetchone()
                    resultat["superseded"] = bool(sup and sup[0])
                    # 043 (port 12): årsaken er NULLABLE — klienter som
                    # antar at `kansellert` er uten årsak må tåle verdien
                    # (samme kontraktstil som 038 port 28). `feil_aarsak`
                    # er uendret.
                    ka = conn.execute(
                        "SELECT kansellert_aarsak FROM oppdrag"
                        " WHERE tenant=%s AND id=%s",
                        (auth.tenant, oid)).fetchone()
                    resultat["kansellert_aarsak"] = ka[0] if ka else None
                if ostatus in ("opprettet", "plukket"):
                    evidens = "MANGLER"
                elif ostatus == "utfort":
                    evidens = "GYLDIG"
                elif ostatus == "feilet":
                    # GYLDIG = signert feilresultat; MANGLER = timeout/
                    # systemfeil uten kvittering. `feil_aarsak` skiller
                    # (v3 pkt. 3).
                    evidens = "GYLDIG" if har_kvittering else "MANGLER"
                    resultat["feil_aarsak"] = \
                        "signert" if har_kvittering else "timeout"
                else:  # kansellert
                    evidens = "GYLDIG" if har_kvittering else "IKKE_RELEVANT"

                # Flaggene AVLEDES av append-only-evidensen (v5 pkt. 2):
                # historikkradene kvitteringsporten skrev. Et identisk
                # replay skrev ALDRI en rad, så det setter heller aldri et
                # flagg. `motstridende_kvittering`-raden fra
                # `_ingest_kvittering`s hasj-sammenligning bærer ikke alltid
                # oppdrag_id i detaljene; en rad uten oppdrag_id på sakens
                # historikk regnes derfor med — heller ett konfliktflagg for
                # mye enn en konflikt som forsvinner.
                #
                # SAKEN SLÅS OPP VIA OPPDRAGET, IKKE OMVENDT (Codex P2).
                # `ounntak` er `oppdrag.unntak_id`, altså «født som
                # reparasjon av» — og den er NULL for hele
                # beslutningsopphavet. Et `h.unntak_id = NULL`-filter
                # matcher ingenting, så begge flaggene sto FALSE for et
                # bestilt oppdrag selv når evidensen lå der: 038 §5 lar
                # sen-/konfliktveiene opprette en EGEN sak, og den peker
                # tilbake med `unntak.oppdrag_id`. Begge retningene tas
                # med, så M-37-formen er uendret og beslutningsformen
                # endelig finner sin egen evidens.
                sakene = ("SELECT u.id FROM unntak u WHERE u.tenant=%s"
                          " AND (u.id=%s OR u.oppdrag_id=%s)")
                flagg = conn.execute(
                    "SELECT"
                    " EXISTS(SELECT 1 FROM unntak_historikk h"
                    f"         WHERE h.tenant=%s AND h.unntak_id IN ({sakene})"
                    "           AND h.hendelse='sen_kvittering'"
                    "           AND (h.detalj->>'oppdrag_id')::bigint=%s),"
                    " EXISTS(SELECT 1 FROM unntak_historikk h"
                    f"         WHERE h.tenant=%s AND h.unntak_id IN ({sakene})"
                    "           AND h.hendelse='motstridende_kvittering'"
                    "           AND (h.detalj->>'oppdrag_id' IS NULL"
                    "                OR (h.detalj->>'oppdrag_id')::bigint=%s))",
                    (auth.tenant, auth.tenant, ounntak, oid, oid,
                     auth.tenant, auth.tenant, ounntak, oid, oid)).fetchone()
                sen, konflikt = bool(flagg[0]), bool(flagg[1])

        # Sikkerhetsaksen (v2 pkt. 3): en sak beslutningen selv fødte
        # (sakstype sikkerhet/drift via loggpost-FK-en), eller registrert
        # konfliktevidens på det FK-bundne oppdraget. Konflikt UTEN
        # sikkerhetsregistrering kan dermed ikke forekomme — invarianten
        # fra v4 pkt. 2 holder per konstruksjon og måles i testene.
        egen_sak = conn.execute(
            "SELECT EXISTS(SELECT 1 FROM unntak WHERE tenant=%s"
            " AND loggpost_id=%s AND sakstype IN ('sikkerhet','drift'))",
            (auth.tenant, bid)).fetchone()[0]
        sak_finnes = bool(egen_sak) or konflikt

        art = resultat["art"]
        if not _kombinasjon_lovlig(art, evidens, sen, konflikt):
            tjeneste.logg.hendelse("intern_feil", rid, auth.tenant,
                                   art="drift", loggpost=bid,
                                   kombinasjon=f"{art}/{evidens}/{sen}/{konflikt}")
            return _feilsvar("intern_feil", rid)

        ref = les_policyref(policyref)
        kropp = {
            "id": bid,
            "handling": handling,
            "begrunnelse": _koder(begrunnelse),
            "policy_versjon": ref[1] if ref else None,
            "policy_hash": policy_hash,
            "beslutning_ts": ts.isoformat(),
            "resultat": resultat,
            "evidensstatus": evidens,
            "sen_evidens": sen,
            "konflikt_evidens": konflikt,
            "request_id": rid,
        }
        if "security:read" in auth.scopes:
            kropp["sikkerhet"] = {"sak_finnes": sak_finnes}
        # Uten scopet er feltet FRAVÆRENDE — fravær betyr «ikke synlig for
        # deg», aldri false (v1 pkt. 5).
        return kanonisk_json(kropp, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)


# ---------------------------------------------------------------------------
# GET /v1/unntak/{id} — scope exceptions:read (+security:read utenfor normal)
# ---------------------------------------------------------------------------

def _hent_unntak(conn, auth, uid: int):
    """Saksraden + scope-regelen for sakstype.

    Sikkerhets- og driftssaker svarer `ikke_funnet` — ikke 403 — for et
    token uten `security:read`: en 403 på en konkret ID bekrefter at det
    FINNES en sikkerhetssak der, og det er nøyaktig informasjonen scopet
    skal verne. Identisk 404-prinsippet gjelder altså også her.
    """
    rad = conn.execute(
        "SELECT u.id, u.ts, u.handling, u.kategori, u.sakstype, u.status,"
        " u.prioritet, r.begrunnelse, u.intensjon_pakrevd, u.saksversjon,"
        " r.policy_id, u.arsak"
        "  FROM unntak u JOIN revisjonslogg r"
        "    ON r.tenant = u.tenant AND r.id = u.loggpost_id"
        " WHERE u.tenant=%s AND u.id=%s", (auth.tenant, uid)).fetchone()
    if rad is None:
        return None
    if rad[4] != "normal" and "security:read" not in auth.scopes:
        return None
    return rad


#: Statusene der et menneske kan handle på saken (PR-012).
_HANDTERBARE_STATUS = frozenset({"manuell", "venter_godkjenning",
                                 "venter_andre_godkjenner", "godkjenning_klar"})


def _tillatte_handlinger(conn, tenant, handling, status, begrunnelse,
                         intensjon_pakrevd, policy_id_label):
    """(tillatte_handlinger[], aarsak_godkjenn_utilgjengelig|None).

    avvis/eskaler er alltid mulig på en håndterbar sak. godkjenn KUN når saken
    har en komplett handlingsintensjon OG en godkjennbar blokkerende grunnkode
    (mengden blokkerende grunner er motorens — her brukes den lagrede
    begrunnelseskjedens siste, blokkerende kode; motoren er den endelige
    autoriteten ved selve godkjenningen)."""
    from . import policyregister
    if status not in _HANDTERBARE_STATUS:
        return [], None
    handlinger = ["avvis", "eskaler"]
    if not intensjon_pakrevd:
        return handlinger, "ingen_intensjon"
    # Den blokkerende grunnkoden er den SISTE i begrunnelseskjeden.
    bundet = None
    if isinstance(begrunnelse, list) and begrunnelse \
            and isinstance(begrunnelse[-1], dict):
        bundet = begrunnelse[-1].get("kode")
    if not isinstance(bundet, str):
        return handlinger, "blokkerende_grunner_uavklart"
    from policy_validator.engine import les_policyref
    from policy_validator.schema import IKKE_MENNESKELIG_GODKJENNBARE
    ref = les_policyref(policy_id_label)
    try:
        policy, _ = policyregister.hent_aktiv(conn, tenant, ref[0]) if ref \
            else (None, None)
    except Exception:
        policy = None
    mo = (policy or {}).get("menneskelig_overstyring") or {}
    godkjennbar = bundet not in IKKE_MENNESKELIG_GODKJENNBARE and any(
        isinstance(e, dict) and e.get("grunnkode") == bundet
        and e.get("handling") == handling for e in mo.get("godkjennbare") or [])
    if godkjennbar:
        return ["godkjenn", *handlinger], None
    return handlinger, "ikke_godkjennbar_grunn"


def _har_utestaaende(conn, tenant: str, uid: int) -> bool:
    """Gate 14a (presentasjon): har saken et LEVENDE oppdrag/kapabilitet?
    Leser gjennom NØYAKTIG samme autoritative DB-funksjon som POST-vakten
    (`sak_utestaaende`, SECURITY DEFINER), så lese-API-et aldri inviterer til
    en `avvis` serverkontrakten allerede vet er utilgjengelig."""
    rad = conn.execute("SELECT 1 FROM sak_utestaaende(%s,%s) LIMIT 1",
                       (tenant, uid)).fetchone()
    return rad is not None


def unntak_detalj(tjeneste, request: Request) -> Response:
    def _fn(conn, auth, rid):
        try:
            uid = int(request.path_params["id"])
        except (KeyError, ValueError):
            return _feilsvar("request_feilformet", rid)
        rad = _hent_unntak(conn, auth, uid)
        if rad is None:
            return _feilsvar("ikke_funnet", rid)
        # Historikken er et EGET endepunkt (v2 pkt. 7) — aldri inline her.
        # Payload/attestasjoner/nøkler hentes ikke engang fra databasen.
        handlinger, aarsak = _tillatte_handlinger(
            conn, auth.tenant, rad[2], rad[5], rad[7], rad[8], rad[10])
        # Gate 14a: er et oppdrag/kapabilitet utestående, er `avvis` utilgjengelig
        # (POST-vakten svarer 409 `utestaaende_oppdrag`) — skjul den her med den
        # lukkede årsaken, så UI-et forklarer det FØR brukeren prøver.
        # 043 (Gate 14b): et levende OPPDRAG stenger ikke lenger avvis —
        # veien løser opp (kansellering med fencing), og flaten skal
        # varsle det FØR klikket (alertdialogen). En levende
        # ARBEIDSKAPABILITET beholder 14a-svaret — med eller uten oppdrag
        # ved siden av, for det er den POST-vakten står på.
        avvis_aarsak = None
        avvis_kansellerer = None
        if "avvis" in handlinger:
            rader = conn.execute(
                "SELECT kilde, ref, status FROM sak_utestaaende(%s,%s)",
                (auth.tenant, uid)).fetchall()
            lev_opp = [int(ref) for kilde, ref, st in rader
                       if kilde == "oppdrag"
                       and st in ("opprettet", "plukket")]
            lev_kap = [ref for kilde, ref, st in rader
                       if kilde == "kapabilitet"]
            # Rekkefølgen er BAKVENDT av den naive (Codex P2, runde 2):
            # POST-vakten blokkerer på `levende_kap` ALENE — også når det
            # finnes kansellerbare oppdrag ved siden av. Prøvde lesingen
            # oppdragene først, tilbød flaten en `avvis` med
            # `avvis_kansellerer`-varsel som ALLTID endte i 409 og
            # kansellerte ingenting: et løfte serverkontrakten ikke holder.
            # Kapabiliteten avgjør derfor her også — nøyaktig som i vakten.
            info = []
            if lev_opp:
                info = conn.execute(
                    "SELECT id, status, COALESCE(modul_id, eiermodul),"
                    " oppdragstype FROM oppdrag"
                    " WHERE tenant=%s AND id = ANY(%s) ORDER BY id",
                    (auth.tenant, lev_opp)).fetchall()
            # Samme prioritering én gang til (Codex P2, runde 6): et levende
            # VERIFIKASJONSoppdrag har ingen oppløsningsvei — POST-vakten
            # blokkerer på det som på en levende arbeidskapabilitet. Tilbød
            # flaten en `avvis` med kanselleringsvarsel her, ville den
            # alltid endt i 409 og kansellert ingenting.
            uloselige = [r for r in info if r[3] == "verifikasjon"]
            if lev_kap or uloselige:
                handlinger = [h for h in handlinger if h != "avvis"]
                avvis_aarsak = "utestaaende_oppdrag"
            elif lev_opp:
                avvis_kansellerer = [
                    {"oppdrag_id": int(r[0]), "status": r[1],
                     "modul_id": r[2]} for r in info]
        kropp = {"id": rad[0], "ts": rad[1].isoformat(), "handling": rad[2],
                 "kategori": rad[3], "sakstype": rad[4], "status": rad[5],
                 "prioritet": rad[6], "begrunnelse": _koder(rad[7]),
                 "saksversjon": rad[9], "arsak": rad[11],
                 "tillatte_handlinger": handlinger,
                 "request_id": rid}
        if aarsak is not None:
            kropp["godkjenn_utilgjengelig"] = aarsak
        if avvis_aarsak is not None:
            kropp["avvis_utilgjengelig"] = avvis_aarsak
        if avvis_kansellerer is not None:
            kropp["avvis_kansellerer"] = avvis_kansellerer
        return kanonisk_json(kropp, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "exceptions:read", _fn)


# ---------------------------------------------------------------------------
# GET /v1/unntak/{id}/historikk — kronologisk keyset (ts ASC, id ASC)
# ---------------------------------------------------------------------------

def unntak_historikk(tjeneste, request: Request) -> Response:
    def _fn(conn, auth, rid):
        try:
            uid = int(request.path_params["id"])
        except (KeyError, ValueError):
            return _feilsvar("request_feilformet", rid)
        grense = _grense(request)
        if grense is None:
            return _feilsvar("request_feilformet", rid)
        # Samme eksistens- og scoperegel som detaljen: historikken til en
        # sak man ikke kan se, finnes ikke.
        if _hent_unntak(conn, auth, uid) is None:
            return _feilsvar("ikke_funnet", rid)

        filtre = {"unntak_id": uid}
        etter = None
        raa = request.query_params.get("cursor")
        if raa:
            try:
                etter = cursormodul.les_v2(
                    raa, tjeneste.cursorpepper, tenant=auth.tenant,
                    endepunkt="unntak_historikk", retning="asc",
                    filtre=filtre)
            except cursormodul.CursorUgyldig:
                tjeneste.logg.hendelse("cursor_ugyldig", rid, auth.tenant)
                return _feilsvar("cursor_ugyldig", rid)

        sql = ("SELECT id, hendelse, fra_status, til_status, ts"
               "  FROM unntak_historikk WHERE tenant=%s AND unntak_id=%s")
        args: list = [auth.tenant, uid]
        if etter is not None:
            # `>`-predikatet — kronologisk fortsettelse (v3 bindende test).
            sql += " AND (ts, id) > (%s, %s)"
            args += [etter[0], etter[1]]
        sql += " ORDER BY ts ASC, id ASC LIMIT %s"
        args.append(grense)
        rader = conn.execute(sql, tuple(args)).fetchall()

        ut = [{"id": r[0], "hendelse": r[1], "fra_status": r[2],
               "til_status": r[3], "ts": r[4].isoformat()} for r in rader]
        neste = None
        if len(rader) == grense:
            neste = cursormodul.lag_v2(
                tjeneste.cursorpepper, tenant=auth.tenant,
                endepunkt="unntak_historikk", retning="asc", filtre=filtre,
                ts=rader[-1][4], rad_id=rader[-1][0])
        return kanonisk_json({"rader": ut, "neste_cursor": neste,
                              "request_id": rid}, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "exceptions:read", _fn)


# ---------------------------------------------------------------------------
# GET /v1/policy/aktiv — scope policy:read. Lukket, redigert DTO (v2 pkt. 8,
# v4 pkt. 4, v5 pkt. 3, v6 pkt. 3).
# ---------------------------------------------------------------------------

#: ISO 4217, aktive koder (vilkår V5: validert mot register, ikke bare
#: ^[A-Z]{3}$ — «XXX» er tre store bokstaver, men ingen valuta man kan
#: sette en beløpsgrense i).
ISO4217 = frozenset("""
AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB
BRL BSD BTN BWP BYN BZD CAD CDF CHF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP
DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF
IDR ILS INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK
LBP LKR LRD LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN
NAD NGN NIO NOK NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF
SAR SBD SCR SDG SEK SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND
TOP TRY TTD TWD TZS UAH UGX USD UYU UZS VES VND VUV WST XAF XCD XOF XPF
YER ZAR ZMW ZWG
""".split())

_BELOP_MONSTER = re.compile(r"^\d{1,13}\.\d{2}$")
_KLOKKE_MONSTER = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_HASH_MONSTER = re.compile(r"^[0-9a-f]{64}$")

#: Kildeskjemaets ukedagskoder, i rekkefølge (0=mandag … 6=søndag).
_DAGER = ("man", "tir", "ons", "tor", "fre", "lor", "son")

#: AVVIK FRA v4-SPESIFIKASJONEN, flagget i PR-en: kildeskjemaet
#: (policy-schema-v0.2) har enheten `minutter` og INGEN `maaned`.
#: Spesifikasjonens enum [time|dag|uke|maaned] kan ikke representere
#: eksisterende, gyldige policyer — DTO-en bærer derfor kildens LUKKEDE
#: enum uendret i stedet for å oversette til en mengde med hull.
_VINDU_ENHETER = ("minutter", "timer", "dager", "uker")

_MODI = ("auto", "auto_med_vilkaar", "alltid_stopp")


def _tidsvindu_dto(raa: str, tidssone: str) -> dict:
    """`man-fre 08:00-16:00` -> TidsvinduDTO. Kaster ValueError ved avvik.

    Kilden er en STRENG (policy-skjemaet), DTO-en er strukturert (v4). Et
    vindu som krysser uken (`fre-man`) rulles rundt — dagene er en MENGDE i
    DTO-en, og rekkefølgen i kilden er bare notasjon.
    """
    dager_del, _, klokke_del = raa.partition(" ")
    fra_dag, _, til_dag = dager_del.partition("-")
    fra_kl, _, til_kl = klokke_del.partition("-")
    i, j = _DAGER.index(fra_dag), _DAGER.index(til_dag)
    ukedager = list(range(i, j + 1)) if i <= j else \
        list(range(i, 7)) + list(range(0, j + 1))
    if not _KLOKKE_MONSTER.fullmatch(fra_kl) \
            or not _KLOKKE_MONSTER.fullmatch(til_kl):
        raise ValueError(f"ugyldig klokkeslett i tidsvindu: {raa!r}")
    return {"ukedager": ukedager, "fra": fra_kl, "til": til_kl,
            "tidssone": tidssone}


def _grenser_dto(g: dict | None, tidssone: str) -> dict | None:
    """Grensene som faste felt. Tomt objekt og null betyr det samme og
    normaliseres til null (v4/v5) — serveren returnerer ALDRI `{}`."""
    if not g:
        return None
    ut: dict = {"belop_maks": None, "valuta": None, "tidsvindu": None,
                "frekvens": None}
    if g.get("belop_maks") is not None:
        # Kanonisk decimal-STRENG med nøyaktig to desimaler (v4) — aldri
        # float, aldri kildens råform.
        ut["belop_maks"] = str(
            Decimal(str(g["belop_maks"])).quantize(Decimal("0.01")))
    if g.get("valuta"):
        # AVVIK FRA v4, flagget i PR-en: kildeskjemaet tillater FLERE
        # valutaer per grense. En DTO med én ville valgt for kunden;
        # feltet er derfor en liste.
        ut["valuta"] = list(g["valuta"])
    if g.get("tidsvindu"):
        ut["tidsvindu"] = _tidsvindu_dto(g["tidsvindu"], tidssone)
    if g.get("frekvens"):
        f = g["frekvens"]
        # `grupperingsnokkel` er et feltnavn fra tenantens payload-domene
        # og står IKKE i DTO-en — allowlist, ikke passthrough.
        ut["frekvens"] = {"maks": f["maks"],
                          "vindu_enhet": f["periode_enhet"],
                          "vindu_antall": f["periode_antall"]}
    if all(v is None for v in ut.values()):
        return None
    return ut


def bygg_policy_dto(policy_id: str, versjon: str, innholds_hash: str,
                    innhold: dict) -> dict:
    """Policyregisterraden -> lukket DTO. KUN allowlistede felt.

    ALDRI: tokenhash, pepper, nøkler, krypteringsmetadata, interne
    DB-felt, rå YAML, `dataklasser`, `retention`, `frister`, `meta` — det
    som ikke bygges her, finnes ikke i responsen.
    """
    tidssone = innhold.get("tidssone", "UTC")
    roller = [{"id": r["id"],
               # Stabil oversettelsesKODE, aldri fritekst (v2 pkt. 8:
               # maskin-DTO — UI oversetter, API-et leverer ikke
               # presentasjonstekst). Kildens `beskrivelse` er fritekst og
               # slipper derfor aldri ut.
               "beskrivelse_kode": f"rolle.{r['id']}"}
              for r in innhold.get("roller", [])]
    handlinger = [{"navn": h["id"], "modus": h["modus"],
                   "grenser": _grenser_dto(h.get("grenser"), tidssone),
                   "vilkaar": [vk["navn"] for vk in h.get("vilkaar") or []]}
                  for h in innhold.get("handlinger", [])]
    verifikatorer = [{"offentlig_id": vid,
                      "betrodd_for": list(v.get("betrodd_for", [])),
                      "kan_fastsla_permanent":
                          bool(v.get("kan_fastsla_permanent", False))}
                     for vid, v in sorted(
                         (innhold.get("verifikatorer") or {}).items())]
    return {"skjemaversjon": 1, "policy_id": policy_id, "versjon": versjon,
            "innholds_hash": innholds_hash, "roller": roller,
            "handlinger": handlinger, "verifikatorer": verifikatorer}


def _unik(verdier, feil: list[str], hva: str) -> None:
    if len(set(verdier)) != len(list(verdier)):
        feil.append(f"{hva}: duplikater")


def valider_policy_dto(dto: dict) -> list[str]:
    """Servermodellens egen kontroll av DTO-en FØR den forlater huset.

    Grensene er v6 pkt. 3s eksakte konstanter. Alle nivåer er lukkede —
    et felt som ikke står i fasiten er en byggefeil, aldri passthrough.
    Tom liste == gyldig.
    """
    feil: list[str] = []

    def _n(navn, verdi, maks=128):
        if not isinstance(verdi, str) or not verdi or len(verdi) > maks:
            feil.append(f"{navn}: ugyldig eller for lang (maks {maks})")

    if set(dto) != {"skjemaversjon", "policy_id", "versjon", "innholds_hash",
                    "roller", "handlinger", "verifikatorer"}:
        return ["PolicyDTO: feil feltmengde"]
    if dto["skjemaversjon"] != 1:
        feil.append("skjemaversjon: må være 1")
    _n("policy_id", dto["policy_id"])
    _n("versjon", dto["versjon"], 64)
    if not isinstance(dto["innholds_hash"], str) \
            or not _HASH_MONSTER.fullmatch(dto["innholds_hash"]):
        feil.append("innholds_hash: må være 64 hex-tegn")

    if len(dto["roller"]) > 50:
        feil.append("roller: flere enn 50")
    _unik([r.get("id") for r in dto["roller"]], feil, "roller")
    for r in dto["roller"]:
        if set(r) != {"id", "beskrivelse_kode"}:
            feil.append("RolleDTO: feil feltmengde")
            continue
        _n("rolle.id", r["id"], 64)
        _n("rolle.beskrivelse_kode", r["beskrivelse_kode"])

    if len(dto["handlinger"]) > 200:
        feil.append("handlinger: flere enn 200")
    _unik([h.get("navn") for h in dto["handlinger"]], feil, "handlinger")
    for h in dto["handlinger"]:
        if set(h) != {"navn", "modus", "grenser", "vilkaar"}:
            feil.append("HandlingDTO: feil feltmengde")
            continue
        _n("handling.navn", h["navn"])
        if h["modus"] not in _MODI:
            feil.append(f"handling.modus: ukjent ({h['modus']!r})")
        if len(h["vilkaar"]) > 50:
            feil.append("handling.vilkaar: flere enn 50")
        _unik(h["vilkaar"], feil, "handling.vilkaar")
        for v in h["vilkaar"]:
            _n("vilkaar", v)
        feil.extend(_valider_grenser(h["grenser"]))

    if len(dto["verifikatorer"]) > 100:
        feil.append("verifikatorer: flere enn 100")
    _unik([v.get("offentlig_id") for v in dto["verifikatorer"]], feil,
          "verifikatorer")
    for v in dto["verifikatorer"]:
        if set(v) != {"offentlig_id", "betrodd_for", "kan_fastsla_permanent"}:
            feil.append("VerifikatorDTO: feil feltmengde")
            continue
        _n("verifikator.offentlig_id", v["offentlig_id"])
        if len(v["betrodd_for"]) > 50:
            feil.append("verifikator.betrodd_for: flere enn 50")
        _unik(v["betrodd_for"], feil, "verifikator.betrodd_for")
        for b in v["betrodd_for"]:
            _n("betrodd_for", b)
        if not isinstance(v["kan_fastsla_permanent"], bool):
            feil.append("verifikator.kan_fastsla_permanent: må være bool")
    return feil


def _valider_grenser(g: dict | None) -> list[str]:
    feil: list[str] = []
    if g is None:
        return feil
    if set(g) != {"belop_maks", "valuta", "tidsvindu", "frekvens"}:
        return ["GrenserDTO: feil feltmengde"]
    if all(v is None for v in g.values()):
        feil.append("GrenserDTO: tomt objekt skal være normalisert til null")
    if g["belop_maks"] is not None:
        if not isinstance(g["belop_maks"], str) \
                or not _BELOP_MONSTER.fullmatch(g["belop_maks"]):
            feil.append("belop_maks: må være kanonisk decimal-streng"
                        " med to desimaler")
        else:
            try:
                if Decimal(g["belop_maks"]) <= 0:
                    feil.append("belop_maks: må være positiv")
            except InvalidOperation:
                feil.append("belop_maks: uleselig")
        # v5: valuta er PÅKREVD når belop_maks finnes — en beløpsgrense
        # uten valuta er ikke en grense, den er et tall.
        if not g["valuta"]:
            feil.append("valuta: påkrevd når belop_maks er satt")
    if g["valuta"] is not None:
        if not isinstance(g["valuta"], list) or not g["valuta"] \
                or len(g["valuta"]) > 10:
            feil.append("valuta: liste med 1–10 koder")
        else:
            _unik(g["valuta"], feil, "valuta")
            for v in g["valuta"]:
                if v not in ISO4217:
                    feil.append(f"valuta: {v!r} er ikke en ISO 4217-kode")
    if g["tidsvindu"] is not None:
        t = g["tidsvindu"]
        if set(t) != {"ukedager", "fra", "til", "tidssone"}:
            feil.append("TidsvinduDTO: feil feltmengde")
        else:
            if not t["ukedager"] or len(t["ukedager"]) > 7 \
                    or len(set(t["ukedager"])) != len(t["ukedager"]) \
                    or not all(isinstance(d, int) and 0 <= d <= 6
                               for d in t["ukedager"]):
                feil.append("ukedager: 1–7 unike heltall 0–6")
            for navn in ("fra", "til"):
                if not isinstance(t[navn], str) \
                        or not _KLOKKE_MONSTER.fullmatch(t[navn]):
                    feil.append(f"tidsvindu.{navn}: ugyldig klokkeslett")
            try:
                ZoneInfo(t["tidssone"])
            except Exception:
                feil.append(f"tidssone: {t['tidssone']!r} finnes ikke i"
                            " IANA-databasen")
    if g["frekvens"] is not None:
        f = g["frekvens"]
        if set(f) != {"maks", "vindu_enhet", "vindu_antall"}:
            feil.append("FrekvensDTO: feil feltmengde")
        else:
            if not isinstance(f["maks"], int) \
                    or not 1 <= f["maks"] <= 100_000:
                feil.append("frekvens.maks: 1–100000")
            if not isinstance(f["vindu_antall"], int) \
                    or not 1 <= f["vindu_antall"] <= 10_000:
                feil.append("frekvens.vindu_antall: 1–10000")
            if f["vindu_enhet"] not in _VINDU_ENHETER:
                feil.append(f"frekvens.vindu_enhet: ukjent"
                            f" ({f['vindu_enhet']!r})")
    return feil


def policy_aktive(tjeneste, request: Request) -> Response:
    """ENUMERER de aktive policyene. Ingen DTO, ingen tolkning — bare hvilke
    som er aktive, i policy_id-rekkefølge.

    Dette er utveien fra tilstanden `/v1/policy/aktiv` med rette nekter å
    servere (Codex P2). Det endepunktet lover ÉN aktiv policy, og svarer
    `intern_feil` når tenanten har flere — fail-closed, fordi å velge en av dem
    ville vært å bestemme kundens gjeldende policy i et leseendepunkt. Men
    NØYAKTIG den tilstanden er feilen «angre en feilopprettet policy» finnes
    for: `tjenestebedrift1` og `tjenestebedrift2` ble begge aktivert ved feil.
    Uten en vei til å SE begge, var flatens slettehandling utilgjengelig i det
    ene tilfellet den er skrevet for, og eier satt igjen med håndskrevet SQL —
    altså der vi startet.

    Fail-closed står: her velges ingen gjeldende policy, og ingen policy
    serveres som håndhevet. Svaret er en LISTE, og at den kan ha lengde 2 er
    hele poenget. Derfor bygges heller ingen `PolicyDTO`: en korrupt rad skal
    kunne PEKES PÅ og slettes, ikke gjøre lista uleselig (`policy_korrupt` her
    ville gjenskapt blindveien ett hakk lenger inn).
    """
    def _fn(conn, auth, rid):
        rader = conn.execute(
            "SELECT policy_id, versjon, innholds_hash FROM policyer"
            " WHERE tenant=%s AND aktiv ORDER BY policy_id, versjon",
            (auth.tenant,)).fetchall()
        return kanonisk_json({
            "policyer": [{"policy_id": p, "versjon": v, "innholds_hash": h}
                         for p, v, h in rader],
            "request_id": rid,
        }, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "policy:read", _fn)


def policy_aktiv(tjeneste, request: Request) -> Response:
    def _fn(conn, auth, rid):
        rader = conn.execute(
            "SELECT policy_id, versjon, innholds_hash, innhold"
            "  FROM policyer WHERE tenant=%s AND aktiv", (auth.tenant,)
        ).fetchall()
        if not rader:
            return _feilsvar("ikke_funnet", rid)
        if len(rader) > 1:
            # Registeret tillater én aktiv per policy_id, altså flere per
            # tenant. Endepunktets kontrakt er ÉN. Å velge en av dem ville
            # vært å bestemme kundens gjeldende policy i et leseendepunkt —
            # fail-closed, og flagget som åpent spesifikasjonspunkt.
            tjeneste.logg.hendelse("intern_feil", rid, auth.tenant,
                                   art="drift", aktive=len(rader))
            return _feilsvar("intern_feil", rid)
        policy_id, versjon, innholds_hash, innhold = rader[0]
        # Å avgjøre at raden ikke KAN tolkes er en del av tolkningen, ikke et
        # forarbeid til den (Codex P2). `innhold` er JSONB, og registeret
        # skriver alltid et objekt (`registrer` validerer før den skriver), så
        # en verdi som kommer tilbake som noe annet enn et dict ER en korrupt
        # rad — nøyaktig dommen `hent_aktiv` feller på beslutningsveien
        # («policyinnholdet er ikke et objekt»).
        #
        # Reparsingen som sto her tok samme rad feil i begge retninger. En
        # JSONB-STRENG som ikke er JSON (`"not-json"`) kastet JSONDecodeError
        # UTENFOR try-en under, altså en generisk 500 i stedet for
        # `policy_korrupt` — og flatens reserve tar bare én rad når koden er
        # `policy_korrupt`, så nettopp den ENSLIGE korrupte policyen ble
        # uslettelig fra flaten igjen. En DOBBELTKODET streng gikk motsatt
        # vei: parset til et objekt og ble servert som en frisk policy her,
        # mens hver beslutning på den svarte `policy_korrupt`. Ett register,
        # to svar på om raden er gyldig, er en verre feil enn den vi kom for.
        if not isinstance(innhold, dict):
            tjeneste.logg.hendelse("policy_korrupt", rid, auth.tenant,
                                   art="drift",
                                   feiltype=type(innhold).__name__)
            return _feilsvar("policy_korrupt", rid)
        try:
            dto = bygg_policy_dto(policy_id, versjon, innholds_hash, innhold)
        except (KeyError, ValueError, TypeError, InvalidOperation) as e:
            tjeneste.logg.hendelse("policy_korrupt", rid, auth.tenant,
                                   art="drift", feiltype=type(e).__name__)
            return _feilsvar("policy_korrupt", rid)
        feil = valider_policy_dto(dto)
        if feil:
            # Sanitert: fullstendig feilliste til driftsloggen, aldri til
            # klienten (samme fail-closed-retning som revalideringen i
            # beslutningsveien).
            tjeneste.logg.hendelse("policy_korrupt", rid, auth.tenant,
                                   art="drift", feil=feil[:5])
            return _feilsvar("policy_korrupt", rid)
        dto["request_id"] = rid
        return kanonisk_json(dto, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "policy:read", _fn)


# ---------------------------------------------------------------------------
# GET /v1/utrulling — scope decisions:read
#
# Utrullingsplanen lå tidligere som en konstant i den STATISK SERVERTE
# klientbunten, altså lesbar for hvem som helst. Her er den bak økten, og
# `utrullingsmodul.svar_for` — ikke klienten — bestemmer hvilke rader som
# forlater prosessen (P1, Codex runde 3).
#
# `decisions:read` er valgt fordi ALLE kunderollene i `autorisasjon.py` har
# det: flaten er kundens egen. En senere plattformoperatørrolle må derfor
# BÅDE ha `platform:admin` (for kontrollplanet) og `decisions:read` (for å
# komme inn her) — eller `platform:admin` må registreres i `LESESCOPES`.
# Det skal være en bevisst endring i autorisasjonslaget, ikke noe dette
# endepunktet avgjør på egen hånd.
# ---------------------------------------------------------------------------

def utrulling(tjeneste, request: Request) -> Response:
    from . import utrulling as utrullingsmodul

    def _fn(conn, auth, rid):
        # `?sprak=` velger fritekstoversettelsen av «neste steg». Kundenavn og
        # «neste steg» kan ikke ligge i det ANONYMT nedlastbare locale-settet
        # (P1, runde 3), så oversettelsen må følge raden ut her. Parameteren er
        # ren presentasjon: `svar_for` bruker den ikke til å velge rader, og en
        # ukjent verdi gir norsk tekst.
        sprak = request.query_params.get("sprak")
        svar = utrullingsmodul.svar_for(auth.tenant, auth.scopes, sprak)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)
