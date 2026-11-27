import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { ApiFeil, UautorisertFeil, erPlattformeier, hentPlattformFirmaer,
         oppdaterPlattformFirma, opprettPlattformFirma,
         settPlattformFirmastatus, hentLeverandor, settLeverandor,
         hentJuridisk, lagreJuridiskUtkast, publiserJuridisk,
         hentInnstillinger, settInnstillinger, hentPliktkatalog,
         settPliktmal, settPliktfrist, slettPliktfrist }
  from "../api.js";
import { tekstTilNoder } from "./juridisk.js";
import { meldLive } from "../komponenter.js";
import { flateHode } from "./felles.js";
import { FASEOVERSIKT, MODULOVERSIKT, modulmerke, planEtikett, plattformTelling }
  from "../plattformdata.js";
import { byggRuter, erPlattformdrift, kanForvaltePolicy } from "../sitekart.js";
import { siteFaseMerke, siteModuleKort } from "../sitekomponenter.js";


// ---------------------------------------------------------------------------
// PLATTFORMEIERENS FIRMAADMINISTRASJON (199).
//
// Eier: «Jeg er … eier … og skal også ha mulighet til å opprette nye firmaer,
// redigere og slette.»
//
// FULLMAKTEN ER IKKE ET SCOPE, og derfor kan ikke menyen avgjøre dette.
// Rutene krever `policy:read` bare for å slippe forbi den generelle porten;
// det er raden i `plattformeier` som bestemmer, og den kjenner bare serveren.
// Seksjonen spør derfor `/v1/plattform/meg` FØRST, og tegner ingenting hvis
// svaret er nei — ikke en tom tabell, ikke en «du mangler tilgang», bare
// ingenting. En seksjon som forteller deg at den finnes, er en opplysning om
// systemet den som ikke skal se den ikke trenger.
//
// Den bor på adminflaten fordi det er flaten som allerede heter «Plattform»
// i toppnavigasjonen. Én ekstra henting, og bare når noen åpner den siden.
//
// FANER (eier 27/11: «disse bør organiseres som tabmeny»): listen og
// opprettingen er to seksjoner med hver sin `h2`, så seksjonsfanene gjør dem
// til hver sin fane. Seksjonene returneres derfor som et PAR.
function plattformseksjon(ctx) {
  const boks = el("section", { class: "kort", "aria-labelledby": "pf-liste-t" });
  boks.hidden = true;
  const nyBoks = el("section", { class: "kort", "aria-labelledby": "pf-ny-t" });
  nyBoks.hidden = true;

  const meld = (nokkel, i = boks) => {
    const m = el("p", { class: "melding", role: "status", text: t(nokkel) });
    // Etter overskriften: seksjonsfanene leser fanetittelen fra `h2`-en,
    // og meldingen skal stå under den, ikke over.
    const h = i.querySelector(":scope > h2");
    if (h) h.after(m); else i.prepend(m);
    meldLive(t(nokkel));
  };

  const feilkode = (e) => {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return null; }
    if (e instanceof ApiFeil && e.kode === "firma_finnes") return "ui.plattform.finnes";
    if (e instanceof ApiFeil && e.kode === "overgang_ulovlig") return "ui.plattform.ulovlig";
    return "ui.plattform.feilet";
  };

  // Lovlige overganger speiler 190s ENE tabell. Kopien her er ERGONOMI —
  // døra dømmer uansett, og en knapp som ikke finnes er bedre enn en 409.
  const HANDLINGER = {
    prove: [["aktiv", "ui.plattform.aktiver"], ["stengt", "ui.plattform.steng"]],
    aktiv: [["stengt", "ui.plattform.steng"]],
    utlopt: [["aktiv", "ui.plattform.aktiver"], ["stengt", "ui.plattform.steng"]],
    stengt: [["aktiv", "ui.plattform.gjenapne"]],
  };

  const tegnListe = (firmaer) => {
    if (!firmaer.length) {
      return el("p", { class: "sub", text: t("ui.plattform.tom") });
    }
    const tbody = el("tbody");
    for (const f of firmaer) {
      const handlinger = el("td");
      // HELE RADEN LÅSES, ikke bare knappen som ble trykket (CodeRabbit).
      // Med bare én knapp låst kunne «Aktiver» og «Steng» ligge ute
      // samtidig, og da er det nettverket — ikke brukeren — som avgjør
      // hvilken status firmaet ender på. Samme feilklasse som de to
      // kanalvalgene i varselflaten.
      const laas = (av) => {
        for (const b of handlinger.querySelectorAll("button")) b.disabled = av;
      };
      for (const [status, nokkel] of (HANDLINGER[f.status] || [])) {
        const knapp = el("button", {
          type: "button", text: t(nokkel),
          class: status === "stengt" ? "knapp liten fare" : "knapp liten" });
        knapp.addEventListener("click", () => {
          laas(true);
          settPlattformFirmastatus(f.tenant, status)
            // OPPFRISK FØRST, MELD ETTERPÅ (CodeRabbit). `last()` kaller
            // `sett(boks, …)`, som erstatter alt innholdet — meldte vi først,
            // ble kvitteringen vasket bort i samme åndedrag, og brukeren så
            // aldri at noe var lagret.
            .then(() => last()).then(() => meld("ui.plattform.endret"))
            .catch((e) => {
              const k = feilkode(e);
              if (k) { meld(k); laas(false); }
            });
        });
        handlinger.append(knapp);
      }
      // ENDRE NAVN, tredje verb i eiers krav. Raden bytter til et skjema i
      // stedet for å åpne en dialog: du ser fortsatt de andre firmaene mens
      // du retter ett, og «Avbryt» tar deg tilbake uten å ha rørt noe.
      const navncelle = el("td", { text: f.navn });
      const endre = el("button", { type: "button", class: "knapp liten",
        text: t("ui.plattform.endre") });
      endre.addEventListener("click", () => {
        const inn = el("input", { type: "text", value: f.navn, class: "felt",
          "aria-label": t("ui.plattform.felt.navn") });
        const lagre = el("button", { type: "button", class: "knapp liten primar",
          text: t("ui.plattform.lagre") });
        const avbryt = el("button", { type: "button", class: "knapp liten",
          text: t("ui.plattform.avbryt") });
        lagre.addEventListener("click", () => {
          const nytt = inn.value.trim();
          if (!nytt) { inn.focus(); return; }
          lagre.disabled = true;
          oppdaterPlattformFirma(f.tenant, { navn: nytt,
                                             orgnummer: f.orgnummer || null })
            .then(() => last()).then(() => meld("ui.plattform.endret"))
            .catch((e) => {
              const k = feilkode(e);
              if (k) { meld(k); lagre.disabled = false; }
            });
        });
        avbryt.addEventListener("click", () => {
          sett(navncelle, f.navn);
          endre.disabled = false;
          endre.focus();
        });
        sett(navncelle, inn, lagre, avbryt);
        endre.disabled = true;
        inn.focus();
      });
      handlinger.append(endre);

      tbody.append(el("tr", {},
        navncelle,
        el("td", { text: f.tenant }),
        el("td", { text: t(`ui.plattform.status.${f.status}`, f.status) }),
        // Prøvefristen er BARE relevant i prøveperioden. Å vise en gammel
        // dato på et aktivt firma ville sett ut som en frist som gjelder.
        el("td", { text: f.status === "prove" && f.prove_utloper
          ? f.prove_utloper.slice(0, 10) : "—" }),
        handlinger));
    }
    // `tablewrap` og `sr-only` er HUSETS navn — begge sto allerede i denne
    // fila og i `base.css`. Jeg fant på `tabellramme` og `visuelt-skjult`, og
    // ingen av dem finnes i noen CSS-fil: tabellen mistet sin vannrette
    // rulling på smal skjerm, og «skjult» bildetekst sto synlig og gjentok
    // overskriften rett over. Eier så det på skjermen; jeg hadde ikke sett
    // etter, fordi jsdom ikke laster CSS og porten derfor var blind.
    return el("div", { class: "tablewrap" },
      el("table", { class: "tabell" },
        el("caption", { class: "sr-only",
          text: t("ui.plattform.firmaer") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.plattform.kol.navn") }),
          el("th", { scope: "col", text: t("ui.plattform.kol.tenant") }),
          el("th", { scope: "col", text: t("ui.plattform.kol.status") }),
          el("th", { scope: "col", text: t("ui.plattform.kol.prove") }),
          el("th", { scope: "col", text: t("ui.plattform.kol.handling") }))),
        tbody));
  };

  const nyttFirmaSkjema = (standardDogn) => {
    const felt = (id, nokkel, type = "text", verdi = "") => {
      const inn = el("input", { type, id, value: verdi, class: "felt" });
      return [el("label", { for: id, text: t(nokkel) }), inn, inn];
    };
    const [lNavn, iNavn] = felt("pf-navn", "ui.plattform.felt.navn");
    const [lTenant, iTenant] = felt("pf-tenant", "ui.plattform.felt.tenant");
    const [lOrg, iOrg] = felt("pf-org", "ui.plattform.felt.orgnummer");
    // STANDARDEN ER PLATTFORMENS INNSTILLING (236), ikke et tall i koden.
    const dognTekst = String(standardDogn);
    const [lProve, iProve] = felt("pf-prove", "ui.plattform.felt.prove",
                                  "number", dognTekst);
    iProve.min = "1"; iProve.max = "365";
    const knapp = el("button", { type: "submit", class: "knapp primar",
      text: t("ui.plattform.opprett") });
    const skjema = el("form", { class: "skjema" },
      lNavn, iNavn, lTenant, iTenant, lOrg, iOrg, lProve, iProve,
      el("div", { class: "knapperad" }, knapp));
    skjema.addEventListener("submit", (ev) => {
      ev.preventDefault();
      knapp.disabled = true;
      opprettPlattformFirma({
        navn: iNavn.value.trim(),
        tenant: iTenant.value.trim(),
        orgnummer: iOrg.value.trim() || null,
        prove_dogn: Number(iProve.value) || standardDogn,
      })
        .then(() => {
          skjema.reset(); iProve.value = dognTekst;
          // Kvitteringen står i OPPRETT-fanen, der hun trykket; listen i
          // den andre fanen er oppfrisket når hun går dit.
          return last().then(() => meld("ui.plattform.opprettet", nyBoks));
        })
        .catch((e) => { const k = feilkode(e); if (k) meld(k, nyBoks); })
        .finally(() => { knapp.disabled = false; });
    });
    sett(nyBoks,
      el("h2", { id: "pf-ny-t", text: t("ui.plattform.nytt_firma") }),
      el("p", { text: t("ui.plattform.nytt_firma_tekst") }),
      skjema);
    nyBoks.hidden = false;
  };

  const last = () => hentPlattformFirmaer()
    .then((d) => {
      sett(boks,
        el("h2", { id: "pf-liste-t", text: t("ui.plattform.firmaer") }),
        el("p", { text: t("ui.plattform.firmaer_tekst") }),
        tegnListe(d.firmaer || []));
      boks.hidden = false;
    })
    .catch((e) => { if (feilkode(e)) boks.hidden = true; });

  // FØRST spørsmålet, så innholdet. Er svaret nei, gjøres ingen flere kall —
  // og seksjonene forblir `hidden`.
  erPlattformeier()
    .then((d) => {
      if (!(d && d.eier === true)) return null;
      return Promise.all([
        last(),
        hentInnstillinger()
          .then((x) => nyttFirmaSkjema(x.innstillinger.prove_dogn))
          .catch((e) => { if (feilkode(e)) nyttFirmaSkjema(30); }),
      ]);
    })
    .catch(() => {});

  return [boks, nyBoks];
}

