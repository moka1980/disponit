"""PR-013 CP5c — porten for policyaktivering (fire-øyne på fullmaktsendring).

Å aktivere en policy er å endre hva agenten HAR LOV TIL. Derfor samme
arbeidsdeling som PR-012s unntaksbehandling, men strengere: en aktivering er en
menneskelig godkjent, MAC-signert overgang som til slutt utføres av den herdede
`aktiver_policy`-funksjonen (migrasjon 013, SECURITY DEFINER, eid av
`disponit_policy_eier`). Denne modulen eier:

  1. **Runde-åpning** (`opprett_aktiveringsrunde`): under `policy_hode`-låsen
     utledes diffen (mot aktiv versjon eller `DENY_ALL_V1`) og klassifiseringen
     (UTVIDER/INNSNEVRER/NØYTRAL), og ALT bindes frosset i runden — diff_hash,
     klassifisering_hash, klassifikator-/skjema-/motorsemantikk-versjoner,
     deny-all, og påkrevd antall godkjennere (V6).
  2. **Attestering** (`attester_aktivering`): en godkjenner attesterer DIFFEN
     (diff_hash), aldri versjonsnummeret (v5 §2). Server bygger + MAC-signer
     konvolutten `disponit_policy_activation_v1` fra LÅSTE data, `er_forfatter`
     er server-utledet (DB-triggeren vokter det, V7), og fire-øyne håndheves av
     antallet + at MINST én godkjenner ikke er forfatteren.
  3. **Aktivering**: når terskelen er nådd, REKALKULERES diff/klasse UNDER
     LÅSEN. Har den aktive policyen flyttet seg siden runden åpnet (eller motor-
     semantikken endret seg), avvises aktiveringen — REBASERING kreves. Ellers
     kalles `aktiver_policy` (deaktiver forrige + sett inn ny i SAMME tx).

Kalleren eier transaksjonen (som `behandle_unntakshandling`). Fullmaktsreglene
håndheves av DB-en (triggere + herdet funksjon), ikke av at koden husker dem.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
from datetime import timedelta

import psycopg

from db.pg import sett_kontekst
from policy_validator import klassifikator, policydiff, semantikk
from policy_validator import schema as _schema

from . import policyregister as _pr
from .autorisasjon import scopes_for_roller
from .mac_register import kanonisk_konvolutt

#: Skjemaversjonen som bindes inn i aktiveringsrunden. Leses fra skjemafilens
#: NAVN (utenfor `SEMANTIKK_MANIFEST`), ikke fra en konstant i `schema.py` —
#: `schema.py` er en manifestfil, og en versjonskonstant der ville tvunget en
#: re-pinning av `MOTOR_SEMANTIKKVERSJON` for en ren metadata-endring. Bytter
#: man til v0.3 uten å oppdatere filnavnet, feiler lasten uansett (fil mangler).
_m = re.search(r"v(\d+\.\d+)", _schema._SKJEMA_STI.name)
POLICYSKJEMA_VERSJON = _m.group(1) if _m else "0"

#: En åpen runde lever innen én arbeidsøkt (fire-øyne skal ikke stå åpent i
#: dager). Utløp lukker runden; attestasjoner slettes aldri.
RUNDE_TTL = timedelta(hours=24)

#: Konvoluttnavnet namespacer aktiveringskonvolutten bort fra
#: `disponit_human_approval_v*`: navnet inngår i de MAC-signerte bytene, så en
#: godkjenningskonvolutt fra unntaksveien kan aldri gjenspilles som aktivering.
KONVOLUTT_TYPE = "disponit_policy_activation_v1"
KONVOLUTTVERSJON = 1

#: Aktiverte policyer settes i produksjonsstatus (den blir den aktive raden).
_AKTIV_STATUS = "produksjon"

_AKTIVER_SCOPE = "policy:activate"

#: Feltene konvolutten binder til de LÅSTE dataene (defense-in-depth: server
#: signerte akkurat over disse selv).
_BINDINGSFELT = ("konvolutt_type", "tenant", "utkast_id", "policy_id", "runde",
                 "diff_hash", "klassifisering_hash", "risikoklasse",
                 "base_policy_hash", "bruker_id", "er_forfatter")


class Aktiveringsfeil(Exception):
    """En avvist policyadmin-handling med en semantisk kode. CP6-endepunktet
    oversetter koden til HTTP; porten holdes fri for HTTP-detaljer."""

    def __init__(self, kode: str, detalj: str = "") -> None:
        super().__init__(kode if not detalj else f"{kode}: {detalj}")
        self.kode = kode
        self.detalj = detalj


# --------------------------------------------------------------------------
# Felles: base-policy (aktiv versjon eller DENY_ALL_V1) + klassifisering.
# --------------------------------------------------------------------------

def _base(conn: psycopg.Connection, tenant: str, policy_id: str,
          aktiv_versjon: str | None) -> tuple[dict, str]:
    """(innhold, innholds_hash) for base-policyen en endring måles mot.

    Ingen aktiv versjon (NULL-peker, evt. helt ny policy) → `DENY_ALL_V1`:
    første policy klassifiseres som en UTVIDELSE fra «ingenting tillatt», ikke
    som en nøytral førstegangsregistrering (V9)."""
    if aktiv_versjon is None:
        return semantikk.DENY_ALL_V1, semantikk.DENY_ALL_HASH
    rad = conn.execute(
        "SELECT innhold, innholds_hash FROM policyer"
        " WHERE tenant=%s AND policy_id=%s AND versjon=%s",
        (tenant, policy_id, aktiv_versjon)).fetchone()
    if rad is None:
        # Pekeren viser på en versjon som ikke finnes — datamodellen skal
        # gjøre dette umulig (kompositt-FK), men porten stoler ikke blindt.
        raise Aktiveringsfeil("base_mangler", f"versjon={aktiv_versjon}")
    innhold, lagret = rad
    if not isinstance(innhold, dict):
        raise Aktiveringsfeil("base_korrupt")
    return innhold, lagret


def _vurder(base_innhold: dict, base_hash: str, ny_innhold: dict) -> dict:
    """Diff + klassifisering + påkrevd antall godkjennere. Ren funksjon av
    inndata (ingen DB) → SAMME resultat ved runde-åpning og ved rekalk under
    låsen; avvik betyr at basen (eller motorsemantikken) flyttet seg."""
    _, dh = policydiff.diff_og_hash(base_innhold, ny_innhold)
    kl = klassifikator.klassifiser(base_innhold, ny_innhold)
    risikoklasse = kl["klasse"]
    # V6: UTVIDER krever to godkjennere (forfatter kan være én, aldri begge —
    # sikret av «minst én ikke-forfatter» + UNIQUE(bruker) per runde).
    # INNSNEVRER/NØYTRAL: én godkjenner ≠ forfatter (samme ikke-forfatter-krav).
    pakrevd = 2 if risikoklasse == klassifikator.UTVIDER else 1
    return {
        "diff": policydiff.strukturert_diff(base_innhold, ny_innhold),
        "diff_hash": dh,
        "risikoklasse": risikoklasse,
        "klassifisering_endringer": kl["endringer"],   # risikoklasse PER endring
        "klassifisering_hash": kl["klassifisering_hash"],
        "klassifikatorversjon": kl["klassifikatorversjon"],
        "base_policy_hash": base_hash,
        "pakrevd_antall_godkjennere": pakrevd,
    }


def _base_med_versjon(conn, tenant, policy_id) -> tuple[dict, str, str | None]:
    """(innhold, hash, aktiv_versjon) for gjeldende aktive base (deny-all om
    ingen). Delt av utkast-detalj og runde-åpning."""
    aktiv = _hode_aktiv_versjon(conn, tenant, policy_id)
    innhold, h = _base(conn, tenant, policy_id, aktiv)
    return innhold, h, aktiv


# --------------------------------------------------------------------------
# Utkast-livssyklus (CP6): opprett → rediger → valider. Et utkast er IKKE en
# policy; det er den ENESTE muterbare tilstanden. Validering fryser
# innholds_hash og låser innholdet (kolonnelåsen i migrasjon 012).
# --------------------------------------------------------------------------

def opprett_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                   request_id: str, policy_id: str, innhold: dict,
                   idempotency_key: str, input_hash: str,
                   rollback_av_versjon: str | None = None) -> dict:
    """Opprett et nytt utkast (status `utkast`). Fanger gjeldende aktive versjon
    + hash som `basert_pa_*` for konfliktdeteksjon (§4). Idempotent (P1 R3):
    en replay returnerer NØYAKTIG samme utkast_id. Kalleren eier tx."""
    sett_kontekst(conn, tenant, aktor, request_id)
    if not isinstance(innhold, dict):
        conn.rollback()
        raise Aktiveringsfeil("utkast_feilformet")
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        conn.rollback()
        return lagret
    if tilstand == "konflikt":
        conn.rollback()
        raise Aktiveringsfeil("idempotenskonflikt")
    _, base_hash, aktiv = _base_med_versjon(conn, tenant, policy_id)
    utkast_id = "u-" + secrets.token_hex(8)
    conn.execute(
        "INSERT INTO policyutkast (tenant, utkast_id, policy_id,"
        " basert_pa_versjon, basert_pa_hash, rollback_av_versjon, innhold,"
        " opprettet_av) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)",
        (tenant, utkast_id, policy_id, aktiv, base_hash, rollback_av_versjon,
         json.dumps(innhold), aktor))
    return _fullfor(conn, tenant, idempotency_key, {
        "utkast_id": utkast_id, "policy_id": policy_id,
        "utkastversjon": 1, "status": "utkast", "base_versjon": aktiv})


def rediger_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                   request_id: str, utkast_id: str, forventet_utkastversjon,
                   innhold: dict, idempotency_key: str, input_hash: str) -> dict:
    """Rediger innholdet i et `utkast`-utkast (optimistisk lås på
    `utkastversjon`). Et validert utkast er frosset (innholds_hash låst) — da
    lages et nytt utkast i stedet. Idempotent (P1 R3). Kalleren eier tx."""
    sett_kontekst(conn, tenant, aktor, request_id)
    if not isinstance(innhold, dict):
        conn.rollback()
        raise Aktiveringsfeil("utkast_feilformet")
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        conn.rollback()
        return lagret
    if tilstand == "konflikt":
        conn.rollback()
        raise Aktiveringsfeil("idempotenskonflikt")
    rad = conn.execute(
        "SELECT status, utkastversjon FROM policyutkast WHERE tenant=%s AND"
        " utkast_id=%s FOR UPDATE", (tenant, utkast_id)).fetchone()
    if rad is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    status, ver = rad
    if status != "utkast":
        conn.rollback()
        raise Aktiveringsfeil("utkast_ulovlig_tilstand", f"status={status}")
    if not isinstance(forventet_utkastversjon, int) \
            or forventet_utkastversjon != ver:
        conn.rollback()
        raise Aktiveringsfeil("utkastversjon_utdatert", f"er={ver}")
    ny = ver + 1
    conn.execute(
        "UPDATE policyutkast SET innhold=%s::jsonb, utkastversjon=%s"
        " WHERE tenant=%s AND utkast_id=%s",
        (json.dumps(innhold), ny, tenant, utkast_id))
    return _fullfor(conn, tenant, idempotency_key, {
        "utkast_id": utkast_id, "utkastversjon": ny, "status": "utkast"})


def valider_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                   request_id: str, utkast_id: str, forventet_utkastversjon,
                   idempotency_key: str, input_hash: str) -> dict:
    """Skjemavalider utkastet; ved suksess fryses `innholds_hash` og status går
    `utkast → validert`. Ugyldig → utfall `ugyldig` med feillisten.

    Idempotensnøkkelen er BUNDET til utkastversjonen (Codex R3): klienten sender
    versjonen den validerer, den inngår i `input_hash`, og den LÅSTE radens
    faktiske versjon må stemme (ellers `utkastversjon_utdatert`). Både gyldig OG
    ugyldig resultat CACHES (én validering av én versjon = ett svar); et forsøk
    med samme nøkkel på et endret utkast får `idempotenskonflikt`, ikke et stille
    replay av et stale svar. Kalleren eier tx."""
    sett_kontekst(conn, tenant, aktor, request_id)
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        conn.rollback()
        return lagret
    if tilstand == "konflikt":
        conn.rollback()
        raise Aktiveringsfeil("idempotenskonflikt")
    rad = conn.execute(
        "SELECT innhold, status, utkastversjon FROM policyutkast WHERE"
        " tenant=%s AND utkast_id=%s FOR UPDATE", (tenant, utkast_id)).fetchone()
    if rad is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    innhold, status, ver = rad
    if status != "utkast":
        conn.rollback()
        raise Aktiveringsfeil("utkast_ulovlig_tilstand", f"status={status}")
    # Bind nøkkelen til den FAKTISKE versjonen: input_hash inneholder den
    # forventede versjonen, og her kreves at den stemmer med den låste raden.
    if not isinstance(forventet_utkastversjon, int) \
            or forventet_utkastversjon != ver:
        conn.rollback()
        raise Aktiveringsfeil("utkastversjon_utdatert", f"er={ver}")
    # Den KANONISKE validatoren: skjema + lag-2-semantikk (referanse-integritet,
    # modus/vilkår osv.) — samme port motoren bruker (PR-014 R2). Her i
    # INNFØRINGS-varianten: utkastet skal aktiveres, og porten inn er stedet
    # der framoverrettede krav (entydig verifikator-id) hører hjemme — ikke i
    # revalideringen av det som alt er aktivt (Codex P1 på #63).
    feil = _schema.valider_ny_policy(innhold)
    if feil:
        # Ugyldig CACHES også (bundet til versjonen): en retry med samme nøkkel
        # får samme svar; et endret utkast (ny versjon) → egen nøkkel/konflikt.
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "ugyldig", "utkast_id": utkast_id, "feil": feil})
    h = _pr.innholds_hash(innhold)
    conn.execute(
        "UPDATE policyutkast SET status='validert', innholds_hash=%s"
        " WHERE tenant=%s AND utkast_id=%s", (h, tenant, utkast_id))
    return _fullfor(conn, tenant, idempotency_key, {
        "utfall": "validert", "utkast_id": utkast_id, "innholds_hash": h})


def _runde_status(status: str, utloper, naa) -> str:
    """Rundens FAKTISKE status. En `apen`/`klar` runde som har passert
    `utloper` er `utlopt` — også før noen har rukket å skrive det ned.

    Statusen i basen er ikke en løgn, den er bare foreldet: overgangen til
    `utlopt` skjer først når en skrivesti kommer forbi (`_lukk_forfalt_runde`),
    og en forfalt runde kan bli liggende vilkårlig lenge uten at noen skriver.
    Lesestien har ingen slik anledning — den ruller tilbake — så den må REGNE
    seg fram til det samme.

    Uten dette var reparasjonen av skrivestiene ikke til å nå (Codex P2):
    flaten valgte handlinger på rundestatusen den fikk servert, så en forfalt
    runde som fortsatt sto `apen` skjulte BÅDE «Åpne runde» og «Forkast» og
    tilbød «Attester» — den ene handlingen som er umulig, siden
    `attester_aktivering` nekter nettopp en forfalt runde (`runde_utlopt`).
    Eier satt igjen med en knapp som alltid feiler og ingen vei ut, mens de to
    veiene ut fantes i API-et.

    Samme predikat som `attester_aktivering` bruker for å nekte, og som
    `_lukk_forfalt_runde` bruker for å lukke: én forståelse av «forfalt».
    """
    if status in ("apen", "klar") and utloper <= naa:
        return "utlopt"
    return status


def _lukk_forfalt_runde(conn: psycopg.Connection, tenant: str, utkast_id: str,
                        naa) -> bool:
    """Lås utkastets aktive runde, og lukk den (`apen|klar → utlopt`) om den
    har passert `utloper`. Returnerer True hvis en LEVENDE runde står igjen.

    Dette er den manglende OVERGANGEN, ikke en opprydding: fram til nå fantes
    det ingen kodesti som noensinne satte `utlopt`. `attester_aktivering`
    nekter en forfalt runde og RULLER TILBAKE, så raden blir liggende `apen`
    for alltid — og en slik zombie låser utkastet på to måter samtidig:

      * forkasting nektes, fordi den ser en «åpen» runde (Codex P2) — og
        forkasting er flatens ENESTE «slett», så forslaget blir uryddbart;
      * en NY runde kan ikke åpnes, fordi unik-indeksen
        `en_aktiv_aktiveringsrunde` teller den med.

    Ingen av dem kunne løses opp av eier. Statusmaskinen i migrasjon 012
    tillot `apen|klar → utlopt` hele tiden; det var bare ingen som gikk den.

    Kalleren eier tx og har allerede låst utkastraden — samme rekkefølge
    (utkast, så runde) i begge kallstedene, så to samtidige handlinger på
    samme utkast køer i stedet for å låse hverandre fast.
    """
    rad = conn.execute(
        "SELECT runde, status, utloper FROM aktiveringsrunde WHERE tenant=%s"
        " AND utkast_id=%s AND status IN ('apen','klar') FOR UPDATE",
        (tenant, utkast_id)).fetchone()
    if rad is None:
        return False
    r_nr, r_status, r_utloper = rad
    # Samme predikat som lesestien serverer (`_runde_status`) og som
    # `attester_aktivering` nekter på: skriver og leser skal aldri kunne bli
    # uenige om hva «forfalt» betyr.
    if _runde_status(r_status, r_utloper, naa) != "utlopt":
        return True
    # Forfalt: runden er allerede død for attestering (`runde_utlopt`), så
    # dette tar ingen fullmakt bort fra noen — det skriver bare ned det som
    # er sant. Attestasjonene blir stående; de tilhører runden, ikke utkastet.
    conn.execute(
        "UPDATE aktiveringsrunde SET status='utlopt' WHERE tenant=%s"
        " AND utkast_id=%s AND runde=%s", (tenant, utkast_id, r_nr))
    return False


def forkast_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                   request_id: str, utkast_id: str, forventet_utkastversjon,
                   idempotency_key: str, input_hash: str, naa) -> dict:
    """Forkast et utkast: status → `forkastet`. TERMINALT (statusmaskinen i
    migrasjon 012 slipper ingen vei ut igjen).

    Et utkast er et FORSLAG, ikke en policy. Å forkaste det endrer ingen
    fullmakt — ingen agent får lov til noe mer eller mindre av det — så det
    krever ikke fire øyne. Derfor er dette også det ENESTE «slett» flaten
    tilbyr: en policy som HAR styrt beslutninger kan ikke fjernes, for da
    ville revisjonssporet pekt på noe som ikke finnes lenger.

    To ting nektes:
      * en LEVENDE åpen eller klar runde — der er attestasjoner i omløp, og et
        utkast skal ikke kunne rives bort under godkjennerne mens de vurderer
        det. Runden må avsluttes først. En runde som har passert `utloper` er
        derimot ikke lenger i omløp: ingen kan attestere den, og den lukkes
        her (se `_lukk_forfalt_runde`) i stedet for å blokkere for alltid;
      * `godkjent` — da HAR fire øyne sagt ja, og å kaste den godkjenningen er
        en annen handling enn å rydde bort et forslag ingen har vurdert.

    Idempotensnøkkelen bindes til utkastversjonen som ellers i denne modulen.
    Kalleren eier tx.
    """
    sett_kontekst(conn, tenant, aktor, request_id)
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        conn.rollback()
        return lagret
    if tilstand == "konflikt":
        conn.rollback()
        raise Aktiveringsfeil("idempotenskonflikt")
    rad = conn.execute(
        "SELECT status, utkastversjon FROM policyutkast WHERE"
        " tenant=%s AND utkast_id=%s FOR UPDATE", (tenant, utkast_id)).fetchone()
    if rad is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    status, ver = rad
    if status not in ("utkast", "validert"):
        conn.rollback()
        raise Aktiveringsfeil("utkast_ulovlig_tilstand", f"status={status}")
    if not isinstance(forventet_utkastversjon, int) \
            or isinstance(forventet_utkastversjon, bool) \
            or forventet_utkastversjon != ver:
        conn.rollback()
        raise Aktiveringsfeil("utkastversjon_utdatert", f"er={ver}")
    if _lukk_forfalt_runde(conn, tenant, utkast_id, naa):
        conn.rollback()
        raise Aktiveringsfeil("runde_allerede_aapen")
    conn.execute(
        "UPDATE policyutkast SET status='forkastet'"
        " WHERE tenant=%s AND utkast_id=%s", (tenant, utkast_id))
    return _fullfor(conn, tenant, idempotency_key, {
        "utfall": "forkastet", "utkast_id": utkast_id})


def hent_utkast_detalj(conn: psycopg.Connection, *, tenant: str, aktor: str,
                       request_id: str, utkast_id: str, naa) -> dict:
    """Utkastet + diffen mot aktiv base + klassifisering + evt. åpen runde med
    attestasjoner. Rent lesende (ruller tilbake til slutt).

    `naa` er klokka lesestien måler `utloper` mot. Uten den var svaret om en
    forfalt runde ikke galt, bare foreldet — og flaten har ingen annen kilde
    (se `_runde_status` under)."""
    sett_kontekst(conn, tenant, aktor, request_id)
    rad = conn.execute(
        "SELECT policy_id, innhold, innholds_hash, status, utkastversjon,"
        " opprettet_av FROM policyutkast WHERE tenant=%s AND utkast_id=%s",
        (tenant, utkast_id)).fetchone()
    if rad is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    policy_id, innhold, innholds_hash, status, ver, opprettet_av = rad
    base_innhold, base_hash, aktiv = _base_med_versjon(conn, tenant, policy_id)
    v = _vurder(base_innhold, base_hash, innhold)
    runde = conn.execute(
        "SELECT runde, status, diff_hash, risikoklasse,"
        " pakrevd_antall_godkjennere, utloper FROM aktiveringsrunde"
        " WHERE tenant=%s AND utkast_id=%s ORDER BY runde DESC LIMIT 1",
        (tenant, utkast_id)).fetchone()
    runde_dto = None
    if runde is not None:
        r_nr, r_status, r_diff, r_risiko, r_pakrevd, r_utloper = runde
        r_status = _runde_status(r_status, r_utloper, naa)
        rows = conn.execute(
            "SELECT bruker_id, rolle, er_forfatter, ts FROM"
            " aktiveringsattestasjon WHERE tenant=%s AND utkast_id=%s AND"
            " runde=%s ORDER BY id", (tenant, utkast_id, r_nr)).fetchall()
        runde_dto = {
            "runde": r_nr, "status": r_status, "diff_hash": r_diff,
            "risikoklasse": r_risiko,
            "pakrevd_antall_godkjennere": r_pakrevd,
            "utloper": r_utloper.isoformat(),
            "attestasjoner": [
                {"bruker_id": b, "rolle": ro, "er_forfatter": ef,
                 "ts": ts.isoformat()} for b, ro, ef, ts in rows]}
    conn.rollback()
    return {
        "utkast_id": utkast_id, "policy_id": policy_id, "status": status,
        "utkastversjon": ver, "opprettet_av": opprettet_av,
        "innholds_hash": innholds_hash, "base_versjon": aktiv,
        "innhold": innhold,                        # for redigering i editoren
        # Basen diffen måles mot. Flaten trenger den for å LESE stiene i
        # diffen: map-nøkler skjøtes med punktum, og en nøkkel som selv
        # inneholder punktum har bare én kilde som vet hvor den slutter — den
        # siden nøkkelen finnes i. En SLETTET nøkkel finnes bare her.
        "base_innhold": base_innhold,
        "diff": v["diff"], "diff_hash": v["diff_hash"],
        "risikoklasse": v["risikoklasse"],
        "klassifisering_endringer": v["klassifisering_endringer"],
        "pakrevd_antall_godkjennere": v["pakrevd_antall_godkjennere"],
        "aktiv_runde": runde_dto}


_MAL_DIR = _schema._SKJEMA_STI.parent            # policies/


def hent_maler() -> list:
    """Bransjemalene (komplette policyer) som utgangspunkt for et nytt utkast.
    Rent lesende fra `policies/bransjemal-*.yaml` — ingen DB, ingen tenant
    (malene er felles). En mal som IKKE validerer mot den KANONISKE validatoren
    (`schema.valider_ny_policy` — skjema + semantikk, inkl. referanse-integritet
    og modus/vilkår, PR-014 R2) serveres ALDRI (fail-closed): den skal ikke
    kunne bli et «gyldig utgangspunkt». Innføringsvarianten er den riktige her:
    en mal er alltid en NY policy, og et utgangspunkt som ikke kan aktiveres er
    ikke et utgangspunkt."""
    import yaml
    ut = []
    for f in sorted(_MAL_DIR.glob("bransjemal-*.yaml")):
        try:
            innhold = yaml.safe_load(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(innhold, dict):
            continue
        if _schema.valider_ny_policy(innhold):
            continue                                # fail-closed: hopp over
        meta = innhold.get("meta") if isinstance(innhold.get("meta"), dict) else {}
        ut.append({"mal_id": f.stem.replace("bransjemal-", ""),
                   "bransjemal": meta.get("bransjemal") or f.stem,
                   "innhold": innhold})
    return ut


def list_utkast(conn: psycopg.Connection, *, tenant: str, aktor: str,
                request_id: str, policy_id: str | None = None) -> list:
    """Utkastene for tenanten (evt. filtrert på policy_id). Rent lesende."""
    sett_kontekst(conn, tenant, aktor, request_id)
    if policy_id:
        rows = conn.execute(
            "SELECT utkast_id, policy_id, status, utkastversjon, opprettet"
            " FROM policyutkast WHERE tenant=%s AND policy_id=%s"
            " ORDER BY opprettet DESC", (tenant, policy_id)).fetchall()
    else:
        rows = conn.execute(
            "SELECT utkast_id, policy_id, status, utkastversjon, opprettet"
            " FROM policyutkast WHERE tenant=%s ORDER BY opprettet DESC",
            (tenant,)).fetchall()
    conn.rollback()
    return [{"utkast_id": u, "policy_id": p, "status": s, "utkastversjon": vv,
             "opprettet": o.isoformat()} for u, p, s, vv, o in rows]


def _hode_aktiv_versjon(conn, tenant, policy_id) -> str | None:
    """Aktiv versjon fra `policy_hode` (plain SELECT). Runtime har KUN SELECT på
    `policy_hode` (V10) — den kan verken låse eller skrive pekeren, og skal ikke:
    den ekte serialiseringen er den herdede `aktiver_policy` (kjører som
    policy-eieren, låser hoderaden og avviser en flyttet base med
    `serialization_failure`). Finnes ikke hoderaden (helt ny policy), er basen
    deny-all og funksjonen oppretter ankerraden idempotent ved aktivering — vi
    oppretter den ALDRI her (en forkastet runde skal ikke etterlate en tom
    hoderad)."""
    rad = conn.execute(
        "SELECT aktiv_versjon FROM policy_hode WHERE tenant=%s AND policy_id=%s",
        (tenant, policy_id)).fetchone()
    return rad[0] if rad else None


def _krev_peker_synk(conn, tenant: str, policy_id: str,
                     aktiv_versjon: str | None) -> None:
    """Pekeren (`policy_hode.aktiv_versjon`) og flagget (`policyer.aktiv`) er to
    utsagn om NØYAKTIG samme sak. Spriker de, er ikke bare aktiveringen i fare —
    HELE runden bygger på feil grunnlag: `_base` følger pekeren, så en tom peker
    over en aktiv policyrad gir en diff mot `DENY_ALL_V1`, en risikoklasse regnet
    ut fra det, og godkjennere som signerer NØYAKTIG den feilen. Repareres
    pekeren etterpå, flytter basen seg, og rekalken under låsen krever
    rebasering: attestasjonene var da verdiløse fra det øyeblikket de ble avgitt.

    Derfor stoppes drift HER — før runden åpnes og før noen attesterer — ikke
    først når `en_aktiv_per_policy` velter INSERT-en inne i `aktiver_policy`.
    Kaster `Aktiveringsfeil("aktiv_peker_usynk")`; dataene må repareres."""
    rad = conn.execute(
        "SELECT versjon FROM policyer WHERE tenant=%s AND policy_id=%s AND aktiv",
        (tenant, policy_id)).fetchone()
    flagget = rad[0] if rad else None
    if flagget != aktiv_versjon:
        raise Aktiveringsfeil(
            "aktiv_peker_usynk", f"peker={aktiv_versjon} flagg={flagget}")


#: Skjemaets versjonsform (`policy-schema-v0.2.json`: `meta.versjon`).
_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
#: Tallpunktet versjon — semver, men også de eldre «1»/«2»-radene den styrte
#: aktiveringen skrev før migrasjon 020. Alt annet sammenlignes ikke.
_TALLVERSJON = re.compile(r"^\d+(\.\d+)*$")


def _versjonsnokkel(versjon: str, ledd: int) -> tuple[int, ...]:
    """Tallpunktet versjon som sammenlignbar nøkkel, NULLPADDET til `ledd`.

    Paddingen er ikke kosmetikk. Uten den sorterer tuppelsammenligningen
    «2.0.0» OVER «2» — prefikset er likt, og den lengste vinner. Det er
    nøyaktig formen de eldre radene bærer («1», «2», skrevet av telleren
    migrasjon 020 fjerner), så en aktiv «2»-rad ville sluppet gjennom
    dokumentversjonen «2.0.0»: samme versjon, ikke en nyere. Padder vi begge
    til samme bredde, blir «2» → (2, 0, 0) og de to er like — som de er.
    """
    tall = tuple(int(d) for d in versjon.split("."))
    return tall + (0,) * (ledd - len(tall))


def _krev_ny_versjon(conn, tenant: str, policy_id: str, ny_innhold,
                     aktiv_versjon: str | None) -> str:
    """Versjonen aktiveringen kommer til å lagre er utkastets EGEN
    `meta.versjon` (migrasjon 020: dokumentet eier versjonen, registerkolonnen
    indekserer det). Da må den holde MENS runden bygges, ikke bare når
    `aktiver_policy` til slutt låser hodet:

    * uten en semantisk `meta.versjon` kan utkastet ikke aktiveres i det hele
      tatt (`policyregister.hent_aktiv` krever at kolonnen og dokumentet er
      enige — ellers er den ferske policyen korrupt for beslutningsveien);
    * er versjonen alt registrert, eller ikke nyere enn den aktive, kan den
      ikke skrives — og det er ingenting godkjennerne kan gjøre med det. Eier
      må øke `meta.versjon` i utkastet og validere på nytt.

    Å oppdage dette først ved aktivering ville kastet bort en hel runde: to
    signaturer på et utkast som aldri kunne lande. Kontrollen speiler derfor
    `_krev_peker_synk` — den kjører før runden åpnes og før noen attesterer.
    -> versjonen som vil bli lagret. Kaster `Aktiveringsfeil`."""
    meta = ny_innhold.get("meta") if isinstance(ny_innhold, dict) else None
    ny = meta.get("versjon") if isinstance(meta, dict) else None
    if not isinstance(ny, str) or not _SEMVER.match(ny):
        raise Aktiveringsfeil("versjon_mangler", f"meta.versjon={ny!r}")
    if conn.execute(
            "SELECT 1 FROM policyer WHERE tenant=%s AND policy_id=%s"
            " AND versjon=%s", (tenant, policy_id, ny)).fetchone():
        raise Aktiveringsfeil("versjon_i_bruk", f"versjon={ny} finnes")
    if aktiv_versjon is not None and _TALLVERSJON.match(aktiv_versjon):
        ledd = max(ny.count("."), aktiv_versjon.count(".")) + 1
        if _versjonsnokkel(ny, ledd) <= _versjonsnokkel(aktiv_versjon, ledd):
            raise Aktiveringsfeil(
                "versjon_i_bruk",
                f"versjon={ny} ikke nyere enn {aktiv_versjon}")
    return ny


def _krev_innforingskrav(ny_innhold) -> None:
    """Utkastet må oppfylle de FRAMOVERRETTEDE kravene for å kunne aktiveres —
    ikke bare ha oppfylt dem den gangen det ble validert.

    `valider_utkast` er porten inn, men den er en ENGANGS-port: den kjører idet
    utkastet går til `validert`, og statusen blir stående. Et utkast som fikk
    `validert` FØR et slikt krav fantes bærer statusen videre, og
    runde-åpningen leser bare status + `innholds_hash` (Codex P2 på #63). Da
    kunne det aktiveres uten noen gang å ha møtt kravet — og «gjelder framover»
    ville i praksis betydd «gjelder framover, unntatt for de utkastene som alt
    lå klare», nøyaktig de som lander først etter utrullingen. Kravet hører
    derfor hjemme på aktiveringsveien selv, ikke bare på porten inn.

    KUN differansen (`schema.valider_innforingskrav`), ikke hele
    `valider_ny_policy`: lastekontrakten er bakoverkompatibel og sier per
    definisjon ingenting nytt her, og å dra den inn ville blandet «bryter et
    nytt krav» sammen med «er strukturelt ødelagt» i samme feilkode.

    Kontrollen speiler `_krev_peker_synk`/`_krev_ny_versjon`: den kjører både
    før runden åpnes OG før noen attesterer. Det andre kallet er ikke
    overflødig — en runde kan ha vært åpen da utrullingen landet, og en
    signatur på et utkast som ikke kan aktiveres er verdiløs i det den skrives.

    Merk asymmetrien mot `hent_aktiv`: en alt AKTIV policy revalideres fortsatt
    mot lastekontrakten alene og virker som før. Det er bare veien INN som
    strammes. Kaster `Aktiveringsfeil("utkast_ugyldig")`; eier må rette
    utkastet og validere det på nytt.

    SISTE SKANSE ligger likevel i `aktiver_policy` (migrasjon 022): begge
    kontrollene her er passert i det aktiveringen skjer, og en runde kan ha
    vært ferdig attestert allerede da utrullingen landet. Denne funksjonen er
    porten som gir eier en forståelig feil FØR signaturene brukes; funksjonen i
    DB er invarianten som holder også for et direkte kall utenom oss."""
    feil = _schema.valider_innforingskrav(ny_innhold)
    if feil:
        raise Aktiveringsfeil("utkast_ugyldig", "; ".join(feil))


#: `CONSTRAINT`-navnet `aktiver_policy` merker innføringskravbruddet med
#: (migrasjon 022). Skiller det fra versjonsinvariantene, som deler SQLSTATE
#: `check_violation` — uten det måtte utfallet utledes av feilteksten.
_INNFORINGSKRAV_CONSTRAINT = "verifikator_id_entydig"


# --------------------------------------------------------------------------
# 1. Runde-åpning.
# --------------------------------------------------------------------------

def opprett_aktiveringsrunde(conn: psycopg.Connection, *, tenant: str,
                             utkast_id: str, aktor: str, request_id: str,
                             idempotency_key: str, input_hash: str,
                             naa) -> dict:
    """Åpne en aktiveringsrunde for et VALIDERT utkast. Utleder diff + klasse
    under `policy_hode`-låsen og fryser ALT i runden. Returnerer det
    godkjennerne skal se (diff, risikoklasse, påkrevd antall). Idempotent
    (P1 R3) — committer via `_fullfor`. Kaster `Aktiveringsfeil`."""
    sett_kontekst(conn, tenant, aktor, request_id)
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        conn.rollback()
        return lagret
    if tilstand == "konflikt":
        conn.rollback()
        raise Aktiveringsfeil("idempotenskonflikt")

    utk = conn.execute(
        "SELECT policy_id, innhold, innholds_hash, status FROM policyutkast"
        " WHERE tenant=%s AND utkast_id=%s FOR UPDATE",
        (tenant, utkast_id)).fetchone()
    if utk is None:
        raise Aktiveringsfeil("utkast_ukjent")
    policy_id, ny_innhold, innholds_hash, status = utk
    if status != "validert":
        # Kun et validert utkast (med frosset innholds_hash) kan aktiveres.
        raise Aktiveringsfeil("utkast_ikke_validert", f"status={status}")
    if innholds_hash is None:
        raise Aktiveringsfeil("utkast_ikke_validert", "mangler innholds_hash")
    # En forfalt runde er ikke en åpen runde. Sto den igjen som `apen`, tok
    # unik-indeksen under INSERT-en nedenfor imot og svarte
    # `runde_allerede_aapen` — for alltid, siden ingenting ellers lukker den.
    _lukk_forfalt_runde(conn, tenant, utkast_id, naa)

    aktiv_versjon = _hode_aktiv_versjon(conn, tenant, policy_id)
    # Ingen runde åpnes på en base vi ikke stoler på (Codex P1): en usynk peker
    # ville gitt godkjennerne en diff mot feil base, og runden kunne uansett
    # ikke aktiveres etter en reparasjon.
    _krev_peker_synk(conn, tenant, policy_id, aktiv_versjon)
    # Og ingen runde åpnes på et utkast som ikke KAN lagres: versjonen det
    # bærer må være semantisk, ubrukt og nyere enn den aktive (migrasjon 020).
    _krev_ny_versjon(conn, tenant, policy_id, ny_innhold, aktiv_versjon)
    # ... eller som ikke oppfyller de framoverrettede kravene: `validert` kan
    # stamme fra før kravet fantes, og status alene er ingen kvittering.
    _krev_innforingskrav(ny_innhold)
    base_innhold, base_hash = _base(conn, tenant, policy_id, aktiv_versjon)
    v = _vurder(base_innhold, base_hash, ny_innhold)

    runde = int(conn.execute(
        "SELECT coalesce(max(runde),0)+1 FROM aktiveringsrunde"
        " WHERE tenant=%s AND utkast_id=%s", (tenant, utkast_id)).fetchone()[0])
    try:
        conn.execute(
            "INSERT INTO aktiveringsrunde (tenant, utkast_id, runde, status,"
            " diff_hash, utkast_innholds_hash, base_policy_hash, risikoklasse,"
            " klassifisering_hash, klassifikatorversjon, policyskjema_versjon,"
            " motor_semantikkversjon, deny_all_hash, deny_all_versjon,"
            " pakrevd_antall_godkjennere, utloper)"
            " VALUES (%s,%s,%s,'apen',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tenant, utkast_id, runde, v["diff_hash"], innholds_hash,
             v["base_policy_hash"], v["risikoklasse"], v["klassifisering_hash"],
             v["klassifikatorversjon"], POLICYSKJEMA_VERSJON,
             semantikk.MOTOR_SEMANTIKKVERSJON, semantikk.DENY_ALL_HASH,
             semantikk.DENY_ALL_VERSJON, v["pakrevd_antall_godkjennere"],
             naa + RUNDE_TTL))
    except psycopg.errors.UniqueViolation:
        # en_aktiv_aktiveringsrunde: allerede en åpen/klar runde for utkastet.
        conn.rollback()
        raise Aktiveringsfeil("runde_allerede_aapen") from None

    return _fullfor(conn, tenant, idempotency_key, {
        "utkast_id": utkast_id, "policy_id": policy_id, "runde": runde,
        "diff": v["diff"], "diff_hash": v["diff_hash"],
        "risikoklasse": v["risikoklasse"],
        "klassifisering_hash": v["klassifisering_hash"],
        "pakrevd_antall_godkjennere": v["pakrevd_antall_godkjennere"],
        "base_versjon": aktiv_versjon,
    })


def _kan_gjenoppta_aktivering(conn, tenant: str, utkast_id: str, runde: int,
                              aktor: str, diff_hash: str, pakrevd: int) -> bool:
    """Er en ny innsending fra en godkjenner som ALT har attestert et lovlig
    forsøk på å fullføre aktiveringen — eller bare en dublett?

    Lovlig KUN når runden allerede er på terskel (antall ≥ påkrevd OG minst én
    ikke-forfatter) og aktørens eksisterende attestasjon binder NØYAKTIG denne
    rundens diff. Da er signaturene på plass, runden er fortsatt åpen, og det
    eneste som gjenstår er aktiveringen: nøyaktig tilstanden `aktiv_peker_usynk`
    etterlater. Innsendingen skriver ingen ny signatur — den kjører de samme
    kontrollene og forsøker aktiveringen om igjen.

    Er runden IKKE på terskel, er det ingenting å gjenoppta: runden venter på
    ANDRE godkjennere, og dubletten er en konflikt (`allerede_attestert`).
    Fire-øyne-gaten står urørt — terskelen måles her på de lagrede radene, og
    `aktiver_policy` verifiserer den uansett selv som policy-eieren."""
    egen = conn.execute(
        "SELECT diff_hash FROM aktiveringsattestasjon WHERE tenant=%s AND"
        " utkast_id=%s AND runde=%s AND bruker_id=%s",
        (tenant, utkast_id, runde, aktor)).fetchone()
    if egen is None or egen[0] != diff_hash:
        # Kollisjonen kom fra noe annet enn aktørens egen attestasjon på denne
        # diffen (f.eks. `jti`) — da er dette ikke en gjenopptakelse.
        return False
    antall, uavhengige = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE NOT er_forfatter) FROM"
        " aktiveringsattestasjon WHERE tenant=%s AND utkast_id=%s AND runde=%s",
        (tenant, utkast_id, runde)).fetchone()
    return antall >= pakrevd and uavhengige >= 1


# --------------------------------------------------------------------------
# 2+3. Attestering + (ved terskel) aktivering.
# --------------------------------------------------------------------------

def attester_aktivering(conn: psycopg.Connection, mac_register, *,
                        tenant: str, aktor: str, request_id: str,
                        utkast_id: str, forventet_diff_hash: str,
                        idempotency_key: str, input_hash: str, naa) -> dict:
    """En godkjenner attesterer diffen. Når terskelen (V6) er nådd, aktiveres
    policyen via den herdede funksjonen — etter en rekalk under låsen som
    avviser en flyttet base (rebasering). Eier transaksjonen. Kaster
    `Aktiveringsfeil`."""
    sett_kontekst(conn, tenant, aktor, request_id)

    # --- 1. Idempotens: serialiser per nøkkel og claim i eiertransaksjonen ---
    tilstand, lagret = _idempotent_start(conn, tenant, idempotency_key,
                                         input_hash, request_id)
    if tilstand == "replay":
        conn.rollback()
        return lagret
    if tilstand == "konflikt":
        conn.rollback()
        raise Aktiveringsfeil("idempotenskonflikt")

    # --- 2. Lås utkastet ---------------------------------------------------
    utk = conn.execute(
        "SELECT policy_id, innhold, innholds_hash, status, opprettet_av"
        " FROM policyutkast WHERE tenant=%s AND utkast_id=%s FOR UPDATE",
        (tenant, utkast_id)).fetchone()
    if utk is None:
        conn.rollback()
        raise Aktiveringsfeil("utkast_ukjent")
    policy_id, ny_innhold, innholds_hash, ustatus, opprettet_av = utk
    if ustatus not in ("validert", "godkjent"):
        conn.rollback()
        raise Aktiveringsfeil("utkast_ulovlig_tilstand", f"status={ustatus}")

    # --- 3. REAUTORISERING ETTER LÅSEN (fail-closed, ingen fallback) -------
    med = conn.execute(
        "SELECT roller, authz_version FROM brukermedlemskap WHERE tenant=%s"
        " AND bruker_id=%s AND aktiv", (tenant, aktor)).fetchone()
    if med is None:
        conn.rollback()
        raise Aktiveringsfeil("mangler_medlemskap")
    roller = list(med[0])
    authz_version = int(med[1])
    if _AKTIVER_SCOPE not in scopes_for_roller(roller):
        conn.rollback()
        raise Aktiveringsfeil("scope_mangler")
    rolle = _revisjonsrolle(roller)

    # --- 4. Lås hodet + den aktive runden ----------------------------------
    aktiv_versjon = _hode_aktiv_versjon(conn, tenant, policy_id)
    runde = conn.execute(
        "SELECT runde, status, diff_hash, klassifisering_hash, risikoklasse,"
        " base_policy_hash, klassifikatorversjon, motor_semantikkversjon,"
        " pakrevd_antall_godkjennere, utloper FROM aktiveringsrunde"
        " WHERE tenant=%s AND utkast_id=%s AND status IN ('apen','klar')"
        " FOR UPDATE", (tenant, utkast_id)).fetchone()
    if runde is None:
        conn.rollback()
        raise Aktiveringsfeil("ingen_aktiv_runde")
    (r_nr, r_status, r_diff_hash, r_klass_hash, r_risiko, r_base_hash,
     r_klassver, r_motorver, r_pakrevd, r_utloper) = runde
    if r_utloper <= naa:
        conn.rollback()
        raise Aktiveringsfeil("runde_utlopt")

    # --- 5. Godkjenneren attesterer DIFFEN, ikke versjonsnummeret (v5 §2) ---
    if forventet_diff_hash != r_diff_hash:
        conn.rollback()
        raise Aktiveringsfeil("diff_utdatert")

    # --- 5b. Basen må være TROVERDIG før noen signerer på den (Codex P1) ----
    # En runde kan ha vært åpen da drift oppsto (eller ha blitt åpnet før denne
    # kontrollen fantes). En godkjenner skal ikke få avgi en attestasjon som er
    # verdiløs i det øyeblikket den skrives: attesterer hun en diff mot en base
    # pekeren ikke er enig i, kan runden ikke aktiveres — og en reparasjon
    # flytter basen, så rekalken i steg 9 krever rebasering uansett.
    try:
        _krev_peker_synk(conn, tenant, policy_id, aktiv_versjon)
        # Samme argument for versjonen: en signatur på et utkast som ikke kan
        # lagres er like verdiløs som en signatur på feil base.
        _krev_ny_versjon(conn, tenant, policy_id, ny_innhold, aktiv_versjon)
        # Og for de framoverrettede kravene: runden kan ha vært åpen da
        # utrullingen som innførte dem landet.
        _krev_innforingskrav(ny_innhold)
    except Aktiveringsfeil:
        conn.rollback()
        raise

    # --- 6. Bygg + MAC-signer konvolutten fra LÅSTE data -------------------
    er_forfatter = (aktor == opprettet_av)
    konvolutt = {
        "konvolutt_type": KONVOLUTT_TYPE, "konvoluttversjon": KONVOLUTTVERSJON,
        "tenant": tenant, "utkast_id": utkast_id, "policy_id": policy_id,
        "runde": r_nr, "diff_hash": r_diff_hash,
        "klassifisering_hash": r_klass_hash, "risikoklasse": r_risiko,
        "base_policy_hash": r_base_hash, "bruker_id": aktor,
        "er_forfatter": er_forfatter, "rolle": rolle,
        "authz_version": authz_version,
        "jti": f"{tenant}-{utkast_id}-r{r_nr}-{aktor}".ljust(22, "j"),
        "utloper": r_utloper.isoformat()}
    mac_key_id, mac = mac_register.signer(konvolutt)
    konvolutt["mac"], konvolutt["mac_key_id"] = mac, mac_key_id

    if not mac_register.verifiser(konvolutt, mac, mac_key_id):
        conn.rollback()
        raise Aktiveringsfeil("sikkerhet", "mac_ugyldig")
    _forventet = {"konvolutt_type": KONVOLUTT_TYPE, "tenant": tenant,
                  "utkast_id": utkast_id, "policy_id": policy_id, "runde": r_nr,
                  "diff_hash": r_diff_hash, "klassifisering_hash": r_klass_hash,
                  "risikoklasse": r_risiko, "base_policy_hash": r_base_hash,
                  "bruker_id": aktor, "er_forfatter": er_forfatter}
    for felt in _BINDINGSFELT:
        if konvolutt.get(felt) != _forventet[felt]:
            conn.rollback()
            raise Aktiveringsfeil("sikkerhet", f"bindingsavvik:{felt}")

    konvolutt_hash = hashlib.sha256(kanonisk_konvolutt(konvolutt)).hexdigest()

    # --- 7. Skriv attestasjonen (append-only; trigger vokter er_forfatter) --
    # Savepointet gjør at en kollisjon kan BESVARES i stedet for å velte
    # transaksjonen: en godkjenner som alt har attestert er som regel en
    # konflikt, men ikke alltid (steg 7b).
    conn.execute("SAVEPOINT attestasjonsforsok")
    try:
        conn.execute(
            "INSERT INTO aktiveringsattestasjon (tenant, utkast_id, runde,"
            " bruker_id, rolle, authz_version, er_forfatter, diff_hash,"
            " klassifisering_hash, risikoklasse, konvoluttversjon,"
            " konvolutt_hash, mac, mac_key_id, jti, utloper)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (tenant, utkast_id, r_nr, aktor, rolle, authz_version,
             er_forfatter, r_diff_hash, r_klass_hash, r_risiko,
             KONVOLUTTVERSJON, konvolutt_hash, mac, mac_key_id,
             konvolutt["jti"], r_utloper))
    except psycopg.errors.UniqueViolation:
        # --- 7b. Samme godkjenner, samme runde, én gang til ----------------
        # Append-only-nøkkelen stopper en NY signatur — men innsendingen kan
        # være det eneste gjenværende forsøket på å FULLFØRE en runde som alt
        # står på terskel. Det skjer etter `aktiv_peker_usynk` i steg 10:
        # attestasjonen ble bevart, runden står åpen, og etter at eier har
        # reparert dataene finnes det ingen annen vei inn — samme
        # idempotensnøkkel replayer bare det lagrede utfallet, og en ny nøkkel
        # traff før dette punktet. Uten denne veien står en runde på NØYAKTIG
        # terskel fast til en ekstra kvalifisert person signerer unødig.
        conn.execute("ROLLBACK TO SAVEPOINT attestasjonsforsok")
        if not _kan_gjenoppta_aktivering(conn, tenant, utkast_id, r_nr, aktor,
                                         r_diff_hash, r_pakrevd):
            conn.rollback()
            raise Aktiveringsfeil("allerede_attestert") from None
        # Faller gjennom til steg 8: ingen ny signatur skrives, men terskelen,
        # reautoriseringen, rekalken og selve aktiveringen kjøres på nytt —
        # med nøyaktig de samme kontrollene som første gang.
    else:
        conn.execute("RELEASE SAVEPOINT attestasjonsforsok")

    # --- 8. Terskel (V6): antall ≥ påkrevd OG minst én ikke-forfatter -------
    rader = conn.execute(
        "SELECT bruker_id, er_forfatter, rolle, authz_version FROM"
        " aktiveringsattestasjon WHERE tenant=%s AND utkast_id=%s AND runde=%s",
        (tenant, utkast_id, r_nr)).fetchall()
    antall = len(rader)
    ikke_forfatter = sum(1 for _b, ef, _r, _a in rader if not ef)
    if antall < r_pakrevd or ikke_forfatter < 1:
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "venter_godkjennere", "utkast_id": utkast_id,
            "runde": r_nr, "antall": antall,
            "gjenstaar": max(0, r_pakrevd - antall),
            "mangler_uavhengig": ikke_forfatter < 1})

    # --- 8b. REAUTORISER ALLE godkjennere ved aktivering (Codex R2) ---------
    # Den siste attestasjonen utløser aktiveringen — men de TIDLIGERE
    # godkjennerne ble autorisert da DE attesterte. Medlemskapet LÅSES og den
    # bundne rollen + authz_version sammenlignes; en rolle-/aktiv-endring i
    # vinduet mellom en tidlig attestasjon og aktiveringen stopper den
    # (fail-closed). Låsen serialiserer mot en samtidig tilbakekalling.
    avvist = _reautoriser_godkjennere(
        conn, tenant, [(b, ro, a) for b, _ef, ro, a in rader])
    if avvist is not None:
        conn.rollback()
        raise Aktiveringsfeil("godkjenner_deautorisert", avvist)

    # --- 9. REKALK UNDER LÅSEN: har basen/semantikken flyttet seg? ---------
    # aktiv_versjon ble lest FOR UPDATE i steg 4, så settet er stabilt.
    base_innhold, base_hash = _base(conn, tenant, policy_id, aktiv_versjon)
    v = _vurder(base_innhold, base_hash, ny_innhold)
    if (v["diff_hash"] != r_diff_hash
            or v["base_policy_hash"] != r_base_hash
            or v["klassifisering_hash"] != r_klass_hash
            or v["risikoklasse"] != r_risiko):
        # En konkurrerende aktivering (eller redigert base) flyttet grunnlaget
        # godkjennerne så → runden kanselleres, rebasering kreves.
        conn.execute("UPDATE aktiveringsrunde SET status='kansellert'"
                     " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
                     (tenant, utkast_id, r_nr))
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "rebasering_kreves", "utkast_id": utkast_id})
    if (v["klassifikatorversjon"] != r_klassver
            or semantikk.MOTOR_SEMANTIKKVERSJON != r_motorver):
        # Motorsemantikken (og dermed klassifikatoren) endret seg siden runden
        # åpnet → klassifiseringen godkjennerne så er stale. Ny runde kreves.
        conn.execute("UPDATE aktiveringsrunde SET status='kansellert'"
                     " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
                     (tenant, utkast_id, r_nr))
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "semantikk_endret", "utkast_id": utkast_id})

    # --- 10. Aktiver via den herdede funksjonen. Funksjonen VERIFISERER SELV
    #         runde + attestasjonsterskel + base-versjon (fire-øyne-gaten ligger
    #         i DB-en, ikke her — Codex P1 R1), leser innholdet fra utkastet, og
    #         lukker runde+utkast atomisk. Et direkte runtime-kall utenom denne
    #         orkestreringen når aldri forbi funksjonens egne kontroller.
    # Savepointet er ikke pynt: attestasjonen i steg 7 ligger i DENNE
    # transaksjonen, og denne godkjenneren er den som fylte terskelen. En full
    # rollback her ville tatt HENNES godkjenning med i fallet — runden ville
    # stått åpen og under terskel igjen, og hun måtte attestert på nytt, stikk i
    # strid med at en usynk peker skal REPARERES og ikke re-attesteres (Codex
    # P1). Savepointet ruller derfor tilbake NØYAKTIG aktiveringsforsøket, og
    # attestasjonen committes med det deterministiske utfallet.
    conn.execute("SAVEPOINT aktiveringsforsok")
    try:
        ny_versjon = conn.execute(
            "SELECT aktiver_policy(%s,%s,%s,%s)",
            (tenant, utkast_id, r_nr, aktiv_versjon)).fetchone()[0]
    except psycopg.errors.SerializationFailure:
        # En konkurrerende aktivering vant kappløpet i funksjonens egen lås.
        # Funksjonen er serialiseringspunktet (V10) — flyttet base = rebasering.
        # Her ER runden død (basen godkjennerne så finnes ikke lenger), så
        # attestasjonen har ingenting å bevares til: en ny runde krever nye.
        conn.rollback()
        raise Aktiveringsfeil("rebasering_kreves") from None
    except psycopg.errors.UniqueViolation:
        # `en_aktiv_per_policy` slo til: det finnes en aktiv policyrad som
        # pekeren ikke kjenner til. Kontrollen i steg 5b fanger drift som ALT
        # var der; hit kommer bare drift som oppsto i vinduet mellom kontrollen
        # og aktiveringen. Runden får stå, attestasjonen består, og eier får et
        # utfall som sier hva som er galt — ikke «Exception in ASGI
        # application», og ikke en tapt godkjenning.
        conn.execute("ROLLBACK TO SAVEPOINT aktiveringsforsok")
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": "aktiv_peker_usynk", "utkast_id": utkast_id,
            "policy_id": policy_id, "runde": r_nr})
    except psycopg.errors.CheckViolation as e:
        # Innholdsinvariantene i `aktiver_policy`: enten VERSJONEN (migrasjon
        # 020 — `meta.versjon` er borte, alt registrert, eller ikke nyere enn
        # den aktive), eller INNFØRINGSKRAVET (migrasjon 022 — en verifikator-id
        # som gjør diffstien flertydig). Kontrollene i steg 5b fanger det som
        # var der da runden ble bygget; hit kommer bare det som traff UTENOM
        # den styrte veien i vinduet etterpå — eller, for innføringskravet, en
        # runde som var ferdig attestert før utrullingen som innførte det.
        #
        # Uansett hvilken av de to: runden er død. Innholdet er frosset, så
        # verken versjonen eller id-en kan rettes uten et nytt utkast og nye
        # signaturer. Runden kanselleres derfor med det samme — en runde som
        # beviselig aldri kan aktiveres skal ikke stå åpen og se levende ut.
        # Signaturene består (append-only); det er sporet av hva som faktisk
        # ble godkjent.
        #
        # UTFALLET må derimot skilles. De to krever ulik retting av eier (øk
        # versjonen vs. rett id-en), og «versjonen er i bruk» om en
        # verifikator-id er en feilmelding som sender eier feil vei.
        # Funksjonen merker id-bruddet med `CONSTRAINT` (022), så skillet
        # leses maskinelt og ikke ut av feilteksten.
        conn.execute("ROLLBACK TO SAVEPOINT aktiveringsforsok")
        conn.execute("UPDATE aktiveringsrunde SET status='kansellert'"
                     " WHERE tenant=%s AND utkast_id=%s AND runde=%s",
                     (tenant, utkast_id, r_nr))
        utfall = ("utkast_ugyldig"
                  if e.diag.constraint_name == _INNFORINGSKRAV_CONSTRAINT
                  else "versjon_i_bruk")
        return _fullfor(conn, tenant, idempotency_key, {
            "utfall": utfall, "utkast_id": utkast_id,
            "policy_id": policy_id, "runde": r_nr})
    conn.execute("RELEASE SAVEPOINT aktiveringsforsok")

    return _fullfor(conn, tenant, idempotency_key, {
        "utfall": "aktivert", "utkast_id": utkast_id, "policy_id": policy_id,
        "versjon": ny_versjon, "runde": r_nr, "risikoklasse": r_risiko})


def _reautoriser_godkjennere(conn, tenant: str, attestasjoner) -> str | None:
    """Reverifiser HVER godkjenner i runden ved aktivering (Codex R2).
    `attestasjoner`: iterable av (bruker_id, rolle, authz_version) — de bundne
    verdiene fra attestasjonstiden. -> None hvis alle fortsatt er autorisert,
    ellers `"<bruker>:<grunn>"`.

    Medlemskapet LÅSES (`laas_godkjenner`, FOR UPDATE via SECURITY DEFINER) så en
    samtidig tilbakekalling ikke kan committe etter lesningen og tape kappløpet.
    Autorisasjonen må være UENDRET siden attestasjonen: samme `authz_version`
    (enhver rolle-/aktiv-endring bumper den), den bundne rollen fortsatt til
    stede, og `policy:activate` fortsatt gitt. Fail-closed."""
    sett = {}
    for bid, rolle, av in attestasjoner:
        sett[bid] = (rolle, av)                     # distinkt per bruker (UNIQUE)
    # DETERMINISTISK låserekkefølge (Codex R3): to samtidige aktiveringer som
    # deler godkjennere ville kunne vranglåse om de tok radlåsene i motsatt
    # rekkefølge. Sortert på bruker_id tar begge samme lås først → serialisering.
    for bid in sorted(sett):
        rolle, av = sett[bid]
        rad = conn.execute("SELECT roller, authz_version FROM"
                           " laas_godkjenner(%s,%s)", (tenant, bid)).fetchone()
        if rad is None:
            return f"{bid}:mangler_medlemskap"
        roller, naa_av = list(rad[0]), int(rad[1])
        if naa_av != int(av):
            return f"{bid}:authz_endret"            # roller/aktiv endret siden attest
        if rolle not in roller:
            return f"{bid}:rolle_borte"
        if _AKTIVER_SCOPE not in scopes_for_roller(roller):
            return f"{bid}:scope_mangler"
    return None


def _revisjonsrolle(roller) -> str:
    """En ekte rolle som gir `policy:activate` (for revisjonssporet). Scope er
    allerede bevist; vi velger den FØRSTE rollen som faktisk bærer scopet, så
    audit-rollen aldri er en rolle uten aktiveringsfullmakt."""
    for r in roller:
        if _AKTIVER_SCOPE in scopes_for_roller([r]):
            return r
    return roller[0]


def _idempotent_start(conn, tenant: str, idempotency_key: str,
                      input_hash: str, request_id: str):
    """Claim en idempotensnøkkel i kallerens tx (spec: `Idempotency-Key` på ALLE
    skriveruter, Codex P1 R3). -> ("ny", None) fortsett · ("replay", dict)
    returner lagret respons · ("konflikt", None) samme nøkkel, ANNET input.
    Serialiserer per nøkkel med en advisory-lås, som unntaksbehandlingen."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                 (f"{tenant}\x1fpolidem\x1f{idempotency_key}",))
    claim = conn.execute(
        "INSERT INTO idempotens (tenant, nokkel, input_hash, status, request_id)"
        " VALUES (%s,%s,%s,'paagaar',%s) ON CONFLICT (tenant, nokkel)"
        " DO NOTHING RETURNING nokkel",
        (tenant, idempotency_key, input_hash, request_id)).fetchone()
    if claim is not None:
        return ("ny", None)
    eksist = conn.execute(
        "SELECT input_hash, status, respons FROM idempotens"
        " WHERE tenant=%s AND nokkel=%s",
        (tenant, idempotency_key)).fetchone()
    if eksist is None:
        return ("konflikt", None)
    lagret_hash, istatus, respons = eksist
    if lagret_hash != input_hash:
        return ("konflikt", None)          # samme nøkkel, annet input
    if istatus == "ferdig":
        return ("replay", {**respons, "replay": True})
    # `paagaar` OG vi holder låsen ⇒ vinneren finnes ikke lenger; overta.
    conn.execute("UPDATE idempotens SET request_id=%s, ts=now()"
                 " WHERE tenant=%s AND nokkel=%s",
                 (request_id, tenant, idempotency_key))
    return ("ny", None)


def _fullfor(conn, tenant, idempotency_key, res: dict) -> dict:
    """Lagre den idempotente responsen og commit. Replay med samme nøkkel og
    input får NØYAKTIG denne responsen — aldri en ny operasjon."""
    conn.execute("UPDATE idempotens SET status='ferdig', respons=%s"
                 " WHERE tenant=%s AND nokkel=%s",
                 (json.dumps(res, ensure_ascii=False), tenant, idempotency_key))
    conn.commit()
    return res
