"""M-6 innhenteren (PR-C a, «M-6 inntak»): planarbeiderens runde henter
meldingene fra hver tilkoblet postboks og skriver dem i registeret.

Dommen 31/8 står: KUN lesende (`Mail.Read`), ingen sendevei, ingen
modellvei her. Runden er 044 §4-formen som de fem utløserne før den —
ingen egen autoritet: kandidatene kommer fra kryss-tenant-døra
`m6_hentekandidater` (175), og alt annet skjer med RADENS tenant satt.

Per kilde:
  1. refresh → kortlivet access (`epost_kilde.hent_access_token`, over
     ssrf-transporten; tokenet lever bare i denne prosessen);
  2. Graphs delta-spørring mot innboksen (`delta_token` er cursoren;
     første runde starter fra nå), få sider per runde — kapasitet
     forsinker, konsumerer aldri;
  3. hver melding: kroppen hentes som TEKST, alt persondata krypteres
     med tenant-DEK i ÉN payload (fra, til, emne, forhåndsvisning,
     kropp), avsender og emne skrives som hasher — ON CONFLICT DO
     NOTHING på leverandørens melding-id (088 port 1);
  4. hentemerkene (`sist_hentet_ts`, `delta_token`) skrives når siden
     er lagret; en autentiseringsfeil setter kilden `feilet` (veien
     tilbake er en ny samtykkerunde, PR-B), alt annet er forbigående og
     rører ingenting.

Loggen bærer aldri adresse, emne eller tekst — bare tellinger og
kildens id. Kill-switch: `DISPONIT_EPOST_INNTAK=av`.
"""
from __future__ import annotations

import hashlib
import json
import os
import time

GRAPH = "https://graph.microsoft.com/v1.0"
#: Delta over innboksen — feltene lista trenger; kroppen hentes per
#: melding som tekst (Prefer-headeren), aldri som HTML i registeret.
DELTA_START = (GRAPH + "/me/mailFolders/inbox/messages/delta"
               "?$select=id,conversationId,receivedDateTime,from,"
               "toRecipients,subject,bodyPreview,hasAttachments&$top=25")
MAKS_KILDER = int(os.environ.get("DISPONIT_EPOST_MAKS_KILDER", "20"))
MAKS_SIDER = int(os.environ.get("DISPONIT_EPOST_MAKS_SIDER", "4"))
MAKS_KROPP = 64 * 1024
AKTOR = "agent:epost"


def er_av() -> bool:
    return os.environ.get("DISPONIT_EPOST_INNTAK", "").strip().lower() \
        in ("av", "0", "false", "nei")


def _hash(v: str) -> str:
    return hashlib.sha256((v or "").strip().lower().encode()).hexdigest()


def kandidater(conn, grense: int = MAKS_KILDER) -> list:
    conn.rollback()
    rader = conn.execute("SELECT * FROM m6_hentekandidater(%s)",
                         (grense,)).fetchall()
    conn.rollback()
    return rader


class GraphFeil(Exception):
    def __init__(self, status: int, melding: str = ""):
        super().__init__(f"graph {status} {melding}".strip())
        self.status = status


def graph_get(access: str, url: str, *, tekstkropp: bool = False) -> dict:
    """GET mot Graph over ssrf-transporten. -> JSON. Bare graph-hosten
    får tokenet — en delta-cursor fra basen valideres FØR den følges."""
    from api import ssrf
    from api.epost_kilde import hent_konfig
    if not url.startswith(GRAPH + "/"):
        raise GraphFeil(0, "url utenfor graph")
    konfig = hent_konfig()
    allowlist = konfig.allowlist if konfig else ()
    klient = ssrf.lag_klient(allowlist)
    try:
        hode = {"authorization": f"Bearer {access}"}
        if tekstkropp:
            hode["prefer"] = 'outlook.body-content-type="text"'
        r = klient.get(url, headers=hode)
        raa = ssrf.les_begrenset(r)
        if r.status_code != 200:
            raise GraphFeil(r.status_code)
        return json.loads(raa)
    except ssrf.SsrfAvvist as e:
        raise GraphFeil(0, type(e).__name__) from e
    except (json.JSONDecodeError, OSError) as e:
        raise GraphFeil(0, type(e).__name__) from e
    finally:
        klient.close()


def _adresse(p) -> tuple[str, str]:
    e = (p or {}).get("emailAddress") or {}
    return str(e.get("address") or ""), str(e.get("name") or "")


