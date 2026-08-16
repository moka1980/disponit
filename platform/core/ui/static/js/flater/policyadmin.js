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
  Tidspunkt, TomTilstand, Feiltilstand, TilgangsVakt, Faner, meldLive,
} from "../komponenter.js";
import { DataTabell } from "../tabell.js";
import { visningsToken, erGjeldendeVisning } from "../ruter.js";
import { Bekreftelsesdialog } from "../dialog.js";
import { medStatus, flateHode, kvRad, fokuserOverskrift } from "./felles.js";
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
// Diffen er det godkjenneren binder seg til, så den må være til å LESE.
//
// Den flate lista var teknisk korrekt og praktisk ubrukelig: en ny policy ga
// ~200 rader av typen `handlinger[0].vilkaar[2].verifikator · added:
// "v_prognose"`. Det spørsmålet et menneske skal svare på — hva får agenten
// lov til å gjøre, og opp til hvilket beløp — lå begravd mellom
// beskrivelsestekster og varslingsdager. En godkjenner som ikke orker å lese
// er en godkjenner som klikker, og da er fire øyne bare seremoni.
//
// Ingenting SKJULES: hver eneste endring er fortsatt til stede og utfoldbar.
// Det som endres er rekkefølgen og oppdelingen — grupper man kan lukke, ett
// kort per element i stedet for én rad per blad, og en presis oppsummering på
// hver overskrift så gruppen kan vurderes uten å åpnes.

// Klassifikatoren merker objekt-map-er med «{}» («verifikatorer{}»,
// «verifikator_prioritet{}»), mens bladdiffen navngir de samme feltene uten
// markør («verifikatorer.v1.betrodd_for[0]»). Markøren sier hva slags
// beholder feltet er, ikke hvilket felt det er, så den må bort før de to
// visningene sammenlignes — ellers er «verifikatorer{}» og «verifikatorer»
// to forskjellige grupper, og en verifikator som UTVIDER fullmakten blir
// verken sortert først, åpnet eller merket (Codex P1). Nettopp den gruppen
// er grunnen til at fire øyne kreves.
function normaliserSti(sti) {
  return String(sti).replace(/\{\}/g, "");
}

