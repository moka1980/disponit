"""Signert keyset-cursor for `GET /v1/unntak` (v2 Del 3.1).

Keyset og ikke OFFSET: en kø som får nye saker mens noen blar, hopper over
eller gjentar rader med OFFSET. Keyset peker på «etter (ts, id)» og er
stabil.

Cursoren er signert med server-pepper og BUNDET TIL TENANT. Uten binding
er en cursor bare et par tall — og et par tall fra en annen tenants kø ser
nøyaktig like gyldige ut. Signaturen gjør det umulig å gjette seg fram til
en gyldig cursor, og tenantbindingen gjør en STJÅLET cursor ubrukelig hos
alle andre enn eieren. Manipulert cursor -> 400 `cursor_ugyldig`.
"""
from __future__ import annotations

import base64
import hmac
import hashlib
import json
from datetime import datetime, timezone


class CursorUgyldig(Exception):
    """Feilformet, uleselig eller feil signert cursor."""


def _mac(pepper: str, kropp: bytes) -> str:
    return hmac.new(pepper.encode("utf-8"), kropp, hashlib.sha256).hexdigest()


def _b64(raa: bytes) -> str:
    return base64.urlsafe_b64encode(raa).decode("ascii").rstrip("=")


def _avb64(tekst: str) -> bytes:
    pad = "=" * (-len(tekst) % 4)
    return base64.urlsafe_b64decode(tekst + pad)


def lag(tenant: str, ts: datetime, sak_id: int, pepper: str) -> str:
    kropp = json.dumps({"t": tenant, "ts": ts.astimezone(timezone.utc).isoformat(),
                        "id": int(sak_id)},
                       sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{_b64(kropp)}.{_mac(pepper, kropp)}"


def les(raa: str, tenant: str, pepper: str) -> tuple[datetime, int]:
    """-> (ts, id) for keyset-fortsettelsen. Kaster CursorUgyldig."""
    try:
        del_b64, signatur = raa.split(".", 1)
        kropp = _avb64(del_b64)
    except Exception as e:
        raise CursorUgyldig("kunne ikke dekodes") from e
    if not hmac.compare_digest(_mac(pepper, kropp), signatur):
        raise CursorUgyldig("signaturen stemmer ikke")
    try:
        data = json.loads(kropp)
        ts = datetime.fromisoformat(data["ts"])
        sak_id = int(data["id"])
    except Exception as e:
        raise CursorUgyldig("innholdet er feilformet") from e
    if data.get("t") != tenant:
        # Signaturen er ekte, men cursoren tilhører noen andre. Dette er den
        # ene feilen som betyr at et gyldig token brukes med en annen
        # tenants cursor — altså et forsøk, ikke en skrivefeil.
        raise CursorUgyldig("cursoren tilhører en annen tenant")
    if ts.tzinfo is None:
        raise CursorUgyldig("tidsstempelet mangler tidssone")
    return ts, sak_id
