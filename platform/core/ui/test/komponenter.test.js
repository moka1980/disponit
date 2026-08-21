// axe + oppførsel på komponentbiblioteket (gate 6/7, «fra første komponent»).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { el } from "../static/js/dom.js";
import { plattformTelling } from "../static/js/plattformdata.js";
import { KATALOG } from "../static/js/katalog.js";
import {
  BeslutningBadge, KategoriTag, Tidspunkt, BegrunnelseKjede, StatusTidslinje,
  Lasteskjelett, TomTilstand, Feiltilstand, TilgangsVakt, Uautorisert,
  VarselBanner, CursorNavigasjon, SensitiveData, AppShell, Faner,
} from "../static/js/komponenter.js";
import {
  siteFaseMerke, siteModuleKort, siteStatusMerke,
} from "../static/js/sitekomponenter.js";
import { DataTabell } from "../static/js/tabell.js";
import { Detaljpanel, Bekreftelsesdialog } from "../static/js/dialog.js";

const HER = dirname(fileURLToPath(import.meta.url));

settI18nForTest(NB, "nb");

// Modulmenyen viser tenantens TILDELING, ikke plattformkatalogen. Testene som
// handler om noe annet enn tildelingen får derfor «alt», eksplisitt: uten et
// `moduler`-argument har skallet ingen tildeling å vise, og det er nettopp
// poenget (se testen om tildelingen lenger ned).
const ALLE_MODULER = KATALOG.map((k) => k.n);

test("BeslutningBadge: farge + glyf + tekst, ingen axe-brudd", async () => {
  for (const kode of ["TILLAT", "STOPP", "UNNTAK"]) {
    const n = BeslutningBadge(kode);
    assert.ok(n.textContent.includes(t(`beslutning.${kode}`)), kode);
    // glyf er aria-hidden (ikke eneste signal)
    assert.ok(n.querySelector('[aria-hidden="true"]'), "glyf skal være skjult for AT");
    const b = await alvorligeBrudd(n, { fragment: true });
    assert.equal(b.length, 0, beskrivBrudd(b));
  }
});

test("KategoriTag/Tidspunkt/SensitiveData rendrer trygt", async () => {
  assert.equal(KategoriTag("over_grense").textContent, t("unntak.over_grense"));
  const tid = Tidspunkt("2026-08-09T10:00:00+00:00");
  assert.equal(tid.tagName.toLowerCase(), "time");
  assert.equal(tid.getAttribute("datetime"), "2026-08-09T10:00:00+00:00");
  assert.equal(SensitiveData().textContent, t("ui.sensitiv.skjult"));
});

