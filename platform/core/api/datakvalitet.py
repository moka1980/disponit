"""M-3 datakvalitet — leseflatens data (092).

AVLESNING, ALDRI ANALYSE. Modulen utleder ingenting og regner ingenting
om: hvert tall her står i en rad `m3_profiler` alt har committet, og den
eneste formingen som skjer er GRUPPERINGEN av dørens flate rader
(kjøring × regel) til én kjøring med sine profiltall — presentasjon, som
etter oversikt-lærdommen bor i API-laget og ikke i SQL.

TO AUTORITETER PÅ SAMME RUTE (`/v1/utrulling`-presedensen):

  * `security:read` — kundens/ops-øktens egen tilstand: registeret (som
    er globalt og uten tenantdata), kjøringenes hoder (plattformskop:
    fire tall om målingen selv) og TENANTENS EGNE profiltall og funn.
    Isolasjonen er `tenant_isolasjon` i basen, ikke et filter her.
  * `platform:admin` — plattformdriften ser I TILLEGG funnlisten på
    tvers av tenanter. Utvidelsen avgjøres HER, inne i endepunktet, av
    samme grunn som utrullingsplanen: det er en utvidelse av svaret, ikke
    en annen inngang. Scopet er ikke i `LESESCOPES`, så en browsersesjon
    når det aldri (verifisert i `app.py::_autentiser`) — og det er
    riktig: dette er en maskin-/ops-autoritet.

DEN BÆRENDE REGELEN FØLGER MED UT. `umaalbare_regler` sendes videre per
kjøring, slik at flaten kan si «ikke målt» ved riktig regel i stedet for
å tegne en tom celle som ser ut som null avvik. Uten det ville
«0 tomme felt» fordi målingen ikke kjørte sett ut som en grønn profil på
skjermen — nøyaktig løgnen modulen finnes for å ikke fortelle.

INGEN MUTASJON FINNES SOM HTTP. Profileringen skrives av
`disponit-kvalitetsprofil.service` med rollen `disponit_kvalitetsmaaler`,
en fullmakt web-API-et ikke har og ikke skal ha, og registeret endres
kun i migrasjon.
"""
from __future__ import annotations

#: Plattformdriftens autoritet. Skilt fra `security:read`, som er en
#: TENANTBUNDET ops/compliance-scope på en kundesesjon (PR-008 §1) — den
#: sier ingenting om rett til å se andre kunders funn.
PLATTFORMDRIFT = "platform:admin"

#: Taket på hvor mange KJØRINGER flaten henter. Dørene klemmer selv til
#: sine egne spenn; tallet her er standarden når klienten ikke ber om noe.
#: Dette er en AVLESNING, ikke en paginert logg.
GRENSE_STANDARD = 10
#: Taket på funnlister. Funnlisten vokser ikke med kadensen (ett funn per
#: regel/tenant/funntype), så den er kort per konstruksjon.
FUNNGRENSE = 200


def _ts(verdi) -> str | None:
    return verdi.isoformat() if verdi is not None else None


def _andel(verdi) -> float | None:
    """`numeric` kommer tilbake som Decimal og er ikke JSON-serialiserbar.

    `float` er riktig her og skjuler ingenting: dette er en ANDEL i
    [0, 1] regnet av basen som en generert kolonne, ikke et beløp. Den
    følges alltid av sine to tellere (`rader_vurdert`, `rader_avvik`), så
    flaten kan vise tallet og ikke bare fargen.
    """
    return None if verdi is None else float(verdi)


def regler(conn, tenant: str) -> list[dict]:
    """Registeret. Globalt, uten tenantdata — men bak tenantkontekst,
    fordi RETTEN til å spørre er øktens selv når dataene ikke er det."""
    rader = conn.execute("SELECT * FROM m3_regelregister(%s)",
                         (tenant,)).fetchall()
    return [{"regel_id": r[0], "relasjon": r[1], "kolonne": r[2],
             "regeltype": r[3], "uttrykk": r[4], "alvorlighet": r[5],
             "terskel_andel": _andel(r[6]), "begrunnelse": r[7]}
            for r in rader]


