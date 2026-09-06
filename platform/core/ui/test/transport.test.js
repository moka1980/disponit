// M-28 transportflaten (139) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
//   ROLLBACK.
//
// Bilen kjører uansett hva basen sier. Derfor måler portene her:
//
//   * at «BESTILLINGER: 0» står i sammendraget, ALLTID.
//   * at det ikke finnes en «bestill»- eller «ombook»-knapp.
//   * at NAVNET STÅR VED SIDEN AV FAREKLASSEN — en fareklasse uten et
//     menneske bak er en påstand ingen svarer for.
//   * at kolliskjemaet IKKE har et beskrivelses- eller varekodefelt:
//     det finnes ikke noe å utlede klassen AV.
//   * at planskjemaet ikke har et landfelt: mottakerlandet leses fra
//     adressen.
//   * at en plan vises med LANDPAKKEVERSJONEN sin.
//   * at et farlig kolli er et varsel.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  dato, FAREKLASSER, faretekst, forslagsrader, funnrader, iDagLokal,
  kollirader, kvitteringstekst, mal, sammendrag, vekt, visTransport,
} from "../static/js/flater/transport.js";

settI18nForTest(NB, "nb");

const K1 = "aaaaaaaa-1111-1111-1111-111111111111";
const K2 = "aaaaaaaa-2222-2222-2222-222222222222";
const F1 = "bbbbbbbb-1111-1111-1111-111111111111";
const F2 = "bbbbbbbb-2222-2222-2222-222222222222";
const U1 = "eeeeeeee-1111-1111-1111-111111111111";
const U2 = "eeeeeeee-2222-2222-2222-222222222222";

const KOLLI = [
  // FARLIG GODS, MED NAVNET PÅ DEN SOM SÅ PÅ DET.
  { kolli_id: K1, referanse: "pk-1", vekt_gram: 25_000,
    lengde_mm: 600, bredde_mm: 400, hoyde_mm: 300,
    fareklasse: "klasse_3_brannfarlige_vaesker", farlig: true,
    fareklasse_oppgitt_av: "u-lagermedarbeider",
    har_apent_forslag: true, registrert: "2026-09-01T08:00:00+00:00" },
  // UFARLIG, OG UTEN PLAN.
  { kolli_id: K2, referanse: "pk-2", vekt_gram: 800,
    lengde_mm: 200, bredde_mm: 150, hoyde_mm: 100,
    fareklasse: "ingen", farlig: false,
    fareklasse_oppgitt_av: "u-kari", har_apent_forslag: false,
    registrert: "2026-09-02T09:00:00+00:00" },
];

const FORSLAG = [
  { forslag_id: F1, kolli_id: K1, kolliref: "pk-1",
    mottakerland: "SE", avsenderland: "NO", landpakke_regelversjon: 1,
    fareklasse: "klasse_3_brannfarlige_vaesker", farlig: true,
    vekt_gram: 25_000, over_kontrollgrense: true, status: "apen",
    begrunnelse: "raskeste rute innen SLA",
    foreslatt_ts: "2026-09-01T10:00:00+00:00", foreslatt_av: "u-ola" },
  // EN VRAKET PLAN. Den står, fordi sletting ville fjernet beviset.
  { forslag_id: F2, kolli_id: K2, kolliref: "pk-2",
    mottakerland: "DK", avsenderland: "NO", landpakke_regelversjon: 1,
    fareklasse: "ingen", farlig: false, vekt_gram: 800,
    over_kontrollgrense: false, status: "forkastet",
    begrunnelse: "kunden avbestilte",
    foreslatt_ts: "2026-08-20T10:00:00+00:00", foreslatt_av: "u-ola" },
];

const FUNN = [
  { funn_id: U1, funntype: "land_uten_pakke", referanse: "DK",
    detalj: "det finnes planer til DK, og ingen landpakke gjelder",
    sveipens: true, forst_sett: "2026-09-06T04:00:00+00:00" },
  { funn_id: U2, funntype: "tungt_kolli_ukontrollert", referanse: F1,
    detalj: "kolliet «pk-1» paa 25000 gram er over kontrollgrensen",
    sveipens: false, forst_sett: "2026-09-06T04:00:00+00:00" },
];

