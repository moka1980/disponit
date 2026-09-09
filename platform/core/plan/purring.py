"""M-23 purringsutløseren (ARC B, PR 3): registeret bestiller når trinnet
forfaller — policyen avgjør.

Sveipen (104) finner `trinn_forfalt` og flytter ingenting. Utløseren er
leddet mellom funnet og bestillingen: den plukker fordringer der neste
trinn er forfalt, som er åpne, som HAR en mottaker (146), og som ikke alt
er bestilt for dette trinnet (148) — og bestiller `purring.send` gjennom
NØYAKTIG samme bestillingsvei som et menneske (`utfor_bestilling`, 044
§4-grepet). Ingen egen autoritet: policyport, idempotens, kvote og
oppdragsopprettelse er den samme veien.

Tre vern rundt bestillingen:
  * ÉN BESTILLING PER FORDRING OG TRINN. Idempotensnøkkelen er
    deterministisk (`purring:<fordring_id>:<trinn>`), og utfallet
    bokføres i `purringsbestilling` FØR neste runde kan se fordringen
    igjen. Ble det brudd, er saken i unntakskøen et menneskes — utløseren
    prøver ikke igjen på samme trinn.
  * POLICYEN MÅ NEVNE HANDLINGEN. En tenant uten `purring.send` i sin
    aktive policy får funn som i dag, og ingenting bestilles — uten å
    brenne en beslutning på et «ukjent_handling».
  * KILL-SWITCH: `DISPONIT_PURRING_UTLOSER=av` i planarbeiderens konfig
    stopper hele utløseren uten deploy. Funnene står.

Forbigående feil (drift-rutede koder, opptatte låser) og
plattformtilstand (modulen ikke claimbar ennå, ingen aktiv policy)
bokføres IKKE: fordringen er kandidat igjen neste runde, og kjernens
idempotens gjør et gjentak til gjenspill, aldri ny kvote. Terminale nei
fra målportene (`fordring_ukjent`, `fordring_ikke_klar_for_purring`)
bokføres som `feil:<kode>` — de er dommer over fordringen.
"""
from __future__ import annotations

import json
import os

#: Hardt tak PER TENANT per runde: overskuddet vurderes neste runde.
MAKS_PER_TENANT = int(os.environ.get("DISPONIT_PURRING_MAKS", "50"))

AKTOR = "agent:purring"

#: TRINNHANDLINGENE AGENTEN ALDRI BESTILLER (eiervedtak 9/9, valg 2):
#: inkassovarsel er et menneskes bestilling (policyhandlingen
#: `purring.send.inkassovarsel`, flaten har knappen); inkasso sendes
#: aldri av systemet. Utløseren bokfører «krever et menneske» og lar
#: kandidaten ligge — ingen beslutning brennes, ingen sak fødes.
MENNESKE_KREVES = {"inkassovarsel": "inkassovarsel_krever_menneske",
                   "inkasso": "inkasso_aldri_automatisk"}


def er_av() -> bool:
    return os.environ.get("DISPONIT_PURRING_UTLOSER", "").strip().lower() \
        in ("av", "0", "false", "nei")


def idempotensnokkel(fordring_id, trinn: int) -> str:
    return f"purring:{fordring_id}:{int(trinn)}"


def kandidater(conn, grense: int = MAKS_PER_TENANT) -> list:
    """-> [(tenant, fordring_id, trinn, handling_trinn, dogn_over_forfall)].
    Kryss-tenant-døra krever at INGEN tenantkontekst står."""
    conn.rollback()
    rader = conn.execute("SELECT * FROM m23_purringskandidater(%s)",
                         (grense,)).fetchall()
    conn.rollback()
    return rader


def policy_har_purring(conn, tenant: str) -> bool:
    """Har tenantens aktive policy handlingen `purring.send`? Én aktiv
    policy er bestillingsveiens eget krav (`policy_ukjent` ellers)."""
    from db.pg import sett_kontekst
    sett_kontekst(conn, tenant, AKTOR, "purring-policy")
    rader = conn.execute(
        "SELECT innhold FROM policyer WHERE tenant=%s AND aktiv",
        (tenant,)).fetchall()
    conn.rollback()
    if len(rader) != 1:
        return False
    innhold = rader[0][0]
    if isinstance(innhold, (str, bytes)):
        innhold = json.loads(innhold)
    return any(h.get("id") == "purring.send"
               for h in (innhold or {}).get("handlinger") or [])


#: PLATTFORMTILSTAND, IKKE DOM OVER FORDRINGEN (CodeRabbit på PR 3):
#: modulen er ikke claimbar ennå (registreringsskriptet er ikke kjørt på
#: verten), eller tenanten har ingen aktiv policy akkurat nå. Begge kan
#: rettes uten at fordringen endrer seg — bokføres de som `feil:`, blir
#: trinnet aldri prøvd igjen etter at feilen er rettet.
_PLATTFORMTILSTAND = frozenset({"bestillingstype_utilgjengelig",
                                "policy_ukjent"})


