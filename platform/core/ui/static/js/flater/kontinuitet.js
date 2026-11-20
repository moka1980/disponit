// Kontinuitet (M-35 v1) — beredskapsflaten. Fire seksjoner: siste
// øvelse, tjenestekartet, beredskapskontaktene og hendelsene med
// tidslinjen sin.
//
// ÆRLIGE TALLNAVN (dom 5) er hele grunnen til at denne flaten er skrevet
// før øvelsen kan kjøres av seg selv: tallene fra backupskriptets
// statusfil heter «målt restore-tid» og «målt backupalder» i
// grensesnittet — ALDRI «RTO» og «RPO». Restore-tallet er en
// restore-til-isolert-base-PROXY; å kalle den RTO ville vært å love et
// menneske i en krise at tjenesten er tilbake om så mange sekunder, når
// det målte er noe langt smalere. Full tjeneste-RTO krever en
// selvrevers-øvelse (v2).
//
// INGEN ØVELSE ER TOM, IKKE NULL: `siste_ovelse: null` tegnes som
// setningen «ingen øvelse registrert», aldri som «restore-tid 0 s». Et
// nullstilt tall leses som en måling; en setning leses som fraværet den
// er (dom 4: aldri grønt uten evidens).
//
// TABELLEN ER TILGANGSFORMEN (m16-formen): ekte <table> med <caption>,
// th[scope=col] på kolonnene og th[scope=row] på cellen som navngir
// raden. Tilstand står som TEKST, aldri som farge alene.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, apneKontinuitetshendelse, bekreftBeredskapskontakt,
  hentJson, hentMedlemmer, hentPlaybooks, leggKontinuitetspost,
  lukkKontinuitetshendelse, nyIdempotensnokkel,
  oppdaterKontinuitetstjeneste, opprettBeredskapskontakt,
  opprettKontinuitetstjeneste,
} from "../api.js";
import { Tidspunkt } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, kvRad, medStatus } from "./felles.js";

// Bekreftelsesferskheten flaten LESER som fersk. Samme 90 døgn som
// øvelseslogikkens `MAKS_BEKREFTELSESALDER_S` og planens §4 — men her
// er den ren PRESENTASJON: dommen felles av øvelsen, flaten sier bare
// hva den ser, slik at et menneske kan handle før neste kjøring.
const FERSK_BEKREFTELSE_DOGN = 90;
const DOGN_MS = 86400000;

const POSTTYPER = ["observasjon", "tiltak", "statusendring", "etteranalyse"];
const ALVORSGRADER = ["kritisk", "alvorlig", "begrenset"];
const REFERENTTYPER = ["ekstern", "modul", "systemd_unit"];
const KRITIKALITETER = ["kritisk", "viktig", "normal"];

// Én nøkkel per intensjon (PR-014 R1): den nullstilles når innholdet
// endres, og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen, og et
// rettet skjema er en ny intensjon som skal ha sin egen.
function bindSkjema(skjema, knapp, ctx, utfall, kall, etter, tekst409) {
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await kall(idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409 ? t(tekst409)
          : t("ui.kontinuitet.skjema.feil") }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    sett(utfall, el("span", { text: t("ui.kontinuitet.skjema.ok") }));
    etter();
  });
}

function velger(id, verdier, tekstFor, valgt) {
  const v = el("select", { id, name: id });
  for (const x of verdier) {
    v.append(el("option", { value: x, text: tekstFor(x),
      selected: x === valgt }));
  }
  return v;
}

function playbookNavn(ref) {
  const kutt = ref.indexOf("@");
  return kutt > 0 ? ref.slice(0, kutt) : ref;
}

