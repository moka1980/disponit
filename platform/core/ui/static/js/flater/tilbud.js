// M-26 tilbudsregisteret (169, ARC B tilbud PR 1) — flaten ved siden av
// prisboka. Et tilbud er en avskrift av boka på en dato, mot én kunde,
// med standardklausulene bundet. Flaten regner ingen pris: linjene viser
// tallene DØRA satte (listepris, enhetspris, sum). Adressen vises aldri —
// bare masken. Ingen «send»-knapp: sendingen er plattformens arm (PR 2–5).
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  UautorisertFeil, avgjorTilbud, hentJson, lagTilbud, nyIdempotensnokkel,
} from "../api.js";
import { meldLive } from "../komponenter.js";
import { harScope } from "../sitekart.js";
import { flateHode, medStatus } from "./felles.js";

export function belopTekst(ore) {
  if (!Number.isInteger(ore)) return "—";
  const neg = ore < 0 ? "-" : "";
  const abs = Math.abs(ore);
  const kr = Math.trunc(abs / 100);
  const rest = abs - kr * 100;
  return `${neg}${kr},${String(rest).padStart(2, "0")}`;
}

export function tilOre(verdi) {
  const s = String(verdi ?? "").trim().replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return null;
  const [kr, des = ""] = s.split(".");
  return Number(kr) * 100 + Number((des + "00").slice(0, 2));
}

export function faktaTekst(tilbud) {
  // De to faktaene policyen bygger på — regnet av registeret, sagt som ord.
  const deler = [
    t(tilbud.priser_fra_boka ? "ui.tilbud.fakta.priser_ok"
                             : "ui.tilbud.fakta.priser_avvik"),
    t(tilbud.klausuler_uendret ? "ui.tilbud.fakta.klausuler_ok"
                               : "ui.tilbud.fakta.klausuler_endret"),
  ];
  return deler.join(" · ");
}

function tilbudsrad(x, apneDetalj) {
  const rad = el("tr", {});
  rad.append(el("th", { scope: "row", class: "celle-tekst", text: x.kunde_navn }));
  rad.append(el("td", { text: x.kunde_maske }));
  rad.append(el("td", { text: x.tilbudsdato }));
  rad.append(el("td", { text: x.gyldig_til }));
  rad.append(el("td", { class: "celle-tall", text: belopTekst(x.sum_ore) }));
  rad.append(el("td", { text: t(`ui.tilbud.status.${x.status}`) }));
  rad.append(el("td", {}, el("span", { text: faktaTekst(x) })));
  const knapp = el("button", { type: "button", text: t("ui.tilbud.knapp.apne") });
  knapp.addEventListener("click", () => apneDetalj(x));
  rad.append(el("td", {}, knapp));
  return rad;
}

function tilbudTabell(liste, apneDetalj) {
  const tabell = el("table", { class: "tabell" });
  const hode = el("tr", {});
  for (const k of ["kunde", "adresse", "dato", "gyldig_til", "sum", "status",
                   "fakta", "handling"]) {
    hode.append(el("th", { scope: "col", text: t(`ui.tilbud.kolonne.${k}`) }));
  }
  tabell.append(el("thead", {}, hode));
  const kropp = el("tbody", {});
  for (const x of liste) kropp.append(tilbudsrad(x, apneDetalj));
  tabell.append(kropp);
  return tabell;
}

function felt(id, tekst, kontroll, hjelp) {
  const boks = el("div", { class: "felt" }, el("label", { for: id, text: t(tekst) }),
                  kontroll);
  if (hjelp) boks.append(el("p", { class: "muted", text: t(hjelp) }));
  return boks;
}

function skjemaramme(ctx, last, { skjema, knapp, utfall, send, tilbakestill,
                                  okNokkel, kvitter }) {
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
        text: e && e.status === 409 ? t("ui.tilbud.feil.tilstand")
                                    : t("ui.tilbud.feil.generell") }));
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

