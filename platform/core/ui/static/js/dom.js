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
