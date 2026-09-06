// M-29 hendelsesflaten (137) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
//   ROLLBACK.
//
// Klynge 9s ytring kunne ikke tas tilbake fordi noen hadde LEST den.
// Denne trenger ingen leser: kontoen er stengt, hemmeligheten er
// rullet, og tokenet den gamle klienten holdt er dødt. Derfor måler
// portene her:
//
//   * at «INGEN INNGREP UTFØRT» står i sammendraget, ALLTID. Tallet er
//     ikke en telling — det er en påstand om at kolonnen ikke finnes.
//   * at det ikke finnes en «isoler konto»- eller «roter nøkkel»-knapp
//     noe sted i flaten.
//   * at stegene i playbookskjemaet er AVKRYSSINGSBOKSER og ikke et
//     tekstfelt. Et tekstfelt ville tatt imot en kommando.
//   * at korrelasjonsskjemaet IKKE har et score-felt: scoren regnes av
//     regelens poeng, ikke av kalleren.
//   * at en score aldri vises uten regelen som ga den.
//   * at alvoret vises slik det STO DA, ikke regnet på nytt.
//   * at en hendelse over terskel UTEN forslag er et varsel.
//   * at de fire umulige funnene ikke har en lukkeknapp som ser ut som
//     en oppgave.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  dato, funnrader, hendelsesrader, iDagLokal, playbookrader,
  regelrader, sammendrag, scoretekst, visHendelse,
} from "../static/js/flater/hendelse.js";

settI18nForTest(NB, "nb");

const H1 = "aaaaaaaa-1111-1111-1111-111111111111";
const H2 = "aaaaaaaa-2222-2222-2222-222222222222";
const R1 = "cccccccc-1111-1111-1111-111111111111";
const R2 = "cccccccc-2222-2222-2222-222222222222";
const P1 = "dddddddd-1111-1111-1111-111111111111";
const P2 = "dddddddd-2222-2222-2222-222222222222";
const F1 = "eeeeeeee-1111-1111-1111-111111111111";
const F2 = "eeeeeeee-2222-2222-2222-222222222222";

const HENDELSER = [
  // OVER TERSKEL, OG INGEN HAR SKREVET ET FORSLAG. Sveipens viktigste
  // funn, og flatens viktigste varsel.
  { hendelse_id: H1, regel: "Gjentatte unntak fra samme aktoer",
    signaltype: "unntak_gjentatt", score: 150, alvor: "over_terskel",
    signaler: 3, forslag: 0, status: "apen",
    oppdaget_ts: "2026-09-01T09:00:00+00:00" },
  // UNDER TERSKEL, LUKKET.
  { hendelse_id: H2, regel: "Handling utenfor tidsvindu",
    signaltype: "handling_utenfor_tidsvindu", score: 40,
    alvor: "under_terskel", signaler: 2, forslag: 1, status: "lukket",
    oppdaget_ts: "2026-08-20T22:15:00+00:00" },
];

const REGLER = [
  { regel_id: R1, navn: "Gjentatte unntak fra samme aktoer",
    signaltype: "unntak_gjentatt", poeng: 50, terskel_treff: 3,
    gyldig_fra: "2026-01-01", gyldig_til: null, gjelder_i_dag: true,
    brukt: 1 },
  // EN REGEL SOM ALDRI TRAFF. Et deteksjonsapparat som ikke
  // detekterer ser nøyaktig ut som en base uten hendelser.
  { regel_id: R2, navn: "Hull i revisjonsloggen",
    signaltype: "revisjonshull", poeng: 200, terskel_treff: 1,
    gyldig_fra: "2026-01-01", gyldig_til: null, gjelder_i_dag: true,
    brukt: 0 },
];

