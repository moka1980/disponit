// Produkt- og plattformdata for den offentlige webflaten og de nye
// administrasjonsvisningene. Én statuskilde gjør at modulstatus kan oppdateres
// ett sted når en modul faktisk er ferdig.

export const MODULSTATUS = {
  1: "i_drift",
  2: "i_drift",
  37: "i_drift",
  38: "bygges",
};

export const MODULOVERSIKT = [
  {
    id: 1,
    navn_nokkel: "site.modul.m1.navn",
    status: "i_drift",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m1.tekst",
  },
  {
    id: 2,
    navn_nokkel: "site.modul.m2.navn",
    status: "i_drift",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m2.tekst",
  },
  {
    id: 37,
    navn_nokkel: "site.modul.m37.navn",
    status: "i_drift",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m37.tekst",
  },
  {
    id: 38,
    navn_nokkel: "site.modul.m38.navn",
    status: "bygges",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m38.tekst",
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

export const TENANTOVERSIKT = [
  {
    navn_nokkel: "site.tenant.nordvik.navn",
    plan_nokkel: "site.plan.pilot",
    moduler: ["M-1", "M-2", "M-37"],
    neste_nokkel: "site.tenant.nordvik.neste",
  },
  {
    navn_nokkel: "site.tenant.bjorkli.navn",
    plan_nokkel: "site.plan.pilot",
    moduler: ["M-1", "M-2"],
    neste_nokkel: "site.tenant.bjorkli.neste",
  },
  {
    navn_nokkel: "site.tenant.granmo.navn",
    plan_nokkel: "site.plan.internt",
    moduler: ["M-1", "M-2", "M-37", "M-38"],
    neste_nokkel: "site.tenant.granmo.neste",
  },
];

export function plattformTelling() {
  const iDrift = Object.values(MODULSTATUS).filter((s) => s === "i_drift").length;
  const bygges = Object.values(MODULSTATUS).filter((s) => s === "bygges").length;
  return { iDrift, bygges, planlagt: 45 - iDrift - bygges, totalt: 45 };
}
