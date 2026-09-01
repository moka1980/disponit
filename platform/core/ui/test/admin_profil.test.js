// Profilkortet i Admin — der identiteten HAVNET da topplinjen ble strammet
// inn (eiervedtak 1/9).
//
// 🔴 DENNE FILEN ER PORTEN SOM FLYTTET MED INNHOLDET. Topplinjen bar før
// e-post, prinsipal-id og rolleliste, låst av
// `AppShell: viser bruker_id når e-post mangler, med roller`. Da eier ba om
// at «alt plasseres under admin/profil» kunne den porten ikke bare slettes:
// den beskyttet fire-øyne-flyten — at man ser HVEM man er før man attesterer
// — og en port man fjerner uten å erstatte, er en egenskap man mister uten
// å bestemme seg for det. Kravet er derfor det samme, målt på det nye stedet.
import test from "node:test";
import assert from "node:assert/strict";
import { NB, alvorligeBrudd, nyttBrett } from "./hjelp.js";
import { settI18nForTest, t } from "../static/js/i18n.js";
import { visAdmin } from "../static/js/flater/admin.js";
import { el } from "../static/js/dom.js";

settI18nForTest(NB, "nb");

const OKT = {
  tenant: "Acme AS",
  epost: "kari@acme.no",
  bruker_id: "bid_10e5674ad46e4063ad2bb4520ffc00be",
  roller: ["godkjenner", "leser"],
  scopes: ["decisions:read"],
};

function tegn(okt) {
  const hoved = el("main", {});
  nyttBrett().append(hoved);
  visAdmin(hoved, okt);
  return hoved;
}

test("Admin-profilen bærer prinsipal-id-en UBESKÅRET", () => {
  const hoved = tegn(OKT);
  // Ikke `includes` på et prefiks: hele strengen, fordi det er nettopp den
  // fulle id-en som skiller to konti som deler en ubekreftet e-post. En
  // avkortet id ville sett riktig ut og ikke skilt noe.
  assert.ok(hoved.textContent.includes(OKT.bruker_id),
    "prinsipal-id-en mangler i profilen — fire-øyne har ingen visning igjen");
  assert.ok(hoved.textContent.includes(OKT.epost));
  assert.ok(hoved.textContent.includes(t("ui.rolle.godkjenner")));
});

test("Hver verdi har en LEDETEKST, ikke bare en streng", () => {
  // Eiers ord var «rotete med bid…»: en 64-tegns streng uten ledetekst er
  // ikke informasjon, den er støy. Porten krever paret.
  const hoved = tegn(OKT);
  const dt = [...hoved.querySelectorAll("dt")].map((e) => e.textContent);
  for (const nokkel of ["ui.profil.epost", "ui.profil.bruker_id",
    "ui.profil.roller"]) {
    assert.ok(dt.includes(t(nokkel)),
      `${nokkel} står uten ledetekst i profilen`);
  }
});

test("En verdi som mangler gir ingen tom rad", () => {
  // `api/oidc.py` lagrer `epost` som None når utstederen utelater kravet.
  // En ledetekst med tomt felt ved siden av leser som en feil; raden skal
  // utebli helt.
  const hoved = tegn({ ...OKT, epost: null });
  const dt = [...hoved.querySelectorAll("dt")].map((e) => e.textContent);
  assert.ok(!dt.includes(t("ui.profil.epost")),
    "tom e-post ga en ledetekst uten verdi");
  assert.ok(hoved.textContent.includes(OKT.bruker_id),
    "id-en skal stå selv når e-posten mangler — den er det eneste som er igjen");
});

test("axe: profilkortet har ingen alvorlige brudd", async () => {
  const hoved = tegn(OKT);
  assert.deepEqual(await alvorligeBrudd(hoved), []);
});
