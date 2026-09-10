"""M-14 bokføringsutløseren (ARC B bokføring, PR 2): registeret bestiller
når kontrollene er rene — policyen avgjør.

En inngående faktura får tre kontroller ved registreringen (106:
dublett, mva, leverandør). Utløseren er leddet mellom kontrollen og
bokføringen: den plukker hver faktura med rene kontroller (166
`m14_bokforingskandidater`) og bestiller `faktura.bokfor` eller
`faktura.bokfor_stor` gjennom NØYAKTIG samme bestillingsvei som et
menneske (`utfor_bestilling`, 044 §4-grepet — som purrings-, kampanje-
og svarutløseren). Ingen egen autoritet: policyport, idempotens, kvote,
attestasjonene av kontrollradene og oppdragsopprettelse er den samme
veien.

HANDLINGEN VELGES AV POLICYENS GRENSER, IKKE AV REGISTERET. Bransjemalen
bærer to bokføringshandlinger med hver sin `belop_maks`; runden leser
dem fra tenantens aktive policy og bestiller den første fakturaen
ligger under. Ligger den over begge, bokføres `menneske_kreves` — ingen
beslutning brennes, flaten viser «over policyens tak», og et menneske
avgjør den som før.

Tre vern rundt bestillingen:
  * ÉN BESTILLING PER FAKTURA. Idempotensnøkkelen er deterministisk
    (`bokforing:<faktura_id>`), og utfallet bokføres i
    `bokforingsbestilling` FØR neste runde kan se fakturaen igjen. Ble
    det brudd, er saken i unntakskøen et menneskes.
  * POLICYEN MÅ NEVNE HANDLINGEN. En tenant uten noen av de to i sin
    aktive policy får fakturaer som i dag, og ingenting bestilles.
  * KILL-SWITCH: `DISPONIT_BOKFORING_UTLOSER=av` i planarbeiderens
    konfig stopper hele utløseren uten deploy. Fakturaene står.

Forbigående feil og plattformtilstand bokføres IKKE: fakturaen er
kandidat igjen neste runde. En dom (`faktura_ukjent`,
`faktura_ikke_klar_for_bokforing`) bokføres som `feil:<kode>`.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation

#: Hardt tak PER TENANT per runde: overskuddet vurderes neste runde.
MAKS_PER_TENANT = int(os.environ.get("DISPONIT_BOKFORING_MAKS", "50"))

AKTOR = "agent:faktura"

HANDLINGER = ("faktura.bokfor", "faktura.bokfor_stor")


def er_av() -> bool:
    return os.environ.get("DISPONIT_BOKFORING_UTLOSER", "").strip().lower() \
        in ("av", "0", "false", "nei")


def idempotensnokkel(faktura_id) -> str:
    return f"bokforing:{faktura_id}"


def kandidater(conn, grense: int = MAKS_PER_TENANT) -> list:
    """-> [(tenant, faktura_id, brutto_ore, valuta)].
    Kryss-tenant-døra krever at INGEN tenantkontekst står."""
    conn.rollback()
    rader = conn.execute("SELECT * FROM m14_bokforingskandidater(%s)",
                         (grense,)).fetchall()
    conn.rollback()
    return rader


def policygrenser(conn, tenant: str) -> dict | None:
    """Tenantens aktive policy → {handling: (belop_maks_ore, valutaer)}
    for de bokføringshandlingene den nevner — eller None uten noen av
    dem. Én aktiv policy er bestillingsveiens eget krav."""
    from db.pg import sett_kontekst
    sett_kontekst(conn, tenant, AKTOR, "bokforing-policy")
    rader = conn.execute(
        "SELECT innhold FROM policyer WHERE tenant=%s AND aktiv",
        (tenant,)).fetchall()
    conn.rollback()
    if len(rader) != 1:
        return None
    innhold = rader[0][0]
    if isinstance(innhold, (str, bytes)):
        innhold = json.loads(innhold)
    ut: dict = {}
    for h in (innhold or {}).get("handlinger") or []:
        if h.get("id") not in HANDLINGER:
            continue
        g = h.get("grenser") or {}
        if g.get("belop_maks") is None:
            # Ingen grense uttalt = ingen grense (None sier det eksplisitt).
            maks = None
        else:
            try:
                maks = int(Decimal(str(g["belop_maks"])) * 100)
            except (InvalidOperation, ValueError, TypeError):
                # En grense som ikke kan leses er IKKE «ingen grense»
                # (CodeRabbit): handlingen holdes utenfor til policyen
                # er rettet — en stille åpning er nettopp feilen.
                print(json.dumps({"hendelse": "bokforing_grense_uleselig",
                                  "tenant": tenant, "handling": h["id"]}),
                      flush=True)
                continue
        ut[h["id"]] = (maks, tuple(g.get("valuta") or ()))
    return ut or None


def velg_handling(grenser: dict, brutto_ore: int, valuta: str) -> str | None:
    """Den første handlingen (liten før stor) beløpet ligger under, i
    policyens valuta — eller None når fakturaen ligger over begge."""
    for navn in HANDLINGER:
        if navn not in grenser:
            continue
        maks, valutaer = grenser[navn]
        if valutaer and valuta not in valutaer:
            continue
        if maks is None or int(brutto_ore) <= maks:
            return navn
    return None


def utlos_en(tjeneste, conn, rad, grenser: dict) -> dict:
    """Én kandidat gjennom bestillingsveien, og utfallet bokført.
    Utfallstolkningen er purringsutløserens (`plan.purring._utfall`)."""
    from api.bestilling import utfor_bestilling
    from db.pg import sett_kontekst
    from plan.purring import _utfall
    tenant, faktura_id, brutto_ore, valuta = rad
    fid = str(faktura_id)
    rid = f"bokforing-{fid[:8]}"
    nokkel = idempotensnokkel(fid)
    handling = velg_handling(grenser, int(brutto_ore), valuta)
    sett_kontekst(conn, tenant, AKTOR, rid)
    if handling is None:
        # Over begge grensene: ingen beslutning brennes; raden sier
        # «over policyens tak» til flaten og til neste runde.
        detalj = {"brutto_ore": int(brutto_ore), "valuta": valuta,
                  "grenser": {k: v[0] for k, v in grenser.items()}}
        ny = conn.execute(
            "SELECT m14_bokfor_bokforingsbestilling(%s,%s,%s,'ingen',"
            "'menneske_kreves',NULL,NULL,%s,%s::jsonb)",
            (tenant, faktura_id, nokkel, rid,
             json.dumps(detalj, ensure_ascii=False))).fetchone()[0]
        conn.commit()
        return {"faktura": fid, "utfall": "menneske_kreves",
                "bokfort": bool(ny)}
    data = {"bestillingstype": handling,
            "faktura_ref": f"faktura:{fid}", "omfang": "bilag"}
    res = utfor_bestilling(tjeneste, conn, tenant, AKTOR, data, nokkel, rid)
    utfall, oppdrag_id, unntak_id, detalj = _utfall(res)
    if utfall is None:
        conn.rollback()
        return {"faktura": fid, "handling": handling,
                "forbigaende": detalj.get("feil")}
    sett_kontekst(conn, tenant, AKTOR, rid)
    ny = conn.execute(
        "SELECT m14_bokfor_bokforingsbestilling(%s,%s,%s,%s,%s,%s,%s,%s,"
        "%s::jsonb)",
        (tenant, faktura_id, nokkel, handling, utfall, oppdrag_id,
         unntak_id, rid, json.dumps(detalj, ensure_ascii=False))
    ).fetchone()[0]
    conn.commit()
    return {"faktura": fid, "handling": handling, "utfall": utfall,
            "oppdrag_id": oppdrag_id, "unntak_id": unntak_id,
            "bokfort": bool(ny)}


def kjor_en_runde(tjeneste, conn) -> dict:
    if er_av():
        print(json.dumps({"hendelse": "bokforing_utloser_av"}), flush=True)
        return {"av": True, "plukket": 0, "resultater": []}
    rader = kandidater(conn)
    grenser: dict[str, dict | None] = {}
    resultater = []
    hoppet_uten_policy = 0
    for rad in rader:
        tenant = rad[0]
        if tenant not in grenser:
            grenser[tenant] = policygrenser(conn, tenant)
            if grenser[tenant] is None:
                print(json.dumps({"hendelse": "bokforing_uten_policy",
                                  "tenant": tenant}), flush=True)
        if grenser[tenant] is None:
            hoppet_uten_policy += 1
            continue
        resultater.append(utlos_en(tjeneste, conn, rad, grenser[tenant]))
    res = {"plukket": len(rader), "uten_policy": hoppet_uten_policy,
           "resultater": resultater}
    if rader:
        print(json.dumps({"hendelse": "bokforing_runde", **res},
                         ensure_ascii=False, default=str), flush=True)
    return res
