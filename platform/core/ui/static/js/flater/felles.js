// Felles flate-ramme: de fem skjermtilstandene (SideStatus) rundt hvert kall.
// 401 og 403 er ULIKE (V2): 401 → global innloggingsflate (ctx.paaUautorisert),
// 403 → ingen-tilgang-tilstand PÅ flaten. Andre feil → feiltilstand med
// «Prøv igjen».
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { visStatus, meldLive } from "../komponenter.js";
import { UautorisertFeil, IngenTilgangFeil } from "../api.js";

export async function medStatus(hoved, ctx, lastFn, tegnFn) {
  visStatus(hoved, { type: "laster" });
  try {
    const data = await lastFn();
    tegnFn(data);
  } catch (e) {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
    if (e instanceof IngenTilgangFeil) {
      visStatus(hoved, { type: "ingen_tilgang" });
      meldLive(t("ui.ingen_tilgang_tittel"));
      return;
    }
    visStatus(hoved, { type: "feil",
      paaProvIgjen: () => medStatus(hoved, ctx, lastFn, tegnFn) });
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
