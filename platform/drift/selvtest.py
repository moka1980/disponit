"""M-11 (091) — selvtestrunden: plattformen måler seg selv, hver time.

Dommen natt til 1/9: dette er en DRIFTSTIMER, aldri en bestillingsplan.
Selvtesten må overleve pasienten. En selvtest som kjørte gjennom
planmotoren ville vært stum nøyaktig når planmotoren står — altså i den
ene tilstanden den finnes for å oppdage. Og den kan ikke varsle om sin
egen død: `varsle_selvtest_uteblitt` bor derfor i varselsenderen, som er
en annen prosess med en annen rolle på en annen kadens.

KUN LESENDE, OG DET ER EN SIKKERHETSINVARIANT, IKKE EN AMBISJON. Modulen
har ingen skrivende HTTP-metode, ingen kommando som endrer en enhet, og
ingen rettighetsheving — `test_m11_selvtest` måler kildeteksten her mot
den regelen, og finner den ett av de forbudte ordene, er porten rød uten
å bry seg om hva som var ment. Grunnen er enkel: en selvtest som kan
endre systemet er et angrepsverktøy med legitim planlagt kjøring, høye
fullmakter og ingen menneskelig godkjenning i sløyfa.

TRE STATUSER, OG DEN TREDJE ER IKKE ET MILDERE RØDT:
  `gronn`             proben målte, og det den målte var i orden
  `rod`               proben målte, og det den målte var feil
  `ikke_konfigurert`  det proben måler finnes ikke på denne verten
`ikke_konfigurert` varsles ALDRI (dommen). En vert uten modellserver er
ikke en vert med en nede modellserver, og et varsel som ikke lar seg
gjøre noe med er et varsel folk lærer seg å overse. En DELVIS
konfigurasjon er derimot `rod`: fem oppsettsnavn der tre finnes er en
feil noen har gjort, ikke et fravær noen har valgt.

HEMMELIGHETER FORLATER ALDRI PROBEN (kanariporten, sikkerhetsinvariant
m11-v1). `smtp_oppsett` avgjør hvorvidt de fem navnene STÅR i
oppsettsfilen, og leser aldri hva som står etter likhetstegnet. Ingen
probe legger en miljøverdi i `maalt`, i et varselparameter eller på
stdout. Porten kjører en hel runde med en kanarisstreng plantet både i
miljøet og i oppsettsfilen, og krever at den ikke finnes igjen noe sted.
Det er en billig test av en dyr feil: rapporten er tenkt LEST av
mennesker og lagret i en tabell flaten viser.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field

#: De tre lovlige statusene. Samme lukkede sett som CHECK-en i 091 —
#: definert her fordi dommen over trenger dem, håndhevet i basen.
GRONN = "gronn"
ROD = "rod"
UKONFIGURERT = "ikke_konfigurert"

#: API-ets Unix-sokkel, samme som `helse-sjekk.sh` bruker.
SOKKEL = "/run/disponit/api.sock"
#: De fem navnene varselsenderen krever i oppsettet (`_smtp_oppsett`).
#: NAVNENE, aldri verdiene.
SMTP_NAVN = ("DISPONIT_SMTP_VERT", "DISPONIT_SMTP_PORT",
             "DISPONIT_SMTP_BRUKER", "DISPONIT_SMTP_PASSORD",
             "DISPONIT_SMTP_AVSENDER")
SMTP_FIL = "/etc/disponit/varsel/smtp.env"
#: Enhetsfilene slik `opp.sh` installerer dem.
ENHETSKATALOG = "/etc/systemd/system"
#: Timerne runden dømmer om. Navnene er unit-navn uten `.timer`.
TIMERE = ("disponit-backup", "disponit-varselsender", "disponit-helse",
          "disponit-evidensreaper", "disponit-artefaktrydding",
          "disponit-domenerevalidering", "disponit-domeneverifisering",
          "disponit-rydd-pending", "disponit-plan")
#: Hvor mange kadenser en timer får ligge stille før den er rød. Tre er
#: samme forhold som helsesjekkens heartbeat-grense (90 s mot 30 s
#: syklustid): to gir falske utslag på jitter og en treg kjøring, fire
#: gjør en død timer usynlig for lenge.
KADENSFAKTOR = 3
#: Sekundtak per probe. Runden skal aldri kunne bli lengre enn kadensen
#: sin, og en probe som henger er selv en observasjon — den blir rød.
TIDSGRENSE_S = 5

#: `systemd.time`-suffiksene enhetsfilene faktisk bruker. Vi tolker ikke
#: hele grammatikken: en kadens vi ikke KAN lese blir `ikke_konfigurert`,
#: aldri et gjettet tall. En terskel regnet av en feiltolket kadens er
#: verre enn ingen terskel — den ser like autoritativ ut.
_TIDSENHET = {"": 1, "s": 1, "sec": 1, "secs": 1, "second": 1,
              "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60,
              "minutes": 60, "h": 3600, "hr": 3600, "hour": 3600,
              "hours": 3600, "d": 86400, "day": 86400, "days": 86400}
_TIDSLEDD = re.compile(r"(\d+)\s*([a-z]*)")


@dataclass
class Probe:
    status: str
    #: Tallene og flaggene proben SELV velger ut. Aldri rå kommandoutdata,
    #: aldri en miljøverdi, aldri innholdet i en oppsettsfil.
    maalt: dict = field(default_factory=dict)

    def som_json(self) -> dict:
        return {"status": self.status, "maalt": self.maalt}


def tolk_tidsspenn(tekst: str) -> int | None:
    """`systemd.time`-spenn → sekunder, eller `None` når vi ikke kan lese det.

    `5min`, `1h`, `60`, `1h 30min`. Et ledd med ukjent suffiks gjør HELE
    spennet uleselig — å hoppe over det ville gitt et for lite tall, og
    et for lite tall her er en terskel som slår ut på friske timere.
    """
    tekst = (tekst or "").strip().lower()
    if not tekst:
        return None
    sum_s = 0
    treff = 0
    for m in _TIDSLEDD.finditer(tekst):
        faktor = _TIDSENHET.get(m.group(2))
        if faktor is None:
            return None
        sum_s += int(m.group(1)) * faktor
        treff += 1
    if not treff or re.sub(r"[\d\sa-z]", "", tekst):
        return None
    return sum_s


def kadens_fra_enhetsfil(sti: str) -> int | None:
    """Timerens kadens i sekunder, lest av enhetsfilen — eller `None`.

    `OnUnitActiveSec` er kadensen der den står. Ellers tas `OnCalendar`,
    men KUN den ene formen vi kan lese uten en kalendermotor: et daglig
    `*-*-* HH:MM:SS`. Alt annet er `None`, og proben blir
    `ikke_konfigurert` i stedet for å bli målt mot et gjettet tall.
    """
    try:
        with open(sti, encoding="utf-8") as f:
            linjer = f.read().splitlines()
    except OSError:
        return None
    kalender = None
    for linje in linjer:
        linje = linje.strip()
        if linje.startswith("#") or "=" not in linje:
            continue
        nokkel, _, verdi = linje.partition("=")
        nokkel = nokkel.strip()
        if nokkel == "OnUnitActiveSec":
            s = tolk_tidsspenn(verdi)
            if s:
                return s
        elif nokkel == "OnCalendar":
            kalender = verdi.strip()
    if kalender and re.match(r"^\*-\*-\*\s+\d{2}:\d{2}(:\d{2})?\b", kalender):
        return 86400
    return None


# ---------------------------------------------------------------------------
# Probene. Hver returnerer en `Probe` og kaster aldri — en probe som
# eksploderer ville tatt hele runden med seg, og da hadde vi mistet de
# ni andre målingene fordi den tiende var uheldig.
# ---------------------------------------------------------------------------

def _hent_over_sokkel(sti: str, sokkel: str = SOKKEL) -> tuple[bool, dict]:
    """Hent én URL over API-ets Unix-sokkel. -> (ok, maalt).

    `curl` med `--unix-socket`, nøyaktig som `helse-sjekk.sh` — samme
    verktøy, samme sokkel, samme standardmetode (henting). Argumentene er
    en fast liste, aldri en kommandolinje satt sammen av tekst: det finnes
    ikke noe sted i denne prosessen der et skall tolker en streng.
    """
    try:
        r = subprocess.run(
            ["curl", "-fsS", "--max-time", str(TIDSGRENSE_S),
             "--unix-socket", sokkel, f"http://disponit{sti}"],
            capture_output=True, timeout=TIDSGRENSE_S + 2)
    except FileNotFoundError:
        return False, {"grunn": "curl_mangler"}
    except subprocess.TimeoutExpired:
        return False, {"grunn": "tidsavbrudd"}
    except OSError as e:
        return False, {"grunn": "kall_feilet", "feiltype": type(e).__name__}
    # SVARKROPPEN LOGGES ALDRI. Den kan bære driftsdetaljer, og proben
    # trenger bare å vite OM den kom. Exitkoden er hele målingen.
    return r.returncode == 0, {"exitkode": r.returncode}


def probe_api_live(sokkel: str = SOKKEL) -> Probe:
    """Svarer hendelsesløkka? Samme spørsmål som helsesjekkens `/live`."""
    ok, maalt = _hent_over_sokkel("/live", sokkel)
    return Probe(GRONN if ok else ROD, maalt)


def probe_api_ready(sokkel: str = SOKKEL) -> Probe:
    """Er API-et klart — inkludert basen bak det?

    Helsesjekken spør bevisst ALDRI om dette: en `/ready` som feiler på en
    DB-hikke ville gitt en omstartsstorm. Her er det motsatt riktig.
    Selvtesten omstarter ingenting; den observerer, og «API-et lever, men
    kommer ikke til basen» er nøyaktig den forskjellen et menneske trenger
    å se. Å måle begge er hele grunnen til at det er to prober.
    """
    ok, maalt = _hent_over_sokkel("/ready", sokkel)
    return Probe(GRONN if ok else ROD, maalt)


def probe_db_drift(conn) -> Probe:
    """Er selvtestens EGEN forbindelse i live?

    `SELECT 1` på jobbens egen forbindelse, ikke på runtimes: proben
    svarer på om DENNE rollen kommer til basen, og det er det eneste
    spørsmålet den har rett til å stille. Per-rolle-sondering av andre
    rollers tilgang står bevisst utenfor v1 (dommen) — den ville krevd
    flere legitimasjoner i én prosess, altså et bredere angrepsmål enn
    målingen er verdt.
    """
    try:
        rad = conn.execute("SELECT 1").fetchone()
    except Exception as e:                                    # noqa: BLE001
        # TILBAKERULLINGEN ER IKKE HØFLIGHET (CodeRabbit, major).
        # `kjor` bruker DENNE forbindelsen til `registrer_selvtest`
        # etterpå. En feilet setning lar transaksjonen stå ABORTED, og
        # da feiler hver eneste senere setning med
        # `InFailedSqlTransaction` — inkludert skrivingen av runden.
        # Resultatet ville vært at en rød `db_drift` gjorde HELE runden
        # uregistrerbar, altså at selvtesten mistet stemmen sin nøyaktig
        # i den tilstanden den finnes for å rapportere. Rullingen selv er
        # skjermet: er forbindelsen borte, er proben rød uansett, og en
        # feil her skal ikke ta de tretten andre målingene med seg.
        try:
            conn.rollback()
        except Exception:                                     # noqa: BLE001
            pass
        return Probe(ROD, {"grunn": "spoerring_feilet",
                           "feiltype": type(e).__name__})
    return Probe(GRONN if rad and rad[0] == 1 else ROD, {})


def _smtp_navn_i_fil(sti: str) -> set[str] | None:
    """Navnene som STÅR i oppsettsfilen, eller `None` om den ikke kan leses.

    Linjen splittes på det første likhetstegnet og alt til høyre kastes
    umiddelbart — verdien blir aldri bundet til en variabel, aldri lagt i
    `maalt`, aldri returnert. Returtypen er et sett av navn fra den
    LUKKEDE konstanten `SMTP_NAVN`, så resultatet kan per konstruksjon
    ikke bære noe som helst fra filens innhold.
    """
    try:
        with open(sti, encoding="utf-8", errors="replace") as f:
            raa = f.read()
    except OSError:
        # Både «finnes ikke» og «får ikke lese» — kalleren skiller dem.
        return None
    funnet = set()
    for linje in raa.splitlines():
        linje = linje.strip()
        if linje.startswith("#") or "=" not in linje:
            continue
        navn = linje.partition("=")[0].strip()
        if navn.startswith("export "):
            navn = navn[len("export "):].strip()
        if navn in SMTP_NAVN:
            funnet.add(navn)
    return funnet


def probe_smtp_oppsett(sti: str = SMTP_FIL) -> Probe:
    """Er de fem oppsettsnavnene satt for varselsenderen?

    NAVNENE, ALDRI VERDIENE (kanariporten).

    TO KILDER, I DENNE REKKEFØLGEN, og grunnen er en permisjonsfelle som
    ville gjort proben PERMANENT RØD i drift:

      1. Oppsettsfilen, når den kan leses. Det er den bokstavelige
         kilden, og den gjelder i root-kontekster og i testene.
      2. MILJØET, når filen finnes men ikke kan leses. I drift er
         `/etc/disponit/varsel/` `0700 root:root` — med vilje, det er
         hemmeligheter — mens selvtesten kjører som `disponit-helse`.
         Proben ville derfor meldt `fil_uleselig` hver eneste time, for
         alltid, og en probe som alltid er rød er en probe folk skrur av.
         Uniten setter derfor `EnvironmentFile=-` på nøyaktig den samme
         filen (som `disponit-varselsender.service` gjør): systemd leser
         den som root og injiserer navnene i prosessens miljø.

    Miljøveien måler DET SENDEREN VIL SE, ikke en fil senderen kanskje
    ikke bruker — den leser samme fil gjennom samme mekanisme. Og
    invarianten er uendret: vi spør `navn in os.environ`, aldri hva
    verdien er. Kanariporten planter nettopp en hemmelighet i MILJØET og
    krever at den ikke finnes igjen noe sted.

    Ingen av kildene har noen av navnene → `ikke_konfigurert`: e-post er
    valgfritt (`EnvironmentFile=-` i senderens enhet), og en installasjon
    uten SMTP er et valg, ikke en feil. Noen navn, men ikke alle → `rod`:
    da har noen ment å sette det opp, og senderen vil rapportere
    `smtp_ikke_konfigurert` i det stille hvert femte minutt. Det er
    nøyaktig feilen denne proben finnes for.
    """
    funnet = _smtp_navn_i_fil(sti)
    kilde = "fil"
    if funnet is None:
        # Filen finnes, men vi får ikke lese den (drift), eller den finnes
        # ikke i det hele tatt. Miljøet er da den eneste kilden — og den
        # er autoritativ nettopp fordi systemd fylte den fra samme fil.
        kilde = "miljo"
        funnet = {n for n in SMTP_NAVN if os.environ.get(n)}
    mangler = [n for n in SMTP_NAVN if n not in funnet]
    if len(mangler) == len(SMTP_NAVN):
        return Probe(UKONFIGURERT, {"grunn": "ingen_navn_satt",
                                    "kilde": kilde})
    # `mangler` er en liste over NAVN fra den lukkede konstanten over —
    # den kan per konstruksjon ikke bære noe fra filen eller fra miljøet.
    maalt = {"funnet": len(funnet), "kreves": len(SMTP_NAVN),
             "kilde": kilde}
    if mangler:
        maalt["mangler"] = mangler
        return Probe(ROD, maalt)
    return Probe(GRONN, maalt)


def probe_ollama(url: str | None = None, navn: str | None = None) -> Probe:
    """Svarer modellserveren, og kjenner den modellen M-57 er bundet til?

    Henter `/api/tags` og ser etter modellnavnet. Uten URL eller navn i
    miljøet er verten uten modellserver — `ikke_konfigurert`, ikke rødt:
    de fleste installasjoner kjører aldri M-57.
    """
    url = (url if url is not None
           else os.environ.get("DISPONIT_M57_MODELL_URL", "")).strip()
    navn = (navn if navn is not None
            else os.environ.get("DISPONIT_M57_MODELLNAVN", "")).strip()
    if not url or not navn:
        return Probe(UKONFIGURERT, {"grunn": "ikke_satt"})
    if not url.startswith(("http://", "https://")):
        return Probe(ROD, {"grunn": "ugyldig_url"})
    try:
        with urllib.request.urlopen(  # noqa: S310 — skjemaet er sjekket over
                url.rstrip("/") + "/api/tags",
                timeout=TIDSGRENSE_S) as svar:
            kropp = json.loads(svar.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return Probe(ROD, {"grunn": "http_feil", "statuskode": e.code})
    except (urllib.error.URLError, OSError) as e:
        return Probe(ROD, {"grunn": "ikke_naadd",
                           "feiltype": type(e).__name__})
    except (ValueError, UnicodeDecodeError):
        return Probe(ROD, {"grunn": "ugyldig_svar"})
    modeller = kropp.get("models") if isinstance(kropp, dict) else None
    if not isinstance(modeller, list):
        return Probe(ROD, {"grunn": "ugyldig_svar"})
    # Ollama svarer med `navn:tag`; M-57 kan være bundet til begge former.
    navnene = {m.get("name") for m in modeller if isinstance(m, dict)}
    finnes = navn in navnene or any(
        isinstance(n, str) and n.split(":")[0] == navn.split(":")[0]
        for n in navnene)
    # Modellnavnene er IKKE hemmeligheter, men de er heller ikke målingen:
    # antallet og treffet er det proben så, og en liste over hva verten
    # har liggende hører ikke hjemme i en driftsrapport flaten viser.
    return Probe(GRONN if finnes else ROD,
                 {"modeller": len(modeller), "modell_funnet": finnes})


def _enhetsfelt(unit: str, felter: tuple[str, ...]) -> dict | None:
    """`systemctl show` for én enhet. -> {felt: verdi} eller `None`.

    REN AVLESNING. `show` endrer ingenting, krever ingen fullmakt utover
    å kunne snakke med systemd, og er den eneste systemd-kommandoen denne
    modulen kjenner. Argumentene er en fast liste; unit-navnet kommer fra
    konstanten `TIMERE`, aldri fra inndata.
    """
    # `--timestamp=unix` er ikke pynt. UTEN den formaterer systemd
    # `LastTriggerUSec` som en LESBAR DATO («Mon 2026-08-31 00:51:16 UTC»),
    # ikke som et tall — og en probe som forsøkte å lese den som
    # mikrosekunder ville meldt `tidsstempel_uleselig` for HVER timer,
    # for alltid. Hele timerarmen ville stått stille i en status som ser
    # ut som et ærlig fravær. Med flagget kommer verdien som `@<sekunder>`.
    argumenter = ["systemctl", "show", "--timestamp=unix"]
    for f in felter:
        argumenter += ["-p", f]
    argumenter.append(unit)
    try:
        r = subprocess.run(argumenter, capture_output=True, text=True,
                           timeout=TIDSGRENSE_S)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if r.returncode != 0:
        return None
    ut = {}
    for linje in r.stdout.splitlines():
        nokkel, _, verdi = linje.partition("=")
        ut[nokkel.strip()] = verdi.strip()
    return ut


def probe_timer(navn: str, *, katalog: str = ENHETSKATALOG,
                naa_s: int | None = None) -> Probe:
    """Har `<navn>.timer` utløst innenfor tre kadenser, og gikk det bra?

    Tre spørsmål, ett svar:
      * er timeren i det hele tatt aktiv,
      * hvor lenge siden utløste den sist (mot 3 × kadensen i
        enhetsfilen), og
      * hvordan gikk tjenesten den utløste (`Result`).
    Ett rødt av tre gjør proben rød. Enhetsfilen finnes ikke → denne
    verten kjører ikke den timeren, og det er `ikke_konfigurert`.

    ALDRI UTLØST er RØDT, ikke fravær. Alle disse timerne er
    `Persistent=true` og utløser ved oppstart; har ingen av dem gjort det,
    er noe galt med installasjonen — og det er nettopp den tilstanden en
    fersk vert trenger å se, ikke skjule bak et mildere ord.
    """
    sti = os.path.join(katalog, f"{navn}.timer")
    if not os.path.exists(sti):
        return Probe(UKONFIGURERT, {"grunn": "enhetsfil_mangler"})
    kadens = kadens_fra_enhetsfil(sti)
    if kadens is None:
        return Probe(UKONFIGURERT, {"grunn": "kadens_uleselig"})
    terskel = kadens * KADENSFAKTOR
    timer = _enhetsfelt(f"{navn}.timer",
                        ("LastTriggerUSec", "ActiveState", "Result"))
    if timer is None:
        return Probe(UKONFIGURERT, {"grunn": "systemd_utilgjengelig"})
    tjeneste = _enhetsfelt(f"{navn}.service", ("Result",)) or {}
    maalt: dict = {"kadens_s": kadens, "terskel_s": terskel,
                   "timer_tilstand": timer.get("ActiveState", "ukjent")}
    if tjeneste.get("Result"):
        maalt["tjeneste_resultat"] = tjeneste["Result"]
    # `@<sekunder>` fra `--timestamp=unix`. En timer som aldri har utløst
    # gir en tom verdi eller `n/a`.
    raa = timer.get("LastTriggerUSec", "").strip()
    if not raa or raa in ("0", "@0", "n/a"):
        maalt["grunn"] = "aldri_utloest"
        return Probe(ROD, maalt)
    try:
        sist_s = int(raa.lstrip("@"))
    except ValueError:
        # En systemd som ikke kjenner `--timestamp=unix` gir fortsatt den
        # formaterte datoen. Vi TOLKER den ikke: en feiltolket klokke er
        # verre enn en ærlig «vet ikke», fordi den ser like autoritativ ut.
        maalt["grunn"] = "tidsstempel_uleselig"
        return Probe(UKONFIGURERT, maalt)
    if naa_s is None:
        naa_s = int(time.time())
    alder_s = max(0, naa_s - sist_s)
    maalt["alder_s"] = alder_s
    if maalt["timer_tilstand"] != "active":
        maalt["grunn"] = "timer_ikke_aktiv"
        return Probe(ROD, maalt)
    if alder_s > terskel:
        maalt["grunn"] = "for_lenge_siden"
        return Probe(ROD, maalt)
    if maalt.get("tjeneste_resultat", "success") != "success":
        maalt["grunn"] = "tjenesten_feilet"
        return Probe(ROD, maalt)
    return Probe(GRONN, maalt)


def samle(conn, *, sokkel: str = SOKKEL, smtp_sti: str = SMTP_FIL,
          enhetskatalog: str = ENHETSKATALOG) -> dict:
    """Kjør alle probene én gang. -> {probenavn: {status, maalt}}.

    Rekkefølgen er fast og alfabetisk der den ikke er tematisk, slik at to
    runder er sammenlignbare linje for linje i journalen.
    """
    prober: dict[str, dict] = {
        "api_live": probe_api_live(sokkel).som_json(),
        "api_ready": probe_api_ready(sokkel).som_json(),
        "db_drift": probe_db_drift(conn).som_json(),
        "smtp_oppsett": probe_smtp_oppsett(smtp_sti).som_json(),
        "ollama": probe_ollama().som_json(),
    }
    for navn in TIMERE:
        prober[f"timer_{navn}"] = probe_timer(
            navn, katalog=enhetskatalog).som_json()
    return prober


def kjor(conn, *, kjoring_id: str | None = None, tenant: str | None = None,
         sokkel: str = SOKKEL, smtp_sti: str = SMTP_FIL,
         enhetskatalog: str = ENHETSKATALOG) -> dict:
    """Én selvtestrunde: mål, og skriv runden i ÉN transaksjon.

    Skrivedøren `registrer_selvtest` feller samlet-dommen selv og køer
    varselet for hver RØD probe i den samme transaksjonen som kjøringen
    skrives. En rød probe uten varsel i køen er derfor urepresenterbar —
    ikke fordi denne funksjonen er nøye, men fordi det ikke finnes en
    kodevei som gir det utfallet.

    Målingen skjer FØR transaksjonen åpnes. Probene bruker sekunder;
    holdt vi en transaksjon åpen gjennom dem, ville selvtesten selv vært
    den lengstlevende transaksjonen i basen hver time.
    """
    kjoring_id = kjoring_id or str(uuid.uuid4())
    tenant = tenant or os.environ.get("DISPONIT_PLATTFORMTENANT", "disponit")
    prober = samle(conn, sokkel=sokkel, smtp_sti=smtp_sti,
                   enhetskatalog=enhetskatalog)
    ny = conn.execute("SELECT registrer_selvtest(%s,%s,%s)",
                      (kjoring_id, json.dumps(prober), tenant)).fetchone()[0]
    conn.commit()
    return {"kjoring_id": kjoring_id, "ny": int(ny), "prober": prober}
