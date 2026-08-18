"""Envelope-kryptering av unntaks-payload (v2 Del 5 + v3-delta pkt. 2-3).

Per-tenant DEK (AES-256-GCM) pakket av KEK fra DISPONIT_KEK (miljø).
payload_kryptert = ciphertext || 16-byte GCM-tag; nonce i egen kolonne.
Crypto-shredding: destruer() nuller wrapped_dek og setter destruert_ts +
aktiv=false i ÉN UPDATE (GO-vilkår 1); ciphertext består som artefakt.
Persondata SKAL være erstattet med kildereferanser FØR kryptering —
minimeringen skjer i api_kjerne.minimer_payload, ikke her.
"""
from __future__ import annotations

import json
import os
import secrets

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _kek() -> AESGCM:
    raa = os.environ.get("DISPONIT_KEK", "")
    if len(raa) < 64:
        raise RuntimeError("DISPONIT_KEK mangler/for kort (krever >= 32 byte hex)")
    return AESGCM(bytes.fromhex(raa[:64]))


def krev_kek() -> None:
    """Boot-sjekk: KEK-en finnes og lar seg bruke. Kaster ellers.

    Kalles av `api.app` FØR prosessen begynner å ta imot forespørsler. Uten
    denne oppdages en manglende KEK først når den første unntaksraden skal
    krypteres — altså midt i en beslutning, med en halvferdig transaksjon
    og en klient som venter. En prosess som ikke kan kryptere skal ikke
    starte.
    """
    _kek()


def _pakk_ut(rad, tenant: str) -> tuple[str, bytes]:
    key_id, wrapped = rad
    nonce, ct = bytes(wrapped[:12]), bytes(wrapped[12:])
    return key_id, _kek().decrypt(nonce, ct, tenant.encode())


def _livslas(conn: psycopg.Connection, tenant: str, *, eksklusiv: bool) -> None:
    """Livstidslås rundt en tenants DEK — DELT for de som krypterer under den,
    EKSKLUSIV for den som destruerer den (Codex P1).

    Uten denne var lesingen av den aktive DEK-en et ulåst punktavlesningsbilde:
    en crypto-shredding kunne committe ETTER at `hent_eller_opprett_aktiv_dek`
    hadde levert nøkkelen, men FØR transaksjonen som krypterte med den var
    committet. Fremmednøkkelen holdt fortsatt (nøkkelRADEN består — det er
    `wrapped_dek` som nulles), så skrivingen lyktes: resultatet var et ciphertext
    som per konstruksjon ALDRI kan dekrypteres, lagret som gyldig evidens. I
    artefaktveien betydde det et 200-svar med brent kapabilitet for en rapport
    ingen kan lese igjen — og en senere kvittering kunne til og med promotere den,
    siden promoteringen ikke sjekker nøkkeltilgjengelighet.

    Låsen er en advisory xact-lås, ikke `SELECT ... FOR SHARE`: radlåsing på
    `tenant_nokler` ville krevd UPDATE-privilegium på tabellen og trukket RLS-ens
    UPDATE-policy inn i en ren lesevei. Den delte formen serialiserer IKKE
    krypterende transaksjoner mot hverandre — bare mot destruksjonen, som venter
    til hver enkelt av dem har committet. Etter det er nøkkelen borte som tiltenkt.
    """
    fn = ("pg_advisory_xact_lock" if eksklusiv
          else "pg_advisory_xact_lock_shared")
    conn.execute(f"SELECT {fn}(hashtextextended(%s, 0))",
                 (f"{tenant}\x1fdek-liv",))


