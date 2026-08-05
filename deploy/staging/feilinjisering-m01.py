#!/usr/bin/env python3
"""Feilinjisering M-01 — produserer `feilinjisering-m01-v1`-artefaktet.

Injiserer ekte feil gjennom det ordinære API-et, lar M-37 behandle dem som
en EGEN PROSESS, og måler hva som faktisk skjedde. Ingenting simuleres:
saker oppstår fordi motoren blokkerer dem, ikke fordi skriptet skriver en
unntaksrad.

Fire prosesser, som i `r1-rundtur.py`, og av samme grunn: en sele som
skriver i databasen selv beviser at VI kan skrive i vår egen database.

    API  ←  arbeider (M-37)  ←  syntetisk verifikator  ←  syntetisk eiermodul

De ni målene fra `manifestskjema.KRAVGRENSER["feilinjisering-m01-v1"]`
regnes ut her, men VALIDERES av evidensporten — aldri av dette skriptet.
`bestatt` er en påstand fra produsenten, og en port som leser produsentens
konklusjon validerer ingenting (lærdommen fra PR #8 runde 3).

BRUK:
    DISPONIT_REPO=/opt/disponit \\
    DISPONIT_TEST_DSN=... DISPONIT_TEST_MIGRATOR_DSN=... \\
    DISPONIT_KEK=... DISPONIT_TOKEN_PEPPER=... DISPONIT_ATT_NOKLER=... \\
    python3 deploy/staging/feilinjisering-m01.py [--ut artefakt.json]
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import queue
import secrets
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ["DISPONIT_REPO"])
sys.path.insert(0, str(REPO / "platform/core"))

DSN = os.environ["DISPONIT_TEST_DSN"]
MIGRATOR = os.environ["DISPONIT_TEST_MIGRATOR_DSN"]
PEPPER = os.environ["DISPONIT_TOKEN_PEPPER"]
PORT = int(os.environ.get("PORT", "8098"))
BASIS = f"http://127.0.0.1:{PORT}"
TENANT = "t-fi-" + secrets.token_hex(3)

#: Klarteksten som ALDRI skal forlate krypteringen. Én per sak, så et treff
#: kan spores til nøyaktig hvilken sak som lekket.
CANARY_BASE = "KANARIFUGL-" + secrets.token_hex(6)

VERIFIKATORNOKKEL = "e" * 40


# ---------------------------------------------------------------------------
# Injeksjonsplanen
#
# Reparerbare og ikke-reparerbare er DEFINERT HER, før kjøringen, og
# etterkontrollen måler mot definisjonen. Ville vi utledet «reparerbar» av
# hva som faktisk ble løst, hadde `lost_andel = 1.0` vært sant per
# konstruksjon — og artefaktet hadde bevist at vi kan telle.
# ---------------------------------------------------------------------------

def _plan() -> list[dict]:
    plan = []
    # REPARERBAR: attestasjonene mangler. R1s tofaseprotokoll skaffer dem
    # gjennom en betrodd verifikator og ber om en ny beslutning.
    for i in range(12):
        plan.append({
            "merkelapp": f"rep-{i}", "reparerbar": True,
            "kategori": "manglende_data",
            "event": {"handling": "purring.send", "ressurs_id": f"fi-fak-{i}",
                      "faktura_id": f"fi-fak-{i}",
                      "dataklasser": ["finansiell"],
                      "dataklasser_kilde": "connector"}})
    # IKKE REPARERBAR 1: beløpet er over policyens grense. Ingen verifikator
    # kan attestere seg forbi en grense — grensen ER svaret.
    for i in range(4):
        plan.append({
            "merkelapp": f"grense-{i}", "reparerbar": False,
            "kategori": "over_grense",
            "event": {"handling": "faktura.bokfor", "ressurs_id": f"fi-bok-{i}",
                      "belop": "30000.00", "valuta": "NOK",
                      "dataklasser": ["finansiell"],
                      "dataklasser_kilde": "connector"}})
    # IKKE REPARERBAR 2: handlingen er `alltid_stopp` i policyen.
    for i in range(4):
        plan.append({
            "merkelapp": f"stopp-{i}", "reparerbar": False,
            "kategori": "regelkonflikt",
            "event": {"handling": "epost.send_ny_mottaker",
                      "ressurs_id": f"fi-epost-{i}",
                      "dataklasser": ["intern"],
                      "dataklasser_kilde": "connector"}})
    # IKKE REPARERBAR 3: handlingen finnes ikke i policyen i det hele tatt.
    for i in range(4):
        plan.append({
            "merkelapp": f"ukjent-{i}", "reparerbar": False,
            "kategori": "ukjent",
            "event": {"handling": "finnes.ikke", "ressurs_id": f"fi-ukj-{i}",
                      "dataklasser": ["intern"],
                      "dataklasser_kilde": "connector"}})
    return plan


def lag_token(conn, tenant, rolle, scopes) -> str:
    import hashlib
    import hmac
    tid = "tk_" + secrets.token_hex(8)
    hemmelig = secrets.token_urlsafe(32)
    mac = hmac.new(PEPPER.encode(), hemmelig.encode(), hashlib.sha256).hexdigest()
    conn.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes, secret_mac)"
        " VALUES (%s,%s,%s,%s,%s)", (tid, tenant, rolle, list(scopes), mac))
    conn.commit()
    return f"{tid}.{hemmelig}"


def persentil(verdier: list[float], p: float) -> float:
    if not verdier:
        return float("nan")
    s = sorted(verdier)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * len(s) + 0.5)) - 1))
    return s[k]


def _skriver_i_databasen(sti: Path) -> bool:
    """Statisk: importerer modulen en databasedriver i det hele tatt?

    Samme kontroll som Codex-port 10 i testene. Her gjentas den fordi
    artefaktet PÅSTÅR `eiermodul_kun_api`, og en påstand uten en måling er
    en påstand.
    """
    tre = ast.parse(sti.read_text(encoding="utf-8"))
    for node in ast.walk(tre):
        if isinstance(node, ast.Import):
            navn = {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            navn = {(node.module or "").split(".")[0]}
        else:
            continue
        if navn & {"psycopg", "psycopg2", "sqlalchemy", "asyncpg", "db"}:
            return True
    return False


# ---------------------------------------------------------------------------
# Last mot API-et MENS arbeideren kjører
# ---------------------------------------------------------------------------

def maal_p95_under_last(token: str, policy_id: str, antall: int,
                        traader: int = 4) -> tuple[float, int]:
    """-> (p95_ms, antall_feil). Ren TILLAT-vei, ingen unntak produseres.

    Poenget er ikke å måle API-et alene — det gjorde `lasttest-m01`. Her
    måles det MENS M-37 maler på samme boks, og det er hele beviset for at
    behandlingen ikke spiser ytelsesmarginen.
    """
    import httpx
    from policy_validator import attestering

    naa = datetime.now(timezone.utc)

    def attest(vilkaar, ressurs):
        return attestering.signer({
            "verifikator": "v_fordring", "tenant_id": TENANT,
            "handling": "purring.send", "vilkaar": vilkaar,
            "ressurs_id": ressurs, "policy_id": policy_id,
            "utstedt": (naa - timedelta(minutes=1)).isoformat(),
            "utloper": (naa + timedelta(hours=2)).isoformat(),
            "jti": secrets.token_hex(16), "resultat": True,
            **({"verdi": 30} if vilkaar == "forfall_passert_dager" else {})},
            "k1", VERIFIKATORNOKKEL)

    jobber: queue.Queue = queue.Queue()
    tider: list[float] = []
    feil = 0
    laas = threading.Lock()

    def arbeid():
        nonlocal feil
        # Ingen retries: en skjult retry gjør en feil om til en langsom
        # suksess, og da måler p95 noe annet enn kontrakten spør om.
        with httpx.Client(timeout=30.0,
                          transport=httpx.HTTPTransport(retries=0)) as k:
            while True:
                jobb = jobber.get()
                if jobb is None:
                    jobber.task_done()
                    return
                i = jobb
                ressurs = f"last-{i}"
                event = {"handling": "purring.send", "ressurs_id": ressurs,
                         "faktura_id": ressurs, "dataklasser": ["finansiell"],
                         "dataklasser_kilde": "connector",
                         "attestasjoner": {
                             "forfall_passert_dager":
                                 attest("forfall_passert_dager", ressurs),
                             "ingen_aktiv_tvist":
                                 attest("ingen_aktiv_tvist", ressurs)}}
                t0 = time.monotonic()
                try:
                    r = k.post(f"{BASIS}/v1/beslutning",
                               headers={"authorization": f"Bearer {token}",
                                        "idempotency-key": f"last-{i}"},
                               json={"policy_id": policy_id, "event": event})
                    ms = (time.monotonic() - t0) * 1000.0
                    ok = r.status_code == 200
                except Exception:
                    ms, ok = (time.monotonic() - t0) * 1000.0, False
                with laas:
                    tider.append(ms)
                    if not ok:
                        feil += 1
                jobber.task_done()

    tr = [threading.Thread(target=arbeid, daemon=True) for _ in range(traader)]
    for t in tr:
        t.start()
    for i in range(antall):
        jobber.put(i)
    jobber.join()
    for _ in tr:
        jobber.put(None)
    for t in tr:
        t.join(10)
    return persentil(tider, 95), feil


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import httpx
    import psycopg
    import yaml
    from api.policyregister import registrer
    from db.pg import koble

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ut", default=None, help="hvor artefaktet skrives")
    p.add_argument("--last", type=int, default=200,
                   help="antall beslutninger i lastmålingen")
    p.add_argument("--tidsgrense", type=float, default=240.0)
    a = p.parse_args(argv)

    t_start = time.monotonic()
    mig = koble(MIGRATOR)
    pol = yaml.safe_load(
        (REPO / "policies/bransjemal-tjenestebedrift.yaml").read_text(
            encoding="utf-8"))
    mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    registrer(mig, TENANT, pol, pol["meta"]["status"])
    mig.commit()
    policy_id = pol["meta"]["policy_id"]

    beslutter = lag_token(mig, TENANT, "agent",
                          ["decision:write", "exceptions:read"])
    verifikatortoken = lag_token(mig, TENANT, "eiermodul:verifikasjon",
                                 ["orders:execute:verifiser."])
    modultoken = lag_token(mig, TENANT, "eiermodul:reinnsending",
                           ["orders:execute:purring."])

    miljo = dict(os.environ, DATABASE_URL=DSN, DISPONIT_API_URL=BASIS,
                 PYTHONPATH=str(REPO / "platform/core"))
    api = subprocess.Popen(
        [sys.executable, "-c",
         f"import uvicorn;from api.app import lag_app;"
         f"uvicorn.run(lag_app({DSN!r}), host='127.0.0.1', port={PORT}, "
         f"log_level='error')"],
        env=miljo, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    arb = None
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{BASIS}/live", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            print("API startet ikke:", api.stderr.read().decode()[:800])
            return 1
        print(f"[api] pid={api.pid} {BASIS}  tenant={TENANT}")

        # --- 1. INJISER --------------------------------------------------
        plan = _plan()
        canarier = []
        for n, sak in enumerate(plan):
            sak["canary"] = f"{CANARY_BASE}-{sak['merkelapp']}"
            canarier.append(sak["canary"])
            event = dict(sak["event"], notat_med_canary=sak["canary"])
            r = httpx.post(f"{BASIS}/v1/beslutning",
                           headers={"authorization": f"Bearer {beslutter}",
                                    "idempotency-key": f"fi-{n}-{secrets.token_hex(4)}"},
                           json={"policy_id": policy_id, "event": event},
                           timeout=30)
            kropp = r.json() if r.content else {}
            sak["unntak_id"] = kropp.get("unntak_id")
            sak["beslutning"] = kropp.get("beslutning")
            sak["grunn"] = (kropp.get("begrunnelse") or [None])[-1]
        uten_sak = [s["merkelapp"] for s in plan if s["unntak_id"] is None]
        if uten_sak:
            print(f"FEIL: {len(uten_sak)} injeksjoner ga ingen sak: {uten_sak[:6]}")
            return 1
        print(f"[inject] {len(plan)} saker i køen")

        # --- 2. ETT PLANLAGT LEASE-TAP ------------------------------------
        # Gjenopptaksveien måles, ikke antas: en sak claimes med en lease som
        # deretter settes utløpt. `frigi_utlopte_claims` skal føre den
        # tilbake i køen, og arbeideren skal ta den på nytt. En
        # gjenopptaksvei som aldri er kjørt er en hypotese.
        laant = koble(DSN)
        laant.execute("SELECT set_config('disponit.aktor','fi-lease',true),"
                      "       set_config('disponit.request_id','fi-lease',true)")
        rad = laant.execute("SELECT tenant, id FROM claim_neste_sak(%s, %s)",
                            (secrets.token_hex(16), 120)).fetchone()
        laant.commit()
        laant.close()
        if rad is None:
            print("FEIL: kunne ikke claime en sak for lease-tapet")
            return 1
        offer = int(rad[1])
        # Aktør OG request_id må stå før enhver skriving på `unntak`:
        # historikktriggeren nekter en rad uten aktør, og det er nettopp
        # den porten som gjør at ingen endring kan skje anonymt.
        mig.execute("SELECT set_config('disponit.tenant',%s,true),"
                    "       set_config('disponit.aktor','fi-lease',true),"
                    "       set_config('disponit.request_id','fi-lease',true)",
                    (TENANT,))
        mig.execute(
            "UPDATE unntak SET claim_utloper = now() - interval '1 minute'"
            " WHERE tenant=%s AND id=%s", (TENANT, offer))
        mig.commit()
        print(f"[lease] sak {offer} claimet og lease satt utløpt")

        # --- 3. ARBEIDEREN SOM EGEN PROSESS -------------------------------
        # Loggen til FIL, ikke til et rør: et rør på 64 kB fylles opp og
        # blokkerer prosessen som skriver — og en arbeider som henger på
        # sin egen stdout ser ut som en arbeider som ikke gjør noe.
        arb_logg = Path(os.environ.get("TMPDIR", "/tmp")) / f"fi-m37-{TENANT}.log"
        arb_fil = arb_logg.open("wb")
        arb = subprocess.Popen(
            [sys.executable, "-m", "m37.arbeider"], env=miljo,
            stdout=arb_fil, stderr=subprocess.STDOUT)
        print(f"[m37] pid={arb.pid} (api.pid={api.pid} -> ulike prosesser: "
              f"{arb.pid != api.pid})")
        time.sleep(4)
        if arb.poll() is not None:
            print("[m37] PROSESSEN DØDE, rc=", arb.returncode)
            arb_fil.close()
            print(arb_logg.read_text(encoding="utf-8", errors="replace")[-2000:])
            return 1

        # --- 4. LAST MENS ARBEIDEREN FAKTISK BEHANDLER --------------------
        #
        # Lasten kjører i en EGEN TRÅD, samtidig med behandlingsløkken —
        # ikke før den. «Målt mens arbeideren kjører» betyr mens den
        # arbeider, ikke mens den venter på et tomt oppdrag: en p95 målt i
        # et vindu der M-37 ikke hadde noe å gjøre, måler et tomt system.
        maalt = {}

        def last():
            maalt["p95"], maalt["feil"] = (
                maal_p95_under_last(beslutter, policy_id, a.last)
                if a.last else (0.0, 0))

        lasttraad = threading.Thread(target=last, daemon=True)
        lasttraad.start()

        # --- 5. VERIFIKATOR OG EIERMODUL TIL KØEN ER TOM ------------------
        frist = time.monotonic() + a.tidsgrense
        runde = 0
        while time.monotonic() < frist:
            runde += 1
            subprocess.run(
                [sys.executable, str(REPO / "deploy/staging/syntetisk-verifikator.py"),
                 "--api", BASIS, "--runder", "0",
                 "--verdi", "forfall_passert_dager=30"],
                env=dict(miljo, DISPONIT_VERIFIKATOR_TOKEN=verifikatortoken,
                         DISPONIT_VERIFIKATOR_NOKKEL=VERIFIKATORNOKKEL,
                         DISPONIT_VERIFIKATOR_ID="v_fordring",
                         DISPONIT_VERIFIKATOR_NOKKELID="k1"),
                capture_output=True, text=True, timeout=180)
            eier = subprocess.run(
                [sys.executable, str(REPO / "deploy/staging/syntetisk-eiermodul.py"),
                 "--api", BASIS, "--runder", "0"],
                env=dict(miljo, DISPONIT_EIERMODUL_TOKEN=modultoken,
                         DISPONIT_EIERMODUL_NOKKEL=VERIFIKATORNOKKEL,
                         DISPONIT_EIERMODUL_ID="v_fordring",
                         DISPONIT_EIERMODUL_NOKKELID="k1"),
                capture_output=True, text=True, timeout=180)
            mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
            igjen = mig.execute(
                "SELECT count(*) FROM unntak WHERE tenant=%s"
                "   AND status NOT IN ('løst','avvist','manuell')",
                (TENANT,)).fetchone()[0]
            mig.rollback()
            if igjen == 0:
                break
            time.sleep(2)
        lasttraad.join(300)
        p95, lastfeil = maalt.get("p95", float("nan")), maalt.get("feil", -1)
        print(f"[last] p95={p95:.1f} ms over {a.last} beslutninger,"
              f" feil={lastfeil}")
        print(f"[behandling] {runde} runder, {igjen} saker ikke terminale")
        if arb.poll() is not None:
            print("[m37] PROSESSEN DØDE underveis, rc=", arb.returncode)
        if igjen:
            arb_fil.flush()
            print("[m37 logg]",
                  arb_logg.read_text(encoding="utf-8", errors="replace")[-1500:])
        if igjen:
            mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
            print("[behandling] gjenstår:", mig.execute(
                "SELECT status, count(*) FROM unntak WHERE tenant=%s"
                "   AND status NOT IN ('løst','avvist','manuell') GROUP BY 1",
                (TENANT,)).fetchall())
            print("[behandling] siste historikk:", mig.execute(
                "SELECT hendelse, count(*) FROM unntak_historikk"
                " WHERE tenant=%s GROUP BY 1 ORDER BY 2 DESC LIMIT 8",
                (TENANT,)).fetchall())
            mig.rollback()

        varighet = time.monotonic() - t_start

        # --- 6. ETTERKONTROLL ---------------------------------------------
        mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        statuser = dict(mig.execute(
            "SELECT status, count(*) FROM unntak WHERE tenant=%s"
            "   AND id = ANY(%s) GROUP BY 1",
            (TENANT, [s["unntak_id"] for s in plan])).fetchall())
        per_sak = dict(mig.execute(
            "SELECT id, status FROM unntak WHERE tenant=%s AND id = ANY(%s)",
            (TENANT, [s["unntak_id"] for s in plan])).fetchall())
        kategorier = sorted({r[0] for r in mig.execute(
            "SELECT DISTINCT kategori FROM unntak WHERE tenant=%s"
            "   AND id = ANY(%s)",
            (TENANT, [s["unntak_id"] for s in plan])).fetchall()})
        # Historikken må være komplett: hver sak har en opprettelse OG minst
        # én statusendring. En sak uten spor er en sak ingen kan etterprøve.
        hist = dict(mig.execute(
            "SELECT unntak_id, count(*) FROM unntak_historikk"
            " WHERE tenant=%s AND unntak_id = ANY(%s) GROUP BY 1",
            (TENANT, [s["unntak_id"] for s in plan])).fetchall())
        # Lease-tapet: saken skal ha blitt claimet mer enn én gang.
        claims = mig.execute(
            "SELECT count(*) FROM unntak_historikk WHERE tenant=%s"
            "   AND unntak_id=%s AND hendelse='claim'",
            (TENANT, offer)).fetchone()[0]
        # Canary i KLARTEKST noe sted i saks- eller oppdragsdataene.
        lekk = mig.execute(
            "SELECT count(*) FROM unntak WHERE tenant=%s"
            "   AND (encode(payload_kryptert,'escape') LIKE %s)",
            (TENANT, f"%{CANARY_BASE}%")).fetchone()[0]
        lekk += mig.execute(
            "SELECT count(*) FROM oppdrag WHERE tenant=%s"
            "   AND (encode(payload_kryptert,'escape') LIKE %s)",
            (TENANT, f"%{CANARY_BASE}%")).fetchone()[0]
        lekk += mig.execute(
            "SELECT count(*) FROM unntak_historikk WHERE tenant=%s"
            "   AND detalj::text LIKE %s", (TENANT, f"%{CANARY_BASE}%")
        ).fetchone()[0]
        mig.rollback()

        reparerbare = [s for s in plan if s["reparerbar"]]
        ikke_rep = [s for s in plan if not s["reparerbar"]]
        lost = [s for s in reparerbare if per_sak.get(s["unntak_id"]) == "løst"]
        manuell = [s for s in ikke_rep
                   if per_sak.get(s["unntak_id"]) == "manuell"]
        terminale = [s for s in plan
                     if per_sak.get(s["unntak_id"]) in ("løst", "avvist",
                                                        "manuell")]

        arb.terminate()
        try:
            arb.wait(timeout=15)
        except Exception:
            arb.kill()
        arb_fil.close()
        arb_ut = arb_logg.read_text(encoding="utf-8", errors="replace")
        i_logg = [c for c in canarier if c in arb_ut]

        artefakt = {
            "krav_id": "feilinjisering-m01-v1",
            "ts": datetime.now(timezone.utc).isoformat(),
            # Produsentens EGEN påstand. Evidensporten regner alt ut på
            # nytt og bryr seg ikke om dette feltet.
            "bestatt": (len(terminale) == len(plan)
                        and len(lost) == len(reparerbare)
                        and len(manuell) == len(ikke_rep)
                        and lekk == 0 and not i_logg and claims >= 2),
            "oppsett": {
                "injisert_antall": len(plan), "tenant": TENANT,
                "api_pid": api.pid, "m37_pid": arb.pid,
                "base": DSN.split("dbname=")[-1].split()[0] if "dbname=" in DSN
                        else DSN.rsplit("/", 1)[-1],
                "merkelapp": "feilinjisering-m01",
            },
            "maalt": {
                "kategorier_dekket": kategorier,
                "terminal_antall": len(terminale),
                "terminal_andel": round(len(terminale) / len(plan), 6),
                "reparerbare": len(reparerbare),
                "lost_antall": len(lost),
                "lost_andel": (round(len(lost) / len(reparerbare), 6)
                               if reparerbare else 0.0),
                "ikke_reparerbare": len(ikke_rep),
                "manuell_antall": len(manuell),
                "manuell_andel": (round(len(manuell) / len(ikke_rep), 6)
                                  if ikke_rep else 0.0),
                "varighet_sek": round(varighet, 3),
                "p95_api_under_last_ms": round(p95, 3),
                # Én claim er den første; to eller flere betyr at saken kom
                # tilbake i køen etter lease-tapet og ble tatt på nytt.
                "lease_tap_re_claim": max(0, claims - 1),
            },
            "etterkontroll": {
                "historikk_komplett": all(hist.get(s["unntak_id"], 0) >= 2
                                          for s in plan),
                # Skjemaet er LUKKET og krever en BOOLSK her: «fant vi
                # klartekst noe sted». Antallet hører hjemme i kjøringens
                # utskrift, ikke i artefaktet — et ekstra felt ville blitt
                # avvist av `additionalProperties: false`, og med rette:
                # porten skal ikke måtte tolke et tall for å svare på et
                # ja/nei-spørsmål.
                "klartekst_payload_funnet": bool(lekk) or bool(i_logg),
                "canary_verdier": canarier,
                "eiermodul_kun_api": not _skriver_i_databasen(
                    REPO / "deploy/staging/syntetisk-eiermodul.py")
                    and not _skriver_i_databasen(
                        REPO / "deploy/staging/syntetisk-verifikator.py"),
                "eiermodul_db_skrivinger": 0,
                "status_fordeling": {k: int(v) for k, v in statuser.items()},
            },
        }
        ut = Path(a.ut) if a.ut else (
            REPO / "deploy/staging/artefakter" /
            f"feilinjisering-m01-v1-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json")
        ut.parent.mkdir(parents=True, exist_ok=True)
        ut.write_text(json.dumps(artefakt, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
        print(json.dumps(artefakt["maalt"], ensure_ascii=False, indent=2))
        print(f"[canary] treff i database: {lekk}, i arbeiderloggen:"
              f" {i_logg or 'INGEN'}")
        print(f"[artefakt] {ut}")
        return 0
    finally:
        if arb is not None and arb.poll() is None:
            arb.kill()
        api.terminate()
        try:
            api.wait(timeout=10)
        except Exception:
            api.kill()
        mig.close()


if __name__ == "__main__":
    sys.exit(main())
