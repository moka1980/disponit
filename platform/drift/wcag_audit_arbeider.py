"""Arbeideren for m_wcag_audit: claim → motor → rapport → kvittering.

Systemd-inngangen for `disponit-wcag-audit.service`. All faglig logikk bor
i `modules.wcag_audit.controller.kjor_en` — dette er bare drift: token og
nøkler fra LoadCredential, motorkommandoen fra config, en tynn HTTP-klient
mot API-et, og en høflig løkke (poll ved tom kø, én JSON-linje per
utfall).

Konfigurasjon (alle via credentials/miljø, se unit-filen):
  DISPONIT_API_URL           API-basen (default http://127.0.0.1:8099)
  DISPONIT_MODULTOKEN        mtk_-tokenet fra onboarding (035); rotasjon
                             er ops' jobb (token-cli) + restart av unｉten
  DISPONIT_WCAG_MOTOR        motorkommandoen (shlex-splittes) — containeren
  DISPONIT_WCAG_KONTEKST     sti til JSON med serverkonteksten
                             {axe_versjon, chromium_versjon,
                              container_image_digest, viewport, locale,
                              timezone} — digestene er VÅRE, aldri motorens
  DISPONIT_KVITTERINGSNOKKEL sti til JSON {verifikator, nokkel_id,
                              hemmelighet} for PR-006-signeringen
"""
from __future__ import annotations

import json
import os
import shlex
import sys
import time
import urllib.error
import urllib.request


