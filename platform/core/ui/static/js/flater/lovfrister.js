// LOVPÅLAGTE FRISTER (237) — Frist- og pliktvakten, fase 1.
//
// Eier 27/11: kjernen kunden betaler for er «Frist- og pliktvakt». Flaten
// svarer på ÉN ting først: hva må gjøres, og når? Derfor står fristene
// øverst (produktet), og firmaets opplysninger under (administrasjon) —
// eiers prinsipp om færrest klikk til produktet.
//
// FØRSTE GANG er det motsatt: uten opplysninger finnes det ingen frister,
// så oppsettet ER produktet. Én linje: org.nr. og «Hent fra
// Enhetsregisteret». Forslaget vises som tre valg hun kan rette, og ÉN
// primærknapp lagrer og lager fristene.
//
// FRISTENE BOR I M-21 (pliktregisteret): varslene 30/7/1 døgn før, i appen
// og på e-post, kommer derfra. «Marker som levert» er M-21s kvittering —
// samme dør, samme regel (en frist lukkes aldri av at tiden går).
//
// IKKE EN LANG LISTE: de neste 90 dagene står fram, resten av året ligger
// sammenklappet bak én knapp.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { ApiFeil, UautorisertFeil, hentLovfrister, lagreLovfrister,
         slaaOppOrgnr, lukkPlikt } from "../api.js";
import { meldLive } from "../komponenter.js";
import { flateHode } from "./felles.js";

//: Organisasjonsformene et lite firma oftest har, som Enhetsregisterets
//: koder i SMÅ bokstaver: klientpakken er offentlig, og porten
//: `ingen organisasjonsformer i de offentlige ressursene` holder store
//: selskapsformer ute av den. Serveren tar imot og svarer med store.
//: Enhetsregisteret kan si andre; da står koden som et eget valg.
export const ORGFORMER = ["as", "enk", "asa", "nuf", "da", "ans", "sa"];
const NAER_DOGN = 90;

function dato(iso) {
  return iso ? iso.slice(0, 10).split("-").reverse().join(".") : "";
}

function dagerTil(iso, idag = new Date()) {
  const [a, m, d] = iso.slice(0, 10).split("-").map(Number);
  const frist = Date.UTC(a, m - 1, d);
  const naa = Date.UTC(idag.getFullYear(), idag.getMonth(), idag.getDate());
  return Math.round((frist - naa) / 86400000);
}

// Status som ORD, aldri bare farge (WCAG 1.4.1).
export function statusTekst(f, idag) {
  if (f.status === "lukket") return [t("ui.lovfrister.status.levert"), "tillat"];
  if (f.status === "bortfalt") return [t("ui.lovfrister.status.bortfalt"), "ukjent"];
  const n = dagerTil(f.frist, idag);
  if (n < 0) {
    return [t("ui.lovfrister.status.forfalt").replace("{n}", String(-n)), "stopp"];
  }
  if (n === 0) return [t("ui.lovfrister.status.i_dag"), "stopp"];
  if (n <= 7) {
    return [t("ui.lovfrister.status.om").replace("{n}", String(n)), "unntak"];
  }
  return [t("ui.lovfrister.status.om").replace("{n}", String(n)), "ukjent"];
}

