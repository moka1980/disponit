// Vilkår, personvern og databehandleravtale (235).
//
// To ting her:
//   * `tekstTilNoder` — en LITEN markdown-visning (overskrifter, avsnitt,
//     lister, tabeller, **fet** og *kursiv*) som bygger DOM-noder med
//     `el(...)`, ALDRI `innerHTML`. Teksten kommer fra basen; en visning
//     som tolket HTML i den, ville vært en vei inn for den som kan skrive
//     teksten. Brukes både av den offentlige siden og av forhåndsvisningen
//     i plattformadmin, så eieren ser NØYAKTIG det kunden vil se.
//   * den frittstående siden `/vilkar` m.fl.
import { el, sett } from "../dom.js";
import { hentI18n, t, velgSprak } from "../i18n.js";

export const DOKUMENTER = ["vilkar", "personvern", "databehandleravtale"];

function inline(tekst) {
  // **fet** og *kursiv*, resten som tekst. Ingen lenker eller HTML.
  const ut = [];
  const re = /(\*\*([^*]+)\*\*|\*([^*]+)\*)/g;
  let sist = 0;
  let m;
  while ((m = re.exec(tekst)) !== null) {
    if (m.index > sist) ut.push(tekst.slice(sist, m.index));
    ut.push(m[2] !== undefined ? el("strong", { text: m[2] })
      : el("em", { text: m[3] }));
    sist = re.lastIndex;
  }
  if (sist < tekst.length) ut.push(tekst.slice(sist));
  return ut;
}

function tabell(linjer) {
  const celler = (l) => l.trim().replace(/^\||\|$/g, "").split("|")
    .map((c) => c.trim());
  const [hode, , ...rader] = linjer;
  return el("div", { class: "tablewrap" },
    el("table", { class: "tabell" },
      el("thead", {}, el("tr", {},
        ...celler(hode).map((c) => el("th", { scope: "col" }, ...inline(c))))),
      el("tbody", {}, ...rader.map((r) => el("tr", {},
        ...celler(r).map((c) => el("td", {}, ...inline(c))))))));
}

export function tekstTilNoder(tekst) {
  const ut = [];
  const linjer = String(tekst || "").replace(/\r\n/g, "\n").split("\n");
  let i = 0;
  while (i < linjer.length) {
    const l = linjer[i];
    if (!l.trim()) { i += 1; continue; }
    const h = /^(#{1,3})\s+(.*)$/.exec(l);
    if (h) {
      // Dokumentets #-tittel blir h1; ## og ### rykker etter.
      ut.push(el(`h${h[1].length}`, {}, ...inline(h[2])));
      i += 1; continue;
    }
    if (l.trim().startsWith("|")) {
      const blokk = [];
      while (i < linjer.length && linjer[i].trim().startsWith("|")) {
        blokk.push(linjer[i]); i += 1;
      }
      if (blokk.length >= 2) ut.push(tabell(blokk));
      continue;
    }
    const pkt = /^\s*([-*]|\d+\.)\s+/;
    if (pkt.test(l)) {
      const nummerert = /^\s*\d+\./.test(l);
      const liste = el(nummerert ? "ol" : "ul");
      while (i < linjer.length && pkt.test(linjer[i])) {
        let punkt = linjer[i].replace(pkt, "");
        i += 1;
        // Fortsettelseslinjer (innrykket) hører til punktet.
        while (i < linjer.length && /^\s{2,}\S/.test(linjer[i])
               && !pkt.test(linjer[i])) {
          punkt += " " + linjer[i].trim(); i += 1;
        }
        liste.append(el("li", {}, ...inline(punkt)));
      }
      ut.push(liste);
      continue;
    }
    // Avsnitt: sammenhengende linjer til en tom linje eller en ny blokk.
    let avsnitt = l.trim();
    i += 1;
    while (i < linjer.length && linjer[i].trim()
           && !/^(#{1,3}\s|\s*([-*]|\d+\.)\s|\s*\|)/.test(linjer[i])) {
      avsnitt += " " + linjer[i].trim(); i += 1;
    }
    ut.push(el("p", {}, ...inline(avsnitt)));
  }
  return ut;
}

export function dokFraSti(sti) {
  const d = String(sti || "").replace(/^\/+|\/+$/g, "");
  return DOKUMENTER.includes(d) ? d : null;
}

export async function start(rot) {
  const s = await hentI18n(velgSprak());
  s.taIBruk();
  const dok = dokFraSti(window.location.pathname);
  rot.setAttribute("aria-busy", "false");
  if (!dok) {
    sett(rot, el("p", { text: t("ui.juridisk.ukjent") }));
    return;
  }
  let d = null;
  try {
    const r = await fetch(`/v1/juridisk/${dok}`, {
      headers: { accept: "application/json" }, credentials: "omit" });
    d = r.ok ? await r.json() : null;
  } catch { d = null; }
  if (!d || !d.tekst) {
    sett(rot, el("h1", { text: t(`ui.juridisk.dok.${dok}`) }),
      el("p", { text: t(d ? "ui.juridisk.ikke_publisert"
        : "ui.juridisk.feil") }));
    return;
  }
  sett(rot, el("article", { class: "juridisk-tekst" },
      ...tekstTilNoder(d.tekst)),
    el("p", { class: "muted juridisk-versjon",
      text: t("ui.juridisk.versjon").replace("{v}", String(d.versjon))
        .replace("{dato}", d.publisert.slice(0, 10).split("-").reverse()
          .join(".")) }),
    el("nav", { class: "juridisk-lenker", "aria-label":
      t("ui.juridisk.andre") }, ...DOKUMENTER.filter((x) => x !== dok)
      .map((x) => el("a", { href: `/${x}`, text: t(`ui.juridisk.dok.${x}`) }))));
  document.title = `${t(`ui.juridisk.dok.${dok}`)} – Disponit`;
}

const rot = typeof document !== "undefined"
  ? document.getElementById("juridisk-rot") : null;
if (rot) start(rot);
