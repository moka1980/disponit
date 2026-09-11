// M-6 PR-B: kildeflaten — postboksene bak e-postagenten. LITEN med
// vilje: liste over tilkoblede kilder (postboks, status, sist hentet),
// «Koble til M365» (åpner Microsofts authorize-URL som TOPPNIVÅ-
// navigasjon — OAuth-samtykket er en sidereise, aldri et XHR) og
// enveis deaktivering. Klassifiserings-/utkastsflaten er PR-D.
//
// TABELLEN ER TILGANGSFORMEN (m16-formen): ekte <table> med <caption>
// og th scope, status som TEKST (aldri kun farge). Forvaltnings-
// kontrollene vises KUN når økten bærer `epost:kilde:administrer`, og
// svarkontrollene KUN med `epost:utkast:behandle` —
// samme regel som wcagkontrolls faner: menyen/ruten gates av flatens
// svakeste ledd (`epost:read`), mutasjonene av sitt eget scope, og
// serveren håndhever begge uansett hva flaten viser.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentEpostKilder, startEpostKilde, deaktiverEpostKilde,
         hentEpostMeldinger, hentEpostMelding, slettEpostMelding,
         skrivSvarutkast, avgjorSvarutkast, settSvarIKo,
         nyIdempotensnokkel, UautorisertFeil, ApiFeil } from "../api.js";
import { Tidspunkt, TomTilstand, meldLive } from "../komponenter.js";
import { visningsToken, erGjeldendeVisning } from "../ruter.js";
import { Bekreftelsesdialog } from "../dialog.js";
import { medStatus, flateHode } from "./felles.js";

const ADMINSCOPE = "epost:kilde:administrer";
// SVARVEIEN HAR SITT EGET SCOPE (088), og det er ikke kildens.
// Flaten gatet hele svarseksjonen på `ADMINSCOPE` mens alle tre
// endepunktene bak den krever `epost:utkast:behandle` — så en økt med
// utkastscopet så INGEN svarkontroller den hadde lov til å bruke, og
// en økt med bare forvaltningsscopet så knapper som ga 403. Én av dem
// er en skjult funksjon, den andre er en løgn.
const UTKASTSCOPE = "epost:utkast:behandle";

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
  // EMNET FØRST (eiers merknad 11/9: «hele epost dashboardet er ikke
  // brukervennlig»). Emnet er det man leter etter i en innboks, og det
  // sto lengst til høyre — etter mottatt, avsender og et tomt felt.
  // `slettes` er ute av tabellen: den er en frist, ikke en innbokslinje,
  // og den står i den åpnede meldingen der den betyr noe.
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.epost.meldinger.kolonne.emne") }),
    el("th", { scope: "col", text: t("ui.epost.meldinger.kolonne.fra") }),
    el("th", { scope: "col", text: t("ui.epost.meldinger.kolonne.mottatt") }),
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
    // AVSENDEREN ER NAVNET NÅR DET FINNES. «M Eliassi
    // <eliassi@gmail.com>» er dobbelt opp; adressen står i meldingen.
    const fra = m.fra_navn || m.fra || "—";
    tbody.append(el("tr", {},
      el("th", { scope: "row" },
        el("span", { text: (m.emne || t("ui.epost.meldinger.uten_emne"))
          + (m.har_vedlegg ? " " + t("ui.epost.meldinger.vedlegg") : "") })),
      el("td", { text: fra, title: m.fra || "" }),
      el("td", {}, Tidspunkt(m.mottatt_ts, {})),
      el("td", {}, handlinger)));
  }
  tabell.append(tbody);
  // PANELET STÅR OVER LISTA, ikke under den. Under seksten rader måtte
  // eier bla forbi hele innboksen for å se meldingen han nettopp åpnet —
  // og skrivefeltet lå enda lenger ned. Over lista er den åpnede
  // meldingen det første man ser, og lista står igjen under som
  // navigasjon.
  boks.append(panel.node);
  if (meldinger.length) boks.append(tabell);
  if (avkortet) {
    boks.append(el("p", { class: "muted", text: t("ui.epost.meldinger.avkortet") }));
  }
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
// AKTØREN ER ET MENNESKE, ikke en økt-streng. Eier så «Klar til
// sending · token:sesjon:bid_c612864ad46e4063ad2bb4520ffc00be» over sitt
// eget svar. Bakenden lagrer nå bruker-ID-en (uten `token:`-prefiks),
// men gamle rader bærer den lange formen — og en id på trettifem tegn
// midt i en statuslinje er støy uansett hvor riktig den er.
export function kortAktor(a) {
  if (!a) return "";
  const uten = String(a).replace(/^token:sesjon:/, "").replace(/^token:/, "");
  return uten.length > 14 ? uten.slice(0, 14) + "…" : uten;
}

