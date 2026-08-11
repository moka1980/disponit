# PR-013 SPESIFIKASJON v3 — DELTA (seks monotonikontrakter → GO)

**Draft: Claude.ai · Klassifikatoren må BEVISE monotoni, ikke antyde den.
Seks kontrakter lukket.**

## 1. Frekvens: burst-fullmakt, ikke gjennomsnittsrate

`maks / vindu` er utilstrekkelig — 1/dag og 7/uke har samme
gjennomsnittsrate, men ukegrensen tillater syv handlinger på ett sekund.
Rettet, v1-regel:
- **INNSNEVRER kun når `vindu_enhet`, `vindu_antall` OG scope
  (nøkkelfelt) er UENDRET og `maks` er REDUSERT.**
- **Enhver annen frekvensendring er UTVIDER**: endret vindustype, endret
  vinduslengde, endret scope, økt `maks`, fjernet frekvensgrense.
- Ingen normalisering på tvers av vindustyper i v1.

## 2. Tidsvindu: mengdeinklusjon i en kanonisk uke

Separat sammenligning av `fra`, `til` og ukedager håndterer ikke
nattvinduer (22:00–06:00), ukeovergang eller sommertid. Rettet:
- Begge vinduer ekspanderes til **tillatte tidsmengder i en kanonisk uke**
  (minuttoppløsning, over ukegrensen).
- Nytt sett ⊊ gammelt → **INNSNEVRER**
- Gammelt ⊊ nytt → **UTVIDER**
- Overlappende, ikke sammenlignbare → **UTVIDER**
- **Tidssoneendring → UTVIDER** (uansett)
- **DST følger SAMME tidsbibliotekversjon som motoren** — ekspansjonen
  skal gi identisk resultat som motorens egen tidsvindukontroll (delt
  kodevei, ikke to implementasjoner).

## 3. Én-til-én-mapping mot skjemaets faktiske leaf-stier

Samleposter som `*_kode` og «hele `menneskelig_overstyring`» kunne skjult
et nytt sikkerhetsrelevant underfelt bak en generell regel. Rettet:
- **Hver muterbar leaf-path i skjema v0.2 mappes eksplisitt til ÉN
  klassifikatorregel.** Ingen wildcard, ingen samlepost.
- **Nøytrale felt listes med EKSAKTE stier**, og hver må bevises **ikke
  lest av motoren** (statisk sjekk: stien forekommer ikke i
  `engine.py`/`schema.py`s beslutningsveier).
- **CI-porten feiler begge veier:** skjemaet får en leaf uten regel →
  feil; en regel peker på en leaf som ikke lenger finnes → feil.
- `menneskelig_overstyring` splittes i sine leaf-stier
  (`godkjennbare[].grunnkode`, `.handling`, `.belop_maks`, `.valuta`,
  `krever_rolle`, `krever_fire_oyne`, `begrunnelse_pakrevd`), hver med
  egen regel.

## 4. Tre versjoner bindes, ikke bare klassifikatoren

Samme policyendring kan bety noe annet hvis MOTORENS tolkning endres,
selv med urørt klassifikatorkode. Bind og håndhev:
`policyskjema_versjon · klassifikatorversjon · motor_semantikkversjon`
- Alle tre inngår i runden, attestasjonskonvolutten og
  rekalkuleringen ved aktivering (v2 §2/§3/§5).
- **En motorendring som påvirker policysemantikk MÅ kreve ny
  klassifikatorversjon** — CI-port: endres `motor_semantikkversjon` uten
  at klassifikatorversjonen bumpes, feiler bygget.
- **Åpne runder med tidligere semantikkversjon kanselleres ved
  aktivering** (legges til tilstandsmaskinen i v2 §7).

## 5. Første aktivering: kanonisk deny-all som diffgrunnlag

Diff mot `NULL` er udefinert. Valgt modell:
- **«Ingen aktiv policy» representeres av en versjonert, kanonisk
  deny-all-baseline** (`DENY_ALL_V1`: ingen handlinger, ingen roller med
  fullmakt, alt `alltid_stopp`) — den er diffgrunnlaget.
- Baselinen er plattformkonstant, ikke kundedata; den kan ikke redigeres
  eller aktiveres som kundepolicy.
- **Første kundepolicy klassifiseres derfor automatisk som UTVIDER** (alt
  er nytt) og **krever fire øyne** — riktig: den første policyen er den
  største fullmaktsutvidelsen som noensinne skjer for en tenant.
- Onboarding trenger dermed ingen egen reviewet baseline-PR;
  deny-all-konstanten dekker det.

## 6. «Nøyaktig én aktiv», ikke bare «maks én»

Delindeksen hindrer to aktive rader, men ikke null — en deaktivering uten
etterfølger ville latt en tenant stå uten policy (og motoren ville
fail-closed, men tilstanden er likevel ulovlig). Rettet:
- **Runtime-rollene mangler direkte INSERT/UPDATE/DELETE på `policyer`.**
- Aktivering skjer KUN gjennom den herdede funksjonen (SECURITY DEFINER,
  NOLOGIN-eier, `search_path=pg_catalog`, EXECUTE kun til API-rollen).
- **Funksjonen kan aldri deaktivere uten å sette inn etterfølger i samme
  transaksjon** — deaktivering og innsetting er én udelelig operasjon.
- Negativ GRANT-test: runtime får `permission denied` på direkte skriving.
- **Krasjtest:** feiler innsettingen av den nye raden, består den gamle
  aktive policyen (rollback beviser det).

## Tester (tillegg)
1/dag → 7/uke = UTVIDER · maks ned med uendret vindu = INNSNEVRER ·
nattvindu 22–06 utvidet til 21–07 = UTVIDER via mengdeinklusjon · DST-
overgang gir samme resultat i klassifikator og motor (delt kodevei) ·
ny leaf i skjema uten regel → CI rødt · regel mot slettet leaf → CI rødt ·
nøytralt felt som leses av motoren → CI rødt · `motor_semantikkversjon`
bumpet uten klassifikatorbump → CI rødt · åpen runde med gammel
semantikkversjon → kansellert ved aktivering · første policy for ny tenant
→ UTVIDER + fire øyne · runtime kan ikke skrive `policyer` direkte ·
krasj under aktivering → gammel policy fortsatt aktiv, aldri null aktive.