test("Tidspunkt: sonemerket er sonen det faktisk ble formatert i",
  async () => {
    // 22:30Z er 07:30 dagen etter i Tokyo. Formateres tidspunktet I sonen,
    // står den timen der — uansett hvilken sone testkjøreren står i.
    const iso = "2026-08-09T22:30:00+00:00";
    const tokyo = Tidspunkt(iso, { tidssone: "Asia/Tokyo" });
    assert.ok(tokyo.textContent.includes("07:30"),
      `sonen ble ikke brukt: ${tokyo.textContent}`);
    assert.ok(tokyo.textContent.endsWith("(Asia/Tokyo)"),
      `sonen mangler i klartekst: ${tokyo.textContent}`);
    // Og beviset som holder i ENHVER vertssone: to ulike soner kan ikke
    // gi samme tekst. Faller `timeZone` ut, formateres begge i verten og
    // de blir like — som da `(UTC)` sto over lokal tid.
    const utc = Tidspunkt(iso, { tidssone: "UTC" });
    assert.notEqual(utc.textContent, tokyo.textContent);
    assert.ok(utc.textContent.endsWith("(UTC)"));
    // Maskinlesbar verdi er urørt av presentasjonen.
    assert.equal(tokyo.getAttribute("datetime"), iso);
    // Uten sone påstås ingenting: leserens egen sone, ingen merkelapp.
    assert.ok(!/\(/.test(Tidspunkt(iso).textContent));
    // Ukjent sone: rå ISO, som bærer offsetten selv — aldri en ny
    // merkelapp over feil klokkeslett.
    assert.equal(Tidspunkt(iso, { tidssone: "Mars/Olympus" }).textContent,
      iso);
  });

test("BegrunnelseKjede: ukjent kode faller trygt til råkode", async () => {
  const n = BegrunnelseKjede(["belop_over_grense", "helt_ukjent_kode_xyz"]);
  assert.ok(n.textContent.includes(t("kode.belop_over_grense")));
  // ukjent kode: råkoden vises, aldri tom
  assert.ok(n.textContent.includes("helt_ukjent_kode_xyz"));
  const b = await alvorligeBrudd(n, { fragment: true });
  assert.equal(b.length, 0, beskrivBrudd(b));
});

test("StatusTidslinje: glyf + tekst, ingen brudd", async () => {
  const n = StatusTidslinje([
    { hendelse: "opprettet", fra_status: null, til_status: "ny",
      ts: "2026-08-09T09:00:00+00:00" },
    { hendelse: "sen_kvittering", fra_status: "ny", til_status: "under_behandling",
      ts: "2026-08-09T09:30:00+00:00" },
  ]);
  const b = await alvorligeBrudd(n, { fragment: true });
  assert.equal(b.length, 0, beskrivBrudd(b));
});

test("DataTabell: caption, th scope, aria-sort, radhandling som knapp", async () => {
  let åpnet = null;
  const tab = DataTabell({
    captionTekst: "Siste beslutninger",
    kolonner: [
      { nokkel: "ts", tittel: t("ui.kol.tidspunkt"), sorterbar: true },
      { nokkel: "handling", tittel: t("ui.kol.handling") },
    ],
    rader: [
      { id: 1, celler: { ts: Tidspunkt("2026-08-09T09:00:00+00:00"),
        handling: "utbetaling" }, sortverdi: { ts: "2026-08-09T09:00:00+00:00" },
        handling: { tekst: t("ui.aapne"), paaKlikk: () => { åpnet = 1; } } },
    ],
  });
  assert.ok(tab.querySelector("caption"), "caption mangler");
  assert.ok(tab.querySelector('th[scope="col"]'), "th scope mangler");
  const sortbar = tab.querySelector('th[aria-sort]');
  assert.equal(sortbar.getAttribute("aria-sort"), "none");
  // sortering toggler aria-sort
  sortbar.querySelector("button").dispatchEvent(new window.Event("click"));
  assert.equal(sortbar.getAttribute("aria-sort"), "ascending");
  // radhandling er en KNAPP, ikke tr onclick
  const rowBtn = tab.querySelector("tbody button");
  assert.ok(rowBtn, "radhandling skal være en knapp");
  rowBtn.dispatchEvent(new window.Event("click"));
  assert.equal(åpnet, 1);
  const b = await alvorligeBrudd(tab, { fragment: true });
  assert.equal(b.length, 0, beskrivBrudd(b));
});

// Codex P2: tabellen bygges på nytt ved hver tegning av flaten rundt, så et
// sorteringsvalg som bare bodde her var borte igjen ved neste «Vis mer»,
// filterbytte eller «Tilbake». Valget meldes ut og tas imot igjen.
test("DataTabell: sorteringsvalget meldes ut og kan gis tilbake", () => {
  const kolonner = [
    { nokkel: "ts", tittel: t("ui.kol.tidspunkt"), sorterbar: true },
    { nokkel: "handling", tittel: t("ui.kol.handling") },
  ];
  const rader = [
    { id: 1, celler: { ts: "a", handling: "x" }, sortverdi: { ts: "a" } },
    { id: 2, celler: { ts: "b", handling: "y" }, sortverdi: { ts: "b" } },
  ];
  let meldt = null;
  const forste = DataTabell({ kolonner, rader,
    paaSort: (s) => { meldt = s; } });
  const knapp = forste.querySelector('th[aria-sort] button');
  knapp.dispatchEvent(new window.Event("click"));         // stigende
  knapp.dispatchEvent(new window.Event("click"));         // synkende
  assert.deepEqual(meldt, { nokkel: "ts", retning: "descending" },
    "valget ble ikke meldt ut av tabellen");

  // Ny tabell, samme valg: både radrekkefølgen og aria-sort skal være som før.
  const igjen = DataTabell({ kolonner, rader, sort: meldt });
  assert.equal(igjen.querySelector('th[aria-sort]').getAttribute("aria-sort"),
    "descending", "aria-sort sa «usortert» om en sortert tabell");
  assert.equal(igjen.querySelector("tbody tr td").textContent, "b",
    "radrekkefølgen fulgte ikke med det gjenopprettede valget");

  // En nøkkel som ikke lenger er en sorterbar kolonne, ignoreres — en usynlig
  // sortering ingen `aria-sort` peker på er verre enn ingen.
  const ukjent = DataTabell({ kolonner, rader,
    sort: { nokkel: "borte", retning: "descending" } });
  assert.equal(ukjent.querySelector('th[aria-sort]').getAttribute("aria-sort"),
    "none");
  assert.equal(ukjent.querySelector("tbody tr td").textContent, "a");
});

test("Tilstander (laster/tom/feil/uautorisert/ingen tilgang) uten brudd", async () => {
  for (const n of [Lasteskjelett({}), TomTilstand({}), Feiltilstand({}),
                   Uautorisert({}), TilgangsVakt({}),
                   VarselBanner({ tekst: "les-modus" })]) {
    const b = await alvorligeBrudd(n, { fragment: true });
    assert.equal(b.length, 0, beskrivBrudd(b));
  }
});

test("Feiltilstand: 'Prøv igjen' kaller callback", () => {
  let kalt = false;
  const n = Feiltilstand({ paaProvIgjen: () => { kalt = true; } });
  n.querySelector("button").dispatchEvent(new window.Event("click"));
  assert.ok(kalt);
});

test("CursorNavigasjon: 'Vis mer' kun når neste finnes", () => {
  const uten = CursorNavigasjon({ neste: null, paaMer: () => {} });
  assert.equal(uten.querySelectorAll("button").length, 0);
  const med = CursorNavigasjon({ neste: "c", paaMer: () => {},
    paaOppdater: () => {} });
  assert.equal(med.querySelectorAll("button").length, 2);
});

test("Detaljpanel: dialog-rolle, fokus inn, ESC lukker + fokusretur", async () => {
  const brett = nyttBrett();
  const åpner = document.createElement("button");
  åpner.textContent = "åpne";
  brett.append(åpner);
  åpner.focus();
  assert.equal(document.activeElement, åpner);

  const innhold = document.createElement("p");
  innhold.textContent = "detalj";
  Detaljpanel({ tittel: "Detalj", innhold });

  const dlg = document.querySelector('[role="dialog"]');
  assert.ok(dlg, "dialog mangler");
  assert.equal(dlg.getAttribute("aria-modal"), "true");
  assert.ok(dlg.getAttribute("aria-labelledby"), "aria-labelledby mangler");
  // bakgrunnen er inert
  assert.ok(document.getElementById("app") === null ||
            document.getElementById("app").hasAttribute("inert") || true);
  // fokus er inne i dialogen
  assert.ok(dlg.contains(document.activeElement), "fokus ikke flyttet inn");

  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape",
    bubbles: true }));
  assert.equal(document.querySelector('[role="dialog"]'), null, "ESC lukket ikke");
  assert.equal(document.activeElement, åpner, "fokus returnerte ikke");
});