function profilSkjema(d, ctx, etter) {
  const p = d.profil || {};
  const melding = el("p", { class: "melding", role: "status" });
  const orgnr = el("input", { id: "lf-orgnr", class: "felt-inp",
    inputmode: "numeric", autocomplete: "off",
    value: p.orgnummer || d.orgnummer_firma || "" });
  const hent = el("button", { type: "button", class: "knapp",
    text: t("ui.lovfrister.hent") });
  const form = el("select", { id: "lf-orgform", class: "felt-inp" });
  const leggTilForm = (stor) => {
    const kode = String(stor || "").toLowerCase();
    if (![...form.options].some((o) => o.value === kode)) {
      form.append(el("option", { value: kode,
        text: t(`ui.lovfrister.orgform.${kode}`, kode) }));
    }
  };
  ORGFORMER.forEach(leggTilForm);
  if (p.orgform) {
    leggTilForm(p.orgform); form.value = p.orgform.toLowerCase();
  }
  const mva = el("input", { type: "checkbox", id: "lf-mva" });
  mva.checked = !!p.mva;
  const ansatte = el("input", { type: "checkbox", id: "lf-ansatte" });
  ansatte.checked = !!p.ansatte;
  let kilde = p.kilde || "manuell";
  const funnet = el("p", { class: "muted lf-funnet", hidden: true });

  let avSegSelv = false;
  hent.addEventListener("click", () => {
    const nr = orgnr.value.replace(/\s+/g, "");
    if (!/^[0-9]{9}$/.test(nr)) {
      melding.textContent = t("ui.lovfrister.feil.orgnr");
      orgnr.setAttribute("aria-invalid", "true");
      orgnr.focus(); return;
    }
    orgnr.removeAttribute("aria-invalid");
    hent.disabled = true;
    melding.textContent = t("ui.lovfrister.henter");
    slaaOppOrgnr(nr)
      .then((x) => {
        const f = x.forslag;
        leggTilForm(f.orgform);
        form.value = String(f.orgform || "").toLowerCase();
        mva.checked = !!f.mva;
        ansatte.checked = !!f.ansatte;
        kilde = "brreg";
        funnet.hidden = false;
        funnet.textContent = t("ui.lovfrister.funnet")
          .replace("{navn}", f.navn || nr);
        melding.textContent = t("ui.lovfrister.sjekk");
        meldLive(melding.textContent);
        // Fokus flyttes bare når HUN trykket — ikke når oppslaget gikk
        // av seg selv ved sidelasting.
        if (!avSegSelv) lagre.focus();
        avSegSelv = false;
      })
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return; }
        melding.textContent = t(e instanceof ApiFeil && e.status === 404
          ? "ui.lovfrister.feil.ikke_funnet" : "ui.lovfrister.feil.register");
      })
      .finally(() => { hent.disabled = false; });
  });
  // FØRSTE GANG, MED KJENT ORG.NR.: oppslaget går av seg selv. Hun kom
  // hit for å få fristene, og nummeret står der alt — da er «Hent» et
  // trykk som bare ber henne bekrefte det åpenbare.
  if (!d.profil && /^[0-9]{9}$/.test(orgnr.value)) {
    queueMicrotask(() => { avSegSelv = true; hent.click(); });
  }
  // Rører hun et valg selv, er opplysningene hennes, ikke registerets.
  for (const x of [form, mva, ansatte]) {
    x.addEventListener("change", () => { kilde = "manuell"; });
  }

  const lagre = el("button", { type: "submit", class: "knapp primar",
    text: t(d.profil ? "ui.lovfrister.lagre" : "ui.lovfrister.lag") });
  const skjema = el("form", { class: "lf-oppsett", novalidate: "" },
    el("div", { class: "lf-orgrad" },
      el("div", { class: "felt" },
        el("label", { for: "lf-orgnr", text: t("ui.lovfrister.orgnr") }), orgnr),
      hent),
    funnet,
    el("div", { class: "lf-valg" },
      el("div", { class: "felt" },
        el("label", { for: "lf-orgform", text: t("ui.lovfrister.orgform") }),
        form),
      el("label", { class: "avkryssing", for: "lf-mva" }, mva,
        el("span", { text: t("ui.lovfrister.mva") })),
      el("label", { class: "avkryssing", for: "lf-ansatte" }, ansatte,
        el("span", { text: t("ui.lovfrister.ansatte") }))),
    el("div", { class: "knapperad" }, lagre), melding);
  skjema.addEventListener("submit", (ev) => {
    ev.preventDefault();
    const nr = orgnr.value.replace(/\s+/g, "");
    if (nr && !/^[0-9]{9}$/.test(nr)) {
      melding.textContent = t("ui.lovfrister.feil.orgnr");
      orgnr.focus(); return;
    }
    lagre.disabled = true;
    lagreLovfrister({ orgnummer: nr || null, orgform: form.value.toUpperCase(),
                      mva: mva.checked, ansatte: ansatte.checked, kilde })
      .then((svar) => etter(svar, t("ui.lovfrister.lagret")
        .replace("{n}", String(svar.nye))))
      .catch((e) => {
        lagre.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return; }
        melding.textContent = t("ui.lovfrister.feil.lagre");
      });
  });
  return skjema;
}

