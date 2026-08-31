"""Model card for M-31 — en AVLEDET leseflate (dom 4, ratifisert 31/8).

Kortet LAGRES aldri: hvert svar er en join over registerets egne rader
(`modulrelease` × siste kjøringer × gjeldende krav × golden-sett-hodet)
i lesetransaksjonen, så det kan per konstruksjon ikke bli stale. Runtime
har KUN SELECT på m31-tabellene (086 + migrer.py) — all skriving går
gjennom de herdede dørene, og denne modulen eier ingen av dem.

Dataene er GLOBALE (plattformregisterets form, ingen RLS): et golden-sett
og et evalueringskrav gjelder modulen på tvers av tenanter, og digesten
er modellens miljøuavhengige identitet (dom 2). Scopet på ruten
(`security:read`, RUTESCOPE i app.py) er derfor admin-lesescopet, ikke
en tenantfiltrering — her finnes ingen tenantkolonne å filtrere på.

Alle avledninger skjer HER eller i basen, aldri i flaten (M-16-regelen:
flaten deler aldri to av svarets tall på hverandre) — men v1 avleder
ingenting: hvert tall i svaret står i en registerrad.
"""
from __future__ import annotations

#: Taket på kjøringslisten per modul. Kortet er en AVLESNING, ikke en
#: paginert logg — hele strømmen bor i `modellstyring_hendelse` og
#: `evalueringskjoring`, og en drilling dit er en egen flate (v2).
KJORINGER_MAKS = 20


def _ts(verdi) -> str | None:
    return verdi.isoformat() if verdi is not None else None


def _kjoring(rad) -> dict:
    (kjoring_id, artifact_digest, kravversjon, antall_eksempler,
     antall_bestatt, antall_modellfeil, p50_ms, p95_ms, varighet_s,
     modellnavn, bestatt, avsluttet_ts) = rad
    return {
        "kjoring_id": str(kjoring_id),
        "artifact_digest": artifact_digest,
        "kravversjon": kravversjon,
        "antall_eksempler": antall_eksempler,
        "antall_bestatt": antall_bestatt,
        "antall_modellfeil": antall_modellfeil,
        "p50_ms": p50_ms,
        "p95_ms": p95_ms,
        "varighet_s": varighet_s,
        "modellnavn": modellnavn,
        "bestatt": bestatt,
        "avsluttet_ts": _ts(avsluttet_ts),
    }


def svar_for(conn) -> dict:
    """Model card per modul som har m31-rader. -> {"moduler": [...]}.

    Modulmengden er unionen av moduler med golden-sett og moduler med
    krav — et sett uten krav er en modul under seeding og skal SES, ikke
    vente på at porten er skrudd på.
    """
    moduler: list[dict] = []
    modul_ids = [r[0] for r in conn.execute(
        "SELECT modul_id FROM golden_sett"
        " UNION SELECT modul_id FROM evalueringskrav"
        " ORDER BY modul_id").fetchall()]
    for modul_id in modul_ids:
        krav_rad = conn.execute(
            "SELECT kravversjon, sett_id, sett_versjon, sett_hash,"
            " terskel_min_andel::float8, terskel_maks_p95_ms,"
            " terskel_maks_modellfeil, opprettet"
            " FROM evalueringskrav"
            " WHERE modul_id = %s AND status = 'gjeldende'",
            (modul_id,)).fetchone()
        krav = None
        if krav_rad:
            krav = {
                "kravversjon": krav_rad[0],
                "sett_id": krav_rad[1],
                "sett_versjon": krav_rad[2],
                "sett_hash": krav_rad[3],
                "terskel_min_andel": krav_rad[4],
                "terskel_maks_p95_ms": krav_rad[5],
                "terskel_maks_modellfeil": krav_rad[6],
                "opprettet": _ts(krav_rad[7]),
            }
        # Settet kortet viser: gjeldende kravs sett når kravet finnes
        # (det er DET porten måler mot), ellers det sist registrerte —
        # en modul under seeding har et sett, men ennå ingen port.
        if krav:
            sett_rad = conn.execute(
                "SELECT sett_id, versjon, innhold_hash, antall_eksempler,"
                " beskrivelse, opprettet FROM golden_sett"
                " WHERE modul_id = %s AND sett_id = %s AND versjon = %s",
                (modul_id, krav["sett_id"], krav["sett_versjon"])).fetchone()
        else:
            sett_rad = conn.execute(
                "SELECT sett_id, versjon, innhold_hash, antall_eksempler,"
                " beskrivelse, opprettet FROM golden_sett"
                " WHERE modul_id = %s ORDER BY opprettet DESC, sett_id,"
                " versjon DESC LIMIT 1", (modul_id,)).fetchone()
        sett = None
        if sett_rad:
            sett = {
                "sett_id": sett_rad[0],
                "versjon": sett_rad[1],
                "innhold_hash": sett_rad[2],
                "antall_eksempler": sett_rad[3],
                "beskrivelse": sett_rad[4],
                "opprettet": _ts(sett_rad[5]),
            }
        kjoring_sql = (
            "SELECT kjoring_id, artifact_digest, kravversjon,"
            " antall_eksempler, antall_bestatt, antall_modellfeil,"
            " p50_ms, p95_ms, varighet_s::float8, modellnavn, bestatt,"
            " avsluttet_ts FROM evalueringskjoring WHERE modul_id = %s")
        kjoringer = [_kjoring(r) for r in conn.execute(
            kjoring_sql + " ORDER BY avsluttet_ts DESC, kjoring_id"
            " LIMIT %s", (modul_id, KJORINGER_MAKS)).fetchall()]
        # Siste BESTÅTTE mot GJELDENDE krav — kortets kjernelinje: den
        # kjøringen porten faktisk ville latt bære et bytte nå. En
        # bestått mot et historisk krav er evidens, ikke bæreevne.
        siste_bestatte = None
        if krav:
            rad = conn.execute(
                kjoring_sql + " AND bestatt AND kravversjon = %s"
                " ORDER BY avsluttet_ts DESC, kjoring_id LIMIT 1",
                (modul_id, krav["kravversjon"])).fetchone()
            if rad:
                siste_bestatte = _kjoring(rad)
        moduler.append({
            "modul_id": modul_id,
            "krav": krav,
            "sett": sett,
            "siste_bestatte": siste_bestatte,
            "kjoringer": kjoringer,
        })
    return {"moduler": moduler}
