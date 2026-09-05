// M-50 postjournal- og innsynsvakten (124) — OFFENTLIG ER IKKE FRITT.
//
// FLATENS VIKTIGSTE JOBB er å vise HVEM VI OPPBEVARER og HVOR LENGE.
// Det er ikke en stilistisk preferanse — det er modulens dom:
//
//   Postjournaler ER offentlige, så den vanlige innvendingen mot
//   utgående oppslag treffer ikke. Det som treffer er at journalene
//   inneholder NAVNGITTE PRIVATPERSONER, og at ti tusen oppslag
//   sammenstilt i et register er en PROFIL — som er VÅR, ikke
//   kommunens.
//
// Derfor står `frist_passert` FØRST og i fet skrift i sammendraget:
// navngitte privatpersoner vi oppbevarer lenger enn vi selv har
// bestemt. Og derfor sorterer basen de passerte fristene øverst — en
// liste sortert på registreringstidspunkt ville begravd bruddet under
// alt som er i orden.
//
// DET FINNES INGEN «HENT»-KNAPP, OG DET KAN IKKE FINNES. Hver post er
// noe et menneske har slått opp, og `hentet_av_person` heter det den
// er.
//
// FORMÅLET STÅR PÅ HVER RAD. En sammenstilling uten et skrevet formål
// er en behandling ingen kan gjøre rede for, og «vi fant det på nett»
// er ikke et rettslig grunnlag.
//
// «ANONYMISER» ER IKKE «SLETT». Knappen tømmer navnet og lar sporet
// stå: at vi HAR oppbevart noen skal fortsatt kunne leses. Sletting
// ville fjernet beviset på at vi hadde den.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  anonymiserPerson, hentJson, lukkJournalfunn, nyIdempotensnokkel,
  opprettJournalsak, registrerJournalkilde, registrerJournalpost,
  settJournalkrav, settKildeGyldigTil, UautorisertFeil,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export const FORMATER = ["noark5", "einnsyn", "kommunal_web", "annet"];
export const GRUNNLAG = ["berettiget_interesse", "avtale",
                         "rettslig_forpliktelse", "samtykke"];
export const ROLLER = ["avsender", "mottaker", "part", "omtalt"];

const FORMATTEKST = {
  noark5: "ui.journal.format_noark5",
  einnsyn: "ui.journal.format_einnsyn",
  kommunal_web: "ui.journal.format_web",
  annet: "ui.journal.format_annet",
};

const GRUNNLAGTEKST = {
  berettiget_interesse: "ui.journal.grunnlag_berettiget",
  avtale: "ui.journal.grunnlag_avtale",
  rettslig_forpliktelse: "ui.journal.grunnlag_rettslig",
  samtykke: "ui.journal.grunnlag_samtykke",
};

const ROLLETEKST = {
  avsender: "ui.journal.rolle_avsender",
  mottaker: "ui.journal.rolle_mottaker",
  part: "ui.journal.rolle_part",
  omtalt: "ui.journal.rolle_omtalt",
};

const FUNNTEKST = {
  ingen_krav: "ui.journal.funn_uten_krav",
  kilde_utlopt: "ui.journal.funn_kilde_utlopt",
  kilde_utloper_snart: "ui.journal.funn_kilde_snart",
  post_mot_utlopt_kilde: "ui.journal.funn_post_utlopt",
  slettefrist_naermer_seg: "ui.journal.funn_frist_naer",
  slettefrist_passert: "ui.journal.funn_frist_passert",
};


// KILDEN, MED GYLDIGHETEN SIN — ALDRI VERSJONEN ALENE.
//
// MUTASJONEN SOM DREPER PORTEN: returner bare «Oslo kommune 2026.1».
// En versjon uten om formatet fortsatt gjelder er nettopp den
// opplysningen som gjør en post lest i et gammelt format umulig å
// skille fra en lest i dagens.
export function kildeTekst(rad) {
  if (!rad || !rad.organ) return t("ui.journal.uten_kilde");
  const navn = `${rad.organ} · ${t(FORMATTEKST[rad.format]
    || rad.format)} ${rad.versjon}`;
  if (rad.gyldig_naa === null || rad.gyldig_naa === undefined) {
    return navn;
  }
  return t(rad.gyldig_naa ? "ui.journal.kilde_gyldig"
                          : "ui.journal.kilde_utlopt")
    .replace("{navn}", navn);
}


// SLETTEFRISTEN, MED RETNING. Fortegnet er hele beskjeden: en frist om
// tretti døgn og en som gikk for tretti døgn siden er to helt
// forskjellige tilstander — den ene er en plan, den andre et brudd.
export function slettefristTekst(dogn) {
  if (dogn === null || dogn === undefined) {
    return t("ui.journal.uten_frist");
  }
  if (dogn < 0) {
    return t("ui.journal.frist_passert").replace("{n}", String(-dogn));
  }
  if (dogn === 0) return t("ui.journal.frist_i_dag");
  return t("ui.journal.frist_om").replace("{n}", String(dogn));
}


