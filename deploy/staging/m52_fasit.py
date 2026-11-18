"""M-52s fasitsett: fire nomenklaturer og fem forslag med KJENT dom.

Tollregisteret har sju funntyper over tre nøkler; settet bruker to:
nomenklaturen (`nomenklatur_id`) og forslaget (`forslag_id`). Subjektet
leses som `coalesce(nomenklatur_id, vare_id, forslag_id)`.

  nomenklatur 1. `nomenklatur_utloper_snart` — gyldig til om 5 døgn
              2. `nomenklatur_utlopt`        — avviklet etter forslaget;
                                               eget system (`kn`), så
                                               ingenting avløser den
              3. `ingen_krav`                — tenant B har ingen krav
              4. (n_gyldig)                  — `hs` uten sluttdato
  forslag     5. `forslag_under_terskel`     — 50 % mot terskel 70
              6. `forslag_ikke_klart`        — 90 %, ikke merket klart
              7. `forslag_mot_utlopt_nomenklatur` — 90 %, merket klart
                                               FØR nomenklaturen avvikles
              8. (ren)                       — 90 %, ikke klart; merkes
                                               klart MELLOM kjøringene
              9. (ok)                        — 90 %, merket klart

Hver vare har ett forslag, og sveipen leser det NYESTE per vare.

To ting avgjør rekkefølgen. `m52_avgi_forslag` og `m52_merk_klart`
nekter mot en nomenklatur som ikke gjelder, så forslaget mot den
utløpte avgis OG merkes klart før avviklingen — uten klarmerkingen ville
det også fått `forslag_ikke_klart`. Og en utløpt nomenklatur er bare et
funn når ingen gyldig nomenklatur av SAMME system har avløst den.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m52-fasit"
EVIDENSKILDE = "m52_tollkode"
FUNNTABELL = "tollfunn"
SUBJEKTKOLONNE = "coalesce(nomenklatur_id, vare_id, forslag_id)"

UNAABARE: dict[str, str] = {
    "vare_uten_forslag":
        "kandidaten krever at en vare er registrert for mer enn "
        "`forslagsfrist_dogn` siden uten forslag, og gulvet for den "
        "terskelen er ett døgn (CHECK BETWEEN 1 AND 365). "
        "`tollvare.registrert` er DEFAULT now() og ingen dørparameter i "
        "`m52_registrer_vare`, så funnet kan ikke lages på én dag.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

#: Kravene i tenant A: sikkerhetsterskel 70 %, utløpsvarsel 30 døgn,
#: forslagsfrist 14 døgn.
KRAV = (70, 30, 14)

#: (merkelapp, forventet, tenantnøkkel, system, gyldig_til om N døgn eller
#:  None, avvikles N døgn tilbake etter forslagene eller None)
NOMENKLATURER: tuple[tuple[str, str | None, str, str, int | None,
                           int | None], ...] = (
    ("n_gyldig", None, "a", "hs", None, None),
    ("n_utloper", "nomenklatur_utloper_snart", "a", "hs", 5, None),
    ("n_utlopt", "nomenklatur_utlopt", "a", "kn", None, 10),
    ("n_uten_krav", "ingen_krav", "b", "hs", None, None),
)

#: (merkelapp, forventet, nomenklatur, sikkerhet, merkes klart i riggen)
FORSLAG: tuple[tuple[str, str | None, str, int, bool], ...] = (
    ("under", "forslag_under_terskel", "n_gyldig", 50, False),
    ("ikke_klart", "forslag_ikke_klart", "n_gyldig", 90, False),
    ("mot_utlopt", "forslag_mot_utlopt_nomenklatur", "n_utlopt", 90, True),
    # Den RENE fødes uklar: funnet den får i første sveip er nettopp det
    # andre sveip skal lukke.
    ("ren", None, "n_gyldig", 90, False),
    ("ok", None, "n_gyldig", 90, True),
)


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m52fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m52-fasit")


def _klart(rt, tenant: str, fid: str) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT m52_merk_klart(%s,%s,%s)", (tenant, fid, AKTOR))
    rt.commit()


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN klarmerkingen av det rene forslaget:
    den kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}, "nomenklaturer": {}, "varenumre": {}}
    a = rigg["a"]
    _sk(rt, a)
    rt.execute("SELECT m52_sett_krav(%s,%s,%s,%s,%s,%s)",
               (a, *KRAV, AKTOR, f"m52fasit-krav-{runde}"))
    rt.commit()

    for merke, _v, tkey, system, til, _ut in NOMENKLATURER:
        tenant = rigg[tkey]
        nid = str(uuid.uuid4())
        _sk(rt, tenant)
        rt.execute(
            "SELECT m52_registrer_nomenklatur(%s,%s,%s,%s,"
            " current_date - 400, CASE WHEN %s::int IS NULL THEN NULL"
            " ELSE current_date + %s::int END, %s, NULL, %s)",
            (tenant, nid, system, f"{system.upper()} fasit {merke}", til,
             til, hashlib.sha256(f"{runde}:{merke}".encode()).hexdigest(),
             AKTOR))
        rt.commit()
        rigg["nomenklaturer"][merke] = nid
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": nid}
        if tkey == "a" and merke in ("n_gyldig", "n_utlopt"):
            vnr = str(uuid.uuid4())
            _sk(rt, tenant)
            rt.execute("SELECT m52_registrer_varenummer(%s,%s,%s,'7318.15',"
                       "'Skruer og bolter av jern eller staal',4500,%s)",
                       (tenant, vnr, nid, AKTOR))
            rt.commit()
            rigg["varenumre"][merke] = vnr

    for merke, _v, nom, sikkerhet, klart in FORSLAG:
        vare, fid = str(uuid.uuid4()), str(uuid.uuid4())
        _sk(rt, a)
        rt.execute("SELECT m52_registrer_vare(%s,%s,%s,'Sekskantskrue M8x40',"
                   "'staal','festemiddel','DE',%s)",
                   (a, vare, f"ART-{merke}-{runde}", AKTOR))
        rt.commit()
        _sk(rt, a)
        rt.execute(
            "SELECT * FROM m52_avgi_forslag(%s,%s,%s,%s,%s,"
            " ARRAY['nomenklaturtekst'], ARRAY['7318.15'],"
            " ARRAY['Skruer og bolter av jern eller staal'],"
            " ARRAY[NULL]::date[], %s)",
            (a, fid, vare, rigg["varenumre"][nom], sikkerhet, AKTOR))
        rt.commit()
        if klart:
            _klart(rt, a, fid)
        rigg["subjekter"][merke] = {"merke": merke, "subjekt_id": fid}

    # Avviklingen kommer ETTER forslagene og klarmerkingen: begge dørene
    # nekter mot en nomenklatur som ikke gjelder.
    for merke, _v, tkey, _sys, _til, utlopt in NOMENKLATURER:
        if utlopt is None:
            continue
        _sk(rt, rigg[tkey])
        rt.execute("SELECT m52_sett_gyldig_til(%s,%s, current_date - %s,"
                   " %s)", (rigg[tkey], rigg["nomenklaturer"][merke],
                            utlopt, AKTOR))
        rt.commit()
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Klarmerkingen som gjør det rene forslaget rent — MELLOM sveipene."""
    _klart(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"])


def forventet() -> dict[str, str | None]:
    ut = {m: f for m, f, _t, _s, _til, _u in NOMENKLATURER}
    ut.update({m: f for m, f, _n, _s, _k in FORSLAG})
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
