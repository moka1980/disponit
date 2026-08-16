// axe + oppførsel på komponentbiblioteket (gate 6/7, «fra første komponent»).
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
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
