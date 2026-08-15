# Disponit — overlevering til ny ChatGPT-chat

**Dato:** 2026-08-11  
**Prosjekt:** `moka1980/disponit`  
**Formål:** Autoritativ kontekst for videre spesifikasjonsreview i en ny chat.

## 1. Arbeidsflyt og roller

Disponit bruker denne obligatoriske kjeden:

1. **Claude.ai (arkitekt)** drafter spesifikasjon før kode.
2. **ChatGPT** reviewer spesifikasjonen og gir `GO` eller `NO-GO` med bindende kontrakter.
3. **Claude Code** implementerer kun mot godkjent og konsolidert spesifikasjon.
4. **Codex** reviewer implementasjonen, testene, migrasjonene og evidensportene, og merger først når portene er lukket.

ChatGPT skal være streng på tillitsgrenser, samtidighet, ulykkelige veier, tenantisolasjon, idempotens, fencing, migrerbarhet, revisjon og målbare negative tester. Samtidig skal nye reviewrunder bare kreves for reelle kontraktsfeil — ikke ordvalg eller prosess-teater.

## 2. Produktstatus

- M-1-kundeflaten er live på `disponit.com`.
- Google OIDC-innlogging virker.
- Lese-API og read-only UI er levert.
- M-37 er behandlingsmotoren for unntak og har null egne forretningsfullmakter.
- PR-012 spesifiserer menneskelig unntaksbehandling gjennom ny policybeslutning, ikke status-bypass.
- PR-013 spesifiserer policyadministrasjon med diffbinding, maskinell risikoklassifisering, revisjon og fire øyne.
- Neste forretningsmodul er automatisk WCAG-kontroll. PR-014 ble delt i tre fordi plattformdelene skal arves av alle senere eiermoduler:
  - **PR-014a:** modulregister, kontrakt/release/deployment og aktiveringsport.
  - **PR-014b:** domeneverifikasjon, executor-sandkasse, egress og artefaktprotokoll.
  - **PR-014c:** selve modulen for automatisk WCAG-kontroll.

## 3. Faste arkitekturprinsipper

- Default deny og positive allowlister; ukjent verdi eller status feiler lukket.
- Beslutning, utførelse, kvittering/evidens og sikkerhet er separate akser.
- Ingen offentlig payload får sette serverautoritativ tenant-, aktør-, release-, digest-, epoch- eller sikkerhetskontekst.
- Tenantisolasjon håndheves med PostgreSQL RLS + FORCE og serverbygget kontekst.
- Irreversible eller eksterne sideeffekter går gjennom outbox, claim, fencing og signert kvittering.
- Terminal tilstand endres ikke i ettertid; sen eller motstridende evidens lagres append-only og routes til avklaring/sikkerhet.
- En binding må være streng uten å gjøre legitim patching, migrering eller rollback umulig.
- Policy binder kontrakten modulen lover, ikke hvilken binær som tilfeldigvis kjører.
- Release og artifact digest er deploymentevidens; ikke kryptografisk bevis på at ingen annen kode kjørte.
- Alle porter skal leveres bygget, obligatoriske og udelelige i samme leveranse.
- Migrasjoner er forward-only, runner-eide, checksum-bundne og fail-closed.

## 4. Viktige ferdige beslutninger

### Sesjon og transport

- OIDC-first; ingen lokal passordlivssyklus.
- Browseren får bare HttpOnly-sesjonscookie, aldri bearer-token.
- OIDC bruker state, nonce, PKCE og egen browserbindingcookie.
- Providertransport må være SSRF-sikker med globalt routbare adresser, DNS-pinning og redirect-revalidering.
- API-et nås fra nginx via rettighetsbeskyttet Unix-socket, ikke en «loopback er betrodd»-antakelse.

### Unntaksbehandling

- Mennesket gir en separat, MAC-verifisert godkjenningsfakta; det er ikke en maskinattestasjon.
- Motoren eier alle policygrenser og re-evaluerer hele handlingen.
- Godkjenningen bindes til eksakt sak, intensjon, policy, handling, beløp, valuta, ressurs og én grunnkode.
- Gate 14a bruker positiv allowlist: avvis er bare tillatt når databasen positivt beviser at utførelse ikke kan skje. Ellers settes `avklaring_kreves` og saken avvises ikke.

### Policyadministrasjon

- Godkjenneren attesterer `diff_hash`, ikke bare versjonsnummer.
- Fullmaktsutvidelser krever to forskjellige godkjennere; forfatteren kan være én av dem, aldri begge.
- Klassifikatoren dekker alle motorfelter; ukjent felt eller ukjent semantikk er `UTVIDER`.
- Første policy sammenlignes med deny-all og regnes som utvidelse.
- Aktiv policy bestemmes av én autoritativ peker.
- Semantikkchecksum dekker manifestlisten, relevante filer, biblioteksversjoner og tzdata.

