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
         slaaOppOrgnr, lukkPlikt, bortfallPlikt, leggTilEgenFrist,
         lagKalenderlenke, slettKalenderlenke } from "../api.js";
import { meldLive } from "../komponenter.js";
import { flateHode } from "./felles.js";

//: Organisasjonsformene et lite firma oftest har, som Enhetsregisterets
//: koder i SMÅ bokstaver: klientpakken er offentlig, og porten
//: `ingen organisasjonsformer i de offentlige ressursene` holder store
//: selskapsformer ute av den. Serveren tar imot og svarer med store.
//: Enhetsregisteret kan si andre; da står koden som et eget valg.
export const ORGFORMER = ["as", "enk", "asa", "nuf", "da", "ans", "sa"];
const NAER_DOGN = 90;
const GJENTAKELSER = ["engang", "aarlig", "kvartalsvis", "manedlig"];

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
  // EGNE FRISTER (239) kan fjernes — de er hennes. En lovpålagt frist kan
  // ikke: den gjelder enten hun liker det eller ei.
  if (f.egen && f.status === "apen") {
    const fjern = el("button", { type: "button", class: "knapp liten",
      text: t("ui.lovfrister.fjern"),
      "aria-label": `${t("ui.lovfrister.fjern")}: ${f.tittel}` });
    fjern.addEventListener("click", () => {
      fjern.disabled = true;
      bortfallPlikt(f.plikt_id, t("ui.lovfrister.fjernet_grunn"))
        .then(() => oppdater(t("ui.lovfrister.fjernet")))
        .catch((e) => {
          fjern.disabled = false;
          if (e instanceof UautorisertFeil) ctx.paaUautorisert?.();
        });
    });
    handling.append(fjern);
  }
  const [, mnd, dag] = f.frist.slice(0, 10).split("-");
  const om = f.egen
    ? el("span", { class: "muted" },
        el("span", { class: "sr-only",
          text: `${t("ui.lovfrister.frist")} ${dato(f.frist)}. ` }),
        el("span", { class: "tag", text: t("ui.lovfrister.egen") }),
        ` ${[f.gjentakelse && f.gjentakelse !== "engang"
          ? t(`ui.lovfrister.gjentakelse.${f.gjentakelse}`) : null,
          f.hjemmel].filter(Boolean).join(" · ")}`)
    : el("span", { class: "muted" },
        el("span", { class: "sr-only",
          text: `${t("ui.lovfrister.frist")} ${dato(f.frist)}. ` }),
        `${f.periode} · ${f.hjemmel} · `,
        el("a", { href: f.kilde_url, target: "_blank", rel: "noopener",
          text: t("ui.lovfrister.kilde") }));
  return el("li", { class: "lf-rad" },
    el("div", { class: "lf-dato", "aria-hidden": "true" },
      el("strong", { text: dag }),
      el("span", { text: t(`ui.lovfrister.mnd.${Number(mnd)}`) })),
    el("div", { class: "lf-hva" }, el("strong", { text: f.tittel }), om),
    el("span", { class: `merke ${art}`, text: tekst }),
    handling);
}

