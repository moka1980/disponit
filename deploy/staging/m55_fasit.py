"""M-55s fasitsett: sju subjekter i tre tenanter med KJENT dom.

Varselregisteret har seks typer, på to nivåer: merket (`funn_id IS NULL`)
eller funnet. Migrasjon 220 avleder husets funnform — `funntype` er
`varseltype`, og `subjekt` er funnet når varselet gjelder et funn, ellers
merket. `funn_eldre_enn_frist` kan ikke nås samme dag (se `UNAABARE`).

  tenant a (terskel 80)
    1. `merkevare_uten_funn`   — aktivt merke uten ett eneste funn
    2. `funn_uten_vurdering`   — et funn ingen har vurdert
    3. `forveksling_ikke_henvist` — identisk navn (likhet 100 ≥ 80), vurdert
                                 og ikke henvist
    4. (ren)                   — som 2; funnet VURDERES mellom kjøringene
                                 (ulikt navn, under terskel)
    5. (ok)                    — et ulikt navn, vurdert under terskel
  tenant b (ingen grenser satt)
    6. `ingen_terskler`        — aktivt merke i en tenant uten terskel
  tenant c (terskel 70, så 75)
    7. `vurdering_med_utdatert_terskel` — vurdert under 70, og terskelen
                                 heves til 75 før første kjøring

ULIKE TERSKLER PER TENANT: kravhendelsen bærer versjon og terskel, og to
tenanter med samme første krav ville delt `input_hash`.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m55-fasit"
EVIDENSKILDE = "m55_merkevare"
FUNNTABELL = "merkevarevarsel"
SUBJEKTKOLONNE = "subjekt"

UNAABARE: dict[str, str] = {
    "funn_eldre_enn_frist":
        "`merkevarefunn.registrert` er DEFAULT now() uten dørparameter, og "
        "`funnfrist_dogn` har gulv ett døgn (CHECK 1–365). Et funn kan derfor "
        "ikke være eldre enn fristen samme dag det ble registrert; riggen "
        "ville målt klokka (M-48-presedensen).",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

SETT: tuple[tuple[str, str | None, str], ...] = (
    ("uten_funn", "merkevare_uten_funn", "a"),
    ("uvurdert", "funn_uten_vurdering", "a"),
    ("forveksling", "forveksling_ikke_henvist", "a"),
    ("ren", None, "a"),
    ("ok", None, "a"),
    ("ingen_terskel", "ingen_terskler", "b"),
    ("utdatert", "vurdering_med_utdatert_terskel", "c"),
)
#: (terskel, funnfrist, henvisningsfrist) — ulike per tenant.
KRAV = {"a": (80, 14, 3), "c": (70, 15, 4)}
KRAV_C_HEVET = (75, 15, 4)

#: Merkenavnet og det observerte navnet. Like → likhet 100; ulike →
#: Levenshtein-likhet langt under enhver terskel i settet.
NAVN = {
    "uten_funn": ("Nordkapp Lys", None),
    "uvurdert": ("Vestavind", "Vestavind Butikk"),
    "forveksling": ("Fjordlys", "Fjordlys"),
    "ren": ("Havbris", "Kvartzon"),
    "ok": ("Morgengry", "Plexitum"),
    "ingen_terskel": ("Solgangsbris", None),
    "utdatert": ("Tindefjell", "Bryggesag"),
}
VURDERES = ("forveksling", "ok", "utdatert")


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m55fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m55-fasit")


def _krav(rt, tenant: str, krav: tuple, nokkel: str) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT m55_sett_krav(%s,%s,%s,%s,%s,%s)",
               (tenant, *krav, AKTOR, nokkel))
    rt.commit()


def _merke(rt, tenant: str, navn: str) -> str:
    mid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m55_registrer_merkevare(%s,%s,%s,'varemerke',NULL,"
               "NULL,ARRAY['35']::text[],current_date - 30,%s)",
               (tenant, mid, navn, AKTOR))
    rt.commit()
    return mid


def _funn(rt, tenant: str, merke: str, observert: str) -> str:
    kid, fid = str(uuid.uuid4()), str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m55_registrer_bevaringskopi(%s,%s,%s,now(),%s,"
               "2048,'text/html',%s,%s)",
               (tenant, kid, f"https://eksempel.test/{kid}",
                secrets.token_hex(32), f"fasit/{kid}", AKTOR))
    rt.execute("SELECT m55_registrer_funn(%s,%s,%s,%s,%s,'annonsetekst',"
               "%s,NULL,%s)",
               (tenant, fid, merke, kid, observert,
                f"Annonse der «{observert}» brukes om lignende varer", AKTOR))
    rt.commit()
    return fid


def _vurder(rt, tenant: str, fid: str) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m55_vurder_funn(%s,%s,%s,%s)",
               (tenant, fid, str(uuid.uuid4()), AKTOR))
    rt.commit()


def forbered(rt, runde: str) -> dict:
    """Alle tre tenantene — men UTEN vurderingen av det rene funnet: den
    kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "c": tenantnavn(runde, "c"), "subjekter": {}, "funn": {}}
    for tkey, krav in KRAV.items():
        _krav(rt, rigg[tkey], krav, f"fasit-{runde}-{tkey}")
    s: dict[str, str] = {}
    for merke_, _f, tkey in SETT:
        t = rigg[tkey]
        navn, observert = NAVN[merke_]
        mid = _merke(rt, t, navn)
        if observert is None:
            s[merke_] = mid                 # merkenivå
            continue
        fid = _funn(rt, t, mid, observert)
        s[merke_] = fid                     # funnivå
        if merke_ in VURDERES:
            _vurder(rt, t, fid)
    # Terskelen heves ETTER vurderingen i c: dommen ble tatt under en
    # annen regel.
    _krav(rt, rigg["c"], KRAV_C_HEVET, f"fasit-{runde}-c2")
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"], rigg["c"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Det rene funnet VURDERES — mellom sveipene. Navnene er ulike, så
    vurderingen havner under terskelen og etterlater ingen ny tilstand;
    sveipen skal se at funnet er vurdert og lukke varselet."""
    _vurder(rt, rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"])


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
