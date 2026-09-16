// REGISTRER BEDRIFTEN — den ENESTE flaten en registrant kan nå.
//
// EIERS MÅL: «man kan lett registrere en bedrift og sette policy og resten
// skal skje automatisk.» Før denne flaten kostet det fem steg, tre av dem
// som root på verten. Her er det tre felter og én knapp.
//
// HVORFOR HUN STÅR HER I DET HELE TATT: en helt ny bruker har ingen
// medlemskap, og innloggingen avviste henne før hun rakk å registrere noe.
// Hun får nå et medlemskap på den reserverte tenanten `_registrering` med
// ett scope — `firma:opprett` — og dette er det eneste det scopet åpner.
// Skallet viser henne derfor ingen andre flater, og det er riktig: hun har
// ingen data å se ennå.
//
// ETTER REGISTRERING MÅ HUN LOGGE INN PÅ NYTT, og flaten sier det HØYT.
// Registrantraden slettes i samme transaksjon som firmaet opprettes (det er
// det som hindrer at hun får to medlemskap og blir bedt om å «velge firma»
// ved neste pålogging). Men sesjonen hennes peker fortsatt på
// `_registrering`, og neste forespørsel finner ingen roller. Uten denne
// beskjeden ville hun møtt en 401 hun ikke forstår, rett etter å ha gjort
// alt riktig.
//
// TRE BRANSJER, IKKE ET POLICYFELT. Kunden leverer aldri policy selv — hun
// velger én av tre maler som følger med appen. En flate som tok imot policy
// ville vært en vei til å skrive sine egne fullmakter.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { registrerFirma, ApiFeil, UautorisertFeil } from "../api.js";
import { meldLive } from "../komponenter.js";
import { flateHode } from "./felles.js";

export const BRANSJER = ["tjenestebedrift", "handverk-bygg", "netthandel"];
//: Det lukkede settet av fullmakter (207) — samme navn som serveren kjenner.
export const FULLMAKTER = ["kundeservice-svar", "kampanje-send",
                           "purring-inkassovarsel", "tilbud-generer"];
//: HVILKE FULLMAKTER HVER BRANSJE KAN BÆRE — serverens kart
//: (`api.firmaregistrering.FULLMAKTER_FOR_BRANSJE`, regnet av malene), pinnet
//: her av `test_selvregistrering_port`. Håndverk og netthandel mangler
//: DLP- og fordringsvitnet i malen, så kundesvar og inkassovarsel kan ikke
//: velges der ennå.
export const FULLMAKTER_FOR_BRANSJE = {
  "tjenestebedrift": ["kampanje-send", "kundeservice-svar",
                      "purring-inkassovarsel", "tilbud-generer"],
  "handverk-bygg": ["kampanje-send", "tilbud-generer"],
  "netthandel": ["kampanje-send", "tilbud-generer"],
};

const SIFRE = /^[0-9]{9}$/;
const MELLOMROM = / /g;

// Nøkkelen er STABIL så lenge innholdet er uendret (PR-014 R1). Et tapt
// svar + nytt klikk skal gi replay, ikke firma nummer to.
//
// SERIALISERINGEN MÅ VÆRE ENTYDIG (CodeRabbit). Første utgave limte feltene
// sammen rått, så («AB», «C», x) og («A», «BC», x) ga SAMME nøkkel — to
// ulike registreringer ville delt idempotensnøkkel, og den andre hadde fått
// replay av den første: hun får «ferdig», og firmaet finnes ikke.
// `JSON.stringify` av et array gir skilletegn som ikke kan forveksles med
// innhold.
//
// TO UAVHENGIGE HASHER, ikke én 32-bits. Én enkelt 32-bits verdi kolliderer
// merkbart lenge før man tror; to med ulike konstanter gir 64 bits, og det
// er rikelig for et skjema én person fyller ut.
function _hash(tekst, frø, faktor) {
  let h = frø >>> 0;
  for (let i = 0; i < tekst.length; i += 1) {
    h = Math.imul(h ^ tekst.charCodeAt(i), faktor) >>> 0;
  }
  return (h ^ (h >>> 15)) >>> 0;
}

