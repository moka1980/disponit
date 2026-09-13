import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { flateHode } from "./felles.js";
import { KUNDEROLLER, modulerFraIder, tenantTelling } from "../plattformdata.js";
import { opprettInvitasjon, hentInvitasjoner,
         nyIdempotensnokkel } from "../api.js";
import { meldLive } from "../komponenter.js";
import { byggRuter, kanForvaltePolicy } from "../sitekart.js";
import { siteModuleKort, siteStatusMerke } from "../sitekomponenter.js";

// Snarveiene er ett kort per rute økten har — aldri én lenke mer. Rekkefølgen
// er fast, så kortene ikke bytter plass når et scope mangler.
const SNARVEIER = [
  { nokkel: "oversikt", tittel: "ui.kundeadmin.handling.oversikt",
    tekst: "ui.kundeadmin.handling.oversikt_tekst" },
  { nokkel: "unntak", tittel: "ui.kundeadmin.handling.unntak",
    tekst: "ui.kundeadmin.handling.unntak_tekst" },
  { nokkel: "policy", tittel: "ui.kundeadmin.handling.policy",
    tekst: "ui.kundeadmin.handling.policy_tekst" },
];

function snarveier(ruter) {
  const kort = SNARVEIER.filter((s) => ruter.has(s.nokkel)).map((s) =>
    el("article", { class: "site-mini-card" },
      el("strong", { text: t(s.tittel) }),
      el("p", { text: t(s.tekst) }),
      el("a", { class: "lenkeknapp", href: `#/${s.nokkel}`,
        text: t("ui.kundeadmin.handling.ga_til") })));
  // En rolle uten leserettigheter i det hele tatt skal se det, ikke en tom rad.
  return kort.length
    ? kort
    : [el("p", { class: "muted", text: t("ui.kundeadmin.handling_ingen") })];
}

// Policykortet har TRE tilstander, ikke to. Den tredje er den som manglet:
// `godkjenner` kan hverken forvalte eller lese policy, og fikk likevel tilbudt
// lesevisningen. Da er det ærligere å forklare at rollen ikke har innsynet enn
// å lenke til noe som svarer 403.
function policykort(forvalter, ruter) {
  if (forvalter) {
    return el("article", { class: "kort" },
      el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.policy") }),
      el("h2", { text: t("ui.kundeadmin.policy_tittel") }),
      el("p", { text: t("ui.kundeadmin.policy_tekst") }),
      el("a", { class: "knapp primar", href: "#/policyadmin",
        text: t("ui.kundeadmin.policy_handling") }));
  }
  if (ruter.has("policy")) {
    return el("article", { class: "kort" },
      el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.policy") }),
      el("h2", { text: t("ui.kundeadmin.policy_lesing_tittel") }),
      el("p", { text: t("ui.kundeadmin.policy_lesing_tekst") }),
      el("a", { class: "knapp primar", href: "#/policy",
        text: t("ui.kundeadmin.policy_lesing_handling") }));
  }
  return el("article", { class: "kort" },
    el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.policy") }),
    el("h2", { text: t("ui.kundeadmin.policy_ingen_tittel") }),
    el("p", { text: t("ui.kundeadmin.policy_ingen_tekst") }));
}

// Rollene en admin kan gi bort. KUNDEROLLER er husets guide over
// KUNDEROLLER (leser/godkjenner/policyforvalter); `admin` står bevisst
// utenfor den — den er ikke en rolle guiden forklarer — men et firma må
// kunne ha mer enn én administrator, og grunnleggeren kan slutte. Derfor
// tilbys den her, eksplisitt og sist.
export const INVITERBARE = [...KUNDEROLLER.map((r) => r.id), "admin"];

