"""Inngangspunkt for `disponit-domenerevalidering.service`.

Wirer sammen de tre delene og gjør INGEN beslutninger selv:

  1. resolverkonfigurasjonen fra miljøet, med diversitetsporten (§2.4) — feiler
     den, avsluttes prosessen FØR databasen er rørt. «Oppstart nektes» skal
     bety at ingenting skjedde, ikke at halve populasjonen ble revalidert mot
     én feilkilde.
  2. TXT-transporten, injisert her og ikke i modulen: det er diversiteten og
     enigheten som er kontrakten, ikke biblioteket.
  3. `kjor()`, som tar arbeidernøkkelen og respekterer budsjettet.

Resultatet skrives som én linje JSON på stdout — journalen er der drift leser
den. Målte egenskaper (fordeling, kjøringer over K, dreneringstid) rapporteres;
de har ingen bestått/ikke bestått.

ALARMEN er unntaket fra det (§2.4). Slår terskelen for bred resolverfeil inn,
avsluttes prosessen med feilkode, slik at systemd setter oneshot-unitten i
`failed` — samme vei som ryddejobbens §6-alarm bruker. Et JSON-felt alene ville
vært en alarm ingenting lytter på: uten en konsument i journalen ville
`systemctl` rapportert kjøringen som vellykket mens domeneautorisasjonene
eldes mot 72-timersgrensen.
"""
from __future__ import annotations

import json
import os
import sys

from . import domenerevalidering as dr


def _txt_oppslag(server: str):
    """TXT-oppslag mot ÉN navnetjener. Returnerer mengden av TXT-verdier.

    dnspython importeres her, ikke på modulnivå: enhetstestene av scheduleren
    injiserer sine egne oppslag og skal ikke trenge en DNS-avhengighet for å
    kjøre. Mangler den i drift, er det en hard oppstartsfeil — ikke en stille
    degradering til «ingen svar», som ville sett ut som en bred resolverfeil.
    """
    try:
        import dns.resolver                              # type: ignore
    except ImportError as e:                             # pragma: no cover
        raise RuntimeError(
            "dnspython mangler — resolvertransporten kan ikke settes opp") from e

    def oppslag(hostname: str) -> frozenset[str]:
        r = dns.resolver.Resolver(configure=False)
        r.nameservers = [server]
        r.lifetime = 5.0
        try:
            svar = r.resolve(hostname, "TXT")
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
            # ET AUTORITATIVT NEI ER ET SVAR (Codex P2). Navnet finnes ikke,
            # eller det har ingen TXT-post: da VET vi at beviset ikke står der,
            # og det er en helt annen ting enn at vi ikke fikk kontakt. Kastet
            # oppslaget her, ble utfallet «uenige resolvere» — samme bøtte som
            # en resolver som er nede. For førstegangsverifiseringen er det
            # normaltilstanden rett etter utstedelsen (kunden har ikke lagt ut
            # posten ennå), så en alarm på den bøtta ville stått rødt støtt.
            # Tom mengde: to resolvere som begge sier «ingen TXT» ER enige, og
            # basen avviser beviset som manglende — `ikke_bevist`, ikke feil.
            # Timeout, SERVFAIL og NoNameservers slipper fortsatt ut som feil.
            return frozenset()
        return frozenset(
            b"".join(rr.strings).decode("utf-8", "replace") for rr in svar)

    return oppslag


def resolvere() -> list[dr.Resolver]:
    """DISPONIT_RESOLVERE = `navn@operator/nett=adresse`, komma-separert."""
    rå = os.environ.get("DISPONIT_RESOLVERE", "").strip()
    if not rå:
        raise dr.Diversitetsfeil(
            "DISPONIT_RESOLVERE er ikke satt — oppstart nektes")
    ut: list[dr.Resolver] = []
    for bit in (b.strip() for b in rå.split(",")):
        if not bit or "@" not in bit or "/" not in bit or "=" not in bit:
            raise dr.Diversitetsfeil(
                f"ugyldig resolverspesifikasjon {bit!r} "
                f"(krever navn@operator/nett=adresse)")
        navn, resten = bit.split("@", 1)
        operator, resten = resten.split("/", 1)
        nett, adresse = resten.split("=", 1)
        # Codex (P2): skilletegnene alene er ikke en gyldig spesifikasjon.
        # `a@/net1=1.1.1.1,b@op2/=8.8.8.8` har både «@», «/» og «=», men to
        # TOMME uavhengighetsfelter — og en tom streng teller som en distinkt
        # operatør/nett i `krev_diversitet`, så porten ville sagt god for et
        # oppsett der uavhengigheten er UDOKUMENTERT, ikke påvist. Hver
        # komponent må derfor være ikke-tom; adressen likeså, ellers ville
        # resolveren blitt bygget mot en tom navnetjener.
        navn, operator = navn.strip(), operator.strip()
        nett, adresse = nett.strip(), adresse.strip()
        tomme = [felt for felt, verdi in
                 (("navn", navn), ("operator", operator),
                  ("nett", nett), ("adresse", adresse)) if not verdi]
        if tomme:
            raise dr.Diversitetsfeil(
                f"ugyldig resolverspesifikasjon {bit!r}: tomt {', '.join(tomme)} "
                f"(krever navn@operator/nett=adresse)")
        ut.append(dr.Resolver(navn=navn, operator=operator, nett=nett,
                              slå_opp=_txt_oppslag(adresse)))
    dr.krev_diversitet(ut)          # deploy-porten: kaster før DB-en røres
    return ut


