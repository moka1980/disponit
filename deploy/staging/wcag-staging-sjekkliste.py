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
nøkkel avledet av rundens id (`RUNDE/runde-id`, eller WCAG_RUNDE_ID) og
bestillingskroppen — se `_idem`. En gjenkjøring av samme fase i samme
runde REPLAYER derfor beslutningene i stedet for å ta nye. Det er ikke
kosmetikk: frekvensgrensen er 12/dag per `mal_url`, og en full runde
bruker nøyaktig 12 på `/index.html` (10 i fase 5, 1 i fase 6, 1 i fase
7). Med nøkler som endret seg per forsøk ga en gjenkjøring
`frekvensgrense_naadd` der fasiten krever 10/10 grønne.

En NY runde krever en ny id (tøm rundekatalogen eller sett
WCAG_RUNDE_ID) — og treffer taket uansett om den kjøres samme døgn som
den forrige. Da er det grensen som virker, ikke en feil.

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
RELEASE = "wcag-r1"
TENANT = "t-wcagfasit"
TENANT_FREKVENS = "t-wcagfrekvens"
VERT = "fasit.test"
PORT = 8443
KONTRAKT = REPO / "platform/modules/wcag_audit/kontrakt"
TESTNETT = REPO / "platform/modules/wcag_audit/testnettsted"
MOTOR_AXE = REPO / "platform/modules/wcag_audit/motor_axe"

RUNDE = Path(os.environ.get("WCAG_RUNDE_DIR", "/root/wcag-runde"))
ATT_FIL = Path("/etc/disponit/api/DISPONIT_ATT_NOKLER")
API = os.environ.get("DISPONIT_API_URL", "http://127.0.0.1:8099")

#: Arbeideren som skal claime EKTE oppdrag etter runden — uniten, brukeren
#: og credential-katalogen den leser fra. Stiene er unitens, ikke rundens:
#: `RUNDE` er /root og utilgjengelig for `disponit-wcag`.
ARBEIDER = "disponit-wcag-audit.service"
ARBEIDER_BRUKER = "disponit-wcag"
DRIFT_KONF = Path(os.environ.get("WCAG_DRIFT_KONF", "/etc/disponit/wcag"))

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
            ut[k] = v.strip().strip('"')
    return ut


def _pg(dsn: str):
    c = psycopg.connect(dsn)
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
                         "--format", "{{.Id}}", "disponit-wcag-motor"],
                        capture_output=True, text=True)
    if ut.returncode != 0:
        raise SystemExit("bygg motorimaget først: bash "
                         f"{MOTOR_AXE}/bygg.sh")
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
            "-e", f"MOTOR_VERTSKART={VERT}=127.0.0.1",
            "disponit-wcag-motor"]


# ---------------------------------------------------------------------------
# Fase 2 — registrering + aktivering (staging)
# ---------------------------------------------------------------------------

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
    p["meta"]["status"] = "aktiv"
    return p


def _seed_tenant(m, tenant, frekvens_maks):
    from api import policyregister
    _kontekst(m, tenant)
    # Tenanten ER radene sine — det finnes ingen tenanttabell å opprette.
    try:
        policyregister.registrer(m, tenant, _policy(frekvens_maks), "aktiv")
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
        " utloper=now()+interval '90 days'", (tenant, VERT))
    m.commit()


def _adminokt(m, tenant):
    from api import sesjon as sesjonmodul
    cookie, csrf = secrets.token_hex(24), secrets.token_hex(24)
    _kontekst(m, tenant)
    bid = "bid_" + secrets.token_hex(12)
    m.execute("INSERT INTO brukeridentitet (bruker_id, idp, subjekt)"
              " VALUES (%s,'wcag-runde',%s) ON CONFLICT DO NOTHING",
              (bid, "runde-" + secrets.token_hex(4)))
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
    _seed_tenant(m, TENANT_FREKVENS, 4)
    evidens("fase3_ok", tenants=[TENANT, TENANT_FREKVENS])


# ---------------------------------------------------------------------------
# Fase 4 — onboarding → modultoken (HTTP, den ekte veien)
# ---------------------------------------------------------------------------

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
    (RUNDE / "modultoken").write_text(mtk)
    os.chmod(RUNDE / "modultoken", 0o600)
    evidens("fase4_ok", token_prefiks=mtk[:8])
    return mtk


# ---------------------------------------------------------------------------
# Fase 5 — fasitmålingen (10 kjøringer innen frist)
# ---------------------------------------------------------------------------

