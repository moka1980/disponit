# PR-012 CP4 — MOTORENDRINGEN, IMPLEMENTERINGSKLARSIGNAL (GO)

**Til Claude Code · Svar på A/B-blokkeren: form **(C)** — egen verifisert
faktakanal. GO for v7 + v8. Dette utfyller PR-012-klarsignalet; alle ni
vilkår og femten porter der gjelder fortsatt.**

## Formen, kort

`evaluate(..., menneskelig_godkjenning: MenneskeligGodkjenning | None = None)`
- Frossen dataklasse med ALLEREDE MAC-verifiserte fakta.
- **Aldri i `event["attestasjoner"]`** — att-nøkkelen kan ikke prege en
  menneskelig godkjenning; en verifikator kan ikke utgi seg for et menneske.
- Motoren verifiserer aldri MAC-en; porten gjør det (samme arbeidsdeling
  som attestasjonssignaturer i dag).
- Kun `behandle_unntakshandling` kan populere den.
- **Motoren eier `belop_maks`** — porten duplikaterer den aldri.

**Feltene (v8 §1 + presisering 1):** `tenant` · `target_action` ·
`ressurs_id` · `belop` · `valuta` · `hi_integritet_hash` ·
`bundet_grunnkode` · `unntak_id` · `runde` · `godkjennere[]` ·
`godkjennings_policy_hash` · `utloper`.
`tenant` ligger eksplisitt i typen (ikke bare i konvolutten) fordi
motorens likhetskontroll trenger den.

## De tre bindende presiseringene

### P1. `tenant` i den frosne typen
Se over — eksplisitt felt, inngår i de seks likhetskontrollene.

### P2. Mengden blokkerende grunnkoder er MOTORENS
API og UI teller eller rekonstruerer den ALDRI. `tillatte_handlinger[]`
utleder `godkjenn` fra motorens autoritative evaluering. **Kan motoren
ikke sikkert fastslå hele mengden → `godkjenn` utilgjengelig,
fail-closed** (`aarsak_utilgjengelig: blokkerende_grunner_uavklart`).

### P3. Innsamling av flere grunner KUN i den nye grenen
Kall UTEN `menneskelig_godkjenning` følger dagens kontrollrekkefølge
uendret og gir **bit-identisk resultat OG begrunnelseskjede**. Den nye
grenen må ikke endre når eller i hvilken rekkefølge eksisterende
kontroller kjører — den legger seg etter dem.

## Rekkefølge i den nye grenen (bindende)
1. Eksakt likhet på alle seks felt (tenant, target_action, ressurs_id,
   belop, valuta, hi_integritet_hash) → avvik i ett = **STOPP +
   sikkerhetsevidens**.
2. `godkjennings_policy_hash` matcher policyen som evalueres → ellers STOPP.
3. `bundet_grunnkode` ∈ `godkjennbare` for denne `target_action` → ellers
   ingen overstyring (godkjenningen er usynlig).
4. `krever_rolle`-match mot godkjennerne.
5. `belop_maks` evalueres mot **hendelsens autoritative beløp** (etter
   bevist likhet), med `valuta`-match.
6. Løft KUN `bundet_grunnkode`. Flere blokkerende grunner → ingen TILLAT.
   Bundet grunnkode ikke lenger blokkerende → ingen overstyring.
7. Alle øvrige kontroller må fortsatt passere.

## De syv Codex-portene for CP4
1. Alle seks feltavvik → STOPP + sikkerhetsevidens (én test per felt)
2. Hvert felt mutasjonstestes — ingen likhetskontroll kan fjernes uten rød test
3. Grensen evalueres mot hendelsens beløp, ikke konvoluttens kopi
4. Én godkjenning kan ikke løfte en annen eller en ekstra grunnkode
5. Ingen offentlig eller ordinær beslutningsvei kan konstruere faktakanalen
6. **Hele eksisterende motorsuite grønn og uendret uten menneskefakta**
   (bit-identisk begrunnelseskjede — regresjonsporten)
7. Revisjonsloggen binder godkjenning, `bundet_grunnkode`, policyhash og
   beslutningsutfall

## Datamodell-konsekvens (migrasjon 011)
`bundet_grunnkode` inn i BÅDE `godkjenningsrunde` og MAC-konvolutten
(konvoluttversjon bumpes). Grunnkoden er **server-utledet fra sakens
begrunnelseskjede** ved åpning av runden — aldri klientvalgt.
`saksversjon` som monoton teller bumpet av kolonnelås-triggeren, som
planlagt.

## UI-konsekvens
`godkjenn` vises kun ved nøyaktig én blokkerende grunnkode som er
godkjennbar; ellers `aarsak_utilgjengelig`
(`flere_blokkerende_grunner` | `blokkerende_grunner_uavklart` |
`ikke_godkjennbar_grunn`). `HandlingDialog` viser den konkrete grunnen i
klartekst, så mennesket ser nøyaktig hva det binder seg til.

## Claude Codes fire egne gap
Godkjent som beskrevet — `saksversjon` i 011, separat-forbindelse
sikkerhetsevidens (V3-helper), gjenbrukbar CSRF-sjekk, browser-mutasjons-
scope hardblokkert i `_autentiser`. `behandle_unntakshandling` speiler
`_flyt` inline (én transaksjon) siden `kjerne.behandle` eier commit.
