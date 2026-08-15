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

export function visningFraSok(sok, ruter) {
  const q = new URLSearchParams(sok || "");
  const visning = q.get("visning");
  return ruter.some((r) => r.nokkel === visning) ? visning : null;
}
