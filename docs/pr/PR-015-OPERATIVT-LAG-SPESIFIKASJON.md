# PR-015 SPESIFIKASJON — Operativt lag: resolverarbeider, M-37-kobling, kapabilitetsutstedelse og ryddetimere

**Draft: Claude.ai · Full sti:
`docs/PR-015-OPERATIVT-LAG-SPESIFIKASJON.md`.
Forutsetning: PR-014b merget (migrasjon 016 + 017 på main).**

**Hva dette er:** 014b definerte funksjonene. Ingen kaller dem ennå.
PR-015 er kallerne — og ingenting annet. Ingen ny DDL på 016/017-tabellene,
ingen nye kontrakter, ingen WCAG-logikk.

---

## 0. To ting som må avklares før draften leses videre

**Migrasjonsnummereringen har flyttet seg.** Spesifikasjonen og
klarsignalet sa «migrasjon 014»; det landet som **016 + 017**. Alle
dokumenter som refererer «migrasjon 014» er nå feil, og en fremtidig leser
vil lete etter en fil som ikke finnes. Rettes som ren docs-endring (RUTINER
§2 tillater at den hopper over porten, med begrunnelse i PR-beskrivelsen):
`docs/PR-014b-IMPLEMENTERINGSKLARSIGNAL.md`,
`docs/PR-014b-DOMENE-EGRESS-ARTEFAKT-SPESIFIKASJON.md` og v2-deltaet.

**To linjer i overleveringen er avkortet** — lærdom 1 slutter på «The rule
now: t parallel», og lærdom 2 på «Codex's residual the operational arc —
not d». Jeg har lest lærdom 1 som *ikke intervener parallelt med
skyløkka*, og jeg har **ikke** gjettet på lærdom 2. Den handler etter alt å
dømme om hvor Codex' gjenstående funn hører hjemme, og det er nettopp
avgrensningen §1 gjør — så send de to linjene fullstendig, så jeg kan
sjekke §1 mot dem framfor mot min egen rekonstruksjon.

## 1. Hvorfor dette, og ikke 014c

014c (automatisk WCAG-kontroll) er en eiermodul som *bruker*
domeneattestasjon, opplastingskapabilitet og artefaktpromotering. Alle tre
er i dag funksjoner uten kaller:

| 014b leverte | Hvem kaller den i dag | 014c trenger at |
|---|---|---|
| `revalider_domenekontroll()` (016:394, 016:569) | Ingen | ferskheten faktisk holdes < 72 t |
| `opprett_overtakelsessak` / `avgjor_domeneovertakelse` (domeneovertakelse.py:76) | Ingen | en konflikt kan avgjøres, ikke bare oppstå |
| Opplastingskapabilitet (017:102) | Ingen | modulen får den ved claim |
| `rydd_staged_artefakter()` (016:812) | Ingen | `staged` ikke vokser uendelig |

Bygges 014c først, blir hver av disse en midlertidig omvei i modulkoden —
og omveien blir permanent. Verre: **uten resolverarbeideren utløper all
ferskhet etter 72 timer**, så 014c ville feilet i staging på dag fire av
grunner som ikke er 014cs.

Ingenting her er nytt design. PR-015 er den delen av 014b som ikke ble
implementert fordi 014b stoppet ved kontraktene.

## 2. Resolverarbeider — den kaller `revalider_domenekontroll()`

**Systemd-timer** `disponit-domenerevalidering.timer`, egen Unix-bruker,
`disponit_domains_admin`-rolle. Arbeideren har **ingen egen autoritet**:
den slår opp DNS og kaller funksjonen. Statusbeslutningen ligger i
databasen, som i dag.

- **Utvalg:** rader med `status IN ('verifisert','avklaring_kreves')` og
  `siste_vellykkede_revalidering < now() - 24 t`, eldste først.
- **Spredning:** kjøres hver time med et **jevnt fordelt utvalg**, ikke én
  daglig storkjøring. Ellers utløper alle kundens domener i samme sekund
  ved en enkelt hendelse, og gjenopprettingen blir like samlet.
