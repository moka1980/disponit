#!/usr/bin/env python3
"""R1-rundtur ende-til-ende: API + arbeider + eiermodul som TRE prosesser.

Beviser hele kjeden: en sak havner i køen -> arbeideren claimer og
klassifiserer -> ber om en NY policystyrt beslutning gjennom API-et ->
legger ut et oppdrag -> eiermodulen plukker det og poster en signert
kvittering -> saken lukkes.

Kjøres mot en lokal base. Er den grønn, virker M-37 faktisk.
"""
import json
import os
import secrets
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROT = Path(__file__).resolve()
REPO = Path(os.environ["DISPONIT_REPO"])
sys.path.insert(0, str(REPO / "platform/core"))

DSN = os.environ["DISPONIT_TEST_DSN"]
MIGRATOR = os.environ["DISPONIT_TEST_MIGRATOR_DSN"]
TENANT = "t-r1-" + secrets.token_hex(3)   # fersk tenant per kjoering
PORT = int(os.environ.get("PORT", "8099"))
BASIS = f"http://127.0.0.1:{PORT}"
PEPPER = os.environ["DISPONIT_TOKEN_PEPPER"]
MODUL = "eiermodul:reinnsending"
CANARY = "KANARIFUGL-" + secrets.token_hex(6)


def lag_token(conn, tenant, rolle, scopes):
    import hashlib, hmac
    tid = "tk_" + secrets.token_hex(8)
    hemmelig = secrets.token_urlsafe(32)
    mac = hmac.new(PEPPER.encode(), hemmelig.encode(), hashlib.sha256).hexdigest()
    conn.execute(
        "INSERT INTO api_tokener (token_id, tenant, rolle, scopes, secret_mac)"
        " VALUES (%s,%s,%s,%s,%s)", (tid, tenant, rolle, list(scopes), mac))
    conn.commit()
    return f"{tid}.{hemmelig}"


