"""M-48s ENE utgående kanal: oppslag mot Enhetsregisteret.

KLYNGE 6s UNNTAK, OG BARE DET (eierbeslutning 3/9). Modulen har to
eksterne kilder i spesifikasjonen, og bare den ene er koblet på:

  FORETAKSREGISTERET er offentlig, uten hemmeligheter, og det vi sender
  ut er et ORGANISASJONSNUMMER — offentlige foretaksdata, ikke
  persondata. Oppslaget er nødvendig i doktrinens egen forstand:
  motpartens roller og registerstatus finnes ikke andre steder.

  KREDITTLEVERANDØREN er kommersiell, krever hemmeligheter, sender de
  reelle rettighetshavernes navn til en tredjepart og gir en score vi
  ville blitt fristet til å handle på. Den er holdt tilbake bak porten
  `modulen_hentet_kredittdata`, og finnes ikke i denne fila.

VERTEN LESES FRA BASEN, ALDRI FRA EN KONSTANT HER. `m48_registrert_vert()`
er den ene kilden, og `m48_reserver_oppslag` avviser alt annet. Hadde
verten stått som en streng i denne fila, ville porten
`oppslag_mot_uregistrert_vert` vært en påstand om Python-kode i stedet
for en regel basen håndhever — og en endring her ville vært usynlig for
alle som leser registeret etterpå.

REKKEFØLGEN ER RESERVER → HENT → FULLFØR, og den er ikke en stilart:
doktrinen sier at den unødvendige forespørselen ER skaden, så vinduet
må sjekkes FØR forespørselen går ut. `m48_reserver_oppslag` gjør
sjekken og skriver raden i én transaksjon; uten en id herfra kan svaret
aldri bli en `motpartsversjon`.

IKKE GJENNOM OPPDRAGSKONTRAKTEN, OG DET ER ET AVVIK JEG SKRIVER NED.

Klyngefundamentet sa at den dagen en integrasjon kobles på, skal det
skje i `oppdragskontrakt.py`. Det gjør det ikke her, og grunnen er at
maskineriet der løser et ANNET problem enn dette:

  * Et `oppdrag` er ASYNKRONT — reserveres, claimes av en modul,
    utføres, produserer et artefakt. `m_wcag_audit` skanner et nettsted
    i minutter. Et foretaksoppslag er ett synkront kall en bruker
    venter på, og å legge det i en kø ville gjort et 200-svar til en
    kvittering brukeren måtte polle på.

  * `krever_malautorisasjon` gjelder ikke. Den finnes fordi WCAG-typen
    skanner KUNDENS nettsted og vi må bevise at kunden eier det.
    Foretaksregisteret er VÅRT valgte mål; det varierende er et
    organisasjonsnummer. Å sette flagget ville krevd en rad i
    `malautorisasjonsvilkar` for en vert kunden ikke eier — en port
    ingen kunne gått gjennom.

DOKTRINEN ER LIKEVEL OPPFYLT, OG PÅ ETT PUNKT STRENGERE:

  * FREKVENS. Aktiveringsportens teller grupperer på det bundne målet.
    Vår regel er per organisasjonsnummer per tenant, og den håndheves
    TRANSAKSJONELT i basen (`m48_reserver_oppslag`) — to samtidige
    arbeidere kan ikke begge slippe gjennom. En teller utenfor
    transaksjonen har det kappløpet.

  * EGRESS. Samme vakt, samme fil: `ssrf.lag_klient` (014b).

  * FORMÅL OG HJEMMEL. `foretaksoppslag` har to NOT NULL-kolonner uten
    standardverdi. Oppdragsraden har ingen tilsvarende.

Avviket er altså i MASKINERIET, ikke i dommen. Skulle oppslaget en dag
bli asynkront — en nattlig porteføljegjennomgang noen faktisk har
hjemlet — er oppdragskontrakten stedet, og da må denne kommentaren
rettes sammen med den endringen.

FORMEN PÅ SVARET ER OBSERVERT, IKKE GJETTET (3/9): feltnavnene under er
lest av et ekte svar fra `enheter/923609016`. 404 for ukjent
organisasjonsnummer er også observert. 410 for en slettet enhet er
DOKUMENTERT hos Brreg, men ikke observert her — den behandles som
`ikke_funnet` med `slettet`-status, og hvis det viser seg feil, er det
`svarstatus`-raden som avslører det.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from . import ssrf

#: Brregs åpne enhetsregister-API. Stien, ikke verten — verten kommer
#: fra basen.
STI = "/enhetsregisteret/api/enheter/{orgnr}"

#: Registerstatusene modulen kjenner, i migrasjon 116s lukkede sett.
AKTIV = "aktiv"
UNDER_AVVIKLING = "under_avvikling"
AVVIKLET = "avviklet"
SLETTET = "slettet"
UKJENT = "ukjent"


class OppslagFeil(Exception):
    """Forespørselen kom ikke fram, eller svaret var ikke lesbart.

    Skilles fra «foretaket finnes ikke»: det siste ER et svar, og
    registreres som `ikke_funnet`.
    """


@dataclass(frozen=True)
class Foretak:
    """Ett lest foretak, tolket ned til det migrasjon 116 lagrer.

    `raa_sha256` er innholdsadressen til svaret slik det kom — den
    pinner nøyaktig hvilke bytes tolkningen bygger på, uten at vi
    lagrer en kropp ingen har lest.
    """
    organisasjonsnummer: str
    navn: str
    organisasjonsform: str
    registerstatus: str
    konkurs: bool
    under_tvangsavvikling: bool
    kildeversjon: str
    raa_sha256: str


def _status(doc: dict) -> str:
    """Registerstatusen, utledet av flaggene registeret faktisk svarer med.

    REKKEFØLGEN ER FRA MEST TIL MINST ENDELIG. En enhet kan være både
    under avvikling og konkurs; da er «under avvikling» det som best
    beskriver hvor den er, og konkursflagget står uansett som sin egen
    kolonne. Å slå dem sammen til ett felt ville vært å tolke bort en
    opplysning vurderingen skal kunne gjøres om igjen på.
    """
    if doc.get("slettedato"):
        return SLETTET
    if doc.get("underAvvikling"):
        return UNDER_AVVIKLING
    if doc.get("underTvangsavviklingEllerTvangsopplosning"):
        return UNDER_AVVIKLING
    if doc.get("registreringsdatoEnhetsregisteret"):
        return AKTIV
    # Et svar uten registreringsdato er et svar vi ikke forstår. `ukjent`
    # er ærligere enn `aktiv`, og feilretningen er den modulen krever:
    # en motpart vi ikke forstår skal ikke se kredittverdig ut.
    return UKJENT


def tolk(raa: bytes) -> Foretak:
    """Rå kropp → `Foretak`. Skilt fra `hent` så den kan testes uten nett."""
    try:
        doc = json.loads(raa)
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise OppslagFeil(f"svaret var ikke lesbar JSON: {e}") from e
    if not isinstance(doc, dict):
        raise OppslagFeil("svaret var ikke et objekt")

    orgnr = str(doc.get("organisasjonsnummer") or "")
    navn = str(doc.get("navn") or "").strip()
    form = str((doc.get("organisasjonsform") or {}).get("kode") or "").strip()
    if not orgnr or not navn or not form:
        raise OppslagFeil(
            "svaret manglet organisasjonsnummer, navn eller"
            " organisasjonsform — det er ikke et foretak")

    # `registreringsdatoEnhetsregisteret` er IKKE kildeversjonen: den
    # sier når foretaket ble registrert, ikke når opplysningene gjaldt.
    # API-et eksponerer ingen egen versjon, så den ærlige kildeversjonen
    # er datoen vi leste — og nøyaktig hvilke bytes vi leste er pinnet
    # av `raa_sha256` på oppslagsraden.
    return Foretak(
        organisasjonsnummer=orgnr,
        navn=navn,
        organisasjonsform=form.upper(),
        registerstatus=_status(doc),
        konkurs=bool(doc.get("konkurs")),
        under_tvangsavvikling=bool(
            doc.get("underTvangsavviklingEllerTvangsopplosning")),
        kildeversjon="",  # settes av `hent`, som vet lesetidspunktet
        raa_sha256=hashlib.sha256(raa).hexdigest(),
    )


def hent(vert: str, organisasjonsnummer: str,
         lest_dato: str) -> Foretak | None:
    """Ett oppslag mot `vert`. -> `Foretak`, eller None for «finnes ikke».

    `vert` SKAL komme fra `m48_registrert_vert()` og sendes videre til
    `m48_reserver_oppslag` — den samme verdien begge steder, slik at
    basen og forespørselen aldri kan mene forskjellige ting.

    Går utelukkende over den IP-pinnede ssrf-transporten: ingen
    redirects, korte timeouts, 256 KiB-tak. Ingen hemmeligheter sendes,
    fordi API-et ikke har noen.
    """
    url = f"https://{vert}{STI.format(orgnr=organisasjonsnummer)}"
    klient = ssrf.lag_klient()
    try:
        r = klient.get(url, headers={"Accept": "application/json"})
        raa = ssrf.les_begrenset(r)
        if r.status_code == 404:
            return None
        if r.status_code == 410:
            # Slettet enhet. Ikke observert i praksis (3/9) — kroppen
            # kan bære `slettedato`, men vi lover ikke å kunne lese den.
            return None
        if r.status_code != 200:
            raise OppslagFeil(
                f"oppslaget ga status {r.status_code}")
        foretak = tolk(raa)
    except ssrf.SsrfAvvist as e:
        raise OppslagFeil(str(e)) from e
    except OSError as e:
        raise OppslagFeil(
            f"oppslaget feilet: {type(e).__name__}") from e
    finally:
        klient.close()

    if foretak.organisasjonsnummer != organisasjonsnummer:
        # Registeret svarte om et ANNET foretak. Det skal ikke kunne
        # skje, og nettopp derfor står sjekken her: uten den ville en
        # forveksling blitt en profil på feil motpart.
        raise OppslagFeil(
            f"registeret svarte om {foretak.organisasjonsnummer},"
            f" ikke {organisasjonsnummer}")
    return Foretak(**{**foretak.__dict__, "kildeversjon": lest_dato})
