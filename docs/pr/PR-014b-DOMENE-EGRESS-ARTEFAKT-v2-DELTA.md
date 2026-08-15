# PR-014b SPESIFIKASJON v2 — DELTA (fire bindende vilkår + to presiseringer → GO)

**Draft: Claude.ai · Retningen står: migrasjon 014, `domenekontroll_verifisert`,
TLS-terminerende proxy, staged→promotert-artefakt. Fire vilkår lukket.
Den bærende rettelsen: tilbakekalling måtte håndheves per request, ikke
per navigasjon — og «subressurs» var ikke en autorisasjon.**

014a er urørt. Ingenting her endrer migrasjon 013.

## 1. B1 — tilbakekalling håndheves per proxiet request

v1 lovet «umiddelbar» tilbakekalling, men kontrollerte bare ved
toppnivånavigasjon. Et oppdrag som hadde gjort sin siste navigasjon kunne
fortsette å hente ressurser på en gjenbrukt forbindelse. Rettet:

- **Hver proxiet request — hver HTTP/2-strøm, også på gjenbrukt
  forbindelse — starter med et ferskt autoritativt oppslag.** Ingen lease,
  ingen cache, ingen gammel proxy-attestasjon.
- Oppslaget er én radlesning mot en smal, egen visning:
```sql
CREATE VIEW v_domeneautorisasjon AS      -- eier: NOLOGIN, ingen RLS-omgåelse for runtime
  SELECT tenant, hostname, autorisasjonsgenerasjon, (status = 'verifisert'
         AND now() < utloper
         AND siste_vellykkede_revalidering > now() - interval '72 hours') AS gyldig
  FROM domenekontroll;
```
  `domenekontroll` får `autorisasjonsgenerasjon BIGINT NOT NULL DEFAULT 0`
  (monoton, økes ved hver status- eller tilbakekallingsendring).
  Proxy-sesjonen bærer generasjonen den ble utstedt med; **avvik eller
  `gyldig = false` → request blokkert, oppdrag → UNNTAK.**
- Egress-proxyen får **egen DB-rolle `disponit_egress` med SELECT KUN på
  `v_domeneautorisasjon`** — ingen andre tabeller, ingen skriverett.
  Negativ GRANT-test. (B1 gir proxyen en DB-vei den ikke hadde; da må
  veien være så smal som kontrollen krever, ikke bredere.)
- **DB utilgjengelig → alle requests blokkeres.** Fail-closed, ikke
  degradering.

**Lukket metode- og protokolliste (positiv):** kun `GET` og `HEAD`.
`CONNECT` godtas **utelukkende** som inngang til TLS-terminering mot
port 443 på autorisert hostname — aldri som ugjennomsiktig tunnel.
`Upgrade`/WebSocket, h2c, `TRACE` og alt utenfor listen avvises lukket og
telles som brudd.

**Nøkkel- og hodegrense:** browser-containeren får **kun det offentlige
trust anchor-et** for den interne CA-en. **CA-privatnøkkelen finnes aldri
i browser-imaget** — verifisert som deploy-port, samme form som
testnøkkelporten i 014a §6. `Proxy-Authorization` og proxy-tokenet er
hop-by-hop, strippes i proxyen og når aldri origin. Ingen request- eller
responskropp logges.

**Ærlig om rekkevidden:** garantien er at **enhver request hvis
autorisasjonskontroll starter etter at tilbakekallingen er committet,
blokkeres**. Et svar som allerede er under overføring avbrytes
best-effort — det loves ikke som invariant, fordi databasen ikke kan
bevise det.

## 2. B2 — konkurrerende domenekontroll serialiseres globalt per hostname

Delindeksen hindret slutt-tilstanden «begge verifisert», men en
constraint-feil er ingen konkurranseprotokoll. Rettet med samme mønster
som `registrer_oppdragstype()` i 014a §2:

```sql
CREATE TABLE hostname_binding (           -- ingen RLS; runtime har INGEN SELECT
  hostname TEXT PRIMARY KEY,
  tenant TEXT NOT NULL,
  bundet_ts TIMESTAMPTZ NOT NULL DEFAULT now());
```
- All verifisering og overtakelse går gjennom **én herdet funksjon**
  (SECURITY DEFINER, NOLOGIN-eier, `search_path=pg_catalog`) som tar
  **`pg_advisory_xact_lock` på hostname som global nøkkel FØR** noen
  tenant-rad røres.