def hent_eller_opprett_aktiv_dek(conn: psycopg.Connection,
                                 tenant: str) -> tuple[str, bytes]:
    """Den aktive DEK-en for tenanten, opprettet ved første behov.

    Opprettelsen er serialisert per tenant. Uten det taper 19 av 20
    samtidige førstegangs-skrivinger kappløpet mot delindeksen
    `en_aktiv_dek_per_tenant`: alle ser «ingen aktiv DEK», alle forsøker å
    lage en, én vinner og resten får unikbrudd — som i API-veien blir
    `unntaksskriv_feilet` og ruller HELE beslutningen.

    Funnet av lasttesten, ikke av lesing: feilen finnes bare i det ene
    øyeblikket en tenant får sin aller første sak, og bare når flere
    forespørsler treffer samtidig. Alle enkelttester passerte.

    Låsen tas KUN når det faktisk mangler en DEK. Å låse per tenant på
    hver eneste unntaksrad ville serialisert hele køskrivingen for en
    kunde, og bootstrap skjer én gang i en tenants levetid.
    """
    # RLS: uten sesjonsvariabelen ser vi null rader og ville laget en NY
    # DEK for en tenant som allerede har en — og da blir gamle unntak
    # uleselige uten at noe feiler høylytt.
    from .pg import sett_tenant
    sett_tenant(conn, tenant)
    # Delt livstidslås FØR lesingen: den DEK-en vi leverer skal ikke kunne
    # destrueres før kalleren har committet det den krypterte med den.
    _livslas(conn, tenant, eksklusiv=False)
    rad = conn.execute(
        "SELECT key_id, wrapped_dek FROM tenant_nokler"
        " WHERE tenant=%s AND aktiv", (tenant,)).fetchone()
    if rad:
        return _pakk_ut(rad, tenant)

    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (f"{tenant}\x1fdek-bootstrap",))
    # Dobbeltsjekk UNDER låsen: vant noen andre mens vi ventet, er deres
    # DEK nå committet og synlig, og vi skal bruke den — ikke lage enda en.
    rad = conn.execute(
        "SELECT key_id, wrapped_dek FROM tenant_nokler"
        " WHERE tenant=%s AND aktiv", (tenant,)).fetchone()
    if rad:
        return _pakk_ut(rad, tenant)

    dek = AESGCM.generate_key(256)
    nonce = secrets.token_bytes(12)
    wrapped = nonce + _kek().encrypt(nonce, dek, tenant.encode())
    key_id = "dek-" + secrets.token_hex(8)
    conn.execute("INSERT INTO tenant_nokler (tenant, key_id, wrapped_dek)"
                 " VALUES (%s,%s,%s)", (tenant, key_id, wrapped))
    return key_id, dek


def hent_dek(conn: psycopg.Connection, tenant: str, key_id: str) -> bytes:
    """DEK for en SPESIFIKK key_id — også en rotert/ikke-aktiv nøkkel. Brukes
    til å dekryptere data som ble kryptert under en tidligere aktiv DEK (f.eks.
    en handlingsintensjon hvis tenanten har rotert nøkkel siden). Kaster hvis
    nøkkelen ikke finnes eller er crypto-shreddet (wrapped_dek = NULL)."""
    from .pg import sett_tenant
    sett_tenant(conn, tenant)
    rad = conn.execute(
        "SELECT key_id, wrapped_dek FROM tenant_nokler"
        " WHERE tenant=%s AND key_id=%s", (tenant, key_id)).fetchone()
    if rad is None or rad[1] is None:
        raise RuntimeError("dek utilgjengelig for key_id")
    return _pakk_ut(rad, tenant)[1]


def _aad(tenant: str, key_id: str, ekstra_aad: bytes | None = None) -> bytes:
    """Tilleggsdata som bindes inn i GCM-taggen.

    Uten AAD er et ciphertext bare en pose bytes: flyttes raden til en annen
    tenant, dekrypterer den fint så lenge nøkkelen er den samme. Med tenant
    og key_id som AAD feiler dekrypteringen i det konteksten er en annen enn
    da det ble kryptert. Det koster ingenting og lukker en stille
    kryss-tenant-vei som RLS alene ikke dekker (RLS beskytter raden, ikke
    bytene hvis de kopieres).

    `ekstra_aad` (PR-012): valgfri tilleggsbinding. Uten den er resultatet
    BYTE-IDENTISK med før — eksisterende ciphertext er uendret. Med den kan
    handlingsintensjonen bindes til (unntak_id, target_action, hi_skjemaversjon,
    intensjon_policy_hash) så et ciphertext ikke kan flyttes mellom saker.
    """
    base = f"{tenant}|{key_id}".encode("utf-8")
    return base if ekstra_aad is None else base + b"|" + ekstra_aad


