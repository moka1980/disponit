"""E-postsenderen: kopien av det som alt står i portalen.

Innboksen er sannheten; denne sender en KOPI. Det avgjør nesten alle valgene
under — særlig at en feilet sending aldri er kritisk: varselet står der
uansett, mottakeren ser det neste gang hun logger inn, og driften skal ikke
vekkes av at én e-post ikke gikk.

TEKSTEN RENDRES HER, ikke i databasen. Raden bærer `tekstnokkel` + `parametre`,
og lokaliseringen skjer ved sending — da leses varselet på mottakerens språk,
og en rettet oversettelse gjelder også for det som alt står i kø. Databasen
skal ikke kunne noe språk.

SMTP-oppsettet kommer fra credentials, aldri fra koden. Eier: WCAGvakts konto
(`send.one.com:587`) brukes til TEST og byttes senere — derfor er avsender og
vert konfigurasjon, så byttet blir en env-endring og ikke en kodeendring.
Testmail kommer altså fra en wcagvakt-adresse; greit for test, ikke for ekte
godkjennere.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

GRENSE = int(os.environ.get("DISPONIT_VARSEL_GRENSE", "50"))
MAKS_FORSOK = int(os.environ.get("DISPONIT_VARSEL_MAKS_FORSOK", "3"))
BACKOFF_MIN = int(os.environ.get("DISPONIT_VARSEL_BACKOFF_MIN", "15"))


def _locale(sprak: str) -> dict:
    """Tekstene, fra samme `locales/` som flaten bruker.

    Én kilde til tekst — ikke en egen e-postmal som driver fra portalen. Det
    var hele poenget med å lagre nøkkel og ikke setning.

    Roten utledes fra MODULENS EGEN plassering, ikke fra en hardkodet
    driftssti. `locales/` ligger i repoet, og denne filen ligger i det samme
    repoet — så `parents[2]` er svaret både på staging (der utsjekken ER
    `/opt/disponit/aktiv`) og i CI, i en utviklers arbeidskopi og i en
    worktree. Den hardkodede stien var sann bare ett sted, og i CI fantes den
    ikke: senderen kastet `FileNotFoundError` på hver eneste e-post.
    `DISPONIT_REPO` overstyrer fortsatt, som i `policy-rundtur.py`.
    """
    rot = Path(os.environ.get("DISPONIT_REPO")
               or Path(__file__).resolve().parents[2])
    sti = rot / "locales" / f"{sprak}.json"
    if not sti.exists():
        sti = rot / "locales" / "nb.json"
    return json.loads(sti.read_text(encoding="utf-8"))


def rendre(tekster: dict, nokkel: str, parametre: dict) -> str:
    """Nøkkel + parametre → setning. Ukjent nøkkel gir nøkkelen selv.

    En manglende oversettelse skal være SYNLIG, ikke bli til en tom e-post:
    `varsel.attestering_venter` i innboksen er stygt, men det forteller
    sannheten. En tom melding forteller ingenting.
    """
    s = tekster.get(nokkel, nokkel)
    for k, v in (parametre or {}).items():
        s = s.replace("{" + str(k) + "}", str(v))
    return s


def _smtp_oppsett() -> dict | None:
    """Vert, port, bruker, passord og avsender — alt fra miljøet.

    Mangler noe, sender vi ikke. Vi markerer heller ikke radene som feilet:
    et manglende oppsett er en DRIFTSTILSTAND, ikke en egenskap ved varselet,
    og å brenne forsøkstelleren på det ville stille kastet varsler som er helt
    i orden.
    """
    n = {k: os.environ.get(f"DISPONIT_SMTP_{k.upper()}")
         for k in ("vert", "port", "bruker", "passord", "avsender")}
    if not all(n.values()):
        return None
    n["port"] = int(n["port"])
    return n


def _send_ekte(oppsett: dict, til: str, emne: str, tekst: str) -> None:
    m = EmailMessage()
    m["From"] = oppsett["avsender"]
    m["To"] = til
    m["Subject"] = emne
    m.set_content(tekst)
    ctx = ssl.create_default_context()
    with smtplib.SMTP(oppsett["vert"], oppsett["port"], timeout=20) as s:
        s.starttls(context=ctx)
        s.login(oppsett["bruker"], oppsett["passord"])
        s.send_message(m)


def kjor(conn, *, send=None, oppsett=None, sprak: str = "nb") -> dict:
    """Tøm køen én gang. -> {sendt, feilet, hoppet_over}.

    `send` er injiserbar, så testene kan måle HVA som ville blitt sendt uten en
    e-postserver. Standard er ekte SMTP.

    Hver rad står for seg: én adresse som ikke tar imot skal ikke stoppe resten
    av køen. Det er også derfor statusen settes per rad gjennom
    `varsel_sett_epoststatus`, som bare flytter `koet` → `sendt|feilet` og
    dermed ikke kan sende det samme to ganger om to sendere kjører samtidig.
    """
    oppsett = oppsett or _smtp_oppsett()
    if oppsett is None and send is None:
        # Ikke konfigurert. Si det tydelig og la køen ligge urørt.
        return {"sendt": 0, "feilet": 0, "hoppet_over": 0,
                "grunn": "smtp_ikke_konfigurert"}
    send = send or (lambda til, emne, tekst: _send_ekte(oppsett, til, emne,
                                                        tekst))
    tekster = _locale(sprak)
    emne = tekster.get("varsel.epost.emne", "Disponit")
    # Først: gi feilede rader en ny sjanse. Egen SQL-funksjon, ikke et utvidet
    # plukk — `koet` forblir den eneste sendbare tilstanden, så to sendere
    # aldri kan ta samme rad.
    conn.execute("SELECT varsel_rekoe_feilede(%s * interval '1 minute', %s)",
                 (BACKOFF_MIN, MAKS_FORSOK))
    conn.commit()
    rader = conn.execute("SELECT * FROM varselkandidater(%s)",
                         (GRENSE,)).fetchall()
    sendt = feilet = hoppet = 0
    for vid, _tenant, epost, nokkel, parametre, forsok in rader:
        if forsok >= MAKS_FORSOK:
            # Gitt opp for lenge siden. Raden blir stående i portalen — det er
            # der varselet egentlig bor — men vi slutter å plage serveren.
            conn.execute("SELECT varsel_sett_epoststatus(%s,'feilet',%s)",
                         (vid, "maks forsøk nådd"))
            conn.commit()
            hoppet += 1
            continue
        try:
            send(epost, emne, rendre(tekster, nokkel, parametre))
        except Exception as e:                                # noqa: BLE001
            conn.execute("SELECT varsel_sett_epoststatus(%s,'feilet',%s)",
                         (vid, f"{type(e).__name__}: {e}"))
            conn.commit()
            feilet += 1
            continue
        conn.execute("SELECT varsel_sett_epoststatus(%s,'sendt',NULL)", (vid,))
        conn.commit()
        sendt += 1
    return {"sendt": sendt, "feilet": feilet, "hoppet_over": hoppet}
