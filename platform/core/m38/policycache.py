"""M-38 — innholdsadressert valideringsmemoisering for policyregisteret.

Dette er IKKE en tilstandskopi av registeret. `hent_aktiv` beholder hele
lastekontrakten fra v2 1.5 per beslutning: den delte låsen
(`laas_policy_delt`, protokollen mot `slett_ubrukt_policy`), den fulle
radlesningen, `PolicyUkjent`-grenen, sha256-REKOMPUTERINGEN av innholdet
mot lagret hash og alle meta-kryssjekkene. Det ENESTE som spares er
skjemavandringen (`valider_policy`), nøklet på den rekomputerte hashen:
`valider_policy` er en ren funksjon av innholdet, så et innhold som har
bestått én gang består alltid — identiske bytes trenger ikke måles om
igjen.

Derfor finnes det INGEN invalideringsprotokoll, og det er ikke en
forglemmelse: nøkkelen er innholdet selv. Aktivering, rollback, sletting
og bootstrap endrer innholdet eller fjerner raden, og radlesningen i samme
transaksjon ser det — et nytt innhold er en ny nøkkel (full validering),
en fjernet rad er `PolicyUkjent` før memoiseringen i det hele tatt spørres.

KUN BESTÅTTE valideringer legges inn. En feilende policy re-måles ved hver
lasting, så feillisten i `PolicyKorrupt` alltid er fersk og komplett.

I minne, per prosess, bak lås — samme husmønster som `Rategrense`
(api/app.py): nullstilles ved restart og deles ikke mellom prosesser. For
en memoisering er det riktig uten kompensasjon: et tomt cache er bare den
første målingen om igjen.
"""
from __future__ import annotations

import threading

#: Taket på antall hasher som holdes samtidig — samme disiplin som
#: `Rategrense.NOKKELTAK`: en dict som bare vokser er en minneflate.
#: Innslagene er sha256-hasher av innhold som ALT har bestått validering,
#: så feiing er alltid trygt; prisen er én ny skjemavandring per innhold.
#: Uten et vindu å måle treff mot feies hele beholdningen når taket nås.
NOKKELTAK = 4096

_laas = threading.Lock()
_bestaatt: dict[str, bool] = {}


def er_validert(innholds_hash: str) -> bool:
    """Har NØYAKTIG dette innholdet bestått `valider_policy` før?

    `innholds_hash` skal være REKOMPUTERT av kalleren fra innholdet den
    faktisk leste — aldri hash-kolonnen. Det er det som gjør nøkkelen til
    innholdet selv, og korrupsjonskontrakten uavhengig av cachen.
    """
    with _laas:
        return _bestaatt.get(innholds_hash, False)


def merk_validert(innholds_hash: str) -> None:
    """Registrer en BESTÅTT validering. Kalles aldri for en feilende."""
    with _laas:
        if len(_bestaatt) >= NOKKELTAK and innholds_hash not in _bestaatt:
            _bestaatt.clear()
        _bestaatt[innholds_hash] = True


def toem() -> None:
    """Fei hele memoiseringen. Test-støtte; driften trenger den aldri."""
    with _laas:
        _bestaatt.clear()