def _rundeid() -> str:
    """Rundens IDENTITET — stabil på tvers av gjenkjøringer (Codex P2).

    Idempotensnøklene under avledes av denne. Den skrives én gang til
    `RUNDE/runde-id` og gjenbrukes så lenge rundekatalogen står — samme
    mekanikk som `RUNDE/modultoken` alt bruker for å overleve `--fase N`.
    `WCAG_RUNDE_ID` overstyrer for den som vil navngi runden selv.

    En NY runde krever en ny id (tøm rundekatalogen eller sett
    WCAG_RUNDE_ID). Merk at frekvensgrensen er 12/dag per `mal_url` og at
    en full runde bruker nøyaktig 12 på `/index.html`: en ny runde samme
    døgn treffer taket uansett nøkler, og det er grensen som virker, ikke
    en feil."""
    fil = RUNDE / "runde-id"
    if fil.exists():
        rid = fil.read_text().strip()
        if rid:
            return rid
    RUNDE.mkdir(parents=True, exist_ok=True)
    rid = os.environ.get("WCAG_RUNDE_ID", "").strip() or (
        "r" + secrets.token_hex(6))
    fil.write_text(rid)
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
    runtime = Path(motorkmd[0]).name if motorkmd else ""
    if runtime in ("docker", "podman", "nerdctl") and "run" in motorkmd:
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
    from modules.wcag_audit import controller
    from modules.wcag_audit.motor import Kommandomotor
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


def fase5(m, http, mtk, motorkmd, digest):
    cookie, csrf = _adminokt(m, TENANT)
    lese_tok = _lesetoken(m, TENANT)
    gronne = 0
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
        # Fristen ble målt da kjøringen faktisk skjedde, og den målingen
        # står i evidensfila fra den runden. Et gjenspill kan ikke måle den
        # på nytt, og skal ikke late som.
        ok = (res.get("utfall") == "utfort" and not avvik
              and (alt_utfort or varighet < frist))
        gronne += ok
        evidens("kjoring", i=i, scenario=scenario, oppdrag=oid,
                utfall=res.get("utfall"), gjenspill=alt_utfort,
                varighet_s=None if varighet is None else round(varighet, 1),
                frist_s=frist, avvik_mot_fasit=len(avvik),
                avvik=avvik[:5], ok=ok)
    # ... og SUMMEN bærer den samme påstanden (Codex P1). Fasitkravet er
    # 10/10; alt annet er en rød måling, uansett hvilken av de ti som
    # sviktet og på hvilken måte. Uten `ok` her kunne aggregatet stå
    # `9/10` i evidensfila mens `_ROEDE` var tom.
    evidens("fase5_resultat",
            ti_kjoringer_signert_innen_frist=f"{gronne}/10",
            funn_avvik_mot_fasit=0 if gronne == 10 else "SE kjoring-linjene",
            ok=gronne == 10)
    return gronne


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
    res = _kontroller_kjor(mtk, motorkmd, digest)
    rr = _rapport(http, lese_tok, r.json()["oppdrag_id"])
    sider = len(rr.json().get("rapport", {}).get("sider_kontrollert", []))
    # Kravet er BEGGE deler: kjøringen må ha fullført (en avbrutt motor
    # kontrollerer trivielt «bare én side»), og rapporten må dekke
    # nøyaktig den ene bestilte siden — ikke null, og ikke de fire
    # bestillingen ba om. `sider` er 0 om rapporten uteble.
    evidens("port20_robots_5xx", utfall=res.get("utfall"),
            sider_kontrollert=sider, krav=1,
            ok=res.get("utfall") == "utfort" and sider == 1)
    _start_testnett(robots_5xx=False)

    # 21: frekvensgrensen — femte bestilling samme døgn → unntakskø.
    ck2, cs2 = _adminokt(m, TENANT_FREKVENS)
    utfall = []
    for i in range(5):
        kropp = {"bestillingstype": OPPDRAGSTYPE, "hostname": VERT,
                 "sti": "/index.html", "kravsett": "wcag21_aa",
                 "omfang": "enkeltside"}
        r = _bestill(http, ck2, cs2, kropp, _idem(f"f6-frekv-{i}", kropp))
        utfall.append(r.json().get("beslutning"))
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
    evidens("fase6_ko_drenert", utfall=drenert,
            krav="tom kø før feilinjiseringen i fase 7")

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
    reapet = m.execute("SELECT tenant, oppdrag_id, unntak_id FROM"
                       " reap_evidensfrister(50)").fetchall()
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
    forrige = forrige[-1]

    def _pek(mal: Path) -> None:
        subprocess.run(["ln", "-sfn", str(mal), str(aktiv)], check=True)

    def _restart_og_status() -> str:
        """-> systemd-statusen etter restart. Restarten er MÅLINGEN, ikke
        en forutsetning: `check=True` her ville kastet før statusen ble
        lest, og en release som ikke starter er nettopp utfallet drillen
        skal fange."""
        subprocess.run(["systemctl", "restart", "disponit-api.service"],
                       capture_output=True)
        time.sleep(4)
        return subprocess.run(
            ["systemctl", "is-active", "disponit-api.service"],
            capture_output=True, text=True).stdout.strip()

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

def _arbeiderpodman(*argv) -> list[str]:
    """`podman ...` kjørt SOM arbeideren, med unitens egne stier.

    Rootless podman leser bildelageret under brukerens `$HOME` og
    kjøretidsting under `XDG_RUNTIME_DIR` — systemd setter begge for
    `User=`-uniten, så importen må bruke NØYAKTIG de samme verdiene.
    Gjør den ikke det, havner imaget i et annet lager enn det arbeideren
    slår opp i."""
    import pwd
    hjem = pwd.getpwnam(ARBEIDER_BRUKER).pw_dir
    return ["runuser", "-u", ARBEIDER_BRUKER, "--", "env", f"HOME={hjem}",
            f"XDG_RUNTIME_DIR=/run/{ARBEIDER_BRUKER}", "podman", *argv]