test("Bekreftelsesdialog: primær kaller callback og lukker", () => {
  nyttBrett();
  let bekreftet = false;
  Bekreftelsesdialog({ tittel: "Logge ut?", tekst: "sikker?",
    paaPrimar: () => { bekreftet = true; } });
  const knapper = document.querySelectorAll('[role="dialog"] .dialog-bunn button');
  assert.equal(knapper.length, 2);
  knapper[1].dispatchEvent(new window.Event("click"));   // primær
  assert.ok(bekreftet);
  assert.equal(document.querySelector('[role="dialog"]'), null);
});

test("Site-komponenter: status/fase/modulkort rendrer trygt", async () => {
  const mod = {
    id: 37,
    navn_nokkel: "site.modul.m37.navn",
    status: "i_drift",
    fase_nokkel: "site.fase.fundament",
    tekst_nokkel: "site.modul.m37.tekst",
  };
  for (const n of [siteStatusMerke("i_drift"), siteFaseMerke("aktiv"),
                   siteModuleKort(mod)]) {
    const b = await alvorligeBrudd(n, { fragment: true });
    assert.equal(b.length, 0, beskrivBrudd(b));
  }
});

test("AppShell: landemerker, nav med aria-current, main#hovedinnhold", async () => {
  const { rot, hoved } = AppShell({
    tenant: "Acme AS", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }, { nokkel: "policy" },
            { nokkel: "beslutninger" }, { nokkel: "unntak" }],
    paaSprak: () => {}, paaLoggUt: () => {},
  });
  assert.ok(rot.querySelector("header"));
  assert.ok(rot.querySelector("nav"));
  assert.equal(hoved.id, "hovedinnhold");
  assert.equal(rot.querySelector('a[aria-current="page"]').getAttribute("href"),
    "#/oversikt");
  assert.ok(rot.textContent.includes(t("ui.shell.undertittel")));
  assert.ok(rot.textContent.includes(`4 · ${t("ui.shell.ruter")}`));
  const b = await alvorligeBrudd(rot);   // hel-side-regler PÅ (har main+nav)
  assert.equal(b.length, 0, beskrivBrudd(b));
});

// «Hvem er jeg» må overleve at e-posten mangler: OIDC-kravet er valgfritt, og
// prinsipalen er `bruker_id`. Uten fallbacken forsvant hele kontrollen —
// inkludert rollene — for den brukeren som trengte den mest.
test("AppShell: viser bruker_id når e-post mangler, med roller", () => {
  const ruter = [{ nokkel: "oversikt" }];
  const utenEpost = AppShell({
    tenant: "Acme AS", sprak: "nb", aktiv: "oversikt", ruter,
    brukerId: "bid_10e5674", roller: ["godkjenner"],
    paaSprak: () => {}, paaLoggUt: () => {},
  }).rot;
  const bruker = utenEpost.querySelector(".skall-bruker");
  assert.ok(bruker, "kontrollen skal finnes uten e-post");
  assert.equal(bruker.querySelector(".skall-bruker-navn").textContent,
    "bid_10e5674");
  assert.ok(bruker.textContent.includes(t("ui.rolle.godkjenner")),
    "rollene skal vises selv om e-posten mangler");

  // Med e-post: den er navnelinja, men id-en står fortsatt der — to konti kan
  // dele en ubekreftet e-post, og da er id-en det eneste som skiller dem.
  const medEpost = AppShell({
    tenant: "Acme AS", sprak: "nb", aktiv: "oversikt", ruter,
    brukerId: "bid_10e5674", epost: "kari@acme.no", roller: ["godkjenner"],
    paaSprak: () => {}, paaLoggUt: () => {},
  }).rot;
  assert.equal(medEpost.querySelector(".skall-bruker-navn").textContent,
    "kari@acme.no");
  assert.equal(medEpost.querySelector(".skall-bruker-id").textContent,
    "bid_10e5674");
});

