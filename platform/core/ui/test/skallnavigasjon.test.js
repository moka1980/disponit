// Skallets navigasjon (eiers vedtak 4/9) — bunnmeny og områdefaner.
//
// BAKGRUNNEN, MED EIERS EGNE ORD: «modulene på venstre ser kjedelig ut og
// ikke godt organisert», «heller ikke mobilvisningen er god», og
// «hovedmenyene skal være helt nederst på mobil».
//
// Det som faktisk sto der: toppnavigasjonen med ti piller som brakk til
// sin egen rad, søkefeltet, menybryteren og en modulmeny på trettisju
// rader — ALT FØR innholdet. På en telefon gikk halve skjermen med til
// navigasjon før første opplysning.
//
// Reglene portene her måler, og hvorfor de er reglene:
//
//   * MAKS FEM I BUNNEN. Flere, og målene blir for smale for en tommel.
//   * MINST 44 PIKSLER. Under det bommer man, og en meny man bommer på
//     er verre enn en man må åpne.
//   * IKON *OG* TEKST. Et ikon alene er en gjetning.
//   * BARE ØVERSTE NIVÅ. Undernavigasjon hører ikke hjemme i bunnen.
//   * ÉN VEI PER MÅL. Samme rute to steder i samme skjermbilde er ikke
//     to muligheter — det er en meny som ser dobbelt så stor ut.
//   * PLASSEN RESERVERES. En fast meny som legger seg over innholdet
//     spiser den siste raden på hver eneste side.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { AppShell } from "../static/js/komponenter.js";
import { IKONSTIER, ikon } from "../static/js/dom.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");
const css = (navn) => readFileSync(
  join(ROT, "platform", "core", "ui", "static", "css", `${navn}.css`),
  "utf-8");

const RUTER = [
  { nokkel: "oversikt" }, { nokkel: "policy" }, { nokkel: "beslutninger" },
  { nokkel: "unntak" }, { nokkel: "kundeadmin" }, { nokkel: "adjudikator" },
  { nokkel: "policyadmin" }, { nokkel: "varsler" }, { nokkel: "admin" },
];

function skall(over = {}) {
  const brett = nyttBrett();
  const s = AppShell({ tenant: "Bjørkli", sprak: "nb", aktiv: "oversikt",
    ruter: RUTER, moduler: [1, 2, 5, 9, 12, 16, 19, 24],
    paaSprak: () => {}, paaLoggUt: () => {}, ...over });
  brett.append(s.rot);
  return s;
}

const bunn = (rot) => rot.querySelector(".skall-bunn");
const valgene = (rot) =>
  [...rot.querySelectorAll(".skall-bunn-liste .skall-bunn-valg")];

// ---------------------------------------------------------------------
// BUNNMENYEN
// ---------------------------------------------------------------------

test("bunnmeny: finnes, er et eget landemerke og har sin egen etikett",
     () => {
  const { rot } = skall();
  const n = bunn(rot);
  assert.ok(n, "bunnmenyen finnes ikke");
  assert.equal(n.tagName, "NAV");
  assert.equal(n.getAttribute("aria-label"), t("ui.shell.bunnmeny"));
  // TRE LANDEMERKER, TRE FORSKJELLIGE NAVN. «Navigasjon» tre ganger er
  // tre oppføringer som ikke navngir noe for den som hopper mellom dem.
  const navn = [...rot.querySelectorAll("nav")].map((e) =>
    e.getAttribute("aria-label")
    || rot.querySelector(`#${e.getAttribute("aria-labelledby")}`).textContent);
  assert.equal(new Set(navn).size, navn.length, navn.join(" / "));
});

test("bunnmeny: maks fem valg", () => {
  // MUTASJONEN SOM DREPER DENNE: legg hele toppnavigasjonen i bunnen.
  const { rot } = skall();
  const v = valgene(rot);
  assert.ok(v.length <= 5, `bunnmenyen har ${v.length} valg`);
  assert.ok(v.length >= 3, "bunnmenyen er for tom til å være en meny");
});

test("bunnmeny: hvert valg har både ikon og tekst", () => {
  const { rot } = skall();
  for (const v of valgene(rot)) {
    const svg = v.querySelector("svg");
    assert.ok(svg, `«${v.textContent}» har ingen ikon`);
    // IKONET LESES IKKE OPP. Teksten står ved siden av, og et ikon som
    // også annonseres sier det samme to ganger.
    assert.equal(svg.getAttribute("aria-hidden"), "true");
    const tekst = v.querySelector(".skall-bunn-tekst");
    assert.ok(tekst && tekst.textContent.trim(), "et valg står uten tekst");
  }
});

