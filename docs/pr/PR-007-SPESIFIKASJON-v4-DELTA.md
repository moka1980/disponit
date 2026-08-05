# PR-007 SPESIFIKASJON v4 — DELTA (tre presiseringer → GO)

**Draft: Claude.ai · Modell (b) + v2 + v3 står. Reviewens anbefalte
løsninger vedtatt direkte. Tre punkter, alle på generasjonens ulykkelige
veier.**

## 1. Retry-aktør + kjørbar overgang: status `verifikasjon_retry_klar`

v3s hull: ingen komponent var autorisert til å opprette neste
verifikasjonsoppdrag — ingest er ikke arbeider, og en sak i
`venter_verifikasjon` så fortsatt ut som om den ventet på forrige
oppdrag. Løsning (reviewens anbefaling, eksplisitt status):

Ny ikke-terminal status `verifikasjon_retry_klar`. Ingest/utløpsjobb ved
negativt eller utløpt resultat avslutter generation atomisk og setter:
- `verifikasjon_retry_klar` dersom retry-budsjett gjenstår
  (verification_generation < maks_auto_forsok_snapshot), ELLER
- `manuell` dersom budsjettet er brukt.

`claim_neste_sak()` utvides til å claime `verifikasjon_retry_klar` med NY
claim_id + inkrementert claim_generation. Den claimede M-37-arbeideren —
og KUN arbeideren — oppretter generation +1 og nytt verifikasjonsoppdrag
i ÉN fenced transaksjon, og setter saken tilbake til
`venter_verifikasjon`. Ingest oppretter ALDRI oppdrag.

Utløpsjobben bruker NØYAKTIG samme overgang (atomisk, idempotent,
auditert) — den setter kun `aktiv → utlopt` + retry-klar/manuell, aldri
oppdragsopprettelse. Statusmaskin komplett:
```
under_behandling      -> venter_verifikasjon | venter_utførelse | løst | avvist | manuell
venter_verifikasjon   -> verifikasjon_klar | verifikasjon_retry_klar | manuell
verifikasjon_klar     -> under_behandling            (fase 2, ny claim)
verifikasjon_retry_klar -> under_behandling          (retry, ny claim)
```

Bindende test: negativ kvittering m/ budsjett → nøyaktig ett nytt oppdrag;
to arbeidere konkurrerer om retry → kun én oppretter generation +1
(fencing).

## 2. Konfliktsemantikk: terminal status endres aldri

v3s feil: `positiv → konflikt` er en overgang triggeren avviser, og en
sen konflikt kan ikke få ugyldiggjøre et bevis fase 2 alt bruker.
Bindende løsning (reviewens):

- **Terminal generasjonsstatus endres ALDRI etter første aksepterte
  resultat.** Tillatte overganger forblir `aktiv → positiv|negativ|utlopt`.
  `konflikt` er IKKE lenger en generasjonsstatus (fjernes fra CHECK).
- Konflikt oppdaget mens generation er `aktiv` (to ulike resultater før
  noen er akseptert) → generation `negativ` + konfliktevidens + sikkerhet.
- Motstridende kvittering ETTER akseptert resultat: lagres som
  **append-only konfliktevidens/sikkerhetshendelse**, generation forblir
  `positiv|negativ|utlopt` uendret.
  - Fase 2 ikke utført ennå → videre automatikk fryses fail-closed:
    saken → `manuell` (eller eksplisitt sikkerhetsstatus), sikkerhetssak
    refererer både opprinnelig bevis og konflikthendelse.
  - Fase 2 allerede utført → forretningsresultatet endres ALDRI
    automatisk; sikkerhetssak opprettes med referanse til begge.
- Identisk kvittering (samme resultathash) → idempotent no-op.

Bindende test: konflikt etter positivt bevis endrer ikke terminal
generasjonsstatus og erstatter ikke beviset; konflikt før fase 2 fullført
stopper videre automatikk; utløpsjobb og sen kvittering konkurrerer →
nøyaktig én terminal generasjonsbeslutning vinner.

## 3. Kompositt referanseintegritet — DB-håndhevet, ikke bare i kode

`bevis_id` + `ventet_bevis_id` alene tillater kryss-tenant/-generation-
referanser utenfor funksjonskoden. Krav:

```sql
-- Bevis får sammensatt unik nøkkel
ALTER TABLE verifikasjonsbevis
  ADD CONSTRAINT bevis_komposittnokkel
  UNIQUE (tenant, unntak_id, vilkaar, generation, id);

-- Generasjonstabellen binder til beviset med FULL kontekst
ALTER TABLE verifikasjonsgenerasjon
  ADD CONSTRAINT gen_bevis_fk
  FOREIGN KEY (tenant, unntak_id, vilkaar, generation, bevis_id)
  REFERENCES verifikasjonsbevis (tenant, unntak_id, vilkaar, generation, id)
  DEFERRABLE INITIALLY DEFERRED;         -- for atomisk ingest-rekkefølge
```
- Fase-2-claimen henter beviset VIA generasjonsraden (den `positiv`
  generasjonen med matchende kontekst), IKKE via et ubundet
  `ventet_bevis_id`. `unntak.ventet_bevis_id` beholdes kun som
  bekvemmelighetspeker, aldri som eneste integritetsgrunnlag.
- FK er `DEFERRABLE INITIALLY DEFERRED` slik at ingest kan INSERT-e bevis
  og sette generation `positiv` med `bevis_id` i samme transaksjon uten
  rekkefølgeproblem.
- Bindende test: DB avviser bevisreferanse til feil tenant, sak, vilkår
  eller generation — bevist mot databasen, ikke bare applikasjonslaget.

## Status etter v4

| Punkt | Status |
|---|---|
| Bevis skilt fra muterbar generasjon | Lukket (retry + konflikt nå komplett) |
| Atomisk ingest | Lukket (alle utfall: positiv/negativ/utløpt/konflikt) |
| Ingen hash-orakel | Lukket (v3) |
| Utførelsesklasse per målhandling | Lukket (v3) |
| Kompositt referanseintegritet | Lukket (DB-håndhevet) |
