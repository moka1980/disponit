// M-50 journalflaten (124) — flateporten (jsdom + axe).
//
// PORTENE MÅLER MODULENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
// Postjournaler ER offentlige, så den vanlige innvendingen mot
// utgående oppslag treffer ikke. Det som treffer er at journalene
// inneholder NAVNGITTE PRIVATPERSONER, og at ti tusen oppslag
// sammenstilt i et register er en PROFIL — som er VÅR, ikke kommunens.
//
// Derfor måler portene her:
//
//   * at det som er GALT står FØRST (personopplysninger oppbevart
//     etter egen slettefrist),
//   * at FORMÅLET følger hver rad — «vi fant det på nett» er ikke et
//     rettslig grunnlag,
//   * at posten ikke kan sendes uten minst én person MED slettefrist,
//   * at «anonymiser» sier at den ikke sletter,
//   * og at flaten ikke avgjør hva som kan lukkes.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  FORMATER, GRUNNLAG, ROLLER, ilokalDato, kildeTekst, kildetabell,
  persontabell, persontilstand, posttabell, saktabell, sammendrag,
  slettefristTekst, visJournal,
} from "../static/js/flater/journal.js";

settI18nForTest(NB, "nb");

const HER = dirname(fileURLToPath(import.meta.url));
const ROT = join(HER, "..", "..", "..", "..");

const K1 = "11111111-1111-1111-1111-111111111111";
const K2 = "22222222-2222-2222-2222-222222222222";
const S1 = "aaaaaaaa-1111-1111-1111-111111111111";
const P1 = "bbbbbbbb-1111-1111-1111-111111111111";
const P2 = "bbbbbbbb-2222-2222-2222-222222222222";
const PE1 = "cccccccc-1111-1111-1111-111111111111";
const PE2 = "cccccccc-2222-2222-2222-222222222222";
const F1 = "dddddddd-1111-1111-1111-111111111111";
const F2 = "dddddddd-2222-2222-2222-222222222222";
const SHA = "a".repeat(64);

const KILDER = [
  { kilde_id: K1, organ: "Oslo kommune", organnummer: "958935420",
    format: "noark5", versjon: "2026.1", gyldig_fra: "2026-01-01",
    gyldig_til: null, gyldig_naa: true, dogn_til_utlop: null,
    innhold_sha256: SHA, kilde_url: null, antall_poster: 2 },
  { kilde_id: K2, organ: "Oslo kommune", organnummer: "958935420",
    format: "kommunal_web", versjon: "2019", gyldig_fra: "2019-01-01",
    gyldig_til: "2020-12-31", gyldig_naa: false,
    dogn_til_utlop: -2000, innhold_sha256: SHA, kilde_url: null,
    antall_poster: 1 },
];

const SAKER = [
  { sak_id: S1, tittel: "Byggesaker Grünerløkka",
    formaal: "kartlegging av byggesaker for oppfølging av egne kunder",
    grunnlag: "berettiget_interesse",
    opprettet: "2026-06-01T09:00:00+00:00", opprettet_av: "u-1",
    antall_poster: 2, antall_personer: 2 },
];

// ÉN POST MED PASSERT SLETTEFRIST, én med frist som nærmer seg.
const POSTER = [
  { post_id: P1, sak_id: S1, saktittel: "Byggesaker Grünerløkka",
    journalnummer: "24/1187", journaldato: "2026-05-14",
    dokumenttittel: "Søknad om rammetillatelse",
    formaal: "kartlegging av byggesaker for oppfølging av egne kunder",
    organ: "Oslo kommune", format: "noark5", kildeversjon: "2026.1",
    kilde_gyldig_naa: true, hentet_av_person: "Ola Nordmann",
    hentet_dato: "2026-05-20", antall_personer: 1,
    antall_levende: 1, naermeste_slettefrist: "2026-08-26",
    kravversjon: 1, registrert: "2026-05-20T09:00:00+00:00" },
  { post_id: P2, sak_id: S1, saktittel: "Byggesaker Grünerløkka",
    journalnummer: "19/442", journaldato: "2019-11-03",
    dokumenttittel: "Gammel sak",
    formaal: "kartlegging av byggesaker for oppfølging av egne kunder",
    organ: "Oslo kommune", format: "kommunal_web",
    kildeversjon: "2019", kilde_gyldig_naa: false,
    hentet_av_person: "Ola Nordmann", hentet_dato: "2026-05-20",
    antall_personer: 1, antall_levende: 1,
    naermeste_slettefrist: "2026-10-05", kravversjon: 1,
    registrert: "2026-05-20T09:00:00+00:00" },
];

