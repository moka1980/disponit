export function harScope(sesjon, scope) {
  return (sesjon.scopes || []).includes(scope);
}

// Å forvalte policy krever skrive- eller aktiveringsscope. `policy:read` er
// IKKE nok: rollene `admin` og `sikkerhet` har bare lesetilgang, og en flate
// med mutasjonsknapper ville bare gitt dem 403 fra API-et.
export function kanForvaltePolicy(sesjon) {
  return harScope(sesjon, "policy:write") || harScope(sesjon, "policy:activate");
}

// Å LESE policy er sitt eget scope, og det er ikke noe alle kunderoller har:
// `godkjenner` og `domeneadjudikator` i `autorisasjon.py` har `decisions:read`
// og unntaksscopene, men ikke `policy:read`. En flate som tilbyr policylesing
// til dem lover noe `GET /v1/policy/aktiv` svarer 403 på.
export function kanLesePolicy(sesjon) {
  return harScope(sesjon, "policy:read");
}

export function kanLeseUnntak(sesjon) {
  return harScope(sesjon, "exceptions:read");
}

// KØEN ER ET VALG, IKKE EN KONSTANT (Codex P2).
//
// `sikkerhet` og `drift` er EGNE køer med eget scope (v3-delta pkt. 5), og
// `/v1/unntak` leser én kø av gangen (`?sakstype=`, `app.py:1306`). Unntaks-
// flaten sendte aldri parameteren og fikk derfor alltid serverens standard
// `normal`. For en økt med `security:read` betydde det at to av tre køer den
// har LOV til å lese ikke fantes noe sted i klienten — og M-16-nøkkeltallene,
// som teller over nøyaktig de sakstypene økten kan se, pekte hit med et løfte
// om «hele listen» flaten ikke kunne holde: et avkuttet utsnitt kunne være
// avkuttet nettopp av rader den eneste nåbare køen ikke inneholder.
//
// Regelen står her, blant de øvrige scope-avledningene, og ikke inne i flaten.
// Det er samme grep som `synlige_sakstyper` i `app.py`: en køregel som bare
// finnes i én leser er en regel den neste leseren ikke vet om, og det var
// nøyaktig den formen serversiden ryddet bort. Verdiene speiler `SAKSTYPER`
// der; endres køene, endres de to stedene sammen, slik `RUTESCOPE` og rutene
// under allerede holdes like.
export function synligeSakstyper(sesjon) {
  return harScope(sesjon, "security:read")
    ? ["normal", "sikkerhet", "drift"]
    : ["normal"];
}

// 044: planen forvaltes av administratoren — opprett/aktiver/gjenoppta.
export function kanForvaltePlan(sesjon) {
  return harScope(sesjon, "plan:opprett") || harScope(sesjon, "plan:aktiver")
    || harScope(sesjon, "plan:gjenoppta");
}

// Varselinnboksen hører MOTTAKEREN til, og hvem som kan bli varslet er ikke
// policyforvaltningen alene lenger (Codex P2): 044 sender pause- og
// bruddvarsler til administratoren som aktiverte planen — en rolle uten
// `policy:write` og `policy:activate`. Uten henne i denne testen fikk skallet
// aldri ruten, pollet aldri `/v1/varsel`, og en administrator som hadde valgt
// `kun_portal` satt igjen med verken e-post eller noe synlig spor av at planen
// var pauset. Varselet ble skrevet, men ingen kunne se det.
//
// Predikatet er «kan bli varslet», ikke «har lesescopet»: hver rolle i
// `ROLLE_TIL_SCOPES` som kan motta et varsel — `policyforvalter` og `admin` —
// bærer `policy:read` uansett, så en ekstra betingelse ville bare vært en
// gjentakelse. Skulle en fremtidig rolle få planscopene UTEN policylesing, er
// det scopet på `GET /v1/varsel` som må endres med den; det er ett sted.
export function kanMottaVarsel(sesjon) {
  return kanForvaltePolicy(sesjon) || kanForvaltePlan(sesjon);
}

// Kontrollplanet på TVERS av tenanter er plattformdriftens, ikke kundens.
// `security:read` er ikke den autoriteten: PR-008 §1 beskriver den som en
// valgfri ops/compliance-scope på en TENANTBUNDET brukersesjon, og rollene
// `admin`/`sikkerhet` i `autorisasjon.py` er kunderoller. Leste admin-flaten
// tenanttabellen ut fra det scopet, så en kundes sikkerhetsansvarlige hver
// eneste andre tenants plan, moduler og neste steg. Ingen kunderolle gir
// `platform:admin` — plattformdrift er en egen autoritet (default-deny).
export function erPlattformdrift(sesjon) {
  return harScope(sesjon, "platform:admin");
}

