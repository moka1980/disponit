// OPPSTARTSVAKTEN — den som sier fra når ingenting annet kan.
//
// EIERS FUNN 4/9: «når man klikker login, vises bare hvit tom side».
//
// ÅRSAKEN VAR UTRULLINGEN, og den er verdt å skrive ned: `opp.sh` stopper
// `disponit-api.socket` i vedlikeholdsvinduet. Uten socketen svarer nginx
// 502 med sin egen HTML-side — også når det er «.js» som spørres etter. En
// modul servert som `text/html` BLOKKERES av nettleseren (MIME-sjekken for
// ES-moduler er streng med vilje), hele importgrafen ryker, og `#app` blir
// stående tom med `aria-busy="true"`.
//
// Da har ingen linje av vår kode kjørt. Ingenting kan si fra.
//
// EN HVIT SIDE ER DEN VERSTE FEILMELDINGEN SOM FINNES. Den skiller ikke
// «laster» fra «ødelagt» fra «du har ikke tilgang», og den eneste som kan
// gjette riktig er den som vet hva som skjedde på serveren.
//
// DERFOR ER DENNE FILA ET KLASSISK SCRIPT, IKKE EN MODUL. En modul ville
// vært en del av nøyaktig den grafen som feilet, og hadde ryket med den.
// Den har ingen importer, og den kan ikke det.
//
// DEN FJERNER IKKE VINDUET. Utrullingen stopper fortsatt socketen, og i de
// sekundene er flaten nede. Vakten gjør vinduet SYNLIG og selvhelbredende:
// du får vite hva som skjer, og siden henter seg selv inn når serveren er
// tilbake. Å fjerne vinduet krever et atomisk katalogbytte i utrullingen —
// en egen sak, i `deploy/`.
(function () {
  "use strict";

  // Terskelen er raus med vilje. En treg forbindelse skal IKKE få en
  // feilmelding om noe som er i ferd med å gå bra — en falsk alarm her
  // ville lært brukeren å ignorere den ekte.
  var FORSTE_SJEKK = 6000;
  var PAUSER = [4000, 8000, 16000];   // eskalerende, ikke fast: en
                                       // utrulling tar sekunder, ikke
                                       // millisekunder, og fem raske
                                       // forsøk hjelper ingen.
  var forsok = 0;

  function app() { return document.getElementById("app"); }

  // FLATEN ER OPPE når `app.js` har satt `aria-busy="false"`. Det er
  // appens EGET signal om at den er ferdig — ikke en gjetning på om
  // noden har innhold, som ville vært sann også for en halvtegnet feil.
  function oppe() {
    var a = app();
    return !a || a.getAttribute("aria-busy") === "false";
  }

  function sprakvalg(node) {
    var lang = (document.documentElement.getAttribute("lang") || "nb");
    var n = lang.slice(0, 2) === "en" ? "data-en" : "data-nb";
    var felter = node.querySelectorAll("[data-nb]");
    for (var i = 0; i < felter.length; i++) {
      // `textContent`, aldri innerHTML (V6): teksten er attributtdata, og
      // den skal inn som tekst uansett hvor den kommer fra.
      felter[i].textContent = felter[i].getAttribute(n)
        || felter[i].getAttribute("data-nb");
    }
  }

  function vis() {
    var boks = document.getElementById("oppstartsfeil");
    if (!boks || !boks.hidden) return;
    sprakvalg(boks);
    boks.hidden = false;
    var knapp = document.getElementById("oppstart-paa-nytt");
    if (knapp) {
      knapp.addEventListener("click", function () {
        window.location.reload();
      });
      // FOKUS TIL BESKJEDEN. Den som ikke ser skjermen får ellers ingen
      // beskjed om at noe skjedde — `role="alert"` annonserer teksten,
      // men knappen må også kunne nås uten å lete.
      knapp.focus();
    }
  }

  function sjekk() {
    if (oppe()) {
      var boks = document.getElementById("oppstartsfeil");
      if (boks) boks.hidden = true;
      return;
    }
    vis();
    if (forsok < PAUSER.length) {
      var pause = PAUSER[forsok];
      forsok += 1;
      // ETTER SISTE FORSØK STÅR BESKJEDEN, MED KNAPPEN. Å laste om i det
      // uendelige ville skjult at problemet er varig — og en side som
      // blinker hvert femte sekund er ikke en beskjed, den er støy.
      window.setTimeout(function () {
        if (oppe()) return;
        window.location.reload();
      }, pause);
    }
  }

  window.setTimeout(sjekk, FORSTE_SJEKK);
})();