// SLUGEN SPEILER SERVERENS `slug_av` (firmaregistrering.py). Forslaget må
// være nøyaktig det serveren VILLE valgt — ellers viser feltet ett kortnavn
// og firmaet får et annet, og da er forslaget en løgn.
//
// Æ/Ø/Å oversettes FØR normaliseringen, av samme grunn som på serveren: NFD
// splitter ikke Ø i O + ring, så «Øre AS» ville blitt «re-as».
export function slugAv(navn) {
  let s = String(navn || "").trim().toLowerCase();
  for (const [fra, til] of [["æ", "ae"], ["ø", "oe"], ["å", "aa"],
                            ["ä", "ae"], ["ö", "oe"], ["ü", "ue"]]) {
    s = s.split(fra).join(til);
  }
  s = s.normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
  s = s.replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  return s.slice(0, 63).replace(/-+$/, "");
}

export function idempotensnokkelFor(navn, orgnummer, bransje, kortnavn,
                                    fullmakter = []) {
  // KORTNAVNET INN I NØKKELEN. Retter hun et opptatt kortnavn og prøver på
  // nytt, er det en NY handling — med den gamle nøkkelen ville hun fått det
  // første forsøkets svar tilbake, altså «opptatt», uansett hva hun skrev.
  // FULLMAKTENE INN I NØKKELEN (207): to like kropper med ulikt valg av
  // fullmakter er to ulike registreringer, ikke en replay.
  const raa = JSON.stringify([navn, orgnummer ?? null, bransje,
                              kortnavn ?? null, [...fullmakter].sort()]);
  const a = _hash(raa, 0x811c9dc5, 0x01000193);
  const b = _hash(raa, 0xdeadbeef, 0x85ebca6b);
  return `firmareg-${a.toString(16).padStart(8, "0")}`
       + `${b.toString(16).padStart(8, "0")}`;
}

// Ni sifre måles HER også, ikke bare av serveren: feltet skal si fra mens
// hun står i det, ikke etter at skjemaet er sendt. MOD-11 er fortsatt
// serverens jobb — en klientsjekk av kontrollsifferet ville bare vært en
// kopi som kan drive fra originalen.
export function orgnummerFeil(verdi) {
  const v = (verdi || "").replace(MELLOMROM, "");
  if (!v) return null;                       // valgfritt
  return SIFRE.test(v) ? null : "ui.firmareg.feil.orgnummer";
}

function felt(id, nokkel, extra) {
  const inp = el("input", Object.assign(
    { id, class: "felt-inp", type: "text" }, extra || {}));
  const rad = el("div", { class: "felt" },
    el("label", { for: id, text: t(nokkel) }), inp);
  return { rad, inp };
}

