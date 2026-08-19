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
  hentMaler, hentAktivPolicyId, hentJson, opprettUtkast, redigerUtkast,
  nyIdempotensnokkel,
  UautorisertFeil, ApiFeil,
} from "../api.js";
import { meldLive, TomTilstand, Feiltilstand, Faner } from "../komponenter.js";
import { flateHode } from "./felles.js";

const MODUS = ["auto", "auto_med_vilkaar", "alltid_stopp"];

// Skjemaets `meta.policy_id` (`^[a-z0-9-]+$` + `minLength: 3`), som
// `policyadmin._POLICY_ID` håndhever på raden. Klienten er ikke porten — den
// er der for at eier skal møte kravet ved feltet, ikke etter en rundtur.
const POLICY_ID_FORM = /^[a-z0-9-]{3,}$/;

// ... og STØRRELSEN (Codex P3). `policyer_pkey` er `(tenant, policy_id,
// versjon)`, og de tre deler btree-oppføringens tak — serveren regner budsjettet
// som `_MAKS_NOKKELBYTES - _VERSJONSRESERVE` MINUS tenantens egne byte.
// Klienten kjenner ikke tenanten, så den kan ikke speile grensen eksakt.
//
// Den speiler derfor den TENANT-UAVHENGIGE delen: en id over dette taket
// avvises av enhver tenant, så kontrollen kan aldri avvise noe serveren ville
// godtatt. Båndet rett under taket er fortsatt serverens å svare på — og den
// svarer nå `policy_id_for_stor`, ikke `utkast_feilformet`, så eier får vite at
// det er id-en som skal forkortes og ikke dokumentet som skal repareres.
const POLICY_ID_MAKS_BYTE = 2400 - 64;

// UTF-8-byte, som `octet_length` i Postgres og `_nokkelbytes` i porten —
// `String.length` teller UTF-16-enheter og ville sagt for lite om en id med
// tegn utenfor ASCII.
function byteLengde(s) {
  return new TextEncoder().encode(s).length;
}

