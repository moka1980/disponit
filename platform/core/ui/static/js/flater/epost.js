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
         hentEpostMeldinger, hentEpostMelding, slettEpostMelding,
         skrivSvarutkast, avgjorSvarutkast,
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

// M-6 PR-D a: meldingene innhenteren (175) la i registeret — LESENDE.
// Ingen svar-, videresend- eller slett-knapp: v1 viser. Avsender og
// emne er dekryptert av serveren for denne økten; en reapet melding
// vises som reapet (tidspunkt består, teksten er borte).
function meldingsliste(ctx, kilde, alle, avkortet, hentDetalj,
                      kanAdministrere, paaSlett) {
  // ETT PANEL PER LISTE (CodeRabbit): en DOM-node kan bare stå ett sted,
  // så et delt panel ville havnet under den SISTE kilden — og en melding
  // åpnet i den første ville dukket opp et helt annet sted på siden.
  const panel = meldingspanel();
  const boks = el("section", {},
    el("h3", { text: t("ui.epost.meldinger.tittel")
      .replace("{postboks}", kilde.postboks) }));
  // SLETTEDE MELDINGER ER SPOR, IKKE INNBOKS. Raden består i registeret
  // — det er hele poenget med at slettingen kan bevises — men en tom rad
  // eier selv fjernet, hører ikke hjemme i lista over det som ligger der.
  // Bryteren under viser dem, for den som vil se hva som er borte.
  const meldinger = alle.filter((m) => !m.reapet);
  const slettede = alle.filter((m) => m.reapet);
  if (!meldinger.length && !slettede.length) {
    boks.append(el("p", { class: "muted", text: t("ui.epost.meldinger.ingen") }));
    return boks;
  }
  if (!meldinger.length) {
    boks.append(el("p", { class: "muted", text: t("ui.epost.meldinger.bare_slettede") }));
  }
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.epost.meldinger.caption")
      .replace("{postboks}", kilde.postboks) }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.epost.meldinger.kolonne.mottatt") }),
    el("th", { scope: "col", text: t("ui.epost.meldinger.kolonne.fra") }),
    el("th", { scope: "col", text: t("ui.epost.meldinger.kolonne.emne") }),
    el("th", { scope: "col", text: t("ui.epost.meldinger.kolonne.slettes") }),
    el("th", { scope: "col", text: t("ui.epost.kolonne.handling") }))));
  const tbody = el("tbody");
  for (const m of meldinger) {
    const knapp = el("button", { type: "button", text: t("ui.epost.meldinger.apne") });
    knapp.addEventListener("click", () => hentDetalj(m, panel, kilde));
    // ÉN RAD, IKKE EN STABEL: handlingene hører sammen og står ved siden
    // av hverandre (eiers merknad 10/9).
    const handlinger = el("div", { class: "knapperad" }, knapp);
    // Slettingen er forvaltning (`epost:kilde:administrer`) og enveis:
    // bak en bekreftelse, som kildedeaktiveringen.
    if (kanAdministrere) {
      const slett = el("button", { class: "knapp fare", type: "button",
        text: t("ui.epost.meldinger.slett") });
      slett.addEventListener("click", () => paaSlett(m));
      handlinger.append(slett);
    }
    tbody.append(el("tr", {},
      el("th", { scope: "row" }, Tidspunkt(m.mottatt_ts, {})),
      el("td", { text: m.fra_navn ? `${m.fra_navn} <${m.fra}>` : (m.fra || "—") }),
      el("td", { text: (m.emne || t("ui.epost.meldinger.uten_emne"))
        + (m.har_vedlegg ? " " + t("ui.epost.meldinger.vedlegg") : "") }),
      el("td", {}, Tidspunkt(m.slettes_ts, {})),
      el("td", {}, handlinger)));
  }
  tabell.append(tbody);
  if (meldinger.length) boks.append(tabell);
  if (avkortet) {
    boks.append(el("p", { class: "muted", text: t("ui.epost.meldinger.avkortet") }));
  }
  // PANELET STÅR HER, rett under lista det hører til — ikke nederst på
  // siden, der en åpnet melding ser ut som ingenting (eiers merknad).
  boks.append(panel.node);
  if (slettede.length) boks.append(slettetliste(slettede));
  return boks;
}

