// Policy — les policyen som håndheves (read-only i v1; redigering er egen,
// versjonert flyt). Menneskelesbar visning av den lukkede PolicyDTO-en.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, slettPolicy, IkkeFunnetFeil, ApiFeil, UautorisertFeil } from "../api.js";
import { VarselBanner, TomTilstand, meldLive } from "../komponenter.js";
import { Bekreftelsesdialog } from "../dialog.js";
import { harScope } from "../sitekart.js";
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
      seksjon(t("ui.policy.verifikatorer"), d.verifikatorer.map(verifikatorNode)),
      angreSeksjon(d, ctx, () => visPolicy(hoved, ctx)));
  });
}

// «Angre» for en feilopprettet policy. Serveren (`slett_ubrukt_policy`, 030)
// håndhever HELE vilkåret: aldri styrt en beslutning, ingen åpen runde.
// Knappen står derfor alltid for policyFORVALTEREN, og en avvisning kommer
// tilbake som en FORKLARING («policyen har styrt beslutninger — den kan
// avvikles, ikke slettes»), ikke som en skjult knapp: en vakt man kan se er
// en vakt man kan forstå.
//
// Men det argumentet gjelder TILSTAND, ikke TILGANG (Codex P2). Denne flaten
// nås med `policy:read`, og `leser`, `admin` og `sikkerhet` har nettopp det og
// ikke `policy:write`. For dem er det ingen tilstand som noen gang kan gjøre
// knappen brukbar — den ville bare invitert dem gjennom en irreversibel
// bekreftelsesdialog fram til en generisk feil fra serverens 403. En
// forklaring man ikke kan handle på er ikke en forklaring, den er støy.
// Lesingen står som før; det er bare mutasjonen som forsvinner.
function angreSeksjon(d, ctx, tegnPaaNytt) {
  if (!harScope(ctx, "policy:write")) return null;
  const status = el("p", { class: "muted", role: "status", text: "" });
  const b = el("button", { class: "knapp fare", type: "button",
    text: t("ui.policy.slett") });
  b.addEventListener("click", () => {
    Bekreftelsesdialog({
      tittel: t("ui.policy.slett_tittel"),
      tekst: `${d.policy_id} · ${t("ui.policy.slett_tekst")}`,
      primarTekst: t("ui.policy.slett"),
      farlig: true,
      paaPrimar: () => slettPolicy(d.policy_id)
        .then(() => {
          meldLive(t("ui.policy.slettet"));
          tegnPaaNytt();               // flaten viser nå TomTilstand — sant.
        })
        .catch((e) => {
          if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
          const kode = e instanceof ApiFeil ? e.kode : "";
          status.textContent = kode === "policy_i_bruk"
            ? t("ui.policy.slett_i_bruk")
            : kode === "runde_allerede_aapen"
              ? t("ui.policy.slett_runde_aapen")
              : t("ui.policy.slett_feilet");
        }),
    });
  });
  return el("section", { class: "policy-angre",
    "aria-label": t("ui.policy.slett_tittel") },
    el("h3", { text: t("ui.policy.slett_tittel") }),
    el("p", { class: "muted", text: t("ui.policy.slett_forklaring") }),
    b, status);
}
