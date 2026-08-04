"""Backfill av policysnapshotet på eksisterende unntak (GO-vilkår V2).

Kjøres av `deploy/staging/migrer.py` MELLOM migrasjon 005 (som legger
kolonnene nullable) og 006 (som setter NOT NULL). Rekkefølgen er porten:
uteblir denne, feiler 006 høylytt i stedet for å etterlate et halvt
snapshot ingen legger merke til.

Hvorfor i Python og ikke i migrasjonsfilen: kravet er å RE-HASHE lagret
policyinnhold og sammenligne mot revisjonsloggen før `maks_auto_forsok`
brukes. Den kanoniske hashen er definert av
`api.policyregister.innholds_hash` (sorterte nøkler, ensure_ascii av,
ingen mellomrom). En andre implementasjon i PL/pgSQL ville vært to kopier
av en sikkerhetskritisk regel — nøyaktig duplikatformen som ga P1 nr. 4 i
PR-002, og som siden har kostet oss funn i hver eneste PR den har fått
stå.

REGELEN (v4 pkt. 5, skjerpet av GO-vilkår V2): aktiv policy brukes ALDRI
som backfill-kilde. Evidensen er revisjonsloggposten saken faktisk peker
på. Finnes ikke den historiske policyraden, stemmer ikke re-hashen, eller
består ikke innholdet valideringen — da får saken eksplisitte
legacy-verdier og settes til `manuell`. Å gjette er verre enn å innrømme
at vi ikke vet.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import psycopg

from policy_validator.engine import les_policyref

LEGACY = "legacy"
AKTOR = "migrasjon:m37-backfill"

#: Backfillen kjører ETTER denne migrasjonsversjonen og FØR den neste.
#: 005 legger kolonnene nullable, 006 setter NOT NULL. Konstanten står her
#: og ikke i `migrer.py` fordi rekkefølgen er denne modulens kontrakt —
#: et tall i et deploy-skript er en konfigurasjon, et tall her er en regel.
KJOR_ETTER_MIGRASJON = 5

#: Legacy-saker får snapshot 0. Da er `forsok < LEAST(snapshot, 3)` usann
#: for enhver forsok-verdi, og claim-funksjonen kan ikke plukke dem opp
#: selv om noen senere skulle flytte dem ut av `manuell`. Fail-closed i to
#: uavhengige lag, ikke bare i statusfeltet.
LEGACY_SNAPSHOT = 0

#: Statuser der `manuell` ikke er en lovlig — eller meningsfull — overgang.
#: En sak som allerede er avgjort skal ikke gjenåpnes av en backfill.
TERMINALE = ("løst", "avvist", "manuell")


@dataclass
class Resultat:
    fra_evidens: int = 0
    legacy: int = 0
    tenanter: int = 0
    grunner: dict[str, int] = field(default_factory=dict)

    def _tell(self, grunn: str) -> None:
        self.grunner[grunn] = self.grunner.get(grunn, 0) + 1

    @property
    def totalt(self) -> int:
        return self.fra_evidens + self.legacy


def _historisk_policy(conn: psycopg.Connection, tenant: str, policy_id: str,
                      logg_hash: str, *, forventet_versjon: str | None = None
                      ) -> tuple[str, int] | str:
    """-> (versjon, maks_auto_forsok) eller en grunnkode som streng.

    Hele identiteten brukes (GO-vilkår V2): tenant + policy_id +
    innholds_hash peker ut NØYAKTIG én historisk rad, og `versjon` leses ut
    av den — for så å bli kryssjekket mot policyens egen `meta.versjon`.
    Det forrige utkastet slo opp på tenant/versjon/hash uten policy_id, og
    da kunne to policyer hos samme tenant med samme versjonsstreng bytte
    plass med hverandre.
    """
    from api.policyregister import innholds_hash
    from policy_validator.schema import valider_policy

    rader = conn.execute(
        "SELECT versjon, innhold, innholds_hash FROM policyer"
        " WHERE tenant=%s AND policy_id=%s AND innholds_hash=%s",
        (tenant, policy_id, logg_hash)).fetchall()
    if len(rader) != 1:
        return "policyrad_mangler" if not rader else "policyrad_flertydig"
    versjon, innhold, lagret_hash = rader[0]
    if not isinstance(innhold, dict):
        return "policyinnhold_ikke_objekt"

    # Re-hashing FØR bruk. Den lagrede `innholds_hash`-kolonnen er en
    # PÅSTAND fra den som skrev raden; hashen som teller er den vi regner
    # ut av innholdet som faktisk ligger der nå.
    faktisk = innholds_hash(innhold)
    if faktisk != logg_hash or faktisk != lagret_hash:
        return "hashavvik"

    meta = innhold.get("meta") or {}
    if meta.get("versjon") != versjon or meta.get("policy_id") != policy_id:
        return "meta_avvik"
    # HELE identiteten (GO-vilkår V2): versjonen fra revisjonsloggens egen
    # referanse må stemme med registerraden vi fant. Uten dette ville
    # tenant+policy_id+hash pekt ut en rad hvis versjon vi aldri sjekket.
    if forventet_versjon is not None and versjon != forventet_versjon:
        return "versjonsavvik"
    if valider_policy(innhold):
        return "policy_ugyldig"

    maks = ((innhold.get("unntak") or {}).get("maks_auto_forsok"))
    if isinstance(maks, bool) or not isinstance(maks, int) or maks < 0:
        return "maks_auto_forsok_ugyldig"
    return versjon, maks


def _backfill_tenant(conn: psycopg.Connection, tenant: str,
                     res: Resultat) -> None:
    from .pg import sett_kontekst

    sett_kontekst(conn, tenant, AKTOR, "backfill")
    rader = conn.execute(
        "SELECT u.id, u.status, r.policy_id, r.policy_content_hash"
        "  FROM unntak u"
        "  JOIN revisjonslogg r ON r.tenant = u.tenant AND r.id = u.loggpost_id"
        " WHERE u.tenant=%s"
        "   AND (u.maks_auto_forsok_snapshot IS NULL"
        "        OR u.policy_versjon IS NULL"
        "        OR u.policy_content_hash IS NULL)"
        " ORDER BY u.id", (tenant,)).fetchall()

    for unntak_id, status, policy_id, logg_hash in rader:
        utfall: tuple[str, int] | str
        # `revisjonslogg.policy_id` er en POLICYREFERANSE
        # (`<policy_id>@<versjon>/<handling>`), ikke en policy-id. Å slå opp
        # `WHERE policy_id = <referanse>` traff aldri noe, og HVER rad ble
        # legacy + manuell. Oppdaget på staging: 4200 av 4200.
        ref = les_policyref(policy_id)
        if ref is None or not logg_hash:
            utfall = "loggpost_uten_policyidentitet"
        else:
            utfall = _historisk_policy(conn, tenant, ref[0], logg_hash,
                                       forventet_versjon=ref[1])

        if isinstance(utfall, tuple):
            versjon, maks = utfall
            conn.execute(
                "UPDATE unntak SET maks_auto_forsok_snapshot=%s,"
                " policy_versjon=%s, policy_content_hash=%s"
                " WHERE tenant=%s AND id=%s",
                (maks, versjon, logg_hash, tenant, unntak_id))
            res.fra_evidens += 1
            continue

        # Uten verifiserbar historisk rad: eksplisitte legacy-verdier og
        # `manuell`. Aldri automatisk behandling (v3 pkt. 7, Codex-port 8).
        ny_status = status if status in TERMINALE else "manuell"
        conn.execute(
            "UPDATE unntak SET maks_auto_forsok_snapshot=%s,"
            " policy_versjon=%s, policy_content_hash=%s, status=%s"
            " WHERE tenant=%s AND id=%s",
            (LEGACY_SNAPSHOT, LEGACY, LEGACY, ny_status, tenant, unntak_id))
        conn.execute(
            "INSERT INTO unntak_historikk (tenant, unntak_id, hendelse,"
            " fra_status, til_status, aktor, request_id, detalj)"
            " VALUES (%s,%s,'legacy_uten_snapshot',%s,%s,%s,'backfill',%s)",
            (tenant, unntak_id, status, ny_status, AKTOR,
             json.dumps({"grunn": utfall, "policy_id": policy_id},
                        ensure_ascii=False)))
        res.legacy += 1
        res._tell(utfall)


def backfill(conn: psycopg.Connection) -> Resultat:
    """Fyller policysnapshotet for alle tenanter. Idempotent.

    Committer per tenant. En base med mange tenanter skal ikke holde én
    transaksjon åpen over hele jobben — og en tenant som feiler skal ikke
    rulle tilbake arbeidet for de andre. Feilen propagerer uansett, så
    oppsettet stopper og 006 nekter å kjøre.
    """
    res = Resultat()
    tenanter = [r[0] for r in conn.execute(
        "SELECT tenant, antall FROM tenanter_uten_policysnapshot()").fetchall()]
    conn.commit()
    for tenant in tenanter:
        _backfill_tenant(conn, tenant, res)
        conn.commit()
        res.tenanter += 1
    return res
