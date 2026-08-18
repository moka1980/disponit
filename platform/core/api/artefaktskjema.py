"""Skjemavalidering av artefaktinnhold (PR-014c §8) — CP5-hullet fra 014b.

Artefakttypens `skjema_hash` har til nå vært en påstand ingen kunne slå
opp: innhold ble kryptert og promotert uten at noen validerte det mot
noe. 036 ga skjemaet et innholdsadressert lager; her er oppslaget og
valideringen — brukt på NØYAKTIG TO punkter, begge påkrevde:

  1. Ved OPPLASTING (`_artefakt_upload`): valider klarteksten før
     kryptering. Avvis — ingen staged rad, ingen kapabilitet kastet bort
     på innhold som aldri kan promoteres.
  2. Ved PROMOTERING (kvittering-ingest, samme transaksjon som
     statusovergangen): dekrypter og valider PÅ NYTT mot samme hash.
     Skjemaet er immutabelt, så dette er ikke forsvar mot endring — det
     er forsvar mot at en FREMTIDIG opplastingsvei glemmer punkt 1.

Ingen skjemarad for hashen → avvist. Ingen stille promotering av innhold
ingen kan validere.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime

import jsonschema
import psycopg

# --------------------------------------------------------------------------
# `format` MÅ SJEKKES (Codex P2)
# --------------------------------------------------------------------------
# `Draft202012Validator` behandler `format` som en ANNOTASJON med mindre den
# får en format-checker. Rapportskjemaets `kjort_ts: {format: date-time}`
# var derfor ren dokumentasjon: et artefakt med `kjort_ts: "i går"` passerte
# BEGGE de annonserte valideringspunktene og ble promotert.
#
# Å bare sende `Draft202012Validator.FORMAT_CHECKER` er IKKE nok, og det er
# den lumske delen: jsonschema sjekker `date-time` bare når den valgfrie
# `rfc3339-validator` er installert, og ellers hopper den STILLE over
# formatet. Da hadde fiksen sett riktig ut i koden og fortsatt sluppet
# gjennom nøyaktig den verdien funnet handler om. Derfor registreres
# `date-time` her, uten ny avhengighet — grammatikken fra RFC 3339 §5.6,
# og deretter `fromisoformat` for det grammatikken ikke fanger (31. februar).
#
# `\Z`, ikke `$`: Pythons `$` matcher OGSÅ rett før en avsluttende
# linjeskift (samme lekkasje som policyskjemaets ECMA-ankre dokumenterer),
# og «gyldig tidsstempel med hale» er ikke et gyldig tidsstempel.
_RFC3339 = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}"
                      r"(\.\d+)?([Zz]|[+-]\d{2}:\d{2})\Z")

FORMATSJEKKER = copy.deepcopy(jsonschema.Draft202012Validator.FORMAT_CHECKER)


def _oppslagsfeilklasser() -> tuple:
    """Unntakene `iter_errors` kaster når en `$ref` ikke lar seg slå opp.

    Klassen flyttet i jsonschema 4.18: `RefResolutionError` ble til
    `referencing.exceptions.Unresolvable`, pakket som
    `jsonschema.exceptions._WrappedReferencingError`. Vi slår opp begge
    ved NAVN i stedet for å importere én av dem, fordi en `ImportError`
    her ville tatt hele modulen ned ved oppstart — og et pinnet
    versjonsvalg i en `except`-linje er nettopp den slags stille
    avhengighet som ryker ved neste `pip install`.

    Blir tuppelen tom, faller `valider` tilbake til dagens oppførsel
    (unntaket slipper ut). Det er det ærlige utfallet: en fangst av
    `Exception` ville skjult ekte feil i validatoren som avviste
    artefakter.
    """
    klasser = [k for k in
               (getattr(jsonschema.exceptions, navn, None) for navn in
                ("RefResolutionError", "_WrappedReferencingError"))
               if isinstance(k, type) and issubclass(k, Exception)]
    try:
        from referencing.exceptions import Unresolvable
    except ImportError:
        pass
    else:
        klasser.append(Unresolvable)
    return tuple(klasser)


_OPPSLAGSFEIL = _oppslagsfeilklasser()


@FORMATSJEKKER.checks("date-time")
def _er_rfc3339(verdi) -> bool:
    """Typen er skjemaets jobb — formatet uttaler seg kun om strenger."""
    if not isinstance(verdi, str):
        return True
    if not _RFC3339.match(verdi):
        return False
    try:
        # RFC 3339 tillater små `t`/`z`; `fromisoformat` gjør det ikke.
        datetime.fromisoformat(verdi.replace("t", "T").replace("z", "Z"))
    except ValueError:
        return False
    return True


def hent_skjema(conn: psycopg.Connection, artefakttype: str) -> dict | None:
    """Skjemaet artefakttypen er bundet til, via registerets `skjema_hash`.
    -> None når typen mangler i registeret ELLER hashen mangler skjemarad —
    begge er samme svar for kalleren: innholdet kan ikke valideres, og da
    skal det heller ikke tas imot."""
    rad = conn.execute(
        "SELECT s.skjema FROM artefakttype_register r"
        "  JOIN artefaktskjema s ON s.skjema_hash = r.skjema_hash"
        " WHERE r.artefakttype = %s", (artefakttype,)).fetchone()
    return rad[0] if rad else None


# --------------------------------------------------------------------------
# BARE NØKKELORD METASKJEMAET SELV VALIDERER (Codex P2)
# --------------------------------------------------------------------------
# Listene under bestemmer hva `_delskjemaer` regner som en SKJEMAPOSISJON, og
# den avgjørelsen brukes til å godkjenne en `$ref`-destinasjon udødelig. Da
# er det ett krav som må holde for hvert eneste nøkkelord her: metaskjemaet
# for Draft 2020-12 må selv validere verdien som et skjema. Ellers godkjenner
# vandringen en destinasjon `check_schema` aldri så på.
#
# `additionalItems` sto her og brøt nettopp det. Nøkkelordet er FJERNET i
# 2020-12 — det er ikke blant metaskjemaets `properties`, i motsetning til
# `definitions` og `dependencies`, som er beholdt som deprecated MED
# metavalidering. Verdien er derfor en ren annotasjon, umetavalidert, og
# `{"additionalItems": {"type": "strng"}, "$ref": "#/additionalItems"}` ga
# ingen `skjemafeil()` mens `iter_errors` kastet `UnknownType` — eller
# `AttributeError` når verdien var en streng. Ingen av dem er i
# `_OPPSLAGSFEIL`, så den permanente 500-eren var tilbake i en udødelig rad.
#
# `additionalItems` er samtidig det ENESTE nøkkelordet i listene som manglet
# metavalidering; de øvrige er kontrollert mot metaskjemaets egne
# `properties`. Nye nøkkelord skal måles på samme måte før de føres opp.

#: Nøkkelord hvis verdi ER et delskjema.
_SKJEMA_NOKKEL = frozenset({
    "additionalProperties", "items", "contains", "not",
    "if", "then", "else", "propertyNames", "unevaluatedItems",
    "unevaluatedProperties", "contentSchema"})
#: Nøkkelord hvis verdi er en LISTE av delskjemaer.
_SKJEMA_LISTE = frozenset({"allOf", "anyOf", "oneOf", "prefixItems"})
#: Nøkkelord hvis verdi er et KART fra navn til delskjema. Navnene er
#: nettopp navn — de gås aldri inn i.
_SKJEMA_KART = frozenset({
    "properties", "patternProperties", "$defs", "definitions",
    "dependentSchemas"})


def _delskjemaer(skjema):
    """Hver SKJEMAPOSISJON i dokumentet, dokumentet selv inkludert, som
    `(sti, delskjema)` — `sti` er JSON-pekerleddene ned dit, allerede
    avescapet (altså `("$defs", "a/b")`, ikke `("$defs", "a~1b")`).

    Vandringen er nøkkelordstyrt, ikke en blind rekursjon over all JSON, og
    forskjellen er ikke akademisk. `{"const": {"$ref": "..."}}` er DATA:
    `$ref`-en der er en verdi innholdet skal være lik, ikke en referanse
    validatoren noen gang slår opp. En blind vandring ville avvist det
    skjemaet ved registrering — og siden både skjemaraden og typebindingen
    er udødelige, er en falsk avvisning her like endelig som en falsk
    godkjenning.

    Ukjente nøkkelord gås heller ikke inn i: for Draft 2020-12 er de
    ANNOTASJONER, og en `$ref` inne i en annotasjon evalueres aldri.

    `true`/`false` er også skjemaer — de bare inneholder ingenting. De
    yields derfor på lik linje (kallerne siler selv på `dict`), fordi
    STIEN dit er en gyldig referansedestinasjon: `{"$defs": {"a": true},
    "$ref": "#/$defs/a"}` er et brukbart skjema.

    Vandringen kjøres ALLTID etter `check_schema` (se `skjemafeil`), og det
    er den rekkefølgen som gjør posisjonene brukbare som `$ref`-mål: hver
    posisjon her er allerede metavalidert. `_SKJEMA_NOKKEL` tas derfor bare
    som ett delskjema — en LISTE der (draft-07-formen av `items`) er ikke
    et gyldig 2020-12-skjema og har alt blitt avvist av metasjekken.
    """
    ko = [((), skjema)]
    while ko:
        sti, s = ko.pop()
        yield sti, s
        if not isinstance(s, dict):
            continue
        for nokkel, verdi in s.items():
            if nokkel in _SKJEMA_NOKKEL:
                ko.append((sti + (nokkel,), verdi))
            elif nokkel in _SKJEMA_LISTE and isinstance(verdi, list):
                ko.extend((sti + (nokkel, str(i)), v)
                          for i, v in enumerate(verdi))
            elif nokkel in _SKJEMA_KART and isinstance(verdi, dict):
                ko.extend((sti + (nokkel, navn), v)
                          for navn, v in verdi.items())


def _pekerledd(fragment: str) -> tuple[str, ...]:
    """Leddene i en JSON-peker (RFC 6901), avescapet.

    Fragmentet prosentdekodes FØR `~1`/`~0`, i den rekkefølgen RFC 6901 §6
    krever: `~` er `%7E` på veien inn, og gjøres de motsatt vei blir et
    ærlig `~0` lest som et bokstavelig `~`.
    """
    from urllib.parse import unquote
    return tuple(unquote(raa).replace("~1", "/").replace("~0", "~")
                 for raa in fragment.split("/")[1:])


def _referansefeil(skjema) -> list[str]:
    """-> feilliste for `$ref`/`$dynamicRef` som ikke lar seg slå opp.

    Codex P2: METASJEKKEN SIER INGENTING OM REFERANSER. Metaskjemaet
    krever bare at `$ref` er en URI-referanse-STRENG, så
    `{"$ref": "#/$defs/missing"}` passerer `check_schema` glatt. Først
    `iter_errors` — altså midt i en opplasting — slår oppslaget feil, og
    da med et unntak, ikke en valideringsfeil: det går rett forbi
    `valider`, ut av `_artefakt_upload` og blir en 500-er. Fordi både
    skjemaraden og typebindingen er immutable, ville HVER opplasting av
    den artefakttypen vært en 500-er for alltid.

    Det er nøyaktig samme hull som `{"type": "strng"}` — et skjema som
    metavalideres, men ikke lar seg BRUKE — og svaret er det samme:
    fang det på den delte registreringsveien, før den udødelige raden
    finnes, og gjenta det i `valider` for rader som kom inn utenom.

    EN REFERANSE UT AV DOKUMENTET AVVISES (ikke bare de som ryker).
    `{"$ref": "https://et-sted.example/s.json"}` er ikke bare et
    oppslag som kan feile; den er to ting til, og begge er verre:

      * Skjemaet er INNHOLDSADRESSERT. Hashen dekker bytene i raden, og
        de bytene sier ingenting om hva som ligger på den andre enden.
        Samme udødelige hash kunne da validert to forskjellige ting i
        dag og i morgen, og hele poenget med å adressere skjemaet på
        innholdet er at det ikke skal kunne skje.
      * Oppslaget er en NETTVERKSFORESPØRSEL fra serveren, utløst av
        innsendt innhold, mot en URL en administrator skrev en gang.
        Validering av et artefakt skal ikke nå ut av maskinen.

    Et registrert artefaktskjema er derfor SELVSTENDIG: alt det viser
    til, viser det til inni seg selv.

    NESTET `$id` AVVISES av samme ærlighetsgrunn som blanktegnvakten i
    036 er dokumentert som nødvendig-men-ikke-tilstrekkelig: en `$id`
    under roten flytter basen lokale pekere løses mot, og da måler
    sjekken under noe annet enn validatoren gjør. En sjekk som ikke kan
    følge omadresseringen skal si fra, ikke gjette. Rot-`$id` flytter
    ingenting og står.

    PEKEREN MÅ TREFFE EN SKJEMAPOSISJON, IKKE BARE EN NODE (Codex P2).
    At `#/x` lar seg SLÅ OPP sier ingenting om at det som ligger der er
    et skjema. `{"x": "ikke et skjema", "$ref": "#/x"}` metavalideres —
    `x` er et ukjent nøkkelord, altså en annotasjon `check_schema` ikke
    ser på — og en peker-eksisterer-sjekk slipper det gjennom. Så
    behandler `jsonschema` strengen som et skjema og kaster
    `AttributeError`, som ikke er blant `_OPPSLAGSFEIL`: den permanente
    500-eren denne sjekken finnes for å hindre, tilbake i samme rad.
    `{"x": {"type": "strng"}, "$ref": "#/x"}` er samme hull med
    `UnknownType` i stedet.

    Derfor måles referansen mot de posisjonene vandringen over faktisk
    besøker. Det er en STRENGERE regel enn «noden finnes», og den er den
    riktige av to grunner: bare på en skjemaposisjon har `check_schema`
    metavalidert det som ligger der, og bare der ville validatoren selv
    ha lest det som et skjema. En destinasjon under et ukjent nøkkelord
    er per definisjon umetavalidert — den kan ikke godkjennes udødelig.
    """
    feil = []
    ankre = set()
    nestet_id = False
    delskjemaer = list(_delskjemaer(skjema))
    posisjoner = {sti for sti, _ in delskjemaer}
    for sti, s in delskjemaer:
        if not isinstance(s, dict):
            continue
        for nokkel in ("$anchor", "$dynamicAnchor"):
            if isinstance(s.get(nokkel), str):
                ankre.add(s[nokkel])
        if sti and isinstance(s.get("$id"), str):
            nestet_id = True
    if nestet_id:
        return ["<skjema>: `$id` under roten — lokale referanser kan ikke"
                " kontrolleres mot en flyttet base"]
    for _, s in delskjemaer:
        if not isinstance(s, dict):
            continue
        for nokkel in ("$ref", "$dynamicRef"):
            ref = s.get(nokkel)
            if not isinstance(ref, str):
                continue
            if not ref.startswith("#"):
                feil.append(f"<skjema>: `{nokkel}` peker ut av dokumentet"
                            f" — {ref[:80]}")
            elif ref == "#":
                continue
            elif ref.startswith("#/"):
                if _pekerledd(ref[1:]) not in posisjoner:
                    feil.append(f"<skjema>: `{nokkel}` treffer ingen"
                                f" skjemaposisjon — {ref[:80]}")
            elif ref[1:] not in ankre:
                feil.append(f"<skjema>: `{nokkel}` treffer ingen `$anchor`"
                            f" — {ref[:80]}")
    return feil


def skjemafeil(skjema) -> list[str]:
    """-> feilliste (tom = gyldig Draft 2020-12-skjema). META-sjekken.

    Codex P2: `registrer_artefaktskjema` kontrollerer bare at JSON-en er et
    OBJEKT — plpgsql kan ikke kjøre en JSON Schema-metavalidering, og skal
    ikke late som. Uten denne sjekken kunne en administrator registrere
    `{"type": "strng"}`, binde en artefakttype til hashen, og deretter få
    HVER opplastning og promotering til å dø på et ufanget `UnknownType`
    fra validatoren — og fordi både skjemaraden og typebindingen er
    immutable, kunne typen aldri repareres.

    Sjekken hører derfor til på begge sider av den udødelige raden:
    registreringsveien (deploy-skriptet) kjører den FØR innsetting, og
    `valider` under kjører den før innhold måles, slik at et skjema som
    likevel skulle ha kommet inn gir en ærlig avvisning i stedet for en
    500-er.

    REFERANSENE ER DEL AV DEN SAMME SJEKKEN (Codex P2). `check_schema`
    ser bare at `$ref` er en streng; om den treffer noe, vises først når
    validatoren følger den midt i en opplasting. Se `_referansefeil` —
    den står her, i den delte sjekken, og ikke hos én kaller, av samme
    grunn som metasjekken selv gjør det.
    """
    try:
        jsonschema.Draft202012Validator.check_schema(skjema)
    except jsonschema.exceptions.SchemaError as e:
        return [f"<skjema>: ugyldig JSON Schema — {e.message[:160]}"]
    return _referansefeil(skjema)


class Skjemaugyldig(ValueError):
    """Skjemaet er ikke et gyldig Draft 2020-12-skjema. Fail-closed: da
    registreres det ikke, og ingen artefakttype kan bindes til det."""


def registrer(conn: psycopg.Connection, skjema: dict, aktor: str) -> str:
    """Registrer skjemaet gjennom den herdede funksjonen. -> skjema_hash.

    DEN DELTE REGISTRERINGSVEIEN (Codex P2). Metasjekken lå først bare i
    WCAG-deploy-skriptet, altså hos ÉN kaller. Neste deploy-verktøy ville
    ikke arvet den, og gapet er ikke reparerbart i ettertid: både
    skjemaraden og typebindingen er immutable, så et ødelagt skjema gjør
    artefakttypen permanent ubrukelig.

    Alt som skal registrere et artefaktskjema fra Python går derfor
    HERFRA. Funksjonen eier tre ting kalleren ellers måtte gjenta likt
    hver gang, og som er feil om de gjøres ulikt:

      1. metasjekken (`skjemafeil`) FØR noe skrives,
      2. kanoniseringen (JCS — det er de bytene hashen er over), og
      3. hashen, regnet ut av nøyaktig de bytene som sendes.

    SQL-siden har sin egen, uavhengige vakt (`_artefaktskjema_typefeil` i
    migrasjon 036), fordi begge admin-rollene fortsatt har EXECUTE og en
    direkte SQL-kaller aldri ser denne funksjonen.
    """
    import hashlib

    from policy_validator import jcs
    feil = skjemafeil(skjema)
    if feil:
        raise Skjemaugyldig("; ".join(feil))
    kanon = jcs.kanoniske_bytes(skjema)
    h = hashlib.sha256(kanon).hexdigest()
    conn.execute("SELECT registrer_artefaktskjema(%s, %s, %s)",
                 (kanon.decode("utf-8"), h, aktor))
    return h


def _bruddkode(e) -> str:
    """Bruddet beskrevet av SKJEMAET, aldri av innholdet (Codex P1).

    `e.message` er bygget rundt den feilende VERDIEN: bryter et felt
    `type`, `enum`, `pattern` eller `format`, står verdien ordrett i
    teksten (`'alice@example.com' is not of type 'integer'`). Den teksten
    gikk rett i `Sikkerhetslogg`, som har «ALDRI payload» som kontrakt og
    skriver til stderr — altså rapportklartekst, med persondata og alt,
    ut av det krypterte sporet og inn i driftsloggene.

    Det som blir igjen her, kommer utelukkende fra skjemaet: hvilket
    nøkkelord som brøt, og hva nøkkelordet KREVDE. Skjemaet er en
    registrert, innholdsadressert plattformartefakt — ikke kundedata — så
    den siden er trygg å logge, og den er også den eneste som forteller en
    driftsperson noe brukbart: hvor det brøt (`_sti`) og hva som var
    kravet. Det er nok til å feilsøke en avvist opplasting uten å ha sett
    et eneste tegn av rapporten.

    `additionalProperties: false` er verdt et eget ord: DER er de
    fornærmende navnene innholdets egne nøkler, og de står bare i
    `e.message`. `validator_value` er `False`, så de faller ut av seg selv.
    """
    nokkel = str(getattr(e, "validator", "?"))
    try:
        krav = json.dumps(getattr(e, "validator_value", None),
                          ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        krav = "<ikke serialiserbart>"
    return f"{nokkel}={krav[:80]}"


def _sti(e) -> str:
    """Stien til bruddet, med bare skjemakjente ledd (Codex P1).

    `absolute_path` er stien i INNHOLDET, og de fleste leddene er trygge:
    et listeindeks er et tall, og et feltnavn skjemaet selv nevner er
    skjemadata. Men tillater skjemaet frie nøkler (`additionalProperties`
    som et delskjema), er det brytende leddet innholdets EGEN nøkkel — som
    like gjerne kan være en e-postadresse som et feltnavn. Å logge den
    ville vært samme lekkasje som `e.message`, bare gjennom stien.

    Derfor: tall beholdes, og navn beholdes bare når `absolute_schema_path`
    nevner dem — altså når det er skjemaet, ikke innsenderen, som fant på
    navnet. Resten blir `<felt>`.
    """
    kjent = {str(s) for s in e.absolute_schema_path}
    deler = [str(p) if isinstance(p, int) or str(p) in kjent else "<felt>"
             for p in e.absolute_path]
    return "/".join(deler)[:120] or "<rot>"


def valider(skjema: dict, innhold: dict) -> list[str]:
    """-> feilliste (tom = gyldig). Draft 2020-12, samme validatorfamilie
    som policyskjemaet, MED format-checkeren over. Feilene er tekst for
    LOGGEN — de sendes aldri ordrett til klienten (innholdet kan bære
    persondata), og de INNEHOLDER heller ikke innholdet selv: se
    `_bruddkode`."""
    # Et ødelagt skjema måler ingenting: uten denne linjen kaster
    # `iter_errors` under (UnknownType) og opplastningen blir en 500-er i
    # stedet for en avvisning. Den fanger nå også referanser som ikke lar
    # seg slå opp (Codex P2) — se `_referansefeil`.
    feil = skjemafeil(skjema)
    if feil:
        return feil
    validator = jsonschema.Draft202012Validator(
        skjema, format_checker=FORMATSJEKKER)
    # OPPSLAGSFEIL ER EN AVVISNING, IKKE EN 500-ER (Codex P2). `skjemafeil`
    # over skal ha tatt dem alle, og i drift gjør den det. Fangsten står
    # likevel, av samme grunn som SQL-siden har sin egen vakt: raden kan
    # være eldre enn sjekken, eller satt inn med direkte SQL av en rolle
    # som fortsatt har EXECUTE — og fordi raden er udødelig, ville ETT
    # slikt skjema gjort artefakttypen til en permanent 500-er.
    # `iter_errors` er lat, så oppslaget skjer først når innholdet når
    # frem til grenen; en tom feilliste er derfor ikke noe bevis på at
    # referansen finnes.
    try:
        avvik = sorted(validator.iter_errors(innhold),
                       key=lambda e: list(e.absolute_path))
    except _OPPSLAGSFEIL as e:
        return [f"<skjema>: referanse lar seg ikke slå opp — {str(e)[:160]}"]
    for e in avvik:
        feil.append(f"{_sti(e)}: {_bruddkode(e)}")
        if len(feil) >= 20:
            feil.append("… (avkortet)")
            break
    return feil