// «+ EGEN FRIST» (239): leieavtalen, forsikringen, domenet. Én linje —
// dato, hva, hvor ofte — og fristen står i samme liste som de lovpålagte,
// med de samme varslene.
function egenSkjema(ctx, etter) {
  const dId = "lf-egen-dato";
  const tId = "lf-egen-tittel";
  const gId = "lf-egen-gjentakelse";
  const datoInn = el("input", { id: dId, type: "date", class: "felt-inp" });
  const tittel = el("input", { id: tId, class: "felt-inp", maxlength: "200",
    autocomplete: "off" });
  const gjenta = el("select", { id: gId, class: "felt-inp" },
    ...GJENTAKELSER.map((g) => el("option", { value: g,
      text: t(`ui.lovfrister.gjentakelse.${g}`) })));
  const knapp = el("button", { type: "submit", class: "knapp primar",
    text: t("ui.lovfrister.egen_lagre") });
  const feil = el("p", { class: "skjema-hjelp", role: "status" });
  const skjema = el("form", { class: "kv-skjema-linje lf-egen", novalidate: "" },
    el("div", { class: "felt" },
      el("label", { for: dId, text: t("ui.lovfrister.frist") }), datoInn),
    el("div", { class: "felt" },
      el("label", { for: tId, text: t("ui.lovfrister.egen_tittel") }), tittel),
    el("div", { class: "felt" },
      el("label", { for: gId, text: t("ui.lovfrister.egen_gjentas") }), gjenta),
    knapp, feil);
  skjema.addEventListener("submit", (ev) => {
    ev.preventDefault();
    if (!datoInn.value) { datoInn.focus(); return; }
    if (!tittel.value.trim()) { tittel.focus(); return; }
    knapp.disabled = true;
    leggTilEgenFrist({ tittel: tittel.value.trim(), frist: datoInn.value,
                       gjentakelse: gjenta.value })
      .then((svar) => etter(svar, t("ui.lovfrister.egen_lagret")))
      .catch((e) => {
        knapp.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return; }
        feil.textContent = t("ui.lovfrister.feil.lagre");
      });
  });
  return skjema;
}

// Hvor abonnementet legges inn, per kalender: [tekstnøkkel, adresse, ny fane].
export function kalendermal(vert, sti) {
  const webcal = `webcal://${vert}${sti}`;
  const https = encodeURIComponent(`https://${vert}${sti}`);
  const outlook = (domene) => `https://${domene}/calendar/0/addfromweb`
    + `?url=${https}&name=Disponit`;
  return [
    ["ui.lovfrister.kal.google",
      `https://calendar.google.com/calendar/r?cid=${encodeURIComponent(webcal)}`,
      true],
    ["ui.lovfrister.kal.outlook_jobb", outlook("outlook.office.com"), true],
    ["ui.lovfrister.kal.outlook_privat", outlook("outlook.live.com"), true],
    ["ui.lovfrister.kal.apne", webcal, false],
  ];
}

