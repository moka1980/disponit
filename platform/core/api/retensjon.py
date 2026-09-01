"""M-4s leseflate — husets retensjonsregnskap, avlest.

Modulen UTLEDER INGENTING. Den leser fire definer-dører og former svaret;
dommen om hvert lager ble felt i MIGRASJON 093 og står i registeret med
sin begrunnelse. En regel som ble regnet ut her ville vært en andre kilde
til den samme sannheten — og den ville sluttet å stemme første gang noen
endret registeret uten å endre denne fila.

SNITTET ER TODELT, OG DET AVGJØRES HER — ikke av ruten.

  * `security:read` (rutens scope) ser REGISTERET: hvert lager med sin
    klasse, sin frist, sin reaper, sin dom og begrunnelsen for den —
    pluss ØKTENS EGEN beholdning og målingens tidsstempel og `avbrutt`.
    Det er kundens rett til å vite hva som lagres om dem og hvor lenge.

  * `platform:admin` ser I TILLEGG katalogtallene (bytes, radestimat),
    ALLE tenanters beholdning og HELE funnlisten. Det er plattformdrift.

Grunnen til at kontrollplanet avgjøres inne i endepunktet og ikke som
rutens scope er konkret: `platform:admin` står ikke i `LESESCOPES` i
app.py, og en browserøkt mot et scope utenfor det settet avvises. En rute
deklarert `platform:admin` ville gitt 403 for hver eneste innlogging.
`/v1/utrulling` løste det samme problemet på samme måte, og formen er
lånt derfra ordrett: den sterkere autoriteten er en UTVIDELSE av svaret,
aldri en annen inngang.

`plattformdrift` står ALLTID i svaret, også som `false`. Flaten skal
kunne si «du ser din egen del» i stedet for stille å utelate en tabell.
"""
from __future__ import annotations

#: Scopet som åpner kontrollplanet. Samme konstant og samme rolle som i
#: `utrulling.py` — kopiert med vilje: de to endepunktene skal kunne
#: endres uavhengig av hverandre.
PLATTFORMDRIFT = "platform:admin"


def _ts(verdi) -> str | None:
    return verdi.isoformat() if verdi is not None else None


def _tall(verdi) -> float | None:
    """`numeric` kommer tilbake som Decimal og er ikke JSON-serialiserbar.

    `frist_dogn` er et antall døgn, ikke et beløp — `float` er riktig og
    skjuler ingenting.
    """
    return None if verdi is None else float(verdi)


def svar_for(conn, tenant: str, scopes) -> dict:
    """Hele svaret for én økt.

    `maaling` er ALLTID med, også når den er `None`: en flate som ikke
    kan se om det har vært en måling, kan heller ikke si at det ikke har
    vært en. Og `avbrutt` sendes videre som det står — en avbrutt kjøring
    rapporteres som avbrutt, aldri som komplett med null.
    """
    plattformdrift = PLATTFORMDRIFT in set(scopes or ())

    rad = conn.execute("SELECT * FROM m4_siste_maaling(%s)",
                       (tenant,)).fetchone()
    maaling = None
    if rad is not None:
        maaling = {"maaling_id": str(rad[0]), "startet_ts": _ts(rad[1]),
                   "fullfort_ts": _ts(rad[2]), "avbrutt": bool(rad[3]),
                   "antall_lagre": rad[4], "antall_umaalbare": rad[5],
                   "antall_funn": rad[6]}

    lagre = [
        {"lager_id": r[0], "relasjon": r[1], "klasse": r[2],
         "tenantkolonne": r[3], "alderskolonne": r[4],
         "reapetkolonne": r[5], "fristkilde": r[6],
         "frist_dogn": _tall(r[7]), "reaper": r[8], "dom": r[9],
         "dom_begrunnelse": r[10], "dom_migrasjon": r[11],
         "rader": r[12], "rader_ureapet": r[13],
         "eldste_ureapet_ts": _ts(r[14]), "sist_reapet_ts": _ts(r[15])}
        for r in conn.execute("SELECT * FROM m4_retensjonsbilde(%s)",
                              (tenant,)).fetchall()]

    svar = {"plattformdrift": plattformdrift, "maaling": maaling,
            "lagre": lagre, "katalog": None, "funn": None}
    if not plattformdrift:
        # Ikke en tom liste: `None` sier «du ser ikke denne delen», mens
        # `[]` sier «det finnes ingen funn». De to må ikke se like ut på
        # en flate hvis hele poenget er at et funn skal være synlig.
        return svar

    # Katalogtallene er FLATE rader (lager × tenant). Grupperingen er
    # presentasjon og bor på flaten — oversikt-lærdommen, som i M-11.
    svar["katalog"] = [
        {"lager_id": r[0], "bytes_totalt": r[1], "rader_estimat": r[2],
         "tenant": r[3], "rader": r[4], "rader_ureapet": r[5],
         "eldste_ureapet_ts": _ts(r[6]), "sist_reapet_ts": _ts(r[7])}
        for r in conn.execute("SELECT * FROM m4_retensjonskatalog(%s)",
                              (tenant,)).fetchall()]
    svar["funn"] = [
        {"funn_id": str(r[0]), "lager_id": r[1], "relasjon": r[2],
         "tenant": r[3], "funntype": r[4], "oppdaget_ts": _ts(r[5]),
         "oppdaget_maaling": str(r[6]), "sist_sett_maaling": str(r[7]),
         "detalj": r[8]}
        for r in conn.execute("SELECT * FROM m4_retensjonsfunn(%s)",
                              (tenant,)).fetchall()]
    return svar