test("bunnmeny: ingen emoji som ikon", () => {
  // Emoji er skriftavhengige, ser forskjellige ut på hver plattform, kan
  // ikke styres av designtokens, og leses opp med sitt eget navn.
  const { rot } = skall();
  const emoji = /\p{Extended_Pictographic}/u;
  for (const v of valgene(rot)) {
    assert.equal(emoji.test(v.textContent), false, v.textContent);
    assert.equal(v.querySelector("svg").namespaceURI,
      "http://www.w3.org/2000/svg", "ikonet er ikke en ekte SVG");
  }
});

test("bunnmeny: «Mer» bærer det bunnen ikke har plass til, og ingenting"
     + " annet", () => {
  // ÉN VEI PER MÅL. En rute som sto begge steder ville gitt to innganger
  // til samme sted i samme meny.
  const { rot } = skall();
  const iBunnen = valgene(rot)
    .filter((v) => v.tagName === "A")
    .map((v) => v.getAttribute("href"));
  const iMer = [...rot.querySelectorAll(".skall-bunn-mer-valg")]
    .map((v) => v.getAttribute("href"));
  assert.equal(iMer.some((h) => iBunnen.includes(h)), false,
    `en rute står både i bunnen og under «Mer»: ${iMer.join(" ")}`);
  // …OG TIL SAMMEN ER DE HELE TOPPNAVIGASJONEN. En rute som falt ut av
  // begge ville vært utilgjengelig på telefon.
  assert.deepEqual([...iBunnen, ...iMer].sort(),
    RUTER.map((r) => `#/${r.nokkel}`).sort());
});

test("bunnmeny: «Mer» er lukket til man ber om den", () => {
  const { rot } = skall();
  const mer = valgene(rot).at(-1);
  assert.equal(mer.getAttribute("aria-expanded"), "false");
  const liste = rot.querySelector("#skall-bunn-mer");
  assert.equal(liste.hidden, true);
  assert.equal(mer.getAttribute("aria-controls"), "skall-bunn-mer");
  mer.click();
  assert.equal(mer.getAttribute("aria-expanded"), "true");
  assert.equal(liste.hidden, false);
  mer.click();
  assert.equal(liste.hidden, true);
});

test("bunnmeny: «Moduler» er en bryter, ikke en lenke, og de to bryterne"
     + " sier det samme", () => {
  // TO KONTROLLER, ÉN TILSTAND. En av dem som sa noe annet enn den andre
  // ville vært en kontroll som lyver om hva den gjorde.
  const { rot } = skall();
  const moduler = valgene(rot).find((v) =>
    v.textContent.includes(t("ui.shell.moduler")));
  assert.equal(moduler.tagName, "BUTTON",
    "«Moduler» lover en side som ikke finnes");
  assert.equal(moduler.getAttribute("aria-controls"), "modulmeny");
  const topp = [...rot.querySelectorAll("button")].find((b) =>
    b.textContent === t("ui.shell.skjul_meny"));
  assert.equal(moduler.getAttribute("aria-expanded"), "true");
  topp.click();
  assert.equal(moduler.getAttribute("aria-expanded"), "false",
    "bunnbryteren sa noe annet enn toppbryteren");
  moduler.click();
  assert.equal(topp.getAttribute("aria-expanded"), "true");
});

test("bunnmeny: den sier hvor du står", () => {
  // Uten «her er du» er bunnmenyen fire like knapper: du vet hvor du kan
  // gå, ikke hvor du står.
  const { rot, settAktiv } = skall();
  const oversikt = valgene(rot).find((v) =>
    v.getAttribute("href") === "#/oversikt");
  assert.equal(oversikt.getAttribute("aria-current"), "page");
  settAktiv("varsler");
  assert.equal(oversikt.hasAttribute("aria-current"), false);
  assert.equal(valgene(rot).find((v) =>
    v.getAttribute("href") === "#/varsler").getAttribute("aria-current"),
  "page");
});

test("bunnmeny: bare ruter økten faktisk har", () => {
  // Samme gating som toppnavigasjonen. En bunnmeny som viste mer ville
  // vært et løfte om flater økten får 403 på.
  const { rot } = skall({ ruter: [{ nokkel: "oversikt" }] });
  const href = valgene(rot).filter((v) => v.tagName === "A")
    .map((v) => v.getAttribute("href"));
  assert.deepEqual(href, ["#/oversikt"]);
  assert.equal(rot.querySelectorAll(".skall-bunn-mer-valg").length, 0);
});

