// Det varige komponentsettet (v1 §3 + v2 §7), token-drevet og WCAG-portert.
// Alle bygger DOM via el()/sett() — API- og locale-tekst går inn som
// tekstnode, aldri innerHTML (V6). Farge er ALDRI eneste signal: badge og
// tidslinje bærer også glyf + tekst.
import { el, ikon, sett } from "./dom.js";
import { t, sprak } from "./i18n.js";
import { OMRADER } from "./katalog.js";
import { omradeFor, faseFor } from "./katalogoppslag.js";
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
// UTEN `tidssone`: formatert i leserens egen sone, og flaten påstår
// ingenting om hvilken — det er formen alle flatene utenom nøkkeltall
// bruker. MED `tidssone`: formatert I den sonen OG merket med den.
//
// De to henger sammen i ÉN beslutning, og den bor her. Skrives merkelappen
// ved SIDEN av kallet, er sonen to steder: formateringen og påstanden om
// formateringen. Da kan de sprike i stillhet — og det gjorde de: `(UTC)`
// sto inntil en `Intl.DateTimeFormat` uten `timeZone`, så en leser i UTC+2
// fikk `10:00–11:00 (UTC)` for vinduet `08:00–09:00Z`.
//
// Kaster `Intl` på en ukjent sone, faller vi tilbake til rå ISO — som
// bærer offsetten selv. Fallbacken kan altså ikke bli en ny feilmerking.
export function Tidspunkt(iso, { tidssone } = {}) {
  let vis = iso;
  try {
    const valg = { dateStyle: "short", timeStyle: "short" };
    if (tidssone) valg.timeZone = tidssone;
    vis = new Intl.DateTimeFormat(sprak() === "en" ? "en-GB" : "nb-NO",
      valg).format(new Date(iso));
    if (tidssone) vis = `${vis} (${tidssone})`;
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

// 043 (Gate 14b §7): utfallet av en kansellering skal AVBRYTE, ikke vente
// på en pause — egen assertiv region ved siden av den høflige. Årsaken
// leses opp, ikke bare vises.
let _alert = null;
export function sikreAlertRegion() {
  if (_alert && _alert.isConnected) return _alert;
  _alert = el("div", { class: "sr-only", role: "alert",
    "aria-live": "assertive", "aria-atomic": "true" });
  document.body.append(_alert);
  return _alert;
}
export function meldAlert(tekst) { sikreAlertRegion().textContent = tekst; }

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
                          brukerId, epost, roller, moduler, varsler, oppdatert,
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

  // ÉN RAD (eiervedtak 1/9, runde 2). Toppfeltet var tre etasjer:
  // merke + undertittel, en sentrert identitetsblokk med e-post,
  // 64-tegns bruker-id, rolleliste og et ruteantall, og til slutt
  // navigasjonen i en egen stripe under. Eier: «mye unødvendig ...
  // masse unødvendig informasjon der oppe, bør alt plasseres under
  // admin/profil» og «plasser språk og logg ut på samme rad i
  // toppmenyen og flytt toppmenyen helt opp».
  //
  // Nå er det én rad: merke, tenant, navigasjonen, og til høyre
  // brukerbrikken, språk og utlogging. Undertittelen er borte — den sto
  // på hver eneste side og fortalte ingenting man kan handle på.
  // Ruteantallet og den fulle identiteten er FLYTTET, ikke slettet: de
  // står i Admin-flaten, som er stedet man går for å se på seg selv.
  //
  // BRUKERBRIKKEN BLIR IGJEN, men bare det et MENNESKE kjenner seg igjen
  // på: e-posten. Den rå prinsipal-id-en (`bid_c612864ad46e…`) er teknisk
  // støy i en topplinje — eier, ordrett: «det er rotete med bid…» — og
  // står nå i Admin, sammen med roller og ruteantall, under egne
  // ledetekster.
  //
  // HVA DET KOSTER, sagt åpent. Blokken sto her fordi fire-øyne krever at
  // TO FORSKJELLIGE prinsipaler attesterer, og med to konti i samme
  // nettleser kunne man ellers attestert to ganger som samme bruker.
  // E-posten fanger det VANLIGE tilfellet: to attestasjoner på rad med
  // samme adresse er synlig. Den fanger ikke det patologiske — to
  // `(issuer, sub)` som DELER e-post — og det er byttet: id-en er ett
  // klikk unna (brikken lenker til Admin) og ligger i `title` ved hover.
  // Håndhevelsen har uansett aldri vært i topplinjen; den er
  // primærnøkkelen i basen.
  //
  // Uten e-post (`api/oidc.py` lagrer None når utstederen utelater
  // kravet) viser brikken id-en likevel — da er den det eneste man har,
  // og et tomt felt ville vært verre enn en teknisk streng.
  // …MEN BARE NÅR PROFILEN FAKTISK ER NÅELIG. `#/admin` krever
  // `security:read` eller plattformdrift (sitekart.js), og en `godkjenner`
  // med bare `decisions:read` har den ikke. For DEN økten ville en lenke
  // pekt på en flate den ikke får — og id-en, som hele resonnementet over
  // hviler på, ville vært utilgjengelig unntatt som `title` ved hover.
  // Altså ikke naaelig med tastatur, for nettopp den brukeren som
  // attesterer.
  //
  // Derfor: har økten profilen, viser brikken bare e-posten og lenker dit.
  // Har den den ikke, står id-en i brikken som før. Vi skjuler den bare
  // når det finnes et sted å finne den.
  const harProfil = ruter.some((r) => r.nokkel === "admin");
  const brukerBrikke = (epost || brukerId)
    ? el(harProfil ? "a" : "span",
      { class: "skall-bruker",
        ...(harProfil ? { href: "#/admin" } : {}),
        title: [epost, brukerId].filter(Boolean).join(" · ") },
      el("span", { class: "skall-bruker-navn", text: epost || brukerId }),
      (!harProfil && epost && brukerId)
        ? el("span", { class: "skall-bruker-id", text: brukerId })
        : null)
    : null;

  const lenker = new Map();
  // HVILKEN FLATE STÅR JEG PÅ (Codex P2). `lenker` er det eneste `settAktiv`
  // oppdaterte, og modulflatene er nettopp de rutene som er filtrert UT av
  // den — så da oppføringen flyttet til venstremenyen, flyttet ikke
  // markeringen med. Ruten holdes derfor som tilstand her, ved siden av
  // lenkene, og navigasjonen som nå EIER modulflatene leser den samme
  // verdien. Startverdien er `aktiv`, så en dyplenke rett inn i flaten er
  // markert fra første tegning — ikke først ved neste navigasjon.
  let aktivFlate = aktiv;
  // Toppnavigasjonen er PLATTFORMFLATENE: modulflater (r.modulflate)
  // bor i venstremenyen som selve inngangen (eiers vedtak 24/8). Ruten
  // finnes fortsatt — dyplenker og bokmerker virker som før.
  const nav = el("nav", { class: "skall-nav", "aria-label": t("app.navn") },
    ruter.filter((r) => !r.modulflate).map((r) => {
      const attrs = { href: `#/${r.nokkel}`, text: t(`ui.nav.${r.nokkel}`) };
      if (r.nokkel === aktiv) attrs["aria-current"] = "page";
      const a = el("a", attrs);
      lenker.set(r.nokkel, a);
      return a;
    }));

  // Navigasjonen bor INNE i toppfeltet nå, ikke i en stripe under det.
  // `<nav>` beholder sin egen `aria-label`, så landemerket er uendret for
  // en skjermleser — det er bare plasseringen som er strammet inn.
  const topp = el("header", { class: "skall-topp" },
    el("span", { class: "skall-merke", text: t("app.navn", "Disponit") }),
    // TENANTBRIKKEN UTELATES NÅR DEN SIER DET SAMME SOM MERKET. Eier,
    // to ganger: «disponit står 2 ganger». Det er ikke en feil i koden —
    // det ene er PRODUKTNAVNET og det andre er TENANTEN, og for en kunde
    // som heter noe annet leser begge riktig. Men når en tenant heter det
    // samme som produktet, er den andre brikken ren gjentakelse, og en
    // topplinje som gjentar seg selv ser ut som en feil selv når den ikke
    // er det.
    //
    // Sammenligningen er lokalefri (`toLowerCase` uten språk) og trimmet:
    // det er en visningsheuristikk, ikke en autorisasjonsavgjørelse, og
    // ingenting går tapt om den bommer — tenanten står uansett i profilen.
    (tenant && tenant.trim().toLowerCase()
      !== t("app.navn", "Disponit").trim().toLowerCase())
      ? el("span", { class: "skall-tenant", text: tenant }) : null,
    nav,
    el("div", { class: "skall-hoyre" }, brukerBrikke, velger, loggUt));

  const hoved = el("main", { id: "hovedinnhold", class: "skall-hoved",
    tabindex: "-1" });

  // Oppdater aktiv nav-lenke på plass (aria-current) — rebygger ikke nav, så
  // fokus og referanser holder. Modulmenyen merkes av samme kall: den er
  // navigasjonen for modulflatene, og en navigasjon som ikke sier hvor du er
  // er halvferdig uansett hvilken av de to sonene ruten bor i.
  function settAktiv(nokkel) {
    for (const [k, a] of lenker) {
      if (k === nokkel) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    }
    aktivFlate = nokkel;
    // HVOR ER JEG — OGSÅ NEDERST. En bunnmeny uten «her er du» er fire
    // like knapper: du vet hvor du kan gå, ikke hvor du står.
    if (typeof bunnknapper !== "undefined") {
      for (const [k, a] of bunnknapper) {
        if (k === nokkel) a.setAttribute("aria-current", "page");
        else a.removeAttribute("aria-current");
      }
    }
    merkValgt();
    // FANEN FØLGER RUTEN. Navigerer du til en modulflate — fra bunnen,
    // fra en dyplenke, fra nettleserens historikk — skal området den
    // hører til stå åpent, ikke det du så på sist.
    if (typeof modulliste !== "undefined" && sokefelt && !sokefelt.value) {
      const her = [...MODULFLATE].find(([, f]) => f === nokkel);
      if (her) {
        const omrade = OMRADER.find((o) => o.moduler.includes(her[0]));
        if (omrade && omrade.id !== apentOmrade) {
          apentOmrade = omrade.id;
          tegnModuler("");
        }
      }
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
  //
  // Kontekstpanelet tar imot FOKUS når et modulvalg fyller det (Codex P2).
  // Uten det skjedde valget i stillhet: fokus ble stående på en knapp som ikke
  // endret seg, panelet ligger et helt annet sted i treet, og ingenting sa at
  // det var oppdatert. På den stablede visningen lå det i tillegg under hele
  // 45-modulersmenyen, altså langt utenfor skjermen. `tabindex="-1"` gjør det
  // til et mål man kan sendes til uten å legge det inn i tabrekkefølgen.
  const kontekst = el("aside", { class: "skall-kontekst", tabindex: "-1",
    "aria-label": t("ui.shell.kontekst") },
    el("p", { class: "muted", text: t("ui.shell.kontekst_tom") }));
  // ET TOMT PANEL SKAL IKKE TA PLASS (eiers skjermbilder 4/9).
  //
  // Panelet reserverte inntil tjue rem på HVER flate — en femtedel av
  // bredden — og på en modulflate sto det med «Velg en modul i menyen»
  // hele tiden, fordi modulflatene ikke bruker det. Kolonnen var altså
  // permanent opptatt av en setning som ba deg gjøre noe du ikke skulle.
  //
  // Tilstanden står på kroppen, ikke på panelet: rutenettet må VITE at
  // sonen er borte, ellers blir kolonnen stående tom (samme lærdom som
  // `data-meny`, Codex P1).
  kontekst.dataset.tom = "ja";

  // OVERSKRIFTSNIVÅENE MÅ HENGE SAMMEN (Codex P2). De elleve gruppene sto som
  // `h3` uten noe på nivå 2 over seg, så den som navigerer på overskrifter
  // begynte på nivå 3 — under et hull. Sonens `aria-label` hjelper ikke: en
  // etikett på et landemerke er ikke en overskrift og lager ikke et nivå.
  //
  // Menyen får derfor sin egen `h2`. Den er visuelt skjult fordi sonen alt SER
  // ut som en meny for den som ser den; det er hierarkiet som manglet, ikke
  // pynten. Sonen merkes av selve overskriften i stedet for av en kopi av
  // teksten: én kilde, og de to kan ikke komme fra hverandre.
  // SONEN ER ET NAVIGASJONSLANDEMERKE, IKKE EN SIDESTILT BOKS (Cursor P2).
  // Som `aside` var den riktig da radene bare byttet kontekstpanelet. Etter
  // eiers vedtak 24/8 er den den eneste annonserte veien til 038 og M-57, og
  // `nav.skall-nav` lister dem med vilje ikke — så den som hopper mellom
  // navigasjonslandemerkene (den vanligste måten å orientere seg med
  // skjermleser) traff bare plattformflatene og fant aldri modulflatene.
  //
  // To `nav`-landemerker krever hver sin etikett, og de har det: toppen bærer
  // `aria-label`, denne bærer overskriften sin gjennom `aria-labelledby`.
  const modulliste = el("div", { class: "skall-modulliste" });
  const menytittel = el("h2", { class: "sr-only", id: "modulmeny-tittel",
    text: t("ui.shell.moduler") });
  const venstre = el("nav", { class: "skall-venstre", id: "modulmeny",
    "aria-labelledby": "modulmeny-tittel" }, menytittel, modulliste);

  // Søket filtrerer modulmenyen. Det er det eneste søket har å søke i her, og
  // et søkefelt som later som det gjør mer ville vært verre enn ingen.
  const sokefelt = el("input", { id: "skall-sok", type: "search",
    class: "felt-inp", placeholder: t("ui.shell.sok_plassholder") });
  const sok = el("div", { class: "skall-sok" },
    el("label", { class: "sr-only", for: "skall-sok",
      text: t("ui.shell.sok_merkelapp") }), sokefelt);

  // Modul → flate-ruten dens, UTLEDET av rutenes egen deklarasjon (aldri et
  // håndholdt kart som kan drifte fra sitekartet). Kartet bærer bare ruter
  // økten FAKTISK har: samme gating som toppmenyen hadde, ingen ny dør.
  const MODULFLATE = new Map(
    ruter.filter((r) => r.modulflate).map((r) => [r.modulflate, r.nokkel]));

  // HVILKEN MODUL SER JEG PÅ (Codex P2). Valget er en tilstand menyen bærer,
  // ikke en engangshendelse: knappene tegnes på nytt for hvert tastetrykk i
  // søket, og uten at valget er lagret her ville markeringen forsvunnet i det
  // brukeren skrev én bokstav — mens panelet fortsatt viste modulen.
  let valgtModul = null;
  const modulknapper = new Map();

  // To slags «her er du», fordi de to radene gjør to forskjellige ting: en
  // modul med flate er NAVIGERT til (`aria-current="page"`, samme ord som
  // toppnavigasjonen bruker), en modul uten flate er VALGT i panelet ved
  // siden av (`aria-current="true"`). De kan ikke kollidere — en modul har
  // enten en flate eller ikke — så begge markeringene kan stå samtidig, og
  // det er riktig: brukeren kan lese om modul 14 i panelet mens hun står på
  // rekrutteringsflaten.
  function merkValgt() {
    for (const [n, kn] of modulknapper) {
      const her = MODULFLATE.has(n)
        ? MODULFLATE.get(n) === aktivFlate : n === valgtModul;
      if (her) kn.setAttribute("aria-current",
        MODULFLATE.has(n) ? "page" : "true");
      else kn.removeAttribute("aria-current");
    }
  }

  // MENYEN VISER KUNDENS MODULER, IKKE PLATTFORMKATALOGEN (Codex P2). Menyen
  // gikk over hele `OMRADER` — altså alle 45 modulene vi TILBYR — mens
  // `/v1/utrulling` allerede har sagt hvilke modul-ID-er DENNE økten er
  // tildelt. En kunde med to moduler fikk dermed 43 fremmede moduler presentert
  // som valgbare i sin egen applikasjonsmeny. Kundeflaten har hatt regelen
  // lenge (`modulerFraIder`): en ukjent eller delvis tildeling erstattes ikke
  // med hele katalogen.
  //
  // `null` er «vet ikke», ikke «ingen»: uten svar fra den autoriserte veien
  // sier menyen at tildelingen ikke er tilgjengelig, i stedet for å gjette i
  // noen av retningene.
  const tildelte = Array.isArray(moduler) ? new Set(moduler) : null;
  const erTildelt = (n) => tildelte !== null && tildelte.has(n);

  // EN FLATE ØKTEN HAR RUTE TIL, MÅ STÅ I MENYEN SOM EIER DEN (Cursor P1).
  // De to portene var uavhengige før og ble seriekoblet av flyttingen: ruten
  // gates på SCOPE (`decisions:read`), mens menyraden gates på KATALOG-
  // TILDELING. Ingen rad i `_UTRULLING` har 56 eller 57, og en ukjent tenant
  // har ingen tildeling i det hele tatt — så i hver eneste ekte økt forsvant
  // WCAG kontroll og rekruttering fra toppnavigasjonen uten å dukke opp i
  // venstremenyen. Flatene ble uten annonsert inngang overhodet, og det er
  // 038-regresjonen: «Én oppføring — WCAG kontroll» ble til ingen.
  //
  // Unionen er derfor ikke plattformkatalogen tilbake: `MODULFLATE` bærer
  // bare ruter økten FAKTISK har fått av `byggRuter`, altså nøyaktig det
  // toppnavigasjonen annonserte før flyttingen. Rekkevidden er den samme som
  // før, bare i sonen eier flyttet den til.
  const erSynlig = (n) => MODULFLATE.has(n) || erTildelt(n);

  // LENKETEKSTEN ER MÅLETS NAVN (Cursor P2). Raden med flate er en lenke, og
  // en lenke skal hete det den åpner: flaten bærer `ui.wcag.tittel` = «WCAG
  // kontroll» og `ui.rekruttering.tittel` = «Rekruttering» — samme strenger
  // som `ui.nav.<rute>`, som er ordene 038 ratifiserte for oppføringen og som
  // brukeren klikket på i toppnavigasjonen fram til nå. Med katalognavnet
  // («Automatisk WCAG-kontroll») pekte den eneste inngangen på en flate som
  // heter noe annet.
  const navnFor = (n) => (MODULFLATE.has(n)
    ? t(`ui.nav.${MODULFLATE.get(n)}`) : t(`site.katalog.m${n}.navn`));

  // Søket ser BEGGE navnene. Modulen heter fortsatt «Automatisk WCAG-kontroll»
  // i utrullingstabellen, på kundeflaten og i kontekstpanelet, og den som
  // søker på det navnet skal finne raden sin — men den som søker på «WCAG
  // kontroll» skal også det, og med bare ett av navnene i høystakken mistet
  // alltid det ene av dem den eneste inngangen flaten har.
  const sokenavn = (n) => [navnFor(n), t(`site.katalog.m${n}.navn`)];

  // HVILKET OMRÅDE STÅR ÅPENT. Fanevalget er en tilstand menyen bærer, av
  // samme grunn som `valgtModul`: lista tegnes på nytt for hvert tastetrykk
  // i søket, og uten dette ville fanen sprunget tilbake til den første.
  let apentOmrade = null;

  function modulrad(n) {
    // EN NAVIGASJON ER EN LENKE (Codex P2). Kortet ER inngangen når
    // modulen har en flate — og etter at oppføringen forsvant fra
    // toppnavigasjonen er det den ENESTE annonserte inngangen. Som
    // `<button>` med `location.hash` mistet den da alt en lenke har
    // med seg: åpne i ny fane, kopier adressen, se hvor den peker før
    // man klikker — og hjelpemidlene fikk «knapp» der brukeren står
    // foran en navigasjon. Adressen er den samme som dyplenken, så
    // `href` er ikke en ny mekanisme, bare den ærlige formen for den
    // som alt fantes.
    //
    // Knappen beholdes for moduler UTEN flate: der finnes det ingen
    // adresse å peke på, og panelet er en visning i samme side.
    const flate = MODULFLATE.get(n);
    const navn = navnFor(n);
    const kn = flate
      ? el("a", { class: "skall-modul", href: `#/${flate}`, text: navn })
      : el("button", { type: "button", class: "skall-modul", text: navn });
    // PÅ EN TELEFON ER MENYEN ET ARK OVER INNHOLDET (omlegging 6/9). Et
    // valg skal også lukke arket — ellers står brukeren igjen i menyen med
    // flaten (eller kontekstpanelet, CodeRabbit) hun ba om liggende bak.
    // `smal` leses én gang ved bygging (se under); på bred skjerm er
    // menyen en kolonne ved siden av og skal stå.
    if (!flate) kn.addEventListener("click", () => {
      if (smal) settMeny(false);
      visKontekst(n);
    });
    else kn.addEventListener("click", () => { if (smal) settMeny(false); });
    modulknapper.set(n, kn);
    return el("li", {}, kn);
  }

  function tegnModuler(filter) {
    const q = (filter || "").trim().toLocaleLowerCase(valgtSprak || "nb");
    const grupper = [];
    modulknapper.clear();

    // SØKET BRYTER FANENE, MED VILJE.
    //
    // Fanene viser ETT område om gangen — det er hele poenget: en meny på
    // trettisju rader er en katalog, ikke en meny. Men et søk som bare
    // lette i det åpne området ville vært et søk som lyver: treffet ditt
    // ligger i et område du ikke ser på, og lista sier «ingen treff».
    //
    // Under et søk er treffene derfor ÉN flat liste på tvers, og hver rad
    // bærer området sitt — for da er det ikke fanen som grupperer, det er
    // søket, og du skal likevel se HVOR modulen hører hjemme.
    if (q) {
      const rader = [];
      for (const omrade of OMRADER) {
        const treff = omrade.moduler.filter((n) => erSynlig(n)).filter((n) =>
          sokenavn(n).some((s) =>
            s.toLocaleLowerCase(valgtSprak || "nb").includes(q)));
        for (const n of treff) {
          rader.push(el("li", {},
            modulrad(n).firstChild,
            el("span", { class: "skall-modul-omrade",
              text: t(`site.omrade.${omrade.id}`) })));
        }
      }
      const melding = tildelte === null ? "ui.shell.moduler_ukjent"
        : rader.length ? null : "ui.shell.sok_ingen_treff";
      sett(modulliste,
        melding ? el("p", { class: "muted", role: "status",
          text: t(melding) }) : null,
        rader.length
          ? el("ul", { class: "skall-modulgruppe-liste" }, rader) : null);
      merkValgt();
      return;
    }

    for (const omrade of OMRADER) {
      const treff = omrade.moduler.filter((n) => erSynlig(n));
      if (!treff.length) continue;
      grupper.push({ id: omrade.id, moduler: treff });
    }
    if (grupper.length) {
      // ETT OMRÅDE OM GANGEN (eiers vedtak 4/9). Det største området har
      // ni flater; hele lista hadde trettisju. Fanene er husets egne —
      // full WAI-ARIA med roving tabindex — så tastaturet og
      // skjermleseren får mønsteret de kjenner, ikke en ny oppfinnelse.
      if (!grupper.some((g) => g.id === apentOmrade)) {
        // FANEN FØLGER DER DU ER. Står du på en modulflate, er det
        // området DEN hører til som skal stå åpent — ikke det første i
        // alfabetet. Ellers måtte du finne deg selv igjen hver gang.
        const her = [...MODULFLATE].find(([, f]) => f === aktivFlate);
        apentOmrade = (her && grupper.find(
          (g) => g.moduler.includes(her[0]))?.id) || grupper[0].id;
      }
      const faner = Faner({
        // ALLE PANELENE BYGGES, ETT VISES (`behold`). Med riving per
        // bytte ville menyen bare INNEHOLDT det åpne området — og da er
        // påstanden «menyen viser tildelingen din» ikke lenger sann:
        // resten av modulene dine finnes ikke i dokumentet, bare i en
        // fane du ennå ikke har trykket på. Skjult er noe annet enn
        // fraværende, og `merkValgt` trenger dessuten radene for å kunne
        // merke den du står på uansett hvilken fane som er åpen.
        behold: true,
        styring: false,
        start: apentOmrade,
        paaBytte: (id) => { apentOmrade = id; },
        merkelapp: t("ui.shell.omrader"),
        trinn: grupper.map((g) => ({
          nokkel: g.id,
          tittel: t(`site.omrade.${g.id}`),
          bygg: () => el("ul", { class: "skall-modulgruppe-liste" },
            g.moduler.map((n) => modulrad(n))),
        })),
      });
      // «VET IKKE» STÅR SAMMEN MED RADENE, ikke i stedet for dem. At
      // tildelingen ikke kunne leses er en opplysning i seg selv, og uten
      // den framstår radene unionen gir som hele svaret på hva økten har.
      // Forbeholdet kommer FØRST — det er det lista skal leses med.
      sett(modulliste,
        tildelte === null
          ? el("p", { class: "muted", text: t("ui.shell.moduler_ukjent") })
          : null,
        faner.rot);
      merkValgt();
      return;
    }
    // INGEN GRUPPER: da er det ingen faner å tegne, og meldingen under er
    // hele svaret.
    // Et tomt søk skal SI at det er tomt, ikke bare vise ingenting — og en
    // tildeling uten moduler er noe annet enn et søk uten treff.
    //
    // «Vet ikke» står nå SAMMEN med flatekortene, ikke i stedet for dem: at
    // tildelingen ikke kunne leses er en opplysning i seg selv, og uten den
    // ville de to radene unionen gir framstått som hele svaret på hva økten
    // har. Meldingen kommer først — den er forbeholdet lista skal leses med.
    const melding = tildelte === null ? "ui.shell.moduler_ukjent"
      : tildelte.size ? "ui.shell.moduler_tomt" : "ui.shell.moduler_ingen";
    sett(modulliste, el("p", { class: "muted", text: t(melding) }));
    merkValgt();
  }

  // `fokuser` er sant for brukerens eget klikk og usant for et programmatisk
  // oppslag: den som fyller panelet uten at brukeren ba om det, skal ikke rykke
  // fokus ut av der brukeren står.
  function visKontekst(n, { fokuser = true } = {}) {
    // Panelet er detaljvisningen til MENYEN, og skal ikke kunne brukes til å
    // hente fram en modul kunden ikke har: den som kaller utenfra ser ikke
    // tildelingen, så grensen står her.
    if (!erTildelt(n)) return;
    const status = modulStatus(n);
    valgtModul = n;
    merkValgt();
    kontekst.dataset.tom = "nei";
    kropp.dataset.kontekst = "fylt";
    sett(kontekst,
      el("h2", { class: "skall-kontekst-tittel",
        text: t(`site.katalog.m${n}.navn`) }),
      el("dl", { class: "skall-kontekst-liste" },
        el("dt", { text: t("ui.shell.kontekst_omrade") }),
        el("dd", { text: t(`site.omrade.${omradeFor(n)}`) }),
        el("dt", { text: t("ui.shell.kontekst_status") }),
        el("dd", {}, siteStatusMerke(status)),
        el("dt", { text: t("ui.shell.kontekst_fase") }),
        el("dd", { text: String(faseFor(n)) })),
      null);
    // Fokus flyttes ETTER at innholdet står der, ellers leses det tomme
    // panelet. Da følger både skjermleseren og skjermbildet med — nettleseren
    // ruller til det fokuserte elementet, som er hele poenget når panelet
    // ligger under en 45-modulersmeny på en liten skjerm.
    if (fokuser) kontekst.focus();
  }

  tegnModuler("");

  const skjul = el("button", { type: "button",
    class: "knapp liten skall-menybryter",
    text: t("ui.shell.skjul_meny") });
  skjul.setAttribute("aria-expanded", "true");
  skjul.setAttribute("aria-controls", "modulmeny");
  // Bryterne settes i takt med `smal` nedenfor, etter at `kropp` finnes.

  // Bryteren og søket er to veier til den SAMME tilstanden, så tilstanden
  // settes ett sted. Kalles først fra en hendelse, altså etter at `kropp` er
  // bygget.
  function settMeny(apen) {
    skjul.setAttribute("aria-expanded", apen ? "true" : "false");
    skjul.textContent = apen ? t("ui.shell.skjul_meny") : t("ui.shell.vis_meny");
    venstre.hidden = !apen;
    // TO BRYTERE, ÉN TILSTAND. Bunnmenyens «Moduler» og toppens
    // «Skjul modulmeny» styrer det samme, og en av dem som sa noe annet
    // enn den andre ville vært en kontroll som lyver om hva den gjorde.
    if (typeof bunnmoduler !== "undefined") {
      bunnmoduler.setAttribute("aria-expanded", apen ? "true" : "false");
    }
    // Rutenettet må VITE at sonen er borte (Codex P1): `hidden` alene tok
    // menyen ut av flyten, og et autoplassert rutenett flyttet da `main` inn i
    // sidebarkolonnen. Tilstanden står på kroppen, og CSS bytter oppsett.
    kropp.dataset.meny = apen ? "apen" : "skjult";
  }
  const menyErApen = () => skjul.getAttribute("aria-expanded") === "true";
  skjul.addEventListener("click", () => settMeny(!menyErApen()));

  // Å SØKE ER Å BE OM RESULTATENE (Codex P2). Søkefeltet står i toppsonen,
  // men det eneste stedet treffene vises er modulmenyen. Var menyen skjult,
  // ble den lista tatt ut av både skjermbildet og tilgjengelighetstreet mens
  // feltet sto igjen synlig og aktivt: hvert tastetrykk bygget en liste ingen
  // kunne se, og kontrollen framsto som ødelagt.
  //
  // Feltet slås ikke av — et søkefelt som er inaktivt av en grunn brukeren
  // ikke ser er samme problem med motsatt fortegn. Søket åpner menyen i
  // stedet: den som søker etter en modul ber om å få se modulene.
  sokefelt.addEventListener("input", () => {
    if (!menyErApen()) settMeny(true);
    tegnModuler(sokefelt.value);
  });

  // Statuslinja sier hva som FAKTISK gjelder. Spesifikasjonens eksempel («45
  // moduler aktive») er en illustrasjon, ikke en verdi: tallet utledes av
  // MODULSTATUS, så linja ikke kan love drift registeret ikke bærer.
  //
  // INGEN TELLER ER IKKE NULL VARSLER (Codex P2). `varsler` falt tidligere
  // tilbake på 0, og siden `app.js` aldri sender den inn — det finnes ingen
  // varselkilde å sende fra ennå — sa hver eneste økt i produksjon «0 varsler»
  // uansett hva som var på gang. Et tall er en påstand: mangler grunnlaget, sier
  // linja at tallet ikke er tilgjengelig i stedet for å hevde at alt er rolig.
  //
  // «SIST OPPDATERT» ER DATAENES TID, IKKE VÅR EGEN (Codex P2). Feltet sto på
  // `new Date()` ved bygging av skallet. Det er klokka i nettleseren i det
  // treet tegnes — ingenting synkroniseres her: modulstatusen er et statisk
  // kart, og varseltallet kommer utenfra. En oppfriskning av siden eller et
  // språkbytte ga altså uendrede data et helt ferskt tidsstempel, og en
  // feilstilt klokke ga et tidspunkt som var galt uansett.
  //
  // Tidspunktet kommer derfor fra den som HAR et: `oppdatert`. Uten et slikt
  // tidsstempel står påstanden ikke der i det hele tatt — en manglende
  // opplysning er ærligere enn en oppdiktet.
  //
  // TALLET KOMMER ETTER SKALLET (Codex P2). `/v1/varsel` er et nettkall, og
  // skallet tegnes synkront — så `varsler` er `null` ved bygging i praksis
  // alltid, og uten en vei til å SETTE den senere sto linja på «ikke
  // tilgjengelig» for godt. Nettopp den brukeren som har valgt kun portal
  // hadde da ingen proaktiv beskjed om at en attestering venter, og måtte
  // åpne varselflaten for å finne ut om det fantes noe å finne ut av.
  // `settVarsler` gis derfor ut sammen med `settAktiv`, og feltet skrives om
  // på plass. Statuslinja er `role="status"`, så tallet blir også annonsert
  // når det kommer — og igjen når det endrer seg.
  const telling = plattformTelling();
  const oppdatertTid = oppdatert == null ? null
    : (oppdatert instanceof Date ? oppdatert : new Date(oppdatert));
  const varselstatus = (n) => (n == null
    ? t("ui.shell.status_varsler_ukjent")
    : t("ui.shell.status_varsler").replace("{antall}", String(n)));
  const varselfelt = el("span", { text: varselstatus(varsler) });
  const settVarsler = (n) => { varselfelt.textContent = varselstatus(n); };
  const deler = [
    el("span", { text: t("ui.shell.status_moduler")
      .replace("{i_drift}", String(telling.iDrift))
      .replace("{totalt}", String(telling.totalt)) }),
    varselfelt,
  ];
  if (oppdatertTid && !Number.isNaN(oppdatertTid.getTime())) {
    deler.push(el("span", { text: t("ui.shell.status_oppdatert")
      .replace("{tid}", oppdatertTid.toLocaleTimeString(valgtSprak || "nb",
        { hour: "2-digit", minute: "2-digit" })) }));
  }
  // Skilletegnet hører til fugen mellom to opplysninger, ikke til den ene av
  // dem: faller en del bort, skal ikke en løs prikk bli stående igjen.
  const statuslinje = el("footer", { class: "skall-status", role: "status" },
    deler.flatMap((d, i) => i ? [el("span", { text: "·" }), d] : [d]));

  // INNHOLDET FØRST PÅ EN TELEFON (eiers ord: «mobil: ubrukelig»).
  //
  // Menyen sto ÅPEN ved start. På en bred skjerm er det riktig — den er
  // en kolonne ved siden av. På en telefon stables sonene, og da lå hele
  // modulmenyen FORAN innholdet: du åpnet en flate og fikk en meny.
  //
  // Terskelen er den samme som bunnmenyens (60rem), og den leses ÉN gang
  // ved bygging: en meny som åpner og lukker seg selv mens du endrer
  // vindusbredde ville tatt fra deg valget du nettopp tok.
  const smal = typeof window !== "undefined" && window.matchMedia
    ? window.matchMedia("(max-width: 60rem)").matches : false;
  const kropp = el("div", { class: "skall-kropp",
    "data-meny": smal ? "skjult" : "apen",
    "data-kontekst": "tom" }, venstre, hoved, kontekst);

  // ------------------------------------------------------------------
  // BUNNMENYEN (eiers vedtak 4/9: «hovedmenyene skal være helt nederst
  // på mobil»).
  // ------------------------------------------------------------------
  //
  // TOPPNAVIGASJONEN VAR IKKE EN MOBILMENY. Ti piller som brakk til en
  // egen rad under merket, og under DEM igjen søkefeltet, bryteren og en
  // modulmeny på trettisju rader — alt sammen FØR innholdet. På en
  // telefon var halve skjermen navigasjon før du hadde sett en eneste
  // opplysning.
  //
  // Reglene bunnmenyen er bygget etter, og hvorfor de er reglene:
  //
  //   * MAKS FEM. Flere enn det, og målene blir for smale for en
  //     tommel — og en meny du bommer på er verre enn en du må åpne.
  //     Her: fire, fordi det er fire ting som er øverste nivå.
  //   * IKON *OG* TEKST. Et ikon alene er en gjetning; hva «☰» betyr
  //     er noe man lærer, ikke noe man ser.
  //   * BARE ØVERSTE NIVÅ. Undernavigasjon hører ikke hjemme her —
  //     «Moduler» åpner modulmenyen, den ER ikke modulmenyen.
  //   * DEN STÅR STILLE. Samme plassering på hver eneste flate. En meny
  //     som flytter seg er en meny man må lete etter hver gang.
  //
  // Den er ETT navigasjonslandemerke til, så den bærer sin egen etikett.
  const bunnvalg = [];
  const bunnknapper = new Map();

  const bunnrute = (nokkel, ikonnavn) => {
    if (!ruter.some((r) => r.nokkel === nokkel)) return;
    const a = el("a", { class: "skall-bunn-valg", href: `#/${nokkel}` },
      ikon(ikonnavn), el("span", { class: "skall-bunn-tekst",
        text: t(`ui.nav.${nokkel}`) }));
    // MARKERT FRA FØRSTE TEGNING, som toppnavigasjonen: en dyplenke rett
    // inn i flaten skal si hvor du er med én gang, ikke først ved neste
    // navigasjon.
    if (nokkel === aktiv) a.setAttribute("aria-current", "page");
    bunnknapper.set(nokkel, a);
    bunnvalg.push(a);
  };
  bunnrute("oversikt", "oversikt");

  // «Moduler» er en BRYTER, ikke en lenke: den åpner menyen som alt
  // finnes, og en lenke ville lovet en side som ikke er der.
  const bunnmoduler = el("button", { type: "button",
    class: "skall-bunn-valg" },
    ikon("moduler"),
    el("span", { class: "skall-bunn-tekst", text: t("ui.shell.moduler") }));
  bunnmoduler.setAttribute("aria-controls", "modulmeny");
  bunnmoduler.setAttribute("aria-expanded", "true");
  bunnvalg.push(bunnmoduler);

  bunnrute("varsler", "varsler");

  // «Mer» åpner resten av toppnavigasjonen. Å KRAMME alt inn i bunnen
  // hadde gitt ti mål på en telefonbredde — omtrent tre kvart
  // tommelbredde hver, altså en meny bygget for å bomme på.
  const merliste = el("div", { class: "skall-bunn-mer-liste", hidden: true,
    id: "skall-bunn-mer" });
  for (const r of ruter) {
    // MODULFLATENE HØRER TIL «MODULER», IKKE «MER» (eiers skjermbilde
    // 4/9). Uten filteret listet «Mer» hele modulkatalogen om igjen —
    // samme rute i to menyer på samme skjerm, og den ene av dem uten
    // området sitt. Samme skille som toppnavigasjonen har hatt siden
    // eiervedtaket 24/8: `!r.modulflate`.
    if (r.modulflate || bunnknapper.has(r.nokkel)) continue;
    merliste.append(el("a", { class: "skall-bunn-mer-valg",
      href: `#/${r.nokkel}`, text: t(`ui.nav.${r.nokkel}`) }));
  }

  // …OG KNAPPEN FINNES BARE HVIS DET ER NOE Å ÅPNE (CodeRabbit). En økt
  // med bare bunnrutene og modulflater ville ellers fått en «Mer» som
  // folder ut ingenting — en kontroll som ser ut som en vei videre og
  // ikke er det. Det er samme klasse som en knapp som alltid feiler:
  // verre enn en valgmulighet som ikke finnes.
  const bunnmer = merliste.childNodes.length
    ? el("button", { type: "button", class: "skall-bunn-valg" },
      ikon("mer"),
      el("span", { class: "skall-bunn-tekst", text: t("ui.shell.mer") }))
    : null;
  if (bunnmer) {
    bunnmer.setAttribute("aria-controls", "skall-bunn-mer");
    bunnmer.setAttribute("aria-expanded", "false");
    bunnmer.addEventListener("click", () => {
      const apen = bunnmer.getAttribute("aria-expanded") === "true";
      bunnmer.setAttribute("aria-expanded", apen ? "false" : "true");
      merliste.hidden = apen;
    });
    bunnvalg.push(bunnmer);
  }

  bunnmoduler.addEventListener("click", () => {
    const apen = !menyErApen();
    settMeny(apen);
    // Å ÅPNE MENYEN ER Å BE OM Å SE DEN. Uten dette hoppet ville
    // brukeren stått igjen nederst på siden med menyen åpnet langt over
    // seg — og på en telefon er «langt over» utenfor skjermen.
    if (apen) venstre.scrollIntoView({ block: "start" });
  });

  // «MER» BÆRER DET BUNNEN IKKE HAR PLASS TIL — og ingenting annet: en
  // rute som står begge steder ville gitt to veier til samme sted i
  // samme meny, som er en meny som ser større ut enn den er.
  // BRYTERNE I TAKT MED STARTTILSTANDEN. `kropp` bærer den; de to
  // kontrollene må si det samme fra første tegning, ellers står det
  // «Skjul modulmeny» over en meny som er skjult.
  settMeny(!smal);

  const bunn = el("nav", { class: "skall-bunn",
    "aria-label": t("ui.shell.bunnmeny") },
    merliste,
    el("ul", { class: "skall-bunn-liste" },
      bunnvalg.map((v) => el("li", {}, v))));

  // `nav` står ikke her lenger — den er et barn av `topp` (én rad).
  const rot = el("div", { class: "skall" }, topp, sok, skjul, kropp,
    statuslinje, bunn);
  // `velger` gis ut fordi den som bygger skallet på nytt må kunne legge fokus
  // tilbake på kontrollen brukeren nettopp brukte (Codex P2) — uten å lete
  // etter den på klassenavn i et tre den selv nettopp har satt inn.
  return { rot, hoved, settAktiv, settVarsler, velger, visKontekst };
}

// --- Faner (WAI-ARIA tabs) -------------------------------------------------
//
// Én lang skjemaside ber brukeren skrolle for å finne ut hva som gjenstår, og
// gir ingen følelse av framdrift. Spesifikasjonen (§2.1) sier «maks 3
// handlingsvalg per skjermbilde» og «alt innen 2 klikk» — begge deler forutsetter
// at innholdet er delt i trinn.
//
// Mønsteret er tabs, ikke bare knapper: `role="tablist"` med piltaster,
// `aria-selected`, og `aria-controls` til panelet. Uten det er fanene bare
// lenker som ser ut som faner — en skjermleser får ingen beskjed om at det
// FINNES flere paneler, og piltaster gjør ingenting.
//
// `trinn`: [{ nokkel, tittel, bygg: () => Node }]
// `styring: false` dropper forrige/neste-knappene: de hører til SKJEMAER
// som fylles ut i rekkefølge (§2.1s trinn), ikke til dashbordfaner der
// panelene er likestilte visninger — der er de to ekstra handlingsvalg
// på et skjermbilde spesifikasjonen alt begrenser til tre.
// Returnerer { rot, gaaTil, aktiv }.
let _fanerTeller = 0;

// `behold: true` bygger hvert panel ÉN gang og beholder innholdet når
// fanen forlates (bare `hidden` veksles). For dashbordfaner der panelene
// holder LEVENDE seksjoner — skjemaer midt i utfylling, åpne detaljer,
// async-svar som lander mens en annen fane er valgt — er riving per
// bytte nettopp remount-klassen rekrutteringsflaten alt har betalt for.
// Standard (false) er som før: `bygg()` per valg, for paneler som leser
// tilstand som endrer seg mellom tegninger (policyadmin/wcag-formen).
export function Faner({ trinn, start, paaBytte, styring = true,
                        behold = false, merkelapp = null } = {}) {
  let aktiv = start && trinn.some((s) => s.nokkel === start) ? start : trinn[0].nokkel;
  // ID-ene var utledet av `nokkel` alene, så to fanesett med samme trinnavn
  // fikk samme ID-er (Codex P2). Det er ikke et teoretisk sammentreff: i
  // policyadmin åpner `paaFerdig` en oppfrisket detaljskuff UTEN å lukke den
  // gamle, så begge fanesettene står i DOM-en samtidig. `getElementById` gir
  // det FØRSTE treffet, og den nye dialogens `aria-controls`/`aria-labelledby`
  // kunne dermed løses opp i den underliggende, inerte dialogen. Et løpenummer
  // per instans holder hvert fanesett innenfor seg selv.
  const merke = `faner${++_fanerTeller}`;
  const faneId = (n) => `${merke}-fane-${n}`;
  const panelId = (n) => `${merke}-panel-${n}`;
  const faner = new Map();
  const paneler = new Map();
  // ETIKETTEN KAN OVERSTYRES. To fanesett på samme side må kunne skilles
  // fra hverandre av den som hopper mellom landemerker — «Faner» og «Faner»
  // er to navn som ikke navngir noe.
  const liste = el("div", { class: "faner-liste", role: "tablist",
    "aria-label": merkelapp || t("ui.faner.merkelapp") });

  // ETT panel per fane, ikke ett panel som bytter ID (Codex P2). Med den gamle
  // løsningen lovet fanene mer enn DOM-en holdt: Roller og Handlinger
  // annonserte `aria-controls="fane-panel-roller"`/`-handlinger` fra første
  // tegning, mens det eneste panelet som fantes het `fane-panel-grunn`.
  // Referansene pekte i tomme luften, og relasjonen var i praksis bare gyldig
  // for den valgte fanen — selv om komponenten beskrives som et komplett
  // WAI-ARIA-tabmønster. De inaktive panelene er `hidden`, så de er
  // eksisterende mål uten å være innhold noen leser.
  //
  // Innholdet bygges fortsatt først når fanen velges: `bygg()` leser tilstand
  // som endrer seg mellom tegninger, og skal ikke fryses ved konstruksjon.
  function tegnPanel() {
    for (const s of trinn) {
      const p = paneler.get(s.nokkel);
      if (behold) {
        if (!p.childNodes.length) sett(p, s.bygg());
        p.hidden = s.nokkel !== aktiv;
        continue;
      }
      if (s.nokkel === aktiv) { p.hidden = false; sett(p, s.bygg()); }
      else { p.hidden = true; sett(p); }
    }
  }

  function gaaTil(nokkel, flyttFokus) {
    if (!trinn.some((s) => s.nokkel === nokkel)) return;
    aktiv = nokkel;
    for (const [k, kn] of faner) {
      const valgt = k === aktiv;
      kn.setAttribute("aria-selected", valgt ? "true" : "false");
      // Bare den valgte fanen er i tab-rekkefølgen (WAI-ARIA: roving
      // tabindex) — ellers må man tabbe gjennom alle fanene for å nå
      // innholdet.
      kn.setAttribute("tabindex", valgt ? "0" : "-1");
      kn.classList.toggle("valgt", valgt);
    }
    tegnPanel();
    if (flyttFokus) faner.get(aktiv).focus();
    if (paaBytte) paaBytte(aktiv);
  }

  trinn.forEach((s, i) => {
    const kn = el("button", { type: "button", class: "fane", id: faneId(s.nokkel),
      role: "tab", text: s.tittel });
    kn.setAttribute("aria-controls", panelId(s.nokkel));
    kn.addEventListener("click", () => gaaTil(s.nokkel, false));
    kn.addEventListener("keydown", (e) => {
      const retning = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!retning) return;
      e.preventDefault();
      gaaTil(trinn[(i + retning + trinn.length) % trinn.length].nokkel, true);
    });
    faner.set(s.nokkel, kn);
    liste.append(kn);
    // Panelet er fokuserbart: er innholdet langt, skal Tab fra fanen lande i
    // panelet og ikke hoppe forbi det.
    paneler.set(s.nokkel, el("div", { class: "faner-panel", role: "tabpanel",
      id: panelId(s.nokkel), "aria-labelledby": faneId(s.nokkel),
      tabindex: "0", hidden: true }));
  });

  // Forrige/neste i tillegg til fanene: et skjema fylles ut i rekkefølge, og
  // da skal veien videre være der hånden er — nederst i panelet.
  const forrige = el("button", { type: "button", class: "knapp",
    text: t("ui.faner.forrige") });
  const neste = el("button", { type: "button", class: "knapp",
    text: t("ui.faner.neste") });
  function steg(retning) {
    const i = trinn.findIndex((s) => s.nokkel === aktiv);
    const ny = trinn[i + retning];
    if (ny) gaaTil(ny.nokkel, true);
  }
  forrige.addEventListener("click", () => steg(-1));
  neste.addEventListener("click", () => steg(1));

  const styringRot = el("div", { class: "faner-styring" }, forrige, neste);
  const rot = el("div", { class: "faner" }, liste, [...paneler.values()],
    ...(styring ? [styringRot] : []));

  function oppdaterStyring() {
    const i = trinn.findIndex((s) => s.nokkel === aktiv);
    forrige.disabled = i === 0;
    neste.disabled = i === trinn.length - 1;
  }
  const opprinnelig = gaaTil;
  const medStyring = (n, f) => { opprinnelig(n, f); oppdaterStyring(); };
  gaaTil = medStyring;
  medStyring(aktiv, false);

  return { rot, gaaTil: medStyring, aktiv: () => aktiv };
}
