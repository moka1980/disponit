// KUNDENE — ett sted, og det er hele poenget med flaten.
//
// EIERS ORD 11/9 (ordrett): «det skal være en enkel plass der firmaene
// enten fyller ut et enkelt skjema om seg selv og legge til deres kunder
// … Ikke gå gjennom hver modul og fylle, så hva blir vitsen hvis alt må
// fylles manuelt».
//
// ÅRSAKEN FLATEN SVARER PÅ, målt før den ble tegnet: `kunde_ref` var
// FRITEKST i fire tabeller uten en eneste fremmednøkkel, og tre moduler
// bar hver sin krypterte kontakt. En kunde lagt inn i tilbud fantes ikke
// i fordring. Registeret (183) er ett sted; dette er døra inn til det.
//
// PRODUKTET FØRST (eiers stående UX-prinsipp 27/8: færrest klikk til
// produktet). Lista står øverst og er det første som tegnes. Skjemaet
// står under den, alltid åpent — tre felter, ikke en «Ny kunde»-knapp
// som skjuler et skjema bak et klikk til.
//
// TOMTILSTANDEN SIER HVORDAN MAN STARTER. «Ingen kunder ennå» er en
// observasjon; en flate som stopper der lar brukeren gjette. 131 av
// husets 234 tomtekster gjorde nettopp det, og det var en del av
// klagen.
//
// ADRESSEN VISES ALDRI HEL. Registeret gir masker, og flaten har ingen
// vei til klarteksten — den finnes bare i ciphertext, og modulene som
// skal SENDE henter den gjennom sin egen dekrypteringsvei.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentParter, registrerPart, settPartKontakt, deaktiverPart,
         UautorisertFeil, ApiFeil } from "../api.js";
import { TomTilstand, meldLive } from "../komponenter.js";
import { Bekreftelsesdialog } from "../dialog.js";
import { medStatus, flateHode } from "./felles.js";

const SKRIVESCOPE = "part:administrer";

// SØKET FILTRERER I REGISTERET, ikke i de 500 radene flaten har. En
// klient som filtrerte selv ville sagt «ingen treff» om en kunde som
// ligger rett bak sidegrensen — samme feilform som dashbordet hadde.
export function partsrad(p, kanSkrive, paaVelg, paaAvvikle) {
  const tr = el("tr", {});
  tr.append(el("th", { scope: "row", text: p.navn }));
  tr.append(el("td", { text: p.part_ref }));
  tr.append(el("td", { text: p.orgnummer || "—" }));
  // KONTAKT SOM TEKST, aldri bare et ikon: masken ER informasjonen, og
  // «mangler» er en tilstand brukeren skal kunne lese (WCAG 1.4.1).
  const k = [];
  if (p.epost_maske) k.push(p.epost_maske);
  if (p.telefon_maske) k.push(p.telefon_maske);
  tr.append(el("td", { text: k.length ? k.join(" · ")
                                      : t("ui.parter.uten_kontakt") }));
  tr.append(el("td", { text: p.aktiv ? t("ui.parter.status.aktiv")
                                     : t("ui.parter.status.avviklet") }));
  const rad = el("div", { class: "knapperad" });
  if (kanSkrive && p.aktiv) {
    const b = el("button", { type: "button",
      text: t("ui.parter.knapp.kontakt") });
    b.addEventListener("click", () => paaVelg(p));
    rad.append(b);
    const a = el("button", { class: "knapp fare", type: "button",
      text: t("ui.parter.knapp.avvikle") });
    a.addEventListener("click", () => paaAvvikle(p));
    rad.append(a);
  }
  tr.append(el("td", {}, rad));
  return tr;
}

export function partstabell(parter, kanSkrive, paaVelg, paaAvvikle) {
  const tabell = el("table", {},
    el("caption", { text: t("ui.parter.tabell") }),
    el("thead", {}, el("tr", {},
      el("th", { scope: "col", text: t("ui.parter.kol.navn") }),
      el("th", { scope: "col", text: t("ui.parter.kol.ref") }),
      el("th", { scope: "col", text: t("ui.parter.kol.orgnr") }),
      el("th", { scope: "col", text: t("ui.parter.kol.kontakt") }),
      el("th", { scope: "col", text: t("ui.parter.kol.status") }),
      el("th", { scope: "col", text: t("ui.parter.kol.handling") }))));
  const tbody = el("tbody", {});
  for (const p of parter) {
    tbody.append(partsrad(p, kanSkrive, paaVelg, paaAvvikle));
  }
  tabell.append(tbody);
  return el("div", { class: "tablewrap" }, tabell);
}

