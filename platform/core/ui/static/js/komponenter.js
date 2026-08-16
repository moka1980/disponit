// Det varige komponentsettet (v1 §3 + v2 §7), token-drevet og WCAG-portert.
// Alle bygger DOM via el()/sett() — API- og locale-tekst går inn som
// tekstnode, aldri innerHTML (V6). Farge er ALDRI eneste signal: badge og
// tidslinje bærer også glyf + tekst.
import { el, sett } from "./dom.js";
import { t, sprak } from "./i18n.js";
import { OMRADER, omradeFor, faseFor } from "./katalog.js";
import { modulStatus, plattformTelling } from "./plattformdata.js";
import { siteStatusMerke } from "./sitekomponenter.js";

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

// --- Hoppelenke (WCAG 2.4.1) ----------------------------------------------
// Lenka står i `index.html`, altså UTENFOR `#app`, og overlever derfor hver
// eneste flatebytte. Den er også det FØRSTE en tastaturbruker treffer.
//
// Den bodde tidligere som en privat hjelper i `app.js`, og da var det bare
// oppstarten der som lokaliserte den: byttet språk fra forsiden, som bare
// skriver `#app`, sto lenka igjen på «Hopp til innhold» under `lang="en"`
// (Codex P2). Nøyaktig den brukeren som trenger den mest — en som ikke leser
// norsk og navigerer med tastatur — møtte den på feil språk.
//
// Den hører hjemme her, sammen med `sikreLiveRegion`: begge er sidens egen
// ramme, ikke en flate, og begge må røres av ALLE som bytter språk.
export function lokaliserSkiplenke() {
  const l = document.querySelector(".hoppelenke");
  if (l) l.textContent = t("ui.hopp_til_innhold");
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
                          brukerId, epost, roller, varsler, paaSprak,
                          paaLoggUt } = {}) {
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
    // HVEM ER JEG. Skallet viste tenant og et ruteantall, men aldri hvilken
    // bruker økten tilhørte. Fire øyne krever at TO FORSKJELLIGE prinsipaler
    // attesterer, og med to konti i samme nettleser kunne eier attestert to
    // ganger som samme bruker og først fått vite det av primærnøkkelen.
    // Rollene står ved siden av: «hvilken rolle har jeg» skal ikke kreve at
    // man leser en policy for å finne ut av.
    //
    // PRINSIPALEN ER `bruker_id`, IKKE E-POSTEN (Codex P2). `api/oidc.py`
    // lagrer `epost` som None når utstederen utelater kravet — da forsvant
    // hele kontrollen, roller og alt, for nettopp den brukeren som ikke har
    // noe annet å kjenne seg igjen på. E-posten er dessuten ikke nøkkelen:
    // den kan være ubekreftet og delt av flere `(issuer, sub)`, så to konti
    // med samme e-post ville sett identiske ut i fire-øyne-flyten. Derfor
    // vises `bruker_id` alltid, med e-posten i tillegg når den finnes.
    (epost || brukerId)
      ? el("span", { class: "skall-bruker",
        title: [epost, brukerId].filter(Boolean).join(" · ") },
        el("span", { class: "skall-bruker-navn", text: epost || brukerId }),
        epost && brukerId
          ? el("span", { class: "skall-bruker-id", text: brukerId })
          : null,
        Array.isArray(roller) && roller.length
          ? el("span", { class: "skall-bruker-roller",
            text: roller.map((r) => t(`ui.rolle.${r}`, r)).join(", ") })
          : null)
      : null,
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

  // §2.3 LAYOUT: topp (nav + søk) · venstre (modulmeny) · sentrum (dashboard)
  // · høyre (kontekstpanel) · bunn (statuslinje). Skallet eide bare topp og
  // sentrum; resten fantes ikke, og modulene var usynlige inne i produktet de
  // utgjør.
  //
  // Venstremenyen kan skjules (§2.3), og bryteren bærer `aria-expanded` — en
  // meny som forsvinner uten at kontrollen sier fra er en meny som er borte
  // for den som ikke ser den forsvinne.
  const kontekst = el("aside", { class: "skall-kontekst",
    "aria-label": t("ui.shell.kontekst") },
    el("p", { class: "muted", text: t("ui.shell.kontekst_tom") }));

  const modulliste = el("div", { class: "skall-modulliste" });
  const venstre = el("aside", { class: "skall-venstre", id: "modulmeny",
    "aria-label": t("ui.shell.moduler") }, modulliste);

  // Søket filtrerer modulmenyen. Det er det eneste søket har å søke i her, og
  // et søkefelt som later som det gjør mer ville vært verre enn ingen.
  const sokefelt = el("input", { id: "skall-sok", type: "search",
    class: "felt-inp", placeholder: t("ui.shell.sok_plassholder") });
  const sok = el("div", { class: "skall-sok" },
    el("label", { class: "sr-only", for: "skall-sok",
      text: t("ui.shell.sok_merkelapp") }), sokefelt);

  function tegnModuler(filter) {
    const q = (filter || "").trim().toLocaleLowerCase(valgtSprak || "nb");
    const grupper = [];
    for (const omrade of OMRADER) {
      const treff = omrade.moduler.filter((n) =>
        !q || t(`site.katalog.m${n}.navn`).toLocaleLowerCase(valgtSprak || "nb")
          .includes(q));
      if (!treff.length) continue;
      grupper.push(el("section", { class: "skall-modulgruppe" },
        el("h3", { class: "skall-modulgruppe-navn",
          text: t(`site.omrade.${omrade.id}`) }),
        el("ul", { class: "skall-modulgruppe-liste" },
          treff.map((n) => {
            const kn = el("button", { type: "button", class: "skall-modul",
              text: t(`site.katalog.m${n}.navn`) });
            kn.addEventListener("click", () => visKontekst(n));
            return el("li", {}, kn);
          }))));
    }
    // Et tomt søk skal SI at det er tomt, ikke bare vise ingenting.
    sett(modulliste, grupper.length ? grupper
      : el("p", { class: "muted", text: t("ui.shell.moduler_tomt") }));
  }

  function visKontekst(n) {
    const status = modulStatus(n);
    sett(kontekst,
      el("h2", { class: "skall-kontekst-tittel",
        text: t(`site.katalog.m${n}.navn`) }),
      el("dl", { class: "skall-kontekst-liste" },
        el("dt", { text: t("ui.shell.kontekst_omrade") }),
        el("dd", { text: t(`site.omrade.${omradeFor(n)}`) }),
        el("dt", { text: t("ui.shell.kontekst_status") }),
        el("dd", {}, siteStatusMerke(status)),
        el("dt", { text: t("ui.shell.kontekst_fase") }),
        el("dd", { text: String(faseFor(n)) })));
  }

  sokefelt.addEventListener("input", () => tegnModuler(sokefelt.value));
  tegnModuler("");

  const skjul = el("button", { type: "button", class: "knapp liten",
    text: t("ui.shell.skjul_meny") });
  skjul.setAttribute("aria-expanded", "true");
  skjul.setAttribute("aria-controls", "modulmeny");
  skjul.addEventListener("click", () => {
    const apen = skjul.getAttribute("aria-expanded") === "true";
    skjul.setAttribute("aria-expanded", apen ? "false" : "true");
    skjul.textContent = apen ? t("ui.shell.vis_meny") : t("ui.shell.skjul_meny");
    venstre.hidden = apen;
  });

  // Statuslinja sier hva som FAKTISK gjelder. Spesifikasjonens eksempel («45
  // moduler aktive») er en illustrasjon, ikke en verdi: tallet utledes av
  // MODULSTATUS, så linja ikke kan love drift registeret ikke bærer.
  const telling = plattformTelling();
  const statuslinje = el("footer", { class: "skall-status", role: "status" },
    el("span", { text: t("ui.shell.status_moduler")
      .replace("{i_drift}", String(telling.iDrift))
      .replace("{totalt}", String(telling.totalt)) }),
    el("span", { text: "·" }),
    el("span", { text: t("ui.shell.status_varsler")
      .replace("{antall}", String(varsler == null ? 0 : varsler)) }),
    el("span", { text: "·" }),
    el("span", { text: t("ui.shell.status_oppdatert")
      .replace("{tid}", new Date().toLocaleTimeString(valgtSprak || "nb",
        { hour: "2-digit", minute: "2-digit" })) }));

  const kropp = el("div", { class: "skall-kropp" }, venstre, hoved, kontekst);
  const rot = el("div", { class: "skall" }, topp, nav, sok, skjul, kropp,
    statuslinje);
  // `velger` gis ut fordi den som bygger skallet på nytt må kunne legge fokus
  // tilbake på kontrollen brukeren nettopp brukte (Codex P2) — uten å lete
  // etter den på klassenavn i et tre den selv nettopp har satt inn.
  return { rot, hoved, settAktiv, velger, visKontekst };
}
