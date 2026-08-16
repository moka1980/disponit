// Guided policy-editor (PR-014). Å skrive en policy fra bunnen er et komplett,
// skjemagyldig JSON-dokument (roller, handlinger, verifikatorer, dataklasser,
// unntak …). Editoren starter derfor fra en BRANSJEMAL (komplett, gyldig) og
// lar eieren redigere de FORRETNINGSKRITISKE feltene: firmanavn, roller, og per
// handling automatiseringsmodus + grenser (beløpstak, valuta, tidsvindu).
// Strukturen (moduler, verifikatorer, reversering, unntak) arves fra malen, så
// utkastet forblir STRUKTURELT komplett — men det er ikke garantert gyldig
// (eier kan fjerne en referert rolle, sette auto_med_vilkaar uten vilkår osv.).
// GYLDIGHETSPORTEN er `valider` server-side (skjema + semantikk); den må passere
// før runde/fire-øyne-aktivering. Lagring går via opprett/rediger.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  hentMaler, hentJson, opprettUtkast, redigerUtkast, nyIdempotensnokkel,
  UautorisertFeil, ApiFeil,
} from "../api.js";
import { meldLive, TomTilstand, Feiltilstand } from "../komponenter.js";
import { flateHode } from "./felles.js";

const MODUS = ["auto", "auto_med_vilkaar", "alltid_stopp"];

function tekstfelt(etikett, verdi, paaEndre, attrs = {}, hint = "") {
  const inp = el("input", {
    type: "text", value: verdi == null ? "" : String(verdi),
    class: "felt-inp", ...attrs,
  });
  inp.addEventListener("input", () => paaEndre(inp.value));
  const id = "f-" + Math.random().toString(36).slice(2, 9);
  inp.id = id;
  // Et hint som ikke tegnes, hjelper ingen. Det henger på feltet via
  // `aria-describedby`, så en skjermleser leser formen SAMMEN med etiketten —
  // ikke som løs tekst i nærheten, eller ikke i det hele tatt. `span`, ikke
  // `p`: innholdet i en `label` er fraseinnhold.
  const hintNode = hint
    ? el("span", { class: "felt-hint", id: `${id}-hint`, text: hint })
    : null;
  if (hintNode) inp.setAttribute("aria-describedby", hintNode.id);
  return el("label", { class: "felt" },
    el("span", { class: "felt-navn", text: etikett }), inp, hintNode);
}

function velg(etikett, verdi, valg, oversettPrefiks, paaEndre) {
  const sel = el("select", { class: "felt-inp" });
  for (const v of valg) {
    const o = el("option", { value: v, text: t(`${oversettPrefiks}${v}`, v) });
    if (v === verdi) o.selected = true;
    sel.append(o);
  }
  sel.addEventListener("change", () => paaEndre(sel.value));
  return el("label", { class: "felt" },
    el("span", { class: "felt-navn", text: etikett }), sel);
}

function rollerSeksjon(policy, tegnPaaNytt) {
  policy.roller = Array.isArray(policy.roller) ? policy.roller : [];
  const liste = el("div", { class: "editor-liste" });
  policy.roller.forEach((r, i) => {
    const rad = el("div", { class: "editor-rad" },
      tekstfelt(t("ui.editor.rolle_id"), r.id, (v) => { r.id = v; }),
      tekstfelt(t("ui.editor.rolle_beskrivelse"), r.beskrivelse || "",
        (v) => { r.beskrivelse = v || undefined; }));
    const fjern = el("button", { class: "knapp liten", type: "button",
      text: t("ui.editor.fjern") });
    fjern.addEventListener("click", () => {
      policy.roller.splice(i, 1); tegnPaaNytt();
    });
    rad.append(fjern);
    liste.append(rad);
  });
  const legg = el("button", { class: "knapp", type: "button",
    text: t("ui.editor.legg_til_rolle") });
  legg.addEventListener("click", () => {
    policy.roller.push({ id: "" }); tegnPaaNytt();
  });
  return el("section", { class: "editor-seksjon",
    "aria-label": t("ui.editor.roller") },
    el("h3", { text: t("ui.editor.roller") }), liste, legg);
}

