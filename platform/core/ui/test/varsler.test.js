// Varselinnboksen — «noe venter på DEG», og valget om hvordan du vil høre det.
//
// Det som prøves her er det flaten LOVER: at teksten kommer fra mottakerens
// locale (ikke fra serveren), at et varsel har en vei til handlingen, at
// «kun portal» kan velges, og at ingenting av dette er utilgjengelig.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visVarsler } from "../static/js/flater/varsler.js";
import { lagRuter } from "../static/js/ruter.js";
import { el, sett } from "../static/js/dom.js";

settI18nForTest(NB, "nb");

const VARSEL = {
  id: 1, art: "attestering_venter", ressurs_type: "policyutkast",
  ressurs_id: "u-1", tekstnokkel: "varsel.attestering_venter",
  parametre: { policy_id: "faktura-no", runde: 1, risikoklasse: "UTVIDER",
               gjenstaar: 1 },
  opprettet: "2026-08-16T09:00:00+00:00", lest: false,
};

let SVAR, POSTET;
globalThis.fetch = async (url, opts) => {
  const sti = url.split("?")[0];
  if (opts && opts.method) {
    POSTET.push({ sti, body: opts.body ? JSON.parse(opts.body) : null });
    return { ok: true, status: 200, json: async () => ({ ok: true }) };
  }
  return { ok: true, status: 200, json: async () => SVAR[sti] };
};

function ctx(over = {}) {
  return { sprak: "nb", scopes: [], tenant: "acme",
    paaUautorisert: () => {}, ...over };
}
const vent = async (p, n = 100) => {
  for (let i = 0; i < n; i++) { if (p()) return true;
    await new Promise((r) => setTimeout(r, 0)); }
  return p();
};
function nyHoved() {
  const brett = nyttBrett();
  const m = document.createElement("main");
  m.id = "hovedinnhold"; m.tabIndex = -1;
  brett.append(m); return m;
}
const finn = (h, tekst) => [...h.querySelectorAll("button")]
  .find((b) => b.textContent.trim() === tekst);

test("Varsler: teksten bygges fra locale + parametre, ikke fra serveren",
  async () => {
    POSTET = [];
    SVAR = { "/v1/varsel": { varsler: [VARSEL], uleste: 1,
      kanal: "epost_og_portal" } };
    const h = nyHoved();
    visVarsler(h, ctx());
    await vent(() => h.querySelector(".varselrad"));
    const tekst = h.querySelector(".varseltekst").textContent;
    // Serveren sendte BARE en nøkkel og parametre. Kom teksten derfra, ville
    // varselet stått på avsenderens språk for alltid.
    assert.ok(tekst.includes("faktura-no"), "policy_id ble ikke satt inn");
    assert.ok(tekst.includes("UTVIDER"), "risikoklassen ble ikke satt inn");
    assert.ok(!tekst.includes("{policy_id}") && !tekst.includes("{gjenstaar}"),
      "plassholdere står igjen ufylte");
    assert.ok(!tekst.includes("varsel.attestering_venter"),
      "nøkkelen vises rå — teksten mangler i locale");
  });

test("Varsler: et ulest varsel er markert med MER enn farge", async () => {
  POSTET = [];
  SVAR = { "/v1/varsel": { varsler: [VARSEL], uleste: 1,
    kanal: "epost_og_portal" } };
  const h = nyHoved();
  visVarsler(h, ctx());
  await vent(() => h.querySelector(".varselrad"));
  // Farge alene bærer ikke betydning (WCAG 1.4.1). Klassen gir kantstripe OG
  // fet tekst; her sjekkes at markeringen finnes og skiller de to tilstandene.
  assert.ok(h.querySelector(".varsel-ulest"), "ulest er ikke markert");
  assert.ok(finn(h, t("ui.varsler.merk_lest")), "mangler «merk som lest»");
});

test("Varsler: tidspunktet er et lesbart klokkeslett, ikke «[object Object]»",
  async () => {
    POSTET = [];
    SVAR = { "/v1/varsel": { varsler: [VARSEL], uleste: 1,
      kanal: "epost_og_portal" } };
    const h = nyHoved();
    visVarsler(h, ctx());
    await vent(() => h.querySelector(".varselrad"));
    const tid = h.querySelector(".varselrad time");
    assert.ok(tid, "raden mangler <time>");
    // `Tidspunkt` tar ISO-STRENGEN. Fikk den et objekt rundt den, ble
    // formateringen ugyldig og fallbacken skrev objektet — synlig for
    // brukeren, og i `datetime` som en maskinlesbar løgn.
    assert.equal(tid.getAttribute("datetime"), VARSEL.opprettet,
      "datetime bærer ikke ISO-strengen");
    assert.ok(!tid.textContent.includes("object Object"),
      "tidspunktet ble rendret fra et objekt");
    assert.ok(/\d/.test(tid.textContent), "tidspunktet viser ingen tall");
  });