// SKJEMAET ER TRE FELTER. Medianen i huset er tolv, og 25 flater har
// over femten — det var halve klagen. Alt annet enn navn og referanse
// kan legges til etterpå, og en kunde uten organisasjonsnummer er en
// helt vanlig kunde.
// `novalidate` + EGEN VALIDERING er husets §7-kontrakt (bestilling.js,
// domener.js, kunnskap.js), og grunnen ble målt her: med bare `required`
// blokkerer nettleseren innsendingen SELV, «submit» fyres aldri, og
// flatens egen — oversatte — melding er uoppnåelig. Brukeren får i
// stedet nettleserens boble på nettleserens språk. `required` blir
// stående fordi skjermlesere annonserer den.
function merkFeil(inp, feilEl, melding) {
  feilEl.textContent = melding;
  inp.setAttribute("aria-invalid", "true");
  inp.setAttribute("aria-errormessage", feilEl.id);
  inp.focus();
}

function nullstillFeil(feilEl, ...felter) {
  feilEl.textContent = "";
  for (const f of felter) {
    f.removeAttribute("aria-invalid");
    f.removeAttribute("aria-errormessage");
  }
}

function nyKundeSkjema(paaLagre) {
  const ref = el("input", { id: "part-ref", class: "felt-inp", required: true,
    maxlength: 100, autocomplete: "off" });
  const navn = el("input", { id: "part-navn", class: "felt-inp",
    required: true, maxlength: 200, autocomplete: "organization" });
  const org = el("input", { id: "part-orgnr", class: "felt-inp",
    maxlength: 20, inputmode: "numeric", autocomplete: "off" });
  const feil = el("p", { id: "part-feil", class: "muted", role: "alert" });
  const skjema = el("form", { class: "kv-skjema", novalidate: true },
    el("label", { for: "part-ref", text: t("ui.parter.felt.ref") }), ref,
    el("label", { for: "part-navn", text: t("ui.parter.felt.navn") }), navn,
    el("label", { for: "part-orgnr", text: t("ui.parter.felt.orgnr") }), org,
    el("button", { type: "submit", text: t("ui.parter.knapp.lagre") }),
    feil);
  skjema.addEventListener("submit", (e) => {
    e.preventDefault();
    nullstillFeil(feil, ref, navn, org);
    if (!ref.value.trim() || !navn.value.trim()) {
      // FOKUS TIL FØRSTE FEIL, ikke bare en melding et sted på siden.
      merkFeil(ref.value.trim() ? navn : ref, feil,
               t("ui.parter.feil.mangler"));
      return;
    }
    // ORGANISASJONSNUMMERET MÅLES HER OGSÅ (CodeRabbit). Serveren
    // avviser det uansett, men da peker feilen på skjemaet som helhet —
    // og brukeren må gjette hvilket av tre felter hun skrev feil.
    // Mellomrom er lov, fordi mennesker skriver «912 345 678».
    const orgtall = org.value.replace(/\s/g, "");
    if (orgtall && !/^[0-9]{9}$/.test(orgtall)) {
      merkFeil(org, feil, t("ui.parter.feil.orgnr"));
      return;
    }
    paaLagre({ part_ref: ref.value.trim(), navn: navn.value.trim(),
               orgnummer: orgtall || null },
             (melding) => {
               // FEILEN STÅR VED SKJEMAET, ikke bare øverst på siden
               // (husets egen lærdom: «errors only at top» er en
               // antipattern, og brukeren ser ikke toppen når hun står
               // i feltet).
               merkFeil(ref, feil, melding);
             },
             () => { ref.value = ""; navn.value = ""; org.value = "";
                     nullstillFeil(feil, ref, navn, org); ref.focus(); });
  });
  return { node: el("section", {},
    el("h2", { text: t("ui.parter.ny") }), skjema), fokuser: () => ref.focus() };
}

// KONTAKTPUNKTET legges til på den valgte kunden. Feltet er ETT, og
// kanalen er et valg — ikke to skjemaer å velge mellom.
function kontaktskjema(part, paaLagre, paaLukk) {
  const kanal = el("select", { id: "kontakt-kanal", class: "felt-inp" },
    el("option", { value: "epost", text: t("ui.parter.kanal.epost") }),
    el("option", { value: "telefon", text: t("ui.parter.kanal.telefon") }));
  const verdi = el("input", { id: "kontakt-verdi", class: "felt-inp",
    required: true, maxlength: 320, autocomplete: "off" });
  const feil = el("p", { id: "kontakt-feil", class: "muted", role: "alert" });
  const skjema = el("form", { class: "kv-skjema", novalidate: true },
    el("label", { for: "kontakt-kanal", text: t("ui.parter.felt.kanal") }),
    kanal,
    el("label", { for: "kontakt-verdi", text: t("ui.parter.felt.verdi") }),
    verdi,
    el("button", { type: "submit", text: t("ui.parter.knapp.lagre") }),
    feil);
  skjema.addEventListener("submit", (e) => {
    e.preventDefault();
    nullstillFeil(feil, verdi);
    if (!verdi.value.trim()) {
      merkFeil(verdi, feil, t("ui.parter.feil.mangler"));
      return;
    }
    paaLagre({ kanal: kanal.value, verdi: verdi.value.trim() },
             (m) => merkFeil(verdi, feil, m),
             // FELTET TØMMES VED SUKSESS (CodeRabbit). To grunner: den
             // neste kontakten skal kunne skrives med én gang, og
             // adressen skal ikke bli stående i klartekst i DOM-en etter
             // at registeret har tatt imot den maskert.
             () => { verdi.value = ""; nullstillFeil(feil, verdi);
                     verdi.focus(); });
  });
  const lukk = el("button", { type: "button",
    text: t("ui.parter.knapp.lukk") });
  lukk.addEventListener("click", paaLukk);
  const boks = el("section", { class: "skjemaboks", tabindex: "-1" },
    el("h2", { text: t("ui.parter.kontakt_for").replace("{navn}", part.navn) }),
    // ADRESSEN SIES Å BLI SKJULT, før den skrives. En bruker som ikke
    // vet at feltet krypteres, skriver enten for lite eller for mye.
    el("p", { class: "muted", text: t("ui.parter.kontakt_note") }),
    skjema, el("div", { class: "knapperad" }, lukk));
  return { node: boks, fokuser: () => { boks.focus(); verdi.focus(); } };
}