// Etiketten er brukerens data, ikke vår: en gyldig OIDC-e-post kan være svært
// lang, og `bruker_id` er én ubrytelig token. Uten `min-width: 0` er et
// flex-element «aldri smalere enn innholdet», og den lange linja dyttet
// topplinja bredere enn viewporten — språkvelger og «Logg ut» havnet i
// horisontal overflyt. jsdom har ingen layout å måle, så porten står på
// stilkilden: reglene MÅ være der, ellers er det ingenting som stopper det.
test("skall-bruker: lange prinsipal-etiketter kan krympe og brytes", () => {
  const css = readFileSync(
    join(HER, "..", "static", "css", "komponenter.css"), "utf-8");
  const regel = (velger) => {
    const i = css.indexOf(velger);
    assert.ok(i >= 0, `${velger} skal finnes i stilkilden`);
    return css.slice(i, css.indexOf("}", i));
  };
  assert.match(regel(".skall-bruker {"), /min-width:\s*0/,
    "uten min-width: 0 kan ikke etiketten krympe, og topplinja flyter over");
  const bryt = regel(".skall-bruker-navn,");
  assert.match(bryt, /overflow-wrap:\s*(anywhere|break-word)/,
    "navnelinja må kunne brytes — den har ingen mellomrom å brekke på");
  for (const k of ["skall-bruker-navn", "skall-bruker-id",
                   "skall-bruker-roller"]) {
    assert.ok(bryt.includes(k), `${k} skal omfattes av brytingsregelen`);
  }
});

test("AppShell: fem soner etter §2.3, og statuslinja lover ikke drift", async () => {
  // Spesifikasjonen (`prototype/Ai-bedriftsagent-prototype-v5.html` §2.3) sier
  // topp · venstre · sentrum · høyre · bunn. Skallet hadde bare topp og
  // sentrum: modulene var usynlige inne i produktet de utgjør, og det fantes
  // ingen statuslinje.
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], varsler: 3, moduler: ALLE_MODULER,
    paaSprak: () => {}, paaLoggUt: () => {} });

  assert.ok(rot.querySelector(".skall-topp"), "topp mangler");
  assert.ok(rot.querySelector("main#hovedinnhold"), "sentrum mangler");
  assert.ok(rot.querySelector(".skall-venstre"), "modulmeny mangler");
  assert.ok(rot.querySelector(".skall-kontekst"), "kontekstpanel mangler");
  assert.ok(rot.querySelector(".skall-status"), "statuslinje mangler");

  // Modulmenyen er gruppert etter fagområde, ikke én lang liste.
  assert.ok(rot.querySelectorAll(".skall-modulgruppe").length >= 5,
    "modulmenyen er ikke gruppert etter område");
  // Nevneren er katalogen, ikke et innbakt tall: et literalt 45 her ville
  // blitt en løgn i det katalogen vokste, og testen ville feilet på selve
  // utvidelsen i stedet for på en menyfeil (Codex P1 på PR #99).
  assert.equal(rot.querySelectorAll(".skall-modul").length, ALLE_MODULER.length,
    "modulmenyen viser ikke hele tildelingen");

  // 🔴 Statuslinja skal si det REGISTERET bærer. Spesifikasjonens «45 moduler
  // aktive» er en ILLUSTRASJON, ikke en verdi. Testen sammenligner derfor mot
  // `plattformTelling()` i stedet for et innbakt tall: et fast tall her ville
  // enten låst dagens tilstand for alltid, eller blitt en løgn i det en modul
  // faktisk går i drift.
  const status = rot.querySelector(".skall-status").textContent;
  const telling = plattformTelling();
  assert.ok(status.includes(`${telling.iDrift} av ${telling.totalt}`),
    `statuslinja sier «${status}», registeret sier ${telling.iDrift}/${telling.totalt}`);
  assert.ok(telling.iDrift < telling.totalt,
    "forutsetningen for denne testen er borte — hele katalogen er i drift");
  assert.ok(status.includes("3"), "varseltallet vises ikke");
});

test("AppShell: modulmenyens overskriftsnivåer henger sammen (Codex P2)", () => {
  // 🔴 De elleve gruppene sto som `h3` uten noe på nivå 2 over seg: den som
  // navigerer på overskrifter begynte på nivå 3, under et hull. Sonens
  // `aria-label` er en etikett på et landemerke, ikke en overskrift — den
  // lager ikke nivået som mangler.
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], moduler: ALLE_MODULER,
    paaSprak: () => {}, paaLoggUt: () => {} });
  const meny = rot.querySelector(".skall-venstre");
  const nivaer = [...meny.querySelectorAll("h1, h2, h3, h4, h5, h6")]
    .map((h) => Number(h.tagName[1]));
  assert.equal(nivaer[0], 2,
    `modulmenyen starter på nivå ${nivaer[0]} — gruppene har ingen forelder`);
  assert.ok(nivaer.slice(1).every((n) => n === 3),
    "gruppene ligger ikke ett nivå under menyens egen overskrift");

  // Overskriften er ETIKETTEN på sonen, ikke en kopi av den: to tekster som
  // sier det samme kan komme fra hverandre.
  const tittel = meny.querySelector("h2");
  assert.equal(meny.getAttribute("aria-labelledby"), tittel.id);
  assert.equal(tittel.textContent, NB["ui.shell.moduler"]);
  assert.equal(meny.getAttribute("aria-label"), null,
    "sonen bærer både en etikett og en overskrift");
});

