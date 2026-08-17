// Policy — les policyen som håndheves (read-only i v1; redigering er egen,
// versjonert flyt). Menneskelesbar visning av den lukkede PolicyDTO-en.
import { el, sett } from "../dom.js";
import { t } from "../i18n.js";
import { hentJson, slettPolicy, nyIdempotensnokkel, IkkeFunnetFeil, ApiFeil,
         UautorisertFeil, IngenTilgangFeil } from "../api.js";
// (`ApiFeil` brukes både til feilkoden fra slettingen og til 5xx-porten i
// `hentAktiv` — se der.)
import { VarselBanner, TomTilstand, meldLive } from "../komponenter.js";
import { Bekreftelsesdialog } from "../dialog.js";
import { harScope } from "../sitekart.js";
import { medStatus, flateHode } from "./felles.js";

function grenserNode(g) {
  if (!g) return el("span", { class: "muted", text: t("ui.policy.ingen_grenser") });
  const ul = el("ul", { class: "grenser" });
  if (g.belop_maks) {
    ul.append(el("li", {},
      `${t("ui.policy.belop")}: ${g.belop_maks} ${(g.valuta || []).join(" / ")}`));
  }
  if (g.tidsvindu) {
    const dager = g.tidsvindu.ukedager.map((d) => t(`dag.${d}`)).join(", ");
    ul.append(el("li", {},
      `${t("ui.policy.tidsvindu")}: ${dager} ${g.tidsvindu.fra}–${g.tidsvindu.til} (${g.tidsvindu.tidssone})`));
  }
  if (g.frekvens) {
    ul.append(el("li", {},
      `${t("ui.policy.frekvens")}: ${g.frekvens.maks} ${t("ui.policy.per")} ` +
      `${g.frekvens.vindu_antall} ${t(`vindu_enhet.${g.frekvens.vindu_enhet}`, g.frekvens.vindu_enhet)}`));
  }
  return ul;
}

function handlingNode(h) {
  const rot = el("div", { class: "rule" },
    el("div", {},
      el("strong", { text: h.navn }), " — ",
      el("span", { text: t(`modus.${h.modus}`, h.modus) })),
    grenserNode(h.grenser));
  if (h.vilkaar && h.vilkaar.length) {
    rot.append(el("div", { class: "muted" },
      `${t("ui.policy.vilkaar")}: ${h.vilkaar.join(", ")}`));
  }
  return rot;
}

function verifikatorNode(v) {
  return el("div", { class: "rule" },
    el("div", {}, el("strong", { text: v.offentlig_id })),
    el("div", { class: "muted" },
      `${t("ui.policy.betrodd_for")}: ${(v.betrodd_for || []).join(", ") || "—"}`),
    v.kan_fastsla_permanent
      ? el("div", { class: "muted", text: t("ui.policy.permanent") }) : null);
}

function seksjon(tittel, barn) {
  return el("section", { class: "policy-sec" },
    el("h2", { text: tittel }), ...barn);
}

//: Identiteten til én policy — samme form overalt den nevnes, så «den andre
//: der» aldri må gjettes ut av rekkefølgen på skjermen.
function merke(p) {
  return `${p.policy_id} · ${t("ui.policy.versjon")} ${p.versjon}`;
}

// Hvilke policyer som står aktive, for ALLE som kan lese policyen (Codex P2).
// Sto identitetene bare i `angreSeksjon`, forsvant de sammen med den bak
// `policy:write` — og `leser`, `admin` og `sikkerhet` har `policy:read` uten
// skrivetilgang. De satt da igjen med et varsel om at arbeidsområdet er i en
// feiltilstand og ingen opplysning om HVA den gjelder: ikke nok til å melde
// fra, ikke nok til å be en forvalter rydde. Det er nettopp opplysningen
// `policy:read` GIR rett til — det er bare mutasjonen som ikke skal stå for
// dem, og den er portet der den hører hjemme, på knappen.
function identiteter(aktive) {
  return seksjon(t("ui.policy.aktive"),
    [el("ul", { class: "liste" },
      aktive.map((p) => el("li", { text: merke(p) })))]);
}

