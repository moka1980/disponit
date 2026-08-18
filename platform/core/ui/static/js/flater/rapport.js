// Rapportvisning (038 §7) — den promoterte WCAG-rapporten bak et
// beslutningsoppdrag. Ærligheten er FØRSTEKLASSES innhold: avkorting,
// dekningsbegrensninger og «manuelle kriterier ikke vurdert» står som tekst
// i rapporten — aldri tooltip, aldri fotnote — og alvorlighet bæres alltid
// av TEKST, aldri av farge alene. Funnene står i ekte tabeller med
// <caption> og <th scope>.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentRapport, UautorisertFeil, IngenTilgangFeil,
  IkkeFunnetFeil } from "../api.js";
import { Tidspunkt, TomTilstand, Feiltilstand, TilgangsVakt,
  meldLive } from "../komponenter.js";
import { flateHode, kvRad } from "./felles.js";

const ALVOR = ["kritisk", "alvorlig", "moderat", "lav"];

function tabell(captionKode, kolonner, rader) {
  const thead = el("thead", {}, el("tr", {},
    ...kolonner.map((k) => el("th", { scope: "col", text: k }))));
  const tbody = el("tbody", {}, ...rader.map((r) =>
    el("tr", {}, ...r.map((c, i) => i === 0
      ? el("th", { scope: "row", text: String(c) })
      : el("td", {}, c && c.nodeType ? c : String(c))))));
  return el("table", { class: "datatabell" },
    el("caption", { text: t(captionKode) }), thead, tbody);
}

function rapportInnhold(d) {
  const r = d.rapport;
  const rot = el("div", { class: "rapport" });

  // Sammendraget — tall MED tekstetiketter (aldri farge alene).
  const dl = el("dl", { class: "kv" });
  kvRad(dl, t("ui.rapport.kravsett"), t(`kravsett.${r.kravsett}`, r.kravsett));
  kvRad(dl, t("ui.rapport.kjort"), Tidspunkt(r.kjort_ts));
  kvRad(dl, t("ui.rapport.regelsett"), r.regelsett_versjon);
  for (const a of ALVOR) {
    kvRad(dl, t(`alvorlighet.${a}`), String(r.sammendrag[a]));
  }
  rot.append(el("h2", { text: t("ui.rapport.sammendrag") }), dl);

  // ÆRLIGHETEN — alltid som tekst i selve rapporten (§7).
  const aerlighet = el("div", { class: "rapport-aerlighet" },
    el("h2", { text: t("ui.rapport.dekning") }),
    el("p", { text: t("ui.rapport.manuelle_ikke_vurdert") }));
  if (r.avkortet && r.avkortet.truffet) {
    aerlighet.append(el("p", {
      text: `${t("ui.rapport.avkortet")} (${r.avkortet.verdi}/${r.avkortet.tak})` }));
  }
  if ((r.dekningsbegrensninger || []).length) {
    aerlighet.append(tabell("ui.rapport.begrensninger_caption",
      [t("ui.rapport.kol.vert"), t("ui.rapport.kol.art"),
       t("ui.rapport.kol.antall")],
      r.dekningsbegrensninger.map((b) =>
        [b.vert, t(`ressursart.${b.art}`, b.art), b.antall])));
  } else {
    aerlighet.append(el("p", { class: "muted",
      text: t("ui.rapport.ingen_begrensninger") }));
  }
  rot.append(aerlighet);

  rot.append(el("h2", { text: t("ui.rapport.sider") }),
    tabell("ui.rapport.sider_caption",
      [t("ui.rapport.kol.url"), t("ui.rapport.kol.status")],
      r.sider_kontrollert.map((s) =>
        [s.url, t(`sidestatus.${s.status}`, s.status)])));

  const funn = r.funn || [];
  rot.append(el("h2", { text: t("ui.rapport.funn") }));
  if (funn.length) {
    rot.append(tabell("ui.rapport.funn_caption",
      [t("ui.rapport.kol.regel"), t("ui.rapport.kol.alvorlighet"),
       t("ui.rapport.kol.antall"), t("ui.rapport.kol.eksempler")],
      funn.map((f) => [f.regel_id,
        t(`alvorlighet.${f.alvorlighet}`, f.alvorlighet), f.antall,
        el("ul", { class: "eksempelliste" },
          ...f.eksempler.map((e) => el("li", { text: e })))])));
  } else {
    rot.append(el("p", { text: t("ui.rapport.ingen_funn") }));
  }
  return rot;
}

