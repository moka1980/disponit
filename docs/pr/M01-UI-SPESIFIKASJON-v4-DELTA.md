# M-1 UI-SPESIFIKASJON v4 — DELTA (fire konsistensrettelser → GO)

**Draft: Claude.ai · v1–v3 står. Kort delta, alle fire vedtatt direkte.**

## 1. Låste gyldige kombinasjoner (server håndhever, UI antar aldri)

De fire strukturene i beslutningsdetaljen kan ikke kombineres fritt.
Serveren returnerer KUN disse kombinasjonene; UI validerer at responsen
er én av dem og viser `Feiltilstand` ellers (aldri gjetting):

| policybeslutning | utførelse | kvittering | unntak | sikkerhet |
|---|---|---|---|---|
| TILLAT | IKKE_RELEVANT (sideeffektfri) ELLER VENTER\|UTFØRT\|FEILET | MANGLER→GYLDIG\|SEN\|KONFLIKT (kun når utførelse via outbox) ELLER IKKE_RELEVANT | null | sak_finnes: false |
| STOPP | IKKE_RELEVANT | IKKE_RELEVANT | null | false ELLER true (ved sikkerhetskode) |
| UNNTAK | IKKE_RELEVANT | IKKE_RELEVANT | {id, kategori, status} PÅKREVD | false ELLER true |

Regler som følger av tabellen (server-håndhevet):
- `kvittering ≠ IKKE_RELEVANT` KREVER `utførelse.status ∈ {VENTER,UTFØRT,FEILET}`
  og `policybeslutning = TILLAT` (kvittering finnes kun for outbox-utførelse).
- `unntak ≠ null` ⇔ `policybeslutning = UNNTAK`.
- `utførelse.UTFØRT` uten `kvittering.GYLDIG` er ULOVLIG for outbox-handlinger
  (beslutning≠utførelse, håndhevet i data — ikke bare i visning).
- `sikkerhet.sak_finnes = true` er lovlig sammen med STOPP og UNNTAK, aldri
  påkrevd av policybeslutningen alene.

UI utleder ALDRI en kombinasjon; den vises som mottatt eller avvises.

## 2. Beslutningsdetalj er selvstendig

`GET /v1/beslutninger/{id}`-responsen (v3 pkt. 2) utvides så panelet står
alene uten å låne fra listeraden:
```
+ id, handling, begrunnelse[kodeliste display-safe],
+ revisjonslogg_ref   (auditkorrelasjon: peker til revisjonsloggposten —
                       samme id som listen, så detalj og logg er koblet)
```
Detaljpanelet trenger nå aldri data fra raden som åpnet det — det er
selvstendig og delbart (dyplenke tåler direkte lasting).

## 3. Sikkerhetsinformasjon bak `security:read` — ellers utelatt i v1

`sikkerhet`-strukturen er ikke display-safe for en vanlig tenant-bruker:
- Har tokenet `security:read` → `sikkerhet: {sak_finnes}` inkluderes.
- Ellers → feltet UTELATES helt fra responsen (ikke `false`, ikke maskert
  — fraværende). UI viser da ingen sikkerhetsseksjon.
- v1s vanlige tenant-token har IKKE `security:read`; sikkerhetsinnsyn er
  en egen rolle (ops/compliance). Dermed lekker et ordinært agenttoken
  aldri at en sikkerhetssak finnes.
Samme prinsipp som `exceptions:manage` — innsyn er scope-styrt, ikke
antatt fra tenant-tilhørighet.

## 4. Endepunktinventar korrigert: seks totalt (fem nye + ett eksisterende)

Rettelse av tellefeilen gjennom v2/v3:
| # | Endepunkt | Status |
|---|---|---|
| 1 | `GET /v1/unntak` (liste) | EKSISTERER (main) |
| 2 | `GET /v1/unntak/{id}` (detalj) | NY |
| 3 | `GET /v1/beslutninger` (liste) | NY |
| 4 | `GET /v1/beslutninger/{id}` (detalj) | NY |
| 5 | `GET /v1/oversikt` | NY |
| 6 | `GET /v1/policy/aktiv` | NY |

**Fem nye, ett eksisterende, seks totalt.** Backend-avhengighets-PR-en
dekker de fem nye med samme kontraktdisiplin (scope, cursor der relevant,
kryss-tenant-404, feilmodell) som PR-005b.

## Status: alle punkter lukket
Gyldige kombinasjoner låst ✓ · beslutningsdetalj selvstendig ✓ ·
sikkerhet bak `security:read` ✓ · seks endepunkter korrekt ✓. Ingen åpne.