const PERSONER = {
  request_id: "r-p", post_id: P1,
  personer: [
    { person_id: PE1, navn: "Kari Nordmann", rolle: "part",
      slettefrist: "2026-08-26", dogn_til_slettefrist: -10,
      anonymisert_ts: null, anonymisert_av: null,
      registrert: "2026-05-20T09:00:00+00:00", registrert_av: "u-1" },
    { person_id: PE2, navn: null, rolle: "omtalt",
      slettefrist: "2026-07-01", dogn_til_slettefrist: -66,
      anonymisert_ts: "2026-07-02T09:00:00+00:00",
      anonymisert_av: "u-1",
      registrert: "2026-05-20T09:00:00+00:00", registrert_av: "u-1" },
  ],
};

const BILDE = {
  request_id: "r-b",
  sammendrag: {
    saker: 1, poster: 2, personer: 3, levende_personer: 2,
    frist_passert: 1, frist_naer: 1, kilder: 2, gyldige: 1,
    utlopte: 1, apne_funn: 2, har_krav: true,
    sletteplan_maks_dogn: 365, kravversjon: 1, vist: 2,
  },
  krav: { sletteplan_maks_dogn: 365, slettevarsel_dogn: 30,
          kildevarsel_dogn: 60, versjon: 1 },
  kilder: KILDER, saker: SAKER, poster: POSTER,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: { saker: 0, poster: 0, personer: 0,
                levende_personer: 0, frist_passert: 0, frist_naer: 0,
                kilder: 0, gyldige: 0, utlopte: 0, apne_funn: 0,
                har_krav: false, sletteplan_maks_dogn: null,
                kravversjon: null, vist: 0 },
  krav: null, kilder: [], saker: [], poster: [],
};

const FUNN = {
  request_id: "r-f",
  funn: [
    { funn_id: F1, funntype: "slettefrist_passert", kilde_id: null,
      post_id: null, person_id: PE1, organ: "Oslo kommune",
      kildeversjon: "2026.1", journalnummer: "24/1187",
      rolle: "part", slettefrist: "2026-08-26", over_grense: 10,
      detalj: "oppbevart etter egen slettefrist", kravversjon: 1,
      // SVEIPENS EGET — ingen kan lukke det for hånd.
      kan_lukkes: false, forst_sett: "2026-08-27T09:00:00+00:00",
      sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
      lukket_ts: null, lukket_av: null, lukkenotat: null },
    { funn_id: F2, funntype: "slettefrist_naermer_seg",
      kilde_id: null, post_id: null, person_id: PE2,
      organ: "Oslo kommune", kildeversjon: "2026.1",
      journalnummer: "19/442", rolle: "omtalt",
      slettefrist: "2026-10-05", over_grense: 30, detalj: null,
      kravversjon: 1,
      // ET VARSEL — det kan lukkes.
      kan_lukkes: true, forst_sett: "2026-09-05T09:00:00+00:00",
      sist_sett_sveip: "2026-09-05T09:00:00+00:00", apen: true,
      lukket_ts: null, lukket_av: null, lukkenotat: null },
  ],
};

