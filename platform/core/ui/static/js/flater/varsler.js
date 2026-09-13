// Varsler — «noe venter på DEG», og valget om hvordan du vil høre om det.
//
// Flaten finnes fordi fire-øyne-runder sto og ventet uten at godkjenneren
// visste det; eier måtte si fra utenom systemet. Innboksen er sannheten,
// e-posten er en kopi — derfor står varselet her selv om e-posten feilet.
//
// TEKSTEN KOMMER FRA `tekstnokkel` + `parametre`, aldri fra serveren.
// Serveren lagrer nøkkelen; flaten oversetter. Da leses varselet på
// MOTTAKERENS språk, ikke på det avsenderen tilfeldigvis hadde da runden ble
// åpnet — og et språkbytte endrer gamle varsler også.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, merkVarselLest, slettVarsel, slettAlleVarsler,
         settVarselkanal, UautorisertFeil } from "../api.js";
import { Tidspunkt, TomTilstand, meldLive } from "../komponenter.js";
import { flateHode, medStatus } from "./felles.js";
import { visningsToken, erGjeldendeVisning } from "../ruter.js";

// Et varsel uten VEI TIL HANDLINGEN er bare støy. Hver art vet hvilken flate
// den hører til, så «attestering venter» kan klikkes rett til utkastet.
const RUTE_FOR_ART = {
  attestering_venter: "policyadmin",
  validering_venter: "policyadmin",
  runde_apnet: "policyadmin",
  // 044 (Codex P2): pausen og bruddstripen er PLANFANENS saker. Planen
  // bor under WCAG kontroll (eier 19/8), og hashen bærer ressurs_id som
  // mål — samleflaten åpner planfanen for et mål den ikke kjenner som
  // fanenøkkel. Pausen kan bare oppheves ett sted: på planen.
  plan_pauset: "wcagkontroll",
  plan_gjentatt_brudd: "wcagkontroll",
  // 090/091: driftsvarslene peker til driftstatusflaten, som er der
  // svaret på «hva målte den» faktisk står. `varsel.selvtest_rodt`
  // henviser dit i sin egen tekst — uten oppføringen her ville henvisningen
  // vært en instruksjon om å lete, ikke en vei.
  backupverifisering_uteblitt: "driftstatus",
  selvtest_rodt: "driftstatus",
  selvtest_uteblitt: "driftstatus",
};

function varseltekst(v) {
  // Parametrene fylles inn i nøkkelens egen tekst. Mangler en parameter, står
  // plassholderen igjen — synlig, ikke stille borte: en tom setning skjuler at
  // noe er galt, en synlig `{gjenstaar}` viser det.
  let s = t(v.tekstnokkel, v.tekstnokkel);
  for (const [k, val] of Object.entries(v.parametre || {})) {
    s = s.split(`{${k}}`).join(String(val));
  }
  return s;
}

function rad(v, paaLest, paaAapne, paaSlett) {
  const li = el("li", { class: `varselrad${v.lest ? "" : " varsel-ulest"}` });
  const tekst = el("p", { class: "varseltekst", text: varseltekst(v) });
  // ISO-STRENGEN, ikke et objekt rundt den (Codex P2). `Tidspunkt` tar
  // strengen selv, slik alle de andre flatene kaller den. Med et objekt ble
  // `new Date(...)` ugyldig, formateringen kastet, og fallbacken skrev
  // objektet både i teksten og i `datetime` — hvert varsel viste
  // «[object Object]» der tidspunktet skulle stått.
  const nar = Tidspunkt(v.opprettet);
  const knapper = el("div", { class: "varselknapper" });

  const rute = RUTE_FOR_ART[v.art];
  if (rute) {
    const g = el("button", { class: "knapp liten", type: "button",
      text: t("ui.varsler.gaa_til") });
    // Å åpne varselet ER å ha sett det. Å kreve to klikk for én erkjennelse
    // gir en innboks full av «uleste» ting folk faktisk har lest.
    g.addEventListener("click", () => paaAapne(v, rute));
    knapper.append(g);
  }
  if (!v.lest) {
    const m = el("button", { class: "knapp liten", type: "button",
      text: t("ui.varsler.merk_lest") });
    m.addEventListener("click", () => paaLest(v));
    knapper.append(m);
  }
  // SLETT STÅR SIST, og uten `primar`. Rekkefølgen er ikke pynt: «Gå til» er
  // veien til handlingen og skal møte fingeren først — den destruktive
  // knappen skal ikke ligge der tommelen lander.
  //
  // Ingen bekreftelse på ÉN rad. Den er billig å miste og dyr å bekrefte:
  // eier har mange varsler hver dag, og et ja-dialog per rad ville gjort
  // ryddingen til det arbeidet den skulle fjerne. «Slett alle» spør, fordi
  // den tar alt på én gang.
  const d = el("button", { class: "knapp liten fare", type: "button",
    text: t("ui.varsler.slett") });
  d.addEventListener("click", () => paaSlett(v));
  knapper.append(d);
  li.append(tekst, el("p", { class: "sub" }, nar), knapper);
  return li;
}


