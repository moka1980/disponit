// M-57 Rekruttering (klarsignalet §8). Flaten viser ÉN prosess om gangen:
// kandidatlisten som <table> med caption/scope/aria-sort, vektene som
// range-kontroller med synlig verdi og ny rekkefølge annonsert i
// aria-live="polite", blindingens tilstand som et deaktivert merke
// (avskruing er en auditert mutasjon og hører til #159), detaljpanelet
// som dialog med fokusfelle,
// og signaturdialogen som sier antall, listetype og hashens kortform før
// den irreversible utsendelsen. Utfall meldes i role="alert".
//
// Trafikklyset er ALDRI bare farge: kategorien står som tekst i cellen, og
// fargen er en klasse oppå — en monokrom skjerm og en skjermleser får
// nøyaktig samme dom.
//
// Poeng er poeng (§6): flaten regner aldri om til prosent, og
// re-rangeringen ved vektendring er RENT klientarbeid på nedbrytningen
// serveren alt har levert — samme sum som `evaluering.ranger`, aldri en
// egen sannhet.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, signerRekrutteringsliste, lagreStillingsprofil,
         slettEvaluering, avbrytEvaluering, slettStillingsprofil,
         hentUtsendingstekster, lagreUtsendingstekst,
         slettUtsendingstekst, opprettUtsendingsliste,
         reserverBunt, lastOppBunt, bestillEvaluering,
         hentEvalueringer, hentEvalueringsrapport,
         nyIdempotensnokkel, hentKandidatdokument,
         hentTidsvalg, opprettTidsvalgSlots, deaktiverTidsvalgSlot,
         UautorisertFeil } from "../api.js";
import { harScope } from "../sitekart.js";
import { DataTabell } from "../tabell.js";

// Kandidatkortet (eiers bestilling 30/8): koblingen tilbake fra
// kandidatnummeret til kundens egne data — avmaskerte felter og
// originaldokumentenes navn. Hentes FØRST når leseren ber om det
// (hver lesing spores på serveren), og bak samme frist som resten:
// etter kundens frist finnes kortet ikke (404 → «utilgjengelig»).
function kandidatkortBoks(oppdragId, kandidatId) {
  const beholder = el("div", { class: "rekrut-kandidatkort" });
  const knapp = el("button", { class: "knapp", type: "button",
    text: t("ui.rekruttering.kandidatkort.vis") });
  knapp.addEventListener("click", async () => {
    knapp.disabled = true;
    let svar;
    try {
      svar = await hentJson("/v1/rekruttering/kandidatkort/"
        + `${oppdragId}/${encodeURIComponent(kandidatId)}`);
    } catch {
      knapp.disabled = false;
      sett(beholder, el("p", { role: "alert",
        text: t("ui.rekruttering.kandidatkort.utilgjengelig") }), knapp);
      return;
    }
    const felter = Object.entries(svar.felter || {});
    const liste = el("dl", { class: "rekrut-kandidatkort-felter" });
    for (const [token, verdi] of felter) {
      // Tokenet bærer felttypen ([NAVN-1] → navn); etiketter bor i
      // locale med tokenet selv som ærlig reserve.
      const m = /^\[([A-Z]+)-\d+\]$/.exec(token);
      const felt = m ? m[1].toLowerCase() : null;
      liste.append(
        el("dt", { text: felt
          ? t(`ui.rekruttering.kandidatkort.felt.${felt}`, felt)
          : token }),
        el("dd", { text: verdi }));
    }
    // DOKUMENTENE VISES, IKKE LASTES NED (eiers funn 31/8: kunden har
    // alt filene lokalt). To atskilte kontekster (runde 2 — direkte
    // `src` ga blank side): HENTINGEN gjør forelderen selv, same-origin
    // med øktcookien, for en sandboxet ramme har opak origin og får
    // aldri cookien med sin egen forespørsel. RENDRINGEN skjer så fra
    // en blob i en ramme uten fullmakter: HTML og tekst i full sandkasse
    // (`sandbox=""` — ingen skript, ingen origin); PDF i egen ramme uten
    // sandkasse-attributt, for PDF-viseren er en plugin nettlesere ikke
    // kjører i sandboxede rammer (blank side igjen) — og en blob TYPET
    // application/pdf av serverens egen dom rendres aldri som HTML, så
    // den kan ikke bli DOM eller skript. Alt annet vises aldri: det får
    // en nedlastingslenke, som serverens attachment-fall.
    const dok = (svar.dokumenter || [])
      .filter((d) => d && typeof d === "object"
        && typeof d.filnavn === "string"
        && typeof d.dokument_id === "string")
      .map((d) => {
        const vis = el("button", { class: "knapp lenkeknapp",
          type: "button", text: d.filnavn });
        vis.setAttribute("aria-label",
          `${t("ui.rekruttering.kandidatkort.vis_dokument")} — ${d.filnavn}`);
        vis.addEventListener("click", async () => {
          vis.disabled = true;
          let dokument;
          try {
            dokument = await hentKandidatdokument(oppdragId, d.dokument_id);
          } catch {
            // Samme dempede form som kortets egen henting: frist,
            // nett eller økt — dokumentet er utilgjengelig NÅ.
            vis.disabled = false;
            Detaljpanel({ tittel: d.filnavn, innhold: el("p", {
              role: "alert",
              text: t("ui.rekruttering.kandidatkort.dokument_feilet") }) });
            return;
          }
          vis.disabled = false;
          const type = dokument.innholdstype;
          // Inline-visning DEKODER dokumentet inn i DOM-en (srcdoc/
          // tekstnode) — en grense står foran (CodeRabbit): et
          // overdimensjonert dokument fryser flaten, og faller i
          // stedet til nedlastingslenken. PDF berøres ikke: blob-URL-en
          // dekoder ingenting i flaten.
          const kanInline = dokument.blob.size <= 2 * 1024 * 1024;
          if (kanInline
              && (type === "text/html" || type === "application/xhtml+xml")) {
            // HTML rendres via `srcdoc`, aldri via en URL (runde 3):
            // WebKit nekter en sandboxet (opak) ramme å slå opp
            // forelderens blob-URL-er, og flatens CSP (`default-src
            // 'none'`) blokkerer enhver ramme-FORESPØRSEL — `srcdoc`
            // har ingen forespørsel å blokkere. Innholdet står som
            // attributt (DOM-escapet), sandkassen er full (`sandbox=""`
            // — ingen skript, ingen origin, ingen økt), og srcdoc-
            // dokumentet arver i tillegg flatens strenge CSP.
            const ramme = el("iframe", { class: "rekrut-dokvisning",
              title: d.filnavn, sandbox: "" });
            ramme.setAttribute("srcdoc", await dokument.blob.text());
            Detaljpanel({ tittel: d.filnavn, innhold: ramme });
          } else if (kanInline && type === "text/plain") {
            // Ren tekst går inn som TEKSTNODE — den kan aldri bli
            // markup og trenger verken ramme eller sandkasse.
            Detaljpanel({ tittel: d.filnavn, innhold: el("pre", {
              class: "rekrut-dokvisning rekrut-dokvisning-tekst",
              text: await dokument.blob.text() }) });
          } else if (type === "application/pdf") {
            // PDF-viseren er en plugin nettlesere ikke kjører i
            // sandboxede rammer — rammen står uten sandkasse-attributt,
            // og en blob TYPET application/pdf av serverens egen dom
            // rendres aldri som HTML, så den kan ikke bli DOM eller
            // skript. Forespørselen tillates av flatens `frame-src
            // blob:`. Blob-URL-en slippes når panelet lukkes.
            const url = URL.createObjectURL(dokument.blob);
            Detaljpanel({ tittel: d.filnavn,
              innhold: el("iframe", {
                class: "rekrut-dokvisning rekrut-dokvisning-pdf",
                title: d.filnavn, src: url }),
              paaLukk: () => URL.revokeObjectURL(url) });
          } else {
            // Alt annet vises aldri — nedlastingslenke, som serverens
            // attachment-fall.
            const url = URL.createObjectURL(dokument.blob);
            const lenke = el("a", { class: "knapp",
              href: url, text: t("ui.rekruttering.kandidatkort.last_ned") });
            lenke.setAttribute("download", d.filnavn);
            Detaljpanel({ tittel: d.filnavn,
              innhold: el("div", {},
                el("p", { text: t(
                  "ui.rekruttering.kandidatkort.kan_ikke_vises") }),
                lenke),
              paaLukk: () => URL.revokeObjectURL(url) });
          }
        });
        return el("li", {}, vis);
      });
    // Knappen leseren sto på erstattes av innholdet — fokus flyttes
    // dit (CodeRabbit): en tastaturbruker skal lande på kortet, ikke
    // falle til body.
    const innhold = el("div", { tabindex: "-1" },
      felter.length ? liste : el("p", {
        text: t("ui.rekruttering.kandidatkort.ingen_felter") }),
      el("h4", { text: t("ui.rekruttering.kandidatkort.dokumenter") }),
      dok.length ? el("ul", {}, ...dok) : el("p", {
        text: t("ui.rekruttering.kandidatkort.ingen_dokumenter") }));
    sett(beholder, innhold);
    innhold.focus();
  });
  beholder.append(knapp);
  return beholder;
}
import { Detaljpanel, Bekreftelsesdialog } from "../dialog.js";
import { Faner, Tidspunkt, meldLive } from "../komponenter.js";
import { medStatus, flateHode } from "./felles.js";

function meldUtfall(hoved, okt, tekst) {
  // KVITTERINGEN HØRER TIL ØKTEN, IKKE TIL ÉN TEGNING (Codex P2, runde
  // 10). Utfallsområdet lages på nytt av hver `tegn`, og bytter brukeren
  // prosess etter at hun bekreftet signeringen men før POST-en er
  // besvart, lukker denne tilbakekallingen fortsatt om den GAMLE noden.
  // Meldingen ble da skrevet til et frakoblet element: ingenting vist,
  // ingenting kunngjort — for den ene handlingen på flaten som ikke kan
  // gjøres om. Teksten legges derfor i økten og skrives til noden som
  // står i visningen NÅ; `role="alert"` gjør at den også blir sagt.
  //
  // Den andre halvdelen av det samme vinduet — at et prosessbytte gir en
  // levende «Signer»-knapp på en liste hvis POST er i lufta — er alt
  // lukket: `okt.signeringsnokler` overlever tegningen, så et nytt klikk
  // bærer SAMME `Idempotency-Key` og serveren replayer i stedet for å
  // signere på nytt (056s egen arm; se `signer_endepunkt`). Det som sto
  // åpent var meldingen.
  okt.utfall = tekst;
  const node = hoved.querySelector(".rekrut-utfall");
  if (node) sett(node, tekst);
}

function meldFeil(ctx, hoved, okt, e) {
  // 401 ER IKKE EN HANDLINGSFEIL (Codex P1). Utløper økten mellom
  // lastingen av flaten og mutasjonen, kaster `api.js` UautorisertFeil —
  // og fanget den her som «noe gikk galt», ble brukeren stående i det
  // innloggede skallet med en flate som ikke lenger har en økt bak seg.
  // Resten av klienten sender 401 til `ctx.paaUautorisert` (V2: 401 →
  // innlogging, 403 → ingen tilgang); disse to mutasjonene er ikke et
  // unntak fra den regelen.
  if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
  // ET TAPT SVAR ER IKKE ET AVSLAG (Codex P1). «Handlingen ble avvist.
  // Ingenting er sendt.» er en DEFINITIV setning, og den ble sagt også
  // når `_muter` kastet status 0 (fetch nådde aldri fram, eller svaret
  // gikk tapt etter at serveren commitet) og ved 5xx, der commit-status
  // er ukjent. For en irreversibel utsendelse er det falsk trygghet:
  // brukeren kan gå fra skjermen i den tro at ingen e-post gikk ut.
  // Bare 4xx er serverens egen avvisning FØR commit. Alt annet meldes
  // som det er — uvisst — med veien videre: nøkkelen er beholdt, så en
  // ny forsøk replayer den samme operasjonen i stedet for å lage en ny.
  const status = (e && typeof e.status === "number") ? e.status : 0;
  const definitivt = status >= 400 && status < 500;
  meldUtfall(hoved, okt, t(definitivt ? "ui.rekruttering.feil_utfall"
    : "ui.rekruttering.usikkert_utfall"));
}

function prosessetikett(p) {
  // EN UUID ER IKKE ET GJENKJENNELIG VALG (Codex P2, runde 4). Velgeren
  // leste `p.navn || p.prosess_id`, men serveren har aldri sendt `navn` —
  // så med flere prosesser måtte brukeren velge mellom rå UUID-er før hun
  // kunne lese kandidater eller signere en irreversibel utsendelse.
  //
  // Navnet — stillingens tittel — finnes ikke å hente ennå: prosessraden
  // har ingen navnekolonne, oppdragets payload er kryptert og bærer bare
  // en `stillingsprofil_ref`, og selve profilen er #162-kjeden. Å grave
  // tittelen fram ville vært ny maskin i en fiksrunde (K1). Det som
  // finnes, er STARTTIDSPUNKTET, og sammen med antall kandidater skiller
  // det prosessene fra hverandre for et menneske.
  //
  // `p.navn` står først likevel: den dagen #162 gir tittelen, skal den
  // vinne uten at denne linjen røres. Og faller begge — et svar uten
  // `opprettet` — er UUID-en fortsatt bedre enn en tom oppføring.
  if (p.navn) return p.navn;
  if (!p.opprettet) return p.prosess_id;
  // Datoformen er husets ene beslutning om tidspunkter (`Tidspunkt` i
  // komponenter.js: leserens egen sone, ingen påstand om hvilken). Her
  // trengs teksten, ikke elementet — `<option>` bærer ikke barn.
  // ANTALLET KOMMER FRA INDEKSEN, IKKE FRA LISTEN (Cursor P2, #183).
  // `kandidater` finnes bare på den VALGTE prosessen etter #183, så
  // `(p.kandidater || []).length` ga ALLTID 0 for de øvrige oppføringene
  // i nedtrekket — en prosess med tusen søkere sto oppført med «kandidater:
  // 0». Serveren teller nå raden sin i indeksen; listen står igjen som
  // reserve for et svar uten feltet, ikke som kilden.
  const antall = typeof p.kandidat_antall === "number"
    ? p.kandidat_antall : (p.kandidater || []).length;
  return t("ui.rekruttering.prosessetikett")
    .replaceAll("{dato}", Tidspunkt(p.opprettet).textContent)
    .replaceAll("{antall}", String(antall));
}

function kortHash(hash) {
  // Kortformen i signaturdialogen (§8): nok til å pare mot listen i
  // revisjonssporet, kort nok til å leses opp.
  return `${(hash || "").slice(0, 12)}…`;
}

function flett(mal, verdier) {
  // ÉN PASS OVER MALEN (Cursor P2). En KJEDE av `.replace` leser
  // resultatet av forrige ledd om igjen, så en verdi som selv inneholder
  // en plassholder stjeler neste nøkkels treff: et profilnavn
  // «Drift{versjon}X» spiste `{versjon}` i «Rangering — {navn} (versjon
  // {versjon})», og overskriften — som er fokusmål etter lasting og går
  // til `meldLive` — endte med brukerens tekst der versjonen skulle stå
  // OG en rå `{versjon}` igjen for øret. `replaceAll` løser gjentakelse,
  // ikke rekkefølge; det er re-skanningen som er defekten, og den dør
  // bare av å skanne malen én gang.
  //
  // Ukjente nøkler står igjen urørt: en manglende parameter skal være
  // synlig i teksten, ikke stum.
  return String(mal).replace(/\{(\w+)\}/g, (helt, nokkel) =>
    Object.prototype.hasOwnProperty.call(verdier, nokkel)
      ? String(verdier[nokkel]) : helt);
}

// LENGDE ER IKKE OPPRINNELSE (Codex P2, runde 3). Kortingen het hele tiden
// «maskingenererte id-er kortes, navn kunden selv har gitt står urørt», men
// den MÅLTE `length > 20` — og kontrakten
// (`m57_ats/parsing.py`, `KANDIDAT_ID_KANON`) tillater kundevalgte
// ASCII-id-er på inntil 64 tegn. `senior-backend-engineer-01` er 26 tegn og
// ble `senior-b…` i begge tabeller og i kontrollenes tilgjengelige navn:
// leseren måtte åpne detaljpanelet for å se hvem raden gjaldt, som er
// nøyaktig det kortnavnet skulle fjerne.
//
// DET FINNES INGEN «GENERERT» KLASSE Å KJENNE IGJEN. Codex foreslo å
// detektere generert-formen, men `kandidat_id` kommer ALLTID fra kundens
// manifest (`parsing.py` leser den, ingenting i modulen lager den) — det er
// én klasse id-er, ikke to, og da finnes det ingen markør å slå opp. Det som
// faktisk plager leseren er heller ikke opprinnelsen, men UGJENNOMSIKTIGHET:
// veggen av heksadesimal der et navn skulle stått. Porten måler derfor DET,
// og bare det.
//
// ASYMMETRIEN BESTEMMER RETNINGEN. Å vise for mye koster bredde — og den
// kostnaden er alt betalt av ombrekkingen i `.rekrut-detalj` (samme runde).
// Å vise for lite ØDELEGGER identiteten raden bæres av. Predikatet er derfor
// stengt: er vi ikke sikre på at id-en er ugjennomsiktig, står den hel.
// Dette er ingen grammatikk som tolkes (K4) — det er en total tegnklassetest
// over en streng vi selv skal TEGNE, og den kan ikke feile til en gal
// avgjørelse, bare til en bred kolonne.
const UGJENNOMSIKTIG = /^[0-9a-fA-F]+(?:-[0-9a-fA-F]+)*$/;
// SIFRE ER IKKE HEKSVEGGEN (Cursor P2). `[0-9a-fA-F]` inneholder sifrene, så
// tegnklassetesten alene dømte også den rene sifferstrengen ugjennomsiktig —
// og `KANDIDAT_ID_KANON` tillater nettopp den: `202408150012345678901` er et
// kundenummer et menneske leser, ikke en digest. Det brøt asymmetrien over:
// vi var ikke sikre, og id-en ble likevel kortet. Ugjennomsiktig krever
// derfor ETT av to positive tegn — en heksbokstav (`a-f`), eller UUID-ens
// egen gruppeform, som er maskingenerert uansett hvilke siffer den fikk.
const HEKSBOKSTAV = /[a-fA-F]/;
const UUID_FORM = /^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$/;

function erUgjennomsiktig(id) {
  // Lang OG uten et eneste tegn utenfor heksadesimalen: en UUID (med eller
  // uten bindestreker) eller en digest. `kandidat-09` (#161) og
  // `senior-backend-engineer-01` faller ut på `k`, `n`, `s`, `r` …
  return id.length > 20 && UGJENNOMSIKTIG.test(id)
    && (HEKSBOKSTAV.test(id) || UUID_FORM.test(id));
}

function kortnavnFor(idene) {
  // KORTNAVNET REGNES AV DATAENE, ikke av et tall (Codex P2). Vi finner
  // den korteste lengden der ALLE id-ene i settet fortsatt er
  // forskjellige, og bruker den for hele tabellen — én lengde, så
  // kolonnen ikke blir ujevn. Finnes ingen slik lengde under full id
  // (to identiske id-er er en serverfeil, men flaten skal ikke lyve om
  // det), står id-ene urørt.
  //
  // Bare id-er som faktisk er lange kortes: manifestets `kandidat-09`
  // (#161) er kortere enn terskelen og går urørt igjennom uansett hva
  // de andre radene inneholder.
  //
  // ÉN ALGORITME, TO TABELLER (Cursor P2). Prosesstabellen og
  // rangeringstabellen i evalueringsrapporten viser samme kandidater på
  // samme flate; regnet de kortnavnet hver for seg, ville en fiks i den
  // ene stilltiende latt den andre stå igjen med en vegg av heksadesimal.
  // Kalleren eier settet — entydigheten gjelder alltid akkurat de id-ene
  // som står i tabellen kortnavnet skal leses i.
  // ENTYDIGHETEN REGNES OVER DE SOM FAKTISK KORTES. Bare de ugjennomsiktige
  // id-ene vises som prefiks, så det er bare de som kan kollidere med
  // hverandre; en hel id kan ikke kollidere med et prefiks, fordi prefikset
  // bærer «…» og kanonen ikke tillater det tegnet. Regnet vi `skille` over
  // HELE settet, ville to like beskrivende navn — som aldri kortes — låst
  // `skille` til full lengde og dermed slått av kortingen for UUID-ene som
  // trengte den.
  const ugjennomsiktige = idene.filter(erUgjennomsiktig);
  const maksLengde = Math.max(0, ...ugjennomsiktige.map((i) => i.length));
  let skille = maksLengde;
  for (let n = 8; n < maksLengde; n += 1) {
    if (new Set(ugjennomsiktige.map((i) => i.slice(0, n))).size
        === ugjennomsiktige.length) {
      skille = n;
      break;
    }
  }
  return (id) => (erUgjennomsiktig(id) && skille < id.length
    ? `${id.slice(0, skille)}…`
    : id);
}

export function visRekruttering(hoved, ctx) {
  // ØKTEN OVERLEVER TEGNINGEN (Codex P1 / Cursor P1). Alt som handler om
  // en IRREVERSIBEL operasjon — idempotensnøkkelen som lar serveren
  // replaye, og «denne listen ER signert» — må høre til den lastede
  // flaten, ikke til én `tegn`-lukning. Prosessvelgeren tegner flaten på
  // nytt mot det SAMME svaret, og lå nøklene inne i `tegn`, fikk et bytte
  // fram og tilbake både en fersk nøkkel (så en retry etter et tapt 2xx
  // ble en NY operasjon serveren ikke kan replaye) og en levende
  // «Signer»-knapp på en liste som alt var sendt. Begge holdes derfor her.
  // `utfall` er kvitteringen for den irreversible handlingen, og hører
  // til her av nøyaktig samme grunn som de to over: den skrives når
  // POST-en svarer, og da kan tegningen som ba om den være borte.
  const okt = { signeringsnokler: new Map(), signerte: new Set(),
    utfall: null,
    // Evalueringskjeden (#162): den opplastede bunten og begge
    // SP-2-nøklene hører til den lastede flaten, ikke én tegning — et
    // prosessbytte skal ikke gjøre en retry til en NY operasjon eller
    // miste en alt opplastet bunt.
    // ... OG DET GJØR KJEDEN SOM ER I LUFTA (Cursor P1-2). En opplasting
    // varer lenge nok til at brukeren rekker å bytte fil under den.
    // `paagaaende` er låsen som holder det med ÉN mutasjon om gangen i
    // kjeden — bestillingen OG profillagringen tar den (A-dommen, #212),
    // fordi de deler både profillisten og velgeren de bestiller mot;
    // `generasjon` er kjedens intensjon, så en flygende opplasting ikke
    // kan skrive sin `inndata_ref` inn i en bunt brukeren alt har byttet
    // ut. Filvelgeren kan ikke fryses med `readOnly`, så `generasjon` blir
    // stående: den vokter et vindu som ikke har noe med om-tegning å gjøre.
    //
    // `laasOpp` er derimot BORTE (A-dommen, #212). Den pekte på
    // «kontrollene som står i visningen» fordi en om-tegning kunne komme
    // midt i kjeden. Nå kan den ikke det — `tegn`-utløserne er frosset så
    // lenge `paagaaende` står — og da er kontrollene kjeden låste de samme
    // som skal låses opp.
    //
    bestilling: { reserverIdem: null, bestillIdem: null,
                  inndataRef: null, filnavn: null,
                  paagaaende: false, generasjon: 0,
                  oppdaterProfilvalg: null },
    // Evalueringslisten hører til ØKTEN av samme grunn som bunten over
    // (Cursor P1): den er tenant-global, ikke prosessbundet, men `tegn`
    // bygger seksjonen på nytt ved hvert prosessbytte og seedet lå i
    // `data`-snapshoten fra sidelastingen. Oppfriskningen etter et
    // levert oppdrag tegnet bare DOM, så oppdraget forsvant i det neste
    // bytte — nøyaktig det Codex-løftet «levert oppdrag synlig uten
    // omlasting» lovte bort. `undefined` betyr «ingen oppfriskning
    // ennå»; `null` er listefeil og en ekte verdi. `nr` er
    // oppfriskningens generasjon og `tegn` den for tiden monterte
    // seksjonens tegner — begge hører til listen, ikke til instansen
    // som tilfeldigvis viser den.
    evalueringer: { liste: undefined, flere: false, cursor: null,
                    nr: 0, tegn: null },
    // Rapporthentingen deler prosessbytte-risikoen med listen (Codex
    // P2): generasjon og tegner hører til ØKTEN, og hver mount melder
    // seg som tegner — et svar som lander etter et bytte tegner i den
    // MONTERTE seksjonen, aldri i en frakoblet.
    // FORENKLINGEN (eierdom, K2-dommen i #224): auto-latchen og det
    // delte løftet var to tilstander med hver sine overganger, og fem
    // runder med funn var interleavings mellom dem. Nå AVLEDES latchen:
    // `aktiv` er den ENE in-flight-markøren (deler løftet ved samme id,
    // hindrer dobbel auto), `siste` er den ENE kvitteringen for tegnet
    // rapport. Auto fyrer når begge er tomme — en feilet runde
    // etterlater dem tomme, og neste mount får prøve, uten noen latch å
    // slippe eller gjenåpne.
    // `aktive` er NØKLET (Codex P2, A→B→A): et raskt gjenvalg av A skal
    // finne As eget løfte selv om B startet imellom — fortsatt samme to
    // tilstandsarter (in-flight + cache), bare per rapport-id.
    rapportHenting: { nr: 0, tegn: null, siste: null,
                      aktive: new Map() },
    // Evalueringsseksjonens NODE (remount-dommen): bygges én gang per
    // rute-inngang, gjenbrukes ved prosessbytte, nullstilles ved full
    // lasting.
    evalDel: null,
    // Prosessbyttet er en HENTING, og da bærer det samme risiko som de to
    // over (Cursor P2, #183): generasjonen hører til ØKTEN, ikke til den
    // `tegn`-lukningen som tilfeldigvis startet hentingen — en teller som
    // fødes på nytt for hver tegning vokter ingenting på tvers av byttene.
    prosessHent: { nr: 0 } };
  medStatus(hoved, ctx,
    async () => {
      // Profilene er TILLEGGSDATA (samme politikk som
      // `hentUtrullingForSkall`): faller de, står prosessflaten likevel
      // — editoren viser sin egen tomtilstand. 401 er kvalitativt annet
      // og skal nå innloggingsveien, som overalt ellers.
      const [pros, prof, evals] = await Promise.all([
        hentJson("/v1/rekruttering/prosesser"),
        hentJson("/v1/rekruttering/stillingsprofiler").catch((e) => {
          if (e instanceof UautorisertFeil) throw e;
          return { profiler: [] };
        }),
        hentEvalueringer().catch((e) => {
          if (e instanceof UautorisertFeil) throw e;
          // `null` er FEIL, ikke tom historikk: en utilgjengelig liste
          // skal aldri rendres som «ingen evalueringer bestilt».
          return null;
        }),
      ]);
      return { ...pros, profiler: (prof && prof.profiler) || [],
               evalueringer: evals ? (evals.evalueringer || []) : null,
               evalueringerFlere: !!(evals && evals.flere),
               evalueringerCursor: (evals && evals.neste_cursor) || null,
               evalueringerFerskeste:
                 (evals && evals.ferskeste_klar_oppdrag) ?? null };
    },
    (data) => {
      // En fersk full lasting ER sannheten — også «Prøv igjen» etter en
      // feilet lasting. Oppfriskningscachen fra forrige lasting skal
      // aldri vinne over den, og en oppfriskning som fortsatt er i lufta
      // skal ikke lande oppå den ferske listen: generasjonen bumpes.
      okt.evalueringer.liste = undefined;
      okt.evalueringer.flere = false;
      okt.evalueringer.cursor = null;
      okt.evalueringer.nr += 1;
      // ... og rapport-cachen følger listen: en fersk lasting er
      // sannheten for begge (auto-lastingen får kjøre på nytt).
      okt.rapportHenting.nr += 1;
      okt.rapportHenting.siste = null;
      // ... og prosesshentingen av nøyaktig samme grunn: et bytte som
      // fortsatt er i lufta skal ikke lande OPPÅ den ferske lastingen og
      // sette flaten tilbake til prosessen brukeren forlot.
      okt.prosessHent.nr += 1;
      okt.evalDel = null;
      tegn(hoved, ctx, data, okt);
    });
}