// Hver rute står her med scopet API-et BAK flaten krever — samme verdi som i
// `RUTESCOPE` i `app.py`. Er de to ikke like, lover menyen (og dyplenken) en
// flate serveren svarer 403 på: `godkjenner` mangler `policy:read`, og
// `policyforvalter` mangler `exceptions:read`. `null` = flaten har ikke noe
// API bak seg og hører derfor alle kundeøkter til.
const BASISRUTER = [
  { nokkel: "oversikt", scope: "decisions:read" },
  // M-16: nøkkeltall regnet fra faktiske beslutninger — ren leseflate
  // over samme scope som oversikten (tallene ER beslutningsdataene).
  //
  // M-16 ble ETTERREGISTRERT som modul 2026-08-31, og EIERVEDTAKET
  // 31/8 («er M-16 på venstre side?») flyttet inngangen dit — samme
  // vedtaksklasse som 24/8-vedtaket for 56/57. Forutsetningen fra
  // registreringen er innfridd i SAMME endring: alle utrullingsradene
  // fikk modul 16, så ingen tenant mister flaten når den forlater
  // toppnavigasjonen.
  { nokkel: "nokkeltall", scope: "decisions:read", modulflate: 16 },
  { nokkel: "policy", scope: "policy:read" },
  { nokkel: "beslutninger", scope: "decisions:read" },
  { nokkel: "unntak", scope: "exceptions:read" },
  // Kundens arbeidsflate er en LESEFLATE over det økten allerede har fått:
  // modulstatus, roller, integrasjoner. Den kaller ikke noe eget endepunkt, og
  // hører derfor til uansett scope. Lå den bak `kanForvaltePolicy`, landet en
  // vanlig `leser` — som kundeinnloggingen sender til `/?visning=kundeadmin` —
  // stille på `oversikt`, og knappen «Åpne kundeflate» åpnet noe annet enn den
  // lovte. Det er bare policyADMINISTRASJONEN som krever forvaltningsscope.
  { nokkel: "kundeadmin", scope: null },
  // 038/039 (eiers UX-krav 18/8): ÉN oppføring — «WCAG kontroll» — med
  // bestilling, rapporter og domeneverifisering som faner.
  //
  // Scopet er flatens SVAKESTE del, ikke den sterkeste (Codex P2). Ruten er
  // en sammenslåing av det som før var `bestilling` (`bestilling:opprett`) og
  // `rapport` (`decisions:read`), og med mutasjonsscopet på hele oppføringen
  // mistet hver eneste ikke-admin kunderolle — `leser`, `sikkerhet`,
  // `godkjenner`, `policyforvalter` — sin ENESTE vei til rapportene, mens
  // `GET /v1/rapport/{id}` fortsatt med vilje krever bare `decisions:read`.
  // En sammenslåing av menyoppføringer skal ikke inndra tilgang.
  //
  // Regelen for ruten er derfor den vanlige: scopet API-et bak den minst
  // krevende delen krever. At bestillings- og domenefanene MUTERER er en
  // sak for fanene, og den avgjøres inne på flaten (`visWcagKontroll`), der
  // det finnes noe å skjule — en rute kan bare være der eller ikke.
  // MODULFLATENE bor i VENSTREMENYEN (eiers arkitekturvedtak 24/8:
  // venstre = modulnavigasjonen, topp = plattformflatene). Ruten består
  // — adresser som virket skal fortsette å virke — men `modulflate`
  // holder den ute av toppnavigasjonen; inngangen er modulkortet.
  { nokkel: "wcagkontroll", scope: "decisions:read", modulflate: 56 },
  // M-57 (§8): ruten sto med VILJE ute mens flaten leste endepunkter som
  // ikke fantes (Codex P1 / Cursor P1) — en menyoppføring hadde da sendt
  // hver økt med `decisions:read` rett i feilflaten, og «Signer» ville
  // vært teater på en irreversibel handling. Betingelsen den ventet på er
  // innfridd i SAMME deployerbare endring som denne linjen: `api/app.py`
  // registrerer nå `GET /v1/rekruttering/prosesser`,
  // `POST …/prosesser/{id}/blinding` og `POST …/lister/{id}/signer`.
  //
  // Scopet er flatens SVAKESTE ledd, samme regel som wcagkontroll over:
  // lesingen krever `decisions:read`, og mutasjonene er alt gatet både
  // inne i flaten og i `RUTESCOPE` på `bestilling:opprett`. Uten
  // oppføring her holder `tillatteFlater` også en håndskrevet
  // `#/rekruttering` ute — og demo-stien (`seed-rekruttering-demo.py`)
  // ber eier åpne nettopp den adressen.
  { nokkel: "rekruttering", scope: "decisions:read", modulflate: 57 },
  // M-5 (094): malregisteret. MODULFLATE, ikke basisrute i toppnav —
  // M-5 er en modul kunden kjøper (`MODULSTATUS[5]`), ikke plattformens
  // eget innsyn i seg selv, og eiervedtaket 24/8 er at venstremenyen er
  // modulnavigasjonen. Samme plass som wcagkontroll og rekruttering.
  //
  // Scopet er flatens SVAKESTE ledd (wcagkontroll-regelen): lesingen bak
  // ruten krever `decisions:read` (`GET /v1/dokumentmal` i RUTESCOPE);
  // opprettelse, publisering og tilbaketrekking muterer og gates INNE på
  // flaten — og av serveren — med `bestilling:opprett`. UTFYLLINGEN er
  // bevisst ikke gatet på mutasjonsscopet: den returnerer, og bærer
  // lesescopet hele veien ned til at `m5_fyll_mal` er STABLE i basen.
  { nokkel: "dokumentmal", scope: "decisions:read", modulflate: 5 },
  // 041: adjudikatorkøen viser sakenes PARTER på tvers av tenanter — den
  // finnes derfor KUN for adjudikasjonsscopet, aldri for en leserolle.
  { nokkel: "adjudikator", scope: "domains:adjudicate" },
  // M-31 (086): modellstyring er en BASISRUTE bak admin-lesescopet —
  // (Flyttet til venstremenyen 1/9 — se vedtaket over.)
  // bor i toppnavigasjonen, ikke bak et modulkort. Scopet er API-ets
  // (`GET /v1/modellstyring` krever `security:read` i RUTESCOPE) —
  // samme regel som resten av tabellen: menyen lover aldri en flate
  // serveren svarer 403 på. En ren `platform:admin`-økt har ikke
  // lesescopet og får derfor heller ikke ruten.
  // EIERVEDTAK 1/9 — TOPPNAVIGASJONEN VAR OVERFYLT. Ruten lå i toppen
  // etter #315-presedensen («ingen modulflate-flipp for en
  // plattforminternflate»), og den presedensen var riktig én modul om
  // gangen. Ved sytten knapper i toppen og fire i venstremenyen bar den
  // ikke lenger: eier så det som «mange knapper på toppen», og de to
  // sonene hadde sluttet å bety noe hver for seg. Nå gjør de det igjen —
  // toppen er PLATTFORMFLATENE, venstremenyen er MODULENE, og
  // venstremenyen har alt områdeoverskriftene som gjør sytten oppføringer
  // lesbare.
  //
  // Rekkevidden er uendret: `erSynlig` i AppShell er
  // `MODULFLATE.has(n) || erTildelt(n)`, så en flate økten har rute til
  // står i menyen uansett katalogtildeling (Cursor P1, 24/8). Dyplenker
  // og bokmerker virker som før — ruten er den samme.
  { nokkel: "modellstyring", scope: "security:read", modulflate: 31 },
  // M-6 PR-B: e-postagentens kildeflate. Scopet er flatens SVAKESTE
  // ledd (wcagkontroll-regelen): lista bak ruten krever `epost:read`;
  // koble-til/deaktiver muterer og avgjøres INNE på flaten (og av
  // serveren) med `epost:kilde:administrer`. (Flyttet 1/9.)
  // i PR-B — klassifiserings-/utkastsflaten og en eventuell
  // modulkort-inngang er PR-D.
  { nokkel: "epost", scope: "epost:read", modulflate: 6 },
  // M-35 (089): kontinuitet er en BASISRUTE bak `kontinuitet:read` —
  // (Flyttet til venstremenyen 1/9 — se vedtaket over.)
  // i toppnavigasjonen, ikke bak et modulkort, fordi beredskapen er
  // plattformens tilstand og ikke en modul kunden har kjøpt. Scopet er
  // API-ets (`GET /v1/kontinuitet` i RUTESCOPE), så menyen lover aldri
  // en flate serveren svarer 403 på.
  { nokkel: "kontinuitet", scope: "kontinuitet:read", modulflate: 35 },
  // 044-planflaten har INGEN egen rute lenger: periodisk kontroll er en
  // fane under wcagkontroll (eier 19/8 — samme arbeidsflyt, én
  // menyoppføring), og wcagkontroll-ruten bærer alt planfanen trenger
  // (decisions:read). Den gamle adressen lever videre som alias under.
  //
  // M-10 + M-11 (090/091): driftstatus er en BASISRUTE bak admin-
  // lesescopet — samme presedens som `modellstyring` over, og samme
  // (Flyttet til venstremenyen 1/9 — se vedtaket over.)
  // og ikke bak et modulkort. De to er heller ikke moduler; de er
  // plattformens eget innsyn i seg selv.
  //
  // Scopet er API-ets: BÅDE `GET /v1/drift/backup` og
  // `GET /v1/drift/selvtest` krever `security:read` i RUTESCOPE, og
  // flaten henter begge i ett kall-par. En rute som lovet mer enn det
  // svakeste av de to endepunktene ville vært nøyaktig løftet denne
  // tabellen finnes for å ikke gi — men her er de like, så flaten er
  // enten hel eller ikke synlig.
  { nokkel: "driftstatus", scope: "security:read", modulflate: 10 },
  // M-3 (092): datakvalitetsflaten. BASISRUTE bak admin-lesescopet —
  // samme presedens som `modellstyring` og `driftstatus` over, og samme
  // (Flyttet til venstremenyen 1/9 — se vedtaket over.)
  // og ikke bak et modulkort. Grunnen er den samme som for driftstatus:
  // profilen måler PLATTFORMENS egne tabeller, ikke en modul kunden har
  // kjøpt — og modulen står `ikke_i_drift` i manifestet, så et modulkort
  // ville lovet en drift som ikke finnes.
  //
  // Scopet er API-ets: `GET /v1/datakvalitet` krever `security:read` i
  // RUTESCOPE, og flaten henter nøyaktig det ene endepunktet. En ren
  // `platform:admin`-økt har ikke lesescopet og får derfor heller ikke
  // ruten — plattformdriftens tverrgående funnliste er en UTVIDELSE av
  // svaret for en økt som alt har `security:read`, ikke en egen inngang.
  { nokkel: "datakvalitet", scope: "security:read", modulflate: 3 },
  // M-4 (093): retensjonsregnskapet er en BASISRUTE bak admin-
  // lesescopet — samme presedens som `modellstyring` og
  // `driftstatus` over (#315: ingen modulflate-flipp for en
  // plattforminternflate). Kontrollplanet (`platform:admin`) er en
  // utvidelse av SVARET, ikke en annen rute, så scopet her er det
  // samme som API-et bak flaten krever.
  { nokkel: "retensjon", scope: "security:read", modulflate: 4 },
  // M-9 (095): ordlisten er en BASISRUTE bak `decisions:read` — scopet
  // API-et bak den krever (`GET /v1/kunnskap` i RUTESCOPE), som resten
  // av tabellen: menyen lover aldri en flate serveren svarer 403 på.
  //
  // (Flyttet til venstremenyen 1/9 — se vedtaket over.)
  // en ordliste er ikke en modul kunden slår på og av — det er
  // bedriftens egne ord, og enhver som leser en beslutning skal kunne
  // slå opp et begrep i den uten å gå veien om et modulkort. Samme
  // presedens som `modellstyring` og `driftstatus` over, av motsatt
  // grunn: de er plattformens tilstand, denne er kundens språk.
  { nokkel: "kunnskap", scope: "decisions:read", modulflate: 9 },
  // M-21 (096): avtale- og fristagenten. BASISRUTE bak `decisions:read`
  // — scopet API-et bak flatens SVAKESTE ledd krever (wcagkontroll-
  // regelen): lista er `GET /v1/plikt` i RUTESCOPE, og de tre
  // skriveveiene er alt gatet både inne på flaten og i registeret
  // (`bestilling:opprett`). En sammenslått oppføring skal aldri inndra
  // tilgang, og her ville et mutasjonsscope på ruten fjernet hele
  // registeret for `leser`, `sikkerhet`, `godkjenner` og
  // `policyforvalter` — som alle har lov til å SE hvilke frister som
  // løper.
  //
  // (Flyttet til venstremenyen 1/9 — se vedtaket over.)
  // og driftstatus): ruten bor i toppnavigasjonen. M-21 står
  // `under_utvikling` i katalogen, og en modulkort-inngang ville lovet
  // en modul kunden kan kjøpe.
  { nokkel: "avtalefrist", scope: "decisions:read", modulflate: 21 },
  // M-12 (097): tilgangsregisteret. MODULFLATE bak `security:read` —
  // scopet API-et bak flatens svakeste ledd krever (`GET /v1/tilgang` i
  // RUTESCOPE), som resten av tabellen: menyen lover aldri en flate
  // serveren svarer 403 på.
  //
  // OG SCOPET ER ET ANNET ENN NABOENS, med vilje. `avtalefrist` over
  // står bak `decisions:read` fordi en fristliste er tenantens egen
  // driftstilstand. Et tilgangsregister er noe annet: det er kartet over
  // hvem som har admin på hvilket system, med kritikalitet per objekt.
  // Med `decisions:read` ville hver `leser`, `godkjenner` og
  // `policyforvalter` fått det kartet — og en sammenslått oppføring skal
  // aldri UTVIDE tilgang like lite som den skal inndra den.
  // Skriveveiene er gatet på `bestilling:opprett` i `RUTESCOPE`.
  { nokkel: "tilgang", scope: "security:read", modulflate: 12 },
  // M-22 (098): SaaS- og lisensagenten. MODULFLATE bak `decisions:read`
  // — scopet API-et bak flatens SVAKESTE ledd krever (wcagkontroll-
  // regelen): lista er `GET /v1/lisens` i RUTESCOPE, og de tre
  // skriveveiene er alt gatet både inne på flaten og i registeret
  // (`bestilling:opprett`). En sammenslått oppføring skal aldri inndra
  // tilgang, og her ville et mutasjonsscope på ruten fjernet hele
  // registeret for `leser`, `sikkerhet`, `godkjenner` og
  // `policyforvalter` — som alle har lov til å SE hva virksomheten
  // betaler for og når det må besluttes.
  { nokkel: "lisens", scope: "decisions:read", modulflate: 22 },
  // M-30 (099): forespørselsregisteret. BASISRUTE bak `security:read`
  // — scopet API-et bak flaten krever (`GET /v1/personvern` i
  // RUTESCOPE), som resten av tabellen: menyen lover aldri en flate
  // serveren svarer 403 på.
  //
  // OG SCOPET ER MED VILJE IKKE `decisions:read`, til forskjell fra
  // naboen over. Det scopet har ALLE kunderollene, og dette registeret
  // sier hvem i virksomheten som har krevd innsyn i, retting av eller
  // sletting av sine egne personopplysninger. `security:read` er
  // compliance/ops-klassen (`sikkerhet` og `admin`) — den samme
  // `retensjon` og `datakvalitet` over ligger i, og et personvernombuds
  // arbeidsflate hører hjemme nettopp der. Her ville en bredere
  // oppføring ikke bevart tilgang, den ville UTVIDET den.
  //
  // De fire skriveveiene er gatet både inne på flaten og i registeret
  // (`bestilling:opprett`), så `sikkerhet` ser listen uten å kunne
  // endre den: å lese hvilke frister som løper er tilsyn, å svare på
  // vegne av virksomheten er myndighet.
  { nokkel: "personvern", scope: "security:read", modulflate: 30 },
  // M-34 (100): kontrollregisteret. MODULFLATE bak `security:read` —
  // scopet API-et bak flaten krever (`GET /v1/compliance` i RUTESCOPE),
  // som resten av tabellen: menyen lover aldri en flate serveren svarer
  // 403 på.
  //
  // SCOPET ER SNEVRERE ENN NABOENS, OG DET ER EN DOM. Fristregisteret
  // over står bak `decisions:read` fordi «hvilke frister løper» ikke er
  // administratorens hemmelighet. Kontrollregisteret er en annen ting:
  // avviksbeskrivelser og evidenshenvisninger er revisjonsmateriale, og
  // PR-008 §1 beskriver nettopp `security:read` som den valgfrie
  // ops/compliance-scopen på en tenantbundet brukersesjon —
  // `autorisasjon.py` kaller rollen `sikkerhet` for «Compliance/ops»
  // med rene ord. Dette er flaten det scopet ble laget for.
  //
  // Regelen om flatens SVAKESTE ledd (wcagkontroll-regelen) er fortsatt
  // den som gjelder: lesingen krever `security:read`, og de tre
  // skriveveiene er gatet både inne på flaten og i `RUTESCOPE` på
  // `bestilling:opprett`.
  { nokkel: "compliance", scope: "security:read", modulflate: 34 },
  // M-13 (101): avstemmingsregisteret. MODULFLATE bak `okonomi:read` —
  // scopet API-et bak flaten krever (`GET /v1/avstemming` i RUTESCOPE),
  // som resten av tabellen: menyen lover aldri en flate serveren svarer
  // 403 på.
  //
  // SCOPET ER NYTT, og det er den eneste oppføringen i denne tabellen som
  // ikke gjenbruker et eksisterende. Begrunnelsen står i
  // `autorisasjon.py` og gjentas ikke her, men den korte formen er at
  // ingen av de to kandidatene passet: `decisions:read` har ALLE
  // kunderollene, og et avstemmingsregister sier hvor pengene til
  // virksomheten går; `security:read` er «Compliance/ops», og økonomi er
  // noe annet enn drift. Kretsen er `admin` alene i v1 — smalere enn
  // noen annen flate her.
  //
  // Regelen om flatens SVAKESTE ledd (wcagkontroll-regelen) er fortsatt
  // den som gjelder: lesingen krever `okonomi:read`, og de fem
  // skriveveiene er gatet både inne på flaten og i `RUTESCOPE` på
  // `bestilling:opprett`.
  { nokkel: "avstemming", scope: "okonomi:read", modulflate: 13 },
  // M-17 (102): kundeservicekøen. MODULFLATE bak `decisions:read` —
  // scopet API-et bak flaten krever (`GET /v1/kundeservice` i
  // RUTESCOPE), som resten av tabellen.
  //
  // SCOPET ER DET BREDESTE I TABELLEN, og det er en dom og ikke slurv:
  // kundeservicekøen er tenantens alminnelige arbeidsflate, ikke
  // revisjonsmateriale (M-34) og ikke virksomhetens pengestrøm (M-13).
  // Den som svarer kunder skal se den.
  //
  // SELVE INNHOLDET LIGGER LIKEVEL BAK `kundeservice:innhold`, som
  // hentes av et eget endepunkt inne på flaten. Regelen om flatens
  // SVAKESTE ledd (wcagkontroll-regelen) gjelder MENYOPPFØRINGEN: den
  // lover at listen kan vises, ikke at hver celle kan åpnes — og flaten
  // gater innholdsknappen på sitt eget scope.
  { nokkel: "kundeservice", scope: "decisions:read", modulflate: 17 },
  // M-18 (103): onboardingløpene. MODULFLATE bak `decisions:read` —
  // scopet API-et bak flaten krever (`GET /v1/onboarding` i RUTESCOPE).
  //
  // SCOPET ER DET BREDE, og det er en dom: hvem som gjør hva for en ny
  // kunde er tenantens alminnelige arbeidsflate, ikke administratorens
  // hemmelighet — og det finnes ingen persondata her utover et
  // kundenavn og interne bruker-id-er. De seks skriveveiene er gatet
  // både inne på flaten og i `RUTESCOPE` på `bestilling:opprett`.
  { nokkel: "onboarding", scope: "decisions:read", modulflate: 18 },
  // M-23 (104): fordringsregisteret. MODULFLATE bak `okonomi:read` —
  // scopet M-13 (101) innførte, GJENBRUKT og ikke nytt. Dette er
  // nøyaktig kretsen det ble laget for: hvem som skylder oss hva er
  // virksomhetens pengestrøm, ikke allmenn tilstandsinnsikt, og
  // `admin` er alene om det i v1.
  //
  // De fem skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`.
  { nokkel: "fordring", scope: "okonomi:read", modulflate: 23 },
  // M-24 (105): leverandør- og SLA-registeret. MODULFLATE bak
  // `okonomi:read` — samme scope som M-13 (101) innførte og M-23 (104)
  // gjenbrukte, og av samme grunn: hva vi har AVTALT å betale, og hva
  // vi FAKTISK betaler, er virksomhetens pengestrøm. `admin` er alene
  // om det i v1.
  //
  // De fem skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`. Ingen av dem betaler noe — den handlingen
  // finnes ikke i v1.
  { nokkel: "leverandor", scope: "okonomi:read", modulflate: 24 },
  // M-14 (106): fakturakontrollen. MODULFLATE bak `okonomi:read` —
  // samme scope som M-13 (101) innførte og M-23/M-24 gjenbrukte, og av
  // samme grunn: hva noen krever av oss er virksomhetens pengestrøm.
  // `admin` er alene om det i v1.
  //
  // De fem skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`. Ingen av dem bokfører noe og ingen av dem
  // attesterer — de handlingene finnes ikke i v1.
  { nokkel: "faktura", scope: "okonomi:read", modulflate: 14 },
  // M-25 (107): prosjekt- og kontraktregisteret. MODULFLATE bak
  // `okonomi:read` — hva et prosjekt koster og hva vi kan kreve for det
  // er virksomhetens pengestrøm. `admin` er alene om det i v1.
  //
  // De seks skriveveiene er gatet både inne på flaten og i
  // `RUTESCOPE` på `bestilling:opprett`. Ingen av dem fakturerer noe.
  { nokkel: "prosjekt", scope: "okonomi:read", modulflate: 25 },
  // M-26 (108): prisboka. MODULFLATE bak `okonomi:read` — hva vi tar
  // betalt er virksomhetens pengestrøm. `admin` er alene om det i v1.
  //
  // De fem skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`. Ingen av dem genererer et tilbud.
  { nokkel: "prisbok", scope: "okonomi:read", modulflate: 26 },
  // M-27 (109): lagerregisteret. MODULFLATE bak `okonomi:read` — en
  // beholdning er bundet kapital, og det er samme leseklasse som de
  // fem foregående registrene.
  //
  // De seks skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`. Ingen av dem bestiller noe.
  { nokkel: "lager", scope: "okonomi:read", modulflate: 27 },
  // M-42 (110): kontoregisteret. MODULFLATE bak `okonomi:read` — og
  // valget er bevisst: den som handler på «en leverandør har byttet
  // konto» er den som BETALER, ikke sikkerhetsvakten. `sikkerhet` ser
  // den derfor ikke i v1; funnene havner i M-37s kø som alt annet.
  //
  // De fem skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`. Ingen av dem stopper en betaling.
  { nokkel: "kontovakt", scope: "okonomi:read", modulflate: 42 },
  // M-41 (111): betalingsregisteret. MODULFLATE bak `okonomi:read` —
  // betalingsstatus ER virksomhetens pengestrøm, og den som handler på
  // et beløpsavvik er den som fakturerer.
  //
  // De fem skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`. Ingen av dem refunderer.
  { nokkel: "betaling", scope: "okonomi:read", modulflate: 41 },
  // M-19 (112): adresseregisteret. MODULFLATE bak `okonomi:read` —
  // leveringsadressen er den som avgjør om varen kommer fram, og den
  // som handler på en ukontrollert adresse er den som sender.
  //
  // De fire skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`. Ingen av dem slår noe opp.
  { nokkel: "adresse", scope: "okonomi:read", modulflate: 19 },
  // M-39 (113): lønnsgrunnlaget. MODULFLATE bak `okonomi:read` — hvor
  // mye en navngitt ansatt har jobbet er persondata OG grunnlaget for
  // hens inntekt, og den som handler på et avvik er den som kjører lønn.
  //
  // De fem skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`. Ingen av dem utbetaler, og ingen av dem
  // produserer en lønnsfil.
  { nokkel: "lonn", scope: "okonomi:read", modulflate: 39 },
  // M-44 (114): kampanjeregisteret. MODULFLATE bak `okonomi:read` —
  // samtykke og kontaktpunkt er persondata, og den som handler på et
  // frekvensbrudd er den som eier markedsføringen.
  //
  // De seks skriveveiene er gatet både inne på flaten og i `RUTESCOPE`
  // på `bestilling:opprett`. Ingen av dem sender noe.
  { nokkel: "kampanje", scope: "okonomi:read", modulflate: 44 },
  // M-48 (116): motpartsregisteret. MODULFLATE bak `okonomi:read` —
  // hvem vi handler med og hva vi tør gi dem er økonomi, og den som
  // handler på en uvurdert motpart er den som fakturerer.
  //
  // DENNE FLATEN ER KLYNGENS ENESTE MED EN «SLÅ OPP»-KNAPP. Den er
  // gatet på `bestilling:opprett` som de andre skriveveiene, men
  // doktrinen ligger ikke i scopet: formål og hjemmel er påkrevde
  // felt uten standardverdi, og ferskhetsvinduet står synlig ved
  // knappen. Ingen av veiene setter en kredittgrense eller avslår en
  // motpart.
  { nokkel: "motpart", scope: "okonomi:read", modulflate: 48 },
  // M-49 (117): sanksjonskontrollen. MODULFLATE bak `okonomi:read` —
  // hvem som står på en sanksjonsliste er en opplysning med
  // rettsvirkning, og den som handler på et uavklart treff er den som
  // handler.
  //
  // FLATEN BLOKKERER INGENTING OG AVFEIER INGEN NAVNELIKHET. De to
  // fraværene er portene `modulen_blokkerte_motpart` og
  // `modulen_avfeide_navnelikhet` — beslutningen, motargumentet og
  // utløseren står i toppen av migrasjon 117.
  { nokkel: "sanksjon", scope: "okonomi:read", modulflate: 49 },
  // M-46 (118): anbuds- og konkurransevakten. MODULFLATE bak
  // `okonomi:read` — en anbudsfrist som passerer er den ene feilen som
  // ikke kan rettes dagen etter.
  //
  // FLATEN SENDER INGEN TILBUD, og den kan ikke skrive et faktapunkt
  // uten kilde: `utkastpunkt` i 118 har ingen fritekstkolonne.
  // Fraværene er portene `modulen_sendte_tilbud` og
  // `utkastpunkt_uten_kilde`.
  { nokkel: "anbud", scope: "okonomi:read", modulflate: 46 },
  // M-51 (119): tilskudds- og støtteordningsvakten. MODULFLATE bak
  // `okonomi:read` — et tilskuddsestimat er et tall en bedrift
  // PLANLEGGER ETTER, og avstanden mellom estimat og lovnad er
  // lønnsutbetalinger.
  //
  // FLATEN SENDER INGEN SØKNAD, og den kan ikke sette et beløp uten
  // kildepost: `tilskuddsestimat` i 119 har ingen beløpskolonne.
  { nokkel: "tilskudd", scope: "okonomi:read", modulflate: 51 },
  // M-55 (120): merkevare- og IP-overvåkeren. MODULFLATE bak
  // `okonomi:read` — et merkevarefunn er DOKUMENTASJON, og et krav
  // sendt på et automatisk funn ville vært en anklage mot en navngitt
  // part.
  //
  // FLATEN SENDER INGEN KRAV OG INGEN KLAGE, og den kan ikke
  // registrere et funn uten bevaringskopi: `merkevarefunn.kopi_id` i
  // 120 er NOT NULL med fremmednøkkel. Fraværene er portene
  // `modulen_sendte_krav` og `funn_uten_bevaringskopi`.
  { nokkel: "merkevare", scope: "okonomi:read", modulflate: 55 },
  // M-54 (121): EHF- og Peppol-avviksretteren. MODULFLATE bak
  // `okonomi:read` — en faktura er et betalingskrav, og en formfeil i
  // den er en teknisk sak med en økonomisk konsekvens.
  //
  // FLATEN SENDER INGEN FAKTURA, og den kan ikke validere mot et
  // utløpt regelsett: 121 nekter. Fraværene er portene
  // `modulen_sendte_faktura` og `validering_mot_utlopt_skjema`.
  { nokkel: "ehf", scope: "okonomi:read", modulflate: 54 },
  // M-52 (122): toll- og HS-kodeagenten. MODULFLATE bak
  // `okonomi:read` — en HS-kode er en RETTSLIG PÅSTAND om hva en vare
  // er, og feil kode gir bot som treffer kunden.
  //
  // FLATEN DEKLARERER INGENTING, og den kan ikke avgi et forslag uten
  // grunnlag: `m52_avgi_forslag` skriver forslaget og grunnene i samme
  // setning. Fraværene er portene `modulen_deklarerte` og
  // `forslag_uten_grunnlag`.
  { nokkel: "tollkode", scope: "okonomi:read", modulflate: 52 },
  // M-47 (123): myndighetsrapporteringsagenten. MODULFLATE bak
  // `okonomi:read` — flatens svakeste ledd, som resten av klyngen.
  // Modulen SENDER INGEN INNSENDING; men her er fraværet ikke nok, for
  // en frist som går uten innsending er nettopp skaden. Flaten viser
  // derfor det som HAR gått galt først.
  { nokkel: "myndighet", scope: "okonomi:read", modulflate: 47 },
  // M-50 (124): postjournal- og innsynsvakten. MODULFLATE bak
  // `okonomi:read`. Modulen HENTER INGENTING — postjournaler er
  // offentlige, men ti tusen oppslag sammenstilt i et register er en
  // PROFIL, og profilen er vår. Flaten viser derfor hvem vi
  // oppbevarer, og hvor lenge.
  { nokkel: "journal", scope: "okonomi:read", modulflate: 50 },
  // M-53 (127): HMS- og avviksmottak. MODULFLATE bak
  // `security:read` og IKKE `okonomi:read`: flaten viser
  // helseopplysninger etter GDPR art. 9 om navngitte ansatte, og
  // finansleseren har ingenting her å gjøre.
  // Modulen VARSLER INGEN MYNDIGHET og LUKKER INGEN AVVIK — den tar
  // imot, måler fristene og sier fra. Anonymt avvik er en TILSTAND og
  // ikke et tomt navnefelt: et felt som kan fylles blir fylt.
  { nokkel: "hms", scope: "security:read", modulflate: 53 },
  // M-15 (128): likviditets- og kostnadsagent. MODULFLATE bak
  // `okonomi:read` — dette er bank, fordringer og kontantbane, altså
  // finansleserens eget bord. Modulen sier ingenting opp og betaler
  // ingenting: et kostnadstiltak er et forslag.
  { nokkel: "likviditet", scope: "okonomi:read", modulflate: 15 },
];

