// M-32 skatteflaten (138) — flateporten (jsdom + axe).
//
// PORTENE MÅLER KLYNGENS DOM, IKKE BARE AT SKJERMEN TEGNES.
//
//   EN HANDLING MED VIRKNING I DEN VIRKELIGE VERDEN ANGRES IKKE AV EN
//   ROLLBACK.
//
// En innberettet mva-oppgave er hos skattemyndigheten. En rollback
// gjør den ikke usendt; den gjør bare at vi ikke lenger vet hva vi
// sendte. Derfor måler portene her:
//
//   * at «INNBERETNINGER: 0» står i sammendraget, ALLTID.
//   * at det ikke finnes en «innberett»- eller «endre sats»-knapp.
//   * at SATSEN ALDRI VISES UTEN REGELVERSJONEN SIN.
//   * at BEGGE LANDENE vises — v1s regel er kjøperens land, og en
//     flate som bare viste svaret ville gjort en forenkling til en
//     sannhet.
//   * at beregningsskjemaet ikke har et promillefelt: satsen er
//     landets.
//   * at en landpakke uten satser er et VARSEL.
//   * at landregisteret er synlig, så den som lurer på hvorfor en
//     beregning stoppet ser det selv.
//
// Ingen delt fixture (m16-formen): hver test bygger sin egen skjerm.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import {
  belop, dato, funnrader, iDagLokal, kvitteringstekst, landrader,
  sammendrag, satstekst, visSkatt, vurderingsrader,
} from "../static/js/flater/skatt.js";

settI18nForTest(NB, "nb");

const V1 = "aaaaaaaa-1111-1111-1111-111111111111";
const V2 = "aaaaaaaa-2222-2222-2222-222222222222";
const F1 = "eeeeeeee-1111-1111-1111-111111111111";
const F2 = "eeeeeeee-2222-2222-2222-222222222222";

const LAND = [
  { landkode: "NO", regelversjon: 1, valuta: "NOK", desimaler: 2,
    avrundingsregel: "halv_opp", dokumentformat: "EHF 3.0",
    gyldig_fra: "2024-01-01", gyldig_til: null, gjelder: true,
    satser: 4, signert_av: "plattform:138" },
  { landkode: "SE", regelversjon: 1, valuta: "SEK", desimaler: 2,
    avrundingsregel: "halv_opp", dokumentformat: "Peppol BIS 3.0",
    gyldig_fra: "2024-01-01", gyldig_til: null, gjelder: true,
    satser: 4, signert_av: "plattform:138" },
  // EN PAKKE UTEN SATSER. Den ville tilfredsstilt fremmednøkkelen og
  // forklart ingenting — og døra ville nektet hver beregning mot den.
  { landkode: "DK", regelversjon: 2, valuta: "DKK", desimaler: 2,
    avrundingsregel: "halv_ned", dokumentformat: "OIOUBL 2.1",
    gyldig_fra: "2026-01-01", gyldig_til: null, gjelder: true,
    satser: 0, signert_av: "plattform:138" },
  // …OG EN SOM HAR GÅTT UT.
  { landkode: "DK", regelversjon: 1, valuta: "DKK", desimaler: 2,
    avrundingsregel: "halv_opp", dokumentformat: "OIOUBL 2.1",
    gyldig_fra: "2024-01-01", gyldig_til: "2025-12-31", gjelder: false,
    satser: 2, signert_av: "plattform:138" },
];

const VURDERINGER = [
  // KJØPEREN ER SVENSK, SELGEREN NORSK. Jurisdiksjonen er kjøperens.
  { vurdering_id: V1, transaksjonsref: "tx-1", jurisdiksjon: "SE",
    kjoperland: "SE", selgerland: "NO", regelversjon: 1,
    satskode: "standard", promille: 250, belop_ore: 100_000,
    skatt_ore: 25_000, transaksjonsdato: "2026-09-01",
    over_kontrollgrense: true,
    beregnet_ts: "2026-09-01T10:00:00+00:00" },
  { vurdering_id: V2, transaksjonsref: "tx-2", jurisdiksjon: "NO",
    kjoperland: "NO", selgerland: "NO", regelversjon: 1,
    satskode: "redusert", promille: 150, belop_ore: 5_000,
    skatt_ore: 750, transaksjonsdato: "2026-08-15",
    over_kontrollgrense: false,
    beregnet_ts: "2026-08-15T09:00:00+00:00" },
];

