"""FastAPI-skjelett (v2 Del 3). Claude Code implementerer mot spesifikasjonen.

BOOT-SJEKKER (prosessen NEKTER start hvis noen feiler):
  - last_nokler() gyldig            (attestasjonsregister)
  - DISPONIT_KEK gyldig             (kryptering._kek())
  - DB nåbar + migrasjonsversjon [1,2,3]
  - bind-adresse == loopback ELLER DISPONIT_TLS_AKTIV=1
ENDEPUNKTER: POST /v1/beslutning (decision:write, Idempotency-Key påkrevd),
GET /v1/unntak (exceptions:read, kun sakstype=normal, kun metadata,
signert keyset-cursor), GET /live, GET /ready (kun localhost).
MIDDLEWARE: byte-tellende body-grense 256 KiB (chunked-sikker),
token-auth via verifiser_token(token_id, HMAC(pepper, secret)),
rate-grense per token (i-minne, deklarert svakhet), request_id-generering.
Alle feilveier mapper til v2 Del 4-tabellen. Ingen stack traces ut.
"""
