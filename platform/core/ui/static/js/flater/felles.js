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
export async function medStatus(hoved, ctx, lastFn, tegnFn, erGjeldende) {
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
      meldLive(t("ui.ingen_tilgang_tittel"));
      return;
    }
    visStatus(hoved, { type: "feil",
      paaProvIgjen: () => medStatus(hoved, ctx, lastFn, tegnFn, erGjeldende) });
    meldLive(t("ui.feil_tittel"));
  }
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
