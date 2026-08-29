// Felles flate-ramme: de fem skjermtilstandene (SideStatus) rundt hvert kall.
// 401 og 403 er ULIKE (V2): 401 → global innloggingsflate (ctx.paaUautorisert),
// 403 → ingen-tilgang-tilstand PÅ flaten. Andre feil → feiltilstand med
// «Prøv igjen».
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { visStatus, meldLive } from "../komponenter.js";
import { visningsToken, erGjeldendeVisning } from "../ruter.js";
import { UautorisertFeil, IngenTilgangFeil } from "../api.js";

// Rammen VOKTER SKJERMEN, ikke bare suksessveien. Alle flater rendrer i ETT
// delt `hoved`, og et kall som er ute på nettet vet ikke at brukeren har
// navigert videre. Flatene hadde begynt å sjekke eierskapet inne i sine egne
// `tegnFn`, men FEILVEIEN ligger her inne (Codex P2): avvises kallet etter at
// hun har byttet rute, ble `tegnFn` aldri nådd, og `visStatus` erstattet ruten
// hun står i med den forrige flatens feiltilstand — komplett med en «Prøv
// igjen» som laster noe helt annet enn det skjermen viser. Én sjekk her dekker
// suksess, 403 og feil for alle flater, i stedet for én per kallsted der bare
// halvparten av veiene er dekket.
//
// `erGjeldende` er flatens EGET eierskap i tillegg til ruterens: en flate
// bytter visning internt (liste → detalj → editor) uten at ruterstempelet
// flyttes, og et svar fra en forlatt visning skal falle her på samme måte.
//
// `flyttFokus` sier at kallet er et SIDEBYTTE en tastaturbruker utløste, og at
// fokus skal følge med dit skjermen havner. Rammen river den fokuserte knappen
// synkront for lastetilstanden, så fokus faller til `body` uansett hvilken vei
// kallet ender opp på. Suksessveien kunne flaten dekke selv, inne i `tegnFn` —
// men 403 og feiltilstanden tegner RAMMEN, og der hadde flaten ingen krok å
// henge fokus på (Codex P2): «Tilbake» som lyktes flyttet fokus til listas
// overskrift, mens «Tilbake» som feilet lot det ligge på `body`, og eier måtte
// navigere seg fram til «Prøv igjen» forfra. Rammen flytter derfor fokus på de
// veiene den selv tegner.
export async function medStatus(hoved, ctx, lastFn, tegnFn, erGjeldende,
                                flyttFokus) {
  const minRute = visningsToken(hoved);
  const eierSkjermen = () => erGjeldendeVisning(hoved, minRute)
    && (!erGjeldende || erGjeldende());
  visStatus(hoved, { type: "laster" });
  try {
    const data = await lastFn();
    if (!eierSkjermen()) return;
    tegnFn(data);
  } catch (e) {
    // 401 er GLOBAL: økten er ute uansett hvilken flate svaret hører til, så
    // den går videre selv om denne visningen er forlatt.
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
    if (!eierSkjermen()) return;
    if (e instanceof IngenTilgangFeil) {
      visStatus(hoved, { type: "ingen_tilgang" });
      if (flyttFokus) fokuserOverskrift(hoved);
      meldLive(t("ui.ingen_tilgang_tittel"));
      return;
    }
    // «Prøv igjen» bærer `flyttFokus` videre: knappen forsvinner i det samme
    // den trykkes, så et nytt forsøk som feiler igjen har nøyaktig samme
    // problem — og et som lykkes skal lande der det opprinnelige sidebyttet
    // skulle.
    visStatus(hoved, { type: "feil",
      paaProvIgjen: () =>
        medStatus(hoved, ctx, lastFn, tegnFn, erGjeldende, flyttFokus) });
    if (flyttFokus) fokuserOverskrift(hoved);
    meldLive(t("ui.feil_tittel"));
  }
}

// KEYSET-LISTA: ett utvalg (filtre/kø) + «Vis mer» som utvider NØYAKTIG det
// utvalget. Cursoren er delt ut i ett bestemt utvalg og betyr ingenting i et
// annet, så et filterbytte er en NY liste — `lastForste` nullstiller rader og
// cursor.
//
// Et svar hører til utvalget som ba om det. Uten den regelen kunne en «Vis
// mer» som alt var ute komme tilbake ETTER at eier byttet kø, og `lastMer`
// skrev både radene og cursoren fra det FORRIGE utvalget inn i det nye
// (Codex P2): gamle rader tegnet under den nye køens overskrift, og en cursor
// som pekte inn i en kø flaten ikke lenger viser. `fetch` kan ikke trekkes
// tilbake gjennom `hentJson`, så svaret slippes i stedet — samme grep som
// `medStatus` gjør for en forlatt visning, ett hakk lenger inn.
//
// Regelen bor HER, ikke i hver flate. Formen var kopiert per flate — samme
// `st`, samme to laste-funksjoner — og en vakt lagt i den ene ville latt den
// andre kopien stå med feilen; det er nettopp derfor `medStatus` selv ble
// løftet hit. Flaten eier fortsatt spørsmålet (`sok` lukker over filtrene
// sine); lista eier svaret.
export function keysetListe({ hoved, ctx, st, sok, tegn, rader }) {
  let utvalg = 0;
  function lastForste() {
    const min = ++utvalg;
    return medStatus(hoved, ctx, () => sok(null), (d) => {
      st.rader = rader(d); st.neste = d.neste_cursor; tegn();
    }, () => min === utvalg);
  }
  async function lastMer() {
    // «Vis mer» starter ikke et nytt utvalg — den utvider det som står.
    const min = utvalg;
    try {
      const d = await sok(st.neste);
      if (min !== utvalg) return;
      st.rader = st.rader.concat(rader(d)); st.neste = d.neste_cursor; tegn();
      meldLive(`${st.rader.length}`);
    } catch (e) {
      // 401 er GLOBAL — økten er ute uansett hvilket utvalg svaret hørte til.
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (min !== utvalg) return;
      meldLive(t("ui.feil_tittel"));
    }
  }
  return { lastForste, lastMer };
}

// Fokus til sidens overskrift: siden ble byttet ut, og uten dette står fokus
// igjen på et element som ikke er på skjermen lenger — i praksis på `body`, der
// tastaturnavigasjonen starter forfra. Bor her fordi både flatene og rammen
// rundt dem tegner sider som må ta imot fokus.
export function fokuserOverskrift(hoved) {
  const h = hoved.querySelector("h1, h2");
  if (h) { h.setAttribute("tabindex", "-1"); h.focus(); }
}

// Overskrift + valgfri undertittel for en flate.
export function flateHode(tittel, undertittel) {
  const barn = [el("h1", { text: tittel })];
  if (undertittel) barn.push(el("p", { class: "sub", text: undertittel }));
  return barn;
}

// dt/dd-rad i en .kv-liste.
export function kvRad(dl, navn, verdi) {
  dl.append(el("dt", { text: navn }),
    el("dd", {}, verdi == null ? "—" : (verdi.nodeType ? verdi : String(verdi))));
}
