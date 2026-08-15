# PR-014 SPESIFIKASJON — Modul-eksistens-port + første handlingsmodul (WCAG-audit)

**Draft: Claude.ai · To ting i én leveranse fordi de er hverandres
forutsetning: porten som krever at handlinger peker på ekte moduler, og
den første modulen som faktisk gjør noe. Dette blir også **den første
ekte eiermodulen** mot outbox-kontrakten — til nå har den andre enden
vært syntetisk.**

**Forutsetning:** `m37_unntak` modulaksept lukket først (rollback-driver +
staging-måling). Bootstrap-regelen fravikes ikke.

---

# DEL A — Modul-eksistens-porten

## A1. Problemet, presist

Det finnes ingen validering av at `handling.modul` eksisterer. Alle tre
bransjemalene passerer `valider_policy`. Den eneste vakten er runtime:
`oppdragskontrakt.Oppdragstype` nekter å lage oppdrag for uregistrert
type — trygt, men feilen kommer sent, ved første utførelsesforsøk, og er
usynlig frem til da.

## A2. Porten

**Ved AKTIVERING (PR-013 lag 3, semantisk validering) legges til:**
> Et utkast kan ikke aktiveres hvis noen handling refererer en modul
> eller oppdragstype som ikke er **registrert OG aktiv** i
> modulregisteret (`registry.py`) på aktiveringstidspunktet.

- Feilen returneres som strukturert kode med sti
  (`handlinger[3].modul: modul_ikke_registrert`), oversatt i UI.
- Sjekken kjøres **under aktiveringslåsen** som resten av lag 1–3
  (PR-013 V4: rekalkulering fra låste rader) — en modul som deaktiveres
  mellom validering og aktivering fanges.
- **Utkast og validering påvirkes ikke.** Man kan forfatte og validere
  mot moduler som ikke finnes ennå; man kan bare ikke aktivere.

## A3. Bransjemalene

Trenger ingen merking eller egen profil. De er **strukturelle
utgangspunkt** man forfatter et utkast fra. Porten håndhever at utkastet
trimmes til registrerte moduler før aktivering. Malene beholdes urørt i
`policies/` som referansemateriale.

## A4. Konsekvens (tilsiktet)
Aktivering av en policy med forretningshandlinger blir **umulig før
handlingsmoduler finnes**. Det er riktig press, ikke en regresjon.

---

# DEL B — M-WCAG-AUDIT: første handlingsmodul

## B1. Hva modulen gjør — og ikke gjør

**Gjør:** kjører en WCAG 2.1-revisjon av en angitt URL og produserer en
strukturert rapport som lagres som artefakt.
**Gjør IKKE i v1:** sender ingenting til noen, endrer ingenting hos
kunden, kontakter ingen. Rapporten produseres og lagres — det er alt.

Reversibilitet: **`direkte`** (en rapport kan forkastes). Det er derfor
denne er først: hele kjeden trenes uten at noe irreversibelt kan skje.

## B2. Handling og oppdragstype

```yaml
handlinger:
  - navn: wcag.revider_nettsted
    modul: m_wcag_audit
    modus: auto                      # eller auto_med_vilkaar per kunde
    grenser:
      frekvens: {maks: 4, vindu_enhet: dag, vindu_antall: 1}
    vilkaar: [domene_eid_av_kunde]   # se B4
    reversering: {type: direkte}
    ved_brudd: unntakskø
```
**Oppdragstype** (`oppdragskontrakt.py`):
`audit.revider` — prefiks `("audit.",)`, eiermodul `m_wcag_audit`.
**Lukket payload-skjema** (PR-006 v4 §4):
`{url, wcag_nivaa[A|AA], omfang[enkeltside|nettsted], maks_sider}`.
Ingen persondata; ingen kildereferanser trengs.

## B3. Den første ekte eiermodulen

Eiermodulen er en egen prosess (systemd-unit `disponit-m-wcag.service`,
egen Unix-bruker, egne credentials — PR-009 v2 §3-mønsteret) som:
1. `POST /v1/oppdrag/claim` med modultoken, scope
   `orders:execute:audit.` → får oppdragsmetadata + minimert payload +
   **kvitteringskapabilitet**.
2. Kjører revisjonen (axe-core i headless browser, samme motor som
   CI-porten bruker på vårt eget UI — gjenbruk).
3. Lagrer rapporten som artefakt (se B5).
4. `POST /v1/oppdrag/kvittering` med **signert `resultatkvittering`**
   (HMAC mot registrert verifikatornøkkel, JCS-kanonisert), bundet til
   tenant, oppdrag_id, ressurs_id, resultathash.

