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
| **014c** | Automatisk WCAG-kontroll (selve modulen) | Etter PR-015 |

**Neste faktiske oppgave er PR-015 (operativt lag)**, ikke 014a/014b:
klarsignalet ligger i `docs/pr/PR-015-IMPLEMENTERINGSKLARSIGNAL.md` og
beskriver migrasjon **019** (domeneobservasjonsrunder, flerpartsoppgjør,
fencing ved reclaim, batchgrense i ryddefunksjonen). Migrasjonshistorikken
er checksum-låst: 014–018 er ferdige filer som ikke skal skrives på nytt.

**Blokkerende forutsetning (lukket):** `m37_unntak` modulaksept
(rollback-m37-driver + staging-måling).

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

Neste oppgave er **PR-015 (operativt lag)**, klarsignal levert:
`docs/pr/PR-015-IMPLEMENTERINGSKLARSIGNAL.md`. Migrasjon **019** —
domeneobservasjonsrunder (arbeideren er scheduler, observatørene skriver
i eget navn), flerpartsoppgjør i `avgjor_domeneovertakelse()`, fencing mot
reclaim på artefaktkapabiliteten, batchgrense i `rydd_staged_artefakter()`,
og rollen som faktisk kan bære `domains:adjudicate`.

Deretter **PR-014c**: automatisk WCAG-kontroll — den første eiermodulen
som bruker plattformen 014a/014b/015 bygde. Den forutsetter
egress-kjøretiden fra 014b §5–6, som ennå ikke finnes: enten bygges den
som en del av 014c, eller så må den planlegges som eget arbeid før
014c kan crawle noe som helst.
