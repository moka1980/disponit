export function harScope(sesjon, scope) {
  return (sesjon.scopes || []).includes(scope);
}

// Å forvalte policy krever skrive- eller aktiveringsscope. `policy:read` er
// IKKE nok: rollene `admin` og `sikkerhet` har bare lesetilgang, og en flate
// med mutasjonsknapper ville bare gitt dem 403 fra API-et.
export function kanForvaltePolicy(sesjon) {
  return harScope(sesjon, "policy:write") || harScope(sesjon, "policy:activate");
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

export function byggRuter(sesjon) {
  // Kundens arbeidsflate er en LESEFLATE: modulstatus, roller, integrasjoner.
  // Den hører derfor til basisrutene. Lå den bak `kanForvaltePolicy`, landet en
  // vanlig `leser` — som kundeinnloggingen sender til `/?visning=kundeadmin` —
  // stille på `oversikt`, og knappen «Åpne kundeflate» åpnet noe annet enn den
  // lovte. Det er bare policyADMINISTRASJONEN som krever forvaltningsscope.
  const ruter = [
    { nokkel: "oversikt" }, { nokkel: "policy" },
    { nokkel: "beslutninger" }, { nokkel: "unntak" },
    { nokkel: "kundeadmin" },
  ];
  if (kanForvaltePolicy(sesjon)) ruter.push({ nokkel: "policyadmin" });
  if (harScope(sesjon, "security:read")) ruter.push({ nokkel: "admin" });
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
