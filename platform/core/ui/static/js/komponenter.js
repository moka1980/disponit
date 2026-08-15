// Det varige komponentsettet (v1 §3 + v2 §7), token-drevet og WCAG-portert.
// Alle bygger DOM via el()/sett() — API- og locale-tekst går inn som
// tekstnode, aldri innerHTML (V6). Farge er ALDRI eneste signal: badge og
// tidslinje bærer også glyf + tekst.
import { el, sett } from "./dom.js";
import { t, sprak } from "./i18n.js";

// --- BeslutningBadge (TILLAT/STOPP/UNNTAK) ---------------------------------
const _BADGE_KLASSE = { TILLAT: "tillat", STOPP: "stopp", UNNTAK: "unntak" };
const _BADGE_GLYF = { TILLAT: "✓", STOPP: "✕", UNNTAK: "!" };

export function BeslutningBadge(kode) {
  const klasse = _BADGE_KLASSE[kode] || "ukjent";
  return el("span", { class: `merke ${klasse}` },
    el("span", { class: "merke-glyf", "aria-hidden": "true",
      text: _BADGE_GLYF[kode] || "?" }),
    el("span", { text: t(`beslutning.${kode}`, kode) }));
}

// --- KategoriTag -----------------------------------------------------------
export function KategoriTag(kategori) {
  return el("span", { class: "tag", text: t(`unntak.${kategori}`, kategori) });
}

// --- Tidspunkt (ISO → lokalisert <time>) -----------------------------------
export function Tidspunkt(iso) {
  let vis = iso;
  try {
    vis = new Intl.DateTimeFormat(sprak() === "en" ? "en-GB" : "nb-NO",
      { dateStyle: "short", timeStyle: "short" }).format(new Date(iso));
  } catch { /* behold iso som fallback */ }
  return el("time", { datetime: iso, text: vis });
}

// --- KodeForklaring (motorkode → tekst + råkode i mono for support) --------
// Trygt fall-back: ukjent kode viser råkoden escaped, aldri tom, aldri kast.
export function KodeForklaring(kode) {
  return el("span", { class: "kodeforklaring" },
    el("span", { text: t(`kode.${kode}`, kode) }),
    " ",
    el("code", { class: "mono kode-raa", text: kode }));
}

// --- BegrunnelseKjede (ordnet liste av koder) ------------------------------
export function BegrunnelseKjede(koder) {
  if (!koder || !koder.length) return el("p", { class: "muted", text: "—" });
  return el("ol", { class: "kjede" },
    koder.map((k) => el("li", {}, KodeForklaring(k))));
}

// --- StatusTidslinje (unntakshistorikk) ------------------------------------
export function StatusTidslinje(historikk) {
  return el("ol", { class: "tidslinje" },
    historikk.map((h) => el("li", { class: "tidslinje-steg" },
      el("span", { class: "tidslinje-glyf", "aria-hidden": "true", text: "●" }),
      el("div", {},
        el("div", { class: "tidslinje-hendelse",
          text: t(`hendelse.${h.hendelse}`, h.hendelse) }),
        (h.fra_status || h.til_status)
          ? el("div", { class: "muted" },
              t(`status.${h.fra_status}`, h.fra_status || "—"), " → ",
              t(`status.${h.til_status}`, h.til_status || "—"))
          : null,
        Tidspunkt(h.ts)))));
}

// --- Skjermtilstander (SideStatus bruker disse) ----------------------------
export function Lasteskjelett({ rader = 4 } = {}) {
  const barn = [];
  for (let i = 0; i < rader; i++) barn.push(el("div", { class: "skjelett" }));
  return el("div", { class: "laster", "aria-busy": "true", role: "status" },
    el("span", { class: "sr-only", text: t("ui.laster") }), ...barn);
}

export function TomTilstand({ tittel, tekst } = {}) {
  return el("div", { class: "tilstand tom" },
    el("h2", { text: tittel || t("ui.tom_tittel") }),
    el("p", { text: tekst || t("ui.tom_tekst") }));
}

export function Feiltilstand({ tittel, tekst, paaProvIgjen } = {}) {
  const n = el("div", { class: "tilstand feil", role: "alert" },
    el("h2", { text: tittel || t("ui.feil_tittel") }),
    el("p", { text: tekst || t("ui.feil_tekst") }));
  if (paaProvIgjen) {
    const b = el("button", { class: "knapp", type: "button",
      text: t("ui.prov_igjen") });
    b.addEventListener("click", paaProvIgjen);
    n.append(b);
  }
  return n;
}

export function TilgangsVakt({ tittel, tekst } = {}) {
  return el("div", { class: "tilstand ingen-tilgang", role: "note" },
    el("h2", { text: tittel || t("ui.ingen_tilgang_tittel") }),
    el("p", { text: tekst || t("ui.ingen_tilgang_tekst") }));
}

