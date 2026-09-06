// M-29 sikkerhets- og hendelsesagent (137) — KORRELASJONEN ER PRODUKTET.
//
// FLATENS VIKTIGSTE JOBB ER Å VISE HVA MODULEN IKKE GJORDE.
//
// Klyngens delte dom: EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN
// ANGRES IKKE AV EN ROLLBACK. Kontoen er stengt, hemmeligheten er
// rullet, og tokenet den gamle klienten holdt er dødt — databasen kan
// rulles tilbake til sekundet før, og klienten er fortsatt logget ut.
//
// DERFOR STÅR «INGEN INNGREP UTFØRT» I SAMMENDRAGET, ALLTID. Tallet er
// ikke en telling av en kolonne — det er en påstand om at kolonnen
// ikke finnes. `inngrepsforslag` har ingen `utfort_ts`, ingen
// `resultat` og ingen `status`.
//
// EN SCORE VISES ALDRI UTEN REGELEN SOM GA DEN. En score uten en
// lesbar forklaring er en påstand, og «forklarbare regler» er
// vaktsetningens eget ord.
//
// ALVORET VISES SLIK DET STO DA. Terskelen kan ha endret seg siden;
// hendelsen skal ikke skifte karakter av det, og kravversjonen den ble
// scoret mot er uforanderlig.
//
// EN PLAYBOOK VISES MED STEGENE SINE, I REKKEFØLGE. Det er ikke en
// utførelsesplan — det er en liste noen har skrevet ned på forhånd, og
// v1 utfører den ikke. Stegene er NAVN fra et lukket sett, og det
// finnes ikke noe felt et argument kunne ligget i.
//
// DET FINNES INGEN «ISOLER KONTO»- ELLER «ROTER NØKKEL»-KNAPP, OG DET
// KAN IKKE FINNES. Modulen korrelerer og foreslår; den handler ikke.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  avviklSikkerhetsregel, foreslaaInngrep, hentJson,
  korrelerHendelse, lukkHendelsesfunn, lukkSikkerhetshendelse,
  nyIdempotensnokkel, registrerPlaybook, registrerSikkerhetsregel,
  settHendelseskrav, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

// SIGNALTYPENE — ET LUKKET SETT OVER DET BASEN FAKTISK KAN SE.
//
// Katalogen lover SIEM, IdP, EDR og skanner. Ingen av dem finnes, og
// ingen kan finnes uten en utgående integrasjon huset ikke har. Alle
// seks under leses av `revisjonslogg`.
export const SIGNALTYPER = [
  "policy_avslag_gjentatt", "unntak_gjentatt",
  "handling_utenfor_tidsvindu", "aktor_ukjent_for_tenant",
  "beslutning_uten_policyhash", "revisjonshull",
];

// STEGTYPENE — NAVN, IKKE KOMMANDOER.
//
// LEGG MERKE TIL AT DET IKKE FINNES EN `annet`-VERDI. Et lukket sett
// med en åpen dør er et åpent sett.
export const STEGTYPER = [
  "varsle_sikkerhetsansvarlig", "varsle_daglig_leder",
  "samle_tidslinje", "kartlegg_beroerte_data",
  "isoler_konto", "isoler_token", "roter_hemmelighet",
  "tilbakestill_sesjoner", "verifiser_gjenoppretting",
  "skriv_laeringsregel",
];

const SIGNALTEKST = {
  policy_avslag_gjentatt: "ui.hendelse.signal_policy",
  unntak_gjentatt: "ui.hendelse.signal_unntak",
  handling_utenfor_tidsvindu: "ui.hendelse.signal_tidsvindu",
  aktor_ukjent_for_tenant: "ui.hendelse.signal_ukjent",
  beslutning_uten_policyhash: "ui.hendelse.signal_uten_hash",
  revisjonshull: "ui.hendelse.signal_hull",
};

const STEGTEKST = {
  varsle_sikkerhetsansvarlig: "ui.hendelse.steg_varsle_sikkerhet",
  varsle_daglig_leder: "ui.hendelse.steg_varsle_leder",
  samle_tidslinje: "ui.hendelse.steg_tidslinje",
  kartlegg_beroerte_data: "ui.hendelse.steg_kartlegg",
  isoler_konto: "ui.hendelse.steg_isoler_konto",
  isoler_token: "ui.hendelse.steg_isoler_token",
  roter_hemmelighet: "ui.hendelse.steg_roter",
  tilbakestill_sesjoner: "ui.hendelse.steg_sesjoner",
  verifiser_gjenoppretting: "ui.hendelse.steg_verifiser",
  skriv_laeringsregel: "ui.hendelse.steg_laering",
};

