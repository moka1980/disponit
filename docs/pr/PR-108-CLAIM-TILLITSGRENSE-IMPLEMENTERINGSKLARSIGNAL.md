# #108 CLAIM-TILLITSGRENSEN — IMPLEMENTERINGSKLARSIGNAL (GO)

**Til Claude Code · Grunnlag: lesesvar 2026-08-20 mot main `47995ea`,
målt mot ratifiseringens §5-invarianter. R47-1 rir med i samme
migrasjon. Branch: `pr-XXX-claim-tillitsgrense` — PR-nummer settes ved
branch. Stående porter SP-1…SP-12.**

> **Migrasjonsnummer: neste ledige mot main.** Verifiseres rett før
> branch-push og på nytt ved hver merge av main inn i grenen.
> **SP-10 gjelder:** migrasjonen har masse-UPDATE (R47-1-backfillen) og
> skal prøvekjøres mot bebodd base; `SET CONSTRAINTS ALL IMMEDIATE`
> etter hver masse-skriving.

---

## 0. Funnet, i én setning

Hele planvindus-protokollen står bak den ene runtimerollen: en
kompromittert runtime kan claime sitt eget forfalte vindu og felle et
forfalsket `tillat`-tick. 045 strammet *beviskravet*; #108 flytter
*retten til å claime*. Grensen er hvem som får claime — ikke fasiten.

## 1. Avgrensning — bevisst og dokumentert

Lesesvaret viser tre nivåer i repoet, og denne arcen tar bare hullet:

| Vei | I dag | Denne arcen |
|---|---|---|
| Varsel | **Komplett presedens:** egen rolle, egen DSN-cred, runtime uten EXECUTE | Urørt — den er malen |
| Planvinduer | **Hullet:** ingen egen rolle; plan-uniten får runtimens `$DATABASE_URL` | **Fikses — speiler varselmodellen** |
| Saker/oppdrag/kapabiliteter | Halvt skille: `disponit_arbeider` finnes, men runtime beholder EXECUTE — og API-veien *trenger* claim-EXECUTE i dag (`claim_neste_oppdrag` kalles av API-et på modulens vegne) | **Ikke her.** Egen issue opprettes; å flytte den grensen krever API-omlegging og er en større beslutning som ikke skal smugles inn |

Kapabiliteter og varsel tilfredsstiller allerede §5-invariantene de
måles mot; oppdragsveiens observasjon om at epoch-gjerdet fencer på
kvittering og ikke på claimet selv **noteres i den nye issuen**, ikke
her.

## 2. Ny rolle: `disponit_plan_arbeider`

Speiler varselsender-modellen fullt ut, inkludert begrunnelsen i
unit-fila:

1. **Rollen** opprettes i `oppsett-postgresql.sh` (innloggingsrolle,
   eier ingenting, `NOINHERIT` som de andre arbeiderrollene).
2. **Migrer-rettighetssteget** utvides med det nye rollesettet — samme
   mekanisme som `disponit_arbeider`-settet.
3. **Migrasjonen flytter EXECUTE:**
   - `claim_planvindu`, `terminaliser_planvindu`, `frigi_planvindu`:
     REVOKE fra `disponit`, GRANT kun til `disponit_plan_arbeider`
     (+migrator).
   - Rollen får i tillegg nøyaktig de EXECUTE-ene plan-arbeideren
     faktisk bruker i dag: bestillingsveien (`utfor_bestilling`-stien og
     dens definere), tick-/klassifiseringsdefinerne fra 044/045
     (`planvinduer_til_klassifisering`, `plan_nedetid_kandidater`),
     `sett_kontekst`. **Settet utledes statisk fra plan-modulenes
     faktiske kall** (port 4) — ingen håndskrevet liste som kan drifte.
   - Ingenting annet: ikke saker, ikke varsel, ikke policy.
4. **`opp.sh`** skriver ny DSN-cred for `disponit-plan`-uniten
   (`skriv_cred plan …` peker på den nye rollens DSN, ikke
   `$DATABASE_URL`), med unit-kommentar i varselsender-stil: *delte den
   DSN med API-et, ville EXECUTE måttet gis til `disponit`*.