const PLAYBOOKER = [
  { playbook_id: P1, navn: "Kompromittert konto",
    naar_gjelder_den: "Naar samme aktoer avvises gjentatte ganger",
    krever_tofaktor: true,
    steg: ["varsle_sikkerhetsansvarlig", "samle_tidslinje",
           "isoler_konto", "roter_hemmelighet"],
    gjelder_i_dag: true, godkjent_av: "u-kari", foreslatt_ganger: 1 },
  // EN PLAYBOOK UTEN STEG. Den ville tilfredsstilt fremmednøkkelen og
  // forklart ingenting.
  { playbook_id: P2, navn: "Tom plan", naar_gjelder_den: "Ubestemt",
    krever_tofaktor: false, steg: [], gjelder_i_dag: true,
    godkjent_av: "u-ola", foreslatt_ganger: 0 },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det.
  { funn_id: F1, funntype: "hendelse_uten_forslag", referanse: H1,
    detalj: "over terskel med score 150, ingen playbook foreslaatt",
    sveipens: true, forst_sett: "2026-09-02T04:00:00+00:00" },
  // ETT AV DE FIRE UMULIGE. Det står i settet og kan aldri reises.
  { funn_id: F2, funntype: "fri_kommando_kjort", referanse: "-",
    detalj: "kan ikke oppstaa: playbooksteg har ingen parameterkolonne",
    sveipens: false, forst_sett: "2026-09-02T04:00:00+00:00" },
];

const BILDE = {
  request_id: "r-t",
  sammendrag: {
    apne_hendelser: 1, over_terskel: 1, regler: 2, playbooker: 2,
    forslag: 1, inngrep_utfort: 0, apne_funn: 2,
    korrelasjonsvindu_min: 60, alvorsterskel: 100,
    apen_hendelse_frist_dogn: 7, signaltak: 50, kravversjon: 1,
  },
  hendelser: HENDELSER, regler: REGLER, playbooker: PLAYBOOKER,
  funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    apne_hendelser: 0, over_terskel: 0, regler: 0, playbooker: 0,
    forslag: 0, inngrep_utfort: 0, apne_funn: 0,
    korrelasjonsvindu_min: null, alvorsterskel: null,
    apen_hendelse_frist_dogn: null, signaltak: null, kravversjon: null,
  },
  hendelser: [], regler: [], playbooker: [], funn: [],
};

let SVAR;
let SISTE;
let SVARSTATUS;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method === "POST") {
    SISTE = { sti, headers: opts.headers,
              kropp: opts.body ? JSON.parse(opts.body) : null };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "hendelse_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, score: 150,
                                  alvor: "over_terskel", signaler: 3 }) };
  }
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
             json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["security:read", "bestilling:opprett"]) {
  return { sprak: "nb", scopes, tenant: "acme",
           paaUautorisert: () => {} };
}

async function vent(pred, n = 120) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 5));
  }
  return pred();
}

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold";
  m.tabIndex = -1;
  brett.append(m);
  SVAR = {
    "/v1/hendelse": BILDE,
    "/v1/hendelse/signaler": {
      request_id: "r-t", fra: "2026-09-01T00:00:00+00:00",
      kandidater: [
        { logg_id: 10001, aktor: "u-kari", kilde: "m01_policy",
          beslutning: "AVSLAG", policy_content_hash: "abc",
          ts: "2026-09-01T08:00:00+00:00" },
        { logg_id: 10002, aktor: "u-kari", kilde: "m01_policy",
          beslutning: "AVSLAG", policy_content_hash: "abc",
          ts: "2026-09-01T08:05:00+00:00" },
      ],
      avkortet: false },
  };
  SISTE = null;
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// DET MODULEN IKKE GJØR, SKAL SES.
// =====================================================================

test("sammendraget sier alltid at ingen inngrep er utført", () => {
  // TALLET ER IKKE EN TELLING AV EN KOLONNE. Det er en påstand om at
  // kolonnen ikke finnes: `inngrepsforslag` har ingen `utfort_ts`.
  //
  // Et menneske som leser flaten skal ikke måtte ANTA at maskinen
  // holdt seg i ro. Hun skal se det.
  const p = sammendrag(BILDE.sammendrag);
  assert.ok(p.textContent.includes(t("ui.hendelse.ingen_inngrep")
    .replace("{n}", "0")));
  // …OGSÅ NÅR ALT ANNET ER TOMT.
  const tom = sammendrag(TOMT.sammendrag);
  assert.ok(tom.textContent.includes(t("ui.hendelse.ingen_inngrep")
    .replace("{n}", "0")));
});

