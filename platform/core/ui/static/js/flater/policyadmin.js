// Policyadministrasjon (PR-013). Å endre policy er å endre agentens fullmakter,
// så flaten viser ALLTID diffen + risikoklassen FØR noe aktiveres, og
// aktivering krever fire øyne (server håndhever antallet). Klienten sender kun
// operatørens valg + `diff_hash` (det hun SÅ); konvolutten MAC-signeres server-
// side. Muterende kall bærer X-Disponit-CSRF (dobbel-innsending).
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import {
  hentJson, validerUtkast, apneRunde, attesterAktivering, UgyldigFeil,
  nyIdempotensnokkel, ApiFeil, UautorisertFeil, IngenTilgangFeil,
} from "../api.js";
import {
  Tidspunkt, TomTilstand, Feiltilstand, TilgangsVakt, meldLive, Faner,
} from "../komponenter.js";
import { DataTabell } from "../tabell.js";
import { visningsToken, erGjeldendeVisning } from "../ruter.js";
import { Bekreftelsesdialog } from "../dialog.js";
import { medStatus, flateHode, kvRad } from "./felles.js";
import { visPolicyeditor } from "./policyeditor.js";

function risikoBadge(klasse) {
  return el("span", {
    class: `risiko risiko-${klasse}`, "data-risiko": klasse,
    text: t(`risiko.${klasse}`, klasse),
  });
}

// Risikoklasse PER endring (per policy-sti) — den meningsfulle visningen som
// forteller nøyaktig hvilke endringer som UTVIDER fullmakten (fire øyne).
function risikoEndringer(detalj) {
  const endr = detalj.klassifisering_endringer || [];
  if (!endr.length) {
    return el("p", { class: "muted",
      text: t("ui.policyadmin.ingen_risikoendringer") });
  }
  const ul = el("ul", { class: "diff-liste", "aria-label":
    t("ui.policyadmin.klassifisering") });
  for (const e of endr) {
    ul.append(el("li", { class: "diff-rad" },
      el("code", { text: e.sti }), risikoBadge(e.klasse)));
  }
  return ul;
}

// Granulær felt-diff (lagt til / fjernet / endret) — hva som konkret skifter.
function feltDiff(detalj) {
  const endr = (detalj.diff && detalj.diff.endringer) || [];
  if (!endr.length) {
    return el("p", { class: "muted", text: t("ui.policyadmin.ingen_endringer") });
  }
  const ul = el("ul", { class: "feltdiff" });
  for (const e of endr) {
    const verdi = e.type === "endret"
      ? `${JSON.stringify(e.fra)} → ${JSON.stringify(e.til)}`
      : (e.type === "lagt_til" ? JSON.stringify(e.til) : JSON.stringify(e.fra));
    ul.append(el("li", {},
      el("code", { text: e.sti }),
      el("span", { class: "sub",
        text: ` · ${t(`ui.policyadmin.endring.${e.type}`, e.type)}: ${verdi}` })));
  }
  return ul;
}

// Fire-øyne-status: N/påkrevd + hvem som har attestert (og om de er forfatter).
function fireOyneStatus(runde) {
  const attest = runde.attestasjoner || [];
  const dl = el("dl", { class: "kv" });
  kvRad(dl, t("ui.policyadmin.godkjennere"),
    `${attest.length} / ${runde.pakrevd_antall_godkjennere}`);
  kvRad(dl, t("ui.policyadmin.runde_status"),
    t(`ui.policyadmin.rundestatus.${runde.status}`, runde.status));
  const ul = el("ul", { class: "godkjennere",
    "aria-label": t("ui.policyadmin.godkjennere") });
  for (const a of attest) {
    ul.append(el("li", {},
      el("span", { text: a.bruker_id }),
      el("span", { class: "sub",
        text: ` · ${a.rolle}${a.er_forfatter
          ? " · " + t("ui.policyadmin.forfatter") : ""}` })));
  }
  return el("section", { class: "fireoyne",
    "aria-label": t("ui.policyadmin.godkjennere") }, dl,
    attest.length ? ul : el("p", { class: "muted",
      text: t("ui.policyadmin.ingen_attestasjoner") }));
}