// PLIKTKATALOGEN (237). Frist- og pliktvaktens kilde: hvilke lovpålagte
// plikter som finnes, hvem de gjelder for, hjemmelen — og fristene, AVLEST
// fra myndigheten (helg og helligdag flytter dem). Kundenes «Lovpålagte
// frister» lages herfra. Eier 27/11: «alt må betjenes via dashboard».
//
// Ikke en lang liste: ett kort per plikt, med «hvem gjelder den for» som
// brikker, fristene som brikker med ×, og en ny frist på ÉN linje.
const PLIKTFELT = [["nokkel", "text"], ["tittel", "text"],
  ["hjemmel", "text"], ["kilde_url", "url"]];

function datoNo(iso) {
  return iso ? iso.slice(0, 10).split("-").reverse().join(".") : "";
}

export function pliktkatalogseksjon(ctx) {
  const boks = el("section", { class: "kort", "aria-labelledby": "pk-tittel" });
  boks.hidden = true;
  const melding = el("p", { class: "melding", role: "status" });
  const feil = (e) => {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return; }
    melding.textContent = t(e instanceof ApiFeil && e.status === 400
      ? "ui.pliktkatalog.feil.form" : "ui.plattform.feilet");
    meldLive(melding.textContent);
  };
  let myndigheter = [];

  const malSkjema = (m, ved) => {
    const id = (f) => `pk-${m ? m.nokkel : "ny"}-${f}`;
    const inn = {};
    const felt = PLIKTFELT.map(([f, type]) => {
      inn[f] = el("input", { id: id(f), type, class: "felt-inp",
        value: (m && m[f]) || "", autocomplete: "off" });
      if (f === "nokkel" && m) inn[f].readOnly = true;
      return el("div", { class: "felt" },
        el("label", { for: id(f), text: t(`ui.pliktkatalog.felt.${f}`) }),
        inn[f]);
    });
    const mynd = el("select", { id: id("myndighet"), class: "felt-inp" },
      ...myndigheter.map((x) => el("option", { value: x,
        text: t(`ui.pliktkatalog.myndighet.${x}`, x) })));
    if (m) mynd.value = m.myndighet;
    const former = el("input", { id: id("orgformer"), class: "felt-inp",
      value: m ? m.orgformer.join(", ") : "", autocomplete: "off" });
    const boksFor = (f, nokkel, verdi) => {
      const b = el("input", { type: "checkbox", id: id(f) });
      b.checked = verdi;
      inn[f] = b;
      return el("label", { class: "avkryssing", for: id(f) }, b,
        el("span", { text: t(nokkel) }));
    };
    const lagre = el("button", { type: "submit", class: "knapp primar",
      text: t(m ? "ui.pliktkatalog.lagre" : "ui.pliktkatalog.legg_til_plikt") });
    const avbryt = el("button", { type: "button", class: "knapp",
      text: t("ui.plattform.avbryt") });
    avbryt.addEventListener("click", () => ved(null));
    const skjema = el("form", { class: "kv-skjema lev-skjema", novalidate: "" },
      ...felt,
      el("div", { class: "felt" },
        el("label", { for: id("myndighet"), text: t("ui.pliktkatalog.felt.myndighet") }),
        mynd),
      el("div", { class: "felt" },
        el("label", { for: id("orgformer"), text: t("ui.pliktkatalog.felt.orgformer") }),
        former,
        el("p", { class: "hjelpetekst", text: t("ui.pliktkatalog.hjelp.orgformer") })),
      boksFor("krever_mva", "ui.pliktkatalog.krever_mva", m ? m.krever_mva : false),
      boksFor("krever_ansatte", "ui.pliktkatalog.krever_ansatte",
              m ? m.krever_ansatte : false),
      boksFor("aktiv", "ui.pliktkatalog.aktiv", m ? m.aktiv : true),
      el("div", { class: "knapperad" }, lagre, avbryt));
    skjema.addEventListener("submit", (ev) => {
      ev.preventDefault();
      lagre.disabled = true;
      settPliktmal({
        nokkel: inn.nokkel.value.trim(), tittel: inn.tittel.value.trim(),
        hjemmel: inn.hjemmel.value.trim(), kilde_url: inn.kilde_url.value.trim(),
        myndighet: mynd.value,
        orgformer: former.value.split(/[\s,]+/).map((x) => x.trim().toUpperCase())
          .filter(Boolean),
        krever_mva: inn.krever_mva.checked,
        krever_ansatte: inn.krever_ansatte.checked, aktiv: inn.aktiv.checked,
      })
        .then((d) => ved(d, t("ui.pliktkatalog.lagret")))
        .catch((e) => { lagre.disabled = false; feil(e); });
    });
    return skjema;
  };

  const malKort = (m, tegn) => {
    const gjelder = [
      m.orgformer.length ? m.orgformer.join(", ") : t("ui.pliktkatalog.alle_former"),
      ...(m.krever_mva ? [t("ui.pliktkatalog.krever_mva")] : []),
      ...(m.krever_ansatte ? [t("ui.pliktkatalog.krever_ansatte")] : []),
      t(`ui.pliktkatalog.myndighet.${m.myndighet}`, m.myndighet),
      ...(m.aktiv ? [] : [t("ui.pliktkatalog.inaktiv")]),
    ];
    const frister = el("ul", { class: "brikkerad" },
      ...m.frister.map((f) => {
        const x = el("button", { type: "button", class: "brikke-x",
          "aria-label": t("ui.pliktkatalog.slett_frist")
            .replace("{frist}", `${f.periode} ${datoNo(f.frist)}`),
          text: "×" });
        x.addEventListener("click", () => {
          x.disabled = true;
          slettPliktfrist({ nokkel: m.nokkel, frist: f.frist })
            .then((d) => tegn(d, t("ui.pliktkatalog.slettet")))
            .catch((e) => { x.disabled = false; feil(e); });
        });
        return el("li", { class: "brikke" },
          el("span", { class: "brikke-navn", text: datoNo(f.frist) }),
          el("span", { class: "muted", text: f.periode }), x);
      }));
    const pId = `pk-${m.nokkel}-periode`;
    const dId = `pk-${m.nokkel}-dato`;
    const periode = el("input", { id: pId, class: "felt-inp", autocomplete: "off" });
    const datoInn = el("input", { id: dId, type: "date", class: "felt-inp" });
    const leggTil = el("button", { type: "submit", class: "knapp",
      text: t("ui.pliktkatalog.legg_til_frist") });
    const nyFrist = el("form", { class: "kv-skjema-linje", novalidate: "" },
      el("div", { class: "felt" },
        el("label", { for: dId, text: t("ui.pliktkatalog.felt.frist") }), datoInn),
      el("div", { class: "felt" },
        el("label", { for: pId, text: t("ui.pliktkatalog.felt.periode") }), periode),
      leggTil);
    nyFrist.addEventListener("submit", (ev) => {
      ev.preventDefault();
      if (!datoInn.value) { datoInn.focus(); return; }
      if (!periode.value.trim()) { periode.focus(); return; }
      leggTil.disabled = true;
      settPliktfrist({ nokkel: m.nokkel, periode: periode.value.trim(),
                       frist: datoInn.value })
        .then((d) => tegn(d, t("ui.pliktkatalog.lagt_til")))
        .catch((e) => { leggTil.disabled = false; feil(e); });
    });
    const endre = el("button", { type: "button", class: "knapp liten",
      text: t("ui.pliktkatalog.endre") });
    const kort = el("article", { class: "pk-mal" },
      el("div", { class: "pk-hode" },
        el("h3", { text: m.tittel }), endre),
      el("p", { class: "muted pk-hjemmel" }, `${m.hjemmel} · `,
        el("a", { href: m.kilde_url, target: "_blank", rel: "noopener",
          text: t("ui.lovfrister.kilde") })),
      el("ul", { class: "brikkerad" },
        ...gjelder.map((g) => el("li", { class: "brikke brikke-tekst", text: g }))),
      el("h4", { text: t("ui.pliktkatalog.frister").replace("{n}",
        String(m.frister.length)) }),
      frister, nyFrist);
    endre.addEventListener("click", () => {
      sett(kort, malSkjema(m, (d, k) => (d ? tegn(d, k) : tegn())));
    });
    return kort;
  };

  let siste = null;
  const tegn = (d, kvittering) => {
    if (d) siste = d;
    const ny = el("button", { type: "button", class: "knapp",
      text: t("ui.pliktkatalog.ny_plikt") });
    const nyBoks = el("div");
    ny.addEventListener("click", () => {
      sett(nyBoks, malSkjema(null, (x, k) => (x ? tegn(x, k) : sett(nyBoks))));
    });
    sett(boks,
      el("h2", { id: "pk-tittel", text: t("ui.pliktkatalog.tittel") }),
      el("p", { text: t("ui.pliktkatalog.tekst") }),
      melding,
      el("div", { class: "pk-maler" }, ...siste.maler.map((m) => malKort(m, tegn))),
      el("div", { class: "knapperad" }, ny), nyBoks);
    melding.textContent = kvittering || "";
    if (kvittering) meldLive(kvittering);
    boks.hidden = false;
  };
  erPlattformeier()
    .then((d) => {
      if (!(d && d.eier === true)) return null;
      return hentPliktkatalog().then((x) => {
        myndigheter = x.myndigheter || [];
        tegn(x);
      });
    })
    .catch(feil);
  return boks;
}

