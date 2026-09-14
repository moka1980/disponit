import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { ApiFeil, UautorisertFeil, erPlattformeier, hentPlattformFirmaer,
         oppdaterPlattformFirma, opprettPlattformFirma,
         settPlattformFirmastatus } from "../api.js";
import { meldLive } from "../komponenter.js";
import { flateHode } from "./felles.js";
import { FASEOVERSIKT, MODULOVERSIKT, modulmerke, planEtikett, plattformTelling }
  from "../plattformdata.js";
import { byggRuter, erPlattformdrift, kanForvaltePolicy } from "../sitekart.js";
import { siteFaseMerke, siteModuleKort } from "../sitekomponenter.js";


// ---------------------------------------------------------------------------
// PLATTFORMEIERENS FIRMAADMINISTRASJON (199).
//
// Eier: «Jeg er … eier … og skal også ha mulighet til å opprette nye firmaer,
// redigere og slette.»
//
// FULLMAKTEN ER IKKE ET SCOPE, og derfor kan ikke menyen avgjøre dette.
// Rutene krever `policy:read` bare for å slippe forbi den generelle porten;
// det er raden i `plattformeier` som bestemmer, og den kjenner bare serveren.
// Seksjonen spør derfor `/v1/plattform/meg` FØRST, og tegner ingenting hvis
// svaret er nei — ikke en tom tabell, ikke en «du mangler tilgang», bare
// ingenting. En seksjon som forteller deg at den finnes, er en opplysning om
// systemet den som ikke skal se den ikke trenger.
//
// Den bor på adminflaten fordi det er flaten som allerede heter «Plattform»
// i toppnavigasjonen. Én ekstra henting, og bare når noen åpner den siden.
function plattformseksjon(ctx) {
  const boks = el("section", { class: "kort" });
  boks.hidden = true;

  const meld = (nokkel) => {
    const m = el("p", { class: "melding", role: "status", text: t(nokkel) });
    boks.prepend(m);
    meldLive(t(nokkel));
  };

  const feilkode = (e) => {
    if (e instanceof UautorisertFeil) { ctx.paaUautorisert?.(); return null; }
    if (e instanceof ApiFeil && e.kode === "firma_finnes") return "ui.plattform.finnes";
    if (e instanceof ApiFeil && e.kode === "overgang_ulovlig") return "ui.plattform.ulovlig";
    return "ui.plattform.feilet";
  };

  // Lovlige overganger speiler 190s ENE tabell. Kopien her er ERGONOMI —
  // døra dømmer uansett, og en knapp som ikke finnes er bedre enn en 409.
  const HANDLINGER = {
    prove: [["aktiv", "ui.plattform.aktiver"], ["stengt", "ui.plattform.steng"]],
    aktiv: [["stengt", "ui.plattform.steng"]],
    utlopt: [["aktiv", "ui.plattform.aktiver"], ["stengt", "ui.plattform.steng"]],
    stengt: [["aktiv", "ui.plattform.gjenapne"]],
  };

  const tegnListe = (firmaer) => {
    if (!firmaer.length) {
      return el("p", { class: "sub", text: t("ui.plattform.tom") });
    }
    const tbody = el("tbody");
    for (const f of firmaer) {
      const handlinger = el("td");
      // HELE RADEN LÅSES, ikke bare knappen som ble trykket (CodeRabbit).
      // Med bare én knapp låst kunne «Aktiver» og «Steng» ligge ute
      // samtidig, og da er det nettverket — ikke brukeren — som avgjør
      // hvilken status firmaet ender på. Samme feilklasse som de to
      // kanalvalgene i varselflaten.
      const laas = (av) => {
        for (const b of handlinger.querySelectorAll("button")) b.disabled = av;
      };
      for (const [status, nokkel] of (HANDLINGER[f.status] || [])) {
        const knapp = el("button", {
          type: "button", text: t(nokkel),
          class: status === "stengt" ? "knapp liten fare" : "knapp liten" });
        knapp.addEventListener("click", () => {
          laas(true);
          settPlattformFirmastatus(f.tenant, status)
            // OPPFRISK FØRST, MELD ETTERPÅ (CodeRabbit). `last()` kaller
            // `sett(boks, …)`, som erstatter alt innholdet — meldte vi først,
            // ble kvitteringen vasket bort i samme åndedrag, og brukeren så
            // aldri at noe var lagret.
            .then(() => last()).then(() => meld("ui.plattform.endret"))
            .catch((e) => {
              const k = feilkode(e);
              if (k) { meld(k); laas(false); }
            });
        });
        handlinger.append(knapp);
      }
      // ENDRE NAVN, tredje verb i eiers krav. Raden bytter til et skjema i
      // stedet for å åpne en dialog: du ser fortsatt de andre firmaene mens
      // du retter ett, og «Avbryt» tar deg tilbake uten å ha rørt noe.
      const navncelle = el("td", { text: f.navn });
      const endre = el("button", { type: "button", class: "knapp liten",
        text: t("ui.plattform.endre") });
      endre.addEventListener("click", () => {
        const inn = el("input", { type: "text", value: f.navn, class: "felt",
          "aria-label": t("ui.plattform.felt.navn") });
        const lagre = el("button", { type: "button", class: "knapp liten primar",
          text: t("ui.plattform.lagre") });
        const avbryt = el("button", { type: "button", class: "knapp liten",
          text: t("ui.plattform.avbryt") });
        lagre.addEventListener("click", () => {
          const nytt = inn.value.trim();
          if (!nytt) { inn.focus(); return; }
          lagre.disabled = true;
          oppdaterPlattformFirma(f.tenant, { navn: nytt,
                                             orgnummer: f.orgnummer || null })
            .then(() => last()).then(() => meld("ui.plattform.endret"))
            .catch((e) => {
              const k = feilkode(e);
              if (k) { meld(k); lagre.disabled = false; }
            });
        });
        avbryt.addEventListener("click", () => {
          sett(navncelle, f.navn);
          endre.disabled = false;
          endre.focus();
        });
        sett(navncelle, inn, lagre, avbryt);
        endre.disabled = true;
        inn.focus();
      });
      handlinger.append(endre);

      tbody.append(el("tr", {},
        navncelle,
        el("td", { text: f.tenant }),
        el("td", { text: t(`ui.plattform.status.${f.status}`, f.status) }),
        // Prøvefristen er BARE relevant i prøveperioden. Å vise en gammel
        // dato på et aktivt firma ville sett ut som en frist som gjelder.
        el("td", { text: f.status === "prove" && f.prove_utloper
          ? f.prove_utloper.slice(0, 10) : "—" }),
        handlinger));
    }
    return el("div", { class: "tabellramme" },
      el("table", { class: "tabell" },
        el("caption", { class: "visuelt-skjult",
          text: t("ui.plattform.firmaer") }),
        el("thead", {}, el("tr", {},
          el("th", { scope: "col", text: t("ui.plattform.kol.navn") }),
          el("th", { scope: "col", text: t("ui.plattform.kol.tenant") }),
          el("th", { scope: "col", text: t("ui.plattform.kol.status") }),
          el("th", { scope: "col", text: t("ui.plattform.kol.prove") }),
          el("th", { scope: "col", text: t("ui.plattform.kol.handling") }))),
        tbody));
  };

  const nyttFirmaSkjema = () => {
    const felt = (id, nokkel, type = "text", verdi = "") => {
      const inn = el("input", { type, id, value: verdi, class: "felt" });
      return [el("label", { for: id, text: t(nokkel) }), inn, inn];
    };
    const [lNavn, iNavn] = felt("pf-navn", "ui.plattform.felt.navn");
    const [lTenant, iTenant] = felt("pf-tenant", "ui.plattform.felt.tenant");
    const [lOrg, iOrg] = felt("pf-org", "ui.plattform.felt.orgnummer");
    const [lProve, iProve] = felt("pf-prove", "ui.plattform.felt.prove",
                                  "number", "30");
    const knapp = el("button", { type: "submit", class: "knapp primar",
      text: t("ui.plattform.opprett") });
    const skjema = el("form", { class: "skjema" },
      lNavn, iNavn, lTenant, iTenant, lOrg, iOrg, lProve, iProve,
      el("div", { class: "knapperad" }, knapp));
    skjema.addEventListener("submit", (ev) => {
      ev.preventDefault();
      knapp.disabled = true;
      opprettPlattformFirma({
        navn: iNavn.value.trim(),
        tenant: iTenant.value.trim(),
        orgnummer: iOrg.value.trim() || null,
        prove_dogn: Number(iProve.value) || 30,
      })
        .then(() => {
          skjema.reset(); iProve.value = "30";
          return last().then(() => meld("ui.plattform.opprettet"));
        })
        .catch((e) => { const k = feilkode(e); if (k) meld(k); })
        .finally(() => { knapp.disabled = false; });
    });
    return el("div", {},
      el("h3", { text: t("ui.plattform.nytt_firma") }), skjema);
  };

  const last = () => hentPlattformFirmaer()
    .then((d) => {
      sett(boks,
        el("h2", { text: t("ui.plattform.firmaer") }),
        el("p", { text: t("ui.plattform.firmaer_tekst") }),
        tegnListe(d.firmaer || []),
        nyttFirmaSkjema());
      boks.hidden = false;
    })
    .catch((e) => { if (feilkode(e)) boks.hidden = true; });

  // FØRST spørsmålet, så innholdet. Er svaret nei, gjøres ingen flere kall —
  // og seksjonen forblir `hidden`.
  erPlattformeier()
    .then((d) => { if (d && d.eier === true) return last(); })
    .catch(() => {});

  return boks;
}