// ÉN idempotensnøkkel per aktiveringsforsøk — gjenbrukes ved nettverksretry, så
// serveren ser samme nøkkel og ikke aktiverer to ganger.
function utfoerAttest(uid, diffHash, paaFerdig, ctx) {
  const nokkel = nyIdempotensnokkel();
  const forsok = (attempt) =>
    attesterAktivering(uid, diffHash, nokkel).then((svar) => {
      const utfall = (svar && svar.utfall) || "";
      meldLive(t(`ui.policyadmin.utfall.${utfall}`,
        t("ui.policyadmin.utfall.ukjent")));
      if (paaFerdig) paaFerdig();
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      // Kun nettverksfeil retries — ÉN gang, SAMME nøkkel. En konflikt (stale
      // diff, rebasering) retries ALDRI blindt; utkastet lastes på nytt.
      if (e instanceof ApiFeil && e.status === 0 && attempt === 0) {
        return forsok(1);
      }
      if (e instanceof ApiFeil && e.kode === "rebasering_kreves") {
        meldLive(t("ui.policyadmin.utfall.rebasering_kreves"));
        if (paaFerdig) paaFerdig();
        return;
      }
      if (e instanceof ApiFeil && e.kode === "diff_utdatert") {
        meldLive(t("ui.policyadmin.diff_utdatert"));
        if (paaFerdig) paaFerdig();
        return;
      }
      meldLive(t("ui.policyadmin.feilet"));
      if (paaFerdig) paaFerdig();
    });
  return forsok(0);
}

let _hintTeller = 0;