// Kartskjemaet — nytt innslag (`tj` er null) eller endring av ett som
// finnes. Identiteten (type og navn) er frosset i basen, så
// endringsskjemaet viser den ikke som felt.
//
// MÅLENE SKRIVES I MINUTTER og sendes i sekunder: ingen setter et mål
// for gjenoppretting i sekunder, og et felt som ba om det ville fått
// «60» fra den som mente en time.
function kartSkjema(tj, d, ctx, last) {
  const pre = tj ? `kart-${tj.tjeneste_id}` : "kart-ny";
  const skjema = el("form", { class: "kv-skjema" });
  const utfall = el("p", { "aria-live": "polite" });
  const felt = (id, etikett, kontroll) => [
    el("label", { for: `${pre}-${id}`, text: t(etikett) }), kontroll];
  const typeV = tj ? null : velger(`${pre}-type`, REFERENTTYPER,
    (x) => t(`ui.kontinuitet.referenttype.${x}`), "ekstern");
  const navnF = tj ? null : el("input", { id: `${pre}-navn`,
    name: "referent_id", type: "text", required: true, maxlength: 200 });
  const kritV = velger(`${pre}-krit`, KRITIKALITETER,
    (x) => t(`ui.kontinuitet.kritikalitet.${x}`),
    tj ? tj.kritikalitet : "viktig");
  const minutter = (id, sek) => el("input", { id: `${pre}-${id}`, name: id,
    type: "number", min: 1, max: 527040, step: 1, required: true,
    inputmode: "numeric",
    value: sek == null ? null : Math.max(1, Math.round(sek / 60)) });
  const rtoF = minutter("tid", tj ? tj.rto_maal_s : null);
  const rpoF = minutter("tap", tj ? tj.rpo_maal_s : null);
  const gjeldende = tj ? playbookNavn(tj.playbook_ref) : null;
  const pbV = velger(`${pre}-pb`, d.playbooks.map((p) => p.navn),
    (n) => d.playbooks.find((p) => p.navn === n).tittel, gjeldende);
  const pbNaar = el("p", { class: "muted" });
  const visNaar = () => {
    const p = d.playbooks.find((x) => x.navn === pbV.value);
    pbNaar.textContent = p ? p.naar : "";
  };
  pbV.addEventListener("change", visNaar);
  visNaar();
  const rolleF = el("input", { id: `${pre}-rolle`, name: "kontaktrolle",
    type: "text", required: true, maxlength: 100,
    value: tj ? tj.kontaktrolle : null });
  const knapp = el("button", { type: "submit", class: "knapp primar",
    text: t(tj ? "ui.kontinuitet.kart.lagre" : "ui.kontinuitet.kart.legg_til") });
  if (!tj) {
    skjema.append(
      ...felt("type", "ui.kontinuitet.kart.felt.type", typeV),
      ...felt("navn", "ui.kontinuitet.kart.felt.navn", navnF),
      el("p", { class: "muted", text: t("ui.kontinuitet.kart.navnhjelp") }));
  }
  skjema.append(
    ...felt("krit", "ui.kontinuitet.kolonne.kritikalitet", kritV),
    ...felt("tid", "ui.kontinuitet.kart.felt.tid", rtoF),
    ...felt("tap", "ui.kontinuitet.kart.felt.tap", rpoF),
    ...felt("pb", "ui.kontinuitet.kolonne.playbook", pbV), pbNaar,
    ...felt("rolle", "ui.kontinuitet.kolonne.kontaktrolle", rolleF),
    el("p", { class: "muted", text: t("ui.kontinuitet.kart.rollehjelp") }),
    knapp);
  bindSkjema(skjema, knapp, ctx, utfall, (idem) => {
    const felles = {
      kritikalitet: kritV.value,
      rto_maal_s: Math.round(Number(rtoF.value) * 60),
      rpo_maal_s: Math.round(Number(rpoF.value) * 60),
      playbook: pbV.value, kontaktrolle: rolleF.value.trim(),
    };
    return tj
      ? oppdaterKontinuitetstjeneste(tj.tjeneste_id, felles, idem)
      : opprettKontinuitetstjeneste({ ...felles,
        referent_type: typeV.value, referent_id: navnF.value.trim() }, idem);
  }, last, "ui.kontinuitet.kart.finnes");
  return [skjema, utfall];
}