def intensjon_aad(unntak_id: int, target_action: str, hi_skjemaversjon: int,
                  intensjon_policy_hash: str) -> bytes:
    """Kanonisk `ekstra_aad` for handlingsintensjon (v6 §3). Alle komponenter
    hentes av kalleren fra UFORANDERLIGE saksfelt (aldri klient/ciphertext).
    Brukes IDENTISK ved kryptering og all senere dekryptering."""
    return "|".join((str(unntak_id), target_action, str(hi_skjemaversjon),
                     intensjon_policy_hash)).encode("utf-8")


def krypter(dek: bytes, payload: dict, tenant: str, key_id: str,
            *, ekstra_aad: bytes | None = None) -> tuple[bytes, bytes]:
    """-> (payload_kryptert = ct||tag, nonce)

    SERIALISERINGEN MÅ VÆRE TOTAL (Codex P2), av samme grunn som
    `tekstbytes.utf8` finnes: payloaden bærer UBETRODDE strenger fra
    hendelsen, og `json.loads` godtar `"\\ud800"`. Et ensomt surrogat blir
    en helt alminnelig `str` som Pythons strenge UTF-8-koder nekter å
    skrive.

    `ensure_ascii=False` gjorde derfor DENNE linja til stedet avsenderen
    kunne kaste fra — midt i transaksjonen som skriver revisjonsraden.
    `input_hash` ble gjort total, men verdien lever videre i
    `minimer_payload`, og `UnicodeEncodeError` her rullet tilbake den
    alt skrevne loggposten og ga `logging_feilet`: nøyaktig den
    revisjonssporbypassen den fiksen skulle lukke, ett steg lenger ned.

    `ensure_ascii=True` er den totale formen HER, og ikke
    `tekstbytes.utf8`: JSON-en skal `json.loads`-es tilbake i
    `dekrypter`, og escapen `\\ud800` er nettopp den formen som gir
    surrogatet uendret tilbake. `surrogatepass` ville gitt bytes
    `json.loads` selv nekter å lese, altså et ciphertext ingen kan åpne.
    Escapingen er injektiv — `json.dumps` skiller en ekte streng `\\ud800`
    fra kodeenheten, den skriver `\\\\ud800` for den første — så ingen to
    ulike payloads kan kollidere.

    Eksisterende rader er urørt: nonce-en er tilfeldig, så ingen
    ciphertext er reproduserbar uansett, og `dekrypter` leser begge
    skrivemåtene."""
    nonce = secrets.token_bytes(12)
    data = json.dumps(payload, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":")).encode()
    return AESGCM(dek).encrypt(nonce, data, _aad(tenant, key_id, ekstra_aad)), nonce


def dekrypter(dek: bytes, ct_og_tag: bytes, nonce: bytes, tenant: str,
              key_id: str, *, ekstra_aad: bytes | None = None) -> dict:
    return json.loads(AESGCM(dek).decrypt(bytes(nonce), bytes(ct_og_tag),
                                          _aad(tenant, key_id, ekstra_aad)))


def destruer(conn: psycopg.Connection, tenant: str, key_id: str) -> None:
    """Crypto-shredding — logging av handlingen gjøres av kalleren
    (revisjonslogg + unntak_historikk per berørt sak)."""
    from .pg import sett_tenant
    sett_tenant(conn, tenant)
    # Eksklusiv livstidslås: vent til hver transaksjon som allerede har fått
    # utlevert denne tenantens DEK har committet, slik at destruksjonen aldri
    # legger seg MELLOM utleveringen og skrivingen av ciphertexten.
    _livslas(conn, tenant, eksklusiv=True)
    res = conn.execute("UPDATE tenant_nokler SET wrapped_dek=NULL,"
                       " destruert_ts=now(), aktiv=false"
                       " WHERE tenant=%s AND key_id=%s", (tenant, key_id))
    if res.rowcount != 1:
        # Stille no-op her ville betydd at sletting av persondata IKKE
        # skjedde, mens kalleren logget at den gjorde det.
        raise RuntimeError(
            f"crypto-shredding traff {res.rowcount} rader for {tenant}/{key_id}"
            " — forventet nøyaktig 1")
