"""Disponit tilstandslag (PR-004, ADR-001).

Tre deler:
  1. `koble()` / `migrer()` — tilkobling og migrasjonskjøring.
  2. `PgTellerLager` — atomisk frekvensreservasjon i databasen
     (ADR-001 krav 1). Streng bool-kontrakt fra TellerLager.
  3. `sikker_beslutning_pg()` — logg og reservasjon i SAMME transaksjon
     (ADR-001 krav 2), med samme fail-closed-kontrakt som filvarianten.

Fail-closed overalt: utilgjengelig database, feilet commit eller uventet
exception gir STOPP — aldri «fortsett uten logg».
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import psycopg

from policy_validator.audit import lag_loggpost
from policy_validator.engine import (
    STOPP, TILLAT, UNNTAK, Decision, Grunn, TellerLager, brudd_utfall, evaluate)

_MIGRASJONER = Path(__file__).resolve().parent / "migrations"


def koble(dsn: str) -> psycopg.Connection:
    """Én tilkobling, autocommit AV — transaksjoner styres eksplisitt."""
    return psycopg.connect(dsn, autocommit=False)


def migrer(conn: psycopg.Connection) -> list[int]:
    """Kjører manglende migrasjoner. Beholdt som navn av hensyn til
    kallstedene; selve kjøringen er flyttet til `db.kjorer`, som legger til
    advisory-lås, checksum-verifisering og avvisning av endret historikk
    (PR-005). Den gamle varianten kjørte ALLE filer på nytt hver gang og
    hadde ingen anelse om at en historisk fil var endret."""
    from .kjorer import migrer as _kjor
    return _kjor(conn)


UKJENT_TENANT = "<ukjent>"


def sett_tenant(conn: psycopg.Connection, tenant: str | None) -> str:
    """Setter `disponit.tenant` for GJELDENDE transaksjon (row level security).

    Migrasjon 002 håndhever tenant-isolasjon i databasegrensen: policyen
    sammenligner radens tenant med denne variabelen. Er den ikke satt, gir
    `current_setting(..., true)` NULL, og både lesing og skriving blir tomt
    — fail-closed. Glemmer koden å sette tenant, stopper databasen den, i
    stedet for å vise alle tenanters rader.

    Uautentiserte forsøk har ingen tenant, men skal fortsatt havne i
    revisjonsloggen — de føres på den reserverte verdien `<ukjent>`, aldri
    på en ekte kundes tenant. En avvist forespørsel som ikke logges er verre
    enn en som logges på feil sted.
    """
    t = tenant if isinstance(tenant, str) and tenant.strip() else UKJENT_TENANT
    conn.execute("SELECT set_config('disponit.tenant', %s, true)", (t,))
    return t


def _forsok_reservasjon(conn: psycopg.Connection, nokkel: tuple[str, ...],
                        siden: datetime, maks: int,
                        tidspunkt: datetime) -> bool:
    """Den atomiske reservasjonen — ÉN kilde, brukt av begge veier inn.

    Forutsetter at kalleren allerede har åpnet en transaksjon: advisory-
    låsen er `xact`-varianten og gjelder til commit/rollback.

    Grunnen til at dette er en egen funksjon: reservasjons-SQL-en lå
    opprinnelig i to kopier — én i `PgTellerLager.reserver` og én inline i
    `sikker_beslutning_pg`. Nøyaktig den duplikatformen ga P1 nr. 4 i
    PR-002, der `ved_brudd`-mappingen fantes to steder og bare den ene ble
    rettet. To kopier av en sikkerhetskritisk kontroll er en feil som
    venter på å bli innført, ikke en stilsak.
    """
    tenant, handling, felt, gruppe = nokkel
    sett_tenant(conn, tenant)   # RLS — samme sted som selve reservasjonen
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 ("\x1f".join(nokkel),))
    rad = conn.execute(
        "INSERT INTO frekvens_hendelser"
        "   (tenant, handling, nokkel_felt, gruppe, tidspunkt)"
        " SELECT %s, %s, %s, %s, %s"
        "  WHERE (SELECT count(*) FROM frekvens_hendelser"
        "          WHERE tenant=%s AND handling=%s"
        "            AND nokkel_felt=%s AND gruppe=%s"
        "            AND tidspunkt >= %s) < %s"
        " RETURNING id",
        (tenant, handling, felt, gruppe, tidspunkt,
         tenant, handling, felt, gruppe, siden, maks)).fetchone()
    return rad is not None      # ekte bool — kontrakten krever identitet


class PgTellerLager(TellerLager):
    """Frekvensteller med databasens garantier.

    `reserver` er én transaksjon: advisory-lås på nøkkelen serialiserer
    konkurrerende reservasjoner, deretter telles og skrives det udelelig.
    Låsen slippes automatisk ved commit/rollback (xact-variant) — ingen
    opprydding å glemme, ingen foreldreløse låser ved krasj.
    """

    def __init__(self, conn: psycopg.Connection) -> None:
        self._conn = conn

    def antall(self, nokkel: tuple[str, ...], siden: datetime) -> int:
        """Rådgivende — aldri håndheving (TellerLager-kontrakten)."""
        tenant, handling, felt, gruppe = nokkel
        sett_tenant(self._conn, tenant)   # RLS: ellers ser vi ingenting
        rad = self._conn.execute(
            "SELECT count(*) FROM frekvens_hendelser"
            " WHERE tenant=%s AND handling=%s AND nokkel_felt=%s"
            "   AND gruppe=%s AND tidspunkt >= %s",
            (tenant, handling, felt, gruppe, siden)).fetchone()
        self._conn.rollback()  # ren lesing — ikke hold transaksjon åpen
        return int(rad[0])

    def reserver(self, nokkel: tuple[str, ...], siden: datetime, maks: int,
                 tidspunkt: datetime) -> bool:
        with self._conn.transaction():
            return _forsok_reservasjon(self._conn, nokkel, siden, maks,
                                       tidspunkt)
        # psycopg.Error propagerer med vilje: sikker_beslutning fanger den
        # og gir STOPP (tellerfeil). Fail-closed, aldri gjetting.

    def registrer(self, nokkel: tuple[str, ...], tidspunkt: datetime) -> None:
        """KUN testoppsett/migrering — aldri håndheving."""
        tenant, handling, felt, gruppe = nokkel
        with self._conn.transaction():
            sett_tenant(self._conn, tenant)
            self._conn.execute(
                "INSERT INTO frekvens_hendelser"
                " (tenant, handling, nokkel_felt, gruppe, tidspunkt)"
                " VALUES (%s, %s, %s, %s, %s)",
                (tenant, handling, felt, gruppe, tidspunkt))


def _skriv_loggpost(conn: psycopg.Connection, post: dict) -> None:
    """Skriver loggposten. Tenant settes både som kolonne og som
    sesjonsvariabel, ellers avviser row level security-policyen raden."""
    post = dict(post)
    post["tenant"] = sett_tenant(conn, post.get("tenant"))
    conn.execute(
        "INSERT INTO revisjonslogg (ts, tenant, aktor, kilde, input_hash,"
        " policy_id, bransjemal, mal_status, schema_version, beslutning,"
        " unntak_kategori, effekt, begrunnelse)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (post["ts"], post["tenant"], post["aktor"], post["kilde"],
         post["input_hash"], post["policy_id"], post["bransjemal"],
         post["mal_status"], post["schema_version"], post["beslutning"],
         post["unntak_kategori"], post["effekt"],
         json.dumps(post["begrunnelse"], ensure_ascii=False)))


def sikker_beslutning_pg(policy: dict, context, event: dict,
                         conn: psycopg.Connection,
                         naa: datetime | None = None,
                         nokler: dict | None = None) -> Decision:
    """PostgreSQL-varianten av logg-før-utførelse-kontrakten.

    Forskjeller fra filvarianten:
      * ADR-001 krav 2: reservasjon OG loggpost committes i SAMME
        databasetransaksjon under advisory-lås — enten begge eller ingen.
        Filvariantens kjente skjevhet («reservert men ikke logget») kan
        ikke oppstå her.
      * ADR-001 krav 3: gis `nokler` (nøkkelregister), verifiseres HMAC-
        signaturen på samtlige attestasjoner FØR motoren evaluerer.
        Ugyldig eller manglende signatur => STOPP. PR-005 (API) MÅ sende
        nokler på alle nettverksforespørsler — uten register er porten av
        (kun lovlig i lokal utvikling og run_synthetic).

    Kontrakt uendret: sideeffekt HVIS OG BARE HVIS retur er TILLAT.
    Alle feilveier — DB nede, commit feilet, motor-exception, signatur-
    brudd — er STOPP.
    """
    from policy_validator import attestering
    naa = naa or datetime.now(timezone.utc)
    handling = event.get("handling") if isinstance(event.get("handling"), str) \
        else "<mangler>"

    if nokler is not None:
        brudd = attestering.kontroller_hendelse(event, nokler)
        if brudd is not None:
            d = Decision(STOPP, handling, "signaturport", [brudd])
            try:
                with conn.transaction():
                    _skriv_loggpost(conn, lag_loggpost(d, event, policy, context))
            except Exception as e:
                return Decision(STOPP, handling, "signaturport",
                                d.begrunnelse + [Grunn(
                                    "logging_feilet", {"type": type(e).__name__})])
            return d

    teller = PgTellerLager(conn)
    try:
        d = evaluate(policy, context, event, teller=teller, naa=naa)
    except Exception as e:  # fail-closed
        d = Decision(STOPP, handling, "ukjent",
                     [Grunn("motor_exception", {"type": type(e).__name__})])

    try:
        if d.beslutning == TILLAT and d.frekvensreservasjon is not None:
            nokkel, vindu_start, maks = d.frekvensreservasjon
            with conn.transaction():
                fikk_plass = _forsok_reservasjon(conn, nokkel, vindu_start,
                                                 maks, naa)
                if fikk_plass is False:  # tapte kappløpet — samme ved_brudd som motoren
                    beslutning, effekt = brudd_utfall(policy, d.handling)
                    d = Decision(beslutning, d.handling, d.policy_id,
                                 d.begrunnelse + [Grunn(
                                     "frekvensgrense_naadd_ved_reservasjon",
                                     {"maks": maks})],
                                 unntak_kategori=("over_grense"
                                                  if beslutning == UNNTAK
                                                  else None),
                                 effekt=effekt)
                _skriv_loggpost(conn, lag_loggpost(d, event, policy, context))
        else:
            with conn.transaction():
                _skriv_loggpost(conn, lag_loggpost(d, event, policy, context))
    except Exception as e:
        return Decision(STOPP, d.handling, d.policy_id,
                        d.begrunnelse + [Grunn(
                            "logging_feilet", {"type": type(e).__name__})])
    return d