// ADRESSER SOM EN GANG VIRKET, SKAL FORTSETTE Å VIRKE (Codex P2). En rute som
// fjernes herfra finnes fortsatt i bokmerker og i lenker kolleger har delt seg
// imellom, og verken ruteren eller dyplenken har noe å si om saken: `lagRuter`
// leser en ukjent rute som ingenting og tegner REServeflaten (som regel
// Oversikt), og `visningFraSok` forkaster en `?visning=` den ikke finner i
// menyen. Den som hadde bokmerket den periodiske kontrollen landet altså på en
// helt annen flate, uten et ord om hvorfor — og fant ingen «Plan» i menyen å
// klikke seg videre på heller, for den oppføringen er nettopp den som ble
// borte.
//
// Aliaset peker på FANEN, ikke bare flaten: `#/plan` → `#/wcagkontroll/plan`.
// Uten målet ville lenken landet på flatens startfane (Domener, eller
// Rapporter for en leseøkt), altså fortsatt ikke der brukeren skulle.
//
// En `Map`, ikke et objekt-oppslag: `{}[..]` svarer på `constructor` og
// `__proto__` med noe arvet og sant, og et alias-treff på en rute ingen har
// definert er ikke et treff.
const ARVEDE_RUTER = new Map([
  ["plan", { rute: "wcagkontroll", mal: "plan" }],
]);

