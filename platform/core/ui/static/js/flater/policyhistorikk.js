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
import { Tidspunkt, TomTilstand, Feiltilstand,
         VarselBanner } from "../komponenter.js";
import { flateHode, fokuserOverskrift } from "./felles.js";

// URL-ene bygges HER, av begge inngangene (Codex P2). `policy_id` er ikke
// et snilt tegnsett bare fordi skjemaet er strengt i dag: lastekontrakten
// slipper med vilje gjennom alt aktive policyer fra før innstrammingen
// bærer, og den gamle Python-`$`-en matchet også en avsluttende linjeskift
// — `/v1/policy/aktiv` kan altså svare med en id som `acme\n`. Interpolert
// rått forsvant linjeskiftet i URL-parseren, og flaten ba om historikken
// til `acme`: leseren fikk en tom eller FEIL historikk, uten at noe sa fra.
// Prosentkoding bevarer identiteten slik den står i basen. Versjonene har
// vært kodet hele tiden; id-en er samme sak.
export function versjonerUrl(policyId) {
  return `/v1/policy/${encodeURIComponent(policyId)}/versjoner`;
}

export function diffUrl(policyId, fra, til) {
  return `/v1/policy/${encodeURIComponent(policyId)}/diff`
    + `?fra=${encodeURIComponent(fra)}&til=${encodeURIComponent(til)}`;
}

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

// Opphavskolonnen sier hva vi VET om kilden, ikke bare hvilket nummer den
// hadde (047, Codex P2). Et versjonsnummer frigjøres av sletting og kan
// gjenskapes med annet innhold, så «rullbakk fra 1» er bare sant så lenge
// generasjonen kopien ble tatt fra fortsatt bærer nummeret. `bundet` er
// den påstanden; `borte` og `ubundet` sier at den ikke kan gjøres.
export function opphavTekst(v) {
  if (!v.rollback_av_versjon) return "";
  const n = v.rollback_av_versjon;
  const nokkel = v.rollback_kilde === "bundet" ? "rullbakk_fra"
    : v.rollback_kilde === "borte" ? "rullbakk_fra_borte"
      : "rullbakk_fra_ubundet";
  return t(`ui.historikk.${nokkel}`).replace("{n}", n);
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
      // «Aktivert» er en påstand om at versjonen HAR vært i kraft. En
      // rad som bare er registrert (`aktiver=False`) har aldri vært det,
      // og skal ikke låne registreringstidspunktet sitt til kolonnen
      // (Codex P2). En migrert rad ble aktivert, men tidspunktet finnes
      // ikke — den viser `opprettet` som før, den ærligste vi har.
      el("td", {}, v.aktivert_ts ? Tidspunkt(v.aktivert_ts)
        : v.aktivert === false
          ? el("span", { class: "muted", text: t("ui.historikk.uaktivert") })
          : Tidspunkt(v.opprettet)),
      el("td", { text: attestantTekst(v) }),
      el("td", { text: opphavTekst(v) }),
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
  // DEFAULT-RETNINGEN LESES AV AKTIVERINGENE (Codex P2). Lista er sortert
  // nyest aktivert først, med de aldri aktiverte bakerst — men å ta indeks
  // 1 og 0 blindt ville likevel plukket en registrert, aldri ikraftsatt
  // versjon i en serie med bare én aktivering, og presentert «uaktivert →
  // aktiv» som om det var kronologien og risikoretningen. Finnes det ikke
  // to aktiverte versjoner, finnes det ingen sann default, og da skal eier
  // velge selv i stedet for å bli servert en påstand.
  const aktiverte = versjoner.filter((v) => v.aktivert !== false);
  const harDefault = aktiverte.length >= 2;
  const tomValg = () => (harDefault ? [] : [el("option", {
    value: "", text: t("ui.historikk.velg_versjon") })]);
  const fra = el("select", { id: "hist-fra" }, ...tomValg(),
    ...versjoner.map((v) => el("option", { value: v.versjon,
                                           text: v.versjon })));
  const til = el("select", { id: "hist-til" }, ...tomValg(),
    ...versjoner.map((v) => el("option", { value: v.versjon,
                                           text: v.versjon })));
  if (harDefault) {
    fra.value = aktiverte[1].versjon;
    til.value = aktiverte[0].versjon;
  } else {
    fra.value = "";
    til.value = "";
  }
  const knapp = el("button", { class: "knapp", type: "button",
    text: t("ui.historikk.vis_diff") });
  knapp.addEventListener("click", () => {
    if (!fra.value || !til.value) {
      // `role="alert"` fordi meldingen kommer som SVAR på et klikk.
      sett(diffUt, el("p", { class: "varsel", role: "alert",
        text: t("ui.historikk.velg_begge") }));
      return;
    }
    sett(diffUt, el("p", { class: "muted", text: t("ui.laster") }));
    hentJson(diffUrl(policyId, fra.value, til.value)).then((d) => {
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
//
// `rullbakkSperret` er PORTENS grunn til at serien ikke kan få et nytt
// utkast (`nytt_utkast_avvist`), ikke flatens egen gjetning: en arvet
// policy-id som `acme\n` kan leses, men `opprett_utkast` avviser den, og
// en rullbakk-knapp ville deterministisk endt i 400 (Codex P2). Da skal
// handlingen ikke tilbys — og grunnen skal STÅ DER, ikke bare mangle.
export function tegnHistorikkflate(hoved, ctx, {
  policyId, versjoner, tilbake, paaRullbakk = null,
  rullbakkSperret = null, erGyldig = () => true,
}) {
  const tilbakeKnapp = el("button", { class: "knapp", type: "button",
    text: t("ui.historikk.tilbake") });
  tilbakeKnapp.addEventListener("click", tilbake);
  // Sperren gjelder bare den som ELLERS ville fått knappen: en ren leseøkt
  // har ingen rullbakk å savne, og skal ikke få en forklaring på noe den
  // ikke ble tilbudt.
  const sperret = paaRullbakk && rullbakkSperret ? rullbakkSperret : null;
  sett(hoved,
    ...flateHode(t("ui.historikk.tittel").replace("{policy}", policyId),
                 t("ui.historikk.under")),
    tilbakeKnapp,
    sperret
      ? VarselBanner({ art: "info",
          tekst: t(`ui.historikk.rullbakk_sperret.${sperret}`,
                   t("ui.historikk.rullbakk_sperret.ukjent"))
            .replace("{policy}", policyId) })
      : null,
    historikkTabell(policyId, versjoner, sperret ? null : paaRullbakk),
    diffSeksjonFor(policyId, versjoner, ctx, erGyldig));
  fokuserOverskrift(hoved);
}