// Skjemaet er smalt, og da skal feltet være det også. Alle tre grensene under
// hadde fritekst, så eier måtte KJENNE formatet på forhånd og fikk svaret av
// validatoren etterpå: valuta som «kommaseparert» (en rå array lekket ut i
// UI-et), tidsvindu som en streng med sin egen grammatikk, beløp uten hint om
// desimaler. Et felt som bare kan produsere gyldige verdier trenger ingen av
// delene.

// `^[A-Z]{3}$` i skjemaet. NOK står først fordi det er standarden her; de
// øvrige er de vi faktisk møter. En policy med en annen kode beholder den
// (den legges inn i lista), så et nedtrekk aldri kan slette data.
const VALUTAER = ["NOK", "EUR", "USD", "SEK", "DKK", "GBP"];
const DAGER = ["man", "tir", "ons", "tor", "fre", "lor", "son"];
// Nøyaktig delene skjemaet godtar — verken mer eller mindre. Er den løsere enn
// skjemaet, plukker parseren fra hverandre en verdi den ikke kan sette sammen
// igjen, og velgerne viser noe annet enn det som ligger i modellen.
const DAG_RE = DAGER.join("|");
const KL_RE = "([01]\\d|2[0-3]):[0-5]\\d";
const TIDSVINDU_RE = new RegExp(
  `^(${DAG_RE})-(${DAG_RE}) (${KL_RE})-(${KL_RE})$`);
const TIDSVINDU_STANDARD =
  { fraDag: "man", tilDag: "fre", fraKl: "08:00", tilKl: "16:00" };
const settSammen = (d) => `${d.fraDag}-${d.tilDag} ${d.fraKl}-${d.tilKl}`;

function tidsvinduDeler(verdi) {
  const m = TIDSVINDU_RE.exec(verdi || "");
  return m ? { fraDag: m[1], tilDag: m[2], fraKl: m[3], tilKl: m[5] }
           : { ...TIDSVINDU_STANDARD };
}

// En lagret verdi er REPRESENTERBAR når en kontroll her kan vise den og skrive
// den tilbake uendret. Er den ikke det, har editoren ingenting å gjette ut fra
// — og å gjette er det farlige: åpner eier et eldre utkast med en ødelagt
// tidsgrense og endrer et helt ANNET felt, ble grensen enten byttet ut med et
// oppdiktet standardvindu eller slettet, bare fordi kortet ble tegnet. Fravær
// av `tidsvindu` betyr INGEN tidsbegrensning, så den stille slettingen gjør en
// policy som før ble avvist gyldig med bredere fullmakt enn eier har valgt.
// Regelen er derfor: rør ikke råverdien, vis at den må repareres, og steng
// lagringen til eier har valgt selv. Standardvinduet er fremdeles greit når
// eier AKTIVT slår grensen på — da er det eiers valg, ikke vår reparasjon.
function tidsvinduUleselig(g) {
  // `undefined` serialiseres bort av `JSON.stringify` og er dermed det samme
  // som fravær — der er det ingenting å reparere.
  return "tidsvindu" in g && g.tidsvindu !== undefined
    && !(typeof g.tidsvindu === "string" && TIDSVINDU_RE.test(g.tidsvindu));
}

const VALUTA_RE = /^[A-Z]{3}$/;

// Samme regel på det andre feltet: `_valider_grenser` krever 1–10 unike koder
// på formen `^[A-Z]{3}$`, og nedtrekket viser nøyaktig det. En dublett, en bar
// streng eller en tom liste er former ingen kontroll her kan vise.
function valutaUleselig(g) {
  if (!("valuta" in g) || g.valuta === undefined) return false;
  const v = g.valuta;
  return !Array.isArray(v) || !v.length || v.length > 10
    || v.some((k) => typeof k !== "string" || !VALUTA_RE.test(k))
    || new Set(v).size !== v.length;
}

