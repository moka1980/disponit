"""M-26 tilbudsutløseren (ARC B tilbud, PR 3): registeret bestiller når
et menneske har godkjent — policyen avgjør.

Et tilbud blir `godkjent` av et menneske (169). Utløseren er leddet
mellom godkjenningen og bestillingen: den plukker hvert godkjent,
gyldig tilbud med linjer (171 `m26_tilbudskandidater`) og bestiller
`tilbud.generer` gjennom NØYAKTIG samme bestillingsvei som et menneske
(`utfor_bestilling`, 044 §4-grepet — som de fire utløserne før). Ingen
egen autoritet: policyport, idempotens, kvote, attestasjonene av
registerets fakta og oppdragsopprettelse er den samme veien.

Tre vern rundt bestillingen:
  * ÉN BESTILLING PER TILBUD. Idempotensnøkkelen er deterministisk
    (`tilbud:<tilbud_id>`), og utfallet bokføres i `tilbudsbestilling`
    FØR neste runde kan se tilbudet igjen. Ble det brudd, er saken i
    unntakskøen et menneskes — et rettet tilbud er et NYTT tilbud.
  * POLICYEN MÅ NEVNE HANDLINGEN. En tenant uten `tilbud.generer` i sin
    aktive policy får tilbud som i dag, og ingenting bestilles.
  * KILL-SWITCH: `DISPONIT_TILBUD_UTLOSER=av` i planarbeiderens konfig
    stopper hele utløseren uten deploy. Tilbudene står.

Forbigående feil og plattformtilstand bokføres IKKE: tilbudet er
kandidat igjen neste runde. En dom (`tilbud_ukjent`) bokføres som
`feil:<kode>`.
"""
from __future__ import annotations

import json
import os

#: Hardt tak PER TENANT per runde: overskuddet vurderes neste runde.
MAKS_PER_TENANT = int(os.environ.get("DISPONIT_TILBUD_MAKS", "50"))

AKTOR = "agent:tilbud"


def er_av() -> bool:
    return os.environ.get("DISPONIT_TILBUD_UTLOSER", "").strip().lower() \
        in ("av", "0", "false", "nei")


def idempotensnokkel(tilbud_id) -> str:
    return f"tilbud:{tilbud_id}"


def kandidater(conn, grense: int = MAKS_PER_TENANT) -> list:
    """-> [(tenant, tilbud_id)]. Kryss-tenant-døra krever at INGEN
    tenantkontekst står."""
    conn.rollback()
    rader = conn.execute("SELECT * FROM m26_tilbudskandidater(%s)",
                         (grense,)).fetchall()
    conn.rollback()
    return rader


def policy_har_tilbud(conn, tenant: str) -> bool:
    from db.pg import sett_kontekst
    sett_kontekst(conn, tenant, AKTOR, "tilbud-policy")
    rader = conn.execute(
        "SELECT innhold FROM policyer WHERE tenant=%s AND aktiv",
        (tenant,)).fetchall()
    conn.rollback()
    if len(rader) != 1:
        return False
    innhold = rader[0][0]
    if isinstance(innhold, (str, bytes)):
        innhold = json.loads(innhold)
    return any(h.get("id") == "tilbud.generer"
               for h in (innhold or {}).get("handlinger") or [])


def utlos_en(tjeneste, conn, rad) -> dict:
    from api.bestilling import utfor_bestilling
    from db.pg import sett_kontekst
    from plan.purring import _utfall
    tenant, tilbud_id = rad
    tid = str(tilbud_id)
    rid = f"tilbud-{tid[:8]}"
    nokkel = idempotensnokkel(tid)
    data = {"bestillingstype": "tilbud.generer",
            "tilbud_ref": f"tilbud:{tid}", "omfang": "tilbud"}
    sett_kontekst(conn, tenant, AKTOR, rid)
    res = utfor_bestilling(tjeneste, conn, tenant, AKTOR, data, nokkel, rid)
    utfall, oppdrag_id, unntak_id, detalj = _utfall(res)
    if utfall is None:
        conn.rollback()
        return {"tilbud": tid, "forbigaende": detalj.get("feil")}
    sett_kontekst(conn, tenant, AKTOR, rid)
    ny = conn.execute(
        "SELECT m26_bokfor_tilbudsbestilling(%s,%s,%s,%s,%s,%s,%s,%s::jsonb)",
        (tenant, tilbud_id, nokkel, utfall, oppdrag_id, unntak_id, rid,
         json.dumps(detalj, ensure_ascii=False))).fetchone()[0]
    conn.commit()
    return {"tilbud": tid, "utfall": utfall, "oppdrag_id": oppdrag_id,
            "unntak_id": unntak_id, "bokfort": bool(ny)}


def kjor_en_runde(tjeneste, conn) -> dict:
    if er_av():
        print(json.dumps({"hendelse": "tilbud_utloser_av"}), flush=True)
        return {"av": True, "plukket": 0, "resultater": []}
    rader = kandidater(conn)
    har_policy: dict[str, bool] = {}
    resultater = []
    hoppet_uten_policy = 0
    for rad in rader:
        tenant = rad[0]
        if tenant not in har_policy:
            har_policy[tenant] = policy_har_tilbud(conn, tenant)
            if not har_policy[tenant]:
                print(json.dumps({"hendelse": "tilbud_uten_policy",
                                  "tenant": tenant}), flush=True)
        if not har_policy[tenant]:
            hoppet_uten_policy += 1
            continue
        resultater.append(utlos_en(tjeneste, conn, rad))
    res = {"plukket": len(rader), "uten_policy": hoppet_uten_policy,
           "resultater": resultater}
    if rader:
        print(json.dumps({"hendelse": "tilbud_runde", **res},
                         ensure_ascii=False, default=str), flush=True)
    return res