- **Samme kontrakt som verifisering:** ≥2 uavhengige resolvere,
  **uenighet → ikke vellykket** revalidering.
- **Én kjører om gangen:** `pg_advisory_lock` på arbeidernøkkel. To timere
  (eller en manuell kjøring under en pågående) må aldri overlappe.
- **Per rad: maks tre forsøk med backoff + jitter innen døgnet.**
  Arbeideren **endrer aldri status** — 72-timersvinduet i visningen gjør
  jobben (014b §4). Det er tre døgn med tre forsøk hvert døgn.

**Korrelert resolverfeil er den reelle faren.** Faller vår egen
DNS-infrastruktur, mister alle tenants ferskhet samtidig og alt stopper.
Vi svekker ikke fail-closed for å unngå det — vi gjør det synlig og
usannsynlig:
- **Resolverdiversitet er et krav, ikke en konfigurasjonsdetalj:** minst to
  resolvere hos **ulike operatører og ulike nett**. Konfigurasjon som
  bryter dette → oppstart nektes (deploy-port).
- **Driftsalarm ved samtidighet:** faller > 20 % av revalideringene innen
  én time, er det en driftshendelse — **én alarm**, ikke én M-37-sak per
  tenant. Å drukne unntakskøen i støy er sin egen feilmodus.
- Alarmen påstår ingenting den ikke vet: den sier «vi fikk ikke svar»,
  aldri «domenene er tapt».

## 3. M-37-kobling — konflikten kan avgjøres

`opprett_overtakelsessak` og `avgjor_domeneovertakelse` finnes
(domeneovertakelse.py:76); appen kaller ingen av dem.

- **Inn:** `verifiser_domenekontroll()` oppretter saken i sin egen
  transaksjon (014b B4). PR-015 legger til at saken **blir synlig** i
  unntaksflaten fra PR-012, med familie `domeneovertakelse`, lineage til
  begge rader, og begge hostnames i saksvisningen.
- **Ut:** attestasjonen fra PR-012 (godkjenn/avvis) kaller
  `avgjor_domeneovertakelse()`. **Ingen knapp skriver status.**
  Mennesket attesterer, funksjonen beslutter — invariant 3.
- **Scope:** avgjørelsen krever eget scope `domains:adjudicate`.
  `exceptions:handle` alene er ikke nok: dette flytter en autorisasjon
  mellom kunder.
- **Fire øyne?** Nei i v1 — men se spørsmål 2.
- **Saksvisningen viser det databasen kan bevise:** hvem som besto
  challenge når, hvem som mistet autorisasjonen, og at A allerede er
  stoppet uansett utfall. Den anslår ikke hvem som «egentlig» eier
  domenet.
- **Terminal sak gjenbrukes aldri.** Ny konflikt på samme hostname mens en
  sak står åpen → **samme sak**, med ny hendelse; ikke en andre sak.

## 4. Flerpartsovertakelse (016:687)

Codex' gjenstående kant: tre eller flere tenants i kjede. A `verifisert`,
B tar over → A `tilbakekalt`, B `avklaring_kreves`. Så består C challenge.

**Regelen holdes enkel, fordi kompleksitet her er et sikkerhetshull:**
- Hostname-låsen (014b B2) serialiserer uansett antall parter.
- **C overtar B-s plass i den ÅPNE saken** — B → `tilbakekalt`
  (`grunn: overtatt_dns_kontroll`), C → `avklaring_kreves`, ny hendelse på
  samme sak. **Aldri en andre åpen sak per hostname** (UNIQUE på
  `(hostname)` for ikke-terminal `domeneovertakelse`-sak).
- **Ingen tenant blir `verifisert` av at en annen taper.** A gjenoppstår
  ikke fordi B ble tilbakekalt. Å gjenvinne autorisasjon krever ny
  challenge — positiv tillatelsesliste.
- **Rask veksling er et signal, ikke støy:** ≥3 parter på samme hostname
  innen 24 t merkes `hoy_konfliktrate` på saken. Det stopper ingenting
  automatisk; det gir mennesket noe databasen faktisk vet.

