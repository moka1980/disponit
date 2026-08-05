# PR-007 SPESIFIKASJON v3 — DELTA (fire presiseringer → GO)

**Draft: Claude.ai · Modell (b) + v2 står. Reviewens anbefalte løsninger
vedtatt direkte. Fire punkter.**

## 1. Skill bevis (append-only) fra generasjonstilstand (muterbar, auditert)

v2s feil: append-only tabell + `WHERE status='aktiv'`-delindeks blokkerer
alle fremtidige generasjoner permanent. Løsning — to tabeller:

**`verifikasjonsgenerasjon`** (muterbar status, trigger-håndhevet):
```sql
CREATE TABLE verifikasjonsgenerasjon (
  tenant TEXT NOT NULL, unntak_id BIGINT NOT NULL, vilkaar TEXT NOT NULL,
  generation INT NOT NULL,
  status TEXT NOT NULL CHECK (status IN
    ('aktiv','positiv','negativ','utlopt','konflikt')) DEFAULT 'aktiv',
  bevis_id BIGINT,                       -- kun ved status='positiv'
  opprettet TIMESTAMPTZ NOT NULL DEFAULT now(),
  status_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, unntak_id, vilkaar, generation),
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id)
);
CREATE UNIQUE INDEX en_aktiv_generasjon_per_sak_vilkaar
  ON verifikasjonsgenerasjon (tenant, unntak_id, vilkaar) WHERE status='aktiv';
```
- Overganger (trigger, auditert): `aktiv → positiv|negativ|utlopt|konflikt`;
  alle fire er terminale for generasjonen. `positiv` krever ikke-null
  `bevis_id`. Statusskifte skriver historikk.
- **`verifikasjonsbevis` forblir append-only** — kun `positiv` generasjon
  peker på nøyaktig ett bevis. Delindeksen ligger nå på
  generasjonstabellen, ikke beviset → nye generasjoner tillates når
  forrige er terminal, uten å røre append-only-beviset.

**Ny generation:** monoton +1, kan opprettes KUN fra en ikke-terminal
retrytilstand — `venter_verifikasjon` med forrige generasjon i
`negativ|utlopt`. IKKE fra `manuell`. `manuell` forblir terminal; en
generasjon som ender `negativ|utlopt|konflikt` sender saken til
`manuell` HVIS retry-budsjettet (maks_auto_forsok_snapshot) er brukt,
ellers til ny generasjon. Administrativ gjenåpning fra `manuell` er
UTENFOR PR-007 (deklarert — krever egen auditert prosedyre).

`fase1_id` inkluderer `generation` (v2 pkt. 3 uendret). Faseidentitet og
generasjonstabell er nå konsistente.

## 2. Atomisk bevis-ingest — én udelelig DB-transaksjon

`registrer_verifikasjonsbevis(konvolutt_verifisert JSONB)` gjør ALT i én
transaksjon eller ingenting (krasj på ethvert punkt → full rollback):

1. Utled tenant/sak/generation/vilkår/ressurs/oppdrag SERVER-SIDE fra
   oppdrags- og saksradene. Den signerte konvolutten er
   SAMMENLIGNINGSGRUNNLAG, ikke autoritativ kilde — hvert felt matches
   mot DB; avvik → sikkerhetssak, ingen bevisrad.
2. Valider gjeldende oppdrag + owner-fencing (owner_claim_id/generation).
3. Idempotens på (oppdrag_id, resultathash): identisk → no-op retur;
   ANNEN gyldig resultathash for samme generation → `konflikt`-status +
   sikkerhetssak, saken uendret.
4. Kun `attestert_resultat=positiv` OG `now() < utloper` → fortsett mot
   `verifikasjon_klar`. Negativt → generasjon `negativ`, saken `manuell`
   (eller ny generasjon per pkt. 1). Utløpt → generasjon `utlopt`, samme.
5. INSERT kryptert bevis (append-only), sett generasjon `positiv` +
   `bevis_id`, sett `unntak.ventet_bevis_id`, saken → `verifikasjon_klar`,
   skriv historikk.

Alt i funksjonen (SECURITY DEFINER, NOLOGIN-eier, search_path=pg_catalog).
Ingen delvis commit mulig — app-laget gjør ALDRI statusskiftet separat.
Signaturverifikasjonen skjer i app-laget (nøkkelregisteret bor der);
DB-funksjonen mottar den ferdig-verifiserte konvolutten og re-kontrollerer
alle DB-bindingene.

## 3. Integritetshash uten orakel-lekkasje

v2s `integritet_hash` (ren SHA-256 over klartekst) er et orakel når
attestasjonen har få utfall (sann/usann). Rettelse:
- Lagringsintegritet: hash over CIPHERTEXT (ikke klartekst) — beskytter
  mot bit-flipping i lagring, lekker ingenting om innholdet.
- Klartekstintegritet: AES-256-GCM-taggen (finnes allerede i envelope) —
  autentiserer klartekst ved dekryptering.
- Trengs deterministisk dedup på klartekst: HMAC med server-pepper
  (samme pepper-disiplin som token-MAC — i app-miljø, ALDRI i DB).
En DB-dump alene kan dermed ikke gjette attestasjonen. Pepper og DEK/KEK
forlater aldri kryptolaget.

## 4. Sideeffektfri gjelder (handler, target_action)-paret

Utførelsesklasse er entydig per KONKRET målhandling, ikke per handler:
- Register `(handler_id, target_action) → utforelsesklasse` (lukket,
  CI-validert): `sideeffektfri | krever_outbox`. De to kan ALDRI være
  sanne samtidig for samme par (CHECK i registeret).
- Ukjent `(handler_id, target_action)` → fail-closed `manuell`.
- M-37 kan verken velge eller overstyre klassen — den er data, slått opp.
- Fase 2: `TILLAT` + `sideeffektfri` → `løst`; `TILLAT` + `krever_outbox`
  → oppdrag + `venter_utførelse`, kun eierkvittering setter `løst`.
- Bindende: CI-test + negativ runtime-test beviser at en sideeffektfull
  målhandling ALDRI går direkte til `løst`.

## Bindende tester (reviewens liste, vedtatt)

Krasj på hvert punkt i ingest → full commit eller ingen endring ·
positiv generasjon blir terminal og frigjør delindeksen · utløpt
generasjon etterfølges av +1 uten samtidige aktive rader · terminal
`manuell` gjenåpnes aldri automatisk · negativ attestasjon → ikke
`verifikasjon_klar` · utgått attestasjon → ikke `verifikasjon_klar` ·
manipulert tenant/sak/vilkår/ressurs/generation i konvolutten avvist av
DB-bindingene · DB-dumpens hash kan ikke gjette laventropiresultat ·
sideeffektfull målhandling kan bare ende i `venter_utførelse`.