const ALVORTEKST = {
  over_terskel: "ui.hendelse.alvor_over",
  under_terskel: "ui.hendelse.alvor_under",
};

const FUNNTEKST = {
  apen_hendelse_over_frist: "ui.hendelse.funn_over_frist",
  hendelse_uten_forslag: "ui.hendelse.funn_uten_forslag",
  regel_uten_treff: "ui.hendelse.funn_regel_uten_treff",
  playbook_uten_steg: "ui.hendelse.funn_playbook_tom",
  signaltak_naadd: "ui.hendelse.funn_signaltak",
  krav_mangler: "ui.hendelse.funn_krav_mangler",
  inngrep_uten_playbook: "ui.hendelse.funn_uten_playbook",
  fri_kommando_kjort: "ui.hendelse.funn_fri_kommando",
  hendelse_uten_score: "ui.hendelse.funn_uten_score",
  score_uten_regel: "ui.hendelse.funn_uten_regel",
};


export function dato(iso) {
  if (typeof iso !== "string" || !iso) return "–";
  return iso.slice(0, 16).replace("T", " ");
}


// DAGENS DATO I BRUKERENS EGEN SONE, IKKE I UTC.
//
// `new Date().toISOString().slice(0, 10)` gir UTC-datoen. Norge ligger
// FORAN UTC, så mellom midnatt og 01/02 om natten gir den GÅRSDAGEN —
// og en regel registrert «i dag» ville da blitt forsøkt avviklet dagen
// FØR den gjaldt. Døra nekter det, med rette, og brukeren ville sett
// en uforklarlig feil som forsvant om morgenen.
//
// Arvet fra 133/135, der CodeRabbit fant den 5/9.
export function iDagLokal(naa) {
  const d = naa || new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}


// SCOREN MED FORKLARINGEN SIN, ALDRI ALENE.
//
// Et tall uten regelen bak er en påstand. `regel` og `signaltype` står
// derfor i samme streng som scoren.
export function scoretekst(rad) {
  if (!rad || typeof rad.score !== "number") return "–";
  const navn = rad.regel || "–";
  return t("ui.hendelse.score_verdi")
    .replace("{n}", String(rad.score))
    .replace("{regel}", navn);
}


function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
}


function velger(id, verdier, tekster) {
  const s = el("select", { id, name: id });
  for (const v of verdier) {
    s.append(el("option", { value: v, text: t(tekster[v]) }));
  }
  return s;
}


// KNAPPEN OG LYTTEREN I ETT (127s lærdom).
function knappMed(tekst, ved) {
  const b = el("button", { type: "button", text: tekst });
  b.addEventListener("click", ved);
  return b;
}


function skjemaramme(ctx, last, { skjema, knapp, utfall, send,
                                 tilbakestill, okNokkel, kvitter }) {
  let idem = null;
  skjema.addEventListener("input", () => { idem = null; });
  skjema.addEventListener("change", () => { idem = null; });
  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (knapp.disabled) return;
    knapp.disabled = true;
    if (!idem) idem = nyIdempotensnokkel();
    try {
      await send(idem);
    } catch (e) {
      knapp.disabled = false;
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e && e.status >= 400 && e.status < 500) idem = null;
      // EN SPESIFIKK MELDING SKAL NÅ FRAM.
      //
      // `korrelasjonspanel` kaster `FeilformetFeil` med «ingen
      // kandidatrader i vinduet» — som er et SVAR, ikke en feil. Med
      // bare den generelle teksten ville brukeren fått «noe gikk galt»
      // og prøvd igjen i det uendelige.
      //
      // En `status` betyr at feilen kom fra tjeneren; da er dens egen
      // tekst ikke vår å vise. En feil UTEN status er en vi selv
      // kastet, og den bærer ordene vi valgte.
      const egen = e && !e.status && typeof e.message === "string"
        && e.message ? e.message : null;
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.hendelse.feil.tilstand")
          : (egen || t("ui.hendelse.feil.generell")) }));
      return;
    }
    idem = null;
    knapp.disabled = false;
    if (tilbakestill) tilbakestill();
    meldLive(t(okNokkel));
    kvitter(t(okNokkel));
    await last();
  });
}