const FUNN = [
  // SVEIPENS EGET — ingen kan lukke det.
  { funn_id: F1, funntype: "landpakke_uten_sats", referanse: "DK/2",
    detalj: "landpakken for DK versjon 2 har ingen satser",
    sveipens: true, forst_sett: "2026-09-06T04:00:00+00:00" },
  // …OG ETT ET MENNESKE KAN LUKKE.
  { funn_id: F2, funntype: "stor_vurdering_ukontrollert",
    referanse: V1, detalj: "transaksjon tx-1 er over kontrollgrensen",
    sveipens: false, forst_sett: "2026-09-06T04:00:00+00:00" },
];

const BILDE = {
  request_id: "r-t",
  sammendrag: {
    vurderinger: 2, land_i_bruk: 2, over_kontrollgrense: 1,
    skatt_ore: 25_750, innberetninger: 0, apne_funn: 2,
    har_krav: true, selgerland: "NO",
    manuell_kontroll_over_ore: 50_000, kontrollfrist_dogn: 14,
    kravversjon: 1,
  },
  vurderinger: VURDERINGER, land: LAND, funn: FUNN,
};

const TOMT = {
  request_id: "r-t",
  sammendrag: {
    vurderinger: 0, land_i_bruk: 0, over_kontrollgrense: 0,
    skatt_ore: 0, innberetninger: 0, apne_funn: 0, har_krav: false,
    selgerland: null, manuell_kontroll_over_ore: null,
    kontrollfrist_dogn: null, kravversjon: null,
  },
  vurderinger: [], land: LAND, funn: [],
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
        json: async () => ({ feil: "skatt_ulovlig_tilstand" }) };
    }
    return { ok: true, status: 200,
             json: async () => ({ ok: true, jurisdiksjon: "SE",
                                  regelversjon: 1, promille: 250,
                                  skatt_ore: 25_000, valuta: "SEK",
                                  krever_kontroll: true }) };
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
  SVAR = { "/v1/skatt": BILDE };
  SISTE = null;
  SVARSTATUS = 200;
  return m;
}


// =====================================================================
// DET MODULEN IKKE GJØR, SKAL SES.
// =====================================================================

test("sammendraget sier alltid at ingenting er innberettet", () => {
  // TALLET ER IKKE EN TELLING. Det er en påstand om at kolonnen ikke
  // finnes: ingen tabell i 138 har `innsendt_ts` eller `kvittering`.
  const p = sammendrag(BILDE.sammendrag);
  assert.ok(p.textContent.includes(t("ui.skatt.ingen_innberetning")
    .replace("{n}", "0")));
  const tom = sammendrag(TOMT.sammendrag);
  assert.ok(tom.textContent.includes(t("ui.skatt.ingen_innberetning")
    .replace("{n}", "0")));
});

test("flaten har ingen innberett- eller endre sats-knapp", async () => {
  const h = nyHoved();
  await visSkatt(h, ctx());
  await vent(() => h.querySelector("#s-grense"));
  const knapper = [...h.querySelectorAll("button")]
    .map((b) => b.textContent);
  assert.deepEqual(knapper.sort(),
    [t("ui.skatt.beregn_knapp"), t("ui.skatt.lagre_krav"),
     t("ui.skatt.lukk_bekreft"), t("ui.skatt.lukk_funn")].sort());
});

