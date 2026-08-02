#!/usr/bin/env python3
"""Ytelsesporten perf-m01-v1 (v2 Del 6). Kjøres på staging, mot API-et.

Måler HELE nettverksveien — ikke motoren direkte. Det er poenget: m01s
ytelsestall skal gjelde det kundene faktisk treffer, med token-oppslag,
transaksjoner, kryptering og revisjonslogg inkludert.

Kontrakten, punkt for punkt:
  * 10 sekunder warmup UTENFOR målingen
  * deretter NØYAKTIG 6 000 målte forespørsler
  * open-loop 100 req/s, 20 samtidige forbindelser
  * p95 serversvartid < 150 ms over alle 6 000
  * null HTTP-, timeout- og DB-feil, og ingen skjulte retries
  * etterkontroll mot databasen: 6 000 auditerte beslutninger = 6 000
    revisjonsrader, og antall unntaksrader stemmer med routingreglene for
    den syntetiske miksen
  * CPU, minne, DB-tilkoblinger og lock-waits samples hvert 5. sekund
  * testtoken og payloads genereres FØR warmup

Resultatet skrives som JSON-artefakt. Manifestfeltet peker på artefaktet,
og er aldri selv beviset (v2 Del 6).

BRUK:
  DISPONIT_TOKEN_PEPPER=... DISPONIT_ATT_NOKLER=... \\
  DISPONIT_TEST_MIGRATOR_DSN=... \\
    python3 deploy/staging/lasttest-m01.py --base http://127.0.0.1:8099
"""
import argparse
import json
import os
import queue
import statistics
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROT / "platform/core"))

KRAV_ID = "perf-m01-v1"
MALTE = 6000
RATE = 100.0                 # req/s, open loop
SAMTIDIGE = 20
WARMUP_SEK = 10
P95_KRAV_MS = 150.0
ANDEL_UNNTAK = 0.20          # den syntetiske miksen
ARTEFAKTMAPPE = ROT / "deploy/staging/artefakter"


# ---------------------------------------------------------------------------
# Syntetisk last
# ---------------------------------------------------------------------------

def lag_payloads(policy: dict, tenant: str, nokler: dict, antall: int,
                 andel_unntak: float) -> list[tuple[dict, bool]]:
    """Alle forespørsler bygges FØR warmup (v2 Del 6).

    Signering av 6 000 attestasjoner koster millisekunder hver. Gjøres det
    underveis, måler vi HMAC-en vår egen lasttest bruker i stedet for
    plattformen — og p95 blir et tall om testverktøyet.
    """
    from policy_validator import attestering
    naa = datetime.now(timezone.utc)
    pid = policy["meta"]["policy_id"]
    ut: list[tuple[dict, bool]] = []
    grense = int(antall * andel_unntak)
    for i in range(antall):
        ressurs = f"last-{i}"
        if i < grense:
            # UNNTAK-grenen: ukjent handling => deny by default => sak i
            # ordinær kø. Uten attestasjoner, ellers treffer den
            # bindingsporten og blir en SIKKERHETSsak i stedet — da måler
            # etterkontrollen feil kø.
            ut.append(({"handling": "last.ukjent", "ressurs_id": ressurs,
                        "faktura_id": ressurs, "dataklasser": ["finansiell"],
                        "dataklasser_kilde": "connector"}, True))
            continue
        attester = {}
        for vilkaar in ("forfall_passert_dager", "ingen_aktiv_tvist"):
            a = {"verifikator": "v_fordring", "tenant_id": tenant,
                 "handling": "purring.send", "vilkaar": vilkaar,
                 "ressurs_id": ressurs, "policy_id": pid,
                 "utstedt": (naa - timedelta(minutes=5)).isoformat(),
                 "utloper": (naa + timedelta(hours=6)).isoformat(),
                 "jti": f"{vilkaar[:4]}-{ressurs}-" + "z" * 20,
                 "resultat": True}
            if vilkaar == "forfall_passert_dager":
                a["verdi"] = 20
                a.pop("resultat")
            nid, hemmelighet = next(iter(nokler["v_fordring"].items()))
            attester[vilkaar] = attestering.signer(a, nid, hemmelighet)
        # Unik faktura_id per forespørsel: frekvensgrensen i policyen er per
        # grupperingsnøkkel, og med samme nøkkel ville lasttesten målt
        # frekvensavvisninger i stedet for beslutninger.
        ut.append(({"handling": "purring.send", "ressurs_id": ressurs,
                    "faktura_id": ressurs, "dataklasser": ["finansiell"],
                    "dataklasser_kilde": "connector",
                    "attestasjoner": attester}, False))
    return ut