// Landingspunktet for hopplenken over rangeringen (Cursor P2). Ankeret
// eies av `tegn` — det er DEN som vet hva som kommer etter
// evalueringsseksjonen — mens lenken selv står i rapporten som skaper
// behovet for den. Id-en er kontrakten mellom de to.
const HOPP_ANKER = "rekrut-etter-evaluering";

function tegn(hoved, ctx, data, okt, valgtId) {
  const prosesser = (data && data.prosesser) || [];
  // A-DOMMEN (#212): GENERATOREN FJERNES, IKKE INSTANSENE. Tre runder med
  // Cursor-funn på samme mekanisme hadde samme rot: `tegn` river og bygger
  // bestillingsseksjonen på nytt mens en async kjede eier den, og hver
  // eneste binding kjeden lukker over — alerten, skjemaet, `visBunt`,
  // knappen — blir en frakoblet node. Botemiddelet var én indireksjon per
  // binding, oppdaget én om gangen, og flaten vokste 865 → 982 → 1131
  // linjer mens den ble «lukket». Runde fire ville funnet den syvende
  // bindingen.
  //
  // Eierens dom er A: frys `tegn`-utløserne mens kjeden er i lufta. Da kan
  // seksjonen ikke rives, og alle bindingene forblir tilkoblet — uten en
  // eneste ny peker mot «det som er synlig nå». Husmønsteret er
  // `bestilling.js`: `frys` + `aria-busy`, aldri en kontroll som ser
  // levende ut og ikke gjør noe. Prisen — ingen prosessbytte under en
  // pågående opplasting — er dommens egen: brukeren har en irreversibel
  // operasjon i flukt, og velgeren skal SI det.
  // Navngitte plasser, ikke en liste: en seksjon som tegnes på nytt
  // ERSTATTER sin egen kontroll i stedet for å legge igjen en frakoblet
  // node ingen låser opp.
  const utlosere = { velger: null, send: null, lagre: null };
  // #214 (A-maskinen — K2-gapet fra #212 lukket): låsen kjenner også
  // KROPPSFELTENE. Gapet var to frysemekanismer med ulik rekkevidde —
  // `laas.frys` tok utløserne, en lokal closure i bestillingsskjemaet
  // tok feltene — og hvilken som kjørte avhang av hvem som tok låsen:
  // bestillingsarmen frøs alt, profilarmen bare utløserne, og seks
  // review-pass fant speilet av forrige runde. Nå fryser ENHVER som tar
  // låsen alt som er meldt — én mekanisme, symmetrisk, ingen speil.
  // Navngitte plasser her også: en om-tegnet seksjon ERSTATTER feltet
  // sitt i stedet for å etterlate en frakoblet node.
  const felter = { antall: null, frist: null, profil: null };
  //: Snapshot per kontroll ved frysing (bare feltene som ber om det):
  //: verdien kroppen ble bygd med, som change-vakten ruller tilbake
  //: til. Eies av LÅSEN, ikke av en seksjons closure — det var
  //: nøyaktig eierskapet som gjorde at profilarmens frys rullet
  //: tilbake til null og TØMTE valget (Cursor P2-1, runde 6).
  const frosne = new Map();
  const frysEn = (kontroll, paa) => {
    if (!kontroll) return;
    kontroll.disabled = paa;
    // `aria-busy` hører til OMRÅDET som er opptatt, ikke til knappen:
    // skjemaet for kontrollene som står i et, velgerens egen rot ellers.
    const rot = kontroll.form || kontroll.parentElement;
    if (!rot) return;
    if (paa) rot.setAttribute("aria-busy", "true");
    else rot.removeAttribute("aria-busy");
  };
  const frysFelt = (felt, paa) => {
    if (!felt) return;
    // `readOnly` for tekst/tall (feltet beholder fokus og lesbarhet for
    // skjermleseren), `disabled` for select — en select har ingen
    // readOnly, samme valg som skjemaet alltid har gjort.
    if (felt.modus === "readOnly") felt.kontroll.readOnly = paa;
    else felt.kontroll.disabled = paa;
    if (felt.snapshot) {
      if (paa) frosne.set(felt.kontroll, felt.kontroll.value);
      else frosne.delete(felt.kontroll);
    }
  };
  const laas = {
    frys: (paa) => {
      for (const k of Object.values(utlosere)) frysEn(k, paa);
      for (const f of Object.values(felter)) frysFelt(f, paa);
    },
    // Kontrollene fødes til ulik tid — profilskjemaet åpnes på et klikk —
    // så en som meldes mens flaten er frosset, fryses med det samme.
    // `paagaaende` ER den tilstanden; en egen `frosset`-kopi ville bare
    // vært en til å holde i takt.
    meld: (navn, kontroll) => {
      utlosere[navn] = kontroll;
      if (okt.bestilling.paagaaende) frysEn(kontroll, true);
    },
    meldFelt: (navn, kontroll, modus, valg) => {
      felter[navn] = { kontroll, modus, snapshot: !!(valg && valg.snapshot) };
      if (okt.bestilling.paagaaende) frysFelt(felter[navn], true);
    },
    //: Verdien kontrollen hadde da låsen ble tatt — `undefined` når
    //: låsen ikke holdes. Change-vakten leser den her, aldri fra en
    //: seksjons egen kopi.
    frossetVerdi: (kontroll) => frosne.get(kontroll),
  };
  // DEN FØRSTE PROFILEN LÅSER OPP BESTILLINGEN (Cursor P1-1). Uten
  // profiler tegner `bestillSeksjon` en «opprett en profil først»-tekst
  // og returnerer — og profileditorens `oppdaterListe` tegner BARE
  // profillisten på nytt. Lagret brukeren sin aller første profil, sto
  // hun altså igjen med oppfordringen hun nettopp hadde etterkommet, og
  // kjeden «profil → bestilling» var brutt til omlasting eller
  // prosessbytte. Seksjonen får derfor en egen rot editoren kan tegne om.
  //
  // Om-tegningen skjer BARE når seksjonen står uten skjema: et skjema som
  // finnes, kan ha en valgt fil, en alert midt i en opplasting og en
  // POST i lufta — å rive det ned fordi en profil ble lagret, ville vært
  // nøyaktig den frakoblede noden `meldUtfall` finnes for å unngå.
  //
  // ... MEN VELGEREN MÅ LIKEVEL FØLGE LISTEN (Cursor P2-2). Vakten over
  // gjorde skjemaet urørlig, og da ble den bevarte tilstanden feil på et
  // annet punkt: lagret brukeren en ny profilVERSJON mens skjemaet sto,
  // fikk hun kvitteringen «lagret (versjon 3)» ved siden av en velger som
  // fortsatt bare kjente `prof-1@2` — og bestilte mot en versjon hun
  // nettopp hadde erstattet. Fiksen river ikke skjemaet; den bytter bare
  // ut alternativene, så filvalget, antallet og en alt opplastet bunt
  // står. Full skip beholdes for kjeder i lufta: der ville et bytte av
  // alternativene endret kroppen under en bestilling som er underveis.
  const bestillRot = el("div", { class: "rekrut-bestill" });
  const tegnBestilling = () => {
    const del = bestillSeksjon(hoved, ctx, data, okt, laas);
    sett(bestillRot, ...(del ? [del] : []));
  };
  tegnBestilling();
  const profilDel = profilSeksjon(hoved, ctx, data, okt, laas, () => {
    if (!bestillRot.querySelector("form")) { tegnBestilling(); return; }
    // FULL SKIP UNDER `paagaaende` ER BORTE (A-dommen, #212, Cursor
    // P2-1). Skipen fantes fordi et bytte av alternativene under en
    // bestilling som er underveis ville endret kroppen brukeren ser og
    // forkastet `bestillIdem` midt i flukten — men den ble aldri
    // innhentet etterpå, så en versjon lagret i vinduet nådde ALDRI
    // `#bestill-profil`: velgeren sto igjen på en erstattet versjon, og
    // neste bestilling gikk mot den. Fiksen er ikke å hente den inn i en
    // `finally` (enda en indireksjon, enda et vindu) — det er å fjerne
    // vinduet: profileditorens «Lagre» tar den SAMME låsen som kjeden,
    // så de to mutasjonene aldri er i lufta samtidig. Da kan denne
    // linjen bare gjøre jobben sin.
    if (okt.bestilling.oppdaterProfilvalg) okt.bestilling.oppdaterProfilvalg();
  });
  const bestillDel = bestillRot.firstChild ? bestillRot : null;
  // SEKSJONEN OVERLEVER BYTTET SOM NODE (eierdom, remount-dommen —
  // dom-klasse `remount-av-tenantglobal-seksjon`, søster til A-dommen i
  // #212): fem av åtte funn i denne PR-en hadde samme rot — `tegn` rev
  // og bygde en tenant-global seksjon på nytt ved hvert prosessbytte,
  // og hver hengende callback fikk et vindu å dø i. Noden bygges nå én
  // gang per rute-inngang og GJENBRUKES; `sett(hoved, …)` flytter den
  // synkront (replaceChildren + append), så ingen callback kan lande i
  // et vindu. Full lasting nullstiller den sammen med resten.
  const evalDel = okt.evalDel
    || (okt.evalDel = evalueringSeksjon(hoved, ctx, data, okt));
  // #160-fanen bygges LAT — første fetch skjer når leseren åpner den,
  // aldri som en bieffekt av at flaten monteres.
  const tekstDel = () => okt.tekstDel
    || (okt.tekstDel = tekstSeksjon(ctx));
  // HOPPLENKENS LANDINGSPUNKT (Cursor P2). «Produktet først» legger en
  // auto-rendret rangering — én fokusbar `<summary>` per kandidat, opp
  // mot 5000 — foran prosessvelger, vekter og signering. Tastaturveien
  // til de irreversible handlingene ble dermed like lang som
  // kandidatlisten. Å ta `<summary>`-ene ut av tab-rekkefølgen ville
  // stengt tastaturveien INN i detaljene, så løsningen er WCAG 2.4.1s
  // egen: et anker rett etter seksjonen, og en hopplenke til det øverst
  // i rapporten. Ankeret står i begge grenene — profileditoren og
  // bestillingen ligger etter rangeringen også når ingen prosess finnes.
  const hoppAnker = el("div", { id: HOPP_ANKER, tabindex: "-1", role: "group",
    "aria-label": t("ui.rekruttering.evalueringer.hopp_maal") });
  // FANENE BÆRER FLATEN (mobil-redesignet 29/8, eiers bestilling:
  // tab-meny når det er flere seksjoner). Tre likestilte visninger —
  // produktet (evalueringene og rapporten) først, bestillingen og
  // profilene i egne paneler i stedet for under en lang rulle der
  // opplastingen gjemte seg nederst. Panelene holder de SAMME nodene
  // (remount-dommen): `bygg` returnerer den bygde panelroten, og et
  // fanebytte FLYTTER den — river aldri.
  const fanevalg = (paneler) => {
    const faner = Faner({
      trinn: [
        { nokkel: "evalueringer",
          tittel: t("ui.rekruttering.fane.evalueringer"),
          bygg: () => paneler.evalueringer },
        { nokkel: "bestill", tittel: t("ui.rekruttering.fane.bestill"),
          bygg: () => paneler.bestill },
        { nokkel: "profiler", tittel: t("ui.rekruttering.fane.profiler"),
          bygg: () => paneler.profiler },
        { nokkel: "tekster", tittel: t("ui.rekruttering.fane.tekster"),
          bygg: () => paneler.tekster() },
      ],
      // Fanen hører til ØKTEN: et prosessbytte re-tegner flaten, og
      // brukeren skal stå igjen i fanen hun sto i.
      start: okt.fane || "evalueringer",
      paaBytte: (n) => { okt.fane = n; },
      styring: false,
      // Panelene holder LEVENDE seksjoner (remount-dommen): bygges én
      // gang, tømmes aldri av et fanebytte.
      behold: true,
    });
    sett(hoved, flateHode(t("ui.rekruttering.tittel")), faner.rot);
  };
  if (!prosesser.length) {
    fanevalg({
      evalueringer: el("div", {}, evalDel, hoppAnker,
        el("p", { text: t("ui.rekruttering.ingen_prosess") })),
      bestill: bestillDel
        || el("p", { text: t("ui.rekruttering.bestill.ingen_profil") }),
      profiler: profilDel,
      tekster: tekstDel,
    });
    return;
  }
  // FLERE PROSESSER ER TILGJENGELIGE, IKKE BARE DEN FØRSTE (Codex P2).
  // Endepunktet er i flertall, og ruten bærer ingen prosess-id, så med
  // `prosesser[0]` var enhver senere prosess — kandidatlisten og de
  // usignerte utsendingene hennes — utilgjengelig for en tenant med mer
  // enn én pågående rekruttering. Velgeren vises bare når det FINNES noe
  // å velge mellom; valget tegner flaten på nytt for den prosessen.
  // BARE ET AKTIVT LØP AUTO-VISES (eiers funn 30/8): ferdige prosesser
  // stablet seg under evalueringsrapporten som «enda en rapport».
  // Serverens valgt følger samme regel; her speiles den for et lokalt
  // re-tegn. `null` betyr: velgeren står, dypdykket venter på et valg.
  // Regelen om HVEM som auto-vises eier serveren (aktiv-else-ingen);
  // klienten ærer `valgt_prosess_id` — det er raden svaret faktisk
  // bærer full data for. `null` betyr: velgeren står, dypdykket venter.
  const prosess = prosesser.find((p) => p.prosess_id === valgtId)
    || prosesser.find((p) => data
      && p.prosess_id === data.valgt_prosess_id)
    || null;
  let velgerRot = null;
  // Feilveien for prosessbyttet (#183): `role="alert"`, fordi et bytte som
  // ikke gikk gjennom er noe brukeren må vite FØR hun leser videre.
  // EGEN KLASSE, ikke `rekrut-utfall`: den klassen er signeringens og
  // blindingens utfallsområde, og flere tester slår den opp med
  // `querySelector(".rekrut-utfall")` — altså FØRSTE treff i DOM-en.
  // Velgeren står over dem, så en gjenbruk her ville kapret oppslaget og
  // gitt en tom node der utfallet skulle stått. Målt: den gjorde det.
  const velgerFeil = el("div", { role: "alert", class: "rekrut-velgerfeil" });
  if (prosesser.length > 1 || !prosess) {
    const velgerId = "rekrut-prosessvelger";
    const velger = el("select", { id: velgerId },
      // Plassholderen er valget «ingen»: uten aktiv prosess skal flaten
      // ikke late som om en gammel er valgt.
      ...(!prosess ? [el("option", { value: "", selected: "",
        disabled: "" }, t("ui.rekruttering.velg_prosess"))] : []),
      ...prosesser.map((p) => el("option",
        { value: p.prosess_id,
          ...(prosess && p.prosess_id === prosess.prosess_id
            ? { selected: "" } : {}) },
        prosessetikett(p))));
    velger.value = prosess ? prosess.prosess_id : "";
    velger.addEventListener("change", () => {
      // FROSSET ER FROSSET (A-dommen, #212). `disabled` er brukerens vei:
      // nettleseren sender ingen `change` fra en låst kontroll. Men hele
      // poenget med A er at om-tegningen ikke SKJER mens kjeden eier
      // seksjonen, og en invariant som bare hviler på nettleserens
      // oppførsel kan verken måles eller mutasjonstestes. Dommen står
      // derfor også her, ett sted fra: låsen spørres, valget rulles
      // tilbake til den prosessen som faktisk vises.
      if (okt.bestilling.paagaaende) {
        velger.value = prosess ? prosess.prosess_id : "";
        return;
      }
      // BYTTE ER EN HENTING, IKKE EN OM-TEGNING (#183, Codex P2 fra #176).
      // Flaten lastet ALT én gang: hver ureapet prosess med hver kandidats
      // funn, sitater og intervjuspørsmål, og velgeren tegnet på nytt mot
      // det samme svaret. Katalogens løfte er 5000 søknader per bestilling
      // og prosessraden lever inntil 365 døgn, så én GET kunne serialisere
      // titusener av payloader. Nå bærer svaret en LETT indeks og full data
      // for ÉN prosess — og da må velgeren hente den nye.
      //
      // `okt` røres ikke: signeringsnøklene og `signerte`-merket bor der og
      // overlever hentingen av seg selv. Det er invarianten Codex P1 og
      // Cursor P1 alt har felt en runde over i #176, og den holder her
      // fordi økten ligger UTENFOR hentingen — ikke fordi vi husker den.
      const nyId = velger.value;
      // ... OG VELGEREN LYVER ALDRI OM DET SOM STÅR UNDER DEN (Cursor P1).
      // Nettleseren har alt flyttet valget til B i det klikket skjer, men
      // rangeringen, listene og Signer-knappene er A helt til svaret
      // lander og `tegn` kjører. Hentingen kan henge så lenge nettet vil,
      // og i det vinduet leser brukeren A under navnet B — på den ene
      // flaten i huset der handlingen ikke kan angres: hun kan autorisere
      // A-s utsendelse i troen på at hun står i B. Valget rulles derfor
      // tilbake til prosessen som FAKTISK vises, og bare en fullført
      // henting flytter det (`tegn` tegner velgeren på nytt med `nyId`).
      // Samme form som `paagaaende`-vakten seksten linjer over, og samme
      // grunn: låsen er brukerens vei, invarianten er husets.
      velger.value = prosess ? prosess.prosess_id : "";
      // ... OG SVARET MÅ FORTSATT VÆRE ØNSKET NÅR DET LANDER (Cursor P2).
      // Hentingen er husets tredje async-vei inn i denne flaten, og de to
      // andre — rapporten og evalueringslisten — bærer begge vakten alt:
      // generasjon på økten, og eierskapet til `hoved`. Denne hadde
      // ingen. Uten dem tegner et tregt svar seg inn i `hoved` etter at
      // brukeren har navigert bort (`tegn` skriver rått med `sett(hoved,
      // …)`, så den treffer den flaten som står der NÅ), og et eldre
      // bytte kan overskrive en ferskere full lasting med prosessen hun
      // nettopp forlot. `medStatus` vokter sin egen lasting på samme vis
      // — vakten mangler bare her.
      //
      // TILKOBLINGEN er rute-halvdelen, ikke ruterens stempel: ruteren
      // river `hoved` synkront ved hver navigasjon (`visStatus` tegner
      // lastetilstanden med én gang), så en velger som ikke lenger står i
      // dokumentet ER en forlatt visning — og den formen fanger også en
      // full «Prøv igjen»-lasting, som ruterstempelet ikke ville sett.
      // Samme port som `rapportRot.isConnected` alt bærer i denne fila.
      const min = ++okt.prosessHent.nr;
      const gjelder = () => min === okt.prosessHent.nr && velger.isConnected;
      velger.disabled = true;
      hentJson(`/v1/rekruttering/prosesser?prosess_id=${encodeURIComponent(nyId)}`)
        .then((svar) => {
          if (!gjelder()) return;
          // ... OG LÅSEN KAN VÆRE TATT ETTER AT VI SPURTE (Codex P1).
          // A-dommen (#212) sier at ingen om-tegning skjer under en
          // mutasjon, og håndhever det ved å fryse `tegn`-utløserne —
          // velgeren er meldt inn som én av dem. Den frysen holder bare
          // så lenge klikket OG om-tegningen er samme hendelse. Etter
          // #183 er byttet en HENTING: `paagaaende`-vakten seksti linjer
          // over måler ved klikket, og om-tegningen skjer et nettverk
          // senere. I det vinduet er Send og Lagre ufrosne, og en
          // bestilling eller profillagring kan ta låsen — og så tegner
          // dette svaret flaten om midt i den.
          //
          // Det river ikke bare skjemaet mutasjonen skriver i. Det bytter
          // ut hvem låsen gjelder: `laas.meld` fryser hver nye kontroll
          // fordi `paagaaende` står, mens mutasjonens `finally` løfter
          // frysen på den `laas`-en den selv tok — den GAMLE, nå
          // frakoblede. Send og Lagre ble da stående låst til omlasting,
          // med utfallet skrevet i en alert som ikke er i dokumentet.
          // Nøyaktig den frakoblede noden A-dommen finnes for å hindre.
          //
          // Vakten hører derfor der om-tegningen FAKTISK skjer, ikke der
          // den ble bedt om. Svaret forkastes — en lesning er fritt
          // gjentakbar, og valget står alt på prosessen flaten viser
          // (rollbacken skjedde ved hentingens start), så meldingen
          // «valget er satt tilbake» er sann. Velgeren låses IKKE opp:
          // den er nå frosset av mutasjonens `laas`, og det er
          // mutasjonens `finally` som skal løfte den — på de samme
          // kontrollene som tok låsen, som A-dommen krever.
          if (okt.bestilling.paagaaende) {
            sett(velgerFeil, t("ui.rekruttering.prosessbytte_feilet"));
            return;
          }
          // ... OG EN ÅPEN MODAL EIER DEN GAMLE PROSESSEN (Codex P2,
          // denne runden). Under hentingen er bare velgeren låst; resten
          // av flaten er levende, så leseren rekker å åpne en
          // kandidatdetalj eller signeringsdialog for prosessen som
          // fortsatt STÅR der. `aapneDialog` fanger `document
          // .activeElement` som åpner og gir fokus tilbake dit ved
          // lukking — men om-tegningen bytter ut hele prosessen under
          // dialogen, og åpneren er da en frakoblet node. Tastaturbruk
          // ender uten fokusposisjon, og i signeringstilfellet går
          // bekreftelsen videre gjennom den gamle knappen etter at
          // flaten har byttet prosess: en irreversibel handling utført på
          // en visning som ikke lenger finnes.
          //
          // Modalen kjennes på overlegget: `aapneDialog` henger det på
          // `document.body` og setter bakgrunnen `inert`. Svaret
          // forkastes, som i mutasjonsarmen over — en lesning er fritt
          // gjentakbar, og valget står alt på prosessen flaten viser.
          //
          // MEN velgeren låses opp her, i motsetning til over: ingen
          // `finally` venter på å løfte den. Fokus flyttes IKKE — det
          // tilhører dialogen, og å rive det ut av en modal ville vært
          // den samme feilen én etasje ned. Meldingen står i bakgrunnen
          // og leses når dialogen lukkes.
          if (document.querySelector(".overlegg")) {
            sett(velgerFeil, t("ui.rekruttering.prosessbytte_utsatt"));
            velger.disabled = false;
            return;
          }
          data.prosesser = svar.prosesser;
          tegn(hoved, ctx, data, okt, nyId);
          const ny = hoved.querySelector(`#${velgerId}`);
          if (ny) ny.focus();
        })
        .catch((e) => {
          // 401 GÅR TIL INNLOGGINGEN, IKKE UT I INTET (Codex, denne
          // runden). `throw e` her sto i enden av en kjede ingen venter
          // på: det ble en ubehandlet avvisning, og brukeren ble stående
          // i det innloggede skallet med en låst velger og ingen melding
          // — nøyaktig tilstanden `meldFeil` alt er felt over én gang
          // (linje 59). Formen er husets: `ctx.paaUautorisert(); return`
          // (V2: 401 → innlogging, 403 → ingen tilgang). Velgeren blir
          // stående låst med vilje — skallet byttes ut av
          // innloggingsveien, og en åpen kontroll på en død økt inviterer
          // bare til et nytt kall som feiler likt.
          if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
          // Feilveien vokter det SAMME (Cursor P2): en avvist henting fra
          // en forlatt visning skal hverken låse opp en kontroll som ikke
          // står der, eller rope i en `role="alert"` som nå tilhører en
          // annen rute. Samme sted `medStatus` har porten sin.
          if (!gjelder()) return;
          // EN FEILET HENTING HAR INGENTING Å RULLE TILBAKE: valget står
          // alt på prosessen flaten viser, for rollbacken skjer ved
          // hentingens START (Cursor P1) — ellers ville vinduet FØR svaret
          // være like løgnaktig som vinduet etter. Feilveien låser derfor
          // bare opp og sier fra. En 404 er en LOVLIG utgang: prosessen
          // kan ha falt ut på fristen mellom to klikk, og da er «finnes
          // ikke lenger» det sanne svaret.
          sett(velgerFeil, t("ui.rekruttering.prosessbytte_feilet"));
          // ... men den låser bare opp en lås den SELV holder (Codex P1,
          // samme rot som i suksessarmen over). Tok en mutasjon
          // `paagaaende` mens hentingen sto på, er velgeren frosset av
          // `laas` — og en `laas`-frys skal løftes av den `finally`-en
          // som tok den, ikke av en henting som tilfeldigvis feilet
          // under den. Uten dette leddet sto velgeren åpen midt i en
          // bestilling, og et klikk der ville bare rullet seg selv
          // tilbake på `paagaaende`-vakten: en kontroll som ser
          // handlingsklar ut og ikke er det.
          if (okt.bestilling.paagaaende) return;
          velger.disabled = false;
          velger.focus();
        });
    });
    velgerRot = el("div", { class: "rekrut-prosessvelger" },
      el("label", { for: velgerId,
        text: t("ui.rekruttering.prosessvelger") }),
      velger, velgerFeil);
    // ... og HER er `tegn`-utløseren A-dommen navngir: den ene kontrollen
    // på flaten som river bestillingsseksjonen og bygger den på nytt.
    laas.meld("velger", velger);
  }
  if (!prosess) {
    fanevalg({
      evalueringer: el("div", {}, evalDel, hoppAnker, velgerRot,
        el("p", { class: "muted",
          text: t("ui.rekruttering.ingen_aktiv_prosess") })),
      bestill: bestillDel
        || el("p", { text: t("ui.rekruttering.bestill.ingen_profil") }),
      profiler: profilDel,
      tekster: tekstDel,
    });
    return;
  }
  const vekter = { ...prosess.vekter };
  const kanBestille = harScope(ctx, "bestilling:opprett");

  // Utfallsområdet: role=alert — signering og blinding melder hit.
  // Meldingen bæres av økten, så en tegning som kommer ETTER svaret
  // (prosessbytte) viser den fortsatt: `meldUtfall` er kilden.
  const utfall = el("div", { role: "alert", class: "rekrut-utfall" });
  if (okt.utfall) sett(utfall, okt.utfall);
  // Re-rangeringens kunngjøring: høflig, aldri avbrytende.
  const kunngjoring = el("div", { "aria-live": "polite",
    class: "sr-only rekrut-kunngjoring" });

  function poengFor(kandidat) {
    // Samme regel som evaluering.ranger: vekt teller når kravet er
    // oppfylt. `oppfylt` er serverens dom; vekten er brukerens valg.
    // OPPFYLT ER `true`, IKKE «sant nok» (Cursor P1): `"false"` er en
    // sann streng i JS akkurat som i Python, og da ga poengsummen
    // kandidaten hele vekten mens trafikklyset — som nå måler `is True`
    // — sa «Bør vurderes». To tall om samme kandidat på samme skjerm.
    let sum = 0;
    for (const [krav, vekt] of Object.entries(vekter)) {
      if (kandidat.oppfylt && kandidat.oppfylt[krav] === true) {
        sum += Number(vekt);
      }
    }
    return sum;
  }

  const tabellRot = el("div", { class: "rekrut-tabell" });

  // EN DELVIS KANDIDATLISTE ER IKKE EN FERDIG RANGERING (Codex P2).
  // Prosessen FØDES mens kjøringen står på (`plukket`), og artefaktene
  // skrives inkrementelt: under en helt normal evaluering viser tabellen
  // derfor de kandidatene som er vurdert SÅ LANGT, i en rekkefølge som
  // ennå kan snu. Er kjøringen `feilet` eller `kansellert`, kommer resten
  // aldri. Serveren sier tilstanden i `evaluering_status`; her sies den
  // videre, over tabellen, der beslutningen tas.
  //
  // Fail-safe som `vekter_kilde`: merknaden vises med mindre svaret
  // POSITIVT sier `utfort`. Et gammelt svar uten feltet er ikke et bevis
  // på at evalueringen er ferdig.
  const merknadRot = el("div", { class: "rekrut-evaluering" });
  // SLETT OGSÅ HER (eiers funn 30/8 ×3): dypdykket er enda et sted
  // «rapporten» står, og et terminalt løp skal kunne ryddes bort der
  // det vises. Samme dialog og samme dør som listens knapp (069+071);
  // etterpå hentes prosessindeksen på nytt — 069-filteret har da
  // sluppet prosessen, og evalueringslisten oppfriskes om den står.
  if (harScope(ctx, "bestilling:opprett") && prosess.oppdrag_id != null
      && ["utfort", "feilet", "kansellert"]
        .includes(prosess.evaluering_status)) {
    const slettKnapp = el("button", { type: "button", class: "knapp fare",
      text: t("ui.rekruttering.evalueringer.slett") });
    slettKnapp.setAttribute("aria-label",
      t("ui.rekruttering.evalueringer.slett")
      + " — " + t("ui.rekruttering.evalueringer.oppdrag")
      + " " + prosess.oppdrag_id);
    slettKnapp.addEventListener("click", () => {
      // Samme vakt som velgerens: under en pågående mutasjon rives
      // ingenting (A-dommen, #212).
      if (okt.bestilling.paagaaende) return;
      Bekreftelsesdialog({
        tittel: t("ui.rekruttering.evalueringer.slett_tittel"),
        tekst: flett(t("ui.rekruttering.evalueringer.slett_tekst"),
          { oppdrag: prosess.oppdrag_id }),
        primarTekst: t("ui.rekruttering.evalueringer.slett"),
        farlig: true,
        paaPrimar: async () => {
          const min = ++okt.prosessHent.nr;
          let svar;
          try {
            await slettEvaluering(prosess.oppdrag_id);
            svar = await hentJson("/v1/rekruttering/prosesser");
          } catch (e) {
            if (e instanceof UautorisertFeil) {
              ctx.paaUautorisert(); return;
            }
            okt.utfall = t("ui.rekruttering.evalueringer.handlingsfeil");
            if (min === okt.prosessHent.nr && hoved.isConnected) {
              tegn(hoved, ctx, data, okt, valgtId);
            }
            return;
          }
          okt.utfall = t("ui.rekruttering.evalueringer.slett_bestilt");
          if (okt.evaluering) okt.evaluering.oppdater();
          if (min !== okt.prosessHent.nr || !hoved.isConnected) return;
          tegn(hoved, ctx, svar, okt);
        },
      });
    });
    merknadRot.append(el("div", { class: "rekrut-prosesshandling" },
      slettKnapp));
  }
  if (prosess.evaluering_status !== "utfort") {
    merknadRot.append(el("p", { class: "rekrut-evaluering-status",
      text: t(["feilet", "kansellert"].includes(prosess.evaluering_status)
        ? "ui.rekruttering.evaluering_avbrutt"
        : "ui.rekruttering.evaluering_pagar") }));
  }

  // SORTERINGEN ER BRUKERENS VALG, IKKE TABELLENS UTGANGSPUNKT (Codex P2).
  // En vektendring KREVER en ny tabell — poengene er nye — og hver ny
  // `DataTabell` fikk «poeng, synkende» hardkodet inn. Hadde brukeren
  // vendt poengkolonnen stigende for å se hvem som faller ut nederst,
  // slo tabellen tilbake til synkende ved første piltast på en skyver:
  // nettopp den handlingen hun sorterte for å studere, kastet
  // sorteringen. `tabell.js` er bygget for dette og sier det selv —
  // «Tabellen kan ikke eie valget selv — den er borte ved neste tegning.
  // Derfor tar den imot `sort` som utgangspunkt og melder fra via
  // `paaSort`». Flaten hadde bare aldri koblet den ledningen.
  //
  // Valget holdes her, i `tegn`, fordi det er her tabellen bygges om.
  // At det IKKE overlever et prosessbytte, er den samme klassen som G8
  // og hører til den eskalerte avgjørelsen — ikke til denne fiksen.
  let sortValg = { nokkel: "poeng", retning: "descending" };

  // Entydigheten regnes over PROSESSENS egne id-er — settet leseren
  // faktisk skal skille fra hverandre i denne tabellen (`kortnavnFor`).
  //
  // ÉN LUKNING, IKKE ETT KALL PER LESESTED (pass-funn, runde 5). Lukningen
  // sto inne i `tegnTabell` og var dermed utilgjengelig for
  // kunngjøringen under, som derfor limte rå `kandidat_id`. Å regne
  // kortnavnet en gang til der ville gitt to lukninger som KAN divergere;
  // her kan de ikke det. Settet er `tegn`-konstant: `prosess` er bundet
  // én gang (`:460`), og et prosessbytte bygger hele flaten på nytt — så
  // dette er samme verdi `tegnTabell` regnet hver gang, regnet én gang.
  const kortnavn = kortnavnFor(prosess.kandidater.map((k) => k.kandidat_id));

  function tegnTabell() {
    // Flatens egen rekkefølge følger valget, så `rader[0]` fortsatt er
    // den raden som faktisk står øverst — kunngjøringen under leser den.
    // Id-en bryter likhet stigende begge veier, som DataTabells stabile
    // sortering ellers ville gjort.
    const rader = prosess.kandidater
      .map((k) => ({ kandidat: k, poeng: poengFor(k) }))
      .sort((a, b) => (sortValg.retning === "ascending"
        ? a.poeng - b.poeng : b.poeng - a.poeng)
        || (a.kandidat.kandidat_id < b.kandidat.kandidat_id ? -1 : 1));
    // Rangeringssjiktene (eiers funn 30/8): topp 5 grønn, 6–10 svak
    // grønn. Sjiktet regnes ALLTID av poeng synkende — det er
    // kandidatens plass i rangeringen, ikke radens plass i visningen —
    // så fargen står riktig også når leseren sorterer stigende.
    const rangert = [...rader]
      .sort((a, b) => b.poeng - a.poeng
        || (a.kandidat.kandidat_id < b.kandidat.kandidat_id ? -1 : 1));
    const sjikt = new Map(rangert.map((r, i) => [r.kandidat.kandidat_id,
      i < 5 ? "rekrut-sjikt-topp" : i < 10 ? "rekrut-sjikt-neste" : ""]));
    // Plassen følger kandidaten (poeng synkende), ikke radens posisjon
    // i visningen — samme regel som sjiktet, samme kart.
    const plassFor = new Map(rangert.map((r, i) =>
      [r.kandidat.kandidat_id, i + 1]));
    sett(tabellRot, DataTabell({
      captionTekst: t("ui.rekruttering.tabell_caption"),
      kolonner: [
        { nokkel: "kandidat", tittel: t("ui.rekruttering.kol_kandidat") },
        { nokkel: "poeng", tittel: t("ui.rekruttering.kol_poeng"),
          sorterbar: true },
        { nokkel: "kategori", tittel: t("ui.rekruttering.kol_kategori") },
      ],
      sort: sortValg,
      paaSort: (valg) => { sortValg = valg; },
      rader: rader.map(({ kandidat, poeng }) => ({
        radKlasse: sjikt.get(kandidat.kandidat_id) || undefined,
        celler: {
          // KANDIDATREFERANSEN KORTES, MEN MISTES IKKE (produktrunden
          // 28/8). Seeden gir kandidatene UUID-er, og en full UUID bryter
          // over tre linjer på mobil — kolonnen ble en vegg av heksadesimal
          // der leseren skulle kjenne igjen en person.
          //
          // ÅTTE TEGN VAR ET TALL, IKKE ET SVAR (Codex P2). Et
          // UUID-prefiks på åtte heksadesimaler er 32 bit, og det er ingen
          // garanti innenfor 5000 kandidater — to rader kunne vist samme
          // referanse, på flaten der leseren skal skille dem fra hverandre
          // før en irreversibel utsendelse. Lengden regnes nå av DATAENE:
          // `kortnavn` finner det korteste prefikset som er entydig i
          // DENNE prosessen, og faller tilbake til hele id-en når intet
          // prefiks skiller. HELE id-en står uansett i `title`.
          //
          // Er id-en alt lesbar, står den urørt — og «lesbar» måles nå på
          // TEGNENE, ikke på lengden (Codex P2, runde 3). Kontrakten
          // tillater kundevalgte navn på inntil 64 tegn, så `length > 20`
          // alene gjorde `senior-backend-engineer-01` til `senior-b…`.
          // `erUgjennomsiktig` korter bare den rene heksadesimalen.
          //
          // `title` ER IKKE KOPI-VEIEN (pass-funn). Kollisjonsporten
          // innrømmer alt at `title` hverken er kopierbar eller
          // tilgjengelig for berøring og tastatur, så «mistes ikke»
          // kunne ikke hvile på den. Den fulle id-en står i
          // detaljpanelets tittel (`visDetalj`) — raden åpner panelet,
          // og DEN veien går både mus, tastatur og skjermleser.
          // `title` blir stående som musens snarvei, ikke som løftet.
          // OMBREKKINGEN GJELDER OGSÅ DEN SYNLIGE CELLEN (Cursor P2).
          // `.rekrut-detalj` vernet innholdet INNI detaljpanelet, men
          // ikke referansen i raden — og etter at lesbare navn står
          // hele (inntil 64 tegn, kanon uten blanktegn) er det denne
          // strengen som løfter kolonnens min-content og skyver
          // tabellen ut i `.tablewrap`-ens sidescroll. Samme token,
          // samme regel: ingen ny maskin.
          // Plassen står UTENFOR navnespanet: kortnavn-portene måler
          // `.rekrut-kandidat` som ren referanse, og nummeret er sin
          // egen opplysning.
          kandidat: el("span", {},
            el("span", { class: "rekrut-plass",
              text: `${plassFor.get(kandidat.kandidat_id)}.` }),
            el("span",
              { class: "rekrut-kandidat", title: kandidat.kandidat_id },
              kortnavn(kandidat.kandidat_id))),
          poeng: String(poeng),
          // Trafikklys: tekst + klasse, aldri farge alene.
          kategori: el("span",
            { class: `trafikklys trafikklys-${kandidat.status}` },
            el("span", { class: "trafikklys-prikk", "aria-hidden": "true" }),
            t(`ui.rekruttering.status.${kandidat.status}`)),
        },
        sortverdi: { poeng },
        // `paaKlikk`, ikke `utfor` (Codex P2): DataTabell binder radens
        // handling med `b.addEventListener("click", h.paaKlikk)`, og en
        // `undefined` lytter er ingen feil i nettleseren — den er bare
        // ingenting. Knappen sto der, tok fokus, ble lest opp som knapp,
        // og gjorde intet: funnene, kildesitatene og intervjuspørsmålene
        // i detaljpanelet var utilgjengelige for alle. Navnet er tabellens,
        // ikke flatens, og de øvrige kallerne bruker det.
        //
        // HVER RAD SIER HVILKEN KANDIDAT (Codex P2). Knappeteksten er
        // «Detaljer» på hver eneste rad, og kandidat-id-en står i
        // SØSKENCELLEN — søskentekst inngår ikke i en knapps tilgjengelige
        // navn. En skjermleserbruker som navigerer knapp for knapp, fikk
        // derfor N identiske «Detaljer» og ingen måte å vite hvilken
        // kandidat hun åpnet. `tabell.js` har mekanismen for nøyaktig dette
        // (`tilgjengeligNavn` → `aria-label`), og `policyadmin` bruker den
        // med samme «tekst: id»-form. Den synlige teksten er uendret.
        //
        // ØRET HØRER DET ØYET SER (pass-funn). Navnet limte rå
        // `kandidat_id` mens cellen ved siden av viste `kortnavn(...)`:
        // referansen leseren hørte, fantes ikke på skjermen, og den som
        // lette opp raden for «58f17252…» fikk i stedet trettiseks tegn
        // heksadesimal lest opp. Navnet bærer nå samme kortform som
        // cellen — regnet over SAMME sett, så entydigheten som gjelder i
        // tabellen gjelder ordrett også for øret.
        //
        // SETNINGEN EIES AV LOCALE, IKKE AV KODEN (pass-funn, §5). Navnet
        // ble limt som `${t(detaljer)}: ${kortnavn}` — skilletegnet og
        // ordstillingen sto i koden, så et språk som setter kandidaten
        // først, eller skiller med noe annet enn kolon, kunne ikke.
        // Rapportens søsterkontroll fikk `vis_funn_for` i denne PR-en;
        // dette er samme form på samme defekt, ett lesested lenger ned.
        handling: {
          tekst: t("ui.rekruttering.detaljer"),
          tilgjengeligNavn: flett(t("ui.rekruttering.detaljer_for"),
            { kandidat: kortnavn(kandidat.kandidat_id) }),
          paaKlikk: () => visDetalj(kandidat, poeng),
        },
      })),
    }));
    return rader;
  }

  function visDetalj(kandidat, poeng) {
    // Sidepanelet er en dialog med fokusfelle (Detaljpanel → aapneDialog).
    // SITATET ER DATA, IKKE EN GARANTI (Cursor P2). Skriveveien krever
    // `kilde` på hvert funn, men runtime har INSERT på artefaktlageret, så
    // et funn UTEN sitat er en form flaten faktisk kan få. `f.kilde.sitat`
    // kastet da TypeError midt i oppbyggingen av panelet: dialogen åpnet
    // aldri, og raden ble sittende igjen med en «Detaljer»-knapp som ikke
    // svarte.
    // Funnet SLETTES ikke når sitatet mangler. Kategorien er selve
    // risikoopplysningen, og et skjult funn er verre enn et funn uten
    // belegg — plassholderen SIER at belegget mangler, i stedet for å la
    // funnet forsvinne fra en flate som skal vises før en irreversibel
    // utsendelse. Teksten bor i locale (RUTINER §5), som resten.
    const funn = (kandidat.funn || []).filter(Boolean).map((f) => {
      const sitat = f.kilde && typeof f.kilde.sitat === "string"
        ? f.kilde.sitat
        : null;
      return el("li", {},
        el("strong", { text: t(`ui.rekruttering.funn.${f.kategori}`) }),
        " — ",
        sitat === null
          ? el("em", { text: t("ui.rekruttering.uten_sitat") })
          : el("q", { text: sitat }));
    });
    // Ingen intervjuspørsmål i detaljpanelet (eiers produktbeslutning
    // 27/8): de hører til innkallingen av de beste, ikke utvelgelsen.
    // Lageret består; shortlist-arcen (#225) henter derfra.
    Detaljpanel({
      tittel: `${t("ui.rekruttering.kandidat")} ${kandidat.kandidat_id}`,
      innhold: el("div", {},
        el("p", { text: `${t("ui.rekruttering.kol_poeng")}: ${poeng}` }),
        el("h3", { text: t("ui.rekruttering.funn_tittel") }),
        funn.length ? el("ul", {}, ...funn)
          : el("p", { text: t("ui.rekruttering.ingen_funn") }),
        prosess.oppdrag_id != null
          ? kandidatkortBoks(prosess.oppdrag_id, kandidat.kandidat_id)
          : el("span")),
    });
  }

  // Vektene: én range per krav, med <label>, synlig verdi og
  // kunngjort re-rangering. Tastatur: piltaster på range er nok.
  //
  // TAKET FØLGER KONTRAKTEN, IKKE OMVENDT (Codex P1). `evaluering.ranger`
  // godtar ETHVERT ikke-negativt heltall som vekt; `max="10"` var flatens
  // egen påstand om noe annet. Kom en gyldig vekt på 20 inn, klemte
  // nettleseren kontrollens verdi til 10 mens `vekter` og den synlige
  // `output`-en fortsatt sa 20: poengsummene i tabellen var regnet på 20,
  // skyveren sto på taket, og brukerens FØRSTE piltast hoppet vekten fra
  // 20 til 9 — en stille omrangering av kandidatene. Taket regnes derfor
  // ut fra verdiene serveren faktisk sendte (aldri under husets 10, så en
  // vanlig prosess får samme skala som før), og alle kravene deler det, så
  // skyverne fortsatt kan sammenliknes med øyet.
  const VEKT_MAKS_STANDARD = 10;
  const vektMaks = Object.values(vekter).reduce((maks, v) => {
    const tall = Number(v);
    return Number.isFinite(tall) && tall > maks ? tall : maks;
  }, VEKT_MAKS_STANDARD);
  const vektRot = el("fieldset", { class: "rekrut-vekter" },
    el("legend", { text: t("ui.rekruttering.vekter_tittel") }));
  // OPPHAVET STÅR PÅ SKJERMEN, IKKE BARE I SVARET (Codex P1). Serveren
  // har hele tiden sagt sannheten i `vekter_kilde`: enten er vektene
  // evalueringsartefaktets egne, eller de er husets reserve (3 per krav,
  // `api/rekruttering.py`). Flaten leste aldri feltet. For en
  // stillingsprofil med UJEVNE vekter ble hvert krav dermed stilt til 3,
  // og tabellen viste en rangering som ikke er den evalueringen faktisk
  // produserte — uten et eneste tegn på at tallene var oppfunnet.
  //
  // Vektene ER en brukerkontroll her (re-rangeringen er rent klientarbeid
  // på nedbrytningen), så reserven kan bli stående som UTGANGSPUNKT. Det
  // som ikke kan bli stående, er stillheten. Fail-safe: merknaden vises
  // med mindre svaret POSITIVT sier at vektene kom fra artefaktet.
  //
  // Den VARIGE kilden er stillingsprofilen, og å lagre den hører til
  // #162-kjeden — ny maskin (lager + skriver), ikke en fiksrunde (K1).
  if (prosess.vekter_kilde !== "evalueringsartefakt") {
    vektRot.append(el("p", { class: "rekrut-vekter-kilde",
      text: t("ui.rekruttering.vekter_standard") }));
  }
  for (const [krav, verdi] of Object.entries(vekter)) {
    const id = `vekt-${krav}`;
    const visning = el("output", { for: id, text: String(verdi) });
    const range = el("input", { type: "range", id, min: "0",
      max: String(vektMaks), step: "1", value: String(verdi) });
    range.addEventListener("input", () => {
      vekter[krav] = Number(range.value);
      visning.textContent = range.value;
      const rader = tegnTabell();
      // ØRET HØRER DET ØYET SER — OGSÅ HER (pass-funn, runde 5). Denne
      // kunngjøringen var det siste stedet som limte rå `kandidat_id`:
      // `aria-live` leste trettiseks tegn heksadesimal opp for den som
      // nettopp flyttet en skyver, mens cellen øverst i tabellen — det
      // ENESTE stedet hun kan bekrefte kunngjøringen — sa `58f17252…`.
      // Samme lukning som cellen, så referansen er ordrett den samme.
      kunngjoring.textContent = t("ui.rekruttering.ny_rekkefolge")
        .replace("{forst}",
          rader.length ? kortnavn(rader[0].kandidat.kandidat_id) : "");
    });
    vektRot.append(el("div", { class: "rekrut-vekt" },
      el("label", { for: id, text: t(`ui.rekruttering.krav.${krav}`, krav) }),
      range, visning));
  }

  // BLINDINGEN ER EN TILSTAND HER, IKKE ET VALG (Codex P2, runde 4).
  // Bryteren sto handlingsklar for enhver administrator, og etiketten
  // lovte at valget «loggføres med hvem, når og hvorfor» — men
  // `blinding_endepunkt` autentiserer og svarer så en KODET avvisning
  // (409 `blinding_avskruing_krever_159`) uten å se på prosessen og uten
  // å skrive et eneste spor, begge veier. Hvert gyldige forsøk på å skru
  // av — eller på igjen — endte altså i en generisk avvisning, på et
  // løfte om revisjonsevidens.
  //
  // Evidensdesignet for avskruing er #159 (K2: selvattestert avskruing er
  // ikke evidens), og å bygge det her ville vært ny maskin i en fiksrunde
  // (K1). Da gjelder samme svar som for «Signer og send»: løftet trekkes
  // der det ble gitt. Bryteren står som deaktivert TILSTANDSMERKE — den
  // viser at blindingen er på — med en merknad ved siden om at avskruing
  // ikke er tilgjengelig ennå. Mutasjonsbenet (alertdialogen, den
  // påkrevde begrunnelsen, idempotensnøklene, `settRekrutteringBlinding`)
  // er tatt ut SAMMEN med løftet i stedet for å bli stående som død kode:
  // #159 er PR-en som bringer det tilbake, med en skriving som faktisk
  // etterlater sporet etiketten lover.
  const blindingId = "rekrut-blinding";
  const bryter = el("input", { type: "checkbox", id: blindingId });
  bryter.checked = !prosess.blinding_av;
  bryter.disabled = true;
  const blindingRot = el("div", { class: "rekrut-blinding" },
    bryter,
    el("label", { for: blindingId,
      text: t("ui.rekruttering.blinding_etikett") }),
    el("p", { class: "rekrut-merknad",
      text: t("ui.rekruttering.blinding_avskruing_utilgjengelig") }));

  // IN-FLIGHT-LÅS (Cursor P2). Signeringen er irreversibel, og uten lås
  // kunne brukeren fyre av nummer to mens nummer én hang: to POST-er på
  // samme liste, og flaten som følger det svaret som tilfeldigvis kom
  // sist. Låsen er per kontroll, ikke per flate, og løftes alltid — også
  // ved feil, så en mislykket runde ikke etterlater en død knapp.
  async function laast(kontroll, arbeid) {
    if (kontroll.disabled) return;
    kontroll.disabled = true;
    try {
      return await arbeid();
    } finally {
      if (kanBestille && !kontroll.dataset.ferdig) kontroll.disabled = false;
    }
  }
  // Innstilte lister: signering er den irreversible handlingen, og
  // dialogen sier nøyaktig hva som skjer (§8) — antall, listetype,
  // hashens kortform, «Kan ikke angres».
  const listeRot = el("div", { class: "rekrut-lister" },
    el("h2", { text: t("ui.rekruttering.lister_tittel") }));
  // ÉN NØKKEL PER (liste, innholdshash) (Codex P1). Uten arg lager
  // `api.js` en fersk nøkkel per kall, og signeringen er nettopp den
  // operasjonen der det er farlig: commiter serveren og svaret går tapt,
  // melder flaten «feil», og brukerens neste klikk kommer med en NY
  // nøkkel — da replayer ikke serveren, den ser en ny operasjon, og
  // klienten kan ikke lenger avgjøre om posten er sendt. Nøkkelen holdes
  // derfor til vi har et definitivt svar; endres innholdshashen, er det
  // en annen operasjon og en annen nøkkel (mønsteret fra bestilling.js).
  // Kartet ligger i ØKTEN (`visRekruttering`), ikke her: se der.
  function listenokkel(liste) {
    return `${liste.liste_id}|${liste.innhold_hash}`;
  }
  function signeringsnokkel(liste) {
    const id = listenokkel(liste);
    if (!okt.signeringsnokler.has(id)) {
      okt.signeringsnokler.set(id, nyIdempotensnokkel());
    }
    return okt.signeringsnokler.get(id);
  }
  for (const liste of prosess.lister || []) {
    // HVER KNAPP SIER HVILKEN LISTE (Codex P2). Knappeteksten er den samme
    // på alle radene — «Signer og send» — og listetypen, antallet og hashen
    // står som SØSKENTEKST i raden, som ikke inngår i knappens tilgjengelige
    // navn. En skjermleserbruker som navigerer knapp for knapp, fikk derfor
    // to identiske irreversible utsendelser og ingen måte å skille dem på før
    // dialogen sto åpen. Huset har mekanismen fra før: `tabell.js` gir hver
    // radhandling `tilgjengeligNavn` → `aria-label` av nøyaktig samme grunn.
    // Teksten i cellen blir stående; `aria-label` erstatter den ikke, den gir
    // knappen det navnet cellen alt viser med øyet.
    const knapp = el("button", { class: "knapp", type: "button",
      text: t("ui.rekruttering.signer_knapp"),
      "aria-label": t("ui.rekruttering.signer_knapp_navn")
        .replaceAll("{listetype}",
          t(`ui.rekruttering.listetype.${liste.listetype}`))
        .replaceAll("{antall}", String(liste.antall))
        .replaceAll("{hash}", kortHash(liste.innhold_hash)) });
    // En liste som ER signert i denne økten, kommer tilbake død — også
    // etter et prosessbytte, der `data` fortsatt er det svaret som ble
    // hentet FØR signeringen og derfor viser listen som usignert.
    //
    // ... OG SERVERENS EGET SVAR TELLER (Codex P2). `okt.signerte` er
    // ØKTENS hukommelse: den overlever et prosessbytte, ikke en
    // omlasting eller en ny fane. `liste.signert` er seriens
    // signatur-slot lest fra basen, og den overlever alt. Uten dette
    // leddet fikk enhver ny økt en handlingsklar knapp på en serie som
    // alt er signert, og klikket kunne bare ende i `serien_alt_signert`
    // — flaten lovte en irreversibel handling den ikke kunne levere.
    if (liste.signert || okt.signerte.has(listenokkel(liste))) {
      knapp.dataset.ferdig = "1";
    }
    if (!kanBestille || knapp.dataset.ferdig) {
      knapp.setAttribute("disabled", "");
    }
    knapp.addEventListener("click", () => {
      Bekreftelsesdialog({
        rolle: "alertdialog",
        farlig: true,
        tittel: t("ui.rekruttering.signer_tittel"),
        // KNAPPEN LOVER DET HANDLINGEN GJØR (Codex P1). Teksten sa
        // «Signer og send … Dette sender {antall} e-poster», men
        // signeringen AUTORISERER bare: den skriver signaturraden gjennom
        // 056. Selve frigivelsen er `frigi_utsendelse` per mottaker pluss
        // en frigivelsesjobb, og den benen har ingen produksjonskaller —
        // den er #151. Brukeren fikk altså en suksessmelding om N sendte
        // e-poster som ikke gikk noe sted. Fiksen er ikke å bygge
        // senderbenet inne i en fiksrunde (K1), men å slutte å love det:
        // dialogen sier nå at signaturen autoriserer, at den ikke kan
        // angres, og at dette klikket ikke sender e-post.
        //
        // `replaceAll`, ikke `replace` (Cursor P1): teksten kan gjenta et
        // felt, og port 31 krever at setningen sier tallet, ikke
        // plassholderen. En tekst som gjentar et felt skal ikke avhenge
        // av hvor i strengen det står.
        tekst: t("ui.rekruttering.signer_tekst")
          .replaceAll("{antall}", String(liste.antall))
          .replaceAll("{listetype}",
            t(`ui.rekruttering.listetype.${liste.listetype}`))
          .replaceAll("{hash}", kortHash(liste.innhold_hash)),
        primarTekst: t("ui.rekruttering.signer_bekreft"),
        paaPrimar: () => laast(knapp, async () => {
          try {
            const svar = await signerRekrutteringsliste(
              liste.liste_id, liste.innhold_hash,
              signeringsnokkel(liste));
            // Signert er signert: knappen står igjen død, så den
            // irreversible handlingen ikke kan gjentas fra denne visningen
            // — og merket ligger i ØKTEN, så heller ikke fra den neste.
            okt.signerte.add(listenokkel(liste));
            knapp.dataset.ferdig = "1";
            meldUtfall(hoved, okt, t("ui.rekruttering.signer_utfall")
              .replaceAll("{hash}", kortHash(svar.innhold_hash
                || liste.innhold_hash)));
          } catch (e) {
            meldFeil(ctx, hoved, okt, e);
          }
        }),
      });
    });
    listeRot.append(el("div", { class: "rekrut-liste" },
      el("span", { text:
        `${t(`ui.rekruttering.listetype.${liste.listetype}`)} · `
        + `${liste.antall} · ${kortHash(liste.innhold_hash)}` }),
      knapp));
  }

  // PRODUKTET FØRST (eiers UX-prinsipp 27/8) — nå som FANER:
  // evalueringene, rapporten og prosessdypdykket i produktfanen;
  // bestillingen og profilene i hver sin.
  fanevalg({
    evalueringer: el("div", {}, evalDel, hoppAnker, velgerRot,
      utfall, kunngjoring, blindingRot, vektRot, merknadRot, tabellRot,
      tidsvalgSeksjon(ctx, prosess.prosess_id),
      listeRot),
    bestill: bestillDel
      || el("p", { text: t("ui.rekruttering.bestill.ingen_profil") }),
    profiler: profilDel,
    tekster: tekstDel,
  });
  tegnTabell();
}


