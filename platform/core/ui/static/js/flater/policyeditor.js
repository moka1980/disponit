// Guided policy-editor (PR-014). Å skrive en policy fra bunnen er et komplett,
// skjemagyldig JSON-dokument (roller, handlinger, verifikatorer, dataklasser,
// unntak …). Editoren starter derfor fra en BRANSJEMAL (komplett, gyldig) og
// lar eieren redigere de FORRETNINGSKRITISKE feltene: firmanavn, roller, og per
// handling automatiseringsmodus + grenser (beløpstak, valuta, tidsvindu).
// Strukturen (moduler, verifikatorer, reversering, unntak) arves fra malen, så
// utkastet forblir STRUKTURELT komplett — men det er ikke garantert gyldig
// (eier kan fjerne en referert rolle, sette auto_med_vilkaar uten vilkår osv.).
// GYLDIGHETSPORTEN er `valider` server-side (skjema + semantikk); den må passere
// før runde/fire-øyne-aktivering. Lagring går via opprett/rediger.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  hentMaler, hentJson, opprettUtkast, redigerUtkast, nyIdempotensnokkel,
  UautorisertFeil, ApiFeil,
} from "../api.js";
import { meldLive, TomTilstand, Feiltilstand } from "../komponenter.js";
import { flateHode } from "./felles.js";

const MODUS = ["auto", "auto_med_vilkaar", "alltid_stopp"];

// `hint` er reglene feltet faktisk håndhever, sagt FØR man skriver. Uten den
// måtte eier gjette formatet og få svaret fra validatoren etterpå — og bare
// hvis feilen i det hele tatt nådde skjermen. Knyttes med `aria-describedby`,
// så den leses opp sammen med etiketten.
function tekstfelt(etikett, verdi, paaEndre, attrs = {}, hint = null) {
  const inp = el("input", {
    type: "text", value: verdi == null ? "" : String(verdi),
    class: "felt-inp", ...attrs,
  });
  inp.addEventListener("input", () => paaEndre(inp.value));
  const id = "f-" + Math.random().toString(36).slice(2, 9);
  inp.id = id;
  if (!hint) {
    return el("label", { class: "felt" },
      el("span", { class: "felt-navn", text: etikett }), inp);
  }
  const hid = `${id}-hint`;
  inp.setAttribute("aria-describedby", hid);
  return el("label", { class: "felt" },
    el("span", { class: "felt-navn", text: etikett }), inp,
    el("span", { class: "felt-hint", id: hid, text: hint }));
}

function velg(etikett, verdi, valg, oversettPrefiks, paaEndre) {
  const sel = el("select", { class: "felt-inp" });
  for (const v of valg) {
    const o = el("option", { value: v, text: t(`${oversettPrefiks}${v}`, v) });
    if (v === verdi) o.selected = true;
    sel.append(o);
  }
  sel.addEventListener("change", () => paaEndre(sel.value));
  return el("label", { class: "felt" },
    el("span", { class: "felt-navn", text: etikett }), sel);
}

// Hva peker på rollen? En referert rolle kan ikke bare fjernes: referansen
// ville stått igjen med et navn som ikke finnes, og validatoren avviser hele
// policyen med «ukjent rolle» — én linje per handling. Det er nøyaktig det
// som skjedde: eier fjernet en rolle og fikk seks feil som pekte på
// handlinger han aldri hadde rørt.
//
// `menneskelig_overstyring.krever_rolle` er en rollereferanse på lik linje med
// `tillatt_for` (Codex P2): `policy_validator/schema.py` avviser policyen på
// nøyaktig samme måte når DEN rollen mangler. Så lenge vakten bare så på
// handlingene, framsto en rolle som kun brukes til overstyring som fjernbar —
// og knappen førte rett i den fella vakten er til for å stenge.
function referanserTilRolle(policy, rolleId) {
  if (!rolleId) return [];
  const ut = (policy.handlinger || [])
    .filter((h) => (h.tillatt_for || []).includes(rolleId))
    .map((h) => h.id);
  const mo = policy.menneskelig_overstyring;
  if (mo && mo.krever_rolle === rolleId) {
    ut.push(t("ui.editor.rolle_i_bruk_overstyring"));
  }
  return ut;
}

