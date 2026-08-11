# PR-013 SPESIFIKASJON v2 — DELTA (åtte fullmaktskontrakter → GO)

**Draft: Claude.ai · Grunnmodellen står. De to tyngste først:
klassifikatoren som algoritme, og hva attestasjonen faktisk binder.**

## 1. `KLASSIFIKATOR_V1` — uttømmende, skjemadrevet, versjonert

Eksempellisten erstattes av en **komplett regel per muterbart felt i
skjema v0.2**. Ukjent sti, ukjent type, ukjent skjemaversjon eller
uklassifiserbar sammenligning → **UTVIDER** (fail-closed).

| Felt / sti | UTVIDER når | INNSNEVRER når | Array-semantikk |
|---|---|---|---|
| `handlinger[]` | handling lagt til | handling fjernet | **mengde** (nøkkel: `navn`) |
| `handlinger[].modus` | mot `auto` i gitteret `alltid_stopp < auto_med_vilkaar < auto` | mot `alltid_stopp` | — |
| `.grenser.belop_maks` | verdi opp, ELLER grense fjernet (ubegrenset) | verdi ned, ELLER grense lagt til | — |
| `.grenser.valuta` | **enhver endring** (kan ikke sammenlignes på tvers) | — | — |
| `.grenser.tidsvindu.ukedager` | dag lagt til | dag fjernet | **mengde** |
| `.grenser.tidsvindu.fra/til` | vindu utvidet | vindu smalnet | — |
| `.grenser.tidsvindu.tidssone` | **enhver endring** (fail-closed) | — | — |
| `.grenser.frekvens` | høyere rate (`maks / vindu` normalisert til sekunder) | lavere rate | — |
| `.grenser.frekvens.vindu_enhet` | ikke normaliserbar → **UTVIDER** | — | — |
| `.vilkaar[]` | vilkår **fjernet** | vilkår **lagt til** | **mengde** |
| `.reversering.type` | mot mer reversibel (`irreversibel → kompenserende → direkte`) — svekker vakten | mot mindre reversibel | — |
| `.ved_brudd` | mot mildere (`frys < stopp_og_varsle < unntakskø`) | mot strengere | — |
| `roller[]` | rolle lagt til | rolle fjernet | **mengde** (nøkkel: `id`) |
| `verifikatorer[]` | verifikator lagt til | fjernet | **mengde** (`offentlig_id`) |
| `verifikatorer[].betrodd_for[]` | vilkår lagt til (kan attestere mer) | fjernet | **mengde** |
| `verifikatorer[].kan_fastsla_permanent` | `false → true` | `true → false` | — |
| `unntak.kategorier[]` | kategori lagt til | fjernet | **mengde** |
| `menneskelig_overstyring` | feltet lagt til, `godkjennbare` utvidet, `belop_maks` opp, `krever_fire_oyne` `true → false`, `krever_rolle` bredere | motsatt | `godkjennbare` = **mengde** |
| `*_kode`, beskrivelsestekst | — | — | **NØYTRAL** |

**Bindende egenskaper:**
- Klassifikatoren er **skjemaorientert**: den itererer skjemaets felt, ikke
  diffens. Et felt som finnes i skjemaet uten regel → byggefeil (CI-port).
- **Mengde vs. ordnet er deklarert i skjemaet.** «Rekkefølge er NØYTRAL»
  gjelder KUN der skjemaet beviser at rekkefølgen er uten semantikk;
  ordnede arrays gir UTVIDER ved omstokking (fail-closed).
- **Manglende standardverdier:** fravær sammenlignes mot skjemaets
  eksplisitte default; er defaulten udefinert → UTVIDER.
- **Versjonert** (`klassifikatorversjon`), og **mutasjonstestet**: hver
  UTVIDER-regel må ha en test som blir rød hvis regelen fjernes.
- Samlet risikoklasse for en diff = strengeste enkeltendring.

## 2. Runden og attestasjonen binder klassifiseringen, ikke bare diffen

Klassifikatorkoden kunne endret seg mellom åpning og aktivering. Rettet —
`aktiveringsrunde` og hver `aktiveringsattestasjon` binder:
`diff_hash · risikoklasse · klassifikatorversjon · klassifisering_hash
(SHA-256 over JCS av klassifiseringsresultatet) · pakrevd_antall_godkjennere`.
**Alt beregnes på nytt under aktiveringslåsen** (§5). Avvik i ett felt →
runden kanselleres, ny runde kreves.

## 3. Aktiveringskonvolutten (lukket, `disponit_policy_activation_v1`)

MAC-en dekker JCS-kanonisert konvolutt med:
`tenant · policy_id · utkast_id · runde · base_policy_hash ·
utkast_innholds_hash · diff_hash · klassifisering_hash ·
klassifikatorversjon · risikoklasse · pakrevd_antall_godkjennere ·
bruker_id · rolle · authz_version · utstedt · utloper · jti`.
Engangsbruk (`UNIQUE (tenant, jti)`), nøkkelrotasjon og `klar → brukt`
følger PR-012-kontrakten uendret.