def main() -> int:
    import psycopg
    import yaml
    from db.pg import koble
    from api.policyregister import registrer

    mig = koble(MIGRATOR)
    # Ryddig utgangspunkt for tenanten.
    mig.execute("SET LOCAL ROLE disponit_m37_claimer")
    for t in ("arbeidskapabiliteter", "kvitteringskapabiliteter"):
        mig.execute(f"DELETE FROM {t} WHERE tenant=%s", (TENANT,))
    mig.execute("RESET ROLE")
    for tab in ("oppdrag", "reparasjonsoperasjoner", "unntak_historikk",
                "unntak", "revisjonslogg", "idempotens", "policyer",
                "tenant_nokler", "attestasjon_jti", "frekvens_hendelser"):
        if tab in ("unntak_historikk", "unntak", "revisjonslogg", "oppdrag",
                   "reparasjonsoperasjoner", "tenant_nokler"):
            trig = {"unntak_historikk": "historikk_ingen_endring",
                    "unntak": "unntak_ingen_delete",
                    "revisjonslogg": "revisjonslogg_ingen_endring",
                    "oppdrag": "oppdrag_ingen_delete",
                    "reparasjonsoperasjoner": "reparasjon_vakt",
                    "tenant_nokler": "tenant_nokler_ingen_delete"}[tab]
            mig.execute(f"ALTER TABLE {tab} DISABLE TRIGGER {trig}")
        mig.execute("SELECT set_config('disponit.tenant',%s,true),"
                    "       set_config('disponit.aktor','oppsett',true)", (TENANT,))
        mig.execute(f"DELETE FROM {tab} WHERE tenant=%s", (TENANT,))
        if tab in ("unntak_historikk", "unntak", "revisjonslogg", "oppdrag",
                   "reparasjonsoperasjoner", "tenant_nokler"):
            mig.execute(f"ALTER TABLE {tab} ENABLE TRIGGER {trig}")
    mig.execute("DELETE FROM api_tokener WHERE tenant=%s", (TENANT,))
    mig.commit()

    pol = yaml.safe_load(
        (REPO / "policies/bransjemal-tjenestebedrift.yaml").read_text(
            encoding="utf-8"))
    mig.execute("SELECT set_config('disponit.tenant',%s,true),"
                "       set_config('disponit.aktor','oppsett',true)", (TENANT,))
    registrer(mig, TENANT, pol, pol["meta"]["status"])
    mig.commit()

    beslutter = lag_token(mig, TENANT, "agent", ["decision:write",
                                                 "exceptions:read"])
    modultoken = lag_token(mig, TENANT, MODUL,
                           ["orders:execute:purring."])
    verifikatortoken = lag_token(mig, TENANT, "eiermodul:verifikasjon",
                                 ["orders:execute:verifiser."])
    print(f"[oppsett] tenant={TENANT} policy={pol['meta']['policy_id']}")

    miljo = dict(os.environ, DATABASE_URL=DSN, DISPONIT_API_URL=BASIS,
                 PYTHONPATH=str(REPO / "platform/core"))

    api = subprocess.Popen(
        [sys.executable, "-c",
         f"import uvicorn;from api.app import lag_app;"
         f"uvicorn.run(lag_app({DSN!r}), host='127.0.0.1', port={PORT}, "
         f"log_level='error')"],
        env=miljo, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        import httpx
        for _ in range(60):
            try:
                if httpx.get(f"{BASIS}/live", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            print("API startet ikke:", api.stderr.read().decode()[:800])
            return 1
        print(f"[api] pid={api.pid} lytter på {BASIS}")

        # --- 1. Lag en sak i køen: manglende attestasjon => manglende_data
        # ETT vilkår attesteres på forhånd, ETT mangler. Policyen krever
        # begge; da er saken reparerbar med NØYAKTIG én verifikasjonsrunde,
        # som er det tofaseprotokollen er laget for.
        from datetime import timedelta as _td
        from policy_validator import attestering as _att
        _naa = datetime.now(timezone.utc)
        tvist = _att.signer({
            "verifikator": "v_fordring", "tenant_id": TENANT,
            "handling": "purring.send", "vilkaar": "ingen_aktiv_tvist",
            "ressurs_id": "fak-r1", "policy_id": pol["meta"]["policy_id"],
            "utstedt": (_naa - _td(minutes=1)).isoformat(),
            "utloper": (_naa + _td(hours=2)).isoformat(),
            "jti": secrets.token_hex(16), "resultat": True},
            "k1", "e" * 40)
        hendelse = {
            "handling": "purring.send", "ressurs_id": "fak-r1",
            "faktura_id": "fak-r1", "dataklasser": ["finansiell"],
            "dataklasser_kilde": "connector",
            "attestasjoner": {"ingen_aktiv_tvist": tvist},
            "notat_med_canary": CANARY,          # skal ALDRI ut av krypteringen
        }
        r = httpx.post(f"{BASIS}/v1/beslutning",
                       headers={"authorization": f"Bearer {beslutter}",
                                "idempotency-key": "r1-" + secrets.token_hex(4)},
                       json={"policy_id": pol["meta"]["policy_id"],
                             "event": hendelse}, timeout=30)
        print(f"[inject] {r.status_code} {r.json().get('beslutning')} "
              f"unntak_id={r.json().get('unntak_id')} "
              f"begrunnelse={r.json().get('begrunnelse')}")
        if r.json().get("unntak_id") is None:
            print("FEIL: ingen sak havnet i køen")
            return 1

        # --- 2. Arbeideren som EGEN PROSESS
        arb = subprocess.Popen(
            [sys.executable, "-m", "m37.arbeider"], env=miljo,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(f"[m37] pid={arb.pid}  (api.pid={api.pid} -> ulike prosesser: "
              f"{arb.pid != api.pid})")
        time.sleep(6)
        if arb.poll() is not None:
            print("[m37] PROSESSEN DØDE, rc=", arb.returncode)
            print((arb.stderr.read() or b"").decode(errors="replace")[-1500:])
            return 1

        mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        rader = mig.execute(
            "SELECT id, status FROM unntak WHERE tenant=%s ORDER BY id",
            (TENANT,)).fetchall()
        opp = mig.execute(
            "SELECT id, status, eiermodul FROM oppdrag WHERE tenant=%s",
            (TENANT,)).fetchall()
        mig.rollback()
        print(f"[etter arbeider] saker={rader} oppdrag={opp}")

        # --- 2b. VERIFIKATOREN (fase 1) ----------------------------
        ver = subprocess.run(
            [sys.executable, str(REPO / "deploy/staging/syntetisk-verifikator.py"),
             "--api", BASIS, "--runder", "0"],
            env=dict(miljo, DISPONIT_VERIFIKATOR_TOKEN=verifikatortoken,
                     DISPONIT_VERIFIKATOR_NOKKEL="e" * 40,
                     DISPONIT_VERIFIKATOR_ID="v_fordring",
                     DISPONIT_VERIFIKATOR_NOKKELID="k1"),
            capture_output=True, text=True, timeout=120)
        print(f"[verifikator] rc={ver.returncode} ut={ver.stdout.strip()[:300]} "
              f"feil={ver.stderr.strip()[-400:]}")

        mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        print("[etter fase 1] saker=", mig.execute(
            "SELECT id, status FROM unntak WHERE tenant=%s ORDER BY id",
            (TENANT,)).fetchall())
        print("[etter fase 1] generasjoner=", mig.execute(
            "SELECT vilkaar, generation, status, bevis_id IS NOT NULL"
            " FROM verifikasjonsgenerasjon WHERE tenant=%s", (TENANT,)).fetchall())
        mig.rollback()

        # --- 2c. Arbeideren igjen: FASE 2 --------------------------
        time.sleep(6)
        mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        print("[etter fase 2] saker=", mig.execute(
            "SELECT id, status FROM unntak WHERE tenant=%s ORDER BY id",
            (TENANT,)).fetchall())
        print("[etter fase 2] oppdrag=", mig.execute(
            "SELECT id, oppdragstype, status FROM oppdrag WHERE tenant=%s"
            " ORDER BY id", (TENANT,)).fetchall())
        mig.rollback()

        # --- 3. Eiermodulen plukker og kvitterer
        ut = subprocess.run(
            [sys.executable, str(REPO / "deploy/staging/syntetisk-eiermodul.py"),
             "--api", BASIS, "--runder", "0"],
            env=dict(miljo, DISPONIT_EIERMODUL_TOKEN=modultoken,
                     DISPONIT_EIERMODUL_NOKKEL="e" * 40,
                     DISPONIT_EIERMODUL_ID="v_fordring",
                     DISPONIT_EIERMODUL_NOKKELID="k1"),
            capture_output=True, text=True, timeout=120)
        print(f"[eiermodul] rc={ut.returncode} ut={ut.stdout.strip()[:300]} "
              f"feil={ut.stderr.strip()[-300:]}")

        if arb.poll() is not None:
            print("[m37] PROSESSEN DOEDE under fase 2, rc=", arb.returncode)
        arb.terminate()
        arb.wait(timeout=15)

        mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        slutt = mig.execute(
            "SELECT id, status FROM unntak WHERE tenant=%s ORDER BY id",
            (TENANT,)).fetchall()
        oppslutt = mig.execute(
            "SELECT id, status FROM oppdrag WHERE tenant=%s", (TENANT,)).fetchall()
        hist = mig.execute(
            "SELECT hendelse, count(*) FROM unntak_historikk WHERE tenant=%s"
            " GROUP BY 1 ORDER BY 1", (TENANT,)).fetchall()
        mig.rollback()
        print(f"[slutt] saker={slutt}")
        print(f"[slutt] oppdrag={oppslutt}")
        print(f"[slutt] historikk={dict(hist)}")

        # --- 4. Canary: klarteksten skal ALDRI ha forlatt krypteringen
        arb_ut = (arb.stdout.read() or b"").decode(errors="replace")
        arb_feil = (arb.stderr.read() or b"").decode(errors="replace")
        if arb_feil.strip():
            print("[m37 stderr]", arb_feil.strip()[-1200:])
        lekk = [n for n, t in (("arbeider.stdout", arb_ut),
                               ("arbeider.stderr", arb_feil),
                               ("eiermodul.stdout", ut.stdout),
                               ("eiermodul.stderr", ut.stderr))
                if CANARY in t]
        print(f"[canary] {CANARY} funnet i: {lekk or 'INGEN STEDER'}")
        return 0
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except Exception:
            api.kill()
        mig.close()


if __name__ == "__main__":
    sys.exit(main())