function kontaktSkjema(d, ctx, last) {
  const skjema = el("form", { class: "kv-skjema" });
  const utfall = el("p", { "aria-live": "polite" });
  const rolleF = el("input", { id: "kontakt-rolle", name: "rolle",
    type: "text", required: true, maxlength: 100 });
  const prioF = el("input", { id: "kontakt-prioritet", name: "prioritet",
    type: "number", min: 1, max: 9, step: 1, required: true, value: 1,
    inputmode: "numeric" });
  const personV = velger("kontakt-person",
    d.medlemmer.map((m) => m.bruker_id),
    (id) => { const m = d.medlemmer.find((x) => x.bruker_id === id);
      return m.navn || m.bruker_id; }, null);
  const knapp = el("button", { type: "submit", class: "knapp primar",
    text: t("ui.kontinuitet.kontakter.legg_til") });
  skjema.append(
    el("label", { for: "kontakt-rolle",
      text: t("ui.kontinuitet.kolonne.rolle") }), rolleF,
    el("p", { class: "muted", text: t("ui.kontinuitet.kontakter.rollehjelp") }),
    el("label", { for: "kontakt-prioritet",
      text: t("ui.kontinuitet.kolonne.prioritet") }), prioF,
    el("label", { for: "kontakt-person",
      text: t("ui.kontinuitet.kolonne.person") }), personV,
    knapp);
  bindSkjema(skjema, knapp, ctx, utfall,
    (idem) => opprettBeredskapskontakt(rolleF.value.trim(),
      Number(prioF.value), personV.value, idem),
    last, "ui.kontinuitet.kontakter.opptatt");
  return [skjema, utfall];
}

function tekstEller(verdi, nokkel) {
  return verdi == null ? t(nokkel) : String(verdi);
}

// ---------------------------------------------------------------------
// Seksjon 1: siste øvelse
// ---------------------------------------------------------------------
function ovelseSeksjon(ovelse) {
  const s = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.kontinuitet.ovelse.tittel") }));
  if (!ovelse) {
    s.append(el("p", { class: "muted",
      text: t("ui.kontinuitet.ovelse.ingen") }));
    return s;
  }
  const dl = el("dl", { class: "kv-liste" });
  // Navnene er tallenes egne. Forklaringen står i undertittelen på
  // hvert felt, ikke i en tooltip: den som leser en beredskapsside skal
  // ikke måtte holde musepekeren stille for å vite hva tallet ikke er.
  kvRad(dl, t("ui.kontinuitet.ovelse.restoretid"),
    ovelse.maalt_restoretid_s == null
      ? t("ui.kontinuitet.ovelse.uten_evidens")
      : t("ui.kontinuitet.ovelse.sekunder")
        .replace("{n}", String(ovelse.maalt_restoretid_s)));
  kvRad(dl, t("ui.kontinuitet.ovelse.backupalder"),
    ovelse.maalt_backupalder_s == null
      ? t("ui.kontinuitet.ovelse.uten_evidens")
      : t("ui.kontinuitet.ovelse.sekunder")
        .replace("{n}", String(ovelse.maalt_backupalder_s)));
  kvRad(dl, t("ui.kontinuitet.ovelse.restore_verifisert"),
    ovelse.restore_verifisert
      ? t("ui.kontinuitet.ja") : t("ui.kontinuitet.nei"));
  kvRad(dl, t("ui.kontinuitet.ovelse.live"),
    ovelse.live_helse_ok
      ? t("ui.kontinuitet.ja") : t("ui.kontinuitet.nei"));
  kvRad(dl, t("ui.kontinuitet.ovelse.gronn_alder"),
    ovelse.siste_gronne_alder_dogn == null
      ? t("ui.kontinuitet.ovelse.ingen_gronn")
      : t("ui.kontinuitet.ovelse.dogn")
        .replace("{n}", String(ovelse.siste_gronne_alder_dogn)));
  s.append(dl, el("p", { class: "muted",
    text: t("ui.kontinuitet.ovelse.proxynotat") }));
  if (Array.isArray(ovelse.funn) && ovelse.funn.length) {
    const ul = el("ul");
    for (const f of ovelse.funn) {
      // Funnet er en TEKSTNØKKEL, aldri fritekst (samme regel som
      // hendelseshodet) — alvoret står som ord ved siden av.
      ul.append(el("li", { text: `${t(`ui.kontinuitet.alvor.${f.alvor}`)}: `
        + t(`ui.${f.tekstnokkel}`) }));
    }
    s.append(el("h3", { text: t("ui.kontinuitet.ovelse.funn") }), ul);
  }
  return s;
}

