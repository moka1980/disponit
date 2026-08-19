"""Serveren for det syntetiske testnettstedet (staging-sjekklisten).

Kun loopback. Tre egenskaper sjekklisten trenger og en vanlig filserver
ikke har:

  * ACCESS-LOGG SOM BEVIS (port 20): hver forespørsel appendes som én
    JSON-linje til --logg. Robots-beviset er NEGATIVT — «ingen linje for
    /privat/» i MÅLETS logg, ikke motorens påstand om egen dyd.
  * --robots-5xx: robots.txt svarer 503 → motoren skal la være å crawle
    (kun mal_url kontrolleres). Resten av nettstedet svarer normalt, så
    en motor som crawler likevel blir SYNLIG i loggen.
  * deterministisk: ingen cache-headere som kan gi 304-varianter.
"""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROT = Path(__file__).resolve().parent / "sider"

TYPER = {".html": "text/html; charset=utf-8",
         ".css": "text/css; charset=utf-8",
         ".png": "image/png",
         ".txt": "text/plain; charset=utf-8"}


def lag_handler(logg: Path, robots_5xx: bool):
    class Handler(BaseHTTPRequestHandler):
        server_version = "fasitbutikken/1"

        def log_message(self, *a):  # stille — vi fører vår egen logg
            pass

        def _logg(self, status: int):
            with logg.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.time(), "sti": self.path,
                                    "status": status}) + "\n")

        def do_GET(self):
            sti = self.path.split("?", 1)[0]
            if sti == "/robots.txt" and robots_5xx:
                self._logg(503)
                self.send_response(503)
                self.end_headers()
                return
            if sti.endswith("/"):
                sti += "index.html"
            if sti == "/":
                sti = "/index.html"
            fil = (ROT / sti.lstrip("/")).resolve()
            if not fil.is_file() or ROT not in fil.parents and fil != ROT:
                self._logg(404)
                self.send_response(404)
                self.end_headers()
                return
            data = fil.read_bytes()
            self._logg(200)
            self.send_response(200)
            self.send_header("content-type",
                             TYPER.get(fil.suffix, "application/octet-stream"))
            self.send_header("content-length", str(len(data)))
            self.send_header("cache-control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8093)
    ap.add_argument("--logg", type=Path, required=True)
    ap.add_argument("--robots-5xx", action="store_true")
    # Plattformen krever https-mål (normaliser_vertsnavn leser KUN
    # https-URL-er), så staging-fixturen serverer TLS med et selvsignert
    # sertifikat; motoren godtar det bare med sin eksplisitte
    # MOTOR_TLS_USIKKER-bryter.
    ap.add_argument("--tls-sert", type=Path)
    ap.add_argument("--tls-nokkel", type=Path)
    a = ap.parse_args()
    a.logg.parent.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port),
                              lag_handler(a.logg, a.robots_5xx))
    tls = bool(a.tls_sert and a.tls_nokkel)
    if tls:
        import ssl
        ktx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ktx.load_cert_chain(a.tls_sert, a.tls_nokkel)
        srv.socket = ktx.wrap_socket(srv.socket, server_side=True)
    print(json.dumps({"hendelse": "testnettsted_oppe", "port": a.port,
                      "tls": tls, "robots_5xx": a.robots_5xx}), flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
