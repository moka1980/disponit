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
    # --- PR-007: verifikasjonskvitteringen ------------------------------
    Feilvei("attestasjon_for_gammel", 403, ("sikkerhet",), "sikkerhet", notat=(
        "Policyens `maks_attestasjon_alder_s` er et TAK verifikatoren ikke"
        " kan heve med sitt eget `utloper` (v7 pkt. 1). Sikkerhetssak fordi"
        " en for gammel attestasjon som slipper gjennom er et faktum ingen"
        " lenger står inne for.")),
    Feilvei("modul_inaktiv", 503, ("drift",), None, notat=(
        "Rollback-kontrakten (rollback-m01-v1): modulen er deaktivert i"
        " registeret, og API-et svarer definert i stedet for å feile.")),
    # --- 035: modul-onboarding -------------------------------------------
    Feilvei("modulepoch_utdatert", 403, ("sikkerhet",), None, notat=(
        "Claim/rotasjonsbruk med en annen epoch enn modulens gjeldende."
        " ALLTID 403, aldri 204: «du har ikke lov» og «det finnes ikke"
        " arbeid» må aldri se like ut fra utsiden (035, port 18–19)."
        " Reaktivering krever ny onboarding; rotasjon plukker aldri opp"
        " ny epoch. Også svaret når en kapabilitet skal INNLØSES etter at"
        " et nødstopp har bumpet epoch: fullmakten var gyldig da den ble"
        " utstedt, men deploymenten er det ikke lenger.")),
    Feilvei("modul_ikke_claimbar", 403, ("sikkerhet",), None, notat=(
        "Modultokenets deployment er ikke `claiming`, eller modulen er"
        " ikke aktiv. Eksplisitt avslag (035, port 10): en draining"
        " release kan fortsatt kvittere og laste opp innen evidensfrist,"
        " men aldri claime — og det skal SIES, ikke se ut som tom kø."
        " På INNLØSNINGSveiene (kvittering/artefakt) leses kun modulens"
        " status, aldri livsløpet: nettopp fordi en draining deployment"
        " skal få levere ferdig, mens en nødstoppet ikke skal.")),
    Feilvei("onboarding_avvist", 403, ("sikkerhet",), None, notat=(
        "Innløsning avvist: ukjent id, feil hemmelighet, allerede brukt"
        " eller utløpt — SAMME svar utad for alle fire (035, port 4/6):"
        " et skille ville vært et orakel for gjettverk.")),
    # --- PR-014c: artefakt-skjemavalidering -------------------------------
    Feilvei("artefakt_skjemabrudd", 422, ("sikkerhet",), None, notat=(
        "Rapporten bryter artefakttypens skjema — avvist VED OPPLASTING,"
        " før kryptering (014c §8 pkt. 1). Sikkerhetslogg, ikke sak:"
        " modulen er autentisert, og et skjemabrudd fra en godkjent"
        " controller er noe drift skal SE. Detaljene står i loggen, aldri"
        " i svaret (innholdet kan bære persondata).")),
    Feilvei("artefaktskjema_mangler", 422, ("drift",), None, notat=(
        "Artefakttypens skjema_hash har ingen rad i artefaktskjema —"
        " konfigurasjonsfeil (typen ble registrert før 036s positive"
        " regel). Innhold ingen kan validere tas ikke imot; raden må"
        " registreres via registrer_artefaktskjema().")),
    # --- PR-014c v4: bestillingsveien -------------------------------------
    Feilvei("bestilling_hostname_uverifisert", 403, ("sikkerhet",), None,
            notat=(
        "Bestilling mot et hostname tenanten ikke har VERIFISERT "
        "domenekontroll for — avvist FØR beslutningen tas (038, port 9): "
        "positivt autorisert mål er ikke policyens ansvar, det er "
        "opprettelsens. Sikkerhetslogg: noen ba plattformen lese et mål "
        "de ikke eier.")),
    Feilvei("bestillingstype_utilgjengelig", 503, ("drift",), None, notat=(
        "Bestillingstypen er kodefestet, men oppdragstypen dens kan ikke "
        "CLAIMES nå: raden mangler i oppdragstype_register, har feil "
        "eiermodul, modulen står ikke 'aktiv', eller ingen deployment i "
        "dette miljøet er 'claiming'. Samme vilkår som "
        "claim_neste_oppdrag (037) — står ett av dem ikke, finnes det "
        "ingen arbeider som kan plukke oppdraget før utforelsesfristen. "
        "Nektes FØR beslutningen: et TILLAT her ville gitt et oppdrag "
        "ingen modul kan claime. 503, ikke 400: kroppen er velformet, det "
        "er PLATTFORMEN som mangler utføreren, og klienten skal prøve "
        "igjen når modulen er aktiv. Hvilket vilkår som sviktet står i "
        "driftsloggen (`grunn`), aldri i svaret — ellers er 503-en et "
        "kart over hvilke moduler som er nede.")),
    Feilvei("domene_challenge_avvist", 409, ("sikkerhet",), None, notat=(
        "utsted_challenge nektet (016): raden står i avklaring/overtakelse "
        "eller bryter en vakt der. 409, ikke 400: kroppen er velformet — "
        "det er domenets TILSTAND som ikke tillater ny utfordring nå.")),
    # --- PR-008: lese-API ----------------------------------------------
    Feilvei("ikke_funnet", 404, ("avvis",), None, notat=(
        "Detalj-ID som ikke finnes OG detalj-ID hos en annen tenant gir"
        " NØYAKTIG samme svar — ellers er 404/403 et oppslagsverk over"
        " andres saker (samme prinsipp som policy_ukjent).")),
    Feilvei("intern_feil", 500, ("drift",), None, notat=(
        "Sanitert 500 med korrelasjons-ID. Brukes når lese-API-ets"
        " servermodell møter en tilstand matrisen forbyr — den skal SES i"
        " driftsloggen, aldri forklares for klienten.")),
    # --- PR-010: OIDC-sesjon -------------------------------------------
    Feilvei("sesjon_ugyldig", 401, ("avvis",), None, notat=(
        "Utløpt/inaktiv/tilbakekalt sesjon, eller authz_version-avvik. UI"
        " viser innloggingsflaten. Lukket kode, aldri hvorfor.")),
    Feilvei("dobbel_principal", 400, ("sikkerhet",), None, notat=(
        "BÅDE sesjonscookie og Authorization-header. Ingen automatisk"
        " fallback mellom mekanismene (v2 §8) — requesten avvises.")),
    Feilvei("csrf_ugyldig", 403, ("sikkerhet",), None, notat=(
        "Mutasjon (logout m.fl.) med manglende/feil X-Disponit-CSRF mot"
        " sesjonens lagrede csrf_hash. Dobbel-innsending håndheves server-"
        " side: en fremmed eller fraværende token tilbakekaller ALDRI"
        " økten. Økten forblir urørt.")),
    Feilvei("ukjent_provider", 400, ("avvis",), None, notat=(
        "Ukjent workspace ELLER provider → samme generiske feil, ingen"
        " eksistenslekkasje (v2 §5).")),
    Feilvei("provider_utilgjengelig", 400, ("drift",), None, notat=(
        "Manglende credential eller discovery-/egress-feil → KUN denne"
        " provideren markeres utilgjengelig, fail-closed (v6 §4).")),
    Feilvei("ingen_tilgang", 401, ("sikkerhet",), None, notat=(
        "Autentisert identitet uten forhåndsmedlemskap. Ingen JIT (v3 §2);"
        " samme generiske avvisning som ukjent identitet.")),
    Feilvei("rate_grense_login", 429, ("sikkerhet",), None, aggregert=True,
            notat="Login-fase over grensen. Retry-After i sekunder (v4 §4)."),
    Feilvei("innlogging_feilet", 400, ("sikkerhet",), None, aggregert=True,
            notat=("Generisk callback-feilside. Gjengir ALDRI"
                   " URL-parametere (v5 §6).")),
    # PR-015 §4: fire øyne ved positiv cross-tenant domenetildeling.
    Feilvei("dobbel_attestasjon", 409, ("sikkerhet",), "sikkerhet", notat=(
        "Samme aktør forsøkte å avgi to stemmer på samme saksrevisjon."
        " Avvist av PRIMÆRNØKKELEN i overtakelse_attestasjon, ikke av UI-et."
        " Sikkerhetssak fordi et forsøk på å produsere begge øyne selv er"
        " nettopp det fire-øyne-kravet finnes for.")),
    Feilvei("attestasjon_avvist", 409, ("sikkerhet",), None, notat=(
        "Motoren nektet attestasjonen: saken er ikke i avklaring_kreves, eller"
        " revisjonen er foreldet av en nyere overtakelse. Ingen sak — dette er"
        " den normale utgangen når en konflikt rekker å bli avløst av en"
        " nyere, og attestasjonsraden er allerede bevart som evidens.")),
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
    # PR-014c: målbindingen. En hendelse som ber om ekstern lesing av et
    # ANNET mål enn autorisasjonen dekker, er ikke en formfeil å rette —
    # det er trafikk ut mot noe ingen har godkjent, med et bevis som ser
    # gyldig ut. Samme kø som en attestasjon på feil ressurs.
    "malautorisasjon_feil_mal", "malautorisasjon_mal_ugyldig",
})

#: Feil i plattformen selv, ikke i forespørselen.
DRIFTSKODER = frozenset({"policy_korrupt", "motor_exception",
                         # Et måldomene uten kjent hendelsesfelt er en
                         # kodefeil hos OSS: typen deklarerer et krav
                         # plattformen ikke vet hvordan den skal binde.
                         "malautorisasjon_domene_ukjent"})

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