// ------------------------------------------------------------------
// Stillingsprofilene (#189): kundens/adminens egen kravliste — navn +
// vekt per krav, alltid lagret som en NY versjon (append-only i basen;
// en kjørt evaluering peker på profilen slik den var). Editoren er
// skjemaform etter §8: ekte <label for>, tallfelt med min/maks, knapper
// som <button>, utfall i role="alert", og fokus flyttes inn i skjemaet
// når det åpnes.
// Evalueringsbestillingen (#162, hele kjeden klikkbar): velg ZIP-bunt,
// profilversjon og antall — flaten reserverer, laster opp og bestiller.
// SP-2 hele veien: bunten er engangs (ny fil = ny reservasjon), og
// bestillingsnøkkelen holdes til et DEFINITIVT svar. Skjemaform etter
// §8: ekte <label for>, tallfelt med min/maks, utfall i role="alert".
// M-57s egen rapportflate ("ats"): bestilte evalueringer med status,
// og den promoterte, blindede rangeringsrapporten — lesbar for alle med
// decisions:read (evidensen bak en beslutning tenanten selv bestilte).
function evalueringSeksjon(hoved, ctx, data, okt) {
  const rot = el("section", { "aria-labelledby": "evaluering-tittel" });
  const utfall = el("div", { role: "alert", class: "utfall" });
  // LISTEFEIL OG RAPPORTSVAR DELER IKKE NODE (#258 P2-B, Codex på #255):
  // med ett felles utfallsområde ryddet en vellykket rapporttegning
  // (`rHent.tegn`) bort varselet om at LISTEN fortsatt var utdatert —
  // brukeren mistet beskjeden uten at listeoperasjonen lyktes. To
  // eiere, to noder; hver ryddes kun av sin egen neste handling.
  const listeutfall = el("div", { role: "alert",
    class: "rekrut-listeutfall" });
  const rapportRot = el("div");
  // To raske klikk må ikke la det TREGESTE svaret vinne: bare den sist
  // bestilte hentingen får rendre (eller melde feil). Generasjonen og
  // tegneren bor på ØKTEN (samme form som listen, Codex P2): en lokal
  // teller nullstilt av prosessbyttet vokter ingenting på tvers av dem,
  // og `sett(rapportRot, …)` i en frakoblet instans er et stille tap.
  const rHent = (okt && okt.rapportHenting) || { nr: 0, tegn: null };
  rHent.tegn = (utfallTekst, noder) => {
    // FORLATT RUTE ER IKKE ET LERRET (Codex P2): et svar som lander
    // etter at brukeren forlot rekrutteringen ville tegnet i frakoblet
    // DOM og (verre) annonsert et produkt fra en annen flate i den
    // GLOBALE live-regionen. Frakoblet mål = ingen tegning, og kalleren
    // leser svaret før den annonserer.
    if (!rapportRot.isConnected) return false;
    sett(utfall, ...(utfallTekst ? [utfallTekst] : []));
    sett(rapportRot, ...(noder || []));
    return true;
  };
  // LISTEHANDLINGENE MELDER FRA (Codex P2). En feilet «Last flere» eller
  // «Oppdater» re-aktiverte bare knappen og lot den utdaterte listen stå:
  // brukeren kunne ikke skille et nettbrudd/403/5xx fra «oppdatert, og
  // ingenting hadde endret seg», og fortsatte derfor å handle på gamle
  // statuser — eller å be om en side hen ikke har tilgang til, om og om
  // igjen. 401 er fortsatt ikke en melding: den eier `ctx.paaUautorisert`.
  //
  // Meldingen går i seksjonens `role="alert"` — SAMME utfallsområde som
  // rapporthentingen bruker — og ryddes av neste vellykkede listehandling,
  // så en gammel feil aldri blir stående over en fersk liste. Frakoblet
  // rot er ikke et lerret (samme regel som `rHent.tegn`).
  const meldListefeil = (feilet) => {
    if (!rot.isConnected) return;
    sett(listeutfall,
      ...(feilet ? [t("ui.rekruttering.evalueringer.handlingfeil")] : []));
  };
  // FOKUS OVERLEVER OM-TEGNINGEN (Codex P2). `tegnListe` bytter hele
  // seksjonen med `sett(rot, …)`, så knappen en tastatur- eller
  // skjermleserbruker nettopp aktiverte er en ANNEN node etterpå: fokus
  // falt tilbake til `document.body`, og brukeren mistet posisjonen sin
  // ved hver oppfriskning og hver lastede side — uten noe som sa hvor de
  // nye radene havnet.
  //
  // Fokus flyttes derfor til ERSTATNINGEN for kontrollen som ble
  // aktivert, med «Oppdater» som fallback: «Last flere» forsvinner jo
  // når siste side er hentet, og da er det ingen erstatning å lande på.
  // Beskjeden går i den høflige live-regionen — samme grep som
  // auto-visningen av rapporten — så det annonseres HVA som skjedde, ikke
  // bare at fokus flyttet seg. KUN på eksplisitt klikk: oppfriskningen
  // etter en bestilling går gjennom `oppdater()` uten dette, og skal
  // aldri rive fokus fra skjemaet brukeren står i.
  const etterListeklikk = (foretrukket, antall) => {
    if (!rot.isConnected) return;
    // BARE ET FALT FOKUS GJENOPPRETTES (#258 P2-D, Codex på #255):
    // klikk→svar er en hel nettrundtur, og i det vinduet kan brukeren
    // ha tabbet videre — typisk ned i bestillingsskjemaet. Står fokus
    // på et levende element utenfor seksjonen, er flyttingen deres, og
    // vi river dem ikke tilbake; annonseringen går uansett.
    const aktiv = rot.ownerDocument.activeElement;
    const flyttet = aktiv && aktiv !== rot.ownerDocument.body
      && !rot.contains(aktiv);
    const ny = rot.querySelector("." + foretrukket)
      || rot.querySelector(".eval-oppdater");
    if (ny && !flyttet) ny.focus();
    meldLive(t("ui.rekruttering.evalueringer.listemeldt")
      .replace("{antall}", String(antall)));
  };
  // Listeoppfriskningen bærer NØYAKTIG samme risiko (Cursor P2):
  // `paagaaende` slipper opp før den fire-and-forget `oppdater()` er
  // ferdig, så to raske bestillinger gir to hentinger i lufta samtidig.
  // Uten generasjon kan det treGE eldre svaret tegne over den nyeste
  // listen og fjerne oppdraget brukeren nettopp leverte.
  //
  // Generasjonen bor på ØKTEN, ikke i instansen (Codex P2): listen er
  // øktens, og en teller som nullstilles av hvert prosessbytte vokter
  // ingenting på tvers av dem — en frakoblet instans' trege svar hadde
  // fortsatt `min === listeNr` i SIN teller og kunne skrive seg inn i
  // øktens liste etter et ferskere svar.

  // Bygger rapportens DOM fra et svar — deles av hentestien og
  // økt-cachen (Codex P2: rapporten skal OVERLEVE et prosessbytte uten
  // ny henting; cachen re-bygges inn i den nye seksjonens rot).
  // DE IRREVERSIBLE HANDLINGENE DELES av listen og rapporten (eiers
  // bestilling 29/8: «det skal være mulig å slette også ved vis
  // rapport»): alltid gjennom Bekreftelsesdialogen, utfallet oppfriskes
  // fra basen — aldri en optimistisk rad flaten selv har diktet.
  // `etter` kjøres KUN etter et vellykket kall, før oppfriskningen —
  // rapportens vei bruker den til å ta sin egen visning ned, siden en
  // bestilt sletting gjør innholdet til noe flaten ikke skal vise videre.
  const bekreftEvalueringshandling = (oppdragId, nokkel, kall, kvittering,
    etter) => {
    Bekreftelsesdialog({
      tittel: t(`ui.rekruttering.evalueringer.${nokkel}_tittel`),
      tekst: flett(t(`ui.rekruttering.evalueringer.${nokkel}_tekst`),
        { oppdrag: oppdragId }),
      primarTekst: t(`ui.rekruttering.evalueringer.${nokkel}`),
      farlig: nokkel === "slett",
      paaPrimar: async () => {
        try {
          const svar = await kall(oppdragId);
          meldLive(t(typeof kvittering === "function"
            ? kvittering(svar) : kvittering));
          if (etter) etter();
        } catch (feil) {
          if (feil instanceof UautorisertFeil) {
            ctx.paaUautorisert(); return;
          }
          // 409 evaluering_terminal og alt annet: basen vant — si
          // det, og la oppfriskningen under vise sannheten.
          meldLive(t("ui.rekruttering.evalueringer.handlingsfeil"));
        }
        if (okt.evaluering) okt.evaluering.oppdater();
      },
    });
  };

  const byggRapport = (svar) => {
      const rapport = svar.rapport;
      // DETALJENE HØRER I RADEN (produktrunden 28/8). De sto som en flat
      // mur av `<details>` UNDER tabellen — tjue «Detaljer for
      // kandidat-NN» på rad, uten noen kobling til linjen de gjaldt.
      // Leseren måtte telle seg fram. Nå er hver kandidats detaljer i
      // kandidatens egen rad, og muren finnes ikke.
      // KANDIDATEN ER LESBAR I RAPPORTEN OGSÅ (Cursor P2). Kortnavnet
      // ble innført i prosesstabellen, men rangeringstabellen her sto
      // igjen med rå `kandidat_id` i radoverskriften — og det er DENNE
      // tabellen som står øverst, i produktdelen leseren møter først.
      // Med seedens UUID-er fikk hun altså veggen av heksadesimal i
      // rapporten og den ryddede kolonnen under. Samme helper, samme
      // regel — og hele id-en står i radens `<details>`, ikke i `title`
      // alene (pass-funn; `title` er musens snarvei, ikke kopi-veien).
      const kortnavn = kortnavnFor(rapport.rangering.map((r) => r.kandidat_id));
      // VEKTENE ER SYNLIGE OG LEVENDE OGSÅ HER (mobil-redesignet 29/8,
      // spesifikasjonens egen guard: «rangering vises som rangering med
      // synlige vekter»). Prosess-dypdykket har hatt skyverne hele
      // tiden; rapporten leseren faktisk møter først, viste bare tall.
      // Re-vektingen er RENT klientarbeid på nedbrytningen — artefaktet
      // står urørt, og det sies på skjermen. Oppfyllelse utledes av
      // nedbrytningen (kravet bidro > 0); et krav med profilvekt 0 kan
      // ikke skilles fra ikke-oppfylt i rapportformen og regnes da som
      // ikke-oppfylt — ærlig grense, samme som dypdykkets.
      const profilVekter = {};
      for (const kravRad of ((rapport.profil || {}).krav || [])) {
        profilVekter[kravRad.kravnavn] = kravRad.vekt;
      }
      // RESERVEN NÅR PROFILEN IKKE BÆRER KRAVENE (samme ærlighet som
      // dypdykkets `vekter_kilde`): kravene utledes av nedbrytningens
      // nøkler, og utgangsvekten er største observerte bidrag per krav
      // (= profilvekten for et oppfylt krav). Kilden sies på skjermen.
      let vektReserve = false;
      if (!Object.keys(profilVekter).length) {
        vektReserve = true;
        for (const rad of rapport.rangering) {
          for (const [k, v] of Object.entries(rad.nedbrytning)) {
            profilVekter[k] = Math.max(profilVekter[k] || 0, v, 1);
          }
        }
      }
      const vekter = { ...profilVekter };
      const radPar = rapport.rangering.map((rad) => {
        const boks = detaljboks(rad);
        const oppfylt = {};
        for (const [k, v] of Object.entries(rad.nedbrytning)) {
          oppfylt[k] = v > 0;
        }
        const poengCelle = el("td", { text: String(rad.poeng) });
        // POENGFORDELINGEN BOR I DETALJEN, IKKE I EN KOLONNE (eiers funn
        // 30/8: «Drift: 0, engelsk: 7, …» per rad tok halve mobilskjermen
        // og var det samme tallet leseren alt ser som sum). Linjen står
        // ØVERST i «Vis funn»-detaljen — samme sted leseren alt går for
        // radens hvorfor — og re-vektes levende som cellen gjorde.
        const fordeling = el("p", { class: "rekrut-fordeling",
          text: Object.entries(rad.nedbrytning)
            .map(([k, v]) => `${t(`ui.rekruttering.krav.${k}`, k)}: ${v}`)
            .join(", ") });
        boks.append(fordeling);
        // Plassnummeret (eiers bestilling 30/8): rangeringen SIES, ikke
        // bare vises som rekkefølge — og tallet oppdateres av `ranger()`
        // i samme løkke som flytter raden.
        const plass = el("span", { class: "rekrut-plass" });
        const hovedrad = el("tr", {},
          el("th", { scope: "row", class: "rekrut-kandidat",
            title: rad.kandidat_id }, plass,
            // Navnet i egen node: portene som måler referansen (kortnavn,
            // tilgjengelige navn, ombrekking) leser NAVNET, ikke nummeret.
            el("span", { class: "rekrut-kandidatnavn" },
              kortnavn(rad.kandidat_id))),
          poengCelle);
        // FUNNENE FÅR FULL BREDDE (mobil-redesignet): detaljboksen bor i
        // sin egen rad som spenner alle kolonnene — aldri i en smal
        // kolonne som bryter ord for ord på mobil.
        const detaljrad = el("tr", { class: "rekrut-detaljrad" },
          el("td", { colspan: "2", class: "rekrut-detalj" }, boks));
        return { rad, oppfylt, poeng: rad.poeng, plass,
          poengCelle, fordeling, hovedrad, detaljrad };
      });
      const kropp = el("tbody", {},
        ...radPar.flatMap((p) => [p.hovedrad, p.detaljrad]));
      // Stabil omsortering: parene FLYTTES i ny rekkefølge — åpne
      // `<details>` beholder tilstanden sin, for nodene er de samme.
      // Rangeringssjiktene (eiers funn 30/8): topp 5 grønn, 6–10 svak
      // grønn — redundant koding (radens plass og poengtallet bærer
      // rangeringen, trafikklys-doktrinen), satt på hovedraden i samme
      // løkke som flytter den. Likhet brytes på kandidat-id, så to like
      // poengsummer alltid står i samme rekkefølge.
      const etterPoeng = (a, b) => (b.poeng ?? 0) - (a.poeng ?? 0)
        || (a.rad.kandidat_id < b.rad.kandidat_id ? -1 : 1);
      const ranger = () => {
        [...radPar].sort(etterPoeng)
          .forEach((p, i) => {
            kropp.append(p.hovedrad, p.detaljrad);
            p.plass.textContent = `${i + 1}.`;
            for (const rad of [p.hovedrad, p.detaljrad]) {
              rad.classList.toggle("rekrut-sjikt-topp", i < 5);
              rad.classList.toggle("rekrut-sjikt-neste",
                i >= 5 && i < 10);
            }
          });
      };
      ranger();
      const omVekt = () => {
        for (const p of radPar) {
          p.poeng = Object.keys(vekter).reduce(
            (sum, k) => sum + (p.oppfylt[k] ? vekter[k] : 0), 0);
          p.poengCelle.textContent = String(p.poeng);
          p.fordeling.textContent = Object.keys(vekter)
            .map((k) => `${t(`ui.rekruttering.krav.${k}`, k)}: `
              + `${p.oppfylt[k] ? vekter[k] : 0}`)
            .join(", ");
        }
        ranger();
      };
      const vektFelt = el("fieldset", { class: "rekrut-rapportvekter" },
        el("legend", { text: t("ui.rekruttering.vekter_tittel") }));
      for (const kravnavn of Object.keys(vekter)) {
        const id = `rapportvekt-${kravnavn}`;
        const visning = el("output", { for: id,
          text: String(vekter[kravnavn]) });
        const skyver = el("input", { type: "range", id, min: "0",
          max: String(Math.max(5, ...Object.values(profilVekter))),
          step: "1", value: String(vekter[kravnavn]) });
        skyver.addEventListener("input", () => {
          vekter[kravnavn] = Number(skyver.value);
          visning.textContent = skyver.value;
          omVekt();
          meldLive(t("ui.rekruttering.ny_rekkefolge").replace("{forst}",
            radPar.length
              ? kortnavn([...radPar].sort(etterPoeng)[0].rad.kandidat_id)
              : ""));
        });
        vektFelt.append(el("div", { class: "rekrut-vekt" },
          el("label", { for: id,
            text: t(`ui.rekruttering.krav.${kravnavn}`, kravnavn) }),
          skyver, visning));
      }
      if (vektReserve) {
        vektFelt.append(el("p", { class: "rekrut-vekter-kilde",
          text: t("ui.rekruttering.vekter_standard") }));
      }
      vektFelt.append(el("p", { class: "muted",
        text: t("ui.rekruttering.vekter_klientside") }));
      // ØRET FIKK NAVNET TO GANGER, OG DUPLIKATET LÅ I TEKSTEN (Codex
      // P2). To former er prøvd på denne mekanismen, og begge bommet på
      // samme sted:
      //
      //   1. `sr-only` caption med overskriftens tekst — `sr-only`
      //      skjuler for øyet, ikke for øret: noden ble stående i
      //      tilgjengelighetstreet med nøyaktig samme streng.
      //   2. `aria-labelledby` mot `h3`-en — den flyttet HVOR navnet
      //      kommer fra, men den demper ikke tabellens egen annonsering.
      //      Skjermleseren leser fortsatt overskriften i
      //      dokumentrekkefølge og SAMME streng igjen som tabellnavn
      //      idet tabellen møtes.
      //
      // Rotårsaken er altså ikke noden navnet bor i, men at navnet ER
      // overskriftens tekst. Å låne den i stedet for å kopiere den er
      // fortsatt en kopi for øret. Tabellen får derfor sitt EGET, korte
      // navn som sier hva tabellen inneholder — «Kandidater rangert
      // etter poeng» — mens `h3`-en beholder profil og versjon. To
      // annonseringer, to forskjellige opplysninger.
      //
      // Navnet blir `sr-only`: produktrunden ville ha ÉN overskrift for
      // øyet, og et synlig tabellnavn under `h3`-en ville bygget den
      // andre linjen opp igjen. Det var aldri `sr-only` som var feilen.
      const tabell = el("table", {},
        el("caption", { class: "sr-only",
          text: t("ui.rekruttering.evalueringer.tabellnavn") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col",
            text: t("ui.rekruttering.evalueringer.kandidat") }),
          el("th", { scope: "col",
            text: t("ui.rekruttering.evalueringer.poeng") }))),
        kropp);
      // Skjemaet tillater 5000 kandidater à 100 funn + 20 spørsmål — en
      // gyldig maksrapport ville bygget hundretusener av noder opp front.
      // Kroppen bygges derfor først når leseren åpner den.
      function detaljboks(rad) {
        // Sammendraget er kort fordi det står I raden: kandidaten er
        // allerede navngitt i radoverskriften, så «Detaljer for
        // kandidat-09» ville gjentatt den på hver linje for ØYET.
        //
        // ØRET FÅR IKKE RADEN GRATIS (Codex P2). En skjermleser som
        // lister interaktive elementer, eller tabber gjennom dem, leser
        // kontrollens tilgjengelige navn ALENE — radoverskriften ved
        // siden av er ikke med. Fem tusen kontroller som alle heter «Vis
        // funn» er nøyaktig den telle-seg-fram-en denne runden fjernet
        // for øyet. `aria-label` bærer derfor kandidaten, mens den synlige
        // teksten forblir kort: samme løsning som prosesstabellens
        // Detaljer-knapper alt bruker.
        //
        // …MEN MED RADENS EGEN REFERANSE (pass-funn). `aria-label` limte
        // rå `kandidat_id` mens radoverskriften over viste
        // `kortnavn(...)`. Kontrollen navnga altså kandidaten med en
        // streng som ikke sto noe sted på skjermen — og i en liste over
        // interaktive elementer var hver rad tilbake til sin vegg av
        // heksadesimal, nøyaktig det denne runden fjernet for øyet.
        // Samme `kortnavn` som `th`-en, samme sett, samme entydighet.
        // ... OG DEN HOLDER SEG I VIEWPORTEN (Codex P2). Panelet står nå i
        // en tabellcelle, så innholdet teller med i tabellens intrinsikke
        // bredde: en 64-tegns id uten brytepunkt løftet cellens minstebredde
        // og ga sidescroll på mobil. `.rekrut-detalj` bryter den — se
        // `base.css` for hvorfor det må være `anywhere` og ikke `break-word`.
        // …OG NAVNET BYGGES I LOCALE, IKKE HER (Cursor P2, RUTINER §5).
        // Skillet `" — "` og ordstillingen «handling, så kandidat» sto i
        // koden, så et språk som vil si det motsatt veien — eller med et
        // annet skilletegn — kunne ikke. Én mal eier hele setningen nå;
        // koden leverer bare verdien. Malen er den forlatte
        // `…evalueringer.detaljer` (den døde da «Detaljer» ble «Vis
        // funn»), gjenbrukt under sitt rette navn — ingen ny nøkkel.
        const boks = el("details", { class: "rekrut-detalj" },
          el("summary", {
            "aria-label": flett(t("ui.rekruttering.evalueringer.vis_funn_for"),
              { kandidat: kortnavn(rad.kandidat_id) }),
            text: t("ui.rekruttering.evalueringer.vis_funn") }));
        let bygget = false;
        boks.addEventListener("toggle", () => {
          if (bygget || !boks.open) return;
          bygget = true;
          const k = rapport.kandidater[rad.kandidat_id] || {};
          // Sitatløse funn beholdes med plassholder — speilet fra
          // prosesspanelets funnliste (`:448`): kategorien er selve
          // risikoopplysningen, og et skjult funn er verre enn et uten belegg.
          const funn = (k.funn || []).filter(Boolean).length
            ? el("ul", {}, ...(k.funn || []).filter(Boolean).map((f) => {
                const sitat = f.kilde && typeof f.kilde.sitat === "string"
                  ? f.kilde.sitat
                  : null;
                return el("li", {},
                  el("strong", { text: t(`ui.rekruttering.funn.${f.kategori}`) }),
                  " — ",
                  sitat === null
                    ? el("em", { text: t("ui.rekruttering.uten_sitat") })
                    : el("q", { text: sitat }));
              }))
            : el("p", { text: t("ui.rekruttering.evalueringer.ingen_funn") });
          // Ingen intervjuspørsmål i RANGERINGEN (eiers produktbeslutning
          // 27/8): de hører til innkallingen av de 5–10 beste, ikke til
          // utvelgelsen blant mange. Lageret består; shortlist-arcen
          // henter derfra.
          // HELE ID-EN BOR HER, IKKE I `title` (pass-funn). Rapporten
          // hadde ingen vei til den fulle id-en utenom `th`-ens `title`,
          // og den er hverken kopierbar eller tilgjengelig for berøring
          // og tastatur — «kortes, men mistes ikke» var altså usant på
          // rapportveien. Prosesstabellen har alt svaret: raden åpner et
          // detaljpanel som bærer full id i tittelen (`visDetalj`).
          // Rapportens `<details>` ER den samme veien, så id-en står
          // øverst i den — én linje, i kroppen som uansett bygges først
          // ved åpning. IKKE en `sr-only` node i hver rad: fem tusen
          // radoverskrifter som leser trettiseks tegn heksadesimal er
          // veggen denne runden rev, gjenreist for øret.
          boks.append(
            el("p", {
              text: `${t("ui.rekruttering.kandidat")}: ${rad.kandidat_id}` }),
            el("h4", { text: t("ui.rekruttering.evalueringer.funn") }), funn,
            kandidatkortBoks(svar.oppdrag_id, rad.kandidat_id));
        });
        return boks;
      }
      // Rapporten settes inn ETTER tabellen brukeren sto i — fokusér
      // overskriften, ellers får tastatur/skjermleser aldri vite at
      // lastingen ble ferdig. Fokusmålet er NODEN (`overskrift.focus()`),
      // ikke en id: tabellen bærer sitt eget navn nå, så overskriften
      // trenger ingen id å bli pekt på med.
      const overskrift = el("h3", { tabindex: "-1",
        text: flett(t("ui.rekruttering.evalueringer.rangering"),
          { navn: rapport.profil.navn, versjon: rapport.profil.versjon }) });
      // Hopplenken FØRST i rapporten: den er tastaturbrukerens vei forbi
      // rangeringens N `<summary>` og ned til prosess, vekter og
      // signering (Cursor P2). Husets `.hoppelenke` — usynlig til den
      // får fokus, som «Hopp til innhold» i skallet.
      // EGEN klasse (pass-funn): husets `.hoppelenke` er viewport-
      // absolute for sidetoppen — midt i flaten teleporterte fokuset
      // brukeren bort fra rangeringen lenken betjener. `.rekrut-hopp`
      // er in-flow, sr-only til fokus.
      const hoppLenke = el("a", { class: "rekrut-hopp", href: `#${HOPP_ANKER}`,
        text: t("ui.rekruttering.evalueringer.hopp_prosess") });
      // ADRESSEN EIES AV RUTEREN, som leser den som `#/<rute>`. Lot vi
      // nettleseren følge fragmentet, fyrte `hashchange` med en ukjent
      // rute — og `ruter.js` faller da tilbake til reserveflaten: lenken
      // hadde FORLATT rekrutteringen i stedet for å hoppe inne i den.
      // Lenkeformen består (hjelpemidlene skal si «lenke», og målet er
      // lesbart før klikk), men fokusflyttingen — nøyaktig det
      // nettleseren selv ville gjort — skjer her, uten å røre hashen.
      hoppLenke.addEventListener("click", (ev) => {
        ev.preventDefault();
        const maal = hoved.querySelector(`#${HOPP_ANKER}`);
        if (maal) maal.focus();
      });
    // Hopplenken ETTER overskriften (Codex P2): eksplisitt klikk
    // fokuserer overskriften, og tab framover derfra skal møte
    // bypass-en FØR rangeringens N `<summary>` — sto lenken foran,
    // var den utabbar fra det eneste stedet fokus faktisk står.
    // SLETT OGSÅ HER (eiers bestilling 29/8): rapporten er stedet
    // leseren faktisk står når dommen «denne skal bort» felles. Samme
    // dialog, samme dør som listens knapp — og etterpå tas visningen
    // ned: en bestilt sletting er kundens grense fra bestillings-
    // øyeblikket (069), så flaten skal ikke fortsette å vise innholdet.
    const rapporthode = el("div", { class: "rekrut-rapporthode" },
      overskrift);
    if (harScope(ctx, "bestilling:opprett")) {
      const slettKnapp = el("button", { type: "button",
        class: "knapp fare",
        text: t("ui.rekruttering.evalueringer.slett") });
      slettKnapp.setAttribute("aria-label",
        t("ui.rekruttering.evalueringer.slett")
        + " — " + t("ui.rekruttering.evalueringer.oppdrag")
        + " " + svar.oppdrag_id);
      slettKnapp.addEventListener("click", () =>
        bekreftEvalueringshandling(svar.oppdrag_id, "slett",
          slettEvaluering, "ui.rekruttering.evalueringer.slett_bestilt",
          () => { rHent.siste = null; rHent.tegn(null, []); }));
      rapporthode.append(slettKnapp);
    }
    // VEKTENE ER SAMMENLEGGBARE, LUKKET SOM STANDARD (eiers mobilfunn
    // 30/8): fire skyveknapper før rangeringen dyttet selve produktet —
    // listen — under bretten. Detaljene ligger i DOM-en hele tiden, så
    // re-vektingen virker uendret i det øyeblikket de åpnes.
    const vektFold = el("details", { class: "rekrut-vekterfold" },
      el("summary", { text: t("ui.rekruttering.vekter_tittel") }),
      vektFelt);
    // INNSTILLINGEN (#149, eiermandat 31/8: kjeden gjøres ferdig).
    // Utvalget er RANGERINGEN LESEREN SER: topp N av gjeldende
    // sortering (vektene med) blir invitasjonslisten, resten blir
    // avslagslisten. Kandidatvalg per rad er senere UX-arbeid —
    // beslutningen «hvor går streken» er tallfeltet.
    // Innstillingen er REVERSIBEL (en liste er et utkast til den
    // signeres), så en fersk nøkkel per klikk er trygg — serveren gjør
    // et dobbelklikk på samme nøkkel til gjenspill, og to serier er
    // bare to utkast.
    const innstilling = el("div", { class: "rekrut-innstilling" });
    if (harScope(ctx, "bestilling:opprett") && radPar.length) {
      const antallId = `innstill-antall-${svar.oppdrag_id}`;
      const antallFelt = el("input", { id: antallId, type: "number",
        min: "1", max: String(radPar.length),
        value: String(Math.min(5, radPar.length)) });
      // Tonen (#160/083): kundens forfattede tekst velges HER, ved
      // innstilling — signataren autoriserer den eksakte versjonen
      // (døren pinner den), og e-posten bærer den. Tom = ingen tone.
      const toneId = `innstill-tone-${svar.oppdrag_id}`;
      const toneVelger = el("select", { id: toneId },
        el("option", { value: "",
          text: t("ui.rekruttering.innstill.tone_ingen") }));
      hentUtsendingstekster().then((d) => {
        for (const tekst of (d && d.tekster) || []) {
          toneVelger.append(el("option", { value: tekst.tekst_id,
            text: tekst.navn }));
        }
      }).catch(() => {});   // tom velger er en ærlig reserve
      const innstillUtfall = el("div", { role: "alert",
        class: "rekrut-innstillingsutfall" });
      const lagKnapp = (listetype, nokkel) => {
        const knapp = el("button", { type: "button", class: "knapp",
          text: t(`ui.rekruttering.innstill.${nokkel}`) });
        knapp.addEventListener("click", async () => {
          const n = Math.max(1, Math.min(radPar.length,
            Number(antallFelt.value) || 0));
          const rangertNaa = [...radPar].sort(etterPoeng)
            .map((par) => par.rad.kandidat_id);
          const valgte = listetype === "invitasjon"
            ? rangertNaa.slice(0, n) : rangertNaa.slice(n);
          if (!valgte.length) {
            sett(innstillUtfall, el("p", {
              text: t("ui.rekruttering.innstill.resten_tom") }));
            return;
          }
          for (const b of innstilling.querySelectorAll("button")) {
            b.disabled = true;
          }
          let liste;
          try {
            liste = await opprettUtsendingsliste(
              svar.oppdrag_id, listetype, valgte, null,
              toneVelger.value || null);
          } catch (e) {
            if (e instanceof UautorisertFeil) {
              ctx.paaUautorisert(); return;
            }
            for (const b of innstilling.querySelectorAll("button")) {
              b.disabled = false;
            }
            // M-8 (DOM 2): en invitasjonsliste krever minst én aktiv
            // slot — egen tekst som peker kunden til tidsvalg-
            // seksjonen, aldri en generisk feil.
            sett(innstillUtfall, el("p", {
              text: t(e && e.kode === "tidsvalg_slot_mangler"
                ? "ui.rekruttering.innstill.tidsvalg_mangler"
                : "ui.rekruttering.innstill.feil") }));
            return;
          }
          // Listen bor under «Innstilte lister» — prosessene hentes på
          // nytt så signeringsflaten viser den med en gang.
          okt.utfall = flett(t("ui.rekruttering.innstill.innstilt"),
            { antall: liste.antall });
          try {
            const ferskt = await hentJson("/v1/rekruttering/prosesser");
            if (hoved.isConnected) tegn(hoved, ctx, ferskt, okt);
          } catch {
            for (const b of innstilling.querySelectorAll("button")) {
              b.disabled = false;
            }
            sett(innstillUtfall, el("p", {
              text: flett(t("ui.rekruttering.innstill.innstilt"),
                { antall: liste.antall }) }));
          }
        });
        return knapp;
      };
      innstilling.append(
        el("h4", { text: t("ui.rekruttering.innstill.tittel") }),
        el("div", { class: "rekrut-innstillingsvalg" },
          el("label", { for: antallId,
            text: t("ui.rekruttering.innstill.antall") }),
          antallFelt,
          el("label", { for: toneId,
            text: t("ui.rekruttering.innstill.tone") }),
          toneVelger,
          lagKnapp("invitasjon", "invitasjon_knapp"),
          lagKnapp("avslag", "avslag_knapp")),
        innstillUtfall);
    }
    return { overskrift, noder: [
      rapporthode,
      hoppLenke,
      el("p", { text: t("ui.rekruttering.evalueringer.blindet") }),
      vektFold,
      el("div", { class: "tablewrap" }, tabell),
      innstilling] };
  };

  const visRapport = async (oppdragId, { fokus = true } = {}) => {
    // Auto-stien (fokus=false) og klikk-stien deler suksessvei, men
    // ALDRI feilform (pass-funn): listen og detaljen kan divergere i
    // vinduet mellom dem (frist/TOCTOU/transient), og en usolicited
    // `role="alert"` på hver sidelasting er falsk alarm. Auto-feil er
    // stille — rapportområdet står tomt, listen er fortsatt sannheten.
    // CACHE-TREFF FØRST (Codex P2): et promotert artefakt er immutabelt,
    // så et klikk på rapporten som alt står i `siste` er aldri en grunn
    // til å laste ned og dekryptere 5000 kandidater på nytt — den
    // re-bygges, og klikket beholder fokus-semantikken sin.
    if (rHent.siste && rHent.siste.oppdrag_id === oppdragId) {
      // ALT VIST? Ikke bygg på nytt (Codex P2): en rebuild kollapser
      // åpne detaljbokser og kaster leserens posisjon — rapporten står
      // jo der. Klikket får bare fokus-semantikken sin.
      const vist = rapportRot.querySelector("h3[tabindex='-1']");
      if (vist) {
        if (fokus) vist.focus();
        return;
      }
      try {
        const { overskrift, noder } = byggRapport(rHent.siste);
        if (rHent.tegn(null, noder) && fokus) overskrift.focus();
        return;
      } catch (e) {
        // Uforventet form i cachen: fall til ekte henting.
        rHent.siste = null;
      }
    }
    // Tøm FØR henting: et feilet kall skal aldri la forrige rapport stå
    // igjen under en feilmelding som gjelder en annen — og CACHEN følger
    // DOM-en (Codex P2): sto den igjen, gjenoppsto den gamle rapporten
    // ved neste prosessbytte selv om brukeren nettopp forlot den.
    rHent.tegn(null, []);
    rHent.siste = null;
    const min = ++rHent.nr;
    let svar;
    try {
      // ÉN henting per rapport-id (Codex P2): klikker brukeren «Vis»
      // mens auto-lastingen av SAMME rapport står i lufta, deles
      // løftet — generasjonen avgjør hvem som får rendre (klikket),
      // og fokus-semantikken er kallerens.
      if (rHent.aktive.has(oppdragId)) {
        svar = await rHent.aktive.get(oppdragId);
      } else {
        const lofte = hentEvalueringsrapport(oppdragId);
        rHent.aktive.set(oppdragId, lofte);
        try {
          svar = await lofte;
        } finally {
          if (rHent.aktive.get(oppdragId) === lofte) {
            rHent.aktive.delete(oppdragId);
          }
        }
      }
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      if (min !== rHent.nr) return;
      // Ryddingen av live-regionen er også EIERSKAPS-vaktet (Codex P2):
      // etter et rutebytte kan en ANNEN flate nettopp ha annonsert der,
      // og vår tømming ville slettet dens beskjed.
      if (rapportRot.isConnected) meldLive("");
      if (fokus) rHent.tegn(t("ui.rekruttering.evalueringer.rapportfeil"), []);
      return;
    }
    if (min !== rHent.nr) return;
    // FORLATT RUTE SJEKKES FØR BYGGING (Codex P2): `rHent.tegn` dropper
    // riktignok frakoblede mål, men da hadde vi alt bygget hele
    // rapport-DOM-en (opptil 5000 rader + detaljbokser) for søpla.
    // Samme eierskapstest, bare FØR arbeidet.
    if (!rapportRot.isConnected) return;
    // RENDRINGEN LIGGER INNE I `try` (Cursor P2). 200 er ikke det samme
    // som rendrbar: mangler `rangering`, `profil` eller `nedbrytning`,
    // kastet dereferansen HER — etter at `utfall` og `rapportRot` alt var
    // tømt. Resultatet var en stille tom seksjon uten `role="alert"`,
    // samme «200-og-feiler-under-rendring»-klasse som diskriminator-
    // portene verner serversiden mot. WCAG-flaten rendrer inne i `try`;
    // ats-veien gjør nå det samme, og lander i den ærlige feiltilstanden.
    try {
      const { overskrift, noder } = byggRapport(svar);
      const tegnet = rHent.tegn(null, noder);
      if (!tegnet) return;
      rHent.siste = svar;
      // Fokus KUN på eksplisitt klikk — auto-visningen ved sidelasting
      // skal aldri stjele fokus fra der brukeren er (a11y).
      //
      // ... men STILLE er ikke det samme som skånsom (Cursor P2): uten
      // fokusflyttingen sto auto-stien helt uten annonsering, så
      // skjermleseren fikk aldri vite at produktet dukket opp. Fokus
      // hører fortsatt til klikket; beskjeden går i den høflige
      // live-regionen i stedet — samme grep som WCAG-søskenet
      // (`rapport.js`, «rapporten er klar»), bare med rangeringens egen
      // overskrift som tekst.
      if (fokus) overskrift.focus();
      else meldLive(overskrift.textContent);
    } catch (e) {
      if (min !== rHent.nr) return;
      // Halv DOM er verre enn ingen: en delvis bygget rapport ser ekte
      // ut — og en TIDLIGERE auto-annonsering skal ikke bli stående og
      // beskrive en rapport som ikke vises (CodeRabbit).
      if (rapportRot.isConnected) meldLive("");
      if (fokus) rHent.tegn(t("ui.rekruttering.evalueringer.rapportfeil"), []);
    }
  };

  const tegnListe = (evalueringer, flere, cursor) => {
    const tittel = el("h2", { id: "evaluering-tittel",
      text: t("ui.rekruttering.evalueringer.tittel") });
    // LEVENDE STATUS UTEN SIDE-RELOAD (#221): listen hentes ved mount og
    // etter bestilling — en evaluering som blir ferdig mens brukeren
    // ser på, krever ellers en omlasting av hele ruta. Knappen bruker
    // øktens egen oppfriskning (samme vei som post-bestilling), så det
    // finnes ÉN henting og én tegner. Ingen polling: en timer som
    // re-fetcher bak brukerens rygg eier ikke feilhåndteringen sin —
    // eksplisitt oppdatering er ærlig og testbar.
    // Oppslaget er LAZY: `okt.evaluering` settes først ETTER den første
    // tegningen (samme mount, synkront) — knappen finnes fra første
    // render, og klikket treffer alltid den sist monterte oppfriskeren.
    const oppdaterKnapp = okt ? (() => {
      const k = el("button", { type: "button", class: "eval-oppdater",
        text: t("ui.rekruttering.evalueringer.oppdater") });
      k.addEventListener("click", async () => {
        if (!okt.evaluering) return;
        // `oppdater()` sier fra om den faktisk TEGNET: en feilet eller
        // forkastet oppfriskning bytter ingen noder, så knappen holder
        // fokus selv — og `role="alert"` annonserer feilen.
        if (await okt.evaluering.oppdater()) {
          etterListeklikk("eval-oppdater",
            (okt.evalueringer.liste || []).length);
        }
      });
      return k;
    })() : null;
    // `null` er FEIL-tilstanden fra hentingen — en utilgjengelig
    // historikk er ikke en tom historikk.
    if (evalueringer === null) {
      sett(rot, tittel,
        el("p", { text: t("ui.rekruttering.evalueringer.listefeil") }),
        ...(oppdaterKnapp ? [oppdaterKnapp] : []),
        listeutfall, utfall, rapportRot);
      return;
    }
    if (!evalueringer.length) {
      sett(rot, tittel,
        el("p", { text: t("ui.rekruttering.evalueringer.ingen") }),
        ...(oppdaterKnapp ? [oppdaterKnapp] : []),
        listeutfall, utfall, rapportRot);
      return;
    }
    // HANDLINGENE DER DE TRENGS (eiers bestilling 29/8: vis, slett,
    // rediger, avbryt): Vis + Slett på en klar rapport, Slett på en
    // feilet, Avbryt på en som venter. Begge de irreversible går
    // gjennom Bekreftelsesdialogen, og utfallet oppfriskes fra basen —
    // aldri en optimistisk rad flaten selv har diktet.
    // EN OBSERVERT UGYLDIG RAPPORT BLIR IKKE STÅENDE (#258 P2-E,
    // Codex på #255): sier den oppfriskede listen om NETTOPP den åpne
    // rapportens oppdrag at den er slettet eller ikke lenger klar, tas
    // både visningen og klikk-cachen ned — flaten skal aldri påstå
    // «slettet» i listen og vise full kandidatrapport under, og cachen
    // skal ikke la den gjenoppstå. KUN observert: en rapport utenfor
    // det oppfriskede vinduet (paginering) er *ikke observert*, ikke
    // slettet, og røres aldri — samme grense som 069-formelen ellers:
    // basen dømmer, flaten dikter ikke.
    if (rHent.siste) {
      const egen = evalueringer.find((e2) =>
        e2.oppdrag_id === rHent.siste.oppdrag_id);
      if (egen && (egen.slettet || !egen.rapport_klar)) {
        rHent.siste = null;
        rHent.tegn(null, []);
      }
    }
    const kanSkrive = harScope(ctx, "bestilling:opprett");
    const bekreftHandling = (e2, nokkel, kall, kvittering) =>
      bekreftEvalueringshandling(e2.oppdrag_id, nokkel, kall, kvittering);
    // KORTLISTE, IKKE TABELL (eiers mobil-redesign 29/8, godkjent
    // mockup): fire kolonner på 390px ga en tabell som hverken kunne
    // leses eller treffes. Kortet bærer samme fakta i samme rekkefølge
    // — oppdrag, tidspunkt, status som PILLE (tekst, aldri bare farge —
    // port 30-regelen), handlingene som egen rad med touch-høyde.
    // Semantikken er en LISTE (ul/li): raden var aldri tabulær data,
    // den var en enhet med handlinger.
    const kort = evalueringer.map((e2) => {
      const handling = el("div", { class: "rekrut-kort-handlinger" });
      if (e2.rapport_klar) {
        const knapp = el("button", { type: "button", class: "knapp primar",
          text: t("ui.rekruttering.evalueringer.vis") });
        knapp.setAttribute("aria-label",
          t("ui.rekruttering.evalueringer.vis")
          + " — " + t("ui.rekruttering.evalueringer.oppdrag")
          + " " + e2.oppdrag_id);
        knapp.addEventListener("click", () => visRapport(e2.oppdrag_id));
        handling.append(knapp);
      }
      const venter = !e2.slettet && !e2.rapport_klar
        && !["feilet", "kansellert", "utfort"].includes(e2.status);
      if (kanSkrive && venter) {
        const avbryt = el("button", { type: "button", class: "knapp",
          text: t("ui.rekruttering.evalueringer.avbryt") });
        avbryt.setAttribute("aria-label",
          t("ui.rekruttering.evalueringer.avbryt")
          + " — " + t("ui.rekruttering.evalueringer.oppdrag")
          + " " + e2.oppdrag_id);
        avbryt.addEventListener("click", () => bekreftHandling(e2,
          "avbryt", avbrytEvaluering,
          "ui.rekruttering.evalueringer.avbrutt"));
        handling.append(avbryt);
      }
      // SLETT BETYR AT RADEN FORSVINNER (eiers funn 30/8, migrasjon
      // 071): knappen står på HVERT terminalt løp — klar rapport,
      // feilet, kansellert, utilgjengelig og alt bestilt slettet —
      // uansett om det finnes kandidatdata bak (serveren tar begge
      // delene i samme vending). Bare et AKTIVT løp mangler den:
      // veien dit er Avbryt.
      if (kanSkrive && (e2.rapport_klar || e2.slettet
          || ["feilet", "kansellert", "utfort"].includes(e2.status))) {
        const slett = el("button", { type: "button", class: "knapp fare",
          text: t("ui.rekruttering.evalueringer.slett") });
        slett.setAttribute("aria-label",
          t("ui.rekruttering.evalueringer.slett")
          + " — " + t("ui.rekruttering.evalueringer.oppdrag")
          + " " + e2.oppdrag_id);
        slett.addEventListener("click", () => bekreftHandling(e2,
          "slett", slettEvaluering,
          (svar) => (svar && svar.ingenting_lagret
            ? "ui.rekruttering.evalueringer.fjernet"
            : "ui.rekruttering.evalueringer.slett_bestilt")));
        handling.append(slett);
      }
      // Terminale statuser er sine egne sannheter — "venter" er bare for
      // oppdrag som faktisk kan bli klare. En reapet evaluering er
      // hverken klar eller underveis: fristen har makulert den (Codex
      // P2 — uten dette sto et `utfort` oppdrag som «under arbeid» i
      // det uendelige etter retensjonsgrensen).
      // «venter» er KUN for løp som kan bli klare (opprettet/plukket).
      // Et utfort oppdrag uten lesbar rapport (intet retensjonsanker —
      // eldre enn anker-fødselen) er utilgjengelig, ikke underveis.
      const art = e2.slettet ? "slettet"
        : e2.rapport_klar ? "klar"
          : (e2.status === "feilet" || e2.status === "kansellert")
            ? e2.status
            : e2.status === "utfort" ? "utilgjengelig" : "venter";
      const statusTekst = t("ui.rekruttering.evalueringer." + art);
      // Pillen bærer ARTEN som klasse for fargen og som TEKST for
      // informasjonen — samme regel som trafikklyset (port 30).
      return el("li", { class: "rekrut-kort rekrut-kort--" + art,
        "data-oppdrag": String(e2.oppdrag_id) },
        el("div", { class: "rekrut-kort-hode" },
          el("div", { class: "rekrut-kort-titler" },
            el("strong", { text: t("ui.rekruttering.evalueringer.oppdrag")
              + " " + e2.oppdrag_id }),
            Tidspunkt(e2.opprettet || "")),
          el("span", { class: "rekrut-pille rekrut-pille--" + art,
            text: statusTekst })),
        ...(handling.childNodes.length ? [handling] : []));
    });
    const liste = el("ul", { class: "rekrut-kortliste",
      "aria-label": t("ui.rekruttering.evalueringer.tabell") },
      ...kort);
    // PAGINERINGEN (#221): cursoren er serverens fortsettelse — flaten
    // regner aldri ut «neste side» selv. Klikket APPENDER: brukeren
    // mister ikke radene hen alt ser på. Generasjonen vokter mot både
    // oppfriskning og prosessbytte midt i flukten — bare den siste
    // hentingen får skrive økten.
    const eval2 = okt ? okt.evalueringer : null;
    const lastFlere = (flere && cursor && eval2) ? (() => {
      const k = el("button", { type: "button", class: "eval-last-flere",
        text: t("ui.rekruttering.evalueringer.last_flere") });
      k.addEventListener("click", async () => {
        k.disabled = true;
        // PAGINERINGEN TAR GENERASJONEN, DEN LESER DEN IKKE (Codex P2).
        // «Oppdater» og «Last flere» er to skrivere på ÉN liste, og
        // begge knappene står klikkbare. Leste pagineringen bare
        // `eval2.nr`, delte de to hentingene generasjon: rakk
        // oppfriskningen inn først, ble et cursorsvar fra den GAMLE
        // kjeden appendet på en nyhentet første side — to kjeder blandet,
        // med rader som kunne gjentas eller falle ut. Rakk pagineringen
        // inn først, ble den stille overskrevet.
        //
        // Å ta generasjonen gjør klikket til den siste intensjonen, og
        // det er nøyaktig samme regel som `oppdater()` alt følger: siste
        // klikk vinner, taperen skriver ingenting. En oppfriskning som
        // lander etterpå forkastes, og motsatt — klikker brukeren
        // «Oppdater» mens en side er i lufta, taper siden.
        const min = ++eval2.nr;
        let svar;
        try {
          svar = await hentEvalueringer(cursor);
        } catch (e) {
          if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
          // Et eldre, tapt kall skal ikke melde feil over en ferskere
          // liste — samme generasjonsregel som suksessveien.
          if (min === eval2.nr) meldListefeil(true);
          k.disabled = false;
          return;
        }
        // Taperen skriver ingenting — men den LÅSER heller ikke
        // kontrollen sin (Cursor P2). Vant en vellykket «Oppdater», er
        // `k` alt revet ut av DOM-en og linjen er en no-op. Vant en
        // FEILET «Oppdater», tegnes ingenting på nytt: da er dette den
        // eneste veien knappen kommer tilbake, og uten den står den
        // deaktivert over en liste som fortsatt har mer å hente.
        if (min !== eval2.nr) { k.disabled = false; return; }
        meldListefeil(false);
        const basis = eval2.liste !== undefined
          ? eval2.liste : (evalueringer || []);
        eval2.liste = basis.concat((svar && svar.evalueringer) || []);
        eval2.flere = !!(svar && svar.flere);
        eval2.cursor = (svar && svar.neste_cursor) || null;
        eval2.tegn(eval2.liste, eval2.flere, eval2.cursor);
        etterListeklikk("eval-last-flere", eval2.liste.length);
      });
      return k;
    })() : null;
    sett(rot, tittel,
      ...(oppdaterKnapp ? [oppdaterKnapp] : []),
      utfall,
      liste,
      listeutfall,
      // Et fullt vindu KAN bety flere — aldri stille avkorting: uten en
      // cursor å følge (eldre server, eller ingen økt å appende i) står
      // meldingen; med den står knappen.
      ...(lastFlere ? [lastFlere]
        : flere ? [el("p",
          { text: t("ui.rekruttering.evalueringer.flere") })] : []),
      rapportRot);
  };

  // Seedet kommer fra ØKTEN når en oppfriskning har vært kjørt, ellers
  // fra lastingens egen liste: et prosessbytte er en om-tegning, ikke en
  // ny lasting, og seksjonen henter ikke selv ved mount. `flere` følger
  // listen den beskriver, uansett kilde.
  const eval_ = okt ? okt.evalueringer : null;
  if (eval_ && eval_.liste !== undefined) {
    tegnListe(eval_.liste, !!eval_.flere, eval_.cursor);
  } else {
    if (eval_) eval_.cursor = (data && data.evalueringerCursor) || null;
    tegnListe(data ? data.evalueringer : [],
      !!(data && data.evalueringerFlere),
      (data && data.evalueringerCursor) || null);
  }
  // NULL KLIKK TIL PRODUKTET (eiers UX-prinsipp 27/8): finnes en ferdig
  // rapport, rendres den ferskeste med en gang — uten fokus-tyveri.
  // Kun ved mount, aldri ved oppfriskning: en levert bestilling skal
  // ikke rive lesingen av en annen rapport.
  //
  // FERSKEST ER SERVERENS EGEN NØKKEL, IKKE FØRSTE RAD (Cursor P2 →
  // Codex P2). `find` leste «ferskeste» ut av listens rekkefølge — en
  // skjult kontrakt med sorteringen i `lesing.py`, som flaten selv ikke
  // binder. Kom listen i en annen rekkefølge (en oppfrisket liste satt
  // sammen et annet sted), viste auto-stien en ELDRE rapport uten at noe
  // feilet. Valget står derfor her, eksplisitt.
  //
  // ... men det må stå på SAMME nøkkel (Codex P2): endepunktet definerer
  // nyest som `(opprettet, id)`, og de to ordenene kan divergere.
  // PostgreSQLs `now()` er TRANSAKSJONENS starttid mens id-en tildeles
  // når inserten faktisk kjører, så en forsinket eldre transaksjon kan
  // få den HØYESTE id-en. `id` alene valgte da en rapport som står lenger
  // ned i en korrekt tidssortert tabell — tabellen riktig, åpningen feil.
  // SERVEREN PEKER (eiers valg A på #258-A, 30/8): tre runder prøvde å
  // gjenskape sorteringsnøkkelen (opprettet µs, id) i klienten, og
  // JS-Date kan ikke bære den — K2 stoppet fjerde formforsøk. Svaret
  // bærer nå `ferskeste_klar_oppdrag`, målt av databasen på nøyaktig
  // nøkkelen, over hele historikken. Klienten sammenligner aldri; et
  // svar uten feltet (eldre server) åpner ærlig ingenting.
  const ferskesteKlar = data ? data.evalueringerFerskeste : null;
  // ... og kun ÉN gang per økt (Codex P2): listen er tenant-global og
  // uavhengig av valgt prosess — hvert prosessbytte bygger seksjonen på
  // nytt, og en ubetinget auto-lasting hadde re-fetchet og re-rendret
  // rapporten for hver eneste veksling. En ALT lastet rapport skal
  // likevel OVERLEVE byttet (Codex P2): den re-bygges fra øktens cache
  // inn i den nye rota — ingen henting, ingen annonsering, samme
  // rapport brukeren sto i.
  // Auto ved RUTE-INNGANG (remount-dommen): mount skjer nå bare der og
  // ved full lasting, så vilkåret er rent — finnes en klar rapport,
  // hentes den. `siste` består KUN som cache-treff for klikk
  // (immutabelt artefakt), `aktive` KUN som delt løfte per id; ingen
  // mount-rebuild og ingen aktive-logikk her, for noden — og dermed
  // enhver hengende hentings mål — overlever prosessbyttene.
  if (ferskesteKlar != null) visRapport(ferskesteKlar, { fokus: false });
  // Bestillingsseksjonen melder fra etter et definitivt `tillat` — da
  // hentes listen på nytt så det ferske oppdraget faktisk vises. Feiler
  // hentingen beholdes listen som står; dette er en oppfriskning, ikke
  // en sannhetskilde.
  if (eval_) {
    // DEN MONTERTE seksjonen er den som tegner (Codex P2). Et svar som
    // lander etter et prosessbytte tilhørte før en frakoblet DOM og ble
    // stille sluppet — da sto det leverte oppdraget usynlig til NESTE
    // bytte, selv om økten hadde det. Instansen melder seg her i stedet
    // for å bli spurt om `isConnected`: siste `tegn` vinner, og den
    // vakten trengs ikke lenger.
    eval_.tegn = tegnListe;
    // SVARET ER «TEGNET DU?» (Codex P2). Fokus og annonsering hører til
    // det eksplisitte klikket, ikke til oppfriskningen som sådan — den
    // kalles også fire-and-forget etter en bestilling, og skal aldri rive
    // fokus fra skjemaet. Knappen spør derfor om det faktisk ble byttet
    // noder; feilet eller forkastet oppfriskning bytter ingen, og da
    // holder knappen fokus selv.
    okt.evaluering = { oppdater: async () => {
      const min = ++eval_.nr;
      let svar;
      try {
        svar = await hentEvalueringer();
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return false; }
        // Også oppfriskningen etter en bestilling melder fra: en stille
        // katch her lot den nye evalueringen mangle fra listen uten at
        // noe sa hvorfor (Codex P2).
        if (min === eval_.nr) meldListefeil(true);
        return false;
      }
      // Samme regel som rapporthentingen: bare den SISTE oppfriskningen
      // får tegne — og et tregt eldre svar skal heller ikke skrive seg
      // inn i økten.
      if (min !== eval_.nr) return false;
      meldListefeil(false);
      // Oppfriskningen er FØRSTE side på nytt — cursoren følger den:
      // en beholdt fortsettelse fra en eldre liste ville pekt midt inn
      // i en historikk som nettopp fikk nye rader øverst.
      eval_.liste = (svar && svar.evalueringer) || [];
      eval_.flere = !!(svar && svar.flere);
      eval_.cursor = (svar && svar.neste_cursor) || null;
      eval_.tegn(eval_.liste, eval_.flere, eval_.cursor);
      return true;
    } };
  }
  return rot;
}


