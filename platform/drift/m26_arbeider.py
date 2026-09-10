"""M-26-arbeideren: claimer tilbud.generer-oppdrag over API-et med
modultokenet fra onboarding, sender tilbudet gjennom husets SMTP, og
kvitterer signert. M-23-arbeiderens form — senderen ER M-23s
(`lag_sender`): husets avsenderadresse, tenantens avsendernavn.

TILLITSSNITTET: prosessen har modultoken + kvitteringsnøkkel + SMTP-
oppsett — men INGEN DB-tilgang, ingen KEK, ingen tenant-nøkler. Adressen,
linjene og klausulene kommer i claim-svaret (172) og lever i minnet så lenge
sendingen varer.

Miljø (EnvironmentFile /etc/disponit/m26/konfig + LoadCredential):
  DISPONIT_API_URL           plattformen (standard http://127.0.0.1:8099)
  DISPONIT_MODULTOKEN        mtk_-tokenet fra onboarding (035)
  DISPONIT_KVITTERINGSNOKKEL sti til JSON {verifikator, nokkel_id,
                             hemmelighet} — tilbudets er `v_prisbok`
  DISPONIT_SMTP_VERT/PORT/BRUKER/PASSORD/AVSENDER  husets SMTP
  DISPONIT_M26_POLL_S        pause mellom tomme claim (standard 10)
"""
from __future__ import annotations

import json
import os
import sys
import time

from drift.m23_arbeider import lag_sender
from drift.varselsender import _smtp_oppsett
from drift.wcag_audit_arbeider import KlientHTTP, nokkelfeil


def main() -> int:
    from db.hemmeligheter import last_credentials
    last_credentials()
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from modules.m26_prisbok import controller
    from policy_validator import attestering
    api = os.environ.get("DISPONIT_API_URL", "http://127.0.0.1:8099")
    token = os.environ.get("DISPONIT_MODULTOKEN", "").strip()
    nokkel_sti = os.environ.get("DISPONIT_KVITTERINGSNOKKEL", "")
    smtp = _smtp_oppsett()
    mangler = [n for n, v in (("DISPONIT_MODULTOKEN", token),
                              ("DISPONIT_KVITTERINGSNOKKEL", nokkel_sti),
                              ("DISPONIT_SMTP_*", smtp))
               if not v]
    if mangler:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "mangler": mangler}), file=sys.stderr)
        return 2
    try:
        with open(nokkel_sti, encoding="utf-8") as f:
            nk = json.load(f)
    except (OSError, ValueError) as e:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "nokkelfil": type(e).__name__}), file=sys.stderr)
        return 2
    nokkelmangler = nokkelfeil(nk)
    if nokkelmangler:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "nokkelfelt": nokkelmangler}), file=sys.stderr)
        return 2

    def signer(kropp):
        return attestering.signer({**kropp, "verifikator": nk["verifikator"]},
                                  nk["nokkel_id"], nk["hemmelighet"])

    sender = lag_sender(smtp)
    klient = KlientHTTP(api, controller.http_frist_s())
    rt = os.environ.get("RUNTIME_DIRECTORY", "").split(":")[0]
    hb = os.path.join(rt, "heartbeat") if rt else None
    poll_s = float(os.environ.get("DISPONIT_M26_POLL_S", "10"))
    print(json.dumps({"hendelse": "m26_arbeider_oppe", "api": api}),
          flush=True)
    while True:
        try:
            res = controller.kjor_en(klient, token, sender, signer)
        except Exception as e:                          # noqa: BLE001
            print(json.dumps({"hendelse": "runde_feilet",
                              "feiltype": type(e).__name__}), flush=True)
            time.sleep(poll_s)
            continue
        if hb:
            try:
                with open(hb, "w") as f:
                    f.write(str(int(time.time())))
            except OSError:
                pass
        if res.get("utfall") == "tomt":
            time.sleep(poll_s)
            continue
        print(json.dumps({"hendelse": "oppdrag_behandlet", **{
            k: v for k, v in res.items()
            if k in ("utfall", "grunn", "kvittering_status", "henvendelse",
                     "utkast", "feiltype")}}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
