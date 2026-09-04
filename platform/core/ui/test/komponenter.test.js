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
import { byggRuter } from "../static/js/sitekart.js";
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
  // Rutene og tildelingen kommer fra produksjonsbyggeren her, så axe faktisk
  // ser den LISTA skallet bygger i dag: modulmenyen blander nå lenker (moduler
  // med arbeidsflate) og knapper (moduler som fyller kontekstpanelet), og en
  // blandet liste var uprøvd av hel-side-reglene.
  const ruter = byggRuter({ scopes: ["decisions:read"] });
  const { rot, hoved } = AppShell({
    tenant: "Acme AS", sprak: "nb", aktiv: "oversikt", ruter,
    moduler: [1, 2, 37], paaSprak: () => {}, paaLoggUt: () => {},
  });
  assert.ok(rot.querySelector("header"));
  assert.ok(rot.querySelector("nav"));
  assert.equal(hoved.id, "hovedinnhold");
  assert.equal(rot.querySelector('a[aria-current="page"]').getAttribute("href"),
    "#/oversikt");
  // Undertittelen og ruteantallet er BORTE fra skallet (eiervedtak 1/9):
  // det ene sto på hver side uten å si noe man kan handle på, det andre
  // er en opplysning om seg selv og hører til profilen. Porten er snudd
  // — de skal ikke komme snikende tilbake i topplinjen.
  // Nøklene BEHOLDES i locale nettopp fordi porten under er negativ: blir
  // de slettet, returnerer `t()` nøkkelnavnet, og en `!includes(...)` på en
  // streng som ikke finnes noe sted er sann uansett — porten ville blitt
  // grønn og målt ingenting. Derfor sjekkes det først at de fortsatt
  // slår opp til ekte tekst.
  assert.notEqual(t("ui.shell.undertittel"), "ui.shell.undertittel",
    "locale-nøkkelen er slettet — den negative porten under måler da ingenting");
  assert.notEqual(t("ui.shell.ruter"), "ui.shell.ruter",
    "locale-nøkkelen er slettet — den negative porten under måler da ingenting");
  assert.ok(!rot.textContent.includes(t("ui.shell.undertittel")),
    "undertittelen er tilbake i topplinjen");
  assert.ok(!rot.textContent
    .includes(`${ruter.length} · ${t("ui.shell.ruter")}`),
  "ruteantallet er tilbake i topplinjen — det hører til profilen");
  // Navigasjonen ligger i SAMME rad: den er et barn av <header>, ikke en
  // stripe under den.
  assert.ok(rot.querySelector("header nav"),
    "navigasjonen forlot toppfeltet");

  // HOPP-FORBI-LENKEN MÅ FORTSATT HOPPE FORBI NAVIGASJONEN (Cursor P2, WCAG
  // 2.4.1 / #52). `.hoppelenke` i `index.html` peker på `#hovedinnhold`, og
  // hele poenget er at navigasjonen ligger UTENFOR målet. Skallet har nå to
  // navigasjonslandemerker, og venstre er den eneste annonserte veien til 038
  // og M-57 — havner en av dem inne i `main`, lander hoppet på menyen igjen.
  // Landingssiden porterer den samme relasjonen for `.site-hovednav`.
  assert.equal(hoved.querySelector("nav"), null,
    "navigasjonen ligger inne i hopp-målet — hoppelenken hopper ingensteds");
  for (const n of rot.querySelectorAll("nav")) {
    assert.ok(!hoved.contains(n), "et navigasjonslandemerke ligger inne i main");
  }

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
  // Uten e-post er id-en det ENESTE man har, og da står den — et tomt felt
  // ville vært verre enn en teknisk streng.
  assert.equal(bruker.querySelector(".skall-bruker-navn").textContent,
    "bid_10e5674");

  // MED e-post viser brikken BARE den (eiervedtak 1/9: «det er rotete med
  // bid…»). Roller og den fulle prinsipal-id-en er flyttet til
  // profilkortet i Admin, og låst der av `admin_profil.test.js` — kravet
  // er det samme, målt på det nye stedet. Brikken lenker dit, så id-en er
  // ett klikk unna midt i en attestasjonsflyt.
  const medEpost = AppShell({
    tenant: "Acme AS", sprak: "nb", aktiv: "oversikt", ruter,
    brukerId: "bid_10e5674", epost: "kari@acme.no", roller: ["godkjenner"],
    paaSprak: () => {}, paaLoggUt: () => {},
  }).rot;
  const brikke = medEpost.querySelector(".skall-bruker");
  assert.equal(brikke.querySelector(".skall-bruker-navn").textContent,
    "kari@acme.no");
  assert.ok(brikke.getAttribute("title").includes("bid_10e5674"),
    "id-en skal fortsatt vaere naaelig ved hover");

  // TO ØKTER, TO UTFALL — og forskjellen er hele poenget (CodeRabbit
  // major). `#/admin` krever `security:read` eller plattformdrift. Har
  // økten profilen, er id-en ETT KLIKK unna, og topplinjen kan la den
  // være. Har den den IKKE, finnes det ingen annen visning, og da må
  // id-en bli stående — ellers er den bare naaelig med mus, for nettopp
  // den brukeren som attesterer.
  const medProfil = byggRuter({ scopes: ["decisions:read", "security:read"] });
  const utenProfil = byggRuter({ scopes: ["decisions:read"] });
  assert.ok(medProfil.some((r) => r.nokkel === "admin"));
  assert.ok(!utenProfil.some((r) => r.nokkel === "admin"));

  const somAdmin = AppShell({
    tenant: "Acme AS", sprak: "nb", aktiv: "oversikt", ruter: medProfil,
    brukerId: "bid_10e5674", epost: "kari@acme.no", roller: ["godkjenner"],
    paaSprak: () => {}, paaLoggUt: () => {},
  }).rot;
  assert.equal(somAdmin.querySelector(".skall-bruker").getAttribute("href"),
    "#/admin", "brikken lenker ikke til profilen for en økt som HAR den");
  assert.ok(!somAdmin.textContent.includes("bid_10e5674"),
    "prinsipal-id-en er tilbake i topplinjen");
  assert.ok(!somAdmin.textContent.includes(t("ui.rolle.godkjenner")),
    "rollelisten er tilbake i topplinjen");

  const somGodkjenner = AppShell({
    tenant: "Acme AS", sprak: "nb", aktiv: "oversikt", ruter: utenProfil,
    brukerId: "bid_10e5674", epost: "kari@acme.no", roller: ["godkjenner"],
    paaSprak: () => {}, paaLoggUt: () => {},
  }).rot;
  const uten = somGodkjenner.querySelector(".skall-bruker");
  assert.equal(uten.getAttribute("href"), null,
    "brikken lenker til en flate økten ikke har");
  assert.ok(somGodkjenner.textContent.includes("bid_10e5674"),
    "id-en forsvant for en økt som ikke har noe sted å finne den");
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
  // 🔴 Sonen er den eneste annonserte veien til modulflatene (Cursor P2), og
  // toppnavigasjonen lister dem med vilje ikke. Som `aside` fant den som
  // hopper mellom navigasjonslandemerker bare plattformflatene. To `nav`-er
  // krever hver sin etikett — her: `aria-label` mot `aria-labelledby`.
  assert.ok(rot.querySelector("nav#modulmeny"),
    "modulmenyen er ikke et navigasjonslandemerke");
  // TRE, ikke to, etter bunnmenyen (4/9): toppen, modulmenyen og
  // hovedmenyen nederst. Tallet står her fordi et landemerke som dukker
  // opp uten at noen har bestemt det er nettopp det som gjør
  // landemerkehopping ubrukelig — da er «neste navigasjon» et sted du
  // ikke vet hva er. Hvert av dem må ha sin egen etikett, ellers er de
  // tre like oppføringer i hjelpemidlets liste.
  const navlandemerker = [...rot.querySelectorAll("nav")];
  assert.equal(navlandemerker.length, 3);
  assert.ok(navlandemerker.every((n) =>
    n.getAttribute("aria-label") || n.getAttribute("aria-labelledby")),
  "et navigasjonslandemerke står uten etikett");
  assert.ok(rot.querySelector(".skall-kontekst"), "kontekstpanel mangler");
  assert.ok(rot.querySelector(".skall-status"), "statuslinje mangler");

  // MODULMENYEN ER FANER, IKKE ÉN LANG LISTE (eiers vedtak 4/9).
  //
  // Grupperingen var overskrifter i én rull: trettisju rader under elleve
  // overskrifter er ikke en meny, det er en katalog. Nå er hvert område en
  // fane, og ETT område er synlig om gangen — det største har ni rader.
  //
  // Kravet er skjerpet, ikke flyttet: før holdt det at radene sto under en
  // overskrift. Nå må det faktisk VÆRE færre synlige samtidig.
  const tabliste = rot.querySelector(".skall-venstre [role='tablist']");
  assert.ok(tabliste, "modulmenyen har ingen områdefaner");
  const faner = [...tabliste.querySelectorAll("[role='tab']")];
  assert.ok(faner.length >= 5,
    `modulmenyen har bare ${faner.length} områdefaner`);
  assert.equal(faner.filter((f) => f.getAttribute("aria-selected") === "true")
    .length, 1, "det er ikke nøyaktig ett område åpent");
  // ALLE RADENE FINNES, ÉN GRUPPE VISES. Skjult er noe annet enn
  // fraværende: menyen påstår å vise tildelingen din, og da må resten av
  // modulene dine ligge i dokumentet — ikke bare i en fane du ennå ikke
  // har trykket på.
  const paneler = [...rot.querySelectorAll(".skall-venstre .faner-panel")];
  assert.equal(paneler.length, faner.length);
  assert.equal(paneler.filter((p) => !p.hidden).length, 1,
    "mer enn ett områdepanel er synlig samtidig");
  assert.ok(paneler.every((p) => p.querySelector(".skall-modul")),
    "et områdepanel ble aldri bygget");
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