## 4. Forfatterforbud gjelder ALLTID, ikke bare ved fire øyne

- **Alle endringer:** minst én godkjenner ≠ `opprettet_av`.
- **UTVIDER:** to ulike godkjennere, **begge** ≠ `opprettet_av`.
- Medlemskap, scope (`policy:activate`), rolle og `authz_version`
  revalideres for hver godkjenner **ved aktivering**, ikke bare ved
  attestering.
- At samme bruker kan ha både `policy:write` og `policy:activate` er
  tillatt — men aldri på egen endring.

## 5. Diff og klasse REKALKULERES fra låste rader ved aktivering

Å sammenligne lagrede hasher holder ikke. I samme transaksjon:
1. Lås nåværende aktive policy + utkastet.
2. Kanoniser begge (JCS).
3. **Beregn diff og risikoklasse på nytt.**
4. Sammenlign mot rundens bundne verdier (§2) — avvik → kanseller runde.
5. Fastslå godkjennerkravet fra den NYE klassen.
6. Deretter aktiver.

**Ingen caller kan sende diff, risikoklasse eller antall godkjennere.**
Codex-port: ingen offentlig signatur eksponerer dem.

## 6. Rullbakk kopierer innhold til NY versjonsidentitet

En historisk rad gjøres ALDRI aktiv igjen ved å flippe `aktiv=true`.
Rullbakk = ny versjon med provenance:
```
basert_pa_versjon      -- versjonen den bygger på
rollback_av_versjon    -- hvilken historisk versjon som ble gjenopprettet
versjon                -- ny, monoton, unik per (tenant, policy_id)
```
Samme `innholds_hash` kan forekomme igjen; **versjonsidentiteten er alltid
ny**. Versjonsallokering er atomisk (sekvens/`FOR UPDATE` på
policy-raden) — testes med 20 samtidige aktiveringer: nøyaktig én aktiv,
ingen duplikate versjonsnumre.

## 7. Aktiveringsrundens totale tilstandsmaskin

| Hendelse | Utfall |
|---|---|
| Utløp mens `apen` | `utlopt`; attestasjoner består; ny runde mulig |
| Utløp mens `klar` | `utlopt`; aktivering ikke lenger mulig; ny runde kreves |
| Valideringsregler/systemreferanser endret etter godkjenning | Revalidering ved aktivering feiler → **runde kansellert** |
| Aktiv policy endret av konkurrerende aktivering | `base_policy_hash`-avvik → **runde kansellert**, rebasering kreves |
| Bruker/rolle/`authz_version` tilbakekalt etter siste attestasjon | Revalidering feiler → **runde kansellert** |
| **Teknisk/DB-feil under aktivering** | Rollback; **runden består `klar`** for idempotent retry (den ble committet i en tidligere transaksjon) |

Skillet er bindende: **policy-, diff- eller autorisasjonsavvik kansellerer;
teknisk feil gjør det ikke.**

## 8. PR-012-runder kanselleres LAT, ikke i aktiveringstransaksjonen

Å låse og oppdatere alle åpne unntakssaker ville gitt ubundet arbeid og
en ny deadlockflate. Sikker minimumsform (mekanismen finnes allerede):
- Policy aktiveres atomisk.
- PR-012-runden binder gammel `godkjennings_policy_hash`.
- **Neste lesing/handling oppdager drift og kansellerer runden under sin
  egen sakslås** (PR-012 v3 §4, uendret).
- Eventuell proaktiv opprydding kan kjøre asynkront og idempotent, men er
  **aldri sikkerhetsautoriteten** — driftdeteksjonen er det.

## Svar på v1-spørsmålene (reviewens, vedtatt)
1. Fire øyne for UTVIDER = hard plattformregel. Kunden kan kreve
   STRENGERE (f.eks. fire øyne for alt), aldri senke gulvet. Den
   innstillingen ligger UTENFOR policyen som skal godkjennes — egen
   tenant-innstilling, ikke et policyfelt.
2. Simulering ikke i v1, og aldri som aktiveringskrav: historiske
   beslutninger kan mangle representativt datagrunnlag og gi falsk
   trygghet. v2, som rådgivende analyse med dekningsrapport.
3. Egen `policyutkast`-tabell bekreftet.

## Tester (tillegg)
Hver klassifikatorregel mutasjonstestet · ukjent skjemasti → UTVIDER ·
omstokket ordnet array → UTVIDER · fjernet vilkår → UTVIDER (ikke
NØYTRAL) · valutaendring → UTVIDER · frekvens 5/dag → 5/uke →
INNSNEVRER · klassifikatorversjon endret mellom åpning og aktivering →
runde kansellert · forfatter alene kan ikke aktivere innsnevring ·
rekalkulert klasse avviker → kansellert · rullbakk får ny versjon med
`rollback_av_versjon` · 20 samtidige aktiveringer → én aktiv, unike
versjoner · teknisk feil → runde består `klar`, retry virker · autorisa-
sjonsavvik → runde kansellert · åpen PR-012-runde kanselleres ved neste
handling, ikke i aktiveringstransaksjonen.
