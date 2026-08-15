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
| **014a** | Modulregister, kontraktversjoner, aktiveringsport | **GO gitt, klarsignal levert** → Claude Code bygger |
| **014b** | Domeneverifikasjon, controller/browser-separasjon, egress-proxy, artefaktprotokoll | **Claude.ai skal drafte NÅ** |
| **014c** | Automatisk WCAG-kontroll (selve modulen) | Etter 014b |

**Blokkerende forutsetning:** `m37_unntak` modulaksept
(rollback-m37-driver + staging-måling) — Claude Code lukker denne først.

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
9. **Terminale tilstander endres aldri** (`løst`, `avvist`, `manuell`).
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

Claude.ai drafter **PR-014b**: domeneverifikasjon (`v_domene` med
DNS-TXT-challenge, livsløp, tilbakekalling), controller/browser-separasjon
(browseren har ingen credentials og kun egress-proxy som nettverksvei),
egress-proxy (kun globalt routbare adresser, IP-pinning, revalidering ved
hvert redirect), og artefaktprotokollen (modulen laster opp lukket
rapport, API-et krypterer, kvittering binder hash, atomisk promotering).

Kjente krav fra PR-014-reviewen som må inn: separat
`artifact_upload_capability` (ikke gjenbruk av kvitteringskapabiliteten),
DB-lagring maks 1 MiB i v1, crawlgrenser (samme hostname, GET/HEAD,
HTML-only, ingen query/fragment, eksakte tak), URL med credentials/query
avvises ved input, eierskap kontrolleres før HVER toppnivånavigasjon.
