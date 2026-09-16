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

import json
import re
import unicodedata
from pathlib import Path

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

#: FULLMAKTENE KUNDEN KAN GI PLATTFORMEN VED REGISTRERINGEN (207). Hver er
#: en utvidelse i `policies/utvidelser/` — de samme filene den styrte veien
#: limer inn for eierens egne tenanter. Bransjemalen røres ikke (byte-bundet
#: til M-02s akseptartefakt); utvidelsene flettes inn i KOPIEN som blir
#: tenantens bootstrap-policy.
#:
#: HVORFOR HER OG IKKE ETTERPÅ: V6 krever to attestasjoner for en utvidelse
#: av fullmaktene, og én fra en annen enn forfatteren. Et enkeltpersonfirma
#: kan aldri nå det kvorumet — så valget tas der policyen fødes, av den som
#: registrerer, som del av en policy ingen har attestert ennå. Kunden velger
#: bare HVILKE av disse; hun leverer aldri policy selv.
FULLMAKTER = {
    "kundeservice-svar":     "kundeservice-svar.yaml",
    "kampanje-send":         "kampanje-send.yaml",
    "purring-inkassovarsel": "purring-inkassovarsel.yaml",
    "tilbud-generer":        "tilbud-generer.yaml",
}

_UGYLDIG = re.compile(r"[^a-z0-9]+")

#: SAMME FORM SOM BASEN krever (`firma_tenant_form`, 190), skrevet her fordi
#: et valgt kortnavn skal avvises med `request_feilformet` og et tydelig felt
#: — ikke som en CheckViolation, som er samme unntaksklasse som firmataket og
#: derfor ville gitt henne «taket er nådd» for en bindestrek på feil plass.
FORM = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def er_reservert(kortnavn: str) -> bool:
    """`_plattform`, `_registrering`, `_oidc` er plattformens egne kontekster.

    FORM-en avviser dem allerede (understrek er ikke med i mønsteret), så
    dette er et belte til seler: skulle mønsteret en dag åpnes, skal ikke en
    kunde kunne registrere seg som en reservert kontekst i samme slengen.
    """
    return kortnavn.startswith("_")


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