// INNSTILLINGER (236). Eier 27/11: «gjøre de siste innstillingene som
// fortsatt ligger i koden mulig å endre i plattformadmin». Prøveperiodens
// lengde og hvilke bransjemaler en ny kunde kan velge sto i
// `firmaregistrering.py`; nå står de her. Malene selv er policyfiler — her
// velges bare hvilke som tilbys.
export function innstillingsseksjon(ctx) {
  const boks = el("section", { class: "kort", "aria-labelledby": "innst-tittel" });
  boks.hidden = true;
  const tegn = (d) => {
    const i = d.innstillinger;
    const melding = el("p", { class: "melding", role: "status" });
    const dogn = el("input", { id: "innst-dogn", type: "number",
      class: "felt-inp", min: "1", max: "365", inputmode: "numeric",
      value: String(i.prove_dogn), required: true });
    const bokser = (d.alle_bransjer || []).map((b) => {
      const boks = el("input", { type: "checkbox", id: `innst-b-${b}`,
        name: "bransjer", value: b });
      boks.checked = i.bransjer.includes(b);
      return el("label", { class: "avkryssing", for: `innst-b-${b}` },
        boks, el("span", { text: t(`ui.firmareg.bransje.${b}`) }));
    });
    const knapp = el("button", { type: "submit", class: "knapp primar",
      text: t("ui.innstillinger.lagre") });
    const skjema = el("form", { class: "kv-skjema lev-skjema", novalidate: "" },
      el("div", { class: "felt" },
        el("label", { for: "innst-dogn", text: t("ui.innstillinger.dogn") }),
        dogn,
        el("p", { class: "hjelpetekst", text: t("ui.innstillinger.dogn_hjelp") })),
      el("fieldset", { class: "felt" },
        el("legend", { text: t("ui.innstillinger.bransjer") }),
        ...bokser,
        el("p", { class: "hjelpetekst",
          text: t("ui.innstillinger.bransjer_hjelp") })),
      el("div", { class: "knapperad" }, knapp), melding);
    skjema.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const n = Number(dogn.value);
      // SAMME REGLER SOM BASEN (236), sagt ved feltet før innsending.
      if (!Number.isInteger(n) || n < 1 || n > 365) {
        melding.textContent = t("ui.innstillinger.feil.dogn");
        dogn.setAttribute("aria-invalid", "true");
        dogn.focus(); return;
      }
      dogn.removeAttribute("aria-invalid");
      const valgte = bokser.map((l) => l.querySelector("input"))
        .filter((b) => b.checked).map((b) => b.value);
      if (!valgte.length) {
        melding.textContent = t("ui.innstillinger.feil.bransjer");
        bokser[0]?.querySelector("input").focus(); return;
      }
      knapp.disabled = true;
      settInnstillinger({ prove_dogn: n, bransjer: valgte })
        .then((x) => {
          tegn({ ...d, innstillinger: x.innstillinger });
          const m = boks.querySelector(".melding");
          m.textContent = t("ui.innstillinger.lagret");
          meldLive(m.textContent);
        })
        .catch((e) => {
          knapp.disabled = false;
          if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return; }
          melding.textContent = t(e instanceof ApiFeil && e.status === 400
            ? "ui.innstillinger.feil.form" : "ui.plattform.feilet");
        });
    });
    sett(boks,
      el("h2", { id: "innst-tittel", text: t("ui.innstillinger.tittel") }),
      el("p", { text: t("ui.innstillinger.tekst") }),
      skjema);
    boks.hidden = false;
  };
  erPlattformeier()
    .then((d) => {
      if (!(d && d.eier === true)) return null;
      return hentInnstillinger().then(tegn);
    })
    .catch((e) => { if (e instanceof UautorisertFeil) ctx.paaUautorisert?.(); });
  return boks;
}


