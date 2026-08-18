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
        # PR-014c: `kontroll.`-prefikset er AVGITT til WCAG-kontrollen.
        # Det var en ubrukt reservasjon — hver produsent bygger
        # verifikasjonshandlinger som `verifiser.<vilkår>` (reparasjoner.py),
        # og ingen kodevei eller test har noensinne laget en
        # `kontroll.*`-handling for denne typen. Å beholde det ville brutt
        # disjunkthetsinvarianten under (`kontroll.wcag.` ⊂ `kontroll.`) og
        # gitt feil type ved oppslag. Konsekvensen er fail-closed: en
        # fremtidig `kontroll.*`-handling som IKKE er WCAG-kontrollens
        # matcher ingen type og avvises ved minimering — støyende, aldri
        # feilrutet.
        handlingsprefikser=("verifiser.",),
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
        beskrivelse=("PR-014c: automatisk WCAG-kontroll av et positivt"
                     " autorisert hostname. `ekstern_lesing`-klassen:"
                     " observerbar trafikk ut, ingen ekstern mutasjon;"
                     " målautorisasjon + frekvens håndheves av"
                     " aktiveringsporten, egress/robots av 014b.")),
}


def type_for_handling(handling: str) -> Oppdragstype | None:
    """Oppdragstypen en handling hører til, eller None.

    Prefiksene er disjunkte per konstruksjon — `test_prefikser_er_disjunkte`
    beviser det. Overlappende prefikser ville gjort feltbredden avhengig av
    hvilken rekkefølge dict-en tilfeldigvis har.
    """
    if not isinstance(handling, str):
        return None
    for t in OPPDRAGSTYPER.values():
        if any(handling.startswith(p) for p in t.handlingsprefikser):
            return t
    return None


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
    """
    import hashlib
    # Typenavnet er med i det som hashes, så `None` og strengen `"None"`
    # ikke får samme avtrykk — ellers ville de vært umulige å skille i
    # sporet.
    return hashlib.sha256(
        f"{type(raa).__name__}:{raa}".encode("utf-8")).hexdigest()[:16]


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
    ingen rapport, og skal fortsatt kunne meldes uten en."""
    t = (OPPDRAGSTYPER.get(oppdragstype)
         if isinstance(oppdragstype, str) else None)
    if t is None or not t.produserer_artefakt:
        return False
    if not isinstance(kropp, dict) or kropp.get("resultat") != "utfort":
        return False
    return kropp.get("artefakt_id") is None