// `hint` er reglene feltet faktisk håndhever, sagt FØR man skriver. Uten den
// måtte eier gjette formatet og få svaret fra validatoren etterpå — og bare
// hvis feilen i det hele tatt nådde skjermen. Knyttes med `aria-describedby`,
// så den leses opp sammen med etiketten.
function tekstfelt(etikett, verdi, paaEndre, attrs = {}, hint = null) {
  const inp = el("input", {
    type: "text", value: verdi == null ? "" : String(verdi),
    class: "felt-inp", ...attrs,
  });
  inp.addEventListener("input", () => paaEndre(inp.value));
  const id = "f-" + Math.random().toString(36).slice(2, 9);
  inp.id = id;
  // `span`, ikke `p`: innholdet i en `label` er fraseinnhold.
  if (!hint) {
    return el("label", { class: "felt" },
      el("span", { class: "felt-navn", text: etikett }), inp);
  }
  const hid = `${id}-hint`;
  inp.setAttribute("aria-describedby", hid);
  return el("label", { class: "felt" },
    el("span", { class: "felt-navn", text: etikett }), inp,
    el("span", { class: "felt-hint", id: hid, text: hint }));
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

// Hva peker på rollen? En referert rolle kan ikke bare fjernes: referansen
// ville stått igjen med et navn som ikke finnes, og validatoren avviser hele
// policyen med «ukjent rolle» — én linje per handling. Det er nøyaktig det
// som skjedde: eier fjernet en rolle og fikk seks feil som pekte på
// handlinger han aldri hadde rørt.
//
// `menneskelig_overstyring.krever_rolle` er en rollereferanse på lik linje med
// `tillatt_for` (Codex P2): `policy_validator/schema.py` avviser policyen på
// nøyaktig samme måte når DEN rollen mangler. Så lenge vakten bare så på
// handlingene, framsto en rolle som kun brukes til overstyring som fjernbar —
// og knappen førte rett i den fella vakten er til for å stenge.
//
// Vakten leser referansepunktene, som hopper over `tillatt_for` som ikke er en
// liste (Codex P2). Et ULAGRET utkast kan inneholde hva som helst der:
// opprett/rediger tar imot en vilkårlig dict, og skjemavalideringen er et eget
// steg — server-siden klassifiserer til og med bevisst en ikke-liste her som
// «tom» framfor å avvise den, så verdien når fram til detaljsvaret. Med
// `.includes` rett på verdien kastet editoren `TypeError` mens den TEGNET, og
// eieren kunne dermed ikke åpne utkastet for å REPARERE det. En ikke-liste har
// ingen rollereferanser; eieren slipper inn og kan rette den.
function referanserTilRolle(policy, rolleId) {
  if (!rolleId) return [];
  const ut = [];
  for (const punkt of referansepunkter(policy)) {
    if (punkt.les() !== rolleId) continue;
    if (!ut.includes(punkt.merkelapp)) ut.push(punkt.merkelapp);
  }
  return ut;
}

// Å endre rolle-ID-en er et NAVNEBYTTE, ikke en ny rolle: referansene som
// pekte på det gamle navnet skal fortsatt peke på den samme rollen. Uten dette
// gjorde `agent` → `agent-ny` handlingenes `tillatt_for: ["agent"]` foreldreløs,
// og policyen havnet i nøyaktig samme «ukjent rolle»-tilstand som
// fjerningsvakten finnes for å stenge — bare via en annen dør.
//
// Men navnebyttet kan IKKE gjøres som global tekstutskifting (Codex P2). Et
// navn skrives tegn for tegn, og underveis passerer det gjerne en ANNEN rolles
// id: med rollene `admin` og `ad` går `ad` → `adm` → `admi` → `admin` →
// `admin2`. I mellomsteget slukte raden den ekte `admin`-rollens referanser,
// og neste tastetrykk flyttet dem videre til `admin2` — en stille
// rettighetsendring som er STRUKTURELT gyldig, så validatoren på serveren sier
// ingenting.
//
// Referansene eies derfor av RADEN, ikke av strengen som står i den: hvert
// referansepunkt er et STED (handling nr. 2 sin første `tillatt_for`-plass,
// eller overstyringsfeltet), knyttet til én rad ved tegning. Et navnebytte
// skriver bare radens egne punkter.
function referansepunkter(policy) {
  const punkter = [];
  const handlinger = Array.isArray(policy.handlinger) ? policy.handlinger : [];
  for (const h of handlinger) {
    if (!h || !Array.isArray(h.tillatt_for)) continue;
    h.tillatt_for.forEach((_, i) => punkter.push({
      merkelapp: h.id,
      les: () => h.tillatt_for[i],
      skriv: (ny) => { h.tillatt_for[i] = ny; },
    }));
  }
  const mo = policy.menneskelig_overstyring;
  if (mo && typeof mo === "object") {
    punkter.push({
      merkelapp: t("ui.editor.rolle_i_bruk_overstyring"),
      les: () => mo.krever_rolle,
      skriv: (ny) => { mo.krever_rolle = ny; },
    });
  }
  return punkter;
}

// Hvert referansepunkt får ÉN eier: raden som har id-en punktet peker på i det
// seksjonen tegnes. Rekkefølgen avgjør ved duplikate rolle-id-er (et utkast kan
// ha dem), og et punkt ingen rad eier blir stående i fred — det er en «ukjent
// rolle»-referanse validatoren skal si ifra om, ikke noe et navnebytte i en
// annen rad skal dra med seg.
function fordelReferanser(roller, policy) {
  const ledige = referansepunkter(policy);
  return roller.map((r) => {
    const id = r && r.id;
    if (!id) return [];
    const mine = ledige.filter((p) => p.les() === id);
    for (const p of mine) ledige.splice(ledige.indexOf(p), 1);
    return mine;
  });
}

function rollerSeksjon(policy, tegnPaaNytt) {
  policy.roller = Array.isArray(policy.roller) ? policy.roller : [];
  const eide = fordelReferanser(policy.roller, policy);
  const liste = el("div", { class: "editor-liste" });
  // Vaktene til ALLE radene, så et navnebytte i én rad kan tegne om de andre:
  // `ubrukt` → `agent` gjør raden låst, og den gamle `agent`-raden fri.
  const vakter = [];
  policy.roller.forEach((r, i) => {
    const fjern = el("button", { class: "knapp liten", type: "button",
      text: t("ui.editor.fjern") });
    const hint = el("p", { class: "editor-hint" });
    // Vakten ble tidligere regnet ut ÉN gang, da raden ble tegnet, mens
    // ID-feltet fortsatte å mutere `r.id` uten å tegne om (Codex P2). Retter
    // eier et ugyldig utkast ved å gi en ubrukt rolle det navnet `tillatt_for`
    // peker på, sto «Fjern» igjen aktiv fra forrige navn — og fjernet en rolle
    // som nå var påkrevd. Vakten regnes derfor om for hvert tastetrykk.
    const oppdaterVakt = () => {
      const brukt = referanserTilRolle(policy, r.id);
      // En rolle i bruk får ikke en «Fjern»-knapp som fører rett i grøfta.
      // Knappen blir stående, men deaktivert, med hvilke handlinger som holder
      // rollen — så eier kan flytte dem først hvis det ER meningen.
      if (brukt.length) {
        fjern.setAttribute("disabled", "");
        fjern.setAttribute("title",
          `${t("ui.editor.rolle_i_bruk")}: ${brukt.join(", ")}`);
        hint.textContent =
          `${t("ui.editor.rolle_i_bruk")} (${brukt.length}): ${brukt.join(", ")}`;
        hint.removeAttribute("hidden");
      } else {
        fjern.removeAttribute("disabled");
        fjern.removeAttribute("title");
        hint.textContent = "";
        hint.setAttribute("hidden", "");
      }
      return brukt;
    };
    vakter.push(oppdaterVakt);
    const rad = el("div", { class: "editor-rad" },
      tekstfelt(t("ui.editor.rolle_id"), r.id, (v) => {
        r.id = v;
        // Tomt navn propagerer IKKE: eier som tømmer feltet for å skrive noe
        // nytt skal ikke få `tillatt_for: [""]` underveis. Referansene blir
        // stående på forrige navn til det nye er skrevet.
        if (v) for (const punkt of eide[i]) punkt.skriv(v);
        for (const oppdater of vakter) oppdater();
      }),
      tekstfelt(t("ui.editor.rolle_beskrivelse"), r.beskrivelse || "",
        (v) => { r.beskrivelse = v || undefined; }));
    fjern.addEventListener("click", () => {
      // Siste port: knappen kan ha blitt aktiv i et tidligere tegnetrinn, og
      // en deaktivert knapp er uansett bare en UI-tilstand. Referansene
      // avgjør, i det øyeblikket det klikkes.
      if (oppdaterVakt().length) return;
      policy.roller.splice(i, 1); tegnPaaNytt();
    });
    rad.append(hint, fjern);
    oppdaterVakt();
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

// Valutamengden er ISO 4217 slik `lesing.py` fører den — ikke `^[A-Z]{3}$`,
// og ikke en håndskrevet kortliste. Skjemaets mønster er for VIDT («XXX» er
// tre store bokstaver, men ingen valuta man kan sette en beløpsgrense i), og
// en kortliste er for SMAL: eier som skal sette CAD, CHF eller JPY hadde
// ingen vei dit, selv om serveren godtar dem. Begge feilene ender samme sted
// — en policy editoren sier ja til og `_valider_grenser` leser som
// `policy_korrupt`. `test_ui_kontrakt.py` pinner denne lista mot `ISO4217`,
// så en ny kode i registeret sier fra her og ikke hos en kunde.
const VALUTAER = [
  "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
  "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
  "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
  "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
  "ERN", "ETB", "EUR", "FJD", "FKP", "GBP", "GEL", "GHS", "GIP", "GMD",
  "GNF", "GTQ", "GYD", "HKD", "HNL", "HTG", "HUF", "IDR", "ILS", "INR",
  "IQD", "IRR", "ISK", "JMD", "JOD", "JPY", "KES", "KGS", "KHR", "KMF",
  "KPW", "KRW", "KWD", "KYD", "KZT", "LAK", "LBP", "LKR", "LRD", "LSL",
  "LYD", "MAD", "MDL", "MGA", "MKD", "MMK", "MNT", "MOP", "MRU", "MUR",
  "MVR", "MWK", "MXN", "MYR", "MZN", "NAD", "NGN", "NIO", "NOK", "NPR",
  "NZD", "OMR", "PAB", "PEN", "PGK", "PHP", "PKR", "PLN", "PYG", "QAR",
  "RON", "RSD", "RUB", "RWF", "SAR", "SBD", "SCR", "SDG", "SEK", "SGD",
  "SHP", "SLE", "SOS", "SRD", "SSP", "STN", "SVC", "SYP", "SZL", "THB",
  "TJS", "TMT", "TND", "TOP", "TRY", "TTD", "TWD", "TZS", "UAH", "UGX",
  "USD", "UYU", "UZS", "VES", "VND", "VUV", "WST", "XAF", "XCD", "XOF",
  "XPF", "YER", "ZAR", "ZMW", "ZWG",
];
// 155 koder er en lang rulle å bla i for den som skal velge NOK. De vi
// faktisk møter står derfor øverst i sin egen gruppe — uten at det gjør noen
// av de øvrige utilgjengelige.
const VANLIGE_VALUTAER = ["NOK", "EUR", "USD", "SEK", "DKK", "GBP"];
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

// Samme regel på det andre feltet, og med den samme autoriteten:
// `_valider_grenser` krever 1–10 unike koder som finnes i `ISO4217`. En
// dublett, en bar streng, en tom liste — eller «XXX», som består skjemaets
// mønster uten å være en valuta — er former nedtrekket ikke kan vise.
const erValuta = (k) => typeof k === "string" && VALUTAER.includes(k);

function valutaUleselig(g) {
  if (!("valuta" in g) || g.valuta === undefined) return false;
  const v = g.valuta;
  return !Array.isArray(v) || !v.length || v.length > 10
    || v.some((k) => !erValuta(k)) || new Set(v).size !== v.length;
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
const VALUTA_MAKS = 10;                  // `_valider_grenser`: 1–10 koder

// Ett nedtrekk over hele den kanoniske mengden. `opptatt` er kodene de ANDRE
// radene alt bærer: en dublett vrakes av `_valider_grenser` (`_unik`) og gjør
// den aktive policyen uleselig, så den skal ikke være valgbar i det hele tatt
// — det er billigere enn å rydde opp i den etterpå. De vanlige seks står i
// sin egen gruppe øverst, så kortlisten er en snarvei og ikke et tak.
function valutanedtrekk(valgt, opptatt, etikett, paaEndre) {
  const sel = el("select", { class: "felt-inp", "aria-label": etikett });
  const legg = (foreldre, v, tekst) => {
    const o = el("option", { value: v, text: tekst });
    if (v === valgt) o.selected = true;
    foreldre.append(o);
  };
  // Legg-til-nedtrekket står på en plassholder til eier velger; en rad står
  // alltid på sin egen kode.
  if (valgt === VALUTA_INGEN) legg(sel, VALUTA_INGEN, etikett);
  const vanlige = el("optgroup", { label: t("ui.editor.valuta_vanlige") });
  const ovrige = el("optgroup", { label: t("ui.editor.valuta_ovrige") });
  for (const v of VANLIGE_VALUTAER) {
    if (!opptatt.includes(v)) legg(vanlige, v, v);    // koder oversettes ikke
  }
  for (const v of VALUTAER) {
    if (!VANLIGE_VALUTAER.includes(v) && !opptatt.includes(v)) {
      legg(ovrige, v, v);
    }
  }
  sel.append(vanlige, ovrige);
  sel.addEventListener("change", () => {
    if (sel.value !== VALUTA_INGEN) paaEndre(sel.value);
  });
  return sel;
}

function valutaVelger(g, tegnPaaNytt) {
  // Samme regel som for tidsvinduet: en lagret liste nedtrekket ikke kan vise,
  // normaliseres ikke i det stille — eier velger. Det som KAN berges av koder
  // vises som ett av valgene, så «behold» er en verdi eier ser før den skrives.
  if (valutaUleselig(g)) {
    // Berging måles mot den KANONISKE mengden, ikke mot mønsteret: `["XXX",
    // "XXX"]` ville ellers blitt berget til `["XXX"]`, sluppet gjennom porten
    // og aktivert — og så lest som `policy_korrupt` av `_valider_grenser`.
    // Da hadde reparasjonen bare flyttet feilen lenger unna feltet.
    const berget = [...new Set((Array.isArray(g.valuta) ? g.valuta : [g.valuta])
      .filter(erValuta))].slice(0, 10);
    const valg = [[t("ui.editor.grense_fjern"),
      () => { delete g.valuta; tegnPaaNytt(); }]];
    if (berget.length) {
      valg.push([`${t("ui.editor.grense_behold")}: ${berget.join(", ")}`,
        () => { g.valuta = berget; tegnPaaNytt(); }]);
    }
    return reparasjon(t("ui.editor.valuta"), g.valuta, valg);
  }
  // Valuta er en LISTE i skjemaet, og kontrollen må kunne det lista kan.
  // Ett nedtrekk som byttet den første koden kunne bare bevare flere valutaer
  // som alt lå der: `["NOK"]` + EUR ga `["EUR"]`, og det fantes ingen vei til
  // `["NOK","EUR"]` i det hele tatt. Fritekstfeltet det avløste kunne legge
  // til. Derfor én rad per kode, med «Fjern», og et eget nedtrekk som legger
  // til — samme form som rollelista over.
  const valutaer = Array.isArray(g.valuta) ? g.valuta : [];
  // Fraværet av rader ER «ingen valutabegrensning»: motoren sjekker valuta
  // bare når feltet finnes, så en tom liste ville vært en annen — og ugyldig
  // — tilstand enn den eier ser.
  const skriv = (liste) => {
    if (liste.length) g.valuta = liste; else delete g.valuta;
    // Alltid ny tegning: hvilke koder de øvrige radene kan tilby, avhenger av
    // hva de andre bærer nå.
    tegnPaaNytt();
  };
  const rader = valutaer.map((kode, i) => {
    const fjern = el("button", { class: "knapp liten", type: "button",
      text: t("ui.editor.fjern") });
    fjern.addEventListener("click",
      () => skriv(valutaer.filter((_, j) => j !== i)));
    return el("div", { class: "editor-rad valuta-rad" },
      valutanedtrekk(kode, valutaer.filter((_, j) => j !== i),
        `${t("ui.editor.valuta")} ${i + 1}`,
        (v) => { const ny = [...valutaer]; ny[i] = v; skriv(ny); }),
      fjern);
  });
  // `_valider_grenser`: 1–10 koder. Taket er serverens, så det skal stå her
  // og ikke oppdages som en avvist lagring.
  const legg = valutaer.length < VALUTA_MAKS
    ? el("div", { class: "editor-rad valuta-legg-til" },
      valutanedtrekk(VALUTA_INGEN, valutaer, t("ui.editor.valuta_legg_til"),
        (v) => skriv([...valutaer, v])))
    : el("p", { class: "editor-hint", text: t("ui.editor.valuta_maks") });
  return el("div", { class: "editor-felt-gruppe" },
    el("p", { class: "felt-navn", text: t("ui.editor.valuta") },
    ), ...rader,
    valutaer.length ? null
      : el("p", { class: "editor-hint", text: t("ui.editor.valuta_ingen") }),
    legg);
}

function handlingKort(h, tegnPaaNytt, policy, grunnlag) {
  h.grenser = (h.grenser && typeof h.grenser === "object") ? h.grenser : {};
  const g = h.grenser;
  return el("div", { class: "editor-kort" },
    el("h4", {}, el("code", { text: h.id || "?" })),
    // `tegnPaaNytt()` er ikke overflødig her (Codex P2): handlingsvelgeren
    // over kortet viser hver handlings MODUS — det raskeste svaret på «hva
    // gjør denne». Muterte vi bare `h.modus`, ble knappen stående med den
    // gamle verdien til noe ANNET tilfeldigvis tegnet på nytt (valuta,
    // tidsvindu), og navigatoren motsa da verdien som faktisk ville blitt
    // lagret. Samme form som bryteren i tidsvinduvelgeren: en endring som
    // vises et annet sted enn der den gjøres, tegner flaten på nytt.
    velg(t("ui.editor.modus"), h.modus, MODUS, "modus.",
      (v) => { h.modus = v; tegnPaaNytt(); }),
    tekstfelt(t("ui.editor.belop_maks"), g.belop_maks == null ? "" : g.belop_maks,
      (v) => {
        v = v.trim();
        if (!v) delete g.belop_maks; else g.belop_maks = v;
      }, { inputmode: "decimal" }, t("ui.editor.belop_hint")),
    valutaVelger(g, tegnPaaNytt),
    tidsvinduVelger(g, tegnPaaNytt),
    vilkaarFelt(h, policy, tegnPaaNytt, grunnlag));
}

// --- Vilkår per handling (047, klarsignal §5) ------------------------------
// To nivåer: policyens EGNE vilkår (redigerbare, nedtrekk fra policyens
// deklarerte verifikatorer og deres betrodd_for) og PLATTFORMVILKÅRENE
// (malautorisasjonsregisteret) — låst rad med aria-disabled OG
// aria-describedby til forklaringen; de kan ikke fjernes herfra, og
// valideringen avviser fjerning uansett vei (port 31/34). Låsen gjelder den
// VELFORMEDE raden: en oppføring som bare bærer plattformnavnet, uten en
// betrodd verifikator, er ikke et håndhevet vilkår ennå og får en
// reparasjonsvei som beholder navnet (se `vilkaarErVelformet`). Registeret leses
// fra serveren — ingen hardkodet liste (port 32); mangler grunnlaget
// (nettfeil), er ALT låst: fail-closed, aldri en flate som lover en
// fjerning serveren nekter.
// Et UTKAST er med vilje ustrukturert til det valideres: skriveveien tar
// imot vilkårlige dicter, og skjemaporten står i `valider_utkast`, ikke i
// lagringen. Et lagret utkast kan derfor bære `vilkaar: [null]` eller en
// naken streng (aktiveringsporten leser begge former). Leste editoren
// `v.navn` rått, kastet den TypeError på nettopp de utkastene eier trengte
// å åpne for å RETTE dem — flaten låste seg på det eneste stedet feilen
// kunne fjernes (Codex P2). Uleselige oppføringer vises derfor som det de
// er, og kan fjernes som alle andre.
function vilkaarNavn(v) {
  if (typeof v === "string") return v;
  if (v && typeof v === "object" && typeof v.navn === "string") return v.navn;
  return null;
}

function vilkaarVerifikator(v) {
  return (v && typeof v === "object" && typeof v.verifikator === "string")
    ? v.verifikator : null;
}

// VELFORMET = det serveren faktisk godtar, i BEGGE lag (Codex P2). Målte
// denne prøven bare §5-semantikken — deklarert og betrodd verifikator —
// var en rad som `{navn, verifikator, min: "ugyldig"}` «velformet» her og
// ble låst, mens `policy-schema-v0.2.json` avviste den på STRUKTUR-laget.
// Låsen tok da igjen fra eier både rettingen og fjerningen, og utkastet
// kunne bare forkastes. Låsen er en påstand om at raden ER et håndhevet
// plattformvilkår; da må den måle hele formen den påstanden hviler på:
//
//   lag 1 (skjemaet): et objekt med `navn` og `verifikator`, ingen andre
//     felter enn `min`, navnet på registerformen, `min` numerisk;
//   lag 2 (`schema.py` §5): verifikatoren er DEKLARERT og betrodd for
//     navnet — de samme kravene «legg til»-nedtrekkene bygger av.
//
// Én definisjon av en gyldig vilkårsrad på flaten, ikke to.
const VILKAAR_TILLATTE_FELT = new Set(["navn", "verifikator", "min"]);
const VILKAAR_NAVNFORM = /^[a-z0-9_]+$/;

// `min` er valgfri, men numerisk når den finnes (skjemaet: `type: number`).
function vilkaarMin(v) {
  return (v && typeof v === "object" && typeof v.min === "number"
    && Number.isFinite(v.min)) ? v.min : null;
}

function vilkaarErVelformet(v, verifikatorer) {
  if (!v || typeof v !== "object" || Array.isArray(v)) return false;
  if (Object.keys(v).some((k) => !VILKAAR_TILLATTE_FELT.has(k))) return false;
  const navn = vilkaarNavn(v);
  const vid = vilkaarVerifikator(v);
  if (navn === null || vid === null) return false;
  if (!VILKAAR_NAVNFORM.test(navn)) return false;
  if ("min" in v && vilkaarMin(v) === null) return false;
  const dekl = verifikatorer[vid];
  return !!dekl && Array.isArray(dekl.betrodd_for)
    && dekl.betrodd_for.includes(navn);
}

// Hvilke av policyens egne verifikatorer som KAN bære et gitt vilkårsnavn.
function betroddeFor(navn, verifikatorer) {
  return Object.keys(verifikatorer).sort().filter((vid) => {
    const dekl = verifikatorer[vid];
    return dekl && Array.isArray(dekl.betrodd_for)
      && dekl.betrodd_for.includes(navn);
  });
}

function vilkaarFelt(h, policy, tegnPaaNytt, grunnlag) {
  const vilkaar = Array.isArray(h.vilkaar) ? h.vilkaar : [];
  const plattform = new Set(
    ((grunnlag && grunnlag.plattformvilkar) || [])
      .map((v) => v.vilkar_type));
  const laastAlt = !grunnlag;
  const forklaringId = `vilkaar-laast-${h.id || "x"}`;
  // Policyens EGNE deklarasjoner. Låsen, reparasjonen og «legg til» måler
  // alle tre mot disse — derfor leses de ett sted, før radene tegnes.
  const verifikatorer = (policy && typeof policy.verifikatorer === "object"
    && policy.verifikatorer) || {};
  const vids = Object.keys(verifikatorer).sort();

  const rader = vilkaar.map((v, i) => {
    const navn = vilkaarNavn(v);
    const fjernKnapp = () => {
      const fjern = el("button", { class: "knapp liten",
        type: "button", text: t("ui.editor.vilkaar_fjern") });
      fjern.addEventListener("click", () => {
        h.vilkaar.splice(i, 1);
        if (!h.vilkaar.length) delete h.vilkaar;
        tegnPaaNytt();
      });
      return fjern;
    };
    // `laastAlt` beholder fail-closed-regelen uendret: uten grunnlaget vet
    // vi ikke hva som ER et plattformvilk\u00e5r, og da l\u00e5ses alt. En uleselig
    // oppf\u00f8ring kan uansett aldri matche registeret, som sl\u00e5r opp p\u00e5 navn.
    const plattformNavn = navn !== null && plattform.has(navn);
    // L\u00c5SEN GJELDER DEN VELFORMEDE RADEN (Codex P2). En rad som bare B\u00c6RER
    // et registrert plattformnavn er ikke et h\u00e5ndhevet plattformvilk\u00e5r \u2014
    // den er et utkast p\u00e5 vei dit. L\u00e5ste vi p\u00e5 navnet alene, ble en
    // oppf\u00f8ring med manglende eller ubetrodd `verifikator` verken
    // redigerbar eller fjernbar, mens valideringen avviste nettopp den
    // verifikatorpekeren: utkastet kunne bare forkastes, enda et utkast med
    // vilje f\u00e5r b\u00e6re ugyldige mellomtilstander. Navnet er likevel det
    // registeret krever, s\u00e5 reparasjonen BEHOLDER det og ber bare om
    // verifikatoren som mangler. Har policyen ingen verifikator betrodd for
    // navnet, kan raden ikke gj\u00f8res gyldig i det hele tatt, og da er
    // fjerning den eneste veien ut.
    //
    // VELFORMET m\u00e5les mot HELE skjemaformen, ikke bare verifikatortilliten
    // (Codex P2): en rad med ugyldig `min` eller et ukjent felt avvises av
    // serveren p\u00e5 strukturlaget, og en l\u00e5s p\u00e5 den gjorde utkastet like
    // ureparerbart som navnel\u00e5sen gjorde. Se `vilkaarErVelformet`.
    const kandidater = (plattformNavn && !laastAlt)
      ? betroddeFor(navn, verifikatorer) : [];
    const erPlattform = laastAlt
      || (plattformNavn && vilkaarErVelformet(v, verifikatorer));
    const rad = el("li", { class: "vilkaar-rad" },
      el("span", {},
        el("code", { text: navn || t("ui.editor.vilkaar_ulesbart") }),
        ` \u2014 ${vilkaarVerifikator(v) || "?"}`),
      erPlattform
        ? el("span", { class: "site-badge info", "aria-disabled": "true",
            "aria-describedby": forklaringId,
            text: t("ui.editor.vilkaar_laast") })
        : (plattformNavn && kandidater.length
            ? (() => {
                const repId = `vk-rep-${h.id || "x"}-${i}`;
                const velg = el("select", { id: repId },
                  ...kandidater.map((vid) =>
                    el("option", { value: vid, text: vid })));
                const knapp = el("button", { class: "knapp liten",
                  type: "button", text: t("ui.editor.vilkaar_reparer") });
                knapp.addEventListener("click", () => {
                  // Navnet er registerets krav og skal overleve
                  // reparasjonen; bare verifikatoren settes. En GYLDIG
                  // `min` er eierens egen terskel og følger med — alt
                  // annet (ugyldig `min`, ukjente felter) er nettopp det
                  // skjemaet avviste, og skal ikke overleve reparasjonen.
                  const rettet = { navn, verifikator: velg.value };
                  const min = vilkaarMin(v);
                  if (min !== null) rettet.min = min;
                  h.vilkaar[i] = rettet;
                  tegnPaaNytt();
                });
                return el("span", { class: "vilkaar-reparer" },
                  el("label", { for: repId,
                    text: t("ui.editor.vilkaar_reparer_hint") }),
                  velg, knapp);
              })()
            : fjernKnapp()));
    return rad;
  });

  // Legg til: verifikator-nedtrekk fra policyens EGNE deklarasjoner,
  // vilkårsnavn fra den valgte verifikatorens betrodd_for (§5) — ukjente
  // kombinasjoner kan ikke velges, og serveren avviser dem uansett.
  let leggTil = null;
  if (!laastAlt && vids.length) {
    const vidValg = el("select", { id: `vk-vid-${h.id || "x"}` },
      ...vids.map((v) => el("option", { value: v, text: v })));
    const navnValg = el("select", { id: `vk-navn-${h.id || "x"}` });
    const fyllNavn = () => {
      const betrodd = (verifikatorer[vidValg.value]
        && verifikatorer[vidValg.value].betrodd_for) || [];
      sett(navnValg, ...betrodd.map((n) =>
        el("option", { value: n, text: n })));
    };
    fyllNavn();
    vidValg.addEventListener("change", fyllNavn);
    const knapp = el("button", { class: "knapp liten", type: "button",
      text: t("ui.editor.vilkaar_legg_til") });
    knapp.addEventListener("click", () => {
      if (!navnValg.value) return;
      h.vilkaar = Array.isArray(h.vilkaar) ? h.vilkaar : [];
      if (h.vilkaar.some((v) => vilkaarNavn(v) === navnValg.value
          && vilkaarVerifikator(v) === vidValg.value)) return;
      h.vilkaar.push({ navn: navnValg.value, verifikator: vidValg.value });
      tegnPaaNytt();
    });
    leggTil = el("div", { class: "vilkaar-legg-til" },
      el("label", { for: `vk-vid-${h.id || "x"}`,
        text: t("ui.editor.vilkaar_verifikator") }), vidValg,
      el("label", { for: `vk-navn-${h.id || "x"}`,
        text: t("ui.editor.vilkaar_navn") }), navnValg,
      knapp);
  }

  return el("div", { class: "vilkaar-felt" },
    el("h5", { text: t("ui.editor.vilkaar") }),
    el("p", { class: "felt-hint", id: forklaringId,
      text: laastAlt ? t("ui.editor.vilkaar_grunnlag_mangler")
                     : t("ui.editor.vilkaar_plattform_forklaring") }),
    rader.length ? el("ul", { class: "vilkaar-liste" }, ...rader)
                 : el("p", { class: "muted",
                     text: t("ui.editor.vilkaar_ingen") }),
    leggTil);
}

// --- Menneskelig overstyring (047, klarsignal §4) --------------------------
// ETT felt per policy; fravær er deny og vises som TILSTAND («ingen
// (standard)»), aldri som tomhet. Hvert (grunnkode × handling)-par er én
// rad; nedtrekkene er policyens egne handlinger/roller og motorens
// godkjennbare grunnkoder (fra editorgrunnlaget — port 29/30: ukjent
// handling/rolle og ikke-godkjennbar grunnkode kan ikke velges, og
// schema.py avviser dem uansett om noe sendes utenom).
function overstyringSeksjon(policy, tegnPaaNytt, grunnlag) {
  const mo = (policy.menneskelig_overstyring
    && typeof policy.menneskelig_overstyring === "object")
    ? policy.menneskelig_overstyring : null;
  const roller = (policy.roller || []).map((r) => r.id).filter(Boolean);
  const handlinger = (policy.handlinger || []).map((h) => h.id)
    .filter(Boolean);
  // Hvilket FELT hver grunnkode krever, fra serverens `godkjennbare_krav`
  // — som leser motorens egen `LOFTBARE_GRUNNKODER` (Codex P1). Et par
  // alene er ikke en overstyring: `_loft_policy` bygger løftet av
  // `belop_maks` eller `valuta`, og uten verdien gir den None, altså STOPP
  // ved HVER godkjenning.
  //
  // FAIL-CLOSED, som resten av grunnlaget (port 30 er en LUKKING): en
  // grunnkode vi ikke vet kravet til kan ikke tilbys, for da kan flaten
  // ikke bygge en oppføring som virker. Er kravlista borte, er lista over
  // valgbare koder tom, og seksjonen sier «grunnlaget mangler» som ellers.
  const overstyringKrav = {};
  for (const k of (grunnlag && grunnlag.godkjennbare_krav) || []) {
    if (k && typeof k.grunnkode === "string" && typeof k.krever === "string") {
      overstyringKrav[k.grunnkode] = k.krever;
    }
  }
  const grunnkoder = ((grunnlag && grunnlag.godkjennbare_grunnkoder) || [])
    .filter((g) => overstyringKrav[g]);

  const deler = [el("h3", { text: t("ui.editor.overstyring") })];
  if (!mo) {
    // Fraværet ER en tilstand: deny-by-default, sagt med ord.
    deler.push(el("p", { class: "felt-hint",
      text: t("ui.editor.overstyring_ingen") }));
  } else {
    deler.push(velg(t("ui.editor.overstyring_rolle"),
      mo.krever_rolle || "", roller, "",
      (v) => { mo.krever_rolle = v; }));
    const par = Array.isArray(mo.godkjennbare) ? mo.godkjennbare : [];
    deler.push(el("ul", { class: "overstyring-liste" },
      ...par.map((e2, i) => {
        const fjern = el("button", { class: "knapp liten", type: "button",
          text: t("ui.editor.overstyring_fjern") });
        fjern.addEventListener("click", () => {
          par.splice(i, 1);
          if (!par.length) delete policy.menneskelig_overstyring;
          tegnPaaNytt();
        });
        const o = (e2 && typeof e2 === "object") ? e2 : {};
        // Verdien motoren løfter TIL vises alltid når den finnes — det er
        // den som avgjør om overstyringen kan virke i det hele tatt.
        const verdi = o.belop_maks
          ? ` (${o.belop_maks} ${o.valuta || ""})`
          : (o.valuta ? ` (${o.valuta})` : null);
        return el("li", {},
          el("span", { text: t("ui.editor.overstyring_par")
            .replace("{grunnkode}", o.grunnkode || "?")
            .replace("{handling}", o.handling || "?") }),
          verdi ? el("span", { class: "sub", text: verdi }) : null,
          fjern);
      })));
    const slettAlt = el("button", { class: "knapp liten", type: "button",
      text: t("ui.editor.overstyring_slett") });
    slettAlt.addEventListener("click", () => {
      delete policy.menneskelig_overstyring;
      tegnPaaNytt();
    });
    deler.push(slettAlt);
  }

  // Legg til par — aktiverer feltet når det ikke finnes. Fail-closed på
  // grunnlaget: uten listen over godkjennbare grunnkoder kan ingenting
  // legges til (port 30 er en LUKKING, ikke en anbefaling).
  if (grunnkoder.length && handlinger.length && roller.length) {
    const gkValg = el("select", { id: "mo-grunnkode" },
      ...grunnkoder.map((g) => el("option", { value: g,
        text: t(`grunnkode.${g}`, g) })));
    const hValg = el("select", { id: "mo-handling" },
      ...handlinger.map((h) => el("option", { value: h, text: h })));
    const krav = overstyringKrav;
    const belop = el("input", { type: "text", id: "mo-belop",
      class: "felt-inp", inputmode: "decimal" });
    const valuta = el("input", { type: "text", id: "mo-valuta",
      class: "felt-inp", maxlength: "3" });
    const belopRad = el("div", { class: "skjemarad" },
      el("label", { for: "mo-belop",
        text: t("ui.editor.overstyring_belop_maks") }), belop);
    const valutaRad = el("div", { class: "skjemarad" },
      el("label", { for: "mo-valuta",
        text: t("ui.editor.overstyring_valuta") }), valuta);
    const feilUt = el("p", { class: "skjemafeil", id: "mo-feil" });
    const nullstillFeil = () => {
      feilUt.textContent = "";
      for (const inp of [belop, valuta]) {
        inp.removeAttribute("aria-invalid");
        inp.removeAttribute("aria-errormessage");
      }
    };
    const settFeil = (inp, tekst) => {
      feilUt.textContent = tekst;
      inp.setAttribute("aria-invalid", "true");
      inp.setAttribute("aria-errormessage", "mo-feil");
      inp.focus();
    };
    belop.addEventListener("input", nullstillFeil);
    valuta.addEventListener("input", nullstillFeil);
    // `belop_maks` drar `valuta` med seg (skjemaets `dependentRequired`):
    // et beløpstak uten valuta er ikke et beløp.
    const synkFelter = () => {
      const k = krav[gkValg.value];
      belopRad.hidden = k !== "belop_maks";
      valutaRad.hidden = k !== "belop_maks" && k !== "valuta";
    };
    gkValg.addEventListener("change", synkFelter);
    synkFelter();
    const leggTil = el("button", { class: "knapp liten", type: "button",
      text: t("ui.editor.overstyring_legg_til") });
    leggTil.addEventListener("click", () => {
      nullstillFeil();
      const k = krav[gkValg.value];
      const oppf = { grunnkode: gkValg.value, handling: hValg.value };
      // Fail-closed på FORMEN også: serveren avviser uansett (mønstrene er
      // skjemaets), men eier skal få vite det her og nå — ikke etter en
      // runde med validering på et frosset utkast.
      if (k === "belop_maks" || k === "valuta") {
        const v = valuta.value.trim().toUpperCase();
        if (!/^[A-Z]{3}$/.test(v)) {
          settFeil(valuta, t("ui.editor.overstyring_valuta_feil"));
          return;
        }
        oppf.valuta = v;
      }
      if (k === "belop_maks") {
        const b = belop.value.trim();
        if (!/^\d+(\.\d{1,2})?$/.test(b)) {
          settFeil(belop, t("ui.editor.overstyring_belop_feil"));
          return;
        }
        oppf.belop_maks = b;
      }
      const m = policy.menneskelig_overstyring
        = (policy.menneskelig_overstyring
           && typeof policy.menneskelig_overstyring === "object")
          ? policy.menneskelig_overstyring
          : { godkjennbare: [], krever_rolle: roller[0] };
      m.godkjennbare = Array.isArray(m.godkjennbare) ? m.godkjennbare : [];
      if (m.godkjennbare.some((e2) => e2 && e2.grunnkode === gkValg.value
          && e2.handling === hValg.value)) return;   // duplikatpar (schema)
      m.godkjennbare.push(oppf);
      tegnPaaNytt();
    });
    deler.push(el("div", { class: "overstyring-legg-til" },
      el("label", { for: "mo-grunnkode",
        text: t("ui.editor.overstyring_grunnkode") }), gkValg,
      el("label", { for: "mo-handling",
        text: t("ui.editor.overstyring_handling") }), hValg,
      belopRad, valutaRad, feilUt,
      leggTil));
  } else if (!grunnkoder.length) {
    deler.push(el("p", { class: "muted",
      text: t("ui.editor.vilkaar_grunnlag_mangler") }));
  }
  return el("section", { class: "editor-seksjon",
    "aria-label": t("ui.editor.overstyring") }, ...deler);
}

function handlingerSeksjon(policy, tegnPaaNytt, st) {
  policy.handlinger = Array.isArray(policy.handlinger) ? policy.handlinger : [];
  const handlinger = policy.handlinger;
  if (!handlinger.length) {
    return el("section", { class: "editor-seksjon",
      "aria-label": t("ui.editor.handlinger") },
      el("h3", { text: t("ui.editor.handlinger") }),
      el("p", { class: "muted", text: t("ui.editor.handlinger_hjelp") }));
  }

  // ÉN handling om gangen, ikke alle stablet. En bransjemal har gjerne sju
  // handlinger med beløpsgrense, valutaliste og tidsvindu hver — som én
  // rulle var fanen «veldig lang» (eiers ord), og feltene til handling fire
  // hadde ingen synlig tilhørighet når overskriften dens var rullet ut av
  // skjermen. Velgeren viser id + modus per handling (modus er det
  // raskeste svaret på «hva gjør denne»), kortet under viser den valgte,
  // og forrige/neste går sekvensielt — samme navigasjonsform som `Faner`.
  //
  // Valget huskes i editorens `st` og overlever re-rendringene feltene
  // utløser (valuta/tidsvindu tegner på nytt) — uten det hoppet fanen
  // tilbake til første handling hver gang man la til en valuta på den femte.
  // INDEKS, ikke id (Codex P2): et utkast kan bære duplikate handlings-id-er
  // helt til valideringen avviser det (`rediger_utkast` lagrer uten
  // unikhetskrav), og et id-oppslag gjorde duplikat nr. 2 unåelig — begge
  // velgerknappene sto som valgt, og «Neste» hoppet tilbake til den første.
  if (!Number.isInteger(st.handlingValgt)
      || st.handlingValgt < 0 || st.handlingValgt >= handlinger.length) {
    st.handlingValgt = 0;
  }
  const i = st.handlingValgt;
  const valgt = handlinger[i];

  const velgerknapper = handlinger.map((h, j) => {
    const b = el("button", { class: "knapp liten handling-velger-knapp",
      type: "button", "aria-pressed": String(j === i) },
      el("code", { text: h.id || "?" }),
      el("span", { class: "sub", text: ` ${t(`modus.${h.modus}`, h.modus)}` }));
    b.addEventListener("click", () => {
      if (j === i) return;
      st.handlingValgt = j;
      tegnPaaNytt();
    });
    return b;
  });

  const forrige = el("button", { class: "knapp liten", type: "button",
    text: t("ui.editor.handling_forrige") });
  forrige.disabled = i === 0;
  forrige.addEventListener("click", () => {
    st.handlingValgt = i - 1; tegnPaaNytt();
  });
  const neste = el("button", { class: "knapp liten", type: "button",
    text: t("ui.editor.handling_neste") });
  neste.disabled = i === handlinger.length - 1;
  neste.addEventListener("click", () => {
    st.handlingValgt = i + 1; tegnPaaNytt();
  });

  return el("section", { class: "editor-seksjon",
    "aria-label": t("ui.editor.handlinger") },
    el("h3", { text: t("ui.editor.handlinger") }),
    el("p", { class: "muted", text: t("ui.editor.handlinger_hjelp") }),
    el("div", { class: "handling-velger", role: "group",
      "aria-label": t("ui.editor.handling_velg") }, ...velgerknapper),
    el("p", { class: "sub", text: t("ui.editor.handling_posisjon")
      .replace("{n}", String(i + 1))
      .replace("{av}", String(handlinger.length)) }),
    handlingKort(valgt, tegnPaaNytt, policy, st.grunnlag),
    el("div", { class: "editor-knapper" }, forrige, neste));
}

// Hva kan vi SI om policy-id-en? Teksten lovet universelt at man «beholder
// malens id for å avløse policyen som gjelder i dag» (Codex P1). Det er ikke
// noe klienten kunne vite: aktivering er per `policy_id`, katalogen har flere
// maler (`tjenestebedrift-no`, `netthandel-no`, `handverk-bygg-no`), og en
// kunde som opprettet policyen med sin EGEN id har ikke malens id i det hele
// tatt. Følger man rådet der, får man en ny policyserie ved siden av den som
// gjelder — mens den gamle fortsatt er i kraft.
//
// Derfor sier hjelpeteksten bare det oppslaget faktisk dekker:
//   kjent + id  → «<id> gjelder i dag; behold den for å avløse den»
//   kjent, ingen aktiv → «det finnes ingen aktiv policy; dette blir den første»
//   ukjent (403/500) → den kontraktsriktige regelen, uten å påstå hva som
//                      gjelder: samme id viderefører serien, en annen lager ny.
function policyIdHint(aktiv) {
  if (!aktiv || !aktiv.kjent) return t("ui.editor.policy_id_hint");
  if (!aktiv.id) return t("ui.editor.policy_id_hint_ingen_aktiv");
  return t("ui.editor.policy_id_hint_avloser").replace("{id}", aktiv.id);
}

// Hjelpeteksten under det LÅSTE feltet. Vanligvis er det bare regelen —
// identiteten velges ved opprettelse. Men er dokumentets id rettet ved
// innlasting (`avstemIdentitet`), er det en endring eier ikke gjorde selv, og
// da skal hun se BÅDE at den skjedde og hva som sto der: et låst felt som
// stille bytter verdi er ikke en reparasjon eier kan gå god for.
function policyIdLaastHint(rettetFra) {
  if (!rettetFra) return t("ui.editor.policy_id_laast");
  // Teksten nevner den gamle id-en mer enn én gang (hva som sto der, og hva
  // eier gjør om det var den hun ville ha) — `split/join` bytter ALLE, der
  // `replace` med en streng bare hadde tatt den første.
  return t("ui.editor.policy_id_rettet").split("{id}").join(rettetFra);
}

function metaSeksjon(policy, erNy, aktiv, rettetFra) {
  policy.meta = (policy.meta && typeof policy.meta === "object") ? policy.meta : {};
  const m = policy.meta;
  const felt = [
    // policy_id er identiteten; kan settes ved NY, låst ved redigering.
    tekstfelt(t("ui.editor.policy_id"), m.policy_id || "",
      (v) => { m.policy_id = v; }, erNy ? {} : { disabled: "" },
      erNy ? policyIdHint(aktiv) : policyIdLaastHint(rettetFra)),
    tekstfelt(t("ui.editor.bedrift"), m.bedrift || "",
      (v) => { m.bedrift = v || undefined; }),
    tekstfelt(t("ui.editor.versjon"), m.versjon || "",
      (v) => { m.versjon = v; }),
  ];
  // Statusen er ikke et felt eier fyller ut — den er en opplysning om hva
  // utkastet blir. Å skrive den inn i dokumentet uten å si det ville vært en
  // stille endring av noe eier tror hun eier; å tilby den som et valg ville
  // vært en løgn, for de andre verdiene kan ikke aktiveres i det hele tatt.
  felt.push(el("p", { class: "felt-hint", text: t("ui.editor.status_laast") }));
  return el("section", { class: "editor-seksjon",
    "aria-label": t("ui.editor.meta") },
    el("h3", { text: t("ui.editor.meta") }), ...felt);
}

// Statusen den STYRTE aktiveringen skriver i registeret. Editoren har ingen
// statuskontroll, og skal ikke ha en: det finnes bare ett utfall for et utkast
// herfra — fire-øyne-runden, som aktiverer det som produksjonspolicy.
const AKTIVERINGSSTATUS = "produksjon";

// Malene bærer `meta.status: utkast` (alle tre i `policies/`), og det er riktig
// FOR EN MAL — den er ikke en policy i kraft. Men et utkast bygget på den er på
// vei ett sted: gjennom fire-øyne-runden, som skriver `produksjon` i registeret.
// `policyregister.hent_aktiv` krever at dokumentet sier det samme, så et utkast
// som beholder malens status kan ikke aktiveres (Codex P1).
//
// Uten dette var utkastet INNELÅST, akkurat som med en fremmed id: valideringen
// frøs dokumentet, rundeåpningen avviste det, og statusfeltet finnes ikke i
// editoren — så den eneste feilen kunne ikke rettes noe sted.
//
// Statusen settes derfor der utkastet BYGGES, ikke etterpå: det er ikke et valg
// eier tar bort fra henne, det er den eneste verdien et utkast herfra kan ha.
function settAktiveringsstatus(policy) {
  policy.meta = (policy.meta && typeof policy.meta === "object")
    ? policy.meta : {};
  policy.meta.status = AKTIVERINGSSTATUS;
  return policy;
}

// Bygg det som skal lagres: hele malen/utkastet med redigerte felt oppå.
function byggInnhold(policy) {
  // `policy` muteres in-place av feltene; statusen er det ene feltet ingen av
  // dem skriver, og den må stå i det som lagres.
  return settAktiveringsstatus(policy);
}

// Radens `policy_id` og dokumentets `meta.policy_id` er ÉN identitet skrevet to
// steder, og spriker de, er det RADEN som gjelder: den er det utkastet er
// registrert under, den kan ikke endres etterpå, og det er den aktiveringen
// lagrer under. Bare dokumentet kan rettes.
//
// Utkastet fødes med malens id (opprettelsen tar id-en fra forespørselen og
// innholdet fra malen), så et utkast opprettet før serveren begynte å kreve
// samsvar kan godt bære en fremmed id. Da nekter valideringen å fryse det og
// viser eier tilbake hit — der feltet er låst, fordi identiteten velges ved
// opprettelse. Uten denne avstemmingen er utkastet dermed innelåst: den ENESTE
// feilen kan ikke rettes noe sted, og et ellers redigerbart utkast må forlates.
//
// Derfor fylles feltet fra raden ved innlasting. Skjermen viser da identiteten
// utkastet FAKTISK har, og første lagring skriver den inn i dokumentet —
// reparasjonen er selve redigeringen, ikke et eget verktøy. Feltet forblir
// låst: eier får ikke velge en ANNEN id, for det ville bare gjenskape avviket
// (`rediger_utkast` rører aldri radens id). En annen identitet krever et nytt
// utkast, som hjelpeteksten alltid har sagt.
// -> id-en dokumentet oppga, når den ble rettet bort; ellers null.
function avstemIdentitet(policy, radId) {
  if (!radId) return null;             // ukjent rad-id: ingenting å avstemme mot
  policy.meta = (policy.meta && typeof policy.meta === "object")
    ? policy.meta : {};
  const iDokumentet = policy.meta.policy_id;
  if (iDokumentet === radId) return null;
  policy.meta.policy_id = radId;
  // Et TOMT felt er ingen påstand om en annen policy — det er bare ufylt, og
  // da er utfyllingen ikke verdt en advarsel. Vi melder fra om det som
  // faktisk er en rettelse: en id dokumentet oppga, og som nå er borte.
  return (typeof iDokumentet === "string" && iDokumentet.trim())
    ? iDokumentet : null;
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
  // opts: { utkast_id?, aapneUtkast: fn(uid), tilbake: fn(), eierSkjermen? }
  //
  // Editoren deler `hoved` med flaten som åpnet den, og den tegner ingenting
  // før utkastet (eller malkatalogen) er hentet — så den forrige siden, med sin
  // tilbakeknapp, blir stående så lenge kallet er ute. Trykker eier «Tilbake»
  // der, eier editoren ikke lenger skjermen, og svaret som kommer etterpå skal
  // ikke tegne seg over det hun gikk til (Codex P2). Kalleren vet når eierskapet
  // skifter; editoren spør bare. Uten `eierSkjermen` (frittstående bruk, test)
  // er svaret alltid ja.
  const eier = opts.eierSkjermen || (() => true);
  const st = { policy: null, utkast_id: opts.utkast_id || null,
               utkastversjon: null, feil: [], laster: true,
               nokkel: null, signatur: null,
               // Hva GJELDER i dag? `kjent: false` er utgangspunktet, ikke en
               // feiltilstand: før oppslaget har svart vet flaten ingenting,
               // og skal derfor heller ikke påstå noe om avløsning.
               aktiv: { kjent: false, id: null },
               // Ble dokumentets id rettet mot radens ved innlasting? Da bærer
               // hjelpeteksten den gamle verdien (`policyIdLaastHint`).
               rettetFra: null,
               // Editorgrunnlaget (047): plattformvilkår + godkjennbare
               // grunnkoder, fra serveren. null = utilgjengelig, og da er
               // vilkårsredigeringen LÅST og overstyringspar kan ikke
               // legges til (fail-closed, port 30/31).
               grunnlag: null };

  hentJson("/v1/policyadmin/editorgrunnlag").then((d) => {
    st.grunnlag = d;
    if (!st.laster && st.policy && eier()) tegn();
  }).catch(() => { /* fail-closed: grunnlag forblir null */ });

  function lagre() {
    const pid = (st.policy.meta && st.policy.meta.policy_id || "").trim();
    if (!st.utkast_id && !pid) {
      st.feil = [t("ui.editor.policy_id_pakrevd")]; tegn(); return;
    }
    // Formen, ikke bare tilstedeværelsen (Codex P2). Serveren avviser nå en
    // rad-id som bryter skjemaets `meta.policy_id`, og det er riktig: en slik
    // id kan aldri skrives inn i dokumentet, og raden kan ikke endres etterpå
    // — utkastet ville vært dødfødt. Men eier skal møte kravet HER, ved feltet
    // hjelpeteksten allerede beskriver det under, ikke som et avslag på en
    // lagring hun trodde gikk gjennom.
    if (!st.utkast_id && !POLICY_ID_FORM.test(pid)) {
      st.feil = [t("ui.editor.policy_id_ugyldig")]; tegn(); return;
    }
    // Formen er i orden, men id-en kan fortsatt være for stor for
    // registernøkkelen. Egen melding: «for lang» er ikke «feil tegn», og en id
    // som er feil på begge måter skal høre om formen først — den er det eier
    // faktisk må velge om igjen.
    if (!st.utkast_id && byteLengde(pid) > POLICY_ID_MAKS_BYTE) {
      st.feil = [t("ui.editor.policy_id_for_stor")]; tegn(); return;
    }
    // Raden opprettes fra den TRIMMEDE id-en, så dokumentet må bære nøyaktig
    // den samme — ikke feltets råtekst. Et utkast født med « acme» i
    // dokumentet og `acme` i raden er allerede i avvik i det det lagres, og
    // ville vært innelåst fra første stund (feltet låses etter opprettelse).
    if (!st.utkast_id) st.policy.meta.policy_id = pid;
    const innhold = byggInnhold(st.policy);
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
      // Utfallet annonseres uansett: lagringen SKJEDDE, og det skal eier få vite
      // selv om hun har gått videre. Det som er betinget, er å ta skjermen.
      meldLive(t("ui.editor.lagret"));
      if (!eier()) return;
      const uid = res.utkast_id || st.utkast_id;
      if (opts.aapneUtkast) opts.aapneUtkast(uid);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      const melding = t(`ui.editor.feil.${e.kode}`, t("ui.editor.lagring_feilet"));
      // Har eier forlatt editoren, er feilboksen ingen kanal — men en lagring
      // som ikke gikk gjennom er hun nødt til å få vite om. Da sier vi det der
      // det fortsatt høres.
      if (!eier()) { meldLive(melding); return; }
      st.feil = [melding];
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

    // Tre trinn i stedet for én rulle (§2.1): grunnopplysninger → roller →
    // handlinger. Fanen som var åpen huskes over en re-rendring — uten det
    // ville hvert tastetrykk som utløser `tegn()` kastet eier tilbake til
    // trinn 1, midt i utfyllingen.
    const faner = Faner({
      start: st.fane,
      paaBytte: (n) => { st.fane = n; },
      trinn: [
        { nokkel: "grunn", tittel: t("ui.editor.fane.grunn"),
          bygg: () => metaSeksjon(st.policy, !st.utkast_id, st.aktiv,
                                  st.rettetFra) },
        { nokkel: "roller", tittel: t("ui.editor.fane.roller"),
          bygg: () => rollerSeksjon(st.policy, tegn) },
        { nokkel: "handlinger", tittel: t("ui.editor.fane.handlinger"),
          bygg: () => handlingerSeksjon(st.policy, tegn, st) },
        { nokkel: "overstyring", tittel: t("ui.editor.fane.overstyring"),
          bygg: () => overstyringSeksjon(st.policy, tegn, st.grunnlag) },
      ],
    });
    const barn = [
      ...flateHode(t("ui.editor.tittel"), t("ui.editor.undertittel")),
      faner.rot,
    ];
    if (feilboks) barn.push(feilboks);
    barn.push(knapper);
    sett(hoved, ...barn);
  }

  // --- Last inn utgangspunkt: enten et eksisterende utkast, eller malvalg ---
  if (st.utkast_id) {
    hentJson(`/v1/policyutkast/${st.utkast_id}`).then((detalj) => {
      if (!eier()) return;
      st.laster = false;
      st.utkastversjon = detalj.utkastversjon;
      st.policy = detalj.innhold || {};
      // Identiteten avstemmes FØR første tegning: feltet er låst, så det eier
      // ser her er det eneste hun kan forholde seg til.
      st.rettetFra = avstemIdentitet(st.policy, detalj.policy_id);
      tegn();
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (!eier()) return;
      st.laster = false; tegn();
    });
  } else if (opts.startPolicy) {
    st.laster = false;
    st.policy = JSON.parse(JSON.stringify(opts.startPolicy));
    tegn();
  } else {
    // Malvelger. Hvilken policy som GJELDER hentes samtidig: uten den kan
    // flaten verken foreslå riktig id eller si sant om hva id-en gjør. Den er
    // hjelpedata, så et avslag (403) eller et tvetydig register (500) stopper
    // ingenting — da blir bare `kjent: false`, og teksten lover mindre.
    Promise.all([hentMaler(), hentAktivPolicyId()]).then(([d, aktiv]) => {
      if (!eier()) return;
      st.laster = false;
      st.aktiv = aktiv;
      visMalvelger(hoved, ctx, (d && d.maler) || [], (mal) => {
        st.policy = JSON.parse(JSON.stringify(mal.innhold));
        st.policy.meta = (st.policy.meta && typeof st.policy.meta === "object")
          ? st.policy.meta : {};
        // Feltet fylles med den AKTIVE policyens id når vi kjenner den — det
        // er den, ikke malens id, som avgjør om utkastet avløser dagens policy
        // eller starter en ny serie ved siden av (Codex P1). Kjenner vi den
        // ikke, står malens egen id igjen som forslag: et tomt felt ba eier
        // finne på en identitet uten å si hva den brukes til eller hvilket
        // format den krever. Begge deler kan overskrives.
        if (st.aktiv.kjent && st.aktiv.id) st.policy.meta.policy_id = st.aktiv.id;
        tegn();
      }, opts.tilbake);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (!eier()) return;
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
