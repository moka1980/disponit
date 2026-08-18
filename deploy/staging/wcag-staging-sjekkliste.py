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

VIKTIG OG SYNLIG: fase 2 registrerer modulen og setter den AKTIV på
denne basen — det åpner /v1/bestilling for typen her. Det er hele
poenget med runden, og basen er staging.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac as hmaclib
import json
import os
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

_EVIDENS: Path | None = None


def evidens(hendelse: str, **felt) -> None:
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
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:
                return e.code, {}

    def post(self, sti, json=None, headers=None, cookies=None, **kw):
        st, kropp = self._kall("POST", sti, kw.get("kropp", json),
                               headers, cookies)
        return _Svar(st, kropp)

    def get(self, sti, headers=None, cookies=None):
        st, kropp = self._kall("GET", sti, None, headers, cookies)
        return _Svar(st, kropp)


class _Svar:
    def __init__(self, status, kropp):
        self.status_code = status
        self._kropp = kropp

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


def motorkommando(args) -> list[str]:
    if args.motor:
        return shlex.split(args.motor)
    return ["docker", "run", "--rm", "-i", "--network", "host",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
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
    art = ("sha256:" + digest.split(":", 1)[1] if digest.startswith("sha256:")
           else digest)
    env = {**os.environ, "DISPONIT_MIGRATOR_URL": migrator_dsn,
           "DISPONIT_REPO": str(REPO)}
    ut = subprocess.run(
        [sys.executable, str(REPO / "deploy/staging/registrer-m-wcag-audit.py"),
         RELEASE, kh, hashlib.sha256(art.encode()).hexdigest(), ph, kvh],
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
    rad = m.execute(
        "SELECT h.status, EXISTS(SELECT 1 FROM moduldeployment d WHERE"
        " d.modul_id=h.modul_id AND d.miljo='staging' AND"
        " d.livslop='claiming') FROM modulhode h WHERE h.modul_id=%s",
        (MODUL,)).fetchone()
    m.rollback()
    if not rad or rad[0] != "aktiv" or not rad[1]:
        raise SystemExit(f"modulkjeden er ikke claimbar: {rad}")
    evidens("fase2_ok", release=RELEASE, kontrakt_hash=kh,
            payload_hash=ph, kvittering_hash=kvh)


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

    kontekst = {"axe_versjon": "4.10.3", "chromium_versjon": "chromium",
                "container_image_digest": "sha256:" + digest.split(":")[-1],
                "viewport": "1280x800", "locale": "nb",
                "timezone": "Europe/Oslo"}
    return controller.kjor_en(Http(API), mtk, Kommandomotor(motorkmd),
                              kontekst, signer)


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
            evidens("kjoring_avvist", i=i, status=r.status_code,
                    svar=r.json())
            continue
        oid = r.json()["oppdrag_id"]
        res = _kontroller_kjor(mtk, motorkmd, digest)
        varighet = time.monotonic() - start
        frist = 1800 if sp["omfang"] == "enkeltside" else 3600
        rr = http.get(f"/v1/rapport/{oid}",
                      headers={"authorization": f"Bearer {lese_tok}"})
        avvik = (_fasitkontroll(scenario, rr.json()["rapport"])
                 if rr.status_code == 200 else [f"rapport {rr.status_code}"])
        ok = (res.get("utfall") == "utfort" and varighet < frist
              and not avvik)
        gronne += ok
        evidens("kjoring", i=i, scenario=scenario, oppdrag=oid,
                utfall=res.get("utfall"), varighet_s=round(varighet, 1),
                frist_s=frist, avvik_mot_fasit=len(avvik),
                avvik=avvik[:5], ok=ok)
    evidens("fase5_resultat",
            ti_kjoringer_signert_innen_frist=f"{gronne}/10",
            funn_avvik_mot_fasit=0 if gronne == 10 else "SE kjoring-linjene")
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
    evidens("port20_robots", privat_forisporsler=privat, krav=0)

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
    rr = http.get(f"/v1/rapport/{r.json()['oppdrag_id']}",
                  headers={"authorization": f"Bearer {lese_tok}"})
    sider = len(rr.json().get("rapport", {}).get("sider_kontrollert", []))
    evidens("port20_robots_5xx", utfall=res.get("utfall"),
            sider_kontrollert=sider, krav=1)
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

    # 24: motoren kjører uten credentials — mål containerens faktiske miljø.
    kanari = "KANARI_" + secrets.token_hex(8)
    ut = subprocess.run(
        motorkmd[:-1] + ["--entrypoint", "env", motorkmd[-1]]
        if motorkmd[0] == "docker" else ["env"],
        env={**os.environ, "DISPONIT_KEK": kanari,
             "DATABASE_URL": "postgresql://hemmelig"},
        capture_output=True, text=True)
    lekk = [l for l in ut.stdout.splitlines()
            if l.startswith("DISPONIT_") or kanari in l
            or "DATABASE_URL" in l]
    evidens("port24_motormiljo", lekkasjer=lekk, ok=not lekk)


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
    evidens("feilinjisering_motorfeil", utfall=res.get("utfall"),
            oppdragstatus=rad[0], har_kvittering=rad[1],
            promoterte_artefakter=art,
            ok=res.get("utfall") == "avbrutt" and art == 0)

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
    releaser = sorted(Path("/opt/disponit/releases").iterdir(),
                      key=lambda p: p.stat().st_mtime)
    forrige = [p for p in releaser if p != naa]
    if not forrige:
        evidens("fase8_hoppet", grunn="ingen forrige release")
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
    evidens("runde_ferdig")
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