function bestillSeksjon(hoved, ctx, data, okt, laas) {
  if (!harScope(ctx, "bestilling:opprett")) return null;
  const profiler = (data && data.profiler) || [];
  const rot = el("section", { "aria-labelledby": "bestill-tittel" });
  const utfall = el("div", { role: "alert", class: "utfall" });
  const tilstand = okt.bestilling;

  if (!profiler.length) {
    sett(rot, el("h2", { id: "bestill-tittel",
      text: t("ui.rekruttering.bestill.tittel") }),
      el("p", { text: t("ui.rekruttering.bestill.ingen_profil") }));
    return rot;
  }

  // NØKKELEN HØRER TIL INTENSJONEN, IKKE TIL FLATEN (Cursor P1-2).
  // `bestillIdem` holdes til et DEFINITIVT svar — det er SP-2 og riktig
  // — men den ble bare nullstilt av serverens egen dom. Endret brukeren
  // kroppen etter et usikkert svar (nett/5xx), bar neste innsending
  // fortsatt nøkkelen til den FORRIGE intensjonen: enten `idempotens-
  // konflikt` på et endret felt, eller — verre — en replay av den gamle
  // bestillingen hvis den første POST-en faktisk commitet, slik at
  // brukeren fikk kvittering for en bestilling hun nettopp endret.
  // Husmønsteret er `bestilling.js`: første endring i et felt gjør neste
  // innsending til en ny intensjon, altså en ny nøkkel.
  const nyIntensjon = () => { tilstand.bestillIdem = null; };
  const filInp = el("input", { type: "file", id: "bestill-fil",
    accept: ".zip,application/zip", required: true });
  // EN OPPLASTET BUNT MÅ SYNES (Cursor P2-6). `inndataRef` hører til
  // ØKTEN og overlever et prosessbytte — men fil-inputen gjør ikke det:
  // etter en om-tegning sto skjemaet med tom filvelger og en bunt
  // serveren for lengst har fått, og en innsending bestilte da på en fil
  // brukeren ikke lenger kunne se. Motsatt vei var den `required`
  // filvelgeren en blindvei: nettleseren blokkerte innsendingen for en
  // fil som IKKE trengs, uten at noe på skjermen sa hvorfor.
  // Bunten står derfor navngitt over velgeren så lenge den finnes, og
  // filen kreves bare når det ikke er noen bunt å bestille på.
  const buntNotis = el("p", { class: "rekrut-bestill-bunt" });
  const visBunt = () => {
    if (tilstand.inndataRef) {
      filInp.removeAttribute("required");
      sett(buntNotis, t("ui.rekruttering.bestill.lagret_bunt")
        .replaceAll("{filnavn}", tilstand.filnavn
          || t("ui.rekruttering.bestill.bunt_uten_navn")));
    } else {
      filInp.setAttribute("required", "");
      sett(buntNotis);
    }
  };
  filInp.addEventListener("change", () => {
    // Ny fil = NY bunt: en alt reservert/opplastet bunt forkastes ved å
    // glemme referansen — serveren rydder utløpte reservasjoner selv.
    tilstand.inndataRef = null;
    tilstand.reserverIdem = null;
    tilstand.filnavn = filInp.files[0] ? filInp.files[0].name : null;
    // ... og en kjede som er i lufta for den GAMLE bunten, er ikke lenger
    // noens intensjon (Cursor P1-2): generasjonen skiller dem, så den
    // flygende opplastingen ikke skriver sin referanse inn under et
    // filnavn brukeren nettopp byttet.
    tilstand.generasjon += 1;
    // ... og en ny bunt er en ny bestilling: `inndata_ref` er et felt i
    // kroppen som alle de andre.
    nyIntensjon();
    visBunt();
  });
  const profilVelger = el("select", { id: "bestill-profil", required: true });
  // ALTERNATIVENE ER LISTEN, IKKE ET ØYEBLIKKSBILDE AV DEN (Cursor P2-2).
  // `profiler` er editorens EGET array (det mutéres, ikke byttes), så en
  // ny versjon lagret mens skjemaet står, er allerede her — velgeren
  // hadde bare aldri en vei til å si det. Byggingen bor derfor i en
  // funksjon `tegn` kan kalle igjen, uten å rive skjemaet.
  const tegnProfilvalg = () => {
    const valgt = profilVelger.value;
    sett(profilVelger, ...profiler.map((pr) => el("option",
      { value: `${pr.profil_id}@${pr.versjon}` },
      flett(t("ui.rekruttering.bestill.profilvalg"),
        { navn: pr.navn, versjon: pr.versjon }))));
    if (valgt && [...profilVelger.options].some((o) => o.value === valgt)) {
      // Valget er brukerens, og det overlever en oppfriskning av listen.
      profilVelger.value = valgt;
    } else if (valgt) {
      // Versjonen brukeren pekte på, finnes ikke lenger: velgeren faller
      // til første oppføring, og det er en ANNEN kropp — nøkkelen hørte
      // til den forrige intensjonen.
      nyIntensjon();
    }
  };
  tegnProfilvalg();
  const antallInp = el("input", { type: "number", id: "bestill-antall",
    min: "1", max: "5000", step: "1", required: true, value: "1" });
  const fristInp = el("input", { type: "number", id: "bestill-frist",
    min: "30", max: "365", step: "1" });
  // FROSSET ER FROSSET (samme grep som prosessvelgeren, A-dommen #212).
  // `disabled` er brukerens vei — nettleseren sender ingen `change` fra en
  // låst kontroll — men en invariant som bare hviler på nettleserens
  // oppførsel kan verken måles eller mutasjonstestes. Låsen spørres derfor
  // også her, og valget rulles tilbake til den profilen kroppen ble bygget
  // på, så `stillingsprofil_ref` og det brukeren ser er samme profil.
  profilVelger.addEventListener("change", () => {
    if (tilstand.paagaaende) {
      // Låsens snapshot, aldri en lokal kopi (#214): under en
      // profillagring er dette verdien slik den sto da DEN låsen ble
      // tatt — ikke null.
      const fry = laas.frossetVerdi(profilVelger);
      if (fry !== undefined) profilVelger.value = fry;
      return;
    }
    nyIntensjon();
  });
  antallInp.addEventListener("input", nyIntensjon);
  fristInp.addEventListener("input", nyIntensjon);
  const send = el("button", { type: "submit",
    text: t("ui.rekruttering.bestill.send") });
  // KROPPEN SKAL IKKE KUNNE ENDRES MENS DEN ER UNDERVEIS (Cursor P1-2).
  // Bare knappen var låst, så antall og slettefrist kunne skrives om midt
  // i en lang opplasting og utfallet vises under NYE tall. Frysen er
  // `readOnly` og ikke `disabled`, av samme grunn som `bestilling.js`
  // sier det: et låst felt beholder fokus og lesbarhet, et deaktivert
  // felt under fingeren flytter fokus og forsvinner for skjermleseren.
  //
  // ... OG PROFILEN ER ET FELT I DEN KROPPEN (Cursor P1). `stillingsprofil_ref`
  // sto igjen som det ENESTE kroppsfeltet uten lås, på en antakelse om at
  // `generasjon` dekket den — men `generasjon` bumpes bare av fil-`change`,
  // aldri av profil. Vinduet var åpent i begge ender: et bytte FØR `kropp`
  // bygges bestilte på en annen profil enn den brukeren trykket Send på, og
  // `change`-handlerens `nyIntensjon()` kastet `bestillIdem` mens POST-en
  // fortsatt sto ubesvart — da bar den retryen `usikkert_utfall` lover er
  // «samme operasjon», en FERSK nøkkel, og kunne bestille en gang til på
  // toppen av en som kanskje alt var committet. En `select` har ingen
  // `readOnly`, så låsen er `disabled`, som for knappene.
  //
  // Filvelgeren står igjen som den ene ufryste: `readOnly` gjelder ikke
  // for den heller, og der gjør nabo-flaten det samme valget som her —
  // kjeden bærer sin egen intensjon i stedet (`generasjon`), så et bytte
  // under opplasting avbryter kjeden i stedet for å binde feil bunt.
  //
  // Knappene og prosessvelgeren eies av flatens `laas` (A-dommen, #212):
  // det er den samme frysen, bare utvidet til `tegn`-utløserne, og den
  // setter `aria-busy` på DETTE skjemaet fordi `send` hører til det.
  // #214: feltene MELDES til låsen i stedet for å fryses av en lokal
  // closure — da fryser også profilarmens lås dem, og snapshotet av
  // profilvalget eies av låsen (aldri null under en fremmed frys).
  laas.meldFelt("antall", antallInp, "readOnly");
  laas.meldFelt("frist", fristInp, "readOnly");
  laas.meldFelt("profil", profilVelger, "disabled", { snapshot: true });
  const frys = (paa) => laas.frys(paa);

  const skjema = el("form", {},
    buntNotis,
    el("p", {}, el("label", { for: "bestill-fil",
      text: t("ui.rekruttering.bestill.fil") }), " ", filInp),
    el("p", {}, el("label", { for: "bestill-profil",
      text: t("ui.rekruttering.bestill.profil") }), " ", profilVelger),
    el("p", {}, el("label", { for: "bestill-antall",
      text: t("ui.rekruttering.bestill.antall") }), " ", antallInp),
    el("p", {}, el("label", { for: "bestill-frist",
      text: t("ui.rekruttering.bestill.slettefrist") }), " ", fristInp),
    el("p", {}, send));
  // Først nå har `send` et skjema — og det er skjemaet `aria-busy` hører
  // til. Meldingen fryser knappen med det samme hvis flaten alt er frosset.
  laas.meld("send", send);

  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    // ÉN KJEDE OM GANGEN — OGSÅ ETTER EN OM-TEGNING (Cursor P1-2).
    // Låsen lå i `send.disabled`, altså i ÉN knapp: byttet brukeren
    // prosess mens opplastingen sto på, bygget `tegn` et helt nytt skjema
    // med en fersk, handlingsklar knapp, og et klikk der startet kjede
    // nummer to mot den samme delte tilstanden — to reservasjoner, to
    // bestillinger, og flaten som følger det svaret som tilfeldigvis kom
    // sist. Låsen hører derfor til ØKTEN, som nøklene og bunten.
    if (tilstand.paagaaende) return;
    const fil = filInp.files[0];
    if (!fil && !tilstand.inndataRef) {
      sett(utfall, t("ui.rekruttering.bestill.mangler_fil"));
      return;
    }
    // Denne kjedens intensjon. Byttes bunten under opplastingen, flytter
    // `generasjon` seg og kjeden vet at den ikke lenger er noens.
    const min = ++tilstand.generasjon;
    tilstand.paagaaende = true;
    frys(true);
    try {
      if (!tilstand.inndataRef) {
        sett(utfall, t("ui.rekruttering.bestill.laster"));
        if (!tilstand.reserverIdem) {
          tilstand.reserverIdem = nyIdempotensnokkel();
        }
        const res = await reserverBunt(tilstand.reserverIdem);
        const bytes = await fil.arrayBuffer();
        await lastOppBunt(res.reservasjon_jti, bytes);
        // BUNTEN KAN VÆRE BYTTET UNDER OPPLASTINGEN (Cursor P1-2).
        // Skjemaet står åpent mens en stor ZIP går opp, og `change`
        // nullstiller referansen — men den flygende handleren skrev
        // likevel SIN `inndata_ref` inn etterpå, mens skjermen viste den
        // nye filen. Neste innsending bestilte da på bunt A under navnet
        // B. Kjeden tilhører intensjonen den startet på: er den forlatt,
        // stopper den her, FØR noen bestilling er sendt. Reservasjonen
        // etterlates til serverens egen opprydding, som ved enhver annen
        // avbrutt opplasting.
        if (tilstand.generasjon !== min) {
          sett(utfall, t("ui.rekruttering.bestill.avbrutt"));
          return;
        }
        // Referansen settes først når BEGGE stegene er i mål: feiler
        // opplastingen, er reservasjonen brukt/utløpende og neste
        // forsøk skal reservere på nytt (fersk nøkkel).
        tilstand.inndataRef = res.inndata_ref;
        visBunt();
      }
      if (!tilstand.bestillIdem) {
        tilstand.bestillIdem = nyIdempotensnokkel();
      }
      const kropp = { bestillingstype: "rekruttering.evaluering",
        inndata_ref: tilstand.inndataRef,
        stillingsprofil_ref: profilVelger.value,
        antall_soknader: Number(antallInp.value), omfang: "bunt" };
      if (fristInp.value !== "") {
        kropp.slettefrist_dogn = Number(fristInp.value);
      }
      // BUNTEN KVITTERINGEN GJELDER, FANGET FØR `await` (Cursor P2,
      // eierdom (b)): `tilstand.filnavn` er øktens, ikke kjedens, og
      // fil-`change` skriver den om mens POST-en står i lufta. Leses den
      // etter svaret, navngir kvitteringen filen brukeren nettopp valgte
      // — ikke den som faktisk ble bestilt. `kropp.inndata_ref` er alt
      // fanget (den er et felt i kroppen) og er fallback når økten arvet
      // en opplastet bunt uten filnavn, samme valg som `visBunt`.
      const sendtBunt = tilstand.filnavn || kropp.inndata_ref;
      const svar = await bestillEvaluering(kropp, tilstand.bestillIdem);
      // ET `200` ER IKKE EN LEVERANSE (Cursor P1-1). Beslutningsveien
      // svarer `200` også når policyen sier STOPP eller sender saken til
      // unntakskøen — uten oppdrag — og serveren lar da bunten stå
      // `lastet` med `oppdrag_id IS NULL`, altså fortsatt fri til å bli
      // bestilt av en lovlig bestilling (`test_stopp_binder_ikke_bunten`).
      // Flaten nullstilte likevel hele kjeden og sa «Bestillingen er
      // levert»: to usannheter i samme setning — leveransen som ikke
      // skjedde, og den frie bunten som ble kastet ut av økten så
      // brukeren måtte laste opp den samme ZIP-en på nytt. Nabo-flaten
      // for WCAG-bestilling (`bestilling.js: visUtfall`) har hele tiden
      // skilt de tre armene, og denne er den samme beslutningen.
      if (svar.beslutning === "tillat") {
        // Definitivt svar: kjeden er fullført — alt nullstilles, en ny
        // bestilling er en ny operasjon med ny bunt. MUTERES i eget
        // objekt, byttes aldri: handleren (og en senere tegning) holder
        // referansen til DETTE objektet — et bytte ga en stale binding
        // der gamle nøkler og en alt FORBRUKT bunt overlevde suksessen.
        //
        // ... men bare hvis kjeden fortsatt ER økten sin (Cursor P1-2):
        // rakk brukeren å velge en ny bunt mens bestillingen sto i lufta,
        // er tilstanden alt satt for DEN, og en nullstilling her ville
        // tømt hennes ferske valg — `skjema.reset()` tar filvelgeren med
        // seg. Kvitteringen skrives uansett: oppdraget er committet, og
        // det skal brukeren få vite.
        //
        // ... MEN «FÅ VITE» ER HVA, IKKE BARE AT (Cursor P2, eierdom (b)).
        // Er intensjonen forlatt, står skjemaet alt med den NYE bunten
        // mens kvitteringen gjaldt den forrige — og `bestill.sendt` sier
        // bare «levert: tillat, oppdrag N». Brukeren leste den mot filen
        // hun så, og bestilte i god tro en gang til på en bunt som enten
        // var eller ikke var den leverte. Feilarmen fikk speilingen sin i
        // `forlatt_usikkert` (`:1031`); dette er den samme setningen for
        // det VISSE utfallet: kvitteringen navngir bunten som ble sendt.
        const forlatt = tilstand.generasjon !== min;
        if (!forlatt) {
          tilstand.reserverIdem = null;
          tilstand.bestillIdem = null;
          tilstand.inndataRef = null;
          tilstand.filnavn = null;
          skjema.reset();
          visBunt();
        }
        const kvittering = (svar.oppdrag_id
          ? t("ui.rekruttering.bestill.sendt")
              .replace("{oppdrag}", String(svar.oppdrag_id))
          : t("ui.rekruttering.bestill.sendt_uten_oppdrag"))
          .replace("{beslutning}", String(svar.beslutning));
        sett(utfall, forlatt
          ? `${kvittering} ${t("ui.rekruttering.bestill.sendt_forlatt_bunt")
            .replaceAll("{filnavn}", sendtBunt)}`
          : kvittering);
        // Det leverte oppdraget skal ikke kreve en side-omlasting for å
        // vises i evalueringslisten.
        if (okt.evaluering) okt.evaluering.oppdater();
      } else {
        // STOPP/unntak: bunten er URØRT og blir stående i skjemaet, så
        // neste forsøk går på den samme reservasjonen. Det ENESTE som er
        // brukt opp, er intensjonen: serveren har dømt nøyaktig denne
        // kroppen, og et nytt forsøk under den samme nøkkelen ville bare
        // fått den samme dommen replayet. (Byttet brukeren bunt mens
        // dommen var underveis, er nøkkelen alt forkastet av `change` —
        // å sette den til `null` igjen er den samme `null`.)
        tilstand.bestillIdem = null;
        // STOPP-årsaken skal LESES OPP, ikke bare vises (§7) — samme
        // grep som `bestilling.js`: kodene er serverens strukturerte
        // begrunnelse, og faller en kode utenfor locale, står koden selv.
        const koder = (svar.begrunnelse || [])
          .map((k) => t(`kode.${k}`, k)).join(". ");
        // ... MEN «BUNTEN STÅR KLAR» ER USANT NÅR INTENSJONEN ER FORLATT
        // (Cursor P2, tredje og siste arm). Begge tekstene lover at
        // bunten ikke er brukt opp og står klar til et nytt forsøk — sant
        // for bunten dommen GJALDT, men byttet brukeren fil mens dommen
        // var underveis, er den bunten ute av skjemaet: `change` nullet
        // `inndataRef` og bumpet `generasjon`. Da peker «den står klar»
        // på en fil som verken er reservert eller lastet opp. Samme
        // løgnklasse som `sendt_forlatt_bunt` (tillat-armen) og
        // `forlatt_usikkert` (0/5xx-armen), og samme måling: `forlatt` er
        // generasjonssammenligningen de alt gjør, og `sendtBunt` (`:916`)
        // er alt fanget før `await` — ingen ny tilstand, ingen ny maskin.
        const forlatt = tilstand.generasjon !== min;
        const dom = svar.beslutning === "stopp"
          ? `${t(forlatt ? "ui.rekruttering.bestill.stoppet_forlatt"
            : "ui.rekruttering.bestill.stoppet")} ${koder}`.trim()
          : t(forlatt ? "ui.rekruttering.bestill.unntak_forlatt"
            : "ui.rekruttering.bestill.unntak");
        sett(utfall, dom.replaceAll("{filnavn}", sendtBunt));
      }
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      const definitivt = !!e && e.status >= 400 && e.status < 500;
      // ... MEN ÉN 4xx ER INGEN DOM: NØKKELEN ER BARE OPPTATT (Codex P1).
      // `utfor_bestilling` tar en SESJONSLÅS på nøkkelen med
      // `pg_try_advisory_lock` og svarer den lokale koden `OPPTATT` når
      // noen andre holder den (`bestilling.py:478-485`); endepunktet
      // oversetter den til `idempotenskonflikt` utad (`:1135-1139`) —
      // samme 409 som en ekte intensjonskonflikt. Serverens egen
      // kommentar sier hva den betyr: «ingen beslutning tas, ingen kvote
      // brennes», altså er det FØRSTE forsøket fortsatt i arbeid. Kaster
      // flaten nøkkelen her, bærer neste Send en FERSK nøkkel mens den
      // første POST-en kan committe: to oppdrag på samme bunt, to
      // kvotetrekk, eller to unntakssaker — nøyaktig det nøkkelen finnes
      // for å hindre. Flaten kan skille de to 409-ene uten å se noe
      // serveren ikke sier: den mynter aldri en nøkkel på nytt innhold
      // (`nyIntensjon` kaster den ved HVER kroppsendring), så en konflikt
      // på HENNES nøkkel er alltid den forbigående. Nøkkelen står, og
      // neste forsøk møter enten gjenspillet eller den samme låsen.
      //
      // `inndataRef != null` er STEDET, og det er en forutsetning, ikke en
      // målt gren: catchen dekker tre forespørsler, og reservasjonens egen
      // 409 bærer den samme koden med MOTSATT betydning — 058 sier at en
      // brukt/utløpt reservasjon svarer konflikt i det uendelige, så DEN
      // nøkkelen må slippes (P1-3, linjen under). Uten stedet ville
      // setningen «denne 409-en er forbigående» vært usann for
      // reservasjonsarmen. At `bestillIdem` uansett er `null` før bunten
      // er i mål (den myntes først etter opplastingen, `:899`, og kastes
      // av hver kroppsendring) gjør ikke setningen sann — den gjør bare
      // dagens feil ubemerket. Grensen er pinnet fra den andre siden av
      // reservasjonsarmens egen test.
      const opptattNokkel = definitivt && e.status === 409
        && e.kode === "idempotenskonflikt" && tilstand.inndataRef != null;
      // #215: buntlåsen er holdt — forbigående, samme nøkkeløkonomi som
      // en opptatt idempotensnøkkel. Koden alene er stedet (som for
      // `buntUbrukelig` under): bestillingsendepunktets egen.
      const buntOpptatt = definitivt && e.status === 409
        && e.kode === "inndata_opptatt";
      if (definitivt) {
        // Serveren DØMTE operasjonen — retry er en NY operasjon. En
        // reservert bunt beholdes: dommen gjaldt bestillingen, ikke
        // opplastingen.
        if (!opptattNokkel && !buntOpptatt) tilstand.bestillIdem = null;
        // EN DØD RESERVASJON MÅ KUNNE SLIPPES (Cursor P1-3). Kom dommen
        // FØR `inndataRef` ble satt, traff den reservasjonen eller
        // opplastingen — og 058 sier at en brukt/utløpt reservasjon
        // krever en NY nøkkel: den gamle svarer `idempotenskonflikt` i
        // det uendelige. Klienten satt da fast på en død nøkkel til
        // brukeren tilfeldigvis byttet fil, som er den ene handlingen
        // ingenting på skjermen ba henne om. Bare 4xx: ved status 0/5xx
        // er utfallet ukjent, og da er retry med SAMME nøkkel nettopp
        // det SP-2 finnes for.
        if (tilstand.inndataRef == null) tilstand.reserverIdem = null;
      }
      // Nettverk/5xx: begge nøklene beholdes — retry er SAMME operasjon.
      //
      // ... OG DA ER «BESTILLINGEN FEILET» EN FALSK SETNING (Cursor
      // P2-4). Samme klasse som alt er lukket for signeringen (`meldFeil`
      // over): ved status 0 nådde forespørselen kanskje aldri fram —
      // eller svaret gikk tapt ETTER at serveren commitet — og ved 5xx er
      // commit-status ukjent. Bare 4xx er serverens egen avvisning før
      // commit. Teksten for det uvisse er husets egen, ordrett den
      // signeringen bruker: den sier at utfallet er ukjent, at en ny
      // forsøk gjentar SAMME operasjon (nøkkelen står, se over), og at
      // en omlasting viser serverens tilstand.
      //
      // ... MEN «SAMME OPERASJON» ER USANT NÅR INTENSJONEN ER FORLATT
      // (Cursor P2). Filvelgeren er den ene kontrollen `frys` ikke tar, og
      // et bunt-bytte under den flygende POST-en bumper `generasjon` og
      // nullstiller `bestillIdem` — det er RIKTIG, en ny bunt er en ny
      // kropp. Men da lover `usikkert_utfall` noe flaten ikke lenger kan
      // holde: neste Send bærer en FERSK nøkkel, så et «prøv igjen» her
      // gjør den forrige bestillingen — som ved 0/5xx godt kan være
      // committet — til nummer to. Nøkkeløkonomien og teksten sier nå det
      // samme. `bestill.avbrutt` (opplastingsarmen over) duger ikke: den
      // lover at INGENTING er bestilt, og det er nettopp det vi ikke vet
      // når kallet alt var i lufta.
      //
      // ... og «bestillingen feilet» er den samme løgnen for en opptatt
      // nøkkel: ingenting feilet, det første forsøket er ikke ferdig.
      // `usikkert_utfall` duger ikke her — den åpner med at vi ikke fikk
      // svar fra serveren, og det fikk vi (409). Teksten sier derfor det
      // serveren sier: ingen dom, ingen kvote, og et nytt forsøk er den
      // SAMME operasjonen (nøkkelen står, se over).
      //
      // ... og «SJEKK FELTENE» ER LØGN NÅR DET ER BUNTEN (Cursor P2-1,
      // eierdom (c) 11:38). `inndata_ubrukelig` er ikke en dom over
      // kroppen: 058 gir ETT svar for alle de TERMINALE årsakene —
      // bunten er ukjent, utløpt, ikke ferdig lastet, eller alt bundet
      // til et annet oppdrag. Ingen av dem står i et felt brukeren kan
      // rette, så «Sjekk feltene og prøv igjen» sender henne til feil
      // sted. Nøkkelen roterer: mot en død bunt er «prøv igjen, samme
      // operasjon» en løgn, og utveien som virker — en ny fil — må STÅ
      // på skjermen (`875de8f`: en bruker som må gjette seg til
      // filbytte er nøyaktig hullet den lukket).
      //
      // DEN FORBIGÅENDE NABOEN HAR SIN EGEN KODE (#215, eierdom (b)):
      // `inndata_opptatt` betyr at en annen bestilling holder bunten
      // AKKURAT NÅ. Samme økonomi som `opptattNokkel`: nøkkelen består
      // (retry er SAMME operasjon), og teksten sier «prøv igjen om et
      // øyeblikk». Før #215 kollapset `KLIENTKODE` begge til
      // `inndata_ubrukelig`, og flaten kunne ikke velge nøkkeløkonomi
      // på koden alene.
      //
      // ... MEN «INGEN DOM, INGEN KVOTE» ER IKKE KODENS LØFTE (Codex
      // P2). Koden bæres av TO grener i `utfor_bestilling`: den vanlige
      // (låsen tas før beslutningen — da er begge deler sant) og
      // gjenopprettingen, der et alt COMMITET `TILLAT` re-tar buntlåsen
      // (`bestilling.py:617-626`) og svarer det samme når en annen
      // holder den. Der ER dommen felt og kvoten trukket, og teksten
      // ville sagt brukeren to usanne ting. Den sier derfor bare det
      // BEGGE grenene garanterer: bunten er holdt akkurat nå, og retry
      // med SAMME nøkkel er trygg.
      //
      // Koden alene er stedet her, uten `inndataRef`-vakten
      // `opptattNokkel` trenger: `idempotenskonflikt` har motsatt
      // betydning i reservasjonsarmen, mens `inndata_ubrukelig` er
      // bestillingsendepunktets alene — `inndata.py` svarer
      // `inndata_reservasjon_ugyldig`/`inndata_alt_lastet` på sine egne
      // 409-er, aldri denne.
      const buntUbrukelig = definitivt && e.status === 409
        && e.kode === "inndata_ubrukelig";
      // «SAMME OPERASJON» ER USANT OGSÅ HER NÅR INTENSJONEN ER
      // FORLATT (Codex P2). Denne armen velges på KODEN alene, uten
      // `opptattNokkel`s `inndataRef`-vakt — og `change` nuller nettopp
      // `inndataRef` samtidig som den bumper `generasjon` og forkaster
      // `bestillIdem` (`:1391`). Byttet brukeren fil mens POST-en fløy,
      // står nøkkelen altså IKKE: linjen over beholder bare en nøkkel
      // som er borte, og neste Send bærer en fersk nøkkel på en NY bunt.
      // Løftet «et nytt forsøk gjentar den SAMME operasjonen» er da
      // løgn, samme klasse som `sendt_forlatt_bunt` (tillat-armen),
      // `stoppet_forlatt`/`unntak_forlatt` (dom-armen) og
      // `forlatt_usikkert` (0/5xx-armen) — og samme måling, `forlatt`
      // under. Teksten navngir ikke bunten: `sendtBunt` (`:1563`) bor i
      // `try`, og armen her klarer seg med det `forlatt_usikkert` sier.
      const forlatt = tilstand.generasjon !== min;
      sett(utfall, t(opptattNokkel ? "ui.rekruttering.bestill.opptatt"
        : buntOpptatt
          ? (forlatt ? "ui.rekruttering.bestill.bunt_opptatt_forlatt"
            : "ui.rekruttering.bestill.bunt_opptatt")
          : buntUbrukelig ? "ui.rekruttering.bestill.bunt_ubrukelig"
            : definitivt ? "ui.rekruttering.bestill.feil"
              : forlatt ? "ui.rekruttering.bestill.forlatt_usikkert"
                : "ui.rekruttering.usikkert_utfall"));
    } finally {
      tilstand.paagaaende = false;
      // Låsen løftes på de SAMME kontrollene som tok den (A-dommen,
      // #212): `tegn`-utløserne sto frosset hele veien, så ingen
      // om-tegning rakk å gjøre `send` — eller alerten, skjemaet,
      // `visBunt` — til en frakoblet node underveis.
      frys(false);
    }
  });

  // Seksjonen kan tegnes midt i en økt der bunten alt er lastet opp
  // (prosessbytte): tilstanden bestemmer hva skjemaet sier, ikke
  // rekkefølgen den ble bygget i.
  visBunt();
  // Seksjonen kan derimot IKKE lenger tegnes midt i en kjede (A-dommen,
  // #212): utløseren som gjorde det er frosset så lenge `paagaaende`
  // står. Derfor er det ingen tilstand å gjenopprette her — `laas.meld`
  // over dekker det ene tilfellet som er igjen, en kontroll som fødes
  // mens flaten er frosset.
  // ... og det er DENNE velgeren en ny profilversjon skal nå (P2-2).
  tilstand.oppdaterProfilvalg = tegnProfilvalg;
  // Broen `frysSkjema` (eierdom B, #212 runde 6) er BORTE (#214, A):
  // feltene er meldt til låsen over, så profillagringens `laas.frys`
  // når dem uten en peker inn i denne seksjonens closure.
  sett(rot, el("h2", { id: "bestill-tittel",
    text: t("ui.rekruttering.bestill.tittel") }),
    utfall, skjema);
  return rot;
}