// PERSONENS TILSTAND. `null` navn er ikke et hull — det ER svaret.
export function persontilstand(p) {
  if (p.anonymisert_ts) return t("ui.journal.anonymisert");
  if (p.dogn_til_slettefrist < 0) {
    return t("ui.journal.oppbevart_for_lenge")
      .replace("{n}", String(-p.dogn_til_slettefrist));
  }
  return t("ui.journal.oppbevares");
}


function felt(id, tekst, kontroll, hjelp) {
  return el("div", { class: "felt" },
    el("label", { for: id, text: t(tekst) }),
    kontroll,
    hjelp ? el("p", { class: "muted", text: t(hjelp) }) : null);
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
      sett(utfall, el("span", { role: "alert",
        text: e && e.status === 409
          ? t("ui.journal.feil.tilstand")
          : t("ui.journal.feil.generell") }));
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


// SAMMENDRAGET. `frist_passert` STÅR FØRST OG I FET SKRIFT.
//
// Det er det ene tallet modulen finnes for: navngitte privatpersoner
// vi oppbevarer lenger enn vi selv har bestemt. Et sammendrag som
// begynte med «142 journalposter registrert» ville fortalt hvor
// flittige vi har vært, ikke hva som er galt.
export function sammendrag(s) {
  const p = el("p");
  p.append(el("strong", {
    text: t("ui.journal.passert_sum")
      .replace("{n}", String(s.frist_passert ?? 0)) }));
  if (s.frist_naer > 0) {
    p.append(" ", el("strong", {
      text: t("ui.journal.naer_sum")
        .replace("{n}", String(s.frist_naer)) }));
  }
  p.append(" ", el("span", {
    text: t("ui.journal.tellinger")
      .replace("{saker}", String(s.saker ?? 0))
      .replace("{poster}", String(s.poster ?? 0))
      .replace("{levende}", String(s.levende_personer ?? 0)) }));
  if (s.utlopte > 0) {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.journal.utlopte_kilder")
        .replace("{n}", String(s.utlopte)) }));
  }
  if (!s.gyldige) {
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.journal.ingen_gyldig_kilde") }));
  }
  if (s.apne_funn > 0) {
    p.append(" ", el("strong", {
      text: t("ui.journal.apne_funn")
        .replace("{n}", String(s.apne_funn)) }));
  }
  if (!s.har_krav) {
    // UTEN OPPBEVARINGSGRENSER ER REGISTERET UOVERVÅKET. Døra nekter
    // nye poster, men de som alt står der skal si fra om hvorfor
    // ingenting skjer.
    p.append(" ", el("strong", { role: "alert",
      text: t("ui.journal.ingen_grenser") }));
  } else {
    p.append(" ", el("span", { class: "muted",
      text: t("ui.journal.taket_er")
        .replace("{n}", String(s.sletteplan_maks_dogn)) }));
  }
  if (s.vist < s.poster) {
    p.append(" ", el("strong", {
      text: t("ui.journal.avkortet")
        .replace("{vist}", String(s.vist)) }));
  }
  return p;
}


export function kildetabell(kilder, aapne) {
  const tbody = el("tbody");
  for (const k of kilder) {
    const knapp = aapne
      ? el("button", { type: "button",
          text: t("ui.journal.knapp.sett_sluttdato") })
      : null;
    if (knapp) knapp.addEventListener("click", () => aapne(k));
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: k.organ }),
      el("td", { text: k.organnummer || "–" }),
      el("td", { text: t(FORMATTEKST[k.format] || k.format) }),
      el("td", { text: k.versjon }),
      el("td", { text: k.gyldig_fra }),
      el("td", { text: k.gyldig_til || t("ui.journal.uten_sluttdato") }),
      el("td", { text: k.gyldig_naa ? t("ui.journal.ja")
                                    : t("ui.journal.nei") }),
      el("td", { class: "tall", text: String(k.antall_poster) }),
      knapp ? el("td", {}, knapp) : null));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.journal.kilder.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.journal.kol.organ") }),
        el("th", { scope: "col", text: t("ui.journal.kol.orgnr") }),
        el("th", { scope: "col", text: t("ui.journal.kol.format") }),
        el("th", { scope: "col", text: t("ui.journal.kol.versjon") }),
        el("th", { scope: "col", text: t("ui.journal.kol.fra") }),
        el("th", { scope: "col", text: t("ui.journal.kol.til") }),
        el("th", { scope: "col",
                   text: t("ui.journal.kol.gyldig_naa") }),
        el("th", { scope: "col", text: t("ui.journal.kol.poster") }),
        aapne ? el("th", { scope: "col",
                           text: t("ui.journal.kol.handling") })
              : null)),
      tbody));
}


