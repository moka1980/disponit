"""Transaksjonell beslutningsvei for API-et (v2 Del 3-5 + v3-delta pkt. 5-6).

behandle() er API-ets ENESTE vei til motoren. Én advisory-låst flyt:
  claim idempotensnøkkel -> signatur+bindingsport -> policy fra register
  -> evaluate -> jti-konsumering (irreversible) -> routing (sakstype)
  -> minimér+kryptér payload -> unntaksrad -> loggpost m/ evidensfelter
  -> idempotens ferdig -> commit. Alle feilveier: STOPP + definert
  HTTP-status per feilveitabellen. Kalleren utfører sideeffekt HVIS OG
  BARE HVIS retur er TILLAT.
NB: draft — Claude Code implementerer mot v2 Del 4-tabellen linje for
linje; skjelettet her viser transaksjonsgrensene og rekkefølgen som
Codex skal verifisere (port 1).
"""
from __future__ import annotations
# Implementasjonsskjelett — fylles av Claude Code i PR-005:
#
# def behandle(conn, ctx, policy_id, event, idempotency_key, request_id,
#              nokler, naa) -> (http_status, Decision, unntak_id | None):
#   1  SET LOCAL disponit.tenant / disponit.aktor / disponit.request_id
#   2  pg_advisory_xact_lock(hash(tenant|idempotency_key))
#   3  INSERT idempotens ... ON CONFLICT DO NOTHING RETURNING
#      - tapt: les rad; input_hash lik -> (200, lagret respons); ulik -> 409
#      - paagaar m/ ledig lås = krasjet vinner -> re-claim (UPDATE request_id)
#   4  attestering.kontroller_hendelse + kontroller_binding (ctx, handling,
#      policy_id, naa) -> brudd: sakstype='sikkerhet'-sak + loggpost + STOPP
#   5  policy: SELECT innhold, innholds_hash FROM policyer WHERE tenant=ctx
#      AND policy_id=%s AND aktiv -> mangler: 404; re-valider skjema ->
#      korrupt: 500 drift+m37
#   6  evaluate(policy, ctx, event, teller=PgTellerLager(conn), naa)
#   7  TILLAT + irreversibel handling: INSERT attestasjon_jti per attestasjon
#      -> unikbrudd: replay -> sikkerhetssak + STOPP
#   8  TILLAT + frekvensreservasjon: betinget INSERT (mønster fra PR-004)
#   9  routing per v2 Del 4 (m/ v3 sakstype): trenger sak ->
#      minimer_payload -> hent_eller_opprett_aktiv_dek -> krypter ->
#      INSERT unntak (FK loggpost skrives FØRST i samme tx)
#  10  INSERT revisjonslogg m/ handling, request_id, idempotency_key,
#      policy_content_hash, attestation_set_hash
#  11  UPDATE idempotens SET status='ferdig', respons=... ; commit
#  Feil hvor som helst etter 3: rollback -> nødlogg (uten payload) ->
#  STOPP/'logging_feilet'-kontrakten fra v2 4.1.
