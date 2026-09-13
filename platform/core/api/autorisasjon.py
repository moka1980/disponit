"""Roller → scopes (PR-010 v5 §4).

`brukermedlemskap.roller[]` er ENESTE autoritet. Scopes AVLEDES her fra et
LUKKET rollemønster — de lagres aldri som egen kolonne (som ville drevet
fra rollene). Endres en brukers roller, økes `authz_version` av
DB-triggeren, alle sesjoner ugyldiggjøres, og neste innlogging får de nye
scopene utledet på nytt herfra.

Scope-navnene er PR-008s lese-scopes. `test_rolle_scopes_er_kjente`
binder utledningen mot den kanoniske LESESCOPES-mengden, så en rolle ikke
kan gi et scope som ikke finnes.

ET NYTT SCOPE MÅ INN FEM STEDER, og de oppdages ellers én CI-runde om
gangen (183 brukte fem runder på å finne dem alle):

  1. HER, i `ROLLE_TIL_SCOPES` — og husk at `sikkerhet` er en
     SUPERMENGDE av `leser`; porten
     `test_sikkerhet_er_fortsatt_en_supermengde_av_leser` måler det nå.
  2. `api/app.py`: `LESESCOPES` hvis scopet er LESENDE. En browsersesjon
     får rollen `bruker` og måles mot NETTOPP det settet — ikke mot
     tabellen her. Uteblir linja, er flaten død for alle som logger inn
     i nettleseren, mens Bearer-tester går grønt.
  3. `api/app.py`: `RUTESCOPE`, og `BROWSER_MUTASJONSSCOPES` hvis scopet
     MUTERER fra flaten — og da må endepunktet selv håndheve CSRF
     (dobbel-innsending); carve-outen slipper bare forbi den generelle
     porten.
  4. En MIGRASJON som seeder `rolle_scope` (043 §6b speiler denne
     tabellen eksakt; port 26 i `test_gate14b` måler de to mot
     hverandre).
  5. `ui/static/js/plattformdata.js`: `KUNDEROLLER` — kundens grunnlag
     for å TILDELE roller. `test_ui_kontrakt` binder guiden mot denne
     tabellen, og et scope som mangler der er en rolle kunden tror er
     snevrere enn den er.

I tillegg pinner `test_pr010_db` både `leser`s lukkede sett og admins
muterende differanse mot LESESCOPES.

OG EN RUTE MED EGET KROPPSTAK TRENGER EN SJETTE: en `location`-blokk i
nginx-malen. Ingressens grense er uavhengig av appens, og uten blokken
svarer proxyen 413 før `RUTEKROPPSGRENSER` konsulteres. Se kommentaren
over den tabellen i `api/app.py`. Malen rulles ut av
`deploy/staging/opp-transport.sh`, IKKE av den vanlige deployen.

EN SJUENDE GJELDER HVER NY PRIVILEGERT DØR, uansett scope: den må stå i
`deploy/staging/eierskap-reparasjon.sql`s `_design`. `test_eierskap`
måler basen mot den lista og sier «privilegert eide objekter utenfor
designet» — et navn som mangler der, er en dør ingen har tatt stilling
til hvem som skal eie.

OG EN ÅTTENDE GJELDER EN NY ROLLE, ikke bare et nytt scope: `ui.rolle.
<rolle>` MÅ finnes i BÅDE `locales/nb.json` og `locales/en.json`.
`test_hver_kanonisk_rolle_har_navn_i_begge_lokalene` går over HELE denne
tabellen, ikke over flaten — uten nøkkelen viser skallet den rå
identifikatoren, altså et norsk stikkord i et ellers engelsk grensesnitt.
Speil 5 (`KUNDEROLLER`) er derimot IKKE påkrevd for alle roller: porten
der går over guiden og sjekker at hver oppføring finnes her — ikke
motsatt. En rolle kunden ikke skal kunne TILDELE (`registrant`, og senere
plattformrollen) hører derfor ikke hjemme i guiden.

EN NIENDE, HVIS SCOPET MUTERER FRA NETTLESEREN: `BROWSER_MUTASJONSSCOPES`
er nevnt i speil 3, men grunnen tåler å gjentas — `app.py` avviser
BLANKT ethvert scope en browsersesjon ber om som verken står i
`LESESCOPES` eller der. Et muterende scope uten den linja gir
`scope_mangler` til en bruker som har rollen, og loggen sier ikke hvorfor.
"""
from __future__ import annotations

