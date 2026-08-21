#!/usr/bin/env python3
"""Staging-sjekklisterunden for m_wcag_audit (PR-014c NESTE, klarsignal §12).

Kjøres PÅ staging-verten (disponit-srv), som root/sudo, ETTER at
branch-releasen er deployet. Runden MÅLER akseptpunktene i manifestets
staging_sjekkliste med EKTE motor (axe i browser-containeren) mot det
syntetiske testnettstedet med kjent fasit — og skriver hver måling som én
JSON-linje til evidensfila. Manifestet oppdateres aldri her: målte
verdier føres inn for hånd i egen commit, med denne fila som kilde.

    sudo -E python3 deploy/staging/wcag-staging-sjekkliste.py \
        --evidens /root/wcag-runde/evidens.jsonl [--fase N] [--motor CMD]

IDEMPOTENSEN ER EKTE, IKKE EN PÅSTAND (Codex P2): hver bestilling får en
nøkkel avledet av rundens id (WCAG_RUNDE_ID, ellers `RUNDE/runde-id.json`)
og bestillingskroppen — se `_idem`. En gjenkjøring av samme fase i samme
runde REPLAYER derfor beslutningene i stedet for å ta nye. Det er ikke
kosmetikk: frekvensgrensen er 12/dag per `mal_url`, og en full runde
bruker nøyaktig 12 på `/index.html` (10 i fase 5, 1 i fase 6, 1 i fase
7). Med nøkler som endret seg per forsøk ga en gjenkjøring
`frekvensgrense_naadd` der fasiten krever 10/10 grønne.

En NY runde krever en ny id: sett WCAG_RUNDE_ID, tøm rundekatalogen,
eller kjør med en ny `WCAG_RELEASE` — identiteten er bundet til releasen
(Codex P1), for en ny release er en ny runde og skal måles, ikke
replayes. Motsatt vei ville replay vært verre enn en rød måling: den ser
grønn ut. En ny runde treffer taket uansett om den kjøres innen ett døgn
etter den forrige. Da er det grensen som virker, ikke en feil, og
gjenopprettingen må vente til det rullende vinduet har flyttet seg.

Faser (idempotente; --fase kjører én, default alle i rekkefølge):
  1 forutsetninger   nøkkelregister, docker-image, testnettsted m/ TLS
  2 registrering     kontrakt/release/typer via de herdede funksjonene +
                     modulstatus aktiv + claiming-deployment (staging)
  3 tenantoppsett    fasit-tenant, aktiveringsformet policy (vilkår +
                     frekvens 12/dag), domenekontroll-seed, admin-økt
  4 onboarding       ops-token → engangshemmelighet → modultoken (HTTP)
  5 fasitmaling      10× bestilling→claim→motor→rapport→signert
                     kvittering innen frist; fasitkontroll per kjøring
  6 porter           18/19 (rapportens ærlighet), 20 (robots + 5xx),
                     21 (frekvens, egen tenant), 24 (motor uten
                     credentials)
  7 feilinjisering   motorfeil → avbrutt uten artefakt; evidensfrist →
                     reaper → M-37-sak (port 22/23)
  8 rollback         forrige release opp og tilbake (uten migrasjonsdelta)
  9 idriftsettelse   arbeideren (disponit-wcag-audit.service) provisjoneres
                     og enables — KUN når ingen måling ble rød

VIKTIG OG SYNLIG: fase 2 registrerer modulen og setter den AKTIV på
denne basen — det åpner /v1/bestilling for typen her. Det er hele
poenget med runden, og basen er staging.

DØREN OG ARBEIDEREN HENGER SAMMEN (Codex P1). Fra fase 2 kan ekte
oppdrag legges i køen, men fasene 5–7 claimer synkront med rundens EGEN
prosess. Uten fase 9 stod køen derfor uten arbeider når runden var ute,
og ekte oppdrag ville ligget `opprettet` til utførelsesfristen løp ut.
Fase 1 stopper arbeideren fra en tidligere runde av samme grunn, motsatt
vei: en pollende unit stjeler oppdragene målingene skal måle.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmaclib
import json
import os
import re
import secrets
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(os.environ.get("DISPONIT_REPO",
                           Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

MODUL = "m_wcag_audit"
OPPDRAGSTYPE = "kontroll.wcag.nettsted"
# Overstyrbar fordi nødstoppens vei tilbake KREVER en ny release-id
# (`bytt_release` nekter å reclaime en drenert deployment, og onboarding
# krever at releasen er claiming) — uten override kunne runneren åpne
# døren én gang, men aldri gjenåpne den etter en rød fase 9. Funnet ved
# å kjøre: uidmap manglet, fase 9 stengte, og wcag-r1 var brent.
# Den nye id-en er BARE halve veien tilbake (Codex P1, runde 15): modulen
# står `nodeaktivert`, og den tilstanden slipper hverken `sett_modulstatus`
# eller `bytt_release` inn. Fase 2 kaller derfor `_gjenapne_modulen` først.
#
# DEFAULTEN ER `wcag-r2` FORDI MANIFESTET ER ENDRET (Codex P1 på #109).
# Releaseraden bærer `manifest_hash` — sha256 over `manifest.yaml` på disk,
# regnet ut av `registrer-m-wcag-audit.py` — og `registrer_release` avviser
# en ANNEN hash for en eksisterende `(m_wcag_audit, release_id)`-rad. #109
# både flyttet manifestet til `m56_wcag_audit/` og skrev om hodet, så
# bytene er ikke lenger de som ble registrert for `wcag-r1` under
# målerunden 2026-08-18. En runde med den gamle id-en ville dødd i fase 2
# på «release er immutable», før et eneste akseptpunkt ble målt.
#
# KONTRAKTEN GÅR MOTSATT VEI, OG DET ER MED VILJE: `KONTRAKT.md` er frosset
# på sine registrerte bytes (porten i test_motor_axe.py), fordi
# kontrakt_hash ER nøkkelen tre registre binder radene sine til — der er en
# ny verdi ikke en ny rad, men en konflikt i tre ledd. Manifestet er
# derimot bare et FELT på release-raden, og en ny release-id er en ny rad:
# derfor fryses kontrakten og nummereres releasen.
#
# Regelen står som port i CI (`test_release_id_folger_manifestets_bytes`):
# endres manifestet igjen, må denne defaulten og den pinnede hashen flyttes
# sammen — ellers oppdages det først i en staging-runde ingen ser før den
# feiler. `LEGACY_RELEASE` under er uberørt: den navngir releasen de gamle
# rundefilene tilhørte, ikke den vi kjører nå.
RELEASE = os.environ.get("WCAG_RELEASE", "").strip() or "wcag-r20"
#: Miljøet deployment- og claim-radene bærer (fase 2 registrerer
#: 'staging'; claim-porten stempler det på oppdraget). Attestmålingen
#: (052) måler claim-sporet mot NØYAKTIG dette paret (release, miljø).
MILJO = "staging"
TENANT = "t-wcagfasit"
TENANT_FREKVENS = "t-wcagfrekvens"
VERT_FREKVENS = "fasit-frekvens.test"
VERT = "fasit.test"
PORT = 8443
KONTRAKT = REPO / "platform/modules/m56_wcag_audit/kontrakt"
TESTNETT = REPO / "platform/modules/m56_wcag_audit/testnettsted"
MOTOR_AXE = REPO / "platform/modules/m56_wcag_audit/motor_axe"

RUNDE = Path(os.environ.get("WCAG_RUNDE_DIR", "/root/wcag-runde"))
#: Modultokenet MED RELEASEN det ble utstedt for (Codex P1, runde 12).
#: Et modultoken er bundet til (modul, miljø, release) OG til modulens
#: epoch, og `noddeaktiver_modul` tilbakekaller hele familien i samme
#: transaksjon som den bumper epochen. Nødstoppets vei tilbake krever en
#: NY release-id (`WCAG_RELEASE`) — så et token lagret uten sin release
#: ville blitt gjenbrukt av gjenopprettingsrunden, og hvert eneste claim
#: fra den nye releasen ville fått 401. Se `_lagret_modultoken`.
TOKEN_FIL = RUNDE / "modultoken.json"
#: Rundens identitet MED RELEASEN den gjelder for (Codex P1, runde 13).
#: Idempotensnøklene avledes av den (`_idem`), og en ny release er per
#: definisjon en ny runde: gjenopprettingen etter en rød fase 9 setter en
#: ny `WCAG_RELEASE`, men beholder rundekatalogen. Lå identiteten i en
#: uversjonert fil, replayet fase 5–6 forrige releases rapporter og fase 7
#: sin alt feilede injiseringsjobb — den nye releasen ble aldri målt.
#: Se `_rundeid`.
RUNDEID_FIL = RUNDE / "runde-id.json"
#: Rundekatalogen slik den så ut FØR release-bindingen (Codex P1/P2,
#: runde 14). Tokenet lå i en uversjonert flatfil, og skriptet hardkodet
#: releasen — så en fil fra det formatet KAN ikke ha tilhørt noen annen
#: enn `LEGACY_RELEASE`. Nettopp derfor er migreringen trygg for den ene
#: releasen og forbudt for enhver annen: kjører vi en annen release, er vi
#: forbi den runden, og det gamle tokenet er utstedt for — og bundet til —
#: en release vi ikke lenger claimer på. Se `_migrert_modultoken`.
#:
#: Porten er `RELEASE != LEGACY_RELEASE`, ikke «er `WCAG_RELEASE` satt», og
#: den forskjellen er hele poenget etter #109: defaulten er nå `wcag-r2`, så
#: migreringen er stengt for den vanlige kjøringen også — helt riktig, for
#: `wcag-r1` er en avsluttet runde med sin egen registrerte manifest-hash.
#: Konstanten blir stående som det den er: navnet på releasen filene i det
#: gamle formatet tilhørte.
LEGACY_RELEASE = "wcag-r1"
LEGACY_TOKEN_FIL = RUNDE / "modultoken"
#: ... og av samme grunn for rundens identitet (Codex P2, runde 14): en
#: `runde-id` fra det gamle formatet tilhører `LEGACY_RELEASE`, og for
#: DEN runden er den identiteten hele idempotensen. Se `_migrert_rundeid`.
LEGACY_RUNDEID_FIL = RUNDE / "runde-id"
ATT_FIL = Path("/etc/disponit/api/DISPONIT_ATT_NOKLER")
API = os.environ.get("DISPONIT_API_URL", "http://127.0.0.1:8099")

#: Arbeideren som skal claime EKTE oppdrag etter runden — uniten, brukeren
#: og credential-katalogen den leser fra. Stiene er unitens, ikke rundens:
#: `RUNDE` er /root og utilgjengelig for `disponit-wcag`.
ARBEIDER = "disponit-wcag-audit.service"
ARBEIDER_BRUKER = "disponit-wcag"

#: MOTORIMAGET RUNDEN SKAL BRUKE — og som KAN PINNES (Codex P1, #117).
#: `disponit-wcag-motor` er en mutabel tag: `bygg.sh` retagger det samme
#: navnet, så to oppslag av taggen i samme kjøring kan gi to ulike images.
#: Fase 1 kjøres på nytt i HVER invokasjon av dette skriptet (se `main`),
#: og rullbakkdrillen invokerer det tre ganger etter at den har drenert
#: den levende deploymenten — flyttes taggen i mellomtiden, oppdages det
#: først i fase 2, med den gamle arbeideren fenset og rullbakk-ID-en brukt
#: opp. `WCAG_MOTORIMAGE` lar kalleren pinne referansen til en IMMUTABEL
#: image-ID (`sha256:…`) den har lest på forhånd; da kan taggen flytte seg
#: så mye den vil uten at runden bytter bytes under seg selv.
MOTORIMAGE_TAG = "disponit-wcag-motor"
MOTORIMAGE = os.environ.get("WCAG_MOTORIMAGE") or MOTORIMAGE_TAG
DRIFT_KONF_STANDARD = Path("/etc/disponit/wcag")
DRIFT_KONF = Path(os.environ.get("WCAG_DRIFT_KONF", str(DRIFT_KONF_STANDARD)))

_EVIDENS: Path | None = None
#: Målingene som IKKE ble grønne i denne prosessen. Fase 9 setter arbeideren
#: i drift, og den porten skal bare åpnes for en modul som faktisk er
#: akseptert — så gaten leser det runden selv har målt, ikke et menneskes
#: minne om det.
_ROEDE: list[str] = []


def evidens(hendelse: str, **felt) -> None:
    if felt.get("ok") is False:
        _ROEDE.append(hendelse)
    linje = json.dumps({"ts": datetime.now(timezone.utc).isoformat(),
                        "hendelse": hendelse, **felt}, ensure_ascii=False)
    print(linje, flush=True)
    if _EVIDENS:
        with _EVIDENS.open("a", encoding="utf-8") as f:
            f.write(linje + "\n")


def _miljo() -> dict:
    ut = {}
    for linje in Path("/etc/disponit/staging.env").read_text().splitlines():
        linje = linje.strip()
        if linje and not linje.startswith("#") and "=" in linje:
            k, v = linje.split("=", 1)
            ut[k] = v.strip().strip('\'"')
    return ut


def _pg(dsn: str):
    c = psycopg.connect(dsn)
    # DRILLENS RESERVASJON ARVES (Codex P1, #117 runde 14). Flippedrillen
    # reserverer deploymentflaten i `moduldeployment_reservasjon` (049) og
    # `moduldeployment`-vakten avviser enhver overgang som ikke presenterer
    # innehaver-tokenet. Fasene 2/4/9 er drillens EGNE overganger, men de
    # kjøres som egne prosesser med egne sesjoner — uten tokenet ville
    # drillen blitt stoppet av sitt eget gjerde. Er variabelen ikke satt
    # (en vanlig sjekklisterunde), settes ingenting, og en runde som treffer
    # en ANNENS reservasjon skal nettopp avvises.
    token = os.environ.get("DISPONIT_DEPLOYRESERVASJON", "").strip()
    if token:
        c.execute("SELECT set_config('disponit.deployreservasjon',%s,false)",
                  (token,))
        c.commit()
    return c


def _kontekst(c, tenant, aktor="wcag-runde", rid=None):
    c.execute("SELECT set_config('disponit.tenant',%s,false),"
              " set_config('disponit.aktor',%s,false),"
              " set_config('disponit.request_id',%s,false)",
              (tenant, aktor, rid or "r-" + secrets.token_hex(6)))


class Http:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def _kall(self, metode, sti, kropp=None, headers=None, cookies=None):
        h = {"content-type": "application/json", **(headers or {})}
        if cookies:
            h["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
        req = urllib.request.Request(
            self.base + sti, method=metode,
            data=json.dumps(kropp).encode() if kropp is not None else None,
            headers=h)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.status, json.loads(r.read() or b"{}"), _hoder(
                    r.headers)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}"), _hoder(e.headers)
            except ValueError:
                return e.code, {}, _hoder(e.headers)

    def post(self, sti, json=None, headers=None, cookies=None, **kw):
        st, kropp, hoder = self._kall("POST", sti, kw.get("kropp", json),
                                      headers, cookies)
        return _Svar(st, kropp, hoder)

    def get(self, sti, headers=None, cookies=None):
        st, kropp, hoder = self._kall("GET", sti, None, headers, cookies)
        return _Svar(st, kropp, hoder)


def _hoder(raa) -> dict:
    """Svarhodene med SMÅ navn — HTTP-hodenavn er case-insensitive, og en
    oppslagsnøkkel som avhenger av serverens skrivemåte er ingen nøkkel."""
    return {str(k).lower(): v for k, v in (raa or {}).items()}


class _Svar:
    def __init__(self, status, kropp, hoder=None):
        self.status_code = status
        self._kropp = kropp
        self.headers = hoder or {}

    def json(self):
        return self._kropp

    @property
    def text(self):
        return json.dumps(self._kropp)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}: {self._kropp}")


# ---------------------------------------------------------------------------
# Fase 1 — forutsetninger
# ---------------------------------------------------------------------------

def fase1(m):
    # ARBEIDEREN STOPPES FØRST. Var den satt i drift av en tidligere runde
    # (fase 9), poller den samme kø som fasene 5–7 claimer fra, og
    # `claim_neste_oppdrag` gir hvert oppdrag til ÉN av dem. Målingene
    # under ville da mistet oppdrag til en prosess som ikke rapporterer inn
    # i evidensfila. Fase 9 setter den opp igjen når målingene er ferdige.
    subprocess.run(["systemctl", "stop", ARBEIDER], capture_output=True)
    reg = json.loads(ATT_FIL.read_text())
    mangler = [v for v in ("v_domenekontroll", "v_wcag_audit")
               if v not in reg]
    if mangler:
        raise SystemExit(
            f"DISPONIT_ATT_NOKLER mangler {mangler}. Legg til (0600) og "
            "restart disponit-api:\n  python3 - <<'P'\nimport json,secrets\n"
            f"f='{ATT_FIL}'\nd=json.load(open(f))\n"
            "for v in ['v_domenekontroll','v_wcag_audit']:\n"
            "    d.setdefault(v, {'k1': secrets.token_urlsafe(48)})\n"
            "json.dump(d, open(f,'w'), indent=1)\nP\n"
            "  systemctl restart disponit-api")
    ut = subprocess.run(["docker", "image", "inspect",
                         "--format", "{{.Id}}", MOTORIMAGE],
                        capture_output=True, text=True)
    if ut.returncode != 0:
        # BASISPINNEN FØRST (Codex P1, runde 6). `bygg.sh` avviser med
        # rette plassholderen i `basis-digest.txt`, så på et ferskt utsjekk
        # er «bygg imaget» to steg og ikke ett — og det første er en verdi
        # bare en pull fra registryet kan gi. Feilmeldingen sier begge, i
        # rekkefølge, i stedet for å sende operatøren innom en byggefeil
        # for å finne det ut.
        pinne = (MOTOR_AXE / "basis-digest.txt").read_text(encoding="utf-8")
        upinnet = not re.search(r"^[^#\s]+@sha256:[0-9a-f]{64}\s*$", pinne,
                                re.MULTILINE)
        raise SystemExit(
            (f"basisimaget er ikke pinnet ({MOTOR_AXE}/basis-digest.txt"
             " står med plassholderen). Hent verdien fra registryet og"
             f" commit den for seg:\n  bash {MOTOR_AXE}/bygg.sh pin\n"
             if upinnet else "")
            + (f"motorimaget er PINNET til {MOTORIMAGE} (WCAG_MOTORIMAGE),"
               " og den referansen finnes ikke i dockerlageret\n"
               if MOTORIMAGE != MOTORIMAGE_TAG else "")
            + f"bygg motorimaget først: bash {MOTOR_AXE}/bygg.sh")
    digest = ut.stdout.strip()
    evidens("fase1_ok", image_id=digest)

    RUNDE.mkdir(parents=True, exist_ok=True)
    sert, nokkel = RUNDE / "s.pem", RUNDE / "n.pem"
    if not sert.exists():
        subprocess.run(
            ["openssl", "req", "-x509", "-newkey", "rsa:2048",
             "-keyout", str(nokkel), "-out", str(sert), "-days", "30",
             "-nodes", "-subj", f"/CN={VERT}",
             "-addext", f"subjectAltName=DNS:{VERT}"], check=True,
            capture_output=True)
    _start_testnett(robots_5xx=False)
    return digest


def _start_testnett(*, robots_5xx: bool):
    subprocess.run(["pkill", "-f", "testnettsted/server\\.py"],
                   capture_output=True)
    time.sleep(0.5)
    logg = RUNDE / ("access-5xx.jsonl" if robots_5xx else "access.jsonl")
    kmd = [sys.executable, str(TESTNETT / "server.py"),
           "--port", str(PORT), "--logg", str(logg),
           "--tls-sert", str(RUNDE / "s.pem"),
           "--tls-nokkel", str(RUNDE / "n.pem")]
    if robots_5xx:
        kmd.append("--robots-5xx")
    subprocess.Popen(kmd, stdout=open(RUNDE / "server.log", "ab"),
                     stderr=subprocess.STDOUT)
    time.sleep(1)
    return logg


#: RESSURSGRENSER PÅ MOTORCONTAINEREN (Codex P1, runde 6). Launcheren ga
#: en vilkårlig, KUNDEKONTROLLERT side en host-nettverket Chromium uten
#: minne-, prosess- eller CPU-grense. En side som allokerer aggressivt
#: eller åpner mange workers kunne dermed spise vertens RAM eller
#: prosesstabell og ta ned API-et ved siden av — controllerens klokkefrist
#: stopper en LANG kjøring, ikke en grådig.
#:
#: Grensene står ETT sted og gjelder både målerunden og driften: en
#: akseptmåling gjort uten dem måler en annen motor enn den som kjører.
#: `--memory-swap` lik `--memory` slår av swap-påslaget (ellers får
#: containeren det dobbelte gjennom disk), og `--pids-limit` er
#: prosesstabellen. CPU-taket ligger på UNITEN (`CPUQuota=`), ikke her:
#: rootless podman får ikke cpu-kontrolleren delegert på en vanlig
#: systemd-vert, så `--cpus` ville feilet ved første claim — og unitens
#: cgroup inneholder containerens, så taket der omslutter begge.
MOTORGRENSER = ["--memory", "2g", "--memory-swap", "2g",
                "--pids-limit", "512"]


def motorkommando(args) -> list[str]:
    if args.motor:
        return shlex.split(args.motor)
    return ["docker", "run", "--rm", "-i", "--network", "host",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            *MOTORGRENSER,
            "-e", "MOTOR_TLS_USIKKER=1",
            # BEGGE de syntetiske vertsnavnene sjekklisten selv setter opp
            # (Codex P2, runde 2). Kartet nevnte bare `VERT`, mens fase 3
            # seeder `TENANT_FREKVENS` på `VERT_FREKVENS` og fase 6 lar de
            # fire tillatte frekvensbestillingene dreneres gjennom motoren.
            # `_pin_mal_ip` slår opp hvert navn som ikke står i kartet, og
            # et `.test`-navn løser ikke — jobbene ble derfor motorfeil, ikke
            # kjøringer. Begge navnene peker på det ene testnettstedet på
            # loopback; det er samme fixture, ikke to.
            "-e", f"MOTOR_VERTSKART={VERT}=127.0.0.1,"
                  f"{VERT_FREKVENS}=127.0.0.1",
            # SAMME REFERANSE SOM FASE 1 SLO OPP — se `MOTORIMAGE`. Sto
            # taggen her mens fase 1 var pinnet, målte fasene 5–7 et annet
            # image enn det fase 2 registrerte som `artifact_digest`.
            MOTORIMAGE]


# ---------------------------------------------------------------------------
# Fase 2 — registrering + aktivering (staging)
# ---------------------------------------------------------------------------

def _gjenapne_modulen(m) -> None:
    """Nødstoppets vei tilbake, FØR releasebyttet (Codex P1, runde 15).

    `WCAG_RELEASE`-overstyringen ble lagt inn for gjenopprettingen etter en
    rød fase 9 — men den halve veien virket ikke: `_steng_doeren` har latt
    modulen stå `nodeaktivert`, og BÅDE `sett_modulstatus` og `bytt_release`
    avviser eksplisitt den tilstanden («reaktiveres kun via reaktiver_modul»,
    «modul % er nodeaktivert»). Begge feilene falt i fase 2 sin brede
    `psycopg.Error`-gren, som fører dem som idempotente hopp, og runden døde
    først på sluttilstandssjekken. Overstyringen kunne altså aldri gjenåpne
    noe uten at operatøren først kjørte en udokumentert reaktivering for
    hånd — nøyaktig det arbeidet den skulle fjerne.

    Reaktiveringen er plattformens egen gjerdede vei, symmetrisk med
    stengingen: `reaktiver_modul` krever den NÅVÆRENDE epochen (fenced), går
    til `staging_verifisert` — der releasebyttet under starter — og bumper
    epochen én gang til mens den tilbakekaller hele tokenfamilien. Runden må
    derfor gjøre onboardingen (fase 4) om; det følger allerede av at
    `TOKEN_FIL` er bundet til releasen, og den er per definisjon en ny.

    Feiler reaktiveringen, er det IKKE et idempotent hopp: da står modulen
    fortsatt nødstoppet, og alt fase 2 gjør etterpå måler en dør som ikke
    kan åpnes. Den felles derfor her, med sin egen feil.
    """
    rad = m.execute("SELECT status, module_epoch FROM modulhode"
                    " WHERE modul_id=%s", (MODUL,)).fetchone()
    m.rollback()
    if rad is None or rad[0] != "nodeaktivert":
        return
    epoch = rad[1]
    try:
        with m.cursor() as c:
            c.execute("SET ROLE disponit_modules_admin")
            c.execute("SELECT reaktiver_modul(%s,%s,'wcag-runde')",
                      (MODUL, epoch))
            c.execute("RESET ROLE")
        m.commit()
    except psycopg.Error as e:
        m.rollback()
        evidens("fase2_reaktivering_feilet", modul=MODUL, epoch=epoch,
                feiltype=type(e).__name__, feil=str(e)[:200], ok=False)
        raise SystemExit(
            f"modulen er nodeaktivert og lot seg ikke reaktivere: {e}")
    etter = m.execute("SELECT status, module_epoch FROM modulhode"
                      " WHERE modul_id=%s", (MODUL,)).fetchone()
    m.rollback()
    evidens("fase2_modul_reaktivert", modul=MODUL, fra_epoch=epoch,
            status=etter[0] if etter else None,
            epoch=etter[1] if etter else None,
            release=RELEASE,
            merknad="tokenfamilien er tilbakekalt av epoch-bumpen —"
                    " onboardingen (fase 4) må gjøres om",
            ok=etter == ("staging_verifisert", epoch + 1))


def fase2(m, migrator_dsn, digest):
    ph = hashlib.sha256((KONTRAKT / "payload-skjema.json")
                        .read_bytes()).hexdigest()
    kvh = hashlib.sha256((KONTRAKT / "kvittering-skjema.json")
                         .read_bytes()).hexdigest()
    kh = hashlib.sha256((KONTRAKT / "KONTRAKT.md").read_bytes()).hexdigest()
    # DEN EKTE DIGESTEN, IKKE EN HASH AV DEN (Codex P1). `digest` er
    # allerede image-ID-en `sha256:<64 hex>` fra `docker image inspect`;
    # den ble hashet EN GANG TIL før den ble skrevet som release-radens
    # `artifact_digest`. Resultatet identifiserte verken image-konfigen
    # eller et registry-manifest — det var bare et tall — og raden er
    # IMMUTABEL: registeret ville for alltid båret falsk proveniens, og
    # den kunne aldri korreleres med `miljo.container_image_digest`
    # kvitteringene attesterer (samme verdi, se `_kontroller_kjor`).
    art = digest.split(":")[-1]
    if not re.fullmatch(r"[0-9a-f]{64}", art):
        raise SystemExit(
            f"motorimagets digest er ikke sha256:<64 hex>: {digest!r} —"
            " uten den kan ikke releaseraden skrives, den er immutabel")
    env = {**os.environ, "DISPONIT_MIGRATOR_URL": migrator_dsn,
           "DISPONIT_REPO": str(REPO)}
    ut = subprocess.run(
        [sys.executable, str(REPO / "deploy/staging/registrer-m-wcag-audit.py"),
         RELEASE, kh, art, ph, kvh],
        env=env, capture_output=True, text=True)
    if ut.returncode != 0:
        raise SystemExit(f"registrering feilet:\n{ut.stdout}\n{ut.stderr}")
    # NØDSTOPPETS VEI TILBAKE FØRST (Codex P1, runde 15): står modulen
    # `nodeaktivert`, avviser både `sett_modulstatus` og `bytt_release` under
    # den tilstanden, og feilene ville blitt svelget som hoppede kall.
    _gjenapne_modulen(m)
    with m.cursor() as c:
        c.execute("SET ROLE disponit_modules_admin")
        for kall, args in (
                ("installer_modul(%s,'wcag-runde')", (MODUL,)),
                ("sett_modulstatus(%s,'staging_verifisert',NULL,"
                 "'wcag-runde')", (MODUL,)),
                ("bytt_release(%s,'staging',%s,1,%s,'wcag-runde')",
                 (MODUL, RELEASE, kh)),
                ("sett_modulstatus(%s,'aktiv',%s,'wcag-runde')",
                 (MODUL, RELEASE))):
            try:
                c.execute(f"SELECT {kall}", args)
                m.commit()
            except psycopg.Error as e:
                # Kjeden er idempotent på identisk innhold, men livsløps-
                # overgangene nekter å gjentas — det er greit SÅ LENGE
                # sluttilstanden faktisk holder; den måles under.
                m.rollback()
                evidens("fase2_kall_hoppet", kall=kall.split("(")[0],
                        feiltype=type(e).__name__)
            c.execute("SET ROLE disponit_modules_admin")
        c.execute("RESET ROLE")
    m.commit()
    # SLUTTILSTANDEN er kravet, ikke kallene: aktiv modul + claiming-
    # deployment i staging — nøyaktig det claim-porten (037/#84) krever.
    #
    # ... OG DEN MÅ VÆRE DENNE RELEASEN (Codex P1). Sjekken spurte bare om
    # status var `aktiv` og om det fantes EN claiming-deployment. Feilet
    # `bytt_release` mens modulen alt sto aktiv med en ELDRE claiming
    # deployment, svelget den brede `psycopg.Error`-grenen over feilen som
    # et idempotent hopp, og denne porten passerte likevel. Runden kunne
    # dermed føre `fase2_ok` og måle hele akseptløpet mot en release den
    # aldri aktiverte — med `artifact_digest` fra et annet motorimage enn
    # det kvitteringene attesterer.
    #
    # Identiteten som kreves er hele kjeden claim-porten selv slår opp:
    # release, kontraktversjon, kontrakt_hash — og releasens
    # `artifact_digest`, som skal være NØYAKTIG imaget fase 1 fant.
    #
    # Andre claiming-deployments felles ikke: `en_claiming_per_kontrakt`
    # tillater bare én per kontrakt_hash, og en deployment med en ANNEN
    # hash kan uansett ikke claime denne oppdragstypen. De føres som
    # evidens, ikke som en rød måling.
    status = m.execute("SELECT status FROM modulhode WHERE modul_id=%s",
                       (MODUL,)).fetchone()
    claiming = m.execute(
        "SELECT d.release_id, d.kontraktversjon, d.kontrakt_hash,"
        "       r.artifact_digest"
        "  FROM moduldeployment d"
        "  JOIN modulrelease r ON r.modul_id = d.modul_id"
        "   AND r.release_id = d.release_id"
        "   AND r.kontraktversjon = d.kontraktversjon"
        "   AND r.kontrakt_hash = d.kontrakt_hash"
        " WHERE d.modul_id=%s AND d.miljo='staging' AND d.livslop='claiming'"
        " ORDER BY d.release_id", (MODUL,)).fetchall()
    m.rollback()
    ventet = (RELEASE, 1, kh, art)
    rader = [tuple(r) for r in claiming]
    if status != ("aktiv",) or ventet not in rader:
        raise SystemExit(
            f"modulkjeden er ikke claimbar som {RELEASE}: status={status},"
            f" claiming={rader} (ventet {ventet})")
    evidens("fase2_ok", release=RELEASE, kontrakt_hash=kh,
            payload_hash=ph, kvittering_hash=kvh, artifact_digest=art,
            claiming=[list(r) for r in rader])


# ---------------------------------------------------------------------------
# Fase 3 — tenant, policy, domenekontroll, admin-økt
# ---------------------------------------------------------------------------

def _policy(frekvens_maks: int):
    import yaml
    p = yaml.safe_load((REPO / "policies/bransjemal-tjenestebedrift.yaml")
                       .read_text(encoding="utf-8"))
    p["roller"].append({"id": "bestiller",
                        "beskrivelse": "Bestiller kontroller"})
    p["verifikatorer"]["v_domenekontroll"] = {
        "beskrivelse": "Plattformens domenekontroll",
        "betrodd_for": ["domenekontroll_verifisert"]}
    p["handlinger"].append({
        "id": OPPDRAGSTYPE, "modul": "M-40", "modus": "auto",
        "ved_brudd": "unntakskø", "tillatt_for": ["bestiller"],
        "dataklasser_tillatt": ["offentlig"],
        "grenser": {"frekvens": {"maks": frekvens_maks,
                                 "periode_antall": 1,
                                 "periode_enhet": "dager",
                                 "grupperingsnokkel": "mal_url"}},
        "vilkaar": [{"navn": "domenekontroll_verifisert",
                     "verifikator": "v_domenekontroll"}],
        "reversering": {"type": "direkte"}})
    p["meta"]["status"] = "produksjon"
    return p


def _seed_tenant(m, tenant, frekvens_maks, vert=VERT):
    from api import policyregister
    _kontekst(m, tenant)
    # Tenanten ER radene sine — det finnes ingen tenanttabell å opprette.
    try:
        policyregister.registrer(m, tenant, _policy(frekvens_maks), 'produksjon')
    except Exception as e:
        if "finnes" not in str(e):
            raise
    m.execute(
        "INSERT INTO domenekontroll (tenant, hostname, status,"
        " autorisasjonsgenerasjon, verifisert_ts,"
        " siste_vellykkede_revalidering, utloper)"
        " VALUES (%s,%s,'verifisert',1,now(),now(),"
        "now()+interval '90 days')"
        " ON CONFLICT (tenant, hostname) DO UPDATE SET status='verifisert',"
        " siste_vellykkede_revalidering=now(),"
        " utloper=now()+interval '90 days'", (tenant, vert))
    m.commit()


def _adminokt(m, tenant):
    from api import sesjon as sesjonmodul
    cookie, csrf = secrets.token_hex(24), secrets.token_hex(24)
    _kontekst(m, tenant)
    # Speiler testenes _identitet: (issuer, sub), bruker_id genereres av basen.
    bid = m.execute(
        "INSERT INTO brukeridentitet (issuer, sub) VALUES (%s,%s)"
        " ON CONFLICT (issuer,sub) DO UPDATE SET sub=EXCLUDED.sub"
        " RETURNING bruker_id",
        ("https://wcag-runde.local", "runde-" + secrets.token_hex(6))
    ).fetchone()[0]
    m.execute("INSERT INTO brukermedlemskap (tenant,bruker_id,roller)"
              " VALUES (%s,%s,%s) ON CONFLICT (tenant,bruker_id)"
              " DO UPDATE SET roller=EXCLUDED.roller",
              (tenant, bid, ["admin"]))
    ver = m.execute("SELECT authz_version FROM brukermedlemskap WHERE"
                    " tenant=%s AND bruker_id=%s", (tenant, bid)).fetchone()[0]
    m.execute(
        "INSERT INTO brukersesjon (sesjon_id_hash, tenant, bruker_id,"
        " authz_snapshot, csrf_hash, opprettet, siste_bruk, utloper,"
        " tilbakekalt) VALUES (%s,%s,%s,%s,%s, now(), now(),"
        " now()+interval '12 hour', false)",
        (sesjonmodul._hash(cookie), tenant, bid, ver,
         sesjonmodul._hash(csrf)))
    m.commit()
    return cookie, csrf


def fase3(m):
    _seed_tenant(m, TENANT, 12)
    _seed_tenant(m, TENANT_FREKVENS, 4, vert=VERT_FREKVENS)
    evidens("fase3_ok", tenants=[TENANT, TENANT_FREKVENS])


# ---------------------------------------------------------------------------
# Fase 4 — onboarding → modultoken (HTTP, den ekte veien)
# ---------------------------------------------------------------------------

def _lagre_modultoken(mtk: str) -> None:
    """Tokenet lagres SAMMEN MED releasen det gjelder for (Codex P1).

    Det er ikke metadata: tokenet er utstedt for nøyaktig én
    (modul, miljø, release) og for modulens epoch i utstedelsesøyeblikket.
    Uten releasen ved siden av seg er fila bare «et token», og en senere
    kjøring kan ikke se at den holder feil ett."""
    TOKEN_FIL.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FIL.write_text(
        json.dumps({"release": RELEASE, "token": mtk}, ensure_ascii=False)
        + "\n", encoding="utf-8")
    os.chmod(TOKEN_FIL, 0o600)


def _migrert_modultoken() -> str | None:
    """Tokenet fra formatet FØR release-bindingen (Codex P1, runde 14).

    Runde 12 flyttet tokenet fra den uversjonerte `RUNDE/modultoken` til
    `TOKEN_FIL`, men en vert som ALT har kjørt en grønn runde bærer bare
    den gamle fila. Ble den lest som «ingen token», ga den dokumenterte
    `--fase 9` etter den grønne runden `mtk = None` — og fase 9 svarer på
    det med `_steng_doeren`. En akseptert release ville blitt drenert av
    et formatbytte, ikke av en måling.

    Migreringen er trygg for NØYAKTIG én release: det gamle skriptet
    hardkodet `wcag-r1`, så en fil fra det formatet kan ikke bære et token
    for noe annet. Kjører vi en ANNEN release — gjenopprettingen etter et
    nødstopp, eller `wcag-r2` som er defaulten etter #109 — er dette
    tokenet enten tilbakekalt sammen med resten av familien eller utstedt
    for en release vi ikke claimer på. Da skal fila ignoreres, med en
    evidenslinje som sier hvorfor.

    Migrert er ikke det samme som gyldig: tokenet får ingen forrang av å
    ha blitt flyttet. Fase 9 feller dommen med `_tokenet_er_autorisert`
    (plattformens egne porter) før uniten enables, akkurat som for et
    token fase 4 nettopp utstedte."""
    if not LEGACY_TOKEN_FIL.exists():
        return None
    if RELEASE != LEGACY_RELEASE:
        evidens("modultoken_legacy_ignorert", fil=str(LEGACY_TOKEN_FIL),
                release=RELEASE, legacy_release=LEGACY_RELEASE,
                grunn="det uversjonerte tokenet kan bare ha tilhørt"
                      f" {LEGACY_RELEASE}, og denne kjøringen er en annen"
                      " release — onboardingen (fase 4) må gjøres om")
        return None
    try:
        tok = LEGACY_TOKEN_FIL.read_text(encoding="utf-8").strip()
    except OSError as e:
        evidens("modultoken_legacy_uleselig", fil=str(LEGACY_TOKEN_FIL),
                feiltype=type(e).__name__, feil=str(e)[:200])
        return None
    if not tok:
        evidens("modultoken_ubrukelig", fil=str(LEGACY_TOKEN_FIL),
                grunn="tom fil fra det gamle formatet — ny onboarding"
                      " kreves")
        return None
    _lagre_modultoken(tok)
    evidens("modultoken_migrert", fra=str(LEGACY_TOKEN_FIL),
            til=str(TOKEN_FIL), release=RELEASE, token_prefiks=tok[:8],
            merknad="tokenet er fra formatet før release-bindingen og kan"
                    f" bare ha vært utstedt for {LEGACY_RELEASE}; fase 9"
                    " måler likevel at det er autorisert før arbeideren"
                    " settes i drift")
    return tok


def _lagret_modultoken() -> str | None:
    """Tokenet fra rundekatalogen — men BARE for DENNE releasen (Codex P1).

    Fila overlever `--fase N` med vilje, og det er riktig så lenge runden
    gjelder samme release. Men gjenopprettingen etter en rød fase 9 går
    nettopp gjennom en NY `WCAG_RELEASE`: `noddeaktiver_modul` har da
    tilbakekalt hele tokenfamilien og bumpet modulens epoch, og
    `bytt_release` nekter å reclaime den drenerte deploymenten. Leste
    fasene 4–7 eller fase 9 likevel det gamle, uversjonerte tokenet, ville
    HVERT claim svart 401 — og fase 9 rapporterer arbeideren som oppe på
    `wcag_arbeider_oppe`, som skrives FØR det første autentiserte claimet.
    Utfallet var en «grønn» idriftsettelse av en arbeider som ikke kan
    utføre ett eneste oppdrag.

    Passer ikke releasen, finnes det altså ikke noe brukbart token: fasene
    4–7 gjør en ny onboarding (fase 4), og `--fase 9` alene har ingenting
    å sette i drift og stenger døren i stedet. Det er ikke en rød måling i
    seg selv — en ny release SKAL kreve ny onboarding — men det står i
    evidensen, for det forklarer hvorfor fase 4 kjørte om igjen.

    Finnes fila ikke, kan verten likevel bære et gyldig token i formatet
    fra før bindingen. Se `_migrert_modultoken`: det leses BARE for
    releasen det gamle skriptet hardkodet."""
    if not TOKEN_FIL.exists():
        return _migrert_modultoken()
    try:
        lagret = json.loads(TOKEN_FIL.read_text(encoding="utf-8"))
    except ValueError:
        lagret = {}
    if not isinstance(lagret, dict):
        lagret = {}
    tok = str(lagret.get("token") or "").strip()
    rel = str(lagret.get("release") or "").strip()
    if not tok:
        evidens("modultoken_ubrukelig", fil=str(TOKEN_FIL),
                grunn="ingen token i fila — ny onboarding kreves")
        return None
    if rel != RELEASE:
        evidens("modultoken_forkastet", lagret_release=rel, release=RELEASE,
                grunn="tokenet er utstedt for en ANNEN release og er"
                      " tilbakekalt av nødstoppet som krevde den nye —"
                      " onboardingen (fase 4) må gjøres om")
        return None
    return tok


def fase4(m, http, pepper):
    token_id = "tk_" + secrets.token_hex(8)
    hemmelig = secrets.token_urlsafe(32)
    mac = hmaclib.new(pepper.encode(), hemmelig.encode(),
                      hashlib.sha256).hexdigest()
    _kontekst(m, TENANT)
    m.execute("INSERT INTO api_tokener (token_id, tenant, rolle, scopes,"
              " secret_mac, status) VALUES (%s,%s,'drift',%s,%s,'AKTIV')",
              (token_id, TENANT, ["modules:onboard"], mac))
    m.commit()
    ops = f"{token_id}.{hemmelig}"
    r = http.post("/v1/modul/onboarding",
                  json={"modul_id": MODUL, "miljo": "staging",
                        "release_id": RELEASE},
                  headers={"authorization": f"Bearer {ops}"})
    r.raise_for_status()
    r2 = http.post("/v1/modul/onboarding/innlos",
                   json={"hemmelighet": r.json()["hemmelighet"]})
    r2.raise_for_status()
    mtk = r2.json()["token"]
    _lagre_modultoken(mtk)
    evidens("fase4_ok", release=RELEASE, token_prefiks=mtk[:8])
    return mtk


# ---------------------------------------------------------------------------
# Fase 5 — fasitmålingen (10 kjøringer innen frist)
# ---------------------------------------------------------------------------

_RUNDEID: str | None = None


def _migrert_rundeid() -> str | None:
    """Identiteten fra formatet FØR release-bindingen (Codex P2, runde 14).

    Runde 13 flyttet rundeid-en fra den uversjonerte `RUNDE/runde-id` til
    `RUNDEID_FIL`. En runde som ALT var i gang da denne versjonen ble
    deployet, bærer bare den gamle fila — og leste vi den som fraværende,
    fikk en gjenkjøring av fasene 5–7 en NY identitet. Det er nøyaktig det
    idempotensen finnes for å hindre: nøklene i `_idem` avledes av
    identiteten, så gjenkjøringen ville tatt nye forretningsbeslutninger i
    stedet for å replaye sine egne. Fase 5 har alt brukt 10 av tenantens
    12 daglige slots på `/index.html`, så resultatet er et par dubletter
    og så `frekvensgrense_naadd` på resten — rødt der fasiten krever 10/10.

    Som for tokenet gjelder migreringen NØYAKTIG den ene releasen det
    gamle skriptet hardkodet. Kjører vi en annen release — en overstyrt
    `WCAG_RELEASE` eller defaulten `wcag-r2` etter #109 — er identiteten
    forrige runde sin, og da er det å forkaste den hele poenget: den nye
    releasen skal måles, ikke replayes."""
    if not LEGACY_RUNDEID_FIL.exists():
        return None
    if RELEASE != LEGACY_RELEASE:
        evidens("rundeid_legacy_ignorert", fil=str(LEGACY_RUNDEID_FIL),
                release=RELEASE, legacy_release=LEGACY_RELEASE,
                grunn="den uversjonerte identiteten kan bare ha tilhørt"
                      f" {LEGACY_RELEASE} — denne releasen skal måles,"
                      " ikke replaye den forrige")
        return None
    try:
        rid = LEGACY_RUNDEID_FIL.read_text(encoding="utf-8").strip()
    except OSError as e:
        evidens("rundeid_legacy_uleselig", fil=str(LEGACY_RUNDEID_FIL),
                feiltype=type(e).__name__, feil=str(e)[:200])
        return None
    if not rid:
        return None
    evidens("rundeid_migrert", fra=str(LEGACY_RUNDEID_FIL),
            til=str(RUNDEID_FIL), release=RELEASE, runde_id=rid,
            merknad="identiteten er fra formatet før release-bindingen og"
                    f" kan bare ha tilhørt {LEGACY_RELEASE}; nøklene i"
                    " fasene 5–7 replayer derfor den påbegynte runden i"
                    " stedet for å bruke nye slots")
    return rid


def _lagret_rundeid() -> str | None:
    """Rundeid-en fra rundekatalogen — BARE for DENNE releasen (Codex P1).

    Fila skal overleve `--fase N` innenfor én runde, og gjør det. Men den
    er ikke gyldig på tvers av releaser: gjenopprettingen etter en rød
    fase 9 KREVER en ny `WCAG_RELEASE` (`bytt_release` nekter å reclaime
    en drenert deployment) mens `RUNDE` står som den står. Overlevde
    identiteten den overgangen, ble hver idempotensnøkkel i fasene 5–7
    den forrige releasens nøkkel: `/v1/bestilling` svarer
    `idempotent-replay`, og runden «måler» rapportene til releasen som
    nettopp ble nødstoppet. Fase 7 gjenbruker i tillegg sin alt feilede
    injiseringsjobb og blir normalt rød på en tom kø. Utfallet var at
    gjenopprettingsrunden brant enda en release uten å gjenåpne noe.

    Finnes fila ikke, kan rundekatalogen likevel bære identiteten i
    formatet fra før bindingen. Se `_migrert_rundeid`: den leses BARE for
    releasen det gamle skriptet hardkodet."""
    if not RUNDEID_FIL.exists():
        return _migrert_rundeid()
    try:
        lagret = json.loads(RUNDEID_FIL.read_text(encoding="utf-8"))
    except ValueError:
        lagret = {}
    if not isinstance(lagret, dict):
        lagret = {}
    rid = str(lagret.get("runde_id") or "").strip()
    rel = str(lagret.get("release") or "").strip()
    if not rid:
        evidens("rundeid_ubrukelig", fil=str(RUNDEID_FIL),
                grunn="ingen runde-id i fila — runden får en ny")
        return None
    if rel != RELEASE:
        evidens("rundeid_forkastet", lagret_release=rel, release=RELEASE,
                lagret_runde_id=rid,
                grunn="identiteten hører til en ANNEN release — nøklene"
                      " ville replayet den forrige releasens bestillinger"
                      " i stedet for å måle denne")
        return None
    return rid


def _rundeid() -> str:
    """Rundens IDENTITET — stabil i runden, NY med releasen (Codex P1, r13).

    Idempotensnøklene under avledes av denne (`_idem`). Den skrives til
    `RUNDEID_FIL` sammen med releasen den gjelder for, og gjenbrukes så
    lenge rundekatalogen OG releasen står — samme mekanikk som
    `TOKEN_FIL`, av samme grunn: en identitet uten sin release er bare
    «en id», og en senere kjøring kan ikke se at den holder feil én.

    Rekkefølgen er bevisst: `WCAG_RUNDE_ID` går FØR fila. Den som navngir
    runden selv, gjør det nettopp for å skille denne kjøringen fra den
    forrige — leste vi fila først, ville overstyringen blitt ignorert
    stille i akkurat det tilfellet den er til for.

    En NY runde krever altså en ny id: sett `WCAG_RUNDE_ID`, tøm
    rundekatalogen, eller kjør med en ny `WCAG_RELEASE`. Merk at
    frekvensgrensen er 12 per rullende døgn per `mal_url` og at en full
    runde bruker nøyaktig 12 på `/index.html`: en ny runde innen ett døgn
    treffer taket uansett nøkler, og det er grensen som virker, ikke en
    feil. En gjenoppretting må derfor vente til vinduet har rullet — det
    er en reell venting, ikke noe nøklene kan trylle bort."""
    global _RUNDEID
    if _RUNDEID is not None:
        return _RUNDEID
    onsket = os.environ.get("WCAG_RUNDE_ID", "").strip()
    lagret = None if onsket else _lagret_rundeid()
    rid = onsket or lagret or ("r" + secrets.token_hex(6))
    evidens("rundeid", runde_id=rid, release=RELEASE,
            kilde=("WCAG_RUNDE_ID" if onsket
                   else "rundekatalogen" if lagret else "ny"))
    RUNDE.mkdir(parents=True, exist_ok=True)
    RUNDEID_FIL.write_text(
        json.dumps({"release": RELEASE, "runde_id": rid}, ensure_ascii=False)
        + "\n", encoding="utf-8")
    _RUNDEID = rid
    return rid


def _idem(merkelapp: str, kropp: dict) -> str:
    """Idempotensnøkkel som er STABIL for samme runde + samme bestilling.

    `secrets.token_hex()` per kall gjorde hver gjenkjøring til en NY
    forretningshandling: samme `mal_url` fikk en ny frekvensreservasjon i
    stedet for å replaye beslutningen. Fasene er dokumentert som
    idempotente, og fase 5 alene bruker 10 av tenantens 12 daglige
    slots — så en gjenkjøring ga `frekvensgrense_naadd` der fasiten
    krever 10/10 grønne. En nøkkel som endrer seg per forsøk er ingen
    idempotensnøkkel.

    Kroppens hash er med i nøkkelen med vilje: endres bestillingen, blir
    den en ny nøkkel i stedet for `idempotenskonflikt` mot den gamle
    (kjernen avviser samme nøkkel med annen intensjon)."""
    h = hashlib.sha256(
        json.dumps(kropp, sort_keys=True, ensure_ascii=False)
        .encode("utf-8")).hexdigest()
    return f"{_rundeid()}-{merkelapp}-{h[:12]}"


#: Skriptet som spør IMAGET selv hva slags Chromium det bærer. Kjøres med
#: `--entrypoint python3`, så det er nøyaktig den nettleseren motoren ville
#: startet — ikke en versjon vi tror står der.
_CHROMIUM_SKRIPT = (
    "import subprocess;"
    "from playwright.sync_api import sync_playwright;"
    "p=sync_playwright().start();"
    "print(subprocess.run([p.chromium.executable_path,'--version'],"
    "capture_output=True,text=True).stdout.strip())")

_chromium_cache: dict = {}

#: Containerkjøretidene en motorkommando kan være bygget på.
KJORETIDER = ("docker", "podman", "nerdctl")


def _kjoretidsledd(motorkmd) -> int | None:
    """Indeksen til containerkjøretiden i en motorkommando, ellers None.

    Kjøretiden står ikke nødvendigvis først: driftens kommando kjøres SOM
    `disponit-wcag` (`runuser -u … -- env … podman run …`), og
    `WCAG_DRIFT_MOTOR` kan bære et hvilket som helst forspann. Det er
    kjøretiden vi trenger for å vite om kommandoen kan inspiseres — og
    imaget er alltid det siste leddet."""
    for i, ledd in enumerate(motorkmd):
        if Path(ledd).name in KJORETIDER and "run" in motorkmd[i + 1:]:
            return i
    return None


def _chromium_versjon(motorkmd) -> str:
    """Nettleserversjonen imaget FAKTISK bærer (Codex P2).

    `chromium_versjon` sto som den bokstavelige strengen «chromium» i hver
    eneste staging- og produksjonskontekst. Verdien er skjemagyldig og
    inneholder ingenting: feltet finnes for å identifisere nettleseren
    kontrollen ble kjørt med, og proveniensen i den promoterte rapporten
    ble villedende i det nettleseratferden endrer seg mellom to
    image-utgivelser — som er nøyaktig når feltet trengs.

    Verdien hentes derfor ut av imaget selv, én gang per motorkommando.
    Kan den ikke hentes (motorkommandoen er ingen containerkjøring, eller
    kjøringen feilet), sies det: `ukjent` er en ærlig verdi, «chromium» var
    en tom påstand som så ut som en opplysning."""
    nokkel = tuple(motorkmd)
    if nokkel in _chromium_cache:
        return _chromium_cache[nokkel]
    versjon = "ukjent"
    # Kjøretiden LETES opp (Codex P2, runde 6): driftens kommando har et
    # forspann (`runuser … env … podman run …`), så posisjon 0 er ikke gitt.
    i = _kjoretidsledd(motorkmd)
    runtime = Path(motorkmd[i]).name if i is not None else ""
    if runtime:
        ut = subprocess.run(
            motorkmd[:-1] + ["--entrypoint", "python3", motorkmd[-1],
                             "-c", _CHROMIUM_SKRIPT],
            capture_output=True, text=True, stdin=subprocess.DEVNULL)
        linje = ut.stdout.strip().splitlines()[-1:] or [""]
        if ut.returncode == 0 and linje[0]:
            versjon = linje[0][:64]
    evidens("motor_chromium_versjon", versjon=versjon, runtime=runtime)
    _chromium_cache[nokkel] = versjon
    return versjon


def _serverkontekst(digest: str, motorkmd) -> dict:
    """`miljo`-blokka rapporten attesterer — ETT sted (Codex P2).

    Blokka sto skrevet ordrett to steder (fase 5–7 og fase 9), og de to
    kan gli fra hverandre: da attesterer staging og drift hvert sitt
    miljø for det samme imaget. Verdiene for nettleserkonteksten er
    motorens egne (`kjor.LOCALE`/`TIDSSONE`/`VIEWPORT`) — se
    `test_nettleserkonteksten_er_den_som_attesteres`."""
    return {"axe_versjon": "4.10.3",
            "chromium_versjon": _chromium_versjon(motorkmd),
            "container_image_digest": "sha256:" + digest.split(":")[-1],
            "viewport": "1280x800", "locale": "nb",
            "timezone": "Europe/Oslo"}


def _bestill(http, cookie, csrf, kropp, nokkel=None):
    from api import sesjon as sesjonmodul
    hoder = {"X-Disponit-CSRF": csrf}
    if nokkel:
        hoder["Idempotency-Key"] = nokkel
    return http.post("/v1/bestilling", json=kropp, headers=hoder,
                     cookies={sesjonmodul.C_SESJON: cookie})


def _kontroller_kjor(mtk, motorkmd, digest):
    from modules.m56_wcag_audit import controller
    from modules.m56_wcag_audit.motor import Kommandomotor
    from policy_validator import attestering
    reg = json.loads(ATT_FIL.read_text())
    nid = sorted(reg["v_wcag_audit"])[0]

    def signer(kropp):
        return attestering.signer(
            {**kropp, "verifikator": "v_wcag_audit"},
            nid, reg["v_wcag_audit"][nid])

    return controller.kjor_en(Http(API), mtk, Kommandomotor(motorkmd),
                              _serverkontekst(digest, motorkmd), signer)


def _fasitkontroll(scenario: str, rapport: dict) -> list[str]:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "fasitkontroll", MOTOR_AXE / "fasitkontroll.py")
    fk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fk)
    fasit = json.loads((TESTNETT / "fasit.json").read_text())
    s = fasit["scenarier"][scenario]
    motorformet = {
        "funn": [{"regel_id": f["regel_id"], "alvorlighet": f["alvorlighet"],
                  "antall": f["antall"]} for f in rapport["funn"]],
        "blokkert": [{"vert": b["vert"], "art": b["art"],
                      "antall": b["antall"]}
                     for b in rapport["dekningsbegrensninger"]],
        "avkortet": [rapport["avkortet"]["truffet"],
                     rapport["avkortet"]["tak"],
                     rapport["avkortet"]["verdi"]],
        "sider": [{"url": x["url"], "status": x["status"]}
                  for x in rapport["sider_kontrollert"]],
    }
    return fk.avvik(s, motorformet)


def _rapport(http, lese_tok, oid):
    return http.get(f"/v1/rapport/{oid}",
                    headers={"authorization": f"Bearer {lese_tok}"})


def _kvittering_signert(m, oid: int) -> bool:
    """MÅLER at kvitteringen på `oid` kom av verifiseringsveien.

    Codex' P2 på PR #117 (runde 15): fase 5 skrev «ti kjøringer signert
    innen frist», og hverken linja eller sammendragets predikat inneholdt
    én eneste måling av en signatur. Ti rapporter med usignerte eller
    manglende kvitteringer ga et grønt `10/10` — ordet «signert» sto der
    som en påstand om noe runden aldri spurte om.

    Det som måles her, er AVTRYKKET kvitteringsveien setter igjen, ikke
    at to felter samme skriver eier er enige:

      * signaturen står i sin egen kolonne, ikke tom, og er identisk med
        verdien i konvolutten som ligger lagret — `kvittering_signatur`
        ER `kvittering->signatur->>verdi`, hentet ut av verifiseringen
        selv (app.py), så spriker de, kommer raden ikke derfra;
      * `resultathash` er satt, siden veien skriver alle tre i samme
        `UPDATE`;
      * og kvitteringskapabiliteten for oppdraget er BRENT med nøyaktig
        den hashen. `kvitteringskapabiliteter` er `REVOKE ALL ... FROM
        PUBLIC` (005): ingen rolle skriver den direkte, bare
        definerne — og `bruk_kvitteringskapabilitet` kalles av API-et
        FØRST etter at `attestering.verifiser` har godtatt signaturen.
        Hashen er uforanderlig når den først er festet.

    Nøklene er HMAC-hemmeligheter som bor i API-et; en SQL-spørring kan
    ikke verifisere signaturen på nytt. Da er den brente kapabiliteten
    med matchende resultathash det sterkeste sporet basen har av at
    verifiseringen FAKTISK skjedde — og det er det denne målingen er.

    MÅLINGEN BOR I BASEN (Codex P1, #117 runde 17). Spørringen sto her,
    og den kjørte som `disponit_migrator` — men `kvitteringskapabiliteter`
    er `REVOKE ALL … FROM PUBLIC` med kolonnegrant bare til
    `disponit_modul_eier`, og migrators medlemskap i eierrollen er `WITH
    INHERIT FALSE`. Den ville derfor dødd på «permission denied» første
    gang den faktisk ble kjørt. `maal_rent_utfall` (049) er samme
    predikat bak fullmakten det trenger, og `registrer_moduldrill` og
    drillsonden kaller nøyaktig den: én måling, ett sted.
    """
    m.execute("SET ROLE disponit_modules_admin")
    rad = m.execute("SELECT maal_rent_utfall(%s,%s)",
                    (TENANT, oid)).fetchone()
    m.execute("RESET ROLE")
    return bool(rad and rad[0])


def _kjoringsattest(m, oid: int) -> dict:
    """Drilloppdragets port anvendt på KONTROLLØPET (052, §1.3a).

    `revisjonslogg_korrekt` lånte fristtellingen — et aggregat som aldri
    spurte om kvitteringen attesterte riktig release, eller om
    revisjonsraden fantes. Nå måles hver kjøring for seg, i basen, av
    `maal_kjoringsattest` (samme fullmaktsform som `maal_rent_utfall`,
    og med drillportens egne ledd):

      * kvitteringsavtrykket (`maal_rent_utfall` — signaturkolonnen
        identisk med konvoluttens, resultathash satt, kapabiliteten
        brent med nøyaktig den hashen);
      * claim-sporet: oppdraget ble claimet av NØYAKTIG (RELEASE, MILJO)
        — kjøringens egen kontekst, ikke en annen releases;
      * artefakt-likheten (SP-3): utfort ⇔ promotert artefakt på
        releasen;
      * revisjonsraden: oppdraget er koblet til sin
        fase-2-TILLAT-loggpost (008), og raden finnes med riktig tenant
        og beslutning. `loggpost` returneres så ti kjøringer kan kreves
        å bære ti DISTINKTE rader — én rad delt av alle er én rad.

    Målingen bor i basen bak fullmakten den trenger (Codex P1, #117
    runde 17-formen); skriptet skriver bare utfallene i evidensstrømmen.
    """
    m.execute("SET ROLE disponit_modules_admin")
    rad = m.execute(
        "SELECT kvittering_ok, claim_release_ok, artefakt_ok,"
        " revisjonsrad_ok, modul_ok, loggpost"
        " FROM maal_kjoringsattest(%s,%s,%s,%s,%s,%s)",
        (TENANT, oid, RELEASE, MILJO, MODUL, OPPDRAGSTYPE)).fetchone()
    m.execute("RESET ROLE")
    return {"kvittering_ok": bool(rad and rad[0]),
            "claim_release_ok": bool(rad and rad[1]),
            "artefakt_ok": bool(rad and rad[2]),
            "revisjonsrad_ok": bool(rad and rad[3]),
            "modul_ok": bool(rad and rad[4]),
            "loggpost": rad[5] if rad else None}


def fase5(m, http, mtk, motorkmd, digest):
    cookie, csrf = _adminokt(m, TENANT)
    lese_tok = _lesetoken(m, TENANT)
    # DATASETTETS IDENTITET (052, §1.2): sha256 over BYTENE serveren
    # faktisk serverer i denne runden (testnettstedet leses live fra
    # utsjekket), målt med NØYAKTIG samme funksjon CI bruker på de
    # innsjekkede bytene (`manifestskjema.datasett_sha256`) — én
    # kanonisering, to ledd, byte-likhet som krav (SP-11). Skrives i
    # evidensstrømmen så konverteren kan bære den til artefaktets
    # `oppsett` og porten sammenligne.
    import manifestskjema as ms
    evidens("datasett_identitet",
            datasett_sha256=ms.datasett_sha256(TESTNETT), ok=True)
    # Regelsettet kjøringene SKAL gå på er motorens pinnede — samme
    # kilde som miljøblokka rapporten attesterer (`_serverkontekst`).
    # Hver rapports `regelsett_versjon` måles mot dette; et avvik er en
    # kjøring på et annet regelsett enn det aksepten binder.
    forventet_regelsett = ("axe-core-"
                           + _serverkontekst(digest, motorkmd)["axe_versjon"])
    gronne = signerte = attesterte = 0
    loggposter = set()
    for i in range(10):
        scenario = "nettsted_maks4" if i % 3 == 0 else "enkeltside"
        fasit = json.loads((TESTNETT / "fasit.json").read_text())
        sp = fasit["scenarier"][scenario]["payload"]
        start = time.monotonic()
        kropp = {"bestillingstype": OPPDRAGSTYPE, "hostname": VERT,
                 "sti": sp["sti"], "kravsett": sp["kravsett"],
                 "omfang": sp["omfang"], "maks_sider": sp["maks_sider"]}
        r = _bestill(http, cookie, csrf, kropp, _idem(f"f5-{i:02d}", kropp))
        if r.status_code != 200 or r.json().get("beslutning") != "tillat":
            # EN AVVIST BESTILLING ER EN RØD MÅLING (Codex P1). Linja bar
            # ingen `ok`, så `_ROEDE` fikk aldri vite at fasitmålingen
            # endte 9/10 — og fase 9 gater aktiveringen av arbeideren
            # utelukkende på `_ROEDE`. En runde med en forbigående avvisning
            # kunne dermed sette produksjonsarbeideren i drift på et
            # akseptløp som ikke bestod.
            evidens("kjoring_avvist", i=i, status=r.status_code,
                    svar=r.json(), ok=False)
            continue
        oid = r.json()["oppdrag_id"]
        # ET GJENSPILL ER IKKE ET NYTT OPPDRAG (Codex P1). Med de stabile
        # `_idem`-nøklene svarer `/v1/bestilling` `idempotent-replay` på en
        # gjenkjøring av samme fase i samme runde: beslutningen er tatt, og
        # oppdraget er som regel alt UTFØRT. `_kontroller_kjor` claimer
        # likevel globalt, fant ingenting å gjøre og ga `utfall: "tomt"` —
        # altså 0/10 for en runde der ti gyldige rapporter lå ferdige.
        # Gjenkjøringen målte dermed køen, ikke resultatet.
        #
        # Finnes rapporten, ER kjøringen gjort: da kontrolleres DEN mot
        # fasiten i stedet for at motoren kjøres om igjen. Finnes den ikke
        # (beslutningen ble tatt, men utførelsen kom aldri i mål), er
        # oppdraget fortsatt claimbart og motoren skal kjøre som før.
        gjenspill = r.headers.get("idempotent-replay") == "1"
        rr = _rapport(http, lese_tok, oid) if gjenspill else None
        alt_utfort = rr is not None and rr.status_code == 200
        if alt_utfort:
            res, varighet = {"utfall": "utfort"}, None
        else:
            res = _kontroller_kjor(mtk, motorkmd, digest)
            varighet = time.monotonic() - start
            rr = _rapport(http, lese_tok, oid)
        frist = 1800 if sp["omfang"] == "enkeltside" else 3600
        avvik = (_fasitkontroll(scenario, rr.json()["rapport"])
                 if rr.status_code == 200 else [f"rapport {rr.status_code}"])
        # …og SIGNATUREN måles, den påstås ikke (Codex P2, runde 15).
        # Aggregatet under het «ti kjøringer SIGNERT innen frist» mens
        # ingen del av runden noensinne spurte om en signatur. Nå bærer
        # hver linje sin egen måling — også en gjenspilt, for der ER
        # kvitteringen alt levert og avtrykket ligger i basen.
        _kontekst(m, TENANT)
        signert = _kvittering_signert(m, oid)
        # …og ATTESTEN er sin egen måling (052, §1.3a): kvitteringen
        # attesterer NØYAKTIG den releasen/miljøet/artefaktet kjøringen
        # gikk på, og revisjonsraden finnes. Også på et gjenspill —
        # avtrykkene ligger i basen.
        attest = _kjoringsattest(m, oid)
        m.rollback()
        regelsett = (rr.json()["rapport"].get("regelsett_versjon")
                     if rr.status_code == 200 else None)
        attest_ok = (attest["kvittering_ok"] and attest["claim_release_ok"]
                     and attest["artefakt_ok"] and attest["modul_ok"]
                     and regelsett == forventet_regelsett)
        # Fristen ble målt da kjøringen faktisk skjedde, og den målingen
        # står i evidensfila fra den runden. Et gjenspill kan ikke måle den
        # på nytt, og skal ikke late som.
        rent = (res.get("utfall") == "utfort" and not avvik
                and (alt_utfort or varighet < frist))
        # 9/10 er rødt, ikke «nesten» (klarsignalet §1.3): en rød attest
        # eller manglende revisjonsrad gjør LINJA rød, og fase 9 gater
        # aktiveringen på nettopp det.
        ok = rent and signert and attest_ok and attest["revisjonsrad_ok"]
        gronne += rent
        signerte += rent and signert
        attesterte += rent and signert and attest_ok
        if rent and attest["revisjonsrad_ok"] \
                and attest["loggpost"] is not None:
            loggposter.add(attest["loggpost"])
        evidens("kjoring", i=i, scenario=scenario, oppdrag=oid,
                utfall=res.get("utfall"), gjenspill=alt_utfort,
                varighet_s=None if varighet is None else round(varighet, 1),
                frist_s=frist, avvik_mot_fasit=len(avvik),
                kvittering_signert=signert,
                kvittering_attest_ok=attest_ok,
                revisjonsrad_ok=attest["revisjonsrad_ok"],
                loggpost=attest["loggpost"], regelsett=regelsett,
                avvik=avvik[:5], ok=ok)
    # ... og SUMMEN bærer den samme påstanden (Codex P1). Fasitkravet er
    # 10/10; alt annet er en rød måling, uansett hvilken av de ti som
    # sviktet og på hvilken måte. Uten `ok` her kunne aggregatet stå
    # `9/10` i evidensfila mens `_ROEDE` var tom.
    #
    # De to tallene er TO målinger, ikke ett navn på to ting (Codex P2,
    # runde 15). Aggregatet het `ti_kjoringer_signert_innen_frist`, men
    # det det talte var RENE utfall innen egen frist — ingen del av
    # runden spurte noen gang om en signatur, og navnet var hele beviset.
    # Det heter nå det det teller, signaturen har fått sitt eget tall, og
    # sammendragsgeneratoren regner begge ut av `kjoring`-linjene i
    # stedet for å lese dem her. Så et navn kan ikke igjen bære en
    # påstand ingen måling står bak.
    #
    # …og REGELSETTET runden bandt seg til skrives ned (Codex P1, #123).
    # `kvittering_attest_ok` bar sammenligningen `regelsett ==
    # forventet_regelsett` som ett ferdigtygd ja/nei fra produsenten,
    # mens `regelsett` på hver linje aldri ble sett av noen. Nå bærer
    # summen forventningen, linjene bærer det observerte, og konverteren
    # regner likheten selv.
    evidens("fase5_resultat",
            regelsett_forventet=forventet_regelsett,
            ti_kjoringer_rent_innen_frist=f"{gronne}/10",
            kjoringer_med_maalt_signatur=f"{signerte}/10",
            kjoringer_med_attestert_kvittering=f"{attesterte}/10",
            revisjonsrader_mot_bestilt=f"{len(loggposter)}/10",
            funn_avvik_mot_fasit=0 if gronne == 10 else "SE kjoring-linjene",
            ok=gronne == 10 and signerte == 10 and attesterte == 10
               and len(loggposter) == 10)
    return signerte


def _lesetoken(m, tenant):
    pepper = _miljo().get("DISPONIT_TOKEN_PEPPER") or os.environ.get(
        "DISPONIT_TOKEN_PEPPER", "")
    token_id = "tk_" + secrets.token_hex(8)
    hemmelig = secrets.token_urlsafe(32)
    mac = hmaclib.new(pepper.encode(), hemmelig.encode(),
                      hashlib.sha256).hexdigest()
    _kontekst(m, tenant)
    m.execute("INSERT INTO api_tokener (token_id, tenant, rolle, scopes,"
              " secret_mac, status) VALUES (%s,%s,'bruker',%s,%s,'AKTIV')",
              (token_id, tenant, ["decisions:read"], mac))
    m.commit()
    return f"{token_id}.{hemmelig}"


# ---------------------------------------------------------------------------
# Fase 6 — portene 18–21 + 24
# ---------------------------------------------------------------------------

def fase6(m, http, mtk, motorkmd, digest):
    # 20a: robots respektert — negativt bevis i MÅLETS logg.
    logg = RUNDE / "access.jsonl"
    privat = sum(1 for linje in logg.open()
                 if "/privat/" in json.loads(linje)["sti"]) \
        if logg.exists() else -1
    # BEGGE ROBOTS-MÅLINGENE GATER AKTIVERINGEN (Codex P1). Linjene sto
    # uten `ok`, og `evidens` legger bare en måling i `_ROEDE` når den
    # SIER at den feilet. De to obligatoriske robots-portene kunne derfor
    # begge slå til — forespørsler under `/privat/` i målets egen logg, en
    # avbrutt 5xx-kjøring, en crawl som dekket et annet antall sider enn
    # den ene tillatte — mens fase 9 så en tom `_ROEDE` og enablet
    # produksjonsarbeideren. En port som ikke kan bli rød er ingen port.
    #
    # `privat == -1` (ingen access-logg) er også rødt: da finnes det
    # ingen måling, og en umålt port er ikke en bestått port.
    evidens("port20_robots", privat_forisporsler=privat, krav=0,
            ok=privat == 0)

    # 20b: robots 5xx → ingen crawl (kun mal_url).
    _start_testnett(robots_5xx=True)
    cookie, csrf = _adminokt(m, TENANT)
    lese_tok = _lesetoken(m, TENANT)
    kropp = {"bestillingstype": OPPDRAGSTYPE, "hostname": VERT,
             "sti": "/index.html", "kravsett": "wcag21_aa",
             "omfang": "nettsted", "maks_sider": 4}
    r = _bestill(http, cookie, csrf, kropp, _idem("f6-r5xx", kropp))
    r.raise_for_status()
    oid = r.json()["oppdrag_id"]
    # ET GJENSPILL ER IKKE ET NYTT OPPDRAG — SAMME PORT SOM I FASE 5
    # (Codex P1, runde 9). Med den stabile `_idem`-nøkkelen svarer
    # `/v1/bestilling` `idempotent-replay` når fase 6 kjøres om igjen på
    # samme runde-ID: beslutningen er tatt og oppdraget er alt utført.
    # Det ubetingede `_kontroller_kjor` claimet likevel GLOBALT, fant en
    # tom kø og ga `utfall: "tomt"` — så port20_robots_5xx ble rød, og
    # fase 9 nøddeaktiverte modulen på grunnlag av en rapport som lå
    # ferdig og gyldig. Var det derimot et ANNET oppdrag i køen, claimet
    # den det, og porten målte en helt annen kjøring enn sin egen.
    #
    # Finnes rapporten, ER kjøringen gjort: da måles DEN. Finnes den ikke
    # (beslutningen ble tatt, men utførelsen kom aldri i mål), er
    # oppdraget fortsatt claimbart og motoren skal kjøre som før.
    gjenspill = r.headers.get("idempotent-replay") == "1"
    rr = _rapport(http, lese_tok, oid) if gjenspill else None
    alt_utfort = rr is not None and rr.status_code == 200
    if alt_utfort:
        res = {"utfall": "utfort"}
    else:
        res = _kontroller_kjor(mtk, motorkmd, digest)
        rr = _rapport(http, lese_tok, oid)
    sider = len(rr.json().get("rapport", {}).get("sider_kontrollert", []))
    # Kravet er BEGGE deler: kjøringen må ha fullført (en avbrutt motor
    # kontrollerer trivielt «bare én side»), og rapporten må dekke
    # nøyaktig den ene bestilte siden — ikke null, og ikke de fire
    # bestillingen ba om. `sider` er 0 om rapporten uteble.
    evidens("port20_robots_5xx", utfall=res.get("utfall"), oppdrag=oid,
            sider_kontrollert=sider, krav=1, gjenspill=alt_utfort,
            ok=res.get("utfall") == "utfort" and sider == 1)
    _start_testnett(robots_5xx=False)

    # 21: frekvensgrensen — femte bestilling samme døgn → unntakskø.
    ck2, cs2 = _adminokt(m, TENANT_FREKVENS)
    lese_tok2 = _lesetoken(m, TENANT_FREKVENS)
    utfall = []
    frekvensoppdrag = []
    for i in range(5):
        kropp = {"bestillingstype": OPPDRAGSTYPE, "hostname": VERT_FREKVENS,
                 "sti": "/index.html", "kravsett": "wcag21_aa",
                 "omfang": "enkeltside"}
        r = _bestill(http, ck2, cs2, kropp, _idem(f"f6-frekv-{i}", kropp))
        svar = r.json()
        utfall.append(svar.get("beslutning"))
        if svar.get("beslutning") == "tillat" and svar.get("oppdrag_id"):
            frekvensoppdrag.append(svar["oppdrag_id"])
    evidens("port21_frekvens", utfall=utfall,
            krav="4 tillat + 1 ikke-tillat",
            ok=utfall[:4] == ["tillat"] * 4 and utfall[4] != "tillat")

    # KØEN TØMMES FØR FASE 7 (Codex P1). Porten over måler BESLUTNINGEN,
    # ikke utførelsen — men de fire tillatte bestillingene er fire EKTE
    # WCAG-oppdrag, og ingen av dem ble claimet. `claim_neste_oppdrag`
    # velger eldste claimbare oppdrag GLOBALT (`ORDER BY opprettet, id`,
    # ingen tenantfilter — funksjonen er SECURITY DEFINER), så fase 7 sin
    # `false`-motor traff ETT AV DISSE i stedet for sitt eget oppdrag.
    #
    # Og fase 7 ble GRØNN av det: den leste `avbrutt` fra en helt annen
    # kjøring, og «ingen promoterte artefakter» fra et oppdrag ingen hadde
    # rørt. En feilinjeksjon som beviser seg på et urørt oppdrag beviser
    # ingenting. Oppdragene kjøres derfor ferdig her, på den ekte veien.
    drenert = []
    for _ in range(len(utfall) + 4):    # tak, aldri en evig løkke
        res = _kontroller_kjor(mtk, motorkmd, digest)
        if res.get("utfall") == "tomt":
            break
        drenert.append(res.get("utfall"))
    else:
        raise SystemExit(f"køen tømmes ikke: {drenert} — fase 7 ville"
                         " feilinjisert mot et annet oppdrag enn sitt eget")
    # DRENERINGEN ER OGSÅ EN MÅLING (Codex P2, runde 2). Porten sendte
    # ingen `ok=`, og `evidens()` rødmerker kun på `ok is False` — den kunne
    # altså ikke bli rød. Hver drenert jobb er et EKTE WCAG-oppdrag på den
    # ekte veien, så `avbrutt`/`ukvittert` her er en motorfeil som skal
    # stoppe runden, ikke en tom kø som skal passere stille. Kravet er
    # begge deler: køen må være tom FØR fase 7, og de tillatte
    # frekvensbestillingene må faktisk ha kjørt ferdig.
    #
    # MEN ET GJENSPILL ER INGEN UDRENERT KØ (Codex P1, runde 3). Fasene er
    # dokumentert idempotente, og med den stabile `_idem`-nøkkelen svarer
    # `/v1/bestilling` `idempotent-replay` når fase 6 kjøres om igjen på
    # samme runde-ID: beslutningene er de opprinnelige, så `tillatt` er
    # fortsatt 4 — men de fire oppdragene er alt utført, køen er tom og
    # `drenert` blir TOM. Et krav om fire NYE dreneringer rødmerket dermed
    # en fullt vellykket gjenkjøring og hindret fase 9 i å enable
    # arbeideren, på nøyaktig den veien port20_robots_5xx alt hadde lært.
    #
    # Porten måler derfor OPPDRAGENE, ikke antall runder i løkka: finnes
    # rapporten, ER kjøringen gjort — enten den kom i stand her eller i
    # forrige forsøk. Motorfeilen fanges fortsatt, av at hver jobb som
    # FAKTISK ble drenert i denne runden må ha kjørt ferdig (en avbrutt
    # kjøring gir ingen rapport, jf. fase 7).
    tillatt = sum(1 for u in utfall if u == "tillat")
    ferdige = [oid for oid in frekvensoppdrag
               if _rapport(http, lese_tok2, oid).status_code == 200]
    evidens("fase6_ko_drenert", utfall=drenert, tillatt=tillatt,
            oppdrag=frekvensoppdrag, ferdige=ferdige,
            krav="tom kø før fase 7 — og hvert tillatt frekvensoppdrag"
                 " rapportert `utfort`",
            ok=tillatt > 0 and len(ferdige) == tillatt
            and all(u == "utfort" for u in drenert))

    # 24: motoren kjører uten credentials — mål containerens faktiske miljø.
    #
    # OG DEN MOTOREN SOM FAKTISK ER KONFIGURERT (Codex P1). Overstyringen
    # sto bak `motorkmd[0] == "docker"`, så med den rootless `podman
    # run`-kommandoen uniten foreskriver, falt målingen tilbake på et bart
    # `env` PÅ VERTEN. Miljøet her bærer med vilje `DISPONIT_KEK` og
    # `DATABASE_URL`, så `lekk` ble nødvendigvis ikke-tom: porten kunne
    # ALDRI passere for nettopp den sikre utrullingen den finnes for å
    # måle. `--entrypoint` er lik for docker og podman, så overstyringen
    # bygges av den KONFIGURERTE kommandoen uansett runtime.
    kanari = "KANARI_" + secrets.token_hex(8)
    runtime = Path(motorkmd[0]).name if motorkmd else ""
    if runtime in ("docker", "podman", "nerdctl") and "run" in motorkmd:
        # Imaget står sist; `--entrypoint` må stå foran det.
        kmd = motorkmd[:-1] + ["--entrypoint", "env", motorkmd[-1]]
    else:
        # En motorkommando som ikke er en containerkjøring kan ikke måles
        # her — og en umålt port er ikke en bestått port. Å måle verten i
        # stedet ville vært å svare på et annet spørsmål.
        kmd = None
        evidens("port24_motormiljo", maalt=False, runtime=runtime,
                grunn="motorkommandoen er ingen containerkjøring — porten"
                      " måler containerens miljø, ikke sjekklistevertens",
                ok=False)
    if kmd is not None:
        ut = subprocess.run(
            kmd, env={**os.environ, "DISPONIT_KEK": kanari,
                      "DATABASE_URL": "postgresql://hemmelig"},
            capture_output=True, text=True)
        lekk = [l for l in ut.stdout.splitlines()
                if l.startswith("DISPONIT_") or kanari in l
                or "DATABASE_URL" in l]
        # En kjøring som ikke kom i gang har heller ingen lekkasjer å vise
        # — og ville dermed sett ut som en bestått port. Returkoden er en
        # del av målingen, ikke en detalj.
        evidens("port24_motormiljo", maalt=True, runtime=runtime,
                returkode=ut.returncode, lekkasjer=lekk,
                feil=ut.stderr.strip()[:200],
                ok=ut.returncode == 0 and not lekk)


# ---------------------------------------------------------------------------
# Fase 7 — feilinjisering (portene 22/23 + motorfeil)
# ---------------------------------------------------------------------------

def fase7(m, http, mtk, digest):
    # Motorfeil → kvittering avbrutt, INTET artefakt.
    cookie, csrf = _adminokt(m, TENANT)
    kropp = {"bestillingstype": OPPDRAGSTYPE, "hostname": VERT,
             "sti": "/index.html", "kravsett": "wcag21_aa",
             "omfang": "enkeltside"}
    r = _bestill(http, cookie, csrf, kropp, _idem("f7-motorfeil", kropp))
    r.raise_for_status()
    oid = r.json()["oppdrag_id"]
    res = _kontroller_kjor(mtk, ["false"], digest)
    _kontekst(m, TENANT)
    rad = m.execute("SELECT status, kvittering IS NOT NULL FROM oppdrag"
                    " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
    art = m.execute("SELECT count(*) FROM artefakt WHERE tenant=%s AND"
                    " oppdrag_id=%s AND tilstand='promotert'",
                    (TENANT, oid)).fetchone()[0]
    m.rollback()
    # PORTEN MÅ TREFFE VÅRT EGET OPPDRAG (Codex P1). `utfall == "avbrutt"
    # and art == 0` var sant også når claimen hadde tatt et ANNET oppdrag:
    # «ingen promoterte artefakter» er trivielt sant for et oppdrag ingen
    # rørte, og `avbrutt` kom fra den andre kjøringen. Kravet er derfor at
    # NETTOPP `oid` endte `feilet` med kvittering — da kan ikke en lekket
    # kø gi et grønt kryss, den gir et rødt.
    evidens("feilinjisering_motorfeil", utfall=res.get("utfall"),
            oppdrag=oid, oppdragstatus=rad[0], har_kvittering=rad[1],
            promoterte_artefakter=art,
            ok=res.get("utfall") == "avbrutt" and art == 0
            and rad[0] == "feilet" and bool(rad[1]))

    # Evidensfrist → reaper → M-37-sak, oppdrag feilet (port 22/23).
    kropp2 = {"bestillingstype": OPPDRAGSTYPE, "hostname": VERT,
              "sti": "/om.html", "kravsett": "wcag21_aa",
              "omfang": "enkeltside"}
    r2 = _bestill(http, cookie, csrf, kropp2, _idem("f7-frist", kropp2))
    r2.raise_for_status()
    oid2 = r2.json()["oppdrag_id"]
    _kontekst(m, TENANT)
    m.execute("ALTER TABLE oppdrag DISABLE TRIGGER oppdrag_laas")
    m.execute("UPDATE oppdrag SET evidensfrist=now()-interval '1 minute',"
              " utforelsesfrist=now()-interval '2 minutes'"
              " WHERE tenant=%s AND id=%s", (TENANT, oid2))
    m.execute("ALTER TABLE oppdrag ENABLE TRIGGER oppdrag_laas")
    m.commit()
    # Migrator har ikke EXECUTE på reaperen — kall den som eieren
    # (claimer), samme SET ROLE-mønster som testene bruker.
    m.execute("SET LOCAL ROLE disponit_m37_claimer")
    reapet = m.execute("SELECT tenant, oppdrag_id, unntak_id FROM"
                       " reap_evidensfrister(50)").fetchall()
    m.execute("RESET ROLE")
    m.commit()
    _kontekst(m, TENANT)
    sak = m.execute("SELECT u.arsak, u.sakstype, o.status FROM unntak u"
                    " JOIN oppdrag o ON o.tenant=u.tenant AND"
                    " o.id=u.oppdrag_id WHERE u.tenant=%s AND"
                    " u.oppdrag_id=%s", (TENANT, oid2)).fetchone()
    m.rollback()
    evidens("feilinjisering_evidensfrist", oppdrag=oid2,
            reapet=[x[1] for x in reapet], sak=sak,
            ok=bool(sak) and sak[0] == "evidensfrist"
            and sak[2] == "feilet")


# ---------------------------------------------------------------------------
# Fase 8 — rollback-drill (uten migrasjonsdelta)
# ---------------------------------------------------------------------------

def fase8():
    aktiv = Path("/opt/disponit/aktiv")
    naa = aktiv.resolve()
    katalog = Path("/opt/disponit/releases")
    releaser = sorted(katalog.iterdir(),
                      key=lambda p: p.stat().st_mtime) if katalog.is_dir() \
        else []
    forrige = [p for p in releaser if p != naa]
    # …OG MÅLET MÅ VÆRE MIGRASJONSKOMPATIBELT (runde r20, 2026-08-21).
    # Bootporten (`krev_migrasjonstilstand`) krever EKSAKT samsvar
    # mellom releasens migrasjonssett og basens — det er porten som
    # virker. Rett etter en deploy som la til migrasjoner finnes det
    # derfor ingen forrige release som KAN boote, og fase 8 valgte
    # offeret blindt: 788bd83 (forventer 1→51) mot basen på 1→53 ga
    # activating → failed, en rød måling av bootportens korrekthet i
    # stedet for av rullbakken. Målet velges nå blant releasene med
    # SAMME migrasjonssett som den aktive (filnavnene i
    # platform/core/db/migrations — samme fasit bootporten leser), og
    # finnes ingen, er drillen ÆRLIG umålt-rød: fyll katalogen med en
    # kompatibel release (neste deploy på samme skjema) og mål igjen.
    def _migrasjonssett(rot: Path) -> tuple:
        katalog = rot / "platform/core/db/migrations"
        return tuple(sorted(p.name for p in katalog.glob("*.sql"))) \
            if katalog.is_dir() else ()
    fasit_sett = _migrasjonssett(naa)
    kompatible = [p for p in forrige if _migrasjonssett(p) == fasit_sett]
    inkompatible = [p.name[:12] for p in forrige if p not in kompatible]
    if not forrige:
        # EN DRILL SOM IKKE KAN KJØRES ER EN RØD MÅLING (Codex P1, runde 6).
        # Grenen førte `fase8_hoppet` UTEN `ok`, så `_ROEDE` forble tom og
        # fase 9 satte arbeideren i drift på en runde der akseptpunktet
        # «forrige release opp og tilbake» aldri ble målt. Er
        # release-katalogen tom eller ryddet, vet runden nettopp ikke det
        # den skal vite — og «ikke målt» skal aldri se ut som «målt grønt».
        # Fyll katalogen (deploy én release til) og kjør `--fase 8` om
        # igjen; det er en forutsetning for drillen, ikke et unntak fra den.
        evidens("fase8_hoppet",
                grunn="ingen forrige release å rulle tilbake til",
                katalog=str(katalog), releaser=[p.name[:12] for p in releaser],
                merknad="rollback-drillen er et akseptpunkt: uten en"
                        " forrige release er den UMÅLT, ikke bestått",
                ok=False)
        return
    if forrige and not kompatible:
        evidens("fase8_hoppet",
                grunn="ingen migrasjonskompatibel forrige release",
                aktiv=naa.name[:12], inkompatible=inkompatible,
                merknad="bootporten krever eksakt migrasjonssamsvar, så"
                        " ingen av de forrige KAN boote mot denne basen"
                        " — rullbakken er UMÅLT, ikke bestått; deploy en"
                        " release til på samme skjema og mål igjen",
                ok=False)
        return
    forrige = kompatible[-1]

    def _pek(mal: Path) -> None:
        subprocess.run(["ln", "-sfn", str(mal), str(aktiv)], check=True)

    def _restart_og_status() -> str:
        """-> systemd-statusen etter restart, MÅLT TIL TERMINAL tilstand.

        Restarten er MÅLINGEN, ikke en forutsetning: `check=True` her
        ville kastet før statusen ble lest, og en release som ikke
        starter er nettopp utfallet drillen skal fange.

        To lærdommer fra r20-runden: (1) `activating` er ingen dom —
        et fast `sleep(4)` målte klokka, ikke releasen; det polles til
        tilstanden er terminal (systemd restarter selv med backoff, så
        vinduet må romme den). (2) `reset-failed` FØRST: etter en
        feilet måling står start-rate-grensen igjen, og uten nullstilling
        ville GJENOPPRETTINGEN truffet «Start request repeated too
        quickly» og latt staging ligge nede — drillen skal aldri
        etterlate miljøet i tilstanden den tester.
        """
        subprocess.run(["systemctl", "reset-failed",
                        "disponit-api.service"], capture_output=True)
        subprocess.run(["systemctl", "restart", "disponit-api.service"],
                       capture_output=True)
        frist = time.monotonic() + 45
        while time.monotonic() < frist:
            st = subprocess.run(
                ["systemctl", "is-active", "disponit-api.service"],
                capture_output=True, text=True).stdout.strip()
            if st not in ("activating", "deactivating", "reloading"):
                return st
            time.sleep(1)
        return st

    # GJENOPPRETTINGEN LIGGER I `finally` (Codex P2). Klarte ikke forrige
    # release å starte, kastet `check=True` med én gang, og `aktiv` ble
    # stående og peke på den ØDELAGTE releasen — altså nøyaktig den
    # feilen drillen finnes for å måle, forvandlet til varig skade på
    # staging. En drill som kan etterlate miljøet i tilstanden den
    # tester, er ikke en drill.
    #
    # Evidenslinja skrives også fra `finally`: en drill som avbrytes uten
    # å si hva den rakk å måle, er en måling som forsvant.
    st1 = "ikke_maalt"
    try:
        _pek(forrige)
        st1 = _restart_og_status()
    finally:
        st2 = "ikke_gjenopprettet"
        try:
            _pek(naa)
            st2 = _restart_og_status()
        finally:
            evidens("fase8_rollback", forrige=forrige.name[:12],
                    forrige_status=st1, tilbake_status=st2,
                    ok=st1 == "active" and st2 == "active")


# ---------------------------------------------------------------------------
# Fase 9 — arbeideren i drift
# ---------------------------------------------------------------------------

def _som_arbeideren(argv) -> list[str]:
    """`argv` kjørt SOM arbeideren, med unitens egne stier.

    Rootless podman leser bildelageret under brukerens `$HOME` og
    kjøretidsting under `XDG_RUNTIME_DIR` — systemd setter begge for
    `User=`-uniten, så både importen og enhver kontroll av den effektive
    motoren må bruke NØYAKTIG de samme verdiene. Gjør de det ikke, ser vi
    i et annet lager enn det arbeideren selv slår opp i."""
    import pwd
    hjem = pwd.getpwnam(ARBEIDER_BRUKER).pw_dir
    return ["runuser", "-u", ARBEIDER_BRUKER, "--", "env", f"HOME={hjem}",
            f"XDG_RUNTIME_DIR=/run/{ARBEIDER_BRUKER}", *argv]


def _arbeiderpodman(*argv) -> list[str]:
    """`podman ...` kjørt SOM arbeideren — se `_som_arbeideren`."""
    return _som_arbeideren(["podman", *argv])


def _motorforspann(motor_argv) -> list[str] | None:
    """Kommandokonteksten en motorkommandos image må slås opp MED.

    -> alt FORAN `run`-underkommandoen, ellers None.

    Kjøretiden og forspannet ER en del av oppslaget (Codex P2): `docker`,
    rootless `podman` og `nerdctl` har hvert sitt lager, og
    `WCAG_DRIFT_MOTOR` kan bære et hvilket som helst forspann — også et
    som peker podman mot et annet lager. Slår vi opp imaget med en annen
    kjøretid enn kommandoen selv bruker, ser vi i et lager kommandoen
    aldri rører: da finnes imaget «ikke», og fase 9 stenger døren på en
    helt gyldig motor.

    DE GLOBALE OPSJONENE HØRER MED (Codex P2, runde 3): lageret velges
    ikke bare av kjøretidens navn, men av opsjonene MELLOM kjøretiden og
    underkommandoen — `podman --root /annet-lager run … <image>` slår opp
    i `/annet-lager`, `docker --context …` og `nerdctl --namespace …`
    tilsvarende. Kuttet vi ved selve kjøretidsleddet, ble
    `--root /annet-lager` kastet og begge oppslagene spurte
    standardlageret om et image som bare finnes i det andre: samme feil
    ett hakk finere, altså at en gyldig release stenges ute.

    Snittet går derfor ved `run` — `_kjoretidsledd` har allerede
    fastslått at leddet finnes etter kjøretiden — og alt foran følger
    med, uansett hva overstyringen fant på å sette der."""
    i = _kjoretidsledd(motor_argv)
    if i is None:
        return None
    return _som_arbeideren(motor_argv[:motor_argv.index("run", i + 1)])


def _effektiv_motorimage(motor_argv) -> tuple[str, str]:
    """Image-ID-en den EFFEKTIVE motorkommandoen kjører (Codex P2, runde 6).

    -> (image-id uten `sha256:`, forklaring). Tom id = kunne ikke leses.

    `WCAG_DRIFT_MOTOR` overstyrer hele kommandoen, men serverkonteksten
    ble likevel avledet av stagingkommandoen og dens digest. Pekte
    overstyringen på et annet image eller en annen nettleser, attesterte
    HVER produksjonsrapport stagingimaget i stedet for motoren som
    faktisk kjørte — proveniens som ser presis ut og er feil.

    Oppslaget gjøres med kommandoens EGEN kjøretid og i arbeiderens eget
    lager, altså der uniten selv ville funnet imaget."""
    if not motor_argv:
        return "", "tom motorkommando"
    forspann = _motorforspann(motor_argv)
    if forspann is None:
        return "", "motorkommandoen er ingen containerkjøring"
    ut = subprocess.run([*forspann, "image", "inspect", "--format",
                         "{{.Id}}", motor_argv[-1]],
                        capture_output=True, text=True)
    if ut.returncode != 0:
        return "", ("image inspect feilet:"
                    f" {(ut.stderr or ut.stdout).strip()[-200:]}")
    return ut.stdout.strip().split(":")[-1], "lest i arbeiderens eget lager"


#: Konfigfeltene som IKKE er atferd, men avtrykk av BYGGECONTAINEREN:
#: docker fører dem med fra det gamle containerformatet (`Image` er
#: forelderens ref, `Hostname`/`Domainname`/`MacAddress` er defaults for
#: en container som aldri kjørte), podman skriver dem ikke i det hele
#: tatt. Ingen av dem endrer hva imaget kjører, og å sammenligne dem
#: ville stengt døren på et helt identisk image.
#:
#: Alt ANNET i konfigen sammenlignes — dette er en nektliste, ikke en
#: tillatelsesliste (Codex P1, runde 2). En tillatelsesliste er et hull
#: som vokser av seg selv: hvert felt noen glemmer — `Healthcheck`,
#: `Volumes`, `ExposedPorts`, `StopSignal`, eller et felt en fremtidig
#: motorversjon finner på — blir usynlig for gaten, og et image bygget
#: `FROM` releasen som bare legger til en helsesjekk eller et volum
#: passerer som «identisk». Ukjente felt teller derfor MED: de gjør et
#: ulikt image ulikt, ikke usynlig.
IKKE_ATFERD = frozenset({"Image", "Hostname", "Domainname", "MacAddress"})


def _normalisert(verdi):
    """Konfigverdien uten skrivemåteforskjeller mellom motorene.

    TOMT ER ÉN VERDI: «ingen entrypoint» skrives som `null`, `[]` eller
    et utelatt felt om hverandre, `Tty`/`ArgsEscaped` som `false` i
    docker og ingenting i podman, `StopTimeout` som `0` eller `null`.
    Uten denne sammenslåingen ville nektlista over måttet vokse med hvert
    slikt felt — og et FALSKT avvik her stenger døren på et image som er
    helt likt. Dicter sorteres (nøkkelrekkefølge er ikke atferd) og tomme
    SKALARER faller ut, så «feltet mangler» og «feltet er tomt» blir
    samme identitet.

    En tom BEHOLDER er derimot ikke tomhet: `Volumes` og `ExposedPorts`
    er mengder der meningen ligger i NØKKELEN og verdien alltid er `{}`.
    Faller de nøklene ut, er `{"/data": {}}` og «ingen volumer» samme
    identitet — og et image bygget `FROM` releasen som bare erklærer et
    volum passerer igjen som likeverdig. Nøkkelen beholdes derfor."""
    if isinstance(verdi, dict):
        d = {}
        for k in sorted(verdi):
            v = verdi[k]
            n = _normalisert(v)
            if n is not None or isinstance(v, (dict, list, tuple)):
                d[k] = n
        return d or None
    if isinstance(verdi, (list, tuple)):
        return [_normalisert(x) for x in verdi] or None
    return verdi if verdi else None


def _image_identitet(forspann, ref: str) -> dict | None:
    """Imagets identitet, lest med EN gitt kjøretidskontekst.

    -> `{"lag": diff_ids, "konfig": …, "plattform": …}`, ellers None.

    LAGKJEDEN ALENE ER IKKE IDENTITET (Codex P1): et image bygget `FROM`
    releasen som bare overstyrer `ENTRYPOINT`, `USER` eller `ENV` har
    NØYAKTIG samme diff_ids — lagene er de samme, det er konfigblobben
    over dem som er ny. En ren lagsammenligning godkjenner et slikt image
    som «innholdsidentisk», og da kan både importoppslaget og en
    `WCAG_DRIFT_MOTOR`-overstyring sette i drift en motor som kjører noe
    annet enn runden målte, med releasens digest på kvitteringen.

    Identiteten er derfor lagkjeden PLUSS HELE konfigen — alt unntatt
    `IKKE_ATFERD` (Codex P1, runde 2). En håndplukket liste over
    «atferdsfelt» glemte `Healthcheck` og `Volumes`: et image bygget
    `FROM` releasen som bare legger til en helsesjekk eller en
    volumdeklarasjon har samme lag og samme håndplukkede felt, men
    kjører en ekstra periodisk kommando og monterer et automatisk
    volum — og fase 9 satte det i drift som «likeverdig». Det er
    feltene som IKKE er atferd som må navngis, ikke de som er det.

    PLATTFORMEN LIGGER UTENFOR KONFIGEN (Codex P1, runde 3): `Os`,
    `Architecture` og `Variant` står på toppnivå i inspect-utdataen, ikke
    i `Config`, så et image med samme lag og samme konfig men bygget for
    en annen arkitektur var identisk for gaten. Kjøretiden bruker
    nettopp de feltene til å velge kompatibilitet og emulering: et
    arm64-image i et amd64-lager blir enten emulert — altså en annen
    kjøring enn runden målte — eller feiler på hver eneste motorstart,
    mens arbeideren melder seg klar og claimer arbeid først. Plattformen
    er derfor en del av identiteten.

    Verdiene normaliseres (`_normalisert`) fordi de to motorene skriver
    tomhet ulikt, og en falsk ulikhet her stenger døren på et image som
    er helt likt."""
    ut = subprocess.run([*forspann, "image", "inspect", "--format",
                         "{{json .}}", ref], capture_output=True, text=True)
    if ut.returncode != 0:
        return None
    try:
        d = json.loads(ut.stdout or "null")
    except ValueError:
        return None
    if isinstance(d, list):
        d = d[0] if d else None
    if not isinstance(d, dict):
        return None
    lag = (d.get("RootFS") or {}).get("Layers") or []
    if not lag:
        return None
    kfg = dict(d.get("Config") or {})
    # HELSESJEKKEN STÅR TO STEDER: docker legger den i `Config`, podman
    # løfter den til toppnivå i inspect-utdataen. Samme atferd — en
    # periodisk kommando i containeren — men leses den bare ett sted, ser
    # et image med helsesjekk ULIKT ut i de to motorene, og gaten stenger
    # døren på releasen selv.
    for navn in ("Healthcheck", "HealthCheck"):
        if not kfg.get("Healthcheck") and d.get(navn):
            kfg["Healthcheck"] = d[navn]
    konfig = {}
    for felt in sorted(kfg):
        if felt in IKKE_ATFERD:
            continue
        # LISTENE SAMMENLIGNES I REKKEFØLGE, også `Env` (Codex P2, runde
        # 3): OCI krever ikke unike navn i miljøtabellen, og prosessen
        # ser den slik den står — `getenv` treffer den første oppføringen
        # og `environ` kan leses i sin helhet. Sortert ble
        # `['MODE=safe', 'MODE=unsafe']` og den omvendte rekkefølgen
        # samme identitet, altså to ULIKE kjøringer godkjent som én.
        # Rekkefølgen kommer fra konfigblobben og er den samme i begge
        # motorene, så den koster ingen falsk ulikhet.
        n = _normalisert(kfg[felt])
        if n is not None:
            konfig[felt] = n
    # PLATTFORMEN STÅR PÅ TOPPNIVÅ, ikke i konfigen — se docstringen.
    plattform = {}
    for felt in ("Os", "Architecture", "Variant"):
        n = _normalisert(d.get(felt))
        if n is not None:
            plattform[felt] = n
    return {"lag": lag, "konfig": konfig, "plattform": plattform}


def _docker_identitet(digest: str) -> dict | None:
    """Releasens identitet, lest i den rootfulle daemonen."""
    return _image_identitet(["docker"], digest)


def _arbeider_identitet(ref: str) -> dict | None:
    """Samme identitet, lest i ARBEIDERENS lager."""
    return _image_identitet(_som_arbeideren(["podman"]), ref)


def _importer_motorimage(digest: str) -> tuple[str, str]:
    """Motorimaget inn i ARBEIDERENS podman-lager. -> (arbeider-id, notat).

    Tom id = imaget er ikke tilgjengelig for arbeideren.

    IDENTITETEN OVERLEVER IKKE TRANSPORTEN (målt på disponit-srv, docker
    29/containerd-lager): `docker save | podman load` ga et image med
    IDENTISK innhold — samme Created på nanosekundet, samme ni lag — men en
    ANNEN image-ID, fordi containerd-snapshotteren skriver konfigen om ved
    eksport og podman regner ID av det den får. Forrige utgave sammenlignet
    ID-er og konkluderte «imaget er ikke i lageret» om et innholdsidentisk
    image som sto der — og hver slik runde brente en release.

    Identiteten som faktisk overlever er LAGKJEDEN (RootFS diff_ids:
    innholdsdigester av de ukomprimerte lagene, samme verdi i begge
    motorer) SAMMEN MED atferdskonfigen — se `_image_identitet`: lagene
    alene skiller ikke releasen fra et image bygget `FROM` den som bare
    overstyrer entrypoint, bruker eller miljø. Oppslag og verifikasjon
    går derfor på begge deler, og funksjonen
    returnerer ARBEIDERENS egen ID — det er den motorkommandoen og
    kvitteringenes `container_image_digest` må bære, for det er den som
    faktisk kjører. Releasens `artifact_digest` (dockers ID) består som
    byggeidentitet; fase 9-evidensen fører BEGGE og lagkjede-beviset som
    binder dem.
    """
    subprocess.run(["install", "-d", "-m", "700", "-o", ARBEIDER_BRUKER,
                    "-g", ARBEIDER_BRUKER, f"/run/{ARBEIDER_BRUKER}"],
                   check=True)
    ventet = _docker_identitet(digest)
    if not ventet:
        return "", "docker image inspect ga ingen identitet for releasen"

    def _finn() -> str:
        ls = subprocess.run(_arbeiderpodman("image", "ls", "-q", "--no-trunc"),
                            capture_output=True, text=True)
        for iid in dict.fromkeys(filter(None, ls.stdout.split())):
            if _arbeider_identitet(iid) == ventet:
                return iid.split(":")[-1]
        return ""

    fantes = _finn()
    if fantes:
        return fantes, "alt i lageret (identisk lagkjede og konfig)"
    ror = subprocess.run(
        ["bash", "-o", "pipefail", "-c",
         # `sha256:`-prefikset er IKKE pynt: `docker save` (i motsetning til
         # `docker image inspect`) avviser en naken 64-hex-ID eksplisitt —
         # «cannot specify 64-byte hexadecimal strings» — så importen feilet
         # på HVER kjøring og fase 9 brente en release per forsøk. Funnet ved
         # å kjøre `docker save` for hånd da røret ga tom stdin til podman.
         f"docker save sha256:{shlex.quote(digest.split(':')[-1])} | "
         + shlex.join(_arbeiderpodman("load"))],
        capture_output=True, text=True)
    if ror.returncode != 0:
        return "", f"import feilet: {(ror.stderr or ror.stdout)[-300:]}"
    etter = _finn()
    if not etter:
        return "", ("lastet uten feil, men intet image i lageret etterpå har"
                    " releasens lagkjede OG konfig")
    return etter, "importert"


def _tokenet_er_autorisert(m, mtk: str) -> tuple[bool, dict]:
    """Er tokenet arbeideren skal få FAKTISK autorisert? (Codex P1, runde 12)

    -> (dom, detaljer til evidensfila).

    Fase 9 måler at uniten kom opp ved å lete etter `wcag_arbeider_oppe` i
    journalen — men arbeideren skriver den linja FØR sitt første
    autentiserte claim. Et tilbakekalt eller feil-release-token gir derfor
    en grønn `fase9_arbeider_i_drift` og en arbeider som svarer 401 på
    hvert eneste claim resten av sin levetid. `TOKEN_FIL` binder nå tokenet
    til sin release, men fila er ikke autoriteten: modulen kan ha vært
    nødstoppet etterpå (epoch bumpet, hele tokenfamilien tilbakekalt) uten
    at release-id-en endret seg.

    Dommen felles derfor av plattformens EGNE porter, de samme to som
    claim-veien bruker: `verifiser_modultoken` (finnes tokenet, er det
    ikke tilbakekalt eller utløpt) og `modultoken_fortsatt_autorisert`
    (er modulen aktiv, stemmer epochen, er identiteten fortsatt denne
    deploymenten). En egen kopi av regelen her ville drevet fra claimens.

    ... OG DEPLOYMENTENS LIVSLØP (Codex P1, runde 15). De to funksjonene er
    ikke HELE claim-porten. `modultoken_fortsatt_autorisert` ser med vilje
    ikke livsløpet — en `draining` deployment skal få levere arbeid den alt
    har claimet — så claim-veien sjekker i tillegg at raden er `claiming`
    før den tildeler NYTT arbeid. Uten den siste sjekken her passerte en
    beholdt runde som ble kjørt om igjen med sin originale release etter at
    `bytt_release` hadde drenert den: release og token stemte, dommen var
    `ok`, og hvert eneste claim fikk `modul_ikke_claimbar` (403).

    Kan dommen ikke felles — oppslaget feiler, tokenet har ikke
    wire-formen — er svaret NEI. En umålt port er ikke en bestått port,
    og prisen for et falskt nei er en runde til, mens prisen for et falskt
    ja er en arbeider i drift som ikke kan utføre noe.
    """
    if not mtk.startswith("mtk_"):
        return False, {"grunn": "ikke et modultoken (mangler mtk_-prefikset)"}
    tid, _, hemmelig = mtk[4:].partition(".")
    if not tid or not hemmelig:
        return False, {"grunn": "modultokenet har ikke formen"
                                " mtk_<token_id>.<hemmelighet>"}
    pepper = _miljo().get("DISPONIT_TOKEN_PEPPER") or os.environ.get(
        "DISPONIT_TOKEN_PEPPER", "")
    mac = hmaclib.new(pepper.encode(), hemmelig.encode(),
                      hashlib.sha256).hexdigest()
    try:
        # Funksjonene eies av `disponit_modul_eier`; migrator har hverken
        # SELECT på tabellene eller EXECUTE utenom eierrollen.
        m.execute("SET LOCAL ROLE disponit_modul_eier")
        rad = m.execute(
            "SELECT token_id, modul_id, miljo, release_id, utstedt_epoch"
            "  FROM verifiser_modultoken(%s)", (mac,)).fetchone()
        dom = m.execute(
            "SELECT modultoken_fortsatt_autorisert(%s,%s,%s,%s,%s)",
            (rad[0], rad[1], rad[2], rad[3], rad[4])).fetchone()[0] \
            if rad is not None else None
    except psycopg.Error as e:
        m.rollback()
        return False, {"grunn": "tokenoppslaget feilet",
                       "feiltype": type(e).__name__, "feil": str(e)[:200]}
    m.rollback()
    if rad is None:
        return False, {"grunn": "tokenet er ukjent, tilbakekalt eller"
                                " utløpt — gjør onboardingen (fase 4) om"}
    # Wire-formatets id må matche radens, samme krav som `preauth`: en
    # gjettet id skal ikke kunne pares med en MAC fra et annet token.
    identitet = {"token_id": str(rad[0]), "modul": rad[1], "miljo": rad[2],
                 "token_release": rad[3], "utstedt_epoch": rad[4], "dom": dom}
    if str(rad[0]) != tid:
        return False, {**identitet,
                       "grunn": "token-id-en i tokenet matcher ikke radens"}
    if (rad[1], rad[2], rad[3]) != (MODUL, "staging", RELEASE):
        return False, {**identitet,
                       "grunn": "tokenet tilhører en annen deployment enn"
                                f" ({MODUL}, staging, {RELEASE})"}
    if dom != "ok":
        return False, {**identitet,
                       "grunn": f"plattformens egen port sier {dom!r}"}
    # ... OG DEPLOYMENTEN MÅ VÆRE `claiming` (Codex P1, runde 15).
    # `modultoken_fortsatt_autorisert` leser med VILJE ikke livsløpet: en
    # `draining` deployment SKAL få levere resultatet av arbeid den alt har
    # claimet, så den svarer `ok` for den. Claim-veien legger derfor på én
    # sjekk til (`_modulporten` i app-laget): raden for (modul, miljø,
    # release) må være `claiming`, ellers 403 `modul_ikke_claimbar`.
    # Kjøres en beholdt runde om igjen med sin ORIGINALE release etter at
    # `bytt_release` har drenert den, er både release og token de riktige —
    # og porten her sa `ok` mens hvert NYTT claim fikk 403. Det er den
    # samme feilen runde 12 fant, én dør lenger inn: en arbeider som melder
    # seg oppe og aldri får ett eneste oppdrag.
    try:
        drad = m.execute(
            "SELECT livslop FROM moduldeployment"
            " WHERE modul_id=%s AND miljo=%s AND release_id=%s",
            (MODUL, "staging", RELEASE)).fetchone()
    except psycopg.Error as e:
        m.rollback()
        return False, {**identitet, "grunn": "deploymentoppslaget feilet",
                       "feiltype": type(e).__name__, "feil": str(e)[:200]}
    m.rollback()
    livslop = drad[0] if drad is not None else "borte"
    identitet = {**identitet, "livslop": livslop}
    if livslop != "claiming":
        return False, {**identitet,
                       "grunn": f"deploymentens livsløp er {livslop!r}, ikke"
                                " 'claiming' — claim svarer"
                                " modul_ikke_claimbar (403)"}
    return True, identitet


def _steng_doeren(m, grunn: str) -> None:
    """Modulen ut av CLAIMBAR tilstand igjen (Codex P1, runde 6).

    Fase 2 setter modulen `aktiv` med en `claiming`-deployment, og fra da
    tar `/v1/bestilling` imot ekte `kontroll.wcag.nettsted`-oppdrag på
    denne basen. Fase 9 kunne så returnere uten å sette arbeideren i drift
    — rød måling, manglende rootless-forutsetninger, manglende motorimage
    — og lot døren stå åpen bak seg. Oppdrag som kommer inn da, blir
    liggende `opprettet` til utførelsesfristen løper ut: kunden får et
    kvittert JA på noe ingen kan utføre. En åpen dør uten noen bak den er
    verre enn en lukket.

    Stengingen er plattformens EGEN gjerdede vei, ikke en UPDATE herfra:
    `noddeaktiver_modul` setter status `nodeaktivert`, bumper
    `module_epoch` og drenerer HVER claiming-deployment i én
    transaksjon — nøyaktig de to vilkårene både bestillingsvakta og
    `claim_neste_oppdrag` krever. Den er idempotent og krever en
    begrunnelse, som føres i modulregisterets hendelsesstrøm.

    VEIEN TILBAKE er dermed også plattformens: `reaktiver_modul` (epoch-
    gjerdet) og en NY release-id, siden en drenert deployment ikke kan
    reclaimes. Det er riktig pris: en runde som gikk rødt skal ikke kunne
    åpne den samme døren igjen ved et uhell.
    """
    try:
        with m.cursor() as c:
            c.execute("SET ROLE disponit_modules_admin")
            c.execute("SELECT noddeaktiver_modul(%s,%s,'wcag-runde')",
                      (MODUL, f"staging-sjekkliste: {grunn}"))
            c.execute("RESET ROLE")
        m.commit()
    except psycopg.Error as e:
        m.rollback()
        # Klarte vi ikke å stenge, er nettopp DET den røde målingen: døren
        # står åpen og evidensfila må si det, ikke tie om det.
        evidens("fase9_doeren_star_apen", grunn=grunn,
                feiltype=type(e).__name__, feil=str(e)[:200],
                merknad="modulen er fortsatt claimbar — steng den for hånd:"
                        " SELECT noddeaktiver_modul(...)",
                ok=False)
        return
    status = m.execute("SELECT status FROM modulhode WHERE modul_id=%s",
                       (MODUL,)).fetchone()
    claiming = m.execute("SELECT count(*) FROM moduldeployment"
                         " WHERE modul_id=%s AND livslop='claiming'",
                         (MODUL,)).fetchone()[0]
    m.rollback()
    # Stengingen er SELV en måling: står modulen igjen som claimbar, er det
    # utfallet denne funksjonen finnes for å hindre.
    evidens("fase9_modul_deaktivert", grunn=grunn,
            status=status[0] if status else None, claiming=claiming,
            vei_tilbake="reaktiver_modul(modul, epoch, aktor) + ny release-id",
            ok=status == ("nodeaktivert",) and claiming == 0)


def _aapne_traversering(forelder: Path) -> bool:
    """Gir arbeidergruppen x (ikke r) på katalogen `DRIFT_KONF` ligger i.

    KUN på en forelder denne runden eier: standarden `/etc/disponit`, eller
    en overstyrt forelder som ikke fantes fra før og som vi derfor oppretter
    selv. `WCAG_DRIFT_KONF` er en prøvekjøringsbryter, og en bryter skal
    ikke kunne skrive om rettighetene på en katalog som alt er i bruk
    (Codex P1): `/tmp/wcag` ville tatt `/tmp` fra 1777 til 0710 og stengt
    verten ute av sin egen temp-katalog, `/wcag` ville gjort det med `/`.
    Overstyrer man inn i en katalog som fins, eier man traverseringen selv.
    """
    if forelder != DRIFT_KONF_STANDARD.parent and forelder.exists():
        evidens("fase9_forelder_urort", forelder=str(forelder),
                grunn="overstyrt sti som fantes fra før — rettighetene der"
                      " tilhører ikke denne runden",
                merknad="arbeideren når bare frem hvis den som satte"
                        " WCAG_DRIFT_KONF selv har gitt gruppa x her")
        return False
    # `install -d` retter modus og gruppe også på en katalog som fins —
    # samme grep som oppsett-postgresql.sh gjør per deploy, i én kommando.
    subprocess.run(["install", "-d", "-m", "710", "-o", "root",
                    "-g", ARBEIDER_BRUKER, str(forelder)], check=True)
    return True


def fase9(m, mtk, digest, *, maalt_runde: bool):
    """Setter arbeideren I DRIFT — ellers claimer ingen noe (Codex P1).

    Fase 2 setter modulen `aktiv` og deploymentet `claiming`. FRA DA er
    `/v1/bestilling` åpen for `kontroll.wcag.nettsted` på denne basen, og
    ekte oppdrag kan legges i køen. Men runden claimet dem SELV, synkront,
    med `_kontroller_kjor` — og `disponit-wcag-audit.service` ble aldri
    provisjonert eller enablet noe sted: `opp.sh` starter den bare hvis den
    ALT var enablet, fase 4 skriver modultokenet under `RUNDE` (/root, som
    `disponit-wcag` ikke kan lese) i stedet for unitens credential-sti, og
    verken kvitteringsnøkkelen eller serverkonteksten hadde en varig plass.
    Når de synkrone fasene var ute, stod køen altså tom for arbeidere: ekte
    oppdrag ville ligget `opprettet` til utførelsesfristen løp ut. En modul
    som er `aktiv` uten en arbeider er en åpen dør uten noen bak den.

    Materialiseringen er unitens kontrakt, ikke rundens: tokenet som
    `LoadCredential`, resten som filer `disponit-wcag` faktisk kan lese, og
    motorkommandoen som den ROOTLESSE podman-kommandoen bygg.sh og
    unit-hodet dokumenterer — aldri rundens docker-kommando, som bærer
    fixture-bryterne `MOTOR_TLS_USIKKER`/`MOTOR_VERTSKART`.

    GATEN: en full runde åpner døren bare når INGEN måling ble rød —
    `_ROEDE` er det runden selv har målt, ikke et menneskes minne om det.
    Er noe rødt, provisjoneres ingenting og uniten enables ikke; «ingen
    arbeider» er da det riktige utfallet, ikke et uhell. `--fase 9` alene
    måler ingenting og er operatørens BEVISSTE idriftsettelse etter at
    evidensfila er lest — den føres som det i evidensen.

    ... OG DA STENGES DØREN (Codex P1, runde 6). Å la være å enable
    arbeideren er ikke det samme som å la være å åpne: fase 2 har alt
    gjort modulen claimbar, og et hopp her etterlot en base som tar imot
    ekte oppdrag ingen kan utføre. Hver gren som returnerer uten en
    arbeider i drift ruller derfor tilbake det fase 2 åpnet — se
    `_steng_doeren`.

    OG TOKENET MÅ VÆRE AUTORISERT (Codex P1, runde 12). Klarhetsmålingen
    til slutt leser `wcag_arbeider_oppe`, som arbeideren skriver FØR sitt
    første autentiserte claim: et tilbakekalt token — eller et fra en
    tidligere release, som gjenopprettingen etter et nødstopp nødvendigvis
    etterlater — ga en grønn idriftsettelse av en arbeider som svarer 401
    på hvert oppdrag. Se `_tokenet_er_autorisert`.
    """
    if _ROEDE:
        evidens("fase9_hoppet", grunn="røde målinger i runden",
                roede=sorted(set(_ROEDE)),
                merknad="arbeideren settes ikke i drift på en runde som"
                        " ikke er grønn, og modulen stenges igjen")
        _steng_doeren(m, "røde målinger i runden")
        return
    if not mtk:
        evidens("fase9_hoppet", grunn="ingen modultoken i denne kjøringen")
        _steng_doeren(m, "ingen modultoken — ingen arbeider kan settes opp")
        return
    if not maalt_runde:
        evidens("fase9_operatoerbeslutning",
                merknad="--fase 9 målte ingenting selv; idriftsettelsen"
                        " hviler på evidensfila fra den grønne runden")

    # TOKENET MÅ VÆRE AUTORISERT, IKKE BARE FINNES (Codex P1, runde 12).
    # `wcag_arbeider_oppe` skrives før arbeiderens FØRSTE autentiserte
    # claim, så klarhetsmålingen nedenfor kan ikke skille en arbeider som
    # gjør jobben fra en som får 401 på hvert oppdrag. Dommen hentes
    # derfor der claimen selv henter den, før uniten enables.
    autorisert, detalj = _tokenet_er_autorisert(m, mtk)
    evidens("fase9_modultoken_autorisert", release=RELEASE, **detalj,
            ok=autorisert)
    if not autorisert:
        evidens("fase9_hoppet",
                grunn="modultokenet er ikke autorisert for denne releasen",
                merknad="uniten enables ikke: arbeideren ville meldt seg"
                        " oppe og så fått 401 på hvert eneste claim")
        _steng_doeren(m, "modultokenet er ikke autorisert for denne releasen")
        return

    reg = json.loads(ATT_FIL.read_text())
    nid = sorted(reg["v_wcag_audit"])[0]

    # ROOTLESS-FORUTSETNINGENE MÅLES FØR UNITEN ENABLES (Codex P1).
    # `opp.sh` deler ut subuid/subgid til `disponit-wcag` og advarer om
    # manglende `newuidmap`/`newgidmap`. Står en av delene igjen, bygger
    # ikke podman namespacet, og arbeideren feiler på HVER kontroll — en
    # unit som er enablet og oppe, men som ikke kan gjøre jobben sin.
    # Målingen hører derfor til her, som alt annet fase 9 påstår.
    subid = {f: any(l.startswith(ARBEIDER_BRUKER + ":")
                    for l in Path(f).read_text().splitlines())
             if Path(f).exists() else False
             for f in ("/etc/subuid", "/etc/subgid")}
    hjelpere = {h: subprocess.run(["sh", "-c", f"command -v {h}"],
                                  capture_output=True).returncode == 0
                for h in ("newuidmap", "newgidmap")}
    klar = all(subid.values()) and all(hjelpere.values())
    evidens("fase9_rootless_forutsetninger", subid=subid, hjelpere=hjelpere,
            krav="subuid/subgid for " + ARBEIDER_BRUKER + " + uidmap-pakken",
            ok=klar)
    if not klar:
        evidens("fase9_hoppet",
                grunn="rootless-forutsetningene mangler — kjør opp.sh"
                      " (subuid/subgid) og installer pakken uidmap",
                merknad="uniten enables ikke: en arbeider som ikke kan"
                        " starte motoren er verre enn ingen arbeider")
        _steng_doeren(m, "rootless-forutsetningene mangler")
        return

    # ... og IMAGET må ligge i arbeiderens EGET lager — se
    # `_importer_motorimage`. Docker og rootless podman deler ingenting, og
    # ID-en overlever ikke transporten (docker 29): importen skjer FØR
    # motorkommandoen bygges, for kommandoen skal bære ARBEIDERENS id.
    arbeider_id, hvordan = _importer_motorimage(digest)
    evidens("fase9_motorimage", artifact_digest=digest.split(":")[-1],
            arbeider_image_id=arbeider_id or None,
            lager=f"rootless podman ({ARBEIDER_BRUKER})", utfall=hvordan,
            ok=bool(arbeider_id))
    if not arbeider_id:
        evidens("fase9_hoppet",
                grunn="motorimaget er ikke tilgjengelig for arbeideren",
                merknad="uniten enables ikke: den ville feilet hvert"
                        " eneste claimet oppdrag")
        _steng_doeren(m, "motorimaget mangler i arbeiderens lager")
        return

    motor = os.environ.get("WCAG_DRIFT_MOTOR") or shlex.join([
        "podman", "run", "--rm", "-i", "--network", "host",
        # MÅLT i drift: rootless podman i en systemtjeneste har ingen
        # dbus-økt, faller til cgroupfs-manageren og prøver å lage
        # containerens cgroup rett under system.slice — «permission
        # denied», og HVERT claimet oppdrag endte `avbrutt: Motorfeil`.
        # `--cgroups split` bruker unitens EGET delegerte subtre
        # (Delegate=yes + User= gjør at systemd chowner det til
        # arbeideren), så MOTORGRENSENE får virke. Verifisert med
        # systemd-run + Delegate=yes: grensene satt, container kjørte.
        "--cgroups", "split",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        # Nettleseren kjører kundens side: `MOTORGRENSER` er minnet og
        # prosesstabellen den kan bruke, unitens `CPUQuota=`/`MemoryMax=`
        # er backstoppet rundt hele arbeideren.
        *MOTORGRENSER,
        # ARBEIDERENS image-id, ikke taggen og ikke dockers: taggen er
        # mutabel, og dockers id finnes ikke i arbeiderens lager (docker
        # 29-transporten skriver konfigen om). Kvitteringene attesterer
        # nøyaktig denne id-en som container_image_digest — motoren som
        # faktisk kjører.
        arbeider_id])

    # KONTEKSTEN AVLEDES AV DEN MOTOREN SOM FAKTISK KJØRER (Codex P2,
    # runde 6). `miljo`-blokka ble bygget av stagingkommandoen og dens
    # digest, også når `WCAG_DRIFT_MOTOR` overstyrte hele kommandoen: pekte
    # overstyringen på et annet image eller en annen nettleser, attesterte
    # HVER produksjonsrapport stagingimaget i stedet for motoren som kjørte.
    #
    # Oppslaget skjer i arbeiderens eget lager, med kommandoens egen
    # kjøretid — og imaget må være NØYAKTIG det releaseraden bærer som
    # `artifact_digest` og målingene attesterte. Et annet image er ikke et
    # konteksproblem å skrive seg ut av: da er akseptmålingen gjort på noe
    # annet enn det som skal i drift, og døren stenges.
    # IDENTITETEN SOM SAMMENLIGNES ER IKKE ID-STRENGEN: dockers id
    # (releasens artifact_digest) og arbeiderens id er to navn på samme
    # image etter docker 29-transporten. Gaten binder den effektive motoren
    # til releasens INNHOLD (diff_ids — innholdsdigester som ikke kan
    # navngis om) OG til atferdskonfigen, for lagene alene skiller ikke
    # releasen fra et image bygget `FROM` den som overstyrer entrypoint,
    # bruker eller miljø — se `_image_identitet`.
    # Og identiteten leses med KOMMANDOENS EGEN kjøretid (Codex P2): id-en
    # over kom fra den effektive kommandoens `image inspect`, så gjør
    # oppslaget av lag og konfig det også. Leser vi i stedet alltid i
    # arbeiderens podman-lager, gir en `WCAG_DRIFT_MOTOR` på `docker`,
    # `nerdctl` eller et podman-forspann mot et annet lager ingen treff på
    # en id som inspiserte helt fint — og døren stenges på feil grunnlag.
    motor_argv = shlex.split(motor)
    forspann = _motorforspann(motor_argv)
    # Forspannet ender på de globale opsjonene, ikke på kjøretiden, så
    # kjøretidsnavnet til evidensen LETES opp — ellers hadde
    # `podman --root /annet-lager` blitt ført som lest med «annet-lager».
    kjt = _kjoretidsledd(motor_argv)
    drift_id, hvorfra = _effektiv_motorimage(motor_argv)
    ventet = _docker_identitet(digest)
    effektiv = (_image_identitet(forspann, drift_id)
                if drift_id and forspann else None)
    identisk = bool(drift_id) and ventet is not None and effektiv == ventet
    evidens("fase9_effektiv_motor", motor=motor, image_id=drift_id,
            artifact_digest=digest.split(":")[-1],
            identisk_lag_og_konfig=identisk, utfall=hvorfra,
            lest_med=Path(motor_argv[kjt]).name if kjt is not None else "",
            overstyrt=bool(os.environ.get("WCAG_DRIFT_MOTOR")),
            ok=identisk)
    if not identisk:
        evidens("fase9_hoppet",
                grunn="den effektive motoren er ikke imaget runden målte",
                merknad="uniten enables ikke: kvitteringene ville attestert"
                        " et image som aldri kjørte")
        _steng_doeren(m, "WCAG_DRIFT_MOTOR peker på et annet image")
        return
    # SAMME `miljo`-blokk som målingene attesterte — ett sted, én verdi —
    # men lest ut av den effektive kommandoen, ikke stagingens.
    kontekst = _serverkontekst(drift_id, _som_arbeideren(motor_argv))

    # FORELDEREN FØRST, og den må kunne traverseres (målt: 700 på
    # /etc/disponit ga PermissionError på kontekst.json — en fil arbeideren
    # hadde lesrett til, bak en katalog den ikke kunne gå gjennom). Samme
    # grep som oppsett-postgresql.sh gjør per deploy; her i tillegg fordi
    # fase 9 er idriftsettelsen og ikke skal avhenge av NESTE deploy.
    _aapne_traversering(DRIFT_KONF.parent)
    subprocess.run(["install", "-d", "-m", "750", "-o", "root",
                    "-g", ARBEIDER_BRUKER, str(DRIFT_KONF)], check=True)

    def _skriv(navn: str, innhold: str, *, lesbar_for_arbeideren: bool):
        """Filen skrives, så rettes rettighetene — i den rekkefølgen, slik
        at innholdet aldri ligger et øyeblikk med for åpne rettigheter."""
        sti = DRIFT_KONF / navn
        sti.write_text(innhold, encoding="utf-8")
        # `LoadCredential`/`EnvironmentFile` leses av systemd som root; det
        # arbeideren åpner SELV må gruppa nå.
        os.chmod(sti, 0o640 if lesbar_for_arbeideren else 0o600)
        if lesbar_for_arbeideren:
            subprocess.run(["chgrp", ARBEIDER_BRUKER, str(sti)], check=True)
        return sti

    # `LoadCredential=DISPONIT_MODULTOKEN:` peker på nøyaktig dette navnet.
    _skriv("DISPONIT_MODULTOKEN", mtk, lesbar_for_arbeideren=False)
    kontekst_sti = _skriv("kontekst.json",
                          json.dumps(kontekst, indent=1) + "\n",
                          lesbar_for_arbeideren=True)
    nokkel_sti = _skriv("kvitteringsnokkel.json", json.dumps(
        {"verifikator": "v_wcag_audit", "nokkel_id": nid,
         "hemmelighet": reg["v_wcag_audit"][nid]}, indent=1) + "\n",
        lesbar_for_arbeideren=True)
    _skriv("konfig", "".join(f"{k}={v}\n" for k, v in (
        ("DISPONIT_API_URL", API),
        ("DISPONIT_WCAG_MOTOR", motor),
        ("DISPONIT_WCAG_KONTEKST", str(kontekst_sti)),
        ("DISPONIT_KVITTERINGSNOKKEL", str(nokkel_sti)))),
        lesbar_for_arbeideren=False)

    subprocess.run(["systemctl", "enable", "--now", ARBEIDER],
                   capture_output=True)
    # OPPSTARTEN ER EN MÅLING, ikke en forutsetning: `check=True` her ville
    # kastet før vi fikk lest hva som skjedde, og en arbeider som nekter
    # oppstart (`oppstart_nektet` på manglende konfig) er nettopp utfallet
    # denne fasen finnes for å fange.
    time.sleep(6)
    aktiv = subprocess.run(["systemctl", "is-active", ARBEIDER],
                           capture_output=True, text=True).stdout.strip()
    logg = subprocess.run(["journalctl", "-u", ARBEIDER, "-n", "20",
                           "--no-pager", "-o", "cat"],
                          capture_output=True, text=True).stdout
    i_drift = aktiv == "active" and "wcag_arbeider_oppe" in logg
    # EN RØD KLARHETSMÅLING RULLER TILBAKE AKTIVERINGEN (Codex P1, runde 5).
    # `enable --now` er PERSISTENT: uniten overlever reboot og starter av
    # seg selv. Målingen over kommer etter, og en rød måling betyr nettopp
    # at vi ikke vet om arbeideren duger — den kan stå i `Restart=on-failure`
    # og prøve igjen hvert tiende sekund, eller bare ha brukt mer enn de
    # seks sekundene vi ga den. Begge deler ender med en arbeider som
    # SENERE claimer ekte oppdrag, på tvers av den målingen som skulle
    # gate aktiveringen. `_ROEDE` stopper resten av sjekklista, men den
    # rører ikke systemd.
    #
    # Derfor: er predikatet usant, stoppes og disables uniten igjen, og
    # tilbakerullingen er SELV evidens. Aktivering er en tilstand på
    # verten, ikke en linje i en rapport — den må rulles tilbake på verten.
    if not i_drift:
        av = subprocess.run(["systemctl", "disable", "--now", ARBEIDER],
                            capture_output=True, text=True)
        etterpaa = subprocess.run(["systemctl", "is-enabled", ARBEIDER],
                                  capture_output=True,
                                  text=True).stdout.strip()
        evidens("fase9_aktivering_rullet_tilbake", unit=ARBEIDER,
                grunn="klarhetsmålingen var rød",
                kommando="systemctl disable --now",
                returkode=av.returncode, er_enablet=etterpaa,
                # Tilbakerullingen må SELV kunne bli rød: blir uniten
                # stående enablet etter dette, er nettopp det utfallet
                # denne grenen finnes for å hindre.
                ok=etterpaa in ("disabled", "static", "masked"))
        # ... og døren stenges med den: en base som tar imot oppdrag uten
        # en arbeider bak, er den samme åpne døren uansett hvorfor
        # arbeideren mangler (Codex P1, runde 6).
        _steng_doeren(m, "arbeideren kom ikke opp etter enable")
    evidens("fase9_arbeider_i_drift", unit=ARBEIDER, status=aktiv,
            motor=motor, konfig=str(DRIFT_KONF),
            oppe="wcag_arbeider_oppe" in logg,
            nektet=[l for l in logg.splitlines()
                    if "oppstart_nektet" in l][:2],
            ok=i_drift)


def main() -> int:
    global _EVIDENS
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidens", type=Path, required=True)
    ap.add_argument("--fase", type=int)
    ap.add_argument("--motor", help="overstyr motorkommandoen")
    args = ap.parse_args()
    _EVIDENS = args.evidens
    _EVIDENS.parent.mkdir(parents=True, exist_ok=True)

    env = _miljo()
    migrator_dsn = env["DISPONIT_MIGRATOR_URL"]
    m = _pg(migrator_dsn)
    http = Http(API)
    motorkmd = motorkommando(args)

    digest = fase1(m)
    if args.fase in (None, 2):
        fase2(m, migrator_dsn, digest)
    if args.fase in (None, 3):
        fase3(m)
    mtk = None
    if args.fase in (None, 4, 5, 6, 7):
        # ... og et lagret token gjelder BARE sin egen release (Codex P1):
        # er `WCAG_RELEASE` en annen enn den fila bærer, er tokenet
        # tilbakekalt og onboardingen må gjøres om.
        lagret = _lagret_modultoken()
        if args.fase in (None, 4) or not lagret:
            mtk = fase4(m, http, env["DISPONIT_TOKEN_PEPPER"])
        else:
            mtk = lagret
    if args.fase in (None, 5):
        fase5(m, http, mtk, motorkmd, digest)
    if args.fase in (None, 6):
        fase6(m, http, mtk, motorkmd, digest)
    if args.fase in (None, 7):
        fase7(m, http, mtk, digest)
    if args.fase in (None, 8):
        fase8()
    if args.fase in (None, 9):
        # `--fase 9` er operatørens bevisste idriftsettelse etter en grønn
        # runde; en full kjøring gjør det selv, til slutt.
        # Tokenet leses for DENNE releasen. Bærer rundekatalogen bare et
        # token fra en tidligere release, er `mtk` None — og fase 9 stenger
        # døren i stedet for å sette opp en arbeider som får 401 på hvert
        # claim (Codex P1).
        if args.fase == 9 and mtk is None:
            mtk = _lagret_modultoken()
        # Ingen `motorkmd`: fase 9 avleder konteksten av den EFFEKTIVE
        # driftskommandoen, ikke av målerundens (Codex P2, runde 6).
        fase9(m, mtk, digest, maalt_runde=args.fase is None)
    evidens("runde_ferdig")
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
