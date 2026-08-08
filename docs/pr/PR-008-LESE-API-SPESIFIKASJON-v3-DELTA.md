# PR-008 SPESIFIKASJON v3 — DELTA (fire utledningskontrakter → GO)

**Draft: Claude.ai · v1+v2 står der de ikke motsies. Reviewens FK,
matrise, snapshot og DTO vedtatt direkte. Kjernen: hver vist verdi må
utledes fra ÉN eksplisitt FK-bundet rad, aldri fra MAX/tidsnærhet.**

## 1. Direkte beslutning→oppdrag-FK (migrasjon i PR-008)

FK-kjeden `beslutning ← unntak ← oppdrag` dekker IKKE en ordinær TILLAT
som lager outbox uten unntaksrad. Rettet med direkte kobling — **eksplisitt
migrasjon i PR-008, ingen heuristisk fallback:**
```sql
ALTER TABLE oppdrag
  ADD COLUMN beslutning_loggpost_id BIGINT,
  ADD CONSTRAINT oppdrag_beslutning_fk
  FOREIGN KEY (tenant, beslutning_loggpost_id)
  REFERENCES revisjonslogg (tenant, id);
```
- HVERT oppdrag som vises under en beslutning har stabil, tenantbundet
  referanse til den KONKRETE beslutningsloggposten.
- `unntak_id` og `repair_operation_id` beholdes som TILLEGGSkoblinger,
  erstatter ikke beslutningsreferansen.
- **Reparasjonsbeslutning (PR-007 fase 2) kobles til SIN EGEN nye
  loggpost** — ikke til den opprinnelige ved tidsnærhet. To
  reparasjonsbeslutninger for samme unntak får hvert sitt oppdrag via egen
  `beslutning_loggpost_id`.
- Detaljendepunktet: `SELECT ... FROM oppdrag WHERE tenant=$t AND
  beslutning_loggpost_id={id}` — direkte, entydig.
- **Sikkerhetssakens boolean:** tilsvarende stabil referanse. Migrasjon
  legger `beslutning_loggpost_id` også på sikkerhetssak-raden (M-37
  sikkerhet/drift-sak), så `sikkerhet.sak_finnes` utledes fra
  `EXISTS(... WHERE beslutning_loggpost_id={id})`, ikke fra tidsnærhet.
- Backfill: eksisterende oppdrag kobles via sin unntak→loggpost-kjede der
  den finnes; rader uten entydig kobling får NULL og vises ikke feilaktig
  under en beslutning (fail-closed — heller ingen kobling enn feil).

## 2. Ingen MAX(generation) — FK-bundet oppdrag, generation som kontekst

`høyeste verification_generation` forkastet (kan være aktiv retry,
negativ, utløpt eller sikkerhetsfryst). Rettet:
- Beslutningsdetaljen viser oppdraget som er DIREKTE FK-bundet til den
  aktuelle beslutningsloggposten (§1). `generation` vises kun som KONTEKST
  på det oppdraget, aldri som koblingsnøkkel.
- Skal UI vise «gjeldende tilstand for den overordnede unntakssaken», er
  det et SEPARAT felt utledet fra sakens eksplisitte
  state-pointer/current-generation på unntaksraden — ALDRI `MAX(generation)`.
  (Unntaksdetaljen `/v1/unntak/{id}` viser sakens nåtilstand; beslutnings-
  detaljen viser dét oppdraget beslutningen skapte.)

## 3. Total resultat.art × evidensstatus-matrise (server avviser resten)

Lukket tabell — hvert lovlig par, alt annet avvist av servermodellen:

| resultat.art | Lovlig evidensstatus | Regel |
|---|---|---|
| policy_stoppet | INGEN | ingen outbox |
| sideeffektfri_tillatt | INGEN | ingen outbox |
| til_unntak | INGEN | evidens hører til reparasjonens egen beslutning, ikke denne |
| outbox_opprettet | MANGLER | venter plukking |
| outbox_plukket | MANGLER | under utførelse |
| outbox_utfort | GYLDIG | KUN gyldig kvittering innen utførelsesfrist |
| outbox_feilet | GYLDIG \| MANGLER | GYLDIG=signert feilresultat; MANGLER=timeout/systemfeil — `feil_aarsak[signert\|timeout]` skiller |
| outbox_kansellert | INGEN \| GYLDIG | INGEN=kansellert før kvittering; GYLDIG=kvittering før kansellering; ALDRI MANGLER/SEN/KONFLIKT |

