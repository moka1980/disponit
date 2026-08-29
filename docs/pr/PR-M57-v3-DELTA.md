# M-57 — v3-DELTA (M57-1a · M57-2a → GO)

**Draft: Claude.ai · Kun de to funnene. M57-3, immutable listeversjoner,
én signert versjon per serie, frigivelse → signatur og alle lukkede
produktvalg er urørt. Migrasjon: neste ledige mot main.**

---

## 1. M57-1a — lineage bundet til serien, og historikken er lineær

To reelle hull, og begge er samme klasse som E1a: FK-en beviste at
forelderen *finnes*, ikke at den **hører hjemme**.

```sql
-- Refererbar nøkkel som bærer serien
ALTER TABLE utsendingsliste
  ADD CONSTRAINT liste_serie_refererbar UNIQUE (tenant, utkast_serie, liste_id);

-- Lineage må ligge i SAMME serie
ALTER TABLE utsendingsliste DROP CONSTRAINT utsendingsliste_forrige_fk;
ALTER TABLE utsendingsliste
  ADD CONSTRAINT liste_forrige_samme_serie
    FOREIGN KEY (tenant, utkast_serie, forrige_liste_id)
    REFERENCES utsendingsliste (tenant, utkast_serie, liste_id);

-- Lineær historikk: høyst ett direkte barn per versjon
CREATE UNIQUE INDEX ett_barn_per_versjon
  ON utsendingsliste (tenant, utkast_serie, forrige_liste_id)
  WHERE forrige_liste_id IS NOT NULL;

-- Og nøyaktig én rot per serie
CREATE UNIQUE INDEX en_rot_per_serie
  ON utsendingsliste (tenant, utkast_serie)
  WHERE forrige_liste_id IS NULL;
```

**Lineær er kontrakten, ikke et tre.** Begrunnelsen står i produktet:
listen er ett dokument under redigering fram mot én signatur, og
spørsmålet «hva ble faktisk godkjent» skal ha ett svar uten å velge gren.
To samtidige redigeringer er ikke to versjoner som sameksisterer — den
andre taper på `ett_barn_per_versjon` og må bygge videre på den første.
Det er samme valg som «ingen delvis signatur», av samme grunn.

Med de to indeksene er serien en kjede: én rot, høyst ett barn per ledd,
alle ledd i samme serie, høyst ett signert ledd. Ingen av påstandene i
teksten hviler lenger på at noen skriver riktig.

**Porter (tillegg):** forelder i annen serie → FK-avvist · to barn av
samme forelder → unikavvist (samtidig forsøk: én vinner, taperen får
konflikt, ikke en stille gren) · to røtter i samme serie → unikavvist ·
kjede fra rot til signert versjon er sammenhengende (lesetest).

**Evidensgrense:** `liste.forelder_i_annen_serie = 0`
(sikkerhetsinvariant) · `serie.forgrenet_historikk = 0` ·
`serie.uten_entydig_rot = 0`.

## 2. M57-2a — lesejobb før siste FK-ledd fryses

Porten har rett, og formuleringen min var nettopp den jeg har blitt tatt
for før: *«FK der skjemaet tillater det; ellers …»* er to uhørte
skjemaantakelser i en setning som samtidig krever en port. Regelen
gjelder også når jeg er nesten sikker.

**Én ren lesejobb, tre spørsmål:**

1. Hva er outbox-radens faktiske identitet og nøkkelform i dag
   (tabellnavn, PK, tenantkolonne), og hvordan opprettes en rad —
   hvilken funksjon, hvilke argumenter?
2. Finnes det allerede et **generisk referansefelt** på outbox-raden
   (opphav, kilde-ID, korrelasjonsreferanse) som kan bære
   `frigivelse_id` med FK — og er det i så fall nullbart i dag?
3. Bærer outbox-raden allerede tenant, og kan en kompositt-FK
   `(tenant, frigivelse_id)` legges uten å kollidere med eksisterende
   constraints eller med 038-opprinnelsesdisiplinen?

**Forhåndsbestemte utfall** (ingen designrom, som porten sier):

- Finnes generisk FK-egnet referanse → **den brukes**, med kompositt-FK
  på tenant.
- Finnes den ikke → **liten generisk referanseutvidelse med FK**: én
  kolonne som betyr «denne outbox-raden ble frigitt av», uten
  ATS-semantikk, brukbar for enhver senere modul med samme behov.
- **Ingen tredje mulighet.** Ingen modell der AST-port eller
  funksjonskonvensjon alene bærer forbindelsen; hvis ingen av de to
  formene lar seg gjennomføre, stopper arcen og problemet kommer
  tilbake til porten — den skal aldri løses ved å svekke påstanden.

Nullbarhet håndteres slik: er kolonnen generisk og delt, er den nullbar
for andre opphav, men **for ATS-utsendelser håndheves den som påkrevd**
via utsendingsfunksjonen *og* en CHECK bundet til radens opprinnelse
(SP-5-total form — eksplisitt `IS NOT NULL` per arm, ingen NULL-arm som
slipper gjennom).

**Porten som må kunne kjøres med direkte DML etterpå:** ATS-outbox-rad
uten frigivelsesreferanse → avvist av lagringen; frigivelse uten
signatur → avvist; altså **ingen gyldig signatur ⇒ ingen representerbar
ATS-utsendelse**, bevist uten å gå gjennom funksjonen.

---

```
NÅ:    Outbox-lesejobben (tre spørsmål, ren lesing) — Claude Code
       — ~/Codex/ ; parallelt: v3-deltaets §1 kan porteres nå
NESTE: Klarsignalet fryses når lesesvaret er inne (utfallene er
       forhåndsbestemt), med tallfestede grenser og evidensgrense
       `m57-v1` — Claude.ai → Claude Code
       — docs/pr/PR-M57-IMPLEMENTERINGSKLARSIGNAL.md
```
