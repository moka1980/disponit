"""Controlleren (PR-014c §2): claimer, styrer motoren, pakker resultatet,
laster opp, kvitterer. Den har credentials (modultokenet fra onboarding);
motoren har ingen.

Én kjøring per kall (`kjor_en`) — timerdrevet i drift, direkte i test.
Alle nettverkskall går gjennom en injisert `klient` (httpx-kompatibel /
Starlette TestClient), og signering av kvitteringen gjennom en injisert
`signer` — kontrakten (PR-006) eies av plattformen, ikke av denne fila.

Feilhåndteringens utfall:
  * Ingen oppdrag (204) → stille retur.
  * UUTFØRBART claim → kvittering `avbrutt` FØR motoren startes. Både
    en bestilling utenfor typens verdikontrakt (`oppdrag_ugyldig`, Codex
    P1) og en claim uten opplastingskapabilitet
    (`ingen_opplastingskapabilitet`, Codex P2) er kjent uutførbare av
    claimet alene. `ekstern_lesing` er observerbar trafikk mot noen
    andres nettsted, og den forespørselen er selve skaden når rapporten
    aldri kunne blitt gyldig eller aldri kunne blitt levert.
  * Motorfeil / skjemabrudd i egen rapport → kvittering `avbrutt` UTEN
    artefakt: et delvis artefakt finnes ikke (§10 siste rad), men
    plattformen skal få vite at oppdraget er FERDIG mislykket — taushet
    ville latt fristen gjøre jobben og M-37 gjette.
  * Opplastingen AVVIST (Codex P1) → samme `avbrutt`, med feilkode
    `opplasting_avvist`. Rapporten ble bygget, men kom ikke frem, og et
    unntak ut av kjøreløkka her ville etterlatt oppdraget claimet uten et
    ord til plattformen.
  * Full suksess → artefakt + kvittering `utfort` med den serverberegnede
    hashen fra opplastingssvaret (den, og bare den, signeres).
  * Kvittering AVVIST (Codex P1) → `ukvittert`. Kvitteringsendepunktet er
    ikke en varsling som alltid går igjennom: fencing, hashvalidering og
    artefaktpromotering kan avvise den (409), og serveren kan feile (5xx).
    Da er oppdraget IKKE ferdig hos plattformen — artefaktet kan til og
    med være karantenesatt — og et `utfort` herfra ville fått en
    planlegger til å tro at kjøringen var i havn. Utfallet er eget nettopp
    fordi det ikke er det samme som en ærlig motorfeil.
  * SEN EVIDENS (Codex P1) → samme `ukvittert`. Blir kjøringen ferdig
    etter `utforelsesfrist`, men før `evidensfrist`, svarer endepunktet
    202 med `lagret_uten_statusendring`: evidensen er bevart, men
    plattformen har BEVISST latt oppdraget være ufullført. En 2xx er
    derfor ikke i seg selv et statusskifte — se `_kvittert`.
  * GJENTATT kvittering (Codex P2) → utfallet den forrige ga. Kvitteringen
    er idempotent med vilje, så en utfører som mistet svaret skal kunne
    sende NØYAKTIG den samme på nytt. Avsluttet den forrige oppdraget,
    svarer plattformen 200 `idempotent`, og da er dette `utfort`/`avbrutt`
    som første gang. Ble den bare bevart som sen evidens, svarer den
    `idempotent_uten_statusendring`, og da står `ukvittert` — samme
    tilstand, samme ord.

Kvitteringen bindes til den KONTROLLERTE VERTEN (Codex P1): `ressurs_id`
er det normaliserte vertsnavnet fra `mal_url`, samme verdi og samme
funksjon som `oppdragskontrakt.malbindingsbrudd` krever av hendelsen — se
`_ressursbinding`.

`avbrutt` over betyr FERDIG mislykket, og forutsetter derfor at
plattformen tok imot feil-kvitteringen. Gjorde den ikke det, er utfallet
`ukvittert` også på feilveiene (Codex P1) — se `_feilutfall`. `grunn` står
uansett: hvorfor kjøringen feilet er like sant om kvitteringen kom frem
eller ikke.
"""
from __future__ import annotations

import jsonschema

from . import OPPDRAGSTYPE, rapportskjema
from .motor import Motorfeil
from .rapport import bygg


