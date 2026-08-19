// Versjonshistorikk, diff og opphav (047, klarsignal §6/§7) — EN visning,
// to innganger.
//
// Rutene bak (`GET /v1/policy/{id}/versjoner` og `.../diff`) krever
// `policy:read`, ikke `policy:write`. Visningen bodde likevel bare i
// policyadmin-flaten, og den ruten legges kun til for den som kan FORVALTE
// policy (`policy:write` eller `policy:activate`) — inngangsknappen sto
// dessuten inne i `aktivePolicyerSeksjon`, som returnerer tomt uten
// `policy:write`. `leser`, `admin` og `sikkerhet` har `policy:read` og kunne
// altså kalle endepunktene, men hadde ingen vei dit i flaten (Codex P2). Det
// er samme regel som ellers i sitekartet: en flate hører til det svakeste
// scopet API-et bak den krever.
//
// Modulen eier RENDRINGEN, ikke navigasjonen: hver vert gir sin egen
// tilbakevei, sin egen ferskhetsprøve (`erGyldig`) og — bare der den er
// lovlig — sin egen rullbakk-handling. Uten `paaRullbakk` finnes ikke
// handlingskolonnen i det hele tatt; knappen skjules ikke, den lages ikke.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, UautorisertFeil } from "../api.js";
import { Tidspunkt, TomTilstand, Feiltilstand } from "../komponenter.js";
import { flateHode, fokuserOverskrift } from "./felles.js";

// «Ingen attestanter» har mer enn én grunn, og de betyr ulike ting (047,
// Codex P2). En `bootstrap`-rad er lagt inn av oppsettsveien — den HAR
// ingen runde, og skal ikke se ut som en versjon vi bare mistet sporet av.
// `historisk` er raden som lå der da lineagen kom.
export function attestantTekst(v) {
  if (!v.aktivert_av_operasjon) {
    if (v.aktiveringskilde === "bootstrap") {
      return t("ui.historikk.kilde_bootstrap");
    }
    return t("ui.historikk.attestanter_ubundet");
  }
  return (v.attestanter || []).join(", ")
    || t("ui.historikk.attestanter_ubundet");
}

export function tegnDiff(rot, d) {
  const endringer = (d.diff && d.diff.endringer) || [];
  // Retningen I ORD FØRST (port 40): risikoklassen er overskriften.
  const retning = t(`ui.historikk.retning.${d.risikoklasse}`, d.risikoklasse);
  sett(rot,
    el("h4", { text: t("ui.historikk.diff_resultat")
      .replace("{retning}", retning)
      .replace("{fra}", d.fra).replace("{til}", d.til) }),
    endringer.length
      ? el("ul", { class: "diffliste" }, ...endringer.map((e2) =>
          el("li", { text: `${t(`ui.historikk.endring.${e2.type}`, e2.type)} ${
            e2.sti}` +
            (e2.type === "endret"
              ? `: ${JSON.stringify(e2.fra)} → ${JSON.stringify(e2.til)}`
              : e2.type === "fjernet"
                ? `: ${JSON.stringify(e2.fra)}`
                : `: ${JSON.stringify(e2.til)}`) })))
      : el("p", { class: "muted", text: t("ui.historikk.diff_tom") }));
}