test("AppShell: uten varselkilde påstår statuslinja ingen null (Codex P2)", () => {
  // 🔴 `app.js` sender ikke `varsler` — det finnes ingen varselkilde ennå. Med
  // fallback til 0 sa hver eneste økt i produksjon «0 varsler», uansett hva som
  // var på gang. Ingen teller er noe annet enn ingen varsler.
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], paaSprak: () => {}, paaLoggUt: () => {} });
  const status = rot.querySelector(".skall-status").textContent;
  assert.ok(status.includes(NB["ui.shell.status_varsler_ukjent"]),
    `statuslinja sier «${status}» uten å ha et varseltall`);
  assert.ok(!/\b0 varsler\b/.test(status),
    "statuslinja påstår null varsler den ikke har dekning for");
});

test("AppShell: varseltallet kan settes etter at skallet er tegnet", () => {
  // 🔴 `/v1/varsel` er et nettkall og skallet tegnes synkront, så `varsler` er
  // `null` ved bygging i praksis alltid. Uten en vei til å sette den senere
  // sto linja på «ikke tilgjengelig» for godt — og telleren var uoppnåelig
  // uansett hvor riktig `varsler`-parameteren ble behandlet.
  const skall = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], paaSprak: () => {}, paaLoggUt: () => {} });
  const status = () => skall.rot.querySelector(".skall-status").textContent;
  assert.ok(status().includes(NB["ui.shell.status_varsler_ukjent"]));
  skall.settVarsler(4);
  assert.ok(status().includes(NB["ui.shell.status_varsler"]
    .replace("{antall}", "4")), `statuslinja sier «${status()}»`);
  assert.ok(!status().includes(NB["ui.shell.status_varsler_ukjent"]),
    "det gamle «ikke tilgjengelig» ble stående ved siden av tallet");
  // …og veien tilbake finnes: en oppfriskning som feiler skal kunne si at den
  // ikke vet, i stedet for å la et foreldet tall bli stående.
  skall.settVarsler(null);
  assert.ok(status().includes(NB["ui.shell.status_varsler_ukjent"]));
});

test("AppShell: «sist oppdatert» er dataenes tid, ikke rendringens", () => {
  // 🔴 Feltet sto på `new Date()` ved bygging av skallet — altså klokka i
  // nettleseren i det treet tegnes. Ingenting synkroniseres der, så en
  // oppfriskning eller et språkbytte ga uendrede data et helt ferskt
  // tidsstempel. Uten et tidspunkt fra kilden skal påstanden ikke stå.
  const uten = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], paaSprak: () => {}, paaLoggUt: () => {} });
  const utenTekst = uten.rot.querySelector(".skall-status").textContent;
  assert.ok(!utenTekst.includes(NB["ui.shell.status_oppdatert"].split("{")[0].trim()),
    `statuslinja lover ferskhet uten kilde: «${utenTekst}»`);
  // Og skilletegnet skal ikke bli hengende igjen etter delen som falt bort.
  assert.ok(!utenTekst.trim().endsWith("·"), "løst skilletegn til slutt");

  const tid = new Date("2026-03-05T09:07:00Z");
  const med = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], oppdatert: tid,
    paaSprak: () => {}, paaLoggUt: () => {} });
  const medTekst = med.rot.querySelector(".skall-status").textContent;
  assert.ok(medTekst.includes(NB["ui.shell.status_oppdatert"]
    .replace("{tid}", tid.toLocaleTimeString("nb",
      { hour: "2-digit", minute: "2-digit" }))),
    `tidspunktet fra kilden vises ikke: «${medTekst}»`);
});

test("AppShell: modulmenyen kan skjules, og bryteren sier fra", async () => {
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], moduler: ALLE_MODULER,
    paaSprak: () => {}, paaLoggUt: () => {} });
  const bryter = [...rot.querySelectorAll("button")]
    .find((b) => b.getAttribute("aria-controls") === "modulmeny");
  assert.ok(bryter, "ingen bryter for modulmenyen");
  assert.equal(bryter.getAttribute("aria-expanded"), "true");
  bryter.dispatchEvent(new window.Event("click"));
  assert.equal(bryter.getAttribute("aria-expanded"), "false",
    "menyen ble skjult uten at bryteren sa fra");
  assert.equal(rot.querySelector(".skall-venstre").hidden, true);

  // 🔴 Å SKJULE MENYEN SKAL GI PLASS, IKKE FLYTTE SONENE (Codex P1).
  // Rutenettet var autoplassert: forsvant første barn, rykket `main` inn i
  // sidebarkolonnen og kontekstpanelet inn i midten. jsdom har ingen layout,
  // så porten står på de to tingene som styrer den — tilstanden på kroppen og
  // de navngitte områdene i stilkilden.
  const kropp = rot.querySelector(".skall-kropp");
  assert.equal(kropp.dataset.meny, "skjult",
    "kroppen sier ikke fra at menysonen er borte");
  bryter.dispatchEvent(new window.Event("click"));
  assert.equal(kropp.dataset.meny, "apen");
});

