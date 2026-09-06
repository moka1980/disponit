// Seksjonsfaner — en lang flate blir faner (eiers krav 6/9: «unngå lange
// lister og deling med tabeller, bruk fanemeny»).
//
// 48 av flatene tegner innholdet sitt som en stabel av `section.kpi-kort`
// med hver sin `h2` inne i én `.kpi-kort-liste`: sammendrag, funn,
// prognoser, tiltak, poster, modeller — og så tre–fire skjemaer. På en
// telefon er det en rulle på flere skjermhøyder før den siste handlingen.
//
// Denne modulen rører IKKE flatene. Den ser på det de tegnet, og når en
// liste har minst `MINST` slike seksjoner, legger den en fanerad (WAI-ARIA
// tabs) foran lista og viser ÉN seksjon om gangen. Flatens egne
// `hidden`-paneler (skjemaer som åpnes fra en knapp) er ikke seksjoner i
// denne forstand: de var skjult da fanene ble bygget, og de får stå der
// flaten legger dem, uansett hvilken fane som er valgt.
//
// Tegnes flaten på nytt — og det gjør de fleste etter hver handling —
// bygges fanene på nytt av det som nå står der, og den valgte fanen
// huskes på TITTEL, ikke på node: noden er ny, tittelen er den samme.
//
// Ingen innerHTML (V6), ingen tekst utenom locale (ui.shell.seksjoner).
import { el } from "./dom.js";
import { t } from "./i18n.js";

const MINST = 3;
let teller = 0;

// Tittelen er seksjonens egen overskrift: `h2` i de fleste flatene, `h3`
// i de femten som nivådeler under en felles h2.
function overskrift(seksjon) {
  return seksjon.querySelector(":scope > h2, :scope > h3");
}

// En seksjon er ethvert direkte barn av lista som bærer sin egen
// overskrift: `section.kpi-kort` (lesedelene) og skjemaboksene med `h3`
// (skrivedelene). Et barn uten overskrift — detaljpanelet som fylles ved
// klikk, en kvitteringslinje — er ikke en fane og står der flaten la det.
// SEKSJONENE FANENE SELV SKJULTE TELLER MED (CodeRabbit). En flate som
// bytter en tabell inne i én seksjon utløser en ny tegning, og uten dette
// leddet ville de andre seksjonene — skjult av fanene, ikke av flaten —
// falt ut av settet, og faneraden krympet til den ene som sto framme.
function seksjonene(liste) {
  return [...liste.children].filter((n) =>
    n.nodeType === 1 && overskrift(n)
    && (!n.hidden || n.dataset.seksjonsfane === "skjult"));
}

function tittelFor(seksjon) {
  return (overskrift(seksjon).textContent || "").trim();
}