// Reparasjonsraden: råverdien SLIK DEN ER LAGRET, og eiers veier ut. Ingen av
// dem skjer av seg selv — det er hele poenget. `JSON.stringify` fordi "" og
// null og 0 må kunne skilles fra hverandre på skjermen.
function reparasjon(etikett, raa, valg) {
  const knapper = valg.map(([tekst, gjor]) => {
    const k = el("button", { class: "knapp liten", type: "button", text: tekst });
    k.addEventListener("click", gjor);
    return k;
  });
  return el("div", { class: "editor-felt-gruppe" },
    el("div", { class: "editor-reparasjon" },
      el("p", { class: "felt-navn", text: etikett }),
      el("p", { text: t("ui.editor.grense_ulesbar") }),
      el("p", { class: "editor-hint",
        text: `${t("ui.editor.grense_lagret_verdi")}: ${JSON.stringify(raa)}` }),
      el("div", { class: "editor-rad" }, ...knapper)));
}

function tidsvinduVelger(g, tegnPaaNytt) {
  if (tidsvinduUleselig(g)) {
    const standard = settSammen(TIDSVINDU_STANDARD);
    return reparasjon(t("ui.editor.tidsvindu"), g.tidsvindu, [
      [t("ui.editor.grense_fjern"),
        () => { delete g.tidsvindu; tegnPaaNytt(); }],
      [`${t("ui.editor.grense_sett_standard")}: ${standard}`,
        () => { g.tidsvindu = standard; tegnPaaNytt(); }],
    ]);
  }
  // Her er `g.tidsvindu` enten fraværende eller et vindu velgerne kan vise.
  const paa = typeof g.tidsvindu === "string";
  const d = tidsvinduDeler(g.tidsvindu);
  const skriv = () => { g.tidsvindu = settSammen(d); };
  const bryter = el("input", { type: "checkbox", class: "felt-bryter" });
  if (paa) bryter.setAttribute("checked", "");
  bryter.addEventListener("change", () => {
    if (bryter.checked) skriv(); else delete g.tidsvindu;
    tegnPaaNytt();
  });
  const rad = el("div", { class: "editor-tidsvindu" });
  if (paa) {
    // Tømmer eier et `type="time"`-felt, blir verdien "". Lagringen kjører
    // ingen native skjemavalidering, så en tom del ble skrevet rett inn som
    // «man-fre -16:00» og døde først hos validatoren etterpå. Feltet tar bare
    // imot det skjemaet godtar, og faller ellers tilbake til siste gyldige
    // verdi. Å FJERNE vinduet er en egen, tydelig handling: av/på-bryteren.
    const KL_BARE = new RegExp(`^${KL_RE}$`);
    const klokke = (les, sett) => {
      const i = el("input", { type: "time", class: "felt-inp", value: les(),
        required: "" });
      i.addEventListener("change", () => {
        if (KL_BARE.test(i.value)) { sett(i.value); skriv(); }
        else i.value = les();
      });
      return i;
    };
    rad.append(
      velg(t("ui.editor.tidsvindu_fra_dag"), d.fraDag, DAGER, "ui.dag.",
        (v) => { d.fraDag = v; skriv(); }),
      velg(t("ui.editor.tidsvindu_til_dag"), d.tilDag, DAGER, "ui.dag.",
        (v) => { d.tilDag = v; skriv(); }),
      el("label", { class: "felt" },
        el("span", { class: "felt-navn", text: t("ui.editor.tidsvindu_fra_kl") }),
        klokke(() => d.fraKl, (v) => { d.fraKl = v; })),
      el("label", { class: "felt" },
        el("span", { class: "felt-navn", text: t("ui.editor.tidsvindu_til_kl") }),
        klokke(() => d.tilKl, (v) => { d.tilKl = v; })));
  }
  return el("div", { class: "editor-felt-gruppe" },
    el("label", { class: "felt felt-vannrett" }, bryter,
      el("span", { class: "felt-navn", text: t("ui.editor.tidsvindu") })),
    rad);
}

