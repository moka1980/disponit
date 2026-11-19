# ADR-005: Klasse G — kjernekrok

**Status:** vedtatt 2026-11-19 (Claude Code, med eiers fullmakt «du kan
bestemme arkitektavklaringen»).
**Gjelder:** M-38 (kapasitets-, kø- og modellruter).
**Bygger på:** ADR-002 (D0–D5), særlig klasse D (lesemodul).

## Problemet

M-38s manifest ber selv om denne avklaringen. M-38 v1 skriver ingenting
eget. Modulen er to kroker i kjernen:

1. **en per-tenant plass-rangering** som tredje sorteringsnøkkel i
   `claim_neste_sak` og `claim_neste_oppdrag` (085). Den sørger for at én
   tenants backlog ikke sulter en annens enkeltsak;
2. **en innholdsadressert memoisering** av skjemavandringen i
   `policyregister.hent_aktiv` (`m38.policycache`). Identiske bytes
   valideres én gang.

Klasse D (lesemodul) er nærmest, men en claim **endrer** køen. Det kan
derfor ikke måles som «null endringer i noen tabell».

## Dommen

**G0. Klasse G er «kjernekrok».** Én produsent (`deploy/staging/m38-lest.py`)
måler i den isolerte basen på 55432. Riggen bruker testsuitens egne
hjelpere (`test_m37`, `test_bootstrap_ankerrad`), og de står derfor i
bevisroten, som for M-08.

**G1. Syntetisk datasett.** Fire målinger, hver mot en kjent fasit:

- **Sakskø:** tre tenanter (6 + 1 + 2 saker) dreneres gjennom den ekte
  claim-funksjonen, og rekkefølgen sammenlignes med fasiten.
- **Oppdragskø:** de to første claimene er naboens eneste oppdrag foran
  backloggens andre.
- **Prioritet:** prioriteten går foran fairness. Dette er den negative
  kontrollen for hvor nøkkelen står.
- **Memoisering:** 50 lesinger av samme innhold gir én skjemavandring, og en
  ny versjon gir én til.

**G2. Revisjonslogg: målt fravær.** En claim endrer **køtabellen og
ingenting annet**. Dette måles fra innsiden av transaksjonen med
`pg_stat_xact_user_tables`. Hverken claimene eller lesingene skriver en
revisjonsrad. Claimen selv er allerede auditert av kjernen. M-38 legger
ikke til noen skrivevei, og det er nettopp det som måles.

**G3. Feilinjisering.** Fire injeksjoner, alle med et typet svar:

- ugyldig claim-id;
- `statement_timeout`;
- trukket `EXECUTE`;
- korrupt lagret innhold **etter** et cachetreff.

Ingen injeksjon gir en delvis claim, og cachen gir aldri et gammelt svar.
Den siste injeksjonen viser at rekomputeringen står foran memoiseringen.

**G4. Rollback.** Kjernen rulles. Forgjengerens og kandidatens kode leser
samme policy og drenerer like køer, og må gi like svar, lik fasiten.

**G5. Ytelse.** Claim måles under kølast (500 saker i 10 tenanter), og p95
skal være ≤ 100 ms. Memoiseringens treffrate skal være ≥ 0,99.

**G6. Suiten** omfatter modulens tre testfiler.
