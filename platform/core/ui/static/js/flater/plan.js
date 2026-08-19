// Periodisk kontroll (044 §9): planskjema + planliste + tick-historikk.
//
// WCAG-kontrakten: ekte <form> med <fieldset>/<legend> per gruppe og
// synlige <label for>; RYTME SOM RADIOKNAPPER i eget fieldset — aldri
// egendefinerte klikkbokser; ukedag/månedsdag skjules med `hidden`, ikke
// bare visuelt. Valideringsfeil får aria-invalid + tilknyttet feiltekst
// og FOKUS TIL FØRSTE FEIL. Aktivering/pause/gjenopptakelse annonseres i
// role="alert" med PAUSEGRUNNEN OPPLEST. Tick-historikk i <table> med
// caption og th scope; utfall aldri kun ved farge; «neste kjøring» som
// absolutt tidspunkt MED tidssone.
import { el, sett } from "../dom.js";
import { harNokkel, t } from "../i18n.js";
import { hentJson, nyIdempotensnokkel, opprettPlan, planHandling, ApiFeil,
         UautorisertFeil } from "../api.js";
import { Tidspunkt, TomTilstand, Feiltilstand, meldAlert,
         meldLive } from "../komponenter.js";
import { flateHode } from "./felles.js";
import { harScope } from "../sitekart.js";

const RYTMER = ["daglig", "ukentlig", "manedlig"];

function feltId(navn) { return `plan-${navn}`; }

function _feil(inp, feilEl, tekst) {
  inp.setAttribute("aria-invalid", "true");
  inp.setAttribute("aria-errormessage", feilEl.id);
  feilEl.textContent = tekst;
  return inp;
}

function _nullstillFeil(form) {
  for (const e of form.querySelectorAll('[aria-invalid="true"]')) {
    e.removeAttribute("aria-invalid");
    e.removeAttribute("aria-errormessage");
  }
  for (const e of form.querySelectorAll(".skjemafeil")) {
    e.textContent = "";
  }
}

// Neste kjøring: absolutt tidspunkt i planens egen tidssone (§9) —
// avledet klientside KUN for visning; forfallet eies av basen.
//
// FORFALLET ER `time_lokal:forfallsminutt`, ikke hel time (Codex P2).
// Spredningen (019 §3.3) legger 0–59 minutter etter vindu_start, avledet
// av en sha256 av plan-id-en — noe flaten ikke kan regne ut selv, og
// derfor får servert av `plan_forfallsminutt`. Uten minuttet viste
// flaten både feil KLOKKESLETT (08:00 for et forfall 08:40) og feil DAG:
// timesammenligningen hoppet over i dag så snart timen var nådd, så
// klokka 08:05 sto en plan som skulle kjøre 08:40 i dag oppført som «i
// morgen». Faller minuttet bort (eldre svar), er 0 den trygge
// tilnærmingen — samme dag, tidligst mulig.
function nesteKjoringTekst(p) {
  const minutt = Number.isInteger(p.forfallsminutt)
    ? Math.min(Math.max(p.forfallsminutt, 0), 59) : 0;
  try {
    const naa = new Date();
    for (let d = 0; d < 62; d++) {
      const kand = new Date(naa.getTime() + d * 86400000);
      const deler = new Intl.DateTimeFormat("en-CA", {
        timeZone: p.tidssone, weekday: "short", day: "numeric",
        year: "numeric", month: "2-digit",
      }).formatToParts(kand);
      const ukedag = { Mon: 1, Tue: 2, Wed: 3, Thu: 4, Fri: 5, Sat: 6,
                       Sun: 7 }[deler.find((x) => x.type === "weekday").value];
      const dag = parseInt(deler.find((x) => x.type === "day").value, 10);
      if (p.rytme === "ukentlig" && ukedag !== p.ukedag) continue;
      if (p.rytme === "manedlig" && dag !== p.manedsdag) continue;
      const dato = `${deler.find((x) => x.type === "year").value}-${
        deler.find((x) => x.type === "month").value}-${
        String(dag).padStart(2, "0")}`;
      const tid = `${String(p.time_lokal).padStart(2, "0")}:${
        String(minutt).padStart(2, "0")}`;
      if (d === 0) {
        // Sammenlign MINUTTER, ikke timer: en plan med forfall 08:40 er
        // fortsatt i dag klokka 08:05.
        const nu = new Intl.DateTimeFormat("en-GB", {
          timeZone: p.tidssone, hour: "2-digit", minute: "2-digit",
          hour12: false,
        }).formatToParts(naa);
        const min = (v) => parseInt(nu.find((x) => x.type === v).value, 10);
        if (min("hour") * 60 + min("minute") >= p.time_lokal * 60 + minutt) {
          continue;
        }
      }
      return `${dato} ${tid} (${p.tidssone})`;
    }
  } catch { /* ukjent sone i nettleseren: vis rytmen i stedet */ }
  return `${t(`ui.plan.rytme.${p.rytme}`)} ${String(p.time_lokal)
    .padStart(2, "0")}:${String(minutt).padStart(2, "0")} (${p.tidssone})`;
}

