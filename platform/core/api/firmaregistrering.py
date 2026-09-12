"""Et firma registrerer seg selv (192).

EIERS MÅL: «man kan lett registrere en bedrift og sette policy og resten
skal skje automatisk.» Målt før denne ruten kostet det fem steg, tre av
dem som root på verten: DNS, TLS + nginx-blokk, `init-tenant.sh`, en
OIDC-binding og en medlemskapsrad.

HAKEN SOM MÅTTE LØSES FØRST: en helt ny bruker har ingen medlemskap, og
innloggingen avviste henne med `ingen_tilgang` før hun rakk å registrere
noe — hun kunne ikke bli kunde fordi hun ikke var kunde. Callbacken gir
henne nå et ekte medlemskap på den reserverte tenanten `_registrering`,
med rollen `registrant` og dens ene scope. Denne ruten er det eneste den
rollen kan gjøre.

FIRE STEG, OG REKKEFØLGEN ER IKKE VILKÅRLIG:

  1. `firma_selvregistrer` — firmaraden OG admin-medlemskapet i ÉN
     transaksjon, og registrantraden ryddes i samme slengen. Et firma
     uten medlem er et firma ingen kommer inn i.
  2. DEK — uten den kan ingen modul kryptere noe for tenanten.
  3. Bransjemalen valideres (`valider_ny_policy`, som i oppsettsveien) og
     aktiveres gjennom `firma_bootstrap_policy`, som nekter hvis tenanten
     har så mye som én policyrad fra før.
  4. Svaret bærer firmanavnet og prøveperiodens frist.

HVORFOR IKKE `policyregister.registrer`: den er oppsettsveien og kjører
på migratorforbindelsen. Runtime har kun `SELECT ON policyer` — «policyer
endres av en egen vei» — og å gi den INSERT ville latt en feil i en rute
aktivere policy uten attestasjon, altså svekket fire-øyne (V6).
"""
from __future__ import annotations

import re
import unicodedata

MAKS_NAVN = 200
MAKS_SLUG = 63
PROVE_DOGN = 30

#: Kunden velger én av disse. Hun leverer ALDRI policy selv — da ville
#: registreringsruten vært en vei til å skrive sine egne fullmakter.
BRANSJEMALER = {
    "tjenestebedrift": "bransjemal-tjenestebedrift.yaml",
    "handverk-bygg":   "bransjemal-handverk-bygg.yaml",
    "netthandel":      "bransjemal-netthandel.yaml",
}

_UGYLDIG = re.compile(r"[^a-z0-9]+")


def slug_av(navn: str) -> str:
    """Firmanavn → tenant-slug, på formen `firma_tenant_form` krever
    (190): små bokstaver, sifre og bindestrek, minst to tegn.

    Æ/Ø/Å translittereres FØR unicode-normaliseringen. `NFKD` splitter
    ikke Ø i O + ring — den har ingen dekomponering — så «Øre AS» ville
    blitt «re-as» uten dette, altså et navn firmaet ikke kjenner igjen.
    """
    s = navn.strip().lower()
    for fra, til in (("æ", "ae"), ("ø", "oe"), ("å", "aa"),
                     ("ä", "ae"), ("ö", "oe"), ("ü", "ue")):
        s = s.replace(fra, til)
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _UGYLDIG.sub("-", s).strip("-")
    s = s[:MAKS_SLUG].rstrip("-")
    return s


def _kandidater(grunn: str):
    """Slugen, så `-2`, `-3` … Kollisjon er ikke en feil: to firmaer kan
    hete det samme, og den andre skal ikke måtte finne på et nytt navn."""
    yield grunn
    for n in range(2, 21):
        hale = f"-{n}"
        yield grunn[:MAKS_SLUG - len(hale)].rstrip("-") + hale


