"""Varsler: «noe venter på DEG».

Eier: «mokhtar.eliassi@gmail.com skal få epost og samtidig melding i
kundeadmin … Det skal være option.»

Behovet kommer fra fire-øyne-flyten. En runde står åpen og venter på en
UAVHENGIG godkjenner, men ingenting forteller henne det — i praksis måtte eier
si fra utenom systemet. En styringsflate der neste steg formidles på SMS er
ikke en styringsflate.

TRE REGLER SOM STYRER DENNE MODULEN:

1. **Portalen er varselet; e-posten er en kopi.** Raden i `varsel` er
   sannheten. Kan e-posten ikke sendes, står varselet fortsatt i innboksen.
   Derfor er e-poststatus en kolonne på raden og ikke en egen kø.

2. **Et varsel skal ALDRI kunne velte handlingen.** En aktiveringsrunde er en
   fullmaktsendring; den skal ikke feile fordi en e-postserver er nede eller
   fordi noen slettet en identitet. `varsle_*` fanger derfor alt og
   rapporterer i stedet for å kaste. Motsatt vei ville vært absurd: et
   varslingsproblem som blokkerer styringen.

3. **Teksten lagres ikke, bare nøkkel + parametre.** Locale-kontrakten sier at
   synlig tekst kommer fra `locales/`, og varselet skal leses på MOTTAKERENS
   språk — ikke på det avsenderen tilfeldigvis hadde. E-posten rendres når den
   sendes, ikke når den køes.

Hvem som skal varsles ved en åpen runde er IKKE «alle med rollen»: det er de
som faktisk kan bringe runden videre. Attesterer man allerede, er man ferdig —
og forfatteren alene kan ikke fullføre fire øyne. Se `mottakere_for_runde`.
"""
import json

import psycopg

from db.pg import sett_kontekst

STANDARDKANAL = "epost_og_portal"


def _kanal(conn: psycopg.Connection, tenant: str, bruker_id: str) -> str:
    """Brukerens valg, eller standarden. Fraværende rad er IKKE «av»: ingen
    skal gå glipp av at noe venter på dem fordi de aldri åpnet
    innstillingene."""
    rad = conn.execute(
        "SELECT kanal FROM varselvalg WHERE tenant=%s AND bruker_id=%s",
        (tenant, bruker_id)).fetchone()
    return rad[0] if rad else STANDARDKANAL


def mottakere_for_runde(conn: psycopg.Connection, tenant: str,
                        utkast_id: str) -> list[str]:
    """Hvem kan bringe DENNE runden videre?

    Ikke «alle policyforvaltere». Den som allerede har attestert er ferdig, og
    å varsle henne igjen lærer henne bare å overse varsler. Forfatteren tas med
    KUN hvis hun ikke alt har attestert — hun teller, men kan ikke fullføre
    fire øyne alene, så terskelen avgjør uansett.
    """
    return [r[0] for r in conn.execute(
        "SELECT m.bruker_id FROM brukermedlemskap m"
        " WHERE m.tenant=%s AND m.aktiv"
        "   AND 'policyforvalter' = ANY(m.roller)"
        "   AND NOT EXISTS (SELECT 1 FROM aktiveringsattestasjon a"
        "                    WHERE a.tenant=m.tenant AND a.utkast_id=%s"
        "                      AND a.bruker_id=m.bruker_id)",
        (tenant, utkast_id)).fetchall()]


def opprett(conn: psycopg.Connection, *, tenant: str, bruker_id: str, art: str,
            ressurs_type: str, ressurs_id: str, tekstnokkel: str,
            hendelse: str = "", parametre: dict | None = None) -> bool:
    """Ett varsel til én mottaker. -> True hvis det ble opprettet.

    Idempotent på (tenant, bruker, art, ressurs, hendelse): en retry av
    handlingen som utløste varselet skal ikke fylle innboksen med duplikater.
    `ON CONFLICT DO NOTHING` er hele mekanismen — det unike indekset i
    migrasjon 026 er det som faktisk håndhever den.

    `hendelse` er grensen mellom «samme hendelse igjen» og «en ny hendelse på
    samme ressurs»: for en aktiveringsrunde er det rundenummeret. Utelates den,
    ville runde 2 på et utkast delt nøkkel med runde 1 og blitt slukt som en
    dublett — og godkjenneren som alt hadde lest det gamle varselet ville ikke
    sett noe nytt i det hele tatt.

    Har mottakeren valgt kun portal, settes `epost_status='ikke_aktuelt'` med
    én gang: et bevisst fravær skal ikke se ut som en sending som aldri kom.
    """
    kanal = _kanal(conn, tenant, bruker_id)
    status = "koet" if kanal == "epost_og_portal" else "ikke_aktuelt"
    rad = conn.execute(
        "INSERT INTO varsel (tenant, bruker_id, art, ressurs_type, ressurs_id,"
        " hendelse, tekstnokkel, parametre, epost_status)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)"
        " ON CONFLICT (tenant, bruker_id, art, ressurs_type, ressurs_id,"
        " hendelse) DO NOTHING RETURNING id",
        (tenant, bruker_id, art, ressurs_type, ressurs_id, hendelse,
         tekstnokkel, json.dumps(parametre or {}), status)).fetchone()
    return rad is not None