function detaljpanel(ctx, last, kvitter, settApen) {
  const boks = el("div", { class: "skjemaboks" });
  const innhold = el("div", {});
  const utfall = el("p", { "aria-live": "polite" });
  const merkelinje = el("p", { class: "muted" });
  const linjer = el("div", {});
  const klausuler = el("div", {});
  let gjeldende = null;

  const dSkjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const dStatus = el("select", { id: "tb-d-status", name: "status", required: true });
  // TO DOMMER, OG «SENDT» ER IKKE EN AV DEM: sendingen er plattformens.
  for (const s of ["godkjent", "forkastet"]) {
    dStatus.append(el("option", { value: s, text: t(`ui.tilbud.status.${s}`) }));
  }
  const dKnapp = el("button", { type: "submit", text: t("ui.tilbud.knapp.avgjor") });
  dSkjema.append(felt("tb-d-status", "ui.tilbud.skjema.dom", dStatus,
                      "ui.tilbud.skjema.dom_hjelp"),
                 el("div", { class: "skjema-bunn" }, dKnapp));
  skjemaramme(ctx, last, {
    skjema: dSkjema, knapp: dKnapp, utfall, kvitter,
    okNokkel: "ui.tilbud.skjema.dom_ok",
    send: (idem) => avgjorTilbud(gjeldende.tilbud_id, dStatus.value, idem),
    tilbakestill: () => { innhold.hidden = true; settApen(null); },
  });

  innhold.append(el("h3", { text: t("ui.tilbud.detalj.tittel") }), merkelinje,
                 el("h4", { text: t("ui.tilbud.detalj.linjer") }), linjer,
                 el("h4", { text: t("ui.tilbud.detalj.klausuler") }), klausuler);
  if (harScope(ctx, "bestilling:opprett")) {
    innhold.append(el("h4", { text: t("ui.tilbud.knapp.avgjor") }), dSkjema);
  }
  boks.append(innhold, utfall);
  innhold.hidden = true;

  return {
    node: boks,
    async apne(x) {
      gjeldende = x;
      settApen(x.tilbud_id);
      sett(utfall); sett(linjer); sett(klausuler);
      merkelinje.textContent = `${x.kunde_navn} · ${x.kunde_maske} · `
        + `${belopTekst(x.sum_ore)} ${x.valuta} · ${faktaTekst(x)}`;
      dKnapp.disabled = x.status !== "utkast";
      innhold.hidden = false;
      let d;
      try {
        d = await hentJson(`/v1/tilbud/${encodeURIComponent(x.tilbud_id)}`);
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        sett(utfall, el("span", { role: "alert", text: t("ui.tilbud.feil.generell") }));
        return;
      }
      if (d.innledning) linjer.append(el("p", { text: d.innledning }));
      const tab = el("table", { class: "tabell" });
      const hode = el("tr", {});
      for (const k of ["produkt", "antall", "listepris", "enhetspris", "linjesum"]) {
        hode.append(el("th", { scope: "col", text: t(`ui.tilbud.linje.${k}`) }));
      }
      tab.append(el("thead", {}, hode));
      const tb = el("tbody", {});
      for (const l of d.linjer || []) {
        tb.append(el("tr", {},
          el("th", { scope: "row", class: "celle-tekst",
                     text: `${l.produktkode} · ${l.produktnavn}` }),
          el("td", { class: "celle-tall", text: `${l.antall} ${l.enhet}` }),
          el("td", { class: "celle-tall", text: belopTekst(l.listepris_ore) }),
          el("td", { class: "celle-tall", text: belopTekst(l.enhetspris_ore) }),
          el("td", { class: "celle-tall", text: belopTekst(l.linjesum_ore) })));
      }
      tab.append(tb);
      linjer.append(tab);
      const ul = el("ul", {});
      for (const k of d.klausuler || []) {
        ul.append(el("li", {}, el("strong", { text: `${k.kode} v${k.versjon} · ${k.tittel}: ` }),
                             el("span", { text: k.tekst })));
      }
      if (!(d.klausuler || []).length) {
        klausuler.append(el("p", { class: "muted", text: t("ui.tilbud.detalj.ingen_klausuler") }));
      } else {
        klausuler.append(ul);
      }
    },
  };
}