test("beregningsskjemaet har ikke et promillefelt", async () => {
  // SATSEN ER LANDETS. En promilleparameter ville gjort hele
  // landregisteret til pynt.
  const h = nyHoved();
  await visSkatt(h, ctx());
  await vent(() => h.querySelector("#s-satskode"));
  const skjema = h.querySelector("#s-satskode").closest("form");
  const felter = [...skjema.querySelectorAll("input, select, textarea")]
    .map((x) => x.id).sort();
  assert.deepEqual(felter, ["s-adresse", "s-belop", "s-dato", "s-ref",
                            "s-satskode"]);
});

test("beregningen sender en adresseversjon, ikke et land", async () => {
  // JURISDIKSJONEN LESES FRA ADRESSEN. En parameter for landet ville
  // gjort modulen til en kalkulator som regner på det den får beskjed
  // om.
  const h = nyHoved();
  await visSkatt(h, ctx());
  await vent(() => h.querySelector("#s-ref"));
  h.querySelector("#s-ref").value = "tx-9";
  h.querySelector("#s-adresse").value = V1;
  h.querySelector("#s-belop").value = "100000";
  const skjema = h.querySelector("#s-ref").closest("form");
  skjema.dispatchEvent(new window.Event("submit", { cancelable: true,
                                                    bubbles: true }));
  await vent(() => SISTE && SISTE.sti === "/v1/skatt/beregn");
  assert.ok(SISTE, "beregningen ble aldri sendt");
  assert.equal(SISTE.kropp.adresseversjon_id, V1);
  for (const forbudt of ["jurisdiksjon", "land", "kjoperland",
                         "promille", "sats"]) {
    assert.ok(!(forbudt in SISTE.kropp), `kroppen bar ${forbudt}`);
  }
});


// =====================================================================
// SATSEN, VERSJONEN OG BEGGE LANDENE.
// =====================================================================

test("en sats vises aldri uten regelversjonen sin", () => {
  // «25 %» sier ingenting om hvilken regel som ga den.
  // «25 % · SE v1» kan slås opp.
  const tekst = satstekst(VURDERINGER[0]);
  assert.ok(tekst.includes("25"));
  assert.ok(tekst.includes("SE"));
  assert.ok(tekst.includes("1"));
  assert.equal(satstekst({}), "–");
  // 15 % skal vises som 15, ikke 15.0.
  assert.ok(satstekst(VURDERINGER[1]).includes("15 %"));
});

test("begge landene vises, ikke bare jurisdiksjonen", () => {
  // v1s regel er at jurisdiksjonen er KJØPERENS land: riktig for
  // fjernsalg til forbruker i EØS, feil for flere andre tilfeller. En
  // flate som bare viste svaret ville gjort en forenkling til en
  // sannhet.
  const rader = vurderingsrader(BILDE);
  assert.ok(rader[0].textContent.includes("SE"));
  assert.ok(rader[0].textContent.includes("NO"));
});

test("beløpene vises i landets valuta og desimaler", () => {
  const rader = vurderingsrader(BILDE);
  // 100 000 øre i SEK med to desimaler = 1000,00 SEK.
  assert.ok(rader[0].textContent.includes("1000.00 SEK"));
  // 750 øre i NOK = 7,50 NOK.
  assert.ok(rader[1].textContent.includes("7.50 NOK"));
});

test("belop deler bare i visningen, aldri i regningen", () => {
  // Heltall hele veien: flyttall og skatt hører ikke sammen.
  assert.equal(belop(25_000, "NOK", 2), "250.00 NOK");
  assert.equal(belop(0, "NOK", 2), "0.00 NOK");
  // JPY har null desimaler.
  assert.equal(belop(1234, "JPY", 0), "1234 JPY");
  assert.equal(belop(null, "NOK", 2), "–");
});

test("en vurdering over kontrollgrensen er et varsel", () => {
  const rader = vurderingsrader(BILDE);
  assert.ok(rader[0].querySelector("strong[role='alert']"));
  assert.equal(rader[1].querySelectorAll("strong[role='alert']").length,
               0);
});


// =====================================================================
// LANDREGISTERET.
// =====================================================================

