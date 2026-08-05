#!/usr/bin/env python3
"""Rollback M-01 — produserer `rollback-m01-v1`-artefaktet.

Deaktiverer beslutningsmodulen MENS trafikk pågår, måler hvor lang tid det
tar før avvisningen er effektiv, at hver forespørsel i vinduet får det
DEFINERTE svaret (503 `modul_inaktiv`) og ikke en tilfeldig 500 eller en
hengende forbindelse, og at ingenting halvferdig blir liggende igjen.

Den bindende delen er ikke tidene. Det er `halvferdige_transaksjoner = 0`
og at radtellingene for de andre tabellene er uendret: en rollback som er
rask og etterlater en halv transaksjon er verre enn en treg som ikke gjør
det.

TO TALL SOM MÅLER TO FORSKJELLIGE TING, med vilje:

  `deaktivering_effektiv_s`  — tiden fra kommandoen gis til avvisningen
                               virker. Restartvinduet der porten er lukket
                               er INKLUDERT. Det er nettopp den tiden
                               driften trenger å vite.
  `paagaaende_requests_korrekt_avvist` — av forespørslene som fikk et HTTP-
                               SVAR mens modulen var av: andelen som fikk
                               `modul_inaktiv`. En lukket port gir ikke et
                               galt svar; den gir ikke noe svar, og den
                               kostnaden står i tiden over.

BRUK:
    DISPONIT_REPO=/opt/disponit DISPONIT_TEST_DSN=... \\
    DISPONIT_TEST_MIGRATOR_DSN=... DISPONIT_KEK=... \\
    DISPONIT_TOKEN_PEPPER=... DISPONIT_ATT_NOKLER=... \\
    python3 deploy/staging/rollback-m01.py [--ut artefakt.json]
"""
from __future__ import annotations

import argparse
import json
import os
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
PORT = int(os.environ.get("PORT", "8097"))
BASIS = f"http://127.0.0.1:{PORT}"
TENANT = "t-rb-" + secrets.token_hex(3)
MODUL = "m01_policy"

#: Tabeller en rollback ALDRI skal røre. Tellingene sammenlignes før og
#: etter, og evidensporten regner dem ut på nytt — flagget
#: `andre_tabeller_uendret` er bare en påstand.
UROERTE = ("revisjonslogg", "unntak", "unntak_historikk", "oppdrag",
           "reparasjonsoperasjoner", "idempotens", "policyer",
           "tenant_nokler")


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