function slettetliste(slettede) {
  // Sporet, sammenklappet: hva som er borte, når og av hvem — aldri hva
  // det inneholdt, for det er nettopp det slettingen fjernet.
  const detaljer = el("details", {},
    el("summary", { text: t("ui.epost.meldinger.slettede_vis")
      .replace("{n}", String(slettede.length)) }));
  const liste = el("ul", {});
  for (const m of slettede) {
    const nokkel = m.slettet_for_fristen
      ? "ui.epost.meldinger.slettet_av_menneske"
      : "ui.epost.meldinger.slettet_av_fristen";
    const rad = el("li", {});
    rad.append(el("span", { text: t(nokkel) + " " }));
    rad.append(Tidspunkt(m.slettet_ts || m.slettes_ts, {}));
    rad.append(el("span", { text: " · " + t("ui.epost.meldinger.mottatt_kort") + " " }));
    rad.append(Tidspunkt(m.mottatt_ts, {}));
    liste.append(rad);
  }
  detaljer.append(liste);
  return detaljer;
}


// LESEVISNINGEN (eiers merknad 10/9: «ikke brukervennlig»).
//
// Graph gir oss tekstversjonen av en HTML-post, og den er en maskinell
// nedkonvertering: hver lenke slepper adressen sin etter seg, hvert
// bilde blir «[alt-tekst] <url>», og en nyhetsbrevmal blir sider med
// URL-er rundt én setning. Vi kan ikke rendre HTML-en — den ville vært
// fremmed markup i vår egen flate — men vi kan la være å vise
// maskinstøyen som om den var innhold.
//
// ORIGINALEN RØRES ALDRI: den ligger kryptert i registeret, og panelet
// har den bak en bryter. Rensingen er en VISNING, ikke en redigering.
export function lesbarTekst(raa) {
  if (!raa) return "";
  const ut = [];
  for (const linje of String(raa).split(/\r?\n/)) {
    let l = linje;
    // Bilder: «[En skjerm som viser …] <https://…>» og «[https://…]».
    l = l.replace(/\[[^\]]*\]\s*<https?:\/\/[^>]*>/g, "");
    l = l.replace(/\[\s*https?:\/\/[^\]]*\]/g, "");
    // Lenker: «Les mer <https://…>» → «Les mer». Adressen kan uansett
    // ikke klikkes i en tekstvisning.
    l = l.replace(/\s*<https?:\/\/[^>]*>/g, "");
    // En linje som BARE er en adresse, sier ingenting alene.
    if (/^\s*https?:\/\/\S+\s*$/.test(l)) continue;
    ut.push(l.trimEnd());
  }
  // Malenes luft: tre tomme linjer på rad blir én.
  return ut.join("\n").replace(/\n{3,}/g, "\n\n").trim();
}

