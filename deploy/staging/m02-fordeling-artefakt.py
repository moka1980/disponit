#!/usr/bin/env python3
"""Staging-leddet for m02-fordelingsartefaktet (m02-aksept §3).

Driver NØYAKTIG samme sett som CI-leddet (`m02_fordeling.bygg_sett`)
gjennom den ekte beslutningsveien på disponit-srv — egen testtenant,
egen policy, ekte attestasjonssignering mot API-ets nøkkelmiljø — og
skriver artefaktet fra radene i `revisjonslogg`.

BRUK (på verten, som root, med API-et oppe):
    /opt/disponit/.venv/bin/python deploy/staging/m02-fordeling-artefakt.py \
        --api https://disponit.com \
        [--tenant t-m02fordeling] [--verifikator v_fordring]
        [--nokkel-id k1] [--ut deploy/staging/artefakter/...json]

Verifikatoren må finnes i API-tjenestens DISPONIT_ATT_NOKLER — skriptet
leser NØYAKTIG de nøklene den kjørende tjenesten har lastet (se
`_verdi`) og NEKTER å starte hvis den ikke gjør det, i stedet for å
drive 84 hendelser som alle blir STOPP: et sett som ikke er settet,
telles ikke.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import secrets
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

spec = importlib.util.spec_from_file_location(
    "m02_fordeling", Path(__file__).with_name("m02_fordeling.py"))
lib = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lib)

MILJOFILER = ("/etc/disponit/staging.env", "/etc/disponit/wcag/konfig")

#: Verifikatoren bransjemalen bruker for purring.send-vilkårene.
MAL_VERIFIKATOR = "v_fordring"

#: TILLITSGRENSEN (K2-beslutningen i #131, spec i issue #132):
#: artefaktet beviser KJØRINGEN — settet, byggerne og policyen som
#: innsjekkede bytes (bevisrot_sha256) — ALDRI verten. Ingen preflight
#: leser API-ets credentials: ingen lesning lukker en TOCTOU, og
#: 84 TILLAT ER nøkkelbeviset — signerer vi med en annen nøkkel enn den
#: API-et faktisk har, feller den fail-closed driveren settet på første
#: TILLAT-hendelse som kommer tilbake som STOPP. Dypere binding
#: (API-runtime, credential-snapshots, importerte biblioteker) er
#: EKSPLISITT utenfor grensen: vertens tilstand bindes av
#: deploymentkjeden (release → digest → CI-attest), som er
#: akseptmaskineriets jobb — ikke dette artefaktets. Neste review som
#: åpner det sporet: les #131-beslutningen først.


def _miljo() -> dict:
    ut: dict[str, str] = {}
    for sti in MILJOFILER:
        p = Path(sti)
        if not p.exists():
            continue
        for linje in p.read_text(encoding="utf-8").splitlines():
            linje = linje.strip()
            if linje and not linje.startswith("#") and "=" in linje:
                k, _, v = linje.partition("=")
                ut.setdefault(k.strip(), v.strip().strip('"'))
    return ut


def _verdi(navn: str, miljo: dict) -> str:
    """Eksplisitt miljø vinner; ellers miljøfilen. Dette er skriptets
    EGNE inndata (hva DET signerer med) — aldri en kontroll av hva
    API-et har: den målingen er de 84 TILLAT-svarene (tillitsgrensen
    øverst i fila)."""
    return os.environ.get(navn) or miljo.get(navn, "")


def bytt_verifikator(policy: dict, fra: str, til: str) -> dict:
    """Pek vilkårene fra `fra` til `til` i den PARSEDE policyen.

    Formen før dette var `yaml.safe_dump(policy).replace(fra, til)` fulgt
    av `safe_load`. Er `til` alt en verifikator i malen — og `v_bank`,
    `v_regnskap`, `v_dlp` er det — ga byttet TO mappingnøkler med samme
    navn: `safe_load` beholder den siste i stillhet, den ekte
    verifikatorens `betrodd_for` forsvant, og `valider_ny_policy` avviste
    policyen («ikke betrodd for vilkår») før settet i det hele tatt fikk
    kjøre. Tekstbyttet traff dessuten hver forekomst av strengen, også
    inne i beskrivelser.

    SP-13/K4: en fremmed grammatikk endres i sin PARSEDE form, aldri som
    tekst. Her betyr det: flytt vilkårene, og UTVID målverifikatorens
    tillitserklæringer i stedet for å skrive over dem.
    """
    if fra == til:
        return policy
    flyttet: set[str] = set()
    for h in policy.get("handlinger") or []:
        for vk in h.get("vilkaar") or []:
            if vk.get("verifikator") == fra:
                vk["verifikator"] = til
                flyttet.add(vk["navn"])
    verifikatorer = policy.setdefault("verifikatorer", {})
    ny = verifikatorer.setdefault(
        til, {"beskrivelse": f"m02-fordeling: signerbar erstatning for {fra}",
              "betrodd_for": []})
    ny["betrodd_for"] = sorted(set(ny.get("betrodd_for") or []) | flyttet)
    return policy


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--tenant", default="t-m02fordeling")
    ap.add_argument("--verifikator", default="v_fordring")
    ap.add_argument("--nokkel-id", default="k1")
    ap.add_argument("--ut", type=Path)
    a = ap.parse_args()

    miljo = _miljo()
    for n in ("DISPONIT_TOKEN_PEPPER", "DISPONIT_ATT_NOKLER",
              "DISPONIT_MIGRATOR_URL"):
        os.environ[n] = _verdi(n, miljo)
        if not os.environ[n]:
            raise SystemExit(f"AVBRUTT: {n} mangler — verken satt i"
                             f" miljøet eller i {MILJOFILER}")
    nokler = json.loads(os.environ["DISPONIT_ATT_NOKLER"])
    if a.verifikator not in nokler \
            or a.nokkel_id not in nokler[a.verifikator]:
        raise SystemExit(
            f"AVBRUTT: verifikator {a.verifikator!r}/{a.nokkel_id!r} står"
            " ikke i API-ets DISPONIT_ATT_NOKLER — velg en som finnes"
            f" ({sorted(nokler)}); å drive settet med en usignerbar"
            " attestant gir 84 STOPP, ikke fordelingen")
    hemmelighet = nokler[a.verifikator][a.nokkel_id]

    from db.pg import koble
    from policy_validator import attestering
    from api import policyregister
    import yaml
    import hashlib
    import hmac

    m = koble(os.environ["DISPONIT_MIGRATOR_URL"])

    # Tenant + policy + token — samme former som test_api sine fixturer,
    # mot staging-basen. Policyen er bransjemal-tjenestebedrift med
    # verifikatoren byttet til den som faktisk KAN signere her.
    #
    # DEK-en lages IKKE her. API-et kaller `hent_eller_opprett_aktiv_dek`
    # ved første unntaksskriv og pakker den med sin egen KEK; en
    # plassholderrad hadde sett aktiv ut for det oppslaget, og `_pakk_ut`
    # kan ikke AES-GCM-dekryptere en nullbyte — den første STOPP-en hadde
    # felt kjøringen, ikke fordelingen. (Har tenanten allerede en aktiv
    # DEK under et annet key_id, ville raden dessuten brutt
    # `en_aktiv_dek_per_tenant`.) Bootstrap er API-ets jobb, med den
    # ekte KEK-en.
    m.execute("SELECT set_config('disponit.tenant', %s, false),"
              " set_config('disponit.aktor', 'm02-fordeling', false)",
              (a.tenant,))
    p = yaml.safe_load((REPO / "policies/bransjemal-tjenestebedrift.yaml"
                        ).read_text(encoding="utf-8"))
    bytt_verifikator(p, MAL_VERIFIKATOR, a.verifikator)
    policyregister.registrer(m, a.tenant, p, p["meta"]["status"])
    m.commit()
    pid = p["meta"]["policy_id"]

    token_id = "tk_" + secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    mac = hmac.new(os.environ["DISPONIT_TOKEN_PEPPER"].encode(),
                   secret.encode(), hashlib.sha256).hexdigest()
    m.execute("INSERT INTO api_tokener (token_id, tenant, rolle, scopes,"
              " secret_mac, status) VALUES (%s,%s,'agent',"
              " %s,%s,'AKTIV')",
              (token_id, a.tenant, ["decision:write", "exceptions:read"],
               mac))
    m.commit()
    bearer = f"{token_id}.{secret}"

    def attestasjon(vilkaar, ressurs, verdi=None):
        naa = datetime.now(timezone.utc)
        att = {"verifikator": a.verifikator, "tenant_id": a.tenant,
               "handling": "purring.send", "vilkaar": vilkaar,
               "ressurs_id": ressurs, "policy_id": pid,
               "utstedt": (naa - timedelta(minutes=5)).isoformat(),
               "utloper": (naa + timedelta(hours=1)).isoformat(),
               "jti": f"{vilkaar[:4]}-{ressurs}-"
                      + secrets.token_hex(12),
               "resultat": True}
        if verdi is not None:
            att["verdi"] = verdi
        return attestering.signer(att, a.nokkel_id, hemmelighet)

    def hendelse(ressurs):
        return {"handling": "purring.send", "ressurs_id": ressurs,
                "faktura_id": ressurs, "dataklasser": ["finansiell"],
                "dataklasser_kilde": "connector",
                "attestasjoner": {
                    "forfall_passert_dager": attestasjon(
                        "forfall_passert_dager", ressurs, verdi=20),
                    "ingen_aktiv_tvist": attestasjon(
                        "ingen_aktiv_tvist", ressurs)}}

    def hendelse_uten(handling, ressurs):
        return {"handling": handling, "ressurs_id": ressurs,
                "faktura_id": ressurs, "dataklasser": ["finansiell"],
                "dataklasser_kilde": "connector"}

    def tukle(e):
        e["attestasjoner"]["ingen_aktiv_tvist"]["resultat"] = False
        return e

    def post(e, nokkel):
        req = urllib.request.Request(
            a.api.rstrip("/") + "/v1/beslutning",
            data=json.dumps({"policy_id": pid, "event": e}).encode(),
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {bearer}",
                     "idempotency-key": nokkel},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read()).get("beslutning")
        except urllib.error.HTTPError as e2:
            return e2.code, None

    runde = secrets.token_hex(4)
    # Pacing under nginx-sonen (600r/m, burst 100): 0,15 s/kall ≈ 6,7/s
    # gir ~27 s for settet — grensen er produktets, ikke en ulempe.
    talt = lib.kjor_sett(runde, post, hendelse, hendelse_uten, tukle,
                         pause_s=0.15)
    print(f"drevet: {talt}")

    m.execute("SELECT set_config('disponit.tenant', %s, false)",
              (a.tenant,))
    rader = m.execute(
        "SELECT id, beslutning FROM revisjonslogg WHERE tenant=%s"
        "  AND idempotency_key LIKE %s",
        (a.tenant, f"m02f-{runde}-%")).fetchall()
    m.rollback()
    ts = datetime.now(timezone.utc).isoformat()
    from manifestskjema import m02_bevisrot_sha256
    art = lib.artefakt([(r[0], r[1]) for r in rader], a.tenant,
                       a.api, ts, m02_bevisrot_sha256())
    ut = a.ut or (REPO / "deploy/staging/artefakter" /
                  f"m02-fordeling-v1-{ts[:19].replace(':', '').replace('-', '')}.json")
    ut.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                             sort_keys=True) + "\n", encoding="utf-8")
    print(f"skrev {ut} (bestatt={art['bestatt']})")
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
