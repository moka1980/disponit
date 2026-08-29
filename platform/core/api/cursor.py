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


def _sjekk_signatur(pepper: str, kropp: bytes, signatur: str) -> None:
    """Konstant-tids signaturkontroll som ALDRI slipper ut annet enn
    `CursorUgyldig` (Cursor P1).

    `hmac.compare_digest` på to `str` kaster TypeError så snart én av dem
    bærer et ikke-ASCII-tegn — og signaturen kommer rått fra en
    query-parameter, så `?cursor=AAAA.%C3%A6` nådde `_les` som et UFANGET
    unntak. `_les` fanger bare `Feilsvar`/`psycopg.Error`, så svaret ble
    500 i stedet for kontraktens `400 cursor_ugyldig`: en klientfeil
    presentert som en serverfeil, og en gratis 500 for hvem som helst med
    en økt. Ulik LENGDE er derimot ikke en feil — `compare_digest` svarer
    da bare False, og gjorde det også før denne vakten."""
    try:
        lik = hmac.compare_digest(_mac(pepper, kropp), signatur)
    except Exception as e:
        raise CursorUgyldig("signaturen er ikke sammenlignbar") from e
    if not lik:
        raise CursorUgyldig("signaturen stemmer ikke")


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
    _sjekk_signatur(pepper, kropp, signatur)
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


# ---------------------------------------------------------------------------
# v2-cursor (PR-008, v4 §3): binder versjon, tenant, endepunkt,
# sorteringsretning, aktive filtre, siste (ts, id) og utløp. IKKE noe
# snapshot-tak — det er den ÆRLIGE keyset-semantikken: ingen duplikater for
# uendrede rader, men samtidig innsetting kan bli synlig eller utelatt
# mellom sider. Et falskt snapshotløfte ble vurdert og forkastet i v4
# (sekvenser er ikke transaksjonelle; REPEATABLE READ lever ikke mellom
# HTTP-kall).
#
# Enhver binding som spriker gir samme svar: `400 cursor_ugyldig`. En
# cursor fra et annet endepunkt, en annen retning eller et endret filter er
# ikke en «nesten riktig» cursor — den peker inn i en annen spørring.
# ---------------------------------------------------------------------------

#: Cursorlevetid. Lang nok til et arbeidsøkt-langt gjennomsyn, kort nok til
#: at en lekket cursor ikke er et varig artefakt.
LEVETID_S = 24 * 3600


def lag_v2(pepper: str, *, tenant: str, endepunkt: str, retning: str,
           filtre: dict, ts: datetime, rad_id: int,
           naa: datetime | None = None) -> str:
    naa = naa or datetime.now(timezone.utc)
    kropp = json.dumps(
        {"v": 2, "t": tenant, "e": endepunkt, "r": retning,
         "f": dict(sorted((filtre or {}).items())),
         "ts": ts.astimezone(timezone.utc).isoformat(), "id": int(rad_id),
         "x": int(naa.timestamp()) + LEVETID_S},
        sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{_b64(kropp)}.{_mac(pepper, kropp)}"


def les_v2(raa: str, pepper: str, *, tenant: str, endepunkt: str,
           retning: str, filtre: dict,
           naa: datetime | None = None) -> tuple[datetime, int]:
    """-> (ts, id) for keyset-fortsettelsen. Kaster CursorUgyldig."""
    naa = naa or datetime.now(timezone.utc)
    try:
        del_b64, signatur = raa.split(".", 1)
        kropp = _avb64(del_b64)
    except Exception as e:
        raise CursorUgyldig("kunne ikke dekodes") from e
    _sjekk_signatur(pepper, kropp, signatur)
    try:
        data = json.loads(kropp)
        ts = datetime.fromisoformat(data["ts"])
        rad_id = int(data["id"])
        utlop = int(data["x"])
    except Exception as e:
        raise CursorUgyldig("innholdet er feilformet") from e
    if data.get("v") != 2:
        raise CursorUgyldig("feil cursorversjon")
    if data.get("t") != tenant:
        raise CursorUgyldig("cursoren tilhører en annen tenant")
    if data.get("e") != endepunkt:
        raise CursorUgyldig("cursoren tilhører et annet endepunkt")
    if data.get("r") != retning:
        raise CursorUgyldig("cursoren har en annen sorteringsretning")
    if data.get("f") != dict(sorted((filtre or {}).items())):
        # Endret filter gjør cursoren ugyldig (v2 §5) — den peker på en
        # posisjon i en ANNEN resultatmengde.
        raise CursorUgyldig("cursorens filtre stemmer ikke")
    if int(naa.timestamp()) > utlop:
        raise CursorUgyldig("cursoren er utløpt")
    if ts.tzinfo is None:
        raise CursorUgyldig("tidsstempelet mangler tidssone")
    return ts, rad_id
