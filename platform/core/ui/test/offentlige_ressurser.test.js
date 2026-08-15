// Port: INGEN TENANTDATA I DE ANONYMT NEDLASTBARE RESSURSENE.
//
// `/ui/{sti}` og `/ui/locale/{sprak}` serveres uten sesjonssjekk — den anonyme
// landingssiden trenger dem — så alt som ligger i klientpakken eller i
// locale-settet kan hentes med en `curl` uten cookie. En scope-sjekk før
// RENDRING hjelper ikke: filen er allerede lastet ned.
//
// Testen finnes fordi presset går den andre veien. Locale-kontrakten sier at
// all synlig tekst skal komme fra `locales/`, og den letteste måten å oppfylle
// den på når en tabell skal vise et kundenavn, er å legge kundenavnet inn som
// en oversettelsesnøkkel. Da er lekkasjen tilbake, og den ser ryddig ut.
// Skillet er: chrome-tekst (overskrifter, kolonnenavn, statusetiketter) er
// oversettelser; kundenavn, planer, modultildelinger og «neste steg» er DATA
// og skal hentes fra en autentisert, server-autorisert vei.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HER = path.dirname(fileURLToPath(import.meta.url));
const UI = path.join(HER, "..");
const ROT = path.join(UI, "..", "..", "..");

function jsFiler(katalog) {
  return fs.readdirSync(katalog, { withFileTypes: true }).flatMap((e) => {
    const full = path.join(katalog, e.name);
    if (e.isDirectory()) return jsFiler(full);
    return e.name.endsWith(".js") ? [full] : [];
  });
}

test("locale-settet bærer ingen tenantnøkler", () => {
  for (const sprak of ["nb", "en"]) {
    const fil = path.join(ROT, "locales", `${sprak}.json`);
    const data = JSON.parse(fs.readFileSync(fil, "utf8"));
    const tenantnokler = Object.keys(data).filter((k) =>
      k.startsWith("site.tenant.") || /^ui\.tenant\./.test(k));
    assert.deepEqual(tenantnokler, [],
      `${sprak}.json bærer tenantdata: ${tenantnokler.join(", ")}`);
  }
});

test("klientpakken eksporterer ikke et tenantregister", () => {
  const mistenkelig = /export\s+(const|let|var|function)\s+(TENANTOVERSIKT|TENANTER|KUNDEOVERSIKT|tenantRad|modulerForTenant)\b/;
  for (const fil of jsFiler(path.join(UI, "static", "js"))) {
    const kilde = fs.readFileSync(fil, "utf8");
    assert.ok(!mistenkelig.test(kilde),
      `${path.relative(UI, fil)} eksporterer et tenantregister — tenantdata ` +
      `hører hjemme bak en autentisert rute, ikke i en anonymt nedlastbar fil`);
  }
});

test("ingen organisasjonsformer i de offentlige ressursene", () => {
  // Et kundenavn i en norsk kontekst bærer nesten alltid en selskapsform.
  // Regelen fanger derfor gjenoppståtte navn selv om nøkkelen heter noe annet.
  const selskapsform = /\b(AS|ASA|ANS|DA|BA|SA|NUF|Ltd|GmbH|Inc)\b/;
  const filer = [
    ...jsFiler(path.join(UI, "static", "js")),
    path.join(ROT, "locales", "nb.json"),
    path.join(ROT, "locales", "en.json"),
  ];
  for (const fil of filer) {
    for (const [nr, linje] of fs.readFileSync(fil, "utf8").split("\n").entries()) {
      // Kommentarer beskriver regelen og skal kunne nevne den.
      const kode = linje.replace(/^\s*(\/\/|\*|\/\*).*$/, "");
      assert.ok(!selskapsform.test(kode),
        `${path.relative(ROT, fil)}:${nr + 1} ser ut til å bære et ` +
        `organisasjonsnavn i en offentlig ressurs: ${linje.trim()}`);
    }
  }
});
