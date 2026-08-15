export function harScope(sesjon, scope) {
  return (sesjon.scopes || []).includes(scope);
}

export function byggRuter(sesjon) {
  const ruter = [
    { nokkel: "oversikt" }, { nokkel: "policy" },
    { nokkel: "beslutninger" }, { nokkel: "unntak" },
  ];
  if (harScope(sesjon, "policy:write") || harScope(sesjon, "policy:activate")) {
    ruter.push({ nokkel: "kundeadmin" });
    ruter.push({ nokkel: "policyadmin" });
  }
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