const BILDE = {
  request_id: "r-t",
  sammendrag: {
    kolli: 2, farlige_kolli: 1, apne_forslag: 1, forkastede: 1,
    land_i_bruk: 2, bestillinger: 0, apne_funn: 2, har_krav: true,
    avsenderland: "NO", maks_kolli_gram: 50_000,
    manuell_kontroll_over_gram: 20_000, forslagsfrist_dogn: 14,
    kravversjon: 1,
  },
  kolli: KOLLI, forslag: FORSLAG, funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    kolli: 0, farlige_kolli: 0, apne_forslag: 0, forkastede: 0,
    land_i_bruk: 0, bestillinger: 0, apne_funn: 0, har_krav: false,
    avsenderland: null, maks_kolli_gram: null,
    manuell_kontroll_over_gram: null, forslagsfrist_dogn: null,
    kravversjon: null,
  },
  kolli: [], forslag: [], funn: [],
};

let SVAR;
let SISTE;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method === "POST") {
    SISTE = { sti, kropp: opts.body ? JSON.parse(opts.body) : null };
    return { ok: true, status: 200,
             json: async () => ({ ok: true, mottakerland: "SE",
                                  landpakke_regelversjon: 1,
                                  fareklasse: "klasse_3_brannfarlige_vaesker",
                                  farlig: true, krever_kontroll: true }) };
  }
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404,
             json: async () => ({ feil: "ikke_funnet" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(scopes = ["okonomi:read", "bestilling:opprett"]) {
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
  SVAR = { "/v1/transport": BILDE };
  SISTE = null;
  return m;
}


// =====================================================================
// DET MODULEN IKKE GJØR, SKAL SES.
// =====================================================================

test("sammendraget sier alltid at ingenting er bestilt", () => {
  // TALLET ER IKKE EN TELLING. Det er en påstand om at kolonnen ikke
  // finnes: `transportforslag` har ingen `bestilt_ts`.
  const p = sammendrag(BILDE.sammendrag);
  assert.ok(p.textContent.includes(t("ui.transport.ingen_bestilling")
    .replace("{n}", "0")));
  const tom = sammendrag(TOMT.sammendrag);
  assert.ok(tom.textContent.includes(t("ui.transport.ingen_bestilling")
    .replace("{n}", "0")));
});

test("flaten har ingen bestill- eller ombookknapp", async () => {
  const h = nyHoved();
  await visTransport(h, ctx());
  await vent(() => h.querySelector("#tr-maks"));
  const knapper = [...h.querySelectorAll("button")]
    .map((b) => b.textContent).sort();
  assert.deepEqual(knapper, [
    t("ui.transport.forkast"),
    t("ui.transport.forkast_bekreft"),
    t("ui.transport.lagre_kolli"),
    t("ui.transport.lagre_krav"),
    t("ui.transport.lukk_bekreft"),
    t("ui.transport.lukk_funn"),
    t("ui.transport.plan_knapp"),
  ].sort());
});

test("kolliskjemaet har ingenting å utlede en fareklasse av", async () => {
  // EN GAL PÅSTAND OM FARLIG GODS ER EN BRANN I EN LASTEBIL.
  //
  // Skjemaet tar ikke imot en produktbeskrivelse, en varekode eller en
  // HS-kode. `fareklasse_utledet_av_maskin` er umulig fordi det ikke
  // finnes noe å utlede klassen AV — ikke fordi noen lot være.
  const h = nyHoved();
  await visTransport(h, ctx());
  await vent(() => h.querySelector("#tr-fareklasse"));
  const skjema = h.querySelector("#tr-fareklasse").closest("form");
  const felter = [...skjema.querySelectorAll("input, select, textarea")]
    .map((x) => x.id).sort();
  assert.deepEqual(felter, ["tr-bredde", "tr-fareklasse", "tr-hoyde",
                            "tr-lengde", "tr-oppgittav", "tr-ref",
                            "tr-vekt"]);
  // …OG `fareklasse_oppgitt_av` ER PÅKREVD.
  assert.ok(h.querySelector("#tr-oppgittav").required);
});

test("fareklassesettet er ADRs ni pluss ingen, uten en annet-verdi", () => {
  // Settet er den internasjonale standarden og er komplett. En
  // `annet` ville latt en pakke ingen visste hva var få lov til å
  // reise — 116s `klassifisering_utenfor_lukket_sett`, anvendt her.
  assert.equal(FAREKLASSER.length, 10);
  assert.equal(FAREKLASSER[0], "ingen");
  for (const apen of ["annet", "andre", "ukjent", "custom"]) {
    assert.ok(!FAREKLASSER.includes(apen), apen);
  }
  // …OG HVER AV DEM HAR EN TEKST.
  for (const k of FAREKLASSER) {
    const tekst = faretekst({ fareklasse: k });
    assert.ok(!tekst.includes(t("ui.transport.fare_ukjent")), k);
  }
});

test("planskjemaet har ikke et landfelt", async () => {
  // MOTTAKERLANDET LESES FRA ADRESSEN. Et felt her ville gjort
  // modulen til en planlegger som planlegger mot det den får beskjed
  // om — og en plan til feil land er en pakke som havner der.
  const h = nyHoved();
  await visTransport(h, ctx());
  await vent(() => h.querySelector("#tr-plankolli"));
  const skjema = h.querySelector("#tr-plankolli").closest("form");
  const felter = [...skjema.querySelectorAll("input, select, textarea")]
    .map((x) => x.id).sort();
  assert.deepEqual(felter, ["tr-planadresse", "tr-plangrunn",
                            "tr-plankolli"]);
});

test("planen sender en adresseversjon, ikke et land", async () => {
  const h = nyHoved();
  await visTransport(h, ctx());
  await vent(() => h.querySelector("#tr-planadresse"));
  h.querySelector("#tr-planadresse").value = K1;
  h.querySelector("#tr-plangrunn").value = "raskeste rute";
  const skjema = h.querySelector("#tr-plankolli").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true,
                                                    bubbles: true }));
  await vent(() => SISTE && SISTE.sti === "/v1/transport/forslag/ny");
  assert.ok(SISTE, "planen ble aldri sendt");
  assert.equal(SISTE.kropp.adresseversjon_id, K1);
  for (const forbudt of ["mottakerland", "land", "fareklasse",
                         "landpakke_regelversjon", "transportor"]) {
    assert.ok(!(forbudt in SISTE.kropp), `kroppen bar ${forbudt}`);
  }
});


