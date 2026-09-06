// Seksjonsfaner — en lang flate blir faner (omlegging 6/9).
//
// Portene her måler kontrakten mot flatene: modulen rører ikke det de
// tegner, den ser på det. Hver port beskriver mutasjonen som dreper den.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { el } from "../static/js/dom.js";
import { seksjonsfaner } from "../static/js/seksjonsfaner.js";

settI18nForTest(NB, "nb");

const tikk = () => new Promise((r) => setTimeout(r, 0));

function seksjon(tittel, nivaa = "h2") {
  return el("section", { class: "kpi-kort" },
    el(nivaa, { text: tittel }), el("p", { text: `Innhold: ${tittel}` }));
}

function flate(titler) {
  const brett = nyttBrett();
  const hoved = el("main", { id: "hovedinnhold" });
  brett.append(hoved);
  const stopp = seksjonsfaner(hoved);
  const liste = el("div", { class: "kpi-kort-liste" },
    titler.map((tt) => seksjon(tt)));
  hoved.append(el("h1", { text: "Flate" }), liste);
  return { hoved, liste, stopp };
}

const faner = (hoved) => [...hoved.querySelectorAll("[role=tab]")];
const synlige = (liste) => [...liste.children].filter((s) => !s.hidden)
  .map((s) => s.querySelector("h2, h3").textContent);

test("seksjonsfaner: tre seksjoner eller flere blir faner, én vises", async () => {
  const { hoved, liste, stopp } = flate(["Sammendrag", "Funn", "Prognoser", "Modeller"]);
  await tikk();
  const rad = hoved.querySelector(".seksjonsfaner");
  assert.ok(rad, "ingen fanerad ble bygget");
  assert.equal(rad.nextElementSibling, liste, "faneraden står ikke rett før lista");
  assert.equal(hoved.querySelector("[role=tablist]").getAttribute("aria-label"),
    t("ui.shell.seksjoner"));
  assert.deepEqual(faner(hoved).map((f) => f.textContent),
    ["Sammendrag", "Funn", "Prognoser", "Modeller"]);
  assert.deepEqual(synlige(liste), ["Sammendrag"]);
  // WAI-ARIA: fanen peker på panelet sitt, og panelet tilbake.
  const f = faner(hoved)[1];
  const panel = hoved.querySelector(`#${f.getAttribute("aria-controls")}`);
  assert.equal(panel.getAttribute("role"), "tabpanel");
  assert.equal(panel.getAttribute("aria-labelledby"), f.id);
  f.click();
  assert.deepEqual(synlige(liste), ["Funn"]);
  assert.equal(f.getAttribute("aria-selected"), "true");
  assert.equal(faner(hoved)[0].getAttribute("aria-selected"), "false");
  stopp();
});

test("seksjonsfaner: under tre seksjoner er det ingen faner", async () => {
  const { hoved, liste, stopp } = flate(["Sammendrag", "Funn"]);
  await tikk();
  assert.equal(hoved.querySelector(".seksjonsfaner"), null);
  assert.deepEqual(synlige(liste), ["Sammendrag", "Funn"]);
  stopp();
});

test("seksjonsfaner: samme tittel to ganger er ÉN fane som viser begge", async () => {
  // Lista «Modeller» og skjemaet «Modeller» er samme emne for brukeren.
  const { hoved, liste, stopp } = flate(["Sammendrag", "Funn", "Modeller", "Modeller"]);
  await tikk();
  assert.deepEqual(faner(hoved).map((f) => f.textContent),
    ["Sammendrag", "Funn", "Modeller"]);
  faner(hoved)[2].click();
  assert.deepEqual(synlige(liste), ["Modeller", "Modeller"]);
  assert.equal(faner(hoved)[2].getAttribute("aria-controls").split(" ").length, 2);
  stopp();
});

test("seksjonsfaner: en endring INNE i en seksjon beholder fanesettet", async () => {
  // 🔴 CodeRabbit (major). Flaten bytter en tabell inne i den synlige
  // seksjonen; uten vernet i `seksjonene` ville de andre — skjult av
  // fanene, ikke av flaten — falt ut, og faneraden krympet til én.
  //
  // MUTASJONEN SOM DREPER DENNE: filtrer på `!n.hidden` alene.
  const { hoved, liste, stopp } = flate(["Sammendrag", "Funn", "Prognoser"]);
  await tikk();
  faner(hoved)[1].click();
  liste.children[1].append(el("table", {}, el("tr", {}, el("td", { text: "ny rad" }))));
  await tikk();
  assert.equal(faner(hoved).length, 3, "faneraden krympet etter en indre endring");
  assert.deepEqual(synlige(liste), ["Funn"], "valgt fane overlevde ikke endringen");
  stopp();
});

