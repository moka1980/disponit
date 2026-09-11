// Kundeflaten (183–186) mot mocket API. Portene måler nøyaktig det eiers
// klage 11/9 handlet om: ett sted å legge inn kunder, et kort skjema, en
// tomtilstand som sier HVORDAN man starter, og en adresse som aldri
// vises hel.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visParter } from "../static/js/flater/parter.js";

settI18nForTest(NB, "nb");

const P1 = {
  part_id: "aa000000-0000-4000-8000-000000000001",
  part_ref: "K-001", navn: "Fjordlys Elektro AS", orgnummer: "912345678",
  parttype: "bedrift", aktiv: true, epost_maske: "fa**@fjordlys.example",
  telefon_maske: null, antall_kontakter: 1,
};
const P2 = {
  part_id: "aa000000-0000-4000-8000-000000000002",
  part_ref: "K-002", navn: "Nordlys Bygg AS", orgnummer: null,
  parttype: "bedrift", aktiv: false, epost_maske: null,
  telefon_maske: null, antall_kontakter: 0,
};

let SVAR;
const KALL = [];
globalThis.fetch = async (url, opts = {}) => {
  KALL.push({ url, metode: opts.method || "GET",
    kropp: opts.body ? JSON.parse(opts.body) : null });
  const sti = url.split("?")[0];
  const oppf = SVAR[sti];
  if (!oppf) {
    return { ok: false, status: 404, json: async () => ({ feil: "ikke_funnet" }) };
  }
  if (typeof oppf === "number") {
    return { ok: false, status: oppf,
      json: async () => ({ feil: SVAR._feilkode || "x" }) };
  }
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx(overstyr = {}) {
  const c = { sprak: "nb", scopes: ["part:read"], tenant: "acme",
    _ua: false, ...overstyr };
  c.paaUautorisert = () => { c._ua = true; };
  return c;
}

async function vent(pred, n = 80) {
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
  return m;
}

test("Kunder: lista er en tilgjengelig tabell, og axe er ren", async () => {
  SVAR = { "/v1/parter": { parter: [P1, P2], avkortet: false } };
  const h = nyHoved();
  visParter(h, ctx({ scopes: ["part:read", "part:administrer"] }));
  await vent(() => h.querySelector("table"));
  const tabell = h.querySelector("table");
  assert.ok(tabell.querySelector("caption"));
  assert.equal(tabell.querySelectorAll("thead th[scope=col]").length, 6);
  assert.equal(tabell.querySelectorAll("tbody th[scope=row]").length, 2);
  // STATUS SOM TEKST, aldri bare farge (WCAG 1.4.1).
  assert.ok(h.textContent.includes(t("ui.parter.status.aktiv")));
  assert.ok(h.textContent.includes(t("ui.parter.status.avviklet")));
  // «Ingen kontakt» er en TILSTAND som sies, ikke en tom celle.
  assert.ok(h.textContent.includes(t("ui.parter.uten_kontakt")));
  const brudd = await alvorligeBrudd(h);
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Kunder: tomtilstanden sier HVORDAN man starter, ikke bare at det er tomt",
  async () => {
    // EIERS KLAGE, MÅLT. 131 av husets 234 tomtekster sier bare at det
    // ikke er noe der. En bruker som åpner en tom flate skal få vite hva
    // hun gjør nå — og skjemaet skal stå der, ikke bak en knapp til.
    SVAR = { "/v1/parter": { parter: [], avkortet: false } };
    const h = nyHoved();
    visParter(h, ctx({ scopes: ["part:read", "part:administrer"] }));
    await vent(() => h.querySelector(".tilstand.tom"));
    const tom = h.querySelector(".tilstand.tom");
    assert.ok(tom.textContent.includes(t("ui.parter.tom_tittel")));
    assert.ok(tom.textContent.includes(t("ui.parter.tom_tekst")));
    assert.ok(t("ui.parter.tom_tekst").split(" ").length > 10,
      "tomteksten er for kort til å forklare noe");
    // Skjemaet står der ALLEREDE — ingen «Ny kunde»-knapp foran.
    assert.ok(h.querySelector("#part-ref"));
    assert.ok(h.querySelector("#part-navn"));
    // TRE FELTER. Medianen i huset er tolv, og det var halve klagen.
    assert.equal(h.querySelectorAll("form.kv-skjema input, form.kv-skjema select")
      .length, 3);
    // En LESER får en annen tomtekst: hun skal ikke lete etter et skjema
    // hun ikke har lov til å bruke.
    const h2 = nyHoved();
    visParter(h2, ctx());
    await vent(() => h2.querySelector(".tilstand.tom"));
    assert.ok(h2.textContent.includes(t("ui.parter.tom_tekst_leser")));
    assert.equal(h2.querySelector("#part-ref"), null);
  });

test("Kunder: en kunde legges inn med tre felter og lista tegnes på nytt",
  async () => {
    SVAR = { "/v1/parter": { parter: [], avkortet: false } };
    const h = nyHoved();
    visParter(h, ctx({ scopes: ["part:read", "part:administrer"] }));
    await vent(() => h.querySelector("#part-ref"));
    h.querySelector("#part-ref").value = "K-009";
    h.querySelector("#part-navn").value = "Fjordlys Elektro AS";
    h.querySelector("#part-orgnr").value = "912345678";
    KALL.length = 0;
    SVAR["/v1/parter"] = { parter: [P1], avkortet: false };
    h.querySelector("form.kv-skjema button[type=submit]").click();
    await vent(() => KALL.some((k) => k.metode === "POST"));
    const post = KALL.find((k) => k.metode === "POST");
    assert.equal(post.url, "/v1/parter");
    assert.deepEqual(post.kropp, { part_ref: "K-009",
      navn: "Fjordlys Elektro AS", orgnummer: "912345678" });
    await vent(() => h.querySelector("table"));
    assert.ok(h.textContent.includes("Fjordlys Elektro AS"));
  });

test("Kunder: et tomt felt stopper FØR kallet, og feilen står ved skjemaet",
  async () => {
    SVAR = { "/v1/parter": { parter: [], avkortet: false } };
    const h = nyHoved();
    visParter(h, ctx({ scopes: ["part:read", "part:administrer"] }));
    await vent(() => h.querySelector("#part-ref"));
    KALL.length = 0;
    h.querySelector("#part-navn").value = "Bare navn";
    h.querySelector("form.kv-skjema button[type=submit]").click();
    await vent(() => h.querySelector("[role=alert]").textContent);
    assert.equal(KALL.filter((k) => k.metode === "POST").length, 0,
      "flaten kalte serveren med et tomt felt");
    const varsel = h.querySelector("form.kv-skjema [role=alert]");
    assert.ok(varsel && varsel.textContent.includes(t("ui.parter.feil.mangler")));
    // §7-KONTRAKTEN: feltet MERKES, og fokus flyttes dit — ikke bare en
    // setning et sted på siden.
    const ref = h.querySelector("#part-ref");
    assert.equal(ref.getAttribute("aria-invalid"), "true");
    assert.equal(ref.getAttribute("aria-errormessage"), varsel.id);
    assert.equal(h.ownerDocument.activeElement, ref);
    // DEN OPPRINNELIGE FELLEN, festet: med bare `required` blokkerer
    // nettleseren innsendingen selv, «submit» fyres aldri, og flatens
    // egen oversatte melding er uoppnåelig — brukeren får nettleserens
    // boble på nettleserens språk i stedet. Målt ved å kjøre.
    assert.ok(h.querySelector("form.kv-skjema").hasAttribute("novalidate"),
      "uten novalidate vil nettleseren spise submit-hendelsen");
    // ORGANISASJONSNUMMERET PEKER PÅ SITT EGET FELT, ikke på skjemaet
    // som helhet: serveren avviser det uansett, men da må brukeren
    // gjette hvilket av tre felter hun skrev feil.
    KALL.length = 0;
    h.querySelector("#part-ref").value = "K-011";
    h.querySelector("#part-orgnr").value = "12345";
    h.querySelector("form.kv-skjema button[type=submit]").click();
    await vent(() => h.querySelector("#part-orgnr")
      .getAttribute("aria-invalid") === "true");
    assert.equal(KALL.filter((k) => k.metode === "POST").length, 0);
    assert.equal(h.ownerDocument.activeElement,
                 h.querySelector("#part-orgnr"));
    // Mellomrom er lov — mennesker skriver «912 345 678».
    h.querySelector("#part-orgnr").value = "912 345 678";
    h.querySelector("form.kv-skjema button[type=submit]").click();
    await vent(() => KALL.some((k) => k.metode === "POST"));
    assert.equal(KALL.find((k) => k.metode === "POST").kropp.orgnummer,
                 "912345678");
    h.querySelector("#part-orgnr").value = "";

    // POSITIV KONTROLL: når feltet FYLLES, slipper samme skjema gjennom.
    // Uten den ville porten vært grønn av et skjema som aldri sender.
    KALL.length = 0;
    h.querySelector("#part-ref").value = "K-010";
    h.querySelector("form.kv-skjema button[type=submit]").click();
    await vent(() => KALL.some((k) => k.metode === "POST"));
    assert.equal(KALL.find((k) => k.metode === "POST").kropp.part_ref,
                 "K-010");
  });

test("Kunder: adressen sendes én vei og kommer aldri tilbake hel",
  async () => {
    SVAR = { "/v1/parter": { parter: [P1], avkortet: false },
             [`/v1/parter/${P1.part_id}/kontakt`]:
               { kontakt_id: "k1", maske: "ny**@fjordlys.example" } };
    const h = nyHoved();
    visParter(h, ctx({ scopes: ["part:read", "part:administrer"] }));
    await vent(() => h.querySelector("table"));
    [...h.querySelectorAll("tbody button")].find(
      (b) => b.textContent === t("ui.parter.knapp.kontakt")).click();
    await vent(() => h.querySelector("#kontakt-verdi"));
    // Brukeren får VITE at adressen krypteres, før hun skriver den.
    assert.ok(h.textContent.includes(t("ui.parter.kontakt_note")));
    h.querySelector("#kontakt-verdi").value = "ny.adresse@fjordlys.example";
    KALL.length = 0;
    h.querySelector(".skjemaboks button[type=submit]").click();
    await vent(() => KALL.some((k) => k.metode === "POST"));
    const post = KALL.find((k) => k.metode === "POST");
    assert.equal(post.url, `/v1/parter/${P1.part_id}/kontakt`);
    assert.deepEqual(post.kropp,
      { kanal: "epost", verdi: "ny.adresse@fjordlys.example" });
    // ...og flaten bærer aldri hele adressen etterpå — hverken i tekst
    // ELLER i et felt. `textContent` ser ikke inn i `input.value`
    // (CodeRabbit), så en assertion på tekst alene ville vært grønn selv
    // om adressen sto igjen i skjemaet.
    // VENT PÅ AT FELTET ER TØMT, ikke på at tabellen finnes — den fantes
    // fra første tegning, så ventingen var en no-op og porten målte
    // tilstanden FØR lagringen var ferdig.
    await vent(() => {
      const f = h.querySelector("#kontakt-verdi");
      return !f || f.value === "";
    });
    assert.ok(!h.textContent.includes("ny.adresse@fjordlys.example"));
    const felter = [...h.querySelectorAll("input, textarea, select")];
    assert.ok(felter.length > 0, "porten måler ingenting uten felter");
    for (const f of felter) {
      assert.ok(!String(f.value).includes("ny.adresse@fjordlys.example"),
        `adressen sto igjen i ${f.id || f.tagName}`);
    }
  });

test("Kunder: en leser ser lista, men ingen knapper som ville gitt 403",
  async () => {
    // LÆRDOMMEN FRA M-6 (#483), brukt før den rakk å bite: én nøkkel for
    // både å se og å endre ga der enten skjulte funksjoner eller knapper
    // serveren nektet. Her er de to skilt fra dag én, og porten måler
    // BEGGE retninger.
    SVAR = { "/v1/parter": { parter: [P1], avkortet: false } };
    const h = nyHoved();
    visParter(h, ctx());                       // bare part:read
    await vent(() => h.querySelector("table"));
    assert.ok(h.textContent.includes("Fjordlys Elektro AS"));
    assert.equal(h.querySelector("#part-ref"), null, "leseren fikk skjemaet");
    assert.equal([...h.querySelectorAll("button")].filter(
      (b) => b.textContent === t("ui.parter.knapp.kontakt")
          || b.textContent === t("ui.parter.knapp.avvikle")).length, 0);
    // ...og med skrivescopet finnes de.
    const h2 = nyHoved();
    visParter(h2, ctx({ scopes: ["part:read", "part:administrer"] }));
    await vent(() => h2.querySelector("table"));
    assert.ok([...h2.querySelectorAll("tbody button")].some(
      (b) => b.textContent === t("ui.parter.knapp.kontakt")));
  });

test("Kunder: en avviklet kunde har ingen handlinger, men står i lista",
  async () => {
    SVAR = { "/v1/parter": { parter: [P2], avkortet: false } };
    const h = nyHoved();
    visParter(h, ctx({ scopes: ["part:read", "part:administrer"] }));
    await vent(() => h.querySelector("table"));
    assert.ok(h.textContent.includes("Nordlys Bygg AS"),
      "den avviklede kunden forsvant fra lista");
    assert.equal(h.querySelectorAll("tbody button").length, 0);
  });

test("Kunder: søket spør REGISTERET, og tomt søk sier at søket ikke traff",
  async () => {
    SVAR = { "/v1/parter": { parter: [P1], avkortet: false } };
    const h = nyHoved();
    visParter(h, ctx());
    await vent(() => h.querySelector("#part-sok"));
    KALL.length = 0;
    SVAR["/v1/parter"] = { parter: [], avkortet: false };
    h.querySelector("#part-sok").value = "finnes-ikke";
    h.querySelector("form.kv-skjema button[type=submit]").click();
    await vent(() => KALL.length > 0);
    // FILTRERINGEN SKJER I REGISTERET. En klient som filtrerte selv ville
    // sagt «ingen treff» om en kunde rett bak sidegrensen.
    assert.ok(KALL[0].url.includes("sok=finnes-ikke"), KALL[0].url);
    await vent(() => h.querySelector(".tilstand.tom"));
    assert.ok(h.textContent.includes(t("ui.parter.tom_sok_tittel")));
    assert.ok(h.textContent.includes(t("ui.parter.tom_sok_tekst")));
  });