// FRAVÆR av `grenser.valuta` er en gyldig tilstand i skjemaet, og den betyr
// noe ANNET enn NOK: motoren sjekker valuta bare når feltet finnes, så «ingen
// begrensning» slipper gjennom enhver kode. Et nedtrekk som viste NOK uten å
// skrive NOK løy derfor om policyen — eier så en begrensning som ikke fantes.
// Den tomme raden ER den tilstanden, og den er valgbar begge veier.
const VALUTA_INGEN = "";

function valutaVelger(g, tegnPaaNytt) {
  // Samme regel som for tidsvinduet: en lagret liste nedtrekket ikke kan vise,
  // normaliseres ikke i det stille — eier velger. Det som KAN berges av koder
  // vises som ett av valgene, så «behold» er en verdi eier ser før den skrives.
  if (valutaUleselig(g)) {
    const berget = [...new Set((Array.isArray(g.valuta) ? g.valuta : [g.valuta])
      .filter((v) => typeof v === "string" && VALUTA_RE.test(v)))].slice(0, 10);
    const valg = [[t("ui.editor.grense_fjern"),
      () => { delete g.valuta; tegnPaaNytt(); }]];
    if (berget.length) {
      valg.push([`${t("ui.editor.grense_behold")}: ${berget.join(", ")}`,
        () => { g.valuta = berget; tegnPaaNytt(); }]);
    }
    return reparasjon(t("ui.editor.valuta"), g.valuta, valg);
  }
  // Valutaen er en liste i skjemaet, men i praksis én kode. Har policyen
  // flere, beholdes de: nedtrekket bytter den FØRSTE og sier fra om resten,
  // i stedet for å kaste dem stille.
  const valutaer = Array.isArray(g.valuta) ? g.valuta : [];
  const valgt = valutaer[0] || VALUTA_INGEN;
  // Halen er like valgbar som hodet. Ble valgene bygd av `valgt` alene, sto en
  // beholdt kode lenger bak — `["NOK","CHF"]` — nevnt i hintet uten å finnes i
  // nedtrekket: eier kunne se CHF, men ikke fjerne NOK og beholde den, slik
  // fritekstfeltet tillot. Skjemaet og `_valider_grenser` godtar enhver
  // ISO 4217-kode, så det er lista vår som er smal — ikke policyen som er feil.
  const ekstra = valutaer.filter((v) => !VALUTAER.includes(v));
  const valgbare = [...ekstra, ...VALUTAER];
  const sel = el("select", { class: "felt-inp" });
  const rad = (v, tekst) => {
    const o = el("option", { value: v, text: tekst });
    if (v === valgt) o.selected = true;
    sel.append(o);
  };
  rad(VALUTA_INGEN, t("ui.editor.valuta_ingen"));
  for (const v of valgbare) rad(v, v);           // valutakoder oversettes ikke
  sel.addEventListener("change", () => {
    if (sel.value === VALUTA_INGEN) delete g.valuta;
    // Den valgte koden kan alt stå lenger bak i lista: `["NOK","EUR"]` + valg
    // av EUR ga `["EUR","EUR"]`. Halen er resten AV settet, ikke resten av
    // rekka — koden som flyttes fram tas ut der den lå.
    else g.valuta = [sel.value,
      ...valutaer.slice(1).filter((v) => v !== sel.value)];
    // Bare hintet under avhenger av tilstanden; uten flere valutaer er det
    // ingenting som kan bli stående og lyve, og da beholder vi fokus.
    if (valutaer.length > 1) tegnPaaNytt();
  });
  return el("div", { class: "editor-felt-gruppe" },
    el("label", { class: "felt" },
      el("span", { class: "felt-navn", text: t("ui.editor.valuta") }), sel),
    valutaer.length > 1
      ? el("p", { class: "editor-hint",
        text: `${t("ui.editor.valuta_flere")}: ${valutaer.join(", ")}` })
      : null);
}