// ---------------------------------------------------------------------
// Seksjon 2: tjenestekartet
// ---------------------------------------------------------------------
function kartSeksjon(tjenester, d, ctx, last) {
  const s = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.kontinuitet.kart.tittel") }));
  // Skjemaene krever skriverett OG en katalog å velge fra: uten
  // playbooker ville skjemaet hatt en tom, påkrevd nedtrekksliste.
  const kanSkrive = harScope(ctx, "kontinuitet:write")
    && d.playbooks && d.playbooks.length > 0;
  const nytt = () => {
    if (!kanSkrive) return;
    s.append(el("div", { class: "skjemaboks" },
      el("h3", { text: t("ui.kontinuitet.kart.ny_tittel") }),
      ...kartSkjema(null, d, ctx, last)));
  };
  if (!tjenester.length) {
    s.append(el("p", { class: "muted", text: t("ui.kontinuitet.kart.ingen") }));
    nytt();
    return s;
  }
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kontinuitet.kart.caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.referent") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.kritikalitet") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.rto_maal") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.rpo_maal") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.playbook") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.kontaktrolle") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.oppdatert") }))));
  const tbody = el("tbody");
  for (const tj of tjenester) {
    // Playbook-referansen er navn@sha256: navnet er lesbart, hashen står
    // i `title`. Kortformen er PRESENTASJON — bindingen verifiseres av
    // deployporten mot repo-YAML-en, aldri av øyet her.
    const pbNavn = playbookNavn(tj.playbook_ref);
    // BINDINGEN SOM ORD: katalogen er releasens. Peker raden på andre
    // bytes enn dem som ligger der nå, er playbooken endret siden den
    // ble valgt — og det skal stå, ikke måtte leses ut av en hash.
    let binding = null;
    if (d.playbooks) {
      const p = d.playbooks.find((x) => x.navn === pbNavn);
      if (!p) binding = t("ui.kontinuitet.kart.playbook_ukjent");
      else if (p.ref !== tj.playbook_ref) {
        binding = t("ui.kontinuitet.kart.playbook_utdatert");
      }
    }
    tbody.append(el("tr", {},
      el("th", { scope: "row" },
        el("span", { text: `${tj.referent_type}: ${tj.referent_id}` })),
      el("td", { text: t(`ui.kontinuitet.kritikalitet.${tj.kritikalitet}`) }),
      el("td", { text: t("ui.kontinuitet.ovelse.sekunder")
        .replace("{n}", String(tj.rto_maal_s)) }),
      el("td", { text: t("ui.kontinuitet.ovelse.sekunder")
        .replace("{n}", String(tj.rpo_maal_s)) }),
      el("td", {}, el("code", { title: tj.playbook_ref, text: pbNavn }),
        binding ? ` — ${binding}` : null),
      el("td", { text: tj.kontaktrolle }),
      el("td", {}, Tidspunkt(tj.oppdatert, {}))));
  }
  tabell.append(tbody);
  s.append(tabell);
  if (kanSkrive) {
    for (const tj of tjenester) {
      s.append(el("details", {},
        el("summary", { text: t("ui.kontinuitet.kart.endre")
          .replace("{navn}", tj.referent_id) }),
        el("div", { class: "skjemaboks" }, ...kartSkjema(tj, d, ctx, last))));
    }
  }
  nytt();
  return s;
}

