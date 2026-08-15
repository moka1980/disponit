# PR-012 SPESIFIKASJON v7 — DELTA (motorens menneskeinngang: svar på A/B)

**Draft: Claude.ai · Svar på Claude Codes CP4-blokker. v1 §5 er OPPHEVET
(den motsa v2 §2). Valgt form er (C) — verken ren (A) eller (B).**

## 0. Hvorfor ikke (A) og ikke (B)

**(A) — håndhevelse utenfor motoren:** «re-feed så motoren gir TILLAT via
en policy-sti som allerede tillater det» finnes ikke. Saken ble UNNTAK
fordi beløpet oversteg auto-grensen; skal motoren gi TILLAT uendret må vi
enten forfalske intensjonen eller la porten autorisere og bare logge.
Begge bryter «motoren avgjør». Grensesjekken ville dessuten levd utenfor
den mutasjonstestede kjernen, og revisjonsloggen ville vist en TILLAT
motoren ikke kan reprodusere.

**(B) — syntetisert verifikator-attestasjon:** ville latt att-nøkkelen
PREGE menneskelig godkjenning. Et kompromittert nøkkelregister kunne
forfalsket godkjenninger, og skillet menneske/maskinverifikator (v2 §2)
ville vært borte.

## 1. (C): egen, separat motorinngang for VERIFISERTE menneskefakta

Presedens i koden: attestasjonssignaturer verifiseres i INNGANGSPORTEN
(`sikker_beslutning_pg` → `attestering.kontroller_hendelse`), ikke i
motoren — motoren konsumerer verifiserte fakta. Samme asymmetri her.

```python
evaluate(policy, context, event, ...,
         menneskelig_godkjenning: MenneskeligGodkjenning | None = None)
```
`MenneskeligGodkjenning` er en frossen dataklasse med ALLEREDE
MAC-verifiserte felt (verifisert av `behandle_unntakshandling` før kallet):
`unntak_id · runde · godkjennere[(bruker_id, rolle, authz_version)] ·
target_action · ressurs_id · belop · valuta · hi_integritet_hash ·
godkjennings_policy_hash · utloper`.

**Bindende egenskaper:**
- Den ligger **ALDRI i `event["attestasjoner"]`** — att-nøkkelen kan
  dermed aldri prege en menneskelig godkjenning, og en verifikator kan
  aldri utgi seg for et menneske.
- **Motoren verifiserer ALDRI MAC-en selv** — det er portens jobb (samme
  arbeidsdeling som attestasjonssignaturer i dag).
- **Fravær ⇒ nøyaktig dagens oppførsel.** Ingen eksisterende kodevei
  endres; alle eksisterende tester står uberørt. Deny-by-default.
- Kun `behandle_unntakshandling` kan populere parameteren (ingen
  API-rute, ingen arbeider, ingen klient — Codex-port).

## 2. Motoren eier grensen — porten duplikaterer den ALDRI

`menneskelig_overstyring.belop_maks` kontrolleres **i motoren**, ikke i
`behandle_unntakshandling`. Duplisert grensesjekk ville drevet fra
hverandre ved første policyendring. Arbeidsdelingen er skarp:

| Porten (`behandle_unntakshandling`) eier | Motoren eier |
|---|---|
| MAC-verifikasjon av konvolutten | Om godkjenningen faktisk gir TILLAT |
| Utløp, runde, `klar`-status | `(grunnkode, handling)` ∈ `godkjennbare` |
| Medlemskap, rolle-eksistens, `authz_version` | `krever_rolle`-match |
| Fire-øyne-telling (to ulike brukere) | `belop_maks` + `valuta`-match |
| Idempotens, låsing, fencing | Alle ØVRIGE policyvilkår |

## 3. Godkjenningen løfter ÉN blokkerende grunn — aldri alt

Dette er kjernen i at det ikke blir en blankofullmakt:
- Motoren identifiserer den/de blokkerende grunnkodene.
- Godkjenningen løfter KUN de grunnkodene som står i `godkjennbare` for
  den `target_action`, og kun innenfor `belop_maks`/`valuta`.
- **Alle andre kontroller må fortsatt passere**: rolle, dataklasser,
  frekvens, tidsvindu, øvrige vilkårsattestasjoner, reverserbarhet.
  Feiler noen av dem → STOPP/UNNTAK som ellers, tross gyldig godkjenning.
- Ingen godkjenning kan gjøre en handling utenfor `godkjennbare`
  tillatt — der er den usynlig for motoren.

## 4. Reproduserbarhet og evidens
- Beslutningen er deterministisk: samme (policy, kontekst, hendelse,
  godkjenning) → samme utfall.
- Revisjonsloggens begrunnelseskjede får egen kode
  `menneskelig_godkjenning_anvendt` med parametre (runde, godkjennere,
  anvendt grunnkode, `belop_maks` som ble brukt) — så en revisor kan
  kjøre beslutningen på nytt og se nøyaktig hva mennesket løftet.
- `godkjennings_policy_hash` i strukturen må matche policyen motoren
  evaluerer mot; avvik → STOPP (`godkjenning_policy_avvik`).

## 5. Bindende motortester (ny negativ suite)
Godkjenning present, rolle feil → STOPP · beløp over
`menneskelig_overstyring.belop_maks` → STOPP (ikke TILLAT) · valuta
avviker → STOPP · `target_action` i strukturen ≠ hendelsens handling →
STOPP · grunnkode utenfor `godkjennbare` → uendret utfall (godkjenningen
er usynlig) · godkjenning present men ANNET vilkår feiler → STOPP/UNNTAK ·
`godkjennings_policy_hash`-avvik → STOPP · **fravær → bit-identisk
oppførsel med dagens motor** (regresjonsport mot hele eksisterende suite)
· godkjenning i `event["attestasjoner"]` ignoreres fullstendig av motoren
(kan ikke smugles inn den veien).

## 6. Konsekvens for CP3 og CP4
- **CP3:** `menneskelig_godkjenning` er IKKE en attestasjon i
  `event["attestasjoner"]`. v1 §5s formulering er opphevet.
- **CP4:** motoren SKAL endres — men additivt og lite: én valgfri
  parameter, én ny gren, ingen endring i eksisterende stier.
`behandle_unntakshandling` speiler `_flyt` inline (én transaksjon) som
Claude Code planla — riktig, siden `kjerne.behandle` eier commit.

## Claude Codes fire egne gap — godkjent som beskrevet
`saksversjon` som monoton teller i migrasjon 011 bumpet av
kolonnelås-triggeren · separat-forbindelse sikkerhetsevidens (V3-helperen)
· gjenbrukbar CSRF-sjekk · browser-mutasjonsscope hardblokkert i
`_autentiser`. Alle fire er riktige og trenger ingen spesifikasjonsendring.
