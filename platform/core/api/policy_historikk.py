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

#: Grunnkodene editoren TILBYR for `menneskelig_overstyring` (port 30) —
#: og feltet hver av dem krever i oppføringen.
#:
#: Listen var HÅNDKURATERT her, ut fra hvilke blokkerende politikk-utfall
#: motoren feller, med den begrunnelsen at «å endre listen endrer ingen
#: beslutning». Det stemte ikke (Codex P1): fem av de sju kodene kan
#: `_loft_policy` ikke uttrykke i det hele tatt, så en overstyring valgt
#: fra dem endte i STOPP ved HVER godkjenning. Eier konfigurerte noe som
#: så komplett ut og aldri kunne virke.
#:
#: Kurateringen er derfor ikke lenger vår: den ER motorens uttrykkskraft,
#: lest direkte fra `engine.LOFTBARE_GRUNNKODER`. Samme kilde som
#: innføringskontrakten avviser mot, så flaten aldri kan tilby noe
#: valideringen nekter — eller omvendt.
def _loftbare() -> dict[str, str]:
    from policy_validator.engine import LOFTBARE_GRUNNKODER
    return LOFTBARE_GRUNNKODER


#: Hvilke handlinger som i det hele tatt KREVER et målautorisasjonsvilkår,
#: og for hvilket domene — som (prefiks, maldomene)-par fra `oppdragskontrakt`.
#:
#: Kravet i `_krev_malautorisasjonsvilkar` gjelder en handling nøyaktig når
#: dens kodefestede type bærer `krever_malautorisasjon` OG et
#: `malautorisasjonsdomene`: bærer typen flagget, er `_er_ekstern_lesing`
#: sann uten å spørre registeret (koden først), og mangler flagget eller
#: domenet, blir dommen en EGEN feillinje («ekstern_lesing uten
#: målautorisasjonsbærende oppdragstype»), ikke et vilkårskrav. Denne
#: differansen er derfor ren KODE — ingen `modulkontrakt`, ingen
#: registerrad — og kan trygt leses av flaten.
#:
#: Flaten trenger den fordi låsen på en vilkårsrad er en påstand om at
#: SERVEREN nekter fjerningen (port 31/34). Uten domenebindingen låste
#: editoren ethvert velformet plattformnavn, også på en handling kravet
#: ikke gjelder for — en rad serveren gjerne ville sluppet, men som eier
#: verken kunne redigere eller fjerne (Codex P2).
def _malautorisasjonskrav() -> list[dict]:
    import oppdragskontrakt
    return sorted(
        ({"prefiks": p, "maldomene": t.malautorisasjonsdomene}
         for t in oppdragskontrakt.OPPDRAGSTYPER.values()
         if t.krever_malautorisasjon and t.malautorisasjonsdomene
         for p in t.handlingsprefikser),
        key=lambda k: (k["prefiks"], k["maldomene"]))


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
            "       rollback_av_versjon, rollback_kilde, aktiveringskilde"
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
             "rollback_av_versjon": r[8],
             # Om kildeGENERASJONEN fortsatt er den som bærer nummeret:
             # `bundet`, `borte` (slettet eller gjenskapt med annet
             # innhold) eller `ubundet` (rullbakk fra før hashen fantes).
             # Uten den kunne flaten bare gjenta nummeret, og et nummer er
             # ikke en varig identitet (047, Codex P2).
             "rollback_kilde": r[9],
             # VEIEN inn (047): `styrt` (fire-øyne + hendelse), `bootstrap`
             # (oppsettsregistreringen) eller `historisk` (lå der da 047
             # landet). Uten den kunne flaten ikke skille en bootstrap
             # skrevet i går fra en rad som ligger foran hele lineagen.
             "aktiveringskilde": r[10]} for r in rader]}, rid)

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
            # 404 eksplisitt, som rullbakkruta gjør for det SAMME
            # `policyversjon_innhold`-avslaget (Codex P2). `ikke_funnet`
            # står ikke i `_FEIL_HTTP`, så uten koden her falt en foreldet,
            # arkivert eller ukjent `fra`/`til` gjennom til standardsvaret
            # 409 — en konflikt, på en tilstand det ikke finnes noen
            # konflikt i: ressursen er bare borte.
            return _feil("ikke_funnet", rid, 404)
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
        loftbare = _loftbare()
        return _ok({
            "plattformvilkar": [{"vilkar_type": r[0], "maldomene": r[1]}
                                for r in rader],
            # Navnene alene holdt ikke her heller: uten å vite hvilket
            # DOMENE den enkelte handlingen krever, låste flaten et
            # velformet plattformnavn hvor som helst det sto (Codex P2).
            # Se `_malautorisasjonskrav`.
            "malautorisasjonskrav": _malautorisasjonskrav(),
            # Navnene alene holdt ikke (Codex P1): flaten må vite hvilket
            # FELT hver grunnkode krever, ellers lager den oppføringer
            # motoren ikke kan anvende. `krever` er feltnavnet;
            # `belop_maks` drar dessuten `valuta` med seg (skjemaets
            # `dependentRequired`).
            "godkjennbare_grunnkoder": sorted(loftbare),
            "godkjennbare_krav": [
                {"grunnkode": gk, "krever": felt}
                for gk, felt in sorted(loftbare.items())]}, rid)

    return _med_conn(tjeneste, rid, kjor)