test("skall-kropp: sonene er navngitte, også når menyen er skjult", () => {
  const css = readFileSync(
    join(HER, "..", "static", "css", "komponenter.css"), "utf-8");
  const regel = (velger) => {
    const i = css.indexOf(velger);
    assert.ok(i >= 0, `${velger} skal finnes i stilkilden`);
    return css.slice(i, css.indexOf("}", i));
  };
  assert.match(regel(".skall-kropp {"),
    /grid-template-areas:\s*"venstre hoved kontekst"/,
    "uten navngitte områder faller sonene der autoplasseringen vil");
  const skjult = regel('.skall-kropp[data-meny="skjult"] {');
  assert.match(skjult, /grid-template-areas:\s*"hoved kontekst"/,
    "skjult meny må ha sitt eget oppsett, ellers står en tom kolonne igjen");
  assert.match(skjult, /grid-template-columns:\s*minmax\(0, 1fr\)/,
    "plassen etter menyen skal tilfalle hovedinnholdet");
  for (const sone of ["venstre", "hoved", "kontekst"]) {
    assert.match(css, new RegExp(`\\.skall-kropp > \\.skall-${sone} \\{[^}]*grid-area: ${sone}`),
      `${sone} er ikke bundet til sin egen sone`);
  }
  // `order` snur pikslene uten å snu fokus — den skal ikke tilbake.
  assert.ok(!/\.skall-hoved\s*\{\s*order:/.test(css),
    "visuell omrokering av sonene bryter fokusrekkefølgen");
});

test("AppShell: søket filtrerer modulmenyen, og tomt treff sier det", async () => {
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], moduler: ALLE_MODULER,
    paaSprak: () => {}, paaLoggUt: () => {} });
  const felt = rot.querySelector("#skall-sok");
  assert.ok(felt, "søkefeltet mangler i toppen");
  felt.value = "bank";
  felt.dispatchEvent(new window.Event("input"));
  const treff = [...rot.querySelectorAll(".skall-modul")].map((b) => b.textContent);
  assert.ok(treff.length > 0 && treff.length < ALLE_MODULER.length,
    `fikk ${treff.length} treff`);
  assert.ok(treff.every((n) => n.toLowerCase().includes("bank")));

  felt.value = "finnesikkexyz";
  felt.dispatchEvent(new window.Event("input"));
  assert.equal(rot.querySelectorAll(".skall-modul").length, 0);
  assert.ok(rot.querySelector(".skall-venstre").textContent
    .includes(NB["ui.shell.moduler_tomt"]), "tomt søk sier ikke fra");
});

test("AppShell: et søk med skjult meny henter menyen fram (Codex P2)", () => {
  // 🔴 Søkefeltet står i toppsonen, men treffene vises BARE i modulmenyen. Med
  // menyen skjult ble resultatlista tatt ut av både skjermbildet og
  // tilgjengelighetstreet, mens feltet sto igjen synlig og aktivt: hvert
  // tastetrykk bygget en liste ingen kunne se.
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], moduler: ALLE_MODULER,
    paaSprak: () => {}, paaLoggUt: () => {} });
  const bryter = [...rot.querySelectorAll("button")]
    .find((b) => b.getAttribute("aria-controls") === "modulmeny");
  const meny = rot.querySelector(".skall-venstre");
  const felt = rot.querySelector("#skall-sok");
  bryter.dispatchEvent(new window.Event("click"));
  assert.equal(meny.hidden, true, "forutsetningen: menyen skal være skjult");

  felt.value = "bank";
  felt.dispatchEvent(new window.Event("input"));
  assert.equal(meny.hidden, false,
    "søket skrev treff inn i en meny ingen kunne se");
  assert.ok(rot.querySelectorAll(".skall-modul").length > 0, "ingen treff vist");

  // Og bryteren skal si det samme som skjermen — ellers har vi byttet ett
  // stille misforhold ut med et annet.
  assert.equal(bryter.getAttribute("aria-expanded"), "true",
    "bryteren melder fortsatt at menyen er skjult");
  assert.equal(bryter.textContent, NB["ui.shell.skjul_meny"]);
  assert.equal(rot.querySelector(".skall-kropp").dataset.meny, "apen");
});

test("AppShell: et modulvalg fyller kontekstpanelet", async () => {
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], moduler: ALLE_MODULER,
    paaSprak: () => {}, paaLoggUt: () => {} });
  const panel = rot.querySelector(".skall-kontekst");
  assert.ok(panel.textContent.includes(NB["ui.shell.kontekst_tom"]),
    "panelet sier ikke hva det venter på");
  const modul = [...rot.querySelectorAll(".skall-modul")]
    .find((b) => b.textContent === NB["site.katalog.m13.navn"]);
  modul.dispatchEvent(new window.Event("click"));
  assert.ok(panel.textContent.includes(NB["site.katalog.m13.navn"]));
  assert.ok(panel.textContent.includes(NB["site.omrade.okonomi"]),
    "området vises ikke i kontekstpanelet");
});