#: Kroppsstatusene som betyr at plattformen FAKTISK skiftet status på
#: oppdraget — de `/v1/oppdrag/kvittering` gir sammen med 200.
#:
#: `idempotent` er med (Codex P2): en RETRY av en kvittering som allerede
#: avsluttet oppdraget er den dokumenterte suksessveien, ikke et avvik.
#: Uten den meldte controlleren `ukvittert` for et oppdrag plattformen for
#: lengst hadde gjort ferdig — og en planlegger som tror på det, følger
#: opp noe som er avsluttet. Retryen er ikke hypotetisk: kvitteringen ER
#: idempotent nettopp for at en utfører som mistet svaret skal kunne
#: sende den samme kvitteringen på nytt.
#:
#: `idempotent_uten_statusendring` er IKKE med, og det er hele grunnen til
#: at plattformen skiller de to: gjentar man en SEN kvittering (202
#: `lagret_uten_statusendring`), er evidensen bevart mens oppdraget står
#: bevisst ufullført. Ett ord for begge ville gjort denne linja til
#: valget mellom to løgner — se `_idempotent_svar` i `api.app`.
_STATUSSKIFTE = ("utfort", "feilet", "idempotent")

def _ressursbinding(payload: dict) -> str | None:
    """Kvitteringens `ressurs_id`: den kontrollerte VERTEN (Codex P1).

    `payload.get("ressurs_id", "")` ga tom streng for HVER ENESTE
    WCAG-kvittering. Det er ikke et uhell i payloaden — `oppdragskontrakt`
    minimerer typen til `mal_url`, `kravsett`, `omfang` og `maks_sider`
    med vilje, så feltet finnes ikke å hente. Controlleren signerte altså
    hver kvittering, både suksess og feil, med en TOM ressursbinding, mens
    `resultathash` på plattformsiden regner `ressurs_id` som en del av
    resultatet.

    Den autoriserte ressursen er det normaliserte vertsnavnet fra
    `mal_url` — det er nøyaktig den likheten `malbindingsbrudd` krever av
    hendelsen (`event["ressurs_id"] == normaliser_vertsnavn(mal_url)`).
    Kvitteringen bindes derfor til SAMME verdi, med SAMME funksjon: en
    egen normalisering her ville vært en annen streng ved første
    rotprikk eller store bokstaver, og da hadde bindingen vært pynt.

    Verten er heller ikke ny eksponering: den er en del av `mal_url`, som
    alt står i det tenantskopede oppdraget kvitteringen hører til.

    Selve avledningen er plattformens (`oppdragskontrakt.malvert`), ikke
    modulens: den slår opp måldomenet på TYPEN og normaliserer med samme
    funksjon som `malbindingsbrudd`. `rapport.bygg` binder sidene i
    rapporten med nøyaktig det samme kallet, så kvittering og evidens kan
    ikke navngi hver sin vert.

    `oppdragskontrakt` er allerede en kjøretidsavhengighet for modulen
    (`rapport` kanoniserer med `policy_validator`), men importen står ved
    kallstedet av samme grunn som der: modulens IMPORTTID skal ikke være
    bundet til plattformkjernen.

    -> None når målet ikke lar seg lese. Da har modulen ingenting å binde
    kvitteringen til, og fail-closed er posituren: se `kjor_en`.
    """
    from oppdragskontrakt import malvert
    return malvert(OPPDRAGSTYPE, payload)


def _kontraktsbrudd(payload: dict) -> list[str]:
    """Feltene i claimet som gjør oppdraget uutførbart — FØR motoren
    startes (Codex P1).

    Bare `mal_url` ble lest før den eksterne skanningen; `omfang`,
    `maks_sider` og `kravsett` ble først sett av `rapport.bygg`, altså
    ETTER at motoren hadde vært ute på kundens nettsted. Et claim med
    `omfang: "alt"`, `maks_sider: 0` eller et ukjent `kravsett` kunne
    derfor aldri gi en gyldig rapport — men det ga observerbar,
    ekstern trafikk mot et nettsted som ikke er vårt, hver eneste gang.
    `ekstern_lesing` er nettopp klassen der en unødvendig forespørsel er
    selve skaden; da skal den ikke sendes for en bestilling vi allerede
    kan se at ingen rapport kan oppfylle.

    Kontrakten er PLATTFORMENS, ikke modulens (`oppdragskontrakt`): den
    samme tabellen stopper oppdraget ved OPPRETTELSEN. To sett regler
    ville betydd at bestillingsveien og utføreren kunne være uenige om
    hva som er et lovlig oppdrag, og da hadde denne porten vært en annen
    port enn den som slapp oppdraget gjennom. Den står likevel her og
    ikke bare der: raden kan være skrevet av en eldre release, og en
    utfører som stoler på at noen andre alt har sjekket, sjekker ikke.

    `mal_url` er ikke med: den leses av `_ressursbinding` rett over, med
    den samme funksjonen målbindingen bruker, og et uleselig mål har sin
    egen feilkode.
    """
    from oppdragskontrakt import bryter_feltkontrakten, mangler_paakrevde
    return sorted({*mangler_paakrevde(OPPDRAGSTYPE, payload),
                   *bryter_feltkontrakten(OPPDRAGSTYPE, payload)})