- Funksjonen ser eksisterende binding gjennom `hostname_binding` —
  **en egen, ikke-RLS-tabell uten SELECT for runtime**. Cross-tenant-innsyn
  finnes i funksjonen, ikke i systemet.
- Taper nummer to ser da **committet tilstand**, ikke en unique violation,
  og går den veien B4 foreskriver. **Utfallet er deterministisk, ikke en
  feilkode.**
- Hele overgangen (A tilbakekalt · B-rad · `hostname_binding` ·
  hendelseslogg) er atomisk i én transaksjon.

**Låserekkefølge (erstatter v1 §8 første ledd):** verifiserings- og
overtakelsestransaksjonen tar **kun** hostname-låsen og
`domenekontroll`-rader (sortert på `tenant`), og **aldri** en lås videre i
014a-kjeden. **Den rører ingen oppdragsrader** — pågående oppdrag stoppes
gjennom `autorisasjonsgenerasjon` og B1s per-request-kontroll (lat
deteksjon, som PR-013 §8). Dermed oppstår ingen ny syklus mot
oppdrag/release/artefakt. `artefakt` låses fortsatt sist.

## 3. B3 — samme hostname gjelder også subressurser

Begrunnelsen «browseren har ingen credentials å lekke» var en
skadebegrensning, ikke en autorisasjon. En fremmed side kan selv generere
requests mot vilkårlige offentlige verter; «ser ut som en subressurs» er
ingen tillatelse. Rettet:

- **Hver proxiet request, toppnivå som subressurs, må gå til nøyaktig det
  autoriserte hostnamet.** Alt annet blokkeres. Ingen unntak i v1.
- Blokkeringen er **et felt, ikke en stillhet** — artefaktskjemaet får
  `dekningsbegrensninger` som **påkrevd** felt (tom liste er lovlig):
```json
{"type":"ekstern_ressurs_blokkert","antall":12,
 "verter":["cdn.example.com"],"maks_verter_vist":20}
```
  Kun hostname lagres — aldri path, query eller fragment.
- 014b sier hva som ble blokkert. **014c avgjør hva det betyr for
  dekningsgraden** — 014b forblir modulnøytralt.
- En senere åpning for fremmede origins krever **egen positiv
  autorisasjonsmodell og misbruksgrense**, som eget vilkår. Den smyges
  ikke inn under ordet «subressurs».

## 4. B4 — overtakelse fjerner autorisasjon, men gir den ikke bort

«Nyeste bevis vinner» blandet to fakta: *hvem kontrollerer DNS nå* og
*hvilken tenant plattformen skal autorisere*. Et DNS-kompromiss ble
dermed en tenantoverføringsmekanisme. Rettet — statusenumet utvides med
`avklaring_kreves`, og funksjonen gjør atomisk:

| Tilstand hos A | Utfall for A | Utfall for B |
|---|---|---|
| `verifisert` OG `now() < utloper` (annen tenant) | `tilbakekalt`, grunn `overtatt_dns_kontroll`, generasjon++ | Bevis bevart, status **`avklaring_kreves`** — **kan ikke opprette oppdrag** |
| `utlopt`, `tilbakekalt` eller ingen rad | — | `verifisert`, nytt 90-døgnsvindu |
| Samme tenant reverifiserer | — | `verifisert`, vindu fornyet, **ingen avklaring** |

- Konflikten oppretter **én idempotent M-37-sak**, familie
  `domeneovertakelse`, med lineage til begge rader. Gjenbruk kun av sak
  som positivt er ikke-terminal OG av samme familie — positiv
  tillatelsesliste, som 014a §6. **Terminale saker endres aldri.**
- Avgjørelsen tas i den eksisterende unntaksbehandlingen (PR-012):
  godkjent → B `verifisert` med nytt vindu; avvist → B `tilbakekalt`.
  Mennesket attesterer, motoren beslutter — ingen knapp skriver status
  direkte.
- A stoppes uansett utfall, umiddelbart. Fail-closed for begge.

## 5. To presiseringer fra reviewen