export function saktabell(saker) {
  const tbody = el("tbody");
  for (const s of saker) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: s.tittel }),
      // FORMÅLET ER EN KOLONNE, ikke en detalj man må klikke seg til.
      el("td", { text: s.formaal }),
      el("td", { text: t(GRUNNLAGTEKST[s.grunnlag] || s.grunnlag) }),
      el("td", { class: "tall", text: String(s.antall_poster) }),
      el("td", { class: "tall", text: String(s.antall_personer) }),
      el("td", { text: s.opprettet_av })));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.journal.saker.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.journal.kol.sak") }),
        el("th", { scope: "col", text: t("ui.journal.kol.formaal") }),
        el("th", { scope: "col", text: t("ui.journal.kol.grunnlag") }),
        el("th", { scope: "col", text: t("ui.journal.kol.poster") }),
        el("th", { scope: "col", text: t("ui.journal.kol.personer") }),
        el("th", { scope: "col", text: t("ui.journal.kol.av") }))),
      tbody));
}


// POSTTABELLEN. FORMÅLET, KILDEVERSJONEN, HVEM SOM HENTET, OG DEN
// NÆRMESTE SLETTEFRISTEN — aldri dokumenttittelen alene.
export function posttabell(poster, aapne) {
  const tbody = el("tbody");
  for (const p of poster) {
    const knapp = aapne
      ? el("button", { type: "button",
          text: t("ui.journal.knapp.apne_personer") })
      : null;
    if (knapp) knapp.addEventListener("click", () => aapne(p));
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: p.journalnummer }),
      el("td", { text: p.dokumenttittel }),
      el("td", { text: p.journaldato }),
      el("td", { text: p.formaal }),
      el("td", { text: kildeTekst({ organ: p.organ,
        format: p.format, versjon: p.kildeversjon,
        gyldig_naa: p.kilde_gyldig_naa }) }),
      // ET MENNESKE HENTET DEN.
      el("td", { text: `${p.hentet_av_person} · ${p.hentet_dato}` }),
      el("td", { class: "tall", text: String(p.antall_levende) }),
      el("td", { text: p.naermeste_slettefrist || "–" }),
      knapp ? el("td", {}, knapp) : el("td", {})));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.journal.poster.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.journal.kol.journalnr") }),
        el("th", { scope: "col", text: t("ui.journal.kol.dokument") }),
        el("th", { scope: "col", text: t("ui.journal.kol.dato") }),
        el("th", { scope: "col", text: t("ui.journal.kol.formaal") }),
        el("th", { scope: "col", text: t("ui.journal.kol.kilde") }),
        el("th", { scope: "col", text: t("ui.journal.kol.hentet_av") }),
        el("th", { scope: "col", text: t("ui.journal.kol.personer") }),
        el("th", { scope: "col", text: t("ui.journal.kol.frist") }),
        el("th", { scope: "col",
                   text: t("ui.journal.kol.handling") }))),
      tbody));
}


// PERSONTABELLEN. `null` navn ETTER anonymisering er ikke et hull.
export function persontabell(personer, anonymiser) {
  const tbody = el("tbody");
  for (const p of personer) {
    const knapp = anonymiser && !p.anonymisert_ts
      ? el("button", { type: "button",
          text: t("ui.journal.knapp.anonymiser") })
      : null;
    if (knapp) knapp.addEventListener("click", () => anonymiser(p));
    tbody.append(el("tr", {},
      el("th", { scope: "row",
                 text: p.navn || t("ui.journal.navn_fjernet") }),
      el("td", { text: t(ROLLETEKST[p.rolle] || p.rolle) }),
      el("td", { text: p.slettefrist }),
      el("td", { text: slettefristTekst(p.dogn_til_slettefrist) }),
      el("td", { text: persontilstand(p) }),
      knapp ? el("td", {}, knapp) : el("td", {})));
  }
  return el("div", { class: "tablewrap" },
    el("table", {},
      el("caption", { text: t("ui.journal.personer.tittel") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.journal.kol.navn") }),
        el("th", { scope: "col", text: t("ui.journal.kol.rolle") }),
        el("th", { scope: "col", text: t("ui.journal.kol.frist") }),
        el("th", { scope: "col", text: t("ui.journal.kol.igjen") }),
        el("th", { scope: "col", text: t("ui.journal.kol.tilstand") }),
        el("th", { scope: "col",
                   text: t("ui.journal.kol.handling") }))),
      tbody));
}


