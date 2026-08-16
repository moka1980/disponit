// Klient-ruting via hash (#/oversikt …). Én skall-rute holder; ingen server-
// side rutekonfig per flate. Ved navigasjon flyttes fokus til main-
// landemerket (WCAG: SPA-navigasjon skal annonseres/flytte fokus).
// --- Eierskap til `hoved` (Codex P2) --------------------------------------
// Alle flater rendrer inn i ETT `hoved`-element, og en flate som venter på et
// svar har ingen måte å vite at brukeren har navigert videre i mellomtiden. Et
// treigt svar tegnet derfor over den NYE ruten, mens menyvalget hennes ble
// stående markert — skjermen viste én flate og navigasjonen en annen.
//
// Ruteren er den eneste som vet når eierskapet skifter, så den stempler
// elementet ved hver navigasjon. En flate leser stempelet ved oppstart og kan
// sjekke `erGjeldendeVisning` før den skriver et sent svar til skjermen.
// Uten ruter (f.eks. i test) står stempelet i ro på 0, og sjekken sier ja.
const EIERSKAP = new WeakMap();

export function visningsToken(node) { return EIERSKAP.get(node) || 0; }

export function erGjeldendeVisning(node, token) {
  return visningsToken(node) === token;
}

export function lagRuter(hoved, ctx, flater, settAktiv) {
  // Reserveruten er den FØRSTE flaten økten faktisk har, ikke hardkodet
  // `oversikt`. `flater` er allerede scope-filtrert av `tillatteFlater`, så en
  // økt uten `decisions:read` — f.eks. en ren `platform:admin`-økt — har ingen
  // `oversikt`-oppføring her. Var reserven hardkodet, slo en tom eller ugyldig
  // hash rett i `flater["oversikt"](...)`, altså et kall på `undefined`: appen
  // kastet under bootstrap og ble stående blank. Rekkefølgen er `byggRuter`
  // sin, så en vanlig kundeøkt får fortsatt `oversikt`.
  const reserve = Object.keys(flater)[0] || null;

  // Hash-en er `#/<rute>` eller `#/<rute>/<mål>`, og målet er det flaten skal
  // åpne PÅ SEG SELV — et utkast, en sak. Uten det leddet hadde ruteren ingen
  // måte å bære en henvisning på, og en flate som ville sende eier til et
  // bestemt objekt måtte lene seg på en callback ruteren aldri gir den: i
  // produksjon kalles hver flate med `(hoved, ctx)`, så «Gå til» fra
  // varselinnboksen kastet `ressurs_id` og landet på lista (Codex P2).
  //
  // Målet er ETT ledd og prosent-dekodes, slik at en id med skråstrek eller
  // mellomrom overlever turen gjennom adressefeltet. En ødelagt escape-sekvens
  // skal ikke rive ned navigasjonen — da bæres det rå leddet videre, og flaten
  // svarer det den ville svart på en ukjent id.
  function deler() {
    const h = (window.location.hash || "").replace(/^#\/?/, "");
    const skille = h.indexOf("/");
    const rute = skille === -1 ? h : h.slice(0, skille);
    const raa = skille === -1 ? "" : h.slice(skille + 1);
    if (!flater[rute]) return { rute: reserve, mal: null };
    let mal = null;
    if (raa) {
      try { mal = decodeURIComponent(raa); } catch { mal = raa; }
    }
    return { rute, mal };
  }

  function gjeldende() { return deler().rute; }

  let forste = true;
  function naviger() {
    const { rute: r, mal } = deler();
    // Ingen rute i det hele tatt: en økt hvis roller ikke ga ETT kjent scope
    // (`scopes_for_roller` er default-deny og gir tom mengde for ukjente
    // roller). Da finnes det ingen flate å vise, og alternativet til å la være
    // er å kaste. Skallet står igjen med sin egen tomtilstand.
    if (!r) return;
    settAktiv(r);
    // Eierskapet skifter FØR den nye flaten får tegne: alt den forrige har
    // ute på nettet er fra nå av foreldet.
    EIERSKAP.set(hoved, visningsToken(hoved) + 1);
    flater[r](hoved, ctx, mal);
    if (!forste && typeof hoved.focus === "function") hoved.focus();
    forste = false;
  }

  window.addEventListener("hashchange", naviger);

  // Ruteren MÅ kunne rives ned igjen (Codex P2). Den lever på et globalt
  // `hashchange`, men rendrer inn i ETT bestemt `hoved`-element — og det
  // elementet blir løsrevet i det skallet bygges på nytt (språkbytte,
  // utlogging). Uten opprydding ble hver nye ruter lagt PÅ TOPPEN av de
  // gamle: én navigasjon kalte da alle sammen, med ett sett API-kall per
  // ruter, og alt utenom den nyeste ble skrevet inn i et tre ingen ser.
  // Antallet vokste for hvert bytte. `stopp` er tålig å kalle to ganger.
  let stoppet = false;
  function stopp() {
    if (stoppet) return;
    stoppet = true;
    window.removeEventListener("hashchange", naviger);
  }

  return { naviger, gjeldende, stopp };
}