// ------------------------------------------------------------------
// M-8 tidsvalg (082, planen §4): kundens slot-administrasjon i
// prosessdetaljen. Tabell med caption/th scope, skjema for ny slot
// (datetime-local + kapasitet, label-bundet), deaktivering bak
// bekreftelsesdialog, utfall i role=alert. Kandidatens side er en EGEN
// flate (static/tidsvalg.html) — denne seksjonen er kundens.
// ------------------------------------------------------------------
function tidsvalgSeksjon(ctx, prosessId) {
  const rot = el("section", { "aria-labelledby": "tidsvalg-tittel",
    class: "rekrut-tidsvalg" });
  const kanSkrive = harScope(ctx, "bestilling:opprett");
  const utfall = el("div", { role: "alert",
    class: "rekrut-tidsvalg-utfall" });
  const listeRot = el("div", {});
  let idem = null;

  const tegnListe = (slots) => {
    if (!slots.length) {
      sett(listeRot, el("p", {
        text: t("ui.rekruttering.tidsvalg.ingen") }));
      return;
    }
    const rader = slots.map((slot) => {
      const tidTekst = `${Tidspunkt(slot.start).textContent} – `
        + Tidspunkt(slot.slutt).textContent;
      const celler = [
        el("td", {}, Tidspunkt(slot.start), " – ", Tidspunkt(slot.slutt)),
        el("td", { text: `${slot.antall_valgt}/${slot.kapasitet}` }),
        // Kandidat-id-ene er lagerets pseudonyme uuid-er — kortformen
        // er sporbar mot kandidatlisten uten å være et navn.
        el("td", { text: slot.valgt_av.length
          ? slot.valgt_av.map((k) => k.slice(0, 8)).join(", ") : "—" }),
        el("td", { text:
          t(`ui.rekruttering.tidsvalg.status.${slot.status}`) }),
      ];
      const handling = el("td", {});
      if (kanSkrive && slot.status === "aktiv") {
        const knapp = el("button", { type: "button", class: "fare",
          text: t("ui.rekruttering.tidsvalg.deaktiver"),
          "aria-label": `${t("ui.rekruttering.tidsvalg.deaktiver")}`
            + ` — ${tidTekst}` });
        knapp.addEventListener("click", () => {
          Bekreftelsesdialog({
            tittel: t("ui.rekruttering.tidsvalg.deaktiver_tittel"),
            tekst: flett(t("ui.rekruttering.tidsvalg.deaktiver_tekst"),
              { tid: tidTekst }),
            primarTekst: t("ui.rekruttering.tidsvalg.deaktiver"),
            farlig: true,
            paaPrimar: async () => {
              try {
                await deaktiverTidsvalgSlot(slot.slot_id);
              } catch (e) {
                if (e instanceof UautorisertFeil) {
                  ctx.paaUautorisert(); return;
                }
                // DOM 3: en slot med bekreftet valg kan ikke trekkes —
                // egen tekst, så kunden forstår at det er valget som
                // står i veien, ikke en teknisk feil.
                sett(utfall, el("span", { role: "alert",
                  text: t(e && e.kode === "tidsvalg_slot_har_valg"
                    ? "ui.rekruttering.tidsvalg.har_valg"
                    : "ui.rekruttering.tidsvalg.feil") }));
                return;
              }
              sett(utfall, flett(
                t("ui.rekruttering.tidsvalg.deaktivert"),
                { tid: tidTekst }));
              provIgjen();
            },
          });
        });
        handling.append(knapp);
      }
      celler.push(handling);
      return el("tr", {}, ...celler);
    });
    sett(listeRot, el("div", { class: "tablewrap" },
      el("table", {},
        el("caption", { text: t("ui.rekruttering.tidsvalg.caption") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col",
            text: t("ui.rekruttering.tidsvalg.kolonne_tid") }),
          el("th", { scope: "col",
            text: t("ui.rekruttering.tidsvalg.kolonne_valgt") }),
          el("th", { scope: "col",
            text: t("ui.rekruttering.tidsvalg.kolonne_valgt_av") }),
          el("th", { scope: "col",
            text: t("ui.rekruttering.tidsvalg.kolonne_status") }),
          el("th", { scope: "col",
            text: t("ui.rekruttering.tidsvalg.kolonne_handling") }))),
        el("tbody", {}, ...rader))));
  };

  const oppdater = async () => {
    const svar = await hentTidsvalg(prosessId);
    if (!rot.isConnected) return;
    tegnListe((svar && svar.slots) || []);
  };

  // FØRSTELASTINGEN ER STILLE (tekstSeksjon-dommen): 401 til
  // innloggingsveien; andre mount-feil roper ikke — en Prøv
  // igjen-knapp, og bare brukerens egne handlinger får role=alert.
  const provIgjen = () => oppdater().catch((e) => {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
    if (!rot.isConnected) return;
    const knapp = el("button", { type: "button",
      text: t("ui.rekruttering.tidsvalg.prov_igjen") });
    knapp.addEventListener("click", () => { sett(utfall); provIgjen(); });
    sett(listeRot, el("span", { class: "muted",
      text: t("ui.rekruttering.tidsvalg.liste_feil") }), " ", knapp);
  });
  provIgjen();

  const skjemaRot = el("div", {});
  if (kanSkrive) {
    const startInp = el("input", { type: "datetime-local",
      id: "tidsvalg-start" });
    const sluttInp = el("input", { type: "datetime-local",
      id: "tidsvalg-slutt" });
    const kapInp = el("input", { type: "number", id: "tidsvalg-kap",
      min: "1", max: "50", value: "1" });
    const lagre = el("button", { type: "submit",
      text: t("ui.rekruttering.tidsvalg.legg_til") });
    const skjema = el("form", {},
      el("p", {}, el("label", { for: "tidsvalg-start",
        text: t("ui.rekruttering.tidsvalg.start") }), startInp),
      el("p", {}, el("label", { for: "tidsvalg-slutt",
        text: t("ui.rekruttering.tidsvalg.slutt") }), sluttInp),
      el("p", {}, el("label", { for: "tidsvalg-kap",
        text: t("ui.rekruttering.tidsvalg.kapasitet") }), kapInp),
      el("p", {}, lagre));
    // Nøkkelen binder ETT innhold (tekstSeksjon-dommen): endres
    // feltene, er det en ny operasjon; et definitivt 4xx forbruker
    // nøkkelen, tapte svar og 5xx beholder den.
    skjema.addEventListener("input", () => { idem = null; });
    skjema.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      if (lagre.disabled) return;
      // datetime-local er sonefri — konverteres til ISO med sone her,
      // så serveren aldri må gjette kundens tidssone.
      const start = new Date(startInp.value);
      const slutt = new Date(sluttInp.value);
      if (!startInp.value || !sluttInp.value
          || !(slutt.getTime() > start.getTime())) {
        sett(utfall, el("span", { role: "alert",
          text: t("ui.rekruttering.tidsvalg.ugyldig_tid") }));
        return;
      }
      lagre.disabled = true;
      if (!idem) idem = nyIdempotensnokkel();
      try {
        await opprettTidsvalgSlots(prosessId,
          [{ start: start.toISOString(), slutt: slutt.toISOString(),
             kapasitet: Math.max(1, Math.min(50,
               Number(kapInp.value) || 1)) }], idem);
      } catch (e) {
        lagre.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (e && e.status >= 400 && e.status < 500) idem = null;
        sett(utfall, el("span", { role: "alert",
          text: t("ui.rekruttering.tidsvalg.feil") }));
        return;
      }
      idem = null;
      lagre.disabled = false;
      sett(utfall, el("span", {
        text: t("ui.rekruttering.tidsvalg.lagret") }));
      startInp.value = ""; sluttInp.value = ""; kapInp.value = "1";
      provIgjen();
    });
    sett(skjemaRot, skjema);
  }

  sett(rot,
    el("h2", { id: "tidsvalg-tittel",
      text: t("ui.rekruttering.tidsvalg.tittel") }),
    el("p", { class: "muted",
      text: t("ui.rekruttering.tidsvalg.forklaring") }),
    utfall, listeRot, skjemaRot);
  return rot;
}


