#!/usr/bin/env python3
"""Flippedrillen for m56 — produserer `rollback-m56-v1`-artefaktet.

Ruller moduldeployment tilbake MENS et oppdrag er underveis, og måler de
tre kontrollpunktene akseptflippen (049) krever:

  (a) claim-stopp: den drenerte releasen claimer INGENTING nytt —
      claim-porten i 015 fencer på kallerens deployment-livsløp, og det
      er nøyaktig den fencingen som måles her, med den levende arbeideren
      som probe (ikke en syntetisk kall-kjede).
  (b) rene utfall: oppdraget som VAR claimet da rullingen traff,
      fullfører eller feiler rent — signert kvittering, aldri et falskt
      verdikt (SP-3).
  (b2) rullbakken KJØRER: den tilbakerullede releasen bootes gjennom
      sjekklistens egne faser (2/4/9) og plukker og fullfører det
      ventende oppdraget claim-stoppet lot ligge. Uten dette leddet
      måler drillen bare at den gamle arbeideren sluttet å claime, og
      en forrige release som ikke lar seg kjøre på verten eller mot
      basen ville gitt et grønt rullbakkbevis (Codex P1, #117).

      OG DEN BÆRER FORGJENGERENS BYTES. Rullback-releasen ble før
      registrert med den DRILLEDE deploymentens digest, så «rullbakken»
      var kandidatens egne bytes under et nytt navn — (b2) kunne stå
      grønt uten at det man ruller tilbake TIL noen gang var prøvd
      (Codex P1, #117 runde 6). Digesten hentes nå fra forgjengeren i
      registeret, og siden sjekklistens fase 1/2 pinner det lokalt bygde
      motorimaget, MÅLES det først at forgjengerens bytes er de samme —
      er de ikke det, kan denne drillen ikke boote dem, og den sier det
      i stedet for å registrere de drillede bytene og kalle det en
      rullbakk.
  (c) fram igjen: akseptkandidaten — byte-identisk med den drillede
      (A1) — plukker det ventende oppdraget og promoterer rapporten.
      Kandidatens promoterte artefakt er samtidig akseptens E2E-bevis:
      hvert bevis binder releasen som faktisk aksepteres.

LIVSLØPET ER ENVEIS (014): `bytt_release` nekter å re-claime en drenert
deployment, så drillen KONSUMERER den drillede releasen. Tilbake-
rullingen skjer til en NY release med forgjengerens bytes
(`--rullback-id`), og «fram igjen» lander på akseptkandidaten
(`--kandidat-id`) — raden aksepten binder. Registreringen av
kandidatleddet (fase 2/4/9 i sjekklisterunden) gjenbrukes uendret; alt
drill-spesifikt bor her.

Kjøres PÅ verten (disponit-srv), som root, med samme miljø som
sjekklisterunden.

REKJØRING: et forsøk som dør FØR første `bytt_release` kjøres om igjen
med de samme argumentene — hvert steg måler tilstanden før det handler,
og bestillingene bruker drill-egne idempotensnøkler. Etter rullingen er
det noe annet: drillen har da konsumert `--rullback-id` (og kanskje
`--kandidat-id`), livsløpet er enveis, og en drill er ÉN måling — de
fire leddene hører til samme kjøring, og et artefakt sydd av to
kjøringer er ikke evidens for noen av dem. `krev_ubrukte_drillreleaser`
måler dette FØR noe bestilles, og sier hvilke id-er som er brukt opp.
Neste forsøk er da en ny, hel drill fra tilstanden som står, med to
ubrukte id-er (Codex P2, #117 runde 5).

BRUK:
    sudo -E python3 deploy/staging/rollback-m56.py \
        --rullback-id wcag-r6 --kandidat-id wcag-r7 \
        --ut deploy/staging/artefakter/rollback-m56-v1-<ts>.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HER = Path(__file__).resolve().parent
REPO = HER.parents[1]
sys.path.insert(0, str(REPO / "platform/core"))

MODUL = "m_wcag_audit"   # registernavnet (modulmappen heter m56_wcag_audit)
MILJO = "staging"
VENTETID_S = 25.0        # claim-stoppet observeres minst så lenge
POLL_S = 0.1


def _api_url() -> str:
    """Samme kilde som arbeideren selv: unitens konfig. Miljøvariabelen
    overlever ikke sudo, og API-et lytter kun på unix-socketen bak
    nginx — 127.0.0.1-defaulten fra sjekklisterunden finnes ikke her."""
    if os.environ.get("DISPONIT_API_URL"):
        return os.environ["DISPONIT_API_URL"]
    konfig = Path("/etc/disponit/wcag/konfig")
    if konfig.exists():
        for linje in konfig.read_text().splitlines():
            if linje.startswith("DISPONIT_API_URL="):
                return linje.split("=", 1)[1].strip()
    raise SystemExit("AVBRUTT: fant ingen DISPONIT_API_URL (miljø eller"
                     " /etc/disponit/wcag/konfig)")


def _sjekkliste():
    os.environ["DISPONIT_API_URL"] = _api_url()
    spec = importlib.util.spec_from_file_location(
        "wcag_sjekkliste", HER / "wcag-staging-sjekkliste.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _admin(m):
    """migrator-cursoren som modules_admin — registerovergangene."""
    m.execute("SET ROLE disponit_modules_admin")
    return m


def _manifest_hash() -> str:
    """Manifestets hash slik `registrer-m-wcag-audit.py` regner den ut."""
    return hashlib.sha256(
        (REPO / "platform/modules/m56_wcag_audit/manifest.yaml").read_bytes()
    ).hexdigest()


def _kjor_faser(release: str, evidens: Path, *, hva: str,
                faser: tuple[str, ...] = ("2", "4", "9")) -> None:
    """Booter `release` gjennom NØYAKTIG sjekklisterundens faser 2/4/9.

    Registrering, release-bytte, modultoken og selve arbeiderunit-en —
    drillen legger ingen egen vei inn i registeret eller på verten, den
    bruker den som finnes. Fase 2 er idempotent på sluttilstanden: er
    deploymenten alt `claiming` (rullbakken er rullet av drillen selv),
    hoppes livsløpskallet og porten måler tilstanden i stedet.

    `faser` finnes for (c): der må registerbyttet (fase 2, som fencer den
    forrige deploymenten) skje FØR kandidatens oppdrag bestilles, mens
    arbeideren (fase 4/9) først startes ETTERPÅ. Rekkefølgen er ellers
    uendret — dette er samme kjede, delt på nøyaktig det ene punktet der
    fencingen og bestillingen møtes (Codex P2, #117 runde 6).
    """
    for fase in faser:
        r = subprocess.run(
            [sys.executable, str(HER / "wcag-staging-sjekkliste.py"),
             "--evidens", str(evidens), "--fase", fase],
            env={**os.environ, "WCAG_RELEASE": release,
                 "WCAG_RUNDE_ID": f"drill-{release}"},
            capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(f"AVBRUTT: sjekklistefase {fase} for {hva}"
                             f" ({release}) feilet:\n{r.stdout[-2000:]}"
                             f"\n{r.stderr[-2000:]}")
        print(f"  fase {fase} for {release} ({hva}): ok")


def _vent_terminal(m, sj, oid: str, frist_s: float) -> str | None:
    """Venter til oppdraget er terminalt. -> status (eller siste sette)."""
    frist = time.monotonic() + frist_s
    st = None
    while time.monotonic() < frist:
        st = _status(m, sj.TENANT, oid)
        if st in ("utfort", "feilet"):
            return st
        time.sleep(0.5)
    return st


def _kvittering(m, tenant, oid):
    m.execute("RESET ROLE")
    m.execute("SELECT set_config('disponit.tenant', %s, true)", (tenant,))
    rad = m.execute("SELECT kvittering IS NOT NULL FROM oppdrag"
                    " WHERE tenant=%s AND id=%s", (tenant, oid)).fetchone()
    m.commit()
    return bool(rad and rad[0])


def _status(m, tenant, oid):
    m.execute("RESET ROLE")
    m.execute("SELECT set_config('disponit.tenant', %s, true)", (tenant,))
    rad = m.execute("SELECT status FROM oppdrag WHERE tenant=%s AND id=%s",
                    (tenant, oid)).fetchone()
    m.commit()
    return rad[0] if rad else None


def _promoterte(m, tenant, oid, release):
    m.execute("SELECT set_config('disponit.tenant', %s, true)", (tenant,))
    rad = m.execute(
        "SELECT artefakt_id::text FROM artefakt WHERE tenant=%s"
        " AND oppdrag_id=%s AND release_id=%s AND tilstand='promotert'",
        (tenant, oid, release)).fetchall()
    m.commit()
    return [r[0] for r in rad]


def _deployment(m, release):
    rad = m.execute(
        "SELECT livslop FROM moduldeployment WHERE modul_id=%s AND miljo=%s"
        " AND release_id=%s", (MODUL, MILJO, release)).fetchone()
    return rad[0] if rad else None


#: Drillen kjører mot det OFFENTLIGE, verifiserte målet — driftsmotoren
#: (uten rundens fixture-brytere) avviser med rette ikke-offentlige
#: adresser, så fasit.test (127.0.0.1) kan aldri være drillens mål.
#: Samme mål som driftskjøringen 19/8 (oppdrag 34).
DRILL_VERT = "disponit.com"


RUNDE_ID = ""   # settes i main() fra kandidat-id — drillens replay-skop


def _bestill_drill(sj, m, merkelapp):
    """Én drift-bestilling med drill-egen idempotensnøkkel. -> oppdrag_id"""
    kropp = {"bestillingstype": sj.OPPDRAGSTYPE, "hostname": DRILL_VERT,
             "sti": "/index.html", "kravsett": "wcag21_aa",
             "omfang": "enkeltside", "maks_sider": 1}
    m.execute("RESET ROLE")
    cookie, csrf = sj._adminokt(m, sj.TENANT)
    m.commit()
    http = sj.Http(sj.API)
    r = sj._bestill(http, cookie, csrf, kropp,
                    f"drill-{RUNDE_ID}-{merkelapp}")
    if r.status_code != 200 or r.json().get("beslutning") != "tillat":
        raise SystemExit(f"AVBRUTT: bestillingen ({merkelapp}) ble avvist:"
                         f" {r.status_code} {r.text[:300]}")
    return r.json()["oppdrag_id"]


def forgjengerens_bytes(m, drillet: str) -> tuple[str, str]:
    """Releasen den drillede overtok fra, og HENNES digest.
    -> (release_id, artifact_digest).

    Codex' P1 på PR #117 (runde 6): rullback-releasen ble registrert med
    `digest` — den DRILLEDE deploymentens. Rullbakken ble dermed
    kandidatens egne bytes under en rullbakk-identitet, og (b2)-leddet
    kunne stå grønt uten at forgjengerens bytes noen gang var prøvd. Et
    rullbakkbevis som aldri rørte det man ruller tilbake TIL, er ikke et
    rullbakkbevis.

    Forgjengeren er deploymentraden med det seneste `fra_ts` før den
    drillede — registerets egen historie, ikke en antakelse.
    """
    rad = m.execute(
        "SELECT d.release_id, r.artifact_digest"
        "  FROM moduldeployment d JOIN modulrelease r"
        "    ON r.modul_id = d.modul_id AND r.release_id = d.release_id"
        " WHERE d.modul_id=%s AND d.miljo=%s AND d.release_id <> %s"
        "   AND d.fra_ts <= (SELECT fra_ts FROM moduldeployment"
        "                     WHERE modul_id=%s AND miljo=%s"
        "                       AND release_id=%s)"
        " ORDER BY d.fra_ts DESC, d.release_id DESC LIMIT 1",
        (MODUL, MILJO, drillet, MODUL, MILJO, drillet)).fetchone()
    if rad is None:
        raise SystemExit(
            f"AVBRUTT: {drillet} har ingen forgjenger i {MILJO} — det"
            " finnes ingenting å rulle tilbake TIL, og en drill som"
            " ruller tilbake til seg selv måler ingen rullbakk")
    return rad[0], rad[1]


def krev_bootbare_forgjengerbytes(forgjenger: str, forgjenger_digest: str,
                                  drillet_digest: str) -> None:
    """Bootveien kan bare kjøre ÉN image — og det må være forgjengerens.

    Sjekklistens fase 1 leser det lokalt bygde `disponit-wcag-motor`, og
    fase 2 KREVER at den claimende deploymentens `artifact_digest` er
    nøyaktig det imaget. `_kjor_faser` har altså ingen måte å boote et
    annet image på, og den drillede releasen ble registrert fra samme
    lokale bygg.

    Er forgjengerens bytes andre enn de drillede, kan denne drillen
    derfor IKKE prøve dem — og da skal den si det, ikke registrere
    rullbakken med de drillede bytene og kalle det en rullbakk (Codex P1,
    #117 runde 6). At m56-releasene i dag deler digest er A1s levende
    bevis, og nettopp derfor må likheten MÅLES her: den er en tilstand
    som kan endre seg, ikke en garanti.
    """
    if forgjenger_digest == drillet_digest:
        return
    raise SystemExit(
        f"AVBRUTT: forgjengeren {forgjenger} bærer digest"
        f" {str(forgjenger_digest)[:19]}…, mens den drillede releasen"
        f" bærer {str(drillet_digest)[:19]}…. Sjekklistens fase 1/2 pinner"
        " det LOKALT BYGDE motorimaget, så drillen kan ikke boote"
        " forgjengerens bytes — og en rullbakk til de drillede bytene"
        " prøver ikke det man ruller tilbake til. Bygg forgjengerens image"
        " på verten først, eller kjør rullbakken manuelt og bind beviset"
        " for hånd.")


def registrer_rullbakkreleasen(m, rullback: str, kver: int, khash: str,
                               forgjenger_digest: str) -> None:
    """Skriver rullback-releaseraden — UBETINGET, før den destruktive
    rullingen (Codex P1, #117 runde 6).

    Kallet lå før inne i en «finnes raden alt?»-test, og en eksisterende
    `--rullback-id` slapp da forbi HELT umålt. Testen ga ingenting:
    `registrer_release` (014) er selv idempotent — den tar advisory-låsen
    på release-identiteten, returnerer stille på identisk innhold og
    hever `release (…) er immutable` på avvikende. Innpakningen fjernet
    altså bare den ENESTE sammenligningen mellom raden og bytene drillen
    mener å rulle tilbake til.

    Og det som slapp forbi var destruktivt, ikke bare slapt: `bytt_release`
    validerer kun kontraktversjon og kontrakt-hash, så drillen ville
    drenert den levende deploymenten og flyttet registeret til den
    avvikende releasen. Først i fase 2 regner `registrer-m-wcag-audit.py`
    manifesthashen ut av disken og treffer immutabilitetskonflikten — da
    er rullingen gjort, modulen står uten claimende arbeider, og
    drilltilstanden er brukt opp. Porten hører hjemme her.

    MANIFESTHASHEN ER DENNE UTSJEKKINGENS, ikke den drillede radens:
    rullback-releasen bootes senere gjennom sjekklistens egne faser, og
    `registrer-m-wcag-audit.py` regner da hashen ut av manifest.yaml på
    disk. Skrev vi den drillede radens hash her, ville den passive
    registreringen og fase 2 vært to ULIKE påstander om samme immutable
    rad — og fase 2 ville dødd på en konflikt drillen selv lagde.

    DIGESTEN ER FORGJENGERENS, ikke den drilledes (Codex P1, runde 6).
    Verdiene er like i dag — `krev_bootbare_forgjengerbytes` har alt målt
    det — men KILDEN er poenget: en rullbakk som henter bytene sine fra
    releasen den ruller VEKK fra, ruller ingen steder.
    """
    manifest = _manifest_hash()
    _admin(m)
    try:
        m.execute("SELECT registrer_release(%s,%s,%s,%s,%s,%s,'m56-drill')",
                  (MODUL, rullback, kver, khash, manifest,
                   forgjenger_digest))
        m.commit()
    except Exception as e:
        m.rollback()
        raise SystemExit(
            f"AVBRUTT: rullback-releasen {rullback} finnes alt i registeret"
            " med et ANNET innhold enn drillen ville skrevet (kontrakt"
            f" v{kver}, manifest {manifest[:12]}…, digest"
            f" {str(forgjenger_digest)[:19]}…): {e}\nRaden er immutabel, så"
            " den kan ikke rettes — og hadde drillen rullet dit likevel,"
            " ville fase 2 dødd på nøyaktig denne konflikten ETTER at den"
            " levende deploymenten var drenert. Kjør drillen med en ubrukt"
            " --rullback-id.") from e
    m.execute("RESET ROLE")


def krev_ubrukte_drillreleaser(m, drillet: str, rullback: str,
                               kandidat: str) -> None:
    """Drill-id-ene må være UBRUKTE deployments, ellers er dette en rest.

    Codex' P2 på PR #117 (runde 5): docstringen lovet at rekjøring var
    trygg, og det holdt bare fram til første `bytt_release`. Etter den er
    den claimende deploymenten en ANNEN release enn den drillen startet
    på, og et nytt forsøk med de samme argumentene leser den som «den
    drillede»:

      * er rullbakken claiming, blir `drillet == rullback` — CHECK-en i
        049 avviser raden, men først ETTER at hele drillen er kjørt om
        igjen,
      * er kandidaten claiming, prøver forsøket å rulle til rullbakken,
        som drillen alt drenerte — og livsløpet er enveis, så det feiler
        midt i målingen.

    Begge tilstandene er vanlige: drillen booter to releaser på verten,
    og en unit som ikke starter etterlater nøyaktig dem.

    Og resten kan ikke gjenopptas. En drill er ÉN måling: claim-stoppet,
    det løpende oppdragets utfall, rullbakkens overtakelse og kandidatens
    overtakelse hører til samme kjøring, og et artefakt sydd av to
    kjøringer er nettopp formen runde 4 forbød for runde-sammendraget.
    Det som KAN gjøres, er å starte en ny, hel drill fra tilstanden som
    nå står — og den trenger sine egne release-id-er.

    Porten står her, FØR noe bestilles eller rulles, og den slipper
    gjennom det som faktisk er trygt: et forsøk som døde før første
    `bytt_release` har ingen deployment å vise til, og kjøres om igjen
    med de samme id-ene uten videre. Den passivt registrerte
    rullbakk-RELEASEN er ikke en deployment og teller ikke.
    """
    if drillet in (rullback, kandidat):
        raise SystemExit(
            f"AVBRUTT: den claimende releasen ER {drillet} — en av"
            " drill-id-ene fra dette kallet. Et tidligere forsøk kom da"
            " forbi rullingen og etterlot resten av drillen ukjørt. En"
            " drill er én måling og kan ikke gjenopptas; start en NY drill"
            f" fra {drillet} med to ubrukte id-er:\n"
            f"  --rullback-id <ny> --kandidat-id <ny>")
    for merkelapp, rel in (("rullback", rullback), ("kandidat", kandidat)):
        livslop = _deployment(m, rel)
        if livslop is not None:
            raise SystemExit(
                f"AVBRUTT: {merkelapp}-releasen {rel} har alt en"
                f" deployment ({livslop}) i {MILJO}. Livsløpet er enveis,"
                " så den kan ikke claimes på nytt — id-en er brukt opp av"
                " et tidligere drillforsøk. Kjør en ny drill med to"
                " ubrukte id-er.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rullback-id", required=True)
    ap.add_argument("--kandidat-id", required=True)
    ap.add_argument("--ut", type=Path, required=True)
    ap.add_argument("--forsok", type=int, default=3,
                    help="maks forsøk på å treffe rullingen midt i et"
                         " løpende oppdrag")
    a = ap.parse_args()

    global RUNDE_ID
    # Per-INVOKASJON, ikke per kandidat: en ny drillkjøring er en ny
    # måling og skal aldri gjenspille en gammel kjørings terminale
    # oppdrag som om de var dagens (idempotensnøklene er drill-skopet).
    RUNDE_ID = f"{a.kandidat_id}-{int(time.time())}"
    sj = _sjekkliste()
    env = sj._miljo()
    m = sj._pg(env["DISPONIT_MIGRATOR_URL"])

    # Utgangspunktet: den claimende deploymenten er den som drilles.
    rad = m.execute(
        "SELECT d.release_id, d.kontraktversjon, d.kontrakt_hash,"
        "       r.artifact_digest"
        "  FROM moduldeployment d JOIN modulrelease r"
        "    ON r.modul_id = d.modul_id AND r.release_id = d.release_id"
        " WHERE d.modul_id=%s AND d.miljo=%s AND d.livslop='claiming'",
        (MODUL, MILJO)).fetchone()
    if rad is None:
        raise SystemExit("AVBRUTT: ingen claiming-deployment å drille")
    drillet, kver, khash, digest = rad
    krev_ubrukte_drillreleaser(m, drillet, a.rullback_id, a.kandidat_id)
    # Rullbakken skal bære FORGJENGERENS bytes, ikke den drilledes
    # (Codex P1, #117 runde 6) — og bootveien må kunne kjøre dem.
    forgjenger, forgjenger_digest = forgjengerens_bytes(m, drillet)
    krev_bootbare_forgjengerbytes(forgjenger, forgjenger_digest, digest)
    epoch = m.execute("SELECT module_epoch FROM modulhode WHERE modul_id=%s",
                      (MODUL,)).fetchone()[0]
    print(f"driller {drillet} (epoch {epoch}, digest {digest[:12]}…),"
          f" rullbakk til forgjengeren {forgjenger}s bytes"
          f" ({forgjenger_digest[:12]}…)")

    # Rullback-releasen registreres FØR racet — registreringen er passiv,
    # selve rullingen er ett kall og fyres midt i det løpende oppdraget.
    registrer_rullbakkreleasen(m, a.rullback_id, kver, khash,
                               forgjenger_digest)

    # (b) — det løpende oppdraget. Arbeideren stoppes så bestillingen
    # beviselig ligger uclaimet, startes, og rullingen fyres i det claimet
    # observeres. Treffer den etter fullføring, er målingen ikke gjort og
    # forsøket gjentas — aldri pyntes.
    inflight = None
    # ARBEIDEREN RØRES IKKE: drillen måler den LEVENDE arbeideren, og en
    # unit som stoppes/startes av drillen selv havner målt og gjentatt i
    # en tilstand der rootless podman nekter proc-mounten på hver eneste
    # container (`runc … mounting "proc" … operation not permitted`) —
    # mens den fase 9-provisjonerte arbeideren kjører friskt. Racet
    # trenger ingen restart: en frisk arbeider claimer bestillingen i
    # løpet av sekunder, og rullingen fyres i det claimet observeres.
    # Forutsetningen MÅLES først: én probe-kjøring skal gå hele veien.
    for p_i in range(3):
        probe = _bestill_drill(sj, m, f"probe{p_i}")
        frist = time.monotonic() + 180
        st_p = None
        while time.monotonic() < frist:
            st_p = _status(m, sj.TENANT, probe)
            if st_p in ("utfort", "feilet"):
                break
            time.sleep(0.5)
        print(f"  forutsetningsprobe {p_i}: {probe} = {st_p}")
        if st_p == "utfort":
            break
        subprocess.run(["systemctl", "restart", sj.ARBEIDER],
                       capture_output=True)
        time.sleep(15)
    else:
        raise SystemExit("AVBRUTT: arbeideren fikk aldri en kjøring"
                         " helskinnet gjennom — se worker-journalen")
    for forsok in range(a.forsok):
        oid = _bestill_drill(sj, m, f"b{forsok}")
        st = _status(m, sj.TENANT, oid)
        frist = time.monotonic() + 120
        while st == "opprettet" and time.monotonic() < frist:
            time.sleep(POLL_S)
            st = _status(m, sj.TENANT, oid)
        if st == "opprettet":
            raise SystemExit(f"AVBRUTT: oppdrag {oid} ble aldri claimet —"
                             " er arbeideren i drift?")
        rulle_ts = time.monotonic()
        if st in ("utfort", "feilet"):
            print(f"  forsøk {forsok}: {oid} rakk å fullføre før rullingen"
                  " — nytt forsøk")
            continue
        _admin(m)
        m.execute("SELECT bytt_release(%s,%s,%s,%s,%s,'m56-drill')",
                  (MODUL, MILJO, a.rullback_id, kver, khash))
        m.commit()
        # Oppdraget VAR claimet da rullingen traff — vent på terminalen.
        frist = time.monotonic() + 600
        while time.monotonic() < frist:
            st = _status(m, sj.TENANT, oid)
            if st in ("utfort", "feilet"):
                break
            time.sleep(0.5)
        inflight = {"oppdrag": oid, "utfall": st,
                    "fullfort_etter_rull_s":
                        round(time.monotonic() - rulle_ts, 3)}
        break
    if inflight is None or inflight["utfall"] not in ("utfort", "feilet"):
        raise SystemExit("AVBRUTT: fikk aldri målt et løpende oppdrag over"
                         " rullingen — kjør drillen på nytt")
    inflight_artefakter = _promoterte(m, sj.TENANT, inflight["oppdrag"],
                                      drillet)
    inflight_kvittering = _kvittering(m, sj.TENANT, inflight["oppdrag"])
    print(f"  (b) inflight: {inflight} artefakter={inflight_artefakter}")

    # (a) — claim-stoppet: nytt oppdrag, drenert release, levende arbeider.
    o2 = _bestill_drill(sj, m, "claimstopp")
    if _status(m, sj.TENANT, o2) != "opprettet":
        raise SystemExit(f"AVBRUTT: {o2} var alt behandlet — idempotens-"
                         "nøkkelen er brukt; kjør med ny runde-id")
    t0 = time.monotonic()
    claimet_under_drenering = 0
    while time.monotonic() - t0 < VENTETID_S:
        if _status(m, sj.TENANT, o2) != "opprettet":
            claimet_under_drenering += 1
            break
        time.sleep(0.5)
    ventetid = round(time.monotonic() - t0, 3)
    print(f"  (a) claim-stopp: {claimet_under_drenering} claims på"
          f" {ventetid} s (arbeider: {drillet}, drenert)")

    evidensfil = a.ut.parent / "drill-evidens.jsonl"

    # (b2) — SELVE RULLBAKKEN: den tilbakerullede releasen BOOTES og
    # prøves. Codex' P1 på PR #117 (runde 3): drillen oppdaterte bare
    # `moduldeployment` og gikk rett videre til kandidaten, så
    # `rullback_id` ble aldri startet på verten. En forrige release som
    # er inkompatibel med verten eller basen — feil imagekonfig, en
    # migrasjon den ikke tåler, en unit som ikke starter — ga da et
    # GRØNT rullbakkartefakt, målt utelukkende på at den gamle
    # arbeideren sluttet å claime. Et rullbakkbevis må vise at det
    # faktisk går an å kjøre på den releasen: den bootes gjennom
    # sjekklistens egne faser og skal plukke og fullføre det ventende
    # oppdraget claim-stoppet nettopp lot ligge.
    rullback_ts = time.monotonic()
    _kjor_faser(a.rullback_id, evidensfil, hva="rullbakken")
    st_rb = _vent_terminal(m, sj, o2, 600)
    if st_rb != "utfort":
        raise SystemExit(f"AVBRUTT: rullback-releasen {a.rullback_id}"
                         f" fullførte ikke det ventende oppdraget ({o2} ="
                         f" {st_rb}) — en release som ikke kan kjøre, er"
                         " ingen rullbakk")
    rullback_overtakelse = round(time.monotonic() - rullback_ts, 3)
    rullback_artefakter = _promoterte(m, sj.TENANT, o2, a.rullback_id)
    print(f"  (b2) rullbakk: {o2} utført av {a.rullback_id}, artefakter"
          f" {rullback_artefakter}, overtakelse {rullback_overtakelse} s")

    # (c) — fram igjen: kandidaten registreres, byttes til, onboardes og
    # provisjoneres via NØYAKTIG sjekklisterundens egne faser (2 → 4 → 9);
    # drillen legger ingen egen vei inn i registeret. Kandidaten får sitt
    # EGET oppdrag: det forrige er rullbakkens bevis, og en overtakelse
    # måles på arbeid som lå og ventet på nettopp den som overtar.
    #
    # FENCINGEN FØRST, SÅ BESTILLINGEN (Codex P2, #117 runde 6). Oppdraget
    # ble før lagt inn FØR fase 2, mens rullbakk-arbeideren fra (b2) sto
    # levende og claimende. Den kunne da plukke og fullføre kandidatens
    # oppdrag i vinduet før registerbyttet — og drillen kunne ikke se
    # forskjell på det og en ekte overtakelse: `_vent_terminal` ble
    # `utfort`, mens `_promoterte(o3, kandidat)` var tom. Resultatet var en
    # rød drill, oppdaget først her, etter at BEGGE drill-id-ene var
    # konsumert og livsløpet er enveis — altså en tapt kjøring på en ren
    # kappløpstilfeldighet.
    #
    # Fase 2 kjøres derfor alene først: den fencer rullbakk-deploymenten
    # (`bytt_release` setter den `draining`, og claim-porten i 015 fencer
    # på kallerens livsløp) uten å starte kandidatens arbeider. Oppdraget
    # legges inn i vinduet DER: fenced rullbakk, ingen kandidatarbeider
    # ennå — det ligger og venter på nøyaktig den som skal overta, slik
    # (b2) også målte det. Fase 4/9 booter kandidaten etterpå.
    _kjor_faser(a.kandidat_id, evidensfil, hva="akseptkandidaten",
                faser=("2",))
    # MÅLT, ikke antatt: at rullbakken faktisk er ute av claiming er hele
    # forutsetningen for at det neste oppdraget venter på kandidaten.
    m.execute("RESET ROLE")
    rb_livslop = _deployment(m, a.rullback_id)
    m.commit()
    if rb_livslop == "claiming":
        raise SystemExit(
            f"AVBRUTT: rullback-releasen {a.rullback_id} er fortsatt"
            " claiming etter kandidatens fase 2 — registerbyttet tok ikke,"
            " og et oppdrag bestilt nå kunne blitt plukket av"
            " rullbakk-arbeideren i stedet for kandidaten")
    o3 = _bestill_drill(sj, m, "framigjen")
    fram_ts = time.monotonic()
    _kjor_faser(a.kandidat_id, evidensfil, hva="akseptkandidaten",
                faser=("4", "9"))
    st2 = _vent_terminal(m, sj, o3, 600)
    if st2 != "utfort":
        raise SystemExit(f"AVBRUTT: kandidaten fullførte ikke det ventende"
                         f" oppdraget ({o3} = {st2})")
    overtakelse = round(time.monotonic() - fram_ts, 3)
    kandidat_artefakter = _promoterte(m, sj.TENANT, o3, a.kandidat_id)
    print(f"  (c) kandidat: {o3} utført, artefakter {kandidat_artefakter},"
          f" overtakelse {overtakelse} s")

    # Etterkontrollen leses fra basen, aldri fra planen.
    m.execute("RESET ROLE")
    kandidat_digest = m.execute(
        "SELECT artifact_digest FROM modulrelease WHERE modul_id=%s"
        " AND release_id=%s", (MODUL, a.kandidat_id)).fetchone()[0]
    rullback_digest = m.execute(
        "SELECT artifact_digest FROM modulrelease WHERE modul_id=%s"
        " AND release_id=%s", (MODUL, a.rullback_id)).fetchone()[0]
    etter = {
        "drillet_livslop": _deployment(m, drillet),
        "rullback_livslop": _deployment(m, a.rullback_id),
        "kandidat_livslop": _deployment(m, a.kandidat_id),
        "digest_likhet": kandidat_digest == digest,
        # Leses fra REGISTERET etter drillen, ikke fra planen: bytene
        # rullbakken faktisk bootet skal være forgjengerens (Codex P1,
        # #117 runde 6).
        "rullback_bytes_er_forgjengerens":
            rullback_digest == forgjenger_digest,
        "modulstatus": m.execute(
            "SELECT status FROM modulhode WHERE modul_id=%s",
            (MODUL,)).fetchone()[0],
    }
    art = {
        "krav_id": "rollback-m56-v1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "oppsett": {
            "modul": MODUL, "miljo": MILJO,
            "drillet_release": drillet,
            "rullback_release": a.rullback_id,
            "kandidat_release": a.kandidat_id,
            "drillet_digest": digest,
            "kandidat_digest": kandidat_digest,
            # Hvem drillen rullet tilbake TIL, og med hvilke bytes. Uten
            # disse kunne artefaktet ikke skille «rullet tilbake til
            # forgjengeren» fra «registrerte den drillede på nytt under et
            # annet navn» (Codex P1, #117 runde 6).
            "forgjenger_release": forgjenger,
            "forgjenger_digest": forgjenger_digest,
            "rullback_digest": rullback_digest,
            "module_epoch": int(epoch),
        },
        # HVA drillen faktisk så, ikke bare HVOR MANGE. Codex' P2 på PR
        # #117 (runde 3): akseptens `--e2e-artefakt` gikk rett inn i den
        # immutable raden, og FK-en i 049 sjekker bare tenant, modul,
        # release og promotert tilstand. Et hvilket som helst annet
        # promotert artefakt fra kandidatreleasen passerte derfor, mens
        # drillartefaktet bare bar et ANTALL og ingen identitet — så
        # aksepten kunne referere et artefakt drillen aldri så. Nå bærer
        # artefaktet identitetene, og akseptporten krever at E2E-beviset
        # er ett av dem drillen faktisk observerte.
        "identiteter": {
            "inflight_oppdrag_id": str(inflight["oppdrag"]),
            "inflight_artefakter": sorted(inflight_artefakter),
            "rullback_oppdrag_id": str(o2),
            "rullback_artefakter": sorted(rullback_artefakter),
            "kandidat_oppdrag_id": str(o3),
            "kandidat_artefakter": sorted(kandidat_artefakter),
        },
        "maalt": {
            "inflight_oppdrag": 1,
            "inflight_utfall": inflight["utfall"],
            # MÅLT i basen (kvitteringskolonnen), aldri utledet av
            # utfallet: et feilet oppdrag uten kvittering er nettopp
            # formen SP-3 forbyr.
            "inflight_har_signert_kvittering": inflight_kvittering,
            # Evidensen bak utfallet MÅLES og skrives ned, slik at porten
            # kan regne motsigelsen ut på nytt i stedet for å tro på
            # tallet under.
            "inflight_promoterte_artefakter": len(inflight_artefakter),
            # Codex' P2 på PR #117: den forrige formen ga alltid 0 så
            # snart utfallet IKKE var `utfort` — en `feilet` jobb som
            # likevel hadde promotert et artefakt ble altså talt som et
            # rent utfall. Falskt verdikt er en MOTSIGELSE mellom utfall
            # og evidens, og den går begge veier: et `utfort` uten
            # evidens, og et ikke-`utfort` MED evidens.
            "falske_verdikter": 0 if (inflight["utfall"] == "utfort")
                                == bool(inflight_artefakter) else 1,
            "nye_oppdrag_claimet_av_drillet_release":
                claimet_under_drenering,
            "ventetid_ubehandlet_s": ventetid,
            # (b2) rullbakken selv: releasen ble BOOTET og gjorde arbeid.
            # Uten disse to måler artefaktet bare at den gamle arbeideren
            # sluttet å claime — ikke at det gikk an å rulle tilbake.
            "rullback_claimet_oppdrag": 1 if st_rb == "utfort" else 0,
            "rullback_promoterte_artefakter": len(rullback_artefakter),
            "rullback_overtakelse_s": rullback_overtakelse,
            "kandidat_claimet_oppdrag": 1 if st2 == "utfort" else 0,
            "kandidat_promoterte_artefakter": len(kandidat_artefakter),
            "overtakelse_s": overtakelse,
        },
        "etterkontroll": etter,
    }
    art["bestatt"] = (
        art["maalt"]["inflight_har_signert_kvittering"]
        and art["maalt"]["nye_oppdrag_claimet_av_drillet_release"] == 0
        and art["maalt"]["falske_verdikter"] == 0
        and art["maalt"]["rullback_claimet_oppdrag"] == 1
        and art["maalt"]["rullback_promoterte_artefakter"] >= 1
        and art["maalt"]["kandidat_promoterte_artefakter"] >= 1
        and etter["digest_likhet"]
        and etter["rullback_bytes_er_forgjengerens"]
        and etter["drillet_livslop"] == "draining"
        and etter["rullback_livslop"] == "draining"
        and etter["kandidat_livslop"] == "claiming"
        and etter["modulstatus"] == "aktiv")
    a.ut.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                               sort_keys=True) + "\n", encoding="utf-8")
    print(("GRØNN" if art["bestatt"] else "RØD")
          + f": artefaktet skrevet til {a.ut}")
    print("kandidatens E2E-artefakt (akseptens A2-bevis): "
          + ", ".join(kandidat_artefakter))
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