def registrer_firma(tjeneste, request):
    """POST /v1/firma/registrer — `firma:opprett`."""
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _feil, _kropp,
                                   _med_conn, _ok_lagret)
    rid = _rid(request)

    def kjor(conn):
        import psycopg

        from db.pg import sett_kontekst

        kontekst, bid = _browserkontekst(
            tjeneste, request, conn, rid, "firma:opprett")
        # Scopet er lås 1; denne er lås 2. Døra krever det samme, men en
        # rute som lar en kundesesjon komme SÅ langt har allerede sagt noe
        # galt — og feilmeldingen herfra er den ærlige.
        if kontekst != "_registrering":
            return _feil("firma_ikke_registrant", rid, 409)

        k = _kropp(request)
        navn = k.get("navn")
        if not isinstance(navn, str) or not navn.strip():
            return _feil("request_feilformet", rid, 400, detalj="navn")
        if len(navn) > MAKS_NAVN:
            return _feil("request_feilformet", rid, 400, detalj="navn er langt")
        navn = navn.strip()

        org = k.get("orgnummer")
        if org is not None:
            if not isinstance(org, str):
                return _feil("request_feilformet", rid, 400, detalj="orgnummer")
            org = org.replace(" ", "") or None
            # FORMEN MÅLES HER, ikke bare av CHECK-en i basen (CodeRabbit).
            # `firma_registrer` ville reist en CheckViolation, som er en
            # IntegrityConstraintViolation — altså samme klasse som taket,
            # og kunden hadde fått «taket på 3 firmaer er nådd» for et
            # feilskrevet organisasjonsnummer. MOD-11 er fortsatt basens
            # jobb; her måles bare at det ER ni sifre.
            if org is not None and (len(org) != 9 or not org.isdigit()):
                return _feil("request_feilformet", rid, 400,
                             detalj="orgnummer er ni siffer")

        bransje = k.get("bransje")
        if bransje not in BRANSJEMALER:
            return _feil("request_feilformet", rid, 400, detalj="bransje")

        grunn = slug_av(navn)
        if len(grunn) < 2:
            # Et navn som bare består av tegn slugen ikke tar imot.
            return _feil("request_feilformet", rid, 400, detalj="navn")

        # STEG 1: firma + admin-medlemskap, atomisk. Kollisjon på slugen er
        # ikke en feil — vi prøver neste kandidat.
        tenant = None
        frist = None
        for kandidat in _kandidater(grunn):
            try:
                with conn.transaction():
                    frist = conn.execute(
                        "SELECT firma_selvregistrer(%s,%s,%s,%s,%s)",
                        (kandidat, navn, org, bid, PROVE_DOGN)).fetchone()[0]
                tenant = kandidat
                break
            except psycopg.errors.UniqueViolation:
                continue          # navnet er tatt; prøv neste
            except psycopg.errors.IntegrityConstraintViolation:
                return _feil("firma_tak_naadd", rid, 409)
            except psycopg.errors.InsufficientPrivilege:
                return _feil("firma_ikke_registrant", rid, 409)
            except psycopg.errors.InvalidParameterValue:
                return _feil("request_feilformet", rid, 400)
        if tenant is None:
            return _feil("firma_navn_opptatt", rid, 409)

        # STEG 2-3: nøkkel og policy hører til det NYE firmaet, så
        # konteksten flyttes dit. Feiler noe her, står firmaet igjen uten
        # policy — og det er bedre enn å rulle tilbake medlemskapet hennes,
        # for da hadde hun hverken firma eller vei tilbake.
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        from db import kryptering
        kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
        _aktiver_bransjemal(conn, tenant, bransje)
        conn.commit()

        sett_kontekst(conn, kontekst, f"bruker:{bid}", rid)
        return _ok_lagret(conn, {"tenant": tenant, "navn": navn,
                                 "prove_utloper": frist.isoformat(),
                                 "bransje": bransje}, rid)

    return _med_conn(tjeneste, rid, kjor)


def _aktiver_bransjemal(conn, tenant: str, bransje: str) -> None:
    """Leser malen som FØLGER MED APPEN, validerer den som oppsettsveien
    gjør, og skriver den gjennom bootstrap-døra.

    Malen kommer aldri fra forespørselen — kunden velger bare hvilken av
    tre. En rute som tok imot policy ville vært en vei til å skrive sine
    egne fullmakter.
    """
    import json
    from pathlib import Path

    import yaml

    from api.policyregister import innholds_hash
    from policy_validator.schema import valider_ny_policy

    sti = (Path(__file__).resolve().parents[3] / "policies"
           / BRANSJEMALER[bransje])
    policy = yaml.safe_load(sti.read_text(encoding="utf-8"))
    feil = valider_ny_policy(policy)
    if feil:
        # Malene ligger i repoet og valideres i CI; kommer vi hit, er det
        # en driftsfeil, ikke noe kunden har gjort.
        raise RuntimeError(f"bransjemal {bransje} er ugyldig: {feil}")
    meta = policy["meta"]
    conn.execute(
        "SELECT firma_bootstrap_policy(%s,%s,%s,%s,%s,%s)",
        (tenant, meta["policy_id"], meta["versjon"], innholds_hash(policy),
         meta["status"], json.dumps(policy, ensure_ascii=False)))