let SVAR;
let SISTE;
let KALL;
let SVARSTATUS;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  KALL.push({ sti, metode: (opts && opts.method) || "GET" });
  if (opts && opts.method === "POST") {
    // EN POST UTEN KROPP ER LOVLIG (CodeRabbit): `/anonymiser` tar
    // ingen felter. `JSON.parse(undefined)` ville kastet, og da hadde
    // riggen — ikke koden — bestemt hva som er en gyldig forespørsel.
    SISTE = { sti, headers: opts.headers,
              kropp: opts.body ? JSON.parse(opts.body) : null };
    if (SVARSTATUS && SVARSTATUS !== 200) {
      return { ok: false, status: SVARSTATUS,
        json: async () => ({ feil: "journal_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, antall_personer: 1 }) };
  }
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
      json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["okonomi:read", "bestilling:opprett"]) {
  return { sprak: "nb", scopes, tenant: "acme", paaUautorisert: () => {} };
}

async function vent(pred, n = 120) {
  for (let i = 0; i < n; i++) {
    if (pred()) return true;
    await new Promise((r) => setTimeout(r, 0));
  }
  return pred();
}

function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m);
  KALL = []; SISTE = undefined; SVARSTATUS = 200;
  return m;
}

const fullSvar = () => ({
  "/v1/journal": BILDE,
  "/v1/journal/funn": FUNN,
  [`/v1/journal/post/${P1}/personer`]: PERSONER,
  [`/v1/journal/post/${P2}/personer`]: {
    request_id: "r-p2", post_id: P2, personer: [] },
});

const tabeller = (h) => [...h.querySelectorAll("table")];
const tabell = (h, nokkel) => tabeller(h).find(
  (tb) => tb.querySelector("caption").textContent === t(nokkel));

// ---------------------------------------------------------------------
// DET SOM ER GALT STÅR FØRST
// ---------------------------------------------------------------------

test("journal: sammendraget begynner med de passerte slettefristene",
     () => {
  // MODULENS DOM, SETT FRA FLATEN. Et sammendrag som begynte med «142
  // journalposter registrert» ville fortalt hvor flittige vi har vært,
  // ikke at vi oppbevarer noen for lenge.
  //
  // MUTASJONEN SOM DREPER DENNE: flytt `frist_passert` bakerst.
  const p = sammendrag(BILDE.sammendrag);
  const forste = p.querySelector("strong");
  assert.equal(forste.textContent,
    t("ui.journal.passert_sum").replace("{n}", "1"));
  assert.equal(forste.tagName, "STRONG");
});

test("journal: uten oppbevaringsgrenser sier flaten at ingenting"
     + " overvåkes", () => {
  const p = sammendrag(TOMT.sammendrag);
  const varsler = [...p.querySelectorAll("[role='alert']")]
    .map((e) => e.textContent);
  assert.ok(varsler.includes(t("ui.journal.ingen_grenser")));
});

test("journal: slettefristen har retning", () => {
  // Fortegnet er hele beskjeden: en frist om tretti døgn er en plan,
  // en som gikk for tretti døgn siden er et brudd.
  assert.equal(slettefristTekst(null), t("ui.journal.uten_frist"));
  assert.equal(slettefristTekst(0), t("ui.journal.frist_i_dag"));
  assert.ok(slettefristTekst(30).includes("30"));
  assert.ok(slettefristTekst(-30).includes("30"));
  assert.notEqual(slettefristTekst(30), slettefristTekst(-30));
});

test("journal: personens tilstand navngir bruddet", () => {
  assert.equal(persontilstand(PERSONER.personer[0]),
    t("ui.journal.oppbevart_for_lenge").replace("{n}", "10"));
  // ANONYMISERT SJEKKES FØRST: en anonymisert rad har ingen
  // personopplysning igjen å oppbevare for lenge.
  assert.equal(persontilstand(PERSONER.personer[1]),
    t("ui.journal.anonymisert"));
  assert.equal(persontilstand({ anonymisert_ts: null,
    dogn_til_slettefrist: 30 }), t("ui.journal.oppbevares"));
});

test("journal: et fjernet navn vises som fjernet, ikke som tomt", () => {
  // `null` er ikke et hull i svaret — det ER svaret.
  const tb = persontabell(PERSONER.personer, null);
  const navn = [...tb.querySelectorAll("tbody th")]
    .map((e) => e.textContent);
  assert.deepEqual(navn, ["Kari Nordmann",
                          t("ui.journal.navn_fjernet")]);
});

// ---------------------------------------------------------------------
// FORMÅLET FØLGER HVER RAD
// ---------------------------------------------------------------------

test("journal: formålet er en kolonne, ikke en detalj", () => {
  // En sammenstilling uten et skrevet formål er en behandling ingen
  // kan gjøre rede for.
  const st = saktabell(SAKER);
  assert.ok(st.textContent.includes(SAKER[0].formaal));
  assert.ok([...st.querySelectorAll("thead th")]
    .map((e) => e.textContent).includes(t("ui.journal.kol.formaal")));
  const pt = posttabell(POSTER, null);
  assert.ok(pt.textContent.includes(POSTER[0].formaal));
  assert.ok([...pt.querySelectorAll("thead th")]
    .map((e) => e.textContent).includes(t("ui.journal.kol.formaal")));
});

test("journal: kildeversjonen vises aldri uten om den gjelder", () => {
  const gyldig = kildeTekst(KILDER[0]);
  const utlopt = kildeTekst(KILDER[1]);
  assert.notEqual(gyldig, utlopt);
  assert.ok(utlopt.includes("2019"));
  assert.equal(utlopt, t("ui.journal.kilde_utlopt")
    .replace("{navn}", `Oslo kommune · ${t("ui.journal.format_web")}`
      + " 2019"));
});

test("journal: posten sier hvem som hentet den", () => {
  // ET MENNESKE HENTET DEN. Det finnes ingen `hentet_automatisk`.
  const pt = posttabell(POSTER, null);
  assert.ok(pt.textContent.includes("Ola Nordmann"));
  assert.ok([...pt.querySelectorAll("thead th")]
    .map((e) => e.textContent)
    .includes(t("ui.journal.kol.hentet_av")));
});

// ---------------------------------------------------------------------
// POSTEN KAN IKKE REGISTRERES UTEN EN PERSON MED SLETTEFRIST
// ---------------------------------------------------------------------

test("journal: postknappen er død uten minst én person", async () => {
  // MODULENS SKARPESTE FLATE. Døra skriver posten og personene i
  // SAMME setning, så en journalpost med navngitte privatpersoner
  // ikke kan eksistere uten slettefrister.
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => h.querySelector("#jo-p-sak"));
  const skjema = h.querySelector("#jo-p-sak").closest("form");
  const send = [...skjema.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.journal.knapp.registrer_post"));
  assert.equal(send.disabled, true);

  for (const [id, verdi] of [["#jo-p-sak", S1], ["#jo-p-kilde", K1],
                             ["#jo-p-nr", "24/9"],
                             ["#jo-p-dato", "2026-09-01"],
                             ["#jo-p-tittel", "Ny sak"],
                             ["#jo-p-formaal",
                              "kartlegging av byggesaker i bydelen"],
                             ["#jo-p-hentet", "Ola Nordmann"],
                             ["#jo-p-hdato", "2026-09-05"]]) {
    const k = skjema.querySelector(id);
    k.value = verdi;
    k.dispatchEvent(new Event("input", { bubbles: true }));
    k.dispatchEvent(new Event("change", { bubbles: true }));
  }
  assert.equal(send.disabled, true,
    "knappen ble levende av alt UNNTATT personene");
  // …OG FLATEN SIER HØYT AT LISTA ER TOM.
  const tom = [...skjema.querySelectorAll("[role='alert']")].find(
    (e) => e.textContent === t("ui.journal.post.ingen_personer"));
  assert.ok(tom, "den tomme personlisten var stille");
  assert.equal(tom.closest("ul"), null,
    "beskjeden står inne i lista og bryter listitem-rollen");
});

test("journal: én person med slettefrist vekker knappen", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => h.querySelector("#jo-p-sak"));
  const skjema = h.querySelector("#jo-p-sak").closest("form");
  for (const [id, verdi] of [["#jo-p-sak", S1], ["#jo-p-kilde", K1],
                             ["#jo-p-nr", "24/9"],
                             ["#jo-p-dato", "2026-09-01"],
                             ["#jo-p-tittel", "Ny sak"],
                             ["#jo-p-formaal",
                              "kartlegging av byggesaker i bydelen"],
                             ["#jo-p-hentet", "Ola Nordmann"],
                             ["#jo-p-hdato", "2026-09-05"],
                             ["#jo-p-pnavn", "Kari Nordmann"],
                             ["#jo-p-pfrist", "2026-10-01"]]) {
    const k = skjema.querySelector(id);
    k.value = verdi;
    k.dispatchEvent(new Event("input", { bubbles: true }));
    k.dispatchEvent(new Event("change", { bubbles: true }));
  }
  const leggTil = [...skjema.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.journal.knapp.legg_til_person"));
  assert.equal(leggTil.disabled, false);
  leggTil.click();
  const send = [...skjema.querySelectorAll("button")].find(
    (b) => b.textContent === t("ui.journal.knapp.registrer_post"));
  assert.equal(send.disabled, false);

  skjema.dispatchEvent(new Event("submit", { bubbles: true,
                                             cancelable: true }));
  await vent(() => SISTE && SISTE.sti.endsWith("/post"));
  // PERSONEN FØLGER POSTEN I SAMME KALL.
  assert.equal(SISTE.kropp.personer.length, 1);
  assert.equal(SISTE.kropp.personer[0].slettefrist, "2026-10-01");
  assert.equal(KALL.filter((k) => k.metode === "POST").length, 1,
    "personen ble sendt i et eget kall");
});

