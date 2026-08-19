"""Terminal klassifisering av utløpte planvinduer (044 §5).

KLASSIFISERINGEN KAN IKKE BESTILLE — modulen har ingen HTTP-klient og
importerer aldri bestillingsveien (håndhevet med statisk AST-test).
Den feller kun dommer over vinduer som ALT er avgjort av virkeligheten:

  * Finnes en rad i `bestilling_idempotens` på vinduets nøkkel, BLE det
    bestilt — utfallet hentes derfra (`beslutning` + `oppdrag_id` fra
    `svarkropp`), aldri `hoppet_over`. Idempotenstabellen er immutabel
    inkludert DELETE; det er nettopp den egenskapen som gjør den brukbar
    som fasit for «ble det bestilt?».
  * Ingen rad + `now() >= vindu_slutt` + intet levende forsøk →
    `hoppet_over`. Missede vinduer tas ALDRI igjen: en WCAG-rapport for
    tirsdag er verdiløs på torsdag (motsatt av revalideringen i 015, og
    med vilje).

Tilbakeblikket er maks 30 døgn og aldri før planens tidligste `fra_ts`;
lengre nedetid gir ÉN aggregert hendelse, ikke tusen rader.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import timedelta

TILBAKEBLIKK_DOGN = 30


def _nokkel(plan_id, vindu_start) -> str:
    # Samme avledning som materialiseringen — men uten å importere den:
    # klassifisereren skal ikke kunne nå bestillingsveien engang
    # transitivt (materialiser importerer api.bestilling).
    from datetime import timezone
    return f"plan:{plan_id}:{vindu_start.astimezone(timezone.utc).isoformat()}"


def klassifiser_vinduer(conn, *, grense: int = 200) -> dict:
    """Utløpte, uterminaliserte vinduer → terminal + tick. -> journalfakta."""
    from db.pg import sett_kontekst

    res = {"hoppet_over": 0, "fra_idempotens": 0, "avvik": []}
    # Materialiser radene for utløpte vinduer som ALDRI fikk et forsøk
    # (nedetid, port 33/14): uten en rad kan ingen dom felles. Radene
    # fødes `ledig` og felles i løkken under som alle andre.
    sett_kontekst(conn, "__plan_sveip__", "planklassifisering", "planklass")
    conn.execute("SELECT count(*) FROM utlopte_planvinduer(%s, %s)",
                 (TILBAKEBLIKK_DOGN, grense))
    conn.commit()
    # Port 7 gjelder også klassifisereren: utvalget leses gjennom
    # claimerens definer, aldri av bordet.
    rader = conn.execute(
        "SELECT plan_id, tenant, vindu_start, tilstand, lease_utloper"
        "  FROM planvinduer_til_klassifisering(%s, %s)",
        (TILBAKEBLIKK_DOGN, grense)).fetchall()
    conn.rollback()

    for plan_id, tenant, vindu_start, tilstand, lease in rader:
        rid = f"planklass-{str(plan_id)[:8]}"
        sett_kontekst(conn, tenant, "planklassifisering", rid)
        nokkel = _nokkel(plan_id, vindu_start)
        # Fasiten først: ble det bestilt?
        fasit = conn.execute(
            "SELECT beslutning, oppdrag_id, svarkropp"
            "  FROM bestilling_idempotens"
            " WHERE tenant=%s AND idempotensnokkel=%s",
            (tenant, nokkel)).fetchone()
        if fasit is not None:
            utfall = fasit[0]
            oppdrag = fasit[1] if fasit[1] is not None else (
                (fasit[2] or {}).get("oppdrag_id"))
            detalj = {"kilde": "bestilling_idempotens"}
        else:
            utfall, oppdrag = "hoppet_over", None
            detalj = {"kilde": "utlopt_vindu"}
        try:
            dom = conn.execute(
                "SELECT terminaliser_planvindu(%s,%s,%s,NULL,%s,%s,%s,%s)",
                (tenant, plan_id, vindu_start, nokkel, utfall, oppdrag,
                 json.dumps(detalj, ensure_ascii=False))).fetchone()[0]
        except Exception as e:
            # «Levende forsøk eier vinduet» (§5, port 44/51): klassifisereren
            # skriver INGENTING mens en materialisering pågår — vinduet
            # ender med det faktiske utfallet i stedet.
            conn.rollback()
            res.setdefault("ventet", []).append(
                {"plan": str(plan_id), "grunn": type(e).__name__})
            continue
        conn.commit()
        if dom.startswith("avvik:"):
            res["avvik"].append({"plan": str(plan_id),
                                 "vindu": str(vindu_start),
                                 "ventet": utfall, "fant": dom[6:]})
        elif fasit is not None:
            res["fra_idempotens"] += 1
        else:
            res["hoppet_over"] += 1

    # Aggregert nedetidshendelse: vinduer ELDRE enn tilbakeblikket får
    # aldri rader — men planen får ÉN hendelse som sier det.
    gamle = conn.execute(
        "SELECT plan_id, tenant, fra, til, antall, vinduer"
        "  FROM plan_nedetid_kandidater(%s)",
        (TILBAKEBLIKK_DOGN,)).fetchall()
    conn.rollback()
    for plan_id, tenant, fra, til, antall, vinduer in gamle:
        rid = f"planklass-{str(plan_id)[:8]}"
        sett_kontekst(conn, tenant, "planklassifisering", rid)
        conn.execute("SELECT plan_nedetid_aggregert(%s,%s,%s,%s,%s,"
                     "'planklassifisering',%s)",
                     (tenant, plan_id, fra, til, antall, rid))
        # Vinduene terminaliseres som hoppet_over uten enkelthendelser —
        # aggregatet er sporet.
        for vs in vinduer:
            conn.execute(
                "SELECT terminaliser_planvindu(%s,%s,%s,NULL,%s,"
                "'hoppet_over',NULL,%s)",
                (tenant, plan_id, vs, _nokkel(plan_id, vs),
                 json.dumps({"kilde": "nedetid_aggregert"})))
        conn.commit()
        res.setdefault("aggregert", []).append(
            {"plan": str(plan_id), "vinduer": antall})

    if any(res.get(k) for k in ("hoppet_over", "fra_idempotens", "avvik",
                                "aggregert")):
        print(json.dumps({"hendelse": "plan_klassifisering", **res},
                         ensure_ascii=False, default=str), flush=True)
    return res


def main() -> int:
    from db.pg import koble
    from db.hemmeligheter import last_credentials
    last_credentials()           # PR-009: LoadCredential før env-lesing
    dsn = os.environ.get("DISPONIT_DATABASE_URL") \
        or os.environ.get("DATABASE_URL")
    if not dsn:
        print(json.dumps({"hendelse": "planklass_oppstart_nektet",
                          "grunn": "DISPONIT_DATABASE_URL mangler"}),
              flush=True)
        return 1
    conn = koble(dsn)
    try:
        klassifiser_vinduer(conn)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