function invitasjonsdel(ctx) {
  const scopes = new Set(ctx.scopes || []);
  const kanInvitere = scopes.has("firma:inviter");
  const kanSe = scopes.has("security:read");
  if (!kanInvitere && !kanSe) return el("div", { hidden: true });

  const boks = el("div", { class: "kv-underdel" });
  // Idempotensnøkkelen lever i lukkingen, ikke i kallet: den skal overleve
  // et nytt klikk etter et tapt svar, og bare da.
  let idem = null;
  let idemFor = null;
  const melding = el("p", { class: "melding", role: "status" });
  const lenkeboks = el("div", {});
  const liste = el("div", {});

  const tegnListe = async () => {
    if (!kanSe) return;
    try {
      const d = await hentInvitasjoner();
      const inv = d.invitasjoner || [];
      sett(liste,
        el("h3", { text: t("ui.kundeadmin.invitasjoner") }),
        inv.length
          ? el("ul", { class: "site-list" }, inv.map((i) =>
              el("li", {},
                el("strong", { text: i.merke }), " ",
                (i.roller || []).join(", "), " — ",
                i.brukt ? t("ui.kundeadmin.invitasjon_brukt")
                        : t("ui.kundeadmin.invitasjon_apen"))))
          : el("p", { class: "muted",
                      text: t("ui.kundeadmin.invitasjoner_tom") }));
    } catch {
      // Lista er et tillegg; feiler den, skal skjemaet fortsatt virke.
      sett(liste, "");
    }
  };

  if (kanInvitere) {
    const avkrysninger = INVITERBARE.map((id) => {
      const inp = el("input", { type: "checkbox", id: `inv-${id}`,
                                value: id });
      return { id, inp,
        rad: el("div", { class: "felt-avkryss" }, inp,
          el("label", { for: `inv-${id}`, text: t(`ui.rolle.${id}`) })) };
    });
    const knapp = el("button", { type: "submit", class: "knapp primar",
                                 text: t("ui.kundeadmin.inviter_knapp") });
    const skjema = el("form", { class: "kv-skjema", novalidate: "" },
      el("h3", { text: t("ui.kundeadmin.inviter") }),
      el("p", { class: "hjelpetekst",
                text: t("ui.kundeadmin.inviter_hjelp") }),
      el("fieldset", {},
        el("legend", { text: t("ui.kundeadmin.inviter_roller") }),
        ...avkrysninger.map((a) => a.rad)),
      el("div", { class: "knapperad" }, knapp), melding, lenkeboks);

    skjema.addEventListener("submit", async (e) => {
      e.preventDefault();
      melding.classList.remove("feil");
      sett(melding, "");
      const valgte = avkrysninger.filter((a) => a.inp.checked).map((a) => a.id);
      if (!valgte.length) {
        melding.classList.add("feil");
        sett(melding, t("ui.kundeadmin.inviter_ingen_rolle"));
        avkrysninger[0].inp.focus();
        return;
      }
      // ÉN NØKKEL PER ROLLEVALG, beholdt til opprettelsen lykkes
      // (CodeRabbit). Første utgave lot klienten lage en fersk nøkkel per
      // klikk: et tapt svar + nytt klikk ga da TO invitasjoner, og den ene
      // ble liggende ubrukt til den utløp. Endrer hun valget, er det en
      // annen operasjon og nøkkelen nullstilles.
      const valgnokkel = valgte.join(",");
      if (idemFor !== valgnokkel) {
        idemFor = valgnokkel;
        idem = nyIdempotensnokkel();
      }
      knapp.disabled = true;
      try {
        const svar = await opprettInvitasjon({ roller: valgte }, idem);
        idemFor = null;          // lykkes den, er neste klikk en ny lenke
        // LENKEN VISES ÉN GANG. Tokenet finnes ikke i basen og kan ikke
        // hentes igjen — derfor står den i et felt hun kan merke og
        // kopiere, ikke i en flyktig melding.
        const url = `${window.location.origin}/?visning=blimed`
          + `&t=${encodeURIComponent(svar.tenant)}`
          + `&k=${encodeURIComponent(svar.token)}`;
        const felt = el("input", { class: "felt-inp", readOnly: true,
                                   value: url, id: "inv-lenke" });
        sett(lenkeboks,
          el("label", { for: "inv-lenke",
                        text: t("ui.kundeadmin.inviter_lenke") }),
          felt,
          el("p", { class: "muted",
                    text: t("ui.kundeadmin.inviter_utloper")
                      .replace("{dato}", svar.utloper) }));
        felt.select();
        meldLive(t("ui.kundeadmin.inviter_lenke"));
        await tegnListe();
      } catch {
        melding.classList.add("feil");
        sett(melding, t("ui.kundeadmin.inviter_feil"));
      } finally {
        knapp.disabled = false;
      }
    });
    boks.append(skjema);
  }

  boks.append(liste);
  tegnListe();
  return boks;
}


