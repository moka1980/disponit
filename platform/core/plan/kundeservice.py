"""M-17 svarutløseren (ARC B kundeservice, PR 3): registeret bestiller
når et menneske har godkjent — policyen avgjør.

Et utkast blir `godkjent` av et menneske (160). Utløseren er leddet
mellom godkjenningen og bestillingen: den plukker hvert godkjent utkast
på en åpen henvendelse med adresse og svarvei (162 `m17_svarkandidater`),
og bestiller `kundeservice.svar.send` gjennom NØYAKTIG samme
bestillingsvei som et menneske (`utfor_bestilling`, 044 §4-grepet — som
purrings- og kampanjeutløseren). Ingen egen autoritet: policyport,
idempotens, kvote, godkjennings- og DLP-attestasjon og
oppdragsopprettelse er den samme veien.

Tre vern rundt bestillingen:
  * ÉN BESTILLING PER UTKAST. Idempotensnøkkelen er deterministisk
    (`svar:<henvendelse_id>:<utkast_id>`), og utfallet bokføres i
    `svarbestilling` FØR neste runde kan se utkastet igjen. Ble det
    brudd (et fødselsnummer i teksten, et løfte), er saken i unntakskøen
    et menneskes — utløseren prøver ikke igjen; et rettet utkast er en
    ny rad og en ny kandidat når det godkjennes.
  * POLICYEN MÅ NEVNE HANDLINGEN. En tenant uten `kundeservice.svar.send`
    i sin aktive policy får utkast som i dag, og ingenting bestilles —
    uten å brenne en beslutning på et «ukjent_handling».
  * KILL-SWITCH: `DISPONIT_SVAR_UTLOSER=av` i planarbeiderens konfig
    stopper hele utløseren uten deploy. Utkastene står.

Forbigående feil og plattformtilstand bokføres IKKE: utkastet er
kandidat igjen neste runde. En dom (`henvendelse_ukjent`) bokføres som
`feil:<kode>`.
"""
from __future__ import annotations

import json
import os

#: Hardt tak PER TENANT per runde: overskuddet vurderes neste runde.
MAKS_PER_TENANT = int(os.environ.get("DISPONIT_SVAR_MAKS", "50"))

AKTOR = "agent:kundeservice"


def er_av() -> bool:
    return os.environ.get("DISPONIT_SVAR_UTLOSER", "").strip().lower() \
        in ("av", "0", "false", "nei")


def idempotensnokkel(henvendelse_id, utkast_id) -> str:
    return f"svar:{henvendelse_id}:{utkast_id}"


def kandidater(conn, grense: int = MAKS_PER_TENANT) -> list:
    """-> [(tenant, henvendelse_id, utkast_id)].
    Kryss-tenant-døra krever at INGEN tenantkontekst står."""
    conn.rollback()
    rader = conn.execute("SELECT * FROM m17_svarkandidater(%s)",
                         (grense,)).fetchall()
    conn.rollback()
    return rader


def policy_har_svar(conn, tenant: str) -> bool:
    """Har tenantens aktive policy handlingen `kundeservice.svar.send`? Én aktiv
    policy er bestillingsveiens eget krav (`policy_ukjent` ellers)."""
    from db.pg import sett_kontekst
    sett_kontekst(conn, tenant, AKTOR, "svar-policy")
    rader = conn.execute(
        "SELECT innhold FROM policyer WHERE tenant=%s AND aktiv",
        (tenant,)).fetchall()
    conn.rollback()
    if len(rader) != 1:
        return False
    innhold = rader[0][0]
    if isinstance(innhold, (str, bytes)):
        innhold = json.loads(innhold)
    return any(h.get("id") == "kundeservice.svar.send"
               for h in (innhold or {}).get("handlinger") or [])


def utlos_en(tjeneste, conn, rad) -> dict:
    """Én kandidat gjennom bestillingsveien, og utfallet bokført.
    Utfallstolkningen er purringsutløserens (`plan.purring._utfall`)."""
    from api.bestilling import utfor_bestilling
    from db.pg import sett_kontekst
    from plan.purring import _utfall
    tenant, henvendelse_id, utkast_id = rad
    hid, uid = str(henvendelse_id), str(utkast_id)
    rid = f"svar-{hid[:8]}-{uid[:8]}"
    nokkel = idempotensnokkel(hid, uid)
    data = {"bestillingstype": "kundeservice.svar.send",
            "henvendelse_ref": f"henvendelse:{hid}",
            "utkast_ref": f"utkast:{uid}", "omfang": "svar"}
    sett_kontekst(conn, tenant, AKTOR, rid)
    res = utfor_bestilling(tjeneste, conn, tenant, AKTOR, data, nokkel, rid)
    utfall, oppdrag_id, unntak_id, detalj = _utfall(res)
    if utfall is None:
        conn.rollback()
        return {"henvendelse": hid, "utkast": uid,
                "forbigaende": detalj.get("feil")}
    sett_kontekst(conn, tenant, AKTOR, rid)
    ny = conn.execute(
        "SELECT m17_bokfor_svarbestilling(%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s::jsonb)",
        (tenant, henvendelse_id, utkast_id, nokkel, utfall, oppdrag_id,
         unntak_id, rid, json.dumps(detalj, ensure_ascii=False))
    ).fetchone()[0]
    conn.commit()
    return {"henvendelse": hid, "utkast": uid, "utfall": utfall,
            "oppdrag_id": oppdrag_id, "unntak_id": unntak_id,
            "bokfort": bool(ny)}


def kjor_en_runde(tjeneste, conn) -> dict:
    if er_av():
        print(json.dumps({"hendelse": "svar_utloser_av"}), flush=True)
        return {"av": True, "plukket": 0, "resultater": []}
    rader = kandidater(conn)
    har_policy: dict[str, bool] = {}
    resultater = []
    hoppet_uten_policy = 0
    for rad in rader:
        tenant = rad[0]
        if tenant not in har_policy:
            har_policy[tenant] = policy_har_svar(conn, tenant)
            if not har_policy[tenant]:
                print(json.dumps({"hendelse": "svar_uten_policy",
                                  "tenant": tenant}), flush=True)
        if not har_policy[tenant]:
            hoppet_uten_policy += 1
            continue
        resultater.append(utlos_en(tjeneste, conn, rad))
    res = {"plukket": len(rader), "uten_policy": hoppet_uten_policy,
           "resultater": resultater}
    if rader:
        print(json.dumps({"hendelse": "svar_runde", **res},
                         ensure_ascii=False, default=str), flush=True)
    return res
