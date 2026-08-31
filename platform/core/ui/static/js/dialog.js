// Detaljpanel (skuff) + Bekreftelsesdialog. Ekte modal dialog (v2 §6):
// role=dialog, aria-modal, fokus flyttes inn, fokusfelle, ESC lukker,
// bakgrunnen settes `inert`, og fokus RETURNERES til åpneren ved lukking.
import { el } from "./dom.js";
import { t } from "./i18n.js";

// `iframe` er med (CodeRabbit på dokumentvisningen): rammene er
// fokuserbare i nettleseren, og sto de utenfor selektoren gikk
// Tab-fella i dokumentpanelet UTENOM selve dokumentet.
const FOKUSERBAR =
  'a[href],button:not([disabled]),input:not([disabled]),' +
  'select:not([disabled]),textarea:not([disabled]),iframe,' +
  '[tabindex]:not([tabindex="-1"])';

function fokuserbare(rot) {
  // Dialogen inneholder kun synlige fokuserbare; vi filtrerer ikke på
  // layout (offsetParent/getClientRects er tomme i jsdom, og en slik filter
  // ville tømt fella i test uten å beskytte noe i praksis). Skjulte felt
  // med hidden/disabled fanges av selektoren selv.
  return Array.from(rot.querySelectorAll(FOKUSERBAR))
    .filter((n) => !n.hasAttribute("hidden"));
}

function bakgrunnsnode() { return document.getElementById("app") || document.body; }

let _dlgTeller = 0;

// Generisk modal. `innhold` er en node; `handlinger` er valgfrie knapper.
export function aapneDialog({ tittel, innhold, klasse = "", handlinger = [],
                             rolle = "dialog", beskrivelseId = null,
                             paaLukk = null }) {
  const aapner = document.activeElement;
  const bakgrunn = bakgrunnsnode();
  const tittelId = `dlg-tittel-${++_dlgTeller}`;

  const lukkeknapp = el("button", { class: "dialog-lukk", type: "button",
    "aria-label": t("ui.lukk") },
    el("span", { "aria-hidden": "true", text: "✕" }));

  const dialog = el("div", { class: `dialog ${klasse}`.trim(), role: rolle,
    "aria-modal": "true", "aria-labelledby": tittelId,
    ...(beskrivelseId ? { "aria-describedby": beskrivelseId } : {}) },
    el("div", { class: "dialog-topp" },
      el("h2", { id: tittelId, class: "dialog-tittel", text: tittel }),
      lukkeknapp),
    el("div", { class: "dialog-kropp" }, innhold),
    handlinger.length
      ? el("div", { class: "dialog-bunn" }, ...handlinger) : null);

  const overlegg = el("div", { class: "overlegg" }, dialog);

  function paaTast(e) {
    if (e.key === "Escape") { e.preventDefault(); lukk(); return; }
    if (e.key !== "Tab") return;
    const f = fokuserbare(dialog);
    if (!f.length) { e.preventDefault(); return; }
    const forste = f[0], siste = f[f.length - 1];
    if (e.shiftKey && document.activeElement === forste) {
      e.preventDefault(); siste.focus();
    } else if (!e.shiftKey && document.activeElement === siste) {
      e.preventDefault(); forste.focus();
    }
  }

  function lukk() {
    document.removeEventListener("keydown", paaTast, true);
    overlegg.remove();
    bakgrunn.removeAttribute("inert");
    if (aapner && typeof aapner.focus === "function") aapner.focus();
    // Opprydding ETTER at dialogen er borte fra DOM — alle lukkeveier
    // (knapp, ESC, overlegg, ctrl.lukk) ender her, så en kaller som
    // holder en ressurs for innholdet (f.eks. en blob-URL) har nøyaktig
    // ett sted å slippe den.
    if (typeof paaLukk === "function") paaLukk();
  }

  lukkeknapp.addEventListener("click", lukk);
  overlegg.addEventListener("mousedown", (e) => { if (e.target === overlegg) lukk(); });
  document.addEventListener("keydown", paaTast, true);

  bakgrunn.setAttribute("inert", "");
  document.body.append(overlegg);
  // Fokus inn: første fokuserbare, ellers dialogen selv.
  const f = fokuserbare(dialog);
  (f[0] || lukkeknapp).focus();
  return { lukk };
}

// Detaljpanel: skuff-varianten (beslutnings-/unntaksdetalj).
export function Detaljpanel({ tittel, innhold, paaLukk = null }) {
  return aapneDialog({ tittel, innhold, klasse: "skuff", paaLukk });
}

// Bekreftelsesdialog: beskriver konsekvens; primær/avbryt; ESC + fokusretur.
// `detaljer` er en valgfri node under setningen — for de bekreftelsene der
// konsekvensen ikke lar seg si i én linje og må VISES (f.eks. en policy-diff).
// `valider` er en VALGFRI port FØR lukkingen (Codex P2): returnerer den
// usant, blir dialogen stående, og brukeren beholder feltet sitt. Uten
// den lukket dialogen seg synkront før `paaPrimar` i det hele tatt kjørte,
// så en callback som fant en manglende obligatorisk verdi meldte fra om
// et felt som allerede var borte fra skjermen — og brukeren måtte finne
// veien tilbake for å rette det. `required` på et felt utenfor en <form>
// hindrer ikke lukkingen; det gjør denne porten.
export function Bekreftelsesdialog({ tittel, tekst, detaljer, primarTekst,
                                    paaPrimar, valider, farlig = false,
                                    rolle = "dialog" } = {}) {
  const avbryt = el("button", { class: "knapp", type: "button",
    text: t("ui.avbryt") });
  const primar = el("button", {
    class: `knapp ${farlig ? "fare" : "primar"}`, type: "button",
    text: primarTekst || t("ui.logg_ut_bekreft_primar") });
  // 043: en alertdialog SKAL peke på budskapet sitt (aria-describedby) —
  // det er advarselen, ikke tittelen, skjermleseren skal åpne med.
  const beskrivelseId = `dlg-beskrivelse-${Date.now()}-${Math.floor(
    Math.random() * 1e6)}`;
  const ctrl = aapneDialog({
    tittel, klasse: "bekreft", rolle,
    beskrivelseId: rolle === "alertdialog" ? beskrivelseId : null,
    innhold: el("div", {},
      el("p", { id: beskrivelseId, text: tekst }), detaljer || null),
    handlinger: [avbryt, primar],
  });
  avbryt.addEventListener("click", ctrl.lukk);
  primar.addEventListener("click", () => {
    if (valider && !valider()) return;      // dialogen blir stående
    ctrl.lukk();
    if (paaPrimar) paaPrimar();
  });
  return ctrl;
}