// OM DISPONIT (234). Eier 27/11: «det må være et skjema for admin også slik
// at admin kan selv legge inn/redigere slike info … alt kan gjøres
// selvbetjent i dashboard både for admin og kunder.» Navnet, org.nr.,
// adressen, kontakten og vernetinget sto før i en fil i koden; nå står de
// her, og vilkårssidene og prøvebanneret leser dem. Samme dør som
// firmalisten: seksjonen finnes bare for plattformeieren.
export function leverandorseksjon(ctx) {
  const boks = el("section", { class: "kort", "aria-labelledby": "lev-tittel" });
  boks.hidden = true;
  const FELT = [
    ["navn", "ui.omdisponit.felt.navn", "text", true],
    ["orgnummer", "ui.omdisponit.felt.orgnummer", "text", false],
    ["adresse", "ui.omdisponit.felt.adresse", "text", false],
    ["kontakt_epost", "ui.omdisponit.felt.kontakt", "email", false],
    ["verneting", "ui.omdisponit.felt.verneting", "text", false],
  ];
  const tegn = (lev) => {
    const melding = el("p", { class: "melding", role: "status" });
    const inn = {};
    const rader = FELT.map(([navn, nokkel, type, paakrevd]) => {
      const id = `lev-${navn}`;
      inn[navn] = el("input", { id, type, class: "felt-inp",
        value: (lev && lev[navn]) || "", autocomplete: "off" });
      if (paakrevd) inn[navn].required = true;
      return el("div", { class: "felt" },
        el("label", { for: id, text: t(nokkel) }), inn[navn],
        ...(navn === "orgnummer" ? [el("p", { class: "hjelpetekst",
          text: t("ui.omdisponit.hjelp.orgnummer") })] : []));
    });
    const knapp = el("button", { type: "submit", class: "knapp primar",
      text: t("ui.omdisponit.lagre") });
    const skjema = el("form", { class: "kv-skjema lev-skjema" }, ...rader,
      el("div", { class: "knapperad" }, knapp), melding);
    skjema.addEventListener("submit", (ev) => {
      ev.preventDefault();
      const kropp = {};
      for (const [navn] of FELT) kropp[navn] = inn[navn].value.trim() || null;
      if (kropp.orgnummer) kropp.orgnummer = kropp.orgnummer.replace(/\s+/g, "");
      // SAMME REGLER SOM BASEN (234), sagt FØR innsending og ved feltet:
      // en 400 som bare sier «feilformet» gir henne ingenting å rette.
      if (kropp.orgnummer && !/^[0-9]{9}$/.test(kropp.orgnummer)) {
        melding.textContent = t("ui.omdisponit.feil.orgnummer");
        inn.orgnummer.focus(); return;
      }
      if (kropp.kontakt_epost
          && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(kropp.kontakt_epost)) {
        melding.textContent = t("ui.omdisponit.feil.kontakt");
        inn.kontakt_epost.focus(); return;
      }
      knapp.disabled = true;
      settLeverandor(kropp)
        .then((d) => {
          tegn(d.leverandor);
          const m = boks.querySelector(".melding");
          m.textContent = t("ui.omdisponit.lagret");
          meldLive(m.textContent);
        })
        .catch((e) => {
          knapp.disabled = false;
          if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return; }
          melding.textContent = t(e instanceof ApiFeil && e.status === 400
            ? "ui.omdisponit.feil.form" : "ui.plattform.feilet");
        });
    });
    sett(boks,
      el("h2", { id: "lev-tittel", text: t("ui.omdisponit.tittel") }),
      el("p", { text: t(lev ? "ui.omdisponit.tekst"
        : "ui.omdisponit.tekst_tom") }),
      skjema);
    boks.hidden = false;
  };
  erPlattformeier()
    .then((d) => {
      if (!(d && d.eier === true)) return null;
      return hentLeverandor().then((x) => tegn(x.leverandor));
    })
    .catch((e) => { if (e instanceof UautorisertFeil) ctx.paaUautorisert?.(); });
  return boks;
}