# ---------------------------------------------------------------------------
# Ressurssampling
# ---------------------------------------------------------------------------

class Sampler(threading.Thread):
    """Hvert 5. sekund: CPU, minne, DB-tilkoblinger og lock-waits."""

    def __init__(self, dsn: str | None, intervall: float = 5.0) -> None:
        super().__init__(daemon=True)
        self.dsn = dsn
        self.intervall = intervall
        self.proever: list[dict] = []
        self._stopp = threading.Event()

    def _system(self) -> dict:
        ut: dict = {}
        try:
            ut["loadavg"] = float(Path("/proc/loadavg").read_text().split()[0])
        except Exception:
            pass
        try:
            for linje in Path("/proc/meminfo").read_text().splitlines():
                if linje.startswith(("MemTotal:", "MemAvailable:")):
                    navn, verdi = linje.split(":")
                    ut[navn.strip()] = int(verdi.split()[0])
        except Exception:
            pass
        return ut

    def _database(self, conn) -> dict:
        rad = conn.execute(
            "SELECT count(*) FILTER (WHERE state IS NOT NULL),"
            "       count(*) FILTER (WHERE wait_event_type='Lock')"
            "  FROM pg_stat_activity WHERE datname = current_database()"
        ).fetchone()
        laaser = conn.execute(
            "SELECT count(*) FROM pg_locks WHERE NOT granted").fetchone()[0]
        conn.rollback()
        return {"db_tilkoblinger": rad[0], "db_venter_paa_laas": rad[1],
                "db_ikke_innvilgede_laaser": laaser}

    def run(self) -> None:
        conn = None
        if self.dsn:
            try:
                from db.pg import koble
                conn = koble(self.dsn)
            except Exception:
                conn = None
        try:
            while not self._stopp.wait(self.intervall):
                proeve = {"t": round(time.monotonic(), 2), **self._system()}
                if conn is not None:
                    try:
                        proeve.update(self._database(conn))
                    except Exception as e:
                        proeve["db_feil"] = type(e).__name__
                self.proever.append(proeve)
        finally:
            if conn is not None:
                conn.close()

    def stopp(self) -> None:
        self._stopp.set()


# ---------------------------------------------------------------------------
# Open-loop-generatoren
# ---------------------------------------------------------------------------

def kjor(base: str, policy_id: str, token: str,
         payloads: list[tuple[dict, bool]], rate: float, samtidige: int,
         merkelapp: str) -> list[dict]:
    """Open loop: avsendingstidspunktene er BESTEMT PÅ FORHÅND.

    En lukket sløyfe (send neste når forrige er ferdig) senker farten når
    systemet blir tregt, og skjuler dermed nøyaktig det man måler —
    «coordinated omission». Her ligger planen fast, og køtiden en treg
    server påfører havner i `total_ms`.
    """
    import httpx
    oppgaver: queue.Queue = queue.Queue()
    resultater: list[dict] = []
    laas = threading.Lock()
    # Ingen retries: en skjult retry gjør en feil om til en langsom suksess,
    # og da måler p95 noe annet enn det kontrakten spør om.
    transport = httpx.HTTPTransport(retries=0)

    def arbeider():
        with httpx.Client(timeout=30.0, transport=transport) as klient:
            while True:
                jobb = oppgaver.get()
                if jobb is None:
                    oppgaver.task_done()
                    return
                i, planlagt, event, venter_sak = jobb
                naa = time.monotonic()
                svar_status, feil = None, None
                t0 = time.monotonic()
                try:
                    r = klient.post(
                        f"{base}/v1/beslutning",
                        json={"policy_id": policy_id, "event": event},
                        headers={"authorization": f"Bearer {token}",
                                 "idempotency-key": f"{merkelapp}-{i}"})
                    svar_status = r.status_code
                    beslutning = r.json().get("beslutning")
                except Exception as e:
                    feil = type(e).__name__
                    beslutning = None
                t1 = time.monotonic()
                with laas:
                    resultater.append({
                        "i": i, "status": svar_status, "feil": feil,
                        "beslutning": beslutning, "venter_sak": venter_sak,
                        "svartid_ms": (t1 - t0) * 1000,
                        "kotid_ms": max(0.0, (naa - planlagt) * 1000),
                        "total_ms": (t1 - planlagt) * 1000})
                oppgaver.task_done()

    traader = [threading.Thread(target=arbeider, daemon=True)
               for _ in range(samtidige)]
    for t in traader:
        t.start()

    start = time.monotonic()
    for i, (event, venter_sak) in enumerate(payloads):
        planlagt = start + i / rate
        forsinkelse = planlagt - time.monotonic()
        if forsinkelse > 0:
            time.sleep(forsinkelse)
        oppgaver.put((i, planlagt, event, venter_sak))
    oppgaver.join()
    for _ in traader:
        oppgaver.put(None)
    for t in traader:
        t.join(10)
    return resultater