function historikkTabell(policyId, versjoner, paaRullbakk) {
  if (!versjoner.length) {
    return TomTilstand({ tittel: t("ui.historikk.tom_tittel"),
                         tekst: t("ui.historikk.tom_tekst") });
  }
  const rader = versjoner.map((v) => {
    const celler = [
      el("th", { scope: "row", text: v.versjon }),
      // Aktiv versjon markeres med TEKST (port 40), aldri kun stil.
      el("td", { text: v.aktiv ? t("ui.historikk.aktiv_na") : "" }),
      el("td", {}, v.aktivert_ts ? Tidspunkt(v.aktivert_ts)
                                 : Tidspunkt(v.opprettet)),
      el("td", { text: attestantTekst(v) }),
      el("td", { text: v.rollback_av_versjon
        ? t("ui.historikk.rullbakk_fra").replace("{n}", v.rollback_av_versjon)
        : "" }),
    ];
    if (paaRullbakk) {
      const handlinger = el("td", { class: "behandling-knapper" });
      const rb = el("button", { class: "knapp liten", type: "button",
        text: t("ui.historikk.rullbakk") });
      rb.addEventListener("click", () => paaRullbakk(v));
      handlinger.append(rb);
      celler.push(handlinger);
    }
    return el("tr", {}, ...celler);
  });

  const kolonner = [
    el("th", { scope: "col", text: t("ui.historikk.kol.versjon") }),
    el("th", { scope: "col", text: t("ui.historikk.kol.status") }),
    el("th", { scope: "col", text: t("ui.historikk.kol.tid") }),
    el("th", { scope: "col", text: t("ui.historikk.kol.attestanter") }),
    el("th", { scope: "col", text: t("ui.historikk.kol.opphav") }),
  ];
  if (paaRullbakk) {
    kolonner.push(el("th", { scope: "col",
      text: t("ui.historikk.kol.handlinger") }));
  }
  return el("div", { class: "tabellrull" }, el("table", { class: "datatabell" },
    el("caption", { text: t("ui.historikk.caption")
      .replace("{policy}", policyId) }),
    el("thead", {}, el("tr", {}, ...kolonner)),
    el("tbody", {}, ...rader)));
}

// Diff mellom to VILKÅRLIGE versjoner — velgere er <select> med <label>
// (port §7), resultatet en liste med tekst, aldri to <pre>.
function diffSeksjonFor(policyId, versjoner, ctx, erGyldig) {
  if (versjoner.length < 2) return null;
  const diffUt = el("div", { class: "historikk-diff" });
  const fra = el("select", { id: "hist-fra" },
    ...versjoner.map((v) => el("option", { value: v.versjon,
                                           text: v.versjon })));
  const til = el("select", { id: "hist-til" },
    ...versjoner.map((v) => el("option", { value: v.versjon,
                                           text: v.versjon })));
  fra.value = versjoner[1].versjon;
  til.value = versjoner[0].versjon;
  const knapp = el("button", { class: "knapp", type: "button",
    text: t("ui.historikk.vis_diff") });
  knapp.addEventListener("click", () => {
    sett(diffUt, el("p", { class: "muted", text: t("ui.laster") }));
    hentJson(`/v1/policy/${policyId}/diff?fra=${
      encodeURIComponent(fra.value)}&til=${
      encodeURIComponent(til.value)}`).then((d) => {
      if (!erGyldig()) return;
      tegnDiff(diffUt, d);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (!erGyldig()) return;
      sett(diffUt, Feiltilstand({}));
    });
  });
  return el("section", { class: "historikk-diffvelger",
    "aria-label": t("ui.historikk.diff_tittel") },
    el("h3", { text: t("ui.historikk.diff_tittel") }),
    el("div", { class: "skjemarad" },
      el("label", { for: "hist-fra", text: t("ui.historikk.fra") }), fra),
    el("div", { class: "skjemarad" },
      el("label", { for: "hist-til", text: t("ui.historikk.til") }), til),
    knapp, diffUt);
}

// Tegn hele historikkskjermen i `hoved`. `paaRullbakk` er null for en ren
// leseøkt; da finnes handlingskolonnen ikke.
export function tegnHistorikkflate(hoved, ctx, {
  policyId, versjoner, tilbake, paaRullbakk = null,
  erGyldig = () => true,
}) {
  const tilbakeKnapp = el("button", { class: "knapp", type: "button",
    text: t("ui.historikk.tilbake") });
  tilbakeKnapp.addEventListener("click", tilbake);
  sett(hoved,
    ...flateHode(t("ui.historikk.tittel").replace("{policy}", policyId),
                 t("ui.historikk.under")),
    tilbakeKnapp,
    historikkTabell(policyId, versjoner, paaRullbakk),
    diffSeksjonFor(policyId, versjoner, ctx, erGyldig));
  fokuserOverskrift(hoved);
}
