"""Invitasjon av kolleger over HTTP (194/195).

MÅLT FØR DETTE: ingen dør skrev `brukermedlemskap`. Et firma som
registrerte seg selv var en énpersonsbedrift for alltid — hun var admin, og
kunne ikke slippe inn en eneste kollega.

TRE RUTER:

* POST /v1/invitasjoner          (`firma:inviter`) — admin lager en lenke.
  RÅTOKENET RETURNERES ÉN GANG og lagres aldri; basen har bare hashen.
  Lukker hun vinduet uten å kopiere lenken, må hun lage en ny — det er
  prisen for at den som får tak i basen ikke kan bruke invitasjonene.
* GET  /v1/invitasjoner          (`firma:inviter`) — hva som er sendt ut,
  og hva som er brukt. Aldri tokenet, bare hashens første tegn som et
  gjenkjennelsesmerke.
* POST /v1/invitasjoner/innloes  (`firma:opprett`) — den inviterte blir
  medlem.

HVORFOR INNLØSNINGEN BRUKER REGISTRANTENS SCOPE
Autoriteten er TOKENET, ikke scopet — sesjonen beviser bare hvem hun er, og
CSRF at det er hennes egen nettleser som ber. Ideelt sett hadde ruten stått
uten scopekrav, som `/v1/sesjon`, men `_autentiser` er bygget for ett
påkrevd scope og avviser `None`.

`firma:opprett` er likevel riktig i praksis, og grunnen er målt: en bruker
med TO medlemskap kan ikke logge inn i det hele tatt — `_firma_for_bruker`
svarer `firma_ikke_valgt` til firmavelgeren finnes (192). «Allerede ansatt i
et annet firma» er altså en tilstand systemet ikke kan nå ennå, og hver
eneste inviterte er registrant.

DETTE MÅ UTVIDES SAMMEN MED FIRMAVELGEREN. Den dagen en person kan høre til
to firmaer, vil en ansatt i firma A ikke kunne innløse en invitasjon til
firma B — hun har ikke `firma:opprett`. Porten
`test_kollegaen_blir_medlem_med_registrantens_scope` navngir bindingen.

TENANTEN STÅR I LENKEN, og det er ikke en lekkasje: den inviterte skal jo
inn dit. Den autoriserer ingenting alene — døra krever tokenet, og feil
tenant gir nøyaktig samme svar som feil token.
"""
from __future__ import annotations

import hashlib
import secrets

MAKS_TIMER = 720          # 30 døgn — samme tak som døra håndhever
STANDARD_TIMER = 168      # én uke
MAKS_ROLLER = 8
SCOPE = "firma:inviter"


def _hash(raa: str) -> str:
    return hashlib.sha256(raa.encode("utf-8")).hexdigest()


def nytt_token() -> tuple[str, str]:
    """(råtoken, hash). Råtokenet forlater prosessen ÉN gang, i svaret."""
    raa = secrets.token_urlsafe(32)
    return raa, _hash(raa)


