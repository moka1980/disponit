"""Det syntetiske beslutningssettet for m02-fordelingsartefaktet.

ETT sett, TO ledd (m02-aksept-klarsignalet §3, premiss korrigert mot
basen 2026-08-21): m01-rundens opprinnelige 180 rader finnes ikke i
disponit-srv-basen — de ble aldri med fra gammel staging, og hele basen
har null STOPP. «Fordelingen 84/3/93 over 180 hendelser» er derfor
REPRODUSERT, ikke mimret: settet under drives gjennom den EKTE
beslutningsveien (/v1/beslutning) både lokalt (CI,
`test_m02_fordeling.py` — det er «likt lokalt»-leddet, stående) og på
staging (`m02-fordeling-artefakt.py` — artefaktets ledd). Fordi begge
ledd bruker NØYAKTIG dette settet, kan de aldri gli fra hverandre.

Kategoriene er beslutningsveiens egne, fra m01-portene:

  * TILLAT — gyldig hendelse med signerte attestasjoner.
  * STOPP  — tuklet attestasjon (`resultat` snudd uten ny signering):
             signaturporten feller den, og det er nettopp den
             sikkerhetsveien som skal ETTERLATE en STOPP-rad.
  * UNNTAK — ukjent handling uten attestasjoner: deny-by-default.
             (Uten attestasjoner med vilje — med dem hadde hendelsen
             truffet BINDINGSPORTEN og blitt sikkerhetssak i stedet;
             samme lærdom som test_api-porten dokumenterer.)

Driveren MÅLER hvert svar (fail-closed): et sett der én hendelse fikk
en annen beslutning enn kategorien lover, er ikke settet — da avbrytes
kjøringen i stedet for å telle videre. Radene identifiseres etterpå i
`revisjonslogg` via idempotensnøklene, som bærer rundens id.
"""
from __future__ import annotations

SETT_VERSJON = "m02-sett-1"
FORDELING = {"TILLAT": 84, "STOPP": 3, "UNNTAK": 93}


def bygg_sett() -> list[tuple[str, int]]:
    """-> [(forventet_beslutning, løpenummer)] — 180, deterministisk."""
    ut: list[tuple[str, int]] = []
    for beslutning, antall in sorted(FORDELING.items()):
        ut += [(beslutning, i) for i in range(antall)]
    return ut


def idemnokkel(runde_id: str, beslutning: str, i: int) -> str:
    return f"m02f-{runde_id}-{beslutning.lower()}-{i:03d}"


def kjor_sett(runde_id: str, post, hendelse, hendelse_uten,
              tukle) -> dict[str, int]:
    """Driver settet gjennom beslutningsveien. -> antall per beslutning.

    Avhengighetene er injisert så CI-leddet og staging-leddet bruker
    hver sin transport, men SAMME sett og SAMME dom:
      post(kropp_event, idemnokkel) -> (status, beslutning)
      hendelse(ressurs)             -> gyldig hendelse (TILLAT-form)
      hendelse_uten(handling, ressurs) -> uten attestasjoner (UNNTAK)
      tukle(e)                      -> hendelsen med snudd attestasjon
    """
    talt: dict[str, int] = {}
    for forventet, i in bygg_sett():
        ressurs = f"m02f-{forventet.lower()}-{i:03d}"
        if forventet == "TILLAT":
            e = hendelse(ressurs)
        elif forventet == "STOPP":
            e = tukle(hendelse(ressurs))
        else:
            e = hendelse_uten(f"m02.finnes.ikke.{i}", ressurs)
        status, beslutning = post(e, idemnokkel(runde_id, forventet, i))
        if status != 200 or beslutning != forventet:
            raise SystemExit(
                f"AVBRUTT: hendelse {forventet}/{i} fikk"
                f" ({status}, {beslutning!r}) — settet er ikke settet,"
                " og en fordeling av noe annet telles ikke")
        talt[beslutning] = talt.get(beslutning, 0) + 1
    return talt


def artefakt(rader: list[tuple[int, str]], tenant: str, vert: str,
             ts: str) -> dict:
    """Artefaktet, med tallene REGNET AV RADENE — aldri av driveren.

    `rader` er [(loggpost_id, beslutning)] lest ut av `revisjonslogg`
    ETTER kjøringen (via idempotensnøklene). Sprikter tellingen fra
    fasiten, skrives artefaktet likevel — porten i KRAVGRENSER feller
    det, og et rødt artefakt som finnes er ærligere enn et grønt som
    ble valgt.
    """
    talt: dict[str, int] = {}
    for _, beslutning in rader:
        talt[beslutning] = talt.get(beslutning, 0) + 1
    return {
        "krav_id": "m02-fordeling-v1",
        "ts": ts,
        "bestatt": talt == FORDELING and len(rader) == sum(
            FORDELING.values()),
        "oppsett": {"modul": "m02_revisjonslogg", "tenant": tenant,
                    "vert": vert, "sett_versjon": SETT_VERSJON},
        "maalt": {"antall_tillat": talt.get("TILLAT", 0),
                  "antall_stopp": talt.get("STOPP", 0),
                  "antall_unntak": talt.get("UNNTAK", 0)},
        "rader": [[lid, beslutning] for lid, beslutning in
                  sorted(rader)],
    }
