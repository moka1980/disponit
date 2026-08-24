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
import { hentJson, signerRekrutteringsliste,
         nyIdempotensnokkel, UautorisertFeil } from "../api.js";
import { harScope } from "../sitekart.js";
import { DataTabell } from "../tabell.js";
import { Detaljpanel, Bekreftelsesdialog } from "../dialog.js";
import { Tidspunkt } from "../komponenter.js";
import { medStatus, flateHode } from "./felles.js";

function meldFeil(ctx, utfall, e) {
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
  sett(utfall, t(definitivt ? "ui.rekruttering.feil_utfall"
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
  const okt = { signeringsnokler: new Map(), signerte: new Set() };
  medStatus(hoved, ctx,
    () => hentJson("/v1/rekruttering/prosesser"),
    (data) => tegn(hoved, ctx, data, okt));
}

function tegn(hoved, ctx, data, okt, valgtId) {
  const prosesser = (data && data.prosesser) || [];
  if (!prosesser.length) {
    sett(hoved, flateHode(t("ui.rekruttering.tittel")),
      el("p", { text: t("ui.rekruttering.ingen_prosess") }));
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
  const utfall = el("div", { role: "alert", class: "rekrut-utfall" });
  // Re-rangeringens kunngjøring: høflig, aldri avbrytende.
  const kunngjoring = el("div", { "aria-live": "polite",
    class: "sr-only rekrut-kunngjoring" });

  function poengFor(kandidat) {
    // Samme regel som evaluering.ranger: vekt teller når kravet er
    // oppfylt. `oppfylt` er serverens dom; vekten er brukerens valg.
    let sum = 0;
    for (const [krav, vekt] of Object.entries(vekter)) {
      if (kandidat.oppfylt && kandidat.oppfylt[krav]) sum += Number(vekt);
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
    const funn = (kandidat.funn || []).map((f) =>
      el("li", {},
        el("strong", { text: t(`ui.rekruttering.funn.${f.kategori}`) }),
        " — ",
        el("q", { text: f.kilde.sitat })));
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
            sett(utfall, t("ui.rekruttering.signer_utfall")
              .replaceAll("{hash}", kortHash(svar.innhold_hash
                || liste.innhold_hash)));
          } catch (e) {
            meldFeil(ctx, utfall, e);
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
    listeRot);
  tegnTabell();
}