// `/v1/policy/aktiv` lover ÉN aktiv policy og svarer 500 (`intern_feil`) når
// tenanten har flere — fail-closed, og riktig: et leseendepunkt skal ikke velge
// hvilken policy som gjelder. Men NØYAKTIG den tilstanden er feilen «angre en
// feilopprettet policy» finnes for (`tjenestebedrift1` og `tjenestebedrift2`
// ble begge aktivert ved feil), så uten en vei videre her var slettehandlingen
// utilgjengelig i det ene tilfellet den er skrevet for (Codex P2) — flaten
// endte i en generisk feiltilstand, og eier var tilbake til håndskrevet SQL.
//
// Reparasjonen er `GET /v1/policy/aktive`, og den hentes bare når den trengs:
// den normale veien er ETT kall, som før. Utløseren er 5xx, ikke feilKODEN:
// «kunne ikke serveres som én» er det vi faktisk vet, og er grunnen at det er
// FLERE, finnes reparasjonen. Er den noe annet (f.eks. `policy_korrupt` på den
// ene aktive), står den opprinnelige feilen — vi bytter ikke ut en ærlig
// feiltilstand med en villedende liste.
async function hentAktiv() {
  try {
    const d = await hentJson("/v1/policy/aktiv");
    return { aktive: [{ policy_id: d.policy_id, versjon: d.versjon,
                        innholds_hash: d.innholds_hash }], dto: d };
  } catch (e) {
    if (e instanceof IkkeFunnetFeil) return { aktive: [], dto: null };
    if (!(e instanceof ApiFeil) || e.status < 500) throw e;
    let liste;
    try { liste = await hentJson("/v1/policy/aktive"); }
    catch (f) {
      // Reserven har lov til å mislykkes — da står den opprinnelige feilen,
      // for den er det vi faktisk vet om policyen. Men 401 og 403 er ikke et
      // utsagn om policyen i det hele tatt, de er et utsagn om ØKTEN (Codex
      // P2): utløper eller trekkes sesjonen mellom de to kallene, ville
      // `throw e` gitt en «prøv igjen»-feilside med en knapp som aldri kan
      // lykkes — i stedet for innloggingen `medStatus` sender alle andre 401
      // til. Rammens globale håndtering gjelder også det andre kallet.
      if (f instanceof UautorisertFeil || f instanceof IngenTilgangFeil) throw f;
      throw e;
    }
    if (liste.policyer.length < 2) throw e;
    return { aktive: liste.policyer, dto: null };
  }
}

export function visPolicy(hoved, ctx) {
  medStatus(hoved, ctx, hentAktiv, ({ aktive, dto }) => {
    const paaNytt = () => visPolicy(hoved, ctx);
    if (!aktive.length) {
      sett(hoved, ...flateHode(t("ui.policy.tittel")), TomTilstand({}));
      return;
    }
    if (!dto) {
      // Flere aktive: policyen kan ikke VISES (hvilken av dem skulle det
      // vært?), men de kan pekes på — og for den som har lov til å rydde,
      // slettes. Identitetene står FØRST og uavhengig av skrivetilgang:
      // de er innholdet i feiltilstanden, ikke en del av handlingen.
      sett(hoved,
        ...flateHode(t("ui.policy.tittel")),
        VarselBanner({ art: "fare", tekst: t("ui.policy.flere_aktive") }),
        identiteter(aktive),
        ...aktive.map((p) => angreSeksjon(p, ctx, paaNytt, true)));
      return;
    }
    sett(hoved,
      ...flateHode(t("ui.policy.tittel"),
        `${t("ui.policy.versjon")} ${dto.versjon}`),
      VarselBanner({ art: "guard", tekst: t("ui.policy.readonly") }),
      seksjon(t("ui.policy.roller"),
        [el("ul", { class: "liste" },
          dto.roller.map((r) => el("li", { text: r.id })))]),
      seksjon(t("ui.policy.handlinger"), dto.handlinger.map(handlingNode)),
      seksjon(t("ui.policy.verifikatorer"),
        dto.verifikatorer.map(verifikatorNode)),
      angreSeksjon(dto, ctx, paaNytt));
  });
}