// SVARET (179): mennesket skriver, mennesket godkjenner. Flaten sender
// ingenting — et godkjent utkast er en tilstand, og plattformen sender
// det innenfor policyen etterpå.
function svarseksjon(m, kilde, kanAdministrere, paaEndring) {
  const boks = el("section", {});
  const utkast = m.utkast || [];
  if (utkast.length) {
    boks.append(el("h4", { text: t("ui.epost.svar.tidligere") }));
    const liste = el("ul", {});
    for (const u of utkast) {
      const rad = el("li", {});
      rad.append(el("p", { class: "muted",
        text: t(`ui.epost.svar.status.${u.status}`) + " · "
          + (u.avgjort_av || "") }));
      rad.append(el("pre", { class: "epost-kropp",
        text: u.tekst || t("ui.epost.svar.uten_tekst") }));
      if (kanAdministrere && u.status === "foreslatt") {
        const rad2 = el("div", { class: "knapperad" });
        for (const [nokkel, status] of [["godkjenn", "godkjent"],
                                        ["forkast", "forkastet"]]) {
          const b = el("button", { type: "button",
            text: t(`ui.epost.svar.knapp.${nokkel}`) });
          if (status === "forkastet") b.classList.add("fare");
          b.addEventListener("click", () => paaEndring(
            () => avgjorSvarutkast(u.utkast_id, status)));
          rad2.append(b);
        }
        rad.append(rad2);
      }
      liste.append(rad);
    }
    boks.append(liste);
  }
  if (!kanAdministrere) return boks;
  if (kilde && kilde.kan_svare === false) {
    boks.append(el("p", { class: "muted", text: t("ui.epost.svar.uten_tilgang") }));
    return boks;
  }
  const id = `svar-${m.melding_id}`;
  const felt = el("textarea", { id, rows: 5, maxlength: 32768 });
  const knapp = el("button", { type: "submit", text: t("ui.epost.svar.lagre") });
  const skjema = el("form", { class: "kv-skjema" },
    el("label", { for: id, text: t("ui.epost.svar.tittel") }), felt,
    el("p", { class: "muted", text: t("ui.epost.svar.forklaring") }),
    el("div", { class: "skjema-bunn" }, knapp));
  skjema.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!felt.value.trim() || knapp.disabled) return;
    // Låst mens kallet er i lufta (CodeRabbit): to raske trykk skal
    // ikke bli to utkast av samme svar.
    knapp.disabled = true;
    Promise.resolve(paaEndring(() => skrivSvarutkast(m.melding_id, felt.value)))
      .finally(() => { knapp.disabled = false; });
  });
  boks.append(el("h4", { text: t("ui.epost.svar.nytt") }), skjema);
  return boks;
}

