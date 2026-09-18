"""M-27s fasitsett: seks varer med KJENT dom, drevet gjennom dørene.

Lagerregisteret har fem funntyper. Settet gir hver av de fire som er
NÅBARE nøyaktig én vare, én vare i en tenant uten terskler, og én vare
som skal være REN:

  1. `under_bestillingspunkt` — punkt 50, beholdning 10
  2. `uten_bevegelse`         — siste bevegelse eldre enn `stille_dogn`
  3. `ikke_talt`              — siste telling eldre enn `telleintervall_dogn`
  4. `ingen_terskel`          — egen tenant UTEN terskler (regelen gjelder
                                tenanten, ikke varen)
  5. (ren)                    — punkt, beholdning over det, fersk bevegelse
                                og fersk telling

DEN FEMTE FUNNTYPEN, `uten_bestillingspunkt`, ER IKKE NÅBAR FOR EN VARE
SOM BLE OPPRETTET I DAG, og det er ikke en mangel ved settet — det er
regelen som virker. Kandidaten krever

    p_dag - greatest(vare.opprettet::date, siste punktslutt) > uten_punkt_dogn

og `m27_registrer_vare` har ingen parameter for `opprettet`: en ny vare
har en nådefrist før fraværet av et punkt blir et funn. Settet bærer
derfor en NEGATIV KONTROLL i stedet: en vare uten punkt, opprettet i
dag, skal IKKE få funnet. Uten den raden hadde nådefristen vært en
påstand i en dokumentstreng, og en regresjon som fjernet den ville gått
upåaktet forbi.

Alt går gjennom modulens egne dører (`m27_sett_terskler`,
`m27_registrer_vare`, `m27_sett_bestillingspunkt`,
`m27_registrer_bevegelse`, `m27_registrer_telling`). Ingen rå DML i
registerets tabeller: en rigg som skriver forbi dørene måler riggen.

TO KJØRINGER, OG LUKKINGEN MÅLES PÅ EKTE (m19-formen): den rene varen
fødes med sin siste bevegelse 200 døgn tilbake, så første sveip gir den
`uten_bevegelse`. Bevegelsen som gjør den ren kommer MELLOM kjøringene,
og andre sveip skal lukke funnet. Sto varen ren fra starten, ville
`lukkede` alltid vært null, og aksen hadde vært en påstand.
"""
from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import sveipfasit_felles as felles

#: Tersklene settet måles mot — pinnet her, aldri utledet av kjøringen.
STILLE_DOGN = 180
UTEN_PUNKT_DOGN = 30
TELLEINTERVALL_DOGN = 365

AKTOR = "m27-fasit"
#: `kilde`-verdien modulens dører skriver i evidenskjeden.
EVIDENSKILDE = "m27_lager"
#: Funntabellen og subjektkolonnen. Navnene står ÉN gang, her, og
#: leses av det delte maskineriet: en kopi der det ene ble rettet og
#: det andre ikke, ville lest et ANNET register enn modulen skriver i
#: — og talt null funn uten å si fra.
FUNNTABELL = "lagerfunn"
SUBJEKTKOLONNE = "vare_id"