test("Varsler: «Gå til» merker lest OG navigerer — også hvis merkingen feiler",
  async () => {
    POSTET = [];
    SVAR = { "/v1/varsel": { varsler: [VARSEL], uleste: 1,
      kanal: "epost_og_portal" } };
    // La merkingen FEILE. Uten dette prøves aldri `.catch`, og testen ville
    // vært grønn også om en feilet merking blokkerte navigasjonen — akkurat
    // det den påstår at den utelukker.
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      if (opts && opts.method && url.includes("/lest")) {
        POSTET.push({ sti: url.split("?")[0], body: null });
        return { ok: false, status: 500, json: async () => ({ feil: "x" }) };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    window.location.hash = "#/varsler";
    visVarsler(h, ctx());
    await vent(() => h.querySelector(".varselrad"));
    finn(h, t("ui.varsler.gaa_til")).dispatchEvent(new window.Event("click"));
    // Codex P2: testen ga tidligere flaten en `gaaTil`-callback og målte den.
    // Den callbacken finnes ikke i appen — ruteren kaller hver flate med
    // `(hoved, ctx)` — så det som ble målt var en vei ingen bruker gikk, mens
    // den ekte veien kastet `ressurs_id` og landet på lista. Nå måles hash-en,
    // altså nøyaktig det produksjon gjør.
    await vent(() => window.location.hash !== "#/varsler");
    assert.equal(window.location.hash, "#/policyadmin/u-1",
      "varselet førte ikke til HANDLINGEN, bare til flaten");
    assert.ok(POSTET.some((p) => p.sti === "/v1/varsel/1/lest"),
      "å åpne varselet skal også merke det lest");
    globalThis.fetch = brukFetch;
  });

test("Varsler: kanalvalget kan settes til kun portal", async () => {
  POSTET = [];
  SVAR = { "/v1/varsel": { varsler: [VARSEL], uleste: 1,
    kanal: "epost_og_portal" } };
  const h = nyHoved();
  visVarsler(h, ctx());
  await vent(() => h.querySelector(".varselvalg"));
  const valgt = h.querySelector('input[name="varselkanal"]:checked');
  assert.equal(valgt.value, "epost_og_portal",
    "serverens gjeldende valg vises ikke");
  const kun = [...h.querySelectorAll('input[name="varselkanal"]')]
    .find((i) => i.value === "kun_portal");
  kun.checked = true;
  kun.dispatchEvent(new window.Event("change"));
  await vent(() => POSTET.some((p) => p.sti === "/v1/varselvalg"));
  assert.equal(POSTET.find((p) => p.sti === "/v1/varselvalg").body.kanal,
    "kun_portal");
});

// Codex P2: to raske kanalvalg lå på nettet samtidig, og da avgjorde nettet —
// ikke brukeren — hvilket valg som ble stående. Et tregt `kun_portal` kunne
// committe ETTER et senere `epost_og_portal`: serveren slo av e-posten mens
// flaten viste den på, begge kallene meldte «lagret», og ingenting leste
// tilstanden tilbake.
test("Varsler: to raske kanalvalg skriver i den rekkefølgen de ble tatt",
  async () => {
    POSTET = [];
    SVAR = { "/v1/varsel": { varsler: [], uleste: 0,
      kanal: "epost_og_portal" } };
    const brukFetch = globalThis.fetch;
    const fullfort = [];
    let slippTreg = null;
    globalThis.fetch = async (url, opts) => {
      const sti = url.split("?")[0];
      if (opts && opts.method && sti === "/v1/varselvalg") {
        const kanal = JSON.parse(opts.body).kanal;
        POSTET.push({ sti, body: { kanal } });
        // Det FØRSTE valget er det trege — nettopp den rekkefølgen som gjorde
        // det gamle utfallet feil.
        if (kanal === "kun_portal") {
          await new Promise((r) => { slippTreg = r; });
        }
        fullfort.push(kanal);
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    visVarsler(h, ctx());
    await vent(() => h.querySelector(".varselvalg"));
    const inn = (v) => [...h.querySelectorAll('input[name="varselkanal"]')]
      .find((i) => i.value === v);
    const kun = inn("kun_portal");
    const begge = inn("epost_og_portal");

    kun.checked = true;
    kun.dispatchEvent(new window.Event("change"));
    await vent(() => slippTreg);
    // Låsen er den første halvdelen: i en ekte nettleser kan hun ikke klikke
    // om igjen mens lagringen pågår.
    assert.ok(kun.disabled && begge.disabled,
      "kanalvelgeren står åpen for et nytt klikk midt i en lagring");

    // …og køen er den andre. Her klikkes det likevel — det er det som måler
    // køen, og det er det en dispatch fra tastaturet eller en gjenoppfrisket
    // knapp kan gjøre uansett hva `disabled` sier.
    begge.checked = true;
    begge.dispatchEvent(new window.Event("change"));
    await vent(() => false, 5);
    assert.deepEqual(POSTET.map((p) => p.body.kanal), ["kun_portal"],
      "det andre valget ble sendt før det første var committet");

    slippTreg();
    await vent(() => fullfort.length === 2);
    assert.deepEqual(fullfort, ["kun_portal", "epost_og_portal"],
      "valget hun tok sist ble ikke skrevet sist");
    await vent(() => !kun.disabled);
    assert.ok(!kun.disabled && !begge.disabled,
      "kanalvelgeren ble stående låst etter at køen var tom");
    globalThis.fetch = brukFetch;
  });

test("Varsler: tom innboks sier det, i stedet for å vise ingenting", async () => {
  POSTET = [];
  SVAR = { "/v1/varsel": { varsler: [], uleste: 0, kanal: "kun_portal" } };
  const h = nyHoved();
  visVarsler(h, ctx());
  await vent(() => h.textContent.includes(t("ui.varsler.tom")));
  assert.ok(h.textContent.includes(t("ui.varsler.tom")));
  assert.equal(h.querySelector(".varselrad"), null);
});

// Codex P2: innboksen tegnet svaret sitt ubetinget. Var GET-en fortsatt ute da
// eier navigerte videre, tegnet varselflaten seg over den nye ruten — mens
// menyen fortsatt markerte den hun valgte.
test("Et sent innbokssvar tegner seg ikke over ruten eier gikk til", async () => {
  POSTET = [];
  let slipp = null;
  const brukFetch = globalThis.fetch;
  globalThis.fetch = async (url, opts) => {
    if (url.split("?")[0] === "/v1/varsel" && !(opts && opts.method)) {
      await new Promise((r) => { slipp = r; });
      return { ok: true, status: 200, json: async () => ({
        varsler: [VARSEL], uleste: 1, kanal: "epost_og_portal" }) };
    }
    return brukFetch(url, opts);
  };
  const h = nyHoved();
  const annenFlate = (hoved) => sett(hoved, el("h1", { text: "annen flate" }));

  window.location.hash = "#/varsler";
  await vent(() => false, 5);
  const ruter = lagRuter(h, ctx(), { varsler: visVarsler, annen: annenFlate },
    () => {});
  ruter.naviger();
  await vent(() => slipp);                        // innboks-GET er ute på nettet

  // Ruteren kobles av med det samme: en `hashchange` som tegnet `annen` på
  // nytt ville vasket bort sporet etter det foreldede svaret.
  window.location.hash = "#/annen";
  ruter.naviger();
  ruter.stopp();
  assert.ok(h.textContent.includes("annen flate"));

  slipp();
  await vent(() => false, 20);                    // la svaret få tegne, om det vil
  assert.ok(h.textContent.includes("annen flate"),
    "innboksen tegnet seg over ruten eier står i");
  assert.equal(h.querySelector(".varselrad"), null,
    "varsellista havnet inne i en annen rute");
  globalThis.fetch = brukFetch;
});

// Samme kappløp, men startet av oppfriskningen etter «merk som lest». Den er
// verre enn GET-en over: `medStatus` tegner lastetilstanden SYNKRONT, så en
// ubetinget `tegn()` herfra vasket bort den nye ruten allerede før svaret var
// ute på nettet.
test("Oppfriskningen etter «merk lest» river ikke bort ruten eier gikk til",
  async () => {
    POSTET = [];
    SVAR = { "/v1/varsel": { varsler: [VARSEL], uleste: 1,
      kanal: "epost_og_portal" } };
    let slippPost = null;
    const brukFetch = globalThis.fetch;
    globalThis.fetch = async (url, opts) => {
      if (opts && opts.method && url.includes("/lest")) {
        await new Promise((r) => { slippPost = r; });
        POSTET.push({ sti: url.split("?")[0], body: null });
        return { ok: true, status: 200, json: async () => ({ ok: true }) };
      }
      return brukFetch(url, opts);
    };
    const h = nyHoved();
    const annenFlate = (hoved) => sett(hoved, el("h1", { text: "annen flate" }));

    window.location.hash = "#/varsler";
    await vent(() => false, 5);
    const ruter = lagRuter(h, ctx(), { varsler: visVarsler, annen: annenFlate },
      () => {});
    ruter.naviger();
    await vent(() => h.querySelector(".varselrad"));
    finn(h, t("ui.varsler.merk_lest")).dispatchEvent(new window.Event("click"));
    await vent(() => slippPost);                  // merkingen er ute på nettet

    window.location.hash = "#/annen";
    ruter.naviger();
    ruter.stopp();

    slippPost();
    await vent(() => false, 20);                  // la oppfriskningen få tegne
    assert.ok(h.textContent.includes("annen flate"),
      "oppfriskningen rev bort ruten eier står i");
    assert.equal(h.querySelector(".varselliste"), null,
      "innboksen tegnet seg inn i en annen rute");
    globalThis.fetch = brukFetch;
  });

// Codex P2: skallets varselteller er den samme opplysningen ett hakk unna.
// Blir den stående på tallet fra innlastingen, teller den varsler brukeren
// nettopp har kvittert ut — og flaten er det eneste stedet det skjer.
test("Varsler: skallets teller bes om å lese på nytt når et varsel merkes lest",
  async () => {
    POSTET = [];
    SVAR = { "/v1/varsel": { varsler: [VARSEL], uleste: 1,
      kanal: "epost_og_portal" } };
    let oppdateringer = 0;
    const h = nyHoved();
    visVarsler(h, ctx({ oppdaterVarseltall: () => { oppdateringer += 1; } }));
    await vent(() => h.querySelector(".varselrad"));
    finn(h, t("ui.varsler.merk_lest")).dispatchEvent(new window.Event("click"));
    await vent(() => oppdateringer > 0);
    assert.equal(oppdateringer, 1,
      "skallet fikk aldri beskjed om at innboksen har endret seg");
  });

test("Varsler: «Gå til» ber også skallet lese telleren på nytt", async () => {
  POSTET = [];
  SVAR = { "/v1/varsel": { varsler: [VARSEL], uleste: 1,
    kanal: "epost_og_portal" } };
  let oppdateringer = 0;
  const h = nyHoved();
  window.location.hash = "#/varsler";
  visVarsler(h, ctx({ oppdaterVarseltall: () => { oppdateringer += 1; } }));
  await vent(() => h.querySelector(".varselrad"));
  finn(h, t("ui.varsler.gaa_til")).dispatchEvent(new window.Event("click"));
  await vent(() => window.location.hash !== "#/varsler");
  assert.equal(window.location.hash, "#/policyadmin/u-1",
    "telleren ble oppdatert på bekostning av navigasjonen");
  assert.equal(oppdateringer, 1,
    "å åpne varselet merker det lest, men skallet fikk ikke vite det");
});

test("Varsler: planvarselet fører til planflaten, ikke til en blindvei",
  async () => {
    // Codex P2: `plan_pauset`/`plan_gjentatt_brudd` sto ikke i
    // `RUTE_FOR_ART`, så mottakeren fikk teksten uten vei til handlingen —
    // og pausen kan bare oppheves ETT sted: på planen.
    const planvarsel = {
      id: 7, art: "plan_pauset", ressurs_type: "plan",
      ressurs_id: "11111111-2222-4333-8444-555555555555",
      tekstnokkel: "varsel.plan_pauset",
      parametre: { aarsak: "policy_stopper" },
      opprettet: "2026-08-19T09:00:00+00:00", lest: false,
    };
    POSTET = [];
    SVAR = { "/v1/varsel": { varsler: [planvarsel], uleste: 1,
      kanal: "kun_portal" } };
    const h = nyHoved();
    window.location.hash = "#/varsler";
    visVarsler(h, ctx());
    await vent(() => h.querySelector(".varselrad"));
    const gaa = finn(h, t("ui.varsler.gaa_til"));
    assert.ok(gaa, "planvarselet fikk ingen vei til handlingen");
    gaa.dispatchEvent(new window.Event("click"));
    await vent(() => window.location.hash !== "#/varsler");
    // Planen bor under WCAG kontroll (eier 19/8): samleflaten åpner
    // planfanen for et mål den ikke kjenner som fanenøkkel.
    assert.equal(window.location.hash,
      `#/wcagkontroll/${planvarsel.ressurs_id}`);
    // Teksten er mottakerens, med pausegrunnen i seg.
    assert.ok(h.querySelector(".varseltekst").textContent
      .includes("policy_stopper"));
  });

test("Varsler: axe-ren, og radiogruppen har en legend", async () => {
  POSTET = [];
  SVAR = { "/v1/varsel": { varsler: [VARSEL], uleste: 1,
    kanal: "epost_og_portal" } };
  const h = nyHoved();
  visVarsler(h, ctx());
  await vent(() => h.querySelector(".varselrad"));
  assert.ok(h.querySelector("fieldset > legend"),
    "et valg uten legend er en gruppe uten navn for skjermleseren");
  assert.equal((await alvorligeBrudd(h, { fragment: true })).length, 0);
});