## 5. PR-014a — reviewhistorikk i kortform

Reviewrundene skilte gradvis:

- **Kontrakt:** hva modulen lover (`kontraktversjon`, `kontrakt_hash`, schema-hasher og sideeffektklasse).
- **Release:** immutable implementasjon av en kontrakt.
- **Deployment:** hvilken release som er autorisert i et miljø.
- **Workload-identitet:** serverbundet modul, miljø, release, kontrakt, artifact digest og epoch.

Andre bindende krav:

- Policy og uclaimede oppdrag refererer kontrakten.
- Claim, kvittering og revisjon refererer den konkrete releasen.
- Artifact digest kommer fra autentisert serverkontekst, aldri modulens payload.
- Epoch følger hele kjeden; bare epoch-avvik kan karantenesettes. Andre bindings- eller signaturavvik går til sikkerhet.
- Flere kontrakter kan kjøre parallelt under kontraktsmigrering.
- Kompatible patchreleaser bruker samme kontrakt og krever ikke ny policyaktivering.
- Runtime kan ikke skrive registertabellene direkte.
- Nøddeaktivering overstyrer vanlig draining med epoch og karantene.

## 6. Siste reviewresultat: PR-014a v5

**Resultat: GO**, med tre bindende mekaniske vilkår i det konsoliderte implementeringsklarsignalet:

1. **Atomisk releasebytte:** Ny release settes `claiming` og gammel settes `draining` i én herdet transaksjon under kontraktlås. Direkte statusoppdatering forbys.
2. **Utløpt claim:** Et utløpt claim fra en draining release kan reclaimes av ny claiming release med ny releasebinding og nytt fencing-token. Gammel kvittering blir stale evidens og kan ikke fullføre oppdraget.
3. **Kvittering etter retirement:** `retired` betyr «kan aldri claime», ikke at historiske kvitteringer automatisk er ugyldige. En kvittering fra et eksisterende, ikke-reclaimet claim kan fortsatt mottas innen evidensfristen. Release/deployment kan ikke slettes mens slike bindinger finnes.

Testporten skal også kjøre to samtidige patchbytter og bevise at nøyaktig én release ender i `claiming` for samme modul, miljø og kontrakt.

Det kreves **ikke** et v6-delta eller en ny arkitekturrunde. Claude.ai skal konsolidere PR-014a v1–v5 til ett komplett implementeringsklarsignal med samlet DDL, overgangsfunksjoner, låserekkefølge, GRANT-modell og testporter.

## 7. Nøyaktig neste steg

1. Claude.ai konsoliderer `PR-014a-MODULREGISTER-SPESIFIKASJON.md` og delta v2–v5, inkludert de tre siste bindende vilkårene.
2. Claude Code implementerer PR-014a mot det konsoliderte signalet.
3. Codex reviewer implementasjonen og merger bare når DDL, samtidighet, negative GRANT-tester, patchbytte og epoch/kvitteringsporter er grønne.
4. Parallelt/løpende skal `m37_unntak`-aksepten lukkes med rollback-driver og staging-evidens.
5. Etter 014a drafter Claude.ai PR-014b til full ChatGPT-port.

## 8. Starttekst til ny ChatGPT-chat

Kopier dette som første melding og legg ved denne filen samt dokumentene som skal reviewes:

> Vi fortsetter Disponit-prosjektet. Les `DISPONIT-CHAT-HANDOFF-2026-08-11.md` som autoritativ prosjektkontekst. Arbeidsflyten er Claude.ai spesifikasjon → ChatGPT GO/NO-GO → Claude Code implementasjon → Codex review/merge. PR-014a v5 har GO med tre bindende mekaniske vilkår beskrevet i handoffen. Ikke start reviewhistorikken på nytt. Fortsett fra «Nøyaktig neste steg», og review neste vedlagte spesifikasjon eller implementering mot de etablerte kontraktene.

## 9. Hva som må legges ved i ny chat

Minimum:

- Denne handoff-filen.
- Det nye dokumentet som skal reviewes.

Ved konsolideringskontroll av PR-014a bør også disse legges ved:

- `PR-014a-MODULREGISTER-SPESIFIKASJON.md`
- `PR-014a-MODULREGISTER-v2-DELTA.md`
- `PR-014a-MODULREGISTER-v3-DELTA.md`
- `PR-014a-MODULREGISTER-v4-DELTA.md`
- `PR-014a-MODULREGISTER-v5-DELTA.md`

GitHub-repoet er den autoritative kilden for implementert kode. Dokumentet er autoritativt for samtalekontekst og beslutningshistorikk, ikke en erstatning for inspeksjon av aktuell `main` eller PR-diff.