// ---------------------------------------------------------------------
// CSS-REGLENE — de er en del av kravet, ikke pynt
// ---------------------------------------------------------------------

test("bunnmeny: 44 piksler, telefonens egen kant, og reservert plass",
     () => {
  const k = css("komponenter");
  const blokk = k.slice(k.indexOf("/* --- Bunnmeny (mobil)"));

  // 44px ER IKKE PYNT — det er tommelens mål, og kravet står på selve
  // målet (lenka), ikke på foreldren.
  assert.match(blokk, /\.skall-bunn-valg\s*\{[^}]*min-height:\s*44px/,
    "trykkmålet er ikke minst 44 piksler");
  assert.match(blokk, /\.skall-bunn-mer-valg\s*\{[^}]*min-height:\s*44px/);

  // GESTUSSTRIPA. Uten dette havner nederste del av menyen under
  // telefonens egen navigasjon — synlig, men ikke trykkbar.
  assert.match(blokk, /padding-bottom:\s*env\(safe-area-inset-bottom/,
    "bunnmenyen tar ikke hensyn til telefonens egen kant");

  // PLASSEN RESERVERES. En fast meny som legger seg OVER innholdet spiser
  // den siste raden på hver side — og den siste raden er ofte knappen.
  assert.match(blokk,
    /\.skall\s*\{\s*padding-bottom:\s*calc\(var\(--bunnmeny-h\)/,
    "innholdet reserverer ikke plass til den faste menyen");

  // HØYDEN BOR I TOKENKILDEN, fordi to uavhengige regler må dele nøyaktig
  // samme tall.
  const tokens = readFileSync(join(ROT, "design", "tokens.css"), "utf-8");
  assert.match(tokens, /--bunnmeny-h:/);
  assert.match(tokens, /--z-bunnmeny:/);
});

test("bunnmeny: den finnes bare der den hører hjemme", () => {
  const k = css("komponenter");
  const blokk = k.slice(k.indexOf("/* --- Bunnmeny (mobil)"));
  // På bred skjerm er sidemenyen riktig form, og to primære navigasjoner
  // samtidig er to svar på samme spørsmål.
  assert.match(blokk, /\.skall-bunn\s*\{\s*display:\s*none;\s*\}/);
  assert.match(blokk, /@media \(max-width: 60rem\)/);
  // …og da er toppnavigasjonens piller duplikater.
  assert.match(blokk, /\.skall-topp \.skall-nav\s*\{\s*display:\s*none/,
    "toppnavigasjonen står igjen som duplikat på mobil");
});

test("bunnmeny: den ligger over innholdet, men under dialogene", () => {
  // En dialog som åpnes skal DEKKE navigasjonen, ikke ligge bak den.
  const tokens = readFileSync(join(ROT, "design", "tokens.css"), "utf-8");
  const tall = (navn) =>
    Number(tokens.match(new RegExp(`--z-${navn}:\\s*(\\d+)`))[1]);
  assert.ok(tall("bunnmeny") > tall("topplinje"));
  assert.ok(tall("bunnmeny") < tall("overlegg"));
  assert.ok(tall("bunnmeny") < tall("skuff"));
});

// ---------------------------------------------------------------------
// OMRÅDEFANENE
// ---------------------------------------------------------------------

test("områdefaner: ett område om gangen, og søket bryter dem", () => {
  // SØKET SOM LYVER er den fella fanene lager: treffet ditt ligger i et
  // område du ikke ser på, og lista sier «ingen treff».
  const { rot } = skall();
  assert.ok(rot.querySelector(".skall-venstre [role='tablist']"));
  const sok = rot.querySelector("#skall-sok");
  sok.value = "policy";
  sok.dispatchEvent(new Event("input", { bubbles: true }));
  assert.equal(rot.querySelector(".skall-venstre [role='tablist']"), null,
    "søket lette bare i det åpne området");
  const treff = [...rot.querySelectorAll(".skall-modul")];
  assert.ok(treff.length >= 1);
  // …OG HVERT TREFF BÆRER OMRÅDET SITT, for nå er det søket som
  // grupperer, og du skal likevel se hvor modulen hører hjemme.
  assert.ok(rot.querySelector(".skall-modul-omrade"),
    "treffene sier ikke hvilket område de hører til");
  sok.value = "";
  sok.dispatchEvent(new Event("input", { bubbles: true }));
  assert.ok(rot.querySelector(".skall-venstre [role='tablist']"),
    "fanene kom ikke tilbake da søket ble tømt");
});

test("områdefaner: fanen følger der du er", () => {
  // Ellers måtte du finne deg selv igjen hver gang du navigerte.
  const { rot, settAktiv } = skall({
    ruter: [...RUTER, { nokkel: "nokkeltall", modulflate: 16 },
      { nokkel: "kunnskap", modulflate: 9 }] });
  const valgtFane = () => rot.querySelector(
    ".skall-venstre [role='tab'][aria-selected='true']").textContent;
  settAktiv("kunnskap");
  const forst = valgtFane();
  settAktiv("nokkeltall");
  assert.notEqual(valgtFane(), forst,
    "fanen ble stående i et annet område enn flaten du står på");
});

test("områdefaner: fanevalget overlever et tastetrykk i søket", () => {
  const { rot } = skall();
  const faner = () => [...rot.querySelectorAll(
    ".skall-venstre [role='tab']")];
  faner().at(1).click();
  const valgt = faner().at(1).textContent;
  const sok = rot.querySelector("#skall-sok");
  sok.value = "z";                        // ingen treff
  sok.dispatchEvent(new Event("input", { bubbles: true }));
  sok.value = "";
  sok.dispatchEvent(new Event("input", { bubbles: true }));
  assert.equal(rot.querySelector(
    ".skall-venstre [role='tab'][aria-selected='true']").textContent, valgt,
  "fanen sprang tilbake til den første");
});

// ---------------------------------------------------------------------
// AXE
// ---------------------------------------------------------------------

test("skallnavigasjon: null alvorlige axe-brudd", async () => {
  const { rot } = skall();
  const brudd = await alvorligeBrudd(rot);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("skallnavigasjon: null alvorlige axe-brudd med «Mer» åpen", async () => {
  const { rot } = skall();
  valgene(rot).at(-1).click();
  const brudd = await alvorligeBrudd(rot);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("ikon: ukjent navn er en feil, ikke et tomt bilde", () => {
  assert.throws(() => ikon("finnes-ikke"));
  assert.ok(Object.keys(IKONSTIER).length >= 4);
});

// ---------------------------------------------------------------------
// KONTEKSTPANELET
// ---------------------------------------------------------------------

test("kontekstpanel: tomt panel tar ikke plass", () => {
  // EIERS SKJERMBILDER 4/9. Panelet reserverte inntil tjue rem — en
  // femtedel av bredden — på HVER flate, og på en modulflate sto det med
  // «Velg en modul i menyen» hele tiden, fordi modulflatene ikke bruker
  // det. Kolonnen var permanent opptatt av en setning som ba deg gjøre
  // noe du ikke skulle.
  //
  // MUTASJONEN SOM DREPER DENNE: sett `data-kontekst` til «fylt» ved
  // bygging.
  const { rot, visKontekst } = skall();
  const kropp = rot.querySelector(".skall-kropp");
  assert.equal(kropp.dataset.kontekst, "tom");

  visKontekst(1, { fokuser: false });
  assert.equal(kropp.dataset.kontekst, "fylt",
    "panelet ble fylt uten at plassen ble gitt tilbake");
});

test("kontekstpanel: det fjernes ikke fra dokumentet, bare fra plassen",
     () => {
  // Panelet er et FOKUSMÅL som `visKontekst` sender fokus til, og et mål
  // som ikke finnes kan man ikke sendes til. Det er plassen som frigis.
  const { rot } = skall();
  const panel = rot.querySelector(".skall-kontekst");
  assert.ok(panel, "panelet forsvant ut av dokumentet");
  assert.equal(panel.getAttribute("tabindex"), "-1");
});

test("kontekstpanel: rutenettet vet at sonen er borte", () => {
  // `display: none` alene tar panelet ut av flyten, men et rutenett med
  // en navngitt kolonne holder plassen uansett — kolonnen ville blitt
  // stående tom. Samme lærdom som `data-meny` (Codex P1).
  const k = css("komponenter");
  const blokk = k.slice(k.indexOf("/* --- Kontekstpanelet tar bare plass"));
  assert.match(blokk,
    /\[data-kontekst="tom"\]\s*\{[^}]*grid-template-areas:\s*"venstre hoved"/);
  assert.match(blokk,
    /\[data-kontekst="tom"\]\[data-meny="skjult"\][^}]*"hoved"/);
});