def _kvittert(rk) -> bool:
    """Skiftet plattformen status på oppdraget?

    Kun 2xx teller — 409 (fencing, hashavvik, avvist promotering) og 5xx
    betyr at oppdraget står igjen uferdig hos plattformen.

    Men 2xx er IKKE nok (Codex P1). Fullfører kjøringen etter
    `utforelsesfrist`, men før `evidensfrist`, svarer endepunktet 202 med
    `status: "lagret_uten_statusendring"`: evidensen er bevart, og det er
    HELE det som skjedde — `unntak.status` står urørt, oppdraget er
    bevisst latt ufullført. Å lese den 202-en som en kvittering ga
    `utfall: "utfort"` for et oppdrag plattformen selv regner som uferdig,
    og en planlegger som tror på modulens ord slutter da å følge opp noe
    som aldri ble avsluttet.

    Derfor kreves BÅDE 2xx og en kroppsstatus som navngir skiftet. En
    kropp vi ikke kan lese (ikke JSON, ikke et objekt) er heller ingen
    bekreftelse: da vet vi ikke hva som skjedde, og «vet ikke» skal
    behandles som uferdig, ikke som ferdig."""
    if not 200 <= rk.status_code < 300:
        return False
    try:
        kropp = rk.json()
    except ValueError:
        return False
    return isinstance(kropp, dict) and kropp.get("status") in _STATUSSKIFTE


def _feilutfall(rk, grunn: str, **ekstra) -> dict:
    """Utfallet for en kjøring som feilet — avledet av `_kvittert`, ikke
    antatt (Codex P1).

    Feilgrenene meldte `avbrutt` uansett hva plattformen svarte på
    feil-kvitteringen. `avbrutt` betyr «oppdraget er FERDIG mislykket», og
    det er nettopp det plattformen ikke har bekreftet når kvitteringen ble
    avvist med 409/5xx eller lagret som sen evidens med 202: da står
    oppdraget fortsatt claimet og uferdig der, akkurat som når en
    SUKSESS-kvittering blir avvist. Suksessgrenen har lest `_kvittert`
    siden forrige runde; feilgrenene gjorde det ikke, og forskjellen var
    vilkårlig — en planlegger som tror på `avbrutt` slutter å følge et
    oppdrag som aldri ble avsluttet.

    `grunn` og `kvittering_status` blir stående uansett utfall: hvorfor
    kjøringen feilet er like sant om kvitteringen kom frem eller ikke."""
    kvittert = _kvittert(rk)
    return {"utfall": "avbrutt" if kvittert else "ukvittert",
            "grunn": grunn, "kvittering_status": rk.status_code,
            "kvittert": kvittert, **ekstra}


