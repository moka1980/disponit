"""M-37 — behandlingsmotoren for unntakskøen (PR-006).

Pakken kjører som EGEN PROSESS (`m37.arbeider`), aldri i API-prosessen.
Det er ikke en stilpreferanse: ytelsesporten `perf-m01-v1` er målt med 3,4×
spredning (p95 24–82 ms) og p99 207 ms, og verste kjøring brukte 55 % av
budsjettet. Arbeid lagt inn i forespørselsveien spiser nøyaktig den
marginen.

Regelen håndheves som en statisk sjekk i testsuiten:
`api/` importerer aldri `m37/`. Er den lenken der, er prosessgrensen en
påstand og ikke en egenskap.

Null-fullmaktsprinsippet gjelder gjennom hele pakken: M-37 utfører aldri en
forretningshandling selv og omgår aldri motoren. Den kan bare BE om nye,
policystyrte beslutninger gjennom det ordinære API-et — og legge ut
oppdrag som en eiermodul med egne fullmakter kan hente.
"""
