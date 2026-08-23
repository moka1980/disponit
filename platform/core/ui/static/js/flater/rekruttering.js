// M-57 Rekruttering (klarsignalet §8). Flaten viser ÉN prosess om gangen:
// kandidatlisten som <table> med caption/scope/aria-sort, vektene som
// range-kontroller med synlig verdi og ny rekkefølge annonsert i
// aria-live="polite", blindingsbryteren med alertdialog ved AVSKRUING
// (valget auditeres på serveren), detaljpanelet som dialog med fokusfelle,
// og signaturdialogen som sier antall, listetype og hashens kortform før
// den irreversible utsendelsen. Utfall meldes i role="alert".
//
// Trafikklyset er ALDRI bare farge: kategorien står som tekst i cellen, og
// fargen er en klasse oppå — en monokrom skjerm og en skjermleser får
// nøyaktig samme dom.
//
// Poeng er poeng (§6): flaten regner aldri om til prosent, og
// re-rangeringen ved vektendring er RENT klientarbeid på nedbrytningen
// serveren alt har levert — samme sum som `evaluering.ranger`, aldri en
// egen sannhet.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, settRekrutteringBlinding, signerRekrutteringsliste,
         nyIdempotensnokkel, UautorisertFeil } from "../api.js";
import { harScope } from "../sitekart.js";
import { DataTabell } from "../tabell.js";
import { Detaljpanel, Bekreftelsesdialog } from "../dialog.js";
import { medStatus, flateHode } from "./felles.js";

function meldFeil(ctx, utfall, e) {
  // 401 ER IKKE EN HANDLINGSFEIL (Codex P1). Utløper økten mellom
  // lastingen av flaten og mutasjonen, kaster `api.js` UautorisertFeil —
  // og fanget den her som «noe gikk galt», ble brukeren stående i det
  // innloggede skallet med en flate som ikke lenger har en økt bak seg.
  // Resten av klienten sender 401 til `ctx.paaUautorisert` (V2: 401 →
  // innlogging, 403 → ingen tilgang); disse to mutasjonene er ikke et
  // unntak fra den regelen.
  if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
  sett(utfall, t("ui.rekruttering.feil_utfall"));
}

function kortHash(hash) {
  // Kortformen i signaturdialogen (§8): nok til å pare mot listen i
  // revisjonssporet, kort nok til å leses opp.
  return `${(hash || "").slice(0, 12)}…`;
}

export function visRekruttering(hoved, ctx) {
  // ØKTEN OVERLEVER TEGNINGEN (Codex P1 / Cursor P1). Alt som handler om
  // en IRREVERSIBEL operasjon — idempotensnøkkelen som lar serveren
  // replaye, og «denne listen ER signert» — må høre til den lastede
  // flaten, ikke til én `tegn`-lukning. Prosessvelgeren tegner flaten på
  // nytt mot det SAMME svaret, og lå nøklene inne i `tegn`, fikk et bytte
  // fram og tilbake både en fersk nøkkel (så en retry etter et tapt 2xx
  // ble en NY operasjon serveren ikke kan replaye) og en levende
  // «Signer»-knapp på en liste som alt var sendt. Begge holdes derfor her.
  const okt = { signeringsnokler: new Map(), signerte: new Set() };
  medStatus(hoved, ctx,
    () => hentJson("/v1/rekruttering/prosesser"),
    (data) => tegn(hoved, ctx, data, okt));
}

