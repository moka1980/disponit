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
import sys
from email.message import EmailMessage
from pathlib import Path

GRENSE = int(os.environ.get("DISPONIT_VARSEL_GRENSE", "50"))
MAKS_FORSOK = int(os.environ.get("DISPONIT_VARSEL_MAKS_FORSOK", "3"))
BACKOFF_MIN = int(os.environ.get("DISPONIT_VARSEL_BACKOFF_MIN", "15"))
#: Hvor lenge et klaim gjelder før en annen kjøring kan ta raden igjen.
#: MYE lengre enn SMTP-timeouten (20 s) med vilje: leasen skal aldri løpe ut
#: for en sending som fortsatt pågår — da ville den gjenskapt dobbeltsendingen
#: klaimet finnes for å hindre.
LEASE_MIN = int(os.environ.get("DISPONIT_VARSEL_LEASE_MIN", "30"))


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
    """Tøm køen én gang. -> {sendt, feilet, gjenkoet, mistet}.

    `send` er injiserbar, så testene kan måle HVA som ville blitt sendt uten en
    e-postserver. Standard er ekte SMTP.

    RADEN KLAIMES FØR SMTP, og klaimet COMMITTES før sendingen begynner. Det er
    hele forskjellen på denne løkka og den forrige (Codex P1): før plukket den
    `koet`-rader med en ren SELECT og flyttet statusen etter at e-posten var
    ute. To sendere som overlappet — timeren går hvert 5. minutt, og en treg
    SMTP-server gjør en kjøring lengre enn det — hentet da samme rad og sendte
    begge e-posten. At den andre statusoppdateringen returnerte `false` var en
    opplysning som kom for sent: SMTP er utført, og en sendt e-post kan ingen
    ROLLBACK hente hjem igjen.

    Committen mellom klaim og sending er ikke en detalj: en uncommittet UPDATE
    er usynlig for den andre senderen, og da ville vi vært like langt så snart
    SMTP-kallet tok tid.

    ÉN RAD OM GANGEN (Codex P2). Klaimet hentet først opptil `GRENSE` rader og
    committet HELE bunken til `under_sending` før det første SMTP-kallet. Da
    lå rad nummer femti klaimet mens de 49 foran ble sendt — minutter, ikke
    mikrosekunder — og i det vinduet kunne mottakeren lese varselet, melde seg
    av e-post eller få runden lukket under seg. Ingen av de tre veiene avlyser
    en `under_sending`-rad, og det er med vilje: den raden er i et SMTP-kall,
    og en e-post som er ute kan ikke kalles hjem. Men med et bunkeklaim var
    den premissen usann for alle radene bak den første, og den bufrede løkka
    sendte dem likevel.

    Klaimvinduet er derfor gjort like langt som sendingen det verner: hver
    runde klaimer nøyaktig én rad og sender den med det samme. `GRENSE` er
    fortsatt taket for hvor mange rader én kjøring tar — nå som antall runder,
    ikke som bunkestørrelse — og FIFO-en er den samme, siden klaimet ordner på
    `opprettet`. Prisen er én tur til databasen per e-post, som er ingenting
    ved siden av et SMTP-kall.

    Det er også det som gjør `varsel.I_KO` sant: en avmelding rekker nå alle
    radene som ennå ikke er i luften, fordi ingen rad er klaimet før den skal
    sendes.

    Hver rad står for seg: én adresse som ikke tar imot skal ikke stoppe resten
    av køen.

    `mistet` teller de radene der statusoppdateringen ikke fant klaimet igjen.
    Med en lease som er mye lengre enn SMTP-timeouten skal det aldri skje — og
    nettopp derfor skal det ikke forbli usagt hvis det gjør det: et tapt klaim
    betyr at raden kan bli sendt en gang til, altså at tallene i denne
    beregningen er de eneste stedet det ville vist seg. Før ble returverdien
    kastet uten å bli lest.
    """
    oppsett = oppsett or _smtp_oppsett()
    if oppsett is None and send is None:
        # Ikke konfigurert. Si det tydelig og la køen ligge urørt.
        return {"sendt": 0, "feilet": 0, "gjenkoet": 0, "mistet": 0,
                "grunn": "smtp_ikke_konfigurert"}
    send = send or (lambda til, emne, tekst: _send_ekte(oppsett, til, emne,
                                                        tekst))
    tekster = _locale(sprak)
    emne = tekster.get("varsel.epost.emne", "Disponit")
    # Først: tilbake i køen med det som ikke kom frem — feilede rader som har
    # ventet ut backoffen, og klaim fra en kjøring som døde underveis. Egen
    # SQL-funksjon, ikke et utvidet klaim: `koet` forblir den eneste tilstanden
    # et klaim kan ta fra.
    gjenkoet = conn.execute(
        "SELECT varsel_rekoe(%s * interval '1 minute', %s,"
        "                    %s * interval '1 minute')",
        (BACKOFF_MIN, MAKS_FORSOK, LEASE_MIN)).fetchone()[0]
    conn.commit()
    sendt = feilet = mistet = 0

    def _sett(vid, status, feil=None):
        beholdt = conn.execute(
            "SELECT varsel_sett_epoststatus(%s,%s,%s)",
            (vid, status, feil)).fetchone()[0]
        conn.commit()
        return beholdt

    for _ in range(max(1, GRENSE)):
        # Klaimet: `koet` → `under_sending` i samme setning som leser raden.
        # ÉN rad, og den sendes med det samme: vinduet der raden er tatt ut av
        # køen skal ikke være lengre enn sendingen det verner.
        rad = conn.execute("SELECT * FROM varsel_klaim_epost(%s,%s)",
                           (1, MAKS_FORSOK)).fetchone()
        conn.commit()      # …og ut av alle andres kø FØR SMTP-kallet.
        if rad is None:
            break          # køen er tom — ingenting mer å hente denne runden.
        vid, _tenant, epost, nokkel, parametre, forsok = rad
        try:
            send(epost, emne, rendre(tekster, nokkel, parametre))
        except Exception as e:                                # noqa: BLE001
            beholdt = _sett(vid, "feilet",
                            f"forsøk {forsok}/{MAKS_FORSOK}: "
                            f"{type(e).__name__}: {e}")
            feilet += 1
        else:
            beholdt = _sett(vid, "sendt")
            sendt += 1
        if not beholdt:
            # Klaimet var ikke vårt lenger. Skal ikke kunne skje med en lease
            # som er mye lengre enn SMTP-timeouten — og da er det nettopp det
            # som gjør det verdt å si fra om: raden kan bli sendt en gang til.
            mistet += 1
            print(f"varselsender: ADVARSEL mistet klaim på varsel {vid}",
                  file=sys.stderr)
    return {"sendt": sendt, "feilet": feilet, "gjenkoet": gjenkoet,
            "mistet": mistet}
