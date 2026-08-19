// Det varige komponentsettet (v1 §3 + v2 §7), token-drevet og WCAG-portert.
// Alle bygger DOM via el()/sett() — API- og locale-tekst går inn som
// tekstnode, aldri innerHTML (V6). Farge er ALDRI eneste signal: badge og
// tidslinje bærer også glyf + tekst.
import { el, sett } from "./dom.js";
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

  // OVERSKRIFTSNIVÅENE MÅ HENGE SAMMEN (Codex P2). De elleve gruppene sto som
  // `h3` uten noe på nivå 2 over seg, så den som navigerer på overskrifter
  // begynte på nivå 3 — under et hull. Sonens `aria-label` hjelper ikke: en
  // etikett på et landemerke er ikke en overskrift og lager ikke et nivå.
  //
  // Menyen får derfor sin egen `h2`. Den er visuelt skjult fordi sonen alt SER
  // ut som en meny for den som ser den; det er hierarkiet som manglet, ikke
  // pynten. Sonen merkes av selve overskriften i stedet for av en kopi av
  // teksten: én kilde, og de to kan ikke komme fra hverandre.
  const modulliste = el("div", { class: "skall-modulliste" });
  const menytittel = el("h2", { class: "sr-only", id: "modulmeny-tittel",
    text: t("ui.shell.moduler") });
  const venstre = el("aside", { class: "skall-venstre", id: "modulmeny",
    "aria-labelledby": "modulmeny-tittel" }, menytittel, modulliste);

  // Søket filtrerer modulmenyen. Det er det eneste søket har å søke i her, og
  // et søkefelt som later som det gjør mer ville vært verre enn ingen.
  const sokefelt = el("input", { id: "skall-sok", type: "search",
    class: "felt-inp", placeholder: t("ui.shell.sok_plassholder") });
  const sok = el("div", { class: "skall-sok" },
    el("label", { class: "sr-only", for: "skall-sok",
      text: t("ui.shell.sok_merkelapp") }), sokefelt);

  // HVILKEN MODUL SER JEG PÅ (Codex P2). Valget er en tilstand menyen bærer,
  // ikke en engangshendelse: knappene tegnes på nytt for hvert tastetrykk i
  // søket, og uten at valget er lagret her ville markeringen forsvunnet i det
  // brukeren skrev én bokstav — mens panelet fortsatt viste modulen.
  let valgtModul = null;
  const modulknapper = new Map();

  function merkValgt() {
    for (const [n, kn] of modulknapper) {
      if (n === valgtModul) kn.setAttribute("aria-current", "true");
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

  function tegnModuler(filter) {
    const q = (filter || "").trim().toLocaleLowerCase(valgtSprak || "nb");
    const grupper = [];
    modulknapper.clear();
    if (tildelte === null) {
      sett(modulliste,
        el("p", { class: "muted", text: t("ui.shell.moduler_ukjent") }));
      return;
    }
    for (const omrade of OMRADER) {
      const treff = omrade.moduler.filter((n) => erTildelt(n)).filter((n) =>
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
            modulknapper.set(n, kn);
            return el("li", {}, kn);
          }))));
    }
    // Et tomt søk skal SI at det er tomt, ikke bare vise ingenting — og en
    // tildeling uten moduler er noe annet enn et søk uten treff.
    const tomNokkel = tildelte.size
      ? "ui.shell.moduler_tomt" : "ui.shell.moduler_ingen";
    sett(modulliste, grupper.length ? grupper
      : el("p", { class: "muted", text: t(tomNokkel) }));
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
    // Fokus flyttes ETTER at innholdet står der, ellers leses det tomme
    // panelet. Da følger både skjermleseren og skjermbildet med — nettleseren
    // ruller til det fokuserte elementet, som er hele poenget når panelet
    // ligger under en 45-modulersmeny på en liten skjerm.
    if (fokuser) kontekst.focus();
  }

  tegnModuler("");

  const skjul = el("button", { type: "button", class: "knapp liten",
    text: t("ui.shell.skjul_meny") });
  skjul.setAttribute("aria-expanded", "true");
  skjul.setAttribute("aria-controls", "modulmeny");

  // Bryteren og søket er to veier til den SAMME tilstanden, så tilstanden
  // settes ett sted. Kalles først fra en hendelse, altså etter at `kropp` er
  // bygget.
  function settMeny(apen) {
    skjul.setAttribute("aria-expanded", apen ? "true" : "false");
    skjul.textContent = apen ? t("ui.shell.skjul_meny") : t("ui.shell.vis_meny");
    venstre.hidden = !apen;
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

  const kropp = el("div", { class: "skall-kropp", "data-meny": "apen" },
    venstre, hoved, kontekst);
  const rot = el("div", { class: "skall" }, topp, nav, sok, skjul, kropp,
    statuslinje);
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
// Returnerer { rot, gaaTil, aktiv }.
let _fanerTeller = 0;

export function Faner({ trinn, start, paaBytte } = {}) {
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
  const liste = el("div", { class: "faner-liste", role: "tablist",
    "aria-label": t("ui.faner.merkelapp") });

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

  const styring = el("div", { class: "faner-styring" }, forrige, neste);
  const rot = el("div", { class: "faner" }, liste, [...paneler.values()],
    styring);

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
