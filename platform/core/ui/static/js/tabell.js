// DataTabell — logg/kø-lister (v1 §3). WCAG: <caption>, th scope=col,
// fungerende sortering via aria-sort på et tastaturbetjent knappe-th, og
// radhandling som KNAPP i en celle (aldri <tr onclick> — v2 §6). Sorteringen
// gjelder de innlastede radene; «Vis mer» (CursorNavigasjon) legger til flere
// og re-sorterer med gjeldende nøkkel.
import { el, sett } from "./dom.js";
import { t } from "./i18n.js";

// Sorteringen er BRUKERENS VALG, og den overlevde ikke at tabellen ble bygget
// på nytt (Codex P2): `sortNokkel` startet på `null` i hver konstruksjon, mens
// flatene rundt rekonstruerer ved enhver ny tegning — «Tilbake» fra en detalj,
// et filterbytte, «Vis mer». Sorterte man på tidspunkt og åpnet en rad, sto man
// i serverrekkefølgen igjen da man kom tilbake, og måtte sortere på nytt for
// hver rad man så på. (Filkommentaren over lovet til og med det motsatte for
// «Vis mer».)
//
// Tabellen kan ikke eie valget selv — den er borte ved neste tegning. Derfor
// tar den imot `sort` som utgangspunkt og melder fra via `paaSort`, slik at
// flaten som overlever tegningene kan holde det. Uten dem er oppførselen som
// før: usortert.
export function DataTabell({ captionTekst, kolonner, rader,
                            handlingTittel, sort, paaSort } = {}) {
  // En rad kan bære ÉN handling (`handling`) eller FLERE side ved side
  // (`handlinger`) — eiers krav: «slett, endre og åpne skal stå ved siden av
  // hverandre». Normaliseringen skjer her, så resten av tabellen har ett
  // begrep.
  const radHandlinger = (r) =>
    r.handlinger || (r.handling ? [r.handling] : []);
  const harHandling = rader.some((r) => radHandlinger(r).length);
  // En nøkkel som ikke lenger er en sorterbar kolonne, ignoreres: kolonnesettet
  // kan ha endret seg siden valget ble tatt, og en usynlig sortering ingen
  // `aria-sort` peker på er verre enn ingen.
  const sorterbare = kolonner.filter((k) => k.sorterbar).map((k) => k.nokkel);
  let sortNokkel = sort && sorterbare.includes(sort.nokkel) ? sort.nokkel : null;
  let sortRetning = sort && sort.retning === "descending"
    ? "descending" : "ascending";

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
      // Radklassen er RADENS egenskap (f.eks. rangeringssjiktet), ikke
      // posisjonens: den følger kandidaten uansett hvilken kolonne og
      // retning leseren sorterer på.
      const tr = el("tr", r.radKlasse ? { class: r.radKlasse } : {});
      for (const kol of kolonner) {
        const innhold = r.celler[kol.nokkel];
        tr.append(el("td", {},
          innhold == null ? "—"
            : (innhold.nodeType ? innhold : String(innhold))));
      }
      if (harHandling) {
        const celle = el("td", { class: "handling-celle" });
        for (const h of radHandlinger(r)) {
          const b = el("button", {
            class: `knapp lenkeknapp${h.farlig ? " fare" : ""}`,
            type: "button", text: h.tekst || t("ui.aapne"),
            // To rader kan bære samme knappetekst («Slett», «Slett») — for
            // skjermleseren må hver si HVILKEN rad den gjelder.
            ...(h.tilgjengeligNavn
              ? { "aria-label": h.tilgjengeligNavn } : {}) });
          b.addEventListener("click", h.paaKlikk);
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
          if (paaSort) paaSort({ nokkel: sortNokkel, retning: sortRetning });
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
  // Indikatorene settes også ved konstruksjon: et gjenopprettet valg som sorterte
  // radene, men lot hver `aria-sort` stå på «none», ville sagt til skjermleseren
  // at tabellen er usortert mens den ikke er det.
  oppdaterSortIndikatorer();
  tegnKropp();
  return el("div", { class: "tablewrap" },
    el("table", {},
      captionTekst
        ? el("caption", { class: "sr-only", text: captionTekst }) : null,
      thead, tbody));
}