// ---------------------------------------------------------------------
// Seksjon 3: beredskapskontaktene
// ---------------------------------------------------------------------
function kontaktSeksjon(kontakter, naa, d, ctx, last) {
  const s = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.kontinuitet.kontakter.tittel") }));
  const kanSkrive = harScope(ctx, "kontinuitet:write");
  const utfall = el("p", { "aria-live": "polite" });
  const navn = (id) => {
    const m = (d.medlemmer || []).find((x) => x.bruker_id === id);
    return m && m.navn ? m.navn : id;
  };
  const nytt = () => {
    // Personen velges blant firmaets medlemmer — uten lista finnes det
    // ingen å velge, og et fritekstfelt for en bruker-id hjelper ingen.
    if (!kanSkrive || !d.medlemmer || !d.medlemmer.length) return;
    s.append(el("div", { class: "skjemaboks" },
      el("h3", { text: t("ui.kontinuitet.kontakter.ny_tittel") }),
      ...kontaktSkjema(d, ctx, last)));
  };
  if (!kontakter.length) {
    s.append(el("p", { class: "muted",
      text: t("ui.kontinuitet.kontakter.ingen") }));
    nytt();
    return s;
  }
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.kontinuitet.kontakter.caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.rolle") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.prioritet") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.person") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.bekreftet") }),
    el("th", { scope: "col", text: t("ui.kontinuitet.kolonne.dekning") }),
    kanSkrive ? el("th", { scope: "col",
      text: t("ui.kontinuitet.kolonne.handling") }) : null)));
  const tbody = el("tbody");
  for (const k of kontakter) {
    // Dekningen som ORD, ikke som farge (m16-regelen): «ubekreftet» og
    // «foreldet» er to ulike hull, og en fargeblind vaktleder skal se
    // forskjellen.
    let dekning;
    if (k.bekreftet == null) {
      dekning = t("ui.kontinuitet.kontakter.ubekreftet");
    } else {
      const alder = (naa - Date.parse(k.bekreftet)) / DOGN_MS;
      dekning = alder > FERSK_BEKREFTELSE_DOGN
        ? t("ui.kontinuitet.kontakter.foreldet")
        : t("ui.kontinuitet.kontakter.fersk");
    }
    tbody.append(el("tr", {},
      el("th", { scope: "row" }, el("span", { text: k.rolle })),
      el("td", { text: String(k.prioritet) }),
      el("td", { text: navn(k.bruker_id) }),
      el("td", {}, k.bekreftet == null
        ? t("ui.kontinuitet.kontakter.aldri")
        : Tidspunkt(k.bekreftet, {})),
      el("td", { text: dekning }),
      kanSkrive ? el("td", {}, bekreftKnapp(k)) : null));
  }
  tabell.append(tbody);
  s.append(tabell, utfall);
  nytt();
  return s;

  // Bekreftelsen er en egen, målt handling: den sier at personen
  // fortsatt har rollen og kan nås. Knappen står på HVER rad, også de
  // ferske — å bekrefte på nytt er lovlig og er veien til dekning.
  function bekreftKnapp(k) {
    const knapp = el("button", { type: "button", class: "knapp",
      text: t("ui.kontinuitet.kontakter.bekreft"),
      "aria-label": t("ui.kontinuitet.kontakter.bekreft_hvem")
        .replace("{rolle}", k.rolle).replace("{navn}", navn(k.bruker_id)) });
    knapp.addEventListener("click", async () => {
      if (knapp.disabled) return;
      knapp.disabled = true;
      try {
        await bekreftBeredskapskontakt(k.kontakt_id);
      } catch (e) {
        knapp.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert",
          text: t("ui.kontinuitet.skjema.feil") }));
        return;
      }
      last();
    });
    return knapp;
  }
}

// ---------------------------------------------------------------------
// Seksjon 4: hendelsene, tidslinjen og skjemaene
// ---------------------------------------------------------------------
function tidslinje(poster) {
  const ol = el("ol", { class: "tidslinje" });
  for (const p of poster) {
    ol.append(el("li", {},
      Tidspunkt(p.ts, {}), " — ",
      el("strong", { text: t(`ui.kontinuitet.posttype.${p.posttype}`) }),
      " — ", el("span", { text: p.aktor }), ": ",
      el("span", { text: p.tekst })));
  }
  return ol;
}

