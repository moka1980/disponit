"""PR-014b CP2b: idempotent M-37-sak ved domeneovertakelse (B4).

`verifiser_domenekontroll` returnerer `'konflikt:<tapt-tenant>'` når en aktiv
verifisering overtas: DB-en fjerner A (tilbakekalt) og setter B `avklaring_kreves`
i samme transaksjon, men gir ALDRI B autorisasjonen der og da. Denne modulen
oppretter den ENE M-37-saken (familie `domeneovertakelse`) som avgjøres i
unntaksbehandlingen (PR-012) og til slutt kaller `avgjor_domeneovertakelse`.

Saken er idempotent PER overtakelsesgenerasjon: samme konflikt (samme B-rads
`autorisasjonsgenerasjon`) gir SAMME sak; en ny overtakelse (ny, monoton
generasjon) gir en ny sak. Generasjonen i idempotensnøkkelen er nettopp det som
lar en terminal sak ligge urørt mens en fremtidig, uavhengig konflikt får sin
egen sak — «gjenbruk kun av ikke-terminal, samme familie; terminale saker endres
aldri» faller ut av at nøkkelen er unik per konflikt.

Saken er `sakstype='sikkerhet'` (normalarbeideren claimer den aldri) med
`UKJENT_SNAPSHOT` (maks_auto_forsok=0 → kan ikke auto-behandles): en overtakelse
er per definisjon en menneskelig/sikkerhetsavgjørelse.
"""
import hashlib

from api import kjerne

#: Familien saken merkes med (unntak.kategori). Lineage til begge domenerader
#: ligger i den krypterte payloaden.
FAMILIE = "domeneovertakelse"


def idempotensnokkel(hostname: str, generasjon: int) -> str:
    return f"{FAMILIE}:{hostname}:{generasjon}"


def opprett_overtakelsessak(conn, *, tenant_ny: str, hostname: str,
                            tenant_tapt: str, generasjon: int,
                            aktor: str) -> int:
    """Opprett (eller gjenbruk) overtakelsessaken. Returnerer `unntak_id`.

    Kalleren MÅ ha satt `disponit.tenant = tenant_ny` (RLS). `generasjon` er
    B-radens `autorisasjonsgenerasjon` etter overtakelsen — monoton, altså unik
    per konflikt.
    """
    key = idempotensnokkel(hostname, generasjon)
    # Codex: serialiser på den avledede nøkkelen. `revisjonslogg` har kun en
    # IKKE-unik indeks på (tenant, idempotency_key), så to samtidige retry-arbeidere
    # kunne begge se «ingen rad» og opprette hver sin sak. Advisory-låsen (transaks-
    # jonsscopet) gjør sjekk-og-opprett atomisk per (tenant, hostname, generasjon).
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (tenant_ny + ":" + key,))
    rad = conn.execute(
        "SELECT id FROM revisjonslogg WHERE tenant=%s AND idempotency_key=%s",
        (tenant_ny, key)).fetchone()
    if rad is not None:
        u = conn.execute(
            "SELECT id FROM unntak WHERE tenant=%s AND loggpost_id=%s",
            (tenant_ny, rad[0])).fetchone()
        if u is not None:
            return int(u[0])   # idempotent: saken finnes alt for denne konflikten

    loggpost = int(conn.execute(
        "INSERT INTO revisjonslogg (tenant, aktor, kilde, input_hash, policy_id,"
        " beslutning, begrunnelse, idempotency_key)"
        " VALUES (%s,%s,%s,%s,%s,'UNNTAK','[]',%s) RETURNING id",
        (tenant_ny, aktor, FAMILIE,
         hashlib.sha256(key.encode()).hexdigest(), FAMILIE, key)).fetchone()[0])

    payload = {
        "hostname": hostname,
        "tenant_tapt": tenant_tapt,     # A — bevis bevart, men tilbakekalt
        "tenant_ny": tenant_ny,         # B — avklaring_kreves inntil avgjørelse
        "generasjon": generasjon,
        "familie": FAMILIE,
    }
    return kjerne._skriv_unntak(
        conn, tenant_ny, loggpost, handling="domene.overtakelse",
        kategori=FAMILIE, sakstype="sikkerhet", prioritet="hoy",
        payload=payload, snapshot=kjerne.UKJENT_SNAPSHOT)