// Handlingsknappene avhenger av utkastets tilstand: utkast → Valider; validert
// uten runde → Åpne runde; åpen/klar runde → Attester (m/ eksplisitt kvittering).
// Returnerer { rot, diffVist } — `diffVist` melder fra at diffpanelet faktisk
// er tegnet, og er det som låser opp attestering (se attest-grenen).
function handlinger(detalj, uid, ctx, paaFerdig, aapneEditor) {
  const boks = el("section", { class: "pa-handling",
    "aria-label": t("ui.policyadmin.handlinger") });
  const runde = detalj.aktiv_runde;
  let diffVist = () => {};

  if (detalj.status === "utkast") {
    // Rediger går RETT til editoren. Da detaljen var en skuff, måtte skuffen
    // lukkes først; som side er det ingenting å lukke — editoren overtar
    // `hoved` selv. Ble lukkingen med videre, kalte den `tilbakeTilListe`, og
    // det er ikke en DOM-operasjon: det starter en ny liste-GET (Codex P1).
    // Den og editorens detalj-GET tegner i samme `hoved`, så kom listesvaret
    // sist, erstattet det editoren — potensielt etter at eier hadde begynt å
    // skrive, og da med det hun hadde skrevet.
    const rediger = el("button", { class: "knapp", type: "button",
      text: t("ui.policyadmin.handling.rediger") });
    rediger.addEventListener("click", () => {
      if (aapneEditor) aapneEditor({ utkast_id: uid });
    });
    // STABIL nøkkel per render (Codex R2): re-klikk = retry, ikke ny operasjon.
    const valNokkel = nyIdempotensnokkel();
    const b = el("button", { class: "knapp", type: "button",
      text: t("ui.policyadmin.handling.valider") });
    // Et ugyldig utkast er ikke et 200-svar med `utfall: "ugyldig"` — serveren
    // svarer 422 `policy_ugyldig` med feillista i `detaljer`. Koden ventet på
    // den første formen, så `.then` kjørte aldri; 422-en havnet i `.catch`,
    // som bare kalte `meldLive`. Det skriver til aria-live-området: en
    // skjermleser sa fra, men på skjermen så knappen helt død ut. Eier klikket
    // «Valider» og ingenting skjedde — den eneste som fikk vite hvorfor, var
    // den som leste serverloggen.
    // ÉN annonsering per klikk (Codex P2): `role="alert"` ER et assertivt
    // live-område — boksen under leses opp av seg selv når den settes inn. Et
    // `meldLive` i tillegg skrev samme setning til det polite område, så
    // skjermleseren fikk to konkurrerende opplesninger for ett klikk, og den
    // polite kunne komme sist og overdøve selve feillista.
    // Overskriften er en DIAGNOSE, og bare 422 gir grunnlag for å stille den
    // (Codex P2). Derfor tar boksen overskrift og linjer som argumenter i
    // stedet for å anta «ugyldig»: en nettverksfeil, 403, 409 eller 5xx sier
    // ingenting om utkastet, og skal ikke få eier til å lete etter feil i en
    // policy som kan være helt i orden.
    const visFeil = (overskrift, linjer) => {
      boks.querySelectorAll(".pa-valfeil").forEach((n) => n.remove());
      boks.append(el("div", { class: "pa-valfeil", role: "alert" },
        el("p", { text: overskrift }),
        ...(linjer.length
          ? [el("ul", {}, ...linjer.map((f) => el("li", { text: String(f) })))]
          : [])));
    };
    b.addEventListener("click", () =>
      validerUtkast(uid, detalj.utkastversjon, valNokkel).then(() => {
        boks.querySelectorAll(".pa-valfeil").forEach((n) => n.remove());
        meldLive(t("ui.policyadmin.validert"));
        if (paaFerdig) paaFerdig();
      }).catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (e instanceof UgyldigFeil) {
          // Serverens egen feilliste når den finnes; ellers sier vi i det
          // minste SYNLIG at utkastet er ugyldig.
          visFeil(t("ui.policyadmin.ugyldig"),
            e.detaljer || [t("ui.policyadmin.ugyldig_uten_detaljer")]);
          return;                       // bli i skuffen så eier ser feilene
        }
        // Enhver annen feil skal også være SYNLIG — men uten å påstå noe om
        // utkastet: her vet vi bare at handlingen ikke gikk gjennom.
        visFeil(t("ui.policyadmin.feilet"), []);
      }));
    boks.append(rediger, b);
    return { rot: boks, diffVist };
  }

  if (detalj.status === "validert"
      && (!runde || ["brukt", "utlopt", "kansellert"].includes(runde.status))) {
    const rundeNokkel = nyIdempotensnokkel();       // stabil per render (R2)
    const b = el("button", { class: "knapp", type: "button",
      text: t("ui.policyadmin.handling.apne_runde") });
    b.addEventListener("click", () =>
      apneRunde(uid, rundeNokkel).then(() => {
        meldLive(t("ui.policyadmin.runde_apnet"));
        if (paaFerdig) paaFerdig();
      }).catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        meldLive(t("ui.policyadmin.feilet"));
      }));
    boks.append(b);
    return { rot: boks, diffVist };
  }

  if (runde && ["apen", "klar"].includes(runde.status)) {
    const b = el("button", { class: "knapp", type: "button",
      text: t("ui.policyadmin.handling.attester") });
    // Invarianten øverst i fila er at diffen ALLTID vises før noe aktiveres.
    // Da innholdet ble delt i faner, ble den stille brutt (Codex P1):
    // handlingene står — med vilje — fast utenfor fanene, mens diffen flyttet
    // inn i «Endringer». En godkjenner kunne dermed attestere rett fra
    // «Oversikt» uten å ha åpnet diffen én eneste gang, og bekreftelsen viste
    // bare risikoklasse og en ugjennomsiktig hash: ingenting hun kunne lese
    // fullmaktsendringen ut av. Knappen er derfor låst til diffpanelet
    // FAKTISK er tegnet, og bekreftelsen viser selve endringene.
    const hintId = `pa-attest-hint-${++_hintTeller}`;
    const hint = el("p", { class: "sub", id: hintId,
      text: t("ui.policyadmin.attester_krever_diff") });
    b.disabled = true;
    b.setAttribute("aria-describedby", hintId);
    diffVist = () => {
      if (!b.disabled) return;
      b.disabled = false;
      b.removeAttribute("aria-describedby");
      hint.remove();
    };
    b.addEventListener("click", () => {
      // Eksplisitt kvittering: mennesket ser risikoklasse + diff-hash det
      // binder seg til FØR aktivering (attesterer diffen, ikke versjonsnr).
      const kort = String(runde.diff_hash || "").slice(0, 12);
      Bekreftelsesdialog({
        tittel: t("ui.policyadmin.handling.attester"),
        tekst: `${t("ui.policyadmin.du_aktiverer")}: `
          + `${detalj.policy_id} · ${t(`risiko.${runde.risikoklasse}`,
            runde.risikoklasse)} · ${t("ui.policyadmin.diff_hash")} ${kort}`,
        // Hashen identifiserer diffen, men SIER den ikke. Den granulære
        // endringslista står derfor i selve bekreftelsen — det er den man
        // binder seg til.
        detaljer: el("div", { class: "bekreft-diff" },
          el("h3", { text: t("ui.policyadmin.klassifisering") }),
          risikoEndringer(detalj),
          el("h3", { text: t("ui.policyadmin.diff") }),
          feltDiff(detalj)),
        primarTekst: t("ui.policyadmin.handling.attester"),
        farlig: runde.risikoklasse === "UTVIDER",
        paaPrimar: () => utfoerAttest(uid, runde.diff_hash, paaFerdig, ctx),
      });
    });
    boks.append(b, hint);
    return { rot: boks, diffVist };
  }

  return { rot: boks, diffVist };
}