function meldingspanel() {
  const boks = el("div", { class: "skjemaboks" });
  boks.hidden = true;
  boks.tabIndex = -1;
  return {
    node: boks,
    fokuser() {
      boks.focus();
      if (boks.scrollIntoView) boks.scrollIntoView({ block: "nearest" });
    },
    vis(m, kilde, kanAdministrere, paaEndring) {
      const til = (m.til || []).join(", ");
      const raa = m.kropp || "";
      const ren = lesbarTekst(raa);
      const kropp = el("pre", { class: "epost-kropp",
        text: ren || t("ui.epost.meldinger.tom_kropp") });
      const deler = [
        el("h3", { text: m.emne || t("ui.epost.meldinger.uten_emne") }),
        el("p", { class: "muted", text: `${m.fra_navn ? m.fra_navn + " " : ""}<${m.fra || "—"}>`
          + (til ? ` → ${til}` : "") }),
        el("p", { class: "muted" }, Tidspunkt(m.mottatt_ts, {})),
        kropp];
      // BRYTEREN VEKSLER, den legger ikke til (eiers merknad 10/9: «da
      // blir det duplikat visning»). Én melding, én tekst på skjermen —
      // knappen bytter hvilken av de to man ser.
      if (ren !== raa.trim()) {
        let raatt = false;
        const bytt = el("button", { type: "button",
          text: t("ui.epost.meldinger.vis_raa") });
        bytt.addEventListener("click", () => {
          raatt = !raatt;
          kropp.textContent = raatt
            ? raa : (ren || t("ui.epost.meldinger.tom_kropp"));
          bytt.textContent = t(raatt ? "ui.epost.meldinger.vis_lesbar"
                                     : "ui.epost.meldinger.vis_raa");
          bytt.setAttribute("aria-pressed", String(raatt));
        });
        bytt.setAttribute("aria-pressed", "false");
        deler.splice(3, 0, el("div", { class: "knapperad" }, bytt));
      }
      if (paaEndring) {
        deler.push(svarseksjon(m, kilde, kanAdministrere, paaEndring));
      }
      sett(boks, ...deler);
      boks.hidden = false;
    },
    feil(tekst) {
      sett(boks, el("p", { role: "alert", text: tekst }));
      boks.hidden = false;
    },
  };
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
    async () => {
      const d = await hentEpostKilder();
      // Meldingene per AKTIV kilde (og feilet — det som alt er hentet,
      // står). En liste som ikke kan hentes stopper ikke kildetabellen:
      // den sies per kilde.
      const meldinger = {};
      for (const k of d.kilder || []) {
        if (k.status === "deaktivert") continue;
        try { meldinger[k.kilde_id] = await hentEpostMeldinger(k.kilde_id); }
        catch (e) {
          if (e instanceof UautorisertFeil) throw e;
          meldinger[k.kilde_id] = null;
        }
      }
      return { ...d, meldinger };
    },
    (d) => {
      const kilder = d.kilder || [];
      // Bare det SISTE valget får tegne panelet (CodeRabbit): to raske
      // klikk er to svar i lufta, og et sent svar for det første skal
      // ikke overskrive det andre — verken som innhold eller som feil.
      // Valget er felles for alle listene: én melding er åpen om gangen.
      let valgt = null;
      // En endring på svaret tegner meldingen på nytt, så tilstanden på
      // skjermen alltid er den registeret har.
      const hentDetalj = (m, panel, kilde) => {
        const paaEndring = (kall) => kall()
          .then(() => hentDetalj(m, panel, kilde))
          .catch((e) => {
            if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
            panel.feil(e && e.status === 409
              ? t("ui.epost.svar.feil_tilstand") : t("ui.epost.feilet"));
          });
        valgt = m.melding_id;
        hentEpostMelding(m.melding_id)
          .then((full) => {
            if (!eierSkjermen() || valgt !== m.melding_id) return;
            panel.vis(full, kilde, kanAdministrere, paaEndring);
            panel.fokuser();
          })
          .catch((e) => {
            if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
            if (valgt === m.melding_id) { panel.feil(t("ui.epost.feilet")); panel.fokuser(); }
          });
      };
      const deler = [
        ...flateHode(t("ui.epost.tittel"), t("ui.epost.undertittel")),
        kildetabell(kilder, kanAdministrere, bekreftDeaktiver),
      ];
      // SAMTYKKET AVGJØR, ikke koden: en postboks koblet før
      // sendetilgangen ble bedt om, kan leses men ikke svares fra. Sagt
      // HER, over meldingene, i stedet for som en feil når svaret alt er
      // skrevet og godkjent.
      for (const k of kilder) {
        if (k.status === "deaktivert" || k.kan_svare !== false) continue;
        deler.push(el("p", { role: "status", class: "muted",
          text: t("ui.epost.kilde.mangler_sendetilgang")
            .replace("{postboks}", k.postboks) }));
      }
      for (const k of kilder) {
        if (k.status === "deaktivert") continue;
        const svar = (d.meldinger || {})[k.kilde_id];
        if (svar === null) {
          deler.push(el("p", { role: "alert", text: t("ui.epost.meldinger.feilet")
            .replace("{postboks}", k.postboks) }));
          continue;
        }
        deler.push(meldingsliste(ctx, k, (svar && svar.meldinger) || [],
                                 !!(svar && svar.avkortet), hentDetalj,
                                 kanAdministrere, bekreftSlett));
      }
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

  // Slettingen av en melding er også enveis, og den fjerner noe eier
  // kanskje ville beholdt til fristen: teksten beskriver tilstanden
  // ETTERPÅ, som for kildedeaktiveringen.
  function bekreftSlett(m) {
    Bekreftelsesdialog({
      tittel: t("ui.epost.meldinger.slett_tittel"),
      tekst: t("ui.epost.meldinger.slett_tekst"),
      primarTekst: t("ui.epost.meldinger.slett"),
      farlig: true,
      rolle: "alertdialog",
      paaPrimar: () => slettMelding(m),
    });
  }

  function slettMelding(m) {
    slettEpostMelding(m.melding_id)
      .then(() => {
        meldLive(t("ui.epost.meldinger.slettet"));
        if (eierSkjermen()) tegn();
      })
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        meldLive(t("ui.epost.feilet"));
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