def _koble(dsn: str):
    """Tilkoblingen bak et navn på modulnivå.

    Importen er fortsatt lat (arbeideren skal kunne importeres uten et
    databasebibliotek i hånda), men navnet gir testene et sømpunkt — samme
    mønster som ryddejobben, slik at alarmveien måles på det `main()` faktisk
    returnerer og ikke på en etterligning av den.
    """
    from db.pg import koble
    return koble(dsn)


def main() -> int:
    from db.hemmeligheter import last_credentials
    last_credentials()  # PR-009 §5: LoadCredential før env-lesing under
    try:
        res_konf = resolvere()
    except dr.Diversitetsfeil as e:
        print(json.dumps({"hendelse": "oppstart_nektet", "grunn": str(e)}),
              file=sys.stderr)
        return 2

    dsn = os.environ.get("DISPONIT_DOMAINS_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        print(json.dumps({"hendelse": "oppstart_nektet",
                          "grunn": "DISPONIT_DOMAINS_URL mangler"}),
              file=sys.stderr)
        return 2

    conn = _koble(dsn)
    try:
        r = dr.kjor(conn, res_konf)
    finally:
        conn.close()

    print(json.dumps({
        "hendelse": "revalideringskjoring",
        # Håndhevede grenser (invarianter).
        "budsjett.ko2_pluss_ko3_over_K": int(r.ko2_pluss_ko3 > r.budsjett_K),
        "sikkerhetsnett.utsatt": 0,
        "populasjon_N": r.populasjon_N, "budsjett_K": r.budsjett_K,
        "plukket": {"ko1": r.plukket_ko1, "ko2": r.plukket_ko2,
                    "ko3": r.plukket_ko3},
        "vellykket": r.vellykket,
        "uenige_resolvere": r.uenige_resolvere,
        "oppslagsfeil": r.oppslagsfeil,
        # Målte egenskaper — rapporteres, ingen bestått/ikke bestått.
        "sikkerhetsnett.kjoringer_over_K": int(r.kjoring_over_K),
        "fordeling.maks_andel_per_time": (
            max(r.fordeling_per_time.values()) / max(sum(
                r.fordeling_per_time.values()), 1)
            if r.fordeling_per_time else 0),
        "alarm.terskel_utlost": int(r.alarm_utlost),
        "maks_samtidighet": r.maks_samtidighet,
    }))
    if r.alarm_utlost:
        # Codex (P1): terskelen skal FØRE et sted. `alarm.terskel_utlost: 1` i
        # en ellers vellykket kjøring er ingen alarm — ingen journalkonsument,
        # ingen OnFailure og ingen annen utrullet vei leser det feltet, så
        # systemd bokførte en bred resolverfeil som en vellykket aktivering
        # mens domeneautorisasjonene eldes mot 72-timersgrensen. Med en
        # feilkode havner unitten i `failed`, synlig for `systemctl --failed`
        # og for enhver OnFailure drift senere henger på — samme kontrakt som
        # ryddejobbens §6-alarm.
        #
        # Radene som FAKTISK ble revalidert er alt committet (én commit per
        # rad), så feilkoden kaster ikke arbeid: den sier at kjøringen ikke kan
        # stås inne for, ikke at den ikke skjedde.
        print(json.dumps({
            "hendelse": "revalideringsalarm",
            "grunn": "bred_resolverfeil",
            "andel_terskel": dr.ALARM_ANDEL,
            "uenige_resolvere": r.uenige_resolvere,
            "oppslagsfeil": r.oppslagsfeil,
            "plukket": r.plukket_ko1 + r.ko2_pluss_ko3,
        }), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(main())