def _importer_motorimage(digest: str) -> tuple[bool, str]:
    """Motorimaget inn i ARBEIDERENS podman-lager (Codex P1).

    Fase 1 henter image-ID-en fra den ROOTFULLE dockerdaemonen, og fase 9
    ga den samme ID-en til rootless podman som `disponit-wcag`. De to har
    hvert sitt lager og ingen felles referanse — ingen `docker save`,
    ingen registry-push, ingen rootless bygging fantes noe sted. Den
    enablede arbeideren kunne derfor ikke starte motoren i det hele tatt,
    og ville feilet hvert eneste claimet oppdrag.

    Image-ID-en er konfigblobbens digest og overlever `docker save` |
    `podman load`, så `container_image_digest` er den samme verdien
    releaseraden og kvitteringene bærer.
    """
    kort = digest.split(":")[-1]
    subprocess.run(["install", "-d", "-m", "700", "-o", ARBEIDER_BRUKER,
                    "-g", ARBEIDER_BRUKER, f"/run/{ARBEIDER_BRUKER}"],
                   check=True)
    if subprocess.run(_arbeiderpodman("image", "exists", kort),
                      capture_output=True).returncode == 0:
        return True, "alt i lageret"
    ror = subprocess.run(
        ["bash", "-o", "pipefail", "-c",
         f"docker save {shlex.quote(kort)} | "
         + shlex.join(_arbeiderpodman("load"))],
        capture_output=True, text=True)
    if ror.returncode != 0:
        return False, f"import feilet: {(ror.stderr or ror.stdout)[-300:]}"
    etter = subprocess.run(_arbeiderpodman("image", "exists", kort),
                           capture_output=True, text=True)
    if etter.returncode != 0:
        return False, ("imaget er ikke i lageret etter import:"
                       f" {etter.stderr[-200:]}")
    return True, "importert"


def fase9(mtk, digest, motorkmd, *, maalt_runde: bool):
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
    """
    if _ROEDE:
        evidens("fase9_hoppet", grunn="røde målinger i runden",
                roede=sorted(set(_ROEDE)),
                merknad="modulen er aktiv, men arbeideren settes ikke i"
                        " drift på en runde som ikke er grønn")
        return
    if not mtk:
        evidens("fase9_hoppet", grunn="ingen modultoken i denne kjøringen")
        return
    if not maalt_runde:
        evidens("fase9_operatoerbeslutning",
                merknad="--fase 9 målte ingenting selv; idriftsettelsen"
                        " hviler på evidensfila fra den grønne runden")

    reg = json.loads(ATT_FIL.read_text())
    nid = sorted(reg["v_wcag_audit"])[0]
    motor = os.environ.get("WCAG_DRIFT_MOTOR") or shlex.join([
        "podman", "run", "--rm", "-i", "--network", "host",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        # Nettleseren kjører kundens side: `MOTORGRENSER` er minnet og
        # prosesstabellen den kan bruke, unitens `CPUQuota=`/`MemoryMax=`
        # er backstoppet rundt hele arbeideren.
        *MOTORGRENSER,
        # Image-ID-en, ikke taggen: taggen er mutabel, og kvitteringen
        # attesterer nøyaktig denne digesten som container_image_digest.
        digest.split(":")[-1]])
    # SAMME `miljo`-blokk som målingene attesterte — ett sted, én verdi.
    # Nettleserversjonen leses ut av imaget (`_chromium_versjon`), som er
    # den samme uansett hvilken runtime som slår den opp: image-ID-en er
    # konfigblobbens digest, og den er lik i begge lagrene.
    kontekst = _serverkontekst(digest, motorkmd)

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
        return

    # ... og IMAGET må ligge i arbeiderens EGET lager — se
    # `_importer_motorimage`. Docker og rootless podman deler ingenting.
    importert, hvordan = _importer_motorimage(digest)
    evidens("fase9_motorimage", image_id=digest.split(":")[-1],
            lager=f"rootless podman ({ARBEIDER_BRUKER})", utfall=hvordan,
            ok=importert)
    if not importert:
        evidens("fase9_hoppet",
                grunn="motorimaget er ikke tilgjengelig for arbeideren",
                merknad="uniten enables ikke: den ville feilet hvert"
                        " eneste claimet oppdrag")
        return

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
        tok_fil = RUNDE / "modultoken"
        if args.fase in (None, 4) or not tok_fil.exists():
            mtk = fase4(m, http, env["DISPONIT_TOKEN_PEPPER"])
        else:
            mtk = tok_fil.read_text().strip()
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
        if args.fase == 9 and mtk is None:
            mtk = (RUNDE / "modultoken").read_text().strip()
        fase9(mtk, digest, motorkmd, maalt_runde=args.fase is None)
    evidens("runde_ferdig")
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
