// M-31 modellstyring — port 14 (jsdom + axe): axe uten alvorlige brudd,
// utfall som TEKST (aldri kun farge), tabellsemantikk (caption/th scope
// begge retninger), digest-kortform med full verdi i title, tall som
// tekst, tomtilstand som eksplisitt innhold, ingen hardkodet tekst
// (pseudo-locale). Ingen delt fixture (m16-formen).
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visModellstyring } from "../static/js/flater/modellstyring.js";

settI18nForTest(NB, "nb");

const DIGEST = "sha256:0123456789abcdef0123456789abcdef";
const SVARFORM = {
  moduler: [{
    modul_id: "m57_ats",
    krav: { kravversjon: 2, sett_id: "hovedsett", sett_versjon: 1,
      sett_hash: "a".repeat(64), terskel_min_andel: 0.9,
      terskel_maks_p95_ms: null, terskel_maks_modellfeil: 0,
      opprettet: "2026-08-30T10:00:00+00:00" },
    sett: { sett_id: "hovedsett", versjon: 1, innhold_hash: "a".repeat(64),
      antall_eksempler: 20, beskrivelse: "demo",
      opprettet: "2026-08-30T09:00:00+00:00" },
    siste_bestatte: { kjoring_id: "k-1", artifact_digest: DIGEST,
      kravversjon: 2, antall_eksempler: 20, antall_bestatt: 19,
      antall_modellfeil: 0, p50_ms: 40, p95_ms: 90, varighet_s: 12.5,
      modellnavn: "mistral", bestatt: true,
      avsluttet_ts: "2026-08-31T11:00:00+00:00" },
    kjoringer: [
      { kjoring_id: "k-1", artifact_digest: DIGEST, kravversjon: 2,
        antall_eksempler: 20, antall_bestatt: 19, antall_modellfeil: 0,
        p50_ms: 40, p95_ms: 90, varighet_s: 12.5, modellnavn: "mistral",
        bestatt: true, avsluttet_ts: "2026-08-31T11:00:00+00:00" },
      { kjoring_id: "k-2", artifact_digest: DIGEST, kravversjon: 1,
        antall_eksempler: 20, antall_bestatt: 11, antall_modellfeil: 2,
        p50_ms: 45, p95_ms: 130, varighet_s: 14.1, modellnavn: "mistral",
        bestatt: false, avsluttet_ts: "2026-08-30T11:00:00+00:00" },
      { kjoring_id: "k-3", artifact_digest: DIGEST, kravversjon: null,
        antall_eksempler: 20, antall_bestatt: 15, antall_modellfeil: 1,
        p50_ms: 50, p95_ms: 140, varighet_s: 15.0, modellnavn: "mistral",
        bestatt: false, avsluttet_ts: "2026-08-29T11:00:00+00:00" },
    ],
  }],
  request_id: "r-test",
};

let SVAR;
globalThis.fetch = async (url) => {
  const sti = url.split("?")[0];
  const oppf = SVAR[sti];
  if (!oppf) return { ok: false, status: 404,
    json: async () => ({ feil: "ikke_funnet" }) };
  return { ok: true, status: 200, json: async () => oppf };
};

function ctx() {
  return { sprak: "nb", scopes: ["security:read"], tenant: "acme",
    paaUautorisert: () => {} };
}

async function vent(pred, n = 60) {
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

test("Modellstyring: model card + kjøringstabell, utfall som tekst, axe rent", async () => {
  SVAR = { "/v1/modellstyring": SVARFORM };
  const h = nyHoved();
  visModellstyring(h, ctx());
  await vent(() => h.querySelectorAll("table").length >= 1);

  // Tabellsemantikk: caption + th scope i BEGGE retninger.
  const tb = h.querySelector("table");
  assert.ok(tb.querySelector("caption").textContent.includes("m57_ats"));
  assert.ok(tb.querySelector('th[scope="col"]'));
  for (const rad of tb.querySelectorAll("tbody tr")) {
    assert.equal(rad.cells[0].tagName, "TH");
    assert.equal(rad.cells[0].getAttribute("scope"), "row");
  }

  // Utfall som TEKST — tre ulike dommer, tre ulike setninger.
  const tekst = tb.textContent;
  assert.ok(tekst.includes(t("ui.modellstyring.bestatt_ja")));
  assert.ok(tekst.includes(t("ui.modellstyring.bestatt_nei")));
  assert.ok(tekst.includes(t("ui.modellstyring.uten_krav")),
    "målekjøringen (kravversjon null) mangler sin egen tekst");

  // Tall som tekst: «19 / 20» er to av svarets tall, aldri en andel.
  assert.ok(tekst.includes("19 / 20"));
  assert.ok(tekst.includes("90"));

  // Digest i kortform, full verdi i title — aldri avkuttet uten spor.
  const kode = tb.querySelector("code");
  assert.equal(kode.getAttribute("title"), DIGEST);
  assert.ok(kode.textContent.length < DIGEST.length);
  assert.ok(kode.textContent.endsWith("…"));

  // Model card-blokken bærer kravet og settet som dt/dd-par.
  const dl = h.querySelector("dl.kv-liste");
  assert.ok(dl.textContent.includes(t("ui.modellstyring.felt.kravversjon")));
  assert.ok(dl.textContent.includes("hovedsett v1"));
  assert.ok(dl.textContent.includes(t("ui.modellstyring.ikke_satt")),
    "NULL-terskelen (p95) skal stå som eksplisitt «ikke satt»");

  const brudd = await alvorligeBrudd(h, { fragment: true });
  assert.equal(brudd.length, 0, beskrivBrudd(brudd));
});

test("Modellstyring: tomtilstand er eksplisitt innhold", async () => {
  SVAR = { "/v1/modellstyring": { moduler: [], request_id: "r" } };
  const h = nyHoved();
  visModellstyring(h, ctx());
  await vent(() => h.textContent.includes(t("ui.modellstyring.ingen")));
  assert.ok(h.textContent.includes(t("ui.modellstyring.ingen")));
});

test("Modellstyring: ingen hardkodet tekst (pseudo-locale)", async () => {
  // Pseudo-locale (kontrakt.test-formen): hver nøkkel → «PL_<nøkkel>».
  // Synlig tekst som IKKE er pseudo-oversatt, data fra svaret eller
  // ren interpunksjon, er hardkodet chrome.
  const PL = Object.fromEntries(Object.keys(NB).map((k) => [k, `PL_${k}`]));
  settI18nForTest(PL, "nb");
  try {
    SVAR = { "/v1/modellstyring": SVARFORM };
    const h = nyHoved();
    visModellstyring(h, ctx());
    await vent(() => h.querySelectorAll("table").length >= 1);
    const tekst = h.textContent;
    for (const ekte of ["Bestått", "Ikke bestått", "Gjeldende krav",
                        "Modellstyring"]) {
      assert.ok(!tekst.includes(ekte),
        `hardkodet norsk tekst i flaten: «${ekte}»`);
    }
    assert.ok(tekst.includes("PL_ui.modellstyring.tittel"));
  } finally {
    settI18nForTest(NB, "nb");
  }
});