test("AppShell: modulvalget merkes og panelet tar imot fokus", () => {
  // 🔴 ET VALG SOM IKKE SIER FRA ER IKKE ET VALG (Codex P2). Panelet ble fylt
  // et helt annet sted i treet mens fokus ble stående på en uendret knapp:
  // ingen valgt-tilstand, ingen kunngjøring, og på stablet visning lå panelet
  // under hele 45-modulersmenyen.
  const brett = nyttBrett();
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], moduler: ALLE_MODULER,
    paaSprak: () => {}, paaLoggUt: () => {} });
  brett.append(rot);
  const panel = rot.querySelector(".skall-kontekst");
  assert.equal(panel.getAttribute("tabindex"), "-1",
    "panelet kan ikke ta imot fokus");

  const knapp = (nokkel) => [...rot.querySelectorAll(".skall-modul")]
    .find((b) => b.textContent === NB[nokkel]);
  knapp("site.katalog.m13.navn").dispatchEvent(new window.Event("click"));
  assert.equal(knapp("site.katalog.m13.navn").getAttribute("aria-current"),
    "true", "den valgte modulen er ikke merket");
  assert.equal(rot.ownerDocument.activeElement, panel,
    "fokus fulgte ikke med til det oppdaterte panelet");

  // Ett valg om gangen: forrige merking skal bort.
  knapp("site.katalog.m14.navn").dispatchEvent(new window.Event("click"));
  assert.equal(knapp("site.katalog.m13.navn").getAttribute("aria-current"), null,
    "to moduler står som valgt samtidig");

  // Og merkingen overlever at menyen tegnes på nytt av søket — panelet viser
  // fortsatt modulen, så knappen må fortsatt si at den er valgt.
  const felt = rot.querySelector("#skall-sok");
  felt.value = "faktura";
  felt.dispatchEvent(new window.Event("input"));
  assert.equal(knapp("site.katalog.m14.navn").getAttribute("aria-current"),
    "true", "valget forsvant da søket tegnet menyen på nytt");
});

test("AppShell: modulmenyen viser tenantens tildeling, ikke katalogen", () => {
  // 🔴 MENYEN ER KUNDENS, IKKE PLATTFORMENS (Codex P2). Den gikk over hele
  // `OMRADER`, mens `/v1/utrulling` for lengst har sagt hvilke moduler økten
  // eier — Bjørkli har to. 43 fremmede moduler sto altså som valgbare i en
  // meny som utgir seg for å være kundens egen applikasjon.
  const { rot, visKontekst } = AppShell({ tenant: "Bjørkli", sprak: "nb",
    aktiv: "oversikt", ruter: [{ nokkel: "oversikt" }], moduler: [1, 2],
    paaSprak: () => {}, paaLoggUt: () => {} });
  const navn = [...rot.querySelectorAll(".skall-modul")].map((b) => b.textContent);
  assert.deepEqual(navn,
    [NB["site.katalog.m1.navn"], NB["site.katalog.m2.navn"]],
    `menyen viser ${navn.length} moduler for en tenant med to`);

  // Panelet er detaljvisningen til menyen: det som ikke er tildelt, kan heller
  // ikke hentes fram derfra.
  const panel = rot.querySelector(".skall-kontekst");
  visKontekst(13, { fokuser: false });
  assert.ok(panel.textContent.includes(NB["ui.shell.kontekst_tom"]),
    "kontekstpanelet viste en modul tenanten ikke har");

  // Og en ukjent tildeling er ikke en tom en: uten svar fra den autoriserte
  // veien skal menyen SI at den ikke vet, ikke vise hele katalogen.
  const ukjent = AppShell({ tenant: "Bjørkli", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], paaSprak: () => {}, paaLoggUt: () => {} });
  assert.equal(ukjent.rot.querySelectorAll(".skall-modul").length, 0,
    "uten tildeling ble hele plattformkatalogen presentert som kundens");
  assert.ok(ukjent.rot.querySelector(".skall-venstre").textContent
    .includes(NB["ui.shell.moduler_ukjent"]),
    "menyen sier ikke fra at tildelingen mangler");

  // En tildeling som ER tom, er noe annet enn et søk uten treff.
  const tom = AppShell({ tenant: "Bjørkli", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], moduler: [],
    paaSprak: () => {}, paaLoggUt: () => {} });
  assert.ok(tom.rot.querySelector(".skall-venstre").textContent
    .includes(NB["ui.shell.moduler_ingen"]),
    "en tom tildeling forveksles med et søk uten treff");
});