// Ett skjema per åpen hendelse: en post, og lukkingen. Begge er bak
// write-scopet — og begge lar SERVEREN felle dommen. Særlig lukkingen:
// flaten teller ikke etteranalyse-poster for å skjule knappen, den
// sender og viser dørens svar. Da kan de to aldri komme i utakt.
function hendelsesskjema(h, ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const utfall = el("p", { "aria-live": "polite" });

  const postSkjema = el("form", { class: "kv-skjema" });
  const postId = `post-${h.hendelse_id}`;
  const typeVelger = el("select", { id: `${postId}-type`, name: "posttype" });
  for (const pt of POSTTYPER) {
    typeVelger.append(el("option", { value: pt,
      text: t(`ui.kontinuitet.posttype.${pt}`) }));
  }
  const tekstFelt = el("textarea",
    { id: `${postId}-tekst`, name: "tekst", rows: 2, required: true });
  const leggKnapp = el("button", { type: "submit",
    text: t("ui.kontinuitet.skjema.legg_til") });
  postSkjema.append(
    el("label", { for: `${postId}-type`,
      text: t("ui.kontinuitet.skjema.posttype") }), typeVelger,
    el("label", { for: `${postId}-tekst`,
      text: t("ui.kontinuitet.skjema.tekst") }), tekstFelt,
    leggKnapp);

  const lukkSkjema = el("form", { class: "kv-skjema" });
  const lukkTekst = el("textarea",
    { id: `lukk-${h.hendelse_id}`, name: "tekst", rows: 2, required: true });
  const lukkKnapp = el("button", { type: "submit",
    text: t("ui.kontinuitet.skjema.lukk") });
  lukkSkjema.append(
    el("label", { for: `lukk-${h.hendelse_id}`,
      text: t("ui.kontinuitet.skjema.lukk_tekst") }), lukkTekst,
    lukkKnapp,
    el("p", { class: "muted", text: t("ui.kontinuitet.skjema.lukk_krav") }));

  // Én nøkkel per intensjon (PR-014 R1): den nullstilles når innholdet
  // endres, og ved 4xx — et avvist forsøk har FORBRUKT nøkkelen, og en
  // rettet tekst er en ny intensjon som skal ha sin egen.
  function bind(skjema, knapp, felt, kall) {
    let idem = null;
    skjema.addEventListener("input", () => { idem = null; });
    skjema.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (knapp.disabled) return;
      knapp.disabled = true;
      if (!idem) idem = nyIdempotensnokkel();
      try {
        await kall(idem);
      } catch (e) {
        knapp.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (e && e.status >= 400 && e.status < 500) idem = null;
        // Dørens 409 er TILSTANDEN som sier nei — den vanligste er
        // lukking uten etteranalyse, og teksten sier nettopp det i
        // stedet for et generisk «noe gikk galt».
        sett(utfall, el("span", { role: "alert",
          text: e && e.status === 409
            ? t("ui.kontinuitet.skjema.tilstand_nei")
            : t("ui.kontinuitet.skjema.feil") }));
        return;
      }
      felt.value = "";
      idem = null;
      knapp.disabled = false;
      sett(utfall, el("span", { text: t("ui.kontinuitet.skjema.ok") }));
      last();
    });
  }
  bind(postSkjema, leggKnapp, tekstFelt,
    (idem) => leggKontinuitetspost(h.hendelse_id, typeVelger.value,
      tekstFelt.value, idem));
  bind(lukkSkjema, lukkKnapp, lukkTekst,
    (idem) => lukkKontinuitetshendelse(h.hendelse_id, lukkTekst.value, idem));

  boks.append(
    el("h4", { text: t("ui.kontinuitet.skjema.post_tittel") }), postSkjema,
    el("h4", { text: t("ui.kontinuitet.skjema.lukk_tittel") }), lukkSkjema,
    utfall);
  return boks;
}

function apneSkjema(ctx, last) {
  const boks = el("div", { class: "skjemaboks" });
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema" });
  const nokkelFelt = el("input",
    { id: "ny-tekstnokkel", name: "tekstnokkel", type: "text", required: true });
  const alvorVelger = el("select", { id: "ny-alvor", name: "alvor" });
  for (const a of ALVORSGRADER) {
    alvorVelger.append(el("option", { value: a,
      text: t(`ui.kontinuitet.alvor.${a}`) }));
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.kontinuitet.skjema.apne") });
  skjema.append(
    // Hodet bærer en TEKSTNØKKEL, aldri fritekst: det som skjedde hører
    // tidslinjen til, der en aktør står ved det. Hjelpeteksten sier det,
    // så ingen prøver å skrive en fortelling i feltet.
    el("label", { for: "ny-tekstnokkel",
      text: t("ui.kontinuitet.skjema.tekstnokkel") }), nokkelFelt,
    el("p", { class: "muted", text: t("ui.kontinuitet.skjema.nokkelhjelp") }),
    el("label", { for: "ny-alvor",
      text: t("ui.kontinuitet.skjema.alvor") }), alvorVelger,
    knapp);
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await apneKontinuitetshendelse(nokkelFelt.value, alvorVelger.value,
        {}, idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      sett(utfall, el("span", { role: "alert",
        text: t("ui.kontinuitet.skjema.feil") }));
      return;
    }
    nokkelFelt.value = "";
    idem = null;
    knapp.disabled = false;
    sett(utfall, el("span", { text: t("ui.kontinuitet.skjema.ok") }));
    last();
  });
  boks.append(el("h3", { text: t("ui.kontinuitet.skjema.apne_tittel") }),
    skjema, utfall);
  return boks;
}