function planSkjema(ctx, paaOpprettet) {
  // Operasjonsnøkkelen for opprettelsen, holdt av skjemaet: den overlever
  // et tapt svar (og dermed et nytt klikk), og byttes først når kroppen
  // endrer seg eller planen faktisk ble opprettet. Se submit-handleren.
  let idem = { signatur: null, nokkel: null };
  const feil = (navn) => el("p", { class: "skjemafeil",
    id: `${feltId(navn)}-feil` });
  const host = el("input", { type: "text", id: feltId("hostname"),
    autocomplete: "off", spellcheck: "false" });
  const hostFeil = feil("hostname");
  const sti = el("input", { type: "text", id: feltId("sti"), value: "/" });
  const stiFeil = feil("sti");
  const omfang = el("select", { id: feltId("omfang") },
    el("option", { value: "enkeltside", text: t("ui.plan.omfang.enkeltside") }),
    el("option", { value: "nettsted", text: t("ui.plan.omfang.nettsted") }));
  const time = el("select", { id: feltId("time") },
    ...Array.from({ length: 24 }, (_, i) =>
      el("option", { value: String(i),
        text: `${String(i).padStart(2, "0")}:00` })));
  time.value = "8";
  const tidssone = el("input", { type: "text", id: feltId("tidssone"),
    value: "Europe/Oslo", autocomplete: "off" });
  const tidssoneFeil = feil("tidssone");

  const ukedag = el("select", { id: feltId("ukedag") },
    ...[1, 2, 3, 4, 5, 6, 7].map((d) =>
      el("option", { value: String(d), text: t(`ui.plan.ukedag.${d}`) })));
  const ukedagRad = el("div", { class: "skjemarad" },
    el("label", { for: feltId("ukedag"), text: t("ui.plan.felt.ukedag") }),
    ukedag);
  const manedsdag = el("select", { id: feltId("manedsdag") },
    ...Array.from({ length: 28 }, (_, i) =>
      el("option", { value: String(i + 1), text: String(i + 1) })));
  const manedsdagRad = el("div", { class: "skjemarad" },
    el("label", { for: feltId("manedsdag"),
      text: t("ui.plan.felt.manedsdag") }),
    manedsdag);
  // `hidden`, ikke bare visuelt (§9): et skjult felt skal være borte for
  // ALLE brukere, også skjermleserens.
  ukedagRad.hidden = true;
  manedsdagRad.hidden = true;

  const radioer = RYTMER.map((r) => {
    const inp = el("input", { type: "radio", name: "plan-rytme",
      id: feltId(`rytme-${r}`), value: r });
    if (r === "daglig") inp.checked = true;
    inp.addEventListener("change", () => {
      ukedagRad.hidden = r !== "ukentlig";
      manedsdagRad.hidden = r !== "manedlig";
    });
    return el("div", { class: "radiorad" }, inp,
      el("label", { for: feltId(`rytme-${r}`),
        text: t(`ui.plan.rytme.${r}`) }));
  });

  const knapp = el("button", { class: "knapp primar", type: "submit",
    text: t("ui.plan.opprett") });
  const status = el("p", { role: "status", class: "muted" });

  const form = el("form", { novalidate: "" },
    el("fieldset", {},
      el("legend", { text: t("ui.plan.gruppe.maal") }),
      el("div", { class: "skjemarad" },
        el("label", { for: feltId("hostname"),
          text: t("ui.plan.felt.hostname") }), host, hostFeil),
      el("div", { class: "skjemarad" },
        el("label", { for: feltId("sti"), text: t("ui.plan.felt.sti") }),
        sti, stiFeil),
      el("div", { class: "skjemarad" },
        el("label", { for: feltId("omfang"),
          text: t("ui.plan.felt.omfang") }), omfang)),
    el("fieldset", {},
      el("legend", { text: t("ui.plan.gruppe.rytme") }),
      ...radioer, ukedagRad, manedsdagRad,
      el("div", { class: "skjemarad" },
        el("label", { for: feltId("time"), text: t("ui.plan.felt.time") }),
        time),
      el("div", { class: "skjemarad" },
        el("label", { for: feltId("tidssone"),
          text: t("ui.plan.felt.tidssone") }), tidssone, tidssoneFeil)),
    knapp, status);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    _nullstillFeil(form);
    const feilene = [];
    const h = host.value.trim().toLowerCase();
    if (!/^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/.test(h)) {
      feilene.push(_feil(host, hostFeil, t("ui.plan.feil.hostname")));
    }
    if (!/^\/(?:[A-Za-z0-9._~-]+\/?)*$/.test(sti.value.trim())) {
      feilene.push(_feil(sti, stiFeil, t("ui.plan.feil.sti")));
    }
    if (!tidssone.value.trim()) {
      feilene.push(_feil(tidssone, tidssoneFeil, t("ui.plan.feil.tidssone")));
    }
    if (feilene.length) {
      feilene[0].focus();     // §9: fokus til FØRSTE feil
      return;
    }
    const rytme = form.querySelector('input[name="plan-rytme"]:checked').value;
    const kropp = {
      bestillingstype: "kontroll.wcag.nettsted",
      hostname: h, sti: sti.value.trim(), kravsett: "wcag21_aa",
      omfang: omfang.value,
      maks_sider: omfang.value === "nettsted" ? 25 : 1,
      rytme, time_lokal: parseInt(time.value, 10),
      tidssone: tidssone.value.trim(),
    };
    if (rytme === "ukentlig") kropp.ukedag = parseInt(ukedag.value, 10);
    if (rytme === "manedlig") kropp.manedsdag = parseInt(manedsdag.value, 10);
    // Operasjonsnøkkelen er STABIL så lenge kroppen er uendret (samme
    // konvensjon som policyeditoren): mister vi svaret på en opprettelse
    // serveren ALT har committet, skal neste klikk REPLAYE den planen —
    // ikke lage plan nummer to med identiske parametre og egen kvotebruk.
    // Retter brukeren skjemaet i mellomtiden, er det en ANNEN plan, og
    // den får en fersk nøkkel i stedet for en konflikt.
    const signatur = JSON.stringify(kropp);
    if (idem.signatur !== signatur) {
      idem = { signatur, nokkel: nyIdempotensnokkel() };
    }
    form.setAttribute("aria-busy", "true");
    knapp.disabled = true;
    status.textContent = t("ui.plan.oppretter");
    try {
      await opprettPlan(kropp, idem.nokkel);
      idem = { signatur: null, nokkel: null };   // neste plan er en ny sak
      meldAlert(t("ui.plan.opprettet_alert"));
      status.textContent = "";
      form.reset();
      if (paaOpprettet) paaOpprettet();
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      status.textContent = "";
      const kode = e instanceof ApiFeil ? e.kode : null;
      meldLive(t(`ui.plan.feilkode.${kode}`, t("ui.plan.opprett_feilet")));
    } finally {
      form.removeAttribute("aria-busy");
      knapp.disabled = false;
    }
  });
  return form;
}

