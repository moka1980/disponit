// Prøveperioden i skallet (eiers vedtak 27/11).
import { test } from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, beskrivBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { firmabanner } from "../static/js/firmabanner.js";

settI18nForTest(NB, "nb");

test("Firmabanner: ingenting før de siste sju dagene, og ingenting uten rad", () => {
  assert.equal(firmabanner({ status: "prove", vis: false, dogn_igjen: 20 }),
               null);
  assert.equal(firmabanner({ status: null, vis: false }), null);
  assert.equal(firmabanner(null), null);
});

test("Firmabanner: nedtellingen sier datoen og dagene, entall for seg", () => {
  const b = firmabanner({ status: "prove", vis: true, pauset: false,
    dogn_igjen: 5, prove_utloper: "2026-12-02", kontakt_epost: null });
  assert.equal(b.getAttribute("role"), "status");
  assert.ok(b.textContent.includes("02.12.2026"));
  assert.ok(b.textContent.includes(t("ui.firmabanner.dager")
    .replace("{n}", "5")));
  const en = firmabanner({ status: "prove", vis: true, dogn_igjen: 1,
    prove_utloper: "2026-11-28" });
  assert.ok(en.textContent.includes(t("ui.firmabanner.en_dag")));
  const siste = firmabanner({ status: "prove", vis: true, dogn_igjen: 0,
    prove_utloper: "2026-11-27" });
  assert.ok(siste.textContent.includes(t("ui.firmabanner.siste_dag")));
});

test("Firmabanner: over er over — agentene står stille, med kontakt, axe rent",
  async () => {
    const b = firmabanner({ status: "utlopt", vis: true, pauset: true,
      prove_utloper: "2026-11-20", kontakt_epost: "salg@disponit.example" });
    assert.equal(b.getAttribute("role"), "alert");
    assert.ok(b.textContent.includes(t("ui.firmabanner.utlopt.tittel")));
    assert.ok(b.textContent.includes(t("ui.firmabanner.utlopt.tekst")));
    assert.equal(b.querySelector("a").getAttribute("href"),
                 "mailto:salg@disponit.example");
    const s = firmabanner({ status: "stengt", vis: true, pauset: true });
    assert.ok(s.textContent.includes(t("ui.firmabanner.stengt.tittel")));
    // Uten kontaktadresse: ingen tom lenke og ingen «Ta kontakt:» som
    // peker på ingenting.
    assert.equal(s.querySelector("a"), null);
    assert.ok(!s.textContent.includes(t("ui.firmabanner.kontakt")));
    const m = document.createElement("main");
    nyttBrett().append(m);
    m.append(b);
    const brudd = await alvorligeBrudd(m);
    assert.equal(brudd.length, 0, beskrivBrudd(brudd));
  });