// Den kanoniske `{ rute, mal }` for en arvet adresse, eller null når ruten
// ikke er arvet. Et mål fra den GAMLE adressen bæres videre urørt og vinner
// over aliasets standardmål: `#/plan/<plan_id>` blir `#/wcagkontroll/<plan_id>`,
// og samleflaten leser en nøkkel den ikke kjenner som en plan-id og åpner
// planfanen på den — nøyaktig det planvarslene gjør i dag.
export function arvetMaal(rute, mal) {
  const arvet = ARVEDE_RUTER.get(rute);
  if (!arvet) return null;
  return { rute: arvet.rute, mal: mal || arvet.mal };
}

// `modulflate` FØLGER MED UT (Codex P1). Mappingen plukket bare `nokkel`, og
// siden dette er den ENESTE veien `visApp` bygger ruter på, så skallet aldri
// et eneste `modulflate` i produksjon: hele vedtaket fra 24/8 — venstremenyen
// som modulnavigasjon — sto igjen i sitekartet uten å nå fram. WCAG kontroll
// og rekruttering ble liggende i toppnav, og modulkortene åpnet panelet i
// stedet for flaten. `scope` er det ENE feltet som med vilje blir igjen: det
// er brukt opp her, i filteret over, og en rute som bærer det videre inviterer
// leseren til å tro at det fortsatt gates på noe.
export function byggRuter(sesjon) {
  const ruter = BASISRUTER
    .filter((r) => !r.scope || harScope(sesjon, r.scope))
    .map(({ scope, ...rute }) => rute);
  if (kanForvaltePolicy(sesjon)) ruter.push({ nokkel: "policyadmin" });
  // Varsler krever ikke fullmakt til å ENDRE noe — å se at noe venter på deg
  // er en leserettighet. Ruten hører derfor MOTTAKEREN til, og etter 044 er
  // det ikke bare policyforvalteren: planadministratoren varsles om pauser og
  // gjentatte brudd.
  if (kanMottaVarsel(sesjon)) ruter.push({ nokkel: "varsler" });
  // Admin-flaten har TO lovlige innganger, og de er ikke den samme autoriteten:
  // `security:read` gir den tenantbundne ops-økten sin EGEN utrullingsrad, mens
  // `platform:admin` er plattformdriften som ser kontrollplanet. Krevde ruten
  // bare `security:read`, ville en ren plattformdriftsøkt — som per definisjon
  // ikke har kundens tenant-lokale scopes — falt stille til `oversikt`, og
  // `erPlattformdrift()` inne på flaten ville aldri fått si noe.
  if (harScope(sesjon, "security:read") || erPlattformdrift(sesjon)) {
    ruter.push({ nokkel: "admin" });
  }
  return ruter;
}