def kjor_en(klient, token: str, motor, kontekst: dict, signer) -> dict:
    """-> {"utfall": "tomt"|"utfort"|"avbrutt"|"ukvittert", ...}."""
    hode = {"authorization": f"Bearer {token}"}
    r = klient.post("/v1/oppdrag/claim", json={}, headers=hode)
    if r.status_code == 204:
        return {"utfall": "tomt"}
    r.raise_for_status()
    claim = r.json()
    payload = claim["payload"]

    vert = _ressursbinding(payload)
    kvittering_basis = {
        "oppdrag_id": claim["oppdrag_id"], "tenant": claim["tenant"],
        "kvittering_jti": claim["kvittering_jti"],
        "repair_operation_id": claim["repair_operation_id"],
        "owner_claim_id": claim["owner_claim_id"],
        "owner_generation": claim["owner_generation"],
        "ressurs_id": vert or "",
    }

    def kvitter(kropp):
        rk = klient.post("/v1/oppdrag/kvittering", json=signer(kropp),
                         headers=hode)
        return rk

    if vert is None:
        # Lar målet seg ikke lese, har modulen ingenting å binde
        # kvitteringen til — og et claim som kom hit BURDE ha et lesbart
        # `mal_url`: `malbindingsbrudd` avviste alt annet da oppdraget ble
        # opprettet. Å kontrollere «noe» og signere en tom binding er den
        # ene tilstanden denne fiksen finnes for å hindre, så motoren
        # startes ikke i det hele tatt. Plattformen får en ærlig feilkode
        # i stedet for taushet.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "malbinding_mangler"})
        return _feilutfall(rk, "malbinding_mangler")

    brudd = _kontraktsbrudd(payload)
    if brudd:
        # HELE bestillingen leses før motoren startes, ikke bare målet
        # (Codex P1): et claim som ikke kan gi en gyldig rapport skal
        # ikke koste kundens nettsted en eneste forespørsel. Feilen er
        # oppdragets, ikke motorens, og feilkoden sier det.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "oppdrag_ugyldig"})
        return _feilutfall(rk, f"oppdrag_ugyldig:{brudd}")

    opplasting = claim.get("opplasting")
    if not opplasting:
        # Claim uten opplastingskapabilitet for denne typen er en
        # konfigurasjonstilstand (port 15-adferden på plattformsiden):
        # artefakttypen mangler, er tvetydig, eller er filtrert bort for
        # denne deploymenten. Rapporten kan da ikke leveres uansett hva
        # kontrollen finner.
        #
        # Sjekken står FØR motoren (Codex P2). Sto den etter, gjorde
        # controlleren hele den eksterne kontrollen — full crawl av
        # kundens nettsted — for så å kaste rapporten på en betingelse
        # den kunne lest av claimet før første forespørsel. Det er samme
        # regnestykke som `_kontraktsbrudd` over: leveringsveien er en
        # del av bestillingens gjennomførbarhet, og en umulig levering
        # skal ikke koste noen andre trafikk.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "ingen_opplastingskapabilitet"})
        return _feilutfall(rk, "ingen_kapabilitet")

    try:
        resultat = motor.kjor(payload)
        rapport = bygg(resultat, payload=payload, kontekst=kontekst)
        # Egen validering FØR innsending: serveren validerer uansett (mot
        # samme innholdsadresserte skjema), men modulen skal aldri sende
        # noe den selv kan se er ugyldig — og en avvist opplasting her er
        # en MOTORFEIL sett fra oppdraget.
        jsonschema.Draft202012Validator(rapportskjema.SKJEMA).validate(
            rapport)
    except (Motorfeil, jsonschema.ValidationError) as e:
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "motor_avbrutt"})
        return _feilutfall(rk, type(e).__name__)

    ro = klient.post("/v1/artefakt",
                     json={"kapabilitet_jti": opplasting["jti"],
                           "rapport": rapport}, headers=hode)
    if not 200 <= ro.status_code < 300:
        # AVVIST OPPLASTING (Codex P1): `ro.raise_for_status()` kastet ut
        # av kjøreløkka uten kvittering, og oppdraget ble stående claimet
        # til fristen — akkurat den taushetslinjen §10 forbyr, og det
        # motsatte av det denne fila selv sier («en avvist opplasting her
        # er en MOTORFEIL sett fra oppdraget»). Rapporten er bygget, men
        # den kom ikke frem: da er oppdraget ærlig mislykket.
        rk = kvitter({**kvittering_basis, "resultat": "feilet",
                      "feilkode": "opplasting_avvist"})
        return _feilutfall(rk, "opplasting_avvist",
                           opplasting_status=ro.status_code)
    artefakt = ro.json()

    rk = kvitter({**kvittering_basis, "resultat": "utfort",
                  "artefakt_id": artefakt["artefakt_id"],
                  "klartekst_sha256": artefakt["klartekst_sha256"]})
    svar = {"artefakt_id": artefakt["artefakt_id"],
            "kvittering_status": rk.status_code,
            "sider": len(rapport["sider_kontrollert"])}
    if not _kvittert(rk):
        # Rapporten er bygget og lastet opp, men plattformen tok IKKE imot
        # kvitteringen — oppdraget er uferdig der, og artefaktet kan være
        # karantenesatt. Å melde `utfort` her ville vært modulens ord mot
        # plattformens tilstand, og planleggeren ville trodd på modulen.
        return {"utfall": "ukvittert", **svar}
    return {"utfall": "utfort", **svar}
