// Bestilling (038 §6/§7) — kunden bestiller en WCAG-kontroll av sitt EGET,
// verifiserte nettsted. Flaten sender aldri en URL: hostname + sti er de
// eneste målfeltene, serveren komponerer `mal_url` og håndhever alt som
// betyr noe (verifisert hostname, policy, frekvens). Klientvalideringen her
// er UX — den gjør feilmeldingen øyeblikkelig og tilgjengelig, aldri
// autoritativ.
//
// §7-kontrakten: ekte <form>/<fieldset>/<legend>/<label for>; feil får
// aria-invalid + aria-errormessage og FOKUS flyttes til første feil;
// utfallet annonseres i role="alert" med STOPP-årsaken i teksten;
// ventetilstand med aria-busy OG tekst. All tekst via locales/.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { bestill, nyIdempotensnokkel, ApiFeil, UautorisertFeil,
  IngenTilgangFeil } from "../api.js";
import { flateHode } from "./felles.js";

// Speiler serverens `_HOSTNAME`/`_STI` (api/bestilling.py) — små A-labels
// med minst to etiketter; absolutt sti uten `..`, prosent, query, fragment.
const HOSTNAME = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;
const STI = /^\/[A-Za-z0-9\-._~!$&'()*+,;=:@/]*$/;

// Serverens omfangsdefault for `maks_sider` (api/bestilling.py: feltet
// UTELATT gir 1 for enkeltside og 50 for nettsted). Her er det aldri
// utelatt — flaten viser tallet — så defaulten må speiles.
const MAKS_STANDARD = { enkeltside: 1, nettsted: 50 };

function stiGyldig(v) {
  if (v === "") return true;
  if (!STI.test(v)) return false;
  return !v.includes("..") && !v.includes("//") && !v.includes("%");
}

export function visBestilling(hoved, ctx) {
  // Idempotensnøkkelen er STABIL så lenge innholdet står urørt: en retry av
  // samme klikk skal REPLAYe på serveren, ikke bestille en gang til. Første
  // endring i et felt gjør neste innsending til en ny intensjon → ny nøkkel.
  let idem = null;

  const felter = {};

  function felt(navn, inp, hjelpKode) {
    const feilId = `bf-${navn}-feil`;
    const hjelpId = `bf-${navn}-hjelp`;
    inp.id = `bf-${navn}`;
    inp.setAttribute("aria-describedby", hjelpId);
    inp.addEventListener("input", () => { idem = null; nullstillFeil(navn); });
    const feilEl = el("p", { class: "skjemafeil", id: feilId });
    felter[navn] = { inp, feilEl, feilId, hjelpId };
    return el("div", { class: "skjemafelt" },
      el("label", { for: inp.id, text: t(`ui.bestilling.felt.${navn}`) }),
      el("p", { class: "hjelpetekst", id: hjelpId, text: t(hjelpKode) }),
      inp, feilEl);
  }

  function nullstillFeil(navn) {
    const f = felter[navn];
    f.inp.removeAttribute("aria-invalid");
    f.inp.removeAttribute("aria-errormessage");
    f.feilEl.textContent = "";
  }

  function settFeil(navn, tekst) {
    const f = felter[navn];
    f.inp.setAttribute("aria-invalid", "true");
    f.inp.setAttribute("aria-errormessage", f.feilId);
    f.inp.setAttribute("aria-describedby", `${f.hjelpId} ${f.feilId}`);
    f.feilEl.textContent = tekst;
  }

  const hostnameInp = el("input", { type: "text", name: "hostname",
    autocomplete: "off", spellcheck: "false", required: true });
  const stiInp = el("input", { type: "text", name: "sti",
    autocomplete: "off", spellcheck: "false" });
  const maksInp = el("input", { type: "number", name: "maks_sider",
    min: "1", max: "50", value: String(MAKS_STANDARD.enkeltside),
    inputmode: "numeric" });

  // SIDETALLET FØLGER OMFANGET (Codex P2). Feltet sto på 1 og ble ALLTID
  // sendt, mens serverens 50-default bare gjelder når `maks_sider`
  // utelates. «Hele nettstedet» ga derfor én side — en kontroll som
  // presenterer seg som hele nettstedet og leverer forsiden, uten at noe
  // på skjermen sa fra. Bytter omfanget, følger tallet med, med mindre
  // brukeren selv har satt et annet: da er det brukerens valg, og det
  // står også synlig i feltet.
  let maksAuto = MAKS_STANDARD.enkeltside;
  let maksValgtAvBruker = false;
  maksInp.addEventListener("input", () => {
    maksValgtAvBruker = maksInp.value.trim() !== String(maksAuto);
  });

  // Omfanget er en ekte radiogruppe i eget fieldset — ikke en div-meny.
  const omfangValg = [];
  const omfangFs = el("fieldset", {},
    el("legend", { text: t("ui.bestilling.felt.omfang") }));
  for (const o of ["enkeltside", "nettsted"]) {
    const r = el("input", { type: "radio", name: "bf-omfang",
      id: `bf-omfang-${o}`, value: o, checked: o === "enkeltside" });
    r.addEventListener("change", () => {
      idem = null;
      if (!r.checked || maksValgtAvBruker) return;
      maksAuto = MAKS_STANDARD[o];
      maksInp.value = String(maksAuto);
      nullstillFeil("maks_sider");
    });
    omfangValg.push(r);
    omfangFs.append(el("div", { class: "radiorad" }, r,
      el("label", { for: r.id, text: t(`ui.bestilling.omfang.${o}`) })));
  }

  const sendKnapp = el("button", { class: "knapp primar", type: "submit",
    text: t("ui.bestilling.send") });
  const ventetekst = el("p", { role: "status", class: "muted" });
  // role="alert" annonserer seg først når teksten SETTES etter innsending —
  // et ferdig utfylt alert-element ved rendring leses aldri opp.
  const utfall = el("div", { class: "bestillingsutfall", role: "alert" });

  const form = el("form", { novalidate: true },
    el("fieldset", {},
      el("legend", { text: t("ui.bestilling.mal_legend") }),
      felt("hostname", hostnameInp, "ui.bestilling.hjelp.hostname"),
      felt("sti", stiInp, "ui.bestilling.hjelp.sti")),
    omfangFs,
    el("fieldset", {},
      el("legend", { text: t("ui.bestilling.grenser_legend") }),
      felt("maks_sider", maksInp, "ui.bestilling.hjelp.maks_sider"),
      el("p", { class: "hjelpetekst",
        text: `${t("ui.bestilling.felt.kravsett")}: ${t("kravsett.wcag21_aa")}` })),
    sendKnapp, ventetekst);

  function frys(paa) {
    for (const inp of [hostnameInp, stiInp, maksInp]) inp.readOnly = paa;
  }

  function valider() {
    const feil = [];
    const host = hostnameInp.value.trim().toLowerCase();
    if (!HOSTNAME.test(host)) {
      settFeil("hostname", t("ui.bestilling.feil.hostname"));
      feil.push(hostnameInp);
    }
    if (!stiGyldig(stiInp.value.trim())) {
      settFeil("sti", t("ui.bestilling.feil.sti"));
      feil.push(stiInp);
    }
    const n = Number(maksInp.value);
    if (!Number.isInteger(n) || n < 1 || n > 50) {
      settFeil("maks_sider", t("ui.bestilling.feil.maks_sider"));
      feil.push(maksInp);
    }
    // §7: fokus til FØRSTE feil — brukeren skal aldri lete etter den.
    if (feil.length) feil[0].focus();
    return feil.length === 0;
  }

  // UTFALLET BÆRER SITT EGET MÅL (Codex P2). Bare knappen var låst mens
  // svaret var underveis; hostname, sti, omfang og sidetall kunne endres
  // fritt, og svaret ble så vist under de NYE verdiene — et
  // oppdragsnummer for nettsted A under nettsted B, uten at noe i
  // meldingen sa hvilket. Nå står målet i selve utfallet, hentet fra den
  // kroppen som faktisk ble sendt. Da kan meldingen ikke tilskrives feil
  // nettsted uansett hva skjemaet viser i mellomtiden — og et ekte,
  // committet oppdrag blir ikke kastet bort for å skjule tvetydigheten.
  function maalLinje(sendt) {
    const deler = [`https://${sendt.hostname}${sendt.sti || "/"}`,
      t(`ui.bestilling.omfang.${sendt.omfang}`)];
    if (sendt.omfang === "nettsted") {
      deler.push(`${t("ui.bestilling.felt.maks_sider")}: ${sendt.maks_sider}`);
    }
    return el("p", { class: "muted",
      text: `${t("ui.bestilling.mal")}: ${deler.join(" · ")}` });
  }

  function visUtfall(svar, sendt) {
    const barn = [maalLinje(sendt)];
    if (svar.beslutning === "tillat") {
      barn.push(el("p", { text: t("ui.bestilling.utfall.tillat") }),
        el("p", { text: `${t("ui.bestilling.oppdrag_id")}: ${svar.oppdrag_id}` }),
        el("p", { class: "muted", text: t("ui.bestilling.se_rapport") }));
    } else if (svar.beslutning === "stopp") {
      // STOPP-årsaken skal LESES OPP, ikke bare vises (§7).
      const koder = (svar.begrunnelse || [])
        .map((k) => t(`kode.${k}`, k)).join(". ");
      barn.push(el("p", {
        text: `${t("ui.bestilling.utfall.stopp")} ${koder}`.trim() }));
    } else {
      barn.push(el("p", { text: t("ui.bestilling.utfall.unntak") }));
      if (svar.unntak_id != null) {
        barn.push(el("p",
          { text: `${t("ui.bestilling.unntak_id")}: ${svar.unntak_id}` }));
      }
    }
    sett(utfall, ...barn);
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    sett(utfall);
    for (const navn of Object.keys(felter)) nullstillFeil(navn);
    if (!valider()) return;
    if (!idem) idem = nyIdempotensnokkel();
    const kropp = {
      bestillingstype: "kontroll.wcag.nettsted",
      hostname: hostnameInp.value.trim().toLowerCase(),
      kravsett: "wcag21_aa",
      omfang: omfangValg.find((r) => r.checked).value,
      maks_sider: Number(maksInp.value),
    };
    const sti = stiInp.value.trim();
    if (sti) kropp.sti = sti;
    // Ventetilstand: aria-busy + TEKST — aldri kun en spinner (§7).
    // Målfeltene fryses med `readOnly` og ikke `disabled`: et låst felt
    // beholder fokus og lesbarhet, mens et deaktivert felt under fingeren
    // flytter fokus og forsvinner for skjermleseren. Radioknappene lar vi
    // stå — de KAN ikke låses uten `disabled` — og derfor bærer utfallet
    // alltid sitt eget mål, omfanget inkludert.
    frys(true);
    form.setAttribute("aria-busy", "true");
    sendKnapp.disabled = true;
    ventetekst.textContent = t("ui.bestilling.venter");
    try {
      const svar = await bestill(kropp, idem);
      visUtfall(svar, kropp);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e instanceof IngenTilgangFeil) {
        // 403 her er nesten alltid uverifisert hostname — navngi det, og
        // navngi hvilket mål det gjaldt.
        sett(utfall, maalLinje(kropp),
          el("p", { text: e.kode === "bestilling_hostname_uverifisert"
            ? t("ui.bestilling.feil.uverifisert")
            : t("ui.bestilling.feil.ingen_tilgang") }));
      } else if (e instanceof ApiFeil && e.kode === "idempotenskonflikt") {
        // Skal ikke kunne skje (nøkkelen nullstilles ved endring) — men
        // svaret må likevel være sant og på skjermen, ikke i konsollen.
        sett(utfall, el("p", { text: t("ui.bestilling.feil.idempotens") }));
      } else if (e instanceof ApiFeil && e.kode === "request_feilformet") {
        sett(utfall, el("p", { text: t("ui.bestilling.feil.avvist") }));
      } else {
        sett(utfall, el("p", { text: t("ui.feil_tittel") }));
      }
    } finally {
      frys(false);
      form.removeAttribute("aria-busy");
      sendKnapp.disabled = false;
      ventetekst.textContent = "";
    }
  });

  sett(hoved,
    ...flateHode(t("ui.bestilling.tittel"), t("ui.bestilling.under")),
    form, utfall);
}
