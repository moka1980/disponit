// Varsler — «noe venter på DEG», og valget om hvordan du vil høre om det.
//
// Flaten finnes fordi fire-øyne-runder sto og ventet uten at godkjenneren
// visste det; eier måtte si fra utenom systemet. Innboksen er sannheten,
// e-posten er en kopi — derfor står varselet her selv om e-posten feilet.
//
// TEKSTEN KOMMER FRA `tekstnokkel` + `parametre`, aldri fra serveren.
// Serveren lagrer nøkkelen; flaten oversetter. Da leses varselet på
// MOTTAKERENS språk, ikke på det avsenderen tilfeldigvis hadde da runden ble
// åpnet — og et språkbytte endrer gamle varsler også.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, merkVarselLest, settVarselkanal, UautorisertFeil } from "../api.js";
import { Tidspunkt, TomTilstand, meldLive } from "../komponenter.js";
import { flateHode, medStatus } from "./felles.js";
import { visningsToken, erGjeldendeVisning } from "../ruter.js";

// Et varsel uten VEI TIL HANDLINGEN er bare støy. Hver art vet hvilken flate
// den hører til, så «attestering venter» kan klikkes rett til utkastet.
const RUTE_FOR_ART = {
  attestering_venter: "policyadmin",
  validering_venter: "policyadmin",
  runde_apnet: "policyadmin",
};

function varseltekst(v) {
  // Parametrene fylles inn i nøkkelens egen tekst. Mangler en parameter, står
  // plassholderen igjen — synlig, ikke stille borte: en tom setning skjuler at
  // noe er galt, en synlig `{gjenstaar}` viser det.
  let s = t(v.tekstnokkel, v.tekstnokkel);
  for (const [k, val] of Object.entries(v.parametre || {})) {
    s = s.split(`{${k}}`).join(String(val));
  }
  return s;
}

function rad(v, paaLest, paaAapne) {
  const li = el("li", { class: `varselrad${v.lest ? "" : " varsel-ulest"}` });
  const tekst = el("p", { class: "varseltekst", text: varseltekst(v) });
  const nar = Tidspunkt({ iso: v.opprettet });
  const knapper = el("div", { class: "varselknapper" });

  const rute = RUTE_FOR_ART[v.art];
  if (rute) {
    const g = el("button", { class: "knapp liten", type: "button",
      text: t("ui.varsler.gaa_til") });
    // Å åpne varselet ER å ha sett det. Å kreve to klikk for én erkjennelse
    // gir en innboks full av «uleste» ting folk faktisk har lest.
    g.addEventListener("click", () => paaAapne(v, rute));
    knapper.append(g);
  }
  if (!v.lest) {
    const m = el("button", { class: "knapp liten", type: "button",
      text: t("ui.varsler.merk_lest") });
    m.addEventListener("click", () => paaLest(v));
    knapper.append(m);
  }
  li.append(tekst, el("p", { class: "sub" }, nar), knapper);
  return li;
}

function kanalvelger(kanal, paaValg) {
  // Valget eier ba om. Radiogruppe, ikke avkryssing: de to alternativene er
  // gjensidig utelukkende, og «kun portal» er et VALG — ikke fravær av et.
  const gruppe = el("fieldset", { class: "varselvalg" },
    el("legend", { text: t("ui.varsler.kanal") }));
  for (const verdi of ["epost_og_portal", "kun_portal"]) {
    const id = `varselkanal-${verdi}`;
    const inn = el("input", { type: "radio", name: "varselkanal", id,
      value: verdi });
    if (verdi === kanal) inn.checked = true;
    inn.addEventListener("change", () => { if (inn.checked) paaValg(verdi); });
    gruppe.append(el("div", { class: "varselvalg-rad" }, inn,
      el("label", { for: id, text: t(`ui.varsler.kanal.${verdi}`) })));
  }
  return gruppe;
}

export function visVarsler(hoved, ctx, opts = {}) {
  // Eierskapet til `hoved` (Codex P2). Alle flater rendrer inn i ETT element,
  // og en innboks-GET som er ute på nettet vet ikke at eier har navigert
  // videre. Kom svaret etterpå, tegnet innboksen seg over den nye ruten mens
  // menyvalget hennes ble stående markert — skjermen viste én flate og
  // navigasjonen en annen.
  //
  // Selve tegningen gis derfor til `medStatus`, som vokter suksess, 403 OG
  // feilveien for alle flatene. Det siste er ikke pynt: en avvist GET nådde
  // aldri tegningen, og den gamle koden erstattet ruten eier sto i med
  // innboksens feiltilstand — komplett med en «Prøv igjen» som laster noe
  // annet enn det skjermen viser. 403 er også en egen tilstand nå; før ble
  // «du har ikke tilgang» vist som en forbigående feil man kunne prøve igjen.
  const minRute = visningsToken(hoved);
  const eierSkjermen = () => erGjeldendeVisning(hoved, minRute);

  const tegn = () => medStatus(hoved, ctx,
    () => hentJson("/v1/varsel"),
    (d) => {
      const varsler = d.varsler || [];
      const liste = varsler.length
        ? el("ul", { class: "varselliste",
            "aria-label": t("ui.varsler.tittel") },
          ...varsler.map((v) => rad(v, merkLest, aapne)))
        : TomTilstand({ tittel: t("ui.varsler.tom"),
            tekst: t("ui.varsler.tom_tekst") });
      sett(hoved,
        ...flateHode(t("ui.varsler.tittel"),
          t("ui.varsler.undertittel").replace("{n}", String(d.uleste || 0))),
        kanalvelger(d.kanal, settKanal),
        liste);
    });

  function merkLest(v) {
    merkVarselLest(v.id)
      .then(() => {
        meldLive(t("ui.varsler.merket_lest"));
        // Oppfriskningen bæres av visningen den ble startet FRA. `medStatus`
        // fanger stempelet i det den kalles, så en `tegn()` startet herfra
        // etter en navigasjon ville fanget det NYE stempelet — og dessuten
        // tegnet lastetilstanden synkront, altså vasket bort den nye ruten før
        // svaret i det hele tatt var ute. Utfallet dør ikke stille: `meldLive`
        // over sier fra uansett hvor eier står.
        if (eierSkjermen()) tegn();
      })
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        meldLive(t("ui.varsler.feilet"));
      });
  }

  function aapne(v, rute) {
    // Merk lest FØRST, men ikke la en feilet merking stoppe navigasjonen:
    // poenget er å komme til handlingen. Et varsel som blir stående ulest er
    // en irritasjon; å ikke komme fram er en blokkering.
    merkVarselLest(v.id).catch(() => {}).then(() => {
      if (opts.gaaTil) opts.gaaTil(rute, v.ressurs_id);
      else window.location.hash = `#/${rute}`;
    });
  }

  function settKanal(kanal) {
    settVarselkanal(kanal)
      .then(() => { meldLive(t("ui.varsler.kanal_lagret")); })
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        meldLive(t("ui.varsler.feilet"));
        // Vis den FAKTISKE tilstanden igjen — men bare hvis eier fortsatt står
        // her. Radioknappen som viser feil valg er borte fra skjermen uansett
        // når hun har navigert videre.
        if (eierSkjermen()) tegn();
      });
  }

  tegn();
}