def kandidater_for(navn: str, valgt: str | None) -> list[str]:
    """Kortnavnene vi skal prøve, i rekkefølge.

    ET VALGT KORTNAVN GÅR ETT FORSØK. Et utledet kan trygt bli `-2`: hun var
    likegyldig til det. Et valgt skal aldri stille bli til noe annet — da får
    hun beskjed, og velger selv. Kortnavnet er permanent (kolonne i 328
    tabeller; `firma_oppdater` kan ikke endre det), så en `-2` hun aldri ba om
    ville fulgt firmaet for alltid.

    SKILT UT SOM EGEN FUNKSJON fordi porten ellers måtte gå gjennom en hel
    browserøkt for å nå beslutningen — og min første test gikk i stedet rett
    på basedøra, altså forbi denne linja. Mutasjonen «ignorer det valgte
    kortnavnet» sto da grønn. En test som ikke går kallerens vei, måler en
    annen vei.
    """
    grunn = valgt or slug_av(navn)
    return [grunn] if valgt else list(_kandidater(grunn))


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
        # FULLMAKTENE: en liste av navn fra det LUKKEDE settet, tom om hun
        # ikke ga noen. Et ukjent navn er en feilformet forespørsel, ikke
        # noe som stille hoppes over — da ville hun trodd hun ga en
        # fullmakt plattformen aldri fikk.
        fullmakter = k.get("fullmakter", [])
        if not isinstance(fullmakter, list) or not all(
                isinstance(x, str) and x in FULLMAKTER for x in fullmakter):
            return _feil("request_feilformet", rid, 400, detalj="fullmakter")
        fullmakter = sorted(set(fullmakter))
        # …OG DE MÅ PASSE BRANSJEN: kundesvaret trenger DLP-vitnet, som bare
        # tjenestebedrift-malen har. Et valg malen ikke bærer er 400 med
        # navnet — ikke en policy som validerer og stille mangler det.
        utenfor = [n for n in fullmakter
                   if n not in FULLMAKTER_FOR_BRANSJE[bransje]]
        if utenfor:
            return _feil("request_feilformet", rid, 400,
                         detalj=f"fullmakter: {', '.join(utenfor)} passer"
                                f" ikke bransjen {bransje}")

        # KORTNAVNET KAN VELGES, OG DA ER DET ET VALG.
        #
        # Eier, etter å ha registrert seg selv: «kortnavn feltet er ikke med i
        # registrering når kunden selv registrerer seg». Flaten fyller det nå
        # ut fra firmanavnet mens hun skriver — men rører hun feltet, sendes
        # det MED, og da er det hennes.
        #
        # Hvorfor forskjellen betyr noe: kortnavnet er PERMANENT. Det står
        # som kolonne i 328 tabeller (målt) og er nøkkelen som holder kundene
        # fra hverandre; `firma_oppdater` kan endre navn og orgnummer, ikke
        # dette. Et utledet kortnavn kan derfor få en `-2` uten at noen
        # merker det — det var hun uansett likegyldig til. Et VALGT kortnavn
        # skal aldri stille bli til noe annet: da får hun beskjed, og velger
        # selv.
        valgt = k.get("kortnavn")
        if valgt is not None:
            if not isinstance(valgt, str) or not FORM.match(valgt.strip()):
                return _feil("request_feilformet", rid, 400, detalj="kortnavn")
            valgt = valgt.strip()
            if er_reservert(valgt):
                return _feil("request_feilformet", rid, 400, detalj="kortnavn")

        kandidater = kandidater_for(navn, valgt)
        if len(kandidater[0]) < 2:
            # Et navn som bare består av tegn slugen ikke tar imot.
            return _feil("request_feilformet", rid, 400, detalj="navn")

        # STEG 1: firma + admin-medlemskap, atomisk. For et UTLEDET kortnavn
        # er kollisjon ikke en feil — vi prøver neste kandidat. For et VALGT
        # er det ett forsøk: hun skal vite at navnet var opptatt, ikke oppdage
        # en `-2` hun aldri ba om.
        tenant = None
        frist = None
        for kandidat in kandidater:
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
            # ULIKE KODER, fordi de krever ulik handling av henne: et opptatt
            # VALGT kortnavn retter hun selv i feltet; et utledet som ikke fant
            # plass på tjue forsøk er noe helt annet.
            return _feil("kortnavn_opptatt" if valgt else "firma_navn_opptatt",
                         rid, 409)

        # STEG 2-3: nøkkel og policy hører til det NYE firmaet, så
        # konteksten flyttes dit. Feiler noe her, står firmaet igjen uten
        # policy — og det er bedre enn å rulle tilbake medlemskapet hennes,
        # for da hadde hun hverken firma eller vei tilbake.
        sett_kontekst(conn, tenant, f"bruker:{bid}", rid)
        from db import kryptering
        kryptering.hent_eller_opprett_aktiv_dek(conn, tenant)
        _aktiver_bransjemal(conn, tenant, bransje, fullmakter)
        conn.commit()

        sett_kontekst(conn, kontekst, f"bruker:{bid}", rid)
        return _ok_lagret(conn, {"tenant": tenant, "navn": navn,
                                 "prove_utloper": frist.isoformat(),
                                 "bransje": bransje,
                                 "fullmakter": fullmakter}, rid)

    return _med_conn(tjeneste, rid, kjor)