test("journal: slettefristfeltet bærer tenantens tak", async () => {
  // TAKET KOMMER FRA SVARET, aldri fra en konstant i flaten.
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => h.querySelector("#jo-p-pfrist"));
  const maks = h.querySelector("#jo-p-pfrist").max;
  assert.ok(maks, "taket ble ikke satt");
  const om365 = new Date();
  om365.setDate(om365.getDate() + 365);
  assert.equal(maks, ilokalDato(om365));
});

test("journal: datoen er brukerens døgn, ikke UTC", () => {
  // 123s CodeRabbit-funn, anvendt her uten å måtte finnes på nytt.
  const midnatt = new Date(2026, 8, 5, 0, 30, 0);
  assert.equal(ilokalDato(midnatt), "2026-09-05");
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "journal.js"), "utf-8");
  assert.equal(/toISOString\(\)\.slice/.test(kilde), false);
});

// ---------------------------------------------------------------------
// «ANONYMISER» ER IKKE «SLETT»
// ---------------------------------------------------------------------

test("journal: anonymiseringsknappen sier at den ikke sletter",
     async () => {
  // At vi HAR oppbevart noen skal fortsatt kunne leses. Sletting ville
  // fjernet beviset på at vi hadde den — og den som trykker skal vite
  // hvilken av de to som skjer.
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => tabell(h, "ui.journal.poster.tittel"));
  const rad = [...tabell(h, "ui.journal.poster.tittel")
    .querySelectorAll("tbody tr")][0];
  rad.querySelector("button").click();
  await vent(() => tabell(h, "ui.journal.personer.tittel"));
  const tekster = [...h.querySelectorAll("p")].map((e) => e.textContent);
  assert.ok(tekster.includes(t("ui.journal.personer.hvorfor")));
  // …OG INGEN KNAPP HETER «SLETT».
  for (const knapp of h.querySelectorAll("button")) {
    assert.equal(knapp.textContent.toLowerCase().includes("slett"),
                 false, knapp.textContent);
  }
});