function slettAlleDel(ider, paaBekreftet) {
  const antall = ider.length;
  // TO STEG, IKKE `confirm()`. Nettleserens dialog er ikke stilbar, ikke
  // oversatt av oss, og i noen sandkasser returnerer den bare `false` — da
  // ville knappen sett ut som den ikke virket. To knapper i flaten er
  // dessuten lesbare for en skjermleser i samme flyt som resten.
  const boks = el("div", { class: "varsler-slettalle" });
  const tegnStart = () => {
    const b = el("button", { class: "knapp liten fare", type: "button",
      text: t("ui.varsler.slett_alle").replace("{n}", String(antall)) });
    b.addEventListener("click", tegnBekreft);
    sett(boks, b);
  };
  const tegnBekreft = () => {
    const ja = el("button", { class: "knapp liten fare", type: "button",
      text: t("ui.varsler.slett_alle_ja") });
    const nei = el("button", { class: "knapp liten", type: "button",
      text: t("ui.varsler.slett_alle_avbryt") });
    ja.addEventListener("click", () => { ja.disabled = true; paaBekreftet(ider); });
    nei.addEventListener("click", tegnStart);
    sett(boks,
      el("p", { class: "melding", role: "alert",
        // «1 varsler» er ikke norsk. Huset har ingen pluralhjelper (målt),
        // så entallet er sin egen nøkkel — ikke en {n} som tilfeldigvis
        // leses riktig for alle tall unntatt ett.
        text: antall === 1
          ? t("ui.varsler.slett_alle_bekreft_en")
          : t("ui.varsler.slett_alle_bekreft").replace("{n}", String(antall)) }),
      el("div", { class: "knapperad" }, ja, nei));
    // Fokus flyttes til AVBRYT, ikke til «ja». Den som traff knappen ved et
    // uhell skal ikke kunne bekrefte med samme bevegelse.
    nei.focus();
  };
  tegnStart();
  return boks;
}

function kanalvelger(kanal, paaValg) {
  // Valget eier ba om. Radiogruppe, ikke avkryssing: de to alternativene er
  // gjensidig utelukkende, og «kun portal» er et VALG — ikke fravær av et.
  const gruppe = el("fieldset", { class: "varselvalg" },
    el("legend", { text: t("ui.varsler.kanal") }));
  for (const verdi of ["epost_og_portal", "kun_portal"]) {
    const id = `varselkanal-${verdi}`;
    const inn = el("input", { type: "radio", name: "varselkanal", id,
      value: verdi });
    if (verdi === kanal) inn.checked = true;
    inn.addEventListener("change", () => { if (inn.checked) paaValg(verdi); });
    gruppe.append(el("div", { class: "varselvalg-rad" }, inn,
      el("label", { for: id, text: t(`ui.varsler.kanal.${verdi}`) })));
  }
  return gruppe;
}