export function visAdmin(hoved, ctx = {}) {
  const telling = plattformTelling();
  const forvalter = kanForvaltePolicy(ctx);
  // Snarveiene nederst peker bare på ruter økten har. En ren plattformdriftsøkt
  // bærer ikke kundens tenant-lokale lesescopes, og lesefallbacken til `#/policy`
  // er heller ikke gratis: `policy:read` er sitt eget scope.
  const ruter = new Set(byggRuter(ctx).map((r) => r.nokkel));
  // Tenanttabellen er kontrollplan på tvers av kunder, og krever
  // plattformdrift. En tenantbundet ops-økt (`security:read`) ser bare sin
  // egen rad — ikke hver eneste andre kundes plan, moduler og neste steg.
  const plattformdrift = erPlattformdrift(ctx);
  // Radene kommer UTENFRA — fra en autentisert, server-autorisert vei — og
  // ligger ikke i klientpakken. `/ui/{sti}` og `/ui/locale/{sprak}` serveres
  // uten sesjonssjekk, så et tenantregister i bundelen (eller som
  // `site.tenant.*`-nøkler i locale-settet) ville vært nedlastbart anonymt
  // uansett hva denne scope-sjekken velger å rendre. Tom liste = vi vet ikke,
  // og da sier flaten det.
  const rader = Array.isArray(ctx.tenanter) ? ctx.tenanter : [];
  const tenanter = plattformdrift
    ? rader
    : rader.filter((rad) => rad.id === ctx.tenant || rad.navn === ctx.tenant);

  // MIN PROFIL (eiervedtak 1/9). Identiteten sto sentrert i topplinjen på
  // hver eneste side — e-post, en 64-tegns prinsipal-id, rollelisten og et
  // ruteantall, uten ledetekster. Eier: «masse unødvendig informasjon der
  // oppe, bør alt plasseres under admin/profil» og «det er rotete med
  // bid…». Den står her nå, som en definisjonsliste: hver verdi har en
  // ledetekst som sier hva den ER, i stedet for å være en streng man må
  // gjette på.
  //
  // Prinsipal-id-en er den som betyr noe for fire-øyne (`(issuer, sub)`,
  // ikke e-posten), så den står ubeskåret her — dette er stedet den hører
  // hjemme, der det er plass til å forklare den.
  const profilRader = [
    [t("ui.profil.epost"), ctx.epost],
    [t("ui.profil.bruker_id"), ctx.bruker_id],
    [t("ui.profil.roller"), Array.isArray(ctx.roller) && ctx.roller.length
      ? ctx.roller.map((r) => t(`ui.rolle.${r}`, r)).join(", ") : null],
    [t("ui.profil.tenant"), ctx.tenant],
    [t("ui.profil.ruter"), String(ruter.size)],
  ].filter(([, v]) => v);
  const profil = el("section", { class: "kort" },
    el("h2", { text: t("ui.profil.tittel") }),
    el("dl", { class: "kv-liste" },
      ...profilRader.flatMap(([n, v]) => [
        el("dt", { text: n }),
        el("dd", { class: "celle-tekst", text: v }),
      ])));

  sett(hoved,
    ...flateHode(t("ui.admin.tittel"), t("ui.admin.undertittel")),
    profil,
    // Seksjonen tegner seg selv NÅR serveren har sagt at kalleren er
    // plattformeier. Til da er den `hidden`, og for alle andre forblir den
    // det for alltid.
    plattformseksjon(ctx),
    el("div", { class: "site-grid site-grid-3" },
      el("section", { class: "kort site-hero-card" },
        el("p", { class: "site-eyebrow", text: t("ui.admin.status") }),
        el("h2", { text: t("ui.admin.status_tittel") }),
        el("p", { text: t("ui.admin.status_tekst") }),
        el("div", { class: "site-kpi-row" },
          el("div", { class: "site-kpi" },
            el("strong", { text: `${telling.iDrift}/${telling.totalt}` }),
            el("span", { text: t("ui.admin.kpi.i_drift") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(telling.underArbeid) }),
            el("span", { text: t("ui.admin.kpi.under_arbeid") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(tenanter.length) }),
            el("span", { text: t("ui.admin.kpi.tenanter") })))),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.admin.utrulling") }),
        el("h2", { text: t("ui.admin.utrulling_tittel") }),
        el("ol", { class: "site-list site-list-ordered" },
          el("li", { text: t("ui.admin.utrulling.ci") }),
          el("li", { text: t("ui.admin.utrulling.staging") }),
          el("li", { text: t("ui.admin.utrulling.kanari") }),
          el("li", { text: t("ui.admin.utrulling.rollback") }))),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.admin.tillit") }),
        el("h2", { text: t("ui.admin.tillit_tittel") }),
        el("ul", { class: "site-list" },
          el("li", { text: t("ui.admin.tillit.p1") }),
          el("li", { text: t("ui.admin.tillit.p2") }),
          el("li", { text: t("ui.admin.tillit.p3") })))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.kontrollplan") }),
          el("h2", { text: t("ui.admin.kontrollplan_tittel") }))),
      el("div", { class: "site-grid site-grid-3" },
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.kontrollplan.release_tittel") }),
          el("p", { text: t("ui.admin.kontrollplan.release_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.kontrollplan.tenant_tittel") }),
          el("p", { text: t("ui.admin.kontrollplan.tenant_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.kontrollplan.port_tittel") }),
          el("p", { text: t("ui.admin.kontrollplan.port_tekst") })))),
    // Modulregisteret sto på den PUBLIKE forsiden, med badges og et
    // «0/45 i drift» som første tall en besøkende møtte. Statusen er ekte og
    // skal fortsatt være bindende — men den er et driftsbilde, ikke et
    // salgsargument, så den leses her, av dem som styrer utrullingen.
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("site.moduler") }),
          el("h2", { text: t("site.moduler_tittel") })),
        el("span", { class: "site-inline-note", text: t("site.moduler_note") })),
      el("div", { class: "site-card-grid" },
        MODULOVERSIKT.map((mod) => siteModuleKort(mod)))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.faser") }),
          el("h2", { text: t("ui.admin.faser_tittel") })),
        el("span", { class: "site-inline-note", text: t("ui.admin.faser_note") })),
      el("div", { class: "site-card-grid" },
        FASEOVERSIKT.map((fase) =>
          el("article", { class: "site-module-card" },
            el("div", { class: "site-module-head" },
              el("strong", { text: t(fase.navn_nokkel) }),
              siteFaseMerke(fase.status)),
            el("p", { text: t(fase.tekst_nokkel) }))))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.tenanter") }),
          el("h2", { text: plattformdrift
            ? t("ui.admin.tenanter_tittel")
            : t("ui.admin.tenanter_egen_tittel") })),
        plattformdrift
          ? null
          : el("span", { class: "site-inline-note",
            text: t("ui.admin.tenanter_egen_note") })),
      tenanter.length
        ? el("div", { class: "tablewrap" },
          el("table", {},
            el("caption", { class: "sr-only", text: plattformdrift
              ? t("ui.admin.tenanter_tittel")
              : t("ui.admin.tenanter_egen_tittel") }),
            el("thead", {},
              el("tr", {},
                el("th", { scope: "col", text: t("ui.admin.kol.tenant") }),
                el("th", { scope: "col", text: t("ui.admin.kol.plan") }),
                el("th", { scope: "col", text: t("ui.admin.kol.moduler") }),
                el("th", { scope: "col", text: t("ui.admin.kol.neste") }))),
            el("tbody", {},
              tenanter.map((tenant) =>
                el("tr", {},
                  // Verdiene er DATA fra den autentiserte veien, ikke
                  // locale-nøkler: kundenavn er ikke oversettelser, og en
                  // `t()` her ville vært en invitasjon til å legge dem
                  // tilbake i det offentlige locale-settet.
                  el("td", {}, el("strong", { text: tenant.navn || "" })),
                  // Unntaket er `plan`: den kommer som KODE fra et lukket
                  // vokabular, og etiketten er chrome. `neste` er fritekst per
                  // kunde og er allerede oversatt av serveren — den kan ikke
                  // ligge i det anonymt nedlastbare locale-settet.
                  el("td", { text: planEtikett(tenant.plan) }),
                  el("td", { text: Array.isArray(tenant.moduler)
                    ? tenant.moduler.map(modulmerke).join(", ") : "" }),
                  el("td", { text: tenant.neste || "" }))))))
        : el("p", { class: "muted", text: t("ui.admin.tenanter_ukjent") })),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.admin.handlinger") }),
          el("h2", { text: t("ui.admin.handlinger_tittel") }))),
      el("div", { class: "site-card-grid" },
        // Kundeadmin er en leseflate og ligger i basisrutene — snarveien dit
        // gjelder derfor alle. PolicyADMINISTRASJONEN krever forvaltning:
        // admin-flaten åpnes av `security:read`, og rollene `admin`/`sikkerhet`
        // har bare `policy:read`, så snarveien ville pekt på en flate ruteren
        // nekter dem, med mutasjonsknapper som uansett gir 403.
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.admin.handling.kundeadmin") }),
          el("p", { text: t("ui.admin.handling.kundeadmin_tekst") }),
          el("a", { class: "lenkeknapp", href: "#/kundeadmin",
            text: t("ui.admin.handling.ga_til") })),
        ...(forvalter ? [
          el("article", { class: "site-mini-card" },
            el("strong", { text: t("ui.admin.handling.policyadmin") }),
            el("p", { text: t("ui.admin.handling.policyadmin_tekst") }),
            el("a", { class: "lenkeknapp", href: "#/policyadmin",
              text: t("ui.admin.handling.ga_til") })),
        // Leserettighet skal fortsatt komme til policy — bare til
        // lesevisningen, ikke til aktiveringsflaten. Uten `policy:read` finnes
        // ingen av delene, og da er ingen snarvei riktigere enn en som 403-er.
        ] : ruter.has("policy") ? [
          el("article", { class: "site-mini-card" },
            el("strong", { text: t("ui.admin.handling.policy_lesing") }),
            el("p", { text: t("ui.admin.handling.policy_lesing_tekst") }),
            el("a", { class: "lenkeknapp", href: "#/policy",
              text: t("ui.admin.handling.ga_til") })),
        ] : []),
        ...(ruter.has("unntak") ? [
          el("article", { class: "site-mini-card" },
            el("strong", { text: t("ui.admin.handling.unntak") }),
            el("p", { text: t("ui.admin.handling.unntak_tekst") }),
            el("a", { class: "lenkeknapp", href: "#/unntak",
              text: t("ui.admin.handling.ga_til") })),
        ] : []))));
}