test("journal: formålet står over navnene", async () => {
  // Den som ser på en liste med navngitte privatpersoner skal se
  // HVORFOR vi har dem.
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => tabell(h, "ui.journal.poster.tittel"));
  [...tabell(h, "ui.journal.poster.tittel")
    .querySelectorAll("tbody tr")][0].querySelector("button").click();
  await vent(() => tabell(h, "ui.journal.personer.tittel"));
  assert.ok([...h.querySelectorAll("p")].some((e) =>
    e.textContent === t("ui.journal.personer.formaal")
      .replace("{formaal}", POSTER[0].formaal)),
  "navnene sto uten formålet de er samlet under");
});

test("journal: en alt anonymisert rad tilbys ikke på nytt", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => tabell(h, "ui.journal.poster.tittel"));
  [...tabell(h, "ui.journal.poster.tittel")
    .querySelectorAll("tbody tr")][0].querySelector("button").click();
  await vent(() => tabell(h, "ui.journal.personer.tittel"));
  const rader = [...tabell(h, "ui.journal.personer.tittel")
    .querySelectorAll("tbody tr")];
  assert.ok(rader[0].querySelector("button"),
    "den levende raden manglet knappen");
  assert.equal(rader[1].querySelector("button"), null,
    "en alt anonymisert rad ble tilbudt anonymisering");
});

