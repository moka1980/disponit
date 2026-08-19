"""Versjonshistorikk og diff mellom vilkårlige versjoner (047, klarsignal
§6): NY lesevei, EKSISTERENDE scope (`policy:read`).

Flaten leser aldri `policyer` direkte (port 38): begge rutene går gjennom
policy-eierens definere (`policyversjoner_for_tenant`,
`policyversjon_innhold`) med eksplisitt tenantport (SP-1) — og kallerens
GUC-RLS står som andre lag. Diffen mellom to vilkårlige versjoner er
`strukturert_diff` + `klassifiser` på de to innholdene — ren gjenbruk
(lesesvar runde 1: begge er rene funksjoner av to dicts), ingen ny
logikk.

Historikkens attestanter kommer fra HENDELSEN (`policyaktivering`), aldri
fra en gjetning: en versjon uten hendelse (ubundet historisk, backfillen
fant intet entydig match) viser `attestanter: null` — flaten sier
«attestanter ikke bundet», aldri feil attestanter (port 21).
"""
from __future__ import annotations

import psycopg
from starlette.requests import Request
from starlette.responses import Response

from .policyadmin_http import _Avbrudd, _feil, _leseauth, _med_conn

#: Grunnkodene editoren TILBYR for `menneskelig_overstyring` (port 30):
#: de blokkerende politikk-utfallene motoren faktisk feller — aldri de
#: tekniske (deny-settet i policy_validator.schema). Konstanten bor HER,
#: ikke i schema.py: skjemaets validering er motorsemantikk (checksummet i
#: MOTOR_SEMANTIKKVERSJON), mens dette er flatens KURATERING av hva som er
#: meningsfullt å velge — å endre listen endrer ingen beslutning.
#: Kildene i engine.py: belop_over_grense (grense 4), valuta_ikke_tillatt
#: (5), frekvensgrense_naadd (7), utenfor_tidsvindu (6),
#: rolle_ikke_tillatt (3), dataklasse_ikke_tillatt (8),
#: modus_alltid_stopp (1).
MENNESKELIG_GODKJENNBARE_GRUNNKODER = (
    "belop_over_grense", "valuta_ikke_tillatt", "frekvensgrense_naadd",
    "utenfor_tidsvindu", "rolle_ikke_tillatt", "dataklasse_ikke_tillatt",
    "modus_alltid_stopp",
)


def versjoner_endepunkt(tjeneste, request: Request) -> Response:
    from .app import _rid
    from .policyadmin_http import _ok
    rid = _rid(request)
    policy_id = request.path_params["policy_id"]

    def kjor(conn):
        tenant, _bid = _leseauth(tjeneste, request, conn, rid)
        rader = conn.execute(
            "SELECT versjon, innholds_hash, aktiv, opprettet, aktivert_ts,"
            "       attestant_a, attestant_b, aktivert_av_operasjon,"
            "       rollback_av_versjon"
            "  FROM policyversjoner_for_tenant(%s, %s)",
            (tenant, policy_id)).fetchall()
        conn.rollback()
        return _ok({"policy_id": policy_id, "versjoner": [
            {"versjon": r[0], "innholds_hash": r[1], "aktiv": r[2],
             "opprettet": r[3].isoformat(),
             "aktivert_ts": r[4].isoformat() if r[4] else None,
             # Attestantene finnes bare via hendelsen. Ubundet rad →
             # None, og flaten viser «attestanter ikke bundet».
             "attestanter": ([a for a in (r[5], r[6]) if a]
                             if r[7] else None),
             "aktivert_av_operasjon": r[7],
             "rollback_av_versjon": r[8]} for r in rader]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def diff_endepunkt(tjeneste, request: Request) -> Response:
    from policy_validator import klassifikator, policydiff
    from .app import _rid
    from .policyadmin_http import _ok
    rid = _rid(request)
    policy_id = request.path_params["policy_id"]

    def kjor(conn):
        tenant, _bid = _leseauth(tjeneste, request, conn, rid)
        fra = request.query_params.get("fra")
        til = request.query_params.get("til")
        if not fra or not til:
            return _feil("request_feilformet", rid)
        try:
            innhold = {}
            for navn, versjon in (("fra", fra), ("til", til)):
                innhold[navn] = conn.execute(
                    "SELECT policyversjon_innhold(%s, %s, %s)",
                    (tenant, policy_id, versjon)).fetchone()[0]
        except psycopg.errors.NoDataFound:
            conn.rollback()
            return _feil("ikke_funnet", rid)
        conn.rollback()
        kl = klassifikator.klassifiser(innhold["fra"], innhold["til"])
        return _ok({
            "policy_id": policy_id, "fra": fra, "til": til,
            "diff": policydiff.strukturert_diff(innhold["fra"],
                                                innhold["til"]),
            "risikoklasse": kl["klasse"],
            "klassifisering_endringer": kl["endringer"]}, rid)

    return _med_conn(tjeneste, rid, kjor)


def editorgrunnlag_endepunkt(tjeneste, request: Request) -> Response:
    """Editorens LUKKEDE vokabularer, lest fra kildene — aldri hardkodet i
    flaten (port 30/32): plattformvilkårene fra `malautorisasjonsvilkar`
    (immutabel tabell, plattform-global og uten RLS med vilje), og de
    godkjennbare grunnkodene fra motorens egen konstant."""
    from .app import _rid
    from .policyadmin_http import _ok
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _leseauth(tjeneste, request, conn, rid)
        rader = conn.execute(
            "SELECT vilkar_type, maldomene FROM malautorisasjonsvilkar"
            " ORDER BY vilkar_type").fetchall()
        conn.rollback()
        return _ok({
            "plattformvilkar": [{"vilkar_type": r[0], "maldomene": r[1]}
                                for r in rader],
            "godkjennbare_grunnkoder":
                list(MENNESKELIG_GODKJENNBARE_GRUNNKODER)}, rid)

    return _med_conn(tjeneste, rid, kjor)
