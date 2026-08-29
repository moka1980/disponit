# DISPONIT — OVERLEVERING TIL NY CHAT

**Lim inn dette først i den nye samtalen. Repoet (`docs/`) er
autoritativt; dette er navigasjonskartet.**

---

## 1. Hvem er hvem

| Rolle | Ansvar |
|---|---|
| **Eier** (moka1980) | Godkjenner retning, eier nøkler/kontoer, tar produktbeslutninger |
| **Claude.ai** (deg) | Arkitekt og spesifikasjonsforfatter. Drafter spesifikasjoner, avslutter ALLTID med NÅ/NESTE-blokk (oppgave — hvem — full filsti) |
| **ChatGPT** | Spesifikasjonsport. Obligatorisk review FØR implementering for alt som rører `platform/`, `policies/`, `deploy/` |
| **Claude Code** | Eneste kanal til shipped kode. Implementerer, tester lokalt+staging, åpner PR |
| **Codex** | Kodereview og merge |

**Kø:** Claude.ai → ChatGPT → Claude Code → Codex.
Eier relayer mellom leddene. Claude.ai leverer spesifikasjoner, ikke
kode-zip (zip har reversert Claude Codes fikser 3/3 ganger).

Repo: `github.com/moka1980/disponit` · Rutiner: `docs/RUTINER.md`

---

## 2. Hva Disponit er

Bedriftsuavhengig AI-operasjonsplattform: moduler utfører rutineprosesser
automatisk innenfor kundens forhåndsgodkjente policy, med sporbarhet,
sikker rollback og policybasert unntakshåndtering.
**Motoren er kode (lik for alle), policyen er data (unik per kunde).**
Kunde null: wcagvakt.no.

---

## 3. Status: hva som KJØRER

**M-1 er live på disponit.com** — Eier logger inn via Google (OIDC) og ser
fire flater: Oversikt, Policy, Beslutninger, Unntak.

**Ferdig og merget:**
- **M-1 policymotor** (`aktiv`, 6/6 sjekkliste, p95 82 ms)
- **M-2 revisjonslogg** (append-only håndhevet av DB-trigger)
- **M-37 unntaksmotor** m/ R1 tofase-reparasjon, outbox, null egne fullmakter
- **Tilstandslag**: PostgreSQL, RLS+FORCE, envelope-kryptering, crypto-shredding
- **Lese-API** (PR-008): syv leseendepunkter
- **Drift** (PR-009/009b): systemd-units, nginx, TLS, Unix-socket-tillitsgrense
- **OIDC-sesjon** (PR-010)
- **M-1 UI** (PR-011) + hardening (PR-011b)
- **Unntaksbehandling** (PR-012): mennesket kan godkjenne/avvise/eskalere
- **Policyadministrasjon** (PR-013): utkast → validering → diff → fire øyne → aktivering

**Den store oppdagelsen:** hele kontrollplanet er ferdig, men det finnes
**ingen handlingsmoduler**. Agenten kan si hva den har lov til, men kan
ikke gjøre forretningsarbeid ennå.

---

## 4. Pågående arbeid

**PR-014-kjeden** — første handlingsmodul, delt i tre:

| PR | Innhold | Status |
|---|---|---|
| **014a** | Modulregister, kontraktversjoner, aktiveringsport | **Merget** — migrasjon 014 + 015 |
| **014b** | Domeneverifikasjon, controller/browser-separasjon, egress-proxy, artefaktprotokoll | **Merget** — migrasjon 016 + 017, oppfølging i 018 (kanonisk hostname) |
| **014c** | Automatisk WCAG-kontroll (selve modulen) | Ikke startet — forutsetter egress-kjøretiden, se §7 |

