"""GET /v1/oppsett — hvor langt firmaet er kommet med å sette opp modulene.

Dashbordets «Kom i gang» spør om én ting per tildelt modul: er kravet
satt, og hvor mange funn står åpne? Svaret leses gjennom modulenes EGNE
lesedører (`<modul>.svar_for`), aldri gjennom en kopi av spørringene —
en kopi ville drevet fra modulen den beskriver.

To porter før en modul kommer med: den må være TILDELT tenanten (samme
utledning som `/v1/utrulling`), og økten må ha modulens EGET lesescope.
Ellers ville oversikten vist tall fra en modul brukeren ikke får åpne.

Hver modul leses i sitt eget savepoint. Feiler én, rulles bare den
tilbake og meldes `tilgjengelig: false`; de andre står.
"""
from __future__ import annotations

import importlib

import psycopg

# (modulnummer, api-modul, lesescope, klar-flagg i `sammendrag`).
# `None` som klar-flagg: modulen har ingen egen «krav satt»-tilstand i
# sammendraget, og da sier oversikten bare antall åpne funn.
ADAPTERE: tuple[tuple[int, str, str, str | None], ...] = (
    (13, "avstemming", "okonomi:read", None),
    (14, "faktura", "okonomi:read", "har_terskel"),
    (17, "kundeservice", "decisions:read", None),
    (18, "onboarding", "decisions:read", None),
    (19, "adresse", "okonomi:read", "har_krav"),
    (23, "fordring", "okonomi:read", "har_purreplan"),
    (24, "leverandor", "okonomi:read", "har_terskel"),
    (25, "prosjekt", "okonomi:read", "har_terskel"),
    (26, "prisbok", "okonomi:read", "har_terskel"),
    (27, "lager", "okonomi:read", "har_terskel"),
    (39, "lonn", "okonomi:read", "har_terskel"),
    (41, "betaling", "okonomi:read", "har_terskel"),
    (42, "kontovakt", "okonomi:read", "har_terskel"),
    (44, "kampanje", "okonomi:read", None),
    (46, "anbud", "okonomi:read", "har_profil"),
    (47, "myndighetsrapport", "okonomi:read", "har_krav"),
    (48, "motpart", "okonomi:read", "har_krav"),
    (49, "sanksjon", "okonomi:read", "har_krav"),
    (50, "postjournal", "okonomi:read", "har_krav"),
    (51, "tilskudd", "okonomi:read", "har_krav"),
    (52, "tollkode", "okonomi:read", "har_krav"),
    (54, "ehf", "okonomi:read", "har_krav"),
    (55, "merkevare", "okonomi:read", "har_krav"),
)


# Anbefalte grenser = skjemaets egne DEFAULT-verdier i modulens
# migrasjon — tallene modulens forfatter valgte. De skrives aldri av seg
# selv: kunden ser dem og trykker «Bruk», og skrivingen går gjennom
# modulens EGEN skrivevei (samme scope, idempotens og revisjonslogg som
# skjemaet). `test_anbefalt_er_skjemaets_default` holder dem like.
ANBEFALT: dict[int, tuple[str, dict[str, int]]] = {
    14: ("/v1/faktura/terskler", {
        "mva_slingring_ore": 1, "belopsgrense_ore": 2500000,
        "kontrollfrist_dogn": 7, "dublettvindu_dogn": 3}),
    24: ("/v1/leverandor/terskler", {
        "prisstigning_promille": 100, "sla_brudd_grense": 1,
        "avtale_varsel_dogn": 30, "maling_stillhet_dogn": 90}),
    25: ("/v1/prosjekt/terskler", {
        "budsjettvarsel_promille": 0, "milepael_frist_dogn": 7,
        "stillhet_dogn": 30}),
    26: ("/v1/prisbok/terskler", {
        "rabattgrense_promille": 100, "utlop_varsel_dogn": 30,
        "uten_pris_dogn": 7}),
    27: ("/v1/lager/terskler", {
        "stille_dogn": 180, "uten_punkt_dogn": 30,
        "telleintervall_dogn": 365}),
    39: ("/v1/lonn/terskler", {
        "normaltid_minutter_dag": 450, "normaltid_minutter_uke": 2250,
        "avvik_minutter": 0, "uten_plan_dogn": 7,
        "vurderingsvindu_dogn": 60}),
    41: ("/v1/betaling/terskler", {
        "uavklart_dogn": 3, "belopsavvik_ore": 0,
        "reautorisasjon_dogn": 7}),
    42: ("/v1/kontovakt/terskler", {
        "reverifikasjon_dogn": 365, "uverifisert_dogn": 7}),
}
SKRIVESCOPE = "bestilling:opprett"


def les_modul(conn, tenant: str, apimodul: str, klarflagg: str | None) -> dict:
    """Én moduls status, i eget savepoint. Kaster aldri psycopg-feil videre."""
    modul = importlib.import_module(f"{__package__}.{apimodul}")
    try:
        with conn.transaction():
            svar = modul.svar_for(conn, tenant)
    except psycopg.Error:
        return {"tilgjengelig": False, "klar": None, "apne_funn": None}
    s = svar.get("sammendrag") or {}
    funn = s.get("apne_funn")
    return {"tilgjengelig": True,
            "klar": None if klarflagg is None else bool(s.get(klarflagg)),
            "apne_funn": funn if isinstance(funn, int) else None}


def svar_for(conn, tenant: str, scopes, tildelte) -> dict:
    """Status for hver tildelt modul økten får lese. `tildelte` er
    modulnumrene fra utrullingen; `None` betyr ukjent tildeling, og da
    vises ingenting heller enn alt."""
    scopes = set(scopes or ())
    tildelt = set(tildelte or ())
    moduler = []
    for nr, apimodul, scope, klarflagg in ADAPTERE:
        if nr not in tildelt or scope not in scopes:
            continue
        rad = {"modul": nr, **les_modul(conn, tenant, apimodul, klarflagg)}
        if (rad["klar"] is False and nr in ANBEFALT
                and SKRIVESCOPE in scopes):
            sti, kropp = ANBEFALT[nr]
            rad["anbefalt"] = {"sti": sti, "kropp": dict(kropp)}
        moduler.append(rad)
    return {"moduler": moduler}


def oppsett(tjeneste, request):
    """GET /v1/oppsett (decisions:read — alle kunderoller har det)."""
    from . import utrulling as utrullingsmodul
    from .lesing import _les, _utrulling_fra_firmaet, kanonisk_json

    def _fn(conn, auth, rid):
        nytt = None
        if utrullingsmodul.egen_rad(auth.tenant) is None:
            nytt = _utrulling_fra_firmaet(conn, auth.tenant, None)
        tildeling = utrullingsmodul.svar_for(auth.tenant, auth.scopes,
                                             nytt_firma=nytt)["moduler"]
        svar = svar_for(conn, auth.tenant, auth.scopes, tildeling)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "decisions:read", _fn)
