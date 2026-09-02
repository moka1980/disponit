"""M-25 (107) — prosjektsveipen for kontrakter og budsjetter.

`disponit-prosjektsveip.timer`, én gang i døgnet, kaller
`m25_sveip_prosjekter(p_grense)`.

Sveipen legger INGEN logikk oppå regelen. Regelen eies av databasen
(107): en unådd milepæl som har passert sin dato med mer enn tenantens
frist er `milepael_over_frist`; et forbruk mer enn
`budsjettvarsel_promille` over budsjettet er `budsjett_overskredet`; et
aktivt prosjekt uten en eneste milepæl er `betalingsplan_mangler`; et
aktivt prosjekt uten en eneste føring på `stillhet_dogn` er
`ingen_arbeid_registrert`; og en tenant uten grenser er `ingen_terskel`.

SVEIPEN FAKTURERER INGEN OG MERKER INGEN MILEPÆL NÅDD. Den kunne,
teknisk — den vet hvilke som har passert sin dato. Men en milepæl er
GRUNNLAGET FOR ET KRAV mot en kunde, og en jobb som merket den nådd om
natten ville skapt det kravet uten at noen dokumenterte noe. Det er
klyngens dom: her holder vi igjen på å AUTORISERE, ikke bare på å
utføre.

GRENSENE ER TENANTENS, IKKE MODULENS. Budsjettvarselet, milepælfristen
og stillhetsgrensen ligger i basen, satt gjennom en dør av tenanten
selv. Denne fila bærer derfor ingen av dem — en konstant her ville vært
nøyaktig den fullmakten invarianten `budsjettvarsel_hardkodet` forbyr.

Formen er `leverandorsveip.py` sin, ordrett, og av de samme grunnene:

  * **Advisory-lås.** To sveip overlapper aldri; `hoppet_over` står PÅ
    resultatet så kalleren vet at feiltelleren skal stå urørt.
  * **Kontrakten valideres FØR commit**, og på ALLE radene: nøyaktig én
    rad, ellers rulles det tilbake og kjøringen sier feilet.
  * **To sammenhengende feilede kjøringer → alarm.** En stille
    prosjektsveip er et budsjett som sprekker uten at noen ser det.
  * **Én JSON-linje per kjøring, exit 1 ved feil.**

TAKET er 500 nye funn per tenant per kjøring. Det begrenser
TRANSAKSJONEN, ikke sannheten.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Maks antall NYE funn sveipen reiser per tenant per kjøring.
GRENSE = 500
#: INGEN TERSKEL HER, og det er poenget: grensene er TENANTENS, og de
#: ligger i `prosjektterskel` i basen. En konstant i denne fila ville
#: vært nøyaktig den fullmakten invarianten `budsjettvarsel_hardkodet`
#: forbyr — «fem prosent over budsjett er for mye» er en
#: forretningsbeslutning, ikke en driftsvurdering.
#: Antall sammenhengende feilede kjøringer som utløser alarm.
ALARM_ETTER_FEIL = 2
#: Advisory-nøkkel: to prosjektsveip overlapper aldri. Tallet er
#: modulens eget og deles ikke med noen annen sveip — to jobber som
#: låser på samme nøkkel ville blokkert hverandre uten grunn.
ARBEIDERNOKKEL = 884_310_565


@dataclass
class Sveipresultat:
    tenanter: int = 0
    nye: int = 0
    oppdaterte: int = 0
    lukkede: int = 0
    #: Antall tenanter der sveipen traff taket sitt. Ikke en feil — men
    #: heller ikke «ferdig», og den forskjellen skal stå i linjen.
    avkortet: int = 0
    feilet: bool = False
    alarm_utlost: bool = False
    #: En kjøring som fant arbeidernøkkelen opptatt har verken lyktes
    #: eller feilet. Skillet må stå PÅ resultatet, ellers kan ikke
    #: kalleren vite at feiltelleren skal stå urørt (artefaktrydding,
    #: Codex P2).
    hoppet_over: bool = False


def kjor(conn, *, grense: int = GRENSE,
         tidligere_feil: int = 0) -> Sveipresultat:
    """Én sveipekjøring.

    `tidligere_feil` er antall sammenhengende feilede kjøringer FØR
    denne; kalleren (timeren) bærer den telleren mellom kjøringer, siden
    hver kjøring er en egen prosess.
    """
    res = Sveipresultat()
    fikk_lås = conn.execute("SELECT pg_try_advisory_lock(%s)",
                            (ARBEIDERNOKKEL,)).fetchone()[0]
    if not fikk_lås:
        # HOPPET OVER, ikke vellykket. Et rent standardresultat her ville
        # sett ut som en kjøring som fant null avvik, og
        # kalleren ville persistert feiltellingen 0 — altså slettet en
        # alt opptelt feil ved hver overlappende aktivering, og alarmen
        # etter to sammenhengende feil ville aldri nådd frem.
        res.hoppet_over = True
        return res
    try:
        # KONTRAKTEN VALIDERES FØR COMMIT, og på ALLE radene.
        #
        # Døren returnerer NØYAKTIG ÉN rad. Ingen rad er ikke «null
        # funn» — det er en dør som ikke oppførte seg som kontrakten, og
        # da skal kjøringen si feilet framfor å rapportere nuller den
        # ikke har målt («en jobb som ikke kunne måle rapporterer FUNN,
        # aldri null»). FLERE rader er den samme feilen fra motsatt
        # kant, og `fetchone()` ville tiet om den.
        #
        # REKKEFØLGEN ER DOMMEN: den forrige formen committet FØRST og
        # oppdaget så at raden manglet — altså en transaksjon som ble
        # stående mens kjøringen rapporterte feilet. Nå rulles den
        # tilbake, og bare en validert kontrakt committes.
        try:
            rader = conn.execute(
                "SELECT * FROM m25_sveip_prosjekter(%s)",
                (grense,)).fetchall()
        except Exception:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        if len(rader) != 1:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        rad = rader[0]
        try:
            conn.commit()
        except Exception:
            _rull_tilbake(conn)
            res.feilet = True
            res.alarm_utlost = tidligere_feil + 1 >= ALARM_ETTER_FEIL
            return res
        (res.tenanter, res.nye, res.oppdaterte, res.lukkede,
         res.avkortet) = (int(rad[0]), int(rad[1]), int(rad[2]),
                          int(rad[3]), int(rad[4]))
        return res
    finally:
        # Opplåsingen er BEST EFFORT. Er tilkoblingen borte, feiler også
        # denne — og et unntak herfra ville erstattet resultatet kalleren
        # skal rapportere og persistere telleren fra. Låsen er
        # sesjonsscopet: en død sesjon slipper den uansett.
        try:
            conn.execute("SELECT pg_advisory_unlock(%s)", (ARBEIDERNOKKEL,))
            conn.commit()
        except Exception:
            pass


def _rull_tilbake(conn) -> None:
    """Rollback som aldri kaster. En død tilkobling kan ikke rulles tilbake."""
    try:
        conn.rollback()
    except Exception:
        pass
