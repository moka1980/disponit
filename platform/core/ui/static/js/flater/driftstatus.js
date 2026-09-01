// Driftstatus (M-10 + M-11) — plattformdriftens eget innsyn, i to
// seksjoner: backupens verifiseringshistorikk og selvtestens runder.
//
// FLATEN VISER, DEN REGNER IKKE. Hvert tall her står i en rad API-et
// leverte; alderen er regnet i BASEN, i samme skann som radene (090/091),
// nettopp for at flaten ikke skal trekke to tidspunkter fra hverandre.
// Det eneste som skjer under er PRESENTASJON: en sekundverdi vises i
// timer (én verdi delt på en konstant enhet — ikke to av svarets tall
// delt på hverandre, som er det M-16-regelen forbyr), og en maskinkode
// slås opp som setning.
//
// STATUS ER TEKST, ALDRI BARE FARGE. `gronn`/`rod`/`ikke_konfigurert`
// rendres som ord i sin egen celle. Det er et WCAG-krav (1.4.1), og det
// er også det eneste som gjør «ikke konfigurert» leselig: den tredje
// statusen er ikke et mildere rødt, og ingen fargeskala kan si det.
//
// TABELLENE ER EKTE TABELLER (m16-formen): <caption>, th scope="col" og
// en th scope="row" som navngir hver rad. Uten radoverskriften mister en
// skjermleser i tallkolonnene hvilken verifisering eller hvilken probe
// tallet gjelder.
//
// INGEN MUTASJONSKNAPPER. Verifiseringer skrives av
// `disponit-backupstatus.service` og runder av
// `disponit-selvtest.service`, hver med sin egen DB-rolle. Det finnes
// ingen HTTP-dør å tegne en knapp til — og det er en sikkerhetsdom, ikke
// en manglende funksjon.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson } from "../api.js";
import { Tidspunkt } from "../komponenter.js";
import { flateHode, medStatus } from "./felles.js";

// Statuskoden → setning. Koden er basens lukkede sett (CHECK-en i 091),
// og reserven er koden selv: en fjerde status skal bli SYNLIG som en
// ukjent kode, ikke falle ut av tabellen.
function statusTekst(kode) {
  return t(`ui.driftstatus.status.${kode}`, kode);
}

// Probens `maalt.grunn` er en maskinkode fra `platform/drift/selvtest.py`.
// Reserven er koden — en ny probe skal kunne rulles ut før oversettelsen
// finnes, og da er koden bedre enn en tom celle.
function grunnTekst(maalt) {
  const kode = maalt && maalt.grunn;
  if (!kode) return "—";
  return t(`ui.driftstatus.grunn.${kode}`, kode);
}

// Sekunder → hele timer. EN verdi omregnet til en annen enhet, ikke et
// forhold mellom to av svarets tall. Under en time vises «< 1», fordi
// «0 timer» leses som «akkurat nå» og det er ikke det tallet sier.
function alderTekst(sekunder) {
  if (typeof sekunder !== "number") return "—";
  const timer = Math.floor(sekunder / 3600);
  return timer < 1
    ? t("ui.driftstatus.under_en_time")
    : t("ui.driftstatus.timer_siden").replace("{timer}", String(timer));
}

function backupseksjon(d) {
  const seksjon = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.driftstatus.backup.tittel") }));
  const rader = (d && d.verifiseringer) || [];
  if (!rader.length) {
    // ÆRLIG TOMTILSTAND: ingen verifisering er ikke «alt i orden». Det er
    // nøyaktig tilstanden `varsle_backupverifisering_uteblitt` varsler på
    // etter 30 timer, og setningen sier det.
    seksjon.append(el("p", { class: "muted",
      text: t("ui.driftstatus.backup.ingen") }));
    return seksjon;
  }
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.driftstatus.backup.caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col",
      text: t("ui.driftstatus.backup.kolonne.backup_ts") }),
    el("th", { scope: "col",
      text: t("ui.driftstatus.backup.kolonne.verifisert") }),
    el("th", { scope: "col", text: t("ui.driftstatus.backup.kolonne.alder") }),
    el("th", { scope: "col",
      text: t("ui.driftstatus.backup.kolonne.varighet") }),
    el("th", { scope: "col",
      text: t("ui.driftstatus.backup.kolonne.tabeller") }),
    el("th", { scope: "col",
      text: t("ui.driftstatus.backup.kolonne.storrelse") }))));
  const tbody = el("tbody");
  for (const v of rader) {
    tbody.append(el("tr", {},
      // Backupens eget tidsstempel NAVNGIR raden — det er identiteten
      // tabellen er idempotent på (PK i 090).
      el("th", { scope: "row" }, Tidspunkt(v.backup_ts, {})),
      el("td", {}, Tidspunkt(v.verifisert_ts, {})),
      el("td", { text: alderTekst(v.alder_s) }),
      el("td", { text: String(v.restore_varighet_s) }),
      el("td", { text: String(v.tabeller) }),
      el("td", { text: String(v.storrelse_b) })));
  }
  tabell.append(tbody);
  seksjon.append(tabell);
  return seksjon;
}