function tegn(hoved, ctx, data, okt, valgtId) {
  const prosesser = (data && data.prosesser) || [];
  if (!prosesser.length) {
    sett(hoved, flateHode(t("ui.rekruttering.tittel")),
      el("p", { text: t("ui.rekruttering.ingen_prosess") }));
    return;
  }
  // FLERE PROSESSER ER TILGJENGELIGE, IKKE BARE DEN FØRSTE (Codex P2).
  // Endepunktet er i flertall, og ruten bærer ingen prosess-id, så med
  // `prosesser[0]` var enhver senere prosess — kandidatlisten og de
  // usignerte utsendingene hennes — utilgjengelig for en tenant med mer
  // enn én pågående rekruttering. Velgeren vises bare når det FINNES noe
  // å velge mellom; valget tegner flaten på nytt for den prosessen.
  const prosess = prosesser.find((p) => p.prosess_id === valgtId)
    || prosesser[0];
  let velgerRot = null;
  if (prosesser.length > 1) {
    const velgerId = "rekrut-prosessvelger";
    const velger = el("select", { id: velgerId },
      ...prosesser.map((p) => el("option",
        { value: p.prosess_id,
          ...(p.prosess_id === prosess.prosess_id ? { selected: "" } : {}) },
        p.navn || p.prosess_id)));
    velger.value = prosess.prosess_id;
    velger.addEventListener("change", () => {
      tegn(hoved, ctx, data, okt, velger.value);
      const ny = hoved.querySelector(`#${velgerId}`);
      if (ny) ny.focus();
    });
    velgerRot = el("div", { class: "rekrut-prosessvelger" },
      el("label", { for: velgerId,
        text: t("ui.rekruttering.prosessvelger") }),
      velger);
  }
  const vekter = { ...prosess.vekter };
  const kanBestille = harScope(ctx, "bestilling:opprett");

  // Utfallsområdet: role=alert — signering og blinding melder hit.
  const utfall = el("div", { role: "alert", class: "rekrut-utfall" });
  // Re-rangeringens kunngjøring: høflig, aldri avbrytende.
  const kunngjoring = el("div", { "aria-live": "polite",
    class: "sr-only rekrut-kunngjoring" });

  function poengFor(kandidat) {
    // Samme regel som evaluering.ranger: vekt teller når kravet er
    // oppfylt. `oppfylt` er serverens dom; vekten er brukerens valg.
    let sum = 0;
    for (const [krav, vekt] of Object.entries(vekter)) {
      if (kandidat.oppfylt && kandidat.oppfylt[krav]) sum += Number(vekt);
    }
    return sum;
  }

  const tabellRot = el("div", { class: "rekrut-tabell" });

  function tegnTabell() {
    const rader = prosess.kandidater
      .map((k) => ({ kandidat: k, poeng: poengFor(k) }))
      .sort((a, b) => b.poeng - a.poeng
        || (a.kandidat.kandidat_id < b.kandidat.kandidat_id ? -1 : 1));
    sett(tabellRot, DataTabell({
      captionTekst: t("ui.rekruttering.tabell_caption"),
      kolonner: [
        { nokkel: "kandidat", tittel: t("ui.rekruttering.kol_kandidat") },
        { nokkel: "poeng", tittel: t("ui.rekruttering.kol_poeng"),
          sorterbar: true },
        { nokkel: "kategori", tittel: t("ui.rekruttering.kol_kategori") },
      ],
      sort: { nokkel: "poeng", retning: "descending" },
      rader: rader.map(({ kandidat, poeng }) => ({
        celler: {
          kandidat: kandidat.kandidat_id,
          poeng: String(poeng),
          // Trafikklys: tekst + klasse, aldri farge alene.
          kategori: el("span",
            { class: `trafikklys trafikklys-${kandidat.status}` },
            el("span", { class: "trafikklys-prikk", "aria-hidden": "true" }),
            t(`ui.rekruttering.status.${kandidat.status}`)),
        },
        sortverdi: { poeng },
        handling: {
          tekst: t("ui.rekruttering.detaljer"),
          utfor: () => visDetalj(kandidat, poeng),
        },
      })),
    }));
    return rader;
  }

  function visDetalj(kandidat, poeng) {
    // Sidepanelet er en dialog med fokusfelle (Detaljpanel → aapneDialog).
    const funn = (kandidat.funn || []).map((f) =>
      el("li", {},
        el("strong", { text: t(`ui.rekruttering.funn.${f.kategori}`) }),
        " — ",
        el("q", { text: f.kilde.sitat })));
    const sporsmal = (kandidat.intervjusporsmal || []).map((s) =>
      el("li", { text: s }));
    Detaljpanel({
      tittel: `${t("ui.rekruttering.kandidat")} ${kandidat.kandidat_id}`,
      innhold: el("div", {},
        el("p", { text: `${t("ui.rekruttering.kol_poeng")}: ${poeng}` }),
        el("h3", { text: t("ui.rekruttering.funn_tittel") }),
        funn.length ? el("ul", {}, ...funn)
          : el("p", { text: t("ui.rekruttering.ingen_funn") }),
        el("h3", { text: t("ui.rekruttering.sporsmal_tittel") }),
        sporsmal.length ? el("ul", {}, ...sporsmal)
          : el("p", { text: t("ui.rekruttering.ingen_sporsmal") })),
    });
  }

  // Vektene: én range per krav, med <label>, synlig verdi og
  // kunngjort re-rangering. Tastatur: piltaster på range er nok.
  //
  // TAKET FØLGER KONTRAKTEN, IKKE OMVENDT (Codex P1). `evaluering.ranger`
  // godtar ETHVERT ikke-negativt heltall som vekt; `max="10"` var flatens
  // egen påstand om noe annet. Kom en gyldig vekt på 20 inn, klemte
  // nettleseren kontrollens verdi til 10 mens `vekter` og den synlige
  // `output`-en fortsatt sa 20: poengsummene i tabellen var regnet på 20,
  // skyveren sto på taket, og brukerens FØRSTE piltast hoppet vekten fra
  // 20 til 9 — en stille omrangering av kandidatene. Taket regnes derfor
  // ut fra verdiene serveren faktisk sendte (aldri under husets 10, så en
  // vanlig prosess får samme skala som før), og alle kravene deler det, så
  // skyverne fortsatt kan sammenliknes med øyet.
  const VEKT_MAKS_STANDARD = 10;
  const vektMaks = Object.values(vekter).reduce((maks, v) => {
    const tall = Number(v);
    return Number.isFinite(tall) && tall > maks ? tall : maks;
  }, VEKT_MAKS_STANDARD);
  const vektRot = el("fieldset", { class: "rekrut-vekter" },
    el("legend", { text: t("ui.rekruttering.vekter_tittel") }));
  for (const [krav, verdi] of Object.entries(vekter)) {
    const id = `vekt-${krav}`;
    const visning = el("output", { for: id, text: String(verdi) });
    const range = el("input", { type: "range", id, min: "0",
      max: String(vektMaks), step: "1", value: String(verdi) });
    range.addEventListener("input", () => {
      vekter[krav] = Number(range.value);
      visning.textContent = range.value;
      const rader = tegnTabell();
      kunngjoring.textContent = t("ui.rekruttering.ny_rekkefolge")
        .replace("{forst}", rader.length ? rader[0].kandidat.kandidat_id : "");
    });
    vektRot.append(el("div", { class: "rekrut-vekt" },
      el("label", { for: id, text: t(`ui.rekruttering.krav.${krav}`, krav) }),
      range, visning));
  }

  // Blindingsbryteren: PÅ er standard. AVSKRUING åpner alertdialog med
  // påkrevd begrunnelse — valget auditeres på serveren (§6).
  const blindingId = "rekrut-blinding";
  const bryter = el("input", { type: "checkbox", id: blindingId });
  bryter.checked = !prosess.blinding_av;
  // Samme scope-gate som signeringen (CodeRabbit major, første pass):
  // en bruker uten mutasjonsscopet skal ikke engang tilbys valget —
  // en bryter som bare kan gi 403 er et løfte flaten ikke kan holde.
  if (!kanBestille) bryter.disabled = true;
  // IN-FLIGHT-LÅS (Cursor P2). Blinding og signering er auditerte,
  // henholdsvis irreversible handlinger, og uten lås kunne brukeren fyre
  // av nummer to mens nummer én hang: to POST-er på samme valg, og
  // bryteren som følger det svaret som tilfeldigvis kom sist. Låsen er
  // per kontroll, ikke per flate, og løftes alltid — også ved feil, så en
  // mislykket runde ikke etterlater en død bryter.
  async function laast(kontroll, arbeid) {
    if (kontroll.disabled) return;
    kontroll.disabled = true;
    try {
      return await arbeid();
    } finally {
      if (kanBestille && !kontroll.dataset.ferdig) kontroll.disabled = false;
    }
  }
  bryter.addEventListener("change", async () => {
    if (bryter.checked) {
      // PÅ igjen er OGSÅ en mutasjon (CodeRabbit major: UI-tilstanden
      // skiftet uten at serveren fikk vite det). Ingen dialog — å slå
      // blindingen PÅ er standardtilstanden — men kallet må gå, og
      // bryteren følger UTFALLET, aldri klikket.
      bryter.checked = false;
      await laast(bryter, async () => {
        try {
          // Begrunnelsen er REVISJONSINNHOLD, men den er også tekst
          // koden skriver (Cursor P2 / port 32): hardkodet sto den norsk
          // også i en engelsk UI. Av-veien får brukerens egne ord;
          // på-veien får husets, via locale.
          await settRekrutteringBlinding(prosess.prosess_id, false,
            t("ui.rekruttering.blinding_pa_begrunnelse"));
          // MODELLEN, IKKE BARE BRYTEREN (Codex P1). `prosess` er objektet
          // i det hentede svaret, og det svaret er alt en ny tegning har å
          // gå på: sto `blinding_av` igjen slik serveren svarte FØR
          // mutasjonen, kunne et prosessbytte og tilbake vise en bryter som
          // påstår PÅ mens serveren har AV. Det er feil revisjonsbilde rett
          // før en evaluering, og bryteren skal aldri lyve om §6-valget.
          prosess.blinding_av = false;
          bryter.checked = true;
          sett(utfall, t("ui.rekruttering.blinding_pa_utfall"));
        } catch (e) {
          meldFeil(ctx, utfall, e);
        }
      });
      return;
    }
    bryter.checked = true;                // står til dialogen bekrefter
    const begrunnelse = el("textarea", { id: "blinding-begrunnelse",
      rows: "3", required: true, "aria-required": "true" });
    // Meldingen om manglende begrunnelse hører hjemme INNE i dialogen
    // (Codex P2): utfallsområdet på flaten ligger bak `inert`-bakgrunnen
    // og fokusfella mens dialogen står, så der ville brukeren verken se
    // eller høre den.
    const dialogfeil = el("p", { role: "alert", class: "dialog-feil" });
    Bekreftelsesdialog({
      rolle: "alertdialog",
      tittel: t("ui.rekruttering.blinding_av_tittel"),
      tekst: t("ui.rekruttering.blinding_av_tekst"),
      detaljer: el("div", {},
        el("label", { for: "blinding-begrunnelse",
          text: t("ui.rekruttering.blinding_begrunnelse") }),
        begrunnelse, dialogfeil),
      farlig: true,
      primarTekst: t("ui.rekruttering.blinding_av_bekreft"),
      // Porten står FØR lukkingen: begrunnelsen er påkrevd, og et
      // tomt felt skal kunne rettes der brukeren står.
      valider: () => {
        if (begrunnelse.value.trim()) {
          begrunnelse.removeAttribute("aria-invalid");
          dialogfeil.textContent = "";
          return true;
        }
        begrunnelse.setAttribute("aria-invalid", "true");
        dialogfeil.textContent =
          t("ui.rekruttering.blinding_begrunnelse_mangler");
        begrunnelse.focus();
        return false;
      },
      paaPrimar: () => laast(bryter, async () => {
        try {
          await settRekrutteringBlinding(prosess.prosess_id, true,
            begrunnelse.value.trim());
          prosess.blinding_av = true;      // se PÅ-veien over
          bryter.checked = false;
          sett(utfall, t("ui.rekruttering.blinding_av_utfall"));
        } catch (e) {
          meldFeil(ctx, utfall, e);
        }
      }),
    });
  });
  const blindingRot = el("div", { class: "rekrut-blinding" },
    bryter,
    el("label", { for: blindingId,
      text: t("ui.rekruttering.blinding_etikett") }));

  // Innstilte lister: signering er den irreversible handlingen, og
  // dialogen sier nøyaktig hva som skjer (§8) — antall, listetype,
  // hashens kortform, «Kan ikke angres».
  const listeRot = el("div", { class: "rekrut-lister" },
    el("h2", { text: t("ui.rekruttering.lister_tittel") }));
  // ÉN NØKKEL PER (liste, innholdshash) (Codex P1). Uten arg lager
  // `api.js` en fersk nøkkel per kall, og signeringen er nettopp den
  // operasjonen der det er farlig: commiter serveren og svaret går tapt,
  // melder flaten «feil», og brukerens neste klikk kommer med en NY
  // nøkkel — da replayer ikke serveren, den ser en ny operasjon, og
  // klienten kan ikke lenger avgjøre om posten er sendt. Nøkkelen holdes
  // derfor til vi har et definitivt svar; endres innholdshashen, er det
  // en annen operasjon og en annen nøkkel (mønsteret fra bestilling.js).
  // Kartet ligger i ØKTEN (`visRekruttering`), ikke her: se der.
  function listenokkel(liste) {
    return `${liste.liste_id}|${liste.innhold_hash}`;
  }
  function signeringsnokkel(liste) {
    const id = listenokkel(liste);
    if (!okt.signeringsnokler.has(id)) {
      okt.signeringsnokler.set(id, nyIdempotensnokkel());
    }
    return okt.signeringsnokler.get(id);
  }
  for (const liste of prosess.lister || []) {
    const knapp = el("button", { class: "knapp", type: "button",
      text: t("ui.rekruttering.signer_knapp") });
    // En liste som ER signert i denne økten, kommer tilbake død — også
    // etter et prosessbytte, der `data` fortsatt er det svaret som ble
    // hentet FØR signeringen og derfor viser listen som usignert.
    if (okt.signerte.has(listenokkel(liste))) knapp.dataset.ferdig = "1";
    if (!kanBestille || knapp.dataset.ferdig) {
      knapp.setAttribute("disabled", "");
    }
    knapp.addEventListener("click", () => {
      Bekreftelsesdialog({
        rolle: "alertdialog",
        farlig: true,
        tittel: t("ui.rekruttering.signer_tittel"),
        // `replaceAll`, ikke `replace` (Cursor P1): `{antall}` står TO
        // ganger i teksten — «… · 42 mottakere · … Dette sender {antall}
        // e-poster» — og port 31 krever at setningen sier tallet, ikke
        // plassholderen. Samme regel for de øvrige feltene: en tekst som
        // gjentar et felt skal ikke avhenge av hvor i strengen det står.
        tekst: t("ui.rekruttering.signer_tekst")
          .replaceAll("{antall}", String(liste.antall))
          .replaceAll("{listetype}",
            t(`ui.rekruttering.listetype.${liste.listetype}`))
          .replaceAll("{hash}", kortHash(liste.innhold_hash)),
        primarTekst: t("ui.rekruttering.signer_bekreft"),
        paaPrimar: () => laast(knapp, async () => {
          try {
            const svar = await signerRekrutteringsliste(
              liste.liste_id, liste.innhold_hash,
              signeringsnokkel(liste));
            // Signert er signert: knappen står igjen død, så den
            // irreversible handlingen ikke kan gjentas fra denne visningen
            // — og merket ligger i ØKTEN, så heller ikke fra den neste.
            okt.signerte.add(listenokkel(liste));
            knapp.dataset.ferdig = "1";
            sett(utfall, t("ui.rekruttering.signer_utfall")
              .replaceAll("{hash}", kortHash(svar.innhold_hash
                || liste.innhold_hash)));
          } catch (e) {
            meldFeil(ctx, utfall, e);
          }
        }),
      });
    });
    listeRot.append(el("div", { class: "rekrut-liste" },
      el("span", { text:
        `${t(`ui.rekruttering.listetype.${liste.listetype}`)} · `
        + `${liste.antall} · ${kortHash(liste.innhold_hash)}` }),
      knapp));
  }

  sett(hoved, flateHode(t("ui.rekruttering.tittel")), velgerRot,
    utfall, kunngjoring, blindingRot, vektRot, tabellRot, listeRot);
  tegnTabell();
}