**Idempotens måles på det kanoniske dokumentet.** Serveren hasher
JCS-kanonisert klartekst; da kan ikke idempotensen måles på bytes. Rettet:
samme `kapabilitet_jti` + **samme `klartekst_sha256`** → samme
`artefakt_id`. Samme jti + annet kanonisk dokument → avvist som
motstridende evidens, sikkerhetssak.

**Sikkerhetsinvariant ≠ ytelsesmåling.** `tilbakekalling.stoppet_ms p95`
beholdes, men **kun som ytelsesmåling**. Sikkerhetsporten er
deterministisk: `tilbakekalling.sluppet_gjennom_etter_commit = 0`.

## 6. Evidensgrense — tillegg til `domene-egress-artefakt-v1`

`egress-014b-v1`: `metode.utenfor_liste_avvist = alle` ·
`connect.ugjennomsiktig_avvist = alle` ·
`autorisasjonskontroll.p95_ms < 1` (kostnaden av B1 måles, ikke antas).
`domene-014b-v1`: `tilbakekalling.sluppet_gjennom_etter_commit = 0` ·
`overtakelse.deterministisk_utfall = 1` (aldri unique violation) ·
`overtakelse.oppdragsrader_rort = 0`.
`sandkasse-014b-v1`: `ca_privatnokkel_i_image = nei` ·
`proxy_authorization_mot_origin = 0`.
`artefakt-014b-v1`: `dekningsbegrensninger.felt_pakrevd = ja`.

## Svar på v1-spørsmålene (reviewens, vedtatt)
1. TLS-terminering beholdes, med CA-nøkkelgrensen presisert (§1).
2. Streng samme-vert, med eksplisitt merket dekningsbegrensning (§3).
3. Overtakelse stopper gammel autorisasjon, men gir ikke ny automatisk (§4).

## Tester (tillegg — Codex-porter 28–41)

28. Tilbakekalling committet → hver etterfølgende request blokkert, også
    på gjenbrukt HTTP/2-forbindelse; null sluppet gjennom
29. `CONNECT` som ugjennomsiktig tunnel · `Upgrade`/WebSocket · h2c ·
    metode utenfor {GET, HEAD} → lukket avvisning
30. CA-privatnøkkel finnes ikke i browser-image (deploy-port)
31. `Proxy-Authorization`/proxy-token når aldri origin (trafikkinspeksjon)
32. DB utilgjengelig → alle requests blokkert, oppdrag → UNNTAK
33. `disponit_egress` har SELECT kun på `v_domeneautorisasjon`, ingen
    andre tabeller, ingen skriverett
34. To samtidige challenges for samme hostname → ett deterministisk
    utfall; taperen ser committet tilstand, ikke unique violation
35. Overtakelse rører null oppdragsrader; stopp skjer via generasjon
36. Cross-tenant overtakelse innen aktivt vindu → A `tilbakekalt`,
    B `avklaring_kreves`, B kan ikke opprette oppdrag; M-37-sak idempotent
37. A `utlopt`/`tilbakekalt` → B `verifisert` direkte, ingen avklaring;
    samme tenant reverifiserer → ingen avklaring
38. Avklaring avvist → B `tilbakekalt`; godkjent → B `verifisert` med nytt
    vindu; terminal sak aldri gjenbrukt
39. Subressurs mot fremmed hostname → blokkert; artefakt bærer
    `dekningsbegrensninger` med antall og verter, uten path/query
40. Artefakt uten `dekningsbegrensninger` → avvist av lukket skjema
41. Samme jti + ulik serialisering av SAMME kanoniske dokument → samme
    `artefakt_id`; annet kanonisk dokument → sikkerhetssak

Alle tester konstruerer egen tilstand. Deadlock-testen (port 27) utvides
med samtidig overtakelse, kvitteringsingest og releasepromotering.

---

```
NÅ:    v2-deltaet tilbake gjennom spesifikasjonsporten (B1–B4 lukket,
       to presiseringer innarbeidet) — ChatGPT (Eier relayer)
       — docs/PR-014b-DOMENE-EGRESS-ARTEFAKT-v2-DELTA.md
NESTE: Ved GO: konsolidert implementeringsklarsignal med full DDL for
       migrasjon 014 (spesifikasjon + v2 slås sammen, deltaformen
       forlates) — Claude.ai
       — docs/PR-014b-IMPLEMENTERINGSKLARSIGNAL.md
```