// 401 er en ANNEN tilstand enn 403 (V2): innloggingsflate, ikke ingen-tilgang.
export function Uautorisert({ paaLoggInn } = {}) {
  const n = el("div", { class: "tilstand uautorisert", role: "status" },
    el("h2", { text: t("ui.uautorisert_tittel") }),
    el("p", { text: t("ui.uautorisert_tekst") }));
  if (paaLoggInn) {
    const b = el("button", { class: "knapp primar", type: "button",
      text: t("ui.logg_inn") });
    b.addEventListener("click", paaLoggInn);
    n.append(b);
  }
  return n;
}

// --- VarselBanner ----------------------------------------------------------
export function VarselBanner({ art = "info", tekst } = {}) {
  return el("div", { class: `banner ${art}`, role: "note" },
    el("span", { text: tekst || "" }));
}

// --- LiveRegion (én aria-live-region for asynkrone meldinger) --------------
let _live = null;
export function sikreLiveRegion() {
  if (_live && _live.isConnected) return _live;
  _live = el("div", { class: "sr-only", role: "status", "aria-live": "polite",
    "aria-atomic": "true" });
  document.body.append(_live);
  return _live;
}
export function meldLive(tekst) { sikreLiveRegion().textContent = tekst; }

// --- CursorNavigasjon (ærlig keyset: «Vis mer» + «Oppdater») ---------------
export function CursorNavigasjon({ neste, paaMer, paaOppdater } = {}) {
  const n = el("div", { class: "cursornav" });
  if (paaOppdater) {
    const o = el("button", { class: "knapp", type: "button",
      text: t("ui.oppdater") });
    o.addEventListener("click", paaOppdater);
    n.append(o);
  }
  if (neste && paaMer) {
    const m = el("button", { class: "knapp", type: "button",
      text: t("ui.vis_mer") });
    m.addEventListener("click", paaMer);
    n.append(m);
  }
  return n;
}

// --- SensitiveData (V6-vakt: sensitive felt vises ALDRI, kun markør) -------
export function SensitiveData() {
  return el("span", { class: "sensitiv muted", text: t("ui.sensitiv.skjult") });
}

// --- SideStatus: én av fem (+ ingen_tilgang) tilstander per flate ----------
export function byggStatus(tilstand) {
  switch (tilstand.type) {
    case "laster": return Lasteskjelett(tilstand);
    case "tom": return TomTilstand(tilstand);
    case "feil": return Feiltilstand(tilstand);
    case "uautorisert": return Uautorisert(tilstand);
    case "ingen_tilgang": return TilgangsVakt(tilstand);
    case "innhold": return tilstand.node;
    default: return Feiltilstand({});
  }
}
export function visStatus(container, tilstand) {
  sett(container, byggStatus(tilstand));
}

// --- AppShell (topplinje + global nav + main-landemerke) -------------------
export function AppShell({ tenant, ruter, aktiv, sprak: valgtSprak,
                          paaSprak, paaLoggUt } = {}) {
  // Språkvelger. `lang` per valg (Codex P2): «English» og «Norsk» er hver på
  // sitt språk, og uten attributtet arver de skallets `lang` — en skjermleser
  // ville lest det ene med feil uttale, uansett hvilket språk siden står i.
  const velger = el("select", { class: "sprakvelger",
    "aria-label": t("ui.sprak") });
  for (const s of ["nb", "en"]) {
    const opt = el("option", { value: s, lang: s, text: t(`ui.sprak.${s}`) });
    if (s === valgtSprak) opt.setAttribute("selected", "");
    velger.append(opt);
  }
  if (paaSprak) velger.addEventListener("change", () => paaSprak(velger.value));

  const loggUt = el("button", { class: "knapp", type: "button",
    text: t("ui.logg_ut") });
  if (paaLoggUt) loggUt.addEventListener("click", paaLoggUt);

  const topp = el("header", { class: "skall-topp" },
    el("div", { class: "skall-brand" },
      el("span", { class: "skall-merke", text: t("app.navn", "Disponit") }),
      el("span", { class: "skall-undertekst", text: t("ui.shell.undertittel") })),
    tenant ? el("span", { class: "skall-tenant", text: tenant }) : null,
    el("span", { class: "skall-ruteantall",
      text: `${ruter.length} · ${t("ui.shell.ruter")}` }),
    el("div", { class: "skall-hoyre" }, velger, loggUt));

  const lenker = new Map();
  const nav = el("nav", { class: "skall-nav", "aria-label": t("app.navn") },
    ruter.map((r) => {
      const attrs = { href: `#/${r.nokkel}`, text: t(`ui.nav.${r.nokkel}`) };
      if (r.nokkel === aktiv) attrs["aria-current"] = "page";
      const a = el("a", attrs);
      lenker.set(r.nokkel, a);
      return a;
    }));

  const hoved = el("main", { id: "hovedinnhold", class: "skall-hoved",
    tabindex: "-1" });

  // Oppdater aktiv nav-lenke på plass (aria-current) — rebygger ikke nav, så
  // fokus og referanser holder.
  function settAktiv(nokkel) {
    for (const [k, a] of lenker) {
      if (k === nokkel) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
  }

  const rot = el("div", { class: "skall" }, topp, nav, hoved);
  return { rot, hoved, settAktiv };
}