test("flaten har ingen isoler- eller roterknapp", async () => {
  // DET ER IKKE EN UTELATELSE. Fullmaktsmålene ligger allerede i
  // basen, og modulrollen har ikke så mye som SELECT på dem. En knapp
  // her ville kalt en rute som ikke finnes.
  //
  // STEGNAVNENE `isoler_konto` og `roter_hemmelighet` STÅR i flaten —
  // som avkryssingsbokser i playbookskjemaet og som tekst i
  // playbooklisten. Forskjellen porten måler er at ingen av dem er en
  // KNAPP: å skrive ned at noen bør isolere en konto er ikke å isolere
  // den.
  const h = nyHoved();
  await visHendelse(h, ctx());
  await vent(() => h.querySelector("#h-terskel"));
  const knapper = [...h.querySelectorAll("button")]
    .map((b) => b.textContent);
  for (const forbudt of [t("ui.hendelse.steg_isoler_konto"),
                         t("ui.hendelse.steg_isoler_token"),
                         t("ui.hendelse.steg_roter"),
                         t("ui.hendelse.steg_sesjoner")]) {
    assert.ok(!knapper.includes(forbudt),
      `flaten har en «${forbudt}»-knapp`);
  }
});

test("stegene er avkryssinger, ikke et tekstfelt", async () => {
  // ET TEKSTFELT VILLE TATT IMOT EN KOMMANDO. En liste med
  // avkryssinger kan bare uttrykke navn fra det lukkede settet.
  //
  // «Ingen fri kommandokjøring» er en grammatikk, ikke en policy — og
  // her er grammatikken selve skjemaet.
  const h = nyHoved();
  await visHendelse(h, ctx());
  await vent(() => h.querySelector("#h-steg-isoler_konto"));
  const boks = h.querySelector("#h-steg-isoler_konto");
  assert.equal(boks.type, "checkbox");
  // …OG DET FINNES INGEN FRITEKST I PLAYBOOKSKJEMAET UTENOM NAVN OG
  // BESKRIVELSE. Begge leses av et menneske; ingen av dem utføres.
  const skjema = boks.closest("form");
  const tekstfelt = [...skjema.querySelectorAll("input[type='text'],"
                                                + " textarea")]
    .map((x) => x.id);
  assert.deepEqual(tekstfelt.sort(), ["h-pbnaar", "h-pbnavn"]);
});

test("korrelasjonsskjemaet har ikke et score-felt", async () => {
  // KALLEREN OPPGIR ALDRI EN SCORE. Den regnes av regelens poeng mot
  // dens egen terskel — 132s lærdom, anvendt på en sikkerhetsscore.
  //
  // Et felt her ville gjort «forklarbare regler» til pynt:
  // forklaringen ville pekt på en regel mens tallet kom fra brukeren.
  const h = nyHoved();
  await visHendelse(h, ctx());
  await vent(() => h.querySelector("#h-korrelregel"));
  const skjema = h.querySelector("#h-korrelregel").closest("form");
  const felter = [...skjema.querySelectorAll("input, select, textarea")]
    .map((x) => x.id);
  assert.deepEqual(felter.sort(), ["h-korrelregel", "h-korreltimer"]);
});


// =====================================================================
// SCOREN, REGELEN OG ALVORET.
// =====================================================================

test("en score vises aldri uten regelen som ga den", () => {
  // En score uten en lesbar forklaring er en påstand, og «forklarbare
  // regler» er vaktsetningens eget ord.
  const tekst = scoretekst(HENDELSER[0]);
  assert.ok(tekst.includes("150"));
  assert.ok(tekst.includes("Gjentatte unntak fra samme aktoer"));
  assert.equal(scoretekst({}), "–");
});