// Ruterens flatekart bygges fra de rutene økten FAKTISK har, ikke fra hele
// flatetabellen: gjør den ikke det, er scope-filteret i `byggRuter` bare
// menypynt, og `#/admin` skrevet rett i adressefeltet rendrer likevel.
export function tillatteFlater(ruter, flater) {
  const tillatt = {};
  for (const r of ruter) {
    if (flater[r.nokkel]) tillatt[r.nokkel] = flater[r.nokkel];
  }
  return tillatt;
}

export function visningFraSok(sok, ruter) {
  const q = new URLSearchParams(sok || "");
  const visning = q.get("visning");
  return ruter.some((r) => r.nokkel === visning) ? visning : null;
}

// Hash-en en dyplenke (`?visning=x`) skal sette, eller null hvis ruteren skal
// navigere selv. Kun ÉN av delene skal skje: å sette hash utløser `hashchange`,
// og et `naviger()` i tillegg ville rendret flaten — og kalt API-et — to ganger.
export function hashForDypLenke(sok, hash, ruter) {
  if (hash) return null;
  const visning = visningFraSok(sok, ruter);
  if (visning) return `#/${visning}`;
  // `?visning=` er den ANDRE inngangen til en arvet adresse, og den slipper
  // ikke gjennom `visningFraSok`: den svarer bare på ruter økten HAR, og en
  // fjernet rute har ingen. Aliaset løses derfor opp her, mot menyen økten
  // faktisk fikk — en `?visning=plan` uten `decisions:read` er like lite en
  // dyplenke som før, og faller til ruterens reserve.
  const raa = new URLSearchParams(sok || "").get("visning");
  const arvet = raa ? arvetMaal(raa, null) : null;
  if (!arvet || !ruter.some((r) => r.nokkel === arvet.rute)) return null;
  // Skrivemåten er `hashDeler`s omvendte: ett ledd, prosent-kodet.
  return `#/${arvet.rute}/${encodeURIComponent(arvet.mal)}`;
}
