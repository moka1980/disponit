// Oppslag i modulkatalogen: hvilket område og hvilken fase en modul hører til.
//
// Funksjonene bor HER og ikke i `katalog.js` (Codex P1). Den fila er generert
// av `tools/gen_katalog.py`, og `test_katalog.py::test_katalogen_er_fersk`
// krever at en ny kjøring gir et byte-identisk resultat. Håndskrevet kode der
// har derfor to utganger, begge dårlige: porten står rød til noen fjerner den,
// eller neste legitime regenerering sletter den stille — og da slutter
// `komponenter.js` å importere. Generert fil = generert innhold; det som er
// skrevet for hånd, står i en fil som er skrevet for hånd.
//
// Katalogen er fortsatt den ene kilden: oppslagene UTLEDES av `KATALOG`, de
// gjentar den ikke.
import { KATALOG } from "./katalog.js";

export function omradeFor(n) {
  const post = KATALOG.find((k) => k.n === n);
  return post ? post.omrade : null;
}

export function faseFor(n) {
  const post = KATALOG.find((k) => k.n === n);
  return post ? post.fase : null;
}
