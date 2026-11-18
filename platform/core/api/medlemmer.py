"""GET /v1/firma/medlemmer — firmaets aktive medlemmer, for eiervelgeren.

Seks registreringsskjemaer ba om eieren som rå `bruker_id`, en verdi
ingen kunde kjenner. Svaret her er det minste velgeren trenger: id og
visningsnavn. ALDRI e-post — den er OIDC-profilens, og en kollega trenger
den ikke for å peke ut en eier.

Radene er tenantens egne: `brukermedlemskap` har RLS på tenantkonteksten
som `_les` setter, og API-rollen har bare SELECT (010).
"""
from __future__ import annotations

MAKS = 500


def svar_for(conn, tenant: str) -> dict:
    rader = conn.execute(
        "SELECT m.bruker_id::text,"
        "       nullif(btrim(coalesce(b.profil->>'visningsnavn', '')), '')"
        "  FROM brukermedlemskap m"
        "  JOIN brukeridentitet b ON b.bruker_id = m.bruker_id"
        " WHERE m.tenant = %s AND m.aktiv"
        " ORDER BY 2 NULLS LAST, 1 LIMIT %s", (tenant, MAKS)).fetchall()
    return {"medlemmer": [{"bruker_id": bid, "navn": navn}
                          for bid, navn in rader]}


def medlemmer(tjeneste, request):
    """GET /v1/firma/medlemmer (decisions:read — alle kunderoller)."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = svar_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)