// SAMMENDRAGET. DEN VIKTIGSTE LINJEN ER DEN SOM ALLTID SIER NULL.
//
// «Ingen inngrep utført» står her hver eneste gang, og det er ikke
// støy — det er modulens dom, gjort synlig. Et menneske som leser
// flaten skal ikke måtte anta at maskinen holdt seg i ro; hun skal se
// det.
//
// Deretter hendelsene over terskel UTEN et forslag: modulen kan ikke
// gjøre noe med dem, og da er det å SI FRA hele det den kan.
export function sammendrag(s) {
  const p = el("p", {});
  if (s.over_terskel > 0) {
    p.append(el("strong", { role: "alert",
      text: t("ui.hendelse.over_terskel")
        .replace("{n}", String(s.over_terskel)) }), " ");
  }
  if (s.apne_hendelser > 0 && s.forslag === 0) {
    p.append(el("strong", { role: "alert",
      text: t("ui.hendelse.uten_forslag")
        .replace("{n}", String(s.apne_hendelser)) }), " ");
  }
  p.append(el("span", {
    text: t("ui.hendelse.sammendrag")
      .replace("{apne}", String(s.apne_hendelser ?? 0))
      .replace("{regler}", String(s.regler ?? 0))
      .replace("{playbooker}", String(s.playbooker ?? 0))
      .replace("{forslag}", String(s.forslag ?? 0))
      .replace("{funn}", String(s.apne_funn ?? 0)) }));
  // LINJEN SOM ALLTID SIER NULL.
  p.append(" ", el("strong", {
    class: "utfort-null",
    text: t("ui.hendelse.ingen_inngrep")
      .replace("{n}", String(s.inngrep_utfort ?? 0)) }));
  return p;
}


function tabell(kolonner, rader) {
  const thead = el("thead", {}, el("tr", {},
    ...kolonner.map((k) => el("th", { scope: "col", text: t(k) }))));
  const tbody = el("tbody", {});
  for (const r of rader) tbody.append(r);
  return el("table", { class: "tabell" }, thead, tbody);
}


// HENDELSESTABELLEN. SCOREN, REGELEN OG ALVORET I SAMME RAD.
export function hendelsesrader(data, ctx, last) {
  const ut = [];
  for (const h of data.hendelser || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: dato(h.oppdaget_ts) }));
    tr.append(el("td", { text: scoretekst(h) }));
    // ALVORET SLIK DET STO DA — ikke regnet på nytt mot dagens
    // terskel. En hendelse som var over terskel da noen så på den, var
    // det.
    tr.append(el("td", {},
      el("span", {
        class: h.alvor === "over_terskel" ? "varsel" : "muted",
        text: t(ALVORTEKST[h.alvor] || "ui.hendelse.alvor_ukjent") })));
    tr.append(el("td", { text: String(h.signaler ?? 0) }));
    // FORSLAG: NULL PÅ EN HENDELSE OVER TERSKEL ER SVEIPENS VIKTIGSTE
    // FUNN.
    const forslag = el("td", {});
    if (h.alvor === "over_terskel" && (h.forslag ?? 0) === 0) {
      forslag.append(el("strong", { role: "alert",
        text: t("ui.hendelse.mangler_forslag") }));
    } else {
      forslag.append(el("span", { text: String(h.forslag ?? 0) }));
    }
    tr.append(forslag);
    const handling = el("td", {});
    if (h.status === "apen" && ctx.kanSkrive) {
      handling.append(knappMed(t("ui.hendelse.lukk_hendelse"),
        () => ctx.aapneLukk(h, last)));
      handling.append(" ");
      handling.append(knappMed(t("ui.hendelse.foresla"),
        () => ctx.aapneForslag(h, last)));
    } else {
      handling.append(el("span", { class: "muted",
        text: t("ui.hendelse.lukket") }));
    }
    tr.append(handling);
    ut.push(tr);
  }
  return ut;
}


// PLAYBOOKENE MED STEGENE SINE.
//
// Stegene står som en LESBAR LISTE, i rekkefølge. En playbook uten
// steg får et varsel: den ville tilfredsstilt fremmednøkkelen i
// `inngrepsforslag` og forklart ingenting.
export function playbookrader(data) {
  const ut = [];
  for (const p of data.playbooker || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: p.navn || "–" }));
    tr.append(el("td", {},
      p.krever_tofaktor
        ? el("strong", { text: t("ui.hendelse.tofaktor_ja") })
        : el("span", { class: "muted",
                       text: t("ui.hendelse.tofaktor_nei") })));
    const steg = el("td", {});
    const liste = Array.isArray(p.steg) ? p.steg : [];
    if (!liste.length) {
      steg.append(el("strong", { role: "alert",
        text: t("ui.hendelse.playbook_uten_steg") }));
    } else {
      const ol = el("ol", { class: "stegliste" });
      for (const s of liste) {
        ol.append(el("li", {
          text: t(STEGTEKST[s] || "ui.hendelse.steg_ukjent") }));
      }
      steg.append(ol);
    }
    tr.append(steg);
    tr.append(el("td", { text: String(p.foreslatt_ganger ?? 0) }));
    tr.append(el("td", {},
      p.gjelder_i_dag
        ? el("span", { text: t("ui.hendelse.gjelder_ja") })
        : el("span", { class: "muted",
                       text: t("ui.hendelse.gjelder_nei") })));
    ut.push(tr);
  }
  return ut;
}


