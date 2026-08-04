# PR-007 — IMPLEMENTERINGSKLARSIGNAL (GO fra ChatGPT, fire runder)

**Til Claude Code · Implementér mot v1+v2+v3+v4 fra main 019e06a.
Branch: `pr-007-r1-tofase`. GO + fem vilkår limes inn i PR-beskrivelsen.**

## De fem implementeringsvilkårene (bindende merge-krav)

### V1. Første committede resultat vinner (serialisering)
`registrer_verifikasjonsbevis()` låser generasjonsraden `FOR UPDATE`.
Første gyldige transaksjon som committer avgjør terminal status. Identisk
senere resultathash → idempotent no-op. Enhver ANNEN senere resultathash
→ konfliktevidens. Ingen kodevei forsøker `positiv|negativ|utlopt →
konflikt` (den overgangen finnes ikke — v4 pkt. 2).

### V2. Fast låserekkefølge — dokumentert og deadlock-testet
Claim, ingest og utløpsjobb låser i SAMME rekkefølge:
```
unntak → verifikasjonsgenerasjon → oppdrag → kapabilitet
```
Rekkefølgen dokumenteres i kode (konstant/kommentar på hver låsevei) og
bevises med konkurrerende-transaksjoner-test (blandet claim/ingest/timeout
→ null deadlock).

### V3. Konflikt fryser pågående automatikk atomisk
Konflikt oppdaget etter at retry eller fase 2 har startet — SAMME
transaksjon: sak → `manuell`, ugyldiggjør gjeldende M-37-claim/fencing
(inkrementer claim_generation så gammel claim treffer 0 rader), hindre
utstedelse OG bruk av ny arbeidskapabilitet, sikre at en alt reservert
kapabilitet ikke kan committes etterpå (kapabilitetens revalidering mot
claim_generation i bruk-steget fanger dette), registrer sikkerhetssak med
referanse til BEGGE resultater. **Allerede fullført/auditert beslutning
eller eierutførelse reverseres ALDRI automatisk.**

### V4. Bind eller fjern `ventet_bevis_id`
Valgt: **fjern `unntak.ventet_bevis_id`** — fase 2 henter alltid beviset
via den `positiv` generasjonsraden (kompositt-kontekst, v4 pkt. 3). Ingen
ubundet bekvemmelighetspeker i tillitsankeret. (Alternativet — kompositt-
FK på pekeren — er mer kode for null gevinst når generasjonsraden alt
bærer bindingen.)

### V5. Retry-budsjett uten off-by-one
Maskinell nummerering: første generation = `1`; ny generation tillates
når `generation < maks_auto_forsok_snapshot`; totalt maks
`maks_auto_forsok_snapshot` verifikasjonsoppdrag; verdien ≥ 1 (CHECK).
Grensetester for 1, 2 og 3.

## De ti Codex-mergeportene (hver MÅ ha en test som dreper sin vakt)

1. To samtidige ulike kvitteringer → én vinner, andre → konflikt
2. Ingest vs. utløpsjobb → nøyaktig én terminal generasjonsstatus
3. Retry-claim vs. sen konflikt → konflikt fryser claimen før ny beslutning committes
4. Gammel arbeidskapabilitet kan ikke brukes etter sikkerhetsfrysing
5. To retry-arbeidere → samlet nøyaktig ett generation +1-oppdrag
6. Ingen deadlock under blandet claim/ingest/timeout-last
7. Maks-forsøk grensetester: 1, 2, 3
8. Bevis fra feil tenant/sak/vilkår/generation → avvist av FK (DB, ikke app)
9. Terminal `manuell` gjenåpnes aldri automatisk
10. Sideeffektfull målhandling kan ikke gå direkte til `løst`

## Implementeringsomfang (v1–v4 samlet)

- **Migrasjon 007** (kjøreren eier tx, ingen BEGIN/COMMIT, reviewet checksum):
  statuser `venter_verifikasjon`, `verifikasjon_klar`,
  `verifikasjon_retry_klar` + statusmaskin (v4 pkt. 1); tabeller
  `verifikasjonsgenerasjon` (status aktiv/positiv/negativ/utlopt — INGEN
  konflikt) og append-only `verifikasjonsbevis` (kryptert, kompositt-nøkkel
  `(tenant,unntak_id,vilkaar,generation,id)`); kompositt-FK
  DEFERRABLE INITIALLY DEFERRED; `registrer_verifikasjonsbevis` SECURITY
  DEFINER (FOR UPDATE-lås, atomisk alle utfall); `(handler_id,
  target_action) → utforelsesklasse`-register (CHECK: sideeffektfri XOR
  krever_outbox); FJERN `ventet_bevis_id`.
- **`platform/core/m37/`:** tofaseklassifisering (Grunn-kode: attestasjon→R1,
  verdi→manuell, ukjent/sammensatt→manuell); fase 1 verifikasjonsoppdrag;
  fase 2 ny hendelse = minimert payload + verifisert attestasjon → API,
  respekterer outbox (sideeffekt → venter_utførelse); retry-arbeider
  oppretter generation +1 fenced.
- **`platform/core/oppdragskontrakt.py`:** `vilkaar`-felt på oppdragstype
  `verifikasjon`; `verifikasjonskvittering_v1` (lukket, eneste attestasjon-
  bærer).
- **`api/`:** kvitteringsingest kaller `registrer_verifikasjonsbevis`
  (opptrer aldri som arbeider); `claim_neste_sak` utvidet for
  `verifikasjon_klar` + `verifikasjon_retry_klar` med ny claim/generation.
- **`deploy/staging/`:** syntetisk verifikator (KUN API-endepunkter, null
  direkte DB-skriving) som poster signerte verifikasjonskvitteringer.

## Etter merge → staging → evidens
Bootstrap → migrasjon 007 → full suite → **feilinjiserings-artefakt gjennom
evidensporten**: injiser `manglende_data`/`attestasjon_mangler`, syntetisk
verifikator leverer signert attestasjon (fase 1), fase 2 → TILLAT → løst.
`lost_andel av reparerbare = 1.0` blir oppnåelig for FØRSTE gang. Artefaktet
beviser også de negative avgrensningene (verdi-mangel → manuell; konflikt →
sikkerhet; sideeffekt → venter_utførelse). Grønt artefakt ⇒ m01s
`feilinjisering_til_unntakskø` = `ja`.

## Invarianter 1–10 (PR-006+007-brief) urørt
Null egne fullmakter (fase 1 attesterer, fase 2 går via policy+outbox).
`api/` importerer aldri `m37/`. Én skrivevei til revisjonsloggen. Alle
veier setter `sett_kontekst` først — nå også retry-arbeideren og
ingest-funksjonen. Kvitteringer som ikke kan avslutte → historikk/
konfliktevidens, aldri på oppdragsraden.