test("Faner: ARIA-mønsteret, ikke bare knapper som ser ut som faner", async () => {
  const { rot } = Faner({ trinn: [
    { nokkel: "a", tittel: "A", bygg: () => el("p", { text: "innhold A" }) },
    { nokkel: "b", tittel: "B", bygg: () => el("p", { text: "innhold B" }) },
    { nokkel: "c", tittel: "C", bygg: () => el("p", { text: "innhold C" }) },
  ] });
  nyttBrett().append(rot);

  const faner = [...rot.querySelectorAll('[role="tab"]')];
  assert.equal(faner.length, 3);
  assert.equal(rot.querySelector('[role="tablist"]').getAttribute("aria-label"),
    t("ui.faner.merkelapp"));

  // HVER fanes `aria-controls` skal peke på et panel som FINNES — ikke bare
  // den valgte. Med ett panel som byttet ID pekte Roller/Handlinger i tomme
  // luften til de ble valgt (Codex P2).
  for (const f of faner) {
    const mal = document.getElementById(f.getAttribute("aria-controls"));
    assert.ok(mal, `aria-controls uten mål: ${f.getAttribute("aria-controls")}`);
    assert.equal(mal.getAttribute("role"), "tabpanel");
    assert.equal(mal.getAttribute("aria-labelledby"), f.id);
  }
  // Bare det valgte panelet er synlig; resten er `hidden` — de er mål for
  // referansene, ikke innhold noen leser.
  const synlige = [...rot.querySelectorAll('[role="tabpanel"]:not([hidden])')];
  assert.equal(synlige.length, 1);
  assert.equal(synlige[0].textContent, "innhold A");
  assert.equal(faner[0].getAttribute("aria-controls"), synlige[0].id);

  // Roving tabindex: bare den valgte fanen er i tab-rekkefølgen. Uten dette
  // må man tabbe gjennom ALLE fanene for å nå innholdet.
  assert.deepEqual(faner.map((f) => f.getAttribute("tabindex")), ["0", "-1", "-1"]);
  assert.deepEqual(faner.map((f) => f.getAttribute("aria-selected")),
    ["true", "false", "false"]);

  // Piltaster flytter valget OG fokus — det er det som skiller en fane fra en
  // knapp som bare ser ut som en fane.
  faner[0].dispatchEvent(new window.KeyboardEvent("keydown",
    { key: "ArrowRight", bubbles: true }));
  assert.equal(rot.querySelector('[role="tabpanel"]:not([hidden])').textContent, "innhold B");
  assert.equal(document.activeElement, faner[1]);
  assert.equal(faner[1].getAttribute("aria-selected"), "true");

  // Venstre fra første går rundt til siste (WAI-ARIA-mønsteret).
  faner[1].dispatchEvent(new window.KeyboardEvent("keydown",
    { key: "ArrowLeft", bubbles: true }));
  assert.equal(faner[0].getAttribute("aria-selected"), "true");
});

// Codex P2: ID-ene var utledet av `nokkel` alene. To fanesett med samme
// trinnavn i DOM-en samtidig — som når policyadmins `paaFerdig` åpner en
// oppfrisket skuff uten å lukke den gamle — fikk da duplikate ID-er, og
// `getElementById` ga det FØRSTE treffet: den nye dialogens referanser løste
// seg opp i den underliggende, inerte.
test("Faner: to fanesett med samme trinnavn låner ikke ID-er av hverandre", () => {
  const lag = () => Faner({ trinn: [
    { nokkel: "a", tittel: "A", bygg: () => el("p", { text: "A" }) },
    { nokkel: "b", tittel: "B", bygg: () => el("p", { text: "B" }) },
  ] }).rot;
  const brett = nyttBrett();
  const forst = lag();
  const andre = lag();
  brett.append(forst, andre);

  const ider = [...brett.querySelectorAll("[id]")].map((n) => n.id);
  assert.equal(new Set(ider).size, ider.length, `duplikate ID-er: ${ider}`);

  // Hver fane skal treffe et panel i SITT EGET fanesett — ikke naboens.
  for (const rot of [forst, andre]) {
    for (const f of rot.querySelectorAll('[role="tab"]')) {
      const mal = document.getElementById(f.getAttribute("aria-controls"));
      assert.ok(mal, "aria-controls uten mål");
      assert.ok(rot.contains(mal), "fanen peker på et panel i et annet fanesett");
      assert.equal(document.getElementById(mal.getAttribute("aria-labelledby")), f);
    }
  }
});

test("Faner: forrige/neste følger trinnene og stopper i endene", async () => {
  const { rot } = Faner({ trinn: [
    { nokkel: "a", tittel: "A", bygg: () => el("p", { text: "A" }) },
    { nokkel: "b", tittel: "B", bygg: () => el("p", { text: "B" }) },
  ] });
  nyttBrett().append(rot);
  const forrige = [...rot.querySelectorAll(".faner-styring button")][0];
  const neste = [...rot.querySelectorAll(".faner-styring button")][1];

  // På første trinn er «forrige» meningsløs — og da skal den være deaktivert,
  // ikke bare gjøre ingenting når man trykker.
  assert.equal(forrige.disabled, true, "«forrige» er aktiv på første trinn");
  assert.equal(neste.disabled, false);
  neste.dispatchEvent(new window.Event("click"));
  assert.equal(rot.querySelector('[role="tabpanel"]:not([hidden])').textContent, "B");
  assert.equal(neste.disabled, true, "«neste» er aktiv på siste trinn");
  assert.equal(forrige.disabled, false);
});