export function visFirmaregistrering(hoved, ctx) {
  const deler = [...flateHode(t("ui.firmareg.tittel"),
                              t("ui.firmareg.undertittel"))];

  const navn = felt("firmareg-navn", "ui.firmareg.navn",
                    { required: true, maxLength: 200 });
  const org = felt("firmareg-orgnr", "ui.firmareg.orgnummer",
                   { inputMode: "numeric", maxLength: 11 });
  // FEILELEMENTET STÅR ALLTID, med `role="alert"` og tom tekst — husets
  // form (`parter.js`). Første utgave skjulte det med `hidden` og slo det
  // på ved feil; axe felte det på `aria-valid-attr-value`, fordi
  // `aria-errormessage` da peker på noe som ikke er eksponert.
  const orgFeil = el("p", { id: "firmareg-orgnr-feil", class: "feltfeil",
                            role: "alert" });
  org.rad.append(orgFeil);

  // KORTNAVNET. Fylles ut fra firmanavnet mens hun skriver — og slutter å
  // gjøre det i det hun rører feltet selv. Da er det hennes, og sendes MED:
  // serveren skiller et VALGT kortnavn fra et utledet, fordi et valgt aldri
  // skal bli stille om til noe annet.
  const kort = felt("firmareg-kortnavn", "ui.firmareg.kortnavn");
  let kortRortAvHenne = false;
  kort.inp.addEventListener("input", () => { kortRortAvHenne = true; });
  navn.inp.addEventListener("input", () => {
    if (!kortRortAvHenne) kort.inp.value = slugAv(navn.inp.value);
  });
  kort.rad.append(el("p", { class: "hjelpetekst",
    text: t("ui.firmareg.kortnavn_hjelp") }));

  const valg = el("select", { id: "firmareg-bransje", class: "felt-inp" });
  for (const b of BRANSJER) {
    valg.append(el("option", { value: b, text: t(`ui.firmareg.bransje.${b}`) }));
  }
  const bransjerad = el("div", { class: "felt" },
    el("label", { for: "firmareg-bransje", text: t("ui.firmareg.bransje") }),
    valg,
    el("p", { class: "hjelpetekst", text: t("ui.firmareg.bransje_hjelp") }));

  // FULLMAKTENE (207): hva plattformen får utføre innenfor policyen.
  // Velges HER, av den som registrerer, som del av startpolicyen — etterpå
  // krever en utvidelse fire øyne (V6), og et enkeltpersonfirma har ikke
  // det. Kundesvaret er forhåndsvalgt: det er reisen som finnes i dag, og
  // hvert svar krever fortsatt et menneskes godkjenning.
  const fullmaktbokser = FULLMAKTER.map((navn) => {
    const boks = el("input", { type: "checkbox", id: `firmareg-fm-${navn}`,
                               name: "fullmakter", value: navn });
    if (navn === "kundeservice-svar") boks.checked = true;
    return el("label", { class: "avkryssing", for: `firmareg-fm-${navn}` },
      boks, el("span", { text: t(`ui.firmareg.fullmakt.${navn}`) }),
      el("span", { class: "muted fullmakt-merknad", hidden: true,
                   text: t("ui.firmareg.fullmakt_ikke_i_bransjen") }));
  });
  // BRANSJEN STYRER HVA SOM KAN VELGES: en boks malen ikke bærer slås av og
  // tømmes, med grunnen som ord ved siden av — aldri en stille 400.
  const tilpassBransje = () => {
    const lov = FULLMAKTER_FOR_BRANSJE[valg.value] || [];
    for (const l of fullmaktbokser) {
      const b = l.querySelector("input");
      const ok = lov.includes(b.value);
      b.disabled = !ok;
      if (!ok) b.checked = false;
      l.querySelector(".fullmakt-merknad").hidden = ok;
    }
  };
  valg.addEventListener("change", tilpassBransje);
  tilpassBransje();
  const fullmaktrad = el("fieldset", { class: "felt fullmakter" },
    el("legend", { text: t("ui.firmareg.fullmakter") }),
    ...fullmaktbokser,
    el("p", { class: "hjelpetekst", text: t("ui.firmareg.fullmakter_hjelp") }));
  const valgteFullmakter = () => fullmaktbokser
    .map((l) => l.querySelector("input")).filter((b) => b.checked)
    .map((b) => b.value);

  const knapp = el("button", { type: "submit", class: "knapp primar",
                               text: t("ui.firmareg.send") });
  const melding = el("p", { class: "melding", hidden: true, role: "status" });

  // `novalidate`: nettleserens egen boble er ikke oversatt og kan ikke
  // knyttes til feltet med aria-errormessage.
  const skjema = el("form", { class: "kv-skjema", novalidate: "" },
    navn.rad, kort.rad, org.rad, bransjerad, fullmaktrad,
    el("div", { class: "knapperad" }, knapp), melding);

  const visFeil = (nokkel) => {
    melding.hidden = false;
    melding.classList.add("feil");
    sett(melding, t(nokkel));
    meldLive(t(nokkel));
  };

  skjema.addEventListener("submit", async (e) => {
    e.preventDefault();
    melding.hidden = true;
    melding.classList.remove("feil");

    const n = navn.inp.value.trim();
    if (!n) {
      navn.inp.setAttribute("aria-invalid", "true");
      navn.inp.focus();
      visFeil("ui.firmareg.feil.navn");
      return;
    }
    navn.inp.removeAttribute("aria-invalid");

    const of = orgnummerFeil(org.inp.value);
    if (of) {
      org.inp.setAttribute("aria-invalid", "true");
      org.inp.setAttribute("aria-errormessage", "firmareg-orgnr-feil");
      orgFeil.textContent = t(of);
      org.inp.focus();
      visFeil(of);
      return;
    }
    org.inp.removeAttribute("aria-invalid");
    org.inp.removeAttribute("aria-errormessage");
    orgFeil.textContent = "";

    const orgnummer = org.inp.value.replace(MELLOMROM, "") || null;
    const bransje = valg.value;
    knapp.disabled = true;
    try {
      // BARE NÅR HUN HAR RØRT DET. Sendes feltet alltid, kan serveren ikke
      // skille et valg fra et forslag — og da ville en kollisjon på et
      // kortnavn hun var likegyldig til stoppet registreringen i stedet for
      // å ta neste ledige.
      // NORMALISERT ÉN GANG, brukt begge steder (CodeRabbit). Rører hun
      // feltet og TØMMER det, er verdien `""` — falsy, så kroppen utelot
      // `kortnavn`, mens nøkkelen fikk `""`. To identiske kropper ville da
      // hatt ulik idempotensnøkkel, og et gjentatt klikk blitt firma nummer
      // to i stedet for en replay.
      const kortnavn =
        (kortRortAvHenne ? kort.inp.value.trim() : "") || null;
      const fullmakter = valgteFullmakter();
      const svar = await registrerFirma(
        kortnavn ? { navn: n, kortnavn, orgnummer, bransje, fullmakter }
          : { navn: n, orgnummer, bransje, fullmakter },
        idempotensnokkelFor(n, orgnummer, bransje, kortnavn, fullmakter));
      // FERDIG — OG HUN MÅ LOGGE INN PÅ NYTT. Sesjonen hennes peker på
      // registreringskonteksten, som ikke lenger har et medlemskap.
      sett(hoved, ...flateHode(t("ui.firmareg.ferdig_tittel"),
                               t("ui.firmareg.ferdig_undertittel")),
           el("p", { text: t("ui.firmareg.ferdig_tekst")
             .replace("{navn}", svar.navn)
             .replace("{frist}", svar.prove_utloper) }),
           el("p", { class: "knapperad" },
             el("a", { class: "knapp primar", href: "/",
                       text: t("ui.firmareg.logg_inn_paa_nytt") })));
      meldLive(t("ui.firmareg.ferdig_tittel"));
      return;
    } catch (f) {
      knapp.disabled = false;
      if (f instanceof UautorisertFeil) {
        visFeil("ui.firmareg.feil.okt");
      } else if (f instanceof ApiFeil && f.kode === "kortnavn_opptatt") {
        kort.inp.setAttribute("aria-invalid", "true");
        kort.inp.focus();
        visFeil("ui.firmareg.feil.kortnavn_opptatt");
      } else if (f instanceof ApiFeil && f.kode === "firma_tak_naadd") {
        visFeil("ui.firmareg.feil.tak");
      } else if (f instanceof ApiFeil && f.kode === "firma_navn_opptatt") {
        visFeil("ui.firmareg.feil.navn_opptatt");
      } else {
        visFeil("ui.firmareg.feil.ukjent");
      }
    }
  });

  deler.push(skjema);
  sett(hoved, ...deler);
}
