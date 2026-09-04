// Trygg DOM-bygging (klarsignal V6): API- og locale-tekst går ALLTID inn som
// tekstnode via `text:` eller barn — ALDRI innerHTML. Det finnes ingen
// innerHTML-vei i dette modultreet; en statisk grep-port håndhever det.

export function el(tag, attrs = {}, ...barn) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "text") n.textContent = v;      // eneste vei for dynamisk tekst
    else if (k === "html") throw new Error("innerHTML er forbudt (V6)");
    else n.setAttribute(k, v === true ? "" : String(v));
  }
  for (const b of barn.flat()) {
    if (b == null || b === false) continue;
    n.append(b.nodeType ? b : document.createTextNode(String(b)));
  }
  return n;
}

// Tøm og sett nytt innhold uten innerHTML.
export function sett(node, ...barn) {
  node.replaceChildren();
  for (const b of barn.flat()) {
    if (b == null || b === false) continue;
    node.append(b.nodeType ? b : document.createTextNode(String(b)));
  }
  return node;
}


// --------------------------------------------------------------------
// IKONER.
// --------------------------------------------------------------------
//
// `el()` bruker `createElement`, som ikke kan lage SVG — et `<svg>` laget
// slik havner i HTML-navnerommet og tegnes ikke. Derfor denne egne veien.
//
// EMOJI ER IKKE IKONER. De er skriftavhengige, ser forskjellige ut på
// hver plattform, kan ikke styres av designtokens, og leses opp med sitt
// eget navn av skjermlesere. Et navigasjonsikon skal være en vektor.
//
// STIENE STÅR I EN TABELL HER, ikke i kallet: en `d`-attributt som kommer
// utenfra ville vært en vei til vilkårlig SVG i dokumentet, og det er
// nøyaktig den døra V6 stengte for HTML.
const SVGNS = "http://www.w3.org/2000/svg";

export const IKONSTIER = {
  // Alle 24×24, `stroke`-baserte, samme visuelle vekt.
  oversikt: "M3 12h6v9H3zM10 3h4v18h-4zM15 8h6v13h-6z",
  moduler: "M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z",
  varsler: "M12 3a6 6 0 0 0-6 6v4l-2 3h16l-2-3V9a6 6 0 0 0-6-6zM9 19a3 3 0 0 0 6 0",
  mer: "M5 12h.01M12 12h.01M19 12h.01",
};

// `aria-hidden` fordi ikonet ALDRI står alene: hver oppføring bærer sin
// egen tekst ved siden av (skillens `nav-label-icon`), og et ikon som
// også leses opp ville sagt det samme to ganger.
export function ikon(navn, { storrelse = 24 } = {}) {
  const d = IKONSTIER[navn];
  if (!d) throw new Error(`ukjent ikon: ${navn}`);
  const s = document.createElementNS(SVGNS, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("width", String(storrelse));
  s.setAttribute("height", String(storrelse));
  s.setAttribute("fill", "none");
  s.setAttribute("stroke", "currentColor");
  s.setAttribute("stroke-width", "1.75");
  s.setAttribute("stroke-linecap", "round");
  s.setAttribute("stroke-linejoin", "round");
  s.setAttribute("aria-hidden", "true");
  s.setAttribute("focusable", "false");
  s.classList.add("ikon");
  const p = document.createElementNS(SVGNS, "path");
  p.setAttribute("d", d);
  s.append(p);
  return s;
}
