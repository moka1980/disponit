# M56-AKSEPT-ARCEN — KLARSIGNAL (GO, konsoliderer blokkert-notene)

**Til Claude Code · Grunnlag: debrief 2026-08-21 ettermiddag, prod
`788bd83`, migrasjoner 1→51. Svar på §3-valget: **blokkert-notene ER
spec-en** — dette dokumentet konsoliderer dem og fastsetter kun det som
manglet: grensene for de nye tellerne, én-målings-regelen og
sammenhengskravet. Ingen re-litigering av punktene; sløyfa blokkerte
alle tre med rette. K1–K5 gjelder rundene. Migrasjon (om målekoden
trenger en): neste ledige mot main.**

---

## 1. De tre punktene, med grensene fastsatt

### 1.1 `rollback_testet`

Notene står: drillen skal **boote** rullbakk-releasen, ikke bare flippe
registerradene — 20/8-drillen målte registeret, ikke kjøringen.

**Grense:** i én måling skal (a) rullbakk-releasen boote og **fullføre
det ventende oppdraget selv** (signert kvittering fra *dens* kjøring,
attestert release == rullbakk-releasen), (b) drenert release ha null
claims, (c) kandidaten overta og promotere etterpå. Alle tre i **samme
drillrad**.

- **Én drill er én måling.** Feiler et kontrollpunkt, forkastes hele
  målingen og drillen kjøres på nytt med **to ferske, ubrukte
  release-id-er** — aldri spleising av kontrollpunkter fra ulike
  forsøk. (Enveis livsløp gjør gjenbruk umulig uansett; regelen gjør
  det også utenkelig.)

### 1.2 `syntetisk_datasett_likt_lokalt` (målekode finnes ikke — bygges)

**Grense:** datasettets sha256 skrives i evidensstrømmen ved
staging-kjøringen **og** bæres til artefaktets oppsett; punktet er `ja`
kun ved **byte-likhet begge steder** (SP-11). Negativ port: én byte
endret i ett av leddene → punktet rødt.

### 1.3 `revisjonslogg_korrekt` (målekode finnes ikke — bygges)

Punktet lånte fristtellingen; nå egne observasjoner, per kjøring:

**Grense:** for de **ti bestilte** kjøringene i stagingrunden:
10/10 kvitteringer med signatur satt og verifisert identisk,
resultathash til stede, og attestert release/regelsett/hash **lik det
kjøringen faktisk gikk på**; og 10/10 revisjonsrader talt mot
bestillingene. **Null avvik i begge tellinger** — 9/10 er rødt, ikke
«nesten».

## 2. Sammenhengskravet (nytt, og det eneste jeg legger til)

**All evidens for v2-grensens 22 punkter måles på ÉN ny, full
stagingrunde + ÉN ny drill** — ferske release-id-er, samme
manifest-commit. Ingen spleising av gamle målinger (19/8-artefaktet,
20/8-drillen) med nye: aksepthendelsen skal referere et evidenssett som
beviselig beskriver **samme kjøring av samme kandidat**. De gamle
målingene forblir historikk; de gjenbrukes ikke som akseptbevis.

Dette følger av A1–A3-logikken (bevis bundet til eksakt
release/deployment), men fortjener å stå som eget krav siden fristelsen
til å gjenbruke den grønne 19/8-kjøringen er reell.

## 3. Rekkefølgen a–e er bindende

(a) målekode-PR: sjekklisteobservasjoner + konverterfelt +
KRAVGRENSER-registrering av de nye tellerne (grensene fra §1) →
(b) deploy → (c) ny full stagingrunde + ny drill, ferske release-id-er →
(d) bindings-PR: tre punkter → `ja`, ny manifesthash, release-bump per
porten → (e) `m56-aksept.py` mot v2-grensen skriver aksepthendelsen.

UMAALTE-regelen står gjennom hele løpet: et punkt uten måling
blokkerer. Og aksepthendelsen skrives fortsatt kun av akseptfunksjonen,
med komplett punktsett, attestant ≠ akseptør.

## 4. Porter (tillegg til de eksisterende akseptportene)

1 Drill der rullbakk-releasen ikke selv fullførte oppdraget (kvittering
attestert på annen release) → `rollback_testet` rødt · 2 Spleiset
drill (kontrollpunkter fra to forsøk) → avvist; én måling, én rad ·
3 Datasett-sha256 ulik i ett ledd → rødt (negativ byte-port) ·
4 9/10 kvitteringer eller 9/10 revisjonsrader → rødt · 5 Kvittering med
attestert release ≠ kjøringens → rødt · 6 Evidenssett med målinger fra
to ulike stagingrunder/manifest-commits → aksept avvist
(sammenhengskravet) · 7 Gjenbruk av release-id i ny drill → avvist av
registeret (enveis livsløp — regresjon).

## 5. Evidensgrense — tillegg til `m56-akseptflipp-v2`

`drill.rullbakk_bootet_og_fullforte = ja` ·
`drill.spleisede_malinger = 0` ·
`datasett.sha_ulik_mellom_ledd = 0` ·
`kvittering.attest_avvik = 0` · `revisjonsrad.avvik_mot_bestilt = 0` ·
`evidens.pa_tvers_av_runder = 0` ·
`aksept.gjenbrukt_gammel_evidens = 0`.

## 6. Utenfor arcen, notert

- **M-16s manuelle tastaturgjennomgang** ligger hos eier/neste
  browser-økt — flagget, og `ui.tastaturgjennomgang_dokumentert`
  står `nei` til den er utført. Ingen unntak fra grensen.
- **m02-aksept-utkastet er mitt neste**, og avhengigheten noteres der:
  m56-manifestflippen krever nå **både** m02-aksept **og** denne arcens
  fullføring — to forutsetninger, begge bevisbårne.
- #119-maskineriet går parallelt fra råstoff-taggen, med SP-13-fasiten.

---

```
NÅ:    Aksept-arcen a–e mot dette klarsignalet — Claude Code
       — platform/modules/m56_wcag_audit/, deploy/staging/,
         manifestskjema (KRAVGRENSER), m56-aksept.py
NESTE: m02-aksept-klarsignalutkast (med dobbeltavhengigheten for
       m56-flippen) — Claude.ai; #119-PR-en — Claude Code;
       M-57-spec / #112 / #115 / #116 / #120 etter eiers prioritering
```