export function visRapport(hoved, ctx) {
  const inp = el("input", { type: "number", id: "rp-oppdrag", min: "1",
    inputmode: "numeric", required: true,
    "aria-describedby": "rp-oppdrag-hjelp" });
  const feilEl = el("p", { class: "skjemafeil", id: "rp-oppdrag-feil" });
  const resultat = el("div", { class: "rapportresultat" });
  const ventetekst = el("p", { role: "status", class: "muted" });

  const form = el("form", { novalidate: true },
    el("div", { class: "skjemafelt" },
      el("label", { for: "rp-oppdrag", text: t("ui.rapport.felt.oppdrag") }),
      el("p", { class: "hjelpetekst", id: "rp-oppdrag-hjelp",
        text: t("ui.rapport.hjelp.oppdrag") }),
      inp, feilEl),
    el("button", { class: "knapp primar", type: "submit",
      text: t("ui.rapport.hent") }),
    ventetekst);

  inp.addEventListener("input", () => {
    inp.removeAttribute("aria-invalid");
    inp.removeAttribute("aria-errormessage");
    feilEl.textContent = "";
  });

  // Hver forespørsel får sitt eget nummer, og bare den SISTE får skrive
  // (Codex P2). Skjemaet lar deg sende på nytt mens et svar er
  // underveis: ber du om oppdrag A og så B, og A svarer sist, erstattet
  // A-svaret B-rapporten — mens feltet viste B. En rapport tilskrevet
  // feil oppdrag er verre enn ingen rapport, og ingenting på skjermen
  // ville avslørt byttet.
  //
  // Nummer og ikke `disabled` på knappen: en knapp som forsvinner under
  // fingeren flytter fokus og gjør ventetilstanden til en felle for
  // tastatur- og skjermleserbrukere. `aria-busy` sier alt at noe pågår;
  // her legger vi bare til at et foreldet svar ties i hjel.
  let generasjon = 0;

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    sett(resultat);
    const n = Number(inp.value);
    if (!Number.isInteger(n) || n < 1) {
      inp.setAttribute("aria-invalid", "true");
      inp.setAttribute("aria-errormessage", "rp-oppdrag-feil");
      feilEl.textContent = t("ui.rapport.feil.oppdrag");
      inp.focus();                       // §7: fokus til første feil
      return;
    }
    const min = ++generasjon;
    form.setAttribute("aria-busy", "true");
    ventetekst.textContent = t("ui.rapport.venter");
    try {
      const d = await hentRapport(n);
      if (min !== generasjon) return;
      sett(resultat, rapportInnhold(d));
      meldLive(t("ui.rapport.klar"));
    } catch (e) {
      // 401 gjelder ØKTEN, ikke forespørselen: den skal virke også om
      // svaret er foreldet — sesjonen er ugyldig uansett hvem som spurte.
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (min !== generasjon) return;
      if (e instanceof IkkeFunnetFeil) {
        sett(resultat, TomTilstand({ tittel: t("ui.rapport.mangler_tittel"),
          tekst: t("ui.rapport.mangler_tekst") }));
      } else if (e instanceof IngenTilgangFeil) {
        sett(resultat, TilgangsVakt({}));
      } else {
        sett(resultat, Feiltilstand({}));
      }
      meldLive(t("ui.feil_tittel"));
    } finally {
      // ... og et foreldet svar rydder heller ikke bort ventetilstanden
      // til den forespørselen som FAKTISK pågår.
      if (min === generasjon) {
        form.removeAttribute("aria-busy");
        ventetekst.textContent = "";
      }
    }
  });

  sett(hoved,
    ...flateHode(t("ui.rapport.tittel"), t("ui.rapport.under")),
    form, resultat);
}