**Deploy-rekkefølgen er lesesvarets flagg 3, og den er bindende:**
rolle → rettighetssteg → migrasjon → DSN-cred → SP-10-prøvekjøring.
Migrasjonen må tåle at rollen allerede finnes (idempotent GRANT), og
opp.sh-steget må komme etter migrasjonen i samme deploy — ellers står
plan-uniten uten fungerende DSN i mellomrommet. Rekkefølgen verifiseres
i prøvekjøringen mot bebodd base, ikke bare beskrives.

## 3. Claimerens identitet: systemattestert, ikke selvrapportert

§5-invariant 2 brytes i dag i alle veiene; her lukkes den for
planvinduene med den billigste formen som faktisk beviser noe:

```sql
ALTER TABLE bestillingsplan_vindu
  ADD COLUMN claimet_av NAME;    -- settes av claim_planvindu: session_user

-- I claim_planvindu (SECURITY DEFINER): NEW-verdien er session_user —
-- innloggingsrollen som faktisk autentiserte, ikke et kallerargument.
-- Kalleren kan ikke oppgi den, og kan ikke lyve om den.
```

- `session_user`, ikke `current_user`: inne i en definer er
  `current_user` funksjonseieren; `session_user` er rollen som logget
  inn — identiteten grensen i §2 nettopp opprettet.
- CHECK-en for tilstandskomplettheten utvides (SP-5-totalt): `aktivt`
  krever `claimet_av IS NOT NULL`; `ledig` krever NULL; `terminal`
  beholder verdien som historikk.
- **Dette er et systemattestert snapshot, ikke en FK** — roller kan ikke
  FK-refereres i PostgreSQL. Det dokumenteres eksplisitt per SP-§3:
  kolonnen påstår «denne rollen holdt claimet da», skrevet av
  lagringen selv, og er dermed den sterkeste formen som finnes for
  akkurat denne identiteten.
- Fencingen på `claim_id` er uendret — den beskytter fortsatt mot
  forsinkede kall fra en tidligere claimer.

## 4. R47-1 — kvorumsvilkåret, kompilerbart

Lesesvarets flagg 1 er riktig: `aktiveringskilde` ligger på `policyer`,
og CHECK kan ikke join'e. Hendelsen får egen kolonne — kolonnen er den
ærlige formen:

```sql
ALTER TABLE policyaktivering ADD COLUMN aktiveringskilde TEXT;

-- Backfill i samme migrasjon: kopier fra policyer via versjonsbindingen
UPDATE policyaktivering pa SET aktiveringskilde = p.aktiveringskilde
  FROM policyer p
 WHERE p.tenant = pa.tenant AND p.policy_id = pa.policy_id
   AND p.versjon = pa.versjon;
SET CONSTRAINTS ALL IMMEDIATE;                    -- SP-10-regelen

ALTER TABLE policyaktivering
  ALTER COLUMN aktiveringskilde SET NOT NULL,
  ADD CONSTRAINT hendelse_kilde_gyldig CHECK
    (aktiveringskilde IN ('styrt','historisk','bootstrap')),
  ADD CONSTRAINT hendelse_styrt_krever_kvorum CHECK (
    aktiveringskilde IS NOT NULL
    AND ( (aktiveringskilde = 'styrt'
           AND attestant_a IS NOT NULL AND attestant_b IS NOT NULL)
       OR (aktiveringskilde IN ('historisk','bootstrap')) ));
```
- `aktiver_policy` setter `'styrt'`; backfill-stien (om den kjøres
  igjen) setter `'historisk'`. Enumverdiene bekreftes mot `policyer`s
  faktiske CHECK ved implementering.
- Kolonnen er immutabel etter INSERT (inn i eksisterende
  `policyaktivering_immutabel`-vern — den dekker allerede hele raden).
- Prod har i dag én hendelse (styrt); backfill-UPDATE-en treffer den
  via versjonsbindingen. **SP-10-prøvekjøringen mot bebodd base er
  obligatorisk** — dette er nøyaktig klassen som stoppet 047.

## 5. Codex-porter

