"""M-9 kunnskaps- og ordlisteflate — begrepsregisteret med kildekrav.

AVLESNING, IKKE UTLEDNING. Modulen former ingenting: `m9_sok` returnerer
radene, `m9_apne_funn` returnerer de åpne utløpsfunnene, og begge er
avlesninger av rader dørene og sveipen alt har committet. `utlopt`
regnes i BASEN (095), i samme skann som radene — flaten skal aldri måtte
sammenligne en dato med dagens for å vite om et begrep har gått ut på
dato, og API-laget skal ikke gjøre det på dens vegne heller.

KILDEN ER EN KOLONNE, IKKE EN FOTNOTE. Hvert treff bærer `eier`, `kilde`
og `gyldig_til`. Det er ikke pynt: «svar uten tilstrekkelig
kildegrunnlag avvises» er katalogens eget krav, og i v1 er det en NOT
NULL i basen. Da skal svaret også bære den — et søketreff uten kilde
ville vært en påstand uten avsender, og skjemaet gjør den
urepresenterbar nettopp for at flaten alltid skal ha noe å vise.

TENANTISOLASJONEN ER RLS SIN, ikke denne modulens. `m9_sok` kaller
`krev_tenantkontekst` først og har INGEN tenant-predikat i WHERE-leddet;
raden filtreres av policyen `tenant_isolasjon` på `begrep`. Modulen her
sender `auth.tenant` videre og har ingen egen mening om saken —
`test_m9_kunnskap.py::tenantlekkasje_i_begrepssok` måler at det holder,
både med direkte DML og over API.

INGEN MUTASJON. v1 har ingen HTTP-skrivevei inn i ordlisten: begreper
registreres og versjoneres gjennom de eier-eide dørene i 095, og
`migrer.py` REVOKEr dem eksplisitt fra runtime-rollen. Det er samme dom
som M-31s model card (086) — og den er en sikkerhetsavgjørelse, ikke en
manglende funksjon.
"""
from __future__ import annotations

#: Taket på hvor mange treff flaten henter når klienten ikke ber om noe.
#: Døren klemmer selv til [1, 200]; tallet her er standarden. En ordliste
#: leses med søk, ikke ved å bla — en drilling ned i historikken til ett
#: begrep er en egen flate.
GRENSE_STANDARD = 50
#: Taket på antall åpne funn i svaret. Døren klemmer til [1, 500].
FUNNGRENSE_STANDARD = 100
#: Lengste søkestreng som slipper videre. `websearch_to_tsquery` har
#: ingen syntaksfeil å kaste, men en spørring på titusener av tegn er
#: ikke et søk — det er en måte å bruke tid i basen på.
SPORRING_MAKS = 200


def _dato(verdi) -> str | None:
    return verdi.isoformat() if verdi is not None else None


def kunnskap_svar(conn, tenant: str, sporring: str | None = None,
                  grense: int = GRENSE_STANDARD) -> dict:
    """Ordlisten + de åpne utløpsfunnene.

    Tom eller manglende `sporring` er LISTINGEN (hele den gjeldende
    ordlisten, alfabetisk) — det er dørens egen oppførsel, ikke et
    spesialtilfelle her. `sporring` sendes videre UENDRET: normalisering
    i API-laget ville vært en andre tolkning av brukerens tekst ved siden
    av `websearch_to_tsquery('norwegian', …)`, og to tolkninger av det
    samme søket er én for mye.
    """
    q = (sporring or "")[:SPORRING_MAKS]
    rader = conn.execute("SELECT * FROM m9_sok(%s,%s,%s)",
                         (tenant, q, grense)).fetchall()
    funn = conn.execute("SELECT * FROM m9_apne_funn(%s,%s)",
                        (tenant, FUNNGRENSE_STANDARD)).fetchall()
    return {
        # Spørringen ekkoes tilbake så flaten kan si HVA den viser
        # treff for — en resultatliste uten spørsmålet er en liste uten
        # kontekst når svaret kommer etter at feltet er endret.
        "sporring": q,
        "begreper": [
            {"begrep_id": str(r[0]), "term": r[1], "forklaring": r[2],
             "eier": r[3], "kilde": r[4], "gyldig_til": _dato(r[5]),
             "versjonsnr": r[6], "utlopt": r[7],
             # `rang` er `real` fra ts_rank_cd. Den sendes med fordi
             # rekkefølgen ellers er en påstand flaten ikke kan
             # etterprøve — men flaten SORTERER ikke om: dørens
             # rekkefølge er svaret.
             "rang": float(r[8])}
            for r in rader],
        "funn": [
            {"begrep_id": str(f[0]), "funntype": f[1], "term": f[2],
             "gyldig_til": _dato(f[3]),
             "forst_sett": f[4].isoformat() if f[4] is not None else None,
             "sist_sett_sveip":
                 f[5].isoformat() if f[5] is not None else None,
             "alder_s": f[6]}
            for f in funn],
    }