// VILKÅR OG PERSONVERN (235). Eier: «alt må betjenes via dashboard …
// ikke gjennom koden». Per dokument: hva som gjelder nå (og hvor mange
// firmaer som har godtatt det), et utkast man redigerer, en forhåndsvisning
// som er NØYAKTIG den offentlige siden, og «Publiser». Publiseringen er
// enveis — en publisert versjon endres aldri, fordi firmaer har godtatt
// nettopp de ordene — så den bekreftes i to trinn, som oppsigelsen.
function dato(iso) {
  return iso ? iso.slice(0, 10).split("-").reverse().join(".") : "";
}

export function juridiskseksjon(ctx) {
  const boks = el("section", { class: "kort", "aria-labelledby": "jur-tittel" });
  boks.hidden = true;
  const melding = el("p", { class: "melding", role: "status" });
  const feil = (e) => {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return; }
    melding.textContent = t("ui.juridiskadmin.feil");
    meldLive(melding.textContent);
  };

  const dokKort = (d, felt) => {
    const g = d.gjeldende;
    const u = d.utkast;
    const id = `jur-${d.dok}`;
    const tekst = el("textarea", { id, class: "felt-inp jur-tekst", rows: "14",
      spellcheck: "true" });
    tekst.value = (u || g || {}).tekst || "";
    const status = el("p", { class: "muted", text: g
      ? t("ui.juridiskadmin.gjeldende").replace("{v}", String(g.versjon))
        .replace("{dato}", dato(g.publisert))
        .replace("{n}", String(g.samtykker))
      : t("ui.juridiskadmin.ikke_publisert") });
    const utkastinfo = el("p", { class: "muted", text: u
      ? t("ui.juridiskadmin.utkast").replace("{v}", String(u.versjon))
      : t("ui.juridiskadmin.ingen_utkast") });
    const forhand = el("div", { class: "jur-forhand", hidden: true });
    const lagre = el("button", { type: "button", class: "knapp",
      text: t("ui.juridiskadmin.lagre") });
    const vis = el("button", { type: "button", class: "knapp",
      text: t("ui.juridiskadmin.forhandsvis"), "aria-expanded": "false" });
    const publiser = el("button", { type: "button", class: "knapp primar",
      text: t("ui.juridiskadmin.publiser") });
    publiser.disabled = !u;
    const rad = el("div", { class: "knapperad" }, lagre, vis, publiser);

    lagre.addEventListener("click", () => {
      if (!tekst.value.trim()) { tekst.focus(); return; }
      lagre.disabled = true;
      lagreJuridiskUtkast(d.dok, tekst.value)
        .then(() => last(t("ui.juridiskadmin.lagret")))
        .catch(feil).finally(() => { lagre.disabled = false; });
    });
    vis.addEventListener("click", () => {
      const apen = forhand.hidden;
      forhand.hidden = !apen;
      vis.setAttribute("aria-expanded", String(apen));
      if (apen) {
        // Feltene fylles inn på serveren ved lesing; her vises navnene,
        // så eieren ser hvor de står.
        sett(forhand, ...tekstTilNoder(tekst.value));
      }
    });
    publiser.addEventListener("click", () => {
      // TO TRINN: «Publiser» bytter til en bekreftelse med «Avbryt» i fokus.
      const ja = el("button", { type: "button", class: "knapp primar",
        text: t("ui.juridiskadmin.publiser_ja") });
      const nei = el("button", { type: "button", class: "knapp",
        text: t("ui.juridiskadmin.avbryt") });
      const sporsmal = el("p", { class: "advarsel",
        text: t("ui.juridiskadmin.publiser_sporsmal") });
      nei.addEventListener("click", () => {
        sett(rad, lagre, vis, publiser); publiser.focus();
      });
      ja.addEventListener("click", () => {
        ja.disabled = true; nei.disabled = true;
        publiserJuridisk(d.dok)
          .then(() => last(t("ui.juridiskadmin.publisert")))
          .catch((e) => { ja.disabled = false; nei.disabled = false; feil(e); });
      });
      sett(rad, sporsmal, ja, nei);
      nei.focus();
    });
    return el("section", { class: "jur-dok", "aria-labelledby": `${id}-t` },
      el("h3", { id: `${id}-t` },
        t(`ui.juridisk.dok.${d.dok}`), " ",
        el("a", { href: `/${d.dok}`, target: "_blank", rel: "noopener",
          text: t("ui.juridiskadmin.se_siden") })),
      status, utkastinfo,
      el("div", { class: "felt jur-felt" },
        el("label", { for: id, text: t("ui.juridiskadmin.tekst") }), tekst,
        el("p", { class: "hjelpetekst", text: t("ui.juridiskadmin.felt")
          .replace("{felt}", felt.map((f) => `{{${f}}}`).join(", ")) })),
      rad, forhand);
  };

  const last = (kvittering) => hentJuridisk().then((d) => {
    sett(boks,
      el("h2", { id: "jur-tittel", text: t("ui.juridiskadmin.tittel") }),
      el("p", { text: t("ui.juridiskadmin.tekst_inn") }),
      ...(d.dokumenter || []).map((x) => dokKort(x, d.felt || [])),
      melding);
    boks.hidden = false;
    if (kvittering) { melding.textContent = kvittering; meldLive(kvittering); }
  });

  erPlattformeier()
    .then((d) => { if (d && d.eier === true) return last(); return null; })
    .catch(feil);
  return boks;
}

