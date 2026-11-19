"""M-45s fasitsett: fem subjekter i to tenanter med KJENT dom.

ESG-registeret har åtte funntyper på `referanse`. Fem er utelukket av
datamodellen, og `estimat_ikke_erstattet_over_frist` kan ikke nås samme
dag (se `UNAABARE`). De to andre rigges, begge på `periode_id`:

  tenant A (estimatterskel 1000 bp, estimatfrist 1 døgn)
    1. `standardversjon_foreldet_i_apen_periode`
                                — åpen periode på standard S1 v1, og en
                                  faktor i S1 v2 gyldig senere
    2. `estimatandel_over_terskel_uavklart`
                                — periode på S2 v1 der hele utslippet er
                                  estimat
    3. (ren)                    — som 1 (egen standard S3); perioden LUKKES
                                  mellom kjøringene
    4. (ok)                     — periode på S4 v1 med en målt (ikke
                                  estimert) mengde
  tenant B (estimatterskel 1001 bp)
    5. `estimatandel_over_terskel_uavklart` — samme regel

HVER PERIODE HAR SIN EGEN STANDARD — OG SIN EGEN KATEGORI. «Foreldet»
gjelder alle åpne perioder på samme standard, så en delt standard ville
gitt subjektene flere funn enn fasiten sier; og faktorens unikhet er
(tenant, kategori, standardversjon, gyldig_fra) uten standarden, så to
standarder med samme kategori og dato ville kollidert.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m45-fasit"
EVIDENSKILDE = "m45_esg"
FUNNTABELL = "esgfunn"
SUBJEKTKOLONNE = "referanse"

UNAABARE: dict[str, str] = {
    "tall_uten_kilde":
        "`esgmaaling.kilde_id` er NOT NULL med fremmednøkkel mot husets "
        "kildedokumenter, så et tall uten kilde kan ikke settes inn; "
        "funntypen står i settet for å navngi vernet.",
    "tall_uten_faktorversjon":
        "`faktor_id` og `standardversjon` er NOT NULL, og døra nekter en "
        "faktor i en annen standardversjon enn periodens — et tall uten "
        "faktorversjon er urepresenterbart.",
    "estimat_ikke_merket":
        "`er_estimat` er NOT NULL UTEN DEFAULT, og døra nekter et kall som "
        "ikke sier om tallet er målt eller gjettet; et umerket estimat kan "
        "ikke oppstå i registeret.",
    "paastand_uten_kilde":
        "`esgpaastand.kilde_id` er NOT NULL med fremmednøkkel, som i M-20; "
        "en påstand uten kilde kan ikke settes inn.",
    "modulen_sendte_rapport":
        "det finnes ingen kolonne og ingen dør for «sendt»: modulen "
        "sammenstiller, den sender ikke, så en rapport modulen sendte kan "
        "ikke representeres.",
    "estimat_ikke_erstattet_over_frist":
        "`esgmaaling.registrert` er DEFAULT now() uten dørparameter og "
        "frosset, og estimatfristen har gulv ett døgn (CHECK 1–3650). Et "
        "estimat kan derfor ikke være over fristen samme dag det ble "
        "registrert; riggen ville målt klokka (M-48-presedensen).",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

SETT: tuple[tuple[str, str | None, str], ...] = (
    ("foreldet", "standardversjon_foreldet_i_apen_periode", "a"),
    ("estimat", "estimatandel_over_terskel_uavklart", "a"),
    ("ren", None, "a"),
    ("ok", None, "a"),
    ("estimat_b", "estimatandel_over_terskel_uavklart", "b"),
)
KRAV = {"a": (1000, 1, 365), "b": (1001, 2, 366)}


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m45fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m45-fasit")


def _kilde(rt, tenant: str) -> str:
    kid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m45_registrer_kilde(%s,%s,'Fasitkilde','annet',"
               " current_date + 365, %s, %s)",
               (tenant, kid, secrets.token_hex(32), AKTOR))
    rt.commit()
    return kid


def _kategori(standard: str) -> str:
    return f"drivstoff_{standard.lower()}"


def _faktor(rt, tenant: str, kilde: str, standard: str, versjon: str,
            fra_sql: str) -> str:
    fid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute(f"SELECT m45_registrer_faktor(%s,%s,%s,'liter',"
               f" 2.5, %s, %s, %s, {fra_sql}, %s)",
               (tenant, fid, _kategori(standard), standard, versjon, kilde,
                AKTOR))
    rt.commit()
    return fid


def _periode(rt, tenant: str, standard: str, merke: str) -> str:
    pid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT m45_apne_periode(%s,%s,%s, date_trunc('year',"
               " current_date)::date, (date_trunc('year', current_date)"
               " + interval '1 year - 1 day')::date, %s, 'v1', %s)",
               (tenant, pid, f"Fasit {merke}", standard, AKTOR))
    rt.commit()
    return pid


def _maaling(rt, tenant: str, periode: str, faktor: str, kilde: str,
             estimat: bool, standard: str) -> None:
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m45_registrer_maaling(%s,%s,%s,%s,"
               " 100, 'liter', %s, %s, %s, NULL, %s, %s)",
               (tenant, str(uuid.uuid4()), periode, _kategori(standard),
                faktor, estimat,
                "Fasit: anslag fra innkjøp" if estimat else None, kilde,
                AKTOR))
    rt.commit()


def forbered(rt, runde: str) -> dict:
    """Begge tenantene — men UTEN lukkingen av den rene perioden: den
    kommer mellom kjøringene."""
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}}
    s: dict[str, str] = {}
    for tkey, krav in KRAV.items():
        _sk(rt, rigg[tkey])
        rt.execute("SELECT m45_sett_krav(%s,%s,%s,%s,%s)",
                   (rigg[tkey], *krav, AKTOR))
        rt.commit()
    a = rigg["a"]
    k = _kilde(rt, a)
    for merke, std in (("foreldet", "S1"), ("ren", "S3")):
        _faktor(rt, a, k, std, "v1", "current_date - 30")
        s[merke] = _periode(rt, a, std, merke)
        _faktor(rt, a, k, std, "v2", "current_date")
    f2 = _faktor(rt, a, k, "S2", "v1", "current_date - 30")
    s["estimat"] = _periode(rt, a, "S2", "estimat")
    _maaling(rt, a, s["estimat"], f2, k, estimat=True, standard="S2")
    f4 = _faktor(rt, a, k, "S4", "v1", "current_date - 30")
    s["ok"] = _periode(rt, a, "S4", "ok")
    _maaling(rt, a, s["ok"], f4, k, estimat=False, standard="S4")

    b = rigg["b"]
    kb = _kilde(rt, b)
    fb = _faktor(rt, b, kb, "S2", "v1", "current_date - 30")
    s["estimat_b"] = _periode(rt, b, "S2", "estimat_b")
    _maaling(rt, b, s["estimat_b"], fb, kb, estimat=True, standard="S2")
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Den rene perioden lukkes — MELLOM sveipene. Sveipen lukker ikke
    perioder selv; den skal se at perioden ikke lenger er åpen og lukke
    funnet."""
    _sk(rt, rigg["a"])
    rt.execute("SELECT m45_lukk_periode(%s,%s,%s)",
               (rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"], AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
