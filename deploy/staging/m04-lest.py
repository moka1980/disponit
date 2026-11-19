#!/usr/bin/env python3
"""M-04s sertifiseringslest — klasse B i ADR-002 (plattformregister + måler).

M-04 passer ikke den generiske sveiplesten. Registeret er GLOBALT og
seedes i migrasjon, funnene gjelder plattformens egne tabeller, og
målingen er kryss-tenant. En rigg som skulle gitt kjent fasit, måtte
derfor skrevet plattformdommer — registerrader, tabeller og funksjoner —
og det skal aldri skje i driftsbasen.

Derfor deler lesten seg i to baser:

  * FASIT, FEILINJISERING og ROLLBACK rigges i den ISOLERTE basen på
    55432, bygget fra commiten som måles (`bygg-i-utsjekk.sh`). Der kan
    riggen lage tabeller, registerrader og en reaper som forsvinner, uten
    at et eneste tall i driftens retensjonsrapport flytter seg.
  * YTELSE måles på den EKTE jobben mot driftsbasen, med lagermålerens
    egen credential. Den skriver bare til M-04s egne måletabeller,
    akkurat som timeren gjør hver natt.

PUNKTENE (ADR-002 D1–D4):

  fasit           syntetisk_datasett_likt_lokalt + revisjonslogg_korrekt.
                  Sju lagre med kjent dom; to fulle målinger; lukking
                  målt; og MÅLT FRAVÆR: null nye rader i revisjonsloggen,
                  null endrede rader utenfor M-04s fem egne tabeller,
                  ingen innloggingsrolle utenom migratoren med skriverett
                  på registeret, og nøyaktig én definer-funksjon målerens
                  rolle kan kalle.
  feilinjisering  to ekte driftsfeil: `statement_timeout` på 1 ms (hvert
                  lager skal bli målt ELLER et `umaalbar`-funn, aldri en
                  null), og en tilkobling uten EXECUTE (feilet, alarm på
                  den andre).
  rollback        kjernen rulles til forgjengeren og fram igjen. Drillen
                  dreper en kjøring midt i et lager, så forgjengerens og
                  kandidatens kode måler hver sin hele runde: samme åpne
                  funn, ingen dubletter, og den drepte målingen står som
                  avbrutt.
  ytelse          tre kjøringer av `drift.kjor_retensjonsmaaling` på
                  driftsbasen.

Skriptet dømmer ingenting: `bestatt` er produsentens påstand, og
`manifestskjema._grenser_m04_*` validerer.

BRUK (på verten):
  # som administrator, med riggens DSN-er eksportert (kjor-suite.sh-formen)
  python deploy/staging/m04-lest.py fasit
  python deploy/staging/m04-lest.py feilinjisering
  python deploy/staging/m04-lest.py rollback --forgjenger /opt/disponit/releases/<sha>
  # som root, med credentialen til lagermåleren
  python deploy/staging/m04-lest.py ytelse --dsn-fil /etc/disponit/lagermaaler/DISPONIT_LAGERMAALER_URL
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (_sjekk_grenser, kjerne_digest,  # noqa: E402
                            m04_bevisrot_sha256, release_digest,
                            valider_artefaktformat, M04_MODULFILER)

UT = REPO / "deploy/staging/artefakter"

#: M-04s fem egne tabeller. Alt annet i `public` skal stå urørt.
EGNE = ("retensjonslager", "retensjonsmaaling", "retensjonsstorrelse",
        "retensjonsbeholdning", "retensjonsfunn")

#: Registerrolle og målerolle.
EIER = "disponit_lager_eier"
MAALER = "disponit_lagermaaler"
MAALEDOR = "m4_mal_lagre(integer,boolean)"


def _log(*a):
    print(f"[{datetime.now(timezone.utc):%H:%M:%S}]", *a, flush=True)


def _env(navn: str) -> str:
    v = os.environ.get(navn)
    if not v:
        sys.exit(f"{navn} mangler — kjør med riggens DSN-er eksportert")
    return v


def _skriv(krav: str, art: dict) -> Path:
    art["krav_id"] = krav
    art["ts"] = datetime.now(timezone.utc).isoformat()
    art.setdefault("feil", [])
    # Formatet valideres med `bestatt` på plass (skjemaet krever feltet);
    # dommen settes etterpå, av feillisten.
    art["bestatt"] = True
    art["feil"] = sorted(set(art["feil"]
                             + valider_artefaktformat(art, krav)
                             + _sjekk_grenser(krav, art)))
    art["bestatt"] = not art["feil"]
    UT.mkdir(parents=True, exist_ok=True)
    navn = (f"{krav}-"
            f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")
    sti = UT / navn
    sti.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                              sort_keys=True) + "\n", encoding="utf-8")
    _log(f"skrev {sti} (bestatt={art['bestatt']})")
    for f in art["feil"]:
        _log("  FEIL:", f)
    return sti


# ---------------------------------------------------------------------------
# Riggen — bare i den isolerte basen.
# ---------------------------------------------------------------------------

def _er_isolert(sup) -> None:
    """Nekter å rigge i noe annet enn riggbasen. Plattformdommer i
    driftsbasen er nettopp det ADR-002 sier aldri skal skje."""
    db, port = sup.execute(
        "SELECT current_database(), current_setting('port')").fetchone()
    if db != "disponit_test" or port != "55432":
        sys.exit(f"NEKTET: riggen skriver plattformdommer og kjører bare i"
                 f" disponit_test på 55432 (fikk {db} på {port})")


def _tabell(mg, navn: str) -> None:
    mg.execute(
        f"CREATE TABLE {navn} (tenant text NOT NULL,"
        " rad_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
        " opprettet timestamptz NOT NULL DEFAULT now(),"
        " slettet_ts timestamptz)")
    mg.execute(f"GRANT SELECT (tenant, opprettet, slettet_ts) ON {navn}"
               f" TO {EIER}")
    mg.commit()


def _registrer(mg, navn: str, **felt) -> None:
    v = {"lager_id": navn, "relasjon": navn, "klasse": "persondata",
         "tenantkolonne": "tenant", "alderskolonne": "opprettet",
         "reapetkolonne": "slettet_ts", "fristkilde": f"{navn}.frist",
         "frist_dogn": 90, "reaper": "m4_reap_egne_maalinger",
         "dom": "under_frist", "dom_begrunnelse": "fasit (m04-lest)",
         "dom_migrasjon": "m04-lest"}
    v.update(felt)
    mg.execute(f"SET LOCAL ROLE {EIER}")
    mg.execute(
        "INSERT INTO retensjonslager (lager_id, relasjon, klasse,"
        " tenantkolonne, alderskolonne, reapetkolonne, fristkilde,"
        " frist_dogn, reaper, dom, dom_begrunnelse, dom_migrasjon) VALUES"
        " (%(lager_id)s,%(relasjon)s,%(klasse)s,%(tenantkolonne)s,"
        "  %(alderskolonne)s,%(reapetkolonne)s,%(fristkilde)s,"
        "  %(frist_dogn)s,%(reaper)s,%(dom)s,%(dom_begrunnelse)s,"
        "  %(dom_migrasjon)s)", v)
    mg.commit()


#: (merkelapp, forventet funntype etter ANDRE måling, første måling)
SETT: tuple[tuple[str, str | None, str | None], ...] = (
    ("uregistrert", "uregistrert", "uregistrert"),
    ("umaalbar", "umaalbar", "umaalbar"),
    ("uten_dom", "uten_dom", "uten_dom"),
    ("reaper_mangler", "reaper_mangler", "reaper_mangler"),
    ("reaper_henger", "reaper_henger", "reaper_henger"),
    # Den RENE fødes uregistrert, og tabellen forsvinner mellom
    # målingene: «funnet lukkes når grunnen forsvinner».
    ("ren", None, "uregistrert"),
    # Den NEGATIVE KONTROLLEN: et riktig registrert, ferskt lager.
    ("ok", None, None),
)
RENSES = "ren"


def forbered(mg, runde: str) -> dict:
    """Sju lagre med kjent dom. Returnerer merkelapp → lager_id."""
    p = f"fm4_{runde}_"
    s: dict[str, str] = {}

    _tabell(mg, p + "ureg")
    s["uregistrert"] = "uregistrert:" + p + "ureg"
    _tabell(mg, p + "ren")
    s["ren"] = "uregistrert:" + p + "ren"

    _tabell(mg, p + "umaal")
    _registrer(mg, p + "umaal")
    mg.execute(f"REVOKE SELECT ON {p}umaal FROM {EIER}")
    mg.commit()
    s["umaalbar"] = p + "umaal"

    _tabell(mg, p + "udom")
    _registrer(mg, p + "udom", dom="uten_frist_apen", reaper=None,
               fristkilde=None, frist_dogn=None)
    s["uten_dom"] = p + "udom"

    # Reaperen må FINNES når raden skrives (triggeren nekter en løgn),
    # og forsvinne etterpå — slik den gjør i drift når en migrasjon
    # dropper en funksjon registeret fortsatt peker på.
    mg.execute(f"CREATE FUNCTION {p}reaper() RETURNS int"
               " LANGUAGE sql AS 'SELECT 0'")
    mg.commit()
    _tabell(mg, p + "rmang")
    _registrer(mg, p + "rmang", reaper=f"{p}reaper")
    mg.execute(f"DROP FUNCTION {p}reaper()")
    mg.commit()
    s["reaper_mangler"] = p + "rmang"

    _tabell(mg, p + "rheng")
    _registrer(mg, p + "rheng")
    mg.execute(f"INSERT INTO {p}rheng (tenant, opprettet)"
               " SELECT 'fm4-t', now() - interval '400 days'"
               " FROM generate_series(1, 3)")
    mg.commit()
    s["reaper_henger"] = p + "rheng"

    _tabell(mg, p + "ok")
    _registrer(mg, p + "ok")
    mg.execute(f"INSERT INTO {p}ok (tenant) VALUES ('fm4-t')")
    mg.commit()
    s["ok"] = p + "ok"
    return s


def rens(mg, runde: str) -> None:
    mg.execute(f"DROP TABLE fm4_{runde}_ren")
    mg.commit()


# ---------------------------------------------------------------------------
# Målinger — som superbruker i riggen (lesing forbi RLS), aldri skriving.
# ---------------------------------------------------------------------------

def _apne(sup, lager_ids: list[str]) -> dict[str, list[str]]:
    rader = sup.execute(
        "SELECT lager_id, funntype FROM retensjonsfunn"
        " WHERE lager_id = ANY(%s) AND lukket_maaling IS NULL"
        " ORDER BY 1, 2", (lager_ids,)).fetchall()
    sup.rollback()
    ut: dict[str, list[str]] = {}
    for lid, ft in rader:
        ut.setdefault(lid, []).append(ft)
    return ut


def _dom(sup, subjekter: dict[str, str], kolonne: int) -> tuple[list, int]:
    apne = _apne(sup, list(subjekter.values()))
    per, avvik = [], 0
    for merke, *forv in SETT:
        ventet = forv[kolonne]
        fikk = sorted(apne.get(subjekter[merke], []))
        ok = fikk == ([ventet] if ventet else [])
        avvik += 0 if ok else 1
        per.append({"merke": merke, "ventet": ventet, "fikk": fikk,
                    "lager_id": subjekter[merke]})
    return per, avvik


def _radbilde(sup) -> dict[str, str]:
    """Innholdsdigest for HVER tabell i public utenom M-04s egne og
    riggens. En UPDATE som ikke endrer antallet fanges også."""
    tabeller = [r[0] for r in sup.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n"
        " ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
        " AND c.relkind IN ('r','p') ORDER BY 1").fetchall()]
    bilde = {}
    for t in tabeller:
        if t in EGNE or t.startswith("fm4_"):
            continue
        n, h = sup.execute(
            f'SELECT count(*), coalesce(sum(hashtext(x::text)::bigint), 0)'
            f' FROM public."{t}" x').fetchone()
        bilde[t] = f"{n}:{h}"
    sup.rollback()
    return bilde


def _katalogbevis(sup) -> dict:
    """D1 (b): ingen rolle utenom migratoren kan skrive registeret, og
    målerens rolle kan kalle nøyaktig én definer-funksjon."""
    skrivere = [r[0] for r in sup.execute(
        "SELECT r.rolname FROM pg_roles r WHERE r.rolcanlogin"
        " AND NOT r.rolsuper AND r.rolname <> 'disponit_migrator'"
        " AND has_table_privilege(r.oid, 'public.retensjonslager',"
        "                         'INSERT,UPDATE,DELETE,TRUNCATE')"
        " ORDER BY 1").fetchall()]
    maaler_skriv = [r[0] for r in sup.execute(
        "SELECT c.relname FROM pg_class c JOIN pg_namespace n"
        " ON n.oid = c.relnamespace WHERE n.nspname = 'public'"
        " AND c.relkind IN ('r','p') AND has_table_privilege(%s, c.oid,"
        " 'INSERT,UPDATE,DELETE,TRUNCATE') ORDER BY 1",
        (MAALER,)).fetchall()]
    definer = [r[0] for r in sup.execute(
        "SELECT p.proname FROM pg_proc p JOIN pg_namespace n"
        " ON n.oid = p.pronamespace WHERE n.nspname = 'public'"
        " AND p.prosecdef AND has_function_privilege(%s, p.oid, 'EXECUTE')"
        " ORDER BY 1", (MAALER,)).fetchall()]
    sup.rollback()
    return {"registerskrivere": skrivere, "maaler_skriverett": maaler_skriv,
            "maaler_definerfunksjoner": definer}


def _revisjonsrader(sup) -> int:
    n = sup.execute("SELECT count(*) FROM revisjonslogg").fetchone()[0]
    sup.rollback()
    return n


def _full_maaling(dsn: str, *, tidsgrense_ms: int | None = None,
                  grense: int = 200) -> dict:
    from db.pg import koble
    from drift import retensjonsmaaling
    c = koble(dsn)
    kw = {} if tidsgrense_ms is None else {"tidsgrense_ms": tidsgrense_ms}
    try:
        r = None
        for _ in range(40):
            r = retensjonsmaaling.kjor(c, grense=grense, **kw)
            if r.ferdig or r.feilet or r.hoppet_over:
                break
        return {"maaling_id": r.maaling_id, "ferdig": r.ferdig,
                "feilet": r.feilet, "hoppet_over": r.hoppet_over,
                "malt": r.malt, "umaalbare": r.umaalbare}
    finally:
        c.close()


def _oppsett(sup, runde: str) -> dict:
    return {"modul": "m04_dataforvalter", "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": m04_bevisrot_sha256()}


# ---------------------------------------------------------------------------
# Punktene.
# ---------------------------------------------------------------------------

def fasit() -> Path:
    sup = psycopg.connect(_env("M04_SUPER_DSN"))
    _er_isolert(sup)
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    maaler = _env("DISPONIT_TEST_LAGERMAALER_DSN")
    runde = secrets.token_hex(4)
    # Basen skal være i ro: en ferdig måling FØR riggen, så første
    # måling i settet starter fra en kjent tilstand.
    _full_maaling(maaler)
    subj = forbered(mg, runde)
    _log(f"rigget runde {runde}: {len(subj)} lagre")

    rev_for, bilde_for = _revisjonsrader(sup), _radbilde(sup)
    m1 = _full_maaling(maaler)
    per1, avvik1 = _dom(sup, subj, 1)
    nye1 = sup.execute(
        "SELECT count(*) FROM retensjonsfunn WHERE lager_id = ANY(%s)"
        " AND oppdaget_maaling = %s",
        (list(subj.values()), m1["maaling_id"])).fetchone()[0]
    sup.rollback()
    _log(f"måling 1: {m1} — {nye1} nye funn, avvik {avvik1}")

    rens(mg, runde)
    m2 = _full_maaling(maaler)
    per2, avvik2 = _dom(sup, subj, 0)
    nye2, lukket2 = sup.execute(
        "SELECT count(*) FILTER (WHERE oppdaget_maaling = %s),"
        "       count(*) FILTER (WHERE lukket_maaling = %s)"
        "  FROM retensjonsfunn WHERE lager_id = ANY(%s)",
        (m2["maaling_id"], m2["maaling_id"],
         list(subj.values()))).fetchone()
    sup.rollback()
    rev_etter, bilde_etter = _revisjonsrader(sup), _radbilde(sup)
    endret = sorted(t for t in set(bilde_for) | set(bilde_etter)
                    if bilde_for.get(t) != bilde_etter.get(t))
    _log(f"måling 2: {m2} — {nye2} nye, {lukket2} lukket, avvik {avvik2}")
    _log(f"revisjonslogg {rev_for} → {rev_etter}; endrede fremmede"
         f" tabeller: {endret or 'ingen'}")
    kat = _katalogbevis(sup)
    _log(f"katalog: {kat}")

    art = {
        "oppsett": {**_oppsett(sup, runde),
                    "subjekter": subj},
        "maalt": {
            "subjekter": len(SETT),
            "funntyper_dekket": sorted({f for _m, f, _f1 in SETT if f}),
            "per_subjekt_forste": per1,
            "per_subjekt": per2,
            "funnavvik_forste": avvik1,
            "funnavvik": avvik2,
            "maaling1_ferdig": bool(m1["ferdig"]),
            "maaling2_ferdig": bool(m2["ferdig"]),
            "maaling1_nye": nye1,
            "maaling2_nye": nye2,
            "maaling2_lukkede": lukket2,
            "revisjonsrader_for": rev_for,
            "revisjonsrader_etter": rev_etter,
            "nye_revisjonsrader": rev_etter - rev_for,
            "fremmede_tabeller_sett": len(bilde_for),
            "endrede_fremmede_tabeller": endret,
            **kat,
        },
    }
    return _skriv("m04-fasit-v1", art)


def feilinjisering() -> Path:
    sup = psycopg.connect(_env("M04_SUPER_DSN"))
    _er_isolert(sup)
    maaler = _env("DISPONIT_TEST_LAGERMAALER_DSN")
    runtime = _env("DISPONIT_TEST_DSN")
    runde = secrets.token_hex(4)
    _full_maaling(maaler)                       # basen i ro

    def register_digest() -> str:
        rader = sup.execute("SELECT r::text FROM retensjonslager r"
                            " ORDER BY lager_id").fetchall()
        sup.rollback()
        return hashlib.sha256("\n".join(r[0] for r in rader)
                              .encode()).hexdigest()

    antall_lagre, umaalbare_for = sup.execute(
        "SELECT (SELECT count(*) FROM retensjonslager),"
        "       (SELECT count(*) FROM retensjonsfunn"
        "         WHERE funntype = 'umaalbar' AND lukket_maaling IS NULL)"
    ).fetchone()
    sup.rollback()

    # (a) TIDSGRENSE UNDER MÅLEKOSTNADEN: hvert lager skal ende som målt
    # ELLER som et `umaalbar`-funn i DENNE målingen — aldri som ingenting.
    reg_for = register_digest()
    from db.pg import koble
    from drift import retensjonsmaaling
    c = koble(maaler)
    mid = None
    try:
        for _ in range(20):
            r = retensjonsmaaling.kjor(c, grense=antall_lagre + 5,
                                       tidsgrense_ms=1)
            mid = r.maaling_id or mid
            if r.ferdig or r.feilet:
                break
            dekket = sup.execute(
                "SELECT (SELECT count(*) FROM retensjonsstorrelse"
                "         WHERE maaling_id = %s)"
                "     + (SELECT count(*) FROM retensjonsfunn"
                "         WHERE sist_sett_maaling = %s"
                "           AND funntype = 'umaalbar'"
                "           AND lukket_maaling IS NULL)", (mid, mid)
            ).fetchone()[0]
            sup.rollback()
            if dekket >= antall_lagre:
                break
    finally:
        c.close()
    malt, umaalbar, beh_uten_storrelse, avbrutt = sup.execute(
        "SELECT (SELECT count(*) FROM retensjonsstorrelse"
        "         WHERE maaling_id = %(m)s),"
        "       (SELECT count(*) FROM retensjonsfunn"
        "         WHERE sist_sett_maaling = %(m)s AND funntype = 'umaalbar'"
        "           AND lukket_maaling IS NULL),"
        "       (SELECT count(*) FROM retensjonsbeholdning b"
        "         WHERE b.maaling_id = %(m)s AND NOT EXISTS ("
        "           SELECT 1 FROM retensjonsstorrelse s"
        "            WHERE s.maaling_id = b.maaling_id"
        "              AND s.lager_id = b.lager_id)),"
        "       (SELECT avbrutt FROM retensjonsmaaling"
        "         WHERE maaling_id = %(m)s)", {"m": mid}).fetchone()
    sup.rollback()
    _log(f"tidsgrense 1 ms: {antall_lagre} lagre → {malt} målt,"
         f" {umaalbar} umålbare, avbrutt={avbrutt}")

    # Gjenoppretting: to fulle målinger (den første lukker den avbrutte,
    # den andre måler alt på nytt og lukker umaalbar-funnene).
    _full_maaling(maaler)
    m_gjen = _full_maaling(maaler)
    umaalbar_etter = sup.execute(
        "SELECT count(*) FROM retensjonsfunn WHERE funntype = 'umaalbar'"
        " AND lukket_maaling IS NULL").fetchone()[0]
    sup.rollback()
    reg_etter_a = register_digest()

    # (b) TILKOBLING UTEN EXECUTE, gjennom den ekte inngangen.
    har_execute = sup.execute(
        "SELECT has_function_privilege('disponit', %s, 'EXECUTE')",
        (MAALEDOR,)).fetchone()[0]
    funn_for_b = sup.execute(
        "SELECT count(*) FROM retensjonsfunn").fetchone()[0]
    sup.rollback()
    linjer = []
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ,
               "DISPONIT_LAGERMAALER_URL": runtime,
               "DISPONIT_MAALETILSTAND": str(Path(d) / "tilstand.json"),
               "PYTHONPATH": f"{REPO / 'platform/core'}:{REPO / 'platform'}"}
        env.pop("CREDENTIALS_DIRECTORY", None)
        for _ in range(2):
            p = subprocess.run(
                [sys.executable, "-m", "drift.kjor_retensjonsmaaling"],
                cwd=REPO / "platform", env=env, capture_output=True,
                text=True, timeout=120)
            linje = json.loads(p.stdout.strip().splitlines()[-1])
            linje["returkode"] = p.returncode
            linjer.append(linje)
    funn_etter_b = sup.execute(
        "SELECT count(*) FROM retensjonsfunn").fetchone()[0]
    sup.rollback()
    reg_etter = register_digest()
    _log(f"uten EXECUTE: {[(x['feilet'], x['alarm']) for x in linjer]}")

    art = {
        "oppsett": {**_oppsett(sup, runde),
                    "injeksjoner": [
                        "statement_timeout = 1 ms per lager",
                        "tilkobling som disponit — uten EXECUTE på "
                        + MAALEDOR]},
        "maalt": {
            "antall_lagre": antall_lagre,
            "tidsgrense_maalt": malt,
            "tidsgrense_umaalbare": umaalbar,
            "tidsgrense_verken_eller": antall_lagre - malt - umaalbar,
            "tidsgrense_beholdning_uten_maaling": beh_uten_storrelse,
            "tidsgrense_maaling_avbrutt": bool(avbrutt),
            "gjenoppretting_ferdig": bool(m_gjen["ferdig"]),
            "umaalbare_for_injeksjon": umaalbare_for,
            "umaalbare_etter_gjenoppretting": umaalbar_etter,
            "injeksjonsrolle_har_execute": bool(har_execute),
            "uten_execute_feilet": [bool(x["feilet"]) for x in linjer],
            "uten_execute_alarm": [bool(x["alarm"]) for x in linjer],
            "uten_execute_returkoder": [x["returkode"] for x in linjer],
            "funn_for_uten_execute": funn_for_b,
            "funn_etter_uten_execute": funn_etter_b,
            "registeret_urort": reg_for == reg_etter_a == reg_etter,
        },
    }
    return _skriv("m04-feilinjisering-v1", art)


def _kjor_kode(rot: Path, dsn: str) -> dict:
    """Én HEL måling med koden i `rot` — forgjengerens eller kandidatens."""
    prog = (
        "import json\n"
        "from db.pg import koble\n"
        "from drift import retensjonsmaaling as rm\n"
        f"c = koble({dsn!r})\n"
        "r = None\n"
        "for _ in range(40):\n"
        "    r = rm.kjor(c, grense=200)\n"
        "    if r.ferdig or r.feilet or r.hoppet_over: break\n"
        "print(json.dumps({'ferdig': r.ferdig, 'feilet': r.feilet,"
        " 'malt': r.malt, 'umaalbare': r.umaalbare,"
        " 'maaling_id': r.maaling_id}))\n")
    env = {**os.environ,
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}"}
    p = subprocess.run([sys.executable, "-c", prog], cwd=rot / "platform",
                       env=env, capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        return {"ferdig": False, "feilet": True, "stderr": p.stderr[-400:]}
    return json.loads(p.stdout.strip().splitlines()[-1])


def rollback(forgjenger: Path) -> Path:
    sup = psycopg.connect(_env("M04_SUPER_DSN"))
    _er_isolert(sup)
    sup.autocommit = True
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    maaler = _env("DISPONIT_TEST_LAGERMAALER_DSN")
    runde = secrets.token_hex(4)
    _full_maaling(maaler)                       # basen i ro

    def apne() -> list[str]:
        return [f"{a}|{b}|{c}" for a, b, c in sup.execute(
            "SELECT lager_id, funntype, tenant FROM retensjonsfunn"
            " WHERE lukket_maaling IS NULL ORDER BY 1, 2, 3").fetchall()]

    def dubletter() -> int:
        return sup.execute(
            "SELECT count(*) FROM (SELECT 1 FROM retensjonsfunn"
            " WHERE lukket_maaling IS NULL GROUP BY lager_id, funntype,"
            " tenant HAVING count(*) > 1) d").fetchone()[0]

    # DRILLEN. Først åpnes en måling med ETT lager, så målingsraden
    # finnes og er committet — ellers ville den ruller tilbake sammen med
    # setningen som blir drept, og det ville ikke finnes noe å lese
    # `avbrutt` av. Så et aldri målt lager, som kommer først i
    # rekkefølgen, låses eksklusivt: kjøringen blir stående INNE i
    # lageret når den drepes.
    from db.pg import koble
    from drift import retensjonsmaaling
    c = koble(maaler)
    try:
        drept_maaling = retensjonsmaaling.kjor(c, grense=1).maaling_id
    finally:
        c.close()
    laas = f"fm4_{runde}_laas"
    _tabell(mg, laas)
    _registrer(mg, laas)
    holder = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    holder.execute(f"LOCK TABLE {laas} IN ACCESS EXCLUSIVE MODE")
    env = {**os.environ,
           "DISPONIT_LAGERMAALER_URL": maaler,
           "DISPONIT_MAALETILSTAND": str(Path(tempfile.mkdtemp())
                                         / "t.json"),
           "PYTHONPATH": f"{REPO / 'platform/core'}:{REPO / 'platform'}"}
    env.pop("CREDENTIALS_DIRECTORY", None)
    proc = subprocess.Popen(
        [sys.executable, "-m", "drift.kjor_retensjonsmaaling"],
        cwd=REPO / "platform", env=env, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)
    ventet = False
    for _ in range(100):
        ventet = sup.execute(
            "SELECT EXISTS (SELECT 1 FROM pg_locks l JOIN pg_class c"
            " ON c.oid = l.relation WHERE c.relname = %s AND NOT l.granted)",
            (laas,)).fetchone()[0]
        if ventet:
            break
        time.sleep(0.1)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=30)
    backends = [r[0] for r in sup.execute(
        "SELECT pid FROM pg_stat_activity WHERE usename = %s",
        (MAALER,)).fetchall()]
    avsluttet = all(sup.execute("SELECT pg_terminate_backend(%s)", (b,))
                    .fetchone()[0] for b in backends)
    holder.rollback()
    holder.close()
    time.sleep(1)
    drept_avbrutt, drept_fullfort = sup.execute(
        "SELECT avbrutt, fullfort_ts IS NOT NULL FROM retensjonsmaaling"
        " WHERE maaling_id = %s", (drept_maaling,)).fetchone()
    nokkel_fri = False
    c = koble(maaler)
    try:
        nokkel_fri = c.execute("SELECT pg_try_advisory_lock(%s)",
                               (retensjonsmaaling.ARBEIDERNOKKEL,)
                               ).fetchone()[0]
        if nokkel_fri:
            c.execute("SELECT pg_advisory_unlock(%s)",
                      (retensjonsmaaling.ARBEIDERNOKKEL,))
        c.commit()
    finally:
        c.close()
    _log(f"drill: ventet={ventet} backends={len(backends)}"
         f" avsluttet={avsluttet} avbrutt={drept_avbrutt}"
         f" nøkkel fri={nokkel_fri}")

    rullbakk = _kjor_kode(forgjenger, maaler)
    apne_rb, dubl_rb = apne(), dubletter()
    kandidat = _kjor_kode(REPO, maaler)
    apne_k, dubl_k = apne(), dubletter()
    _log(f"rullbakk {rullbakk} — {len(apne_rb)} åpne, {dubl_rb} dubletter")
    _log(f"kandidat {kandidat} — {len(apne_k)} åpne, {dubl_k} dubletter")

    art = {
        "oppsett": {**_oppsett(sup, runde),
                    "forgjenger": str(forgjenger),
                    "kjerne_forgjenger": kjerne_digest(forgjenger),
                    "kjerne_kandidat": kjerne_digest(REPO),
                    "modul_forgjenger": release_digest(forgjenger,
                                                       M04_MODULFILER),
                    "modul_kandidat": release_digest(REPO, M04_MODULFILER)},
        "maalt": {
            "drill_ventet_i_lageret": bool(ventet),
            "drill_backends_avsluttet": bool(avsluttet and backends),
            "drept_maaling_avbrutt": bool(drept_avbrutt),
            "drept_maaling_fullfort": bool(drept_fullfort),
            "drept_maaling": drept_maaling,
            "arbeidernokkel_fri": bool(nokkel_fri),
            "rullbakk_ferdig": bool(rullbakk.get("ferdig")),
            "rullbakk_feilet": bool(rullbakk.get("feilet")),
            "kandidat_ferdig": bool(kandidat.get("ferdig")),
            "kandidat_feilet": bool(kandidat.get("feilet")),
            "apne_etter_rullbakk": len(apne_rb),
            "apne_etter_kandidat": len(apne_k),
            "apne_like": apne_rb == apne_k,
            "dubletter": dubl_rb + dubl_k,
        },
    }
    return _skriv("m04-rollback-v1", art)


def ytelse(dsn_fil: Path, runder: int) -> Path:
    """Den ekte jobben mot driftsbasen. Riggen rører ingenting her."""
    dsn = dsn_fil.read_text(encoding="utf-8").strip()
    with psycopg.connect(dsn) as c:
        db = c.execute("SELECT current_database()").fetchone()[0]
    kjoringer = []
    with tempfile.TemporaryDirectory() as d:
        env = {**os.environ,
               "DISPONIT_LAGERMAALER_URL": dsn,
               "DISPONIT_MAALETILSTAND": str(Path(d) / "t.json"),
               "PYTHONPATH": f"{REPO / 'platform/core'}:{REPO / 'platform'}"}
        env.pop("CREDENTIALS_DIRECTORY", None)
        for i in range(runder):
            t0 = time.monotonic()
            p = subprocess.run(
                [sys.executable, "-m", "drift.kjor_retensjonsmaaling"],
                cwd=REPO / "platform", env=env, capture_output=True,
                text=True, timeout=900)
            sek = time.monotonic() - t0
            linje = json.loads(p.stdout.strip().splitlines()[-1])
            kjoringer.append({"sekunder": round(sek, 3),
                              "malt": linje["malt"],
                              "umaalbare": linje["umaalbare"],
                              "ferdig": bool(linje["ferdig"]),
                              "feilet": bool(linje["feilet"]),
                              "hoppet_over": bool(linje["hoppet_over"]),
                              "returkode": p.returncode})
            _log(f"kjøring {i + 1}: {kjoringer[-1]}")
    malt = sum(k["malt"] for k in kjoringer)
    art = {
        "oppsett": {"modul": "m04_dataforvalter",
                    "vert": socket.gethostname(), "base": db,
                    "runder": runder,
                    "bevisrot_sha256": m04_bevisrot_sha256()},
        "maalt": {
            "kjoringer": kjoringer,
            "maks_sekunder": max(k["sekunder"] for k in kjoringer),
            "lagre_maalt": malt,
            "umaalbare": sum(k["umaalbare"] for k in kjoringer),
            "feilede": sum(1 for k in kjoringer if k["feilet"]),
            "sekunder_per_lager": round(
                sum(k["sekunder"] for k in kjoringer) / max(malt, 1), 4),
        },
    }
    return _skriv("m04-ytelse-v1", art)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("punkt", choices=("fasit", "feilinjisering", "rollback",
                                      "ytelse"))
    ap.add_argument("--forgjenger", type=Path)
    ap.add_argument("--dsn-fil", type=Path)
    ap.add_argument("--runder", type=int, default=3)
    a = ap.parse_args()
    if a.punkt == "fasit":
        sti = fasit()
    elif a.punkt == "feilinjisering":
        sti = feilinjisering()
    elif a.punkt == "rollback":
        if not a.forgjenger:
            sys.exit("--forgjenger mangler")
        sti = rollback(a.forgjenger)
    else:
        if not a.dsn_fil:
            sys.exit("--dsn-fil mangler")
        sti = ytelse(a.dsn_fil, a.runder)
    return 0 if json.loads(sti.read_text())["bestatt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