#: (merkelapp, forventet funntype eller None, punkt eller None,
#:  [(bevegelsestype, antall, døgn siden)], telling: (antall, døgn siden)
#:  eller None)
SETT: tuple[tuple[str, str | None, int | None, list, tuple | None], ...] = (
    # Beholdning 10 mot et punkt på 50. Fersk bevegelse, så `uten_bevegelse`
    # ikke også slår inn; ingen telling, så fallbacken til `opprettet`
    # holder `ikke_talt` unna.
    ("under_punkt", "under_bestillingspunkt", 50, [("mottak", 10, 0)], None),
    # Siste bevegelse 200 døgn tilbake. Beholdningen ligger godt over
    # punktet, så bare tidsfunnet står igjen.
    ("stille", "uten_bevegelse", 1, [("mottak", 100, 200)], None),
    # Tellingen er 400 døgn gammel, men bevegelsen er fersk: da er det
    # BARE tellingen som mangler.
    ("utalt", "ikke_talt", 1, [("mottak", 100, 0)], (0, 400)),
    # NEGATIV KONTROLL: uten punkt, men opprettet i dag. Nådefristen på
    # `uten_punkt_dogn` skal holde funnet unna.
    ("uten_punkt_ny", None, None, [("mottak", 100, 0)], (100, 0)),
    # Den RENE: fødes med bevegelsen 200 døgn tilbake, så første sveip
    # gir den `uten_bevegelse`. Bevegelsen som gjør den ren kommer
    # mellom kjøringene.
    #
    # INGEN TELLING HER, og det er selve poenget: EN TELLING ER OGSÅ EN
    # BEVEGELSE (`lagerbevegelse` med type `telling`), så en fersk
    # telling ville gjort varen «i bevegelse» og drept funnet vi skal
    # lukke. Fallbacken til `opprettet` holder `ikke_talt` unna så lenge
    # varen er yngre enn telleintervallet.
    ("ren", None, 1, [("mottak", 100, 200)], None),
)

#: Varen i tenanten UTEN terskler — hele tenanten er funnet.
UTEN_TERSKEL = ("ingen_terskel", "ingen_terskel", 1, [("mottak", 100, 0)],
                (100, 0))

#: Bevegelsen som gjør den rene varen ren, registrert MELLOM sveipene.
REN_BEVEGELSE = ("mottak", 1, 0)

#: FUNNTYPER SETTET IKKE KAN PRODUSERE, og HVORFOR. Lista er en del av
#: kontrakten: porten krever at settet dekker registerets funntyper, og
#: et unntak som bare fantes i en dokumentstreng ville vært et hull
#: ingen kunne se. Hver oppføring skal kunne leses som en påstand om
#: REGELEN, ikke om riggen.
UNAABARE: dict[str, str] = {
    "uten_bestillingspunkt":
        "kandidaten krever at det er gått mer enn `uten_punkt_dogn` siden"
        " varen ble opprettet, og `m27_registrer_vare` har ingen parameter"
        " for `opprettet`: en ny vare har en nådefrist. Settet bærer en"
        " NEGATIV kontroll i stedet — varen `uten_punkt_ny` har intet"
        " punkt og skal likevel ikke få funnet.",
}

#: SUBJEKTET SOM RENSES mellom de to sveipene. Navnet står her, ikke i
#: den generiske produsenten: modulene kaller det ikke det samme, og en
#: hardkodet merkelapp leste bare null.
RENSES = "ren"

#: Subjektet som beviser nådefristen. Navngitt her, ikke bare i settet,
#: så porten kan kreve at det finnes.
NEGATIV_KONTROLL = "uten_punkt_ny"


def sett_sha256() -> str:
    """Settets identitet er BYTENE i denne filen (m02-formen): to
    kjøringer som mener de driver samme sett, men ikke gjør det, skal
    ikke kunne kalles like."""
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


def tenantnavn(runde: str, rolle: str) -> str:
    return f"t-m27fasit-{rolle}-{runde}"


def _sk(conn, tenant: str):
    felles.sett_kontekst(conn, tenant, AKTOR, "m27-fasit")