class _Svar:
    def __init__(self, status: int, kropp: bytes):
        self.status_code = status
        self._kropp = kropp

    def json(self):
        return json.loads(self._kropp.decode("utf-8"))

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class KlientHTTP:
    """Nøyaktig grensesnittet `kjor_en` bruker: `.post(sti, json=, headers=)`
    -> objekt med status_code/json()/raise_for_status().

    FRISTEN ER AVLEDET, IKKE VALGT (Codex P2, runde 5). Den sto som et fast,
    håndskrevet sekundtall ved siden av controllerens retry: `lever` prøver
    fire ganger, for opplastingen og igjen for kvitteringen, så en plattform
    som tar imot forbindelsen og så tier kunne bruke over 480 sekunder på
    opplastingen ALENE — mot de 300 `_skannefrist` reserverer til HELE
    avslutningen. En skanning som ble ferdig nær fristen sin lot da
    kvitteringskapabiliteten løpe ut mens arbeideren fortsatt ventet, og
    oppdraget sto ufullført med kontrollen gjort og betalt.

    `controller.http_frist_s()` er det samme budsjettet delt på kallene som
    faktisk gjøres. Kalleren sender den inn — modulen eier regnestykket,
    drift eier transporten."""

    def __init__(self, base: str, frist_s: float):
        self.base = base.rstrip("/")
        self.frist_s = frist_s

    def post(self, sti: str, json_kropp=None, headers=None, **kw):
        json_kropp = kw.pop("json", json_kropp)
        data = json.dumps(json_kropp or {}).encode("utf-8")
        req = urllib.request.Request(
            self.base + sti, data=data, method="POST",
            headers={"content-type": "application/json",
                     **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=self.frist_s) as r:
                return _Svar(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Svar(e.code, e.read())


def nokkelfeil(nk) -> list[str]:
    """Navnene på feltene i kvitteringsnøkkelen som ikke er brukbare.

    NØKKELEN KONTROLLERES FØR FØRSTE CLAIM (Codex P1, runde 9). Gyldig
    JSON er ikke en brukbar nøkkel: mangler `hemmelighet`, eller er den
    noe annet enn en streng, kaster `signer` først INNE i runden — etter
    at oppdraget er leaset. Da slipper KeyError/AttributeError ut forbi
    controllerens kvitteringshåndtering, løkka nedenfor logger bare
    `runde_feilet`, og oppdraget blir liggende ubesvart til fristen løper
    ut. En arbeider som ikke kan kvittere skal aldri claime, så feilen
    hører hjemme på oppstart der drift ser den.

    EN FOR KORT HEMMELIGHET ER OGSÅ UBRUKELIG (Codex P1, runde 10). En
    ikke-tom hemmelighet signerer noe, men API-siden laster nøkkelregisteret
    gjennom `attestering._valider_register`, som avviser hemmeligheter
    kortere enn `MIN_HEMMELIGHET_TEGN`. Da eksisterer nøkkelen i praksis
    ikke for verifikatoren: arbeideren leaser oppdrag og leverer
    kvitteringer ingen kan godta — nøyaktig utfallet denne kontrollen
    finnes for å hindre. Kravet leses fra API-ets egen modul, ikke skrives
    om her, så et framtidig strengere krav ikke bare gjelder halve veien."""
    from policy_validator.attestering import MIN_HEMMELIGHET_TEGN
    felt = nk if isinstance(nk, dict) else {}
    feil = [f for f in ("verifikator", "nokkel_id", "hemmelighet")
            if not isinstance(felt.get(f), str) or not felt[f].strip()]
    if "hemmelighet" not in feil and (
            len(felt["hemmelighet"]) < MIN_HEMMELIGHET_TEGN):
        feil.append("hemmelighet")
    return feil


def main() -> int:
    from db.hemmeligheter import last_credentials
    last_credentials()
    sys.path.insert(0, os.path.join(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))))
    from modules.wcag_audit import controller
    from modules.wcag_audit.motor import Kommandomotor
    from policy_validator import attestering

    api = os.environ.get("DISPONIT_API_URL", "http://127.0.0.1:8099")
    token = os.environ.get("DISPONIT_MODULTOKEN", "").strip()
    motorkmd = os.environ.get("DISPONIT_WCAG_MOTOR", "")
    kontekst_sti = os.environ.get("DISPONIT_WCAG_KONTEKST", "")
    nokkel_sti = os.environ.get("DISPONIT_KVITTERINGSNOKKEL", "")
    mangler = [n for n, v in (("DISPONIT_MODULTOKEN", token),
                              ("DISPONIT_WCAG_MOTOR", motorkmd),
                              ("DISPONIT_WCAG_KONTEKST", kontekst_sti),
                              ("DISPONIT_KVITTERINGSNOKKEL", nokkel_sti))
               if not v]
    if mangler:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "mangler": mangler}), file=sys.stderr)
        return 2

    kontekst = json.loads(open(kontekst_sti, encoding="utf-8").read())
    nk = json.loads(open(nokkel_sti, encoding="utf-8").read())
    nokkelmangler = nokkelfeil(nk)
    if nokkelmangler:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "nokkelfelt": nokkelmangler}), file=sys.stderr)
        return 2

    def signer(kropp):
        return attestering.signer({**kropp, "verifikator": nk["verifikator"]},
                                  nk["nokkel_id"], nk["hemmelighet"])

    motor = Kommandomotor(shlex.split(motorkmd))
    klient = KlientHTTP(api, controller.http_frist_s())
    rt = os.environ.get("RUNTIME_DIRECTORY", "").split(":")[0]
    hb = os.path.join(rt, "heartbeat") if rt else None
    poll_s = float(os.environ.get("DISPONIT_WCAG_POLL_S", "10"))

    print(json.dumps({"hendelse": "wcag_arbeider_oppe", "api": api}),
          flush=True)
    while True:
        try:
            res = controller.kjor_en(klient, token, motor, kontekst, signer)
        except Exception as e:
            # kjor_en kvitterer selv alt den kan; det som slipper ut her er
            # transport/plattform — logg og fortsett, aldri stille død.
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
            k: v for k, v in res.items() if k in (
                "utfall", "artefakt_id", "kvittering_status", "sider",
                "grunn")}}), flush=True)


if __name__ == "__main__":       # pragma: no cover — systemd-inngangen
    raise SystemExit(main())