test("alvoret vises slik det sto da, ikke regnet på nytt", () => {
  // Terskelen kan ha endret seg siden. En hendelse som var over
  // terskel da noen så på den, VAR det — og flaten leser feltet
  // framfor å regne 150 mot dagens tall.
  const rader = hendelsesrader(BILDE, { kanSkrive: false }, () => {});
  const forste = rader[0].textContent;
  assert.ok(forste.includes(t("ui.hendelse.alvor_over")));
  const andre = rader[1].textContent;
  assert.ok(andre.includes(t("ui.hendelse.alvor_under")));
});

test("en hendelse over terskel uten forslag er et varsel", () => {
  // MODULEN KAN IKKE GJØRE NOE MED HENDELSEN. Da er det å stå uten et
  // forslag den eneste feilen den kan oppdage i seg selv.
  const rader = hendelsesrader(BILDE, { kanSkrive: false }, () => {});
  const varsler = [...rader[0].querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.deepEqual(varsler, [t("ui.hendelse.mangler_forslag")]);
  // …OG DEN LUKKEDE MED ETT FORSLAG ER DET IKKE.
  assert.equal(rader[1].querySelectorAll("strong[role='alert']").length,
               0);
});

test("bare en åpen hendelse kan lukkes eller få et forslag", () => {
  const rader = hendelsesrader(BILDE, { kanSkrive: true }, () => {});
  const knapper = (r) => [...r.querySelectorAll("button")]
    .map((b) => b.textContent);
  assert.deepEqual(knapper(rader[0]).sort(),
    [t("ui.hendelse.foresla"), t("ui.hendelse.lukk_hendelse")].sort());
  assert.deepEqual(knapper(rader[1]), []);
});

test("en leser uten skrivescope får ingen handlingsknapper", () => {
  const rader = hendelsesrader(BILDE, { kanSkrive: false }, () => {});
  assert.equal(rader[0].querySelectorAll("button").length, 0);
});


// =====================================================================
// PLAYBOOKENE OG REGLENE.
// =====================================================================

test("playbooken vises med stegene sine, i rekkefølge", () => {
  // Det er ikke en utførelsesplan — det er en liste noen har skrevet
  // ned på forhånd, og v1 utfører den ikke.
  const rader = playbookrader(BILDE);
  const punkter = [...rader[0].querySelectorAll("ol.stegliste li")]
    .map((x) => x.textContent);
  assert.deepEqual(punkter, [
    t("ui.hendelse.steg_varsle_sikkerhet"),
    t("ui.hendelse.steg_tidslinje"),
    t("ui.hendelse.steg_isoler_konto"),
    t("ui.hendelse.steg_roter"),
  ]);
});

test("en playbook uten steg er et varsel", () => {
  // Den ville tilfredsstilt fremmednøkkelen i `inngrepsforslag` og
  // forklart ingenting — nøyaktig den fail-open-formen modulen finnes
  // for å hindre.
  const rader = playbookrader(BILDE);
  const varsel = rader[1].querySelector("strong[role='alert']");
  assert.ok(varsel);
  assert.equal(varsel.textContent, t("ui.hendelse.playbook_uten_steg"));
});

test("tofaktorkravet står i playbooklisten", () => {
  const rader = playbookrader(BILDE);
  assert.ok(rader[0].textContent.includes(t("ui.hendelse.tofaktor_ja")));
  assert.ok(rader[1].textContent.includes(t("ui.hendelse.tofaktor_nei")));
});

test("en regel som aldri traff er merket", () => {
  // En regelsamling der ingen regel noen gang traff, er et
  // deteksjonsapparat som ikke detekterer — og det ser nøyaktig ut som
  // en base uten hendelser.
  const rader = regelrader(BILDE, { kanSkrive: false }, () => {});
  assert.ok(!rader[0].textContent.includes(t("ui.hendelse.regel_ubrukt")));
  assert.ok(rader[1].textContent.includes(t("ui.hendelse.regel_ubrukt")));
});

test("regelen vises med poengene og terskelen sin", () => {
  const rader = regelrader(BILDE, { kanSkrive: false }, () => {});
  assert.ok(rader[0].textContent.includes("50"));
  assert.ok(rader[0].textContent.includes("3"));
});


// =====================================================================
// FUNNENE.
// =====================================================================

test("sveipens egne funn har ingen lukkeknapp", () => {
  // De lukkes når tilstanden er borte. En knapp her ville invitert til
  // å lukke en MÅLING framfor en sak.
  const rader = funnrader(BILDE, { kanSkrive: true }, () => {});
  assert.equal(rader[0].querySelectorAll("button").length, 0);
  assert.ok(rader[0].textContent
    .includes(t("ui.hendelse.lukkes_av_sveipen")));
});

test("de fire umulige funnene har et navn i flaten", () => {
  // De står i det lukkede settet OG kan aldri reises. At de gjør begge
  // deler er beviset — og en flate som ikke kunne NAVNGI dem ville
  // vist «Ukjent funntype» den dagen noe umulig skjedde.
  for (const type of ["inngrep_uten_playbook", "fri_kommando_kjort",
                      "hendelse_uten_score", "score_uten_regel"]) {
    const rader = funnrader(
      { funn: [{ funn_id: F2, funntype: type, referanse: "-",
                 detalj: "d", sveipens: false,
                 forst_sett: "2026-09-02T04:00:00+00:00" }] },
      { kanSkrive: false }, () => {});
    assert.ok(!rader[0].textContent.includes(t("ui.hendelse.funn_ukjent")),
      `${type} mangler en tekst`);
  }
});

test("dato viser dato og klokkeslett", () => {
  assert.equal(dato("2026-09-01T09:00:00+00:00"), "2026-09-01 09:00");
  assert.equal(dato(null), "–");
  assert.equal(dato(""), "–");
});

test("iDagLokal gir brukerens dato, ikke UTC-datoen", () => {
  // `new Date().toISOString().slice(0, 10)` gir UTC. Norge ligger
  // FORAN UTC, så mellom midnatt og 01/02 om natten gir den
  // GÅRSDAGEN — og en regel registrert «i dag» ville blitt forsøkt
  // avviklet dagen FØR den gjaldt.
  //
  // Arvet fra 133/135, der CodeRabbit fant den 5/9.
  const natt = new Date(2026, 8, 6, 0, 30, 0);
  assert.equal(iDagLokal(natt), "2026-09-06");
});


// =====================================================================
// HELE FLATEN.
// =====================================================================

test("en leser uten skrivescope får ingen skjemaer", async () => {
  const h = nyHoved();
  await visHendelse(h, ctx(["security:read"]));
  await vent(() => h.textContent.includes(t("ui.hendelse.hendelser")));
  assert.equal(h.querySelector("#h-terskel"), null);
  assert.equal(h.querySelector("#h-pbnavn"), null);
  assert.equal(h.querySelector("#h-korrelregel"), null);
});

test("kravskjemaet forhåndsutfylles med ALLE fire grensene", async () => {
  // Et skjema som viser mindre enn det lagrer er en felle (123s
  // lærdom).
  const h = nyHoved();
  await visHendelse(h, ctx());
  await vent(() => h.querySelector("#h-terskel"));
  assert.equal(h.querySelector("#h-vindu").value, "60");
  assert.equal(h.querySelector("#h-terskel").value, "100");
  assert.equal(h.querySelector("#h-frist").value, "7");
  assert.equal(h.querySelector("#h-tak").value, "50");
});

test("korrelasjonspanelet sender aldri en score", async () => {
  const h = nyHoved();
  await visHendelse(h, ctx());
  await vent(() => h.querySelector("#h-korrelregel"));
  const skjema = h.querySelector("#h-korrelregel").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true,
                                                    bubbles: true }));
  await vent(() => SISTE && SISTE.sti === "/v1/hendelse/korreler");
  assert.ok(SISTE, "korrelasjonen ble aldri sendt");
  assert.ok(!("score" in SISTE.kropp), "kroppen bar en score");
  assert.ok(!("alvor" in SISTE.kropp), "kroppen bar et alvor");
  // …OG DE TRE LISTENE ER LIKE LANGE. Ulike lengder ville latt døra
  // stilltiende bruke den korteste og tape signaler.
  assert.equal(SISTE.kropp.kilde_refs.length, 2);
  assert.equal(SISTE.kropp.aktorer.length, 2);
  assert.equal(SISTE.kropp.observert.length, 2);
});

