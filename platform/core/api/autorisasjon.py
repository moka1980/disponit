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
    "leser": frozenset({"decisions:read", "exceptions:read", "policy:read"}),
    # Compliance/ops: i tillegg sikkerhetsinnsyn.
    "sikkerhet": frozenset({"decisions:read", "exceptions:read",
                            "policy:read", "security:read"}),
    # Administrator: alt lesende (v1 er rent lese-API; mutasjon er senere).
    "admin": frozenset({"decisions:read", "exceptions:read", "policy:read",
                        "security:read"}),
    # PR-012: godkjenner kan behandle unntakskøen — den FØRSTE muterende
    # browserrollen. Scopene er per-handling (approve/reject/escalate) så et
    # reject-scope aldri kan godkjenne (v3-test).
    "godkjenner": frozenset({"decisions:read", "exceptions:read",
                             "exceptions:approve", "exceptions:reject",
                             "exceptions:escalate"}),
}


def scopes_for_roller(roller) -> frozenset[str]:
    """Unionen av scopes for brukerens roller. Ukjente roller bidrar med
    ingenting (default-deny)."""
    ut: set[str] = set()
    for rolle in roller or ():
        ut |= ROLLE_TIL_SCOPES.get(rolle, frozenset())
    return frozenset(ut)