// «handlinger[0].vilkaar[2].navn» → { gruppe: "handlinger",
//                                     element: "handlinger[0]", rest: … }
// Skalarledd i en liste («unntak.kategorier[3]») har ingen rest — de slås
// sammen til én rad for hele lista lenger nede.
//
// Elementet er den ENHETEN godkjenneren tar stilling til, og det er hvor
// listen ligger som avgjør hvor den enheten er — ikke hvor dypt i stien vi
// har kommet. Derfor: første indekserte ledd som har felt UNDER seg er
// elementet. `handlinger[0].vilkaar[2].navn` → `handlinger[0]` (én handling
// per kort, ikke ett kort per vilkår), og
// `menneskelig_overstyring.godkjennbare[0].handling` →
// `…godkjennbare[0]` — ett kort per overstyring. Uten indeksen havnet alle
// overstyringene i ETT kort med bare samlestien som overskrift, og det er
// nettopp handling og beløp per overstyring godkjenneren skal skille
// mellom (Codex P2).
//
// Har ingen indeks felt under seg, er det ingen liste av objekter, og
// elementet er gruppen + ett navngitt ledd (`verifikatorer.v1`,
// `unntak.kategorier`). Da MÅ indeksen bli stående i resten: en indeks til
// slutt betyr skalarliste, og `dataklasser[0]`, `dataklasser[1]` … skal
// samles til én rad `dataklasser[]` i stedet for én rad per indeks (Codex
// P2). Toppnivålisten har ikke noe navngitt ledd, så elementet er gruppen.
function delOppSti(raa) {
  const sti = normaliserSti(raa);
  const m = /^([^.[]+)/.exec(sti);
  const gruppe = m ? m[1] : sti;
  const iListe = /^(.*?\[\d+\])(?=\.)/.exec(sti);
  const navngitt = /^([^.[]+\.[^.[]+)/.exec(sti);
  const element = iListe ? iListe[1] : (navngitt ? navngitt[1] : gruppe);
  const rest = sti.slice(element.length).replace(/^\./, "");
  return { gruppe, element, rest };
}

function verdiTekst(e) {
  if (e.type === "endret") {
    return `${JSON.stringify(e.fra)} → ${JSON.stringify(e.til)}`;
  }
  return JSON.stringify(e.type === "lagt_til" ? e.til : e.fra);
}

function bladRad(e) {
  return el("li", {},
    el("code", { text: e.sti }),
    el("span", { class: "sub",
      text: ` · ${t(`ui.policyadmin.endring.${e.type}`, e.type)}: `
        + verdiTekst(e) }));
}

// Overskriften på et element skal si HVA elementet er, ikke hvor det står i
// JSON-en. Identiteten er navnet mennesket kjenner elementet ved; nøkkelfeltene
// er de som avgjør fullmakten, og hører derfor hjemme i overskriften og ikke
// tolv rader ned.
//
// `id` er identiteten for `handlinger[]` og `roller[]`, men
// `menneskelig_overstyring.godkjennbare[]` HAR ingen `id` (skjemaet krever
// `grunnkode` + `handling`). Overskriften falt derfor tilbake til
// «…godkjennbare[2]», og med flere overstyringer måtte godkjenneren åpne
// hvert eneste kort for å finne ut hvilken handling og hvilken beløpsgrense
// det gjaldt — akkurat den forskjellen kortene ble delt opp for å vise
// (Codex P2). Rekkefølgen er prioritert: første felt som finnes, vinner.
const IDENTITET = ["id", "handling"];

// Fullmaktsbærende felt, i den rekkefølgen de vises.
const NOKKELFELT = ["modul", "modus", "grunnkode", "tillatt_for[0]"];

// Beløpsgrense + valutaen den er i — de hører sammen i én merkelapp, og de to
// listene plasserer dem forskjellig: `handlinger[].grenser.belop_maks` mot
// `godkjennbare[].belop_maks`.
const BELOPSFELT = [
  ["grenser.belop_maks", "grenser.valuta[0]"],
  ["belop_maks", "valuta"],
];

// «handlinger[1]» / «menneskelig_overstyring.godkjennbare[0]» → verdien på den
// stien i utkastet, eller `undefined` finnes den ikke.
function slaaOppSti(rot, sti) {
  let v = rot;
  for (const ledd of String(sti).match(/[^.[\]]+/g) || []) {
    if (v === null || typeof v !== "object") return undefined;
    v = Array.isArray(v) ? v[Number(ledd)] : v[ledd];
    if (v === undefined) return undefined;
  }
  return v;
}

// Flater et element ut til de samme rest-stiene bladdiffen bruker
// («id», «grenser.belop_maks», «grenser.valuta[0]») — samme form som
// serverens `_flat`, så oppslagene under treffer uansett kilde.
function flateFelt(verdi, prefiks = "", ut = new Map()) {
  if (Array.isArray(verdi)) {
    verdi.forEach((v, i) => flateFelt(v, `${prefiks}[${i}]`, ut));
  } else if (verdi !== null && typeof verdi === "object") {
    for (const [k, v] of Object.entries(verdi)) {
      flateFelt(v, prefiks ? `${prefiks}.${k}` : k, ut);
    }
  } else if (prefiks) {
    ut.set(prefiks, verdi);
  }
  return ut;
}

// Overskriften bygges fra HELE utkastet, ikke bare fra bladene som endret
// seg. Endres en handling som allerede finnes — si `handlinger[1].modus` —
// inneholder diffen verken `id` eller `modul`, og overskriften falt tilbake
// til «handlinger[1]». Nettopp den vanligste endringen fortalte altså ikke
// godkjenneren HVILKEN handling hun tar stilling til (Codex P2).
//
// Utkastets `innhold` er fasit for hva elementet ER etter endringen, så det
// er kilden når elementet finnes der. Et SLETTET element finnes ikke i
// utkastet — der er `fra`-verdiene i diffen det eneste som forteller hva som
// forsvinner, og de beholdes.
//
// Men å hente overskriften fra utkastet ALENE er å påstå at elementet alltid
// har vært det det er nå. Serverens diff sammenligner lister POSISJONELT, så
// fjernes `A` fra `[A, B]` blir `handlinger[0]` til bladet «id: A → B» —
// og et kort som hentet navnet sitt fra utkastets `handlinger[0]` het bare
// «B». Det slettede `handlinger[1]`-kortet rekonstruerte samtidig gamle `B`
// fra `fra`-verdiene. To kort som begge sa «B», og `A` — det som faktisk
// forsvant — sto ingen steder (Codex P2). Derfor: endret feltet seg, viser
// overskriften BEGGE sider.
function elementOverskrift(element, blader, innhold) {
  // FØR-tilstanden slik diffen beskriver den. `lagt_til` har ingen før.
  const foer = new Map();
  for (const e of blader) {
    if (e.type === "lagt_til") continue;
    const { rest } = delOppSti(e.sti);
    if (rest) foer.set(rest, e.fra);
  }
  const felt = new Map();
  const kilde = slaaOppSti(innhold, element);
  if (kilde !== undefined) {
    for (const [k, v] of flateFelt(kilde)) felt.set(k, v);
  } else {
    for (const e of blader) {
      const { rest } = delOppSti(e.sti);
      const v = e.type === "fjernet" ? e.fra : e.til;
      if (rest) felt.set(rest, v);
    }
  }
  // Et slettet element rekonstrueres fra sine egne `fra`-verdier, så før og
  // etter er like der — pilen dukker bare opp når feltet faktisk skiftet.
  const vis = (f) => {
    const ny = felt.get(f), gml = foer.get(f);
    if (ny === undefined || ny === null) return undefined;
    return (gml !== undefined && gml !== null && gml !== ny)
      ? `${gml} → ${ny}` : String(ny);
  };
  const navn = IDENTITET.map(vis).find((v) => v !== undefined) ?? String(element);
  const merker = [];
  for (const f of NOKKELFELT) {
    const v = vis(f);
    if (v !== undefined) merker.push(v);
  }
  for (const [belop, valuta] of BELOPSFELT) {
    const b = vis(belop);
    if (b === undefined) continue;
    const v = vis(valuta);
    merker.push(`${t("ui.policyadmin.diff.maks")} ${b}${v ? " " + v : ""}`);
  }
  return { navn, merker, felt };
}

// En liste av rene verdier («unntak.kategorier[]», «dataklasser[]») blir én
// rad med alle verdiene, ikke én rad per indeks. Åtte kategorier er én
// beslutning, ikke åtte.
//
// Verdiene serialiseres som JSON, akkurat som i enkeltradene: sammenslåingen
// bytter oppdeling, ikke innhold. `String()` visket ut både type og grenser —
// lista `[true, "true"]` ble «true, true», og en verdi som selv inneholder
// komma var ikke til å skille fra to oppføringer. Godkjenneren attesterer
// `diff_hash` over de EKSAKTE verdiene, så en visning hun ikke kan lese
// verdiene tilbake fra, er ikke en lesbar diff (Codex P2).
function skalarListeRad(element, blader) {
  const verdier = blader.map((e) =>
    JSON.stringify(e.type === "fjernet" ? e.fra : e.til));
  const type = blader[0].type;
  return el("li", {},
    el("code", { text: `${element.replace(/\[\d+\]$/, "")}[]` }),
    el("span", { class: "sub",
      text: ` · ${t(`ui.policyadmin.endring.${type}`, type)} (${verdier.length}): `
        + verdier.join(", ") }));
}

// Et blad er MEDLEM AV EN LISTE bare når resten er en indeks
// («dataklasser[0]», «unntak.kategorier[3]»). Åtte unntakskategorier er ÉN
// beslutning, og slås sammen til én `[]`-rad.
//
// Et blad uten rest er derimot et helt vanlig skalarfelt («tidssone»,
// «unntak.maks_auto_forsok»). Det ble tidligere regnet som skalarblad det
// også, så ett enkelt lagt-til felt gikk gjennom `skalarListeRad` og kom ut
// som «tidssone[]» — diffen påsto at feltet var en liste. Godkjenneren
// attesterer strukturen hun ser, så en sti som ikke finnes i policyen er
// ikke en kosmetisk feil (Codex P2).
function erListeblad(e) {
  return /^\[\d+\]$/.test(delOppSti(e.sti).rest);
}

function erBladetSelv(e) {
  return !delOppSti(e.sti).rest;
}

function feltdiffRad(blader) {
  return el("li", { class: "diff-elementrad" },
    el("ul", { class: "feltdiff" }, ...blader.map(bladRad)));
}

function elementBlokk(element, blader, innhold) {
  // Sammenslåing er BARE trygt for like tillegg/fjerninger. En `endret` verdi
  // har to sider, og den sammenslåtte raden viste bare den nye — `tidssone`
  // ble «Europe/Oslo» uten at «UTC» sto noe sted. Å miste utgangspunktet i en
  // fullmaktsdiff er nøyaktig det grupperingen ikke har lov til å gjøre, så
  // blandede eller endrede blader beholder én rad hver.
  const ensartet = blader.every((e) => e.type === blader[0].type);
  if (blader.every(erListeblad)) {
    return (ensartet && blader[0].type !== "endret")
      ? skalarListeRad(element, blader)
      : feltdiffRad(blader);
  }
  // Elementet ER bladet: ingen felt under seg, ingen liste å slå sammen.
  // Det skal stå med sin egen sti, ikke pakkes i et kort og ikke få «[]».
  if (blader.every(erBladetSelv)) return feltdiffRad(blader);

  const { navn, merker } = elementOverskrift(element, blader, innhold);
  const detaljer = el("details", { class: "diff-element" });
  const opps = el("summary", {},
    el("span", { class: "diff-navn", text: navn }));
  for (const m of merker) {
    opps.append(document.createTextNode(" · "),
      el("span", { class: "diff-merke", text: m }));
  }
  opps.append(document.createTextNode(" · "),
    el("span", { class: "sub",
      text: t("ui.policyadmin.diff.antall_felt")
        .replace("{n}", String(blader.length)) }));
  detaljer.append(opps,
    el("ul", { class: "feltdiff" }, ...blader.map(bladRad)));
  return el("li", { class: "diff-elementrad" }, detaljer);
}

function feltDiff(detalj) {
  const endr = (detalj.diff && detalj.diff.endringer) || [];
  if (!endr.length) {
    return el("p", { class: "muted", text: t("ui.policyadmin.ingen_endringer") });
  }

  // Gruppene som UTVIDER fullmakt står først og står ÅPNE. Det er dem
  // fire-øyne-kravet finnes for; resten er kontekst man kan folde ut.
  const utvider = new Set((detalj.klassifisering_endringer || [])
    .filter((k) => k.klasse === "UTVIDER")
    .map((k) => delOppSti(k.sti).gruppe));

  const grupper = new Map();
  for (const e of endr) {
    const { gruppe, element } = delOppSti(e.sti);
    if (!grupper.has(gruppe)) grupper.set(gruppe, new Map());
    const g = grupper.get(gruppe);
    if (!g.has(element)) g.set(element, []);
    g.get(element).push(e);
  }

  const sortert = [...grupper.keys()].sort((a, b) => {
    const ua = utvider.has(a), ub = utvider.has(b);
    if (ua !== ub) return ua ? -1 : 1;
    return a.localeCompare(b, "nb");
  });

  const rot = el("div", { class: "diff-grupper" });
  rot.append(el("p", { class: "diff-sammendrag",
    text: t("ui.policyadmin.diff.sammendrag")
      .replace("{antall}", String(endr.length))
      .replace("{omrader}", String(sortert.length)) }));

  for (const navn of sortert) {
    const elementer = grupper.get(navn);
    const antall = [...elementer.values()].reduce((n, b) => n + b.length, 0);
    const blokk = el("details", { class: "diff-gruppe" });
    if (utvider.has(navn)) blokk.setAttribute("open", "");
    const opps = el("summary", {},
      el("span", { class: "diff-gruppenavn",
        text: t(`ui.policyadmin.diff.gruppe.${navn}`, navn) }));
    if (utvider.has(navn)) {
      // Samme merking som risikolista over, så de to visningene ikke kan
      // fortelle to forskjellige historier om hva som utvider fullmakten.
      opps.append(document.createTextNode(" · "), risikoBadge("UTVIDER"));
    }
    opps.append(document.createTextNode(" · "),
      el("span", { class: "sub",
        text: t("ui.policyadmin.diff.antall_endringer")
          .replace("{n}", String(antall)) }));
    blokk.append(opps, el("ul", { class: "diff-elementer" },
      ...[...elementer.keys()].sort((a, b) => a.localeCompare(b, "nb"))
        .map((k) => elementBlokk(k, elementer.get(k), detalj.innhold))));
    rot.append(blokk);
  }
  return rot;
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

// Utfallet av en handling skal SES, ikke bare annonseres — og det må overleve
// gjentegningen.
//
// To feil lå oppå hverandre her. Utfallet gikk bare til `meldLive`, altså
// aria-live-området: en skjermleser fikk beskjed, skjermen var uendret. Og selv
// om det HADDE blitt tegnet, ville det forsvunnet umiddelbart, for hver
// vellykket handling kaller `paaFerdig()` → `aapneDetalj()` → `sett(hoved, …)`,
// som bytter ut hele siden. Eier attesterte, så ingenting, og konkluderte med
// at klikket ikke virket — mens serveren hadde svart «venter_godkjennere: 1
// avgitt, 1 gjenstår».
//
// Utfallet legges derfor til side og tegnes inn i den NYE siden, rett under
// overskriften der fokus havner. Det er en `role="status"`, som er et politt
// live-område: innsettingen annonseres av seg selv, så det skal IKKE følges av
// et `meldLive` — da ville ett klikk gitt to konkurrerende opplesninger (samme
// funn Codex hadde på valideringsboksen).
//
// Kvitteringen BÆRER HVILKET UTKAST den gjelder, og lever bare fram til den
// tegningen handlingen selv startet (Codex P2 / eier P1). Uten det var den en
// naken modulglobal: attesterte man A og gikk tilbake før detalj-GET-en kom,
// avviste `eierSkjermen(min)` tegningen — men kvitteringen ble liggende. Neste
// utkast som ble tegnet, HVILKET SOM HELST, forbrukte den og viste
// «attestert / venter på godkjennere» på feil policy. I en styringsflate er
// det ikke en kosmetisk feil: skjermen bekrefter da en fullmaktsendring på et
// annet objekt enn det handlingen traff.
let _ventendeKvittering = null;

function _settKvittering(uid, art, tekst) {
  _ventendeKvittering = { uid, art, tekst };
}

// Forbrukes ÉN gang, og bare av utkastet den gjelder: kvitteringen hører til
// handlingen som nettopp skjedde, og skal verken dukke opp igjen neste gang
// siden tegnes av andre grunner, eller på et annet utkast.
//
// Den tas UT av modulen med én gang tegningen starter — ikke når det tegnes.
// Da eier den enkelte tegningen kvitteringen alene: blir tegningen foreldet,
// dør kvitteringen med den i stedet for å bli liggende og vente på en tilfeldig
// neste tegning. En kvittering som er satt for et annet utkast er per
// definisjon foreldet her, og forkastes.
//
// Å eie den innebærer også en PLIKT: en tegning som fortsatt eier skjermen må
// tegne kvitteringen sin, også når selve oppfriskningen feiler. Ellers bytter
// vi én feil (kvittering på feil utkast) mot en annen (ingen kvittering for en
// handling som faktisk ble utført).
//
// Og hver `_settKvittering` må ha en `taKvittering` som svarer på seg — også i
// den grenen der det ALDRI startes en tegning (Codex P2). Blir oppfriskningen
// droppet fordi eier har gått videre, tas kvitteringen ut her og leses opp i
// live-området i stedet; se `paaFerdig` i `aapneDetalj`. Uten det blir den
// liggende i modulen: usett nå, og feilaktig fersk neste gang samme utkast
// tegnes.
function taKvittering(uid) {
  const k = _ventendeKvittering;
  _ventendeKvittering = null;
  return k && k.uid === uid ? k : null;
}

// Plikten over gjelder HELE veien, ikke bare fram til tegningen er startet
// (Codex P2). Å ta kvitteringen ut med én gang lukket det ene tapsvinduet —
// handlingen som fullførte etter at eier hadde navigert bort — men åpnet et
// annet rett etter: kvitteringen er nå denne tegningens eiendom, og faller
// tegningen bort MENS detalj-GET-en er ute, faller kvitteringen med den. Eier
// rakk å trykke «Tilbake» i sekundet mellom POST og GET, og fikk dermed ingen
// bekreftelse i det hele tatt på en fullmaktshandling som faktisk ble utført.
// Ikke feil kvittering, ikke gammel kvittering — ingen.
//
// Det er samme svar som i `paaFerdig`: skjermen kan ikke vise utfallet lenger,
// men skjermleseren kan fortsatt si det. Den varige live-regionen hører til
// dokumentet, ikke til tegningen, og overlever navigasjonen som drepte boksen.
// Ingen kvitteringsboks når skjermen her, så det finnes heller ingenting å
// konkurrere med om opplesningen.
function meldTaptKvittering(k) {
  if (k) meldLive(k.tekst);
}

// Live-området settes inn TOMT og fylles først når det står i dokumentet
// (Codex P2). Et `role="status"` annonserer ikke pålitelig tekst som lå der
// allerede da området kom inn i tilgjengelighetstreet — den oppførselen er det
// bare `role="alert"` som vanligvis får. Bygget vi hele boksen ferdig utfylt og
// satte den inn som del av ett stort undertre, kunne altså nettopp de POSITIVE
// utfallene («validert», «venter på godkjennere») bli tause for skjermleseren —
// samme klasse feil som den vi kom fra, bare et hakk dypere: synlig på skjermen,
// stille i lyd. Derfor to trinn: regionen først, teksten som en egen endring
// ETTERPÅ, som live-området er laget for å fange opp.
//
// «Etterpå» må dessuten være en SENERE OPPGAVE, ikke bare en senere setning
// (Codex P2). To DOM-endringer i samme oppgave er to endringer for DOM-en, men
// ikke nødvendigvis to endringer for tilgjengelighetstreet: nettleseren
// oppdaterer det treet mellom oppgaver, og rekker den ikke å se regionen tom,
// er «tom → utfylt» aldri en ENDRING i et registrert live-område — bare en
// ferdig utfylt region som dukker opp, altså nøyaktig det tause tilfellet vi
// prøver å unngå. En MutationObserver kan bekrefte at rekkefølgen på
// DOM-endringene er riktig, men ikke at treet ble oppdatert imellom.
//
// `setTimeout(…, 0)` gir den oppdateringen et sted å skje. Rekkefølgen for
// øyet er uendret: teksten kommer i neste oppgave, ikke i neste sekund.
//
// Returnerer { rot, fyll } — den som setter inn `rot`, kaller `fyll()` etterpå.
function kvitteringsBoks(k) {
  const linje = el("p", {});
  const rot = el("div", {
    class: `pa-kvittering pa-kvittering-${k.art}`,
    role: k.art === "feil" ? "alert" : "status",
  }, linje);
  // Er tegningen erstattet før oppgaven kjører, er `linje` frakoblet, og
  // skrivingen er en uskadelig no-op — kvitteringen døde med sin tegning.
  return { rot, fyll: () => setTimeout(() => sett(linje, k.tekst), 0) };
}

// En attestering har TRE utfallsfamilier, ikke to (Codex P2).
//
// `rebasering_kreves` og `semantikk_endret` er TERMINALE: serveren har
// KANSELLERT aktiveringsrunden (se `policyadmin.py` — runden settes til
// 'kansellert' rett før svaret) og krever en ny handling av eier. Men de kommer
// som 200, ikke som feil, så de traff `.then` — og der ble alt som ikke var
// `aktivert` klassifisert som «vent». En kansellert runde fikk dermed samme
// rolige ventestil som en runde som går sin gang, mens SAMME `rebasering_kreves`
// ble vist som feil når den kom som `ApiFeil`. Samme utfall, to farger, og den
// mest villedende av dem sa «len deg tilbake» til noen som må åpne ny runde.
//
// Kartet er derfor eksplisitt, og alt ukjent faller til `feil`: et utfall denne
// flaten ikke kjenner, er ikke en bekreftet aktivering, og skal ikke se ut som
// en. `vent` er reservert for det ENE utfallet der ventingen er svaret.
//
// `Map`, ikke objektliteral (Codex P2): et oppslag i et vanlig objekt treffer
// også ARVEDE navn. Svarte serveren `utfall: "constructor"`, `"toString"` eller
// `"__proto__"`, ga `UTFALLSART[utfall]` en sann verdi — og dermed hoppet det
// ukjente utfallet over `|| "feil"`-fallbacken. Kvitteringen fikk en klasse som
// ikke finnes, og fordi arten da ikke var strengen `"feil"`, ble den tegnet som
// en politt `role="status"` i stedet for en `role="alert"`. Nøyaktig den
// stillheten fail-closed skal hindre, utløst av et navn ingen hadde valgt.
// Et `Map` har ingen prototypenavn å arve, så oppslaget svarer bare på det
// kartet faktisk inneholder.
const UTFALLSART = new Map([
  ["aktivert", "ok"],
  ["venter_godkjennere", "vent"],
  ["rebasering_kreves", "feil"],
  ["semantikk_endret", "feil"],
]);

// Terskelen har TO betingelser, og serverens `gjenstaar` teller bare den ene
// (Codex P2). Feltet er `max(0, påkrevd - antall)` — et rent talloppgjør — mens
// aktiveringen i tillegg krever minst ÉN godkjenner som ikke skrev utkastet.
// For en runde der påkrevd er 1 (INNSNEVRER/NØYTRAL) og forfatteren attesterer
// først, svarer serveren derfor `antall: 1, gjenstaar: 0,
// mangler_uavhengig: true`. Sa vi da «0 gjenstår» og fortsatte med at det
// mangler en uavhengig godkjenner, motsa kvitteringen seg selv i samme
// åndedrag — og tallet, som er det eier leser først, sa at hun var ferdig når
// hun ikke var det.
//
// Tallet som vises er derfor det som FAKTISK gjenstår: er
// uavhengighetskravet uinnfridd, må det komme minst én attestasjon til,
// uansett hva talloppgjøret sier. Serverfeltet står urørt — det er
// talloppgjøret, og skal fortsette å være det.
//
// Mangler `gjenstaar` i svaret, gjettes det ikke: «?» er sant, og setningen om
// uavhengighet står uansett og forteller hva som mangler.
function gjenstaarIgjen(svar) {
  if (svar.gjenstaar == null) return "?";
  return Math.max(svar.gjenstaar, svar.mangler_uavhengig ? 1 : 0);
}

// ÉN idempotensnøkkel per aktiveringsforsøk — gjenbrukes ved nettverksretry, så
// serveren ser samme nøkkel og ikke aktiverer to ganger.
function utfoerAttest(uid, diffHash, paaFerdig, ctx) {
  const nokkel = nyIdempotensnokkel();
  const forsok = (attempt) =>
    attesterAktivering(uid, diffHash, nokkel).then((svar) => {
      const utfall = (svar && svar.utfall) || "";
      // «Venter på flere godkjennere» skal si HVOR MANGE, og om det som
      // mangler er en UAVHENGIG godkjenner. Det er opplysningen som avgjør hva
      // du gjør videre, og den lå i svaret hele tiden.
      let tekst = t(`ui.policyadmin.utfall.${utfall}`,
        t("ui.policyadmin.utfall.ukjent"));
      if (utfall === "venter_godkjennere") {
        tekst += " " + t("ui.policyadmin.utfall.venter_antall")
          .replace("{avgitt}", String(svar.antall != null ? svar.antall : "?"))
          .replace("{gjenstaar}", String(gjenstaarIgjen(svar)));
        if (svar.mangler_uavhengig) {
          tekst += " " + t("ui.policyadmin.utfall.mangler_uavhengig");
        }
      }
      _settKvittering(uid, UTFALLSART.get(utfall) || "feil", tekst);
      if (paaFerdig) paaFerdig();
    }).catch((e) => {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      // Kun nettverksfeil retries — ÉN gang, SAMME nøkkel. En konflikt (stale
      // diff, rebasering) retries ALDRI blindt; utkastet lastes på nytt.
      if (e instanceof ApiFeil && e.status === 0 && attempt === 0) {
        return forsok(1);
      }
      if (e instanceof ApiFeil && e.kode === "rebasering_kreves") {
        _settKvittering(uid, "feil",
          t("ui.policyadmin.utfall.rebasering_kreves"));
        if (paaFerdig) paaFerdig();
        return;
      }
      if (e instanceof ApiFeil && e.kode === "diff_utdatert") {
        _settKvittering(uid, "feil", t("ui.policyadmin.diff_utdatert"));
        if (paaFerdig) paaFerdig();
        return;
      }
      _settKvittering(uid, "feil", t("ui.policyadmin.feilet"));
      if (paaFerdig) paaFerdig();
    });
  return forsok(0);
}

let _hintTeller = 0;

// Feil fra en handling som IKKE friskner opp siden, tegnes rett i
// handlingsboksen — synlig, ikke bare hørbar. Men boksen hører til DEN
// tegningen som lagde den, og POST-en er ute på nettet mens eier fortsatt kan
// klikke (Codex P2). Rakk hun «Tilbake» eller «Rediger» først, har `sett`
// byttet ut hele `hoved`, og `boks` henger i et frakoblet tre når svaret
// kommer. Et `append` dit er da verken synlig ELLER hørbar: `role="alert"`
// annonserer ingenting utenfor dokumentet. Da vi gjorde feilen synlig, mistet
// vi altså det ene sporet — `meldLive` — som overlevde navigasjon, og en
// mislykket handling ble helt stille.
//
// Boksen i dokumentet er fortsatt riktig sted: den ER et assertivt
// live-område og leses opp av seg selv, så et `meldLive` i tillegg ville gitt
// to konkurrerende opplesninger for ett klikk. Er den frakoblet, finnes det
// ingen skjerm å vise noe på, og utfallet går til den varige live-regionen i
// stedet. ÉN annonsering i begge tilfeller — bare ikke null.
function visEllerMeld(boks, node, talemelding) {
  if (boks.isConnected) { boks.append(node); return; }
  meldLive(talemelding);
}

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
    // Samme frakoblingsproblem som «Åpne runde» (se `visEllerMeld`): også
    // valideringen kan svare etter at eier har forlatt siden, og en feilliste
    // tegnet inn i et dødt tre er ingen tilbakemelding.
    const visFeil = (overskrift, linjer) => {
      boks.querySelectorAll(".pa-valfeil").forEach((n) => n.remove());
      visEllerMeld(boks,
        el("div", { class: "pa-valfeil", role: "alert" },
          el("p", { text: overskrift }),
          ...(linjer.length
            ? [el("ul", {}, ...linjer.map((f) => el("li", { text: String(f) })))]
            : [])),
        [overskrift, ...linjer.map(String)].join(" "));
    };
    b.addEventListener("click", () =>
      validerUtkast(uid, detalj.utkastversjon, valNokkel).then(() => {
        boks.querySelectorAll(".pa-valfeil").forEach((n) => n.remove());
        // «Valider» hadde samme feil som «Attester»: et grønt utfall gikk bare
        // til aria-live, og siden ble tegnet på nytt i samme åndedrag. Eier så
        // et klikk uten virkning enten utkastet var gyldig eller ikke.
        _settKvittering(uid, "ok", t("ui.policyadmin.validert"));
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
        _settKvittering(uid, "ok", t("ui.policyadmin.runde_apnet"));
        if (paaFerdig) paaFerdig();
      }).catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        // Ingen gjentegning her, så feilen tegnes rett i handlingsboksen —
        // synlig, ikke bare hørbar. Og har eier gått videre mens POST-en lå
        // ute, er boksen frakoblet: da leses feilen opp i stedet for å
        // forsvinne (Codex P2, se `visEllerMeld`).
        boks.querySelectorAll(".pa-kvittering").forEach((n) => n.remove());
        visEllerMeld(boks,
          el("div", { class: "pa-kvittering pa-kvittering-feil", role: "alert" },
            el("p", { text: t("ui.policyadmin.feilet") })),
          t("ui.policyadmin.feilet"));
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

function detaljInnhold(detalj, uid, ctx, paaFerdig, aapneEditor, kvitt) {
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
  // Kvitteringen står ØVERST, rett under overskriften fokus settes til — ikke
  // nederst ved knappen, som kan være utenfor skjermen etter gjentegningen.
  // Den kommer inn utenfra: den som STARTET tegningen har tatt den, så en
  // tegning som aldri når skjermen ikke etterlater seg en kvittering.
  const boks = kvitt ? kvitteringsBoks(kvitt) : null;
  // { rot, ferdig } — `ferdig()` kalles av den som har SATT INN `rot`, og fyller
  // live-området når det først står i dokumentet.
  return {
    rot: el("div", {}, ...(boks ? [boks.rot] : []), faner.rot, handl.rot),
    ferdig: () => { if (boks) boks.fyll(); },
  };
}

function tilbakeKnapp(tilbakeTilListe) {
  const b = el("button", { class: "knapp", type: "button",
    text: t("ui.policyadmin.tilbake_til_liste") });
  b.addEventListener("click", tilbakeTilListe);
  return b;
}

export function visPolicyadmin(hoved, ctx) {
  // `sort` er eiers kolonnevalg, og det bor HER fordi flaten overlever
  // tegningene — tabellen bygges på nytt hver gang lista lastes (Codex P2).
  const st = { rader: [], sort: null };

  // Eierskapet til `hoved` har TO nivåer, og et sent svar må bestå begge.
  //
  // Ruteren: alle flater deler ETT element, og et svar som er ute på nettet vet
  // ikke at brukeren har navigert videre. Kom detaljsvaret etter at hun hadde
  // valgt en annen toppnivårute, tegnet det seg selv over DEN flaten, mens
  // menyvalget hennes ble stående markert (Codex P2). Stempelet fanges ved
  // oppstart, og ruteren flytter det ved hver navigasjon.
  //
  // Flaten: stempelet står i ro så lenge man blir HER, men flaten bytter
  // visning på egen hånd — liste, detalj, editor — og de kappløper om det samme
  // elementet (Codex P2). Åpnet man utkast A og så B, besto begge svarene
  // rutersjekken: svarte A sist, tegnet A seg over B, og skjermen viste et annet
  // utkast enn det hun nettopp valgte. En «Prøv igjen» som fortsatt hang der da
  // hun gikk tilbake til lista, gjorde det samme. Hvert visningsbytte teller
  // derfor opp en egen generasjon.
  //
  // `fetch` kan ikke avbrytes bakover gjennom `hentJson`, så svaret slippes i
  // stedet: er ETT av stemplene flyttet, er svaret foreldet og skal ikke røre
  // skjermen.
  const minRute = visningsToken(hoved);
  let visning = 0;
  const nyVisning = () => ++visning;
  const eierSkjermen = (min) =>
    erGjeldendeVisning(hoved, minRute) && visning === min;

  // Editoren tar over `hoved`. Ved lagring åpnes utkastets detalj; Avbryt/
  // fullført går tilbake til lista.
  //
  // Eierskapet stoppet ved editordøra (Codex P2): generasjonen ble talt opp
  // her, men editoren fikk aldri vite hva den skulle måles mot. Den tegner
  // ingenting før utkastet er hentet, så detaljsiden — med tilbakeknappen —
  // blir stående mens GET-en er ute. Rakk eier å trykke «Tilbake», lastet lista,
  // og editorsvaret tegnet seg etterpå rett over den. Editoren får derfor med
  // seg SIN generasjon og sjekker den på hver asynkrone vei tilbake.
  function aapneEditor(opts) {
    const min = nyVisning();   // editoren eier `hoved` nå; eldre svar slipper ikke til
    visPolicyeditor(hoved, ctx, {
      ...opts,
      aapneUtkast: aapneDetalj,
      tilbake: tilbakeTilListe,
      eierSkjermen: () => eierSkjermen(min),
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
    const min = nyVisning();
    // Kvitteringen hentes HER, ikke nede i tegningen: fra nå av er den DENNE
    // tegningens eiendom og ligger ikke igjen i modulen. Kommer svaret etter at
    // eier har navigert bort, faller kvitteringen bort sammen med tegningen —
    // den kan ikke lenger forbrukes av et annet utkast. Men så lenge tegningen
    // FORTSATT eier skjermen, skal den vise kvitteringen sin uansett hvordan
    // den ender: begge grenene under tegner den.
    //
    // Og eier ikke tegningen skjermen lenger, dør ikke utfallet stille: begge
    // grenene melder det til live-området i stedet (`meldTaptKvittering`).
    const kvitt = taKvittering(uid);
    hentJson(`/v1/policyutkast/${uid}`).then((detalj) => {
      if (!eierSkjermen(min)) { meldTaptKvittering(kvitt); return; }
      // En handling som fullfører ETTER at eier har forlatt detaljsiden, skal
      // ikke hente den tilbake (Codex P1). `paaFerdig` friskner opp siden
      // etter Valider/Åpne runde/Attester — riktig så lenge siden er den man
      // står i. Klikket eier «Valider» og så «Rediger», lå POST-en fortsatt
      // ute mens editoren tok over `hoved`: svaret kom, kalte `aapneDetalj`,
      // og erstattet editoren — med det hun hadde rukket å skrive i den.
      // Oppfriskningen bæres derfor av visningen den ble startet fra.
      //
      // Men å DROPPE oppfriskningen var ikke å gjøre ingenting (Codex P2):
      // handlingen hadde alt lagt kvitteringen sin i modulen, og uten en
      // gjentegning ble den ALDRI forbrukt. Den ble hverken vist eller lest
      // opp — eier fikk null tilbakemelding på en fullmaktshandling som
      // faktisk ble utført — og den ble liggende igjen som en tidsinnstilt
      // felle: neste tegning av SAMME utkast viste et gammelt utfall som om
      // det var ferskt, og neste tegning av et ANNET utkast kastet den.
      //
      // Den tapte visningen håndteres derfor eksplisitt. Kvitteringen tas ut
      // med én gang — den hører til denne handlingen og skal ikke overleve
      // den — og utfallet leses opp i det varige live-området i stedet.
      // Skjermen kan ikke vise det lenger; skjermleseren kan fortsatt si det.
      // Det er nettopp her `meldLive` er riktig: her finnes det ingen
      // kvitteringsboks å konkurrere med.
      const innhold = detaljInnhold(detalj, uid, ctx, () => {
        if (eierSkjermen(min)) { aapneDetalj(uid); return; }
        meldTaptKvittering(taKvittering(uid));
      }, aapneEditor, kvitt);
      sett(hoved,
        ...flateHode(
          `${t("ui.policyadmin.detalj_tittel")}: ${detalj.policy_id}`,
          t("ui.policyadmin.detalj_undertittel").replace("{id}", uid)),
        tilbakeKnapp(tilbakeTilListe),
        innhold.rot);
      fokuserOverskrift(hoved);
      // Kvitteringsteksten settes ETTER at siden står og fokus er flyttet:
      // live-området skal fange en ENDRING i et område som allerede er i
      // tilgjengelighetstreet, og annonseringen skal ikke bli avbrutt av
      // fokusflyttingen rett etterpå. Selve skrivingen skjer i en senere
      // oppgave — `kvitteringsBoks` sørger for det, og forklarer hvorfor.
      innhold.ferdig();
    }).catch((e) => {
      // 401 er ikke navigasjon: sesjonen er ute, `paaUautorisert` sender eier
      // til innlogging, og et utfall lest opp inn i den overgangen ville blitt
      // overskrevet av innloggingssiden uansett. Kvitteringen faller her.
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (!eierSkjermen(min)) { meldTaptKvittering(kvitt); return; }
      // En feilet detalj-GET skal ikke stenge eier inne (Codex P2). Skuffen lot
      // i det minste lista ligge under seg; siden erstattet HELE flaten med en
      // naken feiltilstand — uten «Prøv igjen» og uten vei tilbake. Et
      // forbigående 5xx eller et nettverksglipp ble dermed en blindvei man bare
      // kom ut av ved å laste appen på nytt. Feiltilstanden er derfor en side
      // som de andre: den beholder overskrift og tilbakeknapp.
      //
      // «Prøv igjen» tilbys bare der den kan hjelpe: 403 er ingen forbigående
      // feil, og en knapp som lover et annet svar neste gang lyver.
      //
      // Kvitteringen følger tegningen HIT også (Codex P2). Utfallet av
      // HANDLINGEN og utfallet av OPPFRISKNINGEN er to forskjellige ting:
      // attesteringen lyktes, det er gjentegningen som feilet. Uten dette ble
      // kvitteringen forbrukt da tegningen startet og så aldri tegnet — eier
      // fikk INGEN tilbakemelding på en fullført fullmaktshandling, og «Prøv
      // igjen» kunne ikke hente den tilbake, for den var borte for godt. Det
      // nærliggende neste trekket er da å attestere om igjen. Derfor står
      // begge: hva som skjedde, og at siden ikke kunne hentes etterpå.
      const boks = kvitt ? kvitteringsBoks(kvitt) : null;
      sett(hoved,
        ...flateHode(t("ui.policyadmin.detalj_tittel"),
          t("ui.policyadmin.detalj_undertittel").replace("{id}", uid)),
        tilbakeKnapp(tilbakeTilListe),
        ...(boks ? [boks.rot] : []),
        e instanceof IngenTilgangFeil
          ? TilgangsVakt({})
          : Feiltilstand({ paaProvIgjen: () => aapneDetalj(uid) }));
      fokuserOverskrift(hoved);
      if (boks) boks.fyll();
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
          sort: st.sort,
          paaSort: (s) => { st.sort = s; },
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
  //
  // Fokuset gis til RAMMEN på samme måte som eierskapet: sjekken sto bare i
  // `tegnFn`, altså på suksessveien, og en avvist liste-GET når aldri dit
  // (Codex P2). Feilet «Tilbake» endte derfor med fokus på `body`. `medStatus`
  // flytter det nå på de veiene den selv tegner.
  function last(opts) {
    const flyttFokus = !!(opts && opts.fokus);
    const min = nyVisning();
    // Eierskapet gis til RAMMEN, ikke bare sjekket i `tegnFn`: en avvist
    // liste-GET når aldri `tegnFn`, og feiltilstanden `medStatus` tegner i
    // stedet, traff skjermen uvoktet (Codex P2). `medStatus` vokter nå begge
    // veier — her sier vi bare hvilken visning kallet ble startet under.
    medStatus(hoved, ctx, () => hentJson("/v1/policyutkast"), (d) => {
      st.rader = (d && d.utkast) || []; tegn(flyttFokus);
    }, () => eierSkjermen(min), flyttFokus);
  }

  function tilbakeTilListe() { last({ fokus: true }); }

  last();
}
