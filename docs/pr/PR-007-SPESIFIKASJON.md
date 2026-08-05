# PR-007 SPESIFIKASJON — R1 som tofaseprotokoll (til ChatGPT-porten)

**Draft: Claude.ai · Basis: main 019e06a (347 tester) + Claude Codes
PR-007-brief + Codex-beslutning på PR #11. Bygger på kvitteringsporten,
oppdragskontrakten og signaturmekanismen som ALLEREDE finnes —
redesigner ingenting av det.**

## 0. Arkitektbeslutning på blokker 1: modell (b), avgrenset ærlig

**Den nye hendelsen bygges fra minimert payload + verifisert attestasjon
— IKKE fra en lagret originalhendelse.** Dataminimeringen fra v2 Del 5
røres ikke. Men briefens motargument er korrekt og avgjør formen:
(b) holder KUN når det manglende er en attestasjon, ikke en verdi.

Derfor: **R1 tofaseprotokoll gjelder present `manglende_data` der det
manglende er et ATTESTERT VILKÅR.** Presist avgrenset:

- `manglende_data` med `attestasjon_mangler`/`attestasjon_utlopt` for et
  vilkår → R1 tofase (verifikator kan levere den manglende attestasjonen;
  ressurs_id-bundet, som allerede finnes i minimert payload). **Behandlbar.**
- `manglende_data` der en GRENSEVERDI mangler i minimert payload (f.eks.
  `belop` for en `over_grense`-nær sak) → dette er IKKE en manglende
  attestasjon og kan ikke rekonstrueres uten originalverdien →
  **`manuell`, ikke R1.** Klassifisereren skiller på Grunn-koden, ikke
  bare kategorien (samme presisjon som taksonomipredikatet i PR-006 v4).

Konsekvens: R1s positive vei blir oppnåelig for nøyaktig den saksklassen
der (b) er konstruktivt mulig, og fail-closed til `manuell` for resten —
uten å utvide datalagringen. Modell (a) er dermed IKKE nødvendig for
PR-007. Skulle en fremtidig handlerklasse trenge originalverdier,
behandles (a) som egen spesifikasjon med egen retention-begrunnelse —
ikke smugles inn her.

## 1. Tofaseprotokollen — sekvensielle faser (bevarer delindeksen)

To faser, ALDRI samtidig aktive (blokker 3): fase 2 starter først når
fase 1s reparasjonsoperasjon er terminal. `en_aktiv_reparasjon_per_sak`
består uendret — andre forsvarslinje mot samtidige generasjoner bevart.

```
Fase 1 — VERIFIKASJON (sideeffektfri):
  M-37 bygger verifikasjonsoppdrag (oppdragstype 'verifikasjon', finnes
  allerede) → outbox → verifikatormodul kontrollerer om det manglende
  vilkåret nå kan attesteres → signert verifikasjonskvittering med
  attestasjonen som bevis (verifikatorens signatur, ikke oppdragets
  payload — blokker 4).

Fase 2 — NY BESLUTNING (policystyrt):
  M-37 bygger ny hendelse = minimert payload + den VERIFISERTE
  attestasjonen fra fase 1 → sender som ny beslutning gjennom API-et
  (arbeidskapabilitet, repair_operation_id) → TILLAT+utført → 'løst';
  ellers → 'manuell'.
```

Fase 1 har null forretningsfullmakter (blokker/invariant 8):
verifikatoren KONTROLLERER og ATTESTERER, utfører ingen sideeffekt.
Fase 2 går gjennom hele policyporten på nytt — M-37 ber kun om en
policystyrt beslutning, utfører aldri selv.

## 2. Statusmaskin: ny tilstand + vei tilbake (blokker 2)

Migrasjon 007 utvider CHECK og statusmaskinen. Ny tilstand
`venter_verifikasjon` med EKSPLISITT vei tilbake til ny vurdering:

