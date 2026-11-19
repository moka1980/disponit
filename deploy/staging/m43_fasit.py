"""M-43s fasitsett: seks subjekter i to tenanter med KJENT dom.

Telefoniregisteret har syv funntyper på `referanse` (samtale-,
eskalerings- eller linje-id). Fire er utelukket av datamodellen (se
`UNAABARE`, med migrasjonens egne grunner); de tre sveipen kan reise,
rigges alle:

  tenant A (samtaletak 1 t, eskaleringsfrist 1 døgn, terskel 7000 bp)
    1. `samtale_uten_avslutning`   — startet for 5 timer siden, aldri avsluttet
    2. `eskalering_over_frist`     — eskalert for 5 døgn siden, aldri lukket
    3. `ubekreftet_linje_uavklart` — transkribert linje med 1000 bp
    4. (ren)                       — som 1; AVSLUTTES mellom kjøringene
    5. (ok)                        — startet for 5 timer siden, avsluttet
  tenant B (samtaletak 2 t, eskaleringsfrist 2 døgn)
    6. `samtale_uten_avslutning`   — samme regel i en annen tenant

SAMTALENS START ER EN DØRPARAMETER (`p_startet_ts`), så samtalealderen
trenger ingen bakdatering. ESKALERINGEN bakdateres som migrator —
`eskalert_ts` er DEFAULT now() uten dørparameter, og
`test_m43_telefoni` går samme vei.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import sveipfasit_felles as felles

AKTOR = "m43-fasit"
EVIDENSKILDE = "m43_telefoni"
FUNNTABELL = "telefonifunn"
SUBJEKTKOLONNE = "referanse"

UNAABARE: dict[str, str] = {
    "opptak_uten_hjemmel":
        "`samtaleopptak.hjemmel_id` er NOT NULL med fremmednøkkel mot "
        "tenantens hjemmelregister, så et opptak uten hjemmel kan ikke "
        "settes inn; funntypen står i settet for å navngi vernet.",
    "opptak_uten_varsling":
        "`varslet_ts` er NOT NULL, og døra nekter et opptak der varslingen "
        "ikke kom FØR opptaket startet; et opptak uten forutgående varsling "
        "er dermed urepresenterbart i registeret.",
    "agenten_skjulte_at_den_er_automatisert":
        "`identifisert_ts` er NOT NULL, døra nekter en identifikasjon "
        "senere enn tenantens frist, og ingen transkripsjonslinje kan "
        "dateres før den — agenten kan ikke ha talt før den sa hva den er.",
    "eskalering_uten_regel":
        "`eskalering.regel_id` er NOT NULL med fremmednøkkel til tenantens "
        "egen eskaleringsregel, og døra nekter en avviklet regel; en "
        "eskalering uten regel kan ikke oppstå.",
}
RENSES = "ren"
NEGATIV_KONTROLL = "ok"

SETT: tuple[tuple[str, str | None, str], ...] = (
    ("samtale", "samtale_uten_avslutning", "a"),
    ("eskalering", "eskalering_over_frist", "a"),
    ("linje", "ubekreftet_linje_uavklart", "a"),
    ("ren", None, "a"),
    ("ok", None, "a"),
    ("samtale_b", "samtale_uten_avslutning", "b"),
)
#: (terskel bp, identifikasjonsfrist sek, eskaleringsfrist døgn,
#:  samtaletak timer) — ulike per tenant, så kravhendelsene ikke deler hash
KRAV = {"a": (7000, 60, 1, 1), "b": (7000, 61, 2, 2)}


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen)."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m43fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m43-fasit")


def _samtale(rt, tenant: str, avslutt: bool) -> str:
    sid = str(uuid.uuid4())
    _sk(rt, tenant)
    rt.execute("SELECT * FROM m43_start_samtale(%s,%s,'inngaaende',"
               "'+4700000000', now() - interval '5 hours',"
               " now() - interval '5 hours' + interval '10 seconds',"
               " 'Du snakker med en automatisert agent', %s)",
               (tenant, sid, AKTOR))
    if avslutt:
        rt.execute("SELECT m43_avslutt_samtale(%s,%s,"
                   " now() - interval '4 hours', %s)", (tenant, sid, AKTOR))
    rt.commit()
    return sid


def forbered(rt, runde: str, mg=None) -> dict:
    """Begge tenantene — men UTEN avslutningen av den rene samtalen: den
    kommer mellom kjøringene."""
    import psycopg
    egen = mg is None
    if egen:
        mg = psycopg.connect(os.environ["DISPONIT_MIGRATOR_URL"])
    rigg = {"a": tenantnavn(runde, "a"), "b": tenantnavn(runde, "b"),
            "subjekter": {}}
    s: dict[str, str] = {}
    try:
        for tkey, krav in KRAV.items():
            _sk(rt, rigg[tkey])
            rt.execute("SELECT m43_sett_krav(%s,%s,%s,%s,%s,%s)",
                       (rigg[tkey], *krav, AKTOR))
            rt.commit()
        a = rigg["a"]
        s["samtale"] = _samtale(rt, a, avslutt=False)

        s_esk = _samtale(rt, a, avslutt=False)
        rid, eid = str(uuid.uuid4()), str(uuid.uuid4())
        _sk(rt, a)
        rt.execute("SELECT m43_registrer_regel(%s,%s,'Fasitregel: eskaler til vakt',"
                   "'vakt@x.test', current_date - 1, NULL, %s)",
                   (a, rid, AKTOR))
        rt.execute("SELECT * FROM m43_eskaler(%s,%s,%s,%s,'Fasit: kunden ba om et menneske',%s)",
                   (a, eid, s_esk, rid, AKTOR))
        # Linjen hører til samme samtale: datert etter identifikasjonen.
        lid = str(uuid.uuid4())
        rt.execute("SELECT * FROM m43_registrer_linje(%s,%s,%s,1,'motpart',"
                   " now() - interval '5 hours' + interval '20 seconds',"
                   " 'Fasitlinje','transkripsjon',1000,NULL,%s)",
                   (a, lid, s_esk, AKTOR))
        rt.execute("SELECT m43_avslutt_samtale(%s,%s,"
                   " now() - interval '4 hours', %s)", (a, s_esk, AKTOR))
        rt.commit()
        _sk(mg, a)
        mg.execute("UPDATE eskalering SET eskalert_ts = now()"
                   " - interval '5 days' WHERE tenant=%s"
                   " AND eskalering_id=%s", (a, eid))
        mg.commit()
        s["eskalering"] = eid
        s["linje"] = lid
        s["ren"] = _samtale(rt, a, avslutt=False)
        s["ok"] = _samtale(rt, a, avslutt=True)
        s["samtale_b"] = _samtale(rt, rigg["b"], avslutt=False)
    finally:
        if egen:
            mg.close()
    rigg["subjekter"] = {m: {"merke": m, "subjekt_id": v}
                         for m, v in s.items()}
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    return [rigg["a"], rigg["b"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Den rene samtalen avsluttes — MELLOM sveipene. Døra rører ikke
    funnet; sveipen må se at samtalen er avsluttet og lukke."""
    _sk(rt, rigg["a"])
    rt.execute("SELECT m43_avslutt_samtale(%s,%s, now() - interval"
               " '4 hours', %s)",
               (rigg["a"], rigg["subjekter"][RENSES]["subjekt_id"], AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    return {m: f for m, f, _t in SETT}


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