def flett_utvidelse(policy: dict, utvidelse: dict) -> dict:
    """Utvidelsen inn i policyen, slik den styrte veien gjør det for hånd:
    verifikatorer legges til (eller erstattes på navn), handlinger
    erstattes på id eller legges til (`tilbud-generer` ERSTATTER
    handlingen med samme id, RELEASE-M26). Returnerer en NY dict."""
    ut = json.loads(json.dumps(policy))
    # VERIFIKATORENE UTVIDES, DE ERSTATTES IKKE: en utvidelse skrevet mot
    # tjenestebedrift-malen bærer `v_prisbok` med SINE vilkår, og et
    # wholesale-bytte ville tatt tilliten fra håndverksmalens egne
    # (`standard_forbehold_inkludert`). `betrodd_for` er unionen.
    verifikatorer = ut.setdefault("verifikatorer", {})
    for navn, ny in (utvidelse.get("verifikatorer") or {}).items():
        gammel = verifikatorer.get(navn)
        if not isinstance(gammel, dict):
            verifikatorer[navn] = ny
            continue
        flettet = {**gammel, **ny}
        betrodd = list(gammel.get("betrodd_for") or [])
        betrodd += [v for v in (ny.get("betrodd_for") or []) if v not in betrodd]
        flettet["betrodd_for"] = betrodd
        verifikatorer[navn] = flettet
    # Handlinger OG roller flettes på id: `purring-inkassovarsel` bærer
    # rollen `bestiller` som handlingen tillater, og en utvidelse uten
    # rollen sin er en handling ingen får utføre.
    for nokkel in ("handlinger", "roller"):
        liste = ut.setdefault(nokkel, [])
        for ny in utvidelse.get(nokkel) or []:
            for i, h in enumerate(liste):
                if h.get("id") == ny.get("id"):
                    liste[i] = ny
                    break
            else:
                liste.append(ny)
    return ut


def bygg_bootstrap_policy(bransje: str, fullmakter: list[str]) -> dict:
    """Malen som FØLGER MED APPEN pluss de valgte utvidelsene — en ren
    funksjon, så porten kan måle innholdet uten en base."""
    import yaml

    rot = Path(__file__).resolve().parents[3] / "policies"
    policy = yaml.safe_load((rot / BRANSJEMALER[bransje])
                            .read_text(encoding="utf-8"))
    for navn in fullmakter:
        utvidelse = yaml.safe_load((rot / "utvidelser" / FULLMAKTER[navn])
                                   .read_text(encoding="utf-8"))
        policy = flett_utvidelse(policy, utvidelse)
    return policy


def _fullmakter_for_bransje() -> dict[str, list[str]]:
    """Hvilke fullmakter hver bransjemal KAN bære — regnet, ikke skrevet:
    en utvidelse som peker på et vitne malen ikke har (`v_dlp`, `v_fordring`
    finnes bare i tjenestebedrift-malen) validerer ikke, og tilbys da ikke.
    Flaten bærer det samme kartet (`FULLMAKTER_FOR_BRANSJE` i
    firmaregistrering.js), og porten krever likhet."""
    from policy_validator.schema import valider_ny_policy
    ut = {}
    for bransje in BRANSJEMALER:
        ut[bransje] = [navn for navn in sorted(FULLMAKTER)
                       if not valider_ny_policy(
                           bygg_bootstrap_policy(bransje, [navn]))]
    return ut


def _aktiver_bransjemal(conn, tenant: str, bransje: str,
                        fullmakter: list[str] = ()) -> None:
    """Leser malen som FØLGER MED APPEN, fletter inn de valgte fullmaktene,
    validerer som oppsettsveien gjør, og skriver gjennom bootstrap-døra.

    Malen kommer aldri fra forespørselen — kunden velger bare hvilken av
    tre, og hvilke av fire fullmakter. En rute som tok imot policy ville
    vært en vei til å skrive sine egne fullmakter.
    """
    from api.policyregister import innholds_hash
    from policy_validator.schema import valider_ny_policy

    policy = bygg_bootstrap_policy(bransje, list(fullmakter))
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