def persentil(verdier: list[float], p: float) -> float:
    if not verdier:
        return float("nan")
    s = sorted(verdier)
    k = max(0, min(len(s) - 1, int(round(p / 100.0 * len(s) + 0.5)) - 1))
    return s[k]


# ---------------------------------------------------------------------------
# Etterkontroll
# ---------------------------------------------------------------------------

def etterkontroll(dsn: str, tenant: str, merkelapp: str,
                  resultater: list[dict]) -> dict:
    """1:1 mellom auditerte beslutninger og revisjonsrader.

    Teller på `idempotency_key`-prefikset, ikke på totalen i tabellen:
    staging har historikk fra før, og en telling av «alle rader» ville
    bestått uansett hva denne kjøringen gjorde.
    """
    from db.pg import koble
    conn = koble(dsn)
    try:
        conn.execute("SELECT set_config('disponit.tenant', %s, true)",
                     (tenant,))
        logg = conn.execute(
            "SELECT count(*) FROM revisjonslogg"
            " WHERE tenant=%s AND idempotency_key LIKE %s",
            (tenant, merkelapp + "-%")).fetchone()[0]
        saker = conn.execute(
            "SELECT sakstype, count(*) FROM unntak u"
            " WHERE u.tenant=%s AND u.loggpost_id IN"
            "   (SELECT id FROM revisjonslogg WHERE tenant=%s"
            "      AND idempotency_key LIKE %s)"
            " GROUP BY sakstype",
            (tenant, tenant, merkelapp + "-%")).fetchall()
        conn.rollback()
    finally:
        conn.close()
    per_sakstype = {r[0]: r[1] for r in saker}
    auditert = sum(1 for r in resultater if r["status"] == 200)
    forventet_saker = sum(1 for r in resultater
                          if r["status"] == 200 and r["venter_sak"])
    return {
        "auditerte_svar": auditert,
        "revisjonsrader": logg,
        "en_til_en": auditert == logg,
        "unntaksrader_per_sakstype": per_sakstype,
        "forventede_normalsaker": forventet_saker,
        "routing_stemmer": per_sakstype.get("normal", 0) == forventet_saker
                           and per_sakstype.get("sikkerhet", 0) == 0,
    }


