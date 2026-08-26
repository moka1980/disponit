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
         reserverBunt, lastOppBunt, bestillEvaluering,
         nyIdempotensnokkel, UautorisertFeil } from "../api.js";
import { harScope } from "../sitekart.js";
import { DataTabell } from "../tabell.js";
import { Detaljpanel, Bekreftelsesdialog } from "../dialog.js";
import { Tidspunkt } from "../komponenter.js";
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
  return t("ui.rekruttering.prosessetikett")
    .replaceAll("{dato}", Tidspunkt(p.opprettet).textContent)
    .replaceAll("{antall}", String((p.kandidater || []).length));
}

function kortHash(hash) {
  // Kortformen i signaturdialogen (§8): nok til å pare mot listen i
  // revisjonssporet, kort nok til å leses opp.
  return `${(hash || "").slice(0, 12)}…`;
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
    bestilling: { reserverIdem: null, bestillIdem: null,
                  inndataRef: null, filnavn: null } };
  medStatus(hoved, ctx,
    async () => {
      // Profilene er TILLEGGSDATA (samme politikk som
      // `hentUtrullingForSkall`): faller de, står prosessflaten likevel
      // — editoren viser sin egen tomtilstand. 401 er kvalitativt annet
      // og skal nå innloggingsveien, som overalt ellers.
      const [pros, prof] = await Promise.all([
        hentJson("/v1/rekruttering/prosesser"),
        hentJson("/v1/rekruttering/stillingsprofiler").catch((e) => {
          if (e instanceof UautorisertFeil) throw e;
          return { profiler: [] };
        }),
      ]);
      return { ...pros, profiler: (prof && prof.profiler) || [] };
    },
    (data) => tegn(hoved, ctx, data, okt));
}