function funnseksjon(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.journal.funn.tittel") }),
    el("p", { class: "muted", text: t("ui.journal.laster") }));
  (async () => {
    let d;
    try {
      d = await hentJson("/v1/journal/funn");
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.journal.funn.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.journal.feil.generell") }));
      return;
    }
    const funn = d.funn || [];
    const deler = [el("h2", { text: t("ui.journal.funn.tittel") })];
    if (!funn.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.journal.funn.ingen") }));
    } else {
      const tbody = el("tbody");
      for (const f of funn) {
        tbody.append(el("tr", {},
          el("th", { scope: "row",
                     text: t(FUNNTEKST[f.funntype] || f.funntype) }),
          el("td", { text: f.organ || "–" }),
          el("td", { text: f.journalnummer || "–" }),
          el("td", { text: f.slettefrist || "–" }),
          el("td", { class: "tall",
                     text: f.over_grense === null
                           || f.over_grense === undefined
                       ? "–" : String(f.over_grense) }),
          // HVORFOR NOEN FUNN IKKE KAN LUKKES, SAGT PÅ RADEN.
          el("td", { text: f.kan_lukkes
            ? t("ui.journal.funn.kan_lukkes")
            : t("ui.journal.funn.sveipens") })));
      }
      deler.push(el("div", { class: "tablewrap" }, el("table", {},
        el("caption", { text: t("ui.journal.funn.tittel") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col",
                     text: t("ui.journal.kol.funntype") }),
          el("th", { scope: "col", text: t("ui.journal.kol.organ") }),
          el("th", { scope: "col",
                     text: t("ui.journal.kol.journalnr") }),
          el("th", { scope: "col", text: t("ui.journal.kol.frist") }),
          el("th", { scope: "col", text: t("ui.journal.kol.over") }),
          el("th", { scope: "col",
                     text: t("ui.journal.kol.lukking") }))),
        tbody)));
      if (harScope(ctx, "bestilling:opprett")) {
        // BARE DE BASEN SIER KAN LUKKES. Det finnes ingen liste over
        // funntyper her — regelen bor i `m50_funn_er_sveipens`.
        const lukkbare = funn.filter((f) => f.kan_lukkes);
        if (lukkbare.length) {
          deler.push(lukkskjema(ctx, last, lukkbare, kvitter));
        }
      }
    }
    sett(node, ...deler);
  })();
  return node;
}


function lukkskjema(ctx, last, funn, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const valg = el("select", { id: "jo-l-valg", name: "funn",
    required: true });
  valg.append(el("option", { value: "",
    text: t("ui.journal.funn.velg") }));
  for (const f of funn) {
    valg.append(el("option", { value: f.funn_id,
      text: `${t(FUNNTEKST[f.funntype] || f.funntype)}`
            + ` — ${f.organ || f.journalnummer || ""}` }));
  }
  const notat = el("input", { id: "jo-l-notat", name: "notat",
    type: "text", required: true, minlength: "4", maxlength: "4000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.journal.knapp.lukk_funn") });
  skjema.append(
    felt("jo-l-valg", "ui.journal.funn.hvilket", valg, null),
    felt("jo-l-notat", "ui.journal.funn.notat", notat,
         "ui.journal.funn.notat_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.journal.skjema.funn_lukket",
    tilbakestill: () => { valg.value = ""; notat.value = ""; },
    send: (idem) =>
      lukkJournalfunn(valg.value, notat.value.trim(), idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.journal.funn.lukk_tittel") }),
    el("p", { class: "muted",
              text: t("ui.journal.funn.lukk_hvorfor") }),
    skjema, utfall);
}


// PERSONPANELET. HER STÅR «ANONYMISER», OG DEN SIER HVA DEN GJØR.
function personpanel(ctx, last, kvitter, settApen) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const aapne = async (post) => {
    settApen(post.post_id);
    node.hidden = false;
    sett(node, el("h2", { text: t("ui.journal.personer.tittel") }),
         el("p", { class: "muted", text: t("ui.journal.laster") }));
    let d = { personer: [] };
    try {
      const id = encodeURIComponent(post.post_id);
      d = await hentJson(`/v1/journal/post/${id}/personer`);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      sett(node, el("h2", { text: t("ui.journal.personer.tittel") }),
           el("p", { role: "alert",
                     text: t("ui.journal.feil.generell") }));
      return;
    }
    const rader = d.personer || [];
    const skriver = harScope(ctx, "bestilling:opprett");
    const anonymiser = skriver
      ? async (person) => {
        try {
          await anonymiserPerson(person.person_id);
        } catch (e) {
          if (e instanceof UautorisertFeil) {
            ctx.paaUautorisert(); return;
          }
          const m = t("ui.journal.feil.generell");
          kvitter(m); meldLive(m);
          return;
        }
        kvitter(t("ui.journal.skjema.anonymisert"));
        meldLive(t("ui.journal.skjema.anonymisert"));
        await last();
      }
      : null;
    const deler = [
      el("h2", { text: t("ui.journal.personer.tittel") }),
      el("p", { class: "muted",
                text: `${post.journalnummer} · `
                      + `${post.dokumenttittel}` }),
      // FORMÅLET STÅR OVER NAVNENE. Den som ser på en liste med
      // navngitte privatpersoner skal se HVORFOR vi har dem.
      el("p", { class: "muted",
                text: t("ui.journal.personer.formaal")
                  .replace("{formaal}", post.formaal) }),
    ];
    if (!rader.length) {
      deler.push(el("p", { class: "muted",
        text: t("ui.journal.personer.ingen") }));
    } else {
      deler.push(persontabell(rader, anonymiser));
      deler.push(el("p", { class: "muted",
        text: t("ui.journal.personer.hvorfor") }));
    }
    sett(node, ...deler);
  };
  return { node, aapne };
}


