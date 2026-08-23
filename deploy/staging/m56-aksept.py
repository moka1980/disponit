#!/usr/bin/env python3
"""Akseptflippen for m56 — skriver drillraden og aksepthendelsen (049).

Kjøres PÅ verten ETTER at migrasjon 049 er deployet. Skriptet finner
selv opp ingenting: hvert tall og hver referanse leses fra de
INNSJEKKEDE, sha256-bundne artefaktene (drillartefaktet og
runde-sammendraget) og fra basen — og lagringen håndhever resten
(FK-kjedene i 049: drill for nøyaktig denne deploymentraden, promotert
E2E-artefakt fra samme release, komplett punktsett eller ingen
hendelse).

Punktobservasjonene (A3) bygges fra kravpunkt-registeret i basen:
måletallene fra runde-sammendraget der de finnes, CI-kjøringen som
kilde for de rene invariantpunktene (skjema.*, egress-tokenet,
malautorisasjonens negativporter) — de har ingen historiske rader og
kan bare bevises «grønne da» av kjøringen på akseptcommiten. Den
kjøringen slås opp og må være ferdig, grønn og kjørt på nettopp den
commiten; `--ci-commit` er derfor akseptcommiten, ikke en fri streng.
Og den må være `ci.yml`s PUSH-kjøring på `main` — ikke en PR-kjøring på
det samme forslaget, som er en helt annen påstand (se
`_vurder_ci_kjoring`).

BRUK (kjøres ETTER merge, fra et utsjekk av `main`):
    sudo -E python3 deploy/staging/m56-aksept.py \
        --drill deploy/staging/artefakter/rollback-m56-v1-<ts>.json \
        --runde deploy/staging/artefakter/wcag-kontroll-v1-<ts>.json \
        --e2e-artefakt <uuid fra drillens kandidat-kjøring> \
        --ci-run <workflow-run-id> --ci-commit <sha> [--manifest-commit <sha>]

    Run-ID-en er push-kjøringen på merge-commiten:
        gh run list --workflow ci.yml --branch main --event push \
            --commit <sha> --json databaseId,conclusion
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HER = Path(__file__).resolve().parent
REPO = HER.parents[1]
sys.path.insert(0, str(REPO / "platform/core"))

MODUL = "m_wcag_audit"   # registernavnet (modulmappen heter m56_wcag_audit)
MILJO = "staging"
# 050: grensen ble SYNLIG reversjonert (BESLUTNING-AKSEPTFLIPP-049 §1)
# — proxytoken-punktet krevde en mekanisme som ikke finnes og er
# erstattet av de to målbare egress-invariantene; endringsnotatet står i
# migrasjonsfila. Registeret er append-only med krav-lås, så revisjonen
# ER et nytt krav_id.
#: v3 (052, aksept-arc-klarsignalet): v2s 22 punkter pluss de sju
#: evidenspunktene fra §5 — drillens to (målt av drillraden selv),
#: attest-/revisjonsrad-avvikene og datasett-likheten (målt i runden),
#: og de to sammenhengspunktene (§2: én runde, én drill, én
#: manifestgenerasjon; aldri gjenbrukt gammel evidens).
KRAV = "m56-akseptflipp-v3"
#: Artefaktkravet fulgte MED målekoden (052): en runde som målte
#: datasett-identiteten og kjøringsattestene sammendras som
#: `wcag-kontroll-v2` — og aksepten krever nettopp de målingene, så
#: v1-artefaktet fra 2026-08-18 kan aldri bli akseptbevis igjen
#: (klarsignalet §2: de gamle målingene er historikk). Fortsatt to
#: akser: artefaktkravet er BEVISENES navn, KRAV er AKSEPTENS.
ARTEFAKT_KRAV = "wcag-kontroll-v2"
DRILLKRAV = "rollback-m56-v1"
TENANT = "t-wcagfasit"
#: Kontrolløpets oppdragstype — samme som sjekklisten bestiller
#: (Codex P1, #125 r3: en modul kan eie flere registrerte typer; ti
#: vellykkede jobber av en annen type er ikke kontrolløpet). Basen
#: deriverer sin egen fasit fra drillens inflight-oppdrag; dette er
#: fail-fast-speilets kopi.
OPPDRAGSTYPE = "kontroll.wcag.nettsted"
MANIFEST_REL = "platform/modules/m56_wcag_audit/manifest.yaml"
MANIFEST_STI = REPO / MANIFEST_REL
#: Workflowen invariantpunktene faktisk hviler på. Repoet har flere
#: workflows, og en grønn kjøring av en ANNEN på samme commit beviser
#: ingenting om testene her (Codex, #117).
CI_WORKFLOW = ".github/workflows/ci.yml"
#: ... og HENDELSEN den må ha kjørt på. `ci.yml` trigges både av
#: `pull_request` og av push til `main`, og bare den siste sier at bytene
#: faktisk ble en del av historikken (Codex P2, #117).
CI_HENDELSE = "push"
HOVEDGREN = "main"

#: grensepunkt → (kilde_type, hvordan verdien hentes). Måletallene peker
#: på runde-sammendraget (selv sha-bundet i manifestet); de rene
#: invariantpunktene bærer CI-kjøringen som kilde. Grensene er §12s.
MAALTE = {
    # SIGNERT måles på signaturtallet, ikke på fristtallet (Codex P2,
    # #117 runde 15). Punktet heter «ti kjøringer SIGNERT innen frist»,
    # og verdien ble hentet fra `kjoringer_signert_innen_frist` — et felt
    # som målte frist og rent utfall, og aldri en signatur. Nå bærer
    # sammendraget de to tallene hver for seg, og punktet krever begge:
    # `krev_maalt_signatur` stopper aksepten hvis signaturen mangler.
    "kontroll.ti_kjoringer_signert_innen_frist":
        ("10/10", lambda m: f"{m['kjoringer_med_maalt_signatur']}"
                            f"/{m['kjoringer_krav']}"),
    "funn.avvik_mot_fasit": ("0", lambda m: str(m["avvik_mot_fasit"])),
    "robots.brudd_i_mallogg":
        ("0", lambda m: str(m["robots_private_forisporsler"])),
    # 0 utførte over grensen — og taket er MÅLT (minst ett faktisk
    # avslag), ellers er «0» bare fravær av forsøk.
    "frekvens.over_grense_utfort":
        ("0", lambda m: "0" if m["frekvens_avvist_over_grense"] >= 1
                        else "umålt (taket ga aldri avslag)"),
    # v2: port24-tallet under sitt RIKTIGE navn — målingen av at
    # hemmelighetene aldri når browser-containerens miljø. (Det var
    # dette tallet proxytoken-punktet lånte; nå bærer det sin egen
    # invariant, og bare den.)
    #
    # …og det leses sammen med probens EGEN tilstand (Codex P1, #121):
    # en port24-kjøring som ikke kom i gang, eller som døde med
    # returkode ≠ 0 før den rakk å skrive miljøet, har heller ingen
    # lekkasjer å vise. Da er «0» fravær av en måling, ikke et rent
    # containermiljø — nøyaktig samme form som frekvenstaket over.
    "egress.hemmeligheter_i_browsermiljo":
        ("0", lambda m: str(m["egress_lekkasjer"])
                        if m.get("egress_motormiljo_maalt") == 1
                        else "umålt (port24-proben kjørte ikke rent)"),
    # v3 (052, §1.3): avvikstallet teller bare når attesteringen NÅDDE
    # kravet — 9/10 attesterte med 0 avvik er én umålt kjøring, ikke en
    # ren runde. Samme form som frekvenspunktet over: den røde formen
    # skrives som tekst basen aldri godtar.
    "kvittering.attest_avvik":
        ("0 (10/10, 9/10 er rødt)",
         lambda m: str(m["kvittering_attest_avvik"])
                   if m["kjoringer_med_attestert_kvittering"]
                      == m["kjoringer_krav"]
                   else (f"rødt ({m['kjoringer_med_attestert_kvittering']}"
                         f"/{m['kjoringer_krav']} attestert)")),
    "revisjonsrad.avvik_mot_bestilt":
        ("0 (10/10 mot de bestilte)",
         lambda m: str(m["revisjonsrad_avvik"])
                   if m["revisjonsrader_mot_bestilt"]
                      == m["kjoringer_krav"]
                   else (f"rødt ({m['revisjonsrader_mot_bestilt']}"
                         f"/{m['kjoringer_krav']} rader)")),
}
#: §5-punktene som måles av DRILLRADEN selv. `registrer_moduldrill`
#: regner kontrollpunktutfallene i basen, og akseptens FK refererer
#: drillraden med de tre utfallsboolene I den refererbare nøkkelen
#: (E1f): en aksept mot en rad der ett av dem er false, finnes ikke.
#: Rullbakkens egen kvittering er en del av `claim_stopp_ok` (052), og
#: «én måling, én rad» er radens egen form — tenant, alle tre oppdrag
#: og artefaktets sha256 i samme rad, spleising har ingen vei inn.
#: `kilde_ref` er `rollback_drill`-registerhendelsen drillraden skrev.
DRILL_PUNKTER = {
    "drill.rullbakk_bootet_og_fullforte":
        ("ja (alle tre kontrollpunkter i samme drillrad)", "ja"),
    "drill.spleisede_malinger": ("0 (én måling, én rad)", "0"),
}
#: Punkter i kravet som INGEN kilde vi har faktisk måler. De skrives
#: ikke — de BLOKKERER aksepten, og teksten sier hvorfor. Mekanismen er
#: RATIFISERT som stående (beslutningsdokumentet §1): et umålt punkt
#: blokkerer, aldri pyntes.
#:
#: Tom i v2 — med vilje, og ikke ved at noen fant «nærmeste tall»:
#: proxytoken-punktet som sto her ble FJERNET av grenserevisjonen (050)
#: fordi mekanismen det målte ikke finnes; erstatningene er ekte målinger
#: med egne kilder. Bygges en utstedende egress-proxy, får dens arc
#: punktet tilbake i sin egen grense — og står det da umålt, skal det
#: rett inn her igjen.
UMAALTE: dict[str, str] = {}
CI_PUNKTER = (
    "skjema.brudd_promotert", "skjema.hash_uten_rad_akseptert",
    "skjema.mutert_ureferert", "skjema.slettet",
    "rapport.uten_pakrevd_arlighetsfelt_akseptert",
    "rapport.klartekst_i_logg_eller_dump",
    "domene.kontroll_uten_verifisering",
    "payload.felt_utover_skjema_utlevert",
    "deploy.registerrad_uten_kodefestet_type",
    "deploy.ekstern_lesing_uten_malautorisasjonsflagg",
    "klasse.eksisterende_kontrakt_omklassifisert",
    "klasse.aktivering_uten_frekvensgrense_lyktes",
    "klasse.aktivering_uten_malautorisasjon_lyktes",
    "malautorisasjon.ikke_registrert_vilkar_talte",
    "malautorisasjon.feil_maldomene_godtatt",
    # v2: 036-porten — aktiveringsveien krever ekstern_lesing-klasse med
    # målautorisasjonsflagg; porttestene er røde ved brudd.
    "egress.sideeffektklasse_gater_aktivering",
)


def krev_maalbare_punkter() -> None:
    """Ingen aksept så lenge et grensepunkt mangler en kilde som måler det.

    Codex' P1 på PR #117 (runde 5):
    `egress.proxytoken_til_ikke_ekstern_lesing` hentet verdien sin fra
    `egress_lekkasjer` — `port24_motormiljo`, som måler
    credential-lekkasje i browser-containeren og aldri ber om et
    proxytoken. Punktet kunne derfor stå «0» i en immutabel akseptrad selv
    om proxytoken-autorisasjonen var brutt for enhver ikke-`ekstern_lesing`
    modul.

    Feilen var ikke bare den ene tilordningen: kartet punkt→kilde var
    håndlaget, og ingenting KREVDE at kilden hadde stilt punktets eget
    spørsmål. Da er «finn et tall som passer» alltid den enkleste veien
    videre. Denne porten gjør det umulige valget synlig i stedet: et punkt
    uten ekte kilde står i `UMAALTE`, og da skrives ingen aksept i det hele
    tatt. Fail-closed — akkurat som `rollback_testet` er blokkert framfor
    å bli pyntet.
    """
    if not UMAALTE:
        return
    raise SystemExit(
        "AVBRUTT: aksepten er blokkert — "
        f"{len(UMAALTE)} grensepunkt i {KRAV} har ingen kilde som måler"
        " dem:\n\n  "
        + "\n\n  ".join(f"{p}:\n    " + s for p, s in sorted(UMAALTE.items()))
        + "\n\nEt punkt uten måling er ikke et grønt punkt, det er et"
          " umålt punkt, og en immutabel akseptrad skal ikke bære"
          " forskjellen som om den ikke fantes.")


def krev_maalt_signatur(m: dict) -> None:
    """Ingen aksept før signaturen på hver av de ti kjøringene ER målt.

    Codex' P2 på PR #117 (runde 15): grensepunktet
    `kontroll.ti_kjoringer_signert_innen_frist` hvilte på et tall som
    aldri hadde spurt om en signatur. Ti rapporter med usignerte eller
    manglende kvitteringer ga «10/10», og ordet «signert» i punktnavnet
    var hele beviset.

    Sammendraget skiller nå de to målingene, og runden fra 18/8 målte
    bare den ene: den kjørte før produsenten begynte å skrive
    `kvittering_signert` på `kjoring`-linjene, så signaturtallet er null
    — ikke fordi kvitteringene manglet signatur, men fordi ingen så
    etter. Det er nøyaktig forskjellen en immutabel akseptrad ikke skal
    bære som om den ikke fantes.

    Fail-closed, samme form som `krev_maalbare_punkter`: en runde som
    ikke har målt dette, aksepterer ingenting. Kjør sjekklisterunden på
    nytt, så bærer linjene målingen.
    """
    maalt, krav = (m.get("kjoringer_med_maalt_signatur"),
                   m.get("kjoringer_krav"))
    if isinstance(maalt, int) and not isinstance(maalt, bool) \
            and maalt == krav:
        return
    raise SystemExit(
        "AVBRUTT: aksepten er blokkert —"
        f" {KRAV}-punktet «kontroll.ti_kjoringer_signert_innen_frist»"
        f" krever at signaturen er MÅLT på alle {krav} kjøringene, men"
        f" sammendraget bærer {maalt!r}.\n\n  Fristtallet"
        " (`kjoringer_rent_innen_frist`) måler rent utfall innen egen"
        " frist og sier ingenting om noen kvittering. Signaturen er en"
        " egen måling: `kvittering_signert` på hver `kjoring`-linje, som"
        " sjekklisterunden gjør i basen — signaturen i sin egen kolonne,"
        " identisk med konvoluttens, med resultathash, og"
        " kvitteringskapabiliteten brent med nøyaktig den hashen.\n\n"
        "  Et punkt som heter «signert» kan ikke hvile på et tall som"
        " ikke er en signaturmåling. Kjør sjekklisterunden på nytt.")


def les_manifest() -> tuple[dict, str]:
    """Manifestet og sha256 av de BYTENE som ble tolket. -> (innhold, sha)."""
    import yaml
    raa = MANIFEST_STI.read_bytes()
    return (yaml.safe_load(raa.decode("utf-8")),
            hashlib.sha256(raa).hexdigest())


def _git(*argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *argv], capture_output=True)


def loes_akseptcommit(oppgitt: str | None) -> str:
    """Commiten akseptraden skal peke på — full sha, og den må finnes.

    `--manifest-commit` var før en helt ukontrollert streng: hva som helst
    kunne skrives inn i den immutable raden. Nå må verdien peke på en
    faktisk commit i dette repoet før noe måles mot den.
    """
    ref = oppgitt or "HEAD"
    r = _git("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    sha = r.stdout.decode().strip()
    if r.returncode != 0 or len(sha) != 40:
        raise SystemExit(f"AVBRUTT: manifest-commit {ref!r} er ingen commit i"
                         " dette repoet")
    return sha


def bind_til_commit(commit: str, rel: str, sha: str) -> None:
    """De validerte bytene må VÆRE bytene commiten inneholder.

    Codex' P1 på PR #117 (runde 2): hash-, skjema- og grensekontrollene
    hadde ARBEIDSTREET som tillitsrot, mens `manifest_commit` var en
    ukontrollert CLI-verdi eller bare `HEAD`. Endres manifestet og
    artefaktene sammen før kommandoen kjøres, passerer alt — og
    akseptraden peker på en commit som ikke inneholder én eneste av de
    bytene den påstår å bevise. Her måles hvert ledd i kjeden
    (manifest → artefakt → råfil) mot commitens egne blober.
    """
    r = _git("cat-file", "blob", f"{commit}:{rel}")
    if r.returncode != 0:
        raise SystemExit(f"AVBRUTT: {rel} finnes ikke i {commit[:12]}… —"
                         " akseptraden ville pekt på en commit uten dette"
                         " beviset")
    i_commit = hashlib.sha256(r.stdout).hexdigest()
    if i_commit != sha:
        raise SystemExit(f"AVBRUTT: {rel} i arbeidstreet er ikke fila i"
                         f" {commit[:12]}… — {sha[:12]}… mot"
                         f" {i_commit[:12]}…; ucommitede bytes er ikke evidens")


def _repo_slug() -> str:
    r = _git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]+([^/]+/[^/]+?)(?:\.git)?$",
                  r.stdout.decode().strip())
    if r.returncode != 0 or not m:
        raise SystemExit("AVBRUTT: finner ingen GitHub-remote å slå"
                         " CI-kjøringen opp mot")
    return m.group(1)


def _hent_ci_kjoring(run_id: str) -> dict:
    """Kjøringen slik GitHub kjenner den — eller avbrudd. Fail-closed:
    et punkt ingen bekreftet kjøring bærer, skrives ikke."""
    slug = _repo_slug()
    url = f"https://api.github.com/repos/{slug}/actions/runs/{run_id}"
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "m56-aksept"})
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=30) as svar:
            return json.loads(svar.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"AVBRUTT: CI-kjøring {run_id} kan ikke slås opp i"
                         f" {slug} (HTTP {e.code}) — en kjøring som ikke"
                         " finnes, beviser ingenting")
    except (OSError, ValueError) as e:
        raise SystemExit(f"AVBRUTT: oppslaget av CI-kjøring {run_id} feilet"
                         f" ({type(e).__name__}) — invariantpunktene kan ikke"
                         " skrives uten at kjøringen er bekreftet grønn")


def _vurder_ci_kjoring(data: dict, run_id: str, ci_commit: str) -> list[str]:
    """De fire påstandene invariantpunktene hviler på, målt på svaret."""
    feil: list[str] = []
    if str(data.get("id")) != str(run_id):
        feil.append(f"oppslaget svarte med kjøring {data.get('id')!r}, ikke"
                    f" {run_id}")
    # Codex' P1 på PR #117 (runde 3): id, terminalstatus, conclusion og
    # sha sa ingenting om HVILKEN workflow som kjørte. Repoet har også
    # `claude.yml`, og en grønn kjøring DERFRA på akseptcommiten kjører
    # ikke én eneste av invarianttestene punktene påberoper seg — like
    # fullt bar den alle 16. Punktene hviler på testene i `ci.yml`, så
    # det er den workflowen som må ha kjørt.
    if (data.get("path") or "") != CI_WORKFLOW:
        feil.append(f"kjøringen er {data.get('path') or '?'}, ikke"
                    f" {CI_WORKFLOW} — bare invarianttestene der bærer"
                    " punktene")
    # ... og HENDELSEN må være pushen til `main` (Codex P2, #117 runde 11).
    # `ci.yml` kjører for BÅDE `pull_request` og push til `main`, og
    # kontrollene over — sti, conclusion, head_sha — passerer like godt på
    # en grønn PR-kjøring. Kjøres kommandoen fra et PR-utsjekk, er den
    # commiten bare et forslag: `loes_akseptcommit` krever at den finnes
    # lokalt, ikke at den er nådd fra `main`, så en immutabel aksept kunne
    # skrives på bytes som aldri ble merget — og kanskje aldri blir det.
    # Utrullingen skjer ETTER merge (RUTINER pkt. 8), så kjøringen aksepten
    # hviler på skal være den historikken faktisk fikk.
    if (data.get("event") or "") != CI_HENDELSE:
        feil.append(f"kjøringen ble trigget av {data.get('event') or '?'},"
                    f" ikke {CI_HENDELSE} — en {data.get('event') or '?'}"
                    "-kjøring testet et forslag, ikke historikken")
    if (data.get("head_branch") or "") != HOVEDGREN:
        feil.append(f"kjøringen sto på {data.get('head_branch') or '?'}, ikke"
                    f" {HOVEDGREN} — akseptcommiten skal være merget")
    if data.get("status") != "completed":
        feil.append(f"status={data.get('status')!r} — kjøringen er ikke ferdig")
    if data.get("conclusion") != "success":
        feil.append(f"conclusion={data.get('conclusion')!r} — bare en grønn"
                    " kjøring bærer punktene")
    if (data.get("head_sha") or "") != ci_commit:
        feil.append(f"kjøringen testet {(data.get('head_sha') or '?')[:12]}…,"
                    f" ikke akseptcommiten {ci_commit[:12]}…")
    return feil


def verifiser_ci_kjoring(run_id: str, ci_commit: str) -> dict:
    """CI-kjøringen bak de 16 invariantpunktene. -> kjøringen.

    Codex' P1 på PR #117 (runde 2): punktene ble hardkodet grønne fra to
    strenger kalleren skrev. En skrivefeil, en gammel kjøring eller en RØD
    kjøring ga like fullt 16 immutable «grønne» punkter — en kjøring
    ingen har sett bevise noe som helst. Nå slås kjøringen opp: den må
    finnes i dette repoet, være `ci.yml` (runde 3: en grønn
    `claude.yml`-kjøring på samme commit kjører ingen av
    invarianttestene), være en PUSH PÅ `main` (runde 11: `ci.yml` kjører
    også for `pull_request`, og en kjøring på et forslag beviser ikke at
    bytene ble historikk), være FERDIG og GRØNN, og ha testet nøyaktig
    akseptcommiten (klarsignalets §2.5: «run-ID + commit-sha på
    akseptcommiten»).
    """
    if not re.fullmatch(r"[0-9]{1,20}", run_id):
        raise SystemExit(f"AVBRUTT: --ci-run {run_id!r} er ingen"
                         " workflow-run-id")
    data = _hent_ci_kjoring(run_id)
    feil = _vurder_ci_kjoring(data, run_id, ci_commit)
    if feil:
        raise SystemExit(f"AVBRUTT: CI-kjøring {run_id} bærer ikke"
                         " invariantpunktene:\n  " + "\n  ".join(feil))
    return data


def repo_rel(sti: Path, hva: str = "artefaktet") -> str:
    """Repo-relativ, POSIX-normalisert sti — eller avbrudd."""
    try:
        return sti.resolve().relative_to(REPO).as_posix()
    except ValueError:
        raise SystemExit(f"AVBRUTT: {hva} {sti} peker utenfor repoet — bare"
                         " innsjekkede, manifestbundne artefakter aksepterer"
                         " noe")


def les_bundet_artefakt(sti: Path, krav_id: str,
                        manifest: dict) -> tuple[dict, str]:
    """Artefaktet gjennom HELE evidensporten. -> (innhold, sha256).

    Codex' P1 på PR #117: den forrige formen leste `bestatt` — et felt
    KALLEREN kontrollerer — og skrev deretter en immutabel, grønn
    drill- og akseptrad. En håndskrevet JSON-fil med `bestatt: true` og
    passende tellere nådde altså de priviligerte funksjonene, og hele
    evidensgrensen skriptet finnes for var omgått av en tekstredigerer.

    Fire lag, alle FØR transaksjonen åpnes:

    1. stien må ligge i repoet og være NØYAKTIG den fila manifestet
       binder for `krav_id` — en lokalt endret eller fremmed fil er
       ikke evidens uansett hva den inneholder;
    2. sha256 av de leste bytene må være manifestets (`_les_artefakt`
       hasher og tolker ETT lesesteg, så det er samme bytes begge veier);
    3. det lukkede JSON-skjemaet (`additionalProperties: false`);
    4. `_sjekk_grenser` — som REGNER UT invariantene på nytt i stedet
       for å tro på `bestatt`, og som håndhever §12-grensene.
    """
    import manifestskjema as ms
    rel = repo_rel(sti)
    bundet = {p["artefakt_sha256"] for p in
              (manifest.get("staging_sjekkliste") or {}).values()
              if isinstance(p, dict) and p.get("status") == "ja"
              and p.get("krav_id") == krav_id and p.get("artefakt") == rel
              and p.get("artefakt_sha256")}
    if not bundet:
        raise SystemExit(f"AVBRUTT: {rel} er ikke artefaktet manifestet"
                         f" binder for {krav_id}")
    data, sha, melding = ms._les_artefakt(sti)
    if melding:
        raise SystemExit(f"AVBRUTT: {rel}: {melding}")
    if sha not in bundet:
        raise SystemExit(f"AVBRUTT: {rel} er endret — manifestet binder"
                         f" {sorted(bundet)[0][:12]}…, filen er {sha[:12]}…")
    feil = (ms.valider_artefaktformat(data, krav_id)
            + ms._sjekk_grenser(krav_id, data))
    if feil:
        raise SystemExit(f"AVBRUTT: {rel} består ikke evidensporten:\n  "
                         + "\n  ".join(feil))
    return data, sha


def drillens_maaletid(drill: dict) -> "datetime":
    """Da drillen FAKTISK kjørte — artefaktets egen `ts`. -> datetime.

    Codex' P2 på PR #117 (runde 3): `moduldrill.utfort_ts` sto med
    `DEFAULT now()` og fikk aldri en verdi, så en drill kjørt timer eller
    dager før aksepten ble skrevet inn som om den kjørte i
    akseptøyeblikket. Måletiden er drillartefaktets, registreringstiden
    er basens — to fakta, to kolonner. Her leses det første, med
    tidssone: en naiv tidsstempelstreng er ikke et øyeblikk.
    """
    raa = drill.get("ts")
    try:
        ts = datetime.fromisoformat(str(raa))
    except (TypeError, ValueError):
        raise SystemExit(f"AVBRUTT: drillartefaktets ts {raa!r} er ingen"
                         " ISO-8601-tid — måletiden kan ikke skrives")
    if ts.tzinfo is None:
        raise SystemExit(f"AVBRUTT: drillartefaktets ts {raa!r} mangler"
                         " tidssone — et øyeblikk uten sone er ikke et"
                         " øyeblikk")
    if ts > datetime.now(timezone.utc):
        raise SystemExit(f"AVBRUTT: drillartefaktets ts {raa!r} ligger fram"
                         " i tid — det er en påstand om framtiden, ikke en"
                         " måling")
    return ts


def verifiser_modul(art: dict, hva: str) -> None:
    """Artefaktet må navngi MODULEN som aksepteres, ikke en annen.

    Codex' P2 på PR #117 (runde 3): runde-sammendraget kalte modulen
    `m56_wcag_audit` — KATALOGNAVNET — mens registeret, drillen, 049-radene
    og dette skriptet bruker `m_wcag_audit`. Skjemaet krevde bare en
    ikke-tom streng, og skriptet sammenlignet aldri feltet med `MODUL`, så
    et sammendrag som navnga en helt annen modul ble evidens for en
    immutabel `m_wcag_audit`-aksept. Feltet er nå bundet i skjemaet OG målt
    her: to lag, samme sannhet.
    """
    navn = (art.get("oppsett") or {}).get("modul")
    if navn != MODUL:
        raise SystemExit(f"AVBRUTT: {hva} gjelder modul {navn!r}, ikke"
                         f" {MODUL!r} — evidens for en annen modul aksepterer"
                         " ingenting her")


def verifiser_miljo(drill: dict) -> None:
    """Drillen må være kjørt i MILJØET som aksepteres, ikke et annet.

    Codex' P1 på PR #117 (runde 4): `verifiser_modul` bandt hvilken MODUL
    evidensen gjelder, men ingenting bandt hvilket MILJØ. Skjemaet godtar
    en hvilken som helst ikke-tom `oppsett.miljo`, og begge basekallene
    nedenfor skriver `MILJO` ubetinget. Release-id-er og digester er
    globale, så er de samme releasene også utrullet i staging, passerer
    både live-tilstands- og digestkontrollen — og et drillutfall målt i
    produksjon kunne kopieres inn i en immutabel STAGING-aksept. Designet
    krever én drill per miljø nettopp fordi vert, base og naboer er
    forskjellige; da må artefaktet si hvilket det målte, og det må være
    dette.

    Bindingen hører hjemme her og ikke i skjemaet: `rollback-m56-v1` er
    drillformen, ikke staging-formen — den samme drillen skal kunne
    kjøres i produksjon senere. Det er DETTE skriptet som velger miljø,
    og derfor her miljøet må måles.
    """
    navn = (drill.get("oppsett") or {}).get("miljo")
    if navn != MILJO:
        raise SystemExit(f"AVBRUTT: drillartefaktet ble målt i miljø"
                         f" {navn!r}, ikke {MILJO!r} — en drill i ett miljø"
                         " er ikke bevis for et annet")


def verifiser_kilde(runde: dict) -> str:
    """Råfilen bak sammendraget. -> sha256 av de faktiske bytene.

    Sammendraget er avledet av `evidens.jsonl`; binder det en råfil som
    ikke lenger er den, er `kilde_sha256` i akseptraden en peker til noe
    som ikke finnes. Kjeden manifest→sammendrag→råfil skal være sha-bundet
    ledd for ledd (SP-11), og siste ledd måles her.
    """
    kilde = (runde.get("oppsett") or {}).get("kilde")
    forventet = (runde.get("oppsett") or {}).get("kilde_sha256")
    if not kilde or not forventet:
        raise SystemExit("AVBRUTT: sammendraget mangler kilde/kilde_sha256")
    sti = (REPO / kilde).resolve()
    try:
        sti.relative_to(REPO)
    except ValueError:
        raise SystemExit(f"AVBRUTT: kilden {kilde} peker utenfor repoet")
    try:
        sha = hashlib.sha256(sti.read_bytes()).hexdigest()
    except OSError as e:
        raise SystemExit(f"AVBRUTT: kilden {kilde} kan ikke leses"
                         f" ({type(e).__name__})")
    if sha != forventet:
        raise SystemExit(f"AVBRUTT: {kilde} er ikke råfilen sammendraget"
                         f" binder — {forventet[:12]}… mot {sha[:12]}…")
    return sha


def verifiser_e2e_artefakt(drill: dict, oppgitt: str) -> str:
    """E2E-beviset må være ett av dem DRILLEN faktisk så. -> uuid-en.

    Codex' P2 på PR #117 (runde 3): `--e2e-artefakt` gikk rett inn i den
    immutable akseptraden, og FK-en i 049 sjekker bare tenant, modul,
    release og promotert tilstand. Et hvilket som helst ANNET promotert
    artefakt fra kandidatreleasen passerte derfor like godt — en
    skrivefeil bandt aksepten til et artefakt drillen aldri observerte,
    mens drillartefaktet bare bar et antall og ingen identitet. Nå bærer
    drillen identitetene, og aksepten må peke på en av dem.
    """
    sett = [str(u) for u in
            ((drill.get("identiteter") or {}).get("kandidat_artefakter")
             or [])]
    if not sett:
        raise SystemExit("AVBRUTT: drillartefaktet bærer ingen"
                         " kandidat-artefakter — uten identitetene kan"
                         " E2E-beviset ikke bindes til drillen")
    if oppgitt.strip().lower() not in {u.strip().lower() for u in sett}:
        raise SystemExit(
            f"AVBRUTT: --e2e-artefakt {oppgitt} er ikke blant artefaktene"
            f" drillens kandidat promoterte ({', '.join(sett)}) — aksepten"
            " skal binde beviset drillen SÅ, ikke et annet promotert"
            " artefakt fra samme release")
    return oppgitt.strip()


def drillens_oppdrag(drill: dict) -> tuple[int, int, int]:
    """De tre oppdragene drillen kjørte. -> (inflight, rullback, kandidat).

    Codex' P1 på PR #117 (runde 5): drillutfallene ble regnet ut i dette
    skriptet og sendt til `registrer_moduldrill` som tre boolske verdier,
    så evidensapparatet lå HELT i skriptet. Basen måler dem nå selv, og
    må da få vite hva den skal måle på. `oppdrag.id` er BIGINT; artefaktet
    bærer id-ene som strenger, og en streng som ikke er et heltall er
    ingen oppdragsidentitet — det skal høres her, ikke som en
    typefeil i psycopg.
    """
    ident = drill.get("identiteter") or {}
    ut = []
    for felt in ("inflight_oppdrag_id", "rullback_oppdrag_id",
                 "kandidat_oppdrag_id"):
        raa = ident.get(felt)
        try:
            ut.append(int(str(raa)))
        except (TypeError, ValueError):
            raise SystemExit(f"AVBRUTT: drillartefaktets {felt} er {raa!r},"
                             " ikke en oppdrags-id — utfallene måles på"
                             " oppdragene, og da må de kunne slås opp")
    return ut[0], ut[1], ut[2]


def drillens_epoch(drill: dict) -> int:
    """Fencing-generasjonen drillen ble målt i. -> module_epoch.

    Codex' P2 på PR #117 (runde 5): `moduldrill.epoch_snapshot` ble
    snapshottet av basen ved REGISTRERINGEN, og drillartefaktets egen
    `oppsett.module_epoch` ble aldri sendt inn eller sammenlignet. En
    nødstopp eller reaktivering mellom drill og aksept flytter
    generasjonen, og den immutable raden kunne da påstå en annen
    fencing-kontekst enn artefaktet som målte drillen — den SKJULTE et
    misdannet bevis i stedet for å avvise det. Feltet er et heltall i
    skjemaet; her leses det slik at en verdi som ikke er det, høres.
    """
    raa = (drill.get("oppsett") or {}).get("module_epoch")
    if not isinstance(raa, int) or isinstance(raa, bool) or raa < 0:
        raise SystemExit(f"AVBRUTT: drillartefaktets module_epoch er"
                         f" {raa!r} — en fencing-generasjon er et"
                         " ikke-negativt heltall, og uten den vet ingen"
                         " hvilken kontekst drillen ble målt i")
    return raa


def _digest(raa: object) -> str:
    """Digesten uten `sha256:`-prefiks — samme form uansett kilde."""
    return str(raa or "").removeprefix("sha256:").strip().lower()


def verifiser_digestkjede(runde: dict, drill: dict) -> str:
    """Bytene rundens tall gjelder, MÅ være bytene som aksepteres.

    Codex' P1 på PR #117 (runde 3): de to artefaktene ble validert hver
    for seg, og `registrer_moduldrill` sammenlignet bare de to
    DATABASE-releasene med hverandre. Ingenting bandt
    `runde['oppsett']['image_digest']` — imaget WCAG-runden faktisk
    målte — til drillens digest. Deler drillet og kandidat en digest
    som er en ANNEN enn den historiske rundens, passerer alt: en deploy
    med helt andre bytes får immutabel aksept på en gammel måling.
    A1 sier aksepterte bytes er drillede bytes; her legges det siste
    leddet til: målte bytes.
    """
    o = drill.get("oppsett") or {}
    runde_digest = _digest((runde.get("oppsett") or {}).get("image_digest"))
    drillet, kandidat = _digest(o.get("drillet_digest")), \
        _digest(o.get("kandidat_digest"))
    if not runde_digest or not drillet or not kandidat:
        raise SystemExit("AVBRUTT: en av digestene mangler — kjeden"
                         " målt→drillet→akseptert kan ikke lukkes")
    for navn, digest in (("drillet", drillet), ("kandidat", kandidat)):
        if digest != runde_digest:
            raise SystemExit(
                f"AVBRUTT: WCAG-runden målte image {runde_digest[:12]}…,"
                f" men {navn} release bærer {digest[:12]}… — tallene"
                " gjelder andre bytes enn de som aksepteres")
    return runde_digest


def verifiser_registrert_digest(conn, releaser: tuple[str, ...],
                                digest: str) -> None:
    """…og BASEN må bære den samme digesten for de samme releasene.

    Artefaktene er innsjekkede bytes; registeret er den levende
    sannheten om hva som kjører. Uten dette leddet kunne to artefakter
    være enige med hverandre om et image ingen av deploymentradene har.
    """
    for release in releaser:
        rad = conn.execute(
            "SELECT artifact_digest FROM modulrelease WHERE modul_id=%s"
            " AND release_id=%s", (MODUL, release)).fetchone()
        if rad is None:
            raise SystemExit(f"AVBRUTT: release {release} finnes ikke i"
                             " registeret")
        if _digest(rad[0]) != digest:
            raise SystemExit(
                f"AVBRUTT: registeret gir {release} digest"
                f" {_digest(rad[0])[:12]}…, mens evidensen gjelder"
                f" {digest[:12]}… — aksepten ville bundet andre bytes enn"
                " de målte")


def verifiser_claim_spor(conn, o: dict, inflight: int, rullback: int,
                         kandidat: int) -> None:
    """Hvert drilledd må være CLAIMET av releasen leddet handler om.

    Codex' P1 på PR #117 (runde 20): drillens (b)-ledd påstår at den
    drillede releasen hadde et oppdrag inne da rullingen traff, men bare
    `utfort` bar en binding (det promoterte artefaktet). Et `feilet`
    utfall hvilte på FRAVÆR av artefakt på den drillede — sant for alt
    arbeid i verden. `claim_neste_oppdrag` stempler nå
    `claim_release_id`/`claim_miljo` (049 §0), og `registrer_moduldrill`
    måler de tre leddene mot det sporet.

    Sporet finnes bare for claim gjort ETTER 049. Uten denne porten
    ville et pre-049-claim gitt en rød drillrad, og aksepten ville falt
    på FK-en mot et grønt utfall — en melding som ikke sier at drillen må
    kjøres på nytt. Her sies det.
    """
    conn.execute("SELECT set_config('disponit.tenant', %s, true)", (TENANT,))
    for ledd, oid, release in (
            ("inflight", inflight, o["drillet_release"]),
            ("rullbakk", rullback, o["rullback_release"]),
            ("kandidat", kandidat, o["kandidat_release"])):
        rad = conn.execute(
            "SELECT claim_release_id, claim_miljo FROM oppdrag"
            " WHERE tenant=%s AND id=%s", (TENANT, oid)).fetchone()
        if rad is None:
            raise SystemExit(f"AVBRUTT: {ledd}-oppdraget {oid} finnes ikke"
                             f" for tenant {TENANT}")
        if tuple(rad) != (release, MILJO):
            raise SystemExit(
                f"AVBRUTT: {ledd}-oppdraget {oid} bærer claim-sporet"
                f" {tuple(rad)}, ikke ({release}, {MILJO}) — drillraden"
                " ville blitt rød på dette leddet. Er sporet tomt, ble"
                " oppdraget claimet før migrasjon 049 la til kolonnen, og"
                " drillen må kjøres på nytt etter 049")
    # …og claim-stoppet er en VARIGHET (Codex P1, #117 runde 21).
    # `registrer_moduldrill` krever at rullbakkoppdraget lå ubehandlet i
    # minst `moduldrill_min_ventetid_s()` fra det ble bestilt til det
    # FØRSTE claimet. Samme tall leses her, fra samme funksjon, så
    # aksepten stopper med en setning om målingen i stedet for på FK-en
    # mot et grønt utfall.
    rad = conn.execute(
        "SELECT EXTRACT(EPOCH FROM (forste_claim_ts - opprettet))::float8,"
        " moduldrill_min_ventetid_s()::float8 FROM oppdrag"
        " WHERE tenant=%s AND id=%s", (TENANT, rullback)).fetchone()
    if rad[0] is None:
        raise SystemExit(
            f"AVBRUTT: rullbakkoppdraget {rullback} står uten"
            " forste_claim_ts — stempelet kom med 049, så oppdraget ble"
            " claimet før migrasjonen. Drillen må kjøres på nytt etter 049")
    if rad[0] < rad[1]:
        raise SystemExit(
            f"AVBRUTT: claim-stoppet ble målt i {rad[0]:.3f} s, og porten"
            f" krever minst {rad[1]:g} s. Den drenerte releasen lot ikke"
            " oppdraget ligge lenge nok til at «den claimet ingenting» er"
            " målt — drillraden ville blitt rød på claim_stopp_ok")


def _manifestgenerasjoner(commit: str) -> dict[str, str]:
    """sha256 → commit for HVER innsjekkede generasjon av manifestet.

    Bare commiter som faktisk endret fila, og bare de som er nåbare fra
    `commit`: en generasjon som lever på en annen gren er ikke en
    generasjon akseptcommiten står for.
    """
    r = _git("rev-list", commit, "--", MANIFEST_REL)
    if r.returncode != 0:
        raise SystemExit(f"AVBRUTT: finner ingen historikk for"
                         f" {MANIFEST_REL} i {commit[:12]}…")
    # Dobbeltnøklet (A-vedtaket): eldre releaserader bærer BYTE-hashen
    # fra registreringen; nye bærer projeksjonen. Byte-nøkkelen legges
    # først, så en (usannsynlig) kollisjon vinnes av den eksakte formen.
    import manifestskjema as ms
    gen: dict[str, str] = {}
    for c in r.stdout.decode().split():
        b = _git("cat-file", "blob", f"{c}:{MANIFEST_REL}")
        if b.returncode == 0:
            gen.setdefault(hashlib.sha256(b.stdout).hexdigest(), c)
            gen.setdefault(
                ms.kanonisk_projeksjon(b.stdout.decode("utf-8")), c)
    return gen


def verifiser_registrert_manifest(conn, releaser: tuple[str, ...],
                                  manifest: dict, manifest_sha: str,
                                  manifest_commit: str) -> str:
    """…og MANIFESTET raden ble registrert med, må være dette manifestet.

    Codex' P1 på PR #117 (runde 7): digestkjeden ble bundet hele veien —
    målt image = drillet image = registrert image — men manifestet gikk
    fri. `modulrelease.manifest_hash` er sha256 av `manifest.yaml` slik
    den så ut da releasen ble REGISTRERT (drillens fase 2), og
    akseptraden peker på `manifest_commit`. Ingenting bandt de to. Og
    de divergerer med nødvendighet: drillartefaktet bindes INN i
    manifestet etter drillen, så akseptcommitens manifest er per
    konstruksjon en annen generasjon enn den kandidatreleasen bærer.
    Aksepten kunne dermed peke på en pen commit mens den aksepterte
    deploymenten kjører en HELT annen modulkonfigurasjon — annen
    `status`, `driftstilstand`, `kjerne`, andre avhengigheter — eller en
    som aldri ble sjekket inn i det hele tatt. Like image-bytes
    reparerer ikke det: manifestet er modulens identitet i registeret
    (014 §port 1), imaget er bare bytene som kjører.

    Det som måles her, er derfor proveniensen selv:

      1. drillet og kandidat må bære SAMME `manifest_hash` — én drill er
         én måling, i én manifestgenerasjon,
      2. den generasjonen må FINNES som innsjekkede bytes i
         akseptcommitens egen historikk (ellers er den registrert fra et
         manifest ingen kan lese), og
      3. den må være identisk med akseptcommitens manifest utenfor
         `staging_sjekkliste` og KATALOGAKSENE — evidensbindingen er
         endringen flyten selv krever mellom drill og aksept, og
         `status`/`driftstilstand` er katalogens AVLESNING av aksepten
         (A-vedtaket på #152), ikke del av den drillede identiteten.
         Alt strukturelt (`kjerne`, `avhengigheter`, `id`, …) ER
         identiteten, og en aksept som påstår en annen enn den
         drillede, må stoppe her.

    -> commiten den registrerte generasjonen ble sjekket inn i.
    """
    import yaml
    hasher = {}
    for release in releaser:
        rad = conn.execute(
            "SELECT manifest_hash FROM modulrelease WHERE modul_id=%s"
            " AND release_id=%s", (MODUL, release)).fetchone()
        if rad is None:
            raise SystemExit(f"AVBRUTT: release {release} finnes ikke i"
                             " registeret")
        hasher[release] = str(rad[0] or "").strip().lower()
    if len(set(hasher.values())) != 1:
        raise SystemExit(
            "AVBRUTT: drillet og kandidat er registrert fra ULIKE"
            " manifestgenerasjoner ("
            + ", ".join(f"{r}={h[:12]}…" for r, h in sorted(hasher.items()))
            + ") — en drill er én måling, og da kan ikke halvparten av"
              " den ha kjørt en annen modulkonfigurasjon")
    registrert = next(iter(hasher.values()))
    if registrert == manifest_sha:
        return manifest_commit
    generasjoner = _manifestgenerasjoner(manifest_commit)
    kilde = generasjoner.get(registrert)
    if kilde is None:
        grunn = ""
        if _git("rev-parse", "--is-shallow-repository"
                ).stdout.decode().strip() == "true":
            grunn = (" Utsjekkingen er GRUNN, så historikken her er ikke"
                     " modulens historikk — kjør aksepten fra et fullt"
                     " klon.")
        raise SystemExit(
            f"AVBRUTT: releasene er registrert med manifest_hash"
            f" {registrert[:12]}…, som ikke er NOEN innsjekket generasjon"
            f" av {MANIFEST_REL} i historikken til"
            f" {manifest_commit[:12]}… — aksepten ville bundet en"
            f" deployment som kjører et manifest ingen kan lese.{grunn}")
    b = _git("cat-file", "blob", f"{kilde}:{MANIFEST_REL}")
    drillet_manifest = yaml.safe_load(b.stdout.decode("utf-8")) or {}
    import manifestskjema as ms
    tillatt = {"staging_sjekkliste", *ms.KATALOGAKSER}
    avvik = sorted(
        felt for felt in set(manifest) | set(drillet_manifest)
        if felt not in tillatt
        and manifest.get(felt) != drillet_manifest.get(felt))
    if avvik:
        raise SystemExit(
            f"AVBRUTT: manifestet releasene ble registrert fra"
            f" ({registrert[:12]}…, commit {kilde[:12]}…) er ikke"
            f" akseptcommitens manifest utenfor evidensbindingen —"
            f" {', '.join(avvik)} er endret. Den aksepterte deploymenten"
            " kjører den drillede generasjonen; en endring der er en NY"
            " release, som må registreres og drilles for seg.")
    return kilde


#: §5-punktene som måles PÅ TVERS av de to artefaktene — klarsignalets
#: §2 (sammenhengskravet) gjort mekanisk. Verdiene regnes her og
#: attesteres på runde-evidensfilen: det er DENS runde de binder.
SAMMENHENG_PUNKTER = (
    "datasett.sha_ulik_mellom_ledd",
    "evidens.pa_tvers_av_runder",
    "aksept.gjenbrukt_gammel_evidens",
)
SAMMENHENG_GRENSER = {
    "datasett.sha_ulik_mellom_ledd": "0 (byte-likhet begge ledd, SP-11)",
    "evidens.pa_tvers_av_runder":
        "0 (én runde, én drill, én manifestgenerasjon)",
    "aksept.gjenbrukt_gammel_evidens": "0 (ny runde, ferske release-id-er)",
}


def sammenheng_verdier(runde: dict, drill: dict, ms) -> dict[str, str]:
    """Sammenhengskravet (§2) målt — grønt er «0», alt annet er teksten.

    * `datasett.sha_ulik_mellom_ledd`: runde-artefaktets
      `oppsett.datasett_sha256` (staging-leddet) mot de innsjekkede
      bytene hashet med SAMME funksjon (`manifestskjema.datasett_sha256`)
      — byte-likhet i begge ledd, én byte ulik er rødt (§1.2).
      `_sjekk_grenser` har alt målt dette i evidensporten; her regnes
      det ut på nytt fordi punktet skrives i en immutabel akseptrad.
    * `evidens.pa_tvers_av_runder`: runden målte NØYAKTIG den releasen
      drillen deretter drillet — `oppsett.release` == drillens
      `drillet_release`. Sammen med `verifiser_registrert_manifest`
      (samme manifest_hash på begge drill-releasene) og
      `verifiser_digestkjede` (samme image-bytes) er det den mekaniske
      formen av «ett evidenssett beskriver samme kjøring av samme
      kandidat». Fristelsen til å spleise den grønne 19/8-runden med en
      ny drill dør her, ikke i en huskeregel.
    * `aksept.gjenbrukt_gammel_evidens`: runde-evidensen bærer
      v2-artefaktkravets målinger (attestene og datasett-identiteten
      fantes ikke før 052, så en gammel runde KAN ikke bære dem —
      `les_bundet_artefakt` har alt avvist alt annet), og
      drill-releasene er ubrukte ved drilling
      (`krev_ubrukte_drillreleaser`) og konsumeres av den (livsløpet er
      enveis, 014). Måles her som kravsjekken på artefaktet; basens
      enveis livsløp er porten som gjør gjenbruk umulig, ikke bare
      forbudt.
    """
    verdier = {}
    sha = (runde.get("oppsett") or {}).get("datasett_sha256")
    lokal = ms.datasett_sha256(ms.DATASETT_STI)
    verdier["datasett.sha_ulik_mellom_ledd"] = (
        "0" if sha == lokal
        else f"ulik ({str(sha)[:12]}… mot innsjekket {lokal[:12]}…)")
    r_rel = (runde.get("oppsett") or {}).get("release")
    d_rel = (drill.get("oppsett") or {}).get("drillet_release")
    verdier["evidens.pa_tvers_av_runder"] = (
        "0" if r_rel and r_rel == d_rel
        else f"runden målte {r_rel!r}, drillen drillet {d_rel!r}")
    verdier["aksept.gjenbrukt_gammel_evidens"] = (
        "0" if runde.get("krav_id") == ARTEFAKT_KRAV
        else f"runde-evidensen er {runde.get('krav_id')!r}, ikke"
             f" {ARTEFAKT_KRAV!r}")
    return verdier


def verifiser_kjoringsattester(conn, runde: dict) -> None:
    """#124: basens NÅ-svar for kontrollkjøringene — før raden skrives.

    Konverterens tellere er avledet av en produsent-skrevet evidensfil,
    og seks review-runder på tvers av #117/#123 fant hver en ny form for
    «hva om fila lyver». Fila kan ikke spørres hardere — men BASEN kan:
    v2-artefaktet navngir nå de ti kjøringene (`identiteter.kjoringer`,
    fra samme utvalg som tellerne), og her re-kjøres
    `maal_kjoringsattest` for hver av dem, mot nøyaktig den releasen
    runden målte. Samme form som drillen: `registrer_moduldrill` tror
    aldri på artefaktets utfall, den måler selv i `oppdrag`/`artefakt`.

    Kravene er akseptgrensens egne (§1.3): hvert oppdrag bærer
    kvitteringsavtrykket, claim-sporet (release+miljø), det utførte
    utfallet med promotert artefakt, modul-eierskapet med registrert
    oppdragstype, og revisjonsraden — og loggpostene er DISTINKTE.

    SKRANKEN BOR I BASEN (053, Codex P1 på #125): `aksepter_
    moduldeployment` gjør nøyaktig denne målingen selv, mot drillradens
    `drillet_release`, bundet til identitetene verifikatoren attesterte.
    Dette kallet er fail-fast-SPEILET av den porten — det stopper en død
    kjøring FØR fullmakter tas og referater skrives, men det er basens
    egen måling akseptraden hviler på, ikke denne.

    REGELSETTET måles ikke her, og trenger ikke måles her: liv-leddet er
    transitivt. Claim-sporet binder kjøringen til releasen, releasen til
    den registrerte digesten (liv-målt av `verifiser_registrert_digest`),
    og motoren i det imaget NEKTER å kjøre med axe-bytes ≠ pinnet sha256
    (`motor_axe/kjor.py`, exit ≠ 0) — enhver fullført kjøring på den
    digesten kjørte det pinnede regelsettet. Filtelleren
    (`kjoringer_med_attestert_kvittering`) vokter transkripsjonen.
    """
    m = runde["maalt"]
    krav = m["kjoringer_krav"]
    ident = (runde.get("identiteter") or {}).get("kjoringer")
    if not (isinstance(ident, list) and len(ident) == krav
            and all(isinstance(o, int) and not isinstance(o, bool)
                    and o > 0 for o in ident)
            and len(set(ident)) == krav):
        raise SystemExit(
            f"AVBRUTT: runde-artefaktet navngir ikke {krav} distinkte"
            " kjøringer (identiteter.kjoringer) — akseptporten kan ikke"
            " re-måle kjøringer den ikke får navngitt")
    rel = runde["oppsett"]["release"]
    loggposter = set()
    conn.execute("SET ROLE disponit_modules_admin")
    try:
        for o in ident:
            rad = conn.execute(
                "SELECT kvittering_ok, claim_release_ok, artefakt_ok,"
                " revisjonsrad_ok, modul_ok, loggpost"
                " FROM maal_kjoringsattest(%s,%s,%s,%s,%s,%s)",
                (TENANT, o, rel, MILJO, MODUL,
                 OPPDRAGSTYPE)).fetchone()
            if not (rad and rad[0] and rad[1] and rad[2] and rad[3]
                    and rad[4] and rad[5] is not None):
                deler = dict(zip(("kvittering", "claim_release",
                                  "artefakt", "revisjonsrad", "modul"),
                                 rad[:5] if rad else (False,) * 5))
                raise SystemExit(
                    f"AVBRUTT: kjøring {o}: basen attesterer den ikke NÅ"
                    f" mot ({rel}, {MILJO}) — {deler}. Artefaktets tall"
                    " er transkripsjonen; akseptraden hviler på basens"
                    " eget svar, og det svaret er nei")
            loggposter.add(rad[5])
    finally:
        conn.execute("RESET ROLE")
    if len(loggposter) != krav:
        raise SystemExit(
            f"AVBRUTT: de {krav} kjøringene bærer {len(loggposter)}"
            " distinkte revisjonsrader — én rad per kjøring er kravet,"
            " og en rad delt av flere kjøringer er én rad")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--drill", type=Path, required=True)
    ap.add_argument("--runde", type=Path, required=True)
    ap.add_argument("--e2e-artefakt", required=True)
    ap.add_argument("--ci-run", required=True)
    ap.add_argument("--ci-commit", required=True)
    ap.add_argument("--manifest-commit")
    a = ap.parse_args()

    import manifestskjema as ms

    # FØRST av alt: har hvert grensepunkt en kilde som faktisk måler det?
    # Et punkt uten måling stopper aksepten her, ikke etter at halve
    # kjeden er verifisert (Codex P1, runde 5).
    krev_maalbare_punkter()

    # Ingenting skrives før hele evidenskjeden er målt: manifestets egen
    # port (hash → skjema → grenser → hvilken måling punktet påberoper
    # seg), så de to artefaktene kalleren faktisk sendte inn, så råfilen.
    manifest_commit = loes_akseptcommit(a.manifest_commit)
    manifest, manifest_sha = les_manifest()
    kjedefeil = ms.valider_artefakter(manifest)
    if kjedefeil:
        raise SystemExit("AVBRUTT: manifestets evidenskjede er rød:\n  "
                         + "\n  ".join(kjedefeil))
    drill, drill_sha = les_bundet_artefakt(a.drill, DRILLKRAV, manifest)
    runde, runde_sha = les_bundet_artefakt(a.runde, ARTEFAKT_KRAV, manifest)
    verifiser_modul(drill, "drillartefaktet")
    verifiser_modul(runde, "runde-sammendraget")
    verifiser_miljo(drill)
    drill_ts = drillens_maaletid(drill)
    # Målte bytes = drillede bytes = aksepterte bytes (A1, siste ledd).
    digest = verifiser_digestkjede(runde, drill)
    # …og E2E-beviset er ett av dem drillen selv observerte.
    e2e_artefakt = verifiser_e2e_artefakt(drill, a.e2e_artefakt)
    # Oppdragene basen skal måle drillutfallene på (Codex P1, runde 5).
    inflight_opp, rullback_opp, kandidat_opp = drillens_oppdrag(drill)
    evidens_sha = verifiser_kilde(runde)
    # …og hele den kjeden bindes til commiten raden faktisk skriver:
    # manifestet (tillitsroten), begge artefaktene og råfilen bakerst.
    for rel, sha in ((MANIFEST_REL, manifest_sha),
                     (repo_rel(a.drill), drill_sha),
                     (repo_rel(a.runde), runde_sha),
                     (runde["oppsett"]["kilde"], evidens_sha)):
        bind_til_commit(manifest_commit, rel, sha)
    # Invariantpunktene har ingen historiske rader: de hviler HELT på at
    # CI-kjøringen finnes, er grønn og testet akseptcommiten.
    ci_commit = loes_akseptcommit(a.ci_commit)
    if ci_commit != manifest_commit:
        raise SystemExit(f"AVBRUTT: --ci-commit {ci_commit[:12]}… er ikke"
                         f" akseptcommiten {manifest_commit[:12]}… — punktene"
                         " påberoper seg «grønn CI på akseptcommiten», og da"
                         " må det være den kjøringen som måles")
    ci_kjoring = verifiser_ci_kjoring(a.ci_run, ci_commit)

    m = runde["maalt"]
    krev_maalt_signatur(m)
    punkter = {}
    # HELE hashen, ikke et prefiks (Codex P1, #117 runde 15): basen måler
    # nå at et `evidensfil`-punkt peker på nøyaktig den filen akseptraden
    # bærer hashen av, og en avkortet referanse er ikke den hashen.
    runde_ref = f"{runde['oppsett']['kilde']}@sha256:{evidens_sha}"
    # Måletallene FILEN bar, som de leses her — de samme som attesteres
    # under (Codex P1, runde 19). Basen regner punktet mot attesten, ikke
    # mot dette kallet; at de er like er poenget, ikke tilfeldig.
    evidens_maalt = {punkt: hent(m) for punkt, (_, hent) in MAALTE.items()}
    # …og sammenhengspunktene (§2/§5) måles på tvers av de to
    # artefaktene og attesteres på runde-evidensfilen — det er DENS
    # runde de binder.
    evidens_maalt.update(sammenheng_verdier(runde, drill, ms))
    for punkt, (grense, _) in MAALTE.items():
        punkter[punkt] = {"grenseverdi": grense,
                          "maalt_verdi": evidens_maalt[punkt],
                          "kilde_type": "evidensfil",
                          "kilde_ref": runde_ref}
    for punkt in SAMMENHENG_PUNKTER:
        punkter[punkt] = {"grenseverdi": SAMMENHENG_GRENSER[punkt],
                          "maalt_verdi": evidens_maalt[punkt],
                          "kilde_type": "evidensfil",
                          "kilde_ref": runde_ref}
    for punkt in CI_PUNKTER:
        punkter[punkt] = {"grenseverdi": "0 (porttest rød ved brudd)",
                          "maalt_verdi": "0 (grønn CI på akseptcommiten)",
                          "kilde_type": "ci_kjoring",
                          "kilde_ref": f"run {a.ci_run} @ {ci_commit}"}
    punkter["malautorisasjon.positiv_sti_virker"] = {
        "grenseverdi": "ja", "maalt_verdi": "ja",
        "kilde_type": "ci_kjoring",
        "kilde_ref": f"run {a.ci_run} @ {ci_commit}"}

    from db.pg import koble
    # Attestveiens innlogging leses FØR noe skrives: mangler den, skal
    # kjøringen dø her og ikke midt mellom to fullmakter.
    if not os.environ.get("DISPONIT_VERIFIKATOR_URL"):
        raise SystemExit(
            "AVBRUTT: DISPONIT_VERIFIKATOR_URL mangler — attesten og"
            " aksepten skal skrives av to autentiserte identiteter, og"
            " basen avviser en attest som bærer akseptørens egen"
            " innlogging. Kjør deploy/staging/oppsett-postgresql.sh")
    conn = koble(os.environ["DISPONIT_MIGRATOR_URL"])
    vconn = None
    try:
        o = drill["oppsett"]
        # Siste ledd i A1-kjeden, målt i registeret FØR fullmakten tas:
        # de innsjekkede artefaktene kan være enige med hverandre om et
        # image ingen av de levende radene bærer.
        verifiser_registrert_digest(
            conn, (o["drillet_release"], o["kandidat_release"]), digest)
        # …og hvert drilledd må bære CLAIM-SPORET sitt (Codex P1, runde
        # 20). `registrer_moduldrill` måler leddene mot
        # `oppdrag.claim_release_id`; mangler sporet, blir drillraden RØD
        # og aksepten faller på en FK-melding som ikke sier hvorfor.
        # Oppdrag claimet FØR migrasjon 049 har intet spor — kolonnen
        # fantes ikke — og det er nettopp svaret operatøren trenger.
        # Måles her, i det samme lesevinduet som digesten, før fullmakten
        # tas og før noe skrives.
        verifiser_claim_spor(conn, o, inflight_opp, rullback_opp,
                             kandidat_opp)
        # …og manifestet radene ble REGISTRERT fra, må være dette
        # manifestet utenfor evidensbindingen (Codex P1, runde 7): like
        # image-bytes sier ingenting om hvilken modulkonfigurasjon den
        # aksepterte deploymenten faktisk kjører.
        manifestkilde = verifiser_registrert_manifest(
            conn, (o["drillet_release"], o["kandidat_release"]),
            manifest, manifest_sha, manifest_commit)
        # #124: basens NÅ-svar for de ti kontrollkjøringene — i samme
        # lesevindu som digest- og claim-sporene, før noe skrives.
        verifiser_kjoringsattester(conn, runde)
        # REFERATET FRA VEIEN SOM SPURTE (Codex P1, #117 runde 16).
        # Basen sammenlignet `kilde_ref` med sine egne to parametre, og
        # to felter fra samme kaller som er enige, er ingen CI-kjøring.
        # Basen kan ikke spørre GitHub — men den kan kreve at den som
        # gjorde det, skrev ned HVA SVARET SA, i en immutabel rad, og så
        # regne invariantpunktene mot kravet i registeret i stedet for
        # mot kalleren. Verdiene her er svarets egne, ikke aksepterens:
        # `verifiser_ci_kjoring` har alt avvist alt annet enn en grønn
        # `ci.yml`-push på `main` for akseptcommiten.
        #
        # …og attesten skrives med EN ANNEN INNLOGGING enn aksepten
        # (Codex P1, runde 19→22). Første form la skillet i rollene:
        # `disponit_modules_admin` har ikke EXECUTE på
        # `attester_ci_kjoring`, så attesten ble skrevet under et
        # `SET ROLE disponit_modul_eier` på DENNE forbindelsen. Men
        # migrator er medlem av begge rollene, og `WITH INHERIT FALSE`
        # sperrer arv, ikke `SET ROLE`: én autentisert identitet gjorde
        # begge rolleskiftene, og attestanten var akseptøren.
        # Attesten har nå sin EGEN forbindelse, med sin egen innlogging
        # (`DISPONIT_VERIFIKATOR_URL`), og basen krever forskjellen —
        # `aksepter_moduldeployment` avviser en attest hvis
        # `attestert_av` er akseptørens `session_user`. Attestene
        # committes FØR aksepten leser dem: to forbindelser deler ingen
        # transaksjon, og et referat som ikke er skrevet, kan ikke leses.
        vconn = koble(os.environ["DISPONIT_VERIFIKATOR_URL"])
        vconn.execute(
            "SELECT attester_ci_kjoring(%s,%s,%s,%s,%s,%s,'m56-aksept')",
            (str(ci_kjoring.get("id")), ci_kjoring.get("path"),
             ci_kjoring.get("event"), ci_kjoring.get("head_branch"),
             ci_kjoring.get("conclusion"), ci_kjoring.get("head_sha")))
        # …og det SAMME referatet for evidensfilen (Codex P1, runde 19).
        # De fire målte punktene hvilte på at `p_evidens_sha` og
        # punktenes `kilde_ref` — to felter fra samme kall — var enige om
        # en hale. Nå skriver veien som faktisk hashet råfilen ned hvilken
        # sti bytene lå på og hva filen bar for hvert punkt, og aksepten
        # regner punktet mot DEN raden. Verdiene er `verifiser_kilde`- og
        # `_sjekk_grenser`-portenes egne; kallet under kan bare gjenta dem.
        # …og attesten navngir DRILLEN de tre sammenhengspunktene ble
        # regnet mot (Codex P1, PR #123). `sammenheng_verdier` leser
        # BEGGE artefaktene, men referatet bar bare rundens sha: en
        # grønn attest fra ett forsøk kunne derfor gjenbrukes av et
        # senere direktekall med en ANNEN drill, og
        # `evidens.pa_tvers_av_runder` sto grønt for et forhold som
        # aldri ble målt mot den drillen. Drillartefaktets bytes er nå
        # en del av attestens identitet, og `aksepter_moduldeployment`
        # slår opp attesten med bytene drillraden ble registrert med.
        # …med IDENTITETSREFERATET i samme kall (053): hvilke kjøringer
        # filen navngir, drillscopet — basen krever at akseptens liste
        # er ordrett referatets. Kanonisk form: kommadelt.
        vconn.execute(
            "SELECT attester_evidensfil(%s,%s,%s,%s::jsonb,'m56-aksept',"
            "%s,%s)",
            (KRAV, runde["oppsett"]["kilde"], evidens_sha,
             json.dumps(evidens_maalt), drill_sha,
             ",".join(str(o) for o in runde["identiteter"]["kjoringer"])))
        vconn.commit()
        vconn.close()
        vconn = None
        conn.execute("SET ROLE disponit_modules_admin")
        # Codex' P1 på PR #117 (runde 5): de tre kontrollpunktutfallene
        # ble regnet ut HER, av artefaktets egne tall, og sendt inn som
        # boolske verdier. Den som holdt `disponit_modules_admin` — den
        # brede deployfullmakten — kunne derfor hoppe over hele dette
        # skriptet, kalle definerne direkte med tre håndskrevne `true`,
        # og få en immutabel grønn aksept uten å ha kjørt noe. Nå sender
        # kallet HVA drillen ble målt på (tenanten, de tre oppdragene og
        # bytene artefaktet består av), og basen måler utfallene selv i
        # `oppdrag`/`artefakt`. Artefaktets `maalt`-tall er fortsatt
        # portet av `_sjekk_grenser` over; forskjellen er at raden ikke
        # lenger HVILER på dem.
        # Codex' P2 (runde 5): `epoch_snapshot` ble snapshottet av basen
        # ved registreringen, mens artefaktets egen `oppsett.module_epoch`
        # aldri ble sendt eller sammenlignet. En nødstopp eller
        # reaktivering mellom drill og aksept flytter generasjonen, og
        # raden ville da påstått en annen fencing-kontekst enn den
        # drillen faktisk målte i. Artefaktet sier nå hvilken det var, og
        # basen krever at det er den levende.
        drill_id = conn.execute(
            "SELECT registrer_moduldrill(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
            "%s,'m56-aksept',%s)",
            (MODUL, MILJO, o["drillet_release"], o["rullback_release"],
             o["kandidat_release"], TENANT,
             inflight_opp, rullback_opp, kandidat_opp,
             drillens_epoch(drill), drill_sha,
             f"drill-{o['kandidat_release']}", drill_ts)).fetchone()[0]
        # Drillpunktene (§5): `kilde_ref` er `rollback_drill`-hendelsen
        # drillraden nettopp skrev (eller skrev ved forrige replay —
        # SP-2 gir samme rad). Utfallene bor i BASEN: FK-en fra
        # akseptraden refererer drillraden med de tre utfallsboolene i
        # den refererbare nøkkelen, så en aksept mot en rad med et rødt
        # kontrollpunkt finnes ikke — verdiene her er filens egne,
        # alt målt grønne av evidensporten (`les_bundet_artefakt`).
        # Hendelsen leses som migrator (registerhendelser eies av
        # migrator; deployfullmakten trenger den ikke) — samme grep som
        # kvitteringslesningen under.
        conn.execute("RESET ROLE")
        hendelse = conn.execute(
            "SELECT id FROM modulregister_hendelse"
            " WHERE modul_id=%s AND hendelse='rollback_drill'"
            "   AND (detalj->>'drill_id')::bigint=%s"
            " ORDER BY id DESC LIMIT 1", (MODUL, drill_id)).fetchone()
        if hendelse is None:
            raise SystemExit(f"AVBRUTT: drillrad {drill_id} har ingen"
                             " rollback_drill-registerhendelse — raden"
                             " og hendelsen skrives i samme kall, så"
                             " dette er en programfeil, ikke en tilstand")
        for punkt, (grense, verdi) in DRILL_PUNKTER.items():
            punkter[punkt] = {"grenseverdi": grense, "maalt_verdi": verdi,
                              "kilde_type": "registerhendelse",
                              "kilde_ref": str(hendelse[0])}
        conn.execute("SET ROLE disponit_modules_admin")
        conn.execute(
            "SELECT aksepter_moduldeployment(%s,%s,%s,%s,%s,%s,%s::uuid,"
            "%s,%s,%s,%s,%s::jsonb,%s,'m56-aksept',%s::bigint[])",
            (MODUL, MILJO, o["kandidat_release"], drill_id, KRAV,
             TENANT, e2e_artefakt, evidens_sha, manifest_commit,
             a.ci_run, ci_commit, json.dumps(punkter),
             f"aksept-{o['kandidat_release']}",
             list(runde["identiteter"]["kjoringer"])))
        conn.commit()
        # Codex' P1 på PR #117 (runde 2): kvitteringslesningen kjørte
        # fortsatt som `disponit_modules_admin`, og 049 gir den rollen
        # BARE `EXECUTE` på de to definerne — `SELECT` på tabellen har
        # eier og runtime, ikke admin. Raden var altså skrevet og
        # committet, hvorpå denne lesningen ga `permission denied`: både
        # kjøringen og hvert eneste forsøk på nytt rapporterte feil på en
        # aksept som ALT lå der. Fullmakten legges ned når den er brukt —
        # kvitteringen leses som migrator (samme grep som drillskriptets
        # etterkontroll).
        conn.execute("RESET ROLE")
        rad = conn.execute(
            "SELECT akseptert_ts FROM modulaksept WHERE modul_id=%s"
            " AND miljo=%s AND release_id=%s",
            (MODUL, MILJO, o["kandidat_release"])).fetchone()
        print(f"AKSEPTERT: ({MODUL}, {MILJO}, {o['kandidat_release']})"
              f" drill_id={drill_id} ts={rad[0]}"
              f" manifestgenerasjon={manifestkilde[:12]}…")
    finally:
        if vconn is not None:
            vconn.close()
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