def varsle_runde_venter(conn: psycopg.Connection, *, tenant: str, aktor: str,
                        request_id: str, utkast_id: str, runde: int,
                        policy_id: str, risikoklasse: str,
                        gjenstaar: int) -> int:
    """Varsle dem som kan bringe runden videre. -> antall varsler opprettet.

    KASTER ALDRI. Kalles fra aktiveringsflyten, og en fullmaktsendring skal
    ikke kunne feile fordi varslingen gjorde det. Feiler noe her, er
    konsekvensen at et menneske ikke får en påminnelse — ikke at styringen
    stopper. Den motsatte avveiningen ville vært uforsvarlig.
    """
    try:
        sett_kontekst(conn, tenant, aktor, request_id)
        n = 0
        for bid in mottakere_for_runde(conn, tenant, utkast_id):
            if opprett(conn, tenant=tenant, bruker_id=bid,
                       art="attestering_venter", ressurs_type="policyutkast",
                       ressurs_id=utkast_id, hendelse=str(runde),
                       tekstnokkel="varsel.attestering_venter",
                       parametre={"policy_id": policy_id, "runde": runde,
                                  "risikoklasse": risikoklasse,
                                  "gjenstaar": gjenstaar}):
                n += 1
        return n
    except Exception:                                         # noqa: BLE001
        return 0


def innboks(conn: psycopg.Connection, *, tenant: str, bruker_id: str,
            kun_uleste: bool = False, grense: int = 50) -> list[dict]:
    """Mottakerens egne varsler, nyeste først."""
    sql = ("SELECT id, art, ressurs_type, ressurs_id, tekstnokkel, parametre,"
           " opprettet, lest_ts FROM varsel"
           " WHERE tenant=%s AND bruker_id=%s")
    if kun_uleste:
        sql += " AND lest_ts IS NULL"
    sql += " ORDER BY opprettet DESC LIMIT %s"
    return [{"id": r[0], "art": r[1], "ressurs_type": r[2], "ressurs_id": r[3],
             "tekstnokkel": r[4], "parametre": r[5],
             "opprettet": r[6].isoformat(),
             "lest": r[7] is not None}
            for r in conn.execute(sql, (tenant, bruker_id, grense)).fetchall()]


def antall_uleste(conn: psycopg.Connection, *, tenant: str,
                  bruker_id: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM varsel WHERE tenant=%s AND bruker_id=%s"
        " AND lest_ts IS NULL", (tenant, bruker_id)).fetchone()[0]


def merk_lest(conn: psycopg.Connection, *, tenant: str, bruker_id: str,
              varsel_id: int) -> bool:
    """Bare MINE varsler, og bare én gang.

    `bruker_id` i WHERE er ikke pynt oppå RLS: RLS skiller tenanter, ikke
    mennesker inne i samme tenant. Uten den kunne én bruker merket en kollegas
    varsel som lest — og dermed skjult at noe ventet på henne.
    """
    return conn.execute(
        "UPDATE varsel SET lest_ts=now() WHERE tenant=%s AND bruker_id=%s"
        " AND id=%s AND lest_ts IS NULL RETURNING id",
        (tenant, bruker_id, varsel_id)).fetchone() is not None


def sett_kanal(conn: psycopg.Connection, *, tenant: str, bruker_id: str,
               kanal: str) -> str:
    """Valget eier ba om. Ukjent verdi avvises — en feilstavet kanal skal ikke
    stille slå av varslingen."""
    if kanal not in ("epost_og_portal", "kun_portal"):
        raise ValueError(f"ukjent varselkanal: {kanal!r}")
    conn.execute(
        "INSERT INTO varselvalg (tenant, bruker_id, kanal) VALUES (%s,%s,%s)"
        " ON CONFLICT (tenant, bruker_id) DO UPDATE"
        " SET kanal=EXCLUDED.kanal, oppdatert=now()",
        (tenant, bruker_id, kanal))
    return kanal


def hent_kanal(conn: psycopg.Connection, *, tenant: str,
               bruker_id: str) -> str:
    return _kanal(conn, tenant, bruker_id)