test("journal: anonymiseringen sendes uten kropp", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => tabell(h, "ui.journal.poster.tittel"));
  [...tabell(h, "ui.journal.poster.tittel")
    .querySelectorAll("tbody tr")][0].querySelector("button").click();
  await vent(() => tabell(h, "ui.journal.personer.tittel"));
  [...tabell(h, "ui.journal.personer.tittel")
    .querySelectorAll("tbody tr")][0].querySelector("button").click();
  await vent(() => SISTE && SISTE.sti.endsWith("/anonymiser"));
  assert.equal(SISTE.sti, `/v1/journal/person/${PE1}/anonymiser`);
  assert.ok(SISTE.headers["Idempotency-Key"]);
  // KROPPEN ER TOM. Anonymiseringen tar ingen felter — et felt her
  // ville vært noe klienten fikk bestemme om en sletting.
  assert.deepEqual(SISTE.kropp, {});
});

// ---------------------------------------------------------------------
// FLATEN AVGJØR IKKE HVA SOM KAN LUKKES
// ---------------------------------------------------------------------

test("journal: bruddet kan ikke lukkes, varselet kan", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => h.querySelector("#jo-l-valg"));
  const valg = [...h.querySelector("#jo-l-valg").options]
    .map((o) => o.textContent);
  assert.equal(valg.some((v) =>
    v.startsWith(t("ui.journal.funn_frist_passert"))), false,
  "et brudd kunne lukkes for hånd");
  assert.ok(valg.some((v) =>
    v.startsWith(t("ui.journal.funn_frist_naer"))));
});

test("journal: hver funnrad sier om den kan lukkes", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => tabell(h, "ui.journal.funn.tittel"));
  const rader = [...tabell(h, "ui.journal.funn.tittel")
    .querySelectorAll("tbody tr")];
  const brudd = rader.find((r) => r.querySelector("th").textContent
    === t("ui.journal.funn_frist_passert"));
  assert.ok(brudd.textContent.includes(t("ui.journal.funn.sveipens")));
  const varsel = rader.find((r) => r.querySelector("th").textContent
    === t("ui.journal.funn_frist_naer"));
  assert.ok(varsel.textContent
    .includes(t("ui.journal.funn.kan_lukkes")));
});

// ---------------------------------------------------------------------
// SKJEMAENE, SCOPE, TABELLER, TEKST OG AXE
// ---------------------------------------------------------------------

test("journal: postskjemaet tilbyr bare gjeldende kildeversjoner",
     async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => h.querySelector("#jo-p-kilde"));
  const verdier = [...h.querySelector("#jo-p-kilde").options]
    .map((o) => o.value).filter(Boolean);
  assert.deepEqual(verdier, [K1], "en avviklet versjon kunne velges");
  // …men den avviklede STÅR i registeret. Det er BRUKEN som er
  // stengt, ikke minnet.
  assert.ok(tabell(h, "ui.journal.kilder.tittel")
    .textContent.includes("2019"));
});