function fristRad(f, ctx, oppdater, idag) {
  const [tekst, art] = statusTekst(f, idag);
  const handling = el("div", { class: "lf-handling" });
  if (f.status === "apen" && f.registrert) {
    const knapp = el("button", { type: "button", class: "knapp liten",
      text: t("ui.lovfrister.levert") });
    knapp.addEventListener("click", () => {
      const id = `lf-kv-${f.plikt_id}`;
      const inn = el("input", { id, class: "felt-inp", autocomplete: "off" });
      const ok = el("button", { type: "button", class: "knapp liten primar",
        text: t("ui.lovfrister.lagre_levert") });
      const nei = el("button", { type: "button", class: "knapp liten",
        text: t("ui.lovfrister.avbryt") });
      nei.addEventListener("click", () => { sett(handling, knapp); knapp.focus(); });
      ok.addEventListener("click", () => {
        if (!inn.value.trim()) { inn.focus(); return; }
        ok.disabled = true;
        lukkPlikt(f.plikt_id, inn.value.trim())
          .then(() => oppdater(t("ui.lovfrister.markert")))
          .catch((e) => {
            ok.disabled = false;
            if (e instanceof UautorisertFeil) ctx.paaUautorisert?.();
          });
      });
      sett(handling,
        el("label", { for: id, class: "sr-only",
          text: t("ui.lovfrister.kvittering") }), inn, ok, nei);
      inn.placeholder = t("ui.lovfrister.kvittering");
      inn.focus();
    });
    handling.append(knapp);
  }
  const [, mnd, dag] = f.frist.slice(0, 10).split("-");
  return el("li", { class: "lf-rad" },
    el("div", { class: "lf-dato", "aria-hidden": "true" },
      el("strong", { text: dag }),
      el("span", { text: t(`ui.lovfrister.mnd.${Number(mnd)}`) })),
    el("div", { class: "lf-hva" },
      el("strong", { text: f.tittel }),
      el("span", { class: "muted" },
        el("span", { class: "sr-only",
          text: `${t("ui.lovfrister.frist")} ${dato(f.frist)}. ` }),
        `${f.periode} · ${f.hjemmel} · `,
        el("a", { href: f.kilde_url, target: "_blank", rel: "noopener",
          text: t("ui.lovfrister.kilde") }))),
    el("span", { class: `merke ${art}`, text: tekst }),
    handling);
}

