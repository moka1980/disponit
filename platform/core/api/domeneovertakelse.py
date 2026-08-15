"""PR-014b CP2b: idempotent M-37-sak ved domeneovertakelse (B4).

`verifiser_domenekontroll` returnerer `'konflikt:<tapt-tenant>'` når en aktiv
verifisering overtas: DB-en fjerner A (tilbakekalt) og setter B `avklaring_kreves`
i samme transaksjon, men gir ALDRI B autorisasjonen der og da. Denne modulen
oppretter den ENE M-37-saken (familie `domeneovertakelse`) som avgjøres i
unntaksbehandlingen (PR-012) og til slutt kaller `avgjor_domeneovertakelse`.

Samme signal kommer når en AVVIST kandidat søker på nytt (DB-en bærer motparten
på raden i `konflikt_motpart`): ny generasjon, og dermed en ny sak — ellers ble
reapplikasjonen stående i `avklaring_kreves` uten noen sak som kunne avgjøre den.

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

#: Handlingen saken bærer (unntak.handling). Sammen med FAMILIE er dette det
#: som skiller VÅRE rader fra alt annet som deler idempotensnavnerommet.
HANDLING = "domene.overtakelse"


def idempotensnokkel(hostname: str, generasjon: int) -> str:
    return f"{FAMILIE}:{hostname}:{generasjon}"


def opprett_overtakelsessak(conn, *, tenant_ny: str, hostname: str,
                            tenant_tapt: str, generasjon: int,
                            aktor: str) -> int:
    """Opprett (eller gjenbruk) overtakelsessaken. Returnerer `unntak_id`.

    Kalleren MÅ ha satt `disponit.tenant = tenant_ny` (RLS). `generasjon` er
    B-radens `autorisasjonsgenerasjon` etter overtakelsen — monoton, altså unik
    per konflikt.

    `hostname` er alltid kanonisk her: nøkkelen bygges på det samme navnet som
    ble sendt til `verifiser_domenekontroll`, og migrasjon 018 (§0) avviser
    enhver annen tekstlig form FØR konflikten i det hele tatt kan oppstå. Det
    er nettopp derfor §0 validerer i stedet for å normalisere — ellers kunne to
    former av samme navn gitt to idempotensnøkler for én konflikt.
    """
    key = idempotensnokkel(hostname, generasjon)
    # Codex: serialiser på den avledede nøkkelen. `revisjonslogg` har kun en
    # IKKE-unik indeks på (tenant, idempotency_key), så to samtidige retry-arbeidere
    # kunne begge se «ingen rad» og opprette hver sin sak. Advisory-låsen (transaks-
    # jonsscopet) gjør sjekk-og-opprett atomisk per (tenant, hostname, generasjon).
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (tenant_ny + ":" + key,))
    # Codex: slå opp SAKEN direkte, scopet til overtakelsesfamilien — ikke en
    # vilkårlig loggpost med samme nøkkel. `revisjonslogg.idempotency_key` er et
    # DELT, KALLERSTYRT navnerom (`/v1/beslutning` skriver klientens
    # Idempotency-Key rett inn) med kun en IKKE-unik indeks. Gikk oppslaget via
    # loggposten først, kunne en fremmed rad som alt het
    # `domeneovertakelse:<hostname>:<generasjon>` kapre idempotensen på to måter:
    # uten `unntak` fant vi ingen sak og opprettet en NY ved hvert retry (én
    # konflikt → mange M-37-saker), og MED et urelatert `unntak` returnerte vi
    # den fremmede saken som om den var overtakelsessaken — og konflikten fikk
    # aldri sin egen sak, mens B ble stående i `avklaring_kreves` for alltid.
    # Joinen bærer både `kilde`/`kategori` og `handling`, altså nøyaktig det
    # denne funksjonen selv skriver; en fremmed rad kan ikke matche.
    sak = conn.execute(
        "SELECT u.id FROM unntak u"
        " JOIN revisjonslogg r ON r.tenant = u.tenant AND r.id = u.loggpost_id"
        " WHERE u.tenant=%s AND u.kategori=%s AND u.handling=%s"
        "   AND r.kilde=%s AND r.idempotency_key=%s"
        " ORDER BY u.id LIMIT 1",
        (tenant_ny, FAMILIE, HANDLING, FAMILIE, key)).fetchone()
    if sak is not None:
        return int(sak[0])   # idempotent: saken finnes alt for denne konflikten

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
        conn, tenant_ny, loggpost, handling=HANDLING,
        kategori=FAMILIE, sakstype="sikkerhet", prioritet="hoy",
        payload=payload, snapshot=kjerne.UKJENT_SNAPSHOT)