function tekstSeksjon(ctx) {
  // #160: kundens utsendingstekster — kilden `flett` refererer.
  // Formen er Profiler-fanens: liste med Rediger (ny versjon) og Slett
  // (skjuling), pluss et lite skjema. Uavhengig av bestillingskjeden.
  const rot = el("section", { "aria-labelledby": "tekst-tittel" });
  const kanSkrive = harScope(ctx, "bestilling:opprett");
  const utfall = el("p", { "aria-live": "polite" });
  const liste = el("div", {});
  const skjemaRot = el("div", {});
  const tekster = [];
  let idem = null;

  const oppdaterListe = async () => {
    const svar = await hentUtsendingstekster();
    tekster.length = 0;
    for (const t_ of (svar && svar.tekster) || []) tekster.push(t_);
    tegnListe();
  };

  const aapneSkjema = (tekst) => {
    idem = null;
    const navnInp = el("input", { type: "text", id: "tekst-navn",
      maxlength: "200", value: tekst ? tekst.navn : "" });
    const kropp = el("textarea", { id: "tekst-kropp", rows: "5",
      maxlength: "4000" });
    kropp.value = tekst ? tekst.tekst : "";
    const lagre = el("button", { type: "submit",
      text: t("ui.rekruttering.tekster.lagre") });
    const avbryt = el("button", { type: "button",
      text: t("ui.rekruttering.tekster.avbryt") });
    avbryt.addEventListener("click", () => sett(skjemaRot));
    const skjema = el("form", {},
      el("p", {}, el("label", { for: "tekst-navn",
        text: t("ui.rekruttering.tekster.navn") }), navnInp),
      el("p", {}, el("label", { for: "tekst-kropp",
        text: t("ui.rekruttering.tekster.tekst") }), kropp),
      el("p", {}, lagre, " ", avbryt));
    // Nøkkelen binder ETT innhold (profil-editorens nyIntensjon-dom,
    // CodeRabbit): endres feltene, er det en ny operasjon — og et
    // definitivt 4xx-svar forbruker nøkkelen. Bare tapte svar og 5xx
    // beholder den, så retryet gjenspiller nøyaktig det som ble sendt.
    skjema.addEventListener("input", () => { idem = null; });
    skjema.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      // ÉN lagring om gangen (CodeRabbit): knappen eier vinduet — et
      // dobbeltklikk eller en redigering mens POST-en står i lufta
      // starter aldri en ny.
      if (lagre.disabled) return;
      lagre.disabled = true;
      if (!idem) idem = nyIdempotensnokkel();
      const sendtNavn = navnInp.value.trim();
      let svar;
      try {
        svar = await lagreUtsendingstekst(
          tekst ? tekst.tekst_id : null, sendtNavn, kropp.value, idem);
      } catch (e) {
        lagre.disabled = false;
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (e && e.status >= 400 && e.status < 500) idem = null;
        sett(utfall, el("span", { role: "alert",
          text: t("ui.rekruttering.tekster.feil") }));
        return;
      }
      idem = null;
      // Kvitteringen er SKRIVETS (CodeRabbit): en feilet gjenlasting av
      // listen får aldri erstatte den — den sies for seg.
      sett(utfall, flett(t("ui.rekruttering.tekster.lagret"),
        { navn: sendtNavn, versjon: svar.versjon }));
      sett(skjemaRot);
      try {
        await oppdaterListe();
      } catch {
        utfall.append(" ", el("span", { class: "muted",
          text: t("ui.rekruttering.tekster.liste_feil") }));
      }
    });
    sett(skjemaRot, skjema);
    navnInp.focus();
  };

  const tegnListe = () => {
    const rader = tekster.map((tk) => {
      const deler = [
        el("strong", { text: tk.navn }), " — ",
        t("ui.rekruttering.profiler.versjon")
          .replace("{versjon}", String(tk.versjon)),
        " · ", el("span", { class: "muted", text: tk.tekst.length > 60
          ? tk.tekst.slice(0, 60) + "…" : tk.tekst }),
      ];
      if (kanSkrive) {
        const rediger = el("button", { type: "button",
          text: t("ui.rekruttering.profiler.rediger") });
        rediger.addEventListener("click", () => aapneSkjema(tk));
        const slett = el("button", { type: "button", class: "fare",
          text: t("ui.rekruttering.profiler.slett") });
        slett.setAttribute("aria-label",
          `${t("ui.rekruttering.profiler.slett")} — ${tk.navn}`);
        slett.addEventListener("click", () => {
          Bekreftelsesdialog({
            tittel: t("ui.rekruttering.tekster.slett_tittel"),
            tekst: flett(t("ui.rekruttering.tekster.slett_tekst"),
              { navn: tk.navn }),
            primarTekst: t("ui.rekruttering.profiler.slett"),
            farlig: true,
            paaPrimar: async () => {
              try {
                await slettUtsendingstekst(tk.tekst_id);
              } catch (e) {
                if (e instanceof UautorisertFeil) {
                  ctx.paaUautorisert(); return;
                }
                sett(utfall, el("span", { role: "alert",
                  text: t("ui.rekruttering.tekster.feil") }));
                return;
              }
              sett(utfall, flett(
                t("ui.rekruttering.tekster.slettet"), { navn: tk.navn }));
              try {
                await oppdaterListe();
              } catch {
                utfall.append(" ", el("span", { class: "muted",
                  text: t("ui.rekruttering.tekster.liste_feil") }));
              }
            },
          });
        });
        deler.push(" ", rediger, " ", slett);
      }
      return el("li", {}, ...deler);
    });
    sett(liste, tekster.length
      ? el("ul", {}, rader)
      : el("p", { text: t("ui.rekruttering.tekster.ingen") }));
  };

  tegnListe();
  // FØRSTELASTINGEN ER STILLE (samme dom som prosessbyttets 401-port):
  // 401 er innlogging og eies av flaten; andre feil ved MONTERING roper
  // ikke — listen står tom med en Prøv igjen-knapp, og bare feil på
  // brukerens egne handlinger får role=alert.
  const provIgjen = () => oppdaterListe().catch((e) => {
    // Utløpt sesjon rutes til innloggingsveien (CodeRabbit) — aldri et
    // dødt, autentisert skall.
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
    const knapp = el("button", { type: "button",
      text: t("ui.rekruttering.tekster.prov_igjen") });
    knapp.addEventListener("click", () => { sett(utfall); provIgjen(); });
    sett(utfall, el("span", { class: "muted",
      text: t("ui.rekruttering.tekster.feil") }), " ", knapp);
  });
  provIgjen();
  const bunn = el("p", {});
  if (kanSkrive) {
    const ny = el("button", { type: "button",
      text: t("ui.rekruttering.tekster.ny") });
    ny.addEventListener("click", () => aapneSkjema(null));
    bunn.append(ny);
  }
  sett(rot,
    el("h2", { id: "tekst-tittel",
      text: t("ui.rekruttering.tekster.tittel") }),
    el("p", { class: "muted",
      text: t("ui.rekruttering.tekster.forklaring") }),
    utfall, liste, bunn, skjemaRot);
  return rot;
}