test("seksjonsfaner: flatens egne skjulte paneler og hodeløse bokser er ikke faner",
  async () => {
  const { hoved, liste, stopp } = flate(["Sammendrag", "Funn", "Prognoser"]);
  // Et panel flaten selv holder skjult til en knapp åpner det, og en
  // detaljboks uten overskrift: begge står der flaten la dem.
  const panel = el("section", { class: "kpi-kort", hidden: true },
    el("h2", { text: "Lukk funn" }));
  const boks = el("div", { class: "skjemaboks" }, el("div", { hidden: true }));
  liste.append(panel, boks);
  await tikk();
  assert.deepEqual(faner(hoved).map((f) => f.textContent),
    ["Sammendrag", "Funn", "Prognoser"]);
  assert.equal(panel.hidden, true);
  assert.equal(boks.hidden, false);
  // Flaten åpner panelet sitt: det synes, uansett hvilken fane som står.
  panel.hidden = false;
  faner(hoved)[2].click();
  assert.equal(panel.hidden, false);
  stopp();
});

test("seksjonsfaner: en ny tegning fra flaten husker valgt fane på tittel", async () => {
  const { hoved, liste, stopp } = flate(["Sammendrag", "Funn", "Prognoser"]);
  await tikk();
  faner(hoved)[2].click();
  // Flaten tegner alt på nytt inn i SAMME liste-node (slik `sett(kropp)` gjør).
  liste.replaceChildren(...["Sammendrag", "Funn", "Prognoser"].map((tt) => seksjon(tt)));
  await tikk();
  assert.deepEqual(synlige(liste), ["Prognoser"]);
  assert.equal(hoved.querySelectorAll(".seksjonsfaner").length, 1, "to fanerader");
  stopp();
});

test("seksjonsfaner: fokus på en fane overlever at flaten tegner om", async () => {
  // CodeRabbit (major). Tastaturbrukeren står på fanen; flaten bytter alt
  // under; knappen hun sto på er borte. Den nye radens valgte fane tar
  // over — uten dette falt fokus til <body>, og hun måtte tabbe inn igjen.
  const { hoved, liste, stopp } = flate(["Sammendrag", "Funn", "Prognoser"]);
  await tikk();
  faner(hoved)[1].focus();
  assert.equal(document.activeElement, faner(hoved)[1]);
  liste.replaceChildren(...["Sammendrag", "Funn", "Prognoser"].map((tt) => seksjon(tt)));
  await tikk();
  assert.equal(document.activeElement, faner(hoved)[1],
    "fokus falt ut av faneraden da flaten tegnet om");
  // …og en omtegning mens fokus står et ANNET sted rykker ikke fokus.
  const knapp = el("button", { type: "button", text: "Annet" });
  hoved.append(knapp);
  await tikk();
  knapp.focus();
  liste.replaceChildren(...["Sammendrag", "Funn", "Prognoser"].map((tt) => seksjon(tt)));
  await tikk();
  assert.equal(document.activeElement, knapp, "omtegningen stjal fokus");
  stopp();
});

test("seksjonsfaner: h3-seksjoner og piltaster", async () => {
  const brett = nyttBrett();
  const hoved = el("main");
  brett.append(hoved);
  const stopp = seksjonsfaner(hoved);
  hoved.append(el("div", { class: "kpi-kort-liste" },
    ["A", "B", "C"].map((tt) => seksjon(tt, "h3"))));
  await tikk();
  const f = faner(hoved);
  assert.equal(f.length, 3);
  f[0].focus();
  f[0].dispatchEvent(new window.KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  assert.equal(f[1].getAttribute("aria-selected"), "true");
  assert.equal(document.activeElement, f[1]);
  f[1].dispatchEvent(new window.KeyboardEvent("keydown", { key: "End", bubbles: true }));
  assert.equal(f[2].getAttribute("aria-selected"), "true");
  stopp();
});

test("seksjonsfaner: null alvorlige axe-brudd", async () => {
  const { hoved, stopp } = flate(["Sammendrag", "Funn", "Prognoser"]);
  await tikk();
  const brudd = await alvorligeBrudd(hoved);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  stopp();
});

test("seksjonsfaner: stopp() slutter å lytte", async () => {
  const { hoved, liste, stopp } = flate(["Sammendrag", "Funn", "Prognoser"]);
  await tikk();
  stopp();
  liste.append(seksjon("Etterpå"));
  await tikk();
  assert.equal(faner(hoved).length, 3, "lytteren lever etter stopp()");
});