// «Angre» for en feilopprettet policy. Serveren (`slett_ubrukt_policy`, 032)
// håndhever HELE vilkåret: aldri styrt en beslutning, ingen åpen runde.
// Knappen står derfor alltid for policyFORVALTEREN, og en avvisning kommer
// tilbake som en FORKLARING («policyen har styrt beslutninger — den kan
// avvikles, ikke slettes»), ikke som en skjult knapp: en vakt man kan se er
// en vakt man kan forstå.
//
// Men det argumentet gjelder TILSTAND, ikke TILGANG (Codex P2). Denne flaten
// nås med `policy:read`, og `leser`, `admin` og `sikkerhet` har nettopp det og
// ikke `policy:write`. For dem er det ingen tilstand som noen gang kan gjøre
// knappen brukbar — den ville bare invitert dem gjennom en irreversibel
// bekreftelsesdialog fram til en generisk feil fra serverens 403. En
// forklaring man ikke kan handle på er ikke en forklaring, den er støy.
// Lesingen står som før; det er bare mutasjonen som forsvinner.
//
// `flere` sier at DENNE policyen er én av flere som står aktive. Det styrer
// to ting som begge følger av nettopp det: seksjonen navngir sin policy (to
// like «Slett policy»-knapper er ikke et valg), og bekreftelsen beskriver den
// tilstanden slettingen faktisk etterlater.
function angreSeksjon(d, ctx, tegnPaaNytt, flere = false) {
  if (!harScope(ctx, "policy:write")) return null;
  // Nøkkelen er STABIL PER RENDER (samme R2-idiom som `apneRunde`), ikke per
  // klikk (Codex P2). Serveren lagrer og replayer nå slettesvaret, men den
  // evnen er verdiløs om kalleren roterer nøkkelen: går svaret tapt på veien
  // tilbake, er policyen borte, og et nytt klikk med FERSK nøkkel er en ny
  // operasjon som møter `policy_ukjent` — den endelige feilmeldingen på en
  // operasjon som faktisk lyktes, altså nøyaktig det replayen skulle hindre.
  // Med samme nøkkel svarer serveren det lagrede svaret, og flaten kommer
  // videre. Per render er riktig grense i begge ender: en avvisning
  // (`policy_i_bruk`, `runde_allerede_aapen`) ruller tilbake claimet server-
  // side, så seksjonen står igjen med en ubrukt nøkkel og kan prøve på nytt,
  // mens et vellykket slett tegner flaten på nytt — og den neste seksjonen
  // gjelder en annen policy og får sin egen nøkkel.
  const slettNokkel = nyIdempotensnokkel();
  const status = el("p", { class: "muted", role: "status", text: "" });
  const b = el("button", { class: "knapp fare", type: "button",
    text: t("ui.policy.slett") });
  b.addEventListener("click", () => {
    Bekreftelsesdialog({
      tittel: t("ui.policy.slett_tittel"),
      // Bekreftelsen må beskrive tilstanden ETTER slettingen, og den er ikke
      // den samme i de to visningene (Codex P2). Endepunktet sletter kun
      // `d.policy_id`: står tenanten med FLERE aktive, blir de øvrige stående
      // og styrer beslutninger videre. «Tenanten står uten aktiv policy» ville
      // der vært direkte galt — og galt på den farligste måten, som det siste
      // en operatør leser før et irreversibelt valg: den som tror at
      // håndhevingen stopper, sletter for å stoppe den.
      tekst: `${d.policy_id} · ${flere ? t("ui.policy.slett_tekst_flere")
                                       : t("ui.policy.slett_tekst")}`,
      primarTekst: t("ui.policy.slett"),
      farlig: true,
      // Identiteten som sendes er den seksjonen VISER — `merke(d)` over står
      // for de samme to feltene. Aktiveres en ny versjon mellom lastingen og
      // bekreftelsen, avviser serveren med `policy_endret` i stedet for å
      // slette den nye policyen operatøren aldri så (Codex P1).
      paaPrimar: () => slettPolicy(d.policy_id, d.versjon, d.innholds_hash,
                                   slettNokkel)
        .then(() => {
          meldLive(t("ui.policy.slettet"));
          tegnPaaNytt();               // flaten viser nå TomTilstand — sant.
        })
        .catch((e) => {
          if (e instanceof UautorisertFeil) { ctx.paaUautorisert(); return; }
          const kode = e instanceof ApiFeil ? e.kode : "";
          status.textContent = kode === "policy_i_bruk"
            ? t("ui.policy.slett_i_bruk")
            : kode === "runde_allerede_aapen"
              ? t("ui.policy.slett_runde_aapen")
              : kode === "policy_endret"
                // Visningen er foreldet, ikke gal — og forskjellen er hele
                // poenget: eier må SE den nye versjonen før hun avgjør om den
                // også skal bort. Flaten tegnes derfor ikke på nytt under
                // henne; hun får vite at siden må lastes.
                ? t("ui.policy.slett_endret")
                : t("ui.policy.slett_feilet");
        }),
    });
  });
  // Står det FLERE slett-seksjoner på flaten, må hver av dem si hvilken policy
  // den gjelder — både synlig og for skjermleseren. En knapp som bare heter
  // «Slett policy», gjentatt, er ikke et valg man kan ta.
  const navn = merke(d);
  return el("section", { class: "policy-angre",
    "aria-label": flere
      ? `${t("ui.policy.slett_tittel")}: ${navn}` : t("ui.policy.slett_tittel") },
    el("h3", { text: t("ui.policy.slett_tittel") }),
    flere ? el("p", {}, el("strong", { text: navn })) : null,
    el("p", { class: "muted", text: t("ui.policy.slett_forklaring") }),
    b, status);
}