function sluttdatopanel(ctx, last, kvitter) {
  const node = el("section", { class: "kpi-kort", hidden: true });
  const aapne = (kilde) => {
    node.hidden = false;
    const utfall = el("p", { "aria-live": "polite" });
    const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
    const til = el("input", { id: "jo-s-til", name: "til",
      type: "date" });
    til.value = kilde.gyldig_til || "";
    const knapp = el("button", { type: "submit",
      text: t("ui.journal.knapp.sett_sluttdato") });
    skjema.append(
      felt("jo-s-til", "ui.journal.sluttdato.dato", til,
           "ui.journal.sluttdato.dato_hjelp"),
      el("div", { class: "skjema-bunn" }, knapp));
    skjemaramme(ctx, last, {
      skjema, knapp, utfall, kvitter,
      okNokkel: "ui.journal.skjema.sluttdato_ok",
      // NØKKELEN SENDES ALLTID, også når verdien er tom (121s lærdom).
      send: (idem) => settKildeGyldigTil(kilde.kilde_id,
                                         til.value || null, idem),
    });
    sett(node,
      el("h2", { text: t("ui.journal.sluttdato.tittel") }),
      el("p", { class: "muted", text: kildeTekst(kilde) }),
      el("p", { class: "muted",
                text: t("ui.journal.sluttdato.hvorfor") }),
      skjema, utfall);
  };
  return { node, aapne };
}


