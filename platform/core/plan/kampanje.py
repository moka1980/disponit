"""M-44 kampanjeutløseren (ARC B kampanje, PR 3): registeret bestiller
på sendedagen — policyen avgjør.

Sveipen (114) finner funn og flytter ingenting. Utløseren er leddet
mellom planen og bestillingen: på sendedagen plukker den hver (kampanje,
mottaker) i planen der kampanjen har innhold og mottakeren er aktiv med
adresse (155 `m44_kampanjekandidater`), og bestiller `kampanje.send`
gjennom NØYAKTIG samme bestillingsvei som et menneske
(`utfor_bestilling`, 044 §4-grepet — som purringsutløseren). Ingen egen
autoritet: policyport, idempotens, kvote, samtykkeattestasjon og
oppdragsopprettelse er den samme veien.

Tre vern rundt bestillingen:
  * ÉN BESTILLING PER KAMPANJE OG MOTTAKER. Idempotensnøkkelen er
    deterministisk (`kampanje:<kampanje_id>:<mottaker_id>`), og utfallet
    bokføres i `kampanjebestilling` FØR neste runde kan se paret igjen.
    Ble det brudd (trukket samtykke, frekvens), er saken i unntakskøen
    et menneskes — utløseren prøver ikke igjen.
  * POLICYEN MÅ NEVNE HANDLINGEN. En tenant uten `kampanje.send` i sin
    aktive policy får funn som i dag, og ingenting bestilles — uten å
    brenne en beslutning på et «ukjent_handling».
  * KILL-SWITCH: `DISPONIT_KAMPANJE_UTLOSER=av` i planarbeiderens konfig
    stopper hele utløseren uten deploy. Planen står.

Forbigående feil og plattformtilstand (modulen ikke claimbar ennå, ingen
aktiv policy) bokføres IKKE: paret er kandidat igjen neste runde, og
kjernens idempotens gjør et gjentak til gjenspill, aldri ny kvote. En dom
over kampanjen (`kampanje_ukjent`) bokføres som `feil:<kode>`.
"""
from __future__ import annotations

import json
import os

#: Hardt tak PER TENANT per runde: overskuddet vurderes neste runde.
MAKS_PER_TENANT = int(os.environ.get("DISPONIT_KAMPANJE_MAKS", "50"))

AKTOR = "agent:kampanje"


def er_av() -> bool:
    return os.environ.get("DISPONIT_KAMPANJE_UTLOSER", "").strip().lower() \
        in ("av", "0", "false", "nei")


def idempotensnokkel(kampanje_id, mottaker_id) -> str:
    return f"kampanje:{kampanje_id}:{mottaker_id}"


def kandidater(conn, grense: int = MAKS_PER_TENANT) -> list:
    """-> [(tenant, kampanje_id, mottaker_id, planlagt_sendt)].
    Kryss-tenant-døra krever at INGEN tenantkontekst står."""
    conn.rollback()
    rader = conn.execute("SELECT * FROM m44_kampanjekandidater(%s)",
                         (grense,)).fetchall()
    conn.rollback()
    return rader


def policy_har_kampanje(conn, tenant: str) -> bool:
    """Har tenantens aktive policy handlingen `kampanje.send`? Én aktiv
    policy er bestillingsveiens eget krav (`policy_ukjent` ellers)."""
    from db.pg import sett_kontekst
    sett_kontekst(conn, tenant, AKTOR, "kampanje-policy")
    rader = conn.execute(
        "SELECT innhold FROM policyer WHERE tenant=%s AND aktiv",
        (tenant,)).fetchall()
    conn.rollback()
    if len(rader) != 1:
        return False
    innhold = rader[0][0]
    if isinstance(innhold, (str, bytes)):
        innhold = json.loads(innhold)
    return any(h.get("id") == "kampanje.send"
               for h in (innhold or {}).get("handlinger") or [])


def utlos_en(tjeneste, conn, rad) -> dict:
    """Én kandidat gjennom bestillingsveien, og utfallet bokført.
    Utfallstolkningen er purringsutløserens (`plan.purring._utfall`):
    samme skille mellom forbigående, plattformtilstand og dom."""
    from api.bestilling import utfor_bestilling
    from db.pg import sett_kontekst
    from plan.purring import _utfall
    tenant, kampanje_id, mottaker_id, _dato = rad
    kid, mid = str(kampanje_id), str(mottaker_id)
    rid = f"kampanje-{kid[:8]}-{mid[:8]}"
    nokkel = idempotensnokkel(kid, mid)
    data = {"bestillingstype": "kampanje.send",
            "kampanje_ref": f"kampanje:{kid}",
            "mottaker_ref": f"mottaker:{mid}", "omfang": "mottaker"}
    sett_kontekst(conn, tenant, AKTOR, rid)
    res = utfor_bestilling(tjeneste, conn, tenant, AKTOR, data, nokkel, rid)
    utfall, oppdrag_id, unntak_id, detalj = _utfall(res)
    if utfall is None:
        conn.rollback()
        return {"kampanje": kid, "mottaker": mid,
                "forbigaende": detalj.get("feil")}
    sett_kontekst(conn, tenant, AKTOR, rid)
    ny = conn.execute(
        "SELECT m44_bokfor_kampanjebestilling(%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s::jsonb)",
        (tenant, kampanje_id, mottaker_id, nokkel, utfall, oppdrag_id,
         unntak_id, rid, json.dumps(detalj, ensure_ascii=False))
    ).fetchone()[0]
    conn.commit()
    return {"kampanje": kid, "mottaker": mid, "utfall": utfall,
            "oppdrag_id": oppdrag_id, "unntak_id": unntak_id,
            "bokfort": bool(ny)}


def kjor_en_runde(tjeneste, conn) -> dict:
    if er_av():
        print(json.dumps({"hendelse": "kampanje_utloser_av"}), flush=True)
        return {"av": True, "plukket": 0, "resultater": []}
    rader = kandidater(conn)
    har_policy: dict[str, bool] = {}
    resultater = []
    hoppet_uten_policy = 0
    for rad in rader:
        tenant = rad[0]
        if tenant not in har_policy:
            har_policy[tenant] = policy_har_kampanje(conn, tenant)
            if not har_policy[tenant]:
                print(json.dumps({"hendelse": "kampanje_uten_policy",
                                  "tenant": tenant}), flush=True)
        if not har_policy[tenant]:
            hoppet_uten_policy += 1
            continue
        resultater.append(utlos_en(tjeneste, conn, rad))
    res = {"plukket": len(rader), "uten_policy": hoppet_uten_policy,
           "resultater": resultater}
    if rader:
        print(json.dumps({"hendelse": "kampanje_runde", **res},
                         ensure_ascii=False, default=str), flush=True)
    return res