def opprett_endepunkt(tjeneste, request):
    """POST /v1/invitasjoner — `firma:inviter`."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _feil, _kropp,
                                   _med_conn, _ok_lagret)
    rid = _rid(request)

    def kjor(conn):
        import psycopg

        tenant, bid = _browserkontekst(tjeneste, request, conn, rid, SCOPE)
        k = _kropp(request)

        roller = k.get("roller")
        if not isinstance(roller, list) or not roller:
            return _feil("request_feilformet", rid, 400, detalj="roller")
        if len(roller) > MAKS_ROLLER:
            return _feil("request_feilformet", rid, 400, detalj="for mange roller")
        if not all(isinstance(r, str) and r.strip() for r in roller):
            return _feil("request_feilformet", rid, 400, detalj="roller")
        roller = [r.strip() for r in roller]

        timer = k.get("timer", STANDARD_TIMER)
        if not isinstance(timer, int) or not 1 <= timer <= MAKS_TIMER:
            return _feil("request_feilformet", rid, 400, detalj="timer")

        raa, h = nytt_token()
        try:
            with conn.transaction():
                utloper = conn.execute(
                    "SELECT invitasjon_opprett(%s,%s,%s,%s,%s)",
                    (tenant, h, roller, f"bruker:{bid}", timer)).fetchone()[0]
        except psycopg.errors.InvalidParameterValue:
            # Dørens egen dom: ukjent rolle eller ulovlig gyldighet.
            return _feil("invitasjon_ugyldig", rid, 400)
        except psycopg.errors.IntegrityConstraintViolation:
            return _feil("invitasjon_ugyldig", rid, 400)

        # RÅTOKENET RETURNERES ÉN GANG. Det finnes ikke i basen, og kan
        # ikke hentes igjen — flaten må vise det med én gang.
        return _ok_lagret(conn, {"token": raa, "tenant": tenant,
                                 "roller": roller,
                                 "utloper": utloper.isoformat()}, rid)

    return _med_conn(tjeneste, rid, kjor)


def liste_endepunkt(tjeneste, request):
    """GET /v1/invitasjoner — `firma:inviter`."""
    from .app import _rid, kanonisk_json
    from .policyadmin_http import _browserkontekst, _med_conn
    rid = _rid(request)

    def kjor(conn):
        tenant, _bid = _browserkontekst(tjeneste, request, conn, rid, SCOPE)
        rader = conn.execute(
            "SELECT token_hash, roller, opprettet_av, opprettet, utloper,"
            " brukt_ts FROM invitasjon_liste(%s)", (tenant,)).fetchall()
        ut = []
        for h, roller, av, opprettet, utloper, brukt in rader:
            ut.append({
                # ALDRI TOKENET. De åtte første tegnene av hashen er et
                # gjenkjennelsesmerke — nok til å snakke om «den ene», for
                # lite til å gjette resten.
                "merke": h[:8],
                "roller": list(roller), "opprettet_av": av,
                "opprettet": opprettet.isoformat(),
                "utloper": utloper.isoformat(),
                "brukt": brukt.isoformat() if brukt else None,
            })
        return kanonisk_json({"invitasjoner": ut, "request_id": rid}, 200,
                             {"x-request-id": rid})

    return _med_conn(tjeneste, rid, kjor)


def innloes_endepunkt(tjeneste, request):
    """POST /v1/invitasjoner/innloes — `firma:opprett` (se modulens topp)."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _feil, _kropp,
                                   _med_conn, _ok_lagret)
    rid = _rid(request)

    def kjor(conn):
        import psycopg

        from db.pg import sett_kontekst

        # Autoriteten er TOKENET; scopet er bare det `_autentiser` krever.
        # `_browserkontekst` gjør resten: autentiserer, håndhever CSRF, og
        # gir oss bruker-id-en fra SESJONEN, aldri fra kroppen.
        kontekst, bid = _browserkontekst(tjeneste, request, conn, rid,
                                        "firma:opprett")
        k = _kropp(request)

        tenant = k.get("tenant")
        token = k.get("token")
        for verdi, felt in ((tenant, "tenant"), (token, "token")):
            if not isinstance(verdi, str) or not verdi.strip():
                return _feil("request_feilformet", rid, 400, detalj=felt)

        try:
            with conn.transaction():
                roller = conn.execute(
                    "SELECT invitasjon_innloes(%s,%s,%s)",
                    (tenant.strip(), _hash(token.strip()), bid)).fetchone()[0]
        except psycopg.errors.IntegrityConstraintViolation:
            # ÉN KODE FOR ALLE FIRE AVSLAGENE (finnes ikke, feil firma,
            # brukt, utløpt) — å skille dem ville latt noen prøve seg fram.
            return _feil("invitasjon_ugyldig", rid, 409)
        except psycopg.errors.ForeignKeyViolation:
            return _feil("invitasjon_ugyldig", rid, 409)

        # Konteksten er lagt tilbake av døra; sett den eksplisitt igjen så
        # `_ok_lagret` skriver kvitteringen der kalleren sto.
        sett_kontekst(conn, kontekst, f"bruker:{bid}", rid)
        return _ok_lagret(conn, {"tenant": tenant.strip(),
                                 "roller": list(roller)}, rid)

    return _med_conn(tjeneste, rid, kjor)