function probetabell(kjoring) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.driftstatus.selvtest.probe_caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col",
      text: t("ui.driftstatus.selvtest.kolonne.probe") }),
    el("th", { scope: "col",
      text: t("ui.driftstatus.selvtest.kolonne.status") }),
    el("th", { scope: "col",
      text: t("ui.driftstatus.selvtest.kolonne.grunn") }))));
  const tbody = el("tbody");
  for (const p of kjoring.prober || []) {
    tbody.append(el("tr", {},
      el("th", { scope: "row", text: p.probe }),
      el("td", { text: statusTekst(p.status) }),
      el("td", { text: grunnTekst(p.maalt) })));
  }
  tabell.append(tbody);
  return tabell;
}

function historikktabell(kjoringer) {
  const tabell = el("table", { class: "kpi-tabell" },
    el("caption", { text: t("ui.driftstatus.selvtest.historikk_caption") }));
  tabell.append(el("thead", {}, el("tr", {},
    el("th", { scope: "col",
      text: t("ui.driftstatus.selvtest.kolonne.tidspunkt") }),
    el("th", { scope: "col",
      text: t("ui.driftstatus.selvtest.kolonne.samlet") }),
    el("th", { scope: "col",
      text: t("ui.driftstatus.selvtest.kolonne.rode") }))));
  const tbody = el("tbody");
  for (const k of kjoringer) {
    // Antall røde prober er en TELLING av rader i svaret, ikke en
    // utledning: den sier hvor mange av kjøringens egne prober som står
    // `rod`, og hver av dem har sin egen rad i probetabellen.
    const rode = (k.prober || []).filter((p) => p.status === "rod").length;
    tbody.append(el("tr", {},
      el("th", { scope: "row" }, Tidspunkt(k.ts, {})),
      el("td", { text: statusTekst(k.samlet) }),
      el("td", { text: String(rode) })));
  }
  tabell.append(tbody);
  return tabell;
}

function selvtestseksjon(d) {
  const seksjon = el("section", { class: "kpi-kort" },
    el("h3", { text: t("ui.driftstatus.selvtest.tittel") }));
  const kjoringer = (d && d.kjoringer) || [];
  if (!kjoringer.length) {
    // ÆRLIG TOMTILSTAND, samme grunn som over: ingen runde er den
    // tilstanden `varsle_selvtest_uteblitt` varsler på etter 3 timer.
    seksjon.append(el("p", { class: "muted",
      text: t("ui.driftstatus.selvtest.ingen") }));
    return seksjon;
  }
  // Nyeste først er dørens rekkefølge (091) — flaten sorterer ikke om.
  const siste = kjoringer[0];
  const oppsummering = el("p", {});
  sett(oppsummering,
    t("ui.driftstatus.selvtest.siste")
      .replace("{status}", statusTekst(siste.samlet)),
    " ", Tidspunkt(siste.ts, {}));
  seksjon.append(oppsummering, probetabell(siste),
    historikktabell(kjoringer));
  return seksjon;
}

export function visDriftstatus(hoved, ctx) {
  sett(hoved, ...flateHode(t("ui.driftstatus.tittel"),
    t("ui.driftstatus.undertittel")));
  const kropp = el("div", { class: "kpi-kort-liste" });
  hoved.append(kropp);
  // TO ENDEPUNKTER, ÉN STATUSRAMME. Seksjonene er to uavhengige
  // driftsfakta, men skjermen er én: to `medStatus`-kall ville gitt to
  // lastetilstander som overskrev hverandre i det samme `hoved`, og en
  // 403 på det ene ville tegnet ingen-tilgang over det andre. `Promise
  // .all` gjør de to kallene til ett utfall rammen kan eie — og siden
  // begge ruter står bak SAMME scope (`security:read` i RUTESCOPE), kan
  // de heller ikke lykkes hver for seg på en meningsfull måte.
  medStatus(hoved, ctx,
    () => Promise.all([hentJson("/v1/drift/backup"),
                       hentJson("/v1/drift/selvtest")]),
    ([backup, selvtest]) => {
      sett(hoved, ...flateHode(t("ui.driftstatus.tittel"),
        t("ui.driftstatus.undertittel")), kropp);
      sett(kropp, backupseksjon(backup), selvtestseksjon(selvtest));
    });
}