## 5. Opplastingskapabilitet utstedes ved claim (017:102)

`artifact_upload_capability` er definert, men ingen utsteder den.

- **Utstedes av `POST /v1/oppdrag/claim`**, sammen med
  kvitteringskapabiliteten og **som separat token** — aldri utledet av
  den, aldri samme audience.
- **Bindingen er serverkontekstens, ikke modulens** (014a §5-disiplinen):
  `tenant · oppdrag_id · modul_id · release_id · kontraktversjon ·
  kontrakt_hash · module_epoch · artefakttype`. Modulen ber ikke om felt;
  den mottar et token.
- **`artefakttype` hentes fra `artefakttype_register`** for oppdragets
  kontrakt. Finnes ingen registrert artefakttype → **ingen
  opplastingskapabilitet utstedes**, og claim lykkes fortsatt. En modul
  som ikke skal laste opp, skal ikke få lov.
- **Levetid = evidensfristen for oppdraget**, aldri lengre. Kortere enn
  kvitteringskapabiliteten er greit; lengre er en feil.
- **Epoch kontrolleres under oppdragslåsen** ved utstedelse, som resten av
  kjeden.

## 6. Ryddetimere (016:812)

`disponit-artefaktrydding.timer`, hvert 15. minutt, kaller
`rydd_staged_artefakter()`.
- Funksjonen er allerede idempotent og positiv («`staged` > 24 t **og**
  uten refererende kvittering, inkludert karantenesatt»). Timeren legger
  ingen logikk oppå — den kaller.
- **Batchgrense per kjøring** (f.eks. 500), så en opphopning ikke låser
  tabellen i én transaksjon.
- **Karantenesatt evidens telles og rapporteres, aldri ryddes.** Måltallet
  `opprydding.karantene_bevart` fra 014b §10 blir nå faktisk produsert.
- Timeren feiler **synlig**: to sammenhengende feilede kjøringer → alarm.
  En stille ryddejobb er en voksende disk.

## 7. De fire portspørsmålene

| Kontroll | Alle veier inn? | Samtidighet? | Riktig vs. velformet? | Lukket format? |
|---|---|---|---|---|
| Revalidering | Én timer, én arbeidernøkkel; manuell kjøring tar samme lås | Advisory-lås hindrer overlapp; spredt utvalg hindrer samlet utløp | ≥2 uenige resolvere → ikke vellykket; arbeideren setter aldri status | Kaller kun `revalider_domenekontroll()` |
| Overtakelsesavgjørelse | Kun via PR-012-attestasjon → funksjonen | Hostname-lås; én åpen sak per hostname (UNIQUE) | Krever `domains:adjudicate`, ikke bare `exceptions:handle` | Funksjonens enum, ingen direkte statusskriving |
| Opplastingskapabilitet | Kun `POST /v1/oppdrag/claim` | Epoch under oppdragslåsen | Bundet til serverkontekst, ikke modulens ønske | Ingen registrert artefakttype → ingen kapabilitet |
| Rydding | Én timer, funksjonens positive regel | Batch + idempotens | Karantene bevares på egenskap, ikke på alder | Kaller kun `rydd_staged_artefakter()` |

## 8. Codex-porter