# ---------------------------------------------------------------------------
# Inngang
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--base", default="http://127.0.0.1:8099")
    p.add_argument("--tenant", default="t-lasttest")
    p.add_argument("--policy", default=str(ROT / "policies/bransjemal-tjenestebedrift.yaml"))
    p.add_argument("--dsn", default=os.environ.get("DISPONIT_TEST_MIGRATOR_DSN")
                   or os.environ.get("DISPONIT_MIGRATOR_URL"))
    p.add_argument("--antall", type=int, default=MALTE)
    p.add_argument("--rate", type=float, default=RATE)
    p.add_argument("--samtidige", type=int, default=SAMTIDIGE)
    p.add_argument("--warmup", type=float, default=WARMUP_SEK)
    p.add_argument("--token", help="token_id.secret; lages ellers via CLI-en")
    p.add_argument("--ut", default=None)
    args = p.parse_args(argv)

    import yaml
    from policy_validator.attestering import last_nokler
    policy = yaml.safe_load(Path(args.policy).read_text(encoding="utf-8"))
    nokler = last_nokler()

    token = args.token or os.environ.get("DISPONIT_LASTTEST_TOKEN")
    if not token:
        print("AVBRUTT: oppgi --token (eller DISPONIT_LASTTEST_TOKEN)."
              " Tokens lages med deploy/staging/token-cli.py — lasttesten"
              " oppretter dem ikke selv, den skal ikke ha den rettigheten.")
        return 2

    # To merkelapper som IKKE er prefiks av hverandre. Warmup het tidligere
    # `<merkelapp>-warm`, og etterkontrollen teller med `LIKE '<merkelapp>-%'`
    # — den fanget dermed warmupens egne rader og rapporterte 1 181
    # revisjonsrader for 600 målte svar. Feilen var i TESTEN, ikke i
    # plattformen, og den ville sett ut som et brudd på 1:1-garantien.
    stempel = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    merkelapp = "maal-" + stempel
    warm_merkelapp = "warm-" + stempel
    print(f"genererer {args.antall} payloads …")
    payloads = lag_payloads(policy, args.tenant, nokler, args.antall,
                            ANDEL_UNNTAK)
    oppvarming = lag_payloads(policy, args.tenant, nokler,
                              max(1, int(args.rate * args.warmup)),
                              ANDEL_UNNTAK)

    pid = policy["meta"]["policy_id"]
    print(f"warmup {args.warmup} s (utenfor målingen) …")
    kjor(args.base, pid, token, oppvarming, args.rate, args.samtidige,
         warm_merkelapp)

    sampler = Sampler(args.dsn)
    sampler.start()
    print(f"måler {args.antall} forespørsler @ {args.rate}/s …")
    t_start = time.monotonic()
    resultater = kjor(args.base, pid, token, payloads, args.rate,
                      args.samtidige, merkelapp)
    varighet = time.monotonic() - t_start
    sampler.stopp()
    sampler.join(10)

    svartider = [r["svartid_ms"] for r in resultater if r["feil"] is None]
    totaltider = [r["total_ms"] for r in resultater if r["feil"] is None]
    feil = [r for r in resultater if r["feil"] is not None
            or r["status"] != 200]
    strupet = [r for r in resultater if r["status"] == 429]
    if strupet:
        # Egen melding, ikke bare et tall i artefaktet: 429 her betyr at
        # rate-grensen er satt lavere enn ytelseskravet. Det er en
        # KONFIGURASJONSMOTSIGELSE, ikke en ytelsesfeil, og den som leser
        # artefaktet skal ikke måtte gjette hvilken av delene det var.
        print(f"ADVARSEL: {len(strupet)} av {len(resultater)} ble"
              f" rate-begrenset (429). Rate-grensen er lavere enn"
              f" ytelseskravet på {args.rate}/s — sett DISPONIT_RATE_PER_MIN"
              f" til minst {int(args.rate * 60)} på API-prosessen.",
              file=sys.stderr)
    p95 = persentil(svartider, 95)
    p95_total = persentil(totaltider, 95)

    kontroll = etterkontroll(args.dsn, args.tenant, merkelapp, resultater) \
        if args.dsn else {"hoppet_over": "ingen DSN oppgitt"}

    bestatt = (len(resultater) == args.antall and not feil
               and p95 < P95_KRAV_MS and kontroll.get("en_til_en", False)
               and kontroll.get("routing_stemmer", False))

    artefakt = {
        "krav_id": KRAV_ID,
        "ts": datetime.now(timezone.utc).isoformat(),
        "bestatt": bestatt,
        "oppsett": {"base": args.base, "antall": args.antall,
                    "rate_per_sek": args.rate, "samtidige": args.samtidige,
                    "warmup_sek": args.warmup, "tenant": args.tenant,
                    "merkelapp": merkelapp, "warm_merkelapp": warm_merkelapp,
                    "andel_unntak": ANDEL_UNNTAK},
        "krav": {"p95_svartid_ms": P95_KRAV_MS, "null_feil": True,
                 "en_til_en_loggposter": True},
        "maalt": {
            "antall": len(resultater),
            "varighet_sek": round(varighet, 2),
            "oppnadd_rate": round(len(resultater) / varighet, 1) if varighet else 0,
            "feil": len(feil),
            "feiltyper": sorted({str(r["feil"] or r["status"]) for r in feil}),
            "rate_begrenset": len(strupet),
            "svartid_ms": {
                "p50": round(persentil(svartider, 50), 1),
                "p95": round(p95, 1),
                "p99": round(persentil(svartider, 99), 1),
                "maks": round(max(svartider), 1) if svartider else None,
                "snitt": round(statistics.fmean(svartider), 1) if svartider else None},
            # Total = fra PLANLAGT avsending til svar. Avviker den mye fra
            # svartiden, klarte ikke generatoren å holde raten, og p95 alene
            # underrapporterer hva en klient faktisk opplevde.
            "total_ms_p95": round(p95_total, 1),
            "beslutninger": {b: sum(1 for r in resultater if r["beslutning"] == b)
                             for b in ("TILLAT", "STOPP", "UNNTAK")}},
        "etterkontroll": kontroll,
        "ressursproever": sampler.proever,
    }

    ARTEFAKTMAPPE.mkdir(parents=True, exist_ok=True)
    sti = Path(args.ut) if args.ut else ARTEFAKTMAPPE / f"{KRAV_ID}-{merkelapp}.json"
    sti.write_text(json.dumps(artefakt, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps({k: artefakt[k] for k in ("krav_id", "bestatt", "maalt",
                                               "etterkontroll")},
                     ensure_ascii=False, indent=2))
    print(f"artefakt: {sti}")
    return 0 if bestatt else 1


if __name__ == "__main__":
    sys.exit(main())
