#!/usr/bin/env python3
"""Feilinjisering menneskelig unntaksbehandling — produserer `behandling-m37-v1`.

Injiserer 12 saker over 4 kategorier og BEHANDLER dem menneskelig gjennom den
EKTE porten (`api.unntaksbehandling.behandle_unntakshandling`): ekte motor,
ekte krypto, ekte triggere, ekte revisjonslogg. Ingenting simuleres —
konvolutten bygges og MAC-signeres server-side under saks­låsen, og
beslutningen skrives av den ene lovlige skriveveien.

Artefaktet MÅLES her, men VALIDERES av evidensporten
(`manifestskjema._grenser_behandling`), aldri av dette skriptet: `bestatt` er
produsentens egen påstand, og en port som leser produsentens konklusjon
validerer ingenting (PR #8 runde 3).

De fire kategoriene (spec §10): avvis-vei terminal · godkjenn-vei gir ny
beslutning motoren evaluerer · sideeffekt → `venter_utførelse` (og videre til
`løst` via M-37-outboxen) · fire-øyne krever to ulike brukere. Pluss de harde
invariantene: saksversjonskonflikt → 409 uten sideeffekt · to samtidige
behandlinger av samme sak → nøyaktig én vinner · ingen klartekst i logg/dump ·
alle handlinger i revisjonsloggen med aktør.

BRUK:
    DISPONIT_REPO=/opt/disponit DISPONIT_TEST_DSN=... DISPONIT_TEST_MIGRATOR_DSN=... \\
    DISPONIT_KEK=... DISPONIT_TOKEN_PEPPER=... DISPONIT_ATT_NOKLER=... \\
    DISPONIT_MAC_NOKLER=... \\
    python3 deploy/staging/feilinjisering-behandling.py [--ut artefakt.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(os.environ["DISPONIT_REPO"])
sys.path.insert(0, str(REPO / "platform/core"))

DSN = os.environ["DISPONIT_TEST_DSN"]
MIGRATOR = os.environ["DISPONIT_TEST_MIGRATOR_DSN"]
NAA = datetime.now(timezone.utc)
TENANT = "t-beh-" + secrets.token_hex(3)

# Klarteksten som ALDRI skal forlate krypteringen (canary). Én per sak, så et
# treff i en dump peker på nøyaktig hvilken.
KANARIFRASE = "KANARI-" + secrets.token_hex(8)

from api.mac_register import MacRegister                       # noqa: E402
from api.minimering import bygg_handlingsintensjon             # noqa: E402
from api.unntaksbehandling import (Godkjenningsfeil,           # noqa: E402
                                   behandle_unntakshandling)
from db import kryptering                                      # noqa: E402
from db.pg import koble, migrer, sett_kontekst, sett_tenant    # noqa: E402
from policy_validator.engine import _policy_innholds_hash      # noqa: E402


def _macreg() -> MacRegister:
    from api.mac_register import last_mac_register
    return last_mac_register()


class _Pool:
    def hent(self, timeout=5.0):
        return koble(DSN)

    def gi_tilbake(self, conn):
        conn.close()


def _policy(policy_id: str, fire_oyne: bool = False) -> dict:
    """Fullt skjema-gyldig policy m/ en VILKÅRSFRI faktura.bokfor (så den
    menneskelige re-evalueringen faktisk kan nå TILLAT) + menneskelig
    overstyring for belop_over_grense."""
    mo = {"godkjennbare": [{"grunnkode": "belop_over_grense",
                            "handling": "faktura.bokfor",
                            "belop_maks": "80000.00", "valuta": "NOK"}],
          "krever_rolle": "okonomi"}
    if fire_oyne:
        mo["krever_fire_oyne"] = True
    return {
        "schema_version": "0.2",
        "meta": {"policy_id": policy_id, "versjon": "1.0.0",
                 "bransjemal": "beh-test", "status": "validert_pilot"},
        "tidssone": "Europe/Oslo",
        "roller": [{"id": "agent", "beskrivelse": "agent"},
                   {"id": "okonomi", "beskrivelse": "okonomi"},
                   {"id": "godkjenner", "beskrivelse": "godkjenner"}],
        "dataklasser": ["offentlig", "intern", "finansiell"],
        "verifikatorer": {"v_x": {"beskrivelse": "ubrukt",
                                  "betrodd_for": ["ubrukt_vilkaar"]}},
        "unntak": {"kategorier": ["over_grense", "manglende_data",
                                  "regelkonflikt", "teknisk_feil",
                                  "ugyldig_data", "ukjent"],
                   "maks_auto_forsok": 3, "eskalering": "unntakskø"},
        "handlinger": [{"id": "faktura.bokfor", "modul": "M-14",
                        "modus": "auto", "ved_brudd": "unntakskø",
                        "tillatt_for": ["agent"],
                        "dataklasser_tillatt": ["finansiell"],
                        "grenser": {"belop_maks": "25000.00",
                                    "valuta": ["NOK"]},
                        "reversering": {"type": "direkte"}}],
        "menneskelig_overstyring": mo,
    }


POL = _policy("beh-mg")
POL_HASH = _policy_innholds_hash(POL)
POL_FIRE = _policy("beh-mg-fire", fire_oyne=True)
POL_FIRE_HASH = _policy_innholds_hash(POL_FIRE)


def _identitet(m, sub: str) -> str:
    return m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES ('https://idp',%s)"
        " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
        " RETURNING bruker_id", (sub,)).fetchone()[0]


def _medlem(sub: str) -> str:
    m = koble(MIGRATOR)
    sett_kontekst(m, TENANT, "sys", "r0")
    bid = _identitet(m, f"{TENANT}-{sub}")
    m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller) VALUES"
              " (%s,%s,ARRAY['godkjenner','okonomi'])"
              " ON CONFLICT (tenant,bruker_id) DO UPDATE SET roller=EXCLUDED.roller",
              (TENANT, bid))
    m.commit()
    m.close()
    return bid


def _injiser(conn, merkelapp: str, policy_id: str, phash: str) -> int:
    """Én manuell, godkjennbar sak med EKTE kryptert intensjon. Klartekst-
    canary legges i begrunnelsesparametrene og i intensjonens ressurs-id, så en
    dump ville avslørt den om noe lekket."""
    from api.kjerne import _skriv_unntak
    import types
    sett_kontekst(conn, TENANT, "sys", "r0")
    # Canaryen legges KUN i det som skal krypteres (payload + intensjonens
    # ressurs_id). Begrunnelseskjeden er klartekst og skal ALDRI bære den —
    # så et treff i canary-skanningen er en EKTE lekkasje, ikke vår egen sådd.
    lid = conn.execute(
        "INSERT INTO revisjonslogg (tenant,input_hash,policy_id,beslutning,"
        "begrunnelse) VALUES (%s,'h',%s,'UNNTAK',%s::jsonb) RETURNING id",
        (TENANT, f"{policy_id}@1.0.0/faktura.bokfor",
         json.dumps([{"kode": "rolle_ok", "params": {"rolle": "agent"}},
                     {"kode": "belop_over_grense"}]))).fetchone()[0]
    snap = types.SimpleNamespace(maks_auto_forsok=3, versjon="1.0.0",
                                 innholds_hash=phash)
    ev = {"handling": "faktura.bokfor", "belop": "45000.00", "valuta": "NOK",
          "ressurs_id": f"{merkelapp}-{KANARIFRASE}",
          "dataklasser": ["finansiell"], "dataklasser_kilde": "connector"}
    uid = _skriv_unntak(conn, TENANT, lid, "faktura.bokfor", "over_grense",
                        "normal", "normal", {"handling": "faktura.bokfor",
                                             "canary": KANARIFRASE},
                        snap, bygg_handlingsintensjon(ev, "agent"))
    conn.execute("UPDATE unntak SET status='manuell' WHERE tenant=%s AND id=%s",
                 (TENANT, uid))
    conn.commit()
    return uid


def _saksversjon(conn, uid: int) -> int:
    sett_tenant(conn, TENANT)
    v = conn.execute("SELECT saksversjon FROM unntak WHERE tenant=%s AND id=%s",
                     (TENANT, uid)).fetchone()[0]
    conn.rollback()
    return v


def _status(conn, uid: int) -> str:
    sett_tenant(conn, TENANT)
    s = conn.execute("SELECT status FROM unntak WHERE tenant=%s AND id=%s",
                     (TENANT, uid)).fetchone()[0]
    conn.rollback()
    return s


def _handle(conn, reg, uid, oh, bid, *, sv=None, idem=None):
    if sv is None:
        sv = _saksversjon(conn, uid)
    idem = idem or f"idem-{uid}-{oh}-{bid}-{secrets.token_hex(2)}"
    ih = hashlib.sha256(
        f"{TENANT}\x1f{bid}\x1f{uid}\x1f{oh}\x1f{sv}".encode()).hexdigest()
    return behandle_unntakshandling(
        conn, _Pool(), reg, tenant=TENANT, aktor=bid, request_id="r",
        unntak_id=uid, operatorhandling=oh, forventet_saksversjon=sv,
        idempotency_key=idem, input_hash=ih, naa=NAA)


def _canary_treff(conn) -> int:
    """Teller forekomster av canary-frasen i det som IKKE skal bære klartekst:
    revisjonsloggens begrunnelse, unntakshistorikken og de krypterte
    payload/intensjon-kolonnene (bytea → tekst)."""
    sett_tenant(conn, TENANT)
    n = 0
    n += conn.execute(
        "SELECT count(*) FROM revisjonslogg WHERE tenant=%s AND"
        " begrunnelse::text LIKE %s", (TENANT, f"%{KANARIFRASE}%")).fetchone()[0]
    n += conn.execute(
        "SELECT count(*) FROM unntak_historikk WHERE tenant=%s AND"
        " coalesce(detalj::text,'') LIKE %s",
        (TENANT, f"%{KANARIFRASE}%")).fetchone()[0]
    # De krypterte kolonnene skal ALDRI inneholde klarteksten.
    n += conn.execute(
        "SELECT count(*) FROM unntak WHERE tenant=%s AND"
        " (encode(payload_kryptert,'escape') LIKE %s OR"
        "  encode(coalesce(handlingsintensjon_kryptert,''::bytea),'escape')"
        "  LIKE %s)",
        (TENANT, f"%{KANARIFRASE}%", f"%{KANARIFRASE}%")).fetchone()[0]
    conn.rollback()
    return n


def _handlinger_med_aktor(conn) -> tuple[int, int]:
    sett_tenant(conn, TENANT)
    tot = conn.execute("SELECT count(*) FROM unntak_historikk WHERE tenant=%s",
                       (TENANT,)).fetchone()[0]
    med = conn.execute("SELECT count(*) FROM unntak_historikk WHERE tenant=%s AND"
                       " aktor IS NOT NULL AND length(btrim(aktor))>0",
                       (TENANT,)).fetchone()[0]
    conn.rollback()
    return med, tot


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ut", default=None)
    args = ap.parse_args(argv)

    kryptering.krev_kek()
    reg = _macreg()

    # Migrer + tenant-DEK.
    m = koble(MIGRATOR)
    migrer(m)
    m.commit()
    m.close()
    boot = koble(DSN)
    sett_kontekst(boot, TENANT, "sys", "r0")
    kryptering.hent_eller_opprett_aktiv_dek(boot, TENANT)
    boot.commit()
    boot.close()
    # Policyer forvaltes av admin-/migratorrollen, ikke runtime.
    pm = koble(MIGRATOR)
    from api import policyregister
    policyregister.registrer(pm, TENANT, POL, "validert_pilot")
    policyregister.registrer(pm, TENANT, POL_FIRE, "validert_pilot")
    pm.commit()
    pm.close()

    op1, op2 = _medlem("op1"), _medlem("op2")
    conn = koble(DSN)
    t0 = time.monotonic()

    maalt = {"avvis": {"injisert": 0, "terminal": 0},
             "godkjenn": {"injisert": 0, "ny_beslutning": 0},
             "sideeffekt": {"injisert": 0, "til_utforelse": 0},
             "fire_oyne": {"injisert": 0, "fullfort": 0}}

    # --- Kategori 1: avvis-vei terminal ---------------------------------
    for i in range(3):
        uid = _injiser(conn, f"avvis-{i}", "beh-mg", POL_HASH)
        maalt["avvis"]["injisert"] += 1
        _handle(conn, reg, uid, "avvis", op1)
        if _status(conn, uid) == "avvist":
            maalt["avvis"]["terminal"] += 1

    # --- Kategori 2: godkjenn-vei gir NY beslutning ---------------------
    for i in range(3):
        uid = _injiser(conn, f"godkj-{i}", "beh-mg", POL_HASH)
        maalt["godkjenn"]["injisert"] += 1
        sett_tenant(conn, TENANT)
        f0 = conn.execute("SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
                          (TENANT,)).fetchone()[0]
        conn.rollback()
        _handle(conn, reg, uid, "godkjenn", op1)
        sett_tenant(conn, TENANT)
        f1 = conn.execute("SELECT count(*) FROM revisjonslogg WHERE tenant=%s",
                          (TENANT,)).fetchone()[0]
        conn.rollback()
        if f1 > f0 and _status(conn, uid) == "venter_utførelse":
            maalt["godkjenn"]["ny_beslutning"] += 1

    # --- Kategori 3: sideeffekt → venter_utførelse (M-37 fullfører til løst)
    # PR-012s HUMAN-vei ender ved `venter_utførelse`: saken er levert til
    # M-37-outboxen, som fullfører til `løst` via en eiermodul sin SIGNERTE
    # kvittering (migrasjon 005 §62) — en egen, allerede-eksisterende prosess
    # (bevist av `feilinjisering-m01` + M-37-testene). Vi måler at
    # godkjenningen faktisk LEVERER saken til outboxen; →løst-steget tilhører
    # M-37 og faked ALDRI her (en status-SET ville vært produsentbevis).
    for i in range(3):
        uid = _injiser(conn, f"side-{i}", "beh-mg", POL_HASH)
        maalt["sideeffekt"]["injisert"] += 1
        _handle(conn, reg, uid, "godkjenn", op1)
        if _status(conn, uid) == "venter_utførelse":
            maalt["sideeffekt"]["til_utforelse"] += 1

    # --- Kategori 4: fire-øyne krever to ULIKE brukere -----------------
    for i in range(3):
        uid = _injiser(conn, f"fire-{i}", "beh-mg-fire", POL_FIRE_HASH)
        maalt["fire_oyne"]["injisert"] += 1
        r1 = _handle(conn, reg, uid, "godkjenn", op1,
                     idem=f"fire-{uid}-op1")
        r2 = _handle(conn, reg, uid, "godkjenn", op2,
                     idem=f"fire-{uid}-op2")
        if (r1.get("utfall") == "venter_andre_godkjenner"
                and r2.get("utfall") == "TILLAT"):
            maalt["fire_oyne"]["fullfort"] += 1

    # --- Hard invariant: saksversjonskonflikt → 409 uten sideeffekt ----
    sv_409 = sv_se = 0
    uid = _injiser(conn, "konflikt", "beh-mg", POL_HASH)
    stale = _saksversjon(conn, uid) + 7
    try:
        _handle(conn, reg, uid, "godkjenn", op1, sv=stale, idem=f"konf-{uid}")
    except Godkjenningsfeil as e:
        if e.kode == "saksversjon_utdatert":
            sv_409 += 1
    conn.rollback()
    if _status(conn, uid) not in ("manuell",):
        sv_se += 1                              # en konflikt SKAL ikke ha sideeffekt

    # --- Hard invariant: to samtidige behandlinger → NØYAKTIG én vinner --
    # Begge tråder starter fra SAMME saksversjon. FOR UPDATE serialiserer:
    # den ene vinner (TILLAT), den andre ser en konflikt (saksversjon_utdatert
    # / ingen_aktiv_runde) og TAPER. Råtellingene bæres i artefaktet; en
    # hengende tråd AVBRYTER — vi kan ikke bevise én vinner uten at begge
    # fullførte.
    konk_uid = _injiser(conn, "samtidig", "beh-mg", POL_HASH)
    konk_sv = _saksversjon(conn, konk_uid)
    ut = {}
    laas = threading.Lock()

    def kappes(navn):
        c = koble(DSN)
        try:
            r = _handle(c, reg, konk_uid, "godkjenn", op1, sv=konk_sv,
                        idem=f"samtidig-{konk_uid}-{navn}")
            vant = r.get("utfall") in ("TILLAT", "venter_andre_godkjenner",
                                       "venter_utførelse")
            with laas:
                ut[navn] = "vant" if vant else "tapte"
        except Exception:                       # taperen ser en konflikt
            with laas:
                ut[navn] = "tapte"
        finally:
            c.close()

    tr = [threading.Thread(target=kappes, args=(n,)) for n in ("a", "b")]
    for t in tr:
        t.start()
    for t in tr:
        t.join(timeout=30)
    if any(t.is_alive() for t in tr):
        raise SystemExit("AVBRUTT: en samtidighetstråd henger etter join —"
                         " kan ikke bevise nøyaktig én vinner")
    samtidig_konkurranser = 1
    samtidig_startet = len(tr)
    samtidig_fullfort = len(ut)
    samtidig_vinnere = sum(1 for v in ut.values() if v == "vant")
    samtidig_tapere = sum(1 for v in ut.values() if v == "tapte")

    canary = _canary_treff(conn)
    med, tot = _handlinger_med_aktor(conn)
    conn.close()

    kategorier_dekket = [k for k, v in maalt.items()
                         if v["injisert"] > 0
                         and list(v.values())[1] == v["injisert"]]
    injisert = sum(v["injisert"] for v in maalt.values())
    m = dict(maalt)
    m.update({
        "kategorier_dekket": kategorier_dekket,
        "saksversjonskonflikt_409": sv_409,
        "saksversjonskonflikt_sideeffekt": sv_se,
        "samtidig_konkurranser": samtidig_konkurranser,
        "samtidig_startet": samtidig_startet,
        "samtidig_fullfort": samtidig_fullfort,
        "samtidig_vinnere": samtidig_vinnere,
        "samtidig_tapere": samtidig_tapere,
        "klartekst_treff": canary,
        "handlinger_med_aktor": med, "handlinger_totalt": tot,
        "varighet_sek": round(time.monotonic() - t0, 3),
    })

    from manifestskjema import _sjekk_grenser, valider_artefaktformat
    # `bestatt` settes provisorisk true, så form- og grensekontrollen har et
    # komplett artefakt å måle; deretter er `bestatt` sann HVIS OG BARE HVIS
    # begge er tomme. Porten regner uansett ut alt på nytt — flagget her er
    # bare produsentens påstand.
    artefakt = {"krav_id": "behandling-m37-v1",
                "ts": datetime.now(timezone.utc).isoformat(), "bestatt": True,
                "oppsett": {"injisert_antall": injisert,
                            "kategorier": list(maalt)},
                "maalt": m}
    formfeil = valider_artefaktformat(artefakt, "behandling-m37-v1")
    grensefeil = _sjekk_grenser("behandling-m37-v1", artefakt)
    artefakt["bestatt"] = not formfeil and not grensefeil

    ut_sti = Path(args.ut) if args.ut else (
        REPO / "deploy/staging/artefakter" / "behandling-m37-v1.json")
    ut_sti.parent.mkdir(parents=True, exist_ok=True)
    ut_sti.write_text(json.dumps(artefakt, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    print(json.dumps(m, ensure_ascii=False, indent=2))
    print(f"[canary] klartekst-treff: {canary}")
    print(f"[form] {formfeil or 'ok'}")
    print(f"[grenser] {grensefeil or 'ok'}")
    print(f"[artefakt] {ut_sti} — bestatt={artefakt['bestatt']}")
    return 0 if artefakt["bestatt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