1. To samtidige revalideringskjøringer → én kjører, én venter, ingen dobbelt
2. Uenige resolvere ved revalidering → ikke vellykket; `siste_vellykkede_revalidering` urørt
3. Tre døgn uten svar → attestasjon nektes; raden **ikke** slettet eller `utlopt`-satt av arbeideren
4. Resolverkonfigurasjon uten diversitet → oppstart nektes (deploy-port)
5. > 20 % feil på én time → én driftsalarm, null M-37-saker opprettet
6. 500 domener spredt over døgnet → ingen time med > 10 % av volumet
7. Overtakelsessak synlig i PR-012-flaten med begge hostnames og lineage
8. Avgjørelse uten `domains:adjudicate` → nektet, selv med `exceptions:handle`
9. Godkjent → B `verifisert` m/ nytt vindu; avvist → `tilbakekalt`; begge kun via funksjonen
10. Ny konflikt på hostname med åpen sak → samme sak, ny hendelse; UNIQUE hindrer sak nr. 2
11. Terminal sak + ny konflikt → ny sak; terminal urørt
12. A→B→C-kjede: hver overtakelse tilbakekaller forrige, kun C `avklaring_kreves`, A gjenoppstår ikke
13. ≥3 parter innen 24 t → `hoy_konfliktrate` satt, ingenting stoppet automatisk
14. Claim returnerer to distinkte tokens; opplastingstokenet virker ikke som kvittering og motsatt
15. Oppdrag uten registrert artefakttype → claim OK, ingen opplastingskapabilitet
16. Opplastingskapabilitet med levetid > evidensfrist → utstedelse avvist
17. Epoch endret mellom claim og utstedelse → ingen kapabilitet
18. Rydding: 600 kandidater → to batcher, idempotent, ingen låsing > grense
19. Karantenesatt artefakt eldre enn 24 t → bevart, telt i `karantene_bevart`
20. To feilede ryddekjøringer → alarm

**Alle tester konstruerer egen tilstand.** Ingen delt fixture.

## 9. Evidensgrense `operativt-lag-v1` (defineres FØR arbeidet)

`revalidering-015-v1`: `dobbeltkjoring = 0` ·
`utvalg.maks_andel_per_time ≤ 0.10` · `uenige_resolvere_avvist = alle` ·
`status_satt_av_arbeider = 0`.
`overtakelse-015-v1`: `apne_saker_per_hostname ≤ 1` ·
`kjede_abc.a_gjenoppstatt = 0` · `avgjorelse_uten_scope_nektet = alle`.
`kapabilitet-015-v1`: `tokens_distinkte = ja` ·
`uten_artefakttype_utstedt = 0` · `levetid_over_frist_avvist = alle`.
`rydding-015-v1`: `karantene_bevart = alle` · `idempotens_kjoring2_slettet = 0`.
Et punkt uten målbar grense regnes som `nei`.

---

## Spørsmål til ChatGPT

1. **Alarm i stedet for saker ved korrelert resolverfeil.** Jeg lar > 20 %
   feilrate gi én driftsalarm framfor én M-37-sak per tenant, fordi en
   flommet unntakskø er sin egen feilmodus. Men det betyr at en enkelt
   kunde som mister DNS *under* en bredere hendelse blir usynlig i
   unntaksflaten. Er terskelen riktig sted å skille drift fra sak, eller
   bør hver tenant få sak uansett og støyen løses i visningen?
2. **Fire øyne på overtakelsesavgjørelse.** Jeg krever ett menneske med
   `domains:adjudicate`. Avgjørelsen flytter en autorisasjon mellom
   kunder — det er nærmere policyaktivering (fire øyne, PR-013) enn
   ordinær unntaksbehandling. Bør v1 kreve to attestasjoner, med den
   kostnaden det har for responstid?
3. **Utstedelse ved claim vs. ved behov.** Opplastingskapabiliteten
   utstedes ved claim og lever ut evidensfristen. Alternativet er et eget
   endepunkt som utsteder den når modulen faktisk har en rapport —
   kortere levetid, men en ekstra autentisert vei inn. Er den kortere
   levetiden verdt den ekstra flaten i v1?

---

```
NÅ:    PR-015-spesifikasjonen gjennom spesifikasjonsporten (de tre faste
       spørsmålene + de tre over); svaret limes inn i PR-beskrivelsen
       — ChatGPT (Eier relayer) — docs/PR-015-OPERATIVT-LAG-SPESIFIKASJON.md
NESTE: Ren docs-rettelse av migrasjonsnummer (014 → 016/017) i de tre
       014b-dokumentene; egen PR, porten hoppes over med begrunnelse
       — Claude Code — docs/PR-014b-IMPLEMENTERINGSKLARSIGNAL.md,
         docs/PR-014b-DOMENE-EGRESS-ARTEFAKT-SPESIFIKASJON.md,
         docs/PR-014b-DOMENE-EGRESS-ARTEFAKT-v2-DELTA.md
```