function statusTekst(p) {
  const base = t(`ui.plan.status.${p.status}`, p.status);
  if (p.status === "pauset" && p.pause_aarsak) {
    return `${base} — ${t(`ui.plan.pause.${p.pause_aarsak}`,
                          p.pause_aarsak)}`;
  }
  return base;
}

export function visPlan(hoved, ctx) {
  const liste = el("div", { class: "planliste", "aria-busy": "true" });
  const historikk = el("div", { class: "planhistorikk" });
  const kanSkrive = harScope(ctx, "plan:opprett");

  let generasjon = 0;

  async function handling(p, hva, bekreftTekst) {
    try {
      await planHandling(p.plan_id, hva);
      // §9: overgangen annonseres i role="alert" — grunnen/utfallet
      // leses opp, ikke bare vises.
      meldAlert(t(`ui.plan.alert.${hva}`));
      last();
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      meldLive(t("ui.plan.handling_feilet"));
    }
  }

  function visHistorikk(p) {
    historikk.setAttribute("aria-busy", "true");
    hentJson(`/v1/plan/${p.plan_id}/historikk`).then((d) => {
      historikk.removeAttribute("aria-busy");
      const rader = d.tick || [];
      const tab = el("table", { class: "datatabell" },
        el("caption", { text: `${t("ui.plan.historikk_for")} ${
          p.parametre?.hostname || p.plan_id}` }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.plan.kol.vindu") }),
          el("th", { scope: "col", text: t("ui.plan.kol.utfall") }),
          el("th", { scope: "col", text: t("ui.plan.kol.oppdrag") }))),
        el("tbody", {}, ...rader.map((r) => el("tr", {},
          el("th", { scope: "row" }, Tidspunkt(r.vindu_start)),
          // Utfall som TEKST — aldri kun farge (§9). `vist_utfall` er hva
          // som gjelder NÅ: et oppdrag som senere ble avvist manuelt sto
          // ellers som «Bestilt» for alltid, selv om pausesveipen hadde
          // sett nei-et. Ticket selv er urørt evidens.
          el("td", { text: ((u) => t(`ui.plan.utfall.${u}`, u))(
            r.vist_utfall || r.utfall) }),
          el("td", { text: r.oppdrag_id ? `#${r.oppdrag_id}` : "—" })))));
      sett(historikk,
        el("h3", { text: t("ui.plan.historikk") }),
        rader.length
          ? el("div", { class: "tabellrull" }, tab)
          : el("p", { class: "muted", text: t("ui.plan.ingen_tick") }));
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      historikk.removeAttribute("aria-busy");
      sett(historikk, Feiltilstand({ paaProvIgjen: () => visHistorikk(p) }));
    });
  }

  function tegn(planer) {
    liste.removeAttribute("aria-busy");
    if (!planer.length) {
      sett(liste, TomTilstand({ tittel: t("ui.plan.tom_tittel"),
        tekst: t("ui.plan.tom_tekst") }));
      return;
    }
    const tab = el("table", { class: "datatabell" },
      el("caption", { text: t("ui.plan.caption") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col", text: t("ui.plan.kol.maal") }),
        el("th", { scope: "col", text: t("ui.plan.kol.rytme") }),
        el("th", { scope: "col", text: t("ui.plan.kol.neste") }),
        el("th", { scope: "col", text: t("ui.plan.kol.status") }),
        el("th", { scope: "col", text: t("ui.plan.kol.handlinger") }))),
      el("tbody", {}, ...planer.map((p) => {
        const handlinger = el("td", { class: "behandling-knapper" });
        const knapp = (tekst, hva) => {
          const k = el("button", { class: "knapp", type: "button",
            text: tekst });
          k.addEventListener("click", () => handling(p, hva));
          return k;
        };
        if (kanSkrive) {
          if (p.status === "utkast") {
            handlinger.append(knapp(t("ui.plan.aktiver"), "aktiver"));
          }
          if (p.status === "pauset") {
            handlinger.append(knapp(t("ui.plan.gjenoppta"), "gjenoppta"));
          }
          if (p.status !== "stanset") {
            handlinger.append(knapp(t("ui.plan.stans"), "stans"));
          }
        }
        const vis = el("button", { class: "knapp", type: "button",
          text: t("ui.plan.vis_historikk") });
        vis.addEventListener("click", () => visHistorikk(p));
        handlinger.append(vis);
        return el("tr", {},
          el("th", { scope: "row",
            text: `${p.parametre?.hostname || "?"}${
              p.parametre?.sti || ""}` }),
          el("td", { text: t(`ui.plan.rytme.${p.rytme}`, p.rytme) }),
          el("td", { text: p.status === "aktiv"
            ? nesteKjoringTekst(p) : "—" }),
          el("td", { text: statusTekst(p) }),
          handlinger);
      })));
    sett(liste, el("div", { class: "tabellrull" }, tab));
  }

  function last() {
    const min = ++generasjon;
    liste.setAttribute("aria-busy", "true");
    hentJson("/v1/plan").then((d) => {
      if (min !== generasjon) return;
      tegn(d.planer || []);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (min !== generasjon) return;
      liste.removeAttribute("aria-busy");
      sett(liste, Feiltilstand({ paaProvIgjen: last }));
    });
  }

  sett(hoved,
    ...flateHode(t("ui.plan.tittel"), t("ui.plan.under")),
    ...(kanSkrive ? [el("h3", { text: t("ui.plan.ny") }),
                     planSkjema(ctx, last)] : []),
    el("h3", { text: t("ui.plan.dine") }),
    liste, historikk);
  last();
}
