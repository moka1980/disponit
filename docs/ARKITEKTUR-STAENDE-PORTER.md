# ARKITEKTURNOTAT — ratifisering og stående porter etter 044

**Forfattet av Claude.ai (ratifisering etter 044) · tatt inn av Claude Code 2026-08-19.
Grunnlag: debrief periodisk kontroll 2026-08-19 (PR #105, 5 runder,
32 funn).**

Dette dokumentet har to formål: rette arkitekturmodellen der
implementeringen viste at spesifikasjonen tok feil, og løfte funnklassene
fra 044 til **stående porter** som skal inn i hver senere spesifikasjon —
ikke gjenoppdages hver runde.

---

## 1. Ratifisert avvik: `utfor_bestilling` in-prosess, ikke HTTP-loopback

Klarsignalet sa «kun `/v1/bestilling`». Implementeringen trakk ut
`utfor_bestilling(...)` som både browserendepunktet og materialisereren
kaller, i API-ets eget tillitsnivå.

**Avviket ratifiseres, og spesifikasjonen tok feil — ikke koden.** Jeg
skrev en *transportmekanisme* der jeg mente en *autorisasjonsgrense*.
Poenget var aldri HTTP; det var at planen ikke skal ha en egen vei rundt
policymotoren. En loopback ville dessuten krevd tokendistribusjon oppå
samme kode, fordi `secret_mac` er HMAC med app-pepper og
`api_tokener_ikke_reservert` blokkerer reserverte tenants — altså mer
angrepsflate for å oppnå det samme.

**Formuleringen som gjelder framover:**

> En ny produsent skal gå gjennom **samme beslutningsfunksjon** som den
> menneskelige veien, i samme tillitsnivå. Om det skjer over HTTP eller
> in-prosess er en transportbeslutning som tas ved implementering.
> Porten er ikke «kun endepunktet», men **statisk AST: produsentmodulene
> har ingen INSERT mot `oppdrag`, `frekvens_hendelser`,
> `bestilling_idempotens` eller `revisjonslogg`** — pluss
> kvotedelingstesten.

Dette gjelder også neste scheduler, neste importvei og neste
integrasjon. Ingen skal re-litigere det.

## 2. Åtte stående porter

Skal inn i hver spesifikasjon som rører DB-funksjoner, flate eller
produsenter. Nummereringen er stabil så de kan refereres direkte.

### SP-1 Tenant-binding i hver definer-tilgang
`tenant = p_tenant` i **hvert** oppslag og **hver** UPDATE, og
`krev_tenantkontekst` på hver tenant-skopet SECURITY DEFINER. En definer
som slår opp på `(plan_id, vindu_start)` alene er en kryss-tenant-vei
selv om RLS-policyen «burde» ta det.
*Port:* for hver definer, kall den med gyldig kontekst for én tenant,
mot en rad som tilhører en annen → nektet.
*Hvorfor den er stående:* dette var Codex' største klasse i runde 2–3, og
den er usynlig ved gjennomlesning fordi RLS ser ut til å dekke den.

### SP-2 Replay-nøkkel på enhver opprettende operasjon
Enhver operasjon som **skaper** noe trenger en replay-/operasjonsnøkkel
ved den autoritative opprettelsesgrensen — **uavhengig av om kallet
kommer over HTTP eller in-prosess.** Planopprettelsen manglet den, og
den første formuleringen av denne porten sa «POST» — altså nøyaktig den
transportbindingen §1 ratifiserer bort. En in-prosess produsent kan
skape et objekt uten å være et endepunkt.
*Port:* samme nøkkel + samme intensjon → ett objekt, identisk svar;
samme nøkkel + annen intensjon → konflikt. Testen kjøres mot
opprettelsesfunksjonen, ikke mot endepunktet.

### SP-3 Driftsfeil er ikke verdikter
`db_utilgjengelig`, `logging_feilet` og `intern_feil` skal **verken**
terminalisere eller pause. Claimet frigis og neste kjøring prøver igjen.
**Utfalls-enumet i spesifikasjonen må skille retrybar fra terminal
eksplisitt** — ikke overlate skillet til implementeringen.
*Port:* injiser DB-feil midt i en kjøring → ingen tick, ingen pause,
claim frigitt, neste kjøring lykkes.

### SP-4 Varselveier bak EXCEPTION-svelg
To feil som begge var usynlige fordi svelget skjulte dem:
- `varsel.bruker_id` er FK mot brukeridentitet. **Aktørstrenger
  (`bruker:<bid>`) må løses opp**, ellers FK-brudd som svelges stille.
- `varsel_en_per_hendelse` kolliderer ved gjentak. **Hver forekomst
  trenger sin egen `hendelse`-verdi.**

*Port:* varsel for samme hendelsestype to ganger → to varsler; aktør
oppgitt som streng → oppløst eller eksplisitt feil, aldri svelget.
*Regel som følger:* «X er ikke evidens» er en grunn til å la systemet
fortsette uten X — **aldri** en grunn til å la feilen være usynlig. Svelg
skal telle og logge.

### SP-5 NULL-sikre CHECK-armer
`NULL BETWEEN 1 AND 7` gjør hele OR-en NULL, og en NULL-CHECK **slipper
raden gjennom**. Hver arm skal ha eksplisitt `IS NOT NULL` for feltene
den påstår noe om.
*Port:* for hver flerarmet CHECK, forsøk å skrive raden med NULL i hvert
felt armen nevner → avvist.
*Merk:* dette er fjerde variant av samme feilform i disse rundene —
`COALESCE(..., false)` i speilingsfunksjonen var den samme innsikten i
plpgsql. **NULL passerer som «vet ikke, derfor tillatt».**

### SP-6 Telleverk leses fra gjeldende periode, med riktig tidsanker
En teller som ser data fra før siste gjenopptak gjør gjenopptaket
virkningsløst. Og periodetilhørighet måles på **forfallet**, ikke på
registreringstidspunktet.
*Port:* pause → gjenoppta → tellere nullstilt i praksis, ikke bare i
kolonnen.

### SP-7 «Ingen bordtilgang» gjelder også leserne
Porten om at runtime ikke skriver direkte dekker ikke lesing, og en
klassifiserer som leser tabellen direkte omgår samme grense. **Utvalg
skal gjennom definere.**
*Port:* statisk test at modulen ikke har SELECT mot de aktuelle
tabellene.

### SP-8 plpgsql: `#variable_conflict use_column`
`RETURNS TABLE`-navn kolliderer med `ON CONFLICT`-kolonner. Rent
mekanisk, men det koster en runde hver gang.

## 3. Hva jeg tar med meg som spesifikasjonsforfatter

Fire runder på 044 og åtte på 040 fant samme klasse feil: **en invariant
jeg påsto var håndhevet av lagringen, men som hvilte på at to ting
tilfeldigvis stemte overens.** Vindusraden mot tick-raden, tokenets frist
mot familiens, JSON-kopien mot kolonnen, speilingen mot NULL.

Konkret endring i hvordan jeg skriver: når en setning inneholder
«håndhevet av lagringen», skal den peke på **én navngitt constraint,
trigger eller lås** — ikke på at to strukturer har samme form. Har jeg
ikke et navn å peke på, er påstanden ikke sann ennå.

---

```
NÅ:    Ratifisering og SP-1…SP-8 gjennom porten som arkitekturendring
       — ChatGPT (Eier relayer) — docs/ARKITEKTUR-STAENDE-PORTER.md
NESTE: SP-1…SP-8 refereres fra hver senere spesifikasjon; avviket i §1
       gjør at «kun /v1/bestilling» ikke skrives igjen — Claude.ai
```