#: Lukket mønster. En ukjent rolle gir INGEN scopes (default-deny) — den
#: er ikke en feil, men den åpner ingenting.
ROLLE_TIL_SCOPES: dict[str, frozenset[str]] = {
    # Vanlig kundebruker: ser beslutninger, unntak og policy — ikke
    # sikkerhetskøen.
    # M-6 PR-A: `epost:read` — lese klassifiseringer/utkast/oppfølging i
    # M-6-flaten (PR-D). Rent lesende, derfor også hos leser/sikkerhet.
    # 089 (M-35): kontinuitetsstatusen — tjenestekart, kontakter,
    # hendelser og siste øvelse — er tenantens egen beredskapsinnsikt,
    # samme leseklasse som beslutningene. Lesescopet gis derfor til alle
    # kunderollene som leser tilstand; SKRIVINGEN (hendelser, poster,
    # lukking) er admin-myndighet alene.
    # 102 (M-17): kundeservicekøen er tenantens alminnelige
    # arbeidsflate og ligger under `decisions:read`, som `leser` alt har.
    # `kundeservice:innhold` er DERIMOT en egen rett: den som svarer
    # kunder trenger den, og derfor står den her — men den er skilt ut
    # nettopp for at en tenant som vil ha en rolle som ser køen UTEN å
    # kunne lese innholdet, kan lage den uten skjemaendring.
    # 183: `part:read` hos ENHVER leser. Kundelisten er ikke en
    # hemmelighet i firmaet — den er selve arbeidsgrunnlaget, og en
    # saksbehandler som ikke ser kundene sine kan ikke gjøre jobben.
    # Å ENDRE den er `part:administrer`, og de to gis hver for seg.
    "leser": frozenset({"part:read",
                        "decisions:read", "exceptions:read", "policy:read",
                        "epost:read", "kontinuitet:read",
                        "kundeservice:innhold"}),
    # Compliance/ops: i tillegg sikkerhetsinnsyn.
    # 102 (M-17): `kundeservice:innhold` er OGSÅ `sikkerhet`s, av to
    # grunner. Den ene er strukturell: `sikkerhet` har alltid vært en
    # SUPERMENGDE av `leser`, og et hull i den containment-en ville vært
    # en endring i rollemodellen skjult i en modul-PR. Den andre er
    # saklig: en henvendelse klassifisert som `mistenkelig` blir en
    # SIKKERHETSSAK i M-37s kø, og den som skal behandle den må kunne
    # lese hva som faktisk sto der.
    "sikkerhet": frozenset({"part:read",
                            "decisions:read", "exceptions:read",
                            "policy:read", "security:read",
                            "epost:read", "kontinuitet:read",
                            "kundeservice:innhold"}),
    # Administrator: alt lesende (v1 er rent lese-API; mutasjon er senere).
    # 038: administratoren bestiller kontroller på tenantens egne,
    # verifiserte mål. Scopet gir retten til å FORSØKE — målautorisasjon,
    # policy og frekvens avgjør (bestilleren velger aldri modul/frist/epoch).
    # 044: planen er tenantens (§6) — administratoren oppretter, aktiverer
    # og gjenopptar. Én rolle i v1; en tenant kan senere splitte
    # aktiver/gjenoppta til egne roller uten skjemaendring.
    # M-6 PR-A: administratoren forvalter kildene (koble til/deaktiver
    # postboks — OAuth-flyten i PR-B) og feller flatens dom over utkast
    # (forkast/brukt manuelt — PR-D). Begge er per-handling-scopes, som
    # PR-012s unntaksbehandling: retten til å FORSØKE; 088-vaktene og
    # statusmaskinene avgjør.
    # 089 (M-35): administratoren eier kontinuitetsregisteret og
    # hendelseshåndteringen — write dekker kartinnslag, kontakter,
    # hendelser, tidslinjeposter og lukking (dørene håndhever resten:
    # append-only, etteranalyse-kravet, SP-2).
    "admin": frozenset({"decisions:read", "exceptions:read", "policy:read",
                        "security:read", "bestilling:opprett",
                        "plan:opprett", "plan:aktiver", "plan:gjenoppta",
                        "epost:read", "epost:kilde:administrer",
                        "epost:utkast:behandle",
                        # 183/184: partsregisteret — se og endre.
                        "part:read", "part:administrer",
                        "kontinuitet:read", "kontinuitet:write",
                        # 101 (M-13): avstemmingsregisteret. `okonomi:read`
                        # er et NYTT scope, og det oppsto ikke av vane —
                        # de to kandidatene passet ikke. `decisions:read`
                        # holdes av `leser`, altså enhver ordinær bruker,
                        # og kontobevegelser, motparter og beløp er ikke
                        # allmenn tilstandsinnsikt. `security:read`
                        # beskrives to linjer over med ordene
                        # «Compliance/ops»; et avstemmingsregister er
                        # økonomi og ikke drift, og å låne det scopet
                        # ville gjort beskrivelsen usann for alle de
                        # andre flatene som bruker det.
                        #
                        # KRETSEN ER `admin` ALENE I V1, og det er en
                        # dom og ikke en forglemmelse: verken `leser`
                        # eller `sikkerhet` får det. En tenant som vil
                        # skille regnskapsfører fra administrator kan
                        # definere en snevrere rolle senere, uten
                        # skjemaendring. M-23 (104) og M-24 (105)
                        # GJENBRUKER scopet — det oppstår her fordi
                        # M-13 kommer først.
                        "okonomi:read",
                        # 102 (M-17): å LESE hva en kunde skrev er en
                        # annen handling enn å se køen, og bare den ene
                        # er persondata. Administratoren har begge.
                        #
                        # NAVNET HAR INGEN UNDERSTREK, og det er ikke
                        # smak: husets scopevokabular er
                        # kolon-separerte små bokstaver, og porten i
                        # `test_ui_kontrakt.py` leser rolleguiden med
                        # `"([a-z:]+)"`. Et scope med understrek ville
                        # blitt STILLE DROPPET av den porten — altså en
                        # rolleguide som lovet mindre enn rollen har,
                        # uten at noe ble rødt.
                        "kundeservice:innhold",
                             "firma:inviter"}),
    # PR-012: godkjenner kan behandle unntakskøen — den FØRSTE muterende
    # browserrollen. Scopene er per-handling (approve/reject/escalate) så et
    # reject-scope aldri kan godkjenne (v3-test).
    "godkjenner": frozenset({"decisions:read", "exceptions:read",
                             "exceptions:approve", "exceptions:reject",
                             "exceptions:escalate"}),
    # 194/195: `firma:inviter` — å slippe inn en kollega er å dele ut
    # fullmakter i firmaet, altså administratorens handling. En `leser` som
    # kunne invitere, kunne invitert seg selv en ny konto med flere roller
    # enn hun har.
    #
    # INNLØSNINGEN krever `firma:opprett` (`RUTESCOPE`), altså REGISTRANTENS
    # scope — ikke fordi autoriteten ligger der, men fordi `_autentiser` er
    # bygget for ett påkrevd scope og avviser `None`. Autoriteten er TOKENET;
    # sesjonen beviser bare hvem hun er, og CSRF at det er hennes egen
    # nettleser.
    #
    # KRAVET MÅ UTVIDES SAMMEN MED FIRMAVELGEREN (192): en ansatt i et ANNET
    # firma har ikke `firma:opprett`, og kunne da ikke innløst en invitasjon.
    # I dag er det uten betydning — en bruker med to medlemskap kan ikke
    # logge inn i det hele tatt før velgeren finnes.
    # PR-013: policyforvalteren redigerer utkast OG attesterer aktivering.
    # `policy:write` og `policy:activate` er adskilte scopes: fire-øyne (V6)
    # hviler på at aktivering krever attestasjoner, ikke på at rollen mangler
    # skrivetilgang — men en tenant KAN gi to ulike personer hver sin rolle
    # (kun-skrive vs. kun-aktivere) ved å definere snevrere roller senere.
    "policyforvalter": frozenset({"decisions:read", "policy:read",
                                  "policy:write", "policy:activate"}),
    # PR-015 §3: cross-tenant domeneautoritet er sin EGEN rolle, og den bærer
    # BEVISST ikke `exceptions:approve`/`reject`/`escalate`. En som kan behandle
    # unntakskøen skal ikke dermed kunne avgjøre hvilken kunde plattformen
    # autoriserer for et domene — «`exceptions:handle` alene gir aldri
    # cross-tenant domeneautoritet». Rollen leser saken (`exceptions:read`) og
    # attesterer utfallet; motoren gjør overgangen.
    "domeneadjudikator": frozenset({"decisions:read", "exceptions:read",
                                    "domains:adjudicate"}),
    # 192: REGISTRANTEN — den eneste rollen som ikke tilhører et firma.
    #
    # En helt ny bruker har ingen medlemskap, og `_opprett_sesjon` avviser
    # derfor med `ingen_tilgang` (v3 §2, «ingen JIT») FØR hun rekker å
    # registrere noe: hun kan ikke bli kunde fordi hun ikke er kunde.
    # Callbacken gir henne i stedet et ekte medlemskap på den reserverte
    # tenanten `_registrering`, og da virker resten av autorisasjonen
    # uendret — ingen særtilfeller i sesjonsveien.
    #
    # ETT SCOPE, OG INGEN LESESCOPES. Hun skal kunne opprette et firma og
    # ingenting annet; `decisions:read` her ville gitt henne en tom
    # beslutningsflate å vandre rundt i mens hun ennå ikke er kunde.
    #
    # Rollen står BEVISST utenfor `KUNDEROLLER` (plattformdata.js): en kunde
    # som kunne tildelt den, ville gitt bort retten til å opprette firmaer
    # på plattformen.
    "registrant": frozenset({"firma:opprett"}),
}


def scopes_for_roller(roller) -> frozenset[str]:
    """Unionen av scopes for brukerens roller. Ukjente roller bidrar med
    ingenting (default-deny)."""
    ut: set[str] = set()
    for rolle in roller or ():
        ut |= ROLLE_TIL_SCOPES.get(rolle, frozenset())
    return frozenset(ut)