export function seksjonsfaner(hoved) {
  // Observatøren hentes fra dokumentets EGET vindu: i testriggen finnes
  // den på jsdom-vinduet, ikke som global, og en modul som leter i
  // globalen ville stille gjort ingenting nettopp der portene måler den.
  const vindu = hoved.ownerDocument && hoved.ownerDocument.defaultView;
  const Observator = (vindu && vindu.MutationObserver)
    || (typeof MutationObserver !== "undefined" ? MutationObserver : null);
  if (!Observator) return () => {};
  // Valgt tittel per liste-node. Flatene gjenbruker `kropp`-noden mellom
  // tegninger, så valget overlever en oppfrisking av innholdet.
  const valgt = new WeakMap();
  let travelt = false;
  let planlagt = false;

  function tegnListe(liste) {
    const seksjoner = seksjonene(liste);
    const gammel = liste.previousElementSibling;
    const harFaner = gammel && gammel.classList.contains("seksjonsfaner");
    if (seksjoner.length < MINST) {
      if (harFaner) gammel.remove();
      delete liste.dataset.faner;
      for (const s of [...liste.children]) {
        if (s.nodeType === 1 && s.dataset.seksjonsfane === "skjult") {
          s.hidden = false; delete s.dataset.seksjonsfane;
        }
      }
      return;
    }
    const perSeksjon = seksjoner.map(tittelFor);
    // ÉN FANE PER TITTEL. «Modeller» (lista) og «Modeller» (skjemaet som
    // fyller den) er samme emne for brukeren; fanen viser begge.
    const titler = [...new Set(perSeksjon)];
    let aktiv = valgt.get(liste);
    if (!titler.includes(aktiv)) aktiv = titler[0];
    valgt.set(liste, aktiv);

    const merke = `seksjonsfaner${++teller}`;
    const knapper = [];
    const tablist = el("div", { class: "seksjonsfaner-liste", role: "tablist",
      "aria-label": t("ui.shell.seksjoner") });

    function vis(tittel, flyttFokus) {
      valgt.set(liste, tittel);
      travelt = true;
      try {
        seksjoner.forEach((s, i) => {
          const her = perSeksjon[i] === tittel;
          s.hidden = !her;
          s.dataset.seksjonsfane = her ? "vist" : "skjult";
        });
        titler.forEach((tt, i) => {
          const her = tt === tittel;
          knapper[i].setAttribute("aria-selected", her ? "true" : "false");
          knapper[i].setAttribute("tabindex", her ? "0" : "-1");
        });
        if (flyttFokus) knapper[titler.indexOf(tittel)].focus();
      } finally { travelt = false; }
    }

    seksjoner.forEach((s, i) => {
      if (!s.id) s.id = `${merke}-panel-${i}`;
      s.setAttribute("role", "tabpanel");
      s.setAttribute("aria-labelledby",
        `${merke}-fane-${titler.indexOf(perSeksjon[i])}`);
    });
    titler.forEach((tittel, i) => {
      const faneId = `${merke}-fane-${i}`;
      const paneler = seksjoner.filter((s, j) => perSeksjon[j] === tittel)
        .map((s) => s.id).join(" ");
      const kn = el("button", { type: "button", class: "seksjonsfane",
        id: faneId, role: "tab", "aria-controls": paneler, text: tittel });
      kn.addEventListener("click", () => vis(tittel, false));
      kn.addEventListener("keydown", (e) => {
        let ny = null;
        if (e.key === "ArrowRight") ny = (i + 1) % titler.length;
        else if (e.key === "ArrowLeft") ny = (i - 1 + titler.length) % titler.length;
        else if (e.key === "Home") ny = 0;
        else if (e.key === "End") ny = titler.length - 1;
        if (ny === null) return;
        e.preventDefault();
        vis(titler[ny], true);
      });
      knapper.push(kn);
      tablist.append(kn);
    });

    const rad = el("div", { class: "seksjonsfaner" }, tablist);
    // FOKUS FØLGER MED OVER EN NY TEGNING (CodeRabbit). Sto tastaturet på
    // en fane da flaten tegnet om, er den knappen borte fra dokumentet og
    // fokus faller til <body>. Den valgte fanen i den nye raden tar over.
    const aktivtElement = liste.ownerDocument.activeElement;
    const fokusTittel = harFaner && gammel.contains(aktivtElement)
      ? (aktivtElement.textContent || "").trim() : null;
    if (harFaner) gammel.replaceWith(rad);
    else liste.before(rad);
    liste.dataset.faner = "ja";
    vis(aktiv, false);
    if (fokusTittel !== null) {
      // Samme fane som før om den finnes, ellers den valgte.
      const i = titler.indexOf(fokusTittel);
      knapper[i >= 0 ? i : titler.indexOf(aktiv)].focus();
    }
  }

  function tegn() {
    if (!hoved.isConnected) return;
    travelt = true;
    try {
      const lister = [...hoved.querySelectorAll(".kpi-kort-liste")];
      for (const liste of lister) tegnListe(liste);
      // En fanerad som mistet lista si (flaten byttet innhold) skal bort.
      for (const rad of hoved.querySelectorAll(".seksjonsfaner")) {
        const neste = rad.nextElementSibling;
        if (!neste || !neste.classList.contains("kpi-kort-liste")) rad.remove();
      }
    } finally {
      mo.takeRecords();
      travelt = false;
    }
  }

  function planlegg() {
    if (travelt || planlagt) return;
    planlagt = true;
    queueMicrotask(() => { planlagt = false; tegn(); });
  }

  const mo = new Observator(planlegg);
  mo.observe(hoved, { childList: true, subtree: true });
  return () => mo.disconnect();
}