def start_api(inaktiv: bool):
    miljo = dict(os.environ, DATABASE_URL=DSN,
                 PYTHONPATH=str(REPO / "platform/core"))
    if inaktiv:
        miljo["DISPONIT_INAKTIVE_MODULER"] = MODUL
    else:
        miljo.pop("DISPONIT_INAKTIVE_MODULER", None)
    return subprocess.Popen(
        [sys.executable, "-c",
         f"import uvicorn;from api.app import lag_app;"
         f"uvicorn.run(lag_app({DSN!r}), host='127.0.0.1', port={PORT}, "
         f"log_level='error')"],
        env=miljo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def radtelling(conn) -> dict:
    conn.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    ut = {}
    for tabell in UROERTE:
        ut[tabell] = int(conn.execute(
            f"SELECT count(*) FROM {tabell}").fetchone()[0])
    conn.rollback()
    return ut


def main(argv=None) -> int:
    import httpx
    import yaml
    from api.policyregister import registrer
    from db.pg import koble
    from policy_validator import attestering

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--ut", default=None)
    p.add_argument("--varighet", type=float, default=12.0,
                   help="sekunder trafikk rundt rollbacken")
    a = p.parse_args(argv)

    reg = attestering.last_nokler()
    nokkel = (reg.get("v_fordring") or {}).get("k1")
    if not isinstance(nokkel, str) or len(nokkel) < 32:
        print("AVBRUTT: v_fordring/k1 mangler i nøkkelregisteret",
              file=sys.stderr)
        return 2

    mig = koble(MIGRATOR)
    pol = yaml.safe_load(
        (REPO / "policies/bransjemal-tjenestebedrift.yaml").read_text(
            encoding="utf-8"))
    mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
    registrer(mig, TENANT, pol, pol["meta"]["status"])
    mig.commit()
    policy_id = pol["meta"]["policy_id"]
    token = lag_token(mig, TENANT, "agent",
                      ["decision:write", "exceptions:read"])

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
            "k1", nokkel)

    #: Delt tilstand mellom trafikktråden og hovedtråden.
    tilstand = {"fase": "aktiv"}
    svar: list[dict] = []
    stopp = threading.Event()

    def trafikk():
        """Sender jevnt, og merker HVER forespørsel med fasen den ble sendt i.

        Merkingen skjer på SENDETIDSPUNKTET, ikke i etterkant: en
        forespørsel sendt mens modulen var av, men som svarer etter at den
        er på igjen, hører til av-vinduet. Sorterte vi etter svartidspunkt,
        ville de vanskeligste tilfellene havnet i feil bøtte.
        """
        i = 0
        with httpx.Client(timeout=10.0,
                          transport=httpx.HTTPTransport(retries=0)) as k:
            while not stopp.is_set():
                i += 1
                ressurs = f"rb-{i}"
                fase = tilstand["fase"]
                event = {"handling": "purring.send", "ressurs_id": ressurs,
                         "faktura_id": ressurs, "dataklasser": ["finansiell"],
                         "dataklasser_kilde": "connector",
                         "attestasjoner": {
                             "forfall_passert_dager":
                                 attest("forfall_passert_dager", ressurs),
                             "ingen_aktiv_tvist":
                                 attest("ingen_aktiv_tvist", ressurs)}}
                post = {"fase": fase, "i": i}
                try:
                    r = k.post(f"{BASIS}/v1/beslutning",
                               headers={"authorization": f"Bearer {token}",
                                        "idempotency-key": f"rb-{i}"},
                               json={"policy_id": policy_id, "event": event})
                    post["status"] = r.status_code
                    try:
                        post["feil"] = (r.json() or {}).get("feil")
                    except Exception:
                        post["feil"] = None
                except Exception as e:
                    # Ingen HTTP-svar i det hele tatt: porten var lukket.
                    post["status"] = None
                    post["feil"] = type(e).__name__
                svar.append(post)
                time.sleep(0.05)

    api = start_api(inaktiv=False)
    tr = None
    try:
        for _ in range(60):
            try:
                if httpx.get(f"{BASIS}/live", timeout=2).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            print("API startet ikke")
            return 1
        print(f"[api] pid={api.pid} {BASIS}  tenant={TENANT}")

        tr = threading.Thread(target=trafikk, daemon=True)
        tr.start()
        time.sleep(a.varighet / 3)

        # --- DEAKTIVER ----------------------------------------------------
        # `overgang`, ikke `av`: kommandoen er gitt, men modulen er ennå
        # PÅ. En forespørsel som treffer den gamle prosessen før den er nede
        # får et normalt 200 — og det er ikke en feilaktig avvisning, det er
        # riktig svar fra en modul som fortsatt var aktiv. Kostnaden ved
        # overgangen står i `deaktivering_effektiv_s`, som inkluderer hele
        # vinduet. Målte vi andelen fra kommandotidspunktet, ville tallet
        # blandet «svarte feil» med «rakk å svare riktig først».
        tilstand["fase"] = "overgang"
        t0 = time.monotonic()
        api.terminate()
        try:
            api.wait(timeout=10)
        except Exception:
            api.kill()
        api = start_api(inaktiv=True)
        for _ in range(600):
            try:
                r = httpx.post(f"{BASIS}/v1/beslutning",
                               headers={"authorization": f"Bearer {token}",
                                        "idempotency-key": "probe-av"},
                               json={"policy_id": policy_id, "event": {}},
                               timeout=3)
                if r.status_code == 503 and (r.json() or {}).get("feil") == "modul_inaktiv":
                    break
            except Exception:
                pass
            time.sleep(0.05)
        else:
            print("modulen ble aldri effektivt deaktivert")
            return 1
        deaktivering = time.monotonic() - t0
        tilstand["fase"] = "av"          # fra og med NÅ er modulen beviselig av
        # Radtellingen tas HER, ikke ved start: trafikken skriver
        # legitimt til revisjonsloggen så lenge modulen er PÅ. Kravet er at
        # AV-VINDUET ikke rører noe — det er den perioden der en halv
        # transaksjon eller en tapt loggpost ville oppstått.
        for_ = radtelling(mig)
        print(f"[rollback] deaktivering effektiv etter {deaktivering:.2f} s")
        time.sleep(a.varighet / 3)

        # --- REAKTIVER ----------------------------------------------------
        etter = radtelling(mig)          # siste øyeblikk der modulen er AV
        t1 = time.monotonic()
        tilstand["fase"] = "overgang2"
        api.terminate()
        try:
            api.wait(timeout=10)
        except Exception:
            api.kill()
        api = start_api(inaktiv=False)
        for _ in range(600):
            try:
                r = httpx.post(f"{BASIS}/v1/beslutning",
                               headers={"authorization": f"Bearer {token}",
                                        "idempotency-key": "probe-paa"},
                               json={"policy_id": policy_id,
                                     "event": {"handling": "purring.send",
                                               "ressurs_id": "probe"}},
                               timeout=3)
                if r.status_code != 503:
                    break
            except Exception:
                pass
            time.sleep(0.05)
        else:
            print("modulen kom aldri tilbake")
            return 1
        reaktivering = time.monotonic() - t1
        tilstand["fase"] = "paa"
        print(f"[rollback] reaktivering effektiv etter {reaktivering:.2f} s")
        time.sleep(a.varighet / 3)

        stopp.set()
        tr.join(20)

        # --- ETTERKONTROLL -------------------------------------------------
        # NEVNEREN ER ALLE forespørslene i av-vinduet (Codex P1, runde 6).
        #
        # Den var tidligere `med_svar` — de som fikk et HTTP-svar. Da
        # forsvant en lukket forbindelse ut av regnestykket i stedet for å
        # telle som feil, og andelen kunne bli 1,0 mens halve trafikken
        # falt på gulvet. En manglende avvisning ER en manglende avvisning,
        # uansett om den skyldes feil kode eller ingen kode.
        i_av = [s for s in svar if s["fase"] == "av"]
        med_svar = [s for s in i_av if s["status"] is not None]
        korrekt = [s for s in i_av
                   if s["status"] == 503 and s["feil"] == "modul_inaktiv"]
        andel = (len(korrekt) / len(i_av)) if i_av else 0.0

        # TAPTE LOGGPOSTER: hver forespørsel som fikk et AUDITERT svar (200)
        # skal ha nøyaktig én revisjonsrad. Idempotensnøkkelen er unik per
        # forespørsel, så tellingen er en 1:1-sjekk, ikke et estimat.
        auditerte = [s for s in svar if s["status"] == 200]
        mig.execute("SELECT set_config('disponit.tenant',%s,true)", (TENANT,))
        logg = int(mig.execute(
            "SELECT count(*) FROM revisjonslogg WHERE tenant=%s"
            "   AND idempotency_key LIKE 'rb-%%'", (TENANT,)).fetchone()[0])
        # HALVFERDIG: en idempotensrad som står igjen på `paagaar` er en
        # forespørsel som rakk å reservere seg og så forsvant. Det er
        # nøyaktig tilstanden `modul_inaktiv`-porten står FØR tilkoblingen
        # hentes for å hindre — avvisningen skjer før noe er reservert.
        halvferdige = int(mig.execute(
            "SELECT count(*) FROM idempotens WHERE tenant=%s"
            "   AND status = 'paagaar'", (TENANT,)).fetchone()[0])
        mig.rollback()
        tapte = max(0, len(auditerte) - logg)

        artefakt = {
            "krav_id": "rollback-m01-v1",
            "ts": datetime.now(timezone.utc).isoformat(),
            "bestatt": (tapte == 0 and halvferdige == 0 and andel >= 1.0
                        and for_ == etter),
            "oppsett": {
                "modul": MODUL, "tenant": TENANT,
                "requests_under_rollback": len(i_av),
                "merkelapp": "rollback-m01",
                "base": DSN.split("dbname=")[-1].split()[0] if "dbname=" in DSN
                        else DSN.rsplit("/", 1)[-1],
            },
            "maalt": {
                "deaktivering_effektiv_s": round(deaktivering, 3),
                "reaktivering_effektiv_s": round(reaktivering, 3),
                "tapte_loggposter": tapte,
                "avviste_requests": len(korrekt),
                "paagaaende_requests_korrekt_avvist": round(andel, 6),
                "halvferdige_transaksjoner": halvferdige,
                "avvisningskode": "modul_inaktiv",
            },
            "etterkontroll": {
                "andre_tabeller_uendret": for_ == etter,
                "radtelling_for": for_,
                "radtelling_etter": etter,
            },
        }
        ut = Path(a.ut) if a.ut else (
            REPO / "deploy/staging/artefakter" /
            f"rollback-m01-v1-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.json")
        ut.parent.mkdir(parents=True, exist_ok=True)
        ut.write_text(json.dumps(artefakt, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
        print(json.dumps(artefakt["maalt"], ensure_ascii=False, indent=2))
        overganger = len([s for s in svar
                          if s["fase"] in ("overgang", "overgang2")])
        print(f"[trafikk] {len(svar)} forespørsler, {len(i_av)} sendt mens"
              f" modulen beviselig var AV ({len(med_svar)} fikk HTTP-svar),"
              f" {overganger} i de to overgangsvinduene")
        print(f"[artefakt] {ut}")
        return 0
    finally:
        stopp.set()
        if tr is not None:
            tr.join(10)
        api.terminate()
        try:
            api.wait(timeout=10)
        except Exception:
            api.kill()
        mig.close()


if __name__ == "__main__":
    sys.exit(main())