function rollerSeksjon(policy, tegnPaaNytt) {
  policy.roller = Array.isArray(policy.roller) ? policy.roller : [];
  const liste = el("div", { class: "editor-liste" });
  policy.roller.forEach((r, i) => {
    const brukt = referanserTilRolle(policy, r.id);
    const rad = el("div", { class: "editor-rad" },
      tekstfelt(t("ui.editor.rolle_id"), r.id, (v) => { r.id = v; }),
      tekstfelt(t("ui.editor.rolle_beskrivelse"), r.beskrivelse || "",
        (v) => { r.beskrivelse = v || undefined; }));
    const fjern = el("button", { class: "knapp liten", type: "button",
      text: t("ui.editor.fjern") });
    // En rolle i bruk får ikke en «Fjern»-knapp som fører rett i grøfta.
    // Knappen blir stående, men deaktivert, med hvilke handlinger som holder
    // rollen — så eier kan flytte dem først hvis det ER meningen.
    if (brukt.length) {
      fjern.setAttribute("disabled", "");
      fjern.setAttribute("title",
        `${t("ui.editor.rolle_i_bruk")}: ${brukt.join(", ")}`);
      rad.append(el("p", { class: "editor-hint",
        text: `${t("ui.editor.rolle_i_bruk")} (${brukt.length}): ${brukt.join(", ")}` }));
    } else {
      fjern.addEventListener("click", () => {
        policy.roller.splice(i, 1); tegnPaaNytt();
      });
    }
    rad.append(fjern);
    liste.append(rad);
  });
  const legg = el("button", { class: "knapp", type: "button",
    text: t("ui.editor.legg_til_rolle") });
  legg.addEventListener("click", () => {
    policy.roller.push({ id: "" }); tegnPaaNytt();
  });
  return el("section", { class: "editor-seksjon",
    "aria-label": t("ui.editor.roller") },
    el("h3", { text: t("ui.editor.roller") }), liste, legg);
}

function handlingKort(h) {
  h.grenser = (h.grenser && typeof h.grenser === "object") ? h.grenser : {};
  const g = h.grenser;
  const valutaTekst = Array.isArray(g.valuta) ? g.valuta.join(", ")
    : (g.valuta || "");
  return el("div", { class: "editor-kort" },
    el("h4", {}, el("code", { text: h.id || "?" })),
    velg(t("ui.editor.modus"), h.modus, MODUS, "modus.", (v) => { h.modus = v; }),
    tekstfelt(t("ui.editor.belop_maks"), g.belop_maks == null ? "" : g.belop_maks,
      (v) => {
        v = v.trim();
        if (!v) delete g.belop_maks; else g.belop_maks = v;
      }),
    tekstfelt(t("ui.editor.valuta"), valutaTekst, (v) => {
      const liste = v.split(",").map((s) => s.trim()).filter(Boolean);
      if (liste.length) g.valuta = liste; else delete g.valuta;
    }),
    tekstfelt(t("ui.editor.tidsvindu"), g.tidsvindu || "", (v) => {
      v = v.trim(); if (v) g.tidsvindu = v; else delete g.tidsvindu;
    }));
}

function handlingerSeksjon(policy) {
  policy.handlinger = Array.isArray(policy.handlinger) ? policy.handlinger : [];
  const kort = policy.handlinger.map(handlingKort);
  return el("section", { class: "editor-seksjon",
    "aria-label": t("ui.editor.handlinger") },
    el("h3", { text: t("ui.editor.handlinger") }),
    el("p", { class: "muted", text: t("ui.editor.handlinger_hjelp") }),
    ...kort);
}

function metaSeksjon(policy, erNy) {
  policy.meta = (policy.meta && typeof policy.meta === "object") ? policy.meta : {};
  const m = policy.meta;
  const felt = [
    // policy_id er identiteten; kan settes ved NY, låst ved redigering.
    tekstfelt(t("ui.editor.policy_id"), m.policy_id || "",
      (v) => { m.policy_id = v; }, erNy ? {} : { disabled: "" },
      erNy ? t("ui.editor.policy_id_hint") : t("ui.editor.policy_id_laast")),
    tekstfelt(t("ui.editor.bedrift"), m.bedrift || "",
      (v) => { m.bedrift = v || undefined; }),
    tekstfelt(t("ui.editor.versjon"), m.versjon || "",
      (v) => { m.versjon = v; }),
  ];
  return el("section", { class: "editor-seksjon",
    "aria-label": t("ui.editor.meta") },
    el("h3", { text: t("ui.editor.meta") }), ...felt);
}

// Bygg det som skal lagres: hele malen/utkastet med redigerte felt oppå.
function byggInnhold(policy) {
  return policy;                       // policy muteres in-place av feltene
}