function profilSeksjon(hoved, ctx, data, okt, laas, paaProfilendring) {
  const profiler = (data && data.profiler) || [];
  // Cursor P2-1 (runde 2): flaten er lesbar med decisions:read, men
  // POST-ruten krever bestilling:opprett (app.py) — skrive-UI uten
  // scopet er en blindvei som først dør server-side. Samme port som
  // kanBestille i bestillingsdelen.
  const kanSkrive = harScope(ctx, "bestilling:opprett");
  // ÉN LÅS FOR BEGGE MUTASJONENE I KJEDEN (#214, A-maskinen). Gapet
  // K2-passet i #212 målte — to frysemekanismer med ulik rekkevidde,
  // og rekkevidden avhang av kalleren — er lukket i selve låsen:
  // feltene er MELDT dit (laas.meldFelt), så `laas.frys` fryser både
  // utløserne og kroppen uansett hvilken arm som tar den, og
  // snapshotet av profilvalget eies av låsen (aldri null under en
  // profillagring).
  // #214 (A): låsen kjenner feltene selv — broen inn i
  // bestillingsseksjonens closure er borte, og den aller første
  // lagringen (ingen bestillingsseksjon ennå) er samme kall.
  const frysKjeden = (paa) => laas.frys(paa);
  const rot = el("section", { "aria-labelledby": "profil-tittel" });
  const utfall = el("div", { role: "alert", class: "utfall" });
  const liste = el("div");
  const skjemaRot = el("div");
  let teller = 0;

  const oppdaterListe = async () => {
    // KUN profildelen hentes på nytt (CodeRabbit minor): en full
    // re-tegning av flaten ville visket ut kvitteringen i alerten før
    // brukeren rakk å lese den. Prosessdelen står som den sto.
    try {
      const prof = await hentJson("/v1/rekruttering/stillingsprofiler");
      profiler.length = 0;
      for (const p of (prof && prof.profiler) || []) profiler.push(p);
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
    }
    tegnListe();
    sett(skjemaRot);
    // Bestillingen leser SAMME `profiler`-array (mutert i stedet for
    // byttet over): gikk listen fra tom til ikke-tom, skal seksjonen
    // våkne. `tegn` eier vurderingen av når det er trygt.
    if (paaProfilendring) paaProfilendring();
  };

  const kravRad = (kropp, krav, paaEndring) => {
    teller += 1;
    const kid = `profil-krav-${teller}`;
    const vid = `profil-vekt-${teller}`;
    const navnInp = el("input", { type: "text", id: kid, maxlength: "120",
      required: true, value: krav ? krav.kravnavn : "" });
    const vektInp = el("input", { type: "number", id: vid, min: "0",
      max: "10", step: "1", required: true,
      value: krav ? String(krav.vekt) : "3" });
    const rad = el("tr", {},
      el("td", {}, el("label", { for: kid, class: "sr-only",
        text: t("ui.rekruttering.profiler.krav") }), navnInp),
      el("td", {}, el("label", { for: vid, class: "sr-only",
        text: t("ui.rekruttering.profiler.vekt") }), vektInp));
    const fjern = el("button", { type: "button",
      text: t("ui.rekruttering.profiler.fjern") });
    // Etiketten settes ved OPPRETTELSEN og følger feltet (CodeRabbit
    // minor): en skjermleser skal høre hvilket krav knappen fjerner FØR
    // den aktiveres, ikke etterpå.
    const settEtikett = () => fjern.setAttribute("aria-label",
      t("ui.rekruttering.profiler.fjern_krav")
        .replace("{navn}", navnInp.value || "?"));
    settEtikett();
    navnInp.addEventListener("input", settEtikett);
    // En fjernet rad er en annen kravliste, altså en annen intensjon
    // (Cursor P2-5) — feltene selv dekkes av lytteren på skjemaet.
    fjern.addEventListener("click", () => {
      rad.remove();
      if (paaEndring) paaEndring();
    });
    rad.append(el("td", {}, fjern));
    kropp.append(rad);
    return navnInp;
  };

  const aapneSkjema = (profil) => {
    teller = 0;
    // SP-2: nøkkelen hører til DETTE skjemaforsøket og holdes til et
    // DEFINITIVT svar (Cursor P1-1/P2-4): et tapt 2xx + nytt klikk skal
    // være samme operasjon — serveren replayer på nøkkelen. Først når
    // svaret kom (uansett utfall serveren har dømt), byttes den.
    //
    // ... OG NØKKELEN BINDER INNHOLDET, IKKE SKJEMAET (Cursor P2-5).
    // Etter et USIKKERT svar sto nøkkelen — riktig — men den sto også
    // når brukeren endret navnet eller vektene i mellomtiden: neste
    // lagring var en annen profilversjon under den forrige intensjonens
    // nøkkel, og serveren ville enten dømt `idempotenskonflikt` eller
    // replayet den GAMLE versjonen som om den nye var lagret. Nøkkelen
    // lages derfor ved innsending og forkastes ved enhver endring —
    // felt, ny kravrad eller fjernet kravrad — akkurat som i
    // `bestilling.js`. `null` betyr «neste innsending er en ny
    // intensjon», og `nyIntensjon` er navnet på den ene setningen.
    let idem = null;
    const nyIntensjon = () => { idem = null; };
    const navnId = "profil-navn";
    const navnInp = el("input", { type: "text", id: navnId,
      maxlength: "200", required: true,
      value: profil ? profil.navn : "" });
    const kropp = el("tbody");
    const tabell = el("table", {},
      el("caption", { text: t("ui.rekruttering.profiler.tabell") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col",
          text: t("ui.rekruttering.profiler.krav") }),
        el("th", { scope: "col",
          text: t("ui.rekruttering.profiler.vekt") }),
        el("th", { scope: "col",
          text: t("ui.rekruttering.profiler.fjern") }))),
      kropp);
    if (profil && profil.krav.length) {
      for (const k of profil.krav) kravRad(kropp, k, nyIntensjon);
    } else {
      kravRad(kropp, null, nyIntensjon);
    }
    const leggTil = el("button", { type: "button",
      text: t("ui.rekruttering.profiler.leggtil") });
    leggTil.addEventListener("click", () => {
      const inp = kravRad(kropp, null, nyIntensjon);
      nyIntensjon();
      inp.focus();
    });
    const lagre = el("button", { type: "submit",
      text: t("ui.rekruttering.profiler.lagre") });
    const avbryt = el("button", { type: "button",
      text: t("ui.rekruttering.profiler.avbryt") });
    avbryt.addEventListener("click", () => {
      sett(skjemaRot);
    });
    const skjema = el("form", {},
      el("p", {},
        el("label", { for: navnId,
          text: t("ui.rekruttering.profiler.navn") }), " ", navnInp),
      tabell, el("p", {}, leggTil, " ", lagre, " ", avbryt));
    // «Lagre» er den ANDRE veien inn i bestillingsseksjonen (A-dommen,
    // #212): den ender i `oppdaterListe` → `paaProfilendring`, som enten
    // tegner seksjonen på nytt eller bytter velgerens alternativer. Den
    // meldes derfor som utløser og fryses av samme `laas` som
    // prosessvelgeren — og fordi skjemaet åpnes på et klikk, kan den
    // fødes mens flaten alt er frosset. `laas.meld` fryser den da med det
    // samme; frysen eier `lagre.disabled` alene, så ingen feilsti kan
    // låse opp en knapp låsen holder.
    laas.meld("lagre", lagre);
    // Én lytter på skjemaet dekker navnet, hvert kravnavn og hver vekt —
    // også radene som legges til senere, siden `input` bobler.
    skjema.addEventListener("input", nyIntensjon);
    skjema.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      // ÉN MUTASJON OM GANGEN I EVALUERINGSKJEDEN (A-dommen, #212).
      // Samme vakt som bestillingens egen, og av samme grunn: `disabled`
      // er brukerens vei, men invarianten skal kunne måles. Uten den er
      // vinduet igjen åpent — en profillagring som lander midt i en
      // bestilling ville byttet velgerens alternativer og forkastet
      // `bestillIdem` mens POST-en fortsatt sto ubesvart.
      if (okt.bestilling.paagaaende) return;
      const krav = [];
      for (const rad of kropp.querySelectorAll("tr")) {
        const [ninp, vinp] = rad.querySelectorAll("input");
        if (!ninp) continue;
        krav.push({ kravnavn: ninp.value.trim(),
                    vekt: Number(vinp.value) });
      }
      if (!krav.length) {
        sett(utfall, t("ui.rekruttering.profiler.tomt_krav"));
        return;
      }
      // Låsen er FLATENS, ikke knappens: den stenger også prosessvelgeren,
      // bestillingens «Send» OG bestillingskroppen mens versjonen skrives,
      // så ingen av dem kan starte noe — eller endres — mot en profilliste
      // som er i ferd med å endre seg.
      okt.bestilling.paagaaende = true;
      frysKjeden(true);
      // Nøkkelen fødes her, med innholdet den skal binde: står den fra
      // et tidligere forsøk med SAMME innhold, gjenbrukes den — det er
      // hele SP-2-replayen.
      if (!idem) idem = nyIdempotensnokkel();
      try {
        // NAVNET KVITTERINGEN GJELDER, FANGET FØR `await` (Cursor P2-1) —
        // samme klasse som bestillingens `sendtBunt` (`:916`): live DOM ≠
        // sendt intensjon. `laas` fryser utløserne og bestillingskroppen,
        // ikke `#profil-navn`, så feltet står åpent mens POST-en er i
        // lufta. Kroppen bar navnet fra kallstart, men alerten leste det
        // PÅ NYTT etter svaret — redigerte brukeren i vinduet, navnga
        // kvitteringen en profil serveren aldri lagret. Ett uttrykk
        // dekker begge lesningene, så de ikke kan gli fra hverandre igjen.
        const sendtNavn = navnInp.value.trim();
        const svar = await lagreStillingsprofil(
          profil ? profil.profil_id : null, sendtNavn, krav, idem);
        nyIntensjon();                 // definitivt svar → ny operasjon
        sett(utfall, flett(t("ui.rekruttering.profiler.lagret"),
          { navn: sendtNavn, versjon: svar.versjon }));
        await oppdaterListe();
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        const definitivt = !!e && e.status >= 400 && e.status < 500;
        if (definitivt) {
          // Serveren DØMTE operasjonen — en retry er en NY operasjon.
          nyIntensjon();
        }
        // Nettverk/5xx: nøkkelen beholdes — retry er SAMME operasjon.
        // ... OG DA ER «KUNNE IKKE LAGRE» EN FALSK SETNING (Cursor P2-1).
        // Nøkkeløkonomien over skiller alt 4xx fra resten, men teksten
        // gjorde det ikke: ved status 0 nådde POST-en kanskje aldri fram
        // — eller svaret gikk tapt ETTER at versjonen ble skrevet — og
        // ved 5xx er commit-status ukjent. Brukeren fikk beskjed om å
        // «sjekke kravene» for en profilversjon som kunne stå lagret,
        // og et nytt forsøk så ut som en ny versjon i stedet for det
        // gjenspillet det faktisk er. Samme klasse er alt lukket for
        // signeringen (`meldFeil`) og for bestillingen (P2-4); dette er
        // den tredje mutasjonen på flaten, og den siste som løy.
        sett(utfall, t(definitivt ? "ui.rekruttering.profiler.feil"
          : "ui.rekruttering.usikkert_utfall"));
      } finally {
        // Løftes ALLTID — også på 401-veien over, som returnerer tidlig:
        // en flate som er på vei til innlogging skal ikke etterlate seg
        // en lås ingen kan se og ingen kan løfte.
        okt.bestilling.paagaaende = false;
        frysKjeden(false);
      }
    });
    sett(skjemaRot, skjema);
    navnInp.focus();
  };

  const tegnListe = () => {
    const rader = profiler.map((p) => {
      const deler = [
        el("strong", { text: p.navn }), " — ",
        t("ui.rekruttering.profiler.versjon")
          .replace("{versjon}", String(p.versjon)),
        " · ",
        p.krav.map((k) => `${k.kravnavn} ${k.vekt}`).join(", "),
      ];
      if (kanSkrive) {
        const rediger = el("button", { type: "button",
          text: t("ui.rekruttering.profiler.rediger") });
        rediger.addEventListener("click", () => aapneSkjema(p));
        deler.push(" ", rediger);
        // SLETT OGSÅ HER (eiers bestilling 30/8): samme dør og dialog
        // som evalueringslisten. Slett = raden forlater flaten og
        // Ny bestilling; versjonene består i basen, for rapportene
        // refererer dem (074 eier enveis-regelen).
        const slett = el("button", { type: "button", class: "fare",
          text: t("ui.rekruttering.profiler.slett") });
        slett.setAttribute("aria-label",
          `${t("ui.rekruttering.profiler.slett")} — ${p.navn}`);
        slett.addEventListener("click", () => {
          if (okt.bestilling.paagaaende) return;
          Bekreftelsesdialog({
            tittel: t("ui.rekruttering.profiler.slett_tittel"),
            tekst: flett(t("ui.rekruttering.profiler.slett_tekst"),
              { navn: p.navn }),
            primarTekst: t("ui.rekruttering.profiler.slett"),
            farlig: true,
            paaPrimar: async () => {
              try {
                await slettStillingsprofil(p.profil_id);
                sett(utfall, flett(
                  t("ui.rekruttering.profiler.slettet"),
                  { navn: p.navn }));
                await oppdaterListe();
              } catch (e) {
                if (e instanceof UautorisertFeil) {
                  ctx.paaUautorisert(); return;
                }
                sett(utfall, el("span", { role: "alert",
                  text: t("ui.rekruttering.profiler.slett_feil") }));
              }
            },
          });
        });
        deler.push(" ", slett);
      }
      return el("li", {}, ...deler);
    });
    sett(liste, profiler.length
      ? el("ul", {}, rader)
      : el("p", { text: t("ui.rekruttering.profiler.ingen") }));
  };

  tegnListe();
  const bunn = el("p", {});
  if (kanSkrive) {
    const ny = el("button", { type: "button",
      text: t("ui.rekruttering.profiler.ny") });
    ny.addEventListener("click", () => aapneSkjema(null));
    bunn.append(ny);
  }
  sett(rot,
    el("h2", { id: "profil-tittel",
      text: t("ui.rekruttering.profiler.tittel") }),
    utfall, liste, bunn, skjemaRot);
  return rot;
}