// =====================================================================
// HVEM SOM SA AT PAKKEN VAR TRYGG.
// =====================================================================

test("fareklassen vises aldri uten navnet som oppga den", () => {
  // «Klasse 3» sier hva pakken er. «Klasse 3 · u-lagermedarbeider»
  // sier hvem som så på den — og det er den som svarer hvis det tar
  // fyr.
  const tekst = faretekst(KOLLI[0]);
  assert.ok(tekst.includes("u-lagermedarbeider"));
  assert.ok(tekst.includes(t("ui.transport.fare_3")));
  // …OG UTEN NAVN FALLER DEN TILBAKE PÅ BARE KLASSEN, framfor å vise
  // «undefined».
  assert.equal(faretekst({ fareklasse: "ingen" }),
               t("ui.transport.fare_ingen"));
  assert.equal(faretekst(null), "–");
});

test("et farlig kolli er et varsel, et ufarlig er det ikke", () => {
  const rader = kollirader(BILDE);
  assert.ok(rader[0].querySelector("strong[role='alert']"));
  assert.equal(rader[1].querySelectorAll("strong[role='alert']").length,
               0);
});

test("navnet står i kollilisten, ikke bare i skjemaet", () => {
  const rader = kollirader(BILDE);
  assert.ok(rader[0].textContent.includes("u-lagermedarbeider"));
  assert.ok(rader[1].textContent.includes("u-kari"));
});


// =====================================================================
// PLANENE.
// =====================================================================

test("en plan vises med landpakkeversjonen sin", () => {
  // Uten den kan ingen etterprøve hvilke regler planen hvilte på når
  // reglene endres.
  const rader = forslagsrader(BILDE, { kanSkrive: false });
  assert.ok(rader[0].textContent.includes("NO"));
  assert.ok(rader[0].textContent.includes("SE"));
  assert.ok(rader[0].textContent.includes("v1"));
});

test("bare en åpen plan kan forkastes, og den vrakede står", () => {
  // Sletting ville fjernet beviset på at vi hadde planen (M-50s dom,
  // 124).
  const rader = forslagsrader(BILDE, { kanSkrive: true });
  const knapper = (r) => [...r.querySelectorAll("button")]
    .map((b) => b.textContent);
  assert.deepEqual(knapper(rader[0]), [t("ui.transport.forkast")]);
  assert.deepEqual(knapper(rader[1]), []);
  assert.ok(rader[1].textContent.includes(t("ui.transport.forkastet")));
});

test("en plan over kontrollgrensen er et varsel", () => {
  const rader = forslagsrader(BILDE, { kanSkrive: false });
  const varsler = [...rader[0].querySelectorAll("strong[role='alert']")]
    .map((x) => x.textContent);
  assert.ok(varsler.includes(t("ui.transport.krever_kontroll")));
});

test("en leser uten skrivescope får ingen forkastknapp", () => {
  const rader = forslagsrader(BILDE, { kanSkrive: false });
  assert.equal(rader[0].querySelectorAll("button").length, 0);
});


