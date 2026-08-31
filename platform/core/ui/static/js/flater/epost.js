// M-6 PR-B: kildeflaten — postboksene bak e-postagenten. LITEN med
// vilje: liste over tilkoblede kilder (postboks, status, sist hentet),
// «Koble til M365» (åpner Microsofts authorize-URL som TOPPNIVÅ-
// navigasjon — OAuth-samtykket er en sidereise, aldri et XHR) og
// enveis deaktivering. Klassifiserings-/utkastsflaten er PR-D.
//
// TABELLEN ER TILGANGSFORMEN (m16-formen): ekte <table> med <caption>
// og th scope, status som TEKST (aldri kun farge). Forvaltnings-
// kontrollene vises KUN når økten bærer `epost:kilde:administrer` —
// samme regel som wcagkontrolls faner: menyen/ruten gates av flatens
// svakeste ledd (`epost:read`), mutasjonene av sitt eget scope, og
// serveren håndhever begge uansett hva flaten viser.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentEpostKilder, startEpostKilde, deaktiverEpostKilde,
         nyIdempotensnokkel, UautorisertFeil, ApiFeil } from "../api.js";
import { Tidspunkt, TomTilstand, meldLive } from "../komponenter.js";
import { visningsToken, erGjeldendeVisning } from "../ruter.js";
import { Bekreftelsesdialog } from "../dialog.js";
import { medStatus, flateHode } from "./felles.js";

const ADMINSCOPE = "epost:kilde:administrer";

// Toppnivå-navigasjonen er et SNITT (i18n.js' `settI18nForTest`-form).
// jsdoms `Location` er [Unforgeable]: `assign` kan verken skrives over
// eller redefineres — verken på instansen eller på prototypen. Uten
// snittet er porten «flaten sender eier til SERVERENS authorize-URL,
// aldri en egenbygd» umålbar, og det er nettopp den porten som holder
// klientsiden fra å konstruere OAuth-URL-er selv.
let _naviger = (url) => { window.location.assign(url); };

export function settNavigasjonForTest(fn) { _naviger = fn; }

function statusTekst(status) {
  const kjent = { aktiv: 1, feilet: 1, deaktivert: 1 };
  return kjent[status]
    ? t(`ui.epost.status.${status}`) : t("ui.epost.status.ukjent");
}

function kildetabell(kilder, kanAdministrere, paaDeaktiver) {
  if (!kilder.length) {
    return TomTilstand({ tittel: t("ui.epost.tom"),
      tekst: t("ui.epost.tom_tekst") });
  }
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.epost.kilder_caption") }));
  const hode = [
    el("th", { scope: "col", text: t("ui.epost.kolonne.postboks") }),
    el("th", { scope: "col", text: t("ui.epost.kolonne.status") }),
    el("th", { scope: "col", text: t("ui.epost.kolonne.sist_hentet") }),
    el("th", { scope: "col", text: t("ui.epost.kolonne.opprettet") }),
  ];
  if (kanAdministrere) {
    hode.push(el("th", { scope: "col",
      text: t("ui.epost.kolonne.handling") }));
  }
  tabell.append(el("thead", {}, el("tr", {}, ...hode)));
  const tbody = el("tbody");
  for (const k of kilder) {
    // Postboksen NAVNGIR raden (m16-regelen: scope="row", ellers mister
    // en skjermleser i de andre kolonnene hvilken boks verdien gjelder).
    const celler = [
      el("th", { scope: "row", text: k.postboks }),
      el("td", { text: statusTekst(k.status) }),
      el("td", {}, k.sist_hentet_ts
        ? Tidspunkt(k.sist_hentet_ts, {})
        : el("span", { text: t("ui.epost.aldri_hentet") })),
      el("td", {}, Tidspunkt(k.opprettet, {})),
    ];
    if (kanAdministrere) {
      // En deaktivert kilde har ingen handling — reaktivering finnes
      // ikke som knapp: veien tilbake er en FULL ny samtykkeflyt, og
      // det står i teksten i stedet for som en død kontroll.
      let handling;
      if (k.status === "deaktivert") {
        handling = el("span", { class: "muted",
          text: t("ui.epost.deaktivert") });
      } else {
        handling = el("button", { class: "knapp fare", type: "button",
          text: t("ui.epost.deaktiver") });
        handling.addEventListener("click", () => paaDeaktiver(k));
      }
      celler.push(el("td", {}, handling));
    }
    tbody.append(el("tr", {}, ...celler));
  }
  tabell.append(tbody);
  return tabell;
}

