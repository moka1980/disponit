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
  // eiervedtaket (#315-presedensen): INGEN modulflate-flipp, så ruten
  // bor i toppnavigasjonen, ikke bak et modulkort. Scopet er API-ets
  // (`GET /v1/modellstyring` krever `security:read` i RUTESCOPE) —
  // samme regel som resten av tabellen: menyen lover aldri en flate
  // serveren svarer 403 på. En ren `platform:admin`-økt har ikke
  // lesescopet og får derfor heller ikke ruten.
  { nokkel: "modellstyring", scope: "security:read" },
  // M-6 PR-B: e-postagentens kildeflate. Scopet er flatens SVAKESTE
  // ledd (wcagkontroll-regelen): lista bak ruten krever `epost:read`;
  // koble-til/deaktiver muterer og avgjøres INNE på flaten (og av
  // serveren) med `epost:kilde:administrer`. Basisrute uten modulflate
  // i PR-B — klassifiserings-/utkastsflaten og en eventuell
  // modulkort-inngang er PR-D.
  { nokkel: "epost", scope: "epost:read" },
  // M-35 (089): kontinuitet er en BASISRUTE bak `kontinuitet:read` —
  // UTEN modulflate, samme #315-presedens som modellstyring: ruten bor
  // i toppnavigasjonen, ikke bak et modulkort, fordi beredskapen er
  // plattformens tilstand og ikke en modul kunden har kjøpt. Scopet er
  // API-ets (`GET /v1/kontinuitet` i RUTESCOPE), så menyen lover aldri
  // en flate serveren svarer 403 på.
  { nokkel: "kontinuitet", scope: "kontinuitet:read" },
  // 044-planflaten har INGEN egen rute lenger: periodisk kontroll er en
  // fane under wcagkontroll (eier 19/8 — samme arbeidsflyt, én
  // menyoppføring), og wcagkontroll-ruten bærer alt planfanen trenger
  // (decisions:read). Den gamle adressen lever videre som alias under.
  //
  // M-10 + M-11 (090/091): driftstatus er en BASISRUTE bak admin-
  // lesescopet — samme presedens som `modellstyring` over, og samme
  // eiervedtak: INGEN modulflate-flipp, så ruten bor i toppnavigasjonen
  // og ikke bak et modulkort. De to er heller ikke moduler; de er
  // plattformens eget innsyn i seg selv.
  //
  // Scopet er API-ets: BÅDE `GET /v1/drift/backup` og
  // `GET /v1/drift/selvtest` krever `security:read` i RUTESCOPE, og
  // flaten henter begge i ett kall-par. En rute som lovet mer enn det
  // svakeste av de to endepunktene ville vært nøyaktig løftet denne
  // tabellen finnes for å ikke gi — men her er de like, så flaten er
  // enten hel eller ikke synlig.
  { nokkel: "driftstatus", scope: "security:read" },
  // M-3 (092): datakvalitetsflaten. BASISRUTE bak admin-lesescopet —
  // samme presedens som `modellstyring` og `driftstatus` over, og samme
  // eiervedtak: INGEN modulflate-flipp, så ruten bor i toppnavigasjonen
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
  { nokkel: "datakvalitet", scope: "security:read" },
  // M-4 (093): retensjonsregnskapet er en BASISRUTE bak admin-
  // lesescopet — samme presedens som `modellstyring` og
  // `driftstatus` over (#315: ingen modulflate-flipp for en
  // plattforminternflate). Kontrollplanet (`platform:admin`) er en
  // utvidelse av SVARET, ikke en annen rute, så scopet her er det
  // samme som API-et bak flaten krever.
  { nokkel: "retensjon", scope: "security:read" },
  // M-9 (095): ordlisten er en BASISRUTE bak `decisions:read` — scopet
  // API-et bak den krever (`GET /v1/kunnskap` i RUTESCOPE), som resten
  // av tabellen: menyen lover aldri en flate serveren svarer 403 på.
  //
  // INGEN `modulflate`, og det er en vurdering og ikke en forglemmelse:
  // en ordliste er ikke en modul kunden slår på og av — det er
  // bedriftens egne ord, og enhver som leser en beslutning skal kunne
  // slå opp et begrep i den uten å gå veien om et modulkort. Samme
  // presedens som `modellstyring` og `driftstatus` over, av motsatt
  // grunn: de er plattformens tilstand, denne er kundens språk.
  { nokkel: "kunnskap", scope: "decisions:read" },
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
