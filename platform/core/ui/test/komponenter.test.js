// axe + oppførsel på komponentbiblioteket (gate 6/7, «fra første komponent»).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { plattformTelling } from "../static/js/plattformdata.js";
import {
  BeslutningBadge, KategoriTag, Tidspunkt, BegrunnelseKjede, StatusTidslinje,
  Lasteskjelett, TomTilstand, Feiltilstand, TilgangsVakt, Uautorisert,
  VarselBanner, CursorNavigasjon, SensitiveData, AppShell,
} from "../static/js/komponenter.js";
import {
  siteFaseMerke, siteModuleKort, siteStatusMerke,
} from "../static/js/sitekomponenter.js";
import { DataTabell } from "../static/js/tabell.js";
import { Detaljpanel, Bekreftelsesdialog } from "../static/js/dialog.js";

const HER = dirname(fileURLToPath(import.meta.url));

settI18nForTest(NB, "nb");

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
    ruter: [{ nokkel: "oversikt" }], varsler: 3,
    paaSprak: () => {}, paaLoggUt: () => {} });

  assert.ok(rot.querySelector(".skall-topp"), "topp mangler");
  assert.ok(rot.querySelector("main#hovedinnhold"), "sentrum mangler");
  assert.ok(rot.querySelector(".skall-venstre"), "modulmeny mangler");
  assert.ok(rot.querySelector(".skall-kontekst"), "kontekstpanel mangler");
  assert.ok(rot.querySelector(".skall-status"), "statuslinje mangler");

  // Modulmenyen er gruppert etter fagområde, ikke én lang liste.
  assert.ok(rot.querySelectorAll(".skall-modulgruppe").length >= 5,
    "modulmenyen er ikke gruppert etter område");
  assert.equal(rot.querySelectorAll(".skall-modul").length, 45,
    "modulmenyen viser ikke hele katalogen");

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

test("AppShell: modulmenyen kan skjules, og bryteren sier fra", async () => {
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], paaSprak: () => {}, paaLoggUt: () => {} });
  const bryter = [...rot.querySelectorAll("button")]
    .find((b) => b.getAttribute("aria-controls") === "modulmeny");
  assert.ok(bryter, "ingen bryter for modulmenyen");
  assert.equal(bryter.getAttribute("aria-expanded"), "true");
  bryter.dispatchEvent(new window.Event("click"));
  assert.equal(bryter.getAttribute("aria-expanded"), "false",
    "menyen ble skjult uten at bryteren sa fra");
  assert.equal(rot.querySelector(".skall-venstre").hidden, true);
});

test("AppShell: søket filtrerer modulmenyen, og tomt treff sier det", async () => {
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], paaSprak: () => {}, paaLoggUt: () => {} });
  const felt = rot.querySelector("#skall-sok");
  assert.ok(felt, "søkefeltet mangler i toppen");
  felt.value = "bank";
  felt.dispatchEvent(new window.Event("input"));
  const treff = [...rot.querySelectorAll(".skall-modul")].map((b) => b.textContent);
  assert.ok(treff.length > 0 && treff.length < 45, `fikk ${treff.length} treff`);
  assert.ok(treff.every((n) => n.toLowerCase().includes("bank")));

  felt.value = "finnesikkexyz";
  felt.dispatchEvent(new window.Event("input"));
  assert.equal(rot.querySelectorAll(".skall-modul").length, 0);
  assert.ok(rot.querySelector(".skall-venstre").textContent
    .includes(NB["ui.shell.moduler_tomt"]), "tomt søk sier ikke fra");
});

test("AppShell: et modulvalg fyller kontekstpanelet", async () => {
  const { rot } = AppShell({ tenant: "Acme", sprak: "nb", aktiv: "oversikt",
    ruter: [{ nokkel: "oversikt" }], paaSprak: () => {}, paaLoggUt: () => {} });
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