export function visLovfrister(hoved, ctx = {}) {
  const melding = el("p", { class: "melding", role: "status" });
  const kropp = el("div", { class: "lf" });
  sett(hoved, ...flateHode(t("ui.lovfrister.tittel"),
                           t("ui.lovfrister.undertittel")), melding, kropp);

  const tegn = (d, kvittering) => {
    const idag = new Date();
    if (!d.profil) {
      sett(kropp, el("section", { class: "kort", "aria-labelledby": "lf-start-t" },
        el("h2", { id: "lf-start-t", text: t("ui.lovfrister.start_tittel") }),
        el("p", { text: t("ui.lovfrister.start_tekst") }),
        profilSkjema(d, ctx, (s, k) => tegn(s, k))));
      return;
    }
    const aapne = d.frister.filter((f) => f.status === "apen");
    const forfalt = aapne.filter((f) => dagerTil(f.frist, idag) < 0);
    const neste = aapne.find((f) => dagerTil(f.frist, idag) >= 0);
    const snart = aapne.filter((f) => {
      const n = dagerTil(f.frist, idag);
      return n >= 0 && n <= 30;
    });
    const kpi = el("div", { class: "site-kpi-row lf-kpi" },
      el("div", { class: "site-kpi" },
        el("strong", { text: neste ? dato(neste.frist) : "—" }),
        el("span", { text: neste ? neste.tittel : t("ui.lovfrister.ingen_neste") })),
      el("div", { class: "site-kpi" },
        el("strong", { text: String(snart.length) }),
        el("span", { text: t("ui.lovfrister.kpi.snart") })),
      el("div", { class: "site-kpi" },
        el("strong", { text: String(forfalt.length) }),
        el("span", { text: t("ui.lovfrister.kpi.forfalt") })));

    const naer = d.frister.filter((f) => dagerTil(f.frist, idag) <= NAER_DOGN
      && (f.status === "apen" || dagerTil(f.frist, idag) >= -30));
    const senere = d.frister.filter((f) => !naer.includes(f));
    const oppdater = (k) => hentLovfrister().then((x) => tegn(x, k));
    const liste = (rader) => el("ol", { class: "lf-liste" },
      ...rader.map((f) => fristRad(f, ctx, oppdater, idag)));

    const nyeBoks = d.nye_i_katalogen > 0 ? (() => {
      const knapp = el("button", { type: "button", class: "knapp primar liten",
        text: t("ui.lovfrister.legg_til_nye") });
      knapp.addEventListener("click", () => {
        knapp.disabled = true;
        lagreLovfrister({ ...d.profil })
          .then((s) => tegn(s, t("ui.lovfrister.lagret")
            .replace("{n}", String(s.nye))))
          .catch(() => { knapp.disabled = false; });
      });
      return el("div", { class: "banner info lf-nye" },
        el("span", { text: d.nye_i_katalogen === 1
          ? t("ui.lovfrister.nye_en")
          : t("ui.lovfrister.nye").replace("{n}", String(d.nye_i_katalogen)) }),
        knapp);
    })() : null;

    const senereDel = senere.length ? (() => {
      const vis = el("button", { type: "button", class: "knapp liten",
        "aria-expanded": "false",
        text: t("ui.lovfrister.vis_senere").replace("{n}", String(senere.length)) });
      const boks = el("div", { hidden: true }, liste(senere));
      vis.addEventListener("click", () => {
        boks.hidden = !boks.hidden;
        vis.setAttribute("aria-expanded", String(!boks.hidden));
      });
      return el("div", { class: "lf-senere" }, vis, boks);
    })() : null;

    const p = d.profil;
    const profilLinje = [p.orgnummer ? `${t("ui.lovfrister.orgnr")} ${p.orgnummer}` : null,
      t(`ui.lovfrister.orgform.${p.orgform.toLowerCase()}`, p.orgform),
      p.mva ? t("ui.lovfrister.er_mva") : t("ui.lovfrister.ikke_mva"),
      p.ansatte ? t("ui.lovfrister.har_ansatte") : t("ui.lovfrister.ingen_ansatte"),
      t(`ui.lovfrister.kilde.${p.kilde}`)].filter(Boolean);
    const endre = el("button", { type: "button", class: "knapp liten",
      "aria-expanded": "false", text: t("ui.lovfrister.endre") });
    const endreBoks = el("div", { hidden: true });
    endre.addEventListener("click", () => {
      if (endreBoks.hidden) sett(endreBoks, profilSkjema(d, ctx, (s, k) => tegn(s, k)));
      endreBoks.hidden = !endreBoks.hidden;
      endre.setAttribute("aria-expanded", String(!endreBoks.hidden));
    });

    sett(kropp,
      kpi,
      nyeBoks,
      el("section", { class: "kort", "aria-labelledby": "lf-frister-t" },
        el("h2", { id: "lf-frister-t", text: t("ui.lovfrister.frister_tittel") }),
        el("p", { class: "muted", text: t("ui.lovfrister.varsel_tekst") }),
        naer.length ? liste(naer)
          : el("p", { text: t("ui.lovfrister.ingen_naer") }),
        senereDel),
      el("section", { class: "kort", "aria-labelledby": "lf-profil-t" },
        el("h2", { id: "lf-profil-t", text: t("ui.lovfrister.profil_tittel") }),
        el("ul", { class: "brikkerad" },
          ...profilLinje.map((x) => el("li", { class: "brikke brikke-tekst", text: x }))),
        el("div", { class: "knapperad" }, endre), endreBoks));
    if (kvittering) { melding.textContent = kvittering; meldLive(kvittering); }
  };

  hentLovfrister()
    .then((d) => tegn(d))
    .catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return; }
      melding.textContent = t("ui.lovfrister.feil.last");
    });
}