**Dette er første gang kvitteringskontrakten møter en virkelig utfører.**
Alt den syntetiske eiermodulen beviste (PR-006 v3 §8, åtte evidenspunkter)
må gjelde uendret: kun de to endepunktene, null direkte DB-skriving,
owner-fencing, idempotens på resultathash, motstridende kvittering →
sikkerhetssak.

## B4. Vilkåret `domene_eid_av_kunde` — hvorfor det må finnes

Å skanne et nettsted er ikke gratis for eieren: det genererer trafikk og
kan trigge sikkerhetsvarsler. **En agent må ikke kunne rette en
revisjon mot en vilkårlig URL.** Derfor:
- Vilkåret krever attestasjon fra en verifikator som bekrefter at domenet
  tilhører tenanten (DNS-TXT-verifisering eller kundeportal-registrering).
- Verifikatoren er `v_domene`, betrodd for `domene_eid_av_kunde`.
- Uten gyldig attestasjon → UNNTAK, ikke utførelse.
- **Robots/rate:** eiermodulen respekterer `robots.txt` og har hard
  hastighetsgrense; brudd på dette er en driftsfeil, ikke policyvalg.

## B5. Rapportartefaktet

Lagres kryptert med tenant-DEK (samme envelope som unntakspayload):
`{url, tidspunkt, wcag_nivaa, sider_revidert, funn[{regel_id, alvorlighet,
antall, eksempler[selector]}], sammendrag{kritisk, alvorlig, moderat,
lav}, verktoy_versjon, axe_versjon}`.
Lukket skjema, `additionalProperties: false`, versjonert.
**`verktoy_versjon` og `axe_versjon` er obligatoriske** — en rapport uten
kjent verktøyversjon kan ikke reproduseres og er derfor ikke evidens.
Retention: 12 måneder, crypto-shredding som ellers.

## B6. Modulmanifest

`platform/modules/m_wcag_audit/manifest.yaml` med egen
staging-sjekkliste. **Evidensgrense `wcag-audit-v1` defineres FØR
arbeidet** (KRAVGRENSER): 10 revisjoner mot syntetisk testnettsted ·
alle 10 gir signert kvittering innen frist · rapportskjema validerer ·
axe-funn matcher fasit på testnettstedet (kjent antall brudd) ·
revisjon uten `domene_eid_av_kunde`-attestasjon → UNNTAK, ingen trafikk
mot målet · robots.txt respektert (verifisert i testnettstedets logg) ·
frekvensgrense håndhevet · rapport aldri i klartekst i logg/DB-dump.

## B7. Fire samtidighetsspørsmål

| Kontroll | Alle veier inn? | Samtidighet? | Riktig vs velformet? | Lukket format? |
|---|---|---|---|---|
| Modul-eksistens | Kun aktiveringsveien, under lås | Modul deaktivert mellom validering og aktivering → fanget | Krever registrert OG aktiv, ikke bare navnet | Registeroppslag, ingen strengmatch |
| Oppdrag-claim | Kun `/v1/oppdrag/claim` | Owner-fencing (PR-006) | Modultoken må ha `audit.`-prefiks | Lukket payload-skjema |
| Kvittering | Kun `/v1/oppdrag/kvittering` | Idempotens på resultathash; motstridende → sikkerhet | Signatur mot registrert nøkkel | JCS, lukket konvolutt |
| Domeneverifikasjon | Policyvilkår, motoren | Attestasjon kan utløpe → UNNTAK | Attestasjon bundet til ressurs_id (domenet) | Verifikator i `betrodd_for` |

---

## Spørsmål til ChatGPT

1. **Modul-eksistens-porten ved aktivering:** riktig plassering, eller bør
   den også gjelde ved *validering* (som advarsel, ikke feil), så
   forfatteren ser problemet mens hun skriver i stedet for ved aktivering?
2. **`domene_eid_av_kunde` som policyvilkår** vs. som en hard kontroll i
   eiermodulen: jeg har lagt den i policyen fordi den da er synlig,
   reviewbar og kundekonfigurerbar — men det betyr at en kunde teoretisk
   kunne fjerne vilkåret (som UTVIDER, med fire øyne). Bør skanning av
   ikke-verifiserte domener i stedet være en plattformregel som ikke kan
   fjernes?
3. **Eiermodulen kjører en headless browser** — det er en betydelig
   angrepsflate (kjører fremmed JS). Bør v1 kreve at den kjører i egen
   sandkasse/container med eget nettverkssegment, eller er egen Unix-bruker
   + systemd-herding tilstrekkelig på staging?