// Utkast man har ombestemt seg om er SPOR, ikke arbeid. De hører i en
// sammenklappet liste, som de slettede meldingene — ikke ved siden av
// det som venter på deg, med like store knapper.
const AVSLUTTEDE = ["forkastet", "brukt_manuelt", "sendt"];

function svarseksjon(m, kilde, kanBehandle, paaEndring) {
  const boks = el("section", {});
  const alle = m.utkast || [];
  const utkast = alle.filter((u) => !AVSLUTTEDE.includes(u.status));
  const avsluttede = alle.filter((u) => AVSLUTTEDE.includes(u.status));
  if (utkast.length) {
    boks.append(el("h4", { text: t("ui.epost.svar.tidligere") }));
    const liste = el("ul", {});
    for (const u of utkast) {
      const rad = el("li", {});
      const av = kortAktor(u.avgjort_av);
      rad.append(el("p", { class: "muted",
        text: t(`ui.epost.svar.status.${u.status}`)
          + (av ? " · " + av : "") }));
      rad.append(el("pre", { class: "epost-kropp",
        text: u.tekst || t("ui.epost.svar.uten_tekst") }));
      // SEND er menneskets egen handling — ingen godkjenningsrunde med
      // seg selv (eiervedtak 10/9, andre runde). Forkasting står ved
      // siden av, for det man ombestemte seg om.
      if (kanBehandle && ["foreslatt", "godkjent", "feilet"]
          .includes(u.status)) {
        const rad2 = el("div", { class: "knapperad" });
        // Send-knappen finnes bare når postboksen KAN sende
        // (CodeRabbit): en knapp som alltid feiler er en løgn om hva
        // systemet kan. Forkasting står uansett — et utkast man ikke
        // vil ha, skal kunne ryddes bort.
        if (!kilde || kilde.kan_svare !== false) {
          const send = el("button", { type: "button",
            text: t("ui.epost.svar.knapp.send") });
          send.addEventListener("click",
            () => paaEndring(() => settSvarIKo(u.utkast_id)));
          rad2.append(send);
        }
        const forkast = el("button", { class: "knapp fare", type: "button",
          text: t("ui.epost.svar.knapp.forkast") });
        forkast.addEventListener("click", () => paaEndring(
          () => avgjorSvarutkast(u.utkast_id, "forkastet")));
        rad2.append(forkast);
        rad.append(rad2);
      }
      if (u.status === "sendes") {
        // VENTETIDEN SIES (eiers merknad): fem minutter er ikke lenge,
        // men en side som ser ferdig ut mens ingenting har skjedd, er
        // verre enn å vente.
        rad.append(el("p", { class: "muted", role: "status",
          text: t("ui.epost.svar.i_koe") }));
      }
      if (u.status === "feilet" && u.feilgrunn) {
        rad.append(el("p", { role: "alert",
          text: t("ui.epost.svar.feilet_grunn").replace("{grunn}", u.feilgrunn) }));
      }
      liste.append(rad);
    }
    boks.append(liste);
  }
  if (avsluttede.length) {
    const d = el("details", {},
      el("summary", { text: t("ui.epost.svar.avsluttede")
        .replace("{n}", String(avsluttede.length)) }));
    const ul = el("ul", {});
    for (const u of avsluttede) {
      const av = kortAktor(u.avgjort_av);
      ul.append(el("li", {},
        el("p", { class: "muted",
          text: t(`ui.epost.svar.status.${u.status}`) + (av ? " · " + av : "") }),
        el("pre", { class: "epost-kropp",
          text: u.tekst || t("ui.epost.svar.uten_tekst") })));
    }
    d.append(ul);
    boks.append(d);
  }
  if (!kanBehandle) return boks;
  if (kilde && kilde.kan_svare === false) {
    boks.append(el("p", { class: "muted", text: t("ui.epost.svar.uten_tilgang") }));
    return boks;
  }
  const id = `svar-${m.melding_id}`;
  const felt = el("textarea", { id, rows: 5, maxlength: 32768 });
  // Å SENDE ER ETT KLIKK. Før måtte man skrive, trykke «Lagre utkast»,
  // bla ned til utkastlista og trykke «Send svaret» der — to trykk og en
  // rulling for det man gjør hver gang. Utkastet er fortsatt sannheten i
  // basen; knappen gjør bare begge stegene.
  const send = el("button", { class: "knapp primar", type: "submit",
    text: t("ui.epost.svar.knapp.send") });
  const lagre = el("button", { type: "button",
    text: t("ui.epost.svar.lagre") });
  const laas = (av) => { send.disabled = av; lagre.disabled = av; };
  const skriv = (ogsaaSend) => {
    if (!felt.value.trim() || send.disabled) return;
    // Låst mens kallet er i lufta (CodeRabbit): to raske trykk skal
    // ikke bli to utkast av samme svar.
    laas(true);
    Promise.resolve(paaEndring(async () => {
      const svar = await skrivSvarutkast(m.melding_id, felt.value);
      if (ogsaaSend && svar && svar.utkast_id) {
        await settSvarIKo(svar.utkast_id);
      }
      return svar;
    })).finally(() => laas(false));
  };
  const skjema = el("form", { class: "kv-skjema" },
    el("label", { for: id, text: t("ui.epost.svar.tittel") }), felt,
    // ÉN LINJE, ikke to avsnitt. Det som MÅ sies er ventetiden; at
    // svaret lagres som utkast ser man av knappen ved siden av.
    el("p", { class: "muted", text: t("ui.epost.svar.ventetid") }),
    el("div", { class: "knapperad" }, send, lagre));
  skjema.addEventListener("submit", (ev) => { ev.preventDefault(); skriv(true); });
  lagre.addEventListener("click", () => skriv(false));
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
    vis(m, kilde, kanBehandle, paaEndring) {
      const til = (m.til || []).join(", ");
      const raa = m.kropp || "";
      const ren = lesbarTekst(raa);
      const kropp = el("pre", { class: "epost-kropp",
        text: ren || t("ui.epost.meldinger.tom_kropp") });
      const deler = [
        el("h3", { text: m.emne || t("ui.epost.meldinger.uten_emne") }),
        el("p", { class: "muted", text: `${m.fra_navn ? m.fra_navn + " " : ""}<${m.fra || "—"}>`
          + (til ? ` → ${til}` : "") }),
        // TIDSPUNKTET OG FRISTEN PÅ ÉN LINJE. Fristen sto som egen
        // kolonne i innboksen, der den stjal plass fra emnet; her
        // betyr den noe, for det er her man leser meldingen og
        // bestemmer om den skal svares på før den ryddes bort.
        el("p", { class: "muted" }, Tidspunkt(m.mottatt_ts, {}),
          el("span", { text: " · " + t("ui.epost.meldinger.kolonne.slettes")
            .toLowerCase() + " " }),
          Tidspunkt(m.slettes_ts, {})),
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
        deler.push(svarseksjon(m, kilde, kanBehandle, paaEndring));
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
  const kanBehandleUtkast = (ctx.scopes || []).includes(UTKASTSCOPE);

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
            panel.vis(full, kilde, kanBehandleUtkast, paaEndring);
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
      // KOBLINGSSEKSJONEN ER SAMMENKLAPPET NÅR EN POSTBOKS ALT ER
      // TILKOBLET. Den er en engangshandling, og et helt avsnitt om
      // hvilke tilganger Microsoft blir bedt om hører ikke hjemme
      // nederst på en side man bruker hver dag (eiers merknad 11/9).
      // Har man INGEN aktiv kilde, er den åpen — da er den hele poenget.
      if (kanAdministrere) {
        const harAktiv = kilder.some((k) => k.status !== "deaktivert");
        deler.push(harAktiv
          ? el("details", {},
              el("summary", { text: t("ui.epost.koble_tittel") }),
              koblingsseksjon())
          : koblingsseksjon());
      }
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
