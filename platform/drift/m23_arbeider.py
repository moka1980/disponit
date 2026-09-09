"""M-23-arbeideren: claimer purring.send-oppdrag over API-et med
modultokenet fra onboarding, sender e-posten gjennom husets SMTP, og
kvitterer signert. m56-arbeiderens form (`wcag_audit_arbeider`).

TILLITSSNITTET: prosessen har modultoken + kvitteringsnøkkel + SMTP-
oppsett — men INGEN DB-tilgang, ingen KEK, ingen tenant-nøkler.
Adressen kommer dekryptert i claim-svaret (149) og lever i minnet så
lenge sendingen varer.

Miljø (EnvironmentFile /etc/disponit/m23/konfig + LoadCredential):
  DISPONIT_API_URL           plattformen (standard http://127.0.0.1:8099)
  DISPONIT_MODULTOKEN        mtk_-tokenet fra onboarding (035)
  DISPONIT_KVITTERINGSNOKKEL sti til JSON {verifikator, nokkel_id,
                             hemmelighet} for kvitteringssigneringen
  DISPONIT_SMTP_VERT/PORT/BRUKER/PASSORD/AVSENDER  husets SMTP
                             (varselsenderens navn, samme oppsett)
  DISPONIT_M23_POLL_S        pause mellom tomme claim (standard 10)
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from drift.varselsender import SMTP_TIMEOUT_S, _smtp_oppsett
from drift.wcag_audit_arbeider import KlientHTTP, nokkelfeil


def lag_sender(oppsett: dict):
    """-> sender(til, emne, tekst, *, avsender_navn, svar_til) -> dict.
    Avsenderadressen er HUSETS; navnet er tenantens (149)."""
    def send(til, emne, tekst, *, avsender_navn=None, svar_til=None):
        m = EmailMessage()
        m["From"] = (formataddr((avsender_navn, oppsett["avsender"]))
                     if avsender_navn else oppsett["avsender"])
        m["To"] = til
        if svar_til:
            m["Reply-To"] = svar_til
        m["Subject"] = emne
        m["Message-ID"] = make_msgid(domain=oppsett["avsender"]
                                     .rsplit("@", 1)[-1])
        m.set_content(tekst)
        ctx = ssl.create_default_context()
        with smtplib.SMTP(oppsett["vert"], oppsett["port"],
                          timeout=SMTP_TIMEOUT_S) as s:
            s.starttls(context=ctx)
            s.login(oppsett["bruker"], oppsett["passord"])
            s.send_message(m)
        return {"melding_id": m["Message-ID"]}
    return send


def main() -> int:
    from db.hemmeligheter import last_credentials
    last_credentials()
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from modules.m23_fordring import controller
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
        # Uten SMTP claimes INGENTING: et claim vi ikke kan utføre er en
        # purring som blir hengende hos oss til fristen.
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "mangler": mangler}), file=sys.stderr)
        return 2
    nk = json.loads(open(nokkel_sti, encoding="utf-8").read())
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
    poll_s = float(os.environ.get("DISPONIT_M23_POLL_S", "10"))
    print(json.dumps({"hendelse": "m23_arbeider_oppe", "api": api}),
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
            if k in ("utfall", "grunn", "kvittering_status", "trinn",
                     "handling_trinn", "feiltype")}}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
