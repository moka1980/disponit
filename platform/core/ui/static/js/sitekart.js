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
  { nokkel: "wcagkontroll", scope: "decisions:read" },
];

export function byggRuter(sesjon) {
  const ruter = BASISRUTER
    .filter((r) => !r.scope || harScope(sesjon, r.scope))
    .map((r) => ({ nokkel: r.nokkel }));
  if (kanForvaltePolicy(sesjon)) ruter.push({ nokkel: "policyadmin" });
  // Varsler krever ikke fullmakt til å ENDRE noe — å se at noe venter på deg
  // er en leserettighet. Kan du forvalte policy, kan du også bli ventet på.
  if (kanForvaltePolicy(sesjon)) ruter.push({ nokkel: "varsler" });
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
  return visning ? `#/${visning}` : null;
}
