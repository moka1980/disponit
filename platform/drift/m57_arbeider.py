"""Arbeideren for m57_ats: claim → evaluering → rapport → kvittering.

Systemd-inngangen for `disponit-m57.service` — m56-arbeiderens form
(`wcag_audit_arbeider`), speilet. All faglig logikk bor i
`modules.m57_ats.controller.kjor_en`; dette er drift: token og nøkler
fra LoadCredential, modell- og uttrekkskonfig fra miljøet, en tynn
HTTP-klient, og en høflig løkke.

Konfigurasjon:
  DISPONIT_API_URL            API-basen (default http://127.0.0.1:8099)
  DISPONIT_MODULTOKEN         mtk_-tokenet fra onboarding (035)
  DISPONIT_M57_MODELL_URL     lokal modellserver (Ollama-form)
  DISPONIT_M57_MODELLNAVN     modellnavnet hos serveren
  DISPONIT_M57_MODELL_DIGEST  sha256-digest biasmålingene er bundet til
  DISPONIT_M57_BIASMAALINGER  sti til JSON {digest: {...Biasmaaling...}}
  DISPONIT_M57_PDF_KOMMANDO   pdf-uttrekkskommandoen (tom = pdf avvises)
  DISPONIT_KVITTERINGSNOKKEL  sti til JSON {verifikator, nokkel_id,
                              hemmelighet} (PR-006-signeringen)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request


class _Svar:
    def __init__(self, status: int, kropp: bytes):
        self.status_code = status
        self.content = kropp

    def json(self):
        return json.loads(self.content.decode("utf-8"))

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class KlientHTTP:
    """m56-klientens form + `content` (buntveien henter rå bytes)."""

    def __init__(self, base: str, frist_s: float):
        self.base = base.rstrip("/")
        self.frist_s = frist_s

    def post(self, sti: str, json_kropp=None, headers=None, timeout=None,
             **kw):
        # `timeout` er EKSPLISITT i signaturen, ikke i `**kw` (Codex P1,
        # #173): kandidatsinkene gir kallet sin egen overføringsfrist
        # (`controller.SKRIVEFRIST_S`), og hadde den ligget i `**kw`
        # ville den blitt slukt i stillhet og hvert stort skriv fortsatt
        # kjørt på avslutningens 5 sekunder — en fiks som så riktig ut i
        # kallet og ikke gjorde noe.
        json_kropp = kw.pop("json", json_kropp)
        data = json.dumps(json_kropp or {}).encode("utf-8")
        req = urllib.request.Request(
            self.base + sti, data=data, method="POST",
            headers={"content-type": "application/json",
                     **(headers or {})})
        frist = self.frist_s if timeout is None else timeout
        try:
            with urllib.request.urlopen(req, timeout=frist) as r:
                return _Svar(r.status, r.read())
        except urllib.error.HTTPError as e:
            return _Svar(e.code, e.read())


def main() -> int:
    # Repo-roten på stien FØR første repo-import (CodeRabbit): ellers
    # avhenger `db.hemmeligheter`-importen av kallerens cwd.
    sys.path.insert(0, os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))
    from db.hemmeligheter import last_credentials
    last_credentials()
    from modules.m57_ats import controller, evaluering
    from modules.m57_ats.modell import Ollamamodell
    from modules.m57_ats.uttrekk import Uttrekker
    from policy_validator import attestering

    # Nøkkelen kontrolleres FØR første claim (m56, Codex P1 runde 9/10):
    # en arbeider som ikke kan kvittere skal aldri claime.
    from drift.wcag_audit_arbeider import nokkelfeil

    api = os.environ.get("DISPONIT_API_URL", "http://127.0.0.1:8099")
    token = os.environ.get("DISPONIT_MODULTOKEN", "").strip()
    modell_url = os.environ.get("DISPONIT_M57_MODELL_URL", "")
    modellnavn = os.environ.get("DISPONIT_M57_MODELLNAVN", "")
    digest = os.environ.get("DISPONIT_M57_MODELL_DIGEST", "")
    bias_sti = os.environ.get("DISPONIT_M57_BIASMAALINGER", "")
    nokkel_sti = os.environ.get("DISPONIT_KVITTERINGSNOKKEL", "")
    mangler = [n for n, v in (
        ("DISPONIT_MODULTOKEN", token),
        ("DISPONIT_M57_MODELL_URL", modell_url),
        ("DISPONIT_M57_MODELLNAVN", modellnavn),
        ("DISPONIT_M57_MODELL_DIGEST", digest),
        ("DISPONIT_M57_BIASMAALINGER", bias_sti),
        ("DISPONIT_KVITTERINGSNOKKEL", nokkel_sti)) if not v]
    if mangler:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "mangler": mangler}), file=sys.stderr)
        return 2

    nk = json.loads(open(nokkel_sti, encoding="utf-8").read())
    nokkelmangler = nokkelfeil(nk)
    if nokkelmangler:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "nokkelfelt": nokkelmangler}), file=sys.stderr)
        return 2

    # Biasmålingene leses og VALIDERES på oppstart: en digest uten
    # målinger skal nekte oppstart her, aldri felle claimede oppdrag én
    # om gangen (port 17-økonomien).
    raa = json.loads(open(bias_sti, encoding="utf-8").read())
    biasmaalinger = {d: evaluering.Biasmaaling(**m)
                     for d, m in (raa or {}).items()}
    try:
        evaluering.krev_biasmaaling(digest, biasmaalinger)
    except evaluering.Evalueringsfeil as e:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "bias": e.kode}), file=sys.stderr)
        return 2

    def signer(kropp):
        return attestering.signer({**kropp, "verifikator": nk["verifikator"]},
                                  nk["nokkel_id"], nk["hemmelighet"])

    modell = Ollamamodell(modell_url, modellnavn, digest)
    uttrekker = Uttrekker(os.environ.get("DISPONIT_M57_PDF_KOMMANDO", ""))
    klient = KlientHTTP(api, controller.http_frist_s())
    rt = os.environ.get("RUNTIME_DIRECTORY", "").split(":")[0]
    hb = os.path.join(rt, "heartbeat") if rt else None
    poll_s = float(os.environ.get("DISPONIT_M57_POLL_S", "15"))

    print(json.dumps({"hendelse": "m57_arbeider_oppe", "api": api}),
          flush=True)
    while True:
        try:
            res = controller.kjor_en(klient, token, modell, uttrekker,
                                     biasmaalinger, signer)
        except Exception as e:                       # noqa: BLE001
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
                "utfall", "artefakt_id", "kvittering_status",
                "kandidater", "grunn")}}), flush=True)


if __name__ == "__main__":       # pragma: no cover — systemd-inngangen
    raise SystemExit(main())
