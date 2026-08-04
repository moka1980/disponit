"""Feilveitabellen fra v2 Del 4 som DATA, ikke som prosa.

Hele kontrakten står som én tabell her, og både app.py, kjerne.py og
testene leser den samme tabellen. Grunnen er konkret: spesifikasjonen
krever «én test per rad», og en tabell som bare finnes i et
markdown-dokument kan ikke telles opp av en test. Med tabellen i kode kan
testsuiten iterere over den og bevise at hver rad har både en kodevei og
en test — og at ingen rad forsvinner stille.

Routing (v2 Del 4 + v3-delta pkt. 5):
  avvis      — kun HTTP-svar, ingen sak, ingen sikkerhetslogg
  sikkerhet  — strukturert sikkerhetslogg + metric. `sakstype` sier om det
               I TILLEGG opprettes en M-37-sak (v2s «sikkerhet +
               m37-referanse»); er den None, finnes det ingen loggpost å
               feste saken til, og da blir det logg/metric alene.
  drift      — alarm + nødlogg. Samme regel for `sakstype`: sak kun når
               tenanten er identifisert (v3-delta pkt. 5).
  m37        — ordinær unntakskø, `sakstype='normal'`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feilvei:
    kode: str
    http: int
    routing: tuple[str, ...]
    sakstype: str | None = None      # unntak.sakstype; None = ingen rad
    aggregert: bool = False          # samles i metric, aldri én sak per treff
    beslutningssvar: bool = False    # 200 med STOPP i kroppen, ikke feilkropp
    notat: str = ""


#: Radene fra v2 Del 4, i samme rekkefølge som i spesifikasjonen.
FEILVEIER: tuple[Feilvei, ...] = (
    Feilvei("token_ugyldig", 401, ("sikkerhet",), None, notat=(
        "Ingen tenant-sak, ingen policylasting: tenanten er ukjent, og en"
        " sak hos feil tenant er verre enn ingen sak.")),
    Feilvei("scope_mangler", 403, ("sikkerhet",), None),
    Feilvei("idempotensnokkel_mangler", 400, ("avvis",), None),
    Feilvei("idempotenskonflikt", 409, ("avvis",), None, aggregert=True),
    Feilvei("body_for_stor", 413, ("sikkerhet",), None, aggregert=True),
    Feilvei("body_lengde_ugyldig", 411, ("sikkerhet",), None, aggregert=True),
    Feilvei("request_feilformet", 400, ("sikkerhet",), None, aggregert=True,
            notat="ALDRI full payload i sak eller logg."),
    Feilvei("policy_ukjent", 404, ("avvis",), None, notat=(
        "Samme svar enten policyen ikke finnes eller tilhører en annen"
        " tenant — ellers er 404/403 et oppslagsverk over andres policyer.")),
    Feilvei("policy_korrupt", 500, ("drift", "m37"), "drift"),
    Feilvei("register_utilgjengelig", 503, ("drift",), None),
    Feilvei("db_utilgjengelig", 503, ("drift",), None),
    Feilvei("attestasjon_signatur_ugyldig", 200, ("sikkerhet", "m37"),
            "sikkerhet", beslutningssvar=True),
    Feilvei("attestasjon_feil_binding", 200, ("sikkerhet", "m37"),
            "sikkerhet", beslutningssvar=True),
    Feilvei("attestasjon_replay", 200, ("sikkerhet", "m37"), "sikkerhet",
            beslutningssvar=True),
    Feilvei("verifikator_ikke_betrodd", 200, ("sikkerhet", "m37"),
            "sikkerhet", beslutningssvar=True, notat=(
                "IKKE ordinær kø — kø-flom-vern (v2 Del 4).")),
    Feilvei("unntak", 200, ("m37",), "normal", beslutningssvar=True),
    Feilvei("stopp_frys", 200, ("m37",), "normal", beslutningssvar=True,
            notat="prioritet=hoy"),
    Feilvei("policyfeil_handlingsbar", 200, ("m37",), "normal",
            beslutningssvar=True),
    Feilvei("unntaksskriv_feilet", 500, ("drift",), None, notat=(
        "Transaksjonen rulles — loggposten committes IKKE, svaret er STOPP.")),
    Feilvei("logging_feilet", 500, ("drift",), None, notat="+ nødlogg, 4.1"),
    Feilvei("tenantnokkel_mangler", 500, ("drift",), None, notat=(
        "Rollback, STOPP — ALDRI klartekstlagring.")),
    Feilvei("rate_grense", 429, ("sikkerhet",), None, aggregert=True),
    Feilvei("cursor_ugyldig", 400, ("sikkerhet",), None),

    # --- PR-006: arbeidskapabiliteter og oppdrag ------------------------
    # Kapabilitetsfeil er 401/403 og IKKE beslutningssvar. En kapabilitet
    # som ikke holder er en autentiseringsfeil, ikke en beslutning om en
    # handling — og en sak her ville dessuten vært på en tenant vi ikke har
    # verifisert at forespørselen tilhører.
    Feilvei("kapabilitet_ugyldig", 401, ("sikkerhet",), None, notat=(
        "Ukjent, utløpt, allerede brukt, eller reservert av en ANNEN"
        " request_id. Samme svar i alle fire tilfellene: en klient som kan"
        " skille dem fra hverandre har et orakel.")),
    Feilvei("kapabilitet_feil_handling", 403, ("sikkerhet",), None, notat=(
        "event.handling != kapabilitetens tillatt_handling. Kapabiliteten"
        " er bundet til ÉN handling ved utstedelse.")),
    Feilvei("kapabilitet_feil_idempotensnokkel", 403, ("sikkerhet",), None,
            notat=("Idempotency-Key må VÆRE repair_operation_id. Ellers kan"
                   " samme kapabilitet gi to ulike forretningshandlinger.")),
    Feilvei("kapabilitet_fencing_tapt", 409, ("drift",), None, notat=(
        "Kapabiliteten var gyldig, men sakens claim/generasjon/lease holder"
        " ikke lenger. Revalidering mot unntaksraden, ikke bare mot"
        " utstedelsesverdiene (v4-delta pkt. 1.2).")),
    Feilvei("oppdrag_tomt", 204, ("avvis",), None, notat=(
        "Ingen oppdrag å plukke. 204 og ikke 404: køen finnes, den er tom.")),
    Feilvei("kvittering_signatur_ugyldig", 403, ("sikkerhet",), "sikkerhet",
            notat="Ugyldig signatur => sikkerhetssak, INGEN statusendring."),
    Feilvei("kvittering_konflikt", 409, ("sikkerhet",), "sikkerhet", notat=(
        "To ulike resultathasher for samme oppdrag. Aldri «siste vinner».")),
    Feilvei("kvittering_for_sen", 410, ("avvis",), None, notat=(
        "Etter evidensfristen. Administrativ import er utenfor PR-006.")),
    Feilvei("modul_inaktiv", 503, ("drift",), None, notat=(
        "Rollback-kontrakten (rollback-m01-v1): modulen er deaktivert i"
        " registeret, og API-et svarer definert i stedet for å feile.")),
)

FEIL: dict[str, Feilvei] = {f.kode: f for f in FEILVEIER}


# ---------------------------------------------------------------------------
# Fra Grunn-kode til sakstype. Motoren og attestasjonsporten produserer
# Grunn-koder; denne tabellen er det ENESTE stedet som oversetter dem til
# routing. Lå oversettelsen spredt i kjerne.py ville en ny Grunn-kode blitt
# rutet til «normal» ved et uhell — og en signaturforfalskning havnet i
# saksbehandlernes ordinære kø.
# ---------------------------------------------------------------------------

#: Brudd på attestasjonsporten. Alle er sikkerhetssaker, aldri normal kø.
SIKKERHETSKODER = frozenset({
    "attestasjoner_feilformet", "attestasjon_feilformet",
    "attestasjon_uten_signatur", "attestasjon_signatur_ugyldig",
    "attestasjon_mangler_binding", "attestasjon_feil_tenant",
    "attestasjon_feil_handling", "attestasjon_feil_vilkaar",
    "attestasjon_feil_policy", "attestasjon_feil_ressurs",
    "attestasjon_tid_ugyldig", "attestasjon_utenfor_gyldighet",
    "attestasjon_jti_ugyldig", "attestasjon_replay",
    "verifikator_ikke_betrodd",
    # PR-006 §4: lukket kanoniseringsformat. En attestasjon signert med en
    # ANNEN kanonisering er ikke en formfeil å rette — det er noen som har
    # signert andre bytes enn vi verifiserer.
    "attestasjon_kanonisering_ukjent",
})

#: Feil i plattformen selv, ikke i forespørselen.
DRIFTSKODER = frozenset({"policy_korrupt", "motor_exception"})

#: Feil i POLICYEN — noen må rette et dokument. Handlingsbart, altså
#: ordinær kø (v2 Del 4: «Autentisert, handlingsbar policyfeil ... m37»).
POLICYFEILKODER = frozenset({
    "policy_belopsgrense_ugyldig", "policy_tidssone_ugyldig",
    "frekvens_uten_tellerlager",
})


def sakstype_for(beslutning: str, siste_grunn: str | None,
                 effekt: str | None) -> tuple[str | None, str]:
    """-> (sakstype, prioritet). sakstype None == ingen M-37-sak.

    `siste_grunn` er den SISTE begrunnelseskoden, ikke en vilkårlig av dem.
    Motorens `blokker()` legger alltid den blokkerende grunnen sist, mens
    alt foran er `*_ok`-kvitteringer. Ser man etter «finnes en
    sikkerhetskode blant begrunnelsene», treffer man også en forespørsel som
    passerte attestasjonene og ble stoppet av noe helt annet.
    """
    if siste_grunn in SIKKERHETSKODER:
        # Går FØR unntaks-/frys-sjekken med vilje: en attestasjon som ikke
        # holder er en sikkerhetssak selv om policyen sier «unntakskø».
        return "sikkerhet", "hoy"
    if siste_grunn in DRIFTSKODER:
        return "drift", "hoy"
    if beslutning == "UNNTAK":
        return "normal", "normal"
    if beslutning == "STOPP":
        if siste_grunn in POLICYFEILKODER:
            return "normal", "hoy"
        if effekt == "frys":
            return "normal", "hoy"
        if effekt == "varsle":
            # `stopp_og_varsle` står ikke som egen rad i v2 Del 4, men
            # effekten betyr per policy-skjemaet at noen skal varsles. Uten
            # sak finnes det ingen å varsle. Vi lager derfor saken —
            # bevisst i den strenge retningen, som ellers i motoren.
            return "normal", "normal"
    return None, "normal"