function handlingKort(h, tegnPaaNytt) {
  h.grenser = (h.grenser && typeof h.grenser === "object") ? h.grenser : {};
  const g = h.grenser;
  return el("div", { class: "editor-kort" },
    el("h4", {}, el("code", { text: h.id || "?" })),
    velg(t("ui.editor.modus"), h.modus, MODUS, "modus.", (v) => { h.modus = v; }),
    tekstfelt(t("ui.editor.belop_maks"), g.belop_maks == null ? "" : g.belop_maks,
      (v) => {
        v = v.trim();
        if (!v) delete g.belop_maks; else g.belop_maks = v;
      }, { inputmode: "decimal" }, t("ui.editor.belop_hint")),
    valutaVelger(g, tegnPaaNytt),
    tidsvinduVelger(g, tegnPaaNytt));
}

function handlingerSeksjon(policy, tegnPaaNytt) {
  policy.handlinger = Array.isArray(policy.handlinger) ? policy.handlinger : [];
  const kort = policy.handlinger.map((h) => handlingKort(h, tegnPaaNytt));
  return el("section", { class: "editor-seksjon",
    "aria-label": t("ui.editor.handlinger") },
    el("h3", { text: t("ui.editor.handlinger") }),
    el("p", { class: "muted", text: t("ui.editor.handlinger_hjelp") }),
    ...kort);
}

function metaSeksjon(policy, erNy) {
  policy.meta = (policy.meta && typeof policy.meta === "object") ? policy.meta : {};
  const m = policy.meta;
  const felt = [
    // policy_id er identiteten; kan settes ved NY, låst ved redigering.
    tekstfelt(t("ui.editor.policy_id"), m.policy_id || "",
      (v) => { m.policy_id = v; }, erNy ? {} : { disabled: "" }),
    tekstfelt(t("ui.editor.bedrift"), m.bedrift || "",
      (v) => { m.bedrift = v || undefined; }),
    tekstfelt(t("ui.editor.versjon"), m.versjon || "",
      (v) => { m.versjon = v; }),
  ];
  return el("section", { class: "editor-seksjon",
    "aria-label": t("ui.editor.meta") },
    el("h3", { text: t("ui.editor.meta") }), ...felt);
}

// Bygg det som skal lagres: hele malen/utkastet med redigerte felt oppå.
function byggInnhold(policy) {
  return policy;                       // policy muteres in-place av feltene
}

// Porten står ved LAGRING, ikke ved tegning: det er lagringen som er den
// farlige handlingen. En grense ingen kontroll kan vise, har eier ennå ikke
// tatt stilling til — og da skal utkastet ikke kunne sendes med den, hverken
// reparert av oss eller uendret. Returnerer handlingene som venter på et valg.
function grenserSomVenterPaaValg(policy) {
  const handlinger = Array.isArray(policy && policy.handlinger)
    ? policy.handlinger : [];
  return handlinger
    .filter((h) => h && h.grenser && typeof h.grenser === "object"
      && (tidsvinduUleselig(h.grenser) || valutaUleselig(h.grenser)))
    .map((h) => h.id || "?");
}