export function visVarsler(hoved, ctx) {
  // Eierskapet til `hoved` (Codex P2). Alle flater rendrer inn i ETT element,
  // og en innboks-GET som er ute på nettet vet ikke at eier har navigert
  // videre. Kom svaret etterpå, tegnet innboksen seg over den nye ruten mens
  // menyvalget hennes ble stående markert — skjermen viste én flate og
  // navigasjonen en annen.
  //
  // Selve tegningen gis derfor til `medStatus`, som vokter suksess, 403 OG
  // feilveien for alle flatene. Det siste er ikke pynt: en avvist GET nådde
  // aldri tegningen, og den gamle koden erstattet ruten eier sto i med
  // innboksens feiltilstand — komplett med en «Prøv igjen» som laster noe
  // annet enn det skjermen viser. 403 er også en egen tilstand nå; før ble
  // «du har ikke tilgang» vist som en forbigående feil man kunne prøve igjen.
  const minRute = visningsToken(hoved);
  const eierSkjermen = () => erGjeldendeVisning(hoved, minRute);

  const tegn = () => medStatus(hoved, ctx,
    () => hentJson("/v1/varsel"),
    (d) => {
      const varsler = d.varsler || [];
      const liste = varsler.length
        ? el("ul", { class: "varselliste",
            "aria-label": t("ui.varsler.tittel") },
          ...varsler.map((v) => rad(v, merkLest, aapne, slettEn)))
        : TomTilstand({ tittel: t("ui.varsler.tom"),
            tekst: t("ui.varsler.tom_tekst") });
      sett(hoved,
        ...flateHode(t("ui.varsler.tittel"),
          t("ui.varsler.undertittel").replace("{n}", String(d.uleste || 0))),
        kanalvelger(d.kanal, settKanal),
        // «Slett alle» finnes bare når det ER noe å slette. En knapp som
        // alltid står der, og som ikke gjør noe i den vanligste tilstanden,
        // lærer eier å overse den.
        // ID-ENE, ikke bare antallet: bekreftelsen lover nøyaktig disse
        // radene, og kallet sletter nøyaktig dem.
        ...(varsler.length
          ? [slettAlleDel(varsler.map((v) => v.id), slettAlle)] : []),
        liste);
    });

  function merkLest(v) {
    merkVarselLest(v.id)
      .then(() => {
        meldLive(t("ui.varsler.merket_lest"));
        // Skallets teller er den samme opplysningen, ett hakk unna: står den
        // igjen på tallet fra innlastingen, teller den varsler brukeren
        // nettopp har kvittert ut. Den oppdateres uavhengig av `eierSkjermen`
        // under — skallet blir stående uansett hvor eier navigerer.
        ctx.oppdaterVarseltall?.();
        // Oppfriskningen bæres av visningen den ble startet FRA. `medStatus`
        // fanger stempelet i det den kalles, så en `tegn()` startet herfra
        // etter en navigasjon ville fanget det NYE stempelet — og dessuten
        // tegnet lastetilstanden synkront, altså vasket bort den nye ruten før
        // svaret i det hele tatt var ute. Utfallet dør ikke stille: `meldLive`
        // over sier fra uansett hvor eier står.
        if (eierSkjermen()) tegn();
      })
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        meldLive(t("ui.varsler.feilet"));
      });
  }

  function slettEn(v) {
    slettVarsel(v.id)
      .then((svar) => {
        // LES SVARET (CodeRabbit). Lå raden i et SMTP-kall, svarer døra
        // `slettet: false` og varselet blir stående — og da er «Varselet er
        // slettet» en kvittering skjermen motsier med én gang.
        meldLive(svar && svar.slettet === false
          ? t("ui.varsler.slettet_ingen") : t("ui.varsler.slettet"));
        // Var varselet ULEST, er skallets teller nå feil — samme grunn som
        // etter `merkLest`. Den oppdateres uavhengig av `eierSkjermen`:
        // skallet blir stående uansett hvor eier navigerer videre.
        ctx.oppdaterVarseltall?.();
        if (eierSkjermen()) tegn();
      })
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        meldLive(t("ui.varsler.feilet"));
      });
  }

  function slettAlle(ider) {
    slettAlleVarsler(ider)
      .then((svar) => {
        const n = Number(svar && svar.slettet) || 0;
        // ANTALLET SERVEREN FAKTISK SLETTET, ikke antallet på skjermen. En
        // rad som lå i et SMTP-kall blir stående igjen (`varsel.slett`), og
        // da er «alt er tømt» usant. Ble ingenting slettet, sier vi DET —
        // en kvittering som gratulerer med en tom handling er verre enn
        // ingen kvittering.
        meldLive(
          n === 0 ? t("ui.varsler.slettet_ingen")
            : n === 1 ? t("ui.varsler.slettet_alle_en")
              : t("ui.varsler.slettet_alle").replace("{n}", String(n)));
        ctx.oppdaterVarseltall?.();
        if (eierSkjermen()) tegn();
      })
      .catch((e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        meldLive(t("ui.varsler.feilet"));
        if (eierSkjermen()) tegn();   // tilbake til knappen, ikke til «ja»
      });
  }

  function aapne(v, rute) {
    // Merk lest FØRST, men ikke la en feilet merking stoppe navigasjonen:
    // poenget er å komme til handlingen. Et varsel som blir stående ulest er
    // en irritasjon; å ikke komme fram er en blokkering.
    //
    // Navigasjonen går gjennom HASH-EN, og bærer `ressurs_id` som mål
    // (Codex P2). Før fantes to veier: en `opts.gaaTil` med id-en, og et fall
    // tilbake til `#/<rute>` uten. I produksjon kalles hver flate med
    // `(hoved, ctx)` — callbacken fantes ALDRI der, bare i testen som skulle
    // bevise veien — så fallet var den eneste virkelige veien, og det kastet
    // id-en. Varselet navnga et utkast og sendte deg til lista over alle.
    // Nå er det én vei, og den er den samme i testen som i appen.
    merkVarselLest(v.id).catch(() => {}).then(() => {
      // Ikke ventet på: navigasjonen er poenget med knappen, og skallets
      // teller skal ikke kunne holde eier igjen på veien til handlingen.
      ctx.oppdaterVarseltall?.();
      window.location.hash = v.ressurs_id
        ? `#/${rute}/${encodeURIComponent(v.ressurs_id)}`
        : `#/${rute}`;
    });
  }

  // KANALVALGET SERIALISERES (Codex P2). To raske klikk ga før to POST-er som
  // lå på nettet samtidig, og da er det nettet — ikke brukeren — som avgjør
  // hvilken som committer sist: et tregt `kun_portal` kunne lande ETTER et
  // senere `epost_og_portal`, og da sto flaten og viste e-post påslått mens
  // serveren hadde slått den av. Begge kallene meldte «lagret», og ingen av
  // dem leste tilstanden tilbake, så det fantes ingen vei til å oppdage det.
  //
  // Tre ledd, og de trengs hver for seg: køen gjør at det SISTE klikket også
  // skriver sist; låsen gjør at det vanligste tilfellet — to klikk på rappen —
  // ikke oppstår i det hele tatt; og bare det siste valget får si «lagret», så
  // kvitteringen gjelder det som faktisk står igjen.
  let kanalko = Promise.resolve();
  let kanalventende = 0;
  let sisteKanalvalg = null;
  let kanalfeilet = false;

  function laasKanalvelger(av) {
    for (const inn of hoved.querySelectorAll('input[name="varselkanal"]')) {
      inn.disabled = av;
    }
  }

  function settKanal(kanal) {
    sisteKanalvalg = kanal;
    kanalventende += 1;
    laasKanalvelger(true);
    kanalko = kanalko
      // Språket sendes med valget (Codex P2): serveren har ingen annen kilde
      // — brukerens språk lever ellers bare i localStorage — og hun STÅR i
      // språket sitt idet hun lagrer. E-posten rendres da på samme språk som
      // portalen hun valgte det i.
      .then(() => settVarselkanal(kanal, ctx.sprak))
      .then(() => {
        // Bare kvittering for det valget som står igjen. Kom det et nytt
        // klikk mens dette var ute, er «lagret» om det gamle valget en
        // opplysning som er sann og villedende på samme tid.
        if (kanal === sisteKanalvalg) meldLive(t("ui.varsler.kanal_lagret"));
      }, (e) => {
        if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
        meldLive(t("ui.varsler.feilet"));
        kanalfeilet = true;
      })
      .then(() => {
        kanalventende -= 1;
        if (kanalventende > 0) return;   // flere valg står i kø; lås videre
        laasKanalvelger(false);
        if (!kanalfeilet) return;
        kanalfeilet = false;
        // Vis den FAKTISKE tilstanden igjen — men bare hvis eier fortsatt står
        // her. Radioknappen som viser feil valg er borte fra skjermen uansett
        // når hun har navigert videre.
        if (eierSkjermen()) tegn();
      });
  }

  tegn();
}
