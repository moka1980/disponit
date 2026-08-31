// Kandidatens tidsvalg (M-8, 082 — planen §4): frittstående flate
// utenfor SPA-skallet. Ingen innlogging, ingen nav, ingen cookies —
// kapabilitetstokenet i URL-FRAGMENTET er hele identiteten, og det
// forlater aldri klienten: flaten leser `location.hash` og POSTer
// tokenet i kroppen (fetch uten credentials).
//
// WCAG 2.1 AA fra første strek (§0): fieldset/legend med radio per
// ledig slot (tiden som tekst), fulle slots disabled MED tekstlig
// «fullt» (DOM 4: binært ledig/fullt, aldri tellere), bekreftelsen i
// role=status med valgt tid gjentatt, og den uniforme avvisningen i
// role=alert uten årsaksskille. Tastaturkomplett: radioer og knapp er
// native kontroller.
import { el, sett } from "../dom.js";
import { hentI18n, t, velgSprak } from "../i18n.js";

// Samme form som serverens dør: tid_ + 32 hex + . + 64 hex. Et token
// som ikke engang har formen dømmes lokalt som avvist — samme utfall
// som serveren ville gitt, uten et kall.
const TOKENMONSTER = /^tid_[0-9a-f]{32}\.[0-9a-f]{64}$/;

function tidspunkt(iso) {
  let vis = iso;
  try {
    vis = new Intl.DateTimeFormat("nb-NO",
      { dateStyle: "full", timeStyle: "short" }).format(new Date(iso));
  } catch { /* behold iso som fallback */ }
  return el("time", { datetime: iso, text: vis });
}

async function post(sti, kropp) {
  const r = await fetch(sti, {
    method: "POST",
    credentials: "omit",              // kapabiliteten ER credentialet
    headers: { "content-type": "application/json",
               accept: "application/json" },
    body: JSON.stringify(kropp),
    redirect: "error",
  });
  let b = null;
  try { b = await r.json(); } catch { b = null; }
  return { ok: r.ok, status: r.status, kropp: b };
}

function avvist(rot) {
  // ÉN tekst for hele avvisningsklassen (§2): ukjent, utløpt,
  // erstattet, reapet, lukket vindu — flaten vet ikke hvilken, og skal
  // ikke vite det.
  sett(rot,
    el("h1", { text: t("ui.tidsvalg.tittel") }),
    el("div", { role: "alert",
      text: t("ui.tidsvalg.avvist") }));
  rot.removeAttribute("aria-busy");
}

function bekreftelse(rot, startIso, sluttIso) {
  // Gjenbesøk viser bekreftelsen (DOM 3): valgt tid GJENTAS i
  // role=status — kandidaten skal kunne lese sin egen avtale.
  sett(rot,
    el("h1", { text: t("ui.tidsvalg.tittel") }),
    el("div", { role: "status" },
      el("p", { text: t("ui.tidsvalg.bekreftet") }),
      el("p", {}, tidspunkt(startIso), " – ", tidspunkt(sluttIso))));
  rot.removeAttribute("aria-busy");
}

function velger(rot, token, svar) {
  const utfall = el("div", { role: "alert" });
  const radioer = [];
  const rader = svar.slots.map((slot, i) => {
    const id = `tidsvalg-slot-${i}`;
    const radio = el("input", { type: "radio", name: "slot", id,
      value: slot.slot_id, ...(slot.ledig ? {} : { disabled: "" }) });
    if (slot.ledig) radioer.push(radio);
    // «Fullt» er TEKST i etiketten, aldri bare en tilstand (§4):
    // en skjermleser og en monokrom skjerm får samme dom.
    const tekst = el("label", { for: id },
      tidspunkt(slot.start), " – ", tidspunkt(slot.slutt),
      slot.ledig ? "" : ` — ${t("ui.tidsvalg.fullt")}`);
    return el("p", {}, radio, " ", tekst);
  });
  const knapp = el("button", { type: "submit",
    text: t("ui.tidsvalg.velg_knapp") });
  const skjema = el("form", {},
    el("fieldset", {},
      el("legend", { text: t("ui.tidsvalg.velg_tittel") }),
      ...rader),
    el("p", {}, knapp));
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const valgt = radioer.find((r) => r.checked);
    if (!valgt) {
      sett(utfall, t("ui.tidsvalg.velg_forst"));
      return;
    }
    if (knapp.disabled) return;
    knapp.disabled = true;
    let res;
    try {
      res = await post("/v1/tidsvalg/velg",
        { token, slot_id: valgt.value });
    } catch {
      knapp.disabled = false;
      sett(utfall, t("ui.tidsvalg.avvist"));
      return;
    }
    if (res.ok && res.kropp && res.kropp.valgt) {
      bekreftelse(rot, res.kropp.start, res.kropp.slutt);
      return;
    }
    const kode = res.kropp && res.kropp.feil;
    if (kode === "slot_fullt" || kode === "valg_alt_registrert") {
      // De to SKILLBARE utfallene (§2) — begge betyr at listen flaten
      // viser er foreldet: hent den på nytt (et alt registrert valg
      // rendres da som bekreftelsen, DOM 3).
      knapp.disabled = false;
      sett(utfall, t(`ui.tidsvalg.${kode}`));
      await last(rot, token, utfall);
      return;
    }
    // Alt annet er den uniforme avvisningen — uten årsaksskille.
    avvist(rot);
  });
  sett(rot,
    el("h1", { text: t("ui.tidsvalg.tittel") }),
    el("p", { text: t("ui.tidsvalg.intro") }),
    utfall,
    svar.slots.length ? skjema
      : el("p", { text: t("ui.tidsvalg.ingen_slots") }));
  rot.removeAttribute("aria-busy");
}

async function last(rot, token, behold = null) {
  let res;
  try {
    res = await post("/v1/tidsvalg/oppslag", { token });
  } catch {
    avvist(rot);
    return;
  }
  if (!res.ok || !res.kropp) {
    avvist(rot);
    return;
  }
  if (res.kropp.valgt_slot) {
    const valgt = res.kropp.slots.find(
      (s) => s.slot_id === res.kropp.valgt_slot);
    if (valgt) {
      bekreftelse(rot, valgt.start, valgt.slutt);
      return;
    }
    // Valget peker på en slot svaret ikke bærer — vis bekreftelsen
    // uten tid heller enn å tilby et nytt valg (valget er endelig).
    sett(rot,
      el("h1", { text: t("ui.tidsvalg.tittel") }),
      el("div", { role: "status",
        text: t("ui.tidsvalg.bekreftet") }));
    rot.removeAttribute("aria-busy");
    return;
  }
  velger(rot, token, res.kropp);
  if (behold && behold.textContent) {
    // Meldingen fra handlingen som utløste omlastingen skal overleve
    // den — den settes inn i den NYE tegningens utfallsområde.
    const nytt = rot.querySelector("[role=alert]");
    if (nytt) sett(nytt, behold.textContent);
  }
}

export async function start(rot) {
  const sett_ = await hentI18n(velgSprak());
  sett_.taIBruk();
  // Fragmentet leses og POSTes i kroppen — det står aldri i en URL
  // serveren ser. Et manglende eller feilformet token er samme uniforme
  // avvisning som et ukjent.
  const token = (window.location.hash || "").slice(1);
  if (!TOKENMONSTER.test(token)) {
    avvist(rot);
    return;
  }
  await last(rot, token);
}

const rot = document.getElementById("tidsvalg-rot");
if (rot) {
  start(rot);
}