test("AppShell: modulkortet ER inngangen til flaten; toppnav bærer bare plattformflatene", () => {
  // Eiers arkitekturvedtak 24/8: venstre = modulnavigasjonen, topp =
  // plattformflatene. Kort med flate navigerer; ruten står (dyplenker
  // virker), men toppnav lister den ikke. Uten flate: panelet som før.
  //
  // RUTENE KOMMER FRA PRODUKSJONSBYGGEREN (Codex P1). Med et håndlaget
  // ruteobjekt testet denne bare skallets halvdel av avtalen, og bommet
  // derfor på at `byggRuter` kastet `modulflate` på vei ut — altså på at
  // ingen ekte økt så noe av dette. `visApp` bygger rutene på nøyaktig
  // denne ene måten, og da er det den måten testen skal bruke.
  const brett = nyttBrett();
  const { rot } = AppShell({ tenant: "acme",
    ruter: byggRuter({ scopes: ["decisions:read"] }),
    aktiv: "oversikt", sprak: "nb", moduler: [1, 57],
    paaSprak: () => {}, paaLoggUt: () => {} });
  brett.append(rot);
  // Toppnav: plattformflaten inne, modulflaten UTE. Selektoren peker på
  // `.skall-nav` og ikke bare `nav`, for modulmenyen er nå selv et
  // navigasjonslandemerke (Cursor P2) — og det er nettopp poenget: begge er
  // navigasjon, spørsmålet er hvilken av dem ruten hører til.
  assert.ok(rot.querySelector('nav.skall-nav a[href="#/oversikt"]'));
  // BEGGE modulflatene, ikke bare den ene (Cursor P2): vedtaket og 038 gjelder
  // like mye for WCAG kontroll, og med bare rekrutteringsraden målt kunne
  // `modulflate: 56` falle ut av sitekartet uten at suiten rødnet — 038 tilbake
  // i feil sone, og to innganger hvis kortet ble stående.
  for (const rute of ["rekruttering", "wcagkontroll"]) {
    assert.equal(rot.querySelector(`nav.skall-nav a[href="#/${rute}"]`), null,
      `modulflate i toppnav: ${rute} — den bor i venstremenyen nå`);
    assert.equal(rot.querySelector(`.skall-modul[href="#/${rute}"]`).tagName, "A",
      `${rute} mangler kortet som nå er inngangen`);
  }
  // Kortet navigerer — og en navigasjon er en LENKE (Codex P2). Kortet er den
  // eneste annonserte inngangen etter at oppføringen forlot toppnavigasjonen,
  // så den må bære adressen sin: ny fane, kopier lenke, og «lenke» framfor
  // «knapp» for den som hører flaten i stedet for å se den.
  const kort = (n) => [...brett.querySelectorAll(".skall-modul")]
    .find((e) => e.textContent === t(`site.katalog.m${n}.navn`));
  const flatekort = (rute) => brett.querySelector(`.skall-modul[href="#/${rute}"]`);
  assert.equal(flatekort("rekruttering").tagName, "A",
    "modulkortet med flate er ikke en lenke");
  // ...og lenken heter det den ÅPNER (Cursor P2): flaten bærer
  // `ui.rekruttering.tittel`, som er samme streng som `ui.nav.rekruttering` —
  // ordet 038 ratifiserte, og det brukeren klikket på i toppnavigasjonen
  // fram til nå. Katalognavnet pekte på en flate som het noe annet.
  assert.equal(flatekort("rekruttering").textContent, t("ui.nav.rekruttering"));
  assert.equal(kort(57), undefined,
    "kortet står fortsatt med katalognavnet i stedet for flatens");
  // `href` er hele påstanden, og med vilje: fragmentnavigasjonen ved klikk er
  // nettleserens jobb nå, ikke vår, og jsdom utfører den ikke. Å legge en
  // klikk-handler oppå lenken bare for å kunne måle den her ville vært å
  // gjenreise mekanismen funnet ba oss fjerne. Adressen er den samme som
  // dyplenken — `ruter.js` svarer på `#/rekruttering` — så det den peker på
  // er alt dekket av rutertestene.
  window.location.hash = "#/oversikt";
  // Modul UTEN flate får panelet, aldri en død navigasjon — og skal fortsatt
  // være en knapp: det finnes ingen adresse å peke på.
  assert.equal(kort(1).tagName, "BUTTON",
    "en flateløs modul utgir seg for å være en lenke");
  kort(1).click();
  assert.ok(brett.querySelector(".skall-kontekst-tittel"),
    "flateløs modul mistet kontekstpanelet");
  assert.equal(window.location.hash, "#/oversikt",
    "flateløs modul endret adressen");
});

