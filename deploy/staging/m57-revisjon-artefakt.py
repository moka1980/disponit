#!/usr/bin/env python3
"""Revisjonsartefaktet for M-57 (`m57-revisjon-v1`).

§6 lover at signaturhendelser og frigivelser står i revisjonsloggen med
identitet. Migrasjon 211 gjør evidensen til en TRIGGER på
`utsendingssignatur` og `utsendingsfrigivelse`; dette skriptet KJØRER de
to dørene på en egen tenant og teller hendelsene loggen faktisk fikk.

Kjøringen går gjennom dørene, aldri rundt dem:
  * `opprett_utsendingsliste` (056) lager lista av et fullført
    `rekruttering.evaluering`-oppdrag,
  * `signer_utsendingsliste` (056) signerer som et MENNESKE med aktivt
    medlemskap — porten der krever det,
  * `frigi_utsendelse` (056) fødes av senderrollen, som i drift.

Tallene leses av `revisjonshendelse` etterpå: én signaturhendelse, én
frigivelseshendelse, null uten identitet, null avskruinger (#159 rev
døra ned — at den er stengt måles av porten, ikke her). Skriptet dømmer
ingenting: `bestatt` er produsentens påstand, og
`manifestskjema._grenser_m57_revisjon` validerer.

UFORANDERLIGHETEN prøves også: en UPDATE mot hendelsen skal avvises av
append-only-triggeren (068). En logg som kan redigeres er ikke en logg.

BRUK (på verten som root, fra et utsjekk, `staging.env` sourcet):
    python deploy/staging/m57-revisjon-artefakt.py [--ut …]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import _sjekk_grenser, valider_artefaktformat  # noqa: E402

KRAV = "m57-revisjon-v1"
AKTOR = "m57-revisjon"


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def ktx(c, tenant, aktor=AKTOR, rid="revisjon"):
    c.execute("SELECT set_config('disponit.tenant', %s, true),"
              " set_config('disponit.aktor', %s, true),"
              " set_config('disponit.request_id', %s, true)",
              (tenant, aktor, rid))


def grunnlag(m, tenant: str) -> tuple[int, uuid.UUID]:
    """Et FULLFØRT `rekruttering.evaluering`-oppdrag med prosess og
    kandidat — det eneste en utsendingsliste kan promotere (§1/§7)."""
    from db import kryptering
    ktx(m, tenant)
    kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
    m.commit()
    # PRODUKSJONSFORMEN (test_m57_utsending._grunnlag): beslutningsoppdrag
    # med KOBLET fase-2-loggpost — koblingsvakten tillater ikke annet.
    ktx(m, tenant)
    logg = m.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash,"
        " policy_id, beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,%s,'api_token','h-rev','p@1.0.0/rekruttering.evaluering',"
        " 'TILLAT','[]',%s) RETURNING id",
        (tenant, AKTOR, secrets.token_hex(8))).fetchone()[0]
    key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(m, tenant)
    ct, nonce = kryptering.krypter(
        dek, {"handling": "rekruttering.evaluering"}, tenant, key_id)
    oid = m.execute(
        "INSERT INTO oppdrag (opprinnelse, tenant, beslutning_loggpost_id,"
        " oppdragstype, handling, eiermodul, payload_kryptert, key_id,"
        " nonce, utforelsesfrist, evidensfrist, koblingsstatus, status)"
        " VALUES ('beslutning',%s,%s,'rekruttering.evaluering',"
        " 'rekruttering.evaluering','m57_ats',%s,%s,%s,"
        " now()+interval '1 day', now()+interval '30 days','KOBLET','plukket')"
        " RETURNING id", (tenant, logg, ct, key_id, nonce)).fetchone()[0]
    pid = uuid.uuid4()
    m.execute("INSERT INTO rekrutteringsprosess (tenant, prosess_id,"
              " oppdrag_id, slettefrist_dogn) VALUES (%s,%s,%s,365)",
              (tenant, pid, oid))
    kid = uuid.uuid4()
    m.execute("INSERT INTO kandidat (tenant, prosess_id, kandidat_id)"
              " VALUES (%s,%s,%s)", (tenant, pid, kid))
    m.execute("UPDATE oppdrag SET status='utfort' WHERE tenant=%s AND id=%s",
              (tenant, oid))
    m.commit()
    return oid, pid, kid


def signatar(m, tenant: str) -> str:
    """Et menneske med aktivt medlemskap — signaturporten krever det."""
    ktx(m, tenant)
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES"
        " ('https://m57.revisjon', %s) RETURNING bruker_id",
        ("s-" + secrets.token_hex(6),)).fetchone()[0]
    m.execute("INSERT INTO brukermedlemskap (tenant, bruker_id, roller,"
              " aktiv) VALUES (%s,%s,%s,true)", (tenant, bid, ["admin"]))
    m.commit()
    return bid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tenant", default=None)
    ap.add_argument("--vert", default=os.uname().nodename)
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()
    dsn = os.environ.get("DISPONIT_MIGRATOR_URL")
    rt_dsn = os.environ.get("DATABASE_URL")
    snd_dsn = os.environ.get("DISPONIT_VARSEL_URL")
    if not (dsn and rt_dsn and snd_dsn):
        raise SystemExit("AVBRUTT: DISPONIT_MIGRATOR_URL/DATABASE_URL/"
                         "DISPONIT_VARSEL_URL mangler")
    tenant = a.tenant or f"t-m57rev-{secrets.token_hex(3)}"
    m = psycopg.connect(dsn)
    # DØRENE KALLES AV DE ROLLENE SOM EIER VEIEN i drift: runtime lager og
    # signerer lista (flaten), senderrollen frigir (utsenderen). Migratoren
    # rigger grunnlaget og LESER — den har ikke EXECUTE på noen av dem.
    rt = psycopg.connect(rt_dsn)
    snd = psycopg.connect(snd_dsn)

    versjon = m.execute("SELECT max(versjon) FROM migrasjoner").fetchone()[0]
    m.commit()
    if versjon is None or int(versjon) < 211:
        raise SystemExit(f"AVBRUTT: basen står på migrasjon {versjon} — 211"
                         " (evidenstriggerne) må være kjørt")

    oid, pid, kid = grunnlag(m, tenant)
    bid = signatar(m, tenant)
    _log(f"tenant {tenant}: oppdrag {oid}, prosess {str(pid)[:8]},"
         f" signatar {bid[:8]}")

    # LISTA gjennom døra (056): promoterer det fullførte oppdraget.
    ktx(rt, tenant)
    serie = uuid.uuid4()
    # 083-formen: medlemmene ER kandidatene (manifestet regner hashen).
    liste_id = rt.execute(
        "SELECT ut_liste_id FROM opprett_utsendingsliste("
        "%s,%s,NULL,%s::bigint,'invitasjon','mal@1',%s::uuid[])",
        (tenant, serie, oid, [str(kid)])).fetchone()[0]
    rt.commit()
    _log(f"liste {str(liste_id)[:8]} (serie {str(serie)[:8]}) opprettet")

    # SIGNATUREN gjennom døra — mennesket trykker.
    ktx(rt, tenant, aktor=f"bruker:{bid}")
    rt.execute("SELECT signer_utsendingsliste(%s,%s,%s,%s)",
               (tenant, liste_id, bid, "op-" + secrets.token_hex(6)))
    rt.commit()
    _log("signert")

    # FRIGIVELSEN gjennom døra — maskinen handler på signaturen.
    ktx(snd, tenant, aktor="m57-utsender")
    frig = snd.execute("SELECT frigi_utsendelse(%s,%s,%s)",
                       (tenant, liste_id, "psn-" + "0" * 64)).fetchone()[0]
    snd.commit()
    _log(f"frigitt {str(frig)[:8]}")

    # TALLENE leses av loggen, ikke av produsenten.
    ktx(m, tenant)
    rader = m.execute(
        "SELECT hendelse_id, handling, aktor, bruker_id FROM revisjonshendelse"
        " WHERE tenant=%s ORDER BY ts", (tenant,)).fetchall()
    m.commit()
    sign = [r for r in rader if r[1] == "m57.utsendingsliste_signert"]
    frigg = [r for r in rader if r[1] == "m57.utsending_frigitt"]
    avskr = [r for r in rader if r[1] == "m57.blinding_avskrudd"]
    uten = [r for r in rader if not (r[3] or "").strip()]

    # UFORANDERLIGHETEN: append-only-triggeren (068) skal avvise en UPDATE.
    uforanderlig = False
    if sign:
        try:
            ktx(m, tenant)
            m.execute("UPDATE revisjonshendelse SET aktor='tuklet'"
                      " WHERE tenant=%s AND hendelse_id=%s",
                      (tenant, sign[0][0]))
            m.commit()
        except psycopg.errors.Error:
            m.rollback()
            uforanderlig = True

    triggere = {r[0] for r in m.execute(
        "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal"
        " AND tgname IN ('utsendingssignatur_revisjon',"
        "                'utsendingsfrigivelse_revisjon')").fetchall()}
    m.commit()

    ts = datetime.now(timezone.utc).isoformat()
    art = {
        "krav_id": KRAV, "ts": ts, "bestatt": True,
        "oppsett": {"modul": "m57_ats", "miljo": "staging", "vert": a.vert,
                    "tenant": tenant, "migrasjon": int(versjon)},
        "identiteter": {
            "liste_id": str(liste_id), "frigivelse_id": str(frig),
            "signatar_prefiks": bid[:8],
            "signaturhendelse_id": str(sign[0][0]) if sign else "",
            "frigivelseshendelse_id": str(frigg[0][0]) if frigg else "",
        },
        "maalt": {
            "revisjon_signaturer": len(sign),
            "revisjon_frigivelser": len(frigg),
            "revisjon_hendelser_uten_identitet": len(uten),
            "revisjon_avskruinger": len(avskr),
            "signaturer_med_signatarens_identitet":
                bool(sign) and all(r[3] == bid for r in sign),
            "frigivelser_med_signatarens_identitet":
                bool(frigg) and all(r[3] == bid for r in frigg),
            "trigger_signatur": "utsendingssignatur_revisjon" in triggere,
            "trigger_frigivelse": "utsendingsfrigivelse_revisjon" in triggere,
            "hendelser_totalt": len(rader),
            "hendelser_er_uforanderlige": uforanderlig,
        },
    }
    feil = valider_artefaktformat(art, KRAV) + _sjekk_grenser(KRAV, art)
    art["bestatt"] = not feil
    if feil:
        art["feil"] = feil
    ut = a.ut or (REPO / "deploy/staging/artefakter"
                  / f"{KRAV}-{ts[:19].replace(':', '').replace('-', '')}Z.json")
    ut.parent.mkdir(parents=True, exist_ok=True)
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False, sort_keys=True)
                  + "\n", encoding="utf-8")
    _log(f"skrev {ut} (bestatt={art['bestatt']})")
    for f in feil:
        _log("  FEIL:", f)
    return 0 if not feil else 1


if __name__ == "__main__":
    raise SystemExit(main())