function detaljInnhold(detalj, uid, ctx, paaFerdig, aapneEditor) {
  const dl = el("dl", { class: "kv" });
  kvRad(dl, t("ui.policyadmin.kol.policy"), detalj.policy_id);
  kvRad(dl, t("ui.policyadmin.kol.status"),
    t(`ui.policyadmin.status.${detalj.status}`, detalj.status));
  kvRad(dl, t("ui.policyadmin.base_versjon"),
    detalj.base_versjon || t("ui.policyadmin.ingen_aktiv"));
  kvRad(dl, t("ui.policyadmin.risikoklasse"),
    risikoBadge(detalj.risikoklasse));

  // Skuffen var samme lange rulle som editoren: nøkkeltall, klassifisering,
  // diff og fire-øyne-status under hverandre, og handlingene nederst — etter
  // alt man måtte skrolle forbi. Nå er innholdet trinn, mens HANDLINGENE står
  // fast utenfor fanene: det man skal GJØRE med utkastet skal ikke ligge og
  // gjemme seg bak et fanevalg.
  const trinn = [
    { nokkel: "oversikt", tittel: t("ui.policyadmin.fane.oversikt"),
      bygg: () => el("div", {}, dl) },
    { nokkel: "endringer", tittel: t("ui.policyadmin.fane.endringer"),
      bygg: () => el("div", {},
        el("h3", { text: t("ui.policyadmin.klassifisering") }),
        risikoEndringer(detalj),
        el("h3", { text: t("ui.policyadmin.diff") }),
        feltDiff(detalj)) },
  ];
  if (detalj.aktiv_runde) {
    trinn.push({ nokkel: "fire_oyne", tittel: t("ui.policyadmin.fane.fire_oyne"),
      bygg: () => el("div", {},
        el("h3", { text: t("ui.policyadmin.fire_oyne") }),
        fireOyneStatus(detalj.aktiv_runde)) });
  }
  // Handlingene bygges FØR fanene: attestering er låst til diffen er sett, og
  // `Faner` melder fra om det allerede under første tegning.
  const handl = handlinger(detalj, uid, ctx, paaFerdig, aapneEditor);
  // Venter utkastet på attestering, er diffen — ikke nøkkeltallene — det
  // godkjenneren er her for. Da åpner skuffen på «Endringer».
  const venterAttest = detalj.aktiv_runde
    && ["apen", "klar"].includes(detalj.aktiv_runde.status);
  const faner = Faner({ trinn, start: venterAttest ? "endringer" : "oversikt",
    paaBytte: (nokkel) => { if (nokkel === "endringer") handl.diffVist(); } });
  return el("div", {}, faner.rot, handl.rot);
}