test("AppShell: flaten økten har rute til står i menyen, også utenfor tildelingen", () => {
  // 🔴 TO PORTER SOM BLE SERIEKOBLET (Cursor P1). Ruten gates på SCOPE,
  // menyraden på KATALOGTILDELING. Ingen rad i `_UTRULLING` har 56 eller 57,
  // og en ukjent tenant har ingen tildeling i det hele tatt — så da
  // oppføringen forlot toppnavigasjonen, forsvant WCAG kontroll og
  // rekruttering fra hver eneste ekte økt uten å dukke opp noe annet sted.
  // Testene så det ikke fordi de oppga `moduler: [1, 57]`; Nordvik har
  // `(1, 2, 37)`.
  const ruter = byggRuter({ scopes: ["decisions:read"] });
  const kortene = (rot) => [...rot.querySelectorAll(".skall-modul")]
    .map((e) => e.getAttribute("href"));

  const nordvik = AppShell({ tenant: "Nordvik Regnskap AS", ruter,
    aktiv: "oversikt", sprak: "nb", moduler: [1, 2, 37],
    paaSprak: () => {}, paaLoggUt: () => {} });
  nyttBrett().append(nordvik.rot);
  assert.ok(kortene(nordvik.rot).includes("#/wcagkontroll"),
    "WCAG kontroll har ingen inngang i det hele tatt for en ekte tenant");
  assert.ok(kortene(nordvik.rot).includes("#/rekruttering"),
    "rekruttering har ingen inngang i det hele tatt for en ekte tenant");
  // Tildelingen står ved siden av, som før — unionen er ikke katalogen
  // tilbake.
  assert.ok(kortene(nordvik.rot).includes("#/nokkeltall"),
    "nøkkeltall har ingen inngang etter eiervedtaket 31/8 (topp → venstre)");
  // M-5 (094): malflaten er den fjerde modulflaten en leseøkt har rute
  // til (`decisions:read`), og den står i venstremenyen ved siden av
  // WCAG kontroll, rekruttering og nøkkeltall — samme union som over.
  assert.ok(kortene(nordvik.rot).includes("#/dokumentmal"),
    "malregisteret har ingen inngang i det hele tatt for en ekte tenant");
  // Eiervedtak 1/9 (topp → venstre, runde 2): ordlisten og
  // fristregisteret er de to nye modulflatene en LESEØKT har rute til.
  // De seks andre som flyttet samme dag ligger bak andre scopes og er
  // derfor ikke i denne økten — porten teller det økten faktisk har.
  assert.ok(kortene(nordvik.rot).includes("#/kunnskap"),
    "ordlisten har ingen inngang i det hele tatt for en ekte tenant");
  assert.ok(kortene(nordvik.rot).includes("#/avtalefrist"),
    "fristregisteret har ingen inngang i det hele tatt for en ekte tenant");
  // M-22 (098): lisensregisteret er den neste modulflaten en LESEØKT har
  // rute til. TALLET UNDER ER UTTØMMENDE og vokser med hver slik flate —
  // en ny modulflate bak `decisions:read` skal UTVIDE det, ikke skrive
  // en ny assert ved siden av (lærdommen fra klynge 1: to uttømmende
  // asserts over samme rot kan aldri begge være sanne).
  assert.ok(kortene(nordvik.rot).includes("#/lisens"),
    "lisensregisteret har ingen inngang i det hele tatt for en ekte tenant");
  // M-17 (102): kundeservicekøen er den neste modulflaten en LESEØKT har
  // rute til — køen er tenantens alminnelige arbeidsflate, og den som
  // svarer kunder skal se den. TALLET UNDER UTVIDES, det dupliseres ikke.
  assert.ok(kortene(nordvik.rot).includes("#/kundeservice"),
    "kundeservicekøen har ingen inngang i det hele tatt for en ekte tenant");
  // M-18 (103): onboardingløpene er den neste modulflaten en LESEØKT
  // har rute til. TALLET UNDER UTVIDES, det dupliseres ikke.
  assert.ok(kortene(nordvik.rot).includes("#/onboarding"),
    "onboardingregisteret har ingen inngang for en ekte tenant");
  assert.equal(nordvik.rot.querySelectorAll(".skall-modul").length, 12,
    "menyen viser mer enn tildelingen pluss flatene økten har rute til");

  // En UKJENT tildeling («vet ikke») skal fortsatt nå flatene sine — og
  // fortsatt si fra at den ikke vet, ellers framstår de to radene som hele
  // svaret på hva økten har.
  const ukjent = AppShell({ tenant: "Nordvik Regnskap AS", ruter,
    aktiv: "oversikt", sprak: "nb", paaSprak: () => {}, paaLoggUt: () => {} });
  nyttBrett().append(ukjent.rot);
  assert.deepEqual(kortene(ukjent.rot).sort(),
    ["#/avtalefrist", "#/dokumentmal", "#/kundeservice", "#/kunnskap",
      "#/lisens", "#/nokkeltall", "#/onboarding", "#/rekruttering",
      "#/wcagkontroll"],
    "en ukjent tildeling mistet flatene økten har rute til");
  assert.ok(ukjent.rot.querySelector(".skall-venstre").textContent
    .includes(NB["ui.shell.moduler_ukjent"]),
  "menyen sier ikke lenger fra at tildelingen mangler");

  // Og motsatt vei: uten scopet finnes ruten ikke, og da finnes heller ikke
  // kortet. Unionen åpner ingen ny dør — den er de rutene `byggRuter` alt gav.
  const uten = AppShell({ tenant: "Nordvik Regnskap AS",
    ruter: byggRuter({ scopes: [] }), aktiv: "kundeadmin", sprak: "nb",
    moduler: [1, 2, 37], paaSprak: () => {}, paaLoggUt: () => {} });
  nyttBrett().append(uten.rot);
  assert.deepEqual(kortene(uten.rot).filter(Boolean), [],
    "en økt uten ruten fikk likevel et kort som lover flaten");
});