def lag_vare(rt, tenant: str, merke: str, punkt: int | None,
             bevegelser: list, telling: tuple | None) -> dict:
    """Én vare med punkt, bevegelser og telling — gjennom dørene.

    TELLINGEN REGISTRERES FØRST når den er gammel: `m27_registrer_telling`
    bokfører differansen mot beholdningen SLIK DEN ER NÅ, ikke slik den
    var på telledatoen. Rekkefølgen er derfor en del av riggen, ikke en
    tilfeldighet."""
    vid = uuid.uuid4()
    _sk(rt, tenant)
    rt.execute("SELECT m27_registrer_vare(%s,%s,%s,%s,%s,%s)",
               (tenant, vid, f"kode-{merke}", f"Fasit {merke}", "stk", AKTOR))
    rt.commit()
    if punkt is not None:
        _sk(rt, tenant)
        rt.execute(
            "SELECT m27_sett_bestillingspunkt(%s,%s,%s,"
            " current_date - 1, %s, %s)",
            (tenant, vid, punkt, f"fasitpunkt {merke}", AKTOR))
        rt.commit()
    if telling is not None:
        talt, siden = telling
        _sk(rt, tenant)
        rt.execute(
            "SELECT m27_registrer_telling(%s,%s,%s,%s,"
            " current_date - %s, %s, %s)",
            (tenant, uuid.uuid4(), vid, talt, siden, f"fasittelling {merke}",
             AKTOR))
        rt.commit()
    for type_, antall, siden in bevegelser:
        _sk(rt, tenant)
        rt.execute(
            "SELECT m27_registrer_bevegelse(%s,%s,%s,%s,%s,NULL,"
            " current_date - %s, %s, %s)",
            (tenant, uuid.uuid4(), vid, type_, antall, siden,
             f"fasitbevegelse {merke}", AKTOR))
        rt.commit()
    return {"merke": merke, "subjekt_id": str(vid)}


def forbered(rt, runde: str) -> dict:
    """Begge tenantene, med og uten terskler — men UTEN den rene varens
    siste bevegelse: den kommer mellom kjøringene (`kontroller_ren`), så
    lukkingen kan måles. -> riggen."""
    med = tenantnavn(runde, "med_terskel")
    uten = tenantnavn(runde, "uten_terskel")
    _sk(rt, med)
    rt.execute("SELECT m27_sett_terskler(%s,%s,%s,%s,%s)",
               (med, STILLE_DOGN, UTEN_PUNKT_DOGN, TELLEINTERVALL_DOGN, AKTOR))
    rt.commit()
    rigg = {"med_terskel": med, "uten_terskel": uten, "subjekter": {}}
    for merke, _ventet, punkt, bevegelser, telling in SETT:
        rigg["subjekter"][merke] = lag_vare(rt, med, merke, punkt,
                                            bevegelser, telling)
    merke, _v, punkt, bevegelser, telling = UTEN_TERSKEL
    rigg["subjekter"][merke] = lag_vare(rt, uten, merke, punkt, bevegelser,
                                        telling)
    return rigg


def riggtenanter(rigg: dict) -> list[str]:
    """Riggens tenanter, i den formen de generiske produsentene ber om."""
    return [rigg["med_terskel"], rigg["uten_terskel"]]


def kontroller_ren(rt, rigg: dict) -> None:
    """Bevegelsen som gjør den rene varen ren — MELLOM de to sveipene."""
    type_, antall, siden = REN_BEVEGELSE
    vid = rigg["subjekter"][RENSES]["subjekt_id"]
    _sk(rt, rigg["med_terskel"])
    rt.execute(
        "SELECT m27_registrer_bevegelse(%s,%s,%s,%s,%s,NULL,"
        " current_date - %s, %s, %s)",
        (rigg["med_terskel"], uuid.uuid4(), vid, type_, antall, siden,
         "fasit: gjør varen ren", AKTOR))
    rt.commit()


def forventet() -> dict[str, str | None]:
    """Fasiten: merkelapp → funntype (None = ingen funn)."""
    ut = {m: f for m, f, _p, _b, _t in SETT}
    ut[UTEN_TERSKEL[0]] = UTEN_TERSKEL[1]
    return ut


def maal(m, rigg: dict) -> dict:
    """Dommen, RE-REGNET av registerets rader — aldri av riktig antall."""
    return felles.maal(m, rigg, funntabell=FUNNTABELL,
                       subjektkolonne=SUBJEKTKOLONNE, forventet=forventet(),
                       tenanter=riggtenanter(rigg), aktor=AKTOR)