def kjoringer(conn, tenant: str, grense: int = GRENSE_STANDARD) -> list[dict]:
    """Siste kjøringer med tenantens EGNE profiltall.

    Døren gir FLATE rader (kjøring × regel) fordi grupperingen er
    presentasjon. Den skjer her, og den bevarer dørens rekkefølge: nyeste
    kjøring først, reglene alfabetisk innenfor hver. En kjøring UTEN
    profilrader for denne tenanten står fortsatt i listen — den skjedde,
    og hodet er den ærligste delen av svaret.
    """
    rader = conn.execute("SELECT * FROM m3_kvalitetsprofil(%s,%s)",
                         (tenant, grense)).fetchall()
    ut: list[dict] = []
    indeks: dict[str, dict] = {}
    for (kid, startet, fullfort, n_regler, n_umaalbare, n_funn,
         umaalbare, avbrutt, alder_s, regel_id, vurdert, avvik,
         andel) in rader:
        nokkel = str(kid)
        k = indeks.get(nokkel)
        if k is None:
            k = {"kjoring_id": nokkel, "startet_ts": _ts(startet),
                 "fullfort_ts": _ts(fullfort), "alder_s": alder_s,
                 "antall_regler": n_regler,
                 "antall_umaalbare": n_umaalbare,
                 "antall_funn": n_funn,
                 # Navnene, ikke bare tallet: flaten skal kunne si «ikke
                 # målt» ved RIKTIG regel.
                 "umaalbare_regler": list(umaalbare or ()),
                 "avbrutt": bool(avbrutt),
                 "profiler": []}
            indeks[nokkel] = k
            ut.append(k)
        # LEFT JOIN-en gir én rad med NULL-regel for en kjøring uten
        # tenantrader. Den er ikke en profil og skal ikke bli en tom rad
        # i tabellen.
        if regel_id is not None:
            k["profiler"].append({
                "regel_id": regel_id, "rader_vurdert": vurdert,
                "rader_avvik": avvik, "andel_avvik": _andel(andel)})
    return ut


def egne_funn(conn, tenant: str, grense: int = FUNNGRENSE) -> list[dict]:
    """Tenantens egne funn. Ren RLS — ingen tenantkolonne i svaret,
    fordi det bare finnes én tenant å snakke om her."""
    rader = conn.execute("SELECT * FROM m3_kvalitetsfunn(%s,%s)",
                         (tenant, grense)).fetchall()
    return [{"regel_id": r[0], "funntype": r[1], "forst_sett_ts": _ts(r[2]),
             "sist_sett_ts": _ts(r[3]), "ganger_sett": r[4],
             "detaljer": r[5] if isinstance(r[5], dict) else {}}
            for r in rader]


def tverrgaaende_funn(conn, tenant: str, grense: int = FUNNGRENSE) -> list[dict]:
    """Funnlisten på tvers — KUN for `platform:admin`.

    Kalles bare når endepunktet har avgjort at økten har
    plattformdriftens autoritet. Døren åpner sitt kryss-tenant-vindu
    lokalt i transaksjonen og lukker det med den; policyen er
    `FOR SELECT`, så vinduet kan ikke bli en skrivevei uansett.
    """
    rader = conn.execute(
        "SELECT * FROM m3_kvalitetsfunn_tverrgaaende(%s,%s)",
        (tenant, grense)).fetchall()
    return [{"tenant": r[0], "regel_id": r[1], "funntype": r[2],
             "forst_sett_ts": _ts(r[3]), "sist_sett_ts": _ts(r[4]),
             "ganger_sett": r[5],
             "detaljer": r[6] if isinstance(r[6], dict) else {}}
            for r in rader]


def svar(conn, tenant: str, scopes, grense: int = GRENSE_STANDARD) -> dict:
    """Hele flatens data i ett svar.

    `plattformdrift` står i svaret fordi flaten må kunne SI at den viser
    en tverrgående liste — en tabell som stille inneholder andre kunders
    rader er verre enn en som sier hvorfor den gjør det.
    """
    plattformdrift = PLATTFORMDRIFT in set(scopes or ())
    ut = {
        "plattformdrift": plattformdrift,
        "regler": regler(conn, tenant),
        "kjoringer": kjoringer(conn, tenant, grense),
        "funn": egne_funn(conn, tenant),
    }
    # Uten plattformdrift finnes NØKKELEN ikke — ikke en tom liste. En
    # tom liste ville lest som «ingen funn på tvers», og det er ikke det
    # svaret sier.
    if plattformdrift:
        ut["tverrgaaende_funn"] = tverrgaaende_funn(conn, tenant)
    return ut