**Rolle og grense (1–7).** 1 `disponit` (runtime): `claim_planvindu` →
`permission denied`; samme for terminaliser og frigi · 2
`disponit_plan_arbeider`: claim → terminaliser → tick fungerer ende til
ende · 3 Plan-rollen kan ikke: claime saker, claime varsel, skrive
policy, lese på tvers av tenant (negativ GRANT-test per funksjon utenfor
settet) · 4 Rollens EXECUTE-sett == statisk utledet kallsett fra
plan-modulene — drift i noen retning feiler porten · 5 Plan-uniten har
ingen `$DATABASE_URL`; DSN-creden peker på plan-rollen (opp.sh-test) ·
6 Varsel-veiens GRANTs uendret (regresjon) · 7 Migrasjonen idempotent på
eksisterende rolle.

**Identitet (8–11).** 8 Claim skriver `claimet_av = session_user`;
verdien kan ikke settes av kalleren (ingen parameter finnes — statisk) ·
9 CHECK: `aktivt` uten `claimet_av` → avvist; `ledig` med → avvist ·
10 Terminal rad beholder `claimet_av` (immutabilitetstriggeren fra 044
dekker den nye kolonnen) · 11 Forsinket kall fra tidligere claimer
fencer fortsatt på `claim_id` (regresjon fra 044 port 46).

**R47-1 (12–16).** 12 Styrt hendelse uten `attestant_b` → avvist av
`hendelse_styrt_krever_kvorum` · 13 Hendelse med `aktiveringskilde =
NULL` → avvist (negativ port fra ratifiseringen) · 14 Ukjent kildeverdi
→ avvist av `hendelse_kilde_gyldig` · 15 `aktiveringskilde` endret etter
INSERT → avvist · 16 Backfill-UPDATE binder eksisterende hendelser via
versjonsbindingen, ikke via gjetting; rapport teller.

**Deploy (17–18).** 17 **SP-10:** migrasjonen prøvekjøres mot seedet
base (0..N−1 → flyttester → N); `SET CONSTRAINTS ALL IMMEDIATE` etter
masse-UPDATE verifisert i selve migrasjonsfila (statisk) · 18 Kjørbar
DDL fra tom base (SP-10s andre halvdel) — begge kjøringene i CI.

**Alle tester konstruerer egen tilstand.** Ingen delt fixture.

## 6. Evidensgrense `claim-tillitsgrense-v1` (defineres FØR arbeidet)

**Sikkerhetsinvarianter:** `runtime.kan_claime_planvindu = 0` ·
`runtime.kan_terminalisere_planvindu = 0` ·
`planrolle.execute_utenfor_settet = 0` ·
`hendelse.styrt_uten_kvorum = 0` · `hendelse.kilde_null = 0` ·
`claim.uten_systemattestert_holder = 0`.

**Øvrige:** `planrolle.mangler_nodvendig_execute = 0` (målt ved
ende-til-ende-kjøring) · `unit.plan_har_runtime_dsn = 0` ·
`varsel.grants_endret = 0` · `claimet_av.satt_av_kaller = 0` ·
`kilde.endret_etter_insert = 0` ·
`ddl.provekjort_mot_bebodd_base = ja` ·
`ddl.migrasjon_kjorer_fra_tom_base = ja` ·
`deploy.rekkefolge_verifisert_i_provekjoring = ja`.

Et punkt uten definert, målbar grense regnes som `nei`.

## 7. Ny issue (opprettes i samme PR, gjør ingenting)

**«Fullfør rolleskillet for saker/oppdrag»:** `disponit_arbeider` finnes,
men runtime beholder EXECUTE fordi API-et claimer på modulens vegne.
Å flytte grensen krever at API-veien får en annen form — større
beslutning, egen arc. Inkluder lesesvarets observasjon: oppdragsveiens
epoch-gjerde fencer på kvittering, ikke på claimet selv.

---

```
NÅ:    Implementer #108 mot dette klarsignalet — rolle, EXECUTE-flytt,
       claimet_av, R47-1, deploy-rekkefølgen fra §2, SP-10-prøvekjøring
       — Claude Code
       — oppsett-postgresql.sh, platform/core/db/migrations/NNN_claim_
         tillitsgrense.sql, platform/core/plan/, deploy/opp.sh
NESTE: Etter merge: akseptflipp-arcen for m56 (rollback-drill-port +
       evidensbinding); #112 i neste policykonsolidering — Claude Code
       / Claude.ai
```