function kravskjema(ctx, last, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const maks = el("input", { id: "jo-k-maks", name: "maks",
    type: "number", required: true, min: "1", max: "3650", step: "1" });
  const varsel = el("input", { id: "jo-k-varsel", name: "varsel",
    type: "number", required: true, min: "1", max: "365", step: "1" });
  const kilde = el("input", { id: "jo-k-kilde", name: "kilde",
    type: "number", required: true, min: "1", max: "730", step: "1" });
  // VERDIENE KOMMER FRA SVARET, ALDRI FRA EN KONSTANT HER.
  maks.value = krav ? String(krav.sletteplan_maks_dogn) : "";
  varsel.value = krav ? String(krav.slettevarsel_dogn ?? "") : "";
  kilde.value = krav ? String(krav.kildevarsel_dogn ?? "") : "";
  const knapp = el("button", { type: "submit",
    text: t("ui.journal.knapp.sett_krav") });
  skjema.append(
    felt("jo-k-maks", "ui.journal.krav.maks", maks,
         "ui.journal.krav.maks_hjelp"),
    felt("jo-k-varsel", "ui.journal.krav.varsel", varsel,
         "ui.journal.krav.varsel_hjelp"),
    felt("jo-k-kilde", "ui.journal.krav.kilde", kilde,
         "ui.journal.krav.kilde_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.journal.skjema.krav_ok",
    send: (idem) => settJournalkrav({
      sletteplan_maks_dogn: Math.trunc(Number(maks.value)),
      slettevarsel_dogn: Math.trunc(Number(varsel.value)),
      kildevarsel_dogn: Math.trunc(Number(kilde.value)),
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.journal.krav.tittel") }),
    el("p", { class: "muted", text: t("ui.journal.krav.hvorfor") }),
    skjema, utfall);
}


function kildeskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const organ = el("input", { id: "jo-c-organ", name: "organ",
    type: "text", required: true, maxlength: "500" });
  const orgnr = el("input", { id: "jo-c-orgnr", name: "orgnr",
    type: "text", maxlength: "9", pattern: "[0-9]{9}" });
  const fmt = el("select", { id: "jo-c-format", name: "format",
    required: true });
  for (const f of FORMATER) {
    fmt.append(el("option", { value: f,
      text: t(FORMATTEKST[f] || f) }));
  }
  const versjon = el("input", { id: "jo-c-versjon", name: "versjon",
    type: "text", required: true, maxlength: "500" });
  const fra = el("input", { id: "jo-c-fra", name: "fra", type: "date",
    required: true });
  const til = el("input", { id: "jo-c-til", name: "til",
    type: "date" });
  const sha = el("input", { id: "jo-c-sha", name: "sha", type: "text",
    required: true, minlength: "64", maxlength: "64",
    pattern: "[0-9a-fA-F]{64}" });
  const url = el("input", { id: "jo-c-url", name: "url", type: "url",
    maxlength: "2000" });
  const knapp = el("button", { type: "submit",
    text: t("ui.journal.knapp.registrer_kilde") });
  skjema.append(
    felt("jo-c-organ", "ui.journal.kilde.organ", organ, null),
    felt("jo-c-orgnr", "ui.journal.kilde.orgnr", orgnr,
         "ui.journal.kilde.orgnr_hjelp"),
    felt("jo-c-format", "ui.journal.kilde.format", fmt, null),
    felt("jo-c-versjon", "ui.journal.kilde.versjon", versjon,
         "ui.journal.kilde.versjon_hjelp"),
    felt("jo-c-fra", "ui.journal.kilde.fra", fra, null),
    felt("jo-c-til", "ui.journal.kilde.til", til,
         "ui.journal.kilde.til_hjelp"),
    felt("jo-c-sha", "ui.journal.kilde.sha", sha,
         "ui.journal.kilde.sha_hjelp"),
    felt("jo-c-url", "ui.journal.kilde.url", url, null),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.journal.skjema.kilde_ok",
    tilbakestill: () => {
      organ.value = ""; orgnr.value = ""; versjon.value = "";
      fra.value = ""; til.value = ""; sha.value = ""; url.value = "";
    },
    send: (idem) => registrerJournalkilde({
      organ: organ.value.trim(),
      organnummer: orgnr.value.trim() || null,
      format: fmt.value,
      versjon: versjon.value.trim(),
      gyldig_fra: fra.value,
      gyldig_til: til.value || null,
      innhold_sha256: sha.value.trim().toLowerCase(),
      kilde_url: url.value.trim() || null,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.journal.kilde.tittel") }),
    el("p", { class: "muted", text: t("ui.journal.kilde.hvorfor") }),
    skjema, utfall);
}


function sakskjema(ctx, last, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const tittel = el("input", { id: "jo-a-tittel", name: "tittel",
    type: "text", required: true, maxlength: "500" });
  // FORMÅLET ER ET TEKSTFELT MED MINSTELENGDE, ikke en nedtrekksliste.
  // Et formål man kan velge fra en liste er et formål ingen har tenkt
  // gjennom, og «markedsføring» sier ikke hva vi faktisk skal gjøre.
  const formaal = el("textarea", { id: "jo-a-formaal",
    name: "formaal", required: true, minlength: "16",
    maxlength: "4000", rows: "3" });
  const grunnlag = el("select", { id: "jo-a-grunnlag",
    name: "grunnlag", required: true });
  for (const g of GRUNNLAG) {
    grunnlag.append(el("option", { value: g,
      text: t(GRUNNLAGTEKST[g] || g) }));
  }
  const knapp = el("button", { type: "submit",
    text: t("ui.journal.knapp.opprett_sak") });
  skjema.append(
    felt("jo-a-tittel", "ui.journal.sak.tittel_felt", tittel, null),
    felt("jo-a-formaal", "ui.journal.sak.formaal", formaal,
         "ui.journal.sak.formaal_hjelp"),
    felt("jo-a-grunnlag", "ui.journal.sak.grunnlag", grunnlag,
         "ui.journal.sak.grunnlag_hjelp"),
    el("div", { class: "skjema-bunn" }, knapp));
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.journal.skjema.sak_ok",
    tilbakestill: () => { tittel.value = ""; formaal.value = ""; },
    send: (idem) => opprettJournalsak({
      tittel: tittel.value.trim(),
      formaal: formaal.value.trim(),
      grunnlag: grunnlag.value,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.journal.sak.tittel") }),
    el("p", { class: "muted", text: t("ui.journal.sak.hvorfor") }),
    skjema, utfall);
}


// POSTSKJEMAET. MODULENS SKARPESTE FLATE.
//
// PERSONENE LEGGES INN FØR POSTEN KAN REGISTRERES, og hver av dem må ha
// en slettefrist. Knappen er død til minst én står der. Det er ikke en
// validering flaten fant på: døra skriver posten og personene i SAMME
// setning, så en journalpost med navngitte privatpersoner ikke kan
// eksistere uten slettefrister — heller ikke i et vindu mellom to kall.
//
// BARE GYLDIGE KILDEVERSJONER TILBYS. Døra nekter mot en avviklet, og
// en knapp som alltid feiler er verre enn en valgmulighet som ikke
// finnes. Arkivet står fortsatt i tabellen over — det er BRUKEN som er
// stengt, ikke minnet.
function postskjema(ctx, last, kilder, saker, krav, kvitter) {
  const utfall = el("p", { "aria-live": "polite" });
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const gyldige = kilder.filter((k) => k.gyldig_naa === true);
  const sak = el("select", { id: "jo-p-sak", name: "sak",
    required: true });
  sak.append(el("option", { value: "",
    text: t("ui.journal.post.velg_sak") }));
  for (const s of saker) {
    sak.append(el("option", { value: s.sak_id, text: s.tittel }));
  }
  const kilde = el("select", { id: "jo-p-kilde", name: "kilde",
    required: true });
  kilde.append(el("option", { value: "",
    text: t("ui.journal.post.velg_kilde") }));
  for (const k of gyldige) {
    kilde.append(el("option", { value: k.kilde_id,
      text: `${k.organ} · ${t(FORMATTEKST[k.format] || k.format)}`
            + ` ${k.versjon}` }));
  }
  const journalnr = el("input", { id: "jo-p-nr", name: "nr",
    type: "text", required: true, maxlength: "200" });
  const journaldato = el("input", { id: "jo-p-dato", name: "dato",
    type: "date", required: true });
  const tittel = el("input", { id: "jo-p-tittel", name: "tittel",
    type: "text", required: true, maxlength: "500" });
  const formaal = el("textarea", { id: "jo-p-formaal",
    name: "formaal", required: true, minlength: "16",
    maxlength: "4000", rows: "2" });
  const hentetAv = el("input", { id: "jo-p-hentet", name: "hentet",
    type: "text", required: true, maxlength: "500" });
  const hentetDato = el("input", { id: "jo-p-hdato", name: "hdato",
    type: "date", required: true });

  // PERSONFELTENE.
  const pnavn = el("input", { id: "jo-p-pnavn", name: "pnavn",
    type: "text", maxlength: "500" });
  const prolle = el("select", { id: "jo-p-prolle", name: "prolle" });
  for (const r of ROLLER) {
    prolle.append(el("option", { value: r,
      text: t(ROLLETEKST[r] || r) }));
  }
  const pfrist = el("input", { id: "jo-p-pfrist", name: "pfrist",
    type: "date" });
  // TAKET SETTES SOM `max`. Døra nekter uansett; dette er
  // vennligheten, ikke gjerdet — og taket kommer fra svaret.
  if (krav && krav.sletteplan_maks_dogn) {
    const d = new Date();
    d.setDate(d.getDate() + krav.sletteplan_maks_dogn);
    pfrist.max = ilokalDato(d);
  }
  const leggTil = el("button", { type: "button", disabled: true,
    text: t("ui.journal.knapp.legg_til_person") });
  const personliste = el("ul", { class: "kv-liste" });
  // DEN TOMME LISTA SIER FRA UTENFOR `ul`-en: en rolle på en `li`
  // overstyrer listitem-rollen (eiers funn 4/9).
  const persontomt = el("p", { role: "alert",
    text: t("ui.journal.post.ingen_personer") });
  const knapp = el("button", { type: "submit", disabled: true,
    text: t("ui.journal.knapp.registrer_post") });
  const personer = [];

  const tegnPersoner = () => {
    sett(personliste, ...personer.map((p) => el("li", {},
      el("strong", { text: p.navn }), " ",
      el("span", { text: t(ROLLETEKST[p.rolle] || p.rolle) }),
      " — ", el("span", { text: p.slettefrist }))));
    personliste.hidden = personer.length === 0;
    persontomt.hidden = personer.length > 0;
  };
  const vurder = () => {
    leggTil.disabled = !pnavn.value.trim() || !pfrist.value;
    // POSTEN KAN IKKE SENDES UTEN MINST ÉN PERSON MED SLETTEFRIST.
    knapp.disabled = !sak.value || !kilde.value
      || !journalnr.value.trim() || !journaldato.value
      || !tittel.value.trim() || formaal.value.trim().length < 16
      || !hentetAv.value.trim() || !hentetDato.value
      || personer.length === 0;
  };
  for (const k of [sak, kilde, journalnr, journaldato, tittel,
                   formaal, hentetAv, hentetDato, pnavn, pfrist]) {
    k.addEventListener("change", vurder);
    k.addEventListener("input", vurder);
  }
  leggTil.addEventListener("click", () => {
    personer.push({ navn: pnavn.value.trim(), rolle: prolle.value,
                    slettefrist: pfrist.value });
    pnavn.value = ""; pfrist.value = "";
    tegnPersoner(); vurder();
  });
  tegnPersoner();

  const deler = [];
  if (!gyldige.length) {
    deler.push(el("p", { role: "alert",
      text: t("ui.journal.post.ingen_gyldige") }));
  } else if (!saker.length) {
    deler.push(el("p", { role: "alert",
      text: t("ui.journal.post.ingen_saker") }));
  } else {
    deler.push(
      felt("jo-p-sak", "ui.journal.post.sak", sak, null),
      felt("jo-p-kilde", "ui.journal.post.kilde", kilde,
           "ui.journal.post.kilde_hjelp"),
      felt("jo-p-nr", "ui.journal.post.journalnr", journalnr, null),
      felt("jo-p-dato", "ui.journal.post.journaldato", journaldato,
           null),
      felt("jo-p-tittel", "ui.journal.post.dokument", tittel, null),
      felt("jo-p-formaal", "ui.journal.post.formaal", formaal,
           "ui.journal.post.formaal_hjelp"),
      felt("jo-p-hentet", "ui.journal.post.hentet_av", hentetAv,
           "ui.journal.post.hentet_av_hjelp"),
      felt("jo-p-hdato", "ui.journal.post.hentet_dato", hentetDato,
           null),
      felt("jo-p-pnavn", "ui.journal.post.personnavn", pnavn,
           "ui.journal.post.personnavn_hjelp"),
      felt("jo-p-prolle", "ui.journal.post.personrolle", prolle, null),
      felt("jo-p-pfrist", "ui.journal.post.slettefrist", pfrist,
           "ui.journal.post.slettefrist_hjelp"),
      el("div", { class: "skjema-bunn" }, leggTil),
      persontomt, personliste,
      el("div", { class: "skjema-bunn" }, knapp));
  }
  skjema.append(...deler);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter,
    okNokkel: "ui.journal.skjema.post_ok",
    tilbakestill: () => {
      journalnr.value = ""; journaldato.value = "";
      tittel.value = ""; formaal.value = "";
      personer.length = 0; tegnPersoner();
      knapp.disabled = true;
    },
    send: (idem) => registrerJournalpost({
      sak_id: sak.value,
      kilde_id: kilde.value,
      journalnummer: journalnr.value.trim(),
      journaldato: journaldato.value,
      dokumenttittel: tittel.value.trim(),
      formaal: formaal.value.trim(),
      hentet_av_person: hentetAv.value.trim(),
      hentet_dato: hentetDato.value,
      personer,
    }, idem),
  });
  return el("div", { class: "skjemaboks" },
    el("h3", { text: t("ui.journal.post.tittel") }),
    el("p", { class: "muted", text: t("ui.journal.post.hvorfor") }),
    skjema, utfall);
}


// DAGENS DATO I BRUKERENS EGET DØGN (123s CodeRabbit-funn).
// `toISOString()` ville gitt UTC, og forskjellen slår inn ved midnatt.
export function ilokalDato(d) {
  const to = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${to(d.getMonth() + 1)}-${to(d.getDate())}`;
}


export function visJournal(hoved, ctx) {
  const hode = () => flateHode(t("ui.journal.tittel"),
    t("ui.journal.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  let apenPost = null;
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  const last = () => medStatus(hoved, ctx,
    () => hentJson("/v1/journal"),
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const s = d.sammendrag || {};
      const kilder = d.kilder || [];
      const saker = d.saker || [];
      const poster = d.poster || [];
      const skriver = harScope(ctx, "bestilling:opprett");
      const person = personpanel(ctx, last, kvitter,
                                 (id) => { apenPost = id; });
      const sluttdato = sluttdatopanel(ctx, last, kvitter);

      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.journal.oversikt.tittel") }),
        sammendrag(s),
        // HVA MODULEN IKKE GJØR, SAGT RETT UT.
        el("p", { class: "muted",
                  text: t("ui.journal.oversikt.hvorfor") }));

      const postseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.journal.poster.tittel") }));
      if (!poster.length) {
        postseksjon.append(el("p", { class: "muted",
          text: t("ui.journal.poster.ingen") }));
      } else {
        postseksjon.append(posttabell(poster, person.aapne));
      }

      const sakseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.journal.saker.tittel") }));
      if (!saker.length) {
        sakseksjon.append(el("p", { class: "muted",
          text: t("ui.journal.saker.ingen") }));
      } else {
        sakseksjon.append(saktabell(saker));
      }

      const kildeseksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.journal.kilder.tittel") }));
      if (!kilder.length) {
        kildeseksjon.append(el("p", { role: "alert",
          text: t("ui.journal.kilder.ingen") }));
      } else {
        kildeseksjon.append(kildetabell(
          kilder, skriver ? sluttdato.aapne : null));
      }

      // POSTENE FØRST. Rekkefølgen er en dom: det som haster er hvem
      // vi oppbevarer, ikke registeret de kom fra.
      const deler = [oversikt, postseksjon, person.node, sakseksjon,
                     kildeseksjon, sluttdato.node,
                     funnseksjon(ctx, last, kvitter)];
      if (skriver) {
        deler.push(postskjema(ctx, last, kilder, saker, d.krav,
                              kvitter),
                   sakskjema(ctx, last, kvitter),
                   kildeskjema(ctx, last, kvitter),
                   kravskjema(ctx, last, d.krav, kvitter));
      }
      sett(kropp, ...deler);
      if (apenPost) {
        const rad = poster.find((x) => x.post_id === apenPost);
        if (rad) person.aapne(rad); else apenPost = null;
      }
    });
  last();
}