def avslutt_endepunkt(tjeneste, request):
    """POST /v1/firma/avslutt — kunden sier opp sitt eget abonnement.

    Eier: «kunden kan avbryte prøveperioden og samme etter 30 dager ikke
    fortsette.»

    DØRA GÅR ÉN VEI (201). Gjenåpning er plattformeierens handling, fordi
    `stengt → aktiv` er den eneste veien ut av `stengt` — en angreknapp her
    ville i praksis vært en oppgraderingsknapp. Angrefristen i 190 gjør at
    veien tilbake fortsatt finnes: raden og dataene står til
    `slettefrist_dogn` er ute.

    TENANTEN KOMMER FRA ØKTEN, aldri fra kroppen. Døra binder den i tillegg
    til kontekstene (038s form), så et firmanavn i en request-kropp treffer
    ingenting.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _feil, _med_conn,
                                   _ok_lagret)
    rid = _rid(request)

    def kjor(conn):
        import psycopg

        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "firma:avslutt")
        try:
            with conn.transaction():
                conn.execute("SELECT firma_kunde_avslutt(%s,%s)",
                             (tenant, f"bruker:{bid}"))
        except psycopg.errors.ForeignKeyViolation:
            return _feil("firma_ukjent", rid, 404)
        except psycopg.errors.IntegrityConstraintViolation:
            # Alt stengt, eller utløpt — ingenting å si opp herfra.
            return _feil("overgang_ulovlig", rid, 409)
        except psycopg.errors.InvalidParameterValue:
            return _feil("request_feilformet", rid, 400)
        return _ok_lagret(conn, {"tenant": tenant, "status": "stengt"}, rid)

    return _med_conn(tjeneste, rid, kjor)


#: Regnet én gang ved import (tolv små valideringer av innsjekkede filer).
FULLMAKTER_FOR_BRANSJE: dict[str, list[str]] = _fullmakter_for_bransje()


# ---------------------------------------------------------------------
# GJENVALG (208): fullmaktene kan velges om så lenge policyen er den
# urørte bootstrap-raden. Den dagen firmaet har gått den styrte veien, er
# det den som gjelder.
# ---------------------------------------------------------------------

def _bransje_for_policy_id() -> dict[str, str]:
    """{meta.policy_id: bransje} for malene som følger med appen."""
    import yaml
    rot = Path(__file__).resolve().parents[3] / "policies"
    ut = {}
    for bransje, fil in BRANSJEMALER.items():
        meta = yaml.safe_load((rot / fil).read_text(encoding="utf-8"))["meta"]
        ut[meta["policy_id"]] = bransje
    return ut


def fullmakter_i(policy: dict) -> list[str]:
    """Hvilke fullmakter policyen BÆRER — en fullmakt er på når alle
    utvidelsens handlinger står der. Målt på innholdet, ikke husket."""
    import yaml
    rot = Path(__file__).resolve().parents[3] / "policies" / "utvidelser"
    # PÅ ID ALENE ER IKKE NOK: `tilbud-generer` ERSTATTER en handling
    # malen alt har (samme id, andre vilkår). Fullmakten er på når hver av
    # utvidelsens handlinger står i policyen SLIK utvidelsen skrev den.
    per_id = {h.get("id"): h for h in (policy.get("handlinger") or [])}
    ut = []
    for navn, fil in FULLMAKTER.items():
        utv = yaml.safe_load((rot / fil).read_text(encoding="utf-8"))
        krav = utv.get("handlinger") or []
        if krav and all(per_id.get(h.get("id")) == h for h in krav):
            ut.append(navn)
    return sorted(ut)


def _fullmakttilstand(conn, tenant: str) -> dict:
    """Tilstanden flaten viser: bransje, valgte, lovlige, og om gjenvalget
    fortsatt er åpent (én bootstrap-rad, ingen utkast, ingen attestasjon)."""
    rader = conn.execute(
        "SELECT policy_id, innhold, aktiv, aktiveringskilde FROM policyer"
        " WHERE tenant=%s", (tenant,)).fetchall()
    aktive = [r for r in rader if r[2]]
    if len(rader) != 1 or len(aktive) != 1:
        return {"bransje": None, "valgte": [], "lov": [],
                "kan_velge_om": False}
    policy_id, innhold, _aktiv, kilde = aktive[0]
    if isinstance(innhold, (str, bytes)):
        innhold = json.loads(innhold)
    bransje = BRANSJE_FOR_POLICY_ID.get(policy_id)
    historikk = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM policyutkast WHERE tenant=%s)"
        " OR EXISTS (SELECT 1 FROM aktiveringsattestasjon WHERE tenant=%s)",
        (tenant, tenant)).fetchone()[0]
    return {"bransje": bransje,
            "valgte": fullmakter_i(innhold or {}),
            "lov": FULLMAKTER_FOR_BRANSJE.get(bransje, []),
            "kan_velge_om": bool(bransje) and kilde == "bootstrap"
                            and not historikk}


def fullmakter_endepunkt(tjeneste, request):
    """GET /v1/firma/fullmakter (policy:read) — hva plattformen har
    fullmakt til, og om valget kan gjøres om."""
    from .lesing import _les, kanonisk_json

    def _fn(conn, auth, rid):
        svar = _fullmakttilstand(conn, auth.tenant)
        svar["request_id"] = rid
        return kanonisk_json(svar, 200, {"x-request-id": rid})
    return _les(tjeneste, request, "policy:read", _fn)


def sett_fullmakter_endepunkt(tjeneste, request):
    """POST /v1/firma/fullmakter (policy:activate, idem) — gjenvalget.

    Bygger policyen på nytt av MALEN pluss de valgte utvidelsene (aldri av
    innholdet klienten sender — hun velger navn, ikke policy), validerer
    som registreringen, og skriver gjennom `firma_bootstrap_gjenvalg`, som
    nekter så snart policyen har historikk.
    """
    from .app import _rid
    from .policyadmin_http import (_browserkontekst, _feil, _kropp,
                                   _krev_idem, _med_conn, _ok_lagret)
    rid = _rid(request)

    def kjor(conn):
        import psycopg
        from api.policyregister import innholds_hash
        from policy_validator.schema import valider_ny_policy
        tenant, bid = _browserkontekst(tjeneste, request, conn, rid,
                                       "policy:activate")
        _krev_idem(request, rid)
        k = _kropp(request)
        valgte = k.get("fullmakter")
        if not isinstance(valgte, list) or not all(
                isinstance(x, str) and x in FULLMAKTER for x in valgte):
            return _feil("request_feilformet", rid, 400, detalj="fullmakter")
        valgte = sorted(set(valgte))
        tilstand = _fullmakttilstand(conn, tenant)
        if not tilstand["kan_velge_om"]:
            return _feil("policy_har_historikk", rid, 409)
        utenfor = [n for n in valgte if n not in tilstand["lov"]]
        if utenfor:
            return _feil("request_feilformet", rid, 400,
                         detalj=f"fullmakter: {', '.join(utenfor)} passer"
                                f" ikke bransjen {tilstand['bransje']}")
        policy = bygg_bootstrap_policy(tilstand["bransje"], valgte)
        feil = valider_ny_policy(policy)
        if feil:
            raise RuntimeError(f"gjenvalg ga ugyldig policy: {feil}")
        try:
            conn.execute("SELECT firma_bootstrap_gjenvalg(%s,%s,%s)",
                         (tenant, innholds_hash(policy),
                          json.dumps(policy, ensure_ascii=False)))
        except psycopg.errors.IntegrityConstraintViolation:
            return _feil("policy_har_historikk", rid, 409)
        conn.commit()
        return _ok_lagret(conn, {"fullmakter": valgte}, rid)
    return _med_conn(tjeneste, rid, kjor)


BRANSJE_FOR_POLICY_ID: dict[str, str] = _bransje_for_policy_id()