function tegn(hoved, ctx, data, okt, valgtId) {
  const prosesser = (data && data.prosesser) || [];
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
  const bestillRot = el("div", { class: "rekrut-bestill" });
  const tegnBestilling = () => {
    const del = bestillSeksjon(hoved, ctx, data, okt);
    sett(bestillRot, ...(del ? [del] : []));
  };
  tegnBestilling();
  const profilDel = profilSeksjon(hoved, ctx, data, okt, () => {
    if (!bestillRot.querySelector("form")) tegnBestilling();
  });
  const bestillDel = bestillRot.firstChild ? bestillRot : null;
  if (!prosesser.length) {
    sett(hoved, flateHode(t("ui.rekruttering.tittel")),
      el("p", { text: t("ui.rekruttering.ingen_prosess") }),
      profilDel, ...(bestillDel ? [bestillDel] : []));
    return;
  }
  // FLERE PROSESSER ER TILGJENGELIGE, IKKE BARE DEN FØRSTE (Codex P2).
  // Endepunktet er i flertall, og ruten bærer ingen prosess-id, så med
  // `prosesser[0]` var enhver senere prosess — kandidatlisten og de
  // usignerte utsendingene hennes — utilgjengelig for en tenant med mer
  // enn én pågående rekruttering. Velgeren vises bare når det FINNES noe
  // å velge mellom; valget tegner flaten på nytt for den prosessen.
  const prosess = prosesser.find((p) => p.prosess_id === valgtId)
    || prosesser[0];
  let velgerRot = null;
  if (prosesser.length > 1) {
    const velgerId = "rekrut-prosessvelger";
    const velger = el("select", { id: velgerId },
      ...prosesser.map((p) => el("option",
        { value: p.prosess_id,
          ...(p.prosess_id === prosess.prosess_id ? { selected: "" } : {}) },
        prosessetikett(p))));
    velger.value = prosess.prosess_id;
    velger.addEventListener("change", () => {
      tegn(hoved, ctx, data, okt, velger.value);
      const ny = hoved.querySelector(`#${velgerId}`);
      if (ny) ny.focus();
    });
    velgerRot = el("div", { class: "rekrut-prosessvelger" },
      el("label", { for: velgerId,
        text: t("ui.rekruttering.prosessvelger") }),
      velger);
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
        celler: {
          kandidat: kandidat.kandidat_id,
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
        handling: {
          tekst: t("ui.rekruttering.detaljer"),
          tilgjengeligNavn:
            `${t("ui.rekruttering.detaljer")}: ${kandidat.kandidat_id}`,
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
    const sporsmal = (kandidat.intervjusporsmal || []).map((s) =>
      el("li", { text: s }));
    Detaljpanel({
      tittel: `${t("ui.rekruttering.kandidat")} ${kandidat.kandidat_id}`,
      innhold: el("div", {},
        el("p", { text: `${t("ui.rekruttering.kol_poeng")}: ${poeng}` }),
        el("h3", { text: t("ui.rekruttering.funn_tittel") }),
        funn.length ? el("ul", {}, ...funn)
          : el("p", { text: t("ui.rekruttering.ingen_funn") }),
        el("h3", { text: t("ui.rekruttering.sporsmal_tittel") }),
        sporsmal.length ? el("ul", {}, ...sporsmal)
          : el("p", { text: t("ui.rekruttering.ingen_sporsmal") })),
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
      kunngjoring.textContent = t("ui.rekruttering.ny_rekkefolge")
        .replace("{forst}", rader.length ? rader[0].kandidat.kandidat_id : "");
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

  sett(hoved, flateHode(t("ui.rekruttering.tittel")), velgerRot,
    utfall, kunngjoring, blindingRot, vektRot, merknadRot, tabellRot,
    listeRot, profilDel, ...(bestillDel ? [bestillDel] : []));
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
function bestillSeksjon(hoved, ctx, data, okt) {
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
    // ... og en ny bunt er en ny bestilling: `inndata_ref` er et felt i
    // kroppen som alle de andre.
    nyIntensjon();
    visBunt();
  });
  const profilVelger = el("select", { id: "bestill-profil", required: true },
    ...profiler.map((pr) => el("option",
      { value: `${pr.profil_id}@${pr.versjon}` },
      t("ui.rekruttering.bestill.profilvalg")
        .replace("{navn}", pr.navn)
        .replace("{versjon}", String(pr.versjon)))));
  const antallInp = el("input", { type: "number", id: "bestill-antall",
    min: "1", max: "5000", step: "1", required: true, value: "1" });
  const fristInp = el("input", { type: "number", id: "bestill-frist",
    min: "30", max: "365", step: "1" });
  profilVelger.addEventListener("change", nyIntensjon);
  antallInp.addEventListener("input", nyIntensjon);
  fristInp.addEventListener("input", nyIntensjon);
  const send = el("button", { type: "submit",
    text: t("ui.rekruttering.bestill.send") });

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

  skjema.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const fil = filInp.files[0];
    if (!fil && !tilstand.inndataRef) {
      sett(utfall, t("ui.rekruttering.bestill.mangler_fil"));
      return;
    }
    send.disabled = true;
    try {
      if (!tilstand.inndataRef) {
        sett(utfall, t("ui.rekruttering.bestill.laster"));
        if (!tilstand.reserverIdem) {
          tilstand.reserverIdem = nyIdempotensnokkel();
        }
        const res = await reserverBunt(tilstand.reserverIdem);
        const bytes = await fil.arrayBuffer();
        await lastOppBunt(res.reservasjon_jti, bytes);
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
        tilstand.reserverIdem = null;
        tilstand.bestillIdem = null;
        tilstand.inndataRef = null;
        tilstand.filnavn = null;
        skjema.reset();
        visBunt();
        sett(utfall, (svar.oppdrag_id
          ? t("ui.rekruttering.bestill.sendt")
              .replace("{oppdrag}", String(svar.oppdrag_id))
          : t("ui.rekruttering.bestill.sendt_uten_oppdrag"))
          .replace("{beslutning}", String(svar.beslutning)));
      } else {
        // STOPP/unntak: bunten er URØRT og blir stående i skjemaet, så
        // neste forsøk går på den samme reservasjonen. Det ENESTE som er
        // brukt opp, er intensjonen: serveren har dømt nøyaktig denne
        // kroppen, og et nytt forsøk under den samme nøkkelen ville bare
        // fått den samme dommen replayet.
        tilstand.bestillIdem = null;
        // STOPP-årsaken skal LESES OPP, ikke bare vises (§7) — samme
        // grep som `bestilling.js`: kodene er serverens strukturerte
        // begrunnelse, og faller en kode utenfor locale, står koden selv.
        const koder = (svar.begrunnelse || [])
          .map((k) => t(`kode.${k}`, k)).join(". ");
        sett(utfall, svar.beslutning === "stopp"
          ? `${t("ui.rekruttering.bestill.stoppet")} ${koder}`.trim()
          : t("ui.rekruttering.bestill.unntak"));
      }
    } catch (e) {
      if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
      const definitivt = !!e && e.status >= 400 && e.status < 500;
      if (definitivt) {
        // Serveren DØMTE operasjonen — retry er en NY operasjon. En
        // reservert bunt beholdes: dommen gjaldt bestillingen, ikke
        // opplastingen.
        tilstand.bestillIdem = null;
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
      sett(utfall, t(definitivt ? "ui.rekruttering.bestill.feil"
        : "ui.rekruttering.usikkert_utfall"));
    } finally {
      send.disabled = false;
    }
  });

  // Seksjonen kan tegnes midt i en økt der bunten alt er lastet opp
  // (prosessbytte): tilstanden bestemmer hva skjemaet sier, ikke
  // rekkefølgen den ble bygget i.
  visBunt();
  sett(rot, el("h2", { id: "bestill-tittel",
    text: t("ui.rekruttering.bestill.tittel") }),
    utfall, skjema);
  return rot;
}


function profilSeksjon(hoved, ctx, data, okt, paaProfilendring) {
  const profiler = (data && data.profiler) || [];
  // Cursor P2-1 (runde 2): flaten er lesbar med decisions:read, men
  // POST-ruten krever bestilling:opprett (app.py) — skrive-UI uten
  // scopet er en blindvei som først dør server-side. Samme port som
  // kanBestille i bestillingsdelen.
  const kanSkrive = harScope(ctx, "bestilling:opprett");
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
    // Én lytter på skjemaet dekker navnet, hvert kravnavn og hver vekt —
    // også radene som legges til senere, siden `input` bobler.
    skjema.addEventListener("input", nyIntensjon);
    skjema.addEventListener("submit", async (ev) => {
      ev.preventDefault();
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
      lagre.disabled = true;
      // Nøkkelen fødes her, med innholdet den skal binde: står den fra
      // et tidligere forsøk med SAMME innhold, gjenbrukes den — det er
      // hele SP-2-replayen.
      if (!idem) idem = nyIdempotensnokkel();
      try {
        const svar = await lagreStillingsprofil(
          profil ? profil.profil_id : null, navnInp.value.trim(), krav,
          idem);
        nyIntensjon();                 // definitivt svar → ny operasjon
        sett(utfall, t("ui.rekruttering.profiler.lagret")
          .replace("{navn}", navnInp.value.trim())
          .replace("{versjon}", String(svar.versjon)));
        await oppdaterListe();
      } catch (e) {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        if (e && e.status >= 400 && e.status < 500) {
          // Serveren DØMTE operasjonen — en retry er en NY operasjon.
          nyIntensjon();
        }
        // Nettverk/5xx: nøkkelen beholdes — retry er SAMME operasjon.
        lagre.disabled = false;
        sett(utfall, t("ui.rekruttering.profiler.feil"));
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