test("playbookskjemaet sender stegene i settets rekkefølge", async () => {
  // To brukere som krysset av i ulik rekkefølge skal få samme
  // playbook. Rekkefølgen er settets, ikke avkryssingens.
  const h = nyHoved();
  await visHendelse(h, ctx());
  await vent(() => h.querySelector("#h-steg-isoler_konto"));
  h.querySelector("#h-pbnavn").value = "Ny plan";
  h.querySelector("#h-pbnaar").value = "Naar noe gjentar seg ofte nok";
  // Kryss av i OMVENDT rekkefølge av settet.
  h.querySelector("#h-steg-roter_hemmelighet").checked = true;
  h.querySelector("#h-steg-samle_tidslinje").checked = true;
  const skjema = h.querySelector("#h-pbnavn").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true,
                                                    bubbles: true }));
  await vent(() => SISTE && SISTE.sti === "/v1/hendelse/playbook");
  assert.deepEqual(SISTE.kropp.steg,
                   ["samle_tidslinje", "roter_hemmelighet"]);
  // …OG INGEN PARAMETER FULGTE MED. Det finnes ikke noe felt et
  // argument kunne ligget i.
  assert.deepEqual(Object.keys(SISTE.kropp).sort(),
    ["gyldig_fra", "gyldig_til", "krever_tofaktor", "naar_gjelder_den",
     "navn", "steg"]);
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visHendelse(h, ctx());
  await vent(() => h.querySelector("#h-terskel"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/hendelse"] = TOMT;
  await visHendelse(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.hendelse.hendelser_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten sier hvorfor det ikke går an å korrelere", async () => {
  // Uten en regel kan ingenting scores, og en score uten regel er en
  // påstand. Flaten sier det framfor å vise et skjema som ikke virker.
  const h = nyHoved();
  SVAR["/v1/hendelse"] = TOMT;
  await visHendelse(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.hendelse.korreler_uten_regel")));
  assert.equal(h.querySelector("#h-korrelregel"), null);
});


test("«ingen kandidater» er et svar, ikke «noe gikk galt»", async () => {
  // INGEN KANDIDATRADER ER IKKE EN FEIL — DET ER SVARET.
  //
  // Første utgave kastet `FeilformetFeil(tekst)`. Den konstruktøren
  // tar `(status, kode, detaljer)`, så teksten havnet i `status` og
  // meldingen ble tom — og `skjemaramme` viste «noe gikk galt». En
  // bruker ville prøvd igjen i det uendelige mot et tomt vindu.
  //
  // CodeRabbit fant at meldingen aldri nådde fram; feilen under den
  // var at den aldri fantes.
  const h = nyHoved();
  SVAR["/v1/hendelse/signaler"] = {
    request_id: "r-t", fra: "2026-09-01T00:00:00+00:00",
    kandidater: [], avkortet: false };
  await visHendelse(h, ctx());
  await vent(() => h.querySelector("#h-korrelregel"));
  const skjema = h.querySelector("#h-korrelregel").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true,
                                                    bubbles: true }));
  await vent(() => h.textContent.includes(t("ui.hendelse.ingen_kandidater")));
  assert.ok(h.textContent.includes(t("ui.hendelse.ingen_kandidater")));
  assert.ok(!h.textContent.includes(t("ui.hendelse.feil.generell")));
});