**PR-015 (operativt lag) er MERGET** (PR #34, 2026-08-15): migrasjon
**019** (domeneobservasjonsrunder, flerpartsoppgjør, sonegjerde mot
wildcard-overlapp, fencing ved reclaim, batchgrense i ryddefunksjonen),
revalideringsjobben i `platform/drift/` og testene ligger i koden.
Klarsignalet `docs/pr/PR-015-IMPLEMENTERINGSKLARSIGNAL.md` leses derfor nå
som **beskrivelse av eksisterende skjema**, ikke som bestilling — bygg den
ikke om igjen. Migrasjonshistorikken er checksum-låst: 014–019 er ferdige
filer som ikke skal skrives på nytt; en retting går i en **ny** migrasjon.

**Neste faktiske oppgave er egress-kjøretiden (014b §5–6), så PR-014c** —
se §7.

**Åpen forutsetning — IKKE lukket:** `m37_unntak` modulaksept
(rollback-m37-driver + staging-måling). Manifestet er autoritativt, og det
står fortsatt `status: under_utvikling`, `driftstilstand: ikke_i_drift` og
`rollback_testet.status: nei` (`platform/modules/m37_unntak/manifest.yaml`
linje 6–9 og 104–117). Manifestet sier også hvorfor, og det er en reell
mangel: `rollback-m01-v1` deaktiverte **beslutningsmodulen**, ikke
M-37-arbeideren, og arbeiderens egen unit
(`deploy/staging/disponit-m37.service`) er ikke engang installert på
staging (RUTINER.md linje 33 sier det samme). Å låne den målingen hit ville
vært å låne konklusjonen fra et annet spørsmål.

**Hva den åpne porten faktisk blokkerer:** modulaksept og driftssetting av
M-37 — ikke plattformarbeidet. Handoffen 2026-08-11 §7.4 legger lukkingen
«parallelt/løpende» ved siden av PR-014-kjeden, så plattformarbeidet
(PR-015, og nå egress-kjøretiden) kan bygges videre. Men
ingen spesifikasjon skal skrives som om porten er passert, og ingen modul
som avhenger av M-37 i drift kan regnes som ferdig før rollbacken er kjørt
og målt.

**Registrerte arbeidselementer (ikke startet):**
- Gate 14b: oppløsning av levende oppdrag ved menneskelig avvis (M-37-domenet)
- Policyadmin v2: simulering (rådgivende, aldri aktiveringskrav)
- M-16 KPI-dashboards
- Editor-hull i policyadmin: `menneskelig_overstyring`, vilkår-redigering,
  rullbakk-knapp, versjonshistorikk-visning

---

## 5. Ufravikelige invarianter (brytes aldri)

1. **Deny-by-default, fail-closed overalt.**
2. **M-37 har null egne forretningsfullmakter** — reparasjon går gjennom
   API + policy + outbox.
3. **Mennesket avgir attestasjon, motoren beslutter.** Ingen knapp
   omgår policymotoren.
4. **Beslutning ≠ utførelse.** TILLAT betyr tillatt, ikke utført. Kun
   eiermodul-kvittering gir `løst`.
5. **`api/` importerer aldri `m37/`** (statisk AST-test).
6. **Én skrivevei til revisjonsloggen.** Append-only, DB-håndhevet.
7. **`sett_kontekst` først på alle veier inn** (tenant, aktør, request_id).
8. **Kjøreren eier migrasjonstransaksjonen**; migrasjonshistorikk er
   checksum-låst og immutable.
9. **Terminale tilstander endres aldri** (`løst`, `avvist`). `manuell` er
   terminal for **automatisk** M-37-behandling, ikke absolutt: PR-012 åpnet
   den ene whitelistede, auditerte veien ut (`manuell → venter_godkjenning`,
   som krever at en `apen` godkjenningsrunde allerede finnes —
   `011_unntaksbehandling.sql` linje 169 + 185). Uten den ville ingen
   menneskelig godkjenning, avvisning eller eskalering vært mulig.
10. **Systemet påstår aldri noe databasen ikke kan bevise.**

---

## 6. Lærdommer som former hver spesifikasjon

**De fire portspørsmålene** — still dem FØR draften sendes, per kontroll:
1. Gjelder den alle veier inn, eller bare hovedveien?
2. Gjelder den under samtidighet, eller bare sekvensielt?
3. Validerer den at verdien er *velformet*, eller at den er *riktig*?
4. Er formatet lukket, så en ny nøkkel blir en feil og ikke stillhet?

**Positiv tillatelsesliste, ikke denylist.** Krav skal være positivt bevis
(«kun globalt routbare adresser», «kun disse oppdragstilstandene tillater
avvis») — da overlever garantien nye verdier ingen tenkte på.

**Ikke straff det riktige.** Fire ganger i PR-014a bygde jeg inn en
binding som gjorde sikkerhetspatching dyr eller umulig. Hver gang du
strammer en binding: sjekk om den gjør en legitim operasjon (patch,
migrering, rullbakk) umulig.

**Tester må konstruere egen tilstand.** Tre ganger har en test råtnet
fordi den antok et utgangspunkt som senere endret seg.

**Ærlig navngivning.** «Automatisk WCAG-kontroll», ikke «WCAG-revisjon».
«Deploymentevidens», ikke «bevis på at ingen annen kode kjørte». Lov
nøyaktig det evidensen bærer.

**Konsolider datamodellen.** Deltaformen sprer DDL over mange dokumenter
— ved GO skal alt samles i ett klarsignal med full DDL.

---

## 7. Neste steg i den nye chatten

**PR-014b-s databaselag er levert og merget** (migrasjon 016 + 017,
oppfølging 018): domeneverifikasjon med DNS-TXT-challenge,
autorisasjonsvisningen `v_domeneautorisasjon`, rollen `disponit_egress`
og artefaktprotokollens funksjoner står i koden, med tester.
Klarsignalet ligger i `docs/pr/PR-014b-IMPLEMENTERINGSKLARSIGNAL.md`; §1–4
er rettet mot det migrasjonene faktisk gjør og leses som beskrivelse av
eksisterende skjema.

**Kjøretiden er IKKE levert, og skal ikke antas levert.** Det finnes
ingen `platform/egress/`, ingen `platform/browser/`, ingen
proxy-tjeneste og ingen forbruker av per-oppdrag proxy-token — kun
databaseobjektene over. §5 (egress-proxy og crawlgrenser) og §6
(controller/browser-separasjon) i 014b-klarsignalet er derfor fortsatt
**bestilling**, ikke beskrivelse. Det betyr noe konkret: den eneste
grensen som skal håndheve domeneautorisasjon og SSRF-restriksjoner på
hver eneste browserforespørsel, finnes ikke ennå. PR-014c (som *bruker*
den grensen) kan ikke regnes som ferdig før den er bygget.

**PR-015 (operativt lag) er levert og merget** (PR #34): migrasjon **019**
— domeneobservasjonsrunder (arbeideren er scheduler, observatørene skriver
i eget navn), flerpartsoppgjør i `avgjor_domeneovertakelse()`, fencing mot
reclaim på artefaktkapabiliteten, batchgrense i `rydd_staged_artefakter()`,
og rollen som faktisk kan bære `domains:adjudicate`. Revalideringsjobben
(`platform/drift/domenerevalidering.py`, `kjor_revalidering.py`) og
019-testene ligger i koden. Ikke implementer den på nytt, og ikke skriv om
`019_overtakelse_attestasjon.sql` — den er checksum-låst (invariant 8);
enhver retting går i migrasjon 020 eller senere.

Det som **gjenstår av 015** er drift, ikke skjema: unitene, de fire
systembrukerne, den lukkede resolveroperatørlista og deploy-portene 28/28b–
28g er beskrevet i klarsignalets §6b–§6d og er ikke målt på staging ennå.

**Neste oppgave er egress-kjøretiden (014b §5–6)** — `platform/egress/`,
`platform/browser/`, proxy-tjenesten og forbrukeren av per-oppdrag
proxy-token. Uten den finnes ikke grensen som håndhever domeneautorisasjon
og SSRF-restriksjoner.

Deretter **PR-014c**: automatisk WCAG-kontroll — den første eiermodulen
som bruker plattformen 014a/014b/015 bygde. Den kan ikke crawle noe som
helst før egress-kjøretiden finnes: enten bygges den som en del av 014c,
eller så planlegges den som eget arbeid først.