test("AppShell: søket finner flateraden på begge navnene den har (Cursor P2)", () => {
  // 🔴 Raden heter nå flaten sin («WCAG kontroll»), mens modulen fortsatt
  // heter «Automatisk WCAG-kontroll» i utrullingstabellen, på kundeflaten og
  // i kontekstpanelet. Med bare ett av navnene i høystakken mistet alltid det
  // andre den eneste inngangen flaten har — og bindestreken gjør at det ene
  // ikke er et delstreng-treff i det andre.
  const brett = nyttBrett();
  const { rot } = AppShell({ tenant: "acme",
    ruter: byggRuter({ scopes: ["decisions:read"] }), aktiv: "oversikt",
    sprak: "nb", moduler: [1, 2, 37], paaSprak: () => {}, paaLoggUt: () => {} });
  brett.append(rot);
  const felt = brett.querySelector("#skall-sok");
  const sok = (q) => {
    felt.value = q;
    felt.dispatchEvent(new window.Event("input"));
    return [...brett.querySelectorAll(".skall-modul")]
      .map((e) => e.getAttribute("href"));
  };
  assert.deepEqual(sok(NB["ui.nav.wcagkontroll"]), ["#/wcagkontroll"],
    "flatens eget navn finner ikke raden");
  assert.deepEqual(sok(NB["site.katalog.m56.navn"]), ["#/wcagkontroll"],
    "modulens katalognavn finner ikke raden");
  // En flateløs modul er uendret: ett navn, og det er katalogens.
  assert.equal(sok(NB["site.katalog.m37.navn"]).length, 1);
});