export function visKundeadmin(hoved, ctx = {}) {
  // Flaten er åpen for hele kundeøkten, men policyADMINISTRASJONEN er det
  // ikke: uten `policy:write`/`policy:activate` peker snarveien på en flate
  // ruteren nekter, med aktiveringsknapper som uansett gir 403. Leseren får
  // lesevisningen `#/policy` i stedet — samme mønster som admin-flaten.
  const forvalter = kanForvaltePolicy(ctx);
  // ...men LESEVISNINGEN er heller ikke gratis. Snarveiene herfra bygges derfor
  // av de rutene økten FAKTISK har, ikke av en egen liste: `policy:read` er sitt
  // eget scope, og `godkjenner` har det ikke — for den økten pekte fallbacken på
  // en flate ruteren nekter, bak et endepunkt som svarer 403. Samme gjelder
  // `#/unntak` for `policyforvalter`, som mangler `exceptions:read`. Én kilde
  // (`byggRuter`) betyr at en lenke herfra ikke kan overleve at ruten forsvinner.
  const ruter = new Set(byggRuter(ctx).map((r) => r.nokkel));
  // Kundens arbeidsflate viser KUNDENS moduler, ikke plattformkatalogen: uten
  // dette meldte en kunde med to tildelte moduler tre aktive, og viste M-37 og
  // M-38 som om de var kundens. Tildelingen kommer fra den autentiserte veien
  // (`ctx.moduler` = modul-ID-er for DENNE økten), ikke fra en tabell i
  // klientpakken — bundelen og locale-settet serveres uten sesjonssjekk.
  // `null` = tildelingen er ukjent, og da sier flaten det i stedet for å gjette.
  const mine = modulerFraIder(ctx.moduler);
  const moduler = mine || [];
  const telling = tenantTelling(moduler);
  const aktive = moduler.filter((mod) => mod.status === "i_drift");
  // «Under arbeid» dekker både `klargjort` (godkjent, ikke satt i drift) og
  // `bygges`: for kunden er begge det samme — modulen er ikke i drift ennå.
  const underArbeid = moduler.filter((mod) => mod.status === "klargjort"
    || mod.status === "bygges");

  sett(hoved,
    ...flateHode(t("ui.kundeadmin.tittel"), t("ui.kundeadmin.undertittel")),
    el("div", { class: "site-grid site-grid-3" },
      el("section", { class: "kort site-hero-card" },
        el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.workspace") }),
        el("h2", { text: ctx.tenant || t("ui.kundeadmin.standard_tenant") }),
        el("p", { text: t("ui.kundeadmin.workspace_tekst") }),
        el("div", { class: "site-kpi-row" },
          el("div", { class: "site-kpi" },
            el("strong", { text: String(aktive.length) }),
            el("span", { text: t("ui.kundeadmin.kpi.aktive_moduler") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(underArbeid.length) }),
            el("span", { text: t("ui.kundeadmin.kpi.under_arbeid") })),
          el("div", { class: "site-kpi" },
            el("strong", { text: String(telling.planlagt) }),
            el("span", { text: t("ui.kundeadmin.kpi.planlagt") })))),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.brukere") }),
        el("h2", { text: t("ui.kundeadmin.brukere_tittel") }),
        el("ul", { class: "site-list" },
          KUNDEROLLER.map((rolle) =>
            el("li", {}, el("strong", { text: t(rolle.navn_nokkel) }), " ",
              t(rolle.tekst_nokkel))),
        ),
        // 194/195: INVITASJONEN STÅR HER, i seksjonen om brukere, og ikke
        // som en egen flate. Rolleguiden over forklarer hva rollene
        // BETYR; skjemaet under er stedet man faktisk bruker den. En egen
        // flate ville skilt forklaringen fra handlingen.
        invitasjonsdel(ctx)),
      el("section", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.integrasjoner") }),
        el("h2", { text: t("ui.kundeadmin.integrasjoner_tittel") }),
        el("ul", { class: "site-list" },
          el("li", {}, t("ui.kundeadmin.integrasjon.regnskap")),
          el("li", {}, t("ui.kundeadmin.integrasjon.epost")),
          el("li", {}, t("ui.kundeadmin.integrasjon.bank")),
          el("li", {}, t("ui.kundeadmin.integrasjon.idp"))))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.plattform") }),
          el("h2", { text: t("ui.kundeadmin.plattform_tittel") }))),
      el("div", { class: "site-grid site-grid-3" },
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.kundeadmin.plattform.status_tittel") }),
          el("p", { text: t("ui.kundeadmin.plattform.status_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.kundeadmin.plattform.policy_tittel") }),
          el("p", { text: t("ui.kundeadmin.plattform.policy_tekst") })),
        el("article", { class: "site-mini-card" },
          el("strong", { text: t("ui.kundeadmin.plattform.utvidelse_tittel") }),
          el("p", { text: t("ui.kundeadmin.plattform.utvidelse_tekst") })))),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.moduler") }),
          el("h2", { text: t("ui.kundeadmin.moduler_tittel") })),
        el("span", { class: "site-inline-note", text: t("ui.kundeadmin.moduler_note") })),
      mine
        ? el("div", { class: "site-card-grid" },
          moduler.map((mod) => siteModuleKort(mod)))
        : el("p", { class: "muted", text: t("ui.kundeadmin.moduler_ukjent") })),
    el("section", { class: "kort site-section" },
      el("div", { class: "site-section-head" },
        el("div", {},
          el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.handlinger") }),
          el("h2", { text: t("ui.kundeadmin.handlinger_tittel") }))),
      el("div", { class: "site-card-grid" },
        ...snarveier(ruter))),
    el("section", { class: "site-grid site-grid-2" },
      policykort(forvalter, ruter),
      el("article", { class: "kort" },
        el("p", { class: "site-eyebrow", text: t("ui.kundeadmin.neste") }),
        el("h2", { text: t("ui.kundeadmin.neste_tittel") }),
        el("p", { text: t("ui.kundeadmin.neste_tekst") }),
        el("div", { class: "site-inline-badges" },
          siteStatusMerke("i_drift"),
          siteStatusMerke("klargjort"),
          siteStatusMerke("bygges"),
          siteStatusMerke("planlagt")))));
}