export function visAdmin(hoved, ctx = {}) {
  const telling = plattformTelling();
  const forvalter = kanForvaltePolicy(ctx);
  // Snarveiene nederst peker bare på ruter økten har. En ren plattformdriftsøkt
  // bærer ikke kundens tenant-lokale lesescopes, og lesefallbacken til `#/policy`
  // er heller ikke gratis: `policy:read` er sitt eget scope.
  const ruter = new Set(byggRuter(ctx).map((r) => r.nokkel));
  // Tenanttabellen er kontrollplan på tvers av kunder, og krever
  // plattformdrift. En tenantbundet ops-økt (`security:read`) ser bare sin
  // egen rad — ikke hver eneste andre kundes plan, moduler og neste steg.
  const plattformdrift = erPlattformdrift(ctx);
  // Radene kommer UTENFRA — fra en autentisert, server-autorisert vei — og
  // ligger ikke i klientpakken. `/ui/{sti}` og `/ui/locale/{sprak}` serveres
  // uten sesjonssjekk, så et tenantregister i bundelen (eller som
  // `site.tenant.*`-nøkler i locale-settet) ville vært nedlastbart anonymt
  // uansett hva denne scope-sjekken velger å rendre. Tom liste = vi vet ikke,
  // og da sier flaten det.
  const rader = Array.isArray(ctx.tenanter) ? ctx.tenanter : [];
  const tenanter = plattformdrift
    ? rader
    : rader.filter((rad) => rad.id === ctx.tenant || rad.navn === ctx.tenant);

  // MIN PROFIL (eiervedtak 1/9). Identiteten sto sentrert i topplinjen på
  // hver eneste side — e-post, en 64-tegns prinsipal-id, rollelisten og et
  // ruteantall, uten ledetekster. Eier: «masse unødvendig informasjon der
  // oppe, bør alt plasseres under admin/profil» og «det er rotete med
  // bid…». Den står her nå, som en definisjonsliste: hver verdi har en
  // ledetekst som sier hva den ER, i stedet for å være en streng man må
  // gjette på.
  //
  // Prinsipal-id-en er den som betyr noe for fire-øyne (`(issuer, sub)`,
  // ikke e-posten), så den står ubeskåret her — dette er stedet den hører
  // hjemme, der det er plass til å forklare den.
  const profilRader = [
    [t("ui.profil.epost"), ctx.epost],
    [t("ui.profil.bruker_id"), ctx.bruker_id],
    [t("ui.profil.roller"), Array.isArray(ctx.roller) && ctx.roller.length
      ? ctx.roller.map((r) => t(`ui.rolle.${r}`, r)).join(", ") : null],
    [t("ui.profil.tenant"), ctx.tenant],
    [t("ui.profil.ruter"), String(ruter.size)],
  ].filter(([, v]) => v);
  const profil = el("section", { class: "kort" },
    el("h2", { text: t("ui.profil.tittel") }),
    el("dl", { class: "kv-liste" },
      ...profilRader.flatMap(([n, v]) => [
        el("dt", { text: n }),
        el("dd", { class: "celle-tekst", text: v }),
      ])));

  // FANER, IKKE EN LANG SIDE (eier 27/11: «når man klikker plattform, siden
  // blir lang … disse bør organiseres som tabmeny»). Hver seksjon er et
  // direkte barn av `.kpi-kort-liste` med sin egen `h2`, og seksjonsfanene
  // (seksjonsfaner.js) gjør dem til faner — én om gangen, med piltaster.
  // Driftsbildet under (status, utrulling, moduler, tenanter, snarveier) er
  // samlet i ÉN fane; det var resten av den lange siden.
  //
  // Plattformeierens seksjoner tegner seg selv NÅR serveren har sagt at
  // kalleren er plattformeier. Til da er de `hidden`, og for alle andre
  // forblir de det for alltid — og en skjult seksjon er ingen fane.
  const drift = el("section", { "aria-labelledby": "admin-drift-t" },
    el("h2", { id: "admin-drift-t", class: "sr-only",
      text: t("ui.admin.drift_fane") }),
    el("div", { class: "site-grid site-grid-3" },
      el("section", { class: "kort site-hero-card" },
        el("p", { class: "site-eyebrow", text: t("ui.admin.status") }),
        el("h2", { text: t("ui.admin.status_tittel") }),
        el("p", { text: t("ui.admin.status_tekst") }),
        el("div", { class: "site-kpi-row" },
          el("div", { class: "site-kpi" },
            el("strong", { text: `${telling.iDrift}/${telling.totalt}` }),
            el("span", { text: t("ui.admin.kpi.i_drift") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(telling.underArbeid) }),
            el("span", { text: t("ui.admin.kpi.under_arbeid") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(tenanter.length) }),
            el("span", { text: t("ui.admin.kpi.tenanter") })))),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.admin.utrulling") }),
        el("h2", { text: t("ui.admin.utrulling_tittel") }),
        el("ol", { class: "site-list site-list-ordered" },
          el("li", { text: t("ui.admin.utrulling.ci") }),
          el("li", { text: t("ui.admin.utrulling.staging") }),
          el("li", { text: t("ui.admin.utrulling.kanari") }),
          el("li", { text: t("ui.admin.utrulling.rollback") }))),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.admin.tillit") }),
        el("h2", { text: t("ui.admin.tillit_tittel") }),
        el("ul", { class: "site-list" },
          el("li", { text: t("ui.admin.tillit.p1") }),
          el("li", { text: t("ui.admin.tillit.p2") }),
          el("li", { text: t("ui.admin.tillit.p3") })))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.kontrollplan") }),
          el("h2", { text: t("ui.admin.kontrollplan_tittel") }))),
      el("div", { class: "site-grid site-grid-3" },
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.kontrollplan.release_tittel") }),
          el("p", { text: t("ui.admin.kontrollplan.release_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.kontrollplan.tenant_tittel") }),
          el("p", { text: t("ui.admin.kontrollplan.tenant_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.kontrollplan.port_tittel") }),
          el("p", { text: t("ui.admin.kontrollplan.port_tekst") })))),
    // Modulregisteret sto på den PUBLIKE forsiden, med badges og et
    // «0/45 i drift» som første tall en besøkende møtte. Statusen er ekte og
    // skal fortsatt være bindende — men den er et driftsbilde, ikke et
    // salgsargument, så den leses her, av dem som styrer utrullingen.
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("site.moduler") }),
          el("h2", { text: t("site.moduler_tittel") })),
        el("span", { class: "site-inline-note", text: t("site.moduler_note") })),
      el("div", { class: "site-card-grid" },
        MODULOVERSIKT.map((mod) => siteModuleKort(mod)))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.faser") }),
          el("h2", { text: t("ui.admin.faser_tittel") })),
        el("span", { class: "site-inline-note", text: t("ui.admin.faser_note") })),
      el("div", { class: "site-card-grid" },
        FASEOVERSIKT.map((fase) =>
          el("article", { class: "site-module-card" },
            el("div", { class: "site-module-head" },
              el("strong", { text: t(fase.navn_nokkel) }),
              siteFaseMerke(fase.status)),
            el("p", { text: t(fase.tekst_nokkel) }))))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.tenanter") }),
          el("h2", { text: plattformdrift
            ? t("ui.admin.tenanter_tittel")
            : t("ui.admin.tenanter_egen_tittel") })),
        plattformdrift
          ? null
          : el("span", { class: "site-inline-note",
            text: t("ui.admin.tenanter_egen_note") })),
      tenanter.length
        ? el("div", { class: "tablewrap" },
          el("table", {},
            el("caption", { class: "sr-only", text: plattformdrift
              ? t("ui.admin.tenanter_tittel")
              : t("ui.admin.tenanter_egen_tittel") }),
            el("thead", {},
              el("tr", {},
                el("th", { scope: "col", text: t("ui.admin.kol.tenant") }),
                el("th", { scope: "col", text: t("ui.admin.kol.plan") }),
                el("th", { scope: "col", text: t("ui.admin.kol.moduler") }),
                el("th", { scope: "col", text: t("ui.admin.kol.neste") }))),
            el("tbody", {},
              tenanter.map((tenant) =>
                el("tr", {},
                  // Verdiene er DATA fra den autentiserte veien, ikke
                  // locale-nøkler: kundenavn er ikke oversettelser, og en
                  // `t()` her ville vært en invitasjon til å legge dem
                  // tilbake i det offentlige locale-settet.
                  el("td", {}, el("strong", { text: tenant.navn || "" })),
                  // Unntaket er `plan`: den kommer som KODE fra et lukket
                  // vokabular, og etiketten er chrome. `neste` er fritekst per
                  // kunde og er allerede oversatt av serveren — den kan ikke
                  // ligge i det anonymt nedlastbare locale-settet.
                  el("td", { text: planEtikett(tenant.plan) }),
                  el("td", { text: Array.isArray(tenant.moduler)
                    ? tenant.moduler.map(modulmerke).join(", ") : "" }),
                  el("td", { text: tenant.neste || "" }))))))
        : el("p", { class: "muted", text: t("ui.admin.tenanter_ukjent") })),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.handlinger") }),
          el("h2", { text: t("ui.admin.handlinger_tittel") }))),
      el("div", { class: "site-card-grid" },
        // Kundeadmin er en leseflate og ligger i basisrutene — snarveien dit
        // gjelder derfor alle. PolicyADMINISTRASJONEN krever forvaltning:
        // admin-flaten åpnes av `security:read`, og rollene `admin`/`sikkerhet`
        // har bare `policy:read`, så snarveien ville pekt på en flate ruteren
        // nekter dem, med mutasjonsknapper som uansett gir 403.
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.handling.kundeadmin") }),
          el("p", { text: t("ui.admin.handling.kundeadmin_tekst") }),
          el("a", { class: "lenkeknapp", href: "#/kundeadmin",
            text: t("ui.admin.handling.ga_til") })),
        ...(forvalter ? [
          el("article", { class: "site-mini-card" },
            el("strong", { text: t("ui.admin.handling.policyadmin") }),
            el("p", { text: t("ui.admin.handling.policyadmin_tekst") }),
            el("a", { class: "lenkeknapp", href: "#/policyadmin",
              text: t("ui.admin.handling.ga_til") })),
        // Leserettighet skal fortsatt komme til policy — bare til
        // lesevisningen, ikke til aktiveringsflaten. Uten `policy:read` finnes
        // ingen av delene, og da er ingen snarvei riktigere enn en som 403-er.
        ] : ruter.has("policy") ? [
          el("article", { class: "site-mini-card" },
            el("strong", { text: t("ui.admin.handling.policy_lesing") }),
            el("p", { text: t("ui.admin.handling.policy_lesing_tekst") }),
            el("a", { class: "lenkeknapp", href: "#/policy",
              text: t("ui.admin.handling.ga_til") })),
        ] : []),
        ...(ruter.has("unntak") ? [
          el("article", { class: "site-mini-card" },
            el("strong", { text: t("ui.admin.handling.unntak") }),
            el("p", { text: t("ui.admin.handling.unntak_tekst") }),
            el("a", { class: "lenkeknapp", href: "#/unntak",
              text: t("ui.admin.handling.ga_til") })),
        ] : []))));

  sett(hoved,
    ...flateHode(t("ui.admin.tittel"), t("ui.admin.undertittel")),
    el("div", { class: "kpi-kort-liste en-kolonne" },
      profil,
      ...plattformseksjon(ctx),
      innstillingsseksjon(ctx),
      pliktkatalogseksjon(ctx),
      leverandorseksjon(ctx),
      juridiskseksjon(ctx),
      drift));
}