// REGLENE. «BRUKT» ER DEN VIKTIGSTE KOLONNEN.
//
// En regelsamling der ingen regel noen gang traff, er et
// deteksjonsapparat som ikke detekterer — og det ser nøyaktig ut som
// en base uten hendelser.
export function regelrader(data, ctx, last) {
  const ut = [];
  for (const r of data.regler || []) {
    const tr = el("tr", {});
    tr.append(el("td", { text: r.navn || "–" }));
    tr.append(el("td", {
      text: t(SIGNALTEKST[r.signaltype] || "ui.hendelse.signal_ukjent") }));
    tr.append(el("td", {
      text: t("ui.hendelse.regel_poeng")
        .replace("{poeng}", String(r.poeng ?? 0))
        .replace("{treff}", String(r.terskel_treff ?? 0)) }));
    const brukt = el("td", {});
    if (r.gjelder_i_dag && (r.brukt ?? 0) === 0) {
      brukt.append(el("span", { class: "muted",
        text: t("ui.hendelse.regel_ubrukt") }));
    } else {
      brukt.append(el("span", { text: String(r.brukt ?? 0) }));
    }
    tr.append(brukt);
    const handling = el("td", {});
    if (r.gjelder_i_dag && ctx.kanSkrive) {
      handling.append(knappMed(t("ui.hendelse.avvikle"),
        () => ctx.avvikl(r, last)));
    } else {
      handling.append(el("span", { class: "muted",
        text: t("ui.hendelse.avviklet") }));
    }
    tr.append(handling);
    ut.push(tr);
  }
  return ut;
}


// FUNNENE. DE FIRE UMULIGE ER MERKET SOM UMULIGE.
//
// De står i settet OG kan aldri reises, og at de gjør begge deler er
// beviset. Flaten sier det: et funn ingen kan lukke fordi ingen kan
// reise det, skal ikke ha en lukkeknapp som ser ut som en oppgave.
export function funnrader(data, ctx, last) {
  const ut = [];
  for (const f of data.funn || []) {
    const tr = el("tr", {});
    tr.append(el("td", {
      text: t(FUNNTEKST[f.funntype] || "ui.hendelse.funn_ukjent") }));
    tr.append(el("td", { text: f.detalj || "–" }));
    tr.append(el("td", { text: dato(f.forst_sett) }));
    const handling = el("td", {});
    if (f.sveipens) {
      // SVEIPENS EGNE LUKKES NÅR TILSTANDEN ER BORTE. En knapp her
      // ville invitert til å lukke en måling.
      handling.append(el("span", { class: "muted",
        text: t("ui.hendelse.lukkes_av_sveipen") }));
    } else if (ctx.kanSkrive) {
      handling.append(knappMed(t("ui.hendelse.lukk_funn"),
        () => ctx.lukkFunn(f, last)));
    }
    tr.append(handling);
    ut.push(tr);
  }
  return ut;
}