// KALENDERLENKEN (239): fristene i Outlook og Google Kalender. Lenken er
// nøkkelen — den vises ÉN gang (bare hashen lagres), kan byttes og kan
// slås av. Derfor tre tilstander: av, nettopp laget (med lenken), på.
function kalenderkort(d, sti, ctx, etter) {
  const feil = (e) => {
    if (e instanceof UautorisertFeil) ctx.paaUautorisert?.();
  };
  const lag = (nokkel, klasse = "knapp") => {
    const b = el("button", { type: "button", class: klasse, text: t(nokkel) });
    b.addEventListener("click", () => {
      b.disabled = true;
      lagKalenderlenke()
        .then((svar) => etter(svar, t("ui.lovfrister.kal.laget"), svar.sti))
        .catch((e) => { b.disabled = false; feil(e); });
    });
    return b;
  };
  const deler = [el("h2", { id: "lf-kal-t", text: t("ui.lovfrister.kal.tittel") })];
  const aktiv = d.kalender && d.kalender.aktiv;
  if (sti) {
    const vindu = globalThis.location || {};
    const url = `${vindu.origin || ""}${sti}`;
    const felt = el("input", { id: "lf-kal-url", class: "felt-inp", readonly: "",
      value: url });
    felt.addEventListener("focus", () => felt.select());
    const kopier = el("button", { type: "button", class: "knapp primar",
      text: t("ui.lovfrister.kal.kopier") });
    const kvitt = el("span", { class: "muted", role: "status" });
    kopier.addEventListener("click", () => {
      const ferdig = () => { kvitt.textContent = t("ui.lovfrister.kal.kopiert"); };
      const utklipp = globalThis.navigator && globalThis.navigator.clipboard;
      if (utklipp && utklipp.writeText) {
        utklipp.writeText(url).then(ferdig).catch(() => felt.select());
      } else { felt.select(); }
    });
    deler.push(
      el("p", { text: t("ui.lovfrister.kal.ny_tekst") }),
      el("div", { class: "lf-kalrad" },
        el("label", { for: "lf-kal-url", class: "sr-only",
          text: t("ui.lovfrister.kal.lenke") }), felt, kopier,
        ),
      kvitt,
      // ÉN KNAPP PER KALENDER. `webcal:` alene åpner et PROGRAM på maskinen
      // — den som har kalenderen i nettleseren (Google, Outlook på nett),
      // fikk bare «velg et program» (eier 28/11). Nettkalenderne tar imot
      // abonnementet på sin egen adresse.
      el("p", { id: "lf-kal-velg", text: t("ui.lovfrister.kal.velg") }),
      el("div", { class: "lf-kalrad", role: "group",
        "aria-labelledby": "lf-kal-velg" },
        ...kalendermal(vindu.host || "", sti).map(([nokkel, href, ny]) =>
          el("a", { class: "knapp", href,
            ...(ny ? { target: "_blank", rel: "noopener noreferrer" } : {}),
            text: t(nokkel) }))),
      el("p", { class: "hjelpetekst", text: t("ui.lovfrister.kal.advarsel") }));
  } else if (aktiv) {
    const av = el("button", { type: "button", class: "knapp",
      text: t("ui.lovfrister.kal.av") });
    av.addEventListener("click", () => {
      av.disabled = true;
      slettKalenderlenke()
        .then((svar) => etter(svar, t("ui.lovfrister.kal.slatt_av")))
        .catch((e) => { av.disabled = false; feil(e); });
    });
    deler.push(
      el("p", { text: t("ui.lovfrister.kal.aktiv")
        .replace("{dato}", dato(d.kalender.opprettet)) }),
      el("div", { class: "knapperad" }, lag("ui.lovfrister.kal.ny"), av));
  } else {
    deler.push(
      el("p", { text: t("ui.lovfrister.kal.tekst") }),
      el("div", { class: "knapperad" }, lag("ui.lovfrister.kal.lag")));
  }
  return el("section", { class: "kort", "aria-labelledby": "lf-kal-t" }, ...deler);
}

