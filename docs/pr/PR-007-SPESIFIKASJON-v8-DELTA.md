# PR-007 SPESIFIKASJON v8 — DELTA (delakkumulering forsonet med v4 → GO)

**Draft: Claude.ai · Modell (b) + form A + v1–v7 står. Dette deltaet
retter én selvpåført motsigelse: v7 pkt. 4 gjeninnførte fler-part-
akkumulering i en `aktiv` generasjon, men v4 gjorde `aktiv → positiv`
til en enkelt monoton overgang. Reviewens tre løsninger vedtatt.**

## 1. Delbevis lever på sub-generasjon, ikke på hovedgenerasjonen

Hovedgenerasjonen (`verifikasjonsgenerasjon`, v3/v4) beholder NØYAKTIG
sin firetilstands monotone maskin — `aktiv → positiv|negativ|utlopt`,
én `FOR UPDATE`-avgjort overgang. Delakkumulering flyttes ETT nivå ned:

Ny tabell `verifikasjonsdel` (migrasjon 007):
```sql
CREATE TABLE verifikasjonsdel (
  tenant TEXT NOT NULL, unntak_id BIGINT NOT NULL,
  generation INT NOT NULL, vilkaar TEXT NOT NULL,
  verifikator TEXT NOT NULL,
  bevis_id BIGINT NOT NULL,             -- append-only bevisrad
  krav_sett_hash TEXT NOT NULL,
  mottatt TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant, unntak_id, generation, vilkaar),
  FOREIGN KEY (tenant, unntak_id, vilkaar, generation, bevis_id)
    REFERENCES verifikasjonsbevis (tenant, unntak_id, vilkaar, generation, id),
  FOREIGN KEY (tenant, unntak_id, generation)
    REFERENCES verifikasjonsgenerasjon (tenant, unntak_id, generation)
);
```
- PK `(…, vilkaar)` gir per-vilkår idempotens gratis: samme vilkår kan
  ikke dobbeltregistreres i en generasjon (INSERT-konflikt → idempotent
  eller konflikt-sjekk).
- Hver del er append-only (samme trigger-mønster). Delbevis er FAKTA
  («dette vilkåret ble attestert av denne verifikatoren»), ikke tilstand.
- Hovedgenerasjonen forblir `aktiv` mens deler akkumuleres — men det er
  IKKE en muterbar mellomtilstand på generasjonen; generasjonens status
  er urørt til den ENE positiv-overgangen.

## 2. `positiv` er én transaksjon som VERIFISERER komplett, ikke bygger opp

Hver del-kvittering committer sin `verifikasjonsdel`-rad(er) atomisk.
Etter hver innsetting kjører ingest, i SAMME transaksjon, den monotone
komplett-sjekken under `FOR UPDATE` på hovedgenerasjonsraden:

```
lås generasjon FOR UPDATE
INSERT verifikasjonsdel (denne kvitteringens vilkår)   -- append-only faktum
hvis  {vilkår i verifikasjonsdel for (sak,generation)} ⊇ krav_sett.innhentbare
      OG alle delbevis gyldige/ferske (v7 pkt.1)
  →   generation.status = 'positiv', bevis_sett bundet, sak → verifikasjon_klar
ellers
  →   generasjon forblir 'aktiv'; sak forblir venter_verifikasjon
```

Overgangen `aktiv → positiv` skjer altså fortsatt ÉN gang, atomisk, når
den siste delen lander — v4-maskinen er urørt. Deler er fakta som
akkumuleres; STATUS muteres aldri gradvis. Motsigelsen er borte:
akkumulering skjer i `verifikasjonsdel`, monotoni på
`verifikasjonsgenerasjon`.

## 3. Hengende delsett: timeout på generasjonen, ikke på delene

En `aktiv` generasjon med ufullstendig delsett er den ENESTE ventetorm.
`verifikasjonsgenerasjon` får `frist TIMESTAMPTZ NOT NULL` (satt ved
opprettelse = now() + sett-verifikasjonsvindu). Utløpsjobben (v4 pkt. 1,
samme aktør/lås/rekkefølge):
```
aktiv OG now() > frist  →  status='utlopt'
                           →  verifikasjon_retry_klar (budsjett igjen)
                              | manuell (budsjett brukt)
```
Delbevisene fra en utløpt generasjon består som append-only fakta
(revisjon), men en NY generasjon starter friskt (v7 pkt. 3) — den arver
ALDRI gamle deler. Ingen del «henger» evig; ingen del gjenbrukes på tvers
av generasjoner.

## 4. Konflikt i én del fryser generasjonen (v4-vilkår 3, uendret mekanisme)

To ulike gyldige attestasjoner for SAMME (generation, vilkår) fra
gyldige verifikatorer → konfliktevidens (append-only) + sikkerhetssak +
generasjonen fryses `negativ` i samme transaksjon (aldri en femte
generasjonsstatus — v4 pkt. 2). Idempotens: identisk attestasjon (samme
bevis-hash) for samme (generation, vilkår) → no-op.

## 5. Låserekkefølge utvidet (v4-vilkår 2)

Fast rekkefølge får sub-nivået innskutt før bevis:
```
unntak → verifikasjonsgenerasjon → verifikasjonsdel → verifikasjonsbevis
       → oppdrag → kapabilitet
```
Dokumentert i kode, deadlock-testet med samtidige del-kvitteringer for
ulike vilkår i samme generasjon (vanligste reelle parallellitet).

## Hvorfor dette ikke åpner en ny runde

Delakkumulering var allerede vedtatt (v7 pkt. 4) — v8 endrer KUN HVOR den
lever (egen tabell) slik at v4-monotonien står urørt. Ingen ny
forretningsatferd, ingen ny tillitsgrense, ingen ny aktør. Det er en
normalisering som gjør de fem tidligere invariantene simultant sanne:
append-only bevis (v3), monoton generasjon (v4), atomisk komplett-sjekk
(v6), per-vilkår-verifikator (v7), og nå fler-verifikator-akkumulering
uten muterbar mellomtilstand.

## Bindende tester (i tillegg til v4–v7)

To verifikatorer, to vilkår → to del-kvitteringer → positiv først når
begge deler finnes · del-kvittering for vilkår utenfor krav_sett →
avvist · samme vilkår to ganger, identisk → idempotent; ulikt → konflikt
+ negativ · generasjon utløper med én av to deler → utlopt → retry, ny
generasjon arver ingen deler · deadlock-test: samtidige del-kvitteringer
for ulike vilkår · komplett-sjekk er atomisk: positiv settes nøyaktig i
transaksjonen der siste del lander, aldri før.