def _utfall(res) -> tuple[str | None, int | None, int | None, dict]:
    """Bestillingsveiens svar → (utfall, oppdrag_id, unntak_id, detalj).
    `None` som utfall = FORBIGÅENDE: ingen bokføring."""
    from plan.materialiser import er_forbigaende
    if res[0] == "feil":
        if res[1] in _PLATTFORMTILSTAND or er_forbigaende(res[1]):
            return None, None, None, {"feil": res[1]}
        return "feil:" + str(res[1]), None, None, {"feil": res[1]}
    kropp = res[1]
    utfall = kropp.get("beslutning")
    if utfall not in ("tillat", "stopp", "brudd"):
        utfall = "stopp"
    return utfall, kropp.get("oppdrag_id"), kropp.get("unntak_id"), {
        k: v for k, v in kropp.items()
        if k in ("beslutning", "begrunnelse", "unntak_id")}


def utlos_en(tjeneste, conn, rad) -> dict:
    """Én kandidat gjennom bestillingsveien, og utfallet bokført."""
    from api.bestilling import utfor_bestilling
    from db.pg import sett_kontekst
    tenant, fordring_id, trinn, handling_trinn, _dogn = rad
    fid = str(fordring_id)
    rid = f"purring-{fid[:8]}-{int(trinn)}"
    nokkel = idempotensnokkel(fid, trinn)
    data = {"bestillingstype": "purring.send",
            "fordring_ref": f"fordring:{fid}", "omfang": "trinn"}
    sett_kontekst(conn, tenant, AKTOR, rid)
    res = utfor_bestilling(tjeneste, conn, tenant, AKTOR, data, nokkel, rid)
    utfall, oppdrag_id, unntak_id, detalj = _utfall(res)
    if utfall is None:
        conn.rollback()
        return {"fordring": fid, "trinn": int(trinn),
                "forbigaende": detalj.get("feil")}
    sett_kontekst(conn, tenant, AKTOR, rid)
    ny = conn.execute(
        "SELECT m23_bokfor_purringsbestilling(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s::jsonb)",
        (tenant, fordring_id, int(trinn), handling_trinn, nokkel, utfall,
         oppdrag_id, unntak_id, rid,
         json.dumps(detalj, ensure_ascii=False))).fetchone()[0]
    conn.commit()
    return {"fordring": fid, "trinn": int(trinn), "utfall": utfall,
            "oppdrag_id": oppdrag_id, "unntak_id": unntak_id,
            "bokfort": bool(ny)}


def bokfor_menneske_kreves(conn, rad) -> dict:
    """Kandidaten er et trinn agenten ikke tar. Bokføres én gang per
    fordring og trinn, med grunnen — så flaten kan si det, og runden
    ikke ser den igjen."""
    from db.pg import sett_kontekst
    tenant, fordring_id, trinn, handling_trinn, _dogn = rad
    fid = str(fordring_id)
    rid = f"purring-{fid[:8]}-{int(trinn)}"
    sett_kontekst(conn, tenant, AKTOR, rid)
    ny = conn.execute(
        "SELECT m23_bokfor_purringsbestilling(%s,%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s::jsonb)",
        (tenant, fordring_id, int(trinn), handling_trinn,
         idempotensnokkel(fid, trinn), "menneske_kreves", None, None, rid,
         json.dumps({"grunn": MENNESKE_KREVES[handling_trinn]}))).fetchone()[0]
    conn.commit()
    return {"fordring": fid, "trinn": int(trinn), "utfall": "menneske_kreves",
            "grunn": MENNESKE_KREVES[handling_trinn], "bokfort": bool(ny)}


def kjor_en_runde(tjeneste, conn) -> dict:
    if er_av():
        print(json.dumps({"hendelse": "purring_utloser_av"}), flush=True)
        return {"av": True, "plukket": 0, "resultater": []}
    rader = kandidater(conn)
    har_policy: dict[str, bool] = {}
    resultater = []
    hoppet_uten_policy = 0
    for rad in rader:
        tenant = rad[0]
        if tenant not in har_policy:
            har_policy[tenant] = policy_har_purring(conn, tenant)
            if not har_policy[tenant]:
                print(json.dumps({"hendelse": "purring_uten_policy",
                                  "tenant": tenant}), flush=True)
        if not har_policy[tenant]:
            hoppet_uten_policy += 1
            continue
        if rad[3] in MENNESKE_KREVES:
            resultater.append(bokfor_menneske_kreves(conn, rad))
            continue
        resultater.append(utlos_en(tjeneste, conn, rad))
    res = {"plukket": len(rader), "uten_policy": hoppet_uten_policy,
           "resultater": resultater}
    if rader:
        print(json.dumps({"hendelse": "purring_runde", **res},
                         ensure_ascii=False, default=str), flush=True)
    return res