// =====================================================================
// MÅL, VEKT OG KVITTERING.
// =====================================================================

test("vekt deler bare i visningen, aldri i regningen", () => {
  // Heltall i basen: flyttall og fysiske mål hører ikke sammen når
  // noen skal laste en bil etter dem.
  assert.equal(vekt(800), "800 g");
  assert.equal(vekt(25_000), "25 kg");
  assert.equal(vekt(1_500), "1.5 kg");
  assert.equal(vekt(null), "–");
});

test("målene vises som én lesbar streng", () => {
  assert.equal(mal(KOLLI[0]), "600×400×300 mm");
  assert.equal(mal(null), "–");
});

test("plankvitteringen bærer landet, versjonen og klassen", () => {
  // Alle tre er ting kalleren IKKE oppga: hun ga et kolli og en
  // adresseversjon, og fikk landet, reglene og klassen tilbake.
  //
  // PORTEN MÅLER FUNKSJONEN, IKKE SKJERMEN — 138s lærdom: en port som
  // leser teksten ut av DOM-en etter to nettverksrunder måler timingen
  // framfor innholdet.
  const tekst = kvitteringstekst({
    mottakerland: "SE", landpakke_regelversjon: 2,
    fareklasse: "klasse_3_brannfarlige_vaesker" });
  assert.ok(tekst.includes("SE"));
  assert.ok(tekst.includes("v2"));
  assert.ok(tekst.includes(t("ui.transport.fare_3")));
  assert.equal(kvitteringstekst(null), "");
});

test("dato viser bare datoen", () => {
  assert.equal(dato("2026-09-01T08:00:00+00:00"), "2026-09-01");
  assert.equal(dato(null), "–");
});

test("iDagLokal gir brukerens dato, ikke UTC-datoen", () => {
  const natt = new Date(2026, 8, 6, 0, 30, 0);
  assert.equal(iDagLokal(natt), "2026-09-06");
});


// =====================================================================
// FUNNENE OG HELE FLATEN.
// =====================================================================

test("sveipens egne funn har ingen lukkeknapp", () => {
  const rader = funnrader(BILDE, { kanSkrive: true });
  assert.equal(rader[0].querySelectorAll("button").length, 0);
  assert.ok(rader[0].textContent
    .includes(t("ui.transport.lukkes_av_sveipen")));
  assert.equal(rader[1].querySelectorAll("button").length, 1);
});

test("de fire umulige funnene har et navn i flaten", () => {
  for (const type of ["kolli_bestilt_to_ganger",
                      "fareklasse_utledet_av_maskin",
                      "farlig_gods_uten_landregel",
                      "forslag_uten_validert_adresse"]) {
    const rader = funnrader(
      { funn: [{ funn_id: U1, funntype: type, referanse: "-",
                 detalj: "d", sveipens: false,
                 forst_sett: "2026-09-06T04:00:00+00:00" }] },
      { kanSkrive: false });
    assert.ok(!rader[0].textContent
      .includes(t("ui.transport.funn_ukjent")), `${type} mangler tekst`);
  }
});

test("en leser uten skrivescope får ingen skjemaer", async () => {
  const h = nyHoved();
  await visTransport(h, ctx(["okonomi:read"]));
  await vent(() => h.textContent.includes(t("ui.transport.kolli")));
  assert.equal(h.querySelector("#tr-maks"), null);
  assert.equal(h.querySelector("#tr-ref"), null);
});

test("uten grenser sier flaten hvorfor ingenting kan måles", async () => {
  const h = nyHoved();
  SVAR["/v1/transport"] = TOMT;
  await visTransport(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.transport.kolli_uten_krav")));
  assert.equal(h.querySelector("#tr-ref"), null);
  // …OG KRAVSKJEMAET STÅR, så veien videre er synlig.
  assert.ok(h.querySelector("#tr-avsenderland"));
});

test("planskjemaet sier fra når hvert kolli alt har en plan", async () => {
  const h = nyHoved();
  SVAR["/v1/transport"] = {
    ...BILDE,
    kolli: KOLLI.map((k) => ({ ...k, har_apent_forslag: true })),
  };
  await visTransport(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.transport.plan_uten_kolli")));
  assert.equal(h.querySelector("#tr-plankolli"), null);
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visTransport(h, ctx());
  await vent(() => h.querySelector("#tr-maks"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/transport"] = TOMT;
  await visTransport(h, ctx());
  await vent(() => h.textContent.includes(t("ui.transport.kolli_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});