export function visEpost(hoved, ctx) {
  const minRute = visningsToken(hoved);
  const eierSkjermen = () => erGjeldendeVisning(hoved, minRute);
  const kanAdministrere = (ctx.scopes || []).includes(ADMINSCOPE);

  // Idempotensnøkkelen holdes av FLATEN og er stabil så lenge
  // postboksfeltet står urørt (038-regelen): et tapt svar + nytt klikk
  // REPLAYer samme authorize-URL i stedet for å utstede state nummer to.
  let idemnokkel = nyIdempotensnokkel();

  const tegn = () => medStatus(hoved, ctx,
    () => hentEpostKilder(),
    (d) => {
      const kilder = d.kilder || [];
      const deler = [
        ...flateHode(t("ui.epost.tittel"), t("ui.epost.undertittel")),
        kildetabell(kilder, kanAdministrere, bekreftDeaktiver),
      ];
      if (kanAdministrere) deler.push(koblingsseksjon());
      sett(hoved, ...deler);
    });

  function koblingsseksjon() {
    const inputId = "epost-kilde-postboks";
    const input = el("input", { id: inputId, type: "email",
      autocomplete: "off" });
    input.addEventListener("input",
      () => { idemnokkel = nyIdempotensnokkel(); });
    const feilfelt = el("p", { class: "muted", "aria-live": "polite" });
    const knapp = el("button", { type: "button",
      text: t("ui.epost.koble_til") });
    knapp.addEventListener("click", () => koble(input, knapp, feilfelt));
    return el("section", {},
      el("h2", { text: t("ui.epost.koble_tittel") }),
      el("p", { class: "muted", text: t("ui.epost.koble_forklaring") }),
      el("label", { for: inputId, text: t("ui.epost.postboks_label") }),
      input, knapp, feilfelt);
  }

  function koble(input, knapp, feilfelt) {
    const postboks = (input.value || "").trim();
    if (!postboks) {
      feilfelt.textContent = t("ui.epost.postboks_mangler");
      return;
    }
    // BEGGE kontrollene låses, ikke bare knappen: `input`-lytteren
    // ruller idempotensnøkkelen, så en redigering mens /start er i lufta
    // ville latt eier bli sendt til Microsoft for den GAMLE boksen med en
    // nøkkel som ikke lenger hører til noe (CodeRabbit).
    knapp.disabled = true;
    input.disabled = true;
    startEpostKilde(postboks, idemnokkel)
      .then((d) => {
        // Samtykket er en toppnivå-navigasjon — hele appen forlates,
        // og callbacken tar eier tilbake til denne flaten. Men BARE hvis
        // ruten fortsatt er vår: rakk eier å bytte flate mens svaret var
        // i lufta, skal et sent svar ikke rive vedkommende til Microsoft
        // (samme vakt som `deaktiver` bruker på omtegningen).
        if (!eierSkjermen()) return;
        _naviger(d.autorisasjonsurl);
      })
      .catch((e) => {
        knapp.disabled = false;
        input.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        feilfelt.textContent =
          (e instanceof ApiFeil && e.kode === "m365_ikke_konfigurert")
            ? t("ui.epost.ikke_konfigurert") : t("ui.epost.feilet");
        meldLive(feilfelt.textContent);
      });
  }

  // Deaktivering er ENVEIS: veien tilbake er en full ny samtykkerunde
  // hos Microsoft. Derfor bak en bekreftelse (policy.js-formen), og
  // teksten beskriver tilstanden ETTER handlingen — det er den som
  // avgjør om eier vil.
  function bekreftDeaktiver(k) {
    Bekreftelsesdialog({
      tittel: t("ui.epost.deaktiver_tittel"),
      tekst: t("ui.epost.deaktiver_tekst").replace("{postboks}", k.postboks),
      primarTekst: t("ui.epost.deaktiver"),
      farlig: true,
      rolle: "alertdialog",
      paaPrimar: () => deaktiver(k),
    });
  }

  function deaktiver(k) {
    deaktiverEpostKilde(k.kilde_id)
      .then(() => {
        meldLive(t("ui.epost.deaktivert_melding")
          .replace("{postboks}", k.postboks));
        if (eierSkjermen()) tegn();
      })
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        meldLive(t("ui.epost.feilet"));
      });
  }

  tegn();
}
