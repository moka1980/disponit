"""Firmaets status har en virkning (eiers vedtak 27/11).

MÅLT FØR DETTE: `firma.status` kunne bli `utlopt` (prøveutløpssveipen,
190) og `stengt` (plattformeieren, 199) — og ingenting brydde seg.
Agentene sendte purringer, e-posten ble hentet og svar gikk ut for et
firma hvis prøveperiode var over eller som eieren hadde stengt.

EIERS VEDTAK 27/11 («Pause agentene, les + eksport»): når prøveperioden
er over, STOPPER det plattformen gjør av seg selv for firmaet — nye
bestillinger (alt agentene og menneskene sender, går gjennom
`bestilling.utfor_bestilling`), e-posthentingen og utsendingen fra
postboksen. Innloggingen, lesingen og eksporten består: kunden skal se
hva hun har, og kunne ta det med seg. Plattformeieren aktiverer firmaet
igjen i plattformadmin, og alt fortsetter der det slapp.

ÅPEN VED FEIL. Statusen er en forretningsregel, ikke en sikkerhetsgrense
(den er tenantens EGEN rad, bak RLS). Kan den ikke leses, går runden som
før — en database som hikker skal ikke stoppe hvert firma i basen.

ET FIRMA UTEN RAD ER IKKE PAUSET: eierens egne tenanter ble til før
firmaregisteret (190) og har ingen rad. De skal gå.
"""
from __future__ import annotations

#: Statusene som pauser. `prove` og `aktiv` går; alt annet står stille.
PAUSET = frozenset({"utlopt", "stengt"})


def status_i(conn, tenant: str) -> str | None:
    """Statusen i en transaksjon der tenantkonteksten ALT er satt."""
    r = conn.execute("SELECT status FROM firma_hent(%s)",
                     (tenant,)).fetchone()
    return r[0] if r else None


def er_pauset_i(conn, tenant: str) -> bool:
    return status_i(conn, tenant) in PAUSET


def pausede(tjeneste, tenanter) -> set[str]:
    """-> de av `tenanter` som står stille. For bakgrunnsrundene, som
    kjører med plan-rollen og derfor slår opp gjennom API-ets egen
    tilkobling (der `firma_hent` er gitt)."""
    import psycopg

    from db.pg import sett_kontekst
    tenanter = {t for t in tenanter if t}
    if not tenanter or tjeneste is None:
        return set()
    try:
        conn = tjeneste.pool.hent()
    except Exception:                                       # noqa: BLE001
        return set()
    ut: set[str] = set()
    try:
        for t in sorted(tenanter):
            try:
                sett_kontekst(conn, t, "system:firmastatus", "firmastatus")
                if er_pauset_i(conn, t):
                    ut.add(t)
            except psycopg.Error:
                pass
            finally:
                try:
                    conn.rollback()
                except psycopg.Error:
                    pass
    finally:
        tjeneste.pool.gi_tilbake(conn)
    return ut


#: Hvor mange dager før fristen nedtellingen vises. Før det er fristen
#: ikke noe å handle på, og et banner hver dag i en måned er støy.
NEDTELLING_DOGN = 7


def status_for(conn, tenant: str) -> dict:
    """Det skallet trenger for banneret — og ikke mer. Dagene regnes i
    BASEN, i Oslo-tid, samme klokke som prøveutløpssveipen (190): en
    nedtelling som sa «1 dag igjen» mens sveipen alt hadde stengt, ville
    vært to sannheter om samme frist."""
    import os
    r = conn.execute(
        "SELECT f.status, f.prove_utloper,"
        "       f.prove_utloper - (now() AT TIME ZONE 'Europe/Oslo')::date"
        "  FROM firma_hent(%s) f", (tenant,)).fetchone()
    # KONTAKTEN ER LEVERANDØRENS (234), redigert i plattformadmin.
    # Miljøvariabelen er reserven fra før skjemaet fantes. Uten noen av
    # dem sier banneret ingenting om kontakt — ikke en tom lenke.
    kontakt = None
    try:
        with conn.transaction():
            r = conn.execute("SELECT kontakt_epost FROM"
                             " plattform_leverandor_les()").fetchone()
        kontakt = r[0] if r else None
    except Exception:                                       # noqa: BLE001
        kontakt = None
    kontakt = kontakt or (os.environ.get("DISPONIT_KONTAKT_EPOST")
                          or "").strip() or None
    if r is None:
        return {"status": None, "prove_utloper": None, "dogn_igjen": None,
                "pauset": False, "vis": False, "kontakt_epost": kontakt}
    status, frist, igjen = r
    pauset = status in PAUSET
    vis = pauset or (status == "prove" and igjen is not None
                     and igjen <= NEDTELLING_DOGN)
    return {"status": status,
            "prove_utloper": frist.isoformat() if frist else None,
            "dogn_igjen": igjen, "pauset": pauset, "vis": vis,
            "kontakt_epost": kontakt}


def status_endepunkt(tjeneste, request):
    """GET /v1/firma/status (decisions:read) — prøveperioden og om
    firmaet står stille, for banneret i skallet."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = status_for(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)
