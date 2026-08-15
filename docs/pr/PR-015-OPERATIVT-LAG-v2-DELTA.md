# PR-015 SPESIFIKASJON v2 — DELTA (to bindende vilkår → GO)

**Draft: Claude.ai · Retningen står: PR-015 før 014c, A→B→C-modellen,
`domains:adjudicate`, utstedelse ved claim, ryddetimere. To vilkår lukket.
Den bærende rettelsen: spredningen var et håp, ikke en mekanisme.**

014b er urørt. Ingenting her endrer 016 eller 017.

## 0. Én ting om scopet, sagt rett ut

Jeg skrev at PR-015 er «kallerne — og ingenting annet, ingen ny DDL».
**P2 gjør det utsagnet usant**, og jeg vil ikke la formuleringen overleve
inn i klarsignalet: den første attestasjonen må lagres et sted mens den
venter på den andre. PR-015 får derfor **migrasjon 018 med nøyaktig én ny
tabell** (§2), additiv, uten å røre 016/017. P1 krever fortsatt ingen DDL
(§1). Alt annet er kallere.

*(Sidenote til Eier: pastet inneholdt også halen av det gamle 014b-GO-et
— «konsolider til klarsignal med full DDL for migrasjon 014». Den er
utført og utdatert, og «migrasjon 014» i den teksten er nettopp
nummerfeilen docs-PR-en retter. Jeg har ikke handlet på den.)*

## 1. P1 — spredningen avledes av hostname, ikke av tidsstempler

«Eldste først + hourly» var ingen fordelingsmekanisme. Etter bootstrap,
import eller outage-recovery blir hundrevis av rader kvalifiserte i samme
sekund, og «eldste først» tømmer dem så fort timeren tillater.

**Rettet: hver rad har et fast minutt i døgnet, avledet stabilt fra
hostname.** Ingen ny kolonne, ingen lagret plan, ingenting som kan komme i
utakt med raden:

```
revalideringsminutt(hostname) = int(sha256(hostname)[0:8], 16) mod 1440
```

- **Bootstrap og import spres av seg selv.** Fordelingen kommer fra
  hostname, ikke fra `siste_vellykkede_revalidering` — 500 domener
  verifisert i samme sekund får 500 uavhengige minutter.
- **Planen flytter seg aldri.** Ingen retry, ingen outage og ingen
  restore endrer et minutt. Restore fra backup gir identisk plan.
- **Normalt utvalg:** rader hvis minutt faller i vinduet
  `[forrige kjøring, nå)` **og** `siste_vellykkede_revalidering <
  now() - 20 t` (20, ikke 24, så en time med drift ikke hopper over et
  døgn).

**Retry er også avledet, ikke lagret.** Radens tre forsøk ligger på
`minutt`, `minutt + 4 t` og `minutt + 8 t`. Et vellykket forsøk setter
`siste_vellykkede_revalidering`, og de senere slottene hopper over raden
fordi den er fersk. **Backoff-jitter gjelder innenfor slottet** (±5 min),
aldri på tvers av dem. Dermed kan et feilforsøk aldri forskyve
normalplanen — det finnes ingen plan å forskyve.

**Etterslep etter outage dreneres med budsjett, ikke i én byge.** Rader
som passerte sitt slott mens timeren var nede, plukkes eldste slott
først, med **maks 25 % ekstra ut over timens normale andel per kjøring**.
En seks timers outage dreneres da over ~ett døgn i stedet for i første
kjøring etter oppstart. Måltallene i §4 skiller derfor **normaldrift** fra
**recovery** — én terskel for begge ville vært feil i begge retninger.

**Sikkerhetsnett:** en rad som passerer 26 t uten vellykket revalidering
plukkes uansett bucket, innenfor samme budsjett. Ingen rad kan falle ut av
planen fordi et vindu ble bommet.

## 2. P2 — positiv cross-tenant tildeling krever to attestasjoner

Ett menneske med et kraftig scope var for svak positiv autorisasjon for en
handling som flytter autorisasjon mellom kunder. Rettet, og **asymmetrisk**:

| Utfall | Krav | Hvorfor |
|---|---|---|
| **Avvis** (B → `tilbakekalt`) | **Én** attestasjon | Fail-closed. Ingen får autorisasjon |
| **Godkjenn** (B → `verifisert`) | **To distinkte** attestasjoner | Etablerer hvilken kunde plattformen autoriserer |

```sql
-- Migrasjon 018, eneste nye tabell
CREATE TABLE overtakelse_attestasjon (
  sak_id UUID NOT NULL,
  saksrevisjon BIGINT NOT NULL,        -- §2, invalidering
  aktor TEXT NOT NULL,
  utfall TEXT NOT NULL CHECK (utfall IN ('godkjenn','avvis')),
  vinnende_tenant TEXT NOT NULL,
  hostname TEXT NOT NULL,
  avgitt_ts TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (sak_id, saksrevisjon, aktor));   -- én aktør, én stemme per revisjon
```

- **Bundet til samme sak OG samme eksakte utfall:** de to radene må ha
  identisk `(saksrevisjon, utfall, vinnende_tenant, hostname)`. Avvik →
  ingen avgjørelse, ikke en sammenslåing.
