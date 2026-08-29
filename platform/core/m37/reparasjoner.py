"""Reparasjonsbiblioteket — et LUKKET register (v1 §2, v2 §7, v3 §5).

Den bærende invarianten, og den eneste som virkelig betyr noe her:

    EN REPARASJON UTFØRER ALDRI FORRETNINGSSIDEEFFEKTEN SELV,
    OG OMGÅR ALDRI MOTOREN.

Den er ikke skrevet som en regel man skal huske. Den er bygget inn i
typene: en handler får payloaden og returnerer en `Reparasjonsplan` — et
dataobjekt. Den har ingen databasetilkobling, ingen HTTP-klient og ingen
måte å endre noe på. Arbeideren, som eier transaksjonen, er den eneste som
kan handle på planen, og den eneste handlingen som finnes er «be om en ny
policystyrt beslutning gjennom API-et» eller «legg ut et oppdrag».

M-37 har dermed null egne fullmakter. Den kan bare be om nye.

Registeret er lukket: en ukjent (kategori, grunnkode) gir ingen handler, og
det er en FEIL-vei — ikke stillhet. `test_registeret_dekker_kun_kjente_
kategorier` feiler hvis noen legger inn en kategori policy-skjemaet ikke
kjenner.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import oppdragskontrakt as oppdragsskjema
from .taksonomi import (Handlerdeklarasjon, Klassifisering,
                        SIKKERHETSKATEGORIER)

# ---------------------------------------------------------------------------
# Planen en handler kan produsere — og ikke noe mer
# ---------------------------------------------------------------------------

#: De fire eneste utfallene. `ko` betyr «tilbake til køen, prøv igjen
#: senere»; forsøkstelleren i databasen sørger for at det ikke kan pågå i
#: det uendelige.
UTFALL = ("oppdrag", "lost", "manuell", "ko", "verifikasjon")


@dataclass(frozen=True)
class Reparasjonsplan:
    """Hva arbeideren SKAL gjøre. Ikke hva handleren HAR gjort.

    Skillet er hele poenget. Et returobjekt kan ikke ha utført noe.
    """
    utfall: str
    grunn: str
    maalhandling: str | None = None
    oppdragstype: str | None = None
    #: Det som inngår i `repair_operation_id`. Må være kanoniserbart og
    #: stabilt: to like reparasjoner av samme sak skal gi samme id, ellers
    #: er idempotensen borte og hvert forsøk blir en ny forretningshandling.
    reparasjonsinput: dict = field(default_factory=dict)

    #: Kun for `utfall='verifikasjon'` (form A): HELE settet fase 1 skal
    #: dekke, hashen som binder det, og den deterministisk valgte
    #: verifikatoren med autoritetsversjonen valget ble gjort mot.
    vilkaar: str | None = None
    krav_sett: dict | None = None
    krav_sett_hash: str | None = None
    valgt_verifikator: str | None = None
    autoritetsversjon: str | None = None

    def __post_init__(self) -> None:
        if self.utfall not in UTFALL:
            raise ValueError(f"ukjent utfall {self.utfall!r}")
        if self.utfall == "oppdrag" and not (self.maalhandling
                                             and self.oppdragstype):
            raise ValueError("oppdrag krever både målhandling og oppdragstype")
        if self.utfall == "verifikasjon" and not (
                self.krav_sett and self.krav_sett_hash
                and self.valgt_verifikator and self.autoritetsversjon):
            raise ValueError(
                "verifikasjon krever krav_sett, hash, valgt verifikator og"
                " autoritetsversjon — en fase 1 uten dem ville bedt om å få"
                " verifisert ingenting, av ingen, mot ingenting")


def input_hash(reparasjonsinput: dict) -> str:
    """Kanonisk SHA-256 over reparasjonens input.

    Samme regler som `api.policyregister.innholds_hash` og
    `api.kjerne.input_hash`: sorterte nøkler, ingen mellomrom, UTF-8. At de
    tre stedene bruker SAMME regler er ikke tilfeldig — hashen krysser
    prosessgrenser, og en avvikende serialisering ett sted ville gitt to
    ulike identiteter for samme reparasjon.
    """
    return hashlib.sha256(json.dumps(
        reparasjonsinput, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def repair_operation_id(tenant: str, unntak_id: int, handler_id_med_versjon: str,
                        maalhandling: str, inp_hash: str) -> str:
    """SHA-256(tenant ‖ unntak_id ‖ handler@versjon ‖ målhandling ‖ input_hash).

    `forsok` og `claim_id` inngår ALDRI (v2-delta pkt. 4). De er
    transportdetaljer: en idempotensnøkkel som endrer seg per forsøk er
    ingen idempotensnøkkel, og hvert retry ville blitt en ny
    forretningshandling i stedet for et nytt forsøk på den samme.

    Separatoren er \\x1f (unit separator) fordi den ikke kan forekomme i
    noen av delene. Med bindestrek ville («a-b», «c») og («a», «b-c») gitt
    samme id.
    """
    raa = "\x1f".join((tenant, str(unntak_id), handler_id_med_versjon,
                       maalhandling, inp_hash))
    return hashlib.sha256(raa.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# De tre handlerne — v1-omfanget er UTTØMMENDE
# ---------------------------------------------------------------------------

R1 = Handlerdeklarasjon(
    handler_id="r1_reinnsending",
    versjon="1",
    kategorier=frozenset({"manglende_data"}),
    grunnkoder=frozenset({
        "vilkaar_mangler_attestasjon", "attestasjon_mangler",
        "manglende_felt", "manglende_dataklasse_kilde",
    }),
    sideeffektfri=False,
    krever_outbox=True,
    tillatte_malhandlinger=("purring.", "faktura.", "melding."),
    timeout_s=60, lease_s=180)

R2 = Handlerdeklarasjon(
    handler_id="r2_lokal_kontroll",
    versjon="1",
    kategorier=frozenset({"teknisk_feil"}),
    grunnkoder=frozenset({
        "motor_exception", "logging_feilet", "payload_uleselig",
        "skjemafeil_forbigaaende",
    }),
    # v3-delta pkt. 5: R2 er innskrenket til KONTROLLER AV DATA M-37
    # ALLEREDE HAR. Alle oppslag mot autoritative kilder er
    # verifikasjonsoppdrag gjennom outbox-protokollen — utført av en modul
    # med egne fullmakter. Uten den innskrenkningen ville
    # null-fullmaktsprinsippet hatt et unntak, og et prinsipp med ett
    # unntak er en retningslinje.
    sideeffektfri=True,
    krever_outbox=False,
    timeout_s=15, lease_s=60)

R3 = Handlerdeklarasjon(
    handler_id="r3_policykrevende",
    versjon="1",
    kategorier=frozenset({"over_grense", "regelkonflikt", "ugyldig_data",
                          "ukjent"}),
    grunnkoder=frozenset({
        "belopsgrense_overskredet", "frekvensgrense_naadd",
        "frekvensgrense_naadd_ved_reservasjon", "utenfor_tidsvindu",
        "rolle_mangler_fullmakt", "dataklasse_ikke_tillatt",
        "regelkonflikt", "ugyldig_data", "ukjent",
        "policy_belopsgrense_ugyldig", "policy_tidssone_ugyldig",
        "frekvens_uten_tellerlager",
    }),
    sideeffektfri=True,
    krever_outbox=False,
    # R3 REPARERER ingenting. Beslutningen var riktig — en handling over
    # beløpsgrensen skal stoppes, og det er ikke en feil som kan fikses av
    # en maskin. Den eneste jobben er å KLASSIFISERE saken som manuell ved
    # FØRSTE claim, slik at den ikke brenner tre forsøk på å oppdage det
    # samme tre ganger.
    kun_klassifisering=True,
    timeout_s=5, lease_s=30)

#: Det lukkede registeret. Rekkefølgen er uten betydning — oppslaget skjer
#: på (kategori, grunnkode), og `test_ingen_to_handlere_deler_kategori`
#: beviser at ingen sak kan treffe to.
REGISTER: tuple[Handlerdeklarasjon, ...] = (R1, R2, R3)


def valider_register() -> list[str]:
    """Tom liste == registeret er konsistent. Kjøres i CI.

    Tre kontroller, og alle tre finnes fordi en av dem alene ville sluppet
    gjennom noe: at hver handler er gyldig for seg, at ingen to handlere
    kan ta samme sak, og at registeret dekker HELE taksonomien. Den siste
    er den viktigste — en kategori uten handler er en sak som blir liggende
    uten at noen har bestemt at den skal det.
    """
    feil: list[str] = []
    for h in REGISTER:
        feil += h.valider()

    sett: dict[tuple[str, str], str] = {}
    for h in REGISTER:
        for kat in sorted(h.kategorier):
            for gk in sorted(h.grunnkoder):
                nokkel = (kat, gk)
                if nokkel in sett:
                    feil.append(f"(kategori={kat}, grunnkode={gk}) tas av både"
                                f" {sett[nokkel]} og {h.handler_id}")
                sett[nokkel] = h.handler_id

    dekket = frozenset().union(*(h.kategorier for h in REGISTER))
    from .taksonomi import M37_TAKSONOMI_V1
    udekket = sorted(M37_TAKSONOMI_V1 - dekket - SIKKERHETSKATEGORIER)
    if udekket:
        feil.append(f"kategorier i taksonomien uten handler: {udekket}"
                    " — en sak i den kategorien ville blitt liggende uten at"
                    " noen har bestemt at den skal det")
    return feil


# ---------------------------------------------------------------------------
# Klassifisering — den lukkede routingtabellen
# ---------------------------------------------------------------------------

def klassifiser(kategori: str, grunnkode: str | None,
                policykategorier: frozenset[str] | set[str]) -> Klassifisering:
    """Hvilken handler tar saken — eller hvorfor ingen gjør det.

    Rekkefølgen på kontrollene er bindende. Sikkerhetskategoriene sjekkes
    FØRST, av samme grunn som `feil.sakstype_for` gjør det: en
    manipulasjonsmistanke er en sikkerhetssak selv om policyen tilfeldigvis
    lister kategorien som en ordinær unntakskategori.
    """
    if kategori in SIKKERHETSKATEGORIER:
        return Klassifisering("sikkerhet", "sikkerhetskategori",
                              kategori=kategori, grunnkode=grunnkode)
    if grunnkode is None:
        # Fail-closed. En sak uten grunnkode kan ikke oppfylle predikatet,
        # og å gjette en ville vært å finne på et saksgrunnlag.
        return Klassifisering("manuell", "grunnkode_mangler",
                              kategori=kategori)
    if kategori not in policykategorier:
        # Kundens policy lister ikke kategorien. Da har ingen bestemt at
        # den skal behandles automatisk hos DENNE kunden.
        return Klassifisering("manuell", "kategori_utenfor_policy",
                              kategori=kategori, grunnkode=grunnkode,
                              detalj={"policykategorier":
                                      sorted(policykategorier)})
    for h in REGISTER:
        if h.behandler(kategori, grunnkode, policykategorier):
            if h.kun_klassifisering:
                return Klassifisering("manuell", "policykrevende", handler=h,
                                      kategori=kategori, grunnkode=grunnkode)
            return Klassifisering("behandle", "handler_funnet", handler=h,
                                  kategori=kategori, grunnkode=grunnkode)
    return Klassifisering("manuell", "ingen_handler",
                          kategori=kategori, grunnkode=grunnkode)


# ---------------------------------------------------------------------------
# Handlerne. Private — kalles kun av `planlegg` i denne modulen.
# ---------------------------------------------------------------------------

def _r1_reinnsending(payload: dict, kl: Klassifisering) -> Reparasjonsplan:
    """Har det manglende grunnlaget kommet på plass?

    R1 sjekker IKKE selv om attestasjonen finnes — det ville vært et
    oppslag mot en autoritativ kilde, og de går gjennom outboxen (v3-delta
    pkt. 5). Den bygger et oppdrag som ber en eiermodul om å utføre
    handlingen på nytt, og handlingen går da gjennom HELE API-veien og
    policyporten en gang til.

    Det er derfor R1 ikke kan bli en bakvei rundt motoren: det den ber om
    er en ny beslutning, ikke en ny utførelse.
    """
    handling = payload.get("handling")
    if not isinstance(handling, str) or not handling:
        return Reparasjonsplan("manuell", "handling_mangler_i_payload")
    t = oppdragsskjema.type_for_handling(handling)
    if t is None:
        return Reparasjonsplan("manuell", "ingen_oppdragstype_for_handling")
    if not any(handling.startswith(p) for p in R1.tillatte_malhandlinger):
        return Reparasjonsplan("manuell", "handling_utenfor_deklarasjonen")
    minimert = oppdragsskjema.minimer(t.navn, payload)
    mangler = oppdragsskjema.mangler_paakrevde(t.navn, minimert)
    if mangler:
        return Reparasjonsplan("manuell", f"oppdrag_ufullstendig:{mangler}")
    # ... og feltene må ha LOVLIGE verdier, ikke bare finnes (Codex P1).
    # Et oppdrag med `omfang: "alt"` eller `maks_sider: 0` er dødfødt: det
    # kan aldri gi en rapport som validerer, men det kan bli claimet og
    # kjørt — altså ekstern trafikk mot kundens nettsted for en
    # bestilling ingen kunne oppfylt. Da skal oppdraget ikke opprettes.
    ugyldige = oppdragsskjema.bryter_feltkontrakten(t.navn, minimert)
    if ugyldige:
        return Reparasjonsplan("manuell", f"oppdrag_ugyldig:{ugyldige}")
    return Reparasjonsplan("oppdrag", "reinnsending_planlagt",
                           maalhandling=handling, oppdragstype=t.navn,
                           reparasjonsinput=minimert)


def _r2_lokal_kontroll(payload: dict, kl: Klassifisering) -> Reparasjonsplan:
    """Rene lokale kontroller av data M-37 allerede har.

    Ingen nettverk, ingen autoritative kilder, ingen sideeffekter. At
    payloaden lot seg dekryptere og tolke som et objekt med de feltene
    saksgrunnlaget krever, ER kontrollen — den tekniske feilen som skapte
    saken var i så fall forbigående.

    Går det ikke, tilbake til køen. Forsøkstelleren i databasen avgjør når
    det er nok; denne funksjonen teller ingenting selv, og kan derfor ikke
    komme i utakt med den.
    """
    if not isinstance(payload, dict) or not payload:
        return Reparasjonsplan("manuell", "payload_uleselig")
    if not isinstance(payload.get("handling"), str):
        return Reparasjonsplan("manuell", "payload_mangler_handling")
    if not isinstance(payload.get("kategori"), (str, type(None))):
        return Reparasjonsplan("ko", "payload_kategori_feiltype")
    return Reparasjonsplan("lost", "lokal_kontroll_bestatt",
                           reparasjonsinput={"kontroll": "lokal",
                                             "handling": payload["handling"]})


def _r3_policykrevende(payload: dict, kl: Klassifisering) -> Reparasjonsplan:
    """Beslutningen var riktig. Det finnes ingenting å reparere."""
    return Reparasjonsplan("manuell", "policykrevende_ingen_reparasjon")


_HANDLERE = {
    R1.handler_id: _r1_reinnsending,
    R2.handler_id: _r2_lokal_kontroll,
    R3.handler_id: _r3_policykrevende,
}


def planlegg(kl: Klassifisering, payload: dict) -> Reparasjonsplan:
    """Kjør handleren klassifiseringen pekte ut. Eneste offentlige vei inn.

    Handlerne er private, og det er en Codex-port: kan de kalles utenfra,
    kan noen kjøre en reparasjon uten å ha gått gjennom klassifiseringen —
    og da er hele predikatet fra v4-delta pkt. 6 hoppet over.
    """
    if kl.utfall != "behandle" or kl.handler is None:
        return Reparasjonsplan("manuell", f"ikke_behandlebar:{kl.grunn}")
    handler = _HANDLERE.get(kl.handler.handler_id)
    if handler is None:
        return Reparasjonsplan("manuell", "handler_uten_implementasjon")
    return handler(payload, kl)


# ---------------------------------------------------------------------------
# Kompenserende reversering (hovedspesifikasjon §3)
# ---------------------------------------------------------------------------
#
# Codex P1 runde 1: første leveranse hadde bare fail-closed-veien (alt gikk
# til `manuell`) og flagget den positive veien som restscope. Det er ikke
# staging-evidens eller senere operasjonalisering — det er
# behandlingsmotorens forretningslivssyklus, og den hører til her.
#
# Prinsippet er det samme som for R1, og det er hele poenget: KOMPENSASJONEN
# ER SELV EN POLICYSTYRT HANDLING. M-37 utfører den aldri; den ber om en ny
# beslutning gjennom `kjerne.behandle()`, og policyporten avgjør på nytt.
# Uten det ville «reverser» vært en fullmakt M-37 hadde og ingen hadde gitt
# den.

#: Idempotensnøkkelens KANONISKE form (v2-delta pkt. 4). Den hashes før
#: bruk fordi databasen krever `^[0-9a-f]{64}$` på repair_operation_id, men
#: det er DENNE strengen som definerer identiteten — og den inneholder
#: verken `forsok` eller `claim_id`.
KOMPENSASJON_NOKKEL = "compensation:{unntak_id}:{handling}:{loggpost_id}"


def kompensasjonsnokkel(unntak_id: int, handling: str,
                        original_loggpost_id: int) -> str:
    return KOMPENSASJON_NOKKEL.format(unntak_id=unntak_id, handling=handling,
                                      loggpost_id=original_loggpost_id)


def _reversering(policy: dict, handling_id: str) -> dict:
    for h in policy.get("handlinger") or []:
        if isinstance(h, dict) and h.get("id") == handling_id:
            return h.get("reversering") or {}
    return {}


def planlegg_kompensasjon(policy: dict, *, opprinnelig_handling: str,
                          unntak_id: int, loggpost_id: int,
                          sak_ts, naa, payload: dict) -> Reparasjonsplan:
    """Den positive kompensasjonsveien, med sine tre negative porter.

    Portene i rekkefølge, og hver av dem er fail-closed:

      1. IRREVERSIBEL KOMPENSERES ALDRI AUTOMATISK. Ikke «sjelden», ikke
         «med ekstra kontroll» — aldri. En handling policyen har erklært
         irreversibel er per definisjon en handling ingen maskin kan gjøre
         om.
      2. UDEFINERT KOMPENSASJONSHANDLING => `manuell`, aldri gjetting.
         Står det ikke i kundens policy hva som reverserer handlingen, har
         ingen bestemt det — og da er det ikke vår oppgave å finne på noe.
      3. UTLØPT `frist_sekunder` => `manuell`. En kompensasjon som kommer
         for sent kan gjøre mer skade enn den reparerer.

    Returnerer en PLAN. Som alle handlere kan denne funksjonen ikke utføre
    noe: den har ingen tilkobling og ingen klient. Arbeideren sender planen
    gjennom API-et som en ny beslutning, og policyporten evaluerer
    kompensasjonshandlingen som enhver annen handling.
    """
    rev = _reversering(policy, opprinnelig_handling)
    type_ = rev.get("type")

    if type_ == "irreversibel":
        return Reparasjonsplan("manuell", "irreversibel_kompenseres_aldri")
    if type_ != "kompenserende":
        # `direkte` betyr at handlingen reverseres av seg selv; da finnes
        # det ingen kompensasjon å be om. Ukjent type er en policyfeil.
        return Reparasjonsplan("manuell",
                               f"reversering_ikke_kompenserende:{type_}")

    maalhandling = rev.get("handling")
    if not isinstance(maalhandling, str) or not maalhandling:
        return Reparasjonsplan("manuell", "kompensasjonshandling_udefinert")
    # Handlingen må FINNES i policyen. En `reversering.handling` som peker
    # på noe som ikke er definert, ville gått til motoren og blitt avvist
    # der — men da med en loggpost som ser ut som et policybrudd i stedet
    # for som en policyfeil.
    if _handling_finnes(policy, maalhandling) is False:
        return Reparasjonsplan("manuell", "kompensasjonshandling_ukjent")

    frist = rev.get("frist_sekunder")
    if isinstance(frist, int) and not isinstance(frist, bool) and frist >= 0:
        if sak_ts is not None and (naa - sak_ts).total_seconds() > frist:
            return Reparasjonsplan("manuell", "kompensasjonsfrist_utlopt")

    t = oppdragsskjema.type_for_handling(maalhandling)
    if t is None:
        return Reparasjonsplan("manuell", "ingen_oppdragstype_for_kompensasjon")
    minimert = oppdragsskjema.minimer(t.navn, {**payload,
                                               "handling": maalhandling})
    mangler = oppdragsskjema.mangler_paakrevde(t.navn, minimert)
    if mangler:
        return Reparasjonsplan("manuell", f"kompensasjon_ufullstendig:{mangler}")
    ugyldige = oppdragsskjema.bryter_feltkontrakten(t.navn, minimert)
    if ugyldige:
        return Reparasjonsplan("manuell", f"kompensasjon_ugyldig:{ugyldige}")

    return Reparasjonsplan(
        "oppdrag", "kompensasjon_planlagt", maalhandling=maalhandling,
        oppdragstype=t.navn,
        reparasjonsinput={**minimert,
                          "kompenserer": opprinnelig_handling,
                          "kompensasjonsnokkel": kompensasjonsnokkel(
                              unntak_id, maalhandling, loggpost_id)})


def _handling_finnes(policy: dict, handling_id: str) -> bool:
    return any(isinstance(h, dict) and h.get("id") == handling_id
               for h in policy.get("handlinger") or [])


def krever_kompensasjon(payload: dict) -> bool:
    """Bærer saken et spor av en DELVIS UTFØRT handling?

    Feltet settes av connectoren/motoren når en handling rakk å få effekt
    før den ble stoppet. Det er en eksplisitt markør og ikke en utledning:
    å gjette at «denne saken ser ut som den trenger reversering» ville vært
    å starte forretningshandlinger på en mistanke.
    """
    return payload.get("delvis_utfort") is True


# ---------------------------------------------------------------------------
# PR-007: tofaseruting — tre eksplisitte ruter, fail-closed (v2-delta)
# ---------------------------------------------------------------------------
#
# Klassifisereren ruter på GRUNN-KODE, ikke på kategori. `manglende_data`
# dekker to helt ulike situasjoner, og bare den ene kan repareres:
#
#   * en ATTESTASJON mangler  -> en verifikator kan skaffe den. Tofase.
#   * en VERDI mangler        -> den fantes i originalhendelsen, som er
#                                minimert bort. Kan ikke rekonstrueres.
#                                Manuell.
#
# Skillet er hele grunnen til at PR-007 finnes: modell (b) — bygg den nye
# hendelsen av minimert payload + verifisert attestasjon — holder KUN for
# den første klassen. For den andre ville vi bygget en hendelse som fortsatt
# mangler det den manglet, og fått UNNTAK igjen. Det var nøyaktig feilen
# som ble målt på en levende trekjede før PR-007.

#: Grunn-koder der det manglende er en ATTESTASJON en verifikator kan
#: skaffe autoritativt. Disse — og kun disse — går tofasevegen.
ATTESTASJONSMANGEL = frozenset({
    "attestasjon_mangler",
    "vilkaar_mangler_attestasjon",
    "attestasjon_utlopt",
})

#: Grunn-koder der det manglende er en FORRETNINGSVERDI fra
#: originalhendelsen. Ingen verifikator kan attestere et beløp som aldri
#: ble lagret — dette er `manuell`, og det er ikke en begrensning vi kan
#: kode oss ut av uten å utvide datalagringen (egen spesifikasjon).
VERDIMANGEL = frozenset({
    "manglende_felt",
    "manglende_dataklasse_kilde",
})


@dataclass(frozen=True)
class Faserute:
    """Hvilken vei en R1-sak skal ta. `vilkaar` kun for tofase."""
    rute: str            # 'tofase' | 'manuell'
    grunn: str
    vilkaar: str | None = None


def rut_r1(grunnkode: str | None, payload: dict) -> Faserute:
    """De tre rutene. Alt som ikke er BEVIST attestasjonsmangel → manuell.

    Den tredje ruten — «ukjent eller sammensatt årsak» — er den viktigste,
    og den er fail-closed med vilje: å gjette at en sak er reparerbar
    starter en verifikasjonsrunde på noe ingen har bekreftet at kan
    verifiseres.
    """
    if grunnkode in VERDIMANGEL:
        return Faserute("manuell", f"verdimangel:{grunnkode}")
    if grunnkode not in ATTESTASJONSMANGEL:
        return Faserute("manuell", f"ukjent_eller_sammensatt:{grunnkode}")

    vilkaar = payload.get("manglende_vilkaar")
    if not isinstance(vilkaar, str) or not vilkaar.strip():
        # Koden sier «attestasjon mangler», men ikke HVILKEN. Uten vilkåret
        # måtte fase 1 gjettet, og en verifikator som attesterer noe annet
        # enn det saken manglet, har ikke verifisert saken.
        return Faserute("manuell", "vilkaar_ukjent")
    return Faserute("tofase", "attestasjonsmangel", vilkaar=vilkaar)


def planlegg_verifikasjon(kl, payload: dict, policy: dict,
                          policy_id: str = "") -> Reparasjonsplan:
    """Fase 1 for HELE settet (form A).

    Rekkefølgen er fail-closed hele veien:

      1. Er dette i det hele tatt en attestasjonsmangel? Ellers manuell.
      2. Bygg det komplette settet fra policyen og frys det.
      3. **Ett eneste ikke-innhentbart vilkår ⇒ manuell UMIDDELBART**, uten
         fase 1. En sak der noe prinsipielt ikke kan skaffes, er ikke
         reparerbar — og skal ikke bruke en verifikasjonsrunde på å
         oppdage det (v5 pkt. 1).
      4. Skjæringsmengden må gi minst én verifikator som dekker HELE
         settet. Tom skjæring ⇒ manuell; fler-verifikator er utenfor
         PR-007.

    Returnerer en PLAN. Fase 1 har null forretningsfullmakter: den ber en
    registrert verifikator KONTROLLERE og ATTESTERE, aldri utføre.
    """
    from oppdragskontrakt import krav_sett_hash as _hash

    rute = rut_r1(kl.grunnkode, payload)
    if rute.rute != "tofase":
        return Reparasjonsplan("manuell", rute.grunn)

    handling = payload.get("handling")
    ressurs = payload.get("ressurs_id")
    if not isinstance(handling, str) or not handling:
        return Reparasjonsplan("manuell", "handling_mangler_i_payload")
    if not isinstance(ressurs, str) or not ressurs:
        # Verifikasjonen er RESSURSBUNDET. Uten ressursen ville
        # attestasjonen gjeldt «noe hos denne kunden», og en attestasjon
        # uten ressursbinding kan gjenbrukes på en annen sak.
        return Reparasjonsplan("manuell", "ressurs_id_mangler")

    # Alle vilkår som mangler en ATTESTASJON er innhentbare. I dag kjenner
    # vi ett sikkert (det blokkerende); resten av settet antas innhentbart
    # med mindre payloaden sier noe annet. Er noe ikke-innhentbart, stopper
    # pkt. 3 saken før fase 1.
    krav_sett = bygg_krav_sett(policy, handling, ressurs,
                               innhentbare_vilkaar=_innhentbare(payload,
                                                                policy,
                                                                handling))
    if not krav_sett["krav"]:
        return Reparasjonsplan("manuell", "handlingen_har_ingen_vilkaar")
    ikke_innhentbare = [e["vilkaar"] for e in krav_sett["krav"]
                        if not e["innhentbar"]]
    if ikke_innhentbare:
        return Reparasjonsplan(
            "manuell", f"ikke_innhentbare_vilkaar:{sorted(ikke_innhentbare)}")

    innhentbare = [e["vilkaar"] for e in krav_sett["krav"]]
    valg = velg_verifikator(policy, innhentbare)
    if valg.verifikator is None:
        return Reparasjonsplan("manuell", valg.grunn)

    h = _hash(krav_sett)
    return Reparasjonsplan(
        "verifikasjon", "verifikasjon_bestilt", vilkaar=None,
        krav_sett=krav_sett, krav_sett_hash=h,
        valgt_verifikator=valg.verifikator,
        autoritetsversjon=autoritetsregister_hash(policy),
        maalhandling=handling, oppdragstype="verifikasjon",
        reparasjonsinput={
            "handling": f"verifiser.{krav_sett['krav'][0]['vilkaar']}",
            "ressurs_id": ressurs,
            "vilkaar_sett": innhentbare,
            "kategori": kl.kategori,
            "maalhandling": handling,
            "policy_id": policy_id,
            "krav_sett_hash": h,
        })


def _innhentbare(payload: dict, policy: dict, handling: str) -> set[str]:
    """Hvilke av handlingens vilkår som kan skaffes autoritativt.

    Et vilkår er innhentbart når det som mangler er en ATTESTASJON. Er det
    en forretningsverdi som manglet, er den minimert bort og ingen
    verifikator kan attestere den.

    I v1 er alle handlingens vilkår innhentbare med mindre payloaden
    eksplisitt merker noe annet — grunnkoden har alt bevist at det er en
    attestasjonsmangel (`rut_r1`), og verdi-mangler er rutet til `manuell`
    før vi kommer hit.
    """
    ikke = payload.get("ikke_innhentbare_vilkaar")
    ikke_sett = set(ikke) if isinstance(ikke, list) else set()
    alle = set()
    for h in policy.get("handlinger") or []:
        if isinstance(h, dict) and h.get("id") == handling:
            for vk in h.get("vilkaar") or []:
                if isinstance(vk, dict) and isinstance(vk.get("navn"), str):
                    alle.add(vk["navn"])
            break
    return alle - ikke_sett


def fase1_id(tenant: str, unntak_id: int, krav_sett_hash: str,
             handler_id_med_versjon: str, generation: int) -> str:
    """SHA-256(tenant ‖ unntak_id ‖ 'verifikasjon' ‖ vilkaar ‖ handler ‖ gen).

    Generasjonen inngår (v2-delta pkt. 3): retry av SAMME generasjon gir
    samme id og er idempotent, mens en NY generasjon er en ny bestilling.
    `forsok` og `claim_id` inngår aldri — uendret prinsipp.
    """
    raa = "\x1f".join((tenant, str(unntak_id), "verifikasjon",
                       krav_sett_hash, handler_id_med_versjon,
                       str(generation)))
    return hashlib.sha256(raa.encode("utf-8")).hexdigest()


def fase2_id(tenant: str, unntak_id: int, maalhandling: str, generation: int,
             aktiv_policy_hash: str, autoritetsregister_hash_: str,
             krav_sett_hash_: str) -> str:
    """Bundet til TRE hasher, ikke bare generasjonen (GO-vilkår V1).

    ```
    SHA-256(tenant ‖ unntak_id ‖ 'beslutning' ‖ målhandling ‖ generation
            ‖ aktiv_policy_hash ‖ aktiv_autoritetsregister_hash
            ‖ krav_sett_hash)
    ```

    Autoritetsregisterets hash er den som gjør arbeidet: trekkes en
    verifikators fullmakt tilbake, endres den hashen — og dermed
    `fase2_id` — SELV om policyinnholdet ellers er uendret. Uten det leddet
    kunne en gammel fase-2-identitet gjenbrukes med en fullmakt som ikke
    lenger finnes.
    """
    raa = "\x1f".join((tenant, str(unntak_id), "beslutning", maalhandling,
                       str(generation), aktiv_policy_hash,
                       autoritetsregister_hash_, krav_sett_hash_))
    return hashlib.sha256(raa.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# PR-007 form A: vilkårssettet, skjæringsmengden og verifikatorvalget
# ---------------------------------------------------------------------------

def bygg_krav_sett(policy: dict, handling_id: str, ressurs_id: str,
                   innhentbare_vilkaar: set[str] | None = None) -> dict:
    """Det KOMPLETTE settet påkrevde vilkår for handlingen (v5 pkt. 1).

    Settet bestemmes ÉN gang ved første klassifisering og fryses på saken.
    Det slås aldri opp på nytt — endrer policyen vilkårene etterpå,
    påvirker det aldri en pågående sak (v6 pkt. 1).

    `innhentbare_vilkaar` er de som mangler en ATTESTASJON og derfor kan
    skaffes autoritativt. Alt annet er ikke-innhentbart: en manglende
    forretningsverdi kan ingen verifikator attestere, fordi den aldri ble
    lagret.
    """
    from oppdragskontrakt import KRAV_SETT_SKJEMAVERSJON
    innhentbare = innhentbare_vilkaar if innhentbare_vilkaar is not None else set()
    krav = []
    for h in policy.get("handlinger") or []:
        if isinstance(h, dict) and h.get("id") == handling_id:
            for vk in h.get("vilkaar") or []:
                navn = vk.get("navn") if isinstance(vk, dict) else None
                if isinstance(navn, str) and navn:
                    krav.append({"vilkaar": navn, "ressurs_id": ressurs_id,
                                 "innhentbar": navn in innhentbare})
            break
    return {"skjemaversjon": KRAV_SETT_SKJEMAVERSJON,
            "krav": sorted(krav, key=lambda e: e["vilkaar"])}


def betrodde_for(policy: dict, vilkaar: str) -> frozenset[str]:
    """Verifikatorene policyen har betrodd for ETT vilkår.

    Relasjonen finnes fra PR-002 (`verifikatorer.<id>.betrodd_for`) — den
    er ikke noe PR-007 innfører.
    """
    ut = set()
    for vid, v in (policy.get("verifikatorer") or {}).items():
        if isinstance(v, dict) and vilkaar in (v.get("betrodd_for") or []):
            ut.add(vid)
    return frozenset(ut)


def autoritetsregister_hash(policy: dict) -> str:
    """Hash over `betrodd_for`-relasjonen slik den er NÅ.

    GO-vilkår V1: `fase2_id` binder til denne, ikke bare til policyinnholdet.
    En tilbaketrukket verifikatorfullmakt endrer hashen — og dermed
    `fase2_id` — SELV om resten av policyen er uendret. Uten den bindingen
    kunne en gammel fase2-identitet gjenbrukes med en fullmakt som ikke
    lenger finnes.
    """
    reg = {vid: sorted((v.get("betrodd_for") or []))
           for vid, v in sorted((policy.get("verifikatorer") or {}).items())
           if isinstance(v, dict)}
    return hashlib.sha256(json.dumps(
        reg, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Verifikatorvalg:
    """Utfallet av skjæringsmengde-regelen. `verifikator` None => manuell."""
    verifikator: str | None
    grunn: str
    kandidater: tuple[str, ...] = ()


def velg_verifikator(policy: dict, innhentbare: list[str]) -> Verifikatorvalg:
    """SKJÆRINGSMENGDE, ikke union (Scope v2 pkt. 1).

    Den første formelen — «flere enn én distinkt verifikator ⇒ manuell» —
    var feil: vilkår A betrodd {V1,V2} og vilkår B betrodd {V1} gir union
    {V1,V2}, men V1 dekker HELE settet alene og saken er innenfor scope.

    ```
    kandidater = ⋂ betrodd_for(vilkår)   for alle innhentbare vilkår
    ```

    Tom skjæring betyr at ingen ENKELT verifikator dekker settet. Da går
    saken `manuell` uten fase 1 — fler-verifikator-akkumulering er
    eksplisitt utenfor PR-007 (v7 pkt. 4 og v8 er ute av scope).

    Valget er DETERMINISTISK og totalt (GO-vilkår V4): eksplisitt
    `verifikator_prioritet` hvis satt, ellers laveste `verifikator_id` som
    sekundærnøkkel. Aldri avhengig av databaseorden — samme sak gir samme
    verifikator hver eneste kjøring.
    """
    if not innhentbare:
        return Verifikatorvalg(None, "ingen_innhentbare_vilkaar")
    kandidater: frozenset[str] | None = None
    for vilkaar in innhentbare:
        b = betrodde_for(policy, vilkaar)
        if not b:
            return Verifikatorvalg(None, f"ingen_betrodd_for:{vilkaar}")
        kandidater = b if kandidater is None else (kandidater & b)
    if not kandidater:
        return Verifikatorvalg(None, "krever_flere_verifikatorer")

    prioritet = policy.get("verifikator_prioritet") or {}
    def nokkel(vid: str):
        p = prioritet.get(vid)
        # Ugyldig prioritering skal ikke gi en tilfeldig vinner: den
        # sorteres bakerst, og `verifikator_id` avgjør — fortsatt totalt.
        return (p if isinstance(p, int) and not isinstance(p, bool) else 10**9,
                vid)
    valgt = sorted(kandidater, key=nokkel)[0]
    return Verifikatorvalg(valgt, "valgt", tuple(sorted(kandidater)))