function tilbakeKnapp(tilbakeTilListe) {
  const b = el("button", { class: "knapp", type: "button",
    text: t("ui.policyadmin.tilbake_til_liste") });
  b.addEventListener("click", tilbakeTilListe);
  return b;
}

// Fokus til sidens overskrift: siden ble byttet ut, og uten dette står fokus
// igjen på raden man klikket i en liste som ikke er der lenger.
function fokuserOverskrift(hoved) {
  const h = hoved.querySelector("h1, h2");
  if (h) { h.setAttribute("tabindex", "-1"); h.focus(); }
}

export function visPolicyadmin(hoved, ctx) {
  const st = { rader: [] };

  // Flatens eierskap til `hoved`, fanget ved oppstart. Alle flater deler ETT
  // element, og et svar som er ute på nettet vet ikke at brukeren har navigert
  // videre: kom detaljsvaret etter at hun hadde valgt en annen toppnivårute,
  // tegnet det seg selv over DEN flaten, mens menyvalget hennes ble stående
  // markert (Codex P2). `fetch` her kan ikke avbrytes bakover gjennom
  // `hentJson`, så svaret slippes i stedet: er stempelet flyttet, er dette
  // svaret foreldet og skal ikke røre skjermen.
  const minVisning = visningsToken(hoved);
  const eierSkjermen = () => erGjeldendeVisning(hoved, minVisning);

  // Editoren tar over `hoved`. Ved lagring åpnes utkastets detalj; Avbryt/
  // fullført går tilbake til lista.
  function aapneEditor(opts) {
    visPolicyeditor(hoved, ctx, {
      ...opts,
      aapneUtkast: aapneDetalj,
      tilbake: tilbakeTilListe,
    });
  }

  // Utkastet åpnes som en VANLIG SIDE i flaten, ikke som en skuff over den.
  //
  // Skuffen var feil form for det som skjer her: å attestere er ikke en rask
  // sidehandling, det er hovedoppgaven. Den la et smalt panel over lista, og —
  // verre — den sa ikke tydelig HVILKET utkast man sto i. Med to åpne runder
  // endte de to attestasjonene på hvert sitt utkast, og ingen av dem kunne
  // aktivere. Siden har utkastets policy-ID i overskriften og en synlig vei
  // tilbake, slik editoren allerede gjør.
  function aapneDetalj(uid) {
    hentJson(`/v1/policyutkast/${uid}`).then((detalj) => {
      if (!eierSkjermen()) return;
      sett(hoved,
        ...flateHode(
          `${t("ui.policyadmin.detalj_tittel")}: ${detalj.policy_id}`,
          t("ui.policyadmin.detalj_undertittel").replace("{id}", uid)),
        tilbakeKnapp(tilbakeTilListe),
        detaljInnhold(detalj, uid, ctx, () => aapneDetalj(uid), aapneEditor));
      fokuserOverskrift(hoved);
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (!eierSkjermen()) return;
      // En feilet detalj-GET skal ikke stenge eier inne (Codex P2). Skuffen lot
      // i det minste lista ligge under seg; siden erstattet HELE flaten med en
      // naken feiltilstand — uten «Prøv igjen» og uten vei tilbake. Et
      // forbigående 5xx eller et nettverksglipp ble dermed en blindvei man bare
      // kom ut av ved å laste appen på nytt. Feiltilstanden er derfor en side
      // som de andre: den beholder overskrift og tilbakeknapp.
      //
      // «Prøv igjen» tilbys bare der den kan hjelpe: 403 er ingen forbigående
      // feil, og en knapp som lover et annet svar neste gang lyver.
      sett(hoved,
        ...flateHode(t("ui.policyadmin.detalj_tittel"),
          t("ui.policyadmin.detalj_undertittel").replace("{id}", uid)),
        tilbakeKnapp(tilbakeTilListe),
        e instanceof IngenTilgangFeil
          ? TilgangsVakt({})
          : Feiltilstand({ paaProvIgjen: () => aapneDetalj(uid) }));
      fokuserOverskrift(hoved);
    });
  }

  function rad(u) {
    return {
      id: u.utkast_id,
      celler: {
        policy: u.policy_id,
        status: t(`ui.policyadmin.status.${u.status}`, u.status),
        versjon: String(u.utkastversjon),
        opprettet: Tidspunkt(u.opprettet),
      },
      sortverdi: { opprettet: u.opprettet, policy: u.policy_id },
      handling: { tekst: t("ui.aapne"),
        paaKlikk: () => aapneDetalj(u.utkast_id) },
    };
  }

  function verktoylinje() {
    const bar = el("div", { class: "filterbar" });
    const nytt = el("button", { class: "knapp primar", type: "button",
      text: t("ui.policyadmin.nytt_utkast") });
    nytt.addEventListener("click", () => aapneEditor({}));
    bar.append(nytt);
    return bar;
  }

  function tegn(flyttFokus) {
    const innhold = st.rader.length
      ? DataTabell({
          captionTekst: t("ui.policyadmin.tittel"),
          kolonner: [
            { nokkel: "policy", tittel: t("ui.policyadmin.kol.policy"),
              sorterbar: true },
            { nokkel: "status", tittel: t("ui.policyadmin.kol.status") },
            { nokkel: "versjon", tittel: t("ui.policyadmin.kol.versjon") },
            { nokkel: "opprettet", tittel: t("ui.policyadmin.kol.opprettet"),
              sorterbar: true },
          ],
          rader: st.rader.map(rad),
        })
      : TomTilstand({ tittel: t("ui.policyadmin.tom_tittel"),
                      tekst: t("ui.policyadmin.tom_tekst") });
    sett(hoved,
      ...flateHode(t("ui.policyadmin.tittel"), t("ui.policyadmin.undertittel")),
      verktoylinje(),
      innhold);
    if (flyttFokus) fokuserOverskrift(hoved);
  }

  // `fokus` settes når lista er et RETURMÅL. Veien FRAM flytter fokus til
  // detaljsidens overskrift; veien TILBAKE gjorde det ikke, og der forsvant
  // fokus (Codex P2): `last()` river DOM-en synkront for lastetilstanden, så
  // knappen tastaturbrukeren nettopp trykte på er borte, og fokus faller til
  // `body`. Da starter tastaturnavigasjonen forfra, utenfor utkastlista.
  //
  // Ved første tegning skal den IKKE flytte fokus: der er det ruteren som eier
  // fokus (`hoved.focus()`), og flaten skal ikke rykke det fra den.
  function last(opts) {
    const flyttFokus = !!(opts && opts.fokus);
    medStatus(hoved, ctx, () => hentJson("/v1/policyutkast"), (d) => {
      if (!eierSkjermen()) return;         // samme foreldelse som i detaljen
      st.rader = (d && d.utkast) || []; tegn(flyttFokus);
    });
  }

  function tilbakeTilListe() { last({ fokus: true }); }

  last();
}