function hendelseSeksjon(hendelser, ctx, last) {
  const s = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.kontinuitet.hendelser.tittel") }));
  const kanSkrive = harScope(ctx, "kontinuitet:write");
  if (kanSkrive) s.append(apneSkjema(ctx, last));
  if (!hendelser.length) {
    s.append(el("p", { class: "muted",
      text: t("ui.kontinuitet.hendelser.ingen") }));
    return s;
  }
  for (const h of hendelser) {
    const apen = h.lukket == null;
    const art = el("article", { class: "kpi-kort" },
      // Hodets tekstnøkkel er KUNDENS: en kjent nøkkel oversettes, en
      // ukjent vises RÅ (reserven) — aldri som en halvoversatt
      // «ui.kontinuitet...»-streng et menneske må dekode midt i en
      // krise.
      el("h3", { text: t(`ui.kontinuitet.hendelse.nokkel.${h.tekstnokkel}`,
        h.tekstnokkel) }));
    const dl = el("dl", { class: "kv-liste" });
    kvRad(dl, t("ui.kontinuitet.hendelse.alvor"),
      t(`ui.kontinuitet.alvor.${h.alvor}`));
    // Tilstanden som ORD — «åpen»/«lukket», aldri bare en farge eller
    // et fravær av dato.
    kvRad(dl, t("ui.kontinuitet.hendelse.tilstand"),
      apen ? t("ui.kontinuitet.hendelse.apen")
        : t("ui.kontinuitet.hendelse.lukket"));
    kvRad(dl, t("ui.kontinuitet.hendelse.apnet"), Tidspunkt(h.apnet, {}));
    kvRad(dl, t("ui.kontinuitet.hendelse.apnet_av"),
      tekstEller(h.apnet_av, "ui.kontinuitet.ukjent"));
    if (!apen) {
      kvRad(dl, t("ui.kontinuitet.hendelse.lukket_ts"), Tidspunkt(h.lukket, {}));
      kvRad(dl, t("ui.kontinuitet.hendelse.lukket_av"),
        tekstEller(h.lukket_av, "ui.kontinuitet.ukjent"));
    }
    art.append(dl,
      el("h4", { text: t("ui.kontinuitet.hendelse.tidslinje") }),
      tidslinje(h.tidslinje));
    // Skjemaene bare på ÅPNE hendelser: en lukket tidslinje tar ikke
    // imot flere poster (vakten avviser det uansett), og en knapp som
    // alltid feiler er en løgn om hva systemet kan.
    if (apen && kanSkrive) art.append(hendelsesskjema(h, ctx, last));
    s.append(art);
  }
  return s;
}

export function visKontinuitet(hoved, ctx) {
  const hode = () => flateHode(t("ui.kontinuitet.tittel"),
    t("ui.kontinuitet.undertittel"));
  sett(hoved, ...hode());
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  // Katalogen og medlemslista er HJELPEDATA: faller en av dem, tegnes
  // flaten likevel — uten skjemaet som trengte den, og med bruker-id i
  // stedet for navn. Beredskapstilstanden skal aldri vente på en
  // nedtrekksliste.
  const hent = async () => {
    const [d, pb, medl] = await Promise.all([
      hentJson("/v1/kontinuitet"),
      hentPlaybooks().catch(() => null),
      hentMedlemmer().catch(() => null)]);
    return { ...d,
      playbooks: pb && Array.isArray(pb.playbooks) ? pb.playbooks : null,
      medlemmer: medl && Array.isArray(medl.medlemmer) ? medl.medlemmer : null };
  };
  const last = () => medStatus(hoved, ctx, hent,
    (d) => {
      sett(hoved, ...hode(), kropp);
      const naa = Date.now();
      sett(kropp,
        ovelseSeksjon(d.siste_ovelse),
        kartSeksjon(d.tjenester || [], d, ctx, last),
        kontaktSeksjon(d.kontakter || [], naa, d, ctx, last),
        hendelseSeksjon(d.hendelser || [], ctx, last));
    });
  last();
}
