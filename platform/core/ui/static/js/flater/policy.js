// Policy — les policyen som håndheves (read-only i v1; redigering er egen,
// versjonert flyt). Menneskelesbar visning av den lukkede PolicyDTO-en.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, IkkeFunnetFeil } from "../api.js";
import { VarselBanner, TomTilstand } from "../komponenter.js";
import { medStatus, flateHode } from "./felles.js";

function grenserNode(g) {
  if (!g) return el("span", { class: "muted", text: t("ui.policy.ingen_grenser") });
  const ul = el("ul", { class: "grenser" });
  if (g.belop_maks) {
    ul.append(el("li", {},
      `${t("ui.policy.belop")}: ${g.belop_maks} ${(g.valuta || []).join(" / ")}`));
  }
  if (g.tidsvindu) {
    const dager = g.tidsvindu.ukedager.map((d) => t(`dag.${d}`)).join(", ");
    ul.append(el("li", {},
      `${t("ui.policy.tidsvindu")}: ${dager} ${g.tidsvindu.fra}–${g.tidsvindu.til} (${g.tidsvindu.tidssone})`));
  }
  if (g.frekvens) {
    ul.append(el("li", {},
      `${t("ui.policy.frekvens")}: ${g.frekvens.maks} ${t("ui.policy.per")} ` +
      `${g.frekvens.vindu_antall} ${t(`vindu_enhet.${g.frekvens.vindu_enhet}`, g.frekvens.vindu_enhet)}`));
  }
  return ul;
}

function handlingNode(h) {
  const rot = el("div", { class: "rule" },
    el("div", {},
      el("strong", { text: h.navn }), " — ",
      el("span", { text: t(`modus.${h.modus}`, h.modus) })),
    grenserNode(h.grenser));
  if (h.vilkaar && h.vilkaar.length) {
    rot.append(el("div", { class: "muted" },
      `${t("ui.policy.vilkaar")}: ${h.vilkaar.join(", ")}`));
  }
  return rot;
}

function verifikatorNode(v) {
  return el("div", { class: "rule" },
    el("div", {}, el("strong", { text: v.offentlig_id })),
    el("div", { class: "muted" },
      `${t("ui.policy.betrodd_for")}: ${(v.betrodd_for || []).join(", ") || "—"}`),
    v.kan_fastsla_permanent
      ? el("div", { class: "muted", text: t("ui.policy.permanent") }) : null);
}

function seksjon(tittel, barn) {
  return el("section", { class: "policy-sec" },
    el("h2", { text: tittel }), ...barn);
}

export function visPolicy(hoved, ctx) {
  medStatus(hoved, ctx, async () => {
    try { return await hentJson("/v1/policy/aktiv"); }
    catch (e) { if (e instanceof IkkeFunnetFeil) return null; throw e; }
  }, (d) => {
    if (!d) {
      sett(hoved, ...flateHode(t("ui.policy.tittel")), TomTilstand({}));
      return;
    }
    sett(hoved,
      ...flateHode(t("ui.policy.tittel"),
        `${t("ui.policy.versjon")} ${d.versjon}`),
      VarselBanner({ art: "guard", tekst: t("ui.policy.readonly") }),
      seksjon(t("ui.policy.roller"),
        [el("ul", { class: "liste" },
          d.roller.map((r) => el("li", { text: r.id })))]),
      seksjon(t("ui.policy.handlinger"), d.handlinger.map(handlingNode)),
      seksjon(t("ui.policy.verifikatorer"), d.verifikatorer.map(verifikatorNode)));
  });
}
