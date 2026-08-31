"""Roller → scopes (PR-010 v5 §4).

`brukermedlemskap.roller[]` er ENESTE autoritet. Scopes AVLEDES her fra et
LUKKET rollemønster — de lagres aldri som egen kolonne (som ville drevet
fra rollene). Endres en brukers roller, økes `authz_version` av
DB-triggeren, alle sesjoner ugyldiggjøres, og neste innlogging får de nye
scopene utledet på nytt herfra.

Scope-navnene er PR-008s lese-scopes. `test_rolle_scopes_er_kjente`
binder utledningen mot den kanoniske LESESCOPES-mengden, så en rolle ikke
kan gi et scope som ikke finnes.
"""
from __future__ import annotations

#: Lukket mønster. En ukjent rolle gir INGEN scopes (default-deny) — den
#: er ikke en feil, men den åpner ingenting.
ROLLE_TIL_SCOPES: dict[str, frozenset[str]] = {
    # Vanlig kundebruker: ser beslutninger, unntak og policy — ikke
    # sikkerhetskøen.
    # M-6 PR-A: `epost:read` — lese klassifiseringer/utkast/oppfølging i
    # M-6-flaten (PR-D). Rent lesende, derfor også hos leser/sikkerhet.
    # 089 (M-35): kontinuitetsstatusen — tjenestekart, kontakter,
    # hendelser og siste øvelse — er tenantens egen beredskapsinnsikt,
    # samme leseklasse som beslutningene. Lesescopet gis derfor til alle
    # kunderollene som leser tilstand; SKRIVINGEN (hendelser, poster,
    # lukking) er admin-myndighet alene.
    "leser": frozenset({"decisions:read", "exceptions:read", "policy:read",
                        "epost:read", "kontinuitet:read"}),
    # Compliance/ops: i tillegg sikkerhetsinnsyn.
    "sikkerhet": frozenset({"decisions:read", "exceptions:read",
                            "policy:read", "security:read",
                            "epost:read", "kontinuitet:read"}),
    # Administrator: alt lesende (v1 er rent lese-API; mutasjon er senere).
    # 038: administratoren bestiller kontroller på tenantens egne,
    # verifiserte mål. Scopet gir retten til å FORSØKE — målautorisasjon,
    # policy og frekvens avgjør (bestilleren velger aldri modul/frist/epoch).
    # 044: planen er tenantens (§6) — administratoren oppretter, aktiverer
    # og gjenopptar. Én rolle i v1; en tenant kan senere splitte
    # aktiver/gjenoppta til egne roller uten skjemaendring.
    # M-6 PR-A: administratoren forvalter kildene (koble til/deaktiver
    # postboks — OAuth-flyten i PR-B) og feller flatens dom over utkast
    # (forkast/brukt manuelt — PR-D). Begge er per-handling-scopes, som
    # PR-012s unntaksbehandling: retten til å FORSØKE; 088-vaktene og
    # statusmaskinene avgjør.
    # 089 (M-35): administratoren eier kontinuitetsregisteret og
    # hendelseshåndteringen — write dekker kartinnslag, kontakter,
    # hendelser, tidslinjeposter og lukking (dørene håndhever resten:
    # append-only, etteranalyse-kravet, SP-2).
    "admin": frozenset({"decisions:read", "exceptions:read", "policy:read",
                        "security:read", "bestilling:opprett",
                        "plan:opprett", "plan:aktiver", "plan:gjenoppta",
                        "epost:read", "epost:kilde:administrer",
                        "epost:utkast:behandle",
                        "kontinuitet:read", "kontinuitet:write"}),
    # PR-012: godkjenner kan behandle unntakskøen — den FØRSTE muterende
    # browserrollen. Scopene er per-handling (approve/reject/escalate) så et
    # reject-scope aldri kan godkjenne (v3-test).
    "godkjenner": frozenset({"decisions:read", "exceptions:read",
                             "exceptions:approve", "exceptions:reject",
                             "exceptions:escalate"}),
    # PR-013: policyforvalteren redigerer utkast OG attesterer aktivering.
    # `policy:write` og `policy:activate` er adskilte scopes: fire-øyne (V6)
    # hviler på at aktivering krever attestasjoner, ikke på at rollen mangler
    # skrivetilgang — men en tenant KAN gi to ulike personer hver sin rolle
    # (kun-skrive vs. kun-aktivere) ved å definere snevrere roller senere.
    "policyforvalter": frozenset({"decisions:read", "policy:read",
                                  "policy:write", "policy:activate"}),
    # PR-015 §3: cross-tenant domeneautoritet er sin EGEN rolle, og den bærer
    # BEVISST ikke `exceptions:approve`/`reject`/`escalate`. En som kan behandle
    # unntakskøen skal ikke dermed kunne avgjøre hvilken kunde plattformen
    # autoriserer for et domene — «`exceptions:handle` alene gir aldri
    # cross-tenant domeneautoritet». Rollen leser saken (`exceptions:read`) og
    # attesterer utfallet; motoren gjør overgangen.
    "domeneadjudikator": frozenset({"decisions:read", "exceptions:read",
                                    "domains:adjudicate"}),
}


def scopes_for_roller(roller) -> frozenset[str]:
    """Unionen av scopes for brukerens roller. Ukjente roller bidrar med
    ingenting (default-deny)."""
    ut: set[str] = set()
    for rolle in roller or ():
        ut |= ROLLE_TIL_SCOPES.get(rolle, frozenset())
    return frozenset(ut)
