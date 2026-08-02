# Rutineendringer vedtatt av Claude.ai (arkitekt), 2026-08-02

Legges inn i `docs/RUTINER.md` av Claude Code i PR-005 (docs-delen).

## Endring 1: Spesifikasjonsporten (steg 2) er obligatorisk — presisert

Erstatt beskrivelsen av steg 2 i pkt. 2 med:

> **2. Spesifikasjonsreview (ChatGPT) — OBLIGATORISK for alle PR-er som
> rører `platform/`, `policies/` eller `deploy/`.** Claude.ai sender
> draften (spesifikasjon eller kode) til ChatGPT FØR Claude Code starter
> implementering. Review-svaret limes inn i PR-beskrivelsen. Kun PR-er
> som utelukkende endrer `docs/` kan hoppe over porten, og da skal
> PR-beskrivelsen si det eksplisitt med begrunnelse.
>
> Historikk: porten ble hoppet over i PR-003 (forsvarlig, ren docs) og
> PR-004 (ikke forsvarlig — tillitsankerets tilstandslag). Codex og
> Claude Code fanget tolv P1 i PR-004-rundene, men porten foran skal
> redusere antallet som når dit. Denne presiseringen finnes fordi
> arkitekten brøt sin egen rutine; regelen gjelder Claude.ai mest av alle.

## Endring 2: Bootstrap-unntak i «ferdig før neste» (pkt. 2)

Legg til etter regelen om at en modul skal være helt ferdig før neste:

> **Bootstrap-unntak (kun fase 1-plattformmoduler):** M-1, M-2, M-37 og
> M-38 er gjensidig avhengige — m01 kan f.eks. ikke bestå
> `feilinjisering_til_unntakskø` før M-37 finnes, og M-37 kan ikke bygges
> uten M-1. For disse fire gjelder «ferdig før neste» på KJEDENIVÅ:
> de bygges i samspill, og ingen fase 2-modul startes før ALLE fire har
> bestått hele sin staging-sjekkliste. Regelen som aldri fravikes:
> en modul settes ikke til `aktiv` i registeret før alle sjekklistepunkter
> er ja — blokkerte punkter markeres `blokkert_av: <modul>` i manifestet,
> ikke som ja. Fra fase 2 gjelder regelen bokstavelig per modul.

## Endring 3: Ytelsesgrensen for m01 er definert (manglet i manifestet)

`ytelse_bestatt` for m01 defineres som: **100 beslutninger/sekund
vedvarende i 60 sekunder mot staging-PostgreSQL med 20 samtidige
tilkoblinger, p95-latens under 150 ms, null feil og null tapte
loggposter (1:1 beslutning↔loggpost verifisert etter kjøringen).**
Grensen er satt for Cloud Server S (2 vCPU) og justeres ved målt behov —
men den er nå definert, målbar og en del av manifestet.

## Endring 4: Sjekklisteformat i manifester

`staging_sjekkliste`-verdier utvides fra ja/nei til:
`ja | nei | blokkert_av: <modul-id>`. m01 oppdateres tilsvarende:
`feilinjisering_til_unntakskø: blokkert_av: m37`,
`rollback_testet: blokkert_av: m37` (krever unntakskø for trygg
deaktiveringstest), `ytelse_bestatt: nei` (grense definert, kjøres på
staging i PR-005-runden).
