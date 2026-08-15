// Produkt- og plattformdata for den offentlige webflaten og de nye
// administrasjonsvisningene. Én statuskilde gjør at modulstatus kan oppdateres
// ett sted når en modul faktisk er ferdig.

// Kanonisk statuskilde. Alt annet — kort, merker og KPI-er — utleder herfra,
// så en modul som skifter tilstand oppdateres ETT sted.
export const MODULSTATUS = {
  1: "i_drift",
  2: "i_drift",
  37: "i_drift",
  38: "bygges",
};

// Status står IKKE her: modulene beskriver navn, fase og tekst, mens
// `MODULSTATUS` eier hva de faktisk er. Sto den begge steder, ville en modul
// som skifter tilstand vise `bygges` på kortet mens KPI-en talte den som
// `i_drift` — eller motsatt, avhengig av hvilket sted som ble oppdatert.
const MODULER = [
  {
    id: 1,
    navn_nokkel: "site.modul.m1.navn",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m1.tekst",
  },
  {
    id: 2,
    navn_nokkel: "site.modul.m2.navn",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m2.tekst",
  },
  {
    id: 37,
    navn_nokkel: "site.modul.m37.navn",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m37.tekst",
  },
  {
    id: 38,
    navn_nokkel: "site.modul.m38.navn",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m38.tekst",
  },
];

// En modul uten oppføring i `MODULSTATUS` er planlagt: den er beskrevet, men
// ingen har sagt at den er i gang.
export function modulStatus(id) {
  return MODULSTATUS[id] || "planlagt";
}

export const MODULOVERSIKT = MODULER.map((mod) => ({
  ...mod, status: modulStatus(mod.id),
}));

// Rolleguiden kundeflaten viser. `scopes` er rollens FAKTISKE scopes slik
// `platform/core/api/autorisasjon.py` utleder dem, og `test_ui_kontrakt.py`
// pinner listen mot den kanoniske `ROLLE_TIL_SCOPES`. Uten den bindingen kunne
// guiden love en fullmakt rollen ikke har — som da `godkjenner` ble beskrevet
// som å attestere policy, mens attestasjon krever `policy:activate` og bare
// `policyforvalter` har den. Kunden ville tildelt feil rolle og oppdaget det
// først på en 403.
export const KUNDEROLLER = [
  {
    id: "leser",
    navn_nokkel: "ui.kundeadmin.rolle.leser",
    tekst_nokkel: "ui.kundeadmin.rolle.leser_tekst",
    scopes: ["decisions:read", "exceptions:read", "policy:read"],
  },
  {
    id: "godkjenner",
    navn_nokkel: "ui.kundeadmin.rolle.godkjenner",
    tekst_nokkel: "ui.kundeadmin.rolle.godkjenner_tekst",
    scopes: ["decisions:read", "exceptions:read", "exceptions:approve",
             "exceptions:reject", "exceptions:escalate"],
  },
  {
    id: "policyforvalter",
    navn_nokkel: "ui.kundeadmin.rolle.policyforvalter",
    tekst_nokkel: "ui.kundeadmin.rolle.policyforvalter_tekst",
    scopes: ["decisions:read", "policy:read", "policy:write", "policy:activate"],
  },
];

export const FASEOVERSIKT = [
  {
    navn_nokkel: "site.fase.fundament",
    status: "aktiv",
    tekst_nokkel: "site.fase.fundament.tekst",
  },
  {
    navn_nokkel: "site.fase.operasjoner",
    status: "planlagt",
    tekst_nokkel: "site.fase.operasjoner.tekst",
  },
  {
    navn_nokkel: "site.fase.autopiloter",
    status: "planlagt",
    tekst_nokkel: "site.fase.autopiloter.tekst",
  },
  {
    navn_nokkel: "site.fase.global",
    status: "planlagt",
    tekst_nokkel: "site.fase.global.tekst",
  },
];

// `moduler` er modul-ID-er, ikke visningsstrenger: da kan en tenants tildeling
// slås opp mot `MODULOVERSIKT` i stedet for å parses tilbake fra "M-1".
export const TENANTOVERSIKT = [
  {
    id: "nordvik",
    navn_nokkel: "site.tenant.nordvik.navn",
    plan_nokkel: "site.plan.pilot",
    moduler: [1, 2, 37],
    neste_nokkel: "site.tenant.nordvik.neste",
  },
  {
    id: "bjorkli",
    navn_nokkel: "site.tenant.bjorkli.navn",
    plan_nokkel: "site.plan.pilot",
    moduler: [1, 2],
    neste_nokkel: "site.tenant.bjorkli.neste",
  },
  {
    id: "granmo",
    navn_nokkel: "site.tenant.granmo.navn",
    plan_nokkel: "site.plan.internt",
    moduler: [1, 2, 37, 38],
    neste_nokkel: "site.tenant.granmo.neste",
  },
];

export function modulmerke(id) {
  return `M-${id}`;
}

// Utrullingsraden for ÉN tenant, eller null når vi ikke kjenner tenanten.
export function tenantRad(tenant) {
  const navn = String(tenant || "").trim().toLowerCase();
  if (!navn) return null;
  return TENANTOVERSIKT.find((tt) => tt.id === navn) || null;
}

// Modultildelingen for ÉN tenant, eller null når vi ikke kjenner tenanten.
// Null betyr «vet ikke», ikke «ingen moduler»: en flate som ikke vet, skal si
// det — ikke vise hele plattformkatalogen som om den var kundens.
export function modulerForTenant(tenant) {
  const rad = tenantRad(tenant);
  if (!rad) return null;
  return MODULOVERSIKT.filter((mod) => rad.moduler.includes(mod.id));
}

// Tenantens egne tall. `planlagt` er plattformmodulene kunden ennå ikke har
// fått aktivert — ikke plattformens globale restliste.
export function tenantTelling(moduler) {
  const iDrift = moduler.filter((m) => m.status === "i_drift").length;
  const bygges = moduler.filter((m) => m.status === "bygges").length;
  const totalt = plattformTelling().totalt;
  return { iDrift, bygges, planlagt: totalt - moduler.length, totalt };
}

export function plattformTelling() {
  const iDrift = Object.values(MODULSTATUS).filter((s) => s === "i_drift").length;
  const bygges = Object.values(MODULSTATUS).filter((s) => s === "bygges").length;
  return { iDrift, bygges, planlagt: 45 - iDrift - bygges, totalt: 45 };
}