test("en landpakke uten satser er et varsel", () => {
  // Den ville tilfredsstilt fremmednøkkelen fra en vurdering og
  // forklart ingenting — og døra ville nektet hver beregning mot den,
  // uten at noen visste hvorfor.
  const rader = landrader(BILDE);
  const dk2 = rader[2];
  const varsel = dk2.querySelector("strong[role='alert']");
  assert.ok(varsel);
  assert.equal(varsel.textContent, t("ui.skatt.uten_satser"));
  // NO har fire satser og er ikke et varsel.
  assert.equal(rader[0].querySelectorAll("strong[role='alert']").length,
               0);
});

test("en utløpt landpakke sier når den gjaldt til", () => {
  const rader = landrader(BILDE);
  assert.ok(rader[3].textContent.includes("2025-12-31"));
});

test("avrundingsregelen vises, fordi den er landets", () => {
  const rader = landrader(BILDE);
  assert.ok(rader[0].textContent.includes(t("ui.skatt.avrund_opp")));
  assert.ok(rader[2].textContent.includes(t("ui.skatt.avrund_ned")));
});

test("landpakken viser hvem som signerte den", () => {
  // En landpakke ingen har satt navnet sitt på er ikke godkjent — den
  // er bare skrevet.
  const rader = landrader(BILDE);
  assert.ok(rader[0].textContent.includes("plattform:138"));
});


// =====================================================================
// FUNNENE OG HELE FLATEN.
// =====================================================================

test("sveipens egne funn har ingen lukkeknapp", () => {
  const rader = funnrader(BILDE, { kanSkrive: true });
  assert.equal(rader[0].querySelectorAll("button").length, 0);
  assert.ok(rader[0].textContent
    .includes(t("ui.skatt.lukkes_av_sveipen")));
  // …OG DET ANDRE HAR EN.
  assert.equal(rader[1].querySelectorAll("button").length, 1);
});

test("de fire umulige funnene har et navn i flaten", () => {
  // De står i det lukkede settet OG kan aldri reises. En flate som
  // ikke kunne NAVNGI dem ville vist «Ukjent funntype» den dagen noe
  // umulig skjedde.
  for (const type of ["transaksjon_uten_jurisdiksjon",
                      "sats_uten_regelversjon",
                      "sats_uten_komplett_landpakke",
                      "landpakke_endret_gjennom_dor"]) {
    const rader = funnrader(
      { funn: [{ funn_id: F1, funntype: type, referanse: "-",
                 detalj: "d", sveipens: false,
                 forst_sett: "2026-09-06T04:00:00+00:00" }] },
      { kanSkrive: false });
    assert.ok(!rader[0].textContent.includes(t("ui.skatt.funn_ukjent")),
      `${type} mangler en tekst`);
  }
});

test("dato viser bare datoen", () => {
  assert.equal(dato("2026-09-01T10:00:00+00:00"), "2026-09-01");
  assert.equal(dato(null), "–");
});

test("iDagLokal gir brukerens dato, ikke UTC-datoen", () => {
  // Norge ligger FORAN UTC, så mellom midnatt og 01/02 om natten gir
  // `toISOString()` GÅRSDAGEN — og en transaksjon datert «i dag»
  // ville blitt regnet mot gårsdagens landpakke.
  const natt = new Date(2026, 8, 6, 0, 30, 0);
  assert.equal(iDagLokal(natt), "2026-09-06");
});

test("en leser uten skrivescope får ingen skjemaer", async () => {
  const h = nyHoved();
  await visSkatt(h, ctx(["okonomi:read"]));
  await vent(() => h.textContent.includes(t("ui.skatt.vurderinger")));
  assert.equal(h.querySelector("#s-grense"), null);
  assert.equal(h.querySelector("#s-ref"), null);
  // …MEN LANDREGISTERET ER SYNLIG. Den som lurer på hvorfor en
  // beregning stoppet skal se det, også uten skriverett.
  assert.ok(h.textContent.includes(t("ui.skatt.land")));
});

