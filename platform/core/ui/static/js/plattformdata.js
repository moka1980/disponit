// Produkt- og plattformdata for den offentlige webflaten og de nye
// administrasjonsvisningene. Én statuskilde gjør at modulstatus kan oppdateres
// ett sted når en modul faktisk er ferdig.
import { t } from "./i18n.js";

// Kanonisk statuskilde. Alt annet — kort, merker og KPI-er — utleder herfra,
// så en modul som skifter tilstand oppdateres ETT sted.
//
// Verdiene er ikke en mening om modulene: de er AVLEDET av manifestenes to
// akser (`platform/modules/*/manifest.yaml`), og
// `test_ui_kontrakt.py::test_modulstatus_folger_manifestene` pinner kartet mot
// dem. Manifestet skiller bevisst `status` (er modulen GODKJENT?) fra
// `driftstilstand` (hvor kjører den FAKTISK?) — kollapset flaten de to til ett
// ord, lovet den drift der registeret sa `ikke_i_drift`. Avledningen:
//
//   driftstilstand: produksjon          → i_drift     modulen kjører hos kunder
//   status: aktiv, ikke i produksjon    → klargjort   godkjent, men ikke i drift
//   manifest finnes, status ikke aktiv  → bygges      under utvikling
//   ingen manifest                      → planlagt    beskrevet, ikke påbegynt
export const MODULSTATUS = {
  1: "klargjort",   // m01_policy: status aktiv, driftstilstand ikke_i_drift
  2: "bygges",      // m02_revisjonslogg: under_utvikling, ikke_i_drift
  37: "bygges",     // m37_unntak: under_utvikling, ikke_i_drift
  38: "planlagt",   // ingen manifest i platform/modules/ ennå
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

// INGEN TENANTDATA I DENNE FILA (Codex P1). `/ui/{sti}` serveres uten
// sesjonssjekk — den anonyme landingssiden importerer dette modultreet — så alt
// som ligger her kan lastes ned av hvem som helst, uansett hvilken
// scope-sjekk flatene gjør før de RENDRER. Et tenantregister her ville lekket
// hver kundes navn, plan, modultildeling og neste steg til en `curl`.
//
// Samme grunn gjelder `locales/`: `/ui/locale/nb` svarer 200 uten cookie, så
// kundenavn kan heller ikke ligge der som oversettelsesnøkler. Tenantdata er
// DATA, ikke chrome-tekst — den hentes fra et autentisert, server-autorisert
// endepunkt og sendes inn i flatene som `ctx.tenanter` / `ctx.moduler`.
// Flatene viser en eksplisitt tomtilstand til det endepunktet finnes.
//
// `platform/core/ui/test/offentlige_ressurser.test.js` håndhever begge deler.

export function modulmerke(id) {
  return `M-${id}`;
}

// Planetiketten. Serveren sender en KODE (`pilot`, `internt`) fordi planen er
// et lukket vokabular: sendte den etiketten, viste den engelske tabellen
// «Internt» uansett hvilket språk brukeren hadde valgt. Selve etiketten er
// chrome og ligger i locale-settet — det er TILDELINGEN av en plan til en
// kunde som er tenantdata, og den blir bak den autentiserte veien.
// Ukjent kode faller til koden selv, aldri til en tom celle.
export function planEtikett(kode) {
  const k = String(kode || "").trim();
  return k ? t(`site.plan.${k}`, k) : "";
}

// Modultildelingen for ÉN tenant, utledet av modul-ID-ene den AUTENTISERTE
// veien oppga. `null` betyr «vet ikke», ikke «ingen moduler»: en flate som
// ikke vet, skal si det — ikke vise hele plattformkatalogen som om den var
// kundens. Selve tildelingen er tenantdata og kommer derfor utenfra, ikke fra
// en tabell i denne fila.
export function modulerFraIder(ider) {
  if (!Array.isArray(ider)) return null;
  return MODULOVERSIKT.filter((mod) => ider.includes(mod.id));
}

// Tenantens egne tall. `planlagt` er plattformmodulene kunden ennå ikke har
// fått aktivert — ikke plattformens globale restliste.
export function tenantTelling(moduler) {
  const tell = (s) => moduler.filter((m) => m.status === s).length;
  const iDrift = tell("i_drift");
  const klargjort = tell("klargjort");
  const bygges = tell("bygges");
  const totalt = KATALOG_TOTALT;
  return { iDrift, klargjort, bygges, underArbeid: klargjort + bygges,
    planlagt: totalt - moduler.length, totalt };
}

//: Modulkatalogen slik produktplanen beskriver den. Bare de fire i `MODULER`
//: har en tilstand ennå; resten er `planlagt`.
const KATALOG_TOTALT = 45;

export function plattformTelling() {
  const tell = (s) => Object.values(MODULSTATUS).filter((v) => v === s).length;
  const iDrift = tell("i_drift");
  const klargjort = tell("klargjort");
  const bygges = tell("bygges");
  return { iDrift, klargjort, bygges, underArbeid: klargjort + bygges,
    planlagt: KATALOG_TOTALT - iDrift - klargjort - bygges,
    totalt: KATALOG_TOTALT };
}
