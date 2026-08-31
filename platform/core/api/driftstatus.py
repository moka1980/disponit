"""Driftstatusens to leseflater — M-10 backupinnsyn og M-11 selvtest.

Begge er AVLESNINGER av rader driftsjobbene alt har committet: modulen
utleder ingenting, regner ingenting om og lagrer ingenting. Den eneste
formingen som skjer her er GRUPPERINGEN av selvtestens flate rader
(kjøring × probe) til én kjøring med sine prober — presentasjon, som
etter oversikt-lærdommen bor i API-laget og ikke i SQL.

DATAENE ER PLATTFORMENS (dommen: begge tabellene er plattformskop, uten
tenant-kolonne og uten RLS). Retten til å spørre er likevel øktens:
`backup_status` og `selvtest_status` kaller `krev_tenantkontekst` FØRST
(051-leseformen), og runtime når tabellene KUN gjennom de to dørene —
det finnes ingen SELECT-rettighet å falle tilbake på. Scopet på begge
ruter er `security:read`, samme klasse som M-31s model card: dette er
plattformdriftens eget innsyn, ikke en tenants tall.

INGEN HEMMELIGHET PASSERER HER. Backupraden er fem tall om plattformens
egen sikkerhetskopi — ingen sti, ingen nøkkel, ingen kunderad. Selvtestens
`maalt` er den formen probene selv valgte, og kanariporten i
`test_m11_selvtest` måler at en hemmelighet i miljøet aldri havner der.
Denne modulen sender `maalt` videre uendret, og det er med vilje: en
filtrering HER ville flyttet ansvaret vekk fra proben, som er det ene
stedet som vet hva den målte.
"""
from __future__ import annotations

#: Taket på hvor mange rader hver flate henter. Dørene klemmer selv til
#: [1, 100]; tallet her er standarden når klienten ikke ber om noe, og
#: det er en AVLESNING og ikke en paginert logg — hele historikken bor i
#: tabellen, og en drilling dit er en egen flate.
GRENSE_STANDARD = 20


def _ts(verdi) -> str | None:
    return verdi.isoformat() if verdi is not None else None


def backup_svar(conn, tenant: str, grense: int = GRENSE_STANDARD) -> dict:
    """Siste verifiseringer, nyeste først. -> {"verifiseringer": [...]}.

    `alder_s` regnes i BASEN, i samme skann som radene (090): flaten skal
    aldri måtte trekke to tidspunkter fra hverandre for å vite hvor gammel
    en verifisering er. Det er M-16-regelen — flaten deler aldri to av
    svarets tall på hverandre — anvendt på en subtraksjon.
    """
    rader = conn.execute("SELECT * FROM backup_status(%s,%s)",
                         (tenant, grense)).fetchall()
    return {"verifiseringer": [
        {"backup_ts": _ts(r[0]), "verifisert_ts": _ts(r[1]),
         # `numeric` kommer tilbake som Decimal og er ikke JSON-serialiserbar.
         # `float` er riktig her og ikke en avrunding som skjuler noe: dette
         # er en restorevarighet i sekunder, målt av `date +%s.%N` med tre
         # desimaler — ikke et beløp.
         "restore_varighet_s": float(r[2]),
         "tabeller": r[3], "storrelse_b": r[4],
         "registrert": _ts(r[5]), "alder_s": r[6]}
        for r in rader]}


def selvtest_svar(conn, tenant: str, grense: int = GRENSE_STANDARD) -> dict:
    """Siste kjøringer med probene sine. -> {"kjoringer": [...]}.

    Døren gir FLATE rader (kjøring × probe) fordi grupperingen er
    presentasjon. Den skjer her, og den bevarer dørens rekkefølge: nyeste
    kjøring først, probene alfabetisk innenfor hver. Da er to kjøringer
    sammenlignbare linje for linje på flaten uten at flaten sorterer.
    """
    rader = conn.execute("SELECT * FROM selvtest_status(%s,%s)",
                         (tenant, grense)).fetchall()
    kjoringer: list[dict] = []
    indeks: dict[str, dict] = {}
    for kjoring_id, ts, samlet, alder_s, probe, status, maalt in rader:
        nokkel = str(kjoring_id)
        k = indeks.get(nokkel)
        if k is None:
            k = {"kjoring_id": nokkel, "ts": _ts(ts), "samlet": samlet,
                 "alder_s": alder_s, "prober": []}
            indeks[nokkel] = k
            kjoringer.append(k)
        k["prober"].append({"probe": probe, "status": status,
                            "maalt": maalt if isinstance(maalt, dict) else {}})
    return {"kjoringer": kjoringer}