test("uten grenser sier flaten hvorfor ingenting kan beregnes", async () => {
  const h = nyHoved();
  SVAR["/v1/skatt"] = TOMT;
  await visSkatt(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.skatt.beregn_uten_krav")));
  assert.equal(h.querySelector("#s-ref"), null);
  // …OG KRAVSKJEMAET STÅR, så veien videre er synlig.
  assert.ok(h.querySelector("#s-selgerland"));
});

test("kravskjemaet tilbyr bare land med en gjeldende pakke", async () => {
  // Uten selgerlandets pakke kan ingen si hva som er innenlands, og
  // «usikker jurisdiksjon» begynner allerede der.
  const h = nyHoved();
  await visSkatt(h, ctx());
  await vent(() => h.querySelector("#s-selgerland"));
  const valg = [...h.querySelectorAll("#s-selgerland option")]
    .map((o) => o.value);
  assert.deepEqual(valg, ["NO", "SE", "DK"]);
  // DK v1 gjelder ikke og skal ikke gi et duplikat.
  assert.equal(valg.filter((v) => v === "DK").length, 1);
});

test("flaten er ren for axe", async () => {
  const h = nyHoved();
  await visSkatt(h, ctx());
  await vent(() => h.querySelector("#s-grense"));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("den tomme flaten er ren for axe", async () => {
  const h = nyHoved();
  SVAR["/v1/skatt"] = TOMT;
  await visSkatt(h, ctx());
  await vent(() => h.textContent
    .includes(t("ui.skatt.vurderinger_tomt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});


test("beregningskvitteringen bruker landets desimaler, ikke to", () => {
  // DESIMALENE ER LANDETS. Her sto `2` hardkodet til CodeRabbit fant
  // det, og det ville undergravd nettopp den kolonnen landpakken har
  // for å bære dem: JPY har null, og 1234 yen ville stått som
  // «12.34 JPY».
  //
  // PORTEN MÅLER FUNKSJONEN, IKKE SKJERMEN. Første utgave leste
  // teksten ut av DOM-en etter to nettverksrunder og målte da
  // TIMINGEN framfor innholdet — den var grønn eller rød etter hvor
  // raskt maskinen var.
  const JP = { landkode: "JP", regelversjon: 1, valuta: "JPY",
               desimaler: 0, avrundingsregel: "mot_null",
               dokumentformat: "test", gyldig_fra: "2024-01-01",
               gyldig_til: null, gjelder: true, satser: 1,
               signert_av: "u-test" };
  const svar = { jurisdiksjon: "JP", regelversjon: 1, promille: 100,
                 skatt_ore: 1234, valuta: "JPY" };
  const tekst = kvitteringstekst(svar, [...LAND, JP]);
  assert.ok(tekst.includes("1234 JPY"),
    `kvitteringen delte paa 100 for et land uten desimaler: ${tekst}`);
  assert.ok(!tekst.includes("12.34"));
  // …OG DEN FINNER RIKTIG VERSJON. DK har to pakker i registeret; en
  // kvittering for v1 skal ikke plukke v2s avrunding.
  const dk = kvitteringstekst(
    { jurisdiksjon: "DK", regelversjon: 1, promille: 250,
      skatt_ore: 25_000, valuta: "DKK" }, LAND);
  assert.ok(dk.includes("250.00 DKK"), dk);
  assert.ok(dk.includes("DK v1"), dk);
  // ET UKJENT LAND FALLER TILBAKE PÅ TO DESIMALER framfor å kaste:
  // en kvittering som forsvinner er verre enn en med feil komma.
  const ukjent = kvitteringstekst(
    { jurisdiksjon: "ZZ", regelversjon: 9, promille: 100,
      skatt_ore: 100, valuta: "ZZZ" }, LAND);
  assert.ok(ukjent.includes("1.00 ZZZ"), ukjent);
  assert.equal(kvitteringstekst(null, LAND), "");
});
