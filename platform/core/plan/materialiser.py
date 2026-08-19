"""Plan-materialiseringen (044 §4): systemd-timer hvert 5. minutt.

Arbeideren har INGEN egen autoritet: den claimer vinduets mutexrad,
committer, og kaller så NØYAKTIG samme bestillingsfunksjon som
browserendepunktet (`bestilling.utfor_bestilling`) — policyport,
idempotens, kvote og oppdragsopprettelse er dermed den samme veien for
en planlagt og en menneskelig bestilling. Ingen HTTP-hop: prosessen
kjører i API-ens eget tillitsnivå (m37-presedensen — samme env, samme
nøkkelregistre), og et loopback-kall ville bare lagt en
tokendistribusjon oppå nøyaktig samme kode.

Protokollen per vindu (§4): les mutexraden FOR UPDATE → terminal =
avbryt uten kall; levende lease = hopp over; ellers claim med lease
(2 × HTTP-timeout) og COMMIT FØR bestillingen — låsen beskytter
overgangen, leasen beskytter forsøket. Etter svar: `aktivt → terminal`
og tick i ÉN transaksjon (`terminaliser_planvindu` — eneste tick-skriver,
FK-en er den andre mekanismen).

Idempotensnøkkelen er deterministisk per vindu:
    "plan:" + plan_id + ":" + vindu_start(ISO 8601, UTC)
slik at et gjenspill treffer 038-idempotensen og får `svarkropp` uendret
— ingen ny beslutning, ingen ny kvoteplass.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

import psycopg

#: Hardt tak per kjøring (§4): overskuddet får INGEN rad og vurderes
#: igjen om fem minutter. Kapasitet forsinker, konsumerer aldri.
MAKS_PER_KJORING = int(os.environ.get("DISPONIT_PLAN_MAKS", "50"))

#: Leasen er forsøkets vern: 2 × HTTP-timeout (v1: 2 minutter).
LEASE_S = 120

#: TERMINALE feilkoder fra bestillingsveien → planens pausegrunn
#: (klarsignal §7). Disse er dommer over planen selv, ikke driftsuhell.
_PAUSE_FOR_FEIL = {
    "bestilling_hostname_uverifisert": "policy_stopper",
    "bestillingstype_utilgjengelig": "modul_utilgjengelig",
}


def idempotensnokkel(plan_id, vindu_start: datetime) -> str:
    return f"plan:{plan_id}:{vindu_start.astimezone(timezone.utc).isoformat()}"


def er_forbigaende(kode) -> bool:
    """Er feilkoden et driftsuhell, og ikke en dom over planen? (Codex P1)

    Feilveitabellen er den ENE autoriteten for hva som er drift, og den
    leses her i stedet for å gjentas: en kode rutet til `drift`
    (`db_utilgjengelig`, `logging_feilet`, `intern_feil` …) er
    infrastruktur som svikter. Terminaliserte vi den, ville et minutts
    databasetrøbbel konsumert vinduet PERMANENT og dessuten pauset planen
    som `policy_stopper` — en policy ingen har uttalt, og en pause bare et
    menneske kan oppheve.

    `_PAUSE_FOR_FEIL` går foran tabellen: `bestillingstype_utilgjengelig`
    er drift-rutet for HTTP-klienten, men for planen er den nettopp en dom
    (modulen finnes ikke lenger) med sin egen pausegrunn i §7.
    """
    if kode in _PAUSE_FOR_FEIL:
        return False
    from api.feil import FEIL
    vei = FEIL.get(kode or "")
    return vei is not None and "drift" in vei.routing


def _tick_utfall(res) -> tuple[str | None, int | None, dict]:
    """Bestillingsveiens svar → tick-utfallet (lukket enum).

    `None` er ikke et utfall: det betyr FORBIGÅENDE — intet tick, ingen
    terminalisering, claimet gis tilbake.
    """
    if res[0] == "feil":
        if er_forbigaende(res[1]):
            return None, None, {"feil": res[1]}
        return "stopp", None, {"feil": res[1]}
    kropp = res[1]
    utfall = kropp.get("beslutning")
    if utfall not in ("tillat", "stopp", "brudd"):
        utfall = "stopp"
    return utfall, kropp.get("oppdrag_id"), {
        k: v for k, v in kropp.items() if k in ("beslutning", "begrunnelse",
                                                "unntak_id")}


def materialiser_en(tjeneste, conn, rad, *, naa=None) -> dict:
    """Ett vindu gjennom hele protokollen. -> journalfakta."""
    from api.bestilling import utfor_bestilling
    from db.pg import sett_kontekst

    plan_id, tenant, vindu_start, vindu_slutt, _forfall, btype, parametre = rad
    rid = f"plan-{str(plan_id)[:8]}-{vindu_start:%Y%m%dT%H%M}"
    nokkel = idempotensnokkel(plan_id, vindu_start)

    sett_kontekst(conn, tenant, f"plan:{plan_id}", rid)
    utfall_claim, claim = conn.execute(
        "SELECT utfall, claim_id FROM claim_planvindu(%s,%s,%s,%s)",
        (tenant, plan_id, vindu_start, LEASE_S)).fetchone()
    conn.commit()   # claimet committes FØR kallet — låsen holdes aldri over
    if utfall_claim != "claimet":
        return {"vindu": str(vindu_start), "plan": str(plan_id),
                "hoppet": utfall_claim}

    data = {"bestillingstype": btype, **(parametre or {})}
    res = utfor_bestilling(tjeneste, conn, tenant, f"plan:{plan_id}",
                           data, nokkel, rid)
    utfall, oppdrag_id, detalj = _tick_utfall(res)

    if utfall is None:
        # FORBIGÅENDE (Codex P1): vinduet skal overleve driftsuhellet.
        # Claimet gis tilbake i stedet for å vente ut leasen — vinduet kan
        # ha sekunder igjen — og det skrives verken tick eller pause.
        # Kvoten er urørt: kjernens egen idempotens gjør neste forsøk på
        # samme vindusnøkkel til et gjenspill, ikke en ny beslutning.
        sett_kontekst(conn, tenant, f"plan:{plan_id}", rid)
        frigitt = conn.execute(
            "SELECT frigi_planvindu(%s,%s,%s,%s)",
            (tenant, plan_id, vindu_start, claim)).fetchone()[0]
        conn.commit()
        return {"vindu": str(vindu_start), "plan": str(plan_id),
                "forbigaende": detalj.get("feil"), "frigitt": frigitt}

    sett_kontekst(conn, tenant, f"plan:{plan_id}", rid)
    dom = conn.execute(
        "SELECT terminaliser_planvindu(%s,%s,%s,%s,%s,%s,%s,%s)",
        (tenant, plan_id, vindu_start, claim, nokkel, utfall, oppdrag_id,
         json.dumps(detalj, ensure_ascii=False))).fetchone()[0]
    # Et `avvik:`-svar har alt ført sin egen sikkerhetshendelse i samme
    # transaksjon (terminaliser_planvindu) — mutexen skal gjøre det
    # umulig, og kontrollen står fordi den beviser det.

    # Pausereglene som følger DIREKTE av tick-utfallet (§7). `brudd`
    # pauser ALDRI — kvoten er ikke brukt, vinduet åpner igjen.
    #
    # INGEN COMMIT MELLOM TICKET OG PAUSEN (Codex P1). Den sto her, og
    # gjorde det terminale `stopp`-ticket varig FØR overgangen det krever:
    # døde prosessen i mellomrommet, ble vinduet aldri plukket igjen
    # (terminal er absorberende), og INGEN sveip lette etter `stopp`-tick
    # uten pause. Planen sto altså aktiv etter en policy- eller moduldom
    # og bestilte videre i hvert eneste vindu — stikk i strid med §7.
    # Ticket og overgangen er nå ÉN transaksjon: enten begge, eller ingen
    # av dem. Låserekkefølgen holder — terminaliseringen tar vindusraden,
    # pausen planraden, aldri motsatt vei.
    #
    # `sett_kontekst` er SET LOCAL og gjelder derfor fortsatt her; det var
    # nettopp commiten som tok den med seg før.
    #
    # Ruller transaksjonen likevel tilbake, står vinduet `aktivt` med en
    # lease som dør, og klassifisereren gjenoppretter ticket fra
    # `bestilling_idempotens` — uten pausen. DEN veien tar
    # `planer_med_ubehandlet_stopp` i pausesveipen.
    if utfall == "stopp":
        grunn = _PAUSE_FOR_FEIL.get(detalj.get("feil"), "policy_stopper")
        pauset = conn.execute(
            "SELECT pause_plan(%s,%s,%s,%s,%s,%s)",
            (tenant, plan_id, grunn, f"plan:{plan_id}", rid,
             json.dumps({"vindu": str(vindu_start),
                         "idempotensnokkel": nokkel} | detalj,
                        ensure_ascii=False))).fetchone()[0]
        conn.commit()
        if pauset:
            print(json.dumps({"hendelse": "plan_pauset", "plan": str(plan_id),
                              "grunn": grunn}), flush=True)
    else:
        conn.commit()
    return {"vindu": str(vindu_start), "plan": str(plan_id),
            "utfall": utfall, "dom": dom}


def pausesveip(conn, *, naa=None) -> list:
    """Pausereglene som krever tilbakeblikk (§7):

    1. Menneskelig avvis: aktive planers `tillat`-tick der oppdraget nå
       står `kansellert` med `menneskelig_avvis` → pause UMIDDELBART,
       første gang. Kolonnen leses via plattformens egen funksjon — samme
       diskriminator som lese-API-et viser kunden.
    2. `gjentatt_uten_resultat`: tre `tillat`-tick på rad i GJELDENDE
       åpne periode uten promotert artefakt.
    3. Gjenoppretting: et `stopp`-tick i gjeldende periode UTEN sin pause.
       Materialisereren skriver de to i én transaksjon, men klassifisererens
       gjenoppretting skriver evidens uten å felle pausedommer — og en
       plan som står aktiv etter en policy- eller moduldom ville bestilt
       videre i hvert eneste vindu.
    """
    from db.pg import sett_kontekst
    pausete = []
    # Gjenopprettingen FØRST: en dom som alt er felt skal virke før
    # tilbakeblikkene bruker et vindu til på den samme planen.
    rader = conn.execute(
        "SELECT plan_id, tenant, vindu_start, idempotensnokkel, detalj"
        "  FROM planer_med_ubehandlet_stopp()").fetchall()
    conn.rollback()
    for plan_id, tenant, vindu_start, nokkel, detalj in rader:
        rid = f"plansveip-{str(plan_id)[:8]}"
        grunn = _PAUSE_FOR_FEIL.get((detalj or {}).get("feil"),
                                    "policy_stopper")
        sett_kontekst(conn, tenant, "plansveip", rid)
        if conn.execute(
                "SELECT pause_plan(%s,%s,%s,'plansveip',%s,%s)",
                (tenant, plan_id, grunn, rid,
                 json.dumps({"vindu": str(vindu_start),
                             "idempotensnokkel": nokkel,
                             "gjenopprettet": True} | (detalj or {}),
                            ensure_ascii=False))).fetchone()[0]:
            pausete.append((str(plan_id), grunn))
        conn.commit()

    rader = conn.execute(
        "SELECT plan_id, tenant, oppdrag_id"
        "  FROM planer_med_menneskelig_avvis()").fetchall()
    conn.rollback()
    for plan_id, tenant, oppdrag_id in rader:
        rid = f"plansveip-{str(plan_id)[:8]}"
        sett_kontekst(conn, tenant, "plansveip", rid)
        if conn.execute("SELECT pause_plan(%s,%s,'menneskelig_avvis',"
                        "'plansveip',%s,%s)",
                        (tenant, plan_id, rid,
                         json.dumps({"oppdrag_id": oppdrag_id}))
                        ).fetchone()[0]:
            pausete.append((str(plan_id), "menneskelig_avvis"))
        conn.commit()

    # Tre tillat-tick på rad uten promotert artefakt, innenfor åpen
    # periode (gjenopptak nullstiller ved at perioden er ny).
    rader = conn.execute(
        "SELECT plan_id, tenant FROM planer_gjentatt_uten_resultat()"
    ).fetchall()
    conn.rollback()
    for plan_id, tenant in rader:
        rid = f"plansveip-{str(plan_id)[:8]}"
        sett_kontekst(conn, tenant, "plansveip", rid)
        if conn.execute("SELECT pause_plan(%s,%s,'gjentatt_uten_resultat',"
                        "'plansveip',%s,NULL)",
                        (tenant, plan_id, rid)).fetchone()[0]:
            pausete.append((str(plan_id), "gjentatt_uten_resultat"))
        conn.commit()

    # §7 siste rad: tre `brudd` på rad varsles — men pauser ALDRI.
    # Dempingen bor i basen (hendelses-sporet); sveipen er bare budbringer.
    rader = conn.execute(
        "SELECT plan_id, tenant FROM planer_med_gjentatt_brudd()").fetchall()
    conn.rollback()
    for plan_id, tenant in rader:
        rid = f"plansveip-{str(plan_id)[:8]}"
        sett_kontekst(conn, tenant, "plansveip", rid)
        if conn.execute("SELECT varsle_plan_brudd(%s,%s,'plansveip',%s)",
                        (tenant, plan_id, rid)).fetchone()[0]:
            print(json.dumps({"hendelse": "plan_gjentatt_brudd",
                              "plan": str(plan_id)}), flush=True)
        conn.commit()
    return pausete


def kjor_en_runde(tjeneste, conn) -> dict:
    from db.pg import sett_kontekst
    sett_kontekst(conn, "__plan_sveip__", "plansveip", "plansveip")
    forfalte = conn.execute(
        "SELECT plan_id, tenant, vindu_start, vindu_slutt, forfall,"
        "       bestillingstype, parametre FROM forfalte_planvinduer(%s)",
        (MAKS_PER_KJORING,)).fetchall()
    conn.commit()   # vindusradene materialiseres av plukket
    resultater = [materialiser_en(tjeneste, conn, rad) for rad in forfalte]
    # Klassifisereren i samme sveip: utløpte vinduer får sin terminale
    # dom (hoppet_over eller idempotens-fasit) — aldri innhenting.
    from plan.klassifiser import klassifiser_vinduer
    klassifisering = klassifiser_vinduer(conn)
    pausete = pausesveip(conn)
    res = {"plukket": len(forfalte), "pausete": pausete,
           "resultater": resultater, "klassifisering": klassifisering}
    if forfalte or pausete:
        print(json.dumps({"hendelse": "plan_runde", **res},
                         ensure_ascii=False, default=str), flush=True)
    return res


def main() -> int:
    from api.app import Tjeneste
    from db.hemmeligheter import last_credentials
    last_credentials()           # PR-009: LoadCredential før env-lesing
    dsn = os.environ.get("DISPONIT_DATABASE_URL") \
        or os.environ.get("DATABASE_URL")
    if not dsn:
        print(json.dumps({"hendelse": "plan_oppstart_nektet",
                          "grunn": "DISPONIT_DATABASE_URL mangler"}),
              flush=True)
        return 1
    tjeneste = Tjeneste(dsn)
    conn = tjeneste.pool.hent()
    try:
        kjor_en_runde(tjeneste, conn)
    finally:
        tjeneste.pool.gi_tilbake(conn)
    return 0


if __name__ == "__main__":
    sys.exit(main())
