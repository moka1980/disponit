# PR-009b — IMPLEMENTERINGSKLARSIGNAL (GO) + BINDENDE ENDRING I PR-009

**Til Claude Code · GO for PR-009b (v1–v3). Branch: `pr-009b-transport`.
⚠️ PR-009 MÅ oppdateres FØR den implementeres ferdig — se §0.**

## 0. ⚠️ Bindende endring i PR-009 (allerede GO-gitt, men endret her)

`disponit-api.service` skal **IKKE** binde TCP `127.0.0.1:8099`. Rettet:
- API-et aktiveres via **systemd `.socket`-unit** på
  `/run/disponit/api.sock`.
- **TCP 8099 åpnes aldri** — porten fjernes fra unit, boot-sjekk,
  `opp.sh`-polling og all dokumentasjon. `/ready`-polling skjer over
  socketen (curl `--unix-socket`).
- Årsak: loopback beviser hvor forbindelsen kom fra, ikke hvem — en
  kompromittert M-37-arbeider kunne ellers sendt falske proxy-headere.
  Tillitsgrensen er filsystemrettigheter.

Er PR-009 allerede bygget mot TCP, er dette en liten, avgrenset endring —
men den er bindende før merge.

## De tre implementeringspresiseringene (bindende)

### V1. systemd `.socket`-unit, ikke app-opprettet socket
```ini
# disponit-api.socket
[Socket]
ListenStream=/run/disponit/api.sock
SocketUser=disponit-api
SocketGroup=disponit-proxy
SocketMode=0660
```
- `/run/disponit/`: eier `disponit-api`, gruppe `disponit-proxy`,
  modus **0750** — nginx får traversalrettighet via gruppen; M-37-brukeren
  er IKKE i gruppen og får ikke traversere.
- Eksplisitt eier/gruppe/modus settes på BÅDE katalogen og socketen.
- API-unit får `Requires=disponit-api.socket` + `After=`.

### V2. ACL-porten tester BEGGE retninger
- **Positiv:** nginx-brukeren kobler til socketen OG fullfører en hel
  request (ikke bare connect).
- **Negativ:** M-37-brukeren OG en ordinær lokal bruker får `EACCES`.
Begge må være egne tester — en port som bare tester én retning beviser
halvparten.

### V3. OIDC-soner ERSTATTER den generelle, stables ikke
- `/v1/oidc/start` og `/v1/oidc/callback` bruker KUN sin egen sone
  (120 r/m, burst 30). Den generelle sonen (600 r/m, burst 100) gjelder
  ikke disse rutene — ellers ville den strammere grensen blitt
  meningsløs eller den generelle utløst først.
- **NAT-testen er reproduserbar:** 200 samtidige klienter fra én kilde-IP,
  60 sekunders varighet, jevn rate → ingen 429 fra den generelle sonen.
  Parametrene skrives i artefaktet.

## Implementeringsomfang (v1–v3 samlet)
nginx foran Unix-socket · ACME-tilstandsmaskin (HTTP-konfig m/ kun
ACME-path → første sertifikat → HTTPS-konfig → `nginx -t` → reload →
ekstern HTTPS-probe) · `ssl_reject_handshake on` i default-server for
ukjent SNI · 421 for kjent SNI + ukjent Host · kanonisk host som
`X-Disponit-Host` (klientsendt strippet) · hardkodet
`X-Forwarded-Proto: https`, alle klientsendte proxy-headere fjernet ·
sesjons-/OIDC-ruter avviser uverifisert transport, `Secure` alltid ·
HTTP-normalisering + smuggling-tester · callback-redaksjon i alle fem
logglag · eksakte TLS-strenger + OpenSSL ≥ 1.1.1 verifisert ved deploy ·
CSP for API nå, UI-CSP i UI-leveransen · HSTS som separat senere port.

## Etter merge
Ekstern HTTPS-probe grønn ⇒ PR-010 kan implementeres (dens e2e nekter å
kjøre uten verifisert HTTPS — Codex-port 12).

---

## Kjeden er nå fullstendig spesifisert og godkjent

| PR | Innhold | Status |
|---|---|---|
| PR-008 | Lese-API (6 endepunkter) | GO, PR #16 under Codex-review |
| PR-009 | Drift: units, helse, deploy, tenant/token | GO (m/ socket-endringen over) |
| PR-009b | Transport: nginx, TLS, tillitsgrense | **GO nå** |
| PR-010 | OIDC-sesjon | GO |
| UI | Komponentbibliotek + fire flater | GO |

Fem leveranser mellom det som står i dag og at Eier logger inn på staging
og ser M-1 virke ende-til-ende.