export function visPolicyeditor(hoved, ctx, opts = {}) {
  // opts: { utkast_id?, aapneUtkast: fn(uid), tilbake: fn() }
  const st = { policy: null, utkast_id: opts.utkast_id || null,
               utkastversjon: null, feil: [], laster: true,
               nokkel: null, signatur: null };

  function lagre() {
    const innhold = byggInnhold(st.policy);
    const pid = (st.policy.meta && st.policy.meta.policy_id || "").trim();
    if (!st.utkast_id && !pid) {
      st.feil = [t("ui.editor.policy_id_pakrevd")]; tegn(); return;
    }
    const venter = grenserSomVenterPaaValg(st.policy);
    if (venter.length) {
      st.feil = [`${t("ui.editor.grense_maa_repareres")}: ${venter.join(", ")}`];
      tegn(); return;
    }
    // STABIL idempotensnøkkel per innhold (Codex R1): samme innhold → samme
    // nøkkel, så en retry etter tapt svar REPLAYer i stedet for å duplisere.
    // Endres innholdet, er det en ny operasjon → ny nøkkel.
    const signatur = JSON.stringify(
      { u: st.utkast_id, v: st.utkastversjon, p: pid, i: innhold });
    if (signatur !== st.signatur) {
      st.nokkel = nyIdempotensnokkel(); st.signatur = signatur;
    }
    const jobb = st.utkast_id
      ? redigerUtkast(st.utkast_id, st.utkastversjon, innhold, st.nokkel)
      : opprettUtkast(pid, innhold, st.nokkel);
    jobb.then((res) => {
      meldLive(t("ui.editor.lagret"));
      const uid = res.utkast_id || st.utkast_id;
      if (opts.aapneUtkast) opts.aapneUtkast(uid);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      st.feil = [t(`ui.editor.feil.${e.kode}`, t("ui.editor.lagring_feilet"))];
      tegn();
    });
  }

  function tegn() {
    if (st.laster) { sett(hoved, ...flateHode(t("ui.editor.tittel")),
      TomTilstand({ tittel: t("ui.laster") })); return; }
    if (!st.policy) { sett(hoved, Feiltilstand({})); return; }
    const knapper = el("div", { class: "editor-knapper" });
    const lagreKnapp = el("button", { class: "knapp primar", type: "button",
      text: t("ui.editor.lagre") });
    lagreKnapp.addEventListener("click", lagre);
    const avbryt = el("button", { class: "knapp", type: "button",
      text: t("ui.editor.avbryt") });
    avbryt.addEventListener("click", () => { if (opts.tilbake) opts.tilbake(); });
    knapper.append(lagreKnapp, avbryt);

    const feilboks = st.feil.length
      ? el("div", { class: "editor-feil", role: "alert" },
          el("p", { text: t("ui.editor.har_feil") }),
          el("ul", {}, ...st.feil.map((f) => el("li", { text: f }))))
      : null;

    const barn = [
      ...flateHode(t("ui.editor.tittel"), t("ui.editor.undertittel")),
      metaSeksjon(st.policy, !st.utkast_id),
      rollerSeksjon(st.policy, tegn),
      handlingerSeksjon(st.policy, tegn),
    ];
    if (feilboks) barn.push(feilboks);
    barn.push(knapper);
    sett(hoved, ...barn);
  }

  // --- Last inn utgangspunkt: enten et eksisterende utkast, eller malvalg ---
  if (st.utkast_id) {
    hentJson(`/v1/policyutkast/${st.utkast_id}`).then((detalj) => {
      st.laster = false;
      st.utkastversjon = detalj.utkastversjon;
      st.policy = detalj.innhold || {};
      tegn();
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      st.laster = false; tegn();
    });
  } else if (opts.startPolicy) {
    st.laster = false;
    st.policy = JSON.parse(JSON.stringify(opts.startPolicy));
    tegn();
  } else {
    // Malvelger.
    hentMaler().then((d) => {
      st.laster = false;
      visMalvelger(hoved, ctx, (d && d.maler) || [], (mal) => {
        st.policy = JSON.parse(JSON.stringify(mal.innhold));
        if (st.policy.meta) st.policy.meta.policy_id = "";   // eier setter id
        tegn();
      }, opts.tilbake);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      st.laster = false; sett(hoved, Feiltilstand({}));
    });
  }
}

function visMalvelger(hoved, ctx, maler, paaValg, tilbake) {
  const kort = maler.map((m) => {
    const k = el("button", { class: "mal-kort", type: "button" },
      el("strong", { text: t(`ui.editor.mal.${m.mal_id}`, m.bransjemal) }),
      el("span", { class: "muted",
        text: `${(m.innhold.roller || []).length} ${t("ui.editor.roller")} · `
          + `${(m.innhold.handlinger || []).length} ${t("ui.editor.handlinger")}` }));
    k.addEventListener("click", () => paaValg(m));
    return k;
  });
  const avbryt = el("button", { class: "knapp", type: "button",
    text: t("ui.editor.avbryt") });
  avbryt.addEventListener("click", () => { if (tilbake) tilbake(); });
  sett(hoved,
    ...flateHode(t("ui.editor.velg_mal"), t("ui.editor.velg_mal_hjelp")),
    el("div", { class: "mal-liste" }, ...kort),
    avbryt);
}
