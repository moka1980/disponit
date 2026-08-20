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

`--ut` MÅ peke på en ledig plass i en mappe som finnes: målet åpnes og
reserveres som aller første handling, før noe er registrert eller rullet
(Codex P2, #117 runde 8). En drill som ikke kan legge fra seg målingen
sin, er en ødeleggelse uten evidens — og et mål som alt finnes, er en
tidligere drills evidens som ikke skal overskrives. Den ene fila et
avbrutt forsøk kan etterlate seg, er en tom `.<navn>.delvis`.

ÉN DRILL AV GANGEN. Filreservasjonen gjelder bare ett filnavn, så den
kan ikke være gjerdet mot parallelle kjøringer (Codex P1, #117 runde 9):
drillen tar en sesjonsvarig advisory-lås på `modul:miljø` før tilstanden
leses, og holder den til prosessen dør. To driller som overlapper leser
samme claimende release og drenerer hverandres deployments midt i
målingen — og livsløpet er enveis, så det er ikke noe å angre på.

BRUK:
    sudo -E python3 deploy/staging/rollback-m56.py \
        --rullback-id wcag-r6 --kandidat-id wcag-r7 \
        --ut deploy/staging/artefakter/rollback-m56-v1-<ts>.json
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import importlib.util
import json
import os
import re
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
#: Motorimaget drillen MÅLTE i preflighten, som immutabel image-referanse
#: (`sha256:…`) — settes av `lokalt_motorimage()` og bæres inn i hver
#: sjekklistefase av `_kjor_faser`. Se der for hvorfor taggen ikke duger.
PINNET_MOTORIMAGE = ""
# Låserommet for flippedriller. Egen klasse, ikke delt med arbeiderens
# eller migrasjonenes nøkler, så en drill aldri kolliderer med noe annet
# enn en annen drill av samme modul+miljø.
DRILLNOKKEL = 915_774_056


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

    MOTORIMAGET ER PINNET (Codex P1, #117 runde 11). Preflighten leste
    `disponit-wcag-motor` og slapp taggen igjen; hver invokasjon under
    kjører sjekklistens fase 1 på nytt (den kjøres uansett `--fase`), og
    et `bygg.sh` som retagger det samme navnet i mellomtiden ville derfor
    gitt fasene et ANNET image enn det porten før rullingen målte — først
    oppdaget i fase 2, med den levende deploymenten drenert og
    rullbakk-ID-en brukt opp. `PINNET_MOTORIMAGE` er den immutable
    image-ID-en preflighten faktisk så, og den bæres inn i hver fase, så
    taggen kan flytte seg uten at drillen bytter bytes under seg selv.
    """
    if not PINNET_MOTORIMAGE:
        raise SystemExit("AVBRUTT: motorimaget er ikke pinnet — fasene skal"
                         " ikke kjøres på en tag som kan ha flyttet seg"
                         " siden porten målte den")
    for fase in faser:
        r = subprocess.run(
            [sys.executable, str(HER / "wcag-staging-sjekkliste.py"),
             "--evidens", str(evidens), "--fase", fase],
            env={**os.environ, "WCAG_RELEASE": release,
                 "WCAG_RUNDE_ID": f"drill-{release}",
                 "WCAG_MOTORIMAGE": PINNET_MOTORIMAGE},
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
    """Er kvitteringen SIGNERT — målt på signaturen, ikke på nyttelasten.

    Codex' P1 på PR #117: `kvittering IS NOT NULL` sier bare at det ligger
    en JSON-blob i feltet. Signaturen er en egen kolonne
    (`oppdrag.kvittering_signatur`), kolonnene varierer uavhengig, og
    kjøretidsrollen har direkte `UPDATE`. Samme krav som porten i
    `registrer_moduldrill` stiller — artefaktet skal måle det aksepten
    regnes av, ellers er en grønn drill og en avvist aksept samme kjøring.
    """
    m.execute("RESET ROLE")
    m.execute("SELECT set_config('disponit.tenant', %s, true)", (tenant,))
    rad = m.execute(
        "SELECT kvittering IS NOT NULL"
        "   AND kvittering_signatur IS NOT NULL"
        "   AND btrim(kvittering_signatur) <> ''"
        "   AND kvittering_signatur"
        "       IS NOT DISTINCT FROM (kvittering->'signatur'->>'verdi')"
        "   AND resultathash IS NOT NULL"
        "  FROM oppdrag WHERE tenant=%s AND id=%s",
        (tenant, oid)).fetchone()
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


def forgjengerens_bytes(m, drillet: str, kver: int,
                        khash: str) -> tuple[str, str]:
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

    OG DEN HISTORIEN ER KONTRAKTLINJENS, ikke modulens (Codex P1, #117
    runde 10). Oppslaget filtrerte på modul, miljø, release-id og tid,
    men `en_claiming_per_kontrakt` fører én linje PER (modul, miljø,
    kontraktversjon, kontrakt_hash), og `den_ene_claimende` har alt valgt
    NØYAKTIG én av dem. Den seneste raden før den drillede kunne derfor
    tilhøre en helt annen kontraktslekt: `bytt_release` opererer innen
    den valgte kontrakten, så digesten derfra enten avbrøt en gyldig
    drill, eller — når digestene tilfeldigvis er like — lot artefaktet
    navngi en rad som ikke er denne linjens forgjenger. Både historikk-
    oppslaget, tidspunktet det måles mot og release-joinen bindes derfor
    til kontrakten som drilles.
    """
    rad = m.execute(
        "SELECT d.release_id, r.artifact_digest"
        "  FROM moduldeployment d JOIN modulrelease r"
        "    ON r.modul_id = d.modul_id AND r.release_id = d.release_id"
        "   AND r.kontraktversjon = d.kontraktversjon"
        "   AND r.kontrakt_hash = d.kontrakt_hash"
        " WHERE d.modul_id=%s AND d.miljo=%s AND d.release_id <> %s"
        "   AND d.kontraktversjon=%s AND d.kontrakt_hash=%s"
        "   AND d.fra_ts <= (SELECT fra_ts FROM moduldeployment"
        "                     WHERE modul_id=%s AND miljo=%s"
        "                       AND release_id=%s"
        "                       AND kontraktversjon=%s"
        "                       AND kontrakt_hash=%s)"
        " ORDER BY d.fra_ts DESC, d.release_id DESC LIMIT 1",
        (MODUL, MILJO, drillet, kver, khash,
         MODUL, MILJO, drillet, kver, khash)).fetchone()
    if rad is None:
        raise SystemExit(
            f"AVBRUTT: {drillet} har ingen forgjenger på kontrakt"
            f" v{kver}/{str(khash)[:12]}… i {MILJO} — det finnes"
            " ingenting å rulle tilbake TIL på linjen som drilles, og en"
            " drill som ruller tilbake til seg selv (eller til en annen"
            " kontraktslekt) måler ingen rullbakk")
    return rad[0], rad[1]


def lokalt_motorimage() -> str:
    """Digesten bootveien FAKTISK vil bære — lest her, og PINNET.

    Sjekklistens fase 1 kjører `docker image inspect --format {{.Id}}
    disponit-wcag-motor`, og fase 2 registrerer nøyaktig den verdien som
    releaseradens `artifact_digest`. Drillen leser samme tag, med samme
    kommando, og får dermed forhånds-svaret på det fase 2 senere vil
    kreve.

    OG SVARET HOLDES FAST (Codex P1, #117 runde 11). Å lese taggen her og
    slippe den igjen målte bare et øyeblikksbilde: `bygg.sh` retagger det
    samme navnet, sjekklistens fase 1 kjøres på nytt i hver av `_kjor_faser`
    sine invokasjoner, og de invokasjonene ligger ETTER at drillen har
    drenert den levende deploymenten og brukt opp rullbakk-ID-en. En
    retagging i det vinduet ga altså fasene andre bytes enn porten godkjente,
    oppdaget først som en immutabilitetskonflikt i fase 2 — med staging
    stående uten claimende arbeider. Image-ID-en er immutabel der taggen
    ikke er, så den settes som `PINNET_MOTORIMAGE` og bæres inn i hver
    fase; flytter taggen seg etterpå, rører det ikke denne drillen.

    -> `artifact_digest`-formen (64 hex, uten `sha256:`-forstavelsen).
    """
    global PINNET_MOTORIMAGE
    ut = subprocess.run(["docker", "image", "inspect", "--format",
                         "{{.Id}}", "disponit-wcag-motor"],
                        capture_output=True, text=True)
    if ut.returncode != 0:
        raise SystemExit(
            "AVBRUTT: fant ikke motorimaget `disponit-wcag-motor` på"
            " verten. Bootveien (sjekklistens fase 1) dør på det samme —"
            " men da etter at rullingen har drenert den levende"
            " deploymenten. Bygg imaget først:\n  bash"
            f" {REPO}/platform/modules/m56_wcag_audit/motor_axe/bygg.sh")
    digest = ut.stdout.strip().split(":")[-1]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SystemExit(
            f"AVBRUTT: `docker image inspect` ga {ut.stdout.strip()!r},"
            " ikke en sha256-digest — fase 2 ville avvist den samme"
            " verdien når releaseraden skulle skrives")
    PINNET_MOTORIMAGE = f"sha256:{digest}"
    return digest


def krev_bootbare_forgjengerbytes(forgjenger: str, forgjenger_digest: str,
                                  drillet_digest: str,
                                  lokal_digest: str) -> None:
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

    OG IMAGET PÅ VERTEN MÅLES MED (Codex P1, #117 runde 10). Sammen-
    ligningen sto før mellom to digester lest ut av REGISTERET, mens
    påstanden den bar handlet om et image på disken. «Den drillede
    releasen ble registrert fra samme lokale bygg» er en historisk
    kjensgjerning, ikke en nåværende: `disponit-wcag-motor` er en
    flyttbar tag, og et nytt `bygg.sh` mellom registreringen og drillen
    flytter den. Første gang bytene ble sett på, var i fase 1/2 — ETTER
    at `bytt_release` hadde drenert den levende deploymenten, og da dør
    fase 2 på immutabilitetskonflikten med den gamle arbeideren fenset
    og rullbakk-id-en brukt opp. Verten spørres derfor her, før racet og
    før rullingen: bærer taggen andre bytes enn forgjengeren, er det
    ikke forgjengerens rullbakk drillen ville kjørt.
    """
    if forgjenger_digest != drillet_digest:
        raise SystemExit(
            f"AVBRUTT: forgjengeren {forgjenger} bærer digest"
            f" {str(forgjenger_digest)[:19]}…, mens den drillede releasen"
            f" bærer {str(drillet_digest)[:19]}…. Sjekklistens fase 1/2"
            " pinner det LOKALT BYGDE motorimaget, så drillen kan ikke"
            " boote forgjengerens bytes — og en rullbakk til de drillede"
            " bytene prøver ikke det man ruller tilbake til. Bygg"
            " forgjengerens image på verten først, eller kjør rullbakken"
            " manuelt og bind beviset for hånd.")
    if lokal_digest == forgjenger_digest:
        return
    raise SystemExit(
        f"AVBRUTT: motorimaget `disponit-wcag-motor` på verten bærer"
        f" digest {lokal_digest[:12]}…, mens forgjengeren {forgjenger}"
        f" (og den drillede releasen) bærer {str(forgjenger_digest)[:12]}…."
        " Det er vertens image sjekklistens fase 1/2 pinner og registrerer,"
        " så drillen ville rullet tilbake til andre bytes enn de den sier"
        " — og fase 2 ville dødd på immutabilitetskonflikten FØRST etter"
        " at rullingen hadde drenert den levende deploymenten. Bygg"
        " forgjengerens image på verten (eller kjør drillen fra det"
        " utsjekket releasene ble bygget fra) før du driller.")


def registrer_drillrelease(m, release: str, kver: int, khash: str,
                           digest: str, *, hva: str, flagg: str) -> None:
    """Skriver drillreleaseraden — UBETINGET, før den destruktive
    rullingen (Codex P1, #117 runde 6; P2, runde 7).

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
    begge drillreleasene bootes senere gjennom sjekklistens egne faser, og
    `registrer-m-wcag-audit.py` regner da hashen ut av manifest.yaml på
    disk. Skrev vi den drillede radens hash her, ville den passive
    registreringen og fase 2 vært to ULIKE påstander om samme immutable
    rad — og fase 2 ville dødd på en konflikt drillen selv lagde.

    RULLBAKKENS DIGEST ER FORGJENGERENS, ikke den drilledes (Codex P1,
    runde 6). Verdiene er like i dag — `krev_bootbare_forgjengerbytes` har
    alt målt det — men KILDEN er poenget: en rullbakk som henter bytene
    sine fra releasen den ruller VEKK fra, ruller ingen steder.

    OG KANDIDATEN GÅR SAMME VEI (Codex P2, runde 7). Porten foran drillen
    målte bare `moduldeployment`, så en `--kandidat-id` som alt lå i
    `modulrelease` — uten deployment, med avvikende immutabelt innhold —
    ble lest som «ubrukt». Konflikten dukket da opp i KANDIDATENS fase 2,
    altså etter at drillen hadde rullet, målt hele (a)/(b)/(b2) og fase 1
    alt hadde stoppet rullbakk-arbeideren: rullbakk-deploymenten sto
    claiming uten arbeider, og begge drill-id-ene var konsumert. Samme
    hull, samme rot, samme rettelse — raden skrives, eller drillen stopper
    før den rører noe.

    Kandidatens digest er den DRILLEDE releasens: sjekklistens fase 1/2
    pinner det lokalt bygde motorimaget, så det er de bytene kandidaten
    kan bootes på — og `krev_bootbare_forgjengerbytes` har alt målt at
    hele drillen står på samme image.
    """
    manifest = _manifest_hash()
    _admin(m)
    try:
        m.execute("SELECT registrer_release(%s,%s,%s,%s,%s,%s,'m56-drill')",
                  (MODUL, release, kver, khash, manifest, digest))
        m.commit()
    except Exception as e:
        m.rollback()
        raise SystemExit(
            f"AVBRUTT: {hva}-releasen {release} finnes alt i registeret"
            " med et ANNET innhold enn drillen ville skrevet (kontrakt"
            f" v{kver}, manifest {manifest[:12]}…, digest"
            f" {str(digest)[:19]}…): {e}\nRaden er immutabel, så"
            " den kan ikke rettes — og hadde drillen kjørt videre likevel,"
            " ville fase 2 dødd på nøyaktig denne konflikten ETTER at den"
            " levende deploymenten var drenert. Kjør drillen med en ubrukt"
            f" {flagg}.") from e
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

    Codex' P2 (runde 7): de to drill-id-ene måtte også være FORSKJELLIGE
    fra hverandre. Var de like — og ubrukte — passerte alt her, mens
    drillen målte en umulighet: første `bytt_release` drenerer den
    levende releasen og gjør den delte id-en claiming, kandidatbyttet blir
    en no-op på en deployment som alt er der, og etterkontrollen leser
    SAMME rad som både rullback og kandidat. Den kan ikke være `draining`
    og `claiming` samtidig, så artefaktet er garantert rødt — etter at
    originaldeploymenten er brukt opp og staging kjører rullbakk-bytene.
    """
    if rullback == kandidat:
        raise SystemExit(
            f"AVBRUTT: --rullback-id og --kandidat-id er samme release"
            f" ({rullback}). Drillen måler at rullbakken DRENERES og at"
            " kandidaten OVERTAR, og én deployment kan ikke være begge:"
            " rullingen ville drenert den levende releasen, kandidatbyttet"
            " blitt en no-op, og drillen endt rødt på en umulighet den"
            " selv laget. Kjør med to ubrukte, ULIKE id-er.")
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


def reserver_artefaktmaal(ut: Path) -> Path:
    """Åpner målet FØR drillen, og reserverer plassen den skal skrives til.

    Codex' P2 på PR #117 (runde 8): `--ut` ble først rørt på aller siste
    linje — etter begge `bytt_release`-ene, etter at alle drilljobbene
    hadde brukt opp sine engangs-deploymentIDer. Livsløpet er ENVEIS: en
    drenert release kan ikke claimes igjen, og id-ene er brukt opp i det
    de er kjørt. En uskrivbar eller ikke-eksisterende foreldermappe ga
    derfor en FULLFØRT destruktiv drill og ingen måling — den eneste
    kopien av evidensen forsvant i en `FileNotFoundError`, og drillen kan
    ikke gjøres om igjen. Pekte stien på en eksisterende evidensfil, var
    utfallet motsatt og verre: en tidligere drills artefakt ble stille
    overskrevet av denne.

    Tre ting avgjøres her, mens ingenting ennå er ødelagt:

      * foreldermappen må FINNES. Vi lager den ikke — en feilskrevet sti
        er den vanligste grunnen til at den mangler, og en drill som er
        enveis skal stoppe på den, ikke lage mappen og kjøre videre.
      * målet må IKKE finnes. Evidens overskrives ikke.
      * mappen må være skrivbar — bevist ved å SKRIVE, ikke ved å spørre
        `os.access` (som svarer for uid-en, ikke for monteringen, og
        uansett bare om fortiden). Den reserverte filen er den samme som
        artefaktet senere skrives i, så det som lykkes her er nøyaktig
        det som må lykkes til slutt.

    -> stien til den reserverte, tomme delvisfilen.
    """
    ut = ut.expanduser()
    if not ut.parent.is_dir():
        raise SystemExit(
            f"AVBRUTT: {ut.parent} finnes ikke (eller er ingen mappe), så"
            f" artefaktet kunne ikke vært skrevet. Drillen er destruktiv og"
            " enveis — den stoppes FØR den kjøres, ikke etterpå.")
    if ut.exists():
        raise SystemExit(
            f"AVBRUTT: {ut} finnes alt. Det er en tidligere drills"
            " evidens, og den overskrives ikke. Velg et nytt filnavn.")
    delvis = ut.parent / f".{ut.name}.delvis"
    try:
        # O_EXCL: reservasjonen er vår, eller så finnes den fra før.
        # Codex' P1 (runde 9): navnet bar PID-en, og da reserverte det
        # ingenting — hver prosess fikk sin egen sti, og O_EXCL kunne per
        # konstruksjon aldri kollidere. Uten PID-en er stien en funksjon
        # av `--ut` alene, og reservasjonen betyr det den sier.
        os.close(os.open(delvis, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        raise SystemExit(
            f"AVBRUTT: {delvis} er alt reservert. Enten kjører en annen"
            f" drill mot {ut} akkurat nå — to samtidige driller av samme"
            " modul måler hverandres halve tilstand — eller så er den en"
            " avbrutt drills reservasjon. Er den TOM kan den fjernes; har"
            " den innhold, er den en måling som aldri ble flyttet på plass,"
            " og den skal tas vare på.")
    except OSError as e:
        raise SystemExit(
            f"AVBRUTT: kan ikke skrive i {ut.parent} ({e}). Uten et sted å"
            " legge artefaktet er drillen en ødeleggelse uten måling.")

    def rydd():
        # Bare den TOMME reservasjonen ryddes. Er det skrevet noe i den,
        # er det en måling som ikke rakk å bli flyttet, og den skal bli
        # liggende — det er hele poenget med å reservere.
        try:
            if delvis.stat().st_size == 0:
                delvis.unlink()
        except OSError:
            pass

    atexit.register(rydd)
    return delvis


def den_ene_claimende(m) -> tuple[str, int, str, str]:
    """Den claimende deploymenten drillen skal måle — eller ingen drill.

    Codex' P2 på PR #117 (runde 9): oppslaget hadde verken kontraktvelger
    eller `ORDER BY`, og `fetchone()` kastet stille resten. Formen leser
    som «den claimende deploymenten», men `en_claiming_per_kontrakt`
    håndhever én claiming PER (modul, miljø, kontraktversjon,
    kontrakt_hash) — flere kontraktlinjer kan altså stå claiming
    samtidig, og det er en TILLATT tilstand, ikke en umulig.

    Da plukket drillen en vilkårlig av dem. Hele drillen henger på den
    ene raden: `bytt_release` drenerer kun deploymenten som matcher den
    VALGTE kontrakten, så den andre blir stående claiming gjennom hele
    kjøringen — levende, og fri til å plukke drillens egne
    probeoppdrag. `nye_oppdrag_claimet_av_drillet_release` måles på
    oppdragets status, ikke på hvem som tok det, så claim-stoppet (a)
    kunne stå rødt fordi en helt annen arbeider gjorde jobben — eller
    (b2)/(c) grønt av arbeid ingen av drillens releaser utførte.
    Ingenting i artefaktet ville vist hvilken av delene som skjedde.

    Drillen tar ikke det valget. Alle radene hentes, og kan den ikke
    peke ut nøyaktig én kontraktlinje, avbryter den — før låsen er brukt
    til noe, og før noe er registrert eller rullet.

    -> (release_id, kontraktversjon, kontrakt_hash, artifact_digest)
    """
    rader = m.execute(
        "SELECT d.release_id, d.kontraktversjon, d.kontrakt_hash,"
        "       r.artifact_digest"
        "  FROM moduldeployment d JOIN modulrelease r"
        "    ON r.modul_id = d.modul_id AND r.release_id = d.release_id"
        "   AND r.kontraktversjon = d.kontraktversjon"
        "   AND r.kontrakt_hash = d.kontrakt_hash"
        " WHERE d.modul_id=%s AND d.miljo=%s AND d.livslop='claiming'"
        " ORDER BY d.kontraktversjon, d.kontrakt_hash, d.release_id",
        (MODUL, MILJO)).fetchall()
    if not rader:
        raise SystemExit("AVBRUTT: ingen claiming-deployment å drille")
    if len(rader) > 1:
        linjer = "\n".join(
            f"  {rel} (kontrakt v{kv}/{kh[:12]}…, digest {dg[:12]}…)"
            for rel, kv, kh, dg in rader)
        raise SystemExit(
            f"AVBRUTT: {MODUL} har {len(rader)} claimende deployments i"
            f" {MILJO}:\n{linjer}\n"
            "En flippedrill måler ÉN kontraktlinje: `bytt_release`"
            " drenerer bare deploymenten som matcher den valgte"
            " kontrakten, så de andre står claiming gjennom hele drillen"
            " og kan plukke dens egne probeoppdrag. Da måler artefaktet"
            " ikke lenger hvem som gjorde hva. Rull de øvrige"
            " kontraktlinjene ut av claiming først, og kjør drillen mot"
            " den ene som står igjen.")
    return rader[0]


def ta_drillereservasjonen(m) -> None:
    """Én flippedrill av gangen per modul+miljø — hele kjøringen igjennom.

    Codex' P1 på PR #117 (runde 9): kommentaren over `O_EXCL` påsto at
    reservasjonen stoppet parallelle driller. Den gjorde ikke det. PID-en
    i navnet ga hver prosess sin egen sti (rettet over), og selv uten den
    hjalp filen bare mot samme `--ut` — to driller med hvert sitt filnavn
    passerte uansett. Noen modulbred lås fantes ikke: `bytt_release` tar
    sin lås per overgang og slipper den, så mellom overgangene er det
    ingenting som holder.

    Og det er nettopp mellom overgangene drillen bor. To kjøringer som
    overlapper leser den SAMME claimende releasen, og begge går inn i et
    enveisløp bygget på at ingen andre flytter registeret under dem:

      * begge registrerer sine drill-releaser og ruller. Den andre
        rullingen drenerer den førstes rullbakk-deployment — som den
        første i (b2) nettopp bootet og venter på skal fullføre et
        oppdrag. Claim-porten fencer arbeideren midt i målingen, og den
        førstes drill dør rødt på noe den ikke gjorde.
      * `krev_ubrukte_drillreleaser` fanger det ikke: den måler
        tilstanden FØR sin egen kjøring, og de to id-parene er ubrukte i
        hvert sitt oppslag. Porten er sann i det den leses, og usann et
        øyeblikk senere.
      * begge id-parene er da konsumert, livsløpet er enveis, og staging
        står igjen i en tilstand ingen av de to artefaktene beskriver.

    Låsen er derfor på SESJONEN, ikke på transaksjonen: den tas her, før
    den claimende deploymenten i det hele tatt leses, og holdes til
    prosessen dør. `m.commit()` underveis rører den ikke, og en drill som
    krasjer slipper den i det forbindelsen lukkes — så en henger som er
    borte, blokkerer ikke neste kjøring.

    `try`, ikke blokkerende: en drill som står i kø og starter når den
    første er ferdig, ville målt en HELT annen tilstand enn den leste
    argumentene for — den drillede releasen er da drenert og
    drill-id-ene brukt opp. Riktig svar er å si ifra, ikke å vente.
    """
    fikk = m.execute("SELECT pg_try_advisory_lock(%s, hashtext(%s))",
                     (DRILLNOKKEL, f"{MODUL}:{MILJO}")).fetchone()[0]
    m.commit()
    if not fikk:
        raise SystemExit(
            f"AVBRUTT: en annen flippedrill av {MODUL} i {MILJO} holder"
            " drillåsen. To samtidige driller leser samme claimende"
            " release og går inn i hvert sitt enveisløp — den ene drenerer"
            " den andres rullbakk-deployment midt i målingen, og begge"
            " par drill-id-er er brukt opp uten at noen av artefaktene"
            " beskriver tilstanden staging står igjen i. Vent til den"
            " andre drillen er ferdig; låsen slippes når prosessen dør.")


def skriv_artefakt(delvis: Path, ut: Path, innhold: str) -> None:
    """Flytter det ferdige artefaktet på plass uten å kunne overskrive.

    `os.link` er atomisk OG feiler hvis målet finnes — i motsetning til
    `os.replace`, som ville overskrevet en evidensfil som dukket opp mens
    drillen kjørte. Skulle flyttingen likevel ryke, blir delvisfilen
    liggende og stien skrevet ut: en destruktiv, uigjentakelig drill skal
    aldri ende med at målingen kastes.
    """
    with open(delvis, "w", encoding="utf-8") as f:
        f.write(innhold)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(delvis, ut)
    except OSError as e:
        raise SystemExit(
            f"ARTEFAKTET ER SKREVET, MEN IKKE FLYTTET: {e}\n"
            f"  målingen ligger i {delvis} — ta vare på den, drillen kan"
            " ikke kjøres om igjen.")
    os.unlink(delvis)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rullback-id", required=True)
    ap.add_argument("--kandidat-id", required=True)
    ap.add_argument("--ut", type=Path, required=True)
    ap.add_argument("--forsok", type=int, default=3,
                    help="maks forsøk på å treffe rullingen midt i et"
                         " løpende oppdrag")
    a = ap.parse_args()

    # FØRST AV ALT: målet for artefaktet åpnes og reserveres, før noe som
    # helst er registrert, rullet eller brukt opp (Codex P2, #117 runde 8).
    a.ut = a.ut.expanduser()
    delvis = reserver_artefaktmaal(a.ut)

    global RUNDE_ID
    # Per-INVOKASJON, ikke per kandidat: en ny drillkjøring er en ny
    # måling og skal aldri gjenspille en gammel kjørings terminale
    # oppdrag som om de var dagens (idempotensnøklene er drill-skopet).
    RUNDE_ID = f"{a.kandidat_id}-{int(time.time())}"
    sj = _sjekkliste()
    env = sj._miljo()
    m = sj._pg(env["DISPONIT_MIGRATOR_URL"])
    # …og FØR tilstanden i det hele tatt leses: én drill av gangen per
    # modul+miljø, holdt av sesjonen hele kjøringen (Codex P1, #117
    # runde 9). Alt under bygger på at ingen andre flytter registeret.
    ta_drillereservasjonen(m)

    # Utgangspunktet: den claimende deploymenten er den som drilles.
    drillet, kver, khash, digest = den_ene_claimende(m)
    krev_ubrukte_drillreleaser(m, drillet, a.rullback_id, a.kandidat_id)
    # Rullbakken skal bære FORGJENGERENS bytes, ikke den drilledes
    # (Codex P1, #117 runde 6) — og bootveien må kunne kjøre dem.
    forgjenger, forgjenger_digest = forgjengerens_bytes(m, drillet,
                                                        kver, khash)
    krev_bootbare_forgjengerbytes(forgjenger, forgjenger_digest, digest,
                                  lokalt_motorimage())
    epoch = m.execute("SELECT module_epoch FROM modulhode WHERE modul_id=%s",
                      (MODUL,)).fetchone()[0]
    print(f"driller {drillet} (epoch {epoch}, digest {digest[:12]}…),"
          f" rullbakk til forgjengeren {forgjenger}s bytes"
          f" ({forgjenger_digest[:12]}…)")

    # BEGGE drillreleasene registreres FØR racet — registreringen er
    # passiv (en rad i `modulrelease`, ingen deployment), mens rullingen
    # er ett kall som fyres midt i det løpende oppdraget. Kandidaten er
    # med her fordi porten over bare måler `moduldeployment`: en
    # eksisterende, avvikende kandidatRAD ville ellers først blitt
    # oppdaget i kandidatens fase 2 — etter rullingen, og etter at fase 1
    # hadde stoppet rullbakk-arbeideren (Codex P2, #117 runde 7).
    registrer_drillrelease(m, a.rullback_id, kver, khash, forgjenger_digest,
                           hva="rullback", flagg="--rullback-id")
    registrer_drillrelease(m, a.kandidat_id, kver, khash, digest,
                           hva="kandidat", flagg="--kandidat-id")

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
    skriv_artefakt(delvis, a.ut,
                   json.dumps(art, indent=2, ensure_ascii=False,
                              sort_keys=True) + "\n")
    print(("GRØNN" if art["bestatt"] else "RØD")
          + f": artefaktet skrevet til {a.ut}")
    print("kandidatens E2E-artefakt (akseptens A2-bevis): "
          + ", ".join(kandidat_artefakter))
    return 0 if art["bestatt"] else 1


if __name__ == "__main__":
    sys.exit(main())
