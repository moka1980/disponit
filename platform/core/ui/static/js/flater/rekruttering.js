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
         hentEvalueringer, hentEvalueringsrapport,
         nyIdempotensnokkel, UautorisertFeil } from "../api.js";
import { harScope } from "../sitekart.js";
import { DataTabell } from "../tabell.js";
import { Detaljpanel, Bekreftelsesdialog } from "../dialog.js";
import { Tidspunkt, meldLive } from "../komponenter.js";
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
    // `frysSkjema` er bestillingsseksjonens egen `frys`, lagt der
    // profillagringen kan nå den (eierdom B, #212 runde 6): kroppen —
    // profil, antall, frist — eies av seksjonen, så en lås som bare går
    // gjennom `laas` fryser utløserne og lar feltene stå åpne.
    bestilling: { reserverIdem: null, bestillIdem: null,
                  inndataRef: null, filnavn: null,
                  paagaaende: false, generasjon: 0,
                  oppdaterProfilvalg: null, frysSkjema: null },
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
    evalueringer: { liste: undefined, flere: false, nr: 0, tegn: null },
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
               evalueringerFlere: !!(evals && evals.flere) };
    },
    (data) => {
      // En fersk full lasting ER sannheten — også «Prøv igjen» etter en
      // feilet lasting. Oppfriskningscachen fra forrige lasting skal
      // aldri vinne over den, og en oppfriskning som fortsatt er i lufta
      // skal ikke lande oppå den ferske listen: generasjonen bumpes.
      okt.evalueringer.liste = undefined;
      okt.evalueringer.flere = false;
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
  const laas = {
    frys: (paa) => {
      for (const k of Object.values(utlosere)) frysEn(k, paa);
    },
    // Kontrollene fødes til ulik tid — profilskjemaet åpnes på et klikk —
    // så en som meldes mens flaten er frosset, fryses med det samme.
    // `paagaaende` ER den tilstanden; en egen `frosset`-kopi ville bare
    // vært en til å holde i takt.
    meld: (navn, kontroll) => {
      utlosere[navn] = kontroll;
      if (okt.bestilling.paagaaende) frysEn(kontroll, true);
    },
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
  if (!prosesser.length) {
    // PRODUKTET FØRST (eiers UX-prinsipp 27/8: færrest mulig klikk
    // til produktet): rapportene øverst, administrasjonen under.
    sett(hoved, flateHode(t("ui.rekruttering.tittel")), evalDel, hoppAnker,
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
  // Feilveien for prosessbyttet (#183): `role="alert"`, fordi et bytte som
  // ikke gikk gjennom er noe brukeren må vite FØR hun leser videre.
  // EGEN KLASSE, ikke `rekrut-utfall`: den klassen er signeringens og
  // blindingens utfallsområde, og flere tester slår den opp med
  // `querySelector(".rekrut-utfall")` — altså FØRSTE treff i DOM-en.
  // Velgeren står over dem, så en gjenbruk her ville kapret oppslaget og
  // gitt en tom node der utfallet skulle stått. Målt: den gjorde det.
  const velgerFeil = el("div", { role: "alert", class: "rekrut-velgerfeil" });
  if (prosesser.length > 1) {
    const velgerId = "rekrut-prosessvelger";
    const velger = el("select", { id: velgerId },
      ...prosesser.map((p) => el("option",
        { value: p.prosess_id,
          ...(p.prosess_id === prosess.prosess_id ? { selected: "" } : {}) },
        prosessetikett(p))));
    velger.value = prosess.prosess_id;
    velger.addEventListener("change", () => {
      // FROSSET ER FROSSET (A-dommen, #212). `disabled` er brukerens vei:
      // nettleseren sender ingen `change` fra en låst kontroll. Men hele
      // poenget med A er at om-tegningen ikke SKJER mens kjeden eier
      // seksjonen, og en invariant som bare hviler på nettleserens
      // oppførsel kan verken måles eller mutasjonstestes. Dommen står
      // derfor også her, ett sted fra: låsen spørres, valget rulles
      // tilbake til den prosessen som faktisk vises.
      if (okt.bestilling.paagaaende) {
        velger.value = prosess.prosess_id;
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
      velger.value = prosess.prosess_id;
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
    // Ingen intervjuspørsmål i detaljpanelet (eiers produktbeslutning
    // 27/8): de hører til innkallingen av de beste, ikke utvelgelsen.
    // Lageret består; shortlist-arcen (#225) henter derfra.
    Detaljpanel({
      tittel: `${t("ui.rekruttering.kandidat")} ${kandidat.kandidat_id}`,
      innhold: el("div", {},
        el("p", { text: `${t("ui.rekruttering.kol_poeng")}: ${poeng}` }),
        el("h3", { text: t("ui.rekruttering.funn_tittel") }),
        funn.length ? el("ul", {}, ...funn)
          : el("p", { text: t("ui.rekruttering.ingen_funn") })),
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

  // PRODUKTET FØRST (eiers UX-prinsipp 27/8): evalueringene og den
  // ferdige rapporten øverst — prosessdypdykk og administrasjon under.
  sett(hoved, flateHode(t("ui.rekruttering.tittel")), evalDel, hoppAnker,
    velgerRot,
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
// M-57s egen rapportflate ("ats"): bestilte evalueringer med status,
// og den promoterte, blindede rangeringsrapporten — lesbar for alle med
// decisions:read (evidensen bak en beslutning tenanten selv bestilte).
function evalueringSeksjon(hoved, ctx, data, okt) {
  const rot = el("section", { "aria-labelledby": "evaluering-tittel" });
  const utfall = el("div", { role: "alert", class: "utfall" });
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
  const byggRapport = (svar) => {
      const rapport = svar.rapport;
      const kropp = el("tbody", {}, ...rapport.rangering.map((rad) =>
        el("tr", {},
          el("th", { scope: "row", text: rad.kandidat_id }),
          el("td", { text: String(rad.poeng) }),
          el("td", { text: Object.entries(rad.nedbrytning)
            .map(([k, v]) => `${t(`ui.rekruttering.krav.${k}`, k)}: ${v}`)
            .join(", ") }))));
      const tabell = el("table", {},
        el("caption", { text: t("ui.rekruttering.evalueringer.rangering")
          .replace("{navn}", rapport.profil.navn)
          .replace("{versjon}", String(rapport.profil.versjon)) }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col",
            text: t("ui.rekruttering.evalueringer.kandidat") }),
          el("th", { scope: "col",
            text: t("ui.rekruttering.evalueringer.poeng") }),
          el("th", { scope: "col",
            text: t("ui.rekruttering.evalueringer.nedbrytning") }))),
        kropp);
      // Skjemaet tillater 5000 kandidater à 100 funn + 20 spørsmål — en
      // gyldig maksrapport ville bygget hundretusener av noder opp front.
      // Kroppen bygges derfor først når leseren åpner den.
      const detaljer = rapport.rangering.map((rad) => {
        const boks = el("details", {},
          el("summary", { text: t("ui.rekruttering.evalueringer.detaljer")
            .replace("{kandidat}", rad.kandidat_id) }));
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
          boks.append(
            el("h4", { text: t("ui.rekruttering.evalueringer.funn") }), funn);
        });
        return boks;
      });
      // Rapporten settes inn ETTER tabellen brukeren sto i — fokusér
      // overskriften, ellers får tastatur/skjermleser aldri vite at
      // lastingen ble ferdig.
      const overskrift = el("h3", { tabindex: "-1",
        text: t("ui.rekruttering.evalueringer.rangering")
          .replace("{navn}", rapport.profil.navn)
          .replace("{versjon}", String(rapport.profil.versjon)) });
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
    return { overskrift, noder: [
      overskrift,
      hoppLenke,
      el("p", { text: t("ui.rekruttering.evalueringer.blindet") }),
      el("div", { class: "tablewrap" }, tabell), ...detaljer] };
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

  const tegnListe = (evalueringer, flere) => {
    const tittel = el("h2", { id: "evaluering-tittel",
      text: t("ui.rekruttering.evalueringer.tittel") });
    // `null` er FEIL-tilstanden fra hentingen — en utilgjengelig
    // historikk er ikke en tom historikk.
    if (evalueringer === null) {
      sett(rot, tittel,
        el("p", { text: t("ui.rekruttering.evalueringer.listefeil") }),
        utfall, rapportRot);
      return;
    }
    if (!evalueringer.length) {
      sett(rot, tittel,
        el("p", { text: t("ui.rekruttering.evalueringer.ingen") }),
        utfall, rapportRot);
      return;
    }
    const rader = evalueringer.map((e2) => {
      const handling = el("td");
      if (e2.rapport_klar) {
        const knapp = el("button", { type: "button",
          text: t("ui.rekruttering.evalueringer.vis") });
        knapp.setAttribute("aria-label",
          t("ui.rekruttering.evalueringer.vis")
          + " — " + t("ui.rekruttering.evalueringer.oppdrag")
          + " " + e2.oppdrag_id);
        knapp.addEventListener("click", () => visRapport(e2.oppdrag_id));
        handling.append(knapp);
      }
      // Terminale statuser er sine egne sannheter — "venter" er bare for
      // oppdrag som faktisk kan bli klare. En reapet evaluering er
      // hverken klar eller underveis: fristen har makulert den (Codex
      // P2 — uten dette sto et `utfort` oppdrag som «under arbeid» i
      // det uendelige etter retensjonsgrensen).
      // «venter» er KUN for løp som kan bli klare (opprettet/plukket).
      // Et utfort oppdrag uten lesbar rapport (intet retensjonsanker —
      // eldre enn anker-fødselen) er utilgjengelig, ikke underveis.
      const statusTekst = e2.slettet
        ? t("ui.rekruttering.evalueringer.slettet")
        : e2.rapport_klar
          ? t("ui.rekruttering.evalueringer.klar")
          : (e2.status === "feilet" || e2.status === "kansellert")
            ? t("ui.rekruttering.evalueringer." + e2.status)
            : e2.status === "utfort"
              ? t("ui.rekruttering.evalueringer.utilgjengelig")
              : t("ui.rekruttering.evalueringer.venter");
      return el("tr", {},
        el("th", { scope: "row", text: String(e2.oppdrag_id) }),
        el("td", {}, Tidspunkt(e2.opprettet || "")),
        el("td", { text: statusTekst }),
        handling);
    });
    const liste = el("table", {},
      el("caption", { text: t("ui.rekruttering.evalueringer.tabell") }),
      el("thead", {}, el("tr", {},
        el("th", { scope: "col",
          text: t("ui.rekruttering.evalueringer.oppdrag") }),
        el("th", { scope: "col",
          text: t("ui.rekruttering.evalueringer.bestilt") }),
        el("th", { scope: "col",
          text: t("ui.rekruttering.evalueringer.status") }),
        el("th", { scope: "col",
          text: t("ui.rekruttering.evalueringer.vis") }))),
      el("tbody", {}, ...rader));
    sett(rot, tittel, utfall,
      el("div", { class: "tablewrap" }, liste),
      // Et fullt vindu KAN bety flere — aldri stille avkorting. Selve
      // pagineringen bor i #221; her sies det bare fra.
      ...(flere ? [el("p",
        { text: t("ui.rekruttering.evalueringer.flere") })] : []),
      rapportRot);
  };

  // Seedet kommer fra ØKTEN når en oppfriskning har vært kjørt, ellers
  // fra lastingens egen liste: et prosessbytte er en om-tegning, ikke en
  // ny lasting, og seksjonen henter ikke selv ved mount. `flere` følger
  // listen den beskriver, uansett kilde.
  const eval_ = okt ? okt.evalueringer : null;
  if (eval_ && eval_.liste !== undefined) {
    tegnListe(eval_.liste, !!eval_.flere);
  } else {
    tegnListe(data ? data.evalueringer : [],
      !!(data && data.evalueringerFlere));
  }
  // NULL KLIKK TIL PRODUKTET (eiers UX-prinsipp 27/8): finnes en ferdig
  // rapport, rendres den ferskeste med en gang — uten fokus-tyveri.
  // Kun ved mount, aldri ved oppfriskning: en levert bestilling skal
  // ikke rive lesingen av en annen rapport.
  //
  // FERSKEST ER HØYESTE OPPDRAG, IKKE FØRSTE RAD (Cursor P2). `find`
  // leste «ferskeste» ut av listens rekkefølge — en skjult kontrakt med
  // `ORDER BY o.id DESC` i `lesing.py`, som flaten selv ikke binder.
  // Kom listen noen gang i en annen rekkefølge (annen sortering, en
  // oppfrisket liste satt sammen et annet sted), viste auto-stien en
  // ELDRE rapport uten at noe feilet. Valget står derfor her, eksplisitt.
  const seedListe = (eval_ && eval_.liste !== undefined)
    ? eval_.liste : (data ? data.evalueringer : []);
  const klarRad = (seedListe || []).reduce((beste, e2) =>
    (e2.rapport_klar && (!beste || e2.oppdrag_id > beste.oppdrag_id))
      ? e2 : beste, null);
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
  if (klarRad) visRapport(klarRad.oppdrag_id, { fokus: false });
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
    okt.evaluering = { oppdater: async () => {
      const min = ++eval_.nr;
      let svar;
      try {
        svar = await hentEvalueringer();
      } catch (e) {
        if (e instanceof UautorisertFeil) ctx.paaUautorisert();
        return;
      }
      // Samme regel som rapporthentingen: bare den SISTE oppfriskningen
      // får tegne — og et tregt eldre svar skal heller ikke skrive seg
      // inn i økten.
      if (min !== eval_.nr) return;
      eval_.liste = (svar && svar.evalueringer) || [];
      eval_.flere = !!(svar && svar.flere);
      eval_.tegn(eval_.liste, eval_.flere);
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
      t("ui.rekruttering.bestill.profilvalg")
        .replace("{navn}", pr.navn)
        .replace("{versjon}", String(pr.versjon)))));
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
    if (tilstand.paagaaende) { profilVelger.value = frossetProfil; return; }
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
  let frossetProfil = null;
  const frys = (paa) => {
    antallInp.readOnly = paa;
    fristInp.readOnly = paa;
    profilVelger.disabled = paa;
    // Valget slik det sto da kjeden tok låsen: det er DENNE profilen
    // `kropp` bygges med, og den `change`-vakten over ruller tilbake til.
    frossetProfil = paa ? profilVelger.value : null;
    laas.frys(paa);
  };

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
  // ... og det er DENNE frysen den ANDRE mutasjonen i kjeden skal ta
  // (Cursor P2-1, eierdom B): `laas` eier utløserne, seksjonen eier
  // kroppen. Låsen er hel bare når profillagringen når begge.
  tilstand.frysSkjema = frys;
  sett(rot, el("h2", { id: "bestill-tittel",
    text: t("ui.rekruttering.bestill.tittel") }),
    utfall, skjema);
  return rot;
}


function profilSeksjon(hoved, ctx, data, okt, laas, paaProfilendring) {
  const profiler = (data && data.profiler) || [];
  // Cursor P2-1 (runde 2): flaten er lesbar med decisions:read, men
  // POST-ruten krever bestilling:opprett (app.py) — skrive-UI uten
  // scopet er en blindvei som først dør server-side. Samme port som
  // kanBestille i bestillingsdelen.
  const kanSkrive = harScope(ctx, "bestilling:opprett");
  // ÉN LÅS FOR BEGGE MUTASJONENE I KJEDEN (Cursor P2-1, eierdom B i
  // runde 6). A-dommen lover én lås for bestilling OG profillagring, men
  // `laas.frys` tar bare `tegn`-utløserne: bestillingens KROPP — profil,
  // antall, frist — eies av seksjonens egen `frys`. Profilarmen frøs
  // derfor utløserne og lot feltene stå åpne, samtidig som `laas.frys`
  // satte `aria-busy` på bestillingsskjemaet gjennom `send`: skjemaet
  // PÅSTO opptatt og tok input. Verst traff det profilvelgeren, som ruller
  // et bytte tilbake til `frossetProfil` — en verdi bare seksjonens `frys`
  // setter, så under en profillagring rullet den tilbake til `null` og
  // TØMTE valget i stedet for å bevare det. Seksjonen eksponerer nå sin
  // egen `frys` på økten, og armen her tar hele låsen gjennom den.
  // Uten bestillingsseksjon — ingen profiler ennå, altså den aller første
  // lagringen — er `laas` alt som finnes å låse.
  const frysKjeden = (paa) => {
    if (okt.bestilling.frysSkjema) okt.bestilling.frysSkjema(paa);
    else laas.frys(paa);
  };
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
        sett(utfall, t("ui.rekruttering.profiler.lagret")
          .replace("{navn}", sendtNavn)
          .replace("{versjon}", String(svar.versjon)));
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