export function visLovfrister(hoved, ctx = {}) {
  const melding = el("p", { class: "melding", role: "status" });
  const kropp = el("div", { class: "lf" });
  sett(hoved, ...flateHode(t("ui.lovfrister.tittel"),
                           t("ui.lovfrister.undertittel")), melding, kropp);

  const tegn = (d, kvittering, sti) => {
    const idag = new Date();
    const etter = (s, k, nySti) => tegn(s, k, nySti);
    const frister = d.frister || [];
    // UTEN OPPSETT står oppsettet øverst — men egne frister og kalenderen
    // virker uansett: hun trenger ikke et org.nr. for å huske leieavtalen.
    const start = d.profil ? null
      : el("section", { class: "kort", "aria-labelledby": "lf-start-t" },
        el("h2", { id: "lf-start-t", text: t("ui.lovfrister.start_tittel") }),
        el("p", { text: t("ui.lovfrister.start_tekst") }),
        profilSkjema(d, ctx, etter));

    const aapne = frister.filter((f) => f.status === "apen");
    const forfalt = aapne.filter((f) => dagerTil(f.frist, idag) < 0);
    const neste = aapne.find((f) => dagerTil(f.frist, idag) >= 0);
    const snart = aapne.filter((f) => {
      const n = dagerTil(f.frist, idag);
      return n >= 0 && n <= 30;
    });
    const kpi = frister.length ? el("div", { class: "site-kpi-row lf-kpi" },
      el("div", { class: "site-kpi" },
        el("strong", { text: neste ? dato(neste.frist) : "—" }),
        el("span", { text: neste ? neste.tittel : t("ui.lovfrister.ingen_neste") })),
      el("div", { class: "site-kpi" },
        el("strong", { text: String(snart.length) }),
        el("span", { text: t("ui.lovfrister.kpi.snart") })),
      el("div", { class: "site-kpi" },
        el("strong", { text: String(forfalt.length) }),
        el("span", { text: t("ui.lovfrister.kpi.forfalt") }))) : null;

    const naer = frister.filter((f) => dagerTil(f.frist, idag) <= NAER_DOGN
      && (f.status === "apen" || dagerTil(f.frist, idag) >= -30));
    const senere = frister.filter((f) => !naer.includes(f));
    const oppdater = (k) => hentLovfrister().then((x) => tegn(x, k));
    const liste = (rader) => el("ol", { class: "lf-liste" },
      ...rader.map((f) => fristRad(f, ctx, oppdater, idag)));

    const nyeBoks = d.profil && d.nye_i_katalogen > 0 ? (() => {
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

    const egenKnapp = el("button", { type: "button", class: "knapp liten",
      "aria-expanded": "false", text: t("ui.lovfrister.egen_ny") });
    const egenBoks = el("div", { hidden: true });
    egenKnapp.addEventListener("click", () => {
      if (egenBoks.hidden) sett(egenBoks, egenSkjema(ctx, etter));
      egenBoks.hidden = !egenBoks.hidden;
      egenKnapp.setAttribute("aria-expanded", String(!egenBoks.hidden));
      if (!egenBoks.hidden) egenBoks.querySelector("input").focus();
    });

    const p = d.profil;
    const profilDel = p ? (() => {
      const linje = [p.orgnummer ? `${t("ui.lovfrister.orgnr")} ${p.orgnummer}` : null,
        t(`ui.lovfrister.orgform.${p.orgform.toLowerCase()}`, p.orgform),
        p.mva ? t("ui.lovfrister.er_mva") : t("ui.lovfrister.ikke_mva"),
        p.ansatte ? t("ui.lovfrister.har_ansatte") : t("ui.lovfrister.ingen_ansatte"),
        t(`ui.lovfrister.kilde.${p.kilde}`)].filter(Boolean);
      const endre = el("button", { type: "button", class: "knapp liten",
        "aria-expanded": "false", text: t("ui.lovfrister.endre") });
      const endreBoks = el("div", { hidden: true });
      endre.addEventListener("click", () => {
        if (endreBoks.hidden) sett(endreBoks, profilSkjema(d, ctx, etter));
        endreBoks.hidden = !endreBoks.hidden;
        endre.setAttribute("aria-expanded", String(!endreBoks.hidden));
      });
      return el("section", { class: "kort", "aria-labelledby": "lf-profil-t" },
        el("h2", { id: "lf-profil-t", text: t("ui.lovfrister.profil_tittel") }),
        el("ul", { class: "brikkerad" },
          ...linje.map((x) => el("li", { class: "brikke brikke-tekst", text: x }))),
        el("div", { class: "knapperad" }, endre), endreBoks);
    })() : null;

    sett(kropp,
      start,
      kpi,
      nyeBoks,
      el("section", { class: "kort", "aria-labelledby": "lf-frister-t" },
        el("div", { class: "lf-hode" },
          el("h2", { id: "lf-frister-t", text: t("ui.lovfrister.frister_tittel") }),
          egenKnapp),
        egenBoks,
        el("p", { class: "muted", text: t("ui.lovfrister.varsel_tekst") }),
        naer.length ? liste(naer)
          : el("p", { text: t(frister.length ? "ui.lovfrister.ingen_naer"
            : "ui.lovfrister.ingen_ennaa") }),
        senereDel),
      kalenderkort(d, sti, ctx, etter),
      profilDel);
    melding.textContent = kvittering || "";
    if (kvittering) meldLive(kvittering);
  };

  hentLovfrister()
    .then((d) => tegn(d))
    .catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return; }
      melding.textContent = t("ui.lovfrister.feil.last");
    });
}