**SEN og KONFLIKT er ORTOGONALE tilleggsflagg, ikke utførelsesstatus** —
de endrer aldri `resultat.art`:
- En sen kvittering på et `outbox_opprettet`/`outbox_plukket`-oppdrag som
  ikke ble endret av evidensen → `resultat.art` uendret, egen
  `sen_evidens: true`.
- `outbox_utfort + SEN` finnes IKKE — sen kvittering lukker aldri
  automatisk (PR-007 v4); et oppdrag som fikk sen kvittering forblir i sin
  faktiske art med `sen_evidens: true`.
- **KONFLIKT har presedens over SEN** hvis begge finnes, uten å endre det
  terminale utførelsesresultatet: `konflikt_evidens: true` +
  `sikkerhet.sak_finnes` (med scope). Presentasjonspresedens, ikke
  statusendring.

Revidert skjema — evidens som flagg, ikke sammenblandet enum:
```
{
  resultat: {art, oppdrag_id?, superseded?, feil_aarsak?, unntak_id?, kategori?, status?},
  evidensstatus: [INGEN|MANGLER|GYLDIG],   // ordinær, fra FK-oppdragets rad
  sen_evidens: bool,                        // ortogonalt flagg
  konflikt_evidens: bool,                   // ortogonalt flagg
  sikkerhet: {sak_finnes} | fraværende
}
```
Servermodellen avviser hvert par utenfor tabellen; UI matcher `art` +
leser flaggene.

## 4. Monotont, immutabelt cursorsnapshot (tåler backdated rader)

MAX(ts,id) stopper ikke en rad satt inn senere med eldre `ts`. Rettet:
- Ved FØRSTE side fastsettes `snapshot_max_id` (immutabel, bundet i cursor).
- ALLE sider: `id <= snapshot_max_id`.
- Sortering/keyset fortsatt `(ts,id)`.
- Første snapshot + første side i SAMME DB-snapshot (én transaksjon /
  `REPEATABLE READ`).
```
Beslutninger DESC:  id <= snapshot_max_id AND (ts,id) < (siste_ts,siste_id)
Historikk ASC:      id <= snapshot_max_id AND (ts,id) > (siste_ts,siste_id)
```
- `id` er IDENTITY (monoton per tabell på main) → duger som
  innsettingsgrense. Skulle en tabell mangle monoton id, brukes PostgreSQL
  txid-snapshot i stedet (flagges per tabell i implementeringen).
- En backdated rad med `id > snapshot_max_id` faller UTENFOR snapshotet →
  vises ikke på senere sider, bryter ikke pagineringen.

## 5. Lukkede, versjonerte policy-DTO-er (additionalProperties: false)

`grenser`, `vilkaar`, `autoritetsmetadata` var frie beholdere. Rettet —
eksakte DTO-er, alle nivåer `additionalProperties: false`:
```
PolicyDTO { policy_id:str, versjon:str, innholds_hash:str,
  roller: [RolleDTO], handlinger: [HandlingDTO], verifikatorer: [VerifikatorDTO] }
HandlingDTO { navn:str, modus:[auto|auto_med_vilkaar|alltid_stopp],
  grenser: GrenserDTO, vilkaar: [str] }              // vilkaar = kodeliste
GrenserDTO { belop_maks:decimal?, valuta:str(3)?, tidsvindu:TidsvinduDTO?,
  frekvens: FrekvensDTO? }                            // faste felt, ingen fri JSON
VerifikatorDTO { offentlig_id:str, betrodd_for:[str],
  kan_fastsla_permanent:bool }                        // eksplisitt redigert
```
Ingen `autoritetsmetadata` som vilkårlig blokk — erstattet av eksplisitte
felt (`betrodd_for`, `kan_fastsla_permanent`). ALDRI tokenhash, pepper,
nøkler, krypteringsmetadata, interne DB-felt, rå YAML. Versjonert
(`skjemaversjon`) så nye backendfelt ikke lekker stille — ukjent felt =
byggefeil, ikke passthrough.

## Bindende tilleggstester (reviewens, vedtatt)
Ordinær TILLAT uten unntaksrad finner outbox via `beslutning_loggpost_id`-FK ·
to reparasjonsbeslutninger samme unntak → hvert sitt oppdrag · nyere aktiv
generation overskriver ikke eldre beslutnings detalj · alle lovlige
resultat/evidens-par aksepteres, alle andre avvist · sen kvittering endrer
ikke art til outbox_utfort · backdated rad under paginering usynlig i
snapshot · historikkcursor bruker `>`-predikat · ekstra felt på ethvert
policy-DTO-nivå avvist.