```
ny               -> under_behandling | manuell
under_behandling -> løst | avvist | manuell | venter_utførelse
                    | venter_verifikasjon              (NY)
under_behandling -> ny                    (kun utløpt lease, uendret)
venter_verifikasjon -> under_behandling   (NY: positiv verifikasjon → fase 2)
venter_verifikasjon -> manuell            (NY: negativ/utløpt/uteblitt)
venter_utførelse    -> løst | manuell     (uendret)
```

`venter_verifikasjon` er IKKE terminal (bevisst — i motsetning til
`løst|avvist|manuell`). Overgang tilbake til `under_behandling` krever
positiv, fenced verifikasjonskvittering; alle andre utfall (negativ
verifikasjon, evidensfrist passert, uteblitt) → `manuell`. Overgangene
håndheves av statusmaskin-triggeren, med fencing-WHERE
(claim_id + generation) som alle andre statusskriv.

## 3. Verifisert bevis — lagring (blokker 4)

Ny tabell `verifikasjonsbevis` (migrasjon 007), fordi `attestasjon_jti`
lagrer at en jti er brukt (ikke hva den sa) og `oppdrag.kvittering` er
uforanderlig bundet til ett oppdrag:

```sql
CREATE TABLE verifikasjonsbevis (
  id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  tenant        TEXT NOT NULL,
  unntak_id     BIGINT NOT NULL,
  repair_operation_id TEXT NOT NULL,     -- fase-1-identitet (se pkt. 5)
  vilkaar       TEXT NOT NULL,           -- hvilket vilkår som ble verifisert
  attestasjon   JSONB NOT NULL,          -- den verifiserte attestasjonen
  verifikator   TEXT NOT NULL,           -- fra signaturen, ikke payload
  signatur      TEXT NOT NULL,           -- JCS + nøkkelregister (finnes)
  gyldig_til    TIMESTAMPTZ NOT NULL,    -- attestasjonens utloper
  opprettet     TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (tenant, unntak_id) REFERENCES unntak (tenant, id),
  UNIQUE (tenant, repair_operation_id)   -- idempotens på fase-1-bevis
);
```
- RLS + FORCE; append-only (ingen UPDATE/DELETE/TRUNCATE — trigger).
- **Beviset bæres av verifikatorens signatur** (blokker 4-kravet):
  ingest verifiserer JCS-signatur mot nøkkelregisteret i app-state, og
  at attestasjonen IKKE stammer fra hendelsen/opprinnelig innsender —
  verifikator-feltet må være en registrert verifikator ulik saksinnsender.
- Lesere: kun M-37-arbeideren (fase 2 leser beviset for å bygge hendelsen)
  og revisjon. Runtime SELECT, ingen UPDATE.
- Levetid: `gyldig_til` = attestasjonens utløp; utløpt bevis kan ikke
  starte fase 2 → `manuell`. Retention følger sakens (ryddes når saken
  er terminal + retention, som øvrige M-37-data).

## 4. Verifikasjonsoppdraget — ett felt lagt til (blokker 5, minimal)

`oppdragskontrakt.py` oppdragstype `verifikasjon` finnes med prefikser
`("verifiser.","kontroll.")` og felter
`{handling, kategori, kildereferanser, ressurs_id}`. Utvidelsen er
nøyaktig ett felt: **`vilkaar`** — hvilket krav som skal verifiseres.
Lukket skjema (additionalProperties: false bevart). Ingen annen endring
i oppdragskontrakten.

## 5. Separat, stabil identitet per fase (blokker 3, Codex punkt 3)

