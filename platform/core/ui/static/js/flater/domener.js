// Domener (039) — selvbetjent domeneverifisering, fanen bestillingen
// peker til når hostnamet ikke er verifisert. Kunden ser domenene sine,
// legger til et nytt og får TXT-oppskriften ÉN gang; verifiseringen
// skjer automatisk (arbeideren, ~5 min) og statusen leses her.
// §7-semantikk som resten: ekte form, aria-invalid + fokus til feil,
// resultat i role=alert, tabell med caption/scope, status alltid tekst.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentDomener, leggTilDomene, ApiFeil, UautorisertFeil,
  IngenTilgangFeil } from "../api.js";
import { Tidspunkt, TomTilstand, Feiltilstand, meldLive } from "../komponenter.js";
import { kvRad } from "./felles.js";

// Samme klientform som bestillingsflaten (serveren avgjør uansett —
// 018 §0 er strengere og svarer 409 for det som slipper gjennom her).
const HOSTNAME = /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$/;

export function visDomener(rot, ctx) {
  const liste = el("div", { class: "domeneliste", "aria-busy": "true" });
  const utfall = el("div", { class: "domeneutfall", role: "alert" });
  const feilEl = el("p", { class: "skjemafeil", id: "dm-host-feil" });
  const inp = el("input", { type: "text", id: "dm-host",
    autocomplete: "off", spellcheck: "false", required: true,
    "aria-describedby": "dm-host-hjelp" });
  inp.addEventListener("input", () => {
    inp.removeAttribute("aria-invalid");
    inp.removeAttribute("aria-errormessage");
    feilEl.textContent = "";
  });
  const knapp = el("button", { class: "knapp primar", type: "submit",
    text: t("ui.domener.legg_til") });
  const ventetekst = el("p", { role: "status", class: "muted" });

  const form = el("form", { novalidate: true },
    el("fieldset", {},
      el("legend", { text: t("ui.domener.legend") }),
      el("div", { class: "skjemafelt" },
        el("label", { for: "dm-host", text: t("ui.domener.felt.hostname") }),
        el("p", { class: "hjelpetekst", id: "dm-host-hjelp",
          text: t("ui.domener.hjelp.hostname") }),
        inp, feilEl)),
    knapp, ventetekst);

  function statusTekst(d) {
    return t(`domenestatus.${d.status}`, d.status);
  }

  function tegnListe(domener) {
    liste.removeAttribute("aria-busy");
    if (!domener.length) {
      sett(liste, TomTilstand({ tittel: t("ui.domener.tom_tittel"),
        tekst: t("ui.domener.tom_tekst") }));
      return;
    }
    const thead = el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: t("ui.domener.kol.hostname") }),
      el("th", { scope: "col", text: t("ui.domener.kol.status") }),
      el("th", { scope: "col", text: t("ui.domener.kol.sist") })));
    const tbody = el("tbody", {}, ...domener.map((d) =>
      el("tr", {},
        el("th", { scope: "row", text: d.hostname }),
        el("td", {}, statusTekst(d)),
        el("td", {}, d.siste_vellykkede_revalidering
          ? Tidspunkt(d.siste_vellykkede_revalidering)
          : (d.challenge_utloper
             ? el("span", {}, `${t("ui.domener.challenge_til")} `,
                 Tidspunkt(d.challenge_utloper))
             : "—")))));
    sett(liste,
      el("table", { class: "datatabell" },
        el("caption", { text: t("ui.domener.caption") }), thead, tbody));
  }

  // Generasjonen (Codex P2, samme form som rapportflaten): to `last()`-kall
  // KAN overlappe — den første innlastingen står ennå ute når en vellykket
  // utstedelse ber om en oppfriskning, og oppfriskningskroken under kan fyre
  // igjen mens en treg henting pågår. Uten et nummer avgjorde ANKOMSTREKKE-
  // FØLGEN hva som ble stående: et gammelt svar kunne overskrive den nyere
  // listen med et foreldet øyeblikksbilde — nettopp den raden kunden nettopp
  // la til, forsvunnet — og en gammel FEIL kunne bytte ut en nyere, riktig
  // tabell med en feiltilstand.
  let generasjon = 0;

  function last() {
    const min = ++generasjon;
    liste.setAttribute("aria-busy", "true");
    hentDomener().then((d) => {
      if (min !== generasjon) return;   // et nyere kall eier listen
      tegnListe(d.domener);
    }).catch((e) => {
      // 401 gjelder ØKTEN, ikke forespørselen: den skal virke også om svaret
      // er foreldet — sesjonen er ugyldig uansett hvem som spurte.
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (min !== generasjon) return;
      liste.removeAttribute("aria-busy");
      sett(liste, Feiltilstand({ paaProvIgjen: last }));
    });
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    sett(utfall);
    const host = inp.value.trim().toLowerCase();
    if (!HOSTNAME.test(host)) {
      inp.setAttribute("aria-invalid", "true");
      inp.setAttribute("aria-errormessage", "dm-host-feil");
      feilEl.textContent = t("ui.domener.feil.hostname");
      inp.focus();
      return;
    }
    form.setAttribute("aria-busy", "true");
    knapp.disabled = true;
    ventetekst.textContent = t("ui.domener.venter");
    try {
      const svar = await leggTilDomene(host);
      // TXT-oppskriften — verdien vises ÉN GANG og finnes ikke igjen.
      const dl = el("dl", { class: "kv" });
      kvRad(dl, t("ui.domener.txt_navn"), svar.txt_navn);
      kvRad(dl, t("ui.domener.txt_type"), "TXT");
      kvRad(dl, t("ui.domener.txt_verdi"),
        el("code", { class: "txtverdi", text: svar.txt_verdi }));
      sett(utfall,
        el("p", { text: t("ui.domener.utfall.utstedt") }),
        dl,
        // OPPFØRINGEN ER PERMANENT (Codex P2). Den ser ut som en
        // engangsutfordring, og en kunde som rydder etter seg fjerner den —
        // men revalideringen slår den opp hver dag, og autorisasjonen faller
        // bort etter 72 timer uten treff, mens fanen fortsatt viser
        // «verifisert». Kravet står derfor som vanlig brødtekst, ikke
        // `muted`: det er en instruksjon, ikke en fotnote.
        el("p", { text: t("ui.domener.behold") }),
        el("p", { class: "muted", text: t("ui.domener.en_gang") }),
        el("p", { class: "muted", text: t("ui.domener.auto") }));
      inp.value = "";
      last();
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (e instanceof ApiFeil && e.kode === "domene_challenge_avvist") {
        sett(utfall, el("p", { text: t("ui.domener.feil.avvist") }));
      } else if (e instanceof IngenTilgangFeil) {
        sett(utfall, el("p", { text: t("ui.domener.feil.ingen_tilgang") }));
      } else if (e instanceof ApiFeil && e.kode === "request_feilformet") {
        sett(utfall, el("p", { text: t("ui.domener.feil.hostname") }));
      } else {
        sett(utfall, el("p", { text: t("ui.feil_tittel") }));
      }
      meldLive(t("ui.feil_tittel"));
    } finally {
      form.removeAttribute("aria-busy");
      knapp.disabled = false;
      ventetekst.textContent = "";
    }
  });

  sett(rot,
    el("h2", { text: t("ui.domener.tittel") }),
    el("p", { class: "sub", text: t("ui.domener.under") }),
    form, utfall, liste);
  last();
  // Oppfriskningskroken (Codex P2). Flaten LOVER at statusen oppdateres når
  // fanen åpnes igjen, og verifiseringen skjer i bakgrunnen (arbeideren, ~5
  // min). Uten den hang samleflaten bare den cachede DOM-en tilbake: en
  // challenge som var blitt `verifisert` mens brukeren var på en annen fane
  // sto fortsatt `ventende` til hele siden ble lastet på nytt.
  //
  // Kroken laster BARE listen. Skjemaet og TXT-oppskriften røres ikke — den
  // verdien vises én gang og finnes ikke igjen, så en ombygging her ville
  // slettet det eneste stedet kunden kan lese den fra.
  return last;
}
