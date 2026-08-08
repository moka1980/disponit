#!/usr/bin/env bash
# ============================================================
# init-tenant.sh <tenant-id> [bransjemal.yaml]  (PR-009 v1 §4 + v2 §5)
#
# Atomisk, idempotent tenantinit under TENANTBUNDET advisory lock:
# DEK + aktiv policy i ÉN kontrollert arbeidsflyt. Eksisterende tenant
# VALIDERES — aldri blind overskriving, og ALDRI automatisk tokenutstedelse
# (mistet hemmelighet er roter-token.sh sitt eksplisitte domene;
# førstegangsutlevering er bootstrap-token.sh sitt).
# ============================================================
set -euo pipefail

TENANT="${1:?bruk: init-tenant.sh <tenant-id> [bransjemal.yaml]}"
MAL="${2:-/opt/disponit/aktiv/policies/bransjemal-tjenestebedrift.yaml}"

set -a; . /etc/disponit/staging.env; set +a

exec /opt/disponit/.venv/bin/python - "$TENANT" "$MAL" <<'PY'
import hashlib
import sys

sys.path.insert(0, "/opt/disponit/aktiv/platform/core")
import psycopg
import yaml

from api import policyregister
from db import kryptering

tenant, malsti = sys.argv[1], sys.argv[2]
import os
conn = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])

# Tenantbundet advisory lock (v2 §5): to samtidige init av SAMME tenant
# serialiseres; ulike tenanter går parallelt.
laas = int.from_bytes(hashlib.sha256(tenant.encode()).digest()[:8],
                      "big", signed=True)
conn.execute("SELECT pg_advisory_xact_lock(%s)", (laas,))

conn.execute("SELECT set_config('disponit.tenant',%s,true),"
             "       set_config('disponit.aktor','init-tenant',true)",
             (tenant,))

# 1. DEK: valider eksisterende, opprett bare hvis den mangler.
rad = conn.execute("SELECT count(*) FROM tenant_nokler WHERE tenant=%s"
                   " AND wrapped_dek IS NOT NULL", (tenant,)).fetchone()
if rad[0] == 0:
    kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
    print(f"DEK: opprettet for {tenant}")
else:
    print(f"DEK: finnes ({rad[0]} nøkler) — urørt")

# 2. Policy: aktiv policy valideres; mangler den, lastes bransjemal
#    (skjema v0.2-validering skjer i policyregister-veien).
policy = yaml.safe_load(open(malsti, encoding="utf-8"))
pid = policy["meta"]["policy_id"]
aktiv = conn.execute("SELECT versjon FROM policyer WHERE tenant=%s"
                     " AND policy_id=%s AND aktiv",
                     (tenant, pid)).fetchone()
if aktiv is None:
    policyregister.registrer(conn, tenant, policy, policy["meta"]["status"])
    print(f"policy: {pid} lastet og aktivert")
else:
    print(f"policy: {pid}@{aktiv[0]} er aktiv — urørt")

# 3. Tokens: KUN validering/rapport — utstedelse er bootstrap-token.sh.
tokens = conn.execute(
    "SELECT rolle, status, count(*) FROM api_tokener WHERE tenant=%s"
    " GROUP BY rolle, status ORDER BY rolle, status", (tenant,)).fetchall()
if tokens:
    for rolle, status, antall in tokens:
        print(f"token: {rolle}/{status}: {antall}")
else:
    print("token: ingen — kjør bootstrap-token.sh for førstegangsutlevering")

conn.commit()
conn.close()
print(f"init-tenant: {tenant} ok")
PY