def _lagre(conn, tenant, kilde_id, m, kropp: str | None, kropp_type) -> bool:
    """Én melding → én rad (ON CONFLICT DO NOTHING). -> ny?"""
    from db import kryptering
    fra, fra_navn = _adresse(m.get("from"))
    til = [_adresse(x)[0] for x in (m.get("toRecipients") or [])][:20]
    emne = str(m.get("subject") or "")
    forhand = str(m.get("bodyPreview") or "")[:400]
    payload = {"fra": fra, "fra_navn": fra_navn, "til": til, "emne": emne,
               "forhandsvisning": forhand,
               "kropp": (kropp if kropp is not None else forhand)[:MAKS_KROPP],
               "kropp_type": kropp_type or "text"}
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    ct, nonce = kryptering.krypter(dek, payload, tenant, key_id)
    rad = conn.execute(
        "INSERT INTO epost_melding (tenant, kilde_id, leverandor_melding_id,"
        " trad_id, mottatt_ts, retning, avsender_hash, emne_hash,"
        " kropp_kryptert, nonce, key_id, har_vedlegg)"
        " VALUES (%s,%s,%s,%s,%s,'inn',%s,%s,%s,%s,%s,%s)"
        " ON CONFLICT (tenant, kilde_id, leverandor_melding_id) DO NOTHING"
        " RETURNING melding_id",
        (tenant, kilde_id, str(m["id"]), m.get("conversationId"),
         m.get("receivedDateTime"), _hash(fra), _hash(emne), ct, nonce,
         key_id, bool(m.get("hasAttachments")))).fetchone()
    return rad is not None


def hent_en(conn, rad, *, graf=graph_get, veksler=None) -> dict:
    """Én kilde, én runde. `graf`/`veksler` er testsnittene."""
    from api.epost_kilde import KildeFeil, hent_access_token
    from db.pg import sett_kontekst
    tenant, kilde_id, _postboks, delta, _sist = rad
    kid = str(kilde_id)
    rid = f"epost-{kid[:8]}"
    ut = {"kilde": kid[:8], "sett": 0, "nye": 0, "sider": 0}
    sett_kontekst(conn, tenant, AKTOR, rid)
    try:
        access = hent_access_token(conn, tenant, kilde_id, veksler=veksler)
    except KildeFeil as e:
        conn.rollback()
        return _feilet(conn, tenant, kilde_id, rid, ut, "token", str(e))
    conn.rollback()
    url = delta if delta and delta.startswith(GRAPH + "/") else DELTA_START
    neste_delta = None
    try:
        for _ in range(MAKS_SIDER):
            side = graf(access, url)
            ut["sider"] += 1
            sett_kontekst(conn, tenant, AKTOR, rid)
            for m in side.get("value") or []:
                if "@removed" in m or not m.get("id"):
                    continue
                ut["sett"] += 1
                kropp, ktype = None, None
                try:
                    k = graf(access, f"{GRAPH}/me/messages/{m['id']}"
                             "?$select=body", tekstkropp=True)
                    kropp = str(((k.get("body") or {}).get("content")) or "")
                    ktype = (k.get("body") or {}).get("contentType")
                except GraphFeil:
                    kropp = None                   # forhåndsvisningen står
                if _lagre(conn, tenant, kilde_id, m, kropp, ktype):
                    ut["nye"] += 1
            conn.commit()
            if side.get("@odata.deltaLink"):
                neste_delta = side["@odata.deltaLink"]
                break
            url = side.get("@odata.nextLink") or ""
            if not url:
                break
    except GraphFeil as e:
        conn.rollback()
        if e.status in (401, 403):
            return _feilet(conn, tenant, kilde_id, rid, ut, "graph_auth",
                           str(e))
        ut["forbigaende"] = f"graph_{e.status}"
        _log("epost_inntak_forbigaende", **ut)
        return ut
    sett_kontekst(conn, tenant, AKTOR, rid)
    conn.execute(
        "UPDATE epost_kilde SET sist_hentet_ts = now(),"
        " delta_token = coalesce(%s, delta_token)"
        " WHERE tenant=%s AND kilde_id=%s", (neste_delta, tenant, kilde_id))
    conn.commit()
    return ut


def _feilet(conn, tenant, kilde_id, rid, ut, grunn, detalj) -> dict:
    """Autentiseringen sviktet: kilden er `feilet` — veien tilbake er en
    ny samtykkerunde (PR-B). Ingen adresse i loggen; `detalj` er
    kildemodulens egen setning uten token."""
    from db.pg import sett_kontekst
    sett_kontekst(conn, tenant, AKTOR, rid)
    conn.execute("UPDATE epost_kilde SET status='feilet'"
                 " WHERE tenant=%s AND kilde_id=%s AND status='aktiv'",
                 (tenant, kilde_id))
    conn.commit()
    ut["feilet"] = grunn
    _log("epost_inntak_kilde_feilet", grunn=grunn, detalj=detalj[:120], **ut)
    return ut


def _log(hendelse: str, **felt) -> None:
    print(json.dumps({"hendelse": hendelse, **felt}, ensure_ascii=False),
          flush=True)


def kjor_en_runde(tjeneste, conn, *, graf=graph_get, veksler=None) -> dict:
    if er_av():
        _log("epost_inntak_av")
        return {"av": True, "plukket": 0, "resultater": []}
    rader = kandidater(conn)
    start = time.monotonic()
    resultater = [hent_en(conn, rad, graf=graf, veksler=veksler)
                  for rad in rader]
    res = {"plukket": len(rader), "resultater": resultater,
           "ms": int((time.monotonic() - start) * 1000)}
    if rader:
        _log("epost_inntak_runde", **res)
    return res
