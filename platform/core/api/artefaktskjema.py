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
    """
    try:
        jsonschema.Draft202012Validator.check_schema(skjema)
    except jsonschema.exceptions.SchemaError as e:
        return [f"<skjema>: ugyldig JSON Schema — {e.message[:160]}"]
    return []


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
    # stedet for en avvisning.
    feil = skjemafeil(skjema)
    if feil:
        return feil
    validator = jsonschema.Draft202012Validator(
        skjema, format_checker=FORMATSJEKKER)
    for e in sorted(validator.iter_errors(innhold),
                    key=lambda e: list(e.absolute_path)):
        feil.append(f"{_sti(e)}: {_bruddkode(e)}")
        if len(feil) >= 20:
            feil.append("… (avkortet)")
            break
    return feil