// KRAVSKJEMAET. ALLE FIRE GRENSENE ER TENANTENS.
//
// Hvor mange poeng som gjør fire uskyldige signaler til en hendelse er
// en vurdering av hva det koster å ta feil BEGGE VEIER — en
// tannlegeklinikk og en bank tåler ikke det samme antallet falske
// alarmer.
//
// SKJEMAET VISER GJELDENDE VERDIER, ikke tomme felt: et skjema som
// viser mindre enn det lagrer er en felle (123s lærdom).
export function kravskjema(ctx, last, kvitter, s) {
  const vindu = el("input", { id: "h-vindu", type: "number", min: "1",
                              max: "10080", required: "required",
                              value: String(s.korrelasjonsvindu_min ?? 60) });
  const terskel = el("input", { id: "h-terskel", type: "number", min: "1",
                                max: "10000", required: "required",
                                value: String(s.alvorsterskel ?? 100) });
  const frist = el("input", { id: "h-frist", type: "number", min: "1",
                              max: "365", required: "required",
                              value: String(s.apen_hendelse_frist_dogn ?? 7) });
  const tak = el("input", { id: "h-tak", type: "number", min: "2",
                            max: "1000", required: "required",
                            value: String(s.signaltak ?? 50) });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.hendelse.lagre_krav") });
  const skjema = el("form", { class: "skjema" },
    felt("h-vindu", "ui.hendelse.felt_vindu", vindu,
         "ui.hendelse.hjelp_vindu"),
    felt("h-terskel", "ui.hendelse.felt_terskel", terskel,
         "ui.hendelse.hjelp_terskel"),
    felt("h-frist", "ui.hendelse.felt_frist", frist,
         "ui.hendelse.hjelp_frist"),
    felt("h-tak", "ui.hendelse.felt_tak", tak,
         "ui.hendelse.hjelp_tak"),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, okNokkel: "ui.hendelse.krav_lagret", kvitter,
    send: (idem) => settHendelseskrav({
      korrelasjonsvindu_min: Number(vindu.value),
      alvorsterskel: Number(terskel.value),
      apen_hendelse_frist_dogn: Number(frist.value),
      signaltak: Number(tak.value),
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.hendelse.krav") }),
    // VERSJONEN TILDELES AV DØRA. Raden er append-only fordi
    // hendelsene peker på den: «terskelen som gjaldt» må kunne slås
    // opp i ettertid.
    el("p", { class: "muted", text: t("ui.hendelse.krav_hjelp") }),
    skjema);
}


// REGELSKJEMAET. DET ENESTE SOM KAN GI POENG.
export function regelskjema(ctx, last, kvitter) {
  const navn = el("input", { id: "h-regelnavn", type: "text",
                             maxlength: "500", required: "required" });
  const type = velger("h-signaltype", SIGNALTYPER, SIGNALTEKST);
  const poeng = el("input", { id: "h-poeng", type: "number", min: "1",
                              max: "1000", required: "required",
                              value: "50" });
  const treff = el("input", { id: "h-treff", type: "number", min: "1",
                              max: "1000", required: "required",
                              value: "3" });
  const grunn = el("textarea", { id: "h-regelgrunn", rows: "3",
                                 maxlength: "8000",
                                 required: "required" });
  const fra = el("input", { id: "h-regelfra", type: "date",
                            required: "required", value: iDagLokal() });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.hendelse.lagre_regel") });
  const skjema = el("form", { class: "skjema" },
    felt("h-regelnavn", "ui.hendelse.felt_regelnavn", navn),
    felt("h-signaltype", "ui.hendelse.felt_signaltype", type,
         "ui.hendelse.hjelp_signaltype"),
    felt("h-poeng", "ui.hendelse.felt_poeng", poeng),
    felt("h-treff", "ui.hendelse.felt_treff", treff,
         "ui.hendelse.hjelp_treff"),
    felt("h-regelgrunn", "ui.hendelse.felt_regelgrunn", grunn,
         "ui.hendelse.hjelp_regelgrunn"),
    felt("h-regelfra", "ui.hendelse.felt_gyldig_fra", fra),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, okNokkel: "ui.hendelse.regel_lagret", kvitter,
    tilbakestill: () => { navn.value = ""; grunn.value = ""; },
    send: (idem) => registrerSikkerhetsregel({
      navn: navn.value, signaltype: type.value,
      poeng: Number(poeng.value), terskel_treff: Number(treff.value),
      begrunnelse: grunn.value, gyldig_fra: fra.value,
      gyldig_til: null,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.hendelse.ny_regel") }),
    el("p", { class: "muted", text: t("ui.hendelse.regel_hjelp") }),
    skjema);
}


// PLAYBOOKSKJEMAET. STEGENE ER AVKRYSSINGSBOKSER, IKKE ET TEKSTFELT.
//
// DET ER HELE POENGET. Et tekstfelt ville tatt imot en kommando; en
// liste med avkryssinger kan bare uttrykke navn fra det lukkede
// settet. «Ingen fri kommandokjøring» er en grammatikk, ikke en
// policy — og her er grammatikken selve skjemaet.
export function playbookskjema(ctx, last, kvitter) {
  const navn = el("input", { id: "h-pbnavn", type: "text",
                             maxlength: "500", required: "required" });
  const naar = el("textarea", { id: "h-pbnaar", rows: "2",
                                maxlength: "8000",
                                required: "required" });
  const tofaktor = el("input", { id: "h-pbtofaktor", type: "checkbox" });
  const fra = el("input", { id: "h-pbfra", type: "date",
                            required: "required", value: iDagLokal() });
  const bokser = new Map();
  const stegfelt = el("fieldset", { class: "felt" },
    el("legend", { text: t("ui.hendelse.felt_steg") }));
  for (const s of STEGTYPER) {
    const boks = el("input", { id: `h-steg-${s}`, type: "checkbox",
                               value: s });
    bokser.set(s, boks);
    stegfelt.append(el("div", { class: "avkrysning" }, boks,
      el("label", { for: `h-steg-${s}`, text: t(STEGTEKST[s]) })));
  }
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.hendelse.lagre_playbook") });
  const skjema = el("form", { class: "skjema" },
    felt("h-pbnavn", "ui.hendelse.felt_pbnavn", navn),
    felt("h-pbnaar", "ui.hendelse.felt_pbnaar", naar,
         "ui.hendelse.hjelp_pbnaar"),
    el("div", { class: "felt" }, tofaktor,
       el("label", { for: "h-pbtofaktor",
                     text: t("ui.hendelse.felt_tofaktor") }),
       el("p", { class: "muted", text: t("ui.hendelse.hjelp_tofaktor") })),
    stegfelt,
    felt("h-pbfra", "ui.hendelse.felt_gyldig_fra", fra),
    knapp, utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, okNokkel: "ui.hendelse.playbook_lagret",
    kvitter,
    tilbakestill: () => {
      navn.value = ""; naar.value = ""; tofaktor.checked = false;
      for (const b of bokser.values()) b.checked = false;
    },
    send: (idem) => registrerPlaybook({
      navn: navn.value, naar_gjelder_den: naar.value,
      krever_tofaktor: tofaktor.checked,
      // REKKEFØLGEN ER SETTETS, ikke avkryssingens: to brukere som
      // krysset av i ulik rekkefølge skal få samme playbook.
      steg: STEGTYPER.filter((s) => bokser.get(s).checked),
      gyldig_fra: fra.value, gyldig_til: null,
    }, idem),
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.hendelse.ny_playbook") }),
    el("p", { class: "muted", text: t("ui.hendelse.playbook_hjelp") }),
    skjema);
}


// KORRELASJONSPANELET. KALLEREN OPPGIR ALDRI EN SCORE.
//
// Panelet henter kandidatradene fra revisjonsloggen gjennom
// `/v1/hendelse/signaler` — som utelater modulens EGET spor — og lar
// brukeren velge hvilke som hører sammen. Scoren regnes av regelens
// poeng mot dens egen terskel, og kommer TILBAKE i svaret.
//
// DET FINNES INGEN SCORE-INPUT HER, og det er ikke en forglemmelse.
export function korrelasjonspanel(ctx, last, kvitter, data) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.hendelse.korreler") }));
  const regler = (data.regler || []).filter((r) => r.gjelder_i_dag);
  if (!regler.length) {
    node.append(el("p", { class: "muted",
      text: t("ui.hendelse.korreler_uten_regel") }));
    return { node, aapne: null };
  }
  const velg = el("select", { id: "h-korrelregel", name: "h-korrelregel" });
  for (const r of regler) {
    velg.append(el("option", { value: r.regel_id,
      text: `${r.navn} · ${t(SIGNALTEKST[r.signaltype]
                              || "ui.hendelse.signal_ukjent")}` }));
  }
  const timer = el("input", { id: "h-korreltimer", type: "number",
                              min: "1", max: "168", value: "24" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const treffliste = el("div", {});
  const knapp = el("button", { type: "submit",
                               text: t("ui.hendelse.korreler_knapp") });
  const skjema = el("form", { class: "skjema" },
    felt("h-korrelregel", "ui.hendelse.felt_regel", velg),
    felt("h-korreltimer", "ui.hendelse.felt_timer", timer,
         "ui.hendelse.hjelp_timer"),
    knapp, utfall, treffliste);

  let kandidater = [];
  const hent = async () => {
    const fra = new Date(Date.now() - Number(timer.value) * 3600_000);
    const svar = await hentJson(
      `/v1/hendelse/signaler?fra=${encodeURIComponent(fra.toISOString())}`);
    kandidater = svar.kandidater || [];
    sett(treffliste, el("p", { class: "muted",
      text: t("ui.hendelse.kandidater")
        .replace("{n}", String(kandidater.length)) }));
  };

  skjemaramme(ctx, last, {
    skjema, knapp, utfall, okNokkel: "ui.hendelse.korrelert", kvitter,
    send: async (idem) => {
      await hent();
      if (!kandidater.length) {
        // INGEN KANDIDATER ER IKKE EN FEIL. DET ER SVARET — og det
        // skal si nettopp det.
        //
        // EN VANLIG `Error`, IKKE `FeilformetFeil`. Den siste tar
        // `(status, kode, detaljer)`, så teksten ville havnet i
        // `status` og meldingen blitt tom. `skjemaramme` viser
        // `message` bare når feilen ikke bærer en status — altså bare
        // for feil vi selv kastet, med ord vi selv valgte.
        throw new Error(t("ui.hendelse.ingen_kandidater"));
      }
      const kravversjon = (data.sammendrag || {}).kravversjon;
      const ut = await korrelerHendelse({
        regel_id: velg.value,
        kravversjon: kravversjon || 1,
        kilde_refs: kandidater.map((k) => k.logg_id),
        aktorer: kandidater.map((k) => k.aktor),
        observert: kandidater.map((k) => k.ts),
      }, idem);
      // SCOREN KOMMER TILBAKE FORDI KALLEREN IKKE OPPGA DEN.
      sett(treffliste, el("p", { role: "status",
        text: t("ui.hendelse.korrelert_svar")
          .replace("{score}", String(ut.score))
          .replace("{alvor}", t(ALVORTEKST[ut.alvor]
                                || "ui.hendelse.alvor_ukjent"))
          .replace("{n}", String(ut.signaler)) }));
      return ut;
    },
  });
  node.append(el("p", { class: "muted",
    text: t("ui.hendelse.korreler_hjelp") }), skjema);
  return { node, aapne: hent };
}


// LUKKEPANELET — FOR HENDELSER OG FOR FUNN.
function lukkepanel(ctx, last, kvitter, art) {
  const grunn = el("textarea", { id: `h-${art}grunn`, rows: "2",
                                 maxlength: "8000",
                                 required: "required" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.hendelse.lukk_bekreft") });
  const skjema = el("form", { class: "skjema" },
    felt(`h-${art}grunn`, "ui.hendelse.felt_grunn", grunn,
         "ui.hendelse.hjelp_grunn"),
    knapp, utfall);
  const node = el("section", { class: "kpi-kort", hidden: "hidden" },
    el("h2", { text: t(art === "hendelse"
                       ? "ui.hendelse.lukk_hendelse_tittel"
                       : "ui.hendelse.lukk_funn_tittel") }), skjema);
  let valgt = null;
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: art === "hendelse"
      ? "ui.hendelse.hendelse_lukket" : "ui.hendelse.funn_lukket",
    tilbakestill: () => {
      grunn.value = ""; valgt = null; node.hidden = true;
    },
    send: (idem) => (art === "hendelse"
      ? lukkSikkerhetshendelse(valgt.hendelse_id, { grunn: grunn.value },
                               idem)
      : lukkHendelsesfunn(valgt.funn_id, { grunn: grunn.value }, idem)),
  });
  return {
    node,
    aapne: (rad) => { valgt = rad; node.hidden = false; grunn.focus(); },
  };
}


// FORSLAGSPANELET. DER VEIEN SLUTTER.
//
// Panelet skriver et FORSLAG som peker på en playbook. Det finnes
// ingen «utfør»-knapp, og det kan ikke finnes: `inngrepsforslag` har
// ingen kolonne å skrive et utfall i.
export function forslagspanel(ctx, last, kvitter, data) {
  const playbooker = (data.playbooker || []).filter((p) => p.gjelder_i_dag);
  const velg = el("select", { id: "h-forslagpb", name: "h-forslagpb" });
  for (const p of playbooker) {
    velg.append(el("option", { value: p.playbook_id,
      text: p.krever_tofaktor
        ? `${p.navn} · ${t("ui.hendelse.tofaktor_ja")}`
        : p.navn }));
  }
  const grunn = el("textarea", { id: "h-forslaggrunn", rows: "2",
                                 maxlength: "8000",
                                 required: "required" });
  const utfall = el("p", { class: "muted", "aria-live": "polite" });
  const knapp = el("button", { type: "submit",
                               text: t("ui.hendelse.foresla_bekreft") });
  const skjema = el("form", { class: "skjema" },
    felt("h-forslagpb", "ui.hendelse.felt_playbook", velg),
    felt("h-forslaggrunn", "ui.hendelse.felt_grunn", grunn),
    knapp, utfall);
  const node = el("section", { class: "kpi-kort", hidden: "hidden" },
    el("h2", { text: t("ui.hendelse.foresla_tittel") }),
    // SAGT RETT UT, I FLATEN: forslaget utføres ikke.
    el("p", { class: "muted", text: t("ui.hendelse.foresla_hjelp") }),
    skjema);
  let valgt = null;
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.hendelse.forslag_lagret",
    tilbakestill: () => {
      grunn.value = ""; valgt = null; node.hidden = true;
    },
    send: (idem) => foreslaaInngrep(valgt.hendelse_id, {
      playbook_id: velg.value, begrunnelse: grunn.value,
    }, idem),
  });
  return {
    node,
    aapne: playbooker.length
      ? (rad) => { valgt = rad; node.hidden = false; grunn.focus(); }
      : null,
  };
}


export function visHendelse(hoved, ctx) {
  const hode = () => flateHode(t("ui.hendelse.tittel"),
    t("ui.hendelse.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/hendelse"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const skriver = harScope(ctx, "bestilling:opprett");
      const hendelseslukking = lukkepanel(ctx, last, kvitter, "hendelse");
      const funnlukking = lukkepanel(ctx, last, kvitter, "funn");
      const forslag = forslagspanel(ctx, last, kvitter, d);
      const korrelasjon = korrelasjonspanel(ctx, last, kvitter, d);

      const kontekst = {
        kanSkrive: skriver,
        aapneLukk: (h) => hendelseslukking.aapne(h),
        aapneForslag: (h) => {
          if (forslag.aapne) forslag.aapne(h);
          else kvitter(t("ui.hendelse.ingen_playbook"));
        },
        lukkFunn: (f) => funnlukking.aapne(f),
        avvikl: (r) => avviklRegel(ctx, last, kvitter, r),
      };

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.hendelse.sammendrag_tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        //
        // Et menneske som leser flaten skal ikke måtte anta at
        // maskinen holdt seg i ro. Hun skal se det, i klartekst, i
        // samme kort som tallene.
        el("p", { class: "muted", text: t("ui.hendelse.hvorfor") }));

      const funnseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.hendelse.funn") }));
      const funn = d.funn || [];
      if (!funn.length) {
        funnseksjon.append(el("p", { class: "muted",
          text: t("ui.hendelse.funn_tomt") }));
      } else {
        funnseksjon.append(tabell(
          ["ui.hendelse.kol_funntype", "ui.hendelse.kol_detalj",
           "ui.hendelse.kol_forst_sett", "ui.hendelse.kol_handling"],
          funnrader(d, kontekst, last)));
      }

      const hendelsesseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.hendelse.hendelser") }));
      if (!(d.hendelser || []).length) {
        hendelsesseksjon.append(el("p", { class: "muted",
          text: t("ui.hendelse.hendelser_tomt") }));
      } else {
        hendelsesseksjon.append(tabell(
          ["ui.hendelse.kol_oppdaget", "ui.hendelse.kol_score",
           "ui.hendelse.kol_alvor", "ui.hendelse.kol_signaler",
           "ui.hendelse.kol_forslag", "ui.hendelse.kol_handling"],
          hendelsesrader(d, kontekst, last)));
      }

      const playbookseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.hendelse.playbooker") }));
      if (!(d.playbooker || []).length) {
        playbookseksjon.append(el("p", { class: "muted",
          text: t("ui.hendelse.playbooker_tomt") }));
      } else {
        playbookseksjon.append(tabell(
          ["ui.hendelse.kol_navn", "ui.hendelse.kol_tofaktor",
           "ui.hendelse.kol_steg", "ui.hendelse.kol_foreslatt",
           "ui.hendelse.kol_gjelder"],
          playbookrader(d)));
      }

      const regelseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.hendelse.regler") }));
      if (!(d.regler || []).length) {
        regelseksjon.append(el("p", { class: "muted",
          text: t("ui.hendelse.regler_tomt") }));
      } else {
        regelseksjon.append(tabell(
          ["ui.hendelse.kol_navn", "ui.hendelse.kol_signaltype",
           "ui.hendelse.kol_poeng", "ui.hendelse.kol_brukt",
           "ui.hendelse.kol_handling"],
          regelrader(d, kontekst, last)));
      }

      // FUNNENE FØRST, SÅ HENDELSENE. Det som haster er en hendelse
      // over terskel ingen har skrevet et forslag på — ikke listen
      // over hvor mange hendelser vi har hatt.
      const deler = [oversikt, funnseksjon, funnlukking.node,
                     hendelsesseksjon, hendelseslukking.node,
                     forslag.node, playbookseksjon, regelseksjon];
      if (skriver) {
        deler.push(korrelasjon.node,
                   playbookskjema(ctx, last, kvitter),
                   regelskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, kvitter, s));
      }
      sett(kropp, ...deler);
    });

  async function avviklRegel(c, l, k, r) {
    // AVVIKLING ER ENVEIS, og datoen er i dag. En regel som kunne
    // avvikles fram i tid ville gjort «gjelder den nå?» til et
    // spørsmål med to svar (133/135s form).
    //
    // OG REGELEN SLETTES ALDRI: en score forklart av en regel som er
    // BORTE er en score uten forklaring.
    try {
      await avviklSikkerhetsregel(r.regel_id,
        { gyldig_til: iDagLokal() }, nyIdempotensnokkel());
    } catch (e) {
      if (e instanceof UautorisertFeil) { c.paaUautorisert(); return; }
      k(t("ui.hendelse.feil.generell"));
      return;
    }
    meldLive(t("ui.hendelse.regel_avviklet"));
    k(t("ui.hendelse.regel_avviklet"));
    await l();
  }

  return last();
}