- **Ingen enkelt aktør produserer begge** — håndhevet av primærnøkkelen,
  ikke av UI-et. Begge krever `domains:adjudicate`.
- **Ny konflikt invaliderer ventende attestasjoner.** Overtar C mens en
  attestasjon for B ligger inne, økes `saksrevisjon` i samme transaksjon
  som overtakelsen (014b B2-låsen), og B-attestasjonen kan aldri telle mot
  C-utfallet. Radene slettes ikke — de er evidens for at noen attesterte
  et utfall som ble foreldet.
- **Motoren beslutter fortsatt.** `avgjor_domeneovertakelse()` teller
  attestasjonene under hostname-låsen og gjør overgangen. Ingen knapp
  skriver status — invariant 3.

**Når tenanten bare har én autorisert aktør, er positiv tildeling umulig.**
Det er riktig fail-closed, men det skal **sies**, ikke oppleves som en
knapp som ikke virker: feilkoden er `krever_to_attestasjoner` med antall
autoriserte aktører, oversatt i UI. Den ærlige utveien finnes allerede og
nevnes i teksten: når A-s 90-døgnsvindu løper ut, kan B verifisere på nytt
**uten konflikt** — da er det ingen cross-tenant-tildeling å attestere.

## 3. Presiseringer fra reviewen

**20 % er driftssignal, aldri synlighetsgrense.** Hver mislykket
revalidering forblir tenantbundet, auditert og individuelt søkbar.
Terskelen deduplikerer **varslingen** til én hendelse — den klassifiserer
ikke tenantens tilstand, oppretter ingen M-37-sak, og skjuler ikke at
`tenant X / hostname Y` har tre døgn uten vellykket revalidering.
Terskelen er konfigurerbar og målt.

**Stale opplastingskapabilitet ved reclaim.** Følger av
`module_epoch`/claim-bindingen fra 014a V2. Jeg legger ikke til en ny
port, men **utvider den eksisterende negative kapabilitetstesten** til
eksplisitt å dekke opplastingstokenet, slik at det er bevist og ikke
antatt.

## 4. Evidensgrense — tillegg til `operativt-lag-v1`

`revalidering-015-v1`:
`bootstrap.maks_andel_per_time ≤ 0.10` (500 rader verifisert i samme
sekund) · `steadystate.maks_andel_per_time ≤ 0.10` ·
`recovery.maks_andel_per_time ≤ 0.125` etter 6 t outage, og
`recovery.etterslep_igjen_etter_24t = 0` ·
`plan.uendret_etter_restore = ja` · `plan.forskjovet_av_retry = 0` ·
`ingen_rad_over_26t_uplukket = 0`.
`overtakelse-015-v1`:
`godkjenn_med_en_attestasjon = 0` · `samme_aktor_to_stemmer = 0` ·
`attestasjon_pa_foreldet_revisjon_talt = 0` ·
`avvis_med_en_attestasjon = tillatt` ·
`en_aktor_gir_feilkode_krever_to_attestasjoner = ja`.

## 5. Tester (tillegg — Codex-porter 21–32)

21. 500 rader verifisert i samme sekund → ingen time får > 10 % (bootstrap)
22. Steady state over et døgn → ingen time > 10 %, hver rad revalidert én gang
23. Seks timers outage → etterslep dreneres med budsjett, ingen byge, tomt innen 24 t
24. Restore fra backup → identisk revalideringsplan (samme minutter)
25. Feilet forsøk → normalplanen uendret; forsøk 2 og 3 på +4 t/+8 t; vellykket forsøk 1 → slott 2 og 3 hopper over raden
26. Rad 26 t uten suksess → plukket uansett bucket, innenfor budsjett
27. Bred resolverfeil → én driftsalarm, null M-37-saker, og `tenant X / hostname Y` fortsatt individuelt synlig med tre døgn uten suksess
28. Godkjenn med én attestasjon → nektet med `krever_to_attestasjoner`
29. Samme aktør to ganger → avvist av primærnøkkel, ikke av UI
30. To attestasjoner med ulikt `vinnende_tenant` eller ulik revisjon → ingen avgjørelse
31. C overtar med B-attestasjon inne → `saksrevisjon` økt, B-attestasjonen teller ikke, raden bevart
32. Avvis med én attestasjon → B `tilbakekalt`; tenant med én autorisert aktør får legibel feilkode, ikke stillhet

Utvidet: eksisterende negativ kapabilitetstest dekker nå også
opplastingstokenet ved reclaim.

**Alle tester konstruerer egen tilstand.** Ingen delt fixture.

---

```
NÅ:    v2-deltaet tilbake gjennom spesifikasjonsporten (P1 og P2 lukket,
       scope-endringen i §0 er ny og skal vurderes) — ChatGPT (Eier relayer)
       — docs/PR-015-OPERATIVT-LAG-v2-DELTA.md
NESTE: Ren docs-rettelse av migrasjonsnummer (014 → 016/017) i de tre
       014b-dokumentene; egen PR, porten hoppes over med begrunnelse
       — Claude Code — docs/PR-014b-*.md
```