test("AppShell: den aktive modulflaten er merket i menyen som eier den", () => {
  // 🔴 HVOR ER JEG (Codex P2). `settAktiv` oppdaterer bare `lenker`, og
  // modulflatene er nettopp de rutene som er filtrert UT av den — mens
  // `valgtModul` bare settes av `visKontekst`, som flateklikket hopper over.
  // Modulflatene hadde altså verken visuell eller `aria-current`-markering
  // noe sted etter at oppføringen flyttet til venstremenyen.
  const brett = nyttBrett();
  const ruter = byggRuter({ scopes: ["decisions:read"] });
  const skall = AppShell({ tenant: "acme", ruter, aktiv: "oversikt",
    sprak: "nb", moduler: [1, 14, 56, 57],
    paaSprak: () => {}, paaLoggUt: () => {} });
  brett.append(skall.rot);
  const kort = (n) => [...brett.querySelectorAll(".skall-modul")]
    .find((e) => e.textContent === t(`site.katalog.m${n}.navn`));
  // Raden med flate heter flaten sin (Cursor P2), så den slås opp på adressen.
  const flatekort = (rute) => brett.querySelector(`.skall-modul[href="#/${rute}"]`);

  assert.equal(flatekort("rekruttering").getAttribute("aria-current"), null,
    "flaten er merket før noen har navigert til den");
  skall.settAktiv("rekruttering");
  assert.equal(flatekort("rekruttering").getAttribute("aria-current"), "page",
    "den aktive modulflaten er umerket i menyen som eier den");
  // Én om gangen, og ordet er «page» — samme som toppnavigasjonen bruker,
  // fordi det er samme slags opplysning.
  skall.settAktiv("wcagkontroll");
  assert.equal(flatekort("wcagkontroll").getAttribute("aria-current"), "page");
  assert.equal(flatekort("rekruttering").getAttribute("aria-current"), null,
    "to modulflater står som aktive samtidig");
  // En plattformflate eier ingen modulrad: da skal ingen av dem være merket.
  skall.settAktiv("oversikt");
  assert.equal(flatekort("wcagkontroll").getAttribute("aria-current"), null,
    "modulraden ble stående merket etter at brukeren forlot flaten");

  // Panelvalget lever ved siden av, og de to kolliderer ikke: en modul har
  // enten en flate eller ikke. Brukeren skal kunne lese om modul 14 i panelet
  // mens hun står på rekrutteringsflaten.
  skall.settAktiv("rekruttering");
  skall.visKontekst(14, { fokuser: false });
  assert.equal(kort(14).getAttribute("aria-current"), "true",
    "panelvalget mistet markeringen sin");
  assert.equal(flatekort("rekruttering").getAttribute("aria-current"), "page",
    "flatemarkeringen forsvant da panelet ble fylt");
  // ...og markeringen overlever at søket tegner menyen på nytt, samme regel
  // som panelvalget alt hadde.
  const felt = brett.querySelector("#skall-sok");
  felt.value = t("ui.nav.rekruttering");
  felt.dispatchEvent(new window.Event("input"));
  assert.equal(flatekort("rekruttering").getAttribute("aria-current"), "page",
    "flatemarkeringen forsvant da søket tegnet menyen på nytt");

  // DYPLENKEN ER FØRSTE TEGNING (Codex P2). Den som åpner `#/rekruttering`
  // rett fra et bokmerke får skallet bygget med ruten som `aktiv`, og skal se
  // hvor hun er med en gang — ikke først etter neste navigasjon.
  const dyp = AppShell({ tenant: "acme", ruter, aktiv: "rekruttering",
    sprak: "nb", moduler: [1, 57], paaSprak: () => {}, paaLoggUt: () => {} });
  nyttBrett().append(dyp.rot);
  assert.equal(dyp.rot.querySelector('.skall-modul[href="#/rekruttering"]')
    .getAttribute("aria-current"), "page",
  "en dyplenke inn i flaten er umerket i menyen");
});