Fasene er sekvensielle, men får LIKEVEL separate stabile identiteter
(Codex krever det eksplisitt):
```
fase1_id = SHA-256(tenant ‖ unntak_id ‖ 'verifikasjon'
                   ‖ vilkaar ‖ handler_id@versjon)
fase2_id = SHA-256(tenant ‖ unntak_id ‖ 'beslutning'
                   ‖ target_action ‖ verifikasjonsbevis.id)
```
Ingen av dem inneholder forsok/claim (transportdetaljer, uendret
prinsipp fra v3). fase2_id binder til det konkrete beviset, så en ny
verifikasjonsgenerasjon (nytt bevis) gir ny fase2-identitet —
replay-sikkert. Delindeksen tåler dette fordi kun én fase er aktiv om
gangen: fase 1s operasjon er terminal før fase 2s opprettes.

## 6. De fire samtidighetsspørsmålene, besvart per kontroll

| Kontroll | Alle veier inn? | Under samtidighet? | Riktig, ikke velformet? | Lukket format? |
|---|---|---|---|---|
| Fase-overgang | Kun statusmaskin-trigger m/ fencing | To arbeidere: fencing-WHERE → én vinner, taper treffer 0 rader | Positiv verifikasjon kreves for fase 2; alt annet manuell | CHECK-enum; ny status er ikke bare en streng |
| Bevis-ingest | Kun kvitteringsendepunktet (finnes) | UNIQUE(tenant,repair_operation_id) → idempotent; motstridende → sikkerhet | Signatur verifisert mot register; verifikator ≠ innsender | JCS påkrevd; ukjent kanonisering avvist |
| Hendelsesbygging fase 2 | Kun arbeideren; `sett_kontekst` først (invariant 10) | Sekvensiell etter terminal fase 1 → ingen samtidig bygging | (b)-avgrensningen: bygges kun når manglende = attestasjon | minimert payload + bevis, ingen andre kilder |
| Klassifisering R1 vs manuell | Klassifisereren, alle saker | N/A (ren funksjon) | Grunn-kode, ikke bare kategori (attestasjon vs verdi) | Lukket Grunn-kode→rute-tabell |

## 7. Evidens (feilinjisering-m01-v1 blir oppnåelig)

Med (b)-avgrensningen finnes nå en reparerbar sak: injiser
`manglende_data`/`attestasjon_mangler`, syntetisk verifikator leverer
signert attestasjon i fase 1, fase 2 gir TILLAT → `løst`.
`lost_andel av reparerbare = 1.0` blir dermed oppnåelig for første gang.
Artefaktet må også bevise: en verdi-basert `manglende_data`-sak går til
`manuell` (ikke R1) — den negative avgrensningen; bevis-ingest idempotent
og motstridende → sikkerhet; venter_verifikasjon → manuell ved utløpt
bevis; kun én aktiv reparasjon per sak gjennom begge faser.

## 8. Invarianter 1–10 urørt

Verifikasjonsfasen bevarer null-fullmakt (fase 1 attesterer, utfører
ikke). `api/` importerer aldri `m37/` — delt kontrakt (`vilkaar`-feltet)
bor i `oppdragskontrakt.py` på core-nivå. Kvitteringer som ikke kan
avslutte lagres i historikk/bevis-tabell, aldri på oppdragsraden.
Alle nye veier setter `sett_kontekst` først. Én skrivevei til
revisjonsloggen. Migrasjon 007 eier ikke transaksjonen (kjøreren gjør),
reviewet checksum til bootstrap.

## Spørsmål til ChatGPT

1. (b)-avgrensningen: er «R1 kun når det manglende er en attestasjon,
   verdi-mangler → manuell» riktig grense — eller finnes en tredje
   saksklasse jeg overser der (b) verken er ren attestasjon eller ren
   verdi?
2. `venter_verifikasjon → under_behandling`: bør tilbakeveien re-claime
   (ny claim_id/generation) for å unngå at fase 1s utløpte lease
   forurenser fase 2, eller er samme claim gjennom begge faser trygt gitt
   fencing?
3. fase2_id binder til `verifikasjonsbevis.id`: ser du et hull hvis to
   verifikasjonsbevis for samme vilkår rekker å eksistere før fase 1 er
   terminal (skal være umulig via delindeksen — men bekreft resonnementet)?