export function visPolicyeditor(hoved, ctx, opts = {}) {
  // opts: { utkast_id?, aapneUtkast: fn(uid), tilbake: fn() }
  const st = { policy: null, utkast_id: opts.utkast_id || null,
               utkastversjon: null, feil: [], laster: true,
               nokkel: null, signatur: null };

  function lagre() {
    const innhold = byggInnhold(st.policy);
    const pid = (st.policy.meta && st.policy.meta.policy_id || "").trim();
    if (!st.utkast_id && !pid) {
      st.feil = [t("ui.editor.policy_id_pakrevd")]; tegn(); return;
    }
    // STABIL idempotensnøkkel per innhold (Codex R1): samme innhold → samme
    // nøkkel, så en retry etter tapt svar REPLAYer i stedet for å duplisere.
    // Endres innholdet, er det en ny operasjon → ny nøkkel.
    const signatur = JSON.stringify(
      { u: st.utkast_id, v: st.utkastversjon, p: pid, i: innhold });
    if (signatur !== st.signatur) {
      st.nokkel = nyIdempotensnokkel(); st.signatur = signatur;
    }
    const jobb = st.utkast_id
      ? redigerUtkast(st.utkast_id, st.utkastversjon, innhold, st.nokkel)
      : opprettUtkast(pid, innhold, st.nokkel);
    jobb.then((res) => {
      meldLive(t("ui.editor.lagret"));
      const uid = res.utkast_id || st.utkast_id;
      if (opts.aapneUtkast) opts.aapneUtkast(uid);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      st.feil = [t(`ui.editor.feil.${e.kode}`, t("ui.editor.lagring_feilet"))];
      tegn();
    });
  }

  function tegn() {
    if (st.laster) { sett(hoved, ...flateHode(t("ui.editor.tittel")),
      TomTilstand({ tittel: t("ui.laster") })); return; }
    if (!st.policy) { sett(hoved, Feiltilstand({})); return; }
    const knapper = el("div", { class: "editor-knapper" });
    const lagreKnapp = el("button", { class: "knapp primar", type: "button",
      text: t("ui.editor.lagre") });
    lagreKnapp.addEventListener("click", lagre);
    const avbryt = el("button", { class: "knapp", type: "button",
      text: t("ui.editor.avbryt") });
    avbryt.addEventListener("click", () => { if (opts.tilbake) opts.tilbake(); });
    knapper.append(lagreKnapp, avbryt);

    const feilboks = st.feil.length
      ? el("div", { class: "editor-feil", role: "alert" },
          el("p", { text: t("ui.editor.har_feil") }),
          el("ul", {}, ...st.feil.map((f) => el("li", { text: f }))))
      : null;

    const barn = [
      ...flateHode(t("ui.editor.tittel"), t("ui.editor.undertittel")),
      metaSeksjon(st.policy, !st.utkast_id),
      rollerSeksjon(st.policy, tegn),
      handlingerSeksjon(st.policy),
    ];
    if (feilboks) barn.push(feilboks);
    barn.push(knapper);
    sett(hoved, ...barn);
  }

  // --- Last inn utgangspunkt: enten et eksisterende utkast, eller malvalg ---
  if (st.utkast_id) {
    hentJson(`/v1/policyutkast/${st.utkast_id}`).then((detalj) => {
      st.laster = false;
      st.utkastversjon = detalj.utkastversjon;
      st.policy = detalj.innhold || {};
      tegn();
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      st.laster = false; tegn();
    });
  } else if (opts.startPolicy) {
    st.laster = false;
    st.policy = JSON.parse(JSON.stringify(opts.startPolicy));
    tegn();
  } else {
    // Malvelger.
    hentMaler().then((d) => {
      st.laster = false;
      visMalvelger(hoved, ctx, (d && d.maler) || [], (mal) => {
        st.policy = JSON.parse(JSON.stringify(mal.innhold));
        // Malen FORESLÅR sin egen id i stedet for å tømme feltet: et tomt felt
        // ba eier finne på en identitet uten å si hva den brukes til eller
        // hvilket format den krever — og en ny id lager en NY policy ved siden
        // av den som gjelder, i stedet for å avløse den. Kan overskrives.
        tegn();
      }, opts.tilbake);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      st.laster = false; sett(hoved, Feiltilstand({}));
    });
  }
}

function visMalvelger(hoved, ctx, maler, paaValg, tilbake) {
  const kort = maler.map((m) => {
    const k = el("button", { class: "mal-kort", type: "button" },
      el("strong", { text: t(`ui.editor.mal.${m.mal_id}`, m.bransjemal) }),
      el("span", { class: "muted",
        text: `${(m.innhold.roller || []).length} ${t("ui.editor.roller")} · `
          + `${(m.innhold.handlinger || []).length} ${t("ui.editor.handlinger")}` }));
    k.addEventListener("click", () => paaValg(m));
    return k;
  });
  const avbryt = el("button", { class: "knapp", type: "button",
    text: t("ui.editor.avbryt") });
  avbryt.addEventListener("click", () => { if (tilbake) tilbake(); });
  sett(hoved,
    ...flateHode(t("ui.editor.velg_mal"), t("ui.editor.velg_mal_hjelp")),
    el("div", { class: "mal-liste" }, ...kort),
    avbryt);
}
