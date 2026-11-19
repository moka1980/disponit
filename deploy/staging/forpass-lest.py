#!/usr/bin/env python3
"""Sertifiseringslesten for klasse E i ADR-003 (forpass i varselsenderen).

M-21 (fristvarsler) og M-22 (utløpsvarsler) har ingen egen sveip, ingen
funn og ingen egen timer. De har tenantdører som skriver evidens, og ett
FORPASS som `platform/drift/varselsender.py` kjører før den ordinære
sendingen: et idempotent anker per (subjekt, varslingspunkt, forekomst),
skrevet i samme transaksjon som varselet det svarer til.

Alle fire punktene måles i den ISOLERTE basen på 55432, bygget fra
commiten som måles. Forpasset kjøres gjennom den EKTE senderen
(`varselsender.kjor`) som `disponit_varselsender`, med en oppsamlende
`send` i stedet for SMTP — ingen e-post forlater verten.

  fasit           syntetisk_datasett_likt_lokalt + revisjonslogg_korrekt.
                  Et KJENT subjektsett i to tenanter med kjent antall
                  ankre per subjekt etter første kjøring, re-regnet av
                  ankrene. Hvert anker peker på et varsel til subjektets
                  eier, med riktig e-poststatus for eierens kanal. Mellom
                  kjøringene kvitteres/fornyes ett subjekt; andre kjøring
                  skal ikke køe noe nytt. Evidensen telles per handling,
                  med aktør og egen input_hash.
  feilinjisering  to ekte driftsfeil i forpasset (tilbaketrukket EXECUTE
                  og et låst register): den ordinære sendingen går som
                  før, feilen telles separat, ingen egne ankre skrives,
                  og inngangspunktet avslutter med kode ≠ 0. Etter
                  reparasjonen går forpasset rent igjen.
  rollback        forgjengerens og kandidatens sender på hvert sitt like
                  sett: like ankre per subjekt. Kandidaten etter
                  forgjengeren køer ingenting nytt — ingen dobbeltvarsel
                  over en rulling.
  ytelse          forpasset alene med et fullt tak forfalne subjekter,
                  målt mot en pinnet grense.

BRUK (på verten, som administrator, med riggens DSN-er eksportert):
  python deploy/staging/forpass-lest.py --modul m21_avtalefrist fasit
  python deploy/staging/forpass-lest.py --modul m22_lisens rollback \
      --forgjenger /opt/disponit/releases/<sha>
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "platform/core"))
sys.path.insert(0, str(REPO / "platform"))

import psycopg  # noqa: E402

from manifestskjema import (FORPASSMODULER, _sjekk_grenser,  # noqa: E402
                            forpass_bevisrot_sha256, kjerne_digest,
                            release_digest, valider_artefaktformat)

UT = REPO / "deploy/staging/artefakter"
AKTOR = "forpass-fasit"


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
    art["bestatt"] = True
    art["feil"] = sorted(set(art["feil"]
                             + valider_artefaktformat(art, krav)
                             + _sjekk_grenser(krav, art)))
    art["bestatt"] = not art["feil"]
    UT.mkdir(parents=True, exist_ok=True)
    sti = UT / f"{krav}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    sti.write_text(json.dumps(art, indent=2, ensure_ascii=False,
                              sort_keys=True) + "\n", encoding="utf-8")
    _log(f"skrev {sti} (bestatt={art['bestatt']})")
    for f in art["feil"]:
        _log("  FEIL:", f)
    return sti


def _er_isolert(sup) -> None:
    db, port = sup.execute(
        "SELECT current_database(), current_setting('port')").fetchone()
    sup.rollback()
    if db != "disponit_test" or port != "55432":
        sys.exit(f"NEKTET: kjører bare i disponit_test på 55432"
                 f" (fikk {db}:{port})")


def _kontekst(c, tenant: str) -> None:
    c.execute("SELECT set_config('disponit.tenant', %s, true),"
              " set_config('disponit.aktor', %s, true),"
              " set_config('disponit.request_id', 'forpass-lest', true)",
              (tenant, AKTOR))


# ---------------------------------------------------------------------------
# SETTENE. Ett per modul: subjekt → (dørkall, ventet antall ankre etter
# første kjøring, handling mellom kjøringene).
# ---------------------------------------------------------------------------

#: M-21. `frist` er et SQL-uttrykk relativt til nå; standardpunktene er
#: 30/7/1 døgn. «Nådd» betyr frist − punkt ≤ nå.
M21_SETT = {
    "a": [
        # merke, frist, gjentakelse, punkter, før kjøring 1, ventet
        ("p30", "now() + interval '20 days'", "engang", None, None, 1),
        ("p7", "now() + interval '5 days'", "engang", None, None, 2),
        ("forfalt", "now() - interval '1 day'", "engang", None, None, 3),
        ("fjern", "now() + interval '60 days'", "engang", None, None, 0),
        ("lukket", "now() + interval '5 days'", "engang", None, "lukk", 0),
        ("bortfalt", "now() + interval '5 days'", "engang", None,
         "bortfall", 0),
        # Kvitteres MELLOM kjøringene: fristen rulles en måned fram, og
        # den nye forekomstens nærmeste punkt (30) er ennå ikke nådd.
        ("rull", "now() + interval '3 days'", "manedlig", None, None, 2),
    ],
    "b": [
        ("egne", "now() + interval '5 days'", "engang", [10, 2], None, 1),
    ],
}
M21_MELLOM = "rull"

#: M-22. Punktene måles mot BESLUTNINGSDATOEN (fornyelse − oppsigelses-
#: frist); standardpunktene er 60/30/7 døgn.
M22_SETT = {
    "a": [
        # merke, fornyelse om (døgn), oppsigelsesfrist, punkter, før, ventet
        ("b40", 70, 30, None, None, 1),
        ("b20", 50, 30, None, None, 2),
        ("passert", 10, 30, None, None, 3),
        ("fjern", 200, 30, None, None, 0),
        ("avsluttet", 50, 30, None, "avslutt", 0),
        # Fornyes ett år MELLOM kjøringene: ny forekomst langt fram.
        ("fornyet", 50, 30, None, None, 2),
    ],
    "b": [
        ("egne", 40, 0, [45, 5], None, 1),
    ],
}
M22_MELLOM = "fornyet"


def _sett(modul: str) -> dict:
    return M21_SETT if modul == "m21_avtalefrist" else M22_SETT


def _ventet(modul: str) -> dict[str, int]:
    return {r[0]: r[-1] for rader in _sett(modul).values() for r in rader}


def _ventet_evidens(modul: str) -> dict[str, dict[str, int]]:
    k = FORPASSMODULER[modul]
    ut: dict[str, dict[str, int]] = {}
    for tkey, rader in _sett(modul).items():
        per: dict[str, int] = {k["h_registrert"]: len(rader)}
        for r in rader:
            if r[4]:
                h = k["h_for"][r[4]]
                per[h] = per.get(h, 0) + 1
        if any(r[0] == k["mellom"] for r in rader):
            per[k["h_mellom"]] = per.get(k["h_mellom"], 0) + 1
        koet = sum(r[-1] for r in rader)
        if koet:
            per[k["h_koet"]] = koet
        ut[tkey] = per
    return ut


def _bruker(mg, tenant: str, epost: str, kanal: str) -> str:
    """Eieren: identitet, aktivt medlemskap og kanalvalg."""
    _kontekst(mg, tenant)
    bid = mg.execute(
        "INSERT INTO brukeridentitet (issuer, sub, profil)"
        " VALUES ('https://forpass.fasit', %s, %s::jsonb) RETURNING bruker_id",
        (f"{tenant}:eier", json.dumps({"epost": epost,
                                       "epost_verifisert": True}))
    ).fetchone()[0]
    mg.execute("INSERT INTO brukermedlemskap (tenant, bruker_id, roller,"
               " aktiv) VALUES (%s,%s, ARRAY['admin'], true)", (tenant, bid))
    mg.execute("INSERT INTO varselvalg (tenant, bruker_id, kanal)"
               " VALUES (%s,%s,%s)", (tenant, bid, kanal))
    mg.commit()
    return bid


def _m21_registrer(rt, tenant, sid, eier, merke, frist, gjent, punkter):
    _kontekst(rt, tenant)
    rt.execute("SELECT m21_registrer_plikt(%s,%s,%s,%s,%s," + frist
               + ",%s,%s,%s)",
               (tenant, sid, f"Fasitplikt {merke}", eier,
                f"Fasitavtale {merke} par. 3", gjent, punkter, AKTOR))
    rt.commit()


def _m22_registrer(rt, tenant, sid, eier, merke, om, frist, punkter):
    _kontekst(rt, tenant)
    rt.execute("SELECT m22_registrer_lisens(%s,%s,%s,%s,%s,5,1200.00,'NOK',"
               " current_date + %s, 'automatisk', %s, %s, %s, %s)",
               (tenant, sid, f"Fasitleverandor {merke}", f"Produkt {merke}",
                eier, om, frist, f"Fasitavtale {merke}", punkter, AKTOR))
    rt.commit()


def _handling(modul: str, rt, tenant: str, sid: str, hva: str) -> None:
    _kontekst(rt, tenant)
    if hva == "lukk":
        rt.execute("SELECT m21_lukk_plikt(%s,%s,%s,%s)",
                   (tenant, sid, f"KV-{sid[:8]}", AKTOR))
    elif hva == "bortfall":
        rt.execute("SELECT m21_marker_bortfalt(%s,%s,%s,%s)",
                   (tenant, sid, "Avtalen er sagt opp av begge parter",
                    AKTOR))
    elif hva == "avslutt":
        rt.execute("SELECT m22_marker_avsluttet(%s,%s,%s,%s)",
                   (tenant, sid, "Lisensen er sagt opp og ikke fornyet",
                    AKTOR))
    elif hva == "forny":
        # Runtime har ingen SELECT på registeret: datoen regnes av settet.
        om = next(r[1] for rader in M22_SETT.values() for r in rader
                  if r[0] == M22_MELLOM)
        rt.execute("SELECT m22_registrer_fornyelse(%s,%s,"
                   " current_date + %s, %s)",
                   (tenant, sid, om + 365, AKTOR))
    else:
        raise ValueError(hva)
    rt.commit()


def forbered(modul: str, rt, mg, runde: str, *, tenantmerke: str = "") -> dict:
    """Begge tenantene — uten handlingen MELLOM kjøringene."""
    k = FORPASSMODULER[modul]
    rigg: dict = {"tenanter": {}, "eiere": {}, "epost": {}, "subjekter": {}}
    for tkey, rader in _sett(modul).items():
        tenant = f"t-{k['kort']}fasit-{tkey}{tenantmerke}-{runde}"
        epost = f"{k['kort']}-{tkey}-{runde}{tenantmerke}@forpass.test"
        # Tenant a vil bare ha varsler i portalen; b vil ha e-post.
        kanal = "kun_portal" if tkey == "a" else "epost_og_portal"
        eier = _bruker(mg, tenant, epost, kanal)
        rigg["tenanter"][tkey], rigg["eiere"][tkey] = tenant, eier
        rigg["epost"][tkey] = epost
        for rad in rader:
            merke, sid = rad[0], str(uuid.uuid4())
            if modul == "m21_avtalefrist":
                _m21_registrer(rt, tenant, sid, eier, merke, *rad[1:4])
            else:
                _m22_registrer(rt, tenant, sid, eier, merke, *rad[1:4])
            if rad[4]:
                _handling(modul, rt, tenant, sid, rad[4])
            rigg["subjekter"][merke] = {"tenant": tkey, "id": sid}
    return rigg


def mellom(modul: str, rt, rigg: dict) -> None:
    k = FORPASSMODULER[modul]
    s = rigg["subjekter"][k["mellom"]]
    _handling(modul, rt, rigg["tenanter"][s["tenant"]], s["id"],
              "lukk" if modul == "m21_avtalefrist" else "forny")


# ---------------------------------------------------------------------------
# Målingene — alltid re-regnet av radene, med superbrukeren (RLS er FORCE).
# ---------------------------------------------------------------------------

def _ankre(sup, modul: str, rigg: dict) -> dict[str, int]:
    k = FORPASSMODULER[modul]
    ut = {}
    for merke, s in rigg["subjekter"].items():
        ut[merke] = sup.execute(
            f"SELECT count(*) FROM {k['anker']} WHERE tenant=%s"
            f" AND {k['subjekt']}=%s",
            (rigg["tenanter"][s["tenant"]], s["id"])).fetchone()[0]
    sup.rollback()
    return ut


def _varselavvik(sup, modul: str, rigg: dict) -> int:
    """Hvert anker skal peke på ETT varsel til subjektets eier, av modulens
    art, med e-poststatus etter eierens kanal."""
    k = FORPASSMODULER[modul]
    avvik = 0
    for tkey, tenant in rigg["tenanter"].items():
        rader = sup.execute(
            f"SELECT v.bruker_id, v.art, v.ressurs_id, v.epost_status,"
            f"       a.{k['subjekt']}::text"
            f"  FROM {k['anker']} a LEFT JOIN varsel v ON v.id = a.varsel_ref"
            f" WHERE a.tenant=%s", (tenant,)).fetchall()
        for bid, art, ressurs, status, sid in rader:
            ok = (bid == rigg["eiere"][tkey] and art == k["art"]
                  and ressurs == sid)
            # a = kun_portal → ingen e-post; b = e-post → køet (eller alt
            # sendt av samme kjøring).
            if tkey == "a":
                ok = ok and status == "ikke_aktuelt"
            else:
                ok = ok and status in ("koet", "sendt")
            avvik += 0 if ok else 1
    sup.rollback()
    return avvik


def _evidens(sup, modul: str, rigg: dict) -> dict:
    k = FORPASSMODULER[modul]
    per_t: dict[str, dict[str, int]] = {}
    totalt = uten = 0
    hasher: list[str] = []
    for tkey, tenant in rigg["tenanter"].items():
        rader = sup.execute(
            "SELECT handling, aktor, input_hash FROM revisjonslogg"
            " WHERE tenant=%s AND kilde=%s",
            (tenant, k["evidenskilde"])).fetchall()
        per: dict[str, int] = {}
        for h, a, i in rader:
            per[h] = per.get(h, 0) + 1
            uten += 0 if a in (AKTOR, k["sveipaktor"]) else 1
            hasher.append(i)
        per_t[tkey] = per
        totalt += len(rader)
    sup.rollback()
    return {"per_tenant": per_t, "totalt": totalt, "uten_aktor": uten,
            "delte_input_hash": len(hasher) - len(set(hasher))}


class _Postkasse:
    """`send` for senderen: samler i stedet for å sende."""

    def __init__(self):
        self.til: list[str] = []

    def __call__(self, til, emne, tekst):
        self.til.append(til)


def _sender(dsn: str):
    from db.pg import koble
    return koble(dsn)


def _kjor(dsn: str) -> tuple[dict, _Postkasse, str]:
    """Én ekte senderkjøring som `disponit_varselsender`."""
    from drift import varselsender
    post = _Postkasse()
    feil = io.StringIO()
    conn = _sender(dsn)
    try:
        with contextlib.redirect_stderr(feil):
            res = varselsender.kjor(conn, send=post)
    finally:
        conn.close()
    return res, post, feil.getvalue()


def _oppsett(modul: str, runde: str) -> dict:
    return {"modul": modul, "vert": socket.gethostname(),
            "base": "disponit_test@55432", "runde": runde,
            "bevisrot_sha256": forpass_bevisrot_sha256()}


# ---------------------------------------------------------------------------

def fasit(modul: str) -> Path:
    sup = psycopg.connect(_env("FORPASS_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    vdsn = _env("DISPONIT_TEST_VARSEL_DSN")
    runde = secrets.token_hex(4)
    rigg = forbered(modul, rt, mg, runde)
    _log(f"rigget {modul} runde {runde}: {rigg['tenanter']}")

    res1, post1, err1 = _kjor(vdsn)
    ankre1 = _ankre(sup, modul, rigg)
    varselavvik = _varselavvik(sup, modul, rigg)
    ventet = _ventet(modul)
    per = [{"merke": m, "ventet": ventet[m], "fikk": ankre1.get(m)}
           for m in ventet]
    avvik1 = sum(1 for r in per if r["ventet"] != r["fikk"])

    mellom(modul, rt, rigg)
    res2, post2, err2 = _kjor(vdsn)
    ankre2 = _ankre(sup, modul, rigg)
    nye2 = sum(ankre2.values()) - sum(ankre1.values())

    ev = _evidens(sup, modul, rigg)
    ev_vent = _ventet_evidens(modul)
    ev_avvik = sum(1 for t in ev_vent if ev["per_tenant"].get(t) != ev_vent[t])
    sendt = {t: post1.til.count(rigg["epost"][t]) + post2.til.count(
        rigg["epost"][t]) for t in rigg["tenanter"]}
    sendt_ventet = {"a": 0,
                    "b": sum(r[-1] for r in _sett(modul)["b"])}
    _log(f"ankre1 {ankre1}; nye i kjøring 2: {nye2}; varselavvik"
         f" {varselavvik}; e-post {sendt}; evidens {ev}")
    art = {
        "oppsett": {**_oppsett(modul, runde),
                    "tenanter": rigg["tenanter"]},
        "maalt": {
            "per_subjekt": per,
            "subjekter": len(per),
            "ankeravvik": avvik1,
            "ankre_kjoring1": sum(ankre1.values()),
            "kjoring2_nye": nye2,
            "varselavvik": varselavvik,
            "epost_sendt": sendt,
            "epost_ventet": sendt_ventet,
            "forpass_feil": [res1.get("forpass_feil"),
                             res2.get("forpass_feil")],
            "forpass_stderr": (err1 + err2)[-2000:],
            "evidens_per_tenant": ev["per_tenant"],
            "evidens_ventet": ev_vent,
            "evidens_avvik": ev_avvik,
            "evidens_totalt": ev["totalt"],
            "evidens_uten_aktor": ev["uten_aktor"],
            "evidens_delte_input_hash": ev["delte_input_hash"],
        },
    }
    return _skriv(f"{FORPASSMODULER[modul]['kort']}-fasit-v1", art)


def _ordinaert_varsel(mg, tenant: str, bid: str) -> None:
    """Et ORDINÆRT varsel i køen — ikke modulens. Sendingen av det som
    alt ligger i køen skal være upåvirket av at et forpass faller."""
    _kontekst(mg, tenant)
    mg.execute(
        "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type,"
        " ressurs_id, hendelse, tekstnokkel, parametre, epost_status)"
        " VALUES (%s,%s,'attestering_venter','policyutkast',%s,'r1',"
        "         'varsel.attestering_venter','{}'::jsonb,'koet')",
        (tenant, bid, "u-" + secrets.token_hex(4)))
    mg.commit()


INNGANG = r"""
import sys
from drift import kjor_varselsender
sys.exit(kjor_varselsender.main())
"""


def _inngang(dsn: str) -> tuple[int, str]:
    """Inngangspunktet unitten starter — returkoden er det systemd ser."""
    env = {**os.environ, "DISPONIT_DATABASE_URL": dsn,
           "PYTHONPATH": f"{REPO / 'platform/core'}:{REPO / 'platform'}"}
    for k in ("CREDENTIALS_DIRECTORY", "DISPONIT_SMTP_VERT"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", INNGANG],
                       cwd=REPO / "platform", env=env, capture_output=True,
                       text=True, timeout=300)
    return p.returncode, (p.stdout + p.stderr)[-800:]


def _med_laas(dsn: str, ms: int) -> str:
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}options=-c%20lock_timeout%3D{ms}"


def feilinjisering(modul: str) -> Path:
    k = FORPASSMODULER[modul]
    sup = psycopg.connect(_env("FORPASS_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    vdsn = _env("DISPONIT_TEST_VARSEL_DSN")
    runde = secrets.token_hex(4)
    signatur = f"{k['forpass']}(integer)"

    def trekk(c):
        c.execute(f"SET ROLE {k['eier']}")
        c.execute(f"REVOKE EXECUTE ON FUNCTION {signatur}"
                  " FROM disponit_varselsender")
        c.execute("RESET ROLE")
        c.commit()

    def gi(c):
        c.execute(f"SET ROLE {k['eier']}")
        c.execute(f"GRANT EXECUTE ON FUNCTION {signatur}"
                  " TO disponit_varselsender")
        c.execute("RESET ROLE")
        c.commit()

    ut = []
    for navn in ("execute_trukket", "register_laast"):
        rigg = forbered(modul, rt, mg, runde, tenantmerke=navn[:3])
        tb = rigg["tenanter"]["b"]
        _ordinaert_varsel(mg, tb, rigg["eiere"]["b"])
        laas = None
        dsn_under = vdsn
        if navn == "execute_trukket":
            trekk(mg)
        else:
            laas = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
            laas.execute(f"LOCK TABLE {k['register']} IN ACCESS EXCLUSIVE"
                         " MODE")
            dsn_under = _med_laas(vdsn, 1500)
        try:
            res, post, err = _kjor(dsn_under)
            ankre_under = sum(_ankre(sup, modul, rigg).values())
            kode_under, logg_under = _inngang(dsn_under)
        finally:
            if laas is not None:
                laas.rollback()
                laas.close()
            else:
                gi(mg)
        res_e, _post_e, _err_e = _kjor(vdsn)
        ankre_etter = sum(_ankre(sup, modul, rigg).values())
        kode_etter, _logg_etter = _inngang(vdsn)
        ut.append({
            "injeksjon": navn,
            "forpass_feil_under": res.get("forpass_feil"),
            "ordinaer_sendt": post.til.count(rigg["epost"]["b"]) >= 1,
            "egne_ankre_under": ankre_under,
            "feilmelding": err[-400:],
            "returkode_under": kode_under,
            "journal_under": logg_under[-400:],
            "forpass_feil_etter": res_e.get("forpass_feil"),
            "egne_ankre_etter": ankre_etter,
            "returkode_etter": kode_etter,
        })
        _log(f"{navn}: {ut[-1]}")
    art = {"oppsett": _oppsett(modul, runde),
           "maalt": {"injeksjoner": ut, "injiserte": len(ut),
                     "ventet_ankre": sum(_ventet(modul).values())}}
    return _skriv(f"{k['kort']}-feilinjisering-v1", art)


KALLER = r"""
import json, sys
from db.pg import koble
from drift import varselsender
til = []
conn = koble(sys.argv[1])
res = varselsender.kjor(conn, send=lambda t, e, x: til.append(t))
conn.close()
print(json.dumps({"res": res, "til": til}, sort_keys=True, default=str))
"""


def _kjor_i(rot: Path, dsn: str) -> dict:
    env = {**os.environ,
           "PYTHONPATH": f"{rot / 'platform/core'}:{rot / 'platform'}"}
    for k in ("CREDENTIALS_DIRECTORY", "DISPONIT_SMTP_VERT"):
        env.pop(k, None)
    p = subprocess.run([sys.executable, "-c", KALLER, dsn],
                       cwd=rot / "platform", env=env, capture_output=True,
                       text=True, timeout=300)
    if p.returncode != 0:
        sys.exit(f"senderen i {rot} feilet:\n{p.stderr[-2000:]}")
    return json.loads(p.stdout.strip().splitlines()[-1])


def rollback(modul: str, forgjenger: Path) -> Path:
    k = FORPASSMODULER[modul]
    sup = psycopg.connect(_env("FORPASS_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    vdsn = _env("DISPONIT_TEST_VARSEL_DSN")
    runde = secrets.token_hex(4)

    r1 = forbered(modul, rt, mg, runde, tenantmerke="rb")
    rb = _kjor_i(forgjenger, vdsn)
    ankre_rb = _ankre(sup, modul, r1)
    kd_etter = _kjor_i(REPO, vdsn)
    nye_etter = sum(_ankre(sup, modul, r1).values()) - sum(ankre_rb.values())
    r2 = forbered(modul, rt, mg, runde, tenantmerke="kd")
    kd = _kjor_i(REPO, vdsn)
    ankre_kd = _ankre(sup, modul, r2)
    art = {
        "oppsett": {**_oppsett(modul, runde), "forgjenger": str(forgjenger),
                    "kjerne_forgjenger": kjerne_digest(forgjenger),
                    "kjerne_kandidat": kjerne_digest(REPO),
                    "modul_forgjenger": release_digest(forgjenger,
                                                       k["modulfiler"]),
                    "modul_kandidat": release_digest(REPO, k["modulfiler"])},
        "maalt": {
            "ankre_forgjenger": ankre_rb,
            "ankre_kandidat": ankre_kd,
            "ankre_like": ankre_rb == ankre_kd,
            "ankre_lik_fasit": ankre_kd == _ventet(modul),
            "kandidat_etter_rullbakk_nye": nye_etter,
            "forpass_feil": [rb["res"].get("forpass_feil"),
                             kd_etter["res"].get("forpass_feil"),
                             kd["res"].get("forpass_feil")],
        },
    }
    _log(f"forgjenger {ankre_rb}; kandidat {ankre_kd}; nye etter rulling"
         f" {nye_etter}")
    return _skriv(f"{k['kort']}-rollback-v1", art)


def ytelse(modul: str, kjoringer: int, antall: int) -> Path:
    """Forpasset ALENE, som senderens rolle, med `antall` forfalne
    subjekter (ett punkt hver) per kjøring — et fullt tak."""
    k = FORPASSMODULER[modul]
    sup = psycopg.connect(_env("FORPASS_SUPER_DSN"))
    _er_isolert(sup)
    rt = psycopg.connect(_env("DISPONIT_TEST_DSN"))
    mg = psycopg.connect(_env("DISPONIT_TEST_MIGRATOR_DSN"))
    runde = secrets.token_hex(4)
    ut = []
    for i in range(kjoringer):
        tenant = f"t-{k['kort']}ytelse-{i}-{runde}"
        eier = _bruker(mg, tenant, f"y{i}-{runde}@forpass.test",
                       "kun_portal")
        for j in range(antall):
            sid = str(uuid.uuid4())
            if modul == "m21_avtalefrist":
                _m21_registrer(rt, tenant, sid, eier, f"y{j}",
                               "now() + interval '5 days'", "engang", [10])
            else:
                _m22_registrer(rt, tenant, sid, eier, f"y{j}", 40, 0, [45])
        v = _sender(_env("DISPONIT_TEST_VARSEL_DSN"))
        t0 = time.monotonic()
        try:
            n = v.execute(f"SELECT {k['forpass']}(%s)",
                          (antall,)).fetchone()[0]
            v.commit()
            feilet = False
        except psycopg.Error:
            v.rollback()
            n, feilet = 0, True
        sek = time.monotonic() - t0
        v.close()
        egne = sup.execute(f"SELECT count(*) FROM {k['anker']}"
                           " WHERE tenant=%s", (tenant,)).fetchone()[0]
        sup.rollback()
        ut.append({"sekunder": round(sek, 4), "koet": n, "egne": egne,
                   "feilet": feilet})
        _log(f"kjøring {i}: {ut[-1]}")
    maks = max(x["sekunder"] for x in ut)
    art = {"oppsett": _oppsett(modul, runde),
           "maalt": {"kjoringer": ut, "per_kjoring": antall,
                     "maks_sekunder": maks,
                     "koet_totalt": sum(x["egne"] for x in ut)}}
    return _skriv(f"{k['kort']}-ytelse-v1", art)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modul", required=True, choices=sorted(FORPASSMODULER))
    ap.add_argument("punkt", choices=("fasit", "feilinjisering", "rollback",
                                      "ytelse"))
    ap.add_argument("--forgjenger", type=Path)
    ap.add_argument("--kjoringer", type=int, default=5)
    ap.add_argument("--antall", type=int, default=100)
    a = ap.parse_args()
    if a.punkt == "fasit":
        sti = fasit(a.modul)
    elif a.punkt == "feilinjisering":
        sti = feilinjisering(a.modul)
    elif a.punkt == "rollback":
        if not a.forgjenger:
            sys.exit("--forgjenger mangler")
        sti = rollback(a.modul, a.forgjenger)
    else:
        sti = ytelse(a.modul, a.kjoringer, a.antall)
    return 0 if json.loads(sti.read_text())["bestatt"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
