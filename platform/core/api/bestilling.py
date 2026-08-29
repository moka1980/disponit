"""Bestillingsveien (014c v4 §6): kundeflaten bestiller en kontroll, og
veien videre er `kjerne.behandle()` — aldri en snarvei.

Endepunktet er EN NY PRODUSENT inn i beslutningsveien: bestillingen blir
en policy-evaluering (frekvensgrensen håndheves KUN i motorens betrodde
teller — endepunktet teller aldri selv), TILLAT blir et
beslutningsoppdrag i outboxen (038), STOPP blir en strukturert kode til
flaten, BRUDD går policyens vei til unntakskøen.

LUKKET KROPP, og klienten sender aldri en URL: serveren komponerer
`mal_url` av `hostname` + normalisert `sti`, så klassen «URL med query,
fragment, credentials eller port» ikke kan uttrykkes. Hostnamet må være
`verifisert` for tenanten VED OPPRETTELSE — det er ikke policyens ansvar.

Idempotensen binder HELE den normaliserte intensjonen (hash over den
serverkomponerte formen): samme nøkkel + samme hash → samme resultat
(også STOPP/BRUDD — et gjenspill etter timeout brenner aldri kvote);
samme nøkkel + annen hash → 409 uten at noen beslutning tas. Kjernens
egen idempotensnøkkel avledes deterministisk av bestillingsnøkkelen, så
en retry som mistet svaret replayer BESLUTNINGEN i kjernen (ingen ny
frekvensreservasjon) før oppdraget sikres idempotent
(`oppdrag_en_per_beslutning`, 008).
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg
from starlette.requests import Request
from starlette.responses import Response

#: Lukket kropp — nøyaktig disse feltene, aldri en URL.
SKJEMAFELT = frozenset({"bestillingstype", "hostname", "sti", "kravsett",
                        "omfang", "maks_sider"})
#: Den normaliserte intensjonen hashen bygges fra. MEKANISK avledet av
#: skjemaet: hostname+sti kollapser til den serverkomponerte `mal_url`,
#: resten følger med som de er (normalisert). Den statiske porten 21f
#: krysser de to mengdene, så et fremtidig semantisk felt ikke kan falle
#: utenfor hashen i stillhet.
INTENSJONSFELT = ("tenant", "bestillingstype", "mal_url", "kravsett",
                  "omfang", "maks_sider")
#: Hvilke skjemafelt hvert intensjonsfelt dekker (for den statiske porten).
FELTDEKNING = {"bestillingstype": ("bestillingstype",),
               "mal_url": ("hostname", "sti"),
               "kravsett": ("kravsett",), "omfang": ("omfang",),
               "maks_sider": ("maks_sider",), "tenant": ()}

#: Lengdegrensene for idempotensnøkkelen. Speiler CHECKen på
#: `bestilling_idempotens.idempotensnokkel` (038 §3), og de to holdes
#: sammen av `test_idempotensnokkelens_grenser_speiler_lagringen`: går de
#: fra hverandre, er det igjen mulig å ta en beslutning som ikke kan
#: bokføres.
IDEMPOTENSNOKKEL_MIN = 8
IDEMPOTENSNOKKEL_MAKS = 200

#: LOKAL kode for «nøkkelen er OPPTATT akkurat nå» (Codex P2). Mot
#: klienten er den `idempotenskonflikt` som før — 409 på nøkkelen, ingen
#: beslutning, ingen kvote — og `bestill_endepunkt` oversetter den
#: tilbake. Skillet finnes for kallere som gjør en feilkode om til
#: TILSTAND: planveien pauser på en terminal kode, og en pause kan bare et
#: menneske oppheve. «En annen forespørsel holder nøkkelen nå» er
#: forbigående; «det står en rad med en annen intensjon» er det ikke.
#: Koden når aldri ut av prosessen og hører derfor ikke hjemme i
#: feilveitabellen — den ER `idempotenskonflikt` utad.
OPPTATT = "idempotens_opptatt"
#: Kode for «bunten er OPPTATT akkurat nå» (Cursor P1). Samme klasse
#: som `OPPTATT`, én knapp ressurs lenger inn: en annen bestilling holder
#: engangsbunten og er i ferd med å binde den. Den er FORBIGÅENDE, ikke
#: en dom: første forsøk kan fortsatt committe, og retry med samme nøkkel
#: er SAMME operasjon. Til #215 var den kollapset til `inndata_ubrukelig`
#: utad, så klienten ikke kunne velge nøkkeløkonomi på koden alene — nå
#: er den sin egen 409 i feilveitabellen (drift, ikke sikkerhet: ingen
#: dom over bunten er felt).
INNDATA_OPPTATT = "inndata_opptatt"
#: Kodene endepunktet oversetter tilbake til klientens kontrakt.
#: `INNDATA_OPPTATT` står ikke her lenger (#215): den ER klientkoden.
KLIENTKODE = {OPPTATT: "idempotenskonflikt"}


@dataclass(frozen=True)
class Bestillingstype:
    handling: str
    oppdragstype: str
    eiermodul: str
    kravsett: tuple[str, ...]
    omfang: tuple[str, ...]
    #: Lukket kropp og intensjonsfelt PER TYPE (#162 PR-3): WCAG-formen
    #: (hostname/sti/kravsett) og rekrutteringsformen (referanser) deler
    #: ingen felter, og ett globalt skjema ville tvunget den ene formens
    #: navn på den andre. Modulnivå-konstantene under står igjen som
    #: WCAG-typens (den statiske porten 21f leser dem fortsatt).
    skjemafelt: frozenset = frozenset()
    intensjonsfelt: tuple = ()


#: Kodefestet og lukket (port 13); deploy-porten krysser mot
#: `oppdragstype_register` (port 14) i deployport-modultyper.py.
#:
#: FRISTEN STÅR IKKE HER (Codex P1). Første utgave bar sin egen
#: `frister_s`-tabell med 90 min for `nettsted`, og den var både en
#: DUPLIKAT og feil: `oppdragskontrakt.UTFORELSESFRIST_VALG` deklarerer
#: 30/60 min, WCAG-manifestet lover det samme, og 90-minutterstallet var
#: nettopp det som ble NEDJUSTERT fordi stacken ikke kan holde det —
#: claimets eier-lease (037) og opplastingskapabiliteten (017) klemmes
#: begge til 3600 s, og ingen av dem har en fornyelsesvei. En kontroll
#: som lovlig brukte 90 min mistet dermed opplastingstokenet OG leasen
#: sin mens det nye 90-minutters utførelsesvinduet fortsatt sto åpent:
#: en annen controller kunne reclaime det samme oppdraget og starte
#: duplisert trafikk mot kundens nettsted, før den første uansett feilet
#: på opplastingen med hele jobben gjort.
#:
#: Fristen hører til KONTRAKTEN, og det er dét som gjør den til én frist:
#: samme tabell som arbeideren (`m37.arbeider._opprett_oppdrag`) og
#: controlleren leser. Et nytt omfang kan da ikke få en frist her som
#: ingen annen del av stacken kjenner.
BESTILLINGSTYPER: dict[str, Bestillingstype] = {
    "kontroll.wcag.nettsted": Bestillingstype(
        handling="kontroll.wcag.nettsted",
        oppdragstype="kontroll.wcag.nettsted",
        eiermodul="m_wcag_audit",
        kravsett=("wcag21_aa",),
        omfang=("enkeltside", "nettsted"),
        skjemafelt=frozenset({"bestillingstype", "hostname", "sti",
                              "kravsett", "omfang", "maks_sider"}),
        intensjonsfelt=("tenant", "bestillingstype", "mal_url",
                        "kravsett", "omfang", "maks_sider")),
    # #162 PR-3: evalueringsbestillingen. Kroppen bærer REFERANSER —
    # bunten (PR-1-opplastingen, bindes i oppdragets fødselstransaksjon)
    # og profilversjonen (061, append-only). Grensene (antall 1–5000,
    # slettefrist 30–365) er KONTRAKTENS (`oppdragskontrakt.FELTGRENSER`)
    # og leses derfra i normaliseringen — aldri en lokal kopi.
    "rekruttering.evaluering": Bestillingstype(
        handling="rekruttering.evaluering",
        oppdragstype="rekruttering.evaluering",
        eiermodul="m57_ats",
        kravsett=(),
        omfang=("bunt",),
        skjemafelt=frozenset({"bestillingstype", "inndata_ref",
                              "stillingsprofil_ref", "antall_soknader",
                              "omfang", "slettefrist_dogn"}),
        intensjonsfelt=("tenant", "bestillingstype", "inndata_id",
                        "stillingsprofil_ref", "antall_soknader",
                        "omfang", "slettefrist_dogn")),
}

_INNDATA_REF = re.compile(
    r"^inndata:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}"
    r"-[0-9a-f]{12}$")
_PROFIL_REF = re.compile(
    r"^([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"@([1-9][0-9]{0,5})$")

_HOSTNAME = re.compile(
    r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
#: Stien: absolutt, allerede normalisert. `..`, prosentkoding, query,
#: fragment, doble skråstreker og rare tegn AVVISES — vi normaliserer
#: ikke i det stille, for da ville to skrivemåter av samme intensjon
#: passert med hver sin hash.
_STI = re.compile(r"^/(?:[A-Za-z0-9._~-]+/?)*$")


class Bestillingsfeil(Exception):
    def __init__(self, kode: str, http: int = 400):
        self.kode, self.http = kode, http


def normaliser(tenant: str, data: dict) -> dict:
    """Lukket kropp → normalisert intensjon. Kaster Bestillingsfeil.

    Typen velges FØR skjemasjekken (per-type lukket kropp, #162 PR-3):
    et ukjent felt for DENNE typen er like feilformet som før, men
    hvilke felter som finnes, eier typen selv.
    """
    if not isinstance(data, dict):
        raise Bestillingsfeil("request_feilformet")
    typenavn = data.get("bestillingstype")
    # Uhashbar type (liste/dict) i feltet ville ellers reist TypeError i
    # dict-oppslaget — en 500 for noe som er klientens feilformede kropp.
    if not isinstance(typenavn, str):
        raise Bestillingsfeil("request_feilformet")
    bt = BESTILLINGSTYPER.get(typenavn)
    if bt is None:
        raise Bestillingsfeil("request_feilformet")
    if set(data) - bt.skjemafelt:
        raise Bestillingsfeil("request_feilformet")
    if bt.oppdragstype == "rekruttering.evaluering":
        return _normaliser_rekruttering(tenant, bt, data)
    host = data.get("hostname")
    if not isinstance(host, str) or "@" in host or ":" in host \
            or "/" in host or not _HOSTNAME.fullmatch(host.lower()) \
            or host != host.lower():
        # A-label, små bokstaver, aldri credentials/port/sti i feltet.
        raise Bestillingsfeil("request_feilformet")
    sti = data.get("sti")
    if sti is None:
        sti = "/"
    if not isinstance(sti, str) or ".." in sti or "//" in sti \
            or not _STI.fullmatch(sti):
        raise Bestillingsfeil("request_feilformet")
    if data.get("kravsett") not in bt.kravsett:
        raise Bestillingsfeil("request_feilformet")
    omfang = data.get("omfang")
    if omfang not in bt.omfang:
        raise Bestillingsfeil("request_feilformet")
    maks = data.get("maks_sider")
    if maks is None:
        maks = 1 if omfang == "enkeltside" else 50
    if not isinstance(maks, int) or isinstance(maks, bool) \
            or not 1 <= maks <= 50:
        raise Bestillingsfeil("request_feilformet")
    return {"tenant": tenant, "bestillingstype": data["bestillingstype"],
            "mal_url": f"https://{host}{sti}",
            "kravsett": data["kravsett"], "omfang": omfang,
            "maks_sider": maks}


def _normaliser_rekruttering(tenant: str, bt: Bestillingstype,
                             data: dict) -> dict:
    """Referanseformene og kontraktgrensene — aldri innhold.

    Grensene leses fra `oppdragskontrakt.FELTGRENSER` (én kilde, samme
    som utførerens skjema); referansene valideres som FORM her og som
    TILSTAND i forhåndsportene/`bind_inndata` senere.
    """
    import oppdragskontrakt
    m = _INNDATA_REF.fullmatch(str(data.get("inndata_ref") or ""))
    if m is None:
        raise Bestillingsfeil("request_feilformet")
    inndata_id = m.group(0).split(":", 1)[1]
    pm = _PROFIL_REF.fullmatch(str(data.get("stillingsprofil_ref") or ""))
    if pm is None:
        raise Bestillingsfeil("request_feilformet")
    grenser = oppdragskontrakt.FELTGRENSER["rekruttering.evaluering"]
    antall = data.get("antall_soknader")
    n_lo, n_hi = grenser["antall_soknader"]
    if isinstance(antall, bool) or not isinstance(antall, int) \
            or not n_lo <= antall <= n_hi:
        raise Bestillingsfeil("request_feilformet")
    if data.get("omfang") not in bt.omfang:
        raise Bestillingsfeil("request_feilformet")
    frist = data.get("slettefrist_dogn")
    if frist is not None:
        f_lo, f_hi = grenser["slettefrist_dogn"]
        if isinstance(frist, bool) or not isinstance(frist, int) \
                or not f_lo <= frist <= f_hi:
            raise Bestillingsfeil("request_feilformet")
        # Kanonisering, ikke default-innsetting (Codex P2): fraværet ER
        # standardvalget (057 `DEFAULT 90`), så en eksplisitt standard er
        # samme intensjon i en annen skrivemåte — uten dette ga retry med
        # utfylt 90 `idempotenskonflikt` mot sin egen første bestilling.
        if frist == oppdragskontrakt.SLETTEFRIST_STANDARD_DOGN:
            frist = None
    norm = {"tenant": tenant, "bestillingstype": data["bestillingstype"],
            "inndata_id": inndata_id,
            "stillingsprofil_ref": data["stillingsprofil_ref"],
            "antall_soknader": antall, "omfang": data["omfang"]}
    if frist is not None:
        norm["slettefrist_dogn"] = frist
    return norm


def intensjonshash(normalisert: dict) -> str:
    """SHA-256(JCS(normalisert intensjon)) — ETTER normalisering, aldri på
    rå request: to ekvivalente skrivemåter er samme intensjon."""
    from policy_validator import jcs
    bt = BESTILLINGSTYPER[normalisert["bestillingstype"]]
    intensjon = {k: normalisert[k] for k in bt.intensjonsfelt
                 if k in normalisert}
    return hashlib.sha256(jcs.kanoniske_bytes(intensjon)).hexdigest()


def kjernenokkelprefiks(nokkel: str) -> str:
    """Alt av kjernenøkkelen som følger av KLIENTENS nøkkel alene.

    Klientnøkkelen står som en sha256 nettopp for at dette skal være et
    entydig prefiks: nøkkelen er 8–200 tegn klientvalgt tekst og kan
    inneholde `:`, så `bestilling:{nokkel}:` ville matchet en ANNEN
    nøkkels kjernenøkkel (`abc` mot `abc:x`). Med et 64-hex ledd er
    prefikset fast og kan ikke gli over på en fremmed nøkkel.

    Prisen er lesbarheten i `revisjonslogg.idempotency_key`; den betales
    med vitende og vilje, og `bestilling_idempotens` bærer fortsatt
    klientnøkkelen i klartekst for den som skal feilsøke.
    """
    return "bestilling:" + hashlib.sha256(
        nokkel.encode("utf-8")).hexdigest() + ":"


def laasenavn_for(tenant: str, nokkel: str) -> str:
    """Navnet på advisory-låsen som serialiserer KLIENTENS nøkkel.

    Samme form som kjernens egen (`kjerne.behandle` steg 2): `\\x1f` er
    en separator ingen av leddene kan inneholde, så to par kan ikke
    kollapse til samme navn. Låsen er en ANNEN enn kjernens — den låser
    klientnøkkelen, kjernens låser kjernenøkkelen — og tas alltid FØR
    den, i hver eneste forespørsel: to låser i fast rekkefølge kan ikke
    danne en syklus.
    """
    return f"{tenant}\x1fbestillingsnokkel\x1f{nokkel}"


def inndata_laasenavn_for(tenant: str, inndata_id: str) -> str:
    """Navnet på advisory-låsen som serialiserer ENGANGSBUNTEN.

    Samme form og samme separator som `laasenavn_for`, men et annet
    andreledd, så de to navnerommene ikke kan kollapse.

    Låsen tas ALLTID etter nøkkellåsen (fast rekkefølge, og begge er
    `try`-låser), og den låser noe nøkkellåsen ikke kan nå: to
    bestillinger med ULIKE `Idempotency-Key` — eller helt uten nøkkel —
    peker lovlig på samme `inndata_id`, og bunten kan bare bindes én
    gang.
    """
    return f"{tenant}\x1finndata\x1f{inndata_id}"


def kjernenokkel_for(nokkel: str, hash_: str) -> str:
    """Kjernens idempotensnøkkel — BÆRER INTENSJONEN (Codex P1).

    Nøkkelen var `bestilling:<klientens nøkkel>` alene, og den formen har
    et hull i nøyaktig det vinduet gjenopprettingen finnes for: dør
    prosessen etter at `kjerne.behandle` har committet, men før
    `bestilling_idempotens` er skrevet, finnes det ikke lenger noe
    varig spor av HVA klienten ba om. Konfliktporten
    (`intensjonshash`-sammenligningen over `bestilling_idempotens`) har
    ingen rad å sammenligne mot, så en retry med samme nøkkel og en ANNEN
    lovlig kropp fant den gamle beslutningen på nøkkelen alene — og halen
    krypterte så retryens `norm` som oppdragets payload. Et TILLAT gitt
    for én side ble dermed oppdraget «crawl 50 sider av nettstedet»,
    lenket til en beslutning som aldri vurderte det.

    Intensjonshashen er derfor en DEL av nøkkelen. Da er selve oppslaget
    sammenligningen: finnes raden, gjaldt beslutningen nøyaktig denne
    intensjonen, og ingen egen sjekk kan gli fra den.

    En retry med en annen kropp i det samme vinduet treffer da ingen rad
    og ARVER aldri den forrige beslutningen. (Utenfor vinduet, som er
    normaltilfellet, står `bestilling_idempotens`-raden og gir
    `idempotenskonflikt` som før: ingen ny beslutning, ingen kvote brent.)

    MEN INTENSJONEN DELER IKKE NØKKELROMMET (Codex P1, runde 4). Bar
    nøkkelen intensjonen ALENE, ble den også serialiseringen: kjernen
    låser og claimer per kjernenøkkel (`behandle` steg 2–3), så to lovlige
    kropper under samme klientnøkkel fikk hver sin lås, hver sin
    beslutning, hver sin kvoteplass og hvert sitt oppdrag — og den siste
    `ON CONFLICT DO NOTHING` etterlot bare det ene oppdraget uten
    klientnøkkelen sin. Endepunktets lovede «samme nøkkel, ulik intensjon
    ⇒ konflikt» var da omgått, både i krasjvinduet og i to helt vanlige
    samtidige forsøk.

    Nøkkelen bærer derfor BEGGE deler, i to atskilte ledd: klientnøkkelen
    som et fast 64-hex prefiks (se `kjernenokkelprefiks`) og intensjonen
    etter det. Prefikset gjør oppslaget intensjonsUAVHENGIG — vi kan se
    at det finnes en committet beslutning på nøkkelen uten å vite hvilken
    intensjon den gjaldt — mens halen gjør sammenligningen. Samtidigheten
    holdes fra hverandre av `laasenavn_for`, som låser klientnøkkelen selv.
    """
    return kjernenokkelprefiks(nokkel) + hash_


#: Gyldighetspredikatet, ORDRETT fra `v_domeneautorisasjon.gyldig`
#: (016 §6). Egress-autoriteten slipper bare gjennom et domene som ER
#: dette, og bestillingsporten må stille samme spørsmål: en bestilling
#: mot et mål egress vil avvise, brenner kvote og legger seg i køen for
#: å dø der ute. Den utelatte biten var ferskheten — et domene kan stå
#: `verifisert` med uutløpt `utloper` i månedsvis etter at den daglige
#: revalideringen sluttet å lykkes, og nettopp det er tilfellet porten
#: finnes for. `test_domenepredikatet_speiler_visningen` krysser de to
#: tekstene mekanisk, så de ikke kan gli fra hverandre i stillhet.
DOMENE_GYLDIG_SQL = (
    "status = 'verifisert' AND now() < utloper"
    " AND siste_vellykkede_revalidering > now() - interval '72 hours'")


def _verifisert_hostname(conn, tenant: str, hostname: str):
    """Positivt autorisert mål VED OPPRETTELSE (portene 9–10): en
    `verifisert`, ikke-utløpt, FERSK revalidert domenekontroll for
    nøyaktig dette hostnamet. Wildcard teller ikke her — bestillingen
    gjelder ett konkret mål.

    -> `siste_vellykkede_revalidering` (verifikasjonens tidspunkt — det
    plattformattestasjonen under attesterer) eller None. NULL-veiene er
    fail-closed, som i visningen: mangler `utloper` eller
    `siste_vellykkede_revalidering`, er raden ikke et bevis på noe."""
    rad = conn.execute(
        "SELECT siste_vellykkede_revalidering FROM domenekontroll"
        " WHERE tenant=%s AND hostname=%s"
        " AND (" + DOMENE_GYLDIG_SQL + ")",
        (tenant, hostname)).fetchone()
    return rad[0] if rad else None


def utfor_bestilling(tjeneste, conn, tenant: str, aktor: str,
                     data, nokkel, rid: str):
    """HELE bestillingsveien etter autentisering og parse — den ENE veien
    (klarsignal periodisk kontroll §4): browserendepunktet og plan-
    materialisereren kaller nøyaktig denne, så policyport, idempotens,
    kvote og oppdragsopprettelse aldri kan gli fra hverandre mellom en
    menneskelig og en planlagt bestilling.

    -> ("feil", kode) eller ("ok", kropp, replay). Kalleren oversetter til
    sitt eget svarformat; `aktor` er hele aktørstrengen ("bruker:<bid>"
    eller "plan:<plan_id>").
    """
    from . import kjerne
    #: Sesjonslåsene som er TATT, i den rekkefølgen de ble tatt (nøkkelen
    #: først, så engangsbunten) — se serialiseringene under og
    #: opprydningen i `finally`.
    laaser: list[str] = []
    try:
        try:
            norm = normaliser(tenant, data)
        except Bestillingsfeil as f:
            tjeneste.logg.hendelse(f.kode, rid, tenant, art="sikkerhet",
                                   flate="bestilling")
            return ("feil", f.kode)
        bt = BESTILLINGSTYPER[norm["bestillingstype"]]
        # `hostname` finnes bare i WCAG-formen; rekrutteringsformens
        # målautorisasjon er referansene (egne porter under).
        hostname = (norm["mal_url"].split("://", 1)[1].split("/", 1)[0]
                    if "mal_url" in norm else None)

        hash_ = intensjonshash(norm)
        # LENGDEN VALIDERES FØR BESLUTNINGEN (Codex P2). Lagringen krever
        # 8–200 tegn, men enhver ikke-blank verdi slapp gjennom hit. Med en
        # for kort eller for lang nøkkel COMMITTET `kjerne.behandle`
        # beslutningen først, og CHECKen slo til på innsettingen etterpå:
        # outbox-transaksjonen rullet tilbake, klienten fikk en 500, og
        # HVER retry gjentok nøyaktig det samme — en committet beslutning
        # (frekvenskvote brent) uten en bestilling som noen gang kan
        # fullføres. En nøkkel vi ikke kan lagre må avvises før den kan
        # utløse noe.
        if nokkel is not None and not (
                IDEMPOTENSNOKKEL_MIN <= len(nokkel) <= IDEMPOTENSNOKKEL_MAKS):
            tjeneste.logg.hendelse("request_feilformet", rid, tenant,
                                   art="sikkerhet", flate="bestilling",
                                   grunn="idempotensnokkel_lengde")
            return ("feil", "request_feilformet")

        # BESLUTNINGENE SERIALISERES PÅ KLIENTENS NØKKEL (Codex P1).
        # Kjernen serialiserer per KJERNEnøkkel, og den bærer intensjonen:
        # to lovlige kropper under samme `Idempotency-Key` fikk derfor hver
        # sin lås og kunne begge committe en beslutning — to kvoteplasser,
        # to oppdrag, og en `ON CONFLICT DO NOTHING` som etterlot det ene
        # oppdraget uten klientnøkkelen sin. Endepunktets lovede «samme
        # nøkkel, ulik intensjon ⇒ konflikt» var da omgått av selve
        # nøkkelvalget.
        #
        # Låsen dekker HELE veien fra gjenspill-lesingen til bokføringen,
        # og den kan ikke være en xact-lås: kjernen committer inne i
        # `behandle`, og en xact-lås tatt her ville sluppet nettopp der
        # vinduet er. Sesjonslåsen slippes derfor eksplisitt i `finally` —
        # og en tilkobling som IKKE fikk sluppet den, lukkes i stedet for å
        # gå tilbake i poolen med en fremmed lås på seg.
        #
        # `try` og ikke en ventende lås: en nøkkel som er opptatt NÅ har en
        # forespørsel i arbeid, og den er per definisjon en konflikt på
        # nøkkelen (409). Ingen beslutning tas, ingen kvote brennes, og
        # klientens neste forsøk møter enten gjenspillet eller konflikten
        # — men aldri en pool-tilkobling som står og venter.
        #
        # ... MEN «OPPTATT NÅ» OG «ANNEN INTENSJON» ER IKKE DET SAMME FOR EN
        # KALLER SOM GJØR KODEN TIL TILSTAND (Codex P2). Mot klienten er
        # begge 409 på nøkkelen, og det er riktig: ingen beslutning, ingen
        # kvote, prøv igjen eller bruk en annen nøkkel. Men planveien
        # oversetter en terminal feilkode til en PAUSE bare et menneske kan
        # oppheve, og «en annen forespørsel holder nøkkelen akkurat nå» er
        # et forbigående sammenstøt: en arbeider som bruker lengre tid enn
        # planleasen (120 s) mister vinduet til en ny arbeider, som da
        # møter nettopp denne låsen mens det FØRSTE forsøket kan lykkes
        # like etter. Den lokale koden er derfor sin egen; endepunktet
        # oversetter den tilbake til klientens kontrakt.
        if nokkel:
            navn = laasenavn_for(tenant, nokkel)
            fikk = conn.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                (navn,)).fetchone()[0]
            conn.rollback()
            if not fikk:
                return ("feil", OPPTATT)
            laaser.append(navn)

        from db.pg import sett_kontekst
        sett_kontekst(conn, tenant, aktor, rid)
        # GJENSPILL FØR HOSTNAME-PORTEN (Codex P2). Porten er en
        # OPPRETTELSES-regel: målet må være positivt autorisert i det
        # bestillingen tas imot. Et gjenspill oppretter ingenting — det
        # leverer et resultat som alt er committet. Sto porten først,
        # ville en bestilling som ble utført, men mistet svaret sitt, fått
        # `bestilling_hostname_uverifisert` på retryen om
        # domeneverifikasjonen i mellomtiden utløp eller ble trukket —
        # samme nøkkel og samme intensjon, nytt svar. Modulens egen
        # kontrakt sier at samme nøkkel + samme hash gir samme resultat,
        # og en verifisering som utløper etterpå gjør ikke det som skjedde
        # ugjort.
        if nokkel:
            rad = conn.execute(
                "SELECT intensjonshash, svarkropp, beslutning, oppdrag_id"
                " FROM bestilling_idempotens WHERE tenant=%s AND"
                " idempotensnokkel=%s", (tenant, nokkel)).fetchone()
            if rad is not None:
                conn.rollback()
                if rad[0] != hash_:
                    # 21b/c: ULIK intensjon gjenbruker ALDRI et resultat —
                    # og tar ingen ny beslutning. Kvoten er urørt.
                    return ("feil", "idempotenskonflikt")
                # HELE det opprinnelige svaret, ikke en redusert form:
                # `begrunnelse` for STOPP/BRUDD og `unntak_id` for BRUDD
                # er nettopp det klienten mistet da svaret forsvant.
                # Tom kropp finnes bare på rader skrevet FØR kolonnen
                # (en base midt i 038); de får den gamle formen, som er
                # alt som noen gang ble lagret om dem.
                lagret = rad[1] or {"beslutning": rad[2],
                                    "oppdrag_id": rad[3]}
                return ("ok", lagret, True)
        verifisert_ts = None
        gjenopprettbar = False
        terminal_dom = False
        if hostname is not None:
            verifisert_ts = _verifisert_hostname(conn, tenant, hostname)
            if verifisert_ts is None:
                conn.rollback()
                tjeneste.logg.hendelse("bestilling_hostname_uverifisert",
                                       rid, tenant, art="sikkerhet",
                                       hostname=hostname)
                return ("feil", "bestilling_hostname_uverifisert")
        else:
            # REKRUTTERINGSFORMENS MÅLPORTER (#162 PR-3): referansene må
            # peke på noe tenanten faktisk EIER, målt før beslutningen
            # brenner kvote — samme økonomi som hostname-porten. Dørene
            # (061-oppslaget ved payloadbygging, `bind_inndata` i
            # fødselstransaksjonen) feller uansett til slutt; dette er
            # den billige, ærlige forhåndsavvisningen.
            #
            # ... MEN EN FORHÅNDSPORT UTEN LÅS ER ET TOCTOU (Cursor P1).
            # Nøkkellåsen over serialiserer `Idempotency-Key`, og bunten
            # er ikke nøkkelen: to samtidige bestillinger med ULIKE
            # nøkler (eller helt uten) på samme `inndata_id` passerte
            # begge porten under, tok begge en TILLAT-beslutning — to
            # frekvenskvoter, to committede beslutninger — og bare den
            # ene vant `bind_inndata` i fødselstransaksjonen. Taperen
            # rullet oppdraget tilbake og svarte `inndata_ubrukelig`,
            # men beslutningen sto igjen committet: en kvoteplass brent
            # uten en eneste jobb. Vinduet er ikke smalt slik
            # `logging_feilet`-armen er smal — det er hele veien fra
            # lesingen her til bindingen, med policyevalueringen imellom.
            #
            # Bunten serialiseres derfor på SEG SELV, med samme mønster
            # som nøkkelen: en sesjons-`try`-lås tatt FØR forhåndsporten
            # leser, holdt gjennom beslutningen og bindingen, sluppet i
            # `finally`. Da er lesingen under gyldig helt fram til
            # bindingen, og en taper møter låsen FØR beslutningen —
            # ingen kvote brent i det hele tatt.
            #
            # `try` og ikke en ventende lås, av samme grunn som over: en
            # bunt som er opptatt NÅ har en bestilling i arbeid, og
            # svaret er forbigående. Og fordi begge låsene er `try`-låser
            # tatt i fast rekkefølge, kan de ikke danne en syklus.
            sett_kontekst(conn, tenant, aktor, rid)
            # GJENOPPRETTBAR DOM FØR BUNTPORTEN (Codex P2 på #210): dør
            # prosessen etter at kjernen committet STOPP/BRUDD men før
            # `bestilling_idempotens` ble skrevet, står bunten ubundet —
            # og utløper den før klienten retryer, dømte porten under
            # `inndata_ubrukelig` FØR gjenopprettingen (656→) rakk å
            # svare med den beslutningen som alt er tatt. Buntens
            # tilstand er muterbar; dommen er det ikke.
            #
            # …OG FØR BUNTLÅSEN (Codex P2, runde 5): lå proben bak
            # låsen, kunne en retry i krasjvinduet møte en annen
            # forespørsels lås og få den FORBIGÅENDE buntkoden i
            # stedet for den IMMUTABLE dommen (eller konflikten).
            # Er dommen alt tatt, trengs ingen buntlås for å svare
            # — gjenspillet tar ingen ny beslutning, og et TILLAT-
            # gjenspills binding vernes terminalt av dørens egen
            # FOR UPDATE.
            #
            # PREFIKSET, ikke den eksakte nøkkelen (Codex P2, runde 3):
            # med eksakt nøkkel+intensjon fanget porten bare retryen med
            # SAMME kropp — en retry med samme klientnøkkel og en ANNEN
            # intensjon er lovet `idempotenskonflikt` (748→), men falt i
            # 404/409 fra de muterbare portene her først. Finnes NOEN
            # ferdig kjernerad under klientnøkkelen, eier nedstrøms-
            # logikken dommen (gjenspill eller konflikt) — samme
            # collation-frie prefiksform som gjenopprettingslesingen.
            # …OG KLASSEN LUKKES VED Å KLASSIFISERE DOMMEN HER (Codex
            # P2, runde 6): en TERMINAL utgang (STOPP/BRUDD-gjenspill,
            # eller konflikt fordi eksakt intensjon ikke matcher) skal
            # forbi ALLE muterbare porter — buntlås, referanser og
            # claim-tilstand — for svaret er alt bestemt. Et gjenspilt
            # TILLAT skal derimot fortsatt gjennom oppdrag+binding, og
            # det er nettopp kappløpet buntlåsen finnes for: det RE-TAR
            # låsen (opptatt → forbigående kode, ingen kvote i fare —
            # dommen er alt committet).
            if nokkel:
                pfx = kjernenokkelprefiks(nokkel)
                rader = conn.execute(
                    "SELECT nokkel, respons FROM idempotens"
                    " WHERE tenant=%s AND left(nokkel, %s) = %s"
                    "   AND status='ferdig'",
                    (tenant, len(pfx), pfx)).fetchall()
                gjenopprettbar = bool(rader)
                eksakt = dict(rader).get(kjernenokkel_for(nokkel, hash_))
                # Kjernen lagrer beslutningen i VERSALER («TILLAT») —
                # målt i testbasen, ikke antatt; casefold, ikke likhet.
                terminal_dom = gjenopprettbar and (
                    eksakt is None
                    or str((eksakt or {}).get("beslutning")
                           or "").lower() != "tillat")
            conn.rollback()
            navn = inndata_laasenavn_for(tenant, norm["inndata_id"])
            fikk = True
            if not terminal_dom:
                fikk = conn.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended(%s, 0))",
                    (navn,)).fetchone()[0]
                conn.rollback()
                if fikk:
                    laaser.append(navn)
            if not fikk:
                # IKKE `inndata_ubrukelig` (Cursor P1): den er terminal,
                # og planveien gjør en terminal kode om til en pause bare
                # et menneske kan oppheve. Siden #215 bærer ledningen
                # skillet helt ut: flaten beholder nøkkelen og sier «prøv
                # igjen om et øyeblikk» — mot en død bunt ville det vært
                # løgn, og mot en holdt lås er alt annet det.
                tjeneste.logg.hendelse(INNDATA_OPPTATT, rid, tenant,
                                       art="drift")
                return ("feil", INNDATA_OPPTATT)
            sett_kontekst(conn, tenant, aktor, rid)
            pm = _PROFIL_REF.fullmatch(norm["stillingsprofil_ref"])
            prad = conn.execute(
                "SELECT 1 FROM stillingsprofil"
                " WHERE tenant=%s AND profil_id=%s AND versjon=%s",
                (tenant, pm.group(1), int(pm.group(2)))).fetchone()
            irad = conn.execute(
                "SELECT status, pg_catalog.now() > utloper, oppdrag_id"
                "  FROM inndata_artefakt"
                " WHERE tenant=%s AND inndata_id=%s"
                "   AND eiermodul='m57_ats' AND formaal='soknadsbunt'",
                (tenant, norm["inndata_id"])).fetchone()
            conn.rollback()
            # BEGGE referanseportene vaktes (Codex P2, runde 4): runde 3
            # vaktet bare buntporten, og en annen-intensjon-retry med en
            # velformet, men IKKE-EKSISTERENDE profilref falt i 404 her
            # før konfliktdommen. Prefiks funnet → nedstrøms eier dommen.
            if prad is None and not gjenopprettbar:
                tjeneste.logg.hendelse("stillingsprofil_ukjent", rid,
                                       tenant, art="sikkerhet")
                return ("feil", "stillingsprofil_ukjent")
            if not gjenopprettbar and (
                    irad is None or irad[1] or irad[0] != "lastet"
                    or irad[2] is not None):
                # Samme svar for ukjent/utløpt/ulastet/alt bundet — et
                # oppslagsverk over bunter skal ikke finnes (058-formen).
                tjeneste.logg.hendelse("inndata_ubrukelig", rid, tenant,
                                       art="sikkerhet")
                return ("feil", "inndata_ubrukelig")
        # Typen må kunne CLAIMES før noen beslutning tas: et TILLAT for et
        # oppdrag ingen modul kan plukke ser vellykket ut mens arbeidet dør
        # stille i køen — det utløper på `utforelsesfrist` uten at noen
        # noen gang så det. Vakta bor HER og ikke i deploy-porten (18/8):
        # registreringen eies av modul-onboardingen og finnes lovlig ikke
        # ennå på en fersk base, og en deploy som krever den tar tjenesten
        # ned i stedet for å verne kunden. Ingen loggpost, intet
        # kvoteforbruk — som hostname-porten.
        #
        # VILKÅRENE ER CLAIM-VEIENS EGNE (Codex P1). Første utgave leste
        # bare registerraden, men den er IMMUTABEL: den overlever både
        # delvis onboarding og nøddeaktivering, så en type kunne stå
        # registrert med riktig eier mens `claim_neste_oppdrag` (037)
        # likevel ikke ville gi den til noen. Funksjonen krever i tillegg
        # `modulhode.status = 'aktiv'` og en `moduldeployment` med
        # registerradens kontrakt, `livslop = 'claiming'` og KALLERENS
        # miljø. Står modulen `installert`/`staging_verifisert` (onboarding
        # påbegynt, ikke fullført) eller `nodeaktivert` (nødstopp), eller
        # er hver deployment `draining`/`retired` (releasebytte), er
        # oppdraget uclaimbart fra det blir opprettet. Vakta speiler derfor
        # de samme vilkårene, og 503-en ber klienten prøve igjen når
        # modulen er tilbake.
        #
        # Kallerens release og module_epoch er IKKE med: en bestilling
        # peker ikke ut hvilken arbeider som skal ta den, og hvilken
        # release som er `claiming` kan lovlig skifte mellom opprettelse og
        # claim. Miljøet er prosessens eget (`gjeldende_miljo`) — en
        # staging-deployment gjør ikke et produksjonsoppdrag claimbart.
        from miljo import gjeldende_miljo
        registrert = conn.execute(
            "SELECT r.eiermodul, h.status,"
            "       EXISTS (SELECT 1 FROM moduldeployment d"
            "                WHERE d.modul_id = r.eiermodul"
            "                  AND d.kontraktversjon = r.kontraktversjon"
            "                  AND d.kontrakt_hash = r.kontrakt_hash"
            "                  AND d.miljo = %s AND d.livslop = 'claiming')"
            "  FROM oppdragstype_register r"
            "  LEFT JOIN modulhode h ON h.modul_id = r.eiermodul"
            " WHERE r.oppdragstype = %s",
            (gjeldende_miljo(), bt.oppdragstype)).fetchone()
        # …med mindre dommen alt er tatt (Codex P2, runde 6): claim-
        # tilstanden er MUTERBAR (draining/nøddeaktivert), og et
        # committet STOPP/BRUDD eller en konflikt skal svares uansett —
        # et gjenspill trenger ingen NY claimbar modul.
        if gjenopprettbar:
            conn.rollback()
        elif (registrert is None or registrert[0] != bt.eiermodul
                or registrert[1] != "aktiv" or not registrert[2]):
            conn.rollback()
            tjeneste.logg.hendelse(
                "bestillingstype_utilgjengelig", rid, tenant, art="drift",
                bestillingstype=norm["bestillingstype"],
                # Hvorfor typen ikke er claimbar hører til DRIFTSLOGGEN, ikke
                # svaret: klienten skal ikke kunne kartlegge hvilke moduler
                # som er nede. Den som feilsøker onboardingen trenger det.
                grunn=("uregistrert" if registrert is None
                       else "eiermodul_avvik" if registrert[0] != bt.eiermodul
                       else f"modulstatus:{registrert[1]}"
                       if registrert[1] != "aktiv" else "ingen_claiming"))
            return ("feil", "bestillingstype_utilgjengelig")
        conn.rollback()

        kjernenokkel = kjernenokkel_for(nokkel, hash_) if nokkel \
            else "bestilling-eng:" + secrets.token_hex(16)
        # Tenantens AKTIVE policy er beslutningsgrunnlaget — bestilleren
        # velger aldri policy (eller modul, frist, epoch — feltene finnes
        # ikke i kroppen, port 16).
        #
        # GJENOPPRETTING FØLGER BESLUTNINGEN, IKKE KLOKKA (Codex P2).
        # Dør prosessen etter at `kjerne.behandle` har committet, men før
        # oppdraget og `bestilling_idempotens` er skrevet, er kjernens
        # egen idempotensrad det eneste sporet av forsøket.
        #
        # GRUNNLAGET GJENSKAPES IKKE — DET LESES (Codex P2, runde 2). Første
        # utgave gjenopprettet bare POLICYEN og bygget resten av hendelsen
        # på nytt for å treffe kjernens input-hash og utløse gjenspillet.
        # Men hendelsen bærer også plattformens domenekontroll-attestasjon,
        # og DEN er avledet av `siste_vellykkede_revalidering` — MUTABEL
        # domenetilstand. Lykkes den planlagte revalideringen i nettopp det
        # vinduet, får retryen et nytt `utstedt`/`utloper`, en ny `jti`, en
        # ny signatur og dermed en ny input-hash: `idempotenskonflikt`, for
        # alltid, med en committet TILLAT-beslutning stående uten oppdraget
        # sitt. Å gjenopprette policyen men gjette på attestasjonen var å
        # løse halve problemet.
        #
        # Kjernen lagrer SELV svaret sitt (`idempotens.respons`, satt i
        # samme transaksjon som loggposten), og et gjenspill er per kontrakt
        # nøyaktig den raden byte for byte. Er den der, er beslutningen alt
        # tatt: da leser vi den, og bygger verken hendelse eller attestasjon
        # på nytt. Ingen avledning av mutabel tilstand kan da komme i veien
        # for at halen får fullført bestillingen.
        #
        # OPPSLAGET ER SELV INTENSJONSSJEKKEN (Codex P1, runde 3). Nøkkelen
        # bærer `hash_` — se `kjernenokkel_for`. Uten det gjenbrukte denne
        # lesingen en committet beslutning på KLIENTENS nøkkel alene, i det
        # ene vinduet der `bestilling_idempotens` ennå ikke finnes og
        # konfliktporten over derfor ikke har noe å sammenligne. Da kunne en
        # retry med en annen lovlig kropp arve beslutningen og få halen til
        # å kryptere SIN payload inn i den.
        #
        # ... OG DET SPØR PÅ KLIENTNØKKELEN, IKKE PÅ INTENSJONEN (Codex P1,
        # runde 4). Sto oppslaget på hele kjernenøkkelen, var det BLINDT for
        # en committet beslutning under en annen intensjon: retryen fant
        # ingen rad, gikk videre og tok sin EGEN beslutning — enda en
        # kvoteplass og enda et oppdrag på en nøkkel som per kontrakt bærer
        # nøyaktig én. Låsen over stenger de samtidige tilfellene, men
        # nettopp her er den borte: den døde med prosessen som krasjet.
        #
        # Vi spør derfor på PREFIKSET (klientnøkkelen alene) og leser
        # intensjonen ut av raden vi fant. Er den vår, er beslutningen tatt
        # og vi leser svaret; er den en annens, er dette `idempotenskonflikt`
        # — samme svar som `bestilling_idempotens` gir utenfor vinduet, og
        # fortsatt uten en eneste ny beslutning.
        #
        # ... OG ROMMET ER VÅRT ALENE (Codex P1, runde 5). Lesingen under
        # STOLER på raden den finner: er den der, er beslutningen tatt, og
        # for et TILLAT lenkes oppdraget til dens loggpost uten at noe av
        # det opprinnelige grunnlaget etterprøves — det KAN ikke etterprøves
        # her, siden hele poenget er at input-hashen ikke lar seg regne ut
        # på nytt (attestasjonen hviler på mutabel domenetilstand, se over).
        # Men `idempotens` er DELT: `/v1/beslutning` førte klientens egen
        # `Idempotency-Key` rett inn i samme tabell, og kjernenøkkelen her
        # er en deterministisk funksjon av data klienten selv sendte. En
        # kaller hos samme tenant med `decision:write` kunne dermed regne ut
        # nøkkelen, kjøre sin EGEN beslutning under den, og la denne
        # lesingen plukke opp svaret som om det var bestillingens.
        # `kjerne.RESERVERTE_NOKKELROM` stenger rommet: en klientvalgt
        # nøkkel som begynner på `bestilling:` avvises av kjernen før den
        # rører databasen. Tilliten her hviler på DEN porten.
        #
        # `left(...) = ` og ikke et intervall (`nokkel >= a AND < b`):
        # intervallet ville lest nøkkelen i databasens COLLATION, og under
        # en ikke-C collation er punktum og kolon ikke nødvendigvis der
        # byte-rekkefølgen har dem — altså et oppslag som kan bomme på
        # raden det finnes for. Prefikssammenligningen er collation-fri.
        sett_kontekst(conn, tenant, aktor, rid)
        prefiks = kjernenokkelprefiks(nokkel) if nokkel else None
        tidligere = conn.execute(
            "SELECT nokkel, respons FROM idempotens"
            " WHERE tenant=%s AND left(nokkel, %s) = %s AND status='ferdig'",
            (tenant, len(prefiks), prefiks)).fetchall() if nokkel else []
        conn.rollback()
        beslutninger = {rad[0]: rad[1] for rad in tidligere}
        # `ferdig_har_respons` (003) garanterer at en ferdig rad HAR en
        # respons, så `None` her betyr «ingen rad», aldri «tom rad».
        gjenopprettet = beslutninger.get(kjernenokkel)
        if gjenopprettet is None and beslutninger:
            # En committet beslutning på nøkkelen, for en ANNEN intensjon.
            # Kvoten er urørt, og halen til den beslutningen kan fortsatt
            # fullføre seg selv når klienten retryer med SIN kropp.
            # Ingen loggpost: `idempotenskonflikt` er `avvis` i feiltabellen
            # — kun et HTTP-svar — og skal svare likt uansett hvilken av de
            # to radene som fant konflikten.
            return ("feil", "idempotenskonflikt")

        if gjenopprettet is not None:
            # Beslutningen ER tatt og committet: svaret er kjernens eget,
            # lest som det står. Nøyaktig det objektet gjenspillet i
            # `kjerne._flyt` ville returnert — uten å gå gjennom hendelsen
            # som bygde det, og dermed uten å avlede noe av tilstand som
            # kan ha flyttet seg siden beslutningen falt.
            respons = dict(gjenopprettet)
            svar = kjerne.Svar(int(respons.get("http", 200)), respons,
                               respons.get("unntak_id"), replay=True)
            tjeneste.logg.hendelse("bestilling_gjenopprettet", rid, tenant,
                                   art="drift",
                                   beslutning=respons.get("beslutning"))
        else:
            sett_kontekst(conn, tenant, aktor, rid)
            prad = conn.execute(
                "SELECT policy_id FROM policyer WHERE tenant=%s AND aktiv",
                (tenant,)).fetchall()
            conn.rollback()
            if len(prad) != 1:
                return ("feil", "policy_ukjent")
            policy_id = prad[0][0]

            # `ressurs_id` er MALBINDINGSFELT for WCAG-formen:
            # `malbindingsbrudd` krever at den ER det normaliserte
            # vertsnavnet fra `mal_url`. Rekrutteringsformens ressurs er
            # bunt-referansen, og dataklassen er PERSONDATA — det er
            # CV-er, og policyen må eksplisitt tillate klassen for
            # handlingen (aldri en stille «offentlig»).
            if hostname is not None:
                event = {"handling": bt.handling, "ressurs_id": hostname,
                         "mal_url": norm["mal_url"],
                         "kravsett": norm["kravsett"],
                         "omfang": norm["omfang"],
                         "maks_sider": norm["maks_sider"],
                         "dataklasser": ["offentlig"],
                         "dataklasser_kilde": "connector"}
            else:
                event = {"handling": bt.handling,
                         "ressurs_id": "inndata:" + norm["inndata_id"],
                         "omfang": norm["omfang"],
                         "antall_soknader": norm["antall_soknader"],
                         "dataklasser": ["persondata"],
                         "dataklasser_kilde": "connector"}
            # PLATTFORMEN ATTESTERER SIN EGEN DOMENEKONTROLL.
            # Aktiveringsporten (036) KREVER at en ekstern_lesing-handling
            # bærer et registrert målautorisasjonsvilkår
            # (domenekontroll_verifisert) — og motoren krever så en
            # attestasjon fra en betrodd verifikator for hvert vilkår i
            # handlingen. Uten denne ville altså enhver bestilling under en
            # LOVLIG AKTIVERT policy endt i unntakskøen med
            # attestasjon_mangler: porten som verifiserte målet over (og
            # fastholder ferskheten, 72 t) er nettopp verifikatoren, men den
            # sa det aldri i attestasjonsformen motoren leser.
            #
            # Verifikatoren er PLATTFORMENS (`v_domenekontroll`): registeret
            # i DISPONIT_ATT_NOKLER bærer nøkkelen, og tenantens policy
            # velger selv om den stoler på den (`verifikatorer`-blokken +
            # `betrodd_for: [domenekontroll_verifisert]`). Mangler nøkkelen i
            # registeret, mintes ingenting — beslutningen tar da vilkårsveien
            # den alltid tok (unntakskø), og driftsloggen navngir hvorfor.
            dk_nokler = (tjeneste.nokler or {}).get("v_domenekontroll") or {}
            if hostname is not None and dk_nokler:
                # DETERMINISTISK, forankret i selve verifikasjonen:
                # `utstedt` er domenekontrollens
                # `siste_vellykkede_revalidering` og `utloper` dens
                # 72-timers ferskhetsvindu — attestasjonen UTTALER nøyaktig
                # det porten over målte, verken mer eller ferskere.
                #
                # Grenen kjøres BARE når det ikke finnes en committet
                # beslutning for nøkkelen (se over). Det er hele grunnen til
                # at avledningen får hvile på mutabel domenetilstand: en
                # retry etter en ny revalidering leser beslutningen sin i
                # stedet for å bygge attestasjonen på nytt, og møter derfor
                # ikke lenger en input-hash som har flyttet seg.
                from policy_validator import attestering
                nid = sorted(dk_nokler)[0]
                # jti-en er engangs (kjernen brenner den i beslutningens
                # transaksjon) og må derfor være unik PER BESLUTNING:
                # nøkkelen inn i avledningen er kjernens idempotensnøkkel,
                # så to bestillinger samme dag deler aldri jti.
                jti_grunnlag = (f"{tenant}|{hostname}|{kjernenokkel}|"
                                f"{verifisert_ts.isoformat()}")
                event["attestasjoner"] = {
                    "domenekontroll_verifisert": attestering.signer({
                        "verifikator": "v_domenekontroll",
                        "tenant_id": tenant, "handling": bt.handling,
                        "vilkaar": "domenekontroll_verifisert",
                        "ressurs_id": hostname, "policy_id": policy_id,
                        "utstedt": verifisert_ts.isoformat(),
                        "utloper": (verifisert_ts
                                    + timedelta(hours=72)).isoformat(),
                        "jti": "dk-" + hashlib.sha256(
                            jti_grunnlag.encode("utf-8")).hexdigest()[:32],
                        "resultat": True,
                    }, nid, dk_nokler[nid])}
            elif hostname is not None:
                tjeneste.logg.hendelse(
                    "domenekontroll_attestasjon_utilgjengelig", rid, tenant,
                    art="drift")
            from policy_validator.engine import EvaluationContext
            ctx = EvaluationContext(
                tenant_id=tenant, aktor_rolle="bestiller", autentisert=True,
                kilde="api_token")
            try:
                svar = kjerne.behandle(
                    conn, ctx, policy_id=policy_id, event=event,
                    idempotency_key=kjernenokkel, request_id=rid,
                    aktor=aktor, nokler=tjeneste.nokler)
            except kjerne.Feilsvar as f:
                tjeneste.logg.hendelse(f.kode, rid, tenant, art="sikkerhet")
                return ("feil", f.kode)

        beslutning = str(svar.kropp.get("beslutning") or "").upper()
        utfall = {"TILLAT": "tillat", "STOPP": "stopp"}.get(
            beslutning, "brudd")
        oppdrag_id = None
        sett_kontekst(conn, tenant, aktor, rid)
        if utfall == "tillat":
            logg = conn.execute(
                "SELECT id FROM revisjonslogg WHERE tenant=%s AND"
                " idempotency_key=%s ORDER BY id DESC LIMIT 1",
                (tenant, kjernenokkel)).fetchone()
            if logg is None:
                conn.rollback()
                return ("feil", "logging_feilet")
            from db import kryptering
            key_id, dek = kryptering.hent_eller_opprett_aktiv_dek(conn,
                                                                  tenant)
            if hostname is not None:
                payload = {k: norm[k] for k in ("mal_url", "kravsett",
                                                "omfang", "maks_sider")}
            else:
                # Profil-ØYEBLIKKSBILDET bygges SERVER-SIDE fra 061 nå,
                # under samme transaksjonsvindu som oppdraget fødes i —
                # versjonen er append-only, så kopien kan aldri drifte.
                # Klientens innhold når aldri payloaden; bare referansen.
                pm = _PROFIL_REF.fullmatch(norm["stillingsprofil_ref"])
                prof = conn.execute(
                    "SELECT navn FROM stillingsprofil"
                    " WHERE tenant=%s AND profil_id=%s AND versjon=%s",
                    (tenant, pm.group(1), int(pm.group(2)))).fetchone()
                if prof is None:
                    # Slettet/aldri fantes mellom forhåndsporten og nå —
                    # append-only gjør dette nær-umulig, men porten
                    # feiler ærlig, ikke med en KeyError.
                    conn.rollback()
                    return ("feil", "stillingsprofil_ukjent")
                krav = [{"kravnavn": kn, "vekt": v} for kn, v in
                        conn.execute(
                            "SELECT kravnavn, vekt FROM"
                            " stillingsprofil_krav WHERE tenant=%s AND"
                            " profil_id=%s AND versjon=%s"
                            " ORDER BY rekkefolge",
                            (tenant, pm.group(1),
                             int(pm.group(2)))).fetchall()]
                payload = {"stillingsprofil_ref":
                               norm["stillingsprofil_ref"],
                           "stillingsprofil": {
                               "profil_id": pm.group(1),
                               "versjon": int(pm.group(2)),
                               "navn": prof[0], "krav": krav},
                           "antall_soknader": norm["antall_soknader"],
                           "omfang": norm["omfang"]}
                if "slettefrist_dogn" in norm:
                    payload["slettefrist_dogn"] = norm["slettefrist_dogn"]
                # Kontraktens egne porter, målt FØR noe skrives — samme
                # tabeller utføreren validerer mot (én kilde).
                import oppdragskontrakt
                minimert = oppdragskontrakt.minimer(bt.oppdragstype,
                                                    payload)
                if (oppdragskontrakt.mangler_paakrevde(bt.oppdragstype,
                                                       minimert)
                        or oppdragskontrakt.bryter_feltkontrakten(
                            bt.oppdragstype, minimert)):
                    conn.rollback()
                    tjeneste.logg.hendelse("intern_feil", rid, tenant,
                                           art="drift",
                                           grunn="payloadkontrakt_brutt")
                    return ("feil", "intern_feil")
                payload = minimert
            ct, nonce = kryptering.krypter(dek, payload, tenant, key_id)
            # Typens EGEN frist, fra kontrakten — samme oppslag og samme
            # tabell som arbeiderveien bruker (Codex P1). `payload` er
            # nøyaktig den minimerte formen `utforelsesfrist_s` velger på.
            import oppdragskontrakt
            frist_s = oppdragskontrakt.utforelsesfrist_s(bt.oppdragstype,
                                                         payload)
            if frist_s is None:
                # Uoppnåelig så lenge den statiske porten under står (se
                # `test_bestillingstyper_arver_kontraktens_frist`), og
                # bevisst en 500 og ikke en stille reservefrist: en type
                # uten deklarert frist ville arvet den generiske
                # 24-timersfristen, og for en `ekstern_lesing`-kanal mot
                # kundens nettsted er det ikke en romslig frist — det er
                # et døgnlangt vindu bestillingen aldri ba om.
                conn.rollback()
                tjeneste.logg.hendelse("intern_feil", rid, tenant,
                                       art="drift",
                                       grunn="utforelsesfrist_mangler",
                                       oppdragstype=bt.oppdragstype)
                return ("feil", "intern_feil")
            naa = datetime.now(timezone.utc)
            try:
                oppdrag_id = int(conn.execute(
                    "SELECT opprett_beslutningsoppdrag(%s,%s,%s,%s,%s,"
                    "%s,%s,%s,%s,%s)",
                    (tenant, logg[0], bt.oppdragstype, bt.handling,
                     bt.eiermodul, ct, key_id, nonce,
                     naa + timedelta(seconds=frist_s),
                     naa + timedelta(seconds=frist_s))).fetchone()[0])
            except psycopg.errors.UniqueViolation:
                # `oppdrag_en_per_beslutning` (008): en retry som mistet
                # svaret ETTER at oppdraget ble skrevet — vinnerens rad er
                # svaret, aldri et oppdrag nummer to (21/21-lik). Var
                # dette rekrutteringsformen, bandt vinnerens transaksjon
                # også bunten (atomisk med oppdraget), så replayet har
                # ingenting å binde.
                conn.rollback()
                sett_kontekst(conn, tenant, aktor, rid)
                oppdrag_id = int(conn.execute(
                    "SELECT id FROM oppdrag WHERE tenant=%s AND"
                    " beslutning_loggpost_id=%s", (tenant,
                                                   logg[0])).fetchone()[0])
            else:
                if hostname is None:
                    # X1 (#192): bindingen skjer i oppdragets EGEN
                    # fødselstransaksjon — `bind_inndata` krever
                    # fødselsattesten, og nettopp derfor kan vinduet
                    # «synlig oppdrag uten bunt» ikke finnes. Feiler
                    # bindingen (utløpt/kapret/opptatt i mellomtiden),
                    # rulles OGSÅ oppdraget tilbake: en evaluering uten
                    # bunt skal aldri fødes. Beslutningen er da alt
                    # committet (kvote brent) — samme økonomi som
                    # `logging_feilet`-armen, og forhåndsporten over gjør
                    # vinduet smalt.
                    try:
                        conn.execute(
                            "SELECT bind_inndata(%s,%s,%s,%s)",
                            (tenant, norm["inndata_id"], oppdrag_id,
                             "m57_ats"))
                    except (psycopg.errors.InvalidParameterValue,
                            psycopg.errors.UniqueViolation) as e:
                        conn.rollback()
                        tjeneste.logg.hendelse(
                            "inndata_ubrukelig", rid, tenant,
                            art="sikkerhet", grunn=type(e).__name__)
                        return ("feil", "inndata_ubrukelig")
        # Svaret bygges FØR bokføringen, så det som lagres er nøyaktig det
        # som sendes — ikke en andre konstruksjon av den samme formen.
        # `request_id` er UTENFOR kroppen: det er forespørselens, ikke
        # bestillingens, og legges på begge veier ut (her og i gjenspillet).
        kropp = {"beslutning": utfall, "oppdrag_id": oppdrag_id}
        if utfall != "tillat":
            kropp["begrunnelse"] = svar.kropp.get("begrunnelse") or []
        if svar.unntak_id is not None:
            kropp["unntak_id"] = svar.unntak_id
        if nokkel:
            # Raden skrives i transaksjonen som FULLFØRER bestillingen, og
            # dekker ALLE utfall (også stopp/brudd — gjenspill etter
            # timeout gir samme kode uten å brenne kvote; kvotevernet for
            # selve beslutningen bæres av kjernens egen idempotensnøkkel,
            # deterministisk avledet av bestillingsnøkkelen).
            conn.execute(
                "INSERT INTO bestilling_idempotens (tenant,"
                " idempotensnokkel, intensjonshash, oppdrag_id, beslutning,"
                " svarkropp) VALUES (%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (tenant, idempotensnokkel) DO NOTHING",
                (tenant, nokkel, hash_, oppdrag_id, utfall,
                 json.dumps(kropp, ensure_ascii=False)))
        conn.commit()
        return ("ok", kropp, False)
    finally:
        # SESJONSLÅSENE SLIPPES HER, ELLER SÅ BÆRER TILKOBLINGEN EN
        # FREMMED LÅS TILBAKE TIL KALLEREN — se endepunktets kommentar.
        # Slippes i motsatt rekkefølge av tagningen, og en tilkobling som
        # ikke fikk sluppet, LUKKES: da faller resten av låsene dens med
        # sesjonen, og det er nettopp derfor sløyfa kan stoppe der.
        for navn in reversed(laaser):
            try:
                conn.rollback()
                conn.execute(
                    "SELECT pg_advisory_unlock(hashtextextended(%s, 0))",
                    (navn,))
                conn.rollback()
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                break


def bestill_endepunkt(tjeneste, request: Request) -> Response:
    from .app import _feilsvar, _rid, kanonisk_json
    from .policyadmin_http import _Avbrudd, _browserkontekst
    rid = _rid(request)
    try:
        conn = tjeneste.pool.hent()
    except (TimeoutError, psycopg.Error):
        return _feilsvar("db_utilgjengelig", rid)
    try:
        try:
            tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                           "bestilling:opprett")
        except _Avbrudd as a:
            return a.respons
        raa = request.scope.get("state", {}).get("kropp", b"")
        try:
            data = json.loads(raa.decode("utf-8"))
        except (ValueError, RecursionError):
            # `json.loads` er REKURSIV (Codex P2) — se historikken i
            # `utfor_bestilling`-æraens forgjenger: dyp nøsting er
            # klientinput, og RecursionError er ingen ValueError.
            return _feilsvar("request_feilformet", rid)
        raa_nokkel = request.headers.get("idempotency-key")
        nokkel = raa_nokkel.strip() if raa_nokkel and raa_nokkel.strip() \
            else None
        res = utfor_bestilling(tjeneste, conn, tenant, f"bruker:{bid}",
                               data, nokkel, rid)
        if res[0] == "feil":
            # Den lokale «opptatt»-koden er `idempotenskonflikt` utad:
            # klientens kontrakt på nøkkelen er uendret (409, ingen
            # beslutning, ingen kvote). Skillet er planveiens.
            return _feilsvar(KLIENTKODE.get(res[1], res[1]), rid)
        _, kropp, replay = res
        hoder = {"x-request-id": rid}
        if replay:
            hoder["idempotent-replay"] = "1"
        return kanonisk_json({**kropp, "request_id": rid}, 200, hoder)
    finally:
        tjeneste.pool.gi_tilbake(conn)