export function visParter(hoved, ctx) {
  const kanSkrive = (ctx.scopes || []).includes(SKRIVESCOPE);
  let sok = "";
  let valgt = null;

  const tegn = () => medStatus(hoved, ctx,
    () => hentParter(sok || null, 500),
    (d) => {
      const parter = d.parter || [];
      const deler = [...flateHode(t("ui.parter.tittel"),
                                  t("ui.parter.undertittel"))];

      // SØKET STÅR OVER LISTA, og bare når det er noe å søke i.
      if (parter.length || sok) {
        const felt = el("input", { id: "part-sok", type: "search",
          class: "felt-inp", value: sok,
          placeholder: t("ui.parter.sok_plassholder") });
        const f = el("form", { class: "kv-skjema" },
          el("label", { class: "sr-only", for: "part-sok",
            text: t("ui.parter.sok") }), felt,
          el("button", { type: "submit", text: t("ui.parter.sok") }));
        f.addEventListener("submit", (e) => {
          e.preventDefault(); sok = felt.value.trim(); tegn();
        });
        deler.push(f);
      }

      if (parter.length) {
        deler.push(partstabell(parter, kanSkrive,
          (p) => { valgt = p; tegn(); },
          (p) => bekreftAvvikling(p)));
        if (d.avkortet) {
          deler.push(el("p", { class: "muted",
            text: t("ui.parter.avkortet") }));
        }
      } else {
        // TOMTILSTANDEN SIER HVA MAN GJØR NÅ. Med søk: at søket ikke
        // traff. Uten: hvor man begynner.
        deler.push(TomTilstand(sok
          ? { tittel: t("ui.parter.tom_sok_tittel"),
              tekst: t("ui.parter.tom_sok_tekst") }
          : { tittel: t("ui.parter.tom_tittel"),
              tekst: kanSkrive ? t("ui.parter.tom_tekst")
                               : t("ui.parter.tom_tekst_leser") }));
      }

      if (valgt) {
        const k = kontaktskjema(valgt,
          (kropp, paaFeil, paaOk) => lagre(
            () => settPartKontakt(valgt.part_id, kropp),
            t("ui.parter.kvittering.kontakt"), paaFeil, paaOk),
          () => { valgt = null; tegn(); });
        deler.push(k.node);
        sett(hoved, ...deler);
        k.fokuser();
        return;
      }

      if (kanSkrive) deler.push(nyKundeSkjema(
        (kropp, paaFeil, paaOk) => lagre(() => registrerPart(kropp),
          t("ui.parter.kvittering.ny"), paaFeil, paaOk)).node);
      sett(hoved, ...deler);
    });

  // ÉN VEI FOR ALLE MUTASJONER: kall, kvittér, tegn på nytt. Flaten
  // gjetter aldri på hva registeret nå inneholder — den spør.
  function lagre(kall, kvittering, paaFeil, paaOk) {
    return kall()
      .then(() => { meldLive(kvittering); if (paaOk) paaOk(); tegn(); })
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        const kode = e instanceof ApiFeil ? e.kode : null;
        paaFeil(kode === "part_ulovlig_tilstand"
          ? t("ui.parter.feil.tilstand")
          : (e && e.status === 400 ? t("ui.parter.feil.form")
                                   : t("ui.feilet")));
      });
  }

  function bekreftAvvikling(p) {
    // `farlig` + `alertdialog`: avviklingen er ENVEIS og starter
    // retensjonsklokken (184). Parametrene er dialogens egne
    // (`primarTekst`/`paaPrimar`), ikke oppfunnet her.
    Bekreftelsesdialog({
      tittel: t("ui.parter.avvikle_tittel"),
      tekst: t("ui.parter.avvikle_tekst").replace("{navn}", p.navn),
      primarTekst: t("ui.parter.knapp.avvikle"),
      farlig: true,
      rolle: "alertdialog",
      paaPrimar: () => lagre(() => deaktiverPart(p.part_id),
        t("ui.parter.kvittering.avviklet"), () => meldLive(t("ui.feilet"))),
    });
  }

  tegn();
}
