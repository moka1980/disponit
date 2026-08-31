"""E-postsenderen: kopien av det som alt står i portalen.

Innboksen er sannheten; denne sender en KOPI. Det avgjør nesten alle valgene
under — særlig at en feilet sending aldri er kritisk: varselet står der
uansett, mottakeren ser det neste gang hun logger inn, og driften skal ikke
vekkes av at én e-post ikke gikk.

TEKSTEN RENDRES HER, ikke i databasen. Raden bærer `tekstnokkel` + `parametre`,
og lokaliseringen skjer ved sending — så en rettet oversettelse gjelder også
for det som alt står i kø. Databasen skal ikke kunne noe språk.

SPRÅKET ER MOTTAKERENS DER HUN HAR VALGT, OG INSTALLASJONENS ELLERS. Teksten
her sa tidligere at det var installasjonens og bare det, og det var sant da
den ble skrevet: portalens språkvalg lever i URL-ledd og `localStorage`
(`ui/static/js/i18n.js`), profil-DTO-en fra IdP-en er lukket til tre felt
(`visningsnavn`, `epost`, `epost_verifisert`), og `varselvalg` bar bare
kanalvalget — det fantes ingen serverlagret preferanse å slå opp.

Nå gjør det det. `varselvalg.sprak` (028) settes av flaten når kanalvalget
lagres — brukeren STÅR i språket sitt i det øyeblikket — og
`varsel_klaim_epost` returnerer det per rad, så én kjøring rendrer hver
e-post riktig. At nøkkelen og ikke setningen lagres er nettopp det som gjør
at det også gjelder for det som alt står i kø.

DEN SOM IKKE HAR VALGT, ER IKKE NORSK (Codex P2). Kolonnen og klaimet skrev
først 'nb' i det tilfellet, og da fikk senderen alltid en gyldig verdi og tok
den for et valg — `DISPONIT_VARSEL_SPRAK=en` var virkningsløs for nettopp den
gruppen innstillingen fantes for. Fra 031 er «ikke uttrykt» NULL, og det er
DA installasjonens valg gjelder: `DISPONIT_VARSEL_SPRAK`, med `nb` som
standard. En engelskspråklig installasjon er en env-endring, ikke en
kodeendring.

SMTP-oppsettet kommer fra credentials, aldri fra koden. Eier: WCAGvakts konto
(`send.one.com:587`) brukes til TEST og byttes senere — derfor er avsender og
vert konfigurasjon, så byttet blir en env-endring og ikke en kodeendring.
Testmail kommer altså fra en wcagvakt-adresse; greit for test, ikke for ekte
godkjennere.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
import time
from email.message import EmailMessage
from pathlib import Path

GRENSE = int(os.environ.get("DISPONIT_VARSEL_GRENSE", "50"))
MAKS_FORSOK = int(os.environ.get("DISPONIT_VARSEL_MAKS_FORSOK", "3"))
BACKOFF_MIN = int(os.environ.get("DISPONIT_VARSEL_BACKOFF_MIN", "15"))
#: Sokkeltimeouten for ett SMTP-kall. Navngitt fordi den er en FAKTOR i to
#: andre tall: leasen skal være mye lengre enn den, og fristen under er
#: definert som «rekker en runde til før unitens frist».
SMTP_TIMEOUT_S = int(os.environ.get("DISPONIT_VARSEL_SMTP_TIMEOUT_S", "20"))
#: Hvor lenge KJØRINGEN får vare før den gir seg av seg selv (Codex P2).
#:
#: `GRENSE` alene er ikke en grense i tid: 50 runder à et SMTP-kall med
#: `SMTP_TIMEOUT_S` per sokkeloperasjon kan bruke en god halvtime, mens
#: uniten dreper prosessen etter `TimeoutStartSec`. Da var ikke spørsmålet OM
#: en legitim kjøring ble avbrutt, men HVOR: lander SIGTERM mellom et
#: akseptert SMTP-kall og statusoppdateringen, står raden `under_sending` med
#: et dødt klaim, og leasen sender den samme e-posten en gang til.
#:
#: Senderen stanser derfor seg selv, og den gjør det på det ene punktet i
#: løkka der ingenting er i luften: FØR et nytt klaim. Da er hver rad enten
#: ferdig eller aldri påbegynt, køen står igjen som den skal, og neste
#: timerkjøring fortsetter der denne slapp — køen er tilstanden.
#:
#: Fristen er kortere enn unitens `TimeoutStartSec` med god margin, slik at
#: SIGTERM forblir et sikkerhetsnett som aldri skal utløses. Se
#: `deploy/staging/disponit-varselsender.service` for regnestykket; de to
#: tallene hører sammen og skal endres sammen.
#:
#: Den stanser aldri den FØRSTE raden — se løkka. En frist satt for kort gir
#: korte kjøringer, ikke tomme.
FRIST_S = int(os.environ.get("DISPONIT_VARSEL_FRIST_S", "240"))
#: Hvor lenge et klaim gjelder før en annen kjøring kan ta raden igjen.
#: MYE lengre enn SMTP-timeouten (20 s) med vilje: leasen skal aldri løpe ut
#: for en sending som fortsatt pågår — da ville den gjenskapt dobbeltsendingen
#: klaimet finnes for å hindre.
LEASE_MIN = int(os.environ.get("DISPONIT_VARSEL_LEASE_MIN", "30"))
#: Språket HELE kjøringen rendres i. Se modulteksten: det finnes ingen
#: serverlagret språkpreferanse per mottaker, så dette er installasjonens
#: valg — ikke den enkeltes. `nb` er standarden fordi det er plattformens
#: reservespråk (`locales/nb.json`, `i18n.js`).
SPRAK = os.environ.get("DISPONIT_VARSEL_SPRAK", "nb")


def _locale(sprak: str) -> dict:
    """Tekstene, fra samme `locales/` som flaten bruker.

    Én kilde til tekst — ikke en egen e-postmal som driver fra portalen. Det
    var hele poenget med å lagre nøkkel og ikke setning.

    Roten utledes fra MODULENS EGEN plassering, ikke fra en hardkodet
    driftssti. `locales/` ligger i repoet, og denne filen ligger i det samme
    repoet — så `parents[2]` er svaret både på staging (der utsjekken ER
    `/opt/disponit/aktiv`) og i CI, i en utviklers arbeidskopi og i en
    worktree. Den hardkodede stien var sann bare ett sted, og i CI fantes den
    ikke: senderen kastet `FileNotFoundError` på hver eneste e-post.
    `DISPONIT_REPO` overstyrer fortsatt, som i `policy-rundtur.py`.
    """
    rot = Path(os.environ.get("DISPONIT_REPO")
               or Path(__file__).resolve().parents[2])
    sti = rot / "locales" / f"{sprak}.json"
    if not sti.exists():
        sti = rot / "locales" / "nb.json"
    return json.loads(sti.read_text(encoding="utf-8"))


def rendre(tekster: dict, nokkel: str, parametre: dict) -> str:
    """Nøkkel + parametre → setning. Ukjent nøkkel gir nøkkelen selv.

    En manglende oversettelse skal være SYNLIG, ikke bli til en tom e-post:
    `varsel.attestering_venter` i innboksen er stygt, men det forteller
    sannheten. En tom melding forteller ingenting.
    """
    s = tekster.get(nokkel, nokkel)
    for k, v in (parametre or {}).items():
        s = s.replace("{" + str(k) + "}", str(v))
    return s


def _smtp_oppsett() -> dict | None:
    """Vert, port, bruker, passord og avsender — alt fra miljøet.

    Mangler noe, sender vi ikke. Vi markerer heller ikke radene som feilet:
    et manglende oppsett er en DRIFTSTILSTAND, ikke en egenskap ved varselet,
    og å brenne forsøkstelleren på det ville stille kastet varsler som er helt
    i orden.
    """
    n = {k: os.environ.get(f"DISPONIT_SMTP_{k.upper()}")
         for k in ("vert", "port", "bruker", "passord", "avsender")}
    if not all(n.values()):
        return None
    n["port"] = int(n["port"])
    return n


def _send_ekte(oppsett: dict, til: str, emne: str, tekst: str) -> None:
    m = EmailMessage()
    m["From"] = oppsett["avsender"]
    m["To"] = til
    m["Subject"] = emne
    m.set_content(tekst)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(oppsett["vert"], oppsett["port"],
                      timeout=SMTP_TIMEOUT_S) as s:
        s.starttls(context=ctx)
        s.login(oppsett["bruker"], oppsett["passord"])
        s.send_message(m)


def kjor(conn, *, send=None, oppsett=None, sprak: str | None = None) -> dict:
    """Tøm køen én gang. -> {sendt, feilet, gjenkoet, mistet, stanset}.

    `send` er injiserbar, så testene kan måle HVA som ville blitt sendt uten en
    e-postserver. Standard er ekte SMTP.

    `sprak` er INSTALLASJONENS språk, og gjelder for den mottakeren som ikke
    har uttrykt noe eget. Standarden er `DISPONIT_VARSEL_SPRAK` (`nb`) — ikke
    en hardkodet «nb» i signaturen. Forskjellen er at driften kan velge språk
    uten en kodeendring, og at valget står ett sted i stedet for hos hver
    kaller. Har mottakeren derimot valgt selv, bærer klaimet det valget, og
    det vinner — locale lastes per språk, ikke per kjøring.

    RADEN KLAIMES FØR SMTP, og klaimet COMMITTES før sendingen begynner. Det er
    hele forskjellen på denne løkka og den forrige (Codex P1): før plukket den
    `koet`-rader med en ren SELECT og flyttet statusen etter at e-posten var
    ute. To sendere som overlappet — timeren går hvert 5. minutt, og en treg
    SMTP-server gjør en kjøring lengre enn det — hentet da samme rad og sendte
    begge e-posten. At den andre statusoppdateringen returnerte `false` var en
    opplysning som kom for sent: SMTP er utført, og en sendt e-post kan ingen
    ROLLBACK hente hjem igjen.

    Committen mellom klaim og sending er ikke en detalj: en uncommittet UPDATE
    er usynlig for den andre senderen, og da ville vi vært like langt så snart
    SMTP-kallet tok tid.

    ÉN RAD OM GANGEN (Codex P2). Klaimet hentet først opptil `GRENSE` rader og
    committet HELE bunken til `under_sending` før det første SMTP-kallet. Da
    lå rad nummer femti klaimet mens de 49 foran ble sendt — minutter, ikke
    mikrosekunder — og i det vinduet kunne mottakeren lese varselet, melde seg
    av e-post eller få runden lukket under seg. Ingen av de tre veiene avlyser
    en `under_sending`-rad, og det er med vilje: den raden er i et SMTP-kall,
    og en e-post som er ute kan ikke kalles hjem. Men med et bunkeklaim var
    den premissen usann for alle radene bak den første, og den bufrede løkka
    sendte dem likevel.

    Klaimvinduet er derfor gjort like langt som sendingen det verner: hver
    runde klaimer nøyaktig én rad og sender den med det samme. `GRENSE` er
    fortsatt taket for hvor mange rader én kjøring tar — nå som antall runder,
    ikke som bunkestørrelse — og FIFO-en er den samme, siden klaimet ordner på
    `opprettet`. Prisen er én tur til databasen per e-post, som er ingenting
    ved siden av et SMTP-kall.

    Det er også det som gjør `varsel.I_KO` sant: en avmelding rekker nå alle
    radene som ennå ikke er i luften, fordi ingen rad er klaimet før den skal
    sendes.

    Hver rad står for seg: én adresse som ikke tar imot skal ikke stoppe resten
    av køen.

    KJØRINGEN GIR SEG SELV FØR UNITEN GJØR DET (Codex P2). `GRENSE` var det
    eneste taket, og det er et tak i ANTALL, ikke i tid: 50 runder à et
    SMTP-kall med `SMTP_TIMEOUT_S` per sokkeloperasjon kan bruke langt mer enn
    unitens `TimeoutStartSec`. Da drepte systemd en kjøring som ikke gjorde
    noe galt — og det farlige var ikke avbruddet, men hvor det landet: SIGTERM
    mellom et akseptert SMTP-kall og `_sett` etterlot raden `under_sending`
    med et klaim ingen holder, og leasen løftet den senere tilbake i køen.
    E-posten ble da sendt to ganger, av nøyaktig den grunnen klaimet finnes
    for å utelukke.

    `FRIST_S` sjekkes derfor FØR hvert klaim, som er det ene punktet i løkka
    der ingenting er i luften: hver rad er enten ferdig eller aldri påbegynt.
    Aldri før den første raden, av samme grunn som `max(1, GRENSE)`: en frist
    som er satt for kort skal gi korte kjøringer, ikke tomme.
    Resten av køen blir stående `koet` og tas av neste timerkjøring — køen er
    tilstanden, og en oneshot som stanser er ikke en jobb som mislyktes.
    `stanset` sier hvilken grense som avsluttet runden (`frist`, `grense`
    eller `tom`), for det er forskjellen på «køen er tømt» og «køen er lengre
    enn ett vindu», og bare den ene av dem er verdt å se på.

    `mistet` teller de radene der statusoppdateringen ikke fant klaimet igjen.
    Med en lease som er mye lengre enn SMTP-timeouten skal det aldri skje — og
    nettopp derfor skal det ikke forbli usagt hvis det gjør det: et tapt klaim
    betyr at raden kan bli sendt en gang til, altså at tallene i denne
    beregningen er de eneste stedet det ville vist seg. Før ble returverdien
    kastet uten å bli lest.

    KLAIMET BÆRER ET TOKEN, og fullføringen krever det (Codex P2). `id` +
    `under_sending` skiller ikke to klaim fra hverandre, og etter en
    lease-gjenopptaking er det nettopp to: pauses denne kjøringen forbi
    leasen, rekøes raden og klaimes av en annen sender, står den
    `under_sending` igjen når vi våkner. Uten tokenet traff vår `sendt` da den
    ANDRES levende klaim, og den senderen — som faktisk holdt på å sende —
    hadde ikke lenger noe sted å skrive resultatet sitt. Med tokenet blir vår
    fullføring `false`, raden telles som `mistet`, og advarselen står i
    journalen der driften ser den.
    """
    # PRE-PASS (035 §5): familiehorisont-varslene (30/7/1 døgn) skrives inn
    # i køen FØR den tømmes — sveipen er idempotent (unikhetsnøkkelen per
    # bruker·familie·terskel), skjermet (en feil her skal aldri stoppe
    # sendingen av det som alt ligger i køen), og kjøres her fordi senderen
    # er den ene timerdrevne prosessen som allerede eier varselkøens rytme.
    # `DISPONIT_PLATTFORMTENANT` er tenanten hvis `admin`-medlemmer driver
    # plattformen (v1-standard: disponit).
    plattformtenant = os.environ.get("DISPONIT_PLATTFORMTENANT", "disponit")
    # 090 (M-10) og 091 (M-11) la til to sveiper til, og de hører hjemme
    # NØYAKTIG her av 035s grunn: senderen er den ene timerdrevne
    # prosessen som allerede eier varselkøens rytme, og begge de nye
    # tilstandene er TAUSHET — noe som IKKE har skjedd. En taushet kan
    # per definisjon ikke varsle om seg selv:
    #
    #   `varsle_backupverifisering_uteblitt` (30 t) fordi lesejobben ikke
    #   kjører når det ikke finnes noe å lese, og
    #   `varsle_selvtest_uteblitt` (3 t) fordi selvtesten ikke kan
    #   rapportere sin egen død — den må observeres utenfra, av en
    #   prosess med en annen rolle på en annen kadens.
    #
    # HVER SVEIP HAR SIN EGEN SKJERMEDE BLOKK, ikke én felles: en feil i
    # den ene skal verken stoppe den andre eller sendingen av det som alt
    # ligger i køen. Alle tre er idempotente per døgn på
    # `varsel_en_per_hendelse`, så en gjentatt kjøring køer ingenting nytt.
    # SQL-en er tre HELE literaler, ikke ett navn satt inn i en mal:
    # funksjonsnavn kan ikke parameteriseres, og en f-streng her ville
    # vært en strenginterpolasjon inn i SQL — riktig i dag, og en felle
    # den dagen noen gjør listen konfigurerbar.
    for setning, hva in (
            ("SELECT varsle_tokenfamilie_utlop(%s)", "familievarsel"),
            ("SELECT varsle_backupverifisering_uteblitt(%s)", "backup"),
            ("SELECT varsle_selvtest_uteblitt(%s)", "selvtest")):
        try:
            conn.execute(setning, (plattformtenant,))
            conn.commit()
        except Exception as e:                                # noqa: BLE001
            conn.rollback()
            print(f"varselsender: {hva}-sveipen feilet: "
                  f"{type(e).__name__}: {e}", file=sys.stderr)

    oppsett = oppsett or _smtp_oppsett()
    if oppsett is None and send is None:
        # Ikke konfigurert. Si det tydelig og la køen ligge urørt.
        return {"sendt": 0, "feilet": 0, "gjenkoet": 0, "mistet": 0,
                "grunn": "smtp_ikke_konfigurert"}
    send = send or (lambda til, emne, tekst: _send_ekte(oppsett, til, emne,
                                                        tekst))
    # SPRÅKET ER PER RAD, ikke per kjøring (Codex P2). Designet lover at
    # varselet leses på MOTTAKERENS språk — det var derfor raden bærer nøkkel
    # og parametre i stedet for ferdig tekst. Å rendre hele kjøringen med ETT
    # språk brøt løftet i nettopp den kanalen mottakeren ikke kan bytte språk
    # i selv. Klaimet returnerer språket fra `varselvalg`, og NULL når
    # brukeren ikke har uttrykt noe (migrasjon 031) — da, og bare da, gjelder
    # installasjonens valg under. Ordboka lastes én gang per språk per
    # kjøring.
    tekstcache: dict = {}

    def _tekster(radsprak):
        radsprak = radsprak if radsprak in ("nb", "en") else (sprak or SPRAK)
        if radsprak not in tekstcache:
            tekstcache[radsprak] = _locale(radsprak)
        return tekstcache[radsprak]
    # Først: tilbake i køen med det som ikke kom frem — feilede rader som har
    # ventet ut backoffen, og klaim fra en kjøring som døde underveis. Egen
    # SQL-funksjon, ikke et utvidet klaim: `koet` forblir den eneste tilstanden
    # et klaim kan ta fra.
    gjenkoet = conn.execute(
        "SELECT varsel_rekoe(%s * interval '1 minute', %s,"
        "                    %s * interval '1 minute')",
        (BACKOFF_MIN, MAKS_FORSOK, LEASE_MIN)).fetchone()[0]
    conn.commit()
    sendt = feilet = mistet = 0

    def _sett(vid, klaim, status, feil=None):
        # Tokenet er med, ikke bare id-en: fullføringen gjelder DETTE klaimet.
        # Kom raden tilbake gjennom leasen og ble klaimet på nytt mens vi sto i
        # SMTP-kallet, finner vi ikke vårt eget klaim igjen — og skal ikke
        # skrive over den andres levende sending.
        beholdt = conn.execute(
            "SELECT varsel_sett_epoststatus(%s,%s,%s,%s)",
            (vid, klaim, status, feil)).fetchone()[0]
        conn.commit()
        return beholdt

    start = time.monotonic()
    stanset = "grense"
    for runde in range(max(1, GRENSE)):
        # FRISTEN SPØRRES FØR KLAIMET, aldri etter. Her er ingenting i luften:
        # forrige rad er ferdig skrevet, neste er ennå ikke tatt ut av køen.
        # Stanser vi et annet sted — og det er der systemd ville stanset oss —
        # kan en rad stå `under_sending` med et dødt klaim, og leasen sender
        # den samme e-posten en gang til.
        #
        # Men aldri før den FØRSTE raden, samme garanti som `max(1, GRENSE)`
        # gir: en frist som er satt for kort skal gjøre kjøringene korte, ikke
        # tomme. Ellers ville en feilkonfigurert `FRIST_S` stanset køen helt,
        # og stille — hver kjøring hadde returnert null uten å ha prøvd noe.
        if runde and time.monotonic() - start >= FRIST_S:
            stanset = "frist"
            break
        # Klaimet: `koet` → `under_sending` i samme setning som leser raden.
        # ÉN rad, og den sendes med det samme: vinduet der raden er tatt ut av
        # køen skal ikke være lengre enn sendingen det verner.
        rad = conn.execute("SELECT * FROM varsel_klaim_epost(%s,%s)",
                           (1, MAKS_FORSOK)).fetchone()
        conn.commit()      # …og ut av alle andres kø FØR SMTP-kallet.
        if rad is None:
            stanset = "tom"
            break          # køen er tom — ingenting mer å hente denne runden.
        vid, _tenant, epost, nokkel, parametre, forsok, klaim, radsprak \
            = rad
        tekster = _tekster(radsprak)
        emne = tekster.get("varsel.epost.emne", "Disponit")
        try:
            send(epost, emne, rendre(tekster, nokkel, parametre))
        except Exception as e:                                # noqa: BLE001
            beholdt = _sett(vid, klaim, "feilet",
                            f"forsøk {forsok}/{MAKS_FORSOK}: "
                            f"{type(e).__name__}: {e}")
            feilet += 1
        else:
            beholdt = _sett(vid, klaim, "sendt")
            sendt += 1
        if not beholdt:
            # Klaimet var ikke vårt lenger. Skal ikke kunne skje med en lease
            # som er mye lengre enn SMTP-timeouten — og da er det nettopp det
            # som gjør det verdt å si fra om: raden kan bli sendt en gang til.
            mistet += 1
            print(f"varselsender: ADVARSEL mistet klaim på varsel {vid}",
                  file=sys.stderr)
    if stanset != "tom":
        # Køen er lengre enn ett vindu. Ikke en feil — neste timerkjøring tar
        # resten — men den ene linjen driften leser skal si det, ellers ser en
        # kø som vokser fortere enn den tømmes nøyaktig ut som en som tømmes.
        print(f"varselsender: køen ikke tømt, stanset på {stanset}",
              file=sys.stderr)
    return {"sendt": sendt, "feilet": feilet, "gjenkoet": gjenkoet,
            "mistet": mistet, "stanset": stanset}