function nyttSkjema(ctx, last, kvitter, produkter) {
  const skjema = el("form", { class: "kv-skjema kv-skjema-rutenett" });
  const navn = el("input", { id: "tb-navn", name: "kunde_navn", type: "text",
                             required: true, maxlength: 200 });
  const epost = el("input", { id: "tb-epost", name: "kunde_epost", type: "email",
                              required: true, maxlength: 254 });
  const gyldig = el("input", { id: "tb-gyldig", name: "gyldig_til", type: "date",
                               required: true });
  const innledning = el("textarea", { id: "tb-innledning", name: "innledning",
                                      rows: 3, maxlength: 4000 });
  const linjeboks = el("div", { id: "tb-linjer" });
  const utfall = el("p", { "aria-live": "polite" });
  const aktive = produkter.filter((p) => p.aktiv);

  function nyLinje() {
    const rad = el("div", { class: "kv-skjema-rutenett tb-linje" });
    const n = linjeboks.children.length + 1;
    const produkt = el("select", { id: `tb-l${n}-produkt`, required: true });
    for (const p of aktive) {
      produkt.append(el("option", { value: p.produkt_id, text: `${p.kode} · ${p.navn}` }));
    }
    const antall = el("input", { id: `tb-l${n}-antall`, type: "number", min: 1,
                                 step: 1, value: 1, required: true });
    const pris = el("input", { id: `tb-l${n}-pris`, type: "text", inputmode: "decimal" });
    rad.append(felt(`tb-l${n}-produkt`, "ui.tilbud.linje.produkt", produkt),
               felt(`tb-l${n}-antall`, "ui.tilbud.linje.antall", antall),
               felt(`tb-l${n}-pris`, "ui.tilbud.linje.enhetspris", pris,
                    "ui.tilbud.skjema.pris_hjelp"));
    linjeboks.append(rad);
  }
  nyLinje();
  const leggTil = el("button", { type: "button", text: t("ui.tilbud.knapp.ny_linje") });
  leggTil.addEventListener("click", nyLinje);
  const knapp = el("button", { type: "submit", text: t("ui.tilbud.knapp.opprett") });
  skjema.append(
    felt("tb-navn", "ui.tilbud.skjema.kunde_navn", navn),
    felt("tb-epost", "ui.tilbud.skjema.kunde_epost", epost, "ui.tilbud.skjema.epost_hjelp"),
    felt("tb-gyldig", "ui.tilbud.skjema.gyldig_til", gyldig),
    felt("tb-innledning", "ui.tilbud.skjema.innledning", innledning),
    linjeboks, leggTil,
    el("div", { class: "skjema-bunn" }, knapp), utfall);
  skjemaramme(ctx, last, {
    skjema, knapp, utfall, kvitter, okNokkel: "ui.tilbud.skjema.opprett_ok",
    send: (idem) => {
      const linjer = [...linjeboks.querySelectorAll(".tb-linje")].map((r) => {
        const l = { produkt_id: r.querySelector("select").value,
                    antall: Number(r.querySelector("input[type=number]").value) };
        const p = r.querySelector("input[type=text]").value.trim();
        if (p) {
          const ore = tilOre(p);
          if (ore === null) throw Object.assign(new Error("pris"), { status: 400 });
          l.enhetspris_ore = ore;
        }
        return l;
      });
      const kropp = { kunde_navn: navn.value, kunde_epost: epost.value,
                      gyldig_til: gyldig.value, linjer };
      if (innledning.value.trim()) kropp.innledning = innledning.value;
      return lagTilbud(kropp, idem);
    },
    tilbakestill: () => { skjema.reset(); sett(linjeboks); nyLinje(); },
  });
  return el("section", { class: "kpi-kort" },
    el("h2", { text: t("ui.tilbud.skjema.tittel") }), skjema);
}

function sammendrag(s) {
  const dl = el("dl", { class: "kpi-liste" });
  for (const [k, v] of [["utkast", s.utkast], ["godkjente", s.godkjente],
                        ["sum_godkjent", belopTekst(s.sum_godkjent_ore)],
                        ["vist", s.vist]]) {
    dl.append(el("dt", { text: t(`ui.tilbud.oversikt.${k}`) }),
              el("dd", { text: String(v ?? "—") }));
  }
  return dl;
}

export function visTilbud(hoved, ctx) {
  const hode = () => flateHode(t("ui.tilbud.tittel"), t("ui.tilbud.undertittel"));
  sett(hoved, ...hode());
  const kvittering = el("p", { class: "muted" });
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kvittering, kropp);
  const kvitter = (tekst) => { kvittering.textContent = tekst; };
  let apenRad = null;
  const settApen = (id) => { apenRad = id; };
  const last = () => medStatus(hoved, ctx,
    async () => {
      const d = await hentJson("/v1/tilbud");
      let produkter = [];
      if (harScope(ctx, "bestilling:opprett")) {
        try { produkter = (await hentJson("/v1/prisbok")).produkter || []; }
        catch (e) { if (e instanceof UautorisertFeil) throw e; }
      }
      return { ...d, produkter };
    },
    (d) => {
      sett(hoved, ...hode(), kvittering, kropp);
      const liste = d.tilbud || [];
      const detalj = detaljpanel(ctx, last, kvitter, settApen);
      const oversikt = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tilbud.oversikt.tittel") }), sammendrag(d.sammendrag || {}));
      const seksjon = el("section", { class: "kpi-kort" },
        el("h2", { text: t("ui.tilbud.liste.tittel") }));
      if (!liste.length) {
        seksjon.append(el("p", { class: "muted", text: t("ui.tilbud.liste.ingen") }));
      } else {
        seksjon.append(tilbudTabell(liste, detalj.apne));
      }
      const deler = [oversikt, seksjon, detalj.node];
      if (harScope(ctx, "bestilling:opprett")) {
        deler.push(nyttSkjema(ctx, last, kvitter, d.produkter || []));
      }
      sett(kropp, ...deler);
      if (apenRad) {
        const rad = liste.find((x) => x.tilbud_id === apenRad);
        if (rad) detalj.apne(rad); else apenRad = null;
      }
    });
  last();
}