test("journal: formålsfeltet krever en skrevet begrunnelse", () => {
  // Et formål man kan velge fra en liste er et formål ingen har tenkt
  // gjennom. Feltet er en `textarea` med minstelengde, ikke en select.
  const kilde = readFileSync(join(ROT, "platform", "core", "ui",
    "static", "js", "flater", "journal.js"), "utf-8");
  assert.match(kilde, /id: "jo-a-formaal"[\s\S]{0,200}minlength: "16"/);
  assert.match(kilde, /el\("textarea", \{ id: "jo-a-formaal"/);
});

test("journal: uten gyldig kilde eller sak finnes ingen postknapp",
     async () => {
  const h = nyHoved();
  SVAR = { ...fullSvar(),
    "/v1/journal": { ...BILDE, kilder: [KILDER[1]],
      sammendrag: { ...BILDE.sammendrag, gyldige: 0 } } };
  visJournal(h, ctx());
  assert.ok(await vent(() =>
    [...h.querySelectorAll("[role='alert']")].some((e) =>
      e.textContent === t("ui.journal.post.ingen_gyldige"))),
  "varselet om manglende kilde kom aldri");
  assert.equal([...h.querySelectorAll("button")].some(
    (b) => b.textContent === t("ui.journal.knapp.registrer_post")),
  false);
});

test("journal: grensene kommer fra svaret, aldri fra flaten", async () => {
  const h = nyHoved();
  SVAR = { ...fullSvar(),
    "/v1/journal": { ...BILDE,
      krav: { ...BILDE.krav, sletteplan_maks_dogn: 90 } } };
  visJournal(h, ctx());
  await vent(() => h.querySelector("#jo-k-maks"));
  assert.equal(h.querySelector("#jo-k-maks").value, "90");
  const h2 = nyHoved();
  SVAR = { "/v1/journal": TOMT,
           "/v1/journal/funn": { request_id: "r-0", funn: [] } };
  visJournal(h2, ctx());
  await vent(() => h2.querySelector("#jo-k-maks"));
  assert.equal(h2.querySelector("#jo-k-maks").value, "");
});

test("journal: en lesende økt ser fristene, men ingen skriveveier",
     async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx(["okonomi:read"]));
  await vent(() => tabell(h, "ui.journal.poster.tittel"));
  assert.equal(h.querySelectorAll("form").length, 0);
  assert.equal(KALL.some((k) => k.metode === "POST"), false);
  // …men fristene STÅR der.
  assert.ok(tabell(h, "ui.journal.poster.tittel")
    .textContent.includes("2026-08-26"));
});

test("journal: hver tabell er en ekte tabell", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => tabeller(h).length >= 4);
  for (const tb of tabeller(h)) {
    assert.ok(tb.querySelector("caption"));
    assert.ok(tb.querySelectorAll("thead th[scope='col']").length > 0);
    for (const rad of tb.querySelectorAll("tbody tr")) {
      assert.ok(rad.querySelector("th[scope='row']"));
    }
  }
});

test("journal: ingen hardkodet tekst", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => tabeller(h).length >= 4);
  assert.equal(h.textContent.includes("ui.journal."), false);
  assert.equal(h.textContent.includes("{"), false);
});

test("journal: null alvorlige axe-brudd på oversikten", async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => tabeller(h).length >= 4);
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("journal: null alvorlige axe-brudd med personpanelet åpent",
     async () => {
  const h = nyHoved(); SVAR = fullSvar();
  visJournal(h, ctx());
  await vent(() => tabell(h, "ui.journal.poster.tittel"));
  [...tabell(h, "ui.journal.poster.tittel")
    .querySelectorAll("tbody tr")][0].querySelector("button").click();
  await vent(() => tabell(h, "ui.journal.personer.tittel"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("journal: null alvorlige axe-brudd på et tomt register",
     async () => {
  const h = nyHoved();
  SVAR = { "/v1/journal": TOMT,
           "/v1/journal/funn": { request_id: "r-0", funn: [] } };
  visJournal(h, ctx());
  await vent(() => [...h.querySelectorAll("[role='alert']")].some(
    (e) => e.textContent === t("ui.journal.kilder.ingen")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("journal: tabellene står alene uten brudd", async () => {
  for (const node of [kildetabell(KILDER, () => {}),
                      saktabell(SAKER),
                      posttabell(POSTER, () => {}),
                      persontabell(PERSONER.personer, () => {})]) {
    const brudd = await alvorligeBrudd(node, { fragment: true });
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  }
});

test("journal: listene er de samme som API-ets", () => {
  assert.deepEqual([...FORMATER], ["noark5", "einnsyn",
    "kommunal_web", "annet"]);
  assert.deepEqual([...GRUNNLAG], ["berettiget_interesse", "avtale",
    "rettslig_forpliktelse", "samtykke"]);
  assert.deepEqual([...ROLLER], ["avsender", "mottaker", "part",
    "omtalt"]);
  const api = readFileSync(join(ROT, "platform", "core", "api",
    "postjournal.py"), "utf-8");
  for (const f of [...FORMATER, ...GRUNNLAG, ...ROLLER]) {
    assert.ok(api.includes(`"${f}"`), f);
  }
});
