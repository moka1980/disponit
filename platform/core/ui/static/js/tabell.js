// DataTabell — logg/kø-lister (v1 §3). WCAG: <caption>, th scope=col,
// fungerende sortering via aria-sort på et tastaturbetjent knappe-th, og
// radhandling som KNAPP i en celle (aldri <tr onclick> — v2 §6). Sorteringen
// gjelder de innlastede radene; «Vis mer» (CursorNavigasjon) legger til flere
// og re-sorterer med gjeldende nøkkel.
import { el, sett } from "./dom.js";
import { t } from "./i18n.js";

export function DataTabell({ captionTekst, kolonner, rader,
                            handlingTittel } = {}) {
  const harHandling = rader.some((r) => r.handling);
  let sortNokkel = null;
  let sortRetning = "ascending";

  const thead = el("thead");
  const tbody = el("tbody");

  function sammenlign(a, b) {
    const va = a.sortverdi ? a.sortverdi[sortNokkel] : undefined;
    const vb = b.sortverdi ? b.sortverdi[sortNokkel] : undefined;
    if (va < vb) return sortRetning === "ascending" ? -1 : 1;
    if (va > vb) return sortRetning === "ascending" ? 1 : -1;
    return 0;
  }

  function tegnKropp() {
    const liste = rader.slice();
    if (sortNokkel) liste.sort(sammenlign);
    sett(tbody, liste.map((r) => {
      const tr = el("tr");
      for (const kol of kolonner) {
        const innhold = r.celler[kol.nokkel];
        tr.append(el("td", {},
          innhold == null ? "—"
            : (innhold.nodeType ? innhold : String(innhold))));
      }
      if (harHandling) {
        const celle = el("td", { class: "handling-celle" });
        if (r.handling) {
          const b = el("button", { class: "knapp lenkeknapp", type: "button",
            text: r.handling.tekst || t("ui.aapne") });
          b.addEventListener("click", r.handling.paaKlikk);
          celle.append(b);
        }
        tr.append(celle);
      }
      return tr;
    }));
  }

  // th-referanser beholdes: ved sortering OPPDATERES aria-sort på plass og
  // kun tbody tegnes på nytt, så fokus blir på sorteringsknappen (en re-
  // bygging av thead ville kastet keyboard-fokus — et reelt a11y-tap).
  const sortbareTh = new Map();

  function oppdaterSortIndikatorer() {
    for (const [nokkel, th] of sortbareTh) {
      th.setAttribute("aria-sort",
        sortNokkel === nokkel ? sortRetning : "none");
    }
  }

  function byggHode() {
    const tr = el("tr");
    for (const kol of kolonner) {
      const th = el("th", { scope: "col" });
      if (kol.sorterbar) {
        th.setAttribute("aria-sort", "none");
        sortbareTh.set(kol.nokkel, th);
        const b = el("button", { class: "sort-knapp", type: "button",
          text: kol.tittel });
        b.addEventListener("click", () => {
          if (sortNokkel === kol.nokkel) {
            sortRetning = sortRetning === "ascending" ? "descending" : "ascending";
          } else { sortNokkel = kol.nokkel; sortRetning = "ascending"; }
          oppdaterSortIndikatorer(); tegnKropp();
        });
        th.append(b);
      } else {
        th.textContent = kol.tittel;
      }
      tr.append(th);
    }
    if (harHandling) {
      tr.append(el("th", { scope: "col" },
        el("span", { class: "sr-only",
          text: handlingTittel || t("ui.detaljer") })));
    }
    sett(thead, tr);
  }

  byggHode();
  tegnKropp();
  return el("div", { class: "tablewrap" },
    el("table", {},
      captionTekst
        ? el("caption", { class: "sr-only", text: captionTekst }) : null,
      thead, tbody));
}