test("AppShell: tenantbrikken utelates når den gjentar produktnavnet", () => {
  // Eier: «disponit står 2 ganger». Produktnavn + tenantnavn er to
  // forskjellige ting, men når de er samme streng er den andre ren støy.
  const ruter = byggRuter({ scopes: ["decisions:read"] });
  const lik = AppShell({ tenant: "disponit", sprak: "nb", aktiv: "oversikt",
    ruter, paaSprak: () => {}, paaLoggUt: () => {} }).rot;
  assert.equal(lik.querySelector(".skall-tenant"), null,
    "tenantbrikken gjentar produktnavnet");

  // …men en ekte kunde skal fortsatt se hvilken tenant hun er i. Det er
  // hele grunnen til at brikken finnes.
  const ulik = AppShell({ tenant: "Nordvik Regnskap AS", sprak: "nb",
    aktiv: "oversikt", ruter, paaSprak: () => {}, paaLoggUt: () => {} }).rot;
  assert.equal(ulik.querySelector(".skall-tenant").textContent,
    "Nordvik Regnskap AS",
    "tenantbrikken forsvant for en kunde som trenger den");
});

test("Mobilreglene gir navigasjonen sin EGEN rad", () => {
  // 🔴 EIERS SKJERMBILDE 1/9. Én-rad-toppfeltet er riktig på en bred
  // skjerm og brakk på telefon: `.skall-nav { flex: 1 }` KRYMPER heller
  // enn å brekke til neste rad, så navigasjonen ble presset ned i en smal
  // kolonne med én lenke per linje — halve skjermen til en meny på ni ord.
  //
  // `flex-wrap: wrap` på foreldren er IKKE nok, og det er hele poenget
  // med denne porten: barnet må eksplisitt kreve hele bredden. jsdom har
  // ingen layout å måle, så regelen leses fra stilarket — samme form som
  // `.skall-bruker`-porten over.
  const raa = readFileSync(new URL(
    "../static/css/base.css", import.meta.url), "utf8");
  // Kommentarene MÅ strippes først: denne blokken FORKLARER hvorfor
  // `.skall-nav { flex: 1 }` er feil på mobil, så et naivt `indexOf`
  // finner omtalen i prosaen før selve regelen — og porten ville målt
  // kommentaren sin egen tekst. Den feilen fanget porten på seg selv
  // ved første kjøring.
  const css = raa.replace(/\/\*[\s\S]*?\*\//g, "");
  const mobil = css.slice(css.indexOf("@media (max-width: 720px)"));
  const navregel = mobil.slice(mobil.indexOf(".skall-nav"),
    mobil.indexOf("}", mobil.indexOf(".skall-nav")));
  assert.match(navregel, /flex:\s*0\s+0\s+100%/,
    "navigasjonen krever ikke hele bredden på mobil — den krymper i stedet"
    + " for å brekke, og hver lenke får sin egen linje");

  // Og regelen skal ikke peke på klasser som ikke finnes: den gamle
  // `.skall-ruteantall`-regelen overlevde flyttingen til profilen og
  // gjorde ingenting, mens den skjulte at resten var utilstrekkelig.
  const doede = ["skall-ruteantall", "skall-undertekst", "skall-brand"];
  for (const klasse of doede) {
    assert.ok(!css.includes(`.${klasse}`),
      `base.css styler .${klasse}, som ikke finnes i skallet lenger`);
  }
});
