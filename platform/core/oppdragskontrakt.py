"""Lukket feltskjema per oppdragstype (v4-delta pkt. 4).

Eiermodulen får plaintext, og det er hele grunnen til at dette finnes:
i det øyeblikket noe forlater krypteringen, er spørsmålet ikke «hvem kan
lese det» men «hva står det i det». Uten et lukket skjema bestemmes svaret
av hva som tilfeldigvis lå i saken.

`api.minimering` gjør det samme for unntakspayloaden og er forbildet:
ALLOWLIST, ikke blocklist. En blocklist må kjenne alle persondatafelter en
connector noensinne finner på å sende, og den listen blir aldri ferdig.

Ett tillegg som ikke fantes i minimeringen: HANDLINGSPREFIKSET GIR ALDRI
FELTBREDDE ALENE. To oppdrag med samme prefiks (`faktura.`) kan ha helt
ulike behov, og å la prefikset styre hvilke felter som slipper ut ville
gjort feltbredden til en funksjon av navnet på handlingen.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass


class Oppdragstypeukjent(KeyError):
    """Ingen registrert oppdragstype. Fail-closed: da sendes ingenting."""


@dataclass(frozen=True)
class Oppdragstype:
    """Én oppdragstype med sitt lukkede felt-sett.

    `felter` er det eiermodulen FÅR SE. `paakrevde` er det den må ha for at
    oppdraget skal kunne opprettes i det hele tatt — mangler ett av dem,
    er oppdraget ufullstendig, og et ufullstendig oppdrag skal ikke ut til
    en utfører som «gjør så godt den kan».
    """
    navn: str
    handlingsprefikser: tuple[str, ...]
    felter: frozenset[str]
    paakrevde: frozenset[str]
    beskrivelse: str = ""
    #: PR-014c: eiermodulen typen hører til, som EKTE modul-id
    #: (`m_wcag_audit`) — aldri det syntetiske `eiermodul:<navn>`.
    #: Autoriteten for HVA modulen får claime er fortsatt registerraden +
    #: releasens kontrakt, og deploy-porten krysser de to kildene. Men
    #: feltet er ikke bare informativt (Codex P1): `_eiermodul_for` binder
    #: nye oppdrag til nettopp denne id-en, fordi claim krever
    #: `oppdrag.eiermodul = auth.modul_id`. Er den None, er typen eierløs
    #: (legacy) og oppdraget bindes til det syntetiske navnet som før.
    eiermodul: str | None = None
    #: PR-014c §5–6: `krever_malautorisasjon` uttrykker et BEHOV, ikke et
    #: bevis — handlingen trenger et positivt autorisert mål. De to feltene
    #: står SAMMEN fordi de må matche hver sin side av aktiveringsporten:
    #: flagget sier at porten gjelder, domenet sier hvilke rader i
    #: `malautorisasjonsvilkar` som kan tilfredsstille den. Implementer
    #: aldri det ene uten det andre.
    krever_malautorisasjon: bool = False
    malautorisasjonsdomene: str | None = None
    #: PR-014c §7 (Codex P1): typen leverer resultatet sitt som et
    #: ARTEFAKT. Da er en vellykket kvittering UTEN `artefakt_id` ikke en
    #: variant, men en selvmotsigelse: oppdraget påstår at kontrollen ble
    #: utført samtidig som det ikke finnes noen rapport å vise til. Se
    #: `mangler_artefaktevidens`.
    produserer_artefakt: bool = False
    #: HVILKEN artefakttype resultatet er (Codex P2). Rapport-lese-API-et
    #: dekrypterer og leverer dokumentet til en flate som kjenner ÉN
    #: skjemaform; uten dette feltet hentet den bare «nyeste promoterte
    #: artefakt på oppdraget», og et artefakt fra en hvilken som helst
    #: annen registrert kontrakt ga 200 med et dokument flaten ikke kan
    #: lese. Navnet står her og ikke i deploy-skriptet fordi begge sider
    #: må mene det samme: registreringen skriver registerraden, lesingen
    #: kjenner den igjen.
    rapport_artefakttype: str | None = None

    def valider(self) -> list[str]:
        feil = []
        mangler = sorted(self.paakrevde - self.felter)
        if mangler:
            feil.append(f"{self.navn}: påkrevde felter som ikke er med i"
                        f" feltlisten: {mangler} — de ville vært påkrevd og"
                        " likevel filtrert bort")
        if not self.handlingsprefikser:
            feil.append(f"{self.navn}: ingen handlingsprefikser — da kan"
                        " ingen handling matche typen")
        if self.krever_malautorisasjon != (
                self.malautorisasjonsdomene is not None):
            feil.append(f"{self.navn}: krever_malautorisasjon og"
                        " malautorisasjonsdomene må settes sammen — et krav"
                        " uten domene kan ingen rad tilfredsstille, og et"
                        " domene uten krav er en port ingen går gjennom")
        if self.produserer_artefakt and self.eiermodul is None:
            feil.append(f"{self.navn}: produserer_artefakt uten eiermodul —"
                        " opplastingskapabiliteten til `/v1/artefakt` er"
                        " modulbundet, så en eierløs type kan aldri levere"
                        " artefaktet kvitteringen ville blitt krevd for")
        if self.produserer_artefakt != (self.rapport_artefakttype is not None):
            feil.append(f"{self.navn}: produserer_artefakt og"
                        " rapport_artefakttype må settes sammen — et"
                        " lovet artefakt uten navngitt type kan leseveien"
                        " ikke kjenne igjen, og en navngitt type på en"
                        " artefaktløs oppdragstype er en form ingen"
                        " kvittering kan levere")
        return feil


#: Registeret. Lukket på samme måte som `api.feil.FEILVEIER`: tabellen ER
#: kontrakten, og en test itererer over den.
#:
#: v1 har to typer, og det er med vilje ikke flere. Hver oppdragstype er en
#: kanal for plaintext ut av plattformen; de skal legges til én om gangen,
#: med sin egen begrunnelse for hvert felt.
OPPDRAGSTYPER: dict[str, Oppdragstype] = {
    "reinnsending": Oppdragstype(
        navn="reinnsending",
        handlingsprefikser=("purring.", "faktura.", "melding."),
        # `ressurs_id` er en ugjennomsiktig referanse, ikke innhold.
        # `kildereferanser` er allerede minimert av `api.minimering` og
        # inneholder per konstruksjon kun {connector, resource_id, field_id}.
        felter=frozenset({"handling", "ressurs_id", "tidspunkt", "valuta",
                          "belop", "kildereferanser", "kategori"}),
        paakrevde=frozenset({"handling", "ressurs_id"}),
        beskrivelse=("R1: en handling som ble stoppet av manglende data, og"
                     " som skal utføres på nytt etter at dataene foreligger.")),
    "verifikasjon": Oppdragstype(
        navn="verifikasjon",
        # `kontroll.` BLIR STÅENDE (Codex P1, runde 11). PR-014c fjernet det
        # først, med den begrunnelsen at reservasjonen var ubrukt: ingen
        # produsent i repoet lager `kontroll.*`-handlinger for denne typen.
        # Den begrunnelsen holder ikke, for den slutter fra KODEN til DATAEN.
        # Handlings-ID-er kommer fra tenantpolicyer, og policyskjemaet
        # (`policy-schema-v0.2.json`, `handlinger[].id`) tillater en fri
        # punktnotert streng. En allerede aktiv policy med f.eks.
        # `kontroll.fakturagrunnlag` er derfor mulig uten at noe i repoet
        # ville vist det — og for den ville fjerningen ikke vært
        # «fail-closed», men et stille tap: `type_for_handling` → None →
        # `_eiermodul_for` → `eiermodul:ukjent`, og oppdraget kan hverken
        # claimes eller minimeres.
        #
        # `kontroll.wcag.` er ikke i konflikt med dette: oppslaget under
        # velger det LENGSTE prefikset som matcher, så WCAG-kontrollen
        # eier sitt eget undernavnerom uten å ta hele `kontroll.`.
        handlingsprefikser=("verifiser.", "kontroll."),
        # Ingen beløp: et verifikasjonsoppdrag skal slå opp mot en
        # autoritativ kilde, ikke få vite hva saken gjaldt i kroner.
        #
        # `vilkaar` er PR-007s eneste utvidelse av oppdragskontrakten:
        # HVILKET krav som skal verifiseres. Uten det måtte verifikatoren
        # gjette ut fra handlingen, og en verifikator som gjetter hva den
        # skal attestere, attesterer noe annet enn det saken manglet.
        # `maalhandling` og `policy_id` er IKKE pynt: attestasjonen
        # verifikatoren produserer må BINDE til den handlingen og den
        # policyen fase 2 skal evalueres mot. `handling` her er
        # verifikasjonshandlingen (`verifiser.<vilkaar>`) — bruker
        # verifikatoren den i attestasjonen, faller `kontroller_binding`
        # i fase 2 med `attestasjon_feil_handling`, og beviset er verdiløst.
        # `vilkaar_sett` (array), ikke `vilkaar` (skalar): form A fra
        # v5-delta. Fase 1 dekker HELE settet av påkrevde vilkår i ÉN
        # generasjon — ellers kan fase 2 aldri bygge en komplett hendelse,
        # siden originalens attestasjoner er minimert bort.
        felter=frozenset({"handling", "ressurs_id", "kildereferanser",
                          "kategori", "vilkaar_sett", "maalhandling",
                          "policy_id", "krav_sett_hash"}),
        paakrevde=frozenset({"handling", "ressurs_id", "vilkaar_sett",
                             "maalhandling", "policy_id", "krav_sett_hash"}),
        beskrivelse=("v3-delta pkt. 5: alle oppslag mot autoritative kilder"
                     " er sideeffektfrie oppdrag utført av en modul med egne"
                     " fullmakter. M-37 rører aldri ERP/bank/CRM selv.")),
    # PR-014c: den første handlingsmodulen. LUKKET payload — de fire
    # feltene er ALT modulen får se: ingen tenantnavn, ingen
    # kundeidentifikator, ingen kontaktdata. `mal_url` er normalisert av
    # urlkontrakten før oppdraget opprettes; `kravsett` er lukket enum
    # (en ny verdi skal være en feil, ikke stillhet) — begge håndheves
    # ved OPPRETTELSEN (bestillingsveien), minimeringen her er siste
    # skanse for feltBREDDEN.
    "kontroll.wcag.nettsted": Oppdragstype(
        navn="kontroll.wcag.nettsted",
        handlingsprefikser=("kontroll.wcag.",),
        felter=frozenset({"mal_url", "kravsett", "omfang", "maks_sider"}),
        paakrevde=frozenset({"mal_url", "kravsett", "omfang"}),
        eiermodul="m_wcag_audit",
        krever_malautorisasjon=True,
        malautorisasjonsdomene="web_hostname",
        produserer_artefakt=True,
        rapport_artefakttype="kontroll.wcag.rapport",
        beskrivelse=("PR-014c: automatisk WCAG-kontroll av et positivt"
                     " autorisert hostname. `ekstern_lesing`-klassen:"
                     " observerbar trafikk ut, ingen ekstern mutasjon;"
                     " målautorisasjon + frekvens håndheves av"
                     " aktiveringsporten, egress/robots av 014b.")),
    # M-57 (klarsignalet §1): evalueringen er RÅDGIVENDE — artefakter,
    # ingenting utad. LUKKET payload: stillingsprofilen (vektede krav),
    # referansen til søknadsbunten i artefaktlageret, antallet (verdi-
    # bundet under — aldri stille avkorting) og omfanget som bærer
    # fristen. Blinding er STANDARD PÅ og er derfor ikke et felt her:
    # å skru den av er en auditert handling i modulflaten (§6), ikke en
    # bestillingsparameter en integrasjon kan liste forbi.
    "rekruttering.evaluering": Oppdragstype(
        navn="rekruttering.evaluering",
        # UTEN punktum til slutt (Codex P1). M-57-familien navngir
        # handlingen NØYAKTIG som oppdragstypen — `rekruttering.evaluering`
        # er både typen og handlingen i 056/057 og i SP-10-seeden, akkurat
        # som `rekruttering.utsending` på utsendingsarmen. Med punktumet
        # var det ingen handling som traff prefikset, så
        # `type_for_handling("rekruttering.evaluering")` ga None: M37s
        # opprettelses- og reparasjonsvei klassifiserte oppdraget som ukjent
        # og skrev `eiermodul:ukjent`, og siden claim krever
        # `oppdrag.eiermodul = auth.modul_id` kunne modulen aldri claimet
        # sitt eget oppdrag. Prefikset uten punktum treffer både den
        # nøyaktige handlingen og et eventuelt senere `...evaluering.<noe>`.
        handlingsprefikser=("rekruttering.evaluering",),
        felter=frozenset({"stillingsprofil_ref", "soknadsbunt_ref",
                          "antall_soknader", "omfang"}),
        paakrevde=frozenset({"stillingsprofil_ref", "soknadsbunt_ref",
                             "antall_soknader", "omfang"}),
        # `m57_ats`, ikke `m_ats` (Codex P2 / Cursor P1). Identiteten er
        # ALLEREDE avgjort og støpt: 056 CHECK-binder utsendingsarmen til
        # `eiermodul = 'm57_ats'`, `opprett_frigivelsesoppdrag` avviser alt
        # annet, og akseptartefaktets `oppsett.modul` er `const m57_ats`.
        # 056 er merget, så å flytte DEN siden er en ny migrasjon på
        # bebodd base — altså ny maskin, ikke en fiks. `m_ats` fantes
        # nøyaktig ett sted i repoet: her. Med to identiteter kunne én
        # modul verken claime evalueringsoppdrag registrert som `m57_ats`
        # eller utsendingsoppdrag registrert som `m_ats`, og
        # akseptartefaktet ville attestert en annen eier enn den som
        # faktisk kjørte jobbene.
        eiermodul="m57_ats",
        produserer_artefakt=True,
        rapport_artefakttype="rekruttering.evalueringsrapport",
        beskrivelse=("M-57: leser og rangerer opptil 5000 søknader mot"
                     " stillingens krav i isolert container — ingen"
                     " ekstern trafikk, ingen mutasjon; utsendelse er en"
                     " egen, signaturbundet vei (056).")),
}


def type_for_handling(handling: str) -> Oppdragstype | None:
    """Oppdragstypen en handling hører til, eller None.

    LENGSTE PREFIKS VINNER (Codex P1, runde 11). Prefiksene var disjunkte
    før PR-014c, og da var «første treff» det samme som «eneste treff».
    Med `kontroll.wcag.` under `kontroll.` er de det ikke lenger, og
    førstetreff ville gjort typen — altså FELTBREDDEN — avhengig av
    rekkefølgen i en dict. Det er nettopp det den gamle
    disjunkthetsinvarianten vernet mot, og lengste treff gir samme vern
    uten å måtte gi fra seg et helt navnerom: `kontroll.wcag.nettsted`
    treffer WCAG-typen (13 tegn), `kontroll.fakturagrunnlag` treffer
    `verifikasjon` (9 tegn), og ingen av dem avhenger av iterasjonen.

    Det som fortsatt IKKE er tillatt, er at to ULIKE typer deklarerer
    NØYAKTIG samme prefiks — da finnes det ikke noe lengste treff å velge,
    og `test_oppdragstypenes_prefikser_er_entydige` avviser det.
    """
    if not isinstance(handling, str):
        return None
    beste: Oppdragstype | None = None
    beste_lengde = -1
    for t in OPPDRAGSTYPER.values():
        for p in t.handlingsprefikser:
            if handling.startswith(p) and len(p) > beste_lengde:
                beste, beste_lengde = t, len(p)
    return beste


#: Måldomenene og hvilket hendelsesfelt de peker på. Lukket på samme måte
#: som `OPPDRAGSTYPER`: et ukjent domene er en feil, ikke en port som er av.
MALDOMENEFELT: dict[str, str] = {"web_hostname": "mal_url"}

#: Hendelsesfeltet som BÆRER målbindingen. Det er `ressurs_id` og ikke et
#: nytt felt fordi `ressurs_id` alt ligger i `BINDINGSFELT`, altså inne i
#: de signerte bytene, og `attestering.kontroller_binding` alt krever at
#: attestasjonen bærer samme verdi som hendelsen — se `malbindingsbrudd`.
#: Navnet står som konstant fordi flere porter må vite HVILKET felt som er
#: server-bundet: kjøretidsbindingen krever at det er det normaliserte
#: målet, og aktiveringsporten krever at frekvenstelleren grupperer på
#: nettopp det (ellers teller taket per forespørsel, ikke per mål).
MALBINDINGSFELT = "ressurs_id"


#: Vertsnavnet i den ENE formen Python og nettleseren garantert leser
#: likt: ren ASCII, bokstaver/siffer/bindestrek, minst to etiketter, og en
#: toppetikett som begynner med en bokstav.
#:
#: Alt utenfor dette er ikke «uvanlig», det er TVETYDIG — se
#: `normaliser_vertsnavn`. Punycode (`xn--p1ai`) er med vilje innenfor:
#: den formen er allerede nettleserens egen normalform, så de to sidene
#: leser den likt. Toppetiketten «begynner med bokstav» er det som skiller
#: et DNS-navn fra en IPv4-lignende verdi (`0x7f.1`, `127.0.0.1`), som
#: nettleseren normaliserer med helt egne regler.
_VERTSNAVN = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*"
    r"\.[a-z]([a-z0-9-]{0,61}[a-z0-9])?\Z")


def normaliser_vertsnavn(raa: object) -> str | None:
    """https-URL -> vertsnavn i normalform, ellers None.

    `urlsplit().hostname` gjør småbokstaver og fjerner credentials og port;
    en avsluttende rotprikk (`example.com.`) fjernes fordi den navngir
    NØYAKTIG samme vert og ellers ville vært et gratis omgåelsestegn.
    `d.port` er en property som selv kaster på ulovlig port — den leses
    inne i vakten slik at et ubetrodd felt gir None, aldri et unntak.

    DEN SOM LESER URL-EN TIL SLUTT ER CHROMIUM (Codex P1). `mal_url` går
    UENDRET videre til motoren, mens denne funksjonen avgjør hvilken vert
    plattformen mener er autorisert — både i `malbindingsbrudd` og i
    kvitteringens `ressurs_id`. Er Python og WHATWG-parseren uenige om
    hvor verten slutter, navngir HELE plattformen én vert mens nettleseren
    besøker en annen:

      * `https://allowed.example\\@evil.example/` — for WHATWG er
        omvendt skråstrek det samme som `/` i en special-scheme-URL, så
        authority er `allowed.example` og resten er sti. `urlsplit` regner
        `\\` som en helt vanlig tegn i netloc, tar delen etter SISTE `@`
        som vert, og svarer `evil.example`. Attestasjonen for
        `evil.example` — en vert angriperen faktisk kontrollerer — ville
        altså passert bindingen, mens trafikken gikk til
        `allowed.example`. Nøyaktig den uautoriserte utgående trafikken
        målautorisasjonen finnes for å hindre, med et bevis som ser
        gyldig ut hele veien.
      * `https://evil%2eexample/`, `https://пример.example/`,
        `https://evil.example。x/` — nettleseren prosentdekoder,
        IDNA-mapper og deler på ideografisk punktum; `urlsplit` gjør
        ingen av delene og gir en annen streng.

    Vakten er derfor todelt, og begge deler trengs: omvendt skråstrek
    avvises på RÅSTRENGEN (i den farlige formen over er `hostname` en
    plettfri LDH-streng, så et mønster alene ser ingenting), og verten må
    stå i `_VERTSNAVN` — den formen begge parsere leser likt.

    Å avvise er riktig utfall og ikke et tap: kalleren behandler None som
    `malautorisasjon_mal_ugyldig` / manglende binding, altså fail-closed.
    En vert som er tvetydig for nettleseren er en vert plattformen ikke
    KAN love noe om, og en bestilling ingen kan lese entydig skal stoppe
    hos den som bestilte den.
    """
    from urllib.parse import urlsplit
    if not isinstance(raa, str) or not raa:
        return None
    # FØR parsingen: `urlsplit` skjuler den — se `hostname` over.
    # Nettleseren skriver om `\` til `/` overalt i en special-scheme-URL,
    # også i stien, så URL-en motoren HENTER er ikke den som ble bestilt.
    if "\\" in raa:
        return None
    try:
        d = urlsplit(raa)
        vert, _ = d.hostname, d.port
    except ValueError:
        return None
    if d.scheme != "https" or not vert:
        return None
    vert = vert.rstrip(".")
    if len(vert) > 253 or not _VERTSNAVN.match(vert):
        return None
    return vert


#: Tegnene som prosentkodes I TILLEGG til C0-kontrolltegn, mellomrom, DEL
#: og alt over ASCII (som UTF-8) — for STIEN.
#:
#: Settet er RFC 3986 sitt, ikke én nettlesers: det er nøyaktig de
#: ASCII-tegnene som ikke kan stå rått i en sti (`path` tillater
#: `unreserved`, `pct-encoded`, `sub-delims`, `:` og `@`). `?` og `#` står
#: der også, men `urlsplit` har alt delt dem av før stien kommer hit, og
#: `\` avvises i sin helhet av `normaliser_vertsnavn`.
#:
#: HVORFOR RFC-ENS SETT OG IKKE CHROMIUMS BORDOPPSLAG (Codex P2, fjerde
#: runde på URL-identitet): de tre foregående rundene jaktet alle på
#: «hvilke tegn koder nettleseren?», og svaret var nytt hver gang — sist
#: `^`, som WHATWG holder utenfor sitt path-sett mens Chromium koder det.
#: Det spørsmålet trenger vi ikke svare på. Normaliseringen kjører på
#: BEGGE sider av sammenligningen (bestillingen og motorens utdata), og
#: `%` kodes aldri om, så det er trygt å kode et OVERSETT av det motoren
#: koder: koder motoren `^`, står `%5E` på begge sider allerede; koder den
#: ikke, gjør vi begge sider til `%5E`. Symmetrien — ikke tabellen — er
#: det som holder.
#:
#: Og over-kodingen kan ikke slå sammen to sider serveren skiller, nettopp
#: fordi settet er RFC-ens: disse tegnene kan ikke stå rått i en gyldig
#: sti, så det finnes ingen rå form å forveksle den kodede med. Tegn som
#: ER lovlige rått (`,`, `;`, `=`, `+`, `:`, `@` ...) rører vi ikke.
_STI_KODES = '"<>`{}^|[]'

#: Samme sett for QUERY-en, som tillater alt stien gjør pluss `/` og `?`.
#: `'` kommer i tillegg: RFC-en tillater den rått, men nettleseren koder
#: den i query-en for special-schemes (`https`), så en rå `'` overlever
#: aldri en ekte navigasjon — serveren ser `%27` uansett.
#:
#: `+` er IKKE med, og `%2B` dekodes aldri. De to betyr hver sin ting for
#: en form-kodet server (`+` er mellomrom, `%2B` er pluss), og det er
#: nettopp skillet mellom `+` og `%20` som ikke skal viskes ut her.
_QUERY_KODES = _STI_KODES + "'"


def _nettleserkodet(tekst: str, kodes: str = _STI_KODES) -> str:
    """Delen slik NETTLESEREN SKRIVER den: prosentkodet etter `kodes`.

    `urlsplit` er en parser og rører ikke tegnene. WHATWG-parseren i
    Chromium prosentkoder mens den navigerer, så en bestilling på
    `https://kunde.example/café?q=café` besøkes og rapporteres som
    `https://kunde.example/caf%C3%A9?q=caf%C3%A9`.

    Kodingen går BARE én vei, aldri tilbake:

      * `%` kodes ALDRI. Det gjør funksjonen idempotent — `caf%C3%A9` er
        allerede den formen nettleseren rapporterer, og skulle den blitt
        til `caf%25C3%25A9`, hadde normaliseringen selv laget avviket den
        er her for å fjerne. Nettleseren lar også en løs `%` stå.
      * Vi DEKODER ikke unødvendige escapes og skriver ikke om
        `%c3%a9` til `%C3%A9`. Nettleseren gjør ingen av delene, og en
        normalisering som gjetter feil vei gjør to ULIKE sider like —
        feilretningen skal være avvisning, ikke stille sammenslåing.

    Rekkefølgen er urørt: dette er en passering tegn for tegn, ikke en
    parsing. Query-parametre kommer ut i den rekkefølgen de kom inn.
    """
    ut: list[str] = []
    for tegn in tekst:
        if tegn == "%" or ("\x20" < tegn < "\x7f" and tegn not in kodes):
            ut.append(tegn)
        else:
            ut.extend(f"%{b:02X}" for b in tegn.encode("utf-8"))
    return "".join(ut)


def _uten_punktsegmenter(sti: str) -> str:
    """Stien slik NETTLESEREN LESER den: `.` og `..` løst opp.

    `urlsplit` gir stien tegn for tegn. Nettleseren gjør noe annet:
    WHATWG-URL-parseren løser punktsegmentene mens den leser stien, så
    `https://kunde.example/a/../side` ER `https://kunde.example/side` for
    Chromium, og det er den formen som besøkes og rapporteres tilbake.

    Reglene er WHATWG sine, ikke RFC 3986 sine, fordi det er nettleseren
    som avgjør hva som faktisk ble besøkt:

      * `%2e` og `%2E` teller som punktum (`/a/%2e%2e/side` er `/side`).
        Gjorde de ikke det, kunne en prosentkodet skrivemåte av nøyaktig
        samme navigasjon skli forbi som «en annen side».
      * `..` under roten går ikke i minus — den blir stående på roten.
        RFC-en etterlater `/..`-rester; nettleseren gjør ikke det.
      * ET AVSLUTTENDE punktsegment gir en avsluttende `/`: `/a/..` er
        `/`, og `/a/.` er `/a/`. Det er ikke pynt — `/a` og `/a/` kan
        være to forskjellige ressurser, og nettleseren ber om den siste.

    Resten av stien røres IKKE HER — store bokstaver og tomme segmenter
    står som de står, og prosentkodingen er `_nettleserkodet` sin jobb.
    """
    #: `%2e`/`%2E` er punktum for WHATWG-parseren, derfor `.lower()`.
    segmenter = sti.split("/")[1:]
    ut: list[str] = []
    for i, segment in enumerate(segmenter):
        lav = segment.lower()
        siste = i == len(segmenter) - 1
        if lav in ("..", ".%2e", "%2e.", "%2e%2e"):
            if ut:
                ut.pop()
            if siste:
                ut.append("")
        elif lav in (".", "%2e"):
            if siste:
                ut.append("")
        else:
            ut.append(segment)
    return "/" + "/".join(ut)


def nettleserlest_sti(sti: str) -> str:
    """Stien slik nettleseren både SKRIVER og LESER den — prosentkodet
    etter WHATWG-settet, og med punktsegmentene løst opp.

    Kodingen står FØRST: den rører verken `.` eller `%2e`, så de to
    lesningene er upåvirket av hverandre, og en allerede kodet sti går
    uendret gjennom begge.

    Funksjonen bor HER og ikke i modulen (Codex P2), av samme grunn som
    `normaliser_vertsnavn` gjør det: den er URL-semantikk, ikke
    WCAG-semantikk, og den brukes nå av BEGGE sider — `rapport._delt_url`
    sammenligner bestilling mot motorutdata med den, og
    `bryter_feltkontrakten` måler rapportformens lengde med den. To
    normaliseringer ville vært to svar.
    """
    return _uten_punktsegmenter(_nettleserkodet(sti))


def nettleserlest_query(query: str) -> str:
    """Query-en slik nettleseren SKRIVER den — prosentkodet etter
    `_QUERY_KODES`.

    Punktsegmenter finnes ikke her: `..` i en query er data, ikke
    navigasjon, og skal stå som den står. Det er BARE kodingen som er
    felles med stien.

    Rekkefølge og form ellers er urørt (Codex P2). En query er ubetrodd
    data for oss — vi vet ikke om serveren ruter på `a=1&b=2` og `b=2&a=1`
    likt, og vi vet ikke om den leser `+` som mellomrom. Det eneste vi vet
    er hva NETTLESEREN gjør før serveren ser noe: den prosentkoder tegnene
    som ikke kan stå rått. Da er det bare den omskrivingen som skal
    speiles, ikke en sortering eller en `+`/`%20`-tolkning.
    """
    return _nettleserkodet(query, _QUERY_KODES)


def rapporturl(raa: object) -> str | None:
    """https-URL i RAPPORTFORM — vert i normalform, ikke-standard port,
    nettleserlest sti, uten query og fragment. -> None når URL-en ikke lar
    seg lese entydig.

    Dette er DEN avledningen: `rapport._delt_url` navngir sidene i
    rapporten med den, og `bryter_feltkontrakten` måler lengden på den.
    Sto lengdegrensa på råstrengen i stedet, ville den målt noe annet enn
    skjemaet måler — prosentkodingen EKSPANDERER (`é` blir seks tegn), så
    en råstreng under grensa kan gi en rapportform over den.

    STANDARDPORTEN ER IKKE MED: `https://kunde.example:443/side` og
    `https://kunde.example/side` ber om nøyaktig samme ressurs, og
    Chromium serialiserer den uten porten. Ikke-standardporter bæres
    videre — de skiller faktisk to endepunkter fra hverandre. Skjemaet er
    alltid https her, så 443 er den eneste standardporten som kan dukke
    opp.

    EN LØS SURROGAT gir None, ikke et unntak: `json.loads` leverer
    `"\\ud800"` som et tegn `str.encode` ikke kan skrive, og en URL ingen
    nettleser kan be om er nettopp en URL som ikke lar seg lese entydig.
    Sto den nakne `UnicodeEncodeError` igjen, ville den sluppet ut av
    forhåndsporten som noe annet enn et avvist felt.
    """
    from urllib.parse import urlsplit, urlunsplit
    vert = normaliser_vertsnavn(raa)
    if vert is None:
        return None
    # `normaliser_vertsnavn` har alt lest `d.port` innenfor sin egen vakt,
    # så en ulovlig port ga None over og kan ikke kaste her.
    d = urlsplit(str(raa))
    nettsted = vert + (f":{d.port}" if d.port and d.port != 443 else "")
    try:
        sti = nettleserlest_sti(d.path or "/")
    except UnicodeEncodeError:
        return None
    return urlunsplit(("https", nettsted, sti, "", ""))


def malvert(oppdragstype: object, payload: object) -> str | None:
    """Verten et oppdrag av denne TYPEN er autorisert for, eller None.

    ÉN avledning, for alle som må navngi målet. Kjøretidsbindingen
    (`malbindingsbrudd`), kvitteringens `ressurs_id` og rapportens
    sidebinding svarer på nøyaktig samme spørsmål — «hvilken vert har noen
    autorisert her?» — og gjorde de det hver for seg, ville første avvik i
    normaliseringen betydd at plattformen navngir én vert i beviset og en
    annen i evidensen. Da er bindingen pynt.

    Oppslaget går via TYPEN og ikke via et fast feltnavn: hvilket
    payloadfelt som bærer målet er en egenskap ved måldomenet
    (`MALDOMENEFELT`), og en ny oppdragstype med et annet domene skal
    kunne komme til uten at kallerne endres.

    -> None når typen ikke krever målautorisasjon, når måldomenet ikke
    peker på noe felt, eller når feltet ikke lar seg lese entydig. Alle
    tre er fail-closed hos kalleren: ingen vert å binde til betyr at
    oppdraget ikke skal utføres, ikke at bindingen er valgfri.
    """
    t = OPPDRAGSTYPER.get(oppdragstype) if isinstance(oppdragstype, str) \
        else None
    if t is None or t.malautorisasjonsdomene is None:
        return None
    felt = MALDOMENEFELT.get(t.malautorisasjonsdomene)
    if felt is None or not isinstance(payload, dict):
        return None
    return normaliser_vertsnavn(payload.get(felt))


def avtrykk(raa: object) -> str:
    """Kort, stabilt avtrykk av en verdi — til revisjonsspor, ikke til
    gjenoppretting.

    `Grunn.params` ender i `revisjonslogg.begrunnelse`, som er en
    KLARTEKST-kolonne: alt som legges der blir liggende permanent, utenfor
    det krypterte sporet. Ressurs-ID-er og payload-avledede verdier
    (vertsnavn fra `mal_url`) hører ikke hjemme der.

    Avtrykket bevarer det etterforskningen faktisk trenger — er det samme
    verdi som sist? er de to sidene like? — uten å publisere verdien. Det
    er bevisst IKKE en hemmelighold-mekanisme: et vertsnavn har lav
    entropi, så den som allerede har en kandidat kan bekrefte den. Skillet
    er mellom å KUNNE bekrefte en mistanke og å FÅ utlevert kundens
    identifikator av loggen selv.

    TOTAL for enhver JSON-verdi (Codex P2). `raa` er her `ressurs_id` rett
    fra en ubetrodd hendelse, og `json.loads` godtar et ensomt surrogat
    (`"\\ud800"`). Med `str.encode("utf-8")` kunne avsenderen dermed velge
    at BINDINGSKONTROLLEN kaster i stedet for å svare: `malbindingsbrudd`
    kjører før motorens unntaksvakt, så forespørselen døde uten
    `malautorisasjon_feil_mal` i revisjonssporet — nøyaktig posten som
    navngir et ulovlig målbindingsforsøk. Se `tekstbytes.utf8` for hvorfor
    kodingen forblir injektiv.
    """
    import hashlib

    import tekstbytes
    # Typenavnet er med i det som hashes, så `None` og strengen `"None"`
    # ikke får samme avtrykk — ellers ville de vært umulige å skille i
    # sporet.
    return hashlib.sha256(
        tekstbytes.utf8(f"{type(raa).__name__}:{raa}")).hexdigest()[:16]


def malbindingsbrudd(handling: object, event: dict) -> tuple[str, dict] | None:
    """None == målautorisasjonen gjelder DEN verten som faktisk kontrolleres.

    Codex P1: aktiveringsporten beviste bare at handlingen bærer et vilkår
    som er REGISTRERT for `web_hostname` — ikke at attestasjonen dekker
    verten i `mal_url`. Kjøretidsbindingen sammenlignet `ressurs_id`, men
    ingen av dem så på `mal_url`. En hendelse kunne derfor gjenbruke en
    ekte, gyldig `domenekontroll_verifisert`-attestasjon med sin egen
    `ressurs_id` og be om kontroll av et HELT ANNET vertsnavn — altså
    trafikk ut mot et mål ingen har autorisert, med et bevis som ser
    perfekt ut hele veien.

    Bindingen legges på `ressurs_id` og ikke på et nytt felt med vilje:
    `ressurs_id` ligger allerede i `BINDINGSFELT`, altså inne i de SIGNERTE
    bytene, og `attestering.kontroller_binding` krever allerede at
    attestasjonen bærer samme verdi som hendelsen. Kreves det at hendelsens
    `ressurs_id` ER det normaliserte vertsnavnet, arver attestasjonen
    bindingen gratis — uten en formatendring som måtte rulles ut på tvers
    av verifikatorer før den kunne håndheves.

    -> (kode, detaljer) ved brudd, slik at kalleren kan lage sin egen Grunn.
    """
    t = type_for_handling(handling) if isinstance(handling, str) else None
    if t is None or t.malautorisasjonsdomene is None:
        return None
    felt = MALDOMENEFELT.get(t.malautorisasjonsdomene)
    if felt is None:
        # Et måldomene ingen vet hvilket felt peker på kan ikke bindes, og
        # da skal handlingen ikke gå. Fail-closed er hele posituren her.
        return ("malautorisasjon_domene_ukjent",
                {"domene": t.malautorisasjonsdomene})
    vert = normaliser_vertsnavn(event.get(felt))
    if vert is None:
        return ("malautorisasjon_mal_ugyldig", {"felt": felt})
    if event.get(MALBINDINGSFELT) != vert:
        # KLARTEKSTLEKKASJEN (Codex P1). Detaljene her blir til
        # `Grunn.params`, og `sikker_beslutning_pg` serialiserer dem inn i
        # `revisjonslogg.begrunnelse` — en klartekstkolonne. Kopierte vi
        # `vert` og `ressurs_id` ordrett dit, la en HVILKEN SOM HELST
        # innsender igjen en vilkårlig kundeidentifikator og et vertsnavn
        # fra payloaden permanent i klartekst, utenfor det krypterte
        # sporet, ved å sende en forespørsel som feiler. Resten av
        # plattformen behandler nettopp de verdiene som kryptert data og
        # legger bare grunnkoder i kolonnen.
        #
        # Igjen står feltnavnet (som er konfigurasjon, ikke data) og to
        # avtrykk: nok til å se at de to sidene er ULIKE og til å knytte
        # gjentatte forsøk sammen, uten å publisere verdiene.
        return ("malautorisasjon_feil_mal",
                {"felt": felt,
                 "forventet_avtrykk": avtrykk(vert),
                 "i_forespoersel_avtrykk": avtrykk(
                     event.get(MALBINDINGSFELT))})
    return None


def minimer(oppdragstype: str, payload: dict) -> dict:
    """Payloaden slik eiermodulen får se den. Kaster Oppdragstypeukjent.

    Verdier som ikke er enkle JSON-typer droppes, akkurat som i
    `api.minimering`: en struktur vi ikke har inspisert er en struktur vi
    ikke vet innholdet i, og «den så ut som en liste» er ikke en
    inspeksjon.
    """
    t = OPPDRAGSTYPER.get(oppdragstype)
    if t is None:
        raise Oppdragstypeukjent(oppdragstype)
    ut: dict[str, object] = {}
    for felt in sorted(t.felter):
        if felt not in payload:
            continue
        verdi = payload[felt]
        if isinstance(verdi, (str, int, float, bool)) or verdi is None:
            ut[felt] = verdi
        elif felt == "vilkaar_sett" and isinstance(verdi, list):
            # Ren liste av vilkårsnavn. Navnene står i kundens policy og er
            # ikke persondata; alt annet enn strenger droppes.
            rene = [v for v in verdi if isinstance(v, str) and v.strip()]
            if rene:
                ut[felt] = sorted(rene)
        elif felt == "kildereferanser" and isinstance(verdi, list):
            # Kildereferanser er allerede normalisert til nøyaktig tre
            # nøkler av `api.minimering._kildereferanser`. Vi gjentar
            # kontrollen her fordi denne modulen ikke kan VITE at den
            # forrige kjørte — payloaden kommer fra en dekryptert rad, og
            # en rad kan være skrevet av en eldre versjon.
            rene = [{k: str(r[k]) for k in ("connector", "resource_id",
                                            "field_id")}
                    for r in verdi
                    if isinstance(r, dict)
                    and all(k in r for k in ("connector", "resource_id",
                                             "field_id"))]
            if rene:
                ut[felt] = rene
    return ut


def mangler_paakrevde(oppdragstype: str, minimert: dict) -> list[str]:
    """Påkrevde felter som ikke overlevde minimeringen. Tom == komplett."""
    t = OPPDRAGSTYPER.get(oppdragstype)
    if t is None:
        raise Oppdragstypeukjent(oppdragstype)
    return sorted(f for f in t.paakrevde if not minimert.get(f))


#: VERDIKONTRAKTEN: felter der TILSTEDEVÆRELSE ikke er nok, fordi bare et
#: lukket sett av verdier gir et oppdrag som kan utføres (Codex P1).
#:
#: `minimer` bestemmer feltBREDDEN og `mangler_paakrevde` at de påkrevde
#: feltene overlevde. Ingen av dem ser på VERDIEN, og for
#: `kontroll.wcag.nettsted` var det hullet konkret: et oppdrag med
#: `omfang: "alt"`, `maks_sider: 0` eller et ukjent `kravsett` ble
#: opprettet, claimet og KJØRT — og først `rapport.bygg`, etter at
#: motoren hadde vært ute på kundens nettsted, oppdaget at bestillingen
#: aldri kunne gi en gyldig rapport. Ekstern, observerbar trafikk mot
#: noen andres nettsted for et oppdrag som var dødfødt fra opprettelsen.
#:
#: Tabellen står HER og ikke i modulen fordi begge sidene må lese den
#: samme: bestillingsveien (M-37) skal ikke opprette oppdraget, og
#: utføreren skal avvise det uten å røre målet om det likevel finnes.
FELTVERDIER: dict[str, dict[str, tuple]] = {
    # M-57: ett omfang i v1 — «bunt». Enumen er lukket med vilje: en ny
    # verdi skal være en feil (og en fristbeslutning), ikke stillhet.
    "rekruttering.evaluering": {"omfang": ("bunt",)},
    "kontroll.wcag.nettsted": {
        # Samme lukkede enum som rapportskjemaets `kravsett`: et oppdrag
        # med en annen verdi kan ikke gi en rapport som validerer.
        "kravsett": ("wcag21_aa",),
        # Manifestets omfang. Fristene henger på nettopp disse to.
        "omfang": ("enkeltside", "nettsted"),
    },
}

#: Heltallsfelter med LUKKEDE grenser (begge inklusive), per type. Øvre
#: grense er ikke pynt: `maks_sider: 200` er et oppdrag ingen rapport kan
#: oppfylle, siden `sider_kontrollert` har `maxItems: 50`.
FELTGRENSER: dict[str, dict[str, tuple[int, int]]] = {
    "kontroll.wcag.nettsted": {"maks_sider": (1, 50)},
    # M-57-klarsignalet §4: 5000 er HARD — 5001 avvises ved validering,
    # aldri stille avkorting (katalogens løfte er «opptil 5000», og et
    # oppdrag som fikk 5001 har alt brutt det før parseren startet).
    "rekruttering.evaluering": {"antall_soknader": (1, 5000)},
}

#: Felter som må være en IKKE-TOM STRENG, per type (Codex P2).
#:
#: `minimer` bevarer skalarer som de er, og `mangler_paakrevde` godtar
#: enhver sann verdi — så `stillingsprofil_ref: 123` overlevde begge og ble
#: køet. Modulens eget payload-skjema krever `string, minLength 1`, men det
#: kjører først når UTFØRELSEN har startet: oppdraget var da alt opprettet,
#: claimet og talt. En referanse som ikke er en referanse skal avvises der
#: bestillingen tas imot, ikke der den utføres — samme begrunnelse som
#: `FELTGRENSER` har for `maks_sider`.
FELTSTRENGER: dict[str, tuple[str, ...]] = {
    "rekruttering.evaluering": ("stillingsprofil_ref", "soknadsbunt_ref"),
}

#: URL-felter hvis RAPPORTFORM (`rapporturl`) har en lengdegrense, per
#: type (Codex P2). Grensa er rapportskjemaets egen `maxLength` på
#: `sider_kontrollert[].url`.
#:
#: Uten den slapp et fullt lovlig https-mål — riktig vert, riktig omfang —
#: gjennom både forhåndsporten og `_ressursbinding` bare fordi STIEN gjorde
#: den ferdige URL-en for lang. Bestillingen ble så skannet eksternt, og
#: avvist først da `rapportskjema.SKJEMA` validerte siden som kom tilbake.
#: `ekstern_lesing` er klassen der den unødvendige forespørselen ER skaden,
#: og denne var unødvendig på nøyaktig samme måte som `maks_sider: 200`: vi
#: kunne visst det før vi kontaktet målet.
#:
#: Målt på RAPPORTFORMEN, ikke på råstrengen: prosentkodingen ekspanderer
#: (`é` blir seks tegn), så en råstreng under grensa kan gi en rapportform
#: over den — og det er rapportformen skjemaet måler.
FELTURLLENGDER: dict[str, dict[str, int]] = {
    "kontroll.wcag.nettsted": {"mal_url": 2000},
}


#: UTFØRELSESFRISTEN typen ber om, i sekunder, valgt av ETT felt i
#: payloaden: {type: (felt, {feltverdi: sekunder})} (Codex P1).
#:
#: Fristen sto som én generisk konstant (`arbeider.UTFORELSESFRIST_S`,
#: 24 timer) for HVER oppdragstype. For WCAG-kontrollen var det ikke en
#: romslig frist, men en annonsert frist stacken ikke holdt: manifestet
#: lover 30 min for `enkeltside` og 60 min for `nettsted`, og en kontroll
#: kunne fullføre og bli kvittert et helt DØGN etter det. Skaden er ikke
#: bare et brutt løfte:
#:
#:   * eier-leasen (migrasjon 037) strekkes til `utforelsesfrist`, så en
#:     krasjet kontroll ble liggende ureclaimet i 24 timer i stedet for
#:     i én,
#:   * og `ekstern_lesing` mot kundens nettsted fikk et døgnlangt vindu
#:     der bestillingen sa én time.
#:
#: Fristen hører til KONTRAKTEN og ikke til modulen: den skrives på
#: oppdragsraden ved opprettelsen, og både lease, claim og
#: kapabilitetene leses av plattformen ut fra den raden.
UTFORELSESFRIST_VALG: dict[str, tuple[str, dict[object, int]]] = {
    "kontroll.wcag.nettsted": ("omfang", {"enkeltside": 30 * 60,
                                          "nettsted": 60 * 60}),
    # M-57 (klarsignalet §4): 240 min for evalueringen — 5000 søknader
    # med porsjonsvis parsing. Tallet REVERIFISERES mot målt prøvekjøring
    # før modulen aksepteres; avviker det, oppdateres klarsignalet, aldri
    # porten (fristen svekkes ikke for å redde en treg kjøring).
    "rekruttering.evaluering": ("omfang", {"bunt": 240 * 60}),
}


def utforelsesfrist_s(oppdragstype: str, minimert: dict) -> int | None:
    """Typens egen utførelsesfrist i sekunder, eller None når typen ikke
    deklarerer noen (og den generiske fristen gjelder).

    Er valgfeltet uleselig, gis den STRENGESTE fristen typen har, ikke
    den generiske. `bryter_feltkontrakten` avviser allerede en slik
    payload ved opprettelsen, så tilstanden skal ikke være nåbar — men
    skulle den bli det, er en for KORT frist et oppdrag som må gjøres om
    igjen, mens en for lang er nettopp den stille overskridelsen denne
    tabellen finnes for å hindre.
    """
    valg = UTFORELSESFRIST_VALG.get(oppdragstype)
    if valg is None:
        return None
    felt, frister = valg
    return frister.get(minimert.get(felt)) or min(frister.values())


def bryter_feltkontrakten(oppdragstype: str, minimert: dict) -> list[str]:
    """Feltene hvis VERDI ligger utenfor typens lukkede kontrakt.

    -> sortert liste av feltnavn; tom liste == ingen brudd. Kaster
    `Oppdragstypeukjent` for en ukjent type, som resten av modulen.

    Bare feltnavnene rapporteres, aldri verdiene: navnene er
    konfigurasjon, verdiene er saksdata, og kallstedene her skriver
    grunnen sin til `revisjonslogg.begrunnelse` og til feilkvitteringer —
    samme skille som `malbindingsbrudd` gjør for avtrykkene sine.

    Et felt som MANGLER er ikke et brudd her: det er `mangler_paakrevde`
    sin jobb, og et valgfritt felt (`maks_sider`) skal kunne være borte.
    """
    if oppdragstype not in OPPDRAGSTYPER:
        raise Oppdragstypeukjent(oppdragstype)
    brudd = set()
    for felt, lovlige in FELTVERDIER.get(oppdragstype, {}).items():
        if felt in minimert and minimert[felt] not in lovlige:
            brudd.add(felt)
    for felt, (nedre, ovre) in FELTGRENSER.get(oppdragstype, {}).items():
        if felt not in minimert:
            continue
        v = minimert[felt]
        # `bool` er en `int` i Python, og `True` ville passert `1 <= v`.
        # Et sidebudsjett på «sant» er ikke et tall noen har bestilt.
        if isinstance(v, bool) or not isinstance(v, int) or not (
                nedre <= v <= ovre):
            brudd.add(felt)
    for felt in FELTSTRENGER.get(oppdragstype, ()):
        if felt not in minimert:
            continue
        v = minimert[felt]
        if not isinstance(v, str) or not v.strip():
            brudd.add(felt)
    for felt, maks in FELTURLLENGDER.get(oppdragstype, {}).items():
        if felt not in minimert:
            continue
        # En ULESELIG URL er ikke et brudd HER. Den har sin egen, mer
        # presise feilkode på begge veier (`malautorisasjon_mal_ugyldig`
        # ved opprettelsen, `_ressursbinding` hos utføreren), og å melde
        # den som «feltet er for langt» ville gitt bestilleren feil grunn.
        u = rapporturl(minimert[felt])
        if u is not None and len(u) > maks:
            brudd.add(felt)
    return sorted(brudd)


# ---------------------------------------------------------------------------
# Verifikasjonskvitteringen — den ENESTE bæreren av en attestasjon
# ---------------------------------------------------------------------------
#
# v2-delta pkt. 4-5. Signaturen dekker en KANONISK (JCS) konvolutt med
# nøyaktig disse feltene og ingen andre. Lukket format, samme prinsipp som
# artefaktskjemaet: en ukjent nøkkel er en FEIL, ikke stillhet.
#
# Hvorfor en egen kvitteringstype: en ordinær utførelseskvittering skal
# ALDRI kunne bære en attestasjon. Var det ett felles skjema, ville en
# eiermodul med `orders:execute`-scope kunnet levere «bevis» for et vilkår
# den ikke har fullmakt til å attestere — og hele skillet mellom å UTFØRE
# og å ATTESTERE ville vært en navnekonvensjon.

KVITTERINGSTYPER = ("utforelseskvittering_v1", "verifikasjonskvittering_v1")

#: `krav_sett`-elementets LUKKEDE, versjonerte skjema (Scope v2 pkt. 3.3).
#: Ukjent felt er en valideringsfeil, ikke stillhet — samme prinsipp som
#: artefaktskjemaet og resten av plattformen.
KRAV_SETT_SKJEMAVERSJON = 1
KRAV_ELEMENT_FELTER = frozenset({"vilkaar", "ressurs_id", "innhentbar"})


def valider_krav_sett(krav_sett: object) -> list[str]:
    """Tom liste == gyldig. Kaster aldri.

    Settet er SAKSBUNDET og frosset ved klassifisering (v6 pkt. 1): det
    slås aldri opp på nytt mot aktiv policy under fase 1 eller 2. Endrer
    policyen vilkårene etterpå, påvirker det aldri en pågående sak.
    """
    if not isinstance(krav_sett, dict):
        return ["krav_sett er ikke et objekt"]
    ukjente = sorted(set(krav_sett) - {"skjemaversjon", "krav"})
    if ukjente:
        return [f"krav_sett har ukjente felter: {ukjente}"]
    if krav_sett.get("skjemaversjon") != KRAV_SETT_SKJEMAVERSJON:
        return [f"krav_sett.skjemaversjon={krav_sett.get('skjemaversjon')!r},"
                f" krever {KRAV_SETT_SKJEMAVERSJON}"]
    krav = krav_sett.get("krav")
    if not isinstance(krav, list) or not krav:
        return ["krav_sett.krav må være en ikke-tom liste"]

    feil: list[str] = []
    sett: set[str] = set()
    for i, e in enumerate(krav):
        if not isinstance(e, dict):
            feil.append(f"krav[{i}] er ikke et objekt")
            continue
        ukjente = sorted(set(e) - KRAV_ELEMENT_FELTER)
        if ukjente:
            feil.append(f"krav[{i}] har ukjente felter: {ukjente}")
        mangler = sorted(KRAV_ELEMENT_FELTER - set(e))
        if mangler:
            feil.append(f"krav[{i}] mangler {mangler}")
            continue
        if not isinstance(e["vilkaar"], str) or not e["vilkaar"].strip():
            feil.append(f"krav[{i}].vilkaar må være en ikke-tom streng")
        elif e["vilkaar"] in sett:
            # Et duplisert vilkår ville gitt to bevis for samme krav i
            # samme generasjon, som `bevis_vilkaar`-UNIQUE uansett stopper
            # — men da som en databasefeil i stedet for som en klar
            # valideringsmelding.
            feil.append(f"krav[{i}].vilkaar={e['vilkaar']!r} er duplisert")
        else:
            sett.add(e["vilkaar"])
        if not isinstance(e["ressurs_id"], str) or not e["ressurs_id"].strip():
            feil.append(f"krav[{i}].ressurs_id må være en ikke-tom streng")
        if not isinstance(e["innhentbar"], bool):
            feil.append(f"krav[{i}].innhentbar må være boolsk")
    return feil


def krav_sett_hash(krav_sett: dict) -> str:
    """SHA-256 over KANONISK SORTERT krav_sett (v6 pkt. 3).

    Binder oppdraget, kvitteringen og generasjonsraden til NØYAKTIG det
    settet saken ble klassifisert mot. En kvittering for et annet sett er
    ikke en formfeil å rette — den gjelder en annen sak enn den vi ser på.
    """
    import hashlib
    kanonisk = {
        "skjemaversjon": krav_sett.get("skjemaversjon"),
        "krav": sorted(
            ({"vilkaar": e["vilkaar"], "ressurs_id": e["ressurs_id"],
              "innhentbar": bool(e["innhentbar"])}
             for e in krav_sett.get("krav") or []),
            key=lambda e: e["vilkaar"]),
    }
    return hashlib.sha256(json.dumps(
        kanonisk, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()

#: Den YTRE konvoluttens felter (Scope v2 pkt. 3.1). Lukket.
#:
#: Kvitteringen bærer HELE settet i én signert konvolutt. De enkelte
#: attestasjonene er i tillegg individuelt signert — ikke som primær
#: integritet for kvitteringen, men fordi MOTOREN verifiserer hver enkelt
#: i fase 2. To lag med hvert sitt formål: den ytre binder settet til
#: oppdraget og generasjonen, de indre gjør hver attestasjon brukbar som
#: bevis for policyporten.
#: AVVIK FRA SPEKKEN, bevisst: feltet heter `verifikator`, ikke
#: `verifikator_id` (v2-delta pkt. 4). `attestering.verifiser` slår opp
#: nøkkelen på `att["verifikator"]`, og det er den ENE signaturverifiseringen
#: i systemet. Å kalle feltet noe annet her ville krevd en andre kopi av
#: HMAC-koden bare for konvolutten — en signaturkontroll nr. 2 er nøyaktig
#: den typen duplikat som gir divergerende sikkerhet.
VERIFIKASJONSKVITTERING_FELTER = frozenset({
    "protokollversjon", "kvitteringstype", "tenant_id", "oppdrag_id",
    "unntak_id", "fase1_repair_operation_id", "verification_generation",
    "krav_sett_hash", "verifikator", "nokkel_id", "utstedt",
    "attestasjoner", "kanonisering", "signatur",
})
VERIFIKASJONSKVITTERING_PAAKREVDE = frozenset(
    VERIFIKASJONSKVITTERING_FELTER - {"kanonisering", "signatur"})

#: Ett element per vilkår i settet.
VERIFIKASJONSELEMENT_FELTER = frozenset({
    "vilkaar", "status", "permanent", "attestasjon"})

#: v6 pkt. 4 + v7 pkt. 2. `attestert` er det eneste positive utfallet.
#: `ikke_attesterbar` med `permanent: true` betyr prinsipiell
#: u-innhentbarhet; uten `permanent` er den forbigående og teller budsjett.
ELEMENTSTATUSER = ("attestert", "ikke_attesterbar", "negativ")

PROTOKOLLVERSJON = 1


class Konvoluttfeil(ValueError):
    """Konvolutten har ikke den deklarerte formen. Aldri en gjetning."""


def valider_verifikasjonskvittering(kropp: object) -> list[str]:
    """Tom liste == konvolutten har NØYAKTIG den deklarerte formen.

    Kaster aldri. KUN formkontroll: at feltene stemmer med databasen —
    tenant, sak, generasjon, oppdrag, sett-hash — kontrolleres server-side
    i `registrer_verifikasjonsbevis`, mot radene og ikke mot konvolutten.
    Konvolutten er sammenligningsgrunnlag, aldri autoritativ kilde.
    """
    if not isinstance(kropp, dict):
        return ["kvitteringen er ikke et objekt"]
    feil: list[str] = []

    ukjente = sorted(set(kropp) - VERIFIKASJONSKVITTERING_FELTER)
    if ukjente:
        feil.append(f"ukjente felter: {ukjente}")
    mangler = sorted(VERIFIKASJONSKVITTERING_PAAKREVDE - set(kropp))
    if mangler:
        feil.append(f"manglende felter: {mangler}")
    if kropp.get("kvitteringstype") != "verifikasjonskvittering_v1":
        feil.append(f"kvitteringstype={kropp.get('kvitteringstype')!r}"
                    " — kun verifikasjonskvittering_v1 kan bære attestasjoner")
    if kropp.get("protokollversjon") != PROTOKOLLVERSJON:
        feil.append(f"protokollversjon={kropp.get('protokollversjon')!r},"
                    f" krever {PROTOKOLLVERSJON}")
    for felt in ("tenant_id", "verifikator", "nokkel_id", "utstedt",
                 "krav_sett_hash"):
        if not isinstance(kropp.get(felt), str) or not str(kropp.get(felt)).strip():
            feil.append(f"{felt} må være en ikke-tom streng")
    for felt in ("oppdrag_id", "unntak_id", "verification_generation"):
        v = kropp.get(felt)
        if isinstance(v, bool) or not isinstance(v, int):
            feil.append(f"{felt} må være et heltall")

    elementer = kropp.get("attestasjoner")
    if not isinstance(elementer, list) or not elementer:
        return feil + ["attestasjoner må være en ikke-tom liste"]
    sett: set[str] = set()
    for i, e in enumerate(elementer):
        if not isinstance(e, dict):
            feil.append(f"attestasjoner[{i}] er ikke et objekt")
            continue
        ukjente = sorted(set(e) - VERIFIKASJONSELEMENT_FELTER)
        if ukjente:
            feil.append(f"attestasjoner[{i}] har ukjente felter: {ukjente}")
        vilkaar = e.get("vilkaar")
        if not isinstance(vilkaar, str) or not vilkaar.strip():
            feil.append(f"attestasjoner[{i}].vilkaar må være en ikke-tom streng")
        elif vilkaar in sett:
            feil.append(f"attestasjoner[{i}].vilkaar={vilkaar!r} er duplisert")
        else:
            sett.add(vilkaar)
        if e.get("status") not in ELEMENTSTATUSER:
            feil.append(f"attestasjoner[{i}].status={e.get('status')!r}"
                        f" — lovlige: {list(ELEMENTSTATUSER)}")
        if "permanent" in e and not isinstance(e["permanent"], bool):
            feil.append(f"attestasjoner[{i}].permanent må være boolsk")
        if e.get("status") == "attestert":
            att = e.get("attestasjon")
            if not isinstance(att, dict):
                feil.append(f"attestasjoner[{i}]: status=attestert krever en"
                            " attestasjon")
            elif att.get("vilkaar") != vilkaar:
                # Elementet og attestasjonen må gjelde SAMME vilkår. Ellers
                # kunne et element merket `a` båret et bevis for `b`, og
                # settkontrollen ville telt feil krav som dekket.
                feil.append(f"attestasjoner[{i}]: attestasjonen gjelder"
                            f" {att.get('vilkaar')!r}, elementet {vilkaar!r}")
        elif e.get("attestasjon") is not None:
            feil.append(f"attestasjoner[{i}]: kun status=attestert kan bære"
                        " en attestasjon")
    return feil


def resultathash_verifikasjon(konvolutt: dict) -> str:
    """SHA-256 over den KANONISKE konvolutten UTEN den ytre signaturen.

    Scope v2 pkt. 3.2 og Codex-port 7: signer over innhold, hash over
    SAMME innhold, aldri over signaturen. Byttes den ytre signaturen ut
    uten at innholdet endres, er det den samme kvitteringen — og en
    idempotensnøkkel som endret seg med signaturen ville gjort hver
    re-signering til et «motstridende resultat».

    Samme mønster som attesterings-MAC-en fra PR-002.
    """
    from policy_validator import jcs
    uten = {k: v for k, v in konvolutt.items() if k != "signatur"}
    import hashlib
    return hashlib.sha256(jcs.kanoniske_bytes(uten)).hexdigest()


def er_utforelseskvittering(kropp: object) -> bool:
    """En ordinær utførelseskvittering skal ALDRI bære attestasjonsfelt.

    Brukes av utførelsesporten til å avvise en kvittering som prøver å
    smugle bevis inn gjennom feil dør.
    """
    if not isinstance(kropp, dict):
        return False
    forbudte = {"attestert_resultat", "vilkaar", "verification_generation",
                "vilkaarsverdier", "fase1_repair_operation_id"}
    return not (set(kropp) & forbudte)


def mangler_artefaktevidens(oppdragstype: object, kropp: object) -> bool:
    """True == kvitteringen melder SUKSESS for en artefaktproduserende
    type uten å bære artefaktet (Codex P1).

    `er_utforelseskvittering` krever ingen av artefaktfeltene, og
    artefaktgrenen i kvitteringsendepunktet står under `if art_id is not
    None`. En vellykket kvittering uten `artefakt_id` hoppet derfor over
    HELE den grenen — promotering, bindingskontroll, epoch-sjekk og
    skjemarevalidering — og falt rett ned i statusskiftet: oppdraget ble
    `utfort` og unntaket `løst`, uten at det fantes en eneste rapport å
    vise til. En kontroll ingen kan lese er ikke en utført kontroll, og
    her ville ingen engang sett at den manglet.

    Regelen står på TYPEN og ikke som en fast liste i endepunktet: det er
    typen som bestemmer om resultatet leveres som artefakt, og eldre
    typer uten artefakt (`reinnsending`) skal være helt urørt.

    Vurderes bare for suksess: en FEILET kvittering har per definisjon
    ingen rapport, og skal fortsatt kunne meldes uten en. Den ANDRE
    halvdelen av den setningen står i `artefakt_uten_utforelse`."""
    t = (OPPDRAGSTYPER.get(oppdragstype)
         if isinstance(oppdragstype, str) else None)
    if t is None or not t.produserer_artefakt:
        return False
    if not isinstance(kropp, dict) or kropp.get("resultat") != "utfort":
        return False
    return kropp.get("artefakt_id") is None


#: Feltene som BARE hører hjemme i en kvittering som melder `utfort`.
_ARTEFAKTFELTER = ("artefakt_id", "klartekst_sha256")


def artefakt_uten_utforelse(kropp: object) -> bool:
    """True == kvitteringen bærer artefaktfelter uten å melde `utfort`
    (Codex P2).

    Speilbildet av `mangler_artefaktevidens`, og den andre halvdelen av
    dens egen kontrakt: «en FEILET kvittering har per definisjon ingen
    rapport». Bare den ene halvdelen var håndhevet. En autentisert modul
    som sendte `resultat: "feilet"` sammen med en gyldig `artefakt_id` og
    hash slapp forbi begge veier — endepunktets artefaktgren står under
    `if art_id is not None`, ikke under resultatet — så rapporten ble
    PROMOTERT til attestert evidens, og deretter ble oppdraget merket
    feilet.

    Det er en selvmotsigende tilstand å lagre: promotert evidens hvis
    egen signerte kvittering sier at kjøringen ikke ble gjennomført. En
    konsument som leser rapporten ser en fullført kontroll; en som leser
    oppdraget ser en mislykket. Feilretningen skal være avvisning, ikke
    et valg mellom to sannheter.

    Regelen står på RESULTATET og ikke på typen, i motsetning til
    `mangler_artefaktevidens`: typen avgjør om en SUKSESS må bære et
    artefakt, men ingen type har en feilet kjøring med evidens. `None` er
    ikke å bære feltet — en kvittering som eksplisitt skriver
    `artefakt_id: null` melder nettopp at den ikke har noe."""
    if not isinstance(kropp, dict) or kropp.get("resultat") == "utfort":
        return False
    return any(kropp.get(f) is not None for f in _ARTEFAKTFELTER)
